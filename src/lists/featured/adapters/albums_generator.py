"""The 1001 Albums Generator's community ratings (an unofficial, keyless API).

Params: ``{"limit": 100, "min_votes": 100}``. Albums are ordered by average rating,
then by vote count. Each album's Spotify ID is matched to a release group through
MusicBrainz URL relationships, with a strict title, artist and year search as the
fallback. ``"min_entries"`` is the fewest albums a complete list has; fewer fail the run
before any MusicBrainz lookup.
"""

from lists.featured.adapters import _music, _rows
from lists.featured.helpers import get_json
from lists.featured.text import parse_year
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://1001albumsgenerator.com/api/v1/albums/stats"
SPOTIFY_URL = "https://open.spotify.com/album/{spotify_id}"


def validate(definition):
    """Check the params without network."""
    if int(definition.params.get("limit", 100)) < 1:
        msg = "limit must be positive"
        raise ValueError(msg)
    if int(definition.params.get("min_votes", 0)) < 0:
        msg = "min_votes must not be negative"
        raise ValueError(msg)
    if set(definition.media_types) != {"music"}:
        msg = "1001 Albums Generator lists hold music"
        raise ValueError(msg)
    _rows.validate(definition, definition.params.get("limit", 100))


def _rated(albums, min_votes):
    rated = []
    for album in albums:
        try:
            rating, votes = float(album["averageRating"]), int(album["votes"])
        except (KeyError, TypeError, ValueError):
            continue
        if votes >= min_votes and album.get("name"):
            rated.append((rating, votes, album))
    rated.sort(key=lambda row: (-row[0], -row[1]))
    return rated


def fetch(definition):
    """Fetch every album's stats and keep the highest rated."""
    params = definition.params
    data = get_json("1001 albums generator", URL)
    albums = (data or {}).get("albums") if isinstance(data, dict) else None
    if not albums:
        msg = "The 1001 Albums Generator returned no albums."
        raise AdapterError(msg)
    top = _rated(albums, int(params.get("min_votes", 0)))[: int(params.get("limit", 100))]
    if not top:
        msg = "The 1001 Albums Generator returned no rated albums."
        raise AdapterError(msg)
    _rows.require(definition, len(top), "The 1001 Albums Generator's ratings")
    urls = {
        album["spotifyId"]: SPOTIFY_URL.format(spotify_id=album["spotifyId"])
        for _, _, album in top
        if album.get("spotifyId")
    }
    by_url = _music.release_groups_for_urls(urls.values())
    entries = []
    for rank, (rating, votes, album) in enumerate(top, start=1):
        artist = album.get("artist") or ""
        year = parse_year(album.get("releaseDate"))
        info = by_url.get(urls.get(album.get("spotifyId"), ""))
        if info is None:
            info = _music.search_release_group(album["name"], artist, year)
        entries.append(
            Entry(
                rank=rank,
                title=info["title"] if info else album["name"],
                year=(info and info["year"]) or year,
                creator=artist,
                media_type="music",
                ids={"mbid_rg": info["id"]} if info else {},
                image=_music.cover_url(info["id"]) if info else "",
                note=f"★ {rating:.2f} · {votes:,} votes",
            ),
        )
    return FetchResult(entries=entries)


ADAPTER = Adapter(key="albums_generator", fetch=fetch, validate=validate)
