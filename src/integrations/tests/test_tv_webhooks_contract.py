"""Plex, Jellyfin and Emby events follow the app's TV rules (TV-802).

A watch ticks the episode at the event time, an unplayed event unticks it, and any other
play counts as choosing Watching. Each event applies in one transaction and creates
nothing else; a rejected event is logged and acknowledged, never answered with an error.

The server runs on America/New_York and the account calendar is America/Los_Angeles;
webhook requests carry no timezone header, so the stored calendar decides the day.
"""

import json
from datetime import date, datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from app import tv_tracking
from app.models import TV, DiaryEntry, Episode, Item, MediaTypes, Season, Sources, Status
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from integrations.tests.webhook_provider_mocks import WebhookProviderMocksMixin
from integrations.webhooks.emby import EmbyWebhookProcessor
from integrations.webhooks.jellyfin import JellyfinWebhookProcessor
from integrations.webhooks.plex import PlexWebhookProcessor
from social.models import Activity

NOON_PACIFIC = datetime(2026, 10, 25, 12, 0)
OCT_25 = date(2026, 10, 25)

PLEX_PLAY = {"event": "media.play"}
PLEX_WATCHED = {"event": "media.scrobble"}
JELLYFIN_UNPLAYED = {"Event": "MarkUnplayed", "Item": {"UserData": {"Played": False}}}
JELLYFIN_STOP = {"Event": "Stop", "Item": {"UserData": {"Played": False}}}
EMBY_WATCHED = {"Event": "playback.stop", "PlaybackInfo": {"PlayedToCompletion": True}}


