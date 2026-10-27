"""Stored copy of each show's provider episode lists (docs/tv-tracking-contract.md, TV-103).

Progress, up next, finishing and caught up count only aired regular episodes from the
provider's list for each season. Spine keeps a copy of each listed season's episode
numbers and air dates (TVListing), refreshes it when it is stale, and uses it when the
provider can't be reached. A write that needs a season Spine has no copy of fails with
TVDetailsUnavailable and changes nothing.

Manual shows are read live from their Items: they never get a stored copy and are never
unavailable, and since manual entries have no air dates, every listed episode counts as
aired.

get_listing may call the provider, so callers fetch the listing before they take a
lock or open the write's transaction (TV-705).
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cached_property

import requests
from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.utils.dateparse import parse_date, parse_datetime

from app import user_calendar
from app.models import Item, MediaTypes, Sources, TVListing
from app.providers import services, tmdb

logger = logging.getLogger(__name__)

UNAVAILABLE_MESSAGE = "TV details are unavailable right now"
UNAIRED_MESSAGE = "This episode hasn't aired yet."

# A stored season with an unaired or undated episode, or one that aired recently, is
# refreshed after a day; every other season after a week. The show-level part (which
# seasons are listed, the next episode to air, whether the show is returning) is
# refreshed after a day.
STALE_ACTIVE = timedelta(hours=24)
STALE_SETTLED = timedelta(days=7)
STALE_SHOW = timedelta(hours=24)
RECENTLY_AIRED = timedelta(days=14)
# Seasons per provider request: TMDB appends two items per season to three base items,
# and allows twenty (A-47).
SEASONS_PER_REQUEST = 8
RETURNING_STATUS = "Returning Series"
# A tick or episode log may name an episode airing up to a day after today on the
# user's calendar, which covers time zones (TV-106).
AIR_DATE_SLACK = timedelta(days=1)
TICK = "tick"
EPISODE_LOG = "episode_log"
FILL = "fill"
WRITE_KINDS = (TICK, EPISODE_LOG, FILL)
PROVIDER_FAILURES = (services.ProviderAPIError, requests.exceptions.RequestException)


class TVDetailsUnavailable(Exception):  # noqa: N818 - the contract's name for it
    """The provider can't be reached and Spine has no copy of a needed season (TV-103).

    Retryable. Never raised for manual shows.
    """

    def __init__(self, message=UNAVAILABLE_MESSAGE, *, season_numbers=()):
        """Keep the user copy and the seasons that were missing."""
        super().__init__(message)
        self.message = message
        self.season_numbers = tuple(season_numbers)


class InvalidEpisode(ValueError):
    """A write names an episode that isn't listed or hasn't aired (TV-106)."""

    def __init__(self, message):
        """Keep the user copy."""
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class ListedEpisode:
    """One episode the provider lists."""

    season_number: int
    episode_number: int
    air_date: date | None
    title: str
    image: str | None
    # Minutes, when the provider gives a length. No TV rule reads it; stats do.
    runtime: int | None = None


@dataclass(frozen=True)
class ShowListing:
    """A show's listed seasons and the episode lists Spine has for them.

    episodes holds the listed seasons whose lists are loaded, each in episode order.
    season_air_dates holds the provider's premiere date of each listed season, when it
    has one. season_images holds each listed season's own poster, when the provider has
    one and the stored copy has it. any_air_dates is False only for a source that gives
    no air dates (manual entries, TV-103): a TMDB show with no dates yet hasn't aired
    anything.
    """

    show_item: Item
    season_numbers: tuple[int, ...]
    episodes: Mapping[int, tuple[ListedEpisode, ...]]
    next_episode_to_air: tuple[int, int | None, date | None] | None
    returning: bool
    any_air_dates: bool
    season_air_dates: Mapping[int, date | None] = field(default_factory=dict)
    season_images: Mapping[int, str] = field(default_factory=dict)

    def regular_seasons(self) -> list[int]:
        """Return the listed regular seasons (numbered 1 or higher), in order."""
        return sorted(number for number in self.season_numbers if number >= 1)

    def is_loaded(self, season_number) -> bool:
        """Return whether this listing holds the season's episode list."""
        return season_number in self.episodes

    def episode(self, season_number, episode_number) -> ListedEpisode | None:
        """Return a listed episode, or None when the provider doesn't list it."""
        return self._by_number.get((season_number, episode_number))

    def is_listed(self, season_number, episode_number) -> bool:
        """Return whether the provider lists this episode for that season."""
        return (season_number, episode_number) in self._by_number

    def is_aired(self, season_number, episode_number, *, today, has_viewing=False) -> bool:
        """Return whether a listed episode counts as aired (TV-103).

        It does when its air date is today or earlier on the user's calendar, when the
        user has a viewing of it, when it has no air date and a later episode's air
        date is today or earlier, or when the source gives no air date at all. For a
        special, "later" means a later special. An unlisted episode counts toward
        nothing, so it never counts as aired.
        """
        listed = self.episode(season_number, episode_number)
        if listed is None:
            return False
        if has_viewing or not self.any_air_dates:
            return True
        if listed.air_date is not None:
            return listed.air_date <= today
        latest = self._latest_aired_positions(today)
        key = "specials" if season_number == 0 else "regular"
        position = latest.get(key)
        return position is not None and (season_number, episode_number) < position

    def next_regular_airing(
        self,
        *,
        today,
        viewed=(),
    ) -> tuple[int, int | None, date | None] | None:
        """Return the next regular episode to air: (season, episode, air date) (TV-202).

        That is the first unaired regular episode in the season lists, or the
        provider's next episode to air when the lists don't include it yet, or a
        listed season with no episodes yet and a future premiere date (episode None).
        viewed holds (season, episode) pairs the user has watched; a viewing makes an
        episode aired for that user.
        """
        viewed = set(viewed)
        candidates = []
        for season_number in self.regular_seasons():
            unaired = next(
                (
                    listed
                    for listed in self.episodes.get(season_number, ())
                    if not self.is_aired(
                        season_number,
                        listed.episode_number,
                        today=today,
                        has_viewing=(season_number, listed.episode_number) in viewed,
                    )
                ),
                None,
            )
            if unaired is not None:
                candidates.append(
                    (season_number, unaired.episode_number, unaired.air_date),
                )
                break
        if self.next_episode_to_air is not None:
            season_number, episode_number, air_date = self.next_episode_to_air
            listed_and_aired = episode_number is not None and self.is_aired(
                season_number,
                episode_number,
                today=today,
                has_viewing=(season_number, episode_number) in viewed,
            )
            if season_number >= 1 and not listed_and_aired:
                candidates.append(self.next_episode_to_air)
        for season_number in self.regular_seasons():
            premiere = self.season_air_dates.get(season_number)
            if (
                not self.episodes.get(season_number)
                and premiere is not None
                and premiere > today
            ):
                candidates.append((season_number, None, premiere))
                break
        if not candidates:
            return None
        return min(candidates, key=lambda airing: (airing[0], airing[1] or 0))

    def premiere(self, *, today) -> tuple[int, date | None] | None:
        """Return (season, premiere date) while no regular episode has aired, else None."""
        regular = self.regular_seasons()
        for season_number in regular:
            for listed in self.episodes.get(season_number, ()):
                if self.is_aired(season_number, listed.episode_number, today=today):
                    return None
        if regular:
            season_number = regular[0]
        elif self.next_episode_to_air is not None and self.next_episode_to_air[0] >= 1:
            season_number = self.next_episode_to_air[0]
        else:
            return None
        dated = [
            listed.air_date
            for listed in self.episodes.get(season_number, ())
            if listed.air_date is not None
        ]
        if dated:
            return season_number, min(dated)
        if self.season_air_dates.get(season_number) is not None:
            return season_number, self.season_air_dates[season_number]
        if self.next_episode_to_air is not None and (
            self.next_episode_to_air[0] == season_number
        ):
            return season_number, self.next_episode_to_air[2]
        return season_number, None

    def season_runtime(self, season_number) -> int | None:
        """Return the average length of the season's listed episodes in minutes, or None."""
        runtimes = [
            listed.runtime
            for listed in self.episodes.get(season_number, ())
            if listed.runtime is not None
        ]
        return round(sum(runtimes) / len(runtimes)) if runtimes else None

    def item_fields(self, media_type, season_number, episode_number=None) -> dict:
        """Return the Item fields the listing knows for a season or episode Item.

        An episode gets its air date and length; a season its premiere date and the
        average length of its episodes. Only known values are returned.
        """
        if media_type == MediaTypes.EPISODE.value:
            listed = self.episode(season_number, episode_number)
            release_date = listed.air_date if listed else None
            runtime = listed.runtime if listed else None
        else:
            release_date = self.season_air_dates.get(season_number)
            runtime = self.season_runtime(season_number)
        fields = {}
        if release_date is not None:
            fields["release_date"] = release_date
            fields["release_year"] = release_date.year
        if runtime is not None:
            fields["runtime_minutes"] = runtime
        return fields

    def episode_title(self, season_number, episode_number) -> str | None:
        """Return a listed episode's title, or None when it isn't listed or has none."""
        listed = self.episode(season_number, episode_number)
        return (listed.title or None) if listed is not None else None

    def episode_still(self, season_number, episode_number) -> str | None:
        """Return a listed episode's still at the size of a season's episode rows, or None."""
        listed = self.episode(season_number, episode_number)
        return still_url(listed.image if listed is not None else None)

    @cached_property
    def _by_number(self):
        return {
            (listed.season_number, listed.episode_number): listed
            for season in self.episodes.values()
            for listed in season
        }

    def _latest_aired_positions(self, today):
        """Return the last dated, aired regular episode and special, by position."""
        cache = self.__dict__.setdefault("_aired_positions", {})
        if today not in cache:
            latest = {}
            for listed in self._by_number.values():
                if listed.air_date is None or listed.air_date > today:
                    continue
                key = "specials" if listed.season_number == 0 else "regular"
                position = (listed.season_number, listed.episode_number)
                if key not in latest or position > latest[key]:
                    latest[key] = position
            cache[today] = latest
        return cache[today]


