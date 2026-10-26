"""Apple Music catalog API client, album pages, search and artist pages.

Raw catalog fetches that return Apple's JSON resources unmapped, and the album, song,
search and artist pages built from them in Spine's shapes (see
docs/music/apple-music-catalog.md). Only ``apple_music`` refs and sources reach it:
music is still catalogued from MusicBrainz unless ``MUSIC_CATALOG_SOURCE`` says
otherwise.

The developer token is signed here from the ``APPLE_MUSIC_*`` key settings and kept
in this process's memory only: never logged, never written to the cache.
"""

import base64
import hashlib
import logging
import re
import time
from collections import Counter
from datetime import date
from urllib.parse import quote

import jwt
import requests
from cryptography.exceptions import UnsupportedAlgorithm
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from app import music_catalog, music_listing
from app.models import (
    AppleAlbum,
    Item,
    ItemFilterFacet,
    MediaTypes,
    MusicAlbumLink,
    Sources,
)
from app.providers import services
from app.providers.search_rank import RANKING_KEYS

APPLE_MUSIC_URL = "https://api.music.apple.com"
TOKEN_LIFETIME = 12 * 60 * 60
# Re-sign once less than this is left, so a token never expires mid-request.
TOKEN_REFRESH_MARGIN = 60 * 60
# Requests someone is waiting on give up quickly, then pause Apple calls for a minute.
USER_TIMEOUT = 10
BACKOFF_KEY = "apple_music:backoff"
BACKOFF_SECONDS = 60
ALBUM_BATCH = 100
FILTER_BATCH = 25
SEARCH_LIMIT = 25
# Bounds a `next` chain, in case Apple ever repeats a link.
MAX_PAGES = 20
# One request serves an album page: its tracks, artists, other versions and similar
# albums. Cached for a day, so the page, its songs and the writes that fetch an
# album's metadata share it.
PAGE_VIEWS = (music_listing.FAMILY_VIEW, "related-albums")
PAGE_INCLUDE = ("tracks", "artists")
PAGE_CACHE_VERSION = "v1"
PAGE_CACHE_TTL = 24 * 60 * 60
# Search: one Apple page of up to 25 albums per Spine page, cached with the listings
# shown so far, so a later page never repeats one.
SEARCH_CACHE_VERSION = "v1"
SEARCH_CACHE_TTL = 6 * 60 * 60
# All media search ranks Apple's first albums higher: 30 for the first, 3 less for
# each one after it.
RANK_BOOST_TOP = 30
RANK_BOOST_STEP = 3
# Apple's sections of an artist page, in Apple's order, and the music credit group
# each one fills (Singles & EPs fills two).
ARTIST_VIEWS = ("full-albums", "singles", "live-albums", "compilation-albums", "appears-on-albums")
ARTIST_VIEW_ROLES = {
    "full-albums": "Albums",
    "live-albums": "Live releases",
    "compilation-albums": "Compilations",
    "appears-on-albums": "Appears on",
}
# Albums read from each section; Apple sends 10 at first and up to 100 a page after.
ARTIST_VIEW_LIMIT = 100
ARTIST_PAGE_CACHE_VERSION = "v1"
ARTIST_PAGE_CACHE_TTL = 24 * 60 * 60
# Covers are stored at 600x600 (about 130 characters); Item.image holds 200.
ARTWORK_SIZE = 600
IMAGE_MAX_LENGTH = 200
SPOTIFY_SEARCH_URL = "https://open.spotify.com/search/{query}/albums"
_TYPE_SUFFIX = re.compile(r"\s+-\s+(EP|Single)\s*$", re.IGNORECASE)
_EDITION_WORDS = re.compile(
    r"\b(deluxe|remaster(?:ed)?|anniversary|expanded|bonus|edition|dlx)\b",
    re.IGNORECASE,
)
# Link rows whose exact Spotify album link can be shown.
_SPOTIFY_LINK_STATUSES = (MusicAlbumLink.Status.MATCHED, MusicAlbumLink.Status.APPLIED)
logger = logging.getLogger(__name__)

# (the key settings it was signed with, token, expiry)
_token = None


class SkippedRequestError(services.ProviderAPIError):
    """Raised instead of a request: the key isn't usable, or Apple failed a moment ago.

    Callers handle it like any other Apple Music failure. Nothing was sent, so it has
    no status code and, unlike its parent, logs nothing.
    """

    def __init__(self, message):
        self.provider = Sources.APPLE_MUSIC.value
        self.status_code = None
        Exception.__init__(self, message)


def is_configured():
    """Return whether all three Apple Music key settings are set."""
    return all(
        str(value or "").strip()
        for value in (
            settings.APPLE_MUSIC_TEAM_ID,
            settings.APPLE_MUSIC_KEY_ID,
            settings.APPLE_MUSIC_PRIVATE_KEY,
        )
    )


