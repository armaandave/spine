"""External ratings for Items that featured syncs create, trickled onto the default worker.

The writer doesn't queue ``enrich_external_ratings_batch`` for every new Item: the
first deploy creates thousands (the static CSVs alone hold about 4,000 TMDB, 500 IGDB
and 600 Hardcover Items), which would backlog the default worker for hours and spend
the app's Hardcover quota. Instead:

- ``add_pending(ids)`` (the writer, on commit) adds the new Items to a Redis set;
- each dispatcher tick calls ``drain()``, which queues one batch of at most
  ``BATCH_SIZE`` on the default queue, and only when the previous batch has finished
  (or ``IN_FLIGHT_TTL`` passed), so app tasks never wait behind a featured backlog;
- the batch runs inside ``quota.scope("featured", …)``, so Hardcover and Open Library
  metadata calls count against the featured budgets. Book Items whose provider has
  no featured budget left today (or is blocked by a 429) stay pending until it resets.

Pending IDs live in ``services.redis_db`` (the Django cache is a dummy under DEBUG). If
Redis loses them, a list detail still enriches its Items when someone opens them.
"""

import logging

from celery import shared_task
from django.conf import settings
from redis.exceptions import RedisError

from app.models import Item, Sources
from app.providers import quota, services
from app.tasks import enrich_external_ratings_batch

logger = logging.getLogger(__name__)

TASK = "Enrich featured list ratings"
BATCH_SIZE = 50
IN_FLIGHT_TTL = 60 * 30  # a lost or crashed batch frees the slot after this long
BUDGETED_SOURCES = {"hardcover": Sources.HARDCOVER.value, "openlibrary": Sources.OPENLIBRARY.value}


def _key(name):
    prefix = f"{settings.REDIS_PREFIX}:" if settings.REDIS_PREFIX else ""
    return f"{prefix}featured:ratings:{name}"


PENDING_KEY = _key("pending")
IN_FLIGHT_KEY = _key("in-flight")


def _scope():
    from lists.featured import engine  # noqa: PLC0415 (engine imports the writer, which imports this)

    return engine.QUOTA_SCOPE, engine.provider_budgets()


def add_pending(item_ids):
    """Record Items that still need external ratings."""
    item_ids = list(item_ids)
    if not item_ids:
        return
    try:
        services.redis_db.sadd(PENDING_KEY, *item_ids)
    except RedisError as error:
        logger.warning("Featured ratings: could not record %s new Items (%s)", len(item_ids), type(error).__name__)


def pending_count():
    """Return how many Items wait for their first ratings."""
    try:
        return int(services.redis_db.scard(PENDING_KEY))
    except RedisError:
        return None


def _within_budget(item_ids):
    """Split ``item_ids`` into ``(ready, waiting)``: books wait while their provider's
    featured budget is spent or blocked; everything else is ready.
    """
    name, budgets = _scope()
    books = dict(
        Item.objects.filter(pk__in=item_ids, source__in=BUDGETED_SOURCES.values()).values_list("pk", "source"),
    )
    if not books:
        return list(item_ids), []
    remaining = {}
    for provider, source in BUDGETED_SOURCES.items():
        budget = budgets.get(provider)
        if budget is None:
            remaining[source] = len(item_ids)  # not budgeted
        elif quota.blocked_until(provider) is not None:
            remaining[source] = 0
        else:
            remaining[source] = max(budget - quota.used_today(name, provider), 0)
    ready, waiting = [], []
    for item_id in item_ids:
        source = books.get(item_id)
        if source is None:
            ready.append(item_id)
        elif remaining[source] > 0:
            remaining[source] -= 1  # about one metadata request per book
            ready.append(item_id)
        else:
            waiting.append(item_id)
    return ready, waiting


def drain():
    """Queue the next ratings batch unless one is in flight; return the queued IDs."""
    client = services.redis_db
    try:
        if not client.set(IN_FLIGHT_KEY, 1, nx=True, ex=IN_FLIGHT_TTL):
            return []
        popped = sorted(int(value) for value in client.spop(PENDING_KEY, BATCH_SIZE) or [])
    except RedisError as error:
        logger.warning("Featured ratings: Redis unavailable (%s)", type(error).__name__)
        return []
    ready, waiting = _within_budget(popped) if popped else ([], [])
    if waiting:
        add_pending(waiting)
    if not ready:
        client.delete(IN_FLIGHT_KEY)
        return []
    try:
        enrich_featured_ratings.delay(ready)
    except Exception as error:  # the broker is down: keep the IDs for the next tick
        logger.warning("Featured ratings: could not queue a batch (%s)", type(error).__name__)
        add_pending(ready)
        client.delete(IN_FLIGHT_KEY)
        return []
    return ready


@shared_task(name=TASK, ignore_result=True)
def enrich_featured_ratings(item_ids):
    """Enrich one featured batch inside the featured provider budgets (default queue)."""
    name, budgets = _scope()
    try:
        with quota.scope(name, budgets):
            return enrich_external_ratings_batch(list(item_ids))
    finally:
        try:
            services.redis_db.delete(IN_FLIGHT_KEY)
        except RedisError:
            pass  # IN_FLIGHT_TTL frees the slot


def clear():
    """Forget every pending Item and the in-flight marker (tests)."""
    services.redis_db.delete(PENDING_KEY, IN_FLIGHT_KEY)
