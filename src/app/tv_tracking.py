"""TV tracking: the one place that records TV facts and works out everything else.

docs/tv-tracking-contract.md is the source of truth; law IDs (TV-xxx) are cited where
they decide behaviour.

Facts are what the user did:

- viewings: Episode rows. kind "tick" is a tap, a webhook watch or an imported play
  (a claimed tick keeps "tick" and gets diary_entry); kind "log" is an episode log's
  own added viewing; kind "fill" is an undated viewing a log (diary_entry set) or a
  mark (diary_entry null) added (TV-100, TV-301, TV-402);
- logs: DiaryEntry rows on the show, season or episode Items, stamped
  progress_snapshot["tv"]["v"] = 1, with a season or show log's coverage in
  progress_snapshot["tv"]["covers"] (TV-400);
- choices, a rewatch, the earlier-episodes question and season-finish offers, on the
  TV row (TV-005 to TV-007, TV-107, TV-403, TV-600);
- stars and hearts at each level (TV-500 to TV-502).

Everything else (status, counts, up next, caught up, next airing, last-watched dates)
is worked out from those facts, the provider's episode lists (app.tv_listing) and today
on the user's calendar (app.user_calendar), and stored on the TV and Season rows by
reconcile_show (TV-207).

Every write fetches its listing and creates the episode Items it needs before it opens
its transaction, then locks the user's TV row (TV-705). Model receivers in
app.tv_signals catch writes made outside this module (admin, the shell).
"""

import copy
import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

from app import single_weight, tv_listing, user_calendar
from app.models import (
    TV,
    DiaryEntry,
    Episode,
    Item,
    MediaLike,
    MediaTypes,
    Season,
    Sources,
    Status,
    TVListing,
    TVMutation,
)
from app.tv_listing import TVDetailsUnavailable

logger = logging.getLogger(__name__)

# Entry points (plan 2.2.3). APP is the new build; LEGACY_APP the installed builds'
# endpoints (no finished, no question shown, but the question is used up).
APP = "app"
LEGACY_APP = "legacy_app"
WEB = "web"
WEBHOOK = "webhook"
IMPORT = "import"
JOB = "job"
HOOK = "hook"
REPAIR = "repair"
ENTRY_POINTS = (APP, LEGACY_APP, WEB, WEBHOOK, IMPORT, JOB, HOOK, REPAIR)
# Entry points that work out a show stored before this contract (TV-801).
WORKING_OUT = (APP, LEGACY_APP, WEB, REPAIR)
# Entry points whose direct API calls get 400s and 409s rather than being ignored.
API_ENTRY_POINTS = (APP, LEGACY_APP)
# Entry points whose writes can start a show in activity (TV-703); imports and jobs can't.
ACTIVITY_ENTRY_POINTS = (APP, LEGACY_APP, WEB, WEBHOOK)

TV_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
TICK = Episode.Kind.TICK.value
LOG = Episode.Kind.LOG.value
FILL = Episode.Kind.FILL.value
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
SHOW = "show"
SEASON = "season"
EPISODE = "episode"

MSG_NOTHING_AIRED = "Nothing has aired yet."
MSG_EPISODE_UNAIRED = "This episode hasn't aired yet."
MSG_ALL_WATCHED = "You've watched everything that has aired."
MSG_STARTED = "You've started this show. Pause or drop it instead."
MSG_REWATCH_IN_PROGRESS = "Finish or stop your rewatch first."
MSG_FINISH_SHOW_FIRST = "Finish this show before rewatching it."
MSG_FINISH_SEASON_FIRST = "Finish this season before rewatching it."
MSG_NOT_REWATCHING = "You're not rewatching this show."
MSG_DELETE_SHOW_LOGS = "Delete this show's logs first."
MSG_DELETE_LOGS = "Delete the logs first."
MSG_DELETE_SHOW_LOG = "Delete your show log first."
MSG_DELETE_EPISODE_LOG = "Delete your log of this episode first."
MSG_FUTURE_LOG = "Log dates can't be in the future."
MSG_FUTURE_WATCH = "Watch dates can't be in the future."
MSG_STATE_CHANGED = "This show has changed. Check it and try again."
MSG_MUTATION_REUSED = "This request was already used for a different change."
MSG_MUTATION_REQUIRED = "This change needs a mutation ID."
MSG_RATING = "Rating must be a whole number from 1 to 10."
MSG_UNLISTED_SEASON = "This season isn't listed for this show."
MSG_CONFIRM = "Confirm marking these episodes unwatched."
MSG_NO_DATES = "Progress and dates are worked out from what you watched."
MSG_SEASON_CHOICE = "Pause, drop or plan the show instead."
MSG_NOT_TV = "This isn't a TV show, season or episode."
MSG_NOT_YOURS = "You can only change your own logs."

TV_WRITE_ONLY_FIELDS = (
    "progress",
    "start_date",
    "end_date",
    "progressed_on",
    "total_minutes",
    "percentage",
)

# True while this module writes, so the model receivers in app.tv_signals do nothing.
_WRITING: ContextVar[bool] = ContextVar("tv_tracking_writing", default=False)


# ---------------------------------------------------------------------------------
# Errors and results
# ---------------------------------------------------------------------------------


class TVInvalid(ValidationError):
    """An invalid TV request (API 400 validation_error).

    TV-106 rejections, TV-207 field and season-status rejections, TV-407 future log
    dates, TV-504 off-scale ratings and a reused mutation ID.
    """

    def __init__(self, message, code="invalid"):
        super().__init__(message, code=code)
        self.message = message

    def __reduce__(self):
        return (type(self), (self.message, self.code))


class TVConflict(Exception):
    """A TV action that isn't available now (API 409)."""

    default_code = "tv_action_unavailable"

    def __init__(self, message, code=None, extra=None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.extra = extra or {}

    def __reduce__(self):
        # Pickle round-trips (Django's parallel test runner pickles errors).
        return (_rebuild_conflict, (type(self), self.message, self.code, self.extra))


def _rebuild_conflict(cls, message, code, extra):
    error = Exception.__new__(cls)
    TVConflict.__init__(error, message, code, extra)
    return error


class TVLogBlocks(TVConflict):
    """An untick, eye, Remove or season delete that a log blocks (API 409)."""

    default_code = "tv_log_blocks"


class TVStateChanged(TVConflict):
    """A confirmed destructive action whose expected_version is stale (TV-705)."""

    default_code = "tv_state_changed"

    def __init__(self, message=MSG_STATE_CHANGED, *, state_version, count):
        super().__init__(
            message,
            extra={"state_version": int(state_version), "count": int(count)},
        )


@dataclass
class WriteResult:
    """What a TV write did, for the entry point to build its response."""

    user: object
    show_item: Item
    tv: TV | None
    changed: bool
    entry: DiaryEntry | None = None
    finished: dict | None = None
    earlier_unwatched: dict | None = None
    season_number: int | None = None
    episode_number: int | None = None
    replayed: bool = False
    # The listing the write used, so the caller can build payloads without a refetch.
    listing: object = None


@dataclass
class LegacyPlan:
    """What convert_legacy_logs and apply_legacy_rule would write (TV-801).

    plan_legacy builds it without writing, so a legacy show's page shows what its
    first write will store (TV-705).
    """

    log_days: dict = field(default_factory=dict)  # entry id -> calendar day
    snapshots: dict = field(default_factory=dict)  # entry id -> progress_snapshot
    shown_day_changes: list = field(default_factory=list)
    log_fills: list = field(default_factory=list)  # (entry id, season, episode)
    links: list = field(default_factory=list)  # (entry id, viewing id, new day or None)
    log_viewings: list = field(default_factory=list)  # (entry id, season, episode, day)
    mark_fills: list = field(default_factory=list)  # (season, episode)
    marks: list = field(default_factory=list)  # {"level", "season_number", "count"}
    choice: str | None = None

    @property
    def empty(self):
        return not (
            self.log_days
            or self.log_fills
            or self.links
            or self.log_viewings
            or self.mark_fills
            or self.choice
        )


def supports(value):
    """Return whether an item or media-type value is a TV show, season or episode."""
    return getattr(value, "media_type", value) in TV_TYPES


def is_writing():
    """Return whether this module is writing (the model receivers then do nothing)."""
    return _WRITING.get()


@contextmanager
def _writing():
    token = _WRITING.set(True)
    try:
        yield
    finally:
        _WRITING.reset(token)


# ---------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------


def _check_entry_point(entry_point):
    if entry_point not in ENTRY_POINTS:
        msg = f"Unknown TV entry point {entry_point!r}"
        raise ValueError(msg)


def _season_label(number):
    return tv_listing.season_label(number)


def _short_date(day, today):
    text = f"{day:%b} {day.day}"
    return text if day.year == today.year else f"{text}, {day.year}"


def _episode_range(first, last):
    """Return "S1 E1", "S1 E1–E9" or "S1 E1–S2 E10"."""
    if first == last:
        return f"S{first[0]} E{first[1]}"
    if first[0] == last[0]:
        return f"S{first[0]} E{first[1]}–E{last[1]}"
    return f"S{first[0]} E{first[1]}–S{last[0]} E{last[1]}"


def _real_id(value):
    """Return a stored viewing's id, or None for one a legacy plan would add."""
    return value if value is not None and value > 0 else None


def _iso(value):
    return value.isoformat() if value is not None else None


def _rating_string(value):
    if value is None:
        return None
    try:
        return str(Decimal(value).quantize(Decimal("0.1")))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)


def _version_after(previous, now):
    return max(int(previous or 0) + 1, int(now.timestamp() * 1000))


def _carrier(day):
    """Return the UTC-midnight carrier single-weight logs store (TV-407)."""
    return datetime.combine(day, time.min, tzinfo=UTC)


def _start_of_day(day, user):
    return datetime.combine(day, time.min, tzinfo=user_calendar.calendar_tz(user))


def _log_day_of(consumed_at, user):
    """Return a stored log's calendar day: a carrier keeps its day (TV-801)."""
    if single_weight.is_calendar_datetime(consumed_at):
        return consumed_at.date()
    return user_calendar.day_of(consumed_at, user)


def _parse_log_day(value, user):
    """Return the calendar day of a log date the caller sent (TV-407).

    A date or "YYYY-MM-DD" is a calendar day; a datetime (installed builds) converts
    to the user's calendar; a stored UTC-midnight carrier keeps its day.
    """
    if value is None or value == "":
        return user_calendar.today(user)
    if isinstance(value, datetime):
        return _log_day_of(value, user)
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        if len(text) == 10:
            parsed = parse_date(text)
            if parsed is not None:
                return parsed
        moment = parse_datetime(text)
    except ValueError:
        moment = None
    if moment is None:
        raise TVInvalid("Enter a valid date.")
    return _log_day_of(moment, user)


def _validate_rating(value, entry_point):
    """Return a TV rating on the 0-10 scale (TV-504), or None to clear."""
    if value is None or value == "":
        return None
    try:
        rating = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise TVInvalid(MSG_RATING) from error
    if entry_point in (IMPORT, REPAIR):
        if rating < 0 or rating > 10:  # noqa: PLR2004
            raise TVInvalid(MSG_RATING)
        return rating.quantize(Decimal("0.1"))
    if rating != rating.to_integral_value() or not 1 <= rating <= 10:  # noqa: PLR2004
        raise TVInvalid(MSG_RATING)
    return rating.quantize(Decimal("0.1"))


def _mutation_uuid(value):
    if value in (None, ""):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as error:
        raise TVInvalid("Enter a valid mutation ID.") from error


def _stamped(snapshot):
    return isinstance(snapshot, dict) and isinstance(snapshot.get("tv"), dict) and (
        snapshot["tv"].get("v") is not None
    )


def _family(show_item, prefix="item__"):
    return Q(
        **{
            f"{prefix}media_id": show_item.media_id,
            f"{prefix}source": show_item.source,
            f"{prefix}media_type__in": TV_TYPES,
        },
    )


def _resolve_show_item(item):
    if not supports(item):
        raise TVInvalid(MSG_NOT_TV)
    return tv_listing.show_item_for(item)


def _invalid_episode(error):
    return TVInvalid(error.message)


# ---------------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------------


@dataclass
class _Viewing:
    id: int | None
    season: int
    episode: int
    kind: str
    entry_id: int | None
    created_at: datetime
    watched_on: date | None
    end_date: datetime | None
    season_row_id: int | None = None
    item_id: int | None = None
    day: date | None = None

    @property
    def pos(self):
        return (self.season, self.episode)

    @property
    def is_mark_fill(self):
        return self.kind == FILL and self.entry_id is None


@dataclass
class _Log:
    id: int
    item_id: int
    level: str
    season: int | None
    episode: int | None
    consumed_at: datetime
    created_at: datetime
    rating: Decimal | None
    liked: bool
    is_rewatch: bool
    visibility: str
    snapshot: dict | None
    day: date
    stamped: bool
    covers: dict


@dataclass
class _Facts:
    seasons: dict
    viewings: list
    logs: list
    likes: set

    def liked(self, level, season=None, episode=None):
        return (level, season, episode) in self.likes


def _viewing_day(kind, watched_on, end_date, user):
    if watched_on is not None:
        return watched_on
    if kind != FILL and end_date is not None:
        return user_calendar.day_of(end_date, user)
    return None


def _covers_from_snapshot(snapshot):
    covers = {}
    raw = ((snapshot or {}).get("tv") or {}).get("covers") or {}
    for key, numbers in raw.items():
        try:
            covers[int(key)] = tuple(int(number) for number in numbers)
        except (TypeError, ValueError):
            continue
    return covers


def _load_facts(user, show_item, tv):
    seasons = {}
    viewings = []
    if tv is not None and tv.pk is not None:
        for season in Season.objects.filter(related_tv=tv).select_related("item"):
            seasons[season.item.season_number] = season
        rows = (
            Episode.objects.filter(related_season__related_tv=tv)
            .order_by("created_at", "id")
            .values_list(
                "id",
                "item__season_number",
                "item__episode_number",
                "kind",
                "diary_entry_id",
                "created_at",
                "watched_on",
                "end_date",
                "related_season_id",
                "item_id",
            )
        )
        for row in rows:
            viewing = _Viewing(*row)
            viewing.day = _viewing_day(viewing.kind, viewing.watched_on, viewing.end_date, user)
            viewings.append(viewing)
    logs = []
    log_rows = (
        DiaryEntry.objects.filter(_family(show_item), user=user)
        .order_by("created_at", "id")
        .values_list(
            "id",
            "item_id",
            "item__media_type",
            "item__season_number",
            "item__episode_number",
            "consumed_at",
            "created_at",
            "rating",
            "liked",
            "is_rewatch",
            "visibility",
            "progress_snapshot",
        )
    )
    for (
        entry_id,
        item_id,
        media_type,
        season_number,
        episode_number,
        consumed_at,
        created_at,
        rating,
        liked,
        is_rewatch,
        visibility,
        snapshot,
    ) in log_rows:
        level = {
            MediaTypes.TV.value: SHOW,
            MediaTypes.SEASON.value: SEASON,
            MediaTypes.EPISODE.value: EPISODE,
        }[media_type]
        logs.append(
            _Log(
                id=entry_id,
                item_id=item_id,
                level=level,
                season=season_number,
                episode=episode_number,
                consumed_at=consumed_at,
                created_at=created_at,
                rating=rating,
                liked=liked,
                is_rewatch=is_rewatch,
                visibility=visibility,
                snapshot=snapshot,
                day=_log_day_of(consumed_at, user),
                stamped=_stamped(snapshot),
                covers=_covers_from_snapshot(snapshot),
            ),
        )
    likes = set()
    for media_type, season_number, episode_number in MediaLike.objects.filter(
        _family(show_item),
        user=user,
    ).values_list("item__media_type", "item__season_number", "item__episode_number"):
        level = {
            MediaTypes.TV.value: SHOW,
            MediaTypes.SEASON.value: SEASON,
            MediaTypes.EPISODE.value: EPISODE,
        }[media_type]
        likes.add((level, season_number, episode_number))
    return _Facts(seasons=seasons, viewings=viewings, logs=logs, likes=likes)


# ---------------------------------------------------------------------------------
# Working out the state (TV-103 to TV-105, TV-200 to TV-202, TV-601, TV-602)
# ---------------------------------------------------------------------------------


@dataclass
class _Rewatch:
    started_at: datetime
    season: int | None

    def covers(self, season):
        return self.season is None or season == self.season


def _rewatch_of(tv):
    if tv is None or tv.rewatch_started_at is None:
        return None
    return _Rewatch(tv.rewatch_started_at, tv.rewatch_season_number)


def _in_pass(rewatch, season, created_at):
    """Return whether something recorded at created_at is in the current pass (TV-601).

    season is the season it belongs to, or None for the show level (show logs).
    """
    if rewatch is None:
        return True
    if rewatch.season is not None and season != rewatch.season:
        return True
    return created_at >= rewatch.started_at


@dataclass
class _SeasonCalc:
    number: int
    listed: bool
    loaded: bool
    episodes: tuple
    aired: list
    watched: set
    complete: bool
    finished: bool
    started: bool
    state: str
    first_gap: int | None
    next_air_date: date | None
    last_watched_on: date | None
    rewatching: bool
    viewings: list
    logs: list

    @property
    def is_specials(self):
        return self.number == 0

    @property
    def aired_count(self):
        return len(self.aired)

    @property
    def watched_count(self):
        return len(self.watched)

    @property
    def episode_count(self):
        return len(self.episodes)

    @property
    def all_watched(self):
        return bool(self.aired) and len(self.watched) == len(self.aired)


@dataclass
class _Calc:
    seasons: dict
    rewatch: _Rewatch | None
    unknown: bool
    regular: list
    regular_aired: list
    watched_pass: set
    viewed_any: set
    aired_count: int
    watched_count: int
    finished: bool
    all_watched: bool
    caught_up: bool
    next_airing: tuple | None
    premiere: tuple | None
    up_next: tuple | None
    up_next_air_date: date | None
    up_next_is_new: bool
    last_watched_on: date | None
    last_activity_on: date | None
    last_activity_at: datetime | None
    recorded: bool
    started: bool
    in_library: bool
    status: str | None
    rewatch_done: bool
    any_aired: bool

    def season(self, number):
        return self.seasons.get(number)


