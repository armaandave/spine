from decimal import Decimal
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from app.models import (
    CustomPosterPreference,
    Item,
    MediaTypes,
    Sources,
)
from app.providers.services import ProviderAPIError


class AnimeStudioAPITests(TestCase):
    """Verify the public MAL anime-studio API contract."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @patch("api.services.media.provider_services.get_company")
    def test_company_detail_returns_mal_anime_studio_contract(
        self,
        company_mock,
    ):
        company_mock.return_value = {
            "id": "1",
            "source": Sources.MAL.value,
            "name": "Wit Studio",
            "description": "Japanese animation studio.",
            "image": "https://example.com/wit.jpg",
            "founded_year": 2012,
            "provider_url": "https://myanimelist.net/anime/producer/1",
            "websites": ["https://www.witstudio.co.jp/"],
        }

        response = self.client.get("/api/v1/companies/mal/1/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["source"], Sources.MAL.value)
        self.assertEqual(response.data["media_type"], MediaTypes.ANIME.value)
        self.assertEqual(response.data["logo_url"], "https://example.com/wit.jpg")
        self.assertEqual(response.data["founded_year"], 2012)
        self.assertEqual(
            response.data["catalogs"],
            {
                "studio": {
                    "available": True,
                    "count": None,
                    "completion": None,
                },
            },
        )
        self.assertIsNone(response.data["igdb_url"])

    @patch(
        "api.services.media.mal.studio_anime_completion_catalog",
        return_value={"complete": False, "results": []},
    )
    @patch("api.services.media.provider_services.get_company_anime")
    def test_anime_catalog_returns_nullable_count_and_user_poster(
        self,
        catalog_mock,
        _completion_catalog_mock,
    ):
        catalog_mock.return_value = {
            "count": None,
            "page": 1,
            "previous_page": None,
            "next_page": 2,
            "results": [
                {
                    "media_id": "16498",
                    "source": Sources.MAL.value,
                    "media_type": MediaTypes.ANIME.value,
                    "title": "Shingeki no Kyojin",
                    "display_title": "Attack on Titan",
                    "image": "https://example.com/aot.jpg",
                    "release_date": "2013-04-07",
                    "genres": ["Action"],
                },
            ],
        }
        item = Item.objects.create(
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            media_id="16498",
            title="Attack on Titan",
            image="https://example.com/aot.jpg",
        )
        user = get_user_model().objects.create_user(username="studio-viewer")
        CustomPosterPreference.objects.create(
            user=user,
            item=item,
            custom_image_url="https://example.com/custom-aot.jpg",
        )
        self.client.force_authenticate(user)

        response = self.client.get(
            "/api/v1/companies/mal/1/anime/",
            {
                "sort": "average_rating",
                "direction": "desc",
                "year": 2013,
                "genre": "Action",
                "exclude_genre": "Comedy",
                "rating_min": "8.0",
                "rating_max": "9.0",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["count"])
        self.assertIsNone(response.data["previous"])
        self.assertIn("page=2", response.data["next"])
        anime = response.data["results"][0]
        # An anime goes by its English title everywhere (AN-705).
        self.assertEqual(anime["title"], "Attack on Titan")
        self.assertEqual(anime["display_title"], "Attack on Titan")
        self.assertEqual(anime["poster_url"], "https://example.com/aot.jpg")
        self.assertEqual(
            anime["custom_poster_url"],
            "https://example.com/custom-aot.jpg",
        )
        call = catalog_mock.call_args
        self.assertEqual(call.kwargs["page"], 1)
        self.assertEqual(call.kwargs["page_size"], 25)
        self.assertEqual(call.kwargs["sort"], "average_rating")
        self.assertEqual(
            call.kwargs["filters"],
            {
                "year": 2013,
                "release_status": None,
                "rating_min": Decimal("8.0"),
                "rating_max": Decimal("9.0"),
                "genres": ["Action"],
                "excluded_genres": ["Comedy"],
            },
        )

    def test_anime_catalog_validates_native_mal_rating_range(self):
        response = self.client.get(
            "/api/v1/companies/mal/1/anime/",
            {"rating_min": "10.1"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "rating_min and rating_max must be between 0 and 10.",
        )

        response = self.client.get(
            "/api/v1/companies/mal/1/anime/",
            {"page_size": "0"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "page_size must be between 1 and 25.",
        )

        response = self.client.get(
            "/api/v1/companies/mal/1/anime/",
            {"page": "0"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "page must be at least 1.",
        )

    def test_anime_catalog_rejects_game_platform_filters(self):
        response = self.client.get(
            "/api/v1/companies/mal/1/anime/",
            {"platform": "PC"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["detail"],
            "platform filters are not supported for anime studio catalogs.",
        )

    @patch(
        "api.services.media.provider_services.get_company_anime",
        side_effect=ProviderAPIError(
            Sources.MAL.value,
            requests.Timeout("offline"),
        ),
    )
    def test_anime_catalog_provider_failure_uses_standard_503_envelope(
        self,
        _catalog_mock,
    ):
        response = self.client.get("/api/v1/companies/mal/1/anime/")

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(
            response.data["error"]["code"],
            "provider_unavailable",
        )

    @patch(
        "api.services.media.provider_services.get_company_anime_filter_options",
    )
    def test_anime_options_use_mal_rating_and_no_platforms(
        self,
        options_mock,
    ):
        options_mock.return_value = {
            "genres": [
                {"value": "Action", "label": "Action"},
            ],
            "platforms": [],
            "years": [2027, 2026],
        }

        response = self.client.get(
            "/api/v1/companies/mal/1/anime-options/",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["sorts"],
            [
                {"value": "popularity", "label": "Popularity"},
                {"value": "release_date", "label": "Release Date"},
                {"value": "average_rating", "label": "MAL Rating"},
                {"value": "title", "label": "Title"},
            ],
        )
        self.assertEqual(response.data["platforms"], [])
        self.assertEqual(
            response.data["genres"],
            [{"value": "Action", "label": "Action"}],
        )

    @patch("api.services.media.provider_services.company_catalog_count")
    @patch("api.services.media.provider_services.get_company")
    def test_existing_igdb_company_contract_is_unchanged(
        self,
        company_mock,
        count_mock,
    ):
        company_mock.return_value = {
            "id": 77,
            "name": "Space Studio",
            "logo": {},
            "websites": [],
        }
        count_mock.side_effect = [24, 8]

        response = self.client.get("/api/v1/companies/igdb/77/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("media_type", response.data)
        self.assertNotIn("provider_url", response.data)
        self.assertEqual(
            response.data["catalogs"],
            {
                "developed": {"count": 24, "completion": None},
                "published": {"count": 8, "completion": None},
            },
        )
