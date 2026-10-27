"""Celery tasks for app."""
import json
import logging
from collections import Counter
from datetime import timedelta
from itertools import batched

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db.models import Min, Q
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError

from config.celery import app
from app import statistics
from app.external_ratings import (
    RATING_SOURCES,
    TransientExternalRatingError,
    eligible_rating_sources,
    rating_sources_needing_refresh,
    refresh_external_ratings,
)
from app.models import ExternalRating, Item, UserMessage

logger = logging.getLogger(__name__)
EXTERNAL_RATING_BATCH_SIZE = 100
EXTERNAL_RATING_STALE_LIMIT = 500
EXTERNAL_RATING_LOCK_TIMEOUT = 60 * 15
EXTERNAL_RATING_RETRY_DELAYS = (60, 120, 240)
OUTCOME_SUMMARY_KEYS = (
    "attempted",
    "available",
    "unavailable",
    "failed",
    "skipped_fresh",
    "preserved_stale",
)


@shared_task(name="Refresh all-media search candidate", ignore_result=True)
def refresh_all_media_search_candidate(media_type, query, page, source):
    """Refresh one stale All-search provider candidate set."""
    from api.services.media import refresh_all_media_candidate

    return refresh_all_media_candidate(
        media_type=media_type,
        query=query,
        page=page,
        source=source,
    )


@shared_task(name="Refresh music search", ignore_result=True)
def refresh_music_search(media_type, query, page, source, preserve_ranking_fields=False):
    """Refresh one stale music search entry."""
    from api.services.media import refresh_music_search as refresh

    return refresh(
        media_type=media_type,
        query=query,
        page=page,
        source=source,
        preserve_ranking_fields=preserve_ranking_fields,
    )


@shared_task(name="Refresh music detail", ignore_result=True)
def refresh_music_detail(source, media_id, include_music_enrichment):
    """Refresh one stale music detail entry."""
    from api.services.media import refresh_music_detail as refresh

    return refresh(
        source=source,
        media_id=media_id,
        include_music_enrichment=include_music_enrichment,
    )


@shared_task(name="Refresh music genre albums")
def refresh_music_genre_albums():
    """Rebuild the popular albums behind music genre pages."""
    from app.providers import musicbrainz

    result = musicbrainz.refresh_genre_albums()
    logger.info("Music genre albums refreshed counts=%s", result)
    return result


@shared_task(name="Fill Apple album bridges", ignore_result=True)
def fill_apple_album_bridges():
    """Fill the MusicBrainz bridge of the Apple albums album pages queued (every 5 minutes)."""
    from app import music_links

    return music_links.fill_bridges()


@shared_task(name="Link genre albums to Apple")
def link_genre_albums_to_apple():
    """Link genre page albums to Apple Music albums (hourly, on the featured worker)."""
    from app import music_links

    result = music_links.link_genre_albums()
    logger.info("Genre albums linked to Apple Music counts=%s", result)
    return result


@shared_task(name="Refresh AniList person page", ignore_result=True)
def refresh_anilist_person_page(person_id, page, marker=None):
    """Refresh one stale AniList filmography page."""
    from app.providers import anilist

    try:
        return anilist.refresh_person_page(person_id, page)
    finally:
        if marker:
            cache.delete(marker)


def _normalize_rating_sources(rating_sources):
    if rating_sources is None:
        return None
    if isinstance(rating_sources, str):
        rating_sources = [rating_sources]
    rating_sources = list(dict.fromkeys(rating_sources))
    unknown = [source for source in rating_sources if source not in RATING_SOURCES]
    if unknown:
        msg = f"Unknown rating source: {unknown[0]}"
        raise ValueError(msg)
    return rating_sources


def _item_result(
    item_id,
    status,
    outcomes=None,
    fresh=0,
    preserved_stale=0,
):
    counts = Counter((outcomes or {}).values())
    return {
        "item_id": item_id,
        "status": status,
        "attempted": len(outcomes or {}),
        "available": counts[ExternalRating.Status.AVAILABLE],
        "unavailable": counts[ExternalRating.Status.UNAVAILABLE],
        "failed": counts[ExternalRating.Status.FAILED],
        "fresh": fresh,
        "skipped_fresh": fresh,
        "preserved_stale": preserved_stale,
    }


