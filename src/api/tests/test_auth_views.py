"""Tests for how the unauthenticated auth views treat malformed and hostile input.

Every one of these views answers bad input with a 400 in the normal API error
envelope, never an HTML 500. Password reset used to crash on an invalid email or on
a confirmation of the wrong shape, and any view that reads a JSON body crashed on
one nested past the decoder's limit.
"""

import logging
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import status
from rest_framework.test import APIClient

AUTH_LOGGER = "api.views.auth"
RESET_ANSWER = {"detail": "If an account exists, a reset email has been sent."}
# Deeper than the JSON decoder allows (its recursion limit is about 10,000 levels).
NESTING_DEPTH = 20_000


class AuthViewTestCase(TestCase):
    """A client that reports a server crash as the 500 a real caller would see."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="reset-user",
            email="reset-user@example.com",
            password="strong-password-123",
        )

    def setUp(self):
        cache.clear()  # the auth endpoints are throttled per client
        self.client = APIClient(raise_request_exception=False)

    def post(self, url_name, body):
        return self.client.post(reverse(url_name), body, format="json")

    def assert_bad_request(self, response, *fields):
        """Assert a 400 in the normal envelope that names every one of ``fields``."""
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response["Content-Type"], "application/json")
        error = response.json()["error"]
        self.assertEqual(error["code"], "invalid")
        for field in fields:
            self.assertIn(field, error["fields"])


class PasswordResetTests(AuthViewTestCase):
    """POST /api/v1/auth/password-reset/."""

    def test_malformed_input_is_a_400_in_the_normal_envelope(self):
        cases = (
            ("invalid email", {"email": "not-an-email"}, "email"),
            ("blank email", {"email": ""}, "email"),
            ("missing email", {}, "email"),
            ("null email", {"email": None}, "email"),
            ("number", {"email": 123}, "email"),
            ("list", {"email": ["reset-user@example.com"]}, "email"),
            ("object", {"email": {"a": 1}}, "email"),
            ("too long", {"email": f"{'a' * 300}@example.com"}, "email"),
            ("top-level list", ["reset-user@example.com"], "non_field_errors"),
        )
        for label, body, field in cases:
            with self.subTest(body=label):
                response = self.post("api-password-reset", body)

                self.assert_bad_request(response, field)
                self.assertEqual(mail.outbox, [])

    def test_known_and_unknown_addresses_get_the_same_answer(self):
        # Whether an account exists must not show in the response. That includes the
        # email failing to send, which today it always does for a real account.
        logging.disable(logging.ERROR)
        self.addCleanup(logging.disable, logging.NOTSET)

        known = self.post("api-password-reset", {"email": "reset-user@example.com"})
        unknown = self.post("api-password-reset", {"email": "nobody@example.com"})

        self.assertEqual(known.status_code, status.HTTP_200_OK)
        self.assertEqual(known.json(), RESET_ANSWER)
        self.assertEqual(unknown.status_code, known.status_code)
        self.assertEqual(unknown.json(), known.json())

    def test_a_failure_sending_the_email_is_not_visible_to_the_caller(self):
        failure = RuntimeError("smtp is down for reset-user@example.com")

        with (
            patch.object(PasswordResetForm, "save", side_effect=failure),
            self.assertLogs(AUTH_LOGGER, level="ERROR") as logs,
        ):
            response = self.post(
                "api-password-reset", {"email": "reset-user@example.com"}
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json(), RESET_ANSWER)
        # The failure is logged for the operator: its type only, since the message
        # can carry an address or a link.
        output = "\n".join(logs.output)
        self.assertIn("RuntimeError", output)
        self.assertNotIn("smtp is down", output)
        self.assertNotIn("reset-user@example.com", output)


class PasswordResetConfirmTests(AuthViewTestCase):
    """POST /api/v1/auth/password-reset/confirm/."""

    def link(self):
        return {
            "uid": urlsafe_base64_encode(force_bytes(self.user.pk)),
            "token": default_token_generator.make_token(self.user),
        }

    def test_malformed_input_is_a_400_in_the_normal_envelope(self):
        cases = (
            ("empty body", {}, ("uid", "token", "new_password")),
            ("null uid", {"uid": None, "token": "t", "new_password": "p"}, ("uid",)),
            ("int uid", {"uid": 5, "token": "t", "new_password": "p"}, ("uid",)),
            ("list uid", {"uid": ["a"], "token": "t", "new_password": "p"}, ("uid",)),
            ("int token", {"uid": "u", "token": 5, "new_password": "p"}, ("token",)),
            (
                "null token",
                {"uid": "u", "token": None, "new_password": "p"},
                ("token",),
            ),
            ("no password", {"uid": "u", "token": "t"}, ("new_password",)),
            (
                "list password",
                {"uid": "u", "token": "t", "new_password": ["x"]},
                ("new_password",),
            ),
            ("top-level list", ["a"], ("non_field_errors",)),
        )
        for label, body, fields in cases:
            with self.subTest(body=label):
                response = self.post("api-password-reset-confirm", body)

                self.assert_bad_request(response, *fields)

    def test_a_valid_link_resets_the_password(self):
        response = self.post(
            "api-password-reset-confirm",
            {**self.link(), "new_password": "a-brand-new-password-987"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("a-brand-new-password-987"))

    def test_a_wrong_link_is_still_a_400_and_changes_nothing(self):
        response = self.post(
            "api-password-reset-confirm",
            {"uid": "bad", "token": "bad", "new_password": "a-brand-new-password-987"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("strong-password-123"))

    def test_a_weak_password_is_still_refused(self):
        response = self.post(
            "api-password-reset-confirm",
            {**self.link(), "new_password": "short"},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("strong-password-123"))


class HostileBodyTests(AuthViewTestCase):
    """A JSON body nested past the decoder's limit is a 400, not an HTML 500."""

    def test_nested_json_bodies_are_a_400_parse_error(self):
        deep = "[" * NESTING_DEPTH + "]" * NESTING_DEPTH
        bodies = {
            "top-level array": deep,
            "inside a field": f'{{"email": {deep}}}',
            "inside another key": f'{{"other": {deep}}}',
        }
        for url_name in (
            "api-register",
            "api-login",
            "api-password-reset",
            "api-password-reset-confirm",
        ):
            for label, body in bodies.items():
                with self.subTest(view=url_name, body=label):
                    cache.clear()

                    response = self.client.post(
                        reverse(url_name),
                        body,
                        content_type="application/json",
                    )

                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    self.assertEqual(response.json()["error"]["code"], "parse_error")
