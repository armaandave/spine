"""ListenBrainz's sitewide most-listened release groups.

Params: ``{"range": "week" | "all_time" | …, "limit": 50}``. ``week`` is the last
complete week (Monday to Monday, UTC).

Rows already carry release-group MBIDs. Repeats are dropped, singles are dropped (one
batched MusicBrainz search gives every type), and the few rows without an MBID get
a strict title and artist search.
"""

from datetime import UTC, datetime

from lists.featured.adapters import _music
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://api.listenbrainz.org/1/stats/sitewide/release-groups"
RANGES = ("this_week", "week", "this_month", "month", "quarter", "this_year", "year", "half_yearly", "all_time")
FETCH_COUNT = 100  # the API's maximum; more than ``limit`` so dropped rows leave a full list


def validate(definition):
    """Check the params without network."""
    if definition.params.get("range") not in RANGES:
        msg = f"range must be one of {RANGES}"
        raise ValueError(msg)
    if not 1 <= int(definition.params.get("limit", 50)) <= FETCH_COUNT:
        msg = f"limit must be between 1 and {FETCH_COUNT}"
        raise ValueError(msg)
    if set(definition.media_types) != {"music"}:
        msg = "ListenBrainz charts hold music"
        raise ValueError(msg)


def edition_label(stats_range, from_ts):
    """Return "Week of Oct 12, 2026" for weekly ranges, "All time" for all time."""
    if stats_range == "all_time":
        return "All time"
    try:
        start = datetime.fromtimestamp(int(from_ts), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    if "week" in stats_range:
        return f"Week of {start:%b} {start.day}, {start.year}"
    return f"Since {start:%b} {start.day}, {start.year}"


def fetch(definition):
    """Fetch the stats, then drop repeats and singles."""
    stats_range = definition.params["range"]
    limit = int(definition.params.get("limit", 50))
    data = get_json("listenbrainz", URL, params={"range": stats_range, "count": FETCH_COUNT})
    payload = (data or {}).get("payload") or {}
    rows = payload.get("release_groups") or []
    if not rows:
        msg = "ListenBrainz returned no release groups."
        raise AdapterError(msg)

    candidates, seen = [], set()
    for row in rows:
        title, artist = row.get("release_group_name") or "", row.get("artist_name") or ""
        mbid = row.get("release_group_mbid") or ""
        info = None
        if not _music.is_mbid(mbid):
            info = _music.search_release_group(title, artist, include_singles=True)
            mbid = info["id"] if info else ""
        if mbid and mbid in seen:
            continue
        seen.add(mbid)
        candidates.append((row, mbid, info))

    details = _music.release_group_details([mbid for _, mbid, _ in candidates if mbid])
    entries = []
    for row, mbid, info in candidates:
        info = details.get(mbid) or info
        if info and (info.get("primary_type") or "").casefold() == "single":
            continue
        listens = row.get("listen_count")
        entries.append(
            Entry(
                rank=len(entries) + 1,
                title=(info and info["title"]) or row.get("release_group_name") or "",
                year=info and info["year"],
                creator=row.get("artist_name") or "",
                media_type="music",
                ids={"mbid_rg": mbid} if mbid else {},
                image=_music.cover_url(mbid) if mbid else "",
                note=f"{listens:,} listens" if isinstance(listens, int) else "",
            ),
        )
        if len(entries) == limit:
            break
    return FetchResult(entries=entries, edition=edition_label(stats_range, payload.get("from_ts")))


ADAPTER = Adapter(key="listenbrainz", fetch=fetch, validate=validate)
