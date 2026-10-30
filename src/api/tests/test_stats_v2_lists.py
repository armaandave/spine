"""Stats v2 Top, list progress rings and collections (docs/stats-contract.md).

STAT-206 to STAT-208 (Top from a tagged ranked list, else Highest rated), STAT-213
(rings) and STAT-214 (collections), with STAT-002 to STAT-004 for other viewers.
Acceptance scenarios S-12 to S-16, S-23 and S-24.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from api.services import stats_v2_lists
from api.services.stats_scope import StatsScope
from api.tests.test_stats_v2_shape import (
    ALL_TIME,
    OWNER_ZONES,
    YEAR_2024,
    YEAR_2025,
    StatsV2Base,
)
from app.models import (
    TV,
    Anime,
    DiaryEntry,
    Game,
    Item,
    MediaSeries,
    MediaSeriesItem,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from lists.featured import registry
from lists.models import CustomList, CustomListItem, FeaturedListSync
from lists.tests.featured.helpers import make_definition
from social.models import Block

OLD = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 6, 1, tzinfo=UTC)


class ListsBase(StatsV2Base):
    """Helpers for lists, tracking rows and scopes."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.curator = get_user_model().objects.create_user(username="curator", password="strong-password-123")

    def make_list(self, name, items, *, owner=None, tags=("top2025",), ranked=True, visibility="public",
                  positions=None, featured=False, updated_at=None):
        custom_list = CustomList.objects.create(
            owner=owner or self.owner,
            name=name,
            tags=list(tags),
            is_ranked=ranked,
            visibility=visibility,
            is_featured=featured,
        )
        for index, item in enumerate(items, start=1):
            if positions is None:
                position = index if ranked else None
            else:
                position = positions[index - 1]
            CustomListItem.objects.create(custom_list=custom_list, item=item, position=position)
        if updated_at is not None:
            CustomList.objects.filter(id=custom_list.id).update(updated_at=updated_at)
        return custom_list

    def movies(self, count, prefix="m", **fields):
        return [self.item(MediaTypes.MOVIE.value, f"{prefix}{index}", **fields) for index in range(count)]

    def completed(self, model, items, user=None):
        model.objects.bulk_create([
            model(user=user or self.owner, item=item, status=Status.COMPLETED.value) for item in items
        ])

    def scope(self, viewer=None, *, start=None, end=None, media_type=None):
        return StatsScope(
            owner=self.owner,
            viewer=viewer or self.owner,
            start_date=start,
            end_date=end,
            media_type=media_type,
        )

    def set_zone(self, zone):
        self.owner.calendar_timezone = zone
        self.owner.save(update_fields=["calendar_timezone"])

    def top(self, viewer, params, media_type=None):
        path = "/api/v1/stats/me/summary/" if viewer == self.owner else self.user_path()
        query = {**params, **({"media_type": media_type} if media_type else {})}
        response = self.get(viewer, query, path=path)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["top"]


class TopTagTests(ListsBase):
    """STAT-206: which tags name a Top list."""

    def test_matching_and_non_matching_spellings(self):
        for tag in ("top2025", "Top 2025", "top-2025", "TOP_2025", "  top2025 ", "top  -_2025"):
            with self.subTest(tag=tag):
                self.assertEqual(stats_v2_lists.top_tag_for(tag), "top2025")
        for tag in ("topalltime", "TOP all time", "Top-All-Time", "top all_time"):
            with self.subTest(tag=tag):
                self.assertEqual(stats_v2_lists.top_tag_for(tag), "topalltime")
        for tag in ("top20255", "top 25", "top", "my top2025", "top2025!", "alltime", None, 2025):
            with self.subTest(tag=tag):
                self.assertIsNone(stats_v2_lists.top_tag_for(tag))


