"""Steam's public charts, no key needed.

Params:

- ``{"chart": "weekly_top_sellers", "country": "US"}``: last week's best sellers
  (``IStoreTopSellersService/GetWeeklyTopSellers``), edition "Week of Oct 13, 2026".
- ``{"chart": "most_played"}``: yesterday's most played games by peak concurrent
  players (``ISteamChartsService/GetMostPlayedGames``), edition "Oct 20, 2026".

Optional ``"limit"`` (at most and by default 100). Only games are kept: hardware, software, DLC,
bundles, packages and preorders are dropped, and the rest are ranked 1..n. Appids
are matched to IGDB in one batched ``external_games`` request; an appid IGDB doesn't
know keeps its ``steam`` id for the resolver. ``"min_entries"`` is the fewest games a
complete chart keeps (about 90 of Steam's 100 rows are games); fewer fail the run.
"""

import json
from datetime import UTC, datetime

from app.providers import igdb
from lists.featured.adapters import _igdb_games, _rows
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

API = "https://api.steampowered.com"
CHARTS = {
    "weekly_top_sellers": f"{API}/IStoreTopSellersService/GetWeeklyTopSellers/v1/",
    "most_played": f"{API}/ISteamChartsService/GetMostPlayedGames/v1/",
}
GAME_APP_TYPE = 0  # Steam store item "type": 0 game, 4 DLC, 6 software, 10 hardware…
APP_ITEM_TYPE = 0  # Steam store "item_type": 0 app; packages and bundles differ
DEFAULT_LIMIT = 100
# Appids IGDB's external_games maps wrongly or not at all, checked by hand.
IGDB_CORRECTIONS = {
    "1329410": "117263",  # 雀魂麻將(MahjongSoul), a regional app; IGDB's Mahjong Soul lists only 2739990
}


def validate(definition):
    """Check the params without network."""
    params = definition.params
    if params.get("chart") not in CHARTS:
        msg = f"chart must be one of {sorted(CHARTS)}"
        raise ValueError(msg)
    country = params.get("country", "US")
    if len(country) != 2 or not country.isupper():  # noqa: PLR2004
        msg = "country must be a two-letter upper-case code"
        raise ValueError(msg)
    if not 1 <= int(params.get("limit", DEFAULT_LIMIT)) <= DEFAULT_LIMIT:
        msg = f"limit must be between 1 and {DEFAULT_LIMIT}"
        raise ValueError(msg)
    if tuple(definition.media_types) != ("game",):
        msg = "Steam charts are game lists"
        raise ValueError(msg)
    _rows.validate(definition, params.get("limit", DEFAULT_LIMIT))


def is_game(row):
    """Return whether a chart row is a released game (not hardware, DLC or a preorder)."""
    if not row.get("appid"):
        return False  # a package or bundle row
    item = row.get("item")
    if not isinstance(item, dict):
        return True  # Steam sent no store data; let the resolver decide
    return (
        item.get("item_type", APP_ITEM_TYPE) == APP_ITEM_TYPE
        and item.get("type", GAME_APP_TYPE) == GAME_APP_TYPE
        and not item.get("is_coming_soon")
    )


def _day(timestamp):
    if not isinstance(timestamp, int) or timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, UTC).date()


def _input_json(params):
    country = params.get("country", "US")
    body = {
        "context": {"language": "english", "country_code": country},
        "data_request": {"include_basic_info": True},
    }
    if params["chart"] == "weekly_top_sellers":
        body.update({"country_code": country, "page_start": 0, "page_count": DEFAULT_LIMIT})
    return {"input_json": json.dumps(body, separators=(",", ":"))}


def entries_from_rows(rows, games, limit=DEFAULT_LIMIT):
    """Turn chart rows into entries, ranked 1..n among the games kept."""
    entries = []
    ordered = sorted((row for row in rows if is_game(row)), key=lambda row: row.get("rank") or 0)
    for row in ordered[:limit]:
        appid = str(row["appid"])
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        game = games.get(appid)
        entries.append(
            Entry(
                rank=len(entries) + 1,
                title=(game.name if game and game.name else item.get("name")) or "",
                year=game.year if game else None,
                media_type="game",
                ids={"steam": appid, "igdb": game.igdb_id if game else None},
                image=game.image if game else "",
            ),
        )
    return entries


def fetch(definition):
    """Fetch one chart and match its appids to IGDB."""
    params = definition.params
    chart = params["chart"]
    data = get_json("steam", CHARTS[chart], params=_input_json(params))
    response = (data or {}).get("response") if isinstance(data, dict) else None
    rows = (response or {}).get("ranks")
    if not rows:
        msg = f"Steam returned no {chart.replace('_', ' ')} chart."
        raise AdapterError(msg)
    appids = [str(row["appid"]) for row in rows if is_game(row)]
    games = _igdb_games.games_by_external(appids, igdb.ExternalGameSource.STEAM, IGDB_CORRECTIONS)
    limit = int(params.get("limit", DEFAULT_LIMIT))
    if chart == "weekly_top_sellers":
        start = _day(response.get("start_date"))
        edition = f"Week of {start:%b} {start.day}, {start.year}" if start else ""
    else:
        day = _day(response.get("rollup_date"))
        edition = f"{day:%b} {day.day}, {day.year}" if day else ""
    entries = entries_from_rows(rows, games, limit)
    _rows.require(definition, len(entries), f"Steam's {chart.replace('_', ' ')} chart")
    return FetchResult(entries=entries, edition=edition)


ADAPTER = Adapter(key="steam", fetch=fetch, validate=validate)
