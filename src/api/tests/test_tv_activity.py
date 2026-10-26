"""TV in activity: a day's ticks are the show's progress (activity contract, TV-703).

The server and the user's calendar are on America/New_York, as production is; the clock
is patched through django.utils.timezone.now (app.tests.tv_fixtures.patch_now).
"""

from datetime import date, datetime
from unittest import mock
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from app import tv_tracking
from app.models import Item, MediaTypes
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from social.models import ProgressChange

NEW_YORK = "America/New_York"
NEW_YORK_TZ = ZoneInfo(NEW_YORK)
OCT_25 = date(2026, 10, 25)


@override_settings(TIME_ZONE=NEW_YORK)
class TVActivityTests(TestCase):
    """Ticks are the show's progress; marks and fills aren't; a log replaces its day."""

    def setUp(self):
        super().setUp()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        # 8 p.m. on Oct 25 in New York.
        self.clock = patch_now(datetime(2026, 10, 25, 20, 0, tzinfo=NEW_YORK_TZ)).__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = make_user(calendar=NEW_YORK)
        self.friend = make_user(calendar=NEW_YORK)
        self.show = make_show(self.fake, title="Severance", seasons={1: 3, 2: 3})
        self.client = APIClient()

    # Writes, each a minute after the last

    def at(self, day, hour, minute=0):
        self.clock.set(datetime(2026, 10, day, hour, minute, tzinfo=NEW_YORK_TZ))

    def tick(self, season, episode, show=None):
        self.clock.advance(minutes=1)
        return tv_tracking.tick(self.user, show or self.show, season, episode)

    def untick(self, season, episode, show=None):
        self.clock.advance(minutes=1)
        return tv_tracking.untick(self.user, show or self.show, season, episode)

    def choose(self, choice, show=None):
        self.clock.advance(minutes=1)
        return tv_tracking.choose(self.user, show or self.show, choice)

    def season_item(self, number, show=None):
        show = show or self.show
        item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=MediaTypes.SEASON.value,
            season_number=number,
            defaults={"title": show.title, "image": show.image},
        )
        return item

    # Reading

    def activity(self, viewer=None):
        """Return (day, type, media type, title, summary) for each activity item."""
        self.client.force_authenticate(viewer or self.user)
        response = self.client.get(f"/api/v1/users/{self.user.username}/activity/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [
            (
                item["consumed_at"],
                item["type"],
                item["media"]["ref"]["media_type"],
                item["media"]["title"],
                item["object"].get("summary", "log"),
            )
            for item in response.data["results"]
        ]

    def progress(self, day, summary, title="Severance"):
        return (day, "progress_updated", "tv", title, summary)

    # Tests

    def test_a_days_ticks_across_seasons_are_one_item_for_the_show(self):
        self.tick(1, 1)
        self.tick(1, 2)
        self.tick(2, 1)

        expected = [self.progress("2026-10-25", "watched 3 episodes")]
        self.assertEqual(self.activity(), expected)
        self.assertEqual(self.activity(viewer=self.friend), expected)

    def test_unticks_net_the_days_progress(self):
        self.tick(1, 1)
        self.tick(1, 2)
        self.tick(2, 1)
        self.untick(1, 2)
        # Season 2's only tick: the untick deletes the season, and still nets.
        self.untick(2, 1)

        self.assertEqual(self.activity(), [self.progress("2026-10-25", "watched 1 episode")])

        # A day that didn't move forward shows nothing.
        self.at(26, 20)
        self.tick(1, 2)
        self.untick(1, 2)

        self.assertEqual(self.activity(), [self.progress("2026-10-25", "watched 1 episode")])

    def test_marks_and_fills_are_not_progress(self):
        self.clock.advance(minutes=1)
        tv_tracking.mark_watched(self.user, self.show)

        self.assertEqual(self.activity(), [])

        andor = make_show(self.fake, title="Andor", seasons={1: 3, 2: 3})
        self.tick(1, 1, show=andor)
        self.clock.advance(minutes=1)
        tv_tracking.mark_watched(self.user, andor, season_number=1)
        self.clock.advance(minutes=1)
        tv_tracking.mark_up_to(self.user, andor, 2, 1)
        # Unticking a marked episode undoes the mark, not progress.
        self.untick(1, 3, show=andor)
        self.clock.advance(minutes=1)
        # A season log fills its unwatched episodes; the friend can't see this log.
        tv_tracking.create_log(self.user, self.season_item(2, andor), consumed_at=OCT_25, visibility="followers")

        self.assertEqual(self.activity(viewer=self.friend), [self.progress("2026-10-25", "watched 1 episode", "Andor")])
        # The owner sees the log, which replaces that day's progress.
        self.assertEqual(self.activity(), [("2026-10-25", "diary_created", "season", "Andor", "log")])
        # Each change of season 1's count is recorded for installed builds' Home; only
        # the tick moved it.
        season_one = ProgressChange.objects.filter(item=self.season_item(1, andor)).order_by("id")
        self.assertEqual(
            [(change.previous_progress["value"], change.current_progress["value"]) for change in season_one],
            [(0, 1), (3, 3), (2, 2)],
        )

    def test_a_season_log_that_day_replaces_the_days_progress(self):
        self.tick(1, 1)
        self.tick(1, 2)
        self.clock.advance(minutes=1)
        tv_tracking.create_log(self.user, self.season_item(2), consumed_at=OCT_25)

        expected = [("2026-10-25", "diary_created", "season", "Severance", "log")]
        self.assertEqual(self.activity(), expected)
        self.assertEqual(self.activity(viewer=self.friend), expected)

        self.at(26, 9)
        self.tick(1, 3)

        self.assertEqual(self.activity(), [self.progress("2026-10-26", "watched 1 episode"), *expected])

    def test_starting_a_show_is_a_start_until_a_day_with_progress(self):
        self.choose("In progress")

        self.assertEqual(self.activity(), [self.progress("2026-10-25", "started watching")])

        self.at(26, 20)
        self.tick(1, 1)
        # Resuming a paused show isn't starting it.
        self.at(27, 20)
        self.choose("Paused")
        self.choose("In progress")

        self.assertEqual(
            self.activity(),
            [
                self.progress("2026-10-26", "watched 1 episode"),
                self.progress("2026-10-25", "started watching"),
            ],
        )
        self.assertEqual(ProgressChange.objects.filter(current_progress__kind=ProgressChange.START).count(), 1)

    def test_starting_again_after_dropping_or_finishing_is_a_start(self):
        andor = make_show(self.fake, title="Andor", seasons={1: 3})
        self.tick(1, 1, show=andor)
        self.at(26, 20)
        self.choose("Dropped", show=andor)
        self.at(27, 20)
        self.choose("In progress", show=andor)
        self.clock.advance(minutes=1)
        tv_tracking.mark_watched(self.user, self.show)
        self.at(28, 20)
        self.clock.advance(minutes=1)
        tv_tracking.start_rewatch(self.user, self.show)

        self.assertEqual(
            self.activity(),
            [
                self.progress("2026-10-28", "started watching"),
                self.progress("2026-10-27", "started watching", "Andor"),
                self.progress("2026-10-25", "watched 1 episode", "Andor"),
            ],
        )

    def test_days_are_new_york_days(self):
        # 11:30 p.m. on Oct 25 and 12:30 a.m. on Oct 26 in New York: both Oct 26 in UTC.
        self.at(25, 23, 30)
        self.tick(1, 1)
        self.at(26, 0, 30)
        self.tick(1, 2)

        self.assertEqual(
            self.activity(),
            [
                self.progress("2026-10-26", "watched 1 episode"),
                self.progress("2026-10-25", "watched 1 episode"),
            ],
        )

    def test_a_show_that_leaves_the_library_leaves_activity(self):
        self.tick(1, 1)
        self.tick(1, 2)
        self.clock.advance(minutes=1)
        tv_tracking.remove(self.user, self.show, mutation_id=uuid4())

        self.assertEqual(self.activity(), [])

        # Unticking the only episode takes the show out of the library too (TV-208).
        self.at(26, 20)
        self.tick(1, 1)
        self.untick(1, 1)

        self.assertEqual(self.activity(), [])
        self.assertFalse(ProgressChange.objects.filter(actor=self.user).exists())
