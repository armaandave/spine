"""Library unit and derived status (docs/tv-tracking-contract.md).

TV-001, TV-003, TV-005 to TV-007, TV-200 to TV-205, TV-208 and the status order of
plan 1.2: a Planning, Paused or Dropped choice; then a rewatch (Watching); then
finished (Completed); then started or a Watching choice (Watching).
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from app import tv_tracking
from app.models import TV, Episode, MediaLike, Season
from app.tests.test_tv_tracking_viewings import OCT_25, TVTrackingTestCase
from app.tv_tracking import TVConflict, TVInvalid, TVLogBlocks, TVStateChanged


class StatusOrderTests(TVTrackingTestCase):
    """The four-step status order (plan 1.2, A-54)."""

    def test_a_54_drop_a_finished_show_then_choose_watching_is_completed(self):
        show = self.make_show({1: 2})
        tv_tracking.mark_watched(self.user, show)
        tv_tracking.choose(self.user, show, "Dropped")
        self.assertEqual(self.status(show), "Dropped")
        tv_tracking.choose(self.user, show, "In progress")
        self.assertEqual(self.status(show), "Completed")

    def test_status_order_choose_watching_then_mark_watched_is_completed(self):
        show = self.make_show({1: 2})
        tv_tracking.choose(self.user, show, "In progress")
        self.assertEqual(self.status(show), "In progress")
        tv_tracking.mark_watched(self.user, show)
        self.assertEqual(self.status(show), "Completed")

    def test_status_order_a_choice_beats_a_rewatch_and_a_rewatch_beats_finished(self):
        show = self.make_show({1: 2})
        tv_tracking.mark_watched(self.user, show)
        self.advance()
        tv_tracking.start_rewatch(self.user, show)
        self.assertEqual(self.status(show), "In progress")
        tv_tracking.choose(self.user, show, "Paused")
        self.assertEqual(self.status(show), "Paused")
        tv_tracking.choose(self.user, show, "In progress")
        self.assertEqual(self.status(show), "In progress")
        self.assertIsNotNone(self.tv(show).rewatch_started_at)

    def test_status_order_started_without_a_choice_is_watching(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        tv = self.tv(show)
        self.assertEqual((tv.choice, tv.status), ("", "In progress"))


class TV001LibraryUnitTests(TVTrackingTestCase):
    """TV-001 (server): the show is the one library card."""

    def test_tv_001_marking_a_show_adds_it_once_and_marks_its_seasons(self):
        show = self.make_show({1: 3, 2: 3, 3: 2})
        tv_tracking.mark_watched(self.user, show)
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.season_numbers(show), [1, 2, 3])
        payload = tv_tracking.list_payload(self.tv(show))
        self.assertTrue(payload["in_library"])
        self.assertEqual(payload["status"], "Completed")
        self.assertEqual((payload["watched_count"], payload["aired_count"]), (8, 8))
        cards = tv_tracking.season_card_states(self.user, show, None)
        self.assertEqual({number: card["state"] for number, card in cards.items()}, {1: "watched", 2: "watched", 3: "watched"})


class TV003MembershipTests(TVTrackingTestCase):
    """TV-003 (server): membership follows tracking; Remove from Library."""

    def test_tv_003_each_fact_puts_the_show_in_the_library(self):
        for act in (
            lambda show: self.tick(show, 1, 1),
            lambda show: self.log(self.season_item(show, 1)),
            lambda show: tv_tracking.mark_watched(self.user, show, season_number=1),
            lambda show: tv_tracking.choose(self.user, show, "Planning"),
            lambda show: tv_tracking.set_like(self.user, show, True),
        ):
            show = self.make_show({1: 2})
            act(show)
            self.assertTrue(tv_tracking.state_payload(self.user, show)["in_library"])

    def test_a_44_remove_is_blocked_by_logs(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        self.log(self.episode_item(show, 1, 2))
        with self.assertRaises(TVLogBlocks) as raised:
            tv_tracking.remove(self.user, show, mutation_id=uuid.uuid4())
        self.assertEqual(raised.exception.message, "Delete this show's logs first.")
        self.assertIsNotNone(self.tv(show))
        state = self.state(show)
        self.assertNotIn("remove", state["available_actions"])
        self.assertEqual(state["action_reasons"]["remove"], "Delete this show's logs first.")

    def test_a_44_remove_without_logs_removes_everything_and_keeps_lists(self):
        from lists.models import CustomList, CustomListItem
        from social.models import Activity

        show = self.make_show({1: 3, 2: 2})
        self.ticks(show, 1, (1, 2))
        tv_tracking.mark_watched(self.user, show, season_number=2)
        tv_tracking.set_rating(self.user, show, 8)
        tv_tracking.set_rating(self.user, self.season_item(show, 1), 6)
        tv_tracking.set_like(self.user, self.season_item(show, 2), True)
        tv_tracking.set_like(self.user, self.episode_item(show, 1, 1), True)
        custom = CustomList.objects.create(name="Mine", owner=self.user)
        CustomListItem.objects.create(item=show, custom_list=custom)
        state = self.state(show)
        self.assertEqual(state["remove_count"], 4)

        result = tv_tracking.remove(
            self.user,
            show,
            expected_version=state["version"],
            mutation_id=uuid.uuid4(),
        )

        self.assertTrue(result.changed)
        self.assertIsNone(self.tv(show))
        self.assertFalse(Season.objects.exists())
        self.assertFalse(Episode.objects.exists())
        self.assertFalse(MediaLike.objects.filter(user=self.user).exists())
        self.assertFalse(Activity.objects.filter(verb="rating_updated").exists())
        self.assertTrue(CustomListItem.objects.filter(item=show, custom_list=custom).exists())

    def test_tv_705_remove_with_a_stale_version_returns_the_new_count(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        version = self.state(show)["version"]
        self.tick(show, 1, 2)
        with self.assertRaises(TVStateChanged) as raised:
            tv_tracking.remove(self.user, show, expected_version=version, mutation_id=uuid.uuid4())
        self.assertEqual(raised.exception.extra["count"], 2)
        self.assertEqual(raised.exception.extra["state_version"], self.tv(show).state_version)
        self.assertIsNotNone(self.tv(show))

    def test_tv_003_remove_from_the_app_needs_a_mutation_id(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        with self.assertRaises(TVInvalid):
            tv_tracking.remove(self.user, show)
        tv_tracking.remove(self.user, show, entry_point=tv_tracking.WEB)
        self.assertIsNone(self.tv(show))


class TV005WatchingTests(TVTrackingTestCase):
    """TV-005: choosing Watching saves in one tap and records nothing else."""

    def test_tv_005_watching_on_a_new_show_records_no_viewing_or_season(self):
        show = self.make_show({1: 3, 2: 3})
        tv_tracking.choose(self.user, show, "In progress")
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertEqual(state["choice"], "In progress")
        self.assertEqual(state["selected_action"], "watching")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 1))
        self.assertEqual(self.viewings(show), [])
        self.assertEqual(self.season_numbers(show), [])

    def test_tv_005_watching_is_unavailable_on_a_finished_show(self):
        show = self.make_show({1: 2})
        tv_tracking.mark_watched(self.user, show)
        with self.assertRaises(TVConflict) as raised:
            tv_tracking.choose(self.user, show, "In progress")
        self.assertEqual(raised.exception.message, "You've watched everything that has aired.")
        state = self.state(show)
        self.assertNotIn("watching", state["available_actions"])
        self.assertEqual(state["action_reasons"]["watching"], "You've watched everything that has aired.")

    def test_tv_005_watching_is_unavailable_before_anything_aired(self):
        show = self.make_show({1: [OCT_25 + timedelta(days=30)]})
        with self.assertRaises(TVConflict) as raised:
            tv_tracking.choose(self.user, show, "In progress")
        self.assertEqual(raised.exception.message, "Nothing has aired yet.")
        self.assertIsNone(self.tv(show))


class TV006PlanningTests(TVTrackingTestCase):
    """TV-006: Planning is only for shows you haven't watched."""

    def test_a_39_planning_on_a_show_with_a_viewing_is_unavailable(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        with self.assertRaises(TVConflict) as raised:
            tv_tracking.choose(self.user, show, "Planning")
        self.assertEqual(raised.exception.message, "You've started this show. Pause or drop it instead.")
        self.assertEqual(
            self.state(show)["action_reasons"]["planning"],
            "You've started this show. Pause or drop it instead.",
        )
        self.assertEqual(self.status(show), "In progress")

    def test_tv_006_a_specials_mark_also_makes_planning_unavailable(self):
        show = self.make_show({0: 2, 1: 3})
        tv_tracking.mark_watched(self.user, show, season_number=0)
        with self.assertRaises(TVConflict):
            tv_tracking.choose(self.user, show, "Planning")

    def test_tv_006_planning_records_nothing_and_a_tick_ends_it(self):
        show = self.make_show({1: 3})
        tv_tracking.choose(self.user, show, "Planning")
        self.assertEqual(self.status(show), "Planning")
        self.assertEqual(self.season_numbers(show), [])
        self.tick(show, 1, 1)
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.choice, tv.ended_choice), ("In progress", "", "Planning"))


