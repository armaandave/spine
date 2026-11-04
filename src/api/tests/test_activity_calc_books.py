"""The one calculation on books: the numbers each decision asks for.

Rows come from real tracking functions under a pinned clock on a New York server. Most
read the rows directly, and StatsThroughTheSwitchTests reads Stats as the phone does.
Phase 1's sheet opens at 830, 360 and 1,540.
"""

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from api.services import activity_rows
from api.services.activity_rows import list_rows, piled, rows_for
from api.services.item_metadata import store_item_metadata
from api.services.stats_scope import build_scope
from api.tests.test_stats_v2_activity import (
    ALL_TIME,
    YEAR_2025,
    YEAR_2026,
    ActivityBase,
    details,
)
from app import book_tracking, series_tracking
from app.models import (
    Book,
    BookSession,
    DiaryEntry,
    ItemCredit,
    ItemFilterFacet,
    MediaTypes,
    Sources,
    Status,
)
from social.models import ProgressChange

NEW_YORK = "America/New_York"
TOKYO = "Asia/Tokyo"
LOS_ANGELES = "America/Los_Angeles"
BOOK = MediaTypes.BOOK.value
PAUSED = ProgressChange.PAUSED
STOPPED = ProgressChange.STOPPED
BACK = ProgressChange.PICKED_BACK_UP


def d(year, month, day):
    """Return a date, shortly."""
    return date(year, month, day)


def rendered(response):
    """Return a response as the phone reads it."""
    return json.loads(JSONRenderer().render(response.data))


@override_settings(TIME_ZONE=NEW_YORK)
class BookCalcBase(ActivityBase):
    """A New York owner, and helpers that save through the real functions."""

    def setUp(self):
        super().setUp()
        self.set_zone(NEW_YORK)

    def book(self, media_id, pages=400):
        """Return a book Item of this length; None means no page count."""
        item = self.item(BOOK, media_id, title=media_id)
        self.set_item(item, total_pages=pages)
        return item

    @contextmanager
    def on(self, day, at="12:00", zone=NEW_YORK):
        """Pin the clock to noon on a day, in a zone."""
        hour, minute = (int(part) for part in at.split(":"))
        moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(zone))
        with patch("django.utils.timezone.now", return_value=moment), timezone.override(ZoneInfo(zone)):
            yield

    def begin(self, item, day, **kwargs):
        """Start reading on a day."""
        with self.on(day, **kwargs):
            return book_tracking.start(self.owner, item)

    def save(self, item, value, day, kind="pages", **kwargs):
        """Save a place on a day."""
        with self.on(day, **kwargs):
            return book_tracking.update_progress(self.owner, item, progress_type=kind, value=value)

    def finish(self, item, day, saved_on=None, **kwargs):
        """Log a finish dated `day`, written on `saved_on`."""
        with self.on(saved_on or day):
            return book_tracking.complete(self.owner, item, completion_date=day, **kwargs)

    def backdate(self, entry, day):
        """Date a log before its read began, through the same edit the phone sends."""
        book_tracking.update_completion(entry, {"consumed_at": day})
        entry.refresh_from_db()

    def do(self, function, item, day, *args, **kwargs):
        """Call a tracking function on a day."""
        with self.on(day):
            return function(self.owner, item, *args, **kwargs)

    def calc(self, **kwargs):
        """Return the owner's books, read by the owner."""
        return rows_for([self.owner], self.owner, types=[BOOK], **kwargs)

    def number(self, *, start=None, end=None, pile=False):
        """Add up the list's rows in a range, plus the pile when asked."""
        calculation = self.calc()
        rows = list_rows(calculation.rows, media_type=BOOK, start_date=start, end_date=end)
        total = sum((row.amount for row in rows), Decimal(0))
        return int(total + (piled(calculation.piles, BOOK).amount if pile else 0))

    def numbers(self):
        """Return 2025, 2026 and All Time, each the rows' sum."""
        return {
            "2025": self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)),
            "2026": self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)),
            "all_time": self.number(pile=True),
        }

    def assert_numbers(self, in_2025, in_2026, all_time):
        """Check the three numbers."""
        self.assertEqual(self.numbers(), {"2025": in_2025, "2026": in_2026, "all_time": all_time})

    def rows(self, item=None):
        """Return the list as readable lines, newest first."""
        lines = []
        for row in list_rows(self.calc().rows, media_type=BOOK):
            if item is not None and row.title_id != item.pk:
                continue
            parts = []
            if row.log_id:
                parts.append(f"log +{int(row.amount)}" if row.amount else "log")
            elif row.amount:
                parts.append(f"read {int(row.amount)}")
            elif row.told:
                parts.append(f"read {row.told.normalize():f}%")
            elif row.started:
                parts.append("started")
            lines.append(f"{row.day} " + ", ".join(parts + row.moves))
        return lines


class SavesTests(BookCalcBase):
    """A rise lands on its save's day; a log adds what's left."""

    def test_twenty_pages(self):
        item = self.book("twenty")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 20, d(2026, 3, 1))
        self.assert_numbers(0, 20, 20)
        self.assertEqual(self.rows(), ["2026-03-01 read 20"])

    def test_a_finish_shows_what_is_left(self):
        item = self.book("finish")
        self.begin(item, d(2026, 3, 1))
        for value, day in ((100, 2), (200, 3), (300, 4)):
            self.save(item, value, d(2026, 3, day))
        self.assert_numbers(0, 300, 300)
        self.finish(item, d(2026, 3, 5))
        self.assert_numbers(0, 400, 400)
        self.assertEqual(
            self.rows(),
            [
                "2026-03-05 log +100",
                "2026-03-04 read 100",
                "2026-03-03 read 100",
                "2026-03-02 read 100",
                "2026-03-01 started",
            ],
        )

    def test_years_split(self):
        item = self.book("split")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 40, d(2025, 12, 10))
        self.save(item, 100, d(2026, 1, 10))
        self.assert_numbers(40, 60, 100)

    def test_a_first_save_counts_whole_even_as_catch_up(self):
        item = self.book("catchup")
        self.save(item, 250, d(2026, 3, 1))
        self.assert_numbers(0, 250, 250)
        self.assertEqual(self.rows(), ["2026-03-01 read 250"])

    def test_lowering_comes_off_the_most_recent_reading_even_last_year(self):
        item = self.book("typo")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 300, d(2025, 12, 20))
        self.save(item, 30, d(2026, 1, 5))
        self.assert_numbers(30, 0, 30)
        self.assertEqual(self.rows(), ["2025-12-20 read 30", "2025-12-01 started"])
        self.save(item, 60, d(2026, 1, 6))
        self.assert_numbers(30, 30, 60)

    def test_a_drop_takes_january_before_december(self):
        item = self.book("across")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 100, d(2025, 12, 20))
        self.save(item, 300, d(2026, 1, 5))
        self.save(item, 30, d(2026, 2, 5))
        self.assert_numbers(30, 0, 30)

    def test_most_recent_means_saved_last_not_dated_last(self):
        item = self.book("order")
        self.begin(item, d(2026, 1, 1))
        self.save(item, 100, d(2026, 1, 5))
        self.save(item, 150, d(2026, 1, 6))
        # Saved second on an earlier date: the phone's day went back on a flight west.
        ProgressChange.objects.filter(item=item, current_progress__value=150).update(saved_on=d(2026, 1, 3))
        self.save(item, 120, d(2026, 1, 7))
        self.assertEqual(self.rows(), ["2026-01-05 read 100", "2026-01-03 read 20", "2026-01-01 started"])

    def test_typed_back_to_zero_then_a_finish(self):
        item = self.book("zero")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 150, d(2026, 3, 2))
        self.save(item, 0, d(2026, 3, 3))
        self.assert_numbers(0, 0, 0)
        self.finish(item, d(2026, 3, 4))
        self.assert_numbers(0, 400, 400)

    def test_retyping_after_a_length_arrives_adds_nothing(self):
        item = self.book("refill", pages=None)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 25, d(2026, 3, 2), kind="percentage")
        store_item_metadata(item, {"title": "refill", "max_progress": 400})
        item.refresh_from_db()
        self.save(item, 25, d(2026, 3, 3), kind="percentage")
        self.assertEqual(self.rows(), ["2026-03-02 read 100", "2026-03-01 started"])

    def test_one_row_a_day_and_the_newest_save_moves_it_to_the_top(self):
        first = self.book("first")
        second = self.book("second")
        self.begin(first, d(2026, 3, 1), at="09:00")
        self.begin(second, d(2026, 3, 1), at="09:30")
        self.save(first, 10, d(2026, 3, 2), at="10:00")
        self.save(second, 20, d(2026, 3, 2), at="11:00")
        self.save(first, 30, d(2026, 3, 2), at="12:00")
        rows = list_rows(self.calc().rows, start_date=d(2026, 3, 2), end_date=d(2026, 3, 2))
        self.assertEqual([(row.title_id, int(row.amount)) for row in rows], [(first.pk, 30), (second.pk, 20)])

    def test_a_log_is_its_own_row_beside_that_days(self):
        item = self.book("beside")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 400, d(2026, 3, 3), at="08:00")
        self.finish(item, d(2026, 3, 3))
        self.assertEqual(self.rows(), ["2026-03-03 log", "2026-03-03 read 400", "2026-03-01 started"])

    def test_rows_read_the_fields_the_list_needs(self):
        item = self.book("fields")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        _, entry = self.finish(item, d(2026, 3, 3))
        log, progress, start = list_rows(self.calc().rows)
        self.assertEqual((log.kind, log.log_id, log.title_key, log.bucket), ("log", entry.pk, f"item:{item.pk}", BOOK))
        self.assertEqual((progress.kind, progress.told, progress.told_unit, progress.known), ("progress", 40, "page", True))
        self.assertEqual(progress.any_item_id, item.pk)
        self.assertEqual(
            progress.progress_id,
            ProgressChange.objects.get(item=item, current_progress__kind="pages").pk,
        )
        self.assertTrue(start.started)
        self.assertFalse(start.counts_title)
        self.assertTrue(start.is_event)
        self.assertIsNotNone(progress.last_at)


