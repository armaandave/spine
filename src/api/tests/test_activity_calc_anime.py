"""The one calculation on anime, switched off until Phase 2.

Anime's history rows are its saves, and each says what it was (AC-2). Amount is episodes
times the live runtime. The provider is faked: every anime save reaches it.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import override_settings

from api.services import tracking as tracking_service
from api.services.activity_rows import list_rows, piled, rows_for
from api.tests.test_activity_calc_books import BookCalcBase, d
from app.models import Anime, DiaryEntry, MediaTypes, Status
from app.services import create_diary_entry

ANIME = MediaTypes.ANIME.value
NEW_YORK = "America/New_York"
RUNTIME = 24
Y2025 = {"start": d(2025, 1, 1), "end": d(2025, 12, 31)}
Y2026 = {"start": d(2026, 1, 1), "end": d(2026, 12, 31)}


@override_settings(TIME_ZONE=NEW_YORK)
class AnimeCalcBase(BookCalcBase):
    """Save through the real functions, on a 12-episode anime at 24 minutes."""

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

    def log_it(self, item, day, saved_on=None, **kwargs):
        moment = datetime(day.year, day.month, day.day, tzinfo=UTC)
        with self.on(saved_on or day):
            return create_diary_entry(self.owner, item, consumed_at=moment, auto_mark_consumed=True, **kwargs)

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
    """A typed number counts on its day; a drop comes off the newest."""

    def test_typed_numbers_count_on_their_days_with_their_minutes(self):
        item = self.anime()
        row = self.track(item, d(2025, 12, 1))
        self.type_number(row, 3, d(2025, 12, 20))
        self.type_number(row, 5, d(2026, 1, 10))
        self.assert_years(3, 2, 5)
        self.assertEqual(self.lines(), ["2026-01-10 watched 2", "2025-12-20 watched 3", "2025-12-01 started"])
        told = next(row for row in self.calc().rows if row.day == d(2026, 1, 10))
        self.assertEqual((int(told.told), int(told.amount), told.told_unit), (2, 2 * RUNTIME, "episode"))

    def test_30_300_31_32_reads_32(self):
        item = self.anime()
        row = self.track(item, d(2026, 2, 1))
        for value, day in ((30, 2), (300, 3), (31, 4), (32, 5)):
            self.type_number(row, value, d(2026, 2, day))
        self.assert_years(0, 32, 32)

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


class LogTests(AnimeCalcBase):
    """A log adds what's left, on its date."""

    def test_logged_whole_on_feb_1(self):
        item = self.anime()
        self.log_it(item, d(2026, 2, 1))
        self.assert_years(0, 12, 12)
        self.assertEqual(self.lines(), ["2026-02-01 log +12"])

    def test_logged_again(self):
        item = self.anime()
        self.log_it(item, d(2026, 2, 1))
        self.log_it(item, d(2026, 3, 1), is_rewatch=True)
        self.assert_years(0, 24, 24)

    def test_5_of_12_then_a_phone_shaped_log(self):
        item = self.anime()
        row = self.track(item, d(2026, 2, 1))
        self.type_number(row, 5, d(2026, 2, 2))
        self.log_it(item, d(2026, 2, 3))
        self.assert_years(0, 12, 12)
        self.assertEqual(self.lines()[0], "2026-02-03 log +7")

    def test_started_dec_1_3_episodes_jan_10_log_dec_20(self):
        """The start is first activity, so the 3 stay on January 10."""
        item = self.anime()
        row = self.track(item, d(2025, 12, 1))
        self.type_number(row, 3, d(2026, 1, 10))
        self.log_it(item, d(2025, 12, 20), saved_on=d(2026, 1, 12))
        self.assert_years(9, 3, 12)

    def test_3_episodes_then_a_log_dated_before_the_start_moves_them(self):
        item = self.anime()
        row = self.track(item, d(2026, 3, 1))
        self.type_number(row, 3, d(2026, 3, 10))
        self.log_it(item, d(2025, 12, 20), saved_on=d(2026, 3, 12))
        self.assert_years(12, 0, 12)
        self.assertEqual(self.lines(), ["2025-12-20 log +12"])

    def test_a_log_with_no_tracking_row_adds_nothing_and_is_unknown(self):
        """A removed row's history names no title, and a log holds no number of its own."""
        item = self.anime()
        row = self.track(item, d(2026, 2, 1), progress=12, status=Status.COMPLETED.value)
        entry = self.log_it(item, d(2026, 2, 2))
        with self.on(d(2026, 3, 1)):
            row.delete()
        self.assertTrue(DiaryEntry.objects.filter(pk=entry.pk).exists())
        self.assert_years(0, 0, 0)
        logged = self.calc().rows[0]
        self.assertEqual((logged.log_id, logged.known), (entry.pk, False))

    def test_a_private_log_adds_nothing_for_a_stranger(self):
        item = self.anime()
        row = self.track(item, d(2026, 2, 1))
        self.type_number(row, 5, d(2026, 2, 2))
        self.log_it(item, d(2026, 2, 3), visibility="private")
        self.assert_years(0, 12, 12)
        self.assert_years(0, 5, 5, viewer=self.stranger)


