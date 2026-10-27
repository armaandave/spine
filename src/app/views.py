import logging
from pathlib import Path
from uuid import uuid4

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import prefetch_related_objects
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse, Http404
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import datetime
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from app import config, exposure, game_tracking, helpers, history_processor, music_catalog, single_weight
from app import statistics as stats
from app import tv_listing, tv_tracking, user_calendar
from app.book_tracking import BookTrackingConflict
from app.forms import EpisodeForm, ManualItemForm, get_form_class, BookProgressForm, BookLogForm, BookStartReadingForm, manual_parent_seasons
from app.models import TV, BasicMedia, Item, MediaTypes, Season, Sources, Status, Movie, Episode, Book, BookSession, UserMessage, UserMessageLevel

from app.providers import igdb, manual, mdblist, services, tmdb
from app.templatetags import app_tags
from users.models import HomeSortChoices, MediaSortChoices, MediaStatusChoices, User
from app.curation import curated_image_url, is_curator
from app.forms import DiaryEntryForm
from app.models import DiaryEntry, MediaLike
from app.utils.color import (
    build_accent_palette,
    compute_and_store_poster_accent,
    get_poster_accent_from_url,
)

logger = logging.getLogger(__name__)


@require_GET
def home(request):
    """Home page with media items in progress."""
    media_type_to_load = request.GET.get("load_media_type")
    exposure.require_media_type(media_type_to_load)
    items_limit = 14

    list_by_type = BasicMedia.objects.get_home_status(
        user=request.user,
        status=Status.IN_PROGRESS.value,
        sort_by=HomeSortChoices.COMPLETION,
        items_limit=items_limit,
        specific_media_type=media_type_to_load,
    )
    list_by_type = {
        media_type: value
        for media_type, value in list_by_type.items()
        if media_type in exposure.media_types()
    }

    # If this is an HTMX request to load more items for a specific media type
    if request.headers.get("HX-Request") and media_type_to_load:
        context = {
            "media_list": list_by_type.get(media_type_to_load, []),
            "home_status": Status.IN_PROGRESS.value,
        }
        return render(request, "app/components/home_grid.html", context)

    context = {
        "list_by_type": list_by_type,
        "items_limit": items_limit,
    }
    return render(request, "app/home.html", context)


@require_POST
def progress_edit(request, media_type, instance_id):
    """Increase or decrease the progress of a media item from home page."""
    exposure.require_media_type(media_type)
    # Games and binary album tracking don't support progress updates.
    if media_type in [MediaTypes.GAME.value, MediaTypes.MUSIC.value]:
        return HttpResponseBadRequest(f"{MediaTypes(media_type).label} does not support progress updates")
    
    operation = request.POST["operation"]

    media = helpers.get_owned_media_or_404(
        request,
        media_type,
        instance_id,
        prefetch=True,
    )

    if tv_tracking.supports(media_type):
        # TV progress is counted from viewings: increase ticks up next, decrease
        # unticks the latest watched episode (TV-100, TV-102, TV-104).
        season_number = media.item.season_number if media_type == MediaTypes.SEASON.value else None
        try:
            show_item = tv_listing.show_item_for(media.item)
            target = _tv_progress_target(
                request.user, show_item, season_number, increase=operation == "increase",
            )
            if target is not None and operation == "increase":
                before = _tv_finished(request.user, show_item, target[0])
                tv_tracking.tick(request.user, show_item, *target, entry_point=tv_tracking.WEB)
                _tv_finish_messages(request.user, show_item, target[0], before)
            elif target is not None and operation == "decrease":
                tv_tracking.untick(request.user, show_item, *target, entry_point=tv_tracking.WEB)
        except TV_WRITE_ERRORS as error:
            return _tv_error_response(error)
    elif operation == "increase":
        media.increase_progress()
    elif operation == "decrease":
        media.decrease_progress()

    if media_type == MediaTypes.SEASON.value:
        # clear prefetch cache to get the updated episodes; an untick can remove a
        # season that no longer holds anything (TV-205)
        try:
            media.refresh_from_db()
        except Season.DoesNotExist:
            getattr(media, "_prefetched_objects_cache", {}).pop("episodes", None)
        prefetch_related_objects([media], "episodes")

    context = {
        "media": media,
    }
    return render(
        request,
        "app/components/progress_changer.html",
        context,
    )


@login_not_required
@require_GET
def media_list(request, media_type, username=None):
    """Return the media list page."""
    exposure.require_media_type(media_type)
    target_user = get_object_or_404(User, username=username) if username else request.user

    if request.user == target_user:
        layout = target_user.update_preference(
            f"{media_type}_layout",
            request.GET.get("layout"),
        )
        sort_filter = target_user.update_preference(
            f"{media_type}_sort",
            request.GET.get("sort"),
        )
        status_filter = target_user.update_preference(
            f"{media_type}_status",
            request.GET.get("status"),
        )
    else:
        if target_user.profile_private:
            raise Http404("User not found")

        enabled_media_types = exposure.filter_media_types(
            target_user.get_enabled_media_types(),
        )
        if not enabled_media_types:
            raise Http404("User doesn't have any media types enabled")

        if media_type not in enabled_media_types:
            return redirect(
                "medialist",
                username=target_user.username,
                media_type=enabled_media_types[0],
            )

        layout = target_user.get_valid_preference(
            f"{media_type}_layout",
            request.GET.get("layout"),
        )
        sort_filter = target_user.get_valid_preference(
            f"{media_type}_sort",
            request.GET.get("sort"),
        )
        status_filter = target_user.get_valid_preference(
            f"{media_type}_status",
            request.GET.get("status"),
        )
    search_query = request.GET.get("search", "")
    page = request.GET.get("page", 1)

    # Prepare status filter for database query
    if not status_filter:
        status_filter = MediaStatusChoices.ALL

    # Get media list with filters applied
    media_queryset = BasicMedia.objects.get_media_list(
        user=target_user,
        media_type=media_type,
        status_filter=status_filter,
        sort_filter=sort_filter,
        search=search_query,
    )

    # Paginate results
    items_per_page = 32
    paginator = Paginator(media_queryset, items_per_page)
    media_page = paginator.get_page(page)

    BasicMedia.objects.annotate_max_progress(
        media_page.object_list,
        media_type,
    )

    status_choices = MediaStatusChoices.choices
    if media_type == MediaTypes.MUSIC.value:
        music_labels = {
            Status.IN_PROGRESS.value: "Listening",
            Status.COMPLETED.value: "Listened",
        }
        status_choices = [
            (value, music_labels.get(value, label))
            for value, label in status_choices
        ]

    context = {
        "media_type": media_type,
        "media_type_plural": app_tags.media_type_readable_plural(media_type).lower(),
        "media_list": media_page,
        "current_layout": layout,
        "layout_class": ".media-grid" if layout == "grid" else "tbody",
        "current_sort": sort_filter,
        "current_status": status_filter,
        "sort_choices": MediaSortChoices.choices,
        "status_choices": status_choices,
        "target_user": target_user,
    }

    # Handle HTMX requests for partial updates
    if request.headers.get("HX-Request"):
        # Changing from empty list to a status with items
        if request.headers.get("HX-Target") == "empty_list":
            response = HttpResponse()
            if username:
                response["HX-Redirect"] = reverse(
                    "medialist",
                    args=[target_user.username, media_type],
                )
            else:
                response["HX-Redirect"] = reverse("medialist", args=[media_type])
            return response
        if layout == "grid":
            template_name = "app/components/media_grid_items.html"
        else:
            template_name = "app/components/media_table_items.html"
    else:
        template_name = "app/media_list.html"

    return render(request, template_name, context)


@require_GET
def media_search(request):
    """Return the media search page."""
    exposure.require_media_type(request.GET["media_type"])
    media_type = request.user.update_preference(
        "last_search_type",
        request.GET["media_type"],
    )
    query = request.GET["q"]
    page = int(request.GET.get("page", 1))
    layout = request.GET.get("layout", "grid")

    # only receives source when searching with secondary source
    source = request.GET.get(
        "source",
        config.get_default_source_name(media_type).value,
    )

    data = services.search(media_type, query, page, source)

    # Enrich search results with user tracking data
    if data.get("results"):
        data["results"] = helpers.enrich_items_with_user_data(
            request,
            data["results"],
            "search",
        )

    context = {
        "data": data,
        "source": source,
        "media_type": media_type,
        "layout": layout,
    }

    return render(request, "app/search.html", context)


@require_GET
def media_details(request, source, media_type, media_id, title):
    """Return the details page for a media item."""
    exposure.require_media_type(media_type)
    # An old link to an album that moved (/details/musicbrainz/music/<mbid>/...) opens
    # the album where it is stored now.
    canonical_source, _, canonical_id = music_catalog.canonical_music_ref(source, media_type, media_id)
    if (canonical_source, canonical_id) != (source, media_id):
        url = reverse(
            "media_details",
            kwargs={
                "source": canonical_source,
                "media_type": media_type,
                "media_id": canonical_id,
                "title": title,
            },
        )
        return redirect(f"{url}?{request.GET.urlencode()}" if request.GET else url)
    media_metadata = services.get_media_metadata(media_type, media_id, source)
    
    if not media_metadata:
        logger.error("No metadata returned for %s %s from %s", media_type, media_id, source)
        # Return a 404 or error page
        from django.http import Http404
        raise Http404("Media not found")

    poster_accent = None
    accent_item = Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=media_type,
    ).first()
    if accent_item:
        poster_accent = compute_and_store_poster_accent(
            accent_item,
            poster_url=media_metadata.get("image") if media_metadata else None,
        )
    else:
        poster_accent = get_poster_accent_from_url(media_metadata.get("image") if media_metadata else None)

    accent_palette = build_accent_palette(poster_accent)
    poster_accent = accent_palette["accent"]
    poster_accent_contrast = accent_palette["contrast"]

    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        media_type,
        source,
    )
    current_instance = user_medias[0] if user_medias else None

    if current_instance is not None:
        helpers.refresh_item_image_if_missing(
            current_instance.item,
            media_metadata.get("image"),
        )

    # Get diary entries for media types with detail-page logging actions.
    diary_entries = []
    if media_type in [
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.BOOK.value,
        MediaTypes.GAME.value,
        MediaTypes.MUSIC.value,
    ]:
        try:
            item = Item.objects.get(source=source, media_type=media_type, media_id=media_id)
            diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).prefetch_related('tags').order_by('-consumed_at')
        except Item.DoesNotExist:
            pass

    # Get MDBList ratings for movies and TV shows from TMDB
    mdblist_ratings = None
    if source == Sources.TMDB.value and media_type in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        try:
            mdblist_ratings = mdblist.get_media_ratings(media_id, media_type)
            logging.getLogger(__name__).info(f"MDBList ratings for {media_type} {media_id}: {mdblist_ratings}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to fetch MDBList ratings for {media_type} {media_id}: {e}")

    # Check if there are any seasons in progress for TV shows
    has_seasons_in_progress = False
    if media_type == MediaTypes.TV.value and current_instance:
        # Use prefetched seasons to avoid N+1 query
        has_seasons_in_progress = any(season.status == Status.IN_PROGRESS.value for season in current_instance.seasons.all())

    # Enrich related items with user tracking data
    if media_metadata.get("related"):
        for section_name, related_items in media_metadata["related"].items():
            if related_items:
                media_metadata["related"][section_name] = (
                    helpers.enrich_items_with_user_data(
                        request,
                        related_items,
                        section_name,
                    )
                )

    if media_type in [MediaTypes.TV.value, MediaTypes.MOVIE.value]:
        watch_providers = tmdb.filter_providers(
            media_metadata.get("providers"),
            request.user.watch_provider_region,
        )
    else:
        watch_providers = None

    context = {
        "media": media_metadata,
        "media_type": media_type,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "diary_entries": diary_entries,
        "mdblist_ratings": mdblist_ratings,
        "poster_accent_color": poster_accent,
        "poster_accent_contrast": poster_accent_contrast,
        "has_seasons_in_progress": has_seasons_in_progress,
        "watch_providers": watch_providers,
        "watch_provider_region": request.user.watch_provider_region,
    }
    return render(request, "app/media_details.html", context)


@require_GET
def person_detail(request, source, person_id, slug):  # noqa: ARG001 slug for URL
    """Return the person page for an actor, crew member, or author."""
    if source not in (Sources.TMDB.value, Sources.OPENLIBRARY.value):
        raise Http404("Person not found")

    try:
        person = services.get_person_page(source, person_id)
    except services.ProviderAPIError:
        raise Http404("Person not found")

    return render(request, "app/person.html", {"person": person})