class RereadTests(BookCalcBase):
    """Decision 6: rereading counts again, in that year and in All Time."""

    def test_after_a_log(self):
        item = self.book("logged")
        self.finish(item, d(2025, 5, 1))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 40, d(2026, 2, 2))
        self.assert_numbers(400, 40, 440)
        self.finish(item, d(2026, 3, 1))
        self.assert_numbers(400, 400, 800)

    def test_after_a_mark(self):
        item = self.book("marked")
        self.do(book_tracking.mark_read, item, d(2026, 1, 15))
        self.assert_numbers(0, 0, 400)
        self.assertEqual(self.rows(), [])
        self.begin(item, d(2026, 2, 1))
        self.save(item, 40, d(2026, 2, 2))
        self.assert_numbers(0, 40, 440)
        self.finish(item, d(2026, 3, 1))
        self.assert_numbers(0, 400, 800)

    def test_twice_in_one_year(self):
        item = self.book("twice")
        self.finish(item, d(2026, 1, 5))
        self.begin(item, d(2026, 2, 1))
        self.finish(item, d(2026, 2, 5))
        self.assert_numbers(0, 800, 800)

    def test_a_typo_on_a_reread_comes_off_the_reread(self):
        item = self.book("reread-typo")
        self.finish(item, d(2025, 5, 1))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 300, d(2026, 2, 2))
        self.save(item, 30, d(2026, 2, 3))
        self.assert_numbers(400, 30, 430)


class StopTests(BookCalcBase):
    """Decision 8: only Restart begins at zero."""

    def test_stop_and_back(self):
        item = self.book("stopped")
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 2))
        self.do(book_tracking.drop, item, d(2025, 11, 3))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 250, d(2026, 2, 2))
        self.assert_numbers(200, 50, 250)
        self.finish(item, d(2026, 2, 10))
        self.assert_numbers(200, 200, 400)
        self.assertEqual(self.rows()[0], "2026-02-10 log +150")

    def test_restart_begins_at_zero(self):
        item = self.book("restarted")
        self.begin(item, d(2026, 5, 1))
        self.save(item, 120, d(2026, 5, 2))
        self.do(book_tracking.restart, item, d(2026, 5, 10))
        self.save(item, 50, d(2026, 5, 11))
        self.assert_numbers(0, 170, 170)
        self.finish(item, d(2026, 5, 20))
        self.assert_numbers(0, 520, 520)

    def test_a_pick_up_is_no_start_and_a_restart_is(self):
        stopped = self.book("picked")
        self.begin(stopped, d(2026, 2, 1))
        self.do(book_tracking.drop, stopped, d(2026, 2, 3))
        self.begin(stopped, d(2026, 2, 5))
        self.assertEqual(self.rows(stopped), ["2026-02-05 picked back up", "2026-02-03 stopped", "2026-02-01 started"])
        restarted = self.book("started-again")
        self.begin(restarted, d(2026, 2, 1))
        self.do(book_tracking.restart, restarted, d(2026, 2, 3))
        self.assertEqual(self.rows(restarted), ["2026-02-03 started", "2026-02-01 started"])

    def test_other_ways_back(self):
        planning = self.book("via-planning")
        self.begin(planning, d(2026, 2, 1))
        self.save(planning, 200, d(2026, 2, 2))
        self.do(book_tracking.drop, planning, d(2026, 2, 3))
        self.do(book_tracking.assign_status, planning, d(2026, 2, 4), Status.PLANNING.value)
        self.begin(planning, d(2026, 2, 5))
        self.save(planning, 250, d(2026, 2, 6))
        self.assert_numbers(0, 250, 250)

    def test_a_log_after_a_stop_finishes_the_same_read(self):
        item = self.book("via-log")
        self.begin(item, d(2026, 2, 1))
        self.save(item, 200, d(2026, 2, 2))
        self.do(book_tracking.drop, item, d(2026, 2, 3))
        self.finish(item, d(2026, 2, 10))
        self.assert_numbers(0, 400, 400)

    def test_pause_and_resume_change_no_number(self):
        item = self.book("paused")
        self.begin(item, d(2026, 2, 1))
        self.save(item, 200, d(2026, 2, 2))
        self.do(book_tracking.pause, item, d(2026, 2, 3))
        self.do(book_tracking.resume, item, d(2026, 2, 4))
        self.save(item, 250, d(2026, 2, 5))
        self.assert_numbers(0, 250, 250)

    def test_deleting_the_stopped_attempt_leaves_its_place_with_no_date(self):
        """The new journey opened at 200, so it adds the rise: 200 counts on no day."""
        item = self.book("delete-first")
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 2))
        self.do(book_tracking.drop, item, d(2025, 11, 3))
        old = Book.objects.get(user=self.owner, item=item).current_session_id
        self.begin(item, d(2026, 2, 1))
        self.save(item, 250, d(2026, 2, 2))
        self.do(book_tracking.delete_journey, item, d(2026, 2, 3), old)
        self.assert_numbers(0, 50, 250)

    def test_deleting_the_new_attempt_leaves_the_old_amounts(self):
        item = self.book("delete-second")
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 2))
        self.do(book_tracking.drop, item, d(2025, 11, 3))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 250, d(2026, 2, 2))
        current = Book.objects.get(user=self.owner, item=item).current_session_id
        self.do(book_tracking.delete_journey, item, d(2026, 2, 3), current)
        self.assert_numbers(200, 0, 200)

    def test_finishing_from_paused_adds_what_is_left(self):
        item = self.book("paused-finish")
        self.begin(item, d(2026, 2, 1))
        self.save(item, 300, d(2026, 2, 2))
        self.do(book_tracking.pause, item, d(2026, 2, 3))
        self.finish(item, d(2026, 2, 4))
        self.assert_numbers(0, 400, 400)
        self.assertEqual(self.rows()[0], "2026-02-04 log +100")


