from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from celery.exceptions import Retry
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from app.external_ratings import TransientExternalRatingError
from app.models import (
    ExternalRating,
    Item,
    MediaTypes,
    Sources,
    UserMessage,
    UserMessageLevel,
)
from app.tasks import (
    EXTERNAL_RATING_BATCH_SIZE,
    EXTERNAL_RATING_STALE_LIMIT,
    _stale_external_rating_item_ids,
    cleanup_user_messages,
    enrich_external_ratings,
    enrich_external_ratings_batch,
    enqueue_external_rating_batches,
    queue_stale_external_ratings,
    refresh_music_genre_albums,
)
from config.celery import app as celery_app


class CleanupUserMessagesTaskTests(TestCase):
    """Test cleanup of old shown user messages."""

    def setUp(self):
        """Create a user for task tests."""
        self.user = get_user_model().objects.create_user(
            username="test",
        )

    @override_settings(USER_MESSAGE_RETENTION_DAYS=30)
    def test_cleanup_user_messages_deletes_only_old_shown_messages(self):
        """Delete only shown messages older than the retention window."""
        now = timezone.now()
        old_shown = UserMessage.objects.create(
            user=self.user,
            level=UserMessageLevel.INFO,
            message="old shown",
            shown_at=now - timedelta(days=31),
        )
        recent_shown = UserMessage.objects.create(
            user=self.user,
            level=UserMessageLevel.INFO,
            message="recent shown",
            shown_at=now - timedelta(days=5),
        )
        unseen = UserMessage.objects.create(
            user=self.user,
            level=UserMessageLevel.INFO,
            message="unseen",
        )

        deleted_count = cleanup_user_messages()

        self.assertEqual(deleted_count, 1)
        self.assertFalse(UserMessage.objects.filter(id=old_shown.id).exists())
        self.assertTrue(UserMessage.objects.filter(id=recent_shown.id).exists())
        self.assertTrue(UserMessage.objects.filter(id=unseen.id).exists())


