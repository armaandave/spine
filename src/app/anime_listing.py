"""Stored copy of each anime title's numbers and episode rows (AN-100, AN-101).

MAL gives the total, whether the title finished airing and its format. AniList gives
episode titles and images, the schedule and the next episode. Every row and count reads
this copy, so no page ever waits for AniList: a first open with no copy tries once with a
short timeout; everything else answers from the copy, and a failure keeps the numbers and
backs off. The calendar job refreshes it (events.calendar.anime).

Manual titles are never stored: they have no provider, so they have no total and no
schedule, and any episode number is accepted.

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
# How often the calendar job looks again at a title it doesn't otherwise pick up, and at
# a title's place in its series.
STALE_SETTLED = timedelta(days=7)
NUMBERS_FRESH = timedelta(days=1)
STATUS_FINISHED = "Finished"
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

    @cached_property
    def by_number(self):
        return {row.number: row for row in self.rows}

    @cached_property
    def has_schedule(self):
        return self.next_episode is not None or any(row.air_at is not None for row in self.rows)

    @property
    def aired_is_known(self):
        """Whether Spine knows how many have aired (AN-102): a total or a schedule."""
        return self.cap is not None or self.has_schedule

    @property
    def may_have_aired(self):
        """Whether a title with no numbers can have aired, so its first row can be ticked."""
        return not self.aired_is_known and not self.is_upcoming

    def aired_through(self, now):
        """Return the last episode that has aired, by AN-100's first three and last rule.

        An episode's airing means every episode before it has too. The user's own
        viewings are the fourth rule, which only the caller knows.
        """
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
        if cap and not self.has_schedule and not self.is_upcoming:
            through = cap
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
        rows_fetched_at=row.rows_fetched_at,
    )


def _stored_row(item):
    return AnimeListing.objects.filter(item=item).select_related("item").first()


def stored_listing(item):
    """Return the stored copy without any network call, or None when there is none."""
    if item.source == Sources.MANUAL.value:
        return Listing(item=item)
    row = _stored_row(item)
    return _listing_from_row(row) if row is not None else None


def get_listing(item):
    """Return the copy, trying once to fetch what it lacks; never hangs on a provider.

    With no copy at all, or with numbers but no rows and the back-off passed, this tries
    each provider once with a short timeout. When MAL can't be read and nothing is stored,
    AnimeDetailsUnavailable is raised. Never call it while holding a lock.
    """
    if item.source == Sources.MANUAL.value:
        return Listing(item=item)
    now = user_calendar.now()
    row = _stored_row(item)
    if row is not None and row.fetched_at is not None and row.rows_fetched_at is not None:
        return _listing_from_row(row)
    if row is not None and row.retry_after is not None and row.retry_after > now:
        return _listing_from_row(row)
    numbers = None
    if row is None or row.fetched_at is None:
        try:
            numbers = mal.anime(item.media_id, timeout=FIRST_OPEN_TIMEOUT, retry_rate_limits=False)
        except PROVIDER_FAILURES:
            logger.warning("MAL can't be read for %s", item)
            if row is None:
                raise AnimeDetailsUnavailable from None
    episodes = None
    try:
        episodes = anilist.anime_episodes(
            [item.media_id],
            timeout=FIRST_OPEN_TIMEOUT,
            retry_rate_limits=False,
        ).get(str(item.media_id), EMPTY_EPISODES)
    except PROVIDER_FAILURES:
        logger.warning("AniList can't be read for %s; keeping its numbers", item)
    return _listing_from_row(_save(item, numbers=numbers, episodes=episodes, now=now))


def page_listing(item):
    """Return what a page or a read shows: the copy, or None when nothing can be had."""
    try:
        return get_listing(item)
    except AnimeDetailsUnavailable:
        return stored_listing(item)


EMPTY_EPISODES = {"next": None, "schedule": {}, "episodes": {}}


def update_from_metadata(item, metadata):
    """Store the numbers and next title from MAL metadata fetched elsewhere; never raises."""
    if item.source == Sources.MANUAL.value or not metadata:
        return
    now = user_calendar.now()
    row = _stored_row(item)
    has_series = bool((metadata.get("related") or {}).get("series"))
    if (
        row is not None
        and row.fetched_at is not None
        and now - row.fetched_at < NUMBERS_FRESH
        and row.total == (metadata.get("max_progress") or None)
        and not (has_series and row.series_checked_at is None)
    ):
        # The usual page view writes nothing.
        return
    try:
        _save(item, numbers=metadata, episodes=None, now=now, backoff=False)
    except (IntegrityError, *PROVIDER_FAILURES):
        logger.exception("Could not store the numbers of %s", item)


def provisional_listing(item, total):
    """Return the copy an import works from: no provider call, ever (AN-800).

    A title with no copy gets one holding the total the source gave, for the calendar job
    or the first open to fill in.
    """
    if item.source == Sources.MANUAL.value:
        return Listing(item=item, total=total)
    row = _stored_row(item)
    if row is None:
        row = _create_row(item, total=total)
    elif total and row.fetched_at is None and row.total != total:
        row.total = total
        row.save(update_fields=["total"])
    return _listing_from_row(row)


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
        row = _save(item, numbers=numbers, episodes=episodes, now=now)
        if _series_is_stale(row, now):
            _refresh_next_title(row, numbers, now)
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
    }


def _create_row(item, **values):
    try:
        with transaction.atomic():
            return AnimeListing.objects.select_related("item").create(item=item, **values)
    except IntegrityError:
        return AnimeListing.objects.select_related("item").get(item=item)


def _save(item, *, numbers, episodes, now, backoff=True):
    """Write what was read onto the stored row; nothing read leaves its part as it is."""
    row = _stored_row(item) or _create_row(item)
    fields = []
    if numbers is not None:
        _apply_numbers(row, numbers, now)
        fields.extend(["total", "provider_status", "media_format", "premiere_date", "fetched_at"])
        series_items = (numbers.get("related") or {}).get("series")
        if series_items:
            row.next_title = next_title_in(series_items, item.media_id)
            row.series_checked_at = now
            fields.extend(["next_title", "series_checked_at"])
    if episodes is not None:
        _apply_episodes(row, episodes, now)
        fields.extend(["next_episode", "next_air_at", "rows", "rows_fetched_at", "retry_after"])
    if backoff and (episodes is None or row.fetched_at is None):
        row.retry_after = now + FAILURE_BACKOFF
        fields.append("retry_after")
    if fields:
        row.save(update_fields=fields)
    row.item = item
    return row


def _apply_numbers(row, metadata, now):
    details = metadata.get("details") or {}
    row.total = metadata.get("max_progress") or None
    row.provider_status = str(details.get("status") or "")[:20]
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
    schedule = episodes.get("schedule") or {}
    listed = episodes.get("episodes") or {}
    if row.total and any(number > row.total for number in listed):
        # AniList can file another season's episodes under a title: it lists Attack on Titan's
        # 25 under Season 2's 12. A list running past the total isn't this title's, and a row
        # without a title still reads "Episode 5" (AN-101).
        listed = {}
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
