from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from app.models import Item, MediaTypes, Sources
from app.providers import hardcover, services


def daily_cap_response():
    """Hardcover's answer once the key's daily cap is spent."""
    response = MagicMock(status_code=429, headers={"Retry-After": "68281"})
    response.json.return_value = {"error": "Too Many Requests"}
    response.text = '{"error":"Too Many Requests"}'
    response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "429 Too Many Requests",
        response=response,
    )
    return response


@patch("app.providers.services.time.sleep")
@patch("app.providers.services.session.post", return_value=daily_cap_response())
class HardcoverRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def save_book(self):
        return Item.objects.create(
            media_id="644",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="Dune",
            image="https://example.com/dune.jpg",
            total_pages=412,
        )

    def test_book_page_opens_from_the_saved_item(self, mock_post, mock_sleep):
        self.save_book()

        response = APIClient().get("/api/v1/media/hardcover/book/644/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "Dune")
        mock_post.assert_called_once()
        mock_sleep.assert_not_called()

    def test_saved_book_page_is_not_cached(self, mock_post, mock_sleep):  # noqa: ARG002
        self.save_book()

        APIClient().get("/api/v1/media/hardcover/book/644/")
        APIClient().get("/api/v1/media/hardcover/book/644/")

        self.assertEqual(mock_post.call_count, 2)

    def test_unsaved_book_page_fails_with_503(self, mock_post, mock_sleep):  # noqa: ARG002
        response = APIClient().get("/api/v1/media/hardcover/book/644/")

        self.assertEqual(response.status_code, 503)

    def test_provider_still_raises_for_saved_books(self, mock_post, mock_sleep):  # noqa: ARG002
        """Progress and status updates keep failing visibly instead of using a stub."""
        self.save_book()

        with self.assertRaises(services.ProviderAPIError) as raised:
            hardcover.book("644")

        self.assertEqual(raised.exception.status_code, 429)

    @patch("app.providers.services.openlibrary.search")
    def test_book_search_falls_back_to_open_library(self, openlibrary_search, mock_post, mock_sleep):  # noqa: ARG002
        openlibrary_search.return_value = {"results": [{"title": "Dune"}]}

        data = services.search(MediaTypes.BOOK.value, "dune", 1)

        self.assertEqual(data["results"], [{"title": "Dune"}])
        self.assertTrue(data[services.RATE_LIMIT_FALLBACK])
        openlibrary_search.assert_called_once_with("dune", 1)

    @patch("app.providers.services.openlibrary.search")
    def test_fallback_search_results_are_not_cached(self, openlibrary_search, mock_post, mock_sleep):  # noqa: ARG002
        openlibrary_search.return_value = {
            "page": 1,
            "total_results": 0,
            "total_pages": 1,
            "results": [],
        }

        client = APIClient()
        client.force_authenticate(get_user_model().objects.create_user(username="reader"))

        for _ in range(2):
            response = client.get("/api/v1/media/search/", {"q": "dune", "media_type": "book"})
            self.assertEqual(response.status_code, 200)

        self.assertEqual(mock_post.call_count, 2)


class ShortRateLimitTests(TestCase):
    @patch("app.providers.services.time.sleep")
    @patch("app.providers.services.session.get")
    def test_wait_within_the_retry_budget_still_retries(self, mock_get, mock_sleep):
        limited = MagicMock(status_code=429, headers={"Retry-After": "90"})
        limited.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "429 Too Many Requests",
            response=limited,
        )
        ok = MagicMock()
        ok.json.return_value = {"data": {}}
        mock_get.side_effect = [limited, ok]

        services.api_request(Sources.MAL.value, "GET", "https://api.myanimelist.net/v2/anime/1")

        mock_sleep.assert_called_once_with(
            services.RATE_LIMIT_MAX_WAIT_SECONDS + services.RATE_LIMIT_RETRY_PADDING_SECONDS,
        )