def _enrich_external_ratings(item_id, rating_sources=None, force=False):
    rating_sources = _normalize_rating_sources(rating_sources)
    item = Item.objects.filter(pk=item_id).first()
    if item is None:
        return _item_result(item_id, "skipped")
    sources = eligible_rating_sources(item, rating_sources)
    if not sources:
        return _item_result(item_id, "skipped")

    pending = rating_sources_needing_refresh(
        item,
        sources,
        force=force,
    )
    if not pending:
        return _item_result(item_id, "fresh", fresh=len(sources))

    lock_key = f"external-ratings:refresh:{item.pk}"
    try:
        acquired = cache.add(lock_key, 1, timeout=EXTERNAL_RATING_LOCK_TIMEOUT)
    except (ConnectionInterrupted, RedisError, ConnectionError, OSError) as error:
        transient = TransientExternalRatingError(pending)
        transient.summary = _item_result(
            item_id,
            "retrying",
            fresh=len(set(sources) - set(pending)),
        )
        raise transient from error
    if not acquired:
        return _item_result(item_id, "deduplicated")
    try:
        pending = rating_sources_needing_refresh(
            item,
            sources,
            force=force,
        )
        if not pending:
            return _item_result(item_id, "fresh", fresh=len(sources))
        successful_sources = set(
            item.external_ratings.exclude(value=None).values_list(
                "rating_source",
                flat=True,
            ),
        )
        try:
            outcomes = refresh_external_ratings(
                item,
                pending,
                raise_transient=True,
            )
        except TransientExternalRatingError as error:
            outcomes = getattr(error, "outcomes", {})
            preserved_stale = sum(
                status == ExternalRating.Status.FAILED
                and source in successful_sources
                for source, status in outcomes.items()
            )
            error.summary = _item_result(
                item_id,
                "retrying",
                outcomes,
                len(set(sources) - set(outcomes)),
                preserved_stale,
            )
            raise
    finally:
        try:
            cache.delete(lock_key)
        except (ConnectionInterrupted, RedisError, ConnectionError, OSError) as error:
            logger.warning(
                "External rating lock release failed item=%s type=%s",
                item_id,
                type(error).__name__,
            )

    counts = Counter(outcomes.values())
    terminal = counts[ExternalRating.Status.AVAILABLE] + counts[
        ExternalRating.Status.UNAVAILABLE
    ]
    if counts[ExternalRating.Status.FAILED]:
        status = "partial" if terminal else "failed"
    else:
        status = "refreshed"
    fresh = len(set(sources) - set(outcomes))
    preserved_stale = sum(
        outcome == ExternalRating.Status.FAILED and source in successful_sources
        for source, outcome in outcomes.items()
    )
    return _item_result(item_id, status, outcomes, fresh, preserved_stale)


@shared_task(bind=True, name="Enrich external ratings", max_retries=3)
def enrich_external_ratings(
    self,
    item_id,
    rating_sources=None,
    force=False,
):
    """Refresh eligible external ratings for one persisted Item."""
    try:
        result = _enrich_external_ratings(item_id, rating_sources, force)
    except TransientExternalRatingError as error:
        retry_number = min(self.request.retries, len(EXTERNAL_RATING_RETRY_DELAYS) - 1)
        summary = getattr(error, "summary", _item_result(item_id, "retrying"))
        logger.warning(
            "external_rating_item_summary %s",
            json.dumps(summary, sort_keys=True),
        )
        raise self.retry(
            args=(),
            kwargs={
                "item_id": item_id,
                "rating_sources": rating_sources,
                "force": False,
            },
            countdown=EXTERNAL_RATING_RETRY_DELAYS[retry_number],
        ) from error

    logger.info("external_rating_item_summary %s", json.dumps(result, sort_keys=True))
    return result


@shared_task(name="Enrich external ratings batch")
def enrich_external_ratings_batch(item_ids, rating_sources=None, force=False):
    """Refresh a bounded explicit batch without aborting on one bad Item."""
    item_ids = list(item_ids)
    if len(item_ids) > EXTERNAL_RATING_BATCH_SIZE:
        msg = f"External rating batches are limited to {EXTERNAL_RATING_BATCH_SIZE} items"
        raise ValueError(msg)
    rating_sources = _normalize_rating_sources(rating_sources)
    item_ids = list(dict.fromkeys(item_ids))
    counts = Counter()
    outcomes = Counter()

    for item_id in item_ids:
        try:
            result = _enrich_external_ratings(item_id, rating_sources, force)
        except TransientExternalRatingError as error:
            summary = getattr(error, "summary", {})
            outcomes.update({key: summary.get(key, 0) for key in OUTCOME_SUMMARY_KEYS})
            try:
                enrich_external_ratings.apply_async(
                    kwargs={
                        "item_id": item_id,
                        "rating_sources": rating_sources,
                        "force": False,
                    },
                    countdown=EXTERNAL_RATING_RETRY_DELAYS[0],
                )
            except Exception as enqueue_error:
                logger.error(
                    "External rating retry enqueue failed item=%s type=%s",
                    item_id,
                    type(enqueue_error).__name__,
                )
                counts["failed"] += 1
            else:
                counts["retrying"] += 1
        except Exception as error:
            logger.error(
                "External rating batch failed item=%s type=%s",
                item_id,
                type(error).__name__,
            )
            counts["failed"] += 1
        else:
            counts[result["status"]] += 1
            outcomes.update({
                key: result.get(key, 0)
                for key in OUTCOME_SUMMARY_KEYS
            })

    result = {
        "requested": len(item_ids),
        **{
            status: counts[status]
            for status in (
                "refreshed",
                "fresh",
                "skipped",
                "deduplicated",
                "partial",
                "failed",
                "retrying",
            )
        },
        "outcomes": {key: outcomes[key] for key in OUTCOME_SUMMARY_KEYS},
    }
    logger.info("external_rating_batch_summary %s", json.dumps(result, sort_keys=True))
    return result


