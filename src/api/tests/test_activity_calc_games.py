"""The one calculation on games, switched off: the comparison reads it until Phase 2.

Each playthrough is its own total (GM-006, GM-102). Rows come from real tracking
functions under a pinned clock on a New York server.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

from rest_framework.test import APIClient

from api.services.activity_rows import list_rows, piled, rows_for
from api.tests.test_activity_calc_books import BookCalcBase, d
from api.tests.test_stats_v2_activity import YEAR_2026, time_row
from api.tests.test_stats_v2_shape import ALL_TIME
from app import game_tracking
from app.models import DiaryEntry, Game, GameSession, MediaTypes, Status
from integrations import exports
from integrations.imports import yamtrack
from social.models import ProgressChange

GAME = MediaTypes.GAME.value


def h(hours):
    """Return hours in minutes."""
    return hours * 60


class GameCalcBase(BookCalcBase):
    """Save through the real functions, and read a game's rows."""

    def game(self, name="game"):
        return self.item(GAME, name, title=name)

    def begin(self, item, day, **kwargs):
        with self.on(day, **kwargs):
            return game_tracking.start(self.owner, item)

    def save(self, item, minutes, day, **kwargs):
        with self.on(day, **kwargs):
            return game_tracking.update_progress(self.owner, item, total_minutes=minutes)

    def percent(self, item, value, day):
        with self.on(day):
            return game_tracking.update_progress(self.owner, item, percentage=value)

    def finish(self, item, day, saved_on=None, **kwargs):
        with self.on(saved_on or day):
            return game_tracking.complete(self.owner, item, completion_date=day, **kwargs)

    def calc(self, **kwargs):
        return rows_for([self.owner], self.owner, types=[GAME], **kwargs)

    def number(self, *, start=None, end=None, pile=False):
        calculation = self.calc()
        rows = list_rows(calculation.rows, media_type=GAME, start_date=start, end_date=end)
        total = sum((row.amount for row in rows), Decimal(0))
        return int(total + (piled(calculation.piles, GAME).amount if pile else 0))

    def lines(self, item=None):
        """Return the list as readable lines, newest first."""
        lines = []
        for row in list_rows(self.calc().rows, media_type=GAME):
            if item is not None and row.title_id != item.pk:
                continue
            parts = []
            if row.log_id:
                parts.append(f"log +{int(row.amount)}" if row.amount else "log")
            elif row.amount:
                parts.append(f"played {int(row.amount)}")
            elif row.told:
                parts.append(f"progressed {row.told.normalize():f}%")
            elif row.started:
                parts.append("started")
            lines.append(f"{row.day} " + ", ".join(parts + row.moves))
        return lines


