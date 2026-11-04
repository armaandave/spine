"""The one calculation on manga and comics, switched off until Phase 2.

docs/manga-comics-tracking-contract.md, MC-007 and MC-200 to MC-202, with the same
stories the branch's probe told, on merged code. A New York server and a pinned clock.
"""

from datetime import UTC, datetime
from decimal import Decimal

from api.services.activity_rows import list_rows, piled, rows_for
from api.tests.test_activity_calc_books import BookCalcBase, d
from app import series_tracking as series
from app.models import Comic, DiaryEntry, Manga, MediaTypes, Status
from social.models import Follow, FollowStatus

MANGA = MediaTypes.MANGA.value
COMIC = MediaTypes.COMIC.value
Y2024 = {"start": d(2024, 1, 1), "end": d(2024, 12, 31)}
Y2025 = {"start": d(2025, 1, 1), "end": d(2025, 12, 31)}
Y2026 = {"start": d(2026, 1, 1), "end": d(2026, 12, 31)}


class SeriesCalcBase(BookCalcBase):
    """Save through the real functions, and read a series' rows."""

    media_type = MANGA

    def title(self, name, total=None, media_type=None):
        item = self.item(media_type or self.media_type, name, title=name)
        self.set_item(item, series_total=total)
        return item

    def do(self, function, item, day, *args, **kwargs):
        with self.on(day):
            return function(self.owner, item, *args, **kwargs)

    def begin(self, item, day):
        return self.do(series.start, item, day)

    def save(self, item, value, day):
        return self.do(series.update_progress, item, day, value=value)

    def finish(self, item, day, saved_on=None, **kwargs):
        with self.on(saved_on or day):
            return series.complete(self.owner, item, completion_date=day, **kwargs)

    def calc(self, viewer=None):
        return rows_for([self.owner], viewer or self.owner, types=[self.media_type])

    def number(self, start=None, end=None, *, pile=False, viewer=None):
        calculation = self.calc(viewer)
        rows = list_rows(calculation.rows, media_type=self.media_type, start_date=start, end_date=end)
        total = sum((row.amount for row in rows), Decimal(0))
        return int(total + (piled(calculation.piles, self.media_type).amount if pile else 0))

    def assert_years(self, in_2025, in_2026, all_time, viewer=None):
        self.assertEqual(
            (
                self.number(**Y2025, viewer=viewer),
                self.number(**Y2026, viewer=viewer),
                self.number(pile=True, viewer=viewer),
            ),
            (in_2025, in_2026, all_time),
        )

    def lines(self, viewer=None):
        """Return the list as readable lines, newest first."""
        lines = []
        for row in list_rows(self.calc(viewer).rows, media_type=self.media_type):
            parts = []
            if row.log_id:
                parts.append(f"log +{int(row.amount)}" if row.amount else "log")
            elif row.amount:
                parts.append(f"read {int(row.amount)}")
            elif row.started:
                parts.append("started")
            lines.append(f"{row.day} " + ", ".join(parts + row.moves))
        return lines


