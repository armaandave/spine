"""AC-1: status moves get recorded, and recording them changes nothing anyone sees.

Pause, resume, stop, pick-up and Restart on a book, game, show, manga and comic. Deleting
only the status rows leaves Stats, Home, the profile list and the post page byte-identical,
same-day order included. latest_progress_change never reports one, on either path.
"""

import json
from datetime import UTC, datetime
from unittest import mock
from uuid import uuid4

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from api.serializers.common import latest_progress_change_for, prime_collection_items
from app import book_tracking, game_tracking, series_tracking, tv_tracking
from app.models import Book, Comic, Game, Item, Manga, MediaTypes, Sources, Status
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from integrations.imports.margins.importer import MarginsImporter
from social.models import Follow, FollowStatus, ProgressChange

NEW_YORK = "America/New_York"
NOON = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)
ALL_TIME = {"schema": "2", "start_date": "all", "end_date": "all"}
YEAR = {"schema": "2", "start_date": "2026-01-01", "end_date": "2026-12-31"}
PAUSED = ProgressChange.PAUSED
STOPPED = ProgressChange.STOPPED
BACK = ProgressChange.PICKED_BACK_UP


def rendered(response):
    return json.loads(JSONRenderer().render(response.data))


@override_settings(TIME_ZONE=NEW_YORK)
class StatusRowBase(TestCase):
    def setUp(self):
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clock = patch_now(NOON)
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.user = make_user(username="reader", calendar=NEW_YORK)
        self.friend = make_user(username="friend", calendar=NEW_YORK)
        Follow.objects.create(from_user=self.friend, to_user=self.user, status=FollowStatus.ACCEPTED)
        self.counter = 0

    def item(self, media_type, **fields):
        self.counter += 1
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=str(self.counter),
            title=f"{media_type} {self.counter}",
            image="https://example.com/poster.jpg",
            **fields,
        )

    def later(self, minutes=1):
        self.clock.advance(minutes=minutes)

    def moves(self, item):
        rows = ProgressChange.objects.filter(actor=self.user, item=item, current_progress__kind=ProgressChange.STATUS)
        return [row.current_progress["move"] for row in rows.order_by("id")]

    def get(self, path, params=None, user=None):
        client = APIClient(HTTP_X_SPINE_TIMEZONE=NEW_YORK)
        client.force_authenticate(user or self.user)
        cache.clear()
        response = client.get(path, params or {})
        self.assertEqual(response.status_code, 200, response.content)
        return rendered(response)

    def everything(self):
        """What Stats, Home, the profile list and the post page say, as JSON."""
        pages = {}
        for name, params in (("all_time", ALL_TIME), ("year", YEAR)):
            pages[f"stats_{name}"] = self.get("/api/v1/stats/me/summary/", params)
            for tab in ("book", "game", "tv", "manga", "comic"):
                pages[f"stats_{name}_{tab}"] = self.get("/api/v1/stats/me/summary/", {**params, "media_type": tab})
        pages["home"] = self.get("/api/v1/feed/", user=self.friend)
        pages["profile"] = self.get("/api/v1/users/reader/activity/", user=self.friend)
        for row in pages["profile"]["results"]:
            if row["type"] == "progress_updated":
                pages[f"post_{row['object']['id']}"] = self.get(f"/api/v1/progress/{row['object']['id']}/")
        return pages

    def assert_status_rows_change_nothing(self):
        with_rows = self.everything()
        deleted, _ = ProgressChange.objects.filter(current_progress__kind=ProgressChange.STATUS).delete()
        self.assertGreater(deleted, 0, "There should be status rows to delete")
        self.maxDiff = None
        self.assertEqual(with_rows, self.everything())


