"""The one-time steps Activity's preparatory ship carries (scripts/ios/SHIP.md).

Undated imported Reads become marks, and past Restarts get their flag. A dry run changes
nothing, a second run changes nothing, the owner's stats version moves, and nothing
lands on disk: the wrapper keeps old values from standard output.
"""

import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import override_settings

from api.services import stats_cache
from api.tests.test_activity_calc_books import BookCalcBase, d
from api.tests.test_stats_v2_activity import ALL_TIME, YEAR_2025, YEAR_2026
from app import book_tracking
from app.models import Book, BookSession, Game, MediaTypes, Status
from social.models import ProgressChange

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "activity-steps"}}


def run(command, **options):
    """Run a step and return (standard output, standard error)."""
    out, err = io.StringIO(), io.StringIO()
    call_command(command, stdout=out, stderr=err, **options)
    return out.getvalue(), err.getvalue()


@override_settings(CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class StepBase(BookCalcBase):
    """A cache that versions stats, so a step's bump shows."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def version(self):
        """Return the owner's stats version."""
        return cache.get(stats_cache.user_version_key(self.owner.pk))

    def assert_step_works(self, command, read_state):
        """Check the dry run, the run, the second run and that nothing lands on disk."""
        before = read_state()
        version = self.version()
        out, err = run(command)
        self.assertEqual(out, "", "A dry run writes no old values")
        self.assertIn("dry run", err)
        self.assertEqual(read_state(), before)
        self.assertEqual(self.version(), version)
        # The test's own writes are still pending: a bump queues once per owner.
        connection.run_on_commit = []
        with TemporaryDirectory() as folder:
            start = Path.cwd()
            os.chdir(folder)
            try:
                with self.captureOnCommitCallbacks(execute=True):
                    out, err = run(command, apply=True)
            finally:
                os.chdir(start)
            self.assertEqual(list(Path(folder).iterdir()), [], "Nothing lands on the container's disk")
        self.assertTrue(out.strip(), "The run prints its old values for the wrapper to keep")
        for line in out.splitlines():
            json.loads(line)
        changed = read_state()
        self.assertNotEqual(changed, before)
        self.assertNotEqual(self.version(), version)
        out, err = run(command, apply=True)
        self.assertEqual(out, "")
        self.assertEqual(read_state(), changed)


class UndatedReadsTests(StepBase):
    """Decision 49: undated Goodreads and StoryGraph Reads become marks."""

    def imported_read(self, name, **fields):
        """Write a Read the way the importers do."""
        item = self.book(name)
        fields = {"status": Status.COMPLETED.value, "progress": 0, "completed_manually": False, **fields}
        Book.objects.bulk_create([Book(user=self.owner, item=item, **fields)])
        return item

    def test_it_raises_all_time_and_no_year(self):
        item = self.imported_read("undated")
        self.assert_numbers(0, 0, 0)

        def state():
            return (Book.objects.get(item=item).completed_manually, self.numbers())

        self.assert_step_works("fill_undated_reads", state)
        self.assert_numbers(0, 0, 400)
        self.assertEqual(self.rows(), [])
        book = Book.objects.get(item=item)
        self.assertTrue(book.undated_read_previous_tracked)
        self.assertIsNone(book.undated_read_previous_status)

    def test_a_marked_read_can_still_be_undone_and_keeps_the_row(self):
        """The importer made the row, so turning the eye off can't delete it."""
        item = self.imported_read("undo", score=8, notes="Loved it")
        run("fill_undated_reads", apply=True)
        self.assertEqual(Book.objects.get(item=item).score, 8)
        with self.on(d(2026, 3, 1)):
            book_tracking.undo_read(self.owner, item)
        book = Book.objects.get(item=item)
        self.assertEqual(book.status, Status.PLANNING.value)
        self.assertEqual(book.notes, "Loved it")
        self.assertFalse(book.completed_manually)
        self.assert_numbers(0, 0, 0)

    def test_a_read_holding_a_stopped_journey_holds_only_what_is_left(self):
        """Stopped, To Read, then a status written on the row, as the web form does."""
        item = self.book("stopped")
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 10))
        self.do(book_tracking.drop, item, d(2025, 11, 15))
        self.do(book_tracking.assign_status, item, d(2026, 1, 2), Status.PLANNING.value)
        Book.objects.filter(item=item).update(status=Status.COMPLETED.value)
        self.assert_numbers(200, 0, 200)
        run("fill_undated_reads", apply=True)
        self.assert_numbers(200, 0, 400)

    def test_the_dry_run_prints_the_rest_of_count_10_and_writes_none_of_it(self):
        """Dated Reads with no log, and marks holding a date: no step writes these."""
        self.imported_read("undated")
        dated = self.imported_read("dated", end_date=datetime(2024, 3, 2, tzinfo=UTC))
        self.set_item(self.imported_read("no-length", end_date=datetime(2024, 3, 3, tzinfo=UTC)), total_pages=None)
        marked = self.book("marked", pages=300)
        Book.objects.bulk_create(
            [
                Book(
                    user=self.owner,
                    item=marked,
                    status=Status.COMPLETED.value,
                    completed_manually=True,
                    end_date=datetime(2024, 3, 4, tzinfo=UTC),
                ),
            ],
        )
        game = self.item(MediaTypes.GAME.value, "marked-game", title="marked game")
        Game.objects.bulk_create(
            [
                Game(
                    user=self.follower,
                    item=game,
                    status=Status.COMPLETED.value,
                    completed_manually=True,
                    end_date=datetime(2024, 3, 5, tzinfo=UTC),
                ),
            ],
        )
        before = list(Book.objects.order_by("id").values())
        out, err = run("fill_undated_reads")
        self.assertEqual(out, "")
        self.assertEqual(list(Book.objects.order_by("id").values()), before)
        source = dated.source
        self.assertIn(f"undated reads: 1, by source: {{'{source}': 1}}", err)
        self.assertIn(f"dated reads with no log, not written: 2, by source: {{'{source}': 2}}, pages per account: {{{self.owner.pk}: 400}}", err)
        self.assertIn(f"marked books with end_date, not written: {{{self.owner.pk}: 1}}", err)
        self.assertIn(f"marked games with end_date, not written: {{{self.follower.pk}: 1}}", err)
        out, err = run("fill_undated_reads", apply=True)
        self.assertIn("dated reads with no log, not written: 2", err)
        self.assertFalse(Book.objects.get(item=dated).completed_manually)
        self.assertEqual(Book.objects.get(item=marked).end_date, datetime(2024, 3, 4, tzinfo=UTC))

    def test_it_leaves_alone_a_log_a_date_a_journey_and_a_mark(self):
        logged = self.book("logged")
        self.finish(logged, d(2025, 3, 1))
        dated = self.imported_read("dated", end_date=datetime(2024, 3, 2, tzinfo=UTC))
        marked = self.book("marked")
        self.do(book_tracking.mark_read, marked, d(2026, 1, 1))
        reading = self.book("reading")
        self.begin(reading, d(2026, 1, 1))
        before = list(Book.objects.order_by("id").values())
        out, err = run("fill_undated_reads", apply=True)
        self.assertEqual(out, "")
        self.assertIn("undated reads: 0", err)
        self.assertEqual(list(Book.objects.order_by("id").values()), before)
        self.assertFalse(Book.objects.get(item=dated).completed_manually)

    def test_it_covers_every_account(self):
        item = self.imported_read("mine")
        other = self.book("theirs")
        Book.objects.bulk_create([Book(user=self.follower, item=other, status=Status.COMPLETED.value)])
        _, err = run("fill_undated_reads", apply=True)
        self.assertIn("undated reads: 2", err)
        self.assertTrue(Book.objects.get(item=item).completed_manually)
        self.assertTrue(Book.objects.get(item=other).completed_manually)


