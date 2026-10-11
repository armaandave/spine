"""Media latency benchmark: scenarios, HTTP cassettes, and the Markdown report.

The benchmark_media_latency management command drives these against the real API
views. A live run records every provider response into a cassette; a replay run
serves that cassette with no network and no rate limits, so its numbers and call
counts repeat exactly.
"""

import base64
import hashlib
import json
import statistics
from contextlib import contextmanager
from dataclasses import asdict
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlparse

import requests
from pyrate_limiter import Limiter
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict

from app import request_timing
from app.providers import services

# One fixed search query and one item to open per media type.
CORPUS = {
    "movie": {"query": "inception", "source": "tmdb", "media_id": "27205"},
    "tv": {"query": "breaking bad", "source": "tmdb", "media_id": "1396"},
    "anime": {"query": "cowboy bebop", "source": "mal", "media_id": "1"},
    "manga": {"query": "berserk", "source": "mal", "media_id": "2"},
    "game": {"query": "the witcher 3", "source": "igdb", "media_id": "1942"},
    "book": {"query": "dune", "source": "hardcover", "media_id": "312460"},
    "comic": {"query": "saga", "source": "comicvine", "media_id": "46568"},
    "music": {
        "query": "ok computer",
        "source": "musicbrainz",
        "media_id": "b1392450-e666-3926-a536-22c65f834433",
    },
}
ALL_MEDIA = "all"
ALL_MEDIA_QUERY = "dune"
# Steps a person triggers; the rest fire automatically right after "detail".
USER_ACTIONS = {"search", "detail"}

SECRET_PARAMS = {"api_key", "apikey", "key", "token", "access_token", "client_id", "client_secret"}
SECRET_FIELDS = ("access_token", "refresh_token", "id_token")
KEPT_HEADERS = {"content-type", "location", "retry-after"}
CATEGORIES = (
    ("network", "http"),
    ("limiter wait", "wait"),
    ("429 sleep", "retry_sleep"),
    ("db", "db"),
)


def steps(media_type):
    """Return the (step, path) requests the iOS app makes to search for and open media."""
    if media_type == ALL_MEDIA:
        return [("search", "/api/v1/media/search/?" + urlencode({"q": ALL_MEDIA_QUERY, "scope": "all"}))]
    item = CORPUS[media_type]
    search = ("search", "/api/v1/media/search/?" + urlencode({"q": item["query"], "media_type": media_type}))
    base = f"/api/v1/media/{item['source']}/{media_type}/{item['media_id']}"
    reviews = ("reviews", f"{base}/reviews/?sort=popular")
    if media_type == "music":
        # The app paints the basic album first, then loads enrichment and reviews together.
        return [search, ("detail", f"{base}/basic/"), ("enrichment", f"{base}/enrichment/"), reviews]
    return [search, ("detail", f"{base}/"), reviews]


def measurement(media_type, step, state, run, response):
    """Capture one API response's status and timing spans."""
    timings = response.wsgi_request.server_timing
    return {
        "media_type": media_type,
        "step": step,
        "state": state,
        "run": run,
        "status": response.status_code,
        "total_ms": timings.total_ms,
        "spans": [asdict(span) for span in timings.spans],
    }


def breakdown(record):
    """Split one measurement into time categories and per-host call counts."""
    spans = [request_timing.Span(**span) for span in record["spans"]]
    result = {"total": record["total_ms"]}
    for label, kind in CATEGORIES:
        result[label] = sum(span.duration_ms for span in spans if span.kind == kind)
    result["app"] = max(record["total_ms"] - request_timing.covered_ms(spans), 0.0)
    result["queries"] = sum(1 for span in spans if span.kind == "db")
    result["tasks"] = sum(1 for span in spans if span.kind == "task")
    result["misses"] = sum(1 for span in spans if span.kind == "miss")
    calls = {}
    for span in spans:
        if span.kind == "http":
            calls[span.name] = calls.get(span.name, 0) + 1
    result["calls"] = calls
    return result


