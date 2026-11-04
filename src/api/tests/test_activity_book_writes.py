"""What Phase 1 changes in how books save: a log's date, a new read, and where one opens.

Decisions 6, 8, 10 and 51, through the phone's real requests where a request is what
decides. Each reads through the calculation as well as the rows it writes.
"""

from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.test import override_settings

from api.services.item_metadata import store_item_metadata
from api.tests.test_activity_calc_books import BOOK, NEW_YORK, BookCalcBase, d
from app import book_tracking
from app.models import Book, BookSession, DiaryEntry, Sources, Status
from social.models import ProgressChange

PERCENT = "percentage"


@override_settings(TIME_ZONE=NEW_YORK)
class LogDateTests(BookCalcBase):
    """Decision 10 on the writes: any date up to today, and the start moves with it."""

    def journey(self, item):
        return BookSession.objects.get(related_book__item=item, related_book__user=self.owner)

    def test_started_mar_1_100_on_mar_10_log_dated_dec_20(self):
        item = self.book("all-moves")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 100, d(2026, 3, 10))
        _, entry = self.finish(item, d(2025, 12, 20), saved_on=d(2026, 3, 12))
        self.assert_numbers(400, 0, 400)
        self.assertEqual(self.rows(), ["2025-12-20 log +400"])
        journey = self.journey(item)
        self.assertEqual(journey.start_date.date(), d(2025, 12, 20))
        self.assertEqual(journey.end_date.date(), d(2025, 12, 20))
        self.assertEqual(Book.objects.get(item=item).start_date.date(), d(2025, 12, 20))

    def test_deleting_it_gives_0_and_100_with_the_start_put_back(self):
        item = self.book("all-moves-back")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 100, d(2026, 3, 10))
        _, entry = self.finish(item, d(2025, 12, 20), saved_on=d(2026, 3, 12))
        with self.on(d(2026, 3, 13)):
            book_tracking.delete_completion(self.owner, entry)
        self.assert_numbers(0, 100, 100)
        self.assertEqual(self.journey(item).start_date.date(), d(2026, 3, 1))
        self.assertEqual(Book.objects.get(item=item).start_date.date(), d(2026, 3, 1))

    def test_started_dec_1_100_on_jan_10_log_dated_dec_20(self):
        item = self.book("the-rest")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 100, d(2026, 1, 10))
        self.finish(item, d(2025, 12, 20), saved_on=d(2026, 1, 12))
        self.assert_numbers(300, 100, 400)
        self.assertEqual(self.rows(), ["2026-01-10 read 100", "2025-12-20 log +300", "2025-12-01 started"])
        self.assertEqual(self.journey(item).start_date.date(), d(2025, 12, 1))

    def test_a_logs_date_edited_before_the_start_and_back(self):
        item = self.book("edited")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 100, d(2026, 3, 10))
        _, entry = self.finish(item, d(2026, 3, 12))
        self.assert_numbers(0, 400, 400)
        with self.on(d(2026, 3, 13)):
            book_tracking.update_completion(entry, {"consumed_at": d(2025, 12, 20)})
        self.assert_numbers(400, 0, 400)
        self.assertEqual(self.journey(item).start_date.date(), d(2025, 12, 20))
        with self.on(d(2026, 3, 14)):
            book_tracking.update_completion(entry, {"consumed_at": d(2026, 3, 12)})
        self.assert_numbers(0, 400, 400)
        self.assertEqual(self.journey(item).start_date.date(), d(2026, 3, 1))

    def test_edit_dates_then_a_log(self):
        """An Edit Dates change is first activity too; a log's own move isn't."""
        item = self.book("edit-dates")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 100, d(2026, 3, 10))
        with self.on(d(2026, 3, 11)):
            book_tracking.update_journey(self.owner, item, self.journey(item).pk, start_date=d(2025, 11, 1))
        change = ProgressChange.objects.get(item=item, current_progress__kind="start")
        self.assertEqual(change.saved_on, d(2025, 11, 1))
        self.finish(item, d(2025, 12, 20), saved_on=d(2026, 3, 12))
        self.assert_numbers(300, 100, 400)
        self.assertEqual(self.rows(), ["2026-03-10 read 100", "2025-12-20 log +300", "2025-11-01 started"])

    def test_a_logs_own_move_leaves_the_start_rows_day(self):
        item = self.book("own-move")
        self.begin(item, d(2026, 3, 1))
        self.finish(item, d(2025, 12, 20), saved_on=d(2026, 3, 12))
        change = ProgressChange.objects.get(item=item, current_progress__kind="start")
        self.assertEqual(change.saved_on, d(2026, 3, 1))

    def test_an_old_shape_log_keeps_its_start_when_it_goes(self):
        """No journey_start_date key: blanking the start would break a live game, and a book's date."""
        item = self.book("old-shape")
        self.begin(item, d(2026, 3, 1))
        _, entry = self.finish(item, d(2026, 3, 5))
        journey = self.journey(item)
        snapshot = dict(journey.pre_completion_progress)
        del snapshot["journey_start_date"]
        BookSession.objects.filter(pk=journey.pk).update(pre_completion_progress=snapshot)
        with self.on(d(2026, 3, 6)):
            book_tracking.delete_completion(self.owner, entry)
        self.assertEqual(self.journey(item).start_date.date(), d(2026, 3, 1))

    def test_a_future_date_stays_refused(self):
        item = self.book("future")
        self.begin(item, d(2026, 3, 1))
        with self.on(d(2026, 3, 2)), self.assertRaises(ValidationError):
            book_tracking.complete(self.owner, item, completion_date=d(2026, 3, 9))

    def test_drop_restart_and_edit_dates_keep_their_checks(self):
        item = self.book("checks")
        self.begin(item, d(2026, 3, 5))
        self.save(item, 100, d(2026, 3, 8))
        with self.on(d(2026, 3, 9)):
            with self.assertRaises(ValidationError):
                book_tracking.drop(self.owner, item, end_date=d(2026, 3, 1))
            with self.assertRaises(ValidationError):
                book_tracking.restart(self.owner, item, end_date=d(2026, 3, 1))
            with self.assertRaises(ValidationError):
                book_tracking.update_journey(self.owner, item, self.journey(item).pk, start_date=d(2026, 3, 9))

    def test_a_log_before_its_start_queues_the_days_statistics(self):
        item = self.book("celery")
        self.begin(item, d(2026, 3, 1))
        with (
            patch("app.book_tracking.update_daily_statistics.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.finish(item, d(2025, 12, 20), saved_on=d(2026, 3, 2))
        delay.assert_any_call(user_id=self.owner.pk, date_str="2025-12-20")


@override_settings(TIME_ZONE=NEW_YORK)
class LogAgainTests(BookCalcBase):
    """Decision 6: rereading counts again, through the phone's real request."""

    def book(self, media_id, pages=400):
        """Return a manual book, which a first log loads with no provider call."""
        item = self.item(BOOK, media_id, source=Sources.MANUAL.value, title=media_id)
        self.set_item(item, total_pages=pages)
        return item

    def api(self):
        return self.client_for(self.owner, NEW_YORK)

    def complete(self, item, day, *, journey_id=None, mutation_id=None):
        with self.on(day):
            response = self.api().post(
                f"/api/v1/tracking/{item.source}/book/{item.media_id}/complete/",
                {
                    "completion_date": day.isoformat(),
                    "journey_id": journey_id,
                    "mutation_id": str(mutation_id or uuid4()),
                },
                format="json",
            )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_a_log_naming_the_finished_journey_is_a_new_read(self):
        """Build 61's Log on a Read book sends the finished journey's id, and got the old log back."""
        item = self.book("log-again", pages=300)
        first = self.complete(item, d(2025, 6, 1))
        journey = BookSession.objects.get(related_book__item=item).pk
        second = self.complete(item, d(2026, 2, 1), journey_id=journey)
        self.assertNotEqual(first["diary_entry"]["id"], second["diary_entry"]["id"])
        self.assertEqual(DiaryEntry.objects.filter(item=item).count(), 2)
        self.assert_numbers(300, 300, 600)

    def test_the_same_request_again_is_a_retry_of_a_live_finish(self):
        item = self.book("retry-live", pages=300)
        self.begin(item, d(2026, 1, 1))
        journey = BookSession.objects.get(related_book__item=item).pk
        mutation = uuid4()
        first = self.complete(item, d(2026, 1, 5), journey_id=journey, mutation_id=mutation)
        second = self.complete(item, d(2026, 1, 5), journey_id=journey, mutation_id=mutation)
        self.assertEqual(first["diary_entry"]["id"], second["diary_entry"]["id"])
        self.assertEqual(DiaryEntry.objects.filter(item=item).count(), 1)

    def test_the_same_request_again_is_a_retry_of_a_direct_log(self):
        item = self.book("retry-direct", pages=300)
        mutation = uuid4()
        first = self.complete(item, d(2026, 1, 5), mutation_id=mutation)
        second = self.complete(item, d(2026, 1, 5), mutation_id=mutation)
        self.assertEqual(first["diary_entry"]["id"], second["diary_entry"]["id"])

    def test_400_in_2025_then_reread_in_2026(self):
        item = self.book("reread")
        self.complete(item, d(2025, 6, 1))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 400, d(2026, 2, 2))
        self.complete(item, d(2026, 2, 3), journey_id=Book.objects.get(item=item).current_session_id)
        self.assert_numbers(400, 400, 800)


@override_settings(TIME_ZONE=NEW_YORK)
class OpeningPlaceTests(BookCalcBase):
    """Decisions 8 and 51: only Restart begins at zero, and the sheet reads the place."""

    def api(self):
        return self.client_for(self.owner, NEW_YORK)

    def progress(self, item):
        """Return the tracking payload's progress, which both builds' sheets open on."""
        response = self.api().get(f"/api/v1/tracking/{item.source}/book/{item.media_id}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["progress"]

    def stopped(self, name, value=200, kind="pages", pages=400):
        item = self.book(name, pages=pages)
        self.begin(item, d(2025, 11, 1))
        self.save(item, value, d(2025, 11, 10), kind=kind)
        self.do(book_tracking.drop, item, d(2025, 11, 15))
        return item

    def test_a_page_place_opens_at_its_page(self):
        item = self.stopped("pages")
        self.begin(item, d(2026, 4, 1))
        self.assertEqual((self.progress(item)["kind"], self.progress(item)["value"]), ("pages", 200))

    def test_a_percent_only_place_opens_at_its_percent(self):
        item = self.stopped("percent", value=25, kind=PERCENT, pages=None)
        self.begin(item, d(2026, 4, 1))
        progress = self.progress(item)
        self.assertEqual((progress["kind"], float(progress["value"])), (PERCENT, 25.0))

    def test_the_copy_adds_no_row_beyond_the_pick_ups_own(self):
        item = self.stopped("no-row", value=25, kind=PERCENT, pages=None)
        before = ProgressChange.objects.filter(item=item).count()
        self.begin(item, d(2026, 4, 1))
        self.assertEqual(ProgressChange.objects.filter(item=item).count(), before + 2)
        self.assertEqual(self.rows(item)[0], "2026-04-01 picked back up")

    def test_a_save_of_30_records_25_to_30(self):
        item = self.stopped("records", value=25, kind=PERCENT, pages=None)
        self.begin(item, d(2026, 4, 1))
        self.save(item, 30, d(2026, 4, 2), kind=PERCENT)
        change = ProgressChange.objects.filter(item=item, current_progress__kind=PERCENT).latest("id")
        self.assertEqual((change.previous_progress["value"], change.current_progress["value"]), (25.0, 30.0))
        self.assertEqual(self.rows(item)[0], "2026-04-02 read 5%")

    def test_a_page_count_arriving_later_reads_the_whole_place(self):
        item = self.stopped("arrives", value=25, kind=PERCENT, pages=None)
        self.begin(item, d(2026, 4, 1))
        store_item_metadata(item, {"title": "arrives", "max_progress": 400})
        self.assert_numbers(100, 0, 100)

    def test_restart_still_opens_blank(self):
        item = self.book("restart", pages=None)
        self.begin(item, d(2026, 4, 1))
        self.save(item, 25, d(2026, 4, 2), kind=PERCENT)
        self.do(book_tracking.restart, item, d(2026, 4, 3))
        self.assertEqual(self.progress(item)["value"], 0)

    def test_stop_then_to_read_then_currently_reading_opens_at_200(self):
        item = self.stopped("through-to-read")
        self.do(book_tracking.assign_status, item, d(2026, 1, 1), Status.PLANNING.value)
        self.begin(item, d(2026, 2, 1))
        self.assertEqual(self.progress(item)["value"], 200)
        self.save(item, 250, d(2026, 2, 2))
        self.assert_numbers(200, 50, 250)

    def test_stop_then_paused_then_currently_reading_opens_at_200(self):
        item = self.stopped("through-paused")
        self.do(book_tracking.assign_status, item, d(2026, 1, 1), Status.PAUSED.value)
        self.begin(item, d(2026, 2, 1))
        self.assertEqual(self.progress(item)["value"], 200)
        self.save(item, 30, d(2026, 2, 2))
        self.assert_numbers(30, 0, 30)

    def test_a_reread_of_an_imported_completed_book_opens_blank(self):
        item = self.book("imported-read")
        Book.objects.bulk_create([Book(user=self.owner, item=item, status=Status.COMPLETED.value, progress=400)])
        self.begin(item, d(2026, 2, 1))
        self.assertEqual(self.progress(item)["value"], 0)

    def test_an_imported_place_with_no_journey_opens_at_its_place(self):
        item = self.book("imported-place")
        Book.objects.bulk_create([Book(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=120)])
        self.begin(item, d(2026, 2, 1))
        self.assertEqual(self.progress(item)["value"], 120)
        self.assert_numbers(0, 0, 120)
        self.save(item, 125, d(2026, 2, 2))
        self.assert_numbers(0, 5, 125)

    def test_reading_after_a_mark_opens_blank(self):
        item = self.book("after-mark")
        self.do(book_tracking.mark_read, item, d(2026, 1, 1))
        self.begin(item, d(2026, 2, 1))
        self.assertEqual(self.progress(item)["value"], 0)
