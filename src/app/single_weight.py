"""Canonical state transitions for single-weight media."""

from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from app.models import (
    DiaryEntry,
    DiaryEntryTag,
    Item,
    MediaLike,
    MediaTypes,
    Status,
    Tag,
)
from app.tasks import update_daily_statistics


MEDIA_TYPES = {MediaTypes.MOVIE.value, MediaTypes.MUSIC.value}
HALF_STAR_MEDIA_TYPES = {*MEDIA_TYPES, MediaTypes.BOOK.value, MediaTypes.GAME.value}
WIRE_RATINGS = {Decimal(step) / 2 for step in range(1, 11)}
STORAGE_RATINGS = {Decimal(step) for step in range(1, 11)}
UNSET = object()


class DiaryHistoryExists(Exception):
    """Tracking cannot be removed while diary history exists."""


def supports(value):
    """Return whether an item or media-type value uses this contract."""
    media_type = getattr(value, "media_type", value)
    return media_type in MEDIA_TYPES


def uses_half_star_rating(value):
    """Return whether API ratings use the shared 0.5-to-5 wire scale."""
    media_type = getattr(value, "media_type", value)
    return media_type in HALF_STAR_MEDIA_TYPES


def rating_from_wire(value):
    """Convert a 0.5-5 star API rating to doubled storage."""
    if value in (None, "", 0, "0", "0.0"):
        return None
    try:
        rating = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError("Rating must be a half-star value from 0.5 to 5.0.") from error
    if rating not in WIRE_RATINGS:
        raise ValidationError("Rating must be a half-star value from 0.5 to 5.0.")
    return rating * 2


def rating_to_wire(value):
    """Convert doubled storage to a 0.5-5 star API rating."""
    if value is None:
        return None
    return Decimal(value) / 2


def validate_storage_rating(value):
    """Validate the existing doubled storage representation."""
    if value in (None, "", 0, "0", "0.0"):
        return None
    try:
        rating = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError("Rating must map to a half-star value from 0.5 to 5.0.") from error
    if rating not in STORAGE_RATINGS:
        raise ValidationError("Rating must map to a half-star value from 0.5 to 5.0.")
    return rating


def calendar_datetime(value, *, today=None):
    """Normalize a user calendar date to a stable UTC date carrier."""
    if value is None:
        raise ValidationError("A consumption date is required.")
    if isinstance(value, datetime):
        day = timezone.localdate(value) if timezone.is_aware(value) else value.date()
    elif isinstance(value, date):
        day = value
    else:
        try:
            day = date.fromisoformat(str(value))
        except (TypeError, ValueError) as error:
            raise ValidationError("Enter a valid calendar date.") from error
    if day > (today or timezone.localdate()):
        raise ValidationError("Consumption dates cannot be in the future.")
    return datetime.combine(day, time.min, tzinfo=UTC)


def calendar_date(value):
    """Return the stored user-selected calendar date."""
    return value.date() if isinstance(value, datetime) else value


def default_repeat(user, item):
    """Derive the new-log repeat default from prior consumption evidence."""
    tracking = _tracking_model(item).objects.filter(user=user, item=item).first()
    return bool(
        tracking
        and (
            tracking.direct_consumption
            or DiaryEntry.objects.filter(user=user, item=item).exists()
        )
    )