def _calculate(facts, listing, today, *, tv, user, ignore_rewatch=False):  # noqa: C901, PLR0912, PLR0915
    rewatch = None if ignore_rewatch else _rewatch_of(tv)
    viewed_any = {viewing.pos for viewing in facts.viewings}
    watched_pass = {
        viewing.pos
        for viewing in facts.viewings
        if _in_pass(rewatch, viewing.season, viewing.created_at)
    }
    numbers = set(facts.seasons)
    numbers.update(viewing.season for viewing in facts.viewings)
    numbers.update(log.season for log in facts.logs if log.season is not None)
    if listing is not None:
        numbers.update(listing.season_numbers)
    next_to_air = listing.next_episode_to_air if listing is not None else None
    seasons = {}
    for number in sorted(numbers):
        listed = listing is not None and number in listing.season_numbers
        loaded = listed and listing.is_loaded(number)
        episodes = tuple(listing.episodes.get(number, ())) if loaded else ()
        aired = [
            listed_episode.episode_number
            for listed_episode in episodes
            if listing.is_aired(
                number,
                listed_episode.episode_number,
                today=today,
                has_viewing=(number, listed_episode.episode_number) in viewed_any,
            )
        ]
        aired_set = set(aired)
        watched = {episode for episode in aired if (number, episode) in watched_pass}
        named_next = (
            next_to_air is not None
            and next_to_air[0] == number
            and not (next_to_air[1] is not None and next_to_air[1] in aired_set)
        )
        complete = loaded and len(aired) == len(episodes) and not named_next
        finished = bool(aired) and complete and len(watched) == len(aired)
        season_viewings = [viewing for viewing in facts.viewings if viewing.season == number]
        season_logs = [log for log in facts.logs if log.season == number]
        started = bool(season_viewings or season_logs)
        first_gap = None
        if aired and not finished and aired[-1] in watched and len(watched) < len(aired):
            first_gap = next(episode for episode in aired if episode not in watched)
        next_air_date = next(
            (
                listed_episode.air_date
                for listed_episode in episodes
                if listed_episode.episode_number not in aired_set
            ),
            None,
        )
        if next_air_date is None and named_next:
            next_air_date = next_to_air[2]
        days = [viewing.day for viewing in season_viewings if viewing.day is not None]
        days.extend(log.day for log in season_logs)
        if not loaded:
            state = "unknown"
        elif not aired:
            state = "unaired"
        elif finished:
            state = "watched"
        elif len(watched) == len(aired):
            state = "airing"
        elif started:
            state = "in_progress"
        else:
            state = "not_started"
        seasons[number] = _SeasonCalc(
            number=number,
            listed=listed,
            loaded=loaded,
            episodes=episodes,
            aired=aired,
            watched=watched,
            complete=complete,
            finished=finished,
            started=started,
            state=state,
            first_gap=first_gap,
            next_air_date=next_air_date,
            last_watched_on=max(days) if days else None,
            rewatching=rewatch is not None and rewatch.covers(number),
            viewings=season_viewings,
            logs=season_logs,
        )

    regular = [number for number in sorted(seasons) if number >= 1 and seasons[number].listed]
    unknown = listing is None or any(not seasons[number].loaded for number in seasons if seasons[number].listed)
    regular_aired = [(number, episode) for number in regular for episode in seasons[number].aired]
    aired_count = len(regular_aired)
    watched_count = sum(len(seasons[number].watched) for number in regular)
    finished = (
        not unknown
        and aired_count > 0
        and all(seasons[number].finished for number in regular if seasons[number].aired)
    )
    all_watched = aired_count > 0 and watched_count == aired_count
    next_airing = (
        listing.next_regular_airing(today=today, viewed=viewed_any) if listing is not None else None
    )
    more_coming = next_airing is not None or bool(listing is not None and listing.returning)
    caught_up = all_watched and (not finished or more_coming)
    premiere = listing.premiere(today=today) if listing is not None and aired_count == 0 else None
    any_aired = any(season.aired for season in seasons.values() if season.listed)

    # Up next (TV-105).
    regular_set = set(regular_aired)
    latest = None
    candidates = [
        viewing
        for viewing in facts.viewings
        if viewing.season >= 1
        and viewing.kind in (TICK, LOG)
        and viewing.pos in regular_set
        and _in_pass(rewatch, viewing.season, viewing.created_at)
    ]
    if candidates:
        latest = max(
            candidates,
            key=lambda viewing: (
                viewing.day is not None,
                viewing.day or date.min,
                viewing.created_at,
                viewing.pos,
            ),
        ).pos
    else:
        watched_regular = [position for position in regular_aired if position in watched_pass]
        if watched_regular:
            latest = watched_regular[-1]
    up_next = None
    if latest is not None:
        up_next = next(
            (position for position in regular_aired if position > latest and position not in watched_pass),
            None,
        )
    if up_next is None:
        up_next = next((position for position in regular_aired if position not in watched_pass), None)
    up_next_air_date = None
    if up_next is not None and listing is not None:
        listed_episode = listing.episode(*up_next)
        up_next_air_date = listed_episode.air_date if listed_episode else None

    # Dates (TV-000 dates, TV-701).
    dated = [viewing.day for viewing in facts.viewings if viewing.day is not None]
    dated.extend(log.day for log in facts.logs)
    last_watched_on = max(dated) if dated else None
    activity = [
        (viewing.day or user_calendar.day_of(viewing.created_at, user), viewing.created_at)
        for viewing in facts.viewings
        if viewing.kind == TICK
    ]
    activity.extend((log.day, log.created_at) for log in facts.logs)
    if not activity:
        activity = [
            (user_calendar.day_of(viewing.created_at, user), viewing.created_at)
            for viewing in facts.viewings
        ]
    if not activity and tv is not None and tv.choice == WATCHING and tv.choice_at is not None:
        activity = [(user_calendar.day_of(tv.choice_at, user), tv.choice_at)]
    last_activity_on, last_activity_at = max(activity) if activity else (None, None)
    seen_days = [
        viewing.day or user_calendar.day_of(viewing.created_at, user) for viewing in facts.viewings
    ]
    seen_days.extend(log.day for log in facts.logs)
    latest_seen = max(seen_days) if seen_days else None
    up_next_is_new = bool(
        up_next_air_date is not None and latest_seen is not None and up_next_air_date > latest_seen,
    )

    recorded = bool(facts.viewings or facts.logs)
    choice = tv.choice if tv is not None else ""
    started = recorded or choice == WATCHING
    in_library = tv is not None and tv.pk is not None and (recorded or bool(choice))
    if choice in HOLDING_CHOICES:
        status = choice
    elif rewatch is not None:
        status = WATCHING
    elif finished:
        status = COMPLETED
    elif started:
        status = WATCHING
    else:
        status = None
    if not in_library:
        status = None

    rewatch_done = False
    if rewatch is not None:
        if rewatch.season is None:
            rewatch_done = all_watched
        else:
            scoped = seasons.get(rewatch.season)
            rewatch_done = scoped is not None and scoped.all_watched

    return _Calc(
        seasons=seasons,
        rewatch=rewatch,
        unknown=unknown,
        regular=regular,
        regular_aired=regular_aired,
        watched_pass=watched_pass,
        viewed_any=viewed_any,
        aired_count=aired_count,
        watched_count=watched_count,
        finished=finished,
        all_watched=all_watched,
        caught_up=caught_up,
        next_airing=next_airing,
        premiere=premiere,
        up_next=up_next,
        up_next_air_date=up_next_air_date,
        up_next_is_new=up_next_is_new,
        last_watched_on=last_watched_on,
        last_activity_on=last_activity_on,
        last_activity_at=last_activity_at,
        recorded=recorded,
        started=started,
        in_library=in_library,
        status=status,
        rewatch_done=rewatch_done,
        any_aired=any_aired,
    )


def _season_status(calc, number, choice):
    """Return a started season's stored status (TV-200, TV-204), or None to leave it."""
    season = calc.seasons.get(number)
    if season is None or not season.started or not season.listed:
        return None
    if season.finished:
        return COMPLETED
    if choice in (PAUSED, DROPPED):
        return choice
    return WATCHING


# ---------------------------------------------------------------------------------
# Items (created before the lock, TV-705)
# ---------------------------------------------------------------------------------


def _queue_external_ratings(item_ids):
    """Queue external ratings for Items created in bulk (bulk_create sends no signal)."""
    if not item_ids:
        return
    from app.tasks import enqueue_external_rating_batches  # noqa: PLC0415

    ids = list(item_ids)
    transaction.on_commit(lambda: enqueue_external_rating_batches(ids), robust=True)


def _item_ids(show_item, listing, season_numbers, episode_keys):
    """Return {season: item id} and {(season, episode): item id}, creating missing Items.

    One select of the show's Items; missing ones are bulk-created from the listing with
    no provider call, then selected again. Never call it inside the write's lock.
    """
    seasons = {}
    episodes = {}
    for item_id, media_type, season_number, episode_number in Item.objects.filter(
        media_id=show_item.media_id,
        source=show_item.source,
        media_type__in=(MediaTypes.SEASON.value, MediaTypes.EPISODE.value),
    ).values_list("id", "media_type", "season_number", "episode_number"):
        if media_type == MediaTypes.SEASON.value:
            seasons[season_number] = item_id
        else:
            episodes[(season_number, episode_number)] = item_id
    season_numbers = set(season_numbers) | {season for season, _ in episode_keys}
    missing_seasons = sorted(number for number in season_numbers if number not in seasons)
    missing_episodes = sorted(key for key in episode_keys if key not in episodes)
    if not (missing_seasons or missing_episodes):
        return seasons, episodes
    if show_item.source == Sources.MANUAL.value:
        msg = f"Manual show {show_item} has no Item for {missing_seasons or missing_episodes}"
        raise TVInvalid(msg)
    new_items = [
        Item(
            media_id=show_item.media_id,
            source=show_item.source,
            media_type=MediaTypes.SEASON.value,
            season_number=number,
            title=show_item.title,
            image=show_item.image or settings.IMG_NONE,
        )
        for number in missing_seasons
    ]
    for season_number, episode_number in missing_episodes:
        listed = listing.episode(season_number, episode_number) if listing else None
        new_items.append(
            Item(
                media_id=show_item.media_id,
                source=show_item.source,
                media_type=MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode_number,
                title=(listed.title if listed and listed.title else show_item.title),
                image=(listed.image if listed and listed.image else settings.IMG_NONE),
            ),
        )
    Item.objects.bulk_create(new_items, ignore_conflicts=True)
    # Select the show's Items again rather than naming every key, so the query's size
    # doesn't grow with the episode count. On SQLite ignore_conflicts also swallows
    # CHECK failures, so this select is the check on both databases.
    created = []
    wanted_seasons = set(missing_seasons)
    wanted_episodes = set(missing_episodes)
    for item_id, media_type, season_number, episode_number in Item.objects.filter(
        media_id=show_item.media_id,
        source=show_item.source,
        media_type__in=(MediaTypes.SEASON.value, MediaTypes.EPISODE.value),
    ).values_list("id", "media_type", "season_number", "episode_number"):
        if media_type == MediaTypes.SEASON.value:
            if season_number in wanted_seasons:
                created.append(item_id)
            seasons[season_number] = item_id
        else:
            if (season_number, episode_number) in wanted_episodes:
                created.append(item_id)
            episodes[(season_number, episode_number)] = item_id
    still_missing = [number for number in missing_seasons if number not in seasons]
    still_missing.extend(key for key in missing_episodes if key not in episodes)
    if still_missing:
        msg = f"Could not create the Items of {show_item}: {still_missing}"
        raise RuntimeError(msg)
    _queue_external_ratings(created)
    return seasons, episodes


def _scope_keys(listing, today, scope):
    """Return the season numbers and episode keys a write in this scope may touch."""
    if listing is None:
        return set(), set()
    seasons = set()
    episodes = set()

    def add_season(number, *, regular_only=False):
        if number not in listing.season_numbers or (regular_only and number < 1):
            return
        seasons.add(number)
        for listed_episode in listing.episodes.get(number, ()):
            if listing.is_aired(number, listed_episode.episode_number, today=today):
                episodes.add((number, listed_episode.episode_number))

    for kind, *values in scope:
        if kind == "all":
            for number in listing.season_numbers:
                add_season(number)
        elif kind == "regular":
            for number in listing.regular_seasons():
                add_season(number)
        elif kind == SEASON:
            add_season(values[0])
        elif kind == EPISODE and listing.is_listed(values[0], values[1]):
            # Only an episode the provider lists: a tick, log or heart of an unlisted one
            # is rejected and must create no Items (TV-106).
            seasons.add(values[0])
            episodes.add((values[0], values[1]))
    return seasons, episodes


# ---------------------------------------------------------------------------------
# The write context
# ---------------------------------------------------------------------------------


class _Ctx:
    """One locked write: the user, the show, its listing, its TV row and facts."""

    def __init__(self, user, show_item, listing, entry_point, items, tv):
        self.user = user
        self.show_item = show_item
        self.listing = listing
        self.entry_point = entry_point
        self.now = user_calendar.now()
        self.today = user_calendar.today(user)
        self.season_items, self.episode_items = items
        self.tv = tv
        self.created = False
        self.changed = False
        self.tv_dirty = set()
        self.version_before = tv.state_version if tv is not None else 0
        self.status_before = tv.status if tv is not None else None
        self.touched_seasons = set()
        self.new_seasons = set()
        # {season number: (watched, aired)} before the write, for seasons never worked out.
        self.progress_baseline = {}
        # Each season's ticks and (item, aired) before the write: activity counts only
        # how ticks moved (TV-703). None outside a write: nothing counts.
        self.ticks_before = None
        self.season_rows_before = {}
        # Set by a tick, Watching or a rewatch, which can start the show (TV-703).
        self.may_start = False
        self.facts = None
        self._calc = None
        self.reload()

    # Facts

    def reload(self):
        self.facts = _load_facts(self.user, self.show_item, self.tv)
        self._calc = None

    @property
    def calc(self):
        if self._calc is None:
            self._calc = _calculate(
                self.facts,
                self.listing,
                self.today,
                tv=self.tv,
                user=self.user,
            )
        return self._calc

    def invalidate(self):
        self._calc = None

    @property
    def legacy(self):
        return self.tv is not None and self.tv.legacy_pending

    @property
    def derived(self):
        """Whether this write works out the derived state (TV-801 gating)."""
        return not self.legacy or self.entry_point in WORKING_OUT

    # Rows

    def ensure_tv(self):
        if self.tv is None:
            self.tv, self.created = _create_tv(self.user, self.show_item)
            self.version_before = self.tv.state_version
            self.status_before = None
            if self.tv.legacy_pending and self.entry_point in WORKING_OUT:
                _work_out_legacy(self)
            elif self.tv.legacy_pending and self.entry_point == WEBHOOK:
                _record_legacy_facts(self)
        return self.tv

    def set_tv(self, **values):
        for name, value in values.items():
            if name in ("rating_source", "like_source"):
                current = getattr(self.tv, f"{name}_id")
                wanted = value.pk if value is not None else None
            else:
                current = getattr(self.tv, name)
                wanted = value
            if current != wanted:
                setattr(self.tv, name, value)
                self.tv_dirty.add(name)
        self.invalidate()

    def season_row(self, number, *, status=WATCHING):
        row = self.facts.seasons.get(number)
        if row is None:
            self.ensure_tv()
            item_id = self.season_items.get(number)
            if item_id is None:
                self.season_items.update(_item_ids(self.show_item, self.listing, {number}, set())[0])
                item_id = self.season_items[number]
            row = Season(
                item_id=item_id,
                user=self.user,
                related_tv=self.tv,
                status=status,
            )
            row.save_base(force_insert=True)
            row = Season.objects.select_related("item").get(pk=row.pk)
            self.facts.seasons[number] = row
            self.new_seasons.add(number)
        return row

    def season_rows(self, numbers):
        missing = [number for number in sorted(set(numbers)) if number not in self.facts.seasons]
        if missing:
            self.ensure_tv()
            rows = [
                Season(
                    item_id=self.season_items[number],
                    user=self.user,
                    related_tv=self.tv,
                    status=WATCHING,
                )
                for number in missing
            ]
            bulk_create_with_history(rows, Season, default_user=self.user, default_date=self.now)
            for row in Season.objects.filter(related_tv=self.tv, item__season_number__in=missing).select_related("item"):
                self.facts.seasons[row.item.season_number] = row
            self.new_seasons.update(missing)
        return {number: self.facts.seasons[number] for number in numbers}

    def episode_item_id(self, season_number, episode_number):
        key = (season_number, episode_number)
        if key not in self.episode_items:
            raise RuntimeError(f"Episode Item {key} was not prepared before the lock")  # noqa: EM102, TRY003
        return self.episode_items[key]

    # Facts written

    def add_viewing(self, season_number, episode_number, *, kind, day=None, end_date=None, entry=None):
        season = self.season_row(season_number)
        viewing = Episode.objects.create(
            item_id=self.episode_item_id(season_number, episode_number),
            related_season=season,
            kind=kind,
            watched_on=day,
            end_date=end_date,
            diary_entry=entry,
        )
        self.changed = True
        return viewing

    def add_fills(self, positions, *, entry=None):
        positions = sorted(set(positions))
        if not positions:
            return 0
        rows = self.season_rows({season for season, _ in positions})
        Episode.objects.bulk_create(
            [
                Episode(
                    item_id=self.episode_item_id(season, episode),
                    related_season=rows[season],
                    kind=FILL,
                    diary_entry=entry,
                )
                for season, episode in positions
            ],
        )
        self.changed = True
        return len(positions)

    def delete_viewings(self, viewings):
        ids = [viewing.id for viewing in viewings if viewing.id is not None]
        if not ids:
            return 0
        _raw_delete(Episode.objects.filter(pk__in=ids))
        Episode.history.filter(id__in=ids).delete()
        self.touched_seasons.update(viewing.season for viewing in viewings)
        self.changed = True
        return len(ids)

    def end_choice(self):
        """A tick, log or mark ends a Planning, Paused or Dropped choice (TV-203)."""
        if self.tv is not None and self.tv.choice in HOLDING_CHOICES:
            self.set_tv(ended_choice=self.tv.choice, ended_choice_at=self.now, choice="")
            self.changed = True

    def flush_tv(self):
        if self.tv is not None and self.tv.pk is not None and self.tv_dirty:
            bulk_update_with_history(
                [self.tv],
                TV,
                fields=sorted(self.tv_dirty),
                default_user=self.user,
                default_date=self.now,
            )
            self.tv_dirty.clear()


def _raw_delete(queryset):
    return queryset._raw_delete(queryset.db)


def _create_tv(user, show_item):
    """Create the user's TV row (inside the lock). Return (tv, created)."""
    existing = TV.objects.select_for_update().filter(user=user, item=show_item).first()
    if existing is not None:
        return existing, False
    snapshots = DiaryEntry.objects.filter(_family(show_item), user=user).values_list(
        "progress_snapshot",
        flat=True,
    )
    legacy = any(not _stamped(snapshot) for snapshot in snapshots)
    seed = (
        TVMutation.objects.filter(user=user, show_item=show_item, action="remove")
        .order_by("-created_at", "-id")
        .values_list("result", flat=True)
        .first()
    )
    tv = TV(
        user=user,
        item=show_item,
        status=WATCHING,
        legacy_pending=legacy,
        state_version=int((seed or {}).get("state_version") or 0),
    )
    try:
        with transaction.atomic():
            tv.save_base(force_insert=True)
    except IntegrityError:
        return TV.objects.select_for_update().get(user=user, item=show_item), False
    return tv, True


def ensure_tv(user, show_item):
    """Return the user's TV row for a show, creating it (call inside the write's lock).

    A new row is legacy when the user already has a log of the show from before this
    contract (TV-801), so webhooks and imports can't skip its conversion.
    """
    with _writing():
        return _create_tv(user, show_item)[0]


# ---------------------------------------------------------------------------------
# Running a write
# ---------------------------------------------------------------------------------


def _listing_for(show_item, entry_point, *, allow_fetch=True):
    if entry_point in (JOB, HOOK) or not allow_fetch:
        listing = tv_listing.stored_listing(show_item)
        if listing is None:
            raise TVDetailsUnavailable
        return listing
    return tv_listing.get_listing(show_item)


def _needs_conversion(user, show_item):
    if TV.objects.filter(user=user, item=show_item, legacy_pending=True).exists():
        return True
    return any(
        not _stamped(snapshot)
        for snapshot in DiaryEntry.objects.filter(_family(show_item), user=user).values_list(
            "progress_snapshot",
            flat=True,
        )
    )


def _replay(user, show_item, mutation_id, action):
    record = TVMutation.objects.filter(user=user, mutation_id=mutation_id).first()
    if record is None:
        return None
    if record.action != action or record.show_item_id != show_item.pk:
        raise TVInvalid(MSG_MUTATION_REUSED)
    result = record.result or {}
    tv = TV.objects.filter(user=user, item=show_item).first()
    return WriteResult(
        user=user,
        show_item=show_item,
        tv=tv,
        changed=bool(result.get("changed")),
        entry=record.diary_entry,
        finished=result.get("finished"),
        earlier_unwatched=result.get("earlier_unwatched"),
        season_number=result.get("season_number"),
        episode_number=result.get("episode_number"),
        replayed=True,
    )


def _perform(  # noqa: PLR0913
    user,
    show_item,
    action,
    body,
    *,
    entry_point,
    mutation_id=None,
    listing=None,
    scope=(),
    require_listing=True,
    season_number=None,
    episode_number=None,
):
    """Run one write: listing and Items first, then the locked transaction (TV-705)."""
    _check_entry_point(entry_point)
    mutation_id = _mutation_uuid(mutation_id)
    if mutation_id is not None:
        replay = _replay(user, show_item, mutation_id, action)
        if replay is not None:
            replay.listing = listing
            return replay
    if listing is None and require_listing:
        listing = _listing_for(show_item, entry_point)
    today = user_calendar.today(user)
    if entry_point in (*WORKING_OUT, WEBHOOK) and listing is not None and _needs_conversion(user, show_item):
        scope = (*scope, ("all",))
    season_numbers, episode_keys = _scope_keys(listing, today, scope)
    items = _item_ids(show_item, listing, season_numbers, episode_keys)
    with _writing():
        for attempt in range(2):
            try:
                with transaction.atomic():
                    return _run_locked(
                        user,
                        show_item,
                        action,
                        body,
                        entry_point=entry_point,
                        mutation_id=mutation_id,
                        listing=listing,
                        items=items,
                        season_number=season_number,
                        episode_number=episode_number,
                    )
            except IntegrityError:
                if attempt:
                    raise
                if mutation_id is not None:
                    replay = _replay(user, show_item, mutation_id, action)
                    if replay is not None:
                        replay.listing = listing
                        return replay
                # A concurrent first write created the TV row; run again against it.
                continue
    return None  # pragma: no cover


def _run_locked(
    user,
    show_item,
    action,
    body,
    *,
    entry_point,
    mutation_id,
    listing,
    items,
    season_number,
    episode_number,
):
    tv = TV.objects.select_for_update().filter(user=user, item=show_item).first()
    if mutation_id is not None:
        replay = _replay(user, show_item, mutation_id, action)
        if replay is not None:
            replay.listing = listing
            return replay
    ctx = _Ctx(user, show_item, listing, entry_point, items, tv)
    if tv is not None and entry_point in WORKING_OUT:
        _work_out_legacy(ctx)
    elif tv is not None and tv.legacy_pending and entry_point == WEBHOOK:
        _record_legacy_facts(ctx)
    _take_progress_baseline(ctx)
    ctx.ticks_before = _ticks_by_season(ctx.facts)
    ctx.season_rows_before = {number: (row.item_id, row.aired_count) for number, row in ctx.facts.seasons.items()}
    outcome = body(ctx) or {}
    result = WriteResult(
        user=user,
        show_item=show_item,
        tv=None,
        changed=bool(outcome.get("changed", ctx.changed)),
        entry=outcome.get("entry"),
        finished=outcome.get("finished"),
        earlier_unwatched=outcome.get("earlier_unwatched"),
        season_number=outcome.get("season_number", season_number),
        episode_number=outcome.get("episode_number", episode_number),
        listing=listing,
    )
    if ctx.tv is not None and ctx.tv.pk is not None and not outcome.get("left_library"):
        if ctx.derived and listing is not None:
            _reconcile(ctx)
        else:
            ctx.flush_tv()
            if ctx.changed or ctx.created:
                TV.objects.filter(pk=ctx.tv.pk).update(
                    state_version=_version_after(ctx.tv.state_version, ctx.now),
                )
        ctx.tv.refresh_from_db()
        result.tv = ctx.tv
        _record_start(ctx)
    if mutation_id is not None or outcome.get("record_mutation"):
        TVMutation.objects.create(
            user=user,
            mutation_id=mutation_id or uuid.uuid4(),
            action=action,
            show_item=show_item,
            diary_entry=result.entry,
            result={
                "changed": result.changed,
                "season_number": result.season_number,
                "episode_number": result.episode_number,
                "finished": result.finished,
                "earlier_unwatched": result.earlier_unwatched,
                "diary_entry_id": result.entry.pk if result.entry is not None else None,
                **(outcome.get("mutation_extra") or {}),
            },
        )
    return result