class StatusMoveTests(BookCalcBase):
    """Decision 21: one row a day, and moves add nothing to any number."""

    def test_moves_show_on_their_days(self):
        item = self.book("moves")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        self.do(book_tracking.pause, item, d(2026, 3, 2))
        self.do(book_tracking.resume, item, d(2026, 3, 5))
        self.do(book_tracking.drop, item, d(2026, 3, 7))
        self.assertEqual(
            self.rows(),
            [
                "2026-03-07 stopped",
                "2026-03-05 picked back up",
                "2026-03-02 read 40, paused",
                "2026-03-01 started",
            ],
        )
        self.assert_numbers(0, 40, 40)
        active = {row.day for row in self.calc().rows if row.is_event}
        self.assertEqual(active, {d(2026, 3, 1), d(2026, 3, 2)})

    def test_a_pause_and_a_resume_on_one_day_net_to_nothing(self):
        item = self.book("net")
        self.begin(item, d(2026, 3, 1))
        self.do(book_tracking.pause, item, d(2026, 3, 2))
        self.do(book_tracking.resume, item, d(2026, 3, 2))
        self.assertEqual(self.rows(), ["2026-03-01 started"])

    def test_resume_read_pause_reads_as_the_reading(self):
        item = self.book("net-read")
        self.begin(item, d(2026, 3, 1))
        self.do(book_tracking.pause, item, d(2026, 3, 2))
        self.do(book_tracking.resume, item, d(2026, 3, 3))
        self.save(item, 40, d(2026, 3, 3))
        self.do(book_tracking.pause, item, d(2026, 3, 3))
        self.assertEqual(self.rows()[0], "2026-03-03 read 40")

    def test_restart_writes_no_stopped_row(self):
        item = self.book("restart-row")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 200, d(2026, 3, 2))
        self.do(book_tracking.restart, item, d(2026, 3, 3))
        self.assertEqual(self.rows(), ["2026-03-03 started", "2026-03-02 read 200", "2026-03-01 started"])

    def test_a_status_rows_id_never_replaces_a_saves(self):
        item = self.book("ids")
        self.begin(item, d(2026, 3, 1))
        self.do(book_tracking.pause, item, d(2026, 3, 2))
        self.do(book_tracking.resume, item, d(2026, 3, 2))
        self.save(item, 40, d(2026, 3, 2))
        saved = ProgressChange.objects.get(item=item, current_progress__kind="pages")
        row = next(row for row in self.calc().rows if row.day == d(2026, 3, 2))
        self.assertEqual(row.progress_id, saved.pk)


class PickUpRowTests(BookCalcBase):
    """A pick-up says so on its day, and is no start (AC-1, plan 5.2)."""

    def row_on(self, item, day):
        return next(row for row in self.calc().rows if row.title_id == item.pk and row.day == day and not row.log_id)

    def assert_picked_up(self, item, day):
        row = self.row_on(item, day)
        self.assertEqual((row.started, row.moves), (False, [BACK]))
        self.assertFalse(row.is_event)

    def test_stopped_then_to_read_then_back_says_so(self):
        item = self.book("through-to-read")
        self.begin(item, d(2025, 1, 1))
        self.save(item, 100, d(2025, 1, 2))
        self.do(book_tracking.drop, item, d(2025, 1, 3))
        self.do(book_tracking.assign_status, item, d(2025, 2, 1), Status.PLANNING.value)
        self.begin(item, d(2026, 1, 1))
        self.assert_picked_up(item, d(2026, 1, 1))

    def test_to_read_on_a_book_never_stopped_is_a_start(self):
        item = self.book("to-read-first")
        self.do(book_tracking.assign_status, item, d(2026, 1, 1), Status.PLANNING.value)
        self.begin(item, d(2026, 1, 2))
        row = self.row_on(item, d(2026, 1, 2))
        self.assertEqual((row.started, row.moves), (True, []))

    def test_to_read_after_a_log_is_a_reread(self):
        item = self.book("to-read-after-log")
        self.finish(item, d(2025, 6, 1))
        self.do(book_tracking.assign_status, item, d(2026, 1, 1), Status.PLANNING.value)
        self.begin(item, d(2026, 1, 2))
        row = self.row_on(item, d(2026, 1, 2))
        self.assertEqual((row.started, row.moves), (True, []))

    def test_back_to_an_imported_place_is_no_start(self):
        item = self.book("place")
        Book.objects.create(user=self.owner, item=item, status=Status.DROPPED.value, progress=120)
        self.begin(item, d(2026, 2, 1))
        self.assert_picked_up(item, d(2026, 2, 1))

    def test_back_after_its_stopped_attempt_was_deleted_is_no_start(self):
        item = self.book("deleted")
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 2))
        self.do(book_tracking.drop, item, d(2025, 11, 3))
        first = Book.objects.get(item=item).current_session_id
        self.begin(item, d(2026, 4, 1))
        self.do(book_tracking.delete_journey, item, d(2026, 4, 2), first)
        self.assert_picked_up(item, d(2026, 4, 1))

    def test_a_pause_and_a_resume_leave_the_journeys_own_start_a_start(self):
        item = self.book("paused-later")
        self.begin(item, d(2026, 1, 1))
        self.do(book_tracking.pause, item, d(2026, 1, 2))
        self.do(book_tracking.resume, item, d(2026, 1, 3))
        self.assertTrue(self.row_on(item, d(2026, 1, 1)).started)


class BackdatedLogTests(BookCalcBase):
    """Decision 10: before the first activity everything moves; else only the rest."""

    def test_before_the_start_everything_moves(self):
        item = self.book("backdated")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 100, d(2026, 3, 10))
        _, entry = self.finish(item, d(2026, 3, 20))
        self.backdate(entry, d(2025, 12, 20))
        self.assert_numbers(400, 0, 400)
        self.assertEqual(self.rows(), ["2025-12-20 log +400"])

    def test_deleting_it_brings_the_saves_back(self):
        item = self.book("backdated-undo")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 100, d(2026, 3, 10))
        _, entry = self.finish(item, d(2026, 3, 20))
        self.backdate(entry, d(2025, 12, 20))
        with self.on(d(2026, 4, 1)):
            book_tracking.delete_completion(self.owner, entry)
        self.assert_numbers(0, 100, 100)

    def test_after_the_start_the_saves_stay_on_their_days(self):
        item = self.book("midway")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 100, d(2026, 1, 10))
        _, entry = self.finish(item, d(2026, 1, 20))
        self.backdate(entry, d(2025, 12, 20))
        self.assert_numbers(300, 100, 400)
        self.assertEqual(
            self.rows(),
            ["2026-01-10 read 100", "2025-12-20 log +300", "2025-12-01 started"],
        )

    def test_an_edited_journey_start_moves_that_starts_row(self):
        item = self.book("edited-start")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 100, d(2026, 3, 2))
        journey = Book.objects.get(item=item).current_session_id
        with self.on(d(2026, 3, 3)):
            book_tracking.update_journey(self.owner, item, journey, start_date=d(2025, 11, 1))
        self.assertEqual(self.rows(), ["2026-03-02 read 100", "2025-11-01 started"])

    def test_a_stop_then_a_log_dated_last_year_moves_everything(self):
        item = self.book("stop-backdate")
        self.begin(item, d(2026, 2, 1))
        self.save(item, 200, d(2026, 2, 2))
        self.do(book_tracking.drop, item, d(2026, 2, 3))
        self.finish(item, d(2025, 6, 1), saved_on=d(2026, 3, 1))
        self.assert_numbers(400, 0, 400)


