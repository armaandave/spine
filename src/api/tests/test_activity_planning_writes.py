"""AC-1: a title added to Planning is recorded once, by the request that adds it.

Only a person's own request is an add: the app, the website and the API. An import writes
tracking rows directly and never records one (SW-500). A planning row adds nothing to any
Stats number, is no active day, and no family's calculation reads it.
"""

from datetime import UTC, date, datetime
from unittest import mock

from django.test import Client

from api.tests.test_activity_status_rows import (
    NOON,
    StatusRowBase,
)
from app import anime_tracking, book_tracking, tv_tracking
from app.models import Item, MediaTypes, Sources, Status
from app.tests.anime_fixtures import FakeAnimeProvider, make_item
from app.tests.tv_fixtures import FakeTVProvider, make_show
from integrations.imports.margins.importer import MarginsImporter
from social.models import ProgressChange

NEW_YORK = "America/New_York"
PLANNING = Status.PLANNING.value


class PlanningWriteBase(StatusRowBase):
    """A reader, and the rows that say a title was added to Planning."""

    def planned(self, item=None):
        rows = ProgressChange.objects.filter(actor=self.user, current_progress__kind=ProgressChange.PLANNING)
        return rows.filter(item=item) if item else rows

    def put(self, item, status=PLANNING, **extra):
        return self.write("put", item, status, **extra)

    def write(self, method, item, status, **extra):
        from rest_framework.test import APIClient

        client = APIClient(HTTP_X_SPINE_TIMEZONE=NEW_YORK)
        client.force_authenticate(self.user)
        path = f"/api/v1/tracking/{item.source}/{item.media_type}/{item.media_id}/"
        response = getattr(client, method)(path, {"status": status, **extra}, format="json")
        self.assertIn(response.status_code, (200, 201), response.content)
        return response


class RequestAddTests(PlanningWriteBase):
    """The app's and the API's tracking write."""

    def test_each_type_with_a_planning_button_writes_one_row_with_its_day(self):
        for media_type in ("movie", "music", "book", "game", "manga", "comic"):
            with self.subTest(media_type):
                item = self.item(media_type)
                self.put(item)
                row = self.planned(item).get()
                shape = {"kind": "planning", "value": 0, "max": None, "unit": "planning"}
                self.assertEqual(row.current_progress, shape)
                self.assertEqual(row.previous_progress, shape)
                self.assertEqual(row.saved_on, date(2026, 10, 25))

    def test_a_repeat_writes_none_and_patch_works_the_same(self):
        item = self.item("movie")
        self.put(item)
        self.put(item)
        self.write("patch", item, PLANNING)
        self.assertEqual(self.planned(item).count(), 1)
        other = self.item("book")
        self.write("patch", other, PLANNING)
        self.assertEqual(self.planned(other).count(), 1)

    def test_leaving_planning_and_coming_back_is_a_second_add(self):
        item = self.item("movie")
        self.put(item)
        self.put(item, Status.DROPPED.value)
        self.later(24 * 60)
        self.put(item)
        self.assertEqual(list(self.planned(item).order_by("id").values_list("saved_on", flat=True)), [date(2026, 10, 25), date(2026, 10, 26)])

    def test_any_other_status_writes_none(self):
        for media_type, status in (("movie", Status.COMPLETED.value), ("game", Status.IN_PROGRESS.value), ("book", Status.IN_PROGRESS.value)):
            item = self.item(media_type)
            self.put(item, status)
            self.assertFalse(self.planned(item).exists(), media_type)

    def test_a_heart_or_stars_on_an_untracked_movie_is_not_an_add(self):
        item = self.item("movie")
        self.write("put", item, Status.COMPLETED.value, rating=4)
        self.assertFalse(self.planned().exists())

    def test_the_day_is_the_owners_calendar_day(self):
        item = self.item("movie")
        # 23:30 then 00:30 in New York.
        self.clock.set(datetime(2026, 10, 26, 3, 30, tzinfo=UTC))
        self.put(item)
        other = self.item("movie")
        self.clock.set(datetime(2026, 10, 26, 4, 30, tzinfo=UTC))
        self.put(other)
        self.assertEqual(self.planned(item).get().saved_on, date(2026, 10, 25))
        self.assertEqual(self.planned(other).get().saved_on, date(2026, 10, 26))

    def test_a_book_with_an_open_journey_refuses_and_writes_none(self):
        book = self.item("book", total_pages=300)
        book_tracking.start(self.user, book)
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(self.user)
        response = client.put(f"/api/v1/tracking/manual/book/{book.media_id}/", {"status": PLANNING}, format="json")
        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(self.planned(book).exists())


