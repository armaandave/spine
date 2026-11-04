"""Turn undated imported Reads into marks (one-time step, scripts/ios/SHIP.md).

Goodreads and StoryGraph import a Read with no date as a Completed book with no log and
no mark, which no number counts. On every account, each such book gets what the eye writes,
directly, because mark_read returns at once on a Completed book. All Time rises and no
year moves.

It also prints the rest of the plan's count 10 and writes none of it: Reads with a date and
no log, which count nowhere, and marks holding a date.

A dry run by default. With --apply it changes rows, and it's idempotent. Counts go to
standard error. With --apply each old value goes to standard output as a JSON line, so the
host wrapper can keep them outside the container (scripts/activity-steps-production.sh).
"""

import json
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Exists, OuterRef

from api.services import stats_cache
from app.models import Book, DiaryEntry, Game, Status


def undated_reads():
    """Return Read books with no date, log, current journey or mark, on every account."""
    return _reads_with_no_log().filter(end_date__isnull=True)


def dated_reads():
    """Return the same with a date: no step writes these, and Phase 1 fixes only imports."""
    return _reads_with_no_log().filter(end_date__isnull=False)


def _reads_with_no_log():
    logs = DiaryEntry.objects.filter(user_id=OuterRef("user_id"), item_id=OuterRef("item_id"))
    return Book.objects.filter(
        ~Exists(logs),
        status=Status.COMPLETED.value,
        completed_manually=False,
        read_override_active=False,
        current_session__isnull=True,
    ).order_by("id")


def _sources_and_pages(books):
    """Say how many came from each source, and how many pages per account."""
    sources = Counter(book.item.source for book in books)
    pages = Counter()
    for book in books:
        pages[book.user_id] += book.item.total_pages or 0
    return f"by source: {dict(sorted(sources.items()))}, pages per account: {dict(sorted(pages.items()))}"


def _marks_with_end_date(model):
    """Say how many marks hold a date, per account."""
    owners = model.objects.filter(completed_manually=True, end_date__isnull=False).values_list("user_id", flat=True)
    return dict(sorted(Counter(owners).items()))


class Command(BaseCommand):
    help = "Give undated imported Reads the undated mark (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Change rows.")

    @transaction.atomic
    def handle(self, *args, **options):
        books = list(undated_reads().select_related("item"))
        if options["apply"]:
            for book in books:
                self.stdout.write(json.dumps({"book": book.pk, "completed_manually": False}))
        self.stderr.write(f"undated reads: {len(books)}, {_sources_and_pages(books)}")
        # The rest of count 10, for Armaan to see. No step writes any of it.
        dated = list(dated_reads().select_related("item"))
        self.stderr.write(f"dated reads with no log, not written: {len(dated)}, {_sources_and_pages(dated)}")
        self.stderr.write(f"marked books with end_date, not written: {_marks_with_end_date(Book)}")
        self.stderr.write(f"marked games with end_date, not written: {_marks_with_end_date(Game)}")
        if not options["apply"]:
            self.stderr.write("dry run: nothing changed")
            return
        # The importer made the row, so undoing it keeps the row, at To Read.
        Book.objects.filter(pk__in=[book.pk for book in books]).update(
            completed_manually=True,
            undated_read_previous_tracked=True,
            undated_read_previous_status=None,
            undated_read_previous_session=None,
            undated_read_previous_override=False,
        )
        for user_id in {book.user_id for book in books}:
            stats_cache.bump_user(user_id)