class TV007PauseDropTests(TVTrackingTestCase):
    """TV-007 (server): Pause and Drop keep progress; Resume continues."""

    def test_tv_007_pause_and_drop_keep_everything(self):
        show = self.make_show({1: 5, 2: 5})
        self.ticks(show, 1, (1, 2, 3))
        self.log(self.episode_item(show, 1, 3))
        tv_tracking.set_rating(self.user, show, 7)
        before = (len(self.viewings(show)), self.state(show)["up_next"])
        tv_tracking.choose(self.user, show, "Paused")
        self.assertEqual(self.status(show), "Paused")
        tv_tracking.choose(self.user, show, "Dropped")
        self.assertEqual(self.status(show), "Dropped")
        self.assertEqual((len(self.viewings(show)), self.state(show)["up_next"]), before)
        self.assertEqual(str(self.tv(show).score), "7.0")
        tv_tracking.choose(self.user, show, "In progress")
        self.assertEqual(self.status(show), "In progress")
        self.assertEqual(self.state(show)["up_next"], before[1])

    def test_tv_007_pause_and_drop_on_a_show_with_nothing_watched(self):
        show = self.make_show({1: 3})
        tv_tracking.choose(self.user, show, "Dropped")
        self.assertEqual(self.status(show), "Dropped")
        self.assertEqual(self.viewings(show), [])
        state = self.state(show)
        self.assertEqual(state["selected_action"], "drop")
        self.assertNotIn("drop", state["available_actions"])

    def test_tv_007_resume_is_offered_only_when_paused(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        self.assertNotIn("resume", self.state(show)["available_actions"])
        tv_tracking.choose(self.user, show, "Paused")
        state = self.state(show)
        self.assertIn("resume", state["available_actions"])
        self.assertIn("watching", state["available_actions"])
        self.assertEqual(state["selected_action"], "pause")


class TV200SeasonFinishedTests(TVTrackingTestCase):
    """TV-200: a season is finished when you've watched all of it."""

    def test_a_06_skipping_e4_isnt_finished_and_names_the_gap(self):
        """A-06: "9 of 10 · E4 not watched", no composer, up next S2 E1."""
        show = self.make_show({1: 10, 2: 10})
        results = [self.tick(show, 1, episode) for episode in (1, 2, 3, *range(5, 11))]
        self.assertTrue(all(result.finished is None for result in results))
        season = tv_tracking.state_payload(self.user, self.season_item(show, 1))["season"]
        self.assertEqual((season["watched_count"], season["aired_count"]), (9, 10))
        self.assertEqual(season["first_gap_episode"], 4)
        self.assertEqual(season["state"], "in_progress")
        state = self.state(show)
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (2, 1))
        self.assertEqual(Season.objects.get(item__season_number=1).first_gap_episode, 4)

    def test_tv_200_a_season_still_airing_reads_airing(self):
        show = self.make_show({1: [date(2026, 10, 1), date(2026, 10, 8), OCT_25 + timedelta(days=4)]})
        self.ticks(show, 1, (1, 2))
        season = tv_tracking.state_payload(self.user, self.season_item(show, 1))["season"]
        self.assertEqual(season["state"], "airing")
        self.assertTrue(season["eye"]["filled"])
        self.assertEqual(season["status"], "In progress")
        self.assertEqual(season["next_air_date"], (OCT_25 + timedelta(days=4)).isoformat())

    def test_a_64_a_named_next_episode_tmdb_doesnt_list_keeps_the_season_airing(self):
        """A-64: "Airing", not finished, no composer; the show is Watching and caught up."""
        thursday = OCT_25 + timedelta(days=4)
        show = self.make_show(
            {1: 6},
            status="Returning Series",
            next_episode_to_air={"season_number": 1, "episode_number": 7, "air_date": thursday},
        )
        results = [self.tick(show, 1, episode) for episode in range(1, 7)]
        self.assertIsNone(results[-1].finished)
        season = tv_tracking.state_payload(self.user, self.season_item(show, 1))["season"]
        self.assertEqual(season["state"], "airing")
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertTrue(state["caught_up"])
        self.assertFalse(state["finished"])
        self.assertEqual(
            state["next_airing"],
            {"season_number": 1, "episode_number": 7, "air_date": thursday.isoformat()},
        )
        self.assertIsNone(state["up_next"])

    def test_tv_200_the_stored_season_status_follows(self):
        show = self.make_show({1: 2, 2: 2})
        self.ticks(show, 1, (1, 2))
        self.tick(show, 2, 1)
        statuses = dict(Season.objects.values_list("item__season_number", "status"))
        self.assertEqual(statuses, {1: "Completed", 2: "In progress"})


