"""Authoritative manga and comic tracking (docs/manga-comics-tracking-contract.md).

Manga and comics are books counted in chapters or issues, so these transitions follow
app.book_tracking, with its journeys called attempts. What differs:

- the place is one whole number, and a number past a known total is refused (MC-001,
  MC-004);
- reaching the total opens the log screen once per attempt, and finishes nothing by
  itself (MC-100, MC-101);
- a series with more to read can continue from its place: Keep Reading, picking up after
  Did Not Finish, and a comic's new issue (MC-200 to MC-202);
- logs keep their own visibility (AC-5).

`row.progress` is always the current place, and every number saved writes a
social.ProgressChange step as a book's does, so activity and stats read these types the
same way as before.
"""

import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from api.services import progress_posts, stats_cache
from api.services.item_metadata import store_series_total
from app import single_weight, user_calendar
from app.book_tracking import BookTrackingConflict, HistoryExists, _mutation
from app.models import (
    Comic,
    DiaryEntry,
    DiaryEntryTag,
    Manga,
    MediaLike,
    MediaTypes,
    SeriesAttempt,
    Sources,
    Status,
    Tag,
)
from app.providers import comicvine
from app.tasks import update_daily_statistics
from social.models import ProgressChange

logger = logging.getLogger(__name__)

UNSET = object()
OPEN_STATUSES = {Status.IN_PROGRESS.value, Status.PAUSED.value}
MODELS = {MediaTypes.MANGA.value: Manga, MediaTypes.COMIC.value: Comic}
UNITS = {MediaTypes.MANGA.value: "chapter", MediaTypes.COMIC.value: "issue"}
MAX_PLACE = 2_147_483_647
# What a place, status or log writes on the tracking row. progressed_at keeps the
# In progress rail's order.
ROW_STATE_FIELDS = [
    "current_attempt",
    "status",
    "progress",
    "progressed_at",
    "start_date",
    "end_date",
    "read_override_active",
]
OVERRIDE_FIELDS = [
    "read_override_active",
    "read_override_previous_tracked",
    "read_override_previous_status",
    "read_override_previous_attempt",
    "read_override_previous_place",
]
UNDATED_FIELDS = [
    "completed_manually",
    "undated_read_previous_tracked",
    "undated_read_previous_status",
    "undated_read_previous_attempt",
    "undated_read_previous_override",
    "undated_read_previous_place",
]
HISTORY_MESSAGE = "Delete reading attempts and logs before removing this series."


class CompletionRequired(BookTrackingConflict):
    """An attempt under way finishes through its log (MC-104)."""

    def __init__(self, journey_id):
        self.journey_id = journey_id
        super().__init__(
            "completion_required",
            "Log this series as finished first.",
            action="finish",
            journey_id=journey_id,
        )


def supports(value):
    """Return whether an item or media-type value is a manga or a comic."""
    return getattr(value, "media_type", value) in MODELS


def total(item):
    """Return the chapters or issues the provider lists, or None (MC-002, MC-003)."""
    return item.series_total or None


def unit(item):
    """Return what a place counts: chapter or issue (MC-001)."""
    return UNITS[item.media_type]


def _plural(count, item):
    return f"{count} {unit(item)}{'' if count == 1 else 's'}"


def _today(user):
    return user_calendar.today(user)


def _user_day(user, value):
    """Return a value's calendar day on the user's calendar; never a future one (SW-006)."""
    if isinstance(value, datetime) and not single_weight.is_calendar_datetime(value):
        value = user_calendar.day_of(value, user)
    return single_weight.calendar_datetime(value, today=_today(user)).date()


def _sent_day(user, value):
    """Return the date a phone sent for a save or a Stop; a day ahead caps at today."""
    if isinstance(value, datetime) and not single_weight.is_calendar_datetime(value):
        value = user_calendar.day_of(value, user)
    return single_weight.sent_day(value, _today(user))


def _carrier(day):
    """Return a calendar day as the UTC midnight the diary stores it at (STAT-100)."""
    return datetime.combine(day, time.min, tzinfo=UTC) if day else None


def _date_string(day):
    return day.isoformat() if day else None


def _storage_rating(value):
    """Return a rating on the stored 0-10 scale, or None.

    Imports and installed builds can send any value on the scale (STAT-109), so this
    checks only the range; the new build's stars are checked where they arrive.
    """
    if value in (None, "", 0, "0", "0.0"):
        return None
    try:
        rating = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError("Enter a valid rating.") from error
    if not rating.is_finite() or rating < 0 or rating > 10:  # noqa: PLR2004
        raise ValidationError("Enter a valid rating.")
    return rating or None


def _whole_number(value):
    """Return a chapter or issue count (MC-001)."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError("Enter a whole number.") from error
    if (
        isinstance(value, bool)
        or not number.is_finite()
        or number < 0
        or number > MAX_PLACE
        or number != number.to_integral_value()
    ):
        raise ValidationError("Enter a whole number.")
    return int(number)


def _locked_row(user, item):
    return (
        MODELS[item.media_type]
        .objects.select_for_update()
        .select_related("item")
        .filter(user=user, item=item)
        .first()
    )


def _ensure_row(user, item, *, history_date=None):
    """Return the one tracking row of this series, and whether it was just created."""
    row = _locked_row(user, item)
    if row is not None:
        return row, False
    row = MODELS[item.media_type](user=user, item=item, status=Status.PLANNING.value, progress=0)
    if history_date is not None:
        row._history_date = history_date  # noqa: SLF001
    try:
        with transaction.atomic():
            row.save(force_insert=True)
    except IntegrityError:
        # A retried first write got here first (BK-501).
        return _locked_row(user, item), False
    row = _locked_row(user, item)
    row.calendar_job_queued = True
    return row, True


def _open_attempt(row):
    return row.attempts.select_for_update().filter(status__in=OPEN_STATUSES).first()


def _attempt_progress(attempt, item):
    return {
        "kind": "count",
        "value": attempt.place,
        "max": total(item),
        "unit": unit(item),
        "journey_id": attempt.pk,
    }


def _set_row_from_attempt(row, attempt):
    row.current_attempt = attempt
    row.status = attempt.status
    row.progress = attempt.place
    row.start_date = _carrier(attempt.start_date)
    row.end_date = _carrier(attempt.end_date)
    row.save(update_fields=ROW_STATE_FIELDS)


def _clear_override(row):
    row.read_override_active = False
    row.read_override_previous_tracked = False
    row.read_override_previous_status = None
    row.read_override_previous_attempt = None
    row.read_override_previous_place = 0


@contextmanager
def _unrecorded(row):
    """Save a restored state with no history row: stats count every rise they read (MC-007)."""
    row.skip_history_when_saving = True
    try:
        yield
    finally:
        del row.skip_history_when_saving


def _forget_attempt_history(row, attempt):
    """An attempt undone counts on no day: stats read these rows (MC-007)."""
    row.history.filter(current_attempt_id=attempt.pk).delete()


def _forget_mark_history(row):
    """A mark undone leaves no row whose number the restored place would rise from."""
    row.history.filter(status=Status.COMPLETED.value, current_attempt_id__isnull=True).filter(
        Q(completed_manually=True) | Q(read_override_active=True),
    ).delete()


def _deactivate_override(row):
    was_active = row.read_override_active
    row.read_override_active = False
    return was_active


def _begin(row, *, created, status=Status.IN_PROGRESS.value, **fields):
    """Create an attempt that remembers the status it replaces (BK-308, BK-309, BK-403)."""
    return row.attempts.create(
        status=status,
        previous_tracked=not created,
        previous_status=None if created else row.status,
        previous_attempt=None if created else row.current_attempt,
        previous_read_override=_deactivate_override(row),
        previous_place=0 if created else row.progress,
        **fields,
    )


def _closing_day(user, attempt, day):
    """BK-306: return an end, which can't predate the attempt's start or latest saved number.

    The phone's own date is never refused: after a flight west today can be before them,
    so the stored date stays no earlier than those.
    """
    if day == _today(user):
        return max(filter(None, (day, attempt.start_date, attempt.progressed_on)))
    if attempt.start_date and day < attempt.start_date:
        raise ValidationError("An end date can't be before the attempt's start date.")
    if attempt.progressed_on and day < attempt.progressed_on:
        raise ValidationError("An end date can't be before the latest progress update.")
    return day


def _queue_statistics(user_id, day):
    transaction.on_commit(
        lambda: update_daily_statistics.delay(user_id=user_id, date_str=day.isoformat()),
    )


def _completed(row):
    return row.attempts.filter(status=Status.COMPLETED.value)


def _latest_completed(row):
    """Return the read the row stands on: its current one, whatever the logs' dates."""
    current = row.current_attempt
    if current and current.status == Status.COMPLETED.value:
        return current
    return _completed(row).order_by(F("end_date").desc(nulls_last=True), "-created_at", "-id").first()


