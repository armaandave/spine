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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import partial

from app import user_calendar
from app.models import (
    Anime,
    Book,
    BookSession,
    Comic,
    ComicAttempt,
    DiaryEntry,
    Game,
    GameSession,
    Manga,
    MangaAttempt,
    MediaTypes,
    SeriesAttempt,
    Status,
)
from social.models import Follow, FollowStatus, ProgressChange, Visibility

# The types Stats reads through this calculation. Every other type's readers use their
# old counters.
CALCULATED_TYPES = frozenset({MediaTypes.BOOK.value})

PROGRESS = "progress"
LOG = "log"
ZERO = Decimal(0)
PAGE = "page"
PERCENT = "percent"
EPISODE = "episode"
MINUTE = "minute"
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
    # Titles with nothing to add up, which count as titles but not in known and total: a
    # game marked finished has no hours to know.
    no_amount: set = field(default_factory=set)


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

    def __init__(self, actor_id, item, bucket, unit, *, length=None):
        """Start with nothing; `unit` is what its amounts are told in.

        With a `length`, each one told is worth that many in the tab's unit: an anime's
        episode, in minutes. Told units with no length are unknown and add no amount.
        """
        self.actor_id = actor_id
        self.item_id = item.pk
        self.title_key = f"item:{item.pk}"
        self.bucket = bucket
        self.unit = unit
        self.length = length
        self.days = {}
        self.logs = {}
        self.pile = ZERO
        self.pile_told = ZERO
        self.in_pile = False
        self.pile_known = True
        self.pile_no_amount = False

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

    def add_told_to_pile(self, told):
        """Add a number told to the pile, at this title's length."""
        self.pile_told += told
        self.add_to_pile(told * (self.length or 0), known=bool(self.length))

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
            row.progress_id = min(filter(None, (row.progress_id, *(note.change_ids or note.status_ids))), default=None)

    def _take_amount(self, amount, unit):
        if not amount.value:
            return
        if amount.log_id:
            row = self.logs[amount.log_id]
        elif amount.day is None:
            if self.unit == EPISODE:
                self.add_told_to_pile(amount.value)
            else:
                self.add_to_pile(amount.value)
            return
        else:
            row = self._day(amount.day)
            row.last_at = max(filter(None, (row.last_at, amount.at)), default=None)
        if unit == PERCENT:
            # A day that moved in both is told in its own unit; a percent adds nothing.
            if not row.amount:
                row.told += amount.value
                row.told_unit = PERCENT
                row.known = False
            return
        if row.told_unit == PERCENT:
            row.told = ZERO
        row.told += amount.value
        row.told_unit = unit
        if self.unit == EPISODE:
            row.amount += amount.value * (self.length or 0)
            row.known = row.known and bool(self.length)
        else:
            row.amount += amount.value

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


def _pages_of(snapshot, length):
    """Return a saved place in pages, or None for a percent of an unknown length."""
    if snapshot.get("kind") == "pages":
        return Decimal(str(snapshot["value"]))
    if snapshot.get("kind") == "percentage" and length:
        return Decimal(round(Decimal(str(snapshot["value"])) / 100 * length))
    return None


def _book_target(snapshot, total):
    """Return (the pages a finished read comes to, whether that's its real length).

    A snapshot with a max holds the length at the finish, and it stays (STAT-201). With
    no max it's only the last page typed, so the real length wins once it's known.
    """
    if snapshot.get("kind") == "pages" and snapshot.get("max"):
        return Decimal(str(snapshot["value"])), True
    if total:
        return Decimal(total), True
    if snapshot.get("kind") == "pages" and snapshot.get("value"):
        return Decimal(str(snapshot["value"])), False
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
            reading.touch(day, change)
            pages = _pages_of(saved, length)
            if pages is None:
                reading.percents.move_to(Decimal(str(saved["value"])), day, change.created_at)
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
        length = (snapshot.get("max") if snapshot.get("kind") == "pages" else None) or total
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


