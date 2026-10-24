"""Authoritative book tracking and reading-journey transitions."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app import single_weight
from app.models import (
    Book,
    BookSession,
    DiaryEntry,
    DiaryEntryTag,
    Item,
    MediaLike,
    Status,
    Tag,
)
from app.tasks import update_daily_statistics

UNSET = object()
OPEN_STATUSES = {Status.IN_PROGRESS.value, Status.PAUSED.value}


class BookTrackingConflict(Exception):
    """A valid request that conflicts with the current book-tracking state."""

    def __init__(self, code, message, *, action=None, **context):
        self.code = code
        self.message = message
        self.action = action
        self.context = context
        super().__init__(message)

    def as_dict(self):
        payload = {"code": self.code, "message": self.message}
        if self.action:
            payload["action"] = self.action
        payload.update(self.context)
        return payload


class CompletionRequired(BookTrackingConflict):
    """A live/paused journey must be completed through its composer."""

    def __init__(self, journey_id):
        self.journey_id = journey_id
        super().__init__(
            "completion_required",
            "Finish this reading journey through its completion log.",
            action="finish",
            journey_id=journey_id,
        )


class HistoryExists(BookTrackingConflict):
    """Simple tracking removal is blocked by real reading history."""

    def __init__(self, message):
        super().__init__("history_exists", message, action="delete_history")


def supports(value):
    """Return whether an item is a book."""
    return getattr(value, "media_type", value) == "book"


def _calendar_datetime(value, *, required=True):
    if value is None and not required:
        return None
    return single_weight.calendar_datetime(value)


def _day(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _date_string(value):
    return _day(value).isoformat() if value else None


def _locked_book(user, item):
    return (
        Book.objects.select_for_update()
        .select_related("item")
        .filter(user=user, item=item)
        .first()
    )


def _ensure_book(user, item):
    book = _locked_book(user, item)
    if book is not None:
        return book, False
    book, created = Book.objects.get_or_create(
        user=user,
        item=item,
        defaults={"status": Status.PLANNING.value, "progress": 0},
    )
    return (
        Book.objects.select_for_update()
        .select_related("item")
        .get(id=book.id),
        created,
    )


def _locked_session(book, session_id=None):
    queryset = BookSession.objects.select_for_update().filter(related_book=book)
    if session_id is not None:
        return queryset.filter(id=session_id).first()
    if book.current_session_id:
        session = queryset.filter(id=book.current_session_id).first()
        if session is not None:
            return session
    return queryset.filter(status__in=OPEN_STATUSES).order_by("-created_at", "-id").first()


def _open_session(book):
    session = _locked_session(book)
    return session if session and session.status in OPEN_STATUSES else None


def _mutation(value):
    if value in (None, ""):
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as error:
        raise ValidationError("mutation_id must be a UUID.") from error


def _journey_progress(session):
    if session is None:
        return None
    total_pages = session.related_book.item.total_pages
    if session.pages_read is None and session.percentage_read is None:
        return None
    if session.pages_read is None:
        return {
            "kind": "percentage",
            "value": float(session.percentage_read),
            "max": 100,
            "unit": "percent",
            "journey_id": session.id,
        }
    return {
        "kind": "pages",
        "value": session.pages_read,
        "max": total_pages,
        "unit": "page",
        "journey_id": session.id,
    }


def _set_book_from_session(book, session):
    book.current_session = session
    book.status = session.status
    book.progress = session.pages_read or 0
    book.start_date = session.start_date
    book.end_date = session.end_date
    book.save(
        update_fields=[
            "current_session",
            "status",
            "progress",
            "start_date",
            "end_date",
            "read_override_active",
        ],
    )
    book._invalidate_progress_cache()


def _clear_override(book):
    book.read_override_active = False
    book.read_override_previous_tracked = False
    book.read_override_previous_status = None
    book.read_override_previous_session = None


def _deactivate_override(book):
    was_active = book.read_override_active
    book.read_override_active = False
    return was_active


def _queue_statistics(user_id, value):
    transaction.on_commit(
        lambda: update_daily_statistics.delay(
            user_id=user_id,
            date_str=_day(value).isoformat(),
        ),
    )


def _has_completed_history(book, *, exclude_session=None):
    sessions = book.reading_sessions.filter(
        status=Status.COMPLETED.value,
        completion_diary_entry__isnull=False,
    )
    if exclude_session is not None:
        sessions = sessions.exclude(id=exclude_session.id)
    return book.completed_manually or sessions.exists()


def _has_prior_completion(book, completed_at, *, exclude_session=None):
    if book.completed_manually:
        return True
    sessions = book.reading_sessions.filter(
        status=Status.COMPLETED.value,
        completion_diary_entry__isnull=False,
        end_date__lte=completed_at,
    )
    if exclude_session is not None:
        sessions = sessions.exclude(id=exclude_session.id)
    return sessions.exists()


def _validate_journey_date(session, value, *, ending=False):
    moment = _calendar_datetime(value)
    day = _day(moment)
    if session.start_date and day < _day(session.start_date):
        raise ValidationError("Journey end dates cannot predate the start date.")
    if ending and session.progressed_on and day < session.progressed_on:
        raise ValidationError("Journey end dates cannot predate the latest progress update.")
    return moment


@transaction.atomic
def assign_status(user, item, status, *, start_date=None, mutation_id=None):
    """Apply one canonical direct or live book status transition."""
    if status not in Status.values:
        raise ValidationError("Invalid book status.")
    book, _ = _ensure_book(user, item)
    open_journey = _open_session(book)
    if status == Status.IN_PROGRESS.value:
        return start(
            user,
            item,
            start_date=start_date,
            mutation_id=mutation_id,
        )
    if status == Status.COMPLETED.value:
        return mark_read(user, item)
    if status == Status.PLANNING.value and open_journey:
        raise ValidationError("Pause or mark this reading journey DNF before moving it to To Read.")
    if status == Status.PAUSED.value and open_journey:
        return pause(user, item)
    if status == Status.DROPPED.value and open_journey:
        return drop(user, item)
    if book.status == status and book.current_session_id:
        return book

    _clear_override(book)
    book.status = status
    book.current_session = None
    book.progress = 0
    book.start_date = None
    book.end_date = None
    book.save(
        update_fields=[
            "status",
            "current_session",
            "progress",
            "start_date",
            "end_date",
            "read_override_active",
            "read_override_previous_tracked",
            "read_override_previous_status",
            "read_override_previous_session",
        ],
    )
    return book


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
):
    """Apply generic API tracking fields without bypassing book laws."""
    book = _locked_book(user, item)
    if status is not UNSET:
        book = assign_status(
            user,
            item,
            status,
            start_date=None if start_date is UNSET else start_date,
            mutation_id=mutation_id,
        )
    if rating is not UNSET:
        book = set_rating(user, item, rating)
    if book is None and (notes is not UNSET or start_date is not UNSET):
        book, _ = _ensure_book(user, item)
    if book is not None and notes is not UNSET and book.notes != notes:
        book.notes = notes
        book.save(update_fields=["notes"])
    return book


@transaction.atomic
def start(user, item, *, start_date=None, mutation_id=None):
    """Start a new journey or resume the current paused journey."""
    book, created = _ensure_book(user, item)
    mutation_id = _mutation(mutation_id)
    if mutation_id:
        existing = book.reading_sessions.filter(mutation_id=mutation_id).first()
        if existing:
            _set_book_from_session(book, existing)
            return book
    current = _open_session(book)
    if current:
        if current.status == Status.PAUSED.value:
            current.status = Status.IN_PROGRESS.value
            current.save(update_fields=["status"])
            _set_book_from_session(book, current)
        return book

    started_at = _calendar_datetime(start_date or timezone.localdate())
    session = BookSession.objects.create(
        related_book=book,
        status=Status.IN_PROGRESS.value,
        start_date=started_at,
        origin=BookSession.Origin.LIVE,
        previous_tracked=not created,
        previous_status=None if created else book.status,
        previous_session=None if created else book.current_session,
        previous_read_override=_deactivate_override(book),
        mutation_id=mutation_id,
    )
    _set_book_from_session(book, session)
    return book


@transaction.atomic
def pause(user, item):
    book = _locked_book(user, item)
    if book is None:
        raise ValidationError("This book is not being read.")
    session = _open_session(book)
    if session is None:
        if book.status == Status.PAUSED.value:
            return book
        raise ValidationError("Only a current reading journey can be paused.")
    if session.status == Status.IN_PROGRESS.value:
        session.status = Status.PAUSED.value
        session.save(update_fields=["status"])
    _set_book_from_session(book, session)
    return book


@transaction.atomic
def resume(user, item, *, start_date=None, mutation_id=None):
    book = _locked_book(user, item)
    if book is None or book.status != Status.PAUSED.value:
        raise ValidationError("Only a paused book can be resumed.")
    session = _open_session(book)
    if session:
        session.status = Status.IN_PROGRESS.value
        session.save(update_fields=["status"])
        _set_book_from_session(book, session)
        return book
    return start(user, item, start_date=start_date, mutation_id=mutation_id)


@transaction.atomic
def drop(user, item, *, end_date=None):
    book = _locked_book(user, item)
    if book is None:
        return assign_status(user, item, Status.DROPPED.value)
    session = _open_session(book)
    if session is None:
        return assign_status(user, item, Status.DROPPED.value)
    session.status = Status.DROPPED.value
    session.end_date = _validate_journey_date(
        session,
        end_date or timezone.localdate(),
        ending=True,
    )
    session.save(update_fields=["status", "end_date"])
    _set_book_from_session(book, session)
    return book


@transaction.atomic
def restart(user, item, *, start_date=None, end_date=None, mutation_id=None):
    """Close the current journey DNF and atomically begin again at zero."""
    book = _locked_book(user, item)
    if book is None:
        raise ValidationError("This book has no reading journey to restart.")
    mutation_id = _mutation(mutation_id)
    if mutation_id:
        existing = book.reading_sessions.filter(mutation_id=mutation_id).first()
        if existing:
            _set_book_from_session(book, existing)
            return book
    old = _open_session(book)
    if old is None:
        raise ValidationError("Only a current or paused journey can be restarted.")
    old.status = Status.DROPPED.value
    old.end_date = _validate_journey_date(
        old,
        end_date or timezone.localdate(),
        ending=True,
    )
    old.save(update_fields=["status", "end_date"])
    started_at = _calendar_datetime(start_date or timezone.localdate())
    new = BookSession.objects.create(
        related_book=book,
        status=Status.IN_PROGRESS.value,
        start_date=started_at,
        origin=BookSession.Origin.LIVE,
        previous_tracked=True,
        previous_status=Status.DROPPED.value,
        previous_session=old,
        previous_read_override=False,
        mutation_id=mutation_id,
    )
    _set_book_from_session(book, new)
    return book


@transaction.atomic
def update_progress(
    user,
    item,
    *,
    progress_type,
    value,
    notes="",
    progressed_on=None,
):
    """Save the current journey position without completing it."""
    book = _locked_book(user, item)
    if book is None:
        book = start(user, item)
    session = _open_session(book)
    if session is None and book.status == Status.IN_PROGRESS.value:
        legacy_progress = book.progress
        book = start(user, item)
        session = _open_session(book)
        if session is not None and legacy_progress:
            session.pages_read = legacy_progress
            if item.total_pages:
                session.percentage_read = (
                    Decimal(legacy_progress * 100) / Decimal(item.total_pages)
                )
            session.save(update_fields=["pages_read", "percentage_read"])
    if session is None or session.status != Status.IN_PROGRESS.value:
        raise ValidationError("Progress can only be updated while Currently Reading.")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError("Enter a valid progress value.") from error
    if amount < 0:
        raise ValidationError("Progress cannot be negative.")

    total_pages = item.total_pages
    pages = None
    percentage = None
    if progress_type == "pages":
        if amount != amount.to_integral_value():
            raise ValidationError("Page progress must be a whole number.")
        pages = int(amount)
        if total_pages and pages > total_pages:
            raise ValidationError("Page progress cannot exceed the book's total pages.")
        if total_pages:
            percentage = Decimal(pages * 100) / Decimal(total_pages)
    elif progress_type == "percentage":
        if amount > 100:
            raise ValidationError("Percentage cannot exceed 100.")
        percentage = amount
        if total_pages:
            pages = int(round(float(amount / 100 * total_pages)))
    else:
        raise ValidationError("progress_type must be pages or percentage.")

    progress_day = _day(_calendar_datetime(progressed_on or timezone.localdate()))
    if session.start_date and progress_day < _day(session.start_date):
        raise ValidationError("Progress updates cannot predate the journey start.")
    previous = _journey_progress(session)
    session.pages_read = pages
    session.percentage_read = percentage
    session.progressed_on = progress_day
    session.notes = notes
    session.save(
        update_fields=["pages_read", "percentage_read", "progressed_on", "notes"],
    )
    _set_book_from_session(book, session)
    current = _journey_progress(session)
    if previous != current:
        _record_progress_change(user, item, previous, current)
    return book


@transaction.atomic
def mark_read(user, item):
    """Establish one undated Read fact or use existing completion history."""
    book, created = _ensure_book(user, item)
    open_journey = _open_session(book)
    if open_journey:
        raise CompletionRequired(open_journey.id)
    if book.status == Status.COMPLETED.value:
        return book
    if _has_completed_history(book):
        book.read_override_active = True
        book.read_override_previous_tracked = not created
        book.read_override_previous_status = book.status
        book.read_override_previous_session = book.current_session
    elif not book.completed_manually:
        book.completed_manually = True
        book.undated_read_previous_tracked = not created
        book.undated_read_previous_status = None if created else book.status
        book.undated_read_previous_session = None if created else book.current_session
        book.undated_read_previous_override = _deactivate_override(book)
    book.status = Status.COMPLETED.value
    book.current_session = None
    book.progress = 0
    book.start_date = None
    book.end_date = None
    book.save(
        update_fields=[
            "completed_manually",
            "undated_read_previous_tracked",
            "undated_read_previous_status",
            "undated_read_previous_session",
            "undated_read_previous_override",
            "read_override_active",
            "read_override_previous_tracked",
            "read_override_previous_status",
            "read_override_previous_session",
            "status",
            "current_session",
            "progress",
            "start_date",
            "end_date",
        ],
    )
    return book


def _restore_marker_state(book):
    if book.undated_read_previous_tracked:
        book.status = book.undated_read_previous_status or Status.PLANNING.value
        book.current_session = book.undated_read_previous_session
        book.progress = (
            book.current_session.pages_read
            if book.current_session and book.current_session.pages_read is not None
            else 0
        )
        book.start_date = book.current_session.start_date if book.current_session else None
        book.end_date = book.current_session.end_date if book.current_session else None
        book.read_override_active = book.undated_read_previous_override
        return book
    return None


@transaction.atomic
def undo_read(user, item):
    """Undo only the reversible current Read state."""
    book = _locked_book(user, item)
    if book is None:
        return None
    if book.current_session and book.current_session.status == Status.COMPLETED.value:
        raise HistoryExists("Delete the completion log before undoing Read.")
    if book.read_override_active:
        previous_tracked = book.read_override_previous_tracked
        book.status = book.read_override_previous_status or Status.PLANNING.value
        book.current_session = book.read_override_previous_session
        _clear_override(book)
        book.progress = book.current_session.pages_read or 0 if book.current_session else 0
        book.start_date = book.current_session.start_date if book.current_session else None
        book.end_date = book.current_session.end_date if book.current_session else None
        book.save(
            update_fields=[
                "status",
                "current_session",
                "progress",
                "start_date",
                "end_date",
                "read_override_active",
                "read_override_previous_tracked",
                "read_override_previous_status",
                "read_override_previous_session",
            ],
        )
        if not previous_tracked and not _other_history(book):
            MediaLike.objects.filter(user=user, item=item).delete()
            book.delete()
            return None
        return book
    if not book.completed_manually:
        raise HistoryExists("Delete the completion log before undoing Read.")
    return delete_undated_read(user, item, clear_current_state=True)


@transaction.atomic
def delete_undated_read(user, item, *, clear_current_state=True):
    """Delete the independent undated Read history fact."""
    book = _locked_book(user, item)
    if book is None or not book.completed_manually:
        return book
    marker_is_current = (
        book.status == Status.COMPLETED.value
        and book.current_session_id is None
        and not book.read_override_active
    )
    restored = _restore_marker_state(book) if marker_is_current else book
    book.completed_manually = False
    book.undated_read_previous_tracked = False
    book.undated_read_previous_status = None
    book.undated_read_previous_session = None
    book.undated_read_previous_override = False
    if marker_is_current and clear_current_state:
        book.score = None
        book.rating_source = None
        book.like_source = None
        book.like_is_independent = False
        MediaLike.objects.filter(user=user, item=item).delete()
    if restored is None and not _other_history(book):
        MediaLike.objects.filter(user=user, item=item).delete()
        book.delete()
        return None
    fields = [
        "completed_manually",
        "undated_read_previous_tracked",
        "undated_read_previous_status",
        "undated_read_previous_session",
        "undated_read_previous_override",
    ]
    if marker_is_current:
        fields.extend(
            [
                "status",
                "current_session",
                "progress",
                "start_date",
                "end_date",
                "read_override_active",
            ],
        )
    if clear_current_state:
        fields.extend(["score", "rating_source", "like_source", "like_is_independent"])
    book.save(update_fields=list(dict.fromkeys(fields)))
    return book


@transaction.atomic
def set_rating(user, item, rating):
    """Set/clear the independent current title rating."""
    rating = single_weight.validate_storage_rating(rating)
    book = _locked_book(user, item)
    if rating is not None:
        if book and _open_session(book):
            raise CompletionRequired(_open_session(book).id)
        book = mark_read(user, item)
    if book is None:
        return None
    rating_changed = book.score != rating
    single_weight.set_current_rating(book, rating)
    if rating is not None and rating_changed:
        source = (
            book.reading_sessions.filter(
                status=Status.COMPLETED.value,
                completion_diary_entry__rating=rating,
            )
            .order_by("-end_date", "-created_at", "-id")
            .first()
        )
        if source:
            book.rating_source = source.completion_diary_entry
            book.save(update_fields=["rating_source"])
    return book


@transaction.atomic
def set_like(user, item, liked):
    """Set the independent current title heart."""
    book = _locked_book(user, item)
    if liked:
        if book and _open_session(book):
            raise CompletionRequired(_open_session(book).id)
        book = mark_read(user, item)
    if book is None:
        MediaLike.objects.filter(user=user, item=item).delete()
        return None
    changed = single_weight.set_current_like(book, bool(liked))
    source = (
        book.reading_sessions.filter(
            status=Status.COMPLETED.value,
            completion_diary_entry__liked=bool(liked),
        )
        .order_by("-end_date", "-created_at", "-id")
        .first()
    )
    if source and changed:
        book.like_source = source.completion_diary_entry
        book.like_is_independent = False
        book.save(update_fields=["like_source", "like_is_independent"])
    if changed:
        single_weight._audit_like(user, item, bool(liked))
    return book


def _add_tags(entry, names):
    for name in dict.fromkeys(str(value).strip().lower() for value in names or []):
        if not name:
            continue
        tag, _ = Tag.objects.get_or_create(name=name)
        DiaryEntryTag.objects.get_or_create(diary_entry=entry, tag=tag)


def _set_like_row(user, item, liked):
    if liked:
        MediaLike.objects.get_or_create(user=user, item=item)
    else:
        MediaLike.objects.filter(user=user, item=item).delete()


def _create_activity(entry):
    from social.models import Activity

    Activity.objects.create(
        actor=entry.user,
        verb="diary_created",
        target_type="diary",
        target_id=entry.id,
        item=entry.item,
        visibility="public",
        snapshot={
            "rating": (
                str(single_weight.rating_to_wire(entry.rating))
                if entry.rating is not None
                else None
            ),
            "liked": entry.liked,
        },
    )


@transaction.atomic
def complete(
    user,
    item,
    *,
    completion_date,
    journey_id=None,
    rating=None,
    liked=False,
    is_rewatch=None,
    review="",
    review_title="",
    contains_spoilers=False,
    tags=None,
    mutation_id=None,
    import_source="",
    import_source_id="",
    import_source_order=None,
    emit_activity=True,
    update_current=True,
):
    """Atomically create one completion log and complete its journey."""
    rating = single_weight.validate_storage_rating(rating)
    completed_at = _calendar_datetime(completion_date)
    mutation_id = _mutation(mutation_id)
    if import_source and import_source_id:
        existing_entry = DiaryEntry.objects.select_for_update().filter(
            user=user,
            import_source=import_source,
            import_source_id=import_source_id,
        ).first()
        if existing_entry:
            if existing_entry.item_id != item.id:
                raise ValidationError("This imported source record already belongs to another item.")
            existing_book = _locked_book(user, item)
            if existing_book is None:
                raise ValidationError("Imported completion tracking is missing.")
            return existing_book, existing_entry
    book, created = _ensure_book(user, item)
    if mutation_id:
        prior = book.reading_sessions.filter(mutation_id=mutation_id).first()
        if prior and prior.completion_diary_entry_id:
            return book, prior.completion_diary_entry

    session = _locked_session(book, journey_id) if journey_id else _open_session(book)
    if journey_id and session is None:
        raise ValidationError("Reading journey not found.")
    if session and session.status == Status.COMPLETED.value:
        if session.completion_diary_entry_id:
            return book, session.completion_diary_entry
        raise ValidationError("This journey is already completed.")
    if session and session.status not in OPEN_STATUSES:
        session = None

    true_reread = _has_prior_completion(
        book,
        completed_at,
        exclude_session=session,
    )
    if session:
        completed_at = _validate_journey_date(session, completed_at, ending=True)
        session.pre_completion_status = session.status
        session.pre_completion_progress = {
            "pages_read": session.pages_read,
            "percentage_read": (
                str(session.percentage_read)
                if session.percentage_read is not None
                else None
            ),
            "progressed_on": _date_string(session.progressed_on),
            "book_progress": book.progress,
            "book_start_date": _date_string(book.start_date),
            "book_end_date": _date_string(book.end_date),
            "book_score": str(book.score) if book.score is not None else None,
            "rating_source_id": book.rating_source_id,
            "title_liked": MediaLike.objects.filter(user=user, item=item).exists(),
            "like_source_id": book.like_source_id,
            "like_is_independent": book.like_is_independent,
        }
    else:
        session = BookSession.objects.create(
            related_book=book,
            status=Status.COMPLETED.value,
            end_date=completed_at,
            origin=BookSession.Origin.DIRECT_LOG,
            previous_tracked=not created,
            previous_status=None if created else book.status,
            previous_session=None if created else book.current_session,
            previous_read_override=_deactivate_override(book),
            mutation_id=mutation_id,
        )

    total_pages = item.total_pages
    session.status = Status.COMPLETED.value
    session.end_date = completed_at
    session.percentage_read = Decimal(100)
    if total_pages:
        session.pages_read = total_pages
    entry = DiaryEntry.objects.create(
        user=user,
        item=item,
        consumed_at=completed_at,
        rating=rating,
        review=review,
        review_title=review_title,
        liked=bool(liked),
        is_rewatch=true_reread if is_rewatch is None else bool(is_rewatch),
        contains_spoilers=contains_spoilers,
        visibility="public",
        progress_snapshot=_journey_progress(session),
        import_source=import_source,
        import_source_id=import_source_id,
        import_source_order=import_source_order,
    )
    _add_tags(entry, tags)
    session.completion_diary_entry = entry
    session._history_date = completed_at
    session.save(
        update_fields=[
            "status",
            "end_date",
            "pages_read",
            "percentage_read",
            "pre_completion_status",
            "pre_completion_progress",
            "completion_diary_entry",
        ],
    )
    if update_current:
        book.status = Status.COMPLETED.value
        book.current_session = session
        book.progress = total_pages or session.pages_read or 0
        book.end_date = completed_at
        book.completion_diary_entry = entry
        if rating is not None:
            book.score = rating
            book.rating_source = entry
        book.like_source = entry
        book.like_is_independent = False
        book._history_date = completed_at
        book.save(
            update_fields=[
                "status",
                "current_session",
                "progress",
                "end_date",
                "completion_diary_entry",
                "score",
                "rating_source",
                "like_source",
                "like_is_independent",
                "read_override_active",
            ],
        )
        _set_like_row(user, item, bool(liked))
    if emit_activity:
        _create_activity(entry)
    _queue_statistics(user.id, completed_at)
    return book, entry


@transaction.atomic
def update_completion(entry, data, *, tags=UNSET):
    """Edit a completion log and its linked journey/provenance."""
    if not supports(entry.item):
        raise ValidationError("This is not a book completion log.")
    book = _locked_book(entry.user, entry.item)
    if book is None:
        raise ValidationError("Book tracking not found.")
    session = BookSession.objects.select_for_update().filter(
        related_book=book,
        completion_diary_entry=entry,
    ).first()
    if session is None:
        raise ValidationError("This diary entry is not linked to a reading journey.")
    rating_is_source = book.rating_source_id == entry.id
    like_is_source = book.like_source_id == entry.id
    previous_consumed_at = entry.consumed_at
    fields = []
    if "consumed_at" in data:
        entry.consumed_at = _validate_journey_date(
            session,
            data["consumed_at"],
            ending=True,
        )
        session.end_date = entry.consumed_at
        session.save(update_fields=["end_date"])
        fields.append("consumed_at")
        if book.current_session_id == session.id:
            book.end_date = entry.consumed_at
    if "rating" in data:
        previous_rating = entry.rating
        entry.rating = single_weight.validate_storage_rating(data["rating"])
        fields.append("rating")
        if rating_is_source:
            book.score = entry.rating
            if entry.rating is None:
                book.rating_source = None
        else:
            single_weight.fill_empty_current_rating(book, entry, previous_rating)
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
    if fields:
        entry.save(update_fields=[*dict.fromkeys(fields), "updated_at"])
    if tags is not UNSET:
        entry.tags.clear()
        _add_tags(entry, tags)
    if like_is_source and "liked" in data:
        _set_like_row(entry.user, entry.item, entry.liked)
    book.save(update_fields=["score", "rating_source", "end_date"])
    _update_activity(entry)
    single_weight._audit_diary(entry, "diary_updated")
    _queue_statistics(entry.user_id, previous_consumed_at)
    if _day(entry.consumed_at) != _day(previous_consumed_at):
        _queue_statistics(entry.user_id, entry.consumed_at)
    return entry


def _update_activity(entry):
    from social.models import Activity

    Activity.objects.filter(
        actor=entry.user,
        target_type="diary",
        target_id=entry.id,
    ).update(
        snapshot={
            "rating": (
                str(single_weight.rating_to_wire(entry.rating))
                if entry.rating is not None
                else None
            ),
            "liked": entry.liked,
        },
    )


def _other_history(book, *, excluding_session=None, excluding_entry=None):
    sessions = book.reading_sessions.all()
    if excluding_session:
        sessions = sessions.exclude(id=excluding_session.id)
    entries = DiaryEntry.objects.filter(user=book.user, item=book.item)
    if excluding_entry:
        entries = entries.exclude(id=excluding_entry.id)
    return book.completed_manually or sessions.exists() or entries.exists()


def _restore_previous(book, session):
    previous = session.previous_session
    if session.previous_tracked:
        book.status = session.previous_status or Status.PLANNING.value
        book.current_session = previous
        book.progress = previous.pages_read or 0 if previous else 0
        book.start_date = previous.start_date if previous else None
        book.end_date = previous.end_date if previous else None
        book.read_override_active = session.previous_read_override
        book.save(
            update_fields=[
                "status",
                "current_session",
                "progress",
                "start_date",
                "end_date",
                "read_override_active",
            ],
        )
        return book
    if _other_history(book, excluding_session=session):
        book.current_session = None
        book.status = Status.COMPLETED.value if book.completed_manually else Status.PLANNING.value
        book.progress = 0
        book.start_date = None
        book.end_date = None
        book.save(
            update_fields=["current_session", "status", "progress", "start_date", "end_date"],
        )
        return book
    MediaLike.objects.filter(user=book.user, item=book.item).delete()
    book.delete()
    return None


def _splice_session(book, session):
    book.reading_sessions.filter(previous_session=session).update(
        previous_session=session.previous_session,
        previous_status=session.previous_status,
        previous_tracked=session.previous_tracked,
        previous_read_override=session.previous_read_override,
    )
    if book.undated_read_previous_session_id == session.id:
        book.undated_read_previous_session = session.previous_session
        book.undated_read_previous_status = session.previous_status
        book.undated_read_previous_tracked = session.previous_tracked
        book.undated_read_previous_override = session.previous_read_override
    if book.read_override_previous_session_id == session.id:
        book.read_override_previous_session = session.previous_session
        book.read_override_previous_status = session.previous_status
    book.save(
        update_fields=[
            "undated_read_previous_session",
            "undated_read_previous_status",
            "undated_read_previous_tracked",
            "undated_read_previous_override",
            "read_override_previous_session",
            "read_override_previous_status",
        ],
    )


def _delete_progress_history(user, item, journey_id):
    from social.models import Activity, ProgressChange

    changes = ProgressChange.objects.filter(
        actor=user,
        item=item,
        current_progress__journey_id=journey_id,
    )
    ids = list(changes.values_list("id", flat=True))
    Activity.objects.filter(target_type="progress_change", target_id__in=ids).delete()
    changes.delete()


@transaction.atomic
def delete_completion(user, entry):
    """Delete one completion and restore only when it is current."""
    if entry.user_id != user.id or not supports(entry.item):
        raise ValidationError("You can only delete your own book completion logs.")
    item = entry.item
    consumed_at = entry.consumed_at
    book = _locked_book(user, item)
    if book is None:
        entry.delete()
        _queue_statistics(user.id, consumed_at)
        return None
    session = BookSession.objects.select_for_update().filter(
        related_book=book,
        completion_diary_entry=entry,
    ).first()
    rating_is_source = book.rating_source_id == entry.id
    like_is_source = book.like_source_id == entry.id
    from social.models import Activity

    Activity.objects.filter(actor=user, target_type="diary", target_id=entry.id).delete()
    single_weight._audit_diary(entry, "diary_deleted")
    entry.delete()
    _queue_statistics(user.id, consumed_at)
    if session is None:
        return book
    current = book.current_session_id == session.id
    restored_live_state = False
    if current and session.origin == BookSession.Origin.LIVE and session.pre_completion_status:
        snapshot = session.pre_completion_progress or {}
        session.status = session.pre_completion_status
        session.pages_read = snapshot.get("pages_read")
        session.percentage_read = snapshot.get("percentage_read")
        session.progressed_on = snapshot.get("progressed_on")
        session.end_date = None
        session.completion_diary_entry = None
        session.pre_completion_status = None
        session.pre_completion_progress = None
        session.save(
            update_fields=[
                "status",
                "pages_read",
                "percentage_read",
                "progressed_on",
                "end_date",
                "completion_diary_entry",
                "pre_completion_status",
                "pre_completion_progress",
            ],
        )
        _set_book_from_session(book, session)
        score = snapshot.get("book_score")
        book.score = Decimal(score) if score is not None else None
        book.rating_source_id = snapshot.get("rating_source_id")
        book.like_source_id = snapshot.get("like_source_id")
        book.like_is_independent = bool(snapshot.get("like_is_independent"))
        book.save(
            update_fields=[
                "score",
                "rating_source",
                "like_source",
                "like_is_independent",
            ],
        )
        _set_like_row(user, item, bool(snapshot.get("title_liked")))
        restored_live_state = True
    else:
        if current:
            _restore_previous(book, session)
        if Book.objects.filter(id=book.id).exists():
            _splice_session(book, session)
        _delete_progress_history(user, item, session.id)
        session.delete()
    book = Book.objects.filter(id=book.id).first()
    if book:
        fields = []
        if rating_is_source and not restored_live_state:
            book.rating_source = None
            fields.append("rating_source")
        if like_is_source and not restored_live_state:
            book.like_source = None
            book.like_is_independent = True
            fields.extend(["like_source", "like_is_independent"])
        latest = book.reading_sessions.filter(
            status=Status.COMPLETED.value,
            completion_diary_entry__isnull=False,
        ).order_by("-end_date", "-created_at", "-id").first()
        book.completion_diary_entry = latest.completion_diary_entry if latest else None
        fields.append("completion_diary_entry")
        if fields:
            book.save(update_fields=fields)
    return book


@transaction.atomic
def update_journey(user, item, journey_id, *, start_date=UNSET, end_date=UNSET):
    """Edit journey dates while preserving chronology."""
    book = _locked_book(user, item)
    if book is None:
        raise ValidationError("Book tracking not found.")
    session = _locked_session(book, journey_id)
    if session is None:
        raise ValidationError("Reading journey not found.")
    if session.completion_diary_entry_id and end_date is not UNSET:
        raise ValidationError("Edit a completed journey's date through its diary log.")
    fields = []
    if start_date is not UNSET:
        started_at = _calendar_datetime(start_date)
        if session.progressed_on and _day(started_at) > session.progressed_on:
            raise ValidationError("Journey starts cannot follow their latest progress update.")
        if session.end_date and _day(started_at) > _day(session.end_date):
            raise ValidationError("Journey starts cannot follow their end date.")
        session.start_date = started_at
        fields.append("start_date")
    if end_date is not UNSET:
        session.end_date = _validate_journey_date(session, end_date, ending=True)
        fields.append("end_date")
    if fields:
        session.save(update_fields=fields)
    if book.current_session_id == session.id:
        _set_book_from_session(book, session)
    return book


@transaction.atomic
def delete_journey(user, item, journey_id):
    """Delete one active, paused, or DNF journey and its progress history."""
    book = _locked_book(user, item)
    if book is None:
        return None
    session = _locked_session(book, journey_id)
    if session is None:
        return book
    if session.completion_diary_entry_id or session.status == Status.COMPLETED.value:
        raise HistoryExists("Delete the completion log instead of the completed journey.")
    current = book.current_session_id == session.id
    if current:
        restored = _restore_previous(book, session)
    else:
        restored = book
    if restored:
        _splice_session(restored, session)
    _delete_progress_history(user, item, session.id)
    session.delete()
    return Book.objects.filter(id=book.id).first()


@transaction.atomic
def remove_tracking(user, item):
    """Remove direct-only tracking, never real reading history."""
    book = _locked_book(user, item)
    if book is None:
        return None
    real_sessions = book.reading_sessions.filter(
        Q(origin__in=[BookSession.Origin.LIVE, BookSession.Origin.DIRECT_LOG])
        | Q(completion_diary_entry__isnull=False)
        | Q(status__in=[Status.IN_PROGRESS.value, Status.PAUSED.value, Status.DROPPED.value]),
    ).exists()
    if real_sessions or DiaryEntry.objects.filter(user=user, item=item).exists():
        raise HistoryExists("Delete reading journeys and completion logs before removing this book.")
    MediaLike.objects.filter(user=user, item=item).delete()
    book.delete()
    return None


def _record_progress_change(user, item, previous, current):
    from social.models import Activity, ProgressChange

    change = ProgressChange.objects.create(
        actor=user,
        item=item,
        previous_progress=previous or {
            "kind": current["kind"],
            "value": 0,
            "max": current["max"],
            "unit": current["unit"],
            "journey_id": current["journey_id"],
        },
        current_progress=current,
    )
    Activity.objects.create(
        actor=user,
        verb="progress_updated",
        target_type="progress_change",
        target_id=change.id,
        item=item,
        snapshot={"previous": change.previous_progress, "current": current},
    )


def is_true_reread(session):
    """Derive reread from undated and chronologically earlier completions."""
    book = session.related_book
    if book.completed_manually:
        return True
    if not session.end_date:
        return _has_completed_history(book, exclude_session=session)
    return book.reading_sessions.filter(
        status=Status.COMPLETED.value,
        completion_diary_entry__isnull=False,
    ).exclude(id=session.id).filter(
        Q(end_date__lt=session.end_date)
        | Q(end_date=session.end_date, created_at__lt=session.created_at)
        | Q(end_date=session.end_date, created_at=session.created_at, id__lt=session.id),
    ).exists()


def journey_payload(session):
    """Serialize a journey for native clients."""
    return {
        "id": session.id,
        "status": session.status,
        "origin": session.origin,
        "start_date": _date_string(session.start_date),
        "end_date": _date_string(session.end_date),
        "progress": _journey_progress(session),
        "completion_diary_entry_id": session.completion_diary_entry_id,
        "is_reread": is_true_reread(session),
    }


def completion_required(book):
    """Return whether current final progress should present completion."""
    session = book.current_session
    if session is None or session.status not in OPEN_STATUSES:
        return False
    if session.percentage_read is not None and session.percentage_read >= 100:
        return True
    return bool(
        book.item.total_pages
        and session.pages_read is not None
        and session.pages_read >= book.item.total_pages
    )


def state_payload(book):
    """Serialize the additive native `book` tracking object."""
    current = book.current_session
    completed = list(
        book.reading_sessions.filter(
            status=Status.COMPLETED.value,
            completion_diary_entry__isnull=False,
        ).order_by("end_date", "created_at", "id"),
    )
    history = list(
        book.reading_sessions.filter(status=Status.DROPPED.value).order_by(
            "-end_date",
            "-created_at",
            "-id",
        ),
    )
    real_history = book.reading_sessions.filter(
        Q(origin__in=[BookSession.Origin.LIVE, BookSession.Origin.DIRECT_LOG])
        | Q(completion_diary_entry__isnull=False)
        | Q(status__in=[Status.IN_PROGRESS.value, Status.PAUSED.value, Status.DROPPED.value]),
    ).exists()
    diary_exists = DiaryEntry.objects.filter(user=book.user, item=book.item).exists()
    can_remove = not real_history and not diary_exists
    if current and current.status in {
        Status.IN_PROGRESS.value,
        Status.PAUSED.value,
        Status.DROPPED.value,
        Status.COMPLETED.value,
    }:
        source = "journey"
    elif book.read_override_active and book.status == Status.COMPLETED.value:
        source = "history_override"
    elif book.completed_manually and book.status == Status.COMPLETED.value:
        source = "undated_read"
    else:
        source = "direct_status"

    open_journey = current if current and current.status in OPEN_STATUSES else None
    actions = ["currently_reading", "planning", "paused", "drop", "log", "mark_read"]
    reasons = {}
    if open_journey:
        actions.extend(["finish", "restart", "delete_journey"])
        actions.append("pause" if open_journey.status == Status.IN_PROGRESS.value else "resume")
        reasons["planning"] = "Pause or mark this reading journey DNF first."
        reasons["mark_read"] = "Finish this reading journey through its completion log."
    if book.read_override_active or (
        book.completed_manually
        and book.status == Status.COMPLETED.value
        and current is None
    ):
        actions.append("undo_read")
    elif current and current.status == Status.COMPLETED.value:
        reasons["undo_read"] = "Delete the completion log to undo this read."
    if book.completed_manually:
        actions.append("delete_undated_read")
    if can_remove:
        actions.append("remove_tracking")
    else:
        reasons["remove_tracking"] = (
            "Delete reading journeys and completion logs before removing this book."
        )

    return {
        "current_journey": journey_payload(current) if current else None,
        "reading_history": [journey_payload(session) for session in history],
        "undated_read": (
            {"id": "undated", "status": Status.COMPLETED.value, "date": None}
            if book.completed_manually
            else None
        ),
        "status_source": source,
        "completion_dates": [_date_string(session.end_date) for session in completed],
        "completed_journey_count": len(completed),
        "lifetime_read_count": len(completed) + int(book.completed_manually),
        "is_rereading": bool(
            open_journey and (book.completed_manually or completed)
        ),
        "completion_required": completion_required(book),
        "can_remove_tracking": can_remove,
        "remove_tracking_reason": reasons.get("remove_tracking"),
        "available_actions": list(dict.fromkeys(actions)),
        "action_reasons": reasons,
    }


# Stable API-facing lifecycle names. The short implementations remain private to
# this module's original call sites while these names form the wiring contract.
start_journey = start
pause_journey = pause
resume_journey = resume
drop_journey = drop
restart_journey = restart
