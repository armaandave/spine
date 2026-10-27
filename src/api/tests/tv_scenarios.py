"""Drivers and a snapshot for the TV acceptance suite (TV-207, TV-705; plan 4.1).

The contract's acceptance preamble asks for every scenario to run through the API,
through the model hooks the web and webhooks use, and through an import where one
applies, and for all paths to reach the same end state. This module gives the suite:

- snapshot(user, show_item): what every surface reads for a user's show, with no row ids
  or write times, so two accounts that did the same things compare equal;
- APIDriver: the new build (the actions endpoint, diary, likes and tracking, with a JWT
  and X-Spine-Timezone), and with installed=True the installed builds' request shapes
  (EpisodeWatchView, SeasonWatchView, datetime log dates, PATCH tracking, liked-media);
- WebDriver: a session client with CSRF against the routed web views;
- WebhookDriver: Plex, Jellyfin and Emby payloads posted to their webhook URLs;
- HookDriver: plain model operations (Episode.objects.create, episode.delete(), status
  saves), worked out by app.tv_signals when the transaction commits;
- ImportDriver: a Yamtrack CSV through integrations.imports.yamtrack.importer, new and
  overwrite modes, including a CSV exported from another account;
- TVAcceptanceTestCase: a fake TMDB, a clock patched through django.utils.timezone.now
  at noon Pacific with the server on America/New_York, and helpers that run the same
  steps through several drivers (one fresh account each) and compare their snapshots.

Every write runs inside captureOnCommitCallbacks(execute=True), so on-commit work (the
hook reconciles, fetch_releases, external ratings) runs as it does after a real commit.
No network and no .env: TMDB is app.tests.tv_fixtures.FakeTVProvider.
"""

import csv
import io
import json
import pickle
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from unittest import mock
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from app import single_weight, tv_listing, tv_tracking, user_calendar
from app.models import (
    TV,
    DiaryEntry,
    Episode,
    Item,
    MediaLike,
    MediaTypes,
    Season,
    Status,
)
from app.providers import tmdb
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from integrations import exports
from integrations.imports import yamtrack
from social.models import Activity, ProgressChange

PACIFIC = "America/Los_Angeles"
NEW_YORK = "America/New_York"
NOON_PACIFIC = datetime(2026, 10, 25, 12, 0)  # noqa: DTZ001 - read in Pacific time by patch_now
PACIFIC_9PM = datetime(2026, 10, 25, 21, 0)  # noqa: DTZ001 - midnight on Oct 26 in New York
OCT_25 = date(2026, 10, 25)
OCT_26 = date(2026, 10, 26)

WATCHING = Status.IN_PROGRESS.value
PLANNING = Status.PLANNING.value
PAUSED = Status.PAUSED.value
DROPPED = Status.DROPPED.value
COMPLETED = Status.COMPLETED.value

TV_TYPE = MediaTypes.TV.value
SEASON_TYPE = MediaTypes.SEASON.value
EPISODE_TYPE = MediaTypes.EPISODE.value

# Provider calls media detail makes beside TMDB's TV metadata (as test_tv_tracking_api).
PROVIDER_PATCHES = (
    ("app.providers.tmdb.get_title_logo", None),
    ("app.providers.tmdb.get_title_logos", []),
    ("app.providers.tmdb.get_backdrop_images", []),
    ("app.providers.tmdb.get_season_backdrop_images", []),
    ("app.providers.tmdb.get_episode_backdrop_images", []),
    ("app.providers.mdblist.get_media_ratings", {}),
)

# The Yamtrack columns hand-written import rows use (the importer ignores missing
# columns it doesn't need); A-45 uses the full export header instead.
IMPORT_HEADER = [
    "media_id", "source", "media_type", "title", "image", "season_number",
    "episode_number", "score", "progress", "status", "start_date", "end_date",
    "notes", "progressed_at",
]

# Keys of the snapshot every path compares by default.
ALL_KEYS = (
    "in_library",
    "status",
    "choice",
    "ended_choice",
    "legacy_pending",
    "finished",
    "caught_up",
    "counts",
    "up_next",
    "next_airing",
    "rewatch",
    "last_watched_on",
    "season_rows",
    "seasons",
    "watched_pass",
    "watched_all",
    "viewings",
    "stars",
    "hearts",
    "logs",
    "activity",
    "activity_detail",
    "progress_changes",
)
# What an import must agree on with the app (TV-800, TV-803: "the same watched
# episodes, sections and stars"). An imported In progress show is a Watching choice
# (TV-800), and imports write no progress changes for installed builds' Home.
IMPORT_KEYS = tuple(key for key in ALL_KEYS if key not in ("choice", "progress_changes"))
# Model hooks see rows the tv_tracking code didn't write, so their first work-out of a
# season records no progress change (those rows have nothing to compare against).
HOOK_KEYS = tuple(key for key in ALL_KEYS if key != "progress_changes")


class Unsupported(Exception):  # noqa: N818 - reads as "this path can't do that"
    """A driver was asked for a step its entry point doesn't offer."""


# ---------------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------------


def family(show_item, prefix="item__"):
    """Return a filter for a show's tv, season and episode Items."""
    return Q(
        **{
            f"{prefix}media_id": show_item.media_id,
            f"{prefix}source": show_item.source,
            f"{prefix}media_type__in": tv_tracking.TV_TYPES,
        },
    )


def _level(item):
    return {TV_TYPE: "show", SEASON_TYPE: "season", EPISODE_TYPE: "episode"}[item.media_type]


def log_day(entry, user):
    """Return a log's calendar day: a UTC-midnight carrier keeps its day (TV-407)."""
    if single_weight.is_calendar_datetime(entry.consumed_at):
        return entry.consumed_at.astimezone(UTC).date()
    return user_calendar.day_of(entry.consumed_at, user)


def _rating(value):
    if value is None:
        return None
    return str(Decimal(value).quantize(Decimal("0.1")))


