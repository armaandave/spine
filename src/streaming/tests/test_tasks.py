import json
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from app.models import TV, Item, MediaTypes, Movie, Season, Sources, Status
from app.providers.services import ProviderAPIError
from config.celery import app as celery_app
from lists.models import CustomList, CustomListItem
from streaming import tasks
from streaming.library import library_titles, titles_needing_refresh
from streaming.models import StreamingAvailability, StreamingOffer
from streaming.tests.helpers import (
    TITLE_RESULTS,
    activate_region,
    make_snapshot,
    reset_streaming_state,
)


def _item(media_type, media_id, source=Sources.TMDB.value, **extra):
    return Item.objects.create(
        source=source,
        media_type=media_type,
        media_id=media_id,
        title=f"{media_type} {media_id}",
        image="https://example.com/poster.jpg",
        **extra,
    )


def _not_found():
    response = type("Response", (), {"status_code": 404, "text": "missing"})()
    return ProviderAPIError("tmdb", type("Error", (), {"response": response})())


class RefreshStreamingTitlesTests(TestCase):
    """Background refresh batches."""

    def setUp(self):
        reset_streaming_state(self)
        activate_region("GB")
        fetch = patch("streaming.sync.tmdb.fetch_title_providers", return_value=TITLE_RESULTS)
        self.fetch_mock = fetch.start()
        self.addCleanup(fetch.stop)

    def test_stores_every_active_region_and_logs_a_summary(self):
        with self.assertLogs("streaming.tasks", level="INFO") as logs:
            result = tasks.refresh_streaming_titles([["movie", "693134"], ["tv", 1396], ["book", "1"]])

        self.assertEqual(result["refreshed"], 2)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["regions"], ["GB", "US"])
        self.assertEqual(
            set(StreamingAvailability.objects.values_list("title_type", "tmdb_id", "region")),
            {("movie", "693134", "US"), ("movie", "693134", "GB"), ("tv", "1396", "US"), ("tv", "1396", "GB")},
        )
        summary = json.loads(logs.output[-1].split("streaming_refresh_summary ", 1)[1])
        self.assertEqual(summary, result)

    def test_fresh_titles_skip_tmdb_unless_forced(self):
        make_snapshot("movie", "1", "US")
        make_snapshot("movie", "1", "GB")

        self.assertEqual(tasks.refresh_streaming_titles([["movie", "1"]])["fresh"], 1)
        self.fetch_mock.assert_not_called()

        self.assertEqual(tasks.refresh_streaming_titles([["movie", "1"]], force=True)["refreshed"], 1)
        self.fetch_mock.assert_called_once_with("movie", "1")

    def test_lock_deduplicates_concurrent_refreshes(self):
        with patch("streaming.sync.cache.add", return_value=False):
            result = tasks.refresh_streaming_titles([["movie", "2"]])

        self.assertEqual(result["deduplicated"], 1)
        self.fetch_mock.assert_not_called()

    def test_one_failing_title_does_not_stop_the_batch(self):
        self.fetch_mock.side_effect = [RuntimeError("boom"), TITLE_RESULTS]

        with self.assertLogs("streaming.sync", level="WARNING") as logs:
            result = tasks.refresh_streaming_titles([["movie", "3"], ["movie", "4"]])

        self.assertEqual((result["failed"], result["refreshed"]), (1, 1))
        self.assertIn("type=RuntimeError", logs.output[0])
        self.assertFalse(StreamingAvailability.objects.filter(tmdb_id="3").exists())
        self.assertTrue(StreamingAvailability.objects.filter(tmdb_id="4").exists())

    def test_missing_tmdb_title_is_recorded_as_unavailable(self):
        self.fetch_mock.side_effect = _not_found()

        result = tasks.refresh_streaming_titles([["tv", "999999999"]])

        self.assertEqual(result["refreshed"], 1)
        self.assertEqual(StreamingAvailability.objects.filter(tmdb_id="999999999").count(), 2)
        self.assertFalse(StreamingOffer.objects.exists())

    def test_batches_are_bounded_and_names_are_stable(self):
        with self.assertRaisesMessage(ValueError, "limited to 100 titles"):
            tasks.refresh_streaming_titles([["movie", str(index)] for index in range(101)])

        self.assertEqual(tasks.refresh_streaming_titles.name, "Refresh streaming availability")
        self.assertEqual(tasks.check_user_streaming_library.name, "Check streaming library")
        self.assertEqual(
            celery_app.tasks["Queue stale streaming availability"].name,
            tasks.queue_stale_streaming_availability.name,
        )
        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULE["queue_stale_streaming_availability"],
            {"task": "Queue stale streaming availability", "schedule": 60 * 60 * 6},
        )

    @patch("streaming.tasks.refresh_streaming_titles.delay")
    def test_enqueue_helper_dedupes_and_chunks(self, delay_mock):
        titles = [("movie", index) for index in range(150)] + [("movie", "0")]

        self.assertEqual(tasks.enqueue_refresh_batches(titles), 2)

        first, second = (call.args[0] for call in delay_mock.call_args_list)
        self.assertEqual((len(first), len(second)), (100, 50))
        self.assertEqual(first[0], ["movie", "0"])
        self.assertEqual(delay_mock.call_args.kwargs, {"force": False})


