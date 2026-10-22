"""MyAnimeList manga rankings (MAL API v2 ``manga/ranking``), with MAL ids.

Params: ``{"ranking_type": "manga" | "bypopularity" | …, "limit": 50}``. Needs the
``MAL_API`` client id, sent as ``X-MAL-CLIENT-ID``. NSFW entries are left out unless
``MAL_NSFW`` is on, like Spine's MAL search.
"""

from django.conf import settings

from app.providers import mal
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

RANKING_TYPES = ("all", "manga", "novels", "oneshots", "doujin", "manhwa", "manhua", "bypopularity", "favorite")
MAX_LIMIT = 500
FIELDS = "start_date,authors{first_name,last_name}"


def validate(definition):
    """Check the params without network."""
    params = definition.params
    if params.get("ranking_type") not in RANKING_TYPES:
        msg = f"ranking_type must be one of {RANKING_TYPES}"
        raise ValueError(msg)
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        msg = f"limit must be an integer from 1 to {MAX_LIMIT}"
        raise ValueError(msg)
    if set(definition.media_types) != {"manga"}:
        msg = "MAL manga rankings are manga lists"
        raise ValueError(msg)


def _authors(node):
    names = []
    for credit in node.get("authors") or []:
        person = (credit.get("node") or {}) if isinstance(credit, dict) else {}
        name = " ".join(part for part in (person.get("first_name"), person.get("last_name")) if part)
        if name:
            names.append(name)
    return ", ".join(names[:2])


def _year(value):
    text = str(value or "")[:4]
    return int(text) if text.isdigit() else None


def fetch(definition):
    """Return the ranking in MAL's order."""
    if not settings.MAL_API:
        msg = "MAL client id is not configured."
        raise AdapterError(msg)
    params = {
        "ranking_type": definition.params["ranking_type"],
        "limit": definition.params.get("limit", 50),
        "fields": FIELDS,
    }
    if settings.MAL_NSFW:
        params["nsfw"] = "true"
    data = get_json(
        "mal",
        f"{mal.base_url}/manga/ranking",
        params=params,
        headers={"X-MAL-CLIENT-ID": settings.MAL_API},
    )
    rows = data.get("data") if isinstance(data, dict) else None
    if not rows:
        msg = "MyAnimeList returned an empty ranking."
        raise AdapterError(msg)
    entries = []
    for index, row in enumerate(rows, start=1):
        node = (row.get("node") or {}) if isinstance(row, dict) else {}
        if not node.get("id"):
            continue
        picture = node.get("main_picture") or {}
        entries.append(
            Entry(
                rank=(row.get("ranking") or {}).get("rank") or index,
                title=node.get("title") or "",
                year=_year(node.get("start_date")),
                creator=_authors(node),
                media_type="manga",
                ids={"mal": node["id"]},
                image=picture.get("large") or picture.get("medium") or "",
            ),
        )
    return FetchResult(entries=entries)


ADAPTER = Adapter(key="mal_manga", fetch=fetch, validate=validate)
