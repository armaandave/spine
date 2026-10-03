from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers

from api.serializers.common import (
    get_or_create_item_from_metadata,
    media_summary_from_item,
    user_summary,
)
from api.services import tracking as tracking_service
from app import single_weight
from app.models import MediaLike, MediaTypes, Tag
from app.providers import services as provider_services
from app.services import create_diary_entry, update_diary_entry
from social.models import Activity, ContentLike


def diary_payload(entry, request=None, viewer=None):
    """Serialize a diary entry for the API."""
    like_count = getattr(entry, "viewer_like_count", None)
    if like_count is None:
        like_count = ContentLike.objects.filter(
            target_type=ContentLike.DIARY_ENTRY,
            target_id=entry.id,
        ).count()
    viewer_has_liked = getattr(entry, "viewer_has_liked", None)
    if viewer_has_liked is None:
        viewer_has_liked = (
            viewer
            and viewer.is_authenticated
            and ContentLike.objects.filter(
                user=viewer,
                target_type=ContentLike.DIARY_ENTRY,
                target_id=entry.id,
            ).exists()
        )
    is_single_weight = single_weight.uses_half_star_rating(entry.item)
    payload = {
        "id": entry.id,
        "user": user_summary(entry.user, request=request),
        "media": media_summary_from_item(
            entry.item,
            request=request,
            user=viewer,
            include_user_state=False,
        ),
        "consumed_at": (
            single_weight.calendar_date(entry.consumed_at).isoformat()
            if is_single_weight
            else entry.consumed_at
        ),
        "rating": (
            str(single_weight.rating_to_wire(entry.rating))
            if is_single_weight and entry.rating is not None
            else str(entry.rating) if entry.rating is not None else None
        ),
        "review_title": entry.review_title,
        "review": entry.review,
        "contains_spoilers": entry.contains_spoilers,
        "liked": entry.liked,
        "is_rewatch": entry.is_rewatch,
        "tags": [tag.name for tag in entry.tags.all()],
        "visibility": "public" if is_single_weight else entry.visibility,
        "like_count": like_count,
        "viewer_has_liked": bool(viewer_has_liked),
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }
    if entry.item.media_type == "game":
        session = getattr(entry, "game_playthrough", None)
        payload.update({"game_playthrough_id": session.pk if session else None, "total_minutes": (entry.progress_snapshot or {}).get("total_minutes"), "percentage": (entry.progress_snapshot or {}).get("percentage")})
    return payload


def prime_diary_likes(entries, viewer):
    """Attach like counts for a bounded diary page."""
    entries = list(entries)
    entry_ids = [entry.pk for entry in entries]
    counts = dict(
        ContentLike.objects.filter(
            target_type=ContentLike.DIARY_ENTRY,
            target_id__in=entry_ids,
        )
        .values("target_id")
        .annotate(count=Count("id"))
        .values_list("target_id", "count"),
    )
    liked = set()
    if viewer and viewer.is_authenticated:
        liked = set(
            ContentLike.objects.filter(
                user=viewer,
                target_type=ContentLike.DIARY_ENTRY,
                target_id__in=entry_ids,
            ).values_list("target_id", flat=True),
        )
    for entry in entries:
        entry.viewer_like_count = counts.get(entry.pk, 0)
        entry.viewer_has_liked = entry.pk in liked


