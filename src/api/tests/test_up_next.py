"""Home's Up next (docs/up-next-contract.md, UN-001 to UN-006)."""

from datetime import UTC, date, datetime, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from app import anime_tracking, tv_tracking
from app.tests.anime_fixtures import AnimeTestCase
from app.tests.test_tv_tracking_viewings import TVTrackingTestCase
from events.tv_airing import refresh_aired_shows

URL = "/api/v1/tracking/up-next/"
# The user's today is Oct 25, 2026 (Los Angeles); these Thursdays are Sep 10 to Nov 12.
THURSDAYS_FROM_SEP_10 = [date(2026, 9, 10) + timedelta(weeks=week) for week in range(10)]
OCT_29 = date(2026, 10, 29)
NOV_5 = date(2026, 11, 5)
MAR_12_2027 = date(2027, 3, 12)


def weekly(first, count=10):
    """Return `count` weekly air dates from `first`."""
    return [first + timedelta(weeks=week) for week in range(count)]


class UpNextTVTestCase(TVTrackingTestCase):
    """TV shows on a fake TMDB; the user's today is Oct 25, 2026."""

    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def up_next(self):
        response = self.api.get(URL)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return response.data["results"]

    def listed(self):
        """Return (title, kind, season, episode, air date) for each row, in order."""
        return [
            (
                row["media"]["title"],
                row["airing"]["kind"],
                row["airing"]["season_number"],
                row["airing"]["episode_number"],
                row["airing"]["air_date"],
            )
            for row in self.up_next()
        ]

    def airing_show(self, title="Airing", first=THURSDAYS_FROM_SEP_10[0]):
        """S1 from 2020; S2 weekly from `first` (Sep 10: E7 is out, E8 is Oct 29)."""
        return self.make_show({1: 10, 2: weekly(first)}, title=title, status="Returning Series")

    def returning_show(self, title="Returning"):
        """S1 and S2 aired long ago; S3 premieres Mar 12, 2027."""
        return self.make_show({1: 10, 2: 10, 3: weekly(MAR_12_2027)}, title=title, status="Returning Series")

    def mark(self, show, season_number=None):
        self.advance()
        tv_tracking.mark_watched(self.user, show, season_number=season_number)

    def choose(self, show, status):
        self.advance()
        tv_tracking.choose(self.user, show, status)


class UN002AiringSeasonTests(UpNextTVTestCase):
    """UN-002: the season that's airing, when you're in it."""

    def test_caught_up_lists_the_next_episode(self):
        self.mark(self.airing_show())
        self.assertEqual(self.listed(), [("Airing", "episode", 2, 8, "2026-10-29")])

    def test_behind_in_the_airing_season_is_listed(self):
        show = self.airing_show()
        self.mark(show, season_number=1)
        self.ticks(show, 2, [1, 2])
        self.assertEqual((self.tv(show).up_next_season, self.tv(show).up_next_episode), (2, 3))
        self.assertEqual(self.listed(), [("Airing", "episode", 2, 8, "2026-10-29")])

    def test_behind_in_an_earlier_season_is_not_listed(self):
        show = self.airing_show()
        self.ticks(show, 1, [1, 2, 3, 4])
        self.assertEqual(self.tv(show).up_next_season, 1)
        self.assertEqual(self.listed(), [])

    def test_watching_with_nothing_watched_is_not_listed(self):
        show = self.airing_show()
        self.choose(show, "In progress")
        self.assertEqual(self.listed(), [])