class LibraryCheckTests(TestCase):
    """ST-102: saving settings checks the user's library for their country."""

    def setUp(self):
        reset_streaming_state(self)
        self.user = activate_region("GB")
        self.other = get_user_model().objects.create_user(username="other", password="pw-12345678")

    def _library(self):
        movie = _item(MediaTypes.MOVIE.value, "603")
        tv_item = _item(MediaTypes.TV.value, "1396")
        season_item = _item(MediaTypes.SEASON.value, "1399", season_number=1)
        Movie.objects.bulk_create([Movie(user=self.user, item=movie, status=Status.COMPLETED.value)])
        tv = TV.objects.bulk_create([TV(user=self.user, item=tv_item, status=Status.IN_PROGRESS.value)])[0]
        season_tv = TV.objects.bulk_create(
            [TV(user=self.other, item=_item(MediaTypes.TV.value, "1399"), status=Status.IN_PROGRESS.value)],
        )[0]
        Season.objects.bulk_create(
            [Season(user=self.user, item=season_item, related_tv=season_tv, status=Status.IN_PROGRESS.value)],
        )
        owned = CustomList.objects.create(name="Owned", owner=self.user)
        shared = CustomList.objects.create(name="Shared", owner=self.other)
        shared.collaborators.add(self.user)
        foreign = CustomList.objects.create(name="Foreign", owner=self.other)
        CustomListItem.objects.create(custom_list=owned, item=_item(MediaTypes.EPISODE.value, "60059", season_number=1, episode_number=1))
        CustomListItem.objects.create(custom_list=owned, item=_item(MediaTypes.BOOK.value, "OL1M", source=Sources.OPENLIBRARY.value))
        CustomListItem.objects.create(custom_list=shared, item=_item(MediaTypes.MOVIE.value, "550"))
        CustomListItem.objects.create(custom_list=foreign, item=_item(MediaTypes.MOVIE.value, "13"))
        Movie.objects.bulk_create(
            [Movie(user=self.other, item=_item(MediaTypes.MOVIE.value, "680"), status=Status.COMPLETED.value)],
        )
        Movie.objects.bulk_create(
            [Movie(user=self.user, item=_item(MediaTypes.MOVIE.value, "m1", source=Sources.MANUAL.value), status=Status.COMPLETED.value)],
        )
        return tv

    def test_library_scope_maps_seasons_and_episodes_to_shows(self):
        self._library()

        self.assertEqual(
            library_titles(self.user),
            {("movie", "603"), ("tv", "1396"), ("tv", "1399"), ("tv", "60059"), ("movie", "550")},
        )
        self.assertEqual(
            library_titles(),
            {("movie", "603"), ("tv", "1396"), ("tv", "1399"), ("tv", "60059"), ("movie", "550"), ("movie", "13"), ("movie", "680")},
        )

    @patch("streaming.tasks.refresh_streaming_titles.delay")
    def test_queues_missing_then_stale_titles_for_the_users_country(self, delay_mock):
        self._library()
        make_snapshot("movie", "603", "GB")
        make_snapshot("tv", "1396", "GB", age=timedelta(days=2))
        make_snapshot("movie", "550", "GB", age=timedelta(days=3))
        make_snapshot("tv", "1399", "US")

        result = tasks.check_user_streaming_library(self.user.pk)

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["region"], "GB")
        self.assertEqual((result["missing"], result["stale"], result["batches"]), (2, 2, 1))
        delay_mock.assert_called_once_with(
            [["tv", "1399"], ["tv", "60059"], ["movie", "550"], ["tv", "1396"]],
            force=False,
        )

    @patch("streaming.tasks.refresh_streaming_titles.delay")
    def test_skips_when_disabled_inactive_or_missing(self, delay_mock):
        self._library()
        inactive = get_user_model().objects.create_user(username="no-services", password="pw-12345678")
        inactive.watch_provider_region = "DE"
        inactive.save(update_fields=["watch_provider_region"])

        self.assertEqual(tasks.check_user_streaming_library(inactive.pk)["status"], "skipped")
        self.assertEqual(tasks.check_user_streaming_library(987654)["status"], "skipped")
        self.user.watch_provider_region = ""
        self.user.save(update_fields=["watch_provider_region"])
        self.assertEqual(tasks.check_user_streaming_library(self.user.pk)["status"], "skipped")
        delay_mock.assert_not_called()

    def test_library_check_refreshes_titles_end_to_end(self):
        self._library()

        with patch("streaming.sync.tmdb.fetch_title_providers", return_value=TITLE_RESULTS) as fetch_mock:
            tasks.check_user_streaming_library(self.user.pk)

        self.assertEqual(fetch_mock.call_count, 5)
        self.assertEqual(titles_needing_refresh(library_titles(self.user), {"GB", "US"}), ([], []))


