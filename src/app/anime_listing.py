"""Stored copy of each anime title's numbers and episode rows (AN-100, AN-101).

MAL gives the total, whether the title finished airing and its format. AniList gives
episode titles and images, the schedule and the next episode. Every row and count reads
this copy, so no page ever waits long for AniList: a first open with no copy, or a read of
one gone stale, tries once with a short timeout; everything else answers from the copy,
and a failure keeps the numbers and backs off. The calendar job refreshes tracked titles
(events.calendar.anime), and only an open refreshes a title nobody tracks.

Manual titles are never stored: they have no provider, so they have no schedule, and
their total is whatever number their owner writes, kept on that person's own row
(anime_tracking, AN-100).

get_listing may call a provider, so callers fetch before they take a lock or open the
write's transaction (TV-705).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cached_property
from typing import NamedTuple

import requests
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_date

from app import user_calendar
from app.models import AnimeListing, Item, MediaTypes, Sources
from app.providers import anilist, mal, services
from app.tv_listing import TVDetailsUnavailable

logger = logging.getLogger(__name__)

UNAVAILABLE_MESSAGE = "Anime details are unavailable right now"
# How long a first open may wait on a provider (AN-101), and how long pages leave it
# alone after a failure.
FIRST_OPEN_TIMEOUT = 3
FAILURE_BACKOFF = timedelta(minutes=15)
# How long a read of a title stays good (as TV's do): a title still airing or announced
# can change any day, and a finished one hardly ever. The calendar job looks again at a
# title it doesn't otherwise pick up, and at a title's place in its series, as often.
STALE_ACTIVE = timedelta(days=1)
STALE_SETTLED = timedelta(days=7)
NUMBERS_FRESH = timedelta(days=1)
# AniList stops following some titles: Nintama Rantarou's last schedule entry is from 2020.
# Half a year on, a title's last entry no longer says how far it has aired (AN-100).
SCHEDULE_ABANDONED = timedelta(days=183)
STATUS_FINISHED = "Finished"
STATUS_AIRING = "Airing"
STATUS_UPCOMING = "Upcoming"
FORMAT_MOVIE = "movie"
PROVIDER_FAILURES = (
    services.ProviderAPIError,
    requests.exceptions.RequestException,
    AttributeError,
    KeyError,
    TypeError,
    ValueError,
)


class AnimeDetailsUnavailable(TVDetailsUnavailable):
    """No provider can be reached and Spine has no copy of the title (AN-100). Retryable."""

    def __init__(self, message=UNAVAILABLE_MESSAGE):
        """Keep the user copy."""
        super().__init__(message)


class Row(NamedTuple):
    """What AniList says about one episode."""

    number: int
    title: str
    image: str | None
    air_at: datetime | None


@dataclass(frozen=True)
class Listing:
    """A title's stored numbers and rows, and what they say has aired (AN-100)."""

    item: Item
    total: int | None = None
    provider_status: str = ""
    media_format: str = ""
    premiere_date: object = None
    next_episode: int | None = None
    next_air_at: datetime | None = None
    rows: tuple = ()
    next_title: dict | None = None
    # When MAL was last read; None means no copy backs this, only what's in memory.
    fetched_at: datetime | None = None
    # When AniList's rows were last read; None means the numbers stand alone.
    rows_fetched_at: datetime | None = None

    @property
    def is_movie(self):
        return self.media_format.strip().lower() == FORMAT_MOVIE

    @property
    def is_single(self):
        """A title whose total is one, or a movie with no total (AN-002)."""
        return self.total == 1 or (self.total is None and self.is_movie)

    @property
    def cap(self):
        """Return the highest episode the title has, or None with no total."""
        if self.total is not None:
            return self.total
        return 1 if self.is_single else None

    @property
    def is_upcoming(self):
        return self.provider_status == STATUS_UPCOMING

    @property
    def finished_airing(self):
        return self.provider_status == STATUS_FINISHED

    @property
    def awaiting_schedule(self):
        """MAL says it's airing and AniList hasn't answered: nobody knows how many aired (AN-100)."""
        return self.provider_status == STATUS_AIRING and self.rows_fetched_at is None

    @cached_property
    def by_number(self):
        return {row.number: row for row in self.rows}

    @cached_property
    def has_schedule(self):
        return self.next_episode is not None or any(row.air_at is not None for row in self.rows)

    @property
    def aired_is_known(self):
        """Whether Spine knows how many have aired (AN-102): a total or a schedule."""
        return not self.awaiting_schedule and (self.cap is not None or self.has_schedule)

    @property
    def may_have_aired(self):
        """Whether an episode past what's known can have aired, so it can be ticked (AN-101).

        With no numbers, its first row. With no total and nothing named next, AniList may
        just be behind, so its next one too.
        """
        if self.is_upcoming:
            return False
        return not self.aired_is_known or (self.cap is None and self.next_episode is None)

    def aired_through(self, now):
        """Return the last episode that has aired, by AN-100's first three and last rule.

        An episode's airing means every episode before it has too. The user's own
        viewings are the fourth rule, which only the caller knows.
        """
        if self.awaiting_schedule:
            return 0
        cap = self.cap
        through = 0
        if self.finished_airing and cap:
            through = cap
        for row in self.rows:
            if row.air_at is not None and row.air_at <= now:
                through = max(through, row.number)
        if self.next_episode is not None:
            passed = self.next_air_at is not None and self.next_air_at <= now
            through = max(through, self.next_episode if passed else self.next_episode - 1)
        if not self.has_schedule and not self.is_upcoming:
            # Spine can't tell, so everything the title lists counts (AN-100).
            through = cap or max(self.by_number, default=0)
        return min(through, cap) if cap else through

    def air_at(self, number):
        """Return when an episode airs, from the rows or the next episode, or None."""
        row = self.by_number.get(number)
        if row is not None and row.air_at is not None:
            return row.air_at
        return self.next_air_at if number == self.next_episode else None

    def next_airing(self, aired):
        """Return (episode, airing time or None) of the next one to air, or None.

        aired holds the numbers that have aired for this user. The named next episode
        comes first, then the schedule; a total with more to come names the next number.
        """
        if self.awaiting_schedule:
            return None
        cap = self.cap
        candidates = []
        if self.next_episode is not None and self.next_episode not in aired:
            candidates.append(self.next_episode)
        candidates.extend(row.number for row in self.rows if row.air_at is not None and row.number not in aired)
        candidates = [number for number in candidates if cap is None or number <= cap]
        if candidates:
            number = min(candidates)
            return number, self.air_at(number)
        if cap:
            number = next((number for number in range(1, cap + 1) if number not in aired), None)
            if number is not None:
                return number, self.air_at(number)
        return None

    def known_unaired(self, aired):
        """Return the unaired episodes Spine knows of, in order (AN-101)."""
        if self.awaiting_schedule:
            return []
        numbers = set()
        if self.cap:
            numbers.update(range(1, self.cap + 1))
        if self.next_episode is not None:
            numbers.add(self.next_episode)
        numbers.update(self.by_number)
        cap = self.cap
        return sorted(number for number in numbers if number not in aired and (cap is None or number <= cap))

    def premiere(self):
        """Return when the title premieres: MAL's date, or its first episode's airing."""
        if self.premiere_date is not None:
            return self.premiere_date
        return self.air_at(1)

    def title_of(self, number):
        row = self.by_number.get(number)
        return (row.title or None) if row is not None else None

    def image_of(self, number):
        row = self.by_number.get(number)
        return (row.image or None) if row is not None else None


