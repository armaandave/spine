"""Anime tracking: the one place that records anime facts and works out everything else.

docs/anime-tracking-contract.md is the source of truth; AN-000 names the TV laws it
inherits, and law IDs are cited where they decide behaviour. An anime title works like
one TV season with no show around it, so this follows app.tv_tracking's shape and reuses
its errors, its entry points and its mutation records.

Facts are what the user did:

- viewings: AnimeViewing rows. kind "tick" is a tap or a webhook watch, dated; kind "fill"
  is an undated viewing a mark or a log added (TV-100, TV-301);
- marks: AnimeMark rows, one per action, each ending with its last fill;
- logs: DiaryEntry rows on the title's Item, stamped progress_snapshot["anime"]["v"] = 1,
  with what they covered in ["covers"] and their pass in ["pass"] (AN-400);
- choices, a rewatch, the earlier-episodes question and the log screen's offer, on the
  Anime row (TV-005 to TV-007, TV-107, AN-203, AN-600);
- stars and the heart (AN-301).

Everything else (status, counts, up next, caught up, next airing, last-watched dates) is
worked out from those facts, the stored listing (app.anime_listing) and now, and stored on
the Anime row by _reconcile in the write's transaction (TV-207). Anime.progress is always
the AN-102 count.
"""

import copy
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from django.db import IntegrityError, transaction
from simple_history.utils import bulk_update_with_history

from api.services import stats_cache
from app import anime_listing, single_weight, user_calendar
from app.anime_listing import AnimeDetailsUnavailable, Listing
from app.models import (
    Anime,
    AnimeMark,
    AnimeViewing,
    DiaryEntry,
    MediaLike,
    MediaTypes,
    Sources,
    Status,
    TVMutation,
)
from app.tv_tracking import (
    ACTIVITY_ENTRY_POINTS,
    API_ENTRY_POINTS,
    APP,
    IMPORT,
    JOB,
    LEGACY_APP,
    MSG_CONFIRM,
    MSG_FUTURE_LOG,
    MSG_FUTURE_WATCH,
    MSG_LOG_FIRST,
    MSG_MUTATION_REQUIRED,
    MSG_MUTATION_REUSED,
    MSG_NOT_YOURS,
    MSG_NOTHING_AIRED,
    MSG_REWATCH_IN_PROGRESS,
    OPINION_LOG,
    OPINION_MARK,
    OPINION_SAVE,
    WEB,
    WEBHOOK,
    TVConflict,
    TVInvalid,
    TVLogBlocks,
    TVLogFirst,
    TVStateChanged,
    _api_rating,
    _audit_diary,
    _audit_like,
    _carrier,
    _create_diary_activity,
    _iso,
    _log_day_of,
    _mutation_uuid,
    _parse_log_day,
    _raw_delete,
    _set_like_row,
    _set_rating_activity,
    _short_date,
    _start_of_day,
    _update_diary_activity,
    _validate_rating,
    _version_after,
)

logger = logging.getLogger(__name__)

TICK = AnimeViewing.Kind.TICK.value
FILL = AnimeViewing.Kind.FILL.value
PLANNING = Status.PLANNING.value
WATCHING = Status.IN_PROGRESS.value
PAUSED = Status.PAUSED.value
DROPPED = Status.DROPPED.value
COMPLETED = Status.COMPLETED.value
CHOICES = (PLANNING, WATCHING, PAUSED, DROPPED)
HOLDING_CHOICES = (PLANNING, PAUSED, DROPPED)
SELECTED_ACTIONS = {
    WATCHING: "watching",
    PLANNING: "planning",
    PAUSED: "pause",
    DROPPED: "drop",
}
STAMP_VERSION = 1
TITLE = "title"
# A tick may name an episode airing up to a day from now, which covers time zones (AN-103).
AIR_TIME_SLACK = timedelta(days=1)
# Past any real title: the longest anime has under 10,000 episodes (AN-103). It also keeps a
# number from building a list that exhausts a worker, or overflowing the column.
MAX_EPISODE = 20000

MSG_NOT_ANIME = "This isn't an anime."
MSG_EPISODE_UNAIRED = "This episode hasn't aired yet."
MSG_ALL_WATCHED = "You've watched everything that has aired."
MSG_STARTED = "You've started this anime. Pause or drop it instead."
MSG_FINISH_FIRST = "Finish this anime before rewatching it."
MSG_NOT_REWATCHING = "You're not rewatching this anime."
MSG_DELETE_LOGS = "Delete this anime's logs first."
MSG_DELETE_LOG = "Delete your log first."
MSG_NAME_EPISODES = "Name the first and last episodes to mark."
MSG_MARK_UP_TO = "Mark watched up to here works on aired episodes."
MSG_NO_NUMBER = "Enter a valid episode number."
MSG_SINGLE_CHOICE = "This has no episodes to keep your place in. Mark it watched or log it."
MSG_SINGLE_WATCHED = "You've already watched this anime."


def supports(value):
    """Return whether an item or media-type value is an anime."""
    return getattr(value, "media_type", value) == MediaTypes.ANIME.value


@dataclass
class WriteResult:
    """What an anime write did, for the entry point to build its response."""

    user: object
    item: object
    row: Anime | None
    changed: bool
    entry: DiaryEntry | None = None
    finished: dict | None = None
    earlier_unwatched: dict | None = None
    episode_number: int | None = None
    replayed: bool = False
    # The listing the write used, so the caller can build payloads without a refetch.
    listing: object = None
    # Whether the rows the app holds are out of date beyond the written episode's.
    rows_changed: bool = True


# ---------------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------------


@dataclass
class _Viewing:
    id: int
    episode: int
    kind: str
    mark_id: int | None
    entry_id: int | None
    pass_number: int
    watched_on: date | None
    watched_at: datetime | None
    created_at: datetime

    @property
    def is_mark_fill(self):
        return self.kind == FILL and self.entry_id is None


@dataclass
class _Log:
    id: int
    consumed_at: datetime
    created_at: datetime
    rating: object
    liked: bool
    is_rewatch: bool
    snapshot: dict | None
    day: date
    stamped: bool
    covers: frozenset
    pass_number: int


@dataclass
class _Facts:
    viewings: list
    logs: list
    liked: bool


def _ranges(numbers):
    """Return sorted numbers as [[first, last], ...], so One Piece's log stays small."""
    ranges = []
    for number in sorted(set(numbers)):
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ranges


def _covers_from(snapshot):
    anime = (snapshot or {}).get("anime") if isinstance(snapshot, dict) else None
    numbers = set()
    for span in (anime or {}).get("covers") or ():
        numbers.update(range(span[0], span[1] + 1))
    return frozenset(numbers)


def _stamped(snapshot):
    return isinstance(snapshot, dict) and isinstance(snapshot.get("anime"), dict)


def _load_facts(user, item, row):
    viewings = []
    if row is not None and row.pk is not None:
        viewings = [
            _Viewing(*values)
            for values in AnimeViewing.objects.filter(anime=row)
            .order_by("created_at", "id")
            .values_list(
                "id",
                "episode_number",
                "kind",
                "mark_id",
                "diary_entry_id",
                "pass_number",
                "watched_on",
                "watched_at",
                "created_at",
            )
        ]
    logs = []
    for entry_id, consumed_at, created_at, rating, liked, is_rewatch, snapshot in (
        DiaryEntry.objects.filter(user=user, item=item)
        .order_by("created_at", "id")
        .values_list("id", "consumed_at", "created_at", "rating", "liked", "is_rewatch", "progress_snapshot")
    ):
        stamped = _stamped(snapshot)
        logs.append(
            _Log(
                id=entry_id,
                consumed_at=consumed_at,
                created_at=created_at,
                rating=rating,
                liked=liked,
                is_rewatch=is_rewatch,
                snapshot=snapshot,
                day=_log_day_of(consumed_at, user),
                stamped=stamped,
                covers=_covers_from(snapshot),
                pass_number=int((snapshot["anime"].get("pass") or 1) if stamped else 1),
            ),
        )
    # Code from before these tables can delete a log after a rollback, and its fills stay
    # behind: with no log they read as undated (AN-901).
    log_ids = {log.id for log in logs}
    for viewing in viewings:
        if viewing.entry_id not in log_ids:
            viewing.entry_id = None
    liked = MediaLike.objects.filter(user=user, item=item).exists()
    return _Facts(viewings=viewings, logs=logs, liked=liked)


# ---------------------------------------------------------------------------------
# Working out the state (AN-100 to AN-103, AN-200, AN-201, AN-600)
# ---------------------------------------------------------------------------------


@dataclass
class _Calc:
    single: bool
    aired: list
    aired_set: set
    aired_known: bool
    may_have_aired: bool
    rewatching: bool
    pass_number: int
    watched_pass: set
    viewed_any: set
    aired_count: int
    watched_count: int
    cap: int | None
    finished: bool
    all_watched: bool
    caught_up: bool
    next_airing: tuple | None
    premiere: date | None
    up_next: int | None
    up_next_air_date: date | None
    up_next_is_new: bool
    first_gap: int | None
    last_watched_on: date | None
    last_activity_on: date | None
    last_activity_at: datetime | None
    recorded: bool
    started: bool
    in_library: bool
    status: str | None
    rewatch_done: bool
    first_dated_at: datetime | None
    last_dated_at: datetime | None


def _in_pass(calc_or_rewatching, pass_number, current_pass=None):
    """Return whether something of this pass is in the current pass (AN-600, TV-601)."""
    if isinstance(calc_or_rewatching, _Calc):
        current_pass = calc_or_rewatching.pass_number
        calc_or_rewatching = calc_or_rewatching.rewatching
    return not calc_or_rewatching or pass_number == current_pass


def _calculate(facts, listing, now, *, row, user, ignore_rewatch=False):  # noqa: C901, PLR0912
    rewatching = bool(row is not None and row.rewatch_started_at is not None and not ignore_rewatch)
    current_pass = row.pass_number if row is not None else 1
    in_pass = [viewing for viewing in facts.viewings if _in_pass(rewatching, viewing.pass_number, current_pass)]
    viewed_any = {viewing.episode for viewing in facts.viewings}
    watched_pass = {viewing.episode for viewing in in_pass}
    cap = listing.cap
    # A viewing makes its episode aired, but a total that fell below it counts it for
    # nothing (AN-100, AN-103).
    counted = {number for number in viewed_any if cap is None or number <= cap}
    aired_set = set(range(1, listing.aired_through(now) + 1)) | counted
    if listing.may_have_aired and not counted:
        # With no numbers its first row can be ticked, marked and logged (AN-101).
        aired_set.add(1)
    aired = sorted(aired_set)
    watched = watched_pass & aired_set
    all_watched = bool(aired) and len(watched) == len(aired)
    finished = cap is not None and cap in aired_set and all(number in watched for number in range(1, cap + 1))
    more_coming = cap is None or cap not in aired_set
    premiere = None
    if not aired:
        premiere = listing.premiere()
        if isinstance(premiere, datetime):
            premiere = user_calendar.day_of(premiere, user)
    next_airing = None
    upcoming = listing.next_airing(aired_set)
    if upcoming is not None:
        number, air_at = upcoming
        day = user_calendar.day_of(air_at, user) if air_at is not None else None
        next_airing = (number, day or (premiere if number == 1 else None))
    up_next = None
    up_next_air_date = None
    up_next_is_new = False
    first_gap = None
    if not listing.is_single:
        ticks = [viewing for viewing in in_pass if viewing.kind == TICK and viewing.episode in aired_set]
        if ticks:
            latest = max(
                ticks,
                key=lambda viewing: (
                    viewing.watched_on is not None,
                    viewing.watched_on or date.min,
                    viewing.created_at,
                    viewing.episode,
                ),
            ).episode
        else:
            latest = max(watched, default=None)
        if latest is not None:
            up_next = next((number for number in aired if number > latest and number not in watched), None)
        if up_next is None:
            up_next = next((number for number in aired if number not in watched), None)
        if up_next is not None and listing.air_at(up_next) is not None:
            up_next_air_date = user_calendar.day_of(listing.air_at(up_next), user)
        if aired and not finished and aired[-1] in watched and len(watched) < len(aired):
            first_gap = next(number for number in aired if number not in watched)

    ticks_all = [viewing for viewing in facts.viewings if viewing.kind == TICK]
    dated_days = [viewing.watched_on for viewing in ticks_all if viewing.watched_on is not None]
    dated_days.extend(log.day for log in facts.logs)
    last_watched_on = max(dated_days) if dated_days else None
    activity = [
        (viewing.watched_on or user_calendar.day_of(viewing.created_at, user), viewing.created_at)
        for viewing in ticks_all
    ]
    activity.extend((log.day, log.created_at) for log in facts.logs)
    if not activity:
        activity = [(user_calendar.day_of(viewing.created_at, user), viewing.created_at) for viewing in facts.viewings]
    if not activity and row is not None and row.choice == WATCHING and row.choice_at is not None:
        activity = [(user_calendar.day_of(row.choice_at, user), row.choice_at)]
    last_activity_on, last_activity_at = max(activity) if activity else (None, None)
    if up_next_air_date is not None and activity:
        up_next_is_new = up_next_air_date > last_activity_on

    recorded = bool(facts.viewings or facts.logs)
    choice = row.choice if row is not None else ""
    started = recorded or choice == WATCHING
    in_library = row is not None and row.pk is not None and (recorded or bool(choice))
    if not in_library:
        status = None
    elif choice in HOLDING_CHOICES:
        status = choice
    elif rewatching:
        status = WATCHING
    elif finished:
        status = COMPLETED
    elif started:
        status = WATCHING
    else:
        status = None

    first_dated_at, last_dated_at = _date_bounds(facts)
    return _Calc(
        single=listing.is_single,
        aired=aired,
        aired_set=aired_set,
        aired_known=listing.aired_is_known,
        may_have_aired=listing.may_have_aired,
        rewatching=rewatching,
        pass_number=current_pass,
        watched_pass=watched_pass,
        viewed_any=viewed_any,
        aired_count=len(aired),
        watched_count=len(watched),
        cap=cap,
        finished=finished,
        all_watched=all_watched,
        caught_up=all_watched and more_coming,
        next_airing=next_airing,
        premiere=premiere,
        up_next=up_next,
        up_next_air_date=up_next_air_date,
        up_next_is_new=up_next_is_new,
        first_gap=first_gap,
        last_watched_on=last_watched_on,
        last_activity_on=last_activity_on,
        last_activity_at=last_activity_at,
        recorded=recorded,
        started=started,
        in_library=in_library,
        status=status,
        rewatch_done=rewatching and all_watched,
        first_dated_at=first_dated_at,
        last_dated_at=last_dated_at,
    )


