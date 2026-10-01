"""Tests for the per-client throttles of the unauthenticated auth endpoints.

DRF's AnonRateThrottle skips authenticated requests. Anyone can register an account
in seconds and send its access token as a bearer header, so the throttle must key
every request by client address, authenticated or not, or the limit on guessing
passwords and flooding reset emails is worth nothing.

Refresh has a throttle (and a bucket) of its own: it needs an HMAC-signed token, so it
cannot be brute-forced, and sharing the login bucket would let refreshes and logins
starve each other if real-IP detection ever failed.
"""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from api.throttling import AuthRateThrottle, RefreshRateThrottle

# (url name, request body, status the endpoint answers while under the limit).
# Every payload is cheap and free of side effects, and none is a 429 by itself.
ENDPOINTS = (
    (
        "api-login",
        {"username_or_email": "victim", "password": "wrong-password"},
        status.HTTP_400_BAD_REQUEST,
    ),
    ("api-register", {}, status.HTTP_400_BAD_REQUEST),
    (
        "api-password-reset",
        {"email": "nobody@example.com"},
        status.HTTP_200_OK,
    ),
    (
        "api-password-reset-confirm",
        {"uid": "bad", "token": "bad", "new_password": "x"},
        status.HTTP_400_BAD_REQUEST,
    ),
    ("api-apple", {}, status.HTTP_501_NOT_IMPLEMENTED),
    (
        "api-refresh",
        {"refresh": "not-a-jwt"},
        status.HTTP_401_UNAUTHORIZED,
    ),
    (
        "api-logout",
        {"refresh": "not-a-jwt"},
        status.HTTP_204_NO_CONTENT,
    ),
)

LOGIN = {"username_or_email": "victim", "password": "wrong-password"}
DEAD_REFRESH = {"refresh": "not-a-jwt"}


class AuthThrottleTests(TestCase):
    """Login, register, password reset, reset confirm, Apple, refresh and logout."""

    @classmethod
    def setUpTestData(cls):
        # An attacker only needs an account of their own to hold a valid token.
        cls.attacker = get_user_model().objects.create_user(
            username="attacker",
            password="strong-password-123",
        )

    def setUp(self):
        cache.clear()
        self.client = APIClient(raise_request_exception=False)
        self.bearer = f"Bearer {AccessToken.for_user(self.attacker)}"

    def post(self, url_name, body):
        return self.client.post(reverse(url_name), body, format="json")

    def statuses(self, url_name, body, count):
        return [self.post(url_name, body).status_code for _ in range(count)]

    def assert_throttled_per_ip(self, url_name, body, under_limit, header):
        """Assert 3 requests a minute pass for one address, then 429 for it only."""
        cache.clear()
        self.client.credentials()
        if header:
            self.client.credentials(HTTP_AUTHORIZATION=header)
        url = reverse(url_name)

        # Whichever throttle the endpoint uses is at 3/min.
        with (
            patch.object(AuthRateThrottle, "THROTTLE_RATES", {"auth": "3/min"}),
            patch.object(RefreshRateThrottle, "rate", "3/min"),
        ):
            allowed = self.statuses(url_name, body, 3)
            throttled = self.client.post(url, body, format="json")
            # The limit is keyed by client address, so someone else is unaffected.
            elsewhere = self.client.post(
                url,
                body,
                format="json",
                REMOTE_ADDR="203.0.113.7",
            )

        self.assertEqual(allowed, [under_limit] * 3)
        self.assertEqual(throttled.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", throttled)
        self.assertEqual(elsewhere.status_code, under_limit)

    def test_endpoints_are_throttled_per_client_address(self):
        for url_name, body, under_limit in ENDPOINTS:
            with self.subTest(endpoint=url_name):
                self.assert_throttled_per_ip(url_name, body, under_limit, None)

    def test_a_valid_bearer_token_does_not_dodge_the_limit(self):
        for url_name, body, under_limit in ENDPOINTS:
            with self.subTest(endpoint=url_name):
                self.assert_throttled_per_ip(url_name, body, under_limit, self.bearer)

    def test_password_guessing_with_a_valid_bearer_token_is_throttled(self):
        # The attack itself: hammer another user's login while authenticated.
        get_user_model().objects.create_user(
            username="victim",
            password="another-password-456",
        )
        cache.clear()
        self.client.credentials(HTTP_AUTHORIZATION=self.bearer)

        with patch.object(AuthRateThrottle, "THROTTLE_RATES", {"auth": "5/min"}):
            statuses = self.statuses("api-login", LOGIN, 8)

        self.assertEqual(statuses.count(status.HTTP_400_BAD_REQUEST), 5)
        self.assertEqual(statuses.count(status.HTTP_429_TOO_MANY_REQUESTS), 3)

    # -- refresh has its own bucket ------------------------------------------

    def test_login_and_refresh_do_not_share_a_bucket(self):
        # Different limits (2 and 4 a minute) make any sharing show up as a count.
        with (
            patch.object(AuthRateThrottle, "THROTTLE_RATES", {"auth": "2/min"}),
            patch.object(RefreshRateThrottle, "rate", "4/min"),
        ):
            with self.subTest(order="login first"):
                cache.clear()
                logins = self.statuses("api-login", LOGIN, 3)
                refreshes = self.statuses("api-refresh", DEAD_REFRESH, 5)
                login_again = self.post("api-login", LOGIN).status_code

                # Spending the whole login budget left refresh its own 4...
                self.assertEqual(logins, [400, 400, 429])
                self.assertEqual(refreshes, [401, 401, 401, 401, 429])
                # ...and spending those did not free up login.
                self.assertEqual(login_again, status.HTTP_429_TOO_MANY_REQUESTS)

            with self.subTest(order="refresh first"):
                cache.clear()
                refreshes = self.statuses("api-refresh", DEAD_REFRESH, 5)
                logins = self.statuses("api-login", LOGIN, 3)

                self.assertEqual(refreshes, [401, 401, 401, 401, 429])
                self.assertEqual(logins, [400, 400, 429])

    def test_refresh_is_still_throttled_at_its_own_limit(self):
        with patch.object(RefreshRateThrottle, "rate", "4/min"):
            statuses = self.statuses("api-refresh", DEAD_REFRESH, 6)

        self.assertEqual(statuses, [401, 401, 401, 401, 429, 429])

    def test_logout_is_throttled_with_the_refresh_limit_not_the_login_one(self):
        with (
            patch.object(AuthRateThrottle, "THROTTLE_RATES", {"auth": "1/min"}),
            patch.object(RefreshRateThrottle, "rate", "3/min"),
        ):
            statuses = self.statuses("api-logout", DEAD_REFRESH, 5)

        self.assertEqual(statuses, [204, 204, 204, 429, 429])

    def test_refresh_gets_60_a_minute_without_any_settings_entry(self):
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertNotIn("auth_refresh", rates)

        refresh = RefreshRateThrottle()
        login = AuthRateThrottle()

        self.assertEqual((refresh.num_requests, refresh.duration), (60, 60))
        # Login and friends keep the settings value, 20 a minute.
        self.assertEqual((login.num_requests, login.duration), (20, 60))
