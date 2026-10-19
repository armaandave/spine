import asyncio
import hashlib
import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from functools import partial
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import uuid4

import requests
from aiohttp import ClientError
from django.apps import apps
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_date
from django_redis.exceptions import ConnectionInterrupted
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import LockError, RedisError

from api.serializers.common import (
    absolute_url,
    cast_from_metadata,
    crew_from_metadata,
    custom_backdrop_url_for_user,
    custom_poster_url_for_user,
    details_for_api,
    episodes_from_metadata,
    find_item,
    get_or_create_item_from_metadata,
    media_summary_from_provider,
    prime_collection_items,
    related_sections_from_payload,
    seasons_from_metadata,
    synopsis_from_payload,
)
from api.services import completion as completion_service
from api.services.filters import (
    apply_person_credit_filters,
    person_rating_source,
    update_item_filter_metadata,
)
from app import config, single_weight
from app.external_ratings import (
    RATING_SOURCES,
    eligible_rating_sources,
    max_rating_value,
    rating_source_is_exposed,
    rating_sources_needing_refresh,
    refresh_external_ratings,
    source_label,
)
from app.external_ratings import (
    normalize_rating_url as _normalize_rating_url,
)
from app.external_ratings import (
    provider_rating_url as _provider_rating_url,
)
from app.external_ratings import (
    third_party_rating_url as _third_party_rating_url,
)
from app.models import (
    BookCreditOverride,
    CustomBackdropPreference,
    CustomLogoPreference,
    CustomPosterPreference,
    ExternalRating,
    Item,
    MediaTypes,
    Sources,
)
from app.providers import anilist, mal, musicbrainz
from app.providers import services as provider_services
from app.providers.search_rank import normalize_search_text, rank_mixed_results
from app.utils.color import build_accent_palette, compute_and_store_poster_accent

SEARCH_TTL = 60 * 60 * 6
SEARCH_CACHE_VERSION = "v3"
MUSIC_SEARCH_CACHE_VERSION = "v5"
MUSIC_CACHE_STALE_TTL = 60 * 60 * 24 * 7
MUSIC_CACHE_LOCK_TIMEOUT = 120
MUSIC_REFRESH_SCHEDULE_TTL = 60
ALL_MEDIA_SEARCH_TIMEOUT = 8
PEOPLE_SEARCH_TIMEOUT = 4
PEOPLE_SEARCH_PROVIDER_LIMIT = 10
ALL_MEDIA_CANDIDATE_STALE_TTL = 60 * 60 * 24 * 7
ALL_MEDIA_REFRESH_SCHEDULE_TTL = 60
DISCOVER_TTL = 60 * 60 * 6
DISCOVER_EMPTY_TTL = 60 * 5
DETAIL_TTL = 60 * 60 * 24
DETAIL_CACHE_VERSION = "v9"
BOOK_DETAIL_CACHE_VERSION = "v1"
MOVIE_DETAIL_CACHE_VERSION = "v1"
EPISODE_DETAIL_CACHE_VERSION = "v1"
MUSIC_DETAIL_CACHE_VERSION = "v3"
ANIME_DETAIL_CACHE_VERSION = "v6"
MANGA_DETAIL_CACHE_VERSION = "v1"
PERSON_PREPARATION_LOCK_TIMEOUT = 60 * 15
DETAIL_RATING_PREPARATION_LOCK_TIMEOUT = 60
PERSON_SORT_OPTIONS = [
    {"value": "popularity", "label": "Popularity"},
    {"value": "title", "label": "Title"},
    {"value": "release_date", "label": "Release Date"},
    {"value": "average_rating", "label": "Average Rating"},
]
COMPANY_SORTS = {"popularity", "release_date", "title", "average_rating"}
COMPANY_GAME_SORT_OPTIONS = [
    {"value": "popularity", "label": "Popularity"},
    {"value": "release_date", "label": "Release Date"},
    {"value": "average_rating", "label": "IGDB Rating"},
    {"value": "title", "label": "Title"},
]
COMPANY_ANIME_SORT_OPTIONS = [
    {"value": "popularity", "label": "Popularity"},
    {"value": "release_date", "label": "Release Date"},
    {"value": "average_rating", "label": "MAL Rating"},
    {"value": "title", "label": "Title"},
]
COMPANY_GAME_OPTIONS_CACHE_VERSION = "v1"
POSTER_UNSUPPORTED_MESSAGE = (
    "Poster customization is only available for TMDB movies/TV shows/seasons, MAL anime, MAL/MangaUpdates manga, Open Library/Hardcover books, IGDB games, and MusicBrainz music."
)
BACKDROP_UNSUPPORTED_MESSAGE = (
    "Backdrop customization is only available for TMDB movies/TV shows/seasons/episodes, MAL anime, and IGDB games."
)
LOGO_UNSUPPORTED_MESSAGE = "Logo customization is only available for TMDB movies/TV shows and IGDB games."
logger = logging.getLogger(__name__)


def default_source_for(media_type):
    """Return the configured default source value for a media type."""
    return config.get_default_source_name(media_type).value


def _search_cache_key(*, media_type, source, query, page, preserve_ranking_fields):
    query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:24]
    cache_version = (
        MUSIC_SEARCH_CACHE_VERSION
        if media_type == MediaTypes.MUSIC.value
        else SEARCH_CACHE_VERSION
    )
    rank_suffix = ":rank" if preserve_ranking_fields else ""
    return (
        f"api:{cache_version}:search:{media_type}:{source}:{query_hash}:"
        f"p{page}:u{getattr(settings, 'TMDB_LANG', 'en')}:nsfw{settings.TMDB_NSFW}{rank_suffix}"
    )


def _search_data(*, media_type, query, page=1, source=None, preserve_ranking_fields=False, timeout=None):
    source = source or default_source_for(media_type)
    cache_key = _search_cache_key(
        media_type=media_type,
        source=source,
        query=query,
        page=page,
        preserve_ranking_fields=preserve_ranking_fields,
    )

    def fetch():
        return provider_services.search(
            media_type,
            query,
            page,
            source,
            preserve_ranking_fields=preserve_ranking_fields,
            timeout=timeout,
        )

    if media_type == MediaTypes.MUSIC.value:
        data = _music_cache_data(
            cache_key,
            SEARCH_TTL,
            fetch,
            lambda: _schedule_music_refresh(
                cache_key,
                "refresh_music_search",
                media_type,
                query,
                page,
                source,
                preserve_ranking_fields,
            ),
        )
    else:
        data = cache.get(cache_key)
        if data is None:
            data = fetch()
            cache.set(cache_key, data, SEARCH_TTL)
    return source, data


def _cache_is_fresh(cache_key, fresh_ttl):
    validation = cache.get(f"{cache_key}:validation")
    try:
        return time.time() - float(validation["validated_at"]) <= fresh_ttl
    except (KeyError, TypeError, ValueError):
        return False


def _candidate_is_fresh(cache_key):
    return _cache_is_fresh(cache_key, SEARCH_TTL)


def _cache_candidate(cache_key, data):
    cache.set(cache_key, data, ALL_MEDIA_CANDIDATE_STALE_TTL)
    cache.set(
        f"{cache_key}:validation",
        {"validated_at": time.time()},
        ALL_MEDIA_CANDIDATE_STALE_TTL,
    )


def _cache_music_data(cache_key, data):
    cache.set(cache_key, data, MUSIC_CACHE_STALE_TTL)
    cache.set(
        f"{cache_key}:validation",
        {"validated_at": time.time()},
        MUSIC_CACHE_STALE_TTL,
    )


def _schedule_music_refresh(cache_key, task_name, *args):
    schedule_key = f"{cache_key}:refresh-scheduled"
    try:
        if not cache.add(schedule_key, 1, timeout=MUSIC_REFRESH_SCHEDULE_TTL):
            return False
        from app import tasks

        getattr(tasks, task_name).delay(*args)
    except (
        KombuOperationalError,
        ConnectionInterrupted,
        RedisError,
        ConnectionError,
        OSError,
    ) as error:
        with suppress(ConnectionInterrupted, RedisError, ConnectionError, OSError):
            cache.delete(schedule_key)
        logger.warning("Music refresh enqueue failed task=%s type=%s", task_name, type(error).__name__)
        return False
    return True


def _music_cache_data(cache_key, fresh_ttl, fetch, schedule_refresh):
    data = cache.get(cache_key)
    if data is not None:
        if not _cache_is_fresh(cache_key, fresh_ttl):
            schedule_refresh()
        return data

    lock = _candidate_lock(cache_key, MUSIC_CACHE_LOCK_TIMEOUT)
    if lock is None:
        data = fetch()
        _cache_music_data(cache_key, data)
        return data

    acquired = lock.acquire(blocking=True, blocking_timeout=MUSIC_CACHE_LOCK_TIMEOUT)
    if not acquired:
        raise TimeoutError("music cache single-flight deadline exceeded")
    try:
        data = cache.get(cache_key)
        if data is None:
            data = fetch()
            _cache_music_data(cache_key, data)
        return data
    finally:
        with suppress(LockError, RedisError, ConnectionError, OSError):
            lock.release()


def _refresh_music_cache(cache_key, fresh_ttl, fetch):
    lock = _candidate_lock(cache_key, MUSIC_CACHE_LOCK_TIMEOUT)
    acquired = False
    try:
        if cache.get(cache_key) is not None and _cache_is_fresh(cache_key, fresh_ttl):
            return {"outcome": "skipped_fresh"}
        if lock is not None:
            acquired = lock.acquire(blocking=False)
            if not acquired:
                return {"outcome": "deduplicated"}
            if cache.get(cache_key) is not None and _cache_is_fresh(cache_key, fresh_ttl):
                return {"outcome": "skipped_fresh"}
        _cache_music_data(cache_key, fetch())
    except Exception as error:  # noqa: BLE001 - keep stale music data on refresh failures
        logger.warning("Music cache refresh failed type=%s", type(error).__name__)
        return {"outcome": "failed", "exception_type": type(error).__name__}
    else:
        return {"outcome": "refreshed"}
    finally:
        cache.delete(f"{cache_key}:refresh-scheduled")
        if acquired:
            with suppress(LockError, RedisError, ConnectionError, OSError):
                lock.release()


def refresh_music_search(*, media_type, query, page, source, preserve_ranking_fields=False):
    """Refresh one stale music search result in a Celery worker."""
    cache_key = _search_cache_key(
        media_type=media_type,
        source=source,
        query=query,
        page=page,
        preserve_ranking_fields=preserve_ranking_fields,
    )
    return _refresh_music_cache(
        cache_key,
        SEARCH_TTL,
        lambda: provider_services.search(
            media_type,
            query,
            page,
            source,
            preserve_ranking_fields=preserve_ranking_fields,
            timeout=None,
        ),
    )


def _fetch_candidate(
    *,
    media_type,
    query,
    page,
    source,
    timeout,
    cache_key,
    search_metrics=None,
):
    started_at = time.monotonic()
    try:
        data = provider_services.search(
            media_type,
            query,
            page,
            source,
            preserve_ranking_fields=True,
            timeout=timeout,
        )
    finally:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        if search_metrics is not None:
            search_metrics["provider_elapsed_ms"] = elapsed_ms
    _cache_candidate(cache_key, data)
    return data, elapsed_ms


def _candidate_lock(cache_key, timeout):
    lock_factory = getattr(cache, "lock", None)
    if lock_factory is None:
        return None
    return lock_factory(
        f"{cache_key}:flight",
        timeout=timeout + 2,
        sleep=0.05,
    )


def _schedule_candidate_refresh(*, media_type, query, page, source, cache_key):
    schedule_key = f"{cache_key}:refresh-scheduled"
    try:
        if not cache.add(schedule_key, 1, timeout=ALL_MEDIA_REFRESH_SCHEDULE_TTL):
            return False

        from app.tasks import refresh_all_media_search_candidate

        refresh_all_media_search_candidate.delay(media_type, query, page, source)
    except (
        KombuOperationalError,
        ConnectionInterrupted,
        RedisError,
        ConnectionError,
        OSError,
    ) as error:
        with suppress(ConnectionInterrupted, RedisError, ConnectionError, OSError):
            cache.delete(schedule_key)
        logger.warning(
            "All-media candidate refresh enqueue failed query=%s media_type=%s type=%s",
            hashlib.sha256(query.strip().lower().encode()).hexdigest()[:24],
            media_type,
            type(error).__name__,
        )
        return False
    return True


def _candidate_data(*, media_type, query, page, source, timeout, search_metrics=None):
    cache_key = _search_cache_key(
        media_type=media_type,
        source=source,
        query=query,
        page=page,
        preserve_ranking_fields=True,
    )
    data = cache.get(cache_key)
    if data is not None:
        if _candidate_is_fresh(cache_key):
            return data, "fresh", None, False
        scheduled = _schedule_candidate_refresh(
            media_type=media_type,
            query=query,
            page=page,
            source=source,
            cache_key=cache_key,
        )
        return data, "stale", None, scheduled

    lock = _candidate_lock(cache_key, timeout)
    if lock is None:
        if search_metrics is not None:
            search_metrics["cache_status"] = "bypass"
        data, provider_elapsed_ms = _fetch_candidate(
            media_type=media_type,
            query=query,
            page=page,
            source=source,
            timeout=timeout,
            cache_key=cache_key,
            search_metrics=search_metrics,
        )
        return data, "bypass", provider_elapsed_ms, False

    acquired = lock.acquire(blocking=True, blocking_timeout=timeout)
    if not acquired:
        raise TimeoutError("candidate single-flight deadline exceeded")
    try:
        data = cache.get(cache_key)
        if data is not None:
            return data, "coalesced", None, False
        data, provider_elapsed_ms = _fetch_candidate(
            media_type=media_type,
            query=query,
            page=page,
            source=source,
            timeout=timeout,
            cache_key=cache_key,
            search_metrics=search_metrics,
        )
        return data, "miss", provider_elapsed_ms, False
    finally:
        with suppress(LockError, RedisError, ConnectionError, OSError):
            lock.release()


def _candidate_worker(*, media_type, query, source, search_metrics):
    started_at = time.monotonic()
    data = None
    try:
        data, cache_status, provider_elapsed_ms, refresh_scheduled = _candidate_data(
            media_type=media_type,
            query=query,
            page=1,
            source=source,
            timeout=ALL_MEDIA_SEARCH_TIMEOUT,
            search_metrics=search_metrics,
        )
        search_metrics.update(
            cache_status=cache_status,
            cache_hit=cache_status in {"fresh", "stale", "coalesced"},
            outcome="success",
            provider_elapsed_ms=provider_elapsed_ms,
            refresh_scheduled=refresh_scheduled,
            result_count=len(data.get("results") or data.get("items") or []),
        )
    except TimeoutError:
        search_metrics.update(
            outcome="single_flight_timeout",
            exception_type="TimeoutError",
        )
    except Exception as error:  # noqa: BLE001 - one provider must not discard other results
        search_metrics.update(
            outcome="provider_exception",
            exception_type=type(error).__name__,
        )
    finally:
        search_metrics["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
    return source, data


def refresh_all_media_candidate(*, media_type, query, page, source):
    """Refresh one stale rank-preserving candidate set in a Celery worker."""
    started_at = time.monotonic()
    query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:24]
    cache_key = _search_cache_key(
        media_type=media_type,
        source=source,
        query=query,
        page=page,
        preserve_ranking_fields=True,
    )
    summary = {
        "candidate_key_hash": hashlib.sha256(cache_key.encode()).hexdigest()[:24],
        "query_hash": query_hash,
        "media_type": media_type,
        "source": source,
        "outcome": "pending",
        "elapsed_ms": 0,
        "provider_elapsed_ms": None,
        "result_count": 0,
    }
    lock = None
    acquired = False
    try:
        data = cache.get(cache_key)
        if data is not None and _candidate_is_fresh(cache_key):
            summary.update(
                outcome="skipped_fresh",
                result_count=len(data.get("results") or data.get("items") or []),
            )
            cache.delete(f"{cache_key}:refresh-scheduled")
            return summary

        lock = _candidate_lock(cache_key, ALL_MEDIA_SEARCH_TIMEOUT)
        if lock is not None:
            acquired = lock.acquire(blocking=False)
            if not acquired:
                summary["outcome"] = "deduplicated"
                return summary

            data = cache.get(cache_key)
            if data is not None and _candidate_is_fresh(cache_key):
                summary.update(
                    outcome="skipped_fresh",
                    result_count=len(data.get("results") or data.get("items") or []),
                )
                cache.delete(f"{cache_key}:refresh-scheduled")
                return summary

        data, provider_elapsed_ms = _fetch_candidate(
            media_type=media_type,
            query=query,
            page=page,
            source=source,
            timeout=ALL_MEDIA_SEARCH_TIMEOUT,
            cache_key=cache_key,
            search_metrics=summary,
        )
        summary.update(
            outcome="refreshed",
            provider_elapsed_ms=provider_elapsed_ms,
            result_count=len(data.get("results") or data.get("items") or []),
        )
        cache.delete(f"{cache_key}:refresh-scheduled")
    except Exception as error:  # noqa: BLE001 - stale candidates must survive refresh failures
        summary.update(
            outcome="failed",
            exception_type=type(error).__name__,
        )
    finally:
        if acquired:
            with suppress(LockError, RedisError, ConnectionError, OSError):
                lock.release()
        summary["elapsed_ms"] = int((time.monotonic() - started_at) * 1000)
        log = (
            logger.info
            if summary["outcome"] in {"refreshed", "skipped_fresh", "deduplicated"}
            else logger.warning
        )
        log(
            "all_media_candidate_refresh_summary %s",
            json.dumps(summary, sort_keys=True),
        )
    return summary


