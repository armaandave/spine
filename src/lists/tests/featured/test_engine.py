from datetime import timedelta
from unittest.mock import patch

import requests
from celery.exceptions import SoftTimeLimitExceeded
from django.core.cache import cache
from django.db import DatabaseError
from django.test import override_settings
from django.utils import timezone

from app.providers import hardcover, igdb, openlibrary, quota
from lists.featured import dispatch, engine, registry
from lists.featured.registry import Guards
from lists.featured.types import AdapterError, FetchResult
from lists.models import FeaturedListSync
from lists.tests.featured.helpers import FeaturedTestCase, entry, fake_adapter, make_definition


def one_movie():
    return [entry(1, "Movie", media_type="movie", ids={"tmdb": "1"}, image="https://x/1.jpg")]


class EngineTests(FeaturedTestCase):
    def test_lock_prevents_concurrent_runs(self):
        definition = make_definition()
        cache.add(f"{engine.LOCK_PREFIX}{definition.key}", "other", 60)
        result = self.run_list(definition, one_movie())
        self.assertEqual(result.status, "locked")
        self.assertFalse(FeaturedListSync.objects.exists())

    def test_lock_is_released_after_success_and_failure(self):
        definition = make_definition()
        self.assertTrue(self.run_list(definition, one_movie()).ok)
        self.assertIsNone(cache.get(f"{engine.LOCK_PREFIX}{definition.key}"))

        def broken(_definition):
            raise AdapterError("source down")

        with fake_adapter(broken), registry.override_definitions([definition]):
            result = engine.run(definition, self.owner)
        self.assertEqual((result.status, result.message), ("failed", "source down"))
        self.assertIsNone(cache.get(f"{engine.LOCK_PREFIX}{definition.key}"))

    def test_failures_are_counted_sanitized_and_clear_the_lease(self):
        definition = make_definition()
        FeaturedListSync.objects.create(key=definition.key, lease_until=timezone.now() + timedelta(minutes=5))

        def broken(_definition):
            raise RuntimeError("GET https://api.test/x?apikey=secret-value-123 failed")

        with fake_adapter(broken), registry.override_definitions([definition]):
            engine.run(definition, self.owner)
            engine.run(definition, self.owner)

        sync = FeaturedListSync.objects.get(key=definition.key)
        self.assertEqual((sync.last_status, sync.consecutive_failures), ("failed", 2))
        self.assertNotIn("secret-value-123", sync.last_error)
        self.assertIsNone(sync.lease_until)
        self.assertIsNotNone(sync.last_attempt_at)
        self.assertIsNone(sync.last_success_at)

        self.assertTrue(self.run_list(definition, one_movie()).ok)
        sync.refresh_from_db()
        self.assertEqual((sync.last_status, sync.consecutive_failures, sync.last_error), ("ok", 0, ""))

    @override_settings(MDBLIST_API="configured-secret-key")
    def test_configured_keys_never_reach_the_error(self):
        definition = make_definition()

        def broken(_definition):
            raise ValueError("bad response for configured-secret-key")

        with fake_adapter(broken), registry.override_definitions([definition]):
            result = engine.run(definition, self.owner)
        self.assertNotIn("configured-secret-key", result.message)
        self.assertNotIn("configured-secret-key", FeaturedListSync.objects.get().last_error)

    def test_missing_owner_is_a_recorded_failure(self):
        self.owner.delete()
        definition = make_definition()
        with fake_adapter(one_movie()), registry.override_definitions([definition]):
            result = engine.run(definition)
        self.assertEqual(result.status, "failed")

    def test_dedupes_and_sorts_ranked_entries(self):
        entries = [
            entry(2, "Two", media_type="movie", ids={"tmdb": "2"}, image="https://x/2.jpg"),
            entry(1, "One", media_type="movie", ids={"tmdb": "1"}, image="https://x/1.jpg"),
            entry(3, "Two again", media_type="movie", ids={"tmdb": "2"}, image="https://x/2.jpg"),
        ]
        result = self.run_list(make_definition(), entries)
        self.assertEqual([item.entry.title for item in result.resolutions], ["One", "Two"])
        self.assertListRefs(result.custom_list, ["tmdb:movie:1", "tmdb:movie:2"])

    def test_dry_run_writes_nothing_and_reports_guards(self):
        definition = make_definition()
        with fake_adapter(FetchResult(entries=one_movie(), expected_count=1)):
            result = engine.dry_run(definition, self.owner)
        self.assertEqual((result.status, result.stored_count, result.message), ("dry-run", 1, ""))
        self.assertFalse(FeaturedListSync.objects.exists())
        with fake_adapter([]):
            self.assertEqual(engine.dry_run(definition, self.owner).error.reason, "empty")

    def test_success_records_definition_hash(self):
        definition = make_definition()
        self.run_list(definition, one_movie())
        with fake_adapter([]):
            expected = engine.compute_hash(definition)
        self.assertEqual(FeaturedListSync.objects.get().definition_hash, expected)