def get_listing(show_item, *, seasons="all", allow_fetch=True, max_age=None):
    """Return the show's listing, fetching missing or stale parts first.

    seasons is "all" (every listed season, Specials included) or the season numbers a
    caller needs; seasons the provider no longer lists are skipped. max_age=None
    applies the STALE_* windows; an explicit max_age replaces them, and timedelta(0)
    forces a fetch. Fetches go through the provider at most SEASONS_PER_REQUEST
    seasons at a time and update the stored copy. When the provider fails, the stored
    copy is used; when a needed season has no stored copy, TVDetailsUnavailable is
    raised. Never call it while holding a lock.
    """
    if show_item.source == Sources.MANUAL.value:
        return _manual_listing(show_item)

    now = user_calendar.now()
    stored = TVListing.objects.filter(item=show_item).first()
    copy = _Copy.from_row(stored)
    forced = max_age is not None
    show_changed = False
    changed_seasons = set()

    if allow_fetch and copy.show_is_stale(now, max_age):
        try:
            if forced:
                _forget_provider_cache(show_item)
            metadata = services.get_media_metadata(
                MediaTypes.TV.value,
                show_item.media_id,
                show_item.source,
            )
        except PROVIDER_FAILURES:
            logger.warning("Using the stored episode lists of %s", show_item)
        else:
            copy.apply_show(metadata, now)
            show_changed = True
    if copy.fetched_at is None:
        raise TVDetailsUnavailable

    wanted = copy.wanted(seasons)
    if allow_fetch:
        to_fetch = [number for number in wanted if copy.season_is_stale(number, now, max_age)]
        for start in range(0, len(to_fetch), SEASONS_PER_REQUEST):
            chunk = to_fetch[start : start + SEASONS_PER_REQUEST]
            try:
                if forced:
                    _forget_provider_cache(show_item, chunk)
                metadata = services.get_media_metadata(
                    "tv_with_seasons",
                    show_item.media_id,
                    show_item.source,
                    chunk,
                )
            except PROVIDER_FAILURES:
                logger.warning(
                    "Using the stored lists of %s seasons %s",
                    show_item,
                    chunk,
                )
                continue
            for number in chunk:
                season_metadata = metadata.get(f"season/{number}")
                if season_metadata is not None:
                    copy.apply_season(number, season_metadata, now)
                    changed_seasons.add(number)

    if show_changed or changed_seasons:
        _save(show_item, copy, show_changed=show_changed, seasons=changed_seasons)
    missing = [number for number in wanted if not copy.is_loaded(number)]
    if missing:
        raise TVDetailsUnavailable(season_numbers=missing)
    return copy.listing(show_item)


