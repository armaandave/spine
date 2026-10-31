"""Stats v2: request, scope, assembly and show-all endpoints (docs/stats-contract.md).

STAT-001 to STAT-006, STAT-100 to STAT-106, STAT-218, STAT-300 to STAT-303, STAT-310,
STAT-311 and STAT-900. Section contents are tested with their sections; these tests
pin what every section relies on: the keys, where null goes, who sees what, which
days count, and the legacy payload staying put.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from api.services.stats_scope import (
    StatsScope,
    build_scope,
    day_string,
    month_string,
    one_decimal,
    rating_bound,
    stars,
)
from app.models import (
    TV,
    Book,
    DiaryEntry,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from social.models import Block, Follow, FollowStatus

NEW_YORK = "America/New_York"
LOS_ANGELES = "America/Los_Angeles"
AUCKLAND = "Pacific/Auckland"
OWNER_ZONES = (NEW_YORK, LOS_ANGELES, AUCKLAND)
ALL_TIME = {"schema": "2", "start_date": "all", "end_date": "all"}
YEAR_2025 = {"schema": "2", "start_date": "2025-01-01", "end_date": "2025-12-31"}
YEAR_2024 = {"schema": "2", "start_date": "2024-01-01", "end_date": "2024-12-31"}
# A calendar day stored as a UTC-midnight carrier.
MAR_2025 = datetime(2025, 3, 2, tzinfo=UTC)
NOW = datetime(2026, 10, 26, 16, 0, tzinfo=UTC)

TOP_LEVEL_KEYS = {
    "schema_version",
    "range",
    "media_type",
    "audience",
    "years",
    "media_types",
    "header",
    "time",
    "top",
    "highest_rated",
    "activity",
    "ratings",
    "rated_vs_everyone",
    "release_years",
    "genres",
    "languages",
    "people",
    "list_rings",
    "collections",
    "most_logged",
    "milestones",
}
DICT_SECTIONS = (
    "header",
    "time",
    "top",
    "highest_rated",
    "activity",
    "ratings",
    "rated_vs_everyone",
    "release_years",
    "genres",
    "most_logged",
)
LIST_SECTIONS = ("list_rings", "milestones")
STAR_KEYS = {"stars", "average_stars", "everyone_stars", "difference", "rating_min", "rating_max"}
PRIMARY = ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"]
COLLECTION_TABS = {"movie", "game", "book", "anime"}
LANGUAGE_TABS = {None, "movie", "tv"}


def clear_library():
    """Delete every viewing, log, tracking row and Item, viewings first (RESTRICT)."""
    Episode.objects.all().delete()
    DiaryEntry.objects.all().delete()
    Season.objects.all().delete()
    TV.objects.all().delete()
    Item.objects.all().delete()


def _star_values(value, path=""):
    """Yield (path, value) for every star-like field anywhere in the payload."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in STAR_KEYS:
                yield f"{path}.{key}", child
            yield from _star_values(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _star_values(child, f"{path}[{index}]")


@override_settings(TIME_ZONE=NEW_YORK, MUSIC_ENABLED=True)
class StatsV2Base(TestCase):
    """An owner, a follower, a stranger and items of every type, at a fixed now."""

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        users = get_user_model().objects
        self.owner = users.create_user(username="owner", password="strong-password-123")
        self.follower = users.create_user(username="follower", password="strong-password-123")
        self.stranger = users.create_user(username="stranger", password="strong-password-123")
        Follow.objects.create(from_user=self.follower, to_user=self.owner, status=FollowStatus.ACCEPTED)
        self.items = {}

    def item(self, media_type, media_id, *, source=None, season=None, episode=None, title=None):
        source = source or {
            MediaTypes.ANIME.value: Sources.MAL.value,
            MediaTypes.MANGA.value: Sources.MAL.value,
            MediaTypes.GAME.value: Sources.IGDB.value,
            MediaTypes.BOOK.value: Sources.OPENLIBRARY.value,
            MediaTypes.COMIC.value: Sources.COMICVINE.value,
            MediaTypes.MUSIC.value: Sources.MUSICBRAINZ.value,
        }.get(media_type, Sources.TMDB.value)
        return Item.objects.create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season,
            episode_number=episode,
            title=title or f"{media_type} {media_id}",
            image="https://example.com/poster.jpg",
        )

    def log(self, item, consumed_at=MAR_2025, *, user=None, rating=None, visibility="public"):
        return DiaryEntry.objects.create(
            user=user or self.owner,
            item=item,
            consumed_at=consumed_at,
            rating=rating,
            visibility=visibility,
        )

    def show_rows(self, show_item, season_item, user=None):
        user = user or self.owner
        tv = TV(user=user, item=show_item, status=Status.IN_PROGRESS.value)
        TV.objects.bulk_create([tv])
        season = Season(user=user, item=season_item, related_tv=tv, status=Status.IN_PROGRESS.value)
        Season.objects.bulk_create([season])
        return tv, season

    def viewing(self, season, episode_item, *, watched_on=None, end_date=None, kind="tick", entry=None):
        viewing = Episode(
            related_season=season,
            item=episode_item,
            watched_on=watched_on,
            end_date=end_date,
            kind=kind,
            diary_entry=entry,
        )
        Episode.objects.bulk_create([viewing])
        return viewing

    def client_for(self, user, zone=None):
        client = APIClient()
        client.force_authenticate(user)
        if zone:
            client.credentials(HTTP_X_SPINE_TIMEZONE=zone)
        return client

    def get(self, user, params, *, path="/api/v1/stats/me/summary/", zone=None):
        return self.client_for(user, zone).get(path, params)

    def user_path(self, endpoint="summary", username="owner"):
        return f"/api/v1/users/{username}/stats/{endpoint}/"

    def type_row(self, payload, media_type):
        return next(row for row in payload["media_types"] if row["media_type"] == media_type)

    def seed_every_type(self):
        for index, media_type in enumerate(PRIMARY):
            if media_type == MediaTypes.TV.value:
                continue
            item = self.item(media_type, f"{media_type}-{index}")
            self.log(item, rating=Decimal("7.4"))
            self.log(item, datetime(2025, 6, 1, tzinfo=UTC), rating=Decimal("9.0"))
        show = self.item(MediaTypes.TV.value, "show")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        _tv, season_row = self.show_rows(show, season)
        entry = self.log(episode, rating=Decimal("8.0"))
        self.viewing(season_row, episode, watched_on=date(2025, 3, 2), kind="log", entry=entry)
        self.log(season, rating=Decimal("9.0"))


