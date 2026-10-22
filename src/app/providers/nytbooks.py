"""New York Times Books API helpers."""

import requests
from django.conf import settings

from app.providers import services

BASE_URL = "https://api.nytimes.com/svc/books/v3"
FEATURED_CHARTS = (
    {
        "slug": "combined-print-and-e-book-fiction",
        "fallback_name": "Combined Print & E-Book Fiction",
        "display_name": "NYT Fiction Bestsellers",
        "featured_position": 2,
    },
    {
        "slug": "combined-print-and-e-book-nonfiction",
        "fallback_name": "Combined Print & E-Book Nonfiction",
        "display_name": "NYT Nonfiction Bestsellers",
        "featured_position": 3,
    },
    {
        "slug": "advice-how-to-and-miscellaneous",
        "fallback_name": "Advice, How-To & Miscellaneous",
        "display_name": "NYT Advice Bestsellers",
        "featured_position": 4,
    },
    {
        "slug": "business-books",
        "fallback_name": "Business Books",
        "display_name": "NYT Business Bestsellers",
        "featured_position": 5,
    },
    {
        "slug": "graphic-books-and-manga",
        "fallback_name": "Graphic Books and Manga",
        "display_name": "NYT Graphic & Manga Bestsellers",
        "featured_position": 6,
    },
)


class NYTBooksError(Exception):
    """A sanitized NYT Books provider failure."""


def _isbn_candidates(book):
    values_13 = [book.get("primary_isbn13")]
    values_10 = [book.get("primary_isbn10")]
    for identifiers in book.get("isbns") or []:
        if isinstance(identifiers, dict):
            values_13.append(identifiers.get("isbn13"))
            values_10.append(identifiers.get("isbn10"))

    seen = set()
    candidates = []
    for value in (*values_13, *values_10):
        normalized = str(value or "").strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)
    return candidates


def _optional_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_current_chart(slug, fallback_name):
    """Return one normalized current NYT Best Sellers chart."""
    if not settings.NYT_BOOKS_API_KEY:
        raise NYTBooksError("NYT Books API key is not configured.")

    try:
        payload = services.api_request(
            "nyt books",
            "GET",
            f"{BASE_URL}/lists/current/{slug}.json",
            params={"api-key": settings.NYT_BOOKS_API_KEY},
        )
    except requests.RequestException as error:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        raise NYTBooksError(f"NYT Books API request failed{suffix}.") from None

    results = payload.get("results") if isinstance(payload, dict) else None
    books = results.get("books") if isinstance(results, dict) else None
    if not isinstance(books, list) or not books:
        raise NYTBooksError(f"NYT Books returned a malformed or empty chart for {slug}.")

    normalized_books = []
    for book in books:
        if not isinstance(book, dict):
            continue
        try:
            rank = int(book.get("rank"))
        except (TypeError, ValueError):
            continue
        isbns = _isbn_candidates(book)
        if rank > 0:
            normalized_books.append(
                {
                    "rank": rank,
                    "title": str(book.get("title") or "").strip(),
                    "isbns": isbns,
                    "author": str(book.get("author") or "").strip(),
                    "image": str(book.get("book_image") or "").strip(),
                    "rank_last_week": _optional_int(book.get("rank_last_week")),
                    "weeks_on_list": _optional_int(book.get("weeks_on_list")),
                },
            )

    if not normalized_books:
        raise NYTBooksError(f"NYT Books returned no ranked ISBN entries for {slug}.")

    return {
        "slug": str(results.get("list_name_encoded") or slug),
        "name": str(
            results.get("display_name")
            or results.get("list_name")
            or fallback_name,
        ).strip(),
        "published_date": str(
            results.get("published_date")
            or results.get("best_sellers_date")
            or "",
        ).strip(),
        "books": sorted(normalized_books, key=lambda book: book["rank"]),
    }
