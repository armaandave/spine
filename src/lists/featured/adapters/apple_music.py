"""Apple Music's most-played albums in one country, from Apple's marketing RSS feed.

Params: ``{"country": "us", "limit": 100}``.

With the Apple catalog, feed IDs are used directly. With the explicit MusicBrainz
fallback, albums are matched through URL relationships or a strict release-group
search (title without edition markers, artist, year).
``"min_entries"`` is the fewest albums a complete feed has; fewer fail the run before
any MusicBrainz lookup.
"""

import time
from datetime import datetime

from django.conf import settings

from app import config
from app.models import Sources
from app.providers import apple_music as catalog
from lists.featured.adapters import _music, _rows
from lists.featured.helpers import get_json
from lists.featured.text import parse_year
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://rss.marketingtools.apple.com/api/v2/{country}/music/most-played/{limit}/albums.json"
LIMITS = (10, 25, 50, 100)
FEED_ATTEMPTS = 3  # the feed answers 504 now and then
ALBUM_URL = "https://music.apple.com/{country}/album/{album_id}"


def validate(definition):
    """Check the params without network."""
    country = definition.params.get("country", "")
    if len(country) != 2 or not country.isalpha() or not country.islower():  # noqa: PLR2004
        msg = "country must be a two-letter lower-case storefront code"
        raise ValueError(msg)
    if int(definition.params.get("limit", 100)) not in LIMITS:
        msg = f"limit must be one of {LIMITS}"
        raise ValueError(msg)
    if set(definition.media_types) != {"music"}:
        msg = "Apple Music charts hold music"
        raise ValueError(msg)
    _rows.validate(definition, definition.params.get("limit", 100))


def _feed(country, limit):
    url = URL.format(country=country, limit=limit)
    for attempt in range(FEED_ATTEMPTS):
        try:
            return get_json("apple music", url)
        except AdapterError:
            if attempt == FEED_ATTEMPTS - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def album_urls(album_id, country):
    """Return the Apple Music URLs MusicBrainz may store for an album, best first."""
    countries = dict.fromkeys(("us", country))
    return [ALBUM_URL.format(country=code, album_id=album_id) for code in countries]


def edition_label(updated):
    """Return "Oct 22, 2026" for the feed's RFC 2822 ``updated`` stamp."""
    try:
        stamp = datetime.strptime(updated, "%a, %d %b %Y %H:%M:%S %z")
    except (TypeError, ValueError):
        return ""
    return f"{stamp:%b} {stamp.day}, {stamp.year}"


def fetch(definition):
    """Fetch the chart and match every album to a release group."""
    country = definition.params["country"]
    feed = (_feed(country, int(definition.params.get("limit", 100))) or {}).get("feed") or {}
    albums = [album for album in feed.get("results") or [] if album.get("id") and album.get("name")]
    if not albums:
        msg = "Apple Music returned no albums."
        raise AdapterError(msg)
    _rows.require(definition, len(albums), f"Apple Music's {country} chart")
    if config.music_catalog_source() == Sources.APPLE_MUSIC:
        equivalents = None
        if country != str(settings.MUSIC_DEFAULT_MARKET or "us").lower():
            equivalents = catalog.equivalent_albums([str(album["id"]) for album in albums], background=True)
        return FetchResult(
            entries=[
                Entry(
                    rank=rank, title=album["name"], year=parse_year(album.get("releaseDate")),
                    creator=album.get("artistName") or "", media_type="music",
                    ids=_native_ids(album, equivalents), image=(album.get("artworkUrl100") or "").replace("/100x100bb", "/600x600bb"),
                )
                for rank, album in enumerate(albums, start=1)
            ],
            edition=edition_label(feed.get("updated")),
        )
    by_url = _music.release_groups_for_urls(
        url for album in albums for url in album_urls(album["id"], country)
    )
    entries = []
    for rank, album in enumerate(albums, start=1):
        artist = album.get("artistName") or ""
        year = parse_year(album.get("releaseDate"))
        info = next((by_url[url] for url in album_urls(album["id"], country) if url in by_url), None)
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
            ),
        )
    return FetchResult(entries=entries, edition=edition_label(feed.get("updated")))


def _native_ids(album, equivalents):
    if equivalents is None:
        return {"apple_album": str(album["id"])}
    equivalent = equivalents.get(str(album["id"]))
    if equivalent is None:
        return {"review": "Unavailable in the app's Apple Music storefront"}
    return {"apple_album": str(equivalent["id"])}


ADAPTER = Adapter(key="apple_music", fetch=fetch, validate=validate)
