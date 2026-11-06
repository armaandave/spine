"""Mark Twerp read on Armaan's account (one-time step, scripts/ios/SHIP.md).

Twerp is a Read book on account 1 dated 2026-06-24 with no log and no mark, so no number
counts it. Armaan said yes on 2026-11-04 to giving it what the eye writes: All Time rises by
its length, 290 pages, and no year moves. It goes through book_tracking.mark_imported_read,
as the undated Reads step does, because mark_read returns at once on a Completed book. That
clears its end_date and any current journey; the old values keep them.

It touches exactly one book: user 1's Book whose Item is titled Twerp, Completed, ending
2026-06-24 on the owner's calendar, with no log of that title, no mark and no read
override. Finding none or more than one, it changes nothing and says why.

A dry run by default. With --apply it changes the row, and a second run finds nothing.
Counts go to standard error. With --apply the row's old values go to standard output as a
JSON line, so the host wrapper can keep them outside the container
(scripts/activity-steps-production.sh).
"""

import json
from datetime import date

from django.core.management.base import BaseCommand
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Exists, OuterRef

from api.services import stats_cache
from api.services.activity_rows import log_day
from app import book_tracking, user_calendar
from app.management.commands.fill_undated_reads import OLD_VALUES
from app.models import Book, BookSession, DiaryEntry, Status
from social.models import ProgressChange

OWNER_ID = 1
TITLE = "Twerp"
READ_ON = date(2026, 6, 24)


def candidates(owner_id=OWNER_ID):
    """Return the books this step may mark: Twerp, read, with no log, mark or override."""
    logs = DiaryEntry.objects.filter(user_id=OuterRef("user_id"), item_id=OuterRef("item_id"))
    books = Book.objects.filter(
        ~Exists(logs),
        user_id=owner_id,
        item__title=TITLE,
        status=Status.COMPLETED.value,
        completed_manually=False,
        read_override_active=False,
        end_date__isnull=False,
    ).select_related("item", "user")
    return [book for book in books.order_by("id") if log_day(book.end_date, user_calendar.calendar_tz(book.user)) == READ_ON]


class Command(BaseCommand):
    help = "Mark Twerp read on account 1, with no date (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Change the row.")
        # Only tests name another account: Armaan's yes covers his own book.
        parser.add_argument("--user", type=int, default=OWNER_ID, help="The account. Armaan's is 1.")

    @transaction.atomic
    def handle(self, *args, **options):
        found = candidates(options["user"])
        pages = sum(book.item.total_pages or 0 for book in found)
        self.stderr.write(f"twerp: {len(found)} found (expected 1), pages: {pages} (expected 290)")
        for book in found:
            journeys = BookSession.objects.filter(related_book=book).count()
            saves = ProgressChange.objects.filter(actor_id=book.user_id, item_id=book.item_id).count()
            self.stderr.write(f"twerp: book {book.pk}, journeys: {journeys}, saves: {saves}")
        if len(found) != 1:
            why = "nothing matches" if not found else "more than one matches"
            self.stderr.write(f"twerp: {why}, so nothing changed")
            return
        if not options["apply"]:
            self.stderr.write("dry run: nothing changed")
            return
        book = Book.objects.select_for_update(of=("self",)).get(pk=found[0].pk)
        old = {name: getattr(book, name) for name in (*OLD_VALUES, "current_session_id")}
        self.stdout.write(json.dumps({"book": book.pk, **old}, cls=DjangoJSONEncoder))
        book_tracking.mark_imported_read(book)
        stats_cache.bump_user(book.user_id)
        self.stderr.write(f"twerp: book {book.pk} marked read with no date")