# ---------------------------------------------------------------------------------
# Reconcile (TV-207)
# ---------------------------------------------------------------------------------


def _take_progress_baseline(ctx):
    """Remember the watched counts of seasons never worked out, before the write runs.

    A season row stored before this contract, or created just now by the legacy
    conversion, has no stored count to compare with. The reconcile compares against
    this instead, so the write that moves its count records a progress change for
    installed builds' Home (TV-703, TV-900), and the conversion itself records none.
    """
    if ctx.tv is None or ctx.listing is None or not ctx.derived:
        return
    pending = [number for number, row in ctx.facts.seasons.items() if row.is_started is None]
    if not pending:
        return
    calc = ctx.calc
    for number in pending:
        season = calc.seasons.get(number)
        if season is not None and season.loaded and season.listed:
            ctx.progress_baseline[number] = (season.watched_count, season.aired_count)
    ctx.new_seasons.difference_update(ctx.progress_baseline)
    ctx.invalidate()


def _ticks_by_season(facts):
    """Return {season number: tick viewings}, the viewings activity counts (TV-703)."""
    ticks = {}
    for viewing in facts.viewings:
        if viewing.kind == TICK:
            ticks[viewing.season] = ticks.get(viewing.season, 0) + 1
    return ticks


def _ticks_moved(ctx, number, ticks_after):
    """Return how a write moved a season's ticks; 0 outside a write (TV-703)."""
    if ctx.ticks_before is None:
        return 0
    return ticks_after.get(number, 0) - ctx.ticks_before.get(number, 0)


def _progress_change(ctx, item_id, *, previous, current):
    """Return an unsaved season ProgressChange; previous and current are (value, max)."""
    from social.models import ProgressChange  # noqa: PLC0415

    def snapshot(value, maximum):
        return {"kind": "episodes", "value": value, "max": maximum, "unit": "episode"}

    return ProgressChange(
        actor=ctx.user,
        item_id=item_id,
        previous_progress=snapshot(*previous),
        current_progress=snapshot(*current),
    )


def _record_start(ctx):
    """Record starting the show: a tick, Watching or a rewatch that put it in Watching.

    Resuming a paused show isn't starting it; starting again after finishing or
    dropping is (docs/activity-contract.md, AC-1). Imports and jobs start nothing.
    """
    from social.models import ProgressChange  # noqa: PLC0415

    if (
        ctx.may_start
        and ctx.entry_point in ACTIVITY_ENTRY_POINTS
        and ctx.tv.status == WATCHING
        and ctx.status_before not in (WATCHING, PAUSED)
    ):
        ProgressChange.record_start(ctx.user, ctx.show_item)


def _forget_progress(ctx, season_numbers=None):
    """Delete the show's recorded starts and progress, or only some seasons' (AC-2)."""
    from social.models import ProgressChange  # noqa: PLC0415

    rows = ProgressChange.objects.filter(
        actor=ctx.user,
        item__source=ctx.show_item.source,
        item__media_id=ctx.show_item.media_id,
    )
    if season_numbers is None:
        rows = rows.filter(item__media_type__in=(MediaTypes.TV.value, MediaTypes.SEASON.value))
    else:
        rows = rows.filter(item__media_type=MediaTypes.SEASON.value, item__season_number__in=season_numbers)
        if ctx.ticks_before is not None:
            for number in season_numbers:
                ctx.ticks_before.pop(number, None)
                ctx.season_rows_before.pop(number, None)
    rows.delete()


def _forget_starts_since(ctx, since):
    """Delete the show's starts recorded since `since`."""
    from social.models import ProgressChange  # noqa: PLC0415

    ProgressChange.objects.filter(
        actor=ctx.user,
        item=ctx.show_item,
        current_progress__kind=ProgressChange.START,
        created_at__gte=since,
    ).delete()


def _normalize_viewing_days(ctx):
    """Give non-fill viewings their calendar day when only end_date is stored."""
    updates = []
    for viewing in ctx.facts.viewings:
        if viewing.id is None or viewing.watched_on is not None or viewing.kind == FILL:
            continue
        if viewing.end_date is None:
            continue
        viewing.watched_on = user_calendar.day_of(viewing.end_date, ctx.user)
        viewing.day = viewing.watched_on
        updates.append(Episode(pk=viewing.id, watched_on=viewing.watched_on))
    if updates:
        Episode.objects.bulk_update(updates, ["watched_on"])


def _reconcile(ctx, *, fresh=False):  # noqa: C901, PLR0912, PLR0915
    """Work out and store the show's and each season's state from the facts (TV-207).

    Writes only changed values; bumps state_version when anything under the show
    changed; records one ProgressChange per season whose watched count changed, moved
    only by its ticks (TV-703); ends a finished rewatch (TV-602); never invents a date,
    deletes a fact or changes a choice; idempotent.
    """
    from social.models import ProgressChange  # noqa: PLC0415

    tv = ctx.tv
    if not fresh:
        ctx.reload()
    _normalize_viewing_days(ctx)
    calc = ctx.calc
    if ctx.entry_point == HOOK and ctx.listing is not None:
        invalid = sorted(
            {
                viewing.pos
                for viewing in ctx.facts.viewings
                if not ctx.listing.is_listed(*viewing.pos)
                and viewing.season in ctx.listing.season_numbers
            },
        )
        if invalid:
            logger.warning("Kept TV viewings of unlisted episodes %s of %s", invalid, ctx.show_item)
    if calc.unknown and ctx.listing is not None:
        missing = [number for number, season in calc.seasons.items() if season.listed and not season.loaded]
        raise TVDetailsUnavailable(season_numbers=missing)
    if calc.rewatch is not None and calc.rewatch_done:
        ctx.set_tv(rewatch_started_at=None, rewatch_season_number=None)
        calc = ctx.calc
    status = calc.status
    if status is not None and tv.status != status:
        ctx.set_tv(status=status)
    previous_status = ctx.status_before

    cache = {
        "watched_count": calc.watched_count,
        "aired_count": calc.aired_count,
        "up_next_season": calc.up_next[0] if calc.up_next else None,
        "up_next_episode": calc.up_next[1] if calc.up_next else None,
        "up_next_air_date": calc.up_next_air_date,
        "up_next_is_new": calc.up_next_is_new,
        "next_air_season": calc.next_airing[0] if calc.next_airing else None,
        "next_air_episode": calc.next_airing[1] if calc.next_airing else None,
        "next_air_date": calc.next_airing[2] if calc.next_airing else None,
        "finished": calc.finished,
        "caught_up": calc.caught_up,
        "last_watched_on": calc.last_watched_on,
        "last_activity_on": calc.last_activity_on,
        "last_activity_at": calc.last_activity_at,
    }
    cache_changes = {name: value for name, value in cache.items() if getattr(tv, name) != value}

    # Seasons.
    season_status_rows = []
    season_cache_rows = []
    season_cache_fields = set()
    progress_changes = []
    ticks_after = _ticks_by_season(ctx.facts)
    for number, row in ctx.facts.seasons.items():
        season = calc.seasons.get(number)
        if season is None:
            continue
        status_value = _season_status(calc, number, tv.choice)
        if status_value is not None and row.status != status_value:
            row.status = status_value
            season_status_rows.append(row)
        values = {
            "is_started": season.started,
            "watched_count": season.watched_count if season.loaded else None,
            "aired_count": season.aired_count if season.loaded else None,
            "episode_count": season.episode_count if season.loaded else None,
            "first_gap_episode": season.first_gap,
            "up_next_episode": calc.up_next[1] if calc.up_next and calc.up_next[0] == number else None,
            "next_air_date": season.next_air_date,
            "last_watched_on": season.last_watched_on,
        }
        previous_watched = row.watched_count
        previous_aired = row.aired_count
        was_worked_out = row.is_started is not None
        if not was_worked_out and number in ctx.progress_baseline:
            previous_watched, previous_aired = ctx.progress_baseline[number]
            was_worked_out = True
        changed_fields = [name for name, value in values.items() if getattr(row, name) != value]
        if changed_fields:
            for name in changed_fields:
                setattr(row, name, values[name])
            season_cache_rows.append(row)
            season_cache_fields.update(changed_fields)
        moved = _ticks_moved(ctx, number, ticks_after)
        if (
            season.loaded
            and season.listed
            and (was_worked_out or number in ctx.new_seasons)
            and ((previous_watched or 0) != season.watched_count or moved)
        ):
            # Only ticks move progress. Marks, fills, logs and rewatches record the new
            # count without moving it: installed builds' Home shows the count, and
            # activity shows nothing for them (TV-703, activity contract AC-1). Ticks
            # that moved without changing the count, such as unticking an episode a log
            # still covers, still count.
            progress_changes.append(
                _progress_change(
                    ctx,
                    row.item_id,
                    previous=(
                        season.watched_count - moved,
                        previous_aired if previous_aired is not None else season.aired_count,
                    ),
                    current=(season.watched_count, season.aired_count),
                ),
            )
    # A season an untick or unmark emptied is deleted; its lost ticks still count
    # against that day's progress (activity contract AC-2).
    for number, (item_id, aired) in ctx.season_rows_before.items():
        moved = _ticks_moved(ctx, number, ticks_after)
        if number not in ctx.facts.seasons and moved:
            progress_changes.append(_progress_change(ctx, item_id, previous=(-moved, aired), current=(0, aired)))
    if season_status_rows:
        bulk_update_with_history(
            season_status_rows,
            Season,
            fields=["status"],
            default_user=ctx.user,
            default_date=ctx.now,
        )
    if season_cache_rows:
        Season.objects.bulk_update(season_cache_rows, sorted(season_cache_fields))
    if progress_changes:
        ProgressChange.objects.bulk_create(progress_changes)

    anything = bool(
        ctx.changed
        or ctx.tv_dirty
        or cache_changes
        or season_status_rows
        or season_cache_rows
        or ctx.created,
    )
    ctx.flush_tv()
    if anything or tv.reconciled_at is None:
        cache_changes["reconciled_at"] = ctx.now
    if anything:
        cache_changes["state_version"] = _version_after(tv.state_version, ctx.now)
    if cache_changes:
        TV.objects.filter(pk=tv.pk).update(**cache_changes)
        for name, value in cache_changes.items():
            setattr(tv, name, value)
    if ctx.created or (previous_status in (PAUSED, DROPPED) and tv.status not in (PAUSED, DROPPED)):
        show_item = ctx.show_item
        transaction.on_commit(lambda: show_item.fetch_releases(delay=True), robust=True)


def reconcile_show(tv, *, entry_point, listing=None):
    """Work out and store a show's state (TV-207); call inside a transaction.

    The caller locks the TV row (ensure_tv does). A show stored before this contract is
    left alone for entry points that don't work it out (TV-801). listing defaults to the
    stored copy (no network); TVDetailsUnavailable when a needed season has none.
    """
    _check_entry_point(entry_point)
    if tv.legacy_pending and entry_point not in WORKING_OUT:
        return
    if listing is None:
        listing = tv_listing.stored_listing(tv.item)
        if listing is None:
            raise TVDetailsUnavailable
    with _writing():
        ctx = _Ctx(tv.user, tv.item, listing, entry_point, ({}, {}), tv)
        _reconcile(ctx, fresh=True)


def _lock_and_reconcile(tv_id, *, entry_point, listing=None):
    with _writing(), transaction.atomic():
        tv = TV.objects.select_for_update().select_related("item", "user").filter(pk=tv_id).first()
        if tv is None or (tv.legacy_pending and entry_point not in WORKING_OUT):
            return None
        before = (tv.status, tv.up_next_season, tv.up_next_episode, tv.caught_up)
        if listing is None:
            listing = tv_listing.stored_listing(tv.item)
            if listing is None:
                raise TVDetailsUnavailable
        ctx = _Ctx(tv.user, tv.item, listing, entry_point, ({}, {}), tv)
        _reconcile(ctx, fresh=True)
        return before != (tv.status, tv.up_next_season, tv.up_next_episode, tv.caught_up)


# ---------------------------------------------------------------------------------
# Legacy data (TV-801)
# ---------------------------------------------------------------------------------


def _last_completed_days(user, rows, model):
    """Return {row id: the calendar day the row was last set to Completed}."""
    ids = [row.pk for row in rows]
    if not ids:
        return {}
    history = model.history.filter(id__in=ids).order_by("id", "history_date", "history_id").values_list(
        "id",
        "status",
        "history_date",
    )
    days = {}
    previous = {}
    for row_id, status, history_date in history:
        if status == COMPLETED and previous.get(row_id) != COMPLETED:
            days[row_id] = user_calendar.day_of(history_date, user)
        previous[row_id] = status
    return days


def _plan(ctx_like, *, include_rule, exact_datetime_first=True, rebuild=False):  # noqa: C901, PLR0912, PLR0915
    """Plan legacy log conversion (and the legacy rule) without writing (TV-801).

    ctx_like has user, tv, listing, today, now and facts.
    """
    user = ctx_like.user
    tv = ctx_like.tv
    listing = ctx_like.listing
    facts = ctx_like.facts
    rewatch = _rewatch_of(tv)
    plan = LegacyPlan()
    viewed_any = {viewing.pos for viewing in facts.viewings}
    watched = {
        viewing.pos for viewing in facts.viewings if _in_pass(rewatch, viewing.season, viewing.created_at)
    }
    linked = {viewing.id for viewing in facts.viewings if viewing.entry_id is not None}
    owners = {viewing.entry_id for viewing in facts.viewings if viewing.entry_id is not None}

    def aired_by(numbers, day):
        positions = []
        if listing is None:
            return positions
        for number in numbers:
            if number not in listing.season_numbers:
                continue
            for listed_episode in listing.episodes.get(number, ()):
                position = (number, listed_episode.episode_number)
                if listing.is_aired(number, listed_episode.episode_number, today=day, has_viewing=position in viewed_any):
                    positions.append(position)
        return positions

    if rebuild:
        targets = [log for log in facts.logs if log.stamped]
    else:
        targets = [log for log in facts.logs if not log.stamped]
    for log in targets:
        if rebuild:
            day = log.day
        else:
            day = _log_day_of(log.consumed_at, user)
            shown = timezone.localtime(log.consumed_at).date()
            plan.log_days[log.id] = day
            if shown != day:
                plan.shown_day_changes.append(
                    {"entry_id": log.id, "before": shown.isoformat(), "after": day.isoformat()},
                )
        if log.level in (SEASON, SHOW):
            if rebuild:
                covered = [
                    (number, episode) for number, episodes in sorted(log.covers.items()) for episode in episodes
                ]
            else:
                saved_day = user_calendar.day_of(log.created_at, user)
                if log.level == SHOW:
                    numbers = listing.regular_seasons() if listing else []
                else:
                    numbers = [log.season]
                covered = aired_by(numbers, saved_day)
                covers = {}
                for number, episode in covered:
                    covers.setdefault(str(number), []).append(episode)
                plan.snapshots[log.id] = {
                    **(log.snapshot or {}),
                    "tv": {"v": STAMP_VERSION, "covers": covers},
                }
            for position in covered:
                if position not in watched:
                    plan.log_fills.append((log.id, *position))
                    watched.add(position)
            continue
        # An episode log: link it to one viewing (TV-801, TV-800).
        if not rebuild:
            plan.snapshots[log.id] = {**(log.snapshot or {}), "tv": {"v": STAMP_VERSION}}
        if log.id in owners:
            continue
        position = (log.season, log.episode)
        candidates = [
            viewing
            for viewing in facts.viewings
            if viewing.pos == position
            and viewing.entry_id is None
            and viewing.kind != FILL
            and viewing.id not in linked
        ]
        chosen = None
        if exact_datetime_first:
            exact = [viewing for viewing in candidates if viewing.end_date == log.consumed_at]
            if exact:
                chosen = exact[-1]
        if chosen is None:
            same_day = [viewing for viewing in candidates if viewing.day == day]
            if same_day:
                chosen = same_day[-1]
        if chosen is not None:
            linked.add(chosen.id)
            plan.links.append((log.id, chosen.id, day if chosen.day != day else None))
        else:
            plan.log_viewings.append((log.id, log.season, log.episode, day))
        watched.add(position)
        viewed_any.add(position)

    if include_rule and tv is not None:
        started_rows = {
            number
            for number, row in facts.seasons.items()
            if any(viewing.season == number for viewing in facts.viewings)
            or any(log.season == number for log in facts.logs)
        }
        completed_rows = [row for number, row in facts.seasons.items() if row.status == COMPLETED]
        season_days = _last_completed_days(user, completed_rows, Season)
        for row in sorted(completed_rows, key=lambda value: value.item.season_number):
            number = row.item.season_number
            as_of = season_days.get(row.pk, ctx_like.today)
            positions = [position for position in aired_by([number], as_of) if position not in watched]
            if positions:
                plan.mark_fills.extend(positions)
                watched.update(positions)
                plan.marks.append({"level": SEASON, "season_number": number, "count": len(positions)})
        if tv.status == COMPLETED and not started_rows:
            as_of = _last_completed_days(user, [tv], TV).get(tv.pk, ctx_like.today)
            numbers = listing.regular_seasons() if listing else []
            positions = [position for position in aired_by(numbers, as_of) if position not in watched]
            if positions:
                plan.mark_fills.extend(positions)
                watched.update(positions)
                plan.marks.append({"level": SHOW, "season_number": None, "count": len(positions)})
        # A stored status a webhook already recorded as the choice, and its watch then
        # ended, stays ended (ended_choice is set only by a write that ended a choice).
        if not tv.choice and not tv.ended_choice:
            recorded = bool(facts.viewings or facts.logs or plan.mark_fills)
            if tv.status in HOLDING_CHOICES:
                plan.choice = tv.status
            elif tv.status == WATCHING and not recorded:
                plan.choice = WATCHING
    return plan


def _apply_plan(ctx, plan):
    """Write a LegacyPlan's conversions, fills, links and marks."""
    if plan.log_days or plan.snapshots:
        entries = []
        for entry_id in sorted(set(plan.log_days) | set(plan.snapshots)):
            entry = DiaryEntry(pk=entry_id)
            fields = []
            if entry_id in plan.log_days:
                entry.consumed_at = _carrier(plan.log_days[entry_id])
                fields.append("consumed_at")
            if entry_id in plan.snapshots:
                entry.progress_snapshot = plan.snapshots[entry_id]
                fields.append("progress_snapshot")
            entries.append((entry, tuple(fields)))
        by_fields = {}
        for entry, fields in entries:
            by_fields.setdefault(fields, []).append(entry)
        for fields, group in by_fields.items():
            DiaryEntry.objects.bulk_update(group, list(fields))
    if plan.links:
        updates = []
        for entry_id, viewing_id, new_day in plan.links:
            viewing = Episode(pk=viewing_id, diary_entry_id=entry_id)
            fields = ["diary_entry"]
            if new_day is not None:
                viewing.watched_on = new_day
                fields.append("watched_on")
            updates.append((viewing, tuple(fields)))
        by_fields = {}
        for viewing, fields in updates:
            by_fields.setdefault(fields, []).append(viewing)
        for fields, group in by_fields.items():
            Episode.objects.bulk_update(group, list(fields))
        ctx.changed = True
    positions = {(season, episode) for _, season, episode in plan.log_fills}
    positions.update((season, episode) for _, season, episode, _ in plan.log_viewings)
    positions.update(plan.mark_fills)
    if positions:
        rows = ctx.season_rows({season for season, _ in positions})
        new = [
            Episode(
                item_id=ctx.episode_item_id(season, episode),
                related_season=rows[season],
                kind=FILL,
                diary_entry_id=entry_id,
            )
            for entry_id, season, episode in plan.log_fills
        ]
        new.extend(
            Episode(
                item_id=ctx.episode_item_id(season, episode),
                related_season=rows[season],
                kind=LOG,
                diary_entry_id=entry_id,
                watched_on=day,
                end_date=_start_of_day(day, ctx.user),
            )
            for entry_id, season, episode, day in plan.log_viewings
        )
        new.extend(
            Episode(
                item_id=ctx.episode_item_id(season, episode),
                related_season=rows[season],
                kind=FILL,
            )
            for season, episode in plan.mark_fills
        )
        Episode.objects.bulk_create(new)
        ctx.changed = True
    if plan.choice:
        ctx.set_tv(choice=plan.choice, choice_at=ctx.now)
        ctx.changed = True
    if not plan.empty:
        ctx.reload()


def _work_out_legacy(ctx):
    """The first write that works a show out: convert, apply the rule, clear the flag."""
    if ctx.tv is None:
        return None
    if ctx.tv.legacy_pending:
        plan = _plan(ctx, include_rule=True)
        _apply_plan(ctx, plan)
        ctx.set_tv(legacy_pending=False)
        ctx.changed = True
        if plan.shown_day_changes:
            logger.info(
                "Converted TV log dates for %s (user %s): %s",
                ctx.show_item,
                ctx.user.pk,
                plan.shown_day_changes,
            )
        return plan
    if any(not log.stamped for log in ctx.facts.logs):
        plan = _plan(ctx, include_rule=False)
        _apply_plan(ctx, plan)
        if plan.shown_day_changes:
            logger.info(
                "Converted TV log dates for %s (user %s): %s",
                ctx.show_item,
                ctx.user.pk,
                plan.shown_day_changes,
            )
        return plan
    return None


