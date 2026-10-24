import contextvars
import hashlib
import logging
import time
from urllib.parse import urlparse

import requests
from defusedxml import ElementTree
from django.conf import settings
from django.core.cache import cache
from pyrate_limiter import RedisBucket
from redis import Redis
from requests.adapters import HTTPAdapter
from requests_ratelimiter import LimiterAdapter, LimiterSession

from app import request_timing
from app.models import MediaTypes, Sources
from app.providers import (
    comicvine,
    hardcover,
    igdb,
    mal,
    mangaupdates,
    manual,
    musicbrainz,
    openlibrary,
    quota,
    tmdb,
)

logger = logging.getLogger(__name__)
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_MAX_WAIT_SECONDS = 60
# Seconds added to a provider's Retry-After before retrying a 429.
RATE_LIMIT_RETRY_PADDING_SECONDS = 3
# Providers whose Retry-After is exact, so a 429 waits only as long as they ask.
EXACT_RETRY_AFTER_PROVIDERS = {Sources.HARDCOVER.value}
SUPPORTED_PERSON_SOURCES = (
    Sources.TMDB.value,
    Sources.HARDCOVER.value,
    Sources.OPENLIBRARY.value,
    Sources.MUSICBRAINZ.value,
    Sources.MAL.value,
    Sources.MANGAUPDATES.value,
    "anilist",
)
# Marks search results from a fallback provider while the usual one is rate limited.
RATE_LIMIT_FALLBACK = "rate_limit_fallback"
PERSON_SEARCH_CACHE_VERSION = "v1"
PERSON_SEARCH_TTL = 60 * 60


def get_redis_client():
    """Return a Redis client."""
    if settings.TESTING:
        import fakeredis  # noqa: PLC0415

        return fakeredis.FakeRedis()
    return Redis.from_url(settings.REDIS_URL)


redis_db = get_redis_client()
bucket_key = f"{settings.REDIS_PREFIX}_api" if settings.REDIS_PREFIX else "api"
musicbrainz_bucket_key = (
    f"{settings.REDIS_PREFIX}_musicbrainz_api"
    if settings.REDIS_PREFIX
    else "musicbrainz_api"
)

session = LimiterSession(
    per_second=5,
    bucket_class=RedisBucket,
    bucket_kwargs={"redis": redis_db, "bucket_key": bucket_key},
)

# Set while a person's search runs: returns True once they've searched for something
# else, so a search still waiting for MusicBrainz gives up its turn.
search_superseded = contextvars.ContextVar("search_superseded", default=None)


class SearchSuperseded(Exception):
    """A newer search from the same person replaced this one while it waited."""


class MusicBrainzSession(LimiterSession):
    """MusicBrainz's one-request-a-second session, where a waiting search can drop out.

    Search runs as you type, so searches someone has typed past would otherwise hold
    up the one they're waiting for. The limiter keeps one thread at a time waiting
    inside it, so a search waits in short turns and checks in between.
    """

    def send(self, request, **kwargs):
        superseded = search_superseded.get()
        if superseded is None:
            return super().send(request, **kwargs)
        bucket_name = self._bucket_name(request)
        while True:
            if superseded():
                raise SearchSuperseded
            if self.limiter.try_acquire(bucket_name, weight=1, timeout=0.1):
                break
        # The turn is taken, so skip LimiterSession's own wait.
        response = requests.Session.send(self, request, **kwargs)
        if response.status_code in self.limit_statuses:
            self._fill_bucket(request)
        return response


musicbrainz_session = MusicBrainzSession(
    per_second=1,
    bucket_class=RedisBucket,
    bucket_kwargs={"redis": redis_db, "bucket_key": musicbrainz_bucket_key},
    limit_statuses=(requests.codes.service_unavailable,),
)

person_search_session = LimiterSession(
    per_second=5,
    bucket_class=RedisBucket,
    bucket_kwargs={"redis": redis_db, "bucket_key": bucket_key},
)

# The weekly music genre refresh makes thousands of ListenBrainz and Wikipedia
# requests, so it gets its own limit instead of spending the shared one.
genre_refresh_session = LimiterSession(per_second=20)
genre_refresh_session.mount(
    # ListenBrainz allows 30 requests per 10 seconds.
    "https://api.listenbrainz.org",
    LimiterAdapter(per_second=2),
)

session.mount("http://", HTTPAdapter(max_retries=3))
session.mount("https://", HTTPAdapter(max_retries=3))

