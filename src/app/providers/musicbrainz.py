import hashlib
import logging
import re
import unicodedata
from contextlib import suppress
from datetime import date
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from django.conf import settings
from django.core.cache import cache
from redis.exceptions import LockError, RedisError

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services
from app.providers.search_rank import rank_results

MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2"
LISTENBRAINZ_URL = "https://api.listenbrainz.org/1/popularity/release-group"
COVER_ART_URL = "https://coverartarchive.org"
COVER_ART_PROVIDER = "cover art archive"
CACHE_VERSION = "v2"
LISTENBRAINZ_CACHE_VERSION = "v1"
RESOLVER_VERSION = "v1"
RECORDING_CACHE_VERSION = "v2"
ARTIST_CACHE_VERSION = "v2"
SEARCH_CACHE_TTL = 6 * 60 * 60
LISTENBRAINZ_FAILURE_CACHE_TTL = 5 * 60
DETAIL_CACHE_TTL = 24 * 60 * 60
MISSING_COVER_CACHE_TTL = 6 * 60 * 60
CACHE_LOCK_TIMEOUT = 120
SEARCH_CANDIDATE_LIMIT = 100
ARTIST_RELEASE_GROUP_LIMIT = 500
ARTIST_RELEASE_GROUP_PAGE_SIZE = 100
LISTENBRAINZ_TIMEOUT = 5
MAX_ATTEMPTS = 3
_CACHE_MISS = object()
_LUCENE_SPECIAL = re.compile(r"(&&|\|\||[+\-!(){}\[\]^\"~*?:\\/])")
_STREAMING_RELATION_IDS = {
    "08445ccf-7b99-4438-9f9a-fb9ac18099ee",
    "320adf26-96fa-4183-9045-1f5f32f833cb",
}
_SONGWRITING_ROLES = ("writer", "composer", "lyricist")
_EDITION_MARKERS = (
    "collector's edition",
    "definitive edition",
    "legacy edition",
    "special edition",
    "super deluxe",
    "anniversary",
    "remastered",
    "collector edition",
    "expanded",
    "deluxe",
    "remaster",
    "bonus",
    "d.l.x.",
    "dlx",
)
logger = logging.getLogger(__name__)


def escape_lucene(term):
    """Escape Lucene operators without changing Unicode search text."""
    return _LUCENE_SPECIAL.sub(r"\\\1", str(term))


def search_release_groups(query, *, limit=settings.PER_PAGE, offset=0, timeout=None):
    """Search MusicBrainz release groups and return the raw response."""
    query = str(query)
    digest = hashlib.sha256(query.encode()).hexdigest()
    return _cached(
        f"musicbrainz_{CACHE_VERSION}_release_group_search_{digest}_{limit}_{offset}",
        SEARCH_CACHE_TTL,
        lambda: _musicbrainz_request(
            "release-group",
            {"query": _release_group_query(query), "limit": limit, "offset": offset},
            timeout=timeout,
        ),
    )


def search(query, page, *, preserve_ranking_fields=False, timeout=None):
    """Search release groups and return Spine-normalized album results."""
    page = max(1, int(page))
    search_options = {"timeout": timeout} if timeout is not None else {}
    response = search_release_groups(
        query,
        limit=SEARCH_CANDIDATE_LIMIT,
        offset=0,
        **search_options,
    )
    candidates = [
        _search_result(group) for group in response.get("release-groups", [])
    ]
    popularity = (
        {}
        if preserve_ranking_fields
        else lookup_release_group_popularity(
            [candidate["media_id"] for candidate in candidates],
        )
    )
    if popularity:
        for candidate in candidates:
            candidate.pop("provider_rank_boost", None)
            candidate.update(popularity.get(candidate["media_id"], {}))

    ranked = rank_results(
        query,
        candidates,
        MediaTypes.MUSIC.value,
        preserve_ranking_fields=preserve_ranking_fields,
    )
    if preserve_ranking_fields:
        for candidate in ranked:
            candidate.pop("provider_rank_boost", None)
    start = (page - 1) * settings.PER_PAGE
    results = ranked[start : start + settings.PER_PAGE]
    try:
        total_results = int(response.get("count", len(ranked)))
    except (TypeError, ValueError):
        total_results = len(ranked)
    total_results = min(max(0, total_results), SEARCH_CANDIDATE_LIMIT)
    return helpers.format_search_response(
        page,
        settings.PER_PAGE,
        total_results,
        results,
    )


def search_people(query, *, limit=10, timeout=None):
    """Search MusicBrainz for human artists."""
    response = _musicbrainz_request(
        "artist",
        {
            "query": f'artist:"{escape_lucene(query)}" AND type:person',
            "limit": limit,
        },
        timeout=timeout,
        max_attempts=1,
        retry_rate_limits=False,
    )
    return [
        {
            "source": Sources.MUSICBRAINZ.value,
            "person_id": str(artist["id"]),
            "name": artist.get("name") or "",
            "profile_url": None,
            "known_for_department": "Artist",
        }
        for artist in response.get("artists") or []
        if artist.get("id") and artist.get("name")
    ]