def search_media(*, media_type, query, page=1, source=None, request=None, user=None):
    """Search provider metadata with a versioned cache key."""
    source, data = _search_data(
        media_type=media_type,
        query=query,
        page=page,
        source=source,
    )

    raw_results = data.get("results") or data.get("items") or []
    return [
        media_summary_from_provider(
            item,
            media_type=item.get("media_type", media_type),
            source=item.get("source", source),
            request=request,
            user=user,
        )
        for item in raw_results
    ]


def search_all_media(*, media_types, query, request=None, user=None):
    """Search enabled providers concurrently and return one ranked result list."""
    started_at = time.monotonic()
    query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:24]
    search_id = uuid4().hex[:12]
    provider_metrics = {
        media_type: {
            "search_id": search_id,
            "query_hash": query_hash,
            "media_type": media_type,
            "source": default_source_for(media_type),
            "cache_status": "miss",
            "cache_hit": False,
            "outcome": "pending",
            "elapsed_ms": 0,
            "provider_elapsed_ms": None,
            "result_count": 0,
            "refresh_scheduled": False,
        }
        for media_type in media_types
    }
    executor = ThreadPoolExecutor(max_workers=len(media_types))
    futures = {
        media_type: executor.submit(
            _candidate_worker,
            media_type=media_type,
            query=query,
            source=provider_metrics[media_type]["source"],
            search_metrics=provider_metrics[media_type],
        )
        for media_type in media_types
    }
    done, pending = wait(futures.values(), timeout=ALL_MEDIA_SEARCH_TIMEOUT)
    completed = []
    unavailable = []
    candidates = []
    reported_metrics = []

    for media_type in media_types:
        future = futures[media_type]
        if future not in done:
            unavailable.append(media_type)
            future.cancel()
            metrics = provider_metrics[media_type]
            metrics.update(
                outcome="aggregate_deadline_timeout",
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            logger.warning(
                "All-media search provider unavailable search_id=%s query=%s media_type=%s "
                "reason=timeout deadline_seconds=%s",
                search_id,
                query_hash,
                media_type,
                ALL_MEDIA_SEARCH_TIMEOUT,
            )
            logger.warning(
                "all_media_search_provider_summary %s",
                json.dumps(metrics, sort_keys=True),
            )
            reported_metrics.append(dict(metrics))
            continue
        source, data = future.result()
        metrics = provider_metrics[media_type]
        if data is None:
            unavailable.append(media_type)
            logger.warning(
                "All-media search provider unavailable search_id=%s query=%s media_type=%s "
                "reason=%s exception_type=%s",
                search_id,
                query_hash,
                media_type,
                metrics["outcome"],
                metrics.get("exception_type"),
            )
            logger.warning(
                "all_media_search_provider_summary %s",
                json.dumps(metrics, sort_keys=True),
            )
            reported_metrics.append(dict(metrics))
            continue

        logger.info(
            "all_media_search_provider_summary %s",
            json.dumps(metrics, sort_keys=True),
        )
        reported_metrics.append(dict(metrics))

        completed.append(media_type)
        raw_results = data.get("results") or data.get("items") or []
        for provider_rank, item in enumerate(raw_results):
            candidate = dict(item)
            candidate.setdefault("media_type", media_type)
            candidate.setdefault("source", source)
            candidates.append((provider_rank, candidate))

    for future in pending:
        future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)

    ranked = rank_mixed_results(query, candidates, limit=settings.PER_PAGE)
    results = [
        media_summary_from_provider(
            item,
            media_type=item["media_type"],
            source=item["source"],
            request=request,
            user=user,
        )
        for item in ranked
    ]
    cache_status_counts = Counter(metrics["cache_status"] for metrics in reported_metrics)
    logger.info(
        "all_media_search_summary %s",
        json.dumps(
            {
                "search_id": search_id,
                "query_hash": query_hash,
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                "cache_status_counts": dict(cache_status_counts),
                "completed_media_types": completed,
                "unavailable_media_types": unavailable,
            },
            sort_keys=True,
        ),
    )
    return {
        "results": results,
        "completed_media_types": completed,
        "unavailable_media_types": unavailable,
    }


def search_people(*, query):
    """Search every supported person provider with one response deadline."""
    started_at = time.monotonic()
    normalized_query = normalize_search_text(query)
    query_hash = hashlib.sha256(normalized_query.encode()).hexdigest()[:24]
    sources = provider_services.SUPPORTED_PERSON_SOURCES
    executor = ThreadPoolExecutor(max_workers=len(sources))
    futures = {
        source: executor.submit(
            provider_services.search_people,
            source,
            query,
            limit=PEOPLE_SEARCH_PROVIDER_LIMIT,
            timeout=PEOPLE_SEARCH_TIMEOUT - 1,
        )
        for source in sources
    }
    done, pending = wait(futures.values(), timeout=PEOPLE_SEARCH_TIMEOUT)
    completed = []
    unavailable = []
    candidates = []
    seen = set()

    for source_index, source in enumerate(sources):
        future = futures[source]
        if future not in done:
            unavailable.append(source)
            future.cancel()
            continue
        try:
            provider_results = future.result()
        except Exception as error:  # noqa: BLE001 - one provider must not discard the others
            unavailable.append(source)
            logger.warning(
                "people_search_provider_unavailable query=%s source=%s type=%s",
                query_hash,
                source,
                type(error).__name__,
            )
            continue

        completed.append(source)
        for provider_rank, person in enumerate(provider_results):
            person_id = str(person.get("person_id") or "").strip()
            name = str(person.get("name") or "").strip()
            identity = (source, person_id)
            if not person_id or not name or identity in seen:
                continue
            seen.add(identity)
            normalized_name = normalize_search_text(name)
            match_rank = (
                0
                if normalized_name == normalized_query
                else 1
                if normalized_name.startswith(normalized_query)
                else 2
                if normalized_query in normalized_name
                else 3
            )
            candidates.append((
                match_rank,
                provider_rank,
                source_index,
                normalized_name,
                {
                    "ref": {"source": source, "id": person_id},
                    "name": name,
                    "profile_url": (
                        None
                        if person.get("profile_url") == settings.IMG_NONE
                        else person.get("profile_url") or None
                    ),
                    "known_for_department": (
                        str(person["known_for_department"]).strip()
                        if person.get("known_for_department")
                        else None
                    ),
                },
            ))

    for future in pending:
        future.cancel()
    executor.shutdown(wait=False, cancel_futures=True)
    candidates.sort(key=lambda candidate: candidate[:4])
    logger.info(
        "people_search_summary query=%s completed=%s unavailable=%s elapsed_ms=%s",
        query_hash,
        len(completed),
        len(unavailable),
        int((time.monotonic() - started_at) * 1000),
    )
    return {
        "results": [
            candidate[4]
            for candidate in candidates[: settings.PER_PAGE]
        ],
        "completed_sources": completed,
        "unavailable_sources": unavailable,
    }


def discover_media(
    *,
    media_type,
    page=1,
    page_size=None,
    source=None,
    genre=None,
    year=None,
    platform=None,
    sort="vote_count",
    request=None,
    user=None,
):
    """Discover provider metadata with a versioned cache key."""
    source = source or default_source_for(media_type)
    filters = urlencode(
        {
            "genre": genre or "",
            "year": year or "",
            "platform": platform or "",
            "sort": sort,
            "page_size": page_size or "",
        },
    )
    filters_hash = hashlib.sha256(filters.encode()).hexdigest()[:24]
    cache_key = f"api:v1:discover:{media_type}:{source}:{filters_hash}:p{page}"
    data = cache.get(cache_key)
    if data is None:
        data = provider_services.discover(
            media_type,
            source=source,
            page=page,
            page_size=page_size,
            genre=genre,
            year=year,
            platform=platform,
            sort=sort,
        )
        empty_mal_genre = (
            source == Sources.MAL.value
            and media_type in {MediaTypes.ANIME.value, MediaTypes.MANGA.value}
            and genre
            and not (data.get("results") or data.get("items"))
        )
        cache.set(
            cache_key,
            data,
            DISCOVER_EMPTY_TTL if empty_mal_genre else DISCOVER_TTL,
        )

    raw_results = data.get("results") or data.get("items") or []
    return {
        "count": data.get("total_results", len(raw_results)),
        "page_size": data.get("per_page") or page_size or len(raw_results),
        "results": [
            media_summary_from_provider(
                item,
                media_type=item.get("media_type", media_type),
                source=item.get("source", source),
                request=request,
                user=user,
            )
            for item in raw_results
        ],
    }


def _external_rating_item(ref, metadata):
    item = find_item(ref)
    if item is not None:
        update_item_filter_metadata(item, metadata)
        return item
    candidate = Item(
        source=ref["source"],
        media_type=ref["media_type"],
        media_id=ref["media_id"],
    )
    if not eligible_rating_sources(candidate):
        return None
    return get_or_create_item_from_metadata(ref, metadata)


def _detail_cache_key(
    *,
    source,
    media_type,
    media_id,
    season_number,
    episode_number,
    include_music_enrichment,
):
    cache_version = DETAIL_CACHE_VERSION
    if media_type == MediaTypes.EPISODE.value:
        cache_version = f"{cache_version}:episode-{EPISODE_DETAIL_CACHE_VERSION}"
    elif media_type == MediaTypes.BOOK.value:
        cache_version = f"{cache_version}:book-{BOOK_DETAIL_CACHE_VERSION}"
    elif media_type == MediaTypes.MOVIE.value:
        cache_version = f"{cache_version}:movie-{MOVIE_DETAIL_CACHE_VERSION}"
    elif media_type == MediaTypes.MUSIC.value:
        stage = "enriched" if include_music_enrichment else "basic"
        cache_version = f"{cache_version}:music-{MUSIC_DETAIL_CACHE_VERSION}:{stage}"
    elif media_type == MediaTypes.ANIME.value:
        cache_version = f"{cache_version}:anime-{ANIME_DETAIL_CACHE_VERSION}"
    elif media_type == MediaTypes.MANGA.value:
        cache_version = f"{cache_version}:manga-{MANGA_DETAIL_CACHE_VERSION}"
    return (
        f"api:{cache_version}:detail:{source}:{media_type}:{media_id}:"
        f"s{season_number}:e{episode_number}:u{getattr(settings, 'TMDB_LANG', 'en')}"
    )


def _music_detail_metadata(*, source, media_id, include_music_enrichment):
    return provider_services.get_media_metadata(
        MediaTypes.MUSIC.value,
        media_id,
        source,
        include_music_enrichment=include_music_enrichment,
    )


def refresh_music_detail(*, source, media_id, include_music_enrichment):
    """Refresh one stale music detail entry in a Celery worker."""
    cache_key = _detail_cache_key(
        source=source,
        media_type=MediaTypes.MUSIC.value,
        media_id=media_id,
        season_number=None,
        episode_number=None,
        include_music_enrichment=include_music_enrichment,
    )
    return _refresh_music_cache(
        cache_key,
        DETAIL_TTL,
        lambda: _music_detail_metadata(
            source=source,
            media_id=media_id,
            include_music_enrichment=include_music_enrichment,
        ),
    )


def media_detail(
    *,
    source,
    media_type,
    media_id,
    request=None,
    user=None,
    season_number=None,
    episode_number=None,
    include_music_enrichment=True,
):
    """Fetch provider metadata and normalize it for the API."""
    if media_type == MediaTypes.EPISODE.value and (
        season_number in (None, "") or episode_number in (None, "")
    ):
        raise ValueError("season_number and episode_number are required for episodes.")
    season_number = int(season_number) if season_number not in (None, "") else None
    episode_number = int(episode_number) if episode_number not in (None, "") else None
    cache_key = _detail_cache_key(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
        include_music_enrichment=include_music_enrichment,
    )

    if media_type == MediaTypes.MUSIC.value:
        metadata = _music_cache_data(
            cache_key,
            DETAIL_TTL,
            lambda: _music_detail_metadata(
                source=source,
                media_id=media_id,
                include_music_enrichment=include_music_enrichment,
            ),
            lambda: _schedule_music_refresh(
                cache_key,
                "refresh_music_detail",
                source,
                media_id,
                include_music_enrichment,
            ),
        )
    else:
        metadata = cache.get(cache_key)
        if metadata is None:
            season_numbers = [season_number] if season_number is not None else None
            metadata = provider_services.get_media_metadata(
                media_type,
                media_id,
                source,
                season_numbers,
                episode_number,
            )
            cache.set(cache_key, metadata, DETAIL_TTL)

    primary_metadata = metadata
    if media_type == MediaTypes.ANIME.value:
        metadata = _enrich_anime_metadata(metadata, source)
    elif media_type == MediaTypes.MANGA.value:
        metadata = _enrich_manga_metadata(metadata, source)

    summary = media_summary_from_provider(
        {
            **metadata,
            "media_id": media_id,
            "media_type": media_type,
            "source": source,
            "season_number": season_number,
            "episode_number": episode_number,
        },
        media_type,
        source,
        request=request,
        user=user,
    )
    synopsis = synopsis_from_payload(metadata) or summary.get("overview")
    if synopsis:
        summary["overview"] = synopsis
    ref = summary["ref"]
    item = _external_rating_item(ref, primary_metadata)
    from streaming import payloads as streaming_payloads
    from streaming import sync as streaming_sync

    # ST-400: refresh stale snapshots from the metadata already fetched.
    streaming_sync.sync_from_metadata(source, media_type, media_id, primary_metadata)
    if summary.get("poster_accent_color") is None:
        summary["poster_accent_color"] = poster_accent_color(metadata, ref)
    logo, custom_logo_url = resolved_logo(
        source=source,
        media_type=media_type,
        media_id=media_id,
        request=request,
        user=user,
    )
    default_backdrop_url, custom_backdrop_url = resolved_backdrop_urls(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
        metadata=metadata,
        request=request,
        user=user,
    )
    rating_payload = external_rating_payload(
        metadata=metadata,
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
        item=item,
    )
    _persist_detail_series(metadata, source=source, media_type=media_type)
    seasons = (
        seasons_from_metadata(
            metadata,
            request=request,
            user=user,
        )
        if media_type == MediaTypes.TV.value
        else []
    )
    enriched_episode_metadata = (
        enrich_episodes(metadata, source, user)
        if media_type == MediaTypes.SEASON.value
        else None
    )
    episodes = (
        episodes_from_metadata(enriched_episode_metadata, request=request)
        if enriched_episode_metadata is not None
        else []
    )
    related_sections = related_sections_from_payload(
        metadata.get("related", {}),
        media_type=media_type,
        source=source,
        request=request,
        user=user,
    )
    completion = None
    if user and user.is_authenticated and media_type == MediaTypes.TV.value:
        completion = completion_service.completion_payload(
            sum(
                (season.get("completion") or {}).get("completed_count", 0)
                for season in seasons
            ),
            sum(
                (season.get("completion") or {}).get("total_count", 0)
                for season in seasons
            ),
        )
    elif (
        user
        and user.is_authenticated
        and media_type == MediaTypes.SEASON.value
    ):
        raw_episodes = enriched_episode_metadata.get("episodes") or []
        completion = completion_service.completion_payload(
            sum(bool(episode.get("history")) for episode in raw_episodes),
            len(episodes),
        )
    return {
        **summary,
        "overview": synopsis,
        "synopsis": synopsis,
        "backdrop_url": default_backdrop_url,
        "logo_url": logo.get("url") if logo else None,
        "logo_width": logo.get("width") if logo else None,
        "logo_height": logo.get("height") if logo else None,
        "logo_aspect_ratio": logo.get("aspect_ratio") if logo else None,
        "custom_logo_url": custom_logo_url,
        "details": details_for_api(metadata),
        **({"music": metadata["music"]} if "music" in metadata else {}),
        "parent": metadata.get("parent"),
        "external_links": metadata.get("external_links", {}),
        "cast": cast_from_metadata(metadata, request=request),
        **(
            {
                "characters": cast_from_metadata(
                    {"cast": metadata.get("characters") or []},
                    request=request,
                ),
            }
            if media_type in {MediaTypes.ANIME.value, MediaTypes.MANGA.value}
            else {}
        ),
        "crew": crew_from_metadata(metadata, request=request),
        "seasons": seasons,
        "episodes": episodes,
        "completion": completion,
        "custom_backdrop_url": custom_backdrop_url,
        "custom_poster_url": custom_poster_url_for_user(user, ref, request=request),
        "related": metadata.get("related", {}),
        "related_sections": related_sections,
        "providers": watch_providers_for_user(metadata, user),
        "where_to_watch": streaming_payloads.where_to_watch(
            source=source,
            media_type=media_type,
            media_id=media_id,
            metadata=primary_metadata,
            user=user,
            request=request,
        ),
        "community": community_stats(
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
        ),
        **rating_payload,
    }


