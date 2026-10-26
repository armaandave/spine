"""Stars and hearts (docs/tv-tracking-contract.md, TV-303 to TV-305, TV-500 to TV-502).

Domain-level tests with A-28 to A-34, A-40 and A-68.
"""

from datetime import date, timedelta

from app import tv_tracking
from app.models import TV, Episode, MediaLike, Season
from app.tests.test_tv_tracking_viewings import OCT_25, TVTrackingTestCase
from app.tv_tracking import TVConflict, TVInvalid


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
    """TV-304: on a started show, stars and a heart are opinion only."""

    def test_a_29_stars_on_a_show_at_s2_e3_are_opinion_only(self):
        show = self.make_show({1: 2, 2: 5})
        self.ticks(show, 1, (1, 2))
        self.ticks(show, 2, (1, 2))
        before = self.state(show)
        tv_tracking.set_rating(self.user, show, 9)
        after = self.state(show)
        self.assertEqual((after["status"], after["up_next"]), (before["status"], before["up_next"]))
        self.assertEqual(len(self.viewings(show)), 4)
        self.assertEqual(rating_activities().get().snapshot, {"rating": "9.0"})

    def test_a_30_stars_on_an_unwatched_season_4_while_watching_season_2(self):
        show = self.make_show({1: 2, 2: 3, 3: 2, 4: 2})
        self.ticks(show, 1, (1, 2))
        self.tick(show, 2, 1)
        tv_tracking.set_rating(self.user, self.season_item(show, 4), 6)
        self.assertEqual(self.viewings(show, 4), [])
        state = self.state(show)
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (2, 2))
        season = tv_tracking.state_payload(self.user, self.season_item(show, 4))["season"]
        self.assertEqual((season["rating"], season["started"], season["status"]), ("6.0", False, None))

    def test_tv_304_opinions_never_end_a_choice(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        tv_tracking.choose(self.user, show, "Paused")
        tv_tracking.set_rating(self.user, show, 5)
        tv_tracking.set_like(self.user, self.season_item(show, 1), True)
        self.assertEqual(self.status(show), "Paused")

    def test_a_40_a_special_then_a_heart_is_opinion_only(self):
        """A-40: Watching, S1 E1 up next, progress unchanged, Planning unavailable."""
        show = self.make_show({0: 2, 1: 3})
        self.tick(show, 0, 1)
        tv_tracking.set_like(self.user, show, True)
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 1))
        self.assertEqual(state["watched_count"], 0)
        self.assertIn("planning", state["action_reasons"])
        self.assertEqual(len(self.viewings(show)), 1)


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
        show = self.make_show({1: 2})
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


class TV501SourceRulesTests(TVTrackingTestCase):
    """TV-501: show and season stars and hearts follow the shared source rules."""

    def test_a_32_rated_log_direct_change_then_edit(self):
        show = self.make_show({1: 2})
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
