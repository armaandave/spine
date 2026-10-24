"""The Steam Awards, from ``store.steampowered.com/steamawards/<year>``.

Params (one mode):

- ``{"year": 2025}``: that year's category winners in Steam's category order, each
  noted with its categories ("Game of the Year · Best Game You Suck At").
- ``{"category": "GOTY", "since": 2018}``: one category's winner for every year
  from ``since`` to now, newest first, each noted with its year. Years whose winner
  isn't out yet (or whose page doesn't exist yet) are skipped.

Pages from 2023 on carry a ``data-steam_awards_config`` JSON blob (``votes`` with
``internal_name``, ``localization.title``, ``winner_appid``, nominees in
``app_discounts`` and ``reveal_time``); 2018–2022 pages only have HTML category
blocks. A year without a page redirects to the latest one, so every page is checked
against the year it was fetched for. Winners' appids are matched to IGDB in one
batched request.
"""

import html
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from django.utils import timezone

from app.providers import igdb
from lists.featured.adapters import _igdb_games
from lists.featured.adapters.steam import IGDB_CORRECTIONS
from lists.featured.helpers import get_text
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

PAGE = "https://store.steampowered.com/steamawards/{year}"
FIRST_YEAR = 2016
_CONFIG = re.compile(r'data-steam_awards_config="([^"]*)"')
_BLOCK = re.compile(r'<div class="vote_category_ctn"')
_BLOCK_YEAR = re.compile(r'class="category_year award_year">\s*(\d{4})\s*<')
_BLOCK_TITLE = re.compile(r'class="category_title award_title">([^<]*)<')
_BLOCK_WINNER = re.compile(r'class="category_winner_ctn">.*?/app/(\d+)/', re.DOTALL)
_BLOCK_WINNER_NAME = re.compile(r'class="winner_name">([^<]*)<')
_NOMINEE = re.compile(r'data-vote-appid="(\d+)"')
# Old pages have no internal names; map their titles onto the config's.
LEGACY_NAMES = {"Game of the Year": "GOTY", "VR Game of the Year": "VRGOTY", "Labor of Love": "LaborOfLove"}


@dataclass(frozen=True)
class Category:
    """One award category on one year's page."""

    name: str  # internal name ("GOTY"), or the title on old pages
    title: str
    winner: str = ""  # appid; empty until announced
    winner_name: str = ""
    nominees: tuple[str, ...] = field(default=())


def validate(definition):
    """Check the params without network."""
    params = definition.params
    year, category = params.get("year"), params.get("category")
    if bool(year) == bool(category):
        msg = "set exactly one of year or category"
        raise ValueError(msg)
    if year is not None and not (isinstance(year, int) and year >= FIRST_YEAR):
        msg = f"year must be an integer from {FIRST_YEAR}"
        raise ValueError(msg)
    if category is not None:
        since = params.get("since")
        if not (isinstance(since, int) and since >= FIRST_YEAR):
            msg = f"category lists need an integer since from {FIRST_YEAR}"
            raise ValueError(msg)
    if tuple(definition.media_types) != ("game",):
        msg = "The Steam Awards are game lists"
        raise ValueError(msg)


def _year_of(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).year if isinstance(timestamp, int) and timestamp > 0 else None


def _from_config(raw, year):
    try:
        config = json.loads(html.unescape(raw))
    except ValueError:
        msg = "The Steam Awards page's embedded config is not readable JSON."
        raise AdapterError(msg) from None
    if not isinstance(config, dict):
        msg = "The Steam Awards page's embedded config is not an object."
        raise AdapterError(msg)
    definitions = config.get("definitions") or {}
    votes = (definitions.get("votes") if isinstance(definitions, dict) else None) or []
    votes = [vote for vote in votes if isinstance(vote, dict)] if isinstance(votes, list) else []
    years = {_year_of(vote.get("start_time")) or _year_of(vote.get("reveal_time")) for vote in votes}
    if years != {year}:
        return None
    categories = []
    for vote in votes:
        localization = vote.get("localization") or {}
        winner = vote.get("winner_appid")
        categories.append(
            Category(
                name=str(vote.get("internal_name") or localization.get("title") or ""),
                title=str(localization.get("title") or vote.get("internal_name") or ""),
                winner=str(winner) if winner else "",
                nominees=tuple(
                    str(app["appid"]) for app in vote.get("app_discounts") or () if isinstance(app, dict) and app.get("appid")
                ),
            ),
        )
    return categories


