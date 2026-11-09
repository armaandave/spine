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
            "data": {"book_mappings": [{"external_id": "54493401"}]},
        }

        self.assertEqual(hardcover.goodreads_id("427578"), "54493401")
        self.assertEqual(hardcover.goodreads_id("427578"), "54493401")
        self.assertEqual(api_request_mock.call_count, 1)

    @patch("app.providers.hardcover.services.api_request")
    def test_book_without_a_goodreads_link_returns_none(self, api_request_mock):
        api_request_mock.return_value = {"data": {"book_mappings": []}}

        self.assertIsNone(hardcover.goodreads_id("427578"))
        self.assertIsNone(hardcover.goodreads_id("427578"))
        self.assertEqual(api_request_mock.call_count, 1)

    @patch("app.providers.hardcover.services.api_request")
    def test_failed_lookup_returns_none(self, api_request_mock):
        api_request_mock.side_effect = requests.exceptions.ConnectionError("down")

        self.assertIsNone(hardcover.goodreads_id("427578"))