@transaction.atomic
def apply_tracking_state(
    user,
    item,
    *,
    status=UNSET,
    rating=UNSET,
    start_date=UNSET,
    notes=UNSET,
):
    """Apply legacy/API tracking fields without bypassing consumption laws."""
    tracking = _locked_tracking(user, item)
    if status == Status.COMPLETED.value:
        tracking = mark_consumed(user, item)
    if rating is not UNSET:
        rating = validate_storage_rating(rating)
        if rating is not None:
            tracking = set_rating(user, item, rating)
    if tracking is None:
        if (
            status is UNSET
            and rating is None
            and start_date is UNSET
            and notes is UNSET
        ):
            return None
        model = _tracking_model(item)
        tracking = model.objects.create(
            user=user,
            item=item,
            status=(status if status is not UNSET else Status.PLANNING.value),
            direct_consumption=False,
        )
    if rating is not UNSET and rating is None:
        tracking = set_rating(user, item, None) or tracking

    has_consumption = tracking.direct_consumption or DiaryEntry.objects.filter(
        user=user,
        item=item,
    ).exists()
    fields = []
    desired_status = (
        Status.COMPLETED.value
        if has_consumption
        else status if status is not UNSET else tracking.status
    )
    if tracking.status != desired_status:
        tracking.status = desired_status
        fields.append("status")
    if has_consumption and tracking.progress != 1:
        tracking.progress = 1
        fields.append("progress")
    if start_date is not UNSET and tracking.start_date != start_date:
        tracking.start_date = start_date
        fields.append("start_date")
    if notes is not UNSET and tracking.notes != notes:
        tracking.notes = notes
        fields.append("notes")
    if fields:
        tracking.save(update_fields=fields)
    _set_last_consumed(tracking)
    return tracking


@transaction.atomic
def import_title_state(
    user,
    item,
    *,
    consumed=True,
    rating=UNSET,
    liked=UNSET,
    history_date=None,
):
    """Import undated title state without activity or synthetic dates."""
    if not consumed and rating is UNSET and liked is UNSET:
        return None
    normalized_rating = (
        validate_storage_rating(rating) if rating is not UNSET else UNSET
    )
    tracking = _locked_tracking(user, item)
    if tracking is None:
        model = _tracking_model(item)
        tracking = model(
            user=user,
            item=item,
            status=Status.COMPLETED.value,
            progress=1,
            direct_consumption=True,
            score=normalized_rating if normalized_rating is not UNSET else None,
            like_is_independent=liked is not UNSET,
        )
        if history_date is not None:
            tracking._history_date = history_date
        tracking.save(force_insert=True)
    else:
        fields = []
        desired = {
            "status": Status.COMPLETED.value,
            "progress": 1,
            "direct_consumption": True,
        }
        if normalized_rating is not UNSET:
            desired.update({"score": normalized_rating, "rating_source": None})
        if liked is not UNSET:
            desired.update({"like_source": None, "like_is_independent": True})
        for field, value in desired.items():
            if getattr(tracking, field) != value:
                setattr(tracking, field, value)
                fields.append(field)
        if fields:
            if history_date is not None:
                tracking._history_date = history_date
            tracking.save(update_fields=fields)
    if liked is not UNSET:
        _set_like_row(user, item, bool(liked))
    _set_last_consumed(tracking)
    return tracking


@transaction.atomic
def import_logs(user, item, rows, *, source):
    """Import identifiable diary history and reconcile non-independent current state."""
    tracking = _locked_tracking(user, item)
    rating_is_independent = bool(
        tracking and tracking.score is not None and tracking.rating_source_id is None
    )
    like_is_independent = bool(tracking and tracking.like_is_independent)
    prior_consumed = bool(
        tracking and tracking.direct_consumption
    ) or DiaryEntry.objects.filter(user=user, item=item).exists()
    created_entries = []
    normalized_rows = sorted(
        enumerate(rows),
        key=lambda pair: (
            calendar_date(calendar_datetime(pair[1]["consumed_at"])),
            pair[1].get("source_order")
            if pair[1].get("source_order") is not None
            else pair[0],
        ),
    )
    for original_order, row in normalized_rows:
        source_id = str(row["source_id"])
        existing = DiaryEntry.objects.filter(
            user=user,
            import_source=source,
            import_source_id=source_id,
        ).first()
        if existing:
            prior_consumed = True
            continue
        repeat = row.get("is_rewatch")
        entry, created = create_log(
            user,
            item,
            consumed_at=row["consumed_at"],
            rating=row.get("rating"),
            review=row.get("review", ""),
            liked=row.get("liked", False),
            is_rewatch=prior_consumed if repeat is None else repeat,
            tags=row.get("tags", []),
            review_title=row.get("review_title", ""),
            contains_spoilers=row.get("contains_spoilers", False),
            import_source=source,
            import_source_id=source_id,
            import_source_order=(
                row.get("source_order")
                if row.get("source_order") is not None
                else original_order
            ),
            emit_activity=False,
            update_current=False,
        )
        if created:
            created_entries.append(entry)
            prior_consumed = True
    tracking = _locked_tracking(user, item)
    if tracking:
        _reconcile_imported_current_state(
            tracking,
            created_entries,
            preserve_rating=rating_is_independent,
            preserve_like=like_is_independent,
        )
        _set_last_consumed(tracking)
    return created_entries


