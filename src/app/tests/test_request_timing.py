import io
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from requests.adapters import HTTPAdapter

from app import request_timing
from app.providers import services


def _spans(timings, kind):
    return [(span.name, span.duration_ms >= 0) for span in timings.spans if span.kind == kind]


class RequestTimingTests(TestCase):
    """Spans are collected per request across threads and library seams."""

    def test_span_records_only_while_collecting(self):
        with request_timing.span("db", "outside"):
            pass
        with request_timing.collect() as timings:
            with request_timing.span("db", "default"):
                pass

        self.assertEqual(_spans(timings, "db"), [("default", True)])
        self.assertGreaterEqual(timings.spans[0].start_ms, 0)
        self.assertIsNotNone(timings.total_ms)

    def test_thread_pool_work_reports_to_the_submitting_request(self):
        request_timing.install()

        def work():
            with request_timing.span("http", "musicbrainz.org"):
                return "done"

        with request_timing.collect() as timings, ThreadPoolExecutor(max_workers=1) as executor:
            self.assertEqual(executor.submit(work).result(), "done")

        self.assertEqual(_spans(timings, "http"), [("musicbrainz.org", True)])

    def test_install_is_idempotent(self):
        request_timing.install()
        send = HTTPAdapter.send
        request_timing.install()

        self.assertIs(HTTPAdapter.send, send)

    def test_adapter_send_records_host_and_reads_body(self):
        def send(adapter, request, **kwargs):
            response = requests.Response()
            response.status_code = 200
            response.raw = io.BytesIO(b'{"ok": true}')
            return response

        timed_send = request_timing._timed_adapter_send(send)
        request = requests.Request("GET", "https://musicbrainz.org/ws/2/release?fmt=json").prepare()

        with request_timing.collect() as timings:
            response = timed_send(HTTPAdapter(), request, stream=False)

        self.assertEqual(_spans(timings, "http"), [("musicbrainz.org", True)])
        self.assertTrue(response._content_consumed)

    def test_limiter_wait_records_bucket_name(self):
        timed_try_acquire = request_timing._timed_try_acquire(lambda limiter, name, **kwargs: True)

        with request_timing.collect() as timings:
            self.assertTrue(timed_try_acquire(None, "api.myanimelist.net", weight=1, blocking=True))

        self.assertEqual(_spans(timings, "wait"), [("api.myanimelist.net", True)])

    def test_db_queries_record_connection_alias(self):
        with (
            request_timing.collect() as timings,
            connection.execute_wrapper(request_timing.db_execute_wrapper),
        ):
            get_user_model().objects.count()

        self.assertEqual(_spans(timings, "db"), [("default", True)])

    @patch("app.providers.services.time.sleep")
    @patch("app.providers.services.session.get")
    def test_api_request_records_rate_limit_retry_sleep(self, mock_get, mock_sleep):
        limited = MagicMock(status_code=429, headers={"Retry-After": "1"})
        error = requests.exceptions.HTTPError("429 Too Many Requests")
        error.response = limited
        limited.raise_for_status.side_effect = error
        ok = MagicMock()
        ok.json.return_value = {"data": "test"}
        mock_get.side_effect = [limited, ok]

        with request_timing.collect() as timings:
            services.api_request("TEST", "GET", "https://api.jikan.moe/v4/anime")

        self.assertEqual(_spans(timings, "retry_sleep"), [("api.jikan.moe", True)])

    @override_settings(SERVER_TIMING=True)
    def test_middleware_adds_header_and_keeps_spans_on_request(self):
        response = self.client.get("/api/v1/health/")

        self.assertIn("total;dur=", response["Server-Timing"])
        self.assertIsNotNone(response.wsgi_request.server_timing.total_ms)

    def test_middleware_is_off_by_default_in_tests(self):
        response = self.client.get("/api/v1/health/")

        self.assertNotIn("Server-Timing", response)