class TitleSearchCapTests(FeaturedTestCase):
    def test_entries_past_the_cap_wait_for_the_next_run(self):
        definition = make_definition(guards=Guards(max_title_searches=1, min_resolve_rate=0.9))
        entries = [entry(rank, f"Film {rank}", year=2000) for rank in (1, 2, 3)]

        def search(media_type, title, page, **kwargs):
            return {"results": [{"media_id": title[-1], "source": "tmdb", "title": title, "release_date": "2000-01-01", "image": "https://x/i.jpg"}]}

        with patch("app.providers.services.search", side_effect=search) as searched:
            first = self.run_list(definition, entries)
            # One search per run; the earlier answers come from the cache.
            self.assertEqual((first.status, first.stored_count), ("deferred", 1))
            sync = FeaturedListSync.objects.get()
            self.assertEqual((sync.resolved_count, sync.entry_count), (1, 3))
            self.assertIsNotNone(sync.deferred_until)
            self.assertEqual(self.run_list(definition, entries).stored_count, 2)
            third = self.run_list(definition, entries)
        self.assertEqual((third.status, third.stored_count), ("ok", 3))
        self.assertEqual(searched.call_count, 3)
        self.assertIsNone(FeaturedListSync.objects.get().deferred_until)


class HardKilledRunTests(FeaturedTestCase):
    """A run killed outright never reaches ``finally``: its attempt already counts."""

    def test_the_attempt_counts_as_a_failure_while_it_runs(self):
        definition = make_definition()
        seen = {}

        def fetch(_definition):
            sync = FeaturedListSync.objects.get(key=definition.key)
            seen["failures"] = sync.consecutive_failures
            seen["decision"] = dispatch.due(definition, sync, sync.last_attempt_at + timedelta(minutes=4))
            return one_movie()

        with fake_adapter(fetch), registry.override_definitions([definition]):
            self.assertTrue(engine.run(definition, self.owner).ok)
        # Had the process died inside fetch, the dispatcher would back off from the list.
        self.assertEqual(seen["failures"], 1)
        self.assertEqual(seen["decision"].reason, "backing off")
        self.assertEqual(FeaturedListSync.objects.get().consecutive_failures, 0)

    def test_killed_attempts_back_off_exponentially(self):
        definition = make_definition()
        sync = FeaturedListSync.objects.create(key=definition.key)
        start = timezone.now()
        for number in range(3):  # three runs that died before recording anything
            engine._start(definition.key, start)
        sync.refresh_from_db()
        self.assertEqual(sync.consecutive_failures, 3)
        self.assertFalse(dispatch.due(definition, sync, start + timedelta(minutes=19)).due)
        self.assertTrue(dispatch.due(definition, sync, start + timedelta(minutes=21)).due)

    def test_failures_count_once(self):
        definition = make_definition()

        def broken(_definition):
            raise AdapterError("source down")

        with fake_adapter(broken), registry.override_definitions([definition]):
            engine.run(definition, self.owner)
        self.assertEqual(FeaturedListSync.objects.get().consecutive_failures, 1)

    def test_a_deferral_takes_the_attempt_back(self):
        definition = make_definition()
        FeaturedListSync.objects.create(key=definition.key, consecutive_failures=2)

        def deferred(_definition):
            raise quota.Deferred("hardcover", timezone.now() + timedelta(hours=1), "is rate limited")

        with fake_adapter(deferred), registry.override_definitions([definition]):
            self.assertEqual(engine.run(definition, self.owner).status, "deferred")
        self.assertEqual(FeaturedListSync.objects.get().consecutive_failures, 2)

    def test_a_database_error_after_locking_releases_the_lock(self):
        definition = make_definition()
        with (
            patch.object(engine, "_start", side_effect=DatabaseError("connection lost")),
            fake_adapter(one_movie()),
            registry.override_definitions([definition]),
        ):
            result = engine.run(definition, self.owner)
        self.assertEqual(result.status, "failed")
        self.assertIsNone(cache.get(f"{engine.LOCK_PREFIX}{definition.key}"))


class FailureLoggingTests(FeaturedTestCase):
    """Container logs never get a traceback, whose frames can hold keyed URLs."""

    @override_settings(MDBLIST_API="configured-secret-key")
    def test_unexpected_errors_log_one_sanitized_line(self):
        definition = make_definition()

        def broken(_definition):
            try:
                raise requests.ConnectionError("Max retries exceeded with url: /x?apikey=configured-secret-key")
            except requests.ConnectionError as error:
                raise RuntimeError("wrapped") from error

        with (
            fake_adapter(broken),
            registry.override_definitions([definition]),
            self.assertLogs("lists.featured.engine", level="INFO") as logs,
        ):
            engine.run(definition, self.owner)
        failure = next(record for record in logs.records if record.levelname == "ERROR")
        self.assertIsNone(failure.exc_info)
        self.assertIn("RuntimeError: wrapped at ", failure.getMessage())
        self.assertNotIn("configured-secret-key", "\n".join(logs.output))


class ProviderSoftLimitTests(FeaturedTestCase):
    """Providers' broad excepts let a soft time limit stop the run."""

    def test_hardcover_series_books(self):
        with patch("app.providers.services.api_request", side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                hardcover.get_series_books(1)

    def test_openlibrary_author_search(self):
        with patch("app.providers.openlibrary.requests.get", side_effect=SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                openlibrary.search_author_by_name("Annie Ernaux")

    def test_igdb_covers(self):
        with (
            patch("app.providers.igdb.get_access_token", return_value="token"),
            patch("app.providers.services.api_request", side_effect=SoftTimeLimitExceeded()),
            self.assertRaises(SoftTimeLimitExceeded),
        ):
            igdb.get_game_covers(1)
