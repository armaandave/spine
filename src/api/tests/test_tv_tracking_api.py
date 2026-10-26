"""TV tracking through the API (docs/tv-tracking-contract.md; plan 2.3 and 2.5).

The API layer only resolves Items, calls app.tv_tracking and builds responses; these
tests drive every TV endpoint the new build and installed builds call. The server runs
on America/New_York and the account calendar is America/Los_Angeles, as production
does; the clock is patched through django.utils.timezone.now and TMDB is the shared fake
(app.tests.tv_fixtures), so no test needs .env or API keys.

TVAPITestCase is the base test_tv_payload_contract imports.
"""

import math
from datetime import UTC, date, datetime, timedelta
from unittest import mock
from uuid import uuid4

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APIClient

from app.models import (
    TV,
    DiaryEntry,
    Episode,
    Item,
    MediaLike,
    MediaTypes,
    Season,
    Sources,
)
from app.tests.tv_fixtures import (
    FakeEpisode,
    FakeTVProvider,
    make_show,
    make_user,
    patch_now,
)
from lists.models import CustomList, CustomListItem
from social.models import Activity, ProgressChange

# 9 p.m. on Oct 25 in Los Angeles is midnight on Oct 26 in New York.
PACIFIC_9PM = datetime(2026, 10, 25, 21, 0)  # noqa: DTZ001 - patch_now reads it in Pacific time
OCT_25 = date(2026, 10, 25)
OCT_26 = date(2026, 10, 26)
PACIFIC = "America/Los_Angeles"

PROVIDER_PATCHES = (
    ("app.providers.tmdb.get_title_logo", None),
    ("app.providers.tmdb.get_title_logos", []),
    ("app.providers.tmdb.get_backdrop_images", []),
    ("app.providers.tmdb.get_season_backdrop_images", []),
    ("app.providers.tmdb.get_episode_backdrop_images", []),
    ("app.providers.mdblist.get_media_ratings", {}),
)


@override_settings(TIME_ZONE="America/New_York")
class TVAPITestCase(TestCase):
    """A fake TMDB, a fixed clock at 9 p.m. Pacific and a signed-in Los Angeles user."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(PACIFIC_9PM, tz=PACIFIC).__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        for target, value in PROVIDER_PATCHES:
            patcher = mock.patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = make_user(calendar=PACIFIC)
        self.client = self.client_for(self.user)

    def client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_SPINE_TIMEZONE=PACIFIC)
        return client

    # Builders

    def make_show(self, seasons=None, **kwargs):
        return make_show(self.fake, seasons=seasons if seasons is not None else {1: 10, 2: 8}, **kwargs)

    def ref(self, show, season=None, episode=None):
        if episode is not None:
            media_type = MediaTypes.EPISODE.value
        elif season is not None:
            media_type = MediaTypes.SEASON.value
        else:
            media_type = MediaTypes.TV.value
        return {
            "source": show.source,
            "media_type": media_type,
            "media_id": show.media_id,
            "season_number": season,
            "episode_number": episode,
        }

    def url(self, show, path=""):
        return f"/api/v1/tracking/{show.source}/tv/{show.media_id}/{path}"

    # Requests (each advances the clock first, so "recorded" order is real)

    def action(self, show, action, client=None, **body):
        self.clock.advance(minutes=1)
        return (client or self.client).post(self.url(show, f"actions/{action}/"), body, format="json")

    def tick(self, show, season, episode, **body):
        return self.action(show, "tick", season_number=season, episode_number=episode, **body)

    def ticks(self, show, season, episodes):
        for episode in episodes:
            response = self.tick(show, season, episode)
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response

    def log(self, show, season=None, episode=None, client=None, **body):
        """Save a log as the new build does: a calendar date and a mutation ID."""
        self.clock.advance(minutes=1)
        body.setdefault("consumed_at", OCT_25.isoformat())
        body.setdefault("mutation_id", str(uuid4()))
        return (client or self.client).post(
            "/api/v1/diary/",
            {"ref": self.ref(show, season, episode), **body},
            format="json",
        )

    def installed_build_log(self, show, season=None, episode=None, **body):
        """Save a log with the installed builds' shape (MediaLogView.swift:304-316)."""
        self.clock.advance(minutes=1)
        payload = {
            "ref": self.ref(show, season, episode),
            "consumed_at": "2026-10-26T04:00:00Z",
            "review": "",
            "review_title": "",
            "liked": False,
            "is_rewatch": False,
            "auto_mark_consumed": True,
            "contains_spoilers": False,
            "visibility": "public",
            "tags": [],
            **body,
        }
        return self.client.post("/api/v1/diary/", payload, format="json")

    def like(self, show, season=None, episode=None, *, liked=True):
        self.clock.advance(minutes=1)
        method = self.client.post if liked else self.client.delete
        return method("/api/v1/me/liked-media/", {"ref": self.ref(show, season, episode)}, format="json")

    def detail(self, show, season=None, episode=None):
        if episode is not None:
            return self.client.get(
                f"/api/v1/media/{show.source}/episode/{show.media_id}/",
                {"season_number": season, "episode_number": episode},
            )
        if season is not None:
            return self.client.get(f"/api/v1/media/{show.source}/tv/{show.media_id}/seasons/{season}/")
        return self.client.get(f"/api/v1/media/{show.source}/tv/{show.media_id}/")

    # Reading

    def tv(self, show):
        return TV.objects.filter(user=self.user, item=show).first()

    def viewings(self, show, user=None):
        return list(
            Episode.objects.filter(
                related_season__related_tv__user=user or self.user,
                related_season__related_tv__item=show,
            ).order_by("created_at", "id"),
        )

    def watched(self, show):
        return {(viewing.item.season_number, viewing.item.episode_number) for viewing in self.viewings(show)}

    def assert_error(self, response, status_code, code, message=None):
        self.assertEqual(response.status_code, status_code, response.data)
        self.assertEqual(response.data["error"]["code"], code)
        if message is not None:
            self.assertEqual(response.data["error"]["message"], message)