session.mount(
    "https://api.myanimelist.net/v2",
    LimiterAdapter(per_minute=30),
)
session.mount(
    "https://graphql.anilist.co",
    LimiterAdapter(per_minute=85),
)
session.mount(
    "https://api.jikan.moe",
    LimiterAdapter(per_second=3),
)
session.mount(
    "https://api.igdb.com/v4",
    LimiterAdapter(per_second=3),
)
session.mount(
    "https://api.tvmaze.com",
    LimiterAdapter(per_second=2),
)
session.mount(
    "https://comicvine.gamespot.com/api",
    LimiterAdapter(per_hour=190),
)
session.mount(
    "https://openlibrary.org",
    LimiterAdapter(per_minute=20),
)
session.mount(
    "https://api.hardcover.app/v1/graphql",
    # No limit_statuses: on a 429 the limiter would otherwise mark the whole minute's
    # budget as spent (a ~60 s stall); api_request waits out Retry-After instead.
    LimiterAdapter(per_minute=50, limit_statuses=()),
)
session.mount(
    "https://www.steamgriddb.com/api/v2",
    LimiterAdapter(per_second=3),
)
session.mount(
    "https://store.steampowered.com/",
    LimiterAdapter(per_second=3),
)

person_search_session.mount(
    "https://graphql.anilist.co",
    LimiterAdapter(per_minute=85),
)
person_search_session.mount(
    "https://api.jikan.moe",
    LimiterAdapter(per_second=3),
)
person_search_session.mount(
    "https://openlibrary.org",
    LimiterAdapter(per_minute=20),
)
person_search_session.mount(
    "https://api.hardcover.app/v1/graphql",
    # No limit_statuses: on a 429 the limiter would otherwise mark the whole minute's
    # budget as spent (a ~60 s stall); api_request waits out Retry-After instead.
    LimiterAdapter(per_minute=50, limit_statuses=()),
)


class ProviderAPIError(Exception):
    """Exception raised when a provider API fails to respond."""

    def __init__(self, provider, error, details=None):
        """Initialize the exception with the provider name."""
        self.provider = provider
        response = getattr(error, "response", None)
        self.status_code = getattr(response, "status_code", None)
        try:
            provider_label = Sources(provider).label
        except ValueError:
            provider_label = provider.title()

        error_text = getattr(response, "text", str(error))
        logger.error("%s error: %s", provider_label, error_text)

        message = f"There was an error contacting the {provider_label} API"
        if self.status_code is None:
            message += " (network error)"
        else:
            message += f" (HTTP {self.status_code})"
        if details:
            message += f": {details}"
        message += ". Check the logs for more details."
        super().__init__(message)


def raise_not_found_error(provider, media_id, media_type="item"):
    """
    Raise a 404 ProviderAPIError for when a media item is not found.

    Args:
        provider: The provider source value (e.g., Sources.COMICVINE.value)
        media_id: The media ID that was not found
        media_type: The type of media (e.g., "comic", "game", "book")
    """
    error_msg = f"{media_type.capitalize()} with ID {media_id} not found"
    logger.error("%s: %s", provider, error_msg)

    # Create a mock 404 error response
    mock_response = type(
        "obj",
        (object,),
        {
            "status_code": 404,
            "text": error_msg,
        },
    )()
    mock_error = requests.exceptions.HTTPError(response=mock_response)

    raise ProviderAPIError(provider, mock_error, error_msg)


