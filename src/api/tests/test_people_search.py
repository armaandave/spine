import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from api.services import media as media_service
from app.providers import services as provider_services


class PeopleSearchAPITests(TestCase):
    """Contract tests for authenticated global people search."""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="people-search",
            password="password",
        )
        self.client.force_authenticate(self.user)

    @patch("api.views.media.media_service.search_people")
    def test_returns_normalized_people_and_partial_failures(self, search_people):
        search_people.return_value = {
            "results": [
                {
                    "ref": {"source": "tmdb", "id": "819"},
                    "name": "Edward Norton",
                    "profile_url": "https://example.com/edward.jpg",
                    "known_for_department": "Acting",
                },
            ],
            "completed_sources": ["tmdb"],
            "unavailable_sources": ["mal"],
        }

        response = self.client.get("/api/v1/people/search/?q=Edward%20Norton")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "ref": {"source": "tmdb", "id": "819"},
                        "name": "Edward Norton",
                        "profile_url": "https://example.com/edward.jpg",
                        "known_for_department": "Acting",
                    },
                ],
                "unavailable_sources": ["mal"],
            },
        )
        search_people.assert_called_once_with(query="Edward Norton")

    def test_requires_authentication_query_and_first_page(self):
        missing = self.client.get("/api/v1/people/search/")
        later_page = self.client.get("/api/v1/people/search/?q=Edward&page=2")
        too_long = self.client.get(
            "/api/v1/people/search/",
            {"q": "x" * 101},
        )
        self.client.force_authenticate(user=None)
        anonymous = self.client.get("/api/v1/people/search/?q=Edward")

        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(later_page.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(too_long.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(anonymous.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("api.views.media.media_service.search_people")
    def test_all_provider_failures_return_standard_503(self, search_people):
        search_people.return_value = {
            "results": [],
            "completed_sources": [],
            "unavailable_sources": list(
                provider_services.person_search_sources(),
            ),
        }

        response = self.client.get("/api/v1/people/search/?q=Edward")

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(
            response.data["error"]["code"],
            "provider_unavailable",
        )


class PeopleSearchServiceTests(TestCase):
    """Aggregation tests for global people search."""

    @patch("api.services.media.provider_services.search_people")
    def test_searches_all_sources_concurrently_and_normalizes_results(
        self,
        provider_search,
    ):
        barrier = threading.Barrier(
            len(provider_services.person_search_sources()),
        )

        def search(source, _query, **_kwargs):
            barrier.wait(timeout=2)
            return [
                {
                    "source": source,
                    "person_id": "1",
                    "name": "Edward Norton",
                    "profile_url": None,
                    "known_for_department": "Actor",
                },
                {
                    "source": source,
                    "person_id": "1",
                    "name": "Duplicate",
                },
            ]

        provider_search.side_effect = search

        payload = media_service.search_people(query="Edward Norton")

        self.assertEqual(
            set(payload["completed_sources"]),
            set(provider_services.person_search_sources()),
        )
        self.assertEqual(payload["unavailable_sources"], [])
        self.assertEqual(
            {result["ref"]["source"] for result in payload["results"]},
            set(provider_services.person_search_sources()),
        )
        self.assertTrue(
            all(
                set(result)
                == {
                    "ref",
                    "name",
                    "profile_url",
                    "known_for_department",
                }
                for result in payload["results"]
            ),
        )

    @patch("api.services.media.provider_services.search_people")
    def test_one_provider_failure_keeps_completed_results(self, provider_search):
        def search(source, _query, **_kwargs):
            if source == "openlibrary":
                raise RuntimeError("provider unavailable")
            return [{
                "person_id": source,
                "name": "Ada Lovelace",
                "profile_url": None,
                "known_for_department": "Author",
            }]

        provider_search.side_effect = search

        payload = media_service.search_people(query="Ada")

        self.assertEqual(payload["unavailable_sources"], ["openlibrary"])
        self.assertNotIn("openlibrary", payload["completed_sources"])
        self.assertEqual(
            len(payload["results"]),
            len(provider_services.person_search_sources()) - 1,
        )

    @patch("api.services.media.provider_services.search_people")
    def test_ranking_normalizes_unicode_names(self, provider_search):
        def search(source, _query, **_kwargs):
            name = "Beyoncé" if source == "tmdb" else "Beyonce Knowles"
            return [{
                "person_id": source,
                "name": name,
                "profile_url": None,
                "known_for_department": "Artist",
            }]

        provider_search.side_effect = search

        payload = media_service.search_people(query="Beyonce")

        self.assertEqual(payload["results"][0]["name"], "Beyoncé")