def _read_before_mark(recorded_id, current_id, journeys, by_journey):
    """Return the stopped read a mark came after, or None.

    A direct To Read or Paused clears the mark's record of it, so with no record it's
    the last journey from before the mark. A journey begun after a Read came after the
    mark, and so did one the title still sits on: a mark takes the title off its
    journey.
    """
    if recorded_id:
        return by_journey.get(recorded_id)
    last = None
    for journey in journeys:
        if journey.previous_status == Status.COMPLETED.value:
            break
        last = journey
    if last is None or last.status != Status.DROPPED.value or current_id == last.pk:
        return None
    return by_journey.get(last.pk)


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
        stopped = _read_before_mark(book.undated_read_previous_session_id, book.current_session_id, sessions, by_session)
        dated = stopped.dated if stopped else ZERO
        title.add_to_pile(max(Decimal(total or 0) - dated, ZERO), known=bool(total))
    elif book and book.progress and not sessions and not (entries and book.status == Status.COMPLETED.value):
        # A place with no journey. A Read with a log holds that log's pages, not more.
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
                told=title.pile_told,
                titles={title.item_id},
                known={title.item_id} if title.pile_known else set(),
                no_amount={title.item_id} if title.pile_no_amount else set(),
            ),
        )


def _who_sees(owners, viewer):
    """Return a test of which logs this viewer sees (AC-5), where visibility counts."""
    viewer_id = viewer.pk if viewer is not None and viewer.is_authenticated else None
    followed = (
        set(
            Follow.objects.filter(
                from_user_id=viewer_id,
                to_user__in=owners,
                status=FollowStatus.ACCEPTED,
            ).values_list("to_user_id", flat=True),
        )
        if viewer_id
        else set()
    )

    def sees(entry):
        if entry.user_id == viewer_id or entry.visibility == Visibility.PUBLIC:
            return True
        return entry.visibility == Visibility.FOLLOWERS and entry.user_id in followed

    return sees


def _logs_by_title(owners, media_type, only):
    logs = defaultdict(list)
    for entry in DiaryEntry.objects.filter(user__in=owners, item__media_type=media_type, **only).select_related("item").order_by("id"):
        logs[entry.user_id, entry.item_id].append(entry)
    return logs


# Games


def _walk_playthrough(title, session, saves, zone):  # noqa: C901
    """Fold one playthrough, its own total (GM-006, GM-102), into rows.

    Saves and the log are read in the order they were saved, so a save after a log stays
    off it (GM-222). A save the log wrote lands on the log's date while the log exists
    (GM-219). Whatever the saves don't explain is a gap against the stored total.
    """
    reading = Reading()
    entry = session.completion_diary_entry
    day_of_log = log_day(entry.consumed_at, zone) if entry else None
    if entry:
        title.add_log(entry, day_of_log, known=session.total_minutes is not None)
    logged = entry is None
    for change in saves:
        if not logged and change.created_at > entry.created_at:
            reading.finish(entry.pk, day_of_log, entry.created_at, None)
            logged = True
        saved = change.current_progress
        kind = saved.get("kind")
        day = saved_day(change, zone)
        if kind == ProgressChange.START:
            reading.touch(day, change, started=True)
        elif kind == ProgressChange.STATUS:
            reading.touch(day, change, move=saved["move"])
        elif kind == ProgressChange.TOUCH:
            reading.touch(day, change)
        elif kind == "minutes" and entry and saved.get("log_id") == entry.pk:
            before = len(reading.counted.amounts)
            reading.counted.move_to(Decimal(str(saved["value"])), day_of_log, change.created_at)
            for amount in reading.counted.amounts[before:]:
                amount.log_id = entry.pk
        elif kind == "minutes":
            reading.touch(day, change)
            reading.counted.move_to(Decimal(str(saved["value"])), day, change.created_at)
        elif kind == "percentage" and not (entry and saved.get("log_id") == entry.pk):
            reading.touch(day, change)
            reading.percents.move_to(Decimal(str(saved["value"])), day, change.created_at)
    if not logged:
        reading.finish(entry.pk, day_of_log, entry.created_at, None)
    if session.total_minutes is not None and Decimal(session.total_minutes) != reading.counted.value:
        _settle_gap(reading, session, entry, day_of_log)
    title.take(reading)


