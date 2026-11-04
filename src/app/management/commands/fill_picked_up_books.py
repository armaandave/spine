"""Give books stopped and picked back up before Phase 1 their place back (scripts/ios/SHIP.md).

Count 15, decision 60. A new journey now opens at the stopped journey's place. Before that
it held nothing, so its sheet opened blank. Each one: an open journey holding neither place
field, whose last journey ended Dropped with a place, and that isn't a Restart. It writes
those two fields and the book's own progress, so no number and no row changes.

A dry run by default. With --apply it changes rows, and it's idempotent. Counts go to
standard error. With --apply each old value goes to standard output as a JSON line, so the
host wrapper can keep them outside the container (scripts/activity-steps-production.sh).
"""

import json

from django.core.management.base import BaseCommand
from django.db import transaction

from api.services import stats_cache
from app.models import Book, BookSession, Status
from social.models import ProgressChange


def picked_up_with_no_place():
    """Return (open journey, the stopped journey it carries on from), on every account."""
    found = []
    journeys = BookSession.objects.filter(
        status__in=[Status.IN_PROGRESS.value, Status.PAUSED.value],
        pages_read__isnull=True,
        percentage_read__isnull=True,
    ).select_related("related_book")
    for journey in journeys.order_by("id"):
        last = (
            BookSession.objects.filter(related_book=journey.related_book, pk__lt=journey.pk)
            .exclude(pages_read__isnull=True, percentage_read__isnull=True)
            .order_by("-id")
            .first()
        )
        if last is None or last.status != Status.DROPPED.value or journey.previous_status == Status.COMPLETED.value:
            continue
        restarted = ProgressChange.objects.filter(
            current_progress__journey_id=journey.pk,
            current_progress__began_over=True,
            actor_id=journey.related_book.user_id,
        ).exists()
        if not restarted:
            found.append((journey, last))
    return found


class Command(BaseCommand):
    help = "Give books already picked up the place they stopped at (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Change rows.")

    @transaction.atomic
    def handle(self, *args, **options):
        found = picked_up_with_no_place()
        per_account = {}
        for journey, last in found:
            owner = journey.related_book.user_id
            per_account[owner] = per_account.get(owner, 0) + 1
            if options["apply"]:
                self.stdout.write(json.dumps({"journey": journey.pk, "pages_read": None, "percentage_read": None}))
                BookSession.objects.filter(pk=journey.pk).update(
                    pages_read=last.pages_read,
                    percentage_read=last.percentage_read,
                )
                Book.objects.filter(pk=journey.related_book_id, current_session=journey).update(
                    progress=last.pages_read or 0,
                )
        self.stderr.write(f"count 15, books picked up with no place: {len(found)}, per account: {dict(sorted(per_account.items()))}")
        if not options["apply"]:
            self.stderr.write("dry run: nothing changed")
            return
        for user_id in per_account:
            stats_cache.bump_user(user_id)
