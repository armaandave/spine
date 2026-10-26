"""The TV payload contract (plan sections 2.3 and 2.5; TV-700, TV-900).

Pins the exact key set of every new TV object, checks that the plan's examples have
those keys and the values the same steps produce, and that every key installed builds
read is still there with its old meaning. New keys are additions only (TV-900).
"""

import json
from datetime import date

from rest_framework import status

from api.tests.test_tv_tracking_api import OCT_25, TVAPITestCase
from app.tests.tv_fixtures import FakeEpisode

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
SHOW_DETAIL_KEYS = SHOW_LIST_KEYS | {
    "selected_action",
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
}
UP_NEXT_KEYS = {"season_number", "episode_number", "title", "air_date", "is_new", "image_url"}
NEXT_AIRING_KEYS = {"season_number", "episode_number", "air_date"}
PREMIERE_KEYS = {"season_number", "air_date"}
REWATCH_KEYS = {"scope", "season_number", "started_at"}
EYE_KEYS = {"filled", "tap", "count", "prompt", "message"}
EYE_TAPS = {"mark", "mark_confirm", "unmark_marks", "unmark_confirm", "blocked", "nothing_aired"}
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
SEASON_STATES = {"watched", "airing", "in_progress", "not_started", "unaired", "unknown"}
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
EPISODE_PAGE_KEYS = EPISODE_KEYS | {"available_actions", "action_reasons", "composer"}
COMPOSER_KEYS = {"default_target", "targets"}
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
ENVELOPE_KEYS = {"tv", "season", "episode", "changed", "finished", "earlier_unwatched"}
FINISHED_KEYS = {"level", "season_number", "show_finished", "composer"}
EARLIER_KEYS = {"from", "to", "count", "seasons", "question", "offer_season_number"}
COORDINATE_KEYS = {"season_number", "episode_number"}
TV_LOG_KEYS = {
    "level",
    "season_number",
    "episode_number",
    "unmarks_count",
    "covers_count",
    "viewing_id",
    "state_version",
}
ERROR_KEYS = {"code", "message", "fields", "request_id"}