@sensitive_variables("signer", "private_key", "token")
def developer_token():
    """Return a signed developer token, re-signing when less than an hour is left."""
    global _token  # noqa: PLW0603
    if not is_configured():
        raise SkippedRequestError("Apple Music isn't configured")
    signer = (
        str(settings.APPLE_MUSIC_TEAM_ID).strip(),
        str(settings.APPLE_MUSIC_KEY_ID).strip(),
        str(settings.APPLE_MUSIC_PRIVATE_KEY),
    )
    now = int(time.time())
    if _token is not None and _token[0] == signer and _token[2] - now > TOKEN_REFRESH_MARGIN:
        return _token[1]
    try:
        private_key = base64.b64decode("".join(signer[2].split()), validate=True)
        token = jwt.encode(
            {"iss": signer[0], "iat": now, "exp": now + TOKEN_LIFETIME},
            private_key,
            algorithm="ES256",
            headers={"kid": signer[1]},
        )
    except (ValueError, TypeError, jwt.PyJWTError, UnsupportedAlgorithm):
        # Never chain or repeat the error: it could quote the key.
        logger.error("Apple Music's private key can't be read")  # noqa: TRY400
        raise SkippedRequestError("Apple Music's private key can't be read") from None
    _token = (signer, token, now + TOKEN_LIFETIME)
    return token


def search(term, *, types, next_path=None, background=False, limit=SEARCH_LIMIT):
    """Return one page of up to 25 search results, with Apple's ``next`` link."""
    params = {"limit": min(limit, SEARCH_LIMIT)}
    if next_path:
        # Apple's next link keeps the term and offset but drops the limit.
        return _get(next_path, params, background=background)
    params.update(term=term, types=_csv(types))
    return _get("search", params, background=background)


def albums(ids, *, views=(), include=(), background=False):
    """Return album resources for ``ids``, 100 per request; unknown IDs are left out."""
    resources = []
    for batch in _batches(ids, ALBUM_BATCH):
        params = {"ids": ",".join(batch), **_options(views, include)}
        resources.extend(_get("albums", params, background=background).get("data") or [])
    return resources


def album(album_id, *, views=(), include=("tracks",), background=False):
    """Return one album resource, with every page of its tracks."""
    response = _get(
        f"albums/{_path_value(album_id)}",
        _options(views, include),
        background=background,
    )
    resource = _first(response)
    tracks = (resource.get("relationships") or {}).get("tracks")
    next_path = tracks.pop("next", None) if tracks else None
    for _ in range(MAX_PAGES):
        if not next_path:
            break
        page = _get(next_path, background=background)
        tracks.setdefault("data", []).extend(page.get("data") or [])
        next_path = page.get("next")
    return resource


def albums_by_upc(upcs, *, background=False):
    """Return the albums for these barcodes, 25 per request.

    Apple returns each barcode's whole product family, not just the exact album.
    """
    resources = []
    for batch in _batches(upcs, FILTER_BATCH):
        params = {"filter[upc]": ",".join(batch)}
        resources.extend(_get("albums", params, background=background).get("data") or [])
    return resources


def songs_by_isrc(isrcs, *, background=False):
    """Return every song with these ISRCs, 25 per request: one per album it is on."""
    resources = []
    for batch in _batches(isrcs, FILTER_BATCH):
        params = {"filter[isrc]": ",".join(batch)}
        resources.extend(_get("songs", params, background=background).get("data") or [])
    return resources


def artist(artist_id, *, views=(), background=False):
    """Return one artist resource, with the requested views."""
    response = _get(
        f"artists/{_path_value(artist_id)}",
        _options(views, ()),
        background=background,
    )
    return _first(response)


def album_page(album_id):
    """Return one album with its tracks, artists, other versions and similar albums."""
    cache_key = f"apple_music_{PAGE_CACHE_VERSION}_album_page_{_path_value(album_id)}"
    resource = cache.get(cache_key)
    if resource is None:
        resource = album(album_id, views=PAGE_VIEWS, include=PAGE_INCLUDE)
        cache.set(cache_key, resource, PAGE_CACHE_TTL)
    return resource


def music(album_id):
    """Return an Apple Music album in Spine's music metadata shape.

    The payload's ``media_id`` is the identity of the album's listing, which can
    differ from ``album_id`` (a clean twin, an older upload). Its tracks and Apple
    link come from the listing's best member, which costs a second request only
    when that isn't the album asked for. One request serves both of the
    MusicBrainz page's stages. Every album the page sees gets an ``AppleAlbum`` row.
    """
    album_id = str(album_id)
    today = timezone.localdate()
    try:
        requested = album_page(album_id)
    except services.ProviderAPIError as error:
        if error.status_code != requests.codes.not_found:
            raise
        requested = _successor(album_id, today)
        if requested is None:
            raise
    identity, members = next(
        (identity, members)
        for identity, members in remember_listings(_family(requested), today=today)
        if any(member["id"] == requested["id"] for member in members)
    )
    content = requested
    best_id = music_listing.url_album_id(music_listing.pick_content_member(members, today=today))
    if best_id != music_listing.url_album_id(requested):
        try:
            content = album_page(best_id)
        except services.ProviderAPIError:
            logger.warning("Apple Music album %s is unavailable; showing %s", best_id, album_id)
    return _album_payload(identity, content)