class TV201ShowFinishedTests(TVTrackingTestCase):
    """TV-201: a show is finished when every season that has started airing is."""

    def test_tv_201_earlier_unwatched_seasons_keep_the_show_unfinished(self):
        show = self.make_show({1: 2, 2: 2})
        self.ticks(show, 2, (1, 2))
        state = self.state(show)
        self.assertFalse(state["finished"])
        self.assertEqual(state["status"], "In progress")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 1))

    def test_tv_201_seasons_that_havent_started_airing_dont_count(self):
        show = self.make_show({1: 2, 2: [OCT_25 + timedelta(days=60)]})
        self.ticks(show, 1, (1, 2))
        state = self.state(show)
        self.assertTrue(state["finished"])
        self.assertEqual(state["status"], "Completed")


class TV202CaughtUpTests(TVTrackingTestCase):
    """TV-202: caught up shows when more is coming."""

    def test_a_09_caught_up_between_seasons_is_completed_with_the_next_date(self):
        """A-09 (domain): Completed, caught up, S3 on its date; then it airs."""
        premiere = OCT_25 + timedelta(days=20)
        show = self.make_show(
            {1: 2, 2: 2},
            status="Returning Series",
            next_episode_to_air={"season_number": 3, "episode_number": 1, "air_date": premiere},
        )
        tv_tracking.mark_watched(self.user, show)
        state = self.state(show)
        self.assertEqual(state["status"], "Completed")
        self.assertTrue(state["caught_up"])
        self.assertEqual(state["next_airing"], {"season_number": 3, "episode_number": 1, "air_date": premiere.isoformat()})
        listing = self.tv(show)
        self.assertEqual(listing.next_air_date, premiere)

        # TV-206: the episode airs and the hourly job works the show out.
        provider = self.fake.show(show.media_id)
        provider.set_season(3, [premiere, premiere + timedelta(days=7)])
        provider.next_episode_to_air = {"season_number": 3, "episode_number": 2, "air_date": premiere + timedelta(days=7)}
        self.clock.advance(days=21)
        tv_tracking.refresh_after_calendar(show)
        tv = self.tv(show)
        self.assertEqual(tv.status, "In progress")
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (3, 1))
        self.assertTrue(tv.up_next_is_new)

    def test_a_10_caught_up_mid_season_stays_watching(self):
        """A-10 (domain): Watching, caught up, up next empty until E7 airs."""
        thursday = OCT_25 + timedelta(days=4)
        episodes = [OCT_25 - timedelta(days=7 * (6 - number)) for number in range(1, 7)] + [thursday]
        show = self.make_show({1: episodes}, status="Returning Series")
        self.ticks(show, 1, range(1, 7))
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertTrue(state["caught_up"])
        self.assertIsNone(state["up_next"])
        self.assertEqual(state["next_airing"]["episode_number"], 7)
        self.clock.advance(days=5)
        self.assertTrue(tv_tracking.refresh_due(self.tv(show)))
        tv = self.tv(show)
        self.assertFalse(tv.caught_up)
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (1, 7))

    def test_tv_202_an_ended_show_is_completed_without_caught_up(self):
        show = self.make_show({1: 2}, status="Ended")
        tv_tracking.mark_watched(self.user, show)
        state = self.state(show)
        self.assertEqual(state["status"], "Completed")
        self.assertFalse(state["caught_up"])


