from datetime import timedelta
from unittest.mock import patch

from django.db import DatabaseError, IntegrityError
from django.test import TestCase
from django.utils import timezone

from streaming import sync
from streaming.models import StreamingAvailability, StreamingOffer, StreamingProvider
from streaming.tests.helpers import (
    GB_LINK,
    HULU,
    NETFLIX,
    TITLE_RESULTS,
    US_LINK,
    activate_region,
    make_snapshot,
    reset_streaming_state,
)


def _offers(title_type, tmdb_id, region):
    return sorted(
        StreamingOffer.objects.filter(
            availability__title_type=title_type,
            availability__tmdb_id=tmdb_id,
            availability__region=region,
        ).values_list("monetization", "provider_id", "display_priority"),
    )


class StoreSnapshotTests(TestCase):
    """Atomic snapshot replacement."""

    def setUp(self):
        reset_streaming_state(self)

    def test_writes_one_row_per_region_with_every_offer_type(self):
        written = sync.store_snapshots("movie", 693134, TITLE_RESULTS, {"US", "gb", "CA", "bad"})

        self.assertEqual(written, {"US", "GB", "CA"})
        rows = {row.region: row for row in StreamingAvailability.objects.filter(tmdb_id="693134")}
        self.assertEqual(set(rows), {"US", "GB", "CA"})
        self.assertEqual(rows["US"].link, US_LINK)
        self.assertEqual(rows["GB"].link, GB_LINK)
        self.assertEqual(rows["CA"].link, "")
        self.assertLess(timezone.now() - rows["US"].fetched_at, timedelta(minutes=1))
        self.assertEqual(
            _offers("movie", "693134", "US"),
            [
                ("ads", 73, 20),
                ("buy", 2, 9),
                ("flatrate", 8, 1),
                ("flatrate", 1899, 151),
                ("free", 191, 30),
                ("rent", 2, 9),
                ("rent", 10, 8),
            ],
        )
        self.assertEqual(_offers("movie", "693134", "GB"), [("flatrate", 8, 1)])
        self.assertEqual(_offers("movie", "693134", "CA"), [])
        self.assertEqual(StreamingProvider.objects.get(pk=1899).name, "HBO Max")

    def test_replaces_offers_in_place(self):
        sync.store_snapshots("movie", "693134", TITLE_RESULTS, {"US"})
        row = StreamingAvailability.objects.get(tmdb_id="693134", region="US")
        StreamingAvailability.objects.filter(pk=row.pk).update(fetched_at=timezone.now() - timedelta(days=3))

        sync.store_snapshots("movie", "693134", {"US": {"flatrate": [HULU]}}, {"US"})

        row.refresh_from_db()
        self.assertEqual(StreamingAvailability.objects.filter(tmdb_id="693134").count(), 1)
        self.assertEqual(row.link, "")
        self.assertLess(timezone.now() - row.fetched_at, timedelta(minutes=1))
        self.assertEqual(_offers("movie", "693134", "US"), [("flatrate", 15, 5)])

    def test_without_replace_existing_rows_are_left_alone(self):
        make_snapshot("movie", "603", "US", [(HULU, "flatrate")])
        before = StreamingAvailability.objects.get(tmdb_id="603", region="US").fetched_at
        stamp = timezone.now() - timedelta(days=2)

        written = sync.store_snapshots("movie", "603", TITLE_RESULTS, {"US", "GB"}, fetched_at=stamp, replace=False)

        self.assertEqual(written, {"GB"})
        self.assertEqual(_offers("movie", "603", "US"), [("flatrate", 15, 5)])
        self.assertEqual(StreamingAvailability.objects.get(tmdb_id="603", region="US").fetched_at, before)
        self.assertEqual(_offers("movie", "603", "GB"), [("flatrate", 8, 1)])
        self.assertEqual(StreamingAvailability.objects.get(tmdb_id="603", region="GB").fetched_at, stamp)

    def test_retries_when_a_concurrent_writer_wins(self):
        real_write = sync._write_snapshots
        calls = []

        def flaky_write(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                raise IntegrityError("duplicate key")
            return real_write(*args, **kwargs)

        with patch("streaming.sync._write_snapshots", side_effect=flaky_write):
            sync.store_snapshots("tv", "1396", {"US": {"flatrate": [NETFLIX]}}, {"US"})

        self.assertEqual(len(calls), 2)
        self.assertEqual(_offers("tv", "1396", "US"), [("flatrate", 8, 1)])

    def test_gives_up_after_repeated_integrity_errors(self):
        with (
            patch("streaming.sync._write_snapshots", side_effect=IntegrityError("duplicate key")) as write_mock,
            self.assertRaises(IntegrityError),
        ):
            sync.store_snapshots("tv", "1396", {}, {"US"})

        self.assertEqual(write_mock.call_count, sync.STORE_ATTEMPTS)


class SyncFromMetadataTests(TestCase):
    """ST-400: detail pages fill in missing snapshots from metadata, without HTTP."""

    def setUp(self):
        reset_streaming_state(self)
        activate_region("GB")
        http = patch("app.providers.services.api_request", side_effect=AssertionError("no HTTP"))
        self.http_mock = http.start()
        self.addCleanup(http.stop)

    def test_writes_missing_active_regions_as_stale_and_leaves_existing_ones(self):
        make_snapshot("movie", "693134", "US", [(HULU, "flatrate")])

        written = sync.sync_from_metadata("tmdb", "movie", "693134", {"providers": TITLE_RESULTS})

        self.assertEqual(written, {"GB"})
        self.assertEqual(_offers("movie", "693134", "US"), [("flatrate", 15, 5)])
        self.assertEqual(_offers("movie", "693134", "GB"), [("flatrate", 8, 1)])
        # Stamped already stale, so the next periodic run fetches it live.
        self.assertEqual(sync.regions_needing_refresh("movie", "693134", {"US", "GB"}), {"GB"})
        self.assertFalse(StreamingAvailability.objects.filter(region="FR").exists())
        self.http_mock.assert_not_called()

    def test_older_cached_metadata_never_rolls_back_a_stored_snapshot(self):
        # A background refresh found the show nowhere a little over a day ago...
        make_snapshot("tv", "1396", "US", age=timedelta(hours=25))
        make_snapshot("tv", "1396", "GB", age=timedelta(hours=25))
        stored = dict(StreamingAvailability.objects.values_list("region", "fetched_at"))

        # ...and a detail view still holds older cached metadata listing offers.
        written = sync.sync_from_metadata("tmdb", "tv", "1396", {"providers": TITLE_RESULTS})

        self.assertEqual(written, set())
        self.assertFalse(StreamingOffer.objects.exists())
        self.assertEqual(dict(StreamingAvailability.objects.values_list("region", "fetched_at")), stored)
        # Still stale, so the background refresh fetches it rather than skip it.
        with patch(
            "streaming.sync.tmdb.fetch_title_providers",
            return_value={"US": {"flatrate": [HULU]}},
        ) as fetch_mock:
            result = sync.refresh_titles([("tv", "1396")])

        self.assertEqual(result["refreshed"], 1)
        fetch_mock.assert_called_once_with("tv", "1396")
        self.assertEqual(_offers("tv", "1396", "US"), [("flatrate", 15, 5)])
        self.assertEqual(sync.regions_needing_refresh("tv", "1396", {"US", "GB"}), set())

    def test_no_providers_anywhere_records_unavailable_rows(self):
        written = sync.sync_from_metadata("tmdb", "movie", "1", {"providers": {}})

        self.assertEqual(written, {"GB", "US"})
        self.assertEqual(StreamingAvailability.objects.filter(tmdb_id="1").count(), 2)
        self.assertFalse(StreamingOffer.objects.exists())

    def test_ignores_seasons_other_sources_and_incomplete_metadata(self):
        metadata = {"providers": TITLE_RESULTS}

        self.assertEqual(sync.sync_from_metadata("tmdb", "season", "1396", metadata), set())
        self.assertEqual(sync.sync_from_metadata("tmdb", "episode", "1396", metadata), set())
        self.assertEqual(sync.sync_from_metadata("mal", "anime", "1", metadata), set())
        self.assertEqual(sync.sync_from_metadata("tmdb", "movie", "2", {"title": "No providers"}), set())
        self.assertEqual(sync.sync_from_metadata("tmdb", "movie", "2", None), set())
        self.assertFalse(StreamingAvailability.objects.exists())

    def test_titles_with_rows_cost_one_query(self):
        make_snapshot("movie", "5", "US")
        make_snapshot("movie", "5", "GB", age=timedelta(days=3))
        sync.active_regions()

        with self.assertNumQueries(1):
            self.assertEqual(sync.sync_from_metadata("tmdb", "movie", "5", {"providers": TITLE_RESULTS}), set())

    def test_database_errors_never_escape(self):
        with (
            patch("streaming.sync.store_snapshots", side_effect=DatabaseError("locked")),
            self.assertLogs("streaming.sync", level="WARNING") as logs,
        ):
            written = sync.sync_from_metadata("tmdb", "movie", "693134", {"providers": TITLE_RESULTS})

        self.assertEqual(written, set())
        self.assertIn("type=DatabaseError", logs.output[0])


class RegionsNeedingRefreshTests(TestCase):
    """Missing and stale snapshot detection."""

    def test_missing_and_stale_regions(self):
        make_snapshot("movie", "9", "US")
        make_snapshot("movie", "9", "GB", age=timedelta(hours=24, minutes=1))

        self.assertEqual(sync.regions_needing_refresh("movie", 9, {"US", "GB", "CA"}), {"GB", "CA"})
        self.assertEqual(sync.regions_needing_refresh("movie", "9", []), set())