@require_GET
def season_details(request, source, media_id, title, season_number):  # noqa: ARG001 For URL
    """Return the details page for a season."""
    tv_with_seasons_metadata = services.get_media_metadata(
        "tv_with_seasons",
        media_id,
        source,
        [season_number],
    )
    season_metadata = tv_with_seasons_metadata[f"season/{season_number}"]
    season_metadata["season_number"] = season_number

    poster_accent = None
    # First check if there's a season item with custom poster
    season_item = Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=MediaTypes.SEASON.value,
        season_number=season_number,
    ).first()
    
    if season_item:
        # Use the season item's image (which may be custom) for accent color
        poster_accent = compute_and_store_poster_accent(
            season_item,
            poster_url=season_item.image,
        )
    else:
        # Fall back to TV show item
        accent_item = Item.objects.filter(
            media_id=media_id,
            source=source,
            media_type=MediaTypes.TV.value,
        ).first()
        if accent_item:
            poster_accent = compute_and_store_poster_accent(
                accent_item,
                poster_url=season_metadata.get("image"),
            )
        else:
            poster_accent = get_poster_accent_from_url(season_metadata.get("image"))

    accent_palette = build_accent_palette(poster_accent)
    poster_accent = accent_palette["accent"]
    poster_accent_contrast = accent_palette["contrast"]

    # Update season metadata with custom poster if available
    if season_item:
        season_metadata["image"] = season_item.image

    user_medias = BasicMedia.objects.filter_media_prefetch(
        request.user,
        media_id,
        MediaTypes.SEASON.value,
        source,
        season_number=season_number,
    )

    current_instance = user_medias[0] if user_medias else None
    episodes_in_db = current_instance.episodes.all() if current_instance else []

    if current_instance is not None:
        helpers.refresh_item_image_if_missing(
            current_instance.item,
            season_metadata.get("image"),
        )

    # Get diary entries for this season
    diary_entries = []
    try:
        item = Item.objects.get(source=source, media_type=MediaTypes.SEASON.value, media_id=media_id, season_number=season_number)
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).prefetch_related('tags').order_by('-consumed_at')
    except Item.DoesNotExist:
        pass

    if source == Sources.MANUAL.value:
        season_metadata["episodes"] = manual.process_episodes(
            season_metadata,
            episodes_in_db,
        )
    else:
        season_metadata["episodes"] = tmdb.process_episodes(
            season_metadata,
            episodes_in_db,
        )
        # Show each episode's saved still, as its card does after a watch toggle.
        saved_stills = {
            episode.episode_number: tmdb.sized_image_url(episode.image, "w500")
            for episode in Item.objects.filter(
                source=source,
                media_type=MediaTypes.EPISODE.value,
                media_id=media_id,
                season_number=season_number,
            )
            if episode.image and episode.image != settings.IMG_NONE
        }
        for episode in season_metadata["episodes"]:
            episode["image"] = saved_stills.get(episode["episode_number"], episode.get("image"))

    # Enrich related items with user tracking data
    if season_metadata.get("related"):
        for section_name, related_items in season_metadata["related"].items():
            if related_items:
                season_metadata["related"][section_name] = (
                    helpers.enrich_items_with_user_data(
                        request,
                        related_items,
                        section_name,
                    )
                )

    # Get MDBList ratings for seasons from TMDB (use TV show ratings)
    mdblist_ratings = None
    if source == Sources.TMDB.value:
        try:
            # Get TV show ratings for seasons (seasons don't have separate ratings)
            mdblist_ratings = mdblist.get_media_ratings(media_id, "tv")
            logging.getLogger(__name__).info(f"MDBList TV ratings for season {media_id}: {mdblist_ratings}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to fetch MDBList ratings for season {media_id}: {e}")

    context = {
        "media": season_metadata,
        "tv": tv_with_seasons_metadata,
        "media_type": MediaTypes.SEASON.value,
        "user_medias": user_medias,
        "current_instance": current_instance,
        "episodes_in_db": episodes_in_db,
        "diary_entries": diary_entries,
        "mdblist_ratings": mdblist_ratings,
        "poster_accent_color": poster_accent,
        "poster_accent_contrast": poster_accent_contrast,
        "watch_providers": tmdb.filter_providers(
            season_metadata.get("providers"),
            request.user.watch_provider_region,
        ),
        "watch_provider_region": request.user.watch_provider_region,
    }
    return render(request, "app/media_details.html", context)


@require_POST
def update_media_score(request, media_type, instance_id):
    """Update the user's score for a media item."""
    exposure.require_media_type(media_type)
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )

    if media_type == MediaTypes.GAME.value:
        try:
            score = single_weight.rating_from_wire(request.POST.get("score"))
            game_tracking.set_rating(request.user, media.item, score)
        except BookTrackingConflict as error:
            return JsonResponse(error.as_dict(), status=409)
        except ValidationError as error:
            return JsonResponse({"error": error.messages[0]}, status=400)
        return JsonResponse({"success": True, "score": single_weight.rating_to_wire(score)})

    score = float(request.POST.get("score"))
    if single_weight.supports(media_type):
        media = single_weight.set_rating(
            request.user,
            media.item,
            single_weight.rating_from_wire(score),
        )
    else:
        media.score = score
        media.save()
    logger.info(
        "%s score updated to %s",
        media,
        score,
    )

    return JsonResponse(
        {
            "success": True,
            "score": score,
        },
    )


@require_POST
def sync_metadata(request, source, media_type, media_id, season_number=None):
    """Refresh the metadata for a media item."""
    exposure.require_media_type(media_type)
    if source == Sources.MANUAL.value:
        msg = "Manual items cannot be synced."
        messages.error(request, msg)
        return HttpResponse(
            msg,
            status=400,
            headers={"HX-Redirect": request.POST.get("next", "/")},
        )

    cache_key = f"{source}_{media_type}_{media_id}"
    if media_type == MediaTypes.SEASON.value:
        cache_key += f"_{season_number}"

    ttl = cache.ttl(cache_key)
    logger.debug("%s - Cache TTL for: %s", cache_key, ttl)

    if ttl is not None and ttl > (settings.CACHE_TIMEOUT - 3):
        msg = "The data was recently synced, please wait a few seconds."
        messages.error(request, msg)
        logger.error(msg)
    else:
        deleted = cache.delete(cache_key)
        logger.debug("%s - Old cache deleted: %s", cache_key, deleted)

        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.update_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
        },
        )
        title = metadata["title"]
        if season_number:
            title += f" - Season {season_number}"

        if media_type == MediaTypes.SEASON.value:
            metadata["episodes"] = tmdb.process_episodes(
                metadata,
                [],
            )

            # Create a dictionary of existing episodes keyed by episode number
            existing_episodes = {
                ep.episode_number: ep
                for ep in Item.objects.filter(
                    source=source,
                    media_type=MediaTypes.EPISODE.value,
                    media_id=media_id,
                    season_number=season_number,
                )
            }

            episodes_to_update = []
            episode_count = 0

            for episode_data in metadata["episodes"]:
                episode_number = episode_data["episode_number"]
                if episode_number in existing_episodes:
                    episode_item = existing_episodes[episode_number]
                    episode_item.title = metadata["title"]
                    episode_item.image = episode_data["image"]
                    episodes_to_update.append(episode_item)
                    episode_count += 1

            logger.info(
                "Found %s existing episodes to update for %s",
                episode_count,
                title,
            )

            if episodes_to_update:
                updated_count = Item.objects.bulk_update(
                    episodes_to_update,
                    ["title", "image"],
                    batch_size=100,
                )
                logger.info(
                    "Successfully updated %s episodes for %s",
                    updated_count,
                    title,
                )

        item.fetch_releases(delay=False)

        msg = f"{title} was synced to {Sources(source).label} successfully."
        messages.success(request, msg)

    if request.headers.get("HX-Request"):
        return HttpResponse(
            status=204,
            headers={
                "HX-Redirect": request.POST["next"],
        },
        )
    return helpers.redirect_back(request)


@require_GET
def track_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
):
    """Return the tracking form for a media item."""
    exposure.require_media_type(media_type)
    instance_id = request.GET.get("instance_id")
    if instance_id:
        media = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
    elif request.GET.get("is_create"):
        media = None
    else:
        # no specific instance, try to find the first one
        user_medias = BasicMedia.objects.filter_media(
            request.user,
            media_id,
            media_type,
            source,
            season_number=season_number,
        )
        media = user_medias.first()
        if media:
            instance_id = media.id

    initial_data = {
        "media_id": media_id,
        "source": source,
        "media_type": media_type,
        "season_number": season_number,
        "instance_id": instance_id,
    }

    if media:
        title = media.item
        if media_type == MediaTypes.GAME.value:
            initial_data["progress"] = helpers.minutes_to_hhmm(media.progress)
    else:
        title = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )["title"]
        if media_type == MediaTypes.SEASON.value:
            title += f" S{season_number}"

    form_kwargs = (
        {"public_rating_scale": True} if single_weight.supports(media_type) or game_tracking.supports(media_type) else {}
    )
    form = get_form_class(media_type)(instance=media, initial=initial_data, **form_kwargs)

    return render(
        request,
        "app/components/fill_track.html",
        {
            "title": title,
            "form": form,
            "media": media,
            "return_url": request.GET["return_url"],
        },
    )


@require_POST
def media_save(request):
    """Save or update media data to the database."""
    media_id = request.POST["media_id"]
    source = request.POST["source"]
    media_type = request.POST["media_type"]
    exposure.require_media_type(media_type)
    season_number = request.POST.get("season_number")
    instance_id = request.POST.get("instance_id")

    if instance_id:
        try:
            instance = BasicMedia.objects.get_media(
                request.user,
                media_type,
                instance_id,
            )
        except apps.get_model(app_label="app", model_name=media_type).DoesNotExist as exc:
            raise Http404 from exc
    else:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
        )
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
        },
        )
        model = apps.get_model(app_label="app", model_name=media_type)
        instance = model(item=item, user=request.user)

    # Validate the form and save the instance if it's valid
    form_class = get_form_class(media_type)
    form_kwargs = (
        {"public_rating_scale": True} if single_weight.supports(media_type) or game_tracking.supports(media_type) else {}
    )
    form = form_class(request.POST, instance=instance, **form_kwargs)
    if form.is_valid() and tv_tracking.supports(media_type):
        # A written status means its action and stars follow TV-303/TV-304 (TV-207).
        # Only values the form changed are written, so saving notes never re-applies
        # the worked-out status.
        is_new = instance.pk is None
        if is_new:
            # A just-created Item still holds the posted season number as text.
            instance.item.refresh_from_db()
        data = {"notes": form.cleaned_data.get("notes", "")}
        if is_new or "status" in form.changed_data:
            data["status"] = form.cleaned_data.get("status")
        score = form.cleaned_data.get("score")
        if (is_new and score is not None) or (not is_new and "score" in form.changed_data):
            data["rating"] = score
        try:
            result = tv_tracking.apply_written_state(
                request.user, instance.item, data, entry_point=tv_tracking.WEB,
            )
        except TV_WRITE_ERRORS as error:
            messages.error(request, _tv_error_message(error))
            return helpers.redirect_back(request)
        logger.info("%s saved successfully.", result.tv)
    elif form.is_valid():
        if single_weight.supports(media_type):
            rating = single_weight.rating_from_wire(form.cleaned_data.get("score"))
            saved = single_weight.apply_tracking_state(
                request.user,
                instance.item,
                status=form.cleaned_data["status"],
                rating=rating,
                start_date=form.cleaned_data.get("start_date"),
                notes=form.cleaned_data.get("notes", ""),
            )
        else:
            try:
                saved = form.save()
            except (BookTrackingConflict, ValidationError) as error:
                messages.error(request, str(error))
                return helpers.redirect_back(request)
        logger.info("%s saved successfully.", saved)
    else:
        logger.error(form.errors.as_json())
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(
                    request,
                    f"{field.replace('_', ' ').title()}: {error}",
                )

    return helpers.redirect_back(request)


@require_POST
def media_delete(request):
    """Delete media data from the database."""
    instance_id = request.POST["instance_id"]
    media_type = request.POST["media_type"]
    exposure.require_media_type(media_type)
    model = apps.get_model(app_label="app", model_name=media_type)

    try:
        media = BasicMedia.objects.get_media(
            request.user,
            media_type,
            instance_id,
        )
        if tv_tracking.supports(media_type):
            # Remove from Library, a season's tracking, or one watch of an episode,
            # with the same log guards as the app (TV-003, TV-102, TV-207).
            try:
                show_item = tv_listing.show_item_for(media.item)
                if media_type == MediaTypes.TV.value:
                    tv_tracking.remove(request.user, show_item, entry_point=tv_tracking.WEB)
                elif media_type == MediaTypes.SEASON.value:
                    tv_tracking.delete_season_tracking(
                        request.user,
                        show_item,
                        media.item.season_number,
                        entry_point=tv_tracking.WEB,
                    )
                else:
                    result = tv_tracking.untick(
                        request.user,
                        show_item,
                        media.item.season_number,
                        media.item.episode_number,
                        viewing_id=media.pk,
                        entry_point=tv_tracking.WEB,
                    )
                    if not result.changed:
                        messages.error(request, MSG_NOT_LATEST_WATCH)
                        return helpers.redirect_back(request)
            except TV_WRITE_ERRORS as error:
                messages.error(request, _tv_error_message(error))
                return helpers.redirect_back(request)
        elif game_tracking.supports(media_type):
            try:
                game_tracking.remove_tracking(request.user, media.item)
            except BookTrackingConflict as error:
                messages.error(request, str(error))
                return helpers.redirect_back(request)
        elif single_weight.supports(media_type):
            try:
                single_weight.unwatch(request.user, media.item)
            except single_weight.DiaryHistoryExists as error:
                messages.error(request, str(error))
                return helpers.redirect_back(request)
        else:
            media.delete()
        logger.info("%s deleted successfully.", media)

    except model.DoesNotExist:
        logger.warning("The %s was already deleted before.", media_type)

    return helpers.redirect_back(request)


@require_POST
def mark_user_messages_shown(request):
    """Mark all unseen persistent messages for the user as shown."""
    message_ids = [
        int(message_id)
        for message_id in request.POST.getlist("message_ids")
        if message_id.isdigit()
    ]
    if not message_ids:
        return HttpResponse(status=204)

    UserMessage.objects.filter(
        id__in=message_ids,
        user=request.user,
        shown_at__isnull=True,
    ).update(shown_at=timezone.now())
    return HttpResponse(status=204)


@require_POST
def episode_save(request):
    """Handle the creation, deletion, and updating of episodes for a season."""
    media_id = request.POST["media_id"]
    season_number = int(request.POST["season_number"])
    episode_number = int(request.POST["episode_number"])
    source = request.POST["source"]

    form = EpisodeForm(request.POST)
    if not form.is_valid():
        logger.error("Form validation failed: %s", form.errors)
        return HttpResponseBadRequest("Invalid form data")

    # The typed day dates the tick; a future day is rejected (TV-100, TV-106, TV-101).
    try:
        show_item = _tv_show_item(source, media_id)
        result = tv_tracking.tick(
            request.user,
            show_item,
            season_number,
            episode_number,
            at=_tv_typed_day(form.cleaned_data["end_date"]),
            entry_point=tv_tracking.WEB,
        )
    except TV_WRITE_ERRORS as error:
        messages.error(request, _tv_error_message(error))
        return helpers.redirect_back(request)
    if not result.changed:
        messages.info(request, MSG_ALREADY_WATCHED)

    return helpers.redirect_back(request)


