from django.db import migrations
from django.db.models import Exists, OuterRef
from django.utils import timezone

CHUNK = 500
CHANGE_REASON = "TV-804: stars filled from the newest rated log"


def fill_tv_ratings_from_logs(apps, _):
    """Give unrated shows and seasons their newest rated log's stars (TV-804, SW-108).

    Season and show log stars never reached the season or show before the TV tracking
    contract. Rows whose history shows stars were cleared on purpose stay empty. No
    network calls; nothing else changes.

    Each fill also writes the history row an ordinary save would, so the repair
    (reconcile_tv), which applies TV-804 again later, sees a clear made after this fill
    as a clear on purpose.
    """
    DiaryEntry = apps.get_model("app", "DiaryEntry")
    now = timezone.now()
    for model_name in ("TV", "Season"):
        model = apps.get_model("app", model_name)
        history = apps.get_model("app", f"Historical{model_name}")
        rated_logs = DiaryEntry.objects.filter(
            user_id=OuterRef("user_id"),
            item_id=OuterRef("item_id"),
            rating__isnull=False,
        )
        ids = list(
            model.objects.filter(score__isnull=True, rating_source__isnull=True)
            .exclude(id__in=history.objects.filter(score__isnull=False).values("id"))
            .filter(Exists(rated_logs))
            .order_by("id")
            .values_list("id", flat=True),
        )
        copied = [
            field
            for field in history._meta.concrete_fields
            if not field.name.startswith("history_")
        ]
        for start in range(0, len(ids), CHUNK):
            rows = list(model.objects.filter(id__in=ids[start : start + CHUNK]))
            newest = {}
            for user_id, item_id, entry_id, rating in (
                DiaryEntry.objects.filter(
                    item_id__in={row.item_id for row in rows},
                    user_id__in={row.user_id for row in rows},
                    rating__isnull=False,
                )
                .order_by("-consumed_at", "-id")
                .values_list("user_id", "item_id", "id", "rating")
            ):
                newest.setdefault((user_id, item_id), (entry_id, rating))
            for row in rows:
                row.rating_source_id, row.score = newest[(row.user_id, row.item_id)]
            model.objects.bulk_update(rows, ["score", "rating_source"])
            history.objects.bulk_create(
                [
                    history(
                        **{field.attname: getattr(row, field.attname) for field in copied},
                        history_date=now,
                        history_type="~",
                        history_change_reason=CHANGE_REASON,
                    )
                    for row in rows
                ],
            )


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0085_tv_tracking_state"),
    ]

    operations = [
        migrations.RunPython(fill_tv_ratings_from_logs, reverse_code=migrations.RunPython.noop),
    ]
