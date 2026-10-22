"""Decide which featured lists are due and queue them on the ``featured`` queue.

``due()`` and ``plan()`` are pure: they take ``now`` and the sync rows explicitly.
The beat entry runs ``dispatch_featured_lists`` every 60 seconds.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from lists.featured import engine, registry
from lists.models import FeaturedListSync

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
    (from 20 minutes before it starts) and the window's interval elapsed.
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
    if sync.last_success_at is None:
        return Decision(due=True, reason="never succeeded")
    if definition_hash is not None and definition_hash != sync.definition_hash:
        return Decision(due=True, reason="definition changed")
    if definition.cadence is not None and now - sync.last_success_at >= definition.cadence:
        return Decision(due=True, reason="cadence elapsed")
    return Decision(due=False, reason="fresh")


def plan(definitions, syncs, now, *, hashes=None, max_non_live=MAX_NON_LIVE):
    """Return the definitions to queue now: every due live one, then the stalest others.

    ``syncs`` maps key to ``FeaturedListSync`` (missing = never attempted). Non-live
    work is capped so that at most ``max_non_live`` are leased at once, which keeps
    the queue short enough for award nights.
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

    def staleness(definition):
        sync = syncs.get(definition.key)
        last = sync.last_success_at if sync is not None else None
        return (last is not None, last or _EPOCH, definition.position, definition.key)

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


def dispatch(now=None):
    """Lease and queue every due definition; return the queued keys."""
    now = now or timezone.now()
    definitions = registry.all_definitions()
    syncs = {sync.key: sync for sync in FeaturedListSync.objects.all()}
    hashes = {definition.key: engine.compute_hash(definition) for definition in definitions}
    queued = []
    for definition in plan(definitions, syncs, now, hashes=hashes):
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
                kwargs={"dispatched_at": (now + timedelta(seconds=offset)).isoformat()},
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
def sync_featured_list(key, dispatched_at=None):
    """Sync one featured list by key."""
    definition = registry.get(key)
    if definition is None:
        FeaturedListSync.objects.filter(key=key).update(lease_until=None)
        return {"key": key, "status": "unknown"}
    sync = FeaturedListSync.objects.filter(key=key).first()
    if dispatched_at and sync is not None and sync.last_attempt_at:
        if sync.last_attempt_at > datetime.fromisoformat(dispatched_at):
            FeaturedListSync.objects.filter(key=key).update(lease_until=None)
            return {"key": key, "status": "skipped", "message": "Already ran since it was queued."}
    FeaturedListSync.objects.filter(key=key).update(
        lease_until=timezone.now() + lease_for(definition),
    )
    result = engine.run(definition, trigger="dispatch")
    return {"key": key, "status": result.status, "message": result.message}