def log_keys(user, show_item):
    """Name each log: {entry id: (level, season, episode, day, n)}.

    n counts same-day logs of the same target, in the order they were saved.
    """
    keys = {}
    seen = Counter()
    entries = (
        DiaryEntry.objects.filter(family(show_item), user=user)
        .select_related("item")
        .order_by("created_at", "id")
    )
    for entry in entries:
        base = (
            _level(entry.item),
            entry.item.season_number if entry.item.media_type != TV_TYPE else None,
            entry.item.episode_number,
            log_day(entry, user).isoformat(),
        )
        seen[base] += 1
        keys[entry.pk] = (*base, seen[base])
    return keys


def snapshot(user, show_item):  # noqa: C901, PLR0912, PLR0915
    """Return everything the contract says every surface must agree on (TV-705).

    Library membership, status and choice; each started season's status and counts;
    the watched episodes in the current pass and in all history; each viewing's kind,
    owner (its log, a mark, or nothing for a tick) and day; up next, caught up, next
    airing and finished; stars and hearts at every level with their sources; each log
    with its coverage and stamp; last_watched_on; the user's Activity rows by verb and
    ProgressChange rows per season. Logs are named by level, target, day and order, so
    no row id or write time appears.
    """
    tv = TV.objects.filter(user=user, item=show_item).first()
    keys = log_keys(user, show_item)

    def owner(entry_id, kind):
        if entry_id is not None:
            return keys.get(entry_id, "deleted log")
        return "mark" if kind == tv_tracking.FILL else None

    rewatch = None
    if tv is not None and tv.rewatch_started_at is not None:
        rewatch = (tv.rewatch_started_at, tv.rewatch_season_number)

    def in_pass(season_number, created_at):
        if rewatch is None:
            return True
        started_at, scope = rewatch
        if scope is not None and season_number != scope:
            return True
        return created_at >= started_at

    viewings = []
    watched_pass = set()
    watched_all = set()
    seasons = {}
    season_rows = []
    season_stars = {}
    season_hearts = {}
    if tv is not None:
        rows = Episode.objects.filter(related_season__related_tv=tv).select_related("item")
        for row in rows:
            position = (row.item.season_number, row.item.episode_number)
            watched_all.add(position)
            if in_pass(position[0], row.created_at):
                watched_pass.add(position)
            viewings.append(
                (
                    *position,
                    row.kind,
                    owner(row.diary_entry_id, row.kind),
                    row.watched_on.isoformat() if row.watched_on else None,
                ),
            )
        for season in Season.objects.filter(related_tv=tv).select_related("item"):
            number = season.item.season_number
            season_rows.append(number)
            if season.is_started:
                seasons[number] = {
                    "status": season.status,
                    "watched": season.watched_count,
                    "aired": season.aired_count,
                    "first_gap": season.first_gap_episode,
                    "last_watched_on": season.last_watched_on,
                }
            if season.score is not None or season.rating_source_id is not None:
                season_stars[number] = (
                    _rating(season.score),
                    keys.get(season.rating_source_id) if season.rating_source_id else None,
                )
            if season.like_source_id is not None or season.like_is_independent:
                season_hearts[number] = (
                    keys.get(season.like_source_id) if season.like_source_id else None,
                    season.like_is_independent,
                )

    likes = MediaLike.objects.filter(family(show_item), user=user).select_related("item")
    liked = {"show": False, "seasons": [], "episodes": []}
    for like in likes:
        if like.item.media_type == TV_TYPE:
            liked["show"] = True
        elif like.item.media_type == SEASON_TYPE:
            liked["seasons"].append(like.item.season_number)
        else:
            liked["episodes"].append((like.item.season_number, like.item.episode_number))

    logs = []
    episode_stars = {}
    entries = (
        DiaryEntry.objects.filter(family(show_item), user=user)
        .select_related("item")
        .order_by("created_at", "id")
    )
    newest = {}
    for entry in entries:
        tv_part = (entry.progress_snapshot or {}).get("tv") if isinstance(entry.progress_snapshot, dict) else None
        covers = None
        stamp = None
        if isinstance(tv_part, dict):
            stamp = tv_part.get("v")
            if tv_part.get("covers") is not None:
                covers = {
                    int(number): sorted(int(episode) for episode in episodes)
                    for number, episodes in tv_part["covers"].items()
                }
        logs.append(
            (
                keys[entry.pk],
                _rating(entry.rating),
                entry.liked,
                entry.is_rewatch,
                json.dumps(covers, sort_keys=True) if covers is not None else None,
                stamp,
            ),
        )
        if entry.item.media_type == EPISODE_TYPE and entry.rating is not None:
            position = (entry.item.season_number, entry.item.episode_number)
            order = (log_day(entry, user), entry.created_at, entry.pk)
            if position not in newest or order > newest[position]:
                newest[position] = order
                episode_stars[position] = _rating(entry.rating)

    activity_rows = list(
        Activity.objects.filter(family(show_item), actor=user).values_list("verb", "snapshot", "visibility"),
    )
    activity = Counter(verb for verb, _, _ in activity_rows)
    activity_detail = sorted(
        ((verb, (snapshot_value or {}).get("rating"), visibility) for verb, snapshot_value, visibility in activity_rows),
        key=repr,
    )
    progress_changes = Counter(
        ProgressChange.objects.filter(
            actor=user,
            item__media_id=show_item.media_id,
            item__source=show_item.source,
            item__media_type=SEASON_TYPE,
        ).values_list("item__season_number", flat=True),
    )

    return {
        "in_library": tv is not None,
        "status": tv.status if tv else None,
        "choice": tv.choice if tv else None,
        "ended_choice": tv.ended_choice if tv else None,
        "legacy_pending": tv.legacy_pending if tv else None,
        "finished": tv.finished if tv else None,
        "caught_up": tv.caught_up if tv else None,
        "counts": (tv.watched_count, tv.aired_count) if tv else None,
        "up_next": (
            (tv.up_next_season, tv.up_next_episode, tv.up_next_air_date, tv.up_next_is_new) if tv else None
        ),
        "next_airing": (tv.next_air_season, tv.next_air_episode, tv.next_air_date) if tv else None,
        "rewatch": ("show" if rewatch[1] is None else ("season", rewatch[1])) if rewatch else None,
        "last_watched_on": tv.last_watched_on if tv else None,
        "season_rows": sorted(season_rows),
        "seasons": seasons,
        "watched_pass": sorted(watched_pass),
        "watched_all": sorted(watched_all),
        "viewings": sorted(viewings, key=repr),
        "stars": {
            "show": (
                _rating(tv.score) if tv else None,
                keys.get(tv.rating_source_id) if tv and tv.rating_source_id else None,
            ),
            "seasons": season_stars,
            "episodes": episode_stars,
        },
        "hearts": {
            "show": liked["show"],
            "show_source": (
                (keys.get(tv.like_source_id) if tv.like_source_id else None, tv.like_is_independent)
                if tv
                else None
            ),
            "seasons": sorted(liked["seasons"]),
            "season_sources": season_hearts,
            "episodes": sorted(liked["episodes"]),
        },
        "logs": logs,
        "activity": dict(activity),
        "activity_detail": activity_detail,
        "progress_changes": dict(progress_changes),
    }


