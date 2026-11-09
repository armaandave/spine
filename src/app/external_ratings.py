"""Provider-agnostic external rating fetch and persistence service."""

from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urljoin, urlsplit

import requests
from botocore.exceptions import (
    ClientError as BotoClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.models import ExternalRating, Item, MediaTypes, Sources

FRESH_FOR = timedelta(hours=24)
_UNSET = object()
LEGACY_RATING_FIELDS = {
    "imdb": "imdb_rating",
    "letterboxd": "letterboxd_rating",
    "tomatoes": "rotten_tomatoes_rating",
}
TRANSIENT_HTTP_STATUSES = {408, 425, 429}


class TransientExternalRatingError(Exception):
    """Signal that persisted rating failures are safe to retry."""

    def __init__(self, sources):
        self.sources = tuple(sorted(sources))
        super().__init__(f"Transient external rating failure: {', '.join(self.sources)}")

RATING_URL_BASES = {
    "comicvine": "https://comicvine.gamespot.com/",
    "goodreads": "https://www.goodreads.com/",
    "hardcover": "https://hardcover.app/",
    "igdb": "https://www.igdb.com/",
    "imdb": "https://www.imdb.com/",
    "anilist": "https://anilist.co/",
    "letterboxd": "https://letterboxd.com/",
    "mal": "https://myanimelist.net/",
    "mangaupdates": "https://www.mangaupdates.com/",
    "metacritic": "https://www.metacritic.com/",
    "musicbrainz": "https://musicbrainz.org/",
    "openlibrary": "https://openlibrary.org/",
    "steam": "https://store.steampowered.com/",
    "tmdb": "https://www.themoviedb.org/",
    "tomatoes": "https://www.rottentomatoes.com/",
}
RATING_URL_HOSTS = {
    "comicvine": {"comicvine.gamespot.com"},
    "goodreads": {"goodreads.com"},
    "hardcover": {"hardcover.app"},
    "igdb": {"igdb.com"},
    "imdb": {"imdb.com"},
    "anilist": {"anilist.co"},
    "letterboxd": {"boxd.it", "letterboxd.com"},
    "mal": {"myanimelist.net"},
    "mangaupdates": {"mangaupdates.com"},
    "metacritic": {"metacritic.com"},
    "musicbrainz": {"musicbrainz.org"},
    "openlibrary": {"openlibrary.org"},
    "steam": {"store.steampowered.com"},
    "tmdb": {"themoviedb.org"},
    "tomatoes": {"rottentomatoes.com"},
}


def _fetch_metadata(item, get_metadata):
    metadata = get_metadata()
    return {
        item.source: {
            "value": metadata.get("score"),
            "vote_count": metadata.get("score_count"),
            "url": provider_rating_url(
                metadata=metadata,
                source=item.source,
                media_type=item.media_type,
                media_id=item.media_id,
                season_number=item.season_number,
                episode_number=item.episode_number,
            ),
        },
    }


def _fetch_mdblist(item, _get_metadata):
    from app.providers import mdblist

    media_type = (
        MediaTypes.TV.value
        if item.media_type == MediaTypes.SEASON.value
        else item.media_type
    )
    return mdblist.get_media_ratings(item.media_id, media_type) or {}


def _fetch_imdb(item, get_metadata):
    from app.providers import imdb

    rating = imdb.get_title_rating(
        get_metadata().get("imdb_id"),
        raise_errors=True,
    )
    return {"imdb": rating}


def _fetch_metacritic(item, _get_metadata):
    from app.providers import steam

    return {
        "metacritic": steam.get_metacritic_rating(
            item.media_id,
            raise_errors=True,
        ),
    }


def _fetch_steam(item, _get_metadata):
    from app.providers import steam

    return {
        "steam": steam.get_review_rating(
            item.media_id,
            raise_errors=True,
        ),
    }


def _fetch_anilist(item, _get_metadata):
    from app.providers import anilist, mal

    if item.media_type == MediaTypes.ANIME.value:
        data = anilist.anime(item.media_id, raise_errors=True)
    else:
        mal_id = (
            item.media_id
            if item.source == Sources.MAL.value
            else mal.match_manga(item.media_id, _get_metadata())
        )
        data = anilist.manga(mal_id, raise_errors=True) if mal_id else {}
    return {"anilist": data.get("rating")}


def _fetch_mal(item, get_metadata):
    if item.source == Sources.MAL.value:
        return _fetch_metadata(item, get_metadata)

    from app.providers import mal

    mal_id = mal.match_manga(item.media_id, get_metadata())
    metadata = mal.manga(mal_id) if mal_id else {}
    return {
        "mal": {
            "value": metadata.get("score"),
            "vote_count": metadata.get("score_count"),
            "url": metadata.get("source_url"),
        },
    }


def _fetch_goodreads(item, get_metadata):
    """The book's Goodreads page, found through Hardcover (Open Library books by ISBN).

    Only the link is stored: Goodreads turns away the server, so the app reads the
    rating from this page on the phone.
    """
    from app.providers import hardcover

    if item.source == Sources.HARDCOVER.value:
        goodreads_id = hardcover.goodreads_id(item.media_id)
    else:
        isbns = (get_metadata().get("details") or {}).get("isbn") or []
        goodreads_id = next(
            (found for isbn in isbns[:2] if (found := hardcover.goodreads_id_for_isbn(isbn))),
            None,
        )
    url = f"https://www.goodreads.com/book/show/{goodreads_id}" if goodreads_id else None
    return {"goodreads": {"url": url}}


def _matched_manga_rating_enabled(item):
    if item.source == Sources.MAL.value:
        return True
    if (
        item.source != Sources.MANGAUPDATES.value
        or item.media_type != MediaTypes.MANGA.value
    ):
        return False
    from app.providers import mal

    return bool(mal.cached_manga_match(item.media_id))


def _imdb_enabled(item):
    if item.media_type != MediaTypes.EPISODE.value:
        return True
    from app.providers import imdb

    return imdb.is_configured()


def _musicbrainz_ratings_enabled(_item=None):
    return settings.MUSICBRAINZ_EXTERNAL_RATINGS_ENABLED


_TMDB_TYPES = frozenset(
    {
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
        MediaTypes.EPISODE.value,
    },
)
_MDBLIST_TYPES = frozenset(
    {
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
    },
)

RATING_SOURCES = {
    "tmdb": {
        "key": "tmdb",
        "label": "TMDB",
        "item_sources": frozenset({Sources.TMDB.value}),
        "media_types": _TMDB_TYPES,
        "max_value": Decimal("10"),
        "wire_max": "10",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_metadata,
    },
    "imdb": {
        "key": "imdb",
        "label": "IMDb",
        "item_sources": frozenset({Sources.TMDB.value}),
        "media_types": _TMDB_TYPES,
        "max_value": Decimal("10"),
        "wire_max": "10",
        "fresh_for": FRESH_FOR,
        "fetch": {
            MediaTypes.MOVIE.value: _fetch_mdblist,
            MediaTypes.TV.value: _fetch_mdblist,
            MediaTypes.SEASON.value: _fetch_mdblist,
            MediaTypes.EPISODE.value: _fetch_imdb,
        },
        "enabled": _imdb_enabled,
    },
    "letterboxd": {
        "key": "letterboxd",
        "label": "Letterboxd",
        "item_sources": frozenset({Sources.TMDB.value}),
        "media_types": _MDBLIST_TYPES,
        "max_value": Decimal("5"),
        "wire_max": "5",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_mdblist,
    },
    "tomatoes": {
        "key": "tomatoes",
        "label": "Rotten Tomatoes",
        "item_sources": frozenset({Sources.TMDB.value}),
        "media_types": _MDBLIST_TYPES,
        "max_value": Decimal("100"),
        "wire_max": "100%",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_mdblist,
    },
    "mal": {
        "key": "mal",
        "label": "MAL",
        "item_sources": frozenset(
            {Sources.MAL.value, Sources.MANGAUPDATES.value},
        ),
        "media_types": frozenset(
            {MediaTypes.ANIME.value, MediaTypes.MANGA.value},
        ),
        "max_value": Decimal("10"),
        "wire_max": "10",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_mal,
        "enabled": _matched_manga_rating_enabled,
    },
    "anilist": {
        "key": "anilist",
        "label": "AniList",
        "item_sources": frozenset(
            {Sources.MAL.value, Sources.MANGAUPDATES.value},
        ),
        "media_types": frozenset(
            {MediaTypes.ANIME.value, MediaTypes.MANGA.value},
        ),
        "max_value": Decimal("100"),
        "wire_max": "100%",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_anilist,
        "enabled": _matched_manga_rating_enabled,
    },
    "mangaupdates": {
        "key": "mangaupdates",
        "label": "MangaUpdates",
        "item_sources": frozenset({Sources.MANGAUPDATES.value}),
        "media_types": frozenset({MediaTypes.MANGA.value}),
        "max_value": Decimal("10"),
        "wire_max": "10",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_metadata,
    },
    "igdb": {
        "key": "igdb",
        "label": "IGDB",
        "item_sources": frozenset({Sources.IGDB.value}),
        "media_types": frozenset({MediaTypes.GAME.value}),
        "max_value": Decimal("100"),
        "wire_max": "100",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_metadata,
    },
    "metacritic": {
        "key": "metacritic",
        "label": "Metacritic",
        "item_sources": frozenset({Sources.IGDB.value}),
        "media_types": frozenset({MediaTypes.GAME.value}),
        "max_value": Decimal("100"),
        "wire_max": "100",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_metacritic,
    },
    "steam": {
        "key": "steam",
        "label": "Steam",
        "item_sources": frozenset({Sources.IGDB.value}),
        "media_types": frozenset({MediaTypes.GAME.value}),
        "max_value": Decimal("100"),
        "wire_max": "100%",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_steam,
    },
    "openlibrary": {
        "key": "openlibrary",
        "label": "OpenLibrary",
        "item_sources": frozenset({Sources.OPENLIBRARY.value}),
        "media_types": frozenset({MediaTypes.BOOK.value}),
        "max_value": Decimal("5"),
        "wire_max": "5",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_metadata,
    },
    "hardcover": {
        "key": "hardcover",
        "label": "Hardcover",
        "item_sources": frozenset({Sources.HARDCOVER.value}),
        "media_types": frozenset({MediaTypes.BOOK.value}),
        "max_value": Decimal("5"),
        "wire_max": "5",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_metadata,
    },
    "goodreads": {
        "key": "goodreads",
        "label": "Goodreads",
        "item_sources": frozenset({Sources.HARDCOVER.value, Sources.OPENLIBRARY.value}),
        "media_types": frozenset({MediaTypes.BOOK.value}),
        "max_value": Decimal("5"),
        "wire_max": "5",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_goodreads,
        # Link only (no value to sort or show); the detail page sends it on its own.
        "exposed": lambda: False,
    },
    "musicbrainz": {
        "key": "musicbrainz",
        "label": "MusicBrainz",
        "item_sources": frozenset({Sources.MUSICBRAINZ.value}),
        "media_types": frozenset({MediaTypes.MUSIC.value}),
        "max_value": Decimal("5"),
        "wire_max": "5",
        "fresh_for": FRESH_FOR,
        "fetch": _fetch_metadata,
        "enabled": _musicbrainz_ratings_enabled,
        "exposed": _musicbrainz_ratings_enabled,
    },
}


def rating_source_is_exposed(source):
    """Return whether a registered source may be exposed to API clients."""
    definition = RATING_SOURCES.get(source)
    return definition is None or definition.get("exposed", lambda: True)()


def source_label(source):
    """Return the registered user-facing source name."""
    definition = RATING_SOURCES.get(source)
    return definition["label"] if definition else str(source).title()


def max_rating_value(source):
    """Return the existing API display maximum for a rating source."""
    definition = RATING_SOURCES.get(source)
    return definition["wire_max"] if definition else "10"


def _is_allowed_rating_host(host, allowed_hosts):
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


def _is_valid_rating_path(source, path):
    if source != "imdb":
        return True
    segments = [segment for segment in path.split("/") if segment]
    return (
        len(segments) == 2
        and segments[0] == "title"
        and segments[1].startswith("tt")
        and segments[1][2:].isdigit()
    )


def _safe_urlsplit(value):
    try:
        return urlsplit(value)
    except ValueError:
        return None


def _absolute_rating_url(source, candidate, allowed_hosts):
    parsed = _safe_urlsplit(candidate)
    if parsed is None:
        return None
    if parsed.scheme:
        return candidate
    if candidate.startswith("//"):
        return f"https:{candidate}"
    first_segment = candidate.lstrip("/").split("/", 1)[0].lower()
    if _is_allowed_rating_host(first_segment, allowed_hosts):
        return f"https://{candidate.lstrip('/')}"
    return urljoin(RATING_URL_BASES[source], candidate)


def normalize_rating_url(source, value):
    """Return a trusted absolute HTTPS URL for a known rating provider."""
    source = str(source).lower()
    allowed_hosts = RATING_URL_HOSTS.get(source)
    candidate = str(value or "").strip()
    if not allowed_hosts or not candidate:
        return None

    candidate = _absolute_rating_url(source, candidate, allowed_hosts)
    parsed = _safe_urlsplit(candidate) if candidate else None
    if parsed is None:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not host
        or parsed.username
        or parsed.password
        or not _is_allowed_rating_host(host, allowed_hosts)
        or not _is_valid_rating_path(source, parsed.path)
    ):
        return None
    if parsed.scheme.lower() == "http":
        parsed = parsed._replace(scheme="https")
    return parsed.geturl()