def _record_legacy_facts(ctx):
    """Before a webhook writes to a show not yet worked out, record its legacy facts (TV-801).

    The legacy plan (its logs converted with their fills and links, the legacy rule's
    marks, the stored show-level choice) describes the show as it was stored before this
    write, so it is recorded first and the event applies on top. Nothing is worked out:
    the show stays legacy for the repair or the user's next write, which then reach the
    same state whether the webhook came first or not (TV-207, TV-802).
    """
    plan = _plan(ctx, include_rule=True)
    _apply_plan(ctx, plan)
    if plan.shown_day_changes:
        logger.info(
            "Converted TV log dates for %s (user %s): %s",
            ctx.show_item,
            ctx.user.pk,
            plan.shown_day_changes,
        )
    return plan


@dataclass
class _ReadCtx:
    user: object
    tv: TV | None
    listing: object
    today: date
    now: datetime
    facts: _Facts


def _read_ctx(tv, listing):
    return _ReadCtx(
        user=tv.user,
        tv=tv,
        listing=listing,
        today=user_calendar.today(tv.user),
        now=user_calendar.now(),
        facts=_load_facts(tv.user, tv.item, tv),
    )


def plan_legacy(tv, listing):
    """Return what convert_legacy_logs and apply_legacy_rule would write (pure)."""
    return _plan(_read_ctx(tv, listing), include_rule=tv.legacy_pending)


def _locked_ctx(tv, listing, entry_point=REPAIR):
    with _writing():
        return _Ctx(tv.user, tv.item, listing, entry_point, _all_items(tv.item, listing, tv.user), tv)


def _all_items(show_item, listing, user):
    seasons, episodes = _scope_keys(listing, user_calendar.today(user), (("all",),))
    return _item_ids(show_item, listing, seasons, episodes)


def prepare_items(user, show_item, listing):
    """Create the show's season and aired episode Items (call before a transaction)."""
    return _all_items(show_item, listing, user)


def convert_legacy_logs(tv, listing):
    """Convert the show's logs from before this contract (TV-801, A-59, A-67).

    Each unstamped log's date becomes a calendar date on the user's calendar; a season
    or show log gets its coverage and fills; an episode log is linked to one viewing;
    then it is stamped. Returns the logs whose shown day changed. Call inside the
    write's transaction with the TV row locked, and with the show's Items created
    (the repair creates them before its transaction).
    """
    with _writing():
        ctx = _locked_ctx(tv, listing)
        plan = _plan(ctx, include_rule=False)
        _apply_plan(ctx, plan)
        ctx.flush_tv()
        return plan.shown_day_changes


def apply_legacy_rule(tv, listing):
    """Apply TV-801's legacy rule while the show is legacy: hollow Completed rows are marks.

    Returns the marks it created. Also turns a stored show-level Planning, Paused or
    Dropped (and an In progress show with nothing recorded) into the user's choice.
    """
    if not tv.legacy_pending:
        return []
    with _writing():
        ctx = _locked_ctx(tv, listing)
        plan = _plan(ctx, include_rule=True)
        rule_only = LegacyPlan(mark_fills=plan.mark_fills, marks=plan.marks, choice=plan.choice)
        _apply_plan(ctx, rule_only)
        ctx.flush_tv()
        return plan.marks


def rebuild_log_viewings(tv, listing, *, exact_datetime_first=False):
    """Rebuild each stamped log's own viewing and fills (TV-800 overwrite imports).

    A season or show log fills its covered episodes that aren't watched; an episode log
    without a viewing claims an imported viewing on its calendar day (or, first, one
    dated exactly at its date and time) before adding one.
    """
    with _writing():
        ctx = _locked_ctx(tv, listing)
        plan = _plan(ctx, include_rule=False, exact_datetime_first=exact_datetime_first, rebuild=True)
        _apply_plan(ctx, plan)


def work_out(tv, listing, *, entry_point=REPAIR):
    """Convert, apply the legacy rule, reconcile and clear the flag, as the first write does.

    Returns {"shown_day_changes": [...], "marks": [...]}; call inside a transaction with
    the TV row locked.
    """
    with _writing():
        ctx = _locked_ctx(tv, listing, entry_point)
        plan = _work_out_legacy(ctx) or LegacyPlan()
        _reconcile(ctx)
        return {"shown_day_changes": plan.shown_day_changes, "marks": plan.marks}


def _apply_plan_in_memory(facts, plan, user, now):
    """Return facts as they would be after a LegacyPlan (for legacy pages)."""
    if plan is None or plan.empty:
        return facts
    viewings = []
    links = {viewing_id: (entry_id, day) for entry_id, viewing_id, day in plan.links}
    for viewing in facts.viewings:
        if viewing.id in links:
            entry_id, day = links[viewing.id]
            viewing = replace(
                viewing,
                entry_id=entry_id,
                watched_on=day or viewing.watched_on,
                day=day or viewing.day,
            )
        viewings.append(viewing)
    # Viewings the plan would add get negative ids, unique in memory and never shown.
    virtual = iter(range(-1, -10**9, -1))
    for entry_id, season, episode in plan.log_fills:
        viewings.append(_Viewing(next(virtual), season, episode, FILL, entry_id, now, None, None))
    for entry_id, season, episode, day in plan.log_viewings:
        viewings.append(
            _Viewing(next(virtual), season, episode, LOG, entry_id, now, day, _start_of_day(day, user), day=day),
        )
    for season, episode in plan.mark_fills:
        viewings.append(_Viewing(next(virtual), season, episode, FILL, None, now, None, None))
    logs = []
    for log in facts.logs:
        if log.id in plan.log_days or log.id in plan.snapshots:
            snapshot = plan.snapshots.get(log.id, log.snapshot)
            log = replace(
                log,
                day=plan.log_days.get(log.id, log.day),
                snapshot=snapshot,
                stamped=True,
                covers=_covers_from_snapshot(snapshot),
            )
        logs.append(log)
    return _Facts(seasons=facts.seasons, viewings=viewings, logs=logs, likes=facts.likes)


# ---------------------------------------------------------------------------------
# Opinions helpers (TV-303 to TV-305, TV-500 to TV-502)
# ---------------------------------------------------------------------------------


def _set_like_row(user, item_id, liked):
    if liked:
        _, created = MediaLike.objects.get_or_create(user=user, item_id=item_id)
        return created
    deleted, _ = MediaLike.objects.filter(user=user, item_id=item_id).delete()
    return bool(deleted)


def _audit_like(user, item_id, liked):
    from social.models import SocialAuditLog  # noqa: PLC0415

    SocialAuditLog.objects.create(
        actor=user,
        action="media_like" if liked else "media_unlike",
        target_type="item",
        target_id=item_id,
    )


def _audit_diary(entry, action):
    from social.models import SocialAuditLog  # noqa: PLC0415

    SocialAuditLog.objects.create(
        actor=entry.user,
        action=action,
        target_type="diary",
        target_id=entry.pk,
        metadata={
            "rating": _rating_string(entry.rating),
            "consumed_at": single_weight.calendar_date(entry.consumed_at).isoformat(),
        },
    )


def _set_rating_activity(user, row, rating):
    """Create, update or delete direct-stars activity on the 0-10 scale (SW-401, TV-703)."""
    from social.models import Activity  # noqa: PLC0415

    queryset = Activity.objects.filter(
        actor=user,
        verb="rating_updated",
        target_type="tracking",
        target_id=row.pk,
        item_id=row.item_id,
    )
    if rating is None:
        queryset.delete()
        return
    snapshot = {"rating": _rating_string(rating)}
    activity = queryset.first()
    if activity is not None:
        activity.snapshot = snapshot
        activity.visibility = "public"
        activity.save(update_fields=["snapshot", "visibility"])
        return
    Activity.objects.create(
        actor=user,
        verb="rating_updated",
        target_type="tracking",
        target_id=row.pk,
        item_id=row.item_id,
        visibility="public",
        snapshot=snapshot,
    )


def _diary_activity_snapshot(entry):
    return {"rating": _rating_string(entry.rating), "liked": bool(entry.liked)}


def _create_diary_activity(entry):
    from social.models import Activity  # noqa: PLC0415

    Activity.objects.create(
        actor=entry.user,
        verb="diary_created",
        target_type="diary",
        target_id=entry.pk,
        item=entry.item,
        visibility=entry.visibility,
        snapshot=_diary_activity_snapshot(entry),
    )


def _update_diary_activity(entry):
    from social.models import Activity  # noqa: PLC0415

    Activity.objects.filter(
        actor=entry.user,
        verb="diary_created",
        target_type="diary",
        target_id=entry.pk,
    ).update(visibility=entry.visibility, snapshot=_diary_activity_snapshot(entry))


def _clear_all_opinions(ctx):
    """Clear the current stars and hearts of the show, its seasons and episodes (GM-304)."""
    from social.models import Activity  # noqa: PLC0415

    MediaLike.objects.filter(_family(ctx.show_item), user=ctx.user).delete()
    Activity.objects.filter(
        _family(ctx.show_item),
        actor=ctx.user,
        verb="rating_updated",
        target_type="tracking",
    ).delete()
    if ctx.tv is not None and ctx.tv.pk is not None:
        ctx.set_tv(score=None, rating_source=None, like_source=None, like_is_independent=False)
    rows = []
    for row in ctx.facts.seasons.values():
        if row.score is not None or row.rating_source_id or row.like_source_id or row.like_is_independent:
            row.score = None
            row.rating_source = None
            row.like_source = None
            row.like_is_independent = False
            rows.append(row)
    if rows:
        bulk_update_with_history(
            rows,
            Season,
            fields=["score", "rating_source", "like_source", "like_is_independent"],
            default_user=ctx.user,
            default_date=ctx.now,
        )


def _delete_season_rows(ctx, numbers):
    """Delete season rows with their viewings, stars and hearts (TV-205, SW-008)."""
    from social.models import Activity  # noqa: PLC0415

    rows = [ctx.facts.seasons[number] for number in numbers if number in ctx.facts.seasons]
    if not rows:
        return
    ids = [row.pk for row in rows]
    viewing_ids = list(Episode.objects.filter(related_season_id__in=ids).values_list("id", flat=True))
    if viewing_ids:
        _raw_delete(Episode.objects.filter(pk__in=viewing_ids))
        Episode.history.filter(id__in=viewing_ids).delete()
    item_ids = [row.item_id for row in rows]
    MediaLike.objects.filter(user=ctx.user, item_id__in=item_ids).delete()
    Activity.objects.filter(
        actor=ctx.user,
        verb="rating_updated",
        target_type="tracking",
        target_id__in=ids,
        item_id__in=item_ids,
    ).delete()
    _raw_delete(Season.objects.filter(pk__in=ids))
    Season.history.filter(id__in=ids).delete()
    for number in numbers:
        ctx.facts.seasons.pop(number, None)
    ctx.changed = True


def _leave_library(ctx):
    """Delete the show's rows, starts and progress and clear its opinions (TV-003).

    Custom lists keep it. A removed title leaves activity (activity contract AC-2).
    """
    if ctx.tv is None or ctx.tv.pk is None:
        return
    _delete_season_rows(ctx, list(ctx.facts.seasons))
    _forget_progress(ctx)
    _clear_all_opinions(ctx)
    tv_id = ctx.tv.pk
    TV.objects.filter(pk=tv_id).delete()
    ctx.tv = None
    ctx.tv_dirty.clear()
    ctx.changed = True


def _after_removal(ctx):
    """Apply TV-205, TV-208 and TV-003 after an undo removed facts.

    Returns True when the show left the library.
    """
    ctx.reload()
    emptied = [
        number
        for number in ctx.touched_seasons
        if number in ctx.facts.seasons
        and not any(viewing.season == number for viewing in ctx.facts.viewings)
        and not any(log.season == number for log in ctx.facts.logs)
    ]
    if emptied:
        _delete_season_rows(ctx, emptied)
    tv = ctx.tv
    recorded = bool(ctx.facts.viewings or ctx.facts.logs)
    if tv.ended_choice and not tv.choice:
        since = tv.ended_choice_at
        remaining = since is None or any(
            viewing.created_at >= since for viewing in ctx.facts.viewings
        ) or any(log.created_at >= since for log in ctx.facts.logs)
        if not remaining:
            ctx.set_tv(choice=tv.ended_choice, ended_choice="", ended_choice_at=None)
            # The undone write's start goes with it: the show is back in Planning or
            # Dropped (activity contract AC-2).
            _forget_starts_since(ctx, since)
            if not recorded:
                _clear_all_opinions(ctx)
            ctx.invalidate()
            return False
    if not recorded and not tv.choice:
        _leave_library(ctx)
        return True
    ctx.invalidate()
    return False


# ---------------------------------------------------------------------------------
# The earlier-episodes question (TV-107) and the season-finish offer (TV-403)
# ---------------------------------------------------------------------------------


def _has_regular_history(facts):
    return any(viewing.season >= 1 for viewing in facts.viewings) or any(
        log.level == SHOW or (log.season or 0) >= 1 for log in facts.logs
    )


def _first_regular_write(ctx):
    """Return whether this write is the show's first regular write, before it runs."""
    return (ctx.tv is None or ctx.tv.earlier_question_at is None) and not _has_regular_history(ctx.facts)


def _use_question(ctx, target, *, offer_season_number=None, by_season=True):
    """Use the question up and, for the app, build earlier_unwatched (TV-107).

    A season-level write names whole seasons ("Also mark Seasons 1–2 watched?"); an
    episode-level one names the episode range ("Also mark S1 E1–S2 E10 watched?").
    seasons lists the whole seasons either way, or is None.
    """
    ctx.ensure_tv()
    if ctx.tv.earlier_question_at is None:
        ctx.set_tv(earlier_question_at=ctx.now)
    if ctx.entry_point != APP or target is None:
        return None
    ctx.reload()
    calc = ctx.calc
    before = [
        position
        for position in calc.regular_aired
        if position < target and position not in calc.watched_pass
    ]
    if not before:
        return None
    first, last = before[0], before[-1]
    numbers = sorted({season for season, _ in before})
    whole = all(
        {(number, listed.episode_number) for listed in calc.seasons[number].episodes} <= set(before)
        for number in numbers
    )
    if whole and by_season:
        question = (
            f"Also mark Season {numbers[0]} watched?"
            if len(numbers) == 1
            else f"Also mark Seasons {numbers[0]}–{numbers[-1]} watched?"
        )
    else:
        question = f"Also mark {_episode_range(first, last)} watched?"
    return {
        "from": {"season_number": first[0], "episode_number": first[1]},
        "to": {"season_number": last[0], "episode_number": last[1]},
        "count": len(before),
        "seasons": numbers if whole else None,
        "question": question,
        "offer_season_number": offer_season_number,
    }


def _offer_in_pass(ctx, season_number):
    offers = ctx.tv.finish_offers or {} if ctx.tv is not None else {}
    offered = offers.get(str(season_number))
    if not offered:
        return False
    offered_at = datetime.fromisoformat(offered)
    rewatch = _rewatch_of(ctx.tv)
    return _in_pass(rewatch, season_number, offered_at)


def _season_log_in_pass(ctx, season_number, calc=None):
    rewatch = (calc or ctx.calc).rewatch
    return any(
        log.level == SEASON and log.season == season_number and _in_pass(rewatch, season_number, log.created_at)
        for log in ctx.facts.logs
    )


def _finish_offer(ctx, season_number, was_finished, day):
    """Return finished for an app tick or episode log that finished its season (TV-403)."""
    if ctx.entry_point != APP or season_number is None or season_number < 1 or was_finished:
        return None
    calc = ctx.calc
    season = calc.seasons.get(season_number)
    if season is None or not season.finished:
        return None
    if _season_log_in_pass(ctx, season_number) or _offer_in_pass(ctx, season_number):
        return None
    offers = dict(ctx.tv.finish_offers or {})
    offers[str(season_number)] = ctx.now.isoformat()
    ctx.set_tv(finish_offers=offers)
    view = _View.from_ctx(ctx)
    targets = [view.composer_target(SEASON, season_number, date_override=day)]
    show_finished = calc.finished
    if show_finished:
        targets.append(view.composer_target(SHOW, None, date_override=day))
    return {
        "level": "season",
        "season_number": season_number,
        "show_finished": show_finished,
        "composer": {"default_target": f"season:{season_number}", "targets": targets},
    }


# ---------------------------------------------------------------------------------
# Viewings: ticks and unticks (TV-100 to TV-102, TV-106)
# ---------------------------------------------------------------------------------


def _validate_episode(listing, season_number, episode_number, today, kind):
    try:
        tv_listing.validate_write(listing, season_number, episode_number, today=today, kind=kind)
    except tv_listing.InvalidEpisode as error:
        raise _invalid_episode(error) from error


def _tick_moment(ctx, at):
    """Return (end_date, day) of a tick (TV-100): now, a webhook's time or a typed day."""
    if at is None or ctx.entry_point in API_ENTRY_POINTS:
        return ctx.now, user_calendar.day_of(ctx.now, ctx.user)
    if isinstance(at, datetime):
        day = user_calendar.day_of(at, ctx.user)
        if day > ctx.today + tv_listing.AIR_DATE_SLACK:
            raise TVInvalid(MSG_FUTURE_WATCH)
        return at, day
    day = at if isinstance(at, date) else _parse_log_day(at, ctx.user)
    if day > ctx.today:
        raise TVInvalid(MSG_FUTURE_WATCH)
    if day == ctx.today:
        return ctx.now, day
    return _start_of_day(day, ctx.user), day


def _do_tick(ctx, season_number, episode_number, *, at=None):
    ctx.ensure_tv()
    calc = ctx.calc
    if (season_number, episode_number) in calc.watched_pass:
        return {"changed": False}
    end_date, day = _tick_moment(ctx, at)
    first = season_number >= 1 and _first_regular_write(ctx)
    season = calc.seasons.get(season_number)
    was_finished = bool(season and season.finished)
    ctx.add_viewing(season_number, episode_number, kind=TICK, day=day, end_date=end_date)
    ctx.end_choice()
    ctx.may_start = True
    ctx.reload()
    earlier = None
    if first:
        earlier = _use_question(
            ctx,
            (season_number, episode_number),
            offer_season_number=season_number,
            by_season=False,
        )
    finished = _finish_offer(ctx, season_number, was_finished, day)
    return {"changed": True, "finished": finished, "earlier_unwatched": earlier}


def tick(
    user,
    show_item,
    season_number,
    episode_number,
    *,
    at=None,
    entry_point=APP,
    mutation_id=None,
):
    """Record one viewing of an aired episode, dated now (TV-100, TV-101, TV-106).

    at dates a webhook watch (its event time) or the web form's typed day; app ticks are
    dated now. Ticking an episode watched in the current pass records nothing.
    """
    season_number = int(season_number)
    episode_number = int(episode_number)

    def body(ctx):
        _validate_episode(ctx.listing, season_number, episode_number, ctx.today, tv_listing.TICK)
        return _do_tick(ctx, season_number, episode_number, at=at)

    return _perform(
        user,
        show_item,
        "tick",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        scope=((EPISODE, season_number, episode_number),),
        season_number=season_number,
        episode_number=episode_number,
    )


def _covering_logs(ctx, season_number, episode_number, calc=None):
    """Return the season and show logs in the current pass that cover an episode (TV-400)."""
    rewatch = (calc or ctx.calc).rewatch
    season_logs = []
    show_logs = []
    for log in ctx.facts.logs:
        if not _in_pass(rewatch, season_number, log.created_at):
            continue
        if episode_number not in log.covers.get(season_number, ()):
            continue
        if log.level == SEASON and log.season == season_number:
            season_logs.append(log)
        elif log.level == SHOW:
            show_logs.append(log)
    return season_logs, show_logs


def _untick_target(ctx, season_number, episode_number, calc=None):
    """Return (viewing to remove, message when blocked) for an untick (TV-102)."""
    calc = calc or ctx.calc
    rewatch = calc.rewatch
    in_pass = [
        viewing
        for viewing in ctx.facts.viewings
        if viewing.pos == (season_number, episode_number)
        and _in_pass(rewatch, season_number, viewing.created_at)
    ]
    if not in_pass:
        return None, None
    season_logs, show_logs = _covering_logs(ctx, season_number, episode_number, calc)
    if season_logs:
        return None, f"Delete your {_season_label(season_number)} log first."
    if show_logs:
        return None, MSG_DELETE_SHOW_LOG
    unlogged = [viewing for viewing in in_pass if viewing.entry_id is None]
    if not unlogged:
        return None, MSG_DELETE_EPISODE_LOG
    target = max(unlogged, key=lambda viewing: (viewing.created_at, viewing.id or 0))
    return target, None


def _do_untick(ctx, season_number, episode_number, *, viewing_id=None):
    if ctx.tv is None:
        return {"changed": False}
    target, message = _untick_target(ctx, season_number, episode_number)
    if message is not None:
        raise TVLogBlocks(message)
    if target is None or (viewing_id is not None and int(viewing_id) != target.id):
        return {"changed": False}
    ctx.delete_viewings([target])
    remaining = [
        viewing for viewing in ctx.facts.viewings if viewing.id != target.id and viewing.pos == target.pos
    ]
    has_log = any(
        log.level == EPISODE and (log.season, log.episode) == target.pos for log in ctx.facts.logs
    )
    if not remaining and not has_log:
        item_id = ctx.episode_items.get(target.pos)
        if item_id is None:
            item_id = Item.objects.filter(
                media_id=ctx.show_item.media_id,
                source=ctx.show_item.source,
                media_type=MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode_number,
            ).values_list("id", flat=True).first()
        if item_id is not None:
            MediaLike.objects.filter(user=ctx.user, item_id=item_id).delete()
    left = _after_removal(ctx)
    return {"changed": True, "left_library": left}