def discover(*, page=1, page_size=None, genre=None):
    """Discover popular official release groups carrying an exact genre."""
    genre = " ".join(str(genre or "").split()).casefold()
    if not genre:
        raise ValueError("genre is required for MusicBrainz discovery.")

    page = max(1, int(page))
    per_page = page_size or settings.PER_PAGE
    query = f'tag:"{escape_lucene(genre)}" AND status:official'
    digest = hashlib.sha256(query.encode()).hexdigest()
    response = _cached(
        (
            f"musicbrainz_{CACHE_VERSION}_genre_discover_{digest}_"
            f"{SEARCH_CANDIDATE_LIMIT}_0"
        ),
        SEARCH_CACHE_TTL,
        lambda: _musicbrainz_request(
            "release-group",
            {
                "query": query,
                "limit": SEARCH_CANDIDATE_LIMIT,
                "offset": 0,
            },
        ),
    )
    candidates = [
        _search_result(group)
        for group in response.get("release-groups", [])
    ]
    popularity = lookup_release_group_popularity(
        [candidate["media_id"] for candidate in candidates],
    )
    ranked = _rank_genre_results(candidates, popularity)
    start = (page - 1) * per_page
    results = ranked[start : start + per_page]
    try:
        total_results = int(response.get("count", len(ranked)))
    except (TypeError, ValueError):
        total_results = len(ranked)
    total_results = min(max(0, total_results), SEARCH_CANDIDATE_LIMIT)
    data = helpers.format_search_response(
        page,
        per_page,
        total_results,
        results,
    )
    data["per_page"] = per_page
    return data


def _rank_genre_results(candidates, popularity):
    """Rank exact genre matches by audience breadth, listens, then relevance."""
    def key(candidate):
        counts = popularity.get(candidate["media_id"], {})
        known = any(
            counts.get(name) is not None
            for name in ("total_user_count", "total_listen_count")
        )
        return (
            known,
            _popularity_count(counts.get("total_user_count")) or 0,
            _popularity_count(counts.get("total_listen_count")) or 0,
            _popularity_count(candidate.get("search_score")) or 0,
            bool(candidate.get("release_date")),
        )

    ranked = sorted(candidates, key=key, reverse=True)
    for candidate in ranked:
        candidate.pop("provider_rank_boost", None)
    return ranked


def lookup_release_group_popularity(release_group_mbids):
    """Return cached ListenBrainz popularity for a batch of release groups."""
    token = str(settings.LISTENBRAINZ_TOKEN or "").strip()
    mbids = list(dict.fromkeys(str(mbid) for mbid in release_group_mbids if mbid))
    if not token or not mbids:
        return {}

    digest = hashlib.sha256("\n".join(sorted(mbids)).encode()).hexdigest()
    cache_key = (
        f"listenbrainz_{LISTENBRAINZ_CACHE_VERSION}_release_group_popularity_"
        f"{digest}"
    )
    cached = cache.get(cache_key, _CACHE_MISS)
    if cached is not _CACHE_MISS:
        return cached

    try:
        response = services.api_request(
            "listenbrainz",
            "POST",
            LISTENBRAINZ_URL,
            params={"release_group_mbids": mbids},
            headers={
                **_headers(),
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
            },
            request_session=services.session,
            timeout=LISTENBRAINZ_TIMEOUT,
        )
        if not isinstance(response, list):
            msg = "ListenBrainz popularity response must be a list"
            raise TypeError(msg)
        requested = set(mbids)
        popularity = {}
        for item in response:
            if not isinstance(item, dict):
                continue
            mbid = str(item.get("release_group_mbid") or "")
            if mbid not in requested:
                continue
            popularity[mbid] = {
                "total_listen_count": _popularity_count(
                    item.get("total_listen_count"),
                ),
                "total_user_count": _popularity_count(
                    item.get("total_user_count"),
                ),
            }
    except (requests.RequestException, TypeError, ValueError) as error:
        logger.warning("ListenBrainz popularity unavailable: %s", error)
        cache.set(cache_key, {}, LISTENBRAINZ_FAILURE_CACHE_TTL)
        return {}

    cache.set(cache_key, popularity, SEARCH_CACHE_TTL)
    return popularity


def lookup_release_group(release_group_mbid):
    """Look up one MusicBrainz release group."""
    mbid = _path_value(release_group_mbid)
    return _cached(
        f"musicbrainz_{CACHE_VERSION}_release_group_{mbid}",
        DETAIL_CACHE_TTL,
        lambda: _musicbrainz_request(
            f"release-group/{mbid}",
            {
                "inc": (
                    "artist-credits+releases+genres+ratings+annotation+url-rels"
                ),
            },
        ),
    )


def lookup_artist(artist_mbid):
    """Look up one MusicBrainz artist."""
    mbid = _path_value(artist_mbid)
    return _cached(
        f"musicbrainz_artist_{ARTIST_CACHE_VERSION}_{mbid}",
        DETAIL_CACHE_TTL,
        lambda: _musicbrainz_request(
            f"artist/{mbid}",
            {"inc": "annotation+url-rels"},
        ),
    )