def _listing_from_row(row):
    return Listing(
        item=row.item,
        total=row.total,
        provider_status=row.provider_status,
        media_format=row.media_format,
        premiere_date=row.premiere_date,
        next_episode=row.next_episode,
        next_air_at=row.next_air_at,
        rows=tuple(
            Row(
                number=int(stored[0]),
                title=stored[1] or "",
                image=stored[2] or None,
                air_at=datetime.fromtimestamp(stored[3], tz=UTC) if stored[3] else None,
            )
            for stored in row.rows or ()
        ),
        next_title=row.next_title,
        fetched_at=row.fetched_at,
        rows_fetched_at=row.rows_fetched_at,
    )


def _stored_row(item):
    return AnimeListing.objects.filter(item=item).select_related("item").first()


def _has_numbers(row):
    """Whether MAL's numbers ever reached this row. Until they do it's no copy (AN-100)."""
    return row is not None and row.fetched_at is not None


def stored_listing(item):
    """Return the stored copy without any network call, or None when there is none."""
    if item.source == Sources.MANUAL.value:
        return Listing(item=item)
    row = _stored_row(item)
    return _listing_from_row(row) if _has_numbers(row) else None


def _is_stale(row, read_at, now):
    """Whether a read needs doing: it never was, or it's older than suits the title (AN-100)."""
    if read_at is None:
        return True
    if row.provider_status == STATUS_FINISHED:
        return now - read_at >= STALE_SETTLED
    if row.next_air_at is not None and read_at < row.next_air_at <= now:
        return True
    return now - read_at >= STALE_ACTIVE