class EverySaveTests(SeriesCalcBase):
    """MC-007."""

    def test_a_first_save_and_a_typo_with_no_total(self):
        item = self.title("a")
        for value, day in ((30, 20), (300, 21), (31, 22), (32, 23)):
            self.save(item, value, d(2026, 10, day))
        self.assert_years(0, 32, 32)
        amounts = sorted(int(row.amount) for row in self.calc().rows if row.amount)
        self.assertEqual(amounts, [1, 1, 30])

    def test_a_log_adds_what_is_left(self):
        item = self.title("c", total=100)
        self.save(item, 30, d(2026, 3, 2))
        self.finish(item, d(2026, 5, 1))
        self.assert_years(0, 100, 100)
        self.assertEqual(self.lines()[0], "2026-05-01 log +70")

    def test_a_direct_log_dated_2024(self):
        item = self.title("d", total=100)
        self.finish(item, d(2024, 6, 1), saved_on=d(2026, 10, 20))
        self.assertEqual(self.number(**Y2024), 100)
        self.assertEqual(self.number(pile=True), 100)

    def test_logged_then_reread_then_logged(self):
        item = self.title("e", total=100)
        self.finish(item, d(2025, 3, 1))
        self.do(series.reread, item, d(2026, 2, 1))
        self.assert_years(100, 0, 100)
        self.save(item, 25, d(2026, 2, 2))
        self.assert_years(100, 25, 125)
        self.finish(item, d(2026, 3, 1))
        self.assert_years(100, 100, 200)

    def test_a_second_log_on_a_finished_series_is_a_new_read(self):
        item = self.title("e2", total=100)
        self.finish(item, d(2025, 3, 1))
        self.finish(item, d(2026, 3, 1))
        self.assert_years(100, 100, 200)

    def test_a_number_below_the_position_comes_off_the_newest(self):
        item = self.title("drop")
        self.save(item, 100, d(2025, 12, 20))
        self.save(item, 300, d(2026, 1, 15))
        self.save(item, 30, d(2026, 2, 1))
        self.assert_years(30, 0, 30)

    def test_a_typed_lower_number_at_a_log_fixes_a_typo(self):
        item = self.title("log-lower")
        self.save(item, 300, d(2026, 1, 15))
        self.finish(item, d(2026, 2, 1), place=30)
        self.assert_years(0, 30, 30)

    def test_a_status_row_never_wipes_a_read(self):
        item = self.title("paused")
        self.save(item, 40, d(2026, 2, 1))
        self.do(series.pause, item, d(2026, 2, 2))
        self.do(series.start, item, d(2026, 2, 3))
        self.save(item, 50, d(2026, 2, 4))
        self.assert_years(0, 50, 50)
        self.assertIn("2026-02-03 picked back up", self.lines())

    def test_attempt_ids_overlap_across_the_two_types(self):
        manga = self.title("same", media_type=MANGA)
        comic = self.title("same", media_type=COMIC)
        self.save(manga, 5, d(2026, 2, 1))
        self.save(comic, 7, d(2026, 2, 1))
        self.assertEqual(
            Manga.objects.get(item=manga).current_attempt_id,
            Comic.objects.get(item=comic).current_attempt_id,
        )
        self.assertEqual(self.number(pile=True), 5)
        self.media_type = COMIC
        self.assertEqual(self.number(pile=True), 7)

    def test_a_deleted_attempt_counts_on_no_day(self):
        item = self.title("deleted")
        self.save(item, 40, d(2026, 2, 1))
        self.do(series.delete_journey, item, d(2026, 2, 2), Manga.objects.get(item=item).current_attempt_id)
        self.assert_years(0, 0, 0)


class ContinuingTests(SeriesCalcBase):
    """MC-200 to MC-202: only Reread and Restart begin at zero."""

    def test_keep_reading_adds_only_the_rise(self):
        item = self.title("keep")
        self.save(item, 120, d(2025, 3, 1))
        self.finish(item, d(2025, 3, 2))
        self.do(series.keep_reading, item, d(2026, 2, 1))
        self.assertEqual(self.lines()[0], "2025-03-02 log")
        self.save(item, 150, d(2026, 2, 2))
        self.finish(item, d(2026, 2, 3))
        self.assert_years(120, 30, 150)

    def test_a_comic_at_12_logged_at_15_is_15(self):
        self.media_type = COMIC
        item = self.title("comic", total=12)
        self.save(item, 12, d(2025, 3, 1))
        self.finish(item, d(2025, 3, 2))
        self.set_item(item, series_total=15)
        with self.on(d(2026, 2, 1)):
            series.new_issue_out(item, start_date=d(2026, 2, 1))
        self.save(item, 15, d(2026, 2, 2))
        self.finish(item, d(2026, 2, 3))
        self.assert_years(12, 3, 15)

    def test_picked_up_after_did_not_finish_continues(self):
        item = self.title("stop", total=100)
        self.save(item, 30, d(2025, 6, 2))
        self.do(series.drop, item, d(2025, 6, 3))
        self.do(series.start, item, d(2026, 2, 1))
        self.save(item, 40, d(2026, 2, 2))
        self.assert_years(30, 10, 40)
        self.finish(item, d(2026, 2, 3))
        self.assert_years(30, 70, 100)
        self.assertIn("2026-02-01 picked back up", self.lines())

    def test_a_direct_log_after_did_not_finish_continues_the_stopped_read(self):
        item = self.title("stop-log", total=100)
        self.save(item, 30, d(2025, 6, 2))
        self.do(series.drop, item, d(2025, 6, 3))
        self.finish(item, d(2026, 2, 3))
        self.assert_years(30, 70, 100)

    def test_a_direct_log_on_a_finished_series_counts_whole(self):
        item = self.title("again", total=100)
        self.save(item, 100, d(2025, 6, 2))
        self.finish(item, d(2025, 6, 3))
        self.finish(item, d(2026, 2, 3))
        self.assert_years(100, 100, 200)

    def test_restart_begins_at_zero(self):
        item = self.title("restart", total=100)
        self.save(item, 30, d(2026, 2, 1))
        self.do(series.restart, item, d(2026, 2, 2))
        self.save(item, 10, d(2026, 2, 3))
        self.assert_years(0, 40, 40)