class LengthTests(BookCalcBase):
    """Percent saves, unknown lengths, and a length that arrives or changes."""

    def test_a_percent_with_a_length_reads_in_pages(self):
        item = self.book("percent")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 25, d(2026, 3, 2), kind="percentage")
        self.assert_numbers(0, 100, 100)
        self.save(item, 150, d(2026, 3, 3))
        self.assertEqual(self.rows()[0], "2026-03-03 read 50")

    def test_a_percent_only_day_keeps_its_row_through_told(self):
        item = self.book("nocount", pages=None)
        self.begin(item, d(2025, 12, 1))
        self.save(item, 25, d(2025, 12, 2), kind="percentage")
        self.save(item, 50, d(2026, 1, 2), kind="percentage")
        self.assert_numbers(0, 0, 0)
        self.assertEqual(
            self.rows(),
            ["2026-01-02 read 25%", "2025-12-02 read 25%", "2025-12-01 started"],
        )
        rows = [row for row in self.calc().rows if row.told]
        self.assertTrue(all(row.counts_title and not row.known and row.told_unit == "percent" for row in rows))

    def test_a_length_arriving_turns_old_percents_into_pages(self):
        item = self.book("arrives", pages=None)
        self.begin(item, d(2025, 12, 1))
        self.save(item, 25, d(2025, 12, 2), kind="percentage")
        self.save(item, 50, d(2026, 1, 2), kind="percentage")
        store_item_metadata(item, {"title": "arrives", "max_progress": 400})
        item.refresh_from_db()
        self.assert_numbers(100, 100, 200)
        self.save(item, 250, d(2026, 2, 2))
        self.assert_numbers(100, 150, 250)

    def test_a_typed_finish_with_no_length_is_flagged_and_grows_later(self):
        item = self.book("typed", pages=None)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 120, d(2026, 3, 2))
        self.finish(item, d(2026, 3, 3))
        self.assert_numbers(0, 120, 120)
        self.assertFalse(next(row for row in self.calc().rows if row.log_id).known)
        store_item_metadata(item, {"title": "typed", "max_progress": 400})
        self.assert_numbers(0, 400, 400)
        self.assertEqual(self.rows()[0], "2026-03-03 log +280")

    def test_a_percent_then_a_finish_with_no_length_adds_nothing_until_it_arrives(self):
        item = self.book("bare", pages=None)
        self.begin(item, d(2026, 4, 1))
        self.save(item, 50, d(2026, 4, 2), kind="percentage")
        self.finish(item, d(2026, 4, 3))
        self.assert_numbers(0, 0, 0)
        store_item_metadata(item, {"title": "bare", "max_progress": 300})
        self.assertEqual(self.rows()[:2], ["2026-04-03 log +150", "2026-04-02 read 150"])

    def test_a_finished_read_keeps_the_length_on_its_log(self):
        item = self.book("edition")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 25, d(2026, 3, 2), kind="percentage")
        self.finish(item, d(2026, 3, 3))
        self.set_item(item, total_pages=800)
        self.assert_numbers(0, 400, 400)
        self.assertEqual(self.rows()[:2], ["2026-03-03 log +300", "2026-03-02 read 100"])

    def test_a_log_with_no_journey_reads_a_percent_snapshot_by_the_length(self):
        item = self.book("snapshot")
        entry = self.log(item, datetime(2025, 3, 2, tzinfo=UTC))
        DiaryEntry.objects.filter(pk=entry.pk).update(progress_snapshot={"kind": "percentage", "value": 100})
        self.assert_numbers(400, 0, 400)

    def test_a_log_with_no_journey_is_whole_and_its_saved_length_wins(self):
        whole = self.book("unlinked", pages=300)
        self.log(whole, datetime(2025, 3, 2, tzinfo=UTC))
        self.assert_numbers(300, 0, 300)
        snapped = self.book("snapped", pages=300)
        entry = self.log(snapped, datetime(2025, 3, 3, tzinfo=UTC))
        DiaryEntry.objects.filter(pk=entry.pk).update(progress_snapshot={"kind": "pages", "value": 320, "max": 300})
        self.assert_numbers(620, 0, 620)

    def test_a_log_with_no_length_anywhere_is_unknown_and_adds_nothing(self):
        item = self.book("nothing", pages=None)
        self.log(item, datetime(2022, 3, 2, tzinfo=UTC))
        self.assert_numbers(0, 0, 0)
        row = self.calc().rows[0]
        self.assertEqual((int(row.amount), row.known, row.counts_title), (0, False, True))

    def test_two_decimal_percents_add_up(self):
        item = self.book("decimal", pages=None)
        self.begin(item, d(2026, 2, 1))
        self.save(item, "33.33", d(2026, 2, 2), kind="percentage")
        self.save(item, "66.67", d(2026, 2, 3), kind="percentage")
        self.save(item, "50.05", d(2026, 2, 4), kind="percentage")
        self.assertEqual(
            self.rows(),
            ["2026-02-03 read 16.72%", "2026-02-02 read 33.33%", "2026-02-01 started"],
        )


class DeleteTests(BookCalcBase):
    """A row follows what it was worked out from."""

    def test_deleting_a_finish_goes_back_to_the_saves(self):
        item = self.book("undo")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 200, d(2026, 3, 2))
        _, entry = self.finish(item, d(2026, 3, 3))
        with self.on(d(2026, 3, 4)):
            book_tracking.delete_completion(self.owner, entry)
        self.assert_numbers(0, 200, 200)
        self.assertEqual(Book.objects.get(item=item).status, Status.IN_PROGRESS.value)

    def test_deleting_a_log_never_turns_a_reread_into_a_typo_fix(self):
        """Stopped at 200, logged, reread to 40: that reread opened at zero when it began."""
        item = self.book("splice")
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 10))
        self.do(book_tracking.drop, item, d(2025, 11, 15))
        _, entry = self.finish(item, d(2025, 12, 1))
        self.assert_numbers(400, 0, 400)
        self.begin(item, d(2026, 2, 1))
        self.save(item, 40, d(2026, 2, 2))
        self.assert_numbers(400, 40, 440)
        with self.on(d(2026, 2, 3)):
            book_tracking.delete_completion(self.owner, entry)
        self.assert_numbers(200, 40, 240)
        self.assertEqual(
            self.rows(),
            [
                "2026-02-02 read 40",
                "2026-02-01 started",
                "2025-11-15 stopped",
                "2025-11-10 read 200",
                "2025-11-01 started",
            ],
        )

    def test_nor_does_to_read_or_paused_between_the_log_and_the_reread(self):
        for status in (Status.PLANNING.value, Status.PAUSED.value):
            with self.subTest(status=status):
                item = self.book(f"between-{status}")
                self.begin(item, d(2025, 11, 1))
                self.save(item, 200, d(2025, 11, 10))
                self.do(book_tracking.drop, item, d(2025, 11, 15))
                _, entry = self.finish(item, d(2025, 12, 1))
                self.do(book_tracking.assign_status, item, d(2026, 1, 1), status)
                self.begin(item, d(2026, 2, 1))
                self.save(item, 40, d(2026, 2, 2))
                with self.on(d(2026, 2, 3)):
                    book_tracking.delete_completion(self.owner, entry)
                self.assertEqual(
                    self.rows(item),
                    [
                        "2026-02-02 read 40",
                        "2026-02-01 started",
                        "2025-11-15 stopped",
                        "2025-11-10 read 200",
                        "2025-11-01 started",
                    ],
                )

    def test_a_reread_over_a_mark_or_a_log_says_it_began_over(self):
        for name, mark in (("over-log", lambda item: self.finish(item, d(2025, 12, 1))), ("over-eye", lambda item: self.do(book_tracking.mark_read, item, d(2025, 12, 1)))):
            with self.subTest(name=name):
                item = self.book(name)
                mark(item)
                self.begin(item, d(2026, 2, 1))
                start = ProgressChange.objects.get(item=item, current_progress__kind=ProgressChange.START)
                self.assertIs(start.current_progress.get("began_over"), True)

    def test_a_first_start_and_a_pick_up_do_not_say_it(self):
        item = self.book("not-over")
        self.begin(item, d(2026, 1, 1))
        self.save(item, 10, d(2026, 1, 2))
        self.do(book_tracking.drop, item, d(2026, 1, 3))
        self.begin(item, d(2026, 1, 4))
        starts = ProgressChange.objects.filter(item=item, current_progress__kind=ProgressChange.START)
        self.assertEqual([change.current_progress.get("began_over") for change in starts], [None, None])

    def test_deleting_a_direct_log_leaves_nothing(self):
        item = self.book("direct")
        _, entry = self.finish(item, d(2026, 3, 3))
        with self.on(d(2026, 3, 4)):
            book_tracking.delete_completion(self.owner, entry)
        self.assert_numbers(0, 0, 0)
        self.assertEqual(self.rows(), [])

    def test_deleting_an_older_finish_leaves_the_reread(self):
        item = self.book("older")
        self.begin(item, d(2026, 1, 1))
        self.save(item, 300, d(2026, 1, 2))
        _, entry = self.finish(item, d(2026, 1, 3))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 40, d(2026, 2, 2))
        self.assert_numbers(0, 440, 440)
        with self.on(d(2026, 2, 3)):
            book_tracking.delete_completion(self.owner, entry)
        self.assert_numbers(0, 40, 40)

    def test_rows_whose_journey_is_gone_are_ignored(self):
        item = self.book("wiped")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 200, d(2026, 3, 2))
        Book.objects.filter(user=self.owner, item=item).delete()
        self.assertEqual(ProgressChange.objects.filter(item=item).count(), 2)
        self.assert_numbers(0, 0, 0)
        self.assertEqual(self.rows(), [])

    def test_a_row_with_no_journey_id_is_ignored(self):
        item = self.book("raw")
        shape = {"kind": "pages", "value": 40, "max": 400, "unit": "page"}
        ProgressChange.objects.create(actor=self.owner, item=item, previous_progress=shape, current_progress=shape)
        self.assert_numbers(0, 0, 0)