def stored_listing(show_item):
    """Return the stored listing without any network call, or None when there is none.

    Manual shows are built from their Items.
    """
    if show_item.source == Sources.MANUAL.value:
        return _manual_listing(show_item)
    copy = _Copy.from_row(TVListing.objects.filter(item=show_item).first())
    if copy.fetched_at is None:
        return None
    return copy.listing(show_item)


def update_from_metadata(show_item, metadata, *, season_number=None):
    """Update the stored copy from provider metadata fetched elsewhere.

    metadata is the show's metadata (with any "season/N" keys it carries), or, with
    season_number, one season's metadata. A failure is logged and never raised: the
    stored copy is a cache.
    """
    if show_item.source == Sources.MANUAL.value or not metadata:
        return
    try:
        now = user_calendar.now()
        copy = _Copy.from_row(TVListing.objects.filter(item=show_item).first())
        show_changed = False
        changed_seasons = set()
        if season_number is not None:
            copy.apply_season(season_number, metadata, now)
            changed_seasons.add(season_number)
        else:
            if isinstance(metadata.get("related"), dict) and (
                "seasons" in metadata["related"]
            ):
                copy.apply_show(metadata, now)
                show_changed = True
            for key, value in metadata.items():
                if key.startswith("season/") and isinstance(value, dict):
                    number = int(key.removeprefix("season/"))
                    copy.apply_season(number, value, now)
                    changed_seasons.add(number)
        if show_changed or changed_seasons:
            _save(show_item, copy, show_changed=show_changed, seasons=changed_seasons)
    except (DatabaseError, KeyError, TypeError, ValueError):
        logger.exception("Could not update the stored episode lists of %s", show_item)