def pick(snap, keys):
    """Return only some keys of a snapshot."""
    return {key: snap[key] for key in keys}


# ---------------------------------------------------------------------------------
# Drivers
# ---------------------------------------------------------------------------------


class Driver:
    """One entry point acting for one account."""

    path = ""
    variant = ""
    name = ""

    def __init__(self, case, user):
        """Act for user in the running test case."""
        self.case = case
        self.user = user

    def __repr__(self):
        """Name the path and account, for failure messages."""
        return f"<{self.path} driver for {self.user.username}>"

    def step(self):
        """Advance the clock a minute before each step, so recorded order is real."""
        self.case.clock.advance(minutes=1)

    def committing(self):
        return self.case.captureOnCommitCallbacks(execute=True)

    def tv(self, show):
        return TV.objects.filter(user=self.user, item=show).first()

    def unsupported(self, step):
        msg = f"The {self.path} path has no {step} step."
        raise Unsupported(msg)

    # Steps every driver may offer; drivers override what their entry point has.

    def tick(self, show, season, episode, **kwargs):
        self.unsupported("tick")

    def untick(self, show, season, episode, **kwargs):
        self.unsupported("untick")

    def mark(self, show, season=None, **kwargs):
        self.unsupported("mark")

    def unmark(self, show, season=None, **kwargs):
        self.unsupported("unmark")

    def choose(self, show, action, **kwargs):
        self.unsupported("choose")

    def log(self, show, season=None, episode=None, **kwargs):
        self.unsupported("log")

    def rate(self, show, rating, season=None, **kwargs):
        self.unsupported("rate")

    def like(self, show, season=None, episode=None, *, liked=True, **kwargs):
        self.unsupported("like")

    def remove(self, show, **kwargs):
        self.unsupported("remove")


def _answer(response, expect):
    """Check a response's status when the step names one."""
    if expect is not None:
        status_code = getattr(response, "status_code", None)
        body = getattr(response, "data", None) or getattr(response, "content", b"")[:400]
        assert status_code == expect, f"expected {expect}, got {status_code}: {body}"  # noqa: S101
    return response


