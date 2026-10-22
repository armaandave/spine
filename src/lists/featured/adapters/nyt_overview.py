"""Every NYT Best Sellers chart the headline lists don't cover, from one overview call.

Params: ``{"slug": "hardcover-fiction"}``. NYT allows 5 requests a minute and about 500
a day, so every definition shares one ``lists/overview.json`` response, cached for
``OVERVIEW_TTL`` seconds: a dispatcher pass over all of them costs one request.

Weekly charts are labeled "Week of Nov 1, 2026" from the overview's publication date.
The overview carries no date for monthly charts (audio, business, graphic books…), so
they're labeled by the month NYT last updated them ("October 2026").

Entries carry every ISBN; the resolver tries Hardcover then Open Library per ISBN, then a
strict title + author search. ``"min_entries"`` is the fewest books a complete chart
has; fewer fail the run.
"""

import re
from datetime import date

from django.conf import settings
from django.core.cache import cache

from app.providers import nytbooks
from lists.featured.adapters import _rows
from lists.featured.adapters.nyt import edition_label
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

OVERVIEW_URL = f"{nytbooks.BASE_URL}/lists/overview.json"
CACHE_KEY = "featured:nyt:overview"
OVERVIEW_TTL = 30 * 60
# "HARRY POTTER AND THE SORCERER'S STONE (FULL-CAST EDITION)": the edition isn't the book.
_EDITION_SUFFIX = re.compile(r"\s*\([^()]*\bEDITION\b[^()]*\)\s*$", re.IGNORECASE)
_ISBN = re.compile(r"^(?:\d{9}[\dX]|\d{13})$")


def validate(definition):
    """Check the params without network."""
    slug = definition.params.get("slug")
    if not slug or not str(slug).replace("-", "").isalnum():
        msg = "slug must be an NYT list_name_encoded value"
        raise ValueError(msg)
    if set(definition.media_types) != {"book"}:
        msg = "NYT charts are book lists"
        raise ValueError(msg)
    _rows.validate(definition)


def overview():
    """Return the overview's ``results``, shared by every definition for a while."""
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    if not settings.NYT_BOOKS_API_KEY:
        msg = "NYT Books API key is not configured."
        raise AdapterError(msg)
    payload = get_json("nyt books", OVERVIEW_URL, params={"api-key": settings.NYT_BOOKS_API_KEY})
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict) or not isinstance(results.get("lists"), list) or not results["lists"]:
        msg = "NYT Books returned a malformed overview."
        raise AdapterError(msg)
    cache.set(CACHE_KEY, results, OVERVIEW_TTL)
    return results


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _note(book, rank, *, monthly):
    period = "month" if monthly else "week"
    weeks = _int(book.get("weeks_on_list"))
    last = _int(book.get("rank_last_week"))
    if weeks == 1 or last == 0:
        return f"New this {period}"
    if last and last != rank:
        change = last - rank
        return f"▲{change}" if change > 0 else f"▼{-change}"
    if weeks:
        return f"{weeks} {period}s on the list"
    return ""


def _author(book):
    """Return the author, repairing NYT's truncated one-word names from ``contributor``.

    NYT sends ``author: "Wm"`` with ``contributor: "by Wm. Paul Young"``.
    """
    author = str(book.get("author") or "").strip()
    contributor = str(book.get("contributor") or "").strip()
    if author and " " not in author and contributor.startswith(f"by {author}."):
        return contributor.removeprefix("by ").strip()
    return author


def _title(book):
    return _EDITION_SUFFIX.sub("", str(book.get("title") or "")).strip()


def _isbns(book):
    """Return the book's ISBNs; audio charts also list vendor ids like "DBKADBL054221"."""
    return tuple(value for value in nytbooks._isbn_candidates(book) if _ISBN.match(value))  # noqa: SLF001


def _monthly_edition(books):
    updated = [str(book.get("updated_date") or "")[:10] for book in books]
    for value in sorted(updated, reverse=True):
        try:
            return edition_label(date.fromisoformat(value).replace(day=1).isoformat(), monthly=True)
        except ValueError:
            continue
    return ""


def fetch(definition):
    """Return one chart from the shared overview."""
    slug = definition.params["slug"]
    results = overview()
    chart = next(
        (item for item in results["lists"] if isinstance(item, dict) and item.get("list_name_encoded") == slug),
        None,
    )
    if chart is None:
        msg = f"NYT's overview has no {slug} chart."
        raise AdapterError(msg)
    books = [book for book in chart.get("books") or [] if isinstance(book, dict)]
    monthly = str(chart.get("updated") or "").upper() == "MONTHLY"
    entries = []
    for book in books:
        rank = _int(book.get("rank"))
        if not rank or rank < 1:
            continue
        entries.append(
            Entry(
                rank=rank,
                title=_title(book),
                creator=_author(book),
                media_type="book",
                isbns=_isbns(book),
                image=str(book.get("book_image") or ""),
                note=_note(book, rank, monthly=monthly),
            ),
        )
    if not entries:
        msg = f"NYT's {slug} chart is empty."
        raise AdapterError(msg)
    _rows.require(definition, len(entries), f"NYT's {slug} chart")
    edition = _monthly_edition(books) if monthly else edition_label(results.get("published_date") or "")
    return FetchResult(entries=entries, edition=edition)


ADAPTER = Adapter(key="nyt_overview", fetch=fetch, validate=validate)
