"""MyAnimeList anime charts (MAL API v2, ``MAL_API`` client id).

Params (one mode):

- ``{"ranking": "all" | "airing" | "bypopularity"}``: ``anime/ranking``.
- ``{"season": "current"}``: ``anime/season/{year}/{season}`` for today's season,
  by members, keeping only shows that premiere that season (MAL's season list also
  carries long-running shows such as One Piece). The edition reads "Fall 2026".

Optional ``"limit"`` (default 100, at most 500). MAL ids are per season, like
Spine's anime Items, so they resolve directly. ``"min_entries"`` is the fewest entries
a complete chart has (the limit for rankings, a floor for a season); fewer fail the run.
"""

from django.conf import settings
from django.utils import timezone

from lists.featured.adapters import _rows
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://api.myanimelist.net/v2/anime"
RANKINGS = ("all", "airing", "bypopularity")
SEASONS = ("winter", "spring", "summer", "fall")  # Jan–Mar, Apr–Jun, Jul–Sep, Oct–Dec
FIELDS = "start_date,start_season,studios"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


def validate(definition):
    """Check the params without network."""
    params = definition.params
    modes = [mode for mode in ("ranking", "season") if params.get(mode)]
    if len(modes) != 1:
        msg = "set exactly one of ranking or season"
        raise ValueError(msg)
    if params.get("ranking") and params["ranking"] not in RANKINGS:
        msg = f"ranking must be one of {RANKINGS}"
        raise ValueError(msg)
    if params.get("season") and params["season"] != "current":
        msg = 'season must be "current"'
        raise ValueError(msg)
    if not 1 <= int(params.get("limit", DEFAULT_LIMIT)) <= MAX_LIMIT:
        msg = f"limit must be between 1 and {MAX_LIMIT}"
        raise ValueError(msg)
    if tuple(definition.media_types) != ("anime",):
        msg = "MAL anime charts are anime lists"
        raise ValueError(msg)
    _rows.validate(definition, params.get("limit", DEFAULT_LIMIT))


def current_season(today):
    """Return ``(year, season)`` for a date."""
    return today.year, SEASONS[(today.month - 1) // 3]


def _year(node):
    start = str(node.get("start_date") or "")
    return int(start[:4]) if start[:4].isdigit() else (node.get("start_season") or {}).get("year")


def entries_from_nodes(nodes):
    """Turn MAL nodes (in chart order) into entries ranked 1..n."""
    entries = []
    for node in nodes:
        if not node.get("id"):
            continue
        studios = node.get("studios") or []
        entries.append(
            Entry(
                rank=len(entries) + 1,
                title=str(node.get("title") or ""),
                year=_year(node),
                creator=", ".join(studio["name"] for studio in studios if studio.get("name")),
                media_type="anime",
                ids={"mal": node["id"]},
                image=(node.get("main_picture") or {}).get("large") or "",
            ),
        )
    return entries


def _get(path, params):
    data = get_json("mal", f"{URL}/{path}", params=params, headers={"X-MAL-CLIENT-ID": settings.MAL_API})
    if not isinstance(data, dict) or not data.get("data"):
        msg = f"MyAnimeList returned no anime for {path}."
        raise AdapterError(msg)
    return [row.get("node") or {} for row in data["data"]]


def fetch(definition):
    """Fetch one chart."""
    params = definition.params
    limit = int(params.get("limit", DEFAULT_LIMIT))
    if params.get("ranking"):
        nodes = _get("ranking", {"ranking_type": params["ranking"], "limit": limit, "fields": FIELDS})
        entries = entries_from_nodes(nodes)
        _rows.require(definition, len(entries), f"MyAnimeList's {params['ranking']} ranking")
        return FetchResult(entries=entries)
    year, season = current_season(timezone.localdate())
    nodes = _get(
        f"season/{year}/{season}",
        {"sort": "anime_num_list_users", "limit": MAX_LIMIT, "fields": FIELDS},
    )
    premieres = [node for node in nodes if node.get("start_season") == {"year": year, "season": season}]
    entries = entries_from_nodes(premieres[:limit])
    _rows.require(definition, len(entries), f"MyAnimeList's {season} {year} premieres")
    return FetchResult(entries=entries, edition=f"{season.title()} {year}")


ADAPTER = Adapter(key="mal_anime", fetch=fetch, validate=validate)