def _date_bounds(facts):
    """Return the first and last moments of the title's dated facts: ticks and logs."""
    moments = [
        viewing.watched_at for viewing in facts.viewings if viewing.kind == TICK and viewing.watched_at is not None
    ]
    moments.extend(log.consumed_at for log in facts.logs)
    return (min(moments), max(moments)) if moments else (None, None)


def _opinion_action(calc):
    """Return what stars or a heart do now (AN-301): only save, mark, or need a log.

    A single has no partway: not watched, they mark it (AN-002).
    """
    if not calc.aired or calc.all_watched:
        return OPINION_SAVE
    if calc.started and not calc.single:
        return OPINION_LOG
    return OPINION_MARK


def _with_own_total(listing, row):
    """Return a listing no copy backs with the total kept on this person's row (AN-100).

    A manual title has no provider, so the number its owner wrote is its total; an imported
    title has what its file said until the first read of MAL. Both stay on the row, as its
    aired count, and never reach anyone else.
    """
    if row is None or listing.fetched_at is not None or listing.total is not None:
        return listing
    total = row.aired_count
    if total is None and row.reconciled_at is None and row.status == COMPLETED:
        total = row.progress
    return replace(listing, total=total) if total else listing


def _completed_numbers(listing, now, count):
    """Return what a stored or imported Completed marks: everything aired (AN-800, AN-902).

    With nothing aired to go on, or no way to know how many have, its own number says more.
    """
    aired = listing.aired_through(now)
    if not aired or listing.may_have_aired:
        aired = max(aired, count or 0)
    return list(range(1, aired + 1))


def _stored_facts(row, listing, now):
    """Return (episodes, choice) a row's stored numbers become at its first write (AN-902).

    Only for a row never worked out. A stored Completed marks everything aired, any other
    number marks that many episodes from the first, and a stored status with nothing
    recorded is the user's choice.
    """
    if row is None or row.reconciled_at is not None:
        return [], ""
    if row.status == COMPLETED:
        numbers = _completed_numbers(listing, now, row.progress)
    else:
        numbers = list(range(1, row.progress + 1))
    choice = ""
    if row.status in HOLDING_CHOICES:
        choice = row.status
    elif row.status == WATCHING and not numbers:
        choice = WATCHING
    return numbers, choice


# ---------------------------------------------------------------------------------
# The write context
# ---------------------------------------------------------------------------------


class _Ctx:
    """One locked write: the user, the title, its listing, its row and facts."""

    def __init__(self, user, item, listing, entry_point, row):
        self.user = user
        self.item = item
        self.listing = _with_own_total(listing, row)
        self.entry_point = entry_point
        self.now = user_calendar.now()
        self.today = user_calendar.today(user)
        self.row = row
        self.created = False
        self.changed = False
        self.dirty = set()
        self.version_before = row.state_version if row is not None else 0
        self.status_before = row.status if row is not None else None
        self.count_before = row.progress if row is not None else 0
        self._history_rows = 0
        # Ticks before the write: activity counts only how ticks moved (TV-703). None
        # outside a write: nothing counts.
        self.ticks_before = None
        # Set by a tick, Watching or a rewatch, which can start the title (AN-703).
        self.may_start = False
        self.facts = None
        self._calc = None
        self.reload()
        # A row's dates follow its dated facts only when those moved (_start_date).
        self.dates_before = _date_bounds(self.facts)

    def reload(self):
        self.facts = _load_facts(self.user, self.item, self.row)
        self._calc = None

    @property
    def calc(self):
        if self._calc is None:
            self._calc = _calculate(self.facts, self.listing, self.now, row=self.row, user=self.user)
        return self._calc

    def invalidate(self):
        self._calc = None

    def history_moment(self):
        """Return the date of this write's next history row: each its own, in order.

        A row's place in the history goes by its date, and the creation row would otherwise
        read the clock after the write's first row.
        """
        moment = self.now + timedelta(microseconds=self._history_rows)
        self._history_rows += 1
        return moment

    def set_own_total(self, number=None):
        """Settle a manual title's total: the number its owner wrote, or what's watched (AN-100)."""
        if self.item.source != Sources.MANUAL.value:
            return
        if number and number > MAX_EPISODE:
            raise TVInvalid(MSG_NO_NUMBER)
        total = max(number or 0, self.listing.total or 0, max(self.calc.viewed_any, default=0), 1)
        if number == total:
            # It's theirs now, and no undo erases it.
            self.ensure_row()
            self.set_row(total_is_written=True)
        if total != self.listing.total:
            self.listing = replace(self.listing, total=total)
            self.invalidate()

    def ensure_row(self):
        if self.row is None:
            self.row = _create_row(
                self.user,
                self.item,
                history_date=self.history_moment() if self.entry_point != IMPORT else None,
            )
            self.created = True
            self.version_before = self.row.state_version
            self.status_before = None
            self.count_before = 0
        return self.row

    def set_row(self, **values):
        for name, value in values.items():
            if name in ("rating_source", "like_source"):
                current = getattr(self.row, f"{name}_id")
                wanted = value.pk if value is not None else None
            else:
                current = getattr(self.row, name)
                wanted = value
            if current != wanted:
                setattr(self.row, name, value)
                self.dirty.add(name)
        self.invalidate()

    def flush_row(self):
        if self.row is not None and self.row.pk is not None and self.dirty:
            if self.entry_point == IMPORT:
                # An import writes one history row per title, at its end (import_title).
                Anime.objects.filter(pk=self.row.pk).update(**{name: getattr(self.row, name) for name in self.dirty})
            else:
                bulk_update_with_history(
                    [self.row],
                    Anime,
                    fields=sorted(self.dirty),
                    default_user=self.user,
                    default_date=self.history_moment(),
                )
            self.dirty.clear()

    def add_ticks(self, numbers, *, watched_on, watched_at):
        self.ensure_row()
        AnimeViewing.objects.bulk_create(
            [
                AnimeViewing(
                    anime=self.row,
                    episode_number=number,
                    kind=TICK,
                    pass_number=self.row.pass_number,
                    watched_on=watched_on,
                    watched_at=watched_at,
                )
                for number in numbers
            ],
            batch_size=1000,
        )
        self.changed = True

    def add_fills(self, numbers, *, origin=None, entry=None, pass_number=None):
        """Add undated viewings as one mark (origin), or as a log's own (entry), in bulk."""
        numbers = sorted(set(numbers))
        if not numbers:
            return 0
        self.ensure_row()
        pass_number = pass_number or self.row.pass_number
        mark = None
        if entry is None:
            mark = AnimeMark.objects.create(anime=self.row, origin=origin, pass_number=pass_number)
        AnimeViewing.objects.bulk_create(
            [
                AnimeViewing(
                    anime=self.row,
                    episode_number=number,
                    kind=FILL,
                    pass_number=pass_number,
                    mark=mark,
                    diary_entry=entry,
                )
                for number in numbers
            ],
        )
        self.changed = True
        return len(numbers)

    def delete_viewings(self, viewings):
        ids = [viewing.id for viewing in viewings]
        if not ids:
            return 0
        _raw_delete(AnimeViewing.objects.filter(pk__in=ids))
        self.changed = True
        return len(ids)

    def end_choice(self):
        """A tick, log or mark ends a Planning, Paused or Dropped choice (TV-203)."""
        if self.row is not None and self.row.choice in HOLDING_CHOICES:
            self.set_row(ended_choice=self.row.choice, ended_choice_at=self.now, choice="")
            self.changed = True

    def use_question(self):
        """The title's first write uses the earlier-episodes question up (TV-107)."""
        self.ensure_row()
        if self.row.earlier_question_at is None:
            self.set_row(earlier_question_at=self.now)


def _locked_row(user, item):
    """Lock and return the user's row, and only it (TV-705).

    Meta's ordering would join the title and the user, and FOR UPDATE would lock those
    too, so one person's write would hold up everyone else on the same title.
    """
    return Anime.objects.select_for_update(of=("self",)).filter(user=user, item=item).order_by("pk").first()


def _create_row(user, item, *, history_date):
    """Create the user's tracking row (inside the lock), with history at that date, or none.

    When a simultaneous first write got here first, IntegrityError goes up: this write's
    facts are out of date, so _perform runs it again against that row (TV-705).
    """
    seed = (
        TVMutation.objects.filter(user=user, show_item=item, action="remove")
        .order_by("-created_at", "-id")
        .values_list("result", flat=True)
        .first()
    )
    row = Anime(user=user, item=item, status=WATCHING, state_version=int((seed or {}).get("state_version") or 0))
    if history_date is None:
        row.skip_history_when_saving = True
    else:
        row._history_date = history_date  # noqa: SLF001
    with transaction.atomic():
        row.save_base(force_insert=True)
    # Both would carry over to the row's later history.
    if history_date is None:
        del row.skip_history_when_saving
    else:
        del row._history_date  # noqa: SLF001
    return row


# ---------------------------------------------------------------------------------
# Running a write
# ---------------------------------------------------------------------------------


def _resolve_item(item):
    if not supports(item):
        raise TVInvalid(MSG_NOT_ANIME)
    return item


def _listing_on_hand(item):
    """Return what's stored, for a write that needs no provider: it never waits on or is refused for one."""
    return anime_listing.stored_listing(item) or Listing(item=item)


def _replay(user, item, mutation_id, action):
    record = TVMutation.objects.filter(user=user, mutation_id=mutation_id).first()
    if record is None:
        return None
    if record.action != action or record.show_item_id != item.pk:
        raise TVInvalid(MSG_MUTATION_REUSED)
    result = record.result or {}
    return WriteResult(
        user=user,
        item=item,
        row=Anime.objects.filter(user=user, item=item).first(),
        changed=bool(result.get("changed")),
        entry=record.diary_entry,
        finished=result.get("finished"),
        earlier_unwatched=result.get("earlier_unwatched"),
        episode_number=result.get("episode_number"),
        replayed=True,
    )


def _perform(  # noqa: PLR0913
    user,
    item,
    action,
    body,
    *,
    entry_point,
    mutation_id=None,
    listing=None,
    episode_number=None,
    rows_changed=True,
):
    """Run one write: the listing first, then the locked transaction (TV-705)."""
    item = _resolve_item(item)
    mutation_id = _mutation_uuid(mutation_id)
    if mutation_id is not None:
        replay = _replay(user, item, mutation_id, action)
        if replay is not None:
            replay.listing = listing
            return replay
    if listing is None and entry_point == IMPORT:
        # An import reads what is stored, whatever that holds (AN-800).
        listing = _listing_on_hand(item)
    if listing is None:
        listing = anime_listing.get_listing(item)
    for attempt in range(2):
        try:
            with transaction.atomic():
                return _run_locked(
                    user,
                    item,
                    action,
                    body,
                    entry_point=entry_point,
                    mutation_id=mutation_id,
                    listing=listing,
                    episode_number=episode_number,
                    rows_changed=rows_changed,
                )
        except IntegrityError:
            if attempt:
                raise
            if mutation_id is not None:
                replay = _replay(user, item, mutation_id, action)
                if replay is not None:
                    replay.listing = listing
                    return replay
            # A concurrent first write created the row; run again against it.
            continue
    return None  # pragma: no cover