def _settle_gap(reading, session, entry, day_of_log):
    """Land what the saves don't explain: the log's date, a stored day, else no date."""
    total = Decimal(session.total_minutes)
    gap = total - reading.counted.value
    if gap > 0:
        if entry:
            reading.counted.amounts.append(Amount(day_of_log, gap, entry.created_at, entry.pk))
            reading.counted.value = total
        else:
            day = session.minutes_updated_on or session.end_date
            reading.counted.move_to(total, day, session.created_at)
    else:
        reading.counted.move_to(total, None, None)


def _game_title(actor_id, item, game, sessions, saves, entries, zone):
    title = Title(actor_id, item, MediaTypes.GAME.value, MINUTE)
    for session in sessions:
        _walk_playthrough(title, session, saves.get(session.pk, []), zone)
    for entry in entries:
        if entry.pk not in title.logs:
            title.add_log(entry, log_day(entry.consumed_at, zone), known=False)
    if game and game.progress and not sessions:
        # An undated import's hours. Steam's lifetime column is never read (GM-500).
        title.add_to_pile(Decimal(game.progress))
    elif game and game.completed_manually and not entries:
        # Marked finished: a title in All Time with no hours to add or to know.
        title.in_pile = True
        title.pile_no_amount = True
    return title


def _game_rows(result, owners, _viewer, titles, piles):
    """Add games' rows. Game logs show to every viewer (AC-5), so all read alike."""
    only = {} if titles is None else {"item__in": titles}
    changes = defaultdict(list)
    for change in ProgressChange.objects.filter(
        actor__in=owners,
        item__media_type=MediaTypes.GAME.value,
        **only,
    ).order_by("id"):
        playthrough_id = change.current_progress.get("playthrough_id")
        if playthrough_id is not None:
            changes[playthrough_id].append(change)
    sessions = defaultdict(list)
    items = {}
    for session in (
        GameSession.objects.filter(
            related_game__user__in=owners,
            related_game__legacy_archived=False,
            **({} if titles is None else {"related_game__item__in": titles}),
        )
        .select_related("completion_diary_entry", "related_game__item")
        .order_by("id")
    ):
        game = session.related_game
        sessions[game.user_id, game.item_id].append(session)
        items[game.item_id] = game.item
    entries = _logs_by_title(owners, MediaTypes.GAME.value, only)
    for (_, item_id), logs in entries.items():
        items[item_id] = logs[0].item
    games = {}
    for game in Game.objects.filter(user__in=owners, legacy_archived=False, **only).select_related("item"):
        games[game.user_id, game.item_id] = game
        items[game.item_id] = game.item
    zones = {owner.pk: user_calendar.calendar_tz(owner) for owner in owners}
    for key in {*sessions, *entries, *games}:
        actor_id, item_id = key
        title = _game_title(actor_id, items[item_id], games.get(key), sessions[key], changes, entries[key], zones[actor_id])
        _collect(result, title, piles=piles)


# Manga and comics: docs/manga-comics-tracking-contract.md


def _series_target(entry, total):
    """Return what a log says was read: a typed number outlasts a later total."""
    value = (entry.progress_snapshot or {}).get("value")
    if value:
        return Decimal(value)
    return Decimal(total) if total else None


def _series_opening(attempt, counts):
    """Return where an attempt opened: above zero if it picks up from a place."""
    if counts:
        return Decimal(counts[0].previous_progress["value"])
    if attempt.pre_completion:
        return Decimal(attempt.pre_completion.get("place", 0))
    if attempt.completion_diary_entry_id:
        return ZERO
    return Decimal(attempt.place)


def _carries_on_series(reading, attempt, opening):
    """Return whether an attempt continues the read before it (MC-200 to MC-202).

    Reread and Restart begin at zero. A direct log after Did Not Finish continues too.
    """
    if reading is None:
        return False
    if attempt.continues:
        return True
    if attempt.previous_status != Status.DROPPED.value:
        return False
    return opening > 0 or (attempt.completion_diary_entry_id is not None and not attempt.pre_completion)


def _walk_attempt_saves(reading, saves, zone, *, carried):
    for change in saves:
        saved = change.current_progress
        kind = saved.get("kind")
        day = saved_day(change, zone)
        if kind == ProgressChange.START:
            # A start on a read carrying on isn't a start (MC-201).
            reading.touch(day, change, started=not carried)
        elif kind == ProgressChange.STATUS:
            reading.touch(day, change, move=saved["move"])
        elif kind == "count":
            # Only a count holds a number: a status row's 0 would wipe the read.
            reading.touch(day, change)
            reading.counted.move_to(Decimal(saved["value"]), day, change.created_at)


