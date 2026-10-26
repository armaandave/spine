"""Record the Apple Music responses Spine's tests read into src/app/tests/mock_data.

Usage, from the repo root:

    uv run --locked python scripts/music/record_apple_fixtures.py --token-file PATH

PATH (or $APPLE_MUSIC_TOKEN_FILE) is a file holding a short-lived Apple Music
developer token, minted outside the repo and saved with mode 600. This script never
signs, prints or logs a token. It keeps to 2 requests a second, keeps only the
fields the code and tests read (no editorial notes or previews; artwork only on
album pages), and refuses to write a file that contains Authorization text or
anything token-like.

``--only GROUP`` records one group of fixtures (repeat it for several): ``catalog``
for the listing rule and client tests, ``album_pages`` for album and song pages,
``search`` for search, people search and artist pages.

Apple's catalog changes, so re-recording can move IDs and counts that the tests
assert. Re-record only when the tests need new data, and review the diff.
"""

# ruff: noqa: INP001, T201

import argparse
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "src" / "app" / "tests" / "mock_data" / "apple_music"
API_URL = "https://api.music.apple.com"
CATALOG = "/v1/catalog/us"
MIN_INTERVAL = 0.5  # 2 requests a second
RETRIES = 3
SZA_ARTIST_ID = "605800394"
# Search fixtures, by name: queries whose first page the search tests read, and one
# read over two pages.
SEARCHES = {
    "renaissance": "renaissance",
    "thriller": "thriller",
    "sos": "sos",
    "my_turn": "lil baby my turn",
    "donda": "kanye west donda",
}
TWO_PAGE_SEARCH = ("sza_sos", "sza sos")
ARTIST_VIEWS = "full-albums,singles,live-albums,compilation-albums,appears-on-albums"
# A view's next page asks for up to this many albums.
ARTIST_VIEW_LIMIT = 100
ALBUM_BATCH = 100
ALBUM_FIELDS = (
    "name",
    "artistName",
    "url",
    "playParams",
    "isComplete",
    "releaseDate",
    "contentRating",
    "trackCount",
)
SONG_FIELDS = ("name", "albumName", "discNumber", "trackNumber", "isrc")
ARTIST_FIELDS = ("name", "url")
# Artist pages and people search also read the artist's picture.
PAGE_ARTIST_FIELDS = (*ARTIST_FIELDS, "artwork")
# Album pages also read the barcode, genres, label, kind and artwork.
PAGE_ALBUM_FIELDS = (
    *ALBUM_FIELDS,
    "upc",
    "genreNames",
    "recordLabel",
    "isSingle",
    "isCompilation",
    "artwork",
)
PAGE_SONG_FIELDS = (
    *SONG_FIELDS,
    "artistName",
    "composerName",
    "durationInMillis",
    "contentRating",
    "releaseDate",
    "url",
)
ARTWORK_FIELDS = ("url", "width", "height")
PAGE_PARAMS = {"include": "tracks,artists", "views": "other-versions,related-albums"}
# Album pages, by fixture name: an album, its re-upload, a two-disc deluxe, a
# pre-order and a video album.
ALBUM_PAGES = {
    "page_sos.json": "1657869377",
    "page_lana.json": "1794644675",
    "page_lana_38.json": "1786643044",
    "page_thriller.json": "269572838",
    "page_whos_next_deluxe.json": "1440850317",
    "page_weathered_preorder.json": "6803287133",
    "page_multitude_video.json": "1621698801",
}
ERROR_FIELDS = ("status", "code", "title", "detail")
FORBIDDEN = re.compile(
    r"authorization|bearer\s|eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}",
    re.IGNORECASE,
)


class Recorder:
    """Make paced Apple Music requests and write checked fixture files."""

    def __init__(self, token, out):
        """Keep the token in memory only."""
        self._token = token
        self._out = out
        self._last = 0.0

    def get(self, path, params=None, *, token=None, expect=200):
        """GET an API path at no more than 2 a second; stop on an unexpected status."""
        for attempt in range(RETRIES):
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            response = requests.get(
                f"{API_URL}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token or self._token}"},
                timeout=30,
            )
            # path_url is the path and query only: no header, so no token.
            print(f"GET {response.request.path_url} -> HTTP {response.status_code}")
            too_many = response.status_code == requests.codes.too_many_requests
            if too_many and attempt < RETRIES - 1:
                time.sleep(5 * (attempt + 1))
                continue
            break
        if response.status_code != expect:
            sys.exit(f"Expected HTTP {expect}; stopping without writing more files.")
        return response

    def catalog(self, path, params=None):
        """GET a path in the US catalog and return its JSON."""
        return self.get(f"{CATALOG}/{path}", params).json()

    def write(self, name, data):
        """Write one fixture, unless it holds anything that looks like a credential."""
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        if self._token in text or FORBIDDEN.search(text):
            sys.exit(f"Refusing to write {name}: it holds Authorization or token text.")
        path = self._out / name
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO) if path.is_relative_to(REPO) else path}")


