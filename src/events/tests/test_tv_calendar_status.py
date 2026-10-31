"""Calendar events and notifications follow the show's status (TV-704).

Every show in Planning, Watching or Completed gets calendar events and release
notifications for its episodes; a Paused or Dropped show gets none. Seasons have no
Pause or Drop of their own, so the show's status alone decides, and shows that have only
logs have tracking now, so they get events too.
"""

from datetime import datetime, timedelta
from unittest import mock

from django.test import TestCase, override_settings

from app import tv_tracking
from app.models import TV, Item, MediaTypes, Season, Status
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from events.models import Event
from events.notifications import get_user_releases

NOON_PACIFIC = datetime(2026, 10, 25, 12, 0)


@override_settings(TIME_ZONE="America/New_York")
class TVCalendarStatusTests(TestCase):
    """Events and releases per show status."""

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

    def show_with_events(self, title):
        """Return a three-season show whose seasons each have an upcoming event."""
        show = make_show(self.fake, seasons={1: 2, 2: 2, 3: 2}, title=title)
        events = []
        for number in (1, 2, 3):
            season_item, _ = Item.objects.get_or_create(
                media_id=show.media_id,
                source=show.source,
                media_type=MediaTypes.SEASON.value,
                season_number=number,
                defaults={"title": show.title, "image": show.image},
            )
            events.append(
                Event.objects.create(
                    item=season_item,
                    content_number=3,
                    datetime=self.clock.moment + timedelta(days=number),
                ),
            )
        return show, events

    def calendar(self):
        today = self.clock.moment.date()
        return set(Event.objects.get_user_events(self.user, today, today + timedelta(days=30)))

    def releases(self, events):
        targets = {(event.item_id, event.content_number): event for event in events}
        return set(get_user_releases([self.user], targets).get(self.user.id, []))

    def act(self, function, *args, **kwargs):
        self.clock.advance(minutes=1)
        return function(self.user, *args, **kwargs)

    def test_tv_704_paused_and_dropped_shows_get_no_events_or_notifications(self):
        paused, paused_events = self.show_with_events("Paused")
        dropped, dropped_events = self.show_with_events("Dropped")
        for show, status in ((paused, Status.PAUSED.value), (dropped, Status.DROPPED.value)):
            self.act(tv_tracking.tick, show, 1, 1)
            self.act(tv_tracking.choose, show, status)
            self.assertEqual(TV.objects.get(user=self.user, item=show).status, status)

        events = paused_events + dropped_events
        self.assertFalse(self.calendar() & set(events))
        self.assertFalse(self.releases(events))

    def test_tv_704_planning_watching_and_completed_shows_get_every_seasons_events(self):
        planning, planning_events = self.show_with_events("Planning")
        watching, watching_events = self.show_with_events("Watching")
        completed, completed_events = self.show_with_events("Completed")
        self.act(tv_tracking.choose, planning, Status.PLANNING.value)
        self.act(tv_tracking.tick, watching, 1, 1)
        self.act(tv_tracking.mark_watched, completed)
        self.assertEqual(TV.objects.get(user=self.user, item=completed).status, Status.COMPLETED.value)

        events = set(planning_events + watching_events + completed_events)
        self.assertEqual(self.calendar() & events, events)
        self.assertEqual(self.releases(events), events)

    def test_tv_704_a_show_with_only_logs_gets_events(self):
        show, events = self.show_with_events("Logged")
        season_item = Item.objects.get(
            media_id=show.media_id, media_type=MediaTypes.SEASON.value, season_number=2,
        )
        self.act(tv_tracking.create_log, season_item, consumed_at=self.clock.moment.date())

        self.assertEqual(self.calendar() & set(events), set(events))
        self.assertEqual(self.releases(events), set(events))

    def test_turning_tv_off_stops_its_events_and_notifications(self):
        show, events = self.show_with_events("Watching")
        self.act(tv_tracking.tick, show, 1, 1)
        # The old Season setting stays on; it no longer keeps TV events alive.
        self.user.tv_enabled = False
        self.user.season_enabled = True
        self.user.save(update_fields=["tv_enabled", "season_enabled"])

        self.assertFalse(self.calendar() & set(events))
        self.assertFalse(self.releases(events))

    def test_the_old_season_setting_cannot_hide_tv_events(self):
        show, events = self.show_with_events("Watching")
        self.act(tv_tracking.tick, show, 1, 1)
        self.user.season_enabled = False
        self.user.save(update_fields=["season_enabled"])

        self.assertEqual(self.calendar() & set(events), set(events))
        self.assertEqual(self.releases(events), set(events))

    def test_tv_704_a_stored_season_dropped_value_cuts_nothing_off(self):
        show, events = self.show_with_events("Watching")
        self.act(tv_tracking.tick, show, 2, 1)
        tv = TV.objects.get(user=self.user, item=show)
        # A season-level Dropped value from before the contract (TV-801).
        Season.objects.filter(related_tv=tv, item__season_number=2).update(status=Status.DROPPED.value)

        self.assertEqual(self.calendar() & set(events), set(events))
        self.assertEqual(self.releases(events), set(events))
