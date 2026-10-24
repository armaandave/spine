"""Dev sign-in answers only on a local dev server, and never in production.

Production runs with DEBUG off, on Postgres, without SPINE_DEV_SIGN_IN, so each guard
alone must turn the route into a 404 that creates nothing.
"""

import os
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from api.views.auth import DEV_SIGN_IN_USERNAME, dev_sign_in_enabled

LOCAL_FLAG = {"SPINE_DEV_SIGN_IN": "1"}
SQLITE = SimpleNamespace(vendor="sqlite")
POSTGRES = SimpleNamespace(vendor="postgresql")


def without_flag():
    """Patch the environment, with SPINE_DEV_SIGN_IN removed until the patch ends."""
    patcher = patch.dict(os.environ)
    patcher.start()
    os.environ.pop("SPINE_DEV_SIGN_IN", None)
    return patcher


class DevSignInGuardTests(SimpleTestCase):
    """The guard is on only when DEBUG, SQLite and the flag all hold."""

    def enabled(self, *, debug, env, database):
        patcher = without_flag()
        self.addCleanup(patcher.stop)
        os.environ.update(env)
        with override_settings(DEBUG=debug), patch("api.views.auth.connection", database):
            return dev_sign_in_enabled()

    def test_local_dev_server_enables_it(self):
        self.assertTrue(self.enabled(debug=True, env=LOCAL_FLAG, database=SQLITE))

    def test_production_configuration_disables_it(self):
        self.assertFalse(self.enabled(debug=False, env={}, database=POSTGRES))

    def test_each_guard_alone_disables_it(self):
        cases = [
            ("DEBUG off", False, LOCAL_FLAG, SQLITE),
            ("Postgres", True, LOCAL_FLAG, POSTGRES),
            ("flag missing", True, {}, SQLITE),
            ("flag not exactly 1", True, {"SPINE_DEV_SIGN_IN": "true"}, SQLITE),
        ]
        for name, debug, env, database in cases:
            with self.subTest(name):
                self.assertFalse(self.enabled(debug=debug, env=env, database=database))


class DevSignInViewTests(TestCase):
    """The route signs in as the dev user when enabled, and is a 404 otherwise."""

    def setUp(self):
        self.api = APIClient()
        self.url = reverse("api-dev-sign-in")

    def post(self, *, enabled):
        with patch("api.views.auth.dev_sign_in_enabled", return_value=enabled):
            return self.api.post(self.url, {}, format="json")

    def dev_users(self):
        return get_user_model().objects.filter(username=DEV_SIGN_IN_USERNAME)

    def test_enabled_signs_in_as_the_passwordless_dev_user(self):
        response = self.post(enabled=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["username"], DEV_SIGN_IN_USERNAME)
        self.assertFalse(self.dev_users().get().has_usable_password())

    def test_signing_in_again_reuses_the_dev_user(self):
        self.post(enabled=True)
        self.post(enabled=True)

        self.assertEqual(self.dev_users().count(), 1)

    def test_disabled_answers_404_and_creates_nothing(self):
        response = self.post(enabled=False)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.dev_users().exists())

    def test_test_settings_without_the_flag_answer_404(self):
        # The test runner turns DEBUG off, like production; nothing is patched here.
        patcher = without_flag()
        self.addCleanup(patcher.stop)

        response = self.api.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.dev_users().exists())
