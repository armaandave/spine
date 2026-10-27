"""Decide which featured lists are due and queue them on the ``featured`` queue.

``due()`` and ``plan()`` are pure: they take ``now`` and the sync rows explicitly.
The beat entry runs ``dispatch_featured_lists`` every 60 seconds.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from app.models import MediaTypes
from app.providers import apple_music, services
from lists.featured import engine, order, ratings, registry
from lists.models import CustomList, FeaturedListSync

logger = logging.getLogger(__name__)
DISPATCH_TASK = "Dispatch featured lists"
SYNC_TASK = "Sync featured list"
QUEUE = "featured"
# Non-live syncs queued or running at once. The featured worker runs two at a time
# (supervisord.conf), so one slot is always free for live polls and the dispatcher.
MAX_NON_LIVE = 1
DISPATCH_PERIOD = timedelta(seconds=60)  # the beat interval of dispatch_featured_lists
BACKOFF_BASE = timedelta(minutes=5)
BACKOFF_MAX = timedelta(hours=24)
LEASE_GRACE = timedelta(seconds=120)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
# While the music data move runs (manage.py music_catalog apply), music lists aren't
# synced, so no sync makes an Item for an album that is moving. The flag lives in
# Redis and ends on its own, in case a run is killed.
MUSIC_PAUSE_KEY = "featured:music_paused"
MUSIC_PAUSE = timedelta(hours=6)


def pause_music_lists(duration=MUSIC_PAUSE):
    """Stop dispatching music lists for ``duration``, or until ``resume_music_lists``."""
    services.redis_db.set(apple_music.redis_key(MUSIC_PAUSE_KEY), 1, ex=int(duration.total_seconds()))


def resume_music_lists():
    """Dispatch music lists again."""
    services.redis_db.delete(apple_music.redis_key(MUSIC_PAUSE_KEY))


def music_lists_paused():
    """Return whether music lists are paused."""
    return bool(services.redis_db.exists(apple_music.redis_key(MUSIC_PAUSE_KEY)))


def _is_music(definition):
    return MediaTypes.MUSIC.value in definition.media_types


@dataclass(frozen=True)
class Decision:
    """Whether a definition is due now, and why."""

    due: bool
    reason: str
    live: bool = False


def backoff(failures, cadence=None):
    """Return how long to wait after ``failures`` consecutive failed attempts."""
    if failures <= 0:
        return timedelta(0)
    cap = min(BACKOFF_MAX, cadence) if cadence else BACKOFF_MAX
    return min(BACKOFF_BASE * 2 ** min(failures - 1, 16), max(cap, BACKOFF_BASE))


def is_leased(sync, now):
    """Return whether another dispatch holds this sync's lease."""
    return bool(sync is not None and sync.lease_until and sync.lease_until > now)


def due(definition, sync, now, *, definition_hash=None):  # noqa: PLR0911
    """Return a ``Decision`` for one definition at ``now``.

    Due when it never succeeded, its ``definition_hash`` changed, its cadence elapsed
    (all with exponential backoff after failures), or it is inside a live window
    (from 20 minutes before it starts) and the window's interval elapsed. A run that
    deferred entries for a provider's quota is due again at ``deferred_until``.
    """
    window = definition.active_live_window(now)
    if is_leased(sync, now):
        return Decision(due=False, reason="leased", live=window is not None)
    last_attempt = sync.last_attempt_at if sync is not None else None
    if window is not None:
        if last_attempt is None or now - last_attempt >= window.interval:
            return Decision(due=True, reason="live window", live=True)
        return Decision(due=False, reason="live interval", live=True)
    if last_attempt is None:
        return Decision(due=True, reason="never attempted")
    if sync.consecutive_failures and now - last_attempt < backoff(sync.consecutive_failures, definition.cadence):
        return Decision(due=False, reason="backing off")
    if sync.deferred_until is not None:
        if now < sync.deferred_until:
            return Decision(due=False, reason="deferred")
        return Decision(due=True, reason="deferral over")
    if sync.last_success_at is None:
        return Decision(due=True, reason="never succeeded")
    if definition_hash is not None and definition_hash != sync.definition_hash:
        return Decision(due=True, reason="definition changed")
    if definition.cadence is not None and now - sync.last_success_at >= definition.cadence:
        return Decision(due=True, reason="cadence elapsed")
    return Decision(due=False, reason="fresh")


def plan(definitions, syncs, now, *, hashes=None, max_non_live=MAX_NON_LIVE):
    """Return the definitions to queue now: every due live one, then the stalest others.

    ``syncs`` maps key to ``FeaturedListSync`` (missing = never attempted). Equally
    stale lists (never synced, say) follow the curated shelf order, then position, so
    flagship lists fill first after a deploy. Non-live work is capped so that at most
    ``max_non_live`` are leased at once, which keeps the queue short enough for award
    nights.
    """
    hashes = hashes or {}
    live, others = [], []
    in_flight = 0
    for definition in definitions:
        sync = syncs.get(definition.key)
        is_live = definition.active_live_window(now) is not None
        if is_leased(sync, now) and not is_live:
            in_flight += 1
        decision = due(definition, sync, now, definition_hash=hashes.get(definition.key))
        if decision.due:
            (live if decision.live else others).append(definition)

    ranks = order.sync_ranks()

    def staleness(definition):
        sync = syncs.get(definition.key)
        last = sync.last_success_at if sync is not None else None
        rank = ranks.get(definition.key)
        return (last is not None, last or _EPOCH, rank is None, rank or 0, definition.position, definition.key)

    others.sort(key=staleness)
    return live + others[: max(0, max_non_live - in_flight)]