class UN003NextSeasonTests(UpNextTVTestCase):
    """UN-003: the next season's premiere, when you're on the latest season so far."""

    def test_finished_show_lists_the_premiere(self):
        show = self.returning_show()
        self.mark(show)
        self.assertEqual(self.status(show), "Completed")
        self.assertEqual(self.listed(), [("Returning", "season_premiere", 3, 1, "2027-03-12")])

    def test_on_the_latest_season_lists_the_premiere(self):
        show = self.returning_show()
        self.mark(show, season_number=1)
        self.ticks(show, 2, [1, 2, 3, 4, 5])
        self.assertEqual(self.listed(), [("Returning", "season_premiere", 3, 1, "2027-03-12")])

    def test_on_an_earlier_season_is_not_listed(self):
        show = self.returning_show()
        self.ticks(show, 1, [1, 2])
        self.assertEqual(self.listed(), [])

    def test_a_season_with_only_a_premiere_date_is_listed_without_an_episode(self):
        show = self.make_show(
            {1: 10, 2: []},
            title="Announced",
            status="Returning Series",
            season_air_dates={2: MAR_12_2027},
        )
        self.mark(show)
        self.assertEqual(self.listed(), [("Announced", "season_premiere", 2, None, "2027-03-12")])


class UN004PlanningTests(UpNextTVTestCase):
    """UN-004: Planning lists only a show's own premiere."""

    def test_a_new_show_in_planning_lists_its_premiere(self):
        show = self.make_show({1: weekly(date(2026, 11, 20))}, title="Brand New", status="Returning Series")
        self.choose(show, "Planning")
        self.assertEqual(self.listed(), [("Brand New", "series_premiere", 1, 1, "2026-11-20")])

    def test_a_planning_show_that_has_aired_is_not_listed_for_a_new_season(self):
        self.choose(self.returning_show(), "Planning")
        self.assertEqual(self.listed(), [])

    def test_a_planning_show_airing_now_is_not_listed(self):
        self.choose(self.airing_show(), "Planning")
        self.assertEqual(self.listed(), [])


class UN001WhatIsListedTests(UpNextTVTestCase):
    """UN-001: dated only, no Paused or Dropped, the order, and leaving when it airs."""

    def test_paused_and_dropped_are_not_listed(self):
        paused = self.airing_show(title="Paused")
        dropped = self.returning_show(title="Dropped")
        self.mark(paused)
        self.mark(dropped)
        self.choose(paused, "Paused")
        self.choose(dropped, "Dropped")
        self.assertEqual(self.listed(), [])

    def test_a_season_with_no_dates_is_not_listed(self):
        show = self.make_show({1: 10, 2: [None, None, None]}, title="Undated", status="Returning Series")
        self.mark(show)
        self.assertTrue(self.tv(show).caught_up)
        self.assertEqual(self.listed(), [])

    def test_returning_with_nothing_scheduled_is_not_listed(self):
        show = self.make_show({1: 10}, title="Someday", status="Returning Series")
        self.mark(show)
        self.assertTrue(self.tv(show).caught_up)
        self.assertEqual(self.listed(), [])

    def test_an_ended_show_is_not_listed(self):
        self.mark(self.make_show({1: 10, 2: 10}))
        self.assertEqual(self.listed(), [])

    def test_soonest_first_then_most_recently_watched(self):
        earlier_watched = self.airing_show(title="Watched first")
        tuesday = self.airing_show(title="Tuesday", first=date(2026, 9, 8))
        later_watched = self.airing_show(title="Watched last")
        self.mark(earlier_watched)
        self.mark(tuesday)
        self.mark(later_watched)
        self.assertEqual(
            [(title, air_date) for title, _kind, _season, _episode, air_date in self.listed()],
            [("Tuesday", "2026-10-27"), ("Watched last", "2026-10-29"), ("Watched first", "2026-10-29")],
        )

    def test_it_stays_until_the_episode_airs_then_moves_to_in_progress(self):
        show = self.airing_show()
        self.mark(show)
        # Oct 29 in Los Angeles: the episode is out, and the hourly job hasn't run yet.
        self.clock.set(datetime(2026, 10, 29, 15, 0, tzinfo=UTC))
        self.assertEqual(self.listed(), [("Airing", "episode", 2, 8, "2026-10-29")])
        refresh_aired_shows()
        self.assertEqual(self.listed(), [("Airing", "episode", 2, 9, "2026-11-05")])
        response = self.api.get("/api/v1/tracking/", {"media_type": "tv", "continue_watching": "true"})
        self.assertEqual([row["media"]["title"] for row in response.data["results"]], ["Airing"])
        self.assertEqual((self.tv(show).up_next_season, self.tv(show).up_next_episode), (2, 8))

    def test_nothing_when_tv_is_turned_off(self):
        self.mark(self.airing_show())
        self.user.tv_enabled = False
        self.user.save(update_fields=["tv_enabled"])
        self.assertEqual(self.listed(), [])

    def test_only_your_own_shows(self):
        show = self.airing_show()
        self.mark(show)
        other = APIClient()
        other.force_authenticate(type(self.user).objects.create_user(username="someone-else"))
        self.assertEqual(other.get(URL).data["results"], [])


