"""A log's `bar`: where its read stood before the log, where it ended, and the length.

The feed draws it under a finishing log, lit for what the log itself added (AC-10).
"""

from datetime import date

from api.services import activity_list
from api.services.activity_rows import rows_for
from api.tests.test_activity_calc_anime import AnimeCalcBase
from api.tests.test_activity_calc_books import d
from api.tests.test_activity_calc_games import GameCalcBase, h
from api.tests.test_activity_calc_series import SeriesCalcBase
from api.tests.test_activity_calc_tv import TV, TVCalcBase
from api.tests.test_activity_rows_wire import WireBase


def bar(rows):
    """Return the one log row's bar as the wire says it."""
    (log,) = [row for row in rows if row.log_id]
    return activity_list.log_fields(log)["bar"]


class BookLogBarTests(WireBase):
    """A book's log lights from the last saved page to the book's length."""

    def test_finished_mid_book_lights_what_the_log_took(self):
        item = self.book("secrets", pages=368)
        self.begin(item, d(2026, 11, 17))
        self.save(item, 110, d(2026, 11, 18))
        self.finish(item, d(2026, 11, 19))
        log = next(row for row in self.results(self.follower) if row["type"] == "diary_created")
        self.assertEqual(log["object"]["bar"], {"unit": "pages", "from": 110, "to": 368, "total": 368})

    def test_saving_the_last_page_first_leaves_the_log_nothing_to_light(self):
        item = self.book("storm", pages=992)
        self.begin(item, d(2026, 11, 7))
        self.save(item, 992, d(2026, 11, 9))
        self.finish(item, d(2026, 11, 9))
        self.assertEqual(bar(self.calc().rows), {"unit": "pages", "from": 992, "to": 992, "total": 992})

    def test_a_read_typed_in_percent_lights_in_percent(self):
        item = self.book("typed", pages=400)
        self.begin(item, d(2026, 3, 1))
        self.save(item, "35.5", d(2026, 3, 2), kind="percentage")
        self.finish(item, d(2026, 3, 3))
        self.assertEqual(bar(self.calc().rows), {"unit": "percent", "from": 35.5, "to": 100, "total": 100})

    def test_a_book_with_no_length_has_no_bar(self):
        item = self.book("unknown", pages=None)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 120, d(2026, 3, 2))
        self.finish(item, d(2026, 3, 3))
        self.assertIsNone(bar(self.calc().rows))


class SeriesLogBarTests(SeriesCalcBase):
    """A manga's log lights from its last chapter to the total."""

    def test_a_manga_log_lights_from_its_last_chapter(self):
        item = self.title("vagabond", total=327)
        self.save(item, 321, d(2026, 3, 2))
        self.finish(item, d(2026, 3, 3))
        self.assertEqual(bar(self.calc().rows), {"unit": "chapters", "from": 321, "to": 327, "total": 327})


class GameLogBarTests(GameCalcBase):
    """A game's log draws the percent it recorded; hours alone have no length."""

    def test_a_finish_at_65_percent_lights_from_where_it_stood(self):
        item = self.game("both")
        self.begin(item, d(2026, 2, 1))
        self.percent(item, 40, d(2026, 2, 2))
        self.finish(item, d(2026, 2, 3), total_minutes=h(40), percentage=65)
        self.assertEqual(bar(self.calc().rows), {"unit": "percent", "from": 40, "to": 65, "total": 100})

    def test_hours_alone_have_no_bar(self):
        item = self.game("hours")
        self.begin(item, d(2026, 2, 1))
        self.save(item, h(10), d(2026, 2, 2))
        self.finish(item, d(2026, 2, 3), total_minutes=h(12))
        self.assertIsNone(bar(self.calc().rows))


class TVLogBarTests(TVCalcBase):
    """A season log lights the episodes it filled; an episode log has no bar."""

    def test_a_season_log_lights_what_it_filled(self):
        show = self.show(10)
        self.on(date(2026, 3, 1))
        self.tick(show, 1, 2, 3, 4, 5, 6, 7)
        self.on(date(2026, 3, 2))
        self.log(self.season(show))
        rows = rows_for([self.user], self.follower, types=[TV]).rows
        self.assertEqual(bar(rows), {"unit": "episodes", "from": 7, "to": 10, "total": 10})

    def test_an_episode_log_has_no_bar(self):
        show = self.show(10)
        self.on(date(2026, 3, 1))
        self.log(self.episode(show, 1, 10))
        self.assertIsNone(bar(rows_for([self.user], self.user, types=[TV]).rows))


class AnimeLogBarTests(AnimeCalcBase):
    """An anime log lights the episodes it filled, after the ticks before it."""

    def test_ticks_then_a_log(self):
        item = self.title()
        self.on(d(2026, 3, 2))
        self.tick(item, 1, 2, 3)
        self.on(d(2026, 3, 3))
        self.log(item, day=d(2026, 3, 3))
        self.assertEqual(bar(self.calc().rows), {"unit": "episodes", "from": 3, "to": 12, "total": 12})
