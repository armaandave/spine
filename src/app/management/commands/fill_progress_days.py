"""Give progress changes a rollback left empty their day (docs/activity-contract.md, AC-2).

Old code writes no ``saved_on``, so a rollback leaves new rows empty until this runs after
the fix (scripts/ios/SHIP.md, Workflow B). It writes only empty rows, on each owner's
calendar, as migration social.0005 did, so running it again changes nothing.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from social.models import ProgressChange

BATCH = 1000


def _zone(name):
    try:
        return ZoneInfo(name) if name else ZoneInfo(settings.TIME_ZONE)
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo(settings.TIME_ZONE)


def fill():
    """Fill every empty ``saved_on``; return how many rows it wrote."""
    zones = {
        user_id: _zone(name)
        for user_id, name in get_user_model().objects.values_list("pk", "calendar_timezone")
    }
    filled = 0
    rows = []
    empty = ProgressChange.objects.filter(saved_on__isnull=True).only("id", "actor_id", "created_at")
    for row in empty.iterator(chunk_size=BATCH):
        row.saved_on = row.created_at.astimezone(zones[row.actor_id]).date()
        rows.append(row)
        if len(rows) >= BATCH:
            ProgressChange.objects.bulk_update(rows, ["saved_on"])
            filled += len(rows)
            rows = []
    if rows:
        ProgressChange.objects.bulk_update(rows, ["saved_on"])
        filled += len(rows)
    return filled


class Command(BaseCommand):
    help = "Fill saved_on on progress changes that have none, on each owner's calendar"

    def handle(self, *args, **options):
        self.stdout.write(f"Filled {fill()} rows.")
