"""Likes stay on the day they were on, and go when the title goes (AC-7).

Both bugs of the feed shipped on Nov 2: one like split across two viewers' days, and an
old like coming back on a new save that day. The feed here is still the old reader.
"""

from datetime import datetime
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from api.services import progress_posts
from api.tests.test_activity_book_imports import (
    BookImportBase,
    goodreads_file,
    margins_file,
    storygraph_file,
)
from api.tests.test_activity_calc_books import BookCalcBase, d
from app import book_tracking, game_tracking, series_tracking, tv_tracking
from app.models import (
    TV,
    Anime,
    Book,
    GameSession,
    Item,
    Manga,
    MediaTypes,
    Sources,
    Status,
)
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from integrations.imports import helpers
from integrations.imports.mal_export.importer import MALExportImporter
from social.models import (
    Comment,
    ContentLike,
    Follow,
    FollowStatus,
    ProgressChange,
    ProgressPost,
    SocialAuditLog,
)

NEW_YORK = "America/New_York"
TOKYO = "Asia/Tokyo"
FEED = "/api/v1/feed/"
MAL_EXPORT = (
    b'<?xml version="1.0" encoding="UTF-8" ?><myanimelist><anime><series_animedb_id>1</series_animedb_id>'
    b"<series_title><![CDATA[Bebop]]></series_title><series_episodes>12</series_episodes>"
    b"<my_watched_episodes>3</my_watched_episodes><my_start_date>0000-00-00</my_start_date>"
    b"<my_finish_date>0000-00-00</my_finish_date><my_score>0</my_score><my_status>Watching</my_status>"
    b"</anime></myanimelist>"
)


def _anime_save(owner, item):
    """Write what a like reaches on an anime: its own saves have no change row here."""
    return ProgressChange.objects.create(
        actor=owner,
        item=item,
        previous_progress={"kind": "count", "value": 0, "max": 12, "unit": "episode"},
        current_progress={"kind": "count", "value": 3, "max": 12, "unit": "episode"},
        saved_on=d(2026, 3, 2),
    )


@override_settings(TIME_ZONE=NEW_YORK)
class LikesBase(BookCalcBase):
    """A New York owner read by a follower from any zone."""

    def change(self, item):
        """Return the newest save on a title: the row a like reaches."""
        return ProgressChange.objects.filter(item=item, current_progress__kind="pages").latest("id")

    def like(self, change, zone=NEW_YORK, viewer=None):
        response = self.client_for(viewer or self.follower, zone).post(f"/api/v1/progress/{change.pk}/like/")
        self.assertEqual(response.status_code, 200, response.content)

    def comment(self, change, zone=NEW_YORK):
        response = self.client_for(self.follower, zone).post(
            f"/api/v1/progress/{change.pk}/comments/",
            {"body": "Nice"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)

    def feed_rows(self, zone=NEW_YORK, viewer=None):
        """Return (day, summary, likes, comments) of each progress row seen."""
        response = self.client_for(viewer or self.follower, zone).get(FEED)
        self.assertEqual(response.status_code, 200, response.content)
        return [
            (row["consumed_at"], row["object"]["summary"], row["like_count"], row["comment_count"])
            for row in response.json()["results"]
            if row["type"] == "progress_updated"
        ]


class OneDayForEveryViewerTests(LikesBase):
    """The first bug: the day came from the viewer's zone."""

    def test_noon_in_new_york_reads_two_dates_on_the_clock(self):
        """What makes the bug show: noon New York is the next day's 02:00 in Tokyo."""
        item = self.book("noon")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 1))
        moment = self.change(item).created_at
        self.assertNotEqual(moment.astimezone(ZoneInfo(NEW_YORK)).date(), moment.astimezone(ZoneInfo(TOKYO)).date())

    def test_both_zones_read_one_row_with_both_likes(self):
        item = self.book("noon")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 1))
        change = self.change(item)
        self.like(change, zone=NEW_YORK)
        self.like(change, zone=TOKYO, viewer=self.stranger)
        self.comment(change, zone=NEW_YORK)
        wanted = [("2026-03-01", "read 40 pages", 2, 1)]
        self.assertEqual(self.feed_rows(zone=NEW_YORK), wanted)
        self.assertEqual(self.feed_rows(zone=TOKYO), wanted)
        self.assertEqual(ProgressPost.objects.count(), 1)

    def test_a_row_with_no_stored_day_reads_one_named_zone(self):
        item = self.book("old-row")
        self.begin(item, d(2026, 3, 1), at="23:30")
        self.save(item, 40, d(2026, 3, 1), at="23:30")
        ProgressChange.objects.filter(item=item).update(saved_on=None)
        change = self.change(item)
        self.like(change, zone=TOKYO, viewer=self.stranger)
        self.like(change, zone=NEW_YORK)
        wanted = [("2026-03-01", "read 40 pages", 2, 0)]
        self.assertEqual(self.feed_rows(zone=NEW_YORK), wanted)
        self.assertEqual(self.feed_rows(zone=TOKYO), wanted)

    def test_the_fixed_day_wins_over_the_moment(self):
        item = self.book("fixed")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        ProgressChange.objects.filter(item=item).update(saved_on=d(2026, 2, 27))
        self.assertEqual(self.feed_rows(), [("2026-02-27", "read 40 pages", 0, 0)])


