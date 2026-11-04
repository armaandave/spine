from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import migrations, models

BATCH = 1000


def _zone(name):
    """Return the owner's calendar; a blank or bad zone falls back to the server's."""
    try:
        return ZoneInfo(name) if name else ZoneInfo(settings.TIME_ZONE)
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo(settings.TIME_ZONE)


def fill(apps, schema_editor):
    """Give each row from before this column its save moment's day on the owner's calendar."""
    change_model = apps.get_model("social", "ProgressChange")
    user_model = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    zones = {
        user_id: _zone(name)
        for user_id, name in user_model.objects.values_list("pk", "calendar_timezone")
    }
    rows = []
    for row in change_model.objects.filter(saved_on__isnull=True).only("id", "actor_id", "created_at").iterator(chunk_size=BATCH):
        row.saved_on = row.created_at.astimezone(zones[row.actor_id]).date()
        rows.append(row)
        if len(rows) >= BATCH:
            change_model.objects.bulk_update(rows, ["saved_on"])
            rows = []
    if rows:
        change_model.objects.bulk_update(rows, ["saved_on"])


class Migration(migrations.Migration):

    dependencies = [
        ("social", "0004_post_comments"),
        ("users", "0060_user_calendar_timezone"),
    ]

    operations = [
        migrations.AddField(
            model_name="progresschange",
            name="saved_on",
            field=models.DateField(blank=True, null=True),
        ),
        # The reverse is a no-op so a rollback's backward test can run; it doesn't undo a fill.
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