def _log_on_read(title, reading, entry, total, zone, sees):
    """Add a log to its read (MC-007, MC-102). One the viewer can't see adds nothing."""
    target = _series_target(entry, total)
    if not sees(entry):
        if target is not None:
            reading.counted.value = target
        return
    day = log_day(entry.consumed_at, zone)
    title.add_log(entry, day, known=target is not None)
    if target is not None and target < reading.counted.value:
        reading.counted.move_to(target, None, None)
    reading.finish(entry.pk, day, entry.created_at, target)


def _series_title(actor_id, item, row, attempts, saves, entries, unit, zone, sees):  # noqa: C901, PLR0912, PLR0915
    title = Title(actor_id, item, item.media_type, unit)
    total = item.series_total or None
    marked = bool(row and row.completed_manually)
    reading = None
    by_attempt = {}
    linked = set()
    # What the mark holds: a read going on over it holds that number once (plan 5.9).
    opening_over_mark = None
    dated_before_mark = ZERO
    stored_on_mark = None
    for attempt in attempts:
        entry = attempt.completion_diary_entry
        if attempt.origin == SeriesAttempt.Origin.IMPORTED and attempt.status == Status.COMPLETED.value and not entry:
            # An imported read with no date: it counts again, on no day (decision 6).
            title.add_to_pile(Decimal(attempt.place))
            continue
        attempt_saves = saves.get((item.pk, attempt.pk), [])
        counts = [change for change in attempt_saves if change.current_progress.get("kind") == "count"]
        opening = _series_opening(attempt, counts)
        after_mark = marked and attempt.previous_attempt_id is None and attempt.previous_status == Status.COMPLETED.value
        if after_mark and stored_on_mark is None:
            stored_on_mark = Decimal(attempt.previous_place)
        carried = _carries_on_series(reading, attempt, opening)
        if not carried:
            if reading:
                title.take(reading)
            reading = Reading()
        if after_mark and attempt.continues and opening_over_mark is None:
            opening_over_mark = opening
            dated_before_mark = reading.dated
            reading.counted.value = max(reading.counted.value, opening)
        elif opening > reading.counted.value:
            # A place with no day: an import's, on no date (MC-400).
            reading.counted.move_to(opening, None, None)
        by_attempt[attempt.pk] = reading
        _walk_attempt_saves(reading, attempt_saves, zone, carried=carried)
        if entry:
            linked.add(entry.pk)
            _log_on_read(title, reading, entry, total, zone, sees)
    if reading:
        title.take(reading)
    for entry in entries:
        # A log with no attempt counts whole: overwrite imports keep hand-made ones.
        if entry.pk not in linked and sees(entry):
            alone = Reading()
            _log_on_read(title, alone, entry, total, zone, sees)
            title.take(alone)
    if marked:
        stopped = _read_before_mark(row.undated_read_previous_attempt_id, row.current_attempt_id, attempts, by_attempt)
        dated = stopped.dated if stopped else ZERO
        if opening_over_mark is not None:
            base = opening_over_mark
            dated = dated_before_mark
        elif stored_on_mark:
            base = stored_on_mark
        elif row.current_attempt_id is None and row.progress:
            base = Decimal(row.progress)
        else:
            base = Decimal(total or 0)
        title.add_to_pile(max(base - dated, ZERO), known=bool(base or total))
    elif row and row.progress and not attempts and not (entries and row.status == Status.COMPLETED.value):
        title.add_to_pile(Decimal(row.progress))
    return title


