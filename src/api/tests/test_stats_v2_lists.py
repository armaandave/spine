"""Stats v2 Top, list progress rings and collections (docs/stats-contract.md).

STAT-206 to STAT-208 (Top from a tagged ranked list, else Highest rated), STAT-213
(rings) and STAT-214 (collections), with STAT-002 to STAT-004 for other viewers.
Acceptance scenarios S-12 to S-16, S-23 and S-24.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from api.services import stats_v2_lists
from api.services.stats_scope import StatsScope
from api.tests.test_stats_v2_shape import ALL_TIME, YEAR_2024, YEAR_2025, StatsV2Base
from app.models import (
    Anime,
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
                          "kind": "award", "seen": 3, "total": 4})
        self.assertEqual((by_id[manual.id]["kind"], by_id[manual.id]["media_type"]), ("editorial", "movie"))
        # The manual list is 3 of 3: the highest share comes first.
        self.assertEqual(rings[0]["id"], manual.id)

    def test_rings_follow_the_tab_and_ignore_the_range(self):
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
        with registry.override_definitions(self.definitions):
            year = stats_v2_lists.build_list_rings(
                self.scope(start=date(2020, 1, 1), end=date(2020, 12, 31)),
            )
        self.assertEqual({ring["id"] for ring in year}, {movie_ring.id, game_ring.id, mixed.id})
        self.assertIsNone(next(ring for ring in year if ring["id"] == mixed.id)["media_type"])

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
                data = self.get(viewer, YEAR_2025, path=path).data
                self.assertEqual([r["id"] for r in data["list_rings"]], [ring.id])


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
        self.assertIsNone(self.get(self.owner, ALL_TIME).data["collections"])
