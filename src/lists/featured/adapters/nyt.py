"""One New York Times Best Sellers chart.

Params: ``{"slug": "combined-print-and-e-book-fiction", "fallback_name": "…"}``, plus
``"monthly": True`` for charts NYT now publishes monthly (the edition reads "October 2026").
Entries carry every ISBN the chart lists, so the resolver tries Hardcover then Open
Library per ISBN before a strict title + author search. ``"min_entries"`` is the
fewest books a complete chart has; fewer fail the run.
"""

from datetime import date

from app.providers import nytbooks
from lists.featured.adapters import _rows
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult


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


def _note(book):
    weeks = book.get("weeks_on_list")
    last = book.get("rank_last_week")
    if weeks == 1 or last == 0:
        return "New this week"
    if last and last != book["rank"]:
        change = last - book["rank"]
        return f"▲{change}" if change > 0 else f"▼{-change}"
    if weeks:
        return f"{weeks} weeks on the list"
    return ""


def edition_label(published_date, *, monthly=False):
    """Return "Week of Oct 25, 2026" (or "October 2026") for an ISO publication date."""
    try:
        published = date.fromisoformat(published_date)
    except (TypeError, ValueError):
        return ""
    if monthly:
        return f"{published:%B} {published.year}"
    return f"Week of {published:%b} {published.day}, {published.year}"


def fetch_result_from_chart(chart, *, monthly=False):
    """Turn ``nytbooks.get_current_chart()`` output into a ``FetchResult``."""
    published = chart.get("published_date") or ""
    entries = [
        Entry(
            rank=book["rank"],
            title=book.get("title") or "",
            creator=book.get("author") or "",
            media_type="book",
            isbns=tuple(book.get("isbns") or ()),
            image=book.get("image") or "",
            note=_note(book),
        )
        for book in chart["books"]
    ]
    return FetchResult(
        entries=entries,
        edition=edition_label(published, monthly=monthly),
        name=chart.get("name") or None,
        description=(
            f"The New York Times Best Sellers list published {published}."
            if published
            else "The current New York Times Best Sellers list."
        ),
    )


def fetch(definition):
    """Fetch the current chart."""
    params = definition.params
    try:
        chart = nytbooks.get_current_chart(
            params["slug"],
            params.get("fallback_name") or definition.name,
        )
    except nytbooks.NYTBooksError as error:
        raise AdapterError(str(error)) from None
    _rows.require(definition, len(chart.get("books") or ()), f"NYT's {params['slug']} chart")
    return fetch_result_from_chart(chart, monthly=bool(params.get("monthly")))


ADAPTER = Adapter(key="nyt", fetch=fetch, validate=validate)
