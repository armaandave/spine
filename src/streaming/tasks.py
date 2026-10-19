"""Celery tasks for streaming availability."""

import json
import logging
from itertools import batched

from celery import shared_task
from django.contrib.auth import get_user_model

from streaming import library, sync
from streaming.services import active_regions, effective_region, region_names

logger = logging.getLogger(__name__)

REFRESH_BATCH_SIZE = 100
# Titles one library check may queue; the periodic task picks up the rest.
LIBRARY_CHECK_LIMIT = 1000
# Titles the periodic task queues per run.
STALE_QUEUE_LIMIT = 1000


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

    Runs after the user saves streaming settings (ST-102). Covers tracked and
    listed movies and shows (seasons and episodes map to the show) and does
    nothing when availability is off or the country is not active.
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


@shared_task(name="Queue stale streaming availability")
def queue_stale_streaming_availability():
    """Queue a bounded refresh of library and list titles in active countries.

    Titles missing a snapshot come first, then the oldest stale ones (ST-400).
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
    }
    try:
        # Keeps country names cached for detail pages, which never fetch them.
        region_names()
    except Exception as error:  # noqa: BLE001 - names are a best-effort warm-up
        logger.warning("Streaming region names refresh failed type=%s", type(error).__name__)
    logger.info("streaming_stale_queue_summary %s", json.dumps(result, sort_keys=True))
    return result