def get_listing(item):
    """Return the copy, trying once to read what it lacks or has let go stale.

    Each provider it needs gets one try with a short timeout, so nothing hangs on one. A
    failure keeps what is stored and backs off. When MAL can't be read and there's no copy,
    AnimeDetailsUnavailable is raised. Never call it while holding a lock.
    """
    if item.source == Sources.MANUAL.value:
        return Listing(item=item)
    now = user_calendar.now()
    row = _stored_row(item)
    has_numbers = _has_numbers(row)
    if has_numbers and row.retry_after is not None and row.retry_after > now:
        return _listing_from_row(row)
    want_numbers = not has_numbers or _is_stale(row, row.fetched_at, now)
    want_rows = row is None or _is_stale(row, row.rows_fetched_at, now)
    if not want_numbers and not want_rows:
        return _listing_from_row(row)
    numbers = None
    if want_numbers:
        try:
            numbers = mal.anime(item.media_id, timeout=FIRST_OPEN_TIMEOUT, retry_rate_limits=False)
        except PROVIDER_FAILURES as error:
            logger.warning("MAL can't be read for %s", item)
            if not has_numbers:
                raise AnimeDetailsUnavailable from error
    episodes = None
    if want_rows:
        try:
            episodes = anilist.anime_episodes(
                [item.media_id],
                timeout=FIRST_OPEN_TIMEOUT,
                retry_rate_limits=False,
            ).get(str(item.media_id), EMPTY_EPISODES)
        except PROVIDER_FAILURES:
            logger.warning("AniList can't be read for %s; keeping its numbers", item)
    failed = (want_numbers and numbers is None) or (want_rows and episodes is None)
    return _listing_from_row(_save(item, numbers=numbers, episodes=episodes, now=now, failed=failed))


def page_listing(item):
    """Return what a page or a read shows: the copy, or None when nothing can be had."""
    try:
        return get_listing(item)
    except AnimeDetailsUnavailable:
        return stored_listing(item)


EMPTY_EPISODES = {"next": None, "schedule": {}, "episodes": {}}


def update_from_metadata(item, metadata):
    """Store the numbers from MAL metadata fetched elsewhere; never raises."""
    if item.source == Sources.MANUAL.value or not metadata:
        return
    now = user_calendar.now()
    row = _stored_row(item)
    if (
        row is not None
        and row.fetched_at is not None
        and now - row.fetched_at < NUMBERS_FRESH
        and row.total == (metadata.get("max_progress") or None)
        and row.provider_status == str((metadata.get("details") or {}).get("status") or "")[:20]
    ):
        # The usual page view writes nothing.
        return
    try:
        _save(item, numbers=metadata, episodes=None, now=now)
    except (IntegrityError, *PROVIDER_FAILURES):
        logger.exception("Could not store the numbers of %s", item)


