from django.db import migrations
from django.db.models import OuterRef, Subquery


def fill_empty_ratings_from_rated_logs(apps, _):
    """Give unrated titles their newest rated log's rating (SW-108).

    Rating a log never reached a title that had no rating. Titles whose history shows a
    rating were cleared on purpose and stay empty.
    """
    DiaryEntry = apps.get_model("app", "DiaryEntry")
    for model_name in ("Movie", "Music", "Book", "Game"):
        model = apps.get_model("app", model_name)
        history = apps.get_model("app", f"Historical{model_name}")
        newest_rated_log = DiaryEntry.objects.filter(
            user_id=OuterRef("user_id"),
            item_id=OuterRef("item_id"),
            rating__isnull=False,
        ).order_by("-consumed_at", "-id")
        titles = (
            model.objects.filter(score__isnull=True, rating_source__isnull=True)
            .exclude(id__in=history.objects.filter(score__isnull=False).values("id"))
            .annotate(
                log_id=Subquery(newest_rated_log.values("id")[:1]),
                log_rating=Subquery(newest_rated_log.values("rating")[:1]),
            )
            .filter(log_id__isnull=False)
        )
        for title in titles:
            title.score = title.log_rating
            title.rating_source_id = title.log_id
            title.save(update_fields=["score", "rating_source"])


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0083_restore_shifted_log_dates"),
    ]

    operations = [
        migrations.RunPython(fill_empty_ratings_from_rated_logs, reverse_code=migrations.RunPython.noop),
    ]
