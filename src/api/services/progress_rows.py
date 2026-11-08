"""Fixing a save and taking one back (docs/activity-contract.md, AC-9).

A book, manga, comic or game keeps one change row per save, so a progress row can be
fixed or deleted from its own page. TV and anime rows have none: unticking fixes TV
(decision 52).

- **Delete** is for the newest row on a title only. It removes that day's saves and puts
  the place back to where it was before them. The day's start and status moves stay:
  they are facts about the read. A row with a start the same day stays as "Started
  reading".
- **Fix** is for an older row. Saves are read in the order they were made and the fold
  uses each save's own number, so fixing 120 to 110 between 100 and 200 reads 100, 10,
  90 and the place stays 200: the save is rewritten in place, and the next save's
  "from" with it. The number stays between the saves either side (decision 58).
- **Edit on the newest row** has no route of its own: the app saves the number again
  with the row's date. A drop comes off the newest amounts first, which is that row.

Both are for a read that is still going (reading or paused). A finished read's pages
belong to its log, and a stopped read's rows are fixed by reading on.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from rest_framework.exceptions import ValidationError

from api.exceptions import ProgressRowConflict
from api.services import activity_rows, progress_posts, stats_cache
from app import book_tracking, game_tracking, series_tracking, user_calendar
from app.models import (
    Book,
    BookSession,
    Comic,
    Game,
    GameSession,
    Manga,
    MediaTypes,
    Status,
)
from social.models import ProgressChange, SocialAuditLog

OPEN = (Status.IN_PROGRESS.value, Status.PAUSED.value)
TOUCH = ProgressChange.TOUCH
# The kinds of change that are a number, by type.
COUNTS = {
    MediaTypes.BOOK.value: ("pages", "percentage"),
    MediaTypes.MANGA.value: ("count",),
    MediaTypes.COMIC.value: ("count",),
    MediaTypes.GAME.value: ("minutes", "percentage"),
}
# What a change's journey is called in its snapshot.
CONTAINER_KEY = {
    MediaTypes.BOOK.value: "journey_id",
    MediaTypes.MANGA.value: "journey_id",
    MediaTypes.COMIC.value: "journey_id",
    MediaTypes.GAME.value: "playthrough_id",
}
WHOLE = ("pages", "count", "minutes")
SERIES = {MediaTypes.MANGA.value: Manga, MediaTypes.COMIC.value: Comic}
MESSAGES = {
    "unavailable": "This row can't be edited or deleted.",
    "newest": "The newest row is fixed by saving its number again with its date.",
    "out_of_range": "The number has to stay between the saves either side of this one.",
}


class ProgressRowUnavailableError(ValidationError):
    """The row has no saves to fix or delete: TV, anime, or a read that ended."""

    default_detail = MESSAGES["unavailable"]


def supports(item):
    """Whether this type's saves are change rows a row can be fixed from."""
    return item.media_type in COUNTS


def _kind(change):
    return change.current_progress.get("kind")


def _number(snapshot):
    """Return a snapshot's number, or None."""
    return activity_rows.saved_number(snapshot.get("value"))


def _plain(value):
    """Return a Decimal as a JSON number: 65, or 42.5."""
    return int(value) if value == value.to_integral_value() else float(value)


@dataclass
class Plan:
    """One person's saves on a title's open read, and what a day of them can be."""

    owner: object
    item: object
    tracking: object
    container: object
    changes: list
    day: date
    zone: object
    on_day: list = field(default_factory=list)
    kind: str = ""
    last: object = None
    track: list = field(default_factory=list)

    @property
    def counted(self):
        """The day's saves that are a number: a retype of the same number is not."""
        return [change for change in self.on_day if _kind(change) != TOUCH]

    @property
    def deletable(self):
        """Whether the day's saves are the newest the read has: no number saved since.

        A retype of the same number is not a row, so one made later doesn't count.
        """
        numbers = [change for change in self.changes if _kind(change) != TOUCH]
        return numbers[len(numbers) - len(self.counted) :] == self.counted

    @property
    def touches_after(self):
        """The retypes of a same number made after the day's last save."""
        newest = max(change.pk for change in self.on_day)
        return [change for change in self.changes if _kind(change) == TOUCH and change.pk > newest]

    @property
    def title_key(self):
        return progress_posts._title_key(self.item)

    def edit(self):
        """Describe how this row can be fixed, or None.

        `mode` is "save" on the newest row (save the number again with `date`, from the
        normal sheet) and "number" on an older one (PATCH). A "number" is held between
        `min` and `max`, the saves either side.
        """
        last = self.last
        if _kind(last) != self.kind or last.previous_progress.get("kind") != self.kind:
            return None
        base = {
            "value": _plain(_number(last.current_progress)),
            "unit": last.current_progress.get("unit"),
            "kind": self.kind,
            "date": self.day.isoformat(),
        }
        later = [change for change in self.track if change.pk > last.pk]
        if not later:
            return {**base, "mode": "save", "min": None, "max": self._limit()}
        following = later[0]
        if _kind(following) != self.kind or following.previous_progress.get("kind") != self.kind:
            return None
        # The next save made must also be the next day in date order, or the fix would
        # move pages onto a row the owner never edited.
        later_days = [
            day for day in (activity_rows.saved_day(change, self.zone) for change in self.track) if day > self.day
        ]
        if not later_days or activity_rows.saved_day(following, self.zone) != min(later_days):
            return None
        same = [change for change in self.counted if _kind(change) == self.kind]
        positions = [self.track.index(change) for change in same]
        contiguous = positions == list(range(positions[0], positions[0] + len(positions)))
        first = same[0] if contiguous and same[0].previous_progress.get("kind") == self.kind else last
        return {
            **base,
            "mode": "number",
            "min": _plain(_number(first.previous_progress)),
            "max": _plain(_number(following.current_progress)),
        }

    def following(self):
        """Return the next save in the number's track: its "from" moves with it."""
        return next(change for change in self.track if change.pk > self.last.pk)

    def _limit(self):
        """Return the most the newest row's number can be, if the title has a length."""
        if self.kind == "percentage":
            return 100
        if self.kind == "pages":
            return self.item.total_pages or None
        if self.kind == "count":
            return series_tracking.total(self.item)
        return None


