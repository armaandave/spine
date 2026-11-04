"""Book imports and the numbers they leave (SW-500, SW-504, decisions 10 and 11).

An undated Read is the same mark from every source. A dated one lands in its year. A
file never closes a read under way, and your own file coming back adds nothing.
"""

import csv
from datetime import datetime
from io import BytesIO, StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import override_settings
from django.urls import reverse

from api.tests.test_activity_calc_books import BOOK, NEW_YORK, BookCalcBase, d
from api.tests.test_stats_v2_activity import YEAR_2025, YEAR_2026
from app import book_tracking
from app.models import Book, BookSession, DiaryEntry, Item, Sources, Status
from integrations import exports
from integrations.imports import goodreads, yamtrack
from integrations.imports.margins.importer import MarginsImporter
from integrations.imports.storygraph.importer import StoryGraphImporter
from integrations.tests.imports.test_goodreads import csv_payload

MEDIA_ID = "hc-1001"
NEW_MEDIA_ID = "hc-1002"
TITLE = "Atlas of Quiet Signals"
PAGES = 321
STORYGRAPH_HEADER = (
    b"Title,Authors,Contributors,ISBN/UID,Format,Read Status,Date Added,Last Date Read,Dates Read,Read Count,"
    b"Moods,Pace,Character- or Plot-Driven?,Strong Character Development?,Loveable Characters?,Diverse Characters?,"
    b"Flawed Characters?,Star Rating,Review,Content Warnings,Content Warning Description,Tags,Owned?\n"
)
MARGINS_HEADER = b"Title,Author(s),Status,Start Date,End Date,Rating,Review,Notes,Private,Format(s),Pages,Audio Length\n"


class OneBook:
    """Resolve any row to the one book."""

    def resolve_rows(self, rows):
        return {
            row.index: {
                "media_id": MEDIA_ID,
                "source": Sources.HARDCOVER.value,
                "title": TITLE,
                "image": "https://example.com/book.jpg",
                "max_progress": PAGES,
                "total_pages": PAGES,
            }
            for row in rows
        }


def goodreads_file(**overrides):
    """Return a Goodreads file with one row for the book."""
    return csv_payload(
        row_indexes=[0],
        overrides={"Title": TITLE, "Exclusive Shelf": "read", "Read Count": "1", **overrides},
    )


def storygraph_file(status="read", dates=""):
    """Return a StoryGraph file with one row for the book."""
    last = dates.split("-")[-1] if dates else ""
    return STORYGRAPH_HEADER + (
        f"{TITLE},Someone,,,Paperback,{status},2024/01/01,{last},{dates},1,,,,,,,,,,,,,No\n".encode()
    )


def margins_file(end="Unknown"):
    """Return a Margins file with one row for the book."""
    return MARGINS_HEADER + f"{TITLE},Someone,Finished,Unknown,{end},,,,No,Print,{PAGES},\n".encode()


@override_settings(TIME_ZONE=NEW_YORK)
class BookImportBase(BookCalcBase):
    """One book of 321 pages, on its Item, and each importer."""

    def setUp(self):
        super().setUp()
        self.item_row = Item.objects.create(
            media_id=MEDIA_ID,
            source=Sources.HARDCOVER.value,
            media_type=BOOK,
            title=TITLE,
            image="https://example.com/book.jpg",
            total_pages=PAGES,
        )

    def from_goodreads(self, payload, mode="new"):
        with patch("app.models.Item.fetch_releases"), patch("app.signals.update_daily_statistics.delay"):
            return goodreads.GoodReadsImporter(BytesIO(payload), self.owner, mode, resolver=OneBook()).import_data()

    def from_storygraph(self, payload, mode="new"):
        with patch("app.models.Item.fetch_releases"), patch("app.signals.update_daily_statistics.delay"):
            return StoryGraphImporter(payload, self.owner, mode, resolver=OneBook()).import_data()

    def from_margins(self, payload, mode="new"):
        with patch("app.models.Item.fetch_releases"), patch("app.signals.update_daily_statistics.delay"):
            return MarginsImporter(payload, self.owner, mode, resolver=OneBook()).import_data()

    def export(self):
        return "".join(exports.generate_rows(self.owner)).encode()

    def from_file(self, payload, mode="new"):
        with patch("app.models.Item.fetch_releases"), patch("app.signals.update_daily_statistics.delay"):
            return yamtrack.importer(BytesIO(payload), self.owner, mode)

    def yamtrack_row(self, **fields):
        """Return a file with one row for this book, as Spine's export writes it."""
        Book.objects.bulk_create([Book(user=self.owner, item=self.item_row, status=Status.PLANNING.value)])
        lines = list(exports.generate_rows(self.owner))
        Book.objects.filter(user=self.owner, item=self.item_row).delete()
        header, row = list(csv.reader(StringIO("".join(lines))))
        values = dict(zip(header, row, strict=True))
        values.update({key: str(value) for key, value in fields.items()})
        out = StringIO()
        writer = csv.DictWriter(out, fieldnames=header)
        writer.writeheader()
        writer.writerow(values)
        return out.getvalue().encode()


