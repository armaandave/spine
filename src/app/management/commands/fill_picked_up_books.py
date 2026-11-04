"""Give books stopped and picked back up before Phase 1 their place back (scripts/ios/SHIP.md).

Count 15, decision 60. A new journey now opens at the stopped journey's place. Before that
it held nothing, so its sheet opened blank. Each one: an open journey holding neither place
field, that carries on from a stopped journey with a place, with no Restart or reread
between. It writes those two fields and the book's own progress, so no number and no row
changes.

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


def _stopped_place(journey):
    """Return the stopped journey whose place this open one carries on from, or None.

    It walks back one journey at a time with the calculation's own test
    (activity_rows._carries_on): a Restart or a reread began at zero, so nothing behind one
    is this read's place. A stopped journey holding nothing passes it on. A file's read is
    its own, so the walk steps past it.
    """
    book = journey.related_book
    began_over = {
        change.current_progress.get("journey_id")
        for change in ProgressChange.objects.filter(
            actor_id=book.user_id,
            item_id=book.item_id,
            current_progress__began_over=True,
        )
    }
    newer = journey
    for prior in book.reading_sessions.filter(pk__lt=journey.pk).order_by("-id"):
        if prior.from_file:
            continue
        if newer.pk in began_over or newer.previous_status == Status.COMPLETED.value or prior.status != Status.DROPPED.value:
            return None
        if prior.pages_read is not None or prior.percentage_read is not None:
            return prior
        newer = prior
    return None


def picked_up_with_no_place():
    """Return (open journey, the stopped journey it carries on from), on every account."""
    found = []
    journeys = BookSession.objects.filter(
        status__in=[Status.IN_PROGRESS.value, Status.PAUSED.value],
        pages_read__isnull=True,
        percentage_read__isnull=True,
    ).select_related("related_book")
    for journey in journeys.order_by("id"):
        stopped = _stopped_place(journey)
        if stopped:
            found.append((journey, stopped))
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