def _run_locked(  # noqa: PLR0913
    user,
    item,
    action,
    body,
    *,
    entry_point,
    mutation_id,
    listing,
    episode_number,
    rows_changed,
):
    row = _locked_row(user, item)
    if mutation_id is not None:
        replay = _replay(user, item, mutation_id, action)
        if replay is not None:
            replay.listing = listing
            return replay
    ctx = _Ctx(user, item, listing, entry_point, row)
    if entry_point != JOB:
        _adopt_stored_row(ctx)
    ctx.ticks_before = _tick_count(ctx.facts)
    rows_before = _row_numbers(ctx.calc, ctx.listing)
    outcome = body(ctx) or {}
    result = WriteResult(
        user=user,
        item=item,
        row=None,
        changed=bool(outcome.get("changed", ctx.changed)),
        entry=outcome.get("entry"),
        finished=outcome.get("finished"),
        earlier_unwatched=outcome.get("earlier_unwatched"),
        episode_number=outcome.get("episode_number", episode_number),
        listing=listing,
        rows_changed=rows_changed,
    )
    if result.changed or ctx.created:
        # Viewings are bulk-created and raw-deleted, so no receiver sees them (STAT-006).
        stats_cache.bump_user(user)
    if ctx.row is not None and ctx.row.pk is not None and not outcome.get("left_library"):
        _reconcile(ctx)
        ctx.row.refresh_from_db()
        result.row = ctx.row
        _record_start(ctx)
    if not rows_changed:
        if ctx.row is None:
            # It left the library: the list is worked out with nothing recorded.
            ctx.reload()
        result.rows_changed = _row_numbers(ctx.calc, ctx.listing) != rows_before
    if mutation_id is not None or outcome.get("record_mutation"):
        TVMutation.objects.create(
            user=user,
            mutation_id=mutation_id or uuid.uuid4(),
            action=action,
            show_item=item,
            diary_entry=result.entry,
            result={
                "changed": result.changed,
                "episode_number": result.episode_number,
                "finished": result.finished,
                "earlier_unwatched": result.earlier_unwatched,
                "diary_entry_id": result.entry.pk if result.entry is not None else None,
                **(outcome.get("mutation_extra") or {}),
            },
        )
    return result


def _adopt_stored_row(ctx):
    """Turn a row's stored numbers into facts at the user's first write (AN-902)."""
    row = ctx.row
    if row is None or row.reconciled_at is not None:
        return
    numbers, choice = _stored_facts(row, ctx.listing, ctx.now)
    if ctx.item.source == Sources.MANUAL.value and ctx.listing.total:
        # What old code stored as a manual title's number was its owner's.
        ctx.set_row(total_is_written=True)
    watched = {viewing.episode for viewing in ctx.facts.viewings}
    missing = [number for number in numbers if number not in watched]
    if missing:
        ctx.add_fills(missing, origin=AnimeMark.Origin.STORED)
    if choice and not row.choice:
        ctx.set_row(choice=choice, choice_at=ctx.now)
    ctx.reload()
    ctx.count_before = ctx.calc.watched_count
    if missing or choice:
        ctx.changed = False


# ---------------------------------------------------------------------------------
# Reconcile (TV-207)
# ---------------------------------------------------------------------------------


def _tick_count(facts):
    return sum(1 for viewing in facts.viewings if viewing.kind == TICK)


def _progress_snapshot(value, maximum):
    return {"kind": "count", "value": value, "max": maximum, "unit": "episode"}


def _reconcile(ctx):  # noqa: C901
    """Work out and store the title's state from the facts (TV-207).

    Writes only changed values; bumps state_version when anything changed; records one
    ProgressChange when the count changed or ticks moved, moved only by ticks (TV-703);
    ends a finished rewatch (AN-600); never invents a date, deletes a fact or changes a
    choice; idempotent.
    """
    from social.models import ProgressChange  # noqa: PLC0415

    row = ctx.row
    ctx.reload()
    calc = ctx.calc
    if calc.rewatch_done:
        ctx.set_row(rewatch_started_at=None)
        calc = ctx.calc
    written = {}
    if calc.status is not None:
        written["status"] = calc.status
    first_before, last_before = ctx.dates_before
    written["start_date"] = _start_date(row.start_date, first_before, calc.first_dated_at)
    written["end_date"] = _end_date(row.end_date, last_before, calc.last_dated_at, calc.status or row.status)
    ctx.dates_before = (calc.first_dated_at, calc.last_dated_at)
    if row.progress != calc.watched_count:
        written["progress"] = calc.watched_count
        written["progressed_at"] = ctx.now
    log_ids = {log.id for log in ctx.facts.logs}
    if row.rating_source_id not in (None, *log_ids):
        written["rating_source"] = None
    if row.like_source_id not in (None, *log_ids):
        written["like_source"] = None
    ctx.set_row(**written)
    calc = ctx.calc

    cache = _stored_values(calc)
    cache_changes = {name: value for name, value in cache.items() if getattr(row, name) != value}

    moved = _tick_count(ctx.facts) - ctx.ticks_before if ctx.ticks_before is not None else 0
    if calc.watched_count != ctx.count_before or moved:
        # Only ticks move progress. A mark, fill, log or rewatch records the new count
        # without moving it: installed builds' Home shows the count, and activity shows
        # nothing for them (TV-703, activity contract AC-1).
        ProgressChange.objects.create(
            actor=ctx.user,
            item=ctx.item,
            previous_progress=_progress_snapshot(calc.watched_count - moved, cache["aired_count"]),
            current_progress=_progress_snapshot(calc.watched_count, cache["aired_count"]),
        )

    anything = bool(ctx.changed or ctx.dirty or cache_changes or ctx.created)
    ctx.flush_row()
    if anything:
        # The row's state is written with update() and bulk writes (STAT-006).
        stats_cache.bump_user(ctx.user)
    # With no copy to go on, a row never worked out still can't be: its stored number waits
    # for the real ones (AN-902).
    can_work_out = (
        row.reconciled_at is not None
        or ctx.listing.fetched_at is not None
        or ctx.item.source == Sources.MANUAL.value
        or ctx.entry_point == IMPORT
    )
    if can_work_out and (anything or row.reconciled_at is None):
        cache_changes["reconciled_at"] = ctx.now
    if anything:
        cache_changes["state_version"] = _version_after(row.state_version, ctx.now)
    if cache_changes:
        Anime.objects.filter(pk=row.pk).update(**cache_changes)
        for name, value in cache_changes.items():
            setattr(row, name, value)
    # An import's end reloads the calendar, which reads its titles in bulk (AN-800).
    if ctx.entry_point != IMPORT and (
        ctx.created or (ctx.status_before in (PAUSED, DROPPED) and row.status not in (PAUSED, DROPPED))
    ):
        item = ctx.item
        transaction.on_commit(lambda: item.fetch_releases(delay=True), robust=True)


def _stored_values(calc):
    """Return what the row stores of the working out, for lists to read (TV-207)."""
    return {
        "watched_count": calc.watched_count,
        "aired_count": calc.aired_count if calc.aired_known else None,
        "up_next_episode": calc.up_next,
        "up_next_air_date": calc.up_next_air_date,
        "up_next_is_new": calc.up_next_is_new,
        "next_air_episode": calc.next_airing[0] if calc.next_airing else None,
        "next_air_date": calc.next_airing[1] if calc.next_airing else None,
        "first_gap_episode": calc.first_gap,
        "finished": calc.finished,
        "caught_up": calc.caught_up,
        "last_watched_on": calc.last_watched_on,
        "last_activity_on": calc.last_activity_on,
        "last_activity_at": calc.last_activity_at,
    }


def _start_date(current, before, after):
    """A row's start follows its first dated fact; a date from an import stays."""
    return after if current is None or current == before else current


def _end_date(current, before, after, status):
    """A finished row's end is its last dated fact; an import's date stays until a newer one."""
    if status != COMPLETED:
        return None
    if current is None or after not in (None, before):
        return after
    return None if after is None and current == before else current


def _record_start(ctx):
    """Record starting the title: a tick, Watching or a rewatch that put it in Watching.

    Resuming a paused title isn't starting it; starting again after finishing or dropping
    is (AN-703, activity contract AC-1). Imports and jobs start nothing.
    """
    from social.models import ProgressChange  # noqa: PLC0415

    if (
        ctx.may_start
        and ctx.entry_point in ACTIVITY_ENTRY_POINTS
        and ctx.row.status == WATCHING
        and ctx.status_before not in (WATCHING, PAUSED)
    ):
        ProgressChange.record_start(ctx.user, ctx.item)


def _forget_progress(ctx):
    """Delete the title's recorded starts and progress (AC-2)."""
    from social.models import ProgressChange  # noqa: PLC0415

    ProgressChange.objects.filter(actor=ctx.user, item=ctx.item).delete()


def _forget_starts_since(ctx, since):
    from social.models import ProgressChange  # noqa: PLC0415

    ProgressChange.objects.filter(
        actor=ctx.user,
        item=ctx.item,
        current_progress__kind=ProgressChange.START,
        created_at__gte=since,
    ).delete()


def _clear_opinions(ctx):
    """Clear the title's current stars and heart (GM-304); custom lists keep it."""
    from social.models import Activity  # noqa: PLC0415

    MediaLike.objects.filter(user=ctx.user, item=ctx.item).delete()
    Activity.objects.filter(
        actor=ctx.user,
        item=ctx.item,
        verb="rating_updated",
        target_type="tracking",
    ).delete()
    if ctx.row is not None and ctx.row.pk is not None:
        ctx.set_row(score=None, rating_source=None, like_source=None, like_is_independent=False)


def _leave_library(ctx):
    """Delete the row, its facts and its starts and progress; clear its opinions (TV-003)."""
    if ctx.row is None or ctx.row.pk is None:
        return
    _forget_progress(ctx)
    _clear_opinions(ctx)
    Anime.objects.filter(pk=ctx.row.pk).delete()
    ctx.row = None
    ctx.dirty.clear()
    ctx.changed = True


def _forget_worked_out_total(ctx):
    """A total a log or mark worked out on a manual title goes when that write is undone (AN-100).

    Nothing says it once no log and no mark that finishes a title is left. A number the
    owner wrote or a file gave is theirs, and stays.
    """
    if ctx.item.source != Sources.MANUAL.value or ctx.listing.total is None or ctx.row.total_is_written:
        return
    finishing_marks = AnimeMark.objects.filter(
        anime=ctx.row,
        origin__in=(AnimeMark.Origin.EYE, AnimeMark.Origin.OPINION),
    )
    if not ctx.facts.logs and not finishing_marks.exists():
        ctx.listing = replace(ctx.listing, total=None)
        ctx.invalidate()


def _after_removal(ctx, *, status_follows=False):
    """Apply TV-208 and TV-003 after an undo removed facts; return True when it left.

    status_follows keeps the row for a status written with the undo: what leaving clears
    goes, and the title starts again from that status (AN-900).
    """
    AnimeMark.objects.filter(anime=ctx.row, fills__isnull=True).delete()
    ctx.reload()
    _forget_worked_out_total(ctx)
    row = ctx.row
    recorded = bool(ctx.facts.viewings or ctx.facts.logs)
    if row.ended_choice and not row.choice:
        since = row.ended_choice_at
        remaining = since is None or any(viewing.created_at >= since for viewing in ctx.facts.viewings) or any(
            log.created_at >= since for log in ctx.facts.logs
        )
        if not remaining:
            ctx.set_row(choice=row.ended_choice, ended_choice="", ended_choice_at=None)
            # The undone write's start goes with it (activity contract AC-2).
            _forget_starts_since(ctx, since)
            if not recorded:
                _clear_opinions(ctx)
            ctx.invalidate()
            return False
    if not recorded and not row.choice:
        if not status_follows:
            _leave_library(ctx)
            return True
        _forget_progress(ctx)
        _clear_opinions(ctx)
        ctx.status_before = None
        ctx.count_before = 0
        ctx.ticks_before = 0
    ctx.invalidate()
    return False


# ---------------------------------------------------------------------------------
# The earlier-episodes question (TV-107) and the log screen's offer (AN-203)
# ---------------------------------------------------------------------------------


def _episode_range(first, last):
    return f"E{first}" if first == last else f"E{first}–E{last}"


def _is_first_write(ctx):
    """Return whether this is the title's first write, before it runs (TV-107)."""
    return (ctx.row is None or ctx.row.earlier_question_at is None) and not (ctx.facts.viewings or ctx.facts.logs)


def _earlier_question(ctx, target):
    """Return earlier_unwatched for the app's first tick, or None (TV-107)."""
    if ctx.entry_point != APP:
        return None
    calc = ctx.calc
    before = [number for number in calc.aired if number < target and number not in calc.watched_pass]
    if not before:
        return None
    return {
        "from": {"episode_number": before[0]},
        "to": {"episode_number": before[-1]},
        "count": len(before),
        "question": f"Also mark {_episode_range(before[0], before[-1])} watched?",
        "offer_log": True,
    }


def _log_in_pass(ctx):
    calc = ctx.calc
    return any(_in_pass(calc, log.pass_number) for log in ctx.facts.logs)


def _finish_offer(ctx, was_finished, day):
    """Return finished for an app tick that finished the title (AN-203), at most once a pass."""
    calc = ctx.calc
    if ctx.entry_point != APP or calc.single or was_finished or not calc.finished:
        return None
    if _log_in_pass(ctx) or ctx.row.log_offered_pass == ctx.row.pass_number:
        return None
    ctx.set_row(log_offered_pass=ctx.row.pass_number)
    target = _View.from_ctx(ctx).composer_target(date_override=day)
    return {
        "level": TITLE,
        "composer": {"default_target": target["id"], "targets": [target]},
    }


# ---------------------------------------------------------------------------------
# Ticks and unticks (AN-103, TV-100 to TV-102)
# ---------------------------------------------------------------------------------


def _plural_episodes(count):
    return f"{count} episode{'' if count == 1 else 's'}"


def _refuse_past_total(listing, number):
    """Refuse a number past a known total, naming it, or past any real title's (AN-103)."""
    if listing.cap is not None and number > listing.cap:
        raise TVInvalid(f"This anime has {_plural_episodes(listing.cap)}.")
    if number > MAX_EPISODE:
        raise TVInvalid(MSG_NO_NUMBER)