class TV203ChoiceStaysTests(TVTrackingTestCase):
    """TV-203: a Planning, Paused or Dropped choice stays until the user acts."""

    def test_tv_203_airing_refreshes_and_opinions_keep_the_choice(self):
        show = self.make_show({1: [OCT_25 - timedelta(days=14), OCT_25 + timedelta(days=3)]})
        self.tick(show, 1, 1)
        tv_tracking.choose(self.user, show, "Dropped")
        tv_tracking.set_rating(self.user, show, 4)
        tv_tracking.set_like(self.user, show, True)
        self.clock.advance(days=5)
        tv_tracking.refresh_due(self.tv(show))
        tv_tracking.refresh_after_calendar(show)
        self.assertEqual(self.status(show), "Dropped")
        self.assertEqual(self.tv(show).choice, "Dropped")


class TV204SeasonsFollowTheShowTests(TVTrackingTestCase):
    """TV-204: seasons show the show's Paused or Dropped."""

    def test_tv_204_started_unfinished_seasons_follow_and_resume_returns(self):
        show = self.make_show({1: 2, 2: 3, 3: 2})
        self.ticks(show, 1, (1, 2))
        self.tick(show, 2, 1)
        tv_tracking.choose(self.user, show, "Paused")
        statuses = dict(Season.objects.values_list("item__season_number", "status"))
        self.assertEqual(statuses, {1: "Completed", 2: "Paused"})
        tv_tracking.choose(self.user, show, "In progress")
        statuses = dict(Season.objects.values_list("item__season_number", "status"))
        self.assertEqual(statuses, {1: "Completed", 2: "In progress"})
        self.assertEqual(len(self.viewings(show)), 3)

    def test_tv_204_season_choices_are_not_actions(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 1)
        with self.assertRaises(TVInvalid):
            tv_tracking.apply_written_state(
                self.user,
                self.season_item(show, 1),
                {"status": "Dropped"},
                entry_point=tv_tracking.APP,
            )
        tv_tracking.apply_written_state(
            self.user,
            self.season_item(show, 1),
            {"status": "Dropped"},
            entry_point=tv_tracking.WEB,
        )
        self.assertEqual(self.status(show), "In progress")