class QueueStaleStreamingTests(TestCase):
    """ST-400 periodic refresh selection."""

    def setUp(self):
        reset_streaming_state(self)
        self.user = activate_region("GB")
        for media_id in ("1", "2", "3", "4"):
            CustomListItem.objects.create(
                custom_list=CustomList.objects.get_or_create(name="Watchlist", owner=self.user)[0],
                item=_item(MediaTypes.MOVIE.value, media_id),
            )

    @patch("streaming.tasks.region_names")
    @patch("streaming.tasks.refresh_streaming_titles.delay")
    def test_missing_first_then_oldest_within_the_bound(self, delay_mock, names_mock):
        for region in ("US", "GB"):
            make_snapshot("movie", "1", region, age=timedelta(days=1, hours=1))
            make_snapshot("movie", "2", region, age=timedelta(days=5))
            make_snapshot("movie", "4", region)
        make_snapshot("movie", "3", "US")

        with patch("streaming.tasks.STALE_QUEUE_LIMIT", 2):
            result = tasks.queue_stale_streaming_availability()

        self.assertEqual(
            result,
            {"regions": ["GB", "US"], "titles": 4, "missing": 1, "stale": 2, "selected": 2, "batches": 1},
        )
        delay_mock.assert_called_once_with([["movie", "3"], ["movie", "2"]], force=False)
        names_mock.assert_called_once()

    @patch("streaming.tasks.region_names", side_effect=RuntimeError("down"))
    @patch("streaming.tasks.refresh_streaming_titles.delay")
    def test_region_name_warmup_failure_is_logged(self, delay_mock, _names_mock):
        with self.assertLogs("streaming.tasks", level="WARNING"):
            result = tasks.queue_stale_streaming_availability()

        self.assertEqual(result["selected"], 4)
        delay_mock.assert_called_once()

    def test_titles_needing_refresh_handles_empty_inputs(self):
        self.assertEqual(titles_needing_refresh(set(), {"US"}), ([], []))
        self.assertEqual(titles_needing_refresh({("movie", "1")}, set()), ([], []))