class ABookSpineHasNeverSeen:
    """Resolve any row to a book with no Item yet, the provider's page count on it."""

    def __init__(self, pages):
        """Hold the page count the provider lists, if any."""
        self.pages = pages

    def resolve_rows(self, rows):
        return {
            row.index: {
                "media_id": NEW_MEDIA_ID,
                "source": Sources.HARDCOVER.value,
                "title": TITLE,
                "image": "https://example.com/book.jpg",
                "max_progress": self.pages,
                "total_pages": self.pages,
            }
            for row in rows
        }


class UndatedReadTests(BookImportBase):
    """An undated Read is the mark, whichever file it came from (decision 49)."""

    def assert_the_mark(self):
        self.assert_numbers(0, 0, PAGES)
        self.assertEqual(self.rows(), [])
        book = Book.objects.get(user=self.owner, item=self.item_row)
        self.assertTrue(book.completed_manually)
        self.assertEqual(book.status, Status.COMPLETED.value)
        self.assertFalse(DiaryEntry.objects.filter(user=self.owner, item=self.item_row).exists())

    def test_goodreads(self):
        self.from_goodreads(goodreads_file(**{"Date Read": ""}))
        self.assert_the_mark()

    def test_storygraph(self):
        self.from_storygraph(storygraph_file())
        self.assert_the_mark()

    def test_margins(self):
        self.from_margins(margins_file())
        self.assert_the_mark()

    def test_yamtrack(self):
        self.from_file(self.yamtrack_row(status=Status.COMPLETED.value, progress=0))
        self.assert_the_mark()

    def new_book_number(self, importer):
        """Import an undated Read of a book Spine has never seen; return its pile."""
        with patch("app.models.Item.fetch_releases"), patch("app.signals.update_daily_statistics.delay"):
            importer()
        item = Item.objects.get(media_id=NEW_MEDIA_ID, media_type=BOOK)
        self.assertTrue(Book.objects.get(user=self.owner, item=item).completed_manually)
        return self.number(pile=True)

    def test_a_new_book_from_goodreads_holds_its_pages_at_once(self):
        resolver = ABookSpineHasNeverSeen(PAGES)
        payload = BytesIO(goodreads_file(**{"Date Read": ""}))
        self.assertEqual(self.new_book_number(goodreads.GoodReadsImporter(payload, self.owner, "new", resolver=resolver).import_data), PAGES)

    def test_a_new_book_from_storygraph_holds_its_pages_at_once(self):
        resolver = ABookSpineHasNeverSeen(PAGES)
        importer = StoryGraphImporter(storygraph_file(), self.owner, "new", resolver=resolver)
        self.assertEqual(self.new_book_number(importer.import_data), PAGES)

    def test_a_new_book_from_margins_holds_its_pages_at_once(self):
        resolver = ABookSpineHasNeverSeen(PAGES)
        importer = MarginsImporter(margins_file(), self.owner, "new", resolver=resolver)
        self.assertEqual(self.new_book_number(importer.import_data), PAGES)

    def test_a_new_book_from_margins_with_no_count_from_the_provider_reads_the_file(self):
        resolver = ABookSpineHasNeverSeen(None)
        importer = MarginsImporter(margins_file(), self.owner, "new", resolver=resolver)
        self.assertEqual(self.new_book_number(importer.import_data), PAGES)

    def test_undoing_an_imported_mark_keeps_the_row(self):
        self.from_goodreads(goodreads_file(**{"Date Read": ""}))
        with self.on(d(2026, 3, 1)):
            book_tracking.undo_read(self.owner, self.item_row)
        self.assertEqual(Book.objects.get(item=self.item_row).status, Status.PLANNING.value)
        self.assert_numbers(0, 0, 0)