class MarkTests(SeriesCalcBase):
    """Marks hold a number once, and never make a row (decision 11)."""

    def test_the_eye_holds_the_total_with_no_row(self):
        item = self.title("eye", total=100)
        self.do(series.mark_read, item, d(2026, 3, 1))
        self.assert_years(0, 0, 100)
        self.assertEqual(self.lines(), [])

    def test_stars_and_a_heart_too(self):
        stars = self.title("stars", total=10)
        heart = self.title("heart", total=20)
        self.do(series.set_rating, stars, d(2026, 3, 1), 8)
        self.do(series.set_like, heart, d(2026, 3, 1), liked=True)
        self.assert_years(0, 0, 30)
        self.do(series.set_like, heart, d(2026, 3, 2), liked=False)
        self.assert_years(0, 0, 30)
        self.assertEqual(self.lines(), [])

    def test_the_eye_then_a_log_holds_both(self):
        item = self.title("eye-log", total=100)
        self.do(series.mark_read, item, d(2026, 3, 1))
        self.finish(item, d(2026, 4, 1))
        self.assert_years(0, 100, 200)

    def test_the_eye_then_a_reread_holds_both(self):
        item = self.title("eye-reread", total=100)
        self.do(series.mark_read, item, d(2026, 3, 1))
        self.do(series.reread, item, d(2026, 3, 2))
        self.save(item, 100, d(2026, 3, 3))
        self.assert_years(0, 100, 200)

    def test_stopped_at_30_of_100_then_the_eye_is_100_not_130(self):
        item = self.title("stop-eye", total=100)
        self.save(item, 30, d(2025, 6, 2))
        self.do(series.drop, item, d(2025, 6, 3))
        self.do(series.mark_read, item, d(2026, 2, 1))
        self.assert_years(30, 0, 100)

    def test_a_comic_marked_at_10_then_an_11th_typed_is_11(self):
        self.media_type = COMIC
        item = self.title("marked", total=10)
        self.do(series.mark_read, item, d(2026, 2, 1))
        self.set_item(item, series_total=11)
        with self.on(d(2026, 3, 1)):
            series.new_issue_out(item, start_date=d(2026, 3, 1), previous_total=10)
        self.save(item, 11, d(2026, 3, 2))
        self.assert_years(0, 1, 11)
        self.assertEqual(int(piled(self.calc().piles, COMIC).amount), 10)
        self.assertEqual(self.lines(), ["2026-03-02 read 1"])

    def test_stopped_at_30_of_100_the_eye_a_new_issue_then_101_is_101(self):
        self.media_type = COMIC
        item = self.title("all-of-it", total=100)
        self.save(item, 30, d(2025, 6, 2))
        self.do(series.drop, item, d(2025, 6, 3))
        self.do(series.mark_read, item, d(2026, 2, 1))
        self.set_item(item, series_total=101)
        with self.on(d(2026, 3, 1)):
            series.new_issue_out(item, start_date=d(2026, 3, 1), previous_total=100)
        self.save(item, 101, d(2026, 3, 2))
        self.assert_years(30, 1, 101)

    def test_a_marked_series_with_no_total_is_unknown(self):
        item = self.title("no-total")
        self.do(series.mark_read, item, d(2026, 3, 1))
        pile = piled(self.calc().piles, MANGA)
        self.assertEqual((pile.titles, pile.known), ({item.pk}, set()))