@override_settings(TIME_ZONE="America/New_York")
class TVWebhookContractTests(TestCase):
    """Events applied through each processor's TV handler."""

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
        self.show = make_show(
            self.fake,
            seasons={1: 3, 2: [date(2021, 1, 5), date(2021, 1, 12), date(2026, 10, 28)]},
            title="Hook Show",
        )

    def event(self, payload, season, episode, processor=None):
        self.clock.advance(minutes=1)
        (processor or PlexWebhookProcessor())._handle_tv_episode(
            self.show.media_id, season, episode, payload, self.user,
        )

    def tv(self):
        return TV.objects.filter(user=self.user, item=self.show).first()

    def viewings(self):
        return Episode.objects.filter(
            related_season__related_tv__user=self.user,
            related_season__related_tv__item=self.show,
        )

    def test_tv_802_a_watch_ticks_the_episode_at_the_event_time(self):
        self.event(PLEX_WATCHED, 1, 1)

        viewing = self.viewings().get()
        self.assertEqual(viewing.kind, Episode.Kind.TICK)
        self.assertEqual(viewing.end_date, self.clock.moment)
        tv = self.tv()
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(tv.choice, "")
        self.assertEqual(list(Season.objects.filter(related_tv=tv).values_list("item__season_number", flat=True)), [1])
        self.assertFalse(Activity.objects.filter(actor=self.user).exists())  # TV-703

    def test_tv_101_a_repeated_watch_records_nothing(self):
        self.event(PLEX_WATCHED, 1, 1)
        self.event(PLEX_WATCHED, 1, 1)
        self.event(EMBY_WATCHED, 1, 1, EmbyWebhookProcessor())

        self.assertEqual(self.viewings().count(), 1)

    def test_a_61_a_webhook_watch_after_an_app_tick_records_nothing(self):
        self.clock.advance(minutes=1)
        tv_tracking.tick(self.user, self.show, 1, 1)
        self.event(PLEX_WATCHED, 1, 1)

        self.assertEqual(self.viewings().count(), 1)

    def test_tv_102_an_unplayed_event_unticks_the_watch(self):
        self.event(PLEX_WATCHED, 1, 1)
        self.event(PLEX_WATCHED, 1, 2)
        self.event(JELLYFIN_UNPLAYED, 1, 2, JellyfinWebhookProcessor())

        self.assertEqual(list(self.viewings().values_list("item__episode_number", flat=True)), [1])
        self.assertEqual((self.tv().up_next_season, self.tv().up_next_episode), (1, 2))

    def test_tv_802_unplaying_a_logged_viewing_is_acknowledged_and_changes_nothing(self):
        episode_item = Item.objects.create(
            media_id=self.show.media_id,
            source=self.show.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
            title="Episode 1",
            image=self.show.image,
        )
        tv_tracking.create_log(self.user, episode_item, consumed_at=OCT_25)
        before = list(self.viewings().values_list("pk", "kind", "diary_entry_id"))

        self.event(JELLYFIN_UNPLAYED, 1, 1, JellyfinWebhookProcessor())

        self.assertEqual(list(self.viewings().values_list("pk", "kind", "diary_entry_id")), before)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user).count(), 1)

    def test_tv_106_an_unlisted_episode_is_acknowledged_and_records_nothing(self):
        self.event(PLEX_WATCHED, 1, 9)

        self.assertIsNone(self.tv())
        self.assertFalse(Season.objects.filter(user=self.user).exists())

    def test_tv_106_an_episode_airing_in_three_days_is_acknowledged_and_records_nothing(self):
        self.event(PLEX_WATCHED, 2, 1)
        self.event(PLEX_WATCHED, 2, 3)

        self.assertEqual(list(self.viewings().values_list("item__episode_number", flat=True)), [1])

    def test_tv_107_a_first_webhook_watch_partway_marks_nothing_earlier(self):
        self.event(PLEX_WATCHED, 2, 1)

        self.assertEqual(self.viewings().count(), 1)
        self.assertFalse(self.viewings().filter(kind=Episode.Kind.FILL).exists())
        self.assertEqual((self.tv().up_next_season, self.tv().up_next_episode), (2, 2))

    def test_a_41_a_play_on_a_completed_show_changes_nothing(self):
        show = make_show(self.fake, seasons={1: 2}, title="Finished Show")
        self.clock.advance(minutes=1)
        tv_tracking.mark_watched(self.user, show)
        tv = TV.objects.get(user=self.user, item=show)
        self.assertEqual(tv.status, Status.COMPLETED.value)
        before = (tv.status, tv.choice, tv.state_version)

        for processor, payload in (
            (PlexWebhookProcessor(), PLEX_PLAY),
            (JellyfinWebhookProcessor(), JELLYFIN_STOP),
        ):
            self.clock.advance(minutes=1)
            processor._handle_tv_episode(show.media_id, 1, 1, payload, self.user)

        tv.refresh_from_db()
        self.assertEqual((tv.status, tv.choice, tv.state_version), before)
        self.assertFalse(Episode.objects.filter(related_season__related_tv=tv, kind=Episode.Kind.TICK).exists())

    def test_a_41_a_play_on_a_paused_show_moves_it_to_watching(self):
        self.event(PLEX_WATCHED, 1, 1)
        self.clock.advance(minutes=1)
        tv_tracking.choose(self.user, self.show, Status.PAUSED.value)
        self.assertEqual(self.tv().status, Status.PAUSED.value)

        self.event(PLEX_PLAY, 1, 2)

        tv = self.tv()
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.viewings().count(), 1)
        self.assertFalse(Season.objects.filter(related_tv=tv, item__season_number=2).exists())

    def test_tv_802_a_play_on_an_untracked_show_starts_it_with_no_season_tracking(self):
        self.event(PLEX_PLAY, 2, 1)

        tv = self.tv()
        self.assertEqual((tv.status, tv.choice), (Status.IN_PROGRESS.value, Status.IN_PROGRESS.value))
        self.assertFalse(Season.objects.filter(related_tv=tv).exists())  # TV-205
        self.assertFalse(self.viewings().exists())

    def test_tv_802_an_unplayed_event_for_an_unknown_show_creates_nothing(self):
        self.clock.advance(minutes=1)
        JellyfinWebhookProcessor()._handle_tv_episode("424242", 1, 1, JELLYFIN_UNPLAYED, self.user)

        self.assertFalse(Item.objects.filter(media_id="424242").exists())

    def test_tv_801_a_webhook_on_a_legacy_show_records_its_fact_without_working_it_out(self):
        tv = TV.objects.create(user=self.user, item=self.show, status=Status.COMPLETED.value)
        TV.objects.filter(pk=tv.pk).update(legacy_pending=True)

        self.event(PLEX_WATCHED, 1, 1)

        # The stored Completed show is a legacy mark (TV-801): a fact from before the
        # watch, recorded first, so the watch records nothing (TV-101) and the repair
        # later ends where it would have without the webhook (TV-207). Nothing is
        # worked out: the show stays legacy with its stored status.
        tv.refresh_from_db()
        self.assertTrue(tv.legacy_pending)
        self.assertIsNone(tv.reconciled_at)
        self.assertEqual(tv.status, Status.COMPLETED.value)
        viewings = self.viewings()
        self.assertGreater(viewings.count(), 1)
        self.assertEqual(set(viewings.values_list("kind", "diary_entry", "watched_on")), {("fill", None, None)})


