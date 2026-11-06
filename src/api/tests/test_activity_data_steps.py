"""The one-time steps Activity's preparatory ship carries (scripts/ios/SHIP.md).

Undated imported Reads become marks, and past Restarts get their flag. A dry run changes
nothing, a second run changes nothing, the owner's stats version moves, and nothing
lands on disk: the wrapper keeps old values from standard output.
"""

import io
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.models import F
from django.test import override_settings

from api.services import stats_cache
from api.tests.test_activity_calc_books import BookCalcBase, d
from api.tests.test_stats_v2_activity import ALL_TIME, YEAR_2025, YEAR_2026
from app import book_tracking, tv_tracking
from app.models import (
    TV,
    Book,
    BookSession,
    DiaryEntry,
    Episode,
    Game,
    Item,
    MediaTypes,
    Status,
)
from app.tests.tv_fixtures import make_manual_show
from social.models import ProgressChange
from users.models import QuickWatchDateChoices

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
        self.assertEqual(book.undated_read_previous_status, Status.PLANNING.value)

    def state_of(self, item):
        """Return a book's row, leaving out what tells two rows apart."""
        return Book.objects.filter(item=item).values(*self.state_fields).get()

    @property
    def state_fields(self):
        apart = {"id", "item", "created_at", "progressed_at"}
        return [field.attname for field in Book._meta.concrete_fields if field.name not in apart]

    def history_of(self, item):
        """Return a book's history rows, oldest first, as the same fields say them."""
        book = Book.objects.get(item=item)
        names = {field.attname for field in Book.history.model._meta.concrete_fields}
        fields = [name for name in self.state_fields if name in names]
        rows = Book.history.filter(id=book.pk).order_by("history_date", "history_id")
        return list(rows.values("history_type", *fields))

    def the_same_book_marked_by_the_eye(self, name, **fields):
        """Return a book at To Read with these, after the eye."""
        item = self.book(name)
        Book.objects.bulk_create([Book(user=self.owner, item=item, status=Status.PLANNING.value, **fields)])
        with self.on(d(2026, 3, 1)):
            book_tracking.mark_read(self.owner, item)
        return item

    def test_it_leaves_exactly_what_the_eye_leaves(self):
        """The eye returns at once on a Completed book, so the step writes this."""
        kept = {"score": 8, "notes": "Loved it"}
        eyed = self.the_same_book_marked_by_the_eye("eyed", **kept)
        item = self.imported_read(
            "imported",
            progress=320,
            start_date=datetime(2024, 3, 1, tzinfo=UTC),
            **kept,
        )
        self.assertNotEqual(self.state_of(item), self.state_of(eyed))
        run("fill_undated_reads", apply=True)
        self.assertEqual(self.state_of(item), self.state_of(eyed))
        book = Book.objects.get(item=item)
        self.assertEqual((book.progress, book.start_date, book.end_date), (0, None, None))

    def test_it_writes_a_history_row_as_the_eye_does(self):
        kept = {"notes": "Loved it"}
        eyed = self.the_same_book_marked_by_the_eye("eyed", **kept)
        item = self.imported_read("imported", progress=320, **kept)
        run("fill_undated_reads", apply=True)
        self.assertEqual(self.history_of(item), self.history_of(eyed))
        self.assertEqual([row["history_type"] for row in self.history_of(item)], ["~"])

    def test_it_calls_no_provider_and_queues_no_release_fetch(self):
        self.imported_read("quiet", progress=320)
        with (
            patch("app.providers.services.get_media_metadata", side_effect=AssertionError("A provider call")),
            patch("requests.Session.request", side_effect=AssertionError("A network call")),
            patch("app.models.Item.fetch_releases", side_effect=AssertionError("A release fetch")),
        ):
            run("fill_undated_reads", apply=True)
        self.assertTrue(Book.objects.get(user=self.owner).completed_manually)

    def test_its_old_values_undo_it(self):
        """They're all the wrapper keeps of a row, so they have to rebuild it."""
        item = self.imported_read(
            "undoable",
            progress=320,
            start_date=datetime(2024, 3, 1, tzinfo=UTC),
            score=8,
            notes="Loved it",
        )
        before = self.state_of(item)
        out, _ = run("fill_undated_reads", apply=True)
        self.assertNotEqual(self.state_of(item), before)
        old = json.loads(out)
        Book.objects.filter(pk=old.pop("book")).update(**old)
        self.assertEqual(self.state_of(item), before)

    def test_its_old_values_hold_no_title_or_notes(self):
        self.imported_read("A Secret Title", notes="A secret note")
        out, _ = run("fill_undated_reads", apply=True)
        self.assertNotIn("Secret", out)
        self.assertNotIn("secret", out)

    def test_a_marked_read_can_still_be_undone_and_keeps_the_row(self):
        """The importer made the row, so turning the eye off can't delete it."""
        item = self.imported_read(
            "undo",
            progress=320,
            start_date=datetime(2024, 3, 1, tzinfo=UTC),
            score=8,
            notes="Loved it",
        )
        run("fill_undated_reads", apply=True)
        self.assertEqual(Book.objects.get(item=item).score, 8)
        with self.on(d(2026, 3, 1)):
            book_tracking.undo_read(self.owner, item)
        book = Book.objects.get(item=item)
        self.assertEqual(book.status, Status.PLANNING.value)
        self.assertEqual(book.notes, "Loved it")
        self.assertIsNone(book.score)
        self.assertEqual((book.progress, book.start_date, book.end_date), (0, None, None))
        self.assertFalse(book.completed_manually)
        self.assert_numbers(0, 0, 0)

    def test_undoing_a_read_that_held_its_length_leaves_no_place(self):
        """The row before the step's mark still says 400, and that's its length."""
        item = self.book("held-length")
        Book.objects.create(user=self.owner, item=item, status=Status.COMPLETED.value, progress=400)
        run("fill_undated_reads", apply=True)
        self.assert_numbers(0, 0, 400)
        self.do(book_tracking.undo_read, item, d(2026, 3, 1))
        self.assert_numbers(0, 0, 0)
        book = Book.objects.get(item=item)
        self.assertEqual((book.status, book.progress), (Status.PLANNING.value, 0))
        self.begin(item, d(2026, 3, 2))
        self.assertEqual(Book.objects.get(item=item).progress, 0)
        self.assertEqual(self.rows(item), ["2026-03-02 started"])

    def test_turning_the_eye_off_afterward_leaves_what_it_leaves_on_any_mark(self):
        kept = {"score": 8, "notes": "Loved it"}
        eyed = self.the_same_book_marked_by_the_eye("eyed", **kept)
        item = self.imported_read("imported", progress=320, **kept)
        run("fill_undated_reads", apply=True)
        with self.on(d(2026, 3, 1)):
            book_tracking.undo_read(self.owner, eyed)
            book_tracking.undo_read(self.owner, item)
        self.assertEqual(self.state_of(item), self.state_of(eyed))

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
    """Decision 60: books stopped and picked up before Phase 1 get their place back."""

    def picked_up_before(self, name="old", value=200, kind="pages", pages=400):
        """Write a pick-up as it was before a new journey opened at its place."""
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
        """Return Stats' whole payload as the phone reads it, each year and All Time."""
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

    def test_a_percent_place_changes_no_number_either(self):
        """Decision 51: every Stats payload reads alike with and without the copy."""
        self.picked_up_before("percent-stats", value=25, kind="percentage", pages=None)
        before = self.payloads()
        run("fill_picked_up_books", apply=True)
        self.assertEqual(self.payloads(), before)

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

    def test_a_mark_in_between_keeps_its_zero(self):
        item = self.book("mark-between")
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 2))
        self.do(book_tracking.drop, item, d(2025, 11, 3))
        self.do(book_tracking.mark_read, item, d(2025, 11, 4))
        self.do(book_tracking.assign_status, item, d(2025, 11, 5), Status.PLANNING.value)
        self.begin(item, d(2026, 4, 1))
        start_row = ProgressChange.objects.filter(item=item, current_progress__kind=ProgressChange.START).latest("id")
        for field in ("previous_progress", "current_progress"):
            getattr(start_row, field).pop("began_over")
        start_row.save(update_fields=["previous_progress", "current_progress"])
        _, err = run("fill_picked_up_books")
        self.assertIn("no place: 0", err)

    def blank(self, item):
        """Empty the journey just picked up, as before one opened at its place."""
        journey = Book.objects.get(item=item).current_session
        BookSession.objects.filter(pk=journey.pk).update(pages_read=None, percentage_read=None)
        Book.objects.filter(item=item).update(progress=0)
        return journey

    def test_a_restart_with_no_reading_then_a_pick_up_changes_no_number(self):
        """Stopped at 200, Restart, stopped with nothing read, back: it began at 0."""
        item = self.book("chain")
        self.begin(item, d(2025, 11, 1))
        self.save(item, 200, d(2025, 11, 2))
        self.do(book_tracking.restart, item, d(2025, 11, 3))
        self.do(book_tracking.drop, item, d(2025, 11, 4))
        self.begin(item, d(2026, 4, 1))
        journey = self.blank(item)
        before = self.numbers()
        _, err = run("fill_picked_up_books", apply=True)
        self.assertIn("no place: 0", err)
        self.assertEqual(self.numbers(), before)
        journey.refresh_from_db()
        self.assertIsNone(journey.pages_read)

    def test_a_pick_up_a_restart_a_stop_and_a_pick_up_changes_no_number(self):
        item = self.book("over-a-restart")
        self.begin(item, d(2025, 5, 1))
        self.save(item, 200, d(2025, 5, 2))
        self.do(book_tracking.drop, item, d(2025, 5, 3))
        self.begin(item, d(2025, 6, 1))
        self.blank(item)
        self.do(book_tracking.restart, item, d(2025, 6, 2))
        self.do(book_tracking.drop, item, d(2025, 6, 3))
        self.begin(item, d(2026, 5, 1))
        self.blank(item)
        before = self.numbers()
        self.assertEqual(before, {"2025": 200, "2026": 0, "all_time": 200})
        run("fill_picked_up_books", apply=True)
        self.assertEqual(self.numbers(), before)

    def test_two_pick_ups_with_nothing_read_between_walk_back_to_the_place(self):
        item = self.book("twice-back")
        self.begin(item, d(2025, 5, 1))
        self.save(item, 200, d(2025, 5, 2))
        self.do(book_tracking.drop, item, d(2025, 5, 3))
        self.begin(item, d(2025, 6, 1))
        self.blank(item)
        self.do(book_tracking.drop, item, d(2025, 6, 2))
        self.begin(item, d(2026, 5, 1))
        journey = self.blank(item)
        before = self.numbers()
        run("fill_picked_up_books", apply=True)
        journey.refresh_from_db()
        self.assertEqual(journey.pages_read, 200)
        self.assertEqual(self.numbers(), before)

    def test_a_file_between_the_stop_and_the_pick_up_is_walked_past(self):
        item = self.book("past-a-file")
        self.begin(item, d(2025, 5, 1))
        self.save(item, 200, d(2025, 5, 2))
        self.do(book_tracking.drop, item, d(2025, 5, 3))
        with self.on(d(2025, 6, 1)):
            book_tracking.complete(self.owner, item, completion_date=d(2024, 1, 1), update_current=False, force_direct=True)
        self.begin(item, d(2026, 5, 1))
        journey = self.blank(item)
        before = self.numbers()
        run("fill_picked_up_books", apply=True)
        journey.refresh_from_db()
        self.assertEqual(journey.pages_read, 200)
        self.assertEqual(self.numbers(), before)

    def test_a_read_after_a_log_keeps_its_zero(self):
        item = self.book("after-a-log")
        self.begin(item, d(2025, 5, 1))
        self.save(item, 200, d(2025, 5, 2))
        self.do(book_tracking.drop, item, d(2025, 5, 3))
        self.finish(item, d(2025, 6, 1))
        self.begin(item, d(2026, 5, 1))
        ProgressChange.objects.filter(item=item, current_progress__began_over=True).delete()
        self.blank(item)
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