def lease_for(definition):
    """Return how long a dispatched sync may hold its lease."""
    return timedelta(seconds=definition.soft_time_limit) + LEASE_GRACE


def poll_offsets(definition, now):
    """Return the countdowns (seconds) to queue this tick: ``[0]``, or for a live
    window polled more often than the dispatcher runs, one per interval until the
    next tick (``[0, 30]`` for a 30 s interval).
    """
    window = definition.active_live_window(now)
    if window is None or window.interval >= DISPATCH_PERIOD:
        return [0]
    step = window.interval.total_seconds()
    return [int(step * index) for index in range(int(DISPATCH_PERIOD.total_seconds() // step))]


def retire_orphans(definitions):
    """Unfeature lists the engine synced whose definition left the registry.

    A list whose sync row's key is gone, and whose identity no definition claims (a
    re-keyed definition keeps its list), would otherwise count as manually featured.
    Only ``is_featured`` is cleared: the list, its items, sync row and snapshots stay.
    Returns the unfeatured list IDs.
    """
    keys = [definition.key for definition in definitions]
    identities = {definition.identity for definition in definitions}
    rows = (
        FeaturedListSync.objects.filter(custom_list__is_featured=True)
        .exclude(key__in=keys)
        .values_list("key", "custom_list_id", "custom_list__import_source", "custom_list__import_source_id")
    )
    retired = {list_id: key for key, list_id, source, source_id in rows if (source, source_id) not in identities}
    if retired:
        CustomList.objects.filter(pk__in=retired).update(is_featured=False)
        logger.info("Featured lists retired (no definition): %s", ", ".join(sorted(retired.values())))
    return sorted(retired)


def dispatch(now=None):
    """Retire orphaned lists, trickle new Items' ratings, then lease and queue every
    due definition; return the queued keys. Music lists wait while they are paused.
    """
    now = now or timezone.now()
    definitions = registry.all_definitions()
    retire_orphans(definitions)
    ratings.drain()
    syncs = {sync.key: sync for sync in FeaturedListSync.objects.defer(*engine.LARGE_FIELDS)}
    hashes = {definition.key: engine.compute_hash(definition) for definition in definitions}
    paused = music_lists_paused()
    runnable = [definition for definition in definitions if not (paused and _is_music(definition))]
    queued = []
    for definition in plan(runnable, syncs, now, hashes=hashes):
        lease = lease_for(definition)
        sync = syncs.get(definition.key) or FeaturedListSync.objects.get_or_create(key=definition.key)[0]
        acquired = (
            FeaturedListSync.objects.filter(pk=sync.pk)
            .filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now))
            .update(lease_until=now + lease)
        )
        if not acquired:
            continue
        for offset in poll_offsets(definition, now):
            # A later poll skips itself if any run started after its own slot.
            sync_featured_list.apply_async(
                args=[definition.key],
                kwargs={
                    "dispatched_at": (now + timedelta(seconds=offset)).isoformat(),
                    "lease_until": (now + lease).isoformat(),
                },
                queue=QUEUE,
                countdown=offset or None,
                expires=lease.total_seconds() + offset,
                soft_time_limit=definition.soft_time_limit,
                time_limit=definition.soft_time_limit + 60,
            )
        queued.append(definition.key)
    return queued


@shared_task(name=DISPATCH_TASK, ignore_result=True)
def dispatch_featured_lists():
    """Queue due featured-list syncs (beat, every 60 seconds)."""
    return dispatch()


@shared_task(name=SYNC_TASK)
def sync_featured_list(key, dispatched_at=None, lease_until=None):
    """Sync one featured list by key.

    ``lease_until`` is the lease the dispatcher took for this task: a skipped poll
    releases only that lease, never one a newer run holds.
    """
    definition = registry.get(key)
    if definition is None:
        FeaturedListSync.objects.filter(key=key).update(lease_until=None)
        return {"key": key, "status": "unknown"}
    if _is_music(definition) and music_lists_paused():
        # Queued before the pause: give its lease back for a sync after it.
        leased = {"lease_until": datetime.fromisoformat(lease_until)} if lease_until else {}
        FeaturedListSync.objects.filter(key=key, **leased).update(lease_until=None)
        return {"key": key, "status": "paused", "message": "Music lists are paused while albums move."}
    sync = FeaturedListSync.objects.filter(key=key).first()
    if dispatched_at and sync is not None and sync.last_attempt_at:
        if sync.last_attempt_at > datetime.fromisoformat(dispatched_at):
            if lease_until:
                FeaturedListSync.objects.filter(
                    key=key,
                    lease_until=datetime.fromisoformat(lease_until),
                ).update(lease_until=None)
            return {"key": key, "status": "skipped", "message": "Already ran since it was queued."}
    FeaturedListSync.objects.filter(key=key).update(
        lease_until=timezone.now() + lease_for(definition),
    )
    result = engine.run(definition, trigger="dispatch")
    return {"key": key, "status": result.status, "message": result.message}
