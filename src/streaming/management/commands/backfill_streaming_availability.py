"""Queue streaming availability refreshes for library, list, and diary titles."""

from math import ceil

from django.core.management.base import BaseCommand, CommandError

from streaming.library import library_titles, titles_needing_refresh
from streaming.services import active_regions
from streaming.tasks import REFRESH_BATCH_SIZE, enqueue_refresh_batches


class Command(BaseCommand):
    """Report and queue bounded refresh batches without TMDB calls here."""

    help = "Report and queue streaming availability refreshes for library, list, and diary titles"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, help="Maximum titles to queue.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print counts without queueing tasks.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Queue fresh titles too and refresh them regardless of age.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit is not None and limit < 1:
            raise CommandError("limit must be a positive integer")

        regions = active_regions()
        titles = library_titles()
        missing, stale = titles_needing_refresh(titles, regions)
        selected = missing + stale
        if options["force"]:
            pending = set(selected)
            selected += sorted(title for title in titles if title not in pending)
        if limit is not None:
            selected = selected[:limit]

        report = (
            ("Active regions", ", ".join(sorted(regions)) or "none"),
            ("Library, list, and diary titles", len(titles)),
            ("Missing in a region", len(missing)),
            ("Stale", len(stale)),
            ("Fresh", len(titles) - len(missing) - len(stale)),
            ("Selected titles", len(selected)),
            ("Batches to queue", ceil(len(selected) / REFRESH_BATCH_SIZE)),
        )
        for label, value in report:
            self.stdout.write(f"{label}: {value}")

        if options["dry_run"]:
            self.stdout.write("Dry run: no tasks queued")
            return
        if not selected:
            self.stdout.write("No streaming availability work to queue")
            return
        batches = enqueue_refresh_batches(selected, force=options["force"])
        self.stdout.write(f"Queued {len(selected)} titles in {batches} batches")
