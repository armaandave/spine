from contextlib import suppress
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from api.exceptions import (
    BookTrackingConflict,
    DiaryHistoryConflict,
    TVDetailsUnavailableError,
    TVValidationError,
    tv_api_error,
)
from api.serializers.common import (
    get_or_create_item_from_metadata,
    progress_for_media,
    tracking_state,
)
from api.serializers.tracking import validate_tv_rating
from app import book_tracking, game_tracking, single_weight, tv_listing, tv_tracking
from app.models import TV, BasicMedia, Book, Item, MediaTypes, Season, Sources, Status
from app.providers import services as provider_services
from social.models import Activity, ProgressChange

TV_ERRORS = (
    tv_tracking.TVConflict,
    tv_tracking.TVInvalid,
    tv_listing.InvalidEpisode,
    tv_listing.TVDetailsUnavailable,
)
# Every action of POST tracking/{source}/tv/{id}/actions/{action}/ (plan 2.3.7).
TV_ACTIONS = frozenset(
    {
        "watching",
        "resume",
        "planning",
        "pause",
        "drop",
        "rewatch",
        "stop_rewatch",
        "remove",
        "mark_watched",
        "consume",
        "unmark",
        "mark_up_to",
        "mark_earlier",
        "tick",
        "untick",
        "rate",
    },
)
# The actions that existed for every media type before the TV contract; their responses
# keep the top-level TrackingState installed clients decode (TV-900).
TV_PRE_CONTRACT_ACTIONS = frozenset({"consume", "pause", "resume", "drop"})
TV_CHOICE_ACTIONS = {
    "watching": Status.IN_PROGRESS.value,
    "resume": Status.IN_PROGRESS.value,
    "planning": Status.PLANNING.value,
    "pause": Status.PAUSED.value,
    "drop": Status.DROPPED.value,
}
TV_EPISODE_ACTIONS = frozenset({"tick", "untick", "mark_up_to"})


def get_tracking(user, *, source, media_type, media_id, season_number=None, episode_number=None):
    """Return a tracked media instance or None."""
    return BasicMedia.objects.filter_media_prefetch(
        user,
        media_id,
        media_type,
        source,
        season_number,
        episode_number,
    ).first()


def create_or_update_tracking(user, *, source, media_type, media_id, data, partial=True):
    """Create or update tracking using existing model behavior.

    TV shows and seasons go through tv_tracking.apply_written_state and return its
    WriteResult (TV-207): a status means its action, stars follow TV-303 and TV-304,
    and progress or date fields, a season Planning, Paused or Dropped, or a season
    without season_number are invalid requests.
    """
    if tv_tracking.supports(media_type):
        return write_tv_tracking(
            user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            data=data,
        )
    season_number = data.get("season_number")
    media = get_tracking(
        user,
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
    )
    existing_media = media is not None
    previous_progress = _progress_snapshot(media) if existing_media and "progress" in data else None
    if media is None and media_type == MediaTypes.GAME.value:
        media = apps.get_model("app", media_type)(item=_game_item(user, source, media_id), user=user)
    elif media is None:
        metadata = provider_services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number] if season_number is not None else None,
        )
        item = get_or_create_item_from_metadata(
            {
                "source": source,
                "media_type": media_type,
                "media_id": media_id,
                "season_number": season_number,
                "episode_number": None,
            },
            metadata,
        )
        model = apps.get_model("app", media_type)
        media = model(item=item, user=user)

    if single_weight.supports(media_type):
        return _write_single_weight_tracking(
            user,
            media if existing_media else None,
            media.item,
            data,
        )

    if media_type == MediaTypes.GAME.value:
        return _write_game_tracking(user, media.item, data)

    if media_type == MediaTypes.BOOK.value:
        return _write_book_tracking(
            user,
            media.item,
            data,
        )

    for api_field, model_field in {
        "status": "status",
        "rating": "score",
        "progress": "progress",
        "start_date": "start_date",
        "end_date": "end_date",
        "notes": "notes",
    }.items():
        if api_field in data and not (
            api_field == "progress" and media_type == MediaTypes.TV.value
        ):
            setattr(media, model_field, data[api_field])
    media.save()
    if previous_progress is not None:
        _record_progress_change(user, media, previous_progress)
    return media


