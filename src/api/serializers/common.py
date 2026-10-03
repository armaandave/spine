from decimal import Decimal

from django.conf import settings
from django.db.models import Count, F, Prefetch, Window, prefetch_related_objects
from django.db.models.functions import RowNumber
from django.utils.text import slugify
from rest_framework import serializers

from api.services import completion as completion_service
from app import config, single_weight
from app.models import (
    BasicMedia,
    DiaryEntry,
    Item,
    MediaLike,
    MediaTypes,
    Season,
    Sources,
    Status,
)
from lists.models import CustomList, CustomListItem
from social.models import ProgressChange


class MediaRefSerializer(serializers.Serializer):
    """Stable public media identity."""

    item_id = serializers.IntegerField(required=False, allow_null=True)
    source = serializers.CharField()
    media_type = serializers.CharField()
    media_id = serializers.CharField()
    season_number = serializers.IntegerField(required=False, allow_null=True)
    episode_number = serializers.IntegerField(required=False, allow_null=True)


class CompletionProgressSerializer(serializers.Serializer):
    """Finite collection completion counts."""

    completed_count = serializers.IntegerField(min_value=0)
    total_count = serializers.IntegerField(min_value=0)


class PersonCompletionResponseSerializer(serializers.Serializer):
    """Viewer completion for one provider person."""

    completion = CompletionProgressSerializer(allow_null=True)


def absolute_url(request, url):
    """Return an absolute URL for relative uploaded media paths."""
    if not url:
        return None
    value = str(url)
    if value.startswith(("http://", "https://")):
        return value
    if request is not None:
        return request.build_absolute_uri(value)
    return value


def image_url(request, value):
    """Normalize image values from model fields or provider payloads."""
    if not value:
        return absolute_url(request, settings.IMG_NONE)
    return absolute_url(request, getattr(value, "url", value))


def artwork_from_payload(payload, media_type=None, request=None):
    """Return normalized poster/backdrop fields for media-card API payloads."""
    poster_value = _first_image_value(
        payload,
        (
            "poster_url",
            "poster",
            "poster_path",
            "main_picture",
            "cover",
            "image",
            "image_url",
            "medium_url",
        ),
    )
    poster = image_url(request, _provider_image_value(poster_value))
    width = _first_number(payload, ("poster_width", "image_width", "width"))
    height = _first_number(payload, ("poster_height", "image_height", "height"))
    aspect_ratio = _aspect_ratio(payload, width, height)
    orientation = _orientation(width, height)

    backdrop = _backdrop_url(payload, request=request)
    if media_type == MediaTypes.EPISODE.value:
        if backdrop is None and poster_value:
            still = _provider_image_value(poster_value)
            if still and str(still) != settings.IMG_NONE:
                backdrop = absolute_url(request, still)
        return {
            "image_url": None,
            "poster_url": None,
            "backdrop_url": backdrop,
            "poster_aspect_ratio": None,
            "poster_width": None,
            "poster_height": None,
            "poster_orientation": None,
        }
    return {
        "image_url": poster,
        "poster_url": poster,
        "backdrop_url": backdrop,
        "poster_aspect_ratio": aspect_ratio,
        "poster_width": width,
        "poster_height": height,
        "poster_orientation": orientation,
    }


def artwork_from_item(item, request=None):
    """Return normalized artwork fields from stored Item data."""
    return artwork_from_payload({"image": item.image}, item.media_type, request=request)


def _first_image_value(payload, keys):
    for key in keys:
        value = payload.get(key)
        if value:
            return value
    return None


def _provider_image_value(value):
    if isinstance(value, dict):
        if value.get("image_id"):
            return f"https://images.igdb.com/igdb/image/upload/t_original/{value['image_id']}.jpg"
        return value.get("large") or value.get("medium") or value.get("small") or value.get("url") or value.get("medium_url")
    if isinstance(value, str) and value.startswith("/"):
        return f"https://image.tmdb.org/t/p/original{value}"
    return value


def _backdrop_url(payload, request=None):
    value = payload.get("backdrop") or payload.get("backdrop_url") or payload.get("backdrop_path")
    if not value:
        for artwork in payload.get("artworks") or []:
            if isinstance(artwork, dict) and artwork.get("image_id"):
                return f"https://images.igdb.com/igdb/image/upload/t_original/{artwork['image_id']}.jpg"
    value = _provider_image_value(value)
    return absolute_url(request, value) if value else None


