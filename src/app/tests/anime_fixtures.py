"""Shared fakes for anime tracking tests (docs/anime-tracking-contract.md).

Every backend anime test uses these instead of the network:

- FakeAnimeProvider stands in for MAL and AniList. As a context manager it patches
  app.providers.mal.anime, app.providers.anilist.anime_episodes and
  app.providers.mal.anime_series, counts every call and can fail on demand. With
  block_network (the default) any other provider request raises.
- AnimeTestCase adds a fixed clock, a server on America/New_York and an account calendar
  on America/Los_Angeles, as production has, plus shorthand for the module's writes.
"""

import itertools
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.services import stats_time
from api.services.stats_scope import build_scope
from app import anime_tracking, user_calendar
from app.models import Anime, AnimeViewing, DiaryEntry, Item, MediaLike, MediaTypes, Sources
from app.providers import anilist, mal, services
from app.tests.tv_fixtures import ProviderDown, make_user, patch_now

PACIFIC_NOON = datetime(2026, 10, 25, 12, 0)
OCT_25 = date(2026, 10, 25)
_ids = itertools.count(90000)


@dataclass
class FakeTitle:
    """One anime as MAL and AniList describe it. Mutate it to change what they say."""

    media_id: str
    title: str = "Attack on Titan"
    total: int | None = 25
    status: str = "Finished"
    format: str = "TV"
    start_date: str | None = "2013-04-07"
    runtime: int = 24
    # AniList: {number: (title, image)}, {number: airing moment}, and the next one.
    episodes: dict = field(default_factory=dict)
    schedule: dict = field(default_factory=dict)
    next_episode: int | None = None
    next_air_at: datetime | None = None
    # During a break: (number, airing moment) of the last entry that has aired.
    last_aired: tuple | None = None
    # MAL's series order: the entries of mal.anime_series()["items"].
    series: list = field(default_factory=list)


class FakeAnimeProvider:
    """A fake MAL and AniList: configurable titles, a call log and failures."""

    def __init__(self, *, block_network=True):
        self.titles = {}
        self.calls = []
        self.failing = set()
        self.block_network = block_network
        self._stack = None

    def add(self, media_id=None, **values):
        """Add a title; return it. Every field of FakeTitle can be given."""
        media_id = str(media_id if media_id is not None else next(_ids))
        title = FakeTitle(media_id=media_id, **values)
        self.titles[media_id] = title
        return title

    def fail(self, *providers):
        """Make mal, anilist or both raise, as they do when they can't be reached."""
        self.failing = set(providers or ("mal", "anilist"))

    def recover(self):
        self.failing = set()

    def count(self, kind=None):
        return len([call for call in self.calls if kind is None or call == kind])

    def __enter__(self):
        self._stack = ExitStack()
        self._stack.enter_context(mock.patch.object(mal, "anime", self._mal_anime))
        self._stack.enter_context(mock.patch.object(mal, "anime_series", self._mal_series))
        self._stack.enter_context(mock.patch.object(anilist, "anime_episodes", self._anilist_episodes))
        # A page's enrichment is another matter: no anime rule reads it.
        self._stack.enter_context(mock.patch.object(anilist, "anime", return_value={}))
        self._stack.enter_context(mock.patch.object(mal, "anime_cast", return_value=[]))
        if self.block_network:
            self._stack.enter_context(mock.patch.object(services, "api_request", self._blocked))
        return self

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)

    @staticmethod
    def _blocked(provider, *args, **kwargs):
        msg = f"A test tried to reach {provider}"
        raise AssertionError(msg)

    def _mal_anime(self, media_id, *, timeout=None, retry_rate_limits=True):  # noqa: ARG002
        self.calls.append("mal")
        if "mal" in self.failing:
            raise ProviderDown
        title = self.titles[str(media_id)]
        relations = []
        if title.series:
            relations.append({"relation": "Sequel", "media_id": "0"})
        return {
            "media_id": str(media_id),
            "source": Sources.MAL.value,
            "media_type": MediaTypes.ANIME.value,
            "title": title.title,
            "display_title": title.title,
            "image": "https://example.com/poster.jpg",
            "max_progress": title.total,
            "series_format": title.format,
            "synopsis": "",
            "genres": [],
            "score": None,
            "score_count": None,
            "details": {
                "format": title.format,
                "start_date": title.start_date,
                "status": title.status,
                "episodes": title.total,
                "runtime": title.runtime,
            },
            "related": {"relations": relations, "recommendations": []},
        }

    def _mal_series(self, media_id):
        self.calls.append("series")
        if "mal" in self.failing:
            raise ProviderDown
        title = self.titles[str(media_id)]
        return {
            "series_id": title.media_id,
            "source": Sources.MAL.value,
            "media_type": MediaTypes.ANIME.value,
            "name": title.title,
            "items": title.series,
        }

    def _anilist_episodes(self, mal_ids, *, timeout=None, retry_rate_limits=True):  # noqa: ARG002
        self.calls.append("anilist")
        if "anilist" in self.failing:
            raise ProviderDown
        results = {}
        for media_id in mal_ids:
            title = self.titles.get(str(media_id))
            if title is None:
                continue
            results[str(media_id)] = {
                "next": (
                    {
                        "episode": title.next_episode,
                        "airing_at": int(title.next_air_at.timestamp()) if title.next_air_at else None,
                    }
                    if title.next_episode
                    else None
                ),
                "schedule": {number: int(moment.timestamp()) for number, moment in title.schedule.items()},
                "episodes": {
                    number: {"title": values[0], "image": values[1]}
                    for number, values in title.episodes.items()
                },
            }
            if title.last_aired:
                results[str(media_id)]["last_aired"] = {
                    "episode": title.last_aired[0],
                    "airing_at": int(title.last_aired[1].timestamp()),
                }
        return results