def _has_more(row, latest_completed):
    """MC-200: a Read series has more with no total, or with its place below its total.

    The eye marks the whole series, so a series marked Read with no number has nothing
    more once it has a total (MC-007); its comic moves on with a new issue (MC-202).
    """
    if row.status != Status.COMPLETED.value:
        return False
    series_total = total(row.item)
    if not series_total:
        return True
    place = latest_completed.place if latest_completed else row.progress
    return 0 < place < series_total


def has_more(row):
    """Return whether Keep Reading is on offer for this row (MC-200)."""
    return _has_more(row, _latest_completed(row))


def _read_place(row):
    latest = _latest_completed(row)
    return latest.place if latest else row.progress


# Status


@transaction.atomic
def assign_status(user, item, status, *, start_date=None, mutation_id=None):
    """Apply one direct or live status (BK-100, BK-101, BK-104)."""
    if status not in Status.values:
        raise ValidationError("Invalid status.")
    if status == Status.IN_PROGRESS.value:
        return start(user, item, start_date=start_date, mutation_id=mutation_id)
    if status == Status.COMPLETED.value:
        return mark_read(user, item)
    row, _ = _ensure_row(user, item)
    attempt = _open_attempt(row)
    if status == Status.PLANNING.value and attempt:
        raise ValidationError("Pause or mark this attempt Did Not Finish before moving it to To Read.")
    if status == Status.PAUSED.value and attempt:
        return pause(user, item)
    if status == Status.DROPPED.value and attempt:
        return drop(user, item)
    if row.status == status:
        return row

    _clear_override(row)
    # A stopped series keeps its place, which an imported one holds with no attempt
    # (MC-201). Anywhere else there is nothing to pick up from.
    stopped = {Status.PAUSED.value, Status.DROPPED.value}
    if not (status in stopped and row.status in stopped):
        row.progress = 0
    row.status = status
    row.current_attempt = None
    row.start_date = None
    row.end_date = None
    row.save(update_fields=[*ROW_STATE_FIELDS, *OVERRIDE_FIELDS])
    return row


@transaction.atomic
def start(user, item, *, start_date=None, mutation_id=None):
    """Currently Reading: resume, pick up, keep going, or begin (BK-104, MC-200, MC-201)."""
    row, created = _ensure_row(user, item)
    mutation_id = _mutation(mutation_id)
    if mutation_id and row.attempts.filter(mutation_id=mutation_id).exists():
        return row
    current = _open_attempt(row)
    if current:
        if current.status == Status.PAUSED.value:
            current.status = Status.IN_PROGRESS.value
            current.save(update_fields=["status"])
            _set_row_from_attempt(row, current)
            _record_status(user, item, current, ProgressChange.PICKED_BACK_UP)
        return row
    if row.status == Status.COMPLETED.value:
        if has_more(row):
            return keep_reading(user, item, start_date=start_date, mutation_id=mutation_id)
        return reread(user, item, start_date=start_date, mutation_id=mutation_id)
    # Picking up a stopped series continues from its place (MC-201): the closed attempt
    # stays as it is, and a read that was carrying on goes on carrying on.
    stopped = row.current_attempt if row.status == Status.DROPPED.value else None
    place = row.progress if row.status in {*OPEN_STATUSES, Status.DROPPED.value} else 0
    picks_back_up = _picks_back_up(row)
    attempt = _begin(
        row,
        created=created,
        start_date=_user_day(user, start_date or _today(user)),
        place=place,
        continues=bool(stopped and stopped.continues),
        origin=SeriesAttempt.Origin.LIVE,
        mutation_id=mutation_id,
    )
    _set_row_from_attempt(row, attempt)
    _record_start(user, item, attempt)
    if picks_back_up:
        _record_status(user, item, attempt, ProgressChange.PICKED_BACK_UP)
    return row


resume = start


@transaction.atomic
def keep_reading(user, item, *, start_date=None, mutation_id=None, automatic=False, place=None):
    """Continue a Read series at its old place, or at `place` (MC-200).

    It isn't a reread and records no start. A new comic issue does this by itself
    (`automatic`, MC-202).
    """
    row = _locked_row(user, item)
    mutation_id = _mutation(mutation_id)
    if row is not None and mutation_id and row.attempts.filter(mutation_id=mutation_id).exists():
        return row
    if row is None or row.status != Status.COMPLETED.value or _open_attempt(row):
        raise ValidationError("Only a finished series can keep going.")
    if not automatic and not has_more(row):
        raise ValidationError("There's nothing more to read yet.")
    attempt = _begin(
        row,
        created=False,
        start_date=_user_day(user, start_date or _today(user)),
        place=_read_place(row) if place is None else place,
        continues=True,
        origin=SeriesAttempt.Origin.LIVE,
        mutation_id=mutation_id,
    )
    _set_row_from_attempt(row, attempt)
    return row


@transaction.atomic
def reread(user, item, *, start_date=None, mutation_id=None):
    """Start again at zero: a new attempt that counts again (MC-200)."""
    row, created = _ensure_row(user, item)
    mutation_id = _mutation(mutation_id)
    if mutation_id and row.attempts.filter(mutation_id=mutation_id).exists():
        return row
    if _open_attempt(row):
        return row
    attempt = _begin(
        row,
        created=created,
        start_date=_user_day(user, start_date or _today(user)),
        origin=SeriesAttempt.Origin.LIVE,
        mutation_id=mutation_id,
    )
    _set_row_from_attempt(row, attempt)
    _record_start(user, item, attempt)
    return row


@transaction.atomic
def pause(user, item):
    """Pause keeps the place (BK-303)."""
    row = _locked_row(user, item)
    if row is None:
        raise ValidationError("This series is not being read.")
    attempt = _open_attempt(row)
    if attempt is None:
        if row.status == Status.PAUSED.value:
            return row
        raise ValidationError("Only a current attempt can be paused.")
    if attempt.status == Status.IN_PROGRESS.value:
        attempt.status = Status.PAUSED.value
        attempt.save(update_fields=["status"])
        _record_status(user, item, attempt, ProgressChange.PAUSED)
    _set_row_from_attempt(row, attempt)
    return row


@transaction.atomic
def drop(user, item, *, end_date=None):
    """Did Not Finish closes the attempt and keeps its place (BK-301)."""
    row = _locked_row(user, item)
    attempt = _open_attempt(row) if row else None
    if attempt is None:
        return assign_status(user, item, Status.DROPPED.value)
    attempt.end_date = _closing_day(user, attempt, _sent_day(user, end_date or _today(user)))
    attempt.status = Status.DROPPED.value
    attempt.save(update_fields=["status", "end_date"])
    _set_row_from_attempt(row, attempt)
    _record_status(user, item, attempt, ProgressChange.STOPPED)
    return row