def _persist_detail_series(metadata, *, source, media_type):
    """Persist a complete canonical series already present in detail metadata."""
    details = metadata.get("details") or {}
    series_id = details.get("series_id")
    if not series_id:
        return None

    related = metadata.get("related") or {}
    if media_type == MediaTypes.ANIME.value:
        members = related.get("series") or []
    elif media_type == MediaTypes.GAME.value:
        members = related.get("collection") or []
    elif media_type == MediaTypes.BOOK.value:
        members = related.get(details.get("series_name")) or []
    elif media_type == MediaTypes.MOVIE.value:
        members = next(
            (
                values
                for key, values in related.items()
                if key not in {"recommendations", "similar"} and values
            ),
            [],
        )
    else:
        members = []

    return completion_service.persist_complete_series({
        "series_id": series_id,
        "source": details.get("series_source") or source,
        "media_type": details.get("series_media_type") or media_type,
        "name": details.get("series_name") or "",
        "item_count": len(members),
        "items": members,
    })


ANIME_RELATION_ORDER = {
    "Source": 0,
    "Adaptation": 1,
    "Prequel": 2,
    "Sequel": 3,
    "Parent Story": 4,
    "Side Story": 5,
    "Spin-off": 6,
    "Alternative": 7,
    "Summary": 8,
    "Compilation": 9,
    "Contains": 10,
    "Character": 11,
    "Other": 12,
}
ANIME_RELATION_LABELS = {
    "parent": "Parent Story",
    "parent story": "Parent Story",
    "side story": "Side Story",
    "spin off": "Spin-off",
    "spin-off": "Spin-off",
    "alternative setting": "Alternative",
    "alternative version": "Alternative",
    "full story": "Other",
}


def _enrich_anime_metadata(metadata, source):
    """Merge optional AniList fields into MAL anime metadata."""
    if source != Sources.MAL.value:
        return metadata
    enrichment = anilist.anime(metadata.get("media_id"))
    if not enrichment:
        fallback_cast = mal.anime_cast(metadata.get("media_id"))
        enriched = deepcopy(metadata)
        if fallback_cast:
            enriched["cast"] = fallback_cast
        return _add_anime_series(enriched)

    enriched = deepcopy(metadata)
    if not enriched.get("display_title"):
        enriched["display_title"] = enrichment.get("display_title")
    if not backdrop_url(enriched):
        enriched["backdrop"] = enrichment.get("backdrop")
    enriched["posters"] = _dedupe_artwork(
        [*(enriched.get("posters") or []), *(enrichment.get("posters") or [])],
    )
    enriched["backdrops"] = _dedupe_artwork(
        [*(enriched.get("backdrops") or []), *(enrichment.get("backdrops") or [])],
    )
    enriched["characters"] = enrichment.get("characters") or []
    enriched["cast"] = enrichment.get("cast") or []
    enriched["_anilist"] = enrichment
    enriched["details"] = {
        **(enriched.get("details") or {}),
        "anilist_rating": enrichment.get("rating_summary"),
    }

    external_links = dict(enriched.get("external_links") or {})
    if enrichment.get("source_url"):
        external_links["anilist"] = enrichment["source_url"]
    enriched["external_links"] = external_links

    related = dict(enriched.get("related") or {})
    related["relations"] = _merge_anime_relations(
        related.get("relations") or [],
        enrichment.get("relations") or [],
        media_id=metadata.get("media_id"),
    )
    enriched["related"] = related
    return _add_anime_series(enriched)


def _add_anime_series(metadata):
    """Attach a complete canonical series without making anime detail fragile."""
    relations = (metadata.get("related") or {}).get("relations") or []
    if not any(
        item.get("relation") in {"Prequel", "Sequel"}
        for item in relations
    ):
        return metadata
    try:
        series = mal.anime_series(metadata.get("media_id"))
    except (requests.RequestException, provider_services.ProviderAPIError):
        return metadata

    enriched = deepcopy(metadata)
    position = next(
        (
            item.get("position")
            for item in series["items"]
            if str(item.get("media_id")) == str(metadata.get("media_id"))
        ),
        None,
    )
    enriched["details"] = {
        **(enriched.get("details") or {}),
        "series_id": series["series_id"],
        "series_source": series["source"],
        "series_media_type": series["media_type"],
        "series_name": series["name"],
        "series_position": position,
    }
    related = dict(enriched.get("related") or {})
    related["relations"] = [
        item
        for item in related.get("relations") or []
        if item.get("relation") not in {"Prequel", "Sequel"}
    ]
    related["series"] = [
        {**item, "series_name": series["name"]}
        for item in series["items"]
    ]
    enriched["related"] = related
    return enriched


def anilist_reviews(*, source, media_type, media_id, page):
    """Return an on-demand AniList review page for a MAL anime."""
    if source != Sources.MAL.value or media_type != MediaTypes.ANIME.value:
        raise NotImplementedError(
            "AniList reviews are only available for MAL anime.",
        )
    try:
        page = int(page)
    except (TypeError, ValueError) as error:
        raise ValueError("page must be a positive integer.") from error
    if page < 1:
        raise ValueError("page must be a positive integer.")
    return anilist.anime_reviews(media_id, page)


