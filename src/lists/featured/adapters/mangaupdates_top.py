"""MangaUpdates series search ordered by rating, with MangaUpdates ids.

Params: ``{"orderby": "rating", "type": ["Manga"], "limit": 50}``. One POST to
``/v1/series/search`` (no key). Adult, Hentai and Doujinshi are excluded unless
``MAL_NSFW`` is on, like Spine's MangaUpdates search. ``"min_entries"`` is the series a
complete page has (the limit); fewer fail the run.
"""

from django.conf import settings

from app.providers import mangaupdates
from lists.featured.adapters import _rows
from lists.featured.helpers import post_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

ORDERS = ("rating", "score", "year", "title")
TYPES = ("Manga", "Manhwa", "Manhua", "Novel", "Doujinshi", "Artbook", "OEL")
MAX_LIMIT = 100


def validate(definition):
    """Check the params without network."""
    params = definition.params
    if params.get("orderby") not in ORDERS:
        msg = f"orderby must be one of {ORDERS}"
        raise ValueError(msg)
    types = params.get("type") or []
    if not isinstance(types, list) or not types or any(value not in TYPES for value in types):
        msg = f"type must be a non-empty list of {TYPES}"
        raise ValueError(msg)
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        msg = f"limit must be an integer from 1 to {MAX_LIMIT}"
        raise ValueError(msg)
    if set(definition.media_types) != {"manga"}:
        msg = "MangaUpdates lists are manga lists"
        raise ValueError(msg)
    _rows.validate(definition, limit)


def _year(value):
    text = str(value or "")[:4]
    return int(text) if text.isdigit() else None


def fetch(definition):
    """Return the series in MangaUpdates' order."""
    params = definition.params
    body = {
        "orderby": params["orderby"],
        "type": list(params["type"]),
        "perpage": params.get("limit", 50),
        "page": 1,
    }
    if not settings.MAL_NSFW:
        body["exclude_genre"] = ["Adult", "Hentai", "Doujinshi"]
    data = post_json("mangaupdates", f"{mangaupdates.base_url}/series/search", json=body)
    rows = data.get("results") if isinstance(data, dict) else None
    if not rows:
        msg = "MangaUpdates returned no series."
        raise AdapterError(msg)
    entries = []
    for rank, row in enumerate(rows, start=1):
        record = (row.get("record") or {}) if isinstance(row, dict) else {}
        if not record.get("series_id"):
            continue
        image = ((record.get("image") or {}).get("url") or {}).get("original") or ""
        entries.append(
            Entry(
                rank=rank,
                title=record.get("title") or "",
                year=_year(record.get("year")),
                media_type="manga",
                ids={"mangaupdates": record["series_id"]},
                image=image,
            ),
        )
    _rows.require(definition, len(entries), "MangaUpdates' series search")
    return FetchResult(entries=entries)


ADAPTER = Adapter(key="mangaupdates_top", fetch=fetch, validate=validate)
