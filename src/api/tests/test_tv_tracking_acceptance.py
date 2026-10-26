"""TV acceptance suite: every case run through every entry point (plan 4.1, 4.2).

docs/tv-tracking-contract.md lists acceptance scenarios A-01 to A-68 and asks for each
to run "through the API, through the model hooks the web and webhooks use, and through
an import where one applies; all paths must reach the same end state" (TV-207). Each
test here drives the same user steps through the drivers in api.tests.tv_scenarios (one
fresh account per path), asserts the scenario's Required result, and compares the
paths' snapshots. Entry-point-only outputs (the season-finish composer, the
earlier-episodes question, logs on paths that can't log) are checked only where the
path offers them. Most tests also check that every surface agrees with the stored state
after the writes (TV-705).

The server runs on America/New_York and each account's calendar is America/Los_Angeles;
the clock is patched through django.utils.timezone.now, TMDB is the shared fake, and no
test needs .env or API keys. TV000InheritedRulesTests covers TV-000's inherited table,
one test per row, through the API; the shared single-weight, book and game suites run
unchanged beside this one.
"""

import csv
import json
import math
import threading
import unittest
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from unittest import mock
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from api.tests.tv_scenarios import (
    ALL_KEYS,
    COMPLETED,
    DROPPED,
    EPISODE_TYPE,
    IMPORT_KEYS,
    NOON_PACIFIC,
    OCT_25,
    PACIFIC,
    PAUSED,
    PLANNING,
    SEASON_TYPE,
    WATCHING,
    APIDriver,
    TVAcceptanceTestCase,
    Unsupported,
    at_pacific,
    export_csv,
    pick,
    snapshot,
    weekly,
)
from app import tv_listing, tv_tracking
from app.models import (
    TV,
    DiaryEntry,
    Episode,
    Item,
    MediaLike,
    Season,
    Sources,
)
from app.tests.tv_fixtures import (
    FakeTVProvider,
    make_manual_show,
    make_show,
    make_user,
    patch_now,
)
from events import tv_airing
from events.models import Event
from events.notifications import get_user_releases
from lists.models import CustomList, CustomListItem
from social.models import Activity

TICK_PATHS = ("api", "installed", "web", "webhook", "hooks")
LOG_PATHS = ("api", "installed", "web")
WEBHOOK_VENDORS = ("webhook:plex", "webhook:jellyfin", "webhook:emby")
OCT_20 = date(2026, 10, 20)
OCT_22 = date(2026, 10, 22)
OCT_24 = date(2026, 10, 24)
OCT_26 = date(2026, 10, 26)
OCT_29 = date(2026, 10, 29)
MAR_12 = date(2027, 3, 12)


def ticks(driver, show, season, episodes):
    """Tick episodes in order; return the last response."""
    response = None
    for episode in episodes:
        response = driver.tick(show, season, episode)
    return response


def positions(season, episodes):
    """Return [(season, episode), ...]."""
    return [(season, episode) for episode in episodes]


class AcceptanceHelpers:
    """Small readers shared by the flows."""

    def snap_of(self, driver, show):
        return snapshot(driver.user, show)

    def season_card(self, driver, show, season):
        reader = self.api(driver.user)
        cards = {card["season_number"]: card["tv"] for card in reader.detail(show).data["seasons"]}
        return cards[season]

    def run_job(self):
        self.clock.advance(minutes=1)
        with self.captureOnCommitCallbacks(execute=True):
            return tv_airing.refresh_aired_shows()


# ---------------------------------------------------------------------------------
# Flow A: episode by episode, the next season, a season log
# ---------------------------------------------------------------------------------


class FlowAEpisodeByEpisodeTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """A-01, A-02, A-03, A-05 to A-08, A-10, A-11, A-18, A-48, A-64."""

    def test_a_01_tick_s1_e1_of_an_untracked_show(self):
        """TV-100, TV-105, TV-205, TV-703.

        Watching, S1 E2 next, "1 of N", nothing else.
        """
        show = self.make_show()
        responses = {}

        def steps(driver):
            if driver.path == "import":
                driver.import_rows(driver.plays(show, [(1, 1, self.clock.advance(minutes=1))]))
            else:
                responses[driver.path] = driver.tick(show, 1, 1)

        drivers = self.run_paths(show, steps, ("api", "installed", "web", *WEBHOOK_VENDORS, "hooks", "import"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["up_next"][:2], (1, 2))
        self.assertEqual(snap["counts"], (1, 18))
        self.assertEqual(snap["seasons"][1]["watched"], 1)
        self.assertEqual(snap["season_rows"], [1])  # TV-205: no other season tracking
        self.assertEqual(snap["viewings"], [(1, 1, "tick", None, "2026-10-25")])
        self.assertEqual(snap["logs"], [])
        self.assertEqual(snap["activity"], {})  # TV-703: no feed item
        self.assertIsNone(responses["api"].data["finished"])
        self.assertIsNone(responses["api"].data["earlier_unwatched"])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_02_a_double_tap_or_retry_records_one_viewing(self):
        """TV-101, TV-705.

        Double taps, retries and repeated webhook watches add nothing.
        """
        show = self.make_show()

        def steps(driver):
            if driver.path == "api":
                mutation_id = str(uuid4())
                first = driver.tick(show, 1, 1, mutation_id=mutation_id)
                replay = driver.tick(show, 1, 1, mutation_id=mutation_id)
                again = driver.tick(show, 1, 1)
                self.assertTrue(first.data["changed"])
                self.assertTrue(replay.data["changed"])  # the recorded result
                self.assertFalse(again.data["changed"])
            else:
                driver.tick(show, 1, 1)
                driver.tick(show, 1, 1)

        drivers = self.run_paths(show, steps, ("api", "installed", "web", *WEBHOOK_VENDORS))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(len(snap["viewings"]), 1)
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_02_a_duplicate_model_write_is_kept_but_counted_once(self):
        """Plan 1.2 Hooks.

        Working out never deletes a viewing (TV-207), so a second Episode.objects.create
        in the pass is kept; it counts once toward watched.
        """
        show = self.make_show()
        driver = self.driver("hooks")

        driver.tick(show, 1, 1)
        driver.tick(show, 1, 1)

        snap = self.snap_of(driver, show)
        self.assertEqual(len(snap["viewings"]), 2)
        self.assertEqual(snap["counts"], (1, 18))
        self.assertEqual(snap["seasons"][1]["watched"], 1)

    def test_a_03_tick_then_untick_the_only_episode(self):
        """TV-102, TV-208: the show leaves the library."""
        show = self.make_show()

        def steps(driver):
            driver.tick(show, 1, 1)
            driver.untick(show, 1, 1)

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "webhook:jellyfin"))

        snap = self.assert_same_state(show, drivers)
        self.assertFalse(snap["in_library"])
        self.assertEqual(snap["viewings"], [])
        self.assertEqual(snap["season_rows"], [])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_03_through_model_hooks_the_show_also_leaves_the_library(self):
        """TV-003, TV-205, TV-207, TV-208.

        Episode.objects.create then episode.delete() leave the same facts as a tick and
        an untick (nothing), so the same state: no TV row, no Season row.
        """
        show = self.make_show()

        def steps(driver):
            driver.tick(show, 1, 1)
            driver.untick(show, 1, 1)

        drivers = self.run_paths(show, steps, ("api", "hooks"))

        snap = self.assert_same_state(show, drivers)
        self.assertFalse(snap["in_library"])

    def test_a_05_not_now_saves_nothing_and_the_re_tick_offers_nothing(self):
        """TV-200, TV-403.

        The finishing tick offers Log Season once per season and pass.
        """
        show = self.make_show({1: 3, 2: 3})
        offers = {}

        def steps(driver):
            first = ticks(driver, show, 1, (1, 2, 3))
            # "Not now": the app sends nothing.
            driver.untick(show, 1, 3)
            again = driver.tick(show, 1, 3)
            if driver.path in ("api", "installed"):
                offers[driver.path] = (first.data["finished"], again.data["finished"])

        drivers = self.run_paths(show, steps, TICK_PATHS)

        snap = self.assert_same_state(show, drivers)
        finished, again = offers["api"]
        self.assertEqual((finished["level"], finished["season_number"]), ("season", 1))
        self.assertFalse(finished["show_finished"])
        self.assertEqual([target["id"] for target in finished["composer"]["targets"]], ["season:1"])
        self.assertEqual(finished["composer"]["targets"][0]["date"], "2026-10-25")
        self.assertIsNone(again)  # the re-tick opens nothing
        self.assertEqual(offers["installed"], (None, None))  # installed builds open nothing
        self.assertEqual(snap["seasons"][1]["status"], COMPLETED)
        self.assertEqual(snap["logs"], [])  # Not now saved nothing
        season_state = drivers["api"].state(show, 1)["season"]
        self.assertEqual(season_state["state"], "watched")
        self.assertIn("log_season", season_state["available_actions"])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_06_a_skipped_episode_keeps_the_season_open(self):
        """TV-105, TV-200, TV-403.

        "9 of 10 · E4 not watched", no composer, S2 E1 next.
        """
        show = self.make_show()
        offers = []

        def steps(driver):
            for episode in (1, 2, 3, 5, 6, 7, 8, 9, 10):
                response = driver.tick(show, 1, episode)
                if driver.path == "api":
                    offers.append(response.data["finished"])

        drivers = self.run_paths(show, steps, TICK_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(offers, [None] * 9)
        season = snap["seasons"][1]
        self.assertEqual((season["watched"], season["aired"], season["first_gap"]), (9, 10, 4))
        self.assertEqual(season["status"], WATCHING)
        self.assertEqual(snap["up_next"][:2], (2, 1))
        card = self.season_card(drivers["api"], show, 1)
        self.assertEqual((card["state"], card["watched_count"], card["first_gap_episode"]), ("in_progress", 9, 4))
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_07_a_season_log_fills_the_gap_and_keeps_the_ticks_dates(self):
        """TV-401: E4 is filled undated by the log, the ticks keep their days."""
        show = self.make_show()

        def steps(driver):
            for episode in (1, 2, 3, 5, 6, 7, 8, 9, 10):
                driver.tick(show, 1, episode)
            driver.log(show, 1, day=OCT_24)

        drivers = self.run_paths(show, steps, LOG_PATHS)

        snap = self.assert_same_state(show, drivers)
        log = ("season", 1, None, "2026-10-24", 1)
        self.assertIn((1, 4, "fill", log, None), snap["viewings"])
        ticked = [viewing for viewing in snap["viewings"] if viewing[2] == "tick"]
        self.assertEqual(len(ticked), 9)
        self.assertTrue(all(viewing[4] == "2026-10-25" and viewing[3] is None for viewing in ticked))
        self.assertEqual(snap["seasons"][1]["status"], COMPLETED)
        self.assertEqual(snap["activity"], {"diary_created": 1})
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_08_finishing_season_1_moves_up_next_without_season_2_tracking(self):
        """TV-105, TV-205: S2 E1 is up next and Season 2 has no tracking."""
        show = self.make_show({1: 4, 2: 4})

        def steps(driver):
            if driver.path == "import":
                plays = [(1, episode, self.clock.advance(minutes=1)) for episode in range(1, 5)]
                driver.import_rows(driver.plays(show, plays))
            elif driver.variant == "progress":
                # The season card's + button ticks the season's up next (progress_edit).
                driver.tick(show, 1, 1)
                for _ in range(3):
                    driver.progress(show, 1, "increase")
            else:
                ticks(driver, show, 1, range(1, 5))

        drivers = self.run_paths(show, steps, (*TICK_PATHS, "web#progress", "import"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["up_next"][:2], (2, 1))
        self.assertEqual(snap["season_rows"], [1])
        self.assertEqual(snap["status"], WATCHING)
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_10_caught_up_mid_season_stays_watching_off_continue_watching(self):
        """TV-202, TV-701.

        Watching, "Caught up · E7 on Thu", back on Continue Watching when E7 airs.
        """
        # Oct 25, 2026 is a Sunday; E6 aired Thursday Oct 22, E7 airs Thursday Oct 29.
        show = self.make_show({1: weekly(date(2026, 9, 17), 8)}, status="Returning Series")

        def steps(driver):
            ticks(driver, show, 1, range(1, 7))

        drivers = self.run_paths(show, steps, TICK_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], WATCHING)
        self.assertTrue(snap["caught_up"])
        self.assertEqual(snap["next_airing"], (1, 7, OCT_29))
        self.assertEqual(snap["seasons"][1]["status"], WATCHING)
        self.assertEqual(drivers["api"].state(show)["next_airing"]["air_date"], "2026-10-29")
        for driver in drivers.values():
            self.assertNotIn(show.media_id, self.api(driver.user).continue_watching())
        self.assert_surfaces_agree_for(show, drivers)

        self.clock.set(datetime(2026, 10, 29, 9, 0, tzinfo=ZoneInfo(PACIFIC)))
        self.run_job()

        snap = self.assert_same_state(show, drivers)
        self.assertFalse(snap["caught_up"])
        self.assertEqual(snap["up_next"][:2], (1, 7))
        for driver in drivers.values():
            self.assertIn(show.media_id, self.api(driver.user).continue_watching())

    def test_a_11_watching_only_the_finale_never_finishes_a_season(self):
        """TV-104, TV-200: "1 of 10", not finished."""
        show = self.make_show()
        questions = []

        def steps(driver):
            if driver.path == "import":
                driver.import_rows(driver.plays(show, [(1, 10, self.clock.advance(minutes=1))]))
                return
            response = driver.tick(show, 1, 10)
            if driver.path == "api":
                questions.append(response.data["earlier_unwatched"])
                self.assertIsNone(response.data["finished"])
                driver.answer(show, response.data["earlier_unwatched"], yes=False)

        drivers = self.run_paths(show, steps, (*TICK_PATHS, "import"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(questions[0]["question"], "Also mark S1 E1–E9 watched?")  # noqa: RUF001 - the contract's copy
        season = snap["seasons"][1]
        self.assertEqual((season["watched"], season["aired"], season["status"]), (1, 10, WATCHING))
        self.assertFalse(snap["finished"])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_18_tick_then_log_counts_one_viewing_on_the_ticks_day(self):
        """TV-402, TV-404, TV-703.

        The composer defaults to the tick's day, Rewatch off; one viewing and one feed
        item.
        """
        show = self.make_show()
        api = self.driver("api")
        self.clock.set(datetime(2026, 10, 24, 20, 0, tzinfo=ZoneInfo(PACIFIC)))
        tick = api.tick(show, 1, 1)
        viewing_id = tick.data["episode"]["untick_viewing_id"]
        self.clock.set(at_pacific(OCT_25))

        target = api.episode_state(show, 1, 1)["composer"]["targets"][0]
        self.assertEqual((target["date"], target["is_rewatch"]), ("2026-10-24", False))
        self.assertEqual(target["claims_viewing_id"], viewing_id)
        api.log(show, 1, 1, day=date.fromisoformat(target["date"]), is_rewatch=target["is_rewatch"], rating=8)

        def steps(driver):
            self.clock.set(datetime(2026, 10, 24, 20, 0, tzinfo=ZoneInfo(PACIFIC)))
            driver.tick(show, 1, 1)
            self.clock.set(at_pacific(OCT_25))
            driver.log(show, 1, 1, day=OCT_24, is_rewatch=False, rating=8)

        drivers = {"api": api, **self.run_paths(show, steps, ("installed",))}

        snap = self.assert_same_state(show, drivers)
        log = ("episode", 1, 1, "2026-10-24", 1)
        self.assertEqual(snap["viewings"], [(1, 1, "tick", log, "2026-10-24")])
        self.assertEqual(snap["activity"], {"diary_created": 1})
        self.assertEqual(snap["logs"][0][3], False)  # is_rewatch
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_48_a_season_log_from_the_finish_composer_blocks_the_untick(self):
        """TV-400, TV-102: "Delete your Season 1 log first." and nothing changes."""
        show = self.make_show()
        before = {}

        def steps(driver):
            last = ticks(driver, show, 1, range(1, 11))
            logger = driver if driver.path in LOG_PATHS else self.api(driver.user)
            if driver.path == "api":
                target = last.data["finished"]["composer"]["targets"][0]
                driver.log(show, 1, day=date.fromisoformat(target["date"]), is_rewatch=target["is_rewatch"])
            else:
                logger.log(show, 1, is_rewatch=False)
            before[driver.name] = snapshot(driver.user, show)
            if driver.path == "api":
                state = driver.episode_state(show, 1, 10)
                self.assertIsNone(state["untick_viewing_id"])
                self.assertEqual(state["untick_message"], "Delete your Season 1 log first.")
                response = driver.untick(show, 1, 10, expect=409)
                self.assertEqual(response.data["error"]["code"], "tv_log_blocks")
                self.assertEqual(response.data["error"]["message"], "Delete your Season 1 log first.")
            elif driver.path == "installed":
                response = driver.untick(show, 1, 10, expect=409)
                self.assertEqual(response.data["error"]["message"], "Delete your Season 1 log first.")
            elif driver.path == "web":
                response = driver.untick(show, 1, 10, expect=409)
                self.assertEqual(response.json(), {"error": "Delete your Season 1 log first."})
            else:
                driver.untick(show, 1, 10)  # acknowledged, changes nothing (TV-802)

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "webhook"))

        snap = self.assert_same_state(show, drivers)
        for path, driver in drivers.items():
            self.assertEqual(self.snap_of(driver, show), before[path], path)
        self.assertEqual(snap["seasons"][1]["status"], COMPLETED)
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_64_a_named_next_episode_not_yet_listed_keeps_the_season_airing(self):
        """TV-200, TV-202, TV-403.

        "Airing", not finished, no composer; Watching and caught up until E7.
        """
        show = self.make_show(
            {1: weekly(date(2026, 9, 17), 6)},
            status="Returning Series",
            next_episode_to_air={"season_number": 1, "episode_number": 7, "air_date": OCT_29},
        )
        offers = []

        def steps(driver):
            last = ticks(driver, show, 1, range(1, 7))
            if driver.path == "api":
                offers.append(last.data["finished"])

        drivers = self.run_paths(show, steps, TICK_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(offers, [None])
        self.assertEqual(snap["status"], WATCHING)
        self.assertTrue(snap["caught_up"])
        self.assertFalse(snap["finished"])
        self.assertEqual(snap["next_airing"], (1, 7, OCT_29))
        self.assertEqual(snap["seasons"][1]["status"], WATCHING)
        self.assertEqual(self.season_card(drivers["api"], show, 1)["state"], "airing")
        self.assert_surfaces_agree_for(show, drivers)


# ---------------------------------------------------------------------------------
# Flow B: seasons only
# ---------------------------------------------------------------------------------


class FlowBSeasonsOnlyTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """A-13, A-16, A-17, A-21, A-32, A-33, A-56."""

    def test_a_13_logging_seasons_of_an_untracked_show_tracks_it(self):
        """TV-400, TV-401, TV-501.

        In the library with S3 E1 next; each season's stars come from its log.
        """
        show = self.make_show({1: 3, 2: 3, 3: 3})

        def steps(driver):
            driver.log(show, 1, rating=8)
            driver.log(show, 2, rating=6)

        drivers = self.run_paths(show, steps, LOG_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["up_next"][:2], (3, 1))
        self.assertEqual(snap["stars"]["seasons"], {
            1: ("8.0", ("season", 1, None, "2026-10-25", 1)),
            2: ("6.0", ("season", 2, None, "2026-10-25", 1)),
        })
        self.assertEqual(snap["stars"]["show"], (None, None))  # TV-500
        self.assertEqual(snap["watched_pass"], [*positions(1, (1, 2, 3)), *positions(2, (1, 2, 3))])
        self.assertTrue(all(viewing[2] == "fill" and viewing[4] is None for viewing in snap["viewings"]))
        self.assert_surfaces_agree_for(show, drivers)

        # If those seasons are all the show has, it is Completed.
        whole = self.make_show({1: 3, 2: 3})
        api = drivers["api"]
        api.log(whole, 1, rating=8)
        api.log(whole, 2, rating=6)
        self.assertEqual(snapshot(api.user, whole)["status"], COMPLETED)

    def test_a_16_deleting_a_season_log_unmarks_only_its_fills(self):
        """TV-406: the deletion asks first, unmarks E4-E10, keeps E1-E3, E4 is next."""
        show = self.make_show()
        counts = {}

        def steps(driver):
            ticks(driver, show, 1, (1, 2, 3))
            driver.log(show, 1)
            entry = DiaryEntry.objects.get(user=driver.user, item__season_number=1)
            if driver.path == "api":
                counts["unmarks"] = driver.entry(entry.pk)["tv_log"]["unmarks_count"]
            driver.delete_log(entry.pk)

        drivers = self.run_paths(show, steps, LOG_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(counts["unmarks"], 7)  # "7 episodes it marked watched will be unmarked."
        self.assertEqual(snap["watched_all"], positions(1, (1, 2, 3)))
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["up_next"][:2], (1, 4))
        self.assertEqual(snap["logs"], [])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_16_a_stale_deletion_changes_nothing_and_asks_again(self):
        """TV-406, TV-705: the dialog's version is stale after another write."""
        show = self.make_show()
        api = self.driver("api")
        ticks(api, show, 1, (1, 2, 3))
        entry_id = api.log(show, 1).data["id"]
        version = api.entry(entry_id)["tv_log"]["state_version"]
        api.tick(show, 2, 1)
        before = self.snap_of(api, show)

        response = api.delete_log(entry_id, version=version, expect=409)

        self.assertEqual(response.data["error"]["code"], "tv_state_changed")
        self.assertEqual(response.data["error"]["tv"]["count"], 7)
        self.assertEqual(self.snap_of(api, show), before)

    def test_a_17_deleting_the_only_log_removes_the_show_and_clears_opinions(self):
        """TV-406, TV-208.

        The show leaves the library; its stars and hearts are cleared.
        """
        show = self.make_show({1: 3, 2: 3})

        def steps(driver):
            response = driver.log(show, 1, rating=8, liked=True)
            driver.rate(show, 7)
            if driver.path != "web":
                driver.like(show)
            entry = DiaryEntry.objects.get(user=driver.user, item__season_number=1)
            self.assertIsNotNone(response)
            driver.delete_log(entry.pk)

        drivers = self.run_paths(show, steps, LOG_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertFalse(snap["in_library"])
        self.assertEqual(snap["stars"], {"show": (None, None), "seasons": {}, "episodes": {}})
        self.assertFalse(snap["hearts"]["show"])
        self.assertEqual(snap["hearts"]["seasons"], [])
        for driver in drivers.values():
            self.assertFalse(MediaLike.objects.filter(user=driver.user).exists())
            self.assertFalse(Activity.objects.filter(actor=driver.user, verb="rating_updated").exists())
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_21_future_log_dates_are_rejected_from_every_build(self):
        """TV-407.

        At 11 p.m. Pacific (2 a.m. in New York) the Pacific day is today and the New
        York day is the future; nothing changes.
        """
        show = self.make_show()

        def steps(driver):
            self.clock.set(datetime(2026, 10, 25, 23, 0, tzinfo=ZoneInfo(PACIFIC)))
            rejected = driver.log(show, 1, day=date(2026, 10, 26), expect=400)
            if driver.path == "web":
                self.assertEqual(rejected.json(), {"error": "Log dates can't be in the future."})
            else:
                self.assertEqual(rejected.data["error"]["code"], "validation_error")
            self.assertFalse(TV.objects.filter(user=driver.user).exists())
            driver.log(show, 1, day=OCT_25)  # the Pacific "today" is accepted
            entry = DiaryEntry.objects.get(user=driver.user)
            before = snapshot(driver.user, show)
            driver.edit_log(entry.pk, day=date(2026, 10, 26), expect=400)
            self.assertEqual(snapshot(driver.user, show), before)

        drivers = self.run_paths(show, steps, LOG_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual([log[0] for log in snap["logs"]], [("season", 1, None, "2026-10-25", 1)])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_32_season_stars_follow_the_source_rules(self):
        """TV-501.

        The log sets and links the stars; a direct change unlinks them; the edit then
        leaves the current stars alone.
        """
        show = self.make_show({1: 3, 2: 3})
        seen = {}

        def steps(driver):
            driver.log(show, 1, rating=8)
            seen.setdefault(driver.name, []).append(snapshot(driver.user, show)["stars"]["seasons"][1])
            driver.rate(show, 6, season=1)
            seen[driver.name].append(snapshot(driver.user, show)["stars"]["seasons"][1])
            entry = DiaryEntry.objects.get(user=driver.user)
            driver.edit_log(entry.pk, rating=4)

        drivers = self.run_paths(show, steps, ("api", "web"))

        snap = self.assert_same_state(show, drivers)
        log = ("season", 1, None, "2026-10-25", 1)
        self.assertEqual(seen["api"], [("8.0", log), ("6.0", None)])
        self.assertEqual(seen["web"], seen["api"])
        self.assertEqual(snap["stars"]["seasons"][1], ("6.0", None))
        self.assertEqual(snap["logs"][0][1], "4.0")
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_33_a_rated_season_log_never_sets_the_shows_stars(self):
        """TV-500: show stars stay as they were."""
        show = self.make_show({1: 3, 2: 3})

        def steps(driver):
            driver.log(show, 1)
            driver.rate(show, 9)
            driver.log(show, 2, rating=4)

        drivers = self.run_paths(show, steps, LOG_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["stars"]["show"], ("9.0", None))
        self.assertEqual(snap["stars"]["seasons"][2][0], "4.0")
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_56_rewatch_defaults_off_for_a_first_log_and_on_after_a_mark(self):
        """TV-404: off for Season 1's log; on for Season 2's after its eye marked it."""
        show = self.make_show({1: 3, 2: 3, 3: 3})
        api = self.driver("api")

        self.assertFalse(api.state(show, 1)["composer"]["targets"][0]["is_rewatch"])
        api.log(show, 1)  # is_rewatch omitted: the server applies the default
        api.mark(show, 2)
        self.assertTrue(api.state(show, 2)["composer"]["targets"][0]["is_rewatch"])
        api.log(show, 2)

        snap = self.snap_of(api, show)
        self.assertEqual([(log[0][1], log[3]) for log in snap["logs"]], [(1, False), (2, True)])

        def steps(driver):
            driver.log(show, 1, is_rewatch=False)
            driver.mark(show, 2)
            driver.log(show, 2, is_rewatch=True)

        drivers = {"api": api, **self.run_paths(show, steps, ("web",))}
        self.assert_same_state(show, drivers)
        self.assert_surfaces_agree_for(show, drivers)


# ---------------------------------------------------------------------------------
# Flow C: the whole show
# ---------------------------------------------------------------------------------

SIXTY_TWO = {0: 3, 1: 10, 2: 12, 3: 20, 4: 20}


class FlowCWholeShowTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """A-23 to A-28, A-44, A-47, A-49 to A-51, A-53."""

    def test_a_23_the_show_eye_marks_every_aired_regular_episode(self):
        """TV-300, TV-004.

        All 62 filled undated as one mark; Completed; every season "Watched"; no log or
        feed item; specials untouched.
        """
        show = self.make_show(SIXTY_TWO)
        prompts = []

        def steps(driver):
            if driver.path == "api":
                eye = driver.eye(show)
                prompts.append((eye["tap"], eye["prompt"]))
            if driver.path == "import":
                driver.import_rows([driver.row(show, "tv", status=COMPLETED)])
            elif driver.variant == "form":
                driver.write_status(show, COMPLETED)
            else:
                driver.mark(show)

        drivers = self.run_paths(
            show, steps, ("api", "installed", "web", "web#form", "hooks", "import"),
        )

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(prompts, [("mark_confirm", "Mark all 62 aired episodes watched?")])
        self.assertEqual(snap["status"], COMPLETED)
        self.assertEqual(snap["counts"], (62, 62))
        self.assertEqual(len(snap["viewings"]), 62)
        self.assertTrue(all(viewing[2:] == ("fill", "mark", None) for viewing in snap["viewings"]))
        self.assertNotIn(0, {viewing[0] for viewing in snap["viewings"]})  # specials untouched
        self.assertEqual({season["status"] for season in snap["seasons"].values()}, {COMPLETED})
        self.assertEqual(sorted(snap["seasons"]), [1, 2, 3, 4])
        self.assertEqual((snap["logs"], snap["activity"]), ([], {}))
        cards = {card["season_number"]: card["tv"]["state"] for card in drivers["api"].detail(show).data["seasons"]}
        self.assertEqual(cards, {1: "watched", 2: "watched", 3: "watched", 4: "watched", 0: "not_started"})
        self.assertEqual(len(drivers["api"].library(media_type="tv")), 1)  # one card
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_24_undoing_a_show_mark_removes_the_show(self):
        """TV-302, TV-208.

        The show leaves the library; its stars and heart are cleared.
        """
        show = self.make_show({1: 3, 2: 3})
        taps = []

        def steps(driver):
            driver.mark(show)
            driver.rate(show, 8)
            if driver.path == "api":
                driver.like(show)
                taps.append(driver.eye(show)["tap"])
            driver.unmark(show)

        drivers = self.run_paths(show, steps, ("api", "web"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(taps, ["unmark_marks"])
        self.assertFalse(snap["in_library"])
        self.assertEqual(snap["stars"]["show"], (None, None))
        self.assertFalse(snap["hearts"]["show"])
        self.assertEqual(snap["activity"], {})
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_25_undoing_the_show_eye_after_ticking_season_1(self):
        """TV-302: Seasons 2 on are unmarked; Season 1 stays ticked; S2 E1 is next."""
        show = self.make_show({1: 3, 2: 3, 3: 3})

        def steps(driver):
            ticks(driver, show, 1, (1, 2, 3))
            driver.mark(show)
            driver.unmark(show)

        drivers = self.run_paths(show, steps, ("api", "web"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["watched_all"], positions(1, (1, 2, 3)))
        self.assertTrue(all(viewing[2] == "tick" for viewing in snap["viewings"]))
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["up_next"][:2], (2, 1))
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_26_the_eye_of_a_show_finished_by_ticking(self):
        """TV-302 case 3.

        Without logs it asks "Mark all 6 episodes unwatched?"; with a log under the show
        the option is unavailable.
        """
        show = self.make_show({1: 3, 2: 3})
        logged = self.make_show({1: 3, 2: 3})
        api = self.driver("api")
        for target in (show, logged):
            for season in (1, 2):
                ticks(api, target, season, (1, 2, 3))
        api.log(logged, 1, 2)

        eye = api.eye(show)
        blocked = api.eye(logged)
        before = self.snap_of(api, logged)
        refused = api.action(logged, "unmark", confirm=True, expected_version=api.state(logged)["version"], expect=409)

        self.assertEqual((eye["tap"], eye["prompt"], eye["count"]), ("unmark_confirm", "Mark all 6 episodes unwatched?", 6))
        self.assertEqual((blocked["tap"], blocked["message"]), ("blocked", "Delete the logs first."))
        self.assertEqual(refused.data["error"]["message"], "Delete the logs first.")
        self.assertEqual(self.snap_of(api, logged), before)

        api.unmark(show)
        self.assertFalse(self.snap_of(api, show)["in_library"])

        def steps(driver):
            for season in (1, 2):
                ticks(driver, show, season, (1, 2, 3))
            driver.unmark(show)

        drivers = {"api": api, **self.run_paths(show, steps, ("web",))}
        self.assert_same_state(show, drivers)

    def test_a_27_a_show_log_blocks_the_show_eye(self):
        """TV-302 case 1.

        Nothing changes, and the eye says "Delete your show log first."
        """
        show = self.make_show({1: 3, 2: 3})
        before = {}

        def steps(driver):
            driver.log(show)
            before[driver.name] = snapshot(driver.user, show)
            if driver.path == "api":
                eye = driver.unmark(show)  # the app shows the eye's message and sends nothing
                self.assertEqual((eye["tap"], eye["message"]), ("blocked", "Delete your show log first."))
                refused = driver.action(show, "unmark", expect=409)
                self.assertEqual(refused.data["error"]["code"], "tv_log_blocks")
                self.assertEqual(refused.data["error"]["message"], "Delete your show log first.")
            else:
                refused = driver.unmark(show, expect=409)
                self.assertEqual(refused.json(), {"error": "Delete your show log first."})

        drivers = self.run_paths(show, steps, ("api", "web"))

        snap = self.assert_same_state(show, drivers)
        for path, driver in drivers.items():
            self.assertEqual(self.snap_of(driver, show), before[path])
        self.assertEqual(snap["status"], COMPLETED)
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_28_a_heart_on_an_unstarted_show_marks_it_from_either_build(self):
        """TV-303, TV-900.

        Marked watched, Completed and hearted; only the new build's eye can undo the
        mark.
        """
        show = self.make_show({1: 3, 2: 3})

        def steps(driver):
            driver.like(show)  # the optional star picker is dismissed: nothing is sent

        drivers = self.run_paths(show, steps, ("api", "installed"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], COMPLETED)
        self.assertTrue(snap["hearts"]["show"])
        self.assertEqual(snap["hearts"]["show_source"], (None, True))
        self.assertTrue(all(viewing[2:] == ("fill", "mark", None) for viewing in snap["viewings"]))
        self.assertEqual(len(snap["viewings"]), 6)
        self.assertEqual(snap["activity"], {})  # a heart creates no feed activity
        self.assert_surfaces_agree_for(show, drivers)

        api, installed = drivers["api"], drivers["installed"]
        self.assertEqual(api.eye(show)["tap"], "unmark_marks")
        api.unmark(show)
        self.assertFalse(self.snap_of(api, show)["in_library"])
        with self.assertRaises(Unsupported):
            installed.unmark(show)  # installed builds' show eye does nothing
        self.assertEqual(self.snap_of(installed, show)["status"], COMPLETED)

    def test_a_44_remove_from_library_with_and_without_logs(self):
        """TV-003.

        Blocked by logs; otherwise asks with its count, removes everything, clears stars
        and hearts and keeps custom lists.
        """
        show = self.make_show({1: 3, 2: 3})
        counts = {}

        def steps(driver):
            ticks(driver, show, 1, (1, 2))
            entry_id = None
            driver.log(show, 2)
            entry_id = DiaryEntry.objects.get(user=driver.user).pk
            custom_list = CustomList.objects.create(owner=driver.user, name="Favourites")
            CustomListItem.objects.create(custom_list=custom_list, item=show)
            before = snapshot(driver.user, show)
            if driver.path == "api":
                state = driver.state(show)
                self.assertNotIn("remove", state["available_actions"])
                self.assertEqual(state["action_reasons"]["remove"], "Delete this show's logs first.")
                refused = driver.action(show, "remove", expected_version=state["version"], expect=409)
                self.assertEqual(refused.data["error"]["code"], "tv_log_blocks")
            elif driver.path == "installed":
                refused = driver.remove(show, expect=409)
                self.assertEqual(refused.data["error"]["message"], "Delete this show's logs first.")
            else:
                driver.remove(show)
            self.assertEqual(snapshot(driver.user, show), before)
            driver.delete_log(entry_id)
            driver.rate(show, 8)
            if driver.path != "web":
                driver.like(show)
            if driver.path == "api":
                counts["remove"] = driver.state(show)["remove_count"]
            driver.remove(show)
            self.assertTrue(CustomListItem.objects.filter(custom_list=custom_list, item=show).exists())

        drivers = self.run_paths(show, steps, LOG_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(counts["remove"], 2)  # "Your 2 watched episodes will be unmarked."
        self.assertFalse(snap["in_library"])
        self.assertEqual(snap["stars"]["show"], (None, None))
        self.assertFalse(snap["hearts"]["show"])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_47_a_700_episode_show_is_marked_and_unmarked_in_bounded_queries(self):
        """TV-301, TV-302, TV-207.

        Marking and undoing are one request each, with a number of queries that doesn't
        grow with the episodes and no provider call per episode; the Library row of the
        marked show reads the stored counts.
        """
        def spec(seasons, per_season):
            first = date(1990, 1, 1)
            return {
                number: weekly(first + timedelta(days=200 * number), per_season(number))
                for number in range(1, seasons + 1)
            }

        small = self.make_show(spec(3, lambda _number: 10))
        big = self.make_show(spec(30, lambda number: 23 + (number > 20)))
        self.assertEqual(self.fake.show(big.media_id).episode_count(), 700)

        measured = {}
        for name, show in (("small", small), ("big", big)):
            api = self.driver("api")
            api.detail(show)  # the stored listing, as opening the page leaves it
            tv_tracking.prepare_items(api.user, show, tv_listing.get_listing(show))
            self.fake.reset_calls()
            with CaptureQueriesContext(connection) as marking:
                api.mark(show)
            mark_calls = self.fake.count()
            self.assertEqual(snapshot(api.user, show)["counts"][0], self.fake.show(show.media_id).episode_count())
            with CaptureQueriesContext(connection) as listing:
                api.library(media_type="tv")
            with CaptureQueriesContext(connection) as unmarking:
                api.unmark(show)
            measured[name] = {
                "mark": len(marking),
                "list": len(listing),
                "unmark": len(unmarking),
                "provider_calls": (mark_calls, self.fake.count()),
            }
            self.assertFalse(snapshot(api.user, show)["in_library"])

        small_counts, big_counts = measured["small"], measured["big"]
        # Bulk inserts batch by the database's parameter limit; allow those batches.
        batches = math.ceil(700 / 100)
        self.assertLessEqual(big_counts["mark"] - small_counts["mark"], 2 * batches, measured)
        self.assertLessEqual(big_counts["unmark"] - small_counts["unmark"], 2 * batches, measured)
        self.assertEqual(big_counts["list"], small_counts["list"], measured)
        self.assertEqual(big_counts["provider_calls"], (0, 0), measured)

    def test_a_49_a_season_log_keeps_its_season_when_the_show_eye_is_undone(self):
        """TV-302, TV-400.

        Every season but Season 2 is unmarked; Season 2's fills now belong to its log.
        """
        show = self.make_show({1: 3, 2: 3, 3: 3})

        def steps(driver):
            driver.mark(show)
            # Season 2 is watched only by marks, so the server's Rewatch default is on
            # (TV-404); both paths save the toggle on.
            driver.log(show, 2, is_rewatch=True)
            driver.unmark(show)

        drivers = self.run_paths(show, steps, ("api", "web"))

        snap = self.assert_same_state(show, drivers)
        log = ("season", 2, None, "2026-10-25", 1)
        self.assertEqual(snap["viewings"], [(2, episode, "fill", log, None) for episode in (1, 2, 3)])
        self.assertEqual(snap["watched_all"], positions(2, (1, 2, 3)))
        self.assertEqual(snap["status"], WATCHING)
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_50_undoing_a_season_eye_after_a_show_mark(self):
        """TV-302.

        Only Season 2 is unmarked, with no dialog; Watching with S2 E1 next.
        """
        show = self.make_show({1: 3, 2: 3, 3: 3})
        taps = []

        def steps(driver):
            driver.mark(show)
            if driver.path == "api":
                taps.append(driver.eye(show, 2)["tap"])
            driver.unmark(show, 2)

        drivers = self.run_paths(show, steps, ("api", "installed", "web"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(taps, ["unmark_marks"])
        self.assertEqual(snap["watched_all"], [*positions(1, (1, 2, 3)), *positions(3, (1, 2, 3))])
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["up_next"][:2], (2, 1))
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_51_season_eyes_then_the_show_eye(self):
        """TV-302 case 2.

        Seasons 2 to 5 are unmarked with no dialog; Season 1 stays ticked.
        """
        show = self.make_show(dict.fromkeys(range(1, 6), 2))
        taps = []

        def steps(driver):
            ticks(driver, show, 1, (1, 2))
            for season in (2, 3, 4, 5):
                driver.mark(show, season)
            if driver.path == "api":
                taps.append(driver.eye(show)["tap"])
            driver.unmark(show)

        drivers = self.run_paths(show, steps, ("api", "web"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(taps, ["unmark_marks"])
        self.assertEqual(snap["viewings"], [(1, 1, "tick", None, "2026-10-25"), (1, 2, "tick", None, "2026-10-25")])
        self.assertEqual(snap["up_next"][:2], (2, 1))
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_53_a_heart_mid_season_then_the_eye(self):
        """TV-202, TV-300, TV-302, TV-208.

        Watching and caught up with the eye filled; the tap removes the mark and the
        show, and clears the heart.
        """
        show = self.make_show({1: weekly(date(2026, 9, 17), 8)}, status="Returning Series")

        def steps(driver):
            driver.like(show)

        drivers = self.run_paths(show, steps, ("api", "installed"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], WATCHING)
        self.assertTrue(snap["caught_up"])
        self.assertEqual(snap["next_airing"], (1, 7, OCT_29))
        self.assertEqual(len(snap["viewings"]), 6)
        api = drivers["api"]
        eye = api.eye(show)
        self.assertEqual((eye["filled"], eye["tap"]), (True, "unmark_marks"))
        self.assert_surfaces_agree_for(show, drivers)

        api.unmark(show)

        after = self.snap_of(api, show)
        self.assertFalse(after["in_library"])
        self.assertFalse(after["hearts"]["show"])


# ---------------------------------------------------------------------------------
# Flow D: rewatch
# ---------------------------------------------------------------------------------


class FlowDRewatchTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """A-19, A-35, A-36."""

    def test_a_19_logging_a_ticked_episode_with_rewatch_on_adds_a_viewing(self):
        """TV-402, TV-603: a second viewing is added; nothing reopens."""
        show = self.make_show({1: 2})

        def steps(driver):
            ticks(driver, show, 1, (1, 2))
            driver.log(show, 1, 2, is_rewatch=True, rating=8)

        drivers = self.run_paths(show, steps, ("api", "installed"))

        snap = self.assert_same_state(show, drivers)
        log = ("episode", 1, 2, "2026-10-25", 1)
        self.assertEqual(
            snap["viewings"],
            [(1, 1, "tick", None, "2026-10-25"), (1, 2, "log", log, "2026-10-25"), (1, 2, "tick", None, "2026-10-25")],
        )
        self.assertEqual(snap["status"], COMPLETED)
        self.assertEqual(snap["seasons"][1]["status"], COMPLETED)
        self.assertIsNone(snap["rewatch"])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_35_rewatching_a_finished_show(self):
        """TV-600, TV-601, TV-602, TV-403.

        "Rewatching · S1 E1 next", each season's composer opens with Rewatch on, and the
        show returns to Completed.
        """
        show = self.make_show({1: 2, 2: 2})
        offers = []

        def steps(driver):
            app = driver if driver.path == "api" else self.api(driver.user)
            app.mark(show)
            app.rewatch(show)
            if driver.path == "api":
                state = driver.state(show)
                self.assertEqual(state["status"], WATCHING)
                self.assertEqual(state["rewatch"]["scope"], "show")
                self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 1))
                self.assertEqual(state["watched_count"], 0)
            for season in (1, 2):
                last = ticks(driver, show, season, (1, 2))
                if driver.path == "api":
                    offers.append(last.data["finished"])

        drivers = self.run_paths(show, steps, ("api", "web"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual([offer["season_number"] for offer in offers], [1, 2])
        self.assertTrue(all(offer["composer"]["targets"][0]["is_rewatch"] for offer in offers))
        self.assertTrue(offers[1]["show_finished"])
        self.assertIn("show", [target["id"] for target in offers[1]["composer"]["targets"]])
        self.assertEqual(snap["status"], COMPLETED)
        self.assertIsNone(snap["rewatch"])
        self.assertEqual(len(snap["viewings"]), 8)  # the first pass's fills stay as history
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_36_stopping_a_rewatch_midway_keeps_what_was_watched(self):
        """TV-602: Completed; the rewatch's tick is kept."""
        show = self.make_show({1: 2, 2: 2})
        api = self.driver("api")
        api.mark(show)
        api.rewatch(show)
        api.tick(show, 1, 1)
        self.assertEqual(api.state(show)["status"], WATCHING)

        api.stop_rewatch(show)

        snap = self.snap_of(api, show)
        self.assertEqual(snap["status"], COMPLETED)
        self.assertIsNone(snap["rewatch"])
        self.assertIn((1, 1, "tick", None, "2026-10-25"), snap["viewings"])
        self.assertEqual(len(snap["viewings"]), 5)
        self.assert_surfaces_agree(api.user, show)


# ---------------------------------------------------------------------------------
# Flow E: pause, drop, planning
# ---------------------------------------------------------------------------------


class FlowEChoiceTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """A-37, A-38, A-39, A-41, A-54, A-57."""

    def test_a_37_unticking_a_mistaken_tick_restores_paused(self):
        """TV-007, TV-208: Watching after the tick, Paused again after the untick."""
        show = self.make_show({1: 3, 2: 6})
        seen = {}

        def steps(driver):
            chooser = driver if driver.path != "webhook" else self.api(driver.user)
            ticks(driver, show, 1, (1, 2, 3))
            ticks(driver, show, 2, (1, 2))
            chooser.choose(show, "pause")
            driver.tick(show, 2, 4)
            middle = snapshot(driver.user, show)
            driver.untick(show, 2, 4)
            seen[driver.name] = (middle["status"], middle["ended_choice"])

        drivers = self.run_paths(show, steps, TICK_PATHS)

        snap = self.assert_same_state(show, drivers)
        # A viewing created through the model (the admin) is a fact as saved; working
        # it out never changes a choice (plan 1.2, Hooks), so only taps, the web and
        # webhooks end Paused in between.
        for path, middle in seen.items():
            if path != "hooks":
                self.assertEqual(middle, (WATCHING, PAUSED), path)
        self.assertEqual(snap["status"], PAUSED)
        self.assertEqual(snap["choice"], PAUSED)
        self.assertEqual(snap["up_next"][:2], (2, 3))  # "Paused at S2 E3"
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_38_a_dropped_show_stays_dropped_with_no_events(self):
        """TV-203, TV-704: a new episode airing moves nothing and notifies no one."""
        show = self.make_show({1: [*weekly(date(2026, 10, 1), 4), OCT_29]}, status="Returning Series")
        season_item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=SEASON_TYPE,
            season_number=1,
            defaults={"title": show.title, "image": show.image},
        )
        event = Event.objects.create(item=season_item, content_number=5, datetime=at_pacific(OCT_29, 21))

        def steps(driver):
            driver.tick(show, 1, 1)
            driver.choose(show, "drop")

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "hooks"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], DROPPED)
        self.assert_surfaces_agree_for(show, drivers)
        self.clock.set(datetime(2026, 10, 30, 9, 0, tzinfo=ZoneInfo(PACIFIC)))
        self.run_job()
        after = self.assert_same_state(show, drivers)
        self.assertEqual(after["status"], DROPPED)
        self.assertEqual(after["choice"], DROPPED)
        targets = {(event.item_id, event.content_number): event}
        for driver in drivers.values():
            events = Event.objects.get_user_events(driver.user, date(2026, 10, 20), date(2026, 11, 20))
            self.assertNotIn(event, set(events))
            self.assertEqual(get_user_releases([driver.user], targets).get(driver.user.id, []), [])

    def test_tv_705_a_held_shows_progress_agrees_everywhere_after_an_episode_airs(self):
        """TV-705, TV-203.

        A Dropped (or Paused or Planning) show doesn't move when an episode airs, but
        its progress still reads the same on the Library card and the show page ("1 of
        5" on both, not "1 of 4" on the card).
        """
        show = self.make_show({1: [*weekly(date(2026, 10, 1), 4), OCT_29]}, status="Returning Series")
        api = self.driver("api")
        api.tick(show, 1, 1)
        api.choose(show, "drop")
        self.clock.set(datetime(2026, 10, 30, 9, 0, tzinfo=ZoneInfo(PACIFIC)))
        self.run_job()

        self.assertEqual(api.state(show)["aired_count"], 5)
        self.assertEqual(api.state(show)["status"], DROPPED)
        self.assert_surfaces_agree(api.user, show)

    def test_a_39_planning_is_unavailable_once_started(self):
        """TV-006: unavailable, with its reason; nothing changes."""
        show = self.make_show()
        reason = "You've started this show. Pause or drop it instead."
        before = {}

        def steps(driver):
            driver.tick(show, 1, 1)
            before[driver.name] = snapshot(driver.user, show)
            if driver.path == "api":
                state = driver.state(show)
                self.assertNotIn("planning", state["available_actions"])
                self.assertEqual(state["action_reasons"]["planning"], reason)
                refused = driver.choose(show, "planning", expect=409)
                self.assertEqual(refused.data["error"]["code"], "tv_action_unavailable")
                self.assertEqual(refused.data["error"]["message"], reason)
            elif driver.path == "installed":
                refused = driver.choose(show, "planning", expect=409)
                self.assertEqual(refused.data["error"]["message"], reason)
            else:
                driver.choose(show, "planning")

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "hooks"))

        snap = self.assert_same_state(show, drivers)
        for path, driver in drivers.items():
            self.assertEqual(pick(self.snap_of(driver, show), ALL_KEYS[:16]), pick(before[path], ALL_KEYS[:16]), path)
        self.assertEqual(snap["status"], WATCHING)
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_41_a_play_that_isnt_a_watch(self):
        """TV-802, TV-203.

        A Completed show doesn't change; a Paused one moves to Watching; neither gets a
        viewing or season tracking.
        """
        completed = self.make_show({1: 2, 2: 2})
        paused = self.make_show({1: 2, 2: 2})
        before = {}

        def steps(driver):
            app = self.api(driver.user)
            app.mark(completed)
            app.tick(paused, 1, 1)
            app.choose(paused, "pause")
            before[driver.name] = snapshot(driver.user, completed)
            driver.play(completed, 1, 1)
            driver.play(paused, 1, 2)

        drivers = self.run_paths(completed, steps, WEBHOOK_VENDORS)

        for path, driver in drivers.items():
            self.assertEqual(self.snap_of(driver, completed), before[path], path)
        snap = self.assert_same_state(paused, drivers, reference="webhook:plex")
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["viewings"], [(1, 1, "tick", None, "2026-10-25")])
        self.assertEqual(snap["season_rows"], [1])
        self.assert_surfaces_agree_for(paused, drivers)

    def test_a_54_watching_after_dropping_a_finished_show_is_completed(self):
        """TV-005, TV-007: a finished show returns to Completed."""
        show = self.make_show({1: 3})

        def steps(driver):
            driver.mark(show)
            driver.choose(show, "drop")
            self.assertEqual(snapshot(driver.user, show)["status"], DROPPED)
            driver.choose(show, "watching")

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "hooks"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], COMPLETED)
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_57_a_season_dropped_is_rejected_by_the_api_and_ignored_elsewhere(self):
        """TV-207, TV-204, TV-800.

        400 from the API; the web and imports ignore it; the show stays Watching.
        """
        show = self.make_show({1: 3, 2: 3})

        def steps(driver):
            if driver.path == "import":
                driver.import_rows([
                    driver.row(show, "tv", status=WATCHING),
                    driver.row(show, SEASON_TYPE, season=1, status=DROPPED),
                    driver.row(show, EPISODE_TYPE, season=1, episode=1, end_date=self.clock.advance(minutes=1)),
                ])
                return
            driver.tick(show, 1, 1)
            if driver.path in ("api", "installed"):
                refused = driver.write_status(show, DROPPED, season=1, expect=400)
                self.assertEqual(refused.data["error"]["code"], "validation_error")
            else:
                driver.write_status(show, DROPPED, season=1)

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "hooks", "import"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["seasons"][1]["status"], WATCHING)
        self.assert_surfaces_agree_for(show, drivers)


# ---------------------------------------------------------------------------------
# Flow F: a new season airs
# ---------------------------------------------------------------------------------


class FlowFNewEpisodesTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """A-09, A-12, A-42, A-60, A-62."""

    def test_a_09_caught_up_between_seasons_then_the_next_season_airs(self):
        """TV-202, TV-206, TV-701.

        Completed and caught up until S3 E1 airs, then Watching with "S3 E1 · New" on
        Continue Watching.
        """
        show = self.make_show(
            {1: 2, 2: 2, 3: [MAR_12]},
            status="Returning Series",
            next_episode_to_air={"season_number": 3, "episode_number": 1, "air_date": MAR_12},
        )

        def steps(driver):
            if driver.path == "import":
                plays = [
                    (season, episode, self.clock.advance(minutes=1)) for season in (1, 2) for episode in (1, 2)
                ]
                driver.import_rows(driver.plays(show, plays))
                return
            for season in (1, 2):
                ticks(driver, show, season, (1, 2))

        drivers = self.run_paths(show, steps, (*TICK_PATHS, "import"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], COMPLETED)
        self.assertTrue(snap["caught_up"])
        self.assertEqual(snap["next_airing"], (3, 1, MAR_12))  # "Caught up · S3 on Mar 12"
        self.assert_surfaces_agree_for(show, drivers)

        self.clock.set(datetime(2027, 3, 12, 9, 0, tzinfo=ZoneInfo(PACIFIC)))
        self.run_job()

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["up_next"], (3, 1, MAR_12, True))  # "S3 E1 · New"
        self.assertFalse(snap["caught_up"])
        self.assertEqual(snap["season_rows"], [1, 2])  # TV-205
        for driver in drivers.values():
            self.assertIn(show.media_id, self.api(driver.user).continue_watching())
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_12_an_aired_episode_added_to_a_finished_season_reopens_it(self):
        """TV-206: the season reopens and the show moves to Watching."""
        show = self.make_show({1: 3})

        def steps(driver):
            ticks(driver, show, 1, (1, 2, 3))

        drivers = self.run_paths(show, steps, TICK_PATHS)
        self.assertEqual(self.assert_same_state(show, drivers)["status"], COMPLETED)

        self.fake.show(show.media_id).add_episode(1, 4, OCT_20)
        self.clock.advance(minutes=1)
        with self.captureOnCommitCallbacks(execute=True):
            tv_tracking.refresh_after_calendar(show)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["seasons"][1]["status"], WATCHING)
        # E4 aired before the latest viewing, so it isn't marked new (TV-701).
        self.assertEqual(snap["up_next"][:3], (1, 4, OCT_20))
        self.assertEqual(snap["counts"], (3, 4))
        for driver in drivers.values():
            self.assertIn(show.media_id, self.api(driver.user).continue_watching())
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_42_a_hollow_legacy_show_is_worked_out_by_the_users_first_tick(self):
        """TV-801, TV-101.

        The hourly job leaves it as stored; after the tick it is still Completed: the
        legacy mark fills it, and the tick records nothing.
        """
        show = self.make_show({1: 3, 2: 3})
        stored = {}

        def legacy_row(user):
            TV.objects.bulk_create([TV(user=user, item=show, status=COMPLETED, legacy_pending=True)])

        def steps(driver):
            legacy_row(driver.user)
            self.run_job()
            stored[driver.name] = snapshot(driver.user, show)
            driver.tick(show, 1, 1)

        drivers = self.run_paths(show, steps, ("api", "installed", "web"))

        for path, before in stored.items():
            self.assertTrue(before["legacy_pending"], path)
            self.assertEqual((before["status"], before["viewings"]), (COMPLETED, []), path)
        snap = self.assert_same_state(show, drivers)
        self.assertFalse(snap["legacy_pending"])
        self.assertEqual(snap["status"], COMPLETED)
        self.assertEqual(len(snap["viewings"]), 6)
        self.assertTrue(all(viewing[2:] == ("fill", "mark", None) for viewing in snap["viewings"]))
        self.assert_surfaces_agree_for(show, drivers)

        # A webhook records its fact on such a show without working it out (TV-801).
        hook = self.driver("webhook")
        legacy_row(hook.user)
        hook.tick(show, 1, 1)
        after = self.snap_of(hook, show)
        self.assertTrue(after["legacy_pending"])
        self.assertEqual((after["status"], after["viewings"]), (COMPLETED, [(1, 1, "tick", None, "2026-10-25")]))

    def test_a_60_a_9pm_pacific_tick_is_the_pacific_day_and_the_job_leaves_it(self):
        """TV-100, TV-103, TV-207.

        With the server on New York (midnight, Oct 26) the tick's day is Oct 25 on the
        account's Los Angeles calendar; E2 airing on Oct 26 hasn't aired for the
        account, so the hourly job changes nothing.
        """
        show = self.make_show({1: [date(2026, 10, 18), OCT_26]}, status="Returning Series")

        def steps(driver):
            if driver.path == "import":
                driver.import_rows(driver.plays(show, [(1, 1, self.clock.moment)]))
            elif driver.variant == "form":
                driver.episode_form(show, 1, 1, OCT_25)  # the episode form's typed day
            else:
                driver.tick(show, 1, 1)

        drivers = self.run_paths(show, steps, (*TICK_PATHS, "web#form", "import"), start=at_pacific(OCT_25, 21))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["viewings"], [(1, 1, "tick", None, "2026-10-25")])
        self.assertEqual(snap["status"], WATCHING)
        self.assertTrue(snap["caught_up"])
        self.assertEqual(snap["next_airing"], (1, 2, OCT_26))
        before = {path: snapshot(driver.user, show) for path, driver in drivers.items()}

        self.run_job()

        for path, driver in drivers.items():
            self.assertEqual(snapshot(driver.user, show), before[path], path)
        self.assert_surfaces_agree_for(show, drivers)

        # An account with no stored calendar gets the zone the app sends (the header).
        fresh = make_user(calendar="")
        app = APIDriver(self, fresh)
        app.tick(show, 1, 1)
        fresh.refresh_from_db()
        self.assertEqual(fresh.calendar_timezone, PACIFIC)
        self.assertEqual(snapshot(fresh, show)["viewings"], [(1, 1, "tick", None, "2026-10-25")])

    def test_a_62_a_season_the_provider_stops_listing_keeps_its_history(self):
        """TV-103.

        Its viewings and stars stay; it counts toward nothing and shows after Specials;
        writes to the show still work.
        """
        show = self.make_show({0: 1, 1: 2, 2: 2, 3: 2})
        drivers = {"api": self.driver("api"), "web": self.driver("web")}
        for path, driver in drivers.items():
            driver.tick(show, 1, 1)
            response = driver.tick(show, 3, 1)
            if path == "api":
                driver.answer(show, response.data["earlier_unwatched"], yes=False)
            driver.rate(show, 6, season=3)

        self.fake.show(show.media_id).remove_season(3)
        self.clock.advance(days=2)
        cache.clear()
        for driver in drivers.values():
            driver.tick(show, 1, 2)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["counts"], (2, 4))
        self.assertIn((3, 1, "tick", None, "2026-10-25"), snap["viewings"])
        self.assertEqual(snap["stars"]["seasons"][3], ("6.0", None))
        page = drivers["api"].detail(show).data
        self.assertEqual([season["season_number"] for season in page["seasons"]], [1, 2, 0])
        self.assertEqual([season["season_number"] for season in page["tv_unlisted_seasons"]], [3])
        self.assertFalse(page["tv_unlisted_seasons"][0]["tv"]["listed"])
        self.assertEqual(page["tv_unlisted_seasons"][0]["tv"]["rating"], "6.0")
        self.assert_surfaces_agree_for(show, drivers)




