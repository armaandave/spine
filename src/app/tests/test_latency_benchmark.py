from types import SimpleNamespace

import requests
from django.test import SimpleTestCase

from app import latency_benchmark, request_timing
from app.latency_benchmark import Cassette


def _span(kind, name, start_ms, duration_ms):
    return {"kind": kind, "name": name, "start_ms": start_ms, "duration_ms": duration_ms}


def _record(media_type, step, state, run, total_ms, spans=(), status=200):
    return {
        "media_type": media_type,
        "step": step,
        "state": state,
        "run": run,
        "status": status,
        "total_ms": total_ms,
        "spans": list(spans),
    }


class SpanMathTests(SimpleTestCase):
    """Parallel spans are counted once and grouped by kind and host."""

    def test_covered_ms_merges_overlapping_spans(self):
        spans = [
            request_timing.Span("http", "a", 0, 100),
            request_timing.Span("http", "b", 50, 100),
            request_timing.Span("db", "default", 300, 10),
        ]

        self.assertEqual(request_timing.covered_ms(spans), 160)

    def test_header_lists_total_then_largest_groups_with_counts(self):
        timings = request_timing.RequestTimings(
            spans=[
                request_timing.Span("wait", "musicbrainz.org", 0, 2000),
                request_timing.Span("http", "musicbrainz.org", 2000, 300),
                request_timing.Span("http", "musicbrainz.org", 2300, 200),
                request_timing.Span("db", "default", 2500, 5),
            ],
            total_ms=2600,
        )

        self.assertEqual(
            request_timing.server_timing_header(timings),
            'total;dur=2600.0, wait.musicbrainz.org;dur=2000.0;desc="n=1", '
            'http.musicbrainz.org;dur=500.0;desc="n=2", app;dur=95.0, db;dur=5.0;desc="n=1"',
        )


class BreakdownTests(SimpleTestCase):
    """Measurements become categories, call counts, and report rows."""

    def test_breakdown_splits_categories_and_counts_calls(self):
        record = _record(
            "music",
            "detail",
            "cold",
            1,
            1500,
            [
                _span("wait", "musicbrainz.org", 0, 1000),
                _span("http", "musicbrainz.org", 1000, 300),
                _span("http", "coverartarchive.org", 1000, 400),
                _span("db", "default", 1400, 20),
                _span("task", "app.tasks.enrich", 1420, 0),
            ],
        )

        result = latency_benchmark.breakdown(record)

        self.assertEqual(result["network"], 700)
        self.assertEqual(result["limiter wait"], 1000)
        self.assertEqual(result["queries"], 1)
        self.assertEqual(result["tasks"], 1)
        self.assertEqual(result["app"], 80)
        self.assertEqual(result["calls"], {"musicbrainz.org": 1, "coverartarchive.org": 1})

    def test_musicbrainz_floor_is_one_second_per_extra_call(self):
        self.assertEqual(latency_benchmark.rate_limit_floor_ms({"musicbrainz.org": 6}), 5000)
        self.assertEqual(latency_benchmark.rate_limit_floor_ms({"api.themoviedb.org": 3}), 0)

    def test_flows_add_slowest_followup_to_open(self):
        results = [
            _record("music", "search", "cold", 1, 900),
            _record("music", "detail", "cold", 1, 1000),
            _record("music", "enrichment", "cold", 1, 4000),
            _record("music", "reviews", "cold", 1, 50),
        ]

        self.assertEqual(
            latency_benchmark.flows(results)[("music", "cold")],
            {"search": 900, "open": 1000, "loaded": 5000},
        )

    def test_report_renders_summary_and_breakdowns(self):
        results = [
            _record("movie", "search", "cold", 1, 400, [_span("http", "api.themoviedb.org", 10, 350)]),
            _record("movie", "detail", "cold", 1, 600, [_span("http", "api.themoviedb.org", 10, 500)]),
            _record("movie", "reviews", "cold", 1, 30, status=502),
        ]
        meta = {"mode": "live", "started_at": "now", "git": "abc", "cold_runs": 1, "warm_runs": 0, "cassette": None}

        report = latency_benchmark.render_report(results, meta)

        self.assertIn("| movie | 400 ms | — | 600 ms | — | 630 ms | — |", report)
        self.assertIn("api.themoviedb.org ×1", report)
        self.assertIn("| 502 |", report)

    def test_ios_music_open_uses_basic_then_enrichment(self):
        self.assertEqual(
            [step for step, _path in latency_benchmark.steps("music")],
            ["search", "detail", "enrichment", "reviews"],
        )
        self.assertTrue(latency_benchmark.steps("music")[1][1].endswith("/basic/"))


class CassetteTests(SimpleTestCase):
    """Recorded responses replay exactly and never store credentials."""

    def _request(self, url, method="GET", data=None):
        return requests.Request(method, url, data=data).prepare()

    def _response(self, status, body, headers=None):
        response = requests.Response()
        response.status_code = status
        response._content = body
        response.headers.update(headers or {"Content-Type": "application/json"})
        return response

    def test_key_drops_credentials_and_sorts_params(self):
        first = self._request("https://api.themoviedb.org/3/movie/27205?language=en&api_key=SECRET")
        second = self._request("https://api.themoviedb.org/3/movie/27205?api_key=OTHER&language=en")

        self.assertEqual(Cassette.key(first), Cassette.key(second))
        self.assertNotIn("SECRET", Cassette.key(first))

    def test_round_trip_and_token_redaction(self):
        cassette = Cassette()
        request = self._request("https://id.twitch.tv/oauth2/token?client_secret=SECRET", "POST")
        cassette.record(request, self._response(200, b'{"access_token": "abc", "expires_in": 10}'))

        replayed = cassette.response(request)

        self.assertEqual(replayed.json(), {"access_token": "REDACTED", "expires_in": 10})
        self.assertEqual(replayed.headers["content-type"], "application/json")

    def test_rate_limited_retry_does_not_replace_success(self):
        cassette = Cassette()
        request = self._request("https://api.jikan.moe/v4/anime/1")
        cassette.record(request, self._response(200, b'{"ok": true}'))
        cassette.record(request, self._response(429, b"{}"))

        self.assertEqual(cassette.response(request).status_code, 200)

    def test_replay_serves_cassette_through_requests_and_reports_misses(self):
        cassette = Cassette()
        cassette.record(
            self._request("https://musicbrainz.org/ws/2/release-group/x?fmt=json"),
            self._response(200, b'{"title": "OK Computer"}'),
        )

        with latency_benchmark.replaying(cassette), request_timing.collect() as timings:
            response = requests.get("https://musicbrainz.org/ws/2/release-group/x?fmt=json", timeout=1)
            with self.assertRaises(requests.ConnectionError):
                requests.get("https://musicbrainz.org/ws/2/release-group/missing", timeout=1)

        self.assertEqual(response.json(), {"title": "OK Computer"})
        self.assertEqual(
            [(span.kind, span.name) for span in timings.spans],
            [("http", "musicbrainz.org"), ("miss", "musicbrainz.org"), ("http", "musicbrainz.org")],
        )

    def test_measurement_reads_spans_from_request(self):
        timings = request_timing.RequestTimings(
            spans=[request_timing.Span("db", "default", 1, 2)],
            total_ms=10,
        )
        response = SimpleNamespace(status_code=200, wsgi_request=SimpleNamespace(server_timing=timings))

        record = latency_benchmark.measurement("tv", "detail", "warm", 2, response)

        self.assertEqual(record["total_ms"], 10)
        self.assertEqual(record["spans"], [_span("db", "default", 1, 2)])
