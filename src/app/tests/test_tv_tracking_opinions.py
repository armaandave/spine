"""Stars and hearts (docs/tv-tracking-contract.md, TV-303 to TV-305, TV-500 to TV-502).

Domain-level tests with A-28 to A-34, A-40 and A-68.
"""

from datetime import date, timedelta

from app import tv_tracking
from app.models import TV, Episode, MediaLike, Season
from app.tests.test_tv_tracking_viewings import OCT_25, TVTrackingTestCase
from app.tv_tracking import TVConflict, TVInvalid, TVLogFirst


def rating_activities():
    from social.models import Activity

    return Activity.objects.filter(verb="rating_updated")


class TV303UnstartedShowTests(TVTrackingTestCase):
    """TV-303: stars or a heart on a show you haven't started mark it watched."""

    def test_a_28_a_heart_on_an_unstarted_show_marks_it_completed(self):
        """A-28: from the new build and from an installed build (same server path)."""
        for entry_point in (tv_tracking.APP, tv_tracking.LEGACY_APP):
            show = self.make_show({1: 3, 2: 2})
            result = tv_tracking.set_like(self.user, show, True, entry_point=entry_point)
            self.assertTrue(result.changed)
            self.assertEqual(self.status(show), "Completed")
            self.assertTrue(MediaLike.objects.filter(user=self.user, item=show).exists())
            tv = self.tv(show)
            self.assertTrue(tv.like_is_independent)
            self.assertEqual(len(self.viewings(show)), 5)
            self.assertEqual(self.state(show)["eye"]["tap"], "unmark_marks")
        self.assertFalse(rating_activities().exists())

    def test_tv_303_stars_on_an_unstarted_show_mark_it_and_post_rating_activity(self):
        show = self.make_show({1: 2})
        tv_tracking.set_rating(self.user, show, 8)
        tv = self.tv(show)
        self.assertEqual(tv.status, "Completed")
        self.assertEqual(str(tv.score), "8.0")
        self.assertIsNone(tv.rating_source_id)
        activity = rating_activities().get()
        self.assertEqual((activity.target_id, activity.snapshot), (tv.pk, {"rating": "8.0"}))

    def test_tv_303_season_stars_on_an_unstarted_show_mark_that_season_and_ask(self):
        show = self.make_show({1: 2, 2: 2, 3: 2})
        result = tv_tracking.set_rating(self.user, self.season_item(show, 2), 7)
        self.assertEqual(self.watched(show), {(2, 1), (2, 2)})
        self.assertEqual(result.earlier_unwatched["question"], "Also mark Season 1 watched?")
        season = Season.objects.get(item__season_number=2)
        self.assertEqual(str(season.score), "7.0")

    def test_tv_303_a_heart_on_an_unaired_show_is_opinion_only(self):
        show = self.make_show({1: [OCT_25 + timedelta(days=30)]})
        tv_tracking.set_like(self.user, show, True)
        self.assertIsNone(self.tv(show))
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=show).exists())
        with self.assertRaises(TVConflict) as raised:
            tv_tracking.set_rating(self.user, show, 8)
        self.assertEqual(raised.exception.message, "Nothing has aired yet.")

    def test_tv_303_clearing_stars_or_the_heart_never_unmarks(self):
        show = self.make_show({1: 2})
        tv_tracking.set_rating(self.user, show, 8)
        tv_tracking.set_like(self.user, show, True)
        tv_tracking.set_rating(self.user, show, None)
        tv_tracking.set_like(self.user, show, False)
        self.assertEqual(self.status(show), "Completed")
        self.assertEqual(len(self.viewings(show)), 2)
        self.assertIsNone(self.tv(show).score)
        self.assertFalse(rating_activities().exists())

    def test_tv_504_off_scale_ratings_are_rejected(self):
        show = self.make_show({1: 2})
        for value in (0, 11, "7.5", "x"):
            with self.assertRaises(TVInvalid):
                tv_tracking.set_rating(self.user, show, value)
        self.assertIsNone(self.tv(show))