@transaction.atomic
def mark_consumed(user, item):
    """Establish undated direct consumption without a diary event."""
    tracking = _ensure_tracking(user, item, direct=True)
    _set_last_consumed(tracking)
    return tracking


@transaction.atomic
def unwatch(user, item):
    """Remove tracking and current state only when no diary history exists."""
    tracking = _locked_tracking(user, item)
    if tracking is None:
        return None
    if DiaryEntry.objects.filter(user=user, item=item).exists():
        raise DiaryHistoryExists("Delete your logs before marking this as unwatched.")
    MediaLike.objects.filter(user=user, item=item).delete()
    _delete_rating_activity(user, tracking)
    tracking.delete()
    return None


@transaction.atomic
def set_rating(user, item, rating, *, direct=True, emit_activity=True):
    """Set or clear the current rating without rewriting diary ratings."""
    rating = validate_storage_rating(rating)
    tracking = _locked_tracking(user, item)
    if tracking is None:
        if rating is None:
            return None
        tracking = _ensure_tracking(user, item, direct=direct)
    if rating is not None and direct and not tracking.direct_consumption:
        tracking.direct_consumption = True
        tracking.save(update_fields=["direct_consumption"])
    set_current_rating(tracking, rating, emit_activity=emit_activity)
    return tracking


@transaction.atomic
def set_like(user, item, liked, *, direct=True, audit=True):
    """Set the current title heart without rewriting diary hearts."""
    tracking = _locked_tracking(user, item)
    if tracking is None:
        if not liked:
            MediaLike.objects.filter(user=user, item=item).delete()
            return None
        tracking = _ensure_tracking(user, item, direct=direct)
    if liked and direct and not tracking.direct_consumption:
        tracking.direct_consumption = True
        tracking.save(update_fields=["direct_consumption"])
    changed = set_current_like(tracking, liked)
    if audit and changed:
        _audit_like(user, item, liked)
    return tracking


def set_current_rating(tracking, rating, *, emit_activity=True):
    """Set an independent title rating on an existing provenance-aware row."""
    rating = validate_storage_rating(rating)
    fields = []
    if tracking.score != rating:
        tracking.score = rating
        fields.append("score")
    if tracking.rating_source_id is not None:
        tracking.rating_source = None
        fields.append("rating_source")
    if fields:
        tracking.save(update_fields=fields)
    if emit_activity:
        _set_rating_activity(tracking.user, tracking, rating)
    return tracking


def set_current_like(tracking, liked):
    """Set an independent title heart on an existing provenance-aware row."""
    fields = []
    if tracking.like_source_id is not None:
        tracking.like_source = None
        fields.append("like_source")
    if not tracking.like_is_independent:
        tracking.like_is_independent = True
        fields.append("like_is_independent")
    if fields:
        tracking.save(update_fields=fields)
    return _set_like_row(tracking.user, tracking.item, bool(liked))


def couple_current_to_log(tracking, entry):
    """Apply the shared new-log rating and heart source laws."""
    fields = ["like_source", "like_is_independent"]
    if entry.rating is not None:
        tracking.score = entry.rating
        tracking.rating_source = entry
        fields.extend(["score", "rating_source"])
    tracking.like_source = entry
    tracking.like_is_independent = False
    tracking.save(update_fields=fields)
    _set_like_row(tracking.user, tracking.item, bool(entry.liked))
    return tracking