@transaction.atomic
def restart(user, item, *, start_date=None, end_date=None, mutation_id=None):
    """Keep the attempt as Did Not Finish and begin again at zero (BK-304, MC-201)."""
    row = _locked_row(user, item)
    if row is None:
        raise ValidationError("This series has no attempt to restart.")
    mutation_id = _mutation(mutation_id)
    if mutation_id and row.attempts.filter(mutation_id=mutation_id).exists():
        return row
    old = _open_attempt(row)
    if old is None:
        raise ValidationError("Only a current or paused attempt can be restarted.")
    old.end_date = _closing_day(user, old, _sent_day(user, end_date or _today(user)))
    old.status = Status.DROPPED.value
    old.save(update_fields=["status", "end_date"])
    new = row.attempts.create(
        status=Status.IN_PROGRESS.value,
        start_date=_sent_day(user, start_date or _today(user)),
        origin=SeriesAttempt.Origin.LIVE,
        previous_tracked=True,
        previous_status=Status.DROPPED.value,
        previous_attempt=old,
        previous_place=old.place,
        mutation_id=mutation_id,
    )
    _set_row_from_attempt(row, new)
    _record_start(user, item, new)
    return row


# The number


def _reread_comic_total(item, place):
    """Ask Comic Vine once before refusing: a new issue can be out already (MC-004)."""
    if (
        item.media_type != MediaTypes.COMIC.value
        or item.source != Sources.COMICVINE.value
        or not total(item)
        or place <= total(item)
    ):
        return
    try:
        store_series_total(item, {"max_progress": comicvine.fresh_issue_count(item.media_id)})
    except Exception:
        # Whatever went wrong, the number is judged against the count Spine has.
        logger.warning("Could not re-read Comic Vine's issue count for %s", item, exc_info=True)


def update_progress(user, item, *, value, progressed_on=None, mutation_id=None):
    """Save the current attempt's place: a number, never a finish (MC-100, MC-101)."""
    place = _whole_number(value)
    _reread_comic_total(item, place)
    return _save_place(user, item, place, progressed_on, mutation_id=mutation_id)


def _refuse_past_total(place, item):
    series_total = total(item)
    if not series_total or place <= series_total:
        return
    so_far = " so far" if item.media_type == MediaTypes.COMIC.value else ""
    raise ValidationError(f"This {item.media_type} has {_plural(series_total, item)}{so_far}.")


@transaction.atomic
def _save_place(user, item, place, progressed_on, *, mutation_id=None):
    row = _locked_row(user, item)
    if row is None:
        row = start(user, item)
    attempt = _open_attempt(row)
    if attempt is None and row.status == Status.IN_PROGRESS.value:
        # A row from before attempts: its number becomes the attempt's place.
        row = start(user, item)
        attempt = _open_attempt(row)
    if attempt is None or attempt.status != Status.IN_PROGRESS.value:
        raise ValidationError("Progress can only be updated while Currently Reading.")
    _refuse_past_total(place, row.item)
    mutation_id = _mutation(mutation_id)
    if mutation_id and mutation_id == attempt.log_offer_mutation_id and place == attempt.place:
        # A retry after a lost answer: that save's log screen still opens (MC-100).
        row.opens_log = True
        return row
    day = _sent_day(user, progressed_on or _today(user))
    if day == _today(user):
        # Never refused: after a flight west today can be before the start or last save.
        day = max(filter(None, (day, attempt.start_date, attempt.progressed_on)))
    elif attempt.start_date and day < attempt.start_date:
        raise ValidationError("Progress updates can't be before the attempt's start date.")
    previous = _attempt_progress(attempt, row.item)
    attempt.place = place
    attempt.progressed_on = day
    # MC-100, MC-101: reaching the total opens the log screen, once per attempt.
    reached = total(row.item) and place == total(row.item)
    opens_log = bool(reached and not attempt.log_offered)
    if opens_log:
        attempt.log_offered = True
        attempt.log_offer_mutation_id = mutation_id
    attempt.save(update_fields=["place", "progressed_on", "log_offered", "log_offer_mutation_id"])
    _set_row_from_attempt(row, attempt)
    current = _attempt_progress(attempt, row.item)
    if previous != current:
        _record_progress_change(user, item, previous, current)
    row.opens_log = opens_log
    return row


# The eye


@transaction.atomic
def mark_read(user, item):
    """The eye: one undated Read, or a status change over older history (BK-102, BK-103)."""
    row, created = _ensure_row(user, item)
    attempt = _open_attempt(row)
    if attempt:
        raise CompletionRequired(attempt.pk)
    if row.status == Status.COMPLETED.value:
        return row
    if row.completed_manually or _completed(row).exists():
        row.read_override_active = True
        row.read_override_previous_tracked = not created
        row.read_override_previous_status = row.status
        row.read_override_previous_attempt = row.current_attempt
        row.read_override_previous_place = row.progress
    else:
        row.completed_manually = True
        row.undated_read_previous_tracked = not created
        row.undated_read_previous_status = None if created else row.status
        row.undated_read_previous_attempt = None if created else row.current_attempt
        row.undated_read_previous_override = _deactivate_override(row)
        row.undated_read_previous_place = row.progress
    row.status = Status.COMPLETED.value
    row.current_attempt = None
    row.progress = 0
    row.start_date = None
    row.end_date = None
    row.save(update_fields=[*UNDATED_FIELDS, *OVERRIDE_FIELDS, *ROW_STATE_FIELDS])
    return row


def _restore_marker_state(row):
    if not row.undated_read_previous_tracked:
        return None
    attempt = row.undated_read_previous_attempt
    row.status = row.undated_read_previous_status or Status.PLANNING.value
    row.current_attempt = attempt
    row.progress = row.undated_read_previous_place
    row.start_date = _carrier(attempt.start_date) if attempt else None
    row.end_date = _carrier(attempt.end_date) if attempt else None
    row.read_override_active = row.undated_read_previous_override
    return row


@transaction.atomic
def undo_read(user, item):
    """The filled eye undoes only a mark, never a log (BK-102, BK-103)."""
    row = _locked_row(user, item)
    if row is None:
        return None
    current = row.current_attempt
    if current and current.status == Status.COMPLETED.value:
        raise HistoryExists("Delete the log before undoing Read.")
    if row.read_override_active:
        previous_tracked = row.read_override_previous_tracked
        attempt = row.read_override_previous_attempt
        place = row.read_override_previous_place
        _forget_mark_history(row)
        row.status = row.read_override_previous_status or Status.PLANNING.value
        row.current_attempt = attempt
        _clear_override(row)
        row.progress = place
        row.start_date = _carrier(attempt.start_date) if attempt else None
        row.end_date = _carrier(attempt.end_date) if attempt else None
        with _unrecorded(row):
            row.save(update_fields=[*ROW_STATE_FIELDS, *OVERRIDE_FIELDS])
        if not previous_tracked and not _other_history(row):
            MediaLike.objects.filter(user=user, item=item).delete()
            row.delete()
            return None
        return row
    if not row.completed_manually:
        raise HistoryExists("Delete the log before undoing Read.")
    return delete_undated_read(user, item, clear_current_state=True)


