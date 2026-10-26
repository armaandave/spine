"""The hourly job and the calendar reopen shows when new episodes air (TV-206).

A newly aired episode reopens a finished show within about an hour of its stored air
date; other provider changes arrive through the daily calendar. Planning, Paused and
Dropped shows never move (TV-203), specials never reopen anything (TV-004), and shows
stored before the contract that were never worked out are left alone (TV-801).

The server runs on America/New_York and the account calendar is America/Los_Angeles;
the clock is patched through django.utils.timezone.now.
"""

from datetime import date, datetime, timedelta
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

from app import tv_listing, tv_tracking
from app.models import TV, Episode, Item, MediaTypes, Season, Status
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from config.celery import app as celery_app
from events import tasks, tv_airing
from events.calendar.tv import process_tv
from events.models import Event

NOON_PACIFIC = datetime(2026, 10, 25, 12, 0)
OCT_25 = date(2026, 10, 25)


@override_settings(TIME_ZONE="America/New_York")
class TVAiringTestCase(TestCase):
    """A fake TMDB, a fixed clock and a Los Angeles calendar."""

    def setUp(self):
        super().setUp()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(NOON_PACIFIC, tz="America/Los_Angeles").__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = make_user(calendar="America/Los_Angeles")

    def at(self, moment):
        self.clock.set(moment, tz="America/Los_Angeles")

    def tick(self, show, season, episodes):
        result = None
        for episode in episodes:
            self.clock.advance(minutes=1)
            result = tv_tracking.tick(self.user, show, season, episode)
        return result

    def tv(self, show):
        return TV.objects.get(user=self.user, item=show)

    def run_job(self):
        self.clock.advance(minutes=1)
        return tv_airing.refresh_aired_shows()