def _tracking(owner, item, *, lock):
    if item.media_type == MediaTypes.BOOK.value:
        model = Book
    elif item.media_type == MediaTypes.GAME.value:
        model = Game
    else:
        model = SERIES[item.media_type]
    rows = model.objects.select_for_update() if lock else model.objects
    return rows.select_related("item").filter(user=owner, item=item).first()


def _open_container(item, tracking):
    if item.media_type == MediaTypes.BOOK.value:
        rows = BookSession.objects.filter(related_book=tracking)
    elif item.media_type == MediaTypes.GAME.value:
        rows = GameSession.objects.filter(related_game=tracking)
    else:
        rows = tracking.attempts.all()
    return rows.filter(status__in=OPEN).order_by("-id").first()


def inspect(owner, item, day, *, unit=None, lock=False):
    """Return the Plan for a person's day on a title, or None if it has no saves to fix.

    `unit` picks which number a game's day edits when it saved both.
    """
    if not supports(item):
        return None
    tracking = _tracking(owner, item, lock=lock)
    container = _open_container(item, tracking) if tracking else None
    if container is None:
        return None
    key = f"current_progress__{CONTAINER_KEY[item.media_type]}"
    kinds = (*COUNTS[item.media_type], TOUCH)
    changes = [
        change
        for change in ProgressChange.objects.filter(actor=owner, item=item, **{key: container.pk}).order_by("id")
        # A log's own change belongs to the log's row (GM-219).
        if _kind(change) in kinds and "log_id" not in change.current_progress
    ]
    zone = user_calendar.calendar_tz(owner)
    on_day = [change for change in changes if activity_rows.saved_day(change, zone) == day]
    plan = Plan(owner, item, tracking, container, changes, day, zone, on_day)
    counted = plan.counted
    if not counted:
        return None
    if unit is not None:
        counted = [change for change in counted if change.current_progress.get("unit") == unit]
        if not counted:
            return None
        plan.kind = _kind(counted[-1])
    elif item.media_type == MediaTypes.GAME.value:
        # Time played tells a day that moved both, as the row does.
        plan.kind = "minutes" if any(_kind(change) == "minutes" for change in counted) else "percentage"
    else:
        plan.kind = _kind(counted[-1])
    plan.last = [change for change in counted if _kind(change) == plan.kind][-1]
    if item.media_type == MediaTypes.GAME.value:
        plan.track = [change for change in changes if _kind(change) == plan.kind]
    else:
        plan.track = [change for change in changes if _kind(change) != TOUCH]
    return plan


def viewer_fields(viewer, owner, item, day):
    """Return what a post page tells the person who may fix or delete it, else {}.

    The owner can fix or delete; an admin can delete anyone's. The app holds no rule of
    its own: it shows what these say.
    """
    is_owner = viewer.pk == owner.pk
    if not (is_owner or viewer.is_staff):
        return {}
    plan = inspect(owner, item, day)
    edit = plan.edit() if plan and is_owner else None
    return {
        "can_edit": edit is not None,
        "can_delete": bool(plan and plan.deletable),
        "edit": edit,
    }


# Delete


@transaction.atomic
def delete(owner, item, day, *, by):
    """Take back the newest row: its saves go and the place goes back (decision 52).

    Raises ProgressRowUnavailableError for a row with nothing to take back, and a 409
    when it isn't the newest any more, because another device saved since the page
    loaded.
    """
    plan = inspect(owner, item, day, lock=True)
    if plan is None:
        raise ProgressRowUnavailableError
    if not plan.deletable:
        raise ProgressRowConflict
    _restore_place(plan)
    ids = [change.pk for change in plan.on_day]
    gone = ids + [change.pk for change in plan.touches_after]
    progress_posts.delete_changes(owner.pk, ProgressChange.objects.filter(pk__in=gone))
    if by.pk != owner.pk:
        SocialAuditLog.objects.create(
            actor=by,
            action="progress_delete",
            target_user=owner,
            target_type="progress_day",
            target_id=ids[0],
            metadata={"title_key": plan.title_key, "day": day.isoformat(), "item_id": item.pk},
        )
    stats_cache.bump_user(owner)


