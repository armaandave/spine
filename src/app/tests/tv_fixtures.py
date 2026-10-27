"""Shared fakes for TV tracking tests (docs/tv-tracking-contract.md).

Every backend TV test uses these instead of the network:

- FakeTVProvider stands in for TMDB. As a context manager it patches
  app.providers.services.get_media_metadata, app.providers.tmdb.tv,
  app.providers.tmdb.tv_with_seasons and app.providers.tmdb.episode, counts every call,
  and can fail on demand. Its metadata comes from TMDB's own processing functions, so
  it has the real shapes. Manual shows and other media types pass through.
- make_user, make_show and make_manual_show build accounts and shows.
- patch_now patches django.utils.timezone.now, the one clock TV code reads (through
  app.user_calendar.now), which auto_now_add fields also read.

Later packages import this module read-only.
"""

import itertools
from contextlib import ContextDecorator, ExitStack
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model

from app.models import Item, MediaTypes, Sources
from app.providers import services, tmdb

DEFAULT_FIRST_AIR_DATE = date(2020, 1, 6)
DEFAULT_IMAGE = "https://image.tmdb.org/t/p/w500/poster.jpg"
PROVIDER_KINDS = ("tv", "tv_with_seasons", "season", "episode")
_ids = itertools.count(1)


@dataclass
class FakeEpisode:
    """One episode TMDB lists."""

    episode_number: int
    air_date: date | None
    title: str = ""
    still_path: str | None = None
    runtime: int | None = 45


@dataclass
class FakeShow:
    """One show as TMDB describes it. Mutate it to change what the provider says."""

    media_id: str
    title: str
    seasons: dict[int, list[FakeEpisode]] = field(default_factory=dict)
    # "Returning Series", "Ended", "Canceled", ...
    status: str = "Ended"
    # {"season_number": 3, "episode_number": 1, "air_date": date(2027, 3, 12)}
    next_episode_to_air: dict | None = None
    # Season premiere dates TMDB reports; a season missing here uses its first
    # episode's date.
    season_air_dates: dict[int, date | None] = field(default_factory=dict)
    poster_path: str | None = "/poster.jpg"
    # Season posters TMDB has, by season number; a season missing here has none.
    season_posters: dict[int, str] = field(default_factory=dict)

    def set_season(self, season_number, spec, *, start=None):
        """Replace a season's episodes; spec as in episodes_from_spec."""
        self.seasons[season_number] = episodes_from_spec(
            spec,
            start=start or _default_start(season_number),
        )

    def add_episode(self, season_number, episode_number, air_date, title=""):
        """Add one episode to a season's list (TV-206)."""
        episodes = self.seasons.setdefault(season_number, [])
        episodes.append(
            FakeEpisode(episode_number, air_date, title or f"Episode {episode_number}"),
        )
        episodes.sort(key=lambda episode: episode.episode_number)

    def remove_season(self, season_number):
        """Stop listing a season (A-62)."""
        self.seasons.pop(season_number, None)
        self.season_air_dates.pop(season_number, None)

    def episode_count(self):
        return sum(len(episodes) for episodes in self.seasons.values())


def episodes_from_spec(spec, *, start=DEFAULT_FIRST_AIR_DATE, every=timedelta(days=7)):
    """Build a season's episodes.

    spec is a count (episodes 1..N airing weekly from start), a list of air dates or
    None (episodes 1..N), a list of (number, air date) pairs (numbering with gaps), or
    a list of FakeEpisode.
    """
    if isinstance(spec, int):
        return [
            FakeEpisode(number, start + every * (number - 1), f"Episode {number}")
            for number in range(1, spec + 1)
        ]
    episodes = []
    for index, value in enumerate(spec, start=1):
        if isinstance(value, FakeEpisode):
            episodes.append(value)
        elif isinstance(value, tuple):
            number, air_date = value
            episodes.append(FakeEpisode(number, air_date, f"Episode {number}"))
        else:
            episodes.append(FakeEpisode(index, value, f"Episode {index}"))
    return sorted(episodes, key=lambda episode: episode.episode_number)


def _default_start(season_number):
    """Default air dates: one season a year from 2020, specials from 2020."""
    return DEFAULT_FIRST_AIR_DATE + timedelta(days=365 * max(season_number - 1, 0))


class ProviderDown(services.ProviderAPIError):
    """The fake provider failing, as TMDB does when it can't be reached."""

    def __init__(self, details="Fake provider failure", status_code=None):
        response = mock.Mock(status_code=status_code, text=details)
        super().__init__(Sources.TMDB.value, mock.Mock(response=response), details)