class TheFeedIsStillTheOldOneTests(LikesBase):
    """What a fix does to a liked row: the rows change at Phase 4, not here."""

    def test_a_typo_fixed_later_still_reads_its_old_number_with_its_like(self):
        item = self.book("typo")
        self.begin(item, d(2025, 12, 20))
        self.save(item, 300, d(2025, 12, 28))
        self.like(self.change(item))
        self.save(item, 30, d(2026, 1, 2))
        self.assertEqual(self.feed_rows()[0], ("2025-12-28", "read 300 pages", 1, 0))
        self.assertNotIn("2026-01-02", [row[0] for row in self.feed_rows()])

    def test_a_drop_in_february_leaves_januarys_row_and_its_like(self):
        item = self.book("across")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 100, d(2025, 12, 15))
        self.save(item, 300, d(2026, 1, 15))
        self.like(self.change(item))
        self.save(item, 30, d(2026, 2, 15))
        self.assertIn(("2026-01-15", "read 200 pages", 1, 0), self.feed_rows())


class AReadDeletedTests(LikesBase):
    """The second bug: a like outlived what it was on and came back."""

    def read_with_a_like(self, name="back"):
        item = self.book(name)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        change = self.change(item)
        self.like(change)
        self.comment(change)
        return item

    def test_a_deleted_read_then_a_new_save_that_day_starts_at_nothing(self):
        item = self.read_with_a_like()
        with self.on(d(2026, 3, 2)):
            journey = Book.objects.get(user=self.owner, item=item).current_session_id
            book_tracking.delete_journey(self.owner, item, journey)
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.begin(item, d(2026, 3, 2))
        self.save(item, 10, d(2026, 3, 2))
        self.assertEqual(self.feed_rows()[0], ("2026-03-02", "read 10 pages", 0, 0))

    def test_each_post_removed_says_so_in_the_audit_log(self):
        item = self.read_with_a_like()
        with self.on(d(2026, 3, 2)):
            book_tracking.delete_journey(self.owner, item, Book.objects.get(item=item).current_session_id)
        entry = SocialAuditLog.objects.get(action="progress_post_removed")
        self.assertEqual(entry.actor, self.owner)
        self.assertEqual(entry.metadata, {"title_key": f"item:{item.pk}", "day": "2026-03-02"})

    def test_a_like_stays_when_another_read_holds_that_day(self):
        item = self.book("two")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        first = Book.objects.get(item=item).current_session_id
        self.like(self.change(item))
        with self.on(d(2026, 3, 2)):
            book_tracking.restart(self.owner, item)
        self.save(item, 10, d(2026, 3, 2))
        with self.on(d(2026, 3, 2)):
            book_tracking.delete_journey(self.owner, item, first)
        self.assertEqual(ContentLike.objects.count(), 1)
        self.assertEqual(self.feed_rows()[0], ("2026-03-02", "read 10 pages", 1, 0))

    def test_a_delete_never_sweeps_the_titles_other_days(self):
        item = self.book("sweep")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        stranded = ProgressPost.objects.create(actor=self.owner, title_key=f"item:{item.pk}", day=d(2026, 3, 3))
        self.like(self.change(item))
        with self.on(d(2026, 3, 2)):
            book_tracking.delete_journey(self.owner, item, Book.objects.get(item=item).current_session_id)
        self.assertEqual(list(ProgressPost.objects.values_list("pk", flat=True)), [stranded.pk])

    def test_a_game_and_a_manga_too(self):
        game = self.item(MediaTypes.GAME.value, "game")
        with self.on(d(2026, 3, 1)):
            game_tracking.start(self.owner, game)
            game_tracking.update_progress(self.owner, game, total_minutes=60)
        change = ProgressChange.objects.filter(item=game, current_progress__unit="minute").latest("id")
        self.like(change)
        session = GameSession.objects.get(related_game__user=self.owner)
        with self.on(d(2026, 3, 1)):
            game_tracking.delete_playthrough(self.owner, game, session.pk)
        self.assertEqual(ContentLike.objects.count(), 0)

        manga = self.item(MediaTypes.MANGA.value, "manga")
        with self.on(d(2026, 3, 1)):
            series_tracking.start(self.owner, manga)
            series_tracking.update_progress(self.owner, manga, value=5)
        change = ProgressChange.objects.filter(item=manga, current_progress__kind="count").latest("id")
        self.like(change)
        attempt = Manga.objects.get(user=self.owner, item=manga).current_attempt_id
        with self.on(d(2026, 3, 1)):
            series_tracking.delete_journey(self.owner, manga, attempt)
        self.assertEqual(ContentLike.objects.count(), 0)


