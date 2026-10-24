from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from streaming.models import UserStreamingService
from streaming.payloads import where_to_watch
from streaming.tests.helpers import (
    AMAZON,
    APPLE,
    HULU,
    MAX,
    NETFLIX,
    TITLE_RESULTS,
    TUBI,
    US_LINK,
    make_provider,
    make_snapshot,
    reset_streaming_state,
)

METADATA = {"title": "Dune: Part Two", "providers": TITLE_RESULTS}


def _provider_ids(payload):
    return [provider["provider_id"] for provider in payload["providers"]]


class WhereToWatchTests(TestCase):
    """ST-200/201 detail payload."""

    def setUp(self):
        reset_streaming_state(self)
        self.user = get_user_model().objects.create_user(username="viewer", password="pw-12345678")

    def _build(self, media_type="movie", metadata=METADATA, user=None, request=None, source="tmdb"):
        return where_to_watch(
            source=source,
            media_type=media_type,
            media_id="693134",
            metadata=metadata,
            user=self.user if user is None else user,
            request=request,
        )

    def _subscribe(self, *entries):
        for entry in entries:
            provider = make_provider(entry)
            UserStreamingService.objects.create(user=self.user, provider=provider)

    def test_lists_major_services_in_contract_shape(self):
        payload = self._build()

        self.assertEqual(set(payload), {"region", "region_name", "link", "providers", "attribution"})
        self.assertEqual(payload["region"], "US")
        self.assertEqual(payload["region_name"], "United States")
        self.assertEqual(payload["link"], US_LINK)
        self.assertEqual(payload["attribution"], {"name": "JustWatch", "url": "https://www.justwatch.com"})
        self.assertEqual(
            payload["providers"][0],
            {
                "provider_id": 8,
                "name": "Netflix",
                "logo_url": "https://image.tmdb.org/t/p/w154/netflix.png",
                "display_priority": 1,
                "is_mine": False,
            },
        )
        # Kanopy and Tubi stream it too, but they are neither major nor the viewer's.
        self.assertEqual(_provider_ids(payload), [8, 1899])

    def test_my_services_come_first_and_are_marked(self):
        self._subscribe(MAX)

        payload = self._build()

        self.assertEqual(_provider_ids(payload), [1899, 8])
        self.assertEqual([provider["is_mine"] for provider in payload["providers"]], [True, False])

    def test_other_services_show_only_when_they_are_mine(self):
        self._subscribe(TUBI)

        payload = self._build()

        self.assertEqual(_provider_ids(payload), [73, 8, 1899])
        self.assertTrue(payload["providers"][0]["is_mine"])

    def test_a_service_streaming_several_ways_shows_once(self):
        metadata = {"providers": {"US": {"flatrate": [NETFLIX], "ads": [HULU, NETFLIX]}}}

        self.assertEqual(_provider_ids(self._build(metadata=metadata)), [8, 15])

    def test_rent_and_buy_are_not_shown(self):
        self._subscribe(APPLE, AMAZON)

        self.assertEqual(_provider_ids(self._build()), [8, 1899])

    def test_title_only_to_rent_or_buy_shows_no_services(self):
        self.user.watch_provider_region = "FR"
        self.user.save(update_fields=["watch_provider_region"])

        payload = self._build()

        self.assertEqual(payload["providers"], [])
        self.assertEqual(payload["link"], TITLE_RESULTS["FR"]["link"])

    def test_unavailable_title_returns_no_services(self):
        payload = self._build(metadata={"providers": {"GB": TITLE_RESULTS["GB"]}})

        self.assertEqual(payload["providers"], [])
        self.assertIsNone(payload["link"])
        self.assertEqual(self._build(metadata={})["providers"], [])

    def test_null_when_disabled_or_unsupported(self):
        self.assertIsNone(self._build(source="mal", media_type="anime"))
        self.assertIsNone(self._build(source="hardcover", media_type="book"))
        self.assertIsNone(self._build(media_type="episode"))
        self.assertIsNone(self._build(media_type="book"))
        self.user.watch_provider_region = ""
        self.user.save(update_fields=["watch_provider_region"])
        self.assertIsNone(self._build())

    def test_fresh_snapshot_wins_over_metadata(self):
        make_snapshot("movie", "693134", "US", [(HULU, "flatrate")], link="https://example.com/us")

        payload = self._build()

        self.assertEqual(_provider_ids(payload), [15])
        self.assertEqual(payload["link"], "https://example.com/us")

    def test_stale_snapshot_falls_back_to_metadata(self):
        make_snapshot("movie", "693134", "US", [(HULU, "flatrate")], age=timedelta(hours=25))

        self.assertEqual(_provider_ids(self._build()), [8, 1899])

    def test_season_uses_its_own_metadata(self):
        make_snapshot("tv", "693134", "US", [(HULU, "flatrate")])
        season_metadata = {"providers": {"US": {"flatrate": [NETFLIX]}}}

        payload = self._build(media_type="season", metadata=season_metadata)

        self.assertEqual(_provider_ids(payload), [8])
        self.assertIsNone(payload["link"])

    def test_anonymous_viewer_uses_header_region_without_services(self):
        request = RequestFactory().get("/", HTTP_X_SPINE_REGION="gb")

        payload = where_to_watch(
            source="tmdb",
            media_type="movie",
            media_id="693134",
            metadata=METADATA,
            user=None,
            request=request,
        )

        self.assertEqual(payload["region"], "GB")
        self.assertEqual(payload["region_name"], "United Kingdom")
        self.assertEqual(_provider_ids(payload), [8])
        self.assertFalse(payload["providers"][0]["is_mine"])

    def test_saved_region_and_unknown_region_name(self):
        self.user.watch_provider_region = "XX"
        self.user.save(update_fields=["watch_provider_region"])

        payload = self._build(metadata={"providers": {"XX": {"flatrate": [NETFLIX]}}})

        self.assertEqual(payload["region"], "XX")
        self.assertEqual(payload["region_name"], "XX")

    def test_snapshot_payload_query_count_is_constant(self):
        self._subscribe(NETFLIX, APPLE)
        make_snapshot("movie", "693134", "US", [(NETFLIX, "flatrate"), (HULU, "flatrate"), (APPLE, "rent")])

        with self.assertNumQueries(3):
            payload = self._build()

        # A stored rent offer stays hidden even on one of the viewer's services.
        self.assertEqual(_provider_ids(payload), [8, 15])
