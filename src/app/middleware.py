import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.core.exceptions import MiddlewareNotUsed
from django.db import DatabaseError, connection
from django.shortcuts import render
from django.utils import timezone

from app import request_timing, user_calendar
from app.providers import services

logger = logging.getLogger(__name__)


class ServerTimingMiddleware:
    """Report where request time went in a Server-Timing header and a log line."""

    def __init__(self, get_response):
        """Enable timing hooks only when SERVER_TIMING is on."""
        if not settings.SERVER_TIMING:
            raise MiddlewareNotUsed
        request_timing.install()
        self.get_response = get_response

    def __call__(self, request):
        """Collect spans for the request and summarize them on the response."""
        with (
            request_timing.collect() as timings,
            connection.execute_wrapper(request_timing.db_execute_wrapper),
        ):
            response = self.get_response(request)
        # Kept on the request so in-process benchmarks can read every span.
        request.server_timing = timings
        header = request_timing.server_timing_header(timings)
        response["Server-Timing"] = header
        logger.info(
            "server_timing %s %s media_type=%s status=%s %s",
            request.method,
            request.path,
            request.GET.get("media_type", "-"),
            response.status_code,
            header,
        )
        return response


class AutoLoginMiddleware:
    """Middleware to auto-login with a specific user."""

    def __init__(self, get_response):
        """Initialize the middleware."""
        self.get_response = get_response

    def __call__(self, request):
        """Handle authorization request."""
        auto_login_username = settings.YAMTRACK_AUTO_LOGIN_USERNAME
        if auto_login_username and not request.user.is_authenticated:
            user_model = get_user_model()
            try:
                user = user_model.objects.get(username=auto_login_username)
                if user.is_active:
                    login(
                        request,
                        user,
                        backend="django.contrib.auth.backends.ModelBackend",
                    )
            except user_model.DoesNotExist:
                pass

        return self.get_response(request)


class ProviderAPIErrorMiddleware:
    """Middleware to handle ProviderAPIError exceptions."""

    def __init__(self, get_response):
        """Initialize the middleware with the get_response callable."""
        self.get_response = get_response

    def __call__(self, request):
        """Process the request and handle exceptions."""
        return self.get_response(request)

    def process_exception(self, request, exception):
        """Handle exceptions raised during request processing."""
        if isinstance(exception, services.ProviderAPIError):
            return render(
                request,
                "500.html",
                {
                    "error_message": str(exception),
                    "provider": exception.provider,
                },
                status=500,
            )
        return None


class APICalendarTimezoneMiddleware:
    """Apply the native client's calendar timezone for one API request.

    The zone is also the user's calendar (app.user_calendar): it applies to the
    requesting user while the request runs, and a changed valid zone is stored on the
    account afterwards, so webhooks, imports and jobs use it too.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        name = request.headers.get("X-Spine-Timezone")
        if not request.path.startswith("/api/") or not name:
            return self.get_response(request)
        try:
            client_timezone = ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            return self.get_response(request)
        zone_token = user_calendar.REQUEST_TIMEZONE.set(client_timezone)
        request_token = user_calendar.REQUEST.set(request)
        try:
            with timezone.override(client_timezone):
                response = self.get_response(request)
        finally:
            user_calendar.REQUEST.reset(request_token)
            user_calendar.REQUEST_TIMEZONE.reset(zone_token)
        self._remember(request, name)
        return response

    @staticmethod
    def _remember(request, name):
        """Store the zone on the account the request authenticated (DRF sets it)."""
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return
        try:
            user_calendar.remember(user, name)
        except DatabaseError:
            logger.exception("Could not store the calendar timezone for %s", user.pk)