@require_http_methods(["GET", "POST"])
def create_entry(request):
    """Return the form for manually adding media items."""
    if request.method == "GET":
        media_types = exposure.media_types()
        return render(request, "app/create_entry.html", {"media_types": media_types})

    # Process the form submission
    exposure.require_media_type(request.POST.get("media_type"))
    form = ManualItemForm(request.POST, user=request.user)
    if not form.is_valid():
        # Handle form validation errors
        logger.error(form.errors.as_json())
        helpers.form_error_messages(form, request)
        return redirect("create_entry")

    # Try to save the item
    try:
        item = form.save()
    except IntegrityError:
        # Handle duplicate item
        media_name = form.cleaned_data["title"]
        if form.cleaned_data.get("season_number"):
            media_name += f" - Season {form.cleaned_data['season_number']}"
        if form.cleaned_data.get("episode_number"):
            media_name += f" - Episode {form.cleaned_data['episode_number']}"

        logger.exception("%s already exists in the database.", media_name)
        messages.error(request, f"{media_name} already exists in the database.")
        return redirect("create_entry")

    # Prepare and validate the media form
    updated_request = request.POST.copy()
    updated_request.update({"source": item.source, "media_id": item.media_id})
    form_kwargs = {"public_rating_scale": True} if single_weight.supports(item) or game_tracking.supports(item) else {}
    media_form = get_form_class(item.media_type)(updated_request, **form_kwargs)

    if not media_form.is_valid():
        # Handle media form validation errors
        logger.error(media_form.errors.as_json())
        helpers.form_error_messages(media_form, request)

        # Delete the item since the media creation failed
        item.delete()
        logger.info("%s was deleted due to media form validation failure", item)
        return redirect("create_entry")

    # Save the media instance
    media_form.instance.user = request.user
    media_form.instance.item = item

    if tv_tracking.supports(item):
        # Manual shows and seasons take a written status as its action, and a manual
        # episode is a tick on the typed day; app.tv_tracking creates the season and
        # show tracking a viewing or mark needs, and no empty seasons (TV-207, TV-205).
        try:
            if item.media_type == MediaTypes.EPISODE.value:
                tv_tracking.tick(
                    request.user,
                    tv_listing.show_item_for(item),
                    item.season_number,
                    item.episode_number,
                    at=_tv_typed_day(media_form.cleaned_data.get("end_date")),
                    entry_point=tv_tracking.WEB,
                )
            else:
                data = {
                    "status": media_form.cleaned_data.get("status"),
                    "notes": media_form.cleaned_data.get("notes", ""),
                }
                if media_form.cleaned_data.get("score") is not None:
                    data["rating"] = media_form.cleaned_data["score"]
                result = tv_tracking.apply_written_state(
                    request.user, item, data, entry_point=tv_tracking.WEB,
                )
                if item.media_type == MediaTypes.TV.value and result.tv is None:
                    # Watching, Completed and stars need an aired episode (TV-005, TV-303).
                    messages.info(request, MSG_MANUAL_SHOW_UNTRACKED)
        except TV_WRITE_ERRORS as error:
            messages.error(request, _tv_error_message(error))
            return redirect("create_entry")
    elif single_weight.supports(item):
        single_weight.apply_tracking_state(
            request.user,
            item,
            status=media_form.cleaned_data["status"],
            rating=single_weight.rating_from_wire(media_form.cleaned_data.get("score")),
            start_date=media_form.cleaned_data.get("start_date"),
            notes=media_form.cleaned_data.get("notes", ""),
        )
    else:
        try:
            media_form.save()
        except (BookTrackingConflict, ValidationError) as error:
            messages.error(request, str(error))
            return redirect("create_entry")

    # Success message
    msg = f"{item} added successfully."
    messages.success(request, msg)
    logger.info(msg)

    return redirect("create_entry")


@require_GET
def search_parent_tv(request):
    """Return the search results for parent TV shows."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for TV shows with query: %s",
        request.user.username,
        query,
    )

    parent_tvs = TV.objects.filter(
        user=request.user,
        item__source=Sources.MANUAL.value,
        item__media_type=MediaTypes.TV.value,
        item__title__icontains=query,
    )[:5]

    return render(
        request,
        "app/components/search_parent_tv.html",
        {"results": parent_tvs, "query": query},
    )


@require_GET
def search_parent_season(request):
    """Return the search results for parent seasons."""
    query = request.GET.get("q", "").strip()

    if len(query) <= 1:
        return render(request, "app/components/search_parent_tv.html")

    logger.debug(
        "%s - Searching for seasons with query: %s",
        request.user.username,
        query,
    )

    # Season Items of the user's manual shows: a season with nothing watched has no
    # tracking yet (TV-205), and its episodes can still be added.
    parent_seasons = manual_parent_seasons(request.user).filter(title__icontains=query)[:5]

    return render(
        request,
        "app/components/search_parent_season.html",
        {"results": parent_seasons, "query": query},
    )


@require_GET
def history_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the history page for a media item."""
    exposure.require_media_type(media_type)
    user_medias = BasicMedia.objects.filter_media(
        request.user,
        media_id,
        media_type,
        source,
        season_number=season_number,
        episode_number=episode_number,
    )

    total_medias = user_medias.count()
    timeline_entries = []
    for index, media in enumerate(user_medias, start=1):
        if history := media.history.all():
            media_entry_number = total_medias - index + 1
            timeline_entries.extend(
                history_processor.process_history_entries(
                    history,
                    media_type,
                    media_entry_number,
                    request.user,
                ),
            )
    return render(
        request,
        "app/components/fill_history.html",
        {
            "media_type": media_type,
            "timeline": timeline_entries,
            "total_medias": total_medias,
            "return_url": request.GET["return_url"],
        },
    )


@require_http_methods(["DELETE"])
def delete_history_record(request, media_type, history_id):
    """Delete a specific history record."""
    exposure.require_media_type(media_type)
    try:
        historical_model = apps.get_model(
            app_label="app",
            model_name=f"historical{media_type.lower()}",
        )

        historical_model.objects.get(
            history_id=history_id,
            history_user=request.user,
        ).delete()

        logger.info(
            "Deleted history record %s",
            str(history_id),
        )

        # Return empty 200 response - the element will be removed by HTMX
        return HttpResponse()

    except historical_model.DoesNotExist:
        logger.exception(
            "History record %s not found for user %s",
            str(history_id),
            str(request.user),
        )
        return HttpResponse("Record not found", status=404)


@require_GET
def statistics(request):
    """Return the statistics page."""
    # Set default date range to last year
    timeformat = "%Y-%m-%d"
    today = timezone.localdate()
    one_year_ago = today.replace(year=today.year - 1)

    # Get date parameters with defaults
    start_date_str = request.GET.get("start-date") or one_year_ago.strftime(timeformat)
    end_date_str = request.GET.get("end-date") or today.strftime(timeformat)

    if start_date_str == "all" and end_date_str == "all":
        start_date = None
        end_date = None
    else:
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)

        if start_date and end_date:
            # Convert to datetime with timezone awareness
            start_date = timezone.make_aware(
                datetime.combine(start_date, datetime.min.time()),
            )

            # End date should be end of day
            end_date = timezone.make_aware(
                datetime.combine(end_date, datetime.max.time()),
            )

    # Get all user media data in a single operation
    user_media, media_count = stats.get_user_media(
        request.user,
        start_date,
        end_date,
    )

    # Calculate all statistics from the retrieved data
    media_type_distribution = stats.get_media_type_distribution(
        media_count,
    )
    score_distribution, top_rated = stats.get_score_distribution(user_media)
    status_distribution = stats.get_status_distribution(user_media)
    status_pie_chart_data = stats.get_status_pie_chart_data(
        status_distribution,
    )
    timeline = stats.get_timeline(user_media)

    activity_data = stats.get_activity_data(request.user, start_date, end_date)

    context = {
        "start_date": start_date,
        "end_date": end_date,
        "media_count": media_count,
        "activity_data": activity_data,
        "media_type_distribution": media_type_distribution,
        "score_distribution": score_distribution,
        "top_rated": top_rated,
        "status_distribution": status_distribution,
        "status_pie_chart_data": status_pie_chart_data,
        "timeline": timeline,
    }

    return render(request, "app/statistics.html", context)


@require_GET
def hof_search(request):
    """Return search results for Hall of Fame selection."""
    try:
        media_type = request.GET["media_type"]  # Use square brackets like working search
        exposure.require_media_type(media_type)
        query = request.GET["q"]  # Use square brackets like working search
    except KeyError as e:
        print(f"HOF Search - Missing parameter: {e}")
        print(f"HOF Search - Available params: {list(request.GET.keys())}")
        return render(request, "app/components/hof_search_results.html", {
            "data": None,
            "query": "",
            "media_type": ""
        })
    
    page = int(request.GET.get("page", 1))
    source = request.GET.get("source")

    # Debug logging
    print(f"HOF Search - Media Type: '{media_type}', Query: '{query}', Page: {page}")

    # Don't search if query is too short
    if len(query.strip()) <= 1:
        print("HOF Search - Query too short")
        return render(request, "app/components/hof_search_results.html", {
            "data": None,
            "query": query,
            "media_type": media_type
        })

    try:
        # Use exact same call as working search
        data = services.search(media_type, query, page, source)
        print(f"HOF Search - Success: {len(data.get('items', []))} items found")
        print(f"HOF Search - Data keys: {list(data.keys()) if data else 'None'}")
    except Exception as e:
        print(f"HOF Search - Error: {e}")
        import traceback
        traceback.print_exc()
        data = None

    return render(request, "app/components/hof_search_results.html", {
        "data": data,
        "source": source,
        "media_type": media_type,
        "query": query
    })

@require_POST
def toggle_hof(request):
    """Toggle an item's Hall of Fame status."""
    print(f"HOF Toggle - POST data: {request.POST}")
    print(f"HOF Toggle - User: {request.user}")
    
    try:
        media_type = request.POST["media_type"]
        exposure.require_media_type(media_type)
        media_id = request.POST["media_id"]
        source = request.POST["source"]
        
        print(f"HOF Toggle - Media Type: {media_type}, ID: {media_id}, Source: {source}")
        
        # Get metadata for the media item
        metadata = services.get_media_metadata(media_type, media_id, source)
        print(f"HOF Toggle - Metadata: {metadata.get('title', 'No title')}")
        
        # Create or get the Item instance
        item, created = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
        },
        )
        
        # Toggle the HOF status
        user = request.user
        hof_field = f"hof_{media_type}"
        
        if hasattr(user, hof_field):
            current_hof_item = getattr(user, hof_field)
            if current_hof_item == item:
                # Remove from hall of fame
                setattr(user, hof_field, None)
                user.save()
                added = False
            else:
                # Add to hall of fame
                setattr(user, hof_field, item)
                user.save()
                added = True
            
            # Trigger HTMX update
            from django.template.loader import render_to_string
            from django.http import HttpResponse
            
            # Return a response that triggers the hofUpdated event
            response = HttpResponse()
            response['HX-Trigger'] = 'hofUpdated'
            return response
    except Exception as e:
        print(f"HOF Toggle - Error: {e}")
        return JsonResponse({"error": str(e)}, status=400)


@require_GET
def book_cover_selection_modal(request, source, media_id):
    """Return a fast-loading modal shell; covers load asynchronously."""
    media_type = MediaTypes.BOOK.value
    try:
        # Fetch or create minimal item for the shell
        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
        except Item.DoesNotExist:
            from app.providers import services
            metadata = services.get_media_metadata(media_type, media_id, source)
            item = Item.objects.create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                title=metadata["title"],
                image=metadata["image"],
            )

        context = {
            "item": item,
            "source": source,
            "media_id": media_id,
        }
        return render(request, "app/components/poster_selection_modal_shell.html", context)
    except Exception as e:
        logger.error("Error preparing book cover modal shell: %s", e)
        return HttpResponseBadRequest("Error opening book covers")


@require_GET
def book_cover_selection_content(request, source, media_id):
    """Return the heavy content for the book cover modal (covers grid)."""
    try:
        from api.services import media as media_service

        options = media_service.book_cover_options(
            source=source,
            media_id=media_id,
            request=request,
            user=request.user,
        )
        for poster in options["posters"]:
            poster["is_current"] = poster["is_selected"]

        context = {
            "item": options["item"],
            "posters": options["posters"],
            "current_poster": options["current_poster"],
            "is_book": True,
        }
        return render(request, "app/components/poster_selection_modal_content.html", context)
    except Exception as e:
        logger.error("Error loading book cover content: %s", e)
        return HttpResponseBadRequest("Error loading book covers")