def _refuse_bad_number(number):
    if number < 1:
        raise TVInvalid(MSG_NO_NUMBER)


def _validate_tick(ctx, number):
    """Refuse a tick past a known total, or more than a day before its air time (AN-103)."""
    _refuse_bad_number(number)
    listing = ctx.listing
    _refuse_past_total(listing, number)
    calc = ctx.calc
    if number in calc.aired_set or number in calc.viewed_any or listing.may_have_aired:
        return
    air_at = listing.air_at(number)
    if air_at is None or air_at > ctx.now + AIR_TIME_SLACK:
        raise TVInvalid(MSG_EPISODE_UNAIRED)


def _tick_moment(ctx, at):
    """Return (watched_at, day) of a tick (TV-100): now, a webhook's time or a typed day."""
    if at is None or ctx.entry_point in API_ENTRY_POINTS:
        return ctx.now, user_calendar.day_of(ctx.now, ctx.user)
    if isinstance(at, datetime):
        day = user_calendar.day_of(at, ctx.user)
        if day > ctx.today + AIR_TIME_SLACK:
            raise TVInvalid(MSG_FUTURE_WATCH)
        return at, day
    day = at if isinstance(at, date) else _parse_log_day(at, ctx.user)
    if day > ctx.today:
        raise TVInvalid(MSG_FUTURE_WATCH)
    if day == ctx.today:
        return ctx.now, day
    return _start_of_day(day, ctx.user), day


def _do_tick(ctx, number, *, at=None):
    _validate_tick(ctx, number)
    if number in ctx.calc.watched_pass:
        return {"changed": False}
    watched_at, day = _tick_moment(ctx, at)
    first = _is_first_write(ctx)
    was_finished = ctx.calc.finished
    ctx.add_ticks([number], watched_on=day, watched_at=watched_at)
    ctx.end_choice()
    ctx.may_start = True
    ctx.reload()
    earlier = None
    if first:
        ctx.use_question()
        earlier = _earlier_question(ctx, number)
    finished = _finish_offer(ctx, was_finished, day)
    return {"changed": True, "finished": finished, "earlier_unwatched": earlier}


def tick(user, item, episode_number, *, at=None, entry_point=APP, mutation_id=None):
    """Record one viewing of an episode, dated now (TV-100, TV-101, AN-103).

    at dates a webhook watch (its event time) or a typed day; app ticks are dated now.
    Ticking an episode watched in the current pass records nothing.
    """
    number = int(episode_number)
    return _perform(
        user,
        item,
        "tick",
        lambda ctx: _do_tick(ctx, number, at=at),
        entry_point=entry_point,
        mutation_id=mutation_id,
        episode_number=number,
        rows_changed=False,
    )


def _untick_target(ctx, number, calc=None):
    """Return (viewing to remove, message when blocked) for an untick (TV-102)."""
    calc = calc or ctx.calc
    in_pass = [
        viewing
        for viewing in ctx.facts.viewings
        if viewing.episode == number and _in_pass(calc, viewing.pass_number)
    ]
    if not in_pass:
        return None, None
    if any(number in log.covers and _in_pass(calc, log.pass_number) for log in ctx.facts.logs):
        return None, MSG_DELETE_LOG
    unlogged = [viewing for viewing in in_pass if viewing.entry_id is None]
    if not unlogged:
        return None, MSG_DELETE_LOG
    return max(unlogged, key=lambda viewing: (viewing.created_at, viewing.id)), None


def _do_untick(ctx, number, *, viewing_id=None):
    if ctx.row is None:
        return {"changed": False}
    target, message = _untick_target(ctx, number)
    if message is not None:
        raise TVLogBlocks(message)
    if target is None or (viewing_id is not None and int(viewing_id) != target.id):
        return {"changed": False}
    ctx.delete_viewings([target])
    left = _after_removal(ctx)
    return {"changed": True, "left_library": left}


def untick(user, item, episode_number, *, viewing_id=None, entry_point=APP, mutation_id=None):
    """Remove the most recently recorded unlogged viewing in the current pass (TV-102).

    viewing_id names the viewing the app showed; if another would be removed now, nothing
    changes. A log covering the episode blocks it (TVLogBlocks).
    """
    number = int(episode_number)
    return _perform(
        user,
        item,
        "untick",
        lambda ctx: _do_untick(ctx, number, viewing_id=viewing_id),
        entry_point=entry_point,
        mutation_id=mutation_id,
        episode_number=number,
        rows_changed=False,
        listing=_listing_on_hand(item),
    )


# ---------------------------------------------------------------------------------
# Marks (AN-300, TV-108, TV-301, TV-302)
# ---------------------------------------------------------------------------------


def _nothing_aired_message(calc, today):
    if calc.premiere is None:
        return MSG_NOTHING_AIRED
    return f"Premieres {_short_date(calc.premiere, today)}"


def _do_mark(ctx, numbers, *, origin):
    """Mark numbers watched, undated, as one mark; end a choice (TV-300, TV-301)."""
    missing = [number for number in numbers if number not in ctx.calc.watched_pass]
    if not missing:
        return {"changed": False}
    ctx.use_question()
    ctx.add_fills(missing, origin=origin)
    ctx.end_choice()
    ctx.reload()
    return {"changed": True}


def _require_aired(ctx):
    """A log or mark of a title with nothing aired is refused (AN-103)."""
    if not ctx.calc.aired:
        raise TVInvalid(_nothing_aired_message(ctx.calc, ctx.today))


def _refuse_unknown_airing(ctx):
    """While nobody knows how many have aired, nothing can fill the rest (AN-100). Retryable.

    An import isn't a person waiting on an answer: it fills only what its source says.
    """
    if ctx.listing.awaiting_schedule and ctx.entry_point != IMPORT:
        raise AnimeDetailsUnavailable


def mark_watched(user, item, *, entry_point=APP, mutation_id=None):
    """The empty eye: mark every aired episode not watched, undated, as one mark (AN-300)."""

    def body(ctx):
        _refuse_unknown_airing(ctx)
        ctx.set_own_total()
        _require_aired(ctx)
        return _do_mark(ctx, ctx.calc.aired, origin=AnimeMark.Origin.EYE)

    return _perform(user, item, "mark_watched", body, entry_point=entry_point, mutation_id=mutation_id)


def _eye_case(ctx, calc=None):
    """Work through AN-300's cases. Returns a dict describing the tap.

    {"case": 1, "message"} | {"case": 2, "remove": [viewings], "count"} | {"case": 3,
    "remove": [viewings], "count", "blocked": message or None} | {"case": 0} when nothing
    is watched.
    """
    calc = calc or ctx.calc
    in_pass = [viewing for viewing in ctx.facts.viewings if _in_pass(calc, viewing.pass_number)]
    if not in_pass:
        return {"case": 0}
    if any(_in_pass(calc, log.pass_number) for log in ctx.facts.logs):
        return {"case": 1, "message": MSG_DELETE_LOG}
    mark_fills = [viewing for viewing in in_pass if viewing.is_mark_fill]
    if mark_fills:
        held_otherwise = {viewing.episode for viewing in in_pass if not viewing.is_mark_fill}
        unwatched = {viewing.episode for viewing in mark_fills} & calc.aired_set - held_otherwise
        if unwatched:
            return {"case": 2, "remove": mark_fills, "count": len(unwatched)}
    counted = [viewing for viewing in in_pass if viewing.episode in calc.aired_set]
    return {
        "case": 3,
        "remove": counted,
        "count": len({viewing.episode for viewing in counted}),
        "blocked": MSG_DELETE_LOG if ctx.facts.logs else None,
    }


def unmark(user, item, *, confirm=False, expected_version=None, entry_point=APP, mutation_id=None):
    """The filled eye, under AN-300's cases.

    1. A log in the pass blocks it. 2. Marks finished it: their fills go and ticks stay.
    3. Otherwise, with confirm (and a matching expected_version when given), every viewing
    in the pass goes; any log blocks it. Then TV-208 applies.
    """

    def body(ctx):
        if ctx.row is None:
            return {"changed": False}
        case = _eye_case(ctx)
        if case["case"] == 0:
            return {"changed": False}
        if case["case"] == 1:
            raise TVLogBlocks(case["message"])
        if case["case"] == 3:
            if case["blocked"]:
                raise TVLogBlocks(case["blocked"])
            if not confirm:
                raise TVInvalid(MSG_CONFIRM)
            if expected_version is not None and int(expected_version) != int(ctx.version_before):
                raise TVStateChanged(state_version=ctx.version_before, count=case["count"])
        ctx.delete_viewings(case["remove"])
        left = _after_removal(ctx)
        return {"changed": True, "left_library": left}

    return _perform(
        user,
        item,
        "unmark",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        listing=_listing_on_hand(item),
    )


def mark_up_to(user, item, episode_number, *, entry_point=APP, mutation_id=None):
    """Mark an aired episode and every unwatched one before it watched (TV-108)."""
    number = int(episode_number)

    def body(ctx):
        _refuse_bad_number(number)
        _refuse_past_total(ctx.listing, number)
        _refuse_unknown_airing(ctx)
        calc = ctx.calc
        if number not in calc.aired_set and not calc.may_have_aired:
            raise TVInvalid(MSG_MARK_UP_TO)
        numbers = [aired for aired in calc.aired if aired <= number]
        if calc.may_have_aired:
            numbers = list(range(1, number + 1))
        return _do_mark(ctx, numbers, origin=AnimeMark.Origin.UP_TO)

    return _perform(
        user,
        item,
        "mark_up_to",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        episode_number=number,
    )


def _episode_ref(ref):
    if ref is None:
        raise TVInvalid(MSG_NAME_EPISODES)
    try:
        return int(ref["episode_number"]) if isinstance(ref, dict) else int(ref)
    except (KeyError, TypeError, ValueError) as error:
        raise TVInvalid(MSG_NAME_EPISODES) from error


def mark_earlier(user, item, *, from_ref, to_ref, offer_log=False, entry_point=APP, mutation_id=None):
    """The Yes to the earlier-episodes question: mark the range's unwatched ones (TV-107).

    Marks only episodes in the range still unwatched and aired, as one mark. finished
    names the log screen when offer_log is set and the title is now finished.
    """
    first = _episode_ref(from_ref)
    last = _episode_ref(to_ref)
    if entry_point == APP and _mutation_uuid(mutation_id) is None:
        raise TVInvalid(MSG_MUTATION_REQUIRED)

    def body(ctx):
        numbers = [number for number in ctx.calc.aired if first <= number <= last]
        was_finished = ctx.calc.finished
        outcome = _do_mark(ctx, numbers, origin=AnimeMark.Origin.EARLIER)
        if offer_log and ctx.row is not None:
            days = [viewing.watched_on for viewing in ctx.facts.viewings if viewing.watched_on is not None]
            outcome["finished"] = _finish_offer(ctx, was_finished, max(days) if days else ctx.today)
        return outcome

    return _perform(user, item, "mark_earlier", body, entry_point=entry_point, mutation_id=mutation_id)


# ---------------------------------------------------------------------------------
# Choices and rewatch (TV-005 to TV-007, TV-203, AN-600)
# ---------------------------------------------------------------------------------


def _choice_unavailable(calc, row, status):
    """Return why a choice is unavailable, or None (TV-005, TV-006)."""
    choice = row.choice if row is not None else ""
    if calc.single and status != PLANNING:
        return MSG_SINGLE_CHOICE
    if status == WATCHING:
        if not calc.aired:
            return MSG_NOTHING_AIRED
        if calc.finished and choice not in (PAUSED, DROPPED):
            return MSG_ALL_WATCHED
        return None
    if status == PLANNING and calc.recorded:
        return _planning_refusal(calc)
    return None


def _planning_refusal(calc):
    """Return why a title with something recorded can't be Planning: a single can't be paused or dropped (AN-002)."""
    return MSG_SINGLE_WATCHED if calc.single else MSG_STARTED


def _do_choose(ctx, status, *, quiet=False):
    if status not in CHOICES:
        raise TVInvalid(f"Choose one of {', '.join(CHOICES)}.")
    reason = _choice_unavailable(ctx.calc, ctx.row, status)
    if reason is not None:
        if quiet:
            return {"changed": False}
        raise TVConflict(reason)
    ctx.ensure_row()
    row = ctx.row
    if row.choice == status and not row.ended_choice:
        return {"changed": False}
    values = {"choice": status, "choice_at": ctx.now, "ended_choice": "", "ended_choice_at": None}
    if row.choice == status:
        values.pop("choice_at")
    ctx.set_row(**values)
    ctx.changed = True
    ctx.may_start = status == WATCHING
    return {"changed": True}


def choose(user, item, status, *, entry_point=APP, mutation_id=None):
    """Choose Watching ("In progress"), Planning, Paused or Dropped (TV-005 to TV-007)."""
    return _perform(
        user,
        item,
        f"choose:{status}",
        lambda ctx: _do_choose(ctx, status),
        entry_point=entry_point,
        mutation_id=mutation_id,
        # A choice is a click: with a copy it doesn't wait on a provider, stale or not.
        listing=anime_listing.stored_listing(item),
    )