def remember_listings(albums, *, today):
    """Return each listing in ``albums`` as ``(identity, members)``, recording them.

    Every album ID seen, its own and the one in its link, gets an ``AppleAlbum`` row
    naming its listing's identity. An identity never moves once an Item holds it:
    a listing whose members already point at a held ID outside it (the successor
    of a taken-down album) keeps that ID.
    """
    listings = music_listing.group_listings(albums)
    ids = {album_id for members in listings for album_id in _member_ids(members)}
    existing = dict(
        AppleAlbum.objects.filter(apple_album_id__in=ids).values_list("apple_album_id", "canonical_id"),
    )
    held = set(
        Item.objects.filter(
            source=Sources.APPLE_MUSIC.value,
            media_type=MediaTypes.MUSIC.value,
            media_id__in=ids | set(existing.values()),
        ).values_list("media_id", flat=True),
    )
    result = []
    rows = {}
    for members in listings:
        member_ids = _member_ids(members)
        pointed_at = {existing[album_id] for album_id in member_ids if album_id in existing}
        pinned = sorted((pointed_at & held) - member_ids)
        identity = pinned[0] if pinned else music_listing.pick_identity(members, held, today=today)
        result.append((identity, members))
        for member in members:
            upc = str((member.get("attributes") or {}).get("upc") or "").strip()
            for album_id in _member_ids([member]):
                rows[album_id] = (identity, upc)
    # Two upserts, so an album seen without its barcode keeps the one it has. Sorted, so
    # searches typed one after another upsert shared rows in the same order.
    for update_fields, has_upc in ((["canonical_id", "upc"], True), (["canonical_id"], False)):
        batch = [
            AppleAlbum(apple_album_id=album_id, canonical_id=identity, upc=upc)
            for album_id, (identity, upc) in sorted(rows.items())
            if bool(upc) == has_upc
        ]
        if batch:
            AppleAlbum.objects.bulk_create(
                batch,
                update_conflicts=True,
                unique_fields=["apple_album_id"],
                update_fields=update_fields,
            )
    return result


def saved_album(album_id):
    """Return a basic page for a saved album while Apple Music can't be reached.

    Built from the Item: its title, cover, release date and genres, with no tracks
    and no buttons. Marked so it is never cached in place of the full page.
    """
    item = Item.objects.filter(
        source=Sources.APPLE_MUSIC.value,
        media_type=MediaTypes.MUSIC.value,
        media_id=str(album_id),
        season_number=None,
        episode_number=None,
    ).first()
    if item is None:
        return None
    release_date = item.release_date.isoformat() if item.release_date else None
    return {
        "media_id": item.media_id,
        "source": Sources.APPLE_MUSIC.value,
        "source_url": None,
        "media_type": MediaTypes.MUSIC.value,
        "title": item.title,
        "subtitle": None,
        "image": item.image or settings.IMG_NONE,
        "poster_width": ARTWORK_SIZE,
        "poster_height": ARTWORK_SIZE,
        "poster_aspect_ratio": 1.0,
        "release_date": release_date,
        "max_progress": 1,
        "genres": _item_genres(item.media_id),
        "score": None,
        "score_count": None,
        "details": {"first_release_date": release_date},
        "external_links": {},
        "music": None,
        "related": {},
        services.ITEM_FALLBACK: True,
    }


def recording(album, song_id):
    """Return a song page's fields from its album's payload, or None if it isn't there.

    Apple gives a song's writers as one line of text, so they become one credit with
    no artist page. Songs have no works or alternative recordings.
    """
    release = (album.get("music") or {}).get("representative_release") or {}
    for medium in release.get("media") or []:
        for track in medium.get("tracks") or []:
            song = track.get("recording") or {}
            if str(song.get("recording_mbid")) != str(song_id):
                continue
            url = song.get("source_url")
            composer = song.get("composer_name")
            return {
                "recording_mbid": str(song_id),
                "title": song.get("title") or track.get("title") or "",
                "artist_credit": track.get("artist_credit") or [],
                "length_ms": song.get("length_ms"),
                "isrcs": song.get("isrcs") or [],
                "disambiguation": None,
                "first_release_date": song.get("first_release_date"),
                "is_video": bool(song.get("is_video")),
                "content_rating": track.get("content_rating"),
                "genres": album.get("genres") or [],
                "rating": None,
                "annotation": None,
                "credits": (
                    [{"artist_mbid": None, "name": composer, "roles": ["composer"]}]
                    if composer
                    else []
                ),
                "works": [],
                "alternative_recordings": [],
                "source_url": url,
                "external_links": {"Apple Music": url} if url else {},
                "releases": [
                    {
                        "release_mbid": release.get("release_mbid"),
                        "title": release.get("title") or "",
                        "status": release.get("status"),
                        "date": release.get("date"),
                        "country": release.get("country"),
                        "barcode": release.get("barcode"),
                        "release_group_mbid": album.get("media_id"),
                    },
                ],
                "context_release": {
                    "release_mbid": release.get("release_mbid"),
                    "medium_mbid": medium.get("medium_mbid"),
                    "track": track,
                },
            }
    return None


