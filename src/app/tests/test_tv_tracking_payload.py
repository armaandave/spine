"""Payloads (plan 2.3): TV-005 (server), TV-104, TV-105, TV-404 (server), TV-103 unlisted.

Key sets are pinned against plan section 2.3; backend-api pins the HTTP envelope.
"""

from datetime import date, timedelta

from app import tv_listing, tv_tracking
from app.models import TV, Episode, Season, Status
from app.tests.test_tv_tracking_viewings import OCT_25, TVTrackingTestCase

SHOW_DETAIL_KEYS = [
    "version",
    "in_library",
    "status",
    "choice",
    "selected_action",
    "started",
    "finished",
    "caught_up",
    "watched_count",
    "aired_count",
    "up_next",
    "next_airing",
    "premiere",
    "rewatch",
    "last_watched_on",
    "remove_count",
    "rating_source_diary_entry_id",
    "like_source_diary_entry_id",
    "like_is_independent",
    "eye",
    "available_actions",
    "action_reasons",
    "composer",
    "season",
    "episode",
]
SHOW_LIST_KEYS = {
    "version",
    "in_library",
    "status",
    "choice",
    "started",
    "finished",
    "caught_up",
    "watched_count",
    "aired_count",
    "up_next",
    "next_airing",
    "premiere",
    "rewatch",
    "last_watched_on",
}
SEASON_CARD_KEYS = {
    "season_number",
    "is_specials",
    "listed",
    "started",
    "status",
    "state",
    "watched_count",
    "aired_count",
    "episode_count",
    "first_gap_episode",
    "next_air_date",
    "rewatching",
    "rating",
    "liked",
}
SEASON_DETAIL_KEYS = SEASON_CARD_KEYS | {
    "rating_source_diary_entry_id",
    "like_source_diary_entry_id",
    "like_is_independent",
    "log_count",
    "eye",
    "available_actions",
    "action_reasons",
}
EPISODE_KEYS = {
    "season_number",
    "episode_number",
    "is_special",
    "listed",
    "aired",
    "air_date",
    "watched",
    "viewing_count",
    "log_count",
    "can_tick",
    "untick_viewing_id",
    "untick_message",
    "can_mark_up_to",
    "mark_up_to_prompt",
    "user_rating",
    "liked",
    "is_up_next",
}
EYE_KEYS = {"filled", "tap", "count", "prompt", "message"}
TARGET_KEYS = {
    "id",
    "level",
    "season_number",
    "episode_number",
    "title",
    "date",
    "rating",
    "liked",
    "is_rewatch",
    "claims_viewing_id",
    "available",
    "reason",
}


