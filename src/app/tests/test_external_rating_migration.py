from datetime import timedelta
from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class ExternalRatingMigrationTests(TransactionTestCase):
    migrate_from = [("app", "0073_externalrating")]
    migrate_to = [("app", "0074_migrate_legacy_external_ratings")]

    def tearDown(self):
        # Restore every app's latest migration, so the next migration test starts
        # from the leaf (a partial state can force a mixed forwards and backwards plan).
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _old_models(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        apps = executor.loader.project_state(self.migrate_from).apps
        return apps, apps.get_model("app", "Item"), apps.get_model(
            "app",
            "ExternalRating",
        )

    def test_copies_all_non_null_legacy_values(self):
        _apps, Item, _ExternalRating = self._old_models()
        historical_at = timezone.now() - timedelta(days=30)
        timestamped = Item.objects.create(
            source="tmdb",
            media_type="movie",
            media_id="timestamped",
            title="Timestamped",
            image="https://example.com/timestamped.jpg",
            letterboxd_rating="4.25",
            imdb_rating="8.50",
            rotten_tomatoes_rating="91",
            filter_metadata_updated_at=historical_at,
        )
        fallback = Item.objects.create(
            source="tmdb",
            media_type="movie",
            media_id="fallback",
            title="Fallback",
            image="https://example.com/fallback.jpg",
            imdb_rating="7.75",
        )
        Item.objects.create(
            source="manual",
            media_type="movie",
            media_id="empty",
            title="Empty",
            image="https://example.com/empty.jpg",
        )
        before = timezone.now()

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        ExternalRating = apps.get_model("app", "ExternalRating")

        self.assertEqual(ExternalRating.objects.count(), 4)
        timestamped_rows = ExternalRating.objects.filter(item_id=timestamped.pk)
        self.assertEqual(
            {
                row.rating_source: (row.value, row.max_value)
                for row in timestamped_rows
            },
            {
                "letterboxd": (4.25, 5),
                "imdb": (8.5, 10),
                "tomatoes": (91, 100),
            },
        )
        self.assertTrue(
            all(
                row.status == "available"
                and row.last_attempted_at == historical_at
                and row.last_success_at == historical_at
                for row in timestamped_rows
            ),
        )
        fallback_row = ExternalRating.objects.get(item_id=fallback.pk)
        self.assertGreaterEqual(fallback_row.last_attempted_at, before)
        self.assertLessEqual(fallback_row.last_attempted_at, timezone.now())
        self.assertEqual(fallback_row.last_success_at, fallback_row.last_attempted_at)

    def test_preserves_existing_rows_and_is_idempotent(self):
        _old_apps, Item, ExternalRating = self._old_models()
        item = Item.objects.create(
            source="tmdb",
            media_type="movie",
            media_id="rolling",
            title="Rolling",
            image="https://example.com/rolling.jpg",
            letterboxd_rating="4.00",
            imdb_rating="7.00",
        )
        existing_at = timezone.now() - timedelta(hours=1)
        existing = ExternalRating.objects.create(
            item_id=item.pk,
            rating_source="imdb",
            value="9.25",
            max_value=10,
            vote_count=123,
            canonical_url="https://www.imdb.com/title/tt0000001/",
            status="available",
            last_attempted_at=existing_at,
            last_success_at=existing_at,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        NewExternalRating = apps.get_model("app", "ExternalRating")
        migration = import_module(
            "app.migrations.0074_migrate_legacy_external_ratings",
        )
        migration.migrate_legacy_external_ratings(apps, None)

        self.assertEqual(NewExternalRating.objects.filter(item_id=item.pk).count(), 2)
        preserved = NewExternalRating.objects.get(pk=existing.pk)
        self.assertEqual(preserved.value, 9.25)
        self.assertEqual(preserved.vote_count, 123)
        self.assertEqual(preserved.last_success_at, existing_at)
        self.assertTrue(
            NewExternalRating.objects.filter(
                item_id=item.pk,
                rating_source="letterboxd",
                value=4,
            ).exists(),
        )