def make_item(fake, title=None, **values):
    """Create a title's Item and tell the fake provider about it."""
    title = title or fake.add(**values)
    return Item.objects.create(
        source=Sources.MAL.value,
        media_type=MediaTypes.ANIME.value,
        media_id=title.media_id,
        title=title.title,
        image="https://example.com/poster.jpg",
        runtime_minutes=title.runtime or None,
    )


@override_settings(TIME_ZONE="America/New_York")
class AnimeTestCase(TestCase):
    """A fake MAL and AniList, a fixed clock and a Los Angeles calendar."""

    start = PACIFIC_NOON
    start_tz = "America/Los_Angeles"

    def setUp(self):
        super().setUp()
        self.fake = FakeAnimeProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(self.start, tz=self.start_tz).__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        patcher = mock.patch("app.models.Item.fetch_releases")
        self.fetch_releases = patcher.start()
        self.addCleanup(patcher.stop)
        self.user = make_user(calendar="America/Los_Angeles")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def title(self, **values):
        return make_item(self.fake, **values)

    def airing(self, *, aired=7, total=12, **values):
        """A weekly title with `aired` episodes out and the next on Oct 29 (a Thursday)."""
        next_air_at = datetime(2026, 10, 29, 15, 0, tzinfo=UTC)
        return self.title(
            total=total,
            status="Airing",
            next_episode=aired + 1,
            next_air_at=next_air_at,
            schedule={aired + 1: next_air_at},
            **values,
        )

    def runner(self, *, aired=7, **values):
        """A long runner: no total, with AniList naming the next episode."""
        return self.airing(aired=aired, total=None, **values)

    # The API, as the app and installed builds call it

    def url(self, item, suffix=""):
        return f"/api/v1/tracking/{item.source}/anime/{item.media_id}/{suffix}"

    def action(self, item, name, expect=200, **data):
        response = self.api.post(self.url(item, f"actions/{name}/"), data, format="json")
        self.assertEqual(response.status_code, expect, getattr(response, "data", None))
        return response

    def write(self, item, expect=200, **data):
        """PATCH the tracking row, as installed builds do."""
        response = self.api.patch(self.url(item), data, format="json")
        self.assertEqual(response.status_code, expect, getattr(response, "data", None))
        return response

    def post_log(self, item, expect=201, **data):
        body = {
            "ref": {"source": item.source, "media_type": "anime", "media_id": item.media_id},
            "consumed_at": user_calendar.today(self.user).isoformat(),
            **data,
        }
        response = self.api.post("/api/v1/diary/", body, format="json")
        self.assertEqual(response.status_code, expect, getattr(response, "data", None))
        return response

    def post_like(self, item, expect=200):
        body = {"ref": {"source": item.source, "media_type": "anime", "media_id": item.media_id}}
        response = self.api.post("/api/v1/me/liked-media/", body, format="json")
        self.assertEqual(response.status_code, expect, getattr(response, "data", None))
        return response

    def delete_like(self, item, expect=200):
        body = {"ref": {"source": item.source, "media_type": "anime", "media_id": item.media_id}}
        response = self.api.delete("/api/v1/me/liked-media/", body, format="json")
        self.assertEqual(response.status_code, expect, getattr(response, "data", None))
        return response

    def get_rows(self, item):
        response = self.api.get(self.url(item, "rows/"))
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return response.data

    def activity(self):
        """Return what the user's own activity shows, newest first."""
        response = self.api.get(f"/api/v1/users/{self.user.username}/activity/")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return response.data["results"]

    def stats_time(self, year=None):
        """Return (minutes, episodes) of anime in a year, or in All Time with none."""
        params = {"media_type": "anime", "start_date": "all", "end_date": "all"}
        if year is not None:
            params.update({"start_date": f"{year}-01-01", "end_date": f"{year}-12-31"})
        scope = build_scope(self.user, self.user, params)
        time = stats_time.type_time(scope, "anime")
        return time.value, time.progress

    def row(self, item):
        return Anime.objects.filter(user=self.user, item=item).first()

    def viewings(self, item):
        return list(AnimeViewing.objects.filter(anime__user=self.user, anime__item=item).order_by("episode_number", "id"))

    def watched(self, item):
        return {viewing.episode_number for viewing in self.viewings(item)}

    def logs(self, item):
        return list(DiaryEntry.objects.filter(user=self.user, item=item).order_by("id"))

    def liked(self, item):
        return MediaLike.objects.filter(user=self.user, item=item).exists()

    def state(self, item):
        return anime_tracking.state_payload(self.user, item)

    def rows(self, item):
        return anime_tracking.rows_payload(self.user, item)

    def tick(self, item, *numbers, **kwargs):
        result = None
        for number in numbers:
            result = anime_tracking.tick(self.user, item, number, **kwargs)
        return result

    def log(self, item, *, day=None, **kwargs):
        return anime_tracking.create_log(self.user, item, consumed_at=day or user_calendar.today(self.user), **kwargs)
