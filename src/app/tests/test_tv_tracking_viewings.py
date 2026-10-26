"""Viewings: ticks and unticks (docs/tv-tracking-contract.md, TV-100 to TV-106, TV-004).

Domain-level tests of app.tv_tracking. The server runs on America/New_York and the
account calendar is America/Los_Angeles, as production does; the clock is patched
through django.utils.timezone.now (app.tests.tv_fixtures.patch_now).

TVTrackingTestCase is the shared base the other test_tv_tracking_* modules import.
"""

from datetime import date, datetime, timedelta
from unittest import mock

from django.test import TestCase, override_settings

from app import tv_tracking, user_calendar
from app.models import TV, DiaryEntry, Episode, Item, MediaLike, MediaTypes, Season
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from app.tv_tracking import TVInvalid, TVLogBlocks

# 9 p.m. on Oct 25 in Los Angeles is midnight on Oct 26 in New York.
PACIFIC_9PM = datetime(2026, 10, 25, 21, 0)
OCT_25 = date(2026, 10, 25)
OCT_26 = date(2026, 10, 26)


@override_settings(TIME_ZONE="America/New_York")
class TVTrackingTestCase(TestCase):
    """A fake TMDB, a fixed clock at 9 p.m. Pacific and a Los Angeles calendar."""

    start = PACIFIC_9PM
    start_tz = "America/Los_Angeles"

    def setUp(self):
        super().setUp()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(self.start, tz=self.start_tz).__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = make_user(calendar="America/Los_Angeles")

    # Builders

    def make_show(self, seasons=None, **kwargs):
        return make_show(self.fake, seasons=seasons if seasons is not None else {1: 10}, **kwargs)

    def season_item(self, show, number):
        item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=MediaTypes.SEASON.value,
            season_number=number,
            defaults={"title": show.title, "image": show.image},
        )
        return item

    def episode_item(self, show, season_number, episode_number):
        item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=season_number,
            episode_number=episode_number,
            defaults={"title": f"Episode {episode_number}", "image": show.image},
        )
        return item

    def advance(self, **delta):
        return self.clock.advance(**(delta or {"minutes": 1}))

    # Actions (each advances the clock first, so "recorded" order is real)

    def tick(self, show, season, episode, **kwargs):
        self.advance()
        return tv_tracking.tick(self.user, show, season, episode, **kwargs)

    def ticks(self, show, season, episodes):
        for episode in episodes:
            self.tick(show, season, episode)

    def untick(self, show, season, episode, **kwargs):
        self.advance()
        return tv_tracking.untick(self.user, show, season, episode, **kwargs)

    def log(self, item, **kwargs):
        self.advance()
        kwargs.setdefault("consumed_at", user_calendar.today(self.user))
        return tv_tracking.create_log(self.user, item, **kwargs)

    # Reading

    def tv(self, show):
        return TV.objects.filter(user=self.user, item=show).first()

    def status(self, show):
        tv = self.tv(show)
        return tv.status if tv is not None else None

    def state(self, item):
        return tv_tracking.state_payload(self.user, item)

    def viewings(self, show, season=None, episode=None):
        rows = Episode.objects.filter(
            related_season__related_tv__user=self.user,
            related_season__related_tv__item=show,
        )
        if season is not None:
            rows = rows.filter(item__season_number=season)
        if episode is not None:
            rows = rows.filter(item__episode_number=episode)
        return list(rows.order_by("created_at", "id"))

    def watched(self, show):
        """The episodes the show page reports watched in the current pass."""
        return {
            (season, episode)
            for season, states in self.season_episode_states(show).items()
            for episode, state in states.items()
            if state["watched"]
        }

    def season_episode_states(self, show):
        listing = self.fake.show(show.media_id)
        return {
            number: tv_tracking.episode_states(self.user, show, number, None)
            for number in listing.seasons
        }

    def season_numbers(self, show):
        tv = self.tv(show)
        if tv is None:
            return []
        return sorted(Season.objects.filter(related_tv=tv).values_list("item__season_number", flat=True))


