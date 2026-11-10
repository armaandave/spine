from django.db import migrations

# How the app ordered the one-pick-per-type slots before the list existed.
RANK = ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"]


def crown_order(filled, enabled):
    """Return the media types in the order today's crown shows them, then the hidden ones.

    ``filled`` and ``enabled`` are the types with a pick and the types the user has on.
    Visible picks follow RANK, except music, which sits in the middle (index n // 2 of
    n visible picks). Picks of types the user turned off follow, in RANK order.
    """
    visible = [t for t in RANK if t in filled and t in enabled and t != "music"]
    if "music" in filled and "music" in enabled:
        visible.insert((len(visible) + 1) // 2, "music")
    hidden = [t for t in RANK if t in filled and t not in enabled]
    return visible + hidden


def fill_picks(apps, schema_editor):
    User = apps.get_model("users", "User")
    Pick = apps.get_model("users", "HallOfFamePick")
    for user in User.objects.all().iterator():
        filled = {t: getattr(user, f"hof_{t}_id") for t in RANK if getattr(user, f"hof_{t}_id")}
        if not filled:
            continue
        enabled = {t for t in RANK if getattr(user, f"{t}_enabled")}
        Pick.objects.bulk_create(
            Pick(user_id=user.pk, item_id=filled[t], position=position)
            for position, t in enumerate(crown_order(set(filled), enabled))
        )


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0062_hall_of_fame_pick"),
    ]

    operations = [
        migrations.RunPython(fill_picks, migrations.RunPython.noop),
    ]