def untick(
    user,
    show_item,
    season_number,
    episode_number,
    *,
    viewing_id=None,
    entry_point=APP,
    mutation_id=None,
):
    """Remove the most recently recorded unlogged viewing in the current pass (TV-102).

    viewing_id names the viewing the app showed; if another one would be removed now,
    nothing changes. A covering log or an episode whose viewings all belong to logs
    blocks it (TVLogBlocks with the log to delete).
    """
    season_number = int(season_number)
    episode_number = int(episode_number)
    return _perform(
        user,
        show_item,
        "untick",
        lambda ctx: _do_untick(ctx, season_number, episode_number, viewing_id=viewing_id),
        entry_point=entry_point,
        mutation_id=mutation_id,
        season_number=season_number,
        episode_number=episode_number,
    )


# ---------------------------------------------------------------------------------
# Marks (TV-108, TV-300 to TV-302)
# ---------------------------------------------------------------------------------


def _level_positions(calc, season_number):
    """Return the aired episodes an eye covers: the show's regular ones, or a season's."""
    if season_number is None:
        return list(calc.regular_aired)
    season = calc.seasons.get(season_number)
    if season is None or not season.listed:
        return []
    return [(season_number, episode) for episode in season.aired]


def _require_listed_season(calc, season_number):
    season = calc.seasons.get(season_number)
    if season is None or not season.listed:
        raise TVInvalid(MSG_UNLISTED_SEASON)
    return season


def _do_mark(ctx, positions, *, target=None, first=None, by_season=True):
    """Mark positions watched, undated, as one mark; end a choice (TV-300, TV-301)."""
    calc = ctx.calc
    missing = [position for position in positions if position not in calc.watched_pass]
    regular = any(season >= 1 for season, _ in positions)
    if not missing:
        return {"changed": False}
    ctx.ensure_tv()
    if first is None:
        first = _first_regular_write(ctx)
    missing = [position for position in missing if position not in ctx.calc.watched_pass]
    ctx.add_fills(missing)
    ctx.end_choice()
    ctx.reload()
    earlier = None
    if first and regular:
        earlier = _use_question(ctx, target, by_season=by_season)
    return {"changed": True, "earlier_unwatched": earlier}


def mark_watched(user, show_item, *, season_number=None, entry_point=APP, mutation_id=None):
    """Mark every aired episode under the eye watched, undated, as one mark (TV-300).

    season_number None is the show eye (regular episodes only, TV-004); 0 is the
    Specials eye. Nothing aired records nothing.
    """
    season_number = None if season_number is None else int(season_number)

    def body(ctx):
        calc = ctx.calc
        if season_number is not None:
            _require_listed_season(calc, season_number)
        positions = _level_positions(calc, season_number)
        target = None
        if season_number is not None and season_number >= 1:
            season = calc.seasons[season_number]
            target = (season_number, season.aired[0]) if season.aired else None
        return _do_mark(ctx, positions, target=target)

    scope = (("regular",),) if season_number is None else ((SEASON, season_number),)
    return _perform(
        user,
        show_item,
        "mark_watched",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        scope=scope,
        season_number=season_number,
    )


def _eye_case(ctx, season_number, calc=None):  # noqa: C901, PLR0912
    """Work through TV-302's cases for a level. Returns a dict describing the tap.

    {"case": 1, "message"} | {"case": 2, "remove": [viewings], "reassign": {id: entry},
    "count"} | {"case": 3, "remove": [viewings], "count", "blocked": message or None}
    | {"case": 0} when nothing under the level is watched.
    """
    calc = calc or ctx.calc
    rewatch = calc.rewatch
    positions = set(_level_positions(calc, season_number))
    level_season = season_number

    def in_level(viewing):
        if season_number is None:
            return viewing.season >= 1
        return viewing.season == season_number

    pass_viewings = [
        viewing
        for viewing in ctx.facts.viewings
        if in_level(viewing) and _in_pass(rewatch, viewing.season, viewing.created_at)
    ]
    if not pass_viewings:
        return {"case": 0}
    # Case 1: a log covers that level.
    for log in ctx.facts.logs:
        if season_number is None:
            if log.level == SHOW and _in_pass(rewatch, None, log.created_at):
                return {"case": 1, "message": MSG_DELETE_SHOW_LOG}
        elif log.level == SEASON and log.season == season_number and _in_pass(rewatch, level_season, log.created_at):
            return {"case": 1, "message": f"Delete your {_season_label(season_number)} log first."}
    if season_number is not None and season_number >= 1:
        for log in ctx.facts.logs:
            if log.level == SHOW and _in_pass(rewatch, season_number, log.created_at):
                return {"case": 1, "message": MSG_DELETE_SHOW_LOG}
    # Case 2: marks finished it.
    mark_fills = [viewing for viewing in pass_viewings if viewing.is_mark_fill]
    if mark_fills:
        reassign = {}
        removable = []
        for viewing in mark_fills:
            season_logs, show_logs = _covering_logs(ctx, viewing.season, viewing.episode, calc)
            owner = (season_logs or show_logs or [None])[0]
            if owner is not None:
                reassign[viewing.id] = owner.id
            else:
                removable.append(viewing)
        removable_ids = {viewing.id for viewing in removable}
        unwatched = set()
        for position in positions:
            viewings = [viewing for viewing in pass_viewings if viewing.pos == position]
            if viewings and all(viewing.id in removable_ids for viewing in viewings):
                unwatched.add(position)
        if unwatched:
            return {"case": 2, "remove": removable, "reassign": reassign, "count": len(unwatched)}
    # Case 3: anything else finished it.
    logs_under = [
        log
        for log in ctx.facts.logs
        if (season_number is None or log.season == season_number)
        and _in_pass(rewatch, log.season if log.season is not None else None, log.created_at)
    ]
    covered_viewings = [viewing for viewing in pass_viewings if viewing.pos in positions]
    count = len({viewing.pos for viewing in covered_viewings})
    blocked = MSG_DELETE_LOGS if logs_under else None
    return {"case": 3, "remove": covered_viewings, "count": count, "blocked": blocked}


def _do_unmark(ctx, season_number, *, confirm, expected_version):
    if ctx.tv is None:
        return {"changed": False}
    calc = ctx.calc
    if season_number is not None:
        season = calc.seasons.get(season_number)
        if season is None:
            return {"changed": False}
    case = _eye_case(ctx, season_number, calc)
    if case["case"] == 0:
        return {"changed": False}
    if case["case"] == 1:
        raise TVLogBlocks(case["message"])
    if case["case"] == 2:
        if case["reassign"]:
            Episode.objects.bulk_update(
                [Episode(pk=viewing_id, diary_entry_id=entry_id) for viewing_id, entry_id in case["reassign"].items()],
                ["diary_entry"],
            )
            ctx.changed = True
        ctx.delete_viewings(case["remove"])
        left = _after_removal(ctx)
        return {"changed": True, "left_library": left}
    if case["blocked"]:
        raise TVLogBlocks(case["blocked"])
    if not confirm:
        raise TVInvalid(MSG_CONFIRM)
    if expected_version is not None and int(expected_version) != int(ctx.version_before):
        raise TVStateChanged(state_version=ctx.version_before, count=case["count"])
    ctx.delete_viewings(case["remove"])
    left = _after_removal(ctx)
    return {"changed": True, "left_library": left}


def unmark(
    user,
    show_item,
    *,
    season_number=None,
    confirm=False,
    expected_version=None,
    entry_point=APP,
    mutation_id=None,
):
    """Undo a filled show or season eye under TV-302's cases.

    1. A log covering the level blocks it (TVLogBlocks). 2. Marks finished it: their
    fills are removed (fills a covering log needs become that log's). 3. Otherwise,
    with confirm (and a matching expected_version when given), every viewing in the
    current pass of the episodes the eye covers is removed; a log under the level
    blocks it. Then TV-208 applies.
    """
    season_number = None if season_number is None else int(season_number)
    return _perform(
        user,
        show_item,
        "unmark",
        lambda ctx: _do_unmark(ctx, season_number, confirm=confirm, expected_version=expected_version),
        entry_point=entry_point,
        mutation_id=mutation_id,
        season_number=season_number,
    )


def mark_up_to(user, show_item, season_number, episode_number, *, entry_point=APP, mutation_id=None):
    """Mark an aired regular episode and every unwatched one before it watched (TV-108)."""
    season_number = int(season_number)
    episode_number = int(episode_number)

    def body(ctx):
        calc = ctx.calc
        target = (season_number, episode_number)
        if season_number < 1 or target not in calc.regular_aired:
            _validate_episode(ctx.listing, season_number, episode_number, ctx.today, tv_listing.FILL)
            raise TVInvalid("Mark watched up to here works on aired episodes, not specials.")
        positions = [position for position in calc.regular_aired if position <= target]
        return _do_mark(ctx, positions, target=positions[0])

    return _perform(
        user,
        show_item,
        "mark_up_to",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        scope=(("regular",),),
        season_number=season_number,
        episode_number=episode_number,
    )


def _ref_position(ref):
    if ref is None:
        raise TVInvalid("Name the first and last episodes to mark.")
    try:
        if isinstance(ref, dict):
            return int(ref["season_number"]), int(ref["episode_number"])
        return int(ref[0]), int(ref[1])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise TVInvalid("Name the first and last episodes to mark.") from error


def mark_earlier(
    user,
    show_item,
    *,
    from_ref,
    to_ref,
    offer_season_number=None,
    entry_point=APP,
    mutation_id=None,
):
    """The Yes to the earlier-episodes question: mark the range's unwatched episodes (TV-107).

    Marks only episodes in the range that are still unwatched and aired, as one mark.
    finished names offer_season_number's composer when that season is now finished and
    hasn't been offered in this pass (TV-403).
    """
    first = _ref_position(from_ref)
    last = _ref_position(to_ref)
    if entry_point == APP and _mutation_uuid(mutation_id) is None:
        raise TVInvalid(MSG_MUTATION_REQUIRED)

    def body(ctx):
        calc = ctx.calc
        positions = [position for position in calc.regular_aired if first <= position <= last]
        offer = None if offer_season_number is None else int(offer_season_number)
        offered_season = calc.seasons.get(offer) if offer is not None else None
        was_finished = bool(offered_season and offered_season.finished)
        outcome = _do_mark(ctx, positions, first=False)
        if offer is not None and ctx.tv is not None:
            season = ctx.calc.seasons.get(offer)
            days = [viewing.day for viewing in (season.viewings if season else []) if viewing.day]
            day = max(days) if days else ctx.today
            outcome["finished"] = _finish_offer(ctx, offer, was_finished, day)
        return outcome

    return _perform(
        user,
        show_item,
        "mark_earlier",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        scope=(("regular",),),
    )


# ---------------------------------------------------------------------------------
# Choices and rewatch (TV-005 to TV-007, TV-203, TV-600 to TV-602)
# ---------------------------------------------------------------------------------


def _choice_unavailable(calc, tv, status):
    """Return why a show-level choice is unavailable, or None (TV-005, TV-006)."""
    choice = tv.choice if tv is not None else ""
    if status == WATCHING:
        if not calc.regular_aired:
            return MSG_NOTHING_AIRED
        if calc.finished and choice not in (PAUSED, DROPPED):
            return MSG_ALL_WATCHED
        return None
    if status == PLANNING and calc.recorded:
        return MSG_STARTED
    return None


def _do_choose(ctx, status, *, quiet=False):
    if status not in CHOICES:
        raise TVInvalid(f"Choose one of {', '.join(CHOICES)}.")
    calc = ctx.calc
    reason = _choice_unavailable(calc, ctx.tv, status)
    if reason is not None:
        if quiet:
            return {"changed": False}
        raise TVConflict(reason)
    ctx.ensure_tv()
    tv = ctx.tv
    if tv.choice == status and not tv.ended_choice:
        return {"changed": False}
    values = {"choice": status, "choice_at": ctx.now, "ended_choice": "", "ended_choice_at": None}
    if tv.choice == status:
        values.pop("choice_at")
    ctx.set_tv(**values)
    ctx.changed = True
    ctx.may_start = status == WATCHING
    return {"changed": True}


def choose(user, show_item, status, *, entry_point=APP, mutation_id=None):
    """Choose Watching ("In progress"), Planning, Paused or Dropped for the show.

    Records no date and no viewing and creates no season tracking (TV-005 to TV-007).
    Watching is unavailable while the show is finished and not Paused or Dropped, and
    before anything has aired; Planning only before anything is watched (TV-006).
    """
    return _perform(
        user,
        show_item,
        f"choose:{status}",
        lambda ctx: _do_choose(ctx, status),
        entry_point=entry_point,
        mutation_id=mutation_id,
    )


def _do_start_rewatch(ctx, season_number):
    if ctx.tv is None:
        raise TVConflict(MSG_FINISH_SHOW_FIRST if season_number is None else MSG_FINISH_SEASON_FIRST)
    if ctx.tv.rewatch_started_at is not None:
        raise TVConflict(MSG_REWATCH_IN_PROGRESS)
    calc = ctx.calc
    if season_number is None:
        if not calc.finished:
            raise TVConflict(MSG_FINISH_SHOW_FIRST)
    else:
        season = calc.seasons.get(season_number)
        if season is None or not season.finished:
            raise TVConflict(MSG_FINISH_SEASON_FIRST)
    values = {"rewatch_started_at": ctx.now, "rewatch_season_number": season_number}
    if ctx.tv.choice in HOLDING_CHOICES:
        values.update({"choice": "", "ended_choice": "", "ended_choice_at": None})
    ctx.set_tv(**values)
    ctx.changed = True
    ctx.may_start = True
    return {"changed": True}


def start_rewatch(user, show_item, *, season_number=None, entry_point=APP, mutation_id=None):
    """Start a rewatch of a finished show or season; records nothing else (TV-600)."""
    season_number = None if season_number is None else int(season_number)
    return _perform(
        user,
        show_item,
        "rewatch",
        lambda ctx: _do_start_rewatch(ctx, season_number),
        entry_point=entry_point,
        mutation_id=mutation_id,
        season_number=season_number,
    )


def stop_rewatch(user, show_item, *, entry_point=APP, mutation_id=None):
    """End the rewatch in progress, keeping everything watched in it (TV-602)."""

    def body(ctx):
        if ctx.tv is None or ctx.tv.rewatch_started_at is None:
            raise TVConflict(MSG_NOT_REWATCHING)
        ctx.set_tv(rewatch_started_at=None, rewatch_season_number=None)
        ctx.changed = True
        return {"changed": True}

    return _perform(
        user,
        show_item,
        "stop_rewatch",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
    )


def _remove_count(facts):
    return len({viewing.pos for viewing in facts.viewings})


def remove(user, show_item, *, expected_version=None, entry_point=APP, mutation_id=None):
    """Remove from Library (TV-003): every viewing, mark and choice; clears opinions.

    Unavailable while any log of the show, a season or an episode exists. The app sends
    the version its dialog showed; a stale one raises TVStateChanged with the new count.
    """
    if entry_point == APP and _mutation_uuid(mutation_id) is None:
        raise TVInvalid(MSG_MUTATION_REQUIRED)

    def body(ctx):
        if ctx.tv is None:
            return {"changed": False}
        if ctx.facts.logs:
            raise TVLogBlocks(MSG_DELETE_SHOW_LOGS)
        if expected_version is not None and int(expected_version) != int(ctx.version_before):
            raise TVStateChanged(state_version=ctx.version_before, count=_remove_count(ctx.facts))
        final_version = _version_after(ctx.tv.state_version, ctx.now)
        _leave_library(ctx)
        return {
            "changed": True,
            "left_library": True,
            "record_mutation": True,
            "mutation_extra": {"state_version": final_version},
        }

    return _perform(
        user,
        show_item,
        "remove",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        require_listing=False,
    )


def delete_season_tracking(user, show_item, season_number, *, entry_point=APP, mutation_id=None):
    """Delete a season's tracking: every viewing and mark of it (TV-207).

    Refused while any log of the season, its episodes or the show exists.
    """
    if season_number is None:
        raise TVInvalid("Name the season to delete.")
    season_number = int(season_number)

    def body(ctx):
        if ctx.tv is None:
            return {"changed": False}
        if any(log.level == SHOW or log.season == season_number for log in ctx.facts.logs):
            raise TVLogBlocks(MSG_DELETE_LOGS)
        if season_number not in ctx.facts.seasons and not any(
            viewing.season == season_number for viewing in ctx.facts.viewings
        ):
            return {"changed": False}
        ctx.delete_viewings([viewing for viewing in ctx.facts.viewings if viewing.season == season_number])
        _delete_season_rows(ctx, [season_number])
        # Deleting a season's tracking deletes its progress, as for any title (AC-2).
        _forget_progress(ctx, [season_number])
        left = _after_removal(ctx)
        return {"changed": True, "left_library": left}

    return _perform(
        user,
        show_item,
        "delete_season",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        season_number=season_number,
        require_listing=entry_point not in (JOB, HOOK),
    )


# ---------------------------------------------------------------------------------
# Opinions (TV-303 to TV-305, TV-500, TV-501)
# ---------------------------------------------------------------------------------


def _mark_for_opinion(ctx, season_number):
    """Mark an unstarted show (season None) or one of its seasons for stars or a heart."""
    calc = ctx.calc
    positions = _level_positions(calc, season_number)
    target = None
    if season_number is not None and season_number >= 1 and positions:
        target = positions[0]
    return _do_mark(ctx, positions, target=target)


def _opinion_row(ctx, season_number, *, create):
    if season_number is None:
        if ctx.tv is None and create:
            ctx.ensure_tv()
        return ctx.tv
    row = ctx.facts.seasons.get(season_number)
    if row is None and create:
        row = ctx.season_row(season_number, status=PLANNING)
    return row


def _level_aired(calc, season_number):
    if season_number is None:
        return bool(calc.regular_aired)
    season = calc.seasons.get(season_number)
    return bool(season and season.aired)


def _stars_unavailable(calc, season_number):
    """Return why stars can't be set on the show or a season now, or None (TV-303, TV-005).

    The + menu's "rate" and the stars write use this one rule. Stars are unavailable
    while nothing under the level has aired. A show in the library whose only aired
    episodes are specials takes stars as opinion; on a show not in the library they
    would have nothing to mark (TV-004), so they stay unavailable there.
    """
    if season_number is None:
        if calc.regular_aired or (calc.any_aired and calc.in_library):
            return None
        return MSG_NOTHING_AIRED
    season = calc.seasons.get(season_number)
    if season is not None and (season.aired or season.started):
        return None
    return MSG_NOTHING_AIRED


def _do_set_rating(ctx, season_number, rating):
    calc = ctx.calc
    outcome = {"changed": False}
    if rating is not None:
        reason = _stars_unavailable(calc, season_number)
        if reason is not None:
            raise TVConflict(reason)
        if not calc.started:
            outcome = _mark_for_opinion(ctx, season_number)
    row = _opinion_row(ctx, season_number, create=rating is not None)
    if row is None:
        return outcome
    before = (row.score, row.rating_source_id)
    if season_number is None:
        ctx.set_tv(score=rating, rating_source=None)
        ctx.flush_tv()
    else:
        single_weight.set_current_rating(row, rating, emit_activity=False)
    if before != (rating, None):
        outcome["changed"] = True
        ctx.changed = True
    _set_rating_activity(ctx.user, row, rating)
    return outcome


def set_rating(user, item, rating, *, entry_point=APP, mutation_id=None):
    """Set or clear the stars of a show or season (TV-303, TV-304, TV-501).

    rating is the 0-10 wire value (whole 1-10 from the app and API) or None. On a show
    the user hasn't started, stars mark the show (or that season) watched in the same
    step; on a started show they are opinion only. Direct stars are independent of any
    log and create rating activity (SW-401).
    """
    rating = _validate_rating(rating, entry_point)
    show_item = _resolve_show_item(item)
    season_number = item.season_number if item.media_type == MediaTypes.SEASON.value else None
    if item.media_type == MediaTypes.EPISODE.value:
        raise TVInvalid("Episodes have no stars of their own; log the episode instead.")
    scope = (("regular",),) if season_number is None else ((SEASON, season_number),)
    return _perform(
        user,
        show_item,
        "rate",
        lambda ctx: _do_set_rating(ctx, season_number, rating),
        entry_point=entry_point,
        mutation_id=mutation_id,
        scope=scope,
        season_number=season_number,
    )


def _do_set_like(ctx, level, season_number, episode_number, liked, item_id):
    calc = ctx.calc
    outcome = {"changed": False}
    if level == EPISODE:
        position = (season_number, episode_number)
        listed = ctx.listing is not None and ctx.listing.is_listed(*position)
        aired = listed and ctx.listing.is_aired(
            season_number,
            episode_number,
            today=ctx.today,
            has_viewing=position in calc.viewed_any,
        )
        if liked and aired and position not in calc.viewed_any:
            outcome = _do_mark(ctx, [position], target=position, by_season=False)
        changed = _set_like_row(ctx.user, item_id, liked)
        if changed:
            _audit_like(ctx.user, item_id, liked)
            outcome["changed"] = True
            ctx.changed = True
        return outcome
    if liked and not calc.started and _level_aired(calc, season_number):
        outcome = _mark_for_opinion(ctx, season_number)
    row = ctx.tv if season_number is None else ctx.facts.seasons.get(season_number)
    if row is not None and row.pk is not None:
        if season_number is None:
            ctx.set_tv(like_source=None, like_is_independent=True)
            ctx.flush_tv()
        else:
            fields = []
            if row.like_source_id is not None:
                row.like_source = None
                fields.append("like_source")
            if not row.like_is_independent:
                row.like_is_independent = True
                fields.append("like_is_independent")
            if fields:
                bulk_update_with_history([row], Season, fields=fields, default_user=ctx.user, default_date=ctx.now)
    changed = _set_like_row(ctx.user, item_id, liked)
    if changed:
        _audit_like(ctx.user, item_id, liked)
        outcome["changed"] = True
        ctx.changed = True
    return outcome