class BookMoveTests(StatusRowBase):
    def setUp(self):
        super().setUp()
        self.book = self.item(MediaTypes.BOOK.value, total_pages=400)

    def test_pause_resume_and_stop_record_their_moves(self):
        book_tracking.start(self.user, self.book)
        book_tracking.pause(self.user, self.book)
        book_tracking.resume(self.user, self.book)
        book_tracking.pause(self.user, self.book)
        book_tracking.start(self.user, self.book)
        book_tracking.drop(self.user, self.book)
        self.assertEqual(self.moves(self.book), [PAUSED, BACK, PAUSED, BACK, STOPPED])

    def test_a_status_row_carries_its_journey_and_zero_in_both_values(self):
        book_tracking.start(self.user, self.book)
        book_tracking.pause(self.user, self.book)
        row = ProgressChange.objects.get(item=self.book, current_progress__kind=ProgressChange.STATUS)
        journey = Book.objects.get(item=self.book).current_session_id
        expected = {"kind": "status", "value": 0, "max": None, "unit": "status", "move": PAUSED, "journey_id": journey}
        self.assertEqual(row.current_progress, expected)
        self.assertEqual(row.previous_progress, expected)

    def test_a_pick_up_after_a_stop_records_its_start_row_and_its_move(self):
        book_tracking.start(self.user, self.book)
        book_tracking.drop(self.user, self.book)
        book_tracking.start(self.user, self.book)
        self.assertEqual(self.moves(self.book), [STOPPED, BACK])
        starts = ProgressChange.objects.filter(item=self.book, current_progress__kind=ProgressChange.START)
        self.assertEqual(starts.count(), 2)

    def test_restart_writes_its_flag_and_no_status_row(self):
        book_tracking.start(self.user, self.book)
        book_tracking.restart(self.user, self.book)
        self.assertEqual(self.moves(self.book), [])
        starts = ProgressChange.objects.filter(item=self.book, current_progress__kind=ProgressChange.START).order_by("id")
        self.assertEqual([row.current_progress.get("began_over") for row in starts], [None, True])

    def test_a_status_set_on_a_book_never_started_writes_nothing(self):
        book_tracking.assign_status(self.user, self.book, Status.PAUSED.value)
        book_tracking.assign_status(self.user, self.book, Status.DROPPED.value)
        book_tracking.start(self.user, self.book)
        self.assertEqual(self.moves(self.book), [])

    def test_a_new_read_after_a_finish_is_no_pick_up(self):
        book_tracking.complete(self.user, self.book, completion_date=NOON.date())
        book_tracking.assign_status(self.user, self.book, Status.PAUSED.value)
        book_tracking.start(self.user, self.book)
        self.assertEqual(self.moves(self.book), [])

    def test_a_mark_and_a_log_deletes_restore_write_no_status_row(self):
        book_tracking.mark_read(self.user, self.book)
        book_tracking.undo_read(self.user, self.book)
        book_tracking.start(self.user, self.book)
        book_tracking.pause(self.user, self.book)
        _, entry = book_tracking.complete(self.user, self.book, completion_date=NOON.date())
        before = self.moves(self.book)
        book_tracking.delete_completion(self.user, entry)
        self.assertEqual(self.moves(self.book), before)
        self.assertEqual(Book.objects.get(item=self.book).status, Status.PAUSED.value)

    def test_an_import_writes_no_status_row(self):
        csv = (
            "Title,Author(s),Status,Start Date,End Date,Rating,Review\n"
            "Given Up,An Author,Stopped,2026-01-01,2026-02-01,,\n"
            "Reading Now,An Author,Reading,2026-01-01,,,\n"
        )

        class Resolver:
            def resolve_rows(self, rows):
                return {
                    row.index: {
                        "media_id": row.title,
                        "source": Sources.MANUAL.value,
                        "title": row.title,
                        "image": "https://example.com/poster.jpg",
                    }
                    for row in rows
                }

        MarginsImporter(SimpleUploadedFile("m.csv", csv.encode()), self.user, "new", resolver=Resolver()).import_data()
        self.assertFalse(ProgressChange.objects.filter(current_progress__kind=ProgressChange.STATUS).exists())


class GameMoveTests(StatusRowBase):
    def setUp(self):
        super().setUp()
        self.game = self.item(MediaTypes.GAME.value)

    def test_pause_resume_and_stop_record_their_moves(self):
        game_tracking.start(self.user, self.game)
        game_tracking.pause(self.user, self.game)
        game_tracking.pause(self.user, self.game)
        game_tracking.resume(self.user, self.game)
        game_tracking.drop(self.user, self.game)
        self.assertEqual(self.moves(self.game), [PAUSED, BACK, STOPPED])
        row = ProgressChange.objects.filter(item=self.game, current_progress__kind=ProgressChange.STATUS).first()
        self.assertEqual(row.current_progress["playthrough_id"], Game.objects.get(item=self.game).current_session_id)

    def test_a_status_set_to_dropped_records_the_stop(self):
        game_tracking.start(self.user, self.game)
        game_tracking.assign_status(self.user, self.game, Status.DROPPED)
        self.assertEqual(self.moves(self.game), [STOPPED])

    def test_a_drop_then_playing_is_a_new_playthrough_with_no_pick_up(self):
        game_tracking.start(self.user, self.game)
        game_tracking.drop(self.user, self.game)
        game_tracking.start(self.user, self.game)
        self.assertEqual(self.moves(self.game), [STOPPED])

    def test_restart_writes_no_status_row(self):
        game_tracking.start(self.user, self.game)
        game_tracking.restart(self.user, self.game, mutation_id=uuid4())
        self.assertEqual(self.moves(self.game), [])

    def test_a_status_set_on_a_game_never_started_writes_nothing(self):
        game_tracking.assign_status(self.user, self.game, Status.PAUSED)
        game_tracking.assign_status(self.user, self.game, Status.DROPPED)
        self.assertEqual(self.moves(self.game), [])

    def test_a_mark_and_an_import_write_no_status_row(self):
        game_tracking.mark_completed(self.user, self.game)
        other = self.item(MediaTypes.GAME.value)
        game_tracking.import_title_state(self.user, other, status=Status.DROPPED)
        self.assertEqual(self.moves(self.game) + self.moves(other), [])