def search_albums(query, page, *, preserve_ranking_fields=False):
    """Search Apple Music albums, in Apple's order: one Apple page per Spine page.

    Each listing is shown once, at the best position of any of its members, with its
    identity and its best member's title, cover and date. Hidden listings and
    listings an earlier page showed are left out. ``preserve_ranking_fields`` keeps
    the fields All media search ranks by, with a boost for Apple's first albums.
    """
    page = max(1, int(page))
    state = _search_state(" ".join(str(query or "").split()), page)
    results = [dict(row) for row in state["results"]]
    if not preserve_ranking_fields:
        results = [{key: value for key, value in row.items() if key not in RANKING_KEYS} for row in results]
    # Apple stops after about 100 albums, so the total is what has been shown so far.
    return {
        "page": page,
        "total_results": len(state["seen"]),
        "total_pages": page + 1 if state["next"] else page,
        "results": results,
    }


def rank_boost(position):
    """Return the All media ranking boost for the listing at ``position`` (0 is first)."""
    return max(0, RANK_BOOST_TOP - RANK_BOOST_STEP * position)


def search_people(query, *, limit=10):
    """Search Apple Music artists."""
    response = search(query, types=("artists",), limit=limit)
    artists = ((response.get("results") or {}).get("artists") or {}).get("data") or []
    return [
        {
            "source": Sources.APPLE_MUSIC.value,
            "person_id": str(artist["id"]),
            "name": artist["attributes"]["name"],
            "profile_url": artwork_url(artist["attributes"].get("artwork"), max_length=None),
            "known_for_department": "Artist",
        }
        for artist in artists[:limit]
        if artist.get("id") and (artist.get("attributes") or {}).get("name")
    ]


def person_page(artist_id):
    """Return an Apple Music artist and their albums, in Spine's person page shape.

    The credits are Apple's own sections of the artist page, in Apple's order, so a
    standard album can sit behind its deluxe as it does in Apple Music. Each album is
    shown as its listing, which also folds Appears On's clean twins into one; hidden
    listings are left out. An album in several sections is one credit in each group.
    """
    cache_key = f"apple_music_{ARTIST_PAGE_CACHE_VERSION}_artist_page_{_path_value(artist_id)}"
    page = cache.get(cache_key)
    if page is None:
        page = _person_page(artist_id)
        cache.set(cache_key, page, ARTIST_PAGE_CACHE_TTL)
    return page


def _search_state(term, page):
    """Return a search page's rows, Apple's next link and the identities shown so far.

    Each page is cached, and a page reads the one before it to continue from Apple's
    next link and leave out the listings already shown.
    """
    empty = {"results": [], "next": None, "seen": []}
    if not term:
        return empty
    cached = cache.get(_search_key(term, page))
    if cached is not None:
        return cached
    state = None
    for number in range(1, page + 1):
        key = _search_key(term, number)
        current = cache.get(key) if number < page else None
        if current is None:
            if state is not None and not state["next"]:
                return {**empty, "seen": state["seen"]}
            current = _search_page(term, state)
            cache.set(key, current, SEARCH_CACHE_TTL)
        state = current
    return state


def _search_key(term, page):
    digest = hashlib.sha256(term.casefold().encode()).hexdigest()[:24]
    return f"apple_music_{SEARCH_CACHE_VERSION}_search_{digest}_{page}"


def _search_page(term, previous):
    """Fetch the search page after ``previous`` (the first when None): two requests.

    The search itself, then one batch of its albums with their version families, so
    the listing rule can fold clean twins and re-uploads.
    """
    if previous is None:
        response, seen = search(term, types=("albums",)), []
    else:
        response = search(term, types=("albums",), next_path=previous["next"])
        seen = previous["seen"]
    found = (response.get("results") or {}).get("albums") or {}
    today = timezone.localdate()
    found_albums = [album for album in found.get("data") or [] if album.get("id")]
    listings = _listings_with_families(found_albums, today=today)
    shown = set(seen)
    rows = []
    for album in found_albums:
        identity, members = listings[str(album["id"])]
        if identity in shown or music_listing.is_hidden(members, today):
            continue
        shown.add(identity)
        rows.append(_search_row(identity, members, position=len(seen) + len(rows), today=today))
    return {
        "results": rows,
        "next": found.get("next") or None,
        "seen": [*seen, *(row["media_id"] for row in rows)],
    }


def _listings_with_families(found_albums, *, today):
    """Return each album's ``(identity, members)`` listing, by album ID, and record them.

    One batch request fetches the albums' version families; an album Apple leaves out
    of it is a listing of its own.
    """
    ids = list(dict.fromkeys(str(album["id"]) for album in found_albums))
    batch = {str(album["id"]): album for album in albums(ids, views=(music_listing.FAMILY_VIEW,))}
    by_id = {str(album["id"]): album for album in found_albums}
    family = [member for album_id in ids for member in _family(batch.get(album_id, by_id[album_id]))]
    listings = {}
    for identity, members in remember_listings(family, today=today):
        for album_id in _member_ids(members):
            listings.setdefault(album_id, (identity, members))
    return listings