class GameStoriesTests(GameCalcBase):
    """The plan's table of games."""

    def test_10h_in_december_12h_in_march(self):
        item = self.game()
        self.begin(item, d(2025, 12, 1))
        self.save(item, h(10), d(2025, 12, 20))
        self.save(item, h(12), d(2026, 3, 1))
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)), h(10))
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), h(2))
        self.assertEqual(self.number(pile=True), h(12))

    def test_retyping_40_adds_no_january_row(self):
        item = self.game()
        self.begin(item, d(2025, 12, 1))
        self.save(item, h(40), d(2025, 12, 20))
        self.save(item, h(40), d(2026, 1, 2))
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), 0)
        self.assertEqual(self.number(pile=True), h(40))
        self.assertEqual(self.lines(), ["2025-12-20 played 2400", "2025-12-01 started"])

    def test_a_touch_row_moves_the_day_up_and_adds_nothing(self):
        item = self.game()
        self.begin(item, d(2025, 12, 1))
        self.save(item, h(40), d(2025, 12, 20))
        session = GameSession.objects.get(related_game__item=item)
        shape = {"kind": "touch", "unit": "touch", "value": 0, "max": None, "playthrough_id": session.pk}
        ProgressChange.objects.create(
            actor=self.owner,
            item=item,
            previous_progress=shape,
            current_progress=shape,
            saved_on=d(2026, 1, 2),
        )
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), 0)
        self.assertEqual(self.number(pile=True), h(40))

    def test_10h_in_december_finish_on_jan_5_typing_12(self):
        item = self.game()
        self.begin(item, d(2025, 12, 1))
        self.save(item, h(10), d(2025, 12, 20))
        self.finish(item, d(2026, 1, 5), total_minutes=h(12))
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)), h(10))
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), h(2))
        self.assertEqual(self.lines()[0], "2026-01-05 log +120")

    def test_stopped_at_5h_then_a_new_attempt_to_3h(self):
        """Decision 44: a new playthrough is its own total, and says started playing."""
        item = self.game()
        self.begin(item, d(2025, 6, 1))
        self.save(item, h(5), d(2025, 6, 2))
        with self.on(d(2025, 6, 3)):
            game_tracking.drop(self.owner, item)
        self.begin(item, d(2026, 2, 1))
        self.save(item, h(3), d(2026, 2, 2))
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)), h(5))
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), h(3))
        self.assertEqual(self.number(pile=True), h(8))
        self.assertIn("2026-02-01 started", self.lines())
        self.assertIn("2025-06-03 stopped", self.lines())

    def test_40_percent_only_adds_no_hours_and_is_told(self):
        item = self.game()
        self.begin(item, d(2026, 2, 1))
        self.percent(item, 40, d(2026, 2, 2))
        self.assertEqual(self.number(pile=True), 0)
        self.assertEqual(self.lines()[0], "2026-02-02 progressed 40%")
        row = next(row for row in self.calc().rows if row.told)
        self.assertEqual((row.counts_title, row.known, row.told_unit), (True, False, "percent"))

    def test_playtime_and_a_percent_on_one_day_tell_the_playtime(self):
        """AC-2 either way: the day's hours are told, so it's known (decision 20)."""
        hours_first = self.game("hours-first")
        self.begin(hours_first, d(2026, 2, 1))
        self.save(hours_first, h(5), d(2026, 2, 2))
        self.percent(hours_first, 40, d(2026, 2, 2))
        percent_first = self.game("percent-first")
        self.begin(percent_first, d(2026, 2, 1))
        self.percent(percent_first, 40, d(2026, 2, 2))
        self.save(percent_first, h(5), d(2026, 2, 2))
        for item in (hours_first, percent_first):
            with self.subTest(item=item.title):
                row = next(row for row in self.calc().rows if row.title_id == item.pk and row.told)
                self.assertEqual((row.told, row.told_unit, row.amount, row.known), (h(5), "minute", h(5), True))

    def test_a_log_with_hours_and_a_percent_is_known(self):
        """GM-310's 40 hours and 65 percent: the hours are its length."""
        item = self.game("both")
        self.begin(item, d(2026, 2, 1))
        _, entry = self.finish(item, d(2026, 2, 2), total_minutes=h(40), percentage=65)
        row = next(row for row in self.calc().rows if row.log_id == entry.pk)
        self.assertEqual((row.amount, row.known), (h(40), True))

    def test_a_percent_on_a_day_with_no_hours_stays_unknown(self):
        """That day's play has no hours, so the game's hours aren't all known."""
        item = self.game("next-day")
        self.begin(item, d(2026, 2, 1))
        self.save(item, h(3), d(2026, 2, 2))
        self.percent(item, 20, d(2026, 2, 3))
        row = next(row for row in self.calc().rows if row.day == d(2026, 2, 3))
        self.assertEqual((row.told_unit, row.known), ("percent", False))

    def test_the_sheet_saving_hours_and_a_percent_together_counts_every_hour(self):
        """Edit Playthrough sends both fields; the number says no note under 3 hours."""
        item = self.game("together")
        self.begin(item, d(2026, 2, 1))
        playthrough = GameSession.objects.get(related_game__item=item)
        client = APIClient()
        client.force_authenticate(self.owner)
        with self.on(d(2026, 2, 2)):
            response = client.patch(
                f"/api/v1/tracking/{item.source}/game/{item.media_id}/playthroughs/{playthrough.pk}/",
                {"total_minutes": h(3), "percentage": 20, "progressed_on": "2026-02-02"},
                format="json",
            )
        self.assertEqual(response.status_code, 200, response.content)
        with self.on(d(2026, 11, 5)):
            row = time_row(self.summary(YEAR_2026, tab=GAME), GAME)
        self.assertEqual((row["value"], row["known"], row["total"], row["approximate"]), (h(3), 1, 1, False))

    def test_steams_lifetime_never_adds(self):
        item = self.game()
        Game.objects.bulk_create(
            [Game(user=self.owner, item=item, status=Status.PLANNING.value, imported_lifetime_minutes=h(300))],
        )
        self.assertEqual(self.number(pile=True), 0)
        self.assertEqual(self.lines(), [])

    def test_50h_in_feb_with_a_log_dated_dec_saying_40h(self):
        """Decision 10 with GM-222: the row says 50, and the log still says 40."""
        item = self.game()
        self.begin(item, d(2026, 2, 1))
        self.save(item, h(50), d(2026, 2, 2))
        _, entry = self.finish(item, d(2026, 3, 1), total_minutes=h(40))
        # A date Phase 2's writes can carry: the save stays on the playthrough (GM-222).
        DiaryEntry.objects.filter(pk=entry.pk).update(consumed_at=datetime(2025, 12, 20, tzinfo=UTC))
        GameSession.objects.filter(related_game__item=item).update(total_minutes=h(50))
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)), h(50))
        self.assertEqual(self.number(start=d(2026, 1, 1), end=d(2026, 12, 31)), 0)
        self.assertEqual(self.lines(), ["2025-12-20 log +3000"])

    def test_a_restart_is_a_real_start_and_its_own_total(self):
        item = self.game()
        self.begin(item, d(2026, 2, 1))
        self.save(item, h(5), d(2026, 2, 2))
        with self.on(d(2026, 2, 3)):
            game_tracking.restart(self.owner, item, mutation_id=uuid4())
        self.save(item, h(1), d(2026, 2, 4))
        self.assertEqual(self.number(pile=True), h(6))


