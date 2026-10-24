"""Celery tasks for streaming availability."""

import json
import logging
from datetime import timedelta
from itertools import batched

from celery import shared_task
from django.contrib.auth import get_user_model
from django.utils import timezone

from streaming import library, sync
from streaming.models import StreamingAvailability
from streaming.services import active_regions, effective_region, region_names

logger = logging.getLogger(__name__)

REFRESH_BATCH_SIZE = 100
# Titles one library check may queue; the periodic task picks up the rest.
LIBRARY_CHECK_LIMIT = 1000
# Titles the periodic task queues per run.
STALE_QUEUE_LIMIT = 1000
# The periodic task deletes snapshots this old in countries that are no longer
# active, in batches of this many rows.
PRUNE_AFTER = timedelta(days=30)
PRUNE_BATCH_SIZE = 1000


@shared_task(name="Refresh streaming availability")
def refresh_streaming_titles(titles, *, force=False):
    """Refresh snapshots for up to 100 titles in every active region.

    ``titles`` is a list of ``[title_type, tmdb_id]`` pairs, e.g.
    ``[["movie", "603"], ["tv", "1396"]]``. Titles already fresh in every
    active region are skipped unless ``force``.
    """
    titles = list(titles)
    if len(titles) > REFRESH_BATCH_SIZE:
        msg = f"Streaming refresh batches are limited to {REFRESH_BATCH_SIZE} titles"
        raise ValueError(msg)
    result = sync.refresh_titles(titles, force=force)
    logger.info("streaming_refresh_summary %s", json.dumps(result, sort_keys=True))
    return result


def enqueue_refresh_batches(titles, *, force=False):
    """Queue refresh tasks for titles in batches of 100; return the batch count."""
    titles = list(dict.fromkeys((title_type, str(tmdb_id)) for title_type, tmdb_id in titles))
    batches = 0
    for batch in batched(titles, REFRESH_BATCH_SIZE):
        refresh_streaming_titles.delay([list(title) for title in batch], force=force)
        batches += 1
    return batches


@shared_task(name="Check streaming library")
def check_user_streaming_library(user_id):
    """Queue refreshes for a user's titles missing or stale in their country.

    Runs after the user saves streaming settings (ST-102). Covers tracked,
    listed, and diary movies and shows (seasons and episodes map to the show)
    and does nothing when availability is off or the country is not active.
    """
    user = get_user_model().objects.filter(pk=user_id).first()
    region = effective_region(user) if user is not None else None
    result = {"user_id": user_id, "region": region}
    if region is None or region not in active_regions():
        result["status"] = "skipped"
    else:
        titles = library.library_titles(user)
        missing, stale = library.titles_needing_refresh(titles, {region})
        selected = (missing + stale)[:LIBRARY_CHECK_LIMIT]
        result.update(
            status="queued",
            titles=len(titles),
            missing=len(missing),
            stale=len(stale),
            selected=len(selected),
            batches=enqueue_refresh_batches(selected),
        )
    logger.info("streaming_library_check_summary %s", json.dumps(result, sort_keys=True))
    return result


def _prune_inactive_snapshots(regions):
    """Delete snapshots older than PRUNE_AFTER outside ``regions``; return the count.

    Nothing refreshes snapshots in countries that are no longer active, so
    they only go stale. Offers are deleted with their snapshot.
    """
    old = StreamingAvailability.objects.filter(
        fetched_at__lt=timezone.now() - PRUNE_AFTER,
    ).exclude(region__in=regions)
    deleted = 0
    while ids := list(old.values_list("pk", flat=True)[:PRUNE_BATCH_SIZE]):
        StreamingAvailability.objects.filter(pk__in=ids).delete()
        deleted += len(ids)
    return deleted


@shared_task(name="Queue stale streaming availability")
def queue_stale_streaming_availability():
    """Queue a bounded refresh of library, list, and diary titles in active countries.

    Titles missing a snapshot come first, then the oldest stale ones (ST-400).
    Old snapshots in countries that are no longer active are deleted.
    """
    regions = active_regions()
    titles = library.library_titles()
    missing, stale = library.titles_needing_refresh(titles, regions)
    selected = (missing + stale)[:STALE_QUEUE_LIMIT]
    result = {
        "regions": sorted(regions),
        "titles": len(titles),
        "missing": len(missing),
        "stale": len(stale),
        "selected": len(selected),
        "batches": enqueue_refresh_batches(selected),
        "pruned": _prune_inactive_snapshots(regions),
    }
    try:
        # Keeps country names cached for detail pages, which never fetch them.
        region_names()
    except Exception as error:  # noqa: BLE001 - names are a best-effort warm-up
        logger.warning("Streaming region names refresh failed type=%s", type(error).__name__)
    logger.info("streaming_stale_queue_summary %s", json.dumps(result, sort_keys=True))
    return result