def validate_write(listing, season_number, episode_number, *, today, kind):
    """Reject a write naming an episode that isn't listed or hasn't aired (TV-106).

    kind is "tick" or "episode_log" (an episode airing up to a day after today is
    accepted, and an undated one only when a later episode has aired or the source
    has no dates), or "fill" (only aired episodes are filled). today is the user's
    calendar day.
    """
    if kind not in WRITE_KINDS:
        msg = f"Unknown write kind {kind!r}"
        raise ValueError(msg)
    if season_number in listing.season_numbers and not listing.is_loaded(season_number):
        raise TVDetailsUnavailable(season_numbers=[season_number])
    listed = listing.episode(season_number, episode_number)
    if listed is None:
        msg = f"This episode isn't listed for {season_label(season_number)}."
        raise InvalidEpisode(msg)
    if kind == FILL:
        if not listing.is_aired(season_number, episode_number, today=today):
            raise InvalidEpisode(UNAIRED_MESSAGE)
        return
    if listed.air_date is not None:
        if listed.air_date > today + AIR_DATE_SLACK:
            raise InvalidEpisode(UNAIRED_MESSAGE)
        return
    if not listing.is_aired(season_number, episode_number, today=today):
        raise InvalidEpisode(UNAIRED_MESSAGE)


def show_item_for(item):
    """Return the tv Item of a tv, season or episode Item, creating it when missing.

    Creating it may call the provider for the show's title and poster, so call this
    before the write's transaction. Raises TVDetailsUnavailable when the provider
    can't be reached.
    """
    if item.media_type == MediaTypes.TV.value:
        return item
    if item.media_type not in (MediaTypes.SEASON.value, MediaTypes.EPISODE.value):
        msg = f"{item} is not a TV item"
        raise ValueError(msg)
    lookup = {
        "media_id": item.media_id,
        "source": item.source,
        "media_type": MediaTypes.TV.value,
        "season_number": None,
        "episode_number": None,
    }
    show = Item.objects.filter(**lookup).first()
    if show is not None:
        return show
    if item.source == Sources.MANUAL.value:
        return Item.objects.get(**lookup)
    try:
        metadata = services.get_media_metadata(
            MediaTypes.TV.value,
            item.media_id,
            item.source,
        )
    except PROVIDER_FAILURES as error:
        raise TVDetailsUnavailable from error
    show, _ = Item.objects.get_or_create(
        **lookup,
        defaults={"title": metadata["title"], "image": metadata["image"]},
    )
    from api.services.item_metadata import store_item_metadata  # noqa: PLC0415

    store_item_metadata(show, metadata)
    return show