class TV304StartedShowTests(TVTrackingTestCase):
    """TV-304: stars and a heart mean you finished what the page shows."""

    def test_a_29_stars_on_a_show_at_s2_e3_are_refused_and_change_nothing(self):
        show = self.make_show({1: 2, 2: 5})
        self.ticks(show, 1, (1, 2))
        self.ticks(show, 2, (1, 2))
        before = self.state(show)
        self.assertEqual(before["opinion_action"], "log")
        with self.assertRaises(TVLogFirst) as raised:
            tv_tracking.set_rating(self.user, show, 9)
        self.assertEqual(raised.exception.message, "Log this first.")
        after = self.state(show)
        self.assertEqual((after["status"], after["up_next"], after["version"]), (before["status"], before["up_next"], before["version"]))
        self.assertEqual(len(self.viewings(show)), 4)
        self.assertIsNone(self.tv(show).score)
        self.assertFalse(rating_activities().exists())

    def test_a_29_saving_the_whole_show_log_with_the_value_marks_the_rest(self):
        show = self.make_show({1: 2, 2: 5})
        self.ticks(show, 1, (1, 2))
        self.log(show, rating=9)
        tv = self.tv(show)
        self.assertEqual((tv.status, str(tv.score)), ("Completed", "9.0"))
        self.assertEqual(len(self.viewings(show)), 7)

    def test_a_29_a_heart_on_a_partway_season_is_refused(self):
        show = self.make_show({1: 2, 2: 5})
        self.ticks(show, 1, (1, 2))
        self.ticks(show, 2, (1, 2))
        season = self.season_item(show, 2)
        self.assertEqual(tv_tracking.state_payload(self.user, season)["season"]["opinion_action"], "log")
        with self.assertRaises(TVLogFirst):
            tv_tracking.set_like(self.user, season, True)
        self.assertFalse(MediaLike.objects.filter(item=season).exists())
        self.log(season, liked=True)
        self.assertTrue(MediaLike.objects.filter(item=season).exists())
        self.assertEqual(len(self.viewings(show, 2)), 5)

    def test_a_30_stars_on_an_unwatched_season_4_while_watching_season_2(self):
        show = self.make_show({1: 2, 2: 3, 3: 2, 4: 2})
        self.ticks(show, 1, (1, 2))
        self.tick(show, 2, 1)
        season_4 = self.season_item(show, 4)
        self.assertEqual(tv_tracking.state_payload(self.user, season_4)["season"]["opinion_action"], "mark")
        result = tv_tracking.set_rating(self.user, season_4, 6)
        self.assertEqual(len(self.viewings(show, 4)), 2)
        self.assertEqual(result.earlier_unwatched["question"], "Also mark S2 E2–S3 E2 watched?")
        self.assertIsNone(result.finished)
        season = tv_tracking.state_payload(self.user, season_4)["season"]
        self.assertEqual((season["rating"], season["started"], season["eye"]["tap"]), ("6.0", True, "unmark_marks"))
        tv_tracking.unmark(self.user, show, season_number=4)
        self.assertEqual(self.viewings(show, 4), [])
        state = self.state(show)
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (2, 2))

    def test_tv_304_finished_or_caught_up_only_saves(self):
        show = self.make_show({1: 2})
        self.ticks(show, 1, (1, 2))
        self.assertEqual(self.state(show)["opinion_action"], "save")
        tv_tracking.set_rating(self.user, show, 5)
        tv_tracking.set_like(self.user, show, True)
        self.assertEqual(self.status(show), "Completed")
        self.assertEqual(len(self.viewings(show)), 2)

    def test_tv_304_caught_up_counts_as_finished_and_a_new_episode_makes_it_partway(self):
        fake_show = self.make_show({1: [date(2026, 10, 1), OCT_25 + timedelta(days=9)]}, status="Returning Series")
        self.tick(fake_show, 1, 1)
        self.assertTrue(self.state(fake_show)["caught_up"])
        self.assertEqual(self.state(fake_show)["opinion_action"], "save")
        tv_tracking.set_rating(self.user, fake_show, 7)
        self.assertEqual(str(self.tv(fake_show).score), "7.0")
        self.clock.advance(days=10)
        state = self.state(fake_show)
        self.assertEqual(state["opinion_action"], "log")
        self.assertEqual(str(self.tv(fake_show).score), "7.0")

    def test_tv_304_only_saving_never_ends_a_choice(self):
        show = self.make_show({1: 2})
        self.ticks(show, 1, (1, 2))
        tv_tracking.choose(self.user, show, "Paused")
        tv_tracking.set_rating(self.user, show, 5)
        tv_tracking.set_like(self.user, self.season_item(show, 1), True)
        self.assertEqual(self.status(show), "Paused")

    def test_tv_304_clearing_is_only_that_at_any_time(self):
        show = self.make_show({1: 3})
        tv_tracking.set_rating(self.user, show, 8)
        tv_tracking.set_like(self.user, show, True)
        tv_tracking.unmark(self.user, show, season_number=None)
        self.tick(show, 1, 1)
        tv_tracking.set_rating(self.user, show, 8, entry_point=tv_tracking.IMPORT)
        tv_tracking.set_like(self.user, show, True, entry_point=tv_tracking.IMPORT)
        self.assertEqual(self.state(show)["opinion_action"], "log")
        tv_tracking.set_rating(self.user, show, None)
        tv_tracking.set_like(self.user, show, False)
        tv = self.tv(show)
        self.assertEqual((tv.score, tv.status), (None, "In progress"))
        self.assertEqual(len(self.viewings(show)), 1)

    def test_tv_304_a_value_already_set_refuses_nothing(self):
        """Stars and hearts given partway before this law stay; re-sending them is no change."""
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        tv_tracking.set_rating(self.user, show, 8, entry_point=tv_tracking.IMPORT)
        tv_tracking.set_like(self.user, show, True, entry_point=tv_tracking.IMPORT)
        tv_tracking.set_rating(self.user, show, 8)
        tv_tracking.set_like(self.user, show, True)
        with self.assertRaises(TVLogFirst):
            tv_tracking.set_rating(self.user, show, 6)

    def test_tv_304_planning_paused_and_dropped_with_nothing_watched_arent_started(self):
        for status in ("Planning", "Paused", "Dropped"):
            show = self.make_show({1: 2})
            tv_tracking.choose(self.user, show, status)
            self.assertEqual(self.state(show)["opinion_action"], "mark")
            tv_tracking.set_rating(self.user, show, 8)
            self.assertEqual(self.status(show), "Completed")

    def test_tv_304_watching_chosen_with_nothing_watched_is_partway(self):
        show = self.make_show({1: 2})
        tv_tracking.choose(self.user, show, "In progress")
        self.assertEqual(self.state(show)["opinion_action"], "log")
        with self.assertRaises(TVLogFirst):
            tv_tracking.set_rating(self.user, show, 8)

    def test_tv_304_during_a_rewatch_partway_means_partway_through_it(self):
        show = self.make_show({1: 2})
        self.ticks(show, 1, (1, 2))
        tv_tracking.start_rewatch(self.user, show)
        state = self.state(show)
        self.assertEqual(state["opinion_action"], "log")
        self.assertTrue(next(target for target in state["composer"]["targets"] if target["id"] == "show")["is_rewatch"])
        with self.assertRaises(TVLogFirst):
            tv_tracking.set_like(self.user, show, True)

    def test_tv_304_an_installed_builds_set_is_refused_the_same_way(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        with self.assertRaises(TVLogFirst):
            tv_tracking.set_rating(self.user, show, 8, entry_point=tv_tracking.LEGACY_APP)
        with self.assertRaises(TVLogFirst):
            tv_tracking.apply_written_state(self.user, show, {"rating": 8}, entry_point=tv_tracking.LEGACY_APP)

    def test_tv_304_the_web_gets_the_refusal_too(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        with self.assertRaises(TVLogFirst):
            tv_tracking.apply_written_state(self.user, show, {"rating": 8}, entry_point=tv_tracking.WEB)

    def test_tv_800_an_import_never_opens_anything_it_only_sets_the_value(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        before = self.state(show)["watched_count"]
        tv_tracking.set_rating(self.user, show, 8, entry_point=tv_tracking.IMPORT)
        tv_tracking.set_like(self.user, show, True, entry_point=tv_tracking.IMPORT)
        self.assertEqual(str(self.tv(show).score), "8.0")
        self.assertEqual(self.state(show)["watched_count"], before)

    def test_tv_800_an_import_marks_something_with_no_history(self):
        show = self.make_show({1: 3})
        tv_tracking.set_rating(self.user, show, 8, entry_point=tv_tracking.IMPORT)
        self.assertEqual(self.status(show), "Completed")
        self.assertEqual(len(self.viewings(show)), 3)

    def test_tv_304_the_state_says_what_each_level_will_do(self):
        show = self.make_show({1: 2, 2: 2, 3: [OCT_25 + timedelta(days=30)]})
        self.ticks(show, 1, (1, 2))
        self.tick(show, 2, 1)
        state = self.state(show)
        self.assertEqual(state["opinion_action"], "log")
        actions = {
            number: tv_tracking.state_payload(self.user, self.season_item(show, number))["season"]["opinion_action"]
            for number in (1, 2, 3)
        }
        self.assertEqual(actions, {1: "save", 2: "log", 3: "save"})

    def test_tv_303_stars_when_only_specials_have_aired_follow_the_rate_action(self):
        """TV-005, TV-303: a started show takes stars as opinion once a special has aired.

        Season 1 premieres in 2027 and S0 E1 aired. Before the special is ticked the show
        isn't in the library, so its stars would have nothing to mark: "rate" is
        unavailable and the write says why. After the tick both allow the stars.
        """
        show = self.make_show({0: [date(2026, 10, 1)], 1: [date(2027, 1, 10)]})
        before = self.state(show)
        self.assertNotIn("rate", before["available_actions"])
        self.assertEqual(before["action_reasons"]["rate"], "Nothing has aired yet.")
        with self.assertRaises(TVConflict) as raised:
            tv_tracking.set_rating(self.user, show, 8)
        self.assertEqual(raised.exception.message, "Nothing has aired yet.")
        self.assertIsNone(self.tv(show))

        self.tick(show, 0, 1)
        started = self.state(show)
        self.assertIn("rate", started["available_actions"])
        self.assertNotIn("rate", started["action_reasons"])
        tv_tracking.set_rating(self.user, show, 8)

        after = self.state(show)
        self.assertEqual(str(self.tv(show).score), "8.0")
        self.assertEqual((after["status"], after["watched_count"]), (started["status"], started["watched_count"]))
        self.assertEqual(len(self.viewings(show)), 1)

    def test_a_40_a_special_then_a_heart_opens_the_whole_show_log(self):
        """A-40: Watching, S1 E1 up next, progress unchanged, Planning unavailable."""
        show = self.make_show({0: 2, 1: 3})
        self.tick(show, 0, 1)
        state = self.state(show)
        self.assertEqual(state["opinion_action"], "log")
        with self.assertRaises(TVLogFirst):
            tv_tracking.set_like(self.user, show, True)
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 1))
        self.assertEqual(state["watched_count"], 0)
        self.assertIn("planning", state["action_reasons"])
        self.assertEqual(len(self.viewings(show)), 1)
        self.assertFalse(MediaLike.objects.filter(item=show).exists())


class TV305EpisodeHeartTests(TVTrackingTestCase):
    """TV-305: a heart on an unwatched episode marks it watched."""

    def test_a_31_heart_an_unwatched_episode_then_unheart_it(self):
        show = self.make_show({1: 3})
        episode = self.episode_item(show, 1, 2)
        result = tv_tracking.set_like(self.user, episode, True)
        rows = self.viewings(show)
        self.assertEqual([(row.kind, row.item.episode_number, row.end_date) for row in rows], [(Episode.Kind.FILL, 2, None)])
        self.assertEqual(result.earlier_unwatched["question"], "Also mark S1 E1 watched?")
        tv_tracking.set_like(self.user, episode, False)
        self.assertEqual(len(self.viewings(show)), 1)
        self.assertFalse(MediaLike.objects.filter(item=episode).exists())

    def test_tv_305_a_heart_on_a_watched_or_unaired_episode_is_opinion_only(self):
        show = self.make_show({1: [date(2026, 10, 1), OCT_25 + timedelta(days=9)]})
        self.tick(show, 1, 1)
        tv_tracking.set_like(self.user, self.episode_item(show, 1, 1), True)
        tv_tracking.set_like(self.user, self.episode_item(show, 1, 2), True)
        self.assertEqual(len(self.viewings(show)), 1)
        self.assertEqual(MediaLike.objects.filter(user=self.user).count(), 2)


class TV500OwnStarsTests(TVTrackingTestCase):
    """TV-500: each level has its own stars and heart; nothing rolls up or down."""

    def test_a_33_a_rated_season_log_leaves_the_show_stars(self):
        show = self.make_show({1: 2}, status="Returning Series")
        tv_tracking.set_rating(self.user, show, 4)
        self.log(self.season_item(show, 1), rating=9, liked=True)
        tv = self.tv(show)
        self.assertEqual(str(tv.score), "4.0")
        self.assertFalse(MediaLike.objects.filter(item=show).exists())

    def test_tv_500_an_episode_log_never_sets_its_seasons_stars(self):
        show = self.make_show({1: 2})
        self.log(self.episode_item(show, 1, 1), rating=8)
        season = Season.objects.get(item__season_number=1)
        self.assertIsNone(season.score)

    def test_tv_500_a_show_log_never_sets_its_seasons_stars_or_hearts(self):
        show = self.make_show({1: 2, 2: 2})
        tv_tracking.set_rating(self.user, self.season_item(show, 2), 4)
        self.log(show, rating=9, liked=True)
        tv = self.tv(show)
        self.assertEqual(str(tv.score), "9.0")
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=show).exists())
        scores = dict(Season.objects.filter(related_tv=tv).values_list("item__season_number", "score"))
        self.assertIsNone(scores[1])
        self.assertEqual(str(scores[2]), "4.0")
        self.assertFalse(
            MediaLike.objects.filter(user=self.user, item__in=[self.season_item(show, 1), self.season_item(show, 2)]).exists(),
        )


