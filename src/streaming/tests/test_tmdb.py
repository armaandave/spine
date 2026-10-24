from unittest.mock import patch

import requests
from django.test import TestCase, override_settings

from app.providers.services import ProviderAPIError
from streaming import tmdb
from streaming.models import StreamingProvider
from streaming.tests.helpers import (
    NETFLIX,
    TITLE_RESULTS,
    make_provider,
    reset_streaming_state,
)


def _catalog_entry(provider_id, name, priorities, priority=0, logo="/logo.png"):
    return {
        "provider_id": provider_id,
        "provider_name": name,
        "logo_path": logo,
        "display_priority": priority,
        "display_priorities": priorities,
    }


def _http_error(status_code):
    response = requests.Response()
    response.status_code = status_code
    response._content = b'{"status_message": "nope"}'
    return requests.exceptions.HTTPError(response=response)


class FetchTitleProvidersTests(TestCase):
    """Per-title watch-provider requests."""

    @patch("streaming.tmdb.provider_services.api_request")
    def test_returns_results_from_the_title_endpoint(self, request_mock):
        request_mock.return_value = {"id": 603, "results": TITLE_RESULTS}

        results = tmdb.fetch_title_providers("movie", "603")

        self.assertEqual(results, TITLE_RESULTS)
        args = request_mock.call_args.args
        self.assertEqual(args[:2], ("tmdb", "GET"))
        self.assertEqual(args[2], "https://api.themoviedb.org/3/movie/603/watch/providers")

    @patch("streaming.tmdb.provider_services.api_request")
    def test_rejects_unknown_title_types_and_wraps_http_errors(self, request_mock):
        with self.assertRaises(ValueError):
            tmdb.fetch_title_providers("season", "1")
        request_mock.assert_not_called()

        request_mock.side_effect = _http_error(404)
        with self.assertRaises(ProviderAPIError) as raised:
            tmdb.fetch_title_providers("tv", "999999999")
        self.assertEqual(raised.exception.status_code, 404)


class ParsingTests(TestCase):
    """TMDB payload parsing."""

    def test_parse_offers_keeps_streaming_types_and_skips_rent_buy_and_unknown_keys(self):
        offers = tmdb.parse_offers(TITLE_RESULTS["US"])

        self.assertEqual(
            [(offer.monetization, offer.provider_id) for offer in offers],
            [
                ("flatrate", 1899),
                ("flatrate", 8),
                ("free", 191),
                ("ads", 73),
            ],
        )
        self.assertEqual(offers[0].name, "HBO Max")
        self.assertEqual(offers[0].display_priority, 151)

    def test_parse_offers_ignores_malformed_entries(self):
        offers = tmdb.parse_offers(
            {
                "flatrate": [{"provider_name": "No ID"}, {"provider_id": 0}, "junk", NETFLIX, NETFLIX],
                "free": "not-a-list",
            },
        )

        self.assertEqual([offer.provider_id for offer in offers], [8])
        self.assertEqual(tmdb.parse_offers(None), [])
        self.assertEqual(tmdb.region_link(None), "")
        self.assertEqual(tmdb.region_link({"link": "x" * 501}), "")


class ProviderCatalogTests(TestCase):
    """Merged per-region provider catalogs."""

    def setUp(self):
        reset_streaming_state(self)

    def _responses(self):
        movie = {
            "results": [
                _catalog_entry(8, "Netflix", {"US": 1, "GB": 2}),
                _catalog_entry(337, "Disney Plus", {"US": 3}, priority=0),
                _catalog_entry(2, "Apple TV Store", {}, priority=7),
            ],
        }
        tv = {
            "results": [
                _catalog_entry(8, "Netflix", {"US": 1, "GB": 2}),
                _catalog_entry(15, "Hulu", {"US": 2}),
                {"provider_name": "No ID"},
            ],
        }
        return [movie, tv]

    @patch("streaming.tmdb.provider_services.api_request")
    def test_merges_dedupes_and_orders_by_region_priority(self, request_mock):
        request_mock.side_effect = self._responses()

        catalog = tmdb.provider_catalog("us")

        self.assertEqual([entry["provider_id"] for entry in catalog], [8, 15, 337, 2])
        self.assertEqual(catalog[0], {"provider_id": 8, "name": "Netflix", "logo_path": "/logo.png", "display_priority": 1})
        self.assertEqual(catalog[3]["display_priority"], 7)
        urls = [call.args[2] for call in request_mock.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://api.themoviedb.org/3/watch/providers/movie",
                "https://api.themoviedb.org/3/watch/providers/tv",
            ],
        )
        self.assertEqual(request_mock.call_args.kwargs["params"]["watch_region"], "US")
        self.assertEqual(
            set(StreamingProvider.objects.values_list("id", flat=True)),
            {2, 8, 15, 337},
        )

        request_mock.reset_mock()
        self.assertEqual(tmdb.provider_catalog("US"), catalog)
        request_mock.assert_not_called()

    @override_settings(STREAMING_DEFAULT_REGION="US")
    @patch("streaming.tmdb.provider_services.api_request")
    def test_only_the_default_region_overwrites_stored_priority(self, request_mock):
        make_provider(NETFLIX, display_priority=40)
        request_mock.side_effect = [*self._responses(), *self._responses()]

        tmdb.provider_catalog("GB")
        netflix = StreamingProvider.objects.get(pk=8)
        self.assertEqual(netflix.display_priority, 40)
        self.assertEqual(netflix.logo_path, "/logo.png")

        tmdb.provider_catalog("US")
        netflix.refresh_from_db()
        self.assertEqual(netflix.display_priority, 1)

    def test_rejects_invalid_region(self):
        with self.assertRaises(ValueError):
            tmdb.provider_catalog("USA")

    def test_upsert_keeps_names_when_payload_omits_them(self):
        make_provider(NETFLIX)

        tmdb.upsert_providers(
            [
                {"provider_id": 8, "name": "", "logo_path": "", "display_priority": 99},
                {"provider_id": 500, "name": "", "logo_path": "", "display_priority": 3},
            ],
        )

        netflix = StreamingProvider.objects.get(pk=8)
        self.assertEqual((netflix.name, netflix.logo_path, netflix.display_priority), ("Netflix", "/netflix.png", 1))
        self.assertEqual(StreamingProvider.objects.get(pk=500).name, "500")

    def test_upsert_skips_writes_when_nothing_changes(self):
        make_provider(NETFLIX)

        with self.assertNumQueries(1):
            tmdb.upsert_providers([tmdb.parse_provider(NETFLIX)])