def season_label(season_number):
    """Return "Specials" for season 0, else "Season N"."""
    return "Specials" if season_number == 0 else f"Season {season_number}"


def still_url(image) -> str | None:
    """Return an episode's stored image as a still at the size of a season's episode rows, or None."""
    if not image or image == settings.IMG_NONE or not image.startswith(("http://", "https://")):
        return None
    return tmdb.sized_image_url(image, "w500")


def stored_episode(seasons, season_number, episode_number) -> tuple[str | None, str | None]:
    """Return one episode's (title, still) from a stored listing's seasons JSON, or (None, None)."""
    entry = (seasons or {}).get(str(season_number))
    for row in (entry.get("episodes") or ()) if isinstance(entry, dict) else ():
        if row and int(row[0]) == episode_number:
            title = row[2] if len(row) > 2 else None
            return title or None, still_url(row[3] if len(row) > 3 else None)
    return None, None


class _Copy:
    """A working copy of a show's stored listing, in its stored JSON shape."""

    def __init__(self, *, season_numbers, seasons, next_episode_to_air, returning, fetched_at):
        self.season_numbers = [int(number) for number in season_numbers]
        self.seasons = {str(key): dict(value) for key, value in seasons.items()}
        self.next_episode_to_air = next_episode_to_air
        self.returning = returning
        self.fetched_at = fetched_at

    @classmethod
    def from_row(cls, row):
        if row is None:
            return cls(
                season_numbers=[],
                seasons={},
                next_episode_to_air=None,
                returning=False,
                fetched_at=None,
            )
        return cls(
            season_numbers=row.season_numbers or [],
            seasons=row.seasons or {},
            next_episode_to_air=row.next_episode_to_air,
            returning=row.returning,
            fetched_at=row.fetched_at,
        )

    def show_is_stale(self, now, max_age):
        if self.fetched_at is None:
            return True
        return now - self.fetched_at >= (STALE_SHOW if max_age is None else max_age)

    def wanted(self, seasons):
        if seasons == "all":
            return sorted(self.season_numbers)
        listed = set(self.season_numbers)
        return sorted({int(number) for number in seasons} & listed)

    def is_loaded(self, number):
        return "episodes" in self.seasons.get(str(number), {})

    def season_is_stale(self, number, now, max_age):
        entry = self.seasons.get(str(number), {})
        fetched = _parse_datetime(entry.get("fetched_at"))
        if "episodes" not in entry or fetched is None:
            return True
        if max_age is None:
            max_age = STALE_ACTIVE if _is_active(entry, now.date()) else STALE_SETTLED
        return now - fetched >= max_age

    def apply_show(self, metadata, now):
        related = (metadata.get("related") or {}).get("seasons") or []
        numbers = set()
        for season in related:
            if season.get("season_number") is None:
                continue
            number = int(season["season_number"])
            numbers.add(number)
            entry = self.seasons.setdefault(str(number), {})
            entry["air_date"] = _date_string(season.get("first_air_date"))
            entry["image"] = _season_image(season)
        self.season_numbers = sorted(numbers)
        self.seasons = {
            key: value for key, value in self.seasons.items() if int(key) in numbers
        }
        if "next_episode_to_air" in metadata:
            self.next_episode_to_air = _next_episode(metadata["next_episode_to_air"])
        elif metadata.get("next_episode_season") is not None:
            self.next_episode_to_air = {
                "season_number": int(metadata["next_episode_season"]),
                "episode_number": None,
                "air_date": None,
            }
        else:
            self.next_episode_to_air = None
        details = metadata.get("details") or {}
        self.returning = details.get("status") == RETURNING_STATUS
        self.fetched_at = now

    def apply_season(self, number, metadata, now):
        rows = {}
        for episode in metadata.get("episodes") or []:
            episode_number = episode.get("episode_number")
            if episode_number is None:
                continue
            rows[int(episode_number)] = [
                int(episode_number),
                _date_string(episode.get("air_date")),
                episode.get("name") or episode.get("title") or episode.get("episode_title") or "",
                _episode_image(episode),
                _runtime(episode.get("runtime")),
            ]
        entry = self.seasons.setdefault(str(number), {})
        entry["episodes"] = [rows[key] for key in sorted(rows)]
        entry["fetched_at"] = now.isoformat()
        if "image" in metadata:
            entry["image"] = _season_image(metadata)
        if "air_date" not in entry:
            entry["air_date"] = _date_string(
                (metadata.get("details") or {}).get("first_air_date"),
            )

    def listing(self, show_item):
        listed = set(self.season_numbers)
        episodes = {}
        season_air_dates = {}
        season_images = {}
        for key, entry in self.seasons.items():
            number = int(key)
            if number not in listed:
                continue
            season_air_dates[number] = _parse_date(entry.get("air_date"))
            if entry.get("image"):
                season_images[number] = entry["image"]
            if "episodes" in entry:
                episodes[number] = tuple(
                    ListedEpisode(
                        season_number=number,
                        episode_number=int(row[0]),
                        air_date=_parse_date(row[1]),
                        title=row[2] or "",
                        image=row[3] if len(row) > 3 else None,
                        runtime=row[4] if len(row) > 4 else None,
                    )
                    for row in entry["episodes"]
                )
        next_episode = None
        if self.next_episode_to_air:
            next_episode = (
                int(self.next_episode_to_air["season_number"]),
                self.next_episode_to_air.get("episode_number"),
                _parse_date(self.next_episode_to_air.get("air_date")),
            )
        return ShowListing(
            show_item=show_item,
            season_numbers=tuple(sorted(listed)),
            episodes=episodes,
            next_episode_to_air=next_episode,
            returning=self.returning,
            any_air_dates=True,
            season_air_dates=season_air_dates,
            season_images=season_images,
        )


