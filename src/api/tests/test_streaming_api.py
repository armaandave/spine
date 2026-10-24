from copy import deepcopy
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from redis.exceptions import RedisError
from rest_framework import status
from rest_framework.test import APIClient

from api.services.media import watch_providers_for_user
from streaming.models import StreamingAvailability, UserStreamingService
from streaming.services import active_regions
from streaming.tests.helpers import (
    HULU,
    MAX,
    NETFLIX,
    TITLE_RESULTS,
    make_provider,
    make_snapshot,
    reset_streaming_state,
)

CATALOGS = {
    "US": [(8, "Netflix", 1), (15, "Hulu", 2), (337, "Disney Plus", 3)],
    "GB": [(39, "NOW", 1), (8, "Netflix", 2)],
}


def _catalog_response(provider, method, url, params=None, **kwargs):
    region = params["watch_region"]
    return {
        "results": [
            {
                "provider_id": provider_id,
                "provider_name": name,
                "logo_path": f"/{provider_id}.png",
                "display_priority": 0,
                "display_priorities": {region: priority},
            }
            for provider_id, name, priority in CATALOGS.get(region, [])
        ],
    }


class StreamingSettingsApiTests(TestCase):
    """Regions, catalogs, and the user's streaming settings."""

    def setUp(self):
        reset_streaming_state(self)
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="streamer", password="strong-password-123")
        self.client.force_authenticate(self.user)
        request_patch = patch("streaming.tmdb.provider_services.api_request", side_effect=_catalog_response)
        self.request_mock = request_patch.start()
        self.addCleanup(request_patch.stop)

    def _select(self, *entries):
        for entry in entries:
            provider = make_provider(entry)
            UserStreamingService.objects.create(user=self.user, provider=provider)

    def _put(self, payload):
        with (
            patch("streaming.tasks.check_user_streaming_library.delay") as check_mock,
            self.captureOnCommitCallbacks(execute=True) as callbacks,
        ):
            response = self.client.put("/api/v1/me/streaming/", payload, format="json")
        return response, check_mock, callbacks

    def test_endpoints_require_authentication(self):
        anonymous = APIClient()

        for url in ("/api/v1/streaming/regions/", "/api/v1/streaming/providers/", "/api/v1/me/streaming/"):
            self.assertIn(anonymous.get(url).status_code, {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})

    def test_regions_are_sorted_by_name(self):
        response = self.client.get("/api/v1/streaming/regions/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["results"],
            [
                {"code": "CA", "name": "Canada"},
                {"code": "FR", "name": "France"},
                {"code": "DE", "name": "Germany"},
                {"code": "GB", "name": "United Kingdom"},
                {"code": "US", "name": "United States"},
            ],
        )

    def test_providers_default_to_the_effective_region(self):
        response = self.client.get("/api/v1/streaming/providers/", HTTP_X_SPINE_REGION="gb")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["region"], "GB")
        self.assertEqual(
            response.data["results"],
            [
                {"provider_id": 39, "name": "NOW", "logo_url": "https://image.tmdb.org/t/p/w154/39.png", "display_priority": 1},
                {"provider_id": 8, "name": "Netflix", "logo_url": "https://image.tmdb.org/t/p/w154/8.png", "display_priority": 2},
            ],
        )

    def test_providers_for_an_explicit_or_unknown_region(self):
        self.user.watch_provider_region = ""
        self.user.save(update_fields=["watch_provider_region"])

        explicit = self.client.get("/api/v1/streaming/providers/?region=us")
        default = self.client.get("/api/v1/streaming/providers/")
        unknown = self.client.get("/api/v1/streaming/providers/?region=ZZ")
        malformed = self.client.get("/api/v1/streaming/providers/?region=USA")

        self.assertEqual(explicit.data["region"], "US")
        self.assertEqual([entry["provider_id"] for entry in explicit.data["results"]], [8, 15, 337])
        self.assertEqual(default.data["region"], "US")
        self.assertEqual(unknown.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(unknown.data, {"region": ["Unknown region."]})
        self.assertEqual(malformed.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.request_mock.call_count, 2)

    def test_get_settings_defaults(self):
        response = self.client.get("/api/v1/me/streaming/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "region": "US",
                "region_name": "United States",
                "region_is_set": False,
                "enabled": True,
                "provider_ids": [],
                "providers": [],
                "attribution": {"name": "JustWatch", "url": "https://www.justwatch.com"},
            },
        )

    def test_get_settings_header_region_selection_and_disabled(self):
        self._select(HULU, NETFLIX)

        from_header = self.client.get("/api/v1/me/streaming/", HTTP_X_SPINE_REGION="FR")
        self.user.watch_provider_region = ""
        self.user.save(update_fields=["watch_provider_region"])
        disabled = self.client.get("/api/v1/me/streaming/", HTTP_X_SPINE_REGION="FR")

        self.assertEqual((from_header.data["region"], from_header.data["region_name"]), ("FR", "France"))
        self.assertFalse(from_header.data["region_is_set"])
        self.assertEqual(from_header.data["provider_ids"], [8, 15])
        self.assertEqual(
            from_header.data["providers"][0],
            {"provider_id": 8, "name": "Netflix", "logo_url": "https://image.tmdb.org/t/p/w154/netflix.png", "display_priority": 1},
        )
        self.assertIsNone(disabled.data["region"])
        self.assertIsNone(disabled.data["region_name"])
        self.assertFalse(disabled.data["enabled"])
        self.assertEqual(disabled.data["provider_ids"], [8, 15])

    def test_put_replaces_selection_saves_region_and_queues_a_library_check(self):
        self._select(HULU)
        self.assertEqual(active_regions(), {"US"})

        response, check_mock, callbacks = self._put({"region": "gb", "provider_ids": [8, 39]})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["region"], "GB")
        self.assertTrue(response.data["region_is_set"])
        self.assertTrue(response.data["enabled"])
        self.assertEqual(set(response.data["provider_ids"]), {8, 39})
        self.user.refresh_from_db()
        self.assertEqual(self.user.watch_provider_region, "GB")
        self.assertEqual(
            set(UserStreamingService.objects.filter(user=self.user).values_list("provider_id", flat=True)),
            {8, 39},
        )
        self.assertEqual(active_regions(), {"GB", "US"})
        self.assertEqual(len(callbacks), 2)
        check_mock.assert_called_once_with(self.user.pk)

    def test_library_check_runs_after_commit_with_fresh_active_regions(self):
        self.assertEqual(active_regions(), {"US"})
        regions_seen_by_check = []

        with patch("streaming.tasks.check_user_streaming_library.delay") as check_mock:
            check_mock.side_effect = lambda _user_id: regions_seen_by_check.append(active_regions())
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.put("/api/v1/me/streaming/", {"region": "GB", "provider_ids": [8]}, format="json")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            check_mock.assert_not_called()

            for callback in callbacks:
                callback()

        check_mock.assert_called_once_with(self.user.pk)
        self.assertEqual(regions_seen_by_check, [{"GB", "US"}])

    def test_repeated_saves_queue_one_library_check_per_country_per_minute(self):
        first, first_check, _callbacks = self._put({"region": "US", "provider_ids": [8]})
        repeat, repeat_check, _callbacks = self._put({"region": "US", "provider_ids": [8, 15]})
        moved, moved_check, _callbacks = self._put({"region": "GB", "provider_ids": [8]})

        self.assertEqual(
            [response.status_code for response in (first, repeat, moved)],
            [status.HTTP_200_OK] * 3,
        )
        first_check.assert_called_once_with(self.user.pk)
        repeat_check.assert_not_called()
        moved_check.assert_called_once_with(self.user.pk)
        self.assertEqual(repeat.data["provider_ids"], [8, 15])

    def test_saving_the_first_services_queues_a_library_check(self):
        empty, empty_check, _callbacks = self._put({"region": "GB", "provider_ids": []})
        first, first_check, _callbacks = self._put({"region": "GB", "provider_ids": [39]})

        self.assertEqual([empty.status_code, first.status_code], [status.HTTP_200_OK] * 2)
        empty_check.assert_not_called()
        first_check.assert_called_once_with(self.user.pk)

    def test_bundled_regions_need_no_tmdb_region_list(self):
        with patch("api.views.streaming.services.region_names", return_value={}):
            saved, _check_mock, _callbacks = self._put({"region": "GB", "provider_ids": []})
            catalog = self.client.get("/api/v1/streaming/providers/", {"region": "GB"})
            unknown = self.client.get("/api/v1/streaming/providers/", {"region": "XX"})

        self.assertEqual(saved.status_code, status.HTTP_200_OK)
        self.assertEqual(saved.data["region"], "GB")
        self.assertEqual([provider["provider_id"] for provider in catalog.data["results"]], [39, 8])
        self.assertEqual(unknown.status_code, status.HTTP_400_BAD_REQUEST)

    def test_library_check_is_queued_when_the_debounce_cache_fails(self):
        with (
            patch("api.views.streaming.cache.add", side_effect=RedisError("down")),
            self.assertLogs("api.views.streaming", level="WARNING") as logs,
        ):
            response, check_mock, _callbacks = self._put({"region": "US", "provider_ids": [8]})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        check_mock.assert_called_once_with(self.user.pk)
        self.assertIn("type=RedisError", logs.output[0])

    def test_country_change_keeps_services_missing_from_the_new_catalog(self):
        self._select(HULU)

        response, _check_mock, _callbacks = self._put({"region": "GB", "provider_ids": [15, 8]})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(set(response.data["provider_ids"]), {8, 15})

    def test_removing_services_does_not_fetch_the_catalog(self):
        self._select(NETFLIX, HULU)

        response, _check_mock, _callbacks = self._put({"region": "US", "provider_ids": [15]})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["provider_ids"], [15])
        self.request_mock.assert_not_called()

    def test_put_validation(self):
        self._select(NETFLIX)
        cases = [
            ({}, {"region", "provider_ids"}),
            ({"region": "ZZ", "provider_ids": []}, {"region"}),
            ({"region": "", "provider_ids": []}, {"region"}),
            ({"region": "US", "provider_ids": [8, 8]}, {"provider_ids"}),
            ({"region": "US", "provider_ids": [0]}, {"provider_ids"}),
            ({"region": "US", "provider_ids": ["netflix"]}, {"provider_ids"}),
            ({"region": "US", "provider_ids": 8}, {"provider_ids"}),
            ({"region": "US", "provider_ids": list(range(1, 102))}, {"provider_ids"}),
            ({"region": "US", "provider_ids": [8, 999]}, {"provider_ids"}),
        ]

        for payload, fields in cases:
            with self.subTest(payload=payload):
                response, check_mock, _callbacks = self._put(payload)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(set(response.data), fields)
                check_mock.assert_not_called()

        self.assertIn("999", response.data["provider_ids"][0])
        self.user.refresh_from_db()
        self.assertEqual(self.user.watch_provider_region, "UNSET")
        self.assertEqual(list(UserStreamingService.objects.values_list("provider_id", flat=True)), [8])

    def test_demo_accounts_are_view_only(self):
        self.user.is_demo = True
        self.user.save(update_fields=["is_demo"])

        response, check_mock, _callbacks = self._put({"region": "US", "provider_ids": [8]})

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(UserStreamingService.objects.exists())
        check_mock.assert_not_called()
        self.assertEqual(self.client.get("/api/v1/me/streaming/").status_code, status.HTTP_200_OK)


