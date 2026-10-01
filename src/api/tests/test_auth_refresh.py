"""Tests for the JWT refresh and logout endpoints used by the iOS client.

The iOS app signs the user out only when POST /api/v1/auth/refresh/ answers 401
(or 400) and treats every other failure, 5xx included, as temporary. These tests
pin the server half of that contract: every dead refresh token must produce a
clean 401 in the API error envelope, never a 500, and the ways a live session
could be lost by accident (a half-committed rotation, a rotation response that
never arrived, a stale header) must not sign anyone out.
"""

import base64
from datetime import timedelta
from unittest.mock import patch

import jwt
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import OperationalError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.state import token_backend
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from api.throttling import RefreshRateThrottle
from api.views import auth as auth_views

AUTH_LOGGER = "api.views.auth"
BEARER_CHALLENGE = 'Bearer realm="api"'
NO_ACCOUNT_MESSAGE = "No active account found for the given token."
REFRESH_UNAVAILABLE_MESSAGE = (
    "Token refresh is temporarily unavailable. Try again shortly."
)
MAX_TOKEN_LENGTH = 2048
# Deeper than the JSON decoder allows (its recursion limit is about 10,000 levels).
NESTING_DEPTH = 20_000


def nested_header_token(depth):
    """Build a JWT-shaped string whose header nests ``depth`` JSON arrays."""

    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b"[" * depth + b"]" * depth
    return f"{b64(header)}.{b64(b'{}')}.{b64(b'signature')}"


def nested_array(depth):
    """Return the JSON text of ``depth`` nested arrays."""
    return "[" * depth + "]" * depth


def nested_json_bodies():
    """Return JSON request bodies nested past the decoder's limit, by placement."""
    deep = nested_array(NESTING_DEPTH)
    return {
        "top-level array": deep,
        "inside refresh": f'{{"refresh": {deep}}}',
        "inside another key": f'{{"other": {deep}, "refresh": "x"}}',
        "top-level objects": '{"a":' * NESTING_DEPTH + "1" + "}" * NESTING_DEPTH,
    }


class RefreshTestCase(TestCase):
    """Log a user in through the API, like the iOS client does."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="refresh-user",
            email="refresh-user@example.com",
            password="strong-password-123",
        )

    def setUp(self):
        # The auth endpoints are throttled per client (20/minute), so start clean.
        cache.clear()
        # Surface a server crash as the 500 a real client would see instead of
        # letting the test client re-raise the view's exception.
        self.client = APIClient(raise_request_exception=False)
        login = self.client.post(
            reverse("api-login"),
            {"username_or_email": "refresh-user", "password": "strong-password-123"},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK)
        self.access_token = login.data["access"]
        self.refresh_token = login.data["refresh"]

    # -- requests -----------------------------------------------------------

    def refresh(self, refresh_token):
        # TestCase never commits, so run the on_commit hooks a real commit would.
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("api-refresh"),
                {"refresh": refresh_token},
                format="json",
            )

    def rotate(self, refresh_token):
        """Refresh successfully and return the refresh token that replaced it."""
        response = self.refresh(refresh_token)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.json()["refresh"]

    def logout(self, refresh_token):
        """Log out like the app does: the refresh token alone, no access token."""
        return self.client.post(
            reverse("api-logout"),
            {"refresh": refresh_token},
            format="json",
        )

    def stale_authorization_headers(self):
        expired_access = AccessToken.for_user(self.user)
        expired_access.set_exp(from_time=timezone.now() - timedelta(hours=2))
        return {
            "expired access token": f"Bearer {expired_access}",
            "garbage bearer token": "Bearer total-garbage",
        }

    # -- rotation bookkeeping -----------------------------------------------

    def jti(self, token):
        return jwt.decode(token, options={"verify_signature": False})["jti"]

    def marker_key(self, token):
        return auth_views.refresh_rotation_marker_key(self.jti(token))

    def end_grace(self, token):
        """Make an already rotated token look like it was rotated long ago."""
        cache.delete(self.marker_key(token))

    def rotate_past_grace(self, token):
        """Rotate ``token``, then let its grace window run out: dead for good."""
        self.rotate(token)
        self.end_grace(token)

    def fresh_refresh_token(self):
        return str(RefreshToken.for_user(self.user))

    def assert_marker_ttl(self, token, seconds):
        """Assert the token's grace marker exists and expires within ``seconds``."""
        ttl = cache.ttl(self.marker_key(token))
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, seconds)

    # -- assertions ---------------------------------------------------------

    def assert_error(self, response, *, http_status, code, message):
        """Assert the 4xx JSON envelope and return its ``error`` object."""
        self.assertEqual(response.status_code, http_status)
        if http_status == status.HTTP_401_UNAUTHORIZED:
            # Without this challenge DRF would answer 403 instead of 401.
            self.assertEqual(response["WWW-Authenticate"], BEARER_CHALLENGE)
        error = response.json()["error"]
        self.assertEqual(error["code"], code)
        self.assertEqual(error["message"], message)
        return error

    def assert_dead_token(self, response, message):
        error = self.assert_error(
            response,
            http_status=status.HTTP_401_UNAUTHORIZED,
            code="token_not_valid",
            message=message,
        )
        self.assertIsNone(error["fields"])

    def assert_no_token_in(self, token, text):
        """Assert neither the token nor any of its segments appears in ``text``."""
        self.assertNotIn(token, text)
        for segment in token.split("."):
            self.assertNotIn(segment, text)

    def auth_records(self, logs):
        """Return the records the auth views logged, out of a root ``assertLogs``."""
        return [record for record in logs.records if record.name == AUTH_LOGGER]

    def auth_message(self, logs):
        """Return the single message the auth views logged, out of a root capture."""
        records = self.auth_records(logs)
        self.assertEqual(len(records), 1, [record.getMessage() for record in records])
        return records[0].getMessage()


