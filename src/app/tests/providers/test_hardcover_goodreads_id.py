from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase

from app.providers import hardcover


class HardcoverGoodreadsIdTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("app.providers.hardcover.services.api_request")
    def test_returns_first_linked_goodreads_id_and_caches_it(self, api_request_mock):
        api_request_mock.return_value = {
            "data": {"books_by_pk": {"book_mappings": [{"external_id": "54493401"}]}},
        }

        self.assertEqual(hardcover.goodreads_id("427578"), "54493401")
        self.assertEqual(hardcover.goodreads_id("427578"), "54493401")
        self.assertEqual(api_request_mock.call_count, 1)

    @patch("app.providers.hardcover.services.api_request")
    def test_book_without_a_goodreads_link_returns_none(self, api_request_mock):
        api_request_mock.return_value = {"data": {"books_by_pk": {"book_mappings": []}}}

        self.assertIsNone(hardcover.goodreads_id("427578"))
        self.assertIsNone(hardcover.goodreads_id("427578"))
        self.assertEqual(api_request_mock.call_count, 1)

    @patch("app.providers.hardcover.services.api_request")
    def test_failed_lookup_raises_for_the_ratings_task_to_retry(self, api_request_mock):
        api_request_mock.side_effect = requests.exceptions.ConnectionError("down")

        with self.assertRaises(requests.exceptions.ConnectionError):
            hardcover.goodreads_id("427578")

    @patch("app.providers.hardcover.services.api_request")
    def test_isbn_finds_the_goodreads_id_of_its_hardcover_book(self, api_request_mock):
        api_request_mock.return_value = {
            "data": {"editions": [{"book": {"book_mappings": [{"external_id": "62047984"}]}}]},
        }

        self.assertEqual(hardcover.goodreads_id_for_isbn("978-0063250833"), "62047984")
        self.assertEqual(
            api_request_mock.call_args.kwargs["params"]["variables"]["isbn"],
            "9780063250833",
        )

    @patch("app.providers.hardcover.services.api_request")
    def test_isbn_hardcover_does_not_know_returns_none(self, api_request_mock):
        api_request_mock.return_value = {"data": {"editions": []}}

        self.assertIsNone(hardcover.goodreads_id_for_isbn("9780000000000"))