def api_request(
    provider,
    method,
    url,
    params=None,
    data=None,
    headers=None,
    response_format="json",
    request_session=None,
    timeout=None,
    retry_rate_limits=True,
):
    """Make a request to the API and return the response.

    Args:
        provider: Provider identifier for error messages
        method: HTTP method ("GET" or "POST")
        url: Request URL
        params: Query params for GET, JSON body for POST
        data: Raw data for POST
        headers: Request headers
        response_format: "json" (default), "xml" for XML parsing, or "text" for the body
        request_session: Optional requests session; defaults to the shared provider session
        timeout: Optional request timeout; defaults to the global provider timeout
        retry_rate_limits: Whether HTTP 429 responses may wait and retry

    Returns:
        Parsed JSON dict or ElementTree for XML
    """
    request_kwargs = {
        "url": url,
        "headers": headers,
        "timeout": timeout or settings.REQUEST_TIMEOUT,
    }

    active_session = request_session or session

    if method == "GET":
        request_kwargs["params"] = params
        request_func = active_session.get
    elif method == "POST":
        request_kwargs["data"] = data
        request_kwargs["json"] = params
        request_func = active_session.post

    rate_limit_retries = RATE_LIMIT_MAX_RETRIES if retry_rate_limits else 0
    for retry_number in range(rate_limit_retries + 1):
        # Featured-list syncs: spend the provider's daily budget, or raise Deferred.
        quota.before_request(provider, url)
        try:
            response = request_func(**request_kwargs)
            response.raise_for_status()
        except requests.exceptions.HTTPError as error:
            error_resp = error.response
            if error_resp.status_code == requests.codes.too_many_requests:
                # A wait longer than we would sleep (a daily quota) blocks the provider
                # for featured-list syncs, which then defer instead of sleeping.
                quota.rate_limited(
                    provider,
                    url,
                    error_resp,
                    max_sleep=RATE_LIMIT_MAX_WAIT_SECONDS,
                )
            if (
                error_resp.status_code != requests.codes.too_many_requests
                or retry_number == rate_limit_retries
            ):
                raise error from None

            try:
                seconds_to_wait = int(error_resp.headers.get("Retry-After", 5))
            except (TypeError, ValueError):
                seconds_to_wait = 5
            # A provider asking for more than every retry could wait (Hardcover's
            # daily cap asks for hours) won't recover in time, so fail now instead
            # of holding the request through every retry.
            if seconds_to_wait > RATE_LIMIT_MAX_WAIT_SECONDS * RATE_LIMIT_MAX_RETRIES:
                raise error from None
            seconds_to_wait = min(
                max(seconds_to_wait, 0),
                RATE_LIMIT_MAX_WAIT_SECONDS,
            )
            if provider not in EXACT_RETRY_AFTER_PROVIDERS:
                seconds_to_wait += RATE_LIMIT_RETRY_PADDING_SECONDS
            logger.warning(
                "Rate limited, waiting %s seconds before retry %s/%s",
                seconds_to_wait,
                retry_number + 1,
                RATE_LIMIT_MAX_RETRIES,
            )
            with request_timing.span("retry_sleep", urlparse(url).hostname or provider):
                time.sleep(seconds_to_wait)
            continue

        if response_format == "xml":
            return ElementTree.fromstring(response.text)
        if response_format == "text":
            return response.text
        return response.json()

    raise RuntimeError("Unreachable rate-limit retry state")


def get_media_metadata(
    media_type,
    media_id,
    source,
    season_numbers=None,
    episode_number=None,
    *,
    include_music_enrichment=True,
):
    """Return the metadata for the selected media."""
    if source == Sources.MANUAL.value:
        if media_type == MediaTypes.SEASON.value:
            return manual.season(media_id, season_numbers[0])
        if media_type == MediaTypes.EPISODE.value:
            return manual.episode(media_id, season_numbers[0], episode_number)
        if media_type == "tv_with_seasons":
            media_type = MediaTypes.TV.value
        return manual.metadata(media_id, media_type)

    # Defensive handling: if a season is requested without season_numbers, fall back to TV
    if media_type == MediaTypes.SEASON.value and not season_numbers:
        return tmdb.tv(media_id)

    metadata_retrievers = {
        MediaTypes.ANIME.value: lambda: mal.anime(media_id),
        MediaTypes.MANGA.value: lambda: (
            mangaupdates.manga(media_id)
            if source == Sources.MANGAUPDATES.value
            else mal.manga(media_id)
        ),
        MediaTypes.TV.value: lambda: tmdb.tv(media_id),
        "tv_with_seasons": lambda: tmdb.tv_with_seasons(media_id, season_numbers),
        MediaTypes.SEASON.value: (
            lambda: tmdb.tv_with_seasons(media_id, season_numbers)[
                f"season/{season_numbers[0]}"
            ]
        ),
        MediaTypes.EPISODE.value: lambda: tmdb.episode(
            media_id,
            season_numbers[0],
            episode_number,
        ),
        MediaTypes.MOVIE.value: lambda: tmdb.movie(media_id),
        MediaTypes.GAME.value: lambda: igdb.game(media_id),
        MediaTypes.BOOK.value: lambda: (
            hardcover.book(media_id)
            if source == Sources.HARDCOVER.value
            else openlibrary.book(media_id)
        ),
        MediaTypes.COMIC.value: lambda: comicvine.comic(media_id),
        MediaTypes.MUSIC.value: lambda: (
            musicbrainz.music(media_id)
            if include_music_enrichment
            else musicbrainz.music(media_id, include_enrichment=False)
        ),
    }
    return metadata_retrievers[media_type]()