def set_like(user, item, liked, *, entry_point=APP, mutation_id=None):
    """Turn the heart of a show, season or episode on or off (TV-303 to TV-305, TV-501).

    On a show the user hasn't started, a heart on the show or a season marks it
    watched; an episode heart marks an aired episode with no viewing in any pass
    watched. Otherwise a heart is opinion only, and turning it off never unmarks.
    """
    liked = bool(liked)
    show_item = _resolve_show_item(item)
    if item.media_type == MediaTypes.EPISODE.value:
        level, season_number, episode_number = EPISODE, item.season_number, item.episode_number
        scope = ((EPISODE, season_number, episode_number),)
    elif item.media_type == MediaTypes.SEASON.value:
        level, season_number, episode_number = SEASON, item.season_number, None
        scope = ((SEASON, season_number),)
    else:
        level, season_number, episode_number = SHOW, None, None
        scope = (("regular",),)
    return _perform(
        user,
        show_item,
        "like",
        lambda ctx: _do_set_like(ctx, level, season_number, episode_number, liked, item.pk),
        entry_point=entry_point,
        mutation_id=mutation_id,
        scope=scope,
        season_number=season_number,
        episode_number=episode_number,
    )


# ---------------------------------------------------------------------------------
# Written state (TV-207)
# ---------------------------------------------------------------------------------


def apply_written_state(user, item, data, *, entry_point, mutation_id=None):
    """Apply a status, stars or notes written through a form, PUT/PATCH or a model save.

    A status means its action (TV-207): on a show, Completed marks it watched, In
    progress chooses Watching, and Planning, Paused or Dropped are that choice; on a
    season, Completed marks the season and In progress chooses Watching for the show.
    From the API, progress or date fields and a season Planning, Paused or Dropped are
    TVInvalid and an unavailable action is TVConflict; from the web, webhooks, imports
    and hooks they are ignored and the worked-out status is kept.
    """
    data = dict(data or {})
    strict = entry_point in API_ENTRY_POINTS
    show_item = _resolve_show_item(item)
    if item.media_type == MediaTypes.EPISODE.value:
        raise TVInvalid("Episodes are ticked, not given a status.")
    season_number = item.season_number if item.media_type == MediaTypes.SEASON.value else None
    if strict:
        for name in TV_WRITE_ONLY_FIELDS:
            if data.get(name) is not None:
                raise TVInvalid(MSG_NO_DATES)
    status = data.get("status") or None
    if season_number is not None and status in HOLDING_CHOICES:
        if strict:
            raise TVInvalid(MSG_SEASON_CHOICE)
        status = None
    if status is not None and status not in (*CHOICES, COMPLETED):
        if strict:
            raise TVInvalid(f"Unknown status {status!r}.")
        status = None
    has_rating = "rating" in data
    rating = _validate_rating(data.get("rating"), entry_point) if has_rating else None
    notes = data.get("notes")
    scope = (("regular",),) if season_number is None else ((SEASON, season_number),)

    def body(ctx):
        outcome = {"changed": False}
        if status == COMPLETED:
            positions = _level_positions(ctx.calc, season_number)
            if not positions:
                if strict:
                    raise TVConflict(MSG_NOTHING_AIRED)
            else:
                target = positions[0] if season_number is not None and season_number >= 1 else None
                outcome = _do_mark(ctx, positions, target=target)
        elif status is not None:
            outcome = _do_choose(ctx, status, quiet=not strict)
        if has_rating:
            rated = _do_set_rating(ctx, season_number, rating) if strict else _quiet(
                lambda: _do_set_rating(ctx, season_number, rating),
            )
            if rated:
                outcome["changed"] = outcome.get("changed") or rated.get("changed")
                if rated.get("earlier_unwatched") and not outcome.get("earlier_unwatched"):
                    outcome["earlier_unwatched"] = rated["earlier_unwatched"]
        if notes is not None:
            row = ctx.tv if season_number is None else ctx.facts.seasons.get(season_number)
            if row is not None and row.pk is not None and row.notes != notes:
                if season_number is None:
                    ctx.set_tv(notes=notes)
                else:
                    row.notes = notes
                    bulk_update_with_history([row], Season, fields=["notes"], default_user=ctx.user, default_date=ctx.now)
                outcome["changed"] = True
                ctx.changed = True
        return outcome

    return _perform(
        user,
        show_item,
        "written_state",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        scope=scope,
        season_number=season_number,
    )


def _quiet(function):
    try:
        return function()
    except (TVConflict, TVInvalid) as error:
        logger.info("Ignored a TV written value: %s", getattr(error, "message", error))
        return None


# ---------------------------------------------------------------------------------
# Logs (TV-400 to TV-407, TV-501 to TV-503, TV-703)
# ---------------------------------------------------------------------------------


def _log_target(item):
    if item.media_type == MediaTypes.TV.value:
        return SHOW, None, None
    if item.media_type == MediaTypes.SEASON.value:
        return SEASON, item.season_number, None
    if item.media_type == MediaTypes.EPISODE.value:
        return EPISODE, item.season_number, item.episode_number
    raise TVInvalid(MSG_NOT_TV)


def _rewatch_covers(calc, level, season_number):
    rewatch = calc.rewatch
    if rewatch is None:
        return False
    if rewatch.season is None:
        return True
    return level in (SEASON, EPISODE) and season_number == rewatch.season


def _claimable_tick(ctx, season_number, episode_number, calc=None):
    calc = calc or ctx.calc
    ticks = [
        viewing
        for viewing in ctx.facts.viewings
        if viewing.pos == (season_number, episode_number)
        and viewing.kind == TICK
        and viewing.entry_id is None
        and _in_pass(calc.rewatch, season_number, viewing.created_at)
    ]
    return max(ticks, key=lambda viewing: (viewing.created_at, viewing.id or 0)) if ticks else None


def _only_marks(ctx, positions, calc):
    """Whether at least one position is watched and every watched one only by marks."""
    watched = [position for position in positions if position in calc.watched_pass]
    if not watched:
        return False
    for position in watched:
        viewings = [
            viewing
            for viewing in ctx.facts.viewings
            if viewing.pos == position and _in_pass(calc.rewatch, position[0], viewing.created_at)
        ]
        if not all(viewing.is_mark_fill for viewing in viewings):
            return False
    return True


def _rewatch_default(ctx, level, season_number, episode_number, calc=None):
    """Return the composer's Rewatch default (TV-404), first matching rule wins."""
    calc = calc or ctx.calc
    if _rewatch_covers(calc, level, season_number):
        return True
    if level == EPISODE:
        if _claimable_tick(ctx, season_number, episode_number, calc) is not None:
            return False
        position = (season_number, episode_number)
        return position in calc.viewed_any or any(
            log.level == EPISODE and (log.season, log.episode) == position for log in ctx.facts.logs
        )
    if level == SEASON:
        if any(log.level == SEASON and log.season == season_number for log in ctx.facts.logs):
            return True
        return _only_marks(ctx, _level_positions(calc, season_number), calc)
    if any(log.level == SHOW for log in ctx.facts.logs):
        return True
    return _only_marks(ctx, list(calc.regular_aired), calc)


def _validate_log_target(ctx, level, season_number, episode_number):
    calc = ctx.calc
    if level == EPISODE:
        _validate_episode(ctx.listing, season_number, episode_number, ctx.today, tv_listing.EPISODE_LOG)
        return
    if level == SEASON:
        season = calc.seasons.get(season_number)
        if season is None or not season.listed:
            raise TVInvalid(MSG_UNLISTED_SEASON)
        if not season.loaded:
            raise TVDetailsUnavailable(season_numbers=[season_number])
        if not season.aired:
            raise TVInvalid(MSG_NOTHING_AIRED)
        return
    if not calc.regular_aired:
        raise TVInvalid(MSG_NOTHING_AIRED)


def _log_positions(calc, level, season_number):
    if level == SHOW:
        return list(calc.regular_aired)
    season = calc.seasons.get(season_number)
    return [(season_number, episode) for episode in (season.aired if season else [])]


def _do_create_log(  # noqa: C901, PLR0912, PLR0915
    ctx,
    item,
    level,
    season_number,
    episode_number,
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
):
    _validate_log_target(ctx, level, season_number, episode_number)
    if day > ctx.today:
        raise TVInvalid(MSG_FUTURE_LOG)
    ctx.ensure_tv()
    calc = ctx.calc
    first = (level == SHOW or (season_number or 0) >= 1) and _first_regular_write(ctx)
    if is_rewatch is None:
        is_rewatch = _rewatch_default(ctx, level, season_number, episode_number, calc)
    if liked is None:
        if level == SHOW:
            liked = ctx.facts.liked(SHOW)
        elif level == SEASON:
            liked = ctx.facts.liked(SEASON, season_number)
        else:
            liked = ctx.facts.liked(EPISODE, season_number, episode_number)
    was_finished = bool(
        level == EPISODE and calc.seasons.get(season_number) and calc.seasons[season_number].finished,
    )
    snapshot = {"tv": {"v": STAMP_VERSION}}
    covered = []
    if level in (SEASON, SHOW):
        covered = _log_positions(calc, level, season_number)
        covers = {}
        for number, episode in covered:
            covers.setdefault(str(number), []).append(episode)
        snapshot["tv"]["covers"] = covers
    entry = DiaryEntry.objects.create(
        user=ctx.user,
        item=item,
        consumed_at=_carrier(day),
        rating=rating,
        review=review or "",
        liked=bool(liked),
        is_rewatch=bool(is_rewatch),
        review_title=review_title or "",
        contains_spoilers=bool(contains_spoilers),
        visibility=visibility or "public",
        progress_snapshot=snapshot,
    )
    single_weight._replace_tags(entry, tags or [])
    if level == SEASON:
        ctx.season_row(season_number)
    if level in (SEASON, SHOW):
        ctx.add_fills([position for position in covered if position not in calc.watched_pass], entry=entry)
    else:
        claim = None
        if not is_rewatch or _rewatch_covers(calc, EPISODE, season_number):
            claim = _claimable_tick(ctx, season_number, episode_number, calc)
        if claim is not None:
            fields = {"diary_entry": entry}
            if claim.day != day:
                fields["watched_on"] = day
            Episode.objects.filter(pk=claim.id).update(**fields)
        else:
            ctx.add_viewing(
                season_number,
                episode_number,
                kind=LOG,
                day=day,
                end_date=_start_of_day(day, ctx.user),
                entry=entry,
            )
    ctx.changed = True
    ctx.end_choice()
    # Stars and hearts (TV-501, TV-502).
    if level == SHOW:
        ctx.flush_tv()
        single_weight.couple_current_to_log(ctx.tv, entry)
        ctx.tv.refresh_from_db()
    elif level == SEASON:
        single_weight.couple_current_to_log(ctx.season_row(season_number), entry)
    else:
        _set_like_row(ctx.user, entry.item_id, bool(entry.liked))
    if emit_activity:
        _create_diary_activity(entry)
    ctx.reload()
    earlier = None
    if first:
        if level == EPISODE:
            target = (season_number, episode_number)
            offer = season_number
        elif level == SEASON:
            target = covered[0] if covered else None
            offer = None
        else:
            target = None
            offer = None
        earlier = _use_question(ctx, target, offer_season_number=offer, by_season=level != EPISODE)
    finished = None
    if level == EPISODE:
        finished = _finish_offer(ctx, season_number, was_finished, day)
    return {"changed": True, "entry": entry, "finished": finished, "earlier_unwatched": earlier}


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
    entry_point=APP,
    mutation_id=None,
):
    """Save a log of an episode, a season or the show (TV-400 to TV-404, TV-407).

    Every log puts the show in the library and counts its target as watched: a season or
    show log fills its unwatched aired episodes and covers them; an episode log claims
    the latest unlogged tick in the pass (unless Rewatch is on outside a rewatch) or
    adds a viewing. consumed_at is a calendar date, or a datetime that converts to the
    user's calendar; future dates are TVInvalid. is_rewatch None takes TV-404's
    default; liked None takes the target's current heart. Posts one diary activity.
    """
    level, season_number, episode_number = _log_target(item)
    show_item = _resolve_show_item(item)
    day = _parse_log_day(consumed_at, user)
    if day > user_calendar.today(user):
        raise TVInvalid(MSG_FUTURE_LOG)
    rating = _validate_rating(rating, entry_point)
    if level == SHOW:
        scope = (("regular",),)
    elif level == SEASON:
        scope = ((SEASON, season_number),)
    else:
        scope = ((EPISODE, season_number, episode_number),)

    def body(ctx):
        return _do_create_log(
            ctx,
            item,
            level,
            season_number,
            episode_number,
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
        )

    return _perform(
        user,
        show_item,
        "log",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        scope=scope,
        season_number=season_number,
        episode_number=episode_number,
    )


def update_log(entry, data, *, tags=None, entry_point=APP):
    """Edit a TV log (TV-405, TV-407, TV-501, TV-502).

    A new date moves only an episode log's own viewing; a season or show log's fills
    stay undated. Stars and the heart follow the source rules on the show and seasons;
    an episode's heart never changes on edit.
    """
    data = dict(data or {})
    user = entry.user
    show_item = _resolve_show_item(entry.item)
    level, season_number, episode_number = _log_target(entry.item)
    new_day = None
    if "consumed_at" in data:
        new_day = _parse_log_day(data["consumed_at"], user)
        if new_day > user_calendar.today(user):
            raise TVInvalid(MSG_FUTURE_LOG)
    if "rating" in data:
        data["rating"] = _validate_rating(data["rating"], entry_point)

    def body(ctx):
        locked = DiaryEntry.objects.select_for_update().get(pk=entry.pk)
        previous_rating = locked.rating
        previous_day = locked.consumed_at
        fields = []
        if new_day is not None and locked.consumed_at != _carrier(new_day):
            locked.consumed_at = _carrier(new_day)
            fields.append("consumed_at")
        for name in (
            "rating",
            "review",
            "review_title",
            "liked",
            "is_rewatch",
            "contains_spoilers",
            "visibility",
        ):
            if name in data and getattr(locked, name) != data[name]:
                setattr(locked, name, data[name])
                fields.append(name)
        if fields:
            locked.save(update_fields=[*dict.fromkeys(fields), "updated_at"])
        if tags is not None:
            single_weight._replace_tags(locked, tags)
        if "consumed_at" in fields and level == EPISODE:
            Episode.objects.filter(diary_entry=locked).exclude(kind=FILL).update(
                watched_on=new_day,
                end_date=_start_of_day(new_day, user),
            )
        if level == SHOW and ctx.tv is not None:
            ctx.flush_tv()
            single_weight.update_current_from_source_log(ctx.tv, locked, fields, previous_rating=previous_rating)
        elif level == SEASON:
            row = ctx.facts.seasons.get(season_number)
            if row is not None:
                single_weight.update_current_from_source_log(row, locked, fields, previous_rating=previous_rating)
        _update_diary_activity(locked)
        _audit_diary(locked, "diary_updated")
        if "consumed_at" in fields:
            single_weight._queue_statistics(user.pk, previous_day)
        ctx.changed = bool(fields) or tags is not None
        if ctx.tv is not None:
            ctx.tv.refresh_from_db()
        return {"changed": ctx.changed, "entry": locked}

    return _perform(
        user,
        show_item,
        "update_log",
        body,
        entry_point=entry_point,
        season_number=season_number,
        episode_number=episode_number,
    )


def _keeping_log(ctx, viewing, deleted_id, calc):
    """Return another season or show log that covers a viewing's episode in its pass (TV-400).

    The log a deletion hands the viewing to, as TV-302 case 2 does with mark fills; a
    season log before a show log, the oldest first.
    """
    rewatch = calc.rewatch
    in_pass = _in_pass(rewatch, viewing.season, viewing.created_at)
    season_logs = []
    show_logs = []
    for log in ctx.facts.logs:
        if log.id == deleted_id or _in_pass(rewatch, viewing.season, log.created_at) != in_pass:
            continue
        if viewing.episode not in log.covers.get(viewing.season, ()):
            continue
        if log.level == SEASON and log.season == viewing.season:
            season_logs.append(log)
        elif log.level == SHOW:
            show_logs.append(log)
    return (season_logs or show_logs or [None])[0]


def _log_effect(ctx, log, calc=None):
    """Return what deleting a log does (TV-400, TV-406).

    (own viewings and fills, episodes the deletion would unmark, {viewing id: log id}
    for own viewings another season or show log covers, which that log keeps as fills).
    """
    calc = calc or ctx.calc
    owned = [viewing for viewing in ctx.facts.viewings if viewing.entry_id == log.id]
    owned_ids = {viewing.id for viewing in owned}
    unmarked = set()
    reassign = {}
    for viewing in owned:
        if viewing.kind == TICK:
            continue
        in_pass = _in_pass(calc.rewatch, viewing.season, viewing.created_at)
        others = [
            other
            for other in ctx.facts.viewings
            if other.pos == viewing.pos
            and other.id not in owned_ids
            and _in_pass(calc.rewatch, other.season, other.created_at) == in_pass
        ]
        if others:
            continue
        keeper = _keeping_log(ctx, viewing, log.id, calc)
        if keeper is not None:
            reassign[viewing.id] = keeper.id
        elif in_pass:
            unmarked.add(viewing.pos)
    return owned, unmarked, reassign


def delete_log(user, entry, *, expected_version=None, entry_point=APP, mutation_id=None):
    """Delete a TV log: only its own viewing and fills go (TV-406).

    A claimed tick stays as an unlogged tick on its current day. An episode another
    season or show log still covers stays watched: the viewing becomes that log's fill
    (TV-400). Stars and heart sources are detached (SW-106, SW-203). When the deletion
    would unmark episodes and expected_version is stale, TVStateChanged. Then TV-205
    and TV-208 apply.
    """
    if entry.user_id != user.pk:
        raise TVInvalid(MSG_NOT_YOURS)
    show_item = _resolve_show_item(entry.item)
    level, season_number, episode_number = _log_target(entry.item)

    def body(ctx):
        log = next((log for log in ctx.facts.logs if log.id == entry.pk), None)
        if log is None:
            return {"changed": False}
        owned, unmarked, reassign = _log_effect(ctx, log)
        if unmarked and expected_version is not None and int(expected_version) != int(ctx.version_before):
            raise TVStateChanged(state_version=ctx.version_before, count=len(unmarked))
        claimed = [viewing for viewing in owned if viewing.kind == TICK]
        if claimed:
            Episode.objects.filter(pk__in=[viewing.id for viewing in claimed]).update(diary_entry=None)
        if reassign:
            # Another season or show log still covers these episodes (TV-400): they stay
            # watched as its fills, as TV-302 case 2 hands mark fills to a covering log.
            Episode.objects.bulk_update(
                [
                    Episode(pk=viewing_id, kind=FILL, diary_entry_id=entry_id, watched_on=None, end_date=None)
                    for viewing_id, entry_id in reassign.items()
                ],
                ["kind", "diary_entry", "watched_on", "end_date"],
            )
        ctx.delete_viewings([viewing for viewing in owned if viewing.kind != TICK and viewing.id not in reassign])
        ctx.touched_seasons.update(viewing.season for viewing in owned)
        if log.season is not None:
            ctx.touched_seasons.add(log.season)
        locked = DiaryEntry.objects.select_for_update().get(pk=entry.pk)
        if ctx.tv is not None:
            single_weight.detach_deleted_source_log(ctx.tv, locked.pk)
            ctx.tv.refresh_from_db()
        for row in ctx.facts.seasons.values():
            if locked.pk in (row.rating_source_id, row.like_source_id):
                single_weight.detach_deleted_source_log(row, locked.pk)
        single_weight._delete_diary_activity(user, locked)
        _audit_diary(locked, "diary_deleted")
        consumed_at = locked.consumed_at
        locked.delete()
        single_weight._queue_statistics(user.pk, consumed_at)
        ctx.changed = True
        left = _after_removal(ctx) if ctx.tv is not None else False
        return {"changed": True, "left_library": left}

    return _perform(
        user,
        show_item,
        "delete_log",
        body,
        entry_point=entry_point,
        mutation_id=mutation_id,
        season_number=season_number,
        episode_number=episode_number,
    )


def log_info(entry):
    """Return the diary payload's tv_log for a TV log: what it covers and would unmark.

    Reads stored rows only, never the provider (TV-207): a log whose show Item doesn't
    exist yet has no tracking to report, so it gets None.
    """
    if not supports(entry.item):
        return None
    if entry.item.media_type == MediaTypes.TV.value:
        show_item = entry.item
    else:
        show_item = Item.objects.filter(
            media_id=entry.item.media_id,
            source=entry.item.source,
            media_type=MediaTypes.TV.value,
            season_number=None,
            episode_number=None,
        ).first()
        if show_item is None:
            return None
    view = _View.load(entry.user, show_item, tv_listing.stored_listing(show_item))
    return view.log_info(entry.pk)


# ---------------------------------------------------------------------------------
# Entry-point helpers
# ---------------------------------------------------------------------------------


def webhook_event(user, show_item, season_number, episode_number, *, event, at):
    """Apply a Plex, Jellyfin or Emby event (TV-802); never raises for a rejected event.

    "watched" ticks the episode at the event time, "unplayed" unticks it, and "play"
    counts as choosing Watching (nothing changes where Watching is unavailable).
    """
    try:
        if event == "watched":
            return tick(user, show_item, season_number, episode_number, at=at, entry_point=WEBHOOK)
        if event == "unplayed":
            return untick(user, show_item, season_number, episode_number, entry_point=WEBHOOK)
        if event == "play":
            return _perform(
                user,
                show_item,
                f"choose:{WATCHING}",
                lambda ctx: _do_choose(ctx, WATCHING, quiet=True),
                entry_point=WEBHOOK,
            )
        logger.warning("Ignored an unknown TV webhook event %r", event)
    except (TVConflict, TVInvalid, TVDetailsUnavailable) as error:
        logger.info(
            "Acknowledged a rejected TV webhook %s for %s S%sE%s: %s",
            event,
            show_item,
            season_number,
            episode_number,
            getattr(error, "message", error),
        )
    return None