@override_settings(TIME_ZONE="UTC")
class FlowBSeasonsOnlyUTCServerTests(FlowBSeasonsOnlyTests):
    """Flow B with the server on UTC.

    At 11 p.m. Pacific it is already Oct 26 in UTC too, and the account's calendar still
    decides (TV-407).
    """


@override_settings(TIME_ZONE="UTC")
class FlowFNewEpisodesUTCServerTests(FlowFNewEpisodesTests):
    """Flow F with the server on UTC.

    The 9 p.m. Pacific tick and the hourly job still go by the account's calendar
    (TV-100, TV-206).
    """


# ---------------------------------------------------------------------------------
# Flow G: starting partway, specials, manual shows
# ---------------------------------------------------------------------------------


class FlowGStartingPartwayTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """A-14, A-15, A-40, A-52, A-55, A-58, A-63."""

    def test_a_14_logging_season_3_first_asks_once_about_seasons_1_and_2(self):
        """TV-107.

        Yes fills Seasons 1 and 2 undated; No fills nothing and never asks again.
        """
        yes_show = self.make_show({1: 2, 2: 2, 3: 2})
        no_show = self.make_show({1: 2, 2: 2, 3: 2})
        api = self.driver("api")

        asked = api.log(yes_show, 3).data["earlier_unwatched"]
        self.assertEqual(
            asked,
            {
                "from": {"season_number": 1, "episode_number": 1},
                "to": {"season_number": 2, "episode_number": 2},
                "count": 4,
                "seasons": [1, 2],
                "question": "Also mark Seasons 1–2 watched?",  # noqa: RUF001 - the contract's copy
                "offer_season_number": None,
            },
        )
        api.answer(yes_show, asked, yes=True)
        yes = self.snap_of(api, yes_show)
        self.assertEqual(yes["status"], COMPLETED)
        marked = [viewing for viewing in yes["viewings"] if viewing[0] in (1, 2)]
        self.assertEqual(marked, [(season, episode, "fill", "mark", None) for season in (1, 2) for episode in (1, 2)])

        self.assertIsNotNone(api.log(no_show, 3).data["earlier_unwatched"])  # answered No
        self.assertIsNone(api.tick(no_show, 1, 1).data["earlier_unwatched"])
        self.assertIsNone(api.log(no_show, 2).data["earlier_unwatched"])

        def steps(driver):
            driver.log(no_show, 3)
            if driver.path != "api":
                # Installed builds and the web never ask, and their first write uses the
                # question up (TV-107).
                self.assertIsNone(self.api(driver.user).tick(no_show, 1, 1).data["earlier_unwatched"])
            else:
                driver.tick(no_show, 1, 1)
            driver.log(no_show, 2)

        drivers = {"api": api, **self.run_paths(no_show, steps, ("installed", "web"))}
        snap = self.assert_same_state(no_show, drivers, keys=tuple(key for key in ALL_KEYS if key != "logs"))
        self.assertEqual(snap["watched_all"], [(1, 1), *positions(2, (1, 2)), *positions(3, (1, 2))])
        self.assert_surfaces_agree_for(no_show, drivers)

    def test_a_15_starting_at_s3_e1_and_declining_the_question(self):
        """TV-105, TV-107, TV-201.

        S3 E2 is next, then S1 E1; the show isn't finished.
        """
        show = self.make_show({1: 2, 2: 2, 3: 3})
        firsts = {}

        def steps(driver):
            response = driver.tick(show, 3, 1)
            if driver.path == "api":
                # A tick names the episodes (TV-107's own example for S3 E1).
                self.assertEqual(response.data["earlier_unwatched"]["question"], "Also mark S1 E1–S2 E2 watched?")  # noqa: RUF001 - the contract's copy
                self.assertEqual(response.data["earlier_unwatched"]["offer_season_number"], 3)
                driver.answer(show, response.data["earlier_unwatched"], yes=False)
            firsts[driver.name] = snapshot(driver.user, show)["up_next"][:2]
            ticks(driver, show, 3, (2, 3))

        drivers = self.run_paths(show, steps, TICK_PATHS)

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(set(firsts.values()), {(3, 2)})
        self.assertEqual(snap["up_next"][:2], (1, 1))
        self.assertFalse(snap["finished"])
        self.assertEqual(snap["status"], WATCHING)
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_40_a_special_starts_the_show_but_counts_for_nothing(self):
        """TV-004, TV-006, TV-304.

        Watching with S1 E1 next and no progress; Planning is unavailable; a heart on
        the show is opinion only.
        """
        show = self.make_show({0: 2, 1: 3})

        def steps(driver):
            if driver.path == "import":
                driver.import_rows(driver.plays(show, [(0, 1, self.clock.advance(minutes=1))]))
                return
            driver.tick(show, 0, 1)
            if driver.path in ("api", "installed"):
                driver.like(show)

        drivers = self.run_paths(show, steps, (*TICK_PATHS, "import"))

        snap = self.assert_same_state(show, {path: drivers[path] for path in ("api", "installed")})
        self.assert_same_state(show, drivers, keys=tuple(key for key in ALL_KEYS if key != "hearts"))
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["up_next"][:2], (1, 1))
        self.assertEqual(snap["counts"], (0, 3))
        self.assertEqual(snap["viewings"], [(0, 1, "tick", None, "2026-10-25")])  # the heart marked nothing
        self.assertTrue(snap["hearts"]["show"])
        state = drivers["api"].state(show)
        self.assertEqual(state["action_reasons"]["planning"], "You've started this show. Pause or drop it instead.")
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_52_mark_watched_up_to_here_then_the_season_1_eye(self):
        """TV-108, TV-302: Season 1 is unmarked; S2 E1 to E4 stay watched."""
        show = self.make_show({1: 3, 2: 6})
        api = self.driver("api")
        episode = api.episode_state(show, 2, 4)
        self.assertTrue(episode["can_mark_up_to"])
        self.assertEqual(episode["mark_up_to_prompt"], "Mark S1 E1–S2 E4 watched?")  # noqa: RUF001 - the contract's copy

        response = api.mark_up_to(show, 2, 4)
        self.assertIsNone(response.data["finished"])  # never opens Log Season (TV-108)
        self.assertEqual(api.eye(show, 1)["tap"], "unmark_marks")
        api.unmark(show, 1)

        snap = self.snap_of(api, show)
        self.assertEqual(snap["watched_all"], positions(2, (1, 2, 3, 4)))
        self.assertEqual(snap["status"], WATCHING)
        self.assertEqual(snap["up_next"][:2], (2, 5))
        self.assertEqual((snap["logs"], snap["activity"]), ([], {}))
        self.assert_surfaces_agree(api.user, show)

    def test_a_55_ticking_s1_e10_first(self):
        """TV-107, TV-403.

        Yes fills E1 to E9 and opens the Season 1 composer once; No fills nothing and
        opens no composer.
        """
        yes_show = self.make_show()
        no_show = self.make_show()
        api = self.driver("api")

        first = api.tick(yes_show, 1, 10).data
        self.assertIsNone(first["finished"])
        asked = first["earlier_unwatched"]
        self.assertEqual((asked["question"], asked["count"], asked["seasons"]), ("Also mark S1 E1–E9 watched?", 9, None))  # noqa: RUF001 - the contract's copy
        self.assertEqual(asked["offer_season_number"], 1)
        yes = api.answer(yes_show, asked, yes=True).data
        self.assertEqual((yes["finished"]["level"], yes["finished"]["season_number"]), ("season", 1))
        self.assertEqual(yes["finished"]["composer"]["targets"][0]["date"], "2026-10-25")
        api.untick(yes_show, 1, 10)
        self.assertIsNone(api.tick(yes_show, 1, 10).data["finished"])  # offered once in the pass
        snap = self.snap_of(api, yes_show)
        self.assertEqual(snap["watched_all"], positions(1, range(1, 11)))
        self.assertEqual(snap["seasons"][1]["status"], COMPLETED)

        declined = api.tick(no_show, 1, 10).data
        api.answer(no_show, declined["earlier_unwatched"], yes=False)
        self.assertIsNone(declined["finished"])
        self.assertEqual(self.snap_of(api, no_show)["watched_all"], [(1, 10)])
        self.assert_surfaces_agree(api.user, yes_show)
        self.assert_surfaces_agree(api.user, no_show)

    def test_a_58_the_specials_eye_changes_nothing_else(self):
        """TV-004.

        The aired specials are marked; progress, up next and status are unchanged, and
        no composer opens.
        """
        show = self.make_show({0: 2, 1: 3})
        offers = []

        def steps(driver):
            driver.tick(show, 1, 1)
            before = snapshot(driver.user, show)
            if driver.path == "api":
                self.assertEqual(driver.eye(show, 0)["tap"], "mark")
            response = driver.mark(show, 0)
            if driver.path == "api":
                offers.append(response.data["finished"])
            after = snapshot(driver.user, show)
            self.assertEqual(
                pick(after, ("status", "counts", "up_next", "finished")),
                pick(before, ("status", "counts", "up_next", "finished")),
                driver.name,
            )

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "hooks"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(offers, [None])
        self.assertEqual(
            [viewing for viewing in snap["viewings"] if viewing[0] == 0],
            [(0, 1, "fill", "mark", None), (0, 2, "fill", "mark", None)],
        )
        self.assertEqual(self.season_card(drivers["api"], show, 0)["state"], "watched")
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_63_a_manual_show_with_no_air_dates(self):
        """TV-103.

        Every listed episode counts as aired; the eyes and season logs work. The show is
        real manual Items read through the manual provider.
        """
        show = make_manual_show(seasons={1: 3, 2: 2})
        self.assertEqual(show.source, Sources.MANUAL.value)

        def steps(driver):
            driver.mark(show, 1)
            middle = snapshot(driver.user, show)
            self.assertEqual((middle["status"], middle["counts"], middle["up_next"][:2]), (WATCHING, (3, 5), (2, 1)))
            driver.log(show, 2, rating=8)

        drivers = self.run_paths(show, steps, ("api", "web"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], COMPLETED)
        self.assertEqual(snap["counts"], (5, 5))
        self.assertEqual(snap["stars"]["seasons"][2][0], "8.0")
        # Only a show log blocks the show eye; Season 1's mark can be undone (TV-302).
        self.assertEqual(drivers["api"].eye(show)["tap"], "unmark_marks")
        self.assert_surfaces_agree_for(show, drivers)