def browse_artist_release_groups(artist_mbid, *, limit=ARTIST_RELEASE_GROUP_PAGE_SIZE, offset=0):
    """Browse one cached page of an artist's primary release groups."""
    mbid = _path_value(artist_mbid)
    return _cached(
        f"musicbrainz_artist_release_groups_{ARTIST_CACHE_VERSION}_{mbid}_{limit}_{offset}",
        DETAIL_CACHE_TTL,
        lambda: _musicbrainz_request(
            "release-group",
            {
                "artist": artist_mbid,
                "release-group-status": "website-default",
                "inc": "artist-credits+genres+ratings",
                "limit": limit,
                "offset": offset,
            },
        ),
    )


def person_page(artist_mbid):
    """Return MusicBrainz artist details and release-group credits."""
    mbid = _path_value(artist_mbid)

    def fetch():
        artist = lookup_artist(artist_mbid)
        groups = []
        offset = 0
        while offset < ARTIST_RELEASE_GROUP_LIMIT:
            response = browse_artist_release_groups(
                artist_mbid,
                limit=min(
                    ARTIST_RELEASE_GROUP_PAGE_SIZE,
                    ARTIST_RELEASE_GROUP_LIMIT - offset,
                ),
                offset=offset,
            )
            page = response.get("release-groups") or []
            groups.extend(page)
            offset += len(page)
            total = response.get("release-group-count", response.get("count", offset))
            try:
                total = int(total)
            except (TypeError, ValueError):
                total = offset
            if not page or offset >= total:
                break

        credits_by_id = {}
        for group in groups[:ARTIST_RELEASE_GROUP_LIMIT]:
            if group.get("id"):
                credits_by_id.setdefault(group["id"], _artist_release_group_credit(group))
        credits = list(credits_by_id.values())
        credits.sort(key=lambda credit: (credit.get("title") or "").casefold())
        credits.sort(key=lambda credit: credit.get("release_date") or "", reverse=True)

        life_span = artist.get("life-span") or {}
        begin_area = artist.get("begin-area") or {}
        area = artist.get("area") or {}
        return {
            "source": Sources.MUSICBRAINZ.value,
            "person_id": str(artist.get("id") or artist_mbid),
            "name": artist.get("name") or "",
            "image": _artist_image(artist.get("relations")),
            "biography": None,
            "known_for_department": "Artist",
            "birth_date": life_span.get("begin") or None,
            "death_date": life_span.get("end") or None,
            "place_of_birth": begin_area.get("name") or area.get("name") or None,
            "popularity": None,
            "credits": credits,
        }

    return _cached(
        f"musicbrainz_artist_page_{ARTIST_CACHE_VERSION}_{mbid}",
        DETAIL_CACHE_TTL,
        fetch,
    )


def music(release_group_mbid, *, include_enrichment=True):
    """Return one release group normalized to Spine's media metadata contract."""
    release_group_mbid = str(release_group_mbid)
    group = lookup_release_group(release_group_mbid)
    credits = _artist_credits(group.get("artist-credit"))
    artist = _artist_credit_text(credits)
    first_release_date = group.get("first-release-date") or None
    primary_type = group.get("primary-type") or group.get("type") or None
    source_url = (
        f"https://musicbrainz.org/release-group/{_path_value(release_group_mbid)}"
    )
    rating = group.get("rating") or {}

    data = {
        "media_id": release_group_mbid,
        "source": Sources.MUSICBRAINZ.value,
        "source_url": source_url,
        "media_type": MediaTypes.MUSIC.value,
        "title": group.get("title") or "",
        "subtitle": artist,
        # Point straight at the Cover Art Archive image, as search results do,
        # instead of first asking the archive whether a front cover exists.
        "image": _cover_art_url(release_group_mbid),
        "poster_width": 500,
        "poster_height": 500,
        "poster_aspect_ratio": 1.0,
        "release_date": first_release_date,
        "max_progress": 1,
        "genres": _genre_names(group.get("genres")),
        "score": rating.get("value"),
        "score_count": rating.get("votes-count"),
        "details": {
            "artist": artist,
            "artist_credits": credits,
            "first_release_date": first_release_date,
            "primary_type": primary_type,
            "secondary_types": group.get("secondary-types") or [],
            "disambiguation": group.get("disambiguation") or None,
            "release_count": group.get("release-count"),
            "annotation": _annotation(group.get("annotation")),
        },
        "external_links": _external_links(group.get("relations"), source_url),
        "music": {
            "release_group_mbid": release_group_mbid,
            "primary_type": primary_type,
            "secondary_types": group.get("secondary-types") or [],
            "disambiguation": group.get("disambiguation") or None,
            "annotation": _annotation(group.get("annotation")),
            "first_release_date": first_release_date,
            "release_count": group.get("release-count"),
            "artist_credit": credits,
            "cover_art": {
                "source": "cover_art_archive",
                "release_group_mbid": release_group_mbid,
                "fallback_used": False,
            },
            "representative_release": None,
        },
    }
    if not include_enrichment:
        return data

    data["music"]["representative_release"] = _music_enrichment(
        release_group_mbid,
        group.get("title") or "",
    )
    return data


