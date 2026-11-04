"""One calculation: what Spine saved, turned into activity rows.

docs/activity-contract.md. Each save stores only its fixed day
(ProgressChange.saved_on). The amount is worked out here when read, so fixing a typo
fixes the old row and a list always adds up to its number. Saves are read in the order
they were saved: a rise adds to that save's day, a drop comes off the newest amounts
first, and a log adds what's left on its own date.

A row is one line on a list: one per person, title and day, with each log its own row.
A pile is what a person has with no date, one total per type: only All Time adds it, and
it never makes a row. Marking as watched, read, played or listened lands in the pile and
nowhere else.

Readers use a type's rows only once it's in CALCULATED_TYPES, so a type switches in one
commit.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from app import user_calendar
from app.models import Book, BookSession, DiaryEntry, MediaTypes, Status
from social.models import ProgressChange

# The types Stats reads through this calculation. Empty: every reader uses its old
# counter.
CALCULATED_TYPES = frozenset()

PROGRESS = "progress"
LOG = "log"
ZERO = Decimal(0)
PAGE = "page"
PERCENT = "percent"
EPISODE = "episode"
# What a list can be asked to count (the `counts` parameter).
TITLES = "titles"
DAYS = "days"
WEEKS = "weeks"
MULTI_TITLE_DAYS = "multi_title_days"
EPISODES = "episodes"
HOURS = "hours"
STOPPING_MOVES = (ProgressChange.PAUSED, ProgressChange.STOPPED)


@dataclass
class Row:
    """One line on a list: a person's day on a title, or one log."""

    kind: str
    actor_id: int
    day: date
    title_key: str
    title_id: int
    any_item_id: int
    bucket: str
    amount: Decimal = ZERO
    told: Decimal = ZERO
    told_unit: str = ""
    known: bool = True
    started: bool = False
    moves: list = field(default_factory=list)
    progress_id: int | None = None
    last_at: datetime | None = None
    log_id: int | None = None

    @property
    def counts_title(self):
        """Return whether this row counts its title; a start alone doesn't."""
        return bool(self.amount or self.told or self.log_id)

    @property
    def is_event(self):
        """Return whether this day is an active day (STAT-106); a status move isn't."""
        return self.counts_title or self.started

    @property
    def sort_key(self):
        """Sort newest day first, then most recently written, as a list is read."""
        return (self.day, self.last_at, 1 if self.log_id else 0, self.log_id or self.progress_id or 0)


@dataclass
class Pile:
    """What one person has with no date, in one type."""

    actor_id: int | None
    bucket: str | None
    amount: Decimal = ZERO
    told: Decimal = ZERO
    titles: set = field(default_factory=set)
    known: set = field(default_factory=set)


@dataclass
class Calculation:
    """What rows_for returns. `moved` is viewing id to day, for TV."""

    rows: list = field(default_factory=list)
    piles: list = field(default_factory=list)
    moved: dict = field(default_factory=dict)


@dataclass
class Amount:
    """Part of a position: no day is the pile, and a log id shows on that log's row."""

    day: date | None
    value: Decimal
    at: datetime | None
    log_id: int | None = None


@dataclass
class Position:
    """A running number and the amounts that built it, which add up (decision 5)."""

    value: Decimal = ZERO
    amounts: list = field(default_factory=list)

    def move_to(self, value, day, at):
        """Add a rise on this day, or take a drop off the newest amounts first."""
        change = value - self.value
        if change > 0:
            self.amounts.append(Amount(day, change, at))
        elif change < 0:
            taking = -change
            for amount in reversed(self.amounts):
                taken = min(amount.value, taking)
                amount.value -= taken
                taking -= taken
        self.value = value


@dataclass
class Note:
    """What a read did on one day, apart from any amount."""

    at: datetime
    started: bool = False
    moves: list = field(default_factory=list)
    change_ids: list = field(default_factory=list)
    status_ids: list = field(default_factory=list)