@override_settings(TIME_ZONE="America/New_York")
class TVWebhookRouteTests(WebhookProviderMocksMixin, TestCase):
    """Events through the HTTP routes: acknowledged, and dated on the user's calendar."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.user = get_user_model().objects.create_user(
            username="hooks",
            token="tv-hook-token",
            plex_usernames="hooks",
            calendar_timezone="America/Los_Angeles",
            jellyfin_mark_played_enabled=True,
            jellyfin_mark_unplayed_enabled=True,
        )
        self.patch_webhook_providers()
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def plex(self, event):
        payload = {
            "event": event,
            "Account": {"title": "hooks"},
            "Metadata": {
                "type": "episode",
                "grandparentTitle": "Friends",
                "index": 1,
                "parentIndex": 1,
                "Guid": [{"id": "imdb://tt0583459"}, {"id": "tvdb://303821"}],
            },
        }
        return self.client.post(
            reverse("plex_webhook", kwargs={"token": "tv-hook-token"}),
            data={"payload": json.dumps(payload)},
        )

    def jellyfin(self, event, played):
        payload = {
            "Event": event,
            "Item": {
                "Type": "Episode",
                "Name": "The One Where Monica Gets a Roommate",
                "ProviderIds": {"Tvdb": "303821", "Imdb": "tt0583459"},
                "SeriesName": "Friends",
                "ParentIndexNumber": 1,
                "IndexNumber": 1,
                "UserData": {"Played": played},
            },
        }
        return self.client.post(
            reverse("jellyfin_webhook", kwargs={"token": "tv-hook-token"}),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def viewings(self):
        return Episode.objects.filter(related_season__user=self.user)

    def test_tv_100_an_11pm_pacific_watch_lands_on_the_pacific_day(self):
        # 11 p.m. Pacific on Oct 25 is 2 a.m. on Oct 26 in New York.
        with patch_now(datetime(2026, 10, 25, 23, 0), tz="America/Los_Angeles"):
            response = self.plex("media.scrobble")

        self.assertEqual(response.status_code, 200)
        viewing = self.viewings().get()
        self.assertEqual(viewing.watched_on, OCT_25)
        tv = TV.objects.get(user=self.user, item__media_id="1668", item__source=Sources.TMDB.value)
        self.assertEqual(tv.last_watched_on, OCT_25)

    def test_tv_802_rejected_events_are_acknowledged_with_200(self):
        with patch_now(NOON_PACIFIC, tz="America/Los_Angeles") as clock:
            self.assertEqual(self.jellyfin("MarkPlayed", played=True).status_code, 200)
            episode_item = self.viewings().get().item
            clock.advance(minutes=1)
            tv_tracking.create_log(self.user, episode_item, consumed_at=OCT_25)
            clock.advance(minutes=1)

            response = self.jellyfin("MarkUnplayed", played=False)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.viewings().count(), 1)
        self.assertEqual(self.viewings().get().diary_entry.item, episode_item)
