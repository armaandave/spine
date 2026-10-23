"""Open Library's trending books (``/trending/<period>.json``), as editions.

Params: ``{"period": "daily" | "weekly" | "monthly", "limit": 50}``. Open Library ranks
works, but Spine stores Open Library editions, so each work becomes its
``cover_edition_key``, or else the first edition the response lists. A work with
neither is left to the strict title + author search. ``"min_entries"`` is the fewest
books a complete response has; fewer fail the run.
"""

from lists.featured.adapters import _rows
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

BASE_URL = "https://openlibrary.org/trending"
PERIODS = ("daily", "weekly", "monthly")
MAX_LIMIT = 100


def validate(definition):
    """Check the params without network."""
    params = definition.params
    if params.get("period") not in PERIODS:
        msg = f"period must be one of {PERIODS}"
        raise ValueError(msg)
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        msg = f"limit must be an integer from 1 to {MAX_LIMIT}"
        raise ValueError(msg)
    if set(definition.media_types) != {"book"}:
        msg = "Open Library trending is a book list"
        raise ValueError(msg)
    _rows.validate(definition, limit)


def edition_key(work):
    """Return the work's cover edition (``OL…M``), else its first listed edition, else ""."""
    candidates = [work.get("cover_edition_key")]
    editions = work.get("editions")
    if isinstance(editions, dict):
        candidates += [doc.get("key") for doc in editions.get("docs") or [] if isinstance(doc, dict)]
    for value in candidates:
        key = str(value or "").rsplit("/", 1)[-1].upper()
        if key.startswith("OL") and key.endswith("M"):
            return key
    return ""


def fetch(definition):
    """Return the trending works as editions, in Open Library's order."""
    params = definition.params
    data = get_json(
        "openlibrary",
        f"{BASE_URL}/{params['period']}.json",
        params={"limit": params.get("limit", 50)},
    )
    works = data.get("works") if isinstance(data, dict) else None
    if not works:
        msg = "Open Library returned no trending books."
        raise AdapterError(msg)
    entries = []
    for rank, work in enumerate(works, start=1):
        if not isinstance(work, dict) or not work.get("title"):
            continue
        olid = edition_key(work)
        authors = work.get("author_name") or []
        entries.append(
            Entry(
                rank=rank,
                title=work["title"],
                year=work.get("first_publish_year"),
                creator=", ".join(authors[:2]) if isinstance(authors, list) else str(authors),
                media_type="book",
                ids={"olid": olid} if olid else {},
                image=f"https://covers.openlibrary.org/b/id/{work['cover_i']}-L.jpg" if work.get("cover_i") else "",
            ),
        )
    _rows.require(definition, len(entries), f"Open Library's {params['period']} trending books")
    return FetchResult(entries=entries)


ADAPTER = Adapter(key="openlibrary_trending", fetch=fetch, validate=validate)