class TVActionEndpointTests(TVAPITestCase):
    """POST tracking/{source}/tv/{id}/actions/{action}/ (plan 2.3.7)."""

    def test_tv_100_tick_returns_the_write_envelope(self):
        show = self.make_show()

        response = self.tick(show, 1, 1)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data),
            {"tracking", "tv", "season", "episode", "changed", "finished", "earlier_unwatched"},
        )
        self.assertTrue(response.data["changed"])
        self.assertIsNone(response.data["finished"])
        self.assertIsNone(response.data["earlier_unwatched"])
        tracking = response.data["tracking"]
        self.assertEqual(tracking["status"], "In progress")
        self.assertEqual(tracking["progress"], {"kind": "episodes", "value": 1, "max": 18, "unit": "episode"})
        self.assertEqual(tracking["tv"], response.data["tv"])
        self.assertEqual(response.data["tv"]["up_next"]["season_number"], 1)
        self.assertEqual(response.data["tv"]["up_next"]["episode_number"], 2)
        viewing = self.viewings(show)[0]
        self.assertEqual(response.data["episode"]["untick_viewing_id"], viewing.pk)
        self.assertEqual(response.data["season"]["watched_count"], 1)
        # The tick's day is the Pacific day, though it is Oct 26 in New York (TV-100).
        self.assertEqual(viewing.kind, "tick")
        self.assertEqual(viewing.watched_on, OCT_25)

    def test_tv_703_ticks_write_no_activity_row_and_record_progress(self):
        show = self.make_show()

        self.ticks(show, 1, [1, 2])

        self.assertFalse(Activity.objects.filter(actor=self.user).exists())
        season_item = Item.objects.get(media_id=show.media_id, media_type=MediaTypes.SEASON.value, season_number=1)
        changes = ProgressChange.objects.filter(actor=self.user, item=season_item).order_by("id")
        self.assertEqual([change.current_progress["value"] for change in changes], [1, 2])

    def test_tv_101_a_retried_tick_records_one_viewing(self):
        show = self.make_show()
        mutation_id = str(uuid4())

        first = self.tick(show, 1, 1, mutation_id=mutation_id)
        replay = self.tick(show, 1, 1, mutation_id=mutation_id)
        again = self.tick(show, 1, 1)

        self.assertTrue(first.data["changed"])
        self.assertTrue(replay.data["changed"])
        self.assertFalse(again.data["changed"])
        self.assertEqual(len(self.viewings(show)), 1)

    def test_tv_705_a_mutation_id_reused_for_another_action_is_400(self):
        show = self.make_show()
        mutation_id = str(uuid4())
        self.tick(show, 1, 1, mutation_id=mutation_id)

        response = self.action(show, "mark_watched", season_number=2, mutation_id=mutation_id)

        self.assert_error(
            response,
            status.HTTP_400_BAD_REQUEST,
            "validation_error",
            "This request was already used for a different change.",
        )
        self.assertEqual(self.watched(show), {(1, 1)})

    def test_tv_106_rejects_unlisted_and_unaired_episodes(self):
        """A-22: ticking or logging an unlisted episode, or one airing in three days."""
        show = self.make_show(seasons={1: [date(2020, 1, 6), OCT_25 + timedelta(days=3)]})

        unlisted = self.tick(show, 1, 99)
        unaired = self.tick(show, 1, 2)
        unlisted_log = self.log(show, 1, 99)
        unaired_log = self.log(show, 1, 2)

        self.assert_error(unlisted, 400, "validation_error", "This episode isn't listed for Season 1.")
        self.assert_error(unaired, 400, "validation_error", "This episode hasn't aired yet.")
        self.assert_error(unlisted_log, 400, "validation_error", "This episode isn't listed for Season 1.")
        self.assert_error(unaired_log, 400, "validation_error", "This episode hasn't aired yet.")
        self.assertIsNone(self.tv(show))
        self.assertFalse(DiaryEntry.objects.filter(user=self.user).exists())

    def test_tv_106_episode_actions_need_season_and_episode_numbers(self):
        show = self.make_show()

        for action in ("tick", "untick", "mark_up_to"):
            response = self.action(show, action, episode_number=1)
            self.assert_error(response, 400, "validation_error")
            self.assertIn("season_number", response.data["error"]["fields"])

        self.assertIsNone(self.tv(show))

    def test_an_unknown_action_is_404(self):
        show = self.make_show()

        response = self.action(show, "binge")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_tv_103_a_write_without_episode_lists_is_a_retryable_503(self):
        show = self.make_show()
        self.fake.fail()

        response = self.tick(show, 1, 1)

        # provider_unavailable is the 503 code installed builds show the message of; any
        # other code reads as "start the backend" there (TV-900). reason names the case.
        self.assert_error(response, 503, "provider_unavailable", "TV details are unavailable right now")
        self.assertEqual(response.data["error"]["reason"], "tv_details_unavailable")
        self.assertIsNone(self.tv(show))

    def test_tv_102_untick_blocked_by_a_season_log_is_409(self):
        """A-48: tick a season, log it, then untick its finale."""
        show = self.make_show()
        self.ticks(show, 1, range(1, 11))
        self.assertEqual(self.log(show, 1).status_code, status.HTTP_201_CREATED)

        response = self.action(show, "untick", season_number=1, episode_number=10)

        self.assert_error(response, 409, "tv_log_blocks", "Delete your Season 1 log first.")
        self.assertIn((1, 10), self.watched(show))

    def test_tv_102_untick_names_the_viewing_it_removes(self):
        show = self.make_show()
        viewing_id = self.tick(show, 1, 1).data["episode"]["untick_viewing_id"]

        stale = self.action(show, "untick", season_number=1, episode_number=1, viewing_id=viewing_id + 1000)
        removed = self.action(show, "untick", season_number=1, episode_number=1, viewing_id=viewing_id)

        self.assertFalse(stale.data["changed"])
        self.assertTrue(removed.data["changed"])
        # A-03: the only viewing gone, the show leaves the library.
        self.assertIsNone(removed.data["tracking"])
        self.assertFalse(removed.data["tv"]["in_library"])

    def test_tv_302_case_3_needs_confirmation_and_a_current_version(self):
        show = self.make_show()
        ticked = self.ticks(show, 1, range(1, 11))
        season = ticked.data["season"]
        self.assertEqual(season["eye"]["tap"], "unmark_confirm")
        version = ticked.data["tv"]["version"]

        unconfirmed = self.action(show, "unmark", season_number=1, mutation_id=str(uuid4()))
        stale = self.action(
            show,
            "unmark",
            season_number=1,
            confirm=True,
            expected_version=version - 1,
            mutation_id=str(uuid4()),
        )

        self.assert_error(unconfirmed, 400, "validation_error")
        self.assert_error(stale, 409, "tv_state_changed")
        self.assertEqual(stale.data["error"]["tv"], {"state_version": version, "count": 10})
        self.assertEqual(len(self.viewings(show)), 10)

        confirmed = self.action(
            show,
            "unmark",
            season_number=1,
            confirm=True,
            expected_version=version,
            mutation_id=str(uuid4()),
        )

        self.assertEqual(confirmed.status_code, status.HTTP_200_OK)
        self.assertEqual(self.viewings(show), [])

    def test_tv_107_a_first_tick_partway_asks_about_earlier_episodes(self):
        show = self.make_show()

        response = self.tick(show, 2, 1)

        self.assertEqual(
            response.data["earlier_unwatched"],
            {
                "from": {"season_number": 1, "episode_number": 1},
                "to": {"season_number": 1, "episode_number": 10},
                "count": 10,
                "seasons": [1],
                "question": "Also mark S1 E1–E10 watched?",  # noqa: RUF001
                "offer_season_number": 2,
            },
        )

    def test_tv_107_yes_marks_the_range_once_with_a_mutation_id(self):
        show = self.make_show()
        question = self.tick(show, 2, 1).data["earlier_unwatched"]
        body = {"from": question["from"], "to": question["to"], "offer_season_number": 2}

        missing_id = self.action(show, "mark_earlier", **body)
        mutation_id = str(uuid4())
        yes = self.action(show, "mark_earlier", mutation_id=mutation_id, **body)
        retry = self.action(show, "mark_earlier", mutation_id=mutation_id, **body)

        self.assert_error(missing_id, 400, "validation_error")
        self.assertEqual(yes.status_code, status.HTTP_200_OK)
        self.assertTrue(yes.data["changed"])
        self.assertIsNone(yes.data["finished"])
        self.assertEqual(retry.status_code, status.HTTP_200_OK)
        fills = [viewing for viewing in self.viewings(show) if viewing.kind == "fill"]
        self.assertEqual(len(fills), 10)
        self.assertTrue(all(fill.watched_on is None for fill in fills))

    def test_tv_403_finishing_a_season_offers_its_log_once(self):
        """A-05: finish Season 1, skip the composer, untick and re-tick the finale."""
        show = self.make_show()

        finale = self.ticks(show, 1, range(1, 11))
        self.action(show, "untick", season_number=1, episode_number=10)
        again = self.tick(show, 1, 10)

        finished = finale.data["finished"]
        self.assertEqual(finished["level"], "season")
        self.assertEqual(finished["season_number"], 1)
        self.assertFalse(finished["show_finished"])
        self.assertEqual(finished["composer"]["default_target"], "season:1")
        self.assertEqual([target["id"] for target in finished["composer"]["targets"]], ["season:1"])
        self.assertEqual(finished["composer"]["targets"][0]["date"], OCT_25.isoformat())
        self.assertIsNone(again.data["finished"])
        self.assertFalse(DiaryEntry.objects.filter(user=self.user).exists())

    def test_tv_005_status_actions_and_their_reasons(self):
        show = self.make_show()

        planning = self.action(show, "planning")
        paused = self.action(show, "pause")
        watching = self.action(show, "watching")
        self.tick(show, 1, 1)
        refused = self.action(show, "planning")
        dropped = self.action(show, "drop")
        resumed = self.action(show, "resume")

        self.assertEqual(planning.data["tracking"]["status"], "Planning")
        self.assertEqual(planning.data["tv"]["choice"], "Planning")
        self.assertEqual(paused.data["tracking"]["status"], "Paused")
        self.assertEqual(watching.data["tv"]["selected_action"], "watching")
        self.assert_error(refused, 409, "tv_action_unavailable", "You've started this show. Pause or drop it instead.")
        self.assertEqual(dropped.data["tracking"]["status"], "Dropped")
        self.assertEqual(resumed.data["tracking"]["status"], "In progress")
        self.assertEqual(
            resumed.data["tv"]["action_reasons"]["planning"],
            "You've started this show. Pause or drop it instead.",
        )
        # TV-205: choices create no season tracking beyond the ticked season.
        self.assertEqual(
            list(Season.objects.filter(user=self.user).values_list("item__season_number", flat=True)),
            [1],
        )

    def test_tv_600_rewatch_needs_a_finished_show(self):
        show = self.make_show(seasons={1: 3})
        self.tick(show, 1, 1)

        early = self.action(show, "rewatch")
        self.action(show, "mark_watched")
        rewatch = self.action(show, "rewatch")
        stopped = self.action(show, "stop_rewatch")

        self.assert_error(early, 409, "tv_action_unavailable", "Finish this show before rewatching it.")
        self.assertEqual(rewatch.data["tv"]["rewatch"]["scope"], "show")
        self.assertEqual(rewatch.data["tracking"]["status"], "In progress")
        self.assertIsNone(stopped.data["tv"]["rewatch"])
        self.assertEqual(stopped.data["tracking"]["status"], "Completed")

    def test_tv_003_remove_from_library(self):
        """A-44: Remove is blocked by logs; otherwise it removes and keeps lists."""
        show = self.make_show()
        logged = self.make_show()
        self.ticks(show, 1, [1, 2])
        self.like(show)
        custom_list = CustomList.objects.create(owner=self.user, name="Favourites")
        CustomListItem.objects.create(custom_list=custom_list, item=show)
        self.log(logged, 1)
        version = self.client.get(self.url(show)).data["tv"]["version"]

        without_id = self.action(show, "remove", expected_version=version)
        blocked = self.action(logged, "remove", expected_version=0, mutation_id=str(uuid4()))
        stale = self.action(show, "remove", expected_version=version - 1, mutation_id=str(uuid4()))
        removed = self.action(show, "remove", expected_version=version, mutation_id=str(uuid4()))

        self.assert_error(without_id, 400, "validation_error")
        self.assert_error(blocked, 409, "tv_log_blocks", "Delete this show's logs first.")
        self.assert_error(stale, 409, "tv_state_changed")
        self.assertEqual(stale.data["error"]["tv"]["count"], 2)
        self.assertEqual(removed.status_code, status.HTTP_200_OK)
        self.assertIsNone(removed.data["tracking"])
        self.assertFalse(removed.data["tv"]["in_library"])
        self.assertIsNone(self.tv(show))
        self.assertEqual(self.viewings(show), [])
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=show).exists())
        self.assertTrue(CustomListItem.objects.filter(custom_list=custom_list, item=show).exists())
        self.assertIsNotNone(self.tv(logged))

    def test_tv_504_rate_accepts_only_whole_values_from_1_to_10(self):
        show = self.make_show()
        self.tick(show, 1, 1)

        for rating in (8.5, 0, 11, "eight", True):
            response = self.action(show, "rate", rating=rating)
            self.assert_error(response, 400, "validation_error")
            self.assertIn("rating", response.data["error"]["fields"])
        missing = self.action(show, "rate")
        rated = self.action(show, "rate", rating=8)
        cleared = self.action(show, "rate", rating=None)

        self.assert_error(missing, 400, "validation_error")
        self.assertEqual(rated.data["tracking"]["rating"], "8.0")
        self.assertIsNone(cleared.data["tracking"]["rating"])

    def test_tv_303_stars_on_an_unstarted_show_mark_it_watched(self):
        show = self.make_show()

        response = self.action(show, "rate", rating=8)

        self.assertEqual(response.data["tracking"]["status"], "Completed")
        self.assertEqual(response.data["tracking"]["rating"], "8.0")
        self.assertEqual(response.data["tv"]["watched_count"], 18)
        self.assertEqual(response.data["tv"]["eye"]["tap"], "unmark_marks")
        # SW-401: direct stars create rating activity on the 0-10 scale.
        activity = Activity.objects.get(actor=self.user, verb="rating_updated")
        self.assertEqual(activity.snapshot, {"rating": "8.0"})

    def test_tv_303_season_stars_on_an_unstarted_show_ask_about_earlier_seasons(self):
        show = self.make_show()

        response = self.action(show, "rate", season_number=2, rating=8)

        self.assertEqual(response.data["season"]["season_number"], 2)
        self.assertEqual(response.data["season"]["rating"], "8.0")
        self.assertEqual(response.data["season"]["state"], "watched")
        self.assertEqual(response.data["earlier_unwatched"]["question"], "Also mark Season 1 watched?")
        self.assertEqual(response.data["earlier_unwatched"]["seasons"], [1])
        self.assertIsNone(response.data["tracking"]["rating"])

    def test_tv_404_an_action_from_a_season_page_returns_that_pages_composer(self):
        """A-56: after the Season 2 eye on its page, Log Season defaults to Rewatch.

        The action names its page, so `tv` carries the page's season and composer; the
        response's own `season` and `episode` stay the write's (TV-705).
        """
        show = self.make_show(seasons={1: 3, 2: 3, 3: 3})
        self.log(show, 1)
        before = self.detail(show, 2).data["user_state"]["tv"]
        self.assertFalse(before["composer"]["targets"][0]["is_rewatch"])

        marked = self.action(show, "mark_watched", season_number=2, page_season_number=2)
        ticked = self.action(show, "tick", season_number=1, episode_number=1, page_season_number=2)
        from_episode = self.action(show, "tick", season_number=3, episode_number=1, page_season_number=3, page_episode_number=1)

        tv = marked.data["tv"]
        self.assertEqual(tv["season"]["season_number"], 2)
        self.assertEqual(tv["season"]["state"], "watched")
        self.assertEqual(tv["composer"]["default_target"], "season:2")
        self.assertTrue(tv["composer"]["targets"][0]["is_rewatch"])
        self.assertEqual(marked.data["season"]["season_number"], 2)
        # A write about another level keeps its own season and episode beside `tv`.
        self.assertEqual(ticked.data["episode"]["episode_number"], 1)
        self.assertEqual(ticked.data["tv"]["season"]["season_number"], 2)
        self.assertEqual(from_episode.data["tv"]["episode"]["season_number"], 3)
        self.assertTrue(from_episode.data["tv"]["episode"]["watched"])
        self.assertEqual(from_episode.data["tv"]["composer"]["default_target"], "episode:3:1")
        # Without a page `tv` describes the show (the show page, Home and Profile).
        self.assertIsNone(self.action(show, "rate", rating=7).data["tv"]["season"])

    def test_tv_302_an_eye_undo_from_a_season_page_returns_its_episode_rows(self):
        """TV-302 case 2, TV-705: the rows show what the undo kept, before any reload.

        Tick E1, mark the season with its eye, then undo it: E1 stays watched and the
        mark's fills go. A season page's action responses carry its episode states.
        """
        show = self.make_show(seasons={1: 4})
        self.tick(show, 1, 1, page_season_number=1)
        self.action(show, "mark_watched", season_number=1, page_season_number=1)

        undone = self.action(show, "unmark", season_number=1, page_season_number=1)

        self.assertEqual(undone.status_code, status.HTTP_200_OK, undone.data)
        rows = {row["episode_number"]: row["watched"] for row in undone.data["season_episodes"]}
        self.assertEqual(rows, {1: True, 2: False, 3: False, 4: False})
        self.assertEqual(undone.data["tv"]["season"]["watched_count"], 1)
        self.assertNotIn("season_episodes", self.tick(show, 1, 2).data)

    def test_tv_005_rate_is_offered_exactly_when_the_stars_write_saves(self):
        """TV-005, TV-303: the server's "rate" action and the stars write agree.

        Includes a show whose only aired episode is a special, before and after it is
        ticked, and a season with nothing aired.
        """
        specials_only = self.make_show(seasons={0: [date(2026, 10, 1)], 1: [date(2027, 1, 10)]})
        unaired = self.make_show(seasons={1: [date(2027, 1, 10)]})
        aired = self.make_show(seasons={1: 2, 2: [date(2027, 1, 10)]})
        cases = []

        def check(name, show, season=None):
            state = self.detail(show, season).data["user_state"]["tv"]
            actions = state["available_actions"] if season is None else state["season"]["available_actions"]
            body = {"rating": 7} if season is None else {"rating": 7, "season_number": season}
            response = self.action(show, "rate", **body)
            cases.append((name, "rate" in actions, response.status_code == status.HTTP_200_OK))
            if response.status_code != status.HTTP_200_OK:
                self.assert_error(response, 409, "tv_action_unavailable", "Nothing has aired yet.")

        check("specials only, not started", specials_only)
        self.tick(specials_only, 0, 1)
        check("specials only, started", specials_only)
        check("nothing aired", unaired)
        check("an aired show", aired)
        check("an unaired season", aired, season=2)
        check("an aired season", aired, season=1)

        self.assertEqual(
            cases,
            [
                ("specials only, not started", False, False),
                ("specials only, started", True, True),
                ("nothing aired", False, False),
                ("an aired show", True, True),
                ("an unaired season", False, False),
                ("an aired season", True, True),
            ],
        )

    def test_tv_304_stars_on_a_started_show_are_opinion_only(self):
        """A-29: stars on a show at S1 E3 change nothing else."""
        show = self.make_show()
        before = self.ticks(show, 1, [1, 2, 3]).data["tv"]

        response = self.action(show, "rate", rating=6)

        self.assertEqual(response.data["tracking"]["status"], "In progress")
        self.assertEqual(response.data["tv"]["up_next"], before["up_next"])
        self.assertEqual(response.data["tv"]["watched_count"], 3)
        self.assertTrue(Activity.objects.filter(actor=self.user, verb="rating_updated").exists())

    def test_tv_300_mark_watched_and_its_consume_alias(self):
        show = self.make_show()
        other = self.make_show()

        marked = self.action(show, "mark_watched")
        consumed = self.action(other, "consume")

        for response in (marked, consumed):
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["tracking"]["status"], "Completed")
            self.assertEqual(response.data["tv"]["watched_count"], 18)
        self.assertTrue(all(viewing.watched_on is None for viewing in self.viewings(show)))


