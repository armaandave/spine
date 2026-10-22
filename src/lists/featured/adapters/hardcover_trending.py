"""Hardcover's trending books (GraphQL ``books_trending``), with Hardcover ids.

Params: ``{"duration": "month", "limit": 25}``; ``duration`` is one of Hardcover's
``TrendingDuration`` values (``week``, ``month``, ``three_month``, ``one_year``, ``all``).
Two requests: the ranked ids, then their titles, authors, years and covers, so new Items
need no further provider call.
"""

from django.conf import settings

from app.providers import hardcover
from lists.featured.helpers import post_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

DURATIONS = ("week", "month", "three_month", "one_year", "all")
MAX_LIMIT = 100
TRENDING_QUERY = """
query Trending($limit: Int!) {
  books_trending(limit: $limit, offset: 0, duration: %s) { ids error }
}
"""
BOOKS_QUERY = """
query TrendingBooks($ids: [Int!]) {
  books(where: {id: {_in: $ids}}) {
    id
    title
    release_year
    cached_image(path: "url")
    author_name: cached_contributors(path: "[0]['author']['name']")
  }
}
"""


def validate(definition):
    """Check the params without network."""
    params = definition.params
    if params.get("duration", "month") not in DURATIONS:
        msg = f"duration must be one of {DURATIONS}"
        raise ValueError(msg)
    limit = params.get("limit", 25)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        msg = f"limit must be an integer from 1 to {MAX_LIMIT}"
        raise ValueError(msg)
    if set(definition.media_types) != {"book"}:
        msg = "Hardcover trending is a book list"
        raise ValueError(msg)


def _graphql(query, variables):
    if not settings.HARDCOVER_API:
        msg = "Hardcover API key is not configured."
        raise AdapterError(msg)
    data = post_json(
        "hardcover",
        hardcover.base_url,
        json={"query": query, "variables": variables},
        headers={"Authorization": settings.HARDCOVER_API},
    )
    if not isinstance(data, dict) or data.get("errors") or not isinstance(data.get("data"), dict):
        msg = "Hardcover returned a GraphQL error."
        raise AdapterError(msg)
    return data["data"]


def fetch(definition):
    """Return the trending books in Hardcover's order."""
    params = definition.params
    duration = params.get("duration", "month")
    trending = _graphql(TRENDING_QUERY % duration, {"limit": params.get("limit", 25)}).get("books_trending") or {}
    if trending.get("error"):
        msg = "Hardcover's trending query failed."
        raise AdapterError(msg)
    ids = [int(book_id) for book_id in trending.get("ids") or [] if str(book_id).isdigit()]
    if not ids:
        msg = "Hardcover returned no trending books."
        raise AdapterError(msg)
    books = {str(book.get("id")): book for book in _graphql(BOOKS_QUERY, {"ids": ids}).get("books") or []}
    entries = []
    for rank, book_id in enumerate(ids, start=1):
        book = books.get(str(book_id))
        if book is None:  # removed or merged since the trending job ran
            continue
        entries.append(
            Entry(
                rank=rank,
                title=book.get("title") or "",
                year=book.get("release_year"),
                creator=book.get("author_name") or "",
                media_type="book",
                ids={"hardcover": book_id},
                image=book.get("cached_image") or "",
            ),
        )
    return FetchResult(entries=entries)


ADAPTER = Adapter(key="hardcover_trending", fetch=fetch, validate=validate)
