from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from streaming.models import UserStreamingService
from streaming.payloads import where_to_watch
from streaming.tests.helpers import (
    APPLE,
    HULU,
    MAX,
    NETFLIX,
    TITLE_RESULTS,
    US_LINK,
    make_provider,
    make_snapshot,
    reset_streaming_state,
)

METADATA = {"title": "Dune: Part Two", "providers": TITLE_RESULTS}


def _provider_ids(payload, group_type):
    group = next(group for group in payload["groups"] if group["type"] == group_type)
    return [provider["provider_id"] for provider in group["providers"]]


class WhereToWatchTests(TestCase):
    """ST-200/201/202 detail payload."""

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

    def test_groups_follow_contract_order_and_shape(self):
        payload = self._build()

        self.assertEqual(payload["region"], "US")
        self.assertEqual(payload["region_name"], "United States")
        self.assertEqual(payload["link"], US_LINK)
        self.assertFalse(payload["has_services"])
        self.assertFalse(payload["on_my_services"])
        self.assertEqual(payload["attribution"], {"name": "JustWatch", "url": "https://www.justwatch.com"})
        self.assertEqual(
            [(group["type"], group["label"]) for group in payload["groups"]],
            [
                ("flatrate", "Stream"),
                ("free", "Free"),
                ("ads", "Free with ads"),
                ("rent", "Rent"),
                ("buy", "Buy"),
            ],
        )
        self.assertEqual(
            payload["groups"][0]["providers"][0],
            {
                "provider_id": 8,
                "name": "Netflix",
                "logo_url": "https://image.tmdb.org/t/p/w154/netflix.png",
                "display_priority": 1,
                "is_mine": False,
            },
        )
        self.assertEqual(_provider_ids(payload, "flatrate"), [8, 1899])
        self.assertEqual(_provider_ids(payload, "rent"), [10, 2])

    def test_my_services_come_first_and_are_marked(self):
        self._subscribe(MAX)

        payload = self._build()

        flatrate = payload["groups"][0]["providers"]
        self.assertEqual([provider["provider_id"] for provider in flatrate], [1899, 8])
        self.assertEqual([provider["is_mine"] for provider in flatrate], [True, False])
        self.assertTrue(payload["has_services"])
        self.assertTrue(payload["on_my_services"])

    def test_rent_and_buy_never_count_as_on_my_services(self):
        self._subscribe(APPLE, HULU)

        payload = self._build()

        self.assertTrue(payload["has_services"])
        self.assertFalse(payload["on_my_services"])
        self.assertEqual(_provider_ids(payload, "rent"), [2, 10])
        self.assertTrue(payload["groups"][3]["providers"][0]["is_mine"])

    def test_unavailable_title_returns_empty_groups(self):
        payload = self._build(metadata={"providers": {"GB": TITLE_RESULTS["GB"]}})

        self.assertEqual(payload["groups"], [])
        self.assertIsNone(payload["link"])
        self.assertFalse(payload["on_my_services"])
        self.assertEqual(self._build(metadata={})["groups"], [])

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

        self.assertEqual([group["type"] for group in payload["groups"]], ["flatrate"])
        self.assertEqual(_provider_ids(payload, "flatrate"), [15])
        self.assertEqual(payload["link"], "https://example.com/us")

    def test_stale_snapshot_falls_back_to_metadata(self):
        make_snapshot("movie", "693134", "US", [(HULU, "flatrate")], age=timedelta(hours=25))

        self.assertEqual(_provider_ids(self._build(), "flatrate"), [8, 1899])

    def test_season_uses_its_own_metadata(self):
        make_snapshot("tv", "693134", "US", [(HULU, "flatrate")])
        season_metadata = {"providers": {"US": {"flatrate": [NETFLIX]}}}

        payload = self._build(media_type="season", metadata=season_metadata)

        self.assertEqual(_provider_ids(payload, "flatrate"), [8])
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
        self.assertFalse(payload["has_services"])
        self.assertEqual(_provider_ids(payload, "flatrate"), [8])

    def test_saved_region_and_unknown_region_name(self):
        self.user.watch_provider_region = "JP"
        self.user.save(update_fields=["watch_provider_region"])

        payload = self._build(metadata={"providers": {"JP": {"flatrate": [NETFLIX]}}})

        self.assertEqual(payload["region"], "JP")
        self.assertEqual(payload["region_name"], "JP")

    def test_snapshot_payload_query_count_is_constant(self):
        self._subscribe(NETFLIX)
        make_snapshot("movie", "693134", "US", [(NETFLIX, "flatrate"), (HULU, "flatrate"), (APPLE, "rent")])

        with self.assertNumQueries(3):
            payload = self._build()

        self.assertEqual(_provider_ids(payload, "flatrate"), [8, 15])
