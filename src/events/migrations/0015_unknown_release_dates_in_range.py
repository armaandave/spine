from datetime import UTC, datetime

from django.db import migrations

# Content with no known release date was stamped datetime.min (0001-01-01 00:00 UTC),
# the very bottom of the datetime range. Converting it to a timezone behind UTC
# (production runs on America/New_York) raises OverflowError, which fails the admin
# page and anything else that shows the value in local time. Move those rows to
# midday on the same day, which new events use (SentinelDatetime.min_datetime()).
# Ported from Yamtrack's events 0016 (issue #884).

UNKNOWN_RELEASE_DATETIME = datetime(1, 1, 1, 11, 59, 59, 999999, tzinfo=UTC)


def move_unknown_release_dates_into_range(apps, schema_editor):
    event_model = apps.get_model("events", "Event")
    event_model.objects.filter(datetime__lt=UNKNOWN_RELEASE_DATETIME).update(
        datetime=UNKNOWN_RELEASE_DATETIME,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0014_delete_empty_content_number_comic_events"),
    ]

    operations = [
        migrations.RunPython(
            move_unknown_release_dates_into_range,
            migrations.RunPython.noop,
        ),
    ]
