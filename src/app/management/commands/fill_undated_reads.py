"""Turn undated imported Reads into marks (one-time step, scripts/ios/SHIP.md).

Goodreads and StoryGraph import a Read with no date as a Completed book with no log and
no mark, which no number counts. On every account, each such book gets what the eye writes,
directly, because mark_read returns at once on a Completed book. All Time rises and no
year moves.

A dry run by default. With --apply it changes rows, and it's idempotent. Counts go to
standard error. With --apply each old value goes to standard output as a JSON line, so the
host wrapper can keep them outside the container (scripts/activity-steps-production.sh).
"""

import json

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Exists, OuterRef

from api.services import stats_cache
from app.models import Book, DiaryEntry, Status


def undated_reads():
    """Return Read books with no date, log, current journey or mark, on every account."""
    logs = DiaryEntry.objects.filter(user_id=OuterRef("user_id"), item_id=OuterRef("item_id"))
    return Book.objects.filter(
        ~Exists(logs),
        status=Status.COMPLETED.value,
        completed_manually=False,
        read_override_active=False,
        current_session__isnull=True,
        end_date__isnull=True,
    ).order_by("id")


class Command(BaseCommand):
    help = "Give undated imported Reads the undated mark (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Change rows.")

    @transaction.atomic
    def handle(self, *args, **options):
        books = list(undated_reads().select_related("item"))
        pages = {}
        for book in books:
            pages[book.user_id] = pages.get(book.user_id, 0) + (book.item.total_pages or 0)
            if options["apply"]:
                old = {"book": book.pk, "completed_manually": False}
                self.stdout.write(json.dumps(old))
        self.stderr.write(f"undated reads: {len(books)}, pages per account: {dict(sorted(pages.items()))}")
        if not options["apply"]:
            self.stderr.write("dry run: nothing changed")
            return
        Book.objects.filter(pk__in=[book.pk for book in books]).update(
            completed_manually=True,
            undated_read_previous_tracked=False,
            undated_read_previous_status=None,
            undated_read_previous_session=None,
            undated_read_previous_override=False,
        )
        for user_id in pages:
            stats_cache.bump_user(user_id)
