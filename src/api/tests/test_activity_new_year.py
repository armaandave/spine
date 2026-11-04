"""AC-3 at the year's edge: a save lands on the phone's date, which is never refused.

Production's server runs on New York. Saves go through the phone's real requests where
it makes them. BK-304, BK-305, BK-306 and GM-013: a sent date equal to the phone's today
is never refused and the stored date stays in order; a day ahead caps at today; any
other date keeps its refusal.
"""

from datetime import UTC, date, datetime
from unittest import mock
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from app import (
    book_tracking,
    game_tracking,
    series_tracking,
    single_weight,
    tv_tracking,
    user_calendar,
)
from app.models import (
    Anime,
    Book,
    BookSession,
    Comic,
    DiaryEntry,
    Game,
    GameSession,
    Item,
    Manga,
    MediaTypes,
    Sources,
    Status,
)
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from integrations.imports.margins.importer import MarginsImporter
from social.models import ProgressChange

NEW_YORK = "America/New_York"
TOKYO = "Asia/Tokyo"
LOS_ANGELES = "America/Los_Angeles"
NEW_YORK_TZ = ZoneInfo(NEW_YORK)
DEC_30 = date(2026, 12, 30)
DEC_31 = date(2026, 12, 31)
JAN_1 = date(2027, 1, 1)
JAN_2 = date(2027, 1, 2)
LATE = datetime(2026, 12, 31, 23, 50, tzinfo=NEW_YORK_TZ)
EARLY = datetime(2027, 1, 1, 0, 10, tzinfo=NEW_YORK_TZ)
# Jan 1 in Tokyo, where a reading starts, then Dec 31 in Los Angeles after the flight.
IN_TOKYO = datetime(2027, 1, 1, 10, 0, tzinfo=ZoneInfo(TOKYO))
AFTER_FLYING_WEST = datetime(2026, 12, 31, 17, 0, tzinfo=ZoneInfo(LOS_ANGELES))