def search(
    media_type,
    query,
    page,
    source=None,
    *,
    preserve_ranking_fields=False,
    timeout=None,
):
    """Search for media based on the query and return the results."""
    search_options = {}
    if preserve_ranking_fields:
        search_options["preserve_ranking_fields"] = True
    if timeout is not None:
        search_options["timeout"] = timeout

    search_handlers = {
        MediaTypes.MANGA.value: lambda: (
            mangaupdates.search(
                query,
                page,
                **search_options,
            )
            if source == Sources.MANGAUPDATES.value
            else mal.search(
                media_type,
                query,
                page,
                **search_options,
            )
        ),
        MediaTypes.ANIME.value: lambda: mal.search(
            media_type,
            query,
            page,
            **search_options,
        ),
        MediaTypes.TV.value: lambda: tmdb.search(
            media_type,
            query,
            page,
            **search_options,
        ),
        MediaTypes.MOVIE.value: lambda: tmdb.search(
            media_type,
            query,
            page,
            **search_options,
        ),
        MediaTypes.SEASON.value: lambda: tmdb.search(
            MediaTypes.TV.value,
            query,
            page,
            **search_options,
        ),
        MediaTypes.EPISODE.value: lambda: tmdb.search(
            MediaTypes.TV.value,
            query,
            page,
            **search_options,
        ),
        MediaTypes.GAME.value: lambda: igdb.search(
            query,
            page,
            **search_options,
        ),
        MediaTypes.BOOK.value: lambda: (
            openlibrary.search(
                query,
                page,
                **search_options,
            )
            if source == Sources.OPENLIBRARY.value
            else _search_hardcover_or_openlibrary(query, page, search_options)
        ),
        MediaTypes.COMIC.value: lambda: comicvine.search(
            query,
            page,
            **search_options,
        ),
        MediaTypes.MUSIC.value: lambda: musicbrainz.search(
            query,
            page,
            **search_options,
        ),
    }
    return search_handlers[media_type]()


def _search_hardcover_or_openlibrary(query, page, search_options):
    """Search Hardcover, or Open Library while Hardcover is rate limited."""
    try:
        return hardcover.search(query, page, **search_options)
    except ProviderAPIError as error:
        if error.status_code != requests.codes.too_many_requests:
            raise
        logger.warning("Hardcover is rate limited; searching Open Library instead")
        # Flagged so callers don't cache it in place of Hardcover's results.
        return {**openlibrary.search(query, page, **search_options), RATE_LIMIT_FALLBACK: True}


def discover(media_type, *, source=None, page=1, page_size=None, genre=None, year=None, platform=None, sort="vote_count", watch_region=None, watch_providers=None):
    """Browse provider metadata by supported media attributes.

    ``watch_region``/``watch_providers`` filter TMDB movies and TV by streaming service.
    """
    if source == Sources.MAL.value and media_type == MediaTypes.ANIME.value:
        if year:
            msg = "year discovery is not supported for anime."
            raise ValueError(msg)
        if platform:
            msg = "platform discovery is only supported for games."
            raise ValueError(msg)
        return mal.discover_anime(
            page=page,
            page_size=page_size,
            genre=genre,
        )

    if source == Sources.MAL.value and media_type == MediaTypes.MANGA.value:
        if year:
            msg = "year discovery is not supported for manga."
            raise ValueError(msg)
        if platform:
            msg = "platform discovery is only supported for games."
            raise ValueError(msg)
        return mal.discover_manga(
            page=page,
            page_size=page_size,
            genre=genre,
        )

    if source == Sources.TMDB.value and media_type in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        if platform:
            msg = "platform discovery is only supported for games."
            raise ValueError(msg)
        watch = {"watch_region": watch_region, "watch_providers": watch_providers} if watch_providers else {}
        return tmdb.discover(media_type, page=page, genre=genre, year=year, **watch)

    if source == Sources.IGDB.value and media_type == MediaTypes.GAME.value:
        return igdb.discover(page=page, page_size=page_size, genre=genre, year=year, platform=platform)

    if media_type == MediaTypes.BOOK.value and source in [Sources.HARDCOVER.value, Sources.OPENLIBRARY.value]:
        if platform:
            msg = "platform discovery is only supported for games."
            raise ValueError(msg)
        if source == Sources.OPENLIBRARY.value:
            return openlibrary.discover(page=page, page_size=page_size, genre=genre, year=year)
        return hardcover.discover(page=page, page_size=page_size, genre=genre, year=year)

    if source == Sources.MUSICBRAINZ.value and media_type == MediaTypes.MUSIC.value:
        if year:
            msg = "year discovery is not supported for music."
            raise ValueError(msg)
        if platform:
            msg = "platform discovery is only supported for games."
            raise ValueError(msg)
        if not str(genre or "").strip():
            msg = "genre is required for MusicBrainz discovery."
            raise ValueError(msg)
        return musicbrainz.discover(page=page, page_size=page_size, genre=genre)

    msg = f"Discovery is not supported for media_type={media_type!r} and source={source!r}."
    raise NotImplementedError(msg)