class TV205NoEmptySeasonsTests(TVTrackingTestCase):
    """TV-205: Spine never creates empty seasons."""

    def test_a_08_finishing_season_1_moves_up_next_and_creates_no_season_2(self):
        show = self.make_show({1: 3, 2: 3})
        self.ticks(show, 1, (1, 2, 3))
        state = self.state(show)
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (2, 1))
        self.assertEqual(self.season_numbers(show), [1])

    def test_tv_205_a_season_losing_its_last_viewing_loses_its_row_and_opinions(self):
        show = self.make_show({1: 2, 2: 2})
        self.tick(show, 1, 1)
        self.tick(show, 2, 1)
        season_2 = self.season_item(show, 2)
        tv_tracking.set_rating(self.user, season_2, 7)
        tv_tracking.set_like(self.user, season_2, True)
        self.untick(show, 2, 1)
        self.assertEqual(self.season_numbers(show), [1])
        self.assertFalse(MediaLike.objects.filter(item=season_2).exists())

    def test_tv_205_an_unstarted_legacy_season_counts_as_not_started(self):
        show = self.make_show({1: 2, 2: 2})
        self.tick(show, 1, 1)
        tv = self.tv(show)
        Season.objects.create(
            item=self.season_item(show, 2),
            user=self.user,
            related_tv=tv,
            status="In progress",
        )
        self.tick(show, 1, 2)
        season = tv_tracking.state_payload(self.user, self.season_item(show, 2))["season"]
        self.assertFalse(season["started"])
        self.assertIsNone(season["status"])
        self.assertEqual(season["state"], "not_started")
        self.assertFalse(Season.objects.get(item__season_number=2).is_started)