@dataclass
class Reading:
    """One read of a title: its amounts, and percents told with no known length."""

    counted: Position = field(default_factory=Position)
    percents: Position = field(default_factory=Position)
    first_day: date | None = None
    notes: dict = field(default_factory=dict)

    def touch(self, day, change, *, started=False, move=None):
        """Note a save on its day. Any save, a start included, can be first activity."""
        self.first_day = day if self.first_day is None else min(self.first_day, day)
        note = self.notes.setdefault(day, Note(at=change.created_at))
        note.at = max(note.at, change.created_at)
        note.started = note.started or started
        if move:
            note.moves.append(move)
            note.status_ids.append(change.pk)
        else:
            note.change_ids.append(change.pk)

    def finish(self, log_id, day, at, target):
        """Add what's left on a log's date (decision 10).

        Dated before the read's first activity, everything the read dated moves there
        too, and the days it was read show nothing.
        """
        if self.first_day is not None and day < self.first_day:
            self.notes.clear()
            for amount in (*self.counted.amounts, *self.percents.amounts):
                if amount.day is not None:
                    amount.log_id = log_id
        if target is not None and target > self.counted.value:
            self.counted.amounts.append(Amount(day, target - self.counted.value, at, log_id))
            self.counted.value = target

    @property
    def dated(self):
        """Return what this read has on a day, leaving out any opening with no date."""
        return sum((amount.value for amount in self.counted.amounts if amount.day is not None), ZERO)


class Title:
    """One person's rows on one title: one a day, each log beside them, and the pile."""

    def __init__(self, actor_id, item, bucket, unit):
        """Start with nothing; `unit` is what its amounts are told in."""
        self.actor_id = actor_id
        self.item_id = item.pk
        self.title_key = f"item:{item.pk}"
        self.bucket = bucket
        self.unit = unit
        self.days = {}
        self.logs = {}
        self.pile = ZERO
        self.in_pile = False
        self.pile_known = True

    def _row(self, kind, day):
        return Row(kind, self.actor_id, day, self.title_key, self.item_id, self.item_id, self.bucket)

    def _day(self, day):
        if day not in self.days:
            self.days[day] = self._row(PROGRESS, day)
        return self.days[day]

    def add_log(self, entry, day, *, known=True):
        """Give a log its row, before any read's amounts land on it."""
        row = self.logs[entry.pk] = self._row(LOG, day)
        row.last_at = entry.created_at
        row.log_id = entry.pk
        row.known = known

    def add_to_pile(self, amount, *, known=True):
        """Add to what this person has of this title with no date."""
        self.pile += amount
        self.in_pile = True
        self.pile_known = self.pile_known and known

    def take(self, reading):
        """Fold a walked read into rows."""
        for amount in reading.counted.amounts:
            self._take_amount(amount, self.unit)
        for amount in reading.percents.amounts:
            self._take_amount(amount, PERCENT)
        for day, note in reading.notes.items():
            row = self._day(day)
            row.started = row.started or note.started
            row.moves.extend(note.moves)
            row.last_at = max(filter(None, (row.last_at, note.at)))
            row.progress_id = min(filter(None, (row.progress_id, *(note.change_ids or note.status_ids))))

    def _take_amount(self, amount, unit):
        if not amount.value:
            return
        if amount.log_id:
            row = self.logs[amount.log_id]
        elif amount.day is None:
            self.add_to_pile(amount.value)
            return
        else:
            row = self._day(amount.day)
            row.last_at = max(filter(None, (row.last_at, amount.at)), default=None)
        if unit == PERCENT:
            row.known = False
            # A row tells one unit (AC-2), and pages win over a percent.
            if row.told_unit not in ("", PERCENT):
                return
        else:
            if row.told_unit == PERCENT:
                row.told = ZERO
            row.amount += amount.value
        row.told += amount.value
        row.told_unit = unit

    def rows(self):
        """Return the rows with something to say, and every log."""
        shown = []
        for row in self.days.values():
            row.moves = net_moves(row.moves)
            if row.told or row.started or row.moves:
                shown.append(row)
        return [*shown, *self.logs.values()]


