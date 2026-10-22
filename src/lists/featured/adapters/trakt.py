"""Trakt charts: the US weekend box office and trending movies or shows.

Params: ``{"chart": "boxoffice"}`` (movies only; Trakt updates it on Mondays), or
``{"chart": "trending", "media": "movies" | "shows", "limit": 20}``.

Requests send ``trakt-api-key`` (``settings.TRAKT_API``) and ``trakt-api-version: 2``.
Every entry carries Trakt's TMDB and IMDb ids. ``"min_entries"`` is the rows a complete
chart has (10 for the box office, the limit for trending); fewer fail the run.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from lists.featured.adapters import _rows
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

BASE_URL = "https://api.trakt.tv"
MEDIA = {"movies": ("movie", "movie"), "shows": ("show", "tv")}  # path: (payload key, Spine type)
CHARTS = ("boxoffice", "trending")
MAX_LIMIT = 100
FRIDAY, SUNDAY = 4, 6


def validate(definition):
    """Check the params without network."""
    params = definition.params
    chart = params.get("chart")
    if chart not in CHARTS:
        msg = f"chart must be one of {CHARTS}"
        raise ValueError(msg)
    media = "movies" if chart == "boxoffice" else params.get("media")
    if media not in MEDIA:
        msg = f"media must be one of {sorted(MEDIA)}"
        raise ValueError(msg)
    if chart == "boxoffice" and params.get("media", "movies") != "movies":
        msg = "the box office chart holds movies only"
        raise ValueError(msg)
    if not 1 <= int(params.get("limit", 20)) <= MAX_LIMIT:
        msg = f"limit must be between 1 and {MAX_LIMIT}"
        raise ValueError(msg)
    if MEDIA[media][1] not in definition.media_types:
        msg = f"Trakt {media} need media type {MEDIA[media][1]}"
        raise ValueError(msg)
    _rows.validate(definition, None if chart == "boxoffice" else params.get("limit", 20))


def weekend_label(today):
    """Return "Weekend of Oct 16, 2026" for the last Friday-Sunday weekend before ``today``."""
    days_since_sunday = (today.weekday() - SUNDAY) % 7 or 7
    friday = today - timedelta(days=days_since_sunday + (SUNDAY - FRIDAY))
    return f"Weekend of {friday:%b} {friday.day}, {friday.year}"


def _revenue_note(revenue):
    if not isinstance(revenue, (int, float)) or revenue <= 0:
        return ""
    if revenue >= 1_000_000:  # noqa: PLR2004
        return f"${revenue / 1_000_000:.1f}M"
    return f"${revenue / 1_000:.0f}K"


def fetch(definition):
    """Fetch one Trakt chart."""
    if not settings.TRAKT_API:
        msg = "TRAKT_API is not configured."
        raise AdapterError(msg)
    params = definition.params
    chart = params["chart"]
    media = "movies" if chart == "boxoffice" else params["media"]
    key, media_type = MEDIA[media]
    query = None if chart == "boxoffice" else {"limit": int(params.get("limit", 20))}
    rows = get_json(
        "trakt",
        f"{BASE_URL}/{media}/{chart}",
        params=query,
        headers={"trakt-api-key": settings.TRAKT_API, "trakt-api-version": "2", "Content-Type": "application/json"},
    )
    if not isinstance(rows, list) or not rows:
        msg = f"Trakt returned no {media} for {chart}."
        raise AdapterError(msg)
    _rows.require(definition, len(rows), f"Trakt's {chart} chart")
    entries = []
    for rank, row in enumerate(rows, start=1):
        item = row.get(key) or {}
        ids = item.get("ids") or {}
        entries.append(
            Entry(
                rank=rank,
                title=item.get("title") or "",
                year=item.get("year"),
                media_type=media_type,
                ids={"tmdb": ids.get("tmdb"), "imdb": ids.get("imdb")},
                note=_revenue_note(row.get("revenue")) if chart == "boxoffice" else "",
            ),
        )
    edition = weekend_label(timezone.localdate()) if chart == "boxoffice" else ""
    return FetchResult(entries=entries, edition=edition)


ADAPTER = Adapter(key="trakt", fetch=fetch, validate=validate)
