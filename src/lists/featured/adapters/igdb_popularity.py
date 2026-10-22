"""IGDB's daily popularity charts (``popularity_primitives``).

Params: ``{"popularity_type": 10, "limit": 50}``. Useful types (``popularity_types``):

- 10 Most Wishlisted Upcoming (Steam wishlists)
- 3 Playing and 2 Want to Play (IGDB members' "playing" and "want to play" marks)

Steam's peak players (5) and global top sellers (9) are better served by the
``steam`` adapter directly, and Twitch hours watched (34) maps non-game categories
to games. Rows carry IGDB game ids; one ``games`` request adds names and covers.
"""

from datetime import UTC, datetime

from lists.featured.adapters import _igdb_games
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

TYPES = {
    2: "Want to Play",
    3: "Playing",
    10: "Most Wishlisted Upcoming",
}
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def validate(definition):
    """Check the params without network."""
    params = definition.params
    if params.get("popularity_type") not in TYPES:
        msg = f"popularity_type must be one of {sorted(TYPES)}"
        raise ValueError(msg)
    if not 1 <= int(params.get("limit", DEFAULT_LIMIT)) <= MAX_LIMIT:
        msg = f"limit must be between 1 and {MAX_LIMIT}"
        raise ValueError(msg)
    if tuple(definition.media_types) != ("game",):
        msg = "IGDB popularity charts are game lists"
        raise ValueError(msg)


def entries_from_rows(rows, games):
    """Turn primitives (highest value first) into entries ranked 1..n."""
    entries = []
    for row in rows:
        igdb_id = str(row.get("game_id") or "")
        if not igdb_id.isdigit():
            continue
        game = games.get(igdb_id)
        entries.append(
            Entry(
                rank=len(entries) + 1,
                title=game.name if game else "",
                year=game.year if game else None,
                media_type="game",
                ids={"igdb": igdb_id},
                image=game.image if game else "",
            ),
        )
    return entries


def fetch(definition):
    """Fetch one popularity chart."""
    params = definition.params
    popularity_type = int(params["popularity_type"])
    limit = int(params.get("limit", DEFAULT_LIMIT))
    rows = _igdb_games.query(
        "popularity_primitives",
        f"fields game_id,value,calculated_at; where popularity_type = {popularity_type}; "
        f"sort value desc; limit {limit};",
    )
    if not rows:
        msg = f"IGDB returned no {TYPES[popularity_type]} chart."
        raise AdapterError(msg)
    games = _igdb_games.games_by_ids(row.get("game_id") for row in rows)
    calculated = max((row.get("calculated_at") or 0 for row in rows), default=0)
    day = datetime.fromtimestamp(calculated, UTC).date() if calculated else None
    return FetchResult(
        entries=entries_from_rows(rows, games),
        edition=f"{day:%b} {day.day}, {day.year}" if day else "",
    )


ADAPTER = Adapter(key="igdb_popularity", fetch=fetch, validate=validate)