class DatedReadTests(BookImportBase):
    """A dated Read lands in its year (SW-500)."""

    def assert_in_2024(self):
        self.assertEqual(self.number(start=d(2024, 1, 1), end=d(2024, 12, 31)), PAGES)
        self.assertEqual(self.number(pile=True), PAGES)

    def test_goodreads(self):
        self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/01"}))
        self.assert_in_2024()

    def test_storygraph(self):
        self.from_storygraph(storygraph_file(dates="2024/05/01"))
        self.assert_in_2024()

    def test_margins(self):
        self.from_margins(margins_file("2024-05-01"))
        self.assert_in_2024()

    def test_yamtrack_row_becomes_a_log_with_a_source_identity(self):
        self.from_file(self.yamtrack_row(status=Status.COMPLETED.value, end_date="2024-05-01 00:00:00+00:00"))
        self.assert_in_2024()
        entry = DiaryEntry.objects.get(user=self.owner, item=self.item_row)
        self.assertEqual(entry.import_source, "yamtrack")
        self.assertEqual(entry.import_source_id, f"{Sources.HARDCOVER.value}:{MEDIA_ID}:2024-05-01")
        self.assertEqual(self.rows(), ["2024-05-01 log +321"])

    def test_the_same_file_twice_adds_nothing(self):
        payload = self.yamtrack_row(status=Status.COMPLETED.value, end_date="2024-05-01 00:00:00+00:00")
        self.from_file(payload)
        self.from_file(payload, mode="new")
        self.assertEqual(DiaryEntry.objects.filter(item=self.item_row).count(), 1)
        self.from_file(payload, mode="overwrite")
        self.assertEqual(DiaryEntry.objects.filter(item=self.item_row).count(), 1)
        self.assert_in_2024()

    def test_a_raw_marked_row_with_a_date_stays_a_mark(self):
        """Eye taps from before the book contract can still hold a date."""
        self.from_file(
            self.yamtrack_row(
                status=Status.COMPLETED.value,
                end_date="2024-05-01 00:00:00+00:00",
                completed_manually=True,
            ),
        )
        self.assertFalse(DiaryEntry.objects.filter(item=self.item_row).exists())
        self.assertEqual(self.number(start=d(2024, 1, 1), end=d(2024, 12, 31)), 0)
        self.assert_numbers(0, 0, PAGES)
        payload = self.summary(YEAR_2026, tab=BOOK)
        self.assertEqual(payload["header"]["titles"], 0)

    def test_a_date_still_to_come_is_no_date(self):
        self.from_file(self.yamtrack_row(status=Status.COMPLETED.value, end_date="2999-01-01 00:00:00+00:00"))
        self.assertFalse(DiaryEntry.objects.filter(item=self.item_row).exists())
        self.assertTrue(Book.objects.get(item=self.item_row).completed_manually)

    def test_a_row_reading_stays_a_place_with_no_day(self):
        self.from_file(self.yamtrack_row(status=Status.IN_PROGRESS.value, progress=120))
        self.assert_numbers(0, 0, 120)
        self.assertEqual(self.rows(), [])


class ALiveReadTests(BookImportBase):
    """Run: a Goodreads file closed a live read and moved its pages."""

    def live_read(self):
        self.begin(self.item_row, d(2026, 3, 1))
        self.save(self.item_row, 100, d(2026, 3, 2))
        return Book.objects.get(item=self.item_row).current_session_id

    def assert_it_stayed(self, journey):
        book = Book.objects.get(user=self.owner, item=self.item_row)
        self.assertEqual(book.status, Status.IN_PROGRESS.value)
        self.assertEqual(book.current_session_id, journey)
        session = BookSession.objects.get(pk=journey)
        self.assertEqual(session.status, Status.IN_PROGRESS.value)
        self.assertEqual(session.pages_read, 100)
        self.assertEqual(session.start_date.date(), d(2026, 3, 1))
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), 100)
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)), PAGES)
        self.assertEqual(self.number(pile=True), 100 + PAGES)

    def test_a_goodreads_read_dated_before_it(self):
        journey = self.live_read()
        self.from_goodreads(goodreads_file(**{"Date Read": "2025/12/20"}))
        self.assert_it_stayed(journey)

    def test_a_storygraph_read_dated_before_it(self):
        journey = self.live_read()
        self.from_storygraph(storygraph_file(dates="2025/12/20"))
        self.assert_it_stayed(journey)

    def test_deleting_the_files_log_leaves_the_read_alone(self):
        journey = self.live_read()
        self.from_goodreads(goodreads_file(**{"Date Read": "2025/12/20"}))
        entry = DiaryEntry.objects.get(item=self.item_row)
        with self.on(d(2026, 3, 3)):
            book_tracking.delete_completion(self.owner, entry)
        self.assertEqual(Book.objects.get(item=self.item_row).current_session_id, journey)
        self.assert_numbers(0, 100, 100)

    def test_stopping_it_after_a_file_keeps_its_pages_where_they_are(self):
        self.live_read()
        self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/01"}))
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), 100)
        self.do(book_tracking.drop, self.item_row, d(2026, 3, 4))
        self.assert_numbers(0, 100, 100 + PAGES)
        self.assertEqual(self.number(start=d(2024, 1, 1), end=d(2024, 12, 31)), PAGES)
        self.assertEqual(
            self.rows(),
            ["2026-03-04 stopped", "2026-03-02 read 100", "2026-03-01 started", "2024-05-01 log +321"],
        )

    def test_it_reopens_at_its_place_after_a_file_and_a_stop(self):
        self.live_read()
        self.from_storygraph(storygraph_file(dates="2024/05/01"))
        self.do(book_tracking.drop, self.item_row, d(2026, 3, 4))
        self.begin(self.item_row, d(2026, 4, 1))
        progress = self.client_for(self.owner, NEW_YORK).get(f"/api/v1/tracking/{self.item_row.source}/{BOOK}/{MEDIA_ID}/").json()["progress"]
        self.assertEqual(progress["value"], 100)
        self.save(self.item_row, 130, d(2026, 4, 2))
        self.assert_numbers(0, 130, 130 + PAGES)
        self.assertEqual(
            self.rows(),
            [
                "2026-04-02 read 30",
                "2026-04-01 picked back up",
                "2026-03-04 stopped",
                "2026-03-02 read 100",
                "2026-03-01 started",
                "2024-05-01 log +321",
            ],
        )