def start_rewatch(user, item, *, entry_point=APP, mutation_id=None):
    """Start a rewatch of a finished title: a new pass, recording nothing else (AN-600)."""

    def body(ctx):
        row = ctx.row
        if row is None or ctx.calc.single:
            raise TVConflict(MSG_FINISH_FIRST)
        if row.rewatch_started_at is not None:
            raise TVConflict(MSG_REWATCH_IN_PROGRESS)
        if not ctx.calc.finished:
            raise TVConflict(MSG_FINISH_FIRST)
        values = {"rewatch_started_at": ctx.now, "pass_number": row.pass_number + 1}
        if row.choice in HOLDING_CHOICES:
            values.update({"choice": "", "ended_choice": "", "ended_choice_at": None})
        ctx.set_row(**values)
        ctx.changed = True
        ctx.may_start = True
        return {"changed": True}

    return _perform(user, item, "rewatch", body, entry_point=entry_point, mutation_id=mutation_id)


def stop_rewatch(user, item, *, entry_point=APP, mutation_id=None):
    """End the rewatch in progress, keeping everything watched in it (AN-600)."""

    def body(ctx):
        if ctx.row is None or ctx.row.rewatch_started_at is None:
            raise TVConflict(MSG_NOT_REWATCHING)
        ctx.set_row(rewatch_started_at=None)
        ctx.changed = True
        return {"changed": True}

    return _perform(
        user,
        item,
        "stop_rewatch",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        listing=_listing_on_hand(item),
    )


def _remove_count(facts):
    return len({viewing.episode for viewing in facts.viewings})


def remove(user, item, *, expected_version=None, entry_point=APP, mutation_id=None):
    """Remove from Library (TV-003): every viewing, mark and choice; clears opinions.

    Unavailable while a log exists. The app sends the version its dialog showed; a stale
    one raises TVStateChanged with the new count.
    """
    if entry_point == APP and _mutation_uuid(mutation_id) is None:
        raise TVInvalid(MSG_MUTATION_REQUIRED)

    def body(ctx):
        if ctx.row is None:
            return {"changed": False}
        if ctx.facts.logs:
            raise TVLogBlocks(MSG_DELETE_LOGS)
        if expected_version is not None and int(expected_version) != int(ctx.version_before):
            raise TVStateChanged(state_version=ctx.version_before, count=_remove_count(ctx.facts))
        final_version = _version_after(ctx.row.state_version, ctx.now)
        _leave_library(ctx)
        return {
            "changed": True,
            "left_library": True,
            "record_mutation": True,
            "mutation_extra": {"state_version": final_version},
        }

    return _perform(
        user,
        item,
        "remove",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        listing=_listing_on_hand(item),
    )


# ---------------------------------------------------------------------------------
# Stars and the heart (AN-301)
# ---------------------------------------------------------------------------------


def _apply_opinion_action(ctx, *, unchanged):
    """Do what setting stars or a heart does now; return the mark's outcome (AN-301).

    unchanged is True when the value is already set, which refuses nothing. Imports never
    open anything, so on a partway title they only set the value (TV-800).
    """
    action = _opinion_action(ctx.calc)
    if action == OPINION_LOG and not unchanged and ctx.entry_point != IMPORT:
        raise TVLogFirst(MSG_LOG_FIRST)
    if action == OPINION_MARK:
        _refuse_unknown_airing(ctx)
        ctx.set_own_total()
        return _do_mark(ctx, ctx.calc.aired, origin=AnimeMark.Origin.OPINION)
    return {"changed": False}


def _do_set_rating(ctx, rating):
    calc = ctx.calc
    outcome = {"changed": False}
    if rating is not None:
        if not calc.aired:
            raise TVConflict(MSG_NOTHING_AIRED)
        outcome = _apply_opinion_action(ctx, unchanged=ctx.row is not None and ctx.row.score == rating)
        ctx.ensure_row()
    row = ctx.row
    if row is None:
        return outcome
    if (row.score, row.rating_source_id) != (rating, None):
        ctx.set_row(score=rating, rating_source=None)
        outcome["changed"] = True
        ctx.changed = True
    _set_rating_activity(ctx.user, row, rating)
    return outcome


def set_rating(user, item, rating, *, entry_point=APP, mutation_id=None):
    """Set or clear the title's stars (AN-301).

    rating is the 0-10 wire value (whole 1-10 from the app and API) or None. Not started,
    stars mark the title watched in the same step; partway they are refused (TVLogFirst),
    because the log screen sets them; finished or caught up they only save. Clearing is
    only that. Direct stars are independent of any log and create rating activity (SW-401).
    """
    rating = _validate_rating(rating, entry_point)
    return _perform(
        user,
        item,
        "rate",
        lambda ctx: _do_set_rating(ctx, rating),
        entry_point=entry_point,
        mutation_id=mutation_id,
        # Clearing needs no copy, so it works at any time.
        listing=_listing_on_hand(item) if rating is None else None,
    )


def _do_set_like(ctx, liked):
    outcome = {"changed": False}
    if liked:
        outcome = _apply_opinion_action(ctx, unchanged=ctx.facts.liked)
    if ctx.row is not None and ctx.row.pk is not None:
        ctx.set_row(like_source=None, like_is_independent=True)
    if _set_like_row(ctx.user, ctx.item.pk, liked):
        _audit_like(ctx.user, ctx.item.pk, liked)
        outcome["changed"] = True
        ctx.changed = True
    return outcome


def set_like(user, item, liked, *, entry_point=APP, mutation_id=None):
    """Turn the title's heart on or off (AN-301).

    Not started, a heart marks the title watched; partway it is refused (TVLogFirst);
    finished or caught up, or with nothing aired, it only saves. Turning it off is only
    that.
    """
    liked = bool(liked)
    return _perform(
        user,
        item,
        "like",
        lambda ctx: _do_set_like(ctx, liked),
        entry_point=entry_point,
        mutation_id=mutation_id,
        # Turning it off needs no copy, so it works at any time.
        listing=None if liked else _listing_on_hand(item),
    )


# ---------------------------------------------------------------------------------
# Written state: what installed builds and the web send (AN-900)
# ---------------------------------------------------------------------------------


def _first_open_row(calc):
    """Return the row a title with no numbers shows past its viewings (AN-101)."""
    number = max(calc.viewed_any, default=0) + 1
    return min(number, calc.cap) if calc.cap else number


def _next_unwatched(calc, count):
    """Return the next count numbers to tick: the first not watched, in order (AN-900)."""
    numbers = []
    candidate = 0
    while len(numbers) < count:
        candidate += 1
        if candidate not in calc.watched_pass:
            numbers.append(candidate)
    return numbers


def _do_write_progress(ctx, count, *, status_follows=False):
    """A progress number means ticks (AN-900): raising ticks, lowering removes."""
    if ctx.listing.total is not None:
        # A manual title's total goes up with the number its owner writes (AN-100).
        ctx.set_own_total(count)
    calc = ctx.calc
    # Before anything is built from the number (AN-103).
    if count < 0:
        raise TVInvalid(MSG_NO_NUMBER)
    _refuse_past_total(ctx.listing, count)
    current = calc.watched_count
    if count > current:
        ctx.ensure_row()
        first = _is_first_write(ctx)
        numbers = _next_unwatched(calc, count - current)
        for number in numbers:
            _validate_tick(ctx, number)
        watched_at, day = _tick_moment(ctx, None)
        ctx.add_ticks(numbers, watched_on=day, watched_at=watched_at)
        ctx.end_choice()
        ctx.may_start = True
        if first:
            ctx.use_question()
        ctx.reload()
        return {"changed": True}
    if count == current:
        return {"changed": False}
    if ctx.row is None:
        return {"changed": False}
    removed = []
    candidates = sorted(
        (
            viewing
            for viewing in ctx.facts.viewings
            if viewing.entry_id is None
            and _in_pass(calc, viewing.pass_number)
            and viewing.episode in calc.aired_set
            and not any(viewing.episode in log.covers and _in_pass(calc, log.pass_number) for log in ctx.facts.logs)
        ),
        key=lambda viewing: (viewing.created_at, viewing.id),
        reverse=True,
    )
    counts = {}
    for viewing in ctx.facts.viewings:
        if _in_pass(calc, viewing.pass_number):
            counts[viewing.episode] = counts.get(viewing.episode, 0) + 1
    to_remove = current - count
    for viewing in candidates:
        if to_remove == 0:
            break
        removed.append(viewing)
        counts[viewing.episode] -= 1
        if counts[viewing.episode] == 0:
            to_remove -= 1
    if to_remove:
        raise TVLogBlocks(MSG_DELETE_LOG)
    ctx.delete_viewings(removed)
    left = _after_removal(ctx, status_follows=status_follows)
    return {"changed": True, "left_library": left}


def _number_lowers(user, item, count):
    """Return whether a written number is below the row's: it only removes, as an untick does."""
    progress = Anime.objects.filter(user=user, item=item).values_list("progress", flat=True).first()
    return progress is not None and int(count) < progress


def write_progress(user, item, count, *, entry_point=APP, mutation_id=None):
    """Apply a progress number from the web or the API: it means ticks (AN-900)."""
    return _perform(
        user,
        item,
        "write_progress",
        lambda ctx: _do_write_progress(ctx, int(count)),
        entry_point=entry_point,
        mutation_id=mutation_id,
        # Lowering it needs no copy, so it works at any time.
        listing=_listing_on_hand(item) if _number_lowers(user, item, count) else None,
    )


def _quiet(function):
    try:
        return function()
    except TVLogFirst:
        # The refusal is the answer here too: the web shows it (AN-301).
        raise
    except (TVConflict, TVInvalid) as error:
        logger.info("Ignored an anime written value: %s", getattr(error, "message", error))
        return None


def apply_written_state(user, item, data, *, entry_point, mutation_id=None):
    """Apply a status, number, stars or notes written through a form or PUT/PATCH (AN-900).

    Completed means mark it watched, In progress means choose Watching, and Planning,
    Paused and Dropped mean that choice. A progress number means ticks. Dates are worked
    out, so they're ignored. From the API an unavailable action is TVConflict; from the
    web it is ignored and the worked-out status is kept (TV-207).
    """
    data = dict(data or {})
    strict = entry_point in API_ENTRY_POINTS
    status = data.get("status") or None
    if status is not None and status not in (*CHOICES, COMPLETED):
        if strict:
            raise TVInvalid(f"Unknown status {status!r}.")
        status = None
    has_rating = "rating" in data
    rating = _validate_rating(data.get("rating"), entry_point) if has_rating else None
    notes = data.get("notes")
    progress = data.get("progress")

    def body(ctx):  # noqa: C901, PLR0912
        outcome = {"changed": False}
        emptied = False

        def merge(result):
            if result:
                outcome["changed"] = outcome["changed"] or bool(result.get("changed"))
                if result.get("left_library"):
                    outcome["left_library"] = True

        if progress is not None and status != COMPLETED:
            merge(_do_write_progress(ctx, int(progress), status_follows=status in CHOICES))
            if outcome.get("left_library"):
                return outcome
            ctx.reload()
            emptied = outcome["changed"] and not ctx.calc.recorded
        if status == COMPLETED:
            _refuse_unknown_airing(ctx)
            ctx.set_own_total(int(progress) if progress else None)
            if not ctx.calc.aired:
                if strict:
                    raise TVConflict(MSG_NOTHING_AIRED)
            else:
                merge(_do_mark(ctx, ctx.calc.aired, origin=AnimeMark.Origin.EYE))
        elif status is not None:
            # A number sent with it can leave the status out of date: reaching the total
            # with In progress, say. The number stands, and that's no conflict.
            merge(_do_choose(ctx, status, quiet=not strict or progress is not None))
            if emptied and not ctx.row.choice:
                # It couldn't apply, as on a single, so nothing keeps the title here (TV-003).
                _leave_library(ctx)
                outcome["left_library"] = True
                return outcome
        if has_rating:
            merge(_do_set_rating(ctx, rating) if strict else _quiet(lambda: _do_set_rating(ctx, rating)))
        if notes is not None and ctx.row is not None and ctx.row.notes != notes:
            ctx.set_row(notes=notes)
            outcome["changed"] = True
            ctx.changed = True
        return outcome

    return _perform(
        user,
        item,
        "written_state",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        # Notes, clearing stars and lowering the number need no copy, so they work at any time.
        listing=(
            _listing_on_hand(item)
            if status is None and rating is None and (progress is None or _number_lowers(user, item, progress))
            else None
        ),
    )


# ---------------------------------------------------------------------------------
# Logs (AN-400, TV-400, TV-401, TV-404 to TV-407, TV-501)
# ---------------------------------------------------------------------------------


def _only_marks(ctx, calc):
    """Whether something is watched and every watched episode only by marks (TV-404)."""
    in_pass = [viewing for viewing in ctx.facts.viewings if _in_pass(calc, viewing.pass_number)]
    return bool(in_pass) and all(viewing.is_mark_fill for viewing in in_pass)


def _rewatch_default(ctx, calc=None):
    """Return the log screen's Rewatch default: TV-404's rule for a season log."""
    calc = calc or ctx.calc
    return calc.rewatching or bool(ctx.facts.logs) or _only_marks(ctx, calc)


def _own_viewing_of_single(ctx):
    """Return the viewing a single's first log takes as its own: one no log holds yet."""
    free = [viewing for viewing in ctx.facts.viewings if viewing.entry_id is None]
    return min(free, key=lambda viewing: (viewing.created_at, viewing.id)) if free else None