class ImportTests(SeriesCalcBase):
    """MC-400: an imported number has no day, unless a finished read has a date."""

    def imported(self, item, **kwargs):
        with self.on(d(2026, 10, 1)):
            return series.import_title(self.owner, item, **kwargs)

    def test_an_imported_read_at_77_stays_77_through_keep_reading_then_80(self):
        item = self.title("seventy-seven", total=100)
        self.imported(item, status=Status.COMPLETED.value, place=77)
        self.assert_years(0, 0, 77)
        self.do(series.keep_reading, item, d(2026, 10, 2))
        self.assert_years(0, 0, 77)
        self.save(item, 80, d(2026, 10, 3))
        self.assert_years(0, 3, 80)
        self.assertEqual(int(piled(self.calc().piles, MANGA).amount), 77)

    def test_read_3_times_with_one_finish_date(self):
        item = self.title("three", total=380)
        earlier = [{"status": Status.COMPLETED.value, "place": 380}, {"status": Status.COMPLETED.value, "place": 380}]
        self.imported(item, status=Status.COMPLETED.value, place=380, end_date=d(2024, 5, 1), earlier=earlier)
        self.assertEqual(self.number(**Y2024), 380)
        self.assertEqual(self.number(pile=True), 1140)

    def test_a_place_then_a_typed_number_adds_only_the_rise(self):
        item = self.title("place")
        self.imported(item, status=Status.IN_PROGRESS.value, place=120)
        self.assert_years(0, 0, 120)
        self.save(item, 125, d(2026, 10, 2))
        self.assert_years(0, 5, 125)

    def test_a_paused_place_with_no_attempt_sits_in_the_pile(self):
        item = self.title("paused-place")
        self.imported(item, status=Status.PAUSED.value, place=40)
        self.assert_years(0, 0, 40)
        self.assertEqual(self.lines(), [])

    def test_a_hand_made_log_with_no_attempt_counts_whole(self):
        """Overwrite imports keep hand-made logs while their attempts go."""
        item = self.title("kept", total=50)
        DiaryEntry.objects.create(user=self.owner, item=item, consumed_at=datetime(2025, 3, 1, tzinfo=UTC))
        self.assert_years(50, 0, 50)
        self.assertEqual(self.lines(), ["2025-03-01 log +50"])


class PrivateLogTests(SeriesCalcBase):
    """AC-5: a log the viewer can't see adds no amount and moves nothing."""

    def setUp(self):
        super().setUp()
        self.media_type = COMIC

    def test_5_of_12_a_private_log_a_new_issue_then_13(self):
        item = self.title("private", total=12)
        self.save(item, 5, d(2026, 2, 1))
        self.finish(item, d(2026, 2, 2), visibility="private")
        self.set_item(item, series_total=13)
        with self.on(d(2026, 3, 1)):
            series.new_issue_out(item, start_date=d(2026, 3, 1))
        self.save(item, 13, d(2026, 3, 2))
        self.assertEqual(self.number(pile=True), 13)
        self.assertEqual(self.number(pile=True, viewer=self.stranger), 6)
        self.assertNotIn("2026-02-02 log +7", self.lines(viewer=self.stranger))

    def test_a_followers_log_shows_a_follower(self):
        item = self.title("followers", total=12)
        self.finish(item, d(2026, 2, 2), visibility="followers")
        self.assertTrue(Follow.objects.filter(from_user=self.follower, status=FollowStatus.ACCEPTED).exists())
        self.assertEqual(self.number(pile=True, viewer=self.follower), 12)
        self.assertEqual(self.number(pile=True, viewer=self.stranger), 0)