class TwerpTests(StepBase):
    """Twerp, a Read with a date and no log or mark, gets the eye (2026-11-04's yes)."""

    def twerp(self, owner=None, **fields):
        """Write Twerp as it sits on account 1: Completed on 2026-06-24, with no log."""
        owner = owner or self.owner
        count = Item.objects.count()
        item = self.item(MediaTypes.BOOK.value, f"twerp-{owner.pk}-{count}", title="Twerp")
        self.set_item(item, total_pages=290)
        fields = {
            "status": Status.COMPLETED.value,
            "progress": 290,
            "completed_manually": False,
            "end_date": datetime(2026, 6, 24, tzinfo=UTC),
            **fields,
        }
        Book.objects.bulk_create([Book(user=owner, item=item, **fields)])
        return item

    def step(self, **options):
        return run("mark_twerp", user=self.owner.pk, **options)

    def stats(self):
        """Return Books' pages in 2025, 2026 and All Time, as Stats send them."""
        pages = []
        for params in (YEAR_2025, YEAR_2026, ALL_TIME):
            cache.clear()
            data = self.get(self.owner, {**params, "media_type": MediaTypes.BOOK.value}).json()
            row = next((row for row in data["time"]["rows"] if row["media_type"] == MediaTypes.BOOK.value), None)
            pages.append(row["value"] if row else 0)
        return pages

    def test_the_dry_run_prints_the_count_and_pages_and_writes_nothing(self):
        item = self.twerp()
        out, err = self.step()
        self.assertEqual(out, "")
        self.assertIn("twerp: 1 found (expected 1), pages: 290 (expected 290)", err)
        self.assertIn("dry run: nothing changed", err)
        self.assertFalse(Book.objects.get(item=item).completed_manually)

    def test_apply_raises_all_time_by_its_length_and_moves_no_year_or_row(self):
        item = self.twerp()
        self.assert_numbers(0, 0, 0)
        before = self.stats()
        version = self.version()
        connection.run_on_commit = []
        with self.captureOnCommitCallbacks(execute=True):
            out, err = self.step(apply=True)
        old = json.loads(out)
        self.assertEqual((old["completed_manually"], old["progress"]), (False, 290))
        self.assertEqual(old["end_date"], "2026-06-24T00:00:00Z")
        self.assertIn("marked read with no date", err)
        self.assertNotEqual(self.version(), version, "The owner's stats version moves")
        self.assert_numbers(0, 0, 290)
        self.assertEqual(self.rows(), [])
        after = self.stats()
        self.assertEqual(after[:2], before[:2], "No year moves")
        self.assertEqual(after[2], before[2] + 290)
        book = Book.objects.get(item=item)
        self.assertTrue(book.completed_manually)
        self.assertIsNone(book.end_date)

    def test_a_second_run_finds_nothing(self):
        self.twerp()
        self.step(apply=True)
        out, err = self.step(apply=True)
        self.assertEqual(out, "")
        self.assertIn("twerp: 0 found", err)
        self.assertIn("nothing matches, so nothing changed", err)

    def test_none_or_two_matches_change_nothing(self):
        out, err = self.step(apply=True)
        self.assertEqual(out, "")
        self.assertIn("nothing matches, so nothing changed", err)
        first = self.twerp()
        second = self.item(MediaTypes.BOOK.value, "twerp-again", title="Twerp")
        Book.objects.bulk_create(
            [Book(user=self.owner, item=second, status=Status.COMPLETED.value, end_date=datetime(2026, 6, 24, tzinfo=UTC))],
        )
        out, err = self.step(apply=True)
        self.assertEqual(out, "")
        self.assertIn("twerp: 2 found", err)
        self.assertIn("more than one matches, so nothing changed", err)
        self.assertFalse(Book.objects.filter(item__in=[first, second], completed_manually=True).exists())

    def test_a_log_a_mark_another_day_or_another_account_is_left_alone(self):
        logged = self.twerp()
        self.log(logged, datetime(2026, 6, 24, tzinfo=UTC))
        _, err = self.step()
        self.assertIn("twerp: 0 found", err)
        Book.objects.filter(item=logged).delete()
        self.twerp(end_date=datetime(2026, 6, 25, tzinfo=UTC))
        _, err = self.step()
        self.assertIn("twerp: 0 found", err)
        self.twerp(owner=self.follower)
        _, err = self.step()
        self.assertIn("twerp: 0 found", err)