class TVAiringJobTests(TVAiringTestCase):
    """refresh_aired_shows, the "Refresh aired TV" task."""

    def test_a_09_a_caught_up_completed_show_reopens_when_the_next_season_airs(self):
        show = make_show(
            self.fake,
            seasons={1: 2, 2: 2, 3: [date(2027, 3, 12)]},
            status="Returning Series",
            next_episode_to_air={"season_number": 3, "episode_number": 1, "air_date": date(2027, 3, 12)},
        )
        self.tick(show, 1, (1, 2))
        self.tick(show, 2, (1, 2))
        tv = self.tv(show)
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertTrue(tv.caught_up)
        self.assertEqual((tv.next_air_season, tv.next_air_episode, tv.next_air_date), (3, 1, date(2027, 3, 12)))

        self.run_job()
        self.assertEqual(self.tv(show).status, Status.COMPLETED.value)

        self.at(datetime(2027, 3, 12, 9, 0))
        summary = self.run_job()

        tv = self.tv(show)
        self.assertEqual(summary["changed"], 1)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (3, 1))
        self.assertTrue(tv.up_next_is_new)  # "S3 E1 · New" (TV-701)
        self.assertFalse(tv.caught_up)
        self.assertFalse(Season.objects.filter(related_tv=tv, item__season_number=3).exists())  # TV-205

    def test_a_60_the_job_uses_the_users_day_not_the_servers(self):
        show = make_show(self.fake, seasons={1: [date(2026, 10, 18), date(2026, 10, 26)]})
        # 9 p.m. Pacific on Oct 25 is already Oct 26 in New York.
        self.at(datetime(2026, 10, 25, 21, 0))
        self.tick(show, 1, (1,))
        tv = self.tv(show)
        self.assertEqual(Episode.objects.get(related_season__related_tv=tv).watched_on, OCT_25)
        self.assertTrue(tv.caught_up)
        before = (tv.status, tv.state_version, tv.up_next_episode)

        self.run_job()

        tv.refresh_from_db()
        self.assertEqual((tv.status, tv.state_version, tv.up_next_episode), before)

        self.at(datetime(2026, 10, 26, 21, 0))
        self.run_job()
        tv.refresh_from_db()
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (1, 2))
        self.assertFalse(tv.caught_up)

    def test_a_64_a_named_next_episode_the_list_lacks_is_fetched_when_it_airs(self):
        show = make_show(
            self.fake,
            seasons={1: [date(2026, 9, 10) + timedelta(days=7 * n) for n in range(6)]},
            status="Returning Series",
            next_episode_to_air={"season_number": 1, "episode_number": 7, "air_date": date(2026, 10, 29)},
        )
        result = self.tick(show, 1, range(1, 7))

        self.assertIsNone(result.finished)  # TV-403: the season isn't finished
        tv = self.tv(show)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertTrue(tv.caught_up)
        self.assertEqual((tv.next_air_episode, tv.next_air_date), (7, date(2026, 10, 29)))
        listing = tv_listing.stored_listing(show)
        self.assertEqual(tv_tracking.season_card_states(self.user, show, listing)[1]["state"], "airing")

        provider_show = self.fake.show(show.media_id)
        provider_show.add_episode(1, 7, date(2026, 10, 29))
        provider_show.next_episode_to_air = {"season_number": 1, "episode_number": 8, "air_date": date(2026, 11, 5)}
        self.fake.reset_calls()
        self.at(datetime(2026, 10, 29, 12, 0))
        self.run_job()

        self.assertEqual(self.fake.season_requests(show.media_id), [(1,)])
        tv.refresh_from_db()
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (1, 7))
        self.assertFalse(tv.caught_up)

    def test_a_38_tv_203_dropped_paused_and_planning_shows_never_move(self):
        shows = {}
        for status in (Status.DROPPED.value, Status.PAUSED.value, Status.PLANNING.value):
            show = make_show(self.fake, seasons={1: [date(2026, 10, 18), date(2026, 10, 27)]}, title=status)
            if status != Status.PLANNING.value:
                self.tick(show, 1, (1,))
            self.clock.advance(minutes=1)
            tv_tracking.choose(self.user, show, status)
            shows[status] = show

        self.at(datetime(2026, 10, 28, 12, 0))
        self.run_job()

        for status, show in shows.items():
            self.assertEqual(self.tv(show).status, status)

    def test_a_42_the_job_leaves_a_legacy_show_as_stored_until_the_users_tick(self):
        show = make_show(self.fake, seasons={1: 3})
        tv = TV.objects.create(user=self.user, item=show, status=Status.COMPLETED.value)
        TV.objects.filter(pk=tv.pk).update(legacy_pending=True)

        summary = self.run_job()

        tv.refresh_from_db()
        self.assertEqual(summary, {"due": 0, "changed": 0, "unreconciled": 0})
        self.assertTrue(tv.legacy_pending)
        self.assertIsNone(tv.reconciled_at)
        self.assertEqual(tv.status, Status.COMPLETED.value)

        self.tick(show, 1, (1,))

        tv.refresh_from_db()
        self.assertFalse(tv.legacy_pending)
        self.assertEqual(tv.status, Status.COMPLETED.value)
        viewings = Episode.objects.filter(related_season__related_tv=tv)
        self.assertEqual(viewings.count(), 3)
        self.assertFalse(viewings.filter(kind=Episode.Kind.TICK).exists())  # TV-101

    def test_tv_004_a_special_airing_reopens_nothing(self):
        show = make_show(self.fake, seasons={0: [date(2026, 1, 5), date(2026, 10, 27)], 1: 2})
        self.tick(show, 1, (1, 2))
        self.tick(show, 0, (1,))
        tv = self.tv(show)
        self.assertEqual(tv.status, Status.COMPLETED.value)
        specials = Season.objects.get(related_tv=tv, item__season_number=0)
        self.assertEqual(specials.next_air_date, date(2026, 10, 27))

        self.at(datetime(2026, 10, 28, 12, 0))
        self.run_job()

        tv.refresh_from_db()
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertIsNone(tv.up_next_season)

    def test_tv_207_the_job_works_out_a_show_a_model_write_left_unworked(self):
        show = make_show(self.fake, seasons={1: 2})
        self.tick(show, 1, (1,))
        tv = self.tv(show)
        TV.objects.filter(pk=tv.pk).update(reconciled_at=None, up_next_episode=None)

        summary = self.run_job()

        tv.refresh_from_db()
        self.assertEqual(summary["unreconciled"], 1)
        self.assertIsNotNone(tv.reconciled_at)
        self.assertEqual(tv.up_next_episode, 2)

    def test_tv_206_a_provider_failure_leaves_the_show_for_the_next_run(self):
        show = make_show(
            self.fake,
            seasons={1: [date(2026, 10, 18)]},
            next_episode_to_air={"season_number": 1, "episode_number": 2, "air_date": date(2026, 10, 27)},
            status="Returning Series",
        )
        self.tick(show, 1, (1,))
        self.fake.show(show.media_id).add_episode(1, 2, date(2026, 10, 27))
        self.fake.fail(show.media_id)
        self.at(datetime(2026, 10, 27, 12, 0))

        self.run_job()
        tv = self.tv(show)
        self.assertTrue(tv.caught_up)

        self.fake.recover()
        self.run_job()
        tv.refresh_from_db()
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (1, 2))