def _enrich_manga_metadata(metadata, source):  # noqa: C901, PLR0912, PLR0915
    """Merge MAL-first manga metadata with optional AniList enrichment."""
    if source not in {Sources.MAL.value, Sources.MANGAUPDATES.value}:
        return metadata

    original = deepcopy(metadata)
    mal_id = str(metadata.get("media_id") or "")
    primary = metadata
    if source == Sources.MANGAUPDATES.value:
        mal_id = mal.match_manga(mal_id, metadata) or ""
        if not mal_id:
            original["score"] = None
            original["crew"] = (original.get("creators") or [])[:12]
            original["related"] = _manga_fallback_related(
                original.get("related") or {},
                source=source,
                media_id=metadata.get("media_id"),
            )
            original["_matched_mal_id"] = None
            return original
        try:
            primary = mal.manga(mal_id)
        except (
            requests.RequestException,
            provider_services.ProviderAPIError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            logger.warning(
                "MAL manga enrichment unavailable for MangaUpdates %s: %s",
                metadata.get("media_id"),
                error,
            )
            original["score"] = None
            original["crew"] = (original.get("creators") or [])[:12]
            original["related"] = _manga_fallback_related(
                original.get("related") or {},
                source=source,
                media_id=metadata.get("media_id"),
            )
            original["_matched_mal_id"] = None
            return original

    enriched = _merge_manga_primary(primary, original) if source == Sources.MANGAUPDATES.value else deepcopy(primary)
    enrichment = anilist.manga(mal_id)
    enriched["_matched_mal_id"] = mal_id
    enriched["_anilist"] = enrichment

    if not enriched.get("display_title"):
        enriched["display_title"] = enrichment.get("display_title")
    if not backdrop_url(enriched):
        enriched["backdrop"] = enrichment.get("backdrop")
    enriched["posters"] = _dedupe_artwork(
        [
            *(primary.get("posters") or []),
            *(enrichment.get("posters") or []),
            *(
                (original.get("posters") or [])
                if source == Sources.MANGAUPDATES.value
                else []
            ),
        ],
    )
    enriched["backdrops"] = _dedupe_artwork(
        [*(enriched.get("backdrops") or []), *(enrichment.get("backdrops") or [])],
    )
    enriched["characters"] = (enrichment.get("characters") or [])[:12]
    enriched["creators"] = _merge_manga_creators(
        enriched.get("creators") or [],
        enrichment.get("creators") or [],
    )
    enriched["crew"] = enriched["creators"]

    external_links = dict(enriched.get("external_links") or {})
    external_links["mal"] = f"https://myanimelist.net/manga/{mal_id}"
    if enrichment.get("source_url"):
        external_links["anilist"] = enrichment["source_url"]
    if source == Sources.MANGAUPDATES.value and original.get("source_url"):
        external_links["mangaupdates"] = original["source_url"]
    enriched["external_links"] = external_links

    related = dict(enriched.get("related") or {})
    related["relations"] = _merge_manga_relations(
        related.get("relations") or [],
        enrichment.get("relations") or [],
        media_id=mal_id,
    )
    if not related["relations"] and source == Sources.MANGAUPDATES.value:
        related["relations"] = _manga_fallback_related(
            original.get("related") or {},
            source=source,
            media_id=original.get("media_id"),
        ).get("relations", [])
    if not related.get("recommendations"):
        related["recommendations"] = (
            enrichment.get("recommendations")
            or (
                (original.get("related") or {}).get("recommendations")
                if source == Sources.MANGAUPDATES.value
                else []
            )
            or []
        )
    enriched["related"] = related

    anilist_rating = enrichment.get("rating")
    external_ratings = dict(enriched.get("external_ratings") or {})
    if anilist_rating and anilist_rating.get("value") is not None:
        external_ratings["anilist"] = anilist_rating
    if source == Sources.MANGAUPDATES.value:
        if primary.get("score") is not None:
            external_ratings["mal"] = {
                "value": primary["score"],
                "vote_count": primary.get("score_count"),
                "url": primary.get("source_url")
                or f"https://myanimelist.net/manga/{mal_id}",
            }
        enriched["score"] = None
        enriched["score_count"] = None
    enriched["external_ratings"] = external_ratings
    return enriched


def _merge_manga_primary(primary, fallback):
    """Return MAL metadata with MangaUpdates values filling only gaps."""
    merged = deepcopy(primary)
    for field in ("display_title", "image", "synopsis", "max_progress", "genres"):
        if _empty_manga_value(merged.get(field)) and not _empty_manga_value(
            fallback.get(field),
        ):
            merged[field] = deepcopy(fallback[field])

    merged["posters"] = _dedupe_artwork(
        [*(primary.get("posters") or []), *(fallback.get("posters") or [])],
    )
    merged_details = deepcopy(fallback.get("details") or {})
    merged_details.update(
        {
            key: value
            for key, value in (primary.get("details") or {}).items()
            if not _empty_manga_value(value)
        },
    )
    merged["details"] = merged_details
    if not (primary.get("creators") or []):
        merged["creators"] = deepcopy(fallback.get("creators") or [])
    return merged


def _empty_manga_value(value):
    return value is None or value == "" or value == [] or value == {}


def _merge_manga_creators(primary, enrichment):
    creators = []
    positions = {}
    for creator in [*primary, *enrichment]:
        if not isinstance(creator, dict) or not creator.get("name"):
            continue
        key = re.sub(r"[\W_]+", "", creator["name"].casefold())
        if key in positions:
            current = creators[positions[key]]
            for field in ("image", "image_url", "role"):
                if not current.get(field) and creator.get(field):
                    current[field] = creator[field]
            if creator.get("person_source") == "anilist":
                current["person_source"] = creator["person_source"]
                current["person_id"] = creator.get("person_id")
            else:
                for field in ("person_source", "person_id"):
                    if not current.get(field) and creator.get(field):
                        current[field] = creator[field]
            continue
        positions[key] = len(creators)
        creators.append(dict(creator))
    return creators[:12]


def _manga_fallback_related(related, *, source, media_id):
    result = dict(related)
    result["relations"] = _merge_manga_relations(
        result.get("relations") or result.get("related_manga") or [],
        [],
        media_id=media_id,
        source=source,
    )
    result.pop("related_manga", None)
    return result


def _dedupe_artwork(candidates):
    results = []
    seen = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        url = candidate.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        results.append(candidate)
    return results


def _merge_anime_relations(primary, enrichment, *, media_id):
    merged = {}
    order = []
    for candidate in [*primary, *enrichment]:
        if not isinstance(candidate, dict):
            continue
        relation = dict(candidate)
        relation["relation"] = _normalize_anime_relation(relation.get("relation"))
        key = (
            relation.get("source") or Sources.MAL.value,
            relation.get("media_type") or MediaTypes.ANIME.value,
            str(relation.get("media_id") or relation.get("id") or ""),
        )
        if not key[2] or (
            key[0] == Sources.MAL.value
            and key[1] == MediaTypes.ANIME.value
            and key[2] == str(media_id)
        ):
            continue
        if key not in merged:
            merged[key] = relation
            order.append(key)
            continue
        current = merged[key]
        for field in ("display_title", "image", "release_date", "source_url"):
            if not current.get(field) and relation.get(field):
                current[field] = relation[field]
    position = {key: index for index, key in enumerate(order)}
    return sorted(
        merged.values(),
        key=lambda relation: (
            ANIME_RELATION_ORDER.get(relation["relation"], 99),
            position[
                (
                    relation.get("source") or Sources.MAL.value,
                    relation.get("media_type") or MediaTypes.ANIME.value,
                    str(relation.get("media_id") or relation.get("id") or ""),
                )
            ],
        ),
    )


def _normalize_anime_relation(value):
    label = str(value or "Other").replace("_", " ").strip()
    normalized = ANIME_RELATION_LABELS.get(label.lower(), label.title())
    return normalized if normalized in ANIME_RELATION_ORDER else "Other"


def _merge_manga_relations(
    primary,
    enrichment,
    *,
    media_id,
    source=Sources.MAL.value,
):
    merged = {}
    order = []
    for candidate in [*primary, *enrichment]:
        if not isinstance(candidate, dict):
            continue
        relation = dict(candidate)
        relation_source = relation.get("source") or source
        relation_type = relation.get("media_type") or MediaTypes.MANGA.value
        relation_id = str(relation.get("media_id") or relation.get("id") or "")
        if not relation_id or (
            relation_source == source
            and relation_type == MediaTypes.MANGA.value
            and relation_id == str(media_id)
        ):
            continue
        relation["relation"] = _normalize_manga_relation(
            relation.get("relation"),
            default="Related" if source == Sources.MANGAUPDATES.value else "Other",
        )
        key = (relation_source, relation_type, relation_id)
        if key not in merged:
            merged[key] = relation
            order.append(key)
            continue
        current = merged[key]
        for field in ("display_title", "image", "release_date", "source_url"):
            if not current.get(field) and relation.get(field):
                current[field] = relation[field]
    position = {key: index for index, key in enumerate(order)}
    return sorted(
        merged.values(),
        key=lambda relation: (
            ANIME_RELATION_ORDER.get(relation["relation"], 99),
            position[
                (
                    relation.get("source") or source,
                    relation.get("media_type") or MediaTypes.MANGA.value,
                    str(relation.get("media_id") or relation.get("id") or ""),
                )
            ],
        ),
    )


def _normalize_manga_relation(value, *, default):
    label = str(value or default).replace("_", " ").strip()
    normalized = ANIME_RELATION_LABELS.get(label.lower(), label.title())
    allowed = {*ANIME_RELATION_ORDER, "Related"}
    return normalized if normalized in allowed else default


def music_recording_detail(*, release_group_mbid, recording_mbid, request=None, user=None):
    """Return read-only recording metadata within a representative album release."""
    release_group_mbid = str(release_group_mbid)
    recording_mbid = str(recording_mbid)
    album_metadata = provider_services.get_media_metadata(
        MediaTypes.MUSIC.value,
        release_group_mbid,
        Sources.MUSICBRAINZ.value,
    )
    representative = (album_metadata.get("music") or {}).get(
        "representative_release",
    )
    context_medium = None
    context_track = None
    for medium in (representative or {}).get("media") or []:
        for track in medium.get("tracks") or []:
            if (track.get("recording") or {}).get("recording_mbid") == recording_mbid:
                context_medium = medium
                context_track = track
                break
        if context_track:
            break
    if context_track is None:
        raise Http404

    parent_payload = {
        **album_metadata,
        "media_id": release_group_mbid,
        "source": Sources.MUSICBRAINZ.value,
        "media_type": MediaTypes.MUSIC.value,
    }
    parent_album = media_summary_from_provider(
        parent_payload,
        MediaTypes.MUSIC.value,
        Sources.MUSICBRAINZ.value,
        request=request,
        user=user,
    )
    metadata = musicbrainz.recording(recording_mbid)

    albums = []
    seen_albums = set()
    for payload in [parent_payload, *metadata.pop("albums")]:
        album_mbid = str(payload.get("media_id") or "")
        if not album_mbid or album_mbid in seen_albums:
            continue
        seen_albums.add(album_mbid)
        albums.append(
            media_summary_from_provider(
                payload,
                MediaTypes.MUSIC.value,
                Sources.MUSICBRAINZ.value,
                request=request,
                user=user,
            ),
        )

    releases = metadata.pop("releases")
    context_release_mbid = representative.get("release_mbid")
    if context_release_mbid not in {release["release_mbid"] for release in releases}:
        releases.insert(
            0,
            {
                "release_mbid": context_release_mbid,
                "title": representative.get("title") or "",
                "status": representative.get("status") or None,
                "date": representative.get("date") or None,
                "country": representative.get("country") or None,
                "barcode": representative.get("barcode") or None,
                "release_group_mbid": release_group_mbid,
            },
        )

    return {
        **metadata,
        "parent_album": parent_album,
        "albums": albums,
        "releases": releases,
        "context_release": {
            "release_mbid": context_release_mbid,
            "medium_mbid": context_medium.get("medium_mbid"),
            "track": context_track,
        },
        "image_url": parent_album["image_url"],
        "capabilities": {
            "trackable": False,
            "rateable": False,
            "reviewable": False,
            "diary_loggable": False,
            "likeable": False,
            "listable": False,
            "library_addable": False,
        },
    }


def _person_credit_identity(credit):
    media_id = str(credit.get("media_id") or "").strip()
    source = credit.get("source")
    media_type = credit.get("media_type")
    season_number = credit.get("season_number")
    episode_number = credit.get("episode_number")
    if (
        not media_id
        or len(media_id) > Item._meta.get_field("media_id").max_length
        or source not in Sources.values
        or media_type not in MediaTypes.values
    ):
        return None
    if media_type == MediaTypes.SEASON.value:
        if season_number is None or episode_number is not None:
            return None
    elif media_type == MediaTypes.EPISODE.value:
        if season_number is None or episode_number is None:
            return None
    elif season_number is not None or episode_number is not None:
        return None
    return source, media_type, media_id, season_number, episode_number


def _person_item_query(identities):
    query = Q(pk__isnull=True)
    grouped = {}
    for source, media_type, media_id, season_number, episode_number in identities:
        grouped.setdefault(
            (source, media_type, season_number, episode_number),
            set(),
        ).add(media_id)
    for (source, media_type, season_number, episode_number), media_ids in grouped.items():
        query |= Q(
            source=source,
            media_type=media_type,
            media_id__in=media_ids,
            season_number=season_number,
            episode_number=episode_number,
        )
    return query


def _person_release_fields(credit):
    raw_date = (
        credit.get("release_date")
        or credit.get("first_air_date")
        or credit.get("publish_date")
    )
    try:
        release_date = parse_date(str(raw_date)) if raw_date else None
    except ValueError:
        release_date = None
    raw_year = credit.get("year") or (str(raw_date)[:4] if raw_date else None)
    try:
        release_year = int(raw_year) if raw_year and int(raw_year) > 0 else None
    except (TypeError, ValueError):
        release_year = None
    raw_runtime = credit.get("runtime_minutes")
    try:
        runtime = int(raw_runtime) if raw_runtime and int(raw_runtime) > 0 else None
    except (TypeError, ValueError):
        runtime = None
    return release_date, release_year, runtime


def _materialize_person_credits(person_credits, rating_source=None):
    identities = {
        identity
        for credit in person_credits
        if (identity := _person_credit_identity(credit)) is not None
    }
    if not identities:
        return {}

    query = _person_item_query(identities)
    with transaction.atomic():
        existing = {
            (item.source, item.media_type, item.media_id, item.season_number, item.episode_number)
            for item in Item.objects.filter(query)
        }
        new_items = []
        for credit in person_credits:
            identity = _person_credit_identity(credit)
            if identity is None or identity in existing:
                continue
            release_date, release_year, runtime = _person_release_fields(credit)
            new_items.append(
                Item(
                    source=identity[0],
                    media_type=identity[1],
                    media_id=identity[2],
                    season_number=identity[3],
                    episode_number=identity[4],
                    title=credit.get("title") or credit.get("name") or "",
                    image=credit.get("image") or settings.IMG_NONE,
                    release_date=release_date,
                    release_year=release_year,
                    runtime_minutes=runtime,
                ),
            )
            existing.add(identity)
        if new_items:
            Item.objects.bulk_create(new_items, ignore_conflicts=True)

        items = Item.objects.filter(query)
        if rating_source:
            items = items.prefetch_related(
                Prefetch(
                    "external_ratings",
                    queryset=ExternalRating.objects.filter(
                        rating_source=rating_source,
                    ),
                ),
            )
        return {
            (item.source, item.media_type, item.media_id, item.season_number, item.episode_number): item
            for item in items
        }


def _existing_person_credit_items(person_credits):
    identities = {
        identity
        for credit in person_credits
        if (identity := _person_credit_identity(credit)) is not None
    }
    if not identities:
        return {}
    return {
        (item.source, item.media_type, item.media_id, item.season_number, item.episode_number): item
        for item in Item.objects.filter(_person_item_query(identities))
    }


def _prime_person_items(items, user):
    """Batch viewer state and artwork preferences for person credit cards."""
    items = list(items)
    if not items or not user or not user.is_authenticated:
        return
    item_ids_by_type = {}
    for item in items:
        item_ids_by_type.setdefault(item.media_type, []).append(item.pk)
    media_by_item = {}
    for media_type, item_ids in item_ids_by_type.items():
        model = apps.get_model("app", media_type)
        media_by_item.update({
            media.item_id: media
            for media in model.objects.filter(user=user, item_id__in=item_ids)
        })
    prime_collection_items(items, user, media_by_item=media_by_item)


def _curate_person_credits(person_source, person_id, credit_list):
    """Apply explicit curator decisions without discarding unverified provider data."""
    overrides = list(
        BookCreditOverride.objects.filter(
            author_source=person_source,
            author_id=str(person_id),
        ),
    )
    if not overrides:
        return credit_list

    dispositions = {
        (row.book_source, row.book_id): row.disposition
        for row in overrides
    }
    curated_mode = any(
        disposition
        in {
            BookCreditOverride.Disposition.PRIMARY,
            BookCreditOverride.Disposition.ADDITIONAL,
        }
        for disposition in dispositions.values()
    )
    curated = []
    for credit in credit_list:
        identity = (
            credit.get("source"),
            str(credit.get("media_id") or ""),
        )
        disposition = dispositions.get(identity)
        if disposition == BookCreditOverride.Disposition.HIDDEN:
            continue

        curated_credit = dict(credit)
        if disposition == BookCreditOverride.Disposition.PRIMARY:
            curated_credit["roles"] = ["Author"]
        elif disposition == BookCreditOverride.Disposition.ADDITIONAL:
            curated_credit["roles"] = ["Additional writing"]
        elif (
            curated_mode
            and credit.get("media_type") == MediaTypes.BOOK.value
            and credit.get("is_author_role")
        ):
            curated_credit["roles"] = ["Unverified catalog"]
        curated.append(curated_credit)
    return curated


def _person_filter_options(person_credits):
    identities = {
        (credit.get("source"), credit.get("media_type"))
        for credit in person_credits
        if credit.get("source") and credit.get("media_type")
    }
    rating_sorts = [
        {
            "value": f"rating:{source}",
            "label": f"{definition['label']} Rating",
        }
        for source, definition in RATING_SOURCES.items()
        if settings.EXTERNAL_RATING_PERSON_PREPARATION_ENABLED
        and rating_source_is_exposed(source)
        and any(
            item_source in definition["item_sources"]
            and media_type in definition["media_types"]
            for item_source, media_type in identities
        )
    ]
    genres = sorted({genre for credit in person_credits for genre in credit.get("genres") or []})
    languages = sorted({language for credit in person_credits for language in credit.get("languages") or []})
    years = set()
    for credit in person_credits:
        _release_date, year, _runtime = _person_release_fields(credit)
        if year is not None:
            years.add(year)
    sorts = [
        option
        for option in PERSON_SORT_OPTIONS
        if option["value"] != "popularity"
        or any(credit.get("vote_count") is not None for credit in person_credits)
    ]
    return {
        "sorts": [*sorts, *rating_sorts],
        "genres": [{"value": value, "label": value} for value in genres],
        "languages": [{"value": value, "label": value} for value in languages],
        "platforms": [],
        "years": sorted(years, reverse=True),
    }


def _person_preparation_key(source, person_id, rating_source, params):
    scope = []
    for key in sorted(params):
        if key in {"sort", "ordering", "direction"}:
            continue
        raw_values = params.getlist(key) if hasattr(params, "getlist") else [params.get(key)]
        scope.append((key, sorted(str(value) for value in raw_values if value not in (None, ""))))
    digest = hashlib.sha256(repr(scope).encode()).hexdigest()[:16]
    return f"external-ratings:person:{source}:{person_id}:{rating_source}:{digest}"


def _fresh_person_rating_status(row, now, definition):
    if row is None or row.last_attempted_at < now - definition["fresh_for"]:
        return None
    if row.status == ExternalRating.Status.AVAILABLE:
        return ExternalRating.Status.AVAILABLE
    if row.status == ExternalRating.Status.UNAVAILABLE:
        return ExternalRating.Status.UNAVAILABLE
    return None


def _person_rating_counts(eligible, rating_source, now, definition):
    ready = unavailable = failed = 0
    pending_ids = []
    seen_items = set()
    for credit in eligible:
        item = credit.get("_catalog_item")
        if item is None:
            failed += 1
            continue
        if item.pk in seen_items:
            continue
        seen_items.add(item.pk)
        rows = list(item.external_ratings.all())
        row = rows[0] if rows else None
        fresh_status = _fresh_person_rating_status(row, now, definition)
        if row is not None and row.status == ExternalRating.Status.FAILED:
            failed += 1
        elif fresh_status == ExternalRating.Status.AVAILABLE:
            ready += 1
        elif fresh_status == ExternalRating.Status.UNAVAILABLE:
            unavailable += 1

        if rating_sources_needing_refresh(item, [rating_source], now=now):
            pending_ids.append(item.pk)
    invalid = sum(credit.get("_catalog_item") is None for credit in eligible)
    return len(seen_items) + invalid, ready, unavailable, failed, pending_ids


def _person_rating_preparation(
    person_credits,
    *,
    person_source,
    person_id,
    rating_source,
    params,
):
    definition = RATING_SOURCES[rating_source]
    eligible = [
        credit
        for credit in person_credits
        if credit.get("source") in definition["item_sources"]
        and credit.get("media_type") in definition["media_types"]
    ]
    now = timezone.now()
    total, ready, unavailable, failed, pending_ids = _person_rating_counts(
        eligible,
        rating_source,
        now,
        definition,
    )
    if failed:
        state = "degraded"
    elif ready + unavailable == total:
        state = "ready"
    else:
        state = "pending"

    if pending_ids:
        lock_key = _person_preparation_key(
            person_source,
            person_id,
            rating_source,
            params,
        )
        try:
            acquired = cache.add(
                lock_key,
                1,
                timeout=PERSON_PREPARATION_LOCK_TIMEOUT,
            )
        except (ConnectionInterrupted, RedisError, ConnectionError, OSError) as error:
            acquired = False
            logger.warning(
                "External rating person lock unavailable type=%s",
                type(error).__name__,
            )
        if acquired:
            from app.tasks import enqueue_external_rating_batches

            transaction.on_commit(
                partial(
                    enqueue_external_rating_batches,
                    sorted(set(pending_ids)),
                    [rating_source],
                ),
                robust=True,
            )

    return {
        "rating_source": rating_source,
        "state": state,
        "total": total,
        "ready": ready,
        "unavailable": unavailable,
        "failed": failed,
    }


def person_detail(
    *,
    source,
    person_id,
    request=None,
    user=None,
    params=None,
    credits_page=1,
):
    """Return a provider person profile plus iOS-ready media summaries."""
    if source not in provider_services.SUPPORTED_PERSON_SOURCES:
        msg = (
            "People pages are only supported for TMDB, Hardcover, OpenLibrary, "
            "MusicBrainz, MAL, MangaUpdates, and AniList in v1."
        )
        raise NotImplementedError(msg)

    params = params or {}
    person = (
        provider_services.get_person_page(
            source,
            person_id,
            page=credits_page,
        )
        if source == "anilist"
        else provider_services.get_person_page(source, person_id)
    )
    raw_credits = [
        {**credit, "source": credit.get("source") or source}
        for credit in person.get("credits") or []
    ]
    raw_credits = _curate_person_credits(source, person_id, raw_credits)
    rating_source = person_rating_source(raw_credits, params)
    items = (
        _materialize_person_credits(raw_credits, rating_source)
        if rating_source
        else _existing_person_credit_items(raw_credits)
    )
    _prime_person_items(items.values(), user)
    enriched_credits = []
    for credit in raw_credits:
        item = items.get(_person_credit_identity(credit))
        ratings = list(item.external_ratings.all()) if item is not None and rating_source else []
        enriched_credits.append({
            **credit,
            "_catalog_item": item,
            "_catalog_item_id": item.pk if item else None,
            "_external_rating": ratings[0] if ratings else None,
        })
    person_credits = apply_person_credit_filters(
        enriched_credits,
        params,
        rating_source=rating_source,
        default_sort=(
            "popularity"
            if source == "anilist"
            and any(credit.get("vote_count") is not None for credit in raw_credits)
            else None
        ),
    )
    rating_preparation = (
        _person_rating_preparation(
            person_credits,
            person_source=source,
            person_id=person_id,
            rating_source=rating_source,
            params=params,
        )
        if rating_source
        else None
    )
    series_summaries = [
        _person_series_summary(
            series,
            default_source=source,
            request=request,
            user=user,
        )
        for series in person.get("series") or []
        if series.get("series_id") and series.get("name")
    ]
    series_summaries.extend(
        _anime_person_series(
            enriched_credits,
            request=request,
            user=user,
        ),
    )
    credit_summaries = [
        media_summary_from_provider(
            credit,
            media_type=credit.get("media_type"),
            source=credit.get("source", source),
            request=request,
            user=user,
            item=credit.get("_catalog_item"),
        )
        for credit in person_credits
        if credit.get("media_type")
        in {
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.BOOK.value,
            MediaTypes.MUSIC.value,
            MediaTypes.MANGA.value,
            MediaTypes.ANIME.value,
        }
    ]
    media_type_groups = {}
    role_groups = {}
    for credit in credit_summaries:
        media_type = credit["ref"]["media_type"]
        media_type_groups.setdefault(media_type, []).append(credit)
        roles = [
            role.strip()
            for role in credit.get("credit_roles") or []
            if isinstance(role, str) and role.strip()
        ] or ["Credits"]
        for role in dict.fromkeys(roles):
            role_groups.setdefault(media_type, {}).setdefault(role, []).append(
                credit,
            )
    credits_are_complete = (
        bool(person["credits_complete"])
        if "credits_complete" in person
        else source != "anilist" or person.get("credits_next_page") is None
    )
    return {
        "id": str(person.get("person_id") or person_id),
        "source": source,
        "name": person.get("name") or "",
        "alternative_names": person.get("alternative_names") or [],
        "biography": person.get("biography"),
        "profile_url": absolute_url(request, person.get("image")),
        "known_for_department": person.get("known_for_department"),
        "birth_date": person.get("birth_date"),
        "death_date": person.get("death_date"),
        "place_of_birth": person.get("place_of_birth"),
        "popularity": person.get("popularity"),
        "filter_options": _person_filter_options(raw_credits),
        "rating_preparation": rating_preparation,
        "credits_page": person.get("credits_page"),
        "credits_next_page": person.get("credits_next_page"),
        "credits_complete": credits_are_complete,
        "credits_truncated": bool(person.get("credits_truncated")),
        "series": series_summaries,
        "credits": {"cast": credit_summaries},
        "completion": (
            completion_service.completion_for_summaries(
                user,
                credit_summaries,
            )
            if credits_are_complete
            else None
        ),
        "media_type_completions": (
            {
                media_type: completion_service.completion_for_summaries(
                    user,
                    summaries,
                )
                for media_type, summaries in media_type_groups.items()
            }
            if user and user.is_authenticated and credits_are_complete
            else None
        ),
        "role_completions": (
            {
                media_type: {
                    role: completion_service.completion_for_summaries(
                        user,
                        summaries,
                    )
                    for role, summaries in groups.items()
                }
                for media_type, groups in role_groups.items()
            }
            if user and user.is_authenticated and credits_are_complete
            else None
        ),
    }


def _person_series_summary(
    series,
    *,
    default_source,
    request=None,
    user=None,
):
    source = series.get("source") or default_source
    media_type = series.get("media_type") or MediaTypes.BOOK.value
    books = series.get("books") or []
    item_count = (
        series.get("item_count")
        or series.get("book_count")
        or len(books)
    )
    persisted = completion_service.persist_complete_series({
        **series,
        "source": source,
        "media_type": media_type,
        "item_count": item_count,
        "items": books,
    })
    return {
        "id": str(series.get("series_id") or ""),
        "source": source,
        "media_type": media_type,
        "name": series.get("name") or "",
        "item_count": item_count,
        "book_count": series.get("book_count") or len(books),
        "poster_urls": [
            absolute_url(request, book.get("image"))
            for book in books[:3]
            if book.get("image")
        ],
        "completion": (
            completion_service.completion_for_items(
                user,
                persisted.items.all(),
            )
            if persisted is not None
            else None
        ),
    }


def person_completion(*, source, person_id, user):
    """Return aggregate completion for one provider person's full credit set."""
    if not user or not user.is_authenticated:
        return None
    if source not in provider_services.SUPPORTED_PERSON_SOURCES:
        msg = "People pages do not support this provider in v1."
        raise NotImplementedError(msg)
    person = (
        anilist.person_page(
            person_id,
            enrich_credit_images=False,
            request_session=provider_services.person_search_session,
        )
        if source == "anilist"
        else provider_services.get_person_page(source, person_id)
    )
    raw_credits = person.get("credits") or []
    if source == "anilist":
        raw_credits = person.get("_completion_credits")
        if raw_credits is None:
            raw_credits = anilist.person_completion_credits(
                person_id,
                person,
            )
        if raw_credits is None:
            return None
    person_credits = _curate_person_credits(
        source,
        person_id,
        [
            {**credit, "source": credit.get("source") or source}
            for credit in raw_credits
            if credit.get("media_type")
            in {
                MediaTypes.MOVIE.value,
                MediaTypes.TV.value,
                MediaTypes.BOOK.value,
                MediaTypes.MUSIC.value,
                MediaTypes.MANGA.value,
                MediaTypes.ANIME.value,
            }
        ],
    )
    return completion_service.completion_for_payloads(
        user,
        person_credits,
        default_source=source,
        default_media_type="",
    )


def _anime_person_series(
    raw_credits,
    *,
    request=None,
    user=None,
):
    anime_credits = {
        str(credit.get("media_id")): credit
        for credit in raw_credits
        if credit.get("media_type") == MediaTypes.ANIME.value
        and credit.get("media_id")
    }
    if len(anime_credits) < 2:
        return []

    candidates = _anime_series_candidate_groups(anime_credits, enrich_missing=True)
    resolved = {}
    for credited_candidate in candidates:
        seed = min(credited_candidate, key=int)
        try:
            series = mal.anime_series(seed)
        except (requests.RequestException, provider_services.ProviderAPIError):
            continue
        member_ids = {str(item.get("media_id")) for item in series["items"]}
        credited_ids = member_ids.intersection(anime_credits)
        if len(credited_ids) < 2:
            continue
        series_id = str(series["series_id"])
        popularity = sum(
            anime_credits[media_id].get("vote_count") or 0
            for media_id in credited_ids
        )
        posters = []
        for item in series["items"][:3]:
            catalog_item = (
                anime_credits.get(str(item.get("media_id"))) or {}
            ).get("_catalog_item")
            summary = media_summary_from_provider(
                item,
                MediaTypes.ANIME.value,
                Sources.MAL.value,
                request=request,
                user=user,
                item=catalog_item,
            )
            poster = summary.get("custom_poster_url") or summary.get("poster_url")
            if poster:
                posters.append(poster)
        persisted = completion_service.persist_complete_series(series)
        resolved[series_id] = {
            "id": series_id,
            "source": Sources.MAL.value,
            "media_type": MediaTypes.ANIME.value,
            "name": series["name"],
            "item_count": series["item_count"],
            "poster_urls": posters,
            "completion": (
                completion_service.completion_for_items(
                    user,
                    persisted.items.all(),
                )
                if persisted is not None
                else None
            ),
            "_popularity": popularity,
        }
    return [
        {key: value for key, value in series.items() if key != "_popularity"}
        for series in sorted(
            resolved.values(),
            key=lambda value: (-value["_popularity"], value["name"].casefold()),
        )
    ]


def _anime_series_candidate_groups(anime_credits, *, enrich_missing):
    nodes = _anilist_person_series_nodes(anime_credits) if enrich_missing else {}
    _apply_anilist_popularity(anime_credits, nodes)

    candidates = {}
    for media_id in anime_credits:
        if root_id := mal.cached_anime_series_id(media_id):
            candidates.setdefault(str(root_id), set()).add(media_id)

    adjacency = {}
    for media_id, credit in anime_credits.items():
        links = credit.get("series_links")
        if links is None:
            links = (nodes.get(media_id) or {}).get("series_links") or []
        for link in links:
            neighbor = str(link.get("media_id") or "")
            if not neighbor or neighbor == media_id:
                continue
            adjacency.setdefault(media_id, set()).add(neighbor)
            adjacency.setdefault(neighbor, set()).add(media_id)

    for component in _connected_components(adjacency):
        credited = component.intersection(anime_credits)
        if len(credited) >= 2:
            candidates.setdefault(f"candidate:{min(credited, key=int)}", set()).update(
                credited,
            )
    return [
        credited
        for credited in candidates.values()
        if len(credited) >= 2
    ]


def _apply_anilist_popularity(anime_credits, nodes):
    for media_id, node in nodes.items():
        credit = anime_credits.get(media_id)
        if credit is not None and credit.get("vote_count") is None:
            credit["vote_count"] = node.get("popularity")


def _connected_components(adjacency):
    remaining = set(adjacency)
    while remaining:
        stack = [remaining.pop()]
        component = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency.get(current, set()) - component)
        remaining -= component
        yield component