class TV501SourceRulesTests(TVTrackingTestCase):
    """TV-501: show and season stars and hearts follow the shared source rules."""

    def test_a_32_rated_log_direct_change_then_edit(self):
        show = self.make_show({1: 2}, status="Returning Series")
        season_item = self.season_item(show, 1)
        entry = self.log(season_item, rating=8).entry
        season = Season.objects.get(item=season_item)
        self.assertEqual((str(season.score), season.rating_source_id), ("8.0", entry.pk))
        tv_tracking.set_rating(self.user, season_item, 6)
        season.refresh_from_db()
        self.assertEqual((str(season.score), season.rating_source_id), ("6.0", None))
        tv_tracking.update_log(entry, {"rating": 10})
        season.refresh_from_db()
        self.assertEqual(str(season.score), "6.0")

    def test_tv_501_an_unrated_new_log_keeps_the_stars_and_the_heart_follows_new_logs(self):
        show = self.make_show({1: 2})
        first = self.log(show, rating=7, liked=True).entry
        second = self.log(show, rating=None, liked=False).entry
        tv = self.tv(show)
        self.assertEqual((str(tv.score), tv.rating_source_id), ("7.0", first.pk))
        self.assertEqual(tv.like_source_id, second.pk)
        self.assertFalse(MediaLike.objects.filter(item=show).exists())

    def test_tv_501_editing_the_source_log_updates_and_clearing_clears(self):
        show = self.make_show({1: 2})
        entry = self.log(show, rating=7, liked=True).entry
        tv_tracking.update_log(entry, {"rating": 9, "liked": False})
        tv = self.tv(show)
        self.assertEqual(str(tv.score), "9.0")
        self.assertFalse(MediaLike.objects.filter(item=show).exists())
        tv_tracking.update_log(entry, {"rating": None})
        tv = self.tv(show)
        self.assertIsNone(tv.score)
        self.assertIsNone(tv.rating_source_id)

    def test_tv_501_new_stars_on_another_log_fill_empty_stars_sw_108(self):
        show = self.make_show({1: 2})
        old = self.log(show).entry
        self.log(show)
        tv_tracking.update_log(old, {"rating": 6})
        tv = self.tv(show)
        self.assertEqual((str(tv.score), tv.rating_source_id), ("6.0", old.pk))

    def test_tv_501_a_direct_heart_is_independent_of_logs(self):
        show = self.make_show({1: 2})
        entry = self.log(show, liked=False).entry
        tv_tracking.set_like(self.user, show, True)
        tv = self.tv(show)
        self.assertEqual((tv.like_source_id, tv.like_is_independent), (None, True))
        entry.refresh_from_db()
        self.assertFalse(entry.liked)