def _search_row(identity, members, *, position, today):
    album = music_listing.pick_content_member(members, today=today)
    attributes = album.get("attributes") or {}
    title, primary_type = album_title(attributes)
    artist = attributes.get("artistName") or None
    release_date = attributes.get("releaseDate") or None
    content_rating = attributes.get("contentRating") or None
    # A listing whose best member is clean has no explicit album to show instead.
    subtitle = " · ".join(
        part
        for part in (
            artist,
            release_date[:4] if release_date else None,
            primary_type,
            "Clean" if content_rating == "clean" else None,
        )
        if part
    )
    return {
        "media_id": identity,
        "source": Sources.APPLE_MUSIC.value,
        "media_type": MediaTypes.MUSIC.value,
        "title": title,
        "artist_credits": [{"artist_mbid": None, "name": artist, "join_phrase": ""}] if artist else [],
        "release_date": release_date,
        "first_release_date": release_date,
        "primary_type": primary_type,
        "secondary_types": ["Compilation"] if attributes.get("isCompilation") else [],
        "disambiguation": None,
        "content_rating": content_rating,
        "image": artwork_url(attributes.get("artwork")) or settings.IMG_NONE,
        "poster_width": ARTWORK_SIZE,
        "poster_height": ARTWORK_SIZE,
        "poster_aspect_ratio": 1.0,
        "subtitle": subtitle or None,
        "artist_name": artist,
        "provider_rank_boost": rank_boost(position),
    }


def _person_page(artist_id):
    resource = artist(artist_id, views=ARTIST_VIEWS)
    attributes = resource.get("attributes") or {}
    sections = [(view, _artist_view_albums(resource, view)) for view in ARTIST_VIEWS]
    today = timezone.localdate()
    listings = _listings_with_families([album for _, entries in sections for album in entries], today=today)
    credits = {}
    for view, entries in sections:
        for entry in entries:
            identity, members = listings[str(entry["id"])]
            if music_listing.is_hidden(members, today):
                continue
            role = ARTIST_VIEW_ROLES.get(view) or (
                "EPs" if album_title(entry.get("attributes"))[1] == "EP" else "Singles"
            )
            credit = credits.get(identity)
            if credit is None:
                credits[identity] = _artist_credit(identity, members, role, today=today)
            elif role not in credit["credit_roles"]:
                credit["credit_roles"].append(role)
    return {
        "source": Sources.APPLE_MUSIC.value,
        "person_id": str(resource.get("id") or artist_id),
        "name": attributes.get("name") or "",
        "image": artwork_url(attributes.get("artwork"), max_length=None) or settings.IMG_NONE,
        "biography": None,
        "known_for_department": "Artist",
        "birth_date": None,
        "death_date": None,
        "place_of_birth": None,
        "popularity": None,
        "credits": list(credits.values()),
    }


def _artist_view_albums(resource, view):
    """Return up to 100 albums from one section of an artist page, following ``next``."""
    section = (resource.get("views") or {}).get(view) or {}
    entries = [album for album in section.get("data") or [] if album.get("id")]
    next_path = section.get("next")
    for _ in range(MAX_PAGES):
        if not next_path or len(entries) >= ARTIST_VIEW_LIMIT:
            break
        more = _get(next_path, {"limit": ARTIST_VIEW_LIMIT})
        entries.extend(album for album in more.get("data") or [] if album.get("id"))
        next_path = more.get("next")
    return entries[:ARTIST_VIEW_LIMIT]


def _artist_credit(identity, members, role, *, today):
    album = music_listing.pick_content_member(members, today=today)
    attributes = album.get("attributes") or {}
    release_date = attributes.get("releaseDate") or None
    return {
        "media_type": MediaTypes.MUSIC.value,
        "source": Sources.APPLE_MUSIC.value,
        "media_id": identity,
        "title": album_title(attributes)[0],
        "image": artwork_url(attributes.get("artwork")) or settings.IMG_NONE,
        "release_date": release_date,
        "year": release_date[:4] if release_date else None,
        "genres": music_catalog.apple_genre_names(attributes.get("genreNames")),
        "content_rating": attributes.get("contentRating") or None,
        "roles": ["Artist"],
        "credit_roles": [role],
        "vote_average": None,
        "vote_count": None,
    }


def artwork_url(artwork, size=ARTWORK_SIZE, *, max_length=IMAGE_MAX_LENGTH):
    """Return Apple's artwork template filled in at ``size``, or None if too long to store.

    ``max_length=None`` allows any length, for pictures that are never an Item's.
    """
    template = str((artwork or {}).get("url") or "")
    if not template:
        return None
    url = (
        template.replace("{w}", str(size))
        .replace("{h}", str(size))
        .replace("{c}", "bb")
        .replace("{f}", "jpg")
    )
    return url if max_length is None or len(url) <= max_length else None