def delete_tracking(
    user,
    *,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
    expected_version=None,
    mutation_id=None,
):
    """Delete tracked media if it exists.

    A show's delete is Remove from Library (TV-003, refused while logs exist); a
    season's removes its viewings and marks (TV-207, refused while logs exist, 400
    without season_number); an episode's unticks its latest unlogged viewing (TV-102).
    """
    if tv_tracking.supports(media_type):
        return delete_tv_tracking(
            user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
            expected_version=expected_version,
            mutation_id=mutation_id,
        )
    media = get_tracking(
        user,
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=season_number,
    )
    if media is not None:
        if media_type == MediaTypes.GAME.value:
            return _call_book(game_tracking.remove_tracking, user, media.item)
        if media_type == MediaTypes.BOOK.value:
            return _call_book(book_tracking.remove_tracking, user, media.item)
        if single_weight.supports(media_type):
            try:
                single_weight.unwatch(user, media.item)
            except single_weight.DiaryHistoryExists as error:
                raise DiaryHistoryConflict from error
        else:
            media.delete()
    return None


def consume_media(
    user,
    *,
    source,
    media_type,
    media_id,
    consumed_at=None,
    season_number=None,
    episode_number=None,
):
    """Mark media consumed/completed.

    A TV show or season is marked watched, undated (TV-300; fixes T16); an episode is
    ticked (TV-100). Both return tv_tracking's WriteResult.
    """
    if tv_tracking.supports(media_type):
        item = resolve_tv_item(
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
        )
        show_item = tv_show_item(source, media_id)
        if media_type == MediaTypes.EPISODE.value:
            return _call_tv(
                tv_tracking.tick,
                user,
                show_item,
                item.season_number,
                item.episode_number,
                entry_point=tv_tracking.LEGACY_APP,
            )
        return _call_tv(
            tv_tracking.mark_watched,
            user,
            show_item,
            season_number=item.season_number,
            entry_point=tv_tracking.LEGACY_APP,
        )
    media = get_tracking(user, source=source, media_type=media_type, media_id=media_id)
    if media is None:
        media = create_or_update_tracking(
            user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            data={"status": Status.COMPLETED.value},
        )
    if media_type == MediaTypes.GAME.value:
        return _call_book(game_tracking.mark_completed, user, media.item)
    if single_weight.supports(media_type):
        return single_weight.mark_consumed(user, media.item)
    if media_type == MediaTypes.BOOK.value:
        return _call_book(book_tracking.mark_read, user, media.item)
    media.end_date = consumed_at or timezone.now()
    media.mark_consumed()
    return media


def set_status(user, *, source, media_type, media_id, status, season_number=None):
    """Set status on an existing or new tracked item.

    For TV the status means its action (TV-207): a show's Paused or Dropped is that
    choice, In progress chooses Watching; a season's Paused or Dropped is a 400.
    """
    data = {"status": status}
    if season_number is not None:
        data["season_number"] = season_number
    media = create_or_update_tracking(
        user,
        source=source,
        media_type=media_type,
        media_id=media_id,
        data=data,
    )
    return media


def watch_episode(user, *, source, media_id, season_number, episode_number, watched_at=None):
    """Tick one TV episode from the installed builds' endpoint (TV-100, TV-101).

    watched_at is accepted and ignored: the server dates the tick. Returns the
    WriteResult; the response stays the season's TrackingState.
    """
    return _call_tv(
        tv_tracking.tick,
        user,
        tv_show_item(source, media_id),
        season_number,
        episode_number,
        entry_point=tv_tracking.LEGACY_APP,
    )


def unwatch_episode(user, *, source, media_id, season_number, episode_number):
    """Untick an episode: remove its latest recorded unlogged viewing (TV-102)."""
    show_item = tv_show_item(source, media_id, create=False)
    if show_item is None:
        return None
    return _call_tv(
        tv_tracking.untick,
        user,
        show_item,
        season_number,
        episode_number,
        entry_point=tv_tracking.LEGACY_APP,
    )


