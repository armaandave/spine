"""A progress day's `place`: where the read stood at the end of that day (wire shape).

A log that finishes a read adds what's left on its own row, so the day before it still
says where that day stopped, not where the finished read ends.
"""

from api.tests.test_activity_calc_books import d
from api.tests.test_activity_calc_games import GameCalcBase, h
from api.tests.test_activity_calc_series import SeriesCalcBase
from api.tests.test_activity_rows_wire import WireBase


def places(rows):
    """Return each row's day and place in the calculation's order; None for a log."""
    return [(str(row.day), None if row.log_id else row.place) for row in rows]


class BookDayPlaceTests(WireBase):
    """A book's day in pages, or in percent with no length."""

    def test_a_day_finished_by_a_log_stops_where_its_saves_did(self):
        item = self.book("storm", pages=992)
        self.begin(item, d(2026, 11, 7))
        self.save(item, 645, d(2026, 11, 7))
        self.save(item, 800, d(2026, 11, 9))
        self.save(item, 893, d(2026, 11, 9))
        self.finish(item, d(2026, 11, 9))

        rows = [row for row in self.results(self.follower) if row["media"]["title"] == "storm"]
        log, day, earlier = rows[0], rows[1], rows[2]
        self.assertEqual(log["type"], "diary_created")
        self.assertEqual(log["object"]["label"], "99 pages")
        self.assertEqual(day["object"]["label"], "248 pages")
        self.assertEqual(day["object"]["place"], {"pages": 893})
        self.assertEqual(earlier["object"]["place"], {"pages": 645})

    def test_a_percent_with_no_length_stands_in_percent(self):
        item = self.book("unknown", pages=None)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 12.5, d(2026, 3, 1), kind="percentage")
        row = next(row for row in self.calc().rows if row.title_id == item.pk)
        self.assertEqual(row.place, {"percent": 12.5})


class SeriesDayPlaceTests(SeriesCalcBase):
    """A manga's day in chapters."""

    def test_a_manga_day_stands_at_its_chapter_before_the_log(self):
        item = self.title("berserk", total=100)
        self.save(item, 30, d(2026, 3, 2))
        self.save(item, 41, d(2026, 3, 3))
        self.finish(item, d(2026, 3, 3))
        self.assertEqual(
            places(self.calc().rows),
            [("2026-03-02", {"chapter": 30}), ("2026-03-03", {"chapter": 41}), ("2026-03-03", None)],
        )


class GameDayPlaceTests(GameCalcBase):
    """A game's day in each number it has."""

    def test_a_game_day_holds_both_numbers_it_has(self):
        item = self.game()
        self.begin(item, d(2026, 3, 1))
        self.percent(item, 40, d(2026, 3, 2))
        self.save(item, h(10), d(2026, 3, 3))
        self.finish(item, d(2026, 3, 4))
        day = next(row for row in self.calc().rows if str(row.day) == "2026-03-03" and not row.log_id)
        self.assertEqual(day.place, {"minute": h(10), "percent": 40})
