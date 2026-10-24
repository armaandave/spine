import hashlib
import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from urllib.parse import urlencode
from heapq import heappop, heappush
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services
from app.providers.search_rank import rank_results

logger = logging.getLogger(__name__)
base_url = "https://api.myanimelist.net/v2"
jikan_base_url = "https://api.jikan.moe/v4"
base_fields = "title,alternative_titles,main_picture,pictures,media_type,start_date,end_date,synopsis,status,genres,mean,num_scoring_users,recommendations"  # noqa: E501
MANGA_MATCH_VERSION = "v1"
MANGA_MATCH_FRESH_TTL = 60 * 60 * 24
MANGA_MATCH_STALE_TTL = 60 * 60 * 24 * 30
MANGA_MATCH_FAILURE_TTL = 60 * 5
PERSON_TTL = 60 * 60 * 24
ANIME_CACHE_VERSION = "v6"
ANIME_POSTER_CACHE_VERSION = "v1"
ANIME_POSTER_TTL = 60 * 60 * 24 * 30
ANIME_CAST_CACHE_VERSION = "v1"
ANIME_CAST_FRESH_TTL = 60 * 60 * 24
ANIME_CAST_STALE_TTL = 60 * 60 * 24 * 30
ANIME_CAST_FAILURE_TTL = 60 * 5
ANIME_SERIES_CACHE_VERSION = "v2"
ANIME_SERIES_FRESH_TTL = 60 * 60 * 24
ANIME_SERIES_STALE_TTL = 60 * 60 * 24 * 30
ANIME_SERIES_FAILURE_TTL = 60 * 5
ANIME_SERIES_TIMEOUT = 3
ANIME_SERIES_BUDGET = 10
ANIME_SERIES_LIMIT = 100
ANIME_DISCOVER_TIMEOUT = 3
ANIME_DISCOVER_MAX_PAGE_SIZE = 25
ANIME_DISCOVER_JIKAN_FAILURE_TTL = 60 * 5
ANIME_DISCOVER_JIKAN_FAILURE_KEY = "mal:v1:anime-discover:jikan-failure"
MANGA_CACHE_VERSION = "v3"
MANGA_POSTER_CACHE_VERSION = "v1"
MANGA_POSTER_TTL = 60 * 60 * 24 * 30
MANGA_DISCOVER_JIKAN_FAILURE_KEY = "mal:v1:manga-discover:jikan-failure"
MAL_GENRE_PAGE_CACHE_VERSION = "v1"
MAL_GENRE_PAGE_SIZE = 100
MAL_GENRE_PAGE_FRESH_TTL = 60 * 60 * 6
MAL_GENRE_PAGE_STALE_TTL = 60 * 60 * 24 * 7
MAL_DISCOVERY_ERRORS = (
    requests.RequestException,
    AttributeError,
    KeyError,
    TypeError,
    ValueError,
)
STUDIO_CACHE_VERSION = "v2"
STUDIO_IDENTITY_TTL = 60 * 60 * 24 * 30
STUDIO_FRESH_TTL = 60 * 60 * 24
STUDIO_STALE_TTL = 60 * 60 * 24 * 30
STUDIO_FAILURE_TTL = 60 * 5
STUDIO_REQUEST_TIMEOUT = 3
STUDIO_LOCK_TTL = 60 * 4
STUDIO_ANIME_EARLIEST_YEAR = 1917
STUDIO_ANIME_PAGE_SIZE = 25
STUDIO_CATALOG_SCAN_LIMIT = 10
STUDIO_ANIME_SORT_FIELDS = {
    "popularity": "members",
    "release_date": "start_date",
    "average_rating": "score",
    "title": "title",
}


def handle_error(error):
    """Handle MAL API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.MAL.value, error) from json_error

    if status_code == requests.codes.forbidden:
        details = "API key is missing"
        raise services.ProviderAPIError(Sources.MAL.value, error, details)
    if status_code == requests.codes.bad_request:
        error_message = error_json.get("message")
        if error_message == "Invalid client id":
            details = "Invalid API key"
            raise services.ProviderAPIError(Sources.MAL.value, error, details)
        if error_message == "invalid q":
            return {"data": []}

    raise services.ProviderAPIError(Sources.MAL.value, error)


def search(media_type, query, page, *, preserve_ranking_fields=False, timeout=None):
    """Search for media on MyAnimeList."""
    rank_suffix = "_rank" if preserve_ranking_fields else ""
    cache_key = f"search_{Sources.MAL.value}_{media_type}_{query}_{page}{rank_suffix}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/{media_type}"
        params = {
            "q": query,
            "fields": "media_type,mean,num_scoring_users,start_date",
            "limit": settings.PER_PAGE,
        }
        if settings.MAL_NSFW:
            params["nsfw"] = "true"

        try:
            response = services.api_request(
                Sources.MAL.value,
                "GET",
                url,
                params=params,
                headers={"X-MAL-CLIENT-ID": settings.MAL_API},
                timeout=timeout,
            )
        except requests.exceptions.HTTPError as error:
            response = handle_error(error)

        response = response["data"]
        results = [
            {
                "media_id": media["node"]["id"],
                "source": Sources.MAL.value,
                "media_type": media_type,
                "title": media["node"]["title"],
                "image": get_image_url(media["node"]),
                "rating": media["node"].get("mean"),
                "num_scoring_users": media["node"].get("num_scoring_users"),
                "release_date": media["node"].get("start_date"),
            }
            for media in response
        ]
        if media_type == MediaTypes.ANIME.value:
            for result in results:
                _cache_anime_poster(result["media_id"], result["image"])
        elif media_type == MediaTypes.MANGA.value:
            for result in results:
                _cache_manga_poster(result["media_id"], result["image"])
        results = rank_results(
            query,
            results,
            media_type,
            preserve_ranking_fields=preserve_ranking_fields,
        )

        data = helpers.format_search_response(
            page,
            100,
            len(results),
            results,
        )

        cache.set(cache_key, data)

    return data


def search_people(query, *, limit=10, timeout=None):
    """Search Jikan's MAL people index."""
    try:
        response = services.api_request(
            Sources.MAL.value,
            "GET",
            f"{jikan_base_url}/people",
            params={
                "q": query,
                "limit": limit,
                "order_by": "favorites",
                "sort": "desc",
            },
            timeout=timeout,
            retry_rate_limits=False,
            request_session=services.person_search_session,
        )
    except requests.exceptions.HTTPError as error:
        response = handle_error(error)

    results = []
    for person in response.get("data") or []:
        if person.get("mal_id") is None or not person.get("name"):
            continue
        images = person.get("images") or {}
        image = images.get("jpg") or images.get("webp") or {}
        results.append({
            "source": Sources.MAL.value,
            "person_id": str(person["mal_id"]),
            "name": person["name"],
            "profile_url": (
                image.get("large_image_url")
                or image.get("image_url")
                or None
            ),
            "known_for_department": None,
        })
    return results[:limit]


def discover_anime(*, page=1, page_size=None, genre=None):
    """Discover MAL-addressable anime by genre."""
    return _discover_mal_genre(
        media_type=MediaTypes.ANIME.value,
        page=page,
        page_size=page_size,
        genre=genre,
        genres_provider=_jikan_anime_genres,
        jikan_provider=_jikan_anime_discovery_page,
        anilist_provider=_anilist_anime_discovery_page,
        jikan_failure_key=ANIME_DISCOVER_JIKAN_FAILURE_KEY,
    )


def _jikan_anime_discovery_page(*, page, page_size, genre_id):
    params = {
        "genres": genre_id,
        "page": page,
        "limit": page_size,
        "order_by": "scored_by",
        "sort": "desc",
    }
    if not settings.MAL_NSFW:
        params["sfw"] = True
    response = services.api_request(
        Sources.MAL.value,
        "GET",
        f"{jikan_base_url}/anime",
        params=params,
        timeout=ANIME_DISCOVER_TIMEOUT,
        retry_rate_limits=False,
    )
    entries = response.get("data")
    pagination = response.get("pagination")
    if not isinstance(entries, list) or not isinstance(pagination, dict):
        raise ValueError("Jikan returned malformed anime discovery data.")

    results = _normalize_jikan_anime(entries)
    pagination_items = pagination.get("items") or {}
    try:
        total_results = int(pagination_items["total"])
    except (KeyError, TypeError, ValueError):
        total_results = (
            (page - 1) * page_size
            + len(results)
            + (1 if pagination.get("has_next_page") else 0)
        )
    try:
        per_page = int(pagination_items["per_page"])
    except (KeyError, TypeError, ValueError):
        per_page = page_size

    return {
        "page": page,
        "per_page": per_page,
        "total_results": total_results,
        "total_pages": pagination.get("last_visible_page"),
        "results": results,
    }


def _anilist_anime_discovery_page(*, genre, page, page_size):
    from app.providers import anilist  # noqa: PLC0415

    payload = anilist.anime_genre_page(
        genre,
        page=page,
        page_size=page_size,
        include_adult=settings.MAL_NSFW,
        timeout=ANIME_DISCOVER_TIMEOUT,
    )
    page_info = payload["page_info"]
    entries = []
    for node in payload["media"]:
        title = node.get("title") or {}
        cover = node.get("coverImage") or {}
        average_score = node.get("averageScore")
        entries.append(
            {
                "mal_id": node["idMal"],
                "url": (
                    f"https://myanimelist.net/anime/{node['idMal']}"
                ),
                "title": (
                    title.get("romaji")
                    or title.get("english")
                    or title.get("native")
                    or ""
                ),
                "title_english": title.get("english"),
                "images": {
                    "jpg": {
                        "large_image_url": (
                            cover.get("extraLarge")
                            or cover.get("large")
                            or cover.get("medium")
                        ),
                    },
                },
                "aired": {"from": node.get("release_date")},
                "type": node.get("format"),
                "episodes": node.get("episodes"),
                "synopsis": helpers.plain_text(node.get("description")),
                "genres": [
                    {"name": value}
                    for value in node.get("genres") or []
                    if value
                ],
                "score": (
                    average_score / 10
                    if isinstance(average_score, (int, float))
                    else None
                ),
                "scored_by": 0,
                "members": node.get("popularity"),
                "studios": [],
            },
        )
    results = _normalize_jikan_anime(entries)
    return {
        "page": int(page_info.get("currentPage") or page),
        "per_page": int(page_info.get("perPage") or page_size),
        "total_results": int(page_info.get("total") or len(results)),
        "total_pages": page_info.get("lastPage"),
        "results": results,
    }


def discover_manga(*, page=1, page_size=None, genre=None):
    """Discover MAL-addressable manga by genre."""
    return _discover_mal_genre(
        media_type=MediaTypes.MANGA.value,
        page=page,
        page_size=page_size,
        genre=genre,
        genres_provider=_jikan_manga_genres,
        jikan_provider=_jikan_manga_discovery_page,
        anilist_provider=_anilist_manga_discovery_page,
        jikan_failure_key=MANGA_DISCOVER_JIKAN_FAILURE_KEY,
    )


def _jikan_manga_discovery_page(*, page, page_size, genre_id):
    params = {
        "genres": genre_id,
        "page": page,
        "limit": page_size,
        "order_by": "scored_by",
        "sort": "desc",
    }
    if not settings.MAL_NSFW:
        params["sfw"] = True
    response = services.api_request(
        Sources.MAL.value,
        "GET",
        f"{jikan_base_url}/manga",
        params=params,
        timeout=ANIME_DISCOVER_TIMEOUT,
        retry_rate_limits=False,
    )
    entries = response.get("data")
    pagination = response.get("pagination")
    if not isinstance(entries, list) or not isinstance(pagination, dict):
        raise ValueError("Jikan returned malformed manga discovery data.")

    results = _normalize_jikan_manga(entries)
    pagination_items = pagination.get("items") or {}
    try:
        total_results = int(pagination_items["total"])
    except (KeyError, TypeError, ValueError):
        total_results = (
            (page - 1) * page_size
            + len(results)
            + (1 if pagination.get("has_next_page") else 0)
        )
    try:
        per_page = int(pagination_items["per_page"])
    except (KeyError, TypeError, ValueError):
        per_page = page_size

    return {
        "page": page,
        "per_page": per_page,
        "total_results": total_results,
        "total_pages": pagination.get("last_visible_page"),
        "results": results,
    }