@require_GET
def poster_selection_modal(request, media_type, media_id, source):
    """Return the poster selection modal with available posters."""
    exposure.require_media_type(media_type)
    if source != Sources.TMDB.value or media_type not in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        return HttpResponseBadRequest("Poster selection only available for TMDB movies and TV shows")
        
    try:
        # Get or create the item
        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
        except Item.DoesNotExist:
            # Item doesn't exist, so we need to create it
            # First get the metadata from TMDB
            from app.providers import services
            metadata = services.get_media_metadata(media_type, media_id, source)
            
            item = Item.objects.create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                title=metadata["title"],
                image=metadata["image"],
            )
        
        # Get available posters from TMDB
        tmdb_posters = tmdb.get_poster_images(media_id, "movie" if media_type == MediaTypes.MOVIE.value else "tv")
        
        # Create the original poster entry
        original_poster = {
            "url": item.image,
            "thumbnail_url": item.image,
            "width": 0,
            "height": 0,
            "aspect_ratio": 0.667,
            "vote_average": 0,
            "vote_count": 0,
            "language": None,
            "is_current": True
        }
        
        # Combine original with TMDB posters, ensuring original is first
        # and not duplicated if it's already in the TMDB results
        posters = [original_poster]
        for poster in tmdb_posters:
            if poster["url"] != item.image:
                # Ensure language is None instead of undefined for consistency
                poster_copy = poster.copy()
                if poster_copy.get("language") is None:
                    poster_copy["language"] = None
                posters.append(poster_copy)
        
        # Get current custom poster if exists
        from app.models import CustomPosterPreference
        try:
            current_preference = CustomPosterPreference.objects.get(
                user=request.user,
                item=item
            )
            current_poster = current_preference.custom_image_url
        except CustomPosterPreference.DoesNotExist:
            current_poster = curated_image_url(CustomPosterPreference, item) or item.image
            
        context = {
            "item": item,
            "posters": posters,
            "current_poster": current_poster,
        }
        
        return render(request, "app/components/poster_selection_modal.html", context)
        
    except Exception as e:
        logger.error("Error in poster selection modal: %s", e)
        return HttpResponseBadRequest("Error loading posters")


@require_GET
def game_poster_selection_modal(request, source, media_id):
    """Return a fast-loading modal shell; covers load asynchronously."""
    if source != Sources.IGDB.value:
        return HttpResponseBadRequest("Poster selection only available for IGDB games")
        
    try:
        # Get or create the item
        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.GAME.value,
            )
        except Item.DoesNotExist:
            # Item doesn't exist, so we need to create it
            # First get the metadata from IGDB
            from app.providers import services
            metadata = services.get_media_metadata(MediaTypes.GAME.value, media_id, source)
            
            item = Item.objects.create(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.GAME.value,
                title=metadata["title"],
                image=metadata["image"],
            )
        
        context = {
            "item": item,
            "source": source,
            "media_id": media_id,
        }
        return render(request, "app/components/poster_selection_modal_shell.html", context)
    except Exception as e:
        logger.error("Error preparing game cover modal shell: %s", e)
        return HttpResponseBadRequest("Error loading game cover modal")


@require_GET
def game_cover_selection_content(request, source, media_id):
    """Return the heavy content for the game cover modal (covers grid)."""
    if source != Sources.IGDB.value:
        return HttpResponseBadRequest("Cover selection only available for IGDB games")
        
    try:
        # Get or create the item
        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.GAME.value,
            )
        except Item.DoesNotExist:
            from app.providers import services
            metadata = services.get_media_metadata(MediaTypes.GAME.value, media_id, source)
            item = Item.objects.create(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.GAME.value,
                title=metadata["title"],
                image=metadata["image"],
            )
        
        # Get available covers from IGDB
        igdb_covers = igdb.get_game_covers(media_id)
        logger.info("Retrieved %s covers from IGDB for game %s", len(igdb_covers) if igdb_covers else 0, media_id)
        
        # Extract image_id from the original cover URL for comparison
        # item.image format: https://images.igdb.com/igdb/image/upload/t_original/{image_id}.jpg
        original_image_id = None
        if item.image and "t_original/" in item.image:
            try:
                # Extract image_id from URL like: .../t_original/co2lbd.jpg
                original_image_id = item.image.split("t_original/")[1].replace(".jpg", "")
            except (IndexError, AttributeError):
                pass
        
        # Create the original cover entry
        original_cover = {
            "url": item.image,
            "thumbnail_url": item.image,
            "width": 0,
            "height": 0,
            "aspect_ratio": 0.667,
            "vote_average": 0,
            "vote_count": 0,
            "language": None,
            "is_current": True,
            "is_original": True,
            "image_id": original_image_id,
        }
        
        # Combine original with IGDB covers, ensuring original is first
        # Include all covers from IGDB, even if they match the original
        # (IGDB may only have one cover per game, but we still want to show it)
        posters = [original_cover]
        seen_image_ids = {original_image_id} if original_image_id else set()
        
        if igdb_covers:
            logger.info("Processing %s covers from IGDB for game %s", len(igdb_covers), media_id)
            for cover in igdb_covers:
                cover_image_id = cover.get("image_id")
                if cover_image_id:
                    # Add all covers, but mark if it's the same as original
                    if cover_image_id in seen_image_ids:
                        logger.debug("Skipping duplicate cover %s for game %s", cover_image_id, media_id)
                        continue
                    seen_image_ids.add(cover_image_id)
                    # Ensure language is None instead of undefined for consistency
                    cover_copy = cover.copy()
                    if cover_copy.get("language") is None:
                        cover_copy["language"] = None
                    posters.append(cover_copy)
                    logger.debug("Added cover %s for game %s", cover_image_id, media_id)
        else:
            logger.warning("No covers returned from IGDB for game %s", media_id)
        
        logger.info("Total posters for game %s: %s (1 original + %s from IGDB)", media_id, len(posters), len(posters) - 1)
        
        # Get current custom poster if exists
        from app.models import CustomPosterPreference
        try:
            current_preference = CustomPosterPreference.objects.get(user=request.user, item=item)
            current_poster = current_preference.custom_image_url
        except CustomPosterPreference.DoesNotExist:
            current_poster = curated_image_url(CustomPosterPreference, item) or item.image
        
        context = {
            "item": item,
            "posters": posters,
            "current_poster": current_poster,
            "is_game": True,  # Use game template which doesn't filter by language
        }
        return render(request, "app/components/poster_selection_modal_content.html", context)
    except Exception as e:
        logger.error("Error loading game cover content: %s", e)
        return HttpResponseBadRequest("Error loading game covers")


@require_GET
def season_poster_selection_modal(request, source, media_id, season_number):
    """Return the poster selection modal with available posters for a season."""
    if source != Sources.TMDB.value:
        return HttpResponseBadRequest("Poster selection only available for TMDB seasons")
        
    try:
        # Get or create the season item
        try:
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.SEASON.value,
                season_number=season_number,
            )
        except Item.DoesNotExist:
            # Item doesn't exist, so we need to create it
            # First get the metadata from TMDB
            from app.providers import services
            metadata = services.get_media_metadata(
                "tv_with_seasons", 
                media_id, 
                source, 
                season_numbers=[season_number]
            )
            season_metadata = metadata[f"season/{season_number}"]
            
            item = Item.objects.create(
                media_id=media_id,
                source=source,
                media_type=MediaTypes.SEASON.value,
                title=season_metadata["title"],
                image=season_metadata["image"],
                season_number=season_number,
            )
        
        # Get available posters from TMDB for the season
        tmdb_posters = tmdb.get_poster_images(media_id, "season", season_number)
        
        # Create the original poster entry
        original_poster = {
            "url": item.image,
            "thumbnail_url": item.image,
            "width": 0,
            "height": 0,
            "aspect_ratio": 0.667,
            "vote_average": 0,
            "vote_count": 0,
            "language": None,
            "is_current": True
        }
        
        # Combine original with TMDB posters, ensuring original is first
        # and not duplicated if it's already in the TMDB results
        posters = [original_poster]
        for poster in tmdb_posters:
            if poster["url"] != item.image:
                # Ensure language is None instead of undefined for consistency
                poster_copy = poster.copy()
                if poster_copy.get("language") is None:
                    poster_copy["language"] = None
                posters.append(poster_copy)
        
        # Get current custom poster if exists
        from app.models import CustomPosterPreference
        try:
            current_preference = CustomPosterPreference.objects.get(
                user=request.user,
                item=item
            )
            current_poster = current_preference.custom_image_url
        except CustomPosterPreference.DoesNotExist:
            current_poster = curated_image_url(CustomPosterPreference, item) or item.image
            
        context = {
            "item": item,
            "posters": posters,
            "current_poster": current_poster,
        }
        
        return render(request, "app/components/poster_selection_modal.html", context)
        
    except Exception as e:
        logger.error("Error in season poster selection modal: %s", e)
        return HttpResponseBadRequest("Error loading season posters")


@require_POST
def save_poster_preference(request):
    """Save the user's poster preference for an item."""
    try:
        media_type = request.POST["media_type"]
        exposure.require_media_type(media_type)
        media_id = request.POST["media_id"]
        source = request.POST["source"]
        custom_image_url = request.POST["poster_url"]
        
        # Handle season items differently
        if media_type == MediaTypes.SEASON.value:
            season_number = request.POST.get("season_number")
            if not season_number:
                return JsonResponse({"error": "Season number required for season items"}, status=400)
            
            # Get or create the season item
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
                season_number=season_number,
            )
        else:
            # Get or create the item
            item = Item.objects.get(
                media_id=media_id,
                source=source,
                media_type=media_type,
            )
        
        # Update or create the preference
        from app.models import CustomPosterPreference

        palette = build_accent_palette(get_poster_accent_from_url(custom_image_url))

        CustomPosterPreference.objects.update_or_create(
            user=request.user,
            item=item,
            defaults={"custom_image_url": custom_image_url, "accent_color": palette["accent"]}
        )

        # Only an admin's pick becomes everyone's cover.
        if is_curator(request.user):
            item.image = custom_image_url
            item.poster_accent_color = palette["accent"]
            item.save(update_fields=["image", "poster_accent_color"])

        return JsonResponse(
            {
                "success": True,
                "poster_url": custom_image_url,
                "accent": palette["accent"],
                "contrast": palette["contrast"],
        }
        )
        
    except Item.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)
    except Exception as e:
        logger.error("Error saving poster preference: %s", e)
        return JsonResponse({"error": str(e)}, status=400)


@require_POST
def mark_consumed(request, media_type, instance_id):
    """Mark a media item as consumed."""
    exposure.require_media_type(media_type)
    if media_type != MediaTypes.MOVIE.value:
        raise Http404("Mark as consumed is only available for movies")
        
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )
    
    from app.services import mark_consumed as mark_consumed_service
    mark_consumed_service(request.user, media)
    
    # Return fragment for HTMX to swap
    context = {
        "media": media,
        "media_type": media_type,
    }
    return render(request, "app/components/media_actions.html", context)


@require_POST
def add_diary_entry(request, media_type, instance_id):
    """Create a new diary entry."""
    exposure.require_media_type(media_type)
    if media_type != MediaTypes.MOVIE.value:
        raise Http404("Diary entries are only available for movies")
        
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )
    
    form = DiaryEntryForm(
        request.POST,
        user=request.user,
        item=media.item,
    )
    
    if form.is_valid():
        from app.services import create_diary_entry
        entry = create_diary_entry(
            user=request.user,
            item=media.item,
            consumed_at=form.cleaned_data["consumed_at"],
            rating=(
                single_weight.rating_from_wire(form.cleaned_data["rating"])
                if single_weight.supports(media.item) and form.cleaned_data["rating"] is not None
                else form.cleaned_data["rating"]
            ),
            review=form.cleaned_data["review"],
            auto_mark_consumed=request.POST.get("auto_mark_consumed") == "true",
            tags=form.cleaned_data.get("tags", []),
        )
        
        if request.headers.get("HX-Request"):
            # Return fragment for modal to close and actions to refresh
            context = {
                "media": media,
                "media_type": media_type,
                "entry": entry,
        }
            return render(request, "app/components/media_actions.html", context)
        
        return redirect("diary_item", media_type=media_type, instance_id=instance_id)
    
    # If form invalid, return the form with errors
    context = {
        "form": form,
        "media": media,
        "media_type": media_type,
    }
    return render(request, "app/components/diary_form.html", context)


@require_GET
def diary_list(request):
    """Show user's diary entries."""
    # Get filters from query params
    media_type = request.GET.get("media_type", "")
    if media_type:
        exposure.require_media_type(media_type)
    year = request.GET.get("year")
    item_id = request.GET.get("item_id")
    page = request.GET.get("page", 1)
    
    # Base queryset - order by created_at descending (newest first) to reflect actual logging order
    entries = DiaryEntry.objects.filter(
        user=request.user,
        item__media_type__in=exposure.media_types(),
    ).select_related('item').prefetch_related('tags').order_by('-created_at')
    
    # Apply filters
    if media_type:
        entries = entries.filter(item__media_type=media_type)
    
    if item_id:
        try:
            item_id = int(item_id)
            entries = entries.filter(item__id=item_id)
        except ValueError:
            pass
    
    if year:
        try:
            year = int(year)
            entries = entries.filter(consumed_at__year=year)
        except ValueError:
            pass
    
    # Get unique years for filter dropdown
    years = entries.dates("consumed_at", "year", order="DESC")
    
    # Paginate
    paginator = Paginator(entries, 25)  # 25 entries per page
    page_obj = paginator.get_page(page)
    
    context = {
        "entries": page_obj,
        "years": years,
        "current_year": year,
        "current_media_type": media_type,
        "media_type_choices": exposure.media_type_choices(),
    }
    
    if request.headers.get("HX-Request"):
        return render(request, "app/components/diary_entries.html", context)
    
    return render(request, "app/diary.html", context)


@require_GET
def diary_item(request, media_type, instance_id):
    """Show diary entries for a specific item."""
    exposure.require_media_type(media_type)
    media = BasicMedia.objects.get_media(
        request.user,
        media_type,
        instance_id,
    )
    
    entries = DiaryEntry.objects.filter(
        user=request.user,
        item=media.item,
    ).prefetch_related('tags').order_by("-consumed_at")
    
    context = {
        "media": media,
        "media_type": media_type,
        "entries": entries,
    }
    
    if request.headers.get("HX-Request"):
        return render(request, "app/components/diary_item_entries.html", context)
    
    return render(request, "app/diary_item.html", context)


from datetime import date