class AStoppedReadTests(BookImportBase):
    """A file's read over a stopped read: neither joins the other (decision 8)."""

    def stopped_read(self):
        self.begin(self.item_row, d(2025, 11, 1))
        self.save(self.item_row, 200, d(2025, 11, 10))
        self.do(book_tracking.drop, self.item_row, d(2025, 11, 15))

    def assert_it_stayed(self):
        self.assert_numbers(200, 0, 200 + PAGES)
        self.assertEqual(self.number(start=d(2024, 1, 1), end=d(2024, 12, 31)), PAGES)

    def test_a_goodreads_read_dated_before_it(self):
        self.stopped_read()
        self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/01"}))
        self.assert_it_stayed()

    def test_a_storygraph_read_dated_before_it(self):
        self.stopped_read()
        self.from_storygraph(storygraph_file(dates="2024/05/01"))
        self.assert_it_stayed()

    def test_it_reopens_at_its_place_and_says_so(self):
        self.stopped_read()
        self.from_goodreads(goodreads_file(**{"Date Read": "2026/01/05"}))
        self.begin(self.item_row, d(2026, 4, 1))
        self.assertEqual(Book.objects.get(item=self.item_row).progress, 200)
        self.assertEqual(self.rows()[0], "2026-04-01 picked back up")
        self.save(self.item_row, 250, d(2026, 4, 2))
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)), 200)
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), 50 + PAGES)

    def test_a_log_of_his_own_after_a_stop_still_finishes_that_read(self):
        self.stopped_read()
        self.finish(self.item_row, d(2025, 12, 1))
        self.assert_numbers(PAGES, 0, PAGES)


class APlaceWithNoJourneyTests(BookImportBase):
    """A file's read over a place: the place stays where it was."""

    def test_the_place_stays_in_the_pile_and_as_the_opening(self):
        self.from_file(self.yamtrack_row(status=Status.IN_PROGRESS.value, progress=120))
        self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/01"}))
        self.assertEqual(self.number(pile=True), 120 + PAGES)
        self.begin(self.item_row, d(2026, 4, 1))
        self.assertEqual(Book.objects.get(item=self.item_row).progress, 120)
        self.assertEqual(self.number(pile=True), 120 + PAGES)