class TV208UndoRestoresChoiceTests(TVTrackingTestCase):
    """TV-208: undoing the write that ended a choice restores the choice."""

    def test_a_37_pause_tick_by_mistake_then_untick_returns_to_paused(self):
        show = self.make_show({1: 5, 2: 5})
        self.ticks(show, 1, range(1, 6))
        self.ticks(show, 2, (1, 2))
        tv_tracking.choose(self.user, show, "Paused")
        self.advance(minutes=5)
        self.tick(show, 2, 3)
        self.assertEqual(self.status(show), "In progress")
        self.untick(show, 2, 3)
        self.assertEqual(self.status(show), "Paused")
        self.assertEqual(self.tv(show).choice, "Paused")

    def test_tv_208_a_new_choice_since_blocks_the_restore(self):
        show = self.make_show({1: 5})
        self.tick(show, 1, 1)
        tv_tracking.choose(self.user, show, "Paused")
        self.tick(show, 1, 2)
        tv_tracking.choose(self.user, show, "Dropped")
        tv_tracking.choose(self.user, show, "In progress")
        self.untick(show, 1, 2)
        self.assertEqual(self.tv(show).choice, "In progress")
        self.assertEqual(self.status(show), "In progress")

    def test_tv_208_restoring_planning_with_nothing_left_clears_opinions(self):
        show = self.make_show({1: 3})
        tv_tracking.choose(self.user, show, "Planning")
        self.tick(show, 1, 1)
        tv_tracking.set_rating(self.user, show, 6)
        self.untick(show, 1, 1)
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.choice), ("Planning", "Planning"))
        self.assertIsNone(tv.score)

    def test_tv_208_watching_chosen_first_stays_watching_when_everything_goes(self):
        show = self.make_show({1: 3})
        tv_tracking.choose(self.user, show, "In progress")
        self.tick(show, 1, 1)
        self.untick(show, 1, 1)
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.choice), ("In progress", "In progress"))
        self.assertEqual(self.viewings(show), [])

    def test_tv_208_no_choice_and_nothing_left_leaves_the_library(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        tv_tracking.set_like(self.user, show, True)
        self.untick(show, 1, 1)
        self.assertIsNone(self.tv(show))
        self.assertFalse(MediaLike.objects.filter(item=show).exists())


class TV207ReconcileTests(TVTrackingTestCase):
    """TV-207 (properties): working out the state writes only changes and is idempotent."""

    def test_tv_207_reconcile_twice_changes_nothing_the_second_time(self):
        show = self.make_show({1: 3, 2: 2})
        self.ticks(show, 1, (1, 2))
        tv = self.tv(show)
        version = tv.state_version
        from social.models import ProgressChange

        changes = ProgressChange.objects.count()
        locked = TV.objects.select_related("item", "user").get(pk=tv.pk)
        # The stored listing, the seasons, viewings, logs and hearts; no writes.
        with self.assertNumQueries(5):
            tv_tracking.reconcile_show(locked, entry_point=tv_tracking.JOB)
        tv.refresh_from_db()
        self.assertEqual(tv.state_version, version)
        self.assertEqual(ProgressChange.objects.count(), changes)

    def test_tv_207_each_watched_count_change_records_one_progress_change(self):
        from social.models import Activity, ProgressChange

        show = self.make_show({1: 3, 2: 2})
        self.tick(show, 1, 1)
        self.tick(show, 1, 2)
        tv_tracking.mark_watched(self.user, show)
        changes = list(ProgressChange.objects.order_by("id").values_list("item__season_number", "current_progress"))
        self.assertEqual(
            [(season, progress["value"]) for season, progress in changes],
            [(1, 1), (1, 2), (1, 3), (2, 2)],
        )
        self.assertFalse(Activity.objects.exists())

    def test_tv_207_state_version_never_repeats(self):
        show = self.make_show({1: 3})
        versions = []
        self.tick(show, 1, 1)
        versions.append(self.tv(show).state_version)
        self.tick(show, 1, 2)
        versions.append(self.tv(show).state_version)
        tv_tracking.remove(self.user, show, mutation_id=uuid.uuid4())
        tv_tracking.tick(self.user, show, 1, 1)  # same instant as the removal
        versions.append(self.tv(show).state_version)
        self.assertEqual(len(set(versions)), 3)
        self.assertEqual(sorted(versions), versions)


class TV207WrittenStateTests(TVTrackingTestCase):
    """TV-207: a status written through any entry point means the matching action."""

    def test_tv_207_a_written_completed_marks_the_show_and_in_progress_chooses_watching(self):
        show = self.make_show({1: 2, 2: 2})
        tv_tracking.apply_written_state(self.user, show, {"status": "In progress"}, entry_point=tv_tracking.APP)
        self.assertEqual((self.status(show), self.tv(show).choice), ("In progress", "In progress"))
        tv_tracking.apply_written_state(self.user, show, {"status": "Completed"}, entry_point=tv_tracking.APP)
        self.assertEqual(self.status(show), "Completed")
        self.assertEqual(len(self.viewings(show)), 4)

    def test_tv_207_a_season_completed_marks_that_season(self):
        show = self.make_show({1: 2, 2: 2})
        tv_tracking.apply_written_state(
            self.user,
            self.season_item(show, 2),
            {"status": "Completed", "rating": 8},
            entry_point=tv_tracking.APP,
        )
        self.assertEqual(self.watched(show), {(2, 1), (2, 2)})
        self.assertEqual(str(Season.objects.get(item__season_number=2).score), "8.0")

    def test_tv_207_the_api_rejects_progress_and_date_fields_the_web_ignores_them(self):
        show = self.make_show({1: 2})
        for field in ("progress", "start_date", "end_date", "progressed_on", "total_minutes", "percentage"):
            with self.assertRaises(TVInvalid):
                tv_tracking.apply_written_state(self.user, show, {field: 3}, entry_point=tv_tracking.APP)
        tv_tracking.apply_written_state(
            self.user,
            show,
            {"progress": 3, "status": "Planning", "notes": "later"},
            entry_point=tv_tracking.WEB,
        )
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.choice, tv.notes), ("Planning", "Planning", "later"))
        self.assertEqual(self.viewings(show), [])

    def test_tv_207_an_unavailable_written_status_is_409_from_the_api_and_kept_elsewhere(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 1)
        with self.assertRaises(TVConflict):
            tv_tracking.apply_written_state(self.user, show, {"status": "Planning"}, entry_point=tv_tracking.APP)
        tv_tracking.apply_written_state(self.user, show, {"status": "Planning"}, entry_point=tv_tracking.WEB)
        self.assertEqual(self.status(show), "In progress")

    def test_tv_207_deleting_a_seasons_tracking(self):
        show = self.make_show({1: 2, 2: 2})
        self.tick(show, 1, 1)
        tv_tracking.mark_watched(self.user, show, season_number=2)
        tv_tracking.delete_season_tracking(self.user, show, 2)
        self.assertEqual(self.season_numbers(show), [1])
        self.assertEqual(self.watched(show), {(1, 1)})
        self.log(self.episode_item(show, 1, 2))
        with self.assertRaises(TVLogBlocks):
            tv_tracking.delete_season_tracking(self.user, show, 1)
        self.assertEqual(self.season_numbers(show), [1])

    def test_a_60_the_hourly_job_doesnt_change_a_pacific_evening_tick(self):
        """A-60 (domain): the tick's day is the Pacific day; the job changes nothing."""
        show = self.make_show({1: [date(2026, 10, 1), date(2026, 10, 8), OCT_25]})
        self.clock.set(datetime(2026, 10, 25, 21, 0), tz="America/Los_Angeles")
        tv_tracking.tick(self.user, show, 1, 3)
        tv = self.tv(show)
        self.assertEqual(self.viewings(show)[0].watched_on, OCT_25)
        before = (tv.status, tv.state_version, tv.up_next_season, tv.up_next_episode)
        self.clock.advance(hours=1)
        self.assertFalse(tv_tracking.refresh_due(tv))
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.state_version, tv.up_next_season, tv.up_next_episode), before)
        self.assertEqual(tv.last_watched_on, OCT_25)