def _couple_to_log(ctx, entry):
    """Apply the shared new-log rating and heart source laws (TV-501, SW-101, SW-202)."""
    values = {"like_source": entry, "like_is_independent": False}
    if entry.rating is not None:
        values.update({"score": entry.rating, "rating_source": entry})
    ctx.set_row(**values)
    _set_like_row(ctx.user, ctx.item.pk, bool(entry.liked))


def _do_create_log(  # noqa: PLR0913
    ctx,
    *,
    day,
    rating,
    review,
    liked,
    is_rewatch,
    tags,
    review_title,
    contains_spoilers,
    visibility,
    emit_activity,
    update_current,
    import_source,
    import_source_id,
    import_source_order,
):
    _refuse_unknown_airing(ctx)
    ctx.set_own_total()
    if ctx.entry_point != IMPORT:
        _require_aired(ctx)
    if day > ctx.today:
        raise TVInvalid(MSG_FUTURE_LOG)
    ctx.use_question()
    calc = ctx.calc
    if is_rewatch is None:
        is_rewatch = _rewatch_default(ctx, calc)
    if liked is None:
        liked = ctx.facts.liked
    aired = list(calc.aired)
    log_pass = ctx.row.pass_number
    if ctx.entry_point == IMPORT and calc.rewatching and not is_rewatch:
        # A source's finish on a title it says is being rewatched is the pass before.
        log_pass -= 1
    snapshot = {"anime": {"v": STAMP_VERSION, "pass": log_pass, "covers": _ranges(aired)}}
    entry = DiaryEntry.objects.create(
        user=ctx.user,
        item=ctx.item,
        consumed_at=_carrier(day),
        rating=rating,
        review=review or "",
        liked=bool(liked),
        is_rewatch=bool(is_rewatch),
        review_title=review_title or "",
        contains_spoilers=bool(contains_spoilers),
        visibility=visibility or "public",
        progress_snapshot=snapshot,
        import_source=import_source or "",
        import_source_id=import_source_id or "",
        import_source_order=import_source_order,
    )
    single_weight._replace_tags(entry, tags or [])  # noqa: SLF001
    claimed = 0
    if ctx.entry_point == IMPORT:
        # An import's log dates the fills of the import's mark (AN-800). The mark isn't
        # kept with them: deleting this log takes them away.
        claimed = AnimeViewing.objects.filter(
            anime=ctx.row,
            mark__isnull=False,
            diary_entry__isnull=True,
            pass_number=log_pass,
        ).update(diary_entry=entry, mark=None)
        AnimeMark.objects.filter(anime=ctx.row, fills__isnull=True).delete()
        ctx.reload()
        calc = ctx.calc
    if log_pass == ctx.row.pass_number:
        if calc.single:
            # One log of a single is one watch of it (AN-002, SW-010). An import's log has
            # its own pass's fill, or a new one: an earlier pass's mark stays a mark.
            own = None if ctx.entry_point == IMPORT else _own_viewing_of_single(ctx)
            if own is not None:
                AnimeViewing.objects.filter(pk=own.id).update(diary_entry=entry)
            elif not claimed:
                ctx.add_fills([1], entry=entry)
        else:
            ctx.add_fills([number for number in aired if number not in calc.watched_pass], entry=entry)
    ctx.changed = True
    ctx.end_choice()
    if update_current:
        _couple_to_log(ctx, entry)
    if emit_activity:
        _create_diary_activity(entry)
    ctx.reload()
    return {"changed": True, "entry": entry}


def create_log(  # noqa: PLR0913
    user,
    item,
    *,
    consumed_at,
    rating=None,
    review="",
    liked=None,
    is_rewatch=None,
    tags=None,
    review_title="",
    contains_spoilers=False,
    visibility="public",
    emit_activity=True,
    update_current=True,
    import_source="",
    import_source_id="",
    import_source_order=None,
    entry_point=APP,
    mutation_id=None,
):
    """Save a log of the title: one entry, its fills, and the state they make (AN-400).

    consumed_at is a calendar date, or a datetime that converts to the user's calendar;
    a future one is TVInvalid. is_rewatch None takes TV-404's default; liked None takes
    the title's current heart. Posts one diary activity (AN-703). An import's date past the
    user's today becomes today: one date never aborts an import, and imported_day has
    already dropped one further out than a time zone (AN-800).
    """
    day = _parse_log_day(consumed_at, user)
    today = user_calendar.today(user)
    if day > today:
        if entry_point != IMPORT:
            raise TVInvalid(MSG_FUTURE_LOG)
        day = today
    rating = _validate_rating(rating, entry_point)
    return _perform(
        user,
        item,
        "log",
        lambda ctx: _do_create_log(
            ctx,
            day=day,
            rating=rating,
            review=review,
            liked=liked,
            is_rewatch=is_rewatch,
            tags=tags,
            review_title=review_title,
            contains_spoilers=contains_spoilers,
            visibility=visibility,
            emit_activity=emit_activity,
            update_current=update_current,
            import_source=import_source,
            import_source_id=import_source_id,
            import_source_order=import_source_order,
        ),
        entry_point=entry_point,
        mutation_id=mutation_id,
    )


def update_log(entry, data, *, tags=None, entry_point=APP):
    """Edit an anime log (TV-405, TV-407, TV-501). A new date moves no viewing."""
    data = dict(data or {})
    user = entry.user
    new_day = None
    if "consumed_at" in data:
        new_day = _parse_log_day(data["consumed_at"], user)
        if new_day > user_calendar.today(user):
            raise TVInvalid(MSG_FUTURE_LOG)
    if "rating" in data:
        data["rating"] = _validate_rating(data["rating"], entry_point)

    def body(ctx):  # noqa: C901
        locked = DiaryEntry.objects.select_for_update().get(pk=entry.pk)
        previous_rating = locked.rating
        previous_day = locked.consumed_at
        fields = []
        if new_day is not None and locked.consumed_at != _carrier(new_day):
            locked.consumed_at = _carrier(new_day)
            fields.append("consumed_at")
        for name in ("rating", "review", "review_title", "liked", "is_rewatch", "contains_spoilers", "visibility"):
            if name in data and getattr(locked, name) != data[name]:
                setattr(locked, name, data[name])
                fields.append(name)
        if fields:
            locked.save(update_fields=[*dict.fromkeys(fields), "updated_at"])
        if tags is not None:
            single_weight._replace_tags(locked, tags)  # noqa: SLF001
        row = ctx.row
        if row is not None:
            # TV-501: an edited source log updates the current value; a log given new
            # stars fills empty ones (SW-104, SW-108).
            if "rating" in fields and row.rating_source_id == locked.pk:
                ctx.set_row(score=locked.rating)
                if locked.rating is None:
                    ctx.set_row(rating_source=None)
            elif row.score is None and locked.rating is not None and locked.rating != previous_rating:
                ctx.set_row(score=locked.rating, rating_source=locked)
            if "liked" in fields and row.like_source_id == locked.pk:
                _set_like_row(user, ctx.item.pk, bool(locked.liked))
        _update_diary_activity(locked)
        _audit_diary(locked, "diary_updated")
        if "consumed_at" in fields:
            single_weight._queue_statistics(user.pk, previous_day)  # noqa: SLF001
        ctx.changed = bool(fields) or tags is not None
        return {"changed": ctx.changed, "entry": locked}

    return _perform(user, entry.item, "update_log", body, entry_point=entry_point, listing=_listing_on_hand(entry.item))


def _keeping_log(ctx, viewing, deleted_id, calc):
    """Return another log covering a fill's episode in its pass, which keeps it (TV-400)."""
    same_pass = _in_pass(calc, viewing.pass_number)
    for log in ctx.facts.logs:
        if log.id != deleted_id and viewing.episode in log.covers and _in_pass(calc, log.pass_number) == same_pass:
            return log
    return None


def _log_effect(ctx, log, calc=None):
    """Return what deleting a log does (TV-400, TV-406).

    (its fills that go, episodes the deletion would unmark, {viewing id: log id} for its
    fills another log keeps, viewings that go back to being a tick's or a mark's).
    """
    calc = calc or ctx.calc
    owned = [viewing for viewing in ctx.facts.viewings if viewing.entry_id == log.id]
    # Each episode's viewings outside this log, split by whether they are in the pass.
    held = {True: set(), False: set()}
    for viewing in ctx.facts.viewings:
        if viewing.entry_id != log.id:
            held[_in_pass(calc, viewing.pass_number)].add(viewing.episode)
    going = []
    unmarked = set()
    reassign = {}
    returned = []
    for viewing in owned:
        if viewing.kind == TICK or viewing.mark_id is not None:
            returned.append(viewing)
            continue
        same_pass = _in_pass(calc, viewing.pass_number)
        if viewing.episode in held[same_pass]:
            going.append(viewing)
            continue
        keeper = _keeping_log(ctx, viewing, log.id, calc)
        if keeper is not None:
            reassign[viewing.id] = keeper.id
            continue
        going.append(viewing)
        if same_pass:
            unmarked.add(viewing.episode)
    return going, unmarked, reassign, returned


def delete_log(user, entry, *, expected_version=None, entry_point=APP, mutation_id=None):
    """Delete an anime log: only its own fills go (AN-400, TV-406).

    An episode another log still covers stays watched as that log's fill (TV-400). Stars
    and heart sources are detached (SW-106, SW-203). When the deletion would unmark
    episodes and expected_version is stale, TVStateChanged. Then TV-208 applies.
    """
    if entry.user_id != user.pk:
        raise TVInvalid(MSG_NOT_YOURS)

    def body(ctx):
        log = next((log for log in ctx.facts.logs if log.id == entry.pk), None)
        if log is None:
            return {"changed": False}
        going, unmarked, reassign, returned = _log_effect(ctx, log)
        if unmarked and expected_version is not None and int(expected_version) != int(ctx.version_before):
            raise TVStateChanged(state_version=ctx.version_before, count=len(unmarked))
        if returned:
            AnimeViewing.objects.filter(pk__in=[viewing.id for viewing in returned]).update(diary_entry=None)
        if reassign:
            AnimeViewing.objects.bulk_update(
                [AnimeViewing(pk=viewing_id, diary_entry_id=keeper) for viewing_id, keeper in reassign.items()],
                ["diary_entry"],
            )
        ctx.delete_viewings(going)
        locked = DiaryEntry.objects.select_for_update().get(pk=entry.pk)
        row = ctx.row
        if row is not None:
            if row.rating_source_id == locked.pk:
                ctx.set_row(rating_source=None)
            if row.like_source_id == locked.pk:
                ctx.set_row(like_source=None, like_is_independent=True)
            ctx.flush_row()
        single_weight._delete_diary_activity(user, locked)  # noqa: SLF001
        _audit_diary(locked, "diary_deleted")
        consumed_at = locked.consumed_at
        locked.delete()
        single_weight._queue_statistics(user.pk, consumed_at)  # noqa: SLF001
        ctx.changed = True
        left = _after_removal(ctx) if ctx.row is not None else False
        return {"changed": True, "left_library": left}

    return _perform(
        user,
        entry.item,
        "delete_log",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        listing=_listing_on_hand(entry.item),
    )


def log_info(entry):
    """Return the diary payload's anime_log: what the log covers and would unmark.

    Reads stored rows only, never a provider (TV-207).
    """
    if not supports(entry.item):
        return None
    view = _View.load(entry.user, entry.item, _listing_on_hand(entry.item))
    log = next((log for log in view.facts.logs if log.id == entry.pk), None)
    if log is None:
        return None
    _, unmarked, _, _ = _log_effect(view, log, view.calc)
    return {
        "unmarks_count": len(unmarked),
        "covers_count": len(log.covers),
        "state_version": int(view.row.state_version) if view.in_library else 0,
    }


# ---------------------------------------------------------------------------------
# Webhooks (AN-801) and the calendar job (AN-202)
# ---------------------------------------------------------------------------------


def webhook_event(user, item, episode_number, *, event, at):
    """Apply a Plex, Jellyfin or Emby event (AN-801); never raises for a rejected event.

    "watched" ticks the episode at the event time, "unplayed" unticks it, and "play"
    counts as choosing Watching (nothing changes where Watching is unavailable).
    """
    try:
        if event == "watched":
            return tick(user, item, episode_number, at=at, entry_point=WEBHOOK)
        if event == "unplayed":
            return untick(user, item, episode_number, entry_point=WEBHOOK)
        if event == "play":
            return _perform(
                user,
                item,
                f"choose:{WATCHING}",
                lambda ctx: _do_choose(ctx, WATCHING, quiet=True),
                entry_point=WEBHOOK,
            )
        logger.warning("Ignored an unknown anime webhook event %r", event)
    except (TVConflict, TVInvalid, AnimeDetailsUnavailable) as error:
        logger.info(
            "Acknowledged a rejected anime webhook %s for %s E%s: %s",
            event,
            item,
            episode_number,
            getattr(error, "message", error),
        )
    return None


def _lock_and_reconcile(row_id, *, listing=None):
    """Work out one row with entry point JOB; return whether what lists show moved."""
    with transaction.atomic():
        row = (
            Anime.objects.select_for_update(of=("self",))
            .select_related("item", "user")
            .filter(pk=row_id)
            .order_by("pk")
            .first()
        )
        # A row never worked out waits for the user's first write (AN-902).
        if row is None or row.reconciled_at is None:
            return False
        if listing is None:
            listing = anime_listing.stored_listing(row.item)
            if listing is None:
                return False
        before = (row.status, row.up_next_episode, row.caught_up)
        ctx = _Ctx(row.user, row.item, listing, JOB, row)
        _reconcile(ctx)
        return before != (row.status, row.up_next_episode, row.caught_up)


