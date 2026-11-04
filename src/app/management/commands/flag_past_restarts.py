"""Flag past book Restarts (one-time step, scripts/ios/SHIP.md).

Only Restart begins a read at zero, and its start row has said so only since the
preparatory ship. An older one: a journey whose last journey ended Dropped the day this one
started, with a start row and no flag. A pick-up recorded since then writes its status row
right after its start, and that's no Restart; a later pause or pick-up says nothing of how
it began. It's an upper bound, because a stop and a tap on Currently Reading the same day
look the same.

A dry run by default. With --apply it changes rows, and it's idempotent. Counts go to
standard error. With --apply each old value goes to standard output as a JSON line, so the
host wrapper can keep them outside the container (scripts/activity-steps-production.sh).
"""

import json

from django.core.management.base import BaseCommand
from django.db import transaction

from api.services import stats_cache
from app.models import BookSession, Status
from social.models import ProgressChange


def past_restarts():
    """Return (start row, owner id) for each likely past Restart, on every account."""
    found = []
    sessions = BookSession.objects.filter(
        previous_status=Status.DROPPED.value,
        previous_session__status=Status.DROPPED.value,
    ).select_related("previous_session", "related_book")
    for session in sessions.order_by("id"):
        ended = session.previous_session.end_date
        if ended is None or session.start_date is None or ended.date() != session.start_date.date():
            continue
        rows = ProgressChange.objects.filter(
            actor_id=session.related_book.user_id,
            item_id=session.related_book.item_id,
            current_progress__journey_id=session.pk,
        )
        start = rows.filter(current_progress__kind=ProgressChange.START).order_by("id").first()
        if start is None or start.current_progress.get("began_over"):
            continue
        following = rows.filter(pk__gt=start.pk).order_by("id").first()
        if following is None or following.current_progress.get("move") != ProgressChange.PICKED_BACK_UP:
            found.append((start, session.related_book.user_id))
    return found


class Command(BaseCommand):
    help = "Flag past book Restarts' start rows (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Change rows.")

    @transaction.atomic
    def handle(self, *args, **options):
        found = past_restarts()
        per_account = {}
        for start, user_id in found:
            per_account[user_id] = per_account.get(user_id, 0) + 1
            if options["apply"]:
                self.stdout.write(json.dumps({"progress_change": start.pk, "current_progress": start.current_progress}))
                start.current_progress = {**start.current_progress, "began_over": True}
                start.save(update_fields=["current_progress"])
        self.stderr.write(f"past restarts: {len(found)}, per account: {dict(sorted(per_account.items()))}")
        if not options["apply"]:
            self.stderr.write("dry run: nothing changed")
            return
        for user_id in per_account:
            stats_cache.bump_user(user_id)
