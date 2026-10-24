import zoneinfo
from collections import Counter
from datetime import UTC, datetime, time, timedelta

from django.conf import settings
from django.db import migrations
from django.db.models import Max


def restore_shifted_log_dates(apps, _):
    """Undo edits that moved a log one day earlier.

    West of UTC, saving a log read its stored date back in local time, so every
    save kept the picked date minus one day. Picking an earlier date moved it two
    days, so an edit exactly one day earlier is always that shift.
    """
    if zoneinfo.ZoneInfo(settings.TIME_ZONE).utcoffset(datetime.now(UTC)) >= timedelta(0):
        return
    DiaryEntry = apps.get_model("app", "DiaryEntry")
    HistoricalDiaryEntry = apps.get_model("app", "HistoricalDiaryEntry")
    shifts = Counter()
    previous = {}
    history = HistoricalDiaryEntry.objects.order_by("id", "history_date", "history_id")
    for entry_id, kind, consumed_at in history.values_list("id", "history_type", "consumed_at").iterator():
        before = previous.get(entry_id)
        previous[entry_id] = consumed_at
        if kind == "~" and _is_date(before) and _is_date(consumed_at) and consumed_at - before == timedelta(days=-1):
            shifts[entry_id] += 1

    for entry in DiaryEntry.objects.filter(id__in=shifts).select_related("item"):
        logs = DiaryEntry.objects.filter(user_id=entry.user_id, item_id=entry.item_id)
        old_latest = logs.aggregate(value=Max("consumed_at"))["value"]
        DiaryEntry.objects.filter(id=entry.id).update(consumed_at=entry.consumed_at + timedelta(days=shifts[entry.id]))
        new_latest = logs.aggregate(value=Max("consumed_at"))["value"]
        tracking = apps.get_model("app", entry.item.media_type)
        if any(field.name == "end_date" for field in tracking._meta.fields):
            tracking.objects.filter(user_id=entry.user_id, item_id=entry.item_id, end_date=old_latest).update(
                end_date=new_latest,
            )


def _is_date(value):
    return value is not None and value.utcoffset() == timedelta(0) and value.time() == time.min


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0082_heart_liked_letterboxd_logs"),
    ]

    operations = [
        migrations.RunPython(restore_shifted_log_dates, reverse_code=migrations.RunPython.noop),
    ]