class StatsV2ShapeTests(StatsV2Base):
    """Every tab has every key; null only where the tab lacks the section (STAT-302)."""

    def test_every_tab_sends_every_key_with_nulls_only_where_the_tab_has_none(self):
        self.seed_every_type()
        for params in (ALL_TIME, YEAR_2025):
            for tab in [None, *PRIMARY]:
                with self.subTest(range=params["start_date"], tab=tab):
                    query = {**params, **({"media_type": tab} if tab else {})}
                    response = self.get(self.owner, query)
                    self.assertEqual(response.status_code, 200, response.data)
                    self.assert_v2_shape(response.data, tab)

    def test_other_viewers_get_the_same_sections(self):
        # STAT-002: no section is null because of who is asking; STAT-004: owner-only
        # Top fields are null and false.
        self.seed_every_type()
        for viewer, audience in ((self.follower, "follower"), (self.stranger, "public")):
            for tab in (None, "movie", "tv", "book"):
                with self.subTest(audience=audience, tab=tab):
                    query = {**YEAR_2025, **({"media_type": tab} if tab else {})}
                    data = self.get(viewer, query, path=self.user_path()).data
                    self.assert_v2_shape(data, tab)
                    self.assertEqual(data["audience"], audience)
                    self.assertIsNone(data["top"]["tag_hint"])
                    self.assertFalse(data["top"]["hint_private"])
                    self.assertIsInstance(data["top"]["hint_private"], bool)

    def test_the_owner_gets_the_tag_hint_for_a_year_or_all_time(self):
        movie_tab = self.get(self.owner, {**YEAR_2024, "media_type": "anime"}).data
        self.assertEqual(movie_tab["top"]["tag"], "top2024")
        self.assertEqual(movie_tab["top"]["tag_hint"], "Tag a ranked anime list top2024")
        global_tab = self.get(self.owner, ALL_TIME).data
        self.assertEqual(global_tab["top"]["tag"], "topalltime")
        self.assertEqual(global_tab["top"]["tag_hint"], "Tag a ranked list with mixed media topalltime")
        other_range = self.get(self.owner, {"schema": "2", "start_date": "2025-02-01", "end_date": "2025-12-31"}).data
        self.assertIsNone(other_range["top"]["tag"])
        self.assertIsNone(other_range["top"]["tag_hint"])

    def test_an_empty_account_gets_empty_sections(self):
        data = self.get(self.owner, YEAR_2025).data
        self.assert_v2_shape(data, None)
        self.assertEqual((data["header"]["titles"], data["header"]["logs"]), (0, 0))
        self.assertEqual(data["time"]["rows"], [])
        for key in ("top", "highest_rated", "most_logged"):
            self.assertEqual(data[key]["items"], [], key)
        self.assertIsNone(data["activity"]["busiest_day"])
        self.assertEqual(data["ratings"]["rated"], 0)
        self.assertIsNone(data["ratings"]["average_stars"])
        self.assertEqual((data["rated_vs_everyone"]["higher"], data["rated_vs_everyone"]["lower"]), ([], []))
        self.assertEqual(data["release_years"]["rows"], [])
        self.assertEqual(data["languages"]["rows"], [])
        self.assertEqual((data["genres"]["rows"], data["genres"]["music"]), ([], []))
        self.assertEqual((data["list_rings"], data["milestones"]), ([], []))
        self.assertIsNone(data["years"]["first"])
        self.assertEqual(data["years"]["current"], 2026)
        self.assertEqual(
            data["media_types"],
            [{"media_type": media_type, "titles": 0, "logs": 0} for media_type in PRIMARY],
        )

    def test_hours_total_names_types_by_exposure_only(self):
        data = self.get(self.stranger, YEAR_2025, path=self.user_path()).data
        self.assertEqual(data["time"]["hours_total"]["media_types"], ["movie", "tv", "anime", "game", "music"])
        self.assertEqual(data["time"]["hours_total"]["leaves_out"], ["manga", "book", "comic"])

    def assert_v2_shape(self, data, tab):
        self.assertEqual(set(data), TOP_LEVEL_KEYS)
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["media_type"], tab)
        self.assertEqual(set(data["range"]), {"start_date", "end_date", "timezone", "is_all_time"})
        self.assertEqual(set(data["years"]), {"first", "current"})
        self.assertEqual([row["media_type"] for row in data["media_types"]], PRIMARY)
        for row in data["media_types"]:
            self.assertEqual(set(row), {"media_type", "titles", "logs"})
        for key in DICT_SECTIONS:
            self.assertIsInstance(data[key], dict, key)
        for key in LIST_SECTIONS:
            self.assertIsInstance(data[key], list, key)
        self.assert_collections_shape(data, tab)
        if tab in LANGUAGE_TABS:
            self.assertEqual(data["languages"]["media_types"], ["movie", "tv"] if tab is None else [tab])
        else:
            self.assertIsNone(data["languages"])
        if tab is None:
            self.assertIsInstance(data["time"]["hours_total"], dict)
        else:
            self.assertIsNone(data["time"]["hours_total"])
            self.assertTrue(all(row["media_type"] == tab for row in data["time"]["rows"]))
        self.assertEqual(len(data["ratings"]["distribution"]), 10)
        self.assertEqual(
            [bucket["stars"] for bucket in data["ratings"]["distribution"]],
            ["0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0"],
        )
        self.assertEqual(len(data["activity"]["weekdays"]), 7)
        if data["range"]["start_date"] == "2025-01-01" and data["range"]["end_date"] == "2025-12-31":
            self.assertEqual(
                [month["month"] for month in data["activity"]["months"]],
                [f"2025-{month:02d}" for month in range(1, 13)],
            )
        for path, value in _star_values(data):
            self.assertTrue(value is None or isinstance(value, str), f"{path} is {value!r}")

    def assert_collections_shape(self, data, tab):
        """Check collections: null off its tabs, no in_range in All Time (STAT-214)."""
        if tab not in COLLECTION_TABS:
            self.assertIsNone(data["collections"])
            return
        self.assertEqual(
            set(data["collections"]),
            {"complete_count", "almost_count", "complete", "almost", "in_range"},
        )
        if data["range"]["is_all_time"]:
            self.assertIsNone(data["collections"]["in_range"])
        else:
            self.assertEqual(
                set(data["collections"]["in_range"]),
                {"completed_count", "completed", "progressed_count", "progressed"},
            )