class PayloadShapeTests(TVTrackingTestCase):
    """Plan 2.3.1 to 2.3.5: the key sets of every TV object."""

    def test_show_detail_and_list_keys(self):
        show = self.make_show({0: 1, 1: 3, 2: 3})
        self.tick(show, 1, 1)
        state = self.state(show)
        self.assertEqual(list(state), SHOW_DETAIL_KEYS)
        self.assertEqual(set(state["eye"]), EYE_KEYS)
        self.assertEqual(set(state["up_next"]), {"season_number", "episode_number", "title", "air_date", "is_new"})
        self.assertEqual(state["up_next"]["title"], "Episode 2")
        for target in state["composer"]["targets"]:
            self.assertEqual(set(target), TARGET_KEYS)
        self.assertEqual(set(tv_tracking.state_payload(self.user, show, detail=False)), SHOW_LIST_KEYS)
        self.assertEqual(set(tv_tracking.list_payload(self.tv(show))), SHOW_LIST_KEYS)
        self.assertEqual(set(tv_tracking.list_payload(None)), SHOW_LIST_KEYS)

    def test_season_and_episode_keys(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        season = self.state(self.season_item(show, 1))["season"]
        self.assertEqual(set(season), SEASON_DETAIL_KEYS)
        self.assertEqual(set(season["eye"]), EYE_KEYS)
        card = tv_tracking.season_card_states(self.user, show, None)[1]
        self.assertEqual(set(card), SEASON_CARD_KEYS)
        episodes = tv_tracking.episode_states(self.user, show, 1, None)
        self.assertEqual(set(episodes[1]), EPISODE_KEYS)
        page = self.state(self.episode_item(show, 1, 1))
        self.assertEqual(set(page["episode"]), EPISODE_KEYS | {"available_actions", "action_reasons", "composer"})
        self.assertEqual([target["id"] for target in page["composer"]["targets"]], ["episode:1:1"])

    def test_write_envelope_keys(self):
        show = self.make_show({1: 3})
        result = self.tick(show, 1, 1)
        envelope = tv_tracking.write_envelope(result)
        self.assertEqual(
            set(envelope),
            {"tv", "season", "episode", "changed", "finished", "earlier_unwatched"},
        )
        self.assertEqual(envelope["season"]["season_number"], 1)
        self.assertEqual(envelope["episode"]["episode_number"], 1)
        self.assertTrue(envelope["changed"])

    def test_an_untracked_show_reports_version_0_and_no_status(self):
        show = self.make_show({1: 3})
        state = self.state(show)
        self.assertEqual(
            (state["version"], state["in_library"], state["status"], state["selected_action"]),
            (0, False, None, None),
        )
        self.assertEqual((state["watched_count"], state["aired_count"]), (0, 3))
        self.assertEqual(state["eye"]["tap"], "mark_confirm")
        self.assertNotIn("remove", state["available_actions"])
        self.assertIn("planning", state["available_actions"])


class TV005ActionsTests(TVTrackingTestCase):
    """TV-005 (server): available actions and reasons (plan 2.3.6)."""

    def test_tv_005_watching_at_s2_e4_matches_the_plan_example(self):
        show = self.make_show({1: 3, 2: 5})
        self.ticks(show, 1, (1, 2, 3))
        self.ticks(show, 2, (1, 2, 3))
        state = self.state(show)
        self.assertEqual(state["selected_action"], "watching")
        self.assertEqual(state["available_actions"], ["pause", "drop", "log", "remove", "rate"])
        self.assertEqual(
            state["action_reasons"],
            {"planning": "You've started this show. Pause or drop it instead."},
        )

    def test_tv_005_a_finished_show_offers_rewatch_and_explains_watching(self):
        show = self.make_show({1: 2})
        tv_tracking.mark_watched(self.user, show)
        state = self.state(show)
        self.assertIsNone(state["selected_action"])
        self.assertIn("rewatch", state["available_actions"])
        self.assertEqual(state["action_reasons"]["watching"], "You've watched everything that has aired.")

    def test_tv_005_nothing_aired_explains_log_rate_and_watching(self):
        show = self.make_show({1: [OCT_25 + timedelta(days=9)]})
        state = self.state(show)
        for key in ("watching", "log", "rate"):
            self.assertEqual(state["action_reasons"][key], "Nothing has aired yet.")
        self.assertEqual(state["premiere"], {"season_number": 1, "air_date": (OCT_25 + timedelta(days=9)).isoformat()})
        self.assertEqual(state["eye"]["message"], "S1 premieres Nov 3")

    def test_tv_005_season_and_episode_actions(self):
        show = self.make_show({1: 2, 2: [OCT_25 + timedelta(days=5)]})
        self.ticks(show, 1, (1, 2))
        season_1 = self.state(self.season_item(show, 1))["season"]
        self.assertEqual(season_1["available_actions"], ["log_season", "rewatch_season", "rate"])
        season_2 = self.state(self.season_item(show, 2))["season"]
        self.assertEqual(season_2["available_actions"], [])
        self.assertEqual(
            season_2["action_reasons"],
            {"log_season": "Nothing has aired yet.", "rate": "Nothing has aired yet."},
        )
        episode = self.state(self.episode_item(show, 2, 1))["episode"]
        self.assertEqual(episode["action_reasons"], {"log_episode": "This episode hasn't aired yet."})


class TV105UpNextTests(TVTrackingTestCase):
    """TV-105: up next is the next episode to watch."""

    def test_tv_105_ticking_s1_e3_moves_up_next_to_s1_e4(self):
        show = self.make_show({1: 10})
        self.tick(show, 1, 3)
        up_next = self.state(show)["up_next"]
        self.assertEqual((up_next["season_number"], up_next["episode_number"]), (1, 4))

    def test_tv_105_the_latest_goes_by_calendar_day_then_recorded_time(self):
        show = self.make_show({1: 5, 2: 5, 3: 5})
        self.tick(show, 1, 1)
        # Recorded later, but on an earlier day: S1 E1 stays the latest.
        self.advance()
        tv_tracking.tick(self.user, show, 2, 5, at=date(2026, 10, 1), entry_point=tv_tracking.WEB)
        up_next = self.state(show)["up_next"]
        self.assertEqual((up_next["season_number"], up_next["episode_number"]), (1, 2))
        # An undated imported play recorded later counts as older than dated ticks.
        self.tick(show, 3, 1)
        Episode.objects.filter(item__season_number=3).update(end_date=None, watched_on=None)
        up_next = self.state(show)["up_next"]
        self.assertEqual((up_next["season_number"], up_next["episode_number"]), (1, 2))

    def test_tv_105_with_no_aired_regular_episode_there_is_no_up_next(self):
        show = self.make_show({0: 2, 1: [OCT_25 + timedelta(days=3)]})
        self.tick(show, 0, 1)
        state = self.state(show)
        self.assertIsNone(state["up_next"])
        self.assertEqual(self.tv(show).up_next_season, None)


class TV404ComposerTests(TVTrackingTestCase):
    """TV-404 (server): the server supplies the composer's defaults."""

    def test_a_56_rewatch_defaults_off_for_a_first_log_and_on_after_a_mark(self):
        show = self.make_show({1: 2, 2: 2})
        targets = {target["id"]: target for target in self.state(show)["composer"]["targets"]}
        self.assertFalse(targets["season:1"]["is_rewatch"])
        result = self.log(self.season_item(show, 1))
        self.assertFalse(result.entry.is_rewatch)
        tv_tracking.mark_watched(self.user, show, season_number=2)
        target = self.state(self.season_item(show, 2))["composer"]["targets"][0]
        self.assertTrue(target["is_rewatch"])
        self.assertTrue(self.log(self.season_item(show, 2)).entry.is_rewatch)

    def test_tv_404_show_page_targets_and_default(self):
        show = self.make_show({0: 1, 1: 2, 2: 2, 3: [OCT_25 + timedelta(days=10)]})
        self.ticks(show, 1, (1, 2))
        self.tick(show, 2, 1)
        composer = self.state(show)["composer"]
        self.assertEqual([target["id"] for target in composer["targets"]], ["season:1", "season:2", "season:0", "show"])
        self.assertEqual(composer["default_target"], "season:1")
        self.assertEqual(composer["targets"][2]["title"], "Specials")
        self.assertEqual(composer["targets"][3]["title"], "Whole show")
        self.log(self.season_item(show, 1))
        self.assertEqual(self.state(show)["composer"]["default_target"], "season:2")

    def test_tv_404_stars_and_heart_prefill_from_the_targets_own_values(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 1)
        tv_tracking.set_rating(self.user, show, 9)
        tv_tracking.set_rating(self.user, self.season_item(show, 1), 6)
        tv_tracking.set_like(self.user, self.season_item(show, 1), True)
        targets = {target["id"]: target for target in self.state(show)["composer"]["targets"]}
        self.assertEqual((targets["season:1"]["rating"], targets["season:1"]["liked"]), ("6.0", True))
        self.assertEqual((targets["show"]["rating"], targets["show"]["liked"]), ("9.0", False))

    def test_tv_404_episode_defaults_claim_and_rewatch(self):
        show = self.make_show({1: 3})
        tv_tracking.tick(self.user, show, 1, 1, at=date(2026, 10, 20), entry_point=tv_tracking.WEB)
        tv_tracking.mark_watched(self.user, show)
        claim = self.state(self.episode_item(show, 1, 1))["composer"]["targets"][0]
        self.assertEqual((claim["date"], claim["is_rewatch"]), ("2026-10-20", False))
        marked = self.state(self.episode_item(show, 1, 2))["composer"]["targets"][0]
        self.assertEqual((marked["date"], marked["is_rewatch"], marked["claims_viewing_id"]), ("2026-10-25", True, None))

    def test_tv_404_a_season_with_ticks_and_marks_defaults_off(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        tv_tracking.mark_watched(self.user, show)
        target = self.state(self.season_item(show, 1))["composer"]["targets"][0]
        self.assertFalse(target["is_rewatch"])


class TV103UnlistedTests(TVTrackingTestCase):
    """TV-103: tracked seasons and episodes the provider no longer lists."""

    def test_a_62_a_season_tmdb_stops_listing_counts_toward_nothing(self):
        show = self.make_show({1: 2, 2: 2})
        tv_tracking.mark_watched(self.user, show)
        tv_tracking.set_rating(self.user, self.season_item(show, 2), 7)
        self.fake.show(show.media_id).remove_season(2)
        listing = tv_listing.get_listing(show, max_age=timedelta(0))
        state = tv_tracking.state_payload(self.user, show, listing=listing)
        self.assertEqual((state["watched_count"], state["aired_count"]), (2, 2))
        cards = tv_tracking.season_card_states(self.user, show, listing)
        self.assertEqual(sorted(cards), [1])
        unlisted = tv_tracking.unlisted_seasons(self.user, show, listing)
        self.assertEqual([season["season_number"] for season in unlisted], [2])
        self.assertEqual(unlisted[0]["title"], "Season 2")
        self.assertFalse(unlisted[0]["tv"]["listed"])
        self.assertEqual(unlisted[0]["tv"]["rating"], "7.0")
        self.assertEqual(len(self.viewings(show, 2)), 2)
        # Writes to the show still work.
        self.untick(show, 1, 1)
        self.assertEqual(self.status(show), "In progress")

    def test_tv_103_an_episode_tmdb_stops_listing_is_shown_last_and_untickable(self):
        show = self.make_show({1: 3})
        self.ticks(show, 1, (1, 2, 3))
        provider = self.fake.show(show.media_id)
        provider.seasons[1] = provider.seasons[1][:2]
        listing = tv_listing.get_listing(show, max_age=timedelta(0))
        episodes = tv_tracking.unlisted_episodes(self.user, show, 1, listing)
        self.assertEqual([episode["episode_number"] for episode in episodes], [3])
        state = episodes[0]["tv"]
        self.assertFalse(state["listed"])
        self.assertFalse(state["can_tick"])
        self.assertIsNotNone(state["untick_viewing_id"])
        self.assertEqual(sorted(tv_tracking.episode_states(self.user, show, 1, listing)), [1, 2])


class ListVariantTests(TVTrackingTestCase):
    """The list variant reads stored values only; legacy rows use the fallback."""

    def test_list_payload_reads_the_cache_with_no_queries(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        tv = self.tv(show)
        with self.assertNumQueries(0):
            payload = tv_tracking.list_payload(tv)
        self.assertEqual(payload["up_next"]["episode_number"], 2)
        self.assertEqual((payload["watched_count"], payload["aired_count"]), (1, 3))
        self.assertTrue(payload["started"])

    def test_prime_progress_is_one_query_for_any_number_of_legacy_rows(self):
        rows = []
        for _ in range(3):
            show = self.make_show({1: 4, 2: 4})
            tv_listing.get_listing(show)
            tv = TV.objects.create(item=show, user=self.user, status=Status.IN_PROGRESS.value)
            season = Season.objects.create(
                item=self.season_item(show, 1),
                user=self.user,
                related_tv=tv,
                status=Status.IN_PROGRESS.value,
            )
            for number in (1, 2, 2):
                Episode.objects.create(item=self.episode_item(show, 1, number), related_season=season)
            TV.objects.filter(pk=tv.pk).update(legacy_pending=True)
            rows.append(TV.objects.get(pk=tv.pk))
        with self.assertNumQueries(1):
            tv_tracking.prime_progress(rows)
        for row in rows:
            payload = tv_tracking.list_payload(row)
            self.assertEqual((payload["watched_count"], payload["aired_count"]), (2, 8))
            self.assertIsNone(payload["up_next"])
            self.assertEqual(payload["status"], "In progress")
            self.assertEqual(
                tv_tracking.progress_payload(row),
                {"kind": "episodes", "value": 2, "max": 8, "unit": "episode"},
            )

    def test_progress_payload_of_a_worked_out_season(self):
        show = self.make_show({1: 4})
        self.ticks(show, 1, (1, 2))
        season = Season.objects.get(item__season_number=1)
        self.assertEqual(
            tv_tracking.progress_payload(season),
            {"kind": "episodes", "value": 2, "max": 4, "unit": "episode"},
        )
