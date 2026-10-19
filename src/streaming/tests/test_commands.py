from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from app.models import Item, MediaTypes, Movie, Sources, Status
from streaming.tests.helpers import (
    activate_region,
    make_snapshot,
    reset_streaming_state,
)


class BackfillStreamingAvailabilityCommandTests(TestCase):
    """Reporting and queueing streaming refresh work."""

    def setUp(self):
        reset_streaming_state(self)
        user = activate_region("GB")
        items = [
            Item.objects.create(
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                media_id=media_id,
                title=f"Movie {media_id}",
                image="https://example.com/poster.jpg",
            )
            for media_id in ("1", "2", "3")
        ]
        Movie.objects.bulk_create(
            [Movie(user=user, item=item, status=Status.COMPLETED.value) for item in items],
        )
        for region in ("US", "GB"):
            make_snapshot("movie", "1", region)
        make_snapshot("movie", "2", "US")

    def _run(self, *args):
        out = StringIO()
        call_command("backfill_streaming_availability", *args, stdout=out)
        return out.getvalue()

    @patch("streaming.tasks.refresh_streaming_titles.delay")
    def test_dry_run_reports_without_queueing(self, delay_mock):
        output = self._run("--dry-run")

        self.assertIn("Active regions: GB, US", output)
        self.assertIn("Library, list, and diary titles: 3", output)
        self.assertIn("Missing in a region: 2", output)
        self.assertIn("Fresh: 1", output)
        self.assertIn("Dry run: no tasks queued", output)
        delay_mock.assert_not_called()

    @patch("streaming.tasks.refresh_streaming_titles.delay")
    def test_queues_pending_titles_within_the_limit(self, delay_mock):
        output = self._run("--limit", "1")

        self.assertIn("Queued 1 titles in 1 batches", output)
        delay_mock.assert_called_once_with([["movie", "2"]], force=False)

    @patch("streaming.tasks.refresh_streaming_titles.delay")
    def test_force_includes_fresh_titles(self, delay_mock):
        self._run("--force")

        delay_mock.assert_called_once_with(
            [["movie", "2"], ["movie", "3"], ["movie", "1"]],
            force=True,
        )

    def test_rejects_non_positive_limit(self):
        with self.assertRaisesMessage(CommandError, "limit must be a positive integer"):
            self._run("--limit", "0")