def _import_facts(ctx, overwritten):
    """Turn an import's stored statuses into facts (TV-800)."""
    tv = ctx.tv
    if overwritten:
        if any(not log.stamped for log in ctx.facts.logs):
            # Logs from before the contract beside logs written under it: convert them
            # first, so they get their coverage and fills too (TV-801, TV-800).
            _apply_plan(ctx, _plan(ctx, include_rule=False))
        plan = _plan(ctx, include_rule=False, exact_datetime_first=False, rebuild=True)
        _apply_plan(ctx, plan)
    calc = ctx.calc
    positions = []
    for number, row in sorted(ctx.facts.seasons.items()):
        if row.status == COMPLETED:
            positions.extend(_level_positions(calc, number))
    if tv.status == COMPLETED:
        positions.extend(calc.regular_aired)
    positions = [position for position in dict.fromkeys(positions) if position not in calc.watched_pass]
    if positions:
        ctx.add_fills(positions)
    if tv.status in HOLDING_CHOICES or tv.status == WATCHING:
        if tv.choice != tv.status:
            ctx.set_tv(choice=tv.status, choice_at=ctx.now)
    ctx.reload()
    ctx.changed = True


def reconcile_imported(user, show_items, *, overwritten_show_item_ids=()):
    """Work out each imported show once, in its own transaction (TV-800).

    A show the import created (or overwrote) gets its imported statuses as facts: a
    Completed show or season becomes a mark with its missing aired regular episodes
    filled; show Planning, Paused, Dropped and In progress become the choice; season
    statuses other than Completed are ignored; overwritten logs get their viewings and
    fills back. A show whose listing is unavailable is left for the hourly job.
    """
    overwritten = {int(value) for value in overwritten_show_item_ids}
    for show_item in show_items:
        try:
            listing = tv_listing.get_listing(show_item)
        except TVDetailsUnavailable:
            logger.warning("TV details unavailable; %s is left for the hourly job", show_item)
            TV.objects.filter(user=user, item=show_item).update(reconciled_at=None)
            continue
        try:
            items = _all_items(show_item, listing, user)
            with _writing(), transaction.atomic():
                tv = TV.objects.select_for_update().filter(user=user, item=show_item).first()
                if tv is None:
                    continue
                fresh = show_item.pk in overwritten or (tv.reconciled_at is None and tv.state_version == 0)
                if fresh and not tv.legacy_pending:
                    stamped = [
                        _stamped(value)
                        for value in DiaryEntry.objects.filter(_family(show_item), user=user).values_list(
                            "progress_snapshot",
                            flat=True,
                        )
                    ]
                    if not all(stamped) and not (show_item.pk in overwritten and any(stamped)):
                        # Logs from before the contract, and none written under it: the
                        # show is legacy, overwritten or not, as events.tv_airing's retry
                        # decides when the listing was unreachable (TV-801). The repair or
                        # the user's next write converts the logs.
                        TV.objects.filter(pk=tv.pk).update(legacy_pending=True)
                        continue
                if tv.legacy_pending:
                    continue
                ctx = _Ctx(user, show_item, listing, IMPORT, items, tv)
                if fresh:
                    _import_facts(ctx, show_item.pk in overwritten)
                _reconcile(ctx)
        except Exception:
            logger.exception("Could not work out imported show %s", show_item)


def refresh_after_calendar(show_item):
    """Force a fresh listing, then work out every non-legacy user's row (JOB)."""
    try:
        listing = tv_listing.get_listing(show_item, max_age=timedelta(0))
    except (TVDetailsUnavailable, *tv_listing.PROVIDER_FAILURES):
        logger.warning("Could not refresh the episode lists of %s", show_item)
        return
    for tv_id in TV.objects.filter(item=show_item, legacy_pending=False).values_list("pk", flat=True):
        try:
            _lock_and_reconcile(tv_id, entry_point=JOB, listing=listing)
        except Exception:
            logger.exception("Could not work out TV row %s after the calendar refresh", tv_id)


def refresh_due(tv):
    """Work out a show whose next episode may have aired (the hourly job). Returns changed.

    Uses the stored listing; when the next episode is known only from the provider's
    next episode to air and its season isn't stored, that season is fetched first.
    """
    if tv.legacy_pending:
        return False
    show_item = tv.item
    listing = tv_listing.stored_listing(show_item)
    if listing is None:
        try:
            listing = tv_listing.get_listing(show_item)
        except TVDetailsUnavailable:
            return False
    next_to_air = listing.next_episode_to_air
    if next_to_air is not None and next_to_air[1] is not None and not listing.is_listed(next_to_air[0], next_to_air[1]):
        try:
            listing = tv_listing.get_listing(show_item, seasons=[next_to_air[0]], max_age=timedelta(0))
        except TVDetailsUnavailable:
            logger.warning("Could not fetch the next season of %s", show_item)
    try:
        return bool(_lock_and_reconcile(tv.pk, entry_point=JOB, listing=listing))
    except TVDetailsUnavailable:
        return False


# ---------------------------------------------------------------------------------
# Model hooks (called only from app.tv_signals)
# ---------------------------------------------------------------------------------


class _PendingReconciles:
    """Hook work queued in one transaction, shared by its on-commit callbacks."""

    def __init__(self):
        self.tv_ids = set()
        self.season_ids = set()
        self.removed_season_ids = set()
        self.new_tv_ids = set()
        self.status_writes = []

    def empty(self):
        return not (
            self.tv_ids or self.season_ids or self.removed_season_ids or self.new_tv_ids or self.status_writes
        )

    def take(self):
        taken = _PendingReconciles()
        taken.tv_ids, self.tv_ids = self.tv_ids, set()
        taken.season_ids, self.season_ids = self.season_ids, set()
        taken.removed_season_ids, self.removed_season_ids = self.removed_season_ids, set()
        taken.new_tv_ids, self.new_tv_ids = self.new_tv_ids, set()
        taken.status_writes, self.status_writes = self.status_writes, []
        return taken


class _PendingCallback:
    """One on-commit callback per queued change; the first to run drains the batch.

    Each change registers its own callback, so a test's captureOnCommitCallbacks sees
    it, while the shared batch still works each show out once per commit.
    """

    def __init__(self, batch):
        self.batch = batch

    def __call__(self):
        if not self.batch.empty():
            _run_pending(self.batch.take())


def _queue(*, tv_id=None, season_id=None, removed_season_id=None, new_tv_id=None, status_write=None):
    connection = transaction.get_connection()
    batch = None
    if connection.in_atomic_block:
        for entry in connection.run_on_commit:
            if isinstance(entry[1], _PendingCallback):
                batch = entry[1].batch
                break
    if batch is None:
        batch = _PendingReconciles()
    if tv_id is not None:
        batch.tv_ids.add(tv_id)
    if season_id is not None:
        batch.season_ids.add(season_id)
    if removed_season_id is not None:
        batch.removed_season_ids.add(removed_season_id)
    if new_tv_id is not None:
        batch.new_tv_ids.add(new_tv_id)
    if status_write is not None:
        batch.status_writes.append(status_write)
    # Outside a transaction on_commit runs the callback at once.
    transaction.on_commit(_PendingCallback(batch), robust=True)


def _run_pending(pending):
    """Run queued hook work: legacy checks, written statuses, then one reconcile per show."""
    try:
        tv_ids = set(pending.tv_ids)
        if pending.season_ids:
            tv_ids.update(
                Season.objects.filter(pk__in=pending.season_ids).values_list("related_tv_id", flat=True),
            )
        for tv_id in sorted(pending.new_tv_ids):
            tv = TV.objects.select_related("item").filter(pk=tv_id).first()
            if tv is None:
                continue
            snapshots = DiaryEntry.objects.filter(_family(tv.item), user_id=tv.user_id).values_list(
                "progress_snapshot",
                flat=True,
            )
            if any(not _stamped(value) for value in snapshots):
                TV.objects.filter(pk=tv_id).update(legacy_pending=True)
                tv_ids.discard(tv_id)
            else:
                tv_ids.add(tv_id)
        for model_name, row_id, status in pending.status_writes:
            _apply_hook_status(model_name, row_id, status)
        removed = {}
        for tv_id, season_number in Season.objects.filter(pk__in=pending.removed_season_ids).values_list(
            "related_tv_id",
            "item__season_number",
        ):
            removed.setdefault(tv_id, set()).add(season_number)
        for tv_id in sorted(tv_ids | set(removed)):
            _hook_reconcile(tv_id, removed.get(tv_id, ()))
    except Exception:
        logger.exception("TV model hooks failed")


def _hook_reconcile(tv_id, removed_seasons=()):
    """Work a show out once after model writes; removed_seasons lost a viewing."""
    try:
        if removed_seasons:
            _lock_and_reconcile_after_removal(tv_id, removed_seasons)
        else:
            _lock_and_reconcile(tv_id, entry_point=HOOK)
    except Exception:
        logger.exception("Could not work out TV row %s after a model write; the hourly job will", tv_id)
        TV.objects.filter(pk=tv_id).update(reconciled_at=None)


def _lock_and_reconcile_after_removal(tv_id, season_numbers):
    """A viewing deleted outside this module: apply TV-205, TV-208 and TV-003, then reconcile."""
    with _writing(), transaction.atomic():
        tv = TV.objects.select_for_update().select_related("item", "user").filter(pk=tv_id).first()
        if tv is None or tv.legacy_pending:
            return
        listing = tv_listing.stored_listing(tv.item)
        ctx = _Ctx(tv.user, tv.item, listing, HOOK, ({}, {}), tv)
        ctx.touched_seasons.update(season_numbers)
        if _after_removal(ctx):
            return
        if listing is None:
            # Keep the removal; the hourly job works the show out once it has a copy.
            ctx.flush_tv()
            TV.objects.filter(pk=tv_id).update(reconciled_at=None)
            return
        _reconcile(ctx)


def _apply_hook_status(model_name, row_id, status):
    model = TV if model_name == "tv" else Season
    row = model.objects.select_related("item", "user").filter(pk=row_id).first()
    if row is None:
        return
    tv = row if model is TV else TV.objects.filter(pk=row.related_tv_id).first()
    if tv is None or tv.legacy_pending:
        return
    try:
        apply_written_state(row.user, row.item, {"status": status}, entry_point=HOOK)
    except Exception:
        logger.exception("Could not apply a written TV status to %s", row)


def on_episode_saved(episode, created):
    """An Episode row saved outside this module: a fact as saved; work the show out."""
    if is_writing():
        return
    _queue(season_id=episode.related_season_id)


def on_episode_deleted(episode, origin):
    """An Episode deleted outside this module; cascades from other models do nothing."""
    if is_writing():
        return
    from django.db.models import QuerySet  # noqa: PLC0415

    is_episode_origin = isinstance(origin, Episode) or (
        isinstance(origin, QuerySet) and origin.model is Episode
    )
    if not is_episode_origin:
        return
    _queue(removed_season_id=episode.related_season_id)


def on_status_written(media, previous_status):
    """A TV or Season status saved outside this module means its action (TV-207)."""
    if is_writing():
        return
    if isinstance(media, TV):
        _queue(status_write=("tv", media.pk, media.status), tv_id=media.pk)
    else:
        _queue(status_write=("season", media.pk, media.status), tv_id=media.related_tv_id)


def on_tv_created(tv):
    """A TV row created outside this module records no choice; check the legacy rule."""
    if is_writing():
        return
    _queue(new_tv_id=tv.pk)


# ---------------------------------------------------------------------------------
# Payloads (plan 2.3)
# ---------------------------------------------------------------------------------


def _empty_listing(show_item):
    return tv_listing.ShowListing(
        show_item=show_item,
        season_numbers=(),
        episodes={},
        next_episode_to_air=None,
        returning=False,
        any_air_dates=True,
    )


class _View:
    """Read-only state of one user's show for payloads."""

    def __init__(self, user, show_item, tv, listing, facts, today, *, unknown=False):
        self.user = user
        self.show_item = show_item
        self.tv = tv
        self.listing = listing
        self.facts = facts
        self.today = today
        self.unknown_listing = unknown
        self.calc = _calculate(facts, None if unknown else listing, today, tv=tv, user=user)
        self.history_calc = None

    @classmethod
    def load(cls, user, show_item, listing):
        tv = TV.objects.filter(user=user, item=show_item).first()
        unknown = listing is None
        if unknown:
            listing = _empty_listing(show_item)
        facts = _load_facts(user, show_item, tv)
        today = user_calendar.today(user)
        legacy = tv is not None and tv.legacy_pending
        if not unknown and (legacy or any(not log.stamped for log in facts.logs)):
            now = user_calendar.now()
            plan = _plan(
                _ReadCtx(user=user, tv=tv, listing=listing, today=today, now=now, facts=facts),
                include_rule=legacy,
            )
            facts = _apply_plan_in_memory(facts, plan, user, now)
            if tv is not None and plan.choice:
                tv = copy.copy(tv)
                tv.choice = plan.choice
                tv.choice_at = now
        return cls(user, show_item, tv, listing, facts, today, unknown=unknown)

    @classmethod
    def from_ctx(cls, ctx):
        view = cls.__new__(cls)
        view.user = ctx.user
        view.show_item = ctx.show_item
        view.tv = ctx.tv
        view.listing = ctx.listing
        view.facts = ctx.facts
        view.today = ctx.today
        view.unknown_listing = ctx.listing is None
        view.calc = ctx.calc
        view.history_calc = None
        return view

    # Helpers

    def history(self):
        if self.history_calc is None:
            self.history_calc = _calculate(
                self.facts,
                None if self.unknown_listing else self.listing,
                self.today,
                tv=self.tv,
                user=self.user,
                ignore_rewatch=True,
            )
        return self.history_calc

    @property
    def in_library(self):
        return self.tv is not None and self.tv.pk is not None

    def status(self):
        if not self.in_library:
            return None
        return self.calc.status or self.tv.status

    def season_row(self, number):
        return self.facts.seasons.get(number)

    def season_rating(self, number):
        row = self.season_row(number)
        return _rating_string(row.score) if row is not None else None

    def premiere_message(self, season_number=None):
        if season_number is None:
            premiere = self.calc.premiere
            if premiere is None:
                return MSG_NOTHING_AIRED
            number, day = premiere
        else:
            if season_number == 0:
                return MSG_NOTHING_AIRED
            season = self.calc.seasons.get(season_number)
            number = season_number
            dated = [episode.air_date for episode in (season.episodes if season else ()) if episode.air_date]
            day = min(dated) if dated else (self.listing.season_air_dates.get(season_number) if self.listing else None)
        if day is None:
            return MSG_NOTHING_AIRED
        return f"S{number} premieres {_short_date(day, self.today)}"

    # Eyes (TV-300, TV-302)

    def eye(self, season_number=None):
        calc = self.calc
        if season_number is not None:
            season = calc.seasons.get(season_number)
            if season is None or not season.listed:
                return {"filled": False, "tap": "blocked", "count": None, "prompt": None, "message": "This season is no longer listed."}
            if not season.aired:
                return {"filled": False, "tap": "nothing_aired", "count": None, "prompt": None, "message": self.premiere_message(season_number)}
            filled = season.all_watched
            if not filled:
                count = season.aired_count - season.watched_count
                return {"filled": False, "tap": "mark", "count": count, "prompt": None, "message": None}
        else:
            if not calc.regular_aired:
                return {"filled": False, "tap": "nothing_aired", "count": None, "prompt": None, "message": self.premiere_message()}
            if not calc.all_watched:
                if calc.rewatch is not None:
                    return {
                        "filled": False,
                        "tap": "mark_confirm",
                        "count": calc.aired_count - calc.watched_count,
                        "prompt": "Mark the rest of this rewatch watched?",
                        "message": None,
                    }
                count = calc.aired_count
                prompt = (
                    "Mark 1 aired episode watched?" if count == 1 else f"Mark all {count} aired episodes watched?"
                )
                return {"filled": False, "tap": "mark_confirm", "count": count, "prompt": prompt, "message": None}
        case = _eye_case(self, season_number, calc)
        if case["case"] == 1:
            return {"filled": True, "tap": "blocked", "count": None, "prompt": None, "message": case["message"]}
        if case["case"] == 2:
            return {"filled": True, "tap": "unmark_marks", "count": case["count"], "prompt": None, "message": None}
        if case["case"] == 3 and case["blocked"]:
            return {"filled": True, "tap": "blocked", "count": None, "prompt": None, "message": case["blocked"]}
        count = case.get("count") or 0
        prompt = "Mark 1 episode unwatched?" if count == 1 else f"Mark all {count} episodes unwatched?"
        return {"filled": True, "tap": "unmark_confirm", "count": count, "prompt": prompt, "message": None}

    # Composer (TV-404)

    def composer_target(self, level, season_number, episode_number=None, *, date_override=None):
        calc = self.calc
        if level == SHOW:
            target_id = "show"
            title = "Whole show"
            available = bool(calc.regular_aired)
            reason = None if available else MSG_NOTHING_AIRED
            rating = _rating_string(self.tv.score) if self.in_library else None
            liked = self.facts.liked(SHOW)
        elif level == SEASON:
            target_id = f"season:{season_number}"
            title = _season_label(season_number)
            season = calc.seasons.get(season_number)
            available = bool(season and season.aired)
            reason = None if available else MSG_NOTHING_AIRED
            rating = self.season_rating(season_number)
            liked = self.facts.liked(SEASON, season_number)
        else:
            target_id = f"episode:{season_number}:{episode_number}"
            listed = self.listing.episode(season_number, episode_number) if self.listing else None
            title = (listed.title if listed and listed.title else f"Episode {episode_number}")
            available = bool(
                listed
                and self.listing.is_aired(
                    season_number,
                    episode_number,
                    today=self.today,
                    has_viewing=(season_number, episode_number) in calc.viewed_any,
                ),
            )
            reason = None if available else MSG_EPISODE_UNAIRED
            rating = None
            liked = self.facts.liked(EPISODE, season_number, episode_number)
        is_rewatch = _rewatch_default(self, level, season_number, episode_number, calc)
        claims = None
        day = self.today
        if level == EPISODE and (not is_rewatch or _rewatch_covers(calc, EPISODE, season_number)):
            # During a rewatch that covers the episode the toggle never stops a claim.
            claim = _claimable_tick(self, season_number, episode_number, calc)
            if claim is not None:
                claims = _real_id(claim.id)
                day = claim.day or day
        if date_override is not None:
            day = date_override
        return {
            "id": target_id,
            "level": level,
            "season_number": season_number,
            "episode_number": episode_number,
            "title": title,
            "date": _iso(day),
            "rating": rating,
            "liked": bool(liked),
            "is_rewatch": bool(is_rewatch),
            "claims_viewing_id": claims,
            "available": available,
            "reason": reason,
        }

    def composer(self, page_level=SHOW, season_number=None, episode_number=None):
        calc = self.calc
        if page_level == EPISODE:
            target = self.composer_target(EPISODE, season_number, episode_number)
            return {"default_target": target["id"], "targets": [target]}
        if page_level == SEASON:
            target = self.composer_target(SEASON, season_number)
            return {"default_target": target["id"], "targets": [target]}
        numbers = [number for number in calc.regular if calc.seasons[number].aired]
        if 0 in calc.seasons and calc.seasons[0].listed and calc.seasons[0].aired:
            numbers.append(0)
        targets = [self.composer_target(SEASON, number) for number in numbers]
        show = self.composer_target(SHOW, None)
        targets.append(show)
        # The show page logs the whole show; while only specials have aired, Specials.
        default = show["id"] if show["available"] else targets[0]["id"]
        return {"default_target": default, "targets": targets}

    # Actions (plan 2.3.6)

    def show_actions(self):  # noqa: C901
        calc = self.calc
        status = self.status()
        selected = SELECTED_ACTIONS.get(status)
        actions = []
        reasons = {}
        if selected != "watching":
            reason = _choice_unavailable(calc, self.tv, WATCHING)
            if reason is None:
                actions.append("watching")
                if status == PAUSED:
                    actions.append("resume")
            else:
                reasons["watching"] = reason
        if selected != "planning":
            if calc.recorded:
                reasons["planning"] = MSG_STARTED
            else:
                actions.append("planning")
        for action in ("pause", "drop"):
            if selected != action:
                actions.append(action)
        if self.history().finished:
            if calc.rewatch is not None:
                reasons["rewatch"] = MSG_REWATCH_IN_PROGRESS
            else:
                actions.append("rewatch")
        if calc.rewatch is not None:
            actions.append("stop_rewatch")
        if calc.any_aired:
            actions.append("log")
        else:
            reasons["log"] = MSG_NOTHING_AIRED
        if self.in_library:
            if self.facts.logs:
                reasons["remove"] = MSG_DELETE_SHOW_LOGS
            else:
                actions.append("remove")
        stars_reason = _stars_unavailable(calc, None)
        if stars_reason is None:
            actions.append("rate")
        else:
            reasons["rate"] = stars_reason
        return actions, reasons

    def season_actions(self, season_number):
        calc = self.calc
        season = calc.seasons.get(season_number)
        actions = []
        reasons = {}
        if season is not None and season.aired:
            actions.append("log_season")
        else:
            reasons["log_season"] = MSG_NOTHING_AIRED
        history = self.history().seasons.get(season_number)
        if history is not None and history.finished:
            if calc.rewatch is not None:
                reasons["rewatch_season"] = MSG_REWATCH_IN_PROGRESS
            else:
                actions.append("rewatch_season")
        stars_reason = _stars_unavailable(calc, season_number)
        if stars_reason is None:
            actions.append("rate")
        else:
            reasons["rate"] = stars_reason
        return actions, reasons

    # States

    def season_state(self, season_number, *, detail=True):
        calc = self.calc
        season = calc.seasons.get(season_number)
        row = self.season_row(season_number)
        listed = bool(season and season.listed)
        loaded = bool(season and season.loaded)
        started = bool(season and season.started)
        status = None
        if started and self.in_library:
            status = _season_status(calc, season_number, self.tv.choice) or (row.status if row else None)
        state = season.state if season is not None else "unknown"
        if not listed:
            state = "unknown"
        payload = {
            "season_number": season_number,
            "is_specials": season_number == 0,
            "listed": listed,
            "started": started,
            "status": status,
            "state": state,
            "watched_count": season.watched_count if loaded else None,
            "aired_count": season.aired_count if loaded else None,
            "episode_count": season.episode_count if loaded else None,
            "first_gap_episode": season.first_gap if season else None,
            "next_air_date": _iso(season.next_air_date) if season else None,
            "rewatching": bool(season and season.rewatching),
            "rating": _rating_string(row.score) if row is not None else None,
            "liked": self.facts.liked(SEASON, season_number),
        }
        if not detail:
            return payload
        actions, reasons = self.season_actions(season_number)
        liked = payload["liked"]
        payload.update(
            {
                "rating_source_diary_entry_id": row.rating_source_id if row is not None else None,
                "like_source_diary_entry_id": row.like_source_id if row is not None else None,
                "like_is_independent": (
                    row.like_is_independent if row is not None else (True if liked else None)
                ),
                "log_count": sum(
                    1 for log in self.facts.logs if log.level == SEASON and log.season == season_number
                ),
                "eye": self.eye(season_number),
                "available_actions": actions,
                "action_reasons": reasons,
            },
        )
        return payload

    def episode_state(self, season_number, episode_number, *, detail=False):
        calc = self.calc
        position = (season_number, episode_number)
        listed_episode = self.listing.episode(season_number, episode_number) if self.listing else None
        listed = listed_episode is not None
        aired = listed and self.listing.is_aired(
            season_number,
            episode_number,
            today=self.today,
            has_viewing=position in calc.viewed_any,
        )
        watched = position in calc.watched_pass
        viewings = [viewing for viewing in self.facts.viewings if viewing.pos == position]
        logs = [log for log in self.facts.logs if log.level == EPISODE and (log.season, log.episode) == position]
        untick_viewing_id = None
        untick_message = None
        if watched:
            target, message = _untick_target(self, season_number, episode_number, calc)
            untick_viewing_id = _real_id(target.id) if target is not None else None
            untick_message = message
        rated = [log for log in logs if log.rating is not None]
        newest = max(rated, key=lambda log: (log.day, log.created_at)) if rated else None
        can_mark_up_to = bool(aired and season_number >= 1 and position in set(calc.regular_aired))
        prompt = None
        if can_mark_up_to:
            unwatched = [
                candidate
                for candidate in calc.regular_aired
                if candidate <= position and candidate not in calc.watched_pass
            ]
            if unwatched:
                prompt = f"Mark {_episode_range(unwatched[0], position)} watched?"
        payload = {
            "season_number": season_number,
            "episode_number": episode_number,
            "is_special": season_number == 0,
            "listed": listed,
            "aired": bool(aired),
            "air_date": _iso(listed_episode.air_date) if listed_episode else None,
            "watched": watched,
            "viewing_count": len(viewings),
            "log_count": len(logs),
            "can_tick": bool(aired and listed and not watched),
            "untick_viewing_id": untick_viewing_id,
            "untick_message": untick_message,
            "can_mark_up_to": can_mark_up_to,
            "mark_up_to_prompt": prompt,
            "user_rating": _rating_string(newest.rating) if newest else None,
            "liked": self.facts.liked(EPISODE, season_number, episode_number),
            "is_up_next": calc.up_next == position,
        }
        if detail:
            if aired and listed:
                payload["available_actions"] = ["log_episode"]
                payload["action_reasons"] = {}
            else:
                payload["available_actions"] = []
                payload["action_reasons"] = {
                    "log_episode": (
                        MSG_EPISODE_UNAIRED
                        if listed
                        else f"This episode isn't listed for {_season_label(season_number)}."
                    ),
                }
            payload["composer"] = self.composer(EPISODE, season_number, episode_number)
        return payload

    def show_state(self, *, detail=True, page=None):
        calc = self.calc
        tv = self.tv if self.in_library else None
        status = self.status()
        rewatch = None
        if calc.rewatch is not None:
            rewatch = {
                "scope": "show" if calc.rewatch.season is None else "season",
                "season_number": calc.rewatch.season,
                "started_at": _iso(calc.rewatch.started_at),
            }
        up_next = None
        if calc.up_next is not None:
            up_next = {
                "season_number": calc.up_next[0],
                "episode_number": calc.up_next[1],
                "title": self.listing.episode_title(*calc.up_next) if self.listing else None,
                "air_date": _iso(calc.up_next_air_date),
                "is_new": calc.up_next_is_new,
                # The show page shows up next as a row of its season's episode list.
                "image_url": self.listing.episode_still(*calc.up_next) if self.listing else None,
            }
        next_airing = None
        if calc.next_airing is not None:
            next_airing = {
                "season_number": calc.next_airing[0],
                "episode_number": calc.next_airing[1],
                "air_date": _iso(calc.next_airing[2]),
            }
        premiere = None
        if calc.premiere is not None:
            premiere = {"season_number": calc.premiere[0], "air_date": _iso(calc.premiere[1])}
        payload = {
            "version": int(tv.state_version) if tv is not None else 0,
            "in_library": tv is not None,
            "status": status,
            "choice": (tv.choice or None) if tv is not None else None,
            "started": calc.started if tv is not None else False,
            "finished": calc.finished,
            "caught_up": calc.caught_up,
            "watched_count": None if self.unknown_listing else calc.watched_count,
            "aired_count": None if self.unknown_listing else calc.aired_count,
            "up_next": up_next,
            "next_airing": next_airing,
            "premiere": premiere,
            "rewatch": rewatch,
            "last_watched_on": _iso(calc.last_watched_on),
        }
        if not detail:
            return payload
        actions, reasons = self.show_actions()
        liked = self.facts.liked(SHOW)
        page_level = SHOW
        page_season = None
        page_episode = None
        if page is not None and page.media_type == MediaTypes.SEASON.value:
            page_level, page_season = SEASON, page.season_number
        elif page is not None and page.media_type == MediaTypes.EPISODE.value:
            page_level, page_season, page_episode = EPISODE, page.season_number, page.episode_number
        payload.update(
            {
                "selected_action": SELECTED_ACTIONS.get(status),
                "remove_count": _remove_count(self.facts),
                "rating_source_diary_entry_id": tv.rating_source_id if tv is not None else None,
                "like_source_diary_entry_id": tv.like_source_id if tv is not None else None,
                "like_is_independent": (
                    tv.like_is_independent if tv is not None else (True if liked else None)
                ),
                "eye": self.eye(None),
                "available_actions": actions,
                "action_reasons": reasons,
                "composer": self.composer(page_level, page_season, page_episode),
                "season": self.season_state(page_season) if page_level == SEASON else None,
                "episode": (
                    self.episode_state(page_season, page_episode, detail=True) if page_level == EPISODE else None
                ),
            },
        )
        # Keep the documented key order (plan 2.3.1).
        order = [
            "version",
            "in_library",
            "status",
            "choice",
            "selected_action",
            "started",
            "finished",
            "caught_up",
            "watched_count",
            "aired_count",
            "up_next",
            "next_airing",
            "premiere",
            "rewatch",
            "last_watched_on",
            "remove_count",
            "rating_source_diary_entry_id",
            "like_source_diary_entry_id",
            "like_is_independent",
            "eye",
            "available_actions",
            "action_reasons",
            "composer",
            "season",
            "episode",
        ]
        return {key: payload[key] for key in order}

    def log_info(self, entry_id):
        log = next((log for log in self.facts.logs if log.id == entry_id), None)
        if log is None:
            return None
        owned, unmarked, _ = _log_effect(self, log, self.calc)
        own = next((viewing for viewing in owned if viewing.kind in (TICK, LOG)), None)
        if log.level == EPISODE:
            covers_count = 1
        else:
            covers_count = sum(len(episodes) for episodes in log.covers.values())
        return {
            "level": log.level,
            "season_number": log.season,
            "episode_number": log.episode,
            "unmarks_count": len(unmarked),
            "covers_count": covers_count,
            "viewing_id": _real_id(own.id) if own is not None else None,
            "state_version": int(self.tv.state_version) if self.in_library else 0,
        }