def get_person_page(source, person_id, *, page=None):
    """Return person details and credits for the person page."""
    if source == Sources.TMDB.value:
        return tmdb.person_page(person_id)
    if source == Sources.HARDCOVER.value:
        return hardcover.person_page(person_id)
    if source == Sources.OPENLIBRARY.value:
        return openlibrary.person_page(person_id)
    if source == Sources.MUSICBRAINZ.value:
        return musicbrainz.person_page(person_id)
    if source == Sources.MAL.value:
        return mal.person_page(person_id)
    if source == Sources.MANGAUPDATES.value:
        return mangaupdates.person_page(person_id)
    if source == "anilist":
        from app.providers import anilist  # noqa: PLC0415

        return (
            anilist.person_page(person_id, page=page)
            if page is not None
            else anilist.person_page(person_id)
        )
    raise_not_found_error(source, person_id, "person")


def search_people(source, query, *, limit=10, timeout=3):
    """Search one supported provider for normalized people."""
    query = " ".join(str(query or "").split())
    if source not in SUPPORTED_PERSON_SOURCES:
        raise ValueError(f"Unsupported person source: {source}")
    if not query:
        raise ValueError("query is required")
    limit = min(max(int(limit), 1), 25)
    digest = hashlib.sha256(query.casefold().encode()).hexdigest()[:24]
    cache_key = (
        f"people-search:{PERSON_SEARCH_CACHE_VERSION}:{source}:{digest}:{limit}"
    )
    results = cache.get(cache_key)
    if results is not None:
        return results

    if source == "anilist":
        from app.providers import anilist  # noqa: PLC0415

        results = anilist.search_people(query, limit=limit, timeout=timeout)
    else:
        handlers = {
            Sources.TMDB.value: tmdb.search_people,
            Sources.HARDCOVER.value: hardcover.search_people,
            Sources.OPENLIBRARY.value: openlibrary.search_people,
            Sources.MUSICBRAINZ.value: musicbrainz.search_people,
            Sources.MAL.value: mal.search_people,
            Sources.MANGAUPDATES.value: mangaupdates.search_people,
        }
        results = handlers[source](query, limit=limit, timeout=timeout)
    cache.set(cache_key, results, PERSON_SEARCH_TTL)
    return results


def get_book_series(source, series_id):
    """Return a provider-backed media series."""
    if source == Sources.HARDCOVER.value:
        return hardcover.series_page(series_id)
    if source == Sources.TMDB.value:
        return tmdb.collection(series_id)
    if source == Sources.IGDB.value:
        return igdb.collection(series_id)
    if source == Sources.MAL.value:
        return mal.anime_series(series_id)
    msg = "Series pages are only supported for Hardcover, TMDB, IGDB, and MAL in v1."
    raise NotImplementedError(msg)


def get_company(source, company_id):
    """Return a provider company profile for native company pages."""
    if source == Sources.IGDB.value:
        return igdb.company(company_id)
    if source == Sources.MAL.value:
        return mal.studio(company_id)
    raise_not_found_error(source, company_id, "company")


def get_company_catalog(source, company_id, role):
    """Return normalized provider games for a company catalogue role."""
    if source == Sources.IGDB.value:
        return igdb.company_catalog(company_id, role)
    raise_not_found_error(source, company_id, "company")


def company_catalog_count(source, company, role):
    """Return the provider-advertised count for a company catalogue role."""
    if source == Sources.IGDB.value:
        return igdb.company_catalog_count(company, role)
    raise_not_found_error(source, company.get("id"), "company")


def get_company_anime(
    source,
    company_id,
    *,
    page,
    page_size,
    sort,
    direction,
    filters,
):
    """Return one provider-paginated anime studio catalog page."""
    if source == Sources.MAL.value:
        return mal.studio_anime(
            company_id,
            page=page,
            page_size=page_size,
            sort=sort,
            direction=direction,
            filters=filters,
        )
    raise_not_found_error(source, company_id, "company")


def get_company_anime_filter_options(source, company_id):
    """Return provider-backed anime studio filter choices."""
    if source == Sources.MAL.value:
        return mal.studio_anime_filter_options(company_id)
    raise_not_found_error(source, company_id, "company")