def create_entry(user, data):
    """Create a diary entry from API payload."""
    ref = data["ref"]
    metadata = provider_services.get_media_metadata(
        ref["media_type"],
        ref["media_id"],
        ref["source"],
        [ref.get("season_number")] if ref.get("season_number") is not None else None,
        ref.get("episode_number"),
    )
    is_single_weight = single_weight.uses_half_star_rating(ref["media_type"])
    consumed_at = data.get("consumed_at")
    if is_single_weight and consumed_at is None:
        raise serializers.ValidationError({"consumed_at": "A consumption date is required."})
    consumed_at = consumed_at or timezone.now()
    rating = data.get("rating")
    if is_single_weight:
        try:
            rating = single_weight.rating_from_wire(rating)
            if ref["media_type"] != MediaTypes.GAME.value:
                consumed_at = single_weight.calendar_datetime(consumed_at)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"detail": error.messages[0]}) from error
    auto_mark_consumed = data.get("auto_mark_consumed", False)
    watch_episode = auto_mark_consumed and ref["media_type"] == MediaTypes.EPISODE.value

    with transaction.atomic():
        item = get_or_create_item_from_metadata(ref, metadata)
        if ref["media_type"] == "game":
            if not data.get("mutation_id"):
                raise serializers.ValidationError({"mutation_id": "A mutation UUID is required for game completion."})
            from app import game_tracking

            payload = {key: value for key, value in data.items() if key in {"mutation_id", "playthrough_id", "start_date", "total_minutes", "percentage", "review", "review_title", "contains_spoilers", "tags", "is_rewatch", "liked"}}
            payload["completion_date"] = consumed_at
            if "rating" in data:
                payload["rating"] = rating
            _, entry = tracking_service._call_book(game_tracking.complete, user, item, **payload)
            return entry
        entry = create_diary_entry(
            user=user,
            item=item,
            consumed_at=consumed_at,
            rating=rating,
            review=data.get("review", ""),
            liked=(
                data["liked"]
                if "liked" in data
                else MediaLike.objects.filter(user=user, item=item).exists()
            ),
            is_rewatch=(
                data.get("is_rewatch")
                if is_single_weight
                else data.get("is_rewatch", False)
            ),
            # Episodes use Season.watch below so every rewatch remains a distinct
            # Episode row instead of rewriting all repeats for the same Item.
            auto_mark_consumed=auto_mark_consumed and not watch_episode,
            tags=data.get("tags", []),
            review_title=data.get("review_title", ""),
            contains_spoilers=data.get("contains_spoilers", False),
            visibility="public" if is_single_weight else data.get("visibility", "public"),
        )
        if watch_episode:
            tracking_service.watch_episode(
                user,
                source=ref["source"],
                media_id=ref["media_id"],
                season_number=ref["season_number"],
                episode_number=ref["episode_number"],
                watched_at=consumed_at,
            )
        if not is_single_weight:
            Activity.objects.create(
                actor=user,
                verb="diary_created",
                target_type="diary",
                target_id=entry.id,
                item=item,
                visibility=entry.visibility,
                snapshot={
                    "rating": str(entry.rating) if entry.rating is not None else None,
                    "liked": bool(entry.liked),
                },
            )
    return entry


def update_entry(entry, data):
    """Update a diary entry from API payload."""
    data = dict(data)
    tags = data.pop("tags", None)
    if single_weight.uses_half_star_rating(entry.item):
        try:
            if "rating" in data:
                data["rating"] = single_weight.rating_from_wire(data["rating"])
            if "consumed_at" in data and entry.item.media_type != MediaTypes.GAME.value:
                data["consumed_at"] = single_weight.calendar_datetime(data["consumed_at"])
        except DjangoValidationError as error:
            raise serializers.ValidationError({"detail": error.messages[0]}) from error
        data.pop("visibility", None)
    return tracking_service._call_book(update_diary_entry, entry, data, tags=tags)


def tag_results(query, user=None, *, limit=10):
    """Return tag search results."""
    queryset = Tag.objects.all()
    ordering = ["-usage_count", "name"]
    if user is not None:
        queryset = (
            queryset.filter(diary_entries__user=user)
            .annotate(user_usage_count=Count("diary_entries", filter=Q(diary_entries__user=user)))
            .distinct()
        )
        ordering = ["-user_usage_count", "name"]
    if query:
        queryset = queryset.filter(name__icontains=query)
    if limit is not None:
        queryset = queryset.order_by(*ordering)[:limit]
    else:
        queryset = queryset.order_by(*ordering)
    return [
        {"name": tag.name, "usage_count": getattr(tag, "user_usage_count", tag.usage_count)}
        for tag in queryset
    ]