class GapTests(GameCalcBase):
    """What a playthrough's total says that its saves don't."""

    def session(self, game, **fields):
        fields.setdefault("status", Status.COMPLETED.value)
        fields.setdefault("origin", GameSession.Origin.DIRECT_LOG)
        rows = GameSession.objects.bulk_create([GameSession(related_game=game, **fields)])
        return rows[0]

    def tracked(self, name="gap"):
        item = self.game(name)
        Game.objects.bulk_create([Game(user=self.owner, item=item, status=Status.COMPLETED.value)])
        return item, Game.objects.get(user=self.owner, item=item)

    def test_a_gap_lands_on_minutes_updated_on_else_end_date(self):
        """test_stats_v2_activity's 720 stays 720."""
        _item, game = self.tracked()
        self.session(game, total_minutes=600, minutes_updated_on=date(2025, 2, 1), end_date=date(2024, 12, 1))
        self.session(game, total_minutes=120, end_date=date(2025, 5, 1))
        self.session(game, total_minutes=None, end_date=date(2025, 6, 1))
        self.session(game, total_minutes=60, end_date=date(2024, 6, 1))
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)), 720)
        self.assertEqual(self.number(pile=True), 780)

    def test_a_gap_with_no_day_sits_in_the_pile(self):
        _item, game = self.tracked()
        self.session(game, total_minutes=90)
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2027, 12, 31)), 0)
        self.assertEqual(self.number(pile=True), 90)

    def test_hours_with_no_playthrough_from_an_undated_import_sit_in_the_pile(self):
        item = self.game("imported")
        Game.objects.bulk_create([Game(user=self.owner, item=item, status=Status.COMPLETED.value, progress=h(3))])
        self.assertEqual(self.number(pile=True), h(3))
        self.assertEqual(self.lines(), [])

    def test_a_marked_game_is_a_title_outside_known_and_total(self):
        item = self.game("eye")
        with self.on(d(2026, 2, 1)):
            game_tracking.mark_completed(self.owner, item)
        pile = piled(self.calc().piles, GAME)
        self.assertEqual(int(pile.amount), 0)
        self.assertEqual(pile.titles, {item.pk})
        self.assertEqual(pile.no_amount, {item.pk})
        self.assertEqual(self.lines(), [])

    def test_a_row_a_log_wrote_lands_on_the_logs_date_while_it_exists(self):
        """GM-219: Phase 2 writes these. Its delete leaves the row on its own day."""
        item = self.game("linked")
        self.begin(item, d(2026, 2, 1))
        self.save(item, h(5), d(2026, 2, 2))
        _, entry = self.finish(item, d(2026, 2, 3))
        session = GameSession.objects.get(related_game__item=item)
        shape = {"kind": "minutes", "unit": "minute", "max": None, "playthrough_id": session.pk, "log_id": entry.pk}
        ProgressChange.objects.create(
            actor=self.owner,
            item=item,
            previous_progress={**shape, "value": h(5)},
            current_progress={**shape, "value": h(7)},
            saved_on=d(2026, 2, 3),
        )
        GameSession.objects.filter(pk=session.pk).update(total_minutes=h(7))
        self.assertEqual(self.lines()[0], "2026-02-03 log +120")
        DiaryEntry.objects.filter(pk=entry.pk).delete()
        self.assertEqual(self.lines()[0], "2026-02-03 played 120")

    def test_many_people_keep_their_own_games(self):
        item = self.game("shared")
        with self.on(d(2026, 2, 1)):
            game_tracking.start(self.owner, item)
            game_tracking.update_progress(self.owner, item, total_minutes=60)
            game_tracking.start(self.follower, item)
            game_tracking.update_progress(self.follower, item, total_minutes=30)
        rows = rows_for([self.owner, self.follower], self.owner, types=[GAME]).rows
        amounts = {row.actor_id: int(row.amount) for row in rows if row.amount}
        self.assertEqual(amounts, {self.owner.pk: 60, self.follower.pk: 30})


