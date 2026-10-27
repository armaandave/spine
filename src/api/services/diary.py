from datetime import datetime

import requests
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from api.serializers.common import (
    find_item,
    get_or_create_item_from_metadata,
    media_summary_from_item,
    user_summary,
)
from api.services import tracking as tracking_service
from app import single_weight, tv_tracking
from app.models import Item, MediaLike, MediaTypes, Sources, Tag, TVListing
from app.providers import services as provider_services
from app.services import create_diary_entry, update_diary_entry
from social.models import Activity, ContentLike

# Where a TV log was saved from; they shape the response, not the log (TV-404, TV-705).
TV_PAGE_FIELDS = ("page_season_number", "page_episode_number")


def tv_write_payload(tv_write, data, *, created):
    """Return the TV keys a log's create or update response adds (plan 2.3.7).

    `tv` is the show's state scoped to the page the log was saved from (its season or
    episode state and composer); `season` and `episode` are the log target's own, so
    the page shows what the log counted watched at once.
    """
    page = tracking_service.tv_page_item(
        tv_write.show_item,
        data.get("page_season_number"),
        data.get("page_episode_number"),
    )
    envelope = tracking_service.tv_envelope(tv_write, page=page)
    keys = ("tv", "season", "episode", "finished", "earlier_unwatched") if created else ("tv", "season", "episode")
    payload = {key: envelope[key] for key in keys}
    if "season_episodes" in envelope:
        payload["season_episodes"] = envelope["season_episodes"]
    return payload


def diary_payload(entry, request=None, viewer=None, *, include_tv_log=False):
    """Serialize a diary entry for the API.

    include_tv_log works out a TV log's tv_log (what it covers and would unmark) for
    one entry: the diary detail, create and update responses. Lists leave it null, so
    a page never works out each show's viewings (TV-207).
    """
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
    media = media_summary_from_item(
        entry.item,
        request=request,
        user=viewer,
        include_user_state=False,
    )
    if not hasattr(entry, "tv_show_title"):
        prime_show_titles([entry])
    # TV-702: "Show · Season 1" and "Show · S1 E4 · Title" need the show's and the
    # episode's own titles; Trakt and Simkl give episode Items the show's title.
    media["show_title"] = entry.tv_show_title
    media["episode_title"] = entry.tv_episode_title
    payload = {
        "id": entry.id,
        "user": user_summary(entry.user, request=request),
        "media": media,
        "consumed_at": consumed_at_payload(entry),
        "rating": rating_payload(entry),
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
        "tv_log": _tv_log(entry, viewer) if include_tv_log else None,
    }
    if entry.item.media_type == "game":
        session = getattr(entry, "game_playthrough", None)
        payload.update({"game_playthrough_id": session.pk if session else None, "total_minutes": (entry.progress_snapshot or {}).get("total_minutes"), "percentage": (entry.progress_snapshot or {}).get("percentage")})
    return payload


def consumed_at_payload(entry):
    """Return a log's date as the API shows it: a calendar date for half-star media."""
    if single_weight.uses_half_star_rating(entry.item):
        return single_weight.calendar_date(entry.consumed_at).isoformat()
    return entry.consumed_at


def rating_payload(entry):
    """Return a log's rating on the API scale, or None."""
    if entry.rating is None:
        return None
    if single_weight.uses_half_star_rating(entry.item):
        return str(single_weight.rating_to_wire(entry.rating))
    return str(entry.rating)


def _tv_log(entry, viewer):
    """Return a TV log's tv_log for its owner only: what it covers and would unmark."""
    if not tv_tracking.supports(entry.item):
        return None
    if viewer is None or not viewer.is_authenticated or viewer.pk != entry.user_id:
        return None
    return tv_tracking.log_info(entry)


def prime_show_titles(entries):
    """Set each entry's show and episode titles with a bounded number of queries.

    Season and episode logs get their show's title; episode logs get the episode's title
    from the stored listing (manual shows: the episode Item's own title), or null when
    unknown. Two queries for a page: the shows' Items and their stored listings.
    """
    entries = list(entries)
    tv_entries = []
    for entry in entries:
        entry.tv_show_title = None
        entry.tv_episode_title = None
        if entry.item.media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value):
            tv_entries.append(entry)
    if not tv_entries:
        return
    keys = {(entry.item.source, entry.item.media_id) for entry in tv_entries}
    shows = {
        (show.source, show.media_id): show
        for show in Item.objects.filter(
            media_type=MediaTypes.TV.value,
            season_number=None,
            episode_number=None,
            source__in={source for source, _ in keys},
            media_id__in={media_id for _, media_id in keys},
        ).only("id", "source", "media_id", "title")
        if (show.source, show.media_id) in keys
    }
    listings = dict(
        TVListing.objects.filter(
            item_id__in=[show.pk for show in shows.values()],
        ).values_list("item_id", "seasons"),
    )
    for entry in tv_entries:
        show = shows.get((entry.item.source, entry.item.media_id))
        if show is None:
            continue
        entry.tv_show_title = show.title
        if entry.item.media_type != MediaTypes.EPISODE.value:
            continue
        if entry.item.source == Sources.MANUAL.value:
            entry.tv_episode_title = entry.item.title or None
            continue
        entry.tv_episode_title = _listed_episode_title(
            listings.get(show.pk),
            entry.item.season_number,
            entry.item.episode_number,
        )