def _anilist_manga_discovery_page(*, genre, page, page_size):
    from app.providers import anilist  # noqa: PLC0415

    payload = anilist.manga_genre_page(
        genre,
        page=page,
        page_size=page_size,
        include_adult=settings.MAL_NSFW,
        timeout=ANIME_DISCOVER_TIMEOUT,
    )
    page_info = payload["page_info"]
    entries = []
    for node in payload["media"]:
        if not node.get("idMal"):
            continue
        title = node.get("title") or {}
        cover = node.get("coverImage") or {}
        average_score = node.get("averageScore")
        entries.append(
            {
                "mal_id": node["idMal"],
                "url": f"https://myanimelist.net/manga/{node['idMal']}",
                "title": (
                    title.get("romaji")
                    or title.get("english")
                    or title.get("native")
                    or ""
                ),
                "title_english": title.get("english"),
                "images": {
                    "jpg": {
                        "large_image_url": (
                            cover.get("extraLarge")
                            or cover.get("large")
                            or cover.get("medium")
                        ),
                    },
                },
                "published": {"from": node.get("release_date")},
                "type": node.get("format"),
                "chapters": node.get("chapters"),
                "volumes": node.get("volumes"),
                "synopsis": helpers.plain_text(node.get("description")),
                "genres": [
                    {"name": value}
                    for value in node.get("genres") or []
                    if value
                ],
                "score": (
                    average_score / 10
                    if isinstance(average_score, (int, float))
                    else None
                ),
                "scored_by": 0,
                "members": node.get("popularity"),
            },
        )
    results = _normalize_jikan_manga(entries)
    return {
        "page": int(page_info.get("currentPage") or page),
        "per_page": int(page_info.get("perPage") or page_size),
        "total_results": int(page_info.get("total") or len(results)),
        "total_pages": page_info.get("lastPage"),
        "results": results,
    }


def _discover_mal_genre(
    *,
    media_type,
    page,
    page_size,
    genre,
    genres_provider,
    jikan_provider,
    anilist_provider,
    jikan_failure_key,
):
    page = int(page)
    page_size = int(page_size or settings.PER_PAGE)
    genre = str(genre or "").strip()
    if page < 1:
        raise ValueError("page must be at least 1.")
    if not 1 <= page_size <= ANIME_DISCOVER_MAX_PAGE_SIZE:
        raise ValueError(
            f"page_size must be between 1 and {ANIME_DISCOVER_MAX_PAGE_SIZE}.",
        )
    if not genre:
        raise ValueError(f"genre is required for MAL {media_type} discovery.")

    genres = genres_provider(include_all=True) or _mal_genre_index(media_type)
    genre_entry = next(
        (
            item
            for item in genres
            if item["name"].casefold() == genre.casefold()
        ),
        None,
    )
    if genres and genre_entry is None:
        raise ValueError(f"Unknown MAL {media_type} genre: {genre}")
    if genre_entry is not None:
        genre = str(genre_entry["name"])
    genre_id = str(genre_entry["id"]) if genre_entry else None
    known_nonempty = genre_entry is not None
    exact_error = None

    if genre_id is None:
        exact_error = RuntimeError("MAL genre mapping is unavailable")
    elif cache.get(jikan_failure_key):
        exact_error = RuntimeError("Cached Jikan discovery failure")
    else:
        try:
            data = jikan_provider(
                page=page,
                page_size=page_size,
                genre_id=genre_id,
            )
            if _known_genre_page_is_empty(data, page, known_nonempty):
                raise ValueError("Jikan returned a false empty genre page")
            cache.delete(jikan_failure_key)
            return data
        except MAL_DISCOVERY_ERRORS as error:
            exact_error = error
            logger.warning(
                "%s_genre_discovery_jikan_failed genre=%s page=%s "
                "fallback=mal_page type=%s",
                media_type,
                genre,
                page,
                type(error).__name__,
            )
            cache.set(
                jikan_failure_key,
                True,
                ANIME_DISCOVER_JIKAN_FAILURE_TTL,
            )

    if genre_id is not None and (
        settings.MAL_NSFW or genre_id not in {"12", "49"}
    ):
        try:
            data = _mal_genre_discovery_page(
                media_type=media_type,
                genre_id=genre_id,
                page=page,
                page_size=page_size,
            )
            if _known_genre_page_is_empty(data, page, known_nonempty):
                raise ValueError("MAL returned a false empty genre page")
            if data["results"] or page > 1:
                return data
            exact_error = ValueError("MAL returned an empty first genre page")
        except MAL_DISCOVERY_ERRORS as error:
            exact_error = error
            logger.warning(
                "%s_genre_discovery_mal_page_failed genre=%s page=%s "
                "fallback=anilist type=%s",
                media_type,
                genre,
                page,
                type(error).__name__,
            )

    try:
        data = anilist_provider(
            genre=genre,
            page=page,
            page_size=page_size,
        )
    except SoftTimeLimitExceeded:
        raise
    except Exception as error:  # noqa: BLE001 - final fallback is contained
        fallback_error = error
    else:
        if data["results"] or page > 1:
            return data
        fallback_error = ValueError("AniList returned an empty first genre page")
        if exact_error is None and not known_nonempty:
            return data

    if media_type == MediaTypes.MANGA.value:
        logger.warning(
            "manga_genre_discovery_unavailable genre=%s page=%s type=%s",
            genre,
            page,
            type(fallback_error).__name__,
        )
        return {
            "page": page,
            "per_page": page_size,
            "total_results": 0,
            "total_pages": 0,
            "results": [],
        }

    error = exact_error or fallback_error
    details = f"{media_type.title()} genre discovery is temporarily unavailable"
    raise services.ProviderAPIError(Sources.MAL.value, error, details) from error


def _known_genre_page_is_empty(data, page, known_nonempty):
    return page == 1 and known_nonempty and not (data.get("results") or [])


def _mal_genre_discovery_page(*, media_type, genre_id, page, page_size):
    start = (page - 1) * page_size
    results = []
    mal_page = 1
    while len(results) < start + page_size:
        data = _mal_genre_page_data(media_type, genre_id, mal_page)
        results.extend(data["results"])
        if not data["has_next_page"]:
            break
        mal_page += 1
    total_results = (
        data["total_results"]
        if data["has_next_page"]
        else len(results)
    )
    return {
        "page": page,
        "per_page": page_size,
        "total_results": total_results,
        "total_pages": (
            (total_results + page_size - 1) // page_size
            if total_results
            else 0
        ),
        "results": results[start : start + page_size],
    }


def _mal_genre_page_data(media_type, genre_id, page):
    prefix = _mal_genre_page_cache_prefix(media_type, genre_id, page)
    fresh_key = f"{prefix}:fresh"
    stale_key = f"{prefix}:stale"
    if data := cache.get(fresh_key):
        return data

    stale = cache.get(stale_key)
    params = {"page": page}
    if not settings.MAL_NSFW:
        params["sfw"] = "1"
    try:
        response = services.session.get(
            f"https://myanimelist.net/{media_type}/genre/{genre_id}",
            params=params,
            headers={"User-Agent": "Spine/1.0"},
            timeout=ANIME_DISCOVER_TIMEOUT,
        )
        response.raise_for_status()
        data = _parse_mal_genre_page(
            response.text,
            media_type=media_type,
            genre_id=genre_id,
            page=page,
        )
        cache.set(fresh_key, data, MAL_GENRE_PAGE_FRESH_TTL)
        cache.set(stale_key, data, MAL_GENRE_PAGE_STALE_TTL)
        return data
    except MAL_DISCOVERY_ERRORS:
        if stale:
            return stale
        raise


def _mal_genre_index(media_type):
    prefix = f"mal:{MAL_GENRE_PAGE_CACHE_VERSION}:{media_type}-genre-index"
    fresh_key = f"{prefix}:fresh"
    stale_key = f"{prefix}:stale"
    if data := cache.get(fresh_key):
        return data

    stale = cache.get(stale_key)
    try:
        response = services.session.get(
            f"https://myanimelist.net/{media_type}.php",
            headers={"User-Agent": "Spine/1.0"},
            timeout=ANIME_DISCOVER_TIMEOUT,
        )
        response.raise_for_status()
        data = _parse_mal_genre_index(response.text, media_type)
        cache.set(fresh_key, data, MAL_GENRE_PAGE_FRESH_TTL)
        cache.set(stale_key, data, MAL_GENRE_PAGE_STALE_TTL)
        return data
    except MAL_DISCOVERY_ERRORS:
        return stale or []


def _parse_mal_genre_index(html, media_type):
    genres = {}
    for link in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        match = re.search(rf"/{media_type}/genre/(\d+)(?:/|$)", link["href"])
        name = re.sub(
            r"\s*\([\d,]+\)\s*$",
            "",
            link.get_text(" ", strip=True),
        )
        if match and name:
            genres[match.group(1)] = {"id": match.group(1), "name": name}
    if not genres:
        raise ValueError(f"MAL returned a malformed {media_type} genre index")
    return sorted(genres.values(), key=lambda item: item["name"].casefold())


def _mal_genre_page_cache_prefix(media_type, genre_id, page):
    return (
        f"mal:{MAL_GENRE_PAGE_CACHE_VERSION}:{media_type}-genre-page:"
        f"{genre_id}:p{page}:nsfw{int(settings.MAL_NSFW)}"
    )


def _parse_mal_genre_page(html, *, media_type, genre_id, page):
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select_one('meta[property="og:url"]')
    canonical_url = str((canonical or {}).get("content") or "").strip()
    expected_path = rf"/{media_type}/genre/{genre_id}(?:/|$)"
    if re.search(expected_path, canonical_url) is None:
        raise ValueError(f"MAL returned the wrong {media_type} genre page")

    container = soup.select_one(".js-categories-seasonal")
    if container is None:
        raise ValueError(f"MAL returned a malformed {media_type} genre page")
    cards = container.select(".js-seasonal-anime")
    if not settings.MAL_NSFW:
        cards = [
            card
            for card in cards
            if not _mal_genre_card_ids(card, media_type).intersection({"12", "49"})
        ]
    results = [
        item
        for card in cards
        if (item := _parse_mal_genre_card(card, media_type))
    ]
    _resolve_mal_genre_posters(results, media_type)

    total_results = max(
        (
            int(match.group(2))
            for link in soup.select(".pagination a")
            if (
                match := re.search(
                    r"(\d+)\s*-\s*(\d+)",
                    link.get_text(" ", strip=True),
                )
            )
        ),
        default=(page - 1) * MAL_GENRE_PAGE_SIZE + len(results),
    )
    has_next_page = bool(
        soup.select_one('link[rel~="next"], a[rel~="next"]'),
    ) or total_results > page * MAL_GENRE_PAGE_SIZE
    return {
        "page": page,
        "per_page": MAL_GENRE_PAGE_SIZE,
        "total_results": total_results,
        "has_next_page": has_next_page,
        "results": results,
    }


def _parse_mal_genre_card(card, media_type):
    title_link = card.select_one(".title a.link-title[href], .title a[href]")
    href = str((title_link or {}).get("href") or "")
    match = re.search(rf"/{media_type}/(\d+)(?:/|$)", href)
    title = title_link.get_text(" ", strip=True) if title_link else ""
    if match is None or not title:
        return None
    media_id = match.group(1)

    genre_links = card.select(f'a[href*="/{media_type}/genre/"][title]')
    genres = list(dict.fromkeys(link.get("title") for link in genre_links))

    image_node = card.select_one(".image img")
    image = _mal_large_image_url(
        str(
            (image_node or {}).get("data-src")
            or (image_node or {}).get("src")
            or "",
        ).strip(),
    )
    return {
        "media_id": media_id,
        "source": Sources.MAL.value,
        "source_url": href,
        "media_type": media_type,
        "title": title,
        "display_title": None,
        "image": image or settings.IMG_NONE,
        "genres": [genre for genre in genres if genre],
    }


def _mal_genre_card_ids(card, media_type):
    ids = set(str(card.get("data-genre") or "").split(","))
    ids.update(
        match.group(1)
        for link in card.select(f'a[href*="/{media_type}/genre/"]')
        if (match := re.search(rf"/{media_type}/genre/(\d+)", link.get("href") or ""))
    )
    return ids