class TV800ReconcileImportedTests(TVTrackingTestCase):
    """TV-800 (contribution): reconcile_imported turns imported statuses into facts."""

    def import_rows(self, show, status, seasons=(), plays=()):
        """Write rows the way the importers do: bulk, with no model hooks."""
        from simple_history.utils import bulk_create_with_history

        tv = bulk_create_with_history([TV(item=show, user=self.user, status=status)], TV)[0]
        tv = TV.objects.get(user=self.user, item=show)
        season_rows = {}
        for number, season_status in seasons:
            season_rows[number] = Season(
                item=self.season_item(show, number),
                user=self.user,
                related_tv=tv,
                status=season_status,
            )
        bulk_create_with_history(list(season_rows.values()), Season)
        season_rows = {row.item.season_number: row for row in Season.objects.filter(related_tv=tv).select_related("item")}
        bulk_create_with_history(
            [
                Episode(
                    item=self.episode_item(show, number, episode),
                    related_season=season_rows[number],
                    end_date=end_date,
                )
                for number, episode, end_date in plays
            ],
            Episode,
        )
        return tv

    def test_tv_800_an_imported_completed_show_becomes_a_mark_with_fills(self):
        show = self.make_show({1: 2, 2: 2})
        self.import_rows(show, "Completed")
        tv_tracking.reconcile_imported(self.user, [show])
        tv_tracking.reconcile_imported(self.user, [show])  # re-running changes nothing
        tv = self.tv(show)
        self.assertEqual(tv.status, "Completed")
        rows = self.viewings(show)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(row.kind == Episode.Kind.FILL and row.end_date is None for row in rows))

    def test_tv_800_imported_choices_plays_and_ignored_season_statuses(self):
        show = self.make_show({1: 3, 2: 3})
        play = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)  # Sep 30 in Los Angeles
        self.import_rows(
            show,
            "Paused",
            seasons=[(1, "Dropped"), (2, "Completed")],
            plays=[(1, 1, play), (1, 1, play + timedelta(days=1)), (1, 2, None)],
        )
        tv_tracking.reconcile_imported(self.user, [show])
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.choice), ("Paused", "Paused"))
        self.assertEqual(len(self.viewings(show, 1, 1)), 2)
        self.assertEqual(self.viewings(show, 1, 1)[0].watched_on, date(2026, 9, 30))
        self.assertEqual(self.watched(show), {(1, 1), (1, 2), (2, 1), (2, 2), (2, 3)})
        statuses = dict(Season.objects.values_list("item__season_number", "status"))
        self.assertEqual(statuses, {1: "Paused", 2: "Completed"})

    def test_tv_800_an_overwrite_rebuilds_each_logs_fills(self):
        show = self.make_show({1: 3})
        entry = self.log(self.season_item(show, 1)).entry
        TV.objects.filter(user=self.user, item=show).delete()
        self.import_rows(show, "In progress", seasons=[(1, "In progress")], plays=[(1, 1, None)])
        tv_tracking.reconcile_imported(self.user, [show], overwritten_show_item_ids=[show.pk])
        fills = [row for row in self.viewings(show) if row.diary_entry_id == entry.pk]
        self.assertEqual(sorted(row.item.episode_number for row in fills), [2, 3])
        self.assertEqual(self.status(show), "Completed")


