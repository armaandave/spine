"""The one calculation on games, switched off: the comparison reads it until Phase 2.

Each playthrough is its own total (GM-006, GM-102). Rows come from real tracking functions
under a pinned clock on a New York server.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from api.services.activity_rows import list_rows, piled, rows_for
from api.tests.test_activity_calc_books import BookCalcBase, d
from app import game_tracking
from app.models import DiaryEntry, Game, GameSession, MediaTypes, Status
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