def _resolve_mal_genre_posters(results, media_type):
    media_ids = [item["media_id"] for item in results]
    cache_version = (
        ANIME_CACHE_VERSION
        if media_type == MediaTypes.ANIME.value
        else MANGA_CACHE_VERSION
    )
    detail_keys = {
        media_id: f"{Sources.MAL.value}_{media_type}_{media_id}_{cache_version}"
        for media_id in media_ids
    }
    details = cache.get_many(detail_keys.values())
    posters = (
        cached_anime_posters(media_ids)
        if media_type == MediaTypes.ANIME.value
        else cached_manga_posters(media_ids)
    )
    cache_poster = (
        _cache_anime_poster
        if media_type == MediaTypes.ANIME.value
        else _cache_manga_poster
    )
    for item in results:
        detail = details.get(detail_keys[item["media_id"]])
        if isinstance(detail, dict) and detail.get("display_title"):
            item["display_title"] = detail["display_title"]
        item["image"] = posters.get(item["media_id"]) or item["image"]
        cache_poster(item["media_id"], item["image"])


def anime(media_id, *, timeout=None, retry_rate_limits=True):
    """Return the metadata for the selected anime or manga from MyAnimeList."""
    cache_key = (
        f"{Sources.MAL.value}_{MediaTypes.ANIME.value}_{media_id}_"
        f"{ANIME_CACHE_VERSION}"
    )
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/anime/{media_id}"
        params = {
            "fields": f"{base_fields},num_list_users,num_episodes,average_episode_duration,studios,start_season,broadcast,source,related_anime",  # noqa: E501
        }

        try:
            response = services.api_request(
                Sources.MAL.value,
                "GET",
                url,
                params=params,
                headers={"X-MAL-CLIENT-ID": settings.MAL_API},
                timeout=timeout,
                retry_rate_limits=retry_rate_limits,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        num_episodes = get_number_of_episodes(response)

        studio_credits = get_studio_credits(response)
        _cache_studio_identities(studio_credits, media_id)
        data = {
            "media_id": media_id,
            "source": Sources.MAL.value,
            "source_url": f"https://myanimelist.net/anime/{media_id}",
            "media_type": MediaTypes.ANIME.value,
            "title": response["title"],
            "display_title": get_english_title(response),
            "member_count": response.get("num_list_users"),
            "max_progress": num_episodes,
            "image": get_image_url(response),
            "series_format": _anime_format(response.get("media_type")),
            "posters": get_pictures(response, media_id, MediaTypes.ANIME.value),
            "synopsis": get_synopsis(response),
            "genres": get_genres(response),
            "score": get_score(response),
            "score_count": get_score_count(response),
            "details": {
                "format": get_format(response),
                "start_date": response.get("start_date"),
                "end_date": response.get("end_date"),
                "status": get_readable_status(response),
                "episodes": num_episodes,
                "runtime": get_runtime(response),
                "studios": get_studios(response),
                "company_credits": studio_credits,
                "season": get_season(response),
                "broadcast": get_broadcast(response),
                "source": get_source(response),
            },
            "related": {
                "relations": get_related(
                    response.get("related_anime"),
                    MediaTypes.ANIME.value,
                ),
                "recommendations": get_related(
                    response.get("recommendations"),
                    MediaTypes.ANIME.value,
                ),
            },
        }

        cache.set(cache_key, data)

    _cache_anime_poster(media_id, data.get("image"))
    return data


def cached_anime_poster(media_id):
    """Return MAL's cached canonical poster without making a provider request."""
    return cached_anime_posters([media_id]).get(str(media_id))


def cached_anime_posters(media_ids):
    """Return cached canonical posters for many MAL anime in two reads."""
    media_ids = {str(media_id) for media_id in media_ids if media_id not in {None, ""}}
    if not media_ids:
        return {}
    poster_keys = {
        media_id: f"mal:{ANIME_POSTER_CACHE_VERSION}:anime-poster:{media_id}"
        for media_id in media_ids
    }
    cached = cache.get_many(poster_keys.values())
    posters = {
        media_id: cached[key]
        for media_id, key in poster_keys.items()
        if cached.get(key)
    }
    missing = media_ids - posters.keys()
    if not missing:
        return posters
    detail_keys = {
        media_id: (
            f"{Sources.MAL.value}_{MediaTypes.ANIME.value}_{media_id}_"
            f"{ANIME_CACHE_VERSION}"
        )
        for media_id in missing
    }
    details = cache.get_many(detail_keys.values())
    for media_id, key in detail_keys.items():
        data = details.get(key)
        image = data.get("image") if isinstance(data, dict) else None
        if image and image != settings.IMG_NONE:
            posters[media_id] = image
    return posters


def _cache_anime_poster(media_id, image):
    if image and image != settings.IMG_NONE:
        cache.set(
            f"mal:{ANIME_POSTER_CACHE_VERSION}:anime-poster:{media_id}",
            image,
            ANIME_POSTER_TTL,
        )


def cached_manga_posters(media_ids):
    """Return cached canonical posters for many MAL manga in two reads."""
    media_ids = {
        str(media_id)
        for media_id in media_ids
        if media_id not in {None, ""}
    }
    if not media_ids:
        return {}
    poster_keys = {
        media_id: f"mal:{MANGA_POSTER_CACHE_VERSION}:manga-poster:{media_id}"
        for media_id in media_ids
    }
    cached = cache.get_many(poster_keys.values())
    posters = {
        media_id: cached[key]
        for media_id, key in poster_keys.items()
        if cached.get(key)
    }
    missing = media_ids - posters.keys()
    if not missing:
        return posters
    detail_keys = {
        media_id: (
            f"{Sources.MAL.value}_{MediaTypes.MANGA.value}_{media_id}_"
            f"{MANGA_CACHE_VERSION}"
        )
        for media_id in missing
    }
    details = cache.get_many(detail_keys.values())
    for media_id, key in detail_keys.items():
        data = details.get(key)
        image = data.get("image") if isinstance(data, dict) else None
        if image and image != settings.IMG_NONE:
            posters[media_id] = image
    return posters


def _cache_manga_poster(media_id, image):
    if image and image != settings.IMG_NONE:
        cache.set(
            f"mal:{MANGA_POSTER_CACHE_VERSION}:manga-poster:{media_id}",
            image,
            MANGA_POSTER_TTL,
        )


def anime_series(media_id):
    """Resolve the complete MAL-first prequel/sequel graph for an anime."""
    seed_id = str(media_id)
    alias_key = _anime_series_key("alias", seed_id)
    root_id = cache.get(alias_key) or seed_id
    fresh_key = _anime_series_key("fresh", root_id)
    stale_key = _anime_series_key("stale", root_id)
    if data := cache.get(fresh_key):
        _log_anime_series("fresh", data)
        return data

    stale = cache.get(stale_key)
    failure_key = _anime_series_key("failure", seed_id)
    if failure := cache.get(failure_key):
        if stale:
            _log_anime_series("stale", stale)
            return stale
        if failure == "not_found":
            services.raise_not_found_error(Sources.MAL.value, seed_id, "anime series")
        raise services.ProviderAPIError(
            Sources.MAL.value,
            RuntimeError("Cached anime series resolution failure"),
        )

    lock_key = _anime_series_key("lock", seed_id)
    if not cache.add(lock_key, 1, timeout=ANIME_SERIES_BUDGET + 2):
        if stale:
            return stale
        raise services.ProviderAPIError(
            Sources.MAL.value,
            RuntimeError("Anime series resolution is already in progress"),
        )

    started_at = time.monotonic()
    try:
        data = _resolve_anime_series(seed_id, started_at + ANIME_SERIES_BUDGET)
        root_id = data["series_id"]
        cache.set(
            _anime_series_key("fresh", root_id),
            data,
            ANIME_SERIES_FRESH_TTL,
        )
        cache.set(
            _anime_series_key("stale", root_id),
            data,
            ANIME_SERIES_STALE_TTL,
        )
        for item in data["items"]:
            cache.set(
                _anime_series_key("alias", item["media_id"]),
                root_id,
                ANIME_SERIES_STALE_TTL,
            )
        cache.delete(failure_key)
        _log_anime_series("resolved", data, started_at)
        return data
    except services.ProviderAPIError as error:
        cache.set(
            failure_key,
            "not_found" if error.status_code == requests.codes.not_found else True,
            ANIME_SERIES_FAILURE_TTL,
        )
        if stale:
            _log_anime_series("stale", stale, started_at)
            return stale
        raise
    except Exception as error:
        cache.set(failure_key, True, ANIME_SERIES_FAILURE_TTL)
        logger.warning(
            "Anime series resolution failed seed=%s elapsed_ms=%s type=%s",
            seed_id,
            int((time.monotonic() - started_at) * 1000),
            type(error).__name__,
        )
        if stale:
            return stale
        raise services.ProviderAPIError(Sources.MAL.value, error) from error
    finally:
        cache.delete(lock_key)


def cached_anime_series_id(media_id):
    """Return an already-resolved canonical root for a MAL anime."""
    return cache.get(_anime_series_key("alias", str(media_id)))


def _resolve_anime_series(seed_id, deadline):
    from app.providers import anilist  # noqa: PLC0415

    metadata = {}
    mal_edges = {}
    anilist_edges = {}
    pending = {seed_id}
    while pending:
        if len(metadata) + len(pending) > ANIME_SERIES_LIMIT:
            raise ValueError("Anime series exceeds the 100-member safety limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Anime series resolution exceeded ten seconds")

        batch = sorted(pending, key=int)
        pending.clear()
        nodes = {}
        try:
            nodes = anilist.anime_series_nodes(
                batch,
                timeout=min(ANIME_SERIES_TIMEOUT, remaining),
            )
        except (
            requests.RequestException,
            services.ProviderAPIError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            logger.warning("AniList anime series gap-fill unavailable: %s", error)

        fetched = _fetch_anime_series_members(batch, deadline)
        if len(fetched) != len(batch):
            raise RuntimeError("MAL returned an incomplete anime series graph")
        for current_id, node in nodes.items():
            if current_id in fetched:
                fetched[current_id]["anilist_popularity"] = node.get("popularity")
                if not fetched[current_id].get("display_title"):
                    fetched[current_id]["display_title"] = node.get("display_title")
        metadata.update(fetched)

        for current_id in batch:
            for related in (fetched[current_id].get("related") or {}).get("relations") or []:
                relation = str(related.get("relation") or "")
                neighbor = str(related.get("media_id") or "")
                if relation not in {"Prequel", "Sequel"} or not neighbor or neighbor == current_id:
                    continue
                pair, edge = _directed_series_edge(current_id, neighbor, relation)
                mal_edges[pair] = edge
                if neighbor not in metadata:
                    pending.add(neighbor)
            for related in (nodes.get(current_id) or {}).get("series_links") or []:
                relation = str(related.get("relation") or "")
                neighbor = str(related.get("media_id") or "")
                if relation not in {"Prequel", "Sequel"} or not neighbor or neighbor == current_id:
                    continue
                pair, edge = _directed_series_edge(current_id, neighbor, relation)
                anilist_edges[pair] = edge
                if neighbor not in metadata:
                    pending.add(neighbor)

    conflicts = sum(
        1
        for pair, edge in anilist_edges.items()
        if pair in mal_edges and mal_edges[pair] != edge
    )
    if conflicts:
        logger.warning(
            "Anime series provider conflicts seed=%s conflicts=%s",
            seed_id,
            conflicts,
        )
    edges = {**anilist_edges, **mal_edges}
    if len(metadata) < 2:
        services.raise_not_found_error(Sources.MAL.value, seed_id, "anime series")

    ordered_ids, cycled = _ordered_anime_series_ids(metadata, edges.values())
    root_candidates = set(metadata)
    for _before, after in edges.values():
        root_candidates.discard(after)
    root_id = min(root_candidates or metadata, key=lambda value: _anime_series_sort_key(metadata[value]))
    items = [
        _anime_series_item(metadata[item_id], position)
        for position, item_id in enumerate(ordered_ids, start=1)
    ]
    if cycled:
        logger.warning("Anime series cycle detected root=%s members=%s", root_id, len(items))
    representative = min(
        metadata.values(),
        key=_anime_series_representative_key,
    )
    return {
        "series_id": root_id,
        "source": Sources.MAL.value,
        "media_type": MediaTypes.ANIME.value,
        "name": (
            representative.get("display_title")
            or representative.get("title")
            or ""
        ),
        "representative_id": str(representative["media_id"]),
        "item_count": len(items),
        "items": items,
    }


def _fetch_anime_series_members(media_ids, deadline):
    results = {}
    with ThreadPoolExecutor(max_workers=min(5, len(media_ids))) as executor:
        futures = {
            executor.submit(
                anime,
                media_id,
                timeout=min(ANIME_SERIES_TIMEOUT, max(deadline - time.monotonic(), 0.01)),
                retry_rate_limits=False,
            ): media_id
            for media_id in media_ids
        }
        for future in as_completed(futures, timeout=max(deadline - time.monotonic(), 0.01)):
            results[futures[future]] = dict(future.result())
    return results


def _directed_series_edge(current_id, neighbor_id, relation):
    edge = (
        (neighbor_id, current_id)
        if relation == "Prequel"
        else (current_id, neighbor_id)
    )
    return frozenset((current_id, neighbor_id)), edge


def _ordered_anime_series_ids(metadata, edges):
    successors = {media_id: set() for media_id in metadata}
    indegree = dict.fromkeys(metadata, 0)
    for before, after in set(edges):
        if before not in metadata or after not in metadata or after in successors[before]:
            continue
        successors[before].add(after)
        indegree[after] += 1

    queue = []
    for media_id, count in indegree.items():
        if count == 0:
            heappush(queue, (_anime_series_sort_key(metadata[media_id]), media_id))
    ordered = []
    while queue:
        _key, media_id = heappop(queue)
        ordered.append(media_id)
        for successor in successors[media_id]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                heappush(
                    queue,
                    (_anime_series_sort_key(metadata[successor]), successor),
                )
    remaining = sorted(
        set(metadata) - set(ordered),
        key=lambda value: _anime_series_sort_key(metadata[value]),
    )
    return [*ordered, *remaining], bool(remaining)


def _anime_series_sort_key(metadata):
    start_date = (metadata.get("details") or {}).get("start_date")
    return (str(start_date or "9999-99-99"), int(metadata["media_id"]))


def _anime_series_representative_key(metadata):
    popularity = metadata.get("member_count")
    if not isinstance(popularity, (int, float)) or isinstance(popularity, bool):
        popularity = metadata.get("anilist_popularity")
    if not isinstance(popularity, (int, float)) or isinstance(popularity, bool):
        popularity = -1
    format_priority = {
        "TV": 0,
        "ONA": 1,
        "Movie": 2,
        "OVA": 3,
        "Special": 4,
        "Music": 5,
    }
    media_format = metadata.get("series_format") or (
        metadata.get("details") or {}
    ).get("format")
    return (
        -popularity,
        format_priority.get(media_format, len(format_priority)),
        *_anime_series_sort_key(metadata),
    )


def _anime_series_item(metadata, position):
    details = metadata.get("details") or {}
    start_date = details.get("start_date")
    year = str(start_date)[:4] if start_date else None
    subtitle = " · ".join(
        value
        for value in (
            metadata.get("series_format") or details.get("format"),
            year,
            f"{details['episodes']} episodes" if details.get("episodes") else None,
        )
        if value
    )
    return {
        "media_id": str(metadata["media_id"]),
        "source": Sources.MAL.value,
        "media_type": MediaTypes.ANIME.value,
        "title": metadata.get("title") or "",
        "display_title": metadata.get("display_title"),
        "image": metadata.get("image"),
        "release_date": start_date,
        "subtitle": subtitle or None,
        "position": position,
    }


def _anime_series_key(kind, value):
    return f"mal:{ANIME_SERIES_CACHE_VERSION}:anime-series:{kind}:{value}"


def _log_anime_series(cache_state, data, started_at=None):
    logger.info(
        "Anime series cache=%s root=%s representative=%s members=%s elapsed_ms=%s",
        cache_state,
        data.get("series_id"),
        data.get("representative_id"),
        data.get("item_count"),
        int((time.monotonic() - started_at) * 1000) if started_at else 0,
    )


def manga(media_id):
    """Return the metadata for the selected anime or manga from MyAnimeList."""
    cache_key = (
        f"{Sources.MAL.value}_{MediaTypes.MANGA.value}_{media_id}_"
        f"{MANGA_CACHE_VERSION}"
    )
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/manga/{media_id}"
        params = {
            "fields": (
                f"{base_fields},num_chapters,num_volumes,authors,serialization,"
                "related_anime,related_manga,recommendations"
            ),
        }

        try:
            response = services.api_request(
                Sources.MAL.value,
                "GET",
                url,
                params=params,
                headers={"X-MAL-CLIENT-ID": settings.MAL_API},
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        num_chapters = get_number_of_episodes(response)

        data = {
            "media_id": media_id,
            "source": Sources.MAL.value,
            "source_url": f"https://myanimelist.net/manga/{media_id}",
            "media_type": MediaTypes.MANGA.value,
            "title": response["title"],
            "display_title": get_english_title(response),
            "image": get_image_url(response),
            "posters": get_pictures(response, media_id, MediaTypes.MANGA.value),
            "synopsis": get_synopsis(response),
            "max_progress": num_chapters,
            "genres": get_genres(response),
            "score": get_score(response),
            "score_count": get_score_count(response),
            "creators": get_authors(response),
            "details": {
                "format": get_format(response),
                "start_date": response.get("start_date"),
                "end_date": response.get("end_date"),
                "status": get_readable_status(response),
                "number_of_chapters": num_chapters,
                "number_of_volumes": response.get("num_volumes") or None,
                "authors": [creator["name"] for creator in get_authors(response)],
                "serialization": [
                    item["node"]["name"]
                    for item in response.get("serialization") or []
                    if isinstance(item, dict) and (item.get("node") or {}).get("name")
                ],
            },
            "related": {
                "relations": [
                    *get_related(
                        response.get("related_manga"),
                        MediaTypes.MANGA.value,
                    ),
                    *get_related(
                        response.get("related_anime"),
                        MediaTypes.ANIME.value,
                    ),
                ],
                "recommendations": get_related(
                    response.get("recommendations"),
                    MediaTypes.MANGA.value,
                ),
            },
        }

        cache.set(cache_key, data)

    _cache_manga_poster(media_id, data.get("image"))
    return data


def match_manga(mangaupdates_id, metadata, *, timeout=3):
    """Return a strictly matched MAL manga ID for MangaUpdates metadata."""
    fresh_key, stale_key, failure_key = _manga_match_keys(mangaupdates_id)
    cached = cache.get(fresh_key)
    if cached is not None:
        return cached.get("mal_id")

    stale = cache.get(stale_key)
    if cache.get(failure_key):
        return stale.get("mal_id") if stale else None

    details = metadata.get("details") or {}
    titles = _match_titles(
        metadata.get("title"),
        details.get("alternative_titles"),
    )
    if not titles:
        cache.set(fresh_key, {"mal_id": None}, MANGA_MATCH_FRESH_TTL)
        return None

    expected_year = _year(details.get("year") or details.get("start_date"))
    expected_format = _format_family(details.get("format"))
    deadline = time.monotonic() + timeout
    candidates = {}

    try:
        for query in titles[:4]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            response = services.api_request(
                Sources.MAL.value,
                "GET",
                f"{base_url}/manga",
                params={
                    "q": query,
                    "fields": "alternative_titles,media_type,start_date",
                    "limit": 10,
                    **({"nsfw": "true"} if settings.MAL_NSFW else {}),
                },
                headers={"X-MAL-CLIENT-ID": settings.MAL_API},
                timeout=remaining,
            )
            for result in response.get("data") or []:
                node = result.get("node") or {}
                node_titles = _match_titles(
                    node.get("title"),
                    (node.get("alternative_titles") or {}).values(),
                )
                if not set(titles).intersection(node_titles):
                    continue
                node_year = _year(node.get("start_date"))
                node_format = _format_family(node.get("media_type"))
                if (
                    expected_year
                    and node_year
                    and expected_year != node_year
                ) or (
                    expected_format
                    and node_format
                    and expected_format != node_format
                ):
                    continue
                if node.get("id") is not None:
                    candidates[str(node["id"])] = node
    except (
        requests.RequestException,
        services.ProviderAPIError,
        AttributeError,
        TypeError,
        ValueError,
    ) as error:
        logger.warning(
            "MAL manga match unavailable for MangaUpdates %s: %s",
            mangaupdates_id,
            error,
        )
        cache.set(failure_key, True, MANGA_MATCH_FAILURE_TTL)
        return stale.get("mal_id") if stale else None

    match = next(iter(candidates)) if len(candidates) == 1 else None
    payload = {"mal_id": match}
    cache.set(fresh_key, payload, MANGA_MATCH_FRESH_TTL)
    if match:
        cache.set(stale_key, payload, MANGA_MATCH_STALE_TTL)
    cache.delete(failure_key)
    return match


def cached_manga_match(mangaupdates_id):
    """Return a cached positive MangaUpdates-to-MAL match."""
    fresh_key, stale_key, _failure_key = _manga_match_keys(mangaupdates_id)
    payload = cache.get(fresh_key)
    if payload is None:
        payload = cache.get(stale_key)
    return payload.get("mal_id") if payload else None


def _manga_match_keys(mangaupdates_id):
    prefix = f"mal:{MANGA_MATCH_VERSION}:manga-match:{mangaupdates_id}"
    return f"{prefix}:fresh", f"{prefix}:stale", f"{prefix}:failure"


def get_format(response):
    """Return the original type of the media."""
    media_format = response["media_type"]

    # MAL return tv in metadata for anime
    if media_format == "tv":
        return "Anime"
    if media_format in ("ova", "ona"):
        return media_format.upper()
    return media_format.replace("_", " ").title()


def get_image_url(response):
    """Return the image URL for the media."""
    # when no picture, main_picture is not present in the response
    # e.g anime: 38869
    try:
        return response["main_picture"]["large"]
    except KeyError:
        return settings.IMG_NONE


def get_english_title(response):
    """Return MAL's English title without changing the canonical title."""
    value = (response.get("alternative_titles") or {}).get("en")
    return value.strip() if isinstance(value, str) and value.strip() else None


def get_pictures(response, media_id, media_type):
    """Return deduplicated MAL poster candidates."""
    source_url = f"https://myanimelist.net/{media_type}/{media_id}"
    pictures = [response.get("main_picture"), *(response.get("pictures") or [])]
    results = []
    seen = set()
    for index, picture in enumerate(pictures):
        if not isinstance(picture, dict):
            continue
        url = picture.get("large") or picture.get("medium")
        if not url or url in seen:
            continue
        seen.add(url)
        results.append(
            {
                "url": url,
                "thumbnail_url": picture.get("medium") or url,
                "provider_name": "MyAnimeList",
                "provider_url": source_url,
                "is_original": index == 0,
            },
        )
    return results


def get_authors(response):
    """Return MAL manga creators with their credited roles."""
    creators = []
    seen = set()
    for author in response.get("authors") or []:
        node = author.get("node") or {}
        name = str(node.get("first_name") or "").strip()
        last_name = str(node.get("last_name") or "").strip()
        full_name = " ".join(part for part in (name, last_name) if part)
        full_name = full_name or str(node.get("name") or "").strip()
        role = str(author.get("role") or "").replace("_", " ").strip().title()
        key = (full_name.casefold(), role.casefold())
        if not full_name or key in seen:
            continue
        seen.add(key)
        person_id = node.get("id")
        creators.append({
            "person_id": str(person_id or ""),
            **({"person_source": Sources.MAL.value} if person_id else {}),
            "name": full_name,
            "role": role or None,
        })
    return creators[:12]


def anime_cast(media_id, *, raise_errors=False):
    """Return cached Jikan voice cast when AniList enrichment is unavailable."""
    prefix = f"mal:{ANIME_CAST_CACHE_VERSION}:anime-cast:{media_id}"
    fresh_key = f"{prefix}:fresh"
    stale_key = f"{prefix}:stale"
    failure_key = f"{prefix}:failure"
    if data := cache.get(fresh_key):
        return data

    stale = cache.get(stale_key)
    if cache.get(failure_key):
        if stale:
            return stale
        if raise_errors:
            raise RuntimeError("Cached Jikan anime cast failure")
        return []

    try:
        response = services.api_request(
            Sources.MAL.value,
            "GET",
            f"{jikan_base_url}/anime/{media_id}/characters",
            timeout=3,
        )
        cast = _normalize_jikan_anime_cast(response.get("data") or [])
        cache.set(fresh_key, cast, ANIME_CAST_FRESH_TTL)
        cache.set(stale_key, cast, ANIME_CAST_STALE_TTL)
        cache.delete(failure_key)
        return cast
    except (
        requests.RequestException,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        logger.warning("Jikan cast unavailable for MAL anime %s", media_id)
        cache.set(failure_key, True, ANIME_CAST_FAILURE_TTL)
        if stale:
            return stale
        if raise_errors:
            raise
        return []


def _normalize_jikan_anime_cast(entries):
    cast = []
    positions = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        character = entry.get("character") or {}
        character_name = str(character.get("name") or "").strip()
        voice_actors = entry.get("voice_actors") or []
        voice_actor = next(
            (
                actor
                for actor in voice_actors
                if str(actor.get("language") or "").casefold() == "japanese"
            ),
            voice_actors[0] if voice_actors else None,
        )
        person = (voice_actor or {}).get("person") or {}
        person_id = person.get("mal_id")
        name = str(person.get("name") or "").strip()
        if not person_id or not name or not character_name:
            continue
        key = str(person_id)
        if key in positions:
            current = cast[positions[key]]
            characters = current["character"].split(" · ")
            if character_name not in characters:
                current["character"] = " · ".join([*characters, character_name])
            continue
        images = person.get("images") or {}
        image = images.get("jpg") or images.get("webp") or {}
        positions[key] = len(cast)
        cast.append({
            "person_id": key,
            "person_source": Sources.MAL.value,
            "name": name,
            "character": character_name,
            "image": image.get("image_url") or image.get("large_image_url"),
        })
    return cast


def studio(studio_id):
    """Return a cached MAL studio profile enriched through Jikan or MAL."""
    studio_id = _positive_studio_id(studio_id)
    identity = cached_studio_identity(studio_id)
    prefix = _studio_cache_prefix(studio_id, "profile")
    fresh_key = f"{prefix}:fresh"
    stale_key = f"{prefix}:stale"
    failure_key = f"{prefix}:failure"
    started_at = time.monotonic()
    if data := cache.get(fresh_key):
        _log_studio_profile(studio_id, "jikan", "fresh", started_at)
        return data

    stale = cache.get(stale_key)
    if cache.get(failure_key):
        if stale:
            _log_studio_profile(studio_id, "jikan", "stale", started_at)
            return stale
        if identity:
            return _minimal_studio_profile(studio_id, identity)
        raise services.ProviderAPIError(
            Sources.MAL.value,
            RuntimeError("Cached anime studio profile failure"),
        )

    lock_key = f"{prefix}:lock"
    if not cache.add(lock_key, 1, timeout=STUDIO_LOCK_TTL):
        if stale:
            return stale
        if identity:
            return _minimal_studio_profile(studio_id, identity)
        raise services.ProviderAPIError(
            Sources.MAL.value,
            RuntimeError("Anime studio profile refresh is already in progress"),
        )

    try:
        provider = "jikan"
        try:
            response = services.api_request(
                Sources.MAL.value,
                "GET",
                f"{jikan_base_url}/producers/{studio_id}/full",
                timeout=STUDIO_REQUEST_TIMEOUT,
                retry_rate_limits=False,
            )
            producer = response.get("data")
            if not isinstance(producer, dict) or not producer:
                raise ValueError("Jikan returned no matching anime studio")
            if (
                producer.get("mal_id") is not None
                and int(producer["mal_id"]) != studio_id
            ):
                raise ValueError("Jikan returned the wrong anime studio")
            data = _normalize_jikan_studio(producer, studio_id, identity)
        except (
            requests.RequestException,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
        ) as jikan_error:
            logger.warning(
                "anime_studio_profile_jikan_failed studio_id=%s "
                "fallback=mal_page type=%s",
                studio_id,
                type(jikan_error).__name__,
            )
            data = _mal_studio_page_data(studio_id, identity)["profile"]
            provider = "mal_page"
        if not data["name"]:
            raise ValueError("MAL returned an anime studio without a name")
        _cache_studio_profile_identity(data, identity)
        cache.set(fresh_key, data, STUDIO_FRESH_TTL)
        cache.set(stale_key, data, STUDIO_STALE_TTL)
        cache.delete(failure_key)
        _log_studio_profile(studio_id, provider, "resolved", started_at)
        return data
    except services.ProviderAPIError:
        raise
    except (
        requests.RequestException,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        cache.set(failure_key, True, STUDIO_FAILURE_TTL)
        logger.warning(
            "anime_studio_profile_failed studio_id=%s elapsed_ms=%s type=%s",
            studio_id,
            int((time.monotonic() - started_at) * 1000),
            type(error).__name__,
        )
        if stale:
            return stale
        if identity:
            return _minimal_studio_profile(studio_id, identity)
        if (
            isinstance(error, requests.exceptions.HTTPError)
            and getattr(error.response, "status_code", None)
            == requests.codes.not_found
        ):
            services.raise_not_found_error(
                Sources.MAL.value,
                studio_id,
                "company",
            )
        raise services.ProviderAPIError(Sources.MAL.value, error) from error
    finally:
        cache.delete(lock_key)


def studio_anime(
    studio_id,
    *,
    page=1,
    page_size=STUDIO_ANIME_PAGE_SIZE,
    sort="popularity",
    direction=None,
    filters=None,
):
    """Return one provider-paginated page of verified MAL studio anime."""
    studio_id = _positive_studio_id(studio_id)
    page = int(page)
    page_size = int(page_size)
    if page < 1:
        raise ValueError("page must be at least 1.")
    if not 1 <= page_size <= STUDIO_ANIME_PAGE_SIZE:
        raise ValueError(
            f"page_size must be between 1 and {STUDIO_ANIME_PAGE_SIZE}.",
        )
    if sort not in STUDIO_ANIME_SORT_FIELDS:
        raise ValueError(
            "sort must be popularity, release_date, title, or average_rating.",
        )
    if direction not in {None, "asc", "desc"}:
        raise ValueError("direction must be asc or desc.")
    direction = direction or ("asc" if sort == "title" else "desc")
    filters = filters or {}

    cache_token = _studio_catalog_token(
        page=page,
        page_size=page_size,
        sort=sort,
        direction=direction,
        filters=filters,
    )
    prefix = _studio_cache_prefix(studio_id, f"anime:{cache_token}")
    fresh_key = f"{prefix}:fresh"
    stale_key = f"{prefix}:stale"
    failure_key = f"{prefix}:failure"
    started_at = time.monotonic()
    if data := cache.get(fresh_key):
        _log_studio_catalog(studio_id, data, "fresh", started_at)
        return data

    stale = cache.get(stale_key)
    if cache.get(failure_key):
        if stale:
            _log_studio_catalog(studio_id, stale, "stale", started_at)
            return stale
        raise services.ProviderAPIError(
            Sources.MAL.value,
            RuntimeError("Cached anime studio catalog failure"),
        )

    lock_key = f"{prefix}:lock"
    if not cache.add(lock_key, 1, timeout=STUDIO_LOCK_TTL):
        if stale:
            return stale
        raise services.ProviderAPIError(
            Sources.MAL.value,
            RuntimeError("Anime studio catalog refresh is already in progress"),
        )

    try:
        try:
            data = _jikan_studio_anime_page(
                studio_id,
                page=page,
                page_size=page_size,
                sort=sort,
                direction=direction,
                filters=filters,
            )
        except (
            requests.RequestException,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
        ) as jikan_error:
            logger.warning(
                "anime_studio_catalog_jikan_failed studio_id=%s "
                "fallback=mal_page type=%s",
                studio_id,
                type(jikan_error).__name__,
            )
            try:
                data = _mal_studio_page_anime_page(
                    studio_id,
                    page=page,
                    page_size=page_size,
                    sort=sort,
                    direction=direction,
                    filters=filters,
                )
            except (
                requests.RequestException,
                AttributeError,
                KeyError,
                TypeError,
                ValueError,
            ) as mal_page_error:
                if stale:
                    cache.set(failure_key, True, STUDIO_FAILURE_TTL)
                    logger.warning(
                        "anime_studio_catalog_mal_page_failed studio_id=%s "
                        "fallback=stale type=%s",
                        studio_id,
                        type(mal_page_error).__name__,
                    )
                    return stale
                logger.warning(
                    "anime_studio_catalog_mal_page_failed studio_id=%s "
                    "fallback=anilist type=%s",
                    studio_id,
                    type(mal_page_error).__name__,
                )
                data = _anilist_studio_anime_page(
                    studio_id,
                    page=page,
                    page_size=page_size,
                    sort=sort,
                    direction=direction,
                    filters=filters,
                )

        cache.set(fresh_key, data, STUDIO_FRESH_TTL)
        cache.set(stale_key, data, STUDIO_STALE_TTL)
        cache.delete(failure_key)
        _log_studio_catalog(studio_id, data, "resolved", started_at)
        return data
    except services.ProviderAPIError:
        cache.set(failure_key, True, STUDIO_FAILURE_TTL)
        if stale:
            return stale
        raise
    except (
        requests.RequestException,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        cache.set(failure_key, True, STUDIO_FAILURE_TTL)
        logger.warning(
            "anime_studio_catalog_failed studio_id=%s elapsed_ms=%s type=%s",
            studio_id,
            int((time.monotonic() - started_at) * 1000),
            type(error).__name__,
        )
        if stale:
            return stale
        raise services.ProviderAPIError(Sources.MAL.value, error) from error
    finally:
        cache.delete(lock_key)


def studio_anime_completion_catalog(studio_id, *, filters=None):
    """Return the full filtered studio catalog when MAL can prove it."""
    studio_id = _positive_studio_id(studio_id)
    filters = filters or {}
    cache_token = _studio_catalog_token(
        page=0,
        page_size=0,
        sort="completion",
        direction="",
        filters=filters,
    )
    cache_key = _studio_cache_prefix(
        studio_id,
        f"anime-completion:{cache_token}",
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        results = [
            {
                "media_id": item["media_id"],
                "source": Sources.MAL.value,
                "media_type": MediaTypes.ANIME.value,
            }
            for item in _mal_studio_anime_catalog(studio_id, filters)
        ]
        payload = {"complete": True, "results": results}
        cache.set(cache_key, payload, STUDIO_FRESH_TTL)
        return payload
    except (
        requests.RequestException,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        logger.warning(
            "anime_studio_completion_unavailable studio_id=%s type=%s",
            studio_id,
            type(error).__name__,
        )
        payload = {"complete": False, "results": []}
        cache.set(cache_key, payload, STUDIO_FAILURE_TTL)
        return payload


def studio_anime_filter_options(studio_id):
    """Return deterministic MAL studio-catalog filter choices."""
    studio_id = _positive_studio_id(studio_id)
    profile = studio(studio_id)
    first_year = profile.get("founded_year") or STUDIO_ANIME_EARLIEST_YEAR
    current_year = datetime.now(tz=ZoneInfo("UTC")).year + 1
    if first_year > current_year:
        first_year = STUDIO_ANIME_EARLIEST_YEAR
    genres = _jikan_anime_genres()
    return {
        "genres": [
            {"value": genre["name"], "label": genre["name"]}
            for genre in genres
        ],
        "languages": [],
        "platforms": [],
        "years": list(range(current_year, first_year - 1, -1)),
    }


def _jikan_studio_anime_page(
    studio_id,
    *,
    page,
    page_size,
    sort,
    direction,
    filters,
):
    requested_page = page
    current_page = page
    role_filtered = 0
    pages_scanned = 0
    while True:
        pages_scanned += 1
        response = services.api_request(
            Sources.MAL.value,
            "GET",
            f"{jikan_base_url}/anime",
            params=_jikan_studio_anime_params(
                studio_id,
                page=current_page,
                page_size=page_size,
                sort=sort,
                direction=direction,
                filters=filters,
            ),
            timeout=STUDIO_REQUEST_TIMEOUT,
            retry_rate_limits=False,
        )
        entries = response.get("data")
        pagination = response.get("pagination") or {}
        if not isinstance(entries, list):
            raise ValueError("Jikan returned a malformed anime studio catalog")
        studio_entries = [
            entry
            for entry in entries
            if str(studio_id) in _studio_ids_from_media(entry)
        ]
        role_filtered += len(entries) - len(studio_entries)
        matching = [
            entry
            for entry in studio_entries
            if _studio_anime_matches(entry, filters)
        ]
        normalized = _normalize_jikan_anime(matching)
        has_next = bool(pagination.get("has_next_page"))
        if normalized or not has_next:
            return {
                "count": None,
                "page": current_page,
                "previous_page": (
                    requested_page - 1
                    if requested_page > 1
                    else None
                ),
                "next_page": current_page + 1 if has_next else None,
                "results": normalized,
                "provider": "jikan",
                "role_filtered": role_filtered,
            }
        if pages_scanned >= STUDIO_CATALOG_SCAN_LIMIT:
            raise ValueError(
                "Jikan studio catalog exceeded the sparse-page scan limit",
            )
        next_page = int(pagination.get("current_page") or current_page) + 1
        if next_page <= current_page:
            raise ValueError("Jikan returned invalid studio pagination")
        current_page = next_page


def _anilist_studio_anime_page(
    studio_id,
    *,
    page,
    page_size,
    sort,
    direction,
    filters,
):
    from app.providers import anilist  # noqa: PLC0415

    identity = cached_studio_identity(studio_id) or {}
    studio_name = identity.get("name")
    seed_anime_id = identity.get("seed_anime_id")
    if not studio_name or not seed_anime_id:
        raise ValueError("AniList studio fallback requires a cached MAL identity")

    requested_page = page
    current_page = page
    role_filtered = 0
    pages_scanned = 0
    while True:
        pages_scanned += 1
        payload = anilist.studio_anime_ids(
            studio_id,
            studio_name=studio_name,
            seed_anime_id=seed_anime_id,
            page=current_page,
            page_size=page_size,
            sort=sort,
            direction=direction,
            timeout=STUDIO_REQUEST_TIMEOUT,
        )
        anime_ids = payload["anime_ids"]
        metadata = _fetch_studio_anime_metadata(anime_ids)
        if anime_ids and not metadata:
            raise ValueError(
                "MAL returned no AniList studio fallback metadata",
            )
        if len(metadata) != len(anime_ids):
            logger.warning(
                "anime_studio_fallback_partial studio_id=%s requested=%s "
                "resolved=%s",
                studio_id,
                len(anime_ids),
                len(metadata),
            )
        matching = []
        for anime_id in anime_ids:
            item = metadata.get(str(anime_id))
            if not item or str(studio_id) not in _studio_ids_from_media(item):
                role_filtered += 1
                continue
            if _studio_anime_matches(item, filters):
                matching.append(item)
        has_next = payload["has_next_page"]
        if matching or not has_next:
            return {
                "count": None,
                "page": current_page,
                "previous_page": (
                    requested_page - 1
                    if requested_page > 1
                    else None
                ),
                "next_page": current_page + 1 if has_next else None,
                "results": matching,
                "provider": "anilist",
                "role_filtered": role_filtered,
            }
        if pages_scanned >= STUDIO_CATALOG_SCAN_LIMIT:
            raise ValueError(
                "AniList studio catalog exceeded the sparse-page scan limit",
            )
        current_page += 1


def _fetch_studio_anime_metadata(anime_ids):
    if not anime_ids:
        return {}
    results = {}
    with ThreadPoolExecutor(max_workers=min(5, len(anime_ids))) as executor:
        futures = {
            executor.submit(
                anime,
                anime_id,
                timeout=STUDIO_REQUEST_TIMEOUT,
                retry_rate_limits=False,
            ): str(anime_id)
            for anime_id in anime_ids
        }
        for future in as_completed(futures):
            anime_id = futures[future]
            try:
                results[anime_id] = future.result()
            except (
                requests.RequestException,
                services.ProviderAPIError,
                AttributeError,
                KeyError,
                TypeError,
                ValueError,
            ):
                logger.warning(
                    "anime_studio_fallback_member_failed anime_id=%s",
                    anime_id,
                )
    return results


def _normalize_jikan_anime(entries):
    posters = cached_anime_posters(
        entry.get("mal_id")
        for entry in entries
    )
    results = []
    for entry in entries:
        anime_id = str(entry.get("mal_id") or "")
        if not anime_id:
            continue
        images = entry.get("images") or {}
        image = images.get("jpg") or images.get("webp") or {}
        studios = [
            studio
            for studio in entry.get("studios") or []
            if studio.get("mal_id") is not None and studio.get("name")
        ]
        release_date = ((entry.get("aired") or {}).get("from"))
        resolved_image = (
            posters.get(anime_id)
            or image.get("large_image_url")
            or image.get("image_url")
            or settings.IMG_NONE
        )
        _cache_anime_poster(anime_id, resolved_image)
        results.append(
            {
                "media_id": anime_id,
                "source": Sources.MAL.value,
                "source_url": (
                    entry.get("url")
                    or f"https://myanimelist.net/anime/{anime_id}"
                ),
                "media_type": MediaTypes.ANIME.value,
                "title": entry.get("title") or "",
                "display_title": entry.get("title_english") or None,
                "image": resolved_image,
                "release_date": release_date,
                "series_format": entry.get("type"),
                "max_progress": entry.get("episodes"),
                "synopsis": entry.get("synopsis"),
                "genres": [
                    genre["name"]
                    for genre in entry.get("genres") or []
                    if genre.get("name")
                ],
                "score": entry.get("score"),
                "score_count": entry.get("scored_by") or 0,
                "member_count": entry.get("members"),
                "details": {
                    "format": entry.get("type"),
                    "start_date": release_date,
                    "episodes": entry.get("episodes"),
                    "studios": [studio["name"] for studio in studios] or None,
                    "company_credits": [
                        {
                            "id": str(studio["mal_id"]),
                            "source": Sources.MAL.value,
                            "name": studio["name"],
                            "roles": ["Studio"],
                        }
                        for studio in studios
                    ],
                },
            },
        )
    return results


def _normalize_jikan_manga(entries):
    posters = cached_manga_posters(
        entry.get("mal_id")
        for entry in entries
    )
    results = []
    for entry in entries:
        manga_id = str(entry.get("mal_id") or "")
        if not manga_id:
            continue
        images = entry.get("images") or {}
        image = images.get("jpg") or images.get("webp") or {}
        release_date = (entry.get("published") or {}).get("from")
        resolved_image = (
            posters.get(manga_id)
            or image.get("large_image_url")
            or image.get("image_url")
            or settings.IMG_NONE
        )
        _cache_manga_poster(manga_id, resolved_image)
        genres = []
        for field in ("genres", "themes", "demographics"):
            for value in entry.get(field) or []:
                name = value.get("name")
                if name and name not in genres:
                    genres.append(name)
        results.append(
            {
                "media_id": manga_id,
                "source": Sources.MAL.value,
                "source_url": (
                    entry.get("url")
                    or f"https://myanimelist.net/manga/{manga_id}"
                ),
                "media_type": MediaTypes.MANGA.value,
                "title": entry.get("title") or "",
                "display_title": entry.get("title_english") or None,
                "image": resolved_image,
                "release_date": release_date,
                "series_format": entry.get("type"),
                "max_progress": entry.get("chapters"),
                "synopsis": entry.get("synopsis"),
                "genres": genres,
                "score": entry.get("score"),
                "score_count": entry.get("scored_by") or 0,
                "member_count": entry.get("members"),
                "details": {
                    "format": entry.get("type"),
                    "start_date": release_date,
                    "number_of_chapters": entry.get("chapters"),
                    "number_of_volumes": entry.get("volumes"),
                },
            },
        )
    return results


def _jikan_studio_anime_params(
    studio_id,
    *,
    page,
    page_size,
    sort,
    direction,
    filters,
):
    params = {
        "producers": studio_id,
        "page": page,
        "limit": page_size,
        "order_by": STUDIO_ANIME_SORT_FIELDS[sort],
        "sort": direction,
    }
    year = filters.get("year")
    if year is not None:
        params["start_date"] = f"{year:04d}-01-01"
        params["end_date"] = f"{year:04d}-12-31"
    if filters.get("rating_min") is not None:
        params["min_score"] = filters["rating_min"]
    if filters.get("rating_max") is not None:
        params["max_score"] = filters["rating_max"]

    genres = _jikan_anime_genres()
    genre_ids = {
        genre["name"].casefold(): str(genre["id"])
        for genre in genres
    }
    included = [
        genre_ids[name.casefold()]
        for name in filters.get("genres") or []
        if name.casefold() in genre_ids
    ]
    excluded = [
        genre_ids[name.casefold()]
        for name in filters.get("excluded_genres") or []
        if name.casefold() in genre_ids
    ]
    if included:
        params["genres"] = ",".join(included)
    if excluded:
        params["genres_exclude"] = ",".join(excluded)
    return params


def _studio_anime_matches(entry, filters):
    genre_names = {
        str(
            genre.get("name") if isinstance(genre, dict) else genre,
        ).strip().casefold()
        for genre in entry.get("genres") or []
        if genre
    }
    included = {
        str(value).strip().casefold()
        for value in filters.get("genres") or []
        if value
    }
    excluded = {
        str(value).strip().casefold()
        for value in filters.get("excluded_genres") or []
        if value
    }
    if included and included.isdisjoint(genre_names):
        return False
    if excluded and not excluded.isdisjoint(genre_names):
        return False

    release_date = _studio_release_date(
        entry.get("release_date")
        or ((entry.get("aired") or {}).get("from"))
        or (entry.get("details") or {}).get("start_date"),
    )
    year = filters.get("year")
    if year is not None and (release_date is None or release_date.year != year):
        return False
    release_status = filters.get("release_status")
    today = datetime.now(tz=ZoneInfo("UTC")).date()
    if release_status == "released" and (
        release_date is None or release_date > today
    ):
        return False
    if release_status == "unreleased" and (
        release_date is None or release_date <= today
    ):
        return False

    rating_min = filters.get("rating_min")
    rating_max = filters.get("rating_max")
    if rating_min is not None or rating_max is not None:
        score = entry.get("score")
        if score in {None, ""}:
            return False
        score = float(score)
        if rating_min is not None and score < float(rating_min):
            return False
        if rating_max is not None and score > float(rating_max):
            return False
    return True


def _studio_ids_from_media(entry):
    raw_studios = entry.get("studios") or []
    details = entry.get("details") or {}
    credits = details.get("company_credits") or []
    return {
        str(value)
        for value in [
            *[
                studio.get("mal_id") or studio.get("id")
                for studio in raw_studios
                if isinstance(studio, dict)
            ],
            *[
                credit.get("id")
                for credit in credits
                if isinstance(credit, dict)
                and "Studio" in (credit.get("roles") or [])
            ],
        ]
        if value is not None
    }


def _mal_studio_page_data(studio_id, identity=None):
    prefix = _studio_cache_prefix(studio_id, "mal-page")
    fresh_key = f"{prefix}:fresh"
    stale_key = f"{prefix}:stale"
    if data := cache.get(fresh_key):
        return data

    stale = cache.get(stale_key)
    try:
        response = services.session.get(
            f"https://myanimelist.net/anime/producer/{studio_id}",
            headers={"User-Agent": "Spine/1.0"},
            timeout=STUDIO_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = _parse_mal_studio_page(
            response.text,
            studio_id,
            identity=identity,
            provider_url=response.url,
        )
        cache.set(fresh_key, data, STUDIO_FRESH_TTL)
        cache.set(stale_key, data, STUDIO_STALE_TTL)
        return data
    except (
        requests.RequestException,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        if stale:
            return stale
        raise


def _parse_mal_studio_page(
    html,
    studio_id,
    *,
    identity=None,
    provider_url=None,
):
    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.select_one('meta[property="og:url"]')
    canonical_url = (
        str(canonical.get("content") or "").strip()
        if canonical
        else ""
    )
    expected_path = rf"/anime/producer/{studio_id}(?:/|$)"
    if re.search(expected_path, canonical_url) is None:
        raise ValueError("MAL returned the wrong anime studio page")

    sidebar = soup.select_one(".content-left")
    if sidebar is None:
        raise ValueError("MAL returned a malformed anime studio page")

    logo_node = sidebar.select_one(".logo img")
    logo_url = (
        str(
            (logo_node or {}).get("data-src")
            or (logo_node or {}).get("src")
            or "",
        ).strip()
    )
    if not logo_url:
        open_graph_image = soup.select_one('meta[property="og:image"]')
        logo_url = (
            str(open_graph_image.get("content") or "").strip()
            if open_graph_image
            else ""
        )

    title_node = soup.select_one('meta[property="og:title"]')
    page_name = (
        str((logo_node or {}).get("alt") or "").strip()
        or str((title_node or {}).get("content") or "")
        .split(" - Companies", maxsplit=1)[0]
        .strip()
    )
    name = (identity or {}).get("name") or page_name
    if not name:
        raise ValueError("MAL returned an anime studio without a name")

    founded_year = None
    description_candidates = []
    for row in sidebar.select(".spaceit_pad"):
        label = row.select_one(".dark_text")
        text = row.get_text(" ", strip=True)
        if label:
            if label.get_text(" ", strip=True).rstrip(":") == "Established":
                match = re.search(r"\b(?:19|20)\d{2}\b", text)
                founded_year = int(match.group()) if match else None
            continue
        if text:
            description_candidates.append(text)

    provider_url = canonical_url or provider_url or (
        f"https://myanimelist.net/anime/producer/{studio_id}"
    )
    websites = []
    for link in sidebar.select(".user-profile-sns a[href]"):
        url = str(link.get("href") or "").strip()
        if url and url != provider_url and url not in websites:
            websites.append(url)

    anime = [
        item
        for card in soup.select(".js-anime-category-studio")
        if (item := _parse_mal_studio_card(card, studio_id, name))
    ]
    return {
        "profile": {
            "id": str(studio_id),
            "source": Sources.MAL.value,
            "name": name,
            "description": (
                max(description_candidates, key=len)
                if description_candidates
                else None
            ),
            "image": logo_url or None,
            "founded_year": founded_year,
            "provider_url": provider_url,
            "websites": websites,
        },
        "anime": anime,
        "catalog_complete": soup.select_one(
            'a[rel~="next"], .pagination a.next, .pagination .link.next',
        ) is None,
    }


def _parse_mal_studio_card(card, studio_id, studio_name):
    title_link = card.select_one(".title a[href]")
    href = str((title_link or {}).get("href") or "")
    match = re.search(r"/anime/(\d+)(?:/|$)", href)
    if match is None:
        return None
    anime_id = match.group(1)
    hidden_title = card.select_one(".js-title")
    title = (
        (title_link.get_text(" ", strip=True) if title_link else "")
        or (hidden_title.get_text(" ", strip=True) if hidden_title else "")
    )
    if not title:
        return None

    image_node = card.select_one(".image img")
    image = str(
        (image_node or {}).get("data-src")
        or (image_node or {}).get("src")
        or "",
    ).strip()
    image = _mal_large_image_url(image)

    date_node = card.select_one(".js-start_date")
    raw_date = date_node.get_text(" ", strip=True) if date_node else ""
    release_date = None
    if re.fullmatch(r"\d{8}", raw_date):
        try:
            release_date = datetime.strptime(raw_date, "%Y%m%d").date().isoformat()
        except ValueError:
            pass

    score_node = card.select_one(".js-score")
    score_text = score_node.get_text(" ", strip=True) if score_node else ""
    try:
        score = float(score_text)
    except ValueError:
        score = None

    members_node = card.select_one(".js-members")
    members_text = (
        members_node.get_text(" ", strip=True) if members_node else ""
    ).replace(",", "")
    member_count = int(members_text) if members_text.isdigit() else None
    type_code = next(
        (
            class_name.removeprefix("js-anime-type-")
            for class_name in card.get("class") or []
            if class_name.startswith("js-anime-type-")
            and class_name != "js-anime-type-all"
        ),
        None,
    )
    series_format = {
        "1": "TV",
        "2": "OVA",
        "3": "Movie",
        "4": "Special",
        "5": "ONA",
        "6": "Music",
    }.get(type_code)
    genre_ids = [
        value
        for value in str(card.get("data-genre") or "").split(",")
        if value
    ]
    return {
        "media_id": anime_id,
        "source": Sources.MAL.value,
        "source_url": href or f"https://myanimelist.net/anime/{anime_id}",
        "media_type": MediaTypes.ANIME.value,
        "title": title,
        "display_title": None,
        "image": image or settings.IMG_NONE,
        "release_date": release_date,
        "series_format": series_format,
        "max_progress": None,
        "synopsis": None,
        "genres": [],
        "genre_ids": genre_ids,
        "score": score,
        "score_count": 0,
        "member_count": member_count,
        "details": {
            "format": series_format,
            "start_date": release_date,
            "episodes": None,
            "studios": [studio_name],
            "company_credits": [
                {
                    "id": str(studio_id),
                    "source": Sources.MAL.value,
                    "name": studio_name,
                    "roles": ["Studio"],
                },
            ],
        },
    }


def _mal_studio_page_anime_page(
    studio_id,
    *,
    page,
    page_size,
    sort,
    direction,
    filters,
):
    anime = _mal_studio_anime_catalog(studio_id, filters)

    value_key = {
        "popularity": lambda item: item.get("member_count"),
        "release_date": lambda item: item.get("release_date"),
        "average_rating": lambda item: item.get("score"),
        "title": lambda item: str(item.get("title") or "").casefold(),
    }[sort]
    present = [item for item in anime if value_key(item) not in {None, ""}]
    missing = [item for item in anime if value_key(item) in {None, ""}]
    present.sort(key=lambda item: int(item["media_id"]))
    present.sort(key=value_key, reverse=direction == "desc")
    missing.sort(key=lambda item: int(item["media_id"]))
    ordered = [*present, *missing]

    start = (page - 1) * page_size
    end = start + page_size
    return {
        "count": None,
        "page": page,
        "previous_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if end < len(ordered) else None,
        "results": ordered[start:end],
        "provider": "mal_page",
        "role_filtered": 0,
    }


def _mal_studio_anime_catalog(studio_id, filters):
    identity = cached_studio_identity(studio_id) or {}
    data = _mal_studio_page_data(studio_id, identity)
    if data.get("catalog_complete") is False:
        raise ValueError("MAL studio page did not contain the full catalog")
    genre_map = {
        str(genre["id"]): genre["name"]
        for genre in _jikan_anime_genres()
    }
    requested_genres = [
        *(filters.get("genres") or []),
        *(filters.get("excluded_genres") or []),
    ]
    if requested_genres and not genre_map:
        raise ValueError("MAL anime genre mapping is unavailable")

    anime = []
    for raw_item in data["anime"]:
        item = {
            **raw_item,
            "genres": [
                genre_map[genre_id]
                for genre_id in raw_item.get("genre_ids") or []
                if genre_id in genre_map
            ],
        }
        if _studio_anime_matches(item, filters):
            anime.append(item)
    return anime


def _mal_large_image_url(url):
    if not url or "cdn.myanimelist.net/images/anime/" not in url:
        return url
    return re.sub(
        r"(?<!l)(\.(?:jpe?g|webp))$",
        r"l\1",
        url,
        flags=re.IGNORECASE,
    )


def _jikan_anime_genres(*, include_all=False):
    return _jikan_media_genres(MediaTypes.ANIME.value, include_all=include_all)


def _jikan_manga_genres(*, include_all=False):
    return _jikan_media_genres(MediaTypes.MANGA.value, include_all=include_all)


def _jikan_media_genres(media_type, *, include_all=False):
    scope = "all" if include_all else "genres"
    prefix = f"mal:{STUDIO_CACHE_VERSION}:{media_type}-genres:{scope}"
    fresh_key = f"{prefix}:fresh"
    stale_key = f"{prefix}:stale"
    failure_key = f"{prefix}:failure"
    if data := cache.get(fresh_key):
        return data
    stale = cache.get(stale_key)
    if cache.get(failure_key):
        return stale or []
    lock_key = f"{prefix}:lock"
    if not cache.add(lock_key, 1, timeout=STUDIO_LOCK_TTL):
        return stale or []
    try:
        response = services.api_request(
            Sources.MAL.value,
            "GET",
            f"{jikan_base_url}/genres/{media_type}",
            params={} if include_all else {"filter": "genres"},
            timeout=STUDIO_REQUEST_TIMEOUT,
            retry_rate_limits=False,
        )
        data = [
            {"id": genre["mal_id"], "name": str(genre["name"]).strip()}
            for genre in response.get("data") or []
            if genre.get("mal_id") is not None
            and str(genre.get("name") or "").strip()
        ]
        data.sort(key=lambda genre: genre["name"].casefold())
        cache.set(fresh_key, data, STUDIO_FRESH_TTL)
        cache.set(stale_key, data, STUDIO_STALE_TTL)
        cache.delete(failure_key)
        return data
    except (
        requests.RequestException,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        cache.set(failure_key, True, STUDIO_FAILURE_TTL)
        return stale or []
    finally:
        cache.delete(lock_key)


def _normalize_jikan_studio(producer, studio_id, identity):
    images = producer.get("images") or {}
    image = images.get("jpg") or images.get("webp") or {}
    provider_url = (
        producer.get("url")
        or f"https://myanimelist.net/anime/producer/{studio_id}"
    )
    websites = []
    for website in producer.get("external") or []:
        url = website.get("url") if isinstance(website, dict) else None
        if url and url != provider_url and url not in websites:
            websites.append(url)
    return {
        "id": str(studio_id),
        "source": Sources.MAL.value,
        "name": (
            (identity or {}).get("name")
            or _jikan_studio_name(producer)
            or ""
        ),
        "description": helpers.plain_text(producer.get("about")),
        "image": (
            image.get("large_image_url")
            or image.get("image_url")
        ),
        "founded_year": _studio_year(producer.get("established")),
        "provider_url": provider_url,
        "websites": websites,
    }


def _minimal_studio_profile(studio_id, identity):
    return {
        "id": str(studio_id),
        "source": Sources.MAL.value,
        "name": identity.get("name") or "",
        "description": None,
        "image": None,
        "founded_year": None,
        "provider_url": f"https://myanimelist.net/anime/producer/{studio_id}",
        "websites": [],
    }


def _cache_studio_profile_identity(profile, identity):
    cache.set(
        _studio_identity_key(profile["id"]),
        {
            "id": profile["id"],
            "name": profile["name"],
            "seed_anime_id": (identity or {}).get("seed_anime_id"),
        },
        STUDIO_IDENTITY_TTL,
    )


def _jikan_studio_name(producer):
    titles = producer.get("titles") or []
    preferred = next(
        (
            title.get("title")
            for title in titles
            if str(title.get("type") or "").casefold() == "default"
        ),
        None,
    )
    if preferred:
        return str(preferred).strip()
    return next(
        (
            str(title.get("title") or "").strip()
            for title in titles
            if str(title.get("title") or "").strip()
        ),
        str(producer.get("name") or "").strip(),
    )


def _studio_year(value):
    match = re.search(r"\b(\d{4})\b", str(value or ""))
    return int(match.group(1)) if match else None


def _studio_release_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _positive_studio_id(studio_id):
    try:
        value = int(studio_id)
    except (TypeError, ValueError) as error:
        raise ValueError("company_id must be a positive integer.") from error
    if value < 1:
        raise ValueError("company_id must be a positive integer.")
    return value


def _studio_cache_prefix(studio_id, kind):
    return (
        f"mal:{STUDIO_CACHE_VERSION}:studio:{studio_id}:{kind}"
    )


def _studio_catalog_token(*, page, page_size, sort, direction, filters):
    values = [
        ("page", page),
        ("page_size", page_size),
        ("sort", sort),
        ("direction", direction),
        (
            "year",
            filters.get("year")
            if filters.get("year") is not None
            else "",
        ),
        ("release_status", filters.get("release_status") or ""),
        (
            "rating_min",
            filters.get("rating_min")
            if filters.get("rating_min") is not None
            else "",
        ),
        (
            "rating_max",
            filters.get("rating_max")
            if filters.get("rating_max") is not None
            else "",
        ),
        *[
            ("genre", value)
            for value in sorted(filters.get("genres") or [], key=str.casefold)
        ],
        *[
            ("exclude_genre", value)
            for value in sorted(
                filters.get("excluded_genres") or [],
                key=str.casefold,
            )
        ],
    ]
    return hashlib.sha256(
        urlencode(values).encode(),
    ).hexdigest()[:20]


def _log_studio_profile(studio_id, provider, cache_state, started_at):
    logger.info(
        "anime_studio_profile studio_id=%s provider=%s cache=%s elapsed_ms=%s",
        studio_id,
        provider,
        cache_state,
        int((time.monotonic() - started_at) * 1000),
    )


def _log_studio_catalog(studio_id, payload, cache_state, started_at):
    logger.info(
        "anime_studio_catalog studio_id=%s provider=%s cache=%s page=%s "
        "returned=%s role_filtered=%s elapsed_ms=%s",
        studio_id,
        payload.get("provider"),
        cache_state,
        payload.get("page"),
        len(payload.get("results") or []),
        payload.get("role_filtered") or 0,
        int((time.monotonic() - started_at) * 1000),
    )


def person_page(
    person_id,
    *,
    timeout=None,
    retry_rate_limits=True,
    request_session=None,
):
    """Return MAL person data through Jikan, which exposes MAL's people pages."""
    cache_key = f"{Sources.MAL.value}_person_{person_id}_v2"
    data = cache.get(cache_key)
    if data is not None:
        return data

    try:
        response = services.api_request(
            Sources.MAL.value,
            "GET",
            f"{jikan_base_url}/people/{person_id}/full",
            timeout=timeout,
            retry_rate_limits=retry_rate_limits,
            request_session=request_session,
        )
    except requests.exceptions.HTTPError as error:
        handle_error(error)

    person = response.get("data") or {}
    if not person:
        services.raise_not_found_error(Sources.MAL.value, person_id, "person")

    credits = []
    positions = {}
    for entry in person.get("manga") or []:
        manga_data = entry.get("manga") or {}
        role = str(entry.get("position") or "").strip() or "Author"
        _merge_jikan_person_credit(
            credits,
            positions,
            manga_data,
            media_type=MediaTypes.MANGA.value,
            role=role,
        )
    for entry in person.get("anime") or []:
        _merge_jikan_person_credit(
            credits,
            positions,
            entry.get("anime") or {},
            media_type=MediaTypes.ANIME.value,
            role=str(entry.get("position") or "").strip() or "Staff",
        )
    for entry in person.get("voices") or []:
        _merge_jikan_person_credit(
            credits,
            positions,
            entry.get("anime") or {},
            media_type=MediaTypes.ANIME.value,
            role="Voice Actor",
        )

    profile_images = person.get("images") or {}
    profile_image = profile_images.get("jpg") or profile_images.get("webp") or {}
    data = {
        "source": Sources.MAL.value,
        "person_id": str(person.get("mal_id") or person_id),
        "name": person.get("name") or "",
        "alternative_names": _jikan_person_alternative_names(person),
        "image": profile_image.get("image_url") or profile_image.get("large_image_url"),
        "biography": helpers.plain_text(person.get("about")),
        "known_for_department": _jikan_known_for_department(credits),
        "birth_date": person.get("birthday"),
        "death_date": None,
        "place_of_birth": None,
        "popularity": person.get("favorites"),
        "credits": credits,
    }
    cache.set(cache_key, data, PERSON_TTL)
    return data


def person_page_by_name(
    name,
    alternative_names=None,
    birth_date=None,
    *,
    timeout=1,
    request_session=None,
    strict=False,
):
    """Find the matching MAL person and return their full Jikan profile."""
    if not str(name or "").strip():
        return None
    response = services.api_request(
        Sources.MAL.value,
        "GET",
        f"{jikan_base_url}/people",
        params={
            "q": name,
            "limit": 10,
            "order_by": "favorites",
            "sort": "desc",
        },
        timeout=timeout,
        retry_rate_limits=False,
        request_session=request_session,
    )
    target_names = _person_name_keys([name, *(alternative_names or [])])
    candidates = [
        candidate
        for candidate in response.get("data") or []
        if target_names
        & _person_name_keys(
            [
                candidate.get("name"),
                candidate.get("given_name"),
                candidate.get("family_name"),
                *(candidate.get("alternate_names") or []),
            ],
        )
    ]
    if not candidates:
        return None
    birthday = str(birth_date or "")[:10]
    if strict:
        strict_matches = (
            [
                candidate
                for candidate in candidates
                if str(candidate.get("birthday") or "")[:10] == birthday
            ]
            if birthday
            else candidates
        )
        if len(strict_matches) != 1:
            return None
        match = strict_matches[0]
    else:
        match = max(
            candidates,
            key=lambda candidate: (
                bool(
                    birthday
                    and str(candidate.get("birthday") or "")[:10] == birthday
                ),
                candidate.get("favorites") or 0,
            ),
        )
    person_id = match.get("mal_id")
    return (
        person_page(
            person_id,
            timeout=timeout,
            retry_rate_limits=False,
            request_session=request_session,
        )
        if person_id
        else None
    )


def _person_name_keys(values):
    keys = set()
    for value in values:
        normalized = _normalize_match_title(value)
        if not normalized:
            continue
        keys.add(normalized)
        words = normalized.split()
        if len(words) > 1 and all(word.isascii() for word in words):
            keys.add(" ".join(sorted(words)))
    return keys


def _merge_jikan_person_credit(
    credits,
    positions,
    media,
    *,
    media_type,
    role,
):
    media_id = media.get("mal_id")
    if not media_id:
        return
    key = (media_type, str(media_id))
    if key in positions:
        current = credits[positions[key]]
        if role not in current["credit_roles"]:
            current["roles"].append(role)
            current["credit_roles"].append(role)
        return

    images = media.get("images") or {}
    image = images.get("jpg") or images.get("webp") or {}
    positions[key] = len(credits)
    credits.append({
        "media_type": media_type,
        "source": Sources.MAL.value,
        "media_id": str(media_id),
        "title": media.get("title_english") or media.get("title") or "",
        "display_title": media.get("title_english") or media.get("title") or "",
        "image": image.get("large_image_url") or image.get("image_url"),
        "roles": [role],
        "credit_roles": [role],
        "url": media.get("url")
        or f"https://myanimelist.net/{media_type}/{media_id}",
    })


def _jikan_person_alternative_names(person):
    values = [
        person.get("given_name"),
        person.get("family_name"),
        *(person.get("alternate_names") or []),
    ]
    primary = str(person.get("name") or "").strip().casefold()
    seen = {primary} if primary else set()
    results = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            results.append(clean)
    return results


def _jikan_known_for_department(credits):
    roles = [
        role
        for credit in credits
        for role in credit.get("credit_roles") or []
    ]
    if "Voice Actor" in roles:
        return "Voice Actor"
    return roles[0] if roles else None


def _match_titles(primary, alternatives=None):
    values = [primary]
    pending = (
        list(alternatives.values())
        if isinstance(alternatives, dict)
        else list(alternatives or [])
    )
    while pending:
        value = pending.pop(0)
        if isinstance(value, (list, tuple, set)):
            pending[:0] = value
        else:
            values.append(value)
    results = []
    seen = set()
    for value in values:
        normalized = _normalize_match_title(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            results.append(normalized)
    return results


def _normalize_match_title(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def _year(value):
    match = re.search(r"\b(\d{4})\b", str(value or ""))
    return int(match.group(1)) if match else None


def _format_family(value):
    normalized = str(value or "").casefold().replace("_", " ").strip()
    if not normalized:
        return None
    if "novel" in normalized:
        return "novel"
    if "one shot" in normalized or "oneshot" in normalized:
        return "one-shot"
    if any(
        label in normalized
        for label in ("manga", "manhwa", "manhua", "doujin", "oel", "webtoon", "comic")
    ):
        return "comic"
    return None


def get_readable_status(response):
    """Return the status in human-readable format."""
    # Map status to human-readable values
    status_map = {
        "finished_airing": "Finished",
        "currently_airing": "Airing",
        "not_yet_aired": "Upcoming",
        "finished": "Finished",
        "currently_publishing": "Publishing",
        "not_yet_published": "Upcoming",
        "on_hiatus": "On Hiatus",
        "discontinued": "Discontinued",
    }
    if response["status"] in status_map:
        return status_map[response["status"]]
    return response["status"].replace("_", " ").title()


def get_synopsis(response):
    """Add the synopsis to the response."""
    # when no synopsis, value from response is empty string
    # e.g manga: 160219
    if response["synopsis"] == "":
        return "No synopsis available."
    return response["synopsis"]


def get_number_of_episodes(response):
    """Return the number of episodes for the media."""
    # when unknown episodes, value from response is 0
    # e.g manga: 160219
    try:
        episodes = response["num_episodes"]
    except KeyError:
        episodes = response["num_chapters"]

    return episodes if episodes != 0 else None


def get_runtime(response):
    """Return the average episode duration."""
    # when unknown duration, value from response is 0
    # e.g anime: 43333
    duration = response["average_episode_duration"]

    # Convert average_episode_duration to hours and minutes
    if duration:
        # duration are in seconds
        hours, minutes = divmod(int(duration / 60), 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes} min"
    return None


def get_genres(response):
    """Return the genres for the media."""
    # when unknown genres, genres key is not present in the response
    # e.g manga: 151971
    if response.get("genres"):
        return [genre["name"] for genre in response["genres"]]
    return None


def get_studios(response):
    """Return the studios for the media."""
    # when unknown studio, studios is an empty list
    # e.g anime: 43333

    if response["studios"]:
        return [studio["name"] for studio in response["studios"]]
    return None


def get_studio_credits(response):
    """Return structured MAL studio identities without replacing legacy names."""
    credits = []
    seen = set()
    for studio in response.get("studios") or []:
        studio_id = studio.get("id")
        name = str(studio.get("name") or "").strip()
        if studio_id is None or not name or str(studio_id) in seen:
            continue
        seen.add(str(studio_id))
        credits.append(
            {
                "id": str(studio_id),
                "source": Sources.MAL.value,
                "name": name,
                "roles": ["Studio"],
            },
        )
    return credits


def cached_studio_identity(studio_id):
    """Return a recently observed official MAL studio identity."""
    return cache.get(_studio_identity_key(studio_id))


def _cache_studio_identities(credits, media_id):
    for credit in credits:
        studio_id = credit["id"]
        current = cached_studio_identity(studio_id) or {}
        cache.set(
            _studio_identity_key(studio_id),
            {
                "id": studio_id,
                "name": credit["name"],
                "seed_anime_id": str(
                    current.get("seed_anime_id") or media_id,
                ),
            },
            STUDIO_IDENTITY_TTL,
        )


def _studio_identity_key(studio_id):
    return (
        f"mal:{STUDIO_CACHE_VERSION}:studio:{studio_id}:identity"
    )


def get_season(response):
    """Return the season for the media."""
    # when unknown start season, no start_season key in response
    # e.g anime: 43333
    try:
        season = response["start_season"]
        return f"{season['season'].title()} {season['year']}"
    except KeyError:
        return None


def get_broadcast(response):
    """Return the broadcast day and time for the media."""
    start_date = response.get("start_date")
    if not start_date:
        return None

    # when unknown broadcast, value is not present in the response
    # e.g anime: 38869
    broadcast = response.get("broadcast")
    if not broadcast:
        return None

    # when unknown start time, value is not present in the broadcast dict
    start_time = broadcast.get("start_time") if broadcast else None
    if not start_time:
        return None

    japan_timezone = ZoneInfo("Asia/Tokyo")
    # Try parsing with different date formats
    try:
        date_obj = datetime.strptime(start_date, "%Y-%m-%d").replace(
            tzinfo=japan_timezone,
        )
    except ValueError:
        date_obj = datetime.strptime(start_date, "%Y-%m").replace(tzinfo=japan_timezone)

    broadcast_time_japan = datetime.strptime(
        f"{date_obj.strftime('%Y-%m-%d')} {start_time}",
        "%Y-%m-%d %H:%M",
    ).replace(tzinfo=japan_timezone)

    broadcast_time_local = broadcast_time_japan.astimezone(settings.TZ)
    return broadcast_time_local.strftime("%A %H:%M")


def get_source(response):
    """Return the source for the media."""
    # when unknown source, value from response is empty string
    # e.g anime: 32253
    try:
        return response["source"].replace("_", " ").title()
    except KeyError:
        return None


def get_score(response):
    """Return the score for the media."""
    # when num_scoring_users is small, the response does not include this field.
    try:
        return round(response["mean"], 1)
    except KeyError:
        return None


def get_score_count(response):
    """Return the score count for the media."""
    if get_score(response):
        return response["num_scoring_users"]
    return 0


def get_related(related_medias, media_type):
    """Return list of related media for the selected media."""
    if related_medias:
        results = [
            {
                "media_id": media["node"]["id"],
                "source": Sources.MAL.value,
                "title": media["node"]["title"],
                "media_type": media_type,
                "image": get_image_url(media["node"]),
                **({"relation": relation} if (relation := _mal_relation(media)) else {}),
            }
            for media in related_medias
        ]
        if media_type == MediaTypes.ANIME.value:
            for result in results:
                _cache_anime_poster(result["media_id"], result["image"])
        elif media_type == MediaTypes.MANGA.value:
            for result in results:
                _cache_manga_poster(result["media_id"], result["image"])
        return results
    return []


def _mal_relation(media):
    value = media.get("relation_type_formatted") or media.get("relation_type")
    if not value:
        return None
    return str(value).replace("_", " ").strip().title() or None


def _anime_format(value):
    normalized = str(value or "").replace("_", " ").strip()
    return normalized.upper() if normalized in {"tv", "ova", "ona"} else normalized.title()