def trim_album(resource, *, tracks=False):
    """Keep an album's ID, the attributes the listing rule reads, and its views."""
    album = {
        "id": resource["id"],
        "type": resource.get("type", "albums"),
        "attributes": _pick(resource.get("attributes"), ALBUM_FIELDS),
    }
    views = resource.get("views") or {}
    if views:
        # Only the IDs of an album's other versions are read; their details come
        # from their own entries.
        album["views"] = {
            name: trim_view(view, member=_ref) for name, view in views.items()
        }
    relationship = (resource.get("relationships") or {}).get("tracks")
    if tracks and relationship:
        album["relationships"] = {
            "tracks": {
                "data": [trim_song(song) for song in relationship.get("data") or []],
                **({"next": relationship["next"]} if relationship.get("next") else {}),
            },
        }
    return album


def trim_view(view, *, member):
    """Keep a view's title, its next link, and its trimmed members."""
    trimmed = {
        "attributes": _pick(view.get("attributes"), ("title",)),
        "data": [member(album) for album in view.get("data") or []],
    }
    if view.get("next"):
        trimmed["next"] = view["next"]
    return trimmed


def trim_song(resource, *, albums=False):
    """Keep a song's ID, position and ISRC, and the IDs of its albums if asked."""
    song = {
        "id": resource["id"],
        "type": resource.get("type", "songs"),
        "attributes": _pick(resource.get("attributes"), SONG_FIELDS),
    }
    if albums:
        relationships = resource.get("relationships") or {}
        related = (relationships.get("albums") or {}).get("data") or []
        song["relationships"] = {"albums": {"data": [_ref(album) for album in related]}}
    return song


def trim_artist(resource):
    """Keep an artist's name, link and album views."""
    return {
        "id": resource["id"],
        "type": resource.get("type", "artists"),
        "attributes": _pick(resource.get("attributes"), ARTIST_FIELDS),
        "views": {
            name: trim_view(view, member=trim_album)
            for name, view in (resource.get("views") or {}).items()
        },
    }


def trim_search(response):
    """Keep a search page's albums and its next link."""
    albums = (response.get("results") or {}).get("albums") or {}
    trimmed = {"data": [trim_album(album) for album in albums.get("data") or []]}
    if albums.get("next"):
        trimmed["next"] = albums["next"]
    return {"results": {"albums": trimmed}}


def trim_error(response):
    """Keep an error's status and Apple's error fields."""
    try:
        body = response.json()
    except ValueError:
        body = ""
    if isinstance(body, dict):
        errors = body.get("errors") or []
        body = {"errors": [_pick(error, ERROR_FIELDS) for error in errors]}
    return {"status": response.status_code, "body": body}


def trim_page_album(resource):
    """Keep what an album page reads: attributes, tracks, artists and both views."""
    album = trim_view_album(resource)
    relationships = resource.get("relationships") or {}
    tracks = relationships.get("tracks") or {}
    artists = relationships.get("artists") or {}
    album["relationships"] = {
        "tracks": {
            "data": [
                {
                    "id": song["id"],
                    "type": song.get("type", "songs"),
                    "attributes": _pick(song.get("attributes"), PAGE_SONG_FIELDS),
                }
                for song in tracks.get("data") or []
            ],
            **({"next": tracks["next"]} if tracks.get("next") else {}),
        },
        "artists": {
            "data": [
                {
                    "id": artist["id"],
                    "type": artist.get("type", "artists"),
                    "attributes": _pick(artist.get("attributes"), ARTIST_FIELDS),
                }
                for artist in artists.get("data") or []
            ],
        },
    }
    album["views"] = {
        name: trim_view(view, member=trim_view_album)
        for name, view in (resource.get("views") or {}).items()
    }
    return album


def trim_view_album(resource):
    """Keep an album's page attributes, with only the artwork link and size."""
    attributes = _pick(resource.get("attributes"), PAGE_ALBUM_FIELDS)
    if "artwork" in attributes:
        attributes["artwork"] = _pick(attributes["artwork"], ARTWORK_FIELDS)
    return {
        "id": resource["id"],
        "type": resource.get("type", "albums"),
        "attributes": attributes,
    }


def trim_family_album(resource):
    """Keep an album's page attributes and its other versions' page attributes."""
    album = trim_view_album(resource)
    views = trim_page_views(resource)
    if views:
        album["views"] = views
    return album


def trim_page_views(resource):
    """Keep a resource's views, with their albums' page attributes."""
    views = resource.get("views") or {}
    return {
        name: trim_view(view, member=trim_view_album) for name, view in views.items()
    }


