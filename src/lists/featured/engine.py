"""Run one featured-list definition: lock, fetch, resolve, guard, write, record."""

import logging
import uuid
from dataclasses import dataclass, field

from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from app.providers import quota
from lists.featured import overrides, registry, writer
from lists.featured.adapters import ADAPTERS
from lists.featured.helpers import failure_summary, sanitize
from lists.featured.resolve import Resolver
from lists.featured.types import AdapterError, FetchResult, GuardError
from lists.models import FeaturedListSync

logger = logging.getLogger(__name__)

OWNER_USERNAME = "Spine"
LOCK_PREFIX = "featured:lock:"
LOCK_GRACE_SECONDS = 120
SUCCESS_STATUSES = (FeaturedListSync.Status.OK, FeaturedListSync.Status.UNCHANGED)
QUOTA_SCOPE = "featured"
# Report-only JSON columns the dispatcher and the shelves never read.
LARGE_FIELDS = ("unresolved", "item_notes", "entry_notes")


def provider_budgets():
    """Return the daily request budgets featured syncs get (``app.providers.quota``)."""
    return {
        "hardcover": settings.FEATURED_HARDCOVER_DAILY_BUDGET,
        "openlibrary": settings.FEATURED_OPENLIBRARY_DAILY_BUDGET,
    }


@dataclass
class RunResult:
    """The outcome of one run. ``status`` is a ``FeaturedListSync.Status`` value,
    ``"locked"`` (another run holds the lock) or ``"dry-run"``.
    """

    key: str
    status: str
    message: str = ""
    fetch: FetchResult | None = None
    resolutions: list = field(default_factory=list)
    custom_list: object = None
    stored_count: int = 0
    resolved_count: int = 0
    error: Exception | None = None

    @property
    def ok(self):
        """Return whether the list is current (written or unchanged)."""
        return self.status in SUCCESS_STATUSES

    @property
    def unresolved(self):
        """Return every unresolved entry as a report row."""
        return writer.unresolved_rows(self.resolutions, limit=None)


def default_owner():
    """Return the ``Spine`` user that owns featured lists."""
    return get_user_model().objects.get(username=OWNER_USERNAME)


def compute_hash(definition):
    """Return the definition hash, including adapter inputs and this list's overrides."""
    return registry.definition_hash(
        definition,
        ADAPTERS.get(definition.adapter),
        sorted(overrides.for_definition(definition).items()),
    )


def dedupe(definition, fetch):
    """Drop repeated entry keys (first wins) and order ranked lists by rank.

    On lists that keep repeats only an entry with the same key and note is a repeat:
    one work's wins in different years are separate entries.
    """
    seen, entries = set(), []
    for entry in fetch.entries:
        identity = (entry.key, entry.note) if definition.keeps_repeats else entry.key
        if identity in seen:
            continue
        seen.add(identity)
        entries.append(entry)
    if definition.is_ranked:
        entries.sort(key=lambda entry: (entry.rank is None, entry.rank or 0))
    return fetch._replace(entries=entries)


def _resolve(definition, owner, fetch, resolver):
    fetch = dedupe(definition, fetch)
    resolutions = (resolver or Resolver(definition, remember=True)).resolve(fetch.entries)
    resolutions = writer.prepare(definition, resolutions)
    previous = writer.current_count(writer.find_list(definition, owner))
    return fetch, resolutions, previous


def _record_failure(sync, status, message, resolutions=None):
    """Record a failed outcome; ``_start`` already counted the attempt as a failure."""
    sync.last_status = status
    sync.last_error = sanitize(message)
    sync.deferred_until = None  # a failure retries on the backoff schedule instead
    fields = ["last_status", "last_error", "deferred_until"]
    if resolutions is not None:
        counted = writer.countable(resolutions)
        sync.entry_count = len(counted)
        sync.resolved_count = sum(1 for resolution in counted if resolution.match is not None)
        sync.unresolved = writer.unresolved_rows(resolutions)
        fields += ["entry_count", "resolved_count", "unresolved"]
    sync.save(update_fields=fields)


def _record_deferral(sync, message, until, resolutions=None):
    """Leave the list as it is and retry at ``until``; neither a success nor a failure,
    so the failure ``_start`` counted is taken back.
    """
    sync.last_status = FeaturedListSync.Status.DEFERRED
    sync.last_error = sanitize(message)
    sync.deferred_until = until
    sync.consecutive_failures = Greatest(F("consecutive_failures") - 1, Value(0))
    fields = ["last_status", "last_error", "deferred_until", "consecutive_failures"]
    if resolutions is not None:
        counted = writer.countable(resolutions)
        sync.entry_count = len(counted)
        sync.resolved_count = sum(1 for resolution in counted if resolution.match is not None)
        sync.unresolved = writer.unresolved_rows(resolutions)
        fields += ["entry_count", "resolved_count", "unresolved"]
    sync.save(update_fields=fields)
    sync.refresh_from_db(fields=["consecutive_failures"])


def _start(key, now):
    """Record the attempt, counted as a failure until an outcome replaces it.

    A run killed outright (SIGKILL, ``time_limit``, OOM, a deploy restart) never
    reaches its ``finally``; counting the attempt up front makes the dispatcher back
    off from such a list instead of retrying it every lease period.
    """
    sync, _ = FeaturedListSync.objects.get_or_create(key=key)
    FeaturedListSync.objects.filter(pk=sync.pk).update(
        last_attempt_at=now,
        consecutive_failures=F("consecutive_failures") + 1,
    )
    sync.refresh_from_db(fields=["last_attempt_at", "consecutive_failures"])
    return sync