class OverwriteYourOwnExportTests(BookImportBase):
    """A Spine export in overwrite mode: All Time holds, and what years do is pinned."""

    def everything(self):
        """Seed a finished read, a reread under way, a mark, and a stopped read."""
        finished = self.book("finished", pages=300)
        Item.objects.filter(pk=finished.pk).update(image="https://example.com/a.jpg")
        self.finish(finished, d(2025, 6, 1))
        self.begin(finished, d(2026, 2, 1))
        self.save(finished, 40, d(2026, 2, 2))
        marked = self.book("marked", pages=350)
        Item.objects.filter(pk=marked.pk).update(image="https://example.com/b.jpg")
        self.do(book_tracking.mark_read, marked, d(2026, 1, 1))
        stopped = self.book("stopped", pages=500)
        Item.objects.filter(pk=stopped.pk).update(image="https://example.com/c.jpg")
        self.begin(stopped, d(2025, 11, 1))
        self.save(stopped, 200, d(2025, 11, 2))
        self.do(book_tracking.drop, stopped, d(2025, 11, 3))
        reading = self.book("reading", pages=500)
        Item.objects.filter(pk=reading.pk).update(image="https://example.com/d.jpg")
        self.begin(reading, d(2026, 3, 1))
        self.save(reading, 100, d(2026, 3, 2))

    def test_all_time_holds_and_unlogged_saves_lose_their_years(self):
        self.everything()
        self.assert_numbers(500, 140, 990)
        with patch("app.models.providers.services.get_media_metadata", side_effect=AssertionError("A provider call")):
            self.from_file(self.export(), mode="overwrite")
        # The log stays in 2025. The file holds no saves, so their places have no day.
        self.assert_numbers(300, 0, 990)
        self.assertEqual(self.rows(), ["2025-06-01 log +300"])

    def test_doing_it_again_changes_nothing(self):
        self.everything()
        for _ in range(2):
            self.from_file(self.export(), mode="overwrite")
        self.assert_numbers(300, 0, 990)
        self.assertEqual(DiaryEntry.objects.filter(user=self.owner).count(), 1)


class FilesComingBackTests(BookImportBase):
    """The same day is the same read, on a server west of UTC too (SW-504)."""

    def assert_one_log(self):
        self.assertEqual(DiaryEntry.objects.filter(item=self.item_row).count(), 1)
        self.assertEqual(self.number(start=d(2024, 1, 1), end=d(2024, 12, 31)), PAGES)
        self.assertEqual(self.number(pile=True), PAGES)

    def test_the_same_goodreads_file_twice(self):
        payload = goodreads_file(**{"Date Read": "2024/05/01"})
        self.from_goodreads(payload)
        self.from_goodreads(payload)
        self.assert_one_log()

    def test_the_same_storygraph_file_twice(self):
        payload = storygraph_file(dates="2024/05/01")
        self.from_storygraph(payload)
        self.from_storygraph(payload)
        self.assert_one_log()

    def test_a_goodreads_file_over_a_phone_log_that_day(self):
        self.finish(self.item_row, d(2024, 5, 1), saved_on=d(2026, 3, 1))
        self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/01"}))
        self.assert_one_log()

    def test_a_storygraph_file_over_a_phone_log_that_day(self):
        self.finish(self.item_row, d(2024, 5, 1), saved_on=d(2026, 3, 1))
        self.from_storygraph(storygraph_file(dates="2024/05/01"))
        self.assert_one_log()

    def log_at_a_real_moment(self):
        """Write an old log at 23:59 New York time, which is the next day in UTC."""
        moment = datetime(2024, 5, 1, 23, 59, 59, tzinfo=ZoneInfo(NEW_YORK))
        self.finish(self.item_row, d(2024, 5, 1))
        DiaryEntry.objects.filter(item=self.item_row).update(consumed_at=moment)
        Book.objects.filter(item=self.item_row).update(end_date=moment)
        BookSession.objects.filter(related_book__item=self.item_row).update(end_date=moment)
        self.assertEqual(self.number(pile=True), PAGES)

    def test_a_goodreads_file_over_an_old_log_at_a_real_moment_that_day(self):
        self.log_at_a_real_moment()
        self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/01"}))
        self.assertEqual(DiaryEntry.objects.filter(item=self.item_row).count(), 1)
        self.assertEqual(self.number(pile=True), PAGES)

    def test_your_own_file_over_an_old_log_at_a_real_moment(self):
        self.log_at_a_real_moment()
        self.from_file(self.export(), mode="overwrite")
        self.assertEqual(DiaryEntry.objects.filter(item=self.item_row).count(), 1)
        self.assertEqual(self.number(pile=True), PAGES)

    def test_the_next_day_is_another_read(self):
        self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/01"}))
        self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/02"}))
        self.assertEqual(DiaryEntry.objects.filter(item=self.item_row).count(), 2)


class YamtrackScoresTests(BookImportBase):
    """A file's score off the half stars keeps its log, and the import says so."""

    def test_it_keeps_the_log_and_rounds_the_rating(self):
        payload = self.yamtrack_row(status=Status.COMPLETED.value, end_date="2024-05-01 00:00:00+00:00", score="8.5")
        _, warnings = self.from_file(payload)
        self.assertEqual(self.number(start=d(2024, 1, 1), end=d(2024, 12, 31)), PAGES)
        self.assertEqual(DiaryEntry.objects.get(item=self.item_row).rating, 9)
        self.assertIn("8.5", warnings)
        self.assertIn(TITLE, warnings)

    def test_a_half_star_value_needs_no_rounding_or_warning(self):
        payload = self.yamtrack_row(status=Status.COMPLETED.value, end_date="2024-05-01 00:00:00+00:00", score="8")
        _, warnings = self.from_file(payload)
        self.assertEqual(DiaryEntry.objects.get(item=self.item_row).rating, 8)
        self.assertFalse(warnings)

    def test_a_score_below_the_lowest_rating_rounds_up_to_it(self):
        payload = self.yamtrack_row(status=Status.COMPLETED.value, end_date="2024-05-01 00:00:00+00:00", score="0.2")
        self.from_file(payload)
        self.assertEqual(DiaryEntry.objects.get(item=self.item_row).rating, 1)