def watch_season(user, *, source, media_id, season_number):
    """Mark a season's aired episodes watched, undated, as one mark (TV-300)."""
    return _call_tv(
        tv_tracking.mark_watched,
        user,
        tv_show_item(source, media_id),
        season_number=season_number,
        entry_point=tv_tracking.LEGACY_APP,
    )


def unwatch_season(user, *, source, media_id, season_number):
    """Undo a season's filled eye under TV-302 (this endpoint was always confirmed)."""
    show_item = tv_show_item(source, media_id, create=False)
    if show_item is None:
        return None
    return _call_tv(
        tv_tracking.unmark,
        user,
        show_item,
        season_number=season_number,
        confirm=True,
        entry_point=tv_tracking.LEGACY_APP,
    )


def log_book_progress(
    user,
    *,
    source,
    media_id,
    progress_type,
    value,
    notes="",
    progressed_on=None,
):
    """Log a book reading session."""
    book = get_tracking(user, source=source, media_type=MediaTypes.BOOK.value, media_id=media_id)
    if book is None:
        book = create_or_update_tracking(
            user,
            source=source,
            media_type=MediaTypes.BOOK.value,
            media_id=media_id,
            data={"status": Status.IN_PROGRESS.value},
        )
    if isinstance(book, Book):
        book = _call_book(
            book_tracking.update_progress,
            user,
            book.item,
            progress_type=progress_type,
            value=value,
            notes=notes,
            progressed_on=progressed_on,
        )
        book.refresh_from_db()
    return book


def perform_book_action(user, *, source, media_id, action, data):
    """Apply one canonical book status or journey action."""
    book = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.BOOK.value,
        media_id=media_id,
    )
    item = book.item if book is not None else _materialize_book_item(source, media_id)
    functions = {
        "mark_read": (book_tracking.mark_read, set()),
        "undo_read": (book_tracking.undo_read, set()),
        "delete_undated_read": (book_tracking.delete_undated_read, set()),
        "pause": (book_tracking.pause_journey, set()),
        "resume": (book_tracking.resume_journey, {"start_date", "mutation_id"}),
        "drop": (book_tracking.drop_journey, {"end_date"}),
        "restart": (
            book_tracking.restart_journey,
            {"start_date", "end_date", "mutation_id"},
        ),
        "start": (book_tracking.start_journey, {"start_date", "mutation_id"}),
    }
    transition = functions.get(action)
    if transition is None:
        raise serializers.ValidationError({"action": "Unsupported book action."})
    function, accepted = transition
    kwargs = {
        key: value
        for key, value in data.items()
        if key in accepted and value is not None
    }
    return _call_book(function, user, item, **kwargs)


def complete_book(user, *, source, media_id, data):
    """Atomically complete a book journey and its diary entry."""
    book = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.BOOK.value,
        media_id=media_id,
    )
    item = book.item if book is not None else _materialize_book_item(source, media_id)
    payload = dict(data)
    payload["rating"] = single_weight.rating_from_wire(payload.get("rating"))
    return _call_book(book_tracking.complete, user, item, **payload)


def update_book_journey(user, *, source, media_id, journey_id, data):
    """Edit a book journey's user calendar dates."""
    book = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.BOOK.value,
        media_id=media_id,
    )
    if book is None:
        raise serializers.ValidationError({"journey": "Book tracking does not exist."})
    return _call_book(
        book_tracking.update_journey,
        user,
        book.item,
        journey_id,
        **dict(data),
    )


def delete_book_journey(user, *, source, media_id, journey_id):
    """Delete one book journey without touching unrelated history."""
    book = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.BOOK.value,
        media_id=media_id,
    )
    if book is None:
        raise serializers.ValidationError({"journey": "Book tracking does not exist."})
    return _call_book(book_tracking.delete_journey, user, book.item, journey_id)