def _payload_listing(show_item, listing):
    if listing is not None:
        return listing
    try:
        return tv_listing.get_listing(show_item)
    except TVDetailsUnavailable:
        return tv_listing.stored_listing(show_item)


def state_payload(user, item, *, detail=True, listing=None):
    """Return the TVShowState (plan 2.3.1) for a show, season or episode page.

    A legacy show's state is what its first write would store (plan_legacy, no writes).
    With detail False, the list variant's keys only.
    """
    show_item = _resolve_show_item(item)
    view = _View.load(user, show_item, _payload_listing(show_item, listing))
    return view.show_state(detail=detail, page=item if detail else None)


def write_envelope(result, item=None, *, page=None):
    """Return the write envelope (plan 2.3.5) for a WriteResult.

    item is the page the write came from (a season or episode Item adds its state);
    otherwise season and episode follow the write's own season and episode. page, when
    given, scopes only `tv` to the page the app showed (its season or episode state and
    its composer, TV-404), and season and episode stay the write's own. A season page
    also gets season_episodes, its listed episodes' states, so its rows show the write
    at once, an eye's undo included (TV-302, TV-705).
    """
    view = _View.load(result.user, result.show_item, _payload_listing(result.show_item, result.listing))
    season_number = result.season_number
    episode_number = result.episode_number
    if item is not None and item.media_type == MediaTypes.SEASON.value:
        season_number, episode_number = item.season_number, None
    elif item is not None and item.media_type == MediaTypes.EPISODE.value:
        season_number, episode_number = item.season_number, item.episode_number
    envelope = {
        "tv": view.show_state(detail=True, page=page if page is not None else item),
        "season": view.season_state(season_number) if season_number is not None else None,
        "episode": (
            view.episode_state(season_number, episode_number, detail=True)
            if season_number is not None and episode_number is not None
            else None
        ),
        "changed": result.changed,
        "finished": result.finished,
        "earlier_unwatched": result.earlier_unwatched,
    }
    if page is not None and page.media_type == MediaTypes.SEASON.value:
        season = view.calc.seasons.get(page.season_number)
        envelope["season_episodes"] = [
            view.episode_state(page.season_number, listed.episode_number)
            for listed in (season.episodes if season is not None else ())
        ]
    return envelope


def list_payload(tv):
    """Return the list variant of TVShowState from the TV row's stored values only."""
    if tv is None:
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
            "up_next": None,
            "next_airing": None,
            "premiere": None,
            "rewatch": None,
            "last_watched_on": None,
        }
    legacy = tv.legacy_pending or tv.reconciled_at is None
    watched_count = tv.watched_count
    aired_count = tv.aired_count
    if legacy:
        primed = getattr(tv, "_tv_progress", None)
        watched_count, aired_count = primed if primed is not None else (None, None)
    up_next = None
    if not legacy and tv.up_next_season is not None:
        # Continue Watching primes the episode's title and still (prime_up_next_episodes).
        title, image_url = getattr(tv, "_tv_up_next_episode", (None, None))
        up_next = {
            "season_number": tv.up_next_season,
            "episode_number": tv.up_next_episode,
            "title": title,
            "air_date": _iso(tv.up_next_air_date),
            "is_new": tv.up_next_is_new,
            "image_url": image_url,
        }
    next_airing = None
    if not legacy and tv.next_air_season is not None:
        next_airing = {
            "season_number": tv.next_air_season,
            "episode_number": tv.next_air_episode,
            "air_date": _iso(tv.next_air_date),
        }
    premiere = None
    if not legacy and (aired_count or 0) == 0 and tv.next_air_season is not None:
        premiere = {"season_number": tv.next_air_season, "air_date": _iso(tv.next_air_date)}
    rewatch = None
    if tv.rewatch_started_at is not None:
        rewatch = {
            "scope": "show" if tv.rewatch_season_number is None else "season",
            "season_number": tv.rewatch_season_number,
            "started_at": _iso(tv.rewatch_started_at),
        }
    started = (
        tv.last_activity_at is not None
        if not legacy
        else tv.status in (WATCHING, COMPLETED, PAUSED, DROPPED)
    )
    return {
        "version": int(tv.state_version),
        "in_library": True,
        "status": tv.status,
        "choice": tv.choice or None,
        "started": bool(started),
        "finished": bool(tv.finished) if not legacy else tv.status == COMPLETED,
        "caught_up": bool(tv.caught_up) if not legacy else False,
        "watched_count": watched_count,
        "aired_count": aired_count,
        "up_next": up_next,
        "next_airing": next_airing,
        "premiere": premiere,
        "rewatch": rewatch,
        "last_watched_on": _iso(tv.last_watched_on),
    }


def _show_view(user, show_item, listing):
    return _View.load(user, show_item, _payload_listing(show_item, listing))


def season_card_states(user, show_item, listing):
    """Return {season number: card TVSeasonState} for the show page's listed seasons."""
    view = _show_view(user, show_item, listing)
    return {
        number: view.season_state(number, detail=False)
        for number, season in view.calc.seasons.items()
        if season.listed
    }


def episode_states(user, show_item, season_number, listing):
    """Return {episode number: TVEpisodeState} for a season page's listed episodes."""
    view = _show_view(user, show_item, listing)
    season = view.calc.seasons.get(int(season_number))
    if season is None:
        return {}
    return {
        listed.episode_number: view.episode_state(int(season_number), listed.episode_number)
        for listed in season.episodes
    }


def unlisted_seasons(user, show_item, listing):
    """Return tracked seasons the provider no longer lists (TV-103), after Specials."""
    view = _show_view(user, show_item, listing)
    result = []
    for number, season in sorted(view.calc.seasons.items()):
        if season.listed:
            continue
        has_anything = (
            season.started
            or number in view.facts.seasons
            or view.facts.liked(SEASON, number)
        )
        if not has_anything:
            continue
        result.append(
            {
                "season_number": number,
                "title": _season_label(number),
                "tv": view.season_state(number, detail=False),
            },
        )
    return result


def unlisted_episodes(user, show_item, season_number, listing):
    """Return the user's tracked episodes of a season the provider no longer lists."""
    view = _show_view(user, show_item, listing)
    season_number = int(season_number)
    numbers = {
        viewing.episode for viewing in view.facts.viewings if viewing.season == season_number
    }
    numbers.update(
        log.episode
        for log in view.facts.logs
        if log.level == EPISODE and log.season == season_number
    )
    numbers.update(
        episode
        for level, season, episode in view.facts.likes
        if level == EPISODE and season == season_number
    )
    result = []
    for number in sorted(numbers):
        if view.listing is not None and view.listing.is_listed(season_number, number):
            continue
        result.append(
            {
                "episode_number": number,
                "title": f"Episode {number}",
                "tv": view.episode_state(season_number, number),
            },
        )
    return result


def progress_payload(media):
    """Return {"kind": "episodes", "value": watched, "max": aired, "unit": "episode"} (TV-104).

    TV and Season rows read their stored counts; rows not yet worked out use the
    fallback prime_progress sets (or computes for this one row).
    """
    worked_out = (
        isinstance(media, TV) and not media.legacy_pending and media.reconciled_at is not None
    ) or (isinstance(media, Season) and media.is_started is not None)
    if worked_out:
        value, maximum = media.watched_count, media.aired_count
    else:
        primed = getattr(media, "_tv_progress", None)
        if primed is None:
            prime_progress([media])
            primed = getattr(media, "_tv_progress", (0, None))
        value, maximum = primed
    return {"kind": "episodes", "value": value or 0, "max": maximum, "unit": "episode"}


def _aired_from_stored(seasons_json, today, *, season_number=None):
    """Count listed episodes whose air date is on or before today from a stored listing."""
    if not seasons_json:
        return None
    count = 0
    found = False
    for key, entry in seasons_json.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        if season_number is None and number < 1:
            continue
        if season_number is not None and number != season_number:
            continue
        rows = entry.get("episodes") if isinstance(entry, dict) else None
        if rows is None:
            continue
        found = True
        for row in rows:
            air_date = parse_date(row[1]) if row and len(row) > 1 and row[1] else None
            if air_date is not None and air_date <= today:
                count += 1
    return count if found else None


def _today_for(user_id, calendar_timezone):
    """Return today on a user's calendar without loading the user row."""
    return user_calendar.today(SimpleNamespace(pk=user_id, calendar_timezone=calendar_timezone or ""))


def _json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


def prime_up_next_episodes(rows):
    """Set up next's title and still on TV rows from their stored listings, in one query.

    Continue Watching's touch-and-hold preview names the episode and shows its still
    (TV-701). Both come from the stored copy (TV-103), never the provider.
    """
    tv_rows = [row for row in rows if isinstance(row, TV) and row.up_next_season is not None]
    if not tv_rows:
        return
    stored = dict(
        TVListing.objects.filter(item_id__in={row.item_id for row in tv_rows}).values_list("item_id", "seasons"),
    )
    for row in tv_rows:
        row._tv_up_next_episode = tv_listing.stored_episode(
            _json(stored.get(row.item_id)),
            row.up_next_season,
            row.up_next_episode,
        )


def prime_progress(rows):
    """Set fallback progress on TV and Season rows not yet worked out, one query each kind.

    The fallback is the distinct regular episodes watched (any viewing) over the stored
    listing's aired count.
    """
    tv_rows = [
        row
        for row in rows
        if isinstance(row, TV) and (row.legacy_pending or row.reconciled_at is None)
    ]
    season_rows = [row for row in rows if isinstance(row, Season) and row.is_started is None]
    if tv_rows:
        listing_seasons = TVListing.objects.filter(item_id=OuterRef("item_id")).values("seasons")[:1]
        values = {
            pk: (watched, seasons, calendar)
            for pk, watched, seasons, calendar in TV.objects.filter(pk__in=[row.pk for row in tv_rows])
            .order_by()
            .annotate(
                tv_watched=Count(
                    "seasons__episodes__item",
                    filter=Q(seasons__episodes__item__season_number__gte=1),
                    distinct=True,
                ),
                tv_listing_seasons=Subquery(listing_seasons),
                tv_calendar=F("user__calendar_timezone"),
            )
            .values_list("pk", "tv_watched", "tv_listing_seasons", "tv_calendar")
        }
        for row in tv_rows:
            watched, seasons, calendar = values.get(row.pk, (0, None, ""))
            today = _today_for(row.user_id, calendar)
            row._tv_progress = (watched or 0, _aired_from_stored(_json(seasons), today))
    if season_rows:
        listing_seasons = TVListing.objects.filter(
            item__media_id=OuterRef("item__media_id"),
            item__source=OuterRef("item__source"),
            item__media_type=MediaTypes.TV.value,
        ).values("seasons")[:1]
        values = {
            pk: (watched, seasons, calendar, number)
            for pk, watched, seasons, calendar, number in Season.objects.filter(
                pk__in=[row.pk for row in season_rows],
            )
            .order_by()
            .annotate(
                tv_watched=Count("episodes__item", distinct=True),
                tv_listing_seasons=Subquery(listing_seasons),
                tv_calendar=F("user__calendar_timezone"),
                tv_season_number=F("item__season_number"),
            )
            .values_list("pk", "tv_watched", "tv_listing_seasons", "tv_calendar", "tv_season_number")
        }
        for row in season_rows:
            watched, seasons, calendar, number = values.get(row.pk, (0, None, "", None))
            today = _today_for(row.user_id, calendar)
            row._tv_progress = (
                watched or 0,
                _aired_from_stored(_json(seasons), today, season_number=number),
            )


__all__ = [
    "API_ENTRY_POINTS",
    "APP",
    "HOOK",
    "IMPORT",
    "JOB",
    "LEGACY_APP",
    "REPAIR",
    "WEB",
    "WEBHOOK",
    "LegacyPlan",
    "TVConflict",
    "TVDetailsUnavailable",
    "TVInvalid",
    "TVLogBlocks",
    "TVStateChanged",
    "WriteResult",
    "apply_legacy_rule",
    "apply_written_state",
    "choose",
    "convert_legacy_logs",
    "create_log",
    "delete_log",
    "delete_season_tracking",
    "ensure_tv",
    "episode_states",
    "list_payload",
    "log_info",
    "mark_earlier",
    "mark_up_to",
    "mark_watched",
    "on_episode_deleted",
    "on_episode_saved",
    "on_status_written",
    "on_tv_created",
    "plan_legacy",
    "prepare_items",
    "prime_progress",
    "prime_up_next_episodes",
    "progress_payload",
    "rebuild_log_viewings",
    "reconcile_imported",
    "reconcile_show",
    "refresh_after_calendar",
    "refresh_due",
    "remove",
    "season_card_states",
    "set_like",
    "set_rating",
    "start_rewatch",
    "state_payload",
    "stop_rewatch",
    "supports",
    "tick",
    "unlisted_episodes",
    "unlisted_seasons",
    "unmark",
    "untick",
    "update_log",
    "work_out",
    "write_envelope",
]
