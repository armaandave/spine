from unittest.mock import MagicMock, patch

import requests
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

    def test_saved_book_page_opens_from_the_saved_item(self, mock_post, mock_sleep):
        self.save_book()

        data = hardcover.book("644")

        self.assertEqual(data["title"], "Dune")
        self.assertEqual(data["source"], Sources.HARDCOVER.value)
        self.assertEqual(data["max_progress"], 412)
        mock_post.assert_called_once()
        mock_sleep.assert_not_called()

    def test_saved_book_page_is_not_cached(self, mock_post, mock_sleep):  # noqa: ARG002
        self.save_book()

        hardcover.book("644")
        hardcover.book("644")

        self.assertEqual(mock_post.call_count, 2)

    def test_unsaved_book_fails_with_429(self, mock_post, mock_sleep):  # noqa: ARG002
        with self.assertRaises(services.ProviderAPIError) as raised:
            hardcover.book("644")

        self.assertEqual(raised.exception.status_code, 429)

    @patch("app.providers.services.openlibrary.search")
    def test_book_search_falls_back_to_open_library(self, openlibrary_search, mock_post, mock_sleep):  # noqa: ARG002
        openlibrary_search.return_value = {"results": [{"title": "Dune"}]}

        data = services.search(MediaTypes.BOOK.value, "dune", 1)

        self.assertEqual(data, {"results": [{"title": "Dune"}]})
        openlibrary_search.assert_called_once_with("dune", 1)

    def test_book_page_api_returns_the_saved_book(self, mock_post, mock_sleep):
        self.save_book()

        response = APIClient().get("/api/v1/media/hardcover/book/644/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["title"], "Dune")
        mock_sleep.assert_not_called()