class TV802WebhookTests(TVTrackingTestCase):
    """TV-802 (contribution): webhook events follow the app's rules and never raise."""

    def test_tv_802_a_play_counts_as_choosing_watching(self):
        finished = self.make_show({1: 1})
        self.tick(finished, 1, 1)
        paused = self.make_show({1: 3})
        self.tick(paused, 1, 1)
        tv_tracking.choose(self.user, paused, "Paused")
        for show in (finished, paused):
            tv_tracking.webhook_event(self.user, show, 1, 1, event="play", at=self.clock.moment)
        self.assertEqual(self.status(finished), "Completed")
        self.assertEqual(self.status(paused), "In progress")
        self.assertEqual(len(self.viewings(paused)), 1)
        fresh = self.make_show({1: 3})
        tv_tracking.webhook_event(self.user, fresh, 1, 2, event="play", at=self.clock.moment)
        self.assertEqual(self.status(fresh), "In progress")
        self.assertEqual(self.season_numbers(fresh), [])

    def test_tv_802_rejected_events_are_acknowledged(self):
        show = self.make_show({1: 2})
        self.log(self.episode_item(show, 1, 1))
        self.assertIsNone(
            tv_tracking.webhook_event(self.user, show, 1, 1, event="unplayed", at=self.clock.moment),
        )
        self.assertIsNone(
            tv_tracking.webhook_event(self.user, show, 1, 9, event="watched", at=self.clock.moment),
        )
        self.assertEqual(len(self.viewings(show)), 1)


class TV206RefreshDueTests(TVTrackingTestCase):
    """TV-206 (contribution): refresh_due fetches a named season the copy lacks."""

    def test_a_64_then_the_named_episode_airs_and_the_job_fetches_its_season(self):
        thursday = OCT_25 + timedelta(days=4)
        show = self.make_show(
            {1: [OCT_25 - timedelta(days=7 * (6 - number)) for number in range(1, 7)]},
            status="Returning Series",
            next_episode_to_air={"season_number": 1, "episode_number": 7, "air_date": thursday},
        )
        self.ticks(show, 1, range(1, 7))
        self.assertTrue(self.tv(show).caught_up)
        provider = self.fake.show(show.media_id)
        provider.add_episode(1, 7, thursday)
        provider.next_episode_to_air = None
        self.clock.advance(days=5)
        self.fake.reset_calls()
        self.assertTrue(tv_tracking.refresh_due(self.tv(show)))
        self.assertEqual(self.fake.count("tv_with_seasons"), 1)
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.caught_up), ("In progress", False))
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (1, 7))