def update_next_title(item, metadata):
    """Store the next title from the series order a page holds (AN-500); never raises.

    Only when the copy's is missing or has gone stale, so the usual page view writes nothing.
    """
    series_items = ((metadata or {}).get("related") or {}).get("series")
    if item.source == Sources.MANUAL.value or not series_items:
        return
    now = user_calendar.now()
    try:
        row = _stored_row(item) or _create_row(item)
        if _series_is_stale(row, now):
            row.next_title = next_title_in(series_items, item.media_id)
            row.series_checked_at = now
            row.save(update_fields=["next_title", "series_checked_at"])
    except (IntegrityError, *PROVIDER_FAILURES):
        logger.exception("Could not store the next title of %s", item)


def provisional_listing(item, total):
    """Return the listing an import works from: no provider call, ever (AN-800).

    A title with no copy gets one holding the total the source gave, in memory only: a
    person's file never sets the copy everyone else reads. What it says stays on their own
    record until the calendar job, which the import's end queues, reads the real numbers.
    """
    stored = None if item.source == Sources.MANUAL.value else stored_listing(item)
    return stored if stored is not None else Listing(item=item, total=total or None)


def refresh(items):
    """Read MAL and AniList again for these titles; return {item id: Listing} (the job).

    One AniList request per 25 titles. A failure keeps what is stored and backs off.
    """
    items = [item for item in items if item.source != Sources.MANUAL.value]
    if not items:
        return {}
    now = user_calendar.now()
    episodes_by_id = None
    try:
        episodes_by_id = anilist.anime_episodes([item.media_id for item in items])
    except PROVIDER_FAILURES:
        logger.warning("AniList can't be read for the calendar job; keeping stored rows")
    listings = {}
    for item in items:
        numbers = None
        try:
            numbers = mal.anime(item.media_id)
        except PROVIDER_FAILURES:
            logger.warning("MAL can't be read for %s; keeping its numbers", item)
        episodes = None
        if episodes_by_id is not None:
            episodes = episodes_by_id.get(str(item.media_id), EMPTY_EPISODES)
        row = _save(item, numbers=numbers, episodes=episodes, now=now, failed=numbers is None or episodes is None)
        if row is None:
            continue
        if _series_is_stale(row, now):
            _refresh_next_title(row, numbers, now)
        if _has_numbers(row):
            listings[item.pk] = _listing_from_row(row)
    return listings


def _series_is_stale(row, now):
    return row.series_checked_at is None or now - row.series_checked_at >= STALE_SETTLED


def _refresh_next_title(row, numbers, now):
    """Store the title after this one in MAL's order (AN-500); never breaks the job.

    The same fallbacks as api.services.media._add_anime_series: a title with no prequel
    or sequel has no series, and a slow or failing MAL leaves things as they are.
    """
    relations = ((numbers or {}).get("related") or {}).get("relations") or []
    if numbers is None:
        return
    next_title = None
    if any(relation.get("relation") in {"Prequel", "Sequel"} for relation in relations):
        try:
            series = mal.anime_series(row.item.media_id)
        except (requests.exceptions.RequestException, services.ProviderAPIError):
            logger.warning("MAL's series order can't be read for %s", row.item)
            return
        next_title = next_title_in(series.get("items") or (), row.item.media_id)
    row.next_title = next_title
    row.series_checked_at = now
    row.save(update_fields=["next_title", "series_checked_at"])