class PileTests(BookCalcBase):
    """Marks and places with no date: All Time only, never a row (decision 11)."""

    def test_the_eye_a_heart_and_stars_hold_no_row(self):
        read = self.book("eye")
        heart = self.book("heart", pages=300)
        stars = self.book("stars", pages=200)
        unknown = self.book("unknown", pages=None)
        with self.on(d(2026, 3, 1)):
            book_tracking.mark_read(self.owner, read)
            book_tracking.set_like(self.owner, heart, liked=True)
            book_tracking.set_rating(self.owner, stars, 7)
            book_tracking.mark_read(self.owner, unknown)
        self.assert_numbers(0, 0, 900)
        self.assertEqual(self.rows(), [])
        pile = piled(self.calc().piles, BOOK)
        self.assertEqual(len(pile.titles), 4)
        self.assertEqual(len(pile.known), 3)
        with self.on(d(2026, 3, 2)):
            book_tracking.set_like(self.owner, heart, liked=False)
        self.assert_numbers(0, 0, 900)
        with self.on(d(2026, 3, 2)):
            book_tracking.delete_undated_read(self.owner, read)
        self.assert_numbers(0, 0, 500)

    def test_stopped_then_the_eye_holds_only_what_is_left(self):
        item = self.book("stop-mark")
        self.begin(item, d(2026, 2, 1))
        self.save(item, 200, d(2026, 2, 2))
        self.do(book_tracking.drop, item, d(2026, 2, 3))
        self.do(book_tracking.mark_read, item, d(2026, 2, 4))
        self.assert_numbers(0, 200, 400)

    def stopped_at_200(self, name):
        item = self.book(name)
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 10))
        self.do(book_tracking.drop, item, d(2025, 11, 15))
        return item

    def test_stopped_then_to_read_then_the_eye_holds_only_what_is_left(self):
        item = self.stopped_at_200("through-to-read")
        self.do(book_tracking.assign_status, item, d(2026, 1, 2), Status.PLANNING.value)
        self.do(book_tracking.mark_read, item, d(2026, 1, 3))
        self.assertIsNone(Book.objects.get(item=item).undated_read_previous_session_id)
        self.assert_numbers(200, 0, 400)

    def test_stopped_then_a_direct_paused_then_the_eye_holds_only_what_is_left(self):
        item = self.stopped_at_200("through-paused")
        self.do(book_tracking.assign_status, item, d(2026, 1, 2), Status.PAUSED.value)
        self.do(book_tracking.mark_read, item, d(2026, 1, 3))
        self.assertIsNone(Book.objects.get(item=item).undated_read_previous_session_id)
        self.assert_numbers(200, 0, 400)

    def test_stopped_then_the_eye_then_a_reread_counts_the_reread_too(self):
        item = self.stopped_at_200("then-a-reread")
        self.do(book_tracking.assign_status, item, d(2026, 1, 2), Status.PLANNING.value)
        self.do(book_tracking.mark_read, item, d(2026, 1, 3))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 50, d(2026, 2, 2))
        self.assert_numbers(200, 50, 450)

    def test_a_read_begun_after_the_eye_holds_on_top_of_it(self):
        """The mark doesn't stand for its pages, even stopped and picked back up."""
        item = self.book("reread")
        self.do(book_tracking.mark_read, item, d(2026, 1, 1))
        self.begin(item, d(2026, 2, 1))
        self.save(item, 200, d(2026, 2, 2))
        self.do(book_tracking.drop, item, d(2026, 2, 3))
        self.assert_numbers(0, 200, 600)
        self.begin(item, d(2026, 2, 4))
        self.save(item, 250, d(2026, 2, 5))
        self.do(book_tracking.drop, item, d(2026, 2, 6))
        self.assert_numbers(0, 250, 650)

    def test_a_read_after_the_eye_through_to_read_holds_on_top_of_it(self):
        item = self.book("marked-first")
        self.do(book_tracking.mark_read, item, d(2026, 1, 1))
        self.do(book_tracking.assign_status, item, d(2026, 1, 2), Status.PLANNING.value)
        self.begin(item, d(2026, 2, 1))
        self.save(item, 200, d(2026, 2, 2))
        self.do(book_tracking.drop, item, d(2026, 2, 3))
        self.assert_numbers(0, 200, 600)

    def test_a_place_with_no_journey_sits_in_the_pile_and_stays_as_the_opening(self):
        item = self.book("yamtrack")
        Book.objects.bulk_create([Book(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=120)])
        self.assert_numbers(0, 0, 120)
        self.assertEqual(self.rows(), [])
        self.save(item, 125, d(2026, 3, 1))
        self.assert_numbers(0, 5, 125)
        self.save(item, 150, d(2026, 3, 2))
        self.assert_numbers(0, 30, 150)
        self.assertEqual(piled(self.calc().piles, BOOK).amount, 120)

    def test_a_journey_holding_a_place_it_never_saved_holds_it_in_the_pile(self):
        item = self.book("carried")
        self.begin(item, d(2026, 3, 1))
        BookSession.objects.filter(related_book__item=item).update(pages_read=120)
        self.assert_numbers(0, 0, 120)

    def imported_place(self, name, place=120):
        """Return a book at a place with no journey, as a file leaves one."""
        item = self.book(name)
        Book.objects.create(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=place)
        return item

    def test_a_place_then_a_stop_then_the_eye_holds_that_place_once(self):
        item = self.imported_place("place-stop-eye")
        self.begin(item, d(2026, 3, 1))
        self.do(book_tracking.drop, item, d(2026, 3, 2))
        self.assert_numbers(0, 0, 120)
        self.do(book_tracking.mark_read, item, d(2026, 3, 3))
        self.assert_numbers(0, 0, 400)

    def test_a_place_a_save_a_stop_then_the_eye_holds_that_place_once(self):
        item = self.imported_place("place-save-eye")
        self.save(item, 125, d(2026, 3, 1))
        self.do(book_tracking.drop, item, d(2026, 3, 2))
        self.assert_numbers(0, 5, 125)
        self.do(book_tracking.mark_read, item, d(2026, 3, 3))
        self.assert_numbers(0, 5, 400)

    def test_a_deleted_stopped_attempt_then_a_stop_then_the_eye_is_the_length(self):
        item = self.stopped_at_200("deleted-first")
        first = Book.objects.get(item=item).current_session_id
        self.begin(item, d(2026, 2, 1))
        self.save(item, 250, d(2026, 2, 2))
        self.do(book_tracking.delete_journey, item, d(2026, 2, 3), first)
        self.do(book_tracking.drop, item, d(2026, 2, 4))
        self.assert_numbers(0, 50, 250)
        self.do(book_tracking.mark_read, item, d(2026, 2, 5))
        self.assert_numbers(0, 50, 400)

    def test_stopped_with_no_saved_rows_then_the_eye_is_the_length(self):
        item = self.book("no-rows", pages=100)
        self.begin(item, d(2026, 3, 1))
        BookSession.objects.filter(related_book__item=item).update(pages_read=30)
        self.do(book_tracking.drop, item, d(2026, 3, 2))
        self.assert_numbers(0, 0, 30)
        self.do(book_tracking.mark_read, item, d(2026, 3, 3))
        self.assert_numbers(0, 0, 100)

    def test_imports_land_where_their_dates_say(self):
        dated = self.book("imported-log")
        stopped = self.book("imported-dnf")
        marked = self.book("imported-undated")
        with self.on(d(2026, 3, 1)):
            book_tracking.complete(
                self.owner,
                dated,
                completion_date=d(2022, 6, 1),
                import_source="margins",
                import_source_id="x",
                emit_activity=False,
            )
            book_tracking.import_journey(
                self.owner,
                stopped,
                status=Status.DROPPED.value,
                start_date=d(2024, 1, 1),
                end_date=d(2026, 2, 1),
            )
            book_tracking.mark_read(self.owner, marked)
        self.assertEqual(self.number(start=d(2022, 1, 1), end=d(2022, 12, 31)), 400)
        self.assertEqual(self.number(pile=True), 800)
        self.assertEqual(self.rows(), ["2022-06-01 log +400"])

    def test_a_legacy_journey_with_a_log_that_is_also_marked_counts_both(self):
        item = self.book("legacy")
        Book.objects.bulk_create(
            [Book(user=self.owner, item=item, status=Status.COMPLETED.value, progress=400, completed_manually=True)],
        )
        book = Book.objects.get(item=item)
        entry = self.log(item, datetime(2024, 3, 2, tzinfo=UTC))
        BookSession.objects.bulk_create(
            [
                BookSession(
                    related_book=book,
                    status=Status.COMPLETED.value,
                    origin=BookSession.Origin.LEGACY,
                    pages_read=400,
                    percentage_read=100,
                    end_date=datetime(2024, 3, 2, tzinfo=UTC),
                    completion_diary_entry=entry,
                ),
            ],
        )
        self.assertEqual(self.number(start=d(2024, 1, 1), end=d(2024, 12, 31)), 400)
        self.assertEqual(self.number(pile=True), 800)

    def test_piles_can_be_left_out(self):
        item = self.book("left-out")
        self.do(book_tracking.mark_read, item, d(2026, 2, 4))
        self.assertEqual(self.calc(piles=False).piles, [])