def _anilist_person_series_nodes(anime_credits):
    if all("series_links" in credit for credit in anime_credits.values()):
        return {}
    try:
        return anilist.anime_series_nodes(anime_credits)
    except (
        requests.RequestException,
        provider_services.ProviderAPIError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return {}


def series_detail(*, source, series_id, request=None, user=None):
    """Return a provider-backed series as iOS-ready media summaries."""
    series = provider_services.get_book_series(source, series_id)
    media_type = series.get("media_type") or MediaTypes.BOOK.value
    raw_items = series.get("items")
    if raw_items is None:
        raw_items = series.get("books") or []
    persisted = completion_service.persist_complete_series({
        **series,
        "source": series.get("source") or source,
        "media_type": media_type,
        "items": raw_items,
    })
    items = [
        media_summary_from_provider(
            item,
            item.get("media_type", media_type),
            item.get("source", source),
            request=request,
            user=user,
        )
        for item in raw_items
    ]
    item_count = series.get("item_count")
    if item_count is None:
        item_count = series.get("book_count")
    if item_count is None:
        item_count = len(items)
    payload = {
        "series_id": str(series.get("series_id") or series_id),
        "id": str(series.get("series_id") or series_id),
        "source": series.get("source") or source,
        "media_type": media_type,
        "name": series.get("name") or "",
        "item_count": item_count,
        "items": items,
        "completion": (
            completion_service.completion_for_items(
                user,
                persisted.items.all(),
            )
            if persisted is not None
            else None
        ),
    }
    if media_type == MediaTypes.BOOK.value:
        payload.update({"book_count": item_count, "books": items})
    return payload


def book_series_detail(*, source, series_id, request=None, user=None):
    """Compatibility wrapper for the original book-only service name."""
    return series_detail(
        source=source,
        series_id=series_id,
        request=request,
        user=user,
    )


def company_detail(*, source, company_id, request=None, user=None):
    """Return a provider company profile for native studio pages."""
    if source not in {Sources.IGDB.value, Sources.MAL.value}:
        msg = "Company pages are only supported for IGDB and MAL in v1."
        raise NotImplementedError(msg)

    company = provider_services.get_company(source, company_id)
    if source == Sources.MAL.value:
        return {
            "id": str(company.get("id") or company_id),
            "source": source,
            "media_type": MediaTypes.ANIME.value,
            "name": company.get("name") or "",
            "description": company.get("description") or None,
            "logo_url": company.get("image") or None,
            "logo_width": None,
            "logo_height": None,
            "founded_year": company.get("founded_year"),
            "country_code": None,
            "status": None,
            "company_size": None,
            "parent": None,
            "igdb_url": None,
            "provider_url": company.get("provider_url") or None,
            "websites": company.get("websites") or [],
            "completion": None,
            "catalogs": {
                "studio": {
                    "available": True,
                    "count": None,
                    "completion": None,
                },
            },
        }

    logo = company.get("logo") or {}
    parent = company.get("parent") or {}
    catalogs = (
        {
            role: provider_services.get_company_catalog(
                source,
                company_id,
                role,
            )
            for role in ("developed", "published")
        }
        if user and user.is_authenticated
        else {"developed": [], "published": []}
    )
    role_completions = {
        role: completion_service.completion_for_payloads(
            user,
            games,
            default_source=Sources.IGDB.value,
            default_media_type=MediaTypes.GAME.value,
        )
        for role, games in catalogs.items()
    }
    all_games = {
        str(game.get("media_id") or game.get("id")): game
        for games in catalogs.values()
        for game in games
        if game.get("media_id") or game.get("id")
    }
    return {
        "id": str(company.get("id") or company_id),
        "source": source,
        "name": company.get("name") or "",
        "description": company.get("description") or None,
        "logo_url": _company_logo_url(logo),
        "logo_width": logo.get("width") or None,
        "logo_height": logo.get("height") or None,
        "founded_year": _company_founded_year(company.get("start_date")),
        "country_code": company.get("country"),
        "status": (company.get("status") or {}).get("name"),
        "company_size": (company.get("company_size") or {}).get("name"),
        "parent": (
            {"id": str(parent["id"]), "name": parent.get("name") or ""}
            if parent.get("id") is not None
            else None
        ),
        "igdb_url": company.get("url") or None,
        "completion": completion_service.completion_for_payloads(
            user,
            all_games.values(),
            default_source=Sources.IGDB.value,
            default_media_type=MediaTypes.GAME.value,
        ),
        "websites": [
            website["url"]
            for website in company.get("websites") or []
            if isinstance(website, dict) and website.get("url")
        ],
        "catalogs": {
            role: {
                "count": provider_services.company_catalog_count(
                    source,
                    company,
                    role,
                ),
                "completion": role_completions[role],
            }
            for role in ("developed", "published")
        },
    }


def company_games(
    *,
    source,
    company_id,
    role,
    sort="popularity",
    direction=None,
    params=None,
    request=None,
    user=None,
):
    """Return sorted native media summaries for a company catalogue role."""
    if source != Sources.IGDB.value:
        msg = "Company pages are only supported for IGDB in v1."
        raise NotImplementedError(msg)
    if role not in {"developed", "published"}:
        raise ValueError("role must be developed or published.")
    if sort not in COMPANY_SORTS:
        raise ValueError("sort must be popularity, release_date, title, or average_rating.")
    if direction not in {None, "asc", "desc"}:
        raise ValueError("direction must be asc or desc.")

    direction = direction or ("asc" if sort == "title" else "desc")
    catalog = provider_services.get_company_catalog(source, company_id, role)
    filtered = _filter_company_catalog(catalog, params or {})
    ordered = _sort_company_catalog(filtered, sort=sort, direction=direction)
    return [
        media_summary_from_provider(
            game,
            media_type=MediaTypes.GAME.value,
            source=Sources.IGDB.value,
            request=request,
            user=user,
        )
        for game in ordered
    ]


def company_game_filter_options(*, source, company_id):
    """Return complete choices across a company's developed and published games."""
    if source != Sources.IGDB.value:
        msg = "Company pages are only supported for IGDB in v1."
        raise NotImplementedError(msg)

    cache_key = f"company_game_options_{source}_{company_id}_{COMPANY_GAME_OPTIONS_CACHE_VERSION}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    games_by_id = {}
    for role in ("developed", "published"):
        for game in provider_services.get_company_catalog(source, company_id, role):
            games_by_id.setdefault(str(game.get("media_id") or ""), game)

    games = games_by_id.values()
    genres = sorted({value for game in games for value in game.get("genres") or []}, key=str.casefold)
    platforms = sorted({value for game in games for value in game.get("platforms") or []}, key=str.casefold)
    years = sorted(
        {
            int(str(game["release_date"])[:4])
            for game in games
            if game.get("release_date") and str(game["release_date"])[:4].isdigit()
        },
        reverse=True,
    )
    options = {
        "sorts": COMPANY_GAME_SORT_OPTIONS,
        "genres": [{"value": value, "label": value} for value in genres],
        "languages": [],
        "platforms": [{"value": value, "label": value} for value in platforms],
        "years": years,
    }
    cache.set(cache_key, options, DETAIL_TTL)
    return options


def company_anime(
    *,
    source,
    company_id,
    sort="popularity",
    direction=None,
    params=None,
    request=None,
    user=None,
):
    """Return a nullable-count, provider-paginated MAL studio catalog."""
    if source != Sources.MAL.value:
        msg = "Anime studio catalogs are only supported for MAL in v1."
        raise NotImplementedError(msg)
    if sort not in COMPANY_SORTS:
        raise ValueError(
            "sort must be popularity, release_date, title, or average_rating.",
        )
    if direction not in {None, "asc", "desc"}:
        raise ValueError("direction must be asc or desc.")

    params = params or {}
    page = _company_int_param(params, "page")
    page_size = _company_int_param(params, "page_size")
    page = 1 if page is None else page
    page_size = 25 if page_size is None else page_size
    if page < 1:
        raise ValueError("page must be at least 1.")
    if not 1 <= page_size <= mal.STUDIO_ANIME_PAGE_SIZE:
        message = (
            f"page_size must be between 1 and {mal.STUDIO_ANIME_PAGE_SIZE}."
        )
        raise ValueError(message)
    year = _company_int_param(params, "year")
    release_status = params.get("release_status")
    if release_status not in {None, "", "released", "unreleased"}:
        raise ValueError("release_status must be released or unreleased.")
    rating_min = _company_decimal_param(params, "rating_min")
    rating_max = _company_decimal_param(params, "rating_max")
    if (rating_min is not None and not 0 <= rating_min <= 10) or (
        rating_max is not None and not 0 <= rating_max <= 10
    ):
        raise ValueError("rating_min and rating_max must be between 0 and 10.")
    if rating_min is not None and rating_max is not None and rating_min > rating_max:
        raise ValueError(
            "rating_min must be less than or equal to rating_max.",
        )
    if _company_query_values(params, "platform") or _company_query_values(
        params,
        "exclude_platform",
    ):
        raise ValueError(
            "platform filters are not supported for anime studio catalogs.",
        )

    catalog_filters = {
        "year": year,
        "release_status": release_status or None,
        "rating_min": rating_min,
        "rating_max": rating_max,
        "genres": _company_query_values(params, "genre"),
        "excluded_genres": _company_query_values(
            params,
            "exclude_genre",
        ),
    }
    page_data = provider_services.get_company_anime(
        source,
        company_id,
        page=page,
        page_size=page_size,
        sort=sort,
        direction=direction,
        filters=catalog_filters,
    )
    results = [
        media_summary_from_provider(
            anime,
            media_type=MediaTypes.ANIME.value,
            source=Sources.MAL.value,
            request=request,
            user=user,
        )
        for anime in page_data.get("results") or []
    ]
    next_link = _company_page_link(request, page_data.get("next_page"))
    previous_link = _company_page_link(
        request,
        page_data.get("previous_page"),
    )
    completion_catalog = (
        mal.studio_anime_completion_catalog(
            company_id,
            filters=catalog_filters,
        )
        if user and user.is_authenticated
        else None
    )
    return {
        "count": None,
        "next": next_link,
        "previous": previous_link,
        "results": results,
        "completion": (
            completion_service.completion_for_payloads(
                user,
                completion_catalog["results"],
                default_source=Sources.MAL.value,
                default_media_type=MediaTypes.ANIME.value,
            )
            if completion_catalog and completion_catalog["complete"]
            else None
        ),
    }


def company_anime_filter_options(*, source, company_id):
    """Return filter choices for one MAL anime studio catalog."""
    if source != Sources.MAL.value:
        msg = "Anime studio catalogs are only supported for MAL in v1."
        raise NotImplementedError(msg)
    options = provider_services.get_company_anime_filter_options(
        source,
        company_id,
    )
    return {
        "sorts": COMPANY_ANIME_SORT_OPTIONS,
        "genres": options.get("genres") or [],
        "languages": [],
        "platforms": [],
        "years": options.get("years") or [],
    }


def _company_page_link(request, page):
    if request is None or page is None:
        return None
    query = request.query_params.copy()
    query["page"] = str(page)
    return request.build_absolute_uri(f"{request.path}?{query.urlencode()}")


def _company_logo_url(logo):
    url = logo.get("url") if isinstance(logo, dict) else None
    if url:
        normalized = f"https:{url}" if url.startswith("//") else url
        parts = urlsplit(normalized)
        path = parts.path.replace("/t_thumb/", "/t_logo_med/")
        filename = path.rsplit("/", 1)[-1]
        if "." in filename:
            path = path.rsplit(".", 1)[0]
        return urlunsplit(parts._replace(path=f"{path}.png"))
    image_id = logo.get("image_id") if isinstance(logo, dict) else None
    if image_id:
        return f"https://images.igdb.com/igdb/image/upload/t_logo_med/{image_id}.png"
    return None


def _company_founded_year(value):
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).year
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _sort_company_catalog(catalog, *, sort, direction):
    descending = direction == "desc"
    if sort == "title":
        return sorted(catalog, key=lambda game: (game.get("title") or "").casefold(), reverse=descending)

    if sort == "popularity":
        key = "vote_count"
    elif sort == "release_date":
        key = "release_date"
    else:
        key = "vote_average"
    known = [game for game in catalog if game.get(key) not in (None, "")]
    unknown = [game for game in catalog if game.get(key) in (None, "")]
    if sort == "release_date":
        known.sort(key=lambda game: game[key], reverse=descending)
    else:
        known.sort(key=lambda game: float(game[key]), reverse=descending)
    return [*known, *unknown]