class TV100TickTests(TVTrackingTestCase):
    """TV-100: a tick records one viewing, dated now on the user's calendar."""

    def test_a_01_tick_of_an_untracked_show(self):
        """A-01: Watching, S1 E2 up next, "1 of N", no log, only Season 1, no feed item."""
        from social.models import Activity

        show = self.make_show({1: 10, 2: 8})
        result = self.tick(show, 1, 1)

        self.assertTrue(result.changed)
        self.assertEqual(self.status(show), "In progress")
        state = self.state(show)
        self.assertEqual(state["up_next"]["season_number"], 1)
        self.assertEqual(state["up_next"]["episode_number"], 2)
        self.assertEqual((state["watched_count"], state["aired_count"]), (1, 18))
        self.assertFalse(DiaryEntry.objects.exists())
        self.assertEqual(self.season_numbers(show), [1])
        self.assertFalse(Activity.objects.exists())

    def test_tv_100_a_tick_stores_the_moment_and_its_pacific_day(self):
        """A-60: a 9 p.m. Pacific tick (midnight in New York) is the Pacific day."""
        show = self.make_show()
        self.clock.set(PACIFIC_9PM, tz="America/Los_Angeles")
        tv_tracking.tick(self.user, show, 1, 1)
        viewing = self.viewings(show)[0]
        self.assertEqual(viewing.kind, Episode.Kind.TICK)
        self.assertEqual(viewing.watched_on, OCT_25)
        self.assertEqual(viewing.end_date, self.clock.moment)
        self.assertIsNone(viewing.diary_entry_id)

    def test_tv_100_the_app_tick_ignores_a_sent_date(self):
        show = self.make_show()
        tv_tracking.tick(self.user, show, 1, 1, at=datetime(2020, 1, 1, 12, 0))
        self.assertEqual(self.viewings(show)[0].watched_on, OCT_25)

    def test_tv_100_a_webhook_tick_takes_the_event_time(self):
        """TV-802: a webhook watch is a tick dated at the event time, on the user's day."""
        show = self.make_show()
        event = datetime(2026, 10, 20, 23, 30, tzinfo=user_calendar.calendar_tz(self.user))
        tv_tracking.tick(self.user, show, 1, 1, at=event, entry_point=tv_tracking.WEBHOOK)
        viewing = self.viewings(show)[0]
        self.assertEqual(viewing.end_date, event)
        self.assertEqual(viewing.watched_on, date(2026, 10, 20))

    def test_tv_100_a_web_form_tick_takes_the_typed_day(self):
        show = self.make_show()
        tv_tracking.tick(self.user, show, 1, 1, at=date(2026, 10, 1), entry_point=tv_tracking.WEB)
        viewing = self.viewings(show)[0]
        self.assertEqual(viewing.watched_on, date(2026, 10, 1))
        with self.assertRaises(TVInvalid):
            tv_tracking.tick(self.user, show, 1, 2, at=OCT_26, entry_point=tv_tracking.WEB)

    def test_tv_100_a_tick_ends_paused_and_leaves_the_worked_out_status(self):
        show = self.make_show({1: 2})
        tv_tracking.choose(self.user, show, "Paused")
        self.tick(show, 1, 1)
        self.assertEqual(self.status(show), "In progress")
        self.tick(show, 1, 2)
        self.assertEqual(self.status(show), "Completed")


class TV101DoubleTickTests(TVTrackingTestCase):
    """TV-101: ticking an episode watched in the current pass records nothing."""

    def test_a_02_double_tap_or_retry_records_one_viewing(self):
        show = self.make_show()
        first = self.tick(show, 1, 1)
        version = self.tv(show).state_version
        second = self.tick(show, 1, 1)
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(len(self.viewings(show)), 1)
        self.assertEqual(self.tv(show).state_version, version)

    def test_tv_101_a_webhook_watch_of_a_watched_episode_records_nothing(self):
        show = self.make_show()
        self.tick(show, 1, 1)
        self.advance()
        tv_tracking.webhook_event(
            self.user,
            show,
            1,
            1,
            event="watched",
            at=self.clock.moment,
        )
        self.assertEqual(len(self.viewings(show)), 1)

    def test_tv_101_a_marked_episode_tick_records_nothing(self):
        show = self.make_show({1: 3})
        tv_tracking.mark_watched(self.user, show)
        self.tick(show, 1, 2)
        self.assertEqual(len(self.viewings(show, 1, 2)), 1)