class ExternalRatingTaskTests(TestCase):
    def setUp(self):
        cache.clear()
        self.item = Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Cowboy Bebop",
            image="https://example.com/bebop.jpg",
        )

    def _rating(self, status):
        return ExternalRating.objects.create(
            item=self.item,
            rating_source="mal",
            value="8.7" if status == ExternalRating.Status.AVAILABLE else None,
            max_value=10,
            status=status,
            last_attempted_at=timezone.now(),
            last_success_at=(
                timezone.now()
                if status == ExternalRating.Status.AVAILABLE
                else None
            ),
        )

    @patch("app.tasks.refresh_external_ratings")
    def test_fresh_terminal_rows_skip_refresh(self, refresh_mock):
        for status in (
            ExternalRating.Status.AVAILABLE,
            ExternalRating.Status.UNAVAILABLE,
        ):
            with self.subTest(status=status):
                self.item.external_ratings.all().delete()
                self._rating(status)

                result = enrich_external_ratings(self.item.pk, ["mal"])

                self.assertEqual(result["status"], "fresh")
                self.assertEqual(result["fresh"], 1)
        refresh_mock.assert_not_called()

    @patch("app.tasks.refresh_external_ratings", return_value={"mal": "available"})
    def test_force_refreshes_fresh_row(self, refresh_mock):
        self._rating(ExternalRating.Status.AVAILABLE)

        result = enrich_external_ratings(self.item.pk, ["mal"], force=True)

        self.assertEqual(result["status"], "refreshed")
        refresh_mock.assert_called_once_with(
            self.item,
            ["mal"],
            raise_transient=True,
        )

    def test_deleted_manual_and_unsupported_items_skip(self):
        deleted_id = self.item.pk
        self.item.delete()
        manual = Item.objects.create(
            media_id=Item.generate_manual_id(),
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Manual",
            image="https://example.com/manual.jpg",
        )
        unsupported = Item.objects.create(
            media_id="comic",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Comic",
            image="https://example.com/comic.jpg",
        )

        self.assertEqual(enrich_external_ratings(deleted_id)["status"], "skipped")
        self.assertEqual(enrich_external_ratings(manual.pk)["status"], "skipped")
        self.assertEqual(
            enrich_external_ratings(unsupported.pk)["status"],
            "skipped",
        )

    @patch("app.tasks.cache.add", return_value=False)
    @patch("app.tasks.refresh_external_ratings")
    def test_duplicate_lock_skips_expensive_work(self, refresh_mock, _cache_add):
        result = enrich_external_ratings(self.item.pk, ["mal"])

        self.assertEqual(result["status"], "deduplicated")
        refresh_mock.assert_not_called()

    @patch("app.tasks.enrich_external_ratings.retry", side_effect=Retry())
    @patch("app.tasks._enrich_external_ratings")
    def test_transient_failure_uses_bounded_retry(self, enrich_mock, retry_mock):
        enrich_mock.side_effect = TransientExternalRatingError(["mal"])

        with self.assertRaises(Retry):
            enrich_external_ratings(self.item.pk, ["mal"], force=True)

        retry_mock.assert_called_once_with(
            args=(),
            kwargs={
                "item_id": self.item.pk,
                "rating_sources": ["mal"],
                "force": False,
            },
            countdown=60,
        )

    @patch("app.tasks.refresh_external_ratings", return_value={"mal": "unavailable"})
    def test_unavailable_is_terminal_without_retry(self, _refresh_mock):
        result = enrich_external_ratings(self.item.pk, ["mal"])

        self.assertEqual(result["status"], "refreshed")
        self.assertEqual(result["unavailable"], 1)

    @patch("app.tasks.refresh_external_ratings", return_value={"mal": "failed"})
    def test_permanent_failure_is_not_retried(self, _refresh_mock):
        result = enrich_external_ratings(self.item.pk, ["mal"])

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed"], 1)

    @patch("app.tasks.refresh_external_ratings", return_value={"mal": "failed"})
    def test_failed_attempt_reports_preserved_stale_value(self, _refresh_mock):
        self._rating(ExternalRating.Status.AVAILABLE)

        result = enrich_external_ratings(self.item.pk, ["mal"], force=True)

        self.assertEqual(result["attempted"], 1)
        self.assertEqual(result["preserved_stale"], 1)
        self.assertEqual(result["skipped_fresh"], 0)

    @patch("app.tasks.enrich_external_ratings.retry", side_effect=Retry())
    @patch("app.tasks.cache.add", side_effect=ConnectionError)
    @patch("app.tasks.refresh_external_ratings")
    def test_cache_outage_retries_without_provider_work(
        self,
        refresh_mock,
        _cache_add,
        retry_mock,
    ):
        with self.assertRaises(Retry):
            enrich_external_ratings(self.item.pk, ["mal"])

        refresh_mock.assert_not_called()
        self.assertEqual(retry_mock.call_args.kwargs["countdown"], 60)

    @patch("app.tasks.enrich_external_ratings.apply_async")
    @patch("app.tasks._enrich_external_ratings")
    def test_mixed_batch_continues_and_delegates_transient_retry(
        self,
        enrich_mock,
        apply_async_mock,
    ):
        def result(item_id, _sources, _force):
            if item_id == 1:
                return {"status": "refreshed"}
            if item_id == 2:
                return {"status": "fresh"}
            if item_id == 3:
                return {"status": "skipped"}
            if item_id == 4:
                raise TransientExternalRatingError(["mal"])
            raise ValueError

        enrich_mock.side_effect = result

        counts = enrich_external_ratings_batch(
            [1, 1, 2, 3, 4, 5],
            ["mal"],
            force=True,
        )

        self.assertEqual(counts["requested"], 5)
        self.assertEqual(counts["refreshed"], 1)
        self.assertEqual(counts["fresh"], 1)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["retrying"], 1)
        self.assertEqual(counts["failed"], 1)
        apply_async_mock.assert_called_once_with(
            kwargs={
                "item_id": 4,
                "rating_sources": ["mal"],
                "force": False,
            },
            countdown=60,
        )

    def test_batch_limit_and_task_names_are_stable(self):
        with self.assertRaisesMessage(ValueError, "limited to 100 items"):
            enrich_external_ratings_batch(range(EXTERNAL_RATING_BATCH_SIZE + 1))

        self.assertEqual(enrich_external_ratings.name, "Enrich external ratings")
        self.assertEqual(
            enrich_external_ratings_batch.name,
            "Enrich external ratings batch",
        )
        self.assertEqual(
            queue_stale_external_ratings.name,
            "Queue stale external ratings",
        )
        self.assertEqual(
            celery_app.tasks["Queue stale external ratings"].name,
            queue_stale_external_ratings.name,
        )
        self.assertEqual(EXTERNAL_RATING_STALE_LIMIT, 500)
        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULE["queue_stale_external_ratings"],
            {"task": "Queue stale external ratings", "schedule": 60 * 60 * 24},
        )
        self.assertTrue(
            {
                "reload_calendar",
                "send_release_notifications",
                "send_daily_digest",
                "cleanup_user_messages",
            }.issubset(settings.CELERY_BEAT_SCHEDULE),
        )

    @patch("app.tasks.enrich_external_ratings_batch.delay")
    def test_enqueue_helper_deduplicates_and_chunks_in_order(self, delay_mock):
        item_ids = [*range(1, 206), 1, 2]
        delay_mock.side_effect = [
            SimpleNamespace(id="task-1"),
            SimpleNamespace(id="task-2"),
            SimpleNamespace(id="task-3"),
        ]

        result = enqueue_external_rating_batches(item_ids)

        self.assertEqual(
            result,
            {
                "items": 205,
                "batches": 3,
                "task_ids": ["task-1", "task-2", "task-3"],
            },
        )
        self.assertEqual(
            [call.args[0] for call in delay_mock.call_args_list],
            [list(range(1, 101)), list(range(101, 201)), list(range(201, 206))],
        )

    @patch("app.tasks.enrich_external_ratings_batch.delay")
    def test_enqueue_helper_accepts_smaller_valid_batches(self, delay_mock):
        delay_mock.side_effect = [
            SimpleNamespace(id="task-1"),
            SimpleNamespace(id="task-2"),
        ]

        result = enqueue_external_rating_batches([3, 1, 2], batch_size=2)

        self.assertEqual(
            result,
            {
                "items": 3,
                "batches": 2,
                "task_ids": ["task-1", "task-2"],
            },
        )
        self.assertEqual(
            [call.args[0] for call in delay_mock.call_args_list],
            [[3, 1], [2]],
        )

    def test_enqueue_helper_rejects_invalid_batch_sizes(self):
        for batch_size in (0, EXTERNAL_RATING_BATCH_SIZE + 1):
            with self.subTest(batch_size=batch_size), self.assertRaises(ValueError):
                enqueue_external_rating_batches([], batch_size=batch_size)

    def test_stale_selector_includes_failed_and_oldest_stale_only(self):
        now = timezone.now()
        stale_item = self.item
        fresh_item = Item.objects.create(
            media_id="2",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Fresh",
        )
        failed_item = Item.objects.create(
            media_id="3",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Failed",
        )
        Item.objects.create(
            media_id="4",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="No rating row",
        )
        unsupported_item = Item.objects.create(
            media_id="comic",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Unsupported",
        )
        ExternalRating.objects.create(
            item=stale_item,
            rating_source="mal",
            value="8",
            max_value=10,
            status=ExternalRating.Status.AVAILABLE,
            last_attempted_at=now - timedelta(hours=25),
            last_success_at=now - timedelta(hours=25),
        )
        ExternalRating.objects.create(
            item=fresh_item,
            rating_source="mal",
            max_value=10,
            status=ExternalRating.Status.UNAVAILABLE,
            last_attempted_at=now,
        )
        ExternalRating.objects.create(
            item=failed_item,
            rating_source="mal",
            max_value=10,
            status=ExternalRating.Status.FAILED,
            last_attempted_at=now - timedelta(hours=1),
        )
        ExternalRating.objects.create(
            item=unsupported_item,
            rating_source="mal",
            max_value=10,
            status=ExternalRating.Status.FAILED,
            last_attempted_at=now - timedelta(hours=30),
        )

        self.assertEqual(
            _stale_external_rating_item_ids(now=now),
            [stale_item.pk, failed_item.pk],
        )
        self.assertEqual(
            _stale_external_rating_item_ids(now=now, limit=1),
            [stale_item.pk],
        )

    @patch("app.tasks.enqueue_external_rating_batches")
    @patch("app.tasks._stale_external_rating_item_ids")
    @patch("app.tasks.refresh_external_ratings")
    def test_stale_scheduler_only_selects_and_enqueues(
        self,
        refresh_mock,
        select_mock,
        enqueue_mock,
    ):
        select_mock.return_value = [3, 7]
        enqueue_mock.return_value = {"items": 2, "batches": 1}

        result = queue_stale_external_ratings()

        self.assertEqual(result, {"selected": 2, "batches": 1})
        enqueue_mock.assert_called_once_with([3, 7])
        refresh_mock.assert_not_called()


class MusicGenreAlbumsTaskTests(TestCase):
    """Test the weekly music genre album refresh."""

    @patch("app.providers.musicbrainz.refresh_genre_albums")
    def test_weekly_refresh_runs_on_the_featured_worker(self, refresh_mock):
        refresh_mock.return_value = {"albums": 3}

        self.assertEqual(refresh_music_genre_albums(), {"albums": 3})
        refresh_mock.assert_called_once_with()
        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULE["refresh_music_genre_albums"]["task"],
            "Refresh music genre albums",
        )
        self.assertEqual(
            settings.CELERY_TASK_ROUTES["Refresh music genre albums"],
            {"queue": "featured"},
        )