class ATitleRemovedTests(LikesBase):
    """A tracking row gone: its saves, posts and likes go on commit."""

    def liked(self, name):
        item = self.book(name)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        self.like(self.change(item))
        return item

    def test_the_row_going_takes_the_title_with_it(self):
        item = self.liked("removed")
        with self.captureOnCommitCallbacks(execute=True):
            Book.objects.filter(user=self.owner, item=item).delete()
        self.assertEqual(ProgressChange.objects.filter(item=item).count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.assertEqual(ContentLike.objects.count(), 0)

    def test_someone_elses_row_on_that_title_stays(self):
        item = self.liked("shared")
        with self.on(d(2026, 3, 1)):
            book_tracking.start(self.follower, item)
        with self.captureOnCommitCallbacks(execute=True):
            Book.objects.filter(user=self.owner, item=item).delete()
        self.assertEqual(ProgressChange.objects.filter(item=item, actor=self.follower).count(), 1)

    def test_a_row_recreated_in_the_same_transaction_keeps_its_saves(self):
        """The receiver can't see an overwrite, so importers call the helper."""
        item = self.liked("recreated")
        with self.captureOnCommitCallbacks(execute=True):
            Book.objects.filter(user=self.owner, item=item).delete()
            Book.objects.create(user=self.owner, item=item, status=Status.PLANNING.value)
        self.assertEqual(ContentLike.objects.count(), 1)

    def test_deleting_the_person_queues_nothing(self):
        item = self.liked("person")
        with self.captureOnCommitCallbacks() as callbacks:
            self.owner.delete()
        self.assertEqual(callbacks, [])
        self.assertFalse(ProgressChange.objects.filter(item=item).exists())

    def test_a_show_keeps_its_likes_while_any_of_its_rows_is_left(self):
        show, _season_item, tv, season = self.tv_show()
        change = ProgressChange.record_start(self.owner, show)
        post = ProgressPost.objects.create(actor=self.owner, title_key=f"tv:{show.source}:{show.media_id}", day=d(2026, 3, 1))
        with self.captureOnCommitCallbacks(execute=True):
            season.delete()
        self.assertTrue(ProgressPost.objects.filter(pk=post.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            tv.delete()
        self.assertFalse(ProgressPost.objects.filter(pk=post.pk).exists())
        self.assertFalse(ProgressChange.objects.filter(pk=change.pk).exists())


class OverwriteTests(LikesBase):
    """An overwrite deletes and re-creates inside one transaction."""

    def test_an_overwrite_in_one_transaction_brings_no_like_back(self):
        item = self.book("overwrite")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        self.like(self.change(item))
        with transaction.atomic(), self.captureOnCommitCallbacks(execute=True):
            progress_posts.forget_titles(self.owner.pk, [item])
            Book.objects.filter(user=self.owner, item=item).delete()
            book_tracking.start(self.owner, item)
            book_tracking.update_progress(self.owner, item, progress_type="pages", value=10)
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(self.feed_rows()[0][1:], ("read 10 pages", 0, 0))

    def test_the_receiver_alone_can_not_see_an_overwrite(self):
        """Why each importer below calls the helper itself."""
        item = self.book("recreated")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        self.like(self.change(item))
        with transaction.atomic(), self.captureOnCommitCallbacks(execute=True):
            Book.objects.filter(user=self.owner, item=item).delete()
            book_tracking.start(self.owner, item)
        self.assertEqual(ContentLike.objects.count(), 1)

    def test_the_budget_50_titles_cost_what_5_cost(self):
        """Assert the work ran: per row it's about 4,000 extra queries on 850 titles."""

        def seed(names):
            items = [self.book(name) for name in names]
            for item in items:
                book_tracking.start(self.owner, item)
                book_tracking.update_progress(self.owner, item, progress_type="pages", value=10)
                post = ProgressPost.objects.create(actor=self.owner, title_key=f"item:{item.pk}", day=d(2026, 3, 1))
                ContentLike.objects.create(user=self.follower, target_type=ContentLike.PROGRESS_POST, target_id=post.pk)
            return items

        def measure(items):
            with CaptureQueriesContext(connection) as queries:
                progress_posts.forget_titles(self.owner.pk, items)
            self.assertFalse(ProgressChange.objects.filter(item__in=items).exists())
            self.assertFalse(ProgressPost.objects.exists())
            self.assertFalse(ContentLike.objects.exists())
            return len(queries)

        few = measure(seed([f"few-{index}" for index in range(5)]))
        many = measure(seed([f"many-{index}" for index in range(50)]))
        self.assertEqual(few, many)

    def test_the_receiver_flushes_in_one_batch(self):
        with patch.object(progress_posts, "forget_titles", wraps=progress_posts.forget_titles) as forget:
            items = [self.book(f"batch-{index}") for index in range(20)]
            for item in items:
                Book.objects.create(user=self.owner, item=item, status=Status.PLANNING.value)
            with self.captureOnCommitCallbacks(execute=True):
                Book.objects.filter(user=self.owner).delete()
        self.assertEqual(forget.call_count, 1)
        self.assertEqual(len(forget.call_args.args[1]), 20)


class RealOverwriteTests(LikesBase, BookImportBase):
    """Each importer's real overwrite: no like, comment, post or save comes back."""

    def liked_read(self):
        self.begin(self.item_row, d(2026, 3, 1))
        self.save(self.item_row, 40, d(2026, 3, 2))
        change = self.change(self.item_row)
        self.like(change)
        self.comment(change)

    def assert_nothing_came_back(self):
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.assertFalse(ProgressChange.objects.filter(actor=self.owner, item=self.item_row).exists())
        self.assertTrue(SocialAuditLog.objects.filter(action="progress_post_removed").exists())

    def test_goodreads(self):
        self.liked_read()
        with self.captureOnCommitCallbacks(execute=True):
            self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/01"}), mode="overwrite")
        self.assert_nothing_came_back()

    def test_storygraph(self):
        self.liked_read()
        with self.captureOnCommitCallbacks(execute=True):
            self.from_storygraph(storygraph_file(dates="2024/05/01"), mode="overwrite")
        self.assert_nothing_came_back()

    def test_margins(self):
        self.liked_read()
        with self.captureOnCommitCallbacks(execute=True):
            self.from_margins(margins_file("2024-05-01"), mode="overwrite")
        self.assert_nothing_came_back()

    def test_a_new_save_that_day_after_goodreads_starts_at_nothing(self):
        self.liked_read()
        with self.captureOnCommitCallbacks(execute=True):
            self.from_goodreads(goodreads_file(**{"Date Read": ""}), mode="overwrite")
        with self.on(d(2026, 3, 2)):
            book_tracking.start(self.owner, self.item_row)
            book_tracking.update_progress(self.owner, self.item_row, progress_type="pages", value=10)
        self.assertEqual(self.feed_rows()[0], ("2026-03-02", "read 10 pages", 0, 0))

    def test_the_mal_export(self):
        item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Bebop",
            image="https://example.com/a.jpg",
        )
        Anime.objects.bulk_create([Anime(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=0)])
        self.like(_anime_save(self.owner, item))
        with self.captureOnCommitCallbacks(execute=True), patch("app.signals.update_daily_statistics.delay"):
            MALExportImporter(
                MAL_EXPORT,
                self.owner,
                "overwrite",
                metadata_fetcher=lambda *_args, **_kwargs: {
                    "media_id": "1",
                    "title": "Bebop",
                    "image": "https://example.com/a.jpg",
                    "max_progress": 12,
                },
            ).import_data()
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.assertFalse(ProgressChange.objects.filter(item=item).exists())
        self.assertEqual(Anime.objects.filter(user=self.owner, item=item).count(), 1)


class EditDatesThenDeleteTests(LikesBase):
    """Edit Dates moves a start row's day, but its post stays where the like was (5.8)."""

    def test_a_book_starts_at_nothing_on_a_new_save_that_day(self):
        item = self.book("edited")
        today = d(2026, 3, 5)
        self.begin(item, today)
        change = ProgressChange.objects.filter(item=item).latest("id")
        self.like(change)
        self.comment(change)
        journey = Book.objects.get(item=item).current_session_id
        with self.on(today):
            book_tracking.update_journey(self.owner, item, journey, start_date=d(2026, 3, 1))
            book_tracking.delete_journey(self.owner, item, journey)
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.begin(item, today)
        self.save(item, 10, today)
        self.assertEqual(self.feed_rows()[0], ("2026-03-05", "read 10 pages", 0, 0))

    def test_a_manga_starts_at_nothing_too(self):
        item = self.item(MediaTypes.MANGA.value, "edited-manga")
        today = d(2026, 3, 5)
        with self.on(today):
            series_tracking.start(self.owner, item)
        self.like(ProgressChange.objects.filter(item=item).latest("id"))
        attempt = Manga.objects.get(user=self.owner, item=item).current_attempt_id
        with self.on(today):
            series_tracking.update_journey(self.owner, item, attempt, start_date=d(2026, 3, 1))
            series_tracking.delete_journey(self.owner, item, attempt)
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)

    def test_a_row_left_on_that_day_keeps_the_post(self):
        """Another journey's row that day holds the post: it's a check, not a sweep."""
        item = self.book("edited-kept")
        today = d(2026, 3, 5)
        self.begin(item, today)
        self.save(item, 40, today)
        self.do(book_tracking.restart, item, today)
        self.save(item, 10, today)
        self.like(self.change(item))
        old = Book.objects.get(item=item).current_session.previous_session_id
        with self.on(today):
            book_tracking.delete_journey(self.owner, item, old)
        self.assertEqual(ContentLike.objects.count(), 1)


class ATitleRemovedThenTrackedAgainTests(LikesBase):
    """Removing a title through the phone's own request, and tracking it again that day."""

    def test_an_anime_starts_at_nothing(self):
        item = Item.objects.create(
            media_id="removed-anime",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.ANIME.value,
            title="removed-anime",
            image="https://example.com/a.jpg",
        )
        phone = self.client_for(self.owner, NEW_YORK)
        url = f"/api/v1/tracking/{item.source}/anime/{item.media_id}/"
        Anime.objects.bulk_create([Anime(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=0)])
        self.assertEqual(phone.patch(url, {"progress": 3}, format="json").status_code, 200)
        change = ProgressChange.objects.filter(item=item).latest("id")
        self.like(change)
        self.comment(change)
        with self.captureOnCommitCallbacks(execute=True):
            response = phone.delete(url)
        self.assertLess(response.status_code, 300, response.content)
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        Anime.objects.bulk_create([Anime(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=0)])
        self.assertEqual(phone.patch(url, {"progress": 2}, format="json").status_code, 200)
        self.assertEqual(self.feed_rows()[0][1:], ("watched 2 episodes", 0, 0))


@override_settings(TIME_ZONE=NEW_YORK)
class RealCommitTests(TransactionTestCase):
    """No wrapping transaction, as an import task runs, so the receiver's hook fires."""

    def setUp(self):
        # No wrapping transaction also means a new MAL Item's ratings task really runs.
        for target in (
            "app.models.Item.fetch_releases",
            "app.signals.update_daily_statistics.delay",
            "app.signals.enrich_external_ratings",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        users = get_user_model().objects
        self.owner = users.create_user(username="overwrite-owner", password="strong-password-123")
        self.friend = users.create_user(username="overwrite-friend", password="strong-password-123")
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Bebop",
            image="https://example.com/a.jpg",
        )
        Anime.objects.bulk_create([Anime(user=self.owner, item=self.item, status=Status.IN_PROGRESS.value, progress=0)])
        client = APIClient()
        client.force_authenticate(self.friend)
        response = client.post(f"/api/v1/progress/{_anime_save(self.owner, self.item).pk}/like/")
        self.assertEqual(response.status_code, 200, response.content)

    def test_the_generic_overwrite_cleanup_brings_no_like_back(self):
        helpers.cleanup_existing_media({MediaTypes.ANIME.value: {self.item.source: [self.item.media_id]}}, self.owner)
        Anime.objects.bulk_create([Anime(user=self.owner, item=self.item, status=Status.IN_PROGRESS.value, progress=0)])
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.assertFalse(ProgressChange.objects.filter(item=self.item).exists())


@override_settings(TIME_ZONE=NEW_YORK)
class TVSeasonDeleteTests(TestCase):
    """A season is left out of the receiver, so its delete has to drop a post (5.4)."""

    def setUp(self):
        self.provider = FakeTVProvider().__enter__()
        self.addCleanup(self.provider.__exit__, None, None, None)
        self.clock = patch_now(datetime(2026, 10, 25, 20, 0, tzinfo=ZoneInfo(NEW_YORK))).__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.owner = make_user(calendar=NEW_YORK)
        self.friend = make_user(calendar=NEW_YORK)
        Follow.objects.create(from_user=self.friend, to_user=self.owner, status=FollowStatus.ACCEPTED)
        self.show = make_show(self.provider, title="Severance", seasons={1: 3, 2: 3})

    def tick(self, season, episode):
        self.clock.advance(minutes=1)
        return tv_tracking.tick(self.owner, self.show, season, episode)

    def phone(self):
        client = APIClient()
        client.force_authenticate(self.friend)
        client.credentials(HTTP_X_SPINE_TIMEZONE=NEW_YORK)
        return client

    def rows(self):
        """Return (day, summary, likes, comments, change id) of each progress row."""
        response = self.phone().get(FEED)
        self.assertEqual(response.status_code, 200, response.content)
        return [
            (row["consumed_at"], row["object"]["summary"], row["like_count"], row["comment_count"], row["object"]["id"])
            for row in response.json()["results"]
            if row["type"] == "progress_updated"
        ]

    def like_the_top_row(self):
        change = self.rows()[0][4]
        self.assertEqual(self.phone().post(f"/api/v1/progress/{change}/like/").status_code, 200)
        self.assertEqual(self.phone().post(f"/api/v1/progress/{change}/comments/", {"body": "x"}, format="json").status_code, 201)

    def delete_season(self, number):
        with self.captureOnCommitCallbacks(execute=True):
            tv_tracking.delete_season_tracking(self.owner, self.show, number)

    def test_a_day_with_another_seasons_row_left_keeps_its_like(self):
        self.tick(1, 1)
        self.tick(1, 2)
        self.tick(2, 1)
        self.like_the_top_row()
        self.delete_season(1)
        self.assertEqual([row[:4] for row in self.rows()], [("2026-10-25", "watched 1 episode", 1, 1)])
        self.assertEqual(ContentLike.objects.count(), 1)

    def test_a_day_left_with_no_row_while_the_show_stays_loses_its_like(self):
        self.tick(2, 1)
        self.clock.advance(days=1)
        self.tick(1, 1)
        self.like_the_top_row()
        self.delete_season(1)
        self.assertTrue(TV.objects.filter(user=self.owner, item=self.show).exists())
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.assertEqual([row[:4] for row in self.rows()], [("2026-10-25", "watched 1 episode", 0, 0)])

    def test_a_show_removed_then_ticked_again_starts_at_nothing(self):
        self.tick(1, 1)
        self.like_the_top_row()
        with self.captureOnCommitCallbacks(execute=True):
            tv_tracking.remove(self.owner, self.show, mutation_id=uuid4())
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        with self.captureOnCommitCallbacks(execute=True):
            self.tick(1, 2)
        self.assertEqual(self.rows()[0][2:4], (0, 0))

    def test_a_shows_only_season_deleted_then_ticked_again_starts_at_nothing(self):
        self.tick(1, 1)
        self.tick(1, 2)
        self.like_the_top_row()
        self.delete_season(1)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.assertEqual(self.rows(), [])
        with self.captureOnCommitCallbacks(execute=True):
            self.tick(1, 1)
        self.assertEqual(self.rows()[0][2:4], (0, 0))
        self.assertEqual(ContentLike.objects.count(), 0)