class LegacyJourneyTests(BookCalcBase):
    """Rows migration 0075 left: a Completed journey with no log, which is no reading."""

    def legacy(self, item, *, marked=True, status=Status.COMPLETED.value, pages=400):
        Book.objects.bulk_create(
            [
                Book(
                    user=self.owner,
                    item=item,
                    status=status,
                    completed_manually=marked,
                    progress=pages if status == Status.COMPLETED.value else 0,
                ),
            ],
        )
        BookSession.objects.bulk_create(
            [
                BookSession(
                    related_book=Book.objects.get(user=self.owner, item=item),
                    status=Status.COMPLETED.value,
                    origin=BookSession.Origin.LEGACY,
                    pages_read=pages,
                    percentage_read=100,
                    end_date=datetime(2024, 3, 2, tzinfo=UTC),
                ),
            ],
        )

    def test_a_marked_book_counts_once(self):
        self.legacy(self.book("marked"))
        self.assert_numbers(0, 0, 400)

    def test_a_logged_book_with_an_extra_journey_counts_once(self):
        item = self.book("logged")
        self.legacy(item, marked=False)
        BookSession.objects.create(
            related_book=Book.objects.get(item=item),
            status=Status.COMPLETED.value,
            origin=BookSession.Origin.LEGACY,
            pages_read=400,
            percentage_read=100,
            end_date=datetime(2024, 3, 2, tzinfo=UTC),
            completion_diary_entry=self.log(item, datetime(2024, 3, 2, tzinfo=UTC)),
        )
        self.assertEqual(self.number(pile=True), 400)

    def test_on_a_to_read_book_it_counts_nothing(self):
        self.legacy(self.book("to-read"), marked=False, status=Status.PLANNING.value)
        self.assert_numbers(0, 0, 0)

    def test_undoing_the_mark_leaves_nothing(self):
        item = self.book("undone")
        self.legacy(item)
        self.do(book_tracking.undo_read, item, d(2026, 3, 1))
        self.assert_numbers(0, 0, 0)


class ImportedPlaceTests(BookCalcBase):
    """A place with no journey sits in the pile, and stays as the opening (5.2)."""

    def place(self, name="place", status=Status.IN_PROGRESS.value):
        """Return a book at page 120 of 400 with no journey, as a file leaves one."""
        item = self.book(name)
        Book.objects.create(user=self.owner, item=item, status=status, progress=120)
        return item

    def test_finishing_with_no_save_between_adds_only_what_is_left(self):
        item = self.place()
        self.begin(item, d(2026, 3, 1))
        self.assert_numbers(0, 0, 120)
        self.finish(item, d(2026, 3, 2))
        self.assert_numbers(0, 280, 400)

    def test_a_direct_log_adds_only_what_is_left(self):
        item = self.place()
        self.finish(item, d(2026, 3, 2))
        self.assert_numbers(0, 280, 400)
        self.assertEqual(self.rows(), ["2026-03-02 log +280"])

    def test_deleting_that_log_gives_the_place_back(self):
        item = self.place()
        _, entry = self.finish(item, d(2026, 3, 2))
        with self.on(d(2026, 3, 3)):
            book_tracking.delete_completion(self.owner, entry)
        self.assert_numbers(0, 0, 120)
        book = Book.objects.get(item=item)
        self.assertEqual((book.status, book.progress), (Status.IN_PROGRESS.value, 120))

    def test_the_same_finish_on_a_series_reads_the_same(self):
        """5.2's same-story test: a series' attempt holds its place the same way."""
        manga = self.item(MediaTypes.MANGA.value, "same-story", title="same-story")
        self.set_item(manga, series_total=400)
        with self.on(d(2026, 3, 1)):
            series_tracking.import_title(self.owner, manga, status=Status.IN_PROGRESS.value, place=120)
        with self.on(d(2026, 3, 2)):
            series_tracking.complete(self.owner, manga, completion_date=d(2026, 3, 2))
        calculation = rows_for([self.owner], self.owner, types=[MediaTypes.MANGA.value])
        rows = list_rows(calculation.rows, media_type=MediaTypes.MANGA.value)
        self.assertEqual([int(row.amount) for row in rows], [280])
        self.assertEqual(int(piled(calculation.piles, MediaTypes.MANGA.value).amount), 120)

    def test_a_status_move_holds_it(self):
        for status in (Status.PAUSED.value, Status.DROPPED.value, Status.PLANNING.value):
            with self.subTest(status=status):
                Book.objects.filter(user=self.owner).delete()
                item = self.place(f"move-{status}")
                self.do(book_tracking.assign_status, item, d(2026, 3, 1), status)
                self.assert_numbers(0, 0, 120)
                book = Book.objects.get(item=item)
                self.assertEqual((book.status, book.progress), (status, 120))
                self.assertEqual(self.rows(), [])

    def test_back_from_a_status_move_opens_at_it(self):
        for status in (Status.PAUSED.value, Status.DROPPED.value, Status.PLANNING.value):
            with self.subTest(status=status):
                Book.objects.filter(user=self.owner).delete()
                item = self.place(f"back-{status}")
                self.do(book_tracking.assign_status, item, d(2026, 3, 1), status)
                self.begin(item, d(2026, 3, 2))
                self.assertEqual(Book.objects.get(item=item).progress, 120)
                self.save(item, 130, d(2026, 3, 3))
                self.assert_numbers(0, 10, 130)

    def test_the_phones_plain_status_write_holds_it(self):
        item = self.item(BOOK, "phone-place", source=Sources.MANUAL.value, title="phone-place")
        self.set_item(item, total_pages=400)
        Book.objects.create(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=120)
        client = self.client_for(self.owner, NEW_YORK)
        with self.on(d(2026, 3, 1)):
            response = client.patch(f"/api/v1/tracking/manual/book/{item.media_id}/", {"status": Status.DROPPED.value}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assert_numbers(0, 0, 120)

    def test_the_eye_then_undo_gives_it_back(self):
        item = self.place()
        self.do(book_tracking.mark_read, item, d(2026, 3, 1))
        self.assert_numbers(0, 0, 400)
        self.do(book_tracking.undo_read, item, d(2026, 3, 2))
        self.assert_numbers(0, 0, 120)
        book = Book.objects.get(item=item)
        self.assertEqual((book.status, book.progress), (Status.IN_PROGRESS.value, 120))

    def test_a_status_move_on_a_marked_book_never_counts_its_length_twice(self):
        """A mark from before the book contract holds its length in progress."""
        item = self.book("old-mark")
        Book.objects.create(user=self.owner, item=item, status=Status.COMPLETED.value, completed_manually=True, progress=400)
        self.do(book_tracking.assign_status, item, d(2026, 3, 1), Status.PLANNING.value)
        self.assertEqual(Book.objects.get(item=item).progress, 0)
        self.assert_numbers(0, 0, 400)

    def test_a_journey_from_before_saves_were_recorded_still_counts_whole_on_its_log(self):
        """Nothing says where it opened, so its log counts whole, as it always has."""
        item = self.book("before-rows")
        self.begin(item, d(2026, 3, 1))
        ProgressChange.objects.filter(item=item).delete()
        BookSession.objects.filter(related_book__item=item).update(pages_read=100)
        self.finish(item, d(2026, 3, 2))
        self.assert_numbers(0, 400, 400)


class DayTests(BookCalcBase):
    """Decision 3: a save's day is fixed as the phone's date when saved."""

    def test_saved_on_dec_31_stays_there_when_read_abroad(self):
        item = self.book("eve")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 40, d(2025, 12, 31), at="23:30")
        self.set_zone("Europe/London")
        self.owner.refresh_from_db()
        self.assert_numbers(40, 0, 40)

    def test_a_row_with_no_stored_day_reads_its_moment_on_the_owners_calendar(self):
        item = self.book("old-row")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2), at="20:30")
        ProgressChange.objects.filter(item=item).update(saved_on=None)
        self.assertEqual(self.rows(), ["2026-03-02 read 40", "2026-03-01 started"])
        self.set_zone(TOKYO)
        self.owner.refresh_from_db()
        self.assertEqual(self.rows(), ["2026-03-03 read 40", "2026-03-02 started"])

    def test_a_date_only_log_ignores_microseconds_and_a_moment_reads_on_the_calendar(self):
        carrier = self.log(self.book("carrier"), datetime(2025, 12, 31, 0, 0, 0, 500, tzinfo=UTC))
        moment = self.log(self.book("moment"), datetime(2026, 1, 1, 3, 0, tzinfo=UTC))
        days = {row.log_id: row.day for row in self.calc().rows}
        self.assertEqual(days[carrier.pk], d(2025, 12, 31))
        self.assertEqual(days[moment.pk], d(2025, 12, 31))

    def test_many_people_cost_one_read_and_keep_their_own_calendars(self):
        tokyo = self.follower
        tokyo.calendar_timezone = TOKYO
        tokyo.save(update_fields=["calendar_timezone"])
        item = self.book("shared")
        with self.on(d(2026, 3, 1), at="23:30"):
            book_tracking.start(self.owner, item)
            book_tracking.start(tokyo, item)
        ProgressChange.objects.update(saved_on=None)
        with CaptureQueriesContext(connection) as one:
            rows_for([self.owner], self.owner, types=[BOOK])
        with CaptureQueriesContext(connection) as two:
            rows = rows_for([self.owner, tokyo], self.owner, types=[BOOK]).rows
        self.assertEqual(len(one), len(two))
        self.assertEqual({row.actor_id: row.day for row in rows}, {self.owner.pk: d(2026, 3, 1), tokyo.pk: d(2026, 3, 2)})

    def test_titles_narrows_the_walk(self):
        item = self.book("one")
        other = self.book("other")
        self.begin(item, d(2026, 3, 1))
        self.begin(other, d(2026, 3, 1))
        rows = rows_for([self.owner], self.owner, titles=[item.pk], types=[BOOK]).rows
        self.assertEqual({row.title_id for row in rows}, {item.pk})