def _filter_company_catalog(catalog, params):
    year = _company_int_param(params, "year")
    release_status = params.get("release_status")
    if release_status not in {None, "", "released", "unreleased"}:
        raise ValueError("release_status must be released or unreleased.")
    rating_min = _company_decimal_param(params, "rating_min")
    rating_max = _company_decimal_param(params, "rating_max")
    if (rating_min is not None and not 0 <= rating_min <= 100) or (
        rating_max is not None and not 0 <= rating_max <= 100
    ):
        raise ValueError("rating_min and rating_max must be between 0 and 100.")
    if rating_min is not None and rating_max is not None and rating_min > rating_max:
        raise ValueError("rating_min must be less than or equal to rating_max.")

    genres = set(_company_query_values(params, "genre"))
    excluded_genres = set(_company_query_values(params, "exclude_genre"))
    platforms = set(_company_query_values(params, "platform"))
    excluded_platforms = set(_company_query_values(params, "exclude_platform"))
    today = datetime.now(tz=UTC).date()

    return [
        game
        for game in catalog
        if _company_game_matches(
            game,
            year=year,
            release_status=release_status,
            rating_min=rating_min,
            rating_max=rating_max,
            genres=genres,
            excluded_genres=excluded_genres,
            platforms=platforms,
            excluded_platforms=excluded_platforms,
            today=today,
        )
    ]


def _company_game_matches(  # noqa: C901, PLR0911
    game,
    *,
    year,
    release_status,
    rating_min,
    rating_max,
    genres,
    excluded_genres,
    platforms,
    excluded_platforms,
    today,
):
    game_genres = set(game.get("genres") or [])
    game_platforms = set(game.get("platforms") or [])
    if genres and genres.isdisjoint(game_genres):
        return False
    if excluded_genres and not excluded_genres.isdisjoint(game_genres):
        return False
    if platforms and platforms.isdisjoint(game_platforms):
        return False
    if excluded_platforms and not excluded_platforms.isdisjoint(game_platforms):
        return False

    release_date = _company_release_date(game.get("release_date"))
    if year is not None and (release_date is None or release_date.year != year):
        return False
    if release_status == "released" and (release_date is None or release_date > today):
        return False
    if release_status == "unreleased" and (release_date is None or release_date <= today):
        return False

    rating = game.get("vote_average")
    if rating_min is not None or rating_max is not None:
        if rating in {None, ""}:
            return False
        rating = Decimal(str(rating))
        if rating_min is not None and rating < rating_min:
            return False
        if rating_max is not None and rating > rating_max:
            return False
    return True


def _company_query_values(params, key):
    if hasattr(params, "getlist"):
        return [value for value in params.getlist(key) if value not in {None, ""}]
    value = params.get(key)
    return [value] if value not in {None, ""} else []


def _company_int_param(params, key):
    value = params.get(key)
    if value in {None, ""}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        message = f"{key} must be an integer."
        raise ValueError(message) from error


def _company_decimal_param(params, key):
    value = params.get(key)
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        message = f"{key} must be a number."
        raise ValueError(message) from error


def _company_release_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def poster_options(*, source, media_type, media_id, season_number=None, request=None, user=None):
    """Return selectable posters for supported media."""
    if _supports_anime_artwork(source, media_type):
        return anime_poster_options(
            source=source,
            media_id=media_id,
            request=request,
            user=user,
        )
    if _supports_manga_posters(source, media_type):
        return manga_poster_options(
            source=source,
            media_id=media_id,
            request=request,
            user=user,
        )
    if _supports_music_posters(source, media_type):
        options = music_cover_options(
            source=source,
            media_id=media_id,
            request=request,
            user=user,
        )
        return {"posters": options["posters"]}
    if _supports_book_posters(source, media_type):
        options = book_cover_options(
            source=source,
            media_id=media_id,
            request=request,
            user=user,
        )
        return {"posters": options["posters"]}
    if _supports_game_posters(source, media_type):
        options = game_cover_options(source=source, media_id=media_id, request=request, user=user)
        return {"posters": options["posters"]}
    if source != Sources.TMDB.value or media_type not in [MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.SEASON.value]:
        raise ValueError(POSTER_UNSUPPORTED_MESSAGE)
    season_number = _required_season_number(media_type, season_number)

    item = _customizable_item(source=source, media_type=media_type, media_id=media_id, season_number=season_number)
    from app.providers import tmdb

    current = CustomPosterPreference.objects.filter(user=user, item=item).first()
    selected_url = current.custom_image_url if current else item.image
    original = {
        "url": absolute_poster_url(request, item.image),
        "thumbnail_url": absolute_poster_url(request, item.image),
        "width": 0,
        "height": 0,
        "aspect_ratio": 0.667,
        "vote_average": 0,
        "vote_count": 0,
        "language": None,
        "is_original": True,
        "is_selected": item.image == selected_url,
    }

    posters = [original]
    for poster in tmdb.get_poster_images(media_id, media_type, season_number):
        if poster["url"] == item.image:
            continue
        posters.append(
            {
                **poster,
                "language": poster.get("language"),
                "is_original": False,
                "is_selected": poster["url"] == selected_url,
            },
        )
    return {"posters": posters}


def save_poster_preference(*, source, media_type, media_id, poster_url, user, season_number=None):
    """Save a user's poster preference and update the stored item image/accent."""
    if (
        source != Sources.TMDB.value
        or media_type not in [MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.SEASON.value]
    ) and not _supports_book_posters(source, media_type) and not _supports_game_posters(
        source,
        media_type,
    ) and not _supports_music_posters(source, media_type) and not _supports_anime_artwork(
        source,
        media_type,
    ) and not _supports_manga_posters(
        source,
        media_type,
    ):
        raise ValueError(POSTER_UNSUPPORTED_MESSAGE)
    season_number = _required_season_number(media_type, season_number)
    if not poster_url:
        raise ValueError("poster_url is required.")

    item = _customizable_item(source=source, media_type=media_type, media_id=media_id, season_number=season_number)
    accent = compute_and_store_poster_accent(item, poster_url=poster_url, force=True)
    palette = build_accent_palette(accent)
    CustomPosterPreference.objects.update_or_create(
        user=user,
        item=item,
        defaults={"custom_image_url": poster_url},
    )
    item.image = poster_url
    item.poster_accent_color = palette["accent"]
    item.save(update_fields=["image", "poster_accent_color"])
    return {
        "poster_url": poster_url,
        "custom_poster_url": poster_url,
        "poster_accent_color": palette["accent"],
    }


def anime_poster_options(*, source, media_id, request=None, user=None):
    """Return MAL and AniList portrait artwork for anime."""
    media_type = MediaTypes.ANIME.value
    if not _supports_anime_artwork(source, media_type):
        raise ValueError(POSTER_UNSUPPORTED_MESSAGE)

    item = _customizable_item(source=source, media_type=media_type, media_id=media_id)
    metadata = _enrich_anime_metadata(
        provider_services.get_media_metadata(media_type, media_id, source),
        source,
    )
    current = CustomPosterPreference.objects.filter(user=user, item=item).first()
    selected_url = current.custom_image_url if current else item.image
    selected_absolute = absolute_poster_url(request, selected_url)
    candidates = list(metadata.get("posters") or [])
    if item.image and item.image not in {candidate.get("url") for candidate in candidates}:
        candidates.insert(
            0,
            {
                "url": item.image,
                "thumbnail_url": item.image,
                "is_original": not candidates,
            },
        )

    posters = []
    for candidate in _dedupe_artwork(candidates):
        url = absolute_poster_url(request, candidate["url"])
        posters.append(
            {
                "url": url,
                "thumbnail_url": absolute_poster_url(
                    request,
                    candidate.get("thumbnail_url") or candidate["url"],
                ),
                "width": candidate.get("width") or 0,
                "height": candidate.get("height") or 0,
                "aspect_ratio": candidate.get("aspect_ratio") or 0.667,
                "vote_average": 0,
                "vote_count": 0,
                "language": candidate.get("language"),
                "provider_name": candidate.get("provider_name"),
                "provider_url": candidate.get("provider_url"),
                "is_original": bool(candidate.get("is_original")),
                "is_selected": url == selected_absolute,
            },
        )
    return {"posters": posters}


def manga_poster_options(*, source, media_id, request=None, user=None):
    """Return MAL, AniList, and native MangaUpdates portrait artwork."""
    media_type = MediaTypes.MANGA.value
    if not _supports_manga_posters(source, media_type):
        raise ValueError(POSTER_UNSUPPORTED_MESSAGE)

    item = _customizable_item(
        source=source,
        media_type=media_type,
        media_id=media_id,
    )
    metadata = _enrich_manga_metadata(
        provider_services.get_media_metadata(media_type, media_id, source),
        source,
    )
    current = CustomPosterPreference.objects.filter(user=user, item=item).first()
    selected_url = current.custom_image_url if current else metadata.get("image") or item.image
    selected_absolute = absolute_poster_url(request, selected_url)
    candidates = list(metadata.get("posters") or [])
    if item.image and item.image not in {
        candidate.get("url") for candidate in candidates
    }:
        candidates.append(
            {
                "url": item.image,
                "thumbnail_url": item.image,
                "provider_name": (
                    "MangaUpdates"
                    if source == Sources.MANGAUPDATES.value
                    else "MyAnimeList"
                ),
                "is_original": not candidates,
            },
        )

    posters = []
    for candidate in _dedupe_artwork(candidates):
        url = absolute_poster_url(request, candidate["url"])
        posters.append(
            {
                "url": url,
                "thumbnail_url": absolute_poster_url(
                    request,
                    candidate.get("thumbnail_url") or candidate["url"],
                ),
                "width": candidate.get("width") or 0,
                "height": candidate.get("height") or 0,
                "aspect_ratio": candidate.get("aspect_ratio") or 0.667,
                "vote_average": 0,
                "vote_count": 0,
                "language": candidate.get("language"),
                "provider_name": candidate.get("provider_name"),
                "provider_url": candidate.get("provider_url"),
                "is_original": bool(candidate.get("is_original")),
                "is_selected": url == selected_absolute,
            },
        )
    return {"posters": posters}


def book_cover_options(*, source, media_id, request=None, user=None):
    """Return selectable covers for Open Library/Hardcover books."""
    media_type = MediaTypes.BOOK.value
    if not _supports_book_posters(source, media_type):
        raise ValueError(POSTER_UNSUPPORTED_MESSAGE)

    item = _customizable_item(source=source, media_type=media_type, media_id=media_id)
    isbns = _book_isbns(source, media_id)
    current = CustomPosterPreference.objects.filter(user=user, item=item).first()
    selected_url = current.custom_image_url if current else item.image
    selected_absolute = absolute_poster_url(request, selected_url)

    posters = []
    seen = set()
    covers = [
        {"url": item.image, "thumbnail_url": item.image, "is_original": True},
        *_book_cover_candidates(source, media_id, isbns),
    ]
    for cover in covers:
        url = cover.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        absolute_url = absolute_poster_url(request, url)
        posters.append(
            {
                "url": absolute_url,
                "thumbnail_url": absolute_poster_url(request, cover.get("thumbnail_url") or url),
                "width": cover.get("width") or 0,
                "height": cover.get("height") or 0,
                "aspect_ratio": cover.get("aspect_ratio") or 0.667,
                "vote_average": cover.get("vote_average") or 0,
                "vote_count": cover.get("vote_count") or 0,
                "language": cover.get("language"),
                "provider_name": cover.get("provider_name"),
                "provider_url": cover.get("provider_url"),
                "is_original": bool(cover.get("is_original")),
                "is_selected": absolute_url == selected_absolute,
            },
        )
    return {"item": item, "posters": posters, "current_poster": selected_url}


def music_cover_options(*, source, media_id, request=None, user=None):
    """Return approved front covers for a MusicBrainz release group."""
    media_type = MediaTypes.MUSIC.value
    if not _supports_music_posters(source, media_type):
        raise ValueError(POSTER_UNSUPPORTED_MESSAGE)

    item = _customizable_item(source=source, media_type=media_type, media_id=media_id)
    current = CustomPosterPreference.objects.filter(user=user, item=item).first()
    selected_url = current.custom_image_url if current else item.image
    selected_absolute = absolute_poster_url(request, selected_url)

    posters = []
    seen = set()
    for image in (musicbrainz.lookup_cover_art(media_id) or {}).get("images", []):
        if (
            not isinstance(image, dict)
            or image.get("approved") is False
            or "Front" not in (image.get("types") or [])
        ):
            continue
        url = _cover_art_https(image.get("image"))
        if not url or url in seen:
            continue
        seen.add(url)
        thumbnails = image.get("thumbnails") or {}
        posters.append(
            {
                "url": absolute_poster_url(request, url),
                "thumbnail_url": absolute_poster_url(
                    request,
                    _cover_art_https(
                        thumbnails.get("500")
                        or thumbnails.get("large")
                        or thumbnails.get("250")
                        or url,
                    ),
                ),
                "width": 0,
                "height": 0,
                "aspect_ratio": 1.0,
                "vote_average": 0,
                "vote_count": 0,
                "language": None,
                "is_original": bool(image.get("front")),
                "is_selected": False,
            },
        )

    if not posters:
        posters.append(
            {
                "url": absolute_poster_url(request, item.image),
                "thumbnail_url": absolute_poster_url(request, item.image),
                "width": 0,
                "height": 0,
                "aspect_ratio": 1.0,
                "vote_average": 0,
                "vote_count": 0,
                "language": None,
                "is_original": True,
                "is_selected": True,
            },
        )
    else:
        if not any(poster["is_original"] for poster in posters):
            posters[0]["is_original"] = True
        candidate_urls = {poster["url"] for poster in posters}
        if selected_absolute not in candidate_urls:
            selected_absolute = next(
                (poster["url"] for poster in posters if poster["is_original"]),
                posters[0]["url"],
            )
        for poster in posters:
            poster["is_selected"] = poster["url"] == selected_absolute

    return {"item": item, "posters": posters, "current_poster": selected_url}


def game_cover_options(*, source, media_id, request=None, user=None):
    """Return selectable covers for IGDB games."""
    if not _supports_game_posters(source, MediaTypes.GAME.value):
        raise ValueError(POSTER_UNSUPPORTED_MESSAGE)

    item = _customizable_item(source=source, media_type=MediaTypes.GAME.value, media_id=media_id)
    current = CustomPosterPreference.objects.filter(user=user, item=item).first()
    selected_url = current.custom_image_url if current else item.image
    selected_absolute = absolute_poster_url(request, selected_url)
    from app.providers import igdb, steamgriddb

    posters = []
    seen = set()
    covers = [
        {"url": item.image, "thumbnail_url": item.image, "is_original": True},
        *steamgriddb.get_game_posters(media_id),
        *igdb.get_game_covers(media_id),
    ]
    for cover in covers:
        url = cover.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        absolute_url = absolute_poster_url(request, url)
        posters.append(
            {
                "url": absolute_url,
                "thumbnail_url": absolute_poster_url(request, cover.get("thumbnail_url") or url),
                "width": cover.get("width") or 0,
                "height": cover.get("height") or 0,
                "aspect_ratio": cover.get("aspect_ratio") or 0.667,
                "vote_average": cover.get("vote_average") or 0,
                "vote_count": cover.get("vote_count") or 0,
                "language": cover.get("language"),
                "is_original": bool(cover.get("is_original")),
                "is_selected": absolute_url == selected_absolute,
            },
        )
    return {"item": item, "posters": posters, "current_poster": selected_url}


