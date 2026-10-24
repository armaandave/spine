"""TMDB trending: ``/trending/{movie|tv}/{day|week}`` with the tmdb provider's key.

Params: ``{"media_type": "movie" | "tv", "window": "week", "min_entries": 20}``. One
page: 20 titles; fewer than ``min_entries`` fail the run.
"""

from app.providers import tmdb
from lists.featured.adapters import _rows
from lists.featured.helpers import get_json
from lists.featured.text import parse_year
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

MEDIA_TYPES = ("movie", "tv")
WINDOWS = ("day", "week")
PAGE_SIZE = 20


def validate(definition):
    """Check the params without network."""
    params = definition.params
    if params.get("media_type") not in MEDIA_TYPES:
        msg = f"media_type must be one of {MEDIA_TYPES}"
        raise ValueError(msg)
    if params.get("window", "week") not in WINDOWS:
        msg = f"window must be one of {WINDOWS}"
        raise ValueError(msg)
    if params["media_type"] not in definition.media_types:
        msg = "media_types must include the trending media type"
        raise ValueError(msg)
    _rows.validate(definition, PAGE_SIZE)


def fetch(definition):
    """Fetch one page of TMDB's trending titles."""
    media_type = definition.params["media_type"]
    window = definition.params.get("window", "week")
    data = get_json("tmdb", f"{tmdb.base_url}/trending/{media_type}/{window}", params=dict(tmdb.base_params))
    results = (data or {}).get("results") if isinstance(data, dict) else None
    if not results:
        msg = f"TMDB returned no trending {media_type} titles."
        raise AdapterError(msg)
    _rows.require(definition, len(results), f"TMDB's trending {media_type} page")
    entries = [
        Entry(
            rank=rank,
            title=result.get("title") or result.get("name") or "",
            year=parse_year(result.get("release_date") or result.get("first_air_date")),
            media_type=media_type,
            ids={"tmdb": result.get("id")},
            image=tmdb.get_image_url(result["poster_path"]) if result.get("poster_path") else "",
        )
        for rank, result in enumerate(results, start=1)
    ]
    return FetchResult(entries=entries)


ADAPTER = Adapter(key="tmdb_trending", fetch=fetch, validate=validate)
