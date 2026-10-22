import importlib
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import clear_url_caches

import config.urls


class DevMediaServingTests(TestCase):
    """Dev servers serve uploads, like profile pictures, to the app without a site login."""

    def tearDown(self):
        importlib.reload(config.urls)
        clear_url_caches()

    def test_uploaded_file_loads_without_a_site_login(self):
        with tempfile.TemporaryDirectory() as media_root:
            Path(media_root, "avatar.jpeg").write_bytes(b"image")
            with override_settings(DEBUG=True, MEDIA_ROOT=media_root):
                importlib.reload(config.urls)
                clear_url_caches()

                response = self.client.get("/media/avatar.jpeg")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(b"".join(response.streaming_content), b"image")