class ShowAddTests(PlanningWriteBase):
    """A show and an anime choose Planning where they choose any status."""

    def setUp(self):
        super().setUp()
        self.tv = FakeTVProvider().__enter__()
        self.addCleanup(self.tv.__exit__, None, None, None)
        self.show = make_show(self.tv, title="Dark", seasons={1: 3})

    def test_the_choice_writes_one_row_and_a_repeat_none(self):
        tv_tracking.choose(self.user, self.show, tv_tracking.PLANNING)
        tv_tracking.choose(self.user, self.show, tv_tracking.PLANNING)
        self.assertEqual(self.planned(self.show).count(), 1)

    def test_through_the_tracking_endpoint(self):
        self.put(self.show)
        self.assertEqual(self.planned(self.show).count(), 1)

    def test_a_webhook_and_a_job_add_none(self):
        tv_tracking.choose(self.user, self.show, tv_tracking.PLANNING, entry_point=tv_tracking.WEBHOOK)
        self.assertFalse(self.planned().exists())

    def test_an_anime_choice_writes_one(self):
        fake = FakeAnimeProvider().__enter__()
        self.addCleanup(fake.__exit__, None, None, None)
        anime = make_item(fake, total=12)
        anime_tracking.choose(self.user, anime, anime_tracking.PLANNING)
        anime_tracking.choose(self.user, anime, anime_tracking.PLANNING)
        self.assertEqual(self.planned(anime).count(), 1)


class WebsiteAddTests(PlanningWriteBase):
    """The website's manual title form."""

    def post(self, media_type, **fields):
        client = Client()
        client.force_login(self.user)
        return client.post("/create", {"media_type": media_type, "title": f"Web {media_type}", **fields})

    def test_a_movie_a_book_and_a_game_added_as_planning(self):
        for media_type in ("movie", "book", "game"):
            with self.subTest(media_type):
                self.post(media_type, status=PLANNING, **({"progress": 0} if media_type == "book" else {}))
                item = Item.objects.get(media_type=media_type, title=f"Web {media_type}")
                self.assertEqual(self.planned(item).count(), 1)

    def test_a_movie_added_as_completed_writes_none(self):
        self.post("movie", status=Status.COMPLETED.value)
        self.assertFalse(self.planned().exists())


class ImportsAddNone(PlanningWriteBase):
    """An import, even one that puts titles in Planning, records nothing (SW-500)."""

    def test_margins_to_read_makes_a_planning_book_and_no_row(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        csv = "Title,Author(s),Status,Start Date,End Date,Rating,Review\nWant,An Author,Want to Read,,,,\n"

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
        from app.models import Book

        self.assertEqual(Book.objects.get(user=self.user).status, PLANNING)
        self.assertFalse(self.planned().exists())

    def test_a_letterboxd_watchlist_makes_planning_movies_and_no_row(self):
        from integrations.imports.letterboxd.importer import LetterboxdImporter
        from integrations.tests.imports.test_letterboxd import FakeResolver, export_zip, tmdb_movie

        payload = export_zip(
            {
                "watchlist.csv": (
                    "Date,Name,Year,Letterboxd URI\n"
                    "2024-01-05,Film Three,2001,https://boxd.it/film-three\n"
                    "2024-01-06,Film Two,2000,https://boxd.it/film-two\n"
                ),
            },
        )
        with (
            mock.patch("integrations.imports.letterboxd.importer.tmdb.movie", side_effect=tmdb_movie),
            mock.patch("app.models.providers.services.get_media_metadata", return_value={"max_progress": 1}),
            mock.patch("app.services.update_daily_statistics.delay"),
            mock.patch("app.signals.update_daily_statistics.delay"),
        ):
            counts, _ = LetterboxdImporter(payload, self.user, "new", resolver=FakeResolver()).import_data()
        self.assertEqual(counts["watchlist"], 2)
        self.assertFalse(self.planned().exists())
