"""Viewer completion counts for finite media collections."""

from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.db.models import OuterRef, Subquery

from app import music_catalog
from app.models import (
    USER_OWNED_MEDIA_TYPES,
    Episode,
    Item,
    MediaSeries,
    MediaSeriesItem,
    MediaTypes,
    Status,
)


def completion_payload(completed_count, total_count):
    """Return the stable additive API shape."""
    return {
        "completed_count": max(int(completed_count or 0), 0),
        "total_count": max(int(total_count or 0), 0),
    }


def completed_item_ids(user):
    """Return items whose latest viewer tracking state is Completed."""
    if not user or not user.is_authenticated:
        return set()
    cached = getattr(user, "_api_completed_item_ids", None)
    if cached is not None:
        return cached

    completed = set()
    for media_type in USER_OWNED_MEDIA_TYPES:
        model = apps.get_model("app", media_type)
        latest_pk = (
            model.objects.filter(user=user, item_id=OuterRef("item_id"))
            .order_by("-created_at", "-id")
            .values("pk")[:1]
        )
        completed.update(
            model.objects.filter(
                user=user,
                pk=Subquery(latest_pk),
                status=Status.COMPLETED.value,
            ).values_list("item_id", flat=True),
        )
    completed.update(
        Episode.objects.filter(
            related_season__user=user,
            item__media_type=MediaTypes.EPISODE.value,
        ).values_list("item_id", flat=True),
    )
    user._api_completed_item_ids = completed
    return completed


def tracked_item_ids(user):
    """Return items with a current viewer tracking row."""
    if not user or not user.is_authenticated:
        return set()
    cached = getattr(user, "_api_tracked_item_ids", None)
    if cached is not None:
        return cached

    tracked = set()
    for media_type in USER_OWNED_MEDIA_TYPES:
        model = apps.get_model("app", media_type)
        tracked.update(
            model.objects.filter(user=user).values_list("item_id", flat=True),
        )
    tracked.update(
        Episode.objects.filter(
            related_season__user=user,
            item__media_type=MediaTypes.EPISODE.value,
        ).values_list("item_id", flat=True),
    )
    user._api_tracked_item_ids = tracked
    return tracked


def completion_for_items(user, items):
    """Count completed stored items for an authenticated viewer."""
    if not user or not user.is_authenticated:
        return None
    item_ids = {
        item.pk if isinstance(item, Item) else int(item)
        for item in items
        if item is not None
    }
    return completion_payload(
        len(item_ids.intersection(completed_item_ids(user))),
        len(item_ids),
    )


def completion_for_summaries(user, summaries):
    """Count completed unique media summaries for an authenticated viewer."""
    if not user or not user.is_authenticated:
        return None

    identities = {}
    for index, summary in enumerate(summaries):
        ref = summary.get("ref") or {}
        identity = (
            ref.get("source"),
            ref.get("media_type"),
            str(ref.get("media_id") or ""),
            ref.get("season_number"),
            ref.get("episode_number"),
        )
        if not identity[2]:
            identity = ("missing", index)
        identities.setdefault(identity, ref.get("item_id"))

    completed = completed_item_ids(user)
    completed_count = sum(
        item_id is not None and item_id in completed
        for item_id in identities.values()
    )
    return completion_payload(completed_count, len(identities))


def completion_for_payloads(
    user,
    payloads,
    *,
    default_source,
    default_media_type,
):
    """Count provider payloads without creating local Item rows."""
    if not user or not user.is_authenticated:
        return None

    identities = {
        (
            str(payload.get("source") or default_source),
            str(payload.get("media_type") or default_media_type),
            str(payload.get("media_id") or payload.get("id") or ""),
            payload.get("season_number"),
            payload.get("episode_number"),
        )
        for value in payloads
        if isinstance(value, dict)
        and isinstance(
            payload := value.get("item", value),
            dict,
        )
        and (payload.get("media_id") or payload.get("id")) is not None
    }
    # Albums that moved count once, under the ref that holds them now.
    canonical = music_catalog.canonical_music_refs(identity[:3] for identity in identities)
    identities = {(*canonical[identity[:3]], *identity[3:]) for identity in identities}
    item_ids = set()
    grouped = {}
    for source, media_type, media_id, season_number, episode_number in identities:
        grouped.setdefault(
            (source, media_type, season_number, episode_number),
            set(),
        ).add(media_id)
    for (
        source,
        media_type,
        season_number,
        episode_number,
    ), media_ids in grouped.items():
        item_ids.update(
            Item.objects.filter(
                source=source,
                media_type=media_type,
                media_id__in=media_ids,
                season_number=season_number,
                episode_number=episode_number,
            ).values_list("id", flat=True),
        )
    return completion_payload(
        len(item_ids.intersection(completed_item_ids(user))),
        len(identities),
    )


@transaction.atomic
def persist_complete_series(payload):
    """Store a provider series only when its full member set is present."""
    series_id = str(payload.get("series_id") or payload.get("id") or "").strip()
    source = str(payload.get("source") or "").strip()
    media_type = str(payload.get("media_type") or "").strip()
    raw_items = payload.get("items")
    if raw_items is None:
        raw_items = payload.get("books")
    raw_items = raw_items or []
    expected_count = payload.get("item_count")
    if expected_count is None:
        expected_count = payload.get("book_count")
    if expected_count is not None and int(expected_count) != len(raw_items):
        return None
    if not series_id or not source or not media_type or not raw_items:
        return None

    members = _materialized_series_members(
        raw_items,
        default_source=source,
        default_media_type=media_type,
    )
    if members is None:
        return None

    series, _ = MediaSeries.objects.update_or_create(
        source=source,
        media_type=media_type,
        series_id=series_id,
        defaults={
            "name": payload.get("name") or payload.get("series_name") or "",
            "item_count": len(members),
        },
    )
    series.memberships.exclude(item_id__in=members).delete()
    existing = {
        membership.item_id: membership
        for membership in series.memberships.filter(item_id__in=members)
    }
    to_create = []
    to_update = []
    for item_id, position in members.items():
        membership = existing.get(item_id)
        if membership is None:
            to_create.append(
                MediaSeriesItem(
                    series=series,
                    item_id=item_id,
                    position=position,
                ),
            )
        elif membership.position != position:
            membership.position = position
            to_update.append(membership)
    MediaSeriesItem.objects.bulk_create(to_create)
    if to_update:
        MediaSeriesItem.objects.bulk_update(to_update, ["position"])
    return series


def _materialized_series_members(
    raw_items,
    *,
    default_source,
    default_media_type,
):
    members = {}
    for index, value in enumerate(raw_items, start=1):
        item_payload = value.get("item", value) if isinstance(value, dict) else {}
        media_id = str(
            item_payload.get("media_id") or item_payload.get("id") or "",
        ).strip()
        if not media_id:
            return None
        item_source = str(item_payload.get("source") or default_source)
        item_media_type = str(
            item_payload.get("media_type") or default_media_type,
        )
        season_number = item_payload.get("season_number")
        episode_number = item_payload.get("episode_number")
        item, _ = Item.objects.get_or_create(
            source=item_source,
            media_type=item_media_type,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
            defaults={
                "title": (
                    item_payload.get("title")
                    or item_payload.get("name")
                    or media_id
                ),
                "image": (
                    item_payload.get("image")
                    or item_payload.get("image_url")
                    or item_payload.get("poster_url")
                    or settings.IMG_NONE
                ),
            },
        )
        members[item.id] = item_payload.get("position") or index
    return members