def next_title_in(series_items, media_id):
    """Return the entry after this title in a series' items, or None (AN-500)."""
    ordered = sorted(series_items, key=lambda entry: entry.get("position") or 0)
    numbers = [str(entry.get("media_id")) for entry in ordered]
    if str(media_id) not in numbers:
        return None
    index = numbers.index(str(media_id)) + 1
    if index >= len(ordered):
        return None
    entry = ordered[index]
    return {
        "media_id": str(entry.get("media_id")),
        "source": entry.get("source") or Sources.MAL.value,
        "media_type": entry.get("media_type") or MediaTypes.ANIME.value,
        "title": entry.get("title") or "",
        "display_title": entry.get("display_title"),
        "image": entry.get("image"),
        "release_date": entry.get("release_date"),
        "single": bool(entry.get("single")),
    }


def _create_row(item, **values):
    try:
        with transaction.atomic():
            return AnimeListing.objects.select_related("item").create(item=item, **values)
    except IntegrityError:
        return AnimeListing.objects.select_related("item").get(item=item)


def _save(item, *, numbers, episodes, now, failed=False):
    """Write what was read onto the stored row; nothing read leaves its part as it is.

    With nothing read and no row, returns None: an empty row would be a copy of nothing.
    """
    row = _stored_row(item)
    if row is None:
        if numbers is None and episodes is None:
            return None
        row = _create_row(item)
    fields = []
    if numbers is not None:
        _apply_numbers(row, numbers, now)
        fields.extend(["total", "provider_status", "media_format", "premiere_date", "fetched_at", "rows_fetched_at"])
    if episodes is not None:
        _apply_episodes(row, episodes, now)
        fields.extend(["next_episode", "next_air_at", "rows", "rows_fetched_at", "retry_after"])
    if failed:
        row.retry_after = now + FAILURE_BACKOFF
        fields.append("retry_after")
    if fields:
        row.save(update_fields=fields)
    row.item = item
    return row


def _apply_numbers(row, metadata, now):
    details = metadata.get("details") or {}
    row.total = metadata.get("max_progress") or None
    status = str(details.get("status") or "")[:20]
    if row.provider_status and status != row.provider_status:
        # AniList answered about a title in another state: announced, say, with nothing
        # on the schedule. That says nothing now, so it has to answer again (AN-100).
        row.rows_fetched_at = None
    row.provider_status = status
    row.media_format = str(metadata.get("series_format") or details.get("format") or "")[:20]
    row.premiere_date = _full_date(details.get("start_date"))
    row.fetched_at = now


def _apply_episodes(row, episodes, now):
    next_airing = episodes.get("next")
    row.next_episode = next_airing["episode"] if next_airing else None
    row.next_air_at = (
        datetime.fromtimestamp(next_airing["airing_at"], tz=UTC)
        if next_airing and next_airing.get("airing_at")
        else None
    )
    schedule = dict(episodes.get("schedule") or {})
    listed = dict(episodes.get("episodes") or {})
    if row.total and any(number > row.total for number in listed):
        # AniList can file another season's episodes under a title: it lists Attack on Titan's
        # 25 under Season 2's 12. A list running past the total isn't this title's, and a row
        # without a title still reads "Episode 5" (AN-101).
        listed = {}
    last_aired = episodes.get("last_aired")
    if last_aired:
        if now - datetime.fromtimestamp(last_aired["airing_at"], tz=UTC) < SCHEDULE_ABANDONED:
            schedule[last_aired["episode"]] = last_aired["airing_at"]
        else:
            # It did air, but that's all it says now.
            listed.setdefault(last_aired["episode"], {})
    row.rows = [
        [
            number,
            (listed.get(number) or {}).get("title") or "",
            (listed.get(number) or {}).get("image") or "",
            schedule.get(number),
        ]
        for number in sorted({*schedule, *listed})
    ]
    row.rows_fetched_at = now
    row.retry_after = None


def _full_date(value):
    """Return a whole YYYY-MM-DD as a date; MAL can give only a year or a month."""
    if not value or len(str(value)) != len("YYYY-MM-DD"):
        return None
    try:
        return parse_date(str(value))
    except ValueError:
        return None
