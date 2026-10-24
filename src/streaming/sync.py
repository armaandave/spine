"""Store streaming availability snapshots (ST-400).

Snapshots are written only for active regions. Detail pages only fill in
missing ones from metadata they already fetched, stamped as already stale;
background tasks call TMDB and keep them fresh.
"""

import logging
from collections import Counter
from datetime import timedelta

from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError

from app.models import MediaTypes
from app.providers.services import ProviderAPIError
from streaming import tmdb
from streaming.models import StreamingAvailability, StreamingOffer
from streaming.services import FRESH_FOR, active_regions, normalize_region, title_key

logger = logging.getLogger(__name__)

REFRESH_LOCK_TIMEOUT = 60 * 15
STORE_ATTEMPTS = 3
REFRESH_STATUSES = ("refreshed", "fresh", "deduplicated", "skipped", "failed")
# Detail metadata can be about two days old (two stacked 24-hour caches), so
# snapshots made from it start out stale and the next periodic run replaces them.
METADATA_SNAPSHOT_AGE = FRESH_FOR + timedelta(seconds=1)


def _regions(regions):
    return {code for code in map(normalize_region, regions) if code}


def regions_needing_refresh(title_type, tmdb_id, regions, *, now=None):
    """Return the regions whose snapshot of the title is missing or stale."""
    regions = _regions(regions)
    if not regions:
        return set()
    cutoff = (now or timezone.now()) - FRESH_FOR
    fresh = StreamingAvailability.objects.filter(
        title_type=title_type,
        tmdb_id=str(tmdb_id),
        region__in=regions,
        fetched_at__gte=cutoff,
    ).values_list("region", flat=True)
    return regions - set(fresh)


def _write_snapshots(title_type, tmdb_id, snapshots, *, fetched_at=None, replace=True):
    fetched_at = fetched_at or timezone.now()
    lookup = {"title_type": title_type, "tmdb_id": tmdb_id, "region__in": list(snapshots)}
    rows = {
        row.region: row
        for row in StreamingAvailability.objects.select_for_update()
        .filter(**lookup)
        .order_by("region")
    }
    if not replace:
        # Existing rows stay as they are; only missing regions are written.
        snapshots = {region: snapshot for region, snapshot in snapshots.items() if region not in rows}
        rows = {}
    for region, row in rows.items():
        row.link = snapshots[region][0]
        row.fetched_at = fetched_at
    if rows:
        StreamingAvailability.objects.bulk_update(rows.values(), ["link", "fetched_at"])
        StreamingOffer.objects.filter(availability__in=list(rows.values())).delete()
    missing = [
        StreamingAvailability(
            title_type=title_type,
            tmdb_id=tmdb_id,
            region=region,
            link=link,
            fetched_at=fetched_at,
        )
        for region, (link, _offers) in snapshots.items()
        if region not in rows
    ]
    if missing:
        StreamingAvailability.objects.bulk_create(missing)
        rows = {row.region: row for row in StreamingAvailability.objects.filter(**lookup)}
    StreamingOffer.objects.bulk_create(
        [
            StreamingOffer(
                availability=rows[region],
                provider_id=offer.provider_id,
                monetization=offer.monetization,
                display_priority=offer.display_priority,
            )
            for region, (_link, offers) in snapshots.items()
            for offer in offers
        ],
    )
    return set(snapshots)


def store_snapshots(title_type, tmdb_id, results, regions, *, fetched_at=None, replace=True):
    """Replace the title's snapshots in ``regions`` with TMDB ``results``.

    ``results`` is TMDB's watch-provider dict keyed by region. Every region
    gets a row, with zero offers when TMDB has nothing there, so "checked, not
    available" stays distinct from "never checked". Rows are stamped
    ``fetched_at`` (default now). With ``replace=False``, regions that already
    have a row are left untouched. Returns the regions written.
    """
    regions = _regions(regions)
    if not regions:
        return set()
    tmdb_id = str(tmdb_id)
    results = results if isinstance(results, dict) else {}
    snapshots = {
        region: (tmdb.region_link(results.get(region)), tmdb.parse_offers(results.get(region)))
        for region in sorted(regions)
    }
    tmdb.upsert_providers(
        offer._asdict() for _link, offers in snapshots.values() for offer in offers
    )
    # A concurrent writer can create the same rows first; retrying then
    # updates the rows it made instead (or skips them without ``replace``).
    for attempt in range(1, STORE_ATTEMPTS + 1):
        try:
            with transaction.atomic():
                written = _write_snapshots(
                    title_type,
                    tmdb_id,
                    snapshots,
                    fetched_at=fetched_at,
                    replace=replace,
                )
        except IntegrityError:
            if attempt == STORE_ATTEMPTS:
                raise
            logger.info(
                "Retrying streaming snapshot write title=%s:%s attempt=%s",
                title_type,
                tmdb_id,
                attempt,
            )
        else:
            break
    return written


