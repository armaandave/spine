from contextlib import suppress
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from api.exceptions import BookTrackingConflict, DiaryHistoryConflict
from api.serializers.common import (
    get_or_create_item_from_metadata,
    progress_for_media,
    tracking_state,
)
from app import book_tracking, game_tracking, single_weight
from app.models import BasicMedia, Book, MediaTypes, Status
from app.providers import services as provider_services
from social.models import Activity, ProgressChange


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
    """Create or update tracking using existing model behavior."""
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
    if media is None:
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


def delete_tracking(user, *, source, media_type, media_id, season_number=None):
    """Delete tracked media if it exists."""
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


def consume_media(user, *, source, media_type, media_id, consumed_at=None):
    """Mark media consumed/completed."""
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


def set_status(user, *, source, media_type, media_id, status):
    """Set status on an existing or new tracked item."""
    media = create_or_update_tracking(
        user,
        source=source,
        media_type=media_type,
        media_id=media_id,
        data={"status": status},
    )
    return media


def watch_episode(user, *, source, media_id, season_number, episode_number, watched_at=None):
    """Watch one TV episode using Season.watch."""
    with transaction.atomic():
        season = get_tracking(
            user,
            source=source,
            media_type=MediaTypes.SEASON.value,
            media_id=media_id,
            season_number=season_number,
        )
        previous_progress = _progress_snapshot(season) if season is not None else None
        if season is None:
            season = create_or_update_tracking(
                user,
                source=source,
                media_type=MediaTypes.SEASON.value,
                media_id=media_id,
                data={"season_number": season_number, "status": Status.IN_PROGRESS.value},
            )
        season.watch(episode_number, watched_at or timezone.now().replace(second=0, microsecond=0))
        season.refresh_from_db()
        _record_progress_change(user, season, previous_progress)
        return season


def unwatch_episode(user, *, source, media_id, season_number, episode_number):
    """Unwatch the latest matching TV episode."""
    season = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        season_number=season_number,
    )
    if season is not None:
        previous_progress = _progress_snapshot(season)
        season.unwatch(episode_number)
        season.refresh_from_db()
        _record_progress_change(user, season, previous_progress)
    return season


def watch_season(user, *, source, media_id, season_number):
    """Mark a season watched."""
    season = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        season_number=season_number,
    )
    previous_progress = _progress_snapshot(season) if season is not None else None
    season = create_or_update_tracking(
        user,
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        data={"season_number": season_number, "status": Status.COMPLETED.value},
    )
    _record_progress_change(user, season, previous_progress)
    return season


def unwatch_season(user, *, source, media_id, season_number):
    """Remove watched episodes for a season and move it in progress."""
    season = get_tracking(
        user,
        source=source,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id,
        season_number=season_number,
    )
    if season is not None:
        previous_progress = _progress_snapshot(season)
        season.episodes.all().delete()
        season.status = Status.IN_PROGRESS.value
        season.save(update_fields=["status"])
        _record_progress_change(user, season, previous_progress)
    return season


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


def serialize_tracking(media):
    """Serialize tracking state after annotating max progress when possible."""
    if media is None:
        return None
    if not hasattr(media, "max_progress"):
        with suppress(Exception):
            BasicMedia.objects.annotate_max_progress([media], media.item.media_type)
    return tracking_state(media)


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
    """Record a durable progress delta and feed activity."""
    if media is None or previous_progress is None:
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
