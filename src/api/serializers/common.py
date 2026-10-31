import logging
from decimal import Decimal

from django.conf import settings
from django.db.models import (
    Count,
    F,
    Max,
    Min,
    Prefetch,
    Q,
    Window,
    prefetch_related_objects,
)
from django.db.models.functions import RowNumber
from django.utils.text import slugify
from rest_framework import serializers

from api.services import completion as completion_service
from app import config, music_catalog, single_weight, tv_tracking
from app.curation import (
    curated_image_url,
    music_picks_apply,
    picked_image_urls,
    prime_curated_artwork,
)
from app.models import (
    TV,
    BasicMedia,
    CustomBackdropPreference,
    CustomPosterPreference,
    DiaryEntry,
    Episode,
    Item,
    MediaLike,
    MediaTypes,
    Music,
    Season,
    Sources,
    Status,
)
from lists.models import CustomList, CustomListItem
from social.models import ProgressChange

logger = logging.getLogger(__name__)


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
    return with_curated_artwork(
        artwork_from_payload({"image": item.image}, item.media_type, request=request),
        item,
        request=request,
    )


def saved_poster_url(item, request=None):
    """Return the poster saved on an Item as list cards show it, else None."""
    if item is None or not item.image or item.image == settings.IMG_NONE:
        return None
    return artwork_from_payload({"image": item.image}, item.media_type, request=request)["poster_url"]


def saved_still_url(item, request=None):
    """Return the still saved on an episode Item as list cards show it, else None."""
    if item is None or not item.image or item.image == settings.IMG_NONE:
        return None
    return artwork_from_payload({"image": item.image}, item.media_type, request=request)["backdrop_url"]


def with_curated_artwork(artwork, item, request=None):
    """Make an admin's poster and backdrop picks the default artwork."""
    if item is None:
        return artwork
    poster = curated_image_url(CustomPosterPreference, item)
    if poster and artwork["poster_url"] is not None:
        poster = absolute_url(request, poster)
        artwork = {**artwork, "image_url": poster, "poster_url": poster}
    backdrop = curated_image_url(CustomBackdropPreference, item)
    if backdrop:
        artwork = {**artwork, "backdrop_url": absolute_url(request, backdrop)}
    return artwork


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
    """Find an Item for a media ref if it has already been materialized.

    A music album is found under its canonical ref, so an old MusicBrainz ref of an
    album that moved finds the Item that holds it now.
    """
    ref = music_catalog.canonical_media_ref(ref)
    return Item.objects.filter(
        source=ref["source"],
        media_type=ref["media_type"],
        media_id=ref["media_id"],
        season_number=ref.get("season_number"),
        episode_number=ref.get("episode_number"),
    ).first()