def backdrop_options(
    *,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
    request=None,
    user=None,
):
    """Return selectable backdrops for supported media."""
    if _supports_anime_artwork(source, media_type) or _supports_manga_backdrops(
        source,
        media_type,
    ):
        return enriched_backdrop_options(
            source=source,
            media_type=media_type,
            media_id=media_id,
            request=request,
            user=user,
        )
    if _supports_game_backdrops(source, media_type):
        return game_backdrop_options(source=source, media_id=media_id, request=request, user=user)
    if source != Sources.TMDB.value or media_type not in [
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
        MediaTypes.EPISODE.value,
    ]:
        raise ValueError(BACKDROP_UNSUPPORTED_MESSAGE)
    season_number, episode_number = _required_backdrop_coordinates(
        media_type,
        season_number,
        episode_number,
    )

    item = _customizable_item(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
    )
    metadata = provider_services.get_media_metadata(
        "tv_with_seasons" if media_type == MediaTypes.SEASON.value else media_type,
        media_id,
        source,
        [season_number]
        if media_type in [MediaTypes.SEASON.value, MediaTypes.EPISODE.value]
        else None,
        episode_number,
    )
    if media_type == MediaTypes.SEASON.value:
        metadata = metadata[f"season/{season_number}"]
    raw_default_url = backdrop_url(metadata)
    from app.providers import tmdb

    tmdb_backdrops = (
        tmdb.get_season_backdrop_images(media_id, season_number)
        if media_type == MediaTypes.SEASON.value
        else tmdb.get_episode_backdrop_images(
            media_id,
            season_number,
            episode_number,
        )
        if media_type == MediaTypes.EPISODE.value
        else tmdb.get_backdrop_images(media_id, media_type)
    )
    default_url, custom_url = resolved_backdrop_urls(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
        metadata=metadata,
        request=request,
        user=user,
        item=item,
        tmdb_backdrops=tmdb_backdrops,
    )
    selected_url = custom_url or default_url
    original = {
        "url": raw_default_url,
        "thumbnail_url": raw_default_url,
        "width": 0,
        "height": 0,
        "aspect_ratio": 1.778,
        "vote_average": 0,
        "vote_count": 0,
        "language": None,
        "is_original": True,
        "is_selected": raw_default_url == selected_url,
    }

    backdrops = [original] if raw_default_url else []
    for backdrop in tmdb_backdrops:
        if backdrop["url"] == raw_default_url:
            continue
        backdrops.append(
            {
                **backdrop,
                "language": backdrop.get("language"),
                "is_original": False,
                "is_selected": backdrop["url"] == selected_url,
            },
        )
    return {"backdrops": backdrops}


def enriched_backdrop_options(
    *,
    source,
    media_type,
    media_id,
    request=None,
    user=None,
):
    """Return enriched landscape artwork and the active backdrop."""
    if not (
        _supports_anime_artwork(source, media_type)
        or _supports_manga_backdrops(source, media_type)
    ):
        raise ValueError(BACKDROP_UNSUPPORTED_MESSAGE)

    item = _customizable_item(source=source, media_type=media_type, media_id=media_id)
    metadata = provider_services.get_media_metadata(media_type, media_id, source)
    metadata = (
        _enrich_anime_metadata(metadata, source)
        if media_type == MediaTypes.ANIME.value
        else _enrich_manga_metadata(metadata, source)
    )
    default_url, custom_url = resolved_backdrop_urls(
        source=source,
        media_type=media_type,
        media_id=media_id,
        metadata=metadata,
        request=request,
        user=user,
        item=item,
    )
    selected_url = custom_url or default_url
    candidates = list(metadata.get("backdrops") or [])
    if selected_url and selected_url not in {candidate.get("url") for candidate in candidates}:
        candidates.insert(
            0,
            {
                "url": selected_url,
                "thumbnail_url": selected_url,
                "is_original": False,
            },
        )

    backdrops = []
    for candidate in _dedupe_artwork(candidates):
        url = absolute_poster_url(request, candidate["url"])
        backdrops.append(
            {
                "url": url,
                "thumbnail_url": absolute_poster_url(
                    request,
                    candidate.get("thumbnail_url") or candidate["url"],
                ),
                "width": candidate.get("width") or 0,
                "height": candidate.get("height") or 0,
                "aspect_ratio": candidate.get("aspect_ratio") or 1.778,
                "vote_average": 0,
                "vote_count": 0,
                "language": candidate.get("language"),
                "provider_name": candidate.get("provider_name"),
                "provider_url": candidate.get("provider_url"),
                "is_original": bool(candidate.get("is_original", True)),
                "is_selected": url == selected_url,
            },
        )
    return {"backdrops": backdrops}


def game_backdrop_options(*, source, media_id, request=None, user=None):
    """Return selectable IGDB artwork images for game backdrops."""
    if not _supports_game_backdrops(source, MediaTypes.GAME.value):
        raise ValueError(BACKDROP_UNSUPPORTED_MESSAGE)

    item = _customizable_item(source=source, media_type=MediaTypes.GAME.value, media_id=media_id)
    metadata = provider_services.get_media_metadata(MediaTypes.GAME.value, media_id, source)
    raw_default_url = backdrop_url(metadata)
    from app.providers import igdb, steamgriddb

    igdb_backdrops = igdb.get_game_backdrops(media_id)
    steamgriddb_backdrops = steamgriddb.get_game_backdrops(media_id)
    default_url, custom_url = resolved_backdrop_urls(
        source=source,
        media_type=MediaTypes.GAME.value,
        media_id=media_id,
        metadata=metadata,
        request=request,
        user=user,
        item=item,
    )
    selected_url = custom_url or default_url
    backdrops = []
    seen = set()
    candidates = [
        {
            "url": raw_default_url,
            "thumbnail_url": raw_default_url,
            "width": 0,
            "height": 0,
            "aspect_ratio": 1.778,
            "vote_average": 0,
            "vote_count": 0,
            "language": None,
            "is_original": True,
        },
        *steamgriddb_backdrops,
        *igdb_backdrops,
    ]
    for candidate in candidates:
        url = candidate.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        backdrops.append(
            {
                **candidate,
                "language": candidate.get("language"),
                "is_original": bool(candidate.get("is_original")),
                "is_selected": url == selected_url,
            },
        )
    return {"backdrops": backdrops}


def save_backdrop_preference(
    *,
    source,
    media_type,
    media_id,
    backdrop_url,
    user,
    season_number=None,
    episode_number=None,
):
    """Save a user's backdrop preference without mutating the item poster."""
    if (
        source != Sources.TMDB.value or media_type not in [
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.EPISODE.value,
        ]
    ) and not _supports_game_backdrops(
        source,
        media_type,
    ) and not _supports_anime_artwork(
        source,
        media_type,
    ) and not _supports_manga_backdrops(source, media_type):
        raise ValueError(BACKDROP_UNSUPPORTED_MESSAGE)
    season_number, episode_number = _required_backdrop_coordinates(
        media_type,
        season_number,
        episode_number,
    )
    if not backdrop_url:
        raise ValueError("backdrop_url is required.")

    item = _customizable_item(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
    )
    CustomBackdropPreference.objects.update_or_create(
        user=user,
        item=item,
        defaults={"custom_image_url": backdrop_url},
    )
    return {
        "backdrop_url": backdrop_url,
        "custom_backdrop_url": backdrop_url,
    }


def logo_options(*, source, media_type, media_id, request=None, user=None):
    """Return selectable title logos for supported media."""
    _require_logo_support(source, media_type)
    item = _customizable_item(source=source, media_type=media_type, media_id=media_id)
    provider_logos = _provider_logo_options(source, media_type, media_id)
    automatic = title_logo(source=source, media_type=media_type, media_id=media_id)
    preference = CustomLogoPreference.objects.filter(user=user, item=item).first()
    selected_url = preference.custom_image_url if preference else automatic.get("url") if automatic else None
    automatic_url = automatic.get("url") if automatic else None

    logos = [
        {
            **logo,
            "style": logo.get("style"),
            "is_original": logo["url"] == automatic_url,
            "is_selected": logo["url"] == selected_url,
        }
        for logo in provider_logos
    ]
    if selected_url and not any(logo["url"] == selected_url for logo in logos):
        logos.insert(
            0,
            {
                "url": absolute_url(request, selected_url),
                "thumbnail_url": absolute_url(request, selected_url),
                "width": 0,
                "height": 0,
                "aspect_ratio": None,
                "vote_average": 0,
                "vote_count": 0,
                "language": None,
                "style": None,
                "is_original": False,
                "is_selected": True,
            },
        )
    return {"logos": logos}


def save_logo_preference(*, source, media_type, media_id, logo_url, user):
    """Save a user's title logo preference."""
    _require_logo_support(source, media_type)
    _validate_logo_url(logo_url)
    item = _customizable_item(source=source, media_type=media_type, media_id=media_id)
    CustomLogoPreference.objects.update_or_create(
        user=user,
        item=item,
        defaults={"custom_image_url": logo_url},
    )
    try:
        matched = next(
            (logo for logo in _provider_logo_options(source, media_type, media_id) if logo["url"] == logo_url),
            None,
        )
    except provider_services.ProviderAPIError:
        matched = None
    return {
        "logo_url": logo_url,
        "custom_logo_url": logo_url,
        "logo_width": matched.get("width") if matched else None,
        "logo_height": matched.get("height") if matched else None,
        "logo_aspect_ratio": matched.get("aspect_ratio") if matched else None,
    }


def resolved_logo(*, source, media_type, media_id, request=None, user=None):
    """Return effective logo metadata and the viewer's custom URL."""
    automatic = title_logo(source=source, media_type=media_type, media_id=media_id)
    if not _supports_logo(source, media_type) or not user or not user.is_authenticated:
        return automatic, None

    item = Item.objects.filter(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=None,
        episode_number=None,
    ).first()
    preference = (
        CustomLogoPreference.objects.filter(user=user, item=item).first()
        if item is not None
        else None
    )
    if preference is None:
        return automatic, None

    custom_url = absolute_url(request, preference.custom_image_url)
    matched = next(
        (
            logo
            for logo in _provider_logo_options(source, media_type, media_id)
            if logo["url"] == preference.custom_image_url
        ),
        None,
    )
    if matched:
        return {**matched, "url": custom_url}, custom_url
    return {
        "url": custom_url,
        "width": None,
        "height": None,
        "aspect_ratio": None,
    }, custom_url


def _provider_logo_options(source, media_type, media_id):
    if source == Sources.TMDB.value:
        from app.providers import tmdb

        return tmdb.get_title_logos(media_id, media_type)
    from app.providers import steamgriddb

    return steamgriddb.get_game_logos(media_id)


def _supports_logo(source, media_type):
    return (
        source == Sources.TMDB.value
        and media_type in [MediaTypes.MOVIE.value, MediaTypes.TV.value]
    ) or (source == Sources.IGDB.value and media_type == MediaTypes.GAME.value)


def _require_logo_support(source, media_type):
    if not _supports_logo(source, media_type):
        raise ValueError(LOGO_UNSUPPORTED_MESSAGE)


def _validate_logo_url(logo_url):
    if not isinstance(logo_url, str) or not logo_url:
        raise ValueError("logo_url is required.")
    if len(logo_url) > CustomLogoPreference._meta.get_field("custom_image_url").max_length:
        raise ValueError("logo_url must be 500 characters or fewer.")
    try:
        URLValidator(schemes=["http", "https"])(logo_url)
    except ValidationError as error:
        raise ValueError("logo_url must be a valid absolute HTTP(S) URL.") from error


def _customizable_item(
    *,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    item = Item.objects.filter(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
    ).first()
    if item is not None:
        return item
    metadata = provider_services.get_media_metadata(
        "tv_with_seasons" if media_type == MediaTypes.SEASON.value else media_type,
        media_id,
        source,
        [season_number]
        if media_type in [MediaTypes.SEASON.value, MediaTypes.EPISODE.value]
        else None,
        episode_number,
    )
    if media_type == MediaTypes.SEASON.value:
        metadata = metadata[f"season/{season_number}"]
    return Item.objects.create(
        media_id=media_id,
        source=source,
        media_type=media_type,
        title=metadata.get("title") or metadata.get("name") or media_id,
        image=metadata.get("image") or settings.IMG_NONE,
        season_number=season_number,
        episode_number=episode_number,
    )


def _required_season_number(media_type, season_number):
    if media_type != MediaTypes.SEASON.value:
        return None
    if season_number in [None, ""]:
        raise ValueError("season_number is required for seasons.")
    return int(season_number)


def _required_backdrop_coordinates(media_type, season_number, episode_number):
    if media_type == MediaTypes.SEASON.value:
        return _required_season_number(media_type, season_number), None
    if media_type != MediaTypes.EPISODE.value:
        return None, None
    if season_number in [None, ""] or episode_number in [None, ""]:
        raise ValueError(
            "season_number and episode_number are required for episodes.",
        )
    try:
        return int(season_number), int(episode_number)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "season_number and episode_number must be integers.",
        ) from error


def _supports_book_posters(source, media_type):
    return media_type == MediaTypes.BOOK.value and source in [
        Sources.OPENLIBRARY.value,
        Sources.HARDCOVER.value,
    ]


def _supports_game_posters(source, media_type):
    return media_type == MediaTypes.GAME.value and source == Sources.IGDB.value


def _supports_music_posters(source, media_type):
    return (
        media_type == MediaTypes.MUSIC.value
        and source == Sources.MUSICBRAINZ.value
    )


def _supports_anime_artwork(source, media_type):
    return source == Sources.MAL.value and media_type == MediaTypes.ANIME.value


def _supports_manga_posters(source, media_type):
    return media_type == MediaTypes.MANGA.value and source in {
        Sources.MAL.value,
        Sources.MANGAUPDATES.value,
    }


def _supports_manga_backdrops(source, media_type):
    return _supports_manga_posters(source, media_type)


def _supports_game_backdrops(source, media_type):
    return media_type == MediaTypes.GAME.value and source == Sources.IGDB.value


def _book_isbns(source, media_id):
    if source == Sources.HARDCOVER.value:
        from app.providers import hardcover

        return hardcover.get_book_isbns(media_id)
    if source == Sources.OPENLIBRARY.value:
        metadata = provider_services.get_media_metadata(MediaTypes.BOOK.value, media_id, source)
        return metadata.get("details", {}).get("isbn", []) or []
    return []