def dry_run(definition, owner=None, *, fetch_result=None, resolver=None):
    """Fetch and resolve without writing anything; report what the guards would do."""
    with quota.scope(QUOTA_SCOPE, provider_budgets()):
        return _dry_run(definition, owner, fetch_result=fetch_result, resolver=resolver)


def _dry_run(definition, owner, *, fetch_result, resolver):
    fetch = fetch_result if fetch_result is not None else ADAPTERS[definition.adapter].fetch(definition)
    owner = owner or get_user_model().objects.filter(username=OWNER_USERNAME).first()
    fetch, resolutions, previous = _resolve(definition, owner, fetch, resolver)
    rows = writer.ordered_matches(definition, resolutions)
    message, error = "", None
    try:
        writer.check_guards(definition, fetch, resolutions, previous)
    except GuardError as guard:
        message, error = str(guard), guard
    waiting = writer.deferred(resolutions)
    if waiting and not message:
        keeps = "" if writer.writes_despite_deferrals(definition, resolutions, previous) else "; the list would be left as it is"
        message = f"{len(waiting)} entries deferred ({waiting[0].reason}){keeps}"
    return RunResult(
        key=definition.key,
        status="dry-run",
        message=message,
        fetch=fetch,
        resolutions=resolutions,
        stored_count=len(rows),
        resolved_count=sum(1 for resolution in writer.countable(resolutions) if resolution.match),
        error=error,
    )


def run(definition, owner=None, trigger="manual", *, now=None, fetch_result=None, resolver=None):
    """Sync one definition. Never raises for source or guard failures; see ``status``.

    ``fetch_result`` skips the adapter (legacy wrappers that already fetched) and
    ``resolver`` replaces the default ``Resolver(definition, remember=True)``.
    """
    lock_key = f"{LOCK_PREFIX}{definition.key}"
    token = uuid.uuid4().hex
    if not cache.add(lock_key, token, timeout=definition.soft_time_limit + LOCK_GRACE_SECONDS):
        return RunResult(definition.key, "locked", "Another sync of this list is running.")
    try:
        return _run_locked(definition, owner, trigger, now or timezone.now(), fetch_result, resolver)
    finally:
        if cache.get(lock_key) == token:
            cache.delete(lock_key)


def _run_locked(definition, owner, trigger, now, fetch_result, resolver):  # noqa: PLR0911
    sync, fetch, resolutions = None, None, None
    try:
        sync = _start(definition.key, now)
        logger.info("Featured list %s: sync started (%s)", definition.key, trigger)
        with quota.scope(QUOTA_SCOPE, provider_budgets()):
            owner = owner or default_owner()
            if fetch_result is None:
                fetch_result = ADAPTERS[definition.adapter].fetch(definition)
            fetch, resolutions, previous = _resolve(definition, owner, fetch_result, resolver)
        writer.check_guards(definition, fetch, resolutions, previous)
        waiting = writer.deferred(resolutions)
        if waiting and not writer.writes_despite_deferrals(definition, resolutions, previous):
            message = f"{len(waiting)} entries deferred ({waiting[0].reason}); the list was left as it is."
            _record_deferral(sync, message, writer.retry_at(resolutions), resolutions)
            logger.info("Featured list %s: %s", definition.key, message)
            return RunResult(definition.key, FeaturedListSync.Status.DEFERRED, message, fetch, resolutions)
        written = writer.commit(
            definition,
            owner,
            fetch,
            resolutions,
            sync=sync,
            now=now,
            definition_hash=compute_hash(definition),
        )
    except quota.Deferred as error:  # the adapter itself needed the provider
        _record_deferral(sync, str(error), error.until)
        logger.info("Featured list %s: %s", definition.key, error)
        return RunResult(definition.key, FeaturedListSync.Status.DEFERRED, str(error), error=error)
    except GuardError as error:
        _record_failure(sync, error.status, str(error), resolutions)
        logger.warning("Featured list %s: %s", definition.key, error)
        return RunResult(definition.key, error.status, str(error), fetch, resolutions or [], error=error)
    except SoftTimeLimitExceeded as error:
        _record_failure(sync, FeaturedListSync.Status.FAILED, "Timed out.", resolutions)
        logger.warning("Featured list %s: timed out", definition.key)
        return RunResult(definition.key, FeaturedListSync.Status.FAILED, "Timed out.", fetch, error=error)
    except AdapterError as error:
        message = sanitize(error) or "The source failed."
        logger.warning("Featured list %s: source failed: %s", definition.key, message)
        _record_failure(sync, FeaturedListSync.Status.FAILED, message)
        return RunResult(definition.key, FeaturedListSync.Status.FAILED, message, error=error)
    except Exception as error:
        message = sanitize(error) or type(error).__name__
        logger.error("Featured list %s failed: %s", definition.key, failure_summary(error))  # noqa: TRY400
        if sync is not None:
            _record_failure(sync, FeaturedListSync.Status.FAILED, message, resolutions)
        return RunResult(definition.key, FeaturedListSync.Status.FAILED, message, fetch, resolutions or [], error=error)
    finally:
        FeaturedListSync.objects.filter(key=definition.key).update(lease_until=None)
    logger.info(
        "Featured list %s: %s, %s/%s resolved",
        definition.key,
        written.status,
        written.resolved_count,
        len(writer.countable(resolutions)),
    )
    return RunResult(
        key=definition.key,
        status=written.status,
        message=sync.last_error if written.status == FeaturedListSync.Status.DEFERRED else "",
        fetch=fetch,
        resolutions=resolutions,
        custom_list=written.custom_list,
        stored_count=written.stored_count,
        resolved_count=written.resolved_count,
    )