@transaction.atomic
def delete_undated_read(user, item, *, clear_current_state=True):
    """Delete the undated Read (BK-310)."""
    row = _locked_row(user, item)
    if row is None or not row.completed_manually:
        return row
    marker_is_current = (
        row.status == Status.COMPLETED.value
        and row.current_attempt_id is None
        and not row.read_override_active
    )
    if marker_is_current:
        _forget_mark_history(row)
    restored = _restore_marker_state(row) if marker_is_current else row
    row.completed_manually = False
    row.undated_read_previous_tracked = False
    row.undated_read_previous_status = None
    row.undated_read_previous_attempt = None
    row.undated_read_previous_override = False
    row.undated_read_previous_place = 0
    if marker_is_current and clear_current_state:
        row.score = None
        row.rating_source = None
        row.like_source = None
        row.like_is_independent = False
        MediaLike.objects.filter(user=user, item=item).delete()
    if restored is None:
        if not _other_history(row):
            MediaLike.objects.filter(user=user, item=item).delete()
            row.delete()
            return None
        row.status = Status.PLANNING.value
        row.progress = 0
        row.start_date = None
        row.end_date = None
    fields = list(UNDATED_FIELDS)
    if marker_is_current:
        fields.extend(ROW_STATE_FIELDS)
    if clear_current_state:
        fields.extend(["score", "rating_source", "like_source", "like_is_independent"])
    if marker_is_current:
        with _unrecorded(row):
            row.save(update_fields=list(dict.fromkeys(fields)))
    else:
        row.save(update_fields=list(dict.fromkeys(fields)))
    return row


# Stars and the heart


@transaction.atomic
def set_rating(user, item, rating):
    """Stars finish a series, so an attempt under way goes through its log (MC-104)."""
    rating = _storage_rating(rating)
    row = _locked_row(user, item)
    if rating is not None:
        attempt = _open_attempt(row) if row else None
        if attempt:
            raise CompletionRequired(attempt.pk)
        row = mark_read(user, item)
    if row is None:
        return None
    row.score = rating
    row.rating_source = None
    row.save(update_fields=["score", "rating_source"])
    single_weight._set_rating_activity(user, row, rating)  # noqa: SLF001
    return row


@transaction.atomic
def set_like(user, item, liked):
    """A heart finishes a series too (MC-104); unhearting never changes its status."""
    row = _locked_row(user, item)
    if liked:
        attempt = _open_attempt(row) if row else None
        if attempt:
            raise CompletionRequired(attempt.pk)
        row = mark_read(user, item)
    if row is None:
        MediaLike.objects.filter(user=user, item=item).delete()
        return None
    row.like_source = None
    row.like_is_independent = True
    row.save(update_fields=["like_source", "like_is_independent"])
    changed = _set_like_row(user, item, bool(liked))
    if changed:
        single_weight._audit_like(user, item, bool(liked))  # noqa: SLF001
    return row


def _set_like_row(user, item, liked):
    if liked:
        _, created = MediaLike.objects.get_or_create(user=user, item=item)
        return created
    deleted, _ = MediaLike.objects.filter(user=user, item=item).delete()
    return bool(deleted)


# Logs


def _add_tags(entry, names):
    for name in dict.fromkeys(str(value).strip().lower() for value in names or []):
        if name:
            tag, _ = Tag.objects.get_or_create(name=name)
            DiaryEntryTag.objects.get_or_create(diary_entry=entry, tag=tag)


def _activity_snapshot(entry):
    return {
        "rating": str(entry.rating) if entry.rating is not None else None,
        "liked": bool(entry.liked),
    }


def _create_activity(entry):
    from social.models import Activity

    Activity.objects.create(
        actor=entry.user,
        verb="diary_created",
        target_type="diary",
        target_id=entry.id,
        item=entry.item,
        visibility=entry.visibility,
        snapshot=_activity_snapshot(entry),
    )


def _has_prior_completion(row, day, *, exclude):
    """BK-400: a read is a reread when an earlier read or undated Read exists."""
    if row.completed_manually:
        return True
    return (
        _completed(row)
        .filter(Q(end_date__isnull=True) | Q(end_date__lte=day))
        .exclude(pk=exclude.pk)
        .exists()
    )


def _log_moment(day, user):
    """Return when a log's day counts: noon on the user's calendar, or now for today."""
    if day >= _today(user):
        return None
    return datetime.combine(day, time(12), tzinfo=user_calendar.calendar_tz(user))


def _dated_rise(row, day, user):
    """Date a log's rise in place on its own day (MC-007), keeping history in order.

    Stats read each rise against the history row before it. Later rows that all hold 0
    say nothing stats read, so they move to just before the log; a number saved later
    stays first, and the rise follows it.
    """
    moment = _log_moment(day, user)
    if moment is None or row is None:
        return moment
    later = row.history.filter(history_date__gte=moment)
    latest = later.order_by("-history_date").values_list("history_date", flat=True).first()
    if latest is None:
        return moment
    if later.filter(progress__gt=0).exists():
        return latest
    later.update(history_date=moment - timedelta(seconds=1))
    return moment


def _move_log_rise(row, attempt, day, user):
    """Move a log's count to its new date (MC-007), in order with the rows around it."""
    own = row.history.filter(current_attempt_id=attempt.pk, status=Status.COMPLETED.value)
    first = own.order_by("history_date", "history_id").first()
    if first is None:
        return
    rise = own.filter(progress__gt=0).order_by("history_date", "history_id").first() or first
    others = row.history.exclude(history_id__in=own.values("history_id"))
    moment = _log_moment(day, user) or timezone.now()
    before_first = Q(history_date__lt=first.history_date) | Q(
        history_date=first.history_date,
        history_id__lt=first.history_id,
    )
    between = others.filter(before_first, history_date__gte=moment)
    ahead = others.exclude(before_first).filter(history_date__lte=moment)
    if between.filter(progress__gt=0).exists():
        moment = between.order_by("-history_date").first().history_date
    else:
        between.update(history_date=moment - timedelta(seconds=1))
    next_row = ahead.order_by("history_date").first()
    if next_row is not None:
        moment = next_row.history_date - timedelta(seconds=1)
    own.filter(Q(history_date__lt=moment) | Q(history_id__lte=rise.history_id)).update(history_date=moment)


def _write_log(user, item, attempt, day, **fields):
    """Create a log and link it to its finished attempt (BK-400)."""
    tags = fields.pop("tags", None)
    entry = DiaryEntry.objects.create(
        user=user,
        item=item,
        consumed_at=_carrier(day),
        progress_snapshot=_attempt_progress(attempt, item),
        **fields,
    )
    _add_tags(entry, tags)
    attempt.completion_diary_entry = entry
    return entry


