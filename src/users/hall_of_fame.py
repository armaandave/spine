"""A user's Hall of Fame: up to eight titles of any media types, in crown order."""

from django.db import transaction

from app.models import PRIMARY_MEDIA_TYPES
from users.models import HALL_OF_FAME_LIMIT, HallOfFamePick, User


class HallOfFameError(ValueError):
    """The list can't be saved as given."""


def picks(user):
    """Return the user's picked Items in crown order."""
    rows = HallOfFamePick.objects.filter(user=user).select_related("item")
    return [row.item for row in rows]


def type_map(user):
    """Return {primary media type: first pick of that type, or None}."""
    first = dict.fromkeys(PRIMARY_MEDIA_TYPES)
    for item in picks(user):
        if item.media_type in first and first[item.media_type] is None:
            first[item.media_type] = item
    return first


def set_picks(user, items):
    """Replace the whole list, in this order, and keep the old per-type fields in step."""
    items = list(items)
    if len(items) > HALL_OF_FAME_LIMIT:
        raise HallOfFameError(f"The Hall of Fame holds at most {HALL_OF_FAME_LIMIT} titles.")
    if len({item.pk for item in items}) != len(items):
        raise HallOfFameError("A title can be in the Hall of Fame only once.")
    with transaction.atomic():
        # One save at a time per user: two at once would both delete, then collide on the
        # (user, position) constraint when inserting.
        User.objects.select_for_update().filter(pk=user.pk).exists()
        HallOfFamePick.objects.filter(user=user).delete()
        HallOfFamePick.objects.bulk_create(
            HallOfFamePick(user=user, item=item, position=position) for position, item in enumerate(items)
        )
        sync_legacy_fields(user)


def sync_legacy_fields(user):
    """Point each hof_<type> at the first pick of that type, so older code still shows one."""
    fields = {f"hof_{media_type}": item for media_type, item in type_map(user).items()}
    for name, item in fields.items():
        setattr(user, name, item)
    User.objects.filter(pk=user.pk).update(**{name: item for name, item in fields.items()})