class PastRestartsTests(StepBase):
    """Decision 49: past book Restarts get their flag; 120, Restart, 50 reads 170."""

    def old_restart(self, name="restarted"):
        """Restart as old code did: no flag on its start row."""
        item = self.book(name)
        self.begin(item, d(2026, 5, 1))
        self.save(item, 120, d(2026, 5, 2))
        self.do(book_tracking.restart, item, d(2026, 5, 10))
        latest = ProgressChange.objects.filter(item=item, current_progress__kind=ProgressChange.START).latest("id")
        for field in ("previous_progress", "current_progress"):
            getattr(latest, field).pop("began_over")
        latest.save(update_fields=["previous_progress", "current_progress"])
        self.save(item, 50, d(2026, 5, 11))
        return item

    def test_it_makes_120_restart_50_read_170(self):
        item = self.old_restart()
        # With no flag it reads as a typo fix.
        self.assert_numbers(0, 50, 50)

        def state():
            return (self.numbers(), self.rows(item))

        self.assert_step_works("flag_past_restarts", state)
        self.assert_numbers(0, 170, 170)

    def test_a_pick_up_with_its_status_row_is_no_restart(self):
        item = self.book("picked-up")
        self.begin(item, d(2026, 5, 1))
        self.do(book_tracking.drop, item, d(2026, 5, 10))
        self.begin(item, d(2026, 5, 10))
        out, err = run("flag_past_restarts", apply=True)
        self.assertEqual(out, "")
        self.assertIn("past restarts: 0", err)

    def test_a_pick_up_on_another_day_is_no_restart(self):
        item = self.book("later")
        self.begin(item, d(2026, 5, 1))
        self.do(book_tracking.drop, item, d(2026, 5, 10))
        ProgressChange.objects.filter(item=item, current_progress__kind=ProgressChange.STATUS).delete()
        self.begin(item, d(2026, 5, 11))
        ProgressChange.objects.filter(item=item, current_progress__kind=ProgressChange.STATUS).delete()
        _, err = run("flag_past_restarts", apply=True)
        self.assertIn("past restarts: 0", err)

    def test_a_restart_paused_later_is_still_a_restart(self):
        """A later pause or pick-up writes a status row, which isn't about its start."""
        item = self.old_restart()
        self.do(book_tracking.pause, item, d(2026, 5, 12))
        self.do(book_tracking.resume, item, d(2026, 5, 13))
        _, err = run("flag_past_restarts", apply=True)
        self.assertIn("past restarts: 1", err)
        self.assert_numbers(0, 170, 170)

    def test_a_restart_paused_before_any_save_is_still_a_restart(self):
        item = self.book("paused-at-once")
        self.begin(item, d(2026, 5, 1))
        self.save(item, 120, d(2026, 5, 2))
        self.do(book_tracking.restart, item, d(2026, 5, 10))
        latest = ProgressChange.objects.filter(item=item, current_progress__kind=ProgressChange.START).latest("id")
        for field in ("previous_progress", "current_progress"):
            getattr(latest, field).pop("began_over")
        latest.save(update_fields=["previous_progress", "current_progress"])
        self.do(book_tracking.pause, item, d(2026, 5, 10))
        _, err = run("flag_past_restarts", apply=True)
        self.assertIn("past restarts: 1", err)

    def test_it_says_how_many_per_account(self):
        self.old_restart()
        self.assertEqual(BookSession.objects.filter(related_book__item__media_type=MediaTypes.BOOK.value).count(), 2)
        _, err = run("flag_past_restarts")
        self.assertIn(f"per account: {{{self.owner.pk}: 1}}", err)


