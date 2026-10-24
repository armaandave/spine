"""Twitch's most-watched categories right now (Helix ``games/top``).

Params: ``{"limit": 100}`` (optional, at most 100). Authenticates with an app token
made from ``IGDB_ID``/``IGDB_SECRET``, which are Twitch client credentials.

Twitch categories that aren't games (Just Chatting, IRL, Music, Slots…) are dropped;
the rest are ranked 1..n. Most rows carry an ``igdb_id``. For the rest IGDB's
``external_games`` (Twitch source) is tried, then the resolver's strict title match.
``"min_entries"`` is the fewest games a complete response keeps; fewer fail the run.
"""

import re

from app.providers import igdb
from lists.featured.adapters import _igdb_games, _rows
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://api.twitch.tv/helix/games/top"
MAX_LIMIT = 100
_YEAR_SUFFIX = re.compile(r"^(?P<title>.+?)\s*\((?P<year>(?:19|20)\d\d)\)$")

# Twitch category ids that aren't video games, checked against Helix ``games?name=``
# on 2026-10-21. Some carry an igdb_id naming a game they were once linked to (I'm
# Only Sleeping, Crypto, Virtual Casino, Watch Party), so they are dropped by id.
NON_GAMES = {
    "743": "Chess",
    "2748": "Magic: The Gathering",
    "26936": "Music",
    "27284": "Retro",
    "29452": "Virtual Casino",
    "66082": "Games + Demos",
    "116747788": "Pools, Hot Tubs, and Beaches",
    "272263131": "Animals, Aquariums, and Zoos",
    "401050785": "Watch Party",
    "417752": "Talk Shows & Podcasts",
    "488190": "Poker",
    "490413": "Board Games",
    "498566": "Slots",
    "498592": "I'm Only Sleeping",
    "499634": "Crypto",
    "499973": "Always On",
    "509577": "Dungeons & Dragons",
    "509658": "Just Chatting",
    "509659": "ASMR",
    "509660": "Art",
    "509663": "Special Events",
    "509664": "Tabletop RPGs",
    "509667": "Food & Drink",
    "509669": "GRWM",
    "509670": "Science & Technology",
    "509671": "Fitness & Health",
    "509672": "IRL",
    "509673": "Makers & Crafting",
    "515214": "Politics",
    "518203": "Sports",
    "1422023940": "QSMP",
    "1469308723": "Software and Game Development",
    "1599346425": "Co-working & Studying",
    "1669431183": "DJs",
}


# Twitch category ids IGDB's external_games maps wrongly, checked by hand.
IGDB_CORRECTIONS = {
    "32399": "242408",  # Counter-Strike is Counter-Strike 2 since 2023; IGDB still links CS:GO (1372)
}


def validate(definition):
    """Check the params without network."""
    if not 1 <= int(definition.params.get("limit", MAX_LIMIT)) <= MAX_LIMIT:
        msg = f"limit must be between 1 and {MAX_LIMIT}"
        raise ValueError(msg)
    if tuple(definition.media_types) != ("game",):
        msg = "Twitch top games is a game list"
        raise ValueError(msg)
    _rows.validate(definition, definition.params.get("limit", MAX_LIMIT))


def split_year(name):
    """Return ``(title, year)``: Twitch disambiguates some names as "Dressmaker (2026)"."""
    match = _YEAR_SUFFIX.match(name.strip())
    if match:
        return match["title"], int(match["year"])
    return name.strip(), None


def entries_from_rows(rows, by_igdb, by_twitch):
    """Turn Helix rows into entries, ranked 1..n among the games kept."""
    entries = []
    for row in rows:
        twitch_id = str(row.get("id") or "")
        if not twitch_id or twitch_id in NON_GAMES:
            continue
        igdb_id = str(row.get("igdb_id") or "").strip()
        game = by_igdb.get(igdb_id) if igdb_id else by_twitch.get(twitch_id)
        title, year = split_year(str(row.get("name") or ""))
        entries.append(
            Entry(
                rank=len(entries) + 1,
                title=(game.name if game and game.name else title),
                year=game.year if game and game.year else year,
                media_type="game",
                ids={"igdb": game.igdb_id if game else igdb_id},
                image=game.image if game else "",
            ),
        )
    return entries


def fetch(definition):
    """Fetch the top categories and match them to IGDB."""
    limit = int(definition.params.get("limit", MAX_LIMIT))
    data = _igdb_games.authorized(get_json, "twitch", URL, params={"first": limit})
    rows = (data or {}).get("data") if isinstance(data, dict) else None
    if not rows:
        msg = "Twitch returned no top games."
        raise AdapterError(msg)
    games = [row for row in rows if str(row.get("id") or "") not in NON_GAMES]
    _rows.require(definition, len(games), "Twitch's top games")
    with_igdb = [str(row["igdb_id"]) for row in games if str(row.get("igdb_id") or "").strip()]
    without = [str(row["id"]) for row in games if not str(row.get("igdb_id") or "").strip()]
    by_igdb = _igdb_games.games_by_ids(with_igdb)
    by_twitch = (
        _igdb_games.games_by_external(without, igdb.ExternalGameSource.TWITCH, IGDB_CORRECTIONS) if without else {}
    )
    return FetchResult(entries=entries_from_rows(rows, by_igdb, by_twitch))


ADAPTER = Adapter(key="twitch", fetch=fetch, validate=validate)
