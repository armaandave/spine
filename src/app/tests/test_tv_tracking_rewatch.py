"""Rewatch (docs/tv-tracking-contract.md, TV-600 to TV-603), with A-35 and A-36."""

from datetime import date

from app import tv_tracking
from app.models import Episode
from app.tests.test_tv_tracking_viewings import TVTrackingTestCase
from app.tv_tracking import TVConflict, TVLogBlocks


class TV600StartRewatchTests(TVTrackingTestCase):
    """TV-600: rewatching a show or season starts a rewatch."""

    def test_tv_600_a_show_rewatch_records_nothing_and_moves_to_watching(self):
        show = self.make_show({1: 2, 2: 2})
        tv_tracking.mark_watched(self.user, show)
        count = len(self.viewings(show))
        self.advance()
        result = tv_tracking.start_rewatch(self.user, show)
        self.assertTrue(result.changed)
        self.assertEqual(len(self.viewings(show)), count)
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertEqual(state["rewatch"]["scope"], "show")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 1))
        self.assertEqual((state["watched_count"], state["aired_count"]), (0, 4))
        self.assertIn("stop_rewatch", state["available_actions"])
        self.assertEqual(state["action_reasons"]["rewatch"], "Finish or stop your rewatch first.")

    def test_tv_600_an_unfinished_show_or_season_cant_be_rewatched(self):
        show = self.make_show({1: 2, 2: 2})
        self.tick(show, 1, 1)
        with self.assertRaises(TVConflict) as raised:
            tv_tracking.start_rewatch(self.user, show)
        self.assertEqual(raised.exception.message, "Finish this show before rewatching it.")
        with self.assertRaises(TVConflict) as raised:
            tv_tracking.start_rewatch(self.user, show, season_number=1)
        self.assertEqual(raised.exception.message, "Finish this season before rewatching it.")
        self.assertNotIn("rewatch", self.state(show)["available_actions"])

    def test_tv_600_one_rewatch_at_a_time(self):
        show = self.make_show({1: 2, 2: 2})
        tv_tracking.mark_watched(self.user, show)
        self.advance()
        tv_tracking.start_rewatch(self.user, show, season_number=1)
        with self.assertRaises(TVConflict) as raised:
            tv_tracking.start_rewatch(self.user, show, season_number=2)
        self.assertEqual(raised.exception.message, "Finish or stop your rewatch first.")
        season = tv_tracking.state_payload(self.user, self.season_item(show, 2))["season"]
        self.assertEqual(season["action_reasons"]["rewatch_season"], "Finish or stop your rewatch first.")

    def test_tv_600_a_season_rewatch(self):
        show = self.make_show({1: 2, 2: 2})
        tv_tracking.mark_watched(self.user, show)
        self.advance()
        tv_tracking.start_rewatch(self.user, show, season_number=2)
        state = self.state(show)
        self.assertEqual(state["rewatch"]["scope"], "season")
        self.assertEqual(state["rewatch"]["season_number"], 2)
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (2, 1))
        cards = tv_tracking.season_card_states(self.user, show, None)
        self.assertEqual((cards[1]["state"], cards[1]["rewatching"]), ("watched", False))
        self.assertEqual((cards[2]["watched_count"], cards[2]["rewatching"]), (0, True))


class TV601PassTests(TVTrackingTestCase):
    """TV-601: a viewing counts toward a rewatch when it's recorded after it began."""

    def test_tv_601_membership_goes_by_when_recorded_not_by_date(self):
        show = self.make_show({1: 3})
        self.ticks(show, 1, (1, 2, 3))
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        entry = self.log(self.episode_item(show, 1, 1), consumed_at=date(2020, 1, 1)).entry
        self.assertEqual(self.state(show)["watched_count"], 1)
        tv_tracking.update_log(entry, {"consumed_at": date(2019, 6, 1)})
        self.assertEqual(self.state(show)["watched_count"], 1)

    def test_tv_601_a_tick_of_an_episode_watched_before_the_rewatch_records_one(self):
        show = self.make_show({1: 2})
        self.ticks(show, 1, (1, 2))
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        self.assertTrue(self.tick(show, 1, 1).changed)
        self.assertFalse(self.tick(show, 1, 1).changed)
        self.assertEqual(len(self.viewings(show, 1, 1)), 2)

    def test_tv_601_an_untick_never_reaches_outside_the_pass(self):
        show = self.make_show({1: 2})
        self.ticks(show, 1, (1, 2))
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        result = self.untick(show, 1, 1)
        self.assertFalse(result.changed)
        self.assertEqual(len(self.viewings(show, 1, 1)), 1)

    def test_tv_601_a_pre_rewatch_season_log_doesnt_block_the_rewatch(self):
        show = self.make_show({1: 2})
        self.log(self.season_item(show, 1))
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        self.tick(show, 1, 1)
        self.untick(show, 1, 1)
        self.assertEqual(self.state(show)["watched_count"], 0)

    def test_tv_601_a_log_saved_during_the_rewatch_covers_it(self):
        show = self.make_show({1: 2, 2: 1})
        tv_tracking.mark_watched(self.user, show)
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        self.tick(show, 1, 1)
        self.log(self.season_item(show, 1))
        with self.assertRaises(TVLogBlocks):
            self.untick(show, 1, 1)