def update_current_from_source_log(tracking, entry, changed_fields):
    """Update current state only when the edited log remains its source."""
    fields = []
    if "rating" in changed_fields and tracking.rating_source_id == entry.id:
        tracking.score = entry.rating
        fields.append("score")
        if entry.rating is None:
            tracking.rating_source = None
            fields.append("rating_source")
    if "liked" in changed_fields and tracking.like_source_id == entry.id:
        _set_like_row(tracking.user, tracking.item, bool(entry.liked))
    if fields:
        tracking.save(update_fields=fields)
    return tracking


def detach_deleted_source_log(tracking, entry_id):
    """Preserve current values but decouple a deleted source log."""
    fields = []
    if tracking.rating_source_id == entry_id:
        tracking.rating_source = None
        fields.append("rating_source")
    if tracking.like_source_id == entry_id:
        tracking.like_source = None
        tracking.like_is_independent = True
        fields.extend(["like_source", "like_is_independent"])
    if fields:
        tracking.save(update_fields=fields)
    return tracking


@transaction.atomic
def create_log(
    user,
    item,
    *,
    consumed_at,
    rating=None,
    review="",
    liked=False,
    is_rewatch=None,
    tags=None,
    review_title="",
    contains_spoilers=False,
    import_source="",
    import_source_id="",
    import_source_order=None,
    emit_activity=True,
    update_current=True,
):
    """Create one dated log and atomically apply its coupled current state."""
    consumed_at = calendar_datetime(consumed_at)
    rating = validate_storage_rating(rating)
    if import_source and import_source_id:
        existing = DiaryEntry.objects.filter(
            user=user,
            import_source=import_source,
            import_source_id=import_source_id,
        ).first()
        if existing:
            return existing, False
    if is_rewatch is None:
        is_rewatch = default_repeat(user, item)
    tracking = _ensure_tracking(user, item, direct=False)
    entry = DiaryEntry.objects.create(
        user=user,
        item=item,
        consumed_at=consumed_at,
        rating=rating,
        review=review,
        liked=bool(liked),
        is_rewatch=bool(is_rewatch),
        review_title=review_title,
        contains_spoilers=contains_spoilers,
        visibility="public",
        import_source=import_source,
        import_source_id=import_source_id,
        import_source_order=import_source_order,
    )
    _replace_tags(entry, tags or [])
    _set_last_consumed(tracking)
    if update_current:
        couple_current_to_log(tracking, entry)
    if emit_activity:
        _create_diary_activity(entry)
    return entry, True


@transaction.atomic
def update_log(entry, data, *, tags=None):
    """Edit a log, changing current state only when this log is its source."""
    if not supports(entry.item):
        raise ValidationError("This diary entry is not single-weight media.")
    tracking = _locked_tracking(entry.user, entry.item)
    rating_is_source = bool(tracking and tracking.rating_source_id == entry.id)
    like_is_source = bool(tracking and tracking.like_source_id == entry.id)
    fields = []
    if "consumed_at" in data:
        entry.consumed_at = calendar_datetime(data["consumed_at"])
        fields.append("consumed_at")
    if "rating" in data:
        entry.rating = validate_storage_rating(data["rating"])
        fields.append("rating")
    for field in (
        "review",
        "review_title",
        "liked",
        "is_rewatch",
        "contains_spoilers",
    ):
        if field in data:
            setattr(entry, field, data[field])
            fields.append(field)
    if entry.visibility != "public":
        entry.visibility = "public"
        fields.append("visibility")
    if fields:
        entry.save(update_fields=[*dict.fromkeys(fields), "updated_at"])
    if tags is not None:
        _replace_tags(entry, tags)
    if tracking is None:
        tracking = _ensure_tracking(entry.user, entry.item, direct=False)
        tracking_fields = ["like_source", "like_is_independent"]
        tracking.like_source = entry
        tracking.like_is_independent = False
        if entry.rating is not None:
            tracking.score = entry.rating
            tracking.rating_source = entry
            tracking_fields.extend(["score", "rating_source"])
        tracking.save(update_fields=tracking_fields)
        _set_like_row(entry.user, entry.item, entry.liked)
        _set_last_consumed(tracking)
        rating_is_source = tracking.rating_source_id == entry.id
        like_is_source = True
    if tracking:
        tracking_fields = []
        if rating_is_source and "rating" in data:
            tracking.score = entry.rating
            tracking_fields.append("score")
            if entry.rating is None:
                tracking.rating_source = None
                tracking_fields.append("rating_source")
        if like_is_source and "liked" in data:
            _set_like_row(entry.user, entry.item, entry.liked)
        if "consumed_at" in data:
            _set_last_consumed(tracking)
        if tracking_fields:
            tracking.save(update_fields=tracking_fields)
    _update_diary_activity(entry)
    _audit_diary(entry, "diary_updated")
    return entry