def log_modal(request, source, media_type, media_id, season_number=None):
    """Show the log entry modal."""
    exposure.require_media_type(media_type)
    if media_type not in [
        MediaTypes.MOVIE.value,
        MediaTypes.TV.value,
        MediaTypes.SEASON.value,
        MediaTypes.BOOK.value,
        MediaTypes.GAME.value,
        MediaTypes.MUSIC.value,
    ]:
        raise Http404(
            "Logging is only available for movies, TV shows, seasons, books, games, and music",
        )
        
    # Get or create the item - fetch metadata if it doesn't exist
    try:
        if media_type == MediaTypes.SEASON.value and season_number is not None:
            item = Item.objects.get(
                source=source, 
                media_type=media_type, 
                media_id=media_id, 
                season_number=season_number
            )
        else:
            item = Item.objects.get(source=source, media_type=media_type, media_id=media_id)
    except Item.DoesNotExist:
        # Fetch metadata and create the item
        if media_type == MediaTypes.SEASON.value and season_number is not None:
            # For seasons, we need to get the TV show metadata first
            tv_metadata = services.get_media_metadata("tv_with_seasons", media_id, source, [season_number])
            season_metadata = tv_metadata[f"season/{season_number}"]
            item, _ = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                season_number=season_number,
                defaults={
                    "title": season_metadata["title"],
                    "image": season_metadata["image"],
        },
            )
        else:
            metadata = services.get_media_metadata(media_type, media_id, source)
            defaults = {
                "title": metadata["title"],
                "image": metadata["image"],
            }
            if media_type == MediaTypes.BOOK.value:
                defaults["total_pages"] = metadata.get("total_pages") or metadata.get("max_progress")
            item, created = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                defaults=defaults,
            )
            if (
                media_type == MediaTypes.BOOK.value
                and not created
                and defaults.get("total_pages")
                and item.total_pages != defaults["total_pages"]
            ):
                item.total_pages = defaults["total_pages"]
                item.save(update_fields=["total_pages"])
        
    # Determine default date for logging
    default_date = timezone.localdate()
    tv_defaults = None
    if tv_tracking.supports(item):
        # The server's composer defaults for this target: date and Rewatch (TV-404).
        tv_defaults = _tv_log_defaults(request.user, item)
        default_date = (
            date.fromisoformat(tv_defaults["date"])
            if tv_defaults and tv_defaults.get("date")
            else user_calendar.today(request.user)
        )

    # Use the same template for both movies and TV shows
    tracking_model = apps.get_model(app_label="app", model_name=media_type)
    tracking = tracking_model.objects.filter(user=request.user, item=item).first()
    is_single_weight = single_weight.supports(item)
    current_rating = (
        single_weight.rating_to_wire(tracking.score)
        if (is_single_weight or game_tracking.supports(item) or tv_tracking.supports(item))
        and tracking
        and tracking.score is not None
        else tracking.score if tracking else None
    )
    game_state = game_tracking.state_payload(tracking) if game_tracking.supports(item) and tracking else None
    return render(request, 'app/components/log_modal.html', {
        'item': item,
        'mutation_id': uuid4(),
        'game_playthrough': game_state['current_playthrough'] if game_state and game_state['current_playthrough'] and game_state['current_playthrough']['status'] in game_tracking.OPEN_STATUSES else None,
        'user': request.user,
        'today': default_date,
        'book_completion': request.GET.get('book_complete') == '1',
        'is_repeat': (
            single_weight.default_repeat(request.user, item)
            if is_single_weight
            else bool(game_state and game_state['lifetime_completion_count']) if game_tracking.supports(item)
            else bool(tv_defaults and tv_defaults.get("is_rewatch")) if tv_tracking.supports(item)
            else request.GET.get('relisten') == '1'
        ),
        'current_rating': current_rating,
        'current_liked': MediaLike.objects.filter(user=request.user, item=item).exists(),
    })


def _tv_log_defaults(user, item):
    """Return the TV composer's defaults for the web log modal's target, or None (TV-404)."""
    target_id = "show" if item.media_type == MediaTypes.TV.value else f"season:{item.season_number}"
    try:
        composer = tv_tracking.state_payload(user, item).get("composer") or {}
    except (*TV_WRITE_ERRORS, *tv_listing.PROVIDER_FAILURES):
        logger.warning("Could not work out the log defaults for %s", item)
        return None
    return next((target for target in composer.get("targets") or () if target["id"] == target_id), None)


@require_POST
def mark_movie_watched(request, source, media_id):
    """Mark a movie as watched by creating a tracking instance and marking it as consumed."""
    media_type = MediaTypes.MOVIE.value
        
    # Get or create the item
    metadata = services.get_media_metadata(media_type, media_id, source)
    item, _ = Item.objects.get_or_create(
        media_id=media_id,
        source=source,
        media_type=media_type,
        defaults={
            "title": metadata["title"],
            "image": metadata["image"],
        },
    )
    
    media_instance = single_weight.mark_consumed(request.user, item)
    
    # Get diary entries for this media
    diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
    
    # Return updated action buttons for HTMX to swap  
    # Add required fields to metadata for template compatibility
    metadata["media_type"] = media_type
    metadata["source"] = source
    metadata["media_id"] = media_id
    context = {
        "media": metadata,
        "media_type": media_type,
        "current_instance": media_instance,
        "diary_entries": diary_entries,
    }
    return render(request, "app/components/media_actions.html", context)


@require_POST
def unmark_movie_watched(request, source, media_id):
    """Unmark a movie as watched by removing the tracking instance."""
    media_type = MediaTypes.MOVIE.value
        
    try:
        # Get the item
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type
        )
        
        # Get the media instance
        from app.models import Movie
        media_instance = Movie.objects.get(
            item=item,
            user=request.user
        )
        
        try:
            single_weight.unwatch(request.user, item)
            media_instance = None
        except single_weight.DiaryHistoryExists as error:
            messages.error(request, str(error))
        
        # Get metadata for template
        metadata = services.get_media_metadata(media_type, media_id, source)
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": media_instance,
            "diary_entries": diary_entries,
        }
        return render(request, "app/components/media_actions.html", context)
        
    except (Item.DoesNotExist, Movie.DoesNotExist):
        # If the item or media instance doesn't exist, return the unwatched state
        metadata = services.get_media_metadata(media_type, media_id, source)
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": None,
        }
        return render(request, "app/components/media_actions.html", context)


def _game_action_response(request, source, media_id, action):
    """Retain legacy game URLs and HTMX fragments with canonical transitions."""
    exposure.require_media_type(MediaTypes.GAME.value)
    metadata = services.get_media_metadata(MediaTypes.GAME.value, media_id, source)
    item, _ = Item.objects.get_or_create(
        media_id=media_id, source=source, media_type=MediaTypes.GAME.value,
        defaults={"title": metadata["title"], "image": metadata["image"]},
    )
    try:
        current = action(request.user, item)
    except game_tracking.CompletionRequired:
        response = log_modal(request, source, MediaTypes.GAME.value, media_id)
        response["HX-Retarget"] = "#log-modal-container"
        response["HX-Reswap"] = "innerHTML"
        return response
    except BookTrackingConflict as error:
        return JsonResponse(error.as_dict(), status=409)
    except ValidationError as error:
        return JsonResponse({"error": error.messages[0]}, status=400)
    metadata.update(media_type=MediaTypes.GAME.value, source=source, media_id=media_id)
    return render(request, "app/components/media_actions.html", {
        "media": metadata,
        "media_type": MediaTypes.GAME.value,
        "current_instance": current,
        "diary_entries": DiaryEntry.objects.filter(user=request.user, item=item).order_by("-consumed_at"),
    })


# ---------------------------------------------------------------------------------
# TV (docs/tv-tracking-contract.md). Every web write records its facts through
# app.tv_tracking with entry point WEB, so it ends in the same state as the app (TV-207).
# ---------------------------------------------------------------------------------

TV_WRITE_ERRORS = (
    tv_tracking.TVConflict,
    tv_tracking.TVInvalid,
    tv_listing.InvalidEpisode,
    tv_listing.TVDetailsUnavailable,
)
MSG_SEASON_STATUS = "Pause or drop the show instead."
MSG_ALREADY_WATCHED = (
    "You've already watched this episode. To record watching it again, log it with Rewatch on."
)
MSG_NOT_LATEST_WATCH = "Remove your most recent watch of this episode first."
MSG_MANUAL_SHOW_UNTRACKED = (
    "Nothing has aired yet, so the show isn't in your library. "
    "Choose Planning to add it."
)


def _tv_error_message(error):
    return getattr(error, "message", None) or str(error)


def _tv_error_response(error):
    """Answer a rejected TV write as the game views do: 409, 400, or 503 to retry."""
    if isinstance(error, tv_tracking.TVConflict):
        status = 409
    elif isinstance(error, tv_listing.TVDetailsUnavailable):
        status = 503
    else:
        status = 400
    return JsonResponse({"error": _tv_error_message(error)}, status=status)


def _tv_show_item(source, media_id, metadata=None):
    """Return the show's tv Item, creating it from the provider's metadata when missing."""
    lookup = {
        "media_id": media_id,
        "source": source,
        "media_type": MediaTypes.TV.value,
        "season_number": None,
        "episode_number": None,
    }
    show_item = Item.objects.filter(**lookup).first()
    if show_item is not None:
        return show_item
    if metadata is None:
        metadata = services.get_media_metadata(MediaTypes.TV.value, media_id, source)
    show_item, _ = Item.objects.get_or_create(
        **lookup,
        defaults={"title": metadata["title"], "image": metadata["image"]},
    )
    return show_item


def _tv_row(user, show_item):
    return TV.objects.filter(user=user, item=show_item).first()


def _tv_web_rating(value):
    """Return a web form's whole-star rating (0 = none) on TV's 0-10 scale (TV-504)."""
    return single_weight.rating_from_wire(value)


def _tv_actions_response(request, metadata, show_item, *, message=None, message_type="success"):
    """Render the show's media_actions fragment after a TV write."""
    tv = _tv_row(request.user, show_item)
    metadata.update(
        media_type=MediaTypes.TV.value,
        source=show_item.source,
        media_id=show_item.media_id,
    )
    has_seasons_in_progress = bool(
        tv is not None
        and tv.seasons.filter(status=Status.IN_PROGRESS.value, is_started=True).exists(),
    )
    response = render(request, "app/components/media_actions.html", {
        "media": metadata,
        "media_type": MediaTypes.TV.value,
        "current_instance": tv,
        "diary_entries": DiaryEntry.objects.filter(
            user=request.user, item=show_item,
        ).order_by("-consumed_at"),
        "has_seasons_in_progress": has_seasons_in_progress,
    })
    if message:
        response["X-Notification-Message"] = message
        response["X-Notification-Type"] = message_type
    return response


def _season_actions_response(request, source, media_id, season_number, *, message=None, message_type="success"):
    """Render a season's media_actions fragment after a TV write."""
    season_metadata = services.get_media_metadata(
        MediaTypes.SEASON.value, media_id, source, season_numbers=[season_number],
    )
    season_metadata.update(
        media_type=MediaTypes.SEASON.value,
        source=source,
        media_id=media_id,
        season_number=season_number,
    )
    show_item = _tv_show_item(source, media_id)
    tv = _tv_row(request.user, show_item)
    season = None
    if tv is not None:
        season = (
            Season.objects.filter(related_tv=tv, item__season_number=season_number)
            .select_related("item", "related_tv")
            .first()
        )
        # A season that isn't started counts as untracked (TV-205).
        if season is not None and season.is_started is False:
            season = None
    season_item = season.item if season is not None else Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=MediaTypes.SEASON.value,
        season_number=season_number,
    ).first()
    response = render(request, "app/components/media_actions.html", {
        "media": season_metadata,
        "media_type": MediaTypes.SEASON.value,
        "current_instance": season,
        "tv_instance": tv,
        "diary_entries": (
            DiaryEntry.objects.filter(user=request.user, item=season_item).order_by("-consumed_at")
            if season_item is not None
            else DiaryEntry.objects.none()
        ),
    })
    if message:
        response["X-Notification-Message"] = message
        response["X-Notification-Type"] = message_type
    return response


def _tv_episode_states(user, show_item, season_number):
    """Return {episode number: TVEpisodeState} for a season's listed episodes."""
    listing = tv_listing.get_listing(show_item)
    return tv_tracking.episode_states(user, show_item, season_number, listing)


def _tv_progress_target(user, show_item, season_number, *, increase):
    """Return the (season, episode) a home-page progress button ticks or unticks.

    Increase ticks the season's up next: the show's up next when it is in this season,
    else the first tickable episode after the season's last watched one. Decrease
    unticks the season's highest-numbered watched episode. A show (season None) uses
    every regular season.
    """
    if season_number is None:
        listing = tv_listing.get_listing(show_item)
        numbers = listing.regular_seasons()
        states = {
            (number, episode): state
            for number in numbers
            for episode, state in tv_tracking.episode_states(user, show_item, number, listing).items()
        }
    else:
        states = {
            (season_number, episode): state
            for episode, state in _tv_episode_states(user, show_item, season_number).items()
        }
    ordered = sorted(states)
    watched = [position for position in ordered if states[position]["watched"]]
    if not increase:
        return watched[-1] if watched else None
    up_next = next((position for position in ordered if states[position]["is_up_next"]), None)
    if up_next is not None:
        return up_next
    tickable = [position for position in ordered if states[position]["can_tick"]]
    last = watched[-1] if watched else None
    later = [position for position in tickable if last is None or position > last]
    return (later or tickable or [None])[0]


def _tv_typed_day(value):
    """Return the calendar day a web form typed for a tick (TV-100).

    The form reads what was typed as wall time in the request's timezone, so that
    zone gives back the typed day, which becomes the tick's day on the user's calendar.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value


@require_POST
def start_playing_game(request, source, media_id):
    """Start or resume one game playthrough."""
    return _game_action_response(request, source, media_id, game_tracking.start)


@require_POST
def mark_game_played(request, source, media_id):
    """Mark past completion or open the current playthrough's composer."""
    return _game_action_response(request, source, media_id, game_tracking.mark_completed)


@require_POST
def mark_game_completed(request, source, media_id):
    """Preserve the older completion URL."""
    return mark_game_played(request, source, media_id)