def net_moves(moves):
    """Cancel a stop or pause and a pick-up on one day, either way (decision 21)."""
    netted = []
    for move in moves:
        opposite = netted and (
            (move == ProgressChange.PICKED_BACK_UP and netted[-1] in STOPPING_MOVES)
            or (move in STOPPING_MOVES and netted[-1] == ProgressChange.PICKED_BACK_UP)
        )
        if opposite:
            netted.pop()
        else:
            netted.append(move)
    return netted


def log_day(consumed_at, zone):
    """Return a log's day: a date-only log's UTC date, else on the owner's calendar.

    As log_is_date_carrier does in SQL (api.services.filters), microseconds don't
    matter.
    """
    moment = consumed_at.astimezone(UTC)
    if (moment.hour, moment.minute, moment.second) == (0, 0, 0):
        return moment.date()
    return consumed_at.astimezone(zone).date()


def saved_day(change, zone):
    """Return a save's fixed day. A rollback's empty row reads its moment instead."""
    return change.saved_on or change.created_at.astimezone(zone).date()


# Books


def saved_number(value):
    """Return a snapshot's number, or None for a null or anything that isn't one."""
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _pages_of(snapshot, length):
    """Return a saved place in pages.

    None with no number, or for a percent of an unknown length.
    """
    value = saved_number(snapshot.get("value"))
    if value is None:
        return None
    if snapshot.get("kind") == "pages":
        return value
    if snapshot.get("kind") == "percentage" and length:
        return Decimal(round(value / 100 * length))
    return None


def _book_target(snapshot, total):
    """Return (the pages a finished read comes to, whether that's its real length).

    A snapshot with a max holds the length at the finish, and it stays (STAT-201). With
    no max it's only the last page typed, so the real length wins once it's known. As in
    stats_time._book_pages, a value of null or 0, or one that's no number, says nothing.
    """
    pages = saved_number(snapshot.get("value")) if snapshot.get("kind") == "pages" else None
    if pages and saved_number(snapshot.get("max")):
        return pages, True
    if total:
        return Decimal(total), True
    if pages:
        return pages, False
    return None, False


def _opening(session, saves, length, *, finished):
    """Return a place a journey holds with no day: from before its saves began."""
    for change in saves:
        if change.current_progress.get("kind") in ("pages", "percentage"):
            return _pages_of(change.previous_progress, length)
    if finished or session.pages_read is None:
        return None
    return Decimal(session.pages_read)


def _walk_journey(reading, session, saves, length, zone, *, continues, finished):
    opening = _opening(session, saves, length, finished=finished)
    if opening and opening > reading.counted.value:
        reading.counted.move_to(opening, None, None)
    for change in saves:
        saved = change.current_progress
        kind = saved.get("kind")
        day = saved_day(change, zone)
        if kind == ProgressChange.START:
            # A start on a read carrying on isn't a start (decision 8).
            reading.touch(day, change, started=not continues)
        elif kind == ProgressChange.STATUS:
            reading.touch(day, change, move=saved["move"])
        elif kind in ("pages", "percentage"):
            value = saved_number(saved.get("value"))
            if value is None:
                # As the feed's SQL reads it: a save with no number moved nothing.
                continue
            reading.touch(day, change)
            pages = _pages_of(saved, length)
            if pages is None:
                reading.percents.move_to(value, day, change.created_at)
            else:
                reading.counted.move_to(pages, day, change.created_at)


def _carries_on(prior, session, saves):
    """Return whether a journey continues the last; only Restart is new (decision 8)."""
    return bool(
        prior
        and prior.status == Status.DROPPED.value
        and session.previous_status != Status.COMPLETED.value
        and not any(change.current_progress.get("began_over") for change in saves),
    )