def serialize_tracking(media, *, tv=None, tv_detail=False):
    """Serialize tracking state after annotating max progress when possible.

    TV shows and seasons read their progress from tv_tracking (TV-104), never from
    calendar events. tv is a TVShowState to attach; tv_detail computes the detail one.
    """
    if media is None:
        return None
    tv_counted = media.item.media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value)
    if not tv_counted and not hasattr(media, "max_progress"):
        with suppress(Exception):
            BasicMedia.objects.annotate_max_progress([media], media.item.media_type)
    return tracking_state(media, tv=tv, tv_detail=tv_detail)


def _write_single_weight_tracking(user, media, item, data):
    """Apply direct single-weight tracking and rating mutations."""
    try:
        rating = single_weight.UNSET
        if "rating" in data:
            rating = single_weight.rating_from_wire(data["rating"])
        media = single_weight.apply_tracking_state(
            user,
            item,
            status=data.get("status", single_weight.UNSET),
            rating=rating,
            start_date=data.get("start_date", single_weight.UNSET),
            notes=data.get("notes", single_weight.UNSET),
        )
    except DjangoValidationError as error:
        raise serializers.ValidationError({"rating": error.messages[0]}) from error
    return media


def _materialize_book_item(source, media_id):
    """Resolve an Item without inventing a Book tracking row."""
    metadata = provider_services.get_media_metadata(
        MediaTypes.BOOK.value,
        media_id,
        source,
    )
    return get_or_create_item_from_metadata(
        {
            "source": source,
            "media_type": MediaTypes.BOOK.value,
            "media_id": media_id,
            "season_number": None,
            "episode_number": None,
        },
        metadata,
    )


def _write_book_tracking(user, item, data):
    """Route generic tracking writes through canonical book transitions."""
    try:
        rating = book_tracking.UNSET
        if "rating" in data:
            rating = single_weight.rating_from_wire(data["rating"])
        book = _call_book(
            book_tracking.apply_tracking_state,
            user,
            item,
            status=data.get("status", book_tracking.UNSET),
            rating=rating,
            start_date=data.get("start_date", book_tracking.UNSET),
            notes=data.get("notes", book_tracking.UNSET),
            mutation_id=data.get("mutation_id"),
        )
        if "progress" in data:
            book = _call_book(
                book_tracking.update_progress,
                user,
                item,
                progress_type="pages",
                value=data["progress"],
            )
    except DjangoValidationError as error:
        raise serializers.ValidationError({"detail": error.messages[0]}) from error
    return book


def _call_book(function, *args, **kwargs):
    """Translate domain validation/conflicts into stable API errors."""
    try:
        return function(*args, **kwargs)
    except book_tracking.BookTrackingConflict as error:
        api_error = BookTrackingConflict(detail=error.message)
        api_error.default_code = error.code
        raise api_error from error
    except DjangoValidationError as error:
        if hasattr(error, "message_dict"):
            raise serializers.ValidationError(error.message_dict) from error
        raise serializers.ValidationError({"detail": error.messages[0]}) from error


def _progress_snapshot(media):
    """Return the API progress shape for a tracked media item."""
    if media is None:
        return None
    if not hasattr(media, "max_progress"):
        with suppress(Exception):
            BasicMedia.objects.annotate_max_progress([media], media.item.media_type)
    return _jsonable(progress_for_media(media))


def _record_progress_change(user, media, previous_progress):
    """Record a durable progress delta and feed activity.

    Never for TV: tv_tracking records season progress changes without feed activity
    (TV-703).
    """
    if media is None or previous_progress is None or tv_tracking.supports(media.item):
        return None
    current_progress = _progress_snapshot(media)
    if not current_progress or previous_progress == current_progress:
        return None
    change = ProgressChange.objects.create(
        actor=user,
        item=media.item,
        previous_progress=previous_progress,
        current_progress=current_progress,
    )
    Activity.objects.create(
        actor=user,
        verb="progress_updated",
        target_type="progress_change",
        target_id=change.id,
        item=media.item,
        snapshot={
            "previous": previous_progress,
            "current": current_progress,
        },
    )
    return change