def trim_page_artist(resource):
    """Keep an artist's name, link, picture and album views with their attributes."""
    attributes = _pick(resource.get("attributes"), PAGE_ARTIST_FIELDS)
    if "artwork" in attributes:
        attributes["artwork"] = _pick(attributes["artwork"], ARTWORK_FIELDS)
    artist = {
        "id": resource["id"],
        "type": resource.get("type", "artists"),
        "attributes": attributes,
    }
    views = trim_page_views(resource)
    if views:
        artist["views"] = views
    return artist


def trim_search_page(response, kind):
    """Keep one search page of albums or artists, with page attributes and next link."""
    results = (response.get("results") or {}).get(kind) or {}
    member = trim_view_album if kind == "albums" else trim_page_artist
    trimmed = {"data": [member(resource) for resource in results.get("data") or []]}
    if results.get("next"):
        trimmed["next"] = results["next"]
    return {"results": {kind: trimmed}}


def _ref(resource):
    return {"id": resource["id"], "type": resource.get("type", "albums")}


def _pick(values, fields):
    values = values or {}
    return {field: values[field] for field in fields if field in values}


def record_family(recorder, name, seed_ids):
    """Record a batch of the seeds and every member of their version families."""
    seeds = list(dict.fromkeys(seed_ids))
    params = {"ids": ",".join(seeds), "views": "other-versions"}
    response = recorder.catalog("albums", params)
    family = list(seeds)
    for album in response.get("data") or []:
        members = album.get("views", {}).get("other-versions", {}).get("data") or []
        family.extend(member["id"] for member in members if member["id"] not in family)
    if family != seeds:
        params = {"ids": ",".join(family), "views": "other-versions"}
        response = recorder.catalog("albums", params)
    albums = [trim_album(album) for album in response.get("data") or []]
    recorder.write(name, {"data": albums})


def titled_ids(response, artist, title):
    """Return the IDs of an artist's search results titled ``title`` or an edition."""
    ids = []
    for album in (response.get("results") or {}).get("albums", {}).get("data") or []:
        attributes = album.get("attributes") or {}
        name = attributes.get("name") or ""
        if attributes.get("artistName") == artist and (
            name == title or name.startswith((f"{title} (", f"{title} ["))
        ):
            ids.append(album["id"])
    return ids


def record_album_pages(recorder):
    """Record the album pages the album and song page tests read."""
    blood_bank = recorder.catalog(
        "search",
        {"term": "bon iver blood bank", "types": "albums", "limit": 10},
    )
    ep_id = titled_ids(blood_bank, "Bon Iver", "Blood Bank - EP")[0]
    for name, album_id in {**ALBUM_PAGES, "page_blood_bank_ep.json": ep_id}.items():
        page = recorder.catalog(f"albums/{album_id}", PAGE_PARAMS)
        albums = [trim_page_album(album) for album in page["data"]]
        recorder.write(name, {"data": albums})


def record_families(recorder, name, ids):
    """Write one batch of albums with their version families' page attributes."""
    params = {"ids": ",".join(ids), "views": "other-versions"}
    families = recorder.catalog("albums", params)
    albums = [trim_family_album(album) for album in families.get("data") or []]
    recorder.write(name, {"data": albums})


def record_search_page(recorder, name, response):
    """Write a search page and the batch of its albums' version families."""
    recorder.write(f"search_albums_{name}.json", trim_search_page(response, "albums"))
    ids = [album["id"] for album in response["results"]["albums"]["data"]]
    record_families(recorder, f"search_families_{name}.json", ids)


def record_search(recorder):
    """Record the search, people search and artist page fixtures."""
    for name, term in SEARCHES.items():
        params = {"term": term, "types": "albums", "limit": 25}
        record_search_page(recorder, name, recorder.catalog("search", params))
    name, term = TWO_PAGE_SEARCH
    first = recorder.catalog("search", {"term": term, "types": "albums", "limit": 25})
    record_search_page(recorder, f"{name}_1", first)
    second = recorder.get(first["results"]["albums"]["next"], {"limit": 25}).json()
    record_search_page(recorder, f"{name}_2", second)

    params = {"term": "sza", "types": "artists", "limit": 10}
    artists = recorder.catalog("search", params)
    recorder.write("search_artists_sza.json", trim_search_page(artists, "artists"))

    artist = recorder.catalog(f"artists/{SZA_ARTIST_ID}", {"views": ARTIST_VIEWS})
    recorder.write("person_sza.json", {"data": [trim_page_artist(artist["data"][0])]})
    views = artist["data"][0].get("views") or {}
    ids = [album["id"] for view in views.values() for album in view.get("data") or []]
    for view_name, view in views.items():
        if view.get("next"):
            more = recorder.get(view["next"], {"limit": ARTIST_VIEW_LIMIT}).json()
            albums = more.get("data") or []
            ids.extend(album["id"] for album in albums)
            trimmed = {"data": [trim_view_album(album) for album in albums]}
            if more.get("next"):
                trimmed["next"] = more["next"]
            recorder.write(f"person_sza_{view_name}_2.json", trimmed)
    ids = list(dict.fromkeys(ids))
    for number, start in enumerate(range(0, len(ids), ALBUM_BATCH), start=1):
        batch = ids[start : start + ALBUM_BATCH]
        record_families(recorder, f"person_sza_families_{number}.json", batch)