@transaction.atomic
def complete(
    user,
    item,
    *,
    completion_date,
    journey_id=None,
    place=UNSET,
    rating=None,
    liked=False,
    is_rewatch=None,
    review="",
    review_title="",
    contains_spoilers=False,
    tags=None,
    visibility="public",
    mutation_id=None,
    import_source="",
    import_source_id="",
    import_source_order=None,
    emit_activity=True,
    update_current=True,
):
    """Save one log and the finished attempt together (BK-400, BK-401, MC-103).

    With a total the place becomes the total; with none it is the number given, else
    the place so far (MC-102).
    """
    rating = _storage_rating(rating)
    day = _user_day(user, completion_date)
    mutation_id = _mutation(mutation_id)
    if import_source and import_source_id:
        existing = DiaryEntry.objects.filter(
            user=user,
            import_source=import_source,
            import_source_id=import_source_id,
        ).first()
        if existing:
            if existing.item_id != item.pk:
                raise ValidationError("This imported record already belongs to another series.")
            return _locked_row(user, item), existing
    row, created = _ensure_row(user, item, history_date=_dated_rise(None, day, user))
    if mutation_id:
        prior = row.attempts.filter(completion_mutation_id=mutation_id).first()
        if prior and prior.completion_diary_entry_id:
            return row, prior.completion_diary_entry

    if journey_id:
        named = row.attempts.select_for_update().filter(pk=journey_id).first()
        if named is None:
            raise ValidationError("Attempt not found.")
        if named.status == Status.COMPLETED.value:
            if named.completion_diary_entry_id:
                return row, named.completion_diary_entry
            raise ValidationError("This attempt is already finished.")
    # What is under way is what a log finishes, whichever closed attempt a stale screen
    # named (BK-300).
    attempt = _open_attempt(row)
    is_direct = attempt is None

    fallback_place = row.progress
    if attempt:
        day = _closing_day(user, attempt, day)
        attempt.pre_completion_status = attempt.status
        attempt.pre_completion = {
            "place": attempt.place,
            "progressed_on": _date_string(attempt.progressed_on),
            "row_score": str(row.score) if row.score is not None else None,
            "rating_source_id": row.rating_source_id,
            "title_liked": MediaLike.objects.filter(user=user, item=item).exists(),
            "like_source_id": row.like_source_id,
            "like_is_independent": row.like_is_independent,
        }
        fallback_place = attempt.place
    else:
        attempt = _begin(
            row,
            created=created,
            status=Status.COMPLETED.value,
            end_date=day,
            origin=SeriesAttempt.Origin.DIRECT_LOG,
            place=row.progress,
        )

    series_total = total(row.item)
    if series_total:
        attempt.place = series_total
    elif place not in (UNSET, None):
        attempt.place = _whole_number(place)
    else:
        attempt.place = fallback_place
    attempt.status = Status.COMPLETED.value
    attempt.end_date = day
    attempt.completion_mutation_id = mutation_id
    derived_reread = not attempt.continues and _has_prior_completion(row, day, exclude=attempt)
    entry = _write_log(
        user,
        row.item,
        attempt,
        day,
        rating=rating,
        review=review,
        review_title=review_title,
        liked=bool(liked),
        is_rewatch=derived_reread if is_rewatch is None else bool(is_rewatch),
        contains_spoilers=contains_spoilers,
        visibility=visibility,
        import_source=import_source,
        import_source_id=import_source_id,
        import_source_order=import_source_order,
        tags=tags,
    )
    attempt.save(
        update_fields=[
            "status",
            "end_date",
            "place",
            "pre_completion_status",
            "pre_completion",
            "completion_diary_entry",
            "completion_mutation_id",
        ],
    )
    if update_current:
        history_date = _dated_rise(row, day, user)
        if history_date is not None:
            row._history_date = history_date  # noqa: SLF001
        row.current_attempt = attempt
        if is_direct and derived_reread and row.progress:
            # A reread counts again (MC-007), and stats count only rises.
            row.progress = 0
            row.save(update_fields=["current_attempt", "progress"])
        row.status = Status.COMPLETED.value
        row.progress = attempt.place
        row.start_date = _carrier(attempt.start_date)
        row.end_date = _carrier(day)
        if rating is not None:
            row.score = rating
            row.rating_source = entry
        row.like_source = entry
        row.like_is_independent = False
        row.save(update_fields=[*ROW_STATE_FIELDS, "score", "rating_source", "like_source", "like_is_independent"])
        _set_like_row(user, item, bool(liked))
    if emit_activity:
        _create_activity(entry)
    _queue_statistics(user.id, day)
    return row, entry


@transaction.atomic
def update_completion(entry, data, *, tags=UNSET):
    """Edit a log, its attempt's date and what it is the source of (BK-403, SW-108)."""
    row = _locked_row(entry.user, entry.item)
    attempt = row.attempts.select_for_update().filter(completion_diary_entry=entry).first() if row else None
    rating_is_source = bool(row and row.rating_source_id == entry.id)
    like_is_source = bool(row and row.like_source_id == entry.id)
    previous_consumed_at = entry.consumed_at
    previous_rating = entry.rating
    fields = []
    if "consumed_at" in data:
        day = _user_day(entry.user, data["consumed_at"])
        if attempt:
            day = _closing_day(entry.user, attempt, day)
            attempt.end_date = day
            attempt.save(update_fields=["end_date"])
            if entry.consumed_at != _carrier(day):
                _move_log_rise(row, attempt, day, entry.user)
        entry.consumed_at = _carrier(day)
        fields.append("consumed_at")
    if "rating" in data:
        entry.rating = _storage_rating(data["rating"])
        fields.append("rating")
    for field in ("review", "review_title", "liked", "is_rewatch", "contains_spoilers", "visibility"):
        if field in data:
            setattr(entry, field, data[field])
            fields.append(field)
    if fields:
        entry.save(update_fields=[*dict.fromkeys(fields), "updated_at"])
    if tags is not UNSET:
        entry.tags.clear()
        _add_tags(entry, tags)
    if row:
        row_fields = []
        if attempt and row.current_attempt_id == attempt.pk and "consumed_at" in data:
            row.end_date = entry.consumed_at
            row_fields.append("end_date")
        if "rating" in data:
            if rating_is_source:
                row.score = entry.rating
                row_fields.append("score")
                if entry.rating is None:
                    row.rating_source = None
                    row_fields.append("rating_source")
            elif single_weight.fill_empty_current_rating(row, entry, previous_rating):
                row_fields.extend(["score", "rating_source"])
        if row_fields:
            row.save(update_fields=row_fields)
        if like_is_source and "liked" in data:
            _set_like_row(entry.user, entry.item, entry.liked)
    from social.models import Activity

    Activity.objects.filter(actor=entry.user, target_type="diary", target_id=entry.id).update(
        visibility=entry.visibility,
        snapshot=_activity_snapshot(entry),
    )
    single_weight._audit_diary(entry, "diary_updated")  # noqa: SLF001
    _queue_statistics(entry.user_id, single_weight.calendar_date(previous_consumed_at))
    if entry.consumed_at != previous_consumed_at:
        _queue_statistics(entry.user_id, single_weight.calendar_date(entry.consumed_at))
    return entry


def _is_real_history(attempt):
    """An imported read with no log is no reason to keep a series, as a book's isn't (BK-002)."""
    return (
        attempt.origin != SeriesAttempt.Origin.IMPORTED
        or attempt.completion_diary_entry_id is not None
        or attempt.status != Status.COMPLETED.value
    )


def _has_logs(row):
    """Return whether the row's owner has logs of it, from a list page's primed count."""
    primed = getattr(row.item, "viewer_diary_count", None)
    if primed is not None:
        return primed > 0
    return DiaryEntry.objects.filter(user_id=row.user_id, item_id=row.item_id).exists()


def _other_history(row, *, excluding=None):
    attempts = row.attempts.all()
    if excluding is not None:
        attempts = attempts.exclude(pk=excluding.pk)
    return (
        row.completed_manually
        or attempts.exists()
        or DiaryEntry.objects.filter(user_id=row.user_id, item_id=row.item_id).exists()
    )


def _restore_previous(row, attempt):
    """Put back the status an attempt replaced; None when the row goes with it (BK-404)."""
    previous = attempt.previous_attempt
    if attempt.previous_tracked:
        row.status = attempt.previous_status or Status.PLANNING.value
        row.current_attempt = previous
        row.progress = attempt.previous_place
        row.start_date = _carrier(previous.start_date) if previous else None
        row.end_date = _carrier(previous.end_date) if previous else None
        row.read_override_active = attempt.previous_read_override
        with _unrecorded(row):
            row.save(update_fields=ROW_STATE_FIELDS)
        return row
    if _other_history(row, excluding=attempt):
        row.current_attempt = None
        row.status = Status.COMPLETED.value if row.completed_manually else Status.PLANNING.value
        row.progress = 0
        row.start_date = None
        row.end_date = None
        with _unrecorded(row):
            row.save(update_fields=ROW_STATE_FIELDS)
        return row
    MediaLike.objects.filter(user_id=row.user_id, item_id=row.item_id).delete()
    row.delete()
    return None