class APIDriver(Driver):
    """The app through the API: the new build, or installed builds (installed=True).

    Requests carry a JWT and X-Spine-Timezone, as the app sends them. The new build
    sends a mutation ID with every TV action and log, reads the eye's tap before an
    unmark and the show's version before Remove or a log deletion, as the app does (plan
    2.3). Installed builds use only today's request shapes (plan 2.5).
    """

    def __init__(self, case, user, *, installed=False, timezone=PACIFIC):
        """Sign in with a JWT and send the timezone header, as the app does."""
        super().__init__(case, user)
        self.installed = installed
        self.path = "installed" if installed else "api"
        self.client = APIClient()
        headers = {"HTTP_AUTHORIZATION": f"Bearer {AccessToken.for_user(user)}"}
        if timezone:
            headers["HTTP_X_SPINE_TIMEZONE"] = timezone
        self.client.credentials(**headers)

    # Plumbing

    def request(self, method, url, body=None, *, expect=None, step=True):
        if step:
            self.step()
        with self.committing():
            response = getattr(self.client, method)(url, body, format="json")
        return _answer(response, expect)

    def get(self, url, params=None, *, expect=200):
        response = self.client.get(url, params or {})
        return _answer(response, expect)

    @staticmethod
    def tracking_url(show, path=""):
        return f"/api/v1/tracking/{show.source}/tv/{show.media_id}/{path}"

    @staticmethod
    def ref(show, season=None, episode=None):
        if episode is not None:
            media_type = EPISODE_TYPE
        elif season is not None:
            media_type = SEASON_TYPE
        else:
            media_type = TV_TYPE
        return {
            "source": show.source,
            "media_type": media_type,
            "media_id": show.media_id,
            "season_number": season,
            "episode_number": episode,
        }

    def action(self, show, action, *, expect=200, **body):
        """POST tracking/{source}/tv/{id}/actions/{action}/ (new build only)."""
        if self.installed:
            self.unsupported(action)
        body.setdefault("mutation_id", str(uuid4()))
        return self.request("post", self.tracking_url(show, f"actions/{action}/"), body, expect=expect)

    # Reads

    def detail(self, show, season=None, episode=None, *, expect=200):
        if episode is not None:
            return self.get(
                f"/api/v1/media/{show.source}/episode/{show.media_id}/",
                {"season_number": season, "episode_number": episode},
                expect=expect,
            )
        if season is not None:
            return self.get(f"/api/v1/media/{show.source}/tv/{show.media_id}/seasons/{season}/", expect=expect)
        return self.get(f"/api/v1/media/{show.source}/tv/{show.media_id}/", expect=expect)

    def state(self, show, season=None, episode=None):
        """Return user_state.tv of a show, season or episode page."""
        return self.detail(show, season, episode).data["user_state"]["tv"]

    def episode_state(self, show, season, episode):
        return self.state(show, season, episode)["episode"]

    def eye(self, show, season=None):
        state = self.state(show, season)
        return state["eye"] if season is None else state["season"]["eye"]

    def library(self, **params):
        return self.get("/api/v1/tracking/", params).data["results"]

    def library_row(self, show):
        rows = [
            row
            for row in self.library(media_type=TV_TYPE)
            if row["media"]["ref"]["media_id"] == show.media_id
        ]
        return rows[0] if rows else None

    def continue_watching(self):
        return [
            row["media"]["ref"]["media_id"]
            for row in self.library(media_type=TV_TYPE, continue_watching="true")
        ]

    def diary(self, show):
        response = self.get(
            "/api/v1/diary/",
            {"media_type": [TV_TYPE, SEASON_TYPE, EPISODE_TYPE]},
        )
        return [
            entry
            for entry in response.data["results"]
            if entry["media"]["ref"]["media_id"] == show.media_id
        ]

    def entry(self, entry_id):
        return self.get(f"/api/v1/diary/{entry_id}/").data

    # Viewings

    def tick(self, show, season, episode, *, expect=200, **body):
        if self.installed:
            url = self.tracking_url(show, f"seasons/{season}/episodes/{episode}/watch/")
            watched_at = user_calendar.now().isoformat()
            return self.request("post", url, {"watched_at": watched_at, **body}, expect=expect)
        return self.action(show, "tick", season_number=season, episode_number=episode, expect=expect, **body)

    def untick(self, show, season, episode, *, expect=200, viewing_id="state", **body):
        """Untick as the app does, naming the viewing the episode's state names."""
        if self.installed:
            url = self.tracking_url(show, f"seasons/{season}/episodes/{episode}/watch/")
            return self.request("delete", url, expect=expect)
        if viewing_id == "state":
            viewing_id = self.episode_state(show, season, episode)["untick_viewing_id"]
        if viewing_id is not None:
            body["viewing_id"] = viewing_id
        return self.action(show, "untick", season_number=season, episode_number=episode, expect=expect, **body)

    # Marks

    def mark(self, show, season=None, *, expect=200):
        if self.installed:
            if season is None:
                return self.write_status(show, COMPLETED, expect=expect)
            return self.request("post", self.tracking_url(show, f"seasons/{season}/watch/"), {}, expect=expect)
        body = {} if season is None else {"season_number": season}
        return self.action(show, "mark_watched", expect=expect, **body)

    def unmark(self, show, season=None, *, expect=200):
        """Tap a filled eye as the app does, by the eye's tap (plan 2.3.2).

        Returns the response, or the eye itself when the app would only show its
        message (blocked) and send nothing.
        """
        if self.installed:
            if season is None:
                self.unsupported("show eye")
            return self.request("delete", self.tracking_url(show, f"seasons/{season}/watch/"), expect=expect)
        state = self.state(show, season)
        eye = state["eye"] if season is None else state["season"]["eye"]
        body = {} if season is None else {"season_number": season}
        if eye["tap"] == "blocked":
            return eye
        if eye["tap"] == "unmark_confirm":
            body.update(confirm=True, expected_version=state["version"])
        return self.action(show, "unmark", expect=expect, **body)

    def mark_up_to(self, show, season, episode, *, expect=200):
        return self.action(show, "mark_up_to", season_number=season, episode_number=episode, expect=expect)

    def answer(self, show, question, *, yes, expect=200):
        """Answer the earlier-episodes question: Yes sends mark_earlier, No nothing."""
        if not yes or question is None:
            return None
        return self.action(
            show,
            "mark_earlier",
            expect=expect,
            **{
                "from": question["from"],
                "to": question["to"],
                "offer_season_number": question.get("offer_season_number"),
            },
        )

    # Choices, rewatch, remove

    def choose(self, show, action, *, expect=200):
        """watching, resume, planning, pause or drop."""
        if self.installed:
            status = {"watching": WATCHING, "resume": WATCHING, "planning": PLANNING, "pause": PAUSED, "drop": DROPPED}
            return self.write_status(show, status[action], expect=expect)
        return self.action(show, action, expect=expect)

    def rewatch(self, show, season=None, *, expect=200):
        body = {} if season is None else {"season_number": season}
        return self.action(show, "rewatch", expect=expect, **body)

    def stop_rewatch(self, show, *, expect=200):
        return self.action(show, "stop_rewatch", expect=expect)

    def remove(self, show, *, expect=None):
        if self.installed:
            return self.request("delete", self.tracking_url(show), expect=expect or 204)
        version = self.state(show)["version"]
        return self.action(show, "remove", expected_version=version, expect=expect or 200)

    def write_status(self, show, status, season=None, *, expect=200, **body):
        """PATCH tracking/{source}/{tv|season}/{id}/, as installed builds' forms do."""
        if season is None:
            url = self.tracking_url(show)
        else:
            url = f"/api/v1/tracking/{show.source}/season/{show.media_id}/"
            body["season_number"] = season
        return self.request("patch", url, {"status": status, **body}, expect=expect)

    def delete_season(self, show, season, *, expect=204):
        url = f"/api/v1/tracking/{show.source}/season/{show.media_id}/?season_number={season}"
        return self.request("delete", url, expect=expect)

    # Opinions

    def rate(self, show, rating, season=None, *, expect=200):
        if self.installed:
            if season is not None:
                self.unsupported("season stars")
            return self.request("patch", self.tracking_url(show), {"rating": rating}, expect=expect)
        body = {"rating": rating}
        if season is not None:
            body["season_number"] = season
        return self.action(show, "rate", expect=expect, **body)

    def like(self, show, season=None, episode=None, *, liked=True, expect=200):
        method = "post" if liked else "delete"
        return self.request(method, "/api/v1/me/liked-media/", {"ref": self.ref(show, season, episode)}, expect=expect)

    # Logs

    def _log_date(self, day):
        if self.installed:
            # Installed builds send the picked day as a moment (MediaLogView.swift:304).
            return datetime.combine(day, time(20, 0), tzinfo=ZoneInfo(PACIFIC)).astimezone(UTC).isoformat()
        return day.isoformat()

    def log(
        self,
        show,
        season=None,
        episode=None,
        *,
        day=OCT_25,
        rating=None,
        liked=False,
        is_rewatch=None,
        review="",
        expect=201,
        mutation_id=None,
    ):
        body = {
            "ref": self.ref(show, season, episode),
            "consumed_at": self._log_date(day),
            "review": review,
            "liked": liked,
        }
        if rating is not None:
            body["rating"] = rating
        if self.installed:
            body.update(
                review_title="",
                is_rewatch=bool(is_rewatch),
                auto_mark_consumed=True,
                contains_spoilers=False,
                visibility="public",
                tags=[],
            )
        else:
            body["mutation_id"] = mutation_id or str(uuid4())
            if is_rewatch is not None:
                body["is_rewatch"] = is_rewatch
        return self.request("post", "/api/v1/diary/", body, expect=expect)

    def edit_log(self, entry_id, *, expect=200, **fields):
        if "day" in fields:
            fields["consumed_at"] = self._log_date(fields.pop("day"))
        return self.request("patch", f"/api/v1/diary/{entry_id}/", fields, expect=expect)

    def delete_log(self, entry_id, *, expect=204, version="current"):
        """Delete a log: the new build sends the version its dialog showed."""
        url = f"/api/v1/diary/{entry_id}/"
        if not self.installed:
            if version == "current":
                version = self.entry(entry_id)["tv_log"]["state_version"]
            url += f"?expected_version={version}&mutation_id={uuid4()}"
        return self.request("delete", url, expect=expect)