# ---------------------------------------------------------------------------------
# Cross-cutting cases
# ---------------------------------------------------------------------------------

# Today's export header, pinned by the plan (A-66, TV-803).
EXPORT_HEADER = [
    "media_id", "source", "media_type", "title", "image", "season_number", "episode_number",
    "poster_accent_color", "total_pages", "release_date", "release_year", "runtime_minutes",
    "letterboxd_rating", "imdb_rating", "rotten_tomatoes_rating", "filter_metadata_updated_at",
    "score", "status", "notes", "start_date", "end_date", "progress", "liked", "direct_consumption",
    "like_is_independent", "legacy_archived", "completed_manually", "status_history",
    "imported_lifetime_minutes", "imported_lifetime_source", "undated_read_previous_tracked",
    "undated_read_previous_status", "undated_read_previous_override", "read_override_active",
    "read_override_previous_tracked", "read_override_previous_status", "created_at", "progressed_at",
]


class CrossCuttingTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """A-04, A-20, A-22, A-29 to A-31, A-34, A-43, A-45, A-46, A-59, A-65 to A-68."""

    def test_a_04_unticking_an_episode_a_season_log_filled_changes_nothing(self):
        """TV-102: nothing changes; the message names the season log."""
        show = self.make_show()
        message = "Delete your Season 1 log first."
        before = {}

        def steps(driver):
            logger = driver if driver.path in LOG_PATHS else self.api(driver.user)
            logger.log(show, 1, is_rewatch=False)
            before[driver.name] = snapshot(driver.user, show)
            if driver.path == "api":
                state = driver.episode_state(show, 1, 4)
                self.assertEqual((state["untick_viewing_id"], state["untick_message"]), (None, message))
                refused = driver.untick(show, 1, 4, expect=409)
                self.assertEqual((refused.data["error"]["code"], refused.data["error"]["message"]), ("tv_log_blocks", message))
            elif driver.path == "installed":
                self.assertEqual(driver.untick(show, 1, 4, expect=409).data["error"]["message"], message)
            elif driver.path == "web":
                self.assertEqual(driver.untick(show, 1, 4, expect=409).json(), {"error": message})
            else:
                driver.untick(show, 1, 4)  # acknowledged (TV-802)

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "webhook"))

        snap = self.assert_same_state(show, drivers)
        for path, driver in drivers.items():
            self.assertEqual(self.snap_of(driver, show), before[path], path)
        self.assertIn((1, 4, "fill", ("season", 1, None, "2026-10-25", 1), None), snap["viewings"])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_20_editing_an_episode_logs_date_moves_only_its_viewing(self):
        """TV-405: the tick keeps its day; the log's own viewing moves."""
        show = self.make_show()

        def steps(driver):
            self.clock.set(datetime(2026, 10, 20, 20, 0, tzinfo=ZoneInfo(PACIFIC)))
            driver.tick(show, 1, 1)
            self.clock.set(datetime(2026, 10, 22, 20, 0, tzinfo=ZoneInfo(PACIFIC)))
            driver.log(show, 1, 1, day=OCT_22, is_rewatch=True)
            self.clock.set(at_pacific(OCT_25))
            entry = DiaryEntry.objects.get(user=driver.user)
            driver.edit_log(entry.pk, day=date(2026, 10, 23))

        drivers = self.run_paths(show, steps, ("api", "installed"))

        snap = self.assert_same_state(show, drivers)
        log = ("episode", 1, 1, "2026-10-23", 1)
        self.assertEqual(snap["viewings"], [(1, 1, "log", log, "2026-10-23"), (1, 1, "tick", None, "2026-10-20")])
        self.assertEqual(snap["last_watched_on"], date(2026, 10, 23))
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_22_unlisted_or_unaired_episodes_are_rejected(self):
        """TV-106.

        A tick or log of an episode TMDB doesn't list, or one airing in three days, is
        rejected and changes nothing.
        """
        show = self.make_show({1: [date(2026, 10, 1), date(2026, 10, 28), date(2026, 11, 4)]})
        unlisted = "This episode isn't listed for Season 1."
        unaired = "This episode hasn't aired yet."

        def steps(driver):
            if driver.path in ("api", "installed"):
                for episode, message in ((9, unlisted), (2, unaired)):
                    refused = driver.tick(show, 1, episode, expect=400)
                    self.assertEqual(refused.data["error"]["message"], message)
                    logged = driver.log(show, 1, episode, expect=None)
                    self.assertIn(logged.status_code, (400, 404), logged.data)
                    if logged.status_code == 400:
                        self.assertEqual(logged.data["error"]["message"], message)
            elif driver.path == "web":
                for episode, message in ((9, unlisted), (2, unaired)):
                    self.assertEqual(driver.tick(show, 1, episode, expect=400).json(), {"error": message})
            else:
                driver.tick(show, 1, 9)
                driver.tick(show, 1, 2)

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "webhook"))

        snap = self.assert_same_state(show, drivers)
        self.assertFalse(snap["in_library"])
        self.assertEqual(snap["logs"], [])
        for driver in drivers.values():
            self.assertFalse(DiaryEntry.objects.filter(user=driver.user).exists())

    def test_a_22_an_unaired_episode_log_is_a_400_with_the_reason(self):
        """TV-106: the log of an episode airing in three days explains why."""
        show = self.make_show({1: [date(2026, 10, 1), date(2026, 10, 28)]})
        api = self.driver("api")

        refused = api.log(show, 1, 2, expect=400)

        self.assertEqual(refused.data["error"]["code"], "validation_error")
        self.assertEqual(refused.data["error"]["message"], "This episode hasn't aired yet.")
        self.assertFalse(self.snap_of(api, show)["in_library"])

    def test_a_29_stars_on_a_started_show_are_opinion_only(self):
        """TV-304, TV-703: status and up next are unchanged; rating activity on 0-10."""
        show = self.make_show({1: 3, 2: 6})
        before = {}

        def steps(driver):
            ticks(driver, show, 1, (1, 2, 3))
            ticks(driver, show, 2, (1, 2))
            before[driver.name] = snapshot(driver.user, show)
            driver.rate(show, 8)

        drivers = self.run_paths(show, steps, LOG_PATHS)

        snap = self.assert_same_state(show, drivers)
        for path, driver in drivers.items():
            keys = ("status", "up_next", "counts", "viewings", "seasons")
            self.assertEqual(pick(self.snap_of(driver, show), keys), pick(before[path], keys), path)
        self.assertEqual(snap["up_next"][:2], (2, 3))
        self.assertEqual(snap["stars"]["show"], ("8.0", None))
        self.assertEqual(snap["activity_detail"], [("rating_updated", "8.0", "public")])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_30_stars_on_an_unwatched_season_while_watching_another(self):
        """TV-303, TV-304.

        Opinion only; Season 4 stays unwatched; up next stays in S2.
        """
        show = self.make_show({1: 2, 2: 3, 3: 2, 4: 2})

        def steps(driver):
            ticks(driver, show, 1, (1, 2))
            driver.tick(show, 2, 1)
            driver.rate(show, 6, season=4)

        drivers = self.run_paths(show, steps, ("api", "web"))

        # The web's tracking form always carries a status; for a season with no tracking
        # it posts the show's, In progress, which chooses Watching (TV-207). That choice
        # shows nowhere while the show is started, so the paths agree on the rest.
        snap = self.assert_same_state(show, drivers, keys=tuple(key for key in ALL_KEYS if key != "choice"))
        self.assertEqual(snap["stars"]["seasons"][4], ("6.0", None))
        self.assertNotIn(4, snap["seasons"])  # not started (TV-205)
        self.assertEqual(snap["watched_all"], [(1, 1), (1, 2), (2, 1)])
        self.assertEqual(snap["up_next"][:2], (2, 2))
        self.assert_surfaces_agree_for(show, drivers)
        # Installed builds send season stars without a season number: a 400, not a 500.
        installed = self.driver("installed", user=drivers["api"].user)
        refused = installed.request(
            "patch",
            f"/api/v1/tracking/{show.source}/season/{show.media_id}/",
            {"rating": 6},
            expect=400,
        )
        self.assertEqual(refused.data["error"]["code"], "validation_error")

    def test_a_31_a_heart_on_an_unwatched_episode_marks_it_and_stays(self):
        """TV-305: marked watched undated, then still watched after unhearting."""
        show = self.make_show({1: 3})
        middles = {}

        def steps(driver):
            response = driver.like(show, 1, 2)
            if driver.path == "api":
                asked = response.data["earlier_unwatched"]
                self.assertEqual(asked["question"], "Also mark S1 E1 watched?")
                driver.answer(show, asked, yes=False)
            middles[driver.name] = snapshot(driver.user, show)
            driver.like(show, 1, 2, liked=False)

        drivers = self.run_paths(show, steps, ("api", "installed"))

        snap = self.assert_same_state(show, drivers)
        for middle in middles.values():
            self.assertEqual(middle["viewings"], [(1, 2, "fill", "mark", None)])
            self.assertEqual(middle["hearts"]["episodes"], [(1, 2)])
        self.assertEqual(snap["viewings"], [(1, 2, "fill", "mark", None)])
        self.assertEqual(snap["hearts"]["episodes"], [])
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_34_an_episodes_stars_are_its_newest_rated_logs(self):
        """TV-502, TV-503.

        After deleting the newer log the stars show the remaining one; the community
        rating counts the person once.
        """
        show = self.make_show()
        friend = self.driver("api")
        friend.log(show, 1, 1, rating=4)
        url = f"/api/v1/media/{show.source}/episode/{show.media_id}/community/"
        params = {"season_number": 1, "episode_number": 1}

        def steps(driver):
            driver.log(show, 1, 1, day=OCT_20, rating=6)
            newer = driver.log(show, 1, 1, day=OCT_24, rating=9, is_rewatch=True)
            reader = self.api(driver.user)
            self.assertEqual(reader.episode_state(show, 1, 1)["user_rating"], "9.0")
            if driver.path == "api":
                # The friend's 4 and this account's newest, 9: two people, each once.
                community = reader.get(url, params).data
                self.assertEqual((community["rating_count"], float(community["average_rating"])), (2, 6.5))
            driver.delete_log(newer.data["id"])

        drivers = self.run_paths(show, steps, ("api", "installed"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["stars"]["episodes"], {(1, 1): "6.0"})
        reader = drivers["api"]
        self.assertEqual(reader.episode_state(show, 1, 1)["user_rating"], "6.0")
        community = reader.get(url, params).data
        # Three people: the friend's 4, and each account's remaining 6.
        self.assertEqual((community["rating_count"], float(community["average_rating"])), (3, round(16 / 3, 2)))
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_43_importing_a_completed_show_again_adds_nothing(self):
        """TV-800: one undated mark with fills; no duplicates; no feed activity."""
        show = self.make_show({1: 3, 2: 3})

        def steps(driver):
            if driver.path == "import":
                rows = [driver.row(show, "tv", status=COMPLETED)]
                driver.import_rows(rows)
                first = snapshot(driver.user, show)
                driver.import_rows(rows)
                self.assertEqual(snapshot(driver.user, show), first)
                driver.import_rows(rows, mode="overwrite")
                self.assertEqual(pick(snapshot(driver.user, show), IMPORT_KEYS), pick(first, IMPORT_KEYS))
            else:
                driver.mark(show)

        drivers = self.run_paths(show, steps, ("api", "import"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["status"], COMPLETED)
        self.assertEqual(len(snap["viewings"]), 6)
        self.assertEqual(snapshot(drivers["import"].user, show)["activity"], {})
        self.assertEqual(TV.objects.filter(user=drivers["import"].user).count(), 1)
        self.assert_surfaces_agree_for(show, drivers)

    def test_a_45_a_csv_export_imports_into_an_empty_account_unchanged(self):
        """TV-803.

        The same watched episodes, sections and stars (A-66: today's header).
        """
        shows = {
            "watching": self.make_show({1: 3, 2: 3}, title="Watching"),
            "completed": self.make_show({1: 2}, title="Completed"),
            "paused": self.make_show({1: 3}, title="Paused"),
            "planning": self.make_show({1: 2}, title="Planning"),
            "dropped": self.make_show({1: 3}, title="Dropped"),
            "logged": self.make_show({1: 3, 2: 3}, title="Logged"),
        }
        source = self.driver("api")
        ticks(source, shows["watching"], 1, (1, 2, 3))
        source.tick(shows["watching"], 2, 1)
        source.rate(shows["watching"], 8)
        source.mark(shows["completed"])
        source.rate(shows["completed"], 9)
        source.tick(shows["paused"], 1, 1)
        source.choose(shows["paused"], "pause")
        source.choose(shows["planning"], "planning")
        source.tick(shows["dropped"], 1, 1)
        source.choose(shows["dropped"], "drop")
        source.log(shows["logged"], 1, rating=7)

        text = export_csv(source.user)
        self.assertEqual(next(iter(csv.reader(StringIO(text)))), EXPORT_HEADER)
        target = self.driver("import")
        target.import_csv(text)

        def kept(snap):
            return {
                "status": snap["status"],
                "watched_all": snap["watched_all"],
                "show_stars": snap["stars"]["show"][0],
                "season_stars": {number: stars[0] for number, stars in snap["stars"]["seasons"].items()},
            }

        for name, show in shows.items():
            with self.subTest(show=name):
                self.assertEqual(kept(snapshot(target.user, show)), kept(snapshot(source.user, show)))
                self.assert_surfaces_agree(target.user, show)

    def test_a_46_retries_double_taps_and_failures_never_duplicate(self):
        """TV-705, TV-207.

        A retried log applies once; a failure partway through a fill leaves nothing
        behind; the retry then applies.
        """
        show = self.make_show({1: 3, 2: 3})
        api = self.driver("api")
        mutation_id = str(uuid4())
        first = api.log(show, 1, mutation_id=mutation_id, rating=8)
        again = api.log(show, 1, mutation_id=mutation_id, rating=8, expect=None)
        self.assertIn(again.status_code, (200, 201))
        self.assertEqual(again.data["id"], first.data["id"])
        self.assertEqual(DiaryEntry.objects.filter(user=api.user).count(), 1)
        api.tick(show, 2, 1)
        api.tick(show, 2, 1)
        self.assertEqual(len([v for v in self.snap_of(api, show)["viewings"] if v[:2] == (2, 1)]), 1)

        other = self.make_show({1: 3, 2: 3})
        with (
            mock.patch.object(Episode.objects, "bulk_create", side_effect=DatabaseError("disk full")),
            self.assertRaises(DatabaseError),
        ):
            api.mark(other)
        nothing = self.snap_of(api, other)
        self.assertFalse(nothing["in_library"])
        self.assertEqual(nothing["season_rows"], [])
        self.assertFalse(Season.objects.filter(user=api.user, item__media_id=other.media_id).exists())

        api.mark(other)
        self.assertEqual(len(self.snap_of(api, other)["viewings"]), 6)
        self.assert_surfaces_agree(api.user, show)
        self.assert_surfaces_agree(api.user, other)

    def test_a_59_the_repair_fills_the_rest_of_an_old_season_log(self):
        """TV-801, TV-401 (reconcile_tv).

        E3 is the log's fill, E1 and E2 keep their dates, and the season is finished, as
        if the app had made the same writes.
        """
        show = self.make_show({1: 3})
        old = self.new_user()
        TV.objects.bulk_create([TV(user=old, item=show, status=WATCHING, legacy_pending=True)])
        tv = TV.objects.get(user=old, item=show)
        season_item, episode_items = self._family_items(show, 1, 3)
        Season.objects.bulk_create([Season(user=old, item=season_item, related_tv=tv, status=WATCHING)])
        season = Season.objects.get(user=old, item=season_item)
        Episode.objects.bulk_create([
            Episode(item=episode_items[0], related_season=season, end_date=at_pacific(OCT_20, 20)),
            Episode(item=episode_items[1], related_season=season, end_date=at_pacific(date(2026, 10, 21), 20)),
        ])
        DiaryEntry.objects.create(user=old, item=season_item, consumed_at=at_pacific(OCT_22, 20))

        out = StringIO()
        call_command("reconcile_tv", "--apply", stdout=out, stderr=StringIO())

        repaired = snapshot(old, show)
        log = ("season", 1, None, "2026-10-22", 1)
        self.assertEqual(
            repaired["viewings"],
            [(1, 1, "tick", None, "2026-10-20"), (1, 2, "tick", None, "2026-10-21"), (1, 3, "fill", log, None)],
        )
        self.assertEqual(repaired["seasons"][1]["status"], COMPLETED)
        self.assertEqual(repaired["status"], COMPLETED)
        self.assertFalse(repaired["legacy_pending"])

        app = self.driver("api")
        self.clock.set(at_pacific(OCT_20, 20))
        app.tick(show, 1, 1)
        self.clock.set(at_pacific(date(2026, 10, 21), 20))
        app.tick(show, 1, 2)
        self.clock.set(at_pacific(OCT_25))
        app.log(show, 1, day=OCT_22, is_rewatch=False)
        keys = ("status", "finished", "counts", "up_next", "seasons", "watched_all", "viewings", "logs")
        self.assertEqual(pick(repaired, keys), pick(snapshot(app.user, show), keys))
        self.assert_surfaces_agree(old, show)

    def _family_items(self, show, season, episodes):
        season_item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=SEASON_TYPE,
            season_number=season,
            defaults={"title": show.title, "image": show.image},
        )
        episode_items = [
            Item.objects.get_or_create(
                media_id=show.media_id,
                source=show.source,
                media_type=EPISODE_TYPE,
                season_number=season,
                episode_number=number,
                defaults={"title": f"Episode {number}", "image": show.image},
            )[0]
            for number in range(1, episodes + 1)
        ]
        return season_item, episode_items

    def test_a_65_untick_removes_the_later_recorded_viewing_on_every_database(self):
        """TV-102.

        Of an undated viewing and a dated one recorded later, the untick removes the one
        recorded later, on SQLite and PostgreSQL (the suite runs on both in CI); and the
        other way round.
        """
        undated_show = self.make_show({1: 3})
        dated_show = self.make_show({1: 3})

        def undated_first(driver):
            self.api(driver.user).mark(undated_show, 1)  # E1's fill: undated, recorded first
            self.driver("hooks", user=driver.user).tick(undated_show, 1, 1)  # dated, later
            driver.untick(undated_show, 1, 1)

        def dated_first(driver):
            self.api(driver.user).tick(dated_show, 1, 1)  # dated, recorded first
            self.clock.advance(minutes=1)
            season = Season.objects.get(user=driver.user, item__media_id=dated_show.media_id)
            item = Episode.objects.get(related_season=season).item
            with self.captureOnCommitCallbacks(execute=True):
                Episode.objects.create(item=item, related_season=season)  # undated, later
            driver.untick(dated_show, 1, 1)

        undated = self.run_paths(undated_show, undated_first, ("api", "web", "webhook:jellyfin"))
        snap = self.assert_same_state(undated_show, undated)
        self.assertEqual([viewing for viewing in snap["viewings"] if viewing[:2] == (1, 1)], [(1, 1, "fill", "mark", None)])

        dated = self.run_paths(dated_show, dated_first, ("api", "web", "webhook:jellyfin"))
        snap = self.assert_same_state(dated_show, dated)
        self.assertEqual(snap["viewings"], [(1, 1, "tick", None, "2026-10-25")])

    def test_a_66_the_export_header_is_todays(self):
        """TV-803: the CSV header is exactly today's, with TV tracked."""
        show = self.make_show({1: 3})
        api = self.driver("api")
        api.tick(show, 1, 1)
        api.log(show, 1)

        rows = list(csv.reader(StringIO(export_csv(api.user))))

        self.assertEqual(rows[0], EXPORT_HEADER)
        types = [row[2] for row in rows[1:]]
        self.assertEqual(types.count("tv"), 1)
        self.assertEqual(types.count("season"), 1)
        self.assertEqual(types.count("episode"), 3)  # the tick and two fills, which have no date
        end_dates = {row[6]: row[EXPORT_HEADER.index("end_date")] for row in rows[1:] if row[2] == "episode"}
        self.assertEqual(end_dates["2"], "")
        self.assertNotEqual(end_dates["1"], "")

    @override_settings(TIME_ZONE="America/New_York")
    def test_a_67_the_repair_keeps_a_9pm_new_york_logs_new_york_day(self):
        """TV-801, TV-407 (reconcile_tv).

        With the server on New York and no stored calendar, a log saved at 9 p.m. New
        York keeps that day; the report lists every log whose shown day changes.
        """
        self.clock.set(datetime(2026, 10, 26, 12, 0, tzinfo=ZoneInfo("America/New_York")))
        show = self.make_show({1: 2})
        season_item, episode_items = self._family_items(show, 1, 2)
        no_calendar = make_user(calendar="")
        pacific = make_user(calendar=PACIFIC)
        nine_pm = datetime(2026, 10, 25, 21, 0, tzinfo=ZoneInfo("America/New_York"))
        one_am = datetime(2026, 10, 26, 1, 0, tzinfo=ZoneInfo("America/New_York"))
        kept = DiaryEntry.objects.create(user=no_calendar, item=season_item, consumed_at=nine_pm)
        moved = DiaryEntry.objects.create(user=pacific, item=episode_items[1], consumed_at=one_am)

        out = StringIO()
        call_command("reconcile_tv", "--apply", stdout=out, stderr=StringIO())
        output = out.getvalue()

        kept.refresh_from_db()
        moved.refresh_from_db()
        self.assertEqual(kept.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))
        self.assertEqual(moved.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))
        self.assertNotIn(f"Log {kept.pk} ", output)
        self.assertIn(f"  Log {moved.pk} (S1 E2): shown day 2026-10-26 → 2026-10-25", output)
        self.assertIn("Logs whose shown day changes: 1", output)
        self.assertIn(no_calendar.username, output)
        self.assertEqual(snapshot(no_calendar, show)["status"], COMPLETED)
        self.assertEqual(snapshot(pacific, show)["viewings"][0][2:], ("log", ("episode", 1, 2, "2026-10-25", 1), "2026-10-25"))

    def test_a_68_turning_an_episode_logs_heart_off_keeps_the_episode_hearted(self):
        """TV-502.

        The episode heart has no source log; editing a log never changes it.
        """
        show = self.make_show()

        def steps(driver):
            driver.log(show, 1, 1, liked=True)
            entry = DiaryEntry.objects.get(user=driver.user)
            driver.edit_log(entry.pk, liked=False)

        drivers = self.run_paths(show, steps, ("api", "installed"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["hearts"]["episodes"], [(1, 1)])
        self.assertFalse(snap["logs"][0][2])
        self.assert_surfaces_agree_for(show, drivers)


# ---------------------------------------------------------------------------------
# A-61: concurrent writes
# ---------------------------------------------------------------------------------


@unittest.skipUnless(connection.vendor == "postgresql", "Concurrent writes need PostgreSQL's row locks")
@override_settings(TIME_ZONE="America/New_York")
class A61ConcurrentWritesTests(TransactionTestCase):
    """A-61 (TV-101, TV-705).

    Two devices tick the same episode at once, and a webhook watch arrives during an app
    tick; each records one viewing. Two threads, each with its own database connection,
    start together; the per-show row lock orders them.
    """

    def setUp(self):
        super().setUp()
        cache.clear()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(NOON_PACIFIC, tz=PACIFIC).__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        for target in (
            "app.models.Item.fetch_releases",
            "app.tv_tracking._queue_external_ratings",
            "app.tasks.update_daily_statistics.delay",
        ):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.webhook_ids = {}
        patcher = mock.patch(
            "app.providers.tmdb.find",
            side_effect=lambda external_id, _source: {
                "tv_episode_results": [
                    dict(zip(("show_id", "season_number", "episode_number"), self.webhook_ids[external_id], strict=True)),
                ],
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = make_user(calendar=PACIFIC)
        self.show = make_show(self.fake, seasons={1: 3})
        tv_listing.get_listing(self.show)  # as the page leaves it

    def together(self, *calls):
        """Run calls in threads that start at once; return their results."""
        barrier = threading.Barrier(len(calls))
        results = [None] * len(calls)
        errors = []

        def run(index, call):
            from django.db import connections

            try:
                barrier.wait(timeout=10)
                results[index] = call()
            except Exception as error:  # noqa: BLE001 - reported below
                errors.append(error)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=run, args=(index, call)) for index, call in enumerate(calls)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        return results

    def device(self):
        from rest_framework.test import APIClient
        from rest_framework_simplejwt.tokens import AccessToken

        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}", HTTP_X_SPINE_TIMEZONE=PACIFIC)
        return client

    def tick_from(self, client):
        url = f"/api/v1/tracking/{self.show.source}/tv/{self.show.media_id}/actions/tick/"
        return lambda: client.post(url, {"season_number": 1, "episode_number": 1, "mutation_id": str(uuid4())}, format="json")

    def viewings(self):
        return Episode.objects.filter(related_season__related_tv__user=self.user, related_season__related_tv__item=self.show)

    def test_a_61_two_devices_tick_the_same_episode_at_once(self):
        phone, tablet = self.device(), self.device()

        first, second = self.together(self.tick_from(phone), self.tick_from(tablet))

        self.assertEqual({first.status_code, second.status_code}, {200})
        self.assertEqual(sorted([first.data["changed"], second.data["changed"]]), [False, True])
        self.assertEqual(self.viewings().count(), 1)
        self.assertEqual(TV.objects.filter(user=self.user, item=self.show).count(), 1)
        self.assertEqual(Season.objects.filter(user=self.user).count(), 1)

    def test_a_61_a_webhook_watch_during_an_app_tick(self):
        from django.test import Client
        from django.urls import reverse

        type(self.user).objects.filter(pk=self.user.pk).update(plex_usernames=self.user.username)
        self.user.refresh_from_db()
        imdb_id = f"tt{self.show.media_id}s01e01"
        self.webhook_ids[imdb_id] = (int(self.show.media_id), 1, 1)
        payload = {
            "event": "media.scrobble",
            "Account": {"title": self.user.username},
            "Metadata": {"type": "episode", "parentIndex": 1, "index": 1, "grandparentTitle": self.show.title,
                         "Guid": [{"id": f"imdb://{imdb_id}"}]},
        }
        url = reverse("plex_webhook", kwargs={"token": self.user.token})
        body = {"payload": json.dumps(payload)}

        def webhook():
            return Client().post(url, body)

        app, plex = self.together(self.tick_from(self.device()), webhook)

        self.assertEqual((app.status_code, plex.status_code), (200, 200))
        self.assertEqual(self.viewings().count(), 1)


class A61SequentialTwinTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """A-61's SQLite twin (SQLite serialises writes).

    Two devices' ticks one after the other, a retried tick, and a webhook watch right
    after an app tick each leave one viewing (TV-101, TV-705).
    """

    def test_a_61_two_devices_and_a_webhook_record_one_viewing(self):
        show = self.make_show({1: 3})
        phone = self.driver("api")
        tablet = APIDriver(self, phone.user)
        plex = self.driver("webhook", user=phone.user)
        mutation_id = str(uuid4())

        first = phone.tick(show, 1, 1, mutation_id=mutation_id)
        retried = phone.tick(show, 1, 1, mutation_id=mutation_id)
        other = tablet.tick(show, 1, 1)
        plex.tick(show, 1, 1)

        self.assertEqual((first.data["changed"], retried.data["changed"], other.data["changed"]), (True, True, False))
        self.assertEqual(self.snap_of(phone, show)["viewings"], [(1, 1, "tick", None, "2026-10-25")])
        self.assert_surfaces_agree(phone.user, show)


# ---------------------------------------------------------------------------------
# TV-000: the inherited rules, one test per row of the contract's table, via the API
# ---------------------------------------------------------------------------------


class TV000InheritedRulesTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """One test per row of TV-000's inherited table, driven through the API."""

    def setUp(self):
        super().setUp()
        self.app = self.driver("api")

    def test_tv_000_undated_marks_with_the_show_and_season_eyes(self):
        """SW-001, SW-004, SW-008, SW-011, BK-102, BK-103.

        An eye marks the aired regular episodes with no date, log or feed item and
        leaves the worked-out status; it is filled whenever what aired is watched; a
        filled eye never deletes a log and names the one to delete first.
        """
        show = self.make_show({1: 3, 2: 3})
        app = self.app

        app.mark(show, 1)  # the optional star picker is dismissed: nothing is sent

        snap = self.snap_of(app, show)
        self.assertEqual(snap["viewings"], [(1, episode, "fill", "mark", None) for episode in (1, 2, 3)])
        self.assertEqual((snap["logs"], snap["activity"], snap["last_watched_on"]), ([], {}, None))
        self.assertEqual(snap["status"], WATCHING)  # worked out, not "Completed" (TV-300)
        self.assertTrue(app.eye(show, 1)["filled"])  # filled though the show isn't Completed

        app.log(show, 2)
        blocked = app.unmark(show, 2)
        self.assertEqual((blocked["tap"], blocked["message"]), ("blocked", "Delete your Season 2 log first."))
        refused = app.action(show, "unmark", season_number=2, expect=409)
        self.assertEqual(refused.data["error"]["message"], "Delete your Season 2 log first.")
        self.assertEqual(len(self.snap_of(app, show)["logs"]), 1)
        self.assert_surfaces_agree(app.user, show)

    def test_tv_000_logs_imply_tracking_dates_and_repeats(self):
        """SW-002, SW-003, SW-005, SW-006, SW-007.

        A log puts the show in the library and counts its target watched; log dates are
        calendar days, never future; same-day logs are valid; the last-watched date
        comes from dated viewings and logs only.
        """
        show = self.make_show({1: 3, 2: 3})
        app = self.app

        app.log(show, 1, day=OCT_22)
        snap = self.snap_of(app, show)
        self.assertTrue(snap["in_library"])
        self.assertEqual(snap["seasons"][1]["status"], COMPLETED)
        self.assertEqual(snap["last_watched_on"], OCT_22)  # the log's day; its fills have none

        app.log(show, 1, day=OCT_22, is_rewatch=True)  # the same day again
        self.assertEqual(len(self.snap_of(app, show)["logs"]), 2)
        refused = app.log(show, 2, day=date(2026, 10, 26), expect=400)
        self.assertEqual(refused.data["error"]["message"], "Log dates can't be in the future.")

        self.clock.set(at_pacific(OCT_24, 20))
        tick = app.tick(show, 2, 1)
        self.assertEqual(self.snap_of(app, show)["last_watched_on"], OCT_24)
        # A claim defaults to its tick's day (SW-006, TV-404).
        self.clock.set(at_pacific(OCT_25))
        target = app.episode_state(show, 2, 1)["composer"]["targets"][0]
        self.assertEqual((target["date"], target["claims_viewing_id"]), ("2026-10-24", tick.data["episode"]["untick_viewing_id"]))

        marked = self.make_show({1: 3})
        app.mark(marked)
        self.assertIsNone(self.snap_of(app, marked)["last_watched_on"])
        self.assert_surfaces_agree(app.user, show)

    def test_tv_000_deleting_logs_and_removal(self):
        """SW-009, SW-012, BK-002, GM-304.

        Deleting a log removes only its viewing and fills; only logs block Remove;
        Remove clears stars and hearts and keeps lists.
        """
        show = self.make_show({1: 3, 2: 3})
        app = self.app
        app.tick(show, 1, 1)
        entry_id = app.log(show, 1).data["id"]

        app.delete_log(entry_id)
        snap = self.snap_of(app, show)
        self.assertEqual(snap["viewings"], [(1, 1, "tick", None, "2026-10-25")])
        self.assertTrue(snap["in_library"])

        second = app.log(show, 2).data["id"]
        self.assertEqual(app.remove(show, expect=409).data["error"]["code"], "tv_log_blocks")
        tracking_delete = app.request("delete", app.tracking_url(show), expect=409)
        self.assertEqual(tracking_delete.data["error"]["message"], "Delete this show's logs first.")
        app.delete_log(second)
        app.rate(show, 7)
        app.like(show)
        custom_list = CustomList.objects.create(owner=app.user, name="Later")
        CustomListItem.objects.create(custom_list=custom_list, item=show)

        app.remove(show)

        self.assertFalse(self.snap_of(app, show)["in_library"])
        self.assertFalse(MediaLike.objects.filter(user=app.user).exists())
        self.assertTrue(CustomListItem.objects.filter(custom_list=custom_list, item=show).exists())

    def test_tv_000_restoring_earlier_states(self):
        """GM-015, BK-102, BK-404.

        Undo never brings back deleted history, and restores an earlier choice only
        under TV-208.
        """
        show = self.make_show({1: 3})
        app = self.app
        app.choose(show, "planning")
        app.tick(show, 1, 1)
        self.assertEqual(self.snap_of(app, show)["status"], WATCHING)
        app.untick(show, 1, 1)
        restored = self.snap_of(app, show)
        self.assertEqual((restored["status"], restored["choice"], restored["viewings"]), (PLANNING, PLANNING, []))

        app.tick(show, 1, 1)
        app.choose(show, "pause")  # a status chosen since
        app.choose(show, "watching")
        app.untick(show, 1, 1)
        kept = self.snap_of(app, show)
        self.assertEqual(kept["status"], WATCHING)  # the later choice, not Planning
        self.assertEqual(kept["viewings"], [])  # the unticked viewing never comes back

    def test_tv_000_show_and_season_stars_and_hearts(self):
        """SW-100 to SW-108, SW-200 to SW-203, BK-200, BK-202, GM-301, GM-303.

        Each level has its own stars and heart with source links; on an unstarted show
        they mark it; clearing stars or turning a heart off changes nothing else.
        """
        show = self.make_show({1: 3, 2: 3})
        app = self.app

        app.rate(show, 8)  # an unstarted show: marked watched (TV-303)
        marked = self.snap_of(app, show)
        self.assertEqual((marked["status"], marked["stars"]["show"]), (COMPLETED, ("8.0", None)))
        app.like(show)
        app.rate(show, None)
        app.like(show, liked=False)
        cleared = self.snap_of(app, show)
        self.assertEqual(cleared["stars"]["show"], (None, None))
        self.assertFalse(cleared["hearts"]["show"])
        self.assertEqual(pick(cleared, ("status", "viewings")), pick(marked, ("status", "viewings")))

        other = self.make_show({1: 3, 2: 3})
        rated = app.log(other, 1, rating=8).data["id"]
        log = ("season", 1, None, "2026-10-25", 1)
        self.assertEqual(self.snap_of(app, other)["stars"]["seasons"][1], ("8.0", log))
        app.log(other, 1, is_rewatch=True)  # unrated: keeps the stars and their source (SW-102)
        self.assertEqual(self.snap_of(app, other)["stars"]["seasons"][1], ("8.0", log))
        app.delete_log(rated)  # SW-106: kept, now independent
        self.assertEqual(self.snap_of(app, other)["stars"]["seasons"][1], ("8.0", None))
        app.rate(other, None, season=1)
        unrated = DiaryEntry.objects.get(user=app.user, item__season_number=1).pk
        app.edit_log(unrated, rating=6)  # SW-108: fills empty stars and becomes the source
        self.assertEqual(self.snap_of(app, other)["stars"]["seasons"][1], ("6.0", ("season", 1, None, "2026-10-25", 1)))

    def test_tv_000_rewatch_defaults(self):
        """SW-010, GM-217.

        The server works out the composer's Rewatch default; on an episode log the saved
        toggle decides the claim, on a season log only the badge.
        """
        show = self.make_show({1: 3})
        twin = self.make_show({1: 3})
        app = self.app
        app.tick(show, 1, 1)
        target = app.episode_state(show, 1, 1)["composer"]["targets"][0]
        self.assertFalse(target["is_rewatch"])
        self.assertIsNotNone(target["claims_viewing_id"])

        app.log(show, 1, 1, is_rewatch=True)  # changed before saving: a viewing is added
        self.assertEqual(len([v for v in self.snap_of(app, show)["viewings"] if v[:2] == (1, 1)]), 2)

        app.log(show, 1, is_rewatch=True)
        app.tick(twin, 1, 1)
        app.log(twin, 1, 1, is_rewatch=True)
        app.log(twin, 1, is_rewatch=False)
        mine, theirs = self.snap_of(app, show), self.snap_of(app, twin)
        self.assertEqual(mine["viewings"], theirs["viewings"])  # the badge only
        first = DiaryEntry.objects.filter(user=app.user, item__media_id=show.media_id).order_by("pk").first()
        app.delete_log(first.pk)  # deleting another log never recalculates a saved value
        self.assertTrue(DiaryEntry.objects.get(user=app.user, item__media_id=show.media_id).is_rewatch)

    def test_tv_000_saving_cancelling_and_one_editor(self):
        """SW-300 to SW-303.

        A write and its fills apply together or not at all; cancelling saves nothing;
        the visible draft is what saves.
        """
        show = self.make_show({1: 3})
        app = self.app
        with (
            mock.patch.object(Episode.objects, "bulk_create", side_effect=DatabaseError("disk full")),
            self.assertRaises(DatabaseError),
        ):
            app.log(show, 1)
        self.assertFalse(self.snap_of(app, show)["in_library"])
        self.assertFalse(DiaryEntry.objects.filter(user=app.user).exists())

        finished = ticks(app, show, 1, (1, 2, 3)).data["finished"]
        self.assertIsNotNone(finished)  # "Not now": nothing is sent
        self.assertEqual(self.snap_of(app, show)["logs"], [])
        self.assertIn("log_season", app.state(show, 1)["season"]["available_actions"])

        app.log(show, 1, day=date(2026, 10, 23), rating=7, liked=True, is_rewatch=False, review="Good")
        entry = DiaryEntry.objects.get(user=app.user)
        self.assertEqual(
            (log_day(entry), entry.rating, entry.liked, entry.is_rewatch, entry.review),
            (date(2026, 10, 23), 7, True, False, "Good"),
        )

    def test_tv_000_plus_menu_and_statuses(self):
        """BK-001, BK-100, BK-101, GM-009, GM-011, GM-012.

        Base options always show with reasons for unavailable ones; Planning, Paused and
        Dropped need no dates or viewings; Pause saves at once; one status, one section.
        """
        show = self.make_show({1: 3})
        app = self.app
        state = app.state(show)
        for action in ("watching", "planning", "pause", "drop"):
            self.assertIn(action, state["available_actions"])
        self.assertIsNone(state["selected_action"])

        app.choose(show, "pause")
        paused = app.state(show)
        self.assertEqual((paused["status"], paused["selected_action"]), (PAUSED, "pause"))
        self.assertNotIn("pause", paused["available_actions"])
        snap = self.snap_of(app, show)
        self.assertEqual((snap["viewings"], snap["season_rows"], snap["last_watched_on"]), ([], [], None))
        sections = {
            status: [row["media"]["ref"]["media_id"] for row in app.library(media_type="tv", status=status)]
            for status in (WATCHING, PLANNING, PAUSED, DROPPED, COMPLETED)
        }
        self.assertEqual({status for status, ids in sections.items() if show.media_id in ids}, {PAUSED})

        app.tick(show, 1, 1)
        reasons = app.state(show)["action_reasons"]
        self.assertEqual(reasons["planning"], "You've started this show. Pause or drop it instead.")
        app.log(show, 1)
        self.assertEqual(app.state(show)["action_reasons"]["remove"], "Delete this show's logs first.")

    def test_tv_000_pause_and_drop_keep_progress(self):
        """BK-303, BK-305: every viewing, log and mark stays; coming back continues."""
        show = self.make_show({1: 2, 2: 2, 3: 2, 4: 2})
        app = self.app
        ticks(app, show, 1, (1, 2))
        app.mark(show, 2)
        app.log(show, 3)
        before = self.snap_of(app, show)

        for hold in ("pause", "drop"):
            app.choose(show, hold)
            held = self.snap_of(app, show)
            self.assertEqual(pick(held, ("viewings", "logs", "up_next")), pick(before, ("viewings", "logs", "up_next")))
            app.choose(show, "watching")
            back = self.snap_of(app, show)
            self.assertEqual(back["status"], WATCHING)
            self.assertEqual(back["up_next"], before["up_next"])

    def test_tv_000_imports(self):
        """SW-500 to SW-504, GM-501.

        Imports use the same state model, post nothing, never infer Paused or Dropped,
        and an imported rating follows TV-303.
        """
        stale = self.make_show({1: 3})
        rated = self.make_show({1: 3})
        importer = self.driver("import")
        old_play = datetime(2019, 3, 1, 20, 0, tzinfo=UTC)
        importer.import_rows([
            *importer.plays(stale, [(1, 1, old_play)]),
            importer.row(rated, "tv", status=COMPLETED, score=8),
        ])

        inactive = snapshot(importer.user, stale)
        self.assertEqual(inactive["status"], WATCHING)  # never Paused or Dropped from inactivity
        stars = snapshot(importer.user, rated)
        self.assertEqual((stars["status"], stars["stars"]["show"][0]), (COMPLETED, "8.0"))
        self.assertEqual(len(stars["viewings"]), 3)
        self.assertFalse(Activity.objects.filter(actor=importer.user).exists())

    def test_tv_000_consistency(self):
        """BK-500, BK-501, SW-700.

        The backend decides each transition; retries and double taps never duplicate;
        every surface agrees after a write.
        """
        show = self.make_show({1: 3, 2: 3})
        app = self.app
        app.tick(show, 1, 1)
        app.tick(show, 1, 1)
        mutation_id = str(uuid4())
        app.log(show, 2, mutation_id=mutation_id)
        app.log(show, 2, mutation_id=mutation_id, expect=None)
        snap = self.assert_surfaces_agree(app.user, show)
        self.assertEqual(len(snap["logs"]), 1)
        self.assertEqual(len([v for v in snap["viewings"] if v[:2] == (1, 1)]), 1)
        refused = app.write_status(show, COMPLETED, expect=400, progress=6)
        self.assertEqual(refused.data["error"]["code"], "validation_error")
        self.assertEqual(self.snap_of(app, show), snap)

    def test_tv_000_privacy_and_activity(self):
        """SW-400, SW-401.

        Activity is action-specific: ticks, marks and hearts post nothing; each log
        posts one diary activity with its stars and visibility; direct stars post rating
        activity on TV's 0-10 scale.
        """
        show = self.make_show({1: 3, 2: 3})
        app = self.app
        app.tick(show, 1, 1)
        app.mark(show, 1)
        app.like(show, 1, 1)
        self.assertEqual(self.snap_of(app, show)["activity"], {})

        app.request(
            "post",
            "/api/v1/diary/",
            {
                "ref": app.ref(show, 2),
                "consumed_at": "2026-10-25",
                "rating": 8,
                "visibility": "followers",
                "mutation_id": str(uuid4()),
            },
            expect=201,
        )
        app.rate(show, 9)
        self.assertEqual(
            self.snap_of(app, show)["activity_detail"],
            [("diary_created", "8.0", "followers"), ("rating_updated", "9.0", "public")],
        )


def log_day(entry):
    """Return a log's calendar day on its owner's calendar."""
    from api.tests.tv_scenarios import log_day as scenario_log_day

    return scenario_log_day(entry, entry.user)


# ---------------------------------------------------------------------------------
# TV-207: written statuses, field rejections and season deletes on every path
# ---------------------------------------------------------------------------------


class TV207WrittenStateTests(AcceptanceHelpers, TVAcceptanceTestCase):
    """TV-207.

    A status written through any entry point means the matching action; the API rejects
    what the web, webhooks and imports ignore; deleting a season's tracking removes its
    viewings and marks unless a log blocks it.
    """

    WRITES = (
        # (written status, season, the app's action, its body)
        (PAUSED, None, "pause", {}),
        (DROPPED, None, "drop", {}),
        (WATCHING, None, "watching", {}),
        (COMPLETED, None, "mark_watched", {}),
        (COMPLETED, 2, "mark_watched", {"season_number": 2}),
    )

    def test_tv_207_a_written_status_means_its_action_on_every_path(self):
        for status, season, action, body in self.WRITES:
            show = self.make_show({1: 3, 2: 3})

            def steps(driver, status=status, season=season, action=action, body=body, show=show):
                driver.tick(show, 1, 1)
                if status == WATCHING:
                    driver.choose(show, "pause")  # writing In progress resumes it
                if driver.path == "api":
                    driver.action(show, action, **body)
                else:
                    driver.write_status(show, status, season=season)

            with self.subTest(status=status, season=season):
                drivers = self.run_paths(show, steps, ("api", "installed", "web", "hooks"))
                self.assert_same_state(show, drivers)
                self.assert_surfaces_agree_for(show, drivers)

    def test_tv_207_a_written_season_in_progress_chooses_watching_for_the_show(self):
        """A season's In progress chooses Watching for the show and starts no season."""
        show = self.make_show({1: 3, 2: 3})

        def steps(driver):
            if driver.path == "api":
                driver.choose(show, "watching")
            else:
                driver.write_status(show, WATCHING, season=2)

        drivers = self.run_paths(show, steps, ("api", "installed", "web"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual((snap["status"], snap["choice"], snap["season_rows"]), (WATCHING, WATCHING, []))
        self.assertEqual(snap["up_next"][:2], (1, 1))

    def test_tv_207_progress_and_date_fields_are_rejected_or_ignored(self):
        """The API answers 400; the web form and imports ignore them."""
        show = self.make_show({1: 3, 2: 3})
        before = {}

        def steps(driver):
            if driver.path == "import":
                row = driver.row(show, "tv", status=WATCHING)
                row["progress"] = "5"
                row["start_date"] = "2020-01-01T00:00:00Z"
                driver.import_rows([row, *driver.plays(show, [(1, 1, self.clock.advance(minutes=1))])[1:]])
                return
            driver.tick(show, 1, 1)
            before[driver.name] = snapshot(driver.user, show)
            if driver.path in ("api", "installed"):
                for field, value in (("progress", 5), ("start_date", "2020-01-01"), ("end_date", "2020-01-01")):
                    refused = driver.write_status(show, WATCHING, expect=400, **{field: value})
                    self.assertEqual(refused.data["error"]["code"], "validation_error")
            else:
                driver.save_form(show, progress=5, start_date="2020-01-01", end_date="2020-01-02")
            self.assertEqual(
                pick(snapshot(driver.user, show), ("status", "counts", "viewings", "up_next")),
                pick(before[driver.name], ("status", "counts", "viewings", "up_next")),
            )

        drivers = self.run_paths(show, steps, ("api", "installed", "web", "import"))

        snap = self.assert_same_state(
            show,
            drivers,
            keys=tuple(key for key in ALL_KEYS if key not in ("choice", "progress_changes")),
        )
        self.assertEqual(snap["counts"], (1, 6))

    def test_tv_207_deleting_a_seasons_tracking(self):
        """TV-207.

        It removes every viewing and mark of the season, and is refused while any log of
        the season, its episodes or the show exists.
        """
        show = self.make_show({1: 3, 2: 3})

        def steps(driver):
            ticks(driver, show, 1, (1, 2))
            driver.mark(show, 2)
            entry_id = driver.log(show, 2, is_rewatch=True)
            entry = DiaryEntry.objects.get(user=driver.user)
            before = snapshot(driver.user, show)
            if driver.path == "api":
                refused = driver.delete_season(show, 2, expect=409)
                self.assertEqual(refused.data["error"]["code"], "tv_log_blocks")
            else:
                driver.delete_season(show, 2)
            self.assertEqual(snapshot(driver.user, show), before)
            self.assertIsNotNone(entry_id)
            driver.delete_log(entry.pk)
            driver.delete_season(show, 2)

        drivers = self.run_paths(show, steps, ("api", "web"))

        snap = self.assert_same_state(show, drivers)
        self.assertEqual(snap["watched_all"], [(1, 1), (1, 2)])
        self.assertEqual(snap["season_rows"], [1])
        self.assertEqual(snap["status"], WATCHING)
        self.assert_surfaces_agree_for(show, drivers)