def _splice(row, attempt):
    """Close the gap an attempt leaves in the chain of what each one replaced."""
    row.attempts.filter(previous_attempt=attempt).update(
        previous_attempt=attempt.previous_attempt,
        previous_status=attempt.previous_status,
        previous_tracked=attempt.previous_tracked,
        previous_read_override=attempt.previous_read_override,
        previous_place=attempt.previous_place,
    )
    stats_cache.bump_user(row.user_id)
    fields = []
    if row.undated_read_previous_attempt_id == attempt.pk:
        row.undated_read_previous_attempt = attempt.previous_attempt
        row.undated_read_previous_status = attempt.previous_status
        row.undated_read_previous_tracked = attempt.previous_tracked
        row.undated_read_previous_override = attempt.previous_read_override
        row.undated_read_previous_place = attempt.previous_place
        fields.extend(UNDATED_FIELDS)
    if row.read_override_previous_attempt_id == attempt.pk:
        row.read_override_previous_attempt = attempt.previous_attempt
        row.read_override_previous_status = attempt.previous_status
        row.read_override_previous_place = attempt.previous_place
        fields.extend(OVERRIDE_FIELDS)
    if fields:
        row.save(update_fields=fields)


def _delete_progress_history(user, item, journey_id):
    progress_posts.delete_changes(
        user.pk,
        ProgressChange.objects.filter(actor=user, item=item, current_progress__journey_id=journey_id),
    )


@transaction.atomic
def delete_completion(user, entry):
    """Deleting a log restores the attempt when it is still current (BK-403, BK-404)."""
    if entry.user_id != user.id or not supports(entry.item):
        raise ValidationError("You can only delete your own logs.")
    item = entry.item
    day = single_weight.calendar_date(entry.consumed_at)
    row = _locked_row(user, item)
    if row is None:
        entry.delete()
        _queue_statistics(user.id, day)
        return None
    attempt = row.attempts.select_for_update().filter(completion_diary_entry=entry).first()
    rating_is_source = row.rating_source_id == entry.id
    like_is_source = row.like_source_id == entry.id
    from social.models import Activity

    Activity.objects.filter(actor=user, target_type="diary", target_id=entry.id).delete()
    single_weight._audit_diary(entry, "diary_deleted")  # noqa: SLF001
    entry.delete()
    _queue_statistics(user.id, day)
    if attempt is None:
        fields = _detach_sources(row, rating_is_source=rating_is_source, like_is_source=like_is_source)
        if fields:
            row.save(update_fields=fields)
        return row
    if row.current_attempt_id == attempt.pk and attempt.pre_completion_status:
        # The attempt's own saves stand; only what the log added is undone (MC-007).
        row.history.filter(current_attempt_id=attempt.pk, status=Status.COMPLETED.value).delete()
        snapshot = attempt.pre_completion or {}
        attempt.status = attempt.pre_completion_status
        attempt.place = snapshot.get("place", attempt.place)
        progressed_on = snapshot.get("progressed_on")
        attempt.progressed_on = date.fromisoformat(progressed_on) if progressed_on else None
        attempt.end_date = None
        attempt.completion_diary_entry = None
        attempt.completion_mutation_id = None
        attempt.pre_completion_status = None
        attempt.pre_completion = None
        attempt.save()
        score = snapshot.get("row_score")
        row.score = Decimal(score) if score is not None else None
        row.rating_source_id = _still_there(snapshot.get("rating_source_id"))
        row.like_source_id = _still_there(snapshot.get("like_source_id"))
        row.like_is_independent = bool(snapshot.get("like_is_independent"))
        with _unrecorded(row):
            _set_row_from_attempt(row, attempt)
            row.save(update_fields=["score", "rating_source", "like_source", "like_is_independent"])
        _set_like_row(user, item, bool(snapshot.get("title_liked")))
        return row
    _forget_attempt_history(row, attempt)
    if row.current_attempt_id == attempt.pk:
        restored = _restore_previous(row, attempt)
    else:
        restored = row
    if restored:
        _splice(restored, attempt)
    _delete_progress_history(user, item, attempt.pk)
    attempt.delete()
    if restored is None:
        return None
    fields = _detach_sources(restored, rating_is_source=rating_is_source, like_is_source=like_is_source)
    if fields:
        restored.save(update_fields=fields)
    return restored


def _still_there(entry_id):
    """Return a remembered source log's id, unless that log has since been deleted."""
    return entry_id if entry_id and DiaryEntry.objects.filter(pk=entry_id).exists() else None


def _detach_sources(row, *, rating_is_source, like_is_source):
    """A deleted log keeps the current values, no longer tied to it (SW-104)."""
    fields = []
    if rating_is_source:
        row.rating_source = None
        fields.append("rating_source")
    if like_is_source:
        row.like_source = None
        row.like_is_independent = True
        fields.extend(["like_source", "like_is_independent"])
    return fields


# Attempts


@transaction.atomic
def update_journey(user, item, journey_id, *, start_date=UNSET, end_date=UNSET):
    """Edit an attempt's dates, keeping them in order (BK-306)."""
    row = _locked_row(user, item)
    attempt = row.attempts.select_for_update().filter(pk=journey_id).first() if row else None
    if attempt is None:
        raise ValidationError("Attempt not found.")
    if attempt.completion_diary_entry_id and end_date is not UNSET:
        raise ValidationError("Edit a finished attempt's date through its log.")
    if attempt.status in OPEN_STATUSES:
        if start_date is None and attempt.start_date:
            raise ValidationError("An attempt under way keeps its start date.")
        if end_date is not UNSET:
            raise ValidationError("An attempt under way has no end date yet.")
    elif end_date is None and attempt.end_date:
        raise ValidationError("A closed attempt keeps its end date.")
    fields = []
    if start_date is not UNSET:
        day = _user_day(user, start_date) if start_date is not None else None
        if day and attempt.progressed_on and day > attempt.progressed_on:
            raise ValidationError("A start date can't follow the latest progress update.")
        if day and attempt.end_date and day > attempt.end_date:
            raise ValidationError("A start date can't follow the end date.")
        attempt.start_date = day
        fields.append("start_date")
        if day:
            # A hand edit of a start is the person saying when it began, so it's first
            # activity too (decision 10), where a log's own move isn't.
            ProgressChange.objects.filter(
                actor=user,
                item=item,
                current_progress__journey_id=attempt.pk,
                current_progress__kind=ProgressChange.START,
            ).update(saved_on=day)
    if end_date is not UNSET:
        day = _user_day(user, end_date) if end_date is not None else None
        if day:
            day = _closing_day(user, attempt, day)
        attempt.end_date = day
        fields.append("end_date")
    if fields:
        attempt.save(update_fields=fields)
    if row.current_attempt_id == attempt.pk:
        _set_row_from_attempt(row, attempt)
    return row


@transaction.atomic
def delete_journey(user, item, journey_id):
    """Delete one attempt with its saved numbers (BK-308, BK-309)."""
    row = _locked_row(user, item)
    if row is None:
        return None
    attempt = row.attempts.select_for_update().filter(pk=journey_id).first()
    if attempt is None:
        return row
    if attempt.completion_diary_entry_id:
        raise HistoryExists("Delete the log instead of the finished attempt.")
    _forget_attempt_history(row, attempt)
    restored = _restore_previous(row, attempt) if row.current_attempt_id == attempt.pk else row
    if restored:
        _splice(restored, attempt)
    _delete_progress_history(user, item, attempt.pk)
    attempt.delete()
    return restored


@transaction.atomic
def remove_tracking(user, item):
    """Remove a series with nothing real behind it (BK-002)."""
    row = _locked_row(user, item)
    if row is None:
        return None
    if any(_is_real_history(attempt) for attempt in row.attempts.all()) or _has_logs(row):
        raise HistoryExists(HISTORY_MESSAGE)
    MediaLike.objects.filter(user=user, item=item).delete()
    row.delete()
    progress_posts.forget_titles(user.pk, [item])
    return None


# Writes from installed builds and the web