def _from_blocks(text, year):
    categories = []
    for block in _BLOCK.split(text)[1:]:
        block_year = _BLOCK_YEAR.search(block)
        if not block_year or int(block_year.group(1)) != year:
            return None
        title = html.unescape((_BLOCK_TITLE.search(block) or [None, ""])[1]).strip()
        header = block.split("category_nominations_ctn", 1)[0]  # the winner, not a nominee
        winner = _BLOCK_WINNER.search(header)
        winner_name = _BLOCK_WINNER_NAME.search(header)
        categories.append(
            Category(
                name=LEGACY_NAMES.get(title, title),
                title=title,
                winner=winner.group(1) if winner else "",  # a developer award links no app
                winner_name=html.unescape(winner_name.group(1)).strip() if winner and winner_name else "",
                nominees=tuple(dict.fromkeys(_NOMINEE.findall(block))),
            ),
        )
    return categories or None


def parse_page(text, year):
    """Return the page's categories, or None when it isn't ``year``'s page."""
    config = _CONFIG.search(text)
    if config:
        return _from_config(config.group(1), year)
    return _from_blocks(text, year)


def fetch_year(year):
    """Return ``year``'s categories, or None when Steam has no page for it."""
    return parse_page(get_text("steam", PAGE.format(year=year)), year)


def _igdb(winners):
    appids = [appid for appid, _, _ in winners]
    return _igdb_games.games_by_external(appids, igdb.ExternalGameSource.STEAM, IGDB_CORRECTIONS)


def _entries(winners, games, *, merge=True):
    """``winners`` is ``[(appid, note, name)]`` in list order.

    With ``merge`` (one year's categories) a game is one entry noted with all its
    categories; without it (one category's years) each win is its own entry.
    """
    notes, names, order = {}, {}, []
    for appid, note, name in winners:
        key = appid if merge else (appid, note)
        if key not in notes:
            order.append(key)
            notes[key], names[key] = [], name
        notes[key].append(note)
    entries = []
    for key in order:
        appid = key if merge else key[0]
        game = games.get(appid)
        entries.append(
            Entry(
                rank=len(entries) + 1,
                title=(game.name if game and game.name else names[key]),
                year=game.year if game else None,
                media_type="game",
                ids={"steam": appid, "igdb": game.igdb_id if game else None},
                image=game.image if game else "",
                note=" · ".join(notes[key]),
            ),
        )
    return entries


def _year_result(year):
    categories = fetch_year(year)
    if categories is None:
        msg = f"Steam has no Steam Awards page for {year} yet."
        raise AdapterError(msg)
    winners = [(category.winner, category.title, category.winner_name) for category in categories if category.winner]
    games = _igdb(winners)
    announced = len({category.name for category in categories if category.winner})
    return FetchResult(
        entries=_entries(winners, games),
        edition=str(year),
        live_label="" if announced == len(categories) else f"{announced} of {len(categories)} announced",
    )


def _category_result(category_name, since, until):
    winners = []
    for year in range(until, since - 1, -1):
        categories = fetch_year(year)
        if categories is None:
            continue
        for category in categories:
            if category.name == category_name and category.winner:
                winners.append((category.winner, str(year), category.winner_name))
    if not winners:
        msg = f"No Steam Awards {category_name} winners found since {since}."
        raise AdapterError(msg)
    games = _igdb(winners)
    years = [int(note) for _, note, _ in winners]
    return FetchResult(entries=_entries(winners, games, merge=False), edition=f"{min(years)}–{max(years)}")


def fetch(definition):
    """Fetch one year's winners, or one category across years."""
    params = definition.params
    if params.get("year"):
        return _year_result(int(params["year"]))
    return _category_result(str(params["category"]), int(params["since"]), timezone.now().year)


ADAPTER = Adapter(key="steam_awards", fetch=fetch, validate=validate)
