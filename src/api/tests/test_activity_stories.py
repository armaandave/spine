"""Stories told across the calculation, and marks that never touch a year (decision 11).

Matrix 11 joins each family at its switch, so this holds books, games, manga and
comics (anime's are in test_activity_calc_anime): every way to mark a title, on a new
one and on one first set to To Read, because today's leak needs a previous history row.
Marking as watched, read, played or listened is never activity, never in a year and
never on a feed. It only feeds All Time's undated pile.
"""

from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse

from api.services.activity_rows import piled
from api.tests.test_activity_book_imports import OneBook, goodreads_file
from api.tests.test_activity_calc_books import BOOK, NEW_YORK, BookCalcBase, d
from api.tests.test_stats_v2_activity import ALL_TIME, YEAR_2025, YEAR_2026
from app import book_tracking, game_tracking, series_tracking
from app.models import Book, DiaryEntry, Item, MediaTypes, Sources, Status
from integrations import exports
from integrations.imports import goodreads, yamtrack

ACTIVITY = "/api/v1/users/owner/activity/"
LIFETIME_SECTIONS = ("list_rings", "collections")


def frozen(payload):
    """Leave out the two sections STAT-213 and STAT-214 make lifetime on purpose."""
    return {key: value for key, value in payload.items() if key not in LIFETIME_SECTIONS}