class AMarkAndALogInYourOwnExportTests(BookImportBase):
    """Your own file coming back: All Time and every year hold (5.11, SW-500)."""

    def titled(self, media_id, pages=400):
        item = self.book(media_id, pages=pages)
        Item.objects.filter(pk=item.pk).update(image="https://example.com/a.jpg")
        item.refresh_from_db()
        return item

    def overwrite(self):
        self.from_file(self.export(), mode="overwrite")

    def test_a_log_then_to_read_then_a_heart_invents_no_mark(self):
        """The heart on a title with a log writes Completed, undated and unflagged."""
        item = self.titled("logged-heart")
        self.finish(item, d(2025, 3, 1))
        self.do(book_tracking.assign_status, item, d(2026, 1, 1), Status.PLANNING.value)
        self.do(book_tracking.set_like, item, d(2026, 1, 2), liked=True)
        self.assertTrue(Book.objects.get(item=item).read_override_active)
        before_2025 = self.summary(YEAR_2025, tab=BOOK)
        self.assert_numbers(400, 0, 400)
        self.overwrite()
        self.assert_numbers(400, 0, 400)
        self.assertFalse(Book.objects.get(item=item).completed_manually)
        self.assertEqual(self.summary(YEAR_2025, tab=BOOK), before_2025)

    def test_a_reread_stopped_then_the_eye_invents_no_mark(self):
        """A Completed row in the file holds no place, so the reread's 50 goes."""
        item = self.titled("reread-eye")
        self.finish(item, d(2025, 3, 1))
        self.begin(item, d(2026, 1, 1))
        self.save(item, 50, d(2026, 1, 2))
        self.do(book_tracking.drop, item, d(2026, 1, 3))
        self.do(book_tracking.mark_read, item, d(2026, 1, 4))
        self.assert_numbers(400, 50, 450)
        self.overwrite()
        self.assert_numbers(400, 0, 400)
        self.assertFalse(Book.objects.get(item=item).completed_manually)

    def test_a_mark_with_no_log_stays_a_mark(self):
        item = self.titled("just-marked")
        self.do(book_tracking.mark_read, item, d(2026, 1, 1))
        self.overwrite()
        self.assert_numbers(0, 0, 400)
        self.assertTrue(Book.objects.get(item=item).completed_manually)

    def test_a_mark_and_a_log_both_stay(self):
        item = self.titled("both")
        self.do(book_tracking.mark_read, item, d(2026, 1, 1))
        self.finish(item, d(2026, 2, 1))
        self.assert_numbers(0, 400, 800)
        self.overwrite()
        self.assert_numbers(0, 400, 800)

    def test_the_eye_then_a_reread_under_way_holds_its_place_too(self):
        item = self.titled("eye-reread")
        self.do(book_tracking.mark_read, item, d(2026, 1, 1))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 100, d(2026, 2, 2))
        self.assert_numbers(0, 100, 500)
        self.overwrite()
        self.assertEqual(self.number(pile=True), 500)
        self.assertEqual(self.rows(), [])

    def test_the_eye_then_a_stopped_reread_holds_its_place_too(self):
        item = self.titled("eye-stop")
        self.do(book_tracking.mark_read, item, d(2026, 1, 1))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 100, d(2026, 2, 2))
        self.do(book_tracking.drop, item, d(2026, 2, 3))
        self.overwrite()
        self.assertEqual(self.number(pile=True), 500)

    def test_a_marked_read_holds_its_length_once(self):
        """A mark from before the book contract holds its length in progress."""
        item = self.book("old-mark")
        Book.objects.create(user=self.owner, item=item, status=Status.COMPLETED.value, completed_manually=True, progress=400)
        self.assert_numbers(0, 0, 400)