class TV102UntickTests(TVTrackingTestCase):
    """TV-102: unticking removes the most recently recorded unlogged viewing."""

    def test_a_03_tick_then_untick_the_only_episode_leaves_the_library(self):
        show = self.make_show({1: 1})
        self.tick(show, 1, 1)
        result = self.untick(show, 1, 1)
        self.assertTrue(result.changed)
        self.assertIsNone(result.tv)
        self.assertIsNone(self.tv(show))
        self.assertFalse(Season.objects.exists())

    def test_a_04_untick_of_a_season_log_fill_names_the_season_log(self):
        show = self.make_show({1: 3})
        self.log(self.season_item(show, 1))
        with self.assertRaises(TVLogBlocks) as raised:
            self.untick(show, 1, 2)
        self.assertEqual(raised.exception.message, "Delete your Season 1 log first.")
        self.assertEqual(len(self.viewings(show)), 3)

    def test_a_48_a_season_log_covering_ticks_blocks_the_untick(self):
        """A-48: tick E1 to E10, save the season log, untick E10: nothing changes."""
        show = self.make_show({1: 10})
        self.ticks(show, 1, range(1, 11))
        self.log(self.season_item(show, 1))
        with self.assertRaises(TVLogBlocks) as raised:
            self.untick(show, 1, 10)
        self.assertEqual(raised.exception.message, "Delete your Season 1 log first.")
        self.assertEqual(len(self.viewings(show, 1, 10)), 1)

    def test_tv_102_a_show_log_blocks_with_its_own_message(self):
        show = self.make_show({1: 2})
        self.log(show)
        with self.assertRaises(TVLogBlocks) as raised:
            self.untick(show, 1, 1)
        self.assertEqual(raised.exception.message, "Delete your show log first.")

    def test_tv_102_an_episode_log_alone_blocks_with_the_episode_message(self):
        show = self.make_show({1: 2})
        self.log(self.episode_item(show, 1, 1))
        with self.assertRaises(TVLogBlocks) as raised:
            self.untick(show, 1, 1)
        self.assertEqual(raised.exception.message, "Delete your log of this episode first.")

    def test_tv_102_untick_removes_latest_unlogged_viewing_not_the_logs(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 1)
        self.log(self.episode_item(show, 1, 1), is_rewatch=True)
        self.assertEqual(len(self.viewings(show, 1, 1)), 2)
        self.untick(show, 1, 1)
        remaining = self.viewings(show, 1, 1)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].kind, Episode.Kind.LOG)

    def test_tv_102_untick_removes_a_marks_fill(self):
        show = self.make_show({1: 3})
        tv_tracking.mark_watched(self.user, show)
        self.untick(show, 1, 2)
        self.assertEqual(self.watched(show), {(1, 1), (1, 3)})
        self.assertEqual(self.status(show), "In progress")

    def test_tv_102_a_named_viewing_that_is_gone_changes_nothing(self):
        show = self.make_show({1: 2})
        self.tick(show, 1, 1)
        stale = self.viewings(show)[0].pk + 1000
        result = self.untick(show, 1, 1, viewing_id=stale)
        self.assertFalse(result.changed)
        self.assertEqual(len(self.viewings(show)), 1)
        state = self.state(self.episode_item(show, 1, 1))["episode"]
        result = self.untick(show, 1, 1, viewing_id=state["untick_viewing_id"])
        self.assertTrue(result.changed)

    def test_tv_102_an_untick_reopens_a_finished_season_and_show(self):
        show = self.make_show({1: 2})
        self.ticks(show, 1, (1, 2))
        self.assertEqual(self.status(show), "Completed")
        self.untick(show, 1, 2)
        self.assertEqual(self.status(show), "In progress")
        self.assertEqual(self.state(show)["up_next"]["episode_number"], 2)

    def test_tv_102_the_last_viewing_clears_the_episode_heart(self):
        """SW-008 at the episode level: no viewing and no log left clears the heart."""
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        self.tick(show, 1, 2)
        episode = self.episode_item(show, 1, 2)
        tv_tracking.set_like(self.user, episode, True)
        self.untick(show, 1, 2)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=episode).exists())

    def test_a_65_untick_removes_the_later_recorded_viewing(self):
        """A-65 (SQLite): an undated viewing and a dated one recorded later."""
        show = self.make_show({1: 2})
        self.tick(show, 1, 2)
        undated = self.viewings(show, 1, 2)[0]
        Episode.objects.filter(pk=undated.pk).update(end_date=None, watched_on=None)
        # An imported play of the same episode, dated, recorded later (TV-800).
        self.advance()
        Episode.objects.create(
            item=undated.item,
            related_season=undated.related_season,
            end_date=self.clock.moment - timedelta(days=30),
            watched_on=date(2026, 9, 25),
        )
        self.untick(show, 1, 2)
        remaining = self.viewings(show, 1, 2)
        self.assertEqual([row.pk for row in remaining], [undated.pk])