class TopTests(ListsBase):
    """STAT-206 to STAT-208 through the summary endpoint."""

    def test_private_list_shows_to_the_owner_with_the_hint_and_others_get_highest_rated(self):
        # S-12.
        films = self.movies(3)
        custom_list = self.make_list("My 2025", films, tags=["Top 2025"], visibility="private")
        owner_top = self.top(self.owner, YEAR_2025, "movie")
        self.assertEqual(owner_top["source"], "list")
        self.assertEqual(owner_top["tag"], "top2025")
        self.assertIsNone(owner_top["tag_hint"])
        self.assertTrue(owner_top["hint_private"])
        self.assertEqual(owner_top["list"], {"id": custom_list.id, "name": "My 2025", "total": 3})
        self.assertEqual([row["rank"] for row in owner_top["items"]], [1, 2, 3])
        self.assertEqual([row["media"]["ref"]["item_id"] for row in owner_top["items"]], [f.id for f in films])
        for viewer in (self.follower, self.stranger):
            with self.subTest(viewer=viewer.username):
                top = self.top(viewer, YEAR_2025, "movie")
                self.assertEqual(top["source"], "highest_rated")
                self.assertIsNone(top["list"])
                self.assertIsNone(top["tag_hint"])
                self.assertFalse(top["hint_private"])

    def test_public_list_shows_to_everyone_without_the_private_hint(self):
        self.make_list("Public 2025", self.movies(2))
        for viewer in (self.owner, self.follower, self.stranger):
            with self.subTest(viewer=viewer.username):
                top = self.top(viewer, YEAR_2025, "movie")
                self.assertEqual(top["source"], "list")
                self.assertFalse(top["hint_private"])
                self.assertIsNone(top["tag_hint"])

    def test_mixed_list_feeds_global_not_the_type_tab(self):
        # S-13: movies and TV (a season counts in the tv bucket).
        show = self.item(MediaTypes.TV.value, "show")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        mixed = self.make_list("Mixed", [*self.movies(2), show, season])
        self.assertEqual(self.top(self.owner, YEAR_2025)["list"]["id"], mixed.id)
        self.assertEqual(self.top(self.owner, YEAR_2025, "movie")["source"], "highest_rated")
        self.assertEqual(self.top(self.owner, YEAR_2025, "tv")["source"], "highest_rated")

    def test_one_type_list_feeds_that_tab_not_global(self):
        show = self.item(MediaTypes.TV.value, "show")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        tv_list = self.make_list("TV", [show, season])
        self.assertEqual(self.top(self.owner, YEAR_2025, "tv")["list"]["id"], tv_list.id)
        self.assertEqual(self.top(self.owner, YEAR_2025)["source"], "highest_rated")

    def test_the_list_edited_last_wins(self):
        # S-14: adding an item through the API moves updated_at (STAT-403).
        films = self.movies(3)
        older = self.make_list("Older", films[:1], updated_at=OLD)
        newer = self.make_list("Newer", films[1:2], updated_at=LATER)
        self.assertEqual(self.top(self.owner, YEAR_2025, "movie")["list"]["id"], newer.id)
        film = films[2]
        response = self.client_for(self.owner).post(
            f"/api/v1/lists/{older.id}/items/",
            {"ref": {"source": film.source, "media_type": film.media_type, "media_id": film.media_id}},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.top(self.owner, YEAR_2025, "movie")["list"]["id"], older.id)

    def test_a_tie_on_updated_at_goes_to_the_highest_id(self):
        films = self.movies(2)
        self.make_list("First", films[:1], updated_at=LATER)
        second = self.make_list("Second", films[1:], updated_at=LATER)
        self.assertEqual(self.top(self.owner, YEAR_2025, "movie")["list"]["id"], second.id)

    def test_letterboxd_positions_count_as_ranked_and_unranked_lists_dont(self):
        # S-15.
        films = self.movies(4)
        imported = self.make_list("Letterboxd", films[:2], tags=["topalltime"], ranked=False, positions=[1, 2])
        self.make_list("Loose", films[2:], tags=["topalltime"], ranked=False, updated_at=LATER)
        self.assertEqual(self.top(self.owner, ALL_TIME, "movie")["list"]["id"], imported.id)

    def test_featured_lists_and_lists_the_owner_only_helps_edit_are_never_candidates(self):
        films = self.movies(2)
        self.make_list("Featured", films[:1], featured=True)
        shared = self.make_list("Shared", films[1:], owner=self.curator)
        shared.collaborators.add(self.owner)
        self.assertEqual(self.top(self.owner, YEAR_2025, "movie")["source"], "highest_rated")

    def test_the_wanted_tag_follows_the_range(self):
        films = self.movies(2)
        self.make_list("2024", films[:1], tags=["top2024"])
        all_time = self.make_list("All", films[1:], tags=["TOP all time"])
        self.assertEqual(self.top(self.owner, YEAR_2025, "movie")["source"], "highest_rated")
        self.assertEqual(self.top(self.owner, YEAR_2024, "movie")["source"], "list")
        self.assertEqual(self.top(self.owner, ALL_TIME, "movie")["list"]["id"], all_time.id)
        other = {"schema": "2", "start_date": "2024-01-01", "end_date": "2024-12-30", "media_type": "movie"}
        top = self.get(self.owner, other).data["top"]
        self.assertEqual((top["source"], top["tag"], top["tag_hint"]), ("highest_rated", None, None))

    def test_ten_items_in_list_order_with_their_title_ratings(self):
        films = self.movies(12)
        # Shuffled positions and one item without a position (last).
        positions = [5, 3, 1, 2, 4, 6, 7, 8, 9, 10, 11, None]
        custom_list = self.make_list("Big", films, positions=positions)
        self.log(films[2], datetime(2025, 3, 1, tzinfo=UTC), rating=Decimal("6.0"))
        self.log(films[2], datetime(2025, 5, 1, tzinfo=UTC), rating=Decimal("9.0"))
        self.log(films[2], datetime(2025, 6, 1, tzinfo=UTC))
        self.log(films[3], datetime(2024, 6, 1, tzinfo=UTC), rating=Decimal("10.0"))
        Movie.objects.bulk_create([Movie(user=self.owner, item=films[4], status=Status.COMPLETED.value,
                                         score=Decimal("8.0"))])
        year = self.top(self.owner, YEAR_2025, "movie")
        self.assertEqual(year["list"], {"id": custom_list.id, "name": "Big", "total": 12})
        self.assertEqual(len(year["items"]), 10)
        self.assertEqual([row["rank"] for row in year["items"]], list(range(1, 11)))
        expected_order = [films[2], films[3], films[1], films[4], films[0], *films[5:10]]
        self.assertEqual([row["media"]["ref"]["item_id"] for row in year["items"]], [f.id for f in expected_order])
        # The newest rated log in the range; nothing else counts in a year.
        self.assertEqual([row["stars"] for row in year["items"][:4]], ["4.5", None, None, None])
        custom_list.tags = ["topalltime"]
        custom_list.save()
        all_time = self.top(self.owner, ALL_TIME, "movie")
        # All Time adds the current rating for Items with no rated log.
        self.assertEqual([row["stars"] for row in all_time["items"][:4]], ["4.5", "5.0", None, "4.0"])

    def test_unexposed_items_are_left_out(self):
        films = self.movies(2)
        album = self.item(MediaTypes.MUSIC.value, "album")
        custom_list = self.make_list("With music", [*films, album])
        with override_settings(MUSIC_ENABLED=False):
            top = self.top(self.owner, YEAR_2025, "movie")
        self.assertEqual(top["list"], {"id": custom_list.id, "name": "With music", "total": 2})
        self.assertEqual(len(top["items"]), 2)

    def test_highest_rated_leaves_out_what_top_shows_from_a_list(self):
        films = self.movies(12)
        for index, film in enumerate(films):
            self.log(film, datetime(2025, 3, 1, tzinfo=UTC), rating=Decimal("10.0") - Decimal(index) / 2)
        # Top shows the list's first 10; the 11th list item isn't in Top, so it stays.
        self.make_list("Best", films[:11])
        response = self.get(self.owner, {**YEAR_2025, "media_type": "movie"})
        self.assertEqual(response.data["top"]["source"], "list")
        highest = response.data["highest_rated"]
        self.assertEqual([row["media"]["ref"]["item_id"] for row in highest["items"]], [films[10].id, films[11].id])
        self.assertEqual(highest["total"], 2)
        page = self.get(
            self.owner,
            {**YEAR_2025, "media_type": "movie"},
            path="/api/v1/stats/me/highest-rated/",
        ).data
        self.assertEqual(page["count"], 2)
        self.assertEqual([row["media"]["ref"]["item_id"] for row in page["results"]], [films[10].id, films[11].id])
        # Without a list, Highest rated keeps every title (and Top is its first 10).
        CustomList.objects.all().delete()
        cache.clear()
        response = self.get(self.owner, {**YEAR_2025, "media_type": "movie"})
        self.assertEqual(response.data["highest_rated"]["total"], 12)

    def test_fallback_ranks_highest_rated_rows(self):
        rows = [{"media": {"title": f"T{index}"}, "stars": "4.0", "day": None} for index in range(12)]
        with patch.object(stats_v2_lists.ratings, "highest_rated_items", return_value=rows):
            top = stats_v2_lists.build_top(self.scope(start=date(2024, 1, 1), end=date(2024, 12, 31),
                                                      media_type="anime"))
        self.assertEqual(top["source"], "highest_rated")
        self.assertEqual(top["tag_hint"], "Tag a ranked anime list top2024")
        self.assertEqual([row["rank"] for row in top["items"]], list(range(1, 11)))
        self.assertEqual(top["items"][0], {"rank": 1, "media": {"title": "T0"}, "stars": "4.0"})

    def test_query_count_is_flat(self):
        def count_queries():
            scope = self.scope(start=date(2025, 1, 1), end=date(2025, 12, 31), media_type="movie")
            with CaptureQueriesContext(connection) as queries:
                top = stats_v2_lists.build_top(scope)
            self.assertEqual(top["source"], "list")
            return len(queries)

        films = self.movies(30)
        small = self.make_list("Small", films[:2], updated_at=LATER)
        for film in films[:2]:
            self.log(film, rating=Decimal("8.0"))
        baseline = count_queries()
        # More lists, more items, more logs: the same number of queries.
        for index in range(5):
            self.make_list(f"Other {index}", films[index * 4 : index * 4 + 4], updated_at=OLD)
        for film in films[2:30]:
            CustomListItem.objects.create(custom_list=small, item=film, position=None)
            self.log(film, rating=Decimal("7.0"))
        CustomList.objects.filter(id=small.id).update(updated_at=LATER)
        self.assertEqual(count_queries(), baseline)