def _earliest(changes, kind):
    return next((change for change in changes if _kind(change) == kind), None)


def _latest_day(plan, remaining, kind=None):
    """Return the newest day among the saves left, or None."""
    days = [
        activity_rows.saved_day(change, plan.zone)
        for change in remaining
        if kind is None or _kind(change) == kind
    ]
    return max(days, default=None)


def _restore_place(plan):
    """Put the read back to where it stood before the day's saves."""
    remaining = [change for change in plan.changes if change not in plan.on_day and _kind(change) != TOUCH]
    first = plan.counted[0]
    item = plan.item
    if item.media_type == MediaTypes.BOOK.value:
        _restore_book(plan, first, remaining)
    elif item.media_type == MediaTypes.GAME.value:
        _restore_game(plan, remaining)
    else:
        _restore_series(plan, first, remaining)


def _restore_book(plan, first, remaining):
    session, book = plan.container, plan.tracking
    previous = first.previous_progress
    value = _number(previous) or Decimal(0)
    total = plan.item.total_pages
    if not remaining and value == 0:
        session.pages_read = None
        session.percentage_read = None
    elif previous.get("kind") == "pages":
        session.pages_read = int(value)
        session.percentage_read = Decimal(session.pages_read * 100) / Decimal(total) if total else None
    else:
        session.percentage_read = value
        session.pages_read = round(float(value / 100 * total)) if total else None
    session.progressed_on = _latest_day(plan, remaining)
    session.save(update_fields=["pages_read", "percentage_read", "progressed_on"])
    book_tracking._set_book_from_session(book, session)


def _restore_series(plan, first, remaining):
    attempt, row = plan.container, plan.tracking
    attempt.place = int(_number(first.previous_progress) or 0)
    attempt.progressed_on = _latest_day(plan, remaining)
    total = series_tracking.total(plan.item)
    if attempt.log_offered and attempt.place != total:
        # Reaching the total again opens the log screen again (MC-101).
        attempt.log_offered = False
        attempt.log_offer_mutation_id = None
    attempt.save(update_fields=["place", "progressed_on", "log_offered", "log_offer_mutation_id"])
    with series_tracking._unrecorded(row):
        series_tracking._set_row_from_attempt(row, attempt)


def _restore_game(plan, remaining):
    session, game = plan.container, plan.tracking
    for kind in ("minutes", "percentage"):
        first = _earliest(plan.counted, kind)
        if first is None:
            continue
        left = [change for change in remaining if _kind(change) == kind]
        value = _number(first.previous_progress) or Decimal(0)
        # Nothing saved is unknown, not 0 (GM-100).
        restored = None if not left and value == 0 else value
        if kind == "minutes":
            session.total_minutes = None if restored is None else int(restored)
            session.minutes_updated_on = _latest_day(plan, left)
        else:
            session.set_completion(restored)
            session.percentage_updated_on = _latest_day(plan, left)
    days = [activity_rows.saved_day(change, plan.zone) for change in remaining]
    session.first_progress_on = min(days, default=None)
    session.save()
    game_tracking._sync_state(game)


# Fix


@transaction.atomic
def fix(owner, item, day, value, *, unit=None):
    """Rewrite an older row's last save of the day, and the next one's "from"."""
    plan = inspect(owner, item, day, unit=unit, lock=True)
    edit = plan.edit() if plan else None
    if edit is None:
        raise ProgressRowUnavailableError
    if edit["mode"] == "save":
        raise ValidationError(MESSAGES["newest"])
    number = _parse(value, plan.kind)
    if not Decimal(str(edit["min"])) <= number <= Decimal(str(edit["max"])):
        message = f"{MESSAGES['out_of_range']} It can be {edit['min']} to {edit['max']}."
        raise ValidationError(message)
    following = plan.following()
    written = _plain(number)
    plan.last.current_progress = {**plan.last.current_progress, "value": written}
    plan.last.save(update_fields=["current_progress"])
    following.previous_progress = {**following.previous_progress, "value": written}
    following.save(update_fields=["previous_progress"])
    # A fix to the number either side empties a day: its likes and comments go with it.
    days = {day, activity_rows.saved_day(following, plan.zone)}
    progress_posts.drop_empty_posts(owner.pk, plan.title_key, days)
    stats_cache.bump_user(owner)


def _parse(value, kind):
    """Return a sent number in the units of the save, or raise a 400."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValidationError({"value": "Enter a number."}) from error
    if not number.is_finite() or number < 0:
        raise ValidationError({"value": "Enter a number that isn't negative."})
    if kind in WHOLE and number != number.to_integral_value():
        raise ValidationError({"value": "Enter a whole number."})
    if kind == "percentage" and (number > 100 or number != number.quantize(Decimal("0.01"))):
        raise ValidationError({"value": "Enter a percent from 0 to 100, with up to two decimals."})
    return number