class TV004SpecialsTests(TVTrackingTestCase):
    """TV-004: specials never count."""

    def test_tv_004_a_specials_tick_starts_the_show_and_counts_nothing(self):
        show = self.make_show({0: 3, 1: 5})
        self.tick(show, 0, 1)
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertTrue(state["started"])
        self.assertEqual((state["watched_count"], state["aired_count"]), (0, 5))
        self.assertEqual(
            (state["up_next"]["season_number"], state["up_next"]["episode_number"]),
            (1, 1),
        )
        self.assertFalse(state["finished"])

    def test_a_58_the_specials_eye_marks_the_aired_specials_only(self):
        show = self.make_show({0: 3, 1: 5})
        self.tick(show, 1, 1)
        before = self.state(show)
        result = tv_tracking.mark_watched(self.user, show, season_number=0)
        after = self.state(show)
        self.assertEqual(len(self.viewings(show, 0)), 3)
        self.assertIsNone(result.finished)
        self.assertEqual(before["up_next"], after["up_next"])
        self.assertEqual(before["watched_count"], after["watched_count"])
        self.assertEqual(after["status"], "In progress")
        season = tv_tracking.state_payload(self.user, self.season_item(show, 0))["season"]
        self.assertEqual(season["state"], "watched")

    def test_tv_004_specials_finishing_opens_no_composer(self):
        show = self.make_show({0: 1, 1: 2})
        result = self.tick(show, 0, 1)
        self.assertIsNone(result.finished)
        self.assertIsNone(result.earlier_unwatched)

    def test_tv_004_the_show_eye_leaves_specials_alone(self):
        show = self.make_show({0: 2, 1: 2})
        tv_tracking.mark_watched(self.user, show)
        self.assertEqual(self.viewings(show, 0), [])
        self.assertEqual(self.status(show), "Completed")