def album_title(attributes):
    """Return an album's name without " - EP" or " - Single", and its type."""
    name = str((attributes or {}).get("name") or "").strip()
    match = _TYPE_SUFFIX.search(name)
    if match:
        return name[: match.start()].strip(), "EP" if match[1].casefold() == "ep" else "Single"
    return name, "Single" if (attributes or {}).get("isSingle") else "Album"


def _family(album):
    """Return an album and its other versions, each with the whole family as its own.

    Apple's version family is symmetric, and its members come without views of
    their own, so each member's family is the album and the other members.
    """
    view = (album.get("views") or {}).get(music_listing.FAMILY_VIEW) or {}
    members = [album, *(view.get("data") or [])]
    ids = [str(member.get("id") or "") for member in members]
    family = [album]
    for member in members[1:]:
        others = [{"id": album_id} for album_id in ids if album_id != str(member.get("id") or "")]
        family.append({**member, "views": {music_listing.FAMILY_VIEW: {"data": others}}})
    return family


def _member_ids(members):
    ids = set()
    for member in members:
        ids.update(value for value in (str(member.get("id") or ""), music_listing.url_album_id(member)) if value)
    return ids


def _successor(album_id, today):
    """Return the album that replaced a taken-down album an Item holds, or None.

    The replacement has the same normalized title and comes back for the album's
    stored barcode, which Apple answers with the barcode's whole product family.
    It is recorded as an alias of the held ID, so the Item keeps its identity.
    """
    item = Item.objects.filter(
        source=Sources.APPLE_MUSIC.value,
        media_type=MediaTypes.MUSIC.value,
        media_id=album_id,
    ).first()
    if item is None:
        return None
    known = (
        AppleAlbum.objects.filter(canonical_id=album_id)
        .exclude(apple_album_id=album_id)
        .values_list("apple_album_id", flat=True)
        .first()
    )
    if known:
        try:
            return album_page(known)
        except services.ProviderAPIError:
            logger.warning("Apple Music alias %s of %s is unavailable", known, album_id)
    upc = AppleAlbum.objects.filter(apple_album_id=album_id).values_list("upc", flat=True).first()
    if not upc:
        return None
    title = music_listing.normalize_title(item.title)
    candidates = [
        candidate
        for candidate in albums_by_upc([upc])
        if music_listing.url_album_id(candidate) != album_id
        and music_listing.normalize_title(album_title(candidate.get("attributes"))[0]) == title
    ]
    if not candidates:
        return None
    successor_id = music_listing.url_album_id(music_listing.pick_content_member(candidates, today=today))
    AppleAlbum.objects.update_or_create(apple_album_id=successor_id, defaults={"canonical_id": album_id})
    logger.info("Apple Music album %s was taken down; showing %s", album_id, successor_id)
    return album_page(successor_id)


def _album_payload(identity, album):
    attributes = album.get("attributes") or {}
    title, primary_type = album_title(attributes)
    artist = attributes.get("artistName") or None
    url = attributes.get("url") or None
    credits = _artist_credits(album)
    bridge = _bridge(identity, music_listing.url_album_id(album))
    release_date = _release_date(attributes.get("releaseDate"), bridge)
    secondary_types = ["Compilation"] if attributes.get("isCompilation") else []
    media = _media(album, credits)
    label = attributes.get("recordLabel")
    return {
        "media_id": identity,
        "source": Sources.APPLE_MUSIC.value,
        "source_url": url,
        "media_type": MediaTypes.MUSIC.value,
        "title": title,
        "subtitle": artist,
        "image": artwork_url(attributes.get("artwork")) or settings.IMG_NONE,
        "poster_width": ARTWORK_SIZE,
        "poster_height": ARTWORK_SIZE,
        "poster_aspect_ratio": 1.0,
        "release_date": release_date,
        "max_progress": 1,
        "genres": _genres(identity, attributes, bridge),
        # No community score and no editor's notes on Apple albums.
        "score": None,
        "score_count": None,
        "details": {
            "artist": artist,
            "artist_credits": credits,
            "first_release_date": release_date,
            "primary_type": primary_type,
            "secondary_types": secondary_types,
            "disambiguation": None,
            "release_count": None,
            "annotation": None,
        },
        "external_links": {"Apple Music": url} if url else {},
        "music": {
            # The MusicBrainz-named fields installed app builds require hold the
            # Apple album ID.
            "release_group_mbid": identity,
            "primary_type": primary_type,
            "secondary_types": secondary_types,
            "disambiguation": None,
            "annotation": None,
            "first_release_date": release_date,
            "release_count": None,
            "artist_credit": credits,
            "content_rating": attributes.get("contentRating") or None,
            "cover_art": {
                "source": Sources.APPLE_MUSIC.value,
                "release_group_mbid": identity,
                # True keeps installed builds from crediting the Cover Art Archive.
                "fallback_used": True,
            },
            "representative_release": {
                "release_mbid": identity,
                "title": title,
                "status": "Official",
                "date": attributes.get("releaseDate") or None,
                "country": _storefront().upper(),
                "barcode": attributes.get("upc") or None,
                "selection_basis": Sources.APPLE_MUSIC.value,
                "labels": [{"label_mbid": None, "name": label, "catalog_number": None}] if label else [],
                "format": "Digital Media",
                "is_deluxe_or_remastered": bool(_EDITION_WORDS.search(title)),
                "streaming_links": _streaming_links(identity, title, artist, url, bridge),
                "disc_count": len(media),
                "track_count": sum(medium["track_count"] for medium in media),
                "media": media,
            },
        },
        "related": {},
    }


