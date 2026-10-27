"""Apple Music search, people search and artist pages through the API.

The catalog setting still defaults to MusicBrainz, so every request that doesn't name
Apple Music behaves as before.
"""

import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from api.services import media as media_service
from app.models import Music, Sources, Status
from app.providers import services as provider_services
from app.providers.search_rank import RANKING_KEYS
from app.tests.providers.test_apple_music import AppleMusicTestCase, apple_item
from app.tests.providers.test_apple_music_search import (
    SZA_ID,
    person_answers,
    search_answers,
)
from lists.models import CustomList, PersonListItem

SEARCH_URL = "/api/v1/media/search/"


class AppleSearchAPITestCase(AppleMusicTestCase):
    """A signed-in listener, with Apple Music faked from recorded responses."""

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="listener", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)


class AppleMusicSearchAPITests(AppleSearchAPITestCase):
    """Music search with the Apple Music source, and without it."""

    def test_apple_music_search_answers_in_the_summary_shape(self):
        item = apple_item("1657869377")
        Music.objects.create(user=self.user, item=item, status=Status.COMPLETED.value)
        self.apple(*search_answers("sza_sos_1"))

        response = self.client.get(SEARCH_URL, {"media_type": "music", "q": "sza sos", "source": "apple_music"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sos, lana = response.data["results"][:2]
        self.assertEqual(
            sos["ref"],
            {
                "item_id": item.id,
                "source": "apple_music",
                "media_type": "music",
                "media_id": "1657869377",
                "season_number": None,
                "episode_number": None,
            },
        )
        self.assertEqual(sos["user_state"]["status"], Status.COMPLETED.value)
        self.assertEqual((lana["title"], lana["subtitle"]), ("SOS Deluxe: LANA", "SZA · 2024 · Album"))
        self.assertEqual(lana["content_rating"], "explicit")
        self.assertEqual(lana["default_source"], "apple_music")
        self.assertFalse(any(key in result for result in response.data["results"] for key in RANKING_KEYS))

    def test_music_search_without_a_source_stays_on_musicbrainz(self):
        apple = self.apple()
        musicbrainz = {
            "page": 1,
            "total_results": 1,
            "total_pages": 1,
            "results": [{"media_id": "3bd76d40-7f0e-36b7-9348-91a33afee20e", "source": "musicbrainz", "title": "Year Zero"}],
        }
        with patch("app.providers.services.musicbrainz.search", return_value=musicbrainz) as search:
            response = self.client.get(SEARCH_URL, {"media_type": "music", "q": "year zero"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["ref"]["source"], "musicbrainz")
        self.assertEqual(search.call_args.args, ("year zero", 1))
        self.assertEqual(apple.requests, [])

    @override_settings(MUSIC_CATALOG_SOURCE="apple_music")
    def test_the_apple_catalog_serves_searches_that_name_no_source(self):
        # Installed builds never send a source for search.
        self.apple(*search_answers("sza_sos_1"))

        response = self.client.get(SEARCH_URL, {"media_type": "music", "q": "sza sos"})

        self.assertEqual(response.data["results"][0]["ref"]["source"], "apple_music")
        self.assertEqual(response.data["results"][0]["ref"]["media_id"], "1657869377")

    def test_apple_searches_use_the_music_cache_by_source(self):
        fake = self.apple(*search_answers("sza_sos_1"))
        params = {"media_type": "music", "q": "sza sos", "source": "apple_music"}

        first = self.client.get(SEARCH_URL, params)
        cache_key = media_service._search_cache_key(
            media_type="music",
            source="apple_music",
            query="sza sos",
            page=1,
            preserve_ranking_fields=False,
        )
        cache.set(f"{cache_key}:validation", {"validated_at": time.time() - media_service.SEARCH_TTL - 1})
        with patch("app.tasks.refresh_music_search.delay") as refresh:
            stale = self.client.get(SEARCH_URL, params)

        self.assertEqual(stale.data, first.data)
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(refresh.call_args.args, ("music", "sza sos", 1, "apple_music", False))
        self.assertIn(":search:music:apple_music:", cache_key)

    def test_a_stale_apple_search_refreshes_through_apple(self):
        fake = self.apple(*search_answers("sza_sos_1"))

        result = media_service.refresh_music_search(media_type="music", query="sza sos", page=1, source="apple_music")

        self.assertEqual(result["outcome"], "refreshed")
        self.assertEqual(len(fake.requests), 2)

    def test_all_media_candidates_keep_apples_boost(self):
        self.apple(*search_answers("sza_sos_1"))

        data, cache_status, _elapsed, _scheduled = media_service._candidate_data(
            media_type="music",
            query="sza sos",
            page=1,
            source="apple_music",
            timeout=media_service.ALL_MEDIA_SEARCH_TIMEOUT,
        )

        self.assertEqual(cache_status, "miss")
        self.assertEqual([row["provider_rank_boost"] for row in data["results"][:3]], [30, 27, 24])
        self.assertEqual(data["results"][0]["artist_name"], "SZA")


class AppleMusicPeopleAPITests(AppleSearchAPITestCase):
    """People search, artist pages and people lists with Apple Music artists."""

    def sources_searched(self):
        searched = []

        def search(source, _query, **_kwargs):
            searched.append(source)
            return [{"person_id": "1", "name": f"{source} artist", "profile_url": None}]

        with patch("api.services.media.provider_services.search_people", side_effect=search):
            payload = media_service.search_people(query="SZA")
        self.assertEqual(set(payload["completed_sources"]), set(searched))
        return set(searched)

    def test_people_search_asks_musicbrainz_for_artists_by_default(self):
        searched = self.sources_searched()

        self.assertIn(Sources.MUSICBRAINZ.value, searched)
        self.assertNotIn(Sources.APPLE_MUSIC.value, searched)
        self.assertEqual(len(searched), len(provider_services.SUPPORTED_PERSON_SOURCES) - 1)

    @override_settings(MUSIC_CATALOG_SOURCE="apple_music")
    def test_people_search_asks_apple_music_for_artists_on_the_apple_catalog(self):
        searched = self.sources_searched()

        self.assertIn(Sources.APPLE_MUSIC.value, searched)
        self.assertNotIn(Sources.MUSICBRAINZ.value, searched)

    def test_an_apple_music_artist_page(self):
        apple_item("1657869377")
        Music.objects.create(user=self.user, item=apple_item("1794644675", title="SOS Deluxe: LANA"), status=Status.COMPLETED.value)
        self.apple(*person_answers())

        response = self.client.get(f"/api/v1/people/apple_music/{SZA_ID}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual((response.data["id"], response.data["source"], response.data["name"]), (SZA_ID, "apple_music", "SZA"))
        self.assertTrue(response.data["profile_url"].endswith("/600x600bb.jpg"))
        cast = response.data["credits"]["cast"]
        self.assertEqual(
            [(credit["ref"]["media_id"], credit["credit_roles"]) for credit in cast[:3]],
            [("1794644675", ["Albums"]), ("1657869377", ["Albums"]), ("1628387909", ["Albums"])],
        )
        self.assertTrue(all(credit["ref"]["source"] == "apple_music" for credit in cast))
        self.assertEqual(cast[0]["user_state"]["status"], Status.COMPLETED.value)
        self.assertEqual(cast[1]["subtitle"], "2022")
        self.assertEqual(cast[1]["content_rating"], "explicit")
        roles = response.data["role_completions"]["music"]
        self.assertEqual(set(roles), {"Albums", "Singles", "EPs", "Live releases", "Appears on"})
        self.assertEqual(roles["Albums"]["completed_count"], 1)

    def test_an_apple_music_artist_can_join_a_people_list(self):
        people = CustomList.objects.create(owner=self.user, name="Artists", list_type=CustomList.ListType.PEOPLE)
        self.apple(*person_answers())

        response = self.client.post(
            f"/api/v1/lists/{people.id}/people/",
            {"ref": {"source": "apple_music", "id": SZA_ID}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        entry = PersonListItem.objects.get(custom_list=people)
        self.assertEqual((entry.source, entry.person_id, entry.name), ("apple_music", SZA_ID, "SZA"))