class TV106RejectionTests(TVTrackingTestCase):
    """TV-106: Spine rejects episodes that don't exist or haven't aired."""

    def test_a_22_an_unlisted_episode_is_rejected_with_no_change(self):
        show = self.make_show({1: 3})
        with self.assertRaises(TVInvalid) as raised:
            self.tick(show, 1, 99)
        self.assertEqual(raised.exception.message, "This episode isn't listed for Season 1.")
        self.assertIsNone(self.tv(show))

    def test_a_22_an_episode_airing_in_three_days_is_rejected(self):
        show = self.make_show({1: [date(2026, 10, 1), OCT_25 + timedelta(days=3)]})
        with self.assertRaises(TVInvalid) as raised:
            self.tick(show, 1, 2)
        self.assertEqual(raised.exception.message, "This episode hasn't aired yet.")
        with self.assertRaises(TVInvalid):
            self.log(self.episode_item(show, 1, 2))
        self.assertIsNone(self.tv(show))

    def test_tv_106_an_episode_airing_tomorrow_is_accepted(self):
        """The one day of slack covers time zones."""
        show = self.make_show({1: [date(2026, 10, 1), OCT_26]})
        self.tick(show, 1, 2)
        self.assertEqual(len(self.viewings(show)), 1)
        state = self.state(self.episode_item(show, 1, 2))["episode"]
        self.assertTrue(state["aired"])  # a viewing makes it aired (TV-103)

    def test_tv_106_a_season_log_of_an_unaired_season_is_rejected(self):
        show = self.make_show({1: 2, 2: [OCT_25 + timedelta(days=30)]})
        with self.assertRaises(TVInvalid):
            self.log(self.season_item(show, 2))
        self.assertIsNone(self.tv(show))


class TV103CountingTests(TVTrackingTestCase):
    """TV-103 (counting): only aired, listed regular episodes count."""

    def test_a_63_a_manual_show_with_no_air_dates(self):
        """A-63: every listed episode counts as aired; eyes and season logs work."""
        from app.tests.tv_fixtures import make_manual_show

        show = make_manual_show(seasons={1: 3, 2: 2})
        tv_tracking.mark_watched(self.user, show, season_number=1)
        state = self.state(show)
        self.assertEqual((state["watched_count"], state["aired_count"]), (3, 5))
        self.log(self.season_item(show, 2))
        self.assertEqual(self.status(show), "Completed")
        self.assertEqual(self.fake.count(), 0)

    def test_a_11_only_the_finale_never_finishes_a_season(self):
        """A-11: progress reads "1 of 10" and the season isn't finished."""
        show = self.make_show({1: 10})
        self.tick(show, 1, 10)
        season = tv_tracking.state_payload(self.user, self.season_item(show, 1))["season"]
        self.assertEqual((season["watched_count"], season["aired_count"]), (1, 10))
        self.assertEqual(season["state"], "in_progress")
        self.assertEqual(self.status(show), "In progress")

    def test_tv_104_progress_counts_distinct_aired_regular_episodes(self):
        """TV-104: E1 to E3 and E5 of ten reads 4 of 10, not 5."""
        show = self.make_show({0: 2, 1: 10})
        for episode in (1, 2, 3, 5):
            self.tick(show, 1, episode)
        self.tick(show, 0, 1)
        self.log(self.episode_item(show, 1, 5), is_rewatch=True)
        tv = self.tv(show)
        self.assertEqual((tv.watched_count, tv.aired_count), (4, 10))
        self.assertEqual(
            tv_tracking.progress_payload(tv),
            {"kind": "episodes", "value": 4, "max": 10, "unit": "episode"},
        )
        season = Season.objects.get(related_tv=tv, item__season_number=1)
        self.assertEqual((season.watched_count, season.aired_count), (4, 10))

    def test_tv_103_an_unaired_episode_counts_toward_nothing(self):
        show = self.make_show({1: [date(2026, 10, 1), date(2026, 10, 8), OCT_25 + timedelta(days=5)]})
        self.ticks(show, 1, (1, 2))
        state = self.state(show)
        self.assertEqual((state["watched_count"], state["aired_count"]), (2, 2))
        self.assertFalse(state["finished"])
        self.assertTrue(state["caught_up"])


class TVViewingsPassThroughUserTests(TestCase):
    """The clock and calendar helpers the tests rely on."""

    def test_patch_now_drives_the_users_today(self):
        user = make_user(calendar="America/Los_Angeles")
        with patch_now(PACIFIC_9PM, tz="America/Los_Angeles"):
            self.assertEqual(user_calendar.today(user), OCT_25)
