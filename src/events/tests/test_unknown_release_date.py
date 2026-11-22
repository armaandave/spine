from datetime import UTC, datetime
from importlib import import_module
from unittest import mock
from zoneinfo import ZoneInfo

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from app.models import Item, MediaTypes, Sources
from events.models import Event, SentinelDatetime

MIGRATION = "0015_unknown_release_dates_in_range"
migration = import_module(f"events.migrations.{MIGRATION}")


class UnknownReleaseDateTests(TestCase):
    def test_an_unknown_release_date_converts_to_local_time(self):
        unknown = SentinelDatetime.min_datetime()

        # Production's timezone, and the furthest behind UTC a midday placeholder allows.
        for zone in ("America/New_York", "Pacific/Pago_Pago"):
            with self.subTest(zone=zone):
                self.assertEqual(unknown.astimezone(ZoneInfo(zone)).year, 1)
        self.assertLess(unknown, datetime(1900, 1, 1, tzinfo=UTC))

    def test_the_migration_moves_stored_unknown_dates_into_range(self):
        with mock.patch("app.models.Item.fetch_releases"):
            item = Item.objects.create(
                media_id="1",
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                season_number=1,
                title="Show",
                image="https://example.com/poster.jpg",
            )
        unknown = Event.objects.create(item=item, content_number=2, datetime=datetime.min.replace(tzinfo=UTC))
        known = Event.objects.create(item=item, content_number=1, datetime=datetime(2026, 11, 21, 1, tzinfo=UTC))

        state = MigrationExecutor(connection).loader.project_state(("events", MIGRATION))
        migration.move_unknown_release_dates_into_range(state.apps, None)

        unknown.refresh_from_db()
        known.refresh_from_db()
        self.assertEqual(unknown.datetime, SentinelDatetime.min_datetime())
        self.assertEqual(known.datetime, datetime(2026, 11, 21, 1, tzinfo=UTC))