def record_catalog(recorder):
    """Record every fixture the listing rule and client tests read."""
    first = recorder.catalog(
        "search",
        {"term": "sza sos", "types": "albums", "limit": 25},
    )
    recorder.write("search_sza_sos_page_1.json", trim_search(first))
    second = recorder.get(first["results"]["albums"]["next"], {"limit": 25}).json()
    recorder.write("search_sza_sos_page_2.json", trim_search(second))

    sos_ids = "1657869377,1658650093,1794644675,1786643044,1796270264"
    sos = recorder.catalog("albums", {"ids": sos_ids, "views": "other-versions"})
    recorder.write(
        "albums_sos_lana.json",
        {"data": [trim_album(album) for album in sos["data"]]},
    )

    record_family(recorder, "family_my_turn.json", ["1498988850", "1534816297"])
    record_family(recorder, "family_donda.json", ["1587795158", "1595496182"])
    record_family(recorder, "family_midnights.json", ["1649434004"])
    weezer = recorder.catalog(
        "search",
        {"term": "weezer", "types": "albums", "limit": 25},
    )
    record_family(
        recorder,
        "family_weezer.json",
        titled_ids(weezer, "Weezer", "Weezer"),
    )
    creed = recorder.catalog(
        "search",
        {"term": "creed weathered", "types": "albums", "limit": 10},
    )
    record_family(
        recorder,
        "family_creed_weathered.json",
        titled_ids(creed, "Creed", "Weathered"),
    )

    thriller = recorder.catalog("albums/269572838", {"include": "tracks"})
    recorder.write(
        "album_thriller.json",
        {"data": [trim_album(album, tracks=True) for album in thriller["data"]]},
    )
    year_zero = recorder.catalog("albums", {"ids": "1147788150,1440826538"})
    recorder.write(
        "albums_year_zero.json",
        {"data": [trim_album(album) for album in year_zero["data"]]},
    )

    upc = recorder.catalog("albums", {"filter[upc]": "196589564931"})
    recorder.write(
        "albums_by_upc_sos.json",
        {"data": [trim_album(album) for album in upc["data"]]},
    )
    songs = recorder.catalog("songs", {"filter[isrc]": "USUM70727128,USRC12204584"})
    recorder.write(
        "songs_by_isrc.json",
        {"data": [trim_song(song, albums=True) for song in songs["data"]]},
    )

    views = "full-albums,singles,live-albums,compilation-albums,appears-on-albums"
    sza = recorder.catalog(f"artists/{SZA_ARTIST_ID}", {"views": views})
    recorder.write(
        "artist_sza.json",
        {"data": [trim_artist(artist) for artist in sza["data"]]},
    )

    # A URL not fetched above, so no cached answer stands in for the 401.
    unauthorized = recorder.get(
        f"{CATALOG}/albums/1440868131",
        {"l": "en-US"},
        token="not-a-token",  # noqa: S106 - a deliberately invalid token
        expect=401,
    )
    recorder.write("error_401.json", trim_error(unauthorized))
    missing = recorder.get(f"{CATALOG}/albums/1", expect=404)
    recorder.write("error_404.json", trim_error(missing))


GROUPS = {
    "catalog": record_catalog,
    "album_pages": record_album_pages,
    "search": record_search,
}


def read_token(path):
    """Return the token from a private file outside the repo."""
    path = Path(path).expanduser().resolve()
    if path.is_relative_to(REPO):
        sys.exit("The token file must live outside the repo.")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        sys.exit("The token file must be readable by you only (chmod 600).")
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        sys.exit("The token file is empty.")
    return token


def main():
    """Parse the arguments and record every fixture."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--token-file",
        default=os.environ.get("APPLE_MUSIC_TOKEN_FILE"),
        help="file holding a developer token (default: $APPLE_MUSIC_TOKEN_FILE)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="fixture directory",
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(GROUPS),
        help="record only this group of fixtures (default: every group)",
    )
    args = parser.parse_args()
    if not args.token_file:
        parser.error("pass --token-file or set APPLE_MUSIC_TOKEN_FILE")
    args.out.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(read_token(args.token_file), args.out)
    for group in args.only or GROUPS:
        GROUPS[group](recorder)


if __name__ == "__main__":
    main()