class GameWritesTests(GameCalcBase):
    """Phase 2's game writes, through the real functions (plan 5.4)."""

    Y2025 = {"start": d(2025, 1, 1), "end": d(2025, 12, 31)}
    Y2026 = {"start": d(2026, 1, 1), "end": d(2026, 12, 31)}

    def session(self, item):
        return GameSession.objects.filter(related_game__item=item).order_by("-id").first()

    def rows_of(self, item, kind):
        return ProgressChange.objects.filter(item=item, current_progress__kind=kind).order_by("id")

    def assert_years(self, in_2025, in_2026, all_time):
        numbers = (self.number(**self.Y2025), self.number(**self.Y2026), self.number(pile=True))
        self.assertEqual(numbers, (h(in_2025), h(in_2026), h(all_time)))

    def test_a_retype_writes_one_touch_row_and_keeps_the_saved_day(self):
        item = self.game()
        self.begin(item, d(2025, 12, 1))
        self.save(item, h(40), d(2025, 12, 20))
        self.save(item, h(40), d(2026, 1, 2))
        touches = list(self.rows_of(item, ProgressChange.TOUCH))
        self.assertEqual([row.saved_on for row in touches], [d(2026, 1, 2)])
        self.assertEqual(touches[0].current_progress["playthrough_id"], self.session(item).pk)
        self.assertEqual((touches[0].current_progress["value"], touches[0].current_progress["unit"]), (0, "touch"))
        self.assertEqual(self.session(item).minutes_updated_on, d(2025, 12, 20))
        self.assert_years(40, 0, 40)

    def test_a_percent_resent_keeps_its_day_and_none_is_not_zero(self):
        item = self.game()
        self.begin(item, d(2026, 2, 1))
        self.percent(item, 40, d(2026, 2, 2))
        self.percent(item, 40, d(2026, 2, 5))
        self.assertEqual(self.session(item).percentage_updated_on, d(2026, 2, 2))
        self.save(item, 0, d(2026, 2, 6))
        self.assertEqual(self.session(item).minutes_updated_on, d(2026, 2, 6))
        self.save(item, 0, d(2026, 2, 7))
        self.assertEqual(self.session(item).minutes_updated_on, d(2026, 2, 6))

    def test_a_finish_typing_a_new_total_writes_its_rise_with_the_log(self):
        """GM-219: deleting the log leaves the rise on the day it was typed."""
        item = self.game()
        self.begin(item, d(2025, 12, 1))
        self.save(item, h(10), d(2025, 12, 20))
        _game, entry = self.finish(item, d(2026, 1, 5), total_minutes=h(12))
        linked = list(self.rows_of(item, "minutes").filter(current_progress__has_key="log_id"))
        self.assertEqual([(row.current_progress["log_id"], row.current_progress["value"]) for row in linked], [(entry.pk, h(12))])
        self.assert_years(10, 2, 12)
        with self.on(d(2026, 1, 6)):
            game_tracking.delete_completion(self.owner, entry)
        self.assertEqual(self.session(item).status, Status.IN_PROGRESS.value)
        self.assertEqual(self.session(item).total_minutes, h(12))
        self.assert_years(10, 2, 12)

    def test_a_logs_total_edited_writes_its_change_with_the_log(self):
        item = self.game()
        self.begin(item, d(2026, 2, 1))
        self.save(item, h(5), d(2026, 2, 2))
        _game, entry = self.finish(item, d(2026, 2, 3))
        with self.on(d(2026, 2, 4)):
            game_tracking.update_completion(entry, {"total_minutes": h(7)})
        linked = self.rows_of(item, "minutes").filter(current_progress__has_key="log_id")
        self.assertEqual([row.current_progress["value"] for row in linked], [h(7)])
        self.assertEqual(self.lines(item)[0], "2026-02-03 log +120")

    def test_the_old_feed_leaves_out_a_logs_own_rise(self):
        from api.services import activity

        item = self.game()
        self.begin(item, d(2026, 1, 1))
        self.save(item, h(10), d(2026, 1, 2))
        self.finish(item, d(2026, 1, 5), total_minutes=h(12))
        days = activity._progress_days(ProgressChange.objects.filter(actor=self.owner))
        self.assertEqual(sorted(row["day"] for row in days), [d(2026, 1, 1), d(2026, 1, 2)])

    def test_a_log_dated_before_the_start_moves_it_and_its_delete_puts_it_back(self):
        """Decision 10, the sheet's G6."""
        item = self.game()
        self.begin(item, d(2026, 3, 1))
        self.save(item, h(10), d(2026, 3, 2))
        _game, entry = self.finish(item, d(2025, 12, 20), saved_on=d(2026, 3, 3))
        self.assertEqual(self.session(item).start_date, d(2025, 12, 20))
        self.assertEqual(entry.progress_snapshot["start_date"], "2026-03-01")
        self.assert_years(10, 0, 10)
        with self.on(d(2026, 3, 4)):
            game_tracking.delete_completion(self.owner, entry)
        self.assertEqual(self.session(item).start_date, d(2026, 3, 1))
        self.assert_years(0, 10, 10)

    def test_a_log_date_edited_before_the_start_moves_it_and_back_no_further(self):
        item = self.game()
        self.begin(item, d(2026, 3, 1))
        self.save(item, h(10), d(2026, 3, 2))
        _game, entry = self.finish(item, d(2026, 3, 5))
        with self.on(d(2026, 3, 6)):
            game_tracking.update_completion(entry, {"consumed_at": d(2025, 12, 20)})
        self.assertEqual(self.session(item).start_date, d(2025, 12, 20))
        with self.on(d(2026, 3, 7)):
            game_tracking.update_completion(entry, {"consumed_at": d(2026, 3, 4)})
        self.assertEqual(self.session(item).start_date, d(2026, 3, 1))

    def test_an_old_shape_log_with_no_start_key_leaves_the_start_alone(self):
        item = self.game()
        self.begin(item, d(2026, 3, 1))
        self.save(item, h(10), d(2026, 3, 2))
        _game, entry = self.finish(item, d(2026, 3, 5))
        snapshot = {key: value for key, value in entry.progress_snapshot.items() if key != "start_date"}
        DiaryEntry.objects.filter(pk=entry.pk).update(progress_snapshot=snapshot)
        entry.refresh_from_db()
        with self.on(d(2026, 3, 6)):
            game_tracking.delete_completion(self.owner, entry)
        self.assertEqual(self.session(item).start_date, d(2026, 3, 1))

    def test_a_start_edited_by_hand_moves_its_start_row(self):
        item = self.game()
        self.begin(item, d(2026, 3, 1))
        with self.on(d(2026, 3, 2)):
            game_tracking.update_playthrough(self.owner, item, self.session(item).pk, start_date=d(2026, 2, 20))
        self.assertEqual([row.saved_on for row in self.rows_of(item, ProgressChange.START)], [d(2026, 2, 20)])
        self.assertEqual(self.lines(), ["2026-02-20 started"])

    def test_stopped_then_playing_again_typing_8_is_13_hours(self):
        """Decision 44, the sheet's G8: a new playthrough."""
        item = self.game()
        self.begin(item, d(2025, 12, 1))
        self.save(item, h(5), d(2025, 12, 20))
        with self.on(d(2025, 12, 21)):
            game_tracking.drop(self.owner, item)
        self.begin(item, d(2026, 2, 1))
        self.save(item, h(8), d(2026, 2, 2))
        self.assert_years(5, 8, 13)