# The examples of plan section 2.3, as written there.
SHOW_DETAIL_EXAMPLE = json.loads(
    """
{"version": 1793051512345, "in_library": true, "status": "In progress", "choice": null,
 "selected_action": "watching", "started": true, "finished": false, "caught_up": false,
 "watched_count": 13, "aired_count": 20,
 "up_next": {"season_number": 2, "episode_number": 4, "title": "Fly", "air_date": "2010-05-23", "is_new": false,
             "image_url": null},
 "next_airing": null, "premiere": null, "rewatch": null, "last_watched_on": "2026-10-25", "remove_count": 13,
 "rating_source_diary_entry_id": null, "like_source_diary_entry_id": null, "like_is_independent": true,
 "eye": {"filled": false, "tap": "mark_confirm", "count": 20, "prompt": "Mark all 20 aired episodes watched?",
         "message": null},
 "available_actions": ["pause", "drop", "log", "remove", "rate"],
 "action_reasons": {"planning": "You've started this show. Pause or drop it instead."},
 "composer": {"default_target": "show", "targets": [
   {"id": "season:1", "level": "season", "season_number": 1, "episode_number": null, "title": "Season 1",
    "date": "2026-10-26", "rating": "8.0", "liked": true, "is_rewatch": false, "claims_viewing_id": null,
    "available": true, "reason": null},
   {"id": "season:2", "level": "season", "season_number": 2, "episode_number": null, "title": "Season 2",
    "date": "2026-10-26", "rating": null, "liked": false, "is_rewatch": false, "claims_viewing_id": null,
    "available": true, "reason": null},
   {"id": "season:0", "level": "season", "season_number": 0, "episode_number": null, "title": "Specials",
    "date": "2026-10-26", "rating": null, "liked": false, "is_rewatch": false, "claims_viewing_id": null,
    "available": true, "reason": null},
   {"id": "show", "level": "show", "season_number": null, "episode_number": null, "title": "Whole show",
    "date": "2026-10-26", "rating": "9.0", "liked": false, "is_rewatch": false, "claims_viewing_id": null,
    "available": true, "reason": null}]},
 "season": null, "episode": null}
""",
)
SHOW_LIST_EXAMPLE = json.loads(
    """
{"version": 1793051500000, "in_library": true, "status": "Completed", "choice": null, "started": true,
 "finished": true, "caught_up": true, "watched_count": 20, "aired_count": 20, "up_next": null,
 "next_airing": {"season_number": 3, "episode_number": 1, "air_date": "2027-03-12"}, "premiere": null,
 "rewatch": null, "last_watched_on": "2026-10-20"}
""",
)
EPISODE_ITEM_EXAMPLE = json.loads(
    """
{"episode_number": 4, "title": "Fly", "overview": "...", "air_date": "2010-05-23", "runtime": "47m",
 "image_url": "...", "image_role": "still", "rating": "8.1",
 "tv": {"season_number": 3, "episode_number": 4, "is_special": false, "listed": true, "aired": true,
        "air_date": "2010-05-23", "watched": true, "viewing_count": 1, "log_count": 0, "can_tick": false,
        "untick_viewing_id": 9912, "untick_message": null, "can_mark_up_to": true, "mark_up_to_prompt": null,
        "user_rating": null, "liked": false, "is_up_next": false}}
""",
)
FINISHED_EXAMPLE = json.loads(
    """
{"level": "season", "season_number": 1, "show_finished": false,
 "composer": {"default_target": "season:1", "targets": [{"id": "season:1", "level": "season",
   "season_number": 1, "episode_number": null, "title": "Season 1", "date": "2026-10-26", "rating": null,
   "liked": false, "is_rewatch": false, "claims_viewing_id": null, "available": true, "reason": null}]}}
""",
)
EARLIER_EXAMPLE = json.loads(
    """
{"from": {"season_number": 1, "episode_number": 1}, "to": {"season_number": 2, "episode_number": 10},
 "count": 19, "seasons": [1, 2], "question": "Also mark Seasons 1\u20132 watched?", "offer_season_number": 3}
""",
)
ACTION_BODY_EXAMPLE = {
    "season_number": 2,
    "episode_number": 4,
    "viewing_id": 9912,
    "mutation_id": "7f1c6d8e-1a2b-4c3d-9e8f-0a1b2c3d4e5f",
    "expected_version": 1793051512345,
    "confirm": True,
    "from": {"season_number": 1, "episode_number": 1},
    "to": {"season_number": 2, "episode_number": 10},
    "offer_season_number": 3,
    "rating": 8,
}


