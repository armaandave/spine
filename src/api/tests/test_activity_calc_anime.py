"""The one calculation on anime, switched off: Stats count anime's viewings (AN-704).

The adapter reads anime's history rows as its saves. A row with a change reason says
what it was; a row without one is read at its moment. The rows here are saved directly,
as anime's history holds them from before docs/anime-tracking-contract.md. Amount is
episodes times the live runtime. The provider is faked: every anime save reaches it.
"""

import io
from datetime import datetime
from decimal import Decimal
from unittest import mock
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core.management import call_command
from django.test import override_settings

from api.services.activity_rows import list_rows, piled, rows_for
from api.tests.test_activity_calc_books import BookCalcBase, d
from app import anime_tracking
from app.models import Anime, MediaTypes, Status
from app.tests.anime_fixtures import AnimeTestCase

ANIME = MediaTypes.ANIME.value
NEW_YORK = "America/New_York"
RUNTIME = 24
Y2025 = {"start": d(2025, 1, 1), "end": d(2025, 12, 31)}
Y2026 = {"start": d(2026, 1, 1), "end": d(2026, 12, 31)}


@override_settings(TIME_ZONE=NEW_YORK)
class AnimeCalcBase(BookCalcBase):
    """Save rows directly, on a 12-episode anime at 24 minutes."""

    def setUp(self):
        super().setUp()
        for patcher in (
            mock.patch("app.models.Item.fetch_releases"),
            mock.patch("app.providers.services.get_media_metadata", return_value={"max_progress": 12}),
            mock.patch("app.signals.update_daily_statistics.delay"),
            mock.patch("app.services.update_daily_statistics.delay"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def anime(self, name="anime", runtime=RUNTIME):
        item = self.item(ANIME, name, title=name)
        self.set_item(item, runtime_minutes=runtime)
        return item

    def track(self, item, day, *, progress=0, status=Status.IN_PROGRESS.value):
        with self.on(day):
            return Anime.objects.create(user=self.owner, item=item, status=status, progress=progress)

    def type_number(self, row, value, day):
        with self.on(day):
            row.progress = value
            row.save()

    def calc(self, viewer=None):
        return rows_for([self.owner], viewer or self.owner, types=[ANIME])

    def number(self, start=None, end=None, *, pile=False, viewer=None):
        calculation = self.calc(viewer)
        rows = list_rows(calculation.rows, media_type=ANIME, start_date=start, end_date=end)
        total = sum((row.amount for row in rows), Decimal(0))
        return int(total + (piled(calculation.piles, ANIME).amount if pile else 0))

    def assert_years(self, in_2025, in_2026, all_time, viewer=None):
        self.assertEqual(
            (
                self.number(**Y2025, viewer=viewer),
                self.number(**Y2026, viewer=viewer),
                self.number(pile=True, viewer=viewer),
            ),
            (in_2025 * RUNTIME, in_2026 * RUNTIME, all_time * RUNTIME),
        )

    def lines(self):
        lines = []
        for row in list_rows(self.calc().rows, media_type=ANIME):
            parts = []
            if row.log_id:
                parts.append(f"log +{int(row.told)}" if row.told else "log")
            elif row.told:
                parts.append(f"watched {int(row.told)}")
            elif row.started:
                parts.append("started")
            lines.append(f"{row.day} " + ", ".join(parts + row.moves))
        return lines


class SavesTests(AnimeCalcBase):
    """A number saved on the row counts on its day."""

    def test_typed_numbers_count_on_their_days_with_their_minutes(self):
        item = self.anime()
        row = self.track(item, d(2025, 12, 1))
        self.type_number(row, 3, d(2025, 12, 20))
        self.type_number(row, 5, d(2026, 1, 10))
        self.assert_years(3, 2, 5)
        self.assertEqual(self.lines(), ["2026-01-10 watched 2", "2025-12-20 watched 3", "2025-12-01 started"])
        told = next(row for row in self.calc().rows if row.day == d(2026, 1, 10))
        self.assertEqual((int(told.told), int(told.amount), told.told_unit), (2, 2 * RUNTIME, "episode"))

    def test_no_length_is_told_and_unknown(self):
        item = self.anime(runtime=None)
        row = self.track(item, d(2026, 2, 1))
        self.type_number(row, 3, d(2026, 2, 2))
        self.assertEqual(self.number(pile=True), 0)
        told = next(row for row in self.calc().rows if row.told)
        self.assertEqual((int(told.told), told.known, told.counts_title), (3, False, True))

    def test_paused_and_dropped_make_no_rows(self):
        item = self.anime()
        row = self.track(item, d(2026, 2, 1))
        self.type_number(row, 3, d(2026, 2, 2))
        with self.on(d(2026, 2, 3)):
            row.status = Status.PAUSED.value
            row.save()
        with self.on(d(2026, 2, 4)):
            row.status = Status.IN_PROGRESS.value
            row.save()
        self.assertEqual(self.lines(), ["2026-02-02 watched 3", "2026-02-01 started"])


class MarkTests(AnimeCalcBase):
    """Marks land in the pile and never make a row (decision 11)."""

    def test_a_tracking_row_with_progress_and_no_history_sits_in_the_pile(self):
        item = self.anime()
        Anime.objects.bulk_create([Anime(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=7)])
        self.assert_years(0, 0, 7)


class UnstampedRowsTests(AnimeCalcBase):
    """Rows with no change reason: anime_tracking's, and older ones."""

    def clear_reasons(self, row):
        Anime.history.filter(id=row.pk).update(history_change_reason=None)

    def test_in_progress_reads_its_moment_on_the_owners_calendar(self):
        item = self.anime()
        row = self.track(item, d(2026, 2, 1))
        self.type_number(row, 3, d(2026, 2, 2))
        self.clear_reasons(row)
        self.assert_years(0, 3, 3)
        self.assertEqual(self.lines(), ["2026-02-02 watched 3", "2026-02-01 started"])

    def test_completed_goes_to_the_pile(self):
        item = self.anime()
        row = self.track(item, d(2026, 2, 1), progress=12, status=Status.COMPLETED.value)
        self.clear_reasons(row)
        self.assert_years(0, 0, 12)

    def test_a_first_row_far_from_the_tracking_rows_creation_came_from_a_file(self):
        item = self.anime()
        row = self.track(item, d(2026, 2, 1), progress=5)
        self.clear_reasons(row)
        Anime.history.filter(id=row.pk).update(history_date=datetime(2024, 5, 1, tzinfo=ZoneInfo(NEW_YORK)))
        self.assert_years(0, 0, 5)


class AnimeTrackingWritesTests(AnimeTestCase):
    """Anime's own writes (app/anime_tracking.py) stamp no reason on its history rows.

    The adapter reads them at their moments and is wrong for anime until it reads
    viewings, but neither it nor the comparison may fail on them.
    """

    def test_the_adapter_and_the_comparison_read_every_kind_of_write(self):
        watched, marked, logged, removed, imported = (self.title(total=12) for _ in range(5))
        self.tick(watched, 1, 2, 3)
        anime_tracking.untick(self.user, watched, 3)
        anime_tracking.mark_watched(self.user, watched)
        anime_tracking.start_rewatch(self.user, watched)
        self.tick(watched, 1)
        anime_tracking.mark_watched(self.user, marked)
        self.log(logged)
        self.tick(removed, 1)
        anime_tracking.remove(self.user, removed, mutation_id=str(uuid4()))
        anime_tracking.import_title(self.user, imported, status=Status.IN_PROGRESS.value, count=5)

        calculation = rows_for([self.user], self.user, types=[ANIME])
        self.assertEqual(
            {row.title_id for row in calculation.rows} | piled(calculation.piles, ANIME).titles,
            {watched.pk, marked.pk, logged.pk, imported.pk},
        )
        printed = io.StringIO()
        call_command("compare_activity_numbers", user=self.user.pk, stdout=printed)
        self.assertNotIn("failed", printed.getvalue())
        self.assertIn(f"user {self.user.pk} anime all:", printed.getvalue())
