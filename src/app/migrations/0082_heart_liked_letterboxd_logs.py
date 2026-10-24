from django.db import migrations
from django.db.models import Exists, OuterRef


def heart_liked_letterboxd_logs(apps, _):
    """Heart imported Letterboxd logs of films the user likes; imports dropped those hearts."""
    DiaryEntry = apps.get_model("app", "DiaryEntry")
    MediaLike = apps.get_model("app", "MediaLike")
    liked = MediaLike.objects.filter(user_id=OuterRef("user_id"), item_id=OuterRef("item_id"))
    DiaryEntry.objects.filter(import_source="letterboxd", liked=False).filter(Exists(liked)).update(liked=True)


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0081_musicgenrealbum"),
    ]

    operations = [
        migrations.RunPython(heart_liked_letterboxd_logs, reverse_code=migrations.RunPython.noop),
    ]