class TVPayloadKeyTests(TVAPITestCase):
    """Exact key sets of every object in plan section 2.3."""

    def assert_show_state(self, state, *, detail):
        self.assertEqual(set(state), SHOW_DETAIL_KEYS if detail else SHOW_LIST_KEYS)
        for key, keys in (
            ("up_next", UP_NEXT_KEYS),
            ("next_airing", NEXT_AIRING_KEYS),
            ("premiere", PREMIERE_KEYS),
            ("rewatch", REWATCH_KEYS),
        ):
            if state[key] is not None:
                self.assertEqual(set(state[key]), keys, key)
        if detail:
            self.assert_eye(state["eye"])
            self.assert_composer(state["composer"])
            self.assertIsInstance(state["available_actions"], list)
            self.assertIsInstance(state["action_reasons"], dict)

    def assert_eye(self, eye):
        self.assertEqual(set(eye), EYE_KEYS)
        self.assertIn(eye["tap"], EYE_TAPS)

    def assert_composer(self, composer):
        self.assertEqual(set(composer), COMPOSER_KEYS)
        self.assertIn(composer["default_target"], {target["id"] for target in composer["targets"]})
        for target in composer["targets"]:
            self.assertEqual(set(target), TARGET_KEYS)

    def assert_season(self, season, *, detail):
        self.assertEqual(set(season), SEASON_DETAIL_KEYS if detail else SEASON_CARD_KEYS)
        self.assertIn(season["state"], SEASON_STATES)
        if detail:
            self.assert_eye(season["eye"])

    def assert_episode(self, episode, *, page):
        self.assertEqual(set(episode), EPISODE_PAGE_KEYS if page else EPISODE_KEYS)
        if page:
            self.assert_composer(episode["composer"])

    def test_show_state_detail_and_list_variants(self):
        show = self.make_show()
        self.tick(show, 1, 1)

        detail = self.detail(show).data["user_state"]["tv"]
        listed = self.client.get("/api/v1/tracking/", {"media_type": "tv"}).data["results"][0]

        self.assert_show_state(detail, detail=True)
        self.assertIsNone(detail["season"])
        self.assertIsNone(detail["episode"])
        self.assert_show_state(listed["tracking"]["tv"], detail=False)
        self.assert_show_state(listed["media"]["user_state"]["tv"], detail=False)

    def test_season_and_episode_states(self):
        show = self.make_show(seasons={0: 1, 1: 3})
        self.tick(show, 1, 1)

        show_page = self.detail(show).data
        season_page = self.detail(show, 1).data
        episode_page = self.detail(show, 1, 1).data

        for card in show_page["seasons"]:
            self.assert_season(card["tv"], detail=False)
        self.assert_show_state(season_page["user_state"]["tv"], detail=True)
        self.assert_season(season_page["user_state"]["tv"]["season"], detail=True)
        for episode in season_page["episodes"]:
            self.assert_episode(episode["tv"], page=False)
        self.assert_episode(episode_page["user_state"]["tv"]["episode"], page=True)
        self.assertEqual(set(episode_page["user_state"]["tv"]["episode"]["action_reasons"]), set())

    def test_write_envelopes(self):
        show = self.make_show(seasons={1: 2, 2: 2})

        action = self.tick(show, 2, 1).data
        finished = self.ticks(show, 1, [1, 2]).data["finished"]
        diary = self.log(show, 2, 2).data
        like = self.like(show, 1, 1).data

        self.assertEqual(set(action), ENVELOPE_KEYS | {"tracking"})
        self.assert_show_state(action["tv"], detail=True)
        self.assert_season(action["season"], detail=True)
        self.assert_episode(action["episode"], page=True)
        self.assertEqual(set(action["earlier_unwatched"]), EARLIER_KEYS)
        self.assertEqual(set(action["earlier_unwatched"]["from"]), COORDINATE_KEYS)
        self.assertEqual(set(action["earlier_unwatched"]["to"]), COORDINATE_KEYS)
        self.assertEqual(set(finished), FINISHED_KEYS)
        self.assert_composer(finished["composer"])
        self.assertTrue({"tv", "finished", "earlier_unwatched", "tv_log"} <= set(diary))
        self.assertEqual(set(diary["tv_log"]), TV_LOG_KEYS)
        self.assertTrue({"show_title", "episode_title"} <= set(diary["media"]))
        self.assertEqual(set(like), {"liked", "media", "tv", "season", "episode", "earlier_unwatched"})
        self.assert_show_state(like["media"]["user_state"]["tv"], detail=True)

    def test_unlisted_season_and_episode_entries(self):
        show = self.make_show(seasons={1: 2, 2: 2})
        self.tick(show, 2, 1)
        self.fake.show(show.media_id).remove_season(2)
        self.clock.advance(days=2)

        show_page = self.detail(show).data
        season_page = self.detail(show, 2).data

        self.assertEqual(set(show_page["tv_unlisted_seasons"][0]), {"season_number", "title", "tv"})
        self.assert_season(show_page["tv_unlisted_seasons"][0]["tv"], detail=False)
        self.assertEqual(set(season_page["tv_unlisted_episodes"][0]), {"episode_number", "title", "tv"})
        self.assert_episode(season_page["tv_unlisted_episodes"][0]["tv"], page=False)

    def test_error_envelope_carries_the_new_version(self):
        show = self.make_show(seasons={1: 2})
        version = self.ticks(show, 1, [1, 2]).data["tv"]["version"]

        response = self.action(show, "unmark", season_number=1, confirm=True, expected_version=version - 1)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(set(response.data["error"]), ERROR_KEYS | {"tv"})
        self.assertEqual(set(response.data["error"]["tv"]), {"state_version", "count"})

    def test_the_action_body_example_is_accepted(self):
        """Plan 2.3.7's example body validates; `from` is a field though a keyword."""
        from api.serializers.tracking import TVActionSerializer

        serializer = TVActionSerializer(data=ACTION_BODY_EXAMPLE)

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["from"], {"season_number": 1, "episode_number": 1})
        self.assertEqual(serializer.validated_data["rating"], 8)


