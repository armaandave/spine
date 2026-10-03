from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from api.services.media import (
    _add_google_books_rating,
    _enrich_book_metadata,
    _google_books_volume,
    book_cover_options,
)
from app.models import Item, MediaTypes, Sources
from app.providers import services as provider_services


class GoogleBooksMetadataTests(SimpleTestCase):
    """Verify request-local Google metadata merging."""

    google = {
        "matched_isbn": "9780439708180",
        "volume_id": "hp1",
        "canonical_url": "https://books.google.com/books?id=hp1",
        "title": "Google Title",
        "subtitle": "Book One",
        "description": "Google synopsis",
        "authors": ["Google Author"],
        "publisher": "Google Publisher",
        "published_date": "1998-09-01",
        "page_count": 309,
        "categories": ["Fantasy"],
        "language": "en",
        "maturity_rating": "NOT_MATURE",
        "rating": {"value": "4.5", "count": 125},
        "price": {"amount": "12.99", "currency": "USD", "country": "US"},
    }

    @patch("api.services.media._google_books_volume")
    def test_google_only_fills_empty_primary_metadata(self, lookup_mock):
        lookup_mock.return_value = self.google
        primary = {
            "title": "Primary Title",
            "subtitle": "",
            "synopsis": "No synopsis available.",
            "genres": ["Primary Genre"],
            "max_progress": None,
            "details": {
                "authors": [{"name": "Primary Author"}],
                "publishers": [],
                "isbn": ["9780439708180"],
                "series_name": "Primary Series",
                "series_position": 1,
            },
        }

        result = _enrich_book_metadata(primary, Sources.HARDCOVER.value)

        self.assertEqual(result["title"], "Primary Title")
        self.assertEqual(result["subtitle"], "Book One")
        self.assertEqual(result["synopsis"], "Google synopsis")
        self.assertEqual(result["genres"], ["Primary Genre"])
        self.assertEqual(result["max_progress"], 309)
        self.assertEqual(result["details"]["authors"], [{"name": "Primary Author"}])
        self.assertEqual(result["details"]["publishers"], ["Google Publisher"])
        self.assertEqual(result["details"]["series_name"], "Primary Series")
        self.assertEqual(result["details"]["series_position"], 1)
        self.assertEqual(result["details"]["maturity_rating"], "Not Mature")
        self.assertEqual(result["details"]["google_books_price_amount"], "12.99")
        self.assertEqual(
            result["external_links"]["google_books"],
            self.google["canonical_url"],
        )
        self.assertNotIn("cover_url", result)
        self.assertEqual(primary["subtitle"], "")

    def test_google_rating_uses_existing_external_rating_shape(self):
        result = _add_google_books_rating(
            {"external_ratings": [], "external_ratings_preparation": {"state": "ready"}},
            self.google,
        )

        self.assertEqual(
            result["external_ratings"],
            [
                {
                    "source": "Google Books",
                    "value": "4.5",
                    "vote_count": 125,
                    "max_value": "5",
                    "url": self.google["canonical_url"],
                },
            ],
        )

    @patch("api.services.media._google_books_volume")
    def test_primary_aliases_block_google_fallbacks(self, lookup_mock):
        lookup_mock.return_value = self.google
        primary = {
            "details": {
                "author": "Primary Author",
                "publisher": "Primary Publisher",
                "release_date": "1997-06-26",
                "pages": 223,
                "language": "English",
                "genres": ["Primary Genre"],
                "isbn": ["9780439708180"],
            },
        }

        result = _enrich_book_metadata(primary, Sources.OPENLIBRARY.value)

        self.assertNotIn("authors", result["details"])
        self.assertNotIn("publishers", result["details"])
        self.assertNotIn("publish_date", result["details"])
        self.assertNotIn("number_of_pages", result["details"])
        self.assertNotIn("languages", result["details"])
        self.assertNotIn("genres", result)

    @patch(
        "api.services.media.googlebooks.lookup_volume",
        side_effect=provider_services.ProviderAPIError("google books", "timeout"),
    )
    def test_provider_failure_returns_no_enrichment(self, _lookup_mock):
        self.assertIsNone(
            _google_books_volume({"details": {"isbn": ["9780439708180"]}}),
        )


class GoogleBooksCoverTests(TestCase):
    """Verify Google cover picker integration."""

    @patch("api.services.media._google_books_volume")
    @patch("api.services.media.provider_services.get_media_metadata")
    @patch("api.services.media._book_cover_candidates", return_value=[])
    @patch("api.services.media._book_isbns", return_value=["9780439708180"])
    def test_cover_picker_adds_one_attributed_google_cover_without_selecting_it(
        self,
        _isbns_mock,
        _cover_candidates_mock,
        metadata_mock,
        google_mock,
    ):
        item = Item.objects.create(
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            media_id="OL1M",
            title="Book",
            image="https://example.com/current.jpg",
        )
        metadata_mock.return_value = {"details": {"isbn": ["9780439708180"]}}
        google_mock.return_value = {
            "cover_url": "https://books.google.com/largest.jpg",
            "canonical_url": "https://books.google.com/books?id=hp1",
        }

        result = book_cover_options(
            source=Sources.OPENLIBRARY.value,
            media_id=item.media_id,
        )

        self.assertEqual(len(result["posters"]), 2)
        google_cover = result["posters"][1]
        self.assertEqual(google_cover["url"], "https://books.google.com/largest.jpg")
        self.assertEqual(google_cover["provider_name"], "Google Books")
        self.assertEqual(
            google_cover["provider_url"],
            "https://books.google.com/books?id=hp1",
        )
        self.assertFalse(google_cover["is_selected"])


class GoogleBooksDetailIntegrationTests(TestCase):
    """Verify detail API wiring without persisting Google metadata."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @patch("api.services.media.update_item_filter_metadata")
    @patch("api.services.media._google_books_volume")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_detail_exposes_google_enrichment_but_persists_primary_only(
        self,
        metadata_mock,
        google_mock,
        update_filter_mock,
    ):
        primary = {
            "media_id": "OL-GOOGLE-M",
            "media_type": MediaTypes.BOOK.value,
            "source": Sources.OPENLIBRARY.value,
            "title": "Primary Title",
            "details": {"isbn": ["9780439708180"]},
        }
        metadata_mock.return_value = primary
        google_mock.return_value = {
            "matched_isbn": "9780439708180",
            "volume_id": "hp1",
            "canonical_url": "https://books.google.com/books?id=hp1",
            "description": "Google synopsis",
            "maturity_rating": "NOT_MATURE",
            "rating": {"value": "4.5", "count": 125},
            "price": {"amount": "12.99", "currency": "USD", "country": "US"},
        }

        item = Item.objects.create(
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            media_id="OL-GOOGLE-M",
            title="Primary Title",
        )

        response = self.client.get(
            "/api/v1/media/openlibrary/book/OL-GOOGLE-M/",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["synopsis"], "Google synopsis")
        self.assertEqual(
            response.data["external_links"]["google_books"],
            "https://books.google.com/books?id=hp1",
        )
        self.assertEqual(response.data["external_ratings"][-1]["source"], "Google Books")
        self.assertEqual(response.data["details"]["maturity_rating"], "Not Mature")
        self.assertNotIn("_google_books", response.data)
        self.assertEqual(update_filter_mock.call_args.args[0], item)
        self.assertIs(update_filter_mock.call_args.args[1], primary)