@require_POST
def unmark_game_completed(request, source, media_id):
    """Undo undated completion without deleting protected history."""
    return _game_action_response(request, source, media_id, game_tracking.undo_completed)


@require_POST
def mark_tv_watched(request, source, media_type, media_id):
    """Mark every aired regular episode watched, undated, as one mark (TV-300)."""
    exposure.require_media_type(media_type)
    if media_type != MediaTypes.TV.value:
        raise Http404("Mark as watched is only available for TV shows")
    metadata = services.get_media_metadata(media_type, media_id, source)
    show_item = _tv_show_item(source, media_id, metadata)
    try:
        tv_tracking.mark_watched(request.user, show_item, entry_point=tv_tracking.WEB)
    except TV_WRITE_ERRORS as error:
        return _tv_error_response(error)
    return _tv_actions_response(
        request,
        metadata,
        show_item,
        message="Marked every aired episode watched",
    )


@require_POST
def unmark_tv_watched(request, source, media_type, media_id):
    """Undo the filled show eye (TV-302); the show leaves only when nothing remains (TV-208)."""
    exposure.require_media_type(media_type)
    if media_type != MediaTypes.TV.value:
        raise Http404("Unwatch is only available for TV shows")
    metadata = services.get_media_metadata(media_type, media_id, source)
    show_item = _tv_show_item(source, media_id, metadata)
    try:
        tv_tracking.unmark(request.user, show_item, confirm=True, entry_point=tv_tracking.WEB)
    except TV_WRITE_ERRORS as error:
        return _tv_error_response(error)
    return _tv_actions_response(
        request,
        metadata,
        show_item,
        message="Marked the show's episodes unwatched",
        message_type="info",
    )


@require_POST
def start_tracking_tv(request, source, media_type, media_id):
    """Choose Watching for the show: no date, no viewing, no season tracking (TV-005)."""
    exposure.require_media_type(media_type)
    if media_type != MediaTypes.TV.value:
        raise Http404("Start tracking is only available for TV shows")
    metadata = services.get_media_metadata(media_type, media_id, source)
    show_item = _tv_show_item(source, media_id, metadata)
    try:
        tv_tracking.choose(
            request.user, show_item, Status.IN_PROGRESS.value, entry_point=tv_tracking.WEB,
        )
    except TV_WRITE_ERRORS as error:
        return _tv_error_response(error)
    return _tv_actions_response(request, metadata, show_item, message="Started watching")


@require_POST
def start_tracking_season(request, source, media_type, media_id, season_number):
    """Choose Watching for the season's show; the season gets no tracking (TV-207, TV-205)."""
    exposure.require_media_type(media_type)
    if media_type != MediaTypes.SEASON.value:
        raise Http404("Start tracking is only available for seasons")
    show_item = _tv_show_item(source, media_id)
    try:
        tv_tracking.choose(
            request.user, show_item, Status.IN_PROGRESS.value, entry_point=tv_tracking.WEB,
        )
    except TV_WRITE_ERRORS as error:
        return _tv_error_response(error)
    return _season_actions_response(
        request, source, media_id, season_number, message="Started watching",
    )


def _latest_viewing(user, show_item, season_number, episode_number):
    """Return the user's most recently recorded viewing of an episode, or None."""
    return (
        Episode.objects.filter(
            related_season__user=user,
            related_season__related_tv__item=show_item,
            item__season_number=season_number,
            item__episode_number=episode_number,
        )
        .select_related("item")
        .order_by("-created_at", "-id")
        .first()
    )


def _tv_finished(user, show_item, season_number):
    """Return (season finished, show finished) from the stored worked-out state."""
    tv = _tv_row(user, show_item)
    if tv is None:
        return False, False
    season_finished = Season.objects.filter(
        related_tv=tv,
        item__season_number=season_number,
        is_started=True,
        status=Status.COMPLETED.value,
    ).exists()
    return season_finished, bool(tv.finished)


def _tv_finish_messages(user, show_item, season_number, before):
    """Leave the web's persistent notes when a tick finished a season or the show."""
    season_before, show_before = before
    season_after, show_after = _tv_finished(user, show_item, season_number)
    if season_number >= 1 and season_after and not season_before:
        UserMessage.objects.create(
            user=user,
            level=UserMessageLevel.SUCCESS,
            message=f"{show_item.title} S{season_number} was marked as completed automatically.",
        )
    if show_after and not show_before:
        UserMessage.objects.create(
            user=user,
            level=UserMessageLevel.SUCCESS,
            message=f"{show_item.title} was marked as completed automatically.",
        )


@require_POST
def watch_episode(request, source, media_type, media_id, season_number, episode_number):
    """Tick an episode: one viewing, dated now (TV-100); a watched one records nothing (TV-101)."""
    exposure.require_media_type(media_type)
    if media_type != MediaTypes.EPISODE.value:
        raise Http404("Watch episode is only available for episodes")
    show_item = _tv_show_item(source, media_id)
    season_before, show_before = _tv_finished(request.user, show_item, season_number)
    try:
        tv_tracking.tick(
            request.user,
            show_item,
            season_number,
            episode_number,
            entry_point=tv_tracking.WEB,
        )
    except TV_WRITE_ERRORS as error:
        return _tv_error_response(error)
    viewing = _latest_viewing(request.user, show_item, season_number, episode_number)
    response = render_episode_card(request, viewing)
    season_after, show_after = _tv_finished(request.user, show_item, season_number)
    if season_after and not season_before and season_number >= 1:
        if show_after and not show_before:
            response["X-Notification-Message"] = f"Congratulations! You completed {show_item.title}! Log it?"
        else:
            response["X-Notification-Message"] = f"Season {season_number} completed! Log it?"
        response["X-Notification-Type"] = "success"
    return response


@require_POST
def unwatch_episode(request, source, media_type, media_id, season_number, episode_number):
    """Untick an episode: remove its most recent unlogged viewing (TV-102)."""
    exposure.require_media_type(media_type)
    if media_type != MediaTypes.EPISODE.value:
        raise Http404("Unwatch episode is only available for episodes")

    show_item = _tv_show_item(source, media_id)
    try:
        tv_tracking.untick(
            request.user,
            show_item,
            season_number,
            episode_number,
            entry_point=tv_tracking.WEB,
        )
    except TV_WRITE_ERRORS as error:
        return _tv_error_response(error)
    viewing = _latest_viewing(request.user, show_item, season_number, episode_number)
    if viewing is not None:
        # Another viewing of the episode still counts (an earlier tick or a fill).
        return render_episode_card(request, viewing)
    episode_item = Item.objects.filter(
        media_id=media_id,
        source=source,
        media_type=media_type,
        season_number=season_number,
        episode_number=episode_number,
    ).first()
    if episode_item is None:
        raise Http404("Episode or season not found")

    # Create a mock episode object with all metadata from the API
    class MockEpisode:
        def __init__(self, item, metadata):
            self.item = item
            self.history = []
            self.image = item.image
            self.title = item.title
            self.episode_number = item.episode_number
            # Get episode data from metadata
            self.air_date = metadata.get("air_date")
            # Format runtime using the same function as tmdb.process_episodes
            from app.providers.tmdb import get_readable_duration
            self.runtime = get_readable_duration(metadata.get("runtime"))
            self.vote_average_out_of_5 = metadata.get("vote_average", 0) / 2 if metadata.get("vote_average") else None
            self.overview = metadata.get("overview", "No synopsis available.")
            # Add fields needed for URL generation
            self.source = item.source
            self.media_type = item.media_type
            self.media_id = item.media_id
            self.season_number = item.season_number
    
    # Get episode metadata from API
    try:
        season_metadata = services.get_media_metadata(
            "tv_with_seasons",
            media_id,
            source,
            [season_number]
        )[f"season/{season_number}"]
        
        episode_metadata = {}
        for ep_data in season_metadata["episodes"]:
            if ep_data["episode_number"] == episode_number:
                episode_metadata = ep_data
                break
    except Exception:
        episode_metadata = {}
    
    mock_episode = MockEpisode(episode_item, episode_metadata)
    
    # Return the updated episode card
    return render_episode_card(request, mock_episode)


def render_episode_card(request, episode):
    """Helper function to render an episode card."""
    # Create a mock episode object that matches the template's expectations
    class MockEpisode:
        def __init__(self, episode_instance):
            # Handle both Episode model instances and existing MockEpisode objects
            if hasattr(episode_instance, 'end_date'):
                # It's an Episode model instance
                self.item = episode_instance.item
                # Every Episode row is a viewing, a fill (no date) included (TV-301).
                self.history = [episode_instance]
                self.image = episode_instance.item.image
                self.title = episode_instance.item.title
                self.episode_number = episode_instance.item.episode_number
                
                # Get episode metadata for additional fields
                try:
                    season_metadata = services.get_media_metadata(
                        "tv_with_seasons",
                        episode_instance.item.media_id,
                        episode_instance.item.source,
                        [episode_instance.item.season_number]
                    )[f"season/{episode_instance.item.season_number}"]
                    
                    # Find the episode in the metadata
                    for episode_data in season_metadata["episodes"]:
                        if episode_data["episode_number"] == episode_instance.item.episode_number:
                            self.air_date = episode_data.get("air_date")
                            # Format runtime using the same function as tmdb.process_episodes
                            from app.providers.tmdb import get_readable_duration
                            self.runtime = get_readable_duration(episode_data.get("runtime"))
                            self.vote_average_out_of_5 = episode_data.get("vote_average", 0) / 2 if episode_data.get("vote_average") else None
                            self.overview = episode_data.get("overview", "No synopsis available.")
                            break
                    else:
                        # Episode not found in metadata
                        self.air_date = None
                        self.runtime = None
                        self.vote_average_out_of_5 = None
                        self.overview = "No synopsis available."
                except Exception:
                    # If we can't get metadata, use defaults
                    self.air_date = None
                    self.runtime = None
                    self.vote_average_out_of_5 = None
                    self.overview = "No synopsis available."
                
                # Add fields needed for URL generation
                self.source = episode_instance.item.source
                self.media_type = episode_instance.item.media_type
                self.media_id = episode_instance.item.media_id
                self.season_number = episode_instance.item.season_number
            else:
                # It's already a MockEpisode object, copy its attributes
                # Make sure we have all the attributes
                self.item = episode_instance.item if hasattr(episode_instance, 'item') else None
                self.history = episode_instance.history if hasattr(episode_instance, 'history') else []
                self.image = episode_instance.image if hasattr(episode_instance, 'image') else None
                self.title = episode_instance.title if hasattr(episode_instance, 'title') else None
                self.episode_number = episode_instance.episode_number if hasattr(episode_instance, 'episode_number') else None
                self.air_date = episode_instance.air_date if hasattr(episode_instance, 'air_date') else None
                self.runtime = episode_instance.runtime if hasattr(episode_instance, 'runtime') else None
                self.vote_average_out_of_5 = episode_instance.vote_average_out_of_5 if hasattr(episode_instance, 'vote_average_out_of_5') else None
                self.overview = episode_instance.overview if hasattr(episode_instance, 'overview') else "No synopsis available."
                self.source = episode_instance.source if hasattr(episode_instance, 'source') else None
                self.media_type = episode_instance.media_type if hasattr(episode_instance, 'media_type') else None
                self.media_id = episode_instance.media_id if hasattr(episode_instance, 'media_id') else None
                self.season_number = episode_instance.season_number if hasattr(episode_instance, 'season_number') else None
    
    mock_episode = MockEpisode(episode)
    
    context = {
        "episode": mock_episode,
        "media": {
            "media_type": MediaTypes.SEASON.value,
            "season_number": episode.item.season_number if hasattr(episode, 'item') else episode.season_number,
        },
        "csrf_token": request.META.get('CSRF_COOKIE', ''),
    }
    return render(request, "app/components/media/episode_card.html", context)


@require_POST
def mark_season_watched(request, source, media_type, media_id, season_number):
    """Mark the season's aired episodes watched, undated, as one mark (TV-300, TV-004)."""
    exposure.require_media_type(media_type)
    show_item = _tv_show_item(source, media_id)
    try:
        tv_tracking.mark_watched(
            request.user,
            show_item,
            season_number=season_number,
            entry_point=tv_tracking.WEB,
        )
    except TV_WRITE_ERRORS as error:
        return _tv_error_response(error)
    return _season_actions_response(
        request,
        source,
        media_id,
        season_number,
        message=f"Marked {tv_listing.season_label(season_number)} watched",
    )


@require_POST
def unmark_season_watched(request, source, media_type, media_id, season_number):
    """Undo the filled season eye (TV-302); a log covering it blocks the undo."""
    exposure.require_media_type(media_type)
    show_item = _tv_show_item(source, media_id)
    try:
        tv_tracking.unmark(
            request.user,
            show_item,
            season_number=season_number,
            confirm=True,
            entry_point=tv_tracking.WEB,
        )
    except TV_WRITE_ERRORS as error:
        return _tv_error_response(error)
    return _season_actions_response(
        request,
        source,
        media_id,
        season_number,
        message=f"Marked {tv_listing.season_label(season_number)} unwatched",
        message_type="info",
    )


