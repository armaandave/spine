from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from lists.featured import engine, registry
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