def _walk_journeys(title, sessions, saves, total, zone):
    """Fold a title's journeys into rows; return each journey's read."""
    by_session = {}
    reading = None
    prior = None
    for session in sessions:
        session_saves = saves.get(session.pk, [])
        continues = _carries_on(prior, session, session_saves)
        if not continues or reading is None:
            if reading:
                title.take(reading)
            reading = Reading()
        by_session[session.pk] = reading
        entry = session.completion_diary_entry if session.status == Status.COMPLETED.value else None
        snapshot = (entry.progress_snapshot or {}) if entry else {}
        # A finished read's percent saves use the length on its log (STAT-201).
        length = (saved_number(snapshot.get("max")) if snapshot.get("kind") == "pages" else None) or total
        _walk_journey(reading, session, session_saves, length, zone, continues=continues, finished=bool(entry))
        if entry:
            day = log_day(entry.consumed_at, zone)
            target, known = _book_target(snapshot, total)
            title.add_log(entry, day, known=known)
            reading.finish(entry.pk, day, entry.created_at, target)
            title.take(reading)
            reading = None
        prior = session
    if reading:
        title.take(reading)
    return by_session


def _read_before_mark(book, sessions, by_session):
    """Return the stopped read a mark came after, or None.

    A direct To Read or Paused clears the mark's record of it, so with no record it's
    the last journey from before the mark. A journey begun after a Read came after the
    mark, and so did one the book still sits on: a mark takes the book off its journey.
    """
    if book.undated_read_previous_session_id:
        return by_session.get(book.undated_read_previous_session_id)
    last = None
    for session in sessions:
        if session.previous_status == Status.COMPLETED.value:
            break
        last = session
    if last is None or last.status != Status.DROPPED.value or book.current_session_id == last.pk:
        return None
    return by_session[last.pk]


def _book_title(actor_id, item, book, sessions, saves, entries, zone):
    title = Title(actor_id, item, MediaTypes.BOOK.value, PAGE)
    total = item.total_pages or None
    by_session = _walk_journeys(title, sessions, saves, total, zone)
    # A log with no journey behind it is a whole read on its own date.
    for entry in entries:
        if entry.pk not in title.logs:
            day = log_day(entry.consumed_at, zone)
            target, known = _book_target(entry.progress_snapshot or {}, total)
            title.add_log(entry, day, known=known)
            alone = Reading()
            alone.finish(entry.pk, day, entry.created_at, target)
            title.take(alone)
    if book and book.completed_manually:
        # A mark after a stop holds only what that read didn't date.
        stopped = _read_before_mark(book, sessions, by_session)
        dated = stopped.dated if stopped else ZERO
        title.add_to_pile(max(Decimal(total or 0) - dated, ZERO), known=bool(total))
    elif book and book.progress and not sessions:
        title.add_to_pile(Decimal(book.progress))
    return title


def _book_rows(result, owners, _viewer, titles, piles):
    """Add books' rows. Book logs show to every viewer (AC-5), so all read alike."""
    only = {} if titles is None else {"item__in": titles}
    changes = defaultdict(list)
    for change in ProgressChange.objects.filter(
        actor__in=owners,
        item__media_type=MediaTypes.BOOK.value,
        **only,
    ).order_by("id"):
        # A row whose journey is gone never gets read.
        journey_id = change.current_progress.get("journey_id")
        if journey_id is not None:
            changes[journey_id].append(change)
    sessions = defaultdict(list)
    items = {}
    for session in (
        BookSession.objects.filter(
            related_book__user__in=owners,
            **({} if titles is None else {"related_book__item__in": titles}),
        )
        .select_related("completion_diary_entry", "related_book__item")
        .order_by("id")
    ):
        book = session.related_book
        sessions[book.user_id, book.item_id].append(session)
        items[book.item_id] = book.item
    entries = defaultdict(list)
    for entry in (
        DiaryEntry.objects.filter(user__in=owners, item__media_type=MediaTypes.BOOK.value, **only)
        .select_related("item")
        .order_by("id")
    ):
        entries[entry.user_id, entry.item_id].append(entry)
        items[entry.item_id] = entry.item
    books = {}
    for book in Book.objects.filter(user__in=owners, **only).select_related("item"):
        books[book.user_id, book.item_id] = book
        items[book.item_id] = book.item
    zones = {owner.pk: user_calendar.calendar_tz(owner) for owner in owners}
    for key in {*sessions, *entries, *books}:
        actor_id, item_id = key
        title = _book_title(
            actor_id,
            items[item_id],
            books.get(key),
            sessions[key],
            changes,
            entries[key],
            zones[actor_id],
        )
        _collect(result, title, piles=piles)


