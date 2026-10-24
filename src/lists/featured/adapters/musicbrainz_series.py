"""A MusicBrainz release-group series: critics' lists, award winners, Best New Album.

``GET /ws/2/series/<id>?inc=release-group-rels+artist-credits`` returns every entry
with its release-group MBID and a ``number`` attribute, so nothing needs matching.

Params:

- ``series``: the series MBID; or ``latest``: ``{"query": <series search>,
  "pattern": <regex whose group 1 is the year>, "min_entries": 50}`` to follow the
  newest complete edition of a yearly list ("TheNeedleDrop: Top 50 Albums of 2025").
- ``order``:
  - ``"number"`` (default): ascending by the ``number`` attribute, which is the rank;
  - ``"date"``: ``number`` is a ``YYYY.MM.DD`` date; newest first, only the last
    ``window_days`` days (Pitchfork's Best New Album);
  - ``"winners"``: only entries numbered ``"YYYY winner"``, newest first (Grammys,
    Mercury Prize), each noted with its year alone (``"2026"``).
- ``limit``: keep at most this many entries.
"""

import re
from datetime import date, timedelta

from django.utils import timezone

from lists.featured.adapters import _music
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

ORDERS = ("number", "date", "winners")
_DATE_NUMBER = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$")
_WINNER_NUMBER = re.compile(r"^(\d{4}) winner$")
LATEST_CANDIDATES = 3  # editions tried, newest first, before giving up


def validate(definition):
    """Check the params without network."""
    params = definition.params
    if set(definition.media_types) != {"music"}:
        msg = "MusicBrainz series lists hold music"
        raise ValueError(msg)
    series, latest = params.get("series"), params.get("latest")
    if bool(series) == bool(latest):
        msg = "set exactly one of series or latest"
        raise ValueError(msg)
    if series and not _music.is_mbid(series):
        msg = "series must be a MusicBrainz series MBID"
        raise ValueError(msg)
    if latest:
        if not latest.get("query") or not latest.get("pattern"):
            msg = "latest needs a query and a pattern"
            raise ValueError(msg)
        if re.compile(latest["pattern"]).groups != 1:
            msg = "latest.pattern must capture the year in one group"
            raise ValueError(msg)
        if int(latest.get("min_entries", 1)) < 1:
            msg = "latest.min_entries must be positive"
            raise ValueError(msg)
    order = params.get("order", "number")
    if order not in ORDERS:
        msg = f"order must be one of {ORDERS}"
        raise ValueError(msg)
    if order == "date" and int(params.get("window_days", 0)) < 1:
        msg = "order=date needs a positive window_days"
        raise ValueError(msg)
    if "limit" in params and int(params["limit"]) < 1:
        msg = "limit must be positive"
        raise ValueError(msg)


def _series(series_id):
    data = _music.mb_get(f"series/{series_id}", {"inc": "release-group-rels+artist-credits"})
    relations = [
        relation
        for relation in data.get("relations") or []
        if relation.get("target-type") == "release_group" and (relation.get("release_group") or {}).get("id")
    ]
    return data, relations


def _latest(spec):
    """Return ``(series, relations, year)`` for the newest edition with enough entries."""
    pattern = re.compile(spec["pattern"])
    found = _music.mb_get("series", {"query": f'series:"{spec["query"]}"', "limit": 100})
    editions = sorted(
        (
            (int(match.group(1)), series["id"])
            for series in found.get("series") or []
            if (match := pattern.fullmatch(series.get("name") or ""))
        ),
        reverse=True,
    )
    minimum = int(spec.get("min_entries", 1))
    for year, series_id in editions[:LATEST_CANDIDATES]:
        data, relations = _series(series_id)
        if len({relation["release_group"]["id"] for relation in relations}) >= minimum:
            return data, relations, year
    msg = f"MusicBrainz has no complete series matching {spec['pattern']!r}."
    raise AdapterError(msg)


def _entry(relation, *, rank=None, note=""):
    group = relation["release_group"]
    info = _music.release_group_info(group)
    return Entry(
        rank=rank,
        title=info["title"],
        year=info["year"],
        creator=info["artist"],
        media_type="music",
        ids={"mbid_rg": info["id"]},
        image=_music.cover_url(info["id"]),
        note=note,
    )


def _number(relation):
    return str((relation.get("attribute-values") or {}).get("number") or "").strip()


def _by_number(relations):
    ranked = []
    for relation in relations:
        number = _number(relation)
        if number.isdigit():
            ranked.append((int(number), relation))
    ranked.sort(key=lambda pair: pair[0])
    return [_entry(relation, rank=rank) for rank, relation in ranked]


def _by_date(relations, window_days, today):
    cutoff = today - timedelta(days=window_days)
    dated = []
    for relation in relations:
        match = _DATE_NUMBER.match(_number(relation))
        if not match:
            continue
        try:
            awarded = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if cutoff <= awarded <= today:
            dated.append((awarded, relation))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [
        _entry(relation, note=f"{awarded:%b} {awarded.day}, {awarded.year}") for awarded, relation in dated
    ], cutoff


def _winners(relations):
    won = []
    for relation in relations:
        match = _WINNER_NUMBER.match(_number(relation))
        if match:
            won.append((int(match.group(1)), relation))
    won.sort(key=lambda pair: pair[0], reverse=True)
    return [_entry(relation, note=str(year)) for year, relation in won], won


def fetch(definition):
    """Fetch the series and order its entries."""
    params = definition.params
    if params.get("latest"):
        data, relations, year = _latest(params["latest"])
        edition, description = str(year), f"{data.get('name') or definition.name}."
    else:
        data, relations = _series(params["series"])
        edition, description = "", ""
    if not relations:
        msg = "The MusicBrainz series has no release groups."
        raise AdapterError(msg)
    order = params.get("order", "number")
    if order == "date":
        entries, cutoff = _by_date(relations, int(params["window_days"]), timezone.localdate())
        edition = f"Since {cutoff:%b} {cutoff.day}, {cutoff.year}"
    elif order == "winners":
        entries, won = _winners(relations)
        if won:
            edition = f"{won[-1][0]}–{won[0][0]}"
    else:
        entries = _by_number(relations)
    if "limit" in params:
        entries = entries[: int(params["limit"])]
    if not entries:
        msg = "The MusicBrainz series has no entries in the requested order."
        raise AdapterError(msg)
    return FetchResult(entries=entries, edition=edition, description=description)


ADAPTER = Adapter(key="musicbrainz_series", fetch=fetch, validate=validate)