def _series_rows(model, attempt_model, unit, result, owners, viewer, titles, piles):
    """Add one series type's rows; attempt ids overlap across the two types."""
    media_type = model._meta.model_name
    only = {} if titles is None else {"item__in": titles}
    changes = defaultdict(list)
    for change in ProgressChange.objects.filter(actor__in=owners, item__media_type=media_type, **only).order_by("id"):
        journey_id = change.current_progress.get("journey_id")
        if journey_id is not None:
            changes[change.item_id, journey_id].append(change)
    attempts = defaultdict(list)
    items = {}
    for attempt in (
        attempt_model.objects.filter(
            tracking__user__in=owners,
            **({} if titles is None else {"tracking__item__in": titles}),
        )
        .select_related("completion_diary_entry", "tracking__item")
        .order_by("id")
    ):
        tracking = attempt.tracking
        attempts[tracking.user_id, tracking.item_id].append(attempt)
        items[tracking.item_id] = tracking.item
    entries = _logs_by_title(owners, media_type, only)
    for (_, item_id), logs in entries.items():
        items[item_id] = logs[0].item
    rows = {}
    for row in model.objects.filter(user__in=owners, **only).select_related("item"):
        rows[row.user_id, row.item_id] = row
        items[row.item_id] = row.item
    zones = {owner.pk: user_calendar.calendar_tz(owner) for owner in owners}
    sees = _who_sees(owners, viewer)
    for key in {*attempts, *entries, *rows}:
        actor_id, item_id = key
        title = _series_title(
            actor_id,
            items[item_id],
            rows.get(key),
            attempts[key],
            changes,
            entries[key],
            unit,
            zones[actor_id],
            sees,
        )
        _collect(result, title, piles=piles)


# Anime. Its history rows are its saves, and each says what it was (AC-2).

# A log written before `log:` rows: its save follows the entry within this.
LOG_SAVE_WINDOW = timedelta(seconds=5)


def _anime_reason(row):
    """Return (the day it counts on, the log that wrote it, whether it's stamped)."""
    reason = row.history_change_reason or ""
    if reason.startswith("day:"):
        return date.fromisoformat(reason[4:]), None, True
    if reason.startswith("log:"):
        return None, int(reason[4:]), True
    return None, None, reason == "mark"


def _unstamped_anime_row(row, first, tracking, zone, logs):
    """Read a row from before the stamps, or written during a rollback.

    Returns (day, log id). Completed is a mark, because web, API and rollback marks
    write it with no stamp, unless it follows a log. A first row far from the tracking
    row's own creation came from a file, and a file's numbers have no day.
    """
    if first and tracking is not None and abs(row.history_date - tracking.created_at) > timedelta(days=1):
        return None, None
    if row.status == Status.COMPLETED.value:
        for entry in logs:
            if timedelta(0) <= row.history_date - entry.created_at <= LOG_SAVE_WINDOW:
                return None, entry.pk
        return None, None
    return row.history_date.astimezone(zone).date(), None


@dataclass
class _HistoryMoment:
    """What a read needs of a save, for an anime's history row: it has no change id."""

    created_at: datetime
    pk: None = None


class _AnimeFold:
    """One person's anime title, its saves and logs in the order they were written."""

    def __init__(self, title, logs, zone, sees):
        self.title = title
        self.zone = zone
        self.sees = sees
        self.live = {entry.pk: entry for entry in logs}
        self.handled = set()
        self.reading = None
        self.finished = False

    def log(self, entry, target):
        """Add what's left on a log's date; with nothing left when finished, a new read.

        A log writes more than one row, and a later one can hold the rise.
        """
        first = entry.pk not in self.handled
        self.handled.add(entry.pk)
        if not self.sees(entry):
            self.reading.counted.value = max(self.reading.counted.value, target)
            return
        day = log_day(entry.consumed_at, self.zone)
        if first:
            self.title.add_log(entry, day, known=bool(self.title.length))
            if self.finished and target and self.reading.counted.value >= target:
                self.title.take(self.reading)
                self.reading = Reading()
        self.reading.finish(entry.pk, day, entry.created_at, target)
        self.finished = True

    def new_read(self):
        if self.reading:
            self.title.take(self.reading)
        self.reading = Reading()
        self.finished = False

    def save(self, row, day, status_before, *, first):
        rise = Decimal(row.progress) - self.reading.counted.value
        moment = _HistoryMoment(row.history_date)
        if rise > 0:
            self.reading.counted.move_to(Decimal(row.progress), day, row.history_date)
            if day:
                self.finished = False
                self.reading.touch(day, moment)
            else:
                self.finished = True
        elif rise < 0:
            self.reading.counted.move_to(Decimal(row.progress), None, None)
            self.finished = False
        if day and row.status == Status.IN_PROGRESS.value and status_before != Status.IN_PROGRESS.value:
            # A pick-up isn't a start. Anime shows no paused or stopped rows (AC-1).
            real = first or status_before not in (Status.PAUSED.value, Status.DROPPED.value)
            self.reading.touch(day, moment, started=real)