def rate_limit_floor_ms(calls):
    """Lowest possible limiter wait for these per-host call counts, from idle buckets."""
    floor = 0
    for host, count in calls.items():
        for rate in _host_rates(host):
            floor = max(floor, (count - 1) // rate.limit * rate.interval)
    return floor


def _host_rates(host):
    # MusicBrainz goes through its own 1/s session (musicbrainz._musicbrainz_request).
    session = services.musicbrainz_session if host == "musicbrainz.org" else services.session
    rates = list(session.limiter.bucket_factory.rates)
    for prefix, adapter in session.adapters.items():
        limiter = getattr(adapter, "limiter", None)
        if limiter is not None and urlparse(prefix).hostname == host:
            rates.extend(limiter.bucket_factory.rates)
    return rates


class Cassette:
    """Recorded provider responses keyed by request, with credentials stripped."""

    def __init__(self, entries=None):
        self.entries = entries or {}

    @staticmethod
    def key(request):
        """Identify a request by method, URL without credentials, and body hash."""
        url = urlparse(request.url)
        query = sorted(
            (name, value)
            for name, value in parse_qsl(url.query, keep_blank_values=True)
            if name.lower() not in SECRET_PARAMS
        )
        body = request.body or b""
        if isinstance(body, str):
            body = body.encode()
        digest = hashlib.sha256(body).hexdigest()[:16] if body else "-"
        return f"{request.method} {url.scheme}://{url.netloc}{url.path}?{urlencode(query)} {digest}"

    def record(self, request, response):
        """Store a response, keeping an earlier success over a later 429."""
        key = self.key(request)
        if response.status_code == requests.codes.too_many_requests and key in self.entries:
            return
        self.entries[key] = {
            "status": response.status_code,
            "headers": {
                name.lower(): value
                for name, value in response.headers.items()
                if name.lower() in KEPT_HEADERS
            },
            "body": base64.b64encode(_redact(response.content)).decode(),
        }

    def response(self, request):
        """Rebuild the recorded response for a request, or None if it was never seen."""
        entry = self.entries.get(self.key(request))
        if entry is None:
            return None
        response = requests.Response()
        response.status_code = entry["status"]
        response.headers = CaseInsensitiveDict(entry["headers"])
        response._content = base64.b64decode(entry["body"])
        response._content_consumed = True
        response.encoding = requests.utils.get_encoding_from_headers(response.headers)
        response.reason = ""
        response.url = request.url
        response.request = request
        return response

    def save(self, path):
        """Write the cassette as JSON."""
        path.write_text(json.dumps(self.entries, sort_keys=True))

    @classmethod
    def load(cls, path):
        """Read a cassette written by save()."""
        return cls(json.loads(path.read_text()))


def _redact(content):
    """Blank OAuth tokens so credentials never land in the cassette file."""
    try:
        payload = json.loads(content)
    except ValueError:
        return content
    if isinstance(payload, dict) and any(field in payload for field in SECRET_FIELDS):
        redacted = {
            name: "REDACTED" if name in SECRET_FIELDS else value
            for name, value in payload.items()
        }
        return json.dumps(redacted).encode()
    return content


@contextmanager
def recording(cassette):
    """Store every non-streamed response that passes through requests."""
    send = HTTPAdapter.send

    def recording_send(adapter, request, *args, **kwargs):
        response = send(adapter, request, *args, **kwargs)
        if not kwargs.get("stream", args[0] if args else False):
            cassette.record(request, response)
        return response

    with patch.object(HTTPAdapter, "send", recording_send):
        yield


@contextmanager
def replaying(cassette):
    """Serve responses from the cassette with no network and no rate-limit waits."""

    def replay_send(adapter, request, *args, **kwargs):
        response = cassette.response(request)
        if response is None:
            with request_timing.span("miss", urlparse(request.url).hostname or "unknown"):
                pass
            raise requests.ConnectionError(f"Not in cassette: {Cassette.key(request)}")
        return response

    def no_wait(limiter, name="pyrate", *args, **kwargs):
        return True

    with (
        patch.object(HTTPAdapter, "send", request_timing._timed_adapter_send(replay_send)),
        patch.object(Limiter, "try_acquire", request_timing._timed_try_acquire(no_wait)),
    ):
        yield


def summarize(results):
    """Median breakdown per (media_type, step, state)."""
    grouped = {}
    for record in results:
        grouped.setdefault((record["media_type"], record["step"], record["state"]), []).append(record)
    summary = {}
    for key, records in grouped.items():
        breakdowns = [breakdown(record) for record in records]
        row = {
            metric: statistics.median(item[metric] for item in breakdowns)
            for metric in ("total", "app", "queries", "tasks", "misses")
            + tuple(label for label, _kind in CATEGORIES)
        }
        totals = [item["total"] for item in breakdowns]
        calls = [item["calls"] for item in breakdowns]
        hosts = sorted({host for counts in calls for host in counts})
        row.update(
            runs=len(records),
            min=min(totals),
            max=max(totals),
            calls={host: (min(c.get(host, 0) for c in calls), max(c.get(host, 0) for c in calls)) for host in hosts},
            floor=max(rate_limit_floor_ms(counts) for counts in calls),
            errors=sorted({record["status"] for record in records if record["status"] >= 400}),  # noqa: PLR2004
        )
        summary[key] = row
    return summary


def flows(results):
    """Median search, open (detail), and fully loaded times per (media_type, state).

    Fully loaded adds the slowest follow-up request to detail, because the app
    loads enrichment and reviews in parallel after the detail response.
    """
    runs = {}
    for record in results:
        runs.setdefault((record["media_type"], record["state"], record["run"]), {})[record["step"]] = record["total_ms"]
    collected = {}
    for (media_type, state, _run), step_ms in runs.items():
        entry = collected.setdefault((media_type, state), {"search": [], "open": [], "loaded": []})
        if "search" in step_ms:
            entry["search"].append(step_ms["search"])
        if "detail" in step_ms:
            followups = [ms for step, ms in step_ms.items() if step not in {"search", "detail"}]
            entry["open"].append(step_ms["detail"])
            entry["loaded"].append(step_ms["detail"] + max(followups, default=0))
    return {
        key: {name: statistics.median(values) if values else None for name, values in entry.items()}
        for key, entry in collected.items()
    }


def top_costs(results, media_type, state="cold", limit=3):
    """Largest span groups across the open requests (everything but search), per run."""
    records = [
        record
        for record in results
        if record["media_type"] == media_type and record["state"] == state and record["step"] != "search"
    ]
    run_count = len({record["run"] for record in records}) or 1
    totals = {}
    for record in records:
        spans = [request_timing.Span(**span) for span in record["spans"]]
        groups = request_timing.group_spans(spans)
        groups["app"] = (max(record["total_ms"] - request_timing.covered_ms(spans), 0.0), None)
        for group, (duration_ms, _count) in groups.items():
            totals[group] = totals.get(group, 0.0) + duration_ms / run_count
    return sorted(totals.items(), key=lambda item: -item[1])[:limit]


def render_report(results, meta):
    """Render the Markdown report for one benchmark run."""
    summary = summarize(results)
    flow = flows(results)
    media_types = list(dict.fromkeys(record["media_type"] for record in results))
    lines = [
        f"# Media latency benchmark ({meta['mode']})",
        "",
        f"- Started: {meta['started_at']}",
        f"- Commit: {meta['git']}",
        f"- Runs per step: {meta['cold_runs']} cold, {meta['warm_runs']} warm",
        "- In-process Django test client, throwaway SQLite database, isolated Redis cache prefix, "
        "debug toolbar off, DEBUG=False. Celery tasks are counted, not delivered.",
    ]
    if meta.get("pause"):
        lines.append(
            f"- Cold runs pause {meta['pause']:g} s before each search and open, like a person reading "
            "results; follow-up requests fire immediately, as in the app.",
        )
    if meta["mode"] == "replay":
        lines.append(f"- Providers served from `{meta['cassette']}` with no network and no rate-limit waits.")
    lines += [
        "",
        "## What each media type costs",
        "",
        "Medians. **Open** is the detail request that blocks the screen. **Fully loaded** adds the slowest "
        "follow-up request (reviews, music enrichment), which the app loads in parallel. Cold means an empty cache.",
        "",
        "| Type | Search cold | Search warm | Open cold | Open warm | Fully loaded cold | Fully loaded warm | Biggest cold costs per open |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for media_type in media_types:
        cold = flow.get((media_type, "cold"), {})
        warm = flow.get((media_type, "warm"), {})
        costs = ", ".join(f"{group} {_ms(ms)}" for group, ms in top_costs(results, media_type)) or "—"
        lines.append(
            f"| {media_type} | {_ms(cold.get('search'))} | {_ms(warm.get('search'))} | {_ms(cold.get('open'))} "
            f"| {_ms(warm.get('open'))} | {_ms(cold.get('loaded'))} | {_ms(warm.get('loaded'))} | {costs} |",
        )
    for state in ("cold", "warm"):
        lines += [
            "",
            f"## {state.title()} breakdown per request",
            "",
            "Median ms. Network, limiter wait, and DB add up time from every thread, so parallel work can "
            "exceed the total. App is wall time outside any span (Python, cache, serialization). The limiter "
            "floor is the minimum wait the configured rate limits force for these call counts.",
            "",
            "| Type | Step | Total (min–max) | Network | Limiter wait | 429 sleep | DB (queries) | App | Provider calls | Limiter floor | Tasks | Errors |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for media_type in media_types:
            for step, _path in steps(media_type):
                row = summary.get((media_type, step, state))
                if row is None:
                    continue
                calls = ", ".join(
                    f"{host} ×{low}" if low == high else f"{host} ×{low}–{high}"
                    for host, (low, high) in row["calls"].items()
                ) or "none"
                errors = ", ".join(str(status) for status in row["errors"]) or "—"
                if row["misses"]:
                    errors += f" ({row['misses']:.0f} cassette misses)"
                lines.append(
                    f"| {media_type} | {step} | {_ms(row['total'])} ({_ms(row['min'])}–{_ms(row['max'])}) "
                    f"| {_ms(row['network'])} | {_ms(row['limiter wait'])} | {_ms(row['429 sleep'])} "
                    f"| {_ms(row['db'])} ({row['queries']:.0f}) | {_ms(row['app'])} | {calls} "
                    f"| {_ms(row['floor'])} | {row['tasks']:.0f} | {errors} |",
                )
    return "\n".join(lines) + "\n"


def _ms(value):
    if value is None:
        return "—"
    if value >= 1000:  # noqa: PLR2004
        return f"{value / 1000:.2f} s"
    return f"{value:.0f} ms"
