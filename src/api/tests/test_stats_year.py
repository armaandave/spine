"""The profile's This Year section (api.services.stats_year).

Its numbers are the Stats page's this-year Global numbers (docs/stats-contract.md
STAT-105, STAT-201, STAT-301), its year is the owner's (STAT-005), and its wall deals
this year's titles one per type in turn, newest first.
"""

import random
from datetime import UTC, datetime
from unittest.mock import patch

from django.conf import settings

from api.services import stats_year
from api.tests.test_stats_v2_activity import YEAR_2026, ActivityBase, utc_day
from app import book_tracking
from app.models import MediaTypes

YEAR_PATH = "/api/v1/stats/me/year/"


class StatsYearTests(ActivityBase):
    """The section's numbers, year, wall and gate."""

    def year(self, viewer=None):
        viewer = viewer or self.owner
        path = YEAR_PATH if viewer == self.owner else self.user_path("year")
        response = self.get(viewer, {}, path=path)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def read_pages(self, item, pages, moment):
        with patch("django.utils.timezone.now", return_value=moment):
            book_tracking.update_progress(self.owner, item, progress_type="pages", value=pages)

    def test_numbers_are_the_stats_pages_this_year_numbers_for_every_viewer(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.set_item(film, runtime_minutes=120)
        self.log(film, utc_day(2026, 3, 2))
        self.log(film, utc_day(2026, 4, 2))
        # Last year, and a log only the owner sees (film, album, book and game logs
        # are always public).
        self.log(self.item(MediaTypes.MOVIE.value, "old"), utc_day(2025, 6, 1))
        self.log(self.item(MediaTypes.MANGA.value, "hidden"), utc_day(2026, 5, 1), visibility="private")
        # Pages read this year count, though the book isn't finished.
        novel = self.item(MediaTypes.BOOK.value, "novel")
        self.read_pages(novel, 40, datetime(2026, 2, 3, 18, tzinfo=UTC))

        for viewer in (self.owner, self.follower, self.stranger):
            with self.subTest(viewer=viewer.username):
                year = self.year(viewer)
                summary = self.summary(YEAR_2026, viewer=viewer)
                self.assertEqual(year["year"], 2026)
                self.assertEqual(year["range"], summary["range"])
                self.assertEqual(year["audience"], summary["audience"])
                # Every type is on, so the rows add up to the header's titles.
                self.assertEqual(year["titles"], summary["header"]["titles"])
                self.assertEqual(year["media_types"], summary["media_types"])
                self.assertEqual(year["time"], summary["time"])

        owner = self.year()
        self.assertEqual(owner["titles"], 3)
        book = next(row for row in owner["time"]["rows"] if row["media_type"] == "book")
        self.assertEqual((book["unit"], book["value"]), ("pages", 40))
        self.assertEqual(self.year(self.stranger)["titles"], 2)

    def test_a_type_turned_off_stays_out_of_the_total_and_the_wall(self):
        self.log(self.item(MediaTypes.MOVIE.value, "film"), utc_day(2026, 3, 2))
        self.log(self.item(MediaTypes.GAME.value, "game"), utc_day(2026, 4, 2))
        self.owner.game_enabled = False
        self.owner.save(update_fields=["game_enabled"])

        year = self.year()
        # Global's header still counts the game (STAT-301); the section adds its rows.
        self.assertEqual(self.summary(YEAR_2026)["header"]["titles"], 2)
        self.assertEqual(year["titles"], 1)
        self.assertNotIn("game", [row["media_type"] for row in year["media_types"]])
        self.assertEqual([poster["ref"]["media_id"] for poster in year["posters"]], ["film"])

    def test_the_year_is_this_year_on_the_owners_calendar(self):
        self.set_zone("Pacific/Auckland")
        # 31 December in New York is already 1 January in Auckland.
        with patch("django.utils.timezone.now", return_value=datetime(2026, 12, 31, 12, tzinfo=UTC)):
            year = self.year()
        self.assertEqual(year["year"], 2027)
        self.assertEqual((year["range"]["start_date"], year["range"]["end_date"]), ("2027-01-01", "2027-12-31"))

    def test_the_wall_is_a_random_pick_with_every_type_dealt_in_turn(self):
        for index in range(6):
            self.log(self.item(MediaTypes.MOVIE.value, f"m{index}"), utc_day(2026, 3, index + 1))
        self.log(self.item(MediaTypes.MOVIE.value, "last-year"), utc_day(2025, 9, 1))
        # Pages read count the book as one of this year's titles.
        self.read_pages(self.item(MediaTypes.BOOK.value, "read"), 25, datetime(2026, 2, 1, 18, tzinfo=UTC))
        self.log(self.item(MediaTypes.MUSIC.value, "album"), utc_day(2026, 1, 5))
        # No artwork: the wall has no blank tiles.
        blank = self.item(MediaTypes.MUSIC.value, "blank")
        self.set_item(blank, image=settings.IMG_NONE)
        self.log(blank, utc_day(2026, 8, 1))

        posters = self.year()["posters"]
        ids = [poster["ref"]["media_id"] for poster in posters]
        self.assertCountEqual(ids, ["m0", "m1", "m2", "m3", "m4", "m5", "read", "album"])
        self.assertTrue(all(poster["user_state"] is None for poster in posters))

        scope = stats_year.year_scope(self.owner, self.owner)
        types = ["movie", "book", "music"]
        picks = set()
        for seed in range(8):
            rng = random.Random(seed)  # noqa: S311 - a seeded shuffle, not a secret
            wall = stats_year.wall_posters(scope, types, limit=3, rng=rng)
            # Six films never crowd out the book and the album: one of each a round.
            self.assertEqual([poster["ref"]["media_type"] for poster in wall], types)
            picks.add(wall[0]["ref"]["media_id"])
        # Which film it is changes from pick to pick.
        self.assertGreater(len(picks), 1)

    def test_a_profile_the_viewer_cant_see_has_no_year(self):
        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])
        self.assertEqual(self.get(self.stranger, {}, path=self.user_path("year")).status_code, 404)
        self.assertEqual(self.get(self.follower, {}, path=self.user_path("year")).status_code, 200)
        self.assertEqual(self.get(self.stranger, {}, path=self.user_path("year", "nobody")).status_code, 404)
