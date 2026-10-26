"""Logs (docs/tv-tracking-contract.md, TV-400 to TV-407 and TV-703).

Domain-level tests with A-05, A-07, A-13, A-16 to A-21 and A-48.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from django.test import override_settings

from app import tv_tracking, user_calendar
from app.models import DiaryEntry, Episode, MediaLike, Season
from app.tests.test_tv_tracking_viewings import OCT_25, OCT_26, TVTrackingTestCase
from app.tv_tracking import TVInvalid, TVLogBlocks, TVStateChanged


class TV400LogsTrackTests(TVTrackingTestCase):
    """TV-400: every log puts the show in the library and counts its target watched."""

    def test_tv_400_a_season_log_of_a_never_tracked_show(self):
        show = self.make_show({1: 3, 2: 3})
        result = self.log(self.season_item(show, 1))
        entry = result.entry
        self.assertEqual(DiaryEntry.objects.count(), 1)
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))
        self.assertEqual(entry.progress_snapshot, {"tv": {"v": 1, "covers": {"1": [1, 2, 3]}}})
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (2, 1))
        self.assertEqual(self.season_numbers(show), [1])

    def test_tv_400_a_season_log_of_the_only_season_completes_the_show(self):
        show = self.make_show({1: 3})
        self.log(self.season_item(show, 1))
        self.assertEqual(self.status(show), "Completed")

    def test_tv_400_a_log_ends_a_choice(self):
        show = self.make_show({1: 3, 2: 3})
        tv_tracking.choose(self.user, show, "Planning")
        self.log(self.episode_item(show, 1, 1))
        self.assertEqual(self.status(show), "In progress")

    def test_a_13_logging_seasons_1_and_2_with_no_ticks(self):
        """A-13: in the library with S3 E1 up next; each season's stars come from its log."""
        show = self.make_show({1: 2, 2: 2, 3: 2})
        self.log(self.season_item(show, 1), rating=8)
        self.log(self.season_item(show, 2), rating=6)
        state = self.state(show)
        self.assertTrue(state["in_library"])
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (3, 1))
        scores = {
            row.item.season_number: str(row.score) for row in Season.objects.select_related("item")
        }
        self.assertEqual(scores, {1: "8.0", 2: "6.0"})
        self.assertIsNone(self.tv(show).score)

    def test_a_48_a_season_log_keeps_its_covered_ticks_watched(self):
        """A-48 (TV-400): the season log covers ticks it didn't fill."""
        show = self.make_show({1: 3})
        self.ticks(show, 1, (1, 2, 3))
        entry = self.log(self.season_item(show, 1)).entry
        self.assertEqual(entry.tv_viewings.count(), 0)
        self.assertEqual(entry.progress_snapshot["tv"]["covers"], {"1": [1, 2, 3]})


class TV401SeasonLogFillsTests(TVTrackingTestCase):
    """TV-401: a season or show log marks the rest of its aired episodes watched."""

    def test_a_07_logging_season_1_fills_e4_and_keeps_the_ticks(self):
        show = self.make_show({1: 10, 2: 3})
        for episode in (1, 2, 3, *range(5, 11)):
            self.tick(show, 1, episode)
        days = {row.item.episode_number: row.watched_on for row in self.viewings(show)}
        entry = self.log(self.season_item(show, 1)).entry
        fill = self.viewings(show, 1, 4)[0]
        self.assertEqual((fill.kind, fill.diary_entry_id, fill.end_date), (Episode.Kind.FILL, entry.pk, None))
        for episode, day in days.items():
            row = self.viewings(show, 1, episode)[0]
            self.assertEqual((row.kind, row.watched_on), (Episode.Kind.TICK, day))
        season = tv_tracking.state_payload(self.user, self.season_item(show, 1))["season"]
        self.assertEqual(season["state"], "watched")

    def test_tv_401_a_show_log_fills_every_regular_season_not_specials(self):
        show = self.make_show({0: 2, 1: 2, 2: 2})
        entry = self.log(show).entry
        self.assertEqual(len(self.viewings(show)), 4)
        self.assertEqual(self.viewings(show, 0), [])
        self.assertEqual(entry.progress_snapshot["tv"]["covers"], {"1": [1, 2], "2": [1, 2]})
        self.assertEqual(self.status(show), "Completed")

    def test_tv_401_a_specials_log_fills_the_aired_specials(self):
        show = self.make_show({0: 2, 1: 2})
        self.log(self.season_item(show, 0))
        self.assertEqual(len(self.viewings(show, 0)), 2)
        state = self.state(show)
        self.assertEqual(state["watched_count"], 0)

    def test_tv_401_unaired_episodes_arent_filled_and_the_show_is_caught_up(self):
        show = self.make_show({1: [OCT_25 - timedelta(days=7), OCT_25 + timedelta(days=7)]})
        entry = self.log(self.season_item(show, 1)).entry
        self.assertEqual(entry.progress_snapshot["tv"]["covers"], {"1": [1]})
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertTrue(state["caught_up"])

    def test_tv_401_a_log_of_a_finished_season_fills_nothing_and_still_covers(self):
        show = self.make_show({1: 2})
        tv_tracking.mark_watched(self.user, show)
        entry = self.log(self.season_item(show, 1)).entry
        self.assertEqual(entry.tv_viewings.count(), 0)
        self.assertEqual(entry.progress_snapshot["tv"]["covers"], {"1": [1, 2]})