@transaction.atomic
def delete_log(user, entry):
    """Delete one log without selecting older logs as current-state sources."""
    if entry.user_id != user.id:
        raise ValidationError("You can only delete your own diary entries.")
    item = entry.item
    tracking = _locked_tracking(user, item)
    rating_is_source = bool(tracking and tracking.rating_source_id == entry.id)
    like_is_source = bool(tracking and tracking.like_source_id == entry.id)
    consumed_at = entry.consumed_at
    _delete_diary_activity(user, entry)
    _audit_diary(entry, "diary_deleted")
    entry.delete()
    remaining = DiaryEntry.objects.filter(user=user, item=item).exists()
    if tracking is None:
        _queue_statistics(user.id, consumed_at)
        return
    tracking.refresh_from_db()
    if not remaining and not tracking.direct_consumption:
        MediaLike.objects.filter(user=user, item=item).delete()
        _delete_rating_activity(user, tracking)
        tracking.delete()
    else:
        fields = []
        if rating_is_source:
            tracking.rating_source = None
            fields.append("rating_source")
        if like_is_source:
            tracking.like_source = None
            tracking.like_is_independent = True
            fields.extend(["like_source", "like_is_independent"])
        if fields:
            tracking.save(update_fields=fields)
        _set_last_consumed(tracking)
    _queue_statistics(user.id, consumed_at)


def _tracking_model(item):
    if not supports(item):
        raise ValidationError("Single-weight consumption only supports movies and music.")
    return apps.get_model("app", item.media_type)


def _locked_tracking(user, item):
    return _tracking_model(item).objects.select_for_update().filter(user=user, item=item).first()


def _ensure_tracking(user, item, *, direct):
    model = _tracking_model(item)
    tracking, created = model.objects.select_for_update().get_or_create(
        user=user,
        item=item,
        defaults={
            "status": Status.COMPLETED.value,
            "progress": 1,
            "direct_consumption": direct,
        },
    )
    fields = []
    if tracking.status != Status.COMPLETED.value:
        tracking.status = Status.COMPLETED.value
        fields.append("status")
    if tracking.progress != 1:
        tracking.progress = 1
        fields.append("progress")
    if direct and not tracking.direct_consumption:
        tracking.direct_consumption = True
        fields.append("direct_consumption")
    if fields and not created:
        tracking.save(update_fields=fields)
    return tracking


def _set_last_consumed(tracking):
    latest = DiaryEntry.objects.filter(
        user=tracking.user,
        item=tracking.item,
    ).aggregate(value=Max("consumed_at"))["value"]
    if tracking.end_date != latest:
        tracking.end_date = latest
        tracking.save(update_fields=["end_date"])


def _reconcile_imported_current_state(
    tracking,
    created_entries,
    *,
    preserve_rating,
    preserve_like,
):
    new_ids = {entry.id for entry in created_entries}
    entries = list(DiaryEntry.objects.filter(user=tracking.user, item=tracking.item))

    def source_key(entry):
        return (
            calendar_date(entry.consumed_at),
            entry.id in new_ids,
            entry.import_source_order if entry.id in new_ids else entry.id,
        )

    fields = []
    if not preserve_rating:
        rated_entries = [entry for entry in entries if entry.rating is not None]
        source = max(rated_entries, key=source_key) if rated_entries else None
        tracking.score = source.rating if source else None
        tracking.rating_source = source
        fields.extend(["score", "rating_source"])
    if not preserve_like and entries:
        source = max(entries, key=source_key)
        _set_like_row(tracking.user, tracking.item, source.liked)
        tracking.like_source = source
        tracking.like_is_independent = False
        fields.extend(["like_source", "like_is_independent"])
    if fields:
        tracking.save(update_fields=fields)


