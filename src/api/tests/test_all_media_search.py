import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from api.services import media as media_service
from app.models import MediaTypes, Sources
from app.providers import services as provider_services
from app.tasks import refresh_all_media_search_candidate


class AllMediaSearchTests(TestCase):
    """Contract tests for authenticated flat all-media search."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="all-search", password="password")
        for media_type in (
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.ANIME.value,
            MediaTypes.MANGA.value,
            MediaTypes.GAME.value,
            MediaTypes.BOOK.value,
            MediaTypes.COMIC.value,
            MediaTypes.MUSIC.value,
        ):
            setattr(self.user, f"{media_type}_enabled", media_type in {MediaTypes.MOVIE.value, MediaTypes.BOOK.value})
        self.user.save()
        self.client.force_authenticate(self.user)

    def test_authenticated_meta_returns_enabled_primary_types(self):
        response = self.client.get("/api/v1/meta/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["enabled_media_types"], [MediaTypes.MOVIE.value, MediaTypes.BOOK.value])

    def test_public_meta_omits_user_enabled_types(self):
        self.client.force_authenticate(user=None)

        response = self.client.get("/api/v1/meta/")

        self.assertNotIn("enabled_media_types", response.data)

    @patch("api.views.media.media_service.search_all_media")
    def test_all_scope_uses_only_enabled_primary_types(self, search_all):
        search_all.return_value = {
            "results": [],
            "completed_media_types": [MediaTypes.MOVIE.value, MediaTypes.BOOK.value],
            "unavailable_media_types": [],
        }

        response = self.client.get(
            "/api/v1/media/search/?scope=all&q=dune&media_types=game,music",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            search_all.call_args.kwargs["media_types"],
            [MediaTypes.MOVIE.value, MediaTypes.BOOK.value],
        )

    def test_all_scope_requires_an_enabled_primary_type(self):
        self.user.movie_enabled = False
        self.user.book_enabled = False
        self.user.save(update_fields=["movie_enabled", "book_enabled"])

        response = self.client.get("/api/v1/media/search/?scope=all&q=dune")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Enable at least one media type", response.data["media_types"][0])

    def test_all_scope_rejects_pagination_after_page_one(self):
        response = self.client.get("/api/v1/media/search/?scope=all&q=dune&page=2")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("first page", response.data["page"][0])

    @patch("api.views.media.media_service.search_all_media")
    def test_partial_result_names_unavailable_media_types(self, search_all):
        search_all.return_value = {
            "results": [],
            "completed_media_types": [MediaTypes.MOVIE.value],
            "unavailable_media_types": [MediaTypes.BOOK.value],
        }

        response = self.client.get("/api/v1/media/search/?scope=all&q=dune")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertIsNone(response.data["next"])
        self.assertIsNone(response.data["previous"])
        self.assertEqual(response.data["unavailable_media_types"], [MediaTypes.BOOK.value])

    @patch("api.views.media.media_service.search_all_media")
    def test_all_provider_failure_returns_standard_error(self, search_all):
        search_all.return_value = {
            "results": [],
            "completed_media_types": [],
            "unavailable_media_types": [MediaTypes.MOVIE.value, MediaTypes.BOOK.value],
        }

        response = self.client.get("/api/v1/media/search/?scope=all&q=dune")

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["error"]["code"], "provider_unavailable")

    @patch("api.services.media.provider_services.search")
    def test_provider_searches_start_concurrently(self, provider_search):
        barrier = threading.Barrier(2)

        def search(media_type, *_args, **_kwargs):
            barrier.wait(timeout=1)
            return {
                "results": [
                    {
                        "media_id": media_type,
                        "media_type": media_type,
                        "source": Sources.TMDB.value if media_type == MediaTypes.MOVIE.value else Sources.HARDCOVER.value,
                        "title": "Dune",
                    },
                ],
            }

        provider_search.side_effect = search

        payload = media_service.search_all_media(
            media_types=[MediaTypes.MOVIE.value, MediaTypes.BOOK.value],
            query="dune",
        )

        self.assertEqual(payload["completed_media_types"], [MediaTypes.MOVIE.value, MediaTypes.BOOK.value])
        self.assertEqual(len(payload["results"]), 2)
        for call in provider_search.call_args_list:
            self.assertTrue(call.kwargs["preserve_ranking_fields"])
            self.assertEqual(call.kwargs["timeout"], media_service.ALL_MEDIA_SEARCH_TIMEOUT)

    @patch("api.services.media.provider_services.search")
    def test_replaced_music_search_replaces_the_whole_search(self, provider_search):
        checks = {}

        def search(media_type, *_args, **_kwargs):
            checks[media_type] = provider_services.search_superseded.get()
            if media_type == MediaTypes.MUSIC.value:
                raise provider_services.SearchSuperseded
            return {"results": []}

        provider_search.side_effect = search

        with self.assertRaises(provider_services.SearchSuperseded):
            media_service.search_all_media(
                media_types=[MediaTypes.MOVIE.value, MediaTypes.MUSIC.value],
                query="my beau",
                user=self.user,
            )

        # Only the music worker, the one waiting on MusicBrainz, can drop out.
        self.assertIsNotNone(checks[MediaTypes.MUSIC.value])
        self.assertIsNone(checks[MediaTypes.MOVIE.value])

    @patch("api.views.media.media_service.search_all_media")
    def test_replaced_all_search_answers_conflict(self, search_all):
        search_all.side_effect = provider_services.SearchSuperseded

        response = self.client.get("/api/v1/media/search/?scope=all&q=my+beau")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "search_replaced")

    @patch("api.services.media.provider_services.search")
    def test_provider_failure_returns_other_completed_results(self, provider_search):
        def search(media_type, *_args, **_kwargs):
            if media_type == MediaTypes.BOOK.value:
                raise RuntimeError("provider unavailable")
            return {
                "results": [
                    {
                        "media_id": "movie",
                        "media_type": MediaTypes.MOVIE.value,
                        "source": Sources.TMDB.value,
                        "title": "Dune",
                    },
                ],
            }

        provider_search.side_effect = search

        payload = media_service.search_all_media(
            media_types=[MediaTypes.MOVIE.value, MediaTypes.BOOK.value],
            query="dune",
        )

        self.assertEqual(payload["completed_media_types"], [MediaTypes.MOVIE.value])
        self.assertEqual(payload["unavailable_media_types"], [MediaTypes.BOOK.value])
        self.assertEqual([item["title"] for item in payload["results"]], ["Dune"])

    @patch("api.services.media.provider_services.search")
    def test_normal_and_preserved_candidate_caches_are_distinct(self, provider_search):
        provider_search.return_value = {
            "results": [
                {
                    "media_id": "dune",
                    "media_type": MediaTypes.MOVIE.value,
                    "source": Sources.TMDB.value,
                    "title": "Dune",
                    "vote_count": 10_000,
                },
            ],
        }

        specific = media_service.search_media(
            media_type=MediaTypes.MOVIE.value,
            query="dune",
        )
        mixed = media_service.search_all_media(
            media_types=[MediaTypes.MOVIE.value],
            query="dune",
        )

        self.assertEqual(provider_search.call_count, 2)
        self.assertFalse(provider_search.call_args_list[0].kwargs["preserve_ranking_fields"])
        self.assertTrue(provider_search.call_args_list[1].kwargs["preserve_ranking_fields"])
        self.assertEqual(specific[0]["title"], mixed["results"][0]["title"])
        self.assertNotIn("vote_count", mixed["results"][0])

    @patch("api.services.media.ALL_MEDIA_SEARCH_TIMEOUT", 0.01)
    @patch("api.services.media.provider_services.search")
    def test_slow_provider_returns_as_partial_failure(self, provider_search):
        release = threading.Event()

        def search(media_type, *_args, **_kwargs):
            if media_type == MediaTypes.BOOK.value:
                release.wait(timeout=1)
            return {"results": []}

        provider_search.side_effect = search
        try:
            with self.assertLogs(media_service.logger, level="WARNING") as logs:
                payload = media_service.search_all_media(
                    media_types=[MediaTypes.MOVIE.value, MediaTypes.BOOK.value],
                    query="dune",
                )
        finally:
            release.set()

        self.assertEqual(payload["completed_media_types"], [MediaTypes.MOVIE.value])
        self.assertEqual(payload["unavailable_media_types"], [MediaTypes.BOOK.value])
        self.assertIn("media_type=book reason=timeout", logs.output[0])
        self.assertNotIn("dune", logs.output[0])


class AllMediaCandidateCacheTests(TestCase):
    """Tests for rank-preserving All-search candidate caching."""

    def setUp(self):
        cache.clear()
        self.media_type = MediaTypes.MOVIE.value
        self.source = Sources.TMDB.value
        self.query = "dune"
        self.cache_key = media_service._search_cache_key(
            media_type=self.media_type,
            source=self.source,
            query=self.query,
            page=1,
            preserve_ranking_fields=True,
        )
        self.data = {
            "results": [
                {
                    "media_id": "438631",
                    "media_type": self.media_type,
                    "source": self.source,
                    "title": "Dune",
                },
            ],
        }

    @staticmethod
    def _log_payload(logs, event):
        message = next(message for message in logs.output if event in message)
        return json.loads(message.split(event, 1)[1].strip())

    def _candidate_data(self, **overrides):
        return media_service._candidate_data(
            media_type=overrides.get("media_type", self.media_type),
            source=overrides.get("source", self.source),
            query=overrides.get("query", self.query),
            page=1,
            timeout=overrides.get("timeout", 1),
        )

    @patch("api.services.media.provider_services.search")
    def test_fresh_candidate_is_returned_and_logged_without_provider_work(self, provider_search):
        media_service._cache_candidate(self.cache_key, self.data)

        with self.assertLogs(media_service.logger, level="INFO") as logs:
            result = media_service.search_all_media(
                media_types=[self.media_type],
                query=self.query,
            )

        provider_search.assert_not_called()
        self.assertEqual(result["results"][0]["title"], "Dune")
        provider_summary = self._log_payload(logs, "all_media_search_provider_summary")
        aggregate = self._log_payload(logs, "all_media_search_summary")
        self.assertEqual(provider_summary["cache_status"], "fresh")
        self.assertTrue(provider_summary["cache_hit"])
        self.assertEqual(provider_summary["outcome"], "success")
        self.assertEqual(aggregate["cache_status_counts"], {"fresh": 1})
        self.assertNotIn(self.query, " ".join(logs.output))

    @patch("app.tasks.refresh_all_media_search_candidate.delay")
    @patch("api.services.media.provider_services.search")
    def test_legacy_candidate_is_served_stale_and_schedules_refresh_once(
        self,
        provider_search,
        refresh_delay,
    ):
        cache.set(self.cache_key, self.data, media_service.ALL_MEDIA_CANDIDATE_STALE_TTL)

        data, cache_status, provider_elapsed_ms, scheduled = self._candidate_data()
        second = self._candidate_data()

        self.assertEqual(data, self.data)
        self.assertEqual(cache_status, "stale")
        self.assertIsNone(provider_elapsed_ms)
        self.assertTrue(scheduled)
        self.assertEqual(second[1], "stale")
        self.assertFalse(second[3])
        provider_search.assert_not_called()
        refresh_delay.assert_called_once_with(self.media_type, self.query, 1, self.source)

    @patch("app.tasks.refresh_all_media_search_candidate.delay")
    def test_concurrent_stale_callers_enqueue_one_refresh(self, refresh_delay):
        cache.set(self.cache_key, self.data, media_service.ALL_MEDIA_CANDIDATE_STALE_TTL)
        barrier = threading.Barrier(4)

        def read_stale():
            barrier.wait(timeout=1)
            return self._candidate_data()

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _index: read_stale(), range(4)))

        self.assertEqual(sum(result[3] for result in results), 1)
        refresh_delay.assert_called_once()

    @patch("api.services.media.provider_services.search")
    def test_concurrent_cold_callers_share_one_provider_request(self, provider_search):
        provider_started = threading.Event()
        release_provider = threading.Event()

        def search(*_args, **_kwargs):
            provider_started.set()
            release_provider.wait(timeout=1)
            return self.data

        provider_search.side_effect = search
        with ThreadPoolExecutor(max_workers=4) as executor:
            owner = executor.submit(self._candidate_data)
            self.assertTrue(provider_started.wait(timeout=1))
            followers = [executor.submit(self._candidate_data) for _index in range(3)]
            time.sleep(0.1)
            release_provider.set()
            results = [owner.result(), *(future.result() for future in followers)]

        self.assertEqual(provider_search.call_count, 1)
        self.assertEqual([result[1] for result in results].count("miss"), 1)
        self.assertEqual([result[1] for result in results].count("coalesced"), 3)
        self.assertTrue(all(result[0] == self.data for result in results))

    @patch("api.services.media.provider_services.search")
    def test_failed_owner_releases_lock_for_later_retry(self, provider_search):
        provider_search.side_effect = [RuntimeError("provider down"), self.data]

        with self.assertRaises(RuntimeError):
            self._candidate_data()
        data, cache_status, *_rest = self._candidate_data()

        self.assertEqual(data, self.data)
        self.assertEqual(cache_status, "miss")
        self.assertEqual(provider_search.call_count, 2)

    @patch("api.services.media.provider_services.search", return_value={"results": []})
    def test_successful_empty_results_are_cached(self, provider_search):
        first = self._candidate_data()
        second = self._candidate_data()

        self.assertEqual(first[1], "miss")
        self.assertEqual(second[1], "fresh")
        self.assertEqual(provider_search.call_count, 1)

    @patch("api.services.media.provider_services.search", return_value={"results": []})
    def test_different_queries_do_not_coalesce(self, provider_search):
        self._candidate_data(query="dune")
        self._candidate_data(query="foundation")

        self.assertEqual(provider_search.call_count, 2)

    @patch("api.services.media.provider_services.search")
    def test_refresh_replaces_stale_candidates(self, provider_search):
        cache.set(self.cache_key, self.data, media_service.ALL_MEDIA_CANDIDATE_STALE_TTL)
        refreshed = {"results": [{**self.data["results"][0], "title": "Dune: Part Two"}]}
        provider_search.return_value = refreshed

        summary = media_service.refresh_all_media_candidate(
            media_type=self.media_type,
            query=self.query,
            page=1,
            source=self.source,
        )

        self.assertEqual(summary["outcome"], "refreshed")
        self.assertEqual(cache.get(self.cache_key), refreshed)
        self.assertTrue(media_service._candidate_is_fresh(self.cache_key))

    @patch("api.services.media.provider_services.search", side_effect=RuntimeError("secret dune URL"))
    def test_refresh_failure_preserves_stale_candidates_and_safe_logs(self, _provider_search):
        schedule_key = f"{self.cache_key}:refresh-scheduled"
        cache.set(self.cache_key, self.data, media_service.ALL_MEDIA_CANDIDATE_STALE_TTL)
        cache.set(schedule_key, 1, media_service.ALL_MEDIA_REFRESH_SCHEDULE_TTL)

        with self.assertLogs(media_service.logger, level="WARNING") as logs:
            summary = media_service.refresh_all_media_candidate(
                media_type=self.media_type,
                query=self.query,
                page=1,
                source=self.source,
            )

        self.assertEqual(summary["outcome"], "failed")
        self.assertEqual(summary["exception_type"], "RuntimeError")
        self.assertEqual(cache.get(self.cache_key), self.data)
        self.assertEqual(cache.get(schedule_key), 1)
        self.assertNotIn("secret dune URL", " ".join(logs.output))

    @patch("api.services.media.provider_services.search")
    def test_delayed_refresh_skips_fresh_candidate(self, provider_search):
        media_service._cache_candidate(self.cache_key, self.data)

        summary = media_service.refresh_all_media_candidate(
            media_type=self.media_type,
            query=self.query,
            page=1,
            source=self.source,
        )

        self.assertEqual(summary["outcome"], "skipped_fresh")
        provider_search.assert_not_called()

    @patch(
        "app.tasks.refresh_all_media_search_candidate.delay",
        side_effect=ConnectionError("broker down"),
    )
    def test_enqueue_failure_returns_stale_and_allows_retry(self, _refresh_delay):
        cache.set(self.cache_key, self.data, media_service.ALL_MEDIA_CANDIDATE_STALE_TTL)

        result = self._candidate_data()

        self.assertEqual(result[0], self.data)
        self.assertEqual(result[1], "stale")
        self.assertFalse(result[3])
        self.assertIsNone(cache.get(f"{self.cache_key}:refresh-scheduled"))

    def test_single_flight_timeout_is_a_partial_failure(self):
        lock = media_service._candidate_lock(self.cache_key, 1)
        self.assertTrue(lock.acquire(blocking=False))
        try:
            with (
                patch("api.services.media.ALL_MEDIA_SEARCH_TIMEOUT", 0.01),
                self.assertLogs(media_service.logger, level="WARNING") as logs,
            ):
                payload = media_service.search_all_media(
                    media_types=[self.media_type],
                    query=self.query,
                )
        finally:
            lock.release()

        self.assertEqual(payload["completed_media_types"], [])
        self.assertEqual(payload["unavailable_media_types"], [self.media_type])
        self.assertIn("single_flight_timeout", " ".join(logs.output))

    @patch("api.services.media.refresh_all_media_candidate", return_value={"outcome": "refreshed"})
    def test_celery_task_delegates_to_media_service(self, refresh_candidate):
        result = refresh_all_media_search_candidate.run(
            self.media_type,
            self.query,
            1,
            self.source,
        )

        self.assertEqual(result, {"outcome": "refreshed"})
        refresh_candidate.assert_called_once_with(
            media_type=self.media_type,
            query=self.query,
            page=1,
            source=self.source,
        )