@override_settings(TIME_ZONE=NEW_YORK)
class MarkMatrixTests(BookCalcBase):
    """Each mark: every dated year identical, no row, All Time moves by the pile."""

    PAGES = 350

    def setUp(self):
        super().setUp()
        self.other = self.book("other", pages=100)
        self.begin(self.other, d(2026, 2, 1))
        self.save(self.other, 40, d(2026, 2, 2))
        self.finish(self.book("finished", pages=200), d(2025, 3, 1))

    def snapshot(self):
        """Return what a mark can't change, as the phone reads it, for three viewers."""
        seen = []
        for viewer in (self.owner, self.follower, self.stranger):
            for params in (YEAR_2025, YEAR_2026):
                seen.append(frozen(self.summary(params, viewer=viewer)))
                seen.append(frozen(self.summary(params, viewer=viewer, tab=BOOK)))
            response = self.client_for(viewer).get(ACTIVITY)
            self.assertEqual(response.status_code, 200, response.content)
            seen.append(response.json())
        return seen

    def all_time(self):
        rows = self.summary(ALL_TIME, tab=BOOK)["time"]["rows"]
        return next(row for row in rows if row["media_type"] == BOOK)["value"]

    def check(self, mark):
        """Run a mark on a new title and on one first set to To Read."""
        for planning in (False, True):
            with self.subTest(planning=planning):
                item = self.book(f"{mark.__name__}-{planning}", pages=self.PAGES)
                if planning:
                    self.do(book_tracking.assign_status, item, d(2026, 1, 20), Status.PLANNING.value)
                before, value = self.snapshot(), self.all_time()
                days = self.summary(ALL_TIME, tab=BOOK)["header"]["active_days"]
                with self.on(d(2026, 3, 1)):
                    mark(item)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.all_time(), value + self.PAGES)
                self.assertEqual(self.summary(ALL_TIME, tab=BOOK)["header"]["active_days"], days)
                self.assertFalse([row for row in self.calc().rows if row.title_id == item.pk])

    def eye(self, item):
        book_tracking.mark_read(self.owner, item)

    def heart(self, item):
        book_tracking.set_like(self.owner, item, liked=True)

    def stars(self, item):
        book_tracking.set_rating(self.owner, item, 7)

    def status_completed(self, item):
        book_tracking.assign_status(self.owner, item, Status.COMPLETED.value)

    def test_the_eye(self):
        self.check(self.eye)

    def test_a_heart(self):
        self.check(self.heart)

    def test_stars(self):
        self.check(self.stars)

    def test_status_set_to_completed(self):
        self.check(self.status_completed)

    def test_unhearting_keeps_the_mark(self):
        item = self.book("unheart", pages=self.PAGES)
        with self.on(d(2026, 3, 1)):
            self.heart(item)
        before, value = self.snapshot(), self.all_time()
        with self.on(d(2026, 3, 2)):
            book_tracking.set_like(self.owner, item, liked=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.all_time(), value)

    def test_an_undated_import(self):
        Item.objects.create(
            media_id="hc-1001",
            source=Sources.HARDCOVER.value,
            media_type=BOOK,
            title="Atlas of Quiet Signals",
            image="https://example.com/book.jpg",
            total_pages=self.PAGES,
        )
        before, value = self.snapshot(), self.all_time()
        with patch("app.models.Item.fetch_releases"), patch("app.signals.update_daily_statistics.delay"):
            goodreads.GoodReadsImporter(
                BytesIO(goodreads_file(**{"Date Read": ""})),
                self.owner,
                "new",
                resolver=OneBook(),
            ).import_data()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.all_time(), value + self.PAGES)

    def test_the_web_form_completed_with_the_date_it_fills_in(self):
        """mediaStatusDateHandler.js fills today on any move to Completed."""
        self.client.force_login(self.owner)
        metadata = {"title": "t", "image": "https://example.com/a.jpg", "max_progress": self.PAGES}

        def web_form(item):
            with patch("app.views.services.get_media_metadata", return_value=metadata), patch("app.models.Item.fetch_releases"):
                book = Book.objects.filter(user=self.owner, item=item).first()
                response = self.client.post(
                    reverse("media_save"),
                    {
                        "media_id": item.media_id,
                        "source": item.source,
                        "media_type": BOOK,
                        "status": Status.COMPLETED.value,
                        "end_date": "2026-03-01",
                        "progress": "0",
                        **({"instance_id": book.pk} if book else {}),
                    },
                )
            self.assertLess(response.status_code, 400, response.content)
            book = Book.objects.get(user=self.owner, item=item)
            self.assertTrue(book.completed_manually)
            self.assertIsNone(book.end_date)

        self.check(web_form)

    def web_form(self, item, status=Status.COMPLETED.value, **fields):
        """Post the status form as the browser does, on Mar 1."""
        book = Book.objects.filter(user=self.owner, item=item).first()
        metadata = {"title": "t", "image": "https://example.com/a.jpg", "max_progress": self.PAGES}
        self.client.force_login(self.owner)
        with (
            patch("app.views.services.get_media_metadata", return_value=metadata),
            patch("app.models.Item.fetch_releases"),
            self.on(d(2026, 3, 1)),
        ):
            response = self.client.post(
                reverse("media_save"),
                {
                    "media_id": item.media_id,
                    "source": item.source,
                    "media_type": BOOK,
                    "status": status,
                    "progress": "0",
                    **({"instance_id": book.pk} if book else {}),
                    **fields,
                },
            )
        self.assertLess(response.status_code, 400, response.content)

    def test_the_web_form_completed_with_an_earlier_date_is_a_log_on_it(self):
        """The person typed it: a date before today is a read, as a file's row is."""
        item = self.book("web-typed", pages=self.PAGES)
        self.web_form(item, end_date="2025-06-01", score="8", notes="kept")
        self.assertEqual(self.rows(item), ["2025-06-01 log +350"])
        book = Book.objects.get(user=self.owner, item=item)
        self.assertEqual((book.status, book.completed_manually, book.notes, book.score), (Status.COMPLETED.value, False, "kept", 8))
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), 40)

    def test_the_web_form_keeps_that_date_on_a_book_at_to_read(self):
        item = self.book("web-typed-planned", pages=self.PAGES)
        self.do(book_tracking.assign_status, item, d(2026, 1, 20), Status.PLANNING.value)
        self.web_form(item, end_date="2025-06-01")
        self.assertEqual(self.rows(item), ["2025-06-01 log +350"])

    def test_the_web_form_with_a_date_past_today_is_the_mark(self):
        item = self.book("web-ahead", pages=self.PAGES)
        self.web_form(item, end_date="2026-03-02")
        self.assertTrue(Book.objects.get(user=self.owner, item=item).completed_manually)
        self.assertEqual(self.rows(item), [])

    def test_the_web_form_refuses_an_earlier_date_on_a_read_under_way(self):
        item = self.book("web-typed-open", pages=self.PAGES)
        self.begin(item, d(2026, 2, 1))
        self.web_form(item, end_date="2025-06-01")
        self.assertEqual(Book.objects.get(user=self.owner, item=item).status, Status.IN_PROGRESS.value)
        self.assertFalse(DiaryEntry.objects.filter(user=self.owner, item=item).exists())

    def test_the_web_form_moving_an_old_mark_on_doubles_nothing(self):
        """A mark from before the contract holds its length, and the form posts it."""
        item = self.book("web-old-mark", pages=self.PAGES)
        Book.objects.create(user=self.owner, item=item, status=Status.COMPLETED.value, completed_manually=True, progress=self.PAGES)
        before, value = self.snapshot(), self.all_time()
        self.web_form(item, Status.PLANNING.value, progress=str(self.PAGES), notes="kept")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.all_time(), value)
        book = Book.objects.get(user=self.owner, item=item)
        self.assertEqual((book.status, book.progress, book.notes), (Status.PLANNING.value, 0, "kept"))

    def test_the_web_form_moving_a_read_row_on_invents_no_place(self):
        item = self.book("web-read-row", pages=self.PAGES)
        Book.objects.create(user=self.owner, item=item, status=Status.COMPLETED.value, progress=self.PAGES)
        value = self.all_time()
        self.web_form(item, Status.DROPPED.value, progress=str(self.PAGES))
        self.assertEqual(self.all_time(), value)

    def test_the_web_form_moving_a_read_to_currently_reading_begins_a_reread(self):
        item = self.book("web-reread", pages=self.PAGES)
        self.finish(item, d(2025, 6, 1))
        self.web_form(item, Status.IN_PROGRESS.value, progress=str(self.PAGES))
        book = Book.objects.get(user=self.owner, item=item)
        self.assertEqual((book.status, book.progress), (Status.IN_PROGRESS.value, 0))
        self.assertEqual(self.rows(item), ["2026-03-01 started", "2025-06-01 log +350"])

    def test_the_web_form_writes_a_new_row_at_another_status_as_it_did(self):
        """A new row's default status is Completed, and it hasn't left any Read."""
        item = self.book("web-new-row", pages=self.PAGES)
        self.web_form(item, Status.IN_PROGRESS.value, progress="50")
        book = Book.objects.get(user=self.owner, item=item)
        self.assertEqual((book.status, book.progress, book.current_session_id), (Status.IN_PROGRESS.value, 50, None))

    def test_the_web_form_mark_comes_back_from_your_own_file_as_a_mark(self):
        item = self.book("web-export", pages=self.PAGES)
        Item.objects.filter(pk=item.pk).update(image="https://example.com/a.jpg")
        self.client.force_login(self.owner)
        with patch("app.models.Item.fetch_releases"), self.on(d(2026, 3, 1)):
            self.client.post(
                reverse("media_save"),
                {
                    "instance_id": Book.objects.create(user=self.owner, item=item, status=Status.PLANNING.value).pk,
                    "media_id": item.media_id,
                    "source": item.source,
                    "media_type": BOOK,
                    "status": Status.COMPLETED.value,
                    "end_date": "2026-03-01",
                    "progress": "0",
                },
            )
        marked = self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), self.number(pile=True)
        payload = "".join(exports.generate_rows(self.owner)).encode()
        with patch("app.models.Item.fetch_releases"), patch("app.signals.update_daily_statistics.delay"):
            yamtrack.importer(BytesIO(payload), self.owner, "new")
        self.assertFalse(DiaryEntry.objects.filter(user=self.owner, item=item).exists())
        self.assertEqual((self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), self.number(pile=True)), marked)
        Book.objects.filter(user=self.owner, item=item).delete()
        with patch("app.models.Item.fetch_releases"), patch("app.signals.update_daily_statistics.delay"):
            yamtrack.importer(BytesIO(payload), self.owner, "new")
        self.assertFalse(DiaryEntry.objects.filter(user=self.owner, item=item).exists())
        self.assertEqual((self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), self.number(pile=True)), marked)

    def test_the_web_form_keeps_its_notes_and_score_on_the_mark(self):
        item = self.book("web-notes", pages=self.PAGES)
        book = Book.objects.create(user=self.owner, item=item, status=Status.PLANNING.value)
        self.client.force_login(self.owner)
        with patch("app.models.Item.fetch_releases"), self.on(d(2026, 3, 1)):
            self.client.post(
                reverse("media_save"),
                {
                    "instance_id": book.pk,
                    "media_id": item.media_id,
                    "source": item.source,
                    "media_type": BOOK,
                    "status": Status.COMPLETED.value,
                    "score": "8.5",
                    "notes": "kept",
                    "progress": "0",
                },
            )
        book.refresh_from_db()
        self.assertTrue(book.completed_manually)
        self.assertEqual((book.notes, book.score), ("kept", Decimal("8.5")))

    def test_the_web_form_refuses_completed_on_a_read_under_way(self):
        item = self.book("web-open", pages=self.PAGES)
        self.begin(item, d(2026, 2, 1))
        book = Book.objects.get(user=self.owner, item=item)
        self.client.force_login(self.owner)
        with patch("app.models.Item.fetch_releases"), self.on(d(2026, 3, 1)):
            self.client.post(
                reverse("media_save"),
                {
                    "instance_id": book.pk,
                    "media_id": item.media_id,
                    "source": item.source,
                    "media_type": BOOK,
                    "status": Status.COMPLETED.value,
                    "progress": "0",
                },
            )
        book.refresh_from_db()
        self.assertEqual(book.status, Status.IN_PROGRESS.value)
        self.assertFalse(book.completed_manually)

    def test_the_web_form_saving_notes_on_a_read_book_marks_nothing(self):
        item = self.book("web-read", pages=self.PAGES)
        self.finish(item, d(2026, 2, 1))
        book = Book.objects.get(user=self.owner, item=item)
        self.client.force_login(self.owner)
        with patch("app.models.Item.fetch_releases"), self.on(d(2026, 3, 1)):
            self.client.post(
                reverse("media_save"),
                {
                    "instance_id": book.pk,
                    "media_id": item.media_id,
                    "source": item.source,
                    "media_type": BOOK,
                    "status": Status.COMPLETED.value,
                    "notes": "later",
                    "progress": str(book.progress),
                },
            )
        book.refresh_from_db()
        self.assertEqual(book.notes, "later")
        self.assertFalse(book.completed_manually)

    def test_the_phones_requests_too(self):
        item = self.item(BOOK, "phone", source=Sources.MANUAL.value, title="phone")
        self.set_item(item, total_pages=self.PAGES)
        before, value = self.snapshot(), self.all_time()
        client = self.client_for(self.owner, NEW_YORK)
        with self.on(d(2026, 3, 1)):
            response = client.post(f"/api/v1/tracking/manual/book/{item.media_id}/actions/mark_read/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.all_time(), value + self.PAGES)


class TypeMarkMatrix:
    """Matrix 11 on one more type: a mark moves no year and no row, only the pile.

    A mixin, so only the types below run it.
    """

    media_type = None
    tracking = None
    # What a mark adds to All Time: a series' total; a game has no hours to add.
    length = 0

    def setUp(self):
        super().setUp()
        # Something counted in each year, so a moved year would show.
        other = self.title("other")
        with self.on(d(2025, 3, 1)):
            self.tracking.start(self.owner, other)
        self.save(other, d(2026, 2, 2))

    def title(self, name):
        item = self.item(self.media_type, name, title=name)
        if self.length:
            self.set_item(item, series_total=self.length)
        return item

    def save(self, item, day):
        with self.on(day):
            if self.media_type == MediaTypes.GAME.value:
                game_tracking.update_progress(self.owner, item, total_minutes=90, progressed_on=day)
            else:
                series_tracking.update_progress(self.owner, item, value=5)

    def snapshot(self):
        seen = []
        for viewer in (self.owner, self.follower, self.stranger):
            for params in (YEAR_2025, YEAR_2026):
                seen.append(frozen(self.summary(params, viewer=viewer)))
                seen.append(frozen(self.summary(params, viewer=viewer, tab=self.media_type)))
            response = self.client_for(viewer).get(ACTIVITY)
            self.assertEqual(response.status_code, 200, response.content)
            seen.append(response.json())
        return seen

    def all_time(self):
        rows = self.summary(ALL_TIME, tab=self.media_type)["time"]["rows"]
        return next((row["value"] for row in rows if row["media_type"] == self.media_type), 0)

    def check(self, mark):
        for planning in (False, True):
            with self.subTest(planning=planning):
                item = self.title(f"{mark.__name__}-{planning}")
                if planning:
                    self.do(self.tracking.assign_status, item, d(2026, 1, 20), Status.PLANNING.value)
                before, value = self.snapshot(), self.all_time()
                days = self.summary(ALL_TIME, tab=self.media_type)["header"]["active_days"]
                with self.on(d(2026, 3, 1)):
                    mark(item)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.all_time(), value + self.length)
                self.assertEqual(self.summary(ALL_TIME, tab=self.media_type)["header"]["active_days"], days)
                self.assertFalse([row for row in self.calc_type().rows if row.title_id == item.pk])

    def calc_type(self):
        from api.services.activity_rows import rows_for

        return rows_for([self.owner], self.owner, types=[self.media_type])

    def eye(self, item):
        if self.media_type == MediaTypes.GAME.value:
            game_tracking.mark_completed(self.owner, item)
        else:
            series_tracking.mark_read(self.owner, item)

    def heart(self, item):
        self.tracking.set_like(self.owner, item, liked=True)

    def stars(self, item):
        self.tracking.set_rating(self.owner, item, 7)

    def status_completed(self, item):
        self.tracking.assign_status(self.owner, item, Status.COMPLETED.value)

    def test_the_eye(self):
        self.check(self.eye)

    def test_a_heart(self):
        self.check(self.heart)

    def test_stars(self):
        self.check(self.stars)

    def test_status_set_to_completed(self):
        self.check(self.status_completed)

    def test_unhearting_keeps_the_mark(self):
        item = self.title("unheart")
        with self.on(d(2026, 3, 1)):
            self.heart(item)
        before, value = self.snapshot(), self.all_time()
        with self.on(d(2026, 3, 2)):
            self.tracking.set_like(self.owner, item, liked=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.all_time(), value)


