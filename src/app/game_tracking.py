"""Game playthrough transitions using shared diary, opinion, and date rules."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from app import single_weight
from app.book_tracking import BookTrackingConflict, HistoryExists, _mutation
from app.models import DiaryEntry, Game, GameSession, Item, MediaLike, Status

UNSET = single_weight.UNSET
OPEN_STATUSES = {Status.IN_PROGRESS.value, Status.PAUSED.value}
_set_like_row = single_weight._set_like_row


class CompletionRequired(BookTrackingConflict):
    """An unfinished playthrough requires its completion composer."""

    def __init__(self, playthrough_id):
        super().__init__(
            "completion_required",
            "Finish this playthrough through its completion log.",
            action="finish",
            playthrough_id=playthrough_id,
        )


def supports(value):
    return getattr(value, "media_type", value) == "game"


def _day(value):
    return single_weight.calendar_date(single_weight.calendar_datetime(value))


def _locked_game(user, item):
    # Lock the persistent item first, including creation of a first tracking row.
    Item.objects.select_for_update().get(pk=item.pk)
    return Game.objects.select_for_update().filter(user=user, item=item).first()


def _ensure_game(user, item):
    game = _locked_game(user, item)
    if game is None:
        return Game.objects.create(user=user, item=item, status=Status.PLANNING), True
    if not game.status_history:
        kind = "undated_completion" if game.completed_manually else "direct_status"
        game.status_history = [{"status": game.status, "kind": kind}]
        game.save(update_fields=["status_history"])
    return game, False


def _open_session(game):
    return (
        game.playthroughs.select_for_update().filter(status__in=OPEN_STATUSES).first()
    )


def _entries(game):
    return DiaryEntry.objects.filter(user=game.user, item=game.item)


def _has_completion(game):
    return game.completed_manually or _entries(game).exists()


def _push(game, status, kind, session=None):
    event = {"status": str(status), "kind": kind}
    if session:
        event["playthrough_id"] = session.pk
    if not game.status_history or game.status_history[-1] != event:
        game.status_history = [*game.status_history, event]


def _clear_opinions(game):
    game.score = None
    game.rating_source = None
    game.like_source = None
    game.like_is_independent = False
    MediaLike.objects.filter(user=game.user, item=game.item).delete()
    single_weight._delete_rating_activity(game.user, game)


def _sync_state(game):
    """Discard unsupported states and select displayed progress independently."""
    sessions = {
        session.pk: session
        for session in game.playthroughs.select_related("completion_diary_entry")
    }
    has_completion = _has_completion(game)
    history = []
    for event in game.status_history:
        session_id = event.get("playthrough_id")
        if session_id is not None and session_id not in sessions:
            continue
        if event["kind"] == "undated_completion" and not game.completed_manually:
            continue
        if (
            event["kind"] == "completion"
            and not sessions[session_id].completion_diary_entry_id
        ):
            continue
        if event["status"] == Status.COMPLETED and not has_completion:
            continue
        history.append(event)
    if not history:
        if has_completion:
            history = [{"status": Status.COMPLETED.value, "kind": "legacy_completion"}]
        elif sessions:
            session = max(
                sessions.values(), key=lambda value: (value.created_at, value.pk)
            )
            history = [
                {
                    "status": session.status,
                    "kind": "playthrough",
                    "playthrough_id": session.pk,
                }
            ]
        else:
            _clear_opinions(game)
            game.delete()
            return None
    current = history[-1]
    game.status_history = history
    game.status = current["status"]
    selected = sessions.get(current.get("playthrough_id"))
    if game.status == Status.COMPLETED:
        completed = [
            session
            for session in sessions.values()
            if session.completion_diary_entry_id
        ]
        selected = (
            max(
                completed,
                key=lambda value: (
                    value.end_date,
                    value.completion_diary_entry.created_at,
                    value.completion_diary_entry_id,
                ),
            )
            if completed
            else None
        )
    game.current_session = selected
    game.start_date = (
        single_weight.calendar_datetime(selected.start_date)
        if selected and selected.start_date
        else None
    )
    latest_entry = _entries(game).order_by("-consumed_at", "-id").first()
    game.end_date = (
        latest_entry.consumed_at
        if game.status == Status.COMPLETED and latest_entry
        else None
    )
    # Legacy progress is retained for upgrade/export; new clients use the session.
    game.save(
        update_fields=[
            "status",
            "status_history",
            "current_session",
            "start_date",
            "end_date",
            "score",
            "rating_source",
            "like_source",
            "like_is_independent",
            "completed_manually",
        ]
    )
    return game


@transaction.atomic
def start(user, item, *, start_date=None, mutation_id=None):
    game, _ = _ensure_game(user, item)
    mutation_id = _mutation(mutation_id)
    if mutation_id and game.playthroughs.filter(mutation_id=mutation_id).exists():
        return game
    session = _open_session(game)
    if session:
        if session.status != Status.IN_PROGRESS:
            session.status = Status.IN_PROGRESS
            session.save(update_fields=["status"])
            _push(game, session.status, "playthrough", session)
        return _sync_state(game)
    session = GameSession.objects.create(
        related_game=game,
        status=Status.IN_PROGRESS,
        start_date=_day(start_date or timezone.localdate()),
        mutation_id=mutation_id,
    )
    _push(game, session.status, "playthrough", session)
    return _sync_state(game)


@transaction.atomic
def assign_status(user, item, status, *, start_date=None, mutation_id=None):
    if status not in Status.values:
        raise ValidationError("Invalid game status.")
    if status == Status.IN_PROGRESS:
        return start(user, item, start_date=start_date, mutation_id=mutation_id)
    if status == Status.COMPLETED:
        return mark_completed(user, item)
    game, _ = _ensure_game(user, item)
    session = _open_session(game)
    if session:
        if status == Status.PLANNING:
            raise ValidationError(
                "Pause, drop, or delete this playthrough before choosing Planning."
            )
        session.status = status
        if status == Status.DROPPED:
            session.end_date = _day(timezone.localdate())
        session.save(update_fields=["status", "end_date"])
        _push(game, status, "playthrough", session)
    elif game.status != status or not game.status_history:
        _push(game, status, "direct_status")
    return _sync_state(game)


def pause(user, item):
    return assign_status(user, item, Status.PAUSED)


def resume(user, item, **kwargs):
    return start(user, item, **kwargs)


@transaction.atomic
def drop(user, item, *, end_date=None):
    game, _ = _ensure_game(user, item)
    session = _open_session(game)
    if not session:
        return assign_status(user, item, Status.DROPPED)
    end = _day(end_date or timezone.localdate())
    _validate_dates(session, end_date=end)
    session.status = Status.DROPPED
    session.end_date = end
    session.save(update_fields=["status", "end_date"])
    _push(game, session.status, "playthrough", session)
    return _sync_state(game)


@transaction.atomic
def restart(user, item, *, start_date=None, end_date=None, mutation_id=None):
    game = _locked_game(user, item)
    mutation_id = _mutation(mutation_id)
    if mutation_id is None:
        raise ValidationError("A mutation UUID is required to restart a playthrough.")
    if (
        game
        and mutation_id
        and game.playthroughs.filter(mutation_id=mutation_id).exists()
    ):
        return game
    if not game or not _open_session(game):
        raise ValidationError("Only a Playing or Paused playthrough can restart.")
    drop(user, item, end_date=end_date)
    return start(user, item, start_date=start_date, mutation_id=mutation_id)


@transaction.atomic
def mark_completed(user, item):
    game, _ = _ensure_game(user, item)
    session = _open_session(game)
    if session:
        raise CompletionRequired(session.pk)
    if game.status == Status.COMPLETED and _has_completion(game):
        return game
    if _has_completion(game):
        _push(game, Status.COMPLETED, "history_override")
    else:
        game.completed_manually = True
        _push(game, Status.COMPLETED, "undated_completion")
    return _sync_state(game)


@transaction.atomic
def undo_completed(user, item):
    game = _locked_game(user, item)
    if not game or game.status != Status.COMPLETED:
        return game
    event = game.status_history[-1] if game.status_history else {}
    if event.get("kind") == "history_override":
        game.status_history = game.status_history[:-1]
    elif event.get("kind") == "undated_completion" or (
        game.completed_manually and not _entries(game).exists()
    ):
        game.completed_manually = False
        game.status_history = [
            event
            for event in game.status_history
            if event["kind"] != "undated_completion"
        ]
        _clear_opinions(game)
    else:
        raise HistoryExists("Delete this completion log first.")
    return _sync_state(game)


@transaction.atomic
def delete_undated_completion(user, item):
    game = _locked_game(user, item)
    if not game or not game.completed_manually:
        return game
    event = game.status_history[-1] if game.status_history else {}
    if event.get("kind") == "undated_completion" or (
        game.status == Status.COMPLETED and not _entries(game).exists()
    ):
        _clear_opinions(game)
    game.completed_manually = False
    game.status_history = [
        event for event in game.status_history if event["kind"] != "undated_completion"
    ]
    return _sync_state(game)


def _integer(value, *, maximum=None):
    if value is None:
        return None
    try:
        number = Decimal(str(value))
        if (
            isinstance(value, bool)
            or (maximum is not None and (isinstance(value, float) or "." in str(value)))
            or not number.is_finite()
            or number < 0
            or number != number.to_integral_value()
            or number > (maximum if maximum is not None else 2_147_483_647)
        ):
            raise ValueError
        return int(number)
    except (ValueError, TypeError, InvalidOperation) as error:
        raise ValidationError(
            "Enter a non-negative whole number"
            + (f" up to {maximum}." if maximum is not None else ".")
        ) from error


def _validate_dates(session, *, start_date=UNSET, end_date=UNSET):
    start = session.start_date if start_date is UNSET else start_date
    end = session.end_date if end_date is UNSET else end_date
    if start is None and session.origin == GameSession.Origin.LIVE:
        raise ValidationError("A tracked playthrough requires a start date.")
    if start and (
        (session.first_progress_on and start > session.first_progress_on)
        or (end and start > end)
    ):
        raise ValidationError(
            "Start date cannot follow the first progress, completion, or drop date."
        )


def _validate_completion_date(session, day):
    if session.start_date and day < session.start_date:
        raise ValidationError(
            "Completion date cannot be before the playthrough start date."
        )
    _validate_dates(session, end_date=day)


@transaction.atomic
def update_progress(
    user,
    item,
    *,
    playthrough_id=None,
    total_minutes=UNSET,
    percentage=UNSET,
    progressed_on=None,
):
    game = _locked_game(user, item)
    if not game:
        raise ValidationError("Start a playthrough before updating progress.")
    session = (
        game.playthroughs.select_for_update()
        .filter(pk=playthrough_id or game.current_session_id)
        .first()
    )
    if not session:
        raise ValidationError("Playthrough not found.")
    minutes = _integer(total_minutes) if total_minutes is not UNSET else UNSET
    percent = _integer(percentage, maximum=100) if percentage is not UNSET else UNSET
    day = _day(progressed_on or timezone.localdate())
    if session.start_date and day < session.start_date:
        raise ValidationError("Progress cannot predate the start date.")
    if minutes is not UNSET:
        session.total_minutes = minutes
        session.minutes_updated_on = day
        session.minutes_linked = False
    if percent is not UNSET:
        session.percentage = percent
        session.percentage_updated_on = day
        session.percentage_linked = False
    if minutes is not UNSET or percent is not UNSET:
        session.first_progress_on = min(session.first_progress_on or day, day)
        session.save()
    return _sync_state(game)


@transaction.atomic
def update_playthrough(
    user,
    item,
    playthrough_id,
    *,
    start_date=UNSET,
    end_date=UNSET,
    total_minutes=UNSET,
    percentage=UNSET,
    progressed_on=None,
):
    game = _locked_game(user, item)
    session = (
        game.playthroughs.select_for_update().filter(pk=playthrough_id).first()
        if game
        else None
    )
    if not session:
        raise ValidationError("Playthrough not found.")
    if end_date is not UNSET and session.completion_diary_entry_id:
        raise ValidationError("Edit completion dates through the completion log.")
    if end_date is not UNSET and session.status != Status.DROPPED:
        raise ValidationError("Only a dropped playthrough has an editable drop date.")
    if start_date is not UNSET:
        session.start_date = _day(start_date) if start_date is not None else None
    if end_date is not UNSET:
        session.end_date = _day(end_date)
    _validate_dates(session)
    session.save()
    if total_minutes is not UNSET or percentage is not UNSET:
        return update_progress(
            user,
            item,
            playthrough_id=session.pk,
            total_minutes=total_minutes,
            percentage=percentage,
            progressed_on=progressed_on,
        )
    return _sync_state(game)


@transaction.atomic
def complete(
    user,
    item,
    *,
    completion_date,
    playthrough_id=None,
    start_date=UNSET,
    total_minutes=UNSET,
    percentage=UNSET,
    rating=UNSET,
    liked=UNSET,
    review="",
    review_title="",
    contains_spoilers=False,
    tags=None,
    is_rewatch=None,
    mutation_id=None,
    import_source="",
    import_source_id="",
    import_source_order=None,
    emit_activity=True,
    update_current=True,
    force_direct=False,
):
    game, created = _ensure_game(user, item)
    mutation_id = _mutation(mutation_id)
    if mutation_id:
        existing = game.playthroughs.filter(
            completion_mutation_id=mutation_id, completion_diary_entry__isnull=False
        ).first()
        if existing:
            return game, existing.completion_diary_entry
    if import_source and import_source_id:
        existing = (
            _entries(game)
            .filter(import_source=import_source, import_source_id=import_source_id)
            .first()
        )
        if existing:
            return game, existing
    session = None if force_direct else _open_session(game)
    if playthrough_id is not None:
        selected = (
            game.playthroughs.select_for_update().filter(pk=playthrough_id).first()
        )
        if not selected:
            raise ValidationError("Playthrough not found.")
        if selected.completion_diary_entry_id:
            return game, selected.completion_diary_entry
        if selected.status not in OPEN_STATUSES:
            raise ValidationError("Only Playing or Paused playthroughs can finish.")
        session = selected
    day = _day(completion_date)
    if session is None:
        session = GameSession(
            related_game=game,
            status=Status.COMPLETED,
            origin=GameSession.Origin.DIRECT_LOG,
        )
    if start_date is not UNSET:
        session.start_date = _day(start_date) if start_date is not None else None
    _validate_completion_date(session, day)
    minutes = (
        session.total_minutes if total_minutes is UNSET else _integer(total_minutes)
    )
    percent = (
        session.percentage if percentage is UNSET else _integer(percentage, maximum=100)
    )
    rating = (
        game.score if rating is UNSET else single_weight.validate_storage_rating(rating)
    )
    liked = (
        MediaLike.objects.filter(user=user, item=item).exists()
        if liked is UNSET
        else bool(liked)
    )
    replay = _has_completion(game) if is_rewatch is None else is_rewatch
    entry = DiaryEntry.objects.create(
        user=user,
        item=item,
        consumed_at=single_weight.calendar_datetime(day),
        rating=rating,
        liked=liked,
        review=review,
        review_title=review_title,
        contains_spoilers=contains_spoilers,
        is_rewatch=bool(replay),
        progress_snapshot={"total_minutes": minutes, "percentage": percent},
        visibility="public",
        import_source=import_source,
        import_source_id=import_source_id,
        import_source_order=import_source_order,
    )
    session.minutes_linked = not (
        session.minutes_updated_on and session.minutes_updated_on > day
    )
    session.percentage_linked = not (
        session.percentage_updated_on and session.percentage_updated_on > day
    )
    if session.minutes_linked:
        session.total_minutes = minutes
    if session.percentage_linked:
        session.percentage = percent
    session.status = Status.COMPLETED
    session.end_date = day
    session.completion_diary_entry = entry
    session.completion_mutation_id = mutation_id
    session.save()
    single_weight._replace_tags(entry, tags or [])
    if update_current:
        single_weight.couple_current_to_log(game, entry)
    if not force_direct or created or not _open_session(game):
        _push(game, Status.COMPLETED, "completion", session)
    game = _sync_state(game)
    if emit_activity:
        single_weight._create_diary_activity(entry)
    return game, entry


@transaction.atomic
def update_completion(entry, data, *, tags=UNSET):
    game = _locked_game(entry.user, entry.item)
    if game is None:
        raise ValidationError("Game tracking not found.")
    session = (
        game.playthroughs.select_for_update()
        .filter(completion_diary_entry=entry)
        .first()
    )
    old_date = entry.consumed_at
    if "consumed_at" in data:
        day = _day(data["consumed_at"])
        if session:
            _validate_completion_date(session, day)
            session.end_date = day
        entry.consumed_at = single_weight.calendar_datetime(day)
    snapshot = dict(entry.progress_snapshot or {})
    for field, linked in (
        ("total_minutes", "minutes_linked"),
        ("percentage", "percentage_linked"),
    ):
        if field in data:
            value = _integer(
                data[field], maximum=100 if field == "percentage" else None
            )
            snapshot[field] = value
            if session and getattr(session, linked):
                setattr(session, field, value)
    entry.progress_snapshot = snapshot
    if "rating" in data:
        entry.rating = single_weight.validate_storage_rating(data["rating"])
    for field in ("review", "review_title", "liked", "is_rewatch", "contains_spoilers"):
        if field in data:
            setattr(entry, field, data[field])
    entry.visibility = "public"
    entry.save()
    if session:
        session.save()
    if tags is not UNSET:
        single_weight._replace_tags(entry, tags or [])
    single_weight.update_current_from_source_log(game, entry, data)
    _sync_state(game)
    single_weight._update_diary_activity(entry)
    single_weight._audit_diary(entry, "diary_updated")
    if entry.consumed_at != old_date:
        single_weight._queue_statistics(entry.user_id, old_date)
    return entry


@transaction.atomic
def delete_completion(user, entry):
    if entry.user_id != user.pk:
        raise ValidationError("You can only delete your own completion logs.")
    game = _locked_game(user, entry.item)
    session = (
        game.playthroughs.select_for_update()
        .filter(completion_diary_entry=entry)
        .first()
        if game
        else None
    )
    single_weight._delete_diary_activity(user, entry)
    single_weight._audit_diary(entry, "diary_deleted")
    if game:
        single_weight.detach_deleted_source_log(game, entry.pk)
    event = game.status_history[-1] if game and game.status_history else {}
    reopen = bool(
        session
        and session.origin == GameSession.Origin.LIVE
        and event.get("kind") == "completion"
        and event.get("playthrough_id") == session.pk
    )
    old_date = entry.consumed_at
    entry.delete()
    if session:
        if reopen:
            game.status_history = [
                event
                for event in game.status_history
                if not (
                    event.get("playthrough_id") == session.pk
                    and event["kind"] == "completion"
                )
            ]
            earlier = next(
                (
                    event
                    for event in reversed(game.status_history)
                    if event.get("playthrough_id") == session.pk
                ),
                None,
            )
            session.status = earlier["status"] if earlier else Status.IN_PROGRESS
            session.end_date = None
            session.completion_diary_entry = None
            session.completion_mutation_id = None
            session.minutes_linked = False
            session.percentage_linked = False
            session.save()
        else:
            session.delete()
    result = _sync_state(game) if game else None
    single_weight._queue_statistics(user.pk, old_date)
    return result


@transaction.atomic
def delete_playthrough(user, item, playthrough_id):
    game = _locked_game(user, item)
    if not game:
        return None
    session = game.playthroughs.select_for_update().filter(pk=playthrough_id).first()
    if not session:
        return game
    if session.completion_diary_entry_id:
        raise HistoryExists("Delete this completion log first.")
    session.delete()
    return _sync_state(game)


@transaction.atomic
def remove_tracking(user, item):
    game = _locked_game(user, item)
    if not game:
        return None
    if game.playthroughs.exists() or _entries(game).exists():
        raise HistoryExists(
            "Delete playthroughs and completion logs before removing this game."
        )
    _clear_opinions(game)
    game.delete()
    return None


@transaction.atomic
def set_rating(user, item, rating, *, emit_activity=True):
    rating = single_weight.validate_storage_rating(rating)
    game = (
        mark_completed(user, item) if rating is not None else _locked_game(user, item)
    )
    if game:
        single_weight.set_current_rating(game, rating, emit_activity=emit_activity)
    return game


@transaction.atomic
def set_like(user, item, liked, *, audit=True):
    game = mark_completed(user, item) if liked else _locked_game(user, item)
    if game:
        changed = single_weight.set_current_like(game, liked)
        if changed and audit:
            single_weight._audit_like(user, item, liked)
    return game


@transaction.atomic
def apply_tracking_state(
    user,
    item,
    *,
    status=UNSET,
    rating=UNSET,
    start_date=UNSET,
    notes=UNSET,
    mutation_id=None,
    total_minutes=UNSET,
    percentage=UNSET,
    progressed_on=None,
):
    game = _locked_game(user, item)
    if status is not UNSET:
        game = assign_status(
            user,
            item,
            status,
            start_date=None if start_date is UNSET else start_date,
            mutation_id=mutation_id,
        )
    if rating is not UNSET:
        game = set_rating(user, item, rating)
    if total_minutes is not UNSET or percentage is not UNSET:
        game = update_progress(
            user,
            item,
            total_minutes=total_minutes,
            percentage=percentage,
            progressed_on=progressed_on,
        )
    if start_date is not UNSET:
        if not game or not game.current_session_id:
            if start_date is not None:
                raise ValidationError("A direct status has no playthrough start date.")
        else:
            game = update_playthrough(
                user, item, game.current_session_id, start_date=start_date
            )
    if notes is not UNSET:
        if game is None:
            game, _ = _ensure_game(user, item)
            _push(game, Status.PLANNING, "direct_status")
        game.notes = notes
        game.save(update_fields=["notes", "status_history"])
    return game


@transaction.atomic
def import_title_state(
    user, item, *, status=None, rating=UNSET, liked=UNSET, history_date=None, **values
):
    """Import one source state without fabricating sessions or source dates."""
    game = _locked_game(user, item)
    if game is None:
        game = Game(user=user, item=item, status=Status.PLANNING)
    session = _open_session(game) if game.pk else None
    if session and status and status != game.status:
        raise BookTrackingConflict(
            "unfinished_playthrough",
            "Finish, drop, or delete the current playthrough before importing a different status.",
        )
    if session and (
        (rating is not UNSET and rating is not None) or (liked is not UNSET and liked)
    ):
        raise CompletionRequired(session.pk)
    if rating is not UNSET:
        rating = single_weight.validate_storage_rating(rating)
        game.score = rating
        game.rating_source = None
    if liked is not UNSET:
        game.like_source = None
        game.like_is_independent = True
    desired = status or game.status
    if (rating is not UNSET and rating is not None) or (liked is not UNSET and liked):
        desired = Status.COMPLETED
    if not session:
        game.status = desired
        if desired == Status.COMPLETED:
            has_completion = game.pk and _has_completion(game)
            if not has_completion:
                game.completed_manually = True
            _push(
                game,
                desired,
                "history_override" if has_completion else "undated_completion",
            )
            game.current_session = (
                game.playthroughs.filter(completion_diary_entry__isnull=False)
                .order_by(
                    "-end_date",
                    "-completion_diary_entry__created_at",
                    "-completion_diary_entry_id",
                )
                .first()
                if game.pk
                else None
            )
        else:
            _push(game, desired, "direct_status")
            game.current_session = None
    for field in (
        "progress",
        "notes",
        "imported_lifetime_minutes",
        "imported_lifetime_source",
    ):
        if field in values and values[field] is not None:
            setattr(game, field, values[field])
    if not game.current_session_id:
        for field in ("start_date", "end_date"):
            if field in values and values[field] is not None:
                setattr(game, field, values[field])
    if history_date:
        game._history_date = history_date
    game.save()
    if liked is not UNSET:
        _set_like_row(user, item, liked)
    return game


def is_replay(session):
    game = session.related_game
    if game.completed_manually:
        return True
    entries = _entries(game)
    if session.completion_diary_entry_id:
        entry = session.completion_diary_entry
        return (
            entries.filter(consumed_at__lt=entry.consumed_at).exists()
            or entries.filter(consumed_at=entry.consumed_at, id__lt=entry.pk).exists()
        )
    return entries.exists()


def playthrough_payload(session):
    return {
        "id": session.pk,
        "status": session.status,
        "origin": session.origin,
        "start_date": session.start_date.isoformat() if session.start_date else None,
        "end_date": session.end_date.isoformat() if session.end_date else None,
        "total_minutes": session.total_minutes,
        "percentage": session.percentage,
        "completion_diary_entry_id": session.completion_diary_entry_id,
        "is_replay": is_replay(session),
    }


def state_payload(game):
    current = game.current_session
    open_session = current if current and current.status in OPEN_STATUSES else None
    dates = list(
        _entries(game)
        .order_by("consumed_at", "id")
        .values_list("consumed_at", flat=True)
    )
    can_remove = not game.playthroughs.exists() and not dates
    actions = ["start", "planning", "paused", "drop", "log", "mark_completed"]
    reasons = {}
    if open_session:
        actions += [
            "finish",
            "restart",
            "delete_playthrough",
            "pause" if current.status == Status.IN_PROGRESS else "resume",
        ]
        reasons["planning"] = "Pause, drop, or delete this playthrough first."
    if current:
        actions.append("update_progress")
    source = game.status_history[-1]["kind"] if game.status_history else "direct_status"
    if game.status == Status.COMPLETED:
        if source in ("undated_completion", "history_override"):
            actions.append("undo_completed")
        else:
            reasons["undo_completed"] = "Delete this completion log first."
    if game.completed_manually:
        actions.append("delete_undated_completion")
    if can_remove:
        actions.append("remove_tracking")
    else:
        reasons["remove_tracking"] = (
            "Delete playthroughs and completion logs before removing this game."
        )
    return {
        "current_playthrough": playthrough_payload(current) if current else None,
        "play_history": [
            playthrough_payload(session)
            for session in game.playthroughs.filter(status=Status.DROPPED).order_by(
                "-end_date", "-created_at", "-id"
            )
        ],
        "undated_completion": {
            "id": "undated",
            "status": Status.COMPLETED.value,
            "date": None,
        }
        if game.completed_manually
        else None,
        "status_source": source,
        "completion_dates": [value.date().isoformat() for value in dates],
        "completed_playthrough_count": len(dates),
        "lifetime_completion_count": len(dates) + int(game.completed_manually),
        "is_replaying": bool(open_session and _has_completion(game)),
        "can_remove_tracking": can_remove,
        "remove_tracking_reason": reasons.get("remove_tracking"),
        "available_actions": actions,
        "action_reasons": reasons,
        "imported_lifetime_minutes": game.imported_lifetime_minutes,
        "imported_lifetime_source": game.imported_lifetime_source or None,
    }