class SeriesMoveTests(StatusRowBase):
    def test_pause_resume_stop_and_pick_up_on_a_manga_and_a_comic(self):
        for media_type in (MediaTypes.MANGA.value, MediaTypes.COMIC.value):
            item = self.item(media_type, series_total=100)
            series_tracking.start(self.user, item)
            series_tracking.pause(self.user, item)
            series_tracking.start(self.user, item)
            series_tracking.drop(self.user, item)
            series_tracking.start(self.user, item)
            self.assertEqual(self.moves(item), [PAUSED, BACK, STOPPED, BACK])

    def test_keep_reading_a_new_issue_and_restart_write_none(self):
        manga = self.item(MediaTypes.MANGA.value, series_total=None)
        series_tracking.start(self.user, manga)
        series_tracking.update_progress(self.user, manga, value=5)
        series_tracking.complete(self.user, manga, completion_date=NOON.date())
        series_tracking.keep_reading(self.user, manga)
        series_tracking.restart(self.user, manga)
        self.assertEqual(self.moves(manga), [])
        comic = self.item(MediaTypes.COMIC.value, series_total=10)
        series_tracking.mark_read(self.user, comic)
        Item.objects.filter(pk=comic.pk).update(series_total=11)
        comic.refresh_from_db()
        series_tracking.new_issue_out(comic)
        self.assertEqual(Comic.objects.get(item=comic).status, Status.IN_PROGRESS.value)
        self.assertEqual(self.moves(comic), [])

    def test_a_status_set_on_a_series_never_started_writes_nothing(self):
        manga = self.item(MediaTypes.MANGA.value, series_total=100)
        series_tracking.assign_status(self.user, manga, Status.DROPPED.value)
        series_tracking.start(self.user, manga)
        self.assertEqual(self.moves(manga), [])

    def test_a_status_row_goes_with_its_attempt(self):
        manga = self.item(MediaTypes.MANGA.value, series_total=100)
        series_tracking.start(self.user, manga)
        series_tracking.pause(self.user, manga)
        series_tracking.delete_journey(self.user, manga, Manga.objects.get(item=manga).current_attempt_id)
        self.assertFalse(ProgressChange.objects.filter(item=manga).exists())


class ShowMoveTests(StatusRowBase):
    def setUp(self):
        super().setUp()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.show = make_show(self.fake, title="Severance", seasons={1: 3})

    def test_pause_stop_and_pick_up_once_started(self):
        tv_tracking.tick(self.user, self.show, 1, 1)
        self.later()
        tv_tracking.choose(self.user, self.show, tv_tracking.PAUSED)
        self.later()
        tv_tracking.choose(self.user, self.show, tv_tracking.WATCHING)
        self.later()
        tv_tracking.choose(self.user, self.show, tv_tracking.DROPPED)
        self.later()
        tv_tracking.choose(self.user, self.show, tv_tracking.WATCHING)
        self.assertEqual(self.moves(self.show), [PAUSED, BACK, STOPPED, BACK])

    def test_a_show_never_started_writes_nothing(self):
        tv_tracking.choose(self.user, self.show, tv_tracking.PAUSED)
        self.later()
        tv_tracking.choose(self.user, self.show, tv_tracking.DROPPED)
        self.assertEqual(self.moves(self.show), [])

    def test_a_tick_that_ends_a_pause_is_just_the_tick(self):
        tv_tracking.tick(self.user, self.show, 1, 1)
        self.later()
        tv_tracking.choose(self.user, self.show, tv_tracking.PAUSED)
        self.later()
        tv_tracking.tick(self.user, self.show, 1, 2)
        self.assertEqual(self.moves(self.show), [PAUSED])

    def test_a_webhook_chooses_nothing(self):
        tv_tracking.tick(self.user, self.show, 1, 1)
        self.later()
        tv_tracking.choose(self.user, self.show, tv_tracking.PAUSED, entry_point=tv_tracking.WEBHOOK)
        self.assertEqual(self.moves(self.show), [])

    def test_a_mark_writes_no_status_row(self):
        tv_tracking.mark_watched(self.user, self.show)
        self.assertEqual(self.moves(self.show), [])