def _listed_episode_title(seasons, season_number, episode_number):
    """Return an episode's title from a stored listing's TVListing.seasons JSON."""
    season = (seasons or {}).get(str(season_number)) or {}
    for row in season.get("episodes") or ():
        if row and int(row[0]) == episode_number:
            return (row[2] if len(row) > 2 else "") or None
    return None


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
    if tv_tracking.supports(ref["media_type"]):
        return _create_tv_entry(user, data)
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
            auto_mark_consumed=auto_mark_consumed,
            tags=data.get("tags", []),
            review_title=data.get("review_title", ""),
            contains_spoilers=data.get("contains_spoilers", False),
            visibility="public" if is_single_weight else data.get("visibility", "public"),
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


TV_LOG_FIELDS = (
    "consumed_at",
    "rating",
    "review",
    "review_title",
    "liked",
    "is_rewatch",
    "contains_spoilers",
    "visibility",
)


def _create_tv_entry(user, data):
    """Save a TV log through tv_tracking.create_log (TV-400 to TV-407, TV-703).

    It fetches what it needs before its own transaction, so no provider call runs inside
    one. auto_mark_consumed is ignored: every TV log counts its target watched. A date
    or datetime becomes the user's calendar date, and a future one is a 400. The log
    posts its one diary activity itself. A mutation ID applies the log at most once.

    Installed builds send a datetime and no mutation ID; they get the server's rules but
    no earlier-episodes question or season-finish composer (LEGACY_APP).
    """
    ref = data["ref"]
    tracking_service.require_tv_numbers(
        ref["media_type"],
        ref.get("season_number"),
        ref.get("episode_number"),
    )
    item = find_item(ref)
    if item is None:
        try:
            metadata = provider_services.get_media_metadata(
                ref["media_type"],
                ref["media_id"],
                ref["source"],
                [ref.get("season_number")] if ref.get("season_number") is not None else None,
                ref.get("episode_number"),
            )
        except provider_services.ProviderAPIError as error:
            if error.status_code != requests.codes.not_found:
                raise
            # The provider doesn't list it; tv_tracking says why it's rejected (TV-106).
            item = tracking_service.resolve_tv_item(
                source=ref["source"],
                media_type=ref["media_type"],
                media_id=ref["media_id"],
                season_number=ref.get("season_number"),
                episode_number=ref.get("episode_number"),
            )
        else:
            item = get_or_create_item_from_metadata(ref, metadata)
    consumed_at = data.get("consumed_at")
    installed_build = isinstance(consumed_at, datetime) and not data.get("mutation_id")
    result = tracking_service._call_tv(
        tv_tracking.create_log,
        user,
        item,
        consumed_at=consumed_at,
        rating=data.get("rating"),
        review=data.get("review", ""),
        liked=data.get("liked"),
        is_rewatch=data.get("is_rewatch"),
        tags=data.get("tags") or [],
        review_title=data.get("review_title", ""),
        contains_spoilers=data.get("contains_spoilers", False),
        visibility=data.get("visibility", "public"),
        entry_point=tv_tracking.LEGACY_APP if installed_build else tv_tracking.APP,
        mutation_id=data.get("mutation_id"),
    )
    entry = result.entry
    if entry is None:
        # A repeated mutation whose log has since been deleted.
        raise NotFound
    entry.tv_write = result
    return entry


def _update_tv_entry(entry, data, tags):
    """Edit a TV log through tv_tracking.update_log (TV-405, TV-407, TV-501, TV-502)."""
    payload = {key: value for key, value in data.items() if key in TV_LOG_FIELDS}
    result = tracking_service._call_tv(
        tv_tracking.update_log,
        entry,
        payload,
        tags=tags,
        entry_point=tv_tracking.APP,
    )
    updated = result.entry or entry
    updated.tv_write = result
    return updated


def update_entry(entry, data):
    """Update a diary entry from API payload."""
    data = dict(data)
    tags = data.pop("tags", None)
    for page_key in TV_PAGE_FIELDS:
        data.pop(page_key, None)
    if tv_tracking.supports(entry.item):
        return _update_tv_entry(entry, data, tags)
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
