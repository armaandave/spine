import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from app.models import ExternalRating, Item, MediaTypes, Sources


class BackfillExternalRatingsCommandTests(TestCase):
    def setUp(self):
        self.now = timezone.now()
        self.movie = Item.objects.create(
            media_id="1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Movie",
        )
        self.anime = Item.objects.create(
            media_id="2",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Anime",
        )
        self.game = Item.objects.create(
            media_id="3",
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            title="Game",
        )
        Item.objects.create(
            media_id=Item.generate_manual_id(),
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Manual",
        )
        self._rating(self.movie, "imdb", "available", value="8.5", maximum=10)
        self._rating(self.movie, "letterboxd", "unavailable", maximum=5)
        self._rating(self.movie, "tomatoes", "failed", maximum=100)
        self._rating(self.anime, "mal", "available", value="8.7", maximum=10)
        self._rating(
            self.game,
            "igdb",
            "available",
            value="90",
            maximum=100,
            attempted_at=self.now - timedelta(hours=25),
        )

    def _rating(
        self,
        item,
        source,
        status,
        *,
        value=None,
        maximum,
        attempted_at=None,
    ):
        attempted_at = attempted_at or self.now
        return ExternalRating.objects.create(
            item=item,
            rating_source=source,
            value=value,
            max_value=maximum,
            status=status,
            last_attempted_at=attempted_at,
            last_success_at=attempted_at if value is not None else None,
        )

    @staticmethod
    def _batch_result(requested, *, item_status="refreshed", outcome="available"):
        result = {
            "requested": requested,
            "refreshed": 0,
            "fresh": 0,
            "skipped": 0,
            "deduplicated": 0,
            "partial": 0,
            "failed": 0,
            "retrying": 0,
            "outcomes": {
                "attempted": requested,
                "available": 0,
                "unavailable": 0,
                "failed": 0,
                "skipped_fresh": 0,
                "preserved_stale": 0,
            },
        }
        result[item_status] = requested
        if outcome:
            result["outcomes"][outcome] = requested
        return result

    @patch("app.external_ratings.refresh_external_ratings")
    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.mdblist.get_media_ratings")
    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_dry_run_reports_pair_and_item_counts_without_work(
        self,
        enqueue_mock,
        mdblist_mock,
        metadata_mock,
        refresh_mock,
    ):
        stdout = StringIO()

        with self.assertNumQueries(2):
            call_command(
                "backfill_external_ratings",
                dry_run=True,
                batch_size=2,
                stdout=stdout,
            )

        self.assertIn("Eligible item/source pairs: 9", stdout.getvalue())
        self.assertIn("Already fresh terminal pairs: 3", stdout.getvalue())
        self.assertIn("Pending/missing pairs: 6", stdout.getvalue())
        self.assertIn("Unavailable rows: 1", stdout.getvalue())
        self.assertIn("Failed rows: 1", stdout.getvalue())
        self.assertIn("Eligible Items: 3", stdout.getvalue())
        self.assertIn("Pending Items: 3", stdout.getvalue())
        self.assertIn("Selected Items: 3", stdout.getvalue())
        self.assertIn("Batches to queue: 2", stdout.getvalue())
        self.assertIn("Dry run: no tasks queued", stdout.getvalue())
        enqueue_mock.assert_not_called()
        mdblist_mock.assert_not_called()
        metadata_mock.assert_not_called()
        refresh_mock.assert_not_called()

    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_source_media_and_item_scopes_are_combined(self, enqueue_mock):
        stdout = StringIO()

        call_command(
            "backfill_external_ratings",
            rating_sources=["tmdb"],
            media_types=[MediaTypes.MOVIE.value],
            item_sources=[Sources.TMDB.value],
            stdout=stdout,
        )

        self.assertIn("Eligible item/source pairs: 1", stdout.getvalue())
        self.assertIn("Pending/missing pairs: 1", stdout.getvalue())
        enqueue_mock.assert_called_once_with(
            [self.movie.pk],
            rating_sources=["tmdb"],
            force=False,
            batch_size=100,
        )

    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_fresh_unavailable_skips_and_failed_retries(self, enqueue_mock):
        call_command(
            "backfill_external_ratings",
            rating_sources=["letterboxd"],
            stdout=StringIO(),
        )
        enqueue_mock.assert_not_called()

        call_command(
            "backfill_external_ratings",
            rating_sources=["tomatoes"],
            stdout=StringIO(),
        )
        enqueue_mock.assert_called_once_with(
            [self.movie.pk],
            rating_sources=["tomatoes"],
            force=False,
            batch_size=100,
        )

    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_rerun_resumes_after_completed_work(self, enqueue_mock):
        call_command(
            "backfill_external_ratings",
            rating_sources=["tomatoes"],
            stdout=StringIO(),
        )
        rating = self.movie.external_ratings.get(rating_source="tomatoes")
        rating.status = ExternalRating.Status.AVAILABLE
        rating.value = 80
        rating.last_attempted_at = timezone.now()
        rating.last_success_at = rating.last_attempted_at
        rating.save()

        call_command(
            "backfill_external_ratings",
            rating_sources=["tomatoes"],
            stdout=StringIO(),
        )

        self.assertEqual(enqueue_mock.call_count, 1)

    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_force_limit_and_stable_item_order(self, enqueue_mock):
        stdout = StringIO()

        call_command(
            "backfill_external_ratings",
            force=True,
            limit=2,
            batch_size=1,
            stdout=stdout,
        )

        self.assertIn("Selected Items: 2", stdout.getvalue())
        self.assertIn("Batches to queue: 2", stdout.getvalue())
        enqueue_mock.assert_called_once_with(
            [self.movie.pk, self.anime.pk],
            rating_sources=None,
            force=True,
            batch_size=1,
        )

    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_limit_applies_to_pending_items_and_advances_on_rerun(self, enqueue_mock):
        options = {
            "rating_sources": ["tmdb", "igdb"],
            "limit": 1,
            "stdout": StringIO(),
        }

        call_command("backfill_external_ratings", **options)
        self.assertEqual(enqueue_mock.call_args.args[0], [self.movie.pk])

        self._rating(
            self.movie,
            "tmdb",
            "available",
            value="7.5",
            maximum=10,
        )
        enqueue_mock.reset_mock()
        options["stdout"] = StringIO()
        call_command("backfill_external_ratings", **options)

        self.assertEqual(enqueue_mock.call_args.args[0], [self.game.pk])

    def test_invalid_options_are_rejected(self):
        cases = (
            ({"rating_sources": ["goodreads"]}, "Unknown rating source"),
            ({"media_types": ["podcast"]}, "Unknown media type"),
            ({"item_sources": ["goodreads"]}, "Unknown item source"),
            ({"batch_size": 0}, "batch-size must be between 1 and 100"),
            ({"batch_size": 101}, "batch-size must be between 1 and 100"),
            ({"limit": 0}, "limit must be a positive integer"),
            ({"max_runtime_seconds": 0}, "max-runtime-seconds must be a positive integer"),
            ({"wait_timeout_seconds": 0}, "wait-timeout-seconds must be a positive integer"),
            ({"max_items": 0}, "max-items must be a positive integer"),
            (
                {"drain": True, "coverage_only": True, "limit": 1},
                "drain requires explicit",
            ),
            (
                {
                    "drain": True,
                    "rating_sources": ["tmdb"],
                    "media_types": ["movie"],
                    "item_sources": ["tmdb"],
                    "limit": 1,
                },
                "drain requires coverage-only",
            ),
            (
                {
                    "drain": True,
                    "coverage_only": True,
                    "rating_sources": ["tmdb"],
                    "media_types": ["movie"],
                    "item_sources": ["tmdb"],
                },
                "drain requires a positive limit",
            ),
            (
                {
                    "drain": True,
                    "coverage_only": True,
                    "rating_sources": ["tmdb"],
                    "media_types": ["movie"],
                    "item_sources": ["tmdb"],
                    "limit": 1,
                    "force": True,
                },
                "drain cannot be combined with force",
            ),
            (
                {
                    "drain": True,
                    "coverage_only": True,
                    "rating_sources": ["tmdb"],
                    "media_types": ["movie"],
                    "item_sources": ["tmdb"],
                    "limit": 1,
                    "dry_run": True,
                },
                "drain cannot be combined with dry-run",
            ),
        )
        for options, message in cases:
            with self.subTest(options=options), self.assertRaisesMessage(CommandError, message):
                call_command("backfill_external_ratings", stdout=StringIO(), **options)

    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_zero_eligible_records_succeeds_without_queueing(self, enqueue_mock):
        stdout = StringIO()

        call_command(
            "backfill_external_ratings",
            item_sources=[Sources.MANUAL.value],
            stdout=stdout,
        )

        self.assertIn("Eligible item/source pairs: 0", stdout.getvalue())
        self.assertIn("Selected Items: 0", stdout.getvalue())
        self.assertIn("No external-rating work to queue", stdout.getvalue())
        enqueue_mock.assert_not_called()

    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_coverage_only_accepts_stale_terminal_rows(self, enqueue_mock):
        stdout = StringIO()

        call_command(
            "backfill_external_ratings",
            rating_sources=["igdb"],
            media_types=["game"],
            item_sources=["igdb"],
            coverage_only=True,
            dry_run=True,
            stdout=stdout,
        )

        self.assertIn("Pending/missing pairs: 0", stdout.getvalue())
        self.assertIn("Selected Items: 0", stdout.getvalue())
        enqueue_mock.assert_not_called()

    @patch("app.management.commands.backfill_external_ratings.Command._wait_for_batch")
    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_drain_runs_exact_sequential_batches_and_completes(
        self,
        enqueue_mock,
        wait_mock,
    ):
        pending = [
            Item.objects.create(
                media_id=str(media_id),
                source=Sources.MAL.value,
                media_type=MediaTypes.ANIME.value,
                title=f"Anime {media_id}",
            )
            for media_id in range(10, 13)
        ]
        queued_batches = []

        def enqueue(item_ids, **_kwargs):
            queued_batches.append(list(item_ids))
            return {
                "items": len(item_ids),
                "batches": 1,
                "task_ids": [f"task-{len(queued_batches)}"],
            }

        def wait(_task_id, *, timeout):  # noqa: ARG001
            batch_index = len(wait_mock.mock_calls) - 1
            item_ids = queued_batches[batch_index]
            outcome = (
                ExternalRating.Status.UNAVAILABLE
                if batch_index == 0
                else ExternalRating.Status.AVAILABLE
            )
            for item_id in item_ids:
                self._rating(
                    Item.objects.get(pk=item_id),
                    "mal",
                    outcome,
                    value="8.5" if outcome == ExternalRating.Status.AVAILABLE else None,
                    maximum=10,
                )
            return self._batch_result(len(item_ids), outcome=outcome)

        enqueue_mock.side_effect = enqueue
        wait_mock.side_effect = wait
        stdout = StringIO()

        call_command(
            "backfill_external_ratings",
            rating_sources=["mal"],
            media_types=["anime"],
            item_sources=["mal"],
            coverage_only=True,
            drain=True,
            limit=3,
            batch_size=2,
            json_output=True,
            stdout=stdout,
        )

        self.assertEqual(
            queued_batches,
            [[pending[0].pk, pending[1].pk], [pending[2].pk]],
        )
        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(events[-1]["event"], "complete")
        self.assertEqual(events[-1]["ending_pending_pairs"], 0)
        self.assertEqual(events[-1]["ending_covered_pairs"], 4)
        self.assertEqual(events[-1]["batches_completed"], 2)
        self.assertEqual(events[-1]["unavailable"], 2)

    @patch("app.management.commands.backfill_external_ratings.Command._wait_for_batch")
    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_drain_stops_on_failed_or_retrying_batch(self, enqueue_mock, wait_mock):
        enqueue_mock.return_value = {
            "items": 1,
            "batches": 1,
            "task_ids": ["task-1"],
        }
        cases = (
            self._batch_result(1, item_status="failed", outcome="failed"),
            self._batch_result(1, item_status="partial", outcome="failed"),
            self._batch_result(1, item_status="retrying", outcome="failed"),
            self._batch_result(2),
            {},
        )
        for result in cases:
            with self.subTest(result=result):
                enqueue_mock.reset_mock()
                wait_mock.return_value = result
                stdout = StringIO()
                with self.assertRaises(CommandError):
                    call_command(
                        "backfill_external_ratings",
                        rating_sources=["tmdb"],
                        media_types=["movie"],
                        item_sources=["tmdb"],
                        coverage_only=True,
                        drain=True,
                        limit=1,
                        batch_size=1,
                        json_output=True,
                        stdout=stdout,
                    )
                events = [
                    json.loads(line)
                    for line in stdout.getvalue().splitlines()
                ]
                self.assertEqual(events[-1]["event"], "failed")
                self.assertEqual(enqueue_mock.call_count, 1)

    @patch("app.management.commands.backfill_external_ratings.Command._wait_for_batch")
    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_drain_stops_when_batch_makes_no_progress(self, enqueue_mock, wait_mock):
        enqueue_mock.return_value = {
            "items": 1,
            "batches": 1,
            "task_ids": ["task-1"],
        }
        wait_mock.return_value = self._batch_result(
            1,
            item_status="deduplicated",
            outcome=None,
        )

        with self.assertRaisesMessage(CommandError, "made no coverage progress"):
            call_command(
                "backfill_external_ratings",
                rating_sources=["tmdb"],
                media_types=["movie"],
                item_sources=["tmdb"],
                coverage_only=True,
                drain=True,
                limit=1,
                batch_size=1,
                stdout=StringIO(),
            )

    @patch("app.management.commands.backfill_external_ratings.Command._wait_for_batch")
    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_drain_reports_wait_timeout_as_failure(self, enqueue_mock, wait_mock):
        enqueue_mock.return_value = {
            "items": 1,
            "batches": 1,
            "task_ids": ["task-1"],
        }
        wait_mock.side_effect = CommandError(
            "Timed out waiting for external-rating batch task-1",
        )
        stdout = StringIO()

        with self.assertRaisesMessage(CommandError, "Timed out waiting"):
            call_command(
                "backfill_external_ratings",
                rating_sources=["tmdb"],
                media_types=["movie"],
                item_sources=["tmdb"],
                coverage_only=True,
                drain=True,
                limit=1,
                batch_size=1,
                json_output=True,
                stdout=stdout,
            )

        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(events[-1]["event"], "failed")
        self.assertEqual(events[-1]["batches_completed"], 0)

    @patch("app.management.commands.backfill_external_ratings.time.monotonic")
    @patch("app.management.commands.backfill_external_ratings.Command._wait_for_batch")
    @patch("app.management.commands.backfill_external_ratings.enqueue_external_rating_batches")
    def test_drain_pauses_cleanly_after_runtime_limit(
        self,
        enqueue_mock,
        wait_mock,
        monotonic_mock,
    ):
        second = Item.objects.create(
            media_id="runtime-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Runtime 2",
        )
        queued_batches = []
        enqueue_mock.side_effect = lambda item_ids, **_kwargs: (
            queued_batches.append(list(item_ids))
            or {"items": len(item_ids), "batches": 1, "task_ids": ["task-1"]}
        )

        def wait(_task_id, *, timeout):  # noqa: ARG001
            item_id = queued_batches[-1][0]
            self._rating(
                Item.objects.get(pk=item_id),
                "tmdb",
                ExternalRating.Status.AVAILABLE,
                value="8",
                maximum=10,
            )
            return self._batch_result(1)

        wait_mock.side_effect = wait
        monotonic_mock.side_effect = [0, 0, 2, 2, 2]
        stdout = StringIO()

        call_command(
            "backfill_external_ratings",
            rating_sources=["tmdb"],
            media_types=["movie"],
            item_sources=["tmdb"],
            coverage_only=True,
            drain=True,
            limit=1,
            batch_size=1,
            max_runtime_seconds=1,
            json_output=True,
            stdout=stdout,
        )

        events = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(events[-1]["event"], "paused")
        self.assertEqual(events[-1]["reason"], "max_runtime")
        self.assertGreater(events[-1]["ending_pending_pairs"], 0)
        self.assertTrue(Item.objects.filter(pk=second.pk).exists())