def _music_enrichment(release_group_mbid, title):
    """Resolve the representative release (edition and tracklist) for an album."""
    try:
        resolved = resolve_representative_release(release_group_mbid, title)
        representative_release = (
            _representative_release(resolved["release"], resolved["reason"])
            if resolved["release"] is not None
            else None
        )
        if representative_release is not None:
            logger.info(
                "Selected MusicBrainz representative release group=%s release=%s reason=%s",
                release_group_mbid,
                representative_release["release_mbid"],
                resolved["reason"],
            )
    except (services.ProviderAPIError, AttributeError, KeyError, TypeError, ValueError):
        logger.exception(
            "MusicBrainz representative release resolution failed for group=%s",
            release_group_mbid,
        )
        representative_release = None
    return representative_release


def browse_releases(release_group_mbid, *, limit=100, offset=0):
    """Browse one page of releases belonging to a release group."""
    mbid = _path_value(release_group_mbid)
    return _cached(
        f"musicbrainz_{CACHE_VERSION}_releases_{mbid}_{limit}_{offset}",
        DETAIL_CACHE_TTL,
        lambda: _musicbrainz_request(
            "release",
            {
                "release-group": release_group_mbid,
                "inc": "artist-credits+labels+media+url-rels",
                "limit": limit,
                "offset": offset,
            },
        ),
    )


def lookup_release(release_mbid):
    """Look up a release with its media and recordings."""
    mbid = _path_value(release_mbid)
    return _cached(
        f"musicbrainz_{CACHE_VERSION}_release_{mbid}",
        DETAIL_CACHE_TTL,
        lambda: _musicbrainz_request(
            f"release/{mbid}",
            {
                "inc": (
                    "artist-credits+labels+media+recordings+isrcs+url-rels"
                ),
            },
        ),
    )


def resolve_representative_release(release_group_mbid, release_group_title):
    """Choose and load the stable representative release for a release group."""
    mbid = _path_value(release_group_mbid)
    selection = _cached(
        f"musicbrainz_representative_release_{RESOLVER_VERSION}_{mbid}",
        DETAIL_CACHE_TTL,
        lambda: _select_representative_release(
            release_group_mbid,
            release_group_title,
        ),
    )
    return {
        **selection,
        "release": (
            lookup_release(selection["release_mbid"])
            if selection["release_mbid"]
            else None
        ),
    }


def _select_representative_release(release_group_mbid, release_group_title):
    candidates = [
        release
        for release in _all_releases(release_group_mbid)
        if release.get("id") and _declared_complete(release)
    ]
    verified = {}

    streaming = sorted(
        (
            release
            for release in candidates
            if release.get("status") == "Official"
            and _is_digital(release)
            and _streaming_relations(release.get("relations"))
        ),
        key=lambda release: (
            _country_rank(release.get("country")),
            _title_rank(release.get("title"), release_group_title),
            _date_rank(release.get("date")),
            release["id"],
        ),
    )
    selected = _first_complete(streaming, verified)
    if selected:
        title_rank = _title_rank(selected.get("title"), release_group_title)
        if (selected.get("country") or "").upper() == settings.MUSIC_DEFAULT_MARKET.upper():
            reason = "streaming_market_match"
        elif title_rank == 0:
            reason = "streaming_standard_edition"
        else:
            reason = "official_digital"
        return {"release_mbid": selected["id"], "reason": reason}

    official = sorted(
        (release for release in candidates if release.get("status") == "Official"),
        key=lambda release: (_date_rank(release.get("date")), release["id"]),
    )
    selected = _first_complete(official, verified)
    if selected:
        return {"release_mbid": selected["id"], "reason": "earliest_official"}

    selected = _first_complete(
        sorted(
            candidates,
            key=lambda release: (_date_rank(release.get("date")), release["id"]),
        ),
        verified,
    )
    return {
        "release_mbid": selected["id"] if selected else None,
        "reason": "complete_tracklist_fallback" if selected else None,
    }


def _all_releases(release_group_mbid):
    releases = []
    offset = 0
    total = None
    while total is None or offset < total:
        page = browse_releases(release_group_mbid, limit=100, offset=offset)
        batch = page.get("releases") or []
        releases.extend(batch)
        total = page.get("release-count", page.get("count", len(releases)))
        if not batch:
            break
        offset += len(batch)
    return releases


def _first_complete(candidates, verified):
    for candidate in candidates:
        release_mbid = candidate["id"]
        if release_mbid not in verified:
            release = lookup_release(release_mbid)
            verified[release_mbid] = release if _complete_tracklist(release) else None
        if verified[release_mbid] is not None:
            return candidate
    return None


def _declared_complete(release):
    media = release.get("media") or []
    return bool(media) and all(_positive_int(medium.get("track-count")) for medium in media)