class GameImportTests(GameCalcBase):
    """A dated finished game in a file lands in its year (decision 47, plan 5.4)."""

    def import_row(self, item, **fields):
        from integrations.imports.helpers import bulk_create_media

        fields.setdefault("status", Status.COMPLETED.value)
        with self.on(d(2026, 10, 20)):
            bulk_create_media({GAME: [Game(user=self.owner, item=item, **fields)]}, self.owner, source="hltb")

    def test_a_dated_finished_row_becomes_a_log_in_its_year(self):
        item = self.game("dated")
        finished = datetime(2025, 6, 1, tzinfo=UTC)
        self.import_row(item, end_date=finished, progress=h(30), score=Decimal(8))
        logs = list(DiaryEntry.objects.filter(user=self.owner, item=item))
        self.assertEqual([(log.consumed_at.date(), log.import_source, log.rating) for log in logs], [(d(2025, 6, 1), "hltb", Decimal(8))])
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)), h(30))
        self.assertEqual(self.number(pile=True), h(30))
        self.assertFalse(Game.objects.get(user=self.owner, item=item).completed_manually)

    def test_the_same_file_twice_adds_nothing(self):
        item = self.game("twice")
        for _ in range(2):
            self.import_row(item, end_date=datetime(2025, 6, 1, tzinfo=UTC), progress=h(30))
        self.assertEqual(DiaryEntry.objects.filter(user=self.owner, item=item).count(), 1)
        self.assertEqual(self.number(pile=True), h(30))

    def test_a_marked_row_with_a_date_stays_a_mark(self):
        item = self.game("marked")
        self.import_row(item, end_date=datetime(2025, 6, 1, tzinfo=UTC), completed_manually=True)
        self.assertFalse(DiaryEntry.objects.filter(user=self.owner, item=item).exists())
        self.assertEqual(self.number(start=d(2025, 1, 1), end=d(2025, 12, 31)), 0)
        self.assertEqual(self.lines(), [])

    def legacy_mark_file(self, item):
        """Mark a game finished as 0078 leaves one, dated, with hours; export."""
        with self.on(d(2026, 1, 10)):
            game_tracking.mark_completed(self.owner, item)
        Game.objects.filter(user=self.owner, item=item).update(end_date=datetime(2026, 3, 1, 5, tzinfo=UTC), progress=h(10))
        self.assertTrue(Game.objects.get(user=self.owner, item=item).completed_manually)
        return "".join(exports.generate_rows(self.owner)).encode()

    def from_file(self, payload, mode):
        with self.on(d(2026, 10, 20)), patch("app.models.Item.fetch_releases"), patch("app.signals.update_daily_statistics.delay"):
            yamtrack.importer(BytesIO(payload), self.owner, mode)

    def numbers_and_days(self):
        year = self.summary(YEAR_2026, tab=GAME)
        row = time_row(year, GAME)
        all_time = self.summary(ALL_TIME, tab=GAME)
        return (row and row["value"], year["header"]["active_days"], time_row(all_time, GAME)["value"], all_time["header"]["active_days"])

    def test_spines_own_file_brings_a_marked_game_back_as_a_mark(self):
        """A mark is never activity: its date and hours stay out of 2026."""
        for mode in ("new", "overwrite"):
            with self.subTest(mode=mode):
                item = self.game(f"legacy-{mode}")
                payload = self.legacy_mark_file(item)
                before = self.numbers_and_days()
                if mode == "new":
                    Game.objects.filter(user=self.owner, item=item).delete()
                self.from_file(payload, mode)
                self.assertFalse(DiaryEntry.objects.filter(user=self.owner, item=item).exists())
                self.assertTrue(Game.objects.get(user=self.owner, item=item).completed_manually)
                self.assertEqual(self.numbers_and_days(), before)
                Game.objects.filter(user=self.owner, item=item).delete()

    def test_an_undated_finished_row_stays_a_mark(self):
        item = self.game("undated")
        self.import_row(item, progress=h(3))
        self.assertFalse(DiaryEntry.objects.filter(user=self.owner, item=item).exists())
        self.assertEqual(self.lines(), [])