class TV502EpisodeStarsTests(TVTrackingTestCase):
    """TV-502: an episode's stars are those of its newest rated log."""

    def test_a_34_two_rated_logs_then_delete_the_newer(self):
        show = self.make_show({1: 2})
        episode = self.episode_item(show, 1, 1)
        self.log(episode, rating=6, consumed_at=date(2026, 10, 1))
        newer = self.log(episode, rating=9, consumed_at=date(2026, 10, 10), is_rewatch=True).entry
        self.log(episode, rating=None, consumed_at=date(2026, 10, 20), is_rewatch=True)
        self.assertEqual(self.state(episode)["episode"]["user_rating"], "9.0")
        tv_tracking.delete_log(self.user, newer)
        self.assertEqual(self.state(episode)["episode"]["user_rating"], "6.0")

    def test_a_68_editing_an_episode_logs_heart_off_keeps_the_episode_hearted(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 2)  # keeps the show in the library after the deletion (TV-208)
        episode = self.episode_item(show, 1, 1)
        entry = self.log(episode, liked=True).entry
        self.assertTrue(MediaLike.objects.filter(item=episode).exists())
        tv_tracking.update_log(entry, {"liked": False})
        self.assertTrue(MediaLike.objects.filter(item=episode).exists())
        tv_tracking.delete_log(self.user, entry)
        self.assertTrue(MediaLike.objects.filter(item=episode).exists())

    def test_tv_502_a_new_log_sets_the_episode_heart_to_its_own(self):
        show = self.make_show({1: 2})
        episode = self.episode_item(show, 1, 1)
        tv_tracking.set_like(self.user, episode, True)
        self.log(episode, liked=False, is_rewatch=True)
        self.assertFalse(MediaLike.objects.filter(item=episode).exists())
        self.assertTrue(TV.objects.filter(user=self.user, item=show).exists())