class PickedUpBooksTests(StepBase):
    """Decision 60: books stopped and picked back up before Phase 1 get their place back."""

    def picked_up_before(self, name="old", value=200, kind="pages", pages=400):
        """A pick-up as it was before a new journey opened at its place."""
        item = self.book(name, pages=pages)
        self.begin(item, d(2025, 11, 1))
        self.save(item, value, d(2025, 11, 2), kind=kind)
        self.do(book_tracking.drop, item, d(2025, 11, 3))
        self.begin(item, d(2026, 4, 1))
        journey = Book.objects.get(item=item).current_session
        BookSession.objects.filter(pk=journey.pk).update(pages_read=None, percentage_read=None)
        Book.objects.filter(item=item).update(progress=0)
        return item, journey

    def payloads(self):
        """Return Stats' whole payload, as the phone reads it, for each year and All Time."""
        return [self.summary(params, tab="book") for params in (YEAR_2025, YEAR_2026, ALL_TIME)]

    def test_a_dry_run_changes_nothing_and_counts_per_account(self):
        _item, journey = self.picked_up_before()
        _, err = run("fill_picked_up_books")
        self.assertIn(f"count 15, books picked up with no place: 1, per account: {{{self.owner.pk}: 1}}", err)
        journey.refresh_from_db()
        self.assertIsNone(journey.pages_read)

    def test_apply_gives_a_page_place_back_and_a_second_run_changes_nothing(self):
        item, journey = self.picked_up_before()
        out, _ = run("fill_picked_up_books", apply=True)
        self.assertEqual(json.loads(out), {"journey": journey.pk, "pages_read": None, "percentage_read": None})
        journey.refresh_from_db()
        self.assertEqual(journey.pages_read, 200)
        self.assertEqual(Book.objects.get(item=item).progress, 200)
        out, err = run("fill_picked_up_books", apply=True)
        self.assertEqual(out, "")
        self.assertIn("no place: 0", err)

    def test_apply_gives_a_percent_place_back(self):
        _item, journey = self.picked_up_before("percent", value=25, kind="percentage", pages=None)
        run("fill_picked_up_books", apply=True)
        journey.refresh_from_db()
        self.assertIsNone(journey.pages_read)
        self.assertEqual(float(journey.percentage_read), 25.0)

    def test_no_number_and_no_row_changes(self):
        self.picked_up_before()
        rows = ProgressChange.objects.count()
        before = self.payloads()
        run("fill_picked_up_books", apply=True)
        self.assertEqual(self.payloads(), before)
        self.assertEqual(ProgressChange.objects.count(), rows)

    def test_a_restart_and_a_reread_keep_their_zero(self):
        restarted = self.book("restarted")
        self.begin(restarted, d(2026, 3, 1))
        self.save(restarted, 100, d(2026, 3, 2))
        self.do(book_tracking.restart, restarted, d(2026, 3, 3))
        reread = self.book("reread")
        self.finish(reread, d(2026, 3, 1))
        self.begin(reread, d(2026, 3, 2))
        _, err = run("fill_picked_up_books")
        self.assertIn("no place: 0", err)

    def test_the_owners_version_moves(self):
        self.picked_up_before()
        key = stats_cache.user_version_key(self.owner.pk)
        with self.captureOnCommitCallbacks(execute=True):
            setattr(connection, stats_cache.PENDING_ATTR, (None, None))
            before = cache.get(key)
            run("fill_picked_up_books", apply=True)
        self.assertNotEqual(cache.get(key), before)