@override_settings(MUSIC_ENABLED=True)
class ListRingTests(ListsBase):
    """STAT-213: rings from Spine's own lasting featured lists."""

    def featured(self, key, items, *, definition=True, sync=True, fingerprint="f1", **fields):
        custom_list = self.make_list(key, items, owner=self.curator, tags=(), featured=True)
        if sync:
            FeaturedListSync.objects.create(key=key, custom_list=custom_list, fingerprint=fingerprint,
                                            **fields.pop("sync_fields", {}))
        if definition:
            self.definitions.append(make_definition(key=key, name=key, **fields))
        return custom_list

    def setUp(self):
        super().setUp()
        self.definitions = []

    def rings(self, viewer=None, media_type=None):
        with registry.override_definitions(self.definitions):
            return stats_v2_lists.build_list_rings(self.scope(viewer, media_type=media_type))

    def test_only_lasting_lists_with_two_seen_show_rings(self):
        # S-23.
        films = self.movies(6)
        self.completed(Movie, films[:3])
        kinds = ("award", "canon", "community", "curator", "editorial")
        lasting = {kind: self.featured(f"{kind}-list", films[:4], kind=kind) for kind in kinds}
        self.featured("chart-list", films[:4], kind="chart")
        window = registry.LiveWindow(starts_at=OLD, ends_at=OLD + timedelta(hours=4))
        self.featured("live-award", films[:4], kind="award", live_windows=(window,))
        self.featured("partial-list", films[:4], kind="canon",
                      sync_fields={"entry_count": 100, "resolved_count": 10})
        self.featured("mal-most-popular-anime", films[:4], kind="community")
        retired = self.featured("retired-list", films[:4], definition=False)
        self.featured("one-seen", [films[0], films[4], films[5]], kind="canon")
        manual = self.featured("manual", films[:3], definition=False, sync=False)
        rings = self.rings()
        by_id = {ring["id"]: ring for ring in rings}
        self.assertEqual(set(by_id), {*(custom_list.id for custom_list in lasting.values()), manual.id})
        self.assertNotIn(retired.id, by_id)
        self.assertEqual(by_id[lasting["award"].id],
                         {"id": lasting["award"].id, "name": "award-list", "media_type": "movie",
                          "kind": "award", "seen": 3, "total": 4, "seen_in_range": None})
        self.assertEqual((by_id[manual.id]["kind"], by_id[manual.id]["media_type"]), ("editorial", "movie"))
        # The manual list is 3 of 3: the highest share comes first.
        self.assertEqual(rings[0]["id"], manual.id)

    def test_rings_follow_the_tab(self):
        films = self.movies(2)
        games = [self.item(MediaTypes.GAME.value, f"g{index}") for index in range(2)]
        self.completed(Movie, films)
        self.completed(Game, games)
        movie_ring = self.featured("movie-canon", films, kind="canon")
        game_ring = self.featured("game-canon", games, kind="canon", media_types=("game",))
        self.featured("igdb-playing", games, kind="community", media_types=("game",))
        mixed = self.featured("mixed-manual", [*films, *games], definition=False, sync=False)
        self.assertEqual({ring["id"] for ring in self.rings()}, {movie_ring.id, game_ring.id, mixed.id})
        self.assertEqual([ring["id"] for ring in self.rings(media_type="game")], [game_ring.id])
        self.assertEqual([ring["id"] for ring in self.rings(media_type="movie")], [movie_ring.id])
        self.assertIsNone(next(ring for ring in self.rings() if ring["id"] == mixed.id)["media_type"])
        # Completed with no log is an undated completion: it never counts in a range,
        # so a year has no progress to show and sends no rings.
        with registry.override_definitions(self.definitions):
            year = stats_v2_lists.build_list_rings(
                self.scope(start=date(2020, 1, 1), end=date(2020, 12, 31)),
            )
        self.assertEqual(year, [])

    def test_other_viewers_count_completed_rows_but_only_logs_they_can_see(self):
        # STAT-002/003: Completed rows count for everyone; a hidden log doesn't.
        anime = [self.item(MediaTypes.ANIME.value, f"a{index}") for index in range(4)]
        self.completed(Anime, anime[:1])
        self.log(anime[1], visibility="followers")
        self.log(anime[2], visibility="private")
        ring = self.featured("anime-canon", anime, kind="canon", media_types=("anime",))
        seen = {viewer.username: {r["id"]: r["seen"] for r in self.rings(viewer)}.get(ring.id)
                for viewer in (self.owner, self.follower, self.stranger)}
        self.assertEqual(seen, {"owner": 3, "follower": 2, "stranger": None})

    def test_ring_totals_are_cached_per_fingerprint(self):
        films = self.movies(3)
        self.completed(Movie, films)
        ring = self.featured("canon", films[:2], kind="canon")
        self.assertEqual(self.rings()[0]["total"], 2)
        CustomListItem.objects.create(custom_list=ring, item=films[2], position=3)
        self.assertEqual(self.rings()[0]["total"], 2)
        FeaturedListSync.objects.filter(key="canon").update(fingerprint="f2")
        self.assertEqual(self.rings()[0]["total"], 3)

    def test_the_summary_sends_rings_for_every_viewer(self):
        films = self.movies(2)
        self.completed(Movie, films)
        ring = self.featured("canon", films, kind="canon")
        with registry.override_definitions(self.definitions):
            for viewer, path in ((self.owner, "/api/v1/stats/me/summary/"), (self.follower, self.user_path())):
                data = self.get(viewer, ALL_TIME, path=path).data
                self.assertEqual([r["id"] for r in data["list_rings"]], [ring.id])

    def ring_in(self, ring, year=None, viewer=None, media_type=None):
        """Return the ring's row in a calendar year, or All Time; None when not sent."""
        dates = {} if year is None else {"start": date(year, 1, 1), "end": date(year, 12, 31)}
        rows = self.ring_rows(viewer, media_type=media_type, **dates)
        return next((row for row in rows if row["id"] == ring.id), None)

    def ring_rows(self, viewer=None, **scope_fields):
        with registry.override_definitions(self.definitions):
            return stats_v2_lists.build_list_rings(self.scope(viewer, **scope_fields))

    def test_a_year_counts_the_titles_first_seen_in_it(self):
        films = self.movies(5)
        self.completed(Movie, films[:1] + films[2:3])
        # Seen in 2024, then again in 2025: first seen in 2024.
        self.log(films[0], datetime(2024, 5, 1, tzinfo=UTC))
        self.log(films[0], datetime(2025, 5, 1, tzinfo=UTC))
        self.log(films[1], datetime(2025, 7, 1, tzinfo=UTC))
        # films[2] is Completed with no log: an undated completion, never in a range.
        # films[3] is logged on 1 January 2025, a UTC-midnight carrier: 2025 everywhere.
        self.log(films[3], datetime(2025, 1, 1, tzinfo=UTC))
        ring = self.featured("canon", films, kind="canon")
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                self.set_zone(zone)
                year = self.ring_in(ring, 2025)
                self.assertEqual(
                    {key: year[key] for key in ("seen", "total", "seen_in_range")},
                    {"seen": 4, "total": 5, "seen_in_range": 2},
                )
                self.assertEqual(self.ring_in(ring, 2024)["seen_in_range"], 1)
                # No title first seen in 2023: the ring isn't sent that year.
                self.assertIsNone(self.ring_in(ring, 2023))
                self.assertIsNone(self.ring_in(ring)["seen_in_range"])
                self.assertEqual(self.ring_in(ring, 2025, media_type="movie")["seen_in_range"], 2)

    def test_a_year_sorts_rings_by_titles_first_seen_in_it(self):
        films = self.movies(6)
        self.completed(Movie, films[:1])
        self.log(films[1], datetime(2025, 3, 1, tzinfo=UTC))
        for film in films[2:5]:
            self.log(film, datetime(2025, 4, 1, tzinfo=UTC))
        full = self.featured("full", films[:2], kind="canon")
        most = self.featured("most", films[2:6], kind="canon")
        # All Time: the higher share first. 2025: more first seen in it first.
        self.assertEqual([row["id"] for row in self.ring_rows()], [full.id, most.id])
        year = self.ring_rows(start=date(2025, 1, 1), end=date(2025, 12, 31))
        self.assertEqual([(row["id"], row["seen_in_range"]) for row in year], [(most.id, 3), (full.id, 1)])

    def test_first_seen_days_are_on_the_owners_calendar(self):
        anime = [self.item(MediaTypes.ANIME.value, f"a{index}") for index in range(3)]
        ring = self.featured("anime-manual", anime, definition=False, sync=False)
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                DiaryEntry.objects.all().delete()
                self.set_zone(zone)
                local = ZoneInfo(zone)
                self.log(anime[0], datetime(2025, 12, 31, 23, 30, tzinfo=local))
                self.log(anime[1], datetime(2026, 1, 1, 0, 30, tzinfo=local))
                for viewer in (self.owner, self.follower):
                    self.assertEqual(self.ring_in(ring, 2025, viewer)["seen_in_range"], 1)
                    self.assertEqual(self.ring_in(ring, 2026, viewer)["seen_in_range"], 1)
                    self.assertIsNone(self.ring_in(ring, 2024, viewer))

    def test_tv_titles_are_first_seen_on_their_earliest_log_or_dated_viewing(self):
        shows = []
        for index in range(5):
            show = self.item(MediaTypes.TV.value, f"show{index}")
            season = self.item(MediaTypes.SEASON.value, f"show{index}", season=1)
            episode = self.item(MediaTypes.EPISODE.value, f"show{index}", season=1, episode=1)
            tv, season_row = self.show_rows(show, season)
            TV.objects.filter(pk=tv.pk).update(status=Status.COMPLETED.value)
            shows.append((show, season, episode, season_row))
        # 0: ticked in 2024, then an episode log in 2025: first seen in 2024.
        _show, _season, episode, season_row = shows[0]
        self.viewing(season_row, episode, watched_on=date(2024, 5, 1))
        entry = self.log(episode, datetime(2025, 3, 1, tzinfo=UTC))
        self.viewing(season_row, episode, watched_on=date(2025, 3, 1), kind="log", entry=entry)
        # 1: ticked in 2025, with no log.
        self.viewing(shows[1][3], shows[1][2], watched_on=date(2025, 3, 1))
        # 2: a season log in 2025 counts as its show.
        self.log(shows[2][1], datetime(2025, 4, 1, tzinfo=UTC))
        # 3: marked watched: its fills have no day.
        self.viewing(shows[3][3], shows[3][2], kind="fill")
        # 4: its only viewing belongs to a private log in 2025.
        private = self.log(shows[4][2], datetime(2025, 5, 1, tzinfo=UTC), visibility="private")
        self.viewing(shows[4][3], shows[4][2], watched_on=date(2025, 5, 1), kind="log", entry=private)
        ring = self.featured("tv-manual", [row[0] for row in shows], definition=False, sync=False)

        owner = self.ring_in(ring, 2025)
        self.assertEqual((owner["media_type"], owner["seen"], owner["seen_in_range"]), ("tv", 5, 3))
        self.assertEqual(self.ring_in(ring, 2025, media_type="tv")["seen_in_range"], 3)
        self.assertEqual(self.ring_in(ring, 2024)["seen_in_range"], 1)
        # A hidden log and its viewing count for nothing (STAT-003).
        stranger = self.ring_in(ring, 2025, self.stranger)
        self.assertEqual((stranger["seen"], stranger["seen_in_range"]), (5, 2))

    def test_query_count_is_flat_in_a_year(self):
        def count_queries():
            scope = self.scope(start=date(2025, 1, 1), end=date(2025, 12, 31))
            with registry.override_definitions(self.definitions):
                # Ring totals are cached per list: warm them, then count.
                stats_v2_lists.build_list_rings(scope)
                with CaptureQueriesContext(connection) as queries:
                    rings = stats_v2_lists.build_list_rings(
                        self.scope(start=date(2025, 1, 1), end=date(2025, 12, 31)),
                    )
            self.assertTrue(rings)
            return len(queries)

        films = self.movies(40)
        for film in films[:2]:
            self.log(film, datetime(2025, 3, 1, tzinfo=UTC))
        self.featured("canon", films[:2], kind="canon")
        baseline = count_queries()
        for index, film in enumerate(films[2:]):
            self.log(film, datetime(2024 + index % 2, 3, 1, tzinfo=UTC))
        self.featured("more", films[2:], kind="canon", fingerprint="f2")
        self.assertEqual(count_queries(), baseline)