def apply_tracking_state(
    user,
    item,
    *,
    status=UNSET,
    rating=UNSET,
    start_date=UNSET,
    notes=UNSET,
    progress=UNSET,
    mutation_id=None,
):
    """Apply a tracking write, status and number together, under these same rules (MC-500)."""
    place = UNSET
    if progress is not UNSET:
        place = _whole_number(progress)
        _reread_comic_total(item, place)
    with transaction.atomic():
        row = _locked_row(user, item)
        if status is not UNSET:
            row = assign_status(
                user,
                item,
                status,
                start_date=None if start_date is UNSET else start_date,
                mutation_id=mutation_id,
            )
        if rating is not UNSET:
            row = set_rating(user, item, rating)
        if place is not UNSET and status in (UNSET, Status.IN_PROGRESS.value):
            row = _save_place(user, item, place, None)
        if row is None and notes is not UNSET:
            row, _ = _ensure_row(user, item)
        if row is not None and notes is not UNSET and row.notes != notes:
            row.notes = notes
            row.save(update_fields=["notes"])
    return row


# Imports


def _import_day(user, value):
    """Return an imported date's calendar day; a future one isn't a real read's date."""
    if value is None:
        return None
    if single_weight.is_calendar_datetime(value):
        day = value.date()
    elif isinstance(value, datetime):
        # A source's date arrives as midnight on the server's zone: that is the date,
        # on any reader's calendar (SW-006). A real moment is read on theirs.
        server_time = timezone.localtime(value) if timezone.is_aware(value) else value
        day = server_time.date() if server_time.time() == time.min else user_calendar.day_of(value, user)
    else:
        day = value
    return day if day <= _today(user) else None


def _ordered(start_day, end_day):
    return start_day if start_day and end_day and start_day <= end_day else None if end_day else start_day


@transaction.atomic
def import_title(
    user,
    item,
    *,
    status,
    place=0,
    score=None,
    start_date=None,
    end_date=None,
    notes="",
    earlier=(),
    history_date=None,
    import_source="",
    import_source_id="",
    tags=None,
):
    """Import one series as one tracking record (MC-400).

    `earlier` holds the source's earlier reads in order, each a dict of status, place,
    score, start_date and end_date. A finished read with a date lands on that date as a
    log; another read on that same day, or one with no date, is an undated finished
    attempt. Reading becomes an attempt at its place with no start. To Read, Paused and
    Dropped stay the user's choice, and no imported number is activity (SW-500).
    """
    model = MODELS[item.media_type]
    if model.objects.filter(user=user, item=item).exists():
        return None
    series_total = total(item)
    if status == Status.COMPLETED.value and not place and series_total:
        place = series_total
    rating = _storage_rating(score)
    start_day = _import_day(user, start_date)
    end_day = _import_day(user, end_date)
    # Logs an overwrite kept: hand-made ones, with no read of their own any more.
    kept_logs = list(DiaryEntry.objects.filter(user=user, item=item).order_by("consumed_at", "id"))
    log_days = {single_weight.calendar_date(entry.consumed_at) for entry in kept_logs}

    # A source can repeat one finish date for all its reads of a title: the latest read
    # with that date holds it.
    def claim(day):
        if day is None or day in log_days:
            return None
        log_days.add(day)
        return day

    current_log_day = claim(end_day) if status == Status.COMPLETED.value else None
    reads = []
    for read in reversed(earlier):
        day = _import_day(user, read.get("end_date"))
        reads.append((read, claim(day) if read["status"] == Status.COMPLETED.value else day))
    reads.reverse()

    row = model(
        user=user,
        item=item,
        status=status,
        progress=0 if status == Status.PLANNING.value else place,
        score=rating,
        notes=notes or "",
        start_date=_carrier(start_day if status in OPEN_STATUSES else _ordered(start_day, end_day)),
        end_date=_carrier(end_day) if status in {Status.COMPLETED.value, Status.DROPPED.value} else None,
        # A kept log is already this read: an undated one on top would count it twice.
        completed_manually=status == Status.COMPLETED.value and current_log_day is None and not kept_logs,
    )
    if status == Status.PLANNING.value:
        row.start_date = None
    if history_date is not None:
        row._history_date = history_date  # noqa: SLF001
    row.save(force_insert=True)

    previous = None
    finished_before = False

    def add_attempt(**fields):
        nonlocal previous
        previous = row.attempts.create(
            previous_tracked=previous is not None,
            previous_status=previous.status if previous is not None else None,
            previous_attempt=previous,
            previous_place=previous.place if previous is not None else 0,
            **fields,
        )
        return previous

    def add_kept_log(entry):
        day = single_weight.calendar_date(entry.consumed_at)
        return add_attempt(
            status=Status.COMPLETED.value,
            origin=SeriesAttempt.Origin.DIRECT_LOG,
            end_date=day,
            place=series_total or (entry.progress_snapshot or {}).get("value") or place or 0,
            completion_diary_entry=entry,
        )

    def add_logged_read(read_place, read_start, day, read_rating, *, source_id=""):
        attempt = add_attempt(
            status=Status.COMPLETED.value,
            origin=SeriesAttempt.Origin.DIRECT_LOG,
            start_date=_ordered(read_start, day),
            end_date=day,
            place=read_place,
        )
        entry = _write_log(
            user,
            row.item,
            attempt,
            day,
            rating=read_rating,
            is_rewatch=finished_before or row.completed_manually,
            visibility="public",
            import_source=import_source,
            import_source_id=source_id or f"{item.media_type}:{item.media_id}:{day.isoformat()}",
            tags=tags,
        )
        attempt.save(update_fields=["completion_diary_entry"])
        return attempt, entry

    latest_kept = None

    def add_kept_logs(until=None):
        """Link kept logs in date order among the reads: a deletion restores what came before."""
        nonlocal latest_kept
        while kept_logs and (until is None or single_weight.calendar_date(kept_logs[0].consumed_at) <= until):
            latest_kept = add_kept_log(kept_logs.pop(0))

    for read, day in reads:
        if day:
            add_kept_logs(day)
        read_start = _import_day(user, read.get("start_date"))
        if read["status"] == Status.COMPLETED.value:
            read_place = read.get("place") or series_total or 0
            if day:
                add_logged_read(read_place, read_start, day, _storage_rating(read.get("score")))
            else:
                add_attempt(status=Status.COMPLETED.value, origin=SeriesAttempt.Origin.IMPORTED, place=read_place)
            finished_before = True
        elif read["status"] == Status.DROPPED.value:
            add_attempt(
                status=Status.DROPPED.value,
                origin=SeriesAttempt.Origin.IMPORTED,
                start_date=_ordered(read_start, day),
                end_date=day,
                place=read.get("place") or 0,
            )

    add_kept_logs()

    pointers = {}
    if current_log_day:
        attempt, entry = add_logged_read(place, start_day, current_log_day, rating, source_id=import_source_id)
        pointers = {
            "current_attempt": attempt,
            "like_source": entry,
            "rating_source": entry if rating is not None else None,
        }
    elif status == Status.COMPLETED.value and latest_kept:
        pointers = {"current_attempt": latest_kept, "progress": latest_kept.place}
    elif status == Status.IN_PROGRESS.value:
        pointers = {
            "current_attempt": add_attempt(
                status=Status.IN_PROGRESS.value,
                origin=SeriesAttempt.Origin.LIVE,
                start_date=start_day,
                place=place,
            ),
        }
    if pointers:
        # No second history row: the number arrived with the row (STAT-201).
        model.objects.filter(pk=row.pk).update(**pointers)
        for field, value in pointers.items():
            setattr(row, field, value)
    return row


def clear_for_import(user, media_type, source, media_ids):
    """Overwrite replaces these series and their imported logs; hand-made logs stay."""
    rows = MODELS[media_type].objects.filter(user=user, item__source=source, item__media_id__in=media_ids)
    items = [row.item for row in rows.select_related("item")]
    rows.delete()
    DiaryEntry.objects.filter(user=user, item__in=items).exclude(import_source="").delete()
    progress_posts.forget_titles(user.pk, items)