class FakeTVProvider:
    """A fake TMDB for TV shows: configurable shows, a call log and failures.

    with FakeTVProvider() as fake:
        show = make_show(fake, seasons={1: 10, 2: 8})
        fake.show(show.media_id).add_episode(2, 9, date(2026, 10, 30))
        fake.fail()            # every call for any show raises ProviderAPIError
        fake.recover()
        fake.count("tv_with_seasons")

    With block_network (the default), any other provider request raises, so a test
    can't reach the network by accident.
    """

    def __init__(self, *shows, block_network=True):
        self.shows = {show.media_id: show for show in shows}
        self.calls = []
        self.block_network = block_network
        self._failing = {}
        self._stack = None
        self._original_get_media_metadata = services.get_media_metadata

    # Configuration

    def add_show(
        self,
        media_id=None,
        *,
        title="Test Show",
        seasons=None,
        status="Ended",
        next_episode_to_air=None,
        season_air_dates=None,
    ):
        """Register a show; seasons maps numbers to specs (see episodes_from_spec)."""
        media_id = str(media_id or 900000 + next(_ids))
        show = FakeShow(
            media_id=media_id,
            title=title,
            status=status,
            next_episode_to_air=next_episode_to_air,
            season_air_dates=dict(season_air_dates or {}),
        )
        for season_number, spec in (seasons if seasons is not None else {1: 10}).items():
            show.set_season(season_number, spec)
        self.shows[media_id] = show
        return show

    def show(self, media_id):
        return self.shows[str(media_id)]

    def fail(self, media_id=None, *, kinds=PROVIDER_KINDS, status_code=None):
        """Make calls fail: for one show or all (media_id None), for some call kinds."""
        for kind in kinds:
            self._failing[(None if media_id is None else str(media_id), kind)] = (
                status_code
            )

    def recover(self):
        self._failing.clear()

    def count(self, kind=None, media_id=None):
        """Return how many calls were made, optionally of one kind or for one show."""
        return sum(
            1
            for call_kind, call_id, _ in self.calls
            if (kind is None or call_kind == kind)
            and (media_id is None or call_id == str(media_id))
        )

    def season_requests(self, media_id=None):
        """Return the season lists of each tv_with_seasons call, in order."""
        return [
            seasons
            for kind, call_id, seasons in self.calls
            if kind == "tv_with_seasons" and (media_id is None or call_id == str(media_id))
        ]

    def reset_calls(self):
        self.calls.clear()

    # Context manager

    def __enter__(self):
        self._stack = ExitStack()
        self._stack.enter_context(
            mock.patch.object(services, "get_media_metadata", self.get_media_metadata),
        )
        self._stack.enter_context(mock.patch.object(tmdb, "tv", self.tv))
        self._stack.enter_context(
            mock.patch.object(tmdb, "tv_with_seasons", self.tv_with_seasons),
        )
        self._stack.enter_context(mock.patch.object(tmdb, "episode", self.episode))
        if self.block_network:
            self._stack.enter_context(
                mock.patch.object(services, "api_request", self._blocked_request),
            )
        return self

    def __exit__(self, *exc_info):
        self._stack.close()
        self._stack = None
        return False

    # Patched provider functions

    def get_media_metadata(
        self,
        media_type,
        media_id,
        source,
        season_numbers=None,
        episode_number=None,
        **kwargs,
    ):
        """Serve TV kinds for registered shows; pass everything else through."""
        if (
            source == Sources.MANUAL.value
            or media_type not in PROVIDER_KINDS
            or str(media_id) not in self.shows
        ):
            return self._original_get_media_metadata(
                media_type,
                media_id,
                source,
                season_numbers,
                episode_number,
                **kwargs,
            )
        if media_type == MediaTypes.TV.value:
            return self.tv(media_id)
        if media_type == "tv_with_seasons":
            return self.tv_with_seasons(media_id, season_numbers)
        if media_type == MediaTypes.SEASON.value:
            if not season_numbers:
                return self.tv(media_id)
            number = season_numbers[0]
            return self.tv_with_seasons(media_id, [number])[f"season/{number}"]
        return self.episode(media_id, season_numbers[0], episode_number)

    def tv(self, media_id):
        show = self._serve("tv", media_id, ())
        return tmdb.process_tv(self._raw_tv(show))

    def tv_with_seasons(self, media_id, season_numbers):
        numbers = tuple(int(number) for number in season_numbers or ())
        show = self._serve("tv_with_seasons", media_id, numbers)
        raw = self._raw_tv(show)
        data = tmdb.process_tv(raw)
        for number in numbers:
            if number not in show.seasons:
                msg = f"Season {number} not found in TMDB with ID {media_id}"
                raise ProviderDown(msg, status_code=404)
            season = tmdb.process_season(self._raw_season(show, number), {})
            data[f"season/{number}"] = tmdb.enrich_season_with_tv_data(
                season,
                data,
                show.media_id,
                number,
            )
        return data

    def episode(self, media_id, season_number, episode_number):
        season_number = int(season_number)
        episode_number = int(episode_number)
        show = self._serve("episode", media_id, (season_number,))
        listed = next(
            (
                episode
                for episode in show.seasons.get(season_number, [])
                if episode.episode_number == episode_number
            ),
            None,
        )
        if listed is None:
            msg = f"Episode {episode_number} not found in season {season_number}"
            raise ProviderDown(msg, status_code=404)
        title = listed.title or f"Episode {episode_number}"
        air_date = listed.air_date.isoformat() if listed.air_date else None
        return {
            "media_id": show.media_id,
            "source": Sources.TMDB.value,
            "source_url": (
                f"https://www.themoviedb.org/tv/{show.media_id}/season/"
                f"{season_number}/episode/{episode_number}"
            ),
            "media_type": MediaTypes.EPISODE.value,
            "title": title,
            "subtitle": f"{show.title} • S{season_number} E{episode_number}",
            "series_title": show.title,
            "season_title": f"Season {season_number}",
            "episode_title": title,
            "season_number": season_number,
            "episode_number": episode_number,
            "max_progress": 1,
            "image": tmdb.get_image_url(listed.still_path),
            "backdrop_path": listed.still_path,
            "synopsis": "No synopsis available.",
            "release_date": air_date,
            "score": None,
            "score_count": 0,
            "details": {
                "format": "Episode",
                "series_title": show.title,
                "season_title": f"Season {season_number}",
                "season_number": season_number,
                "episode_number": episode_number,
                "air_date": air_date,
                "runtime": tmdb.get_readable_duration(listed.runtime),
                "production_code": None,
            },
            "cast": [],
            "crew": [],
            "external_links": {},
        }

    # Internals

    def _serve(self, kind, media_id, season_numbers):
        media_id = str(media_id)
        self.calls.append((kind, media_id, tuple(season_numbers)))
        for key in ((media_id, kind), (None, kind)):
            if key in self._failing:
                raise ProviderDown(status_code=self._failing[key])
        if media_id not in self.shows:
            msg = f"No fake show {media_id}"
            raise ProviderDown(msg, status_code=404)
        return self.shows[media_id]

    @staticmethod
    def _blocked_request(provider, method, url, *args, **kwargs):
        msg = f"Network call during a test: {provider} {method} {url}"
        raise RuntimeError(msg)

    def _raw_tv(self, show):
        """Return a TMDB /tv/{id} response for the show."""
        dated = [
            episode.air_date
            for episodes in show.seasons.values()
            for episode in episodes
            if episode.air_date is not None
        ]
        regular = [number for number in show.seasons if number >= 1]
        next_episode = None
        if show.next_episode_to_air:
            next_air = show.next_episode_to_air.get("air_date")
            next_episode = {
                "season_number": show.next_episode_to_air["season_number"],
                "episode_number": show.next_episode_to_air.get("episode_number"),
                "air_date": next_air.isoformat() if next_air else "",
            }
        return {
            "id": int(show.media_id) if show.media_id.isdigit() else show.media_id,
            "name": show.title,
            "number_of_episodes": sum(
                len(show.seasons[number]) for number in regular
            ),
            "number_of_seasons": len(regular),
            "next_episode_to_air": next_episode,
            "last_episode_to_air": None,
            "poster_path": show.poster_path,
            "backdrop_path": None,
            "overview": "",
            "genres": [],
            "vote_average": 0,
            "vote_count": 0,
            "popularity": 0,
            "first_air_date": min(dated).isoformat() if dated else "",
            "last_air_date": max(dated).isoformat() if dated else None,
            "status": show.status,
            "episode_run_time": [45],
            "content_ratings": {"results": []},
            "production_companies": [],
            "production_countries": [],
            "spoken_languages": [],
            "created_by": [],
            "credits": {"cast": [], "crew": []},
            "seasons": [
                {
                    "season_number": number,
                    "name": "Specials" if number == 0 else f"Season {number}",
                    "air_date": self._season_air_date(show, number),
                    "episode_count": len(show.seasons[number]),
                    "poster_path": show.season_posters.get(number),
                }
                for number in sorted(show.seasons)
            ],
            "recommendations": {"results": []},
            "external_ids": {},
            "watch/providers": {"results": {}},
        }

    def _raw_season(self, show, number):
        """Return a TMDB /tv/{id}/season/{n} response for the season."""
        return {
            "name": "Specials" if number == 0 else f"Season {number}",
            "season_number": number,
            "poster_path": show.season_posters.get(number),
            "overview": "",
            "vote_average": 0,
            "air_date": self._season_air_date(show, number),
            "episodes": [
                {
                    "episode_number": episode.episode_number,
                    "season_number": number,
                    "name": episode.title or f"Episode {episode.episode_number}",
                    "air_date": episode.air_date.isoformat() if episode.air_date else None,
                    "still_path": episode.still_path,
                    "runtime": episode.runtime,
                    "vote_count": 0,
                    "vote_average": 0,
                    "overview": "",
                }
                for episode in show.seasons[number]
            ],
        }

    @staticmethod
    def _season_air_date(show, number):
        if number in show.season_air_dates:
            value = show.season_air_dates[number]
            return value.isoformat() if value else ""
        dated = [episode.air_date for episode in show.seasons[number] if episode.air_date]
        return min(dated).isoformat() if dated else ""