class TVCalendarReopenTests(TVAiringTestCase):
    """The daily calendar refresh works every selected show out (TV-206)."""

    @mock.patch("events.calendar.tv.get_tvmaze_episode_map", return_value={})
    def test_a_12_an_aired_episode_added_to_a_finished_season_reopens_the_show(self, _tvmaze):
        show = make_show(self.fake, seasons={1: 2}, status="Ended")
        self.tick(show, 1, (1, 2))
        self.assertEqual(self.tv(show).status, Status.COMPLETED.value)
        season_item = Item.objects.get(
            media_id=show.media_id, media_type=MediaTypes.SEASON.value, season_number=1,
        )
        for number in (1, 2):
            Event.objects.create(item=season_item, content_number=number, datetime=self.clock.moment)
        self.fake.show(show.media_id).add_episode(1, 3, date(2026, 10, 1))

        events_bulk = []
        process_tv(show, events_bulk)

        self.assertEqual(events_bulk, [])  # the season already has events
        tv = self.tv(show)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (1, 3))
        season = Season.objects.get(related_tv=tv, item__season_number=1)
        self.assertEqual(season.status, Status.IN_PROGRESS.value)

    @mock.patch("events.calendar.tv.get_tvmaze_episode_map", return_value={})
    def test_tv_801_the_calendar_leaves_a_legacy_show_alone(self, _tvmaze):
        show = make_show(self.fake, seasons={1: 2}, status="Ended")
        tv = TV.objects.create(user=self.user, item=show, status=Status.COMPLETED.value)
        TV.objects.filter(pk=tv.pk).update(legacy_pending=True)

        process_tv(show, [])

        tv.refresh_from_db()
        self.assertTrue(tv.legacy_pending)
        self.assertIsNone(tv.reconciled_at)

    @mock.patch("events.calendar.tv.get_tvmaze_episode_map", return_value={})
    def test_tv_206_a_provider_failure_still_lets_the_calendar_run(self, _tvmaze):
        show = make_show(self.fake, seasons={1: 2}, status="Ended")
        self.tick(show, 1, (1,))
        self.fake.fail(show.media_id)

        events_bulk = []
        process_tv(show, events_bulk)

        self.assertEqual(self.tv(show).status, Status.IN_PROGRESS.value)


class TVAiringScheduleTests(TestCase):
    """The beat entry and the task (AGENTS.md section 4.5)."""

    def test_the_hourly_job_is_scheduled_and_registered(self):
        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULE["refresh_aired_tv"],
            {"task": "Refresh aired TV", "schedule": 3600},
        )
        self.assertEqual(tasks.refresh_aired_tv.name, "Refresh aired TV")
        self.assertEqual(celery_app.tasks["Refresh aired TV"].name, tasks.refresh_aired_tv.name)

    def test_the_task_runs_the_job(self):
        with mock.patch("events.tv_airing.refresh_aired_shows", return_value={"due": 0}) as job:
            result = tasks.refresh_aired_tv.delay()
        self.assertEqual(result.get(), {"due": 0})
        job.assert_called_once_with()