def _jsonable(value):
    """Convert progress payloads to JSONField-safe primitives."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _game_item(user, source, media_id):
    from app.models import Item

    item = Item.objects.filter(source=source, media_type="game", media_id=media_id).first()
    if item:
        return item
    metadata = provider_services.get_media_metadata("game", media_id, source)
    return get_or_create_item_from_metadata({"source": source, "media_type": "game", "media_id": media_id, "season_number": None, "episode_number": None}, metadata)


def perform_game_action(user, *, source, media_id, action, data):
    """Route game actions through the authoritative domain service."""
    functions = {
        "start": (game_tracking.start, {"start_date", "mutation_id"}),
        "resume": (game_tracking.resume, {"start_date", "mutation_id"}),
        "pause": (game_tracking.pause, set()),
        "drop": (game_tracking.drop, {"end_date"}),
        "restart": (game_tracking.restart, {"start_date", "end_date", "mutation_id"}),
        "mark_completed": (game_tracking.mark_completed, set()),
        "consume": (game_tracking.mark_completed, set()),
        "undo_completed": (game_tracking.undo_completed, set()),
        "delete_undated_completion": (game_tracking.delete_undated_completion, set()),
    }
    if action not in functions:
        raise serializers.ValidationError({"action": "Unsupported game action."})
    function, accepted = functions[action]
    return _call_book(function, user, _game_item(user, source, media_id), **{key: value for key, value in data.items() if key in accepted and value is not None})


def complete_game(user, *, source, media_id, data):
    """Save one game completion and return its canonical state."""
    payload = dict(data)
    if "rating" in payload:
        payload["rating"] = _call_book(single_weight.rating_from_wire, payload["rating"])
    return _call_book(game_tracking.complete, user, _game_item(user, source, media_id), **payload)


def _write_game_tracking(user, item, data):
    payload = {key: value for key, value in data.items() if key in {"status", "rating", "start_date", "notes", "mutation_id", "total_minutes", "percentage", "progressed_on"}}
    if "rating" in payload:
        payload["rating"] = _call_book(single_weight.rating_from_wire, payload["rating"])
    if "progress" in data:
        payload["total_minutes"] = data["progress"]
    if "end_date" in data and data["end_date"] is not None:
        raise serializers.ValidationError({"end_date": "Use the completion log or playthrough date editor."})
    return _call_book(game_tracking.apply_tracking_state, user, item, **payload)


# ---------------------------------------------------------------------------------
# TV (docs/tv-tracking-contract.md). Every TV rule lives in app.tv_tracking; this layer
# resolves Items, calls it and builds responses.
# ---------------------------------------------------------------------------------


def _call_tv(function, *args, **kwargs):
    """Call a tv_tracking function, translating its errors to API errors."""
    try:
        return function(*args, **kwargs)
    except TV_ERRORS as error:
        raise tv_api_error(error) from error


def tv_show_item(source, media_id, *, create=True):
    """Return the show's tv Item, creating it from the provider when missing."""
    lookup = {
        "source": source,
        "media_type": MediaTypes.TV.value,
        "media_id": str(media_id),
        "season_number": None,
        "episode_number": None,
    }
    item = Item.objects.filter(**lookup).first()
    if item is not None or not create:
        return item
    if source == Sources.MANUAL.value:
        raise NotFound
    try:
        metadata = provider_services.get_media_metadata(MediaTypes.TV.value, media_id, source)
    except provider_services.ProviderAPIError as error:
        if error.status_code == 404:
            raise NotFound from error
        raise TVDetailsUnavailableError from error
    return get_or_create_item_from_metadata(lookup, metadata)


def tv_family_item(show_item, media_type, season_number=None, episode_number=None):
    """Return a season or episode Item of the show, creating it from the show's copy.

    No provider call: a new Item takes the show's title and image, or the stored
    listing's episode title, as tv_tracking does for the Items a write needs.
    """
    if media_type == MediaTypes.TV.value:
        return show_item
    lookup = {
        "source": show_item.source,
        "media_type": media_type,
        "media_id": show_item.media_id,
        "season_number": season_number,
        "episode_number": episode_number if media_type == MediaTypes.EPISODE.value else None,
    }
    item = Item.objects.filter(**lookup).first()
    if item is not None:
        return item
    if show_item.source == Sources.MANUAL.value:
        raise NotFound
    title = show_item.title
    image = show_item.image
    if media_type == MediaTypes.EPISODE.value:
        listing = tv_listing.stored_listing(show_item)
        listed = listing.episode(season_number, episode_number) if listing else None
        if listed is not None:
            title = listed.title or title
            image = listed.image or image
    item, _ = Item.objects.get_or_create(**lookup, defaults={"title": title, "image": image})
    return item