class TV402EpisodeLogTests(TVTrackingTestCase):
    """TV-402: an episode log describes your tick, or adds a viewing."""

    def test_a_18_tick_then_log_counts_one_viewing_and_one_feed_item(self):
        from social.models import Activity

        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        composer = self.state(self.episode_item(show, 1, 1))["episode"]["composer"]
        target = composer["targets"][0]
        self.assertEqual(target["date"], OCT_25.isoformat())
        self.assertFalse(target["is_rewatch"])
        self.assertEqual(target["claims_viewing_id"], self.viewings(show)[0].pk)
        entry = self.log(self.episode_item(show, 1, 1)).entry
        rows = self.viewings(show)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].kind, rows[0].diary_entry_id), (Episode.Kind.TICK, entry.pk))
        self.assertFalse(entry.is_rewatch)
        self.assertEqual(Activity.objects.filter(verb="diary_created").count(), 1)
        self.assertEqual(Activity.objects.count(), 1)

    def test_tv_402_a_claimed_tick_takes_the_logs_day(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        self.log(self.episode_item(show, 1, 1), consumed_at=date(2026, 10, 20))
        row = self.viewings(show)[0]
        self.assertEqual(row.watched_on, date(2026, 10, 20))

    def test_a_19_rewatch_on_adds_a_second_viewing_and_reopens_nothing(self):
        show = self.make_show({1: 2})
        self.ticks(show, 1, (1, 2))
        result = self.log(self.episode_item(show, 1, 2), is_rewatch=True)
        rows = self.viewings(show, 1, 2)
        self.assertEqual([row.kind for row in rows], [Episode.Kind.TICK, Episode.Kind.LOG])
        self.assertIsNone(rows[0].diary_entry_id)
        self.assertEqual(self.status(show), "Completed")
        self.assertIsNone(result.finished)

    def test_tv_402_a_fill_is_never_claimed(self):
        show = self.make_show({1: 2})
        tv_tracking.mark_watched(self.user, show)
        self.log(self.episode_item(show, 1, 1))
        rows = self.viewings(show, 1, 1)
        self.assertEqual(sorted(row.kind for row in rows), [Episode.Kind.FILL, Episode.Kind.LOG])

    def test_tv_402_without_a_tick_the_log_adds_a_dated_viewing(self):
        show = self.make_show({1: 2})
        entry = self.log(self.episode_item(show, 1, 1), consumed_at=date(2026, 10, 3)).entry
        row = self.viewings(show)[0]
        self.assertEqual((row.kind, row.diary_entry_id, row.watched_on), (Episode.Kind.LOG, entry.pk, date(2026, 10, 3)))


class TV403FinishOfferTests(TVTrackingTestCase):
    """TV-403 (server): finishing a season offers its log once."""

    def test_a_05_tick_the_last_episode_not_now_then_untick_and_re_tick(self):
        show = self.make_show({1: 3, 2: 2})
        self.ticks(show, 1, (1, 2))
        result = self.tick(show, 1, 3)
        self.assertEqual(result.finished["season_number"], 1)
        self.assertFalse(result.finished["show_finished"])
        composer = result.finished["composer"]
        self.assertEqual(composer["default_target"], "season:1")
        self.assertEqual([target["id"] for target in composer["targets"]], ["season:1"])
        self.assertEqual(composer["targets"][0]["date"], OCT_25.isoformat())
        self.assertFalse(composer["targets"][0]["is_rewatch"])
        # Not now saves nothing; Log Season stays in the + menu.
        self.assertFalse(DiaryEntry.objects.exists())
        season = tv_tracking.state_payload(self.user, self.season_item(show, 1))["season"]
        self.assertIn("log_season", season["available_actions"])
        self.untick(show, 1, 3)
        self.assertIsNone(self.tick(show, 1, 3).finished)

    def test_tv_403_finishing_the_show_offers_the_whole_show_too(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 1)
        finished = self.tick(show, 1, 2).finished
        self.assertTrue(finished["show_finished"])
        self.assertEqual([target["id"] for target in finished["composer"]["targets"]], ["season:1", "show"])

    def test_tv_403_an_episode_log_that_finishes_a_season_offers_it_dated_by_the_log(self):
        show = self.make_show({1: 2, 2: 2})
        self.tick(show, 1, 1)
        result = self.log(self.episode_item(show, 1, 2), consumed_at=date(2026, 10, 22))
        self.assertEqual(result.finished["composer"]["targets"][0]["date"], "2026-10-22")

    def test_tv_403_marks_season_logs_and_webhooks_open_nothing(self):
        show = self.make_show({1: 2, 2: 2})
        self.assertIsNone(self.log(self.season_item(show, 1)).finished)
        self.assertIsNone(tv_tracking.mark_watched(self.user, show, season_number=2).finished)
        webhook = self.make_show({1: 1})
        self.advance()
        result = tv_tracking.webhook_event(self.user, webhook, 1, 1, event="watched", at=self.clock.moment)
        self.assertTrue(result.changed)
        self.assertIsNone(result.finished)

    def test_tv_403_installed_builds_get_no_finished(self):
        show = self.make_show({1: 1})
        self.assertIsNone(self.tick(show, 1, 1, entry_point=tv_tracking.LEGACY_APP).finished)

    def test_tv_403_a_season_logged_in_the_pass_opens_nothing(self):
        show = self.make_show({1: [OCT_25 - timedelta(days=14), OCT_25 - timedelta(days=7), OCT_25 + timedelta(days=2)]})
        self.tick(show, 1, 1)
        self.log(self.season_item(show, 1))
        self.clock.advance(days=3)
        self.assertIsNone(self.tick(show, 1, 3).finished)
        self.assertEqual(self.status(show), "Completed")


class TV405DateEditTests(TVTrackingTestCase):
    """TV-405: editing a log's date moves only that log's own viewing."""

    def test_a_20_only_the_logs_viewing_moves(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 1)
        entry = self.log(self.episode_item(show, 1, 1), is_rewatch=True, consumed_at=date(2026, 10, 10)).entry
        tick, own = self.viewings(show, 1, 1)
        tv_tracking.update_log(entry, {"consumed_at": "2026-10-12"})
        tick.refresh_from_db()
        own.refresh_from_db()
        self.assertEqual(own.watched_on, date(2026, 10, 12))
        self.assertEqual(tick.watched_on, OCT_25)
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 12, tzinfo=UTC))

    def test_tv_405_a_season_log_date_edit_moves_no_viewing(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 1)
        entry = self.log(self.season_item(show, 1)).entry
        tv_tracking.update_log(entry, {"consumed_at": date(2026, 10, 1)})
        fill = self.viewings(show, 1, 2)[0]
        self.assertEqual((fill.watched_on, fill.end_date), (None, None))
        self.assertEqual(self.viewings(show, 1, 1)[0].watched_on, OCT_25)
        self.assertEqual(self.tv(show).last_watched_on, OCT_25)