def _set_like_row(user, item, liked):
    if supports(item):
        tracking_model = _tracking_model(item)
        if any(field.name == "liked" for field in tracking_model._meta.fields):
            tracking_model.objects.filter(user=user, item=item).update(liked=liked)
    if liked:
        _, created = MediaLike.objects.get_or_create(user=user, item=item)
        return created
    deleted, _ = MediaLike.objects.filter(user=user, item=item).delete()
    return bool(deleted)


def _replace_tags(entry, tag_names):
    entry.tags.clear()
    for name in dict.fromkeys(str(value).strip().lower() for value in tag_names):
        if not name:
            continue
        tag, _ = Tag.objects.get_or_create(name=name)
        DiaryEntryTag.objects.get_or_create(diary_entry=entry, tag=tag)


def _queue_statistics(user_id, consumed_at):
    transaction.on_commit(
        lambda: update_daily_statistics.delay(
            user_id=user_id,
            date_str=calendar_date(consumed_at).isoformat(),
        ),
    )


def _create_diary_activity(entry):
    from social.models import Activity

    Activity.objects.create(
        actor=entry.user,
        verb="diary_created",
        target_type="diary",
        target_id=entry.id,
        item=entry.item,
        visibility="public",
        snapshot={
            "rating": str(rating_to_wire(entry.rating)) if entry.rating is not None else None,
            "liked": entry.liked,
        },
    )


def _update_diary_activity(entry):
    from social.models import Activity

    Activity.objects.filter(
        actor=entry.user,
        verb="diary_created",
        target_type="diary",
        target_id=entry.id,
    ).update(
        visibility="public",
        snapshot={
            "rating": str(rating_to_wire(entry.rating)) if entry.rating is not None else None,
            "liked": entry.liked,
        },
    )


def _delete_diary_activity(user, entry):
    from social.models import Activity

    Activity.objects.filter(actor=user, target_type="diary", target_id=entry.id).delete()


def _set_rating_activity(user, tracking, rating):
    from social.models import Activity

    queryset = Activity.objects.filter(
        actor=user,
        verb="rating_updated",
        target_type="tracking",
        target_id=tracking.id,
        item=tracking.item,
    )
    if rating is None:
        queryset.delete()
        return
    snapshot = {"rating": str(rating_to_wire(rating))}
    activity = queryset.first()
    if activity:
        activity.snapshot = snapshot
        activity.visibility = "public"
        activity.save(update_fields=["snapshot", "visibility"])
    else:
        Activity.objects.create(
            actor=user,
            verb="rating_updated",
            target_type="tracking",
            target_id=tracking.id,
            item=tracking.item,
            visibility="public",
            snapshot=snapshot,
        )


def _delete_rating_activity(user, tracking):
    from social.models import Activity

    Activity.objects.filter(
        actor=user,
        verb="rating_updated",
        target_type="tracking",
        target_id=tracking.id,
        item=tracking.item,
    ).delete()


def _audit_like(user, item, liked):
    from social.models import SocialAuditLog

    SocialAuditLog.objects.create(
        actor=user,
        action="media_like" if liked else "media_unlike",
        target_type="item",
        target_id=item.id,
    )


def _audit_diary(entry, action):
    from social.models import SocialAuditLog

    SocialAuditLog.objects.create(
        actor=entry.user,
        action=action,
        target_type="diary",
        target_id=entry.id,
        metadata={
            "rating": str(rating_to_wire(entry.rating)) if entry.rating is not None else None,
            "consumed_at": calendar_date(entry.consumed_at).isoformat(),
        },
    )