def require_tv_numbers(media_type, season_number, episode_number):
    """Reject a season write without its number, or an episode's without both."""
    errors = {}
    if media_type in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value) and season_number is None:
        errors["season_number"] = ["This field is required."]
    if media_type == MediaTypes.EPISODE.value and episode_number is None:
        errors["episode_number"] = ["This field is required."]
    if errors:
        raise TVValidationError(detail=errors)


def int_param(value, name):
    """Return an optional integer request parameter; a malformed one is a 400."""
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise TVValidationError(detail={name: ["A valid integer is required."]}) from error


def resolve_tv_item(*, source, media_type, media_id, season_number=None, episode_number=None):
    """Return the tv, season or episode Item a TV request names (TV-106)."""
    season_number = int_param(season_number, "season_number")
    episode_number = int_param(episode_number, "episode_number")
    require_tv_numbers(media_type, season_number, episode_number)
    return tv_family_item(tv_show_item(source, media_id), media_type, season_number, episode_number)


def _tv_rating(value):
    try:
        return validate_tv_rating(value)
    except serializers.ValidationError as error:
        raise TVValidationError(detail={"rating": error.detail}) from error


def write_tv_tracking(user, *, source, media_type, media_id, data):
    """Apply PUT/PATCH of a show's or season's tracking (TV-207).

    Installed builds send these, so the entry point is LEGACY_APP.
    """
    item = resolve_tv_item(
        source=source,
        media_type=media_type,
        media_id=media_id,
        season_number=data.get("season_number"),
    )
    payload = {key: value for key, value in data.items() if key not in ("season_number", "mutation_id")}
    if "rating" in payload:
        payload["rating"] = _tv_rating(payload["rating"])
    return _call_tv(
        tv_tracking.apply_written_state,
        user,
        item,
        payload,
        entry_point=tv_tracking.LEGACY_APP,
        mutation_id=data.get("mutation_id"),
    )


def delete_tv_tracking(
    user,
    *,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
    expected_version=None,
    mutation_id=None,
):
    """DELETE tracking/{source}/{tv|season|episode}/{id}/ (TV-003, TV-207, TV-102)."""
    season_number = int_param(season_number, "season_number")
    episode_number = int_param(episode_number, "episode_number")
    expected_version = int_param(expected_version, "expected_version")
    require_tv_numbers(media_type, season_number, episode_number)
    show_item = tv_show_item(source, media_id, create=False)
    if show_item is None:
        return None
    if media_type == MediaTypes.TV.value:
        return _call_tv(
            tv_tracking.remove,
            user,
            show_item,
            expected_version=expected_version,
            mutation_id=mutation_id,
            entry_point=tv_tracking.LEGACY_APP,
        )
    if media_type == MediaTypes.SEASON.value:
        return _call_tv(
            tv_tracking.delete_season_tracking,
            user,
            show_item,
            season_number,
            mutation_id=mutation_id,
            entry_point=tv_tracking.LEGACY_APP,
        )
    return _call_tv(
        tv_tracking.untick,
        user,
        show_item,
        season_number,
        episode_number,
        mutation_id=mutation_id,
        entry_point=tv_tracking.LEGACY_APP,
    )


