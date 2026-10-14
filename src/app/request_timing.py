"""Per-request timing spans that show where API request time goes.

While ServerTimingMiddleware is collecting, spans record network time per
provider host ("http"), rate-limiter waits ("wait"), HTTP 429 retry sleeps
("retry_sleep"), and database queries ("db").
"""

import contextvars
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlparse

from pyrate_limiter import Limiter
from requests.adapters import HTTPAdapter

_current = contextvars.ContextVar("request_timings", default=None)
_installed = False


@dataclass(slots=True)
class Span:
    """One timed operation, with its start relative to the request start."""

    kind: str
    name: str
    start_ms: float
    duration_ms: float


@dataclass
class RequestTimings:
    """Spans collected for one request."""

    started_at: float = field(default_factory=time.perf_counter)
    spans: list = field(default_factory=list)
    total_ms: float | None = None

    def add(self, kind, name, started_at):
        """Record a span that began at started_at and ends now."""
        # list.append is atomic, so thread-pool workers can share one collector.
        self.spans.append(
            Span(
                kind,
                name,
                round((started_at - self.started_at) * 1000, 1),
                round((time.perf_counter() - started_at) * 1000, 1),
            ),
        )


@contextmanager
def collect():
    """Collect spans recorded anywhere in this request for the block's duration."""
    timings = RequestTimings()
    token = _current.set(timings)
    try:
        yield timings
    finally:
        timings.total_ms = round((time.perf_counter() - timings.started_at) * 1000, 1)
        _current.reset(token)


@contextmanager
def span(kind, name):
    """Time the block as one span when a collector is active."""
    timings = _current.get()
    if timings is None:
        yield
        return
    started_at = time.perf_counter()
    try:
        yield
    finally:
        timings.add(kind, name, started_at)


def db_execute_wrapper(execute, sql, params, many, context):
    """Time one query; install with connection.execute_wrapper()."""
    with span("db", context["connection"].alias):
        return execute(sql, params, many, context)


def install():
    """Wrap the HTTP, rate-limiter, and thread-pool seams once per process.

    The wrappers pass straight through when no collector is active.
    """
    global _installed  # noqa: PLW0603
    if _installed:
        return
    HTTPAdapter.send = _timed_adapter_send(HTTPAdapter.send)
    Limiter.try_acquire = _timed_try_acquire(Limiter.try_acquire)
    ThreadPoolExecutor.submit = _propagating_submit(ThreadPoolExecutor.submit)
    _installed = True


def _timed_adapter_send(send):
    def timed_send(self, request, *args, **kwargs):
        if _current.get() is None:
            return send(self, request, *args, **kwargs)
        stream = kwargs.get("stream", args[0] if args else False)
        with span("http", urlparse(request.url).hostname or "unknown"):
            response = send(self, request, *args, **kwargs)
            if not stream:
                # Session.send reads the body right after this returns; reading it
                # here counts the download as network time.
                response.content  # noqa: B018
        return response

    return timed_send


def _timed_try_acquire(try_acquire):
    def timed_try_acquire(self, name="pyrate", *args, **kwargs):
        # requests_ratelimiter names buckets after the request host.
        with span("wait", str(name)):
            return try_acquire(self, name, *args, **kwargs)

    return timed_try_acquire


def _propagating_submit(submit):
    def propagating_submit(self, fn, /, *args, **kwargs):
        timings = _current.get()
        if timings is not None:
            fn = _bind(timings, fn)
        return submit(self, fn, *args, **kwargs)

    return propagating_submit


def _bind(timings, fn):
    def bound(*args, **kwargs):
        token = _current.set(timings)
        try:
            return fn(*args, **kwargs)
        finally:
            _current.reset(token)

    return bound


def group_spans(spans):
    """Sum spans by "kind.name" ("db" for all queries) as {group: (duration_ms, count)}."""
    groups = {}
    for span in spans:
        group = "db" if span.kind == "db" else f"{span.kind}.{span.name}"
        duration_ms, count = groups.get(group, (0.0, 0))
        groups[group] = (duration_ms + span.duration_ms, count + 1)
    return groups


def covered_ms(spans):
    """Wall-clock time covered by at least one span (parallel spans count once)."""
    covered = 0.0
    start = end = None
    for span in sorted(spans, key=lambda span: span.start_ms):
        span_end = span.start_ms + span.duration_ms
        if end is None or span.start_ms > end:
            if end is not None:
                covered += end - start
            start, end = span.start_ms, span_end
        else:
            end = max(end, span_end)
    if end is not None:
        covered += end - start
    return covered


def app_ms(timings):
    """Request time outside every span: Python work, cache calls, serialization."""
    return max(timings.total_ms - covered_ms(timings.spans), 0.0)


def server_timing_header(timings):
    """Summarize spans as a Server-Timing header value, largest cost first.

    For example: total;dur=6210.4, wait.musicbrainz.org;dur=4100.2;desc="n=6", ...
    """
    groups = group_spans(timings.spans)
    groups["app"] = (app_ms(timings), None)
    metrics = [("total", timings.total_ms, None)]
    for name, (duration_ms, count) in sorted(groups.items(), key=lambda item: -item[1][0]):
        metrics.append((name, duration_ms, f"n={count}" if count else None))
    return ", ".join(_format_metric(*metric) for metric in metrics)


def _format_metric(name, duration_ms, description=None):
    """Format one metric, turning the name into a header-safe token."""
    token = re.sub(r"[^A-Za-z0-9!#$%&'*+.^_`|~-]", "-", name)
    metric = f"{token};dur={duration_ms:.1f}"
    if description:
        escaped = description.replace("\\", "\\\\").replace('"', '\\"')
        metric += f';desc="{escaped}"'
    return metric