class MarkTests(AnimeCalcBase):
    """Marks land in the pile and never make a row (decision 11)."""

    def test_the_eye_through_the_api_holds_its_length_with_no_row(self):
        item = self.anime()
        with self.on(d(2026, 3, 1)):
            tracking_service.consume_media(self.owner, source=item.source, media_type=ANIME, media_id=item.media_id)
        self.assert_years(0, 0, 12)
        self.assertEqual(self.lines(), [])
        pile = piled(self.calc().piles, ANIME)
        self.assertEqual(int(pile.told), 12)

    def test_marked_then_logged_holds_both(self):
        item = self.anime()
        with self.on(d(2026, 3, 1)):
            tracking_service.consume_media(self.owner, source=item.source, media_type=ANIME, media_id=item.media_id)
        self.log_it(item, d(2026, 3, 2))
        self.assert_years(0, 12, 24)

    def test_a_completed_anime_with_no_length_is_a_pile_title_with_amount_0(self):
        item = self.anime(runtime=None)
        with self.on(d(2026, 3, 1)):
            tracking_service.consume_media(self.owner, source=item.source, media_type=ANIME, media_id=item.media_id)
        pile = piled(self.calc().piles, ANIME)
        self.assertEqual((int(pile.amount), pile.titles, pile.known), (0, {item.pk}, set()))

    def test_a_tracking_row_with_progress_and_no_history_sits_in_the_pile(self):
        item = self.anime()
        Anime.objects.bulk_create([Anime(user=self.owner, item=item, status=Status.IN_PROGRESS.value, progress=7)])
        self.assert_years(0, 0, 7)


class UnstampedRowsTests(AnimeCalcBase):
    """Rows from before the stamps, or written during a rollback, have an empty reason."""

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

    def test_completed_within_5_seconds_after_a_log_is_that_logs(self):
        item = self.anime()
        entry = self.log_it(item, d(2026, 2, 1))
        row = Anime.objects.get(user=self.owner, item=item)
        self.clear_reasons(row)
        Anime.history.filter(id=row.pk).update(history_date=entry.created_at)
        self.assert_years(0, 12, 12)
        self.assertEqual(self.lines(), ["2026-02-01 log +12"])

    def test_a_first_row_far_from_the_tracking_rows_creation_came_from_a_file(self):
        item = self.anime()
        row = self.track(item, d(2026, 2, 1), progress=5)
        self.clear_reasons(row)
        Anime.history.filter(id=row.pk).update(history_date=datetime(2024, 5, 1, tzinfo=ZoneInfo(NEW_YORK)))
        self.assert_years(0, 0, 5)

    def test_each_tracking_row_is_its_own_read(self):
        item = self.anime()
        first = self.track(item, d(2025, 2, 1))
        self.type_number(first, 12, d(2025, 2, 2))
        second = self.track(item, d(2026, 2, 1))
        self.type_number(second, 4, d(2026, 2, 2))
        self.assert_years(12, 4, 16)