def _save(show_item, copy, *, show_changed, seasons):
    """Write the copy's changed parts onto the stored row.

    Only the parts this caller fetched are written, so a season another request stored
    meanwhile is kept.
    """
    with transaction.atomic():
        row = TVListing.objects.filter(item=show_item).first()
        if row is None:
            try:
                with transaction.atomic():
                    TVListing.objects.create(
                        item=show_item,
                        season_numbers=copy.season_numbers,
                        seasons=copy.seasons,
                        next_episode_to_air=copy.next_episode_to_air,
                        returning=copy.returning,
                        fetched_at=copy.fetched_at,
                    )
            except IntegrityError:
                row = TVListing.objects.get(item=show_item)
            else:
                return
        merged = _Copy.from_row(row)
        if show_changed:
            listed = set(copy.season_numbers)
            merged.season_numbers = copy.season_numbers
            merged.next_episode_to_air = copy.next_episode_to_air
            merged.returning = copy.returning
            merged.fetched_at = copy.fetched_at
            merged.seasons = {
                key: entry for key, entry in merged.seasons.items() if int(key) in listed
            }
            for number in listed:
                entry = merged.seasons.setdefault(str(number), {})
                entry["air_date"] = copy.seasons.get(str(number), {}).get("air_date")
                entry["image"] = copy.seasons.get(str(number), {}).get("image")
        for number in seasons:
            merged.seasons[str(number)] = copy.seasons[str(number)]
        row.season_numbers = merged.season_numbers
        row.seasons = merged.seasons
        row.next_episode_to_air = merged.next_episode_to_air
        row.returning = merged.returning
        row.fetched_at = merged.fetched_at
        row.save()