class TVInstalledBuildEndpointTests(TVAPITestCase):
    """The endpoints and request shapes installed builds send (TV-900)."""

    def test_tv_900_installed_build_episode_watch(self):
        """AppRepositories.swift:944-956: POST .../watch/ with watched_at."""
        show = self.make_show()
        url = self.url(show, "seasons/1/episodes/1/watch/")

        first = self.client.post(url, {"watched_at": "2026-01-01T12:00:00Z"}, format="json")
        retry = self.client.post(url, {"watched_at": "2026-01-01T12:00:00Z"}, format="json")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        season = Season.objects.get(user=self.user, item__season_number=1)
        self.assertEqual(first.data["tracking_id"], season.pk)
        self.assertEqual(first.data["status"], "In progress")
        self.assertEqual(first.data["progress"], {"kind": "episodes", "value": 1, "max": 10, "unit": "episode"})
        self.assertEqual(first.data["latest_progress_change"]["current"]["value"], 1)
        self.assertIn("tv", first.data)
        self.assertIsNone(first.data["finished"])
        self.assertIsNone(first.data["earlier_unwatched"])
        # TV-100: watched_at is ignored; the server dates the tick.
        self.assertEqual([viewing.watched_on for viewing in self.viewings(show)], [OCT_25])
        self.assertFalse(retry.data["changed"])

    def test_tv_900_pre_contract_actions_keep_a_top_level_tracking_state(self):
        """consume, pause, resume and drop returned a TrackingState before the contract.

        They keep it at the top level, beside the new keys (TV-900: additions only).
        """
        show = self.make_show()
        responses = {action: self.action(show, action) for action in ("consume", "pause", "resume", "drop")}

        tv = self.tv(show)
        for action, response in responses.items():
            with self.subTest(action=action):
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                data = response.data
                self.assertEqual(data["tracking_id"], tv.pk)
                self.assertEqual(data["status"], data["tracking"]["status"])
                self.assertEqual(data["progress"], data["tracking"]["progress"])
                self.assertTrue({"tv", "season", "episode", "changed", "finished", "earlier_unwatched"} <= set(data))
        self.assertEqual(
            [responses[action].data["status"] for action in ("consume", "pause", "resume", "drop")],
            ["Completed", "Paused", "Completed", "Dropped"],
        )
        # The new action names keep the nested shape only.
        ticked = self.tick(self.make_show(), 1, 1)
        self.assertNotIn("tracking_id", ticked.data)

    def test_tv_102_installed_build_episode_unwatch(self):
        show = self.make_show()
        self.client.post(self.url(show, "seasons/1/episodes/1/watch/"), {}, format="json")
        self.client.post(self.url(show, "seasons/1/episodes/2/watch/"), {}, format="json")

        response = self.client.delete(self.url(show, "seasons/1/episodes/2/watch/"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["progress"]["value"], 1)
        self.assertEqual(self.watched(show), {(1, 1)})

    def test_tv_300_season_watch_response_is_fresh(self):
        """T23: the season watch response showed progress 0."""
        show = self.make_show()

        marked = self.client.post(self.url(show, "seasons/1/watch/"), {}, format="json")
        unmarked = self.client.delete(self.url(show, "seasons/1/watch/"))

        self.assertEqual(marked.status_code, status.HTTP_200_OK)
        self.assertEqual(marked.data["status"], "Completed")
        self.assertEqual(marked.data["progress"], {"kind": "episodes", "value": 10, "max": 10, "unit": "episode"})
        self.assertEqual(unmarked.status_code, status.HTTP_200_OK)
        self.assertFalse(unmarked.data["tv"]["in_library"])
        self.assertEqual(self.viewings(show), [])

    def test_tv_205_start_endpoints_create_no_seasons(self):
        show = self.make_show()
        other = self.make_show()

        started = self.client.post(self.url(show, "start/"), {}, format="json")
        season_started = self.client.post(self.url(other, "seasons/2/start/"), {}, format="json")

        self.assertEqual(started.data["status"], "In progress")
        self.assertEqual(started.data["tv"]["choice"], "In progress")
        self.assertEqual(season_started.data["status"], "In progress")
        self.assertEqual(season_started.data["tracking_id"], self.tv(other).pk)
        self.assertFalse(Season.objects.filter(user=self.user).exists())

    def test_tv_207_written_statuses_mean_their_actions(self):
        """A-57 (API): a season's Dropped is a 400 and the show stays Watching."""
        show = self.make_show()
        season_url = f"/api/v1/tracking/tmdb/season/{show.media_id}/"
        show_url = f"/api/v1/tracking/tmdb/tv/{show.media_id}/"
        self.tick(show, 1, 1)

        dropped_season = self.client.patch(season_url, {"season_number": 1, "status": "Dropped"}, format="json")
        no_number = self.client.patch(season_url, {"status": "Completed"}, format="json")
        progress = self.client.patch(show_url, {"progress": 3}, format="json")
        dates = self.client.patch(show_url, {"start_date": "2026-01-01T00:00:00Z"}, format="json")

        self.assert_error(dropped_season, 400, "validation_error", "Pause, drop or plan the show instead.")
        self.assert_error(no_number, 400, "validation_error")
        self.assertIn("season_number", no_number.data["error"]["fields"])
        self.assert_error(progress, 400, "validation_error")
        self.assert_error(dates, 400, "validation_error")
        self.assertEqual(self.tv(show).status, "In progress")

        completed_season = self.client.patch(season_url, {"season_number": 2, "status": "Completed"}, format="json")
        dropped_show = self.client.put(show_url, {"status": "Dropped"}, format="json")

        self.assertEqual(completed_season.status_code, status.HTTP_200_OK)
        self.assertEqual(completed_season.data["status"], "Completed")
        self.assertEqual(completed_season.data["season"]["season_number"], 2)
        self.assertEqual(dropped_show.data["status"], "Dropped")
        self.assertEqual(dropped_show.data["tv"]["choice"], "Dropped")
        self.assertIn("changed", dropped_show.data)

    def test_tv_207_completed_on_a_show_marks_it_watched(self):
        show = self.make_show()

        response = self.client.patch(f"/api/v1/tracking/tmdb/tv/{show.media_id}/", {"status": "Completed"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "Completed")
        self.assertEqual(response.data["progress"]["value"], 18)
        self.assertEqual(len(self.viewings(show)), 18)

    def test_tv_504_season_stars_from_installed_builds(self):
        """Installed builds PATCH season stars, which today is a 500 (T32)."""
        show = self.make_show()
        self.tick(show, 1, 1)
        url = f"/api/v1/tracking/tmdb/season/{show.media_id}/"

        rated = self.client.patch(url, {"season_number": 2, "rating": "8.0"}, format="json")
        half = self.client.patch(url, {"season_number": 2, "rating": "8.5"}, format="json")

        self.assertEqual(rated.status_code, status.HTTP_200_OK)
        self.assertEqual(rated.data["rating"], "8.0")
        # TV-304: opinion only on a started show; the season isn't started.
        self.assertIsNone(rated.data["status"])
        self.assertFalse(rated.data["season"]["started"])
        self.assert_error(half, 400, "validation_error")
        self.assertEqual(self.watched(show), {(1, 1)})

    def test_tv_003_tracking_deletes(self):
        show = self.make_show()
        logged = self.make_show()
        self.ticks(show, 1, [1, 2])
        self.tick(show, 2, 1)
        self.log(logged, 1)

        blocked_show = self.client.delete(f"/api/v1/tracking/tmdb/tv/{logged.media_id}/")
        blocked_season = self.client.delete(f"/api/v1/tracking/tmdb/season/{logged.media_id}/?season_number=1")
        no_number = self.client.delete(f"/api/v1/tracking/tmdb/season/{show.media_id}/")
        season_deleted = self.client.delete(f"/api/v1/tracking/tmdb/season/{show.media_id}/?season_number=2")

        self.assert_error(blocked_show, 409, "tv_log_blocks", "Delete this show's logs first.")
        self.assert_error(blocked_season, 409, "tv_log_blocks")
        self.assert_error(no_number, 400, "validation_error")
        self.assertEqual(season_deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.watched(show), {(1, 1), (1, 2)})

        removed = self.client.delete(f"/api/v1/tracking/tmdb/tv/{show.media_id}/")

        self.assertEqual(removed.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIsNone(self.tv(show))

    def test_tracking_detail_reads(self):
        show = self.make_show()
        self.tick(show, 1, 1)
        self.client.patch(
            f"/api/v1/tracking/tmdb/season/{show.media_id}/",
            {"season_number": 2, "rating": 7},
            format="json",
        )

        show_state = self.client.get(f"/api/v1/tracking/tmdb/tv/{show.media_id}/")
        season_state = self.client.get(f"/api/v1/tracking/tmdb/season/{show.media_id}/", {"season_number": 1})
        unstarted = self.client.get(f"/api/v1/tracking/tmdb/season/{show.media_id}/", {"season_number": 2})
        episode_state = self.client.get(
            f"/api/v1/tracking/tmdb/episode/{show.media_id}/",
            {"season_number": 1, "episode_number": 1},
        )

        self.assertEqual(show_state.status_code, status.HTTP_200_OK)
        self.assertIn("composer", show_state.data["tv"])
        self.assertIsNone(show_state.data["tv"]["season"])
        self.assertEqual(season_state.data["tv"]["season"]["season_number"], 1)
        # TV-205: a season that isn't started answers 404.
        self.assertEqual(unstarted.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(episode_state.status_code, status.HTTP_200_OK)
        self.assertTrue(episode_state.data["tv"]["episode"]["watched"])

    def test_tv_207_generic_season_actions(self):
        show = self.make_show()
        url = f"/api/v1/tracking/tmdb/season/{show.media_id}/actions/"

        paused = self.client.post(f"{url}pause/", {"season_number": 1}, format="json")
        consumed = self.client.post(f"{url}consume/", {"season_number": 1}, format="json")

        self.assert_error(paused, 400, "validation_error", "Pause, drop or plan the show instead.")
        self.assertEqual(consumed.status_code, status.HTTP_200_OK)
        self.assertEqual(consumed.data["status"], "Completed")
        self.assertEqual(len(self.viewings(show)), 10)


class TVListEndpointTests(TVAPITestCase):
    """GET tracking/ for TV (TV-002, TV-104, TV-207, TV-701, TV-900)."""

    def tracked(self, **params):
        # A fresh user object, as each real request loads one (the completion counts are
        # cached on the user object).
        self.client.force_authenticate(type(self.user).objects.get(pk=self.user.pk))
        response = self.client.get("/api/v1/tracking/", params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data["results"]

    def titles(self, results):
        return [row["media"]["title"] for row in results]

    def test_tv_002_five_library_sections(self):
        watching = self.make_show(title="Watching")
        planning = self.make_show(title="Planning")
        paused = self.make_show(title="Paused")
        dropped = self.make_show(title="Dropped")
        completed = self.make_show(title="Completed")
        self.tick(watching, 1, 1)
        self.action(planning, "planning")
        self.tick(paused, 1, 1)
        self.action(paused, "pause")
        self.action(dropped, "drop")
        self.action(completed, "mark_watched")

        for section in ("In progress", "Planning", "Paused", "Dropped", "Completed"):
            expected = "Watching" if section == "In progress" else section
            self.assertEqual(self.titles(self.tracked(media_type="tv", status=section)), [expected])
        self.assertCountEqual(
            self.titles(self.tracked(media_type="tv", status="tracked")),
            ["Watching", "Paused", "Dropped", "Completed"],
        )

    def test_tv_104_list_progress_is_watched_over_aired(self):
        """E1-E3 and E5 of a ten-episode season read 4, not 5."""
        show = self.make_show()
        self.ticks(show, 1, [1, 2, 3, 5])

        row = self.tracked(media_type="tv")[0]

        self.assertEqual(row["tracking"]["progress"], {"kind": "episodes", "value": 4, "max": 18, "unit": "episode"})
        self.assertEqual(row["tracking"]["tv"]["watched_count"], 4)
        self.assertEqual(row["tracking"]["tv"]["up_next"]["episode_number"], 6)
        self.assertEqual(row["media"]["user_state"]["tv"], row["tracking"]["tv"])
        tv = self.tv(show)
        self.assertEqual(row["tracking"]["updated_at"], tv.last_activity_at)

    def test_tv_207_tv_lists_load_no_viewings(self):
        """The Library and Continue Watching read stored values and two dates per show.

        They never load each show's viewings (TV-207); start_date and end_date are still
        the first and last regular viewing, specials left out.
        """
        long = self.make_show(title="Long", seasons={0: 2, 1: 30, 2: 30})
        short = self.make_show(title="Short", seasons={1: 5})
        self.action(long, "mark_watched")
        self.ticks(long, 2, [1])  # already watched: records nothing
        self.tick(long, 0, 1)
        self.ticks(short, 1, [1, 2])
        long_tv, short_tv = self.tv(long), self.tv(short)
        original = Episode.from_db.__func__
        loaded = []

        def counting(cls, *args, **kwargs):
            loaded.append(1)
            return original(cls, *args, **kwargs)

        with mock.patch.object(Episode, "from_db", classmethod(counting)):
            library = {row["media"]["title"]: row["tracking"] for row in self.tracked(media_type="tv")}
            watching = self.tracked(media_type="tv", continue_watching="true")

        self.assertEqual(loaded, [])
        self.assertEqual(self.titles(watching), ["Short"])
        for title, tv in (("Long", long_tv), ("Short", short_tv)):
            self.assertEqual(library[title]["start_date"], tv.start_date)
            self.assertEqual(library[title]["end_date"], tv.end_date)
        self.assertIsNone(library["Long"]["start_date"])  # undated fills; the special doesn't count
        self.assertIsNotNone(library["Short"]["end_date"])

    def test_tv_801_a_show_stored_before_the_contract_waits_for_the_ship_repair(self):
        """TV-701 with TV-801 and TV-103: lists read stored values only.

        A Watching show stored before the contract has none until the repair works it
        out, so Continue Watching leaves it out and its Library progress has no max.
        The repair that ships with the contract (scripts/ios/SHIP.md) brings it back.
        """
        from io import StringIO

        from django.core.management import call_command

        show = self.make_show(title="Stored Before")
        tv = TV.objects.create(user=self.user, item=show, status="In progress")
        TV.objects.filter(pk=tv.pk).update(legacy_pending=True)
        season = Season.objects.create(
            item=Item.objects.create(
                media_id=show.media_id,
                source=show.source,
                media_type=MediaTypes.SEASON.value,
                season_number=1,
                title=show.title,
            ),
            user=self.user,
            related_tv=tv,
            status="In progress",
        )
        for number in (1, 2):
            Episode.objects.create(
                item=Item.objects.create(
                    media_id=show.media_id,
                    source=show.source,
                    media_type=MediaTypes.EPISODE.value,
                    season_number=1,
                    episode_number=number,
                    title=f"Episode {number}",
                ),
                related_season=season,
                end_date=datetime(2026, 10, number, 20, 0, tzinfo=UTC),
            )

        self.assertEqual(self.tracked(media_type="tv", continue_watching="true"), [])
        before = self.tracked(media_type="tv")[0]["tracking"]
        self.assertEqual(before["progress"], {"kind": "episodes", "value": 2, "max": None, "unit": "episode"})

        call_command("reconcile_tv", "--apply", stdout=StringIO(), stderr=StringIO())

        rows = self.tracked(media_type="tv", continue_watching="true")
        self.assertEqual(self.titles(rows), ["Stored Before"])
        up_next = rows[0]["tracking"]["tv"]["up_next"]
        self.assertEqual((up_next["season_number"], up_next["episode_number"]), (1, 3))
        self.assertEqual(rows[0]["tracking"]["progress"], {"kind": "episodes", "value": 2, "max": 18, "unit": "episode"})

    def test_tv_701_continue_watching_lists_shows_by_last_activity(self):
        older = self.make_show(title="Older")
        newer = self.make_show(title="Newer")
        paused = self.make_show(title="Paused")
        completed = self.make_show(title="Completed")
        caught_up = self.make_show(
            title="Caught up",
            seasons={1: [date(2026, 10, 1), date(2026, 10, 8), date(2026, 10, 29)]},
        )
        legacy = self.make_show(title="Legacy")
        self.tick(older, 1, 1)
        self.tick(paused, 1, 1)
        self.action(paused, "pause")
        self.action(completed, "mark_watched")
        self.ticks(caught_up, 1, [1, 2])
        self.tick(newer, 1, 1)
        self.tick(legacy, 1, 1)
        TV.objects.filter(user=self.user, item=legacy).update(legacy_pending=True)
        self.clock.advance(days=1)
        chosen = self.make_show(title="Chosen")
        self.action(chosen, "watching")

        results = self.tracked(media_type="tv", continue_watching="true", ordering="title")

        self.assertEqual(self.titles(results), ["Chosen", "Newer", "Older"])
        self.assertEqual(results[0]["tracking"]["tv"]["up_next"]["episode_number"], 1)

    def test_tv_701_continue_watching_names_up_next_and_sends_its_still(self):
        # Touch and hold on a Continue Watching card previews up next: its still and
        # title, from the stored listing (no provider calls; TV-207 bounds the reads).
        show = self.make_show(
            title="Stills",
            seasons={
                1: [
                    FakeEpisode(1, date(2026, 1, 5), "One", still_path="/one.jpg"),
                    FakeEpisode(2, date(2026, 1, 12), "Two", still_path="/two.jpg"),
                    FakeEpisode(3, date(2026, 1, 19), "Three"),
                ],
            },
        )
        self.tick(show, 1, 1)
        calls = self.fake.count()

        up_next = self.tracked(media_type="tv", continue_watching="true")[0]["tracking"]["tv"]["up_next"]

        self.assertEqual(
            (up_next["episode_number"], up_next["title"], up_next["image_url"]),
            (2, "Two", "https://image.tmdb.org/t/p/w500/two.jpg"),
        )
        self.assertEqual(self.fake.count(), calls)
        self.tick(show, 1, 2)
        up_next = self.tracked(media_type="tv", continue_watching="true")[0]["tracking"]["tv"]["up_next"]
        self.assertEqual((up_next["title"], up_next["image_url"]), ("Three", None), "E3 has no still")
        # Other library lists don't load it.
        self.assertIsNone(self.tracked(media_type="tv")[0]["tracking"]["tv"]["up_next"]["image_url"])

    def test_tv_900_home_season_list_has_only_started_unfinished_seasons(self):
        started = self.make_show(title="Started")
        finished = self.make_show(title="Finished")
        legacy = self.make_show(title="Legacy")
        self.tick(started, 1, 1)
        self.action(finished, "mark_watched", season_number=1)
        self.action(started, "rate", season_number=2, rating=7)
        self.tick(legacy, 1, 1)
        legacy_tv = self.tv(legacy)
        empty_item = Item.objects.create(
            media_id=legacy.media_id,
            source=legacy.source,
            media_type=MediaTypes.SEASON.value,
            season_number=2,
            title=legacy.title,
        )
        Season.objects.bulk_create(
            [Season(user=self.user, item=empty_item, related_tv=legacy_tv, status="In progress")],
        )

        results = self.tracked(media_type="season", status="In progress")

        self.assertCountEqual(
            [(row["media"]["ref"]["media_id"], row["media"]["ref"]["season_number"]) for row in results],
            [(started.media_id, 1), (legacy.media_id, 1)],
        )
        self.assertEqual({row["tracking"]["progress"]["max"] for row in results}, {10})

    def make_tracked_shows(self, count, start=0):
        for index in range(start, start + count):
            show = self.make_show(title=f"Show {index:02}", seasons={1: 4})
            self.tick(show, 1, 1)
        legacy = self.make_show(title=f"Legacy {start}", seasons={1: 4})
        self.tick(legacy, 1, 1)
        TV.objects.filter(user=self.user, item=legacy).update(legacy_pending=True)

    def list_queries(self, **params):
        with CaptureQueriesContext(connection) as captured:
            self.tracked(**params)
        return len(captured)

    def test_tv_207_list_reads_are_bounded_and_make_no_provider_calls(self):
        self.make_tracked_shows(3)
        calls = self.fake.count()
        few = self.list_queries(media_type="tv")
        few_continue = self.list_queries(media_type="tv", continue_watching="true")
        self.make_tracked_shows(27, start=3)
        calls_after_writes = self.fake.count()

        with self.assertNumQueries(few):
            many = self.tracked(media_type="tv")
        with self.assertNumQueries(few_continue):
            self.tracked(media_type="tv", continue_watching="true")

        self.assertEqual(len(many), 25)
        self.assertEqual(self.fake.count() - calls_after_writes, 0)
        self.assertGreater(calls_after_writes, calls)


class TVMediaDetailTests(TVAPITestCase):
    """GET media/... for shows, seasons and episodes (plan 2.3.7, Media detail)."""

    def test_tv_004_show_page_lists_specials_last_with_season_cards(self):
        show = self.make_show(seasons={0: 2, 1: 10, 2: 8})
        self.tick(show, 1, 1)

        response = self.detail(show)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        seasons = response.data["seasons"]
        self.assertEqual([season["season_number"] for season in seasons], [1, 2, 0])
        self.assertEqual(seasons[0]["completion"], {"completed_count": 1, "total_count": 10})
        self.assertEqual(seasons[0]["tv"]["state"], "in_progress")
        self.assertTrue(seasons[2]["tv"]["is_specials"])
        # TV-104: regular watched over aired; specials don't count.
        self.assertEqual(response.data["completion"], {"completed_count": 1, "total_count": 18})
        self.assertEqual(response.data["tv_unlisted_seasons"], [])
        state = response.data["user_state"]["tv"]
        self.assertEqual(state["watched_count"], 1)
        self.assertIn("composer", state)
        self.assertEqual(
            [target["id"] for target in state["composer"]["targets"]],
            ["season:1", "season:2", "season:0", "show"],
        )

    def test_tv_state_shows_for_an_untracked_show(self):
        show = self.make_show()

        state = self.detail(show).data["user_state"]["tv"]

        self.assertFalse(state["in_library"])
        self.assertEqual(state["eye"]["tap"], "mark_confirm")
        self.assertEqual(state["eye"]["prompt"], "Mark all 18 aired episodes watched?")

    def test_tv_104_season_page_carries_episode_states(self):
        show = self.make_show(seasons={1: [date(2020, 1, 6), date(2020, 1, 13), OCT_25 + timedelta(days=10)]})
        viewing_id = self.tick(show, 1, 1).data["episode"]["untick_viewing_id"]

        response = self.detail(show, 1)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        episodes = response.data["episodes"]
        self.assertTrue(episodes[0]["tv"]["watched"])
        self.assertEqual(episodes[0]["tv"]["untick_viewing_id"], viewing_id)
        self.assertTrue(episodes[1]["tv"]["can_tick"])
        self.assertFalse(episodes[2]["tv"]["aired"])
        self.assertFalse(episodes[2]["tv"]["can_tick"])
        self.assertEqual(response.data["completion"], {"completed_count": 1, "total_count": 2})
        self.assertEqual(response.data["user_state"]["tv"]["season"]["watched_count"], 1)
        self.assertEqual(response.data["tv_unlisted_episodes"], [])

    def test_tv_301_a_marked_season_page_takes_the_same_queries_however_long(self):
        """A season page's reads don't grow with its viewings (TV-301, A-47)."""
        short = self.make_show(seasons={1: 10})
        long = self.make_show(seasons={1: 40})
        for show in (short, long):
            self.action(show, "mark_watched", season_number=1)
        for show in (short, long):
            self.detail(show, 1)  # stores the listing and settles caches

        reads = {}
        for name, show in (("short", short), ("long", long)):
            with CaptureQueriesContext(connection) as captured:
                response = self.detail(show, 1)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            reads[name] = sum(1 for query in captured.captured_queries if query["sql"].startswith("SELECT"))

        self.assertEqual(reads["long"], reads["short"])

    def test_episode_page_carries_its_episode_state(self):
        show = self.make_show()
        self.tick(show, 1, 1)

        response = self.detail(show, 1, 1)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["user_state"]["is_tracked"])
        episode = response.data["user_state"]["tv"]["episode"]
        self.assertTrue(episode["watched"])
        self.assertEqual(episode["available_actions"], ["log_episode"])

    def test_tv_103_seasons_the_provider_stopped_listing(self):
        """A-62: a tracked season TMDB stops listing keeps its history, shown last."""
        show = self.make_show(seasons={1: 2, 2: 2, 3: 2})
        self.tick(show, 3, 1)
        self.fake.show(show.media_id).remove_season(3)
        self.clock.advance(days=2)
        cache.clear()

        show_page = self.detail(show)
        season_page = self.detail(show, 3)
        never_listed = self.detail(show, 4)
        stranger = self.client_for(make_user()).get(f"/api/v1/media/tmdb/tv/{show.media_id}/seasons/3/")

        self.assertEqual([season["season_number"] for season in show_page.data["seasons"]], [1, 2])
        unlisted = show_page.data["tv_unlisted_seasons"]
        self.assertEqual([(season["season_number"], season["title"]) for season in unlisted], [(3, "Season 3")])
        self.assertFalse(unlisted[0]["tv"]["listed"])
        self.assertEqual(season_page.status_code, status.HTTP_200_OK)
        self.assertEqual(season_page.data["title"], "Season 3")
        self.assertEqual(season_page.data["episodes"], [])
        self.assertIsNone(season_page.data["completion"])
        self.assertFalse(season_page.data["user_state"]["tv"]["season"]["listed"])
        unlisted_episode = season_page.data["tv_unlisted_episodes"][0]
        self.assertEqual(unlisted_episode["episode_number"], 1)
        self.assertFalse(unlisted_episode["tv"]["listed"])
        self.assertFalse(unlisted_episode["tv"]["can_tick"])
        self.assertIsNotNone(unlisted_episode["tv"]["untick_viewing_id"])
        self.assertEqual(never_listed.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(stranger.status_code, status.HTTP_404_NOT_FOUND)
        # Writes to the show still work.
        self.assertEqual(self.tick(show, 1, 1).status_code, status.HTTP_200_OK)

    def test_tv_103_first_open_of_a_long_show_makes_bounded_requests(self):
        """A-47: at most 1 + ceil(30/8) provider requests, none on a second open."""
        first_aired = date(1990, 1, 1)
        show = self.make_show(
            seasons={
                number: [first_aired + timedelta(days=60 * number), first_aired + timedelta(days=60 * number + 7)]
                for number in range(1, 31)
            },
        )

        first = self.detail(show)
        first_calls = self.fake.count()
        second = self.detail(show)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["user_state"]["tv"]["aired_count"], 60)
        self.assertLessEqual(first_calls, 1 + math.ceil(30 / 8))
        self.assertTrue(all(len(seasons) <= 8 for seasons in self.fake.season_requests()))
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertLessEqual(self.fake.count() - first_calls, 1)

    def test_tv_503_episode_community_counts_each_person_once(self):
        """A-34: two rated logs, then the newer one deleted."""
        show = self.make_show()
        self.log(show, 1, 1, consumed_at="2026-10-20", rating=6)
        newer = self.log(show, 1, 1, consumed_at="2026-10-24", rating=9, is_rewatch=True)
        friend = make_user()
        self.log(show, 1, 1, client=self.client_for(friend), rating=4)
        url = f"/api/v1/media/tmdb/episode/{show.media_id}/community/?season_number=1&episode_number=1"

        community = self.client.get(url)
        self.client.delete(f"/api/v1/diary/{newer.data['id']}/")
        after = self.client.get(url)
        episode = self.detail(show, 1, 1).data["user_state"]["tv"]["episode"]

        self.assertEqual(community.data["rating_count"], 2)
        self.assertEqual(float(community.data["average_rating"]), 6.5)
        self.assertEqual(community.data["diary_count"], 3)
        self.assertEqual(after.data["rating_count"], 2)
        self.assertEqual(float(after.data["average_rating"]), 5.0)
        self.assertEqual(episode["user_rating"], "6.0")


class TVDiaryEndpointTests(TVAPITestCase):
    """POST, PATCH and DELETE diary/ for TV logs, and their payload (TV-400 to 407)."""

    def test_tv_400_installed_build_season_log(self):
        """MediaLogView.swift:304-316: a datetime, auto_mark_consumed and 1-10 stars."""
        show = self.make_show()

        response = self.installed_build_log(show, 1, rating=8)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        # TV-407: 04:00 UTC is Oct 26 in New York but Oct 25 on the Pacific calendar.
        self.assertEqual(str(response.data["consumed_at"])[:10], OCT_25.isoformat())
        self.assertEqual(response.data["rating"], "8.0")
        self.assertIsNone(response.data["finished"])
        self.assertIsNone(response.data["earlier_unwatched"])
        self.assertEqual(response.data["tv"]["status"], "In progress")
        self.assertEqual(response.data["tv"]["up_next"]["season_number"], 2)
        self.assertEqual(len(self.viewings(show)), 10)
        season = Season.objects.get(user=self.user, item__season_number=1)
        self.assertEqual(str(season.score), "8.0")
        self.assertIsNone(self.tv(show).score)
        # TV-703: one diary activity, no second one from the API layer.
        self.assertEqual(Activity.objects.filter(actor=self.user, verb="diary_created").count(), 1)

    def test_tv_400_auto_mark_consumed_false_still_counts_the_log(self):
        show = self.make_show()

        response = self.installed_build_log(show, 1, 3, auto_mark_consumed=False)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.watched(show), {(1, 3)})

    def test_tv_407_future_log_dates_are_rejected(self):
        """A-21: from the new build, an installed build and an edit, with no change."""
        show = self.make_show()

        new_build = self.log(show, 1, consumed_at=OCT_26.isoformat())
        installed = self.installed_build_log(show, 1, consumed_at="2026-10-27T12:00:00Z")

        self.assert_error(new_build, 400, "validation_error", "Log dates can't be in the future.")
        self.assert_error(installed, 400, "validation_error", "Log dates can't be in the future.")
        self.assertIsNone(self.tv(show))
        self.assertFalse(DiaryEntry.objects.filter(user=self.user).exists())

        entry_id = self.log(show, 1, 1).data["id"]
        edit = self.client.patch(f"/api/v1/diary/{entry_id}/", {"consumed_at": OCT_26.isoformat()}, format="json")

        self.assert_error(edit, 400, "validation_error", "Log dates can't be in the future.")
        self.assertEqual(DiaryEntry.objects.get(pk=entry_id).consumed_at.date(), OCT_25)

    def test_tv_705_a_log_with_a_mutation_id_applies_once(self):
        show = self.make_show()
        mutation_id = str(uuid4())

        first = self.log(show, 2, 1, mutation_id=mutation_id)
        retry = self.log(show, 2, 1, mutation_id=mutation_id)

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(retry.data["id"], first.data["id"])
        self.assertEqual(DiaryEntry.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            first.data["earlier_unwatched"]["question"],
            "Also mark S1 E1–E10 watched?",  # noqa: RUF001
        )
        self.assertEqual(retry.data["earlier_unwatched"], first.data["earlier_unwatched"])

    def test_tv_705_a_log_from_its_page_returns_that_pages_state_and_composer(self):
        """TV-400, TV-404, TV-705: the page shows what the log counted, and defaults.

        An episode log from its episode page, and a season log from its season page,
        name their page; `tv` then carries that page's state and composer, and
        `season` and `episode` are the log target's own.
        """
        show = self.make_show()
        self.ticks(show, 1, [1, 2, 3])

        episode_log = self.log(show, 1, 5, page_season_number=1, page_episode_number=5)
        season_log = self.log(show, 1, page_season_number=1, is_rewatch=False)
        edited = self.client.patch(
            f"/api/v1/diary/{season_log.data['id']}/",
            {"rating": 8, "page_season_number": 1},
            format="json",
        )

        self.assertEqual(episode_log.status_code, status.HTTP_201_CREATED, episode_log.data)
        page = episode_log.data["tv"]["episode"]
        self.assertEqual((page["season_number"], page["episode_number"], page["watched"]), (1, 5, True))
        self.assertEqual(episode_log.data["episode"], page)
        self.assertEqual(episode_log.data["season"]["watched_count"], 4)
        target = episode_log.data["tv"]["composer"]["targets"][0]
        # The episode now has a log, so logging it again defaults to Rewatch (TV-404).
        self.assertEqual((target["id"], target["is_rewatch"]), ("episode:1:5", True))
        self.assertIsNone(episode_log.data["tv"]["season"])

        self.assertEqual(season_log.data["tv"]["season"]["state"], "watched")
        self.assertEqual(season_log.data["season"]["watched_count"], 10)
        self.assertIsNone(season_log.data["episode"])
        target = season_log.data["tv"]["composer"]["targets"][0]
        self.assertEqual((target["id"], target["is_rewatch"]), ("season:1", True))
        self.assertEqual(edited.data["tv"]["season"]["rating"], "8.0")
        self.assertEqual(edited.data["season"]["rating"], "8.0")

        # Without a page (the show page, the Diary) `tv` describes the show, as before.
        show_page = self.log(show, 2)
        self.assertIsNone(show_page.data["tv"]["season"])
        self.assertEqual(show_page.data["season"]["season_number"], 2)

    def test_tv_403_an_episode_log_that_finishes_a_season_offers_its_log(self):
        show = self.make_show()
        self.ticks(show, 1, range(1, 10))

        response = self.log(show, 1, 10, consumed_at="2026-10-24")

        self.assertEqual(response.data["finished"]["season_number"], 1)
        self.assertEqual(response.data["finished"]["composer"]["targets"][0]["date"], "2026-10-24")

    def test_tv_406_a_stale_delete_that_would_unmark_is_409(self):
        """A-16: tick E1-E3, log the season, then delete the log."""
        show = self.make_show()
        self.ticks(show, 1, [1, 2, 3])
        entry_id = self.log(show, 1).data["id"]
        tv_log = self.client.get(f"/api/v1/diary/{entry_id}/").data["tv_log"]

        stale = self.client.delete(
            f"/api/v1/diary/{entry_id}/?expected_version={tv_log['state_version'] - 1}&mutation_id={uuid4()}",
        )
        deleted = self.client.delete(
            f"/api/v1/diary/{entry_id}/?expected_version={tv_log['state_version']}&mutation_id={uuid4()}",
        )

        self.assertEqual(tv_log["level"], "season")
        self.assertEqual(tv_log["unmarks_count"], 7)
        self.assert_error(stale, 409, "tv_state_changed")
        self.assertEqual(stale.data["error"]["tv"]["count"], 7)
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.watched(show), {(1, 1), (1, 2), (1, 3)})
        self.assertEqual(self.tv(show).up_next_episode, 4)

    def test_tv_900_installed_builds_delete_logs_without_a_version(self):
        show = self.make_show()
        entry_id = self.installed_build_log(show, 1).data["id"]

        response = self.client.delete(f"/api/v1/diary/{entry_id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        # A-17: the only log under the show; it leaves the library.
        self.assertIsNone(self.tv(show))

    def test_tv_405_edit_response_adds_the_show_state(self):
        show = self.make_show()
        entry_id = self.log(show, 1, 1).data["id"]

        response = self.client.patch(f"/api/v1/diary/{entry_id}/", {"rating": 7}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["rating"], "7.0")
        self.assertEqual(response.data["tv"]["watched_count"], 1)

    def test_tv_702_the_diary_lists_every_tv_log_with_titles(self):
        show = self.make_show(title="Doctor Who")
        self.fake.show(show.media_id).seasons[1][3] = FakeEpisode(4, date(2020, 1, 27), "Fly")
        # Trakt and Simkl give episode Items the show's title.
        Item.objects.create(
            media_id=show.media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=4,
            title="Doctor Who",
        )
        self.log(show)
        self.log(show, 1)
        self.log(show, 1, 4)

        response = self.client.get(
            "/api/v1/diary/",
            {"media_type": ["tv", "season", "episode"]},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_type = {row["media"]["ref"]["media_type"]: row for row in response.data["results"]}
        self.assertEqual(set(by_type), {"tv", "season", "episode"})
        self.assertIsNone(by_type["tv"]["media"]["show_title"])
        self.assertEqual(by_type["season"]["media"]["show_title"], "Doctor Who")
        self.assertIsNone(by_type["season"]["media"]["episode_title"])
        self.assertEqual(by_type["episode"]["media"]["show_title"], "Doctor Who")
        self.assertEqual(by_type["episode"]["media"]["episode_title"], "Fly")
        # tv_log is worked out only for one log at a time: the log's own screen reads it
        # from GET diary/{id}/ (TV-207: a list never works out each show's viewings).
        self.assertEqual({row["tv_log"] for row in response.data["results"]}, {None})
        detail = {kind: self.client.get(f"/api/v1/diary/{row['id']}/").data for kind, row in by_type.items()}
        self.assertEqual(detail["episode"]["tv_log"]["level"], "episode")
        self.assertEqual(detail["season"]["tv_log"]["covers_count"], 10)

    def test_tv_log_is_for_the_owner_only(self):
        show = self.make_show()
        entry_id = self.log(show, 1, 1).data["id"]
        stranger = self.client_for(make_user())

        own = self.client.get(f"/api/v1/diary/{entry_id}/")
        theirs = stranger.get(f"/api/v1/diary/{entry_id}/")

        self.assertEqual(own.data["tv_log"]["level"], "episode")
        self.assertEqual(theirs.status_code, status.HTTP_200_OK)
        self.assertIsNone(theirs.data["tv_log"])

    def test_tv_702_diary_list_takes_the_same_queries_however_many_shows(self):
        """TV-207: a Diary page reads stored values only, whatever its TV logs."""
        shows = [self.make_show(title=f"Show {index}") for index in range(12)]
        self.log(shows[0], 1, 1)
        self.log(shows[1], 1)
        with CaptureQueriesContext(connection) as few:
            self.client.get("/api/v1/diary/", {"page_size": 100})
        for show in shows[2:]:
            self.log(show, 1, 2)
            self.log(show, 2)
        # A log from before the contract (unstamped, legacy show) costs nothing extra.
        DiaryEntry.objects.filter(user=self.user, item__media_id=shows[11].media_id).update(progress_snapshot=None)
        TV.objects.filter(user=self.user, item=shows[11]).update(legacy_pending=True)
        self.fake.reset_calls()

        with CaptureQueriesContext(connection) as many:
            response = self.client.get("/api/v1/diary/", {"page_size": 100})

        self.assertEqual(len(response.data["results"]), 22)
        self.assertEqual(len(many), len(few))
        self.assertEqual(self.fake.count(), 0)

    def test_tv_207_a_log_whose_show_item_is_missing_reads_without_the_provider(self):
        """The Diary and the log's screen never call TMDB, so an outage can't fail."""
        show = self.make_show()
        entry_id = self.log(show, 1, 1).data["id"]
        # A season or episode Item can exist without its show Item (imports, the web).
        TV.objects.filter(user=self.user, item=show).delete()
        Item.objects.filter(pk=show.pk).delete()
        self.fake.reset_calls()
        self.fake.fail()

        listed = self.client.get("/api/v1/diary/")
        detail = self.client.get(f"/api/v1/diary/{entry_id}/")

        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(len(listed.data["results"]), 1)
        self.assertIsNone(detail.data["tv_log"])
        self.assertEqual(self.fake.count(), 0)


class TVLikeEndpointTests(TVAPITestCase):
    """POST and DELETE me/liked-media/ for TV refs (TV-303 to TV-305)."""

    def test_tv_303_a_heart_on_an_unstarted_show_marks_it_watched(self):
        """A-28: the same request from the new build and an installed build."""
        show = self.make_show()

        response = self.like(show)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["liked"])
        state = response.data["media"]["user_state"]
        self.assertTrue(state["has_liked"])
        self.assertEqual(state["tv"]["status"], "Completed")
        self.assertEqual(state["tv"]["eye"]["tap"], "unmark_marks")
        self.assertIn("composer", state["tv"])
        self.assertEqual(response.data["tv"], state["tv"])
        self.assertEqual(self.tv(show).status, "Completed")
        self.assertEqual(len(self.viewings(show)), 18)

        undone = self.action(show, "unmark")

        self.assertFalse(undone.data["tv"]["in_library"])
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=show).exists())

    def test_tv_303_a_season_heart_asks_about_earlier_seasons(self):
        show = self.make_show()

        response = self.like(show, 2)

        self.assertEqual(response.data["earlier_unwatched"]["question"], "Also mark Season 1 watched?")
        self.assertEqual(response.data["season"]["season_number"], 2)
        self.assertTrue(response.data["season"]["liked"])
        self.assertEqual(response.data["media"]["user_state"]["tv"]["season"]["season_number"], 2)

    def test_tv_304_turning_a_heart_off_never_unmarks(self):
        show = self.make_show()
        self.like(show)

        response = self.like(show, liked=False)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data), {"liked", "tv", "season", "episode", "earlier_unwatched"})
        self.assertFalse(response.data["liked"])
        self.assertEqual(response.data["tv"]["status"], "Completed")
        self.assertEqual(len(self.viewings(show)), 18)

    def test_tv_305_an_episode_heart_marks_it_watched(self):
        """A-31: heart an unwatched episode, then unheart it."""
        show = self.make_show()

        liked = self.like(show, 1, 3)
        unliked = self.like(show, 1, 3, liked=False)

        self.assertTrue(liked.data["episode"]["watched"])
        self.assertTrue(liked.data["episode"]["liked"])
        self.assertTrue(unliked.data["episode"]["watched"])
        self.assertFalse(unliked.data["episode"]["liked"])
        self.assertEqual(self.watched(show), {(1, 3)})