def get_or_create_item_from_metadata(ref, metadata, *, persist_series=True):
    """Get or create an Item using provider metadata.

    A music album is stored under its canonical ref. persist_series=False leaves the
    metadata's series for the caller to store.
    """
    from api.services.item_metadata import store_item_metadata

    ref = music_catalog.canonical_media_ref(ref)
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
    store_item_metadata(item, metadata, persist_series=persist_series)
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
    prime_curated_artwork(items)
    if not items or not user or not user.is_authenticated:
        return

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
        .exclude(current_progress__kind=ProgressChange.START)
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
    curated_backdrop_url = curated_image_url(CustomBackdropPreference, item)
    if curated_backdrop_url:
        return absolute_url(request, curated_backdrop_url), None

    if item.media_type not in [
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.GAME.value,
        MediaTypes.ANIME.value,
        MediaTypes.MANGA.value,
    ]:
        return None, custom_backdrop_url

    from api.services import media as media_service
    from app.providers import services as provider_services

    metadata = provider_services.get_media_metadata(item.media_type, item.media_id, item.source)
    # Anime and manga pages take their backdrop from AniList's banner; match them.
    if item.media_type == MediaTypes.ANIME.value:
        metadata = media_service._enrich_anime_metadata(metadata, item.source)
    elif item.media_type == MediaTypes.MANGA.value:
        metadata = media_service._enrich_manga_metadata(metadata, item.source)
    return media_service.resolved_backdrop_urls(
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
# An album page's related sections, in the order the app shows them, and their titles.
MUSIC_RELATED_SECTIONS = (
    ("other_versions", "Other Versions"),
    ("more_by_artist", "More by {artist}"),
    ("related_albums", "You Might Also Like"),
)
MUSIC_RELATED_LIMIT = 10
# The optional fields of the app's MediaSummary, by the type it decodes them as.
SUMMARY_TEXT_FIELDS = (
    "relation",
    "content_rating",
    "subtitle",
    "overview",
    "image_url",
    "poster_url",
    "custom_poster_url",
    "backdrop_url",
    "custom_backdrop_url",
    "poster_accent_color",
    "release_date",
    "default_source",
)
SUMMARY_TEXT_LIST_FIELDS = ("genres", "languages", "roles", "credit_roles")
POSTER_ORIENTATIONS = (None, "portrait", "landscape", "square", "unknown")


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
    artwork = artwork_from_payload(payload, media_type, request=request)
    saved_poster = saved_poster_url(item, request=request)
    if saved_poster:
        # Match list cards: the saved poster, not the provider's current one.
        artwork = {**artwork, "image_url": saved_poster, "poster_url": saved_poster}
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
        # "explicit" or "clean", for Apple Music albums that Apple rates.
        **(
            {"content_rating": payload["content_rating"]}
            if payload.get("content_rating")
            else {}
        ),
        "subtitle": payload.get("year") or payload.get("subtitle"),
        "overview": synopsis_from_payload(payload),
        **with_curated_artwork(artwork, item, request=request),
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


def seasons_from_metadata(metadata, request=None, user=None, *, tv_seasons=None):
    """Normalize TV seasons into the native season summary shape.

    Specials are listed last (TV-004). tv_seasons maps season numbers to the viewer's
    card TVSeasonState (tv_tracking.season_card_states): each season then carries it as
    `tv`, and its completion is watched over aired episodes (TV-104), or null when the
    season's episode list isn't known.
    """
    seasons = (metadata.get("related") or {}).get("seasons") or []
    seasons = [season for season in seasons if isinstance(season, dict)]
    seasons = sorted(seasons, key=lambda season: season.get("season_number") == 0)
    tracked = {}
    if user and user.is_authenticated and tv_seasons is None:
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
    season_items = list(
        Item.objects.filter(
            source=metadata.get("source"),
            media_type=MediaTypes.SEASON.value,
            media_id=str(metadata.get("media_id") or ""),
            season_number__in=[value.get("season_number") for value in seasons if isinstance(value, dict)],
        ),
    )
    # A pick wins; otherwise a season shows the poster saved on it, as list cards do.
    season_posters = {
        **{
            item.season_number: poster
            for item in season_items
            if (poster := saved_poster_url(item, request=request))
        },
        **picked_images_by_number(season_items, CustomPosterPreference, user, "season_number"),
    }
    summaries = []
    for season in seasons:
        number = season.get("season_number")
        if tv_seasons is not None:
            completion = _tv_card_completion(tv_seasons.get(number))
        elif user and user.is_authenticated:
            completion = completion_service.completion_payload(
                len({episode.item.episode_number for episode in tracked[number].episodes.all()})
                if number in tracked
                else 0,
                _season_episode_count(season),
            )
        else:
            completion = None
        summary = {
            "season_number": number,
            "title": season.get("season_title") or season.get("title") or season.get("name") or "",
            "episode_count": _season_episode_count(season),
            "image_url": absolute_url(request, season_posters[number])
            if number in season_posters
            else image_url(request, season.get("image") or season.get("poster_path"))
            if (season.get("image") or season.get("poster_path"))
            else None,
            "release_date": season.get("first_air_date") or season.get("air_date") or season.get("release_date"),
            "completion": completion,
        }
        if tv_seasons is not None:
            summary["tv"] = tv_seasons.get(number)
        summaries.append(summary)
    return summaries


def _tv_card_completion(card):
    """Return a season card's completion: watched over aired episodes (TV-104)."""
    if not card or card.get("watched_count") is None or card.get("aired_count") is None:
        return None
    return completion_service.completion_payload(card["watched_count"], card["aired_count"])


def picked_images_by_number(items, model, user, number_field):
    """Map season or episode numbers to the viewer's own pick, else an admin's."""
    items = list(items)
    picks = picked_image_urls(items, model, user)
    return {getattr(item, number_field): picks[item.pk] for item in items if item.pk in picks}


def episode_stills_by_number(items, user, request=None):
    """Map episode numbers to a viewer pick, else an admin's, else the saved still."""
    from app.providers import tmdb

    items = list(items)
    return {
        # Rows are thumbnails, so saved TMDB stills load at w500 like live ones.
        **{
            item.episode_number: tmdb.sized_image_url(still, "w500")
            for item in items
            if (still := saved_still_url(item, request=request))
        },
        **picked_images_by_number(items, CustomBackdropPreference, user, "episode_number"),
    }


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


def episodes_from_metadata(metadata, request=None, picked_stills=None, *, tv_episodes=None):
    """Normalize season episodes into the native episode summary shape.

    tv_episodes maps episode numbers to the viewer's TVEpisodeState
    (tv_tracking.episode_states); each episode then carries it as `tv`.
    """
    episodes = metadata.get("episodes") or []
    picked_stills = picked_stills or {}
    return [
        {
            "episode_number": episode.get("episode_number"),
            "title": episode.get("title") or episode.get("name") or "",
            "overview": episode.get("overview"),
            "air_date": episode.get("air_date"),
            "runtime": episode.get("runtime"),
            "image_url": absolute_url(request, picked_stills[episode.get("episode_number")])
            if episode.get("episode_number") in picked_stills
            else image_url(request, episode.get("image") or episode.get("still_path"))
            if (episode.get("image") or episode.get("still_path"))
            else None,
            "image_role": "still",
            "rating": str(episode.get("vote_average")) if episode.get("vote_average") is not None else episode.get("rating"),
            **({"tv": tv_episodes.get(episode.get("episode_number"))} if tv_episodes is not None else {}),
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
    if item is None or not music_picks_apply(item):
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
    if item is None or not music_picks_apply(item):
        return None
    prefetched = getattr(item, "viewer_custom_backdrop_preferences", None)
    preference = prefetched[0] if prefetched else None
    if prefetched is None:
        preference = CustomBackdropPreference.objects.filter(user=user, item=item).first()
    return absolute_url(request, preference.custom_image_url) if preference else None


def related_sections_from_payload(related, media_type, source, request=None, user=None, metadata=None):  # noqa: C901
    """Normalize provider related media into mobile section cards.

    ``metadata`` is the page's own; it names the artist of an album's More by section.
    """
    if not related:
        return []

    if media_type == MediaTypes.MUSIC.value:
        return _music_related_sections(related, source, metadata=metadata or {}, request=request, user=user)
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

    # One lookup for every section, so albums that moved open under their canonical ref.
    payloads = [
        value.get("item", value) if isinstance(value, dict) else value
        for _, _, values in candidates
        for value in values
    ]
    canonical = music_catalog.canonical_music_refs(
        music_catalog.payload_ref(payload, source=source, media_type=media_type)
        for payload in payloads
        if isinstance(payload, dict)
    )
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
            payload = music_catalog.with_canonical_ref(payload, canonical, source=source, media_type=media_type)
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


def _music_related_sections(related, source, *, metadata, request, user):
    """Return an album page's Other Versions, More by and You Might Also Like sections.

    At most 10 albums each, under their canonical refs, with their Items found in one
    query. An album the app couldn't decode is left out: one bad card would break the
    whole page, which the app decodes strictly.
    """
    artist = next(
        (
            credit["name"]
            for credit in (metadata.get("music") or {}).get("artist_credit") or []
            if isinstance(credit, dict) and credit.get("name")
        ),
        None,
    )
    candidates = [
        (key, title.format(artist=artist or "This Artist"), values[:MUSIC_RELATED_LIMIT])
        for key, title in MUSIC_RELATED_SECTIONS
        if (values := [value for value in related.get(key) or [] if isinstance(value, dict)])
    ]
    music = MediaTypes.MUSIC.value
    canonical = music_catalog.canonical_music_refs(
        music_catalog.payload_ref(value, source=source, media_type=music)
        for _, _, values in candidates
        for value in values
    )
    items = _music_items(set(canonical.values()), user)
    sections = []
    for key, title, values in candidates:
        summaries = []
        refs = set()
        for value in values:
            payload = music_catalog.with_canonical_ref(value, canonical, source=source, media_type=music)
            ref = music_catalog.payload_ref(payload, source=source, media_type=music)
            if ref in refs:
                continue
            summary = _music_related_summary(payload, ref, items.get(ref), request=request, user=user)
            if summary is not None:
                refs.add(ref)
                summaries.append(summary)
        if summaries:
            sections.append({"id": key, "title": title, "items": summaries, "completion": None})
    return sections


def _music_items(refs, user):
    """Return the Items of these album refs, primed with the viewer's state, by ref."""
    ids_by_source = {}
    for source, media_type, media_id in refs:
        if media_type == MediaTypes.MUSIC.value and source and media_id:
            ids_by_source.setdefault(source, set()).add(media_id)
    if not ids_by_source:
        return {}
    query = Q(pk__in=[])
    for source, media_ids in ids_by_source.items():
        query |= Q(source=source, media_id__in=media_ids)
    items = list(
        Item.objects.filter(
            query,
            media_type=MediaTypes.MUSIC.value,
            season_number=None,
            episode_number=None,
        ),
    )
    media_by_item = (
        {music.item_id: music for music in Music.objects.filter(user=user, item__in=items).select_related("item")}
        if items and user and user.is_authenticated
        else None
    )
    prime_collection_items(items, user, media_by_item=media_by_item)
    return {(item.source, item.media_type, item.media_id): item for item in items}


def music_summaries_from_provider(payloads, source, request=None, user=None):
    """Serialize a page of albums with their Items and the viewer's state.

    The same summaries as ``media_summary_from_provider`` one album at a time, read in
    a fixed number of queries however many albums the page has.
    """
    refs = [music_catalog.payload_ref(payload, source=source, media_type=MediaTypes.MUSIC.value) for payload in payloads]
    items = _music_items(set(refs), user)
    return [
        media_summary_from_provider(payload, ref[1], ref[0], request=request, user=user, item=items.get(ref))
        for payload, ref in zip(payloads, refs, strict=True)
    ]


def _music_related_summary(payload, ref, item, *, request, user):
    """Return a related album's summary, or None when the app couldn't decode it."""
    if not all(isinstance(part, str) and part for part in ref):
        return None
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    try:
        summary = media_summary_from_provider(payload, ref[1], ref[0], request=request, user=user, item=item)
    except (AttributeError, KeyError, TypeError, ValueError):
        logger.warning("Left out a related album that couldn't be read: %s", ref)
        return None
    if not _decodable_summary(summary):
        logger.warning("Left out a related album the app couldn't decode: %s", ref)
        return None
    return summary


def _decodable_summary(summary):
    """Return whether the app's strict ``MediaSummary`` decoder accepts a summary."""

    def text(value, *, required=False):
        return isinstance(value, str) and bool(value) if required else value is None or isinstance(value, str)

    def whole(value):
        return value is None or (isinstance(value, int) and not isinstance(value, bool))

    ref = summary.get("ref")
    if not isinstance(ref, dict):
        return False
    return (
        all(text(ref.get(key), required=True) for key in ("source", "media_type", "media_id"))
        and all(whole(ref.get(key)) for key in ("item_id", "season_number", "episode_number"))
        and text(summary.get("title"), required=True)
        and all(text(summary.get(key)) for key in SUMMARY_TEXT_FIELDS)
        and all(
            isinstance(summary.get(key) or [], list) and all(isinstance(value, str) for value in summary.get(key) or [])
            for key in SUMMARY_TEXT_LIST_FIELDS
        )
        and all(whole(summary.get(key)) for key in ("poster_width", "poster_height", "position"))
        and (
            summary.get("poster_aspect_ratio") is None
            or (
                isinstance(summary["poster_aspect_ratio"], int | float)
                and not isinstance(summary["poster_aspect_ratio"], bool)
            )
        )
        and summary.get("poster_orientation") in POSTER_ORIENTATIONS
        and (summary.get("user_state") is None or isinstance(summary["user_state"], dict))
    )


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
        "diary_rating": single_weight.rating_to_api(latest_diary.rating, item) if latest_diary else None,
        "diary_consumed_at": (
            single_weight.calendar_date(latest_diary.consumed_at).isoformat()
            if uses_half_star and latest_diary
            else latest_diary.consumed_at if latest_diary else None
        ),
        "has_liked": has_liked,
    }
    if media is None:
        state = {"is_tracked": False, "status": None, "rating": None, "in_lists": list_ids, **diary_state}
        if media_type == MediaTypes.TV.value:
            state["tv"] = tv_tracking.list_payload(None)
        return state
    if media_type == MediaTypes.SEASON.value and not tv_season_started(media):
        # A season that isn't started is untracked whatever it stores; its stars and
        # heart still show (TV-205, TV-900).
        return {
            "is_tracked": False,
            "status": None,
            "rating": single_weight.rating_to_api(media.score, item),
            "in_lists": list_ids,
            **diary_state,
        }
    state = {
        "is_tracked": True,
        "tracking_id": media.id,
        "status": getattr(media, "status", None),
        "rating": single_weight.rating_to_api(getattr(media, "score", None), item),
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
    if media_type == MediaTypes.TV.value:
        # The list variant, from the TV row's stored values only (plan 2.3.1). Detail
        # pages and TV like responses replace it with the detail variant.
        state["tv"] = tv_tracking.list_payload(media)
    return state


def tv_season_started(season):
    """Return whether a Season row is started: it has a viewing, log or mark (TV-205).

    A worked-out row stores it in is_started. A row not yet worked out counts as started
    when it has a viewing, or is stored Completed (the legacy rule's mark, TV-801).
    """
    if season.is_started is not None:
        return season.is_started
    if season.status == Status.COMPLETED.value:
        return True
    prefetched = getattr(season, "_prefetched_objects_cache", {})
    if "episodes" in prefetched:
        return bool(prefetched["episodes"])
    return season.episodes.exists()


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
    if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        # Watched over aired regular episodes in the current pass (TV-104, TV-900).
        # Checked first: TV's progress properties load every episode (A-47).
        return tv_tracking.progress_payload(media)
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
    # Episodes have no user of their own; they belong to the season's user.
    user = media.related_season.user if media.item.media_type == MediaTypes.EPISODE.value else media.user
    return (
        ProgressChange.objects.filter(actor=user, item=media.item)
        .exclude(current_progress__kind=ProgressChange.START)
        .order_by("-created_at", "-id")
        .first()
    )


def _tv_updated_at(media):
    """Return a TV row's updated_at: its last activity (TV-701), else the fallbacks."""
    return (
        getattr(media, "last_activity_at", None)
        or getattr(media, "progressed_at", None)
        or getattr(media, "created_at", None)
    )


def _tv_viewing_dates(media):
    """Return a TV or Season row's (start_date, end_date) in one query (A-47).

    The same values as the models' properties, which read every season's episodes;
    rows whose seasons or episodes are prefetched read the prefetch instead, and the TV
    list's rows read its two annotations.
    """
    prefetched = getattr(media, "_prefetched_objects_cache", {})
    if isinstance(media, TV):
        if hasattr(media, "tv_first_viewing_end"):
            # The TV list annotates both (api.views.tracking._tv_viewing_dates).
            return media.tv_first_viewing_end, media.tv_last_viewing_end
        if "seasons" in prefetched:
            return media.start_date, media.end_date
        viewings = Episode.objects.filter(related_season__related_tv=media).exclude(
            related_season__item__season_number=0,
        )
    else:
        if "episodes" in prefetched:
            return media.start_date, media.end_date
        viewings = Episode.objects.filter(related_season=media)
    dates = viewings.filter(end_date__isnull=False).aggregate(start=Min("end_date"), end=Max("end_date"))
    return dates["start"], dates["end"]


def tracking_state(media, *, tv=None, tv_detail=False):
    """Serialize any tracked media model into TrackingState.

    TV shows, seasons and episodes add `tv`: the given TVShowState, the detail variant
    for that page (tv_detail), or a show row's list variant.
    """
    uses_half_star = single_weight.uses_half_star_rating(media.item)
    if isinstance(media, (TV, Season)):
        start_date, end_date = _tv_viewing_dates(media)
    else:
        start_date = getattr(media, "start_date", None)
        end_date = getattr(media, "end_date", None)
    state = {
        "tracking_id": media.id,
        "status": getattr(media, "status", None),
        "rating": single_weight.rating_to_api(getattr(media, "score", None), media.item),
        "progress": progress_for_media(media),
        "repeats": getattr(media, "repeats", 1),
        "start_date": start_date,
        "end_date": (
            single_weight.calendar_date(end_date).isoformat()
            if uses_half_star and end_date
            else end_date
        ),
        "notes": getattr(media, "notes", ""),
        "updated_at": (
            _tv_updated_at(media)
            if isinstance(media, TV)
            else getattr(media, "progressed_at", None) or getattr(media, "created_at", None)
        ),
        "latest_progress_change": progress_change_payload(latest_progress_change_for(media)),
    }
    if tv_tracking.supports(media.item):
        if isinstance(media, Season) and not tv_season_started(media):
            state["status"] = None
        if tv is not None:
            state["tv"] = tv
        elif tv_detail:
            owner = media.related_season.user if media.item.media_type == MediaTypes.EPISODE.value else media.user
            state["tv"] = tv_tracking.state_payload(owner, media.item)
        elif isinstance(media, TV):
            state["tv"] = tv_tracking.list_payload(media)
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
