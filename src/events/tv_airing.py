"""The hourly TV job: reopen shows whose next episode aired (TV-206, TV-207).

Two passes, each working a show out with entry point JOB:

1. Due shows: shows of every status whose stored next air date (the show's, or a
   started season's) is on or before the user's today. Planning, Paused and Dropped
   shows are worked out too, so their stored counts stay current (TV-705); working out
   never changes a choice (TV-203). The candidates are selected up
   to the server's tomorrow and compared per user, because a user's calendar can be a
   day ahead of the server's. tv_tracking.refresh_due works from the stored listing,
   fetching only a season the provider names as next but the stored copy lacks (A-64).
2. Unfinished work: shows a hook or an import couldn't work out (reconciled_at empty),
   oldest first, at most MAX_UNRECONCILED per run, each with its listing fetched before
   its row is locked. A row that was never worked out at all is retried the way the
   import would have worked it out (tv_tracking.reconcile_imported, TV-800).

Shows stored before the contract that were never worked out (legacy_pending) are
skipped (TV-801). A provider failure leaves a show for the next run.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Min, OuterRef, Q, Subquery
from django.utils import timezone

from app import tv_listing, tv_tracking, user_calendar
from app.models import TV, DiaryEntry, Season

logger = logging.getLogger(__name__)

MAX_UNRECONCILED = 100
RETRYABLE = (tv_listing.TVDetailsUnavailable, *tv_listing.PROVIDER_FAILURES)


def refresh_aired_shows(now=None):
    """Run both passes; return how many shows each pass worked out and changed."""
    now = now or user_calendar.now()
    due, changed = _refresh_due_shows(now)
    unreconciled = _work_out_unreconciled(skip=due)
    summary = {
        "due": len(due),
        "changed": changed,
        "unreconciled": unreconciled,
    }
    logger.info("Refreshed aired TV: %s", summary)
    return summary


def _refresh_due_shows(now):
    """Work out every due show; return (the ids worked, how many changed)."""
    horizon = timezone.localdate(now) + timedelta(days=1)
    season_next = (
        Season.objects.filter(
            related_tv=OuterRef("pk"),
            is_started=True,
            next_air_date__isnull=False,
        )
        .values("related_tv")
        .annotate(earliest=Min("next_air_date"))
        .values("earliest")
    )
    candidates = (
        TV.objects.filter(legacy_pending=False)
        .annotate(season_next_air_date=Subquery(season_next))
        .filter(Q(next_air_date__lte=horizon) | Q(season_next_air_date__lte=horizon))
        .select_related("item", "user")
        .order_by("next_air_date", "pk")
    )
    worked = set()
    changed = 0
    for tv in candidates:
        today = user_calendar.today(tv.user)
        dates = [day for day in (tv.next_air_date, tv.season_next_air_date) if day is not None]
        if not dates or min(dates) > today:
            continue
        worked.add(tv.pk)
        try:
            if tv_tracking.refresh_due(tv):
                changed += 1
        except RETRYABLE:
            logger.warning("TV details unavailable; %s is left for the next run", tv.item)
        except Exception:
            logger.exception("Could not refresh aired TV row %s", tv.pk)
    return worked, changed


def _work_out_unreconciled(*, skip=()):
    """Work out shows a hook or an import left unworked; return how many were."""
    rows = list(
        TV.objects.filter(legacy_pending=False, reconciled_at__isnull=True)
        .exclude(pk__in=skip)
        .select_related("item", "user")
        .order_by("created_at", "pk")[:MAX_UNRECONCILED],
    )
    worked = 0
    for tv in rows:
        if tv.state_version == 0:
            # Never worked out: an import (or a model write) whose episode lists were
            # unavailable. Its stored statuses become facts as the import's would
            # have (TV-800, TV-207).
            if _retry_import(tv):
                worked += 1
            continue
        try:
            listing = tv_listing.get_listing(tv.item)
        except RETRYABLE:
            logger.warning("TV details unavailable; %s is left for the next run", tv.item)
            continue
        try:
            with transaction.atomic():
                locked = (
                    TV.objects.select_for_update()
                    .select_related("item", "user")
                    .filter(pk=tv.pk, legacy_pending=False, reconciled_at__isnull=True)
                    .first()
                )
                if locked is None:
                    continue
                tv_tracking.reconcile_show(locked, entry_point=tv_tracking.JOB, listing=listing)
        except RETRYABLE:
            logger.warning("TV details unavailable; %s is left for the next run", tv.item)
            continue
        except Exception:
            logger.exception("Could not work out TV row %s", tv.pk)
            continue
        worked += 1
    return worked


def _retry_import(tv):
    """Work out a row an import created, as reconcile_imported would have; return success.

    A row that was never worked out while its show already has logs written under the
    contract was recreated by an overwrite import, so those logs get their viewings
    and fills back (TV-800). Logs from before the contract instead make the show
    legacy, for the repair or the user's next write (TV-801).
    """
    snapshots = list(
        DiaryEntry.objects.filter(
            user=tv.user,
            item__media_id=tv.item.media_id,
            item__source=tv.item.source,
            item__media_type__in=tv_tracking.TV_TYPES,
        ).values_list("progress_snapshot", flat=True),
    )
    stamped = [_is_stamped(snapshot) for snapshot in snapshots]
    overwritten = [tv.item_id] if stamped and all(stamped) else []
    tv_tracking.reconcile_imported(tv.user, [tv.item], overwritten_show_item_ids=overwritten)
    return TV.objects.filter(pk=tv.pk, reconciled_at__isnull=False).exists()


def _is_stamped(snapshot):
    """Return whether a TV log was written under the contract (it carries the stamp)."""
    return isinstance(snapshot, dict) and isinstance(snapshot.get("tv"), dict) and (
        snapshot["tv"].get("v") is not None
    )