def _first_number(payload, keys):
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _aspect_ratio(payload, width, height):
    if width and height:
        return round(width / height, 3)
    value = payload.get("poster_aspect_ratio") or payload.get("aspect_ratio")
    if value in (None, ""):
        return None
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _orientation(width, height):
    if not width or not height:
        return "unknown"
    if width == height:
        return "square"
    return "portrait" if height > width else "landscape"


def media_ref_from_item(item):
    """Serialize an Item as a stable media ref."""
    if item is None:
        return None
    return {
        "item_id": item.id,
        "source": item.source,
        "media_type": item.media_type,
        "media_id": item.media_id,
        "season_number": item.season_number,
        "episode_number": item.episode_number,
    }


def find_item(ref):
    """Find an Item for a media ref if it has already been materialized."""
    return Item.objects.filter(
        source=ref["source"],
        media_type=ref["media_type"],
        media_id=ref["media_id"],
        season_number=ref.get("season_number"),
        episode_number=ref.get("episode_number"),
    ).first()


def get_or_create_item_from_metadata(ref, metadata):
    """Get or create an Item using provider metadata."""
    from api.services.filters import update_item_filter_metadata

    defaults = {
        "title": metadata.get("title") or metadata.get("name") or ref["media_id"],
        "image": metadata.get("image") or settings.IMG_NONE,
    }
    if ref["media_type"] == MediaTypes.BOOK.value:
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        if total_pages:
            defaults["total_pages"] = total_pages
    item, _ = Item.objects.get_or_create(
        source=ref["source"],
        media_type=ref["media_type"],
        media_id=ref["media_id"],
        season_number=ref.get("season_number"),
        episode_number=ref.get("episode_number"),
        defaults=defaults,
    )
    if defaults.get("total_pages") and item.total_pages != defaults["total_pages"]:
        item.total_pages = defaults["total_pages"]
        item.save(update_fields=["total_pages"])
    update_item_filter_metadata(item, metadata)
    return item


def media_summary_from_item(
    item,
    request=None,
    user=None,
    *,
    include_resolved_backdrop=False,
    include_user_state=True,
):
    """Serialize an Item into the common media summary shape."""
    artwork = artwork_from_item(item, request=request)
    if include_resolved_backdrop:
        default_backdrop_url, custom_backdrop_url = resolved_item_backdrop_urls(item, request=request, user=user)
        artwork["backdrop_url"] = default_backdrop_url
    else:
        custom_backdrop_url = custom_backdrop_url_for_user(
            user,
            media_ref_from_item(item),
            request=request,
            item=item,
        ) if user else None
    return {
        "ref": media_ref_from_item(item),
        "title": item.title,
        "subtitle": None,
        "overview": None,
        **artwork,
        "poster_accent_color": item.poster_accent_color or None,
        "release_date": None,
        "default_source": item.source,
        "custom_poster_url": custom_poster_url_for_user(
            user,
            media_ref_from_item(item),
            request=request,
            item=item,
        ) if user else None,
        "custom_backdrop_url": custom_backdrop_url,
        "user_state": user_state_for_item(user, item) if user and include_user_state else None,
    }