class NewYearBase(TestCase):
    """A New York owner, a clock to move and the phone's own requests."""

    def setUp(self):
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.clock = patch_now(LATE)
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.user = make_user(calendar=NEW_YORK)
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

    def phone(self, zone=NEW_YORK, user=None):
        client = APIClient(HTTP_X_SPINE_TIMEZONE=zone)
        client.force_authenticate(user or self.user)
        return client

    def url(self, item, suffix=""):
        return f"/api/v1/tracking/{item.source}/{item.media_type}/{item.media_id}/{suffix}"

    def post(self, client, item, suffix, expected=200, **body):
        response = client.post(self.url(item, suffix), body, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response

    def latest_day(self, item):
        return ProgressChange.objects.filter(item=item).order_by("-id").values_list("saved_on", flat=True).first()

    def saves_at_the_edge(self, save):
        """Run one save on each side of midnight; each lands on its own date."""
        for moment, day in ((LATE, DEC_31), (EARLY, JAN_1)):
            self.clock.set(moment)
            self.assertEqual(save(), day)


@override_settings(TIME_ZONE=NEW_YORK)
class SavesAtMidnightTests(NewYearBase):
    """Each save lands on its own side of midnight."""

    def test_a_book_save(self):
        item = self.item(MediaTypes.BOOK.value, total_pages=400)
        book_tracking.start(self.user, item)
        values = iter((10, 20))

        def save():
            self.post(self.phone(), item, "progress/", progress_type="pages", value=next(values))
            return self.latest_day(item)

        self.saves_at_the_edge(save)

    def test_a_game_save(self):
        item = self.item(MediaTypes.GAME.value)
        game_tracking.start(self.user, item)
        values = iter((60, 120))

        def save():
            day = str(user_calendar.today(self.user))
            self.post(self.phone(), item, "progress/", total_minutes=next(values), progressed_on=day)
            return self.latest_day(item)

        self.saves_at_the_edge(save)

    def test_a_manga_and_a_comic_save(self):
        for media_type in (MediaTypes.MANGA.value, MediaTypes.COMIC.value):
            with self.subTest(media_type):
                self.clock.set(LATE)
                self.series_saves_at_the_edge(self.item(media_type, series_total=100))

    def series_saves_at_the_edge(self, item):
        series_tracking.start(self.user, item)
        values = iter((1, 2))

        def save():
            self.post(self.phone(), item, "progress/", value=next(values))
            return self.latest_day(item)

        self.saves_at_the_edge(save)

    def test_an_anime_typed_save(self):
        item = self.item(MediaTypes.ANIME.value)
        row = Anime.objects.create(user=self.user, item=item, status=Status.IN_PROGRESS.value, progress=0)
        values = iter((1, 2))

        def save():
            response = self.phone().patch(self.url(item), {"progress": next(values)}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            newest = Anime.history.filter(id=row.pk).order_by("-history_id").first()
            return date.fromisoformat(newest.history_change_reason.removeprefix("day:"))

        with mock.patch("app.providers.services.get_media_metadata", return_value={"max_progress": 12}):
            self.saves_at_the_edge(save)

    def test_a_show_start_row(self):
        with FakeTVProvider() as fake:
            for moment, day in ((LATE, DEC_31), (EARLY, JAN_1)):
                self.clock.set(moment)
                show = make_show(fake, title=f"Show {moment}", seasons={1: 3})
                tv_tracking.choose(self.user, show, tv_tracking.WATCHING)
                self.assertEqual(self.latest_day(show), day)


@override_settings(TIME_ZONE=NEW_YORK)
class PhoneAheadTests(NewYearBase):
    """A clock 30 seconds ahead sends tomorrow just before midnight."""

    def setUp(self):
        super().setUp()
        self.clock.set(datetime(2026, 12, 31, 23, 59, 40, tzinfo=NEW_YORK_TZ))
        self.book = self.item(MediaTypes.BOOK.value, total_pages=400)
        self.game = self.item(MediaTypes.GAME.value)
        self.manga = self.item(MediaTypes.MANGA.value, series_total=100)
        book_tracking.start(self.user, self.book)
        game_tracking.start(self.user, self.game)
        series_tracking.start(self.user, self.manga)

    def test_a_game_save_keeps_its_number_and_lands_today(self):
        self.post(self.phone(), self.game, "progress/", total_minutes=60, progressed_on=str(JAN_1))
        session = GameSession.objects.get(related_game__item=self.game)
        self.assertEqual(session.total_minutes, 60)
        self.assertEqual(session.minutes_updated_on, DEC_31)
        self.assertEqual(self.latest_day(self.game), DEC_31)

    def test_a_game_save_further_ahead_stays_refused(self):
        self.post(self.phone(), self.game, "progress/", expected=400, total_minutes=60, progressed_on=str(JAN_2))
        self.assertIsNone(GameSession.objects.get(related_game__item=self.game).total_minutes)

    def test_a_stop_caps_at_today(self):
        self.post(self.phone(), self.book, "actions/drop/", end_date=str(JAN_1))
        self.post(self.phone(), self.game, "actions/drop/", end_date=str(JAN_1))
        self.post(self.phone(), self.manga, "actions/drop/", end_date=str(JAN_1))
        self.assertEqual(BookSession.objects.get(related_book__item=self.book).end_date.date(), DEC_31)
        self.assertEqual(GameSession.objects.get(related_game__item=self.game).end_date, DEC_31)
        self.assertEqual(Manga.objects.get(item=self.manga).current_attempt.end_date, DEC_31)

    def test_a_stop_further_ahead_stays_refused(self):
        for item in (self.book, self.game, self.manga):
            self.post(self.phone(), item, "actions/drop/", expected=400, end_date=str(JAN_2))

    def test_a_games_start_caps_at_today(self):
        game = self.item(MediaTypes.GAME.value)
        self.post(self.phone(), game, "actions/start/", start_date=str(JAN_1), mutation_id=str(uuid4()))
        self.assertEqual(Game.objects.get(item=game).current_session.start_date, DEC_31)

    def test_a_games_start_after_a_stop_caps_at_today(self):
        self.post(self.phone(), self.game, "actions/drop/", end_date=str(JAN_1))
        self.post(self.phone(), self.game, "actions/start/", start_date=str(JAN_1), mutation_id=str(uuid4()))
        self.assertEqual(Game.objects.get(item=self.game).current_session.start_date, DEC_31)

    def test_a_games_start_further_ahead_stays_refused(self):
        game = self.item(MediaTypes.GAME.value)
        self.post(self.phone(), game, "actions/start/", expected=400, start_date=str(JAN_2), mutation_id=str(uuid4()))
        self.assertFalse(GameSession.objects.filter(related_game__item=game).exists())

    def test_a_restart_caps_both_its_dates(self):
        body = {"start_date": str(JAN_1), "end_date": str(JAN_1), "mutation_id": str(uuid4())}
        self.post(self.phone(), self.book, "actions/restart/", **body)
        self.post(self.phone(), self.game, "actions/restart/", **{**body, "mutation_id": str(uuid4())})
        self.post(self.phone(), self.manga, "actions/restart/", **{**body, "mutation_id": str(uuid4())})
        self.assertEqual(Book.objects.get(item=self.book).current_session.start_date.date(), DEC_31)
        self.assertEqual(Game.objects.get(item=self.game).current_session.start_date, DEC_31)
        self.assertEqual(Manga.objects.get(item=self.manga).current_attempt.start_date, DEC_31)


@override_settings(TIME_ZONE=NEW_YORK)
class FlightWestTests(NewYearBase):
    """Started in Tokyo on Jan 1, then in Los Angeles the phone says Dec 31."""

    def setUp(self):
        super().setUp()
        self.clock.set(IN_TOKYO)
        tokyo = self.phone(TOKYO)
        self.book = self.item(MediaTypes.BOOK.value, total_pages=400)
        self.game = self.item(MediaTypes.GAME.value)
        self.manga = self.item(MediaTypes.MANGA.value, series_total=100)
        self.comic = self.item(MediaTypes.COMIC.value, series_total=100)
        day = {"start_date": str(JAN_1)}
        self.post(tokyo, self.book, "actions/start/", **day)
        self.post(tokyo, self.game, "actions/start/", **day)
        self.post(tokyo, self.manga, "actions/start/", **day)
        self.post(tokyo, self.comic, "actions/start/", **day)
        self.clock.set(AFTER_FLYING_WEST)
        self.west = self.phone(LOS_ANGELES)

    def test_saving_keeps_the_phones_date_and_the_stored_order(self):
        self.post(self.west, self.book, "progress/", progress_type="pages", value=40)
        self.post(self.west, self.game, "progress/", total_minutes=60, progressed_on=str(DEC_31))
        self.post(self.west, self.manga, "progress/", value=5)
        for item in (self.book, self.game, self.manga):
            self.assertEqual(self.latest_day(item), DEC_31)
        self.assertEqual(BookSession.objects.get(related_book__item=self.book).progressed_on, JAN_1)
        self.assertEqual(GameSession.objects.get(related_game__item=self.game).minutes_updated_on, JAN_1)
        self.assertEqual(Manga.objects.get(item=self.manga).current_attempt.progressed_on, JAN_1)

    def test_a_save_stays_no_earlier_than_the_last_one(self):
        """Started before the flight, so only the last save holds it up (GM-013)."""
        self.clock.set(datetime(2026, 12, 20, 12, 0, tzinfo=NEW_YORK_TZ))
        home = self.phone(NEW_YORK)
        game = self.item(MediaTypes.GAME.value)
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        manga = self.item(MediaTypes.MANGA.value, series_total=100)
        for item in (game, book, manga):
            self.post(home, item, "actions/start/", start_date="2026-12-20")
        self.clock.set(IN_TOKYO)
        tokyo = self.phone(TOKYO)
        self.post(tokyo, game, "progress/", total_minutes=60, progressed_on=str(JAN_1))
        self.post(tokyo, book, "progress/", progress_type="pages", value=40)
        self.post(tokyo, manga, "progress/", value=5)
        self.clock.set(AFTER_FLYING_WEST)
        self.post(self.west, game, "progress/", total_minutes=90, percentage=10, progressed_on=str(DEC_31))
        self.post(self.west, book, "progress/", progress_type="pages", value=50)
        self.post(self.west, manga, "progress/", value=6)
        session = GameSession.objects.get(related_game__item=game)
        self.assertEqual(session.minutes_updated_on, JAN_1)
        self.assertEqual(session.percentage_updated_on, JAN_1)
        self.assertEqual(BookSession.objects.get(related_book__item=book).progressed_on, JAN_1)
        self.assertEqual(Manga.objects.get(item=manga).current_attempt.progressed_on, JAN_1)
        for item in (game, book, manga):
            self.assertEqual(self.latest_day(item), DEC_31)

    def test_stop_with_the_phones_request_body_lands_on_the_start(self):
        for item in (self.book, self.game, self.manga, self.comic):
            self.post(self.west, item, "actions/drop/", end_date=str(DEC_31))
        self.assertEqual(BookSession.objects.get(related_book__item=self.book).end_date.date(), JAN_1)
        self.assertEqual(GameSession.objects.get(related_game__item=self.game).end_date, JAN_1)
        self.assertEqual(Manga.objects.get(item=self.manga).current_attempt.end_date, JAN_1)
        self.assertEqual(Comic.objects.get(item=self.comic).current_attempt.end_date, JAN_1)

    def test_restart_with_the_phones_request_body(self):
        for item in (self.book, self.game, self.manga):
            self.post(
                self.west,
                item,
                "actions/restart/",
                start_date=str(DEC_31),
                end_date=str(DEC_31),
                mutation_id=str(uuid4()),
            )
        old, new = BookSession.objects.filter(related_book__item=self.book).order_by("id")
        self.assertEqual((old.end_date.date(), new.start_date.date()), (JAN_1, DEC_31))
        old, new = GameSession.objects.filter(related_game__item=self.game).order_by("id")
        self.assertEqual((old.end_date, new.start_date), (JAN_1, DEC_31))

    def test_start_with_the_phones_request_body_after_a_stop(self):
        self.post(self.west, self.game, "actions/drop/", end_date=str(DEC_31))
        self.post(self.west, self.game, "actions/start/", start_date=str(DEC_31))
        self.assertEqual(Game.objects.get(item=self.game).current_session.start_date, DEC_31)

    def test_a_games_status_set_to_dropped_has_no_date_to_refuse(self):
        response = self.west.patch(self.url(self.game), {"status": Status.DROPPED.value}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(GameSession.objects.get(related_game__item=self.game).end_date, JAN_1)

    def test_a_book_log_on_the_phones_date_stays_in_order(self):
        self.post(self.west, self.book, "complete/", expected=201, completion_date=str(DEC_31), mutation_id=str(uuid4()))
        self.assertEqual(BookSession.objects.get(related_book__item=self.book).end_date.date(), JAN_1)

    def test_a_game_log_on_the_phones_date_stays_in_order(self):
        self.post(self.west, self.game, "progress/", total_minutes=60, progressed_on=str(DEC_31))
        self.post(self.west, self.game, "complete/", expected=201, completion_date=str(DEC_31), mutation_id=str(uuid4()))
        session = GameSession.objects.get(related_game__item=self.game)
        self.assertEqual(session.end_date, JAN_1)
        self.assertEqual(session.completion_diary_entry.consumed_at, datetime(2027, 1, 1, tzinfo=UTC))
        self.assertTrue(session.minutes_linked)

    def test_a_game_logs_date_edit_to_the_phones_date_stays_in_order(self):
        self.clock.set(IN_TOKYO)
        self.post(self.phone(TOKYO), self.game, "complete/", expected=201, completion_date=str(JAN_1), mutation_id=str(uuid4()))
        self.clock.set(AFTER_FLYING_WEST)
        entry = GameSession.objects.get(related_game__item=self.game).completion_diary_entry
        response = self.west.patch(f"/api/v1/diary/{entry.pk}/", {"consumed_at": str(DEC_31)}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(GameSession.objects.get(related_game__item=self.game).end_date, JAN_1)

    def test_a_different_date_before_the_start_keeps_the_refusal(self):
        self.post(self.west, self.book, "actions/drop/", expected=400, end_date=str(DEC_30))
        self.post(self.west, self.game, "actions/drop/", expected=400, end_date=str(DEC_30))
        self.post(self.west, self.manga, "actions/drop/", expected=400, end_date=str(DEC_30))
        self.post(self.west, self.game, "progress/", expected=400, total_minutes=60, progressed_on=str(DEC_30))
        self.post(self.west, self.book, "progress/", expected=400, progress_type="pages", value=40, progressed_on=str(DEC_30))
        self.post(self.west, self.manga, "progress/", expected=400, value=5, progressed_on=str(DEC_30))
        self.post(self.west, self.game, "complete/", expected=400, completion_date=str(DEC_30), mutation_id=str(uuid4()))


@override_settings(TIME_ZONE=NEW_YORK)
class NoHeaderTests(NewYearBase):
    """Web, jobs and imports send no zone, so the stored calendar decides."""

    def test_a_tokyo_account_at_8pm_new_york_can_use_its_new_day(self):
        tokyo = make_user(calendar=TOKYO)
        self.clock.set(datetime(2026, 12, 31, 20, 0, tzinfo=NEW_YORK_TZ))
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        game = self.item(MediaTypes.GAME.value)
        book_tracking.start(tokyo, book, start_date=JAN_1)
        book_tracking.update_progress(tokyo, book, progress_type="pages", value=40)
        book_tracking.drop(tokyo, book, end_date=JAN_1)
        game_tracking.start(tokyo, game, start_date=JAN_1)
        game_tracking.update_progress(tokyo, game, total_minutes=60, progressed_on=JAN_1)
        game_tracking.complete(tokyo, game, completion_date=JAN_1)
        self.assertEqual(self.latest_day(book), JAN_1)
        self.assertEqual(GameSession.objects.get(related_game__item=game).end_date, JAN_1)

    def test_a_los_angeles_account_at_1am_new_york_stays_on_its_old_day(self):
        west = make_user(calendar=LOS_ANGELES)
        self.clock.set(datetime(2027, 1, 1, 1, 0, tzinfo=NEW_YORK_TZ))
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        game = self.item(MediaTypes.GAME.value)
        book_tracking.start(west, book)
        book_tracking.update_progress(west, book, progress_type="pages", value=40)
        book_tracking.drop(west, book)
        game_tracking.start(west, game)
        game_tracking.update_progress(west, game, total_minutes=60)
        game_tracking.drop(west, game)
        session = BookSession.objects.get(related_book__item=book)
        self.assertEqual((session.start_date.date(), session.end_date.date()), (DEC_31, DEC_31))
        self.assertEqual(GameSession.objects.get(related_game__item=game).end_date, DEC_31)
        self.assertEqual(self.latest_day(book), DEC_31)

    def test_a_margins_row_for_a_los_angeles_account_at_1am_new_york(self):
        west = make_user(calendar=LOS_ANGELES)
        self.clock.set(datetime(2027, 1, 1, 1, 0, tzinfo=NEW_YORK_TZ))
        csv = (
            "Title,Author(s),Status,Start Date,End Date,Rating,Review\n"
            "Current Read,An Author,Reading,,,,\n"
            "Tomorrow's Read,An Author,Finished,,2027-01-01,,\n"
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

        with mock.patch("app.models.Item.fetch_releases"):
            importer = MarginsImporter(SimpleUploadedFile("m.csv", csv.encode()), west, "new", resolver=Resolver())
            importer.import_data()
        session = BookSession.objects.get(related_book__user=west, related_book__item__media_id="Current Read")
        self.assertEqual(session.start_date.date(), DEC_31)
        # A date after the person's today counts as unknown, as any future date does.
        tomorrow = Book.objects.get(user=west, item__media_id="Tomorrow's Read")
        self.assertTrue(tomorrow.completed_manually)

    def diary_post(self, user, item, consumed_at):
        client = APIClient()
        client.force_authenticate(user)
        ref = {"source": item.source, "media_type": item.media_type, "media_id": item.media_id}
        return client.post(
            "/api/v1/diary/",
            {"ref": ref, "consumed_at": consumed_at, "mutation_id": str(uuid4())},
            format="json",
        )

    def test_a_tokyo_accounts_log_through_the_diary_uses_its_new_day(self):
        tokyo = make_user(calendar=TOKYO)
        self.clock.set(datetime(2026, 12, 31, 20, 0, tzinfo=NEW_YORK_TZ))
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        response = self.diary_post(tokyo, book, str(JAN_1))
        self.assertEqual(response.status_code, 201, response.data)
        entry = DiaryEntry.objects.get(user=tokyo, item=book)
        self.assertEqual(entry.consumed_at, datetime(2027, 1, 1, tzinfo=UTC))
        client = APIClient()
        client.force_authenticate(tokyo)
        response = client.patch(f"/api/v1/diary/{entry.pk}/", {"consumed_at": str(JAN_1)}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

    def test_a_los_angeles_accounts_moment_through_the_diary_stays_on_its_day(self):
        west = make_user(calendar=LOS_ANGELES)
        self.clock.set(datetime(2027, 1, 1, 1, 0, tzinfo=NEW_YORK_TZ))
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        response = self.diary_post(west, book, datetime(2027, 1, 1, 6, 0, tzinfo=UTC).isoformat())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(DiaryEntry.objects.get(user=west, item=book).consumed_at, datetime(2026, 12, 31, tzinfo=UTC))

    def web_get_and_post_log(self, user, item):
        """Post the log modal's own date, as its form does."""
        browser = Client()
        browser.force_login(user)
        metadata = {"title": item.title, "image": item.image, "max_progress": 1, "details": {}, "total_pages": None}
        args = [item.source, item.media_type, item.media_id]
        with mock.patch("app.views.services.get_media_metadata", return_value=metadata):
            default = browser.get(reverse("log_modal", args=args)).context["today"]
            return browser, default, browser.post(reverse("add_movie_diary_entry", args=args), {"watch_date": str(default)})

    def test_a_tokyo_accounts_web_film_and_album_log_with_the_modals_own_date(self):
        tokyo = make_user(calendar=TOKYO)
        self.clock.set(datetime(2026, 12, 31, 20, 0, tzinfo=NEW_YORK_TZ))
        for media_type in (MediaTypes.MOVIE.value, MediaTypes.MUSIC.value):
            with self.subTest(media_type):
                item = self.item(media_type)
                _, default, response = self.web_get_and_post_log(tokyo, item)
                self.assertEqual(default, JAN_1)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(DiaryEntry.objects.get(user=tokyo, item=item).consumed_at, datetime(2027, 1, 1, tzinfo=UTC))

    def test_a_tokyo_accounts_web_film_log_date_edit(self):
        tokyo = make_user(calendar=TOKYO)
        self.clock.set(datetime(2026, 12, 30, 20, 0, tzinfo=NEW_YORK_TZ))
        film = self.item(MediaTypes.MOVIE.value)
        browser, _, _ = self.web_get_and_post_log(tokyo, film)
        self.clock.set(datetime(2026, 12, 31, 20, 0, tzinfo=NEW_YORK_TZ))
        entry = DiaryEntry.objects.get(user=tokyo, item=film)
        response = browser.post(reverse("update_diary_entry", args=[entry.pk]), {"watch_date": str(JAN_1)})
        self.assertEqual(response.status_code, 200, response.content)
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at, datetime(2027, 1, 1, tzinfo=UTC))

    def test_a_tokyo_accounts_imported_film_log_on_its_new_day(self):
        tokyo = make_user(calendar=TOKYO)
        self.clock.set(datetime(2026, 12, 31, 20, 0, tzinfo=NEW_YORK_TZ))
        film = self.item(MediaTypes.MOVIE.value)
        rows = [{"source_id": "1", "source_order": 0, "consumed_at": JAN_1, "is_rewatch": False}]
        created = single_weight.import_logs(tokyo, film, rows, source="letterboxd")
        self.assertEqual([entry.consumed_at for entry in created], [datetime(2027, 1, 1, tzinfo=UTC)])

    def test_a_web_book_log_with_no_date_for_a_los_angeles_account_at_1am_new_york(self):
        west = make_user(calendar=LOS_ANGELES)
        self.clock.set(datetime(2027, 1, 1, 1, 0, tzinfo=NEW_YORK_TZ))
        book = self.item(MediaTypes.BOOK.value, total_pages=400)
        browser = Client()
        browser.force_login(west)
        metadata = {"title": book.title, "image": book.image, "max_progress": 400, "total_pages": 400}
        with mock.patch("app.views.services.get_media_metadata", return_value=metadata):
            response = browser.post(
                reverse("add_movie_diary_entry", args=[book.source, book.media_type, book.media_id]),
                {"auto_mark_consumed": "true", "book_complete": "true", "watch_date": ""},
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(DiaryEntry.objects.get(user=west, item=book).consumed_at, datetime(2026, 12, 31, tzinfo=UTC))
        self.assertEqual(Book.objects.get(user=west, item=book).end_date, datetime(2026, 12, 31, tzinfo=UTC))

    def test_a_web_finish_for_a_los_angeles_account_at_1am_new_york(self):
        west = make_user(calendar=LOS_ANGELES)
        self.clock.set(datetime(2027, 1, 1, 1, 0, tzinfo=NEW_YORK_TZ))
        item = self.item(MediaTypes.BOOK.value, total_pages=400)
        browser = Client()
        browser.force_login(west)
        metadata = {"title": item.title, "image": item.image, "max_progress": 400, "total_pages": 400}
        with mock.patch("app.views.services.get_media_metadata", return_value=metadata):
            response = browser.post(reverse("log_book_completed", args=[item.source, item.media_id]), {})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Book.objects.get(user=west, item=item).completion_diary_entry.consumed_at, datetime(2026, 12, 31, tzinfo=UTC))