class AnEyeOverALogWithNoJourneyTests(BookImportBase):
    """The eye over any log adds nothing (BK-103): your own file leaves logs with no journey."""

    def logged_with_no_journey(self):
        self.finish(self.item_row, d(2025, 3, 1))
        self.do(book_tracking.assign_status, self.item_row, d(2026, 1, 1), Status.PLANNING.value)
        self.from_file(self.export(), mode="overwrite")
        self.assertFalse(BookSession.objects.filter(related_book__item=self.item_row).exists())
        self.assert_numbers(PAGES, 0, PAGES)

    def test_the_eye_uses_the_log(self):
        self.logged_with_no_journey()
        self.do(book_tracking.mark_read, self.item_row, d(2026, 1, 2))
        self.assert_numbers(PAGES, 0, PAGES)
        book = Book.objects.get(item=self.item_row)
        self.assertTrue(book.read_override_active)
        self.assertFalse(book.completed_manually)

    def test_undoing_it_gives_to_read_back(self):
        self.logged_with_no_journey()
        self.do(book_tracking.mark_read, self.item_row, d(2026, 1, 2))
        self.do(book_tracking.undo_read, self.item_row, d(2026, 1, 3))
        self.assert_numbers(PAGES, 0, PAGES)
        self.assertEqual(Book.objects.get(item=self.item_row).status, Status.PLANNING.value)


class ALogDeletedThenYourOwnFileTests(BookImportBase):
    """What a deleted log leaves has to come back from your own file as it went."""

    def assert_it_comes_back_the_same(self, in_2025, in_2026, all_time):
        self.assert_numbers(in_2025, in_2026, all_time)
        self.from_file(self.export(), mode="overwrite")
        self.assert_numbers(in_2025, in_2026, all_time)

    def test_a_log_your_own_file_left_with_no_journey_stays_gone(self):
        self.finish(self.item_row, d(2025, 3, 1))
        self.from_file(self.export(), mode="overwrite")
        entry = DiaryEntry.objects.get(item=self.item_row)
        with self.on(d(2026, 3, 1)):
            book_tracking.delete_completion(self.owner, entry)
        self.assert_it_comes_back_the_same(0, 0, 0)
        self.assertFalse(DiaryEntry.objects.filter(item=self.item_row).exists())
        self.assertFalse(Book.objects.get(item=self.item_row).completed_manually)

    def test_an_eye_that_leaned_on_the_log_becomes_the_mark(self):
        """A log, To Read, the eye: the eye said Read, and a log's delete takes back only its own."""
        _, entry = self.finish(self.item_row, d(2025, 3, 1))
        self.do(book_tracking.assign_status, self.item_row, d(2026, 1, 1), Status.PLANNING.value)
        self.do(book_tracking.mark_read, self.item_row, d(2026, 1, 2))
        self.assert_numbers(PAGES, 0, PAGES)
        with self.on(d(2026, 1, 3)):
            book_tracking.delete_completion(self.owner, entry)
        book = Book.objects.get(item=self.item_row)
        self.assertEqual((book.status, book.completed_manually, book.read_override_active), (Status.COMPLETED.value, True, False))
        self.assertEqual(self.rows(), [])
        self.assert_it_comes_back_the_same(0, 0, PAGES)
        self.assertTrue(Book.objects.get(item=self.item_row).completed_manually)

    def test_that_mark_undoes_to_what_the_eye_covered(self):
        _, entry = self.finish(self.item_row, d(2025, 3, 1))
        self.do(book_tracking.assign_status, self.item_row, d(2026, 1, 1), Status.PLANNING.value)
        self.do(book_tracking.mark_read, self.item_row, d(2026, 1, 2))
        with self.on(d(2026, 1, 3)):
            book_tracking.delete_completion(self.owner, entry)
        self.do(book_tracking.undo_read, self.item_row, d(2026, 1, 4))
        self.assert_numbers(0, 0, 0)
        self.assertEqual(Book.objects.get(item=self.item_row).status, Status.PLANNING.value)

    def test_an_eye_with_another_log_still_behind_it_stays_as_it_is(self):
        _, first = self.finish(self.item_row, d(2025, 3, 1))
        self.finish(self.item_row, d(2025, 4, 1))
        self.do(book_tracking.assign_status, self.item_row, d(2026, 1, 1), Status.PLANNING.value)
        self.do(book_tracking.mark_read, self.item_row, d(2026, 1, 2))
        with self.on(d(2026, 1, 3)):
            book_tracking.delete_completion(self.owner, first)
        book = Book.objects.get(item=self.item_row)
        self.assertEqual((book.completed_manually, book.read_override_active), (False, True))
        self.assert_numbers(PAGES, 0, PAGES)