class RefreshEndpointTests(RefreshTestCase):
    """POST /api/v1/auth/refresh/ answers every dead refresh token with a 401."""

    # -- success path -------------------------------------------------------

    def test_valid_refresh_returns_new_access_and_rotated_refresh_token(self):
        response = self.refresh(self.refresh_token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(set(body), {"access", "refresh"})
        self.assertEqual(AccessToken(body["access"])["user_id"], str(self.user.pk))
        self.assertNotEqual(body["access"], self.access_token)
        self.assertNotEqual(body["refresh"], self.refresh_token)
        # The rotated token is the one that keeps working.
        self.assertEqual(
            self.refresh(body["refresh"]).status_code,
            status.HTTP_200_OK,
        )

    # -- dead tokens must be 401, never 500 ---------------------------------

    def test_reusing_a_rotated_refresh_token_past_its_grace_returns_401(self):
        self.rotate_past_grace(self.refresh_token)

        replay = self.refresh(self.refresh_token)

        self.assertEqual(replay.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(replay["WWW-Authenticate"], BEARER_CHALLENGE)
        # The exact wire contract the iOS client decodes.
        self.assertEqual(
            replay.json(),
            {
                "error": {
                    "code": "token_not_valid",
                    "message": "Token is blacklisted",
                    "fields": None,
                    "request_id": None,
                },
            },
        )

    def test_garbage_refresh_token_returns_401(self):
        response = self.refresh("not-a-jwt")

        self.assert_dead_token(response, "Token is invalid")

    def test_expired_refresh_token_returns_401(self):
        token = RefreshToken.for_user(self.user)
        # exp = from_time + the 30 day refresh lifetime, i.e. one day ago.
        token.set_exp(from_time=timezone.now() - timedelta(days=31))

        response = self.refresh(str(token))

        self.assert_dead_token(response, "Token is expired")

    def test_access_token_is_not_accepted_as_a_refresh_token(self):
        response = self.refresh(self.access_token)

        self.assert_dead_token(response, "Token has wrong type")

    def test_refresh_token_of_an_inactive_user_returns_401(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.refresh(self.refresh_token)

        error = self.assert_error(
            response,
            http_status=status.HTTP_401_UNAUTHORIZED,
            code="authentication_failed",
            message=NO_ACCOUNT_MESSAGE,
        )
        self.assertIsNone(error["fields"])

    def test_refresh_token_of_a_deleted_user_returns_401(self):
        self.user.delete()

        response = self.refresh(self.refresh_token)

        error = self.assert_error(
            response,
            http_status=status.HTTP_401_UNAUTHORIZED,
            code="authentication_failed",
            message=NO_ACCOUNT_MESSAGE,
        )
        self.assertIsNone(error["fields"])

    # -- leftover credentials must not decide the outcome -------------------

    def test_stale_authorization_header_does_not_break_a_live_refresh(self):
        headers = self.stale_authorization_headers()
        headers["valid access token"] = f"Bearer {self.access_token}"
        for name, header in headers.items():
            with self.subTest(header=name):
                self.client.credentials(HTTP_AUTHORIZATION=header)

                response = self.refresh(self.fresh_refresh_token())

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(set(response.json()), {"access", "refresh"})

    def test_dead_refresh_token_with_a_stale_authorization_header_is_401(self):
        self.rotate_past_grace(self.refresh_token)
        for name, header in self.stale_authorization_headers().items():
            with self.subTest(header=name):
                self.client.credentials(HTTP_AUTHORIZATION=header)

                response = self.refresh(self.refresh_token)

                # Also asserts 401 rather than 403, plus the Bearer challenge.
                self.assert_dead_token(response, "Token is blacklisted")

    def test_session_cookie_does_not_affect_refresh(self):
        # A leftover Django session (web login) makes SessionAuthentication demand a
        # CSRF token, which a token endpoint has no use for.
        client = APIClient(enforce_csrf_checks=True, raise_request_exception=False)
        client.force_login(self.user)
        url = reverse("api-refresh")

        live = client.post(
            url,
            {"refresh": self.fresh_refresh_token()},
            format="json",
        )
        dead = client.post(url, {"refresh": "not-a-jwt"}, format="json")

        self.assertEqual(live.status_code, status.HTTP_200_OK)
        self.assertEqual(dead.status_code, status.HTTP_401_UNAUTHORIZED)

    # -- throttling ---------------------------------------------------------

    def assert_throttled_per_ip(self, header):
        """Assert 3 refreshes a minute are allowed per client address, then 429."""
        cache.clear()
        self.client.credentials()
        if header:
            self.client.credentials(HTTP_AUTHORIZATION=header)

        with patch.object(RefreshRateThrottle, "rate", "3/min"):
            allowed = [self.refresh("not-a-jwt").status_code for _ in range(3)]
            throttled = self.refresh("not-a-jwt")
            # The limit is keyed by client address, so someone else is unaffected.
            elsewhere = self.client.post(
                reverse("api-refresh"),
                {"refresh": "not-a-jwt"},
                format="json",
                REMOTE_ADDR="203.0.113.7",
            )

        self.assertEqual(allowed, [status.HTTP_401_UNAUTHORIZED] * 3)
        self.assertEqual(throttled.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", throttled)
        self.assertEqual(elsewhere.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_refresh_is_throttled_per_ip_whatever_the_authorization_header(self):
        # A bearer header, valid or not, must not dodge the limit.
        headers = {
            "no header": None,
            "valid access token": f"Bearer {self.access_token}",
            **self.stale_authorization_headers(),
        }
        for name, header in headers.items():
            with self.subTest(header=name):
                self.assert_throttled_per_ip(header)

    # -- logging ------------------------------------------------------------

    def test_rejected_refresh_logs_a_warning_without_the_token(self):
        self.rotate_past_grace(self.refresh_token)

        with self.assertLogs() as logs:
            response = self.refresh(self.refresh_token)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        record = self.auth_records(logs)[0]
        self.assertEqual(record.levelname, "WARNING")
        message = self.auth_message(logs)
        self.assertIn("Token is blacklisted", message)
        self.assertRegex(message, r"blacklisted \d+s ago")
        self.assertIn(f"claimed_user_id={self.user.pk}", message)
        # Every logger is captured, not just the auth views'.
        self.assert_no_token_in(self.refresh_token, "\n".join(logs.output))

    def test_rejected_garbage_token_logs_reason_but_not_the_string(self):
        with self.assertLogs() as logs:
            response = self.refresh("not-a-jwt")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        message = self.auth_message(logs)
        self.assertIn("Token is invalid", message)
        self.assertIn("claimed_user_id=unknown", message)
        self.assertNotIn("blacklisted", message)
        self.assertNotIn("not-a-jwt", "\n".join(logs.output))

    def test_expired_token_logs_the_claimed_user_id_read_without_verification(self):
        token = RefreshToken.for_user(self.user)
        token.set_exp(from_time=timezone.now() - timedelta(days=31))

        with self.assertLogs() as logs:
            self.refresh(str(token))

        message = self.auth_message(logs)
        self.assertIn("Token is expired", message)
        self.assertIn(f"claimed_user_id={self.user.pk}", message)
        self.assert_no_token_in(str(token), "\n".join(logs.output))

    def test_untrusted_user_id_claim_cannot_inject_log_lines(self):
        token = RefreshToken.for_user(self.user)
        token["user_id"] = "7\n[ERROR] forged log line"
        token.set_exp(from_time=timezone.now() - timedelta(days=31))

        with self.assertLogs() as logs:
            response = self.refresh(str(token))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("claimed_user_id=unknown", self.auth_message(logs))
        self.assertNotIn("forged", "\n".join(logs.output))

    def test_inactive_and_deleted_user_rejections_are_logged(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        with self.assertLogs() as inactive_logs:
            self.refresh(self.refresh_token)

        self.user.delete()
        with self.assertLogs() as deleted_logs:
            self.refresh(self.refresh_token)

        inactive = self.auth_message(inactive_logs)
        deleted = self.auth_message(deleted_logs)
        self.assertIn(NO_ACCOUNT_MESSAGE, inactive)
        self.assertIn("User no longer exists", deleted)
        for logs in (inactive_logs, deleted_logs):
            self.assert_no_token_in(self.refresh_token, "\n".join(logs.output))


class RefreshRotationTests(RefreshTestCase):
    """Rotation is atomic, and a rotation whose response was lost can be retried."""

    # -- atomic rotation ----------------------------------------------------

    def test_failed_rotation_rolls_back_so_the_old_token_still_works(self):
        with (
            patch.object(
                RefreshToken,
                "outstand",
                side_effect=OperationalError("database is locked"),
            ),
            self.assertLogs("django.request", level="ERROR"),
        ):
            failed = self.refresh(self.refresh_token)

        self.assertEqual(failed.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        # Nothing of the half-finished rotation survived: the old token is not
        # blacklisted, and no grace marker was written for it.
        self.assertFalse(BlacklistedToken.objects.exists())
        self.assertIsNone(cache.get(self.marker_key(self.refresh_token)))

        retry = self.refresh(self.refresh_token)

        self.assertEqual(retry.status_code, status.HTTP_200_OK)
        self.assertEqual(set(retry.json()), {"access", "refresh"})

    # -- the grace marker ---------------------------------------------------

    def test_marker_is_only_written_by_the_commit_hook(self):
        with self.captureOnCommitCallbacks(execute=False) as hooks:
            response = self.client.post(
                reverse("api-refresh"),
                {"refresh": self.refresh_token},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # The rotation only schedules the hook: nothing is written before the commit.
        self.assertEqual(len(hooks), 1)
        self.assertIsNone(cache.get(self.marker_key(self.refresh_token)))

        hooks[0]()  # the commit

        self.assert_marker_ttl(self.refresh_token, 60)

    def test_a_failure_after_the_hook_is_scheduled_leaves_no_marker(self):
        real_schedule = auth_views._schedule_rotation_marker
        scheduled = []

        def schedule_then_fail(request):
            with self.captureOnCommitCallbacks(execute=False) as hooks:
                real_schedule(request)
            scheduled.append(len(hooks))
            raise OperationalError("fails after the hook was scheduled")

        with (
            patch.object(auth_views, "_schedule_rotation_marker", schedule_then_fail),
            self.captureOnCommitCallbacks(execute=True) as surviving_hooks,
            self.assertLogs("django.request", level="ERROR"),
        ):
            failed = self.client.post(
                reverse("api-refresh"),
                {"refresh": self.refresh_token},
                format="json",
            )

        self.assertEqual(failed.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        # The hook had really been scheduled, and the rollback discarded it: a marker
        # never exists for a rotation that did not commit.
        self.assertEqual(scheduled, [1])
        self.assertEqual(surviving_hooks, [])
        self.assertIsNone(cache.get(self.marker_key(self.refresh_token)))
        self.assertFalse(BlacklistedToken.objects.exists())
        self.assertEqual(
            self.refresh(self.refresh_token).status_code,
            status.HTTP_200_OK,
        )

    def test_marker_is_written_after_a_successful_rotation(self):
        self.assertIsNone(cache.get(self.marker_key(self.refresh_token)))

        self.rotate(self.refresh_token)

        self.assert_marker_ttl(self.refresh_token, 60)

    @override_settings(JWT_REFRESH_REUSE_GRACE_SECONDS=5)
    def test_grace_window_follows_the_setting(self):
        self.rotate(self.refresh_token)

        self.assert_marker_ttl(self.refresh_token, 5)

    @override_settings(JWT_REFRESH_REUSE_GRACE_SECONDS=0)
    def test_grace_can_be_switched_off(self):
        self.rotate(self.refresh_token)

        self.assertIsNone(cache.get(self.marker_key(self.refresh_token)))
        self.assertEqual(
            self.refresh(self.refresh_token).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_marker_write_failure_does_not_fail_the_rotation(self):
        with (
            patch.object(auth_views, "cache") as broken_cache,
            self.assertLogs() as logs,
        ):
            broken_cache.set.side_effect = RuntimeError("redis is down")
            response = self.refresh(self.refresh_token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("RuntimeError", self.auth_message(logs))

    # -- reuse within the window --------------------------------------------

    def test_reuse_within_the_window_returns_a_fresh_pair_and_logs_it(self):
        lost = self.rotate(self.refresh_token)  # this response never reached the phone

        with self.assertLogs() as logs:
            replay = self.refresh(self.refresh_token)

        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        body = replay.json()
        self.assertEqual(set(body), {"access", "refresh"})
        self.assertEqual(AccessToken(body["access"])["user_id"], str(self.user.pk))
        # A brand-new pair, not a replay of the rotation the phone missed.
        self.assertNotIn(body["refresh"], {self.refresh_token, lost})
        record = self.auth_records(logs)[0]
        self.assertEqual(record.levelname, "WARNING")
        self.assertRegex(
            self.auth_message(logs),
            rf"reused rotated token within grace \(\d+s after rotation, "
            rf"user_id={self.user.pk}\)",
        )
        self.assertNotIn("claimed", self.auth_message(logs))
        self.assert_no_token_in(self.refresh_token, "\n".join(logs.output))

        # The new pair keeps working and rotates normally.
        rotated = self.refresh(body["refresh"])
        self.assertEqual(rotated.status_code, status.HTTP_200_OK)
        self.assertNotEqual(rotated.json()["refresh"], body["refresh"])
        self.end_grace(body["refresh"])
        self.assert_dead_token(self.refresh(body["refresh"]), "Token is blacklisted")

    # -- documented trade-offs ----------------------------------------------
    # These pin behavior the design accepts on purpose. Changing either is a
    # decision to make deliberately, not a bug fix.

    def test_documented_tradeoff_parent_stays_exchangeable_after_child_logout(self):
        # Nothing links a token to its predecessor, so logging the child out cannot
        # reach the marker of the parent that was rotated to produce it.
        child = self.rotate(self.refresh_token)
        self.assertEqual(self.logout(child).status_code, status.HTTP_204_NO_CONTENT)
        self.assert_dead_token(self.refresh(child), "Token is blacklisted")

        replay = self.refresh(self.refresh_token)

        self.assertEqual(replay.status_code, status.HTTP_200_OK)
        self.assertEqual(set(replay.json()), {"access", "refresh"})

    def test_documented_tradeoff_every_grace_replay_mints_a_pair(self):
        # The marker is not consumed, so a retry whose own response is lost can be
        # retried again. Each replay therefore issues and stores another pair.
        self.rotate(self.refresh_token)
        outstanding_before = OutstandingToken.objects.count()

        pairs = [self.refresh(self.refresh_token).json() for _ in range(3)]

        self.assertEqual(len({pair["access"] for pair in pairs}), 3)
        self.assertEqual(len({pair["refresh"] for pair in pairs}), 3)
        self.assertEqual(OutstandingToken.objects.count(), outstanding_before + 3)
        for pair in pairs:
            self.assertEqual(
                self.refresh(pair["refresh"]).status_code,
                status.HTTP_200_OK,
            )

    def test_reuse_after_the_window_returns_401_and_logs_how_long_ago(self):
        def rotated_longer_ago_than_the_window(token):
            BlacklistedToken.objects.filter(token__jti=self.jti(token)).update(
                blacklisted_at=timezone.now() - timedelta(seconds=65),
            )

        cases = {
            "marker expired": (self.end_grace, r"blacklisted \d+s ago"),
            "blacklisted longer ago than the window": (
                rotated_longer_ago_than_the_window,
                r"blacklisted 6[5-6]s ago",
            ),
        }
        for label, (let_grace_run_out, expected_log) in cases.items():
            with self.subTest(case=label):
                token = self.fresh_refresh_token()
                self.rotate(token)
                let_grace_run_out(token)

                with self.assertLogs() as logs:
                    replay = self.refresh(token)

                self.assert_dead_token(replay, "Token is blacklisted")
                self.assertRegex(self.auth_message(logs), expected_log)

    def test_token_revoked_by_logout_gets_no_grace(self):
        logout = self.logout(self.refresh_token)

        self.assertEqual(logout.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIsNone(cache.get(self.marker_key(self.refresh_token)))
        self.assert_dead_token(
            self.refresh(self.refresh_token),
            "Token is blacklisted",
        )

    def test_inactive_user_gets_no_grace(self):
        self.rotate(self.refresh_token)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        response = self.refresh(self.refresh_token)

        self.assert_dead_token(response, "Token is blacklisted")

    def test_deleted_user_gets_no_grace(self):
        self.rotate(self.refresh_token)
        self.user.delete()

        response = self.refresh(self.refresh_token)

        self.assert_dead_token(response, "Token is blacklisted")

    def test_unverified_tokens_carrying_a_marked_jti_get_no_grace(self):
        self.rotate(self.refresh_token)
        # The one thing standing between a forger and a fresh pair is verification.
        self.assertIsNotNone(cache.get(self.marker_key(self.refresh_token)))
        self.assertTrue(
            BlacklistedToken.objects.filter(
                token__jti=self.jti(self.refresh_token),
            ).exists(),
        )
        claims = jwt.decode(self.refresh_token, options={"verify_signature": False})
        expired_at = int((timezone.now() - timedelta(days=1)).timestamp())
        forged = {
            "wrong signature": jwt.encode(claims, "x" * 64, algorithm="HS256"),
            "unsigned": jwt.encode(claims, None, algorithm="none"),
            "expired": token_backend.encode({**claims, "exp": expired_at}),
            "wrong type": token_backend.encode({**claims, "token_type": "access"}),
        }

        for label, token in forged.items():
            with self.subTest(forgery=label):
                response = self.refresh(token)

                self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
                self.assertEqual(set(response.json()), {"error"})
                self.assertEqual(
                    response.json()["error"]["code"],
                    "token_not_valid",
                )

    def test_marker_lookup_failure_is_a_503_so_the_client_retries(self):
        self.rotate(self.refresh_token)

        with (
            patch.object(auth_views, "cache") as broken_cache,
            self.assertLogs() as logs,
        ):
            broken_cache.get.side_effect = RuntimeError("redis is down")
            response = self.refresh(self.refresh_token)

        # A 401 reads as "session over" to the client and signs the user out. A 503
        # reads as "try again", which is right: whether grace applies is unknown.
        error = self.assert_error(
            response,
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="service_unavailable",
            message=REFRESH_UNAVAILABLE_MESSAGE,
        )
        self.assertIsNone(error["fields"])
        self.assertNotIn("WWW-Authenticate", response)
        message = self.auth_message(logs)
        self.assertIn("RuntimeError", message)
        self.assertIn(f"user_id={self.user.pk}", message)

    def test_cache_is_not_read_for_tokens_that_cannot_get_grace(self):
        outside_window = self.fresh_refresh_token()
        self.rotate(outside_window)
        BlacklistedToken.objects.filter(token__jti=self.jti(outside_window)).update(
            blacklisted_at=timezone.now() - timedelta(seconds=65),
        )

        with patch.object(auth_views, "cache") as broken_cache:
            broken_cache.get.side_effect = RuntimeError("redis is down")
            late = self.refresh(outside_window)
            garbage = self.refresh("not-a-jwt")

        # Only a token blacklisted within the window needs the marker, so a cache
        # outage cannot turn other dead tokens into a retryable 503.
        self.assert_dead_token(late, "Token is blacklisted")
        self.assert_dead_token(garbage, "Token is invalid")
        broken_cache.get.assert_not_called()


class RefreshRequestTests(RefreshTestCase):
    """Hostile, oversized and malformed request bodies."""

    def post_refresh(self, body):
        return self.client.post(reverse("api-refresh"), body, format="json")

    def test_body_shapes_are_rejected_and_the_problem_is_logged(self):
        cases = (
            ("missing field", {}, 400, "refresh=required"),
            ("null", {"refresh": None}, 400, "refresh=null"),
            ("blank", {"refresh": ""}, 400, "refresh=blank"),
            ("whitespace", {"refresh": "   "}, 400, "refresh=blank"),
            ("empty list", {"refresh": []}, 400, "refresh=invalid"),
            ("empty object", {"refresh": {}}, 400, "refresh=invalid"),
            ("boolean", {"refresh": True}, 400, "refresh=invalid"),
            ("top-level list", ["a", "b"], 400, "non_field_errors=invalid"),
            ("number", {"refresh": 123}, 401, "Token is invalid"),
        )
        for label, body, expected_status, expected_log in cases:
            with self.subTest(body=label), self.assertLogs() as logs:
                response = self.post_refresh(body)

                self.assertEqual(response.status_code, expected_status)
                self.assertIn(expected_log, self.auth_message(logs))

    def test_missing_or_blank_refresh_field_returns_400_with_fields(self):
        for payload in ({}, {"refresh": ""}, {"refresh": None}):
            with self.subTest(payload=payload):
                response = self.post_refresh(payload)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("refresh", response.json()["error"]["fields"])

    def test_malformed_json_is_400_and_logged_without_the_body(self):
        with self.assertLogs() as logs:
            response = self.client.post(
                reverse("api-refresh"),
                '{"refresh": "secret-looking-value',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["error"]["code"], "parse_error")
        self.assertIn("unparseable request body", self.auth_message(logs))
        self.assertNotIn("secret-looking-value", "\n".join(logs.output))

    def test_token_length_limit(self):
        for length, expected_status, expected_log in (
            (MAX_TOKEN_LENGTH, 401, "Token is invalid"),
            (MAX_TOKEN_LENGTH + 1, 400, "refresh=max_length"),
        ):
            token = "a" * length
            with self.subTest(length=length), self.assertLogs() as logs:
                response = self.refresh(token)

                self.assertEqual(response.status_code, expected_status)
                self.assertIn(expected_log, self.auth_message(logs))
                self.assertNotIn(token, "\n".join(logs.output))

    def test_lone_surrogate_refresh_is_a_400(self):
        with self.assertLogs() as logs:
            response = self.client.post(
                reverse("api-refresh"),
                '{"refresh": "\\ud800"}',
                content_type="application/json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("refresh=", self.auth_message(logs))

    def test_nested_json_bodies_are_a_400_parse_error_not_a_500(self):
        # JSON nested past the decoder's limit makes it raise RecursionError, which
        # DRF's parser does not expect (it only handles ValueError).
        for label, body in nested_json_bodies().items():
            with self.subTest(body=label), self.assertLogs() as logs:
                response = self.client.post(
                    reverse("api-refresh"),
                    body,
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.json()["error"]["code"], "parse_error")
                self.assertIn("unparseable request body", self.auth_message(logs))
                self.assertNotIn("[[[[", "\n".join(logs.output))

    def test_moderately_nested_bodies_are_still_judged_normally(self):
        # Well inside the decoder's limit: a plain validation error, no parse error.
        response = self.client.post(
            reverse("api-refresh"),
            f'{{"refresh": {nested_array(500)}}}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["error"]["code"], "invalid")
        self.assertIn("refresh", response.json()["error"]["fields"])

    def test_deeply_nested_hostile_token_is_400_not_a_500(self):
        # About 27 KB with a header nested 10,000 levels deep: PyJWT's JSON decoder
        # raises RecursionError on it, which used to surface as an HTML 500.
        hostile = nested_header_token(10_000)
        self.assertGreater(len(hostile), 26_000)

        with self.assertLogs() as logs:
            response = self.refresh(hostile)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("refresh", response.json()["error"]["fields"])
        self.assertIn("refresh=max_length", self.auth_message(logs))
        self.assertNotIn(hostile[:200], "\n".join(logs.output))


class LogoutTests(RefreshTestCase):
    """POST /api/v1/auth/logout/ revokes a refresh token with no access token.

    The app signs the user out locally first and revokes in the background with only
    the refresh token it captured, often after the access token has expired, so the
    endpoint must not ask for one. Whatever is posted, the answer is 204.
    """

    def logout_logged(self, refresh_token):
        """Log out and return the response with the single auth log message."""
        with self.assertLogs() as logs:
            response = self.logout(refresh_token)
        records = self.auth_records(logs)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].levelname, "INFO")
        return response, records[0].getMessage(), logs

    def post_raw(self, body):
        """Post raw JSON text to logout, with no Authorization header."""
        return self.client.post(
            reverse("api-logout"),
            body,
            content_type="application/json",
        )

    # -- the refresh token is the only credential ----------------------------

    def test_logout_needs_only_the_refresh_token(self):
        response, message, logs = self.logout_logged(self.refresh_token)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIn(f"user_id={self.user.pk}", message)
        self.assertIn("revoked=True", message)
        self.assert_no_token_in(self.refresh_token, "\n".join(logs.output))
        self.assertTrue(
            BlacklistedToken.objects.filter(
                token__jti=self.jti(self.refresh_token),
            ).exists(),
        )
        # The revoked token is dead at once: logout writes no grace marker, so it
        # fails refresh within the grace window and after it alike.
        self.assert_dead_token(self.refresh(self.refresh_token), "Token is blacklisted")
        self.end_grace(self.refresh_token)
        self.assert_dead_token(self.refresh(self.refresh_token), "Token is blacklisted")

    def test_logout_ignores_a_stale_authorization_header(self):
        for name, header in self.stale_authorization_headers().items():
            token = self.fresh_refresh_token()
            self.client.credentials(HTTP_AUTHORIZATION=header)
            with self.subTest(header=name), self.assertLogs() as logs:
                response = self.logout(token)

                self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
                self.assertIn("revoked=True", self.auth_message(logs))
            self.assert_dead_token(self.refresh(token), "Token is blacklisted")

    def test_logout_reports_the_owner_of_the_refresh_token_not_of_the_header(self):
        other = get_user_model().objects.create_user(
            username="other-user",
            password="strong-password-123",
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(other)}",
        )

        response, message, _ = self.logout_logged(self.refresh_token)

        # The refresh token is the credential, so it is the token's owner that logs.
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIn(f"user_id={self.user.pk}", message)
        self.assertNotIn(f"user_id={other.pk} ", message)
        self.assertIn("revoked=True", message)

    def test_logout_blacklists_the_token_and_logs_revoked(self):
        response, message, logs = self.logout_logged(self.refresh_token)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIn(f"user_id={self.user.pk}", message)
        self.assertIn("revoked=True", message)
        self.assert_no_token_in(self.refresh_token, "\n".join(logs.output))

    # -- unusable tokens: still 204, nothing revoked --------------------------

    def test_logout_answers_204_and_revokes_nothing_for_unusable_tokens(self):
        claims = jwt.decode(self.refresh_token, options={"verify_signature": False})
        expired = RefreshToken.for_user(self.user)
        expired.set_exp(from_time=timezone.now() - timedelta(days=31))
        rotated = self.fresh_refresh_token()
        self.rotate(rotated)  # blacklisted now, like any already-logged-out token
        cases = {
            # Nothing verifies, so nobody is named in the log.
            "garbage": ("not-a-jwt", "user_id=unknown"),
            "expired": (str(expired), "user_id=unknown"),
            "access token": (self.access_token, "user_id=unknown"),
            "wrong signature": (
                jwt.encode(claims, "x" * 64, algorithm="HS256"),
                "user_id=unknown",
            ),
            # Genuine but already dead: verified, so its owner is named.
            "already blacklisted": (rotated, f"user_id={self.user.pk}"),
        }
        for label, (token, expected_user) in cases.items():
            blacklisted_before = BlacklistedToken.objects.count()
            with self.subTest(token=label):
                response, message, logs = self.logout_logged(token)

                self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
                self.assertIn(expected_user, message)
                self.assertIn("revoked=False", message)
                self.assertEqual(BlacklistedToken.objects.count(), blacklisted_before)
                self.assert_no_token_in(token, "\n".join(logs.output))

    def test_logout_without_a_refresh_token_is_a_quiet_204(self):
        with self.assertLogs() as logs:
            response = self.client.post(reverse("api-logout"), {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        message = self.auth_message(logs)
        self.assertIn("user_id=unknown", message)
        self.assertIn("revoked=False", message)

    # -- hostile bodies ------------------------------------------------------

    def test_logout_does_not_decode_an_oversized_token(self):
        hostile = nested_header_token(10_000)

        with (
            patch.object(auth_views, "RefreshToken") as refresh_token_class,
            patch.object(auth_views, "UntypedToken") as untyped_token_class,
        ):
            response, message, logs = self.logout_logged(hostile)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIn("revoked=False", message)
        refresh_token_class.assert_not_called()
        untyped_token_class.assert_not_called()
        self.assertNotIn(hostile[:200], "\n".join(logs.output))

    def test_logout_ignores_a_refresh_value_that_is_not_ascii(self):
        # Real tokens are ASCII. A lone surrogate used to reach PyJWT, whose
        # UnicodeEncodeError became an HTML 500.
        bodies = {
            "lone surrogate": '{"refresh": "\\ud800"}',
            "non-ascii text": '{"refresh": "t\\u00f3ken"}',
        }
        for label, body in bodies.items():
            with self.subTest(refresh=label), self.assertLogs() as logs:
                response = self.post_raw(body)

                self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
                self.assertIn("revoked=False", self.auth_message(logs))

    def test_logout_with_nested_json_bodies_is_a_400_parse_error(self):
        for label, body in nested_json_bodies().items():
            with self.subTest(body=label):
                response = self.post_raw(body)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.json()["error"]["code"], "parse_error")

    def test_logout_with_malformed_json_is_still_a_400(self):
        response = self.post_raw('{"refresh": ')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_logout_with_a_non_object_body_is_a_204(self):
        with self.assertLogs() as logs:
            response = self.client.post(reverse("api-logout"), ["a"], format="json")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertIn("revoked=False", self.auth_message(logs))