class SheetBase(BookCalcBase):
    """Phase 1's sheet: 830, 360 and 1,540."""

    def seed(self):
        """Write the sheet's books through the real functions."""
        a = self.book("A")
        self.begin(a, d(2025, 12, 1))
        self.save(a, 100, d(2025, 12, 20))
        self.save(a, 200, d(2026, 3, 1))
        b = self.book("B")
        self.begin(b, d(2025, 12, 20))
        self.save(b, 300, d(2025, 12, 28))
        self.save(b, 30, d(2026, 1, 2))
        c = self.book("C", pages=300)
        self.finish(c, d(2025, 6, 1))
        self.begin(c, d(2026, 2, 1))
        self.save(c, 40, d(2026, 2, 10))
        dd = self.book("D")
        self.begin(dd, d(2025, 11, 1))
        self.save(dd, 200, d(2025, 11, 10))
        self.do(book_tracking.drop, dd, d(2025, 11, 15))
        self.begin(dd, d(2026, 4, 1))
        self.save(dd, 250, d(2026, 4, 2))
        e = self.book("E", pages=350)
        self.do(book_tracking.mark_read, e, d(2026, 1, 15))
        f = self.book("F")
        self.begin(f, d(2026, 5, 1))
        self.save(f, 120, d(2026, 5, 2))
        self.do(book_tracking.restart, f, d(2026, 5, 10))
        self.save(f, 50, d(2026, 5, 11))
        g = self.book("G", pages=None)
        self.begin(g, d(2026, 6, 1))
        self.save(g, 25, d(2026, 6, 2), kind="percentage")
        h = self.book("H", pages=200)
        self.finish(h, d(2025, 3, 1))
        self.book("J", pages=300)
        self.book("V", pages=300)
        self.sheet = {"A": a, "B": b, "C": c, "D": dd, "E": e, "F": f, "G": g, "H": h}


class SheetTests(SheetBase):
    """The rows themselves."""

    def setUp(self):
        super().setUp()
        self.seed()

    def test_the_numbers(self):
        self.assert_numbers(830, 360, 1540)

    def test_all_time_is_every_year_plus_the_pile(self):
        self.assertEqual(self.number(), 830 + 360)
        self.assertEqual(piled(self.calc().piles, BOOK).amount, 350)

    def test_tapping_the_count_shows_one_row_per_title_on_its_newest_day(self):
        calculation = self.calc()
        titles = list_rows(
            calculation.rows,
            media_type=BOOK,
            start_date=d(2026, 1, 1),
            end_date=d(2026, 12, 31),
            counts=activity_rows.TITLES,
        )
        names = {item.pk: name for name, item in self.sheet.items()}
        self.assertEqual(
            [(names[row.title_id], str(row.day)) for row in titles],
            [
                ("G", "2026-06-02"),
                ("F", "2026-05-11"),
                ("D", "2026-04-02"),
                ("A", "2026-03-01"),
                ("C", "2026-02-10"),
            ],
        )

    def test_tapping_the_measure_shows_every_days_row_and_they_add_up(self):
        rows = list_rows(self.calc().rows, media_type=BOOK, start_date=d(2026, 1, 1), end_date=d(2026, 12, 31))
        amounts = sorted(int(row.amount) for row in rows if row.amount)
        self.assertEqual(amounts, [40, 50, 50, 100, 120])
        self.assertEqual(sum(amounts), 360)

    def test_a_days_list_leaves_out_status_only_rows(self):
        rows = list_rows(self.calc().rows, start_date=d(2025, 11, 15), end_date=d(2025, 11, 15))
        self.assertEqual([row.moves for row in rows], [[STOPPED]])
        days = list_rows(self.calc().rows, start_date=d(2025, 11, 15), end_date=d(2025, 11, 15), counts=activity_rows.DAYS)
        self.assertEqual(days, [])

    def test_weeks_list_the_rows_days_do(self):
        rows = self.calc().rows
        self.assertEqual(
            list_rows(rows, counts=activity_rows.WEEKS),
            list_rows(rows, counts=activity_rows.DAYS),
        )

    def test_books_have_no_hours_or_episodes_to_list(self):
        rows = self.calc().rows
        self.assertEqual(list_rows(rows, counts=activity_rows.HOURS), [])
        self.assertEqual(list_rows(rows, counts=activity_rows.EPISODES), [])

    def test_the_multi_title_days_list(self):
        self.save(self.sheet["A"], 210, d(2026, 5, 11))
        rows = list_rows(self.calc().rows, counts=activity_rows.MULTI_TITLE_DAYS)
        self.assertEqual({str(row.day) for row in rows}, {"2025-12-20", "2026-05-11"})
        self.assertEqual(len(rows), 4)