def _manual_listing(show_item):
    """Build a manual show's listing from its Items: listed seasons and their episodes."""
    rows = Item.objects.filter(
        media_id=show_item.media_id,
        source=Sources.MANUAL.value,
        media_type__in=(MediaTypes.SEASON.value, MediaTypes.EPISODE.value),
    ).values_list("media_type", "season_number", "episode_number", "title", "image")
    season_numbers = set()
    episodes = {}
    for media_type, season_number, episode_number, title, image in rows:
        if media_type == MediaTypes.SEASON.value:
            season_numbers.add(season_number)
        else:
            episodes.setdefault(season_number, []).append(
                ListedEpisode(
                    season_number=season_number,
                    episode_number=episode_number,
                    air_date=None,
                    title=title or "",
                    image=image or None,
                ),
            )
    return ShowListing(
        show_item=show_item,
        season_numbers=tuple(sorted(season_numbers)),
        episodes={
            number: tuple(sorted(episodes.get(number, []), key=lambda e: e.episode_number))
            for number in season_numbers
        },
        next_episode_to_air=None,
        returning=False,
        any_air_dates=False,
    )


def _forget_provider_cache(show_item, season_numbers=()):
    """Make a forced fetch read the provider rather than its response cache."""
    if show_item.source == Sources.TMDB.value:
        tmdb.forget_tv(show_item.media_id, season_numbers)


def _is_active(entry, today):
    """Return whether a stored season may still change soon (unaired or recent)."""
    episodes = entry.get("episodes") or []
    if not episodes:
        return True
    for row in episodes:
        air_date = _parse_date(row[1])
        if air_date is None or air_date > today - RECENTLY_AIRED:
            return True
    return False


def _next_episode(value):
    if not value or value.get("season_number") is None:
        return None
    episode_number = value.get("episode_number")
    return {
        "season_number": int(value["season_number"]),
        "episode_number": int(episode_number) if episode_number is not None else None,
        "air_date": _date_string(value.get("air_date")),
    }


def _season_image(season):
    """Return a season's own poster from provider metadata, or None when it has none."""
    image = season.get("image")
    if not image or image == settings.IMG_NONE:
        return None
    return image


def _episode_image(episode):
    if episode.get("still_path"):
        return f"https://image.tmdb.org/t/p/original{episode['still_path']}"
    return episode.get("image") or None


def _runtime(value):
    """Return an episode's length in minutes from a number or text ("45m"), or None."""
    from api.services.filters import _runtime_value_minutes  # noqa: PLC0415

    return _runtime_value_minutes(value)


def _date_string(value):
    """Return a YYYY-MM-DD string for a date or date string, or None."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed else None


def _parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        return parse_date(value[:10])
    except ValueError:
        return None


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None