def _book_cover_candidates(source, media_id, isbns):
    from app.providers import openlibrary

    try:
        if source == Sources.OPENLIBRARY.value:
            return asyncio.run(openlibrary.get_reliable_covers_for_book(media_id, isbns, cap=20))
        return asyncio.run(openlibrary.get_reliable_covers_by_isbns(isbns, cap=20))
    except (ClientError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        logger.warning("Reliable cover fetch failed, falling back to ISBN covers: %s", error)
        return openlibrary.get_book_cover_images(isbns)


def absolute_poster_url(request, url):
    """Return an absolute poster URL without substituting the global placeholder."""
    if not url:
        return None
    if str(url).startswith(("http://", "https://")):
        return url
    return request.build_absolute_uri(url) if request is not None else url


def _cover_art_https(url):
    return str(url).replace("http://coverartarchive.org", "https://coverartarchive.org", 1) if url else None


def backdrop_url(metadata):
    """Return an absolute backdrop URL when available."""
    value = metadata.get("backdrop") or metadata.get("backdrop_url") or metadata.get("backdrop_path")
    if not value:
        for artwork in metadata.get("artworks") or []:
            if isinstance(artwork, dict) and artwork.get("image_id"):
                return f"https://images.igdb.com/igdb/image/upload/t_original/{artwork['image_id']}.jpg"
    if isinstance(value, str) and value.startswith("/"):
        return f"https://image.tmdb.org/t/p/original{value}"
    return value


def _game_default_backdrop_url(media_id, raw_default_url=None):
    if raw_default_url:
        return raw_default_url
    from app.providers import igdb, steamgriddb

    for backdrop in [*steamgriddb.get_game_backdrops(media_id), *igdb.get_game_backdrops(media_id)]:
        if backdrop.get("url"):
            return backdrop["url"]
    return None


def resolved_backdrop_urls(
    *,
    source,
    media_type,
    media_id,
    metadata,
    season_number=None,
    episode_number=None,
    request=None,
    user=None,
    item=None,
    tmdb_backdrops=None,
):
    """Return the resolved default backdrop and viewer custom backdrop."""
    raw_default_url = backdrop_url(metadata)
    if _supports_game_backdrops(source, media_type):
        raw_default_url = _game_default_backdrop_url(media_id, raw_default_url)
    if _supports_anime_artwork(source, media_type) or _supports_manga_backdrops(
        source,
        media_type,
    ):
        item = item or Item.objects.filter(
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=None,
            episode_number=None,
        ).first()
        return (
            _curated_backdrop_url(item, request=request) or raw_default_url,
            _backdrop_preference_url(user, item, request=request),
        )
    if source != Sources.TMDB.value or media_type not in [
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
        MediaTypes.EPISODE.value,
    ]:
        return raw_default_url, custom_backdrop_url_for_user(
            user,
            {
                "source": source,
                "media_type": media_type,
                "media_id": media_id,
                "season_number": season_number,
                "episode_number": episode_number,
            },
            request=request,
        )

    item = item or Item.objects.filter(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
    ).first()
    custom_url = _backdrop_preference_url(user, item, request=request)
    if media_type == MediaTypes.EPISODE.value:
        default_url = (
            _curated_backdrop_url(item, request=request)
            or raw_default_url
            or (tmdb_backdrops[0]["url"] if tmdb_backdrops else None)
        )
        return default_url, custom_url
    if media_type == MediaTypes.SEASON.value:
        if tmdb_backdrops is None and season_number is not None:
            from app.providers import tmdb

            tmdb_backdrops = tmdb.get_season_backdrop_images(media_id, season_number)
        default_url = (
            _curated_backdrop_url(item, request=request)
            or (tmdb_backdrops[0]["url"] if tmdb_backdrops else None)
            or raw_default_url
        )
        return default_url, custom_url
    default_url = (
        _curated_backdrop_url(item, request=request)
        or _tmdb_vote_sorted_backdrop_url(
            media_id,
            media_type,
            raw_default_url=raw_default_url,
            tmdb_backdrops=tmdb_backdrops,
        )
    )
    return default_url, custom_url


def _backdrop_preference_url(user, item, request=None):
    if not user or not user.is_authenticated or item is None:
        return None
    preference = CustomBackdropPreference.objects.filter(user=user, item=item).first()
    return absolute_url(request, preference.custom_image_url) if preference else None


def _curated_backdrop_url(item, request=None):
    curator_username = getattr(settings, "SPINE_CURATOR_USERNAME", None)
    if not curator_username or item is None:
        return None
    preference = CustomBackdropPreference.objects.filter(
        user__username=curator_username,
        item=item,
    ).first()
    return absolute_url(request, preference.custom_image_url) if preference else None


def _tmdb_vote_sorted_backdrop_url(media_id, media_type, *, raw_default_url=None, tmdb_backdrops=None):
    if tmdb_backdrops is None:
        from app.providers import tmdb

        tmdb_backdrops = tmdb.get_backdrop_images(media_id, media_type)
    tmdb_backdrops = [backdrop for backdrop in tmdb_backdrops if backdrop.get("language") is None]
    if len(tmdb_backdrops) > 1:
        return tmdb_backdrops[1]["url"]
    if tmdb_backdrops:
        return raw_default_url or tmdb_backdrops[0]["url"]
    return raw_default_url


def title_logo(*, source, media_type, media_id):
    """Return title logo metadata for supported detail responses."""
    if source == Sources.TMDB.value and media_type in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        from app.providers import tmdb

        return tmdb.get_title_logo(media_id, media_type)
    if source == Sources.IGDB.value and media_type == MediaTypes.GAME.value:
        from app.providers import steamgriddb

        return steamgriddb.get_game_logo(media_id)
    return None


def poster_accent_color(metadata, ref):
    """Compute an accent only when there is no stored Item color."""
    item = find_item(ref)
    if item is not None and item.poster_accent_color:
        return item.poster_accent_color
    return None


def watch_providers_for_user(metadata, user):
    """Return provider availability, region-filtered for authenticated users."""
    providers = metadata.get("providers")
    if not providers:
        return providers
    region = getattr(user, "watch_provider_region", None) if user and user.is_authenticated else None
    if not region or region == "UNSET":
        return providers
    from app.providers import tmdb

    return tmdb.filter_providers(deepcopy(providers), region)


def enrich_episodes(metadata, source, user):
    """Apply provider episode formatting when tracking rows exist."""
    if not metadata.get("episodes"):
        return metadata
    from app.models import BasicMedia
    from app.providers import manual, tmdb

    episodes_in_db = []
    if user and user.is_authenticated:
        current = BasicMedia.objects.filter_media(
            user,
            metadata.get("media_id"),
            MediaTypes.SEASON.value,
            source,
            metadata.get("season_number"),
        ).first()
        episodes_in_db = current.episodes.all() if current else []

    payload = dict(metadata)
    if source == "manual":
        payload["episodes"] = manual.process_episodes(metadata, episodes_in_db)
    elif source == "tmdb":
        payload["episodes"] = tmdb.process_episodes(metadata, episodes_in_db)
    return payload


def _normalized_external_rating(*, metadata, rating_source, rating, media_type, media_id):
    """Normalize a provider-supplied rating or link-only rating hint."""
    value = rating.get("value") or rating.get("score")
    url = _third_party_rating_url(
        metadata=metadata,
        rating_source=rating_source,
        rating=rating,
        media_type=media_type,
        media_id=media_id,
    )
    if value is None and url is None:
        return None
    return {
        "source": source_label(rating_source),
        "value": str(value) if value is not None else "",
        "vote_count": rating.get("vote_count", rating.get("votes")),
        "max_value": str(rating.get("max_value") or max_rating_value(rating_source)),
        "url": url,
    }


def _provider_external_ratings(
    *,
    metadata,
    source,
    media_type,
    media_id,
    season_number,
    episode_number,
):
    """Return provider ratings with optional exact-episode IMDb enrichment."""
    provider_ratings = {
        rating_source: dict(rating)
        for rating_source, rating in (metadata.get("external_ratings") or {}).items()
    }
    if source != Sources.TMDB.value or media_type != MediaTypes.EPISODE.value:
        return provider_ratings

    from app.providers import imdb

    imdb_rating = imdb.get_title_rating(metadata.get("imdb_id"))
    if not imdb_rating:
        return provider_ratings

    provider_ratings["imdb"] = {
        **provider_ratings.get("imdb", {}),
        **imdb_rating,
    }
    return provider_ratings


def _compact_decimal(value):
    text = format(value, "f").rstrip("0").rstrip(".")
    return text or "0"


def _stored_external_ratings(item, metadata):
    ratings = {}
    if metadata.get("score") is not None and rating_source_is_exposed(item.source):
        ratings[item.source] = {
            "source": source_label(item.source),
            "value": str(metadata["score"]),
            "vote_count": metadata.get("score_count"),
            "max_value": max_rating_value(item.source),
            "url": _provider_rating_url(
                metadata=metadata,
                source=item.source,
                media_type=item.media_type,
                media_id=item.media_id,
                season_number=item.season_number,
                episode_number=item.episode_number,
            ),
        }

    for rating_source, rating in (metadata.get("external_ratings") or {}).items():
        if not rating_source_is_exposed(rating_source):
            continue
        normalized = _normalized_external_rating(
            metadata=metadata,
            rating_source=rating_source,
            rating=rating,
            media_type=item.media_type,
            media_id=item.media_id,
        )
        if normalized is not None:
            ratings[rating_source] = normalized

    source_order = {source: index for index, source in enumerate(RATING_SOURCES)}
    stored = sorted(
        item.external_ratings.exclude(value=None),
        key=lambda rating: (
            source_order.get(rating.rating_source, len(source_order)),
            rating.rating_source,
        ),
    )
    for rating in stored:
        if not rating_source_is_exposed(rating.rating_source):
            continue
        if rating.rating_source == item.source and metadata.get("score") is not None:
            continue
        value = _compact_decimal(rating.value)
        if rating.rating_source in {"steam", "tomatoes"}:
            value = f"{value}%"
        max_value = (
            max_rating_value(rating.rating_source)
            if rating.rating_source in RATING_SOURCES
            else _compact_decimal(rating.max_value)
        )
        ratings[rating.rating_source] = {
            "source": source_label(rating.rating_source),
            "value": value,
            "vote_count": rating.vote_count,
            "max_value": max_value,
            "url": rating.canonical_url,
        }

    return [
        ratings[source]
        for source in sorted(
            ratings,
            key=lambda source: (source_order.get(source, len(source_order)), source),
        )
    ]


def _external_rating_preparation(item, pending, *, enqueue_failed=False):
    eligible = set(eligible_rating_sources(item))
    failed = item.external_ratings.filter(
        rating_source__in=eligible,
        status=ExternalRating.Status.FAILED,
    ).exists()
    if enqueue_failed or failed:
        state = "degraded"
    elif pending:
        state = "pending"
    else:
        state = "ready"
    return {
        "state": state,
        "retry_after_seconds": 2,
    }


def _enqueue_detail_external_ratings(item, pending):
    if not pending:
        return False
    lock_key = f"external-ratings:detail:{item.pk}"
    try:
        acquired = cache.add(
            lock_key,
            1,
            timeout=DETAIL_RATING_PREPARATION_LOCK_TIMEOUT,
        )
    except (ConnectionInterrupted, RedisError, ConnectionError, OSError):
        acquired = True
    if not acquired:
        return False

    from app.tasks import enrich_external_ratings

    try:
        enrich_external_ratings.delay(item.pk, pending)
    except (KombuOperationalError, RedisError, ConnectionError, OSError) as error:
        with suppress(ConnectionInterrupted, RedisError, ConnectionError, OSError):
            cache.delete(lock_key)
        logger.warning(
            "External rating detail enqueue failed item=%s type=%s",
            item.pk,
            type(error).__name__,
        )
        return True
    return False


def _cached_external_rating_payload(item, metadata=None):
    metadata = metadata or {}
    pending = rating_sources_needing_refresh(item)
    enqueue_failed = _enqueue_detail_external_ratings(item, pending)
    return {
        "external_ratings": _stored_external_ratings(item, metadata),
        "external_ratings_preparation": _external_rating_preparation(
            item,
            pending,
            enqueue_failed=enqueue_failed,
        ),
    }


def _tracked_external_rating_payload(item, metadata):
    """Persist native metadata, queue optional work, and return cached ratings."""
    if item.source in RATING_SOURCES:
        refresh_external_ratings(item, [item.source], metadata=metadata)
    return _cached_external_rating_payload(item, metadata)


def _tracked_external_ratings(item, metadata):
    return _tracked_external_rating_payload(item, metadata)["external_ratings"]


def external_rating_payload(
    *,
    metadata,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
    item=None,
):
    """Return detail ratings with their asynchronous preparation state."""
    if item is not None:
        return _tracked_external_rating_payload(item, metadata)
    return {
        "external_ratings": external_ratings(
            metadata=metadata,
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
        ),
        "external_ratings_preparation": {
            "state": "ready",
            "retry_after_seconds": 2,
        },
    }


def media_external_rating_payload(
    *,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return cached ratings and preparation state for one exact identity."""
    if media_type == MediaTypes.EPISODE.value and (
        season_number in (None, "") or episode_number in (None, "")
    ):
        raise ValueError("season_number and episode_number are required for episodes.")
    season_number = int(season_number) if season_number not in (None, "") else None
    episode_number = int(episode_number) if episode_number not in (None, "") else None
    item = Item.objects.filter(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
    ).first()
    if item is None:
        return {
            "external_ratings": [],
            "external_ratings_preparation": {
                "state": "ready",
                "retry_after_seconds": 2,
            },
        }
    return _cached_external_rating_payload(item)


def external_ratings(
    *,
    metadata,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
    item=None,
):
    """Normalize provider and third-party ratings for media detail."""
    if item is not None:
        return _tracked_external_ratings(item, metadata)

    ratings = []
    score = metadata.get("score")
    if score is not None:
        ratings.append(
            {
                "source": source_label(source),
                "value": str(score),
                "vote_count": metadata.get("score_count"),
                "max_value": max_rating_value(source),
                "url": _provider_rating_url(
                    metadata=metadata,
                    source=source,
                    media_type=media_type,
                    media_id=media_id,
                    season_number=season_number,
                    episode_number=episode_number,
                ),
            },
        )

    provider_ratings = _provider_external_ratings(
        metadata=metadata,
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
    )

    for rating_source, rating in provider_ratings.items():
        normalized = _normalized_external_rating(
            metadata=metadata,
            rating_source=rating_source,
            rating=rating,
            media_type=media_type,
            media_id=media_id,
        )
        if normalized is not None:
            ratings.append(normalized)

    if source == "tmdb" and media_type in {MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.SEASON.value}:
        from app.providers import mdblist

        mdblist_type = MediaTypes.TV.value if media_type == MediaTypes.SEASON.value else media_type
        mdblist_ratings = mdblist.get_media_ratings(media_id, mdblist_type) or {}
        for rating_source, rating in mdblist_ratings.items():
            normalized = _normalized_external_rating(
                metadata=metadata,
                rating_source=rating_source,
                rating=rating,
                media_type=media_type,
                media_id=media_id,
            )
            if normalized is not None:
                ratings.append(normalized)

    if source == Sources.IGDB.value and media_type == MediaTypes.GAME.value:
        from app.providers import steam

        metacritic = steam.get_metacritic_rating(media_id)
        if metacritic:
            ratings.append(
                {
                    "source": source_label("metacritic"),
                    "value": str(metacritic["value"]),
                    "vote_count": None,
                    "max_value": max_rating_value("metacritic"),
                    "url": _normalize_rating_url("metacritic", metacritic.get("url")),
                },
            )

    return ratings


def tv_seasons(*, source, media_id, request=None, user=None):
    """Return season summaries for a TV show."""
    detail = media_detail(
        source=source,
        media_type=MediaTypes.TV.value,
        media_id=media_id,
        request=request,
        user=user,
    )
    return {
        "seasons": detail.get("seasons", []),
        "completion": detail.get("completion"),
    }


def season_detail(*, source, media_id, season_number, request=None, user=None):
    """Return normalized season detail."""
    return media_detail(
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        season_number=season_number,
        request=request,
        user=user,
    )


def season_episodes(*, source, media_id, season_number, request=None, user=None):
    """Return episode summaries for a season."""
    detail = season_detail(
        source=source,
        media_id=media_id,
        season_number=season_number,
        request=request,
        user=user,
    )
    return {
        "episodes": detail.get("episodes", []),
        "completion": detail.get("completion"),
    }


def community_stats(
    *,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return current community aggregates for a media identity."""
    from app.models import DiaryEntry, Item, MediaLike

    item = Item.objects.filter(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
        episode_number=episode_number,
    ).first()
    if item is None:
        return {
            "average_rating": None,
            "rating_count": 0,
            "diary_count": 0,
            "review_count": 0,
            "liked_count": 0,
            "rating_distribution": [],
        }
    entries = DiaryEntry.objects.filter(item=item)
    if media_type in {MediaTypes.MOVIE.value, MediaTypes.MUSIC.value}:
        # Single-weight privacy is account-level; legacy per-entry visibility is
        # intentionally ignored for these media types.
        entries = entries.filter(user__profile_private=False)
    else:
        entries = entries.exclude(visibility="private")
    rating_values = [entry.rating for entry in entries if entry.rating is not None]
    if single_weight.uses_half_star_rating(media_type):
        rating_values = [single_weight.rating_to_wire(value) for value in rating_values]
    average = round(sum(rating_values) / len(rating_values), 2) if rating_values else None
    distribution = [
        {
            "rating": str(
                bucket["rating"] / 2
                if single_weight.uses_half_star_rating(media_type)
                else bucket["rating"]
            ),
            "count": bucket["count"],
        }
        for bucket in entries.exclude(rating__isnull=True)
        .values("rating")
        .annotate(count=Count("id"))
        .order_by("rating")
    ]
    liked = MediaLike.objects.filter(item=item)
    if media_type in {MediaTypes.MOVIE.value, MediaTypes.MUSIC.value}:
        liked = liked.filter(user__profile_private=False)
    return {
        "average_rating": str(average) if average is not None else None,
        "rating_count": len(rating_values),
        "diary_count": entries.count(),
        "review_count": entries.exclude(review="").count(),
        "liked_count": liked.count(),
        "rating_distribution": distribution,
    }