class WebDriver(Driver):
    """The web through a session client that enforces CSRF (plan 1.4's view table)."""

    path = "web"

    def __init__(self, case, user):
        """Sign in with a session and a CSRF cookie."""
        super().__init__(case, user)
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(user)
        self.csrf = _get_new_csrf_string()
        self.client.cookies[settings.CSRF_COOKIE_NAME] = self.csrf

    def post(self, name, *args, data=None, htmx=False, expect=None):
        self.step()
        data = dict(data or {})
        headers = {}
        if htmx:
            headers = {"HX-Request": "true", "X-CSRFToken": self.csrf}
        else:
            data["csrfmiddlewaretoken"] = self.csrf
        with self.committing():
            response = self.client.post(reverse(name, args=args), data, headers=headers)
        return _answer(response, expect)

    @staticmethod
    def show_args(show):
        return (show.source, TV_TYPE, show.media_id)

    @staticmethod
    def episode_args(show, season, episode):
        return (show.source, EPISODE_TYPE, show.media_id, season, episode)

    # Viewings

    def tick(self, show, season, episode, *, expect=200):
        return self.post("watch_episode", *self.episode_args(show, season, episode), htmx=True, expect=expect)

    def untick(self, show, season, episode, *, expect=200):
        return self.post("unwatch_episode", *self.episode_args(show, season, episode), htmx=True, expect=expect)

    def episode_form(self, show, season, episode, day, *, expect=302):
        """Tick through the episode form, dated with the typed day (TV-100)."""
        data = {
            "media_id": show.media_id,
            "source": show.source,
            "season_number": season,
            "episode_number": episode,
            "end_date": day.isoformat(),
        }
        return self.post("episode_save", data=data, expect=expect)

    def progress(self, show, season, operation, *, expect=200):
        """Press the season card's + or - button (progress_edit)."""
        row = Season.objects.get(related_tv=self.tv(show), item__season_number=season)
        return self.post(
            "progress_edit",
            SEASON_TYPE,
            row.pk,
            data={"operation": operation},
            htmx=True,
            expect=expect,
        )

    # Marks

    def mark(self, show, season=None, *, expect=200):
        if season is None:
            return self.post("mark_tv_watched", *self.show_args(show), htmx=True, expect=expect)
        return self.post("mark_season_watched", *self.show_args(show), season, htmx=True, expect=expect)

    def unmark(self, show, season=None, *, expect=200):
        if season is None:
            return self.post("unmark_tv_watched", *self.show_args(show), htmx=True, expect=expect)
        return self.post("unmark_season_watched", *self.show_args(show), season, htmx=True, expect=expect)

    # Choices and the tracking form

    def choose(self, show, action, *, expect=200):
        if action == "planning":
            return self.save_form(show, status=PLANNING)
        name = {
            "watching": "start_tracking_tv",
            "resume": "resume_media",
            "pause": "pause_media",
            "drop": "drop_media",
        }[action]
        return self.post(name, *self.show_args(show), htmx=True, expect=expect)

    def save_form(self, show, season=None, *, expect=302, **fields):
        """POST media_save as the tracking form does, prefilled with saved values."""
        data = {"media_id": show.media_id, "source": show.source, "score": "", "notes": ""}
        tv = self.tv(show)
        if season is None:
            data["media_type"] = TV_TYPE
            if tv is not None:
                data.update(instance_id=tv.pk, status=tv.status, score=tv.score if tv.score is not None else "")
        else:
            data.update(media_type=SEASON_TYPE, season_number=season)
            row = Season.objects.filter(related_tv=tv, item__season_number=season).first() if tv else None
            if row is not None:
                data.update(instance_id=row.pk, status=row.status, score=row.score if row.score is not None else "")
            else:
                data["status"] = tv.status if tv is not None else WATCHING
        data.update({key: ("" if value is None else value) for key, value in fields.items()})
        return self.post("media_save", data=data, expect=expect)

    def write_status(self, show, status, season=None, *, expect=302):
        return self.save_form(show, season, status=status, expect=expect)

    def rate(self, show, rating, season=None, *, expect=302):
        """Set stars with the tracking form's score, on TV's 0-10 scale."""
        return self.save_form(show, season, score=rating, expect=expect)

    def remove(self, show, *, expect=302):
        return self.post("media_delete", data={"instance_id": self.tv(show).pk, "media_type": TV_TYPE}, expect=expect)

    def delete_season(self, show, season, *, expect=302):
        row = Season.objects.get(related_tv=self.tv(show), item__season_number=season)
        return self.post("media_delete", data={"instance_id": row.pk, "media_type": SEASON_TYPE}, expect=expect)

    # Logs (the web logs shows and seasons; it has no episode log)

    def log(
        self,
        show,
        season=None,
        episode=None,
        *,
        day=OCT_25,
        rating=None,
        liked=False,
        is_rewatch=False,
        review="",
        expect=200,
        mutation_id=None,
    ):
        if episode is not None:
            self.unsupported("episode log")
        args = [show.source, TV_TYPE if season is None else SEASON_TYPE, show.media_id]
        if season is not None:
            args.append(season)
        data = {
            "watch_date": day.isoformat(),
            # The web's stars are whole and half stars (0-5); TV keeps 0-10 (TV-504).
            "rating": "" if rating is None else str(Decimal(rating) / 2),
            "liked": "true" if liked else "false",
            "review": review,
            "mutation_id": mutation_id or str(uuid4()),
        }
        if is_rewatch:
            data["is_rewatch"] = "on"
        return self.post("add_movie_diary_entry", *args, data=data, expect=expect)

    def edit_log(self, entry_id, *, expect=200, **fields):
        """Edit a log with the diary edit form, prefilled with its saved values."""
        entry = DiaryEntry.objects.get(pk=entry_id)
        rating = fields.pop("rating", entry.rating)
        data = {
            "watch_date": fields.pop("day", log_day(entry, self.user)).isoformat(),
            "rating": "" if rating is None else str(Decimal(rating) / 2),
            "review": fields.pop("review", entry.review),
            "liked": "true" if fields.pop("liked", entry.liked) else "false",
        }
        if fields.pop("is_rewatch", entry.is_rewatch):
            data["is_rewatch"] = "on"
        assert not fields, fields  # noqa: S101
        return self.post("update_diary_entry", entry_id, data=data, expect=expect)

    def delete_log(self, entry_id, *, expect=200):
        return self.post("delete_diary_entry", entry_id, expect=expect)