def _movie_metadata(media_id, providers=TITLE_RESULTS):
    return {
        "media_id": media_id,
        "media_type": "movie",
        "source": "tmdb",
        "title": f"Movie {media_id}",
        "image": "https://example.com/movie.jpg",
        "providers": deepcopy(providers),
    }


@patch("app.providers.tmdb.get_title_logo", return_value=None)
@patch("app.providers.tmdb.get_backdrop_images", return_value=[])
@patch("app.providers.tmdb.get_season_backdrop_images", return_value=[])
@patch("app.providers.mdblist.get_media_ratings", return_value={})
class WhereToWatchDetailApiTests(TestCase):
    """``where_to_watch`` on media detail (ST-200, ST-400)."""

    def setUp(self):
        reset_streaming_state(self)
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="watcher", password="strong-password-123")
        self.user.watch_provider_region = "US"
        self.user.save(update_fields=["watch_provider_region"])
        UserStreamingService.objects.create(user=self.user, provider=make_provider(NETFLIX))
        self.client.force_authenticate(self.user)
        metadata_patch = patch("api.services.media.provider_services.get_media_metadata")
        self.metadata_mock = metadata_patch.start()
        self.addCleanup(metadata_patch.stop)
        http_patch = patch("app.providers.services.api_request", side_effect=AssertionError("unexpected TMDB request"))
        self.http_mock = http_patch.start()
        self.addCleanup(http_patch.stop)
        fetch_patch = patch("streaming.sync.tmdb.fetch_title_providers", side_effect=AssertionError("unexpected fetch"))
        self.fetch_mock = fetch_patch.start()
        self.addCleanup(fetch_patch.stop)

    def _assert_no_provider_requests(self):
        self.http_mock.assert_not_called()
        self.fetch_mock.assert_not_called()

    def test_movie_detail_adds_where_to_watch_without_extra_requests(self, *_mocks):
        self.metadata_mock.return_value = _movie_metadata("693134")

        response = self.client.get("/api/v1/media/tmdb/movie/693134/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        where = response.data["where_to_watch"]
        self.assertEqual((where["region"], where["region_name"]), ("US", "United States"))
        self.assertEqual([provider["provider_id"] for provider in where["providers"]], [8, 1899])
        self.assertTrue(where["providers"][0]["is_mine"])
        self.assertEqual(where["attribution"]["name"], "JustWatch")
        self.assertEqual(
            response.data["providers"],
            watch_providers_for_user(_movie_metadata("693134"), self.user),
        )
        self.assertEqual(
            set(StreamingAvailability.objects.values_list("title_type", "tmdb_id", "region")),
            {("movie", "693134", "US")},
        )
        self._assert_no_provider_requests()

    def test_tv_detail_adds_where_to_watch_and_stores_the_show(self, *_mocks):
        self.metadata_mock.return_value = {
            **_movie_metadata("1396"),
            "media_type": "tv",
            "related": {"seasons": []},
        }

        response = self.client.get("/api/v1/media/tmdb/tv/1396/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["where_to_watch"]["providers"][0]["provider_id"], 8)
        self.assertTrue(StreamingAvailability.objects.filter(title_type="tv", tmdb_id="1396", region="US").exists())
        self._assert_no_provider_requests()

    def test_season_detail_uses_season_offers_and_stores_nothing(self, *_mocks):
        self.metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "season",
            "source": "tmdb",
            "title": "Game of Thrones",
            "season_title": "Season 1",
            "image": "https://example.com/season.jpg",
            "season_number": 1,
            "episodes": [],
            "providers": {"US": {"link": "https://example.com/s1", "flatrate": [MAX]}},
        }

        response = self.client.get("/api/v1/media/tmdb/tv/1399/seasons/1/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        where = response.data["where_to_watch"]
        self.assertEqual([provider["provider_id"] for provider in where["providers"]], [1899])
        self.assertFalse(where["providers"][0]["is_mine"])
        self.assertEqual(where["link"], "https://example.com/s1")
        self.assertFalse(StreamingAvailability.objects.exists())
        self._assert_no_provider_requests()

    def test_null_for_books_and_disabled_users(self, *_mocks):
        self.metadata_mock.return_value = {
            "media_id": "OL1M",
            "media_type": "book",
            "source": "openlibrary",
            "title": "A Book",
            "image": "https://example.com/book.jpg",
        }
        book = self.client.get("/api/v1/media/openlibrary/book/OL1M/")
        self.metadata_mock.return_value = _movie_metadata("603")
        self.user.watch_provider_region = ""
        self.user.save(update_fields=["watch_provider_region"])
        disabled = self.client.get("/api/v1/media/tmdb/movie/603/")

        self.assertEqual(book.status_code, status.HTTP_200_OK)
        self.assertIn("where_to_watch", book.data)
        self.assertIsNone(book.data["where_to_watch"])
        self.assertEqual(disabled.status_code, status.HTTP_200_OK)
        self.assertIsNone(disabled.data["where_to_watch"])

    def test_anonymous_detail_uses_header_region(self, *_mocks):
        self.metadata_mock.return_value = _movie_metadata("693134")

        response = APIClient().get("/api/v1/media/tmdb/movie/693134/", HTTP_X_SPINE_REGION="GB")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        where = response.data["where_to_watch"]
        self.assertEqual(where["region"], "GB")
        self.assertEqual(where["providers"][0]["name"], "Netflix")
        self.assertFalse(where["providers"][0]["is_mine"])
        self._assert_no_provider_requests()

    def test_where_to_watch_queries_are_bounded(self, *_mocks):
        many = [
            (
                {
                    "provider_id": 1000 + index,
                    "provider_name": f"Service {index}",
                    "logo_path": "/s.png",
                    "display_priority": index,
                },
                "flatrate",
            )
            for index in range(12)
        ]
        # The viewer's own services, so all twelve show.
        for entry, _monetization in many:
            UserStreamingService.objects.create(user=self.user, provider=make_provider(entry))
        make_snapshot("movie", "1", "US", [(NETFLIX, "flatrate"), (HULU, "rent")])
        make_snapshot("movie", "2", "US", many)
        active_regions()
        # Warm per-process lookups so both measured requests do the same work.
        self.metadata_mock.return_value = _movie_metadata("0")
        self.client.get("/api/v1/media/tmdb/movie/0/")

        counts = {}
        for media_id in ("1", "2"):
            self.metadata_mock.return_value = _movie_metadata(media_id)
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get(f"/api/v1/media/tmdb/movie/{media_id}/")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            streaming_queries = [query["sql"] for query in queries if "streaming_" in query["sql"]]
            self.assertLessEqual(len(streaming_queries), 4, streaming_queries)
            counts[media_id] = len(queries)

        self.assertEqual(counts["1"], counts["2"])
        self.assertEqual(len(response.data["where_to_watch"]["providers"]), 12)
