"""Service filter for collections, filter options, and Discover (ST-300 to ST-302).

Contract: docs/streaming-availability-contract.md
"""

import logging
from itertools import batched

from django.core.cache import cache
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError
from rest_framework.exceptions import ValidationError

from api.services.filters import values
from app.models import MediaTypes, Sources
from streaming.models import (
    STREAMABLE_MONETIZATIONS,
    StreamingAvailability,
    StreamingOffer,
    StreamingProvider,
)
from streaming.services import (
    MAX_SERVICES,
    active_regions,
    effective_region,
    logo_url,
    region_name,
    title_key,
    user_provider_ids,
)

logger = logging.getLogger(__name__)

MINE = "mine"
TV_MEDIA_TYPES = (
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.EPISODE.value,
)
# Item media types with availability; seasons and episodes use their show's (ST-002).
STREAMABLE_MEDIA_TYPES = (MediaTypes.MOVIE.value, *TV_MEDIA_TYPES)
MAX_PROVIDER_ID = 2_147_483_647
# Unchecked titles queued per request, how long a user's queueing is debounced,
# and how many titles one refresh task takes.
PENDING_CHECK_LIMIT = 500
PENDING_CHECK_DEBOUNCE = 600
REFRESH_BATCH_SIZE = 100


def parse_service_param(params):
    """Return None, MINE, or sorted provider IDs from ``service``.

    Raises a DRF ValidationError for anything else.
    """
    raw_values = [value.strip() for value in values(params, "service")]
    raw_values = [value for value in raw_values if value]
    if not raw_values:
        return None
    if all(value.lower() == MINE for value in raw_values):
        return MINE
    provider_ids = set()
    for value in raw_values:
        if not (value.isascii() and value.isdigit() and len(value) <= 10) or not 0 < int(value) <= MAX_PROVIDER_ID:
            raise ValidationError({"service": ["Use mine or streaming provider IDs."]})
        provider_ids.add(int(value))
    if len(provider_ids) > MAX_SERVICES:
        raise ValidationError({"service": [f"Choose at most {MAX_SERVICES} services."]})
    return sorted(provider_ids)


def selected_provider_ids(selection, user):
    """Return the provider IDs a parsed ``service`` selection stands for."""
    return user_provider_ids(user) if selection == MINE else list(selection)


def streamable_filter(region, provider_ids, *, item_path="item__"):
    """Return a Q for items streamable on any of ``provider_ids`` in ``region``."""

    def offers(title_type):
        return StreamingOffer.objects.filter(
            availability__title_type=title_type,
            availability__tmdb_id=OuterRef(f"{item_path}media_id"),
            availability__region=region,
            provider_id__in=provider_ids,
            monetization__in=STREAMABLE_MONETIZATIONS,
        )

    return _title_has(offers, item_path)


def _title_has(rows_for, item_path):
    """Return a Q for items whose title has a row in ``rows_for(title_type)``.

    Movies use movie rows; shows, seasons, and episodes use the show's (ST-002).
    Other items never match.
    """
    movie = Q(**{f"{item_path}media_type": MediaTypes.MOVIE.value})
    tv = Q(**{f"{item_path}media_type__in": TV_MEDIA_TYPES})
    return Q(**{f"{item_path}source": Sources.TMDB.value}) & (
        (movie & Exists(rows_for(StreamingAvailability.TitleType.MOVIE)))
        | (tv & Exists(rows_for(StreamingAvailability.TitleType.TV)))
    )


