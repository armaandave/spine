"""The user's calendar: which day it is for a user (docs/tv-tracking-contract.md, Terms).

The user's calendar is the timezone the app last sent in the X-Spine-Timezone header.
Spine stores it on the account (User.calendar_timezone) and uses it on every path: the
app, the web, webhooks, imports, scheduled jobs and the repair. Until the app has sent
one, the server's timezone (settings.TIME_ZONE) is used.

TV code reads the clock only through now() and the user's day only through today(), so
tests patch django.utils.timezone.now and every recorded time agrees with it.
"""

import logging
from contextvars import ContextVar
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_NAME_LENGTH = 64

# The zone the app sent with the current API request; APICalendarTimezoneMiddleware
# sets it for one request. It is the newest value for the requesting user, so it wins
# over the stored one for that user while the request runs.
REQUEST_TIMEZONE: ContextVar[ZoneInfo | None] = ContextVar(
    "spine_request_timezone",
    default=None,
)
# The Django request that sent REQUEST_TIMEZONE, so the zone applies only to its user.
REQUEST: ContextVar[object | None] = ContextVar("spine_request", default=None)


def now() -> datetime:
    """Return the current time, through django.utils.timezone so tests can patch it."""
    return timezone.now()


def zone_named(name) -> ZoneInfo | None:
    """Return the IANA zone with this name, or None when it isn't a valid zone name."""
    if not isinstance(name, str) or not name or len(name) > MAX_NAME_LENGTH:
        return None
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        return None


def server_tz() -> ZoneInfo:
    """Return the server's timezone, used until the app sends one."""
    return ZoneInfo(settings.TIME_ZONE)


def calendar_tz(user) -> ZoneInfo:
    """Return the user's calendar: the request's zone, the stored one, or the server's."""
    request_zone = REQUEST_TIMEZONE.get()
    if request_zone is not None and _request_is_for(user):
        return request_zone
    stored = zone_named(getattr(user, "calendar_timezone", ""))
    if stored is not None:
        return stored
    return server_tz()


def today(user) -> date:
    """Return today's date on the user's calendar."""
    return now().astimezone(calendar_tz(user)).date()


def day_of(moment, user) -> date:
    """Return the calendar day of a moment on the user's calendar.

    A date is already a calendar day. A naive datetime is read as wall time on that
    calendar.
    """
    if not isinstance(moment, datetime):
        return moment
    if timezone.is_naive(moment):
        return moment.date()
    return moment.astimezone(calendar_tz(user)).date()


def remember(user, name) -> bool:
    """Store a valid zone the app sent as the user's calendar; return whether it changed.

    Writes only that column, with one UPDATE, so it never races another save of the
    account.
    """
    if user is None or getattr(user, "pk", None) is None or zone_named(name) is None:
        return False
    if getattr(user, "calendar_timezone", None) == name:
        return False
    get_user_model().objects.filter(pk=user.pk).update(calendar_timezone=name)
    user.calendar_timezone = name
    logger.debug("Stored calendar timezone %s for user %s", name, user.pk)
    return True


def _request_is_for(user) -> bool:
    """Return whether the request zone belongs to this user.

    The zone is set before the request is authenticated, so it is matched against the
    request's user when the zone is read. A zone set without a request (a test, or a
    caller that sets it on purpose) applies to everyone.
    """
    request = REQUEST.get()
    if request is None:
        return True
    request_user = getattr(request, "user", None)
    if request_user is None or not getattr(request_user, "is_authenticated", False):
        return False
    return user is not None and getattr(user, "pk", None) == request_user.pk