def prime_collection_items(items, user, *, media_by_item=None):
    """Attach bounded viewer data once for a paginated collection."""
    items = list(items)
    if not items or not user or not user.is_authenticated:
        return
    from app.models import CustomBackdropPreference, CustomPosterPreference

    prefetch_related_objects(
        items,
        Prefetch(
            "customposterpreference_set",
            queryset=CustomPosterPreference.objects.filter(user=user),
            to_attr="viewer_custom_poster_preferences",
        ),
        Prefetch(
            "custombackdroppreference_set",
            queryset=CustomBackdropPreference.objects.filter(user=user),
            to_attr="viewer_custom_backdrop_preferences",
        ),
    )
    if media_by_item is None:
        return

    item_ids = [item.pk for item in items]
    list_ids = {}
    for item_id, list_id in CustomListItem.objects.filter(
        item_id__in=item_ids,
        custom_list__owner=user,
    ).values_list("item_id", "custom_list_id"):
        list_ids.setdefault(item_id, []).append(list_id)
    latest_diary = {
        entry.item_id: entry
        for entry in DiaryEntry.objects.filter(user=user, item_id__in=item_ids)
        .annotate(
            viewer_count=Window(Count("id"), partition_by=[F("item_id")]),
            viewer_row=Window(
                RowNumber(),
                partition_by=[F("item_id")],
                order_by=[F("consumed_at").desc(), F("id").desc()],
            ),
        )
        .filter(viewer_row=1)
    }
    liked = set(
        MediaLike.objects.filter(user=user, item_id__in=item_ids).values_list(
            "item_id",
            flat=True,
        ),
    )
    progress_changes = {
        change.item_id: change
        for change in ProgressChange.objects.filter(
            actor=user,
            item_id__in=item_ids,
        )
        .annotate(
            viewer_row=Window(
                RowNumber(),
                partition_by=[F("item_id")],
                order_by=[F("created_at").desc(), F("id").desc()],
            ),
        )
        .filter(viewer_row=1)
    }
    for item in items:
        media = media_by_item.get(item.pk)
        if media is not None:
            media.user = user
            media.viewer_latest_progress_change = progress_changes.get(item.pk)
        item.viewer_media = media
        item.viewer_list_ids = list_ids.get(item.pk, [])
        item.viewer_latest_diary = latest_diary.get(item.pk)
        item.viewer_diary_count = getattr(item.viewer_latest_diary, "viewer_count", 0)
        item.viewer_has_liked = item.pk in liked


def resolved_item_backdrop_urls(item, request=None, user=None):
    """Return the same default/custom backdrop pair used by media detail."""
    custom_backdrop_url = custom_backdrop_url_for_user(user, media_ref_from_item(item), request=request) if user else None
    if custom_backdrop_url:
        return None, custom_backdrop_url

    if item.media_type not in [MediaTypes.MOVIE.value, MediaTypes.TV.value, MediaTypes.GAME.value]:
        return None, custom_backdrop_url

    from api.services.media import resolved_backdrop_urls
    from app.providers import services as provider_services

    metadata = provider_services.get_media_metadata(item.media_type, item.media_id, item.source)
    return resolved_backdrop_urls(
        source=item.source,
        media_type=item.media_type,
        media_id=item.media_id,
        metadata=metadata,
        request=request,
        user=user,
        item=item,
    )