class TVPayloadExampleTests(TVAPITestCase):
    """The plan's examples have our key sets, and the same steps give their values."""

    def test_show_detail_example(self):
        """Watching at S2 E4, with season and show stars, as plan 2.3.1 describes."""
        self.assertEqual(set(SHOW_DETAIL_EXAMPLE), SHOW_DETAIL_KEYS)
        self.assertEqual(set(SHOW_DETAIL_EXAMPLE["up_next"]), UP_NEXT_KEYS)
        self.assertEqual(set(SHOW_DETAIL_EXAMPLE["eye"]), EYE_KEYS)
        for target in SHOW_DETAIL_EXAMPLE["composer"]["targets"]:
            self.assertEqual(set(target), TARGET_KEYS)
        show = self.make_show(seasons={0: 2, 1: 10, 2: 10})
        self.fake.show(show.media_id).seasons[2][3] = FakeEpisode(4, date(2010, 5, 23), "Fly")
        self.ticks(show, 1, range(1, 11))
        self.ticks(show, 2, [1, 2, 3])
        self.action(show, "rate", season_number=1, rating=8)
        self.like(show, 1)
        self.action(show, "rate", rating=9)

        state = self.detail(show).data["user_state"]["tv"]

        for key in (
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
            "remove_count",
            "eye",
            "action_reasons",
            "season",
            "episode",
        ):
            self.assertEqual(state[key], SHOW_DETAIL_EXAMPLE[key], key)
        self.assertEqual(state["last_watched_on"], OCT_25.isoformat())
        self.assertCountEqual(state["available_actions"], SHOW_DETAIL_EXAMPLE["available_actions"])
        composer = state["composer"]
        self.assertEqual(composer["default_target"], SHOW_DETAIL_EXAMPLE["composer"]["default_target"])
        for target, expected in zip(composer["targets"], SHOW_DETAIL_EXAMPLE["composer"]["targets"], strict=True):
            for key in ("id", "level", "season_number", "title", "rating", "liked", "is_rewatch", "available"):
                self.assertEqual(target[key], expected[key], (target["id"], key))
            self.assertEqual(target["date"], OCT_25.isoformat())

    def test_show_list_example(self):
        """A Completed, returning show (TV-202): caught up, with the next airing."""
        self.assertEqual(set(SHOW_LIST_EXAMPLE), SHOW_LIST_KEYS)
        show = self.make_show(
            seasons={1: 10, 2: 10},
            status="Returning Series",
            next_episode_to_air={"season_number": 3, "episode_number": 1, "air_date": date(2027, 3, 12)},
        )
        self.log(show, consumed_at="2026-10-20")

        row = self.client.get("/api/v1/tracking/", {"media_type": "tv"}).data["results"][0]

        state = row["tracking"]["tv"]
        for key, value in SHOW_LIST_EXAMPLE.items():
            if key != "version":
                self.assertEqual(state[key], value, key)

    def test_episode_item_example(self):
        self.assertEqual(set(EPISODE_ITEM_EXAMPLE["tv"]), EPISODE_KEYS)
        show = self.make_show(seasons={1: 2, 2: 2, 3: 4})
        self.fake.show(show.media_id).seasons[3][3] = FakeEpisode(4, date(2010, 5, 23), "Fly")
        self.action(show, "mark_watched", season_number=1)
        self.action(show, "mark_watched", season_number=2)
        self.ticks(show, 3, [1, 2, 3])
        viewing_id = self.tick(show, 3, 4).data["episode"]["untick_viewing_id"]

        episodes = self.detail(show, 3).data["episodes"]

        episode = next(episode for episode in episodes if episode["episode_number"] == 4)
        self.assertTrue(set(EPISODE_ITEM_EXAMPLE) <= set(episode))
        expected = {**EPISODE_ITEM_EXAMPLE["tv"], "untick_viewing_id": viewing_id}
        self.assertEqual(episode["tv"], expected)

    def test_finished_example(self):
        self.assertEqual(set(FINISHED_EXAMPLE), FINISHED_KEYS)
        show = self.make_show()

        finished = self.ticks(show, 1, range(1, 11)).data["finished"]

        self.assertEqual(
            finished,
            {
                **FINISHED_EXAMPLE,
                "composer": {
                    "default_target": "season:1",
                    "targets": [{**FINISHED_EXAMPLE["composer"]["targets"][0], "date": OCT_25.isoformat()}],
                },
            },
        )

    def test_earlier_unwatched_example(self):
        """Logging Season 3 first asks about whole seasons (TV-107)."""
        self.assertEqual(set(EARLIER_EXAMPLE), EARLIER_KEYS)
        show = self.make_show(seasons={1: 9, 2: 10, 3: 10})

        earlier = self.log(show, 3).data["earlier_unwatched"]

        self.assertEqual(earlier, {**EARLIER_EXAMPLE, "offer_season_number": None})


