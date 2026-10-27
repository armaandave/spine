import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from api.services import media


@override_settings(MUSIC_CATALOG_SOURCE="musicbrainz")
class MusicCacheTests(SimpleTestCase):
    """Verify the music stale cache contract."""

    def setUp(self):
        cache.clear()

    def test_stale_music_data_returns_before_refresh(self):
        cache_key = "test:music:stale"
        stale = {"results": [{"title": "Year Zero"}]}
        media._cache_music_data(cache_key, stale)
        cache.set(
            f"{cache_key}:validation",
            {"validated_at": time.time() - media.SEARCH_TTL - 1},
            media.MUSIC_CACHE_STALE_TTL,
        )
        fetch = Mock(side_effect=AssertionError("stale requests must not fetch inline"))
        schedule = Mock()

        result = media._music_cache_data(cache_key, media.SEARCH_TTL, fetch, schedule)

        self.assertEqual(result, stale)
        fetch.assert_not_called()
        schedule.assert_called_once_with()

    def test_failed_refresh_preserves_stale_music_data(self):
        cache_key = "test:music:refresh-failure"
        stale = {"title": "Year Zero"}
        media._cache_music_data(cache_key, stale)
        cache.set(
            f"{cache_key}:validation",
            {"validated_at": 0},
            media.MUSIC_CACHE_STALE_TTL,
        )

        result = media._refresh_music_cache(
            cache_key,
            media.DETAIL_TTL,
            Mock(side_effect=RuntimeError("provider unavailable")),
        )

        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(cache.get(cache_key), stale)

    def test_concurrent_cold_music_requests_share_one_fetch(self):
        cache_key = "test:music:cold"
        data = {"title": "Year Zero"}
        fetch_started = threading.Event()
        release_fetch = threading.Event()

        def fetch():
            fetch_started.set()
            release_fetch.wait(timeout=1)
            return data

        fetch = Mock(side_effect=fetch)
        schedule = Mock()
        with ThreadPoolExecutor(max_workers=2) as executor:
            owner = executor.submit(
                media._music_cache_data,
                cache_key,
                media.DETAIL_TTL,
                fetch,
                schedule,
            )
            self.assertTrue(fetch_started.wait(timeout=1))
            follower = executor.submit(
                media._music_cache_data,
                cache_key,
                media.DETAIL_TTL,
                fetch,
                schedule,
            )
            time.sleep(0.05)
            release_fetch.set()
            results = [owner.result(), follower.result()]

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(results, [data, data])


@override_settings(MUSIC_CATALOG_SOURCE="musicbrainz")
class MusicDetailStageViewTests(TestCase):
    """Verify the two iOS music detail stages."""

    def setUp(self):
        self.client = APIClient()

    @patch("api.views.media.media_service.media_detail", return_value={"title": "Year Zero"})
    def test_basic_and_enrichment_endpoints_select_their_stage(self, detail):
        album_id = "3bd76d40-7f0e-36b7-9348-91a33afee20e"

        basic = self.client.get(f"/api/v1/media/musicbrainz/music/{album_id}/basic/")
        enriched = self.client.get(f"/api/v1/media/musicbrainz/music/{album_id}/enrichment/")

        self.assertEqual(basic.status_code, status.HTTP_200_OK)
        self.assertEqual(enriched.status_code, status.HTTP_200_OK)
        self.assertFalse(detail.call_args_list[0].kwargs["include_music_enrichment"])
        self.assertTrue(detail.call_args_list[1].kwargs["include_music_enrichment"])