def apply_service_filter(queryset, params, *, user, request, item_path="item__"):
    """Apply the Service filter to an Item-backed queryset (ST-300, ST-301).

    Returns ``(queryset, streaming)``. ``streaming`` is the response block, or
    None when the request has no ``service``. Titles never checked for the
    country are excluded, counted as pending, and queued for a check.
    """
    selection = parse_service_param(params)
    if selection is None:
        return queryset, None
    region = effective_region(user, request)
    if region is None:
        return queryset.none(), {"region": None, "pending_count": 0}
    pending_count = check_pending_titles(queryset, region, user=user, item_path=item_path)
    provider_ids = selected_provider_ids(selection, user)
    if provider_ids:
        queryset = queryset.filter(streamable_filter(region, provider_ids, item_path=item_path))
    else:
        queryset = queryset.none()
    return queryset, {"region": region, "pending_count": pending_count}


def check_pending_titles(queryset, region, *, user, item_path="item__"):
    """Count titles in ``queryset`` with no snapshot for ``region`` and queue checks.

    Checks are only queued for countries that get snapshots (ST-400), at most
    PENDING_CHECK_LIMIT per request, debounced per user and country, after commit.
    """
    titles = unchecked_titles(queryset, region, item_path=item_path)
    if titles and region in active_regions() and _claim_pending_check(user, region):
        queued = [list(title) for title in titles[:PENDING_CHECK_LIMIT]]
        # A plain callable, not functools.partial: Django's robust on_commit
        # error logging reads __qualname__, which partial objects lack.
        transaction.on_commit(lambda: _queue_title_checks(queued), robust=True)
    return len(titles)


def unchecked_titles(queryset, region, *, item_path="item__"):
    """Return sorted ``(title_type, tmdb_id)`` keys never checked for ``region``."""
    keys = {
        title_key(Sources.TMDB.value, media_type, media_id)
        for media_type, media_id in _unchecked_rows(queryset, region, item_path)
    }
    return sorted(key for key in keys if key)


def _unchecked_rows(queryset, region, item_path):
    # Driven by the collection's own rows (not the global Item table) so the cost
    # follows the collection size.
    def snapshots(title_type):
        return StreamingAvailability.objects.filter(
            title_type=title_type,
            tmdb_id=OuterRef(f"{item_path}media_id"),
            region=region,
        )

    return (
        queryset.filter(
            **{
                f"{item_path}source": Sources.TMDB.value,
                f"{item_path}media_type__in": STREAMABLE_MEDIA_TYPES,
            },
        )
        .exclude(_title_has(snapshots, item_path))
        .order_by()
        .values_list(f"{item_path}media_type", f"{item_path}media_id")
    )


def _claim_pending_check(user, region):
    try:
        return cache.add(
            f"streaming:pending-check:v1:{user.pk}:{region}",
            1,
            timeout=PENDING_CHECK_DEBOUNCE,
        )
    except (ConnectionInterrupted, RedisError, ConnectionError, OSError) as error:
        logger.warning("Streaming pending check lock unavailable type=%s", type(error).__name__)
        return False


def _queue_title_checks(titles):
    from streaming.tasks import refresh_streaming_titles

    for batch in batched(titles, REFRESH_BATCH_SIZE):
        refresh_streaming_titles.delay(list(batch))


def scope_has_titles(queryset, params=None, *, item_path="item__"):
    """Return True when a collection holds a TMDB movie, show, season, or episode."""
    media_types = values(params or {}, "media_type")
    if media_types:
        queryset = queryset.filter(**{f"{item_path}media_type__in": media_types})
    return queryset.filter(
        **{
            f"{item_path}source": Sources.TMDB.value,
            f"{item_path}media_type__in": STREAMABLE_MEDIA_TYPES,
        },
    ).exists()


def filter_options(user, request, *, available):
    """Return the ``streaming`` block for ``GET /filter-options/``."""
    region = effective_region(user, request)
    services = StreamingProvider.objects.filter(subscribers__user=user).order_by(
        "display_priority",
        "name",
    )
    return {
        "available": available,
        "region": region,
        "region_name": region_name(region),
        "services": [
            {
                "value": str(provider.id),
                "label": provider.name,
                "logo_url": logo_url(provider.logo_path),
            }
            for provider in services
        ],
    }