@override_settings(TIME_ZONE=NEW_YORK)
class GameMarkMatrixTests(TypeMarkMatrix, BookCalcBase):
    """Matrix 11's marks on this type."""

    media_type = MediaTypes.GAME.value
    tracking = game_tracking


@override_settings(TIME_ZONE=NEW_YORK)
class MangaMarkMatrixTests(TypeMarkMatrix, BookCalcBase):
    """Matrix 11's marks on this type."""

    media_type = MediaTypes.MANGA.value
    tracking = series_tracking
    length = 100


@override_settings(TIME_ZONE=NEW_YORK)
class ComicMarkMatrixTests(TypeMarkMatrix, BookCalcBase):
    """Matrix 11's marks on this type."""

    media_type = MediaTypes.COMIC.value
    tracking = series_tracking
    length = 12


@override_settings(TIME_ZONE=NEW_YORK)
class BookStoriesTests(BookCalcBase):
    """Stories more than one rule has to get right together."""

    def test_stopped_at_30_of_100_then_the_eye_is_100_not_130(self):
        item = self.book("thirty", pages=100)
        self.begin(item, d(2026, 2, 1))
        self.save(item, 30, d(2026, 2, 2))
        self.do(book_tracking.drop, item, d(2026, 2, 3))
        self.do(book_tracking.mark_read, item, d(2026, 2, 4))
        self.assert_numbers(0, 30, 100)

    def test_the_eye_then_a_reread_is_500_not_400(self):
        item = self.book("eye-reread")
        self.do(book_tracking.mark_read, item, d(2026, 1, 1))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 100, d(2026, 2, 2))
        self.assert_numbers(0, 100, 500)

    def test_an_imported_place_then_a_typed_number(self):
        item = self.book("place")
        Book.objects.bulk_create([Book(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=120)])
        self.save(item, 125, d(2026, 3, 1))
        self.assert_numbers(0, 5, 125)
        self.assertEqual(int(piled(self.calc().piles, BOOK).amount), 120)

    def test_stopped_at_200_back_at_250_then_finished(self):
        """Decision 8: 250 in total, a finish adds what's left, only Restart is zero."""
        item = self.book("back")
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 10))
        self.do(book_tracking.drop, item, d(2025, 11, 15))
        self.begin(item, d(2026, 4, 1))
        self.save(item, 250, d(2026, 4, 2))
        self.assert_numbers(200, 50, 250)
        self.finish(item, d(2026, 4, 3))
        self.assert_numbers(200, 200, 400)
        self.assertEqual(self.rows()[0], "2026-04-03 log +150")

    def test_edit_dates_then_a_log_goes_by_the_edited_start(self):
        """Edited to November, a log in December is after first activity: saves stay."""
        item = self.book("edited-log")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 100, d(2026, 3, 2))
        with self.on(d(2026, 3, 3)):
            book_tracking.update_journey(self.owner, item, Book.objects.get(item=item).current_session_id, start_date=d(2025, 11, 1))
        self.finish(item, d(2025, 12, 20), saved_on=d(2026, 3, 4))
        self.assertEqual(self.rows(), ["2026-03-02 read 100", "2025-12-20 log +300", "2025-11-01 started"])
        self.assert_numbers(300, 100, 400)

    def test_a_first_save_counts_whole_and_a_year_exists_for_it(self):
        """Decisions 9 and 16."""
        item = self.book("first", pages=500)
        self.save(item, 285, d(2024, 3, 10))
        data = self.summary({"schema": "2", "start_date": "2024-01-01", "end_date": "2024-12-31"}, tab=BOOK)
        self.assertEqual((data["header"]["titles"], data["header"]["logs"]), (1, 0))
        self.assertEqual(data["years"]["first"], 2024)