class WebhookDriver(Driver):
    """Plex, Jellyfin and Emby events posted to their webhook URLs (TV-802).

    The payloads carry an IMDb ID that the patched TMDB find maps to the episode, as
    TMDB's /find does; webhook requests carry no timezone header, so the account's
    stored calendar decides the day.
    """

    path = "webhook"

    def __init__(self, case, user, *, vendor="plex"):
        """Turn on the account's Plex and Jellyfin webhook settings."""
        super().__init__(case, user)
        self.vendor = vendor
        type(user).objects.filter(pk=user.pk).update(
            plex_usernames=user.username,
            jellyfin_mark_played_enabled=True,
            jellyfin_mark_unplayed_enabled=True,
        )
        user.refresh_from_db()
        self.client = Client()

    @staticmethod
    def imdb_id(show, season, episode):
        return f"tt{show.media_id}s{season:02d}e{episode:02d}"

    def event(self, show, season, episode, event, *, vendor=None):
        """Post one event: "watched", "unplayed" or "play"."""
        vendor = vendor or self.vendor
        imdb_id = self.imdb_id(show, season, episode)
        self.case.webhook_ids[imdb_id] = (show.media_id, season, episode)
        token = self.user.token
        if vendor == "plex":
            if event == "unplayed":
                self.unsupported("Plex unplayed event")
            payload = {
                "event": "media.scrobble" if event == "watched" else "media.play",
                "Account": {"title": self.user.username},
                "Metadata": {
                    "type": "episode",
                    "grandparentTitle": show.title,
                    "parentIndex": season,
                    "index": episode,
                    "Guid": [{"id": f"imdb://{imdb_id}"}],
                },
            }
            url = reverse("plex_webhook", kwargs={"token": token})
            request = {"data": {"payload": json.dumps(payload)}}
        else:
            item = {
                "Type": "Episode",
                "SeriesName": show.title,
                "ParentIndexNumber": season,
                "IndexNumber": episode,
                "ProviderIds": {"Imdb": imdb_id},
                "UserData": {"Played": event == "watched"},
            }
            if vendor == "jellyfin":
                name = {"watched": "MarkPlayed", "unplayed": "MarkUnplayed", "play": "Play"}[event]
                url = reverse("jellyfin_webhook", kwargs={"token": token})
                request = {"data": json.dumps({"Event": name, "Item": item}), "content_type": "application/json"}
            else:
                if event == "unplayed":
                    self.unsupported("Emby unplayed event")
                payload = {
                    "Event": "playback.stop" if event == "watched" else "playback.start",
                    "Item": item,
                    "PlaybackInfo": {"PlayedToCompletion": event == "watched"},
                }
                url = reverse("emby_webhook", kwargs={"token": token})
                request = {"data": {"data": json.dumps(payload)}}
        self.step()
        with self.committing():
            response = self.client.post(url, **request)
        return _answer(response, 200)

    def tick(self, show, season, episode, *, vendor=None):
        return self.event(show, season, episode, "watched", vendor=vendor)

    def untick(self, show, season, episode, *, vendor="jellyfin"):
        return self.event(show, season, episode, "unplayed", vendor=vendor)

    def play(self, show, season, episode, *, vendor=None):
        return self.event(show, season, episode, "play", vendor=vendor)