def refresh_after_calendar(items):
    """Refresh these titles' listings and work out every user's row (the calendar job).

    This is how a new episode or a raised total reopens a title (AN-202). Choices never
    move (TV-203). Never raises for one row.
    """
    items = list(items)
    listings = anime_listing.refresh(items)
    for item in items:
        listing = listings.get(item.pk)
        if listing is None:
            continue
        for row_id in Anime.objects.filter(item=item).values_list("pk", flat=True):
            try:
                _lock_and_reconcile(row_id, listing=listing)
            except Exception:
                logger.exception("Could not work out anime row %s after the calendar refresh", row_id)


def refresh_due_titles(now=None):
    """Work out rows whose next episode may have aired, from stored copies (the hourly job).

    Selected up to the server's tomorrow and compared per user, because a user's calendar
    can be a day ahead of the server's. Returns how many rows moved (TV-206).
    """
    now = now or user_calendar.now()
    horizon = now.date() + timedelta(days=1)
    changed = 0
    rows = (
        Anime.objects.filter(next_air_date__lte=horizon, reconciled_at__isnull=False)
        .select_related("user")
        .order_by("next_air_date", "pk")
    )
    for row in rows:
        if row.next_air_date > user_calendar.today(row.user):
            continue
        try:
            changed += int(_lock_and_reconcile(row.pk))
        except Exception:
            logger.exception("Could not work out aired anime row %s", row.pk)
    return changed


# ---------------------------------------------------------------------------------
# Imports (AN-800)
# ---------------------------------------------------------------------------------


def imported_day(user, value):
    """Return a source's date as a day on the user's calendar, or None (AN-800).

    One day past their today becomes today, because that's a time zone. A date further out
    isn't real, so it counts as no date: Spine never invents one.
    """
    if not value:
        return None
    day = _parse_log_day(value, user)
    today = user_calendar.today(user)
    return None if day > today + timedelta(days=1) else min(day, today)


def _imported_date(user, value):
    """Return what a row stores of a source's date: as given, while it's a real one."""
    day = imported_day(user, value)
    if day is None:
        return None
    return value if day == _parse_log_day(value, user) else _carrier(day)


@transaction.atomic
def import_title(  # noqa: C901, PLR0912, PLR0913
    user,
    item,
    *,
    status,
    count=0,
    score=None,
    notes="",
    earlier=(),
    total=None,
    start_date=None,
    end_date=None,
    history_date=None,
):
    """Import one title as one tracking record, ending where the app would (AN-800).

    A count becomes undated fills as one mark, and Completed an undated mark of everything
    aired. Each entry of `earlier` is a finished earlier pass ({"count": n}); a pass under
    way after them is a rewatch. Planning, Paused and Dropped stay the choice; In progress
    means Watching. No provider is called, and no question is asked. Counts and marks are
    no progress and no start; a log the import goes on to make is a log (AC-1).
    Logs an overwrite kept get their fills back (TV-800). The source's dates stay on the
    row, and its one history row carries the source's date, as other imports' do.
    """
    if Anime.objects.filter(user=user, item=item).exists():
        return None
    count = _sane_number(count)
    total = _sane_number(total)
    earlier = [{"count": _sane_number(entry.get("count"))} for entry in earlier]
    start_date = _imported_date(user, start_date)
    end_date = _imported_date(user, end_date)
    listing = anime_listing.provisional_listing(item, total or _implied_total(status, count, earlier))
    rating = _validate_rating(score, IMPORT) if score not in (None, "", 0, "0", "0.0") else None
    ctx = _Ctx(user, item, listing, IMPORT, None)
    try:
        ctx.ensure_row()
    except IntegrityError:
        # A simultaneous import of the same file got here first.
        return None
    row = ctx.row
    now_aired = list(range(1, listing.aired_through(ctx.now) + 1))
    passes = len(earlier)
    numbers = []
    if status == COMPLETED:
        numbers = _completed_numbers(listing, ctx.now, count)
    elif count:
        numbers = list(range(1, int(count) + 1))
    kept_logs = ctx.facts.logs
    if (
        not passes
        and not listing.is_single
        and (status == WATCHING or (status in (PAUSED, DROPPED) and numbers))
        and any(log.covers.difference(numbers) for log in kept_logs)
    ):
        # A kept log means a pass was finished, and the source's place comes after it (TV-800).
        passes = max(log.pass_number for log in kept_logs)
    values = {"pass_number": passes + 1, "earlier_question_at": ctx.now}
    if item.source == Sources.MANUAL.value and listing.total:
        values["total_is_written"] = True
    # Planning isn't available once something's been logged (TV-006).
    if status in HOLDING_CHOICES and not (status == PLANNING and kept_logs):
        values.update({"choice": status, "choice_at": ctx.now})
        if status != PLANNING and passes and numbers:
            # A place after a finished pass is a rewatch paused or dropped: it keeps it (AN-600).
            values["rewatch_started_at"] = ctx.now
    elif status == WATCHING:
        if passes:
            values["rewatch_started_at"] = ctx.now
        elif not numbers:
            values.update({"choice": WATCHING, "choice_at": ctx.now})
    elif not status and rating is not None and not numbers:
        # An imported rating with no status and no history marks the title (AN-301).
        numbers = now_aired
    if notes:
        values["notes"] = notes
    if rating is not None:
        values["score"] = rating
    if start_date and status != PLANNING:
        values["start_date"] = start_date
    if end_date:
        values["end_date"] = end_date
    ctx.set_row(**values)

    def write_history():
        Anime.history.bulk_history_create([row], default_user=user, default_date=history_date or ctx.now)

    earlier_numbers = [
        list(range(1, (earlier_pass.get("count") or listing.cap or 0) + 1)) for earlier_pass in earlier
    ]
    if status == COMPLETED and not numbers and not any(earlier_numbers) and not ctx.facts.logs:
        # Nothing says how many it has: it stays on its shelf as a stored Completed,
        # worked out at the user's first write (AN-902).
        ctx.set_row(status=COMPLETED)
        ctx.flush_row()
        write_history()
        return row
    # Logs an overwrite kept get their fills back first; the source's numbers fill the
    # rest of each pass (TV-800).
    held = _rebuild_kept_logs(ctx)
    for offset, pass_numbers in enumerate(earlier_numbers, start=1):
        ctx.add_fills(
            [number for number in pass_numbers if number not in held[offset]],
            origin=AnimeMark.Origin.IMPORT,
            pass_number=offset,
        )
    ctx.add_fills(
        [number for number in numbers if number not in held[row.pass_number]],
        origin=AnimeMark.Origin.IMPORT,
    )
    # Nothing here counts against progress: the number arrives with the row.
    ctx.reload()
    ctx.ticks_before = None
    ctx.count_before = ctx.calc.watched_count
    _reconcile(ctx)
    write_history()
    return row


def _sane_number(value):
    """Return a source's number, or 0 when it's past any real title's: it isn't filled (AN-103)."""
    number = int(value or 0)
    if number > MAX_EPISODE:
        logger.warning("Ignored an imported anime number past the ceiling: %s", number)
        return 0
    return max(number, 0)


def _implied_total(status, count, earlier):
    """A finished source row with no total: its own number is all Spine has to go on."""
    if status == COMPLETED and count:
        return count
    return max((entry.get("count") or 0 for entry in earlier), default=None) or None


def _rebuild_kept_logs(ctx):
    """Give each kept log its fills again; return {pass: the episodes they hold} (TV-800).

    A log means a pass was finished, so it goes back in the pass it finished, and never in
    one the source says is still open.
    """
    row = ctx.row
    last_finished = row.pass_number - 1 if row.rewatch_started_at is not None else row.pass_number
    held = defaultdict(set)
    for log in ctx.facts.logs:
        log_pass = min(log.pass_number, last_finished)
        if log.stamped and log_pass != log.pass_number:
            snapshot = {**log.snapshot, "anime": {**log.snapshot["anime"], "pass": log_pass}}
            DiaryEntry.objects.filter(pk=log.id).update(progress_snapshot=snapshot)
        missing = sorted(log.covers - held[log_pass])
        if missing:
            ctx.add_fills(missing, entry=DiaryEntry(pk=log.id), pass_number=log_pass)
            held[log_pass].update(missing)
    return held


def clear_for_import(user, source, media_ids):
    """Overwrite replaces these titles' viewings, marks and choices; logs survive (AN-800)."""
    from social.models import Activity, ProgressChange  # noqa: PLC0415

    rows = Anime.objects.filter(user=user, item__source=source, item__media_id__in=media_ids)
    item_ids = list(rows.values_list("item_id", flat=True))
    rows.delete()
    Activity.objects.filter(actor=user, item_id__in=item_ids, verb="rating_updated", target_type="tracking").delete()
    ProgressChange.objects.filter(actor=user, item_id__in=item_ids).delete()


# ---------------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------------


def _row_numbers(calc, listing):
    """Return the episode numbers the rows list, in order and with no gaps (AN-101)."""
    if calc.single:
        return []
    numbers = [*calc.aired, *listing.known_unaired(calc.aired_set)]
    if calc.may_have_aired:
        # Spine can't say how many have aired, so every one up to the next can be ticked.
        numbers.append(_first_open_row(calc))
    return list(range(1, max(numbers) + 1)) if numbers else []


