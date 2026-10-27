from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from app import user_calendar
from app.middleware import APICalendarTimezoneMiddleware, AutoLoginMiddleware
from app.tests.tv_fixtures import patch_now

UserModel = get_user_model()


class AutoLoginMiddlewareTest(TestCase):
    """Test cases for AutoLoginMiddleware."""

    def setUp(self):
        """Create test users."""
        self.factory = RequestFactory()
        self.existing_active_user = UserModel.objects.create_user(
            username="active_user",
            password="active_user_password",  # noqa: S106
            is_active=True,
        )
        self.existing_inactive_user = UserModel.objects.create_user(
            username="inactive_user",
            password="inactive_user_password",  # noqa: S106
            is_active=False,
        )

    def get_request(self):
        """Return a request with session and user middleware applied."""
        request = self.factory.get("/")
        SessionMiddleware(lambda _request: None).process_request(request)
        AuthenticationMiddleware(lambda _request: None).process_request(request)
        return request

    def run_middleware(self, request):
        """Run auto-login middleware against the request."""
        middleware = AutoLoginMiddleware(lambda _request: None)
        middleware(request)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME=None)
    def test_env_var_unset(self):
        """Test that no auto-login occurs when YAMTRACK_AUTO_LOGIN_USERNAME is unset."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertFalse(request.user.is_authenticated)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="active_user")
    def test_existing_active_user(self):
        """Test that auto-login works with an existing active user."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertTrue(request.user.is_authenticated)
        self.assertEqual(request.user, self.existing_active_user)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="missing_user")
    def test_missing_user(self):
        """Test that no auto-login occurs with a missing user."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertFalse(request.user.is_authenticated)

    @override_settings(YAMTRACK_AUTO_LOGIN_USERNAME="inactive_user")
    def test_inactive_user(self):
        """Test that no auto-login occurs with an inactive user."""
        request = self.get_request()

        self.run_middleware(request)

        self.assertFalse(request.user.is_authenticated)


@override_settings(TIME_ZONE="America/New_York")
class APICalendarTimezoneMiddlewareTest(TestCase):
    """The app's X-Spine-Timezone is the user's calendar (Terms; A-60)."""

    def setUp(self):
        """Create an account with no stored calendar yet."""
        self.factory = RequestFactory()
        self.user = UserModel.objects.create_user(username="calendar-user")

    def run_middleware(self, path, zone, user):
        """Run the middleware and record the calendar the request saw."""
        seen = {}

        def view(request):
            seen["zone"] = user_calendar.calendar_tz(self.user)
            seen["other"] = user_calendar.calendar_tz(self.other)
            seen["local"] = timezone.get_current_timezone_name()
            return HttpResponse()

        self.other = UserModel.objects.create_user(
            username=f"other-{UserModel.objects.count()}",
            calendar_timezone="Europe/Paris",
        )
        headers = {"X-Spine-Timezone": zone} if zone else {}
        request = self.factory.get(path, headers=headers)
        request.user = user
        APICalendarTimezoneMiddleware(view)(request)
        self.user.refresh_from_db()
        return seen

    def test_a60_the_header_is_the_requesting_users_calendar_and_is_stored(self):
        seen = self.run_middleware("/api/v1/me/", "America/Los_Angeles", self.user)

        self.assertEqual(seen["zone"], ZoneInfo("America/Los_Angeles"))
        self.assertEqual(seen["other"], ZoneInfo("Europe/Paris"))
        self.assertEqual(seen["local"], "America/Los_Angeles")
        self.assertEqual(self.user.calendar_timezone, "America/Los_Angeles")
        # Outside the request the stored zone applies.
        self.assertIsNone(user_calendar.REQUEST_TIMEZONE.get())
        self.assertEqual(user_calendar.calendar_tz(self.user), ZoneInfo("America/Los_Angeles"))

    def test_an_invalid_zone_is_ignored(self):
        seen = self.run_middleware("/api/v1/me/", "not/a/timezone", self.user)
        self.assertEqual(seen["zone"], ZoneInfo("America/New_York"))
        self.assertEqual(self.user.calendar_timezone, "")

    def test_without_a_header_or_stored_zone_the_server_zone_is_used(self):
        seen = self.run_middleware("/api/v1/me/", None, self.user)
        self.assertEqual(seen["zone"], ZoneInfo("America/New_York"))
        self.assertEqual(self.user.calendar_timezone, "")

    def test_anonymous_and_non_api_requests_store_nothing(self):
        seen = self.run_middleware("/api/v1/me/", "Asia/Tokyo", AnonymousUser())
        self.assertEqual(seen["zone"], ZoneInfo("America/New_York"))
        self.run_middleware("/settings/", "Asia/Tokyo", self.user)
        self.assertEqual(self.user.calendar_timezone, "")

    def test_a60_an_authenticated_api_request_stores_the_zone(self):
        client = APIClient()
        token = RefreshToken.for_user(self.user).access_token
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token}",
            HTTP_X_SPINE_TIMEZONE="America/Los_Angeles",
        )

        response = client.get("/api/v1/me/")

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.calendar_timezone, "America/Los_Angeles")
        with patch_now(datetime(2026, 10, 25, 21, 0), tz="America/Los_Angeles"):
            self.assertEqual(user_calendar.today(self.user), date(2026, 10, 25))