class TVSectionCardTests(TVAPITestCase):
    """TV-700 (server): each section's list row carries what its card line names."""

    def row(self, show):
        results = self.client.get("/api/v1/tracking/", {"media_type": "tv"}).data["results"]
        return next(row for row in results if row["media"]["ref"]["media_id"] == show.media_id)

    def test_tv_700_each_section_has_its_card_data(self):
        watching = self.make_show(title="Watching")
        planning = self.make_show(title="Planning", seasons={1: [date(2027, 3, 12)]})
        paused = self.make_show(title="Paused")
        dropped = self.make_show(title="Dropped")
        self.ticks(watching, 1, [1, 2, 3])
        self.action(planning, "planning")
        self.ticks(paused, 1, [1, 2])
        self.action(paused, "pause")
        self.ticks(dropped, 2, [1, 2, 3])
        self.action(dropped, "drop")

        watching_tv = self.row(watching)["tracking"]["tv"]
        planning_tv = self.row(planning)["tracking"]["tv"]
        paused_tv = self.row(paused)["tracking"]["tv"]
        dropped_tv = self.row(dropped)["tracking"]["tv"]

        # "S1 E4 next · 3 of 18"
        self.assertEqual((watching_tv["up_next"]["season_number"], watching_tv["up_next"]["episode_number"]), (1, 4))
        self.assertEqual((watching_tv["watched_count"], watching_tv["aired_count"]), (3, 18))
        # "Premieres Mar 12"
        self.assertEqual(planning_tv["status"], "Planning")
        self.assertEqual(planning_tv["premiere"], {"season_number": 1, "air_date": "2027-03-12"})
        # "Paused at S1 E3", "Stopped at S2 E4"
        self.assertEqual((paused_tv["status"], paused_tv["up_next"]["episode_number"]), ("Paused", 3))
        self.assertEqual(
            (dropped_tv["status"], dropped_tv["up_next"]["season_number"], dropped_tv["up_next"]["episode_number"]),
            ("Dropped", 2, 4),
        )