class AMarkOverAPlaceTests(BookImportBase):
    """A mark moves All Time only by the pile: the eye, a heart or stars never wipe a place."""

    def place_and_a_files_log(self, status=Status.IN_PROGRESS.value):
        Book.objects.create(user=self.owner, item=self.item_row, status=status, progress=120)
        self.from_goodreads(goodreads_file(**{"Date Read": "2024/05/01"}))
        book = Book.objects.get(item=self.item_row)
        self.assertEqual((book.status, book.progress), (status, 120))
        self.assertEqual(self.number(pile=True), 120 + PAGES)

    def test_the_eye_over_the_log_holds_it(self):
        self.place_and_a_files_log()
        self.do(book_tracking.mark_read, self.item_row, d(2026, 3, 1))
        self.assertEqual(self.number(pile=True), 120 + PAGES)
        self.assertEqual(self.rows(), ["2024-05-01 log +321"])

    def test_undoing_that_eye_gives_it_back(self):
        self.place_and_a_files_log()
        self.do(book_tracking.mark_read, self.item_row, d(2026, 3, 1))
        self.do(book_tracking.undo_read, self.item_row, d(2026, 3, 2))
        book = Book.objects.get(item=self.item_row)
        self.assertEqual((book.status, book.progress), (Status.IN_PROGRESS.value, 120))
        self.assertEqual(self.number(pile=True), 120 + PAGES)

    def test_a_heart_and_stars_over_the_log_hold_it(self):
        self.place_and_a_files_log(Status.DROPPED.value)
        self.do(book_tracking.set_like, self.item_row, d(2026, 3, 1), liked=True)
        self.assertEqual(self.number(pile=True), 120 + PAGES)
        self.do(book_tracking.undo_read, self.item_row, d(2026, 3, 2))
        self.do(book_tracking.set_rating, self.item_row, d(2026, 3, 3), 8)
        self.assertEqual(self.number(pile=True), 120 + PAGES)

    def test_a_status_move_off_that_eye_holds_it(self):
        self.place_and_a_files_log()
        self.do(book_tracking.mark_read, self.item_row, d(2026, 3, 1))
        self.do(book_tracking.assign_status, self.item_row, d(2026, 3, 2), Status.PLANNING.value)
        self.assertEqual(self.number(pile=True), 120 + PAGES)
        self.assertEqual(Book.objects.get(item=self.item_row).progress, 120)

    def test_your_own_file_holding_a_mark_and_a_reread_then_the_eye(self):
        self.do(book_tracking.mark_read, self.item_row, d(2026, 1, 1))
        self.begin(self.item_row, d(2026, 2, 1))
        self.save(self.item_row, 100, d(2026, 2, 2))
        self.from_file(self.export(), mode="overwrite")
        self.assertEqual(self.number(pile=True), PAGES + 100)
        self.do(book_tracking.mark_read, self.item_row, d(2026, 3, 1))
        self.assertEqual(self.number(pile=True), PAGES + 100)
        self.do(book_tracking.undo_read, self.item_row, d(2026, 3, 2))
        self.assertEqual(self.number(pile=True), PAGES + 100)
        self.assertEqual(Book.objects.get(item=self.item_row).progress, 100)

    def test_deleting_a_mark_after_to_read_gives_the_place_it_replaced_back(self):
        Book.objects.create(user=self.owner, item=self.item_row, status=Status.IN_PROGRESS.value, progress=120)
        self.do(book_tracking.mark_read, self.item_row, d(2026, 3, 1))
        self.assertEqual(self.number(pile=True), PAGES)
        self.do(book_tracking.assign_status, self.item_row, d(2026, 3, 2), Status.PLANNING.value)
        self.do(book_tracking.delete_undated_read, self.item_row, d(2026, 3, 3))
        self.assertEqual(self.number(pile=True), 120)
        self.assertEqual(Book.objects.get(item=self.item_row).progress, 120)


class WebDiaryAddTests(BookImportBase):
    """A web diary add stops clearing the undated mark."""

    def test_book_complete_keeps_the_mark(self):
        self.do(book_tracking.mark_read, self.item_row, d(2026, 1, 1))
        self.client.force_login(self.owner)
        metadata = {"title": TITLE, "image": "https://example.com/book.jpg", "max_progress": PAGES}
        with (
            patch("app.views.services.get_media_metadata", return_value=metadata),
            patch("app.signals.update_daily_statistics.delay"),
        ):
            response = self.client.post(
                reverse("add_movie_diary_entry", args=[self.item_row.source, BOOK, self.item_row.media_id]),
                {"auto_mark_consumed": "true", "book_complete": "true", "watch_date": "2026-02-01"},
            )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(DiaryEntry.objects.filter(item=self.item_row).count(), 1)
        self.assertTrue(Book.objects.get(item=self.item_row).completed_manually)