def make_user(username=None, *, calendar="America/Los_Angeles", **fields):
    """Create a user whose stored calendar is `calendar` ("" for the server's)."""
    return get_user_model().objects.create_user(
        username=username or f"tv-user-{next(_ids)}",
        calendar_timezone=calendar,
        **fields,
    )


def make_show(
    fake=None,
    *,
    media_id=None,
    title="Test Show",
    seasons=None,
    status="Ended",
    next_episode_to_air=None,
    season_air_dates=None,
    image=DEFAULT_IMAGE,
):
    """Create a TMDB show's tv Item and, with a fake provider, what TMDB lists for it.

    Returns the tv Item. fake.show(item.media_id) is the provider's side to change.
    """
    media_id = str(media_id or 900000 + next(_ids))
    if fake is not None:
        fake.add_show(
            media_id,
            title=title,
            seasons=seasons,
            status=status,
            next_episode_to_air=next_episode_to_air,
            season_air_dates=season_air_dates,
        )
    return Item.objects.create(
        media_id=media_id,
        source=Sources.TMDB.value,
        media_type=MediaTypes.TV.value,
        title=title,
        image=image,
    )


def make_manual_show(*, title="Manual Show", seasons=None, image=DEFAULT_IMAGE):
    """Create a manual show's tv, season and episode Items; seasons maps numbers to counts.

    Manual entries have no air dates, so every listed episode counts as aired (A-63).
    """
    media_id = Item.generate_manual_id()
    show = Item.objects.create(
        media_id=media_id,
        source=Sources.MANUAL.value,
        media_type=MediaTypes.TV.value,
        title=title,
        image=image,
    )
    for season_number, count in (seasons if seasons is not None else {1: 3}).items():
        Item.objects.create(
            media_id=media_id,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            season_number=season_number,
            title=title,
            image=image,
        )
        for episode_number in range(1, count + 1):
            Item.objects.create(
                media_id=media_id,
                source=Sources.MANUAL.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=season_number,
                episode_number=episode_number,
                title=f"Episode {episode_number}",
                image=image,
            )
    return show


class patch_now(ContextDecorator):  # noqa: N801 - reads like mock.patch
    """Fix django.utils.timezone.now (and so app.user_calendar.now and auto_now_add).

    with patch_now(datetime(2026, 10, 25, 21, 0), tz="America/Los_Angeles") as clock:
        ...
        clock.advance(minutes=1)

    A naive moment is read in `tz` (default UTC).
    """

    def __init__(self, moment, *, tz="UTC"):
        self.moment = _aware(moment, tz)
        self._patcher = None

    def __enter__(self):
        self._patcher = mock.patch(
            "django.utils.timezone.now",
            side_effect=lambda: self.moment,
        )
        self._patcher.start()
        return self

    def __exit__(self, *exc_info):
        self._patcher.stop()
        self._patcher = None
        return False

    def set(self, moment, *, tz="UTC"):
        self.moment = _aware(moment, tz)

    def advance(self, **delta):
        self.moment += timedelta(**delta)
        return self.moment


def _aware(moment, tz):
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(tz))
    return moment.astimezone(UTC)