class TVInstalledBuildKeyTests(TVAPITestCase):
    """TV-900: every key installed builds read keeps its meaning (plan 2.5)."""

    USER_STATE_KEYS = {
        "is_tracked",
        "tracking_id",
        "status",
        "rating",
        "in_lists",
        "diary_entry_id",
        "diary_count",
        "diary_rating",
        "diary_consumed_at",
        "has_liked",
    }
    TRACKING_KEYS = {
        "tracking_id",
        "status",
        "rating",
        "progress",
        "latest_progress_change",
        "repeats",
        "start_date",
        "end_date",
        "notes",
        "updated_at",
    }

    def test_tv_900_user_state_and_tracking_state_keys(self):
        show = self.make_show()
        self.tick(show, 1, 1)
        self.action(show, "rate", rating=8)

        user_state = self.detail(show).data["user_state"]
        tracking = self.client.get(f"/api/v1/tracking/tmdb/tv/{show.media_id}/").data
        listed = self.client.get("/api/v1/tracking/", {"media_type": "tv"}).data["results"][0]

        self.assertTrue(set(user_state) >= self.USER_STATE_KEYS)
        self.assertTrue(user_state["is_tracked"])
        self.assertEqual(user_state["status"], "In progress")
        self.assertEqual(user_state["rating"], "8.0")
        self.assertTrue(set(tracking) >= self.TRACKING_KEYS)
        self.assertEqual(set(tracking["progress"]), {"kind", "value", "max", "unit"})
        self.assertEqual(tracking["progress"]["kind"], "episodes")
        self.assertEqual(set(listed), {"media", "tracking"})
        self.assertTrue(set(listed["tracking"]) >= self.TRACKING_KEYS)

    def test_tv_205_an_unstarted_season_reports_untracked_but_keeps_its_opinions(self):
        show = self.make_show()
        self.tick(show, 1, 1)
        self.action(show, "rate", season_number=2, rating=7)
        self.like(show, 2)

        user_state = self.detail(show, 2).data["user_state"]

        self.assertFalse(user_state["is_tracked"])
        self.assertIsNone(user_state["status"])
        self.assertEqual(user_state["rating"], "7.0")
        self.assertTrue(user_state["has_liked"])
        self.assertFalse(user_state["tv"]["season"]["started"])

    def test_tv_900_episode_is_tracked_means_a_viewing(self):
        show = self.make_show()
        self.tick(show, 1, 1)

        watched = self.detail(show, 1, 1).data["user_state"]
        unwatched = self.detail(show, 1, 2).data["user_state"]

        self.assertTrue(watched["is_tracked"])
        self.assertFalse(unwatched["is_tracked"])

    def test_tv_900_season_and_episode_lists_keep_their_keys(self):
        show = self.make_show(seasons={0: 1, 1: 3})
        self.tick(show, 1, 1)

        show_page = self.detail(show).data
        season_page = self.detail(show, 1).data

        for season in show_page["seasons"]:
            self.assertTrue(
                {"season_number", "title", "episode_count", "image_url", "release_date", "completion"} <= set(season),
            )
        self.assertEqual(set(show_page["completion"]), {"completed_count", "total_count"})
        for episode in season_page["episodes"]:
            self.assertTrue(
                {"episode_number", "title", "overview", "air_date", "runtime", "image_url", "rating"} <= set(episode),
            )
        self.assertEqual(len(season_page["episodes"]), 3)

    def test_tv_900_like_response_and_log_dates(self):
        show = self.make_show()

        like = self.like(show).data
        entry = self.installed_build_log(show, 1, 1, rating=8).data
        activity = self.client.get(f"/api/v1/users/{self.user.username}/activity/").data["results"][0]

        self.assertTrue({"liked", "media"} <= set(like))
        self.assertEqual(str(entry["consumed_at"])[:10], OCT_25.isoformat())
        # Activity object.rating stays on the 0-10 scale (TV-504).
        self.assertEqual(activity["type"], "diary_created")
        self.assertEqual(activity["object"]["rating"], "8.0")