def _complete_tracklist(release):
    media = release.get("media") or []
    if not media:
        return False
    for medium in media:
        track_count = _positive_int(medium.get("track-count"))
        tracks = medium.get("tracks") or []
        if not track_count or len(tracks) != track_count:
            return False
        if any(
            not track.get("id") or not (track.get("recording") or {}).get("id")
            for track in tracks
        ):
            return False
    return True


def _is_digital(release):
    media = release.get("media") or []
    return bool(media) and all(medium.get("format") == "Digital Media" for medium in media)


def _streaming_relations(relations):
    links = []
    for relation in relations or []:
        if relation.get("type-id") not in _STREAMING_RELATION_IDS:
            continue
        resource = (relation.get("url") or {}).get("resource")
        try:
            parsed = urlsplit(resource)
        except (TypeError, ValueError):
            continue
        hostname = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or not hostname:
            continue
        links.append(
            {
                "service": hostname,
                "url": urlunsplit(parsed._replace(scheme="https")),
            },
        )
    return [
        {"service": service, "url": url}
        for service, url in sorted(
            {(item["service"], item["url"]) for item in links},
        )
    ]


def _country_rank(country):
    country = (country or "").upper()
    market = settings.MUSIC_DEFAULT_MARKET.upper()
    if country == market:
        return (0, "")
    if country == "XW":
        return (1, "")
    if not country:
        return (2, "")
    return (3, country)


def _date_rank(value):
    parts = str(value or "").split("-")
    try:
        precision = len(parts)
        parsed = date(
            int(parts[0]),
            int(parts[1]) if precision > 1 else 1,
            int(parts[2]) if precision > 2 else 1,
        )
    except (TypeError, ValueError):
        return (1, date.max, 0)
    return (0, parsed, -precision)