class HookDriver(Driver):
    """Plain model operations, as the admin or the shell make them (app.tv_signals).

    The receivers work the show out from the stored episode lists when the transaction
    commits, so the driver first stores the listing, as opening the show's page does.
    """

    path = "hooks"

    def _items(self, show, season, episode=None):
        season_item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=SEASON_TYPE,
            season_number=season,
            defaults={"title": show.title, "image": show.image},
        )
        if episode is None:
            return season_item, None
        episode_item, _ = Item.objects.get_or_create(
            media_id=show.media_id,
            source=show.source,
            media_type=EPISODE_TYPE,
            season_number=season,
            episode_number=episode,
            defaults={"title": f"Episode {episode}", "image": show.image},
        )
        return season_item, episode_item

    def season_row(self, show, season):
        season_item, _ = self._items(show, season)
        row = Season.objects.filter(user=self.user, item=season_item).first()
        if row is None:
            row = Season.objects.create(item=season_item, user=self.user, status=WATCHING)
        return row

    def tick(self, show, season, episode):
        tv_listing.get_listing(show)
        self.step()
        with self.committing():
            row = self.season_row(show, season)
            _, episode_item = self._items(show, season, episode)
            return Episode.objects.create(item=episode_item, related_season=row, end_date=user_calendar.now())

    def untick(self, show, season, episode):
        self.step()
        viewing = (
            Episode.objects.filter(
                related_season__user=self.user,
                related_season__item__media_id=show.media_id,
                item__season_number=season,
                item__episode_number=episode,
            )
            .order_by("-created_at", "-id")
            .first()
        )
        with self.committing():
            viewing.delete()

    def write_status(self, show, status, season=None):
        """Save a status on the TV or Season row, creating it as the admin would."""
        tv_listing.get_listing(show)
        if season is None and self.tv(show) is None:
            # A row created with a status records no choice (plan 1.2, Hooks:
            # Season.get_tv creates TV rows with copied statuses), so the status is
            # written to an existing row, as an edit in the admin is.
            self.step()
            with self.committing():
                TV.objects.create(user=self.user, item=show, status=WATCHING)
        self.step()
        with self.committing():
            row = self.tv(show) if season is None else self.season_row(show, season)
            row.status = status
            row.save()
            return row

    def mark(self, show, season=None):
        return self.write_status(show, COMPLETED, season)

    def choose(self, show, action):
        status = {"watching": WATCHING, "resume": WATCHING, "planning": PLANNING, "pause": PAUSED, "drop": DROPPED}
        return self.write_status(show, status[action])


class ImportDriver(Driver):
    """A Yamtrack CSV through integrations.imports.yamtrack.importer (TV-800)."""

    path = "import"

    @staticmethod
    def row(show, media_type, *, season="", episode="", status="", score="", end_date=""):
        if isinstance(end_date, datetime):
            end_date = end_date.astimezone(UTC).isoformat()
        return {
            "media_id": show.media_id,
            "source": show.source,
            "media_type": media_type,
            "title": show.title,
            "image": show.image,
            "season_number": season,
            "episode_number": episode,
            "score": score,
            "progress": "",
            "status": status,
            "start_date": "",
            "end_date": end_date,
            "notes": "",
            "progressed_at": "2024-02-09T12:00:00Z",
        }

    def plays(self, show, plays, *, status=WATCHING, season_status=WATCHING):
        """Return rows for a show and its plays: [(season, episode, moment), ...]."""
        rows = [self.row(show, TV_TYPE, status=status)]
        rows.extend(
            self.row(show, SEASON_TYPE, season=number, status=season_status)
            for number in sorted({season for season, _, _ in plays})
        )
        rows.extend(
            self.row(show, EPISODE_TYPE, season=season, episode=episode, end_date=moment or "")
            for season, episode, moment in plays
        )
        return rows

    def import_rows(self, rows, *, mode="new", header=IMPORT_HEADER):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=header, quoting=csv.QUOTE_ALL, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return self.import_csv(buffer.getvalue(), mode=mode)

    def import_csv(self, text, *, mode="new"):
        self.step()
        with self.committing():
            counts, warnings = yamtrack.importer(io.BytesIO(text.encode()), self.user, mode)
        assert warnings == "", warnings  # noqa: S101
        return counts

    def import_export_of(self, other, *, mode="new"):
        """Export another account's data as the web's CSV export does, and import it."""
        return self.import_csv(export_csv(other), mode=mode)


def export_csv(user):
    """Return the user's CSV export (integrations.exports.generate_rows)."""
    return "".join(exports.generate_rows(user))


# ---------------------------------------------------------------------------------
# Test case
# ---------------------------------------------------------------------------------