class NothingMovesTests(StatusRowBase):
    """Deleting only the status rows changes nothing any page says."""

    def test_on_every_type_with_same_day_order(self):
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        game = self.item(MediaTypes.GAME.value)
        manga = self.item(MediaTypes.MANGA.value, series_total=100)
        comic = self.item(MediaTypes.COMIC.value, series_total=100)
        with FakeTVProvider() as fake:
            show = make_show(fake, title="Severance", seasons={1: 3})
            # Saves first, then each move, so a status row is newest on its day.
            book_tracking.start(self.user, book)
            self.later()
            book_tracking.update_progress(self.user, book, progress_type="pages", value=40)
            self.later()
            game_tracking.start(self.user, game)
            self.later()
            game_tracking.update_progress(self.user, game, total_minutes=120)
            self.later()
            series_tracking.start(self.user, manga)
            self.later()
            series_tracking.update_progress(self.user, manga, value=5)
            self.later()
            series_tracking.start(self.user, comic)
            self.later()
            series_tracking.update_progress(self.user, comic, value=3)
            self.later()
            tv_tracking.tick(self.user, show, 1, 1)
            self.later()
            book_tracking.pause(self.user, book)
            self.later()
            book_tracking.resume(self.user, book)
            self.later()
            book_tracking.drop(self.user, book)
            self.later()
            book_tracking.start(self.user, book)
            self.later()
            game_tracking.pause(self.user, game)
            self.later()
            game_tracking.resume(self.user, game)
            self.later()
            game_tracking.drop(self.user, game)
            self.later()
            series_tracking.pause(self.user, manga)
            self.later()
            series_tracking.start(self.user, manga)
            self.later()
            series_tracking.drop(self.user, comic)
            self.later()
            series_tracking.start(self.user, comic)
            self.later()
            tv_tracking.choose(self.user, show, tv_tracking.PAUSED)
            self.later()
            tv_tracking.choose(self.user, show, tv_tracking.WATCHING)
            self.later()
            book_tracking.restart(self.user, book)
            self.assert_status_rows_change_nothing()

    def test_a_move_on_another_day_leaves_no_row(self):
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="pages", value=40)
        self.later(minutes=60 * 24 * 3)
        book_tracking.pause(self.user, book)
        self.later(minutes=60 * 24 * 3)
        book_tracking.resume(self.user, book)
        self.assert_status_rows_change_nothing()
        self.assertEqual(len(self.get("/api/v1/users/reader/activity/")["results"]), 1)


class LatestProgressChangeTests(StatusRowBase):
    """Installed builds read the newest real progress, on the primed path and the fallback."""

    def both_paths(self, media):
        media = type(media).objects.select_related("item").get(pk=media.pk)
        fallback = latest_progress_change_for(media)
        prime_collection_items([media.item], self.user, media_by_item={media.item_id: media})
        self.assertTrue(hasattr(media, "viewer_latest_progress_change"))
        return fallback, latest_progress_change_for(media)

    def test_a_status_touch_and_planning_row_are_never_the_latest(self):
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="pages", value=40)
        saved = ProgressChange.objects.get(item=book, current_progress__kind="pages")
        book_tracking.pause(self.user, book)
        for kind in (ProgressChange.TOUCH, ProgressChange.PLANNING):
            shape = {"kind": kind, "value": 0, "max": None, "unit": kind}
            ProgressChange.objects.create(actor=self.user, item=book, previous_progress=shape, current_progress=shape)
        fallback, primed = self.both_paths(Book.objects.get(item=book))
        self.assertEqual(fallback.pk, saved.pk)
        self.assertEqual(primed.pk, saved.pk)

    def test_on_a_title_with_only_a_status_row(self):
        game = self.item(MediaTypes.GAME.value)
        game_tracking.start(self.user, game)
        game_tracking.pause(self.user, game)
        fallback, primed = self.both_paths(Game.objects.get(item=game))
        self.assertIsNone(fallback)
        self.assertIsNone(primed)

    def test_through_the_tracking_endpoints(self):
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="pages", value=40)
        url = f"/api/v1/tracking/{book.source}/book/{book.media_id}/"
        detail = self.get(url)
        listed = self.get("/api/v1/tracking/", {"media_type": "book"})
        book_tracking.pause(self.user, book)
        book_tracking.resume(self.user, book)
        self.assertEqual(self.get(url)["latest_progress_change"], detail["latest_progress_change"])
        rows = self.get("/api/v1/tracking/", {"media_type": "book"})["results"]
        self.assertEqual(
            rows[0]["tracking"]["latest_progress_change"],
            listed["results"][0]["tracking"]["latest_progress_change"],
        )