class TV602EndTests(TVTrackingTestCase):
    """TV-602: a rewatch ends when it's finished or stopped."""

    def test_a_35_rewatch_and_tick_through(self):
        """A-35: each season's composer opens with Rewatch on; Completed at the end."""
        show = self.make_show({1: 2, 2: 2})
        self.ticks(show, 1, (1, 2))
        self.ticks(show, 2, (1, 2))
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        self.tick(show, 1, 1)
        state = self.state(show)
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 2))
        finished_1 = self.tick(show, 1, 2).finished
        self.assertEqual(finished_1["season_number"], 1)
        self.assertTrue(finished_1["composer"]["targets"][0]["is_rewatch"])
        self.tick(show, 2, 1)
        finished_2 = self.tick(show, 2, 2).finished
        self.assertEqual(finished_2["season_number"], 2)
        self.assertTrue(finished_2["composer"]["targets"][0]["is_rewatch"])
        self.assertTrue(finished_2["show_finished"])
        tv = self.tv(show)
        self.assertIsNone(tv.rewatch_started_at)
        self.assertEqual(tv.status, "Completed")
        self.assertEqual(len(self.viewings(show)), 8)

    def test_a_36_stop_rewatching_midway_keeps_everything(self):
        show = self.make_show({1: 2, 2: 2})
        tv_tracking.mark_watched(self.user, show)
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        self.tick(show, 1, 1)
        tv_tracking.stop_rewatch(self.user, show)
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.rewatch_started_at), ("Completed", None))
        self.assertEqual(len(self.viewings(show)), 5)
        with self.assertRaises(TVConflict):
            tv_tracking.stop_rewatch(self.user, show)

    def test_tv_602_pause_and_drop_keep_the_rewatch_and_resume_continues(self):
        show = self.make_show({1: 3})
        tv_tracking.mark_watched(self.user, show)
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        self.tick(show, 1, 1)
        tv_tracking.choose(self.user, show, "Paused")
        self.assertIsNotNone(self.tv(show).rewatch_started_at)
        tv_tracking.choose(self.user, show, "In progress")
        state = self.state(show)
        self.assertEqual(state["rewatch"]["scope"], "show")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 2))

    def test_tv_602_the_show_eye_marks_the_rest_of_the_rewatch(self):
        show = self.make_show({1: 3})
        tv_tracking.mark_watched(self.user, show)
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        self.tick(show, 1, 1)
        eye = self.state(show)["eye"]
        self.assertEqual(eye["prompt"], "Mark the rest of this rewatch watched?")
        self.assertEqual(eye["count"], 2)
        tv_tracking.mark_watched(self.user, show)
        tv = self.tv(show)
        self.assertIsNone(tv.rewatch_started_at)
        self.assertEqual(tv.status, "Completed")


class TV603EpisodeRewatchTests(TVTrackingTestCase):
    """TV-603: rewatching one episode never reopens anything."""

    def test_tv_603_a_rewatch_log_adds_a_viewing_and_starts_no_rewatch(self):
        show = self.make_show({1: 2})
        tv_tracking.mark_watched(self.user, show)
        result = self.log(self.episode_item(show, 1, 1), is_rewatch=True)
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.rewatch_started_at), ("Completed", None))
        self.assertIsNone(result.finished)
        kinds = sorted(row.kind for row in self.viewings(show, 1, 1))
        self.assertEqual(kinds, [Episode.Kind.FILL, Episode.Kind.LOG])


class TV404RewatchComposerTests(TVTrackingTestCase):
    """TV-404 with TV-402: during a rewatch the composer is on and still claims the tick."""

    def test_tv_404_a_rewatch_tick_is_claimed_with_rewatch_on(self):
        show = self.make_show({1: 2})
        self.ticks(show, 1, (1, 2))
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        self.tick(show, 1, 1)
        tick = self.viewings(show, 1, 1)[-1]
        target = self.state(self.episode_item(show, 1, 1))["composer"]["targets"][0]
        self.assertTrue(target["is_rewatch"])
        self.assertEqual(target["claims_viewing_id"], tick.pk)
        entry = self.log(self.episode_item(show, 1, 1), is_rewatch=True).entry
        tick.refresh_from_db()
        self.assertEqual(tick.diary_entry_id, entry.pk)
        self.assertEqual(len(self.viewings(show, 1, 1)), 2)