class _View:
    """Read-only state of one user's title for payloads."""

    def __init__(self, user, item, row, listing, facts, now, today):
        self.user = user
        self.item = item
        self.row = row
        self.listing = listing
        self.facts = facts
        self.now = now
        self.today = today
        self.calc = _calculate(facts, listing, now, row=row, user=user)
        self._history = None

    @classmethod
    def load(cls, user, item, listing):
        if listing is None:
            listing = Listing(item=item)
        row = Anime.objects.filter(user=user, item=item).first()
        listing = _with_own_total(listing, row)
        facts = _load_facts(user, item, row)
        now = user_calendar.now()
        numbers, choice = _stored_facts(row, listing, now)
        if numbers or choice:
            # What the first write will store: it shows the same now (TV-705, AN-902).
            watched = {viewing.episode for viewing in facts.viewings}
            virtual = [
                _Viewing(-number, number, FILL, None, None, row.pass_number, None, None, row.created_at)
                for number in numbers
                if number not in watched
            ]
            facts = replace(facts, viewings=[*facts.viewings, *virtual])
            if choice and not row.choice:
                row = copy.copy(row)
                row.choice = choice
                row.choice_at = now
        return cls(user, item, row, listing, facts, now, user_calendar.today(user))

    @classmethod
    def from_ctx(cls, ctx):
        view = cls.__new__(cls)
        view.user = ctx.user
        view.item = ctx.item
        view.row = ctx.row
        view.listing = ctx.listing
        view.facts = ctx.facts
        view.now = ctx.now
        view.today = ctx.today
        view.calc = ctx.calc
        view._history = None
        return view

    def history(self):
        if self._history is None:
            self._history = _calculate(
                self.facts,
                self.listing,
                self.now,
                row=self.row,
                user=self.user,
                ignore_rewatch=True,
            )
        return self._history

    @property
    def in_library(self):
        return self.row is not None and self.row.pk is not None

    def status(self):
        if not self.in_library:
            return None
        return self.calc.status or self.row.status

    def eye(self):
        calc = self.calc
        if not calc.aired:
            message = _nothing_aired_message(calc, self.today)
            return {"filled": False, "tap": "nothing_aired", "count": None, "prompt": None, "message": message}
        if not calc.all_watched:
            count = calc.aired_count - calc.watched_count
            return {"filled": False, "tap": "mark", "count": count, "prompt": None, "message": None}
        case = _eye_case(self, calc)
        if case["case"] == 1:
            return {"filled": True, "tap": "blocked", "count": None, "prompt": None, "message": case["message"]}
        if case["case"] == 2:
            return {"filled": True, "tap": "unmark_marks", "count": case["count"], "prompt": None, "message": None}
        if case.get("blocked"):
            return {"filled": True, "tap": "blocked", "count": None, "prompt": None, "message": case["blocked"]}
        count = case.get("count") or 0
        prompt = "Mark 1 episode unwatched?" if count == 1 else f"Mark all {count} episodes unwatched?"
        return {"filled": True, "tap": "unmark_confirm", "count": count, "prompt": prompt, "message": None}

    def composer_target(self, *, date_override=None):
        calc = self.calc
        available = bool(calc.aired)
        return {
            "id": TITLE,
            "level": TITLE,
            "title": self.item.title,
            "date": _iso(date_override or self.today),
            "rating": _api_rating(self.row.score) if self.in_library else None,
            "liked": bool(self.facts.liked),
            "is_rewatch": bool(_rewatch_default(self, calc)),
            "available": available,
            "reason": None if available else MSG_NOTHING_AIRED,
        }

    def composer(self):
        target = self.composer_target()
        return {"default_target": target["id"], "targets": [target]}

    def actions(self):
        """Return (available actions, reasons): the + menu (TV-005, AN-002, AN-600)."""
        calc = self.calc
        status = self.status()
        selected = SELECTED_ACTIONS.get(status)
        actions = []
        reasons = {}
        if not calc.single:
            if selected != "watching":
                reason = _choice_unavailable(calc, self.row, WATCHING)
                if reason is None:
                    actions.append("resume" if status == PAUSED else "watching")
                else:
                    reasons["watching"] = reason
            actions.extend(action for action in ("pause", "drop") if selected != action)
            if self.history().finished:
                if calc.rewatching:
                    reasons["rewatch"] = MSG_REWATCH_IN_PROGRESS
                else:
                    actions.append("rewatch")
            if calc.rewatching:
                actions.append("stop_rewatch")
        if selected != "planning":
            if calc.recorded:
                reasons["planning"] = _planning_refusal(calc)
            else:
                actions.append("planning")
        if calc.aired:
            actions.append("log")
        else:
            reasons["log"] = MSG_NOTHING_AIRED
        if self.in_library:
            if self.facts.logs:
                reasons["remove"] = MSG_DELETE_LOGS
            else:
                actions.append("remove")
        if calc.aired:
            actions.append("rate")
        else:
            reasons["rate"] = MSG_NOTHING_AIRED
        return actions, reasons

    def next_title(self):
        """Return "Next: ..." once finished, until the user finishes that one (AN-500)."""
        if not self.history().finished or not self.listing.next_title:
            return None
        return _next_title_payload(self.user, self.listing.next_title, self.today)

    def up_next_payload(self):
        calc = self.calc
        if calc.up_next is None:
            return None
        return {
            "episode_number": calc.up_next,
            "title": self.listing.title_of(calc.up_next),
            "image_url": self.listing.image_of(calc.up_next),
            "air_date": _iso(calc.up_next_air_date),
            "is_new": calc.up_next_is_new,
        }

    def state(self, *, detail=True):
        calc = self.calc
        row = self.row if self.in_library else None
        status = self.status()
        next_airing = None
        if calc.next_airing is not None:
            next_airing = {"episode_number": calc.next_airing[0], "air_date": _iso(calc.next_airing[1])}
        payload = {
            "version": int(row.state_version) if row is not None else 0,
            "in_library": row is not None,
            "status": status,
            "choice": (row.choice or None) if row is not None else None,
            "started": calc.started if row is not None else False,
            "finished": calc.finished,
            "caught_up": calc.caught_up,
            "single": calc.single,
            "total": self.listing.cap,
            "watched_count": calc.watched_count,
            "aired_count": calc.aired_count if calc.aired_known else None,
            "first_gap_episode": calc.first_gap,
            "up_next": self.up_next_payload(),
            "next_airing": next_airing,
            "premiere": {"air_date": _iso(calc.premiere)} if calc.premiere else None,
            "rewatch": (
                {"started_at": _iso(row.rewatch_started_at), "pass_number": row.pass_number}
                if row is not None and calc.rewatching
                else None
            ),
            "last_watched_on": _iso(calc.last_watched_on),
        }
        if not detail:
            return payload
        actions, reasons = self.actions()
        payload.update(
            {
                "selected_action": SELECTED_ACTIONS.get(status),
                "remove_count": _remove_count(self.facts),
                "rating_source_diary_entry_id": self._source_id(row, "rating_source_id"),
                "like_source_diary_entry_id": self._source_id(row, "like_source_id"),
                "like_is_independent": row.like_is_independent if row is not None else (True if self.facts.liked else None),
                "liked": bool(self.facts.liked),
                "eye": self.eye(),
                "available_actions": actions,
                "action_reasons": reasons,
                "opinion_action": _opinion_action(calc),
                "composer": self.composer(),
                "next_title": self.next_title(),
            },
        )
        return payload

    def _source_id(self, row, name):
        """Return the log a value came from, if it's still there (AN-901)."""
        source_id = getattr(row, name) if row is not None else None
        return source_id if any(log.id == source_id for log in self.facts.logs) else None

    def _row_index(self):
        """Return what rows need: each watched episode's latest unlogged viewing, and logs.

        One pass over what's loaded, so a thousand rows cost no more per row than ten.
        """
        calc = self.calc
        latest = {}
        logged = set()
        for viewing in self.facts.viewings:
            if not _in_pass(calc, viewing.pass_number):
                continue
            if viewing.entry_id is not None:
                logged.add(viewing.episode)
            else:
                current = latest.get(viewing.episode)
                if current is None or (viewing.created_at, viewing.id) >= (current.created_at, current.id):
                    latest[viewing.episode] = viewing
        for log in self.facts.logs:
            if _in_pass(calc, log.pass_number):
                logged.update(log.covers)
        return latest, logged

    def row_payload(self, number, index=None):
        """Return one row, leaving out what's empty, so a long list stays small (AN-101).

        n: number. t, i: its title and image, when AniList lists them. a: 1 once aired;
        d: an unaired row's air date; neither: "TBA". w: 1 when watched. u: the viewing an
        untick removes. l: 1 when a log covers it, so an untick says log_message.
        """
        calc = self.calc
        latest, logged = index or self._row_index()
        row = {"n": number}
        title = self.listing.title_of(number)
        if title:
            row["t"] = title
        image = self.listing.image_of(number)
        if image:
            row["i"] = image
        if number in calc.aired_set or calc.may_have_aired:
            row["a"] = 1
        else:
            air_at = self.listing.air_at(number)
            if air_at is not None:
                row["d"] = _iso(user_calendar.day_of(air_at, self.user))
        if number in calc.watched_pass:
            row["w"] = 1
            if number in logged:
                row["l"] = 1
            elif number in latest and latest[number].id > 0:
                row["u"] = latest[number].id
        return row

    def rows(self):
        """Return the rows payload: one query-free pass over what's loaded (AN-101)."""
        calc = self.calc
        numbers = _row_numbers(calc, self.listing)
        tickable = numbers if calc.may_have_aired else calc.aired
        first_unwatched = next((number for number in tickable if number not in calc.watched_pass), None)
        index = self._row_index()
        return {
            "count": len(numbers),
            "up_next": calc.up_next,
            "first_unwatched": first_unwatched,
            "log_message": MSG_DELETE_LOG,
            "episodes": [self.row_payload(number, index) for number in numbers],
        }


def _next_title_payload(user, stored, today):
    """Return the next title's payload, or None once the user has finished it (AN-500)."""
    media_id = stored.get("media_id")
    source = stored.get("source") or Sources.MAL.value
    if Anime.objects.filter(user=user, item__source=source, item__media_id=media_id, finished=True).exists():
        return None
    copy_of_next = _next_title_copy(stored)
    return {
        "source": source,
        "media_type": MediaTypes.ANIME.value,
        "media_id": media_id,
        "title": stored.get("display_title") or stored.get("title") or "",
        "image_url": stored.get("image"),
        "has_aired": _next_title_has_aired(stored, today, copy_of_next),
        # A single has no rows, so Home's sheet doesn't move on to it (AN-501).
        "single": copy_of_next.is_single if copy_of_next is not None else bool(stored.get("single")),
    }


def _next_title_copy(stored):
    """Return the next title's own stored copy as a listing, or None with none read."""
    from app.models import AnimeListing  # noqa: PLC0415

    row = (
        AnimeListing.objects.filter(
            item__source=stored.get("source") or Sources.MAL.value,
            item__media_type=MediaTypes.ANIME.value,
            item__media_id=stored.get("media_id"),
        )
        .select_related("item")
        .first()
    )
    if row is None or row.fetched_at is None:
        return None
    return anime_listing._listing_from_row(row)  # noqa: SLF001


def _next_title_has_aired(stored, today, copy_of_next):
    """Return whether the next title has an aired episode (AN-501), from what's stored."""
    if copy_of_next is not None and not copy_of_next.awaiting_schedule:
        return copy_of_next.aired_through(user_calendar.now()) > 0
    released = str(stored.get("release_date") or "")
    if not released:
        return False
    # A year or a month alone is only known to have aired once it has passed.
    if len(released) < len("YYYY-MM-DD"):
        return released < today.isoformat()[: len(released)]
    return released <= today.isoformat()


def state_payload(user, item, *, listing=None, detail=True):
    """Return the title's state for a page; reads the stored copy, never hanging."""
    if listing is None:
        listing = anime_listing.page_listing(item)
    return _View.load(user, item, listing).state(detail=detail)


def rows_payload(user, item, *, listing=None):
    """Return the title's rows (AN-101): no per-row query and no per-episode call."""
    if listing is None:
        listing = anime_listing.page_listing(item)
    return _View.load(user, item, listing).rows()


def _catch_up(view):
    """Store what this read worked out when the row has fallen behind its copy (TV-207).

    The calendar job does it for tracked titles, but a page can know first: a new episode,
    or an imported title's real numbers. Lists read the row, so it has to agree. Returns
    whether the row was worked out again; a failure here never fails the read.
    """
    row = view.row
    if row is None or row.reconciled_at is None or view.listing.fetched_at is None:
        return False
    calc = view.calc
    behind = (calc.status or row.status) != row.status or any(
        getattr(row, name) != value for name, value in _stored_values(calc).items()
    )
    if not behind:
        return False
    try:
        _lock_and_reconcile(row.pk, listing=view.listing)
    except Exception:
        logger.exception("Could not store the worked-out state of anime row %s", row.pk)
        return False
    return True


def page_payload(user, item, *, listing=None):
    """Return a page's state and rows from one read of the title's facts."""
    if listing is None:
        listing = anime_listing.page_listing(item)
    view = _View.load(user, item, listing)
    if _catch_up(view):
        view = _View.load(user, item, listing)
    return {"state": view.state(detail=True), "rows": view.rows()}


def write_envelope(result):
    """Return the write's response keys, as TV's envelope has them, for a WriteResult.

    `rows` is the whole list when a write changed more than the row it named, else null;
    `row` is the written episode's own row.
    """
    listing = result.listing or anime_listing.page_listing(result.item)
    view = _View.load(result.user, result.item, listing)
    number = result.episode_number
    return {
        "anime": view.state(detail=True),
        "row": view.row_payload(number) if number is not None and not view.calc.single else None,
        "rows": view.rows() if result.rows_changed else None,
        "changed": result.changed,
        "finished": result.finished,
        "earlier_unwatched": result.earlier_unwatched,
    }


def list_payload(row):
    """Return the list variant of the state from the row's stored values only (TV-207)."""
    if row is None:
        return {
            "version": 0,
            "in_library": False,
            "status": None,
            "choice": None,
            "started": False,
            "finished": False,
            "caught_up": False,
            "watched_count": None,
            "aired_count": None,
            "first_gap_episode": None,
            "up_next": None,
            "next_airing": None,
            "premiere": None,
            "rewatch": None,
            "last_watched_on": None,
        }
    worked_out = row.reconciled_at is not None
    title, image_url = getattr(row, "_anime_up_next_episode", (None, None))
    up_next = None
    if row.up_next_episode is not None:
        up_next = {
            "episode_number": row.up_next_episode,
            "title": title,
            "image_url": image_url,
            "air_date": _iso(row.up_next_air_date),
            "is_new": row.up_next_is_new,
        }
    next_airing = None
    if row.next_air_episode is not None:
        next_airing = {"episode_number": row.next_air_episode, "air_date": _iso(row.next_air_date)}
    return {
        "version": int(row.state_version),
        "in_library": True,
        "status": row.status,
        "choice": row.choice or None,
        "started": row.last_activity_at is not None if worked_out else row.status != PLANNING,
        "finished": row.finished if worked_out else row.status == COMPLETED,
        "caught_up": row.caught_up,
        "watched_count": row.watched_count if worked_out else row.progress,
        "aired_count": row.aired_count,
        "first_gap_episode": row.first_gap_episode,
        "up_next": up_next,
        "next_airing": next_airing,
        "premiere": (
            {"air_date": _iso(row.next_air_date)} if not row.aired_count and row.next_air_date is not None else None
        ),
        "rewatch": (
            {"started_at": _iso(row.rewatch_started_at), "pass_number": row.pass_number}
            if row.rewatch_started_at is not None
            else None
        ),
        "last_watched_on": _iso(row.last_watched_on),
    }


def prime_up_next_episodes(rows):
    """Set up next's title and image on rows from their stored listings, in one query."""
    from app.models import AnimeListing  # noqa: PLC0415

    anime_rows = [row for row in rows if isinstance(row, Anime) and row.up_next_episode is not None]
    if not anime_rows:
        return
    stored = dict(
        AnimeListing.objects.filter(item_id__in={row.item_id for row in anime_rows}).values_list("item_id", "rows"),
    )
    for row in anime_rows:
        for episode in stored.get(row.item_id) or ():
            if int(episode[0]) == row.up_next_episode:
                row._anime_up_next_episode = (episode[1] or None, episode[2] or None)  # noqa: SLF001
                break


__all__ = [
    "APP",
    "IMPORT",
    "JOB",
    "LEGACY_APP",
    "WEB",
    "WEBHOOK",
    "AnimeDetailsUnavailable",
    "WriteResult",
    "apply_written_state",
    "choose",
    "clear_for_import",
    "create_log",
    "delete_log",
    "import_title",
    "imported_day",
    "list_payload",
    "log_info",
    "mark_earlier",
    "mark_up_to",
    "mark_watched",
    "page_payload",
    "prime_up_next_episodes",
    "refresh_after_calendar",
    "refresh_due_titles",
    "remove",
    "rows_payload",
    "set_like",
    "set_rating",
    "start_rewatch",
    "state_payload",
    "stop_rewatch",
    "supports",
    "tick",
    "unmark",
    "untick",
    "update_log",
    "webhook_event",
    "write_envelope",
    "write_progress",
]