# A comic's new issue


def _carry_mark_place(row, place):
    """Give a marked Read's history the place it carries on from, so no rise shows (MC-007).

    Stats count each history row's rise against the row before it. The rows after the
    last number saved move up together; a number saved before the mark leaves only the
    difference, on the mark's day.
    """
    moved = []
    for history_id, progress in row.history.order_by("-history_date", "-history_id").values_list(
        "history_id",
        "progress",
    ):
        if progress:
            break
        moved.append(history_id)
    row.history.filter(history_id__in=moved).update(progress=place)


def new_issue_out(item, *, start_date=None, previous_total=None):
    """MC-202: a comic on the Read shelf continues by itself, adding no activity.

    A comic already read to the total stays: its own log can be what ran this job. The
    attempt starts on the issue's day, so a log since then is fine (BK-306). A comic
    marked with the eye had every issue so far, so only what comes next counts (MC-007).
    """
    series_total = total(item)
    rows = Comic.objects.filter(item=item, status=Status.COMPLETED.value).select_related("user")
    for row in rows:
        read_place = _read_place(row)
        if series_total and read_place >= series_total:
            continue
        place = read_place
        if series_total and not read_place:
            place = previous_total if previous_total and previous_total < series_total else series_total - 1
        try:
            with transaction.atomic():
                if place != read_place:
                    _carry_mark_place(row, place)
                keep_reading(
                    row.user,
                    item,
                    start_date=min(start_date, _today(row.user)) if start_date else None,
                    automatic=True,
                    place=place,
                )
        except (ValidationError, BookTrackingConflict, IntegrityError):
            logger.warning("Could not move %s back to Currently Reading for %s", item, row.user, exc_info=True)


# Activity


def _record_progress_change(user, item, previous, current):
    from social.models import Activity

    change = ProgressChange.objects.create(
        actor=user,
        item=item,
        previous_progress=previous,
        current_progress=current,
        saved_on=_today(user),
    )
    Activity.objects.create(
        actor=user,
        verb="progress_updated",
        target_type="progress_change",
        target_id=change.id,
        item=item,
        snapshot={"previous": previous, "current": current},
    )


def _record_start(user, item, attempt):
    """Record a new attempt; deleting the attempt drops it with its numbers."""
    ProgressChange.record_start(user, item, journey_id=attempt.pk)


def _picks_back_up(row):
    """Return whether a new attempt carries on from a read that was stopped.

    Paused or Did Not Finish on a series never started has nothing to pick back up. Nor
    has one finished, whose next attempt is a new read.
    """
    if row.status not in {Status.PAUSED.value, Status.DROPPED.value}:
        return False
    last = row.attempts.order_by("-id").first()
    return last.status == Status.DROPPED.value if last else bool(row.progress)


def _record_status(user, item, attempt, move):
    """Record a status move the person chose.

    Keep Reading and a new issue record none (MC-200, MC-202). Nor do imports, marks,
    restores or Restart (AC-1).
    """
    ProgressChange.record_status(user, item, move, journey_id=attempt.pk)


# What the app reads


def _is_reread(row, attempt, completed):
    """BK-400, derived from the row's other reads; a read that carries on never is (MC-200)."""
    if attempt.continues:
        return False
    if row.completed_manually:
        return True
    if not attempt.end_date:
        return any(other.pk != attempt.pk for other in completed)

    def order(read):
        return (read.end_date or date.min, read.created_at, read.pk)

    return any(other.pk != attempt.pk and order(other) < order(attempt) for other in completed)


def _journey_payload(row, attempt, completed):
    return {
        "id": attempt.pk,
        "status": attempt.status,
        "origin": attempt.origin,
        "start_date": _date_string(attempt.start_date),
        "end_date": _date_string(attempt.end_date),
        "progress": _attempt_progress(attempt, row.item),
        "completion_diary_entry_id": attempt.completion_diary_entry_id,
        "is_reread": _is_reread(row, attempt, completed),
        "continues": attempt.continues,
    }


def state_payload(row):
    """Serialize the additive `series` object: the `book` object's shape, plus totals."""
    attempts = sorted(row.attempts.all(), key=lambda attempt: (attempt.created_at, attempt.pk))
    current = next((attempt for attempt in attempts if attempt.pk == row.current_attempt_id), None)
    completed = [attempt for attempt in attempts if attempt.status == Status.COMPLETED.value]
    logged = [attempt for attempt in completed if attempt.completion_diary_entry_id]
    history = [
        attempt
        for attempt in attempts
        if attempt.status == Status.DROPPED.value or (attempt in completed and attempt not in logged)
    ]
    history.sort(key=lambda attempt: (attempt.end_date or date.min, attempt.created_at, attempt.pk), reverse=True)
    latest = (
        current
        if current and current.status == Status.COMPLETED.value
        else max(
            completed,
            key=lambda attempt: (attempt.end_date or date.min, attempt.created_at, attempt.pk),
            default=None,
        )
    )
    can_remove = not any(_is_real_history(attempt) for attempt in attempts) and not _has_logs(row)
    open_attempt = current if current and current.status in OPEN_STATUSES else None
    series_total = total(row.item)

    if current:
        source = "journey"
    elif row.read_override_active and row.status == Status.COMPLETED.value:
        source = "history_override"
    elif row.completed_manually and row.status == Status.COMPLETED.value:
        source = "undated_read"
    else:
        source = "direct_status"

    actions = ["currently_reading", "planning", "paused", "drop", "log", "mark_read"]
    reasons = {}
    if open_attempt:
        actions.extend(["finish", "restart", "delete_journey"])
        if series_total != 1:
            # A series with a total of one has no number (MC-005).
            actions.append("update_progress")
        actions.append("pause" if open_attempt.status == Status.IN_PROGRESS.value else "resume")
        reasons["planning"] = "Pause or mark this attempt Did Not Finish first."
        reasons["mark_read"] = "Finish this attempt through its log."
    more = _has_more(row, latest)
    if row.status == Status.COMPLETED.value and not open_attempt:
        actions.append("reread")
        if more:
            actions.append("keep_reading")
    if row.read_override_active or (
        row.completed_manually and row.status == Status.COMPLETED.value and current is None
    ):
        actions.append("undo_read")
    elif current and current.status == Status.COMPLETED.value:
        reasons["undo_read"] = "Delete the log to undo this read."
    if row.completed_manually:
        actions.append("delete_undated_read")
    if can_remove:
        actions.append("remove_tracking")
    else:
        reasons["remove_tracking"] = HISTORY_MESSAGE

    return {
        "current_journey": _journey_payload(row, current, completed) if current else None,
        "reading_history": [_journey_payload(row, attempt, completed) for attempt in history],
        "undated_read": (
            {"id": "undated", "status": Status.COMPLETED.value, "date": None}
            if row.completed_manually
            else None
        ),
        "status_source": source,
        "completion_dates": sorted(_date_string(attempt.end_date) for attempt in logged),
        "completed_journey_count": len(completed),
        # A read that carried on is the same read (MC-200).
        "lifetime_read_count": len([attempt for attempt in completed if not attempt.continues])
        + int(row.completed_manually),
        "is_rereading": bool(
            open_attempt and not open_attempt.continues and (row.completed_manually or completed),
        ),
        "completion_required": bool(open_attempt and series_total and open_attempt.place >= series_total),
        "can_remove_tracking": can_remove,
        "remove_tracking_reason": reasons.get("remove_tracking"),
        "available_actions": list(dict.fromkeys(actions)),
        "action_reasons": reasons,
        "total": series_total,
        "unit": unit(row.item),
        "offers_keep_reading": more,
        # This save reached the total for the first time in this attempt (MC-100, MC-101).
        "opens_log": getattr(row, "opens_log", False),
    }