def _anime_title(actor_id, item, tracking_rows, history, logs, zone, sees):  # noqa: C901, PLR0912
    title = Title(actor_id, item, MediaTypes.ANIME.value, EPISODE, length=Decimal(item.runtime_minutes or 0))
    fold = _AnimeFold(title, logs, zone, sees)
    live_rows = {row.pk: row for row in tracking_rows}
    rows = [row for row in history if row.history_type != "-"]
    reasons = {}
    seen = set()
    for row in rows:
        day, log_id, stamped = _anime_reason(row)
        if not stamped:
            day, log_id = _unstamped_anime_row(row, row.id not in seen, live_rows[row.id], zone, logs)
        seen.add(row.id)
        reasons[row.history_id] = (day, log_id)
    linked = {log_id for _day, log_id in reasons.values()} - {None}
    events = [((row.history_date, 0, row.history_id), row) for row in rows]
    events += [((entry.created_at, 1, entry.pk), entry) for entry in logs if entry.pk not in linked]
    events.sort(key=lambda event: event[0])
    fold.new_read()
    status_before = {}
    current = None
    for _, event in events:
        if isinstance(event, DiaryEntry):
            fold.log(event, fold.reading.counted.value)
            continue
        first = event.id not in status_before
        if event.id != current:
            if current is not None:
                fold.new_read()
            current = event.id
        day, log_id = reasons[event.history_id]
        if log_id is None:
            fold.save(event, day, status_before.get(event.id), first=first)
        elif log_id in fold.live:
            fold.log(fold.live[log_id], Decimal(event.progress))
        status_before[event.id] = event.status
    title.take(fold.reading)
    # A tracking row with progress and no history at all has no day to read.
    for row in tracking_rows:
        if row.progress and row.pk not in status_before:
            title.add_told_to_pile(Decimal(row.progress))
    if not tracking_rows:
        # A removed row's history names no title, so nothing says how far this log got.
        for entry in logs:
            if sees(entry):
                title.add_log(entry, log_day(entry.consumed_at, zone), known=False)
    elif not title.in_pile and not logs and any(row.status == Status.COMPLETED.value for row in tracking_rows):
        # Marked with no number: a title in All Time with nothing to add (STAT-105).
        title.in_pile = True
        title.pile_known = False
    return title


def _anime_rows(result, owners, viewer, titles, piles):
    """Add anime's rows. Its start rows are for installed builds, and aren't read."""
    only = {} if titles is None else {"item__in": titles}
    tracking = defaultdict(list)
    items = {}
    for row in Anime.objects.filter(user__in=owners, **only).select_related("item"):
        tracking[row.user_id, row.item_id].append(row)
        items[row.item_id] = row.item
    owner_of = {row.pk: key for key, rows in tracking.items() for row in rows}
    history = defaultdict(list)
    for row in Anime.history.model.objects.filter(id__in=list(owner_of)).order_by("history_date", "history_id"):
        history[owner_of[row.id]].append(row)
    logs = _logs_by_title(owners, MediaTypes.ANIME.value, only)
    for (_, item_id), entries in logs.items():
        items[item_id] = entries[0].item
    zones = {owner.pk: user_calendar.calendar_tz(owner) for owner in owners}
    sees = _who_sees(owners, viewer)
    for key in {*tracking, *logs}:
        actor_id, item_id = key
        title = _anime_title(actor_id, items[item_id], tracking[key], history[key], logs[key], zones[actor_id], sees)
        _collect(result, title, piles=piles)


ADAPTERS = {
    MediaTypes.BOOK.value: _book_rows,
    MediaTypes.GAME.value: _game_rows,
    MediaTypes.ANIME.value: _anime_rows,
    MediaTypes.MANGA.value: partial(_series_rows, Manga, MangaAttempt, "chapter"),
    MediaTypes.COMIC.value: partial(_series_rows, Comic, ComicAttempt, "issue"),
}


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
            total.no_amount |= pile.no_amount
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