class StatsV2CountingTests(StatsV2Base):
    """media_types[], years and the scope's titles and events (STAT-102 to STAT-106)."""

    def test_a_type_the_owner_turned_off_leaves_the_picker_but_still_counts_on_global(self):
        self.log(self.item(MediaTypes.MOVIE.value, "movie"))
        self.log(self.item(MediaTypes.ANIME.value, "anime"))
        self.owner.anime_enabled = False
        self.owner.save(update_fields=["anime_enabled"])
        # The viewer's own switches never matter: the stranger keeps anime on.
        for viewer, path in ((self.owner, "/api/v1/stats/me/summary/"), (self.stranger, self.user_path())):
            with self.subTest(viewer=viewer.username):
                data = self.get(viewer, YEAR_2025, path=path).data

                types = [row["media_type"] for row in data["media_types"]]
                self.assertNotIn("anime", types)
                self.assertEqual(types, [media_type for media_type in PRIMARY if media_type != "anime"])
                self.assertEqual((data["header"]["titles"], data["header"]["logs"]), (2, 2))

    def test_a_show_with_show_season_and_episode_logs_is_one_title(self):
        # S-03
        show = self.item(MediaTypes.TV.value, "show")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        for item in (show, season, episode):
            self.log(item)

        data = self.get(self.owner, YEAR_2025).data

        self.assertEqual(self.type_row(data, "tv"), {"media_type": "tv", "titles": 1, "logs": 3})

    def test_a_season_without_its_show_item_is_its_own_title(self):
        season = self.item(MediaTypes.SEASON.value, "orphan", season=2)
        episode = self.item(MediaTypes.EPISODE.value, "orphan", season=2, episode=1)
        self.log(season)
        self.log(episode)
        scope = build_scope(self.owner, self.owner, {"start_date": "all", "end_date": "all"})

        self.assertEqual(
            set(scope.titles.values_list("id", flat=True)),
            {season.id, episode.id},
        )

    def test_dated_viewings_add_their_show_and_fills_count_only_in_all_time(self):
        show = self.item(MediaTypes.TV.value, "ticked")
        filled_show = self.item(MediaTypes.TV.value, "filled")
        _tv, season_row = self.show_rows(show, self.item(MediaTypes.SEASON.value, "ticked", season=1))
        _filled_tv, filled_season = self.show_rows(
            filled_show,
            self.item(MediaTypes.SEASON.value, "filled", season=1),
        )
        self.viewing(
            season_row,
            self.item(MediaTypes.EPISODE.value, "ticked", season=1, episode=1),
            end_date=datetime(2025, 5, 1, 12, tzinfo=UTC),
        )
        self.viewing(
            filled_season,
            self.item(MediaTypes.EPISODE.value, "filled", season=1, episode=1),
            kind="fill",
            end_date=datetime(2025, 5, 1, 12, tzinfo=UTC),
        )

        year = build_scope(self.owner, self.owner, {"start_date": "2025-01-01", "end_date": "2025-12-31"})
        all_time = build_scope(self.owner, self.owner, {"start_date": "all", "end_date": "all"})

        self.assertEqual(list(year.titles.values_list("id", flat=True)), [show.id])
        self.assertEqual(year.viewings.count(), 1)
        self.assertEqual(all_time.viewings.count(), 2)
        self.assertEqual(all_time.dated_viewings.count(), 1)
        # A fill is never an event and never a title on its own (STAT-105, STAT-106).
        self.assertEqual(list(all_time.titles.values_list("id", flat=True)), [show.id])

    def test_undated_completions_count_only_in_all_time(self):
        # S-05: a film marked watched with no log; a book marked read with a log.
        film = self.item(MediaTypes.MOVIE.value, "marked")
        book = self.item(MediaTypes.BOOK.value, "read")
        Movie.objects.bulk_create([
            Movie(user=self.owner, item=film, status=Status.COMPLETED.value, direct_consumption=True),
        ])
        Book.objects.bulk_create([
            Book(user=self.owner, item=book, status=Status.COMPLETED.value, completed_manually=True),
        ])
        self.log(book)

        all_time = self.get(self.owner, ALL_TIME).data
        year = self.get(self.owner, YEAR_2025).data

        self.assertEqual(self.type_row(all_time, "movie"), {"media_type": "movie", "titles": 1, "logs": 0})
        self.assertEqual(self.type_row(all_time, "book"), {"media_type": "book", "titles": 1, "logs": 1})
        self.assertEqual(self.type_row(year, "movie"), {"media_type": "movie", "titles": 0, "logs": 0})
        self.assertEqual(self.type_row(year, "book"), {"media_type": "book", "titles": 1, "logs": 1})

    def test_a_completed_show_with_any_log_is_not_an_undated_completion(self):
        show = self.item(MediaTypes.TV.value, "done")
        season = self.item(MediaTypes.SEASON.value, "done", season=1)
        tv, _season_row = self.show_rows(show, season)
        TV.objects.filter(pk=tv.pk).update(status=Status.COMPLETED.value)
        self.log(season, visibility="private")

        public = build_scope(self.owner, self.stranger, {"start_date": "all", "end_date": "all"})
        owner = build_scope(self.owner, self.owner, {"start_date": "all", "end_date": "all"})

        # The hidden log keeps the show from counting through its status, so no
        # number reveals it (STAT-003).
        self.assertEqual(public.titles.count(), 0)
        self.assertEqual(list(owner.titles.values_list("id", flat=True)), [show.id])

    def test_hidden_logs_and_their_viewings_count_for_nothing(self):
        # S-18: a followers-only and a private TV log.
        show = self.item(MediaTypes.TV.value, "show")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        first = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        second = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=2)
        other_show = self.item(MediaTypes.TV.value, "secret")
        other_season = self.item(MediaTypes.SEASON.value, "secret", season=1)
        secret_episode = self.item(MediaTypes.EPISODE.value, "secret", season=1, episode=1)
        _tv, season_row = self.show_rows(show, season)
        _other_tv, other_season_row = self.show_rows(other_show, other_season)
        followers_log = self.log(first, visibility="followers")
        private_log = self.log(second, visibility="private")
        secret_log = self.log(secret_episode, visibility="private")
        self.viewing(season_row, first, watched_on=date(2025, 3, 2), kind="log", entry=followers_log)
        self.viewing(season_row, second, watched_on=date(2025, 3, 2), kind="log", entry=private_log)
        self.viewing(other_season_row, secret_episode, watched_on=date(2025, 3, 2), kind="log", entry=secret_log)

        owner = self.type_row(self.get(self.owner, YEAR_2025).data, "tv")
        follower = self.type_row(self.get(self.follower, YEAR_2025, path=self.user_path()).data, "tv")
        stranger = self.type_row(self.get(self.stranger, YEAR_2025, path=self.user_path()).data, "tv")

        self.assertEqual(owner, {"media_type": "tv", "titles": 2, "logs": 3})
        self.assertEqual(follower, {"media_type": "tv", "titles": 1, "logs": 1})
        self.assertEqual(stranger, {"media_type": "tv", "titles": 0, "logs": 0})
        follower_scope = build_scope(self.owner, self.follower, {"start_date": "all", "end_date": "all"})
        self.assertEqual(follower_scope.viewings.count(), 1)

    def test_three_episodes_and_a_season_log_on_one_day_are_one_event(self):
        # S-04
        show = self.item(MediaTypes.TV.value, "show")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        _tv, season_row = self.show_rows(show, season)
        for number in (1, 2, 3):
            episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=number)
            self.viewing(season_row, episode, watched_on=date(2025, 3, 2))
        season_log = self.log(season)
        self.viewing(
            season_row,
            self.item(MediaTypes.EPISODE.value, "show", season=1, episode=4),
            kind="fill",
            entry=season_log,
        )
        movie = self.item(MediaTypes.MOVIE.value, "film")
        self.log(movie)

        scope = build_scope(self.owner, self.owner, {"start_date": "2025-01-01", "end_date": "2025-12-31"})
        tv_scope = scope.with_media_type("tv")
        movie_scope = scope.with_media_type("movie")

        self.assertEqual(
            scope.events,
            sorted([(date(2025, 3, 2), show.id, "tv"), (date(2025, 3, 2), movie.id, "movie")]),
        )
        self.assertEqual(tv_scope.events, [(date(2025, 3, 2), show.id, "tv")])
        self.assertEqual(movie_scope.events, [(date(2025, 3, 2), movie.id, "movie")])
        # The season log's fill counts on the log's day (STAT-104).
        self.assertEqual(scope.viewings.count(), 4)
        self.assertEqual(scope.all_time().viewings.count(), 4)

    def test_days_follow_the_owners_calendar_for_every_viewer(self):
        # A viewing ticked at 23:30 on 31 Dec and an anime log at the same moment, in
        # the owner's zone, stay in that year whatever zone the viewer's app sends.
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                clear_library()
                self.owner.calendar_timezone = zone
                self.owner.save(update_fields=["calendar_timezone"])
                late = datetime(2025, 12, 31, 23, 30, tzinfo=ZoneInfo(zone))
                show = self.item(MediaTypes.TV.value, "show")
                _tv, season_row = self.show_rows(show, self.item(MediaTypes.SEASON.value, "show", season=1))
                self.viewing(
                    season_row,
                    self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1),
                    end_date=late,
                )
                self.log(self.item(MediaTypes.ANIME.value, "late"), late)
                self.log(self.item(MediaTypes.MOVIE.value, "carrier"), datetime(2026, 1, 1, tzinfo=UTC))

                for viewer, path, header in (
                    (self.owner, "/api/v1/stats/me/summary/", zone),
                    (self.stranger, self.user_path(), "Asia/Tokyo"),
                ):
                    in_2025 = self.get(viewer, YEAR_2025, path=path, zone=header).data
                    in_2026 = self.get(
                        viewer,
                        {"schema": "2", "start_date": "2026-01-01", "end_date": "2026-12-31"},
                        path=path,
                        zone=header,
                    ).data
                    self.assertEqual(in_2025["range"]["timezone"], zone)
                    self.assertEqual(self.type_row(in_2025, "tv")["titles"], 1)
                    self.assertEqual(self.type_row(in_2025, "anime")["logs"], 1)
                    self.assertEqual(self.type_row(in_2025, "movie")["logs"], 0)
                    self.assertEqual(self.type_row(in_2026, "tv")["titles"], 0)
                    self.assertEqual(self.type_row(in_2026, "anime")["logs"], 0)
                    self.assertEqual(self.type_row(in_2026, "movie")["logs"], 1)
                    self.assertEqual(in_2025["years"]["first"], 2025)

    def test_years_first_counts_only_what_the_viewer_can_see(self):
        self.log(self.item(MediaTypes.MOVIE.value, "film"), datetime(2020, 5, 1, tzinfo=UTC))
        self.log(self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1), datetime(2014, 3, 2, tzinfo=UTC), visibility="private")
        show = self.item(MediaTypes.TV.value, "ticked")
        _tv, season_row = self.show_rows(show, self.item(MediaTypes.SEASON.value, "ticked", season=1))
        self.viewing(
            season_row,
            self.item(MediaTypes.EPISODE.value, "ticked", season=1, episode=1),
            watched_on=date(2017, 8, 1),
        )

        self.assertEqual(self.get(self.owner, YEAR_2025).data["years"]["first"], 2014)
        self.assertEqual(self.get(self.stranger, YEAR_2025, path=self.user_path()).data["years"]["first"], 2017)

    def test_the_default_range_is_the_last_year_on_the_owners_calendar(self):
        # 16:00 UTC on 26 Oct 2026 is already 27 Oct in Auckland.
        self.owner.calendar_timezone = AUCKLAND
        self.owner.save(update_fields=["calendar_timezone"])

        data = self.get(self.stranger, {"schema": "2"}, path=self.user_path(), zone=LOS_ANGELES).data

        self.assertEqual(
            data["range"],
            {"start_date": "2025-10-27", "end_date": "2026-10-27", "timezone": AUCKLAND, "is_all_time": False},
        )