def sync_from_metadata(source, media_type, media_id, metadata):
    """Fill in missing snapshots from detail metadata already in hand.

    Cached detail metadata can be older than stored rows, so existing rows are
    never updated. Only active regions with no row at all are written, stamped
    as already stale so the next periodic run replaces them from a live TMDB
    call. Only TMDB movie and TV detail metadata qualifies; season metadata
    holds season-level offers. Makes no HTTP request and never raises on
    database or cache errors, since it runs inside detail requests. Returns
    the regions written.
    """
    if media_type not in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
        return set()
    key = title_key(source, media_type, media_id)
    if key is None or not isinstance(metadata, dict):
        return set()
    providers = metadata.get("providers")
    if not isinstance(providers, dict):
        return set()
    title_type, tmdb_id = key
    try:
        regions = active_regions()
        stored = StreamingAvailability.objects.filter(
            title_type=title_type,
            tmdb_id=tmdb_id,
            region__in=regions,
        ).values_list("region", flat=True)
        missing = regions - set(stored)
        if not missing:
            return set()
        return store_snapshots(
            title_type,
            tmdb_id,
            providers,
            missing,
            fetched_at=timezone.now() - METADATA_SNAPSHOT_AGE,
            replace=False,
        )
    except (DatabaseError, ConnectionInterrupted, RedisError) as error:
        logger.warning(
            "Streaming sync from metadata failed title=%s:%s type=%s",
            *key,
            type(error).__name__,
        )
        return set()


def _title(value):
    try:
        title_type, tmdb_id = value
    except (TypeError, ValueError):
        return None
    tmdb_id = str(tmdb_id).strip() if tmdb_id is not None else ""
    if title_type not in tmdb.TITLE_TYPES or not tmdb_id:
        return None
    return title_type, tmdb_id


def _refresh_title(title_type, tmdb_id, regions, *, force):
    if not force and not regions_needing_refresh(title_type, tmdb_id, regions):
        return "fresh"
    lock_key = f"streaming:refresh:{title_type}:{tmdb_id}"
    if not cache.add(lock_key, 1, timeout=REFRESH_LOCK_TIMEOUT):
        return "deduplicated"
    try:
        if not force and not regions_needing_refresh(title_type, tmdb_id, regions):
            return "fresh"
        try:
            results = tmdb.fetch_title_providers(title_type, tmdb_id)
        except ProviderAPIError as error:
            if error.status_code != 404:
                raise
            # TMDB has no such title, so it is unavailable everywhere.
            results = {}
        store_snapshots(title_type, tmdb_id, results, regions)
    finally:
        cache.delete(lock_key)
    return "refreshed"


def refresh_titles(titles, *, force=False):
    """Fetch and store availability for titles in every active region.

    ``titles`` holds ``(title_type, tmdb_id)`` pairs. Titles are isolated: a
    failure is counted and the rest continue. A per-title cache lock keeps
    concurrent workers from repeating the TMDB call, and titles already fresh
    everywhere are skipped unless ``force``.
    """
    titles = list(titles)
    keys = list(dict.fromkeys(key for key in map(_title, titles) if key is not None))
    # Malformed and repeated titles count as skipped.
    counts = Counter(skipped=len(titles) - len(keys))
    regions = active_regions()
    for title_type, tmdb_id in keys:
        try:
            status = _refresh_title(title_type, tmdb_id, regions, force=force)
        except Exception as error:  # noqa: BLE001 - one bad title must not stop a batch
            logger.warning(
                "Streaming refresh failed title=%s:%s type=%s",
                title_type,
                tmdb_id,
                type(error).__name__,
            )
            status = "failed"
        counts[status] += 1
    return {
        "requested": len(titles),
        "regions": sorted(regions),
        **{status: counts[status] for status in REFRESH_STATUSES},
    }
