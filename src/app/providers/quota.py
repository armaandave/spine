"""Daily request budgets and 429 back-off for background work that shares provider keys.

Featured-list syncs (``lists.featured.engine``) run inside ``scope(name, budgets)``.
Inside a scope, every ``services.api_request`` to a budgeted provider:

- counts against that provider's budget for the UTC day (a Redis counter shared by
  every worker); once it is spent, the request raises ``Deferred`` until midnight UTC;
- raises ``Deferred`` without touching the network while the provider is blocked.

A provider is blocked when it answers 429 with a wait longer than ``api_request``
would sleep (Hardcover's daily quota says "Try again in 69163 seconds"). The block is
recorded for every caller, so the app's own requests hitting the quota stop the
background work too, but only scoped requests honor it: the app's search and pages
are never counted or blocked here.

The counters live in ``services.redis_db`` rather than the Django cache, which is a
dummy cache under ``DEBUG``.
"""

import contextvars
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

# Hosts whose provider name callers spell differently (adapters pass their own label).
PROVIDER_HOSTS = {
    "api.hardcover.app": "hardcover",
    "openlibrary.org": "openlibrary",
}
LABELS = {"hardcover": "Hardcover", "openlibrary": "Open Library"}
COUNTER_TTL = 60 * 60 * 48
_SECONDS_IN_TEXT = re.compile(r"try again in\s+(\d+)\s*s", re.IGNORECASE)

_active = contextvars.ContextVar("provider_quota_scope", default=None)


@dataclass(frozen=True)
class _Scope:
    name: str
    budgets: dict


class Deferred(Exception):  # noqa: N818
    """A provider may not be called again until ``until`` (an aware UTC datetime)."""

    def __init__(self, provider, until, reason):
        self.provider = provider
        self.until = until
        label = LABELS.get(provider, provider)
        super().__init__(f"{label} {reason}; deferred until {until:%Y-%m-%d %H:%M} UTC")


@contextmanager
def scope(name, budgets):
    """Count and gate requests to the providers in ``budgets`` (``{provider: per day}``)."""
    token = _active.set(_Scope(name, dict(budgets)))
    try:
        yield
    finally:
        _active.reset(token)


def provider_for(provider, url):
    """Return the budget name for a request: by host when known, else ``provider``."""
    return PROVIDER_HOSTS.get(urlparse(url or "").hostname or "", provider)


def _redis():
    from app.providers import services  # noqa: PLC0415 (services imports this module)

    return services.redis_db


def _key(*parts):
    prefix = f"{settings.REDIS_PREFIX}:" if settings.REDIS_PREFIX else ""
    return prefix + "quota:" + ":".join(parts)


def _now(now=None):
    return (now or timezone.now()).astimezone(UTC)


def next_reset(now=None):
    """Return the next midnight UTC, when daily budgets start again."""
    now = _now(now)
    return datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)


def used_today(name, provider, now=None):
    """Return how many requests ``name`` made to ``provider`` today (UTC)."""
    now = _now(now)
    return int(_redis().get(_key(name, provider, now.date().isoformat())) or 0)


def blocked_until(provider, now=None):
    """Return when a recorded 429 block on ``provider`` ends, or None."""
    value = _redis().get(_key("blocked", provider))
    if value is None:
        return None
    until = datetime.fromtimestamp(float(value), tz=UTC)
    return until if until > _now(now) else None


def before_request(provider, url):
    """Inside a scope, spend one request on a budgeted provider or raise ``Deferred``."""
    current = _active.get()
    provider = provider_for(provider, url)
    if current is None or provider not in current.budgets:
        return
    now = _now()
    until = blocked_until(provider, now)
    if until is not None:
        raise Deferred(provider, until, "asked us to stop calling it")
    budget = current.budgets[provider]
    key = _key(current.name, provider, now.date().isoformat())
    client = _redis()
    count = client.incr(key)
    if count == 1:
        client.expire(key, COUNTER_TTL)
    if count > budget:
        raise Deferred(provider, next_reset(now), f"daily budget of {budget} requests for {current.name} is spent")


def retry_after_seconds(response):
    """Return the wait a 429 asks for, from ``Retry-After`` or the body text, or None."""
    header = (getattr(response, "headers", None) or {}).get("Retry-After")
    if header:
        try:
            return max(int(header), 0)
        except ValueError:
            try:
                return max(int((parsedate_to_datetime(header) - _now()).total_seconds()), 0)
            except (TypeError, ValueError):
                pass
    match = _SECONDS_IN_TEXT.search(getattr(response, "text", "") or "")
    return int(match.group(1)) if match else None


def rate_limited(provider, url, response, *, max_sleep):
    """Handle a 429 whose wait exceeds ``max_sleep``: block the provider until it ends.

    Inside a scope that budgets the provider, raise ``Deferred`` instead of letting the
    caller sleep and retry.
    """
    seconds = retry_after_seconds(response)
    if seconds is None or seconds <= max_sleep:
        return
    provider = provider_for(provider, url)
    until = _now() + timedelta(seconds=seconds)
    _redis().set(_key("blocked", provider), until.timestamp(), ex=seconds + 1)
    current = _active.get()
    if current is not None and provider in current.budgets:
        raise Deferred(provider, until, f"is rate limited for {seconds} s")


def clear():
    """Delete every quota counter and block (tests)."""
    client = _redis()
    for key in client.scan_iter(match=_key("*")):
        client.delete(key)