def synopsis_from_payload(payload):
    """Return provider synopsis text for API responses."""
    placeholder = "No synopsis available."
    for key in ("overview", "synopsis", "description"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text != placeholder:
            return text
    return None


_UNRESOLVED_ITEM = object()
COMPLETABLE_RELATED_SECTION_IDS = {
    "collection",
    "dlcs",
    "expanded_games",
    "expansions",
    "relations",
    "remakes",
    "remasters",
    "series",
    "standalone_expansions",
}


def media_summary_from_provider(
    payload,
    media_type,
    source,
    request=None,
    user=None,
    item=_UNRESOLVED_ITEM,
):
    """Serialize provider search/detail payload into the common summary shape."""
    media_id = str(payload.get("media_id") or payload.get("id") or "")
    season_number = payload.get("season_number")
    episode_number = payload.get("episode_number")
    if item is _UNRESOLVED_ITEM:
        item = Item.objects.filter(
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
        ).first()
    return {
        "ref": {
            "item_id": item.id if item else None,
            "source": source,
            "media_type": media_type,
            "media_id": media_id,
            "season_number": season_number,
            "episode_number": episode_number,
        },
        "title": payload.get("title") or payload.get("name") or "",
        **(
            {"display_title": payload["display_title"]}
            if payload.get("display_title")
            else {}
        ),
        **({"relation": payload["relation"]} if payload.get("relation") else {}),
        "subtitle": payload.get("year") or payload.get("subtitle"),
        "overview": synopsis_from_payload(payload),
        **artwork_from_payload(payload, media_type, request=request),
        "poster_accent_color": getattr(item, "poster_accent_color", None) or None,
        "release_date": (
            payload.get("release_date")
            or payload.get("first_air_date")
            or payload.get("publish_date")
            or payload.get("end_date")
            or (payload.get("details") or {}).get("release_date")
        ),
        "genres": payload.get("genres") or [],
        "languages": payload.get("languages") or [],
        "roles": payload.get("roles") or [],
        "credit_roles": payload.get("credit_roles") or payload.get("roles") or [],
        "default_source": source,
        "position": payload.get("position"),
        "custom_poster_url": custom_poster_url_for_user(
            user,
            media_ref_from_item(item),
            request=request,
            item=item,
        ) if user and item else None,
        "user_state": user_state_for_item(user, item) if user and item else None,
    }


def details_for_api(metadata):
    """Return provider details with common top-level fields merged in."""
    details = dict(metadata.get("details") or {})
    genres = metadata.get("genres")
    if genres and "genres" not in details:
        names = []
        for genre in genres:
            name = genre.get("name") if isinstance(genre, dict) else genre
            if name:
                names.append(str(name))
        details["genres"] = names
    if metadata.get("time_to_beat") and "time_to_beat" not in details:
        details["time_to_beat"] = metadata["time_to_beat"]
    if metadata.get("revenue") and "revenue" not in details:
        details["revenue"] = metadata["revenue"]
    return details


def _credit_id(person):
    value = person.get("person_id") or person.get("id")
    return str(value or slugify(person.get("name") or "person"))


def _credit_image(request, person):
    value = person.get("image") or person.get("image_url") or person.get("profile_path")
    return image_url(request, value) if value else None


def cast_from_metadata(metadata, request=None):
    """Normalize provider cast into the native credit shape."""
    people = metadata.get("cast") or []
    return [
        {
            "id": _credit_id(person),
            "name": person.get("name"),
            "person_source": person.get("person_source"),
            "role": person.get("role"),
            "character": person.get("character"),
            "image_url": _credit_image(request, person),
        }
        for person in people
        if isinstance(person, dict) and person.get("name")
    ]


def crew_from_metadata(metadata, request=None):
    """Normalize provider crew into the native credit shape."""
    people = metadata.get("crew") or []
    return [
        {
            "id": _credit_id(person),
            "name": person.get("name"),
            "person_source": person.get("person_source"),
            "role": (person.get("roles") or [None])[0] if person.get("roles") else person.get("job") or person.get("role"),
            "character": person.get("character"),
            "image_url": _credit_image(request, person),
        }
        for person in people
        if isinstance(person, dict) and person.get("name")
    ]


def seasons_from_metadata(metadata, request=None, user=None):
    """Normalize TV seasons into the native season summary shape."""
    seasons = (metadata.get("related") or {}).get("seasons") or []
    tracked = {}
    if user and user.is_authenticated:
        tracked = {
            season.item.season_number: season
            for season in Season.objects.filter(
                user=user,
                item__source=metadata.get("source"),
                item__media_id=str(metadata.get("media_id") or ""),
                item__season_number__in=[
                    value.get("season_number")
                    for value in seasons
                    if isinstance(value, dict)
                ],
            )
            .select_related("item")
            .prefetch_related("episodes__item")
        }
    return [
        {
            "season_number": season.get("season_number"),
            "title": season.get("season_title") or season.get("title") or season.get("name") or "",
            "episode_count": _season_episode_count(season),
            "image_url": image_url(request, season.get("image") or season.get("poster_path"))
            if (season.get("image") or season.get("poster_path"))
            else None,
            "release_date": season.get("first_air_date") or season.get("air_date") or season.get("release_date"),
            "completion": (
                completion_service.completion_payload(
                    len({
                        episode.item.episode_number
                        for episode in tracked[season.get("season_number")].episodes.all()
                    }),
                    _season_episode_count(season),
                )
                if user
                and user.is_authenticated
                and season.get("season_number") in tracked
                else completion_service.completion_payload(
                    0,
                    _season_episode_count(season),
                )
                if user and user.is_authenticated
                else None
            ),
        }
        for season in seasons
        if isinstance(season, dict)
    ]


def _season_episode_count(season):
    value = (
        season.get("episode_count")
        or season.get("episodes")
        or season.get("max_progress")
        or 0
    )
    if isinstance(value, (list, tuple)):
        return len(value)
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def episodes_from_metadata(metadata, request=None):
    """Normalize season episodes into the native episode summary shape."""
    episodes = metadata.get("episodes") or []
    return [
        {
            "episode_number": episode.get("episode_number"),
            "title": episode.get("title") or episode.get("name") or "",
            "overview": episode.get("overview"),
            "air_date": episode.get("air_date"),
            "runtime": episode.get("runtime"),
            "image_url": image_url(request, episode.get("image") or episode.get("still_path"))
            if (episode.get("image") or episode.get("still_path"))
            else None,
            "image_role": "still",
            "rating": str(episode.get("vote_average")) if episode.get("vote_average") is not None else episode.get("rating"),
        }
        for episode in episodes
        if isinstance(episode, dict)
    ]


def custom_poster_url_for_user(user, ref, request=None, item=None):
    """Return a viewer's custom poster for an existing Item."""
    if not user or not user.is_authenticated:
        return None
    from app.models import CustomPosterPreference

    if item is None:
        item = find_item(ref)
    if item is None:
        return None
    prefetched = getattr(item, "viewer_custom_poster_preferences", None)
    preference = prefetched[0] if prefetched else None
    if prefetched is None:
        preference = CustomPosterPreference.objects.filter(user=user, item=item).first()
    return absolute_url(request, preference.custom_image_url) if preference else None


def custom_backdrop_url_for_user(user, ref, request=None, item=None):
    """Return a viewer's custom backdrop for an existing Item."""
    if not user or not user.is_authenticated:
        return None
    from app.models import CustomBackdropPreference

    if item is None:
        item = find_item(ref)
    if item is None:
        return None
    prefetched = getattr(item, "viewer_custom_backdrop_preferences", None)
    preference = prefetched[0] if prefetched else None
    if prefetched is None:
        preference = CustomBackdropPreference.objects.filter(user=user, item=item).first()
    return absolute_url(request, preference.custom_image_url) if preference else None


def related_sections_from_payload(related, media_type, source, request=None, user=None):
    """Normalize provider related media into mobile section cards."""
    if not related:
        return []

    if media_type == MediaTypes.BOOK.value:
        series_sections = [
            ("series", key, values)
            for key, values in related.items()
            if source == Sources.HARDCOVER.value
            and key not in {"other_editions", "recommendations"}
            and values
        ]
        candidates = [
            *series_sections,
            ("other_editions", "Other Editions", related.get("other_editions") or []),
            ("recommendations", "Recommendations", related.get("recommendations") or []),
        ]
    elif media_type == MediaTypes.GAME.value:
        candidates = [
            (key, key.replace("_", " ").title(), related.get(key) or [])
            for key in (
                "collection",
                "dlcs",
                "expansions",
                "standalone_expansions",
                "remasters",
                "remakes",
                "expanded_games",
                "recommendations",
            )
        ]
    elif media_type == MediaTypes.ANIME.value:
        ordered_keys = [
            key
            for key in ("series", "relations", "recommendations")
            if related.get(key)
        ]
        ordered_keys.extend(
            key
            for key, values in related.items()
            if key not in {*ordered_keys, "seasons", "all_related"} and values
        )
        candidates = [
            (
                key,
                (
                    "Related"
                    if key == "relations"
                    else (
                        values[0].get("series_name") or "Series"
                        if key == "series" and isinstance(values[0], dict)
                        else key.replace("_", " ").title()
                    )
                ),
                values,
            )
            for key in ordered_keys
            if (values := related.get(key) or [])
        ]
    else:
        candidates = [
            (
                "collection"
                if media_type == MediaTypes.MOVIE.value
                and key not in {"recommendations", "similar"}
                else key,
                "Related" if key == "relations" else key.replace("_", " ").title(),
                values,
            )
            for key, values in related.items()
            if key not in {"seasons", "all_related"} and values
        ]

    sections = []
    for key, title, values in candidates:
        items = []
        completion = (
            completion_service.completion_for_payloads(
                user,
                values,
                default_source=source,
                default_media_type=media_type,
            )
            if key in COMPLETABLE_RELATED_SECTION_IDS
            else None
        )
        section_values = values if key in {"relations", "series"} else values[:7]
        for value in section_values:
            payload = value.get("item", value) if isinstance(value, dict) else value
            if not isinstance(payload, dict):
                continue
            item_media_type = payload.get("media_type", media_type)
            item_source = payload.get("source", source)
            summary = media_summary_from_provider(
                payload,
                item_media_type,
                item_source,
                request=request,
                user=user,
            )
            if (
                user
                and getattr(user, "hide_completed_recommendations", False)
                and key == "recommendations"
                and summary.get("user_state", {}).get("status") == "Completed"
            ):
                continue
            items.append(summary)
        if items:
            sections.append({
                "id": key,
                "title": title,
                "items": items,
                "completion": completion,
            })
    return sections


def user_state_for_item(user, item):
    """Return compact viewer-specific state for an item."""
    if not user or not user.is_authenticated or item is None:
        return None
    media_type = item.media_type
    if hasattr(item, "viewer_media"):
        media = item.viewer_media
        list_ids = item.viewer_list_ids
        latest_diary = item.viewer_latest_diary
        diary_count = getattr(latest_diary, "viewer_count", 0)
        has_liked = item.viewer_has_liked
    else:
        media = BasicMedia.objects.filter_media(
            user,
            item.media_id,
            media_type,
            item.source,
            item.season_number,
            item.episode_number,
        ).first()
        list_ids = list(
            CustomList.objects.filter(
                owner=user,
                items=item,
            ).values_list("id", flat=True),
        )
        diary_entries = DiaryEntry.objects.filter(user=user, item=item)
        latest_diary = diary_entries.order_by("-consumed_at", "-id").first()
        diary_count = diary_entries.count()
        has_liked = MediaLike.objects.filter(user=user, item=item).exists()
    uses_half_star = single_weight.uses_half_star_rating(item)
    has_provenance = uses_half_star
    diary_state = {
        "diary_entry_id": latest_diary.id if latest_diary else None,
        "diary_count": diary_count,
        "diary_rating": (
            decimal_string(single_weight.rating_to_wire(latest_diary.rating))
            if uses_half_star and latest_diary and latest_diary.rating is not None
            else decimal_string(latest_diary.rating) if latest_diary else None
        ),
        "diary_consumed_at": (
            single_weight.calendar_date(latest_diary.consumed_at).isoformat()
            if uses_half_star and latest_diary
            else latest_diary.consumed_at if latest_diary else None
        ),
        "has_liked": has_liked,
    }
    if media is None:
        return {"is_tracked": False, "status": None, "rating": None, "in_lists": list_ids, **diary_state}
    state = {
        "is_tracked": True,
        "tracking_id": media.id,
        "status": getattr(media, "status", None),
        "rating": (
            decimal_string(single_weight.rating_to_wire(media.score))
            if uses_half_star and media.score is not None
            else decimal_string(getattr(media, "score", None))
        ),
        "in_lists": list_ids,
        **diary_state,
    }
    if has_provenance:
        state.update(
            {
                "direct_consumption": getattr(media, "direct_consumption", None),
                "rating_source_diary_entry_id": getattr(media, "rating_source_id", None),
                "like_source_diary_entry_id": getattr(media, "like_source_id", None),
                "like_is_independent": getattr(media, "like_is_independent", None),
            },
        )
    if item.media_type == MediaTypes.BOOK.value:
        from app import book_tracking

        state["book"] = book_tracking.state_payload(media)
    if media.item.media_type == MediaTypes.GAME.value:
        from app import game_tracking

        state["game"] = game_tracking.state_payload(media)
    return state


def decimal_string(value):
    """Serialize decimals as stable strings."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def progress_for_media(media):  # noqa: PLR0911 - media kinds are intentionally explicit
    """Return typed progress for a media instance."""
    media_type = media.item.media_type
    max_progress = getattr(media, "max_progress", None)
    value = getattr(media, "progress", 0)
    if media_type in (MediaTypes.MOVIE.value, MediaTypes.MUSIC.value):
        is_complete = media.status == Status.COMPLETED.value
        return {
            "kind": "binary",
            "value": 1 if is_complete else 0,
            "max": 1,
            "unit": "album" if media_type == MediaTypes.MUSIC.value else "movie",
        }
    if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        return {"kind": "episodes", "value": value, "max": max_progress, "unit": "episode"}
    if media_type == MediaTypes.GAME.value:
        session = media.current_session
        if session and session.percentage is not None:
            return {"kind": "percentage", "value": session.percentage, "max": 100, "unit": "percent"}
        return {"kind": "minutes", "value": session.total_minutes if session else None, "max": None, "unit": "minute"}
    if media_type == MediaTypes.BOOK.value:
        snapshot = getattr(media, "progress_snapshot", None)
        if snapshot and snapshot.has_percentage and not snapshot.has_pages:
            return {"kind": "percentage", "value": snapshot.percentage, "max": 100, "unit": "percent"}
        if not snapshot and media.status == Status.COMPLETED.value:
            if media.item.total_pages:
                return {
                    "kind": "pages",
                    "value": media.item.total_pages,
                    "max": media.item.total_pages,
                    "unit": "page",
                }
            return {"kind": "percentage", "value": 100, "max": 100, "unit": "percent"}
        return {
            "kind": "pages",
            "value": snapshot.pages if snapshot and snapshot.has_pages else value,
            "max": media.item.total_pages,
            "unit": "page",
        }
    return {
        "kind": "count",
        "value": value,
        "max": max_progress,
        "unit": config.get_unit(media_type, short=False).lower()
        if config.get_config(media_type) and config.get_config(media_type).get("unit")
        else media_type,
    }


def progress_change_payload(change):
    """Serialize a progress delta."""
    if change is None:
        return None
    return {
        "id": change.id,
        "previous": change.previous_progress,
        "current": change.current_progress,
        "created_at": change.created_at,
    }


def latest_progress_change_for(media):
    """Return the newest recorded progress delta for this user and item."""
    if hasattr(media, "viewer_latest_progress_change"):
        return media.viewer_latest_progress_change
    return (
        ProgressChange.objects.filter(actor=media.user, item=media.item)
        .order_by("-created_at", "-id")
        .first()
    )


def tracking_state(media):
    """Serialize any tracked media model into TrackingState."""
    uses_half_star = single_weight.uses_half_star_rating(media.item)
    state = {
        "tracking_id": media.id,
        "status": getattr(media, "status", None),
        "rating": (
            decimal_string(single_weight.rating_to_wire(media.score))
            if uses_half_star and media.score is not None
            else decimal_string(getattr(media, "score", None))
        ),
        "progress": progress_for_media(media),
        "repeats": getattr(media, "repeats", 1),
        "start_date": getattr(media, "start_date", None),
        "end_date": (
            single_weight.calendar_date(media.end_date).isoformat()
            if uses_half_star and media.end_date
            else getattr(media, "end_date", None)
        ),
        "notes": getattr(media, "notes", ""),
        "updated_at": getattr(media, "progressed_at", None) or getattr(media, "created_at", None),
        "latest_progress_change": progress_change_payload(latest_progress_change_for(media)),
    }
    if uses_half_star:
        primed = hasattr(media.item, "viewer_has_liked")
        state.update(
            {
                "liked": (
                    media.item.viewer_has_liked
                    if primed
                    else MediaLike.objects.filter(user=media.user, item=media.item).exists()
                ),
                "direct_consumption": getattr(media, "direct_consumption", None),
                "rating_source_diary_entry_id": getattr(media, "rating_source_id", None),
                "like_source_diary_entry_id": getattr(media, "like_source_id", None),
                "like_is_independent": getattr(media, "like_is_independent", None),
                "diary_count": (
                    media.item.viewer_diary_count
                    if primed
                    else DiaryEntry.objects.filter(
                        user=media.user,
                        item=media.item,
                    ).count()
                ),
            },
        )
    if media.item.media_type == MediaTypes.BOOK.value:
        from app import book_tracking

        state["book"] = book_tracking.state_payload(media)
    if media.item.media_type == MediaTypes.GAME.value:
        from app import game_tracking

        state["game"] = game_tracking.state_payload(media)
    return state


class UserSummarySerializer(serializers.Serializer):
    """Compact public user summary."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    display_name = serializers.CharField()
    avatar_url = serializers.CharField(allow_null=True)


def user_summary(user, request=None):
    """Serialize a user for nested responses."""
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "avatar_url": image_url(request, user.profile_picture) if user.profile_picture else None,
    }