class StatsScopeTests(StatsV2Base):
    """The scope's range facts and formatting helpers."""

    def test_calendar_year_top_tag_and_today(self):
        def scope(start, end):
            return StatsScope(owner=self.owner, viewer=self.owner, start_date=start, end_date=end)

        year = scope(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual((year.calendar_year, year.top_tag, year.includes_today), (2026, "top2026", True))
        past = scope(date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual((past.calendar_year, past.top_tag, past.includes_today), (2024, "top2024", False))
        partial = scope(date(2024, 1, 2), date(2024, 12, 31))
        self.assertEqual((partial.calendar_year, partial.top_tag), (None, None))
        spanning = scope(date(2024, 1, 1), date(2025, 12, 31))
        self.assertIsNone(spanning.calendar_year)
        all_time = scope(None, None)
        self.assertEqual((all_time.top_tag, all_time.includes_today), ("topalltime", True))

    def test_audience(self):
        params = {"start_date": "all", "end_date": "all"}
        self.assertEqual(build_scope(self.owner, self.owner, params).audience, "owner")
        self.assertEqual(build_scope(self.owner, self.follower, params).audience, "follower")
        self.assertEqual(build_scope(self.owner, self.stranger, params).audience, "public")

    def test_media_summaries_are_loaded_once_with_no_tracking_state(self):
        film = self.item(MediaTypes.MOVIE.value, "film", title="Film")
        scope = build_scope(self.owner, self.stranger, {"start_date": "all", "end_date": "all"})

        summaries = scope.media_summaries([film.id, film.id])
        with self.assertNumQueries(0):
            again = scope.with_media_type("movie").media_summary(film.id)

        self.assertEqual(list(summaries), [film.id])
        self.assertEqual(summaries[film.id]["title"], "Film")
        self.assertIsNone(summaries[film.id]["user_state"])
        self.assertIs(again, summaries[film.id])

    def test_star_strings(self):
        self.assertEqual(stars(Decimal("8.0")), "4.0")
        self.assertEqual(stars(Decimal("7.0")), "3.5")
        self.assertEqual(stars(Decimal("7.3")), "3.65")
        self.assertEqual(stars(7.3), "3.65")
        self.assertEqual(stars(Decimal(10)), "5.0")
        self.assertEqual(stars(Decimal("8.25")), "4.13")
        self.assertIsNone(stars(None))
        self.assertEqual(one_decimal(Decimal("3.45")), "3.5")
        self.assertEqual(one_decimal(-1.1), "-1.1")
        self.assertEqual(rating_bound(Decimal(10)), "10.0")
        self.assertEqual(rating_bound(0), "0.0")
        self.assertEqual(day_string(date(2025, 3, 2)), "2025-03-02")
        self.assertEqual(month_string(date(2025, 3, 2)), "2025-03")


class StatsV2RequestTests(StatsV2Base):
    """Legacy fallback, 400s and 404s (STAT-300, STAT-311, STAT-900)."""

    def test_without_schema_2_the_legacy_payload_is_unchanged(self):
        self.seed_every_type()
        for params in ({}, {"schema": "1"}, {"schema": "3"}, {"schema": ""}):
            with self.subTest(params=params):
                data = self.get(self.owner, {**params, "start_date": "all", "end_date": "all"}).data
                self.assertEqual(data["schema_version"], 1)
                for key in ("overview", "media_count", "score_distribution", "top_rated", "diary_top_rated"):
                    self.assertIn(key, data)
                self.assertNotIn("header", data)

    def test_the_legacy_range_is_on_the_owners_calendar_for_every_viewer(self):
        # STAT-900: at NOW it is 27 October in Auckland, the 26th in New York.
        self.owner.calendar_timezone = NEW_YORK
        self.owner.save(update_fields=["calendar_timezone"])
        expected = {"start_date": "2025-10-26", "end_date": "2026-10-26", "timezone": NEW_YORK, "is_all_time": False}
        owner = self.get(self.owner, {}, zone=NEW_YORK).data
        follower = self.get(self.follower, {}, path=self.user_path(), zone=AUCKLAND).data
        self.assertEqual((owner["schema_version"], follower["schema_version"]), (1, 1))
        self.assertEqual(owner["range"], expected)
        self.assertEqual(follower["range"], expected)
        self.assertEqual((follower["list_progress"], follower["series_progress"]), ([], []))

    def test_invalid_requests_answer_400_with_the_field(self):
        cases = [
            ({"start_date": "2025-13-01", "end_date": "2025-12-31"}, "start_date"),
            ({"start_date": "2025-01-01", "end_date": "soon"}, "end_date"),
            ({"start_date": "2025-12-31", "end_date": "2025-01-01"}, "date_range"),
            ({"start_date": "all", "end_date": "2025-12-31"}, "date_range"),
            ({"start_date": "all", "end_date": "all", "media_type": "season"}, "media_type"),
            ({"start_date": "all", "end_date": "all", "media_type": "episode"}, "media_type"),
            ({"start_date": "all", "end_date": "all", "media_type": "podcast"}, "media_type"),
        ]
        for path in (
            "/api/v1/stats/me/summary/",
            "/api/v1/stats/me/highest-rated/",
            "/api/v1/stats/me/most-logged/",
            self.user_path(),
            self.user_path("highest-rated"),
        ):
            for params, key in cases:
                with self.subTest(path=path, params=params):
                    response = self.get(self.follower, {"schema": "2", **params}, path=path)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(list(response.data["error"]["fields"]), [key])
                    self.assertIsInstance(response.data["error"]["fields"][key], list)

    @override_settings(MUSIC_ENABLED=False)
    def test_an_unexposed_type_is_rejected_and_left_out(self):
        response = self.get(self.owner, {**ALL_TIME, "media_type": "music"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data["error"]["fields"]), ["media_type"])

        data = self.get(self.owner, ALL_TIME).data
        self.assertNotIn("music", [row["media_type"] for row in data["media_types"]])
        self.assertNotIn("music", data["time"]["hours_total"]["media_types"])
        self.assertNotIn("music", data["time"]["hours_total"]["leaves_out"])

    def test_collections_need_a_collection_type_and_a_group(self):
        path = "/api/v1/stats/me/collections/"
        cases = [
            ({"group": "complete"}, "media_type"),
            ({"media_type": "tv", "group": "complete"}, "media_type"),
            ({"media_type": "movie"}, "group"),
            ({"media_type": "movie", "group": "all"}, "group"),
        ]
        for params, key in cases:
            with self.subTest(params=params):
                response = self.get(self.owner, params, path=path)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(list(response.data["error"]["fields"]), [key])
        for media_type in COLLECTION_TABS:
            for group in ("complete", "almost"):
                response = self.get(self.owner, {"media_type": media_type, "group": group}, path=path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(set(response.data), {"count", "next", "previous", "results"})

    def test_hidden_profiles_answer_404_everywhere(self):
        # S-19
        private = get_user_model().objects.create_user(
            username="private",
            password="strong-password-123",
            profile_private=True,
        )
        blocker = get_user_model().objects.create_user(username="blocker", password="strong-password-123")
        Block.objects.create(blocker=blocker, blocked=self.stranger)
        Block.objects.create(blocker=self.follower, blocked=blocker)
        endpoints = [
            ("summary", {**YEAR_2025}),
            ("summary", {"start_date": "all", "end_date": "all"}),
            ("highest-rated", {**YEAR_2025}),
            ("most-logged", {**YEAR_2025}),
            ("most-logged", {"start_date": "all", "end_date": "all"}),
            ("collections", {"media_type": "movie", "group": "complete"}),
        ]
        for username, viewer in (
            ("private", self.stranger),
            ("blocker", self.stranger),
            ("blocker", self.follower),
            ("nobody", self.stranger),
        ):
            for endpoint, params in endpoints:
                with self.subTest(username=username, viewer=viewer.username, endpoint=endpoint):
                    response = self.get(viewer, params, path=self.user_path(endpoint, username))
                    self.assertEqual(response.status_code, 404)

        Follow.objects.create(from_user=self.stranger, to_user=private, status=FollowStatus.ACCEPTED)
        for endpoint, params in endpoints:
            with self.subTest(followed=endpoint):
                response = self.get(self.stranger, params, path=self.user_path(endpoint, "private"))
                self.assertEqual(response.status_code, 200)

    def test_stats_require_sign_in(self):
        for path in (
            "/api/v1/stats/me/summary/",
            "/api/v1/stats/me/highest-rated/",
            "/api/v1/stats/me/collections/",
            self.user_path("highest-rated"),
        ):
            with self.subTest(path=path):
                self.assertEqual(APIClient().get(path, ALL_TIME).status_code, 401)


class StatsV2ShowAllTests(StatsV2Base):
    """The show-all endpoints' paging (STAT-310)."""

    def test_most_logged_keeps_its_legacy_rows_without_schema_2(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.log(film)
        self.log(film, datetime(2025, 4, 1, tzinfo=UTC))

        legacy = self.get(self.owner, {"start_date": "all", "end_date": "all"}, path="/api/v1/stats/me/most-logged/")

        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(legacy.data["count"], 1)
        self.assertEqual(legacy.data["results"][0]["log_count"], 2)
        self.assertNotIn("times", legacy.data["results"][0])

    def test_pages_carry_next_and_previous_links(self):
        rows = [{"media": None, "stars": "5.0", "day": None}] * 10

        def page_of(_scope, page, page_size, exclude_ids=()):
            return {"count": 60, "results": rows[:page_size]}

        with patch("api.services.stats_v2_ratings.highest_rated_page", side_effect=page_of) as fetch:
            first = self.get(self.owner, {**ALL_TIME, "page_size": "25"}, path="/api/v1/stats/me/highest-rated/")
            second = self.get(self.owner, {**ALL_TIME, "page": "2", "page_size": "25"}, path="/api/v1/stats/me/highest-rated/")
            third = self.get(self.owner, {**ALL_TIME, "page": "3", "page_size": "25"}, path="/api/v1/stats/me/highest-rated/")
            capped = self.get(self.owner, {**ALL_TIME, "page_size": "500"}, path="/api/v1/stats/me/highest-rated/")
            past = self.get(self.owner, {**ALL_TIME, "page": "4", "page_size": "25"}, path="/api/v1/stats/me/highest-rated/")
            invalid = self.get(self.owner, {**ALL_TIME, "page": "zero"}, path="/api/v1/stats/me/highest-rated/")

        self.assertEqual(first.data["count"], 60)
        self.assertIn("page=2", first.data["next"])
        self.assertIsNone(first.data["previous"])
        self.assertIn("page=3", second.data["next"])
        self.assertNotIn("page=", second.data["previous"])
        self.assertIsNone(third.data["next"])
        self.assertIn("page=2", third.data["previous"])
        self.assertEqual([call.args[1:] for call in fetch.call_args_list[:4]], [(1, 25), (2, 25), (3, 25), (1, 100)])
        self.assertIsNone(capped.data["next"])
        self.assertEqual(past.status_code, 404)
        self.assertEqual(invalid.status_code, 404)

    def test_every_show_all_endpoint_answers_a_page(self):
        self.seed_every_type()
        for path, params in (
            ("/api/v1/stats/me/highest-rated/", {**YEAR_2025, "media_type": "movie"}),
            ("/api/v1/stats/me/most-logged/", {**ALL_TIME}),
            (self.user_path("highest-rated"), {**ALL_TIME}),
            (self.user_path("most-logged"), {**YEAR_2025, "media_type": "tv"}),
            (self.user_path("collections"), {"media_type": "book", "group": "almost"}),
        ):
            with self.subTest(path=path):
                response = self.get(self.follower, params, path=path)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(set(response.data), {"count", "next", "previous", "results"})
                self.assertIsInstance(response.data["results"], list)


class StatsV2QueryTests(StatsV2Base):
    """The query count doesn't grow with the library (PLAN 2.6 test 17)."""

    def test_query_count_is_flat(self):
        for tab in (None, "movie", "tv"):
            with self.subTest(tab=tab):
                small = self.queries_with(2, tab)
                large = self.queries_with(6, tab)
                self.assertEqual(large, small)

    def queries_with(self, count, tab):
        clear_library()
        for index in range(count):
            film = self.item(MediaTypes.MOVIE.value, f"film-{count}-{index}")
            self.log(film, rating=Decimal("8.0"))
            self.log(film, datetime(2025, 5, index + 1, tzinfo=UTC), rating=Decimal("6.0"))
            show = self.item(MediaTypes.TV.value, f"show-{count}-{index}")
            season = self.item(MediaTypes.SEASON.value, f"show-{count}-{index}", season=1)
            episode = self.item(MediaTypes.EPISODE.value, f"show-{count}-{index}", season=1, episode=1)
            _tv, season_row = self.show_rows(show, season)
            entry = self.log(episode, rating=Decimal("9.0"))
            self.viewing(season_row, episode, watched_on=date(2025, 3, 2), kind="log", entry=entry)
            self.viewing(season_row, episode, watched_on=date(2025, 4, 2))
        client = self.client_for(self.follower)
        query = {**YEAR_2025, **({"media_type": tab} if tab else {})}
        with CaptureQueriesContext(connection) as queries:
            response = client.get(self.user_path(), query)
        self.assertEqual(response.status_code, 200)
        return len(queries)