def _collect(result, title, *, piles):
    result.rows.extend(title.rows())
    if piles and title.in_pile:
        result.piles.append(
            Pile(
                title.actor_id,
                title.bucket,
                amount=title.pile,
                titles={title.item_id},
                known={title.item_id} if title.pile_known else set(),
            ),
        )


ADAPTERS = {MediaTypes.BOOK.value: _book_rows}


def rows_for(owners, viewer, titles=None, types=None, *, piles=True):
    """Return the rows and piles of these people, as this viewer may read them.

    Rows group by person and title before the same fold, so many people cost what one
    does. `titles` limits the walk to those Items. `types` defaults to the switched-on
    ones, so a switched-off adapter costs nothing.
    """
    result = Calculation()
    owners = list(owners)
    for media_type in CALCULATED_TYPES if types is None else types:
        ADAPTERS[media_type](result, owners, viewer, titles, piles)
    return result


# Reading the rows


def in_range(rows, *, media_type=None, start_date=None, end_date=None):
    """Return the rows of a type between two days, each end optional."""
    return [
        row
        for row in rows
        if (media_type is None or row.bucket == media_type)
        and (start_date is None or row.day >= start_date)
        and (end_date is None or row.day <= end_date)
    ]


def piled(piles, media_type=None):
    """Return one Pile holding these piles' totals, for a type or every type."""
    total = Pile(None, media_type)
    for pile in piles:
        if media_type is None or pile.bucket == media_type:
            total.amount += pile.amount
            total.told += pile.told
            total.titles |= pile.titles
            total.known |= pile.known
    return total


def _newest_per_title(rows):
    """Return each title once: `rows` are in list order, so its first is its newest."""
    seen = set()
    newest = []
    for row in rows:
        if row.counts_title and row.title_id not in seen:
            seen.add(row.title_id)
            newest.append(row)
    return newest


def _on_multi_title_days(rows):
    titles_by_day = defaultdict(set)
    for row in rows:
        if row.is_event:
            titles_by_day[row.day].add(row.title_id)
    return [row for row in rows if row.is_event and len(titles_by_day[row.day]) >= 2]


def list_rows(rows, *, media_type=None, start_date=None, end_date=None, counts=None):
    """Return a list's rows, newest first, so it adds up to the number tapped.

    Decision 40: `titles` gives each title once, on its newest row that counts it,
    picked in the list's own order so no page repeats a title. A measure gives every
    day's row. `days` and `weeks` leave out status-only rows: days can't add to weeks,
    so they list the same rows.
    """
    picked = sorted(
        in_range(rows, media_type=media_type, start_date=start_date, end_date=end_date),
        key=lambda row: row.sort_key,
        reverse=True,
    )
    if counts == TITLES:
        return _newest_per_title(picked)
    if counts in (DAYS, WEEKS):
        return [row for row in picked if row.is_event]
    if counts == MULTI_TITLE_DAYS:
        return _on_multi_title_days(picked)
    if counts == EPISODES:
        return [row for row in picked if row.told_unit == EPISODE]
    if counts == HOURS:
        from api.services.stats_time import HOURS_TYPES

        return [row for row in picked if row.bucket in HOURS_TYPES and row.is_event]
    return picked