class TVAppServicesTests(TVAPITestCase):
    """app.services routes TV to tv_tracking for the web's diary and likes (WEB)."""

    def season_item(self, show, number):
        item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=MediaTypes.SEASON.value,
            season_number=number,
            defaults={"title": show.title, "image": show.image},
        )
        return item

    def episode_item(self, show, season, episode):
        item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=MediaTypes.EPISODE.value,
            season_number=season,
            episode_number=episode,
            defaults={"title": f"Episode {episode}", "image": show.image},
        )
        return item

    def test_tv_703_a_web_log_posts_one_diary_activity(self):
        """T01 and TV-400: a web season log puts the show in the library and fills."""
        from app.services import create_diary_entry

        show = self.make_show()

        entry = create_diary_entry(
            self.user,
            self.season_item(show, 1),
            consumed_at=datetime(2026, 10, 24, 20, 0, tzinfo=UTC),
            rating="8.0",
            auto_mark_consumed=False,
        )

        self.assertEqual(entry.consumed_at, datetime(2026, 10, 24, tzinfo=UTC))
        self.assertEqual(self.tv(show).status, "In progress")
        self.assertEqual(len(self.viewings(show)), 10)
        self.assertEqual(
            list(Activity.objects.filter(actor=self.user).values_list("verb", flat=True)),
            ["diary_created"],
        )

    def test_tv_405_a_web_date_edit_moves_only_that_logs_viewing(self):
        """T04: editing one episode log's date rewrote every viewing of the episode."""
        from app.services import create_diary_entry, update_diary_entry

        show = self.make_show()
        self.tick(show, 1, 1)
        entry = create_diary_entry(
            self.user,
            self.episode_item(show, 1, 1),
            consumed_at=date(2026, 10, 20),
            is_rewatch=True,
        )

        update_diary_entry(entry, {"consumed_at": date(2026, 10, 22)})

        days = sorted(viewing.watched_on for viewing in self.viewings(show))
        self.assertEqual(days, [date(2026, 10, 22), OCT_25])

    def test_tv_406_a_web_log_deletion_keeps_independent_viewings(self):
        """T02: deleting the last season log deleted the season's tracking."""
        from app.services import create_diary_entry, delete_diary_entry

        show = self.make_show()
        self.ticks(show, 1, [1, 2])
        entry = create_diary_entry(self.user, self.season_item(show, 1), consumed_at=OCT_25)

        delete_diary_entry(self.user, entry)

        self.assertEqual(self.watched(show), {(1, 1), (1, 2)})
        self.assertFalse(Activity.objects.filter(actor=self.user, verb="diary_created").exists())

    def test_tv_303_a_web_heart_marks_an_unstarted_show(self):
        from app.services import set_media_like

        show = self.make_show()

        like = set_media_like(self.user, show, liked=True)

        self.assertEqual(like.item, show)
        self.assertEqual(self.tv(show).status, "Completed")
        self.assertFalse(DiaryEntry.objects.filter(user=self.user).exists())

    def test_tv_300_mark_consumed_marks_the_season(self):
        from app.services import mark_consumed

        show = self.make_show()
        self.tick(show, 1, 1)
        season = Season.objects.get(user=self.user, item__season_number=1)

        mark_consumed(self.user, season)

        season.refresh_from_db()
        self.assertEqual(season.status, "Completed")
        self.assertEqual(len(self.viewings(show)), 10)


@override_settings(TIME_ZONE="UTC")
class TVDiaryUTCServerTests(TVAPITestCase):
    """TV-407 under a UTC server: the user's calendar still decides a log's day."""

    def test_tv_407_the_users_calendar_decides_under_a_utc_server(self):
        show = self.make_show()

        installed = self.installed_build_log(show, 1)
        future = self.log(show, 2, consumed_at=OCT_26.isoformat())

        self.assertEqual(installed.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(installed.data["consumed_at"])[:10], OCT_25.isoformat())
        self.assert_error(future, 400, "validation_error", "Log dates can't be in the future.")


class TVDiaryServerCalendarTests(TVAPITestCase):
    """Until the app sends a calendar, the server's (New York) decides (Terms)."""

    def setUp(self):
        super().setUp()
        self.user = make_user(calendar="")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_tv_407_without_a_calendar_the_server_day_counts(self):
        show = self.make_show()

        installed = self.installed_build_log(show, 1)
        new_build = self.log(show, 2, consumed_at=OCT_26.isoformat())

        self.assertEqual(str(installed.data["consumed_at"])[:10], OCT_26.isoformat())
        self.assertEqual(new_build.status_code, status.HTTP_201_CREATED)