def _artist_credits(album):
    """Return one credit per album artist, joined as "A, B & C"."""
    artists = [
        artist
        for artist in ((album.get("relationships") or {}).get("artists") or {}).get("data") or []
        if artist.get("id") and (artist.get("attributes") or {}).get("name")
    ]
    if not artists:
        name = (album.get("attributes") or {}).get("artistName")
        return [{"artist_mbid": None, "name": name, "join_phrase": ""}] if name else []
    credits = []
    for index, artist in enumerate(artists):
        remaining = len(artists) - index - 1
        credits.append(
            {
                # Never an Apple ID here: installed builds open artist_mbid as a
                # MusicBrainz artist.
                "artist_mbid": None,
                "name": artist["attributes"]["name"],
                "join_phrase": ", " if remaining > 1 else " & " if remaining else "",
                "artist_source": Sources.APPLE_MUSIC.value,
                "artist_id": str(artist["id"]),
            },
        )
    return credits


def _media(album, credits):
    """Return the album's discs and tracks; track IDs are made unique if a song repeats."""
    album_artist = (album.get("attributes") or {}).get("artistName")
    tracks = ((album.get("relationships") or {}).get("tracks") or {}).get("data") or []
    discs = {}
    seen = Counter()
    for index, song in enumerate(tracks, start=1):
        attributes = song.get("attributes") or {}
        song_id = str(song.get("id") or "")
        seen[song_id] += 1
        disc = _positive_int(attributes.get("discNumber")) or 1
        position = _positive_int(attributes.get("trackNumber")) or index
        title = attributes.get("name") or ""
        artist = attributes.get("artistName")
        isrc = attributes.get("isrc")
        discs.setdefault(disc, []).append(
            {
                "track_mbid": song_id if seen[song_id] == 1 else f"{song_id}-{seen[song_id]}",
                "disc_number": disc,
                "position": position,
                "number": str(position),
                "title": title,
                "length_ms": attributes.get("durationInMillis"),
                "artist_credit": (
                    credits
                    if not artist or artist == album_artist
                    else [{"artist_mbid": None, "name": artist, "join_phrase": ""}]
                ),
                "content_rating": attributes.get("contentRating") or None,
                "recording": {
                    "recording_mbid": song_id,
                    "title": title,
                    "length_ms": attributes.get("durationInMillis"),
                    "disambiguation": None,
                    "first_release_date": attributes.get("releaseDate") or None,
                    "is_video": song.get("type") == "music-videos",
                    "isrcs": [isrc] if isrc else [],
                    "composer_name": attributes.get("composerName") or None,
                    "source_url": attributes.get("url") or None,
                },
            },
        )
    return [
        {
            "medium_mbid": None,
            "position": disc,
            "title": None,
            "format": "Digital Media",
            "track_count": len(disc_tracks),
            "tracks": sorted(disc_tracks, key=lambda track: track["position"]),
        }
        for disc, disc_tracks in sorted(discs.items())
    ]


def _bridge(identity, content_id):
    """Return the MusicBrainz bridge for an album page: its identity's row, else its content's."""
    rows = {
        row.apple_album_id: row
        for row in AppleAlbum.objects.filter(apple_album_id__in={identity, content_id})
    }
    for row in (rows.get(identity), rows.get(content_id)):
        if row is not None and (row.release_group_mbid or row.genres or row.spotify_url):
            return row
    return None


def _release_date(apple_date, bridge):
    """Return the earlier of Apple's date and MusicBrainz's first release date.

    MusicBrainz's date counts only when the bridge's release group has this title,
    and only when it is surely earlier: "1982" isn't earlier than 1982-11-29.
    """
    musicbrainz_date = bridge.first_release_date if bridge is not None and bridge.same_title else ""
    last_musicbrainz_day = music_listing.release_day(musicbrainz_date)
    if last_musicbrainz_day is None:
        return apple_date or None
    first_apple_day = _first_day(apple_date)
    if first_apple_day is None or last_musicbrainz_day < first_apple_day:
        return musicbrainz_date
    return apple_date


def _first_day(value):
    """Return the first day a release date could mean ("2010" is 2010-01-01), or None."""
    if music_listing.release_day(value) is None:
        return None
    year, month, day = [*(int(part) for part in str(value).strip().split("-")), 1, 1][:3]
    return date(year, month, day)


def _genres(identity, attributes, bridge):
    """Return MusicBrainz genre names: the bridge's, else the Item's, else Apple's mapped.

    Album pages rewrite the Item's genre filters from this list, so it never holds
    fewer genres than the Item already has.
    """
    item_genres = _item_genres(identity)
    bridge_genres = [str(genre) for genre in (bridge.genres if bridge is not None else []) if genre]
    genres = bridge_genres or item_genres or music_catalog.apple_genre_names(attributes.get("genreNames"))
    return genres if len(genres) >= len(item_genres) else item_genres


