"""Sort and filter a page of lists: a profile's Lists, a tag's lists, Add to List."""

from django.db.models import Count, Exists, F, IntegerField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce, Lower
from rest_framework.exceptions import ValidationError

from app import exposure
from app.models import MediaTypes
from lists.models import CustomList, CustomListItem, PersonListItem
from social.models import ContentLike

# Each sort's direction when the request names none.
SORTS = {
    "updated": "desc",
    "name": "asc",
    "created": "desc",
    "items": "desc",
    "likes": "desc",
}
VISIBILITIES = set(CustomList.Visibility.values)
TV_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value, MediaTypes.EPISODE.value)


def apply(lists, params):
    """Return ``lists`` filtered and sorted by the request's list browse params.

    Nothing changes without ``sort``, ``media_type`` or ``visibility``, so older
    apps get the order they always had.
    """
    lists = _filter_visibility(lists, params.get("visibility"))
    lists = _filter_media_type(lists, params.get("media_type"))
    return _sort(lists, params.get("sort"), params.get("direction"))


def _filter_visibility(lists, visibility):
    if not visibility:
        return lists
    if visibility not in VISIBILITIES:
        raise ValidationError({"visibility": [f"Use one of: {', '.join(sorted(VISIBILITIES))}."]})
    return lists.filter(visibility=visibility)


def _filter_media_type(lists, media_type):
    """Keep media lists holding only ``media_type``. Seasons and episodes are TV."""
    if not media_type:
        return lists
    if media_type not in exposure.primary_media_types():
        raise ValidationError({"media_type": ["Use a media type you can see."]})
    wanted = TV_TYPES if media_type == MediaTypes.TV.value else (media_type,)
    others = [value for value in exposure.media_types() if value not in wanted]
    entries = CustomListItem.objects.filter(custom_list=OuterRef("pk"))
    return lists.filter(
        Exists(entries.filter(item__media_type__in=wanted)),
        ~Exists(entries.filter(item__media_type__in=others)),
    )


def _sort(lists, sort, direction):
    if not sort:
        return lists
    if sort not in SORTS:
        raise ValidationError({"sort": [f"Use one of: {', '.join(SORTS)}."]})
    if direction in (None, ""):
        direction = SORTS[sort]
    elif direction not in ("asc", "desc"):
        raise ValidationError({"direction": ["Use asc or desc."]})
    keys = {
        "updated": F("updated_at"),
        "name": Lower("name"),
        "created": F("id"),
        "items": _entries_count(),
        "likes": _likes_count(),
    }
    # Annotated, so Postgres can order a DISTINCT query by them.
    lists = lists.annotate(browse_key=keys[sort], browse_name=Lower("name"))
    ordering = F("browse_key").desc() if direction == "desc" else F("browse_key").asc()
    # Ties fall back to the name, then the order lists were made in.
    return lists.order_by(ordering, "browse_name", "id")


def _count(queryset):
    return Coalesce(
        Subquery(
            queryset.order_by().values("custom_list").annotate(total=Count("pk")).values("total")[:1],
            output_field=IntegerField(),
        ),
        Value(0),
    )


def _entries_count():
    """Count what a list's count label shows: its visible items, or its people."""
    return _count(
        CustomListItem.objects.filter(
            custom_list=OuterRef("pk"),
            item__media_type__in=exposure.media_types(),
        ),
    ) + _count(PersonListItem.objects.filter(custom_list=OuterRef("pk")))


def _likes_count():
    return Coalesce(
        Subquery(
            ContentLike.objects.filter(
                target_type=ContentLike.CUSTOM_LIST,
                target_id=OuterRef("pk"),
            )
            .order_by()
            .values("target_id")
            .annotate(total=Count("pk"))
            .values("total")[:1],
            output_field=IntegerField(),
        ),
        Value(0),
    )
