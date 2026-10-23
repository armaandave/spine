"""Batched IGDB lookups shared by the game adapters (not an adapter itself).

One request names, dates and covers up to 500 games, so a 100-row chart costs one
or two IGDB calls instead of one ``external_game`` call plus one metadata call per
row. Entries built from these carry an ``igdb`` id and a cover, so the resolver and
writer need no further network.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import requests
from django.conf import settings
from django.core.cache import cache

from app.models import Sources
from app.providers import igdb, services
from lists.featured.helpers import post_json, sanitize
from lists.featured.types import AdapterError

BATCH = 500
COVER_URL = "https://images.igdb.com/igdb/image/upload/t_original/{}.jpg"  # as igdb.get_image_url
GAME_FIELDS = "name,first_release_date,cover.image_id"


@dataclass(frozen=True)
class Game:
    """What a featured entry needs from IGDB."""

    igdb_id: str
    name: str = ""
    year: int | None = None
    image: str = ""


def auth_headers():
    """Return IGDB's headers: the Twitch client id and an app token (also valid for Helix)."""
    try:
        token = igdb.get_access_token()
    except (services.ProviderAPIError, requests.RequestException, KeyError, TypeError) as error:
        msg = f"IGDB sign-in failed: {sanitize(error, 160)}"
        raise AdapterError(msg) from None
    return {"Client-ID": settings.IGDB_ID, "Authorization": f"Bearer {token}"}


def authorized(request, *args, **kwargs):
    """Call ``request`` (``get_json`` or ``post_json``) with ``auth_headers()``.

    Twitch can revoke the cached token before it expires, so an HTTP 401 drops it and
    retries once with a fresh one, as ``igdb.handle_error`` does.
    """
    headers = auth_headers()
    try:
        return request(*args, headers=headers, **kwargs)
    except AdapterError as error:
        if "(HTTP 401)" not in str(error):
            raise
    cache.delete(f"{Sources.IGDB.value}_access_token")
    return request(*args, headers=auth_headers(), **kwargs)


def query(endpoint, body):
    """POST an Apicalypse ``body`` to ``api.igdb.com/v4/<endpoint>``."""
    result = authorized(post_json, "igdb", f"{igdb.base_url}/{endpoint}", data=body)
    if not isinstance(result, list):
        msg = f"IGDB {endpoint} returned an unexpected response."
        raise AdapterError(msg)
    return result


def _game(payload, igdb_id=None):
    released = payload.get("first_release_date")
    image_id = (payload.get("cover") or {}).get("image_id")
    return Game(
        igdb_id=str(igdb_id or payload["id"]),
        name=str(payload.get("name") or ""),
        year=datetime.fromtimestamp(released, UTC).year if isinstance(released, int) else None,
        image=COVER_URL.format(image_id) if image_id else "",
    )


def _chunks(values):
    values = list(dict.fromkeys(str(value) for value in values if str(value).strip()))
    for start in range(0, len(values), BATCH):
        yield values[start : start + BATCH]


def games_by_ids(ids):
    """Return ``{igdb_id: Game}`` for every id IGDB knows."""
    found = {}
    for chunk in _chunks(ids):
        numeric = [value for value in chunk if value.isdigit()]
        if not numeric:
            continue
        rows = query("games", f"fields {GAME_FIELDS}; where id = ({','.join(numeric)}); limit {BATCH};")
        for row in rows:
            if row.get("id"):
                found[str(row["id"])] = _game(row)
    return found


def games_by_external(uids, source, corrections=None):
    """Return ``{uid: Game}`` through IGDB's ``external_games`` (Steam appids, Twitch ids).

    When IGDB maps one uid to several games, the oldest mapping wins, as in
    ``igdb.external_game``. ``corrections`` (``{uid: igdb_id}``) replaces IGDB's
    mapping for uids it gets wrong or lacks; those are looked up by game id.
    """
    corrections = {str(uid): str(igdb_id) for uid, igdb_id in (corrections or {}).items()}
    uids = [str(uid) for uid in uids]
    corrected = games_by_ids(corrections[uid] for uid in uids if uid in corrections)
    found = {uid: corrected[corrections[uid]] for uid in uids if corrections.get(uid) in corrected}
    uids = [uid for uid in uids if uid not in corrections]
    fields = ",".join(f"game.{field}" for field in GAME_FIELDS.split(","))
    for chunk in _chunks(uids):
        quoted = ",".join(f'"{value}"' for value in chunk if value.isdigit())
        if not quoted:
            continue
        rows = query(
            "external_games",
            f"fields uid,game,{fields}; where uid = ({quoted}) & external_game_source = {int(source)}; "
            f"sort id asc; limit {BATCH};",
        )
        for row in rows:
            uid, game = str(row.get("uid") or ""), row.get("game")
            if uid and uid not in found and isinstance(game, dict) and game.get("id"):
                found[uid] = _game(game)
    return found