def enqueue_external_rating_batches(
    item_ids,
    rating_sources=None,
    force=False,
    batch_size=EXTERNAL_RATING_BATCH_SIZE,
):
    """Queue deterministic Phase 4 batches for explicit Item IDs."""
    if not 1 <= batch_size <= EXTERNAL_RATING_BATCH_SIZE:
        msg = f"External rating batch size must be between 1 and {EXTERNAL_RATING_BATCH_SIZE}"
        raise ValueError(msg)
    item_ids = list(dict.fromkeys(item_ids))
    batches = 0
    task_ids = []
    for item_batch in batched(item_ids, batch_size):
        result = enrich_external_ratings_batch.delay(
            list(item_batch),
            rating_sources=rating_sources,
            force=force,
        )
        task_ids.append(result.id)
        batches += 1
    return {
        "items": len(item_ids),
        "batches": batches,
        "task_ids": task_ids,
    }


def _stale_external_rating_item_ids(now=None, limit=EXTERNAL_RATING_STALE_LIMIT):
    now = now or timezone.now()
    stale = Q(pk__isnull=True)
    for source, definition in RATING_SOURCES.items():
        stale |= Q(
            rating_source=source,
            item__source__in=definition["item_sources"],
            item__media_type__in=definition["media_types"],
        ) & (
            Q(status=ExternalRating.Status.FAILED)
            | Q(last_attempted_at__lt=now - definition["fresh_for"])
        )
    return list(
        ExternalRating.objects.filter(stale)
        .values("item_id")
        .annotate(oldest_attempt=Min("last_attempted_at"))
        .order_by("oldest_attempt", "item_id")
        .values_list("item_id", flat=True)[:limit],
    )


@shared_task(name="Queue stale external ratings")
def queue_stale_external_ratings():
    """Queue a bounded oldest-first refresh of existing rating rows."""
    item_ids = _stale_external_rating_item_ids()
    queued = enqueue_external_rating_batches(item_ids)
    result = {"selected": len(item_ids), "batches": queued["batches"]}
    logger.info("Stale external rating queue counts=%s", result)
    return result


@app.task
def update_daily_statistics(user_id, date_str=None):
    """
    Update statistics for a specific day.
    
    Args:
        user_id: The user to update statistics for
        date_str: Optional date string. If not provided, uses current date
    """
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    try:
        user = User.objects.get(id=user_id)
        
        # Get the date to process
        if date_str:
            day = timezone.datetime.fromisoformat(date_str)
        else:
            day = timezone.now()
            
        # Set time range for the full day
        start_of_day = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = start_of_day + timezone.timedelta(days=1)
        
        # Update statistics
        statistics.get_activity_data(
            user=user,
            start_date=start_of_day,
            end_date=end_of_day,
        )
        
        logger.info("Updated statistics for user %s on %s", user, start_of_day.date())
        
    except User.DoesNotExist:
        logger.error("Could not find user with id %s", user_id)
    except Exception as e:
        logger.error("Error updating statistics for user %s: %s", user_id, e) 


@shared_task(name="Cleanup user messages")
def cleanup_user_messages():
    """Delete shown user messages older than the configured retention window."""
    cutoff = timezone.now() - timedelta(days=settings.USER_MESSAGE_RETENTION_DAYS)
    deleted_count, _ = UserMessage.objects.filter(
        shown_at__isnull=False,
        shown_at__lt=cutoff,
    ).delete()

    logger.info("Deleted %s old shown user messages.", deleted_count)

    return deleted_count


@shared_task(name="Fill item metadata", ignore_result=True)
def fill_item_metadata(cursor=0, state=None, token=None):
    """Run one ~30 s chunk of the paced metadata fill and queue the next (app.metadata_fill)."""
    from app import metadata_fill  # noqa: PLC0415

    metadata_fill.chain_chunk(cursor=cursor, state=state, token=token)


@shared_task(name="Sweep item metadata")
def sweep_item_metadata():
    """Fill Items tracked or logged in the last 48 h, then resume the fill's backlog."""
    from app import metadata_fill  # noqa: PLC0415

    return metadata_fill.sweep()
