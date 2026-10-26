"""The stats v2 response cache (docs/stats-contract.md STAT-006, PLAN.md 2.6).

A response is cached for an hour under a key that names everything it depends on:

    stats:2:{epoch}:{owner_pk}:{date_joined µs}:{ver}:{audience}:{start}:{end}:
        {type|all}:{tz}:{today}:{host}:{kind}[:{extra}...]

- ``ver`` is the owner's version. Every write the owner makes (and a collaborator's
  edit to the owner's list) bumps it once per transaction, when the transaction commits,
  so the next request misses (``bump_user``; receivers in app.stats_signals and explicit
  calls where code writes with ``.update()``, bulk writes or raw deletes).
- ``epoch`` is global. It is bumped when a metadata fill chain finishes
  (``bump_epoch``); other outside changes (featured syncs, curated artwork) wait for the
  TTL, as STAT-006 allows.
- ``date_joined`` keeps a reused primary key from reading a deleted user's entries.
- ``audience`` (owner, follower or public) is all a viewer changes: log visibility
  depends only on it, and Top no longer depends on the individual viewer.
- ``today`` keeps "right now" numbers, such as the current streak, from outliving
  midnight.

Version and epoch keys never expire: they are created with ``add(..., timeout=None)``
and then incremented, starting from the current time in microseconds, so a key lost to
eviction or the deploy's cache flush comes back larger than any value it had before.

Cache errors never break a write or a page: they are logged and the page is worked out
uncached. Tests run with the cache off (``settings.TESTING``) unless they set
``STATS_CACHE_ENABLED=True``, because the test suite's cache is shared across tests.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

logger = logging.getLogger(__name__)

SCHEMA = 2
TTL_SECONDS = 3600
EPOCH_KEY = "stats:epoch"
# Owners queued for a bump in the current transaction, kept on the connection.
PENDING_ATTR = "_stats_cache_pending"


def enabled():
    """Return whether stats responses are cached and versions bumped."""
    return getattr(settings, "STATS_CACHE_ENABLED", not getattr(settings, "TESTING", False))


def user_version_key(user_pk):
    """Return the cache key of one owner's stats version."""
    return f"stats:ver:{user_pk}"


# Bumps


def bump_user(user):
    """Invalidate one owner's cached stats when the current transaction commits.

    Takes a user or a primary key. Inside a transaction the bump is queued once per
    owner, however many rows the transaction writes; outside one it happens now.
    """
    user_pk = getattr(user, "pk", user)
    if user_pk is None or not enabled():
        return
    connection = transaction.get_connection()
    if not connection.in_atomic_block:
        _increment(user_version_key(user_pk))
        return
    # Django replaces run_on_commit with a new list on commit and on any rollback, so a
    # pending set tied to another list belongs to a transaction that has ended (or a
    # savepoint that rolled back) and starts again empty. At worst an owner is bumped
    # twice; never zero times.
    hooks, pending = getattr(connection, PENDING_ATTR, (None, None))
    if hooks is not connection.run_on_commit:
        pending = set()
    if user_pk in pending:
        return
    pending.add(user_pk)
    transaction.on_commit(_queued_bump(user_pk, pending), robust=True)
    setattr(connection, PENDING_ATTR, (connection.run_on_commit, pending))


def _queued_bump(user_pk, pending):
    def bump_on_commit():
        # Once it has run, the owner is no longer pending.
        pending.discard(user_pk)
        _increment(user_version_key(user_pk))

    return bump_on_commit


def bump_epoch():
    """Invalidate every owner's cached stats when the current transaction commits."""
    if enabled():
        transaction.on_commit(_bump_epoch_now, robust=True)


def _bump_epoch_now():
    _increment(EPOCH_KEY)


def _initial_value():
    return time.time_ns() // 1000


def _increment(key):
    try:
        cache.add(key, _initial_value(), timeout=None)
        try:
            cache.incr(key)
        except ValueError:
            # Evicted between add and incr: a fresh key is newer than any old value.
            cache.add(key, _initial_value() + 1, timeout=None)
    except Exception:
        logger.exception("Could not bump the stats cache key %s", key)


# Reads


def cached(scope, kind, build, *extra):
    """Return ``build()`` for this scope, from the cache when it is there.

    ``kind`` names the response (summary, highest_rated, most_logged, collections) and
    ``extra`` holds what else it depends on (page, page size, group). The key is worked
    out before ``build`` runs, so a write that commits meanwhile leaves the stored
    answer under the old version.
    """
    if not enabled():
        return build()
    try:
        key = response_key(scope, kind, *extra)
        value = cache.get(key)
    except Exception:
        logger.exception("Could not read the stats cache")
        return build()
    if value is not None:
        return value
    value = build()
    try:
        cache.set(key, value, timeout=TTL_SECONDS)
    except Exception:
        logger.exception("Could not store a stats response")
    return value


def response_key(scope, kind, *extra):
    """Return the cache key of one response; see the module docstring."""
    owner = scope.owner
    epoch, version = _versions(owner.pk)
    joined = int(owner.date_joined.timestamp() * 1_000_000) if owner.date_joined else 0
    request = scope.request
    host = f"{request.scheme}://{request.get_host()}" if request is not None else ""
    parts = [
        "stats",
        SCHEMA,
        epoch,
        owner.pk,
        joined,
        version,
        scope.audience,
        scope.start_date.isoformat() if scope.start_date else "all",
        scope.end_date.isoformat() if scope.end_date else "all",
        scope.media_type or "all",
        scope.timezone_name,
        scope.today.isoformat(),
        host,
        kind,
        *extra,
    ]
    return ":".join(str(part) for part in parts)


def _versions(user_pk):
    """Return (epoch, owner version), creating either key when it is missing."""
    user_key = user_version_key(user_pk)
    values = cache.get_many([EPOCH_KEY, user_key])
    for key in (EPOCH_KEY, user_key):
        if values.get(key) is None:
            cache.add(key, _initial_value(), timeout=None)
            values[key] = cache.get(key)
    return values[EPOCH_KEY], values[user_key]