class UN006ReadTests(UpNextTVTestCase):
    """UN-006: the response, and a query count that doesn't grow with the list."""

    def test_the_row_carries_the_media_summary(self):
        show = self.airing_show()
        self.mark(show)
        row = self.up_next()[0]
        self.assertEqual(row["media"]["ref"]["media_id"], show.media_id)
        self.assertEqual(row["media"]["ref"]["media_type"], "tv")
        self.assertEqual(set(row["airing"]), {"kind", "season_number", "episode_number", "air_date"})

    def test_the_query_count_does_not_grow_with_the_list(self):
        for index in range(2):
            self.mark(self.airing_show(title=f"Show {index}"))
        with CaptureQueriesContext(connection) as two:
            self.assertEqual(len(self.up_next()), 2)
        for index in range(2, 6):
            self.mark(self.airing_show(title=f"Show {index}"))
        with CaptureQueriesContext(connection) as six:
            self.assertEqual(len(self.up_next()), 6)
        self.assertEqual(len(six), len(two))

    def test_no_provider_call(self):
        self.mark(self.airing_show())
        self.fake.reset_calls()
        self.up_next()
        self.assertEqual(self.fake.count(), 0)


class UN005AnimeTests(AnimeTestCase):
    """UN-005: anime episodes and a planned title's premiere (today is Oct 25)."""

    def listed(self):
        response = self.api.get(URL)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return [
            (
                row["airing"]["kind"],
                row["airing"]["season_number"],
                row["airing"]["episode_number"],
                row["airing"]["air_date"],
            )
            for row in response.data["results"]
        ]

    def test_caught_up_lists_the_next_episode(self):
        item = self.airing()
        anime_tracking.mark_watched(self.user, item)
        self.assertTrue(self.row(item).caught_up)
        self.assertEqual(self.listed(), [("episode", None, 8, "2026-10-29")])

    def test_behind_is_listed(self):
        item = self.airing()
        self.tick(item, 1, 2, 3)
        self.assertEqual(self.row(item).up_next_episode, 4)
        self.assertEqual(self.listed(), [("episode", None, 8, "2026-10-29")])

    def test_a_planned_title_lists_its_premiere(self):
        item = self.title(
            total=12,
            status="Not yet aired",
            start_date="2027-01-10",
            next_episode=1,
            next_air_at=datetime(2027, 1, 10, 15, 0, tzinfo=self.clock.moment.tzinfo),
        )
        anime_tracking.choose(self.user, item, "Planning")
        self.assertEqual(self.listed(), [("series_premiere", None, 1, "2027-01-10")])

    def test_a_planned_title_airing_now_is_not_listed(self):
        item = self.airing()
        anime_tracking.choose(self.user, item, "Planning")
        self.assertEqual(self.listed(), [])

    def test_paused_and_dropped_are_not_listed(self):
        paused = self.airing()
        dropped = self.airing()
        self.tick(paused, 1)
        self.tick(dropped, 1)
        anime_tracking.choose(self.user, paused, "Paused")
        anime_tracking.choose(self.user, dropped, "Dropped")
        self.assertEqual(self.listed(), [])

    def test_a_finished_title_is_not_listed(self):
        item = self.title(total=12)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.listed(), [])

    def test_nothing_when_anime_is_turned_off(self):
        anime_tracking.mark_watched(self.user, self.airing())
        self.user.anime_enabled = False
        self.user.save(update_fields=["anime_enabled"])
        self.assertEqual(self.listed(), [])