@override_settings(TIME_ZONE=NEW_YORK)
class TVAcceptanceTestCase(TestCase):
    """A fake TMDB, a clock at noon Pacific, a New York server, Los Angeles accounts."""

    start = NOON_PACIFIC
    maxDiff = None

    def setUp(self):
        super().setUp()
        cache.clear()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(self.start, tz=PACIFIC).__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.fetch_releases = self.patch("app.models.Item.fetch_releases")
        self.patch("app.tv_tracking._queue_external_ratings")
        self.patch("app.tasks.update_daily_statistics.delay")
        for target, value in PROVIDER_PATCHES:
            self.patch(target, return_value=value)
        self.webhook_ids = {}
        patcher = mock.patch.object(tmdb, "find", side_effect=self._tmdb_find)
        patcher.start()
        self.addCleanup(patcher.stop)

    def __getstate__(self):
        """Pickle the test without its mocks and fakes.

        The parallel runner pickles each test to send it to a worker, and a failed
        subtest with its test case to report it; once setUp has run, the case holds
        mocks and the fake provider, which can't be pickled and aren't needed to name
        the test.
        """
        state = {}
        for name, value in self.__dict__.items():
            try:
                pickle.dumps(value)
            except Exception:  # noqa: BLE001, S112 - anything unpicklable stays behind
                continue
            state[name] = value
        state.setdefault("_cleanups", [])
        return state

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        started = patcher.start()
        self.addCleanup(patcher.stop)
        return started

    def _tmdb_find(self, external_id, _external_source):
        if external_id in self.webhook_ids:
            media_id, season, episode = self.webhook_ids[external_id]
            return {"tv_episode_results": [{"show_id": int(media_id), "season_number": season, "episode_number": episode}]}
        return {"movie_results": [], "tv_episode_results": []}

    # Builders

    def make_show(self, seasons=None, **kwargs):
        return make_show(self.fake, seasons=seasons if seasons is not None else {1: 10, 2: 8}, **kwargs)

    def new_user(self, calendar=PACIFIC):
        return make_user(calendar=calendar)

    def driver(self, path, user=None, **kwargs):
        """Return a driver for a path name ("web#form": the web, variant "form")."""
        name = path
        path, _, variant = path.partition("#")
        made = self._driver(path, user or self.new_user(), **kwargs)
        made.variant = variant
        made.name = name
        return made

    def _driver(self, path, user, **kwargs):
        if path == "api":
            return APIDriver(self, user, **kwargs)
        if path == "installed":
            return APIDriver(self, user, installed=True, **kwargs)
        if path == "web":
            return WebDriver(self, user)
        if path.startswith("webhook"):
            vendor = path.partition(":")[2] or kwargs.get("vendor", "plex")
            return WebhookDriver(self, user, vendor=vendor)
        if path == "hooks":
            return HookDriver(self, user)
        if path == "import":
            return ImportDriver(self, user)
        msg = f"Unknown path {path!r}"
        raise ValueError(msg)

    def api(self, user):
        """Return a new-build API driver for any account, to read its surfaces."""
        return APIDriver(self, user)

    def run_paths(self, show, steps, paths, *, start=None):
        """Run steps(driver) once per path, each for a fresh account.

        Returns {path: driver}. The clock restarts at the same moment for each path, so
        every account's days match; recorded order within each account stays real.
        """
        start_moment = start or self.clock.moment
        drivers = {}
        for path in paths:
            self.clock.moment = start_moment
            driver = self.driver(path)
            with self.subTest(path=path):
                steps(driver)
            drivers[path] = driver
        return drivers

    # Assertions

    def snap(self, driver_or_user, show):
        user = getattr(driver_or_user, "user", driver_or_user)
        return snapshot(user, show)

    def assert_same_state(self, show, drivers, *, keys=ALL_KEYS, reference="api"):
        """Check every path's snapshot equals the reference path's (TV-207)."""
        drivers = dict(drivers)
        base_driver = drivers.pop(reference)
        base = pick(self.snap(base_driver, show), keys)
        for path, driver in drivers.items():
            path_keys = keys
            if path == "import":
                path_keys = tuple(key for key in keys if key in IMPORT_KEYS)
            elif path == "hooks":
                path_keys = tuple(key for key in keys if key in HOOK_KEYS)
            with self.subTest(path=path):
                self.assertEqual(
                    pick(self.snap(driver, show), path_keys),
                    pick(base, path_keys),
                    f"{path} differs from {reference}",
                )
        return base

    def assert_surfaces_agree(self, user, show):
        """Check that every surface agrees with the stored state (TV-705).

        Library, Continue Watching, the show, season and episode pages and the Diary
        agree after a write, whatever path made it.
        """
        snap = snapshot(user, show)
        reader = self.api(user)
        row = reader.library_row(show)
        page = reader.detail(show).data
        state = page["user_state"]["tv"]
        self.assertEqual(state["in_library"], snap["in_library"])
        self.assertEqual(row is not None, snap["in_library"])
        if not snap["in_library"]:
            self.assertIsNone(page["user_state"]["status"])
            self.assertEqual(reader.diary(show), [])
            return snap
        tv = TV.objects.get(user=user, item=show)
        legacy = tv.legacy_pending
        self.assertEqual(row["tracking"]["status"], snap["status"])
        self.assertEqual(page["user_state"]["status"], snap["status"])
        self.assertEqual(state["status"], snap["status"])
        if not legacy:
            watched, aired = snap["counts"]
            self.assertEqual(
                (row["tracking"]["progress"]["value"], row["tracking"]["progress"]["max"]),
                (watched, aired),
            )
            listed = row["tracking"]["tv"]
            for source in (listed, state):
                self.assertEqual((source["watched_count"], source["aired_count"]), snap["counts"])
                self.assertEqual(source["finished"], snap["finished"])
                self.assertEqual(source["caught_up"], snap["caught_up"])
                up_next = source["up_next"]
                self.assertEqual(
                    (up_next["season_number"], up_next["episode_number"]) if up_next else (None, None),
                    snap["up_next"][:2],
                )
                self.assertEqual(
                    source["last_watched_on"],
                    snap["last_watched_on"].isoformat() if snap["last_watched_on"] else None,
                )
            self.assertEqual(
                (page["completion"]["completed_count"], page["completion"]["total_count"]),
                (watched, aired),
            )
            in_continue = show.media_id in reader.continue_watching()
            up_next_aired = snap["up_next"][0] is not None
            self.assertEqual(
                in_continue,
                snap["status"] == WATCHING and up_next_aired and not snap["caught_up"],
            )
            cards = {card["season_number"]: card["tv"] for card in page["seasons"]}
            cards.update({card["season_number"]: card["tv"] for card in page["tv_unlisted_seasons"]})
            for number, season in snap["seasons"].items():
                card = cards[number]
                self.assertTrue(card["started"], number)
                self.assertEqual(card["status"], season["status"], number)
                self.assertEqual(card["watched_count"], season["watched"], number)
                if card["listed"]:
                    episodes = reader.detail(show, number).data["episodes"]
                    self.assertEqual(
                        sorted((number, episode["tv"]["episode_number"]) for episode in episodes if episode["tv"]["watched"]),
                        [position for position in snap["watched_pass"] if position[0] == number],
                    )
        diary = reader.diary(show)
        self.assertEqual(len(diary), len(snap["logs"]))
        for entry in diary:
            # The list leaves tv_log out (TV-207); the log's own screen reads it.
            self.assertIsNone(entry["tv_log"])
            self.assertIsNotNone(reader.entry(entry["id"])["tv_log"])
        return snap

    def assert_surfaces_agree_for(self, show, drivers):
        for path, driver in dict(drivers).items():
            with self.subTest(surfaces=path):
                self.assert_surfaces_agree(driver.user, show)


def at_pacific(day, hour=12, minute=0):
    """Return an aware moment on the Pacific calendar."""
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(PACIFIC))


def weekly(first, count, *, every=timedelta(days=7)):
    """Return count air dates a week apart from first."""
    return [first + every * index for index in range(count)]