class CollectionTests(ListsBase):
    """STAT-214: complete and almost complete series, lifetime."""

    def series(self, name, seen, unseen, *, media_type="movie", source=Sources.TMDB.value, extra=()):
        series = MediaSeries.objects.create(source=source, series_id=name, media_type=media_type, name=name)
        model = {"movie": Movie, "game": Game, "anime": Anime}[media_type]
        seen_items = [
            self.item(media_type, f"{name}-s{index}", source=source) for index in range(seen)
        ]
        unseen_items = [
            self.item(media_type, f"{name}-u{index}", source=source) for index in range(unseen)
        ]
        for item in [*seen_items, *unseen_items]:
            item.release_date = date(2020, 1, 1)
            item.save(update_fields=["release_date"])
        self.completed(model, seen_items)
        members = [*seen_items, *unseen_items, *extra]
        MediaSeriesItem.objects.bulk_create([
            MediaSeriesItem(series=series, item=item, position=index) for index, item in enumerate(members, start=1)
        ])
        return series

    def collections(self, viewer=None, media_type="movie"):
        return stats_v2_lists.build_collections(self.scope(viewer, media_type=media_type))

    def test_complete_and_almost_complete_rules(self):
        # S-24.
        self.series("two-of-three", 2, 1)
        self.series("eighteen-of-twenty", 18, 2)
        self.series("one-of-three", 1, 2)
        self.series("one-of-two", 1, 1)
        self.series("half", 2, 2)
        undated = self.item("movie", "undated-tmdb")
        future = self.item("movie", "future")
        future.release_date = date(2027, 1, 1)
        future.save(update_fields=["release_date"])
        self.series("done", 2, 0, extra=[undated, future])
        data = self.collections()
        self.assertEqual((data["complete_count"], data["almost_count"]), (1, 2))
        complete = data["complete"][0]
        self.assertEqual(
            {key: complete[key] for key in ("id", "source", "media_type", "name", "item_count", "completion")},
            {"id": "done", "source": "tmdb", "media_type": "movie", "name": "done", "item_count": 2,
             "completion": {"completed_count": 2, "total_count": 2}},
        )
        self.assertEqual(complete["poster_urls"], ["https://example.com/poster.jpg"] * 2)
        # Fewest left first, then largest.
        self.assertEqual([row["id"] for row in data["almost"]], ["two-of-three", "eighteen-of-twenty"])
        self.assertEqual(data["almost"][1]["completion"], {"completed_count": 18, "total_count": 20})

    def test_an_undated_member_counts_outside_tmdb(self):
        undated = self.item("game", "undated", source=Sources.IGDB.value)
        self.series("igdb", 2, 0, media_type="game", source=Sources.IGDB.value, extra=[undated])
        data = self.collections(media_type="game")
        self.assertEqual((data["complete_count"], data["almost_count"]), (0, 1))
        self.assertEqual(data["almost"][0]["completion"], {"completed_count": 2, "total_count": 3})

    def test_sorting_and_four_tiles_with_posters_only_for_them(self):
        for index, size in enumerate((2, 5, 3, 3, 4, 2)):
            self.series(f"c{index}", size, 0)
        scope = self.scope(media_type="movie")
        data = stats_v2_lists.build_collections(scope)
        self.assertEqual(data["complete_count"], 6)
        self.assertEqual([row["id"] for row in data["complete"]], ["c1", "c4", "c2", "c3"])
        self.assertTrue(all(len(row["poster_urls"]) <= 3 for row in data["complete"]))
        shown = set(
            MediaSeriesItem.objects.filter(series__series_id__in=["c1", "c4", "c2", "c3"]).values_list("item_id", flat=True),
        )
        self.assertTrue(set(scope._summaries) <= shown)
        self.assertEqual(len(scope._summaries), 12)

    def test_more_than_a_thousand_tracked_items(self):
        Item.objects.bulk_create([
            Item(media_id=f"big{index}", source=Sources.TMDB.value, media_type="movie", title=f"Big {index}",
                 image="https://example.com/poster.jpg", release_date=date(2020, 1, 1))
            for index in range(1101)
        ])
        films = list(Item.objects.filter(media_id__startswith="big").order_by("id"))
        self.completed(Movie, films[:1100])
        series = MediaSeries.objects.create(source="tmdb", series_id="big", media_type="movie", name="Big")
        MediaSeriesItem.objects.bulk_create([
            MediaSeriesItem(series=series, item=item, position=index) for index, item in enumerate(films, start=1)
        ])
        data = self.collections()
        self.assertEqual(data["almost_count"], 1)
        self.assertEqual(data["almost"][0]["completion"], {"completed_count": 1100, "total_count": 1101})
        page = stats_v2_lists.collections_page(self.scope(media_type="movie"), "almost", 1, 25)
        self.assertEqual(page["count"], 1)

    def test_other_viewers_see_collections_with_their_visible_logs(self):
        anime = [self.item("anime", f"a{index}") for index in range(3)]
        for item in anime:
            item.release_date = date(2020, 1, 1)
            item.save(update_fields=["release_date"])
        self.completed(Anime, anime[:1])
        self.log(anime[1], visibility="followers")
        self.log(anime[2], visibility="private")
        series = MediaSeries.objects.create(source="mal", series_id="franchise", media_type="anime", name="F")
        MediaSeriesItem.objects.bulk_create([MediaSeriesItem(series=series, item=item) for item in anime])
        counts = {}
        for viewer in (self.owner, self.follower, self.stranger):
            data = self.collections(viewer, media_type="anime")
            counts[viewer.username] = (data["complete_count"], data["almost_count"])
        self.assertEqual(counts, {"owner": (1, 0), "follower": (0, 1), "stranger": (0, 0)})

    def test_show_all_endpoint_pages_and_applies_the_profile_gate(self):
        for index in range(3):
            self.series(f"c{index}", 2 + index, 0)
        response = self.get(self.owner, {"media_type": "movie", "group": "complete", "page_size": 2},
                            path="/api/v1/stats/me/collections/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual([row["id"] for row in response.data["results"]], ["c2", "c1"])
        self.assertIsNotNone(response.data["next"])
        second = self.get(self.owner, {"media_type": "movie", "group": "complete", "page_size": 2, "page": 2},
                          path="/api/v1/stats/me/collections/")
        self.assertEqual([row["id"] for row in second.data["results"]], ["c0"])
        follower = self.get(self.follower, {"media_type": "movie", "group": "complete"},
                            path=self.user_path("collections"))
        self.assertEqual(follower.status_code, 200)
        self.assertEqual(follower.data["count"], 3)
        Block.objects.create(blocker=self.owner, blocked=self.stranger)
        blocked = self.get(self.stranger, {"media_type": "movie", "group": "complete"},
                           path=self.user_path("collections"))
        self.assertEqual(blocked.status_code, 404)
        bad = self.get(self.owner, {"media_type": "movie", "group": "most"}, path="/api/v1/stats/me/collections/")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("group", bad.data["error"]["fields"])

    def test_the_summary_sends_collections_on_type_tabs(self):
        self.series("done", 2, 0)
        data = self.get(self.follower, {**ALL_TIME, "media_type": "movie"}, path=self.user_path()).data
        self.assertEqual(data["collections"]["complete_count"], 1)
        self.assertIsNone(data["collections"]["in_range"])
        self.assertIsNone(self.get(self.owner, ALL_TIME).data["collections"])
        year = self.get(self.owner, {**YEAR_2025, "media_type": "movie"}).data["collections"]
        self.assertEqual(year["complete_count"], 1)
        self.assertEqual(
            year["in_range"],
            {"completed_count": 0, "completed": [], "progressed_count": 0, "progressed": []},
        )

    # Progress made in a dated range: collections.in_range.

    def members(self, name, items, *, media_type="movie", source=Sources.TMDB.value):
        """Store a released series of the given Items, in order."""
        series = MediaSeries.objects.create(source=source, series_id=name, media_type=media_type, name=name)
        Item.objects.filter(id__in=[item.id for item in items]).update(release_date=date(2020, 1, 1))
        MediaSeriesItem.objects.bulk_create([
            MediaSeriesItem(series=series, item=item, position=index) for index, item in enumerate(items, start=1)
        ])
        return series

    def in_range(self, year, viewer=None, media_type="movie"):
        scope = self.scope(viewer, start=date(year, 1, 1), end=date(year, 12, 31), media_type=media_type)
        return stats_v2_lists.build_collections(scope)["in_range"]

    def rows(self, rows):
        return [(row["id"], row["seen_in_range"]) for row in rows]

    def test_collections_completed_and_progressed_in_a_year(self):
        a = self.movies(3, "a")
        self.completed(Movie, a[:2])
        self.log(a[0], datetime(2023, 2, 1, tzinfo=UTC))
        # a[1] is Completed with no log: undated, so earlier than every dated member.
        self.log(a[2], datetime(2025, 6, 1, tzinfo=UTC))
        b = self.movies(2, "b")
        self.log(b[0], datetime(2025, 2, 1, tzinfo=UTC))
        self.log(b[1], datetime(2026, 2, 1, tzinfo=UTC))
        c = self.movies(4, "c")
        self.log(c[0], datetime(2025, 3, 1, tzinfo=UTC))
        self.log(c[1], datetime(2025, 1, 1, tzinfo=UTC))
        d = self.movies(2, "d")
        for film in d:
            self.log(film, datetime(2020, 5, 1, tzinfo=UTC))
        # Seeing a title again isn't progress.
        self.log(d[0], datetime(2025, 5, 1, tzinfo=UTC))
        e = self.movies(2, "e")
        self.completed(Movie, e)
        for name, films in (("A", a), ("B", b), ("C", c), ("D", d), ("E", e)):
            self.members(name, films)

        year = self.in_range(2025)
        self.assertEqual((year["completed_count"], year["progressed_count"]), (1, 2))
        # A became complete in 2025; B's last member was first seen in 2026.
        self.assertEqual(self.rows(year["completed"]), [("A", 1)])
        self.assertEqual(self.rows(year["progressed"]), [("C", 2), ("B", 1)])
        self.assertEqual(year["completed"][0]["completion"], {"completed_count": 3, "total_count": 3})
        self.assertEqual(year["progressed"][0]["completion"], {"completed_count": 2, "total_count": 4})
        self.assertEqual(year["completed"][0]["poster_urls"], ["https://example.com/poster.jpg"] * 3)
        next_year = self.in_range(2026)
        self.assertEqual((self.rows(next_year["completed"]), next_year["progressed"]), ([("B", 1)], []))
        self.assertEqual(self.rows(self.in_range(2020)["completed"]), [("D", 2)])
        # The lifetime groups don't change with the range and carry no seen_in_range.
        lifetime = stats_v2_lists.build_collections(self.scope(media_type="movie"))
        self.assertIsNone(lifetime["in_range"])
        dated = stats_v2_lists.build_collections(
            self.scope(start=date(2025, 1, 1), end=date(2025, 12, 31), media_type="movie"),
        )
        self.assertEqual(
            {key: dated[key] for key in ("complete_count", "almost_count", "complete", "almost")},
            {key: lifetime[key] for key in ("complete_count", "almost_count", "complete", "almost")},
        )
        self.assertTrue(all("seen_in_range" not in row for row in dated["complete"]))

    def test_in_range_rows_are_capped_at_four(self):
        for index in range(6):
            films = self.movies(2, f"s{index}-")
            for film in films:
                self.log(film, datetime(2025, 3, 1, tzinfo=UTC))
            self.members(f"S{index}", films)
        year = self.in_range(2025)
        self.assertEqual(year["completed_count"], 6)
        self.assertEqual([row["id"] for row in year["completed"]], ["S0", "S1", "S2", "S3"])

    def test_in_range_first_seen_days_are_on_the_owners_calendar(self):
        anime = [self.item("anime", f"f{index}") for index in range(2)]
        self.members("F", anime, media_type="anime", source=Sources.MAL.value)
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                DiaryEntry.objects.all().delete()
                self.set_zone(zone)
                local = ZoneInfo(zone)
                self.log(anime[0], datetime(2024, 12, 31, 23, 30, tzinfo=local))
                self.log(anime[1], datetime(2025, 1, 1, 0, 30, tzinfo=local))
                for viewer in (self.owner, self.follower):
                    year = self.in_range(2025, viewer, "anime")
                    self.assertEqual((self.rows(year["completed"]), year["progressed"]), ([("F", 1)], []))
                    before = self.in_range(2024, viewer, "anime")
                    self.assertEqual((before["completed"], self.rows(before["progressed"])), ([], [("F", 1)]))

    def test_other_viewers_count_only_the_logs_they_can_see_in_range(self):
        anime = [self.item("anime", f"p{index}") for index in range(2)]
        self.log(anime[0], datetime(2025, 2, 1, tzinfo=UTC))
        self.log(anime[1], datetime(2025, 3, 1, tzinfo=UTC), visibility="private")
        self.members("P", anime, media_type="anime", source=Sources.MAL.value)
        owner = self.in_range(2025, media_type="anime")
        self.assertEqual((self.rows(owner["completed"]), owner["progressed"]), ([("P", 2)], []))
        stranger = self.in_range(2025, self.stranger, "anime")
        self.assertEqual((stranger["completed"], self.rows(stranger["progressed"])), ([], [("P", 1)]))

    def test_show_all_pages_the_in_range_groups(self):
        for index in range(3):
            films = self.movies(2, f"s{index}-")
            for film in films:
                self.log(film, datetime(2025, 3, 1 + index, tzinfo=UTC))
            self.members(f"S{index}", films)
        path = "/api/v1/stats/me/collections/"
        params = {"media_type": "movie", "group": "completed_in_range", "start_date": "2025-01-01",
                  "end_date": "2025-12-31", "page_size": 2}
        response = self.get(self.owner, params, path=path)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 3)
        self.assertEqual(self.rows(response.data["results"]), [("S0", 2), ("S1", 2)])
        self.assertIsNotNone(response.data["next"])
        second = self.get(self.owner, {**params, "page": 2}, path=path)
        self.assertEqual(self.rows(second.data["results"]), [("S2", 2)])
        progressed = self.get(self.owner, {**params, "group": "progressed_in_range"}, path=path)
        self.assertEqual(progressed.data["count"], 0)
        self.assertEqual(self.get(self.owner, {**params, "start_date": "2024-01-01", "end_date": "2024-12-31"},
                                  path=path).data["count"], 0)
        follower = self.get(self.follower, params, path=self.user_path("collections"))
        self.assertEqual((follower.status_code, follower.data["count"]), (200, 3))
        # The lifetime groups still ignore dates.
        lifetime = self.get(self.owner, {**params, "group": "complete"}, path=path)
        self.assertEqual(lifetime.data["count"], 3)
        self.assertTrue(all("seen_in_range" not in row for row in lifetime.data["results"]))
        all_time = self.get(self.owner, {**params, "start_date": "all", "end_date": "all"}, path=path)
        self.assertEqual(all_time.status_code, 400)
        self.assertIn("date_range", all_time.data["error"]["fields"])
        bad = self.get(self.owner, {**params, "end_date": "2024-01-01"}, path=path)
        self.assertEqual(bad.status_code, 400)

    def test_in_range_query_count_is_flat(self):
        def count_queries():
            scope = self.scope(start=date(2025, 1, 1), end=date(2025, 12, 31), media_type="movie")
            with CaptureQueriesContext(connection) as queries:
                data = stats_v2_lists.build_collections(scope)
            self.assertTrue(data["in_range"]["completed"] and data["in_range"]["progressed"])
            return len(queries)

        def seed(prefix, count):
            for index in range(count):
                films = self.movies(3, f"{prefix}{index}-")
                self.log(films[0], datetime(2024, 3, 1, tzinfo=UTC))
                self.log(films[1], datetime(2025, 3, 1, tzinfo=UTC))
                self.members(f"{prefix}-done-{index}", films[:2])
                self.members(f"{prefix}-part-{index}", films)

        seed("small", 1)
        baseline = count_queries()
        seed("big", 8)
        self.assertEqual(count_queries(), baseline)