def perform_tv_action(user, *, source, media_id, action, data):  # noqa: C901, PLR0911, PLR0912
    """Apply one action of POST tracking/{source}/tv/{id}/actions/{action}/.

    Returns tv_tracking's WriteResult. The view answers 404 for an unknown action.
    """
    if action not in TV_ACTIONS:
        raise NotFound
    season_number = data.get("season_number")
    episode_number = data.get("episode_number")
    if action in TV_EPISODE_ACTIONS:
        require_tv_numbers(MediaTypes.EPISODE.value, season_number, episode_number)
    if action == "rate" and "rating" not in data:
        raise TVValidationError(detail={"rating": ["Send a rating from 1 to 10, or null to clear it."]})
    show_item = tv_show_item(source, media_id)
    common = {"entry_point": tv_tracking.APP, "mutation_id": data.get("mutation_id")}
    if action in TV_CHOICE_ACTIONS:
        return _call_tv(tv_tracking.choose, user, show_item, TV_CHOICE_ACTIONS[action], **common)
    if action == "rewatch":
        return _call_tv(tv_tracking.start_rewatch, user, show_item, season_number=season_number, **common)
    if action == "stop_rewatch":
        return _call_tv(tv_tracking.stop_rewatch, user, show_item, **common)
    if action == "remove":
        return _call_tv(
            tv_tracking.remove,
            user,
            show_item,
            expected_version=data.get("expected_version"),
            **common,
        )
    if action in ("mark_watched", "consume"):
        return _call_tv(tv_tracking.mark_watched, user, show_item, season_number=season_number, **common)
    if action == "unmark":
        return _call_tv(
            tv_tracking.unmark,
            user,
            show_item,
            season_number=season_number,
            confirm=bool(data.get("confirm")),
            expected_version=data.get("expected_version"),
            **common,
        )
    if action == "mark_up_to":
        return _call_tv(tv_tracking.mark_up_to, user, show_item, season_number, episode_number, **common)
    if action == "mark_earlier":
        return _call_tv(
            tv_tracking.mark_earlier,
            user,
            show_item,
            from_ref=data.get("from"),
            to_ref=data.get("to"),
            offer_season_number=data.get("offer_season_number"),
            **common,
        )
    if action == "tick":
        return _call_tv(tv_tracking.tick, user, show_item, season_number, episode_number, **common)
    if action == "untick":
        return _call_tv(
            tv_tracking.untick,
            user,
            show_item,
            season_number,
            episode_number,
            viewing_id=data.get("viewing_id"),
            **common,
        )
    item = show_item
    if season_number is not None:
        item = tv_family_item(show_item, MediaTypes.SEASON.value, season_number)
    return _call_tv(tv_tracking.set_rating, user, item, data["rating"], **common)


def tv_envelope(result, item=None, *, page=None):
    """Return the write envelope (plan 2.3.5) of a TV write; page scopes its `tv`."""
    return _call_tv(tv_tracking.write_envelope, result, item, page=page)


def tv_page_item(show_item, season_number=None, episode_number=None):
    """Return the season or episode Item of the page a TV write came from, or None.

    The app sends page_season_number (and page_episode_number) from a season or
    episode page, so the response's `tv` carries that page's state and composer
    (TV-404, TV-705).
    None for the show page, or an Item that doesn't exist; nothing is created.
    """
    if show_item is None or season_number is None:
        return None
    media_type = MediaTypes.SEASON.value if episode_number is None else MediaTypes.EPISODE.value
    return Item.objects.filter(
        source=show_item.source,
        media_id=show_item.media_id,
        media_type=media_type,
        season_number=season_number,
        episode_number=episode_number,
    ).first()


def tv_season_row(result, season_number=None):
    """Return the user's Season row of a write's season, or None."""
    number = result.season_number if season_number is None else season_number
    if result.tv is None or number is None:
        return None
    return (
        Season.objects.filter(related_tv=result.tv, item__season_number=number)
        .select_related("item", "user")
        .first()
    )


def tv_tracking_response(result, *, row, item=None):
    """Return row's TrackingState (or nothing) plus the envelope keys."""
    envelope = tv_envelope(result, item)
    state = serialize_tracking(row) if row is not None else {}
    return {**state, **envelope}


def tv_action_response(result, page=None):
    """Return the actions endpoint's body: the show's TrackingState and the envelope.

    page is the season or episode page the action came from (tv_page_item).
    """
    envelope = tv_envelope(result, page=page)
    tracking = None
    if result.tv is not None:
        tv = TV.objects.select_related("item", "user").get(pk=result.tv.pk)
        tracking = serialize_tracking(tv, tv=envelope["tv"])
    return {"tracking": tracking, **envelope}
