"""Measure search and open latency per media type the way the iOS app calls the API."""

import json
import logging
import subprocess
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

from celery.signals import before_task_publish
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test.utils import override_settings
from django.utils import timezone
from django_redis.cache import RedisCache
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from app import latency_benchmark, request_timing
from config.celery import app as celery_app

CACHE_PREFIX = "media-latency-benchmark"


def _count_task(sender=None, **kwargs):
    with request_timing.span("task", str(sender)):
        pass


class Command(BaseCommand):
    """Run each media type's search, detail, and follow-up requests cold and warm."""

    help = __doc__

    def add_arguments(self, parser):
        """Register benchmark options."""
        parser.add_argument(
            "--types",
            default=",".join([*latency_benchmark.CORPUS, latency_benchmark.ALL_MEDIA]),
            help="Comma-separated media types; 'all' is the all-media search.",
        )
        parser.add_argument("--cold-runs", type=int, default=3)
        parser.add_argument("--warm-runs", type=int, default=5)
        parser.add_argument(
            "--pause",
            type=float,
            default=1.5,
            help="Seconds to wait before each cold user action (search, open) so "
            "per-second rate-limit windows drain, like a person reading results.",
        )
        parser.add_argument(
            "--replay",
            type=Path,
            help="cassette.json from a live run; serve providers from it offline.",
        )
        parser.add_argument("--output-dir", type=Path)

    def handle(self, *args, **options):
        """Run the benchmark and write report.md, results.json, and cassette.json."""
        types = [value.strip() for value in options["types"].split(",") if value.strip()]
        known = {*latency_benchmark.CORPUS, latency_benchmark.ALL_MEDIA}
        if unknown := sorted(set(types) - known):
            raise CommandError(f"Unknown media types: {', '.join(unknown)}")
        mode = "replay" if options["replay"] else "live"
        started_at = timezone.now()
        output_dir = options["output_dir"] or (
            settings.BASE_DIR.parent
            / "benchmarks"
            / "media-latency"
            / f"{started_at:%Y%m%d-%H%M%S}-{mode}"
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        if mode == "replay":
            cassette = latency_benchmark.Cassette.load(options["replay"])
        else:
            cassette = latency_benchmark.Cassette()

        request_timing.install()
        # Tasks are published to an in-memory broker so a local worker never runs
        # them against the real database; each publish is counted as a span.
        celery_app.conf.broker_url = "memory://"
        before_task_publish.connect(_count_task)
        with ExitStack() as stack:
            stack.enter_context(self._isolated_environment())
            if mode == "replay":
                stack.enter_context(latency_benchmark.replaying(cassette))
            else:
                stack.enter_context(latency_benchmark.recording(cassette))
            pause = 0 if mode == "replay" else options["pause"]
            results = self._run(types, options["cold_runs"], options["warm_runs"], pause)
        before_task_publish.disconnect(_count_task)

        meta = {
            "mode": mode,
            "started_at": started_at.isoformat(timespec="seconds"),
            "git": _git_revision(),
            "cold_runs": options["cold_runs"],
            "warm_runs": options["warm_runs"],
            "pause": pause,
            "cassette": str(options["replay"]) if options["replay"] else None,
        }
        (output_dir / "results.json").write_text(
            json.dumps({"meta": meta, "results": results}, indent=1),
        )
        if mode == "live":
            cassette.save(output_dir / "cassette.json")
        report = latency_benchmark.render_report(results, meta)
        (output_dir / "report.md").write_text(report)
        self.stdout.write(report)
        self.stdout.write(self.style.SUCCESS(f"Wrote {output_dir}"))

    @contextmanager
    def _isolated_environment(self):
        """Throwaway database, own cache prefix, no throttles, toolbar, or debug cursors."""
        with ExitStack() as stack:
            workdir = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            stack.enter_context(
                override_settings(
                    DEBUG=False,
                    ALLOWED_HOSTS=["testserver"],
                    SERVER_TIMING=True,
                    MIDDLEWARE=[name for name in settings.MIDDLEWARE if "debug_toolbar" not in name],
                    CACHES={
                        "default": {
                            "BACKEND": "django_redis.cache.RedisCache",
                            "LOCATION": settings.REDIS_URL,
                            "TIMEOUT": settings.CACHE_TIMEOUT,
                            "KEY_PREFIX": CACHE_PREFIX,
                            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
                        },
                    },
                ),
            )
            stack.enter_context(_quiet_logs())
            stack.enter_context(
                patch.object(SimpleRateThrottle, "allow_request", lambda *args, **kwargs: True),
            )
            if connection.vendor == "sqlite":
                connection.settings_dict["TEST"]["NAME"] = str(workdir / "benchmark.sqlite3")
            self.stdout.write("Creating throwaway database...")
            old_name = connection.creation.create_test_db(verbosity=0, autoclobber=True, serialize=False)
            stack.callback(connection.creation.destroy_test_db, old_name, verbosity=0)
            yield

    def _run(self, types, cold_runs, warm_runs, pause):
        if not isinstance(caches["default"], RedisCache):
            raise CommandError("Benchmark cache override did not apply.")
        user = get_user_model().objects.create_user(username="latency-benchmark")
        client = APIClient()
        client.force_authenticate(user)
        client.raise_request_exception = False
        # Load URLconf, DRF, and middleware once so the first measured type isn't penalized.
        client.get("/api/v1/health/")

        results = []
        for media_type in types:
            for state, runs in (("cold", cold_runs), ("warm", warm_runs)):
                for run in range(1, runs + 1):
                    if state == "cold":
                        # Only this benchmark's keys; rate-limit buckets and Celery keep theirs.
                        cache.delete_pattern("*")
                    for step, path in latency_benchmark.steps(media_type):
                        if state == "cold" and step in latency_benchmark.USER_ACTIONS:
                            time.sleep(pause)
                        response = client.get(path)
                        record = latency_benchmark.measurement(media_type, step, state, run, response)
                        results.append(record)
                        self.stdout.write(
                            f"{media_type:<6} {step:<10} {state} #{run}  "
                            f"{record['total_ms']:>9.1f} ms  HTTP {record['status']}",
                        )
        cache.delete_pattern("*")
        return results


@contextmanager
def _quiet_logs():
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.WARNING)
    try:
        yield
    finally:
        root.setLevel(level)


def _git_revision():
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{revision}{' (uncommitted changes)' if dirty else ''}"