class TV406DeleteLogTests(TVTrackingTestCase):
    """TV-406: deleting a log removes only its own viewing and fills."""

    def test_a_16_tick_e1_to_e3_log_the_season_then_delete_the_log(self):
        show = self.make_show({1: 10})
        self.ticks(show, 1, (1, 2, 3))
        entry = self.log(self.season_item(show, 1)).entry
        info = tv_tracking.log_info(entry)
        self.assertEqual(info["unmarks_count"], 7)
        self.assertEqual(info["covers_count"], 10)
        self.assertEqual(info["level"], "season")
        with self.assertRaises(TVStateChanged) as raised:
            tv_tracking.delete_log(self.user, entry, expected_version=info["state_version"] - 5)
        self.assertEqual(raised.exception.extra["count"], 7)
        tv_tracking.delete_log(self.user, entry, expected_version=info["state_version"], mutation_id=uuid.uuid4())
        self.assertFalse(DiaryEntry.objects.exists())
        self.assertEqual(self.watched(show), {(1, 1), (1, 2), (1, 3)})
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 4))

    def test_a_17_deleting_the_only_log_leaves_the_library(self):
        show = self.make_show({1: 3})
        entry = self.log(show, rating=9, liked=True).entry
        self.assertEqual(str(self.tv(show).score), "9.0")
        tv_tracking.delete_log(self.user, entry)
        self.assertIsNone(self.tv(show))
        self.assertFalse(MediaLike.objects.filter(item=show).exists())
        self.assertFalse(Episode.objects.exists())

    def test_tv_406_a_claimed_tick_stays_as_a_tick_on_its_current_day(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        entry = self.log(self.episode_item(show, 1, 1), consumed_at=date(2026, 10, 20)).entry
        tv_tracking.delete_log(self.user, entry)
        row = self.viewings(show)[0]
        self.assertEqual((row.kind, row.diary_entry_id, row.watched_on), (Episode.Kind.TICK, None, date(2026, 10, 20)))
        self.assertEqual(self.status(show), "In progress")

    def test_tv_406_a_deleted_source_log_leaves_the_stars_independent(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 1)
        season_item = self.season_item(show, 1)
        first = self.log(season_item, rating=7, liked=True).entry
        self.log(season_item, rating=None, liked=True)
        tv_tracking.delete_log(self.user, first)
        season = Season.objects.get(item=season_item)
        self.assertEqual(str(season.score), "7.0")
        self.assertIsNone(season.rating_source_id)

    def test_tv_400_deleting_one_of_two_season_logs_keeps_the_season_watched(self):
        """The newer log still covers Season 1, so its fills move to it (TV-400, TV-406)."""
        show = self.make_show({1: 5, 2: 3})
        older = self.log(self.season_item(show, 1), consumed_at=date(2026, 10, 20)).entry
        newer = self.log(self.season_item(show, 1)).entry
        self.assertEqual(tv_tracking.log_info(older)["unmarks_count"], 0)

        tv_tracking.delete_log(self.user, older, expected_version=self.tv(show).state_version)

        self.assertEqual(self.watched(show), {(1, episode) for episode in range(1, 6)})
        self.assertEqual(
            {(row.kind, row.diary_entry_id, row.watched_on, row.end_date) for row in self.viewings(show)},
            {(Episode.Kind.FILL, newer.pk, None, None)},
        )
        state = self.state(show)
        self.assertEqual((state["watched_count"], state["status"]), (5, "In progress"))
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (2, 1))
        self.assertEqual(tv_tracking.log_info(newer)["unmarks_count"], 5)

    def test_tv_400_deleting_an_episode_log_a_season_log_covers_keeps_it_watched(self):
        """The episode log's own viewing becomes the season log's fill (TV-400)."""
        show = self.make_show({1: 5})
        episode_log = self.log(self.episode_item(show, 1, 3), consumed_at=date(2026, 10, 20)).entry
        season_log = self.log(self.season_item(show, 1)).entry
        self.assertEqual(tv_tracking.log_info(episode_log)["unmarks_count"], 0)

        tv_tracking.delete_log(self.user, episode_log)

        self.assertEqual(self.watched(show), {(1, episode) for episode in range(1, 6)})
        row = self.viewings(show, 1, 3)[0]
        self.assertEqual((row.kind, row.diary_entry_id, row.watched_on, row.end_date), (Episode.Kind.FILL, season_log.pk, None, None))
        self.assertEqual(self.status(show), "Completed")
        with self.assertRaises(TVLogBlocks) as raised:
            self.untick(show, 1, 3)
        self.assertEqual(raised.exception.message, "Delete your Season 1 log first.")

    def test_tv_400_deleting_a_season_log_under_a_show_log_keeps_the_season_and_its_stars(self):
        show = self.make_show({1: 3, 2: 3})
        season_log = self.log(self.season_item(show, 1), rating=8).entry
        show_log = self.log(show).entry
        self.assertEqual(tv_tracking.log_info(season_log)["unmarks_count"], 0)

        tv_tracking.delete_log(self.user, season_log)

        self.assertEqual(self.watched(show), {(season, episode) for season in (1, 2) for episode in (1, 2, 3)})
        self.assertEqual({row.diary_entry_id for row in self.viewings(show)}, {show_log.pk})
        self.assertEqual(self.status(show), "Completed")
        season = Season.objects.get(item=self.season_item(show, 1))
        self.assertEqual((str(season.score), season.rating_source_id), ("8.0", None))

    def test_tv_400_fills_a_log_took_over_from_a_mark_pass_to_the_next_covering_log(self):
        """Mark the show, log Season 2 twice, undo the show eye, then delete the older log."""
        show = self.make_show({1: 2, 2: 3})
        tv_tracking.mark_watched(self.user, show)
        older = self.log(self.season_item(show, 2), consumed_at=date(2026, 10, 20)).entry
        newer = self.log(self.season_item(show, 2)).entry
        self.advance()
        tv_tracking.unmark(self.user, show)  # TV-302 case 2: Season 2's fills become the older log's
        self.assertEqual({row.diary_entry_id for row in self.viewings(show)}, {older.pk})

        tv_tracking.delete_log(self.user, older)

        self.assertEqual(self.watched(show), {(2, 1), (2, 2), (2, 3)})
        self.assertEqual({row.diary_entry_id for row in self.viewings(show)}, {newer.pk})

    def test_tv_406_only_episodes_no_remaining_log_covers_are_unmarked(self):
        """A show log, then a Season 2 log: deleting the show log unmarks Season 1 only."""
        show = self.make_show({1: 2, 2: 3})
        show_log = self.log(show, consumed_at=date(2026, 10, 20)).entry
        self.log(self.season_item(show, 2))
        self.assertEqual(tv_tracking.log_info(show_log)["unmarks_count"], 2)

        tv_tracking.delete_log(self.user, show_log)

        self.assertEqual(self.watched(show), {(2, 1), (2, 2), (2, 3)})
        self.assertEqual(self.season_numbers(show), [2])
        state = self.state(show)
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 1))

    def test_tv_406_and_tv_208_a_deletion_restores_the_ended_choice(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        tv_tracking.choose(self.user, show, "Paused")
        entry = self.log(self.season_item(show, 1)).entry
        self.assertEqual(self.status(show), "Completed")
        tv_tracking.delete_log(self.user, entry)
        self.assertEqual(self.status(show), "Paused")


@override_settings(TIME_ZONE="America/New_York")
class TV407LogDateTests(TVTrackingTestCase):
    """TV-407: log dates are calendar dates on the user's calendar, never in the future."""

    start = datetime(2026, 10, 25, 23, 0)  # 11 p.m. Pacific is 2 a.m. Oct 26 in New York

    def test_tv_407_the_pacific_today_is_accepted_and_the_new_york_date_rejected(self):
        show = self.make_show({1: 3})
        self.assertEqual(user_calendar.today(self.user), OCT_25)
        self.log(self.episode_item(show, 1, 1), consumed_at="2026-10-25")
        with self.assertRaises(TVInvalid) as raised:
            self.log(self.episode_item(show, 1, 2), consumed_at="2026-10-26")
        self.assertEqual(raised.exception.message, "Log dates can't be in the future.")
        self.assertEqual(DiaryEntry.objects.count(), 1)

    def test_a_21_a_future_date_is_rejected_on_create_and_edit(self):
        show = self.make_show({1: 3})
        with self.assertRaises(TVInvalid):
            self.log(self.season_item(show, 1), consumed_at=OCT_26 + timedelta(days=3))
        self.assertIsNone(self.tv(show))
        entry = self.log(self.season_item(show, 1)).entry
        with self.assertRaises(TVInvalid):
            tv_tracking.update_log(entry, {"consumed_at": OCT_26})
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))

    def test_tv_407_an_installed_builds_datetime_converts_to_the_calendar_day(self):
        show = self.make_show({1: 3})
        sent = datetime(2026, 10, 26, 5, 30, tzinfo=UTC)  # 10:30 p.m. Oct 25 in Los Angeles
        entry = self.log(self.season_item(show, 1), consumed_at=sent.isoformat()).entry
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))

    def test_tv_407_a_log_can_be_dated_before_the_air_date(self):
        show = self.make_show({1: [date(2026, 10, 1)]})
        entry = self.log(self.episode_item(show, 1, 1), consumed_at="2026-09-01").entry
        self.assertEqual(entry.consumed_at.date(), date(2026, 9, 1))


class TV703FeedTests(TVTrackingTestCase):
    """TV-703: ticks don't post; each log posts one diary activity with its stars."""

    def test_tv_703_ticks_marks_and_fills_post_nothing_and_logs_post_once(self):
        from social.models import Activity

        show = self.make_show({1: 3, 2: 3})
        self.tick(show, 1, 1)
        tv_tracking.mark_watched(self.user, show, season_number=1)
        self.untick(show, 1, 2)
        self.assertFalse(Activity.objects.exists())
        entry = self.log(self.season_item(show, 2), rating=8, liked=True, visibility="followers").entry
        activity = Activity.objects.get()
        self.assertEqual(
            (activity.verb, activity.target_id, activity.visibility, activity.snapshot),
            ("diary_created", entry.pk, "followers", {"rating": "8.0", "liked": True}),
        )
        tv_tracking.update_log(entry, {"rating": 6})
        activity.refresh_from_db()
        self.assertEqual(activity.snapshot["rating"], "6.0")
        tv_tracking.delete_log(self.user, entry)
        self.assertFalse(Activity.objects.exists())