def _provider_rating_fallback(
    *,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    encoded_id = quote(str(media_id), safe="")
    if source == Sources.TMDB.value and media_type == MediaTypes.SEASON.value:
        return (
            f"https://www.themoviedb.org/tv/{encoded_id}/season/{season_number}"
            if season_number is not None
            else None
        )
    if source == Sources.TMDB.value and media_type == MediaTypes.EPISODE.value:
        if season_number is None or episode_number is None:
            return None
        return (
            f"https://www.themoviedb.org/tv/{encoded_id}/season/"
            f"{season_number}/episode/{episode_number}"
        )
    return {
        (Sources.TMDB.value, MediaTypes.MOVIE.value): (
            f"https://www.themoviedb.org/movie/{encoded_id}"
        ),
        (Sources.TMDB.value, MediaTypes.TV.value): (
            f"https://www.themoviedb.org/tv/{encoded_id}"
        ),
        (Sources.MAL.value, MediaTypes.ANIME.value): (
            f"https://myanimelist.net/anime/{encoded_id}"
        ),
        (Sources.MAL.value, MediaTypes.MANGA.value): (
            f"https://myanimelist.net/manga/{encoded_id}"
        ),
        (Sources.MUSICBRAINZ.value, MediaTypes.MUSIC.value): (
            f"https://musicbrainz.org/release-group/{encoded_id}"
        ),
        (Sources.OPENLIBRARY.value, MediaTypes.BOOK.value): (
            f"https://openlibrary.org/books/{encoded_id}"
        ),
        (Sources.HARDCOVER.value, MediaTypes.BOOK.value): (
            f"https://hardcover.app/book/{encoded_id}"
        ),
    }.get((source, media_type))


def provider_rating_url(
    *,
    metadata,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the trusted provider page for its native rating."""
    if url := normalize_rating_url(source, metadata.get("source_url")):
        return url
    return normalize_rating_url(
        source,
        _provider_rating_fallback(
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
        ),
    )


def third_party_rating_url(*, metadata, rating_source, rating, media_type, media_id):
    """Return a trusted third-party rating URL or verified fallback."""
    if url := normalize_rating_url(rating_source, rating.get("url")):
        return url
    external_links = metadata.get("external_links") or {}
    fallback = None
    if rating_source == "imdb":
        fallback = external_links.get("IMDb") or external_links.get("imdb")
    elif rating_source == "letterboxd" and media_type == MediaTypes.MOVIE.value:
        fallback = external_links.get("Letterboxd") or external_links.get("letterboxd")
        if not fallback:
            fallback = f"https://letterboxd.com/tmdb/{quote(str(media_id), safe='')}"
    return normalize_rating_url(rating_source, fallback)


def _fetcher_for(definition, item):
    fetcher = definition["fetch"]
    return fetcher.get(item.media_type) if isinstance(fetcher, dict) else fetcher


def _eligible(definition, item):
    return (
        item.source in definition["item_sources"]
        and item.media_type in definition["media_types"]
    )


def eligible_rating_sources(item, rating_sources=None):
    """Return requested registry sources supported by an Item identity."""
    explicit_sources = rating_sources is not None
    if isinstance(rating_sources, str):
        rating_sources = [rating_sources]
    requested = (
        list(dict.fromkeys(rating_sources))
        if explicit_sources
        else list(RATING_SOURCES)
    )
    unknown = [source for source in requested if source not in RATING_SOURCES]
    if unknown:
        msg = f"Unknown rating source: {unknown[0]}"
        raise ValueError(msg)
    return [
        source
        for source in requested
        if _eligible(RATING_SOURCES[source], item)
        and RATING_SOURCES[source].get("enabled", lambda _item: True)(item)
    ]


def rating_sources_needing_refresh(
    item,
    rating_sources=None,
    *,
    force=False,
    now=None,
):
    """Return eligible sources without a fresh terminal outcome."""
    sources = eligible_rating_sources(item, rating_sources)
    if force:
        return sources
    rows = {
        rating.rating_source: rating
        for rating in item.external_ratings.all()
        if rating.rating_source in sources
    }
    now = now or timezone.now()
    return [
        source
        for source in sources
        if (rating := rows.get(source)) is None
        or rating.status
        not in {ExternalRating.Status.AVAILABLE, ExternalRating.Status.UNAVAILABLE}
        or rating.last_attempted_at < now - RATING_SOURCES[source]["fresh_for"]
    ]


def _http_status(error):
    status = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status is None and isinstance(response, dict):
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    if status is None:
        status = getattr(response, "status_code", None)
    return status


def _is_transient_error(error):
    from app.providers.services import ProviderAPIError

    if isinstance(
        error,
        (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            TimeoutError,
            ConnectionError,
            EndpointConnectionError,
            ConnectionClosedError,
            ReadTimeoutError,
        ),
    ):
        return True
    if isinstance(error, RuntimeError) and str(error).startswith("Cached "):
        return True
    if isinstance(error, BotoClientError):
        code = str(((error.response.get("Error") or {}).get("Code") or ""))
        if "throttl" in code.lower() or code in {
            "RequestLimitExceeded",
            "TooManyRequestsException",
        }:
            return True
    if isinstance(error, (ProviderAPIError, requests.exceptions.HTTPError, BotoClientError)):
        status = _http_status(error)
        try:
            status = int(status) if status is not None else None
        except (TypeError, ValueError):
            return False
        return status is None or status in TRANSIENT_HTTP_STATUSES or status >= 500
    return False


def _normalize_outcome(source, payload, item, metadata):
    definition = RATING_SOURCES[source]
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return {"status": ExternalRating.Status.FAILED, "error": "Invalid rating payload"}

    canonical_url = third_party_rating_url(
        metadata=metadata,
        rating_source=source,
        rating=payload,
        media_type=item.media_type,
        media_id=item.media_id,
    )
    raw_value = payload.get("value")
    if raw_value is None:
        raw_value = payload.get("score")
    if raw_value in (None, ""):
        return {
            "status": ExternalRating.Status.UNAVAILABLE,
            "canonical_url": canonical_url,
        }

    try:
        value = Decimal(str(raw_value).strip().replace("%", ""))
        if not value.is_finite() or value < 0 or value > definition["max_value"]:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        return {
            "status": ExternalRating.Status.FAILED,
            "error": f"Invalid {source} rating value",
        }

    vote_count = payload.get("vote_count", payload.get("votes"))
    if vote_count is not None:
        try:
            vote_count = int(vote_count)
            if not 0 <= vote_count <= 9223372036854775807:
                vote_count = None
        except (TypeError, ValueError):
            vote_count = None
    return {
        "status": ExternalRating.Status.AVAILABLE,
        "value": value,
        "vote_count": vote_count,
        "canonical_url": canonical_url,
    }


def _persist_outcomes(item, outcomes, attempted_at):
    with transaction.atomic():
        for source, outcome in outcomes.items():
            definition = RATING_SOURCES[source]
            status = outcome["status"]
            defaults = {
                "status": status,
                "max_value": definition["max_value"],
                "last_attempted_at": attempted_at,
                "last_error": outcome.get("error", "")[:1000],
            }
            if status == ExternalRating.Status.AVAILABLE:
                defaults.update(
                    value=outcome["value"],
                    vote_count=outcome["vote_count"],
                    canonical_url=outcome["canonical_url"],
                    last_success_at=attempted_at,
                )
            elif status == ExternalRating.Status.UNAVAILABLE:
                defaults["canonical_url"] = outcome["canonical_url"]

            rating, created = ExternalRating.objects.select_for_update().get_or_create(
                item=item,
                rating_source=source,
                defaults=defaults,
            )
            if created:
                continue
            if status == ExternalRating.Status.FAILED:
                rating.status = status
                rating.last_attempted_at = attempted_at
                rating.last_error = defaults["last_error"]
                rating.save(
                    update_fields=["status", "last_attempted_at", "last_error"],
                )
                continue

            rating.status = status
            rating.value = outcome.get("value")
            rating.max_value = definition["max_value"]
            rating.vote_count = outcome.get("vote_count")
            rating.last_attempted_at = attempted_at
            rating.last_error = ""
            update_fields = [
                "status",
                "value",
                "max_value",
                "vote_count",
                "last_attempted_at",
                "last_error",
            ]
            if outcome.get("canonical_url") is not None:
                rating.canonical_url = outcome["canonical_url"]
                update_fields.append("canonical_url")
            if status == ExternalRating.Status.AVAILABLE:
                rating.last_success_at = attempted_at
                update_fields.append("last_success_at")
            rating.save(update_fields=update_fields)

        _sync_legacy_rating_fields(item, outcomes)


def _sync_legacy_rating_fields(item, outcomes):
    updates = {}
    for source, field in LEGACY_RATING_FIELDS.items():
        outcome = outcomes.get(source)
        if not outcome or outcome["status"] == ExternalRating.Status.FAILED:
            continue
        updates[field] = outcome.get("value")
    if not updates:
        return
    Item.objects.filter(pk=item.pk).update(**updates)
    for field, value in updates.items():
        setattr(item, field, value)


def refresh_external_ratings(
    item,
    rating_sources=None,
    *,
    metadata=None,
    raise_transient=False,
):
    """Fetch and atomically persist eligible external ratings for one Item."""
    explicit_sources = rating_sources is not None
    requested = eligible_rating_sources(item, rating_sources)

    results = {}
    selected_fetchers = []
    for source in requested:
        definition = RATING_SOURCES[source]
        fetcher = _fetcher_for(definition, item)
        if fetcher is not None and fetcher not in selected_fetchers:
            selected_fetchers.append(fetcher)
    if explicit_sources:
        requested_input = [rating_sources] if isinstance(rating_sources, str) else rating_sources
        for source in requested_input:
            if source not in requested:
                results[source] = "skipped"

    metadata = _UNSET if metadata is None else metadata
    metadata_error = None

    def get_metadata():
        nonlocal metadata, metadata_error
        if metadata_error is not None:
            raise metadata_error
        if metadata is _UNSET:
            from app.providers import services

            season_numbers = (
                [item.season_number]
                if item.media_type in {MediaTypes.SEASON.value, MediaTypes.EPISODE.value}
                else None
            )
            try:
                metadata = services.get_media_metadata(
                    item.media_type,
                    item.media_id,
                    item.source,
                    season_numbers=season_numbers,
                    episode_number=item.episode_number,
                )
                if not isinstance(metadata, dict):
                    msg = "Provider returned invalid metadata"
                    raise ValueError(msg)
            except Exception as error:
                metadata_error = error
                raise
        return metadata

    attempted_at = timezone.now()
    outcomes = {}
    transient_sources = set()
    for fetcher in selected_fetchers:
        operation_sources = [
            source
            for source, definition in RATING_SOURCES.items()
            if _eligible(definition, item)
            and definition.get("enabled", lambda _item: True)(item)
            and _fetcher_for(definition, item) is fetcher
        ]
        try:
            payloads = fetcher(item, get_metadata)
            if not isinstance(payloads, dict):
                msg = "Rating provider returned invalid results"
                raise ValueError(msg)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"[:1000]
            if _is_transient_error(error):
                transient_sources.update(operation_sources)
            for source in operation_sources:
                outcomes[source] = {
                    "status": ExternalRating.Status.FAILED,
                    "error": message,
                }
                results[source] = ExternalRating.Status.FAILED
            continue

        loaded_metadata = metadata if isinstance(metadata, dict) else {}
        for source in operation_sources:
            outcome = _normalize_outcome(
                source,
                payloads.get(source),
                item,
                loaded_metadata,
            )
            outcomes[source] = outcome
            results[source] = outcome["status"]

    if outcomes:
        _persist_outcomes(item, outcomes, attempted_at)
    if raise_transient and transient_sources:
        error = TransientExternalRatingError(transient_sources)
        error.outcomes = results
        raise error
    return results