def _item_genres(album_id):
    return sorted(
        ItemFilterFacet.objects.filter(
            item__source=Sources.APPLE_MUSIC.value,
            item__media_type=MediaTypes.MUSIC.value,
            item__media_id=str(album_id),
            item__season_number=None,
            item__episode_number=None,
            facet_type=ItemFilterFacet.FacetType.GENRE,
        ).values_list("value", flat=True),
    )


def _streaming_links(identity, title, artist, url, bridge):
    """Return Apple Music's link, then Spotify's: the exact album when known, else a search."""
    links = [{"service": "music.apple.com", "url": url, "kind": "album"}] if url else []
    spotify = bridge.spotify_url if bridge is not None and bridge.spotify_exact else ""
    spotify = spotify or (
        MusicAlbumLink.objects.filter(apple_album_id=identity, status__in=_SPOTIFY_LINK_STATUSES)
        .exclude(spotify_url="")
        .values_list("spotify_url", flat=True)
        .first()
    )
    if spotify:
        links.append({"service": "open.spotify.com", "url": spotify, "kind": "album"})
    elif title:
        query = quote(" ".join(part for part in (artist, title) if part), safe="")
        links.append(
            {
                "service": "open.spotify.com",
                "url": SPOTIFY_SEARCH_URL.format(query=query),
                "kind": "search",
            },
        )
    return links


def _positive_int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    return max(value, 0)


@sensitive_variables("token", "headers")
def _get(path, params=None, *, background=False):
    """GET a catalog path (``albums/1440826538``) or an Apple ``next`` link.

    Requests someone is waiting on use the user-facing bucket, a 10-second timeout and
    no 429 retries; a 429, 5xx or timeout pauses them for a minute. ``background``
    requests use the slower bucket and the usual retries, and ignore the pause.
    """
    url = _url(path)
    token = developer_token()
    if not background and is_paused():
        raise SkippedRequestError("Apple Music is paused for a minute after a failed request")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    if background:
        request_session, timeout = services.apple_music_background_session, None
    else:
        request_session, timeout = services.apple_music_session, USER_TIMEOUT
    try:
        return services.api_request(
            Sources.APPLE_MUSIC.value,
            "GET",
            url,
            params=params,
            headers=headers,
            request_session=request_session,
            timeout=timeout,
            retry_rate_limits=background,
        )
    except requests.exceptions.JSONDecodeError as error:
        raise services.ProviderAPIError(
            Sources.APPLE_MUSIC.value,
            error,
            "invalid JSON response",
        ) from error
    except requests.exceptions.HTTPError as error:
        status_code = getattr(error.response, "status_code", None) or 0
        if not background and (
            status_code == requests.codes.too_many_requests
            or status_code >= requests.codes.internal_server_error
        ):
            _back_off()
        raise services.ProviderAPIError(Sources.APPLE_MUSIC.value, error) from error
    except requests.exceptions.Timeout as error:
        if not background:
            _back_off()
        raise services.ProviderAPIError(Sources.APPLE_MUSIC.value, error) from error
    except requests.RequestException as error:
        raise services.ProviderAPIError(Sources.APPLE_MUSIC.value, error) from error


def is_paused():
    """Return whether user-facing Apple calls are paused after a failed request."""
    return bool(services.redis_db.exists(backoff_key()))


def backoff_key():
    # In Redis rather than the Django cache, which is a dummy cache under DEBUG.
    prefix = f"{settings.REDIS_PREFIX}:" if settings.REDIS_PREFIX else ""
    return prefix + BACKOFF_KEY


def _back_off():
    services.redis_db.set(backoff_key(), 1, ex=BACKOFF_SECONDS)


def _url(path):
    # Only paths on Apple's API host are requested, so the token never goes elsewhere.
    if path.startswith("/v1/"):
        return f"{APPLE_MUSIC_URL}{path}"
    if path.startswith("/") or "://" in path:
        msg = f"Not an Apple Music API path: {path}"
        raise ValueError(msg)
    return f"{APPLE_MUSIC_URL}/v1/catalog/{_storefront()}/{path}"


def _storefront():
    return str(settings.MUSIC_DEFAULT_MARKET or "us").strip().lower()


def _options(views, include):
    options = {}
    if views:
        options["views"] = _csv(views)
    if include:
        options["include"] = _csv(include)
    return options


def _csv(values):
    if isinstance(values, str):
        return values
    return ",".join(values)


def _batches(values, size):
    unique = list(dict.fromkeys(str(value).strip() for value in values if value))
    unique = [value for value in unique if value]
    return [unique[start : start + size] for start in range(0, len(unique), size)]


def _first(response):
    data = response.get("data") if isinstance(response, dict) else None
    if not data:
        raise services.ProviderAPIError(
            Sources.APPLE_MUSIC.value,
            ValueError("no data in response"),
            "response has no data",
        )
    return data[0]


def _path_value(value):
    return quote(str(value), safe="")