def _normalized_title(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(
        "".join(
            " " if unicodedata.category(char)[0] in {"P", "Z"} else char
            for char in value
        ).split(),
    )


def _without_edition_marker(value):
    normalized = _normalized_title(value)
    markers = sorted(
        (_normalized_title(marker) for marker in _EDITION_MARKERS),
        key=len,
        reverse=True,
    )
    for marker in markers:
        for suffix in (f"{marker} edition", marker):
            if normalized.endswith(f" {suffix}"):
                return normalized[: -(len(suffix) + 1)].strip(), True
    return normalized, False


def _title_rank(release_title, release_group_title):
    release_title = _normalized_title(release_title)
    group_title = _normalized_title(release_group_title)
    if release_title == group_title:
        return 0
    stripped, has_marker = _without_edition_marker(release_title)
    if has_marker and stripped == group_title:
        return 1
    return 3 if has_marker else 2


def _representative_release(release, reason):
    media = []
    for disc_index, medium in enumerate(release.get("media") or [], start=1):
        disc_number = _positive_int(medium.get("position")) or disc_index
        tracks = []
        for track_index, track in enumerate(medium.get("tracks") or [], start=1):
            recording = track.get("recording") or {}
            position = _positive_int(track.get("position")) or track_index
            tracks.append(
                {
                    "track_mbid": track.get("id"),
                    "disc_number": disc_number,
                    "position": position,
                    "number": str(track.get("number") or position),
                    "title": track.get("title") or recording.get("title") or "",
                    "length_ms": track.get("length"),
                    "artist_credit": _artist_credits(
                        track.get("artist-credit")
                        or recording.get("artist-credit")
                        or release.get("artist-credit"),
                    ),
                    "recording": {
                        "recording_mbid": recording.get("id"),
                        "title": recording.get("title") or track.get("title") or "",
                        "length_ms": recording.get("length"),
                        "disambiguation": recording.get("disambiguation") or None,
                        "first_release_date": recording.get("first-release-date") or None,
                        "is_video": bool(recording.get("video")),
                        "isrcs": sorted(set(recording.get("isrcs") or [])),
                    },
                },
            )
        media.append(
            {
                "medium_mbid": medium.get("id"),
                "position": disc_number,
                "title": medium.get("title") or None,
                "format": medium.get("format") or None,
                "track_count": len(tracks),
                "tracks": tracks,
            },
        )

    formats = {medium["format"] for medium in media if medium["format"]}
    labels = []
    for label_info in release.get("label-info") or []:
        label = label_info.get("label") or {}
        labels.append(
            {
                "label_mbid": label.get("id"),
                "name": label.get("name"),
                "catalog_number": label_info.get("catalog-number") or None,
            },
        )
    _, has_edition_marker = _without_edition_marker(release.get("title"))
    return {
        "release_mbid": release.get("id"),
        "title": release.get("title") or "",
        "status": release.get("status") or None,
        "date": release.get("date") or None,
        "country": release.get("country") or None,
        "barcode": release.get("barcode") or None,
        "selection_basis": reason,
        "labels": labels,
        "format": next(iter(formats)) if len(formats) == 1 else None,
        "is_deluxe_or_remastered": has_edition_marker,
        "streaming_links": _streaming_relations(release.get("relations")),
        "disc_count": len(media),
        "track_count": sum(medium["track_count"] for medium in media),
        "media": media,
    }


def _positive_int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _popularity_count(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def lookup_recording(recording_mbid):
    """Look up one MusicBrainz recording."""
    mbid = _path_value(recording_mbid)
    return _cached(
        f"musicbrainz_recording_{RECORDING_CACHE_VERSION}_{mbid}",
        DETAIL_CACHE_TTL,
        lambda: _musicbrainz_request(
            f"recording/{mbid}",
            {
                "inc": (
                    "artist-credits+isrcs+genres+ratings+annotation+url-rels+"
                    "work-rels+work-level-rels+artist-rels+recording-rels"
                ),
            },
        ),
    )


def browse_recording_releases(recording_mbid, *, limit=100, offset=0):
    """Browse one cached page of releases containing a recording."""
    mbid = _path_value(recording_mbid)
    return _cached(
        f"musicbrainz_{CACHE_VERSION}_recording_releases_{mbid}_{limit}_{offset}",
        DETAIL_CACHE_TTL,
        lambda: _musicbrainz_request(
            "release",
            {
                "recording": recording_mbid,
                "inc": "artist-credits+release-groups",
                "limit": limit,
                "offset": offset,
            },
        ),
    )


def recording(recording_mbid):
    """Return one recording and every associated release normalized for the API."""
    raw = lookup_recording(recording_mbid)
    source_url = f"https://musicbrainz.org/recording/{_path_value(recording_mbid)}"
    rating = raw.get("rating") or {}
    releases = _recording_releases(recording_mbid)
    albums = []
    seen_albums = set()
    normalized_releases = []

    for release in releases:
        release_group = release.get("release-group") or {}
        release_group_mbid = release_group.get("id")
        normalized_releases.append(
            {
                "release_mbid": release.get("id"),
                "title": release.get("title") or "",
                "status": release.get("status") or None,
                "date": release.get("date") or None,
                "country": release.get("country") or None,
                "barcode": release.get("barcode") or None,
                "release_group_mbid": release_group_mbid,
            },
        )
        if not release_group_mbid or release_group_mbid in seen_albums:
            continue
        seen_albums.add(release_group_mbid)
        credits = _artist_credits(
            release_group.get("artist-credit") or release.get("artist-credit"),
        )
        artist = _artist_credit_text(credits)
        first_release_date = release_group.get("first-release-date") or None
        albums.append(
            {
                "media_id": release_group_mbid,
                "source": Sources.MUSICBRAINZ.value,
                "media_type": MediaTypes.MUSIC.value,
                "title": release_group.get("title") or release.get("title") or "",
                "subtitle": artist,
                "image": _cover_art_url(release_group_mbid),
                "poster_width": 500,
                "poster_height": 500,
                "poster_aspect_ratio": 1.0,
                "release_date": first_release_date,
                "genres": _genre_names(release_group.get("genres")),
            },
        )

    return {
        "recording_mbid": raw.get("id") or str(recording_mbid),
        "title": raw.get("title") or "",
        "artist_credit": _artist_credits(raw.get("artist-credit")),
        "length_ms": raw.get("length"),
        "isrcs": sorted(set(raw.get("isrcs") or [])),
        "disambiguation": raw.get("disambiguation") or None,
        "first_release_date": raw.get("first-release-date") or None,
        "is_video": bool(raw.get("video")),
        "genres": _genre_names(raw.get("genres")),
        "rating": (
            {
                "value": rating.get("value"),
                "votes_count": rating.get("votes-count"),
                "max_value": 5,
            }
            if rating.get("value") is not None
            else None
        ),
        "annotation": _annotation(raw.get("annotation")),
        "works": _recording_works(raw.get("relations")),
        "alternative_recordings": _alternative_recordings(
            raw.get("relations"),
            raw.get("id") or str(recording_mbid),
        ),
        "source_url": source_url,
        "external_links": _external_links(raw.get("relations"), source_url),
        "albums": albums,
        "releases": normalized_releases,
    }


def _recording_releases(recording_mbid):
    releases = []
    offset = 0
    while True:
        response = browse_recording_releases(recording_mbid, offset=offset)
        page = response.get("releases") or []
        releases.extend(page)
        offset += len(page)
        total = response.get("release-count", response.get("count", len(releases)))
        if not page or offset >= total:
            break

    unique = {}
    for release in releases:
        if release.get("id"):
            unique.setdefault(release["id"], release)
    return list(unique.values())


def _recording_works(relations):
    works = []
    for relation in relations or []:
        work = relation.get("work") or {}
        if relation.get("target-type") != "work" or not work.get("id"):
            continue
        credits = {}
        for credit_relation in work.get("relations") or []:
            role = credit_relation.get("type")
            artist = credit_relation.get("artist") or {}
            if role not in _SONGWRITING_ROLES or not artist.get("id"):
                continue
            credit = credits.setdefault(
                artist["id"],
                {
                    "artist_mbid": artist["id"],
                    "name": artist.get("name") or "",
                    "roles": [],
                },
            )
            if role not in credit["roles"]:
                credit["roles"].append(role)
        works.append(
            {
                "work_mbid": work["id"],
                "title": work.get("title") or "",
                "relationship_type": relation.get("type") or None,
                "iswcs": sorted(set(work.get("iswcs") or [])),
                "language": work.get("language") or None,
                "credits": list(credits.values()),
            },
        )
    return works


def _alternative_recordings(relations, recording_mbid):
    alternatives = []
    seen = set()
    for relation in relations or []:
        related = relation.get("recording") or {}
        related_mbid = related.get("id")
        key = (related_mbid, relation.get("type"), relation.get("direction"))
        if (
            relation.get("target-type") != "recording"
            or not related_mbid
            or related_mbid == recording_mbid
            or key in seen
        ):
            continue
        seen.add(key)
        alternatives.append(
            {
                "recording_mbid": related_mbid,
                "relationship_type": relation.get("type") or None,
                "direction": relation.get("direction") or None,
                "title": related.get("title") or "",
                "artist_credit": _artist_credits(related.get("artist-credit")),
                "length_ms": related.get("length"),
                "disambiguation": related.get("disambiguation") or None,
            },
        )
    return alternatives


def lookup_cover_art(release_group_mbid):
    """Return release-group Cover Art Archive JSON, or None for a cached miss."""
    mbid = _path_value(release_group_mbid)
    cache_key = f"coverartarchive_{CACHE_VERSION}_release_group_{mbid}"

    def fetch():
        try:
            return services.api_request(
                COVER_ART_PROVIDER,
                "GET",
                f"{COVER_ART_URL}/release-group/{mbid}",
                headers=_headers(),
                request_session=services.session,
            )
        except requests.exceptions.JSONDecodeError as error:
            raise services.ProviderAPIError(
                COVER_ART_PROVIDER,
                error,
                "invalid JSON response",
            ) from error
        except requests.exceptions.HTTPError as error:
            if error.response is not None and error.response.status_code == requests.codes.not_found:
                return False
            raise services.ProviderAPIError(COVER_ART_PROVIDER, error) from error
        except requests.RequestException as error:
            raise services.ProviderAPIError(COVER_ART_PROVIDER, error) from error

    data = _cached(
        cache_key,
        DETAIL_CACHE_TTL,
        fetch,
        missing_timeout=MISSING_COVER_CACHE_TTL,
    )
    return None if data is False else data


def _cached(cache_key, timeout, fetch, *, missing_timeout=None):
    data = cache.get(cache_key, _CACHE_MISS)
    if data is not _CACHE_MISS:
        return data

    lock_factory = getattr(cache, "lock", None)
    if lock_factory is None:
        data = fetch()
        cache.set(cache_key, data, missing_timeout if data is False else timeout)
        return data

    lock = lock_factory(f"{cache_key}:flight", timeout=CACHE_LOCK_TIMEOUT + 2, sleep=0.05)
    if not lock.acquire(blocking=True, blocking_timeout=CACHE_LOCK_TIMEOUT):
        raise services.ProviderAPIError(
            Sources.MUSICBRAINZ.value,
            TimeoutError("cache single-flight deadline exceeded"),
        )
    try:
        data = cache.get(cache_key, _CACHE_MISS)
        if data is _CACHE_MISS:
            data = fetch()
            cache.set(cache_key, data, missing_timeout if data is False else timeout)
        return data
    finally:
        with suppress(LockError, RedisError, ConnectionError, OSError):
            lock.release()


def _release_group_query(query):
    escaped = escape_lucene(query)
    return f"(releasegroup:({escaped}) OR artist:({escaped}))"


def _search_result(group):
    release_group_mbid = str(group.get("id") or "")
    credits = _artist_credits(group.get("artist-credit"))
    artist = _artist_credit_text(credits)
    first_release_date = group.get("first-release-date") or None
    primary_type = group.get("primary-type") or group.get("type") or None
    search_score = group.get("score")
    subtitle = " · ".join(
        part
        for part in (
            artist,
            first_release_date[:4] if first_release_date else None,
            primary_type,
        )
        if part
    )
    return {
        "media_id": release_group_mbid,
        "source": Sources.MUSICBRAINZ.value,
        "media_type": MediaTypes.MUSIC.value,
        "title": group.get("title") or "",
        "artist_credits": credits,
        "release_date": first_release_date,
        "first_release_date": first_release_date,
        "primary_type": primary_type,
        "secondary_types": group.get("secondary-types") or [],
        "disambiguation": group.get("disambiguation") or None,
        "search_score": search_score,
        "provider_rank_boost": search_score,
        "image": _cover_art_url(release_group_mbid),
        "poster_width": 500,
        "poster_height": 500,
        "poster_aspect_ratio": 1.0,
        "subtitle": subtitle or None,
    }


def _artist_credits(raw_credits):
    credits = []
    for credit in raw_credits or []:
        artist = credit.get("artist") or {}
        name = credit.get("name") or artist.get("name")
        if not name:
            continue
        credits.append(
            {
                "artist_mbid": artist.get("id"),
                "name": name,
                "join_phrase": credit.get("joinphrase") or "",
            },
        )
    return credits


def _artist_credit_text(credits):
    text = "".join(
        f"{credit['name']}{credit['join_phrase']}" for credit in credits
    ).strip()
    return text or None


def _genre_names(genres):
    return [
        str(genre["name"])
        for genre in genres or []
        if isinstance(genre, dict) and genre.get("name")
    ]


def _artist_release_group_credit(group):
    release_group_mbid = str(group.get("id") or "")
    release_date = group.get("first-release-date") or None
    rating = group.get("rating") or {}
    return {
        "media_type": MediaTypes.MUSIC.value,
        "source": Sources.MUSICBRAINZ.value,
        "media_id": release_group_mbid,
        "title": group.get("title") or "",
        "image": _cover_art_url(release_group_mbid),
        "release_date": release_date,
        "year": release_date[:4] if release_date else None,
        "genres": _genre_names(group.get("genres")),
        "roles": ["Artist"],
        "credit_roles": [_release_group_category(group)],
        "vote_average": rating.get("value"),
        "vote_count": rating.get("votes-count"),
    }


def _release_group_category(group):
    secondary_types = {
        str(value).casefold() for value in group.get("secondary-types") or []
    }
    secondary_categories = (
        ("soundtrack", "Soundtracks"),
        ("compilation", "Compilations"),
        ("live", "Live releases"),
        ("remix", "Remix releases"),
        ("mixtape/street", "Mixtapes"),
        ("dj-mix", "DJ mixes"),
        ("demo", "Demos"),
        ("audiobook", "Audiobooks"),
        ("interview", "Interviews"),
    )
    for release_type, category in secondary_categories:
        if release_type in secondary_types:
            return category

    primary_type = str(group.get("primary-type") or group.get("type") or "").casefold()
    return {
        "album": "Albums",
        "ep": "EPs",
        "single": "Singles",
        "broadcast": "Broadcasts",
    }.get(primary_type, "Other")


def _artist_image(relations):
    for relation in relations or []:
        if relation.get("target-type") != "url" or relation.get("type") != "image":
            continue
        resource = (relation.get("url") or {}).get("resource")
        try:
            parsed = urlsplit(resource)
        except (TypeError, ValueError):
            continue
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return urlunsplit(parsed._replace(scheme="https"))
    return settings.IMG_NONE


def _annotation(value):
    if isinstance(value, dict):
        value = value.get("text")
    text = str(value or "").strip()
    return text or None


def _cover_art_url(release_group_mbid):
    return (
        f"{COVER_ART_URL}/release-group/{_path_value(release_group_mbid)}/front-500"
    )


def _external_links(relations, source_url):
    links = {"MusicBrainz": source_url}
    for relation in relations or []:
        if relation.get("target-type") != "url" or relation.get("type") != "other databases":
            continue
        resource = (relation.get("url") or {}).get("resource")
        try:
            parsed = urlsplit(resource)
        except (TypeError, ValueError):
            continue
        hostname = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or not hostname:
            continue
        links.setdefault(hostname, urlunsplit(parsed._replace(scheme="https")))
    return links


def _musicbrainz_request(
    path,
    params,
    *,
    timeout=None,
    max_attempts=MAX_ATTEMPTS,
    retry_rate_limits=True,
):
    params = {"fmt": "json", **params}
    request_options = {"timeout": timeout} if timeout is not None else {}
    if not retry_rate_limits:
        request_options["retry_rate_limits"] = False
    for attempt in range(max_attempts):
        try:
            return services.api_request(
                Sources.MUSICBRAINZ.value,
                "GET",
                f"{MUSICBRAINZ_URL}/{path}",
                params=params,
                headers=_headers(),
                request_session=services.musicbrainz_session,
                **request_options,
            )
        except requests.exceptions.JSONDecodeError as error:
            raise services.ProviderAPIError(
                Sources.MUSICBRAINZ.value,
                error,
                "invalid JSON response",
            ) from error
        except requests.exceptions.HTTPError as error:
            status_code = getattr(error.response, "status_code", None)
            if status_code == requests.codes.service_unavailable and attempt < max_attempts - 1:
                continue
            raise services.ProviderAPIError(Sources.MUSICBRAINZ.value, error) from error
        except requests.exceptions.Timeout as error:
            raise services.ProviderAPIError(Sources.MUSICBRAINZ.value, error) from error
        except requests.ConnectionError as error:
            if attempt < max_attempts - 1:
                continue
            raise services.ProviderAPIError(Sources.MUSICBRAINZ.value, error) from error
        except requests.RequestException as error:
            raise services.ProviderAPIError(Sources.MUSICBRAINZ.value, error) from error

    raise AssertionError("unreachable")


def _headers():
    return {
        "Accept": "application/json",
        "User-Agent": f"Spine/{settings.VERSION} ({settings.MUSICBRAINZ_CONTACT})",
    }


def _path_value(value):
    return quote(str(value), safe="")
