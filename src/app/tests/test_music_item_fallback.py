"""An Apple Music album with an Item keeps working while Apple Music fails."""

from datetime import date

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from api.services import media as media_service
from app.models import DiaryEntry, ItemFilterFacet, MediaTypes, Music, Sources
from app.providers import apple_music, services
from app.tests.providers.test_apple_music import (
    AppleMusicTestCase,
    apple_item,
    fixture,
    genre_facets,
    related_answers,
)
from events.calendar.helpers import date_parser
from events.calendar.other import process_other

ALBUM_ID = "1657869377"
REF = {"source": Sources.APPLE_MUSIC.value, "media_type": MediaTypes.MUSIC.value, "media_id": ALBUM_ID}


class AppleItemFallbackTests(AppleMusicTestCase):
    def setUp(self):
        super().setUp()
        self.item = apple_item(ALBUM_ID, title="SOS", release_date=date(2022, 12, 9), release_year=2022)
        genre_facets(self.item, "r&b", "contemporary r&b")
        self.user = get_user_model().objects.create_user(username="listener", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def genres(self):
        return set(
            ItemFilterFacet.objects.filter(
                item=self.item,
                facet_type=ItemFilterFacet.FacetType.GENRE,
            ).values_list("value", flat=True),
        )

    def assert_the_album_still_works(self):
        response = self.client.get(f"/api/v1/media/apple_music/music/{ALBUM_ID}/")

        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["ref"]["item_id"], self.item.id)
        self.assertEqual(detail["ref"]["media_id"], ALBUM_ID)
        self.assertEqual(detail["title"], "SOS")
        self.assertEqual(detail["image_url"], "https://example.com/cover.jpg")
        self.assertEqual(detail["release_date"], "2022-12-09")
        self.assertIsNone(detail["music"])
        self.assertEqual(sorted(detail["details"]["genres"]), ["contemporary r&b", "r&b"])
        for stage in (True, False):
            cache_key = media_service._detail_cache_key(
                source=Sources.APPLE_MUSIC.value,
                media_type=MediaTypes.MUSIC.value,
                media_id=ALBUM_ID,
                season_number=None,
                episode_number=None,
                include_music_enrichment=stage,
            )
            self.assertIsNone(cache.get(cache_key))
            self.assertIsNone(cache.get(f"{cache_key}:validation"))

        # Tracking first: a diary entry would already have made the tracking row, and
        # only a new row fetches the album.
        tracking = self.client.put(
            f"/api/v1/tracking/apple_music/music/{ALBUM_ID}/",
            {"status": "Planning"},
            format="json",
        )
        self.assertEqual(tracking.status_code, 200, tracking.content)
        self.assertTrue(Music.objects.filter(user=self.user, item=self.item).exists())

        diary = self.client.post(
            "/api/v1/diary/",
            {"ref": REF, "consumed_at": "2026-10-20T12:00:00Z", "rating": "4.5"},
            format="json",
        )
        self.assertEqual(diary.status_code, 201, diary.content)
        self.assertTrue(DiaryEntry.objects.filter(user=self.user, item=self.item).exists())

        events = []
        process_other(self.item, events)
        self.assertEqual([event.datetime for event in events], [date_parser("2022-12-09")])

        self.assertEqual(self.genres(), {"r&b", "contemporary r&b"})

    @override_settings(APPLE_MUSIC_TEAM_ID="", APPLE_MUSIC_KEY_ID="", APPLE_MUSIC_PRIVATE_KEY="")
    def test_without_a_key(self):
        fake = self.apple()

        self.assert_the_album_still_works()

        self.assertEqual(fake.requests, [])

    def test_when_apple_rejects_the_key(self):
        # A 401 doesn't pause Apple calls, so the page, tracking, diary and calendar
        # each ask again.
        fake = self.apple(*[(401, b"")] * 4)

        self.assert_the_album_still_works()

        self.assertEqual(len(fake.requests), 4)

    def test_when_apple_is_rate_limited(self):
        # The first 429 pauses Apple calls, so the rest fail without a request.
        fake = self.apple((429, {"errors": []}))

        self.assert_the_album_still_works()

        self.assertEqual(len(fake.requests), 1)

    def test_when_apple_times_out(self):
        fake = self.apple(requests.exceptions.Timeout("timed out"))

        self.assert_the_album_still_works()

        self.assertEqual(len(fake.requests), 1)

    def test_when_apple_has_taken_the_album_down(self):
        # No barcode is stored, so there is no successor to look for.
        fake = self.apple(*[(404, fixture("error_404.json")["body"])] * 4)

        self.assert_the_album_still_works()

        self.assertEqual(len(fake.requests), 4)

    def test_the_full_page_comes_back_once_apple_answers(self):
        self.apple((429, {"errors": []}))
        self.assertIsNone(services.get_media_metadata("music", ALBUM_ID, "apple_music")["music"])
        services.redis_db.delete(apple_music.backoff_key())
        self.apple((200, fixture("page_sos.json")), *related_answers())

        response = self.client.get(f"/api/v1/media/apple_music/music/{ALBUM_ID}/")

        self.assertEqual(response.json()["music"]["representative_release"]["track_count"], 23)

    def test_an_album_without_an_item_still_fails(self):
        self.apple((429, {"errors": []}))

        with self.assertRaises(services.ProviderAPIError):
            services.get_media_metadata("music", "1658650093", "apple_music")