class OldEyeTapsTests(StepBase):
    """Old eye taps leave their years, and All Time holds (decision 49)."""

    def setUp(self):
        super().setUp()
        self.tapped = self.show()

    def show(self, episodes=3):
        show = make_manual_show(seasons={1: episodes})
        Item.objects.filter(media_id=show.media_id, media_type=MediaTypes.EPISODE.value).update(runtime_minutes=45)
        return show

    def tap(self, show, day, *numbers):
        """Write what the old eye wrote: a season's episodes, dated, in one moment."""
        with self.on(day):
            for number in numbers:
                tv_tracking.tick(self.owner, show, 1, number)

    def repair(self):
        """Write the TV repair's first fill: only viewings saved before it are taps."""
        with self.on(d(2026, 10, 27)):
            tv_tracking.mark_watched(self.owner, self.show(), season_number=1)

    def step(self, **options):
        return run("undate_old_eye_taps", **options)

    def minutes(self):
        """Return TV minutes in 2025, 2026 and All Time, as Stats send them."""
        found = []
        for params in (YEAR_2025, YEAR_2026, ALL_TIME):
            cache.clear()
            data = self.get(self.owner, {**params, "media_type": MediaTypes.TV.value}).json()
            row = next((row for row in data["time"]["rows"] if row["media_type"] == MediaTypes.TV.value), None)
            found.append(row["value"] if row else 0)
        return found

    def kinds(self, show):
        return list(
            Episode.objects.filter(related_season__related_tv__item=show)
            .order_by("item__episode_number")
            .values_list("kind", "watched_on"),
        )

    def test_the_dry_run_the_run_and_a_second_run(self):
        self.tap(self.tapped, d(2025, 3, 1), 1, 2, 3)
        self.repair()
        self.assert_step_works("undate_old_eye_taps", lambda: self.kinds(self.tapped))

    def test_the_tap_leaves_its_year_and_its_start_row_goes(self):
        self.tap(self.tapped, d(2025, 3, 1), 1, 2, 3)
        self.repair()
        self.assertEqual(self.minutes(), [135, 0, 270])
        starts = ProgressChange.objects.filter(actor=self.owner, item=self.tapped, current_progress__kind="start")
        self.assertEqual(starts.count(), 1)
        _out, err = self.step()
        self.assertIn(f"user {self.owner.pk}: 1 taps, 3 episodes (2025: 3 = 135 min)", err)
        self.step(apply=True)
        self.assertEqual(self.minutes(), [0, 0, 270])
        self.assertEqual(self.kinds(self.tapped), [(Episode.Kind.FILL, None)] * 3)
        self.assertFalse(starts.exists())
        self.assertIsNone(TV.objects.get(user=self.owner, item=self.tapped).last_watched_on)

    def test_a_single_tick_a_shared_window_and_an_old_logs_own_episodes_stay(self):
        self.tap(self.tapped, d(2025, 3, 1), 1)
        other, logged = self.show(), self.show()
        with self.on(d(2025, 4, 1)):
            tv_tracking.tick(self.owner, other, 1, 1)
            tv_tracking.tick(self.owner, other, 1, 2)
            tv_tracking.tick(self.owner, self.tapped, 1, 2)
        season = Item.objects.get(media_id=logged.media_id, media_type=MediaTypes.SEASON.value)
        with self.on(d(2025, 5, 1)):
            DiaryEntry.objects.create(
                user=self.owner,
                item=season,
                consumed_at=datetime(2025, 5, 1, tzinfo=UTC),
                progress_snapshot={"tv": {"v": 1, "covers": {"1": [1, 2, 3]}}},
            )
            self.tap(logged, d(2025, 5, 1), 1, 2)
        self.repair()
        before = [self.kinds(show) for show in (self.tapped, other, logged)]
        _out, err = self.step(apply=True)
        self.assertIn("0 episodes in all", err)
        self.assertEqual([self.kinds(show) for show in (self.tapped, other, logged)], before)

    def test_a_tap_after_the_repair_is_a_tick(self):
        self.repair()
        self.tap(self.tapped, d(2026, 10, 28), 1, 2, 3)
        _out, err = self.step(apply=True)
        self.assertIn("0 episodes in all", err)
        self.assertEqual({kind for kind, _day in self.kinds(self.tapped)}, {Episode.Kind.TICK})

    def release_date_account(self):
        self.owner.quick_watch_date = QuickWatchDateChoices.RELEASE_DATE
        self.owner.save(update_fields=["quick_watch_date"])

    def test_on_a_release_date_account_one_bulk_write_is_a_tap(self):
        self.release_date_account()
        self.tap(self.tapped, d(2025, 3, 1), 1, 2, 3)
        self.repair()
        self.step(apply=True)
        self.assertEqual(self.kinds(self.tapped), [(Episode.Kind.FILL, None)] * 3)

    def test_on_a_release_date_account_two_quick_ticks_stay_ticks(self):
        """Two taps half a second apart are real ticks, not one bulk write."""
        self.release_date_account()
        self.tap(self.tapped, d(2025, 3, 1), 1, 2)
        Episode.objects.filter(related_season__related_tv__item=self.tapped, item__episode_number=2).update(
            created_at=F("created_at") + timedelta(milliseconds=500),
        )
        self.repair()
        _out, err = self.step(apply=True)
        self.assertIn("0 episodes in all", err)
        self.assertEqual({kind for kind, _day in self.kinds(self.tapped)}, {Episode.Kind.TICK})

    def test_apply_refuses_more_episodes_than_expected(self):
        shows = [self.show() for _ in range(6)]
        for number, show in enumerate(shows, start=1):
            self.tap(show, d(2025, 3, number), 1, 2, 3)
        self.repair()
        before = [self.kinds(show) for show in shows]
        with self.assertRaisesMessage(CommandError, "18 episodes"):
            self.step(apply=True)
        self.assertEqual([self.kinds(show) for show in shows], before)
        _out, err = self.step()
        self.assertIn("18 episodes in all (expected at most 17)", err)
        self.step(apply=True, max=18)
        self.assertEqual({kind for show in shows for kind, _day in self.kinds(show)}, {Episode.Kind.FILL})