class StatsThroughTheSwitchTests(SheetBase):
    """Stats with books switched on: read as the phone reads them."""

    def phone(self, params, *, viewer=None, tab=BOOK):
        """Return the Stats payload as JSON."""
        cache.clear()
        viewer = viewer or self.owner
        client = APIClient()
        client.force_authenticate(viewer)
        path = "/api/v1/stats/me/summary/" if viewer == self.owner else self.user_path()
        response = client.get(path, {**params, **({"media_type": tab} if tab else {})})
        self.assertEqual(response.status_code, 200, response.data)
        return rendered(response)

    def time(self, data):
        """Return the book time row."""
        return next(row for row in data["time"]["rows"] if row["media_type"] == BOOK)

    def test_the_opening_numbers_and_counts(self):
        self.seed()
        for params, value, books, logs in (
            (YEAR_2025, 830, 5, 2),
            (YEAR_2026, 360, 5, 0),
            (ALL_TIME, 1540, 8, 2),
        ):
            data = self.phone(params)
            row = self.time(data)
            self.assertEqual(row["value"], value)
            self.assertIs(type(row["value"]), int, "A Decimal renders as 360.0, which the app reads as 0")
            self.assertEqual(data["header"]["titles"], books)
            self.assertEqual(data["header"]["logs"], logs)

    def test_known_and_total_count_titles_with_a_length(self):
        self.seed()
        row = self.time(self.phone(YEAR_2026))
        self.assertEqual((row["known"], row["total"]), (4, 5))
        row = self.time(self.phone(YEAR_2025))
        self.assertEqual((row["known"], row["total"]), (5, 5))
        row = self.time(self.phone(ALL_TIME))
        self.assertEqual((row["known"], row["total"]), (7, 8))

    def test_each_later_step_on_the_sheet(self):
        self.seed()
        self.save(self.sheet["A"], 250, d(2026, 7, 1))
        self.assertEqual(self.time(self.phone(YEAR_2026))["value"], 410)
        self.finish(self.sheet["A"], d(2026, 7, 2))
        self.assertEqual(self.time(self.phone(YEAR_2026))["value"], 560)
        self.assertEqual(self.time(self.phone(ALL_TIME))["value"], 1740)
        self.save(self.sheet["B"], 60, d(2026, 7, 3))
        self.assertEqual(self.time(self.phone(YEAR_2026))["value"], 590)
        self.save(self.sheet["D"], 300, d(2026, 7, 4))
        self.assertEqual(self.time(self.phone(YEAR_2026))["value"], 640)
        self.save(self.sheet["D"], 260, d(2026, 7, 5))
        self.assertEqual(self.time(self.phone(YEAR_2026))["value"], 600)
        self.assertEqual(self.time(self.phone(ALL_TIME))["value"], 1780)

    def test_reading_with_no_finish_makes_the_year_exist(self):
        item = self.book("unfinished", pages=500)
        ItemFilterFacet.objects.create(item=item, facet_type=ItemFilterFacet.FacetType.GENRE, value="Fantasy")
        ItemCredit.objects.create(
            item=item,
            role=ItemCredit.Role.AUTHOR.value,
            person_source="openlibrary",
            person_id="a1",
            name="Some Author",
            order=0,
        )
        self.begin(item, d(2025, 5, 1))
        self.save(item, 285, d(2025, 6, 1))
        data = self.phone(YEAR_2025)
        self.assertEqual(data["header"]["titles"], 1)
        self.assertEqual(data["header"]["logs"], 0)
        self.assertEqual(self.time(data)["value"], 285)
        self.assertEqual(data["years"]["first"], 2025)
        self.assertEqual(data["genres"]["rows"][0]["titles"], 1)
        names = [person["person"]["name"] for group in data["people"]["groups"] for person in group["rows"]]
        self.assertEqual(names, ["Some Author"])
        tab = next(row for row in data["media_types"] if row["media_type"] == BOOK)
        self.assertEqual((tab["titles"], tab["logs"]), (1, 0))
        follower = self.phone(YEAR_2025, viewer=self.follower)
        self.assertEqual(self.time(follower)["value"], 285)

    def test_active_days_follow_the_rows(self):
        item = self.book("days")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        self.do(book_tracking.pause, item, d(2026, 3, 3))
        data = self.phone(YEAR_2026)
        self.assertEqual(data["header"]["active_days"], 2)
        self.assertEqual(details(data)["longest_weekly_streak"]["value"], 2)

    def test_a_typo_leaves_no_active_day_on_the_day_it_was_fixed(self):
        item = self.book("typo-days")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 300, d(2025, 12, 20))
        self.save(item, 30, d(2026, 1, 5))
        data = self.phone(ALL_TIME)
        self.assertEqual(data["header"]["active_days"], 2)

    def test_a_mark_moves_no_year_and_no_active_day(self):
        item = self.book("mark")
        self.do(book_tracking.mark_read, item, d(2026, 2, 4))
        year = self.phone(YEAR_2026, tab=None)
        self.assertEqual(year["header"]["active_days"], 0)
        self.assertEqual(year["header"]["titles"], 0)
        everything = self.phone(ALL_TIME)
        self.assertEqual(self.time(everything)["value"], 400)
        self.assertEqual(everything["header"]["titles"], 1)
        self.assertEqual(everything["header"]["active_days"], 0)
        self.assertIsNone(everything["years"]["first"])

    def test_a_private_account_of_the_rest_still_uses_the_old_counters(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.set_item(film, runtime_minutes=120)
        self.log(film, datetime(2026, 3, 2, tzinfo=UTC))
        row = next(row for row in self.phone(YEAR_2026, tab=None)["time"]["rows"] if row["media_type"] == "movie")
        self.assertEqual(row["value"], 120)


class MovedViewingTests(BookCalcBase):
    """TV's days take a move a log makes, which no reader asks for until TV switches."""

    def setUp(self):
        super().setUp()
        show, season_item, _tv, self.season = self.tv_show()
        self.episode = self.item(MediaTypes.EPISODE.value, show.media_id, season=1, episode=1)
        self.season_item = season_item

    def scope(self, params, moved):
        """Return a scope whose calculation moved these viewings."""
        scope = build_scope(self.owner, self.owner, params)
        patcher = patch.object(type(scope), "moved_days", return_value=moved)
        patcher.start()
        self.addCleanup(patcher.stop)
        return scope

    def test_nothing_moved_changes_nothing(self):
        viewing = self.viewing(self.season, self.episode, watched_on=d(2026, 3, 1))
        scope = build_scope(self.owner, self.owner, YEAR_2026)
        self.assertEqual(scope.moved_days(), {})
        self.assertEqual(list(scope.viewings), [viewing])

    def test_a_moved_tick_leaves_its_day_and_lands_on_the_logs(self):
        viewing = self.viewing(self.season, self.episode, watched_on=d(2026, 3, 1))
        moved = {viewing.pk: d(2025, 12, 20)}
        self.assertEqual(list(self.scope(YEAR_2026, moved).viewings), [])
        self.assertEqual(list(self.scope(YEAR_2025, moved).viewings), [viewing])
        days = self.scope(YEAR_2025, moved).viewings.annotate(day=self.scope(YEAR_2025, moved).viewing_day())
        self.assertEqual(days.get().day, d(2025, 12, 20))

    def test_a_moved_mark_fill_has_a_day_in_all_time(self):
        viewing = self.viewing(self.season, self.episode, kind="fill")
        self.assertEqual(list(build_scope(self.owner, self.owner, ALL_TIME).dated_viewings), [])
        self.assertEqual(list(self.scope(ALL_TIME, {viewing.pk: d(2025, 12, 20)}).dated_viewings), [viewing])


class AdapterCostTests(BookCalcBase):
    """A switched-off adapter costs nothing, and one request walks once."""

    def counted_adapter(self, media_type=BOOK):
        """Return a mock that counts one adapter's calls."""
        counted = MagicMock(wraps=activity_rows.ADAPTERS[media_type])
        return patch.dict(activity_rows.ADAPTERS, {media_type: counted}), counted

    def test_switched_off_adapters_never_run_on_a_stats_request(self):
        for media_type in (MediaTypes.GAME.value, MediaTypes.ANIME.value, MediaTypes.MANGA.value, MediaTypes.COMIC.value):
            patcher, counted = self.counted_adapter(media_type)
            with patcher:
                self.summary(ALL_TIME)
                self.summary(YEAR_2026, tab=BOOK)
                self.assertEqual(counted.call_count, 0, media_type)

    def test_one_global_request_walks_once(self):
        patcher, counted = self.counted_adapter()
        with patcher:
            self.summary(ALL_TIME)
            self.assertEqual(counted.call_count, 1)