@require_POST
def add_movie_diary_entry(request, source, media_type, media_id, season_number=None):
    """Create a diary entry for a movie, TV show, or season using media metadata."""
    exposure.require_media_type(media_type)
    try:
        book_instance = None
        if media_type not in [
            MediaTypes.MOVIE.value,
            MediaTypes.TV.value,
            MediaTypes.SEASON.value,
            MediaTypes.BOOK.value,
            MediaTypes.GAME.value,
            MediaTypes.MUSIC.value,
        ]:
            raise Http404(
                "Diary entries are only available for movies, TV shows, seasons, books, games, and music",
            )
            
        logger.info(f"Creating diary entry for {media_type} {media_id} from {source}")
        logger.info(f"POST data: {dict(request.POST)}")
        logger.info(f"Tags in POST: {request.POST.get('tags', 'NOT_FOUND')}")
        logger.info(f"All POST keys: {list(request.POST.keys())}")
        logger.info(f"Season number parameter: {season_number}")
        
        # Get or create the item
        if media_type == MediaTypes.SEASON.value and season_number is not None:
            logger.info(f"Processing season item creation for season {season_number}")
            # For seasons, we need to get the TV show metadata first
            tv_metadata = services.get_media_metadata("tv_with_seasons", media_id, source, [season_number])
            logger.info(f"TV metadata keys: {list(tv_metadata.keys())}")
            season_key = f"season/{season_number}"
            logger.info(f"Looking for season key: {season_key}")
            
            if season_key not in tv_metadata:
                logger.error(f"Season key {season_key} not found in tv_metadata")
                raise ValueError(f"Season {season_number} not found in metadata")
                
            season_metadata = tv_metadata[season_key]
            logger.info(f"Season metadata keys: {list(season_metadata.keys())}")
            logger.info(f"Season title: {season_metadata.get('title', 'NO_TITLE')}")
            
            item, created = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                season_number=season_number,
                defaults={
                    "title": season_metadata["title"],
                    "image": season_metadata["image"],
        },
            )
            logger.info(f"Season item {'created' if created else 'found'}: {item}")
        else:
            metadata = services.get_media_metadata(media_type, media_id, source)
            defaults = {
                "title": metadata["title"],
                "image": metadata["image"],
            }
            if media_type == MediaTypes.BOOK.value:
                defaults["total_pages"] = metadata.get("total_pages") or metadata.get("max_progress")
            item, created = Item.objects.get_or_create(
                media_id=media_id,
                source=source,
                media_type=media_type,
                defaults=defaults,
            )
            if (
                media_type == MediaTypes.BOOK.value
                and not created
                and defaults.get("total_pages")
                and item.total_pages != defaults["total_pages"]
            ):
                item.total_pages = defaults["total_pages"]
                item.save(update_fields=["total_pages"])
        logger.info(f"Item {'created' if created else 'found'}: {item}")
        
        # Create diary entry using the services function
        from app.services import create_diary_entry
        from django.utils.dateparse import parse_date
        
        # Parse form data
        consumed_at = parse_date(request.POST.get('watch_date'))
        is_tv = tv_tracking.supports(media_type)
        if is_tv:
            # TV logs are calendar dates on the user's calendar, never in the future
            # (TV-407); create_diary_entry hands them to app.tv_tracking.
            today = user_calendar.today(request.user)
            consumed_at = consumed_at or today
            if consumed_at > today:
                return JsonResponse({"error": tv_tracking.MSG_FUTURE_LOG}, status=400)
        elif not consumed_at:
            if (single_weight.supports(media_type) or game_tracking.supports(media_type)):
                return JsonResponse({"error": "A consumption date is required."}, status=400)
            consumed_at = timezone.now()
        elif (single_weight.supports(media_type) or game_tracking.supports(media_type)) and consumed_at > timezone.localdate():
            return JsonResponse({"error": "Consumption dates cannot be in the future."}, status=400)
        else:
            # Convert date to datetime at end of day to allow multiple entries per day
            consumed_at = timezone.datetime.combine(consumed_at, timezone.datetime.max.time())
            consumed_at = timezone.make_aware(consumed_at)
            
        rating = request.POST.get('rating')
        if rating and rating.strip():
            rating = float(rating)
            if (single_weight.supports(media_type) or game_tracking.supports(media_type)):
                rating = single_weight.rating_from_wire(rating)
            elif is_tv:
                rating = _tv_web_rating(rating)
        else:
            rating = None
            
        review = request.POST.get('review', '').strip()
        review_title = request.POST.get("review_title", "").strip()
        liked = request.POST.get('liked', '').lower() == 'true'
        is_rewatch = request.POST.get('is_rewatch') == 'on'
        auto_mark_consumed = request.POST.get('auto_mark_consumed') == 'true'
        contains_spoilers = request.POST.get("contains_spoilers") == "on"
        visibility = request.POST.get("visibility", "public")
        if visibility not in {"public", "followers", "private"}:
            visibility = "public"
        
        # Parse tags
        tags_data = request.POST.get('tags', '').strip()
        tag_names = []
        if tags_data:
            tag_names = [tag.strip().lower() for tag in tags_data.split(',') if tag.strip()]
        
        logger.info(f"Parsed data - Date: {consumed_at}, Rating: {rating}, Review: {len(review)} chars, Liked: {liked}, Rewatch: {is_rewatch}, Auto-consume: {auto_mark_consumed}, Tags: {tag_names}")
        
        # Create the diary entry
        logger.info(f"About to create diary entry with auto_mark_consumed={auto_mark_consumed}")
        if game_tracking.supports(item):
            if not request.POST.get("mutation_id"):
                return JsonResponse({"error": "Reopen the completion form and try again."}, status=400)
            progress = {
                name: request.POST[name] or None
                for name in ("total_minutes", "percentage") if name in request.POST
            }
            _, entry = game_tracking.complete(
                request.user, item, completion_date=consumed_at,
                mutation_id=request.POST["mutation_id"],
                playthrough_id=int(request.POST["playthrough_id"]) if request.POST.get("playthrough_id") else None,
                rating=rating, liked=liked, review=review, review_title=review_title,
                contains_spoilers=contains_spoilers, is_rewatch=is_rewatch,
                tags=tag_names, **progress,
            )
        else:
            # A TV log counts its target watched whatever auto_mark_consumed says, and
            # the log modal's mutation ID applies it at most once (TV-400, TV-705).
            tv_kwargs = {"mutation_id": request.POST.get("mutation_id") or None} if is_tv else {}
            try:
                entry = create_diary_entry(
                    user=request.user,
                    item=item,
                    consumed_at=consumed_at,
                    rating=rating,
                    review=review,
                    liked=liked,
                    is_rewatch=is_rewatch,
                    auto_mark_consumed=auto_mark_consumed,
                    tags=tag_names,
                    review_title=review_title,
                    contains_spoilers=contains_spoilers,
                    visibility=visibility,
                    **tv_kwargs,
                )
            except TV_WRITE_ERRORS as error:
                return _tv_error_response(error)

        logger.info(f"Diary entry created successfully: {entry}")
        
        if media_type == MediaTypes.BOOK.value:
            completion_datetime = consumed_at or timezone.now()
            from decimal import Decimal

            book_defaults = {
                "status": Status.COMPLETED.value,
                "end_date": completion_datetime,
            }
            book_instance, created_book = Book.objects.get_or_create(
                item=item,
                user=request.user,
                defaults=book_defaults,
            )
            logger.info(f"Book instance {'created' if created_book else 'found'}: {book_instance}")

            book_instance.status = Status.COMPLETED.value
            if not book_instance.start_date:
                book_instance.start_date = completion_datetime
            book_instance.end_date = completion_datetime

            if rating is not None:
                book_instance.score = Decimal(str(rating))
            if review:
                book_instance.notes = review

            book_instance.completed_manually = False
            book_instance.save(update_fields=["status", "end_date", "completed_manually"])
            logger.info(f"Book {book_instance} marked as completed for diary entry")
            if request.POST.get('book_complete', '').lower() in ('true', '1', 'yes', 'on'):
                book_instance.completion_diary_entry = entry
                book_instance.completed_manually = False
                book_instance.save(update_fields=['completion_diary_entry', 'completed_manually'])

        # Return success response
        return JsonResponse({"success": True, "entry_id": entry.id})
        
    except Exception as e:
        logger.error(f"Error creating diary entry: {str(e)}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=400)


@require_GET
def edit_diary_entry(request, entry_id):
    """Show edit modal for a diary entry."""
    try:
        # Get entry with all necessary relationships
        entry = get_object_or_404(
            DiaryEntry.objects.select_related('item', 'user').prefetch_related('tags'),
            id=entry_id,
            user=request.user
        )
        
        # Ensure entry.item exists
        if not entry.item:
            raise ValueError(f"Diary entry {entry_id} has no associated item")
        
        # Prepare initial data
        initial_data = {
            'rating': (
                single_weight.rating_to_wire(entry.rating)
                if (
                    single_weight.supports(entry.item)
                    or game_tracking.supports(entry.item)
                    or tv_tracking.supports(entry.item)
                ) and entry.rating is not None
                else entry.rating
            ),
            'review': entry.review or '',
        }
        
        # Handle consumed_at based on TRACK_TIME setting
        if settings.TRACK_TIME:
            initial_data['consumed_at'] = entry.consumed_at
        else:
            # Convert datetime to date if not tracking time
            if hasattr(entry.consumed_at, 'date'):
                initial_data['consumed_at'] = entry.consumed_at.date()
            else:
                initial_data['consumed_at'] = entry.consumed_at
        
        # Create form
        form = DiaryEntryForm(
            initial=initial_data,
            user=request.user,
            item=entry.item
        )
        
        # Manually set tags initial value
        existing_tags = list(entry.tags.all())
        if existing_tags:
            form.initial["tags"] = ", ".join([tag.name for tag in existing_tags])
        else:
            form.initial["tags"] = ""
        
        context = {
            'entry': entry,
            'form': form,
            'user': request.user,
            'display_rating': initial_data['rating'],
        }
        
        return render(request, 'app/components/edit_diary_modal.html', context)
    except Exception as e:
        logger.error(f"Error in edit_diary_entry: {str(e)}", exc_info=True)
        import traceback
        import json
        error_details = {
            'error': str(e),
            'traceback': traceback.format_exc()
        }
        # Return JSON that JavaScript can parse and display
        return JsonResponse(error_details, status=500)


@require_POST
def update_diary_entry(request, entry_id):
    """Update a diary entry."""
    try:
        entry = get_object_or_404(DiaryEntry, id=entry_id, user=request.user)
        
        # Debug: Log the POST data
        logger.info(f"Update diary entry {entry_id} - POST data: {dict(request.POST)}")
        
        # An omitted date preserves history; an explicit game date must be valid.
        raw_date = request.POST.get("watch_date")
        consumed_at = parse_date(raw_date) if raw_date is not None else None
        is_tv = tv_tracking.supports(entry.item)
        if game_tracking.supports(entry.item) and raw_date is not None and not consumed_at:
            return JsonResponse({"error": "A valid completion date is required."}, status=400)
        if is_tv:
            # A TV log's date is a plain calendar date on the user's calendar, never in
            # the future; an omitted date leaves the log's date alone (TV-405, TV-407).
            if consumed_at and consumed_at > user_calendar.today(request.user):
                return JsonResponse({"error": tv_tracking.MSG_FUTURE_LOG}, status=400)
        elif not consumed_at:
            # Keep the original datetime to preserve ordering.
            consumed_at = entry.consumed_at
        elif (single_weight.supports(entry.item) or game_tracking.supports(entry.item)) and consumed_at > timezone.localdate():
            return JsonResponse({"error": "Consumption dates cannot be in the future."}, status=400)
        else:
            # Convert date to datetime at end of day to allow multiple entries per day
            consumed_at = timezone.datetime.combine(consumed_at, timezone.datetime.max.time())
            consumed_at = timezone.make_aware(consumed_at)
            
        rating = request.POST.get('rating')
        if rating and rating.strip():
            rating = float(rating)
            if (single_weight.supports(entry.item) or game_tracking.supports(entry.item)):
                rating = single_weight.rating_from_wire(rating)
            elif is_tv:
                rating = _tv_web_rating(rating)
        else:
            rating = None
            
        review = request.POST.get('review', '').strip()
        review_title = request.POST.get("review_title", "").strip()
        liked = request.POST.get('liked', '').lower() == 'true'
        is_rewatch = request.POST.get('is_rewatch') == 'on'  # Checkbox value
        contains_spoilers = request.POST.get("contains_spoilers") == "on"
        visibility = request.POST.get("visibility", "public")
        if visibility not in {"public", "followers", "private"}:
            visibility = "public"
        
        # Parse tags
        tags_data = request.POST.get('tags', '').strip()
        tag_names = []
        if tags_data:
            tag_names = [tag.strip().lower() for tag in tags_data.split(',') if tag.strip()]
        
        logger.info(f"Parsed data - Date: {consumed_at}, Rating: {rating}, Review: '{review}', Liked: {liked}, Rewatch: {is_rewatch}, Tags: {tag_names}")
        
        from app.services import update_diary_entry as update_diary_entry_service

        update_data = {
            "consumed_at": consumed_at,
            "rating": rating,
            "review": review,
            "review_title": review_title,
            "liked": liked,
            "is_rewatch": is_rewatch,
            "contains_spoilers": contains_spoilers,
            "visibility": visibility,
            **{
                name: request.POST[name] or None
                for name in ("total_minutes", "percentage")
                if game_tracking.supports(entry.item) and name in request.POST
            },
        }
        if is_tv and not consumed_at:
            update_data.pop("consumed_at")
        try:
            update_diary_entry_service(entry, update_data, tags=tag_names)
        except TV_WRITE_ERRORS as error:
            return _tv_error_response(error)
        
        logger.info(f"Diary entry updated successfully: {entry}")
        logger.info(f"Updated values - Date: {entry.consumed_at}, Rating: {entry.rating}, Review: '{entry.review}', Liked: {entry.liked}")
        
        # Return updated diary entries HTML - use same logic as diary_list to preserve order and filters
        # Get filters from POST data (form submission) or query params
        media_type = request.POST.get("current_media_type") or request.GET.get("media_type", "")
        year = request.POST.get("current_year") or request.GET.get("year")
        page = request.POST.get("current_page") or request.GET.get("page", 1)
        
        # Base queryset - order by created_at descending (newest first) to match diary_list
        entries = DiaryEntry.objects.filter(user=request.user).select_related('item').prefetch_related('tags').order_by('-created_at')
        
        # Apply filters
        if media_type:
            entries = entries.filter(item__media_type=media_type)
        
        if year:
            try:
                year = int(year)
                entries = entries.filter(consumed_at__year=year)
            except ValueError:
                pass
        
        # Get unique years for filter dropdown
        years = entries.dates("consumed_at", "year", order="DESC")
        
        # Paginate
        paginator = Paginator(entries, 25)  # 25 entries per page
        page_obj = paginator.get_page(page)
        
        context = {
            "entries": page_obj,
            "years": years,
            "current_year": year,
            "current_media_type": media_type,
            "media_type_choices": exposure.media_type_choices(),
        }
        
        return render(request, "app/components/diary_entries.html", context)
        
    except Exception as e:
        logger.error(f"Error updating diary entry: {str(e)}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=400)


@require_POST
def delete_diary_entry(request, entry_id):
    """Delete a diary entry."""
    try:
        entry = get_object_or_404(DiaryEntry, id=entry_id, user=request.user)
        from app.services import delete_diary_entry as delete_diary_entry_service

        delete_diary_entry_service(request.user, entry)
        
        # Return success response
        return JsonResponse({"success": True})
        
    except Exception as e:
        logger.error(f"Error deleting diary entry: {str(e)}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=400)


@require_GET
def tag_autocomplete(request):
    """Return tag suggestions for autocomplete."""
    query = request.GET.get('q', '').strip().lower()
    if len(query) < 1:
        return JsonResponse({'tags': []})
    
    try:
        from app.models import Tag
        tags = Tag.objects.filter(name__icontains=query).order_by('-usage_count', 'name')[:10]
        
        return JsonResponse({
            'tags': [{'name': tag.name, 'usage_count': tag.usage_count} for tag in tags]
        })
    except Exception as e:
        # If Tag model doesn't exist yet, return empty results
        return JsonResponse({'tags': []})


@require_POST
def mark_book_read(request, source, media_id):
    """Mark a book as read by creating a Book instance with completed status."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        item, created_item = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                "total_pages": total_pages,
            },
        )
        if not created_item and total_pages and item.total_pages != total_pages:
            item.total_pages = total_pages
            item.save(update_fields=["total_pages"])
        
        from app import book_tracking

        book_instance = book_tracking.mark_read(request.user, item)
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": book_instance,
            "diary_entries": diary_entries,
        }
        
        return render(request, "app/components/media_actions.html", context)
        
    except Exception as e:
        logger.error(f"Error marking book as read: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def start_reading_book(request, source, media_id):
    """Start reading a book by creating a Book instance with in-progress status."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        item, created_item = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                "total_pages": total_pages,
            },
        )
        if not created_item and total_pages and item.total_pages != total_pages:
            item.total_pages = total_pages
            item.save(update_fields=["total_pages"])
        
        from app import book_tracking

        book_instance = book_tracking.start_journey(request.user, item)
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": book_instance,
            "diary_entries": diary_entries,
        }
        
        return render(request, "app/components/media_actions.html", context)
        
    except Exception as e:
        logger.error(f"Error starting to read book: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def log_book_progress(request, source, media_id):
    """Log reading progress for a book."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
            
        form = BookProgressForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"error": "Invalid form data", "details": form.errors}, status=400)
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        item, created_item = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                "total_pages": total_pages,
            },
        )
        if not created_item and total_pages and item.total_pages != total_pages:
            item.total_pages = total_pages
            item.save(update_fields=["total_pages"])
        
        from app import book_tracking

        book_instance = book_tracking.update_progress(
            request.user,
            item,
            progress_type=form.cleaned_data['progress_type'],
            value=form.cleaned_data['progress_value'],
        )
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": book_instance,
            "diary_entries": diary_entries,
        }
        
        return render(request, "app/components/media_actions.html", context)
        
    except Exception as e:
        logger.error(f"Error logging book progress: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def log_book_completed(request, source, media_id):
    """Log a completed book with rating and review."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
            
        form = BookLogForm(request.POST)
        if not form.is_valid():
            return JsonResponse({"error": "Invalid form data", "details": form.errors}, status=400)
            
        # Get or create the item
        metadata = services.get_media_metadata(media_type, media_id, source)
        total_pages = metadata.get("total_pages") or metadata.get("max_progress")
        item, created_item = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
                "total_pages": total_pages,
            },
        )
        if not created_item and total_pages and item.total_pages != total_pages:
            item.total_pages = total_pages
            item.save(update_fields=["total_pages"])
        
        from uuid import uuid4

        from app import book_tracking

        book_instance, _ = book_tracking.complete(
            request.user,
            item,
            completion_date=form.cleaned_data.get('end_date') or timezone.localdate(),
            rating=form.cleaned_data.get('score'),
            review=form.cleaned_data.get('notes', ''),
            mutation_id=uuid4(),
        )
        
        # Get diary entries for this media
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Return updated action buttons for HTMX to swap  
        # Add required fields to metadata for template compatibility
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": book_instance,
            "diary_entries": diary_entries,
        }
        
        return render(request, "app/components/media_actions.html", context)
        
    except Exception as e:
        logger.error(f"Error logging completed book: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def book_progress_modal(request, source, media_id):
    """Return the book progress modal template."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
        # Get the item for context
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type,
        )
        context = {
            'media': item,
        }
        return render(request, "app/components/book_progress_modal.html", context)
    except Item.DoesNotExist:
        raise Http404("Book not found")


@require_GET
def book_completed_modal(request, source, media_id):
    """Return the book completed modal template."""
    try:
        media_type = MediaTypes.BOOK.value  # Set media_type to BOOK since this is book-specific
        # Get the item for context
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type,
        )
        context = {
            'media': item,
        }
        return render(request, "app/components/book_completed_modal.html", context)
    except Item.DoesNotExist:
        raise Http404("Book not found")


def _tv_choice_response(request, source, media_type, media_id, status, message):
    """Pause, resume or drop a show: a choice about the whole show (TV-007, TV-204)."""
    if media_type == MediaTypes.SEASON.value:
        # Seasons have no status actions of their own (TV-204, TV-207).
        return JsonResponse({"error": MSG_SEASON_STATUS}, status=409)
    metadata = services.get_media_metadata(media_type, media_id, source)
    show_item = _tv_show_item(source, media_id, metadata)
    try:
        tv_tracking.choose(request.user, show_item, status, entry_point=tv_tracking.WEB)
    except TV_WRITE_ERRORS as error:
        return _tv_error_response(error)
    return _tv_actions_response(request, metadata, show_item, message=message)


@require_POST
def pause_media(request, source, media_type, media_id):
    """Pause a media item by setting its status to PAUSED."""
    exposure.require_media_type(media_type)
    if media_type == MediaTypes.GAME.value:
        return _game_action_response(request, source, media_id, game_tracking.pause)
    if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        return _tv_choice_response(
            request, source, media_type, media_id, Status.PAUSED.value, "Media paused",
        )
    try:
        # Movies don't support pause/drop
        if media_type == MediaTypes.MOVIE.value:
            return JsonResponse({"error": "Movies do not support pause/drop"}, status=400)
        
        # Get metadata
        metadata = services.get_media_metadata(media_type, media_id, source)
        
        # Get or create the item
        item, _ = Item.objects.get_or_create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            defaults={
                "title": metadata["title"],
                "image": metadata["image"],
            },
        )
        
        # Get the media model
        model = apps.get_model(app_label="app", model_name=media_type)
        
        # Get the media instance
        try:
            media_instance = model.objects.get(
                item=item,
                user=request.user
            )
        except model.DoesNotExist:
            # Can't pause media that hasn't been started
            return JsonResponse({"error": "Cannot pause media that hasn't been started"}, status=400)
        
        # Update status to PAUSED through the canonical book journey service.
        if media_type == MediaTypes.BOOK.value:
            from app import book_tracking

            media_instance = book_tracking.pause_journey(request.user, item)
        elif media_instance.status == Status.IN_PROGRESS.value:
            media_instance.status = Status.PAUSED.value
            # Preserve start_date and progress
            media_instance.save()
        elif media_instance.status == Status.PAUSED.value:
            # Already paused, no change needed
            pass
        else:
            # Can't pause from other statuses
            return JsonResponse({"error": f"Cannot pause media with status {media_instance.status}"}, status=400)
        
        # Get diary entries
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Prepare context
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": media_instance,
            "diary_entries": diary_entries,
        }
        
        response = render(request, "app/components/media_actions.html", context)
        response["X-Notification-Message"] = "Media paused"
        response["X-Notification-Type"] = "success"
        return response
        
    except Exception as e:
        logger.error(f"Error pausing media: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def resume_media(request, source, media_type, media_id):
    """Resume a paused or dropped media item by setting its status to IN_PROGRESS."""
    exposure.require_media_type(media_type)
    if media_type == MediaTypes.GAME.value:
        return _game_action_response(request, source, media_id, game_tracking.resume)
    if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        return _tv_choice_response(
            request, source, media_type, media_id, Status.IN_PROGRESS.value, "Media resumed",
        )
    try:
        # Movies don't support pause/drop
        if media_type == MediaTypes.MOVIE.value:
            return JsonResponse({"error": "Movies do not support pause/drop"}, status=400)
        
        # Get metadata
        metadata = services.get_media_metadata(media_type, media_id, source)
        
        # Get the item
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type
        )
        
        # Get the media model
        model = apps.get_model(app_label="app", model_name=media_type)
        
        # Get the media instance
        media_instance = model.objects.get(
            item=item,
            user=request.user
        )
        
        # Resume books through the canonical journey service so Paused resumes
        # in place while a DNF begins a fresh zero-progress journey.
        if media_type == MediaTypes.BOOK.value:
            from app import book_tracking

            if media_instance.status == Status.PAUSED.value:
                media_instance = book_tracking.resume_journey(request.user, item)
            elif media_instance.status == Status.DROPPED.value:
                media_instance = book_tracking.start_journey(request.user, item)
            else:
                return JsonResponse({"error": f"Cannot resume media with status {media_instance.status}"}, status=400)
        elif media_instance.status in [Status.PAUSED.value, Status.DROPPED.value]:
            media_instance.status = Status.IN_PROGRESS.value
            # Ensure start_date is set if missing
            if not media_instance.start_date:
                media_instance.start_date = timezone.now()
            media_instance.save()
        else:
            return JsonResponse({"error": f"Cannot resume media with status {media_instance.status}"}, status=400)
        
        # Handle game-specific logic
        if media_type == MediaTypes.GAME.value:
            # Clear end_date if it was set (game was dropped, not completed)
            if media_instance.end_date:
                media_instance.end_date = None
                media_instance.save(update_fields=["end_date"])
        
        # Get diary entries
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Prepare context
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": media_instance,
            "diary_entries": diary_entries,
        }
        
        response = render(request, "app/components/media_actions.html", context)
        response["X-Notification-Message"] = "Media resumed"
        response["X-Notification-Type"] = "success"
        return response
        
    except Item.DoesNotExist:
        return JsonResponse({"error": "Media item not found"}, status=404)
    except Http404:
        return JsonResponse({"error": "Media instance not found"}, status=404)
    except Exception as e:
        logger.error(f"Error resuming media: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)


@require_POST
def drop_media(request, source, media_type, media_id):
    """Drop a media item by setting its status to DROPPED."""
    exposure.require_media_type(media_type)
    if media_type == MediaTypes.GAME.value:
        return _game_action_response(request, source, media_id, game_tracking.drop)
    if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        return _tv_choice_response(
            request, source, media_type, media_id, Status.DROPPED.value, "Media dropped",
        )
    try:
        # Movies don't support pause/drop
        if media_type == MediaTypes.MOVIE.value:
            return JsonResponse({"error": "Movies do not support pause/drop"}, status=400)
        
        # Get metadata
        metadata = services.get_media_metadata(media_type, media_id, source)
        
        # Get the item
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type
        )
        
        # Get the media model
        model = apps.get_model(app_label="app", model_name=media_type)
        
        # Get the media instance
        media_instance = model.objects.get(
            item=item,
            user=request.user
        )
        
        # Drop books through the canonical journey service to retain progress.
        if media_type == MediaTypes.BOOK.value:
            from app import book_tracking

            media_instance = book_tracking.drop_journey(request.user, item)
        elif media_instance.status in [Status.IN_PROGRESS.value, Status.PAUSED.value]:
            media_instance.status = Status.DROPPED.value
            media_instance.save()
        elif media_instance.status == Status.DROPPED.value:
            # Already dropped, no change needed
            pass
        else:
            return JsonResponse({"error": f"Cannot drop media with status {media_instance.status}"}, status=400)
        
        # Handle game-specific logic (already handled in Game.save() but ensure end_date is cleared)
        if media_type == MediaTypes.GAME.value:
            if media_instance.end_date:
                media_instance.end_date = None
                media_instance.save(update_fields=["end_date"])
        
        # Get diary entries
        diary_entries = DiaryEntry.objects.filter(user=request.user, item=item).order_by('-consumed_at')
        
        # Prepare context
        metadata["media_type"] = media_type
        metadata["source"] = source
        metadata["media_id"] = media_id
        
        context = {
            "media": metadata,
            "media_type": media_type,
            "current_instance": media_instance,
            "diary_entries": diary_entries,
        }
        
        response = render(request, "app/components/media_actions.html", context)
        response["X-Notification-Message"] = "Media dropped"
        response["X-Notification-Type"] = "success"
        return response
        
    except Item.DoesNotExist:
        return JsonResponse({"error": "Media item not found"}, status=404)
    except Http404:
        return JsonResponse({"error": "Media instance not found"}, status=404)
    except Exception as e:
        logger.error(f"Error dropping media: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JsonResponse({"error": str(e)}, status=500)


@require_GET
def service_worker():
    """Serve the service worker file."""
    sw_path = Path(settings.STATICFILES_DIRS[0]) / "js" / "serviceworker.js"
    with sw_path.open() as f:
        response = HttpResponse(f.read(), content_type="application/javascript")
        response["Service-Worker-Allowed"] = "/"
        return response
