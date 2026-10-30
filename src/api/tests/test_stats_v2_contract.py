"""Stats v2 conformance: every section's keys and types match docs/stats-contract.md.

One seeded owner with data in every type (movies, TV with season and episode logs and
ticks, anime, manga, comics, books, games, music, ratings, community ratings, genres,
languages, a ranked top2025 list, a private top2025 list, a featured list and a
MediaSeries) is requested on Global and every type tab, in All Time and a year, by the
owner, a follower and a public viewer. Each payload is checked field by field against
the contract's section shapes (STAT-301 to STAT-304), and the viewers' payloads are
compared under STAT-002 to STAT-004.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings

from api.tests.test_stats_v2_shape import (
    ALL_TIME,
    NOW,
    PRIMARY,
    YEAR_2025,
    StatsV2Base,
)
from app.models import (
    Anime,
    Book,
    Comic,
    ExternalRating,
    Game,
    GameSession,
    ItemFilterFacet,
    Manga,
    MediaSeries,
    MediaSeriesItem,
    MediaTypes,
    Movie,
    Music,
    Status,
)
from lists.featured import registry
from lists.models import CustomList, CustomListItem, FeaturedListSync
from lists.tests.featured.helpers import make_definition

MEDIA_KEYS = {
    "ref",
    "title",
    "subtitle",
    "overview",
    "image_url",
    "poster_url",
    "backdrop_url",
    "poster_aspect_ratio",
    "poster_width",
    "poster_height",
    "poster_orientation",
    "poster_accent_color",
    "release_date",
    "default_source",
    "custom_poster_url",
    "custom_backdrop_url",
    "user_state",
}
REF_KEYS = {"item_id", "source", "media_type", "media_id", "season_number", "episode_number"}
UNITS = {
    "movie": "minutes",
    "tv": "minutes",
    "anime": "minutes",
    "manga": "chapters",
    "comic": "issues",
    "game": "minutes",
    "book": "pages",
    "music": "minutes",
}
SHARED_DETAILS = [
    "current_daily_streak",
    "longest_weekly_streak",
    "current_weekly_streak",
    "rewatches",
    "reviews",
    "likes",
    "days_with_two_plus_titles",
]
TYPE_DETAILS = {
    "tv": ["episodes", "seasons_finished", "shows_finished"],
    "anime": ["episodes"],
    "book": ["reads", "did_not_finish"],
    "game": ["completions", "abandoned"],
}
# The extra diary filters each detail's drill sends (STAT-200); other details have none.
DRILL_KEYS = {
    "current_daily_streak": {"watched_from", "watched_to"},
    "longest_weekly_streak": {"watched_from", "watched_to"},
    "current_weekly_streak": {"watched_from", "watched_to"},
    "rewatches": {"repeat"},
    "reviews": {"has_review"},
    "likes": {"liked"},
    "days_with_two_plus_titles": {"multi_title_days"},
}
ALL_TIME_ONLY_DETAILS = {"seasons_finished", "shows_finished"}
TODAY_ONLY_DETAILS = {"current_daily_streak", "current_weekly_streak"}
BUCKET_BOUNDS = [
    ("0.5", "0.0", "1.4"),
    ("1.0", "1.5", "2.4"),
    ("1.5", "2.5", "3.4"),
    ("2.0", "3.5", "4.4"),
    ("2.5", "4.5", "5.4"),
    ("3.0", "5.5", "6.4"),
    ("3.5", "6.5", "7.4"),
    ("4.0", "7.5", "8.4"),
    ("4.5", "8.5", "9.4"),
    ("5.0", "9.5", "10.0"),
]
GROUP_KEYS = {
    "drama",
    "comedy",
    "action_adventure",
    "scifi_fantasy",
    "horror",
    "thriller_crime",
    "mystery",
    "romance",
    "documentary_nonfiction",
    "family_kids",
    "history_war",
    "slice_of_life",
    "sports",
    "music_musicals",
    "strategy_puzzle",
}
GENRE = ItemFilterFacet.FacetType.GENRE
THEME = ItemFilterFacet.FacetType.THEME
ORIGINAL_LANGUAGE = ItemFilterFacet.FacetType.ORIGINAL_LANGUAGE
JUNE_2025 = datetime(2025, 6, 14, tzinfo=UTC)


def _is_star_string(value):
    if not isinstance(value, str):
        return False
    whole, _dot, decimals = value.partition(".")
    return whole.lstrip("-").isdigit() and decimals.isdigit() and 1 <= len(decimals) <= 2


def _is_one_decimal(value):
    if not isinstance(value, str):
        return False
    whole, _dot, decimals = value.partition(".")
    return whole.lstrip("-").isdigit() and len(decimals) == 1 and decimals.isdigit()


def _is_day(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_month(value):
    return isinstance(value, str) and len(value) == 7 and _is_day(f"{value}-01")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


@override_settings(MUSIC_ENABLED=True)
class StatsV2ContractConformanceTests(StatsV2Base):
    """Every section, every tab, every audience, against the contract's shapes."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.definitions = []
        self.seed()
        overrides = registry.override_definitions(self.definitions)
        overrides.__enter__()
        self.addCleanup(overrides.__exit__, None, None, None)

    # Seed

    def facets(self, item, *, genres=(), themes=(), language=None):
        rows = [ItemFilterFacet(item=item, facet_type=GENRE, value=value) for value in genres]
        rows += [ItemFilterFacet(item=item, facet_type=THEME, value=value) for value in themes]
        if language:
            rows.append(ItemFilterFacet(item=item, facet_type=ORIGINAL_LANGUAGE, value=language))
        ItemFilterFacet.objects.bulk_create(rows)

    def dated(self, item, year, runtime=None, pages=None):
        item.release_date = date(year, 5, 1)
        item.release_year = year
        item.runtime_minutes = runtime
        item.total_pages = pages
        item.save(update_fields=["release_date", "release_year", "runtime_minutes", "total_pages"])
        return item

    def community(self, item, source, value, max_value, votes=None):
        ExternalRating.objects.create(
            item=item,
            rating_source=source,
            value=Decimal(str(value)),
            max_value=Decimal(str(max_value)),
            vote_count=votes,
            status="available",
            last_attempted_at=NOW,
            last_success_at=NOW,
        )

    def seed(self):
        # Movies: two logged films (one twice, rated on the stored scale), a film
        # marked watched with a current score and no log, and a release-dated film.
        self.dune = self.dated(self.item(MediaTypes.MOVIE.value, "dune", title="Dune"), 2024, runtime=166)
        self.alien = self.dated(self.item(MediaTypes.MOVIE.value, "alien", title="Alien"), 1979, runtime=117)
        self.marked = self.dated(self.item(MediaTypes.MOVIE.value, "marked", title="Marked"), 2001, runtime=100)
        Movie.objects.bulk_create([
            Movie(user=self.owner, item=self.dune, status=Status.COMPLETED.value),
            Movie(user=self.owner, item=self.alien, status=Status.COMPLETED.value),
            Movie(user=self.owner, item=self.marked, status=Status.COMPLETED.value,
                  score=Decimal("10.0"), direct_consumption=True),
        ])
        first = self.log(self.dune, rating=Decimal("9.0"))
        first.review = "Loved it"
        first.liked = True
        first.save(update_fields=["review", "liked"])
        self.log(self.dune, JUNE_2025, rating=Decimal("10.0"))
        self.log(self.alien, datetime(2025, 3, 3, tzinfo=UTC), rating=Decimal("4.0"))
        self.facets(self.dune, genres=["Science Fiction"], language="English")
        self.facets(self.alien, genres=["Horror", "Science Fiction"], language="English")
        self.community(self.dune, "letterboxd", "3.1", "5", votes=8000)
        self.community(self.alien, "letterboxd", "4.4", "5", votes=9000)

        self.seed_tv()
        self.seed_other_types()
        self.seed_lists()

    def seed_tv(self):
        # TV: a show with a show row, a season log, an episode log with its viewing,
        # a second viewing of that episode and a tick of another.
        self.show = self.dated(self.item(MediaTypes.TV.value, "show", title="Show"), 2019, runtime=45)
        self.season = self.item(MediaTypes.SEASON.value, "show", season=1, title="Show")
        episode_one = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1, title="Show")
        episode_two = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=2, title="Show")
        _tv, season_row = self.show_rows(self.show, self.season)
        episode_log = self.log(episode_one, rating=Decimal("8.0"))
        self.viewing(season_row, episode_one, watched_on=date(2025, 3, 2), kind="log", entry=episode_log)
        self.viewing(season_row, episode_one, watched_on=date(2025, 4, 2))
        self.viewing(season_row, episode_two, watched_on=date(2025, 4, 2))
        self.log(self.season, datetime(2025, 4, 2, tzinfo=UTC), rating=Decimal("9.0"))
        self.facets(self.show, genres=["Sci-Fi & Fantasy", "Drama"], language="Japanese")
        self.community(self.show, "imdb", "6.0", "10", votes=500)

    def seed_other_types(self):
        # One of each other type, logged, with tracking rows where time reads them.
        self.anime = self.dated(self.item(MediaTypes.ANIME.value, "anime", title="Anime"), 2020, runtime=24)
        Anime.objects.bulk_create([Anime(user=self.owner, item=self.anime, status=Status.COMPLETED.value, progress=12)])
        self.log(self.anime, datetime(2025, 3, 4, 18, tzinfo=UTC), rating=Decimal("7.0"))
        self.facets(self.anime, genres=["Sci-Fi"])
        self.community(self.anime, "mal", "8.6", "10", votes=1000)

        self.manga = self.dated(self.item(MediaTypes.MANGA.value, "manga", title="Manga"), 2010)
        Manga.objects.bulk_create([Manga(user=self.owner, item=self.manga, status=Status.COMPLETED.value, progress=40)])
        self.log(self.manga, rating=Decimal("6.0"))
        self.facets(self.manga, genres=["Sci-Fi", "Sci-fi"])

        self.comic = self.dated(self.item(MediaTypes.COMIC.value, "comic", title="Comic"), 1986)
        Comic.objects.bulk_create([Comic(user=self.owner, item=self.comic, status=Status.COMPLETED.value, progress=12)])
        self.log(self.comic, rating=Decimal("8.0"))

        self.book = self.dated(self.item(MediaTypes.BOOK.value, "book", title="Book"), 1954, pages=400)
        Book.objects.bulk_create([
            Book(user=self.owner, item=self.book, status=Status.COMPLETED.value, completed_manually=True),
        ])
        self.log(self.book, JUNE_2025, rating=Decimal("10.0"))
        self.facets(self.book, genres=["Fantasy"])

        self.game = self.dated(self.item(MediaTypes.GAME.value, "game", title="Game"), 2017)
        game_row = Game(user=self.owner, item=self.game, status=Status.COMPLETED.value)
        Game.objects.bulk_create([game_row])
        GameSession.objects.create(
            related_game=game_row,
            status=Status.COMPLETED.value,
            start_date=date(2025, 3, 1),
            end_date=date(2025, 3, 5),
            total_minutes=600,
            minutes_updated_on=date(2025, 3, 5),
        )
        self.log(self.game, datetime(2025, 3, 5, tzinfo=UTC), rating=Decimal("8.5"))
        self.facets(self.game, genres=["Puzzle"], themes=["Science fiction"])

        self.album = self.dated(self.item(MediaTypes.MUSIC.value, "album", title="Album"), 1991, runtime=50)
        Music.objects.bulk_create([Music(user=self.owner, item=self.album, status=Status.COMPLETED.value)])
        self.log(self.album, datetime(2025, 3, 6, tzinfo=UTC), rating=Decimal("9.5"))
        self.facets(self.album, genres=["shoegaze"])

    def seed_lists(self):
        # Top: a public ranked mixed list feeds Global; a private ranked movie list
        # feeds the movie tab for the owner only (S-12, S-13).
        self.mixed_list = self.make_list("My 2025", [self.dune, self.show], tags=["top2025"])
        self.private_list = self.make_list("Private films", [self.alien, self.dune], tags=["Top 2025"],
                                           visibility="private")

        # A featured canon list with two seen films (STAT-213).
        curator = self.stranger
        self.ring = self.make_list("Canon", [self.dune, self.alien, self.marked], owner=curator, tags=[],
                                   featured=True)
        FeaturedListSync.objects.create(key="canon-films", custom_list=self.ring, fingerprint="f1")
        self.definitions.append(make_definition(key="canon-films", name="Canon", kind="canon"))

        # A movie series with 2 of 3 released members seen (STAT-214).
        self.sequel = self.dated(self.item(MediaTypes.MOVIE.value, "dune-3", title="Dune 3"), 2024)
        series = MediaSeries.objects.create(source="tmdb", series_id="dune", media_type="movie", name="Dune")
        MediaSeriesItem.objects.bulk_create([
            MediaSeriesItem(series=series, item=item, position=index)
            for index, item in enumerate([self.dune, self.marked, self.sequel], start=1)
        ])

    def make_list(self, name, items, *, owner=None, tags=(), visibility="public", featured=False):
        custom_list = CustomList.objects.create(
            owner=owner or self.owner,
            name=name,
            tags=list(tags),
            is_ranked=True,
            visibility=visibility,
            is_featured=featured,
        )
        for position, item in enumerate(items, start=1):
            CustomListItem.objects.create(custom_list=custom_list, item=item, position=position)
        return custom_list

    # Requests

    def payload(self, viewer, params, tab=None):
        path = "/api/v1/stats/me/summary/" if viewer == self.owner else self.user_path()
        query = {**params, **({"media_type": tab} if tab else {})}
        response = self.get(viewer, query, path=path)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def viewers(self):
        return ((self.owner, "owner"), (self.follower, "follower"), (self.stranger, "public"))

    # Tests

    def test_every_section_matches_the_contract_for_every_tab_range_and_audience(self):
        for params in (ALL_TIME, YEAR_2025):
            for tab in [None, *PRIMARY]:
                for viewer, audience in self.viewers():
                    with self.subTest(range=params["start_date"], tab=tab, audience=audience):
                        data = self.payload(viewer, params, tab)
                        self.assertEqual(data["audience"], audience)
                        self.check_payload(data, tab)

    def test_the_seed_fills_every_section_on_global(self):
        # The conformance test above only means something when sections aren't empty.
        data = self.payload(self.owner, YEAR_2025)
        self.assertGreater(data["header"]["titles"], 0)
        # Anime, manga and comic time in a year comes from progress history, which
        # bulk-created rows don't have; All Time reads their progress.
        self.assertEqual({row["media_type"] for row in data["time"]["rows"]}, set(PRIMARY) - {"anime", "manga", "comic"})
        all_time_rows = self.payload(self.owner, ALL_TIME)["time"]["rows"]
        self.assertEqual([row["media_type"] for row in all_time_rows], PRIMARY)
        self.assertEqual(data["top"]["source"], "list")
        self.assertTrue(data["highest_rated"]["items"])
        self.assertIsNotNone(data["activity"]["busiest_day"])
        self.assertGreater(data["ratings"]["rated"], 0)
        self.assertTrue(data["rated_vs_everyone"]["higher"])
        self.assertTrue(data["rated_vs_everyone"]["lower"])
        self.assertTrue(data["release_years"]["rows"])
        self.assertTrue(data["genres"]["rows"])
        self.assertEqual(data["genres"]["music"], [{"name": "shoegaze", "values": ["shoegaze"], "titles": 1}])
        self.assertTrue(data["languages"]["rows"])
        self.assertEqual([ring["id"] for ring in data["list_rings"]], [self.ring.id])
        self.assertTrue(data["most_logged"]["items"])
        self.assertTrue(data["milestones"])
        self.assertEqual([ring["seen_in_range"] for ring in data["list_rings"]], [2])
        movie_tab = self.payload(self.owner, ALL_TIME, "movie")
        self.assertEqual(movie_tab["collections"]["almost_count"], 1)
        movie_year = self.payload(self.owner, YEAR_2025, "movie")["collections"]["in_range"]
        self.assertEqual((movie_year["completed_count"], movie_year["progressed_count"]), (0, 1))
        self.assertEqual(movie_year["progressed"][0]["seen_in_range"], 1)
        tv_tab = self.payload(self.owner, ALL_TIME, "tv")
        self.assertEqual(
            [detail["key"] for detail in tv_tab["header"]["details"]][-3:],
            ["episodes", "seasons_finished", "shows_finished"],
        )

    def test_followers_and_public_viewers_see_the_owners_stats(self):
        # STAT-002: with every log visible, the only differences are the audience,
        # Top's owner-only fields, Top from a private list (STAT-003, STAT-004) and
        # the owner-only drills in the header's details (STAT-200).
        for params in (ALL_TIME, YEAR_2025):
            for tab in [None, *PRIMARY]:
                owner = self.payload(self.owner, params, tab)
                self.assertTrue(any(detail["drill"] for detail in owner["header"]["details"]))
                for detail in owner["header"]["details"]:
                    detail["drill"] = None
                skipped = {"audience", "top"}
                if owner["top"]["hint_private"]:
                    # Only the owner's Top comes from the private list, so only the
                    # owner's Highest rated leaves its titles out (STAT-205).
                    skipped.add("highest_rated")
                for viewer, audience in self.viewers()[1:]:
                    with self.subTest(range=params["start_date"], tab=tab, audience=audience):
                        other = self.payload(viewer, params, tab)
                        for key in set(owner) - skipped:
                            self.assertEqual(other[key], owner[key], key)
                        self.assertIsNone(other["top"]["tag_hint"])
                        self.assert_false(other["top"]["hint_private"])
                        if owner["top"]["source"] == "list" and not owner["top"]["hint_private"]:
                            self.assertEqual(other["top"]["list"], owner["top"]["list"])
                            self.assertEqual(other["top"]["items"], owner["top"]["items"])

    def test_top_follows_list_visibility_and_owner_only_hints(self):
        # S-12 and S-13.
        owner_movies = self.payload(self.owner, YEAR_2025, "movie")["top"]
        self.assertEqual(owner_movies["source"], "list")
        self.assertEqual(owner_movies["list"], {"id": self.private_list.id, "name": "Private films", "total": 2})
        self.assertTrue(owner_movies["hint_private"])
        self.assertIsInstance(owner_movies["hint_private"], bool)
        self.assertEqual([item["rank"] for item in owner_movies["items"]], [1, 2])
        self.assertEqual(owner_movies["items"][0]["media"]["ref"]["item_id"], self.alien.id)
        self.assertEqual(owner_movies["items"][0]["stars"], "2.0")

        follower_movies = self.payload(self.follower, YEAR_2025, "movie")["top"]
        self.assertEqual(follower_movies["source"], "highest_rated")
        self.assertIsNone(follower_movies["list"])
        self.assertIsNone(follower_movies["tag_hint"])
        self.assertEqual(follower_movies["tag"], "top2025")

        for viewer, _audience in self.viewers():
            top = self.payload(viewer, YEAR_2025)["top"]
            self.assertEqual(top["list"], {"id": self.mixed_list.id, "name": "My 2025", "total": 2})
            self.assert_false(top["hint_private"])

        anime = self.payload(self.owner, YEAR_2025, "anime")["top"]
        self.assertEqual(anime["source"], "highest_rated")
        self.assertEqual(anime["tag_hint"], "Tag a ranked anime list top2025")

    # Shape checks

    def check_payload(self, data, tab):
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["media_type"], tab)
        self.check_keys(data["range"], {"start_date", "end_date", "timezone", "is_all_time"}, "range")
        self.assertIsInstance(data["range"]["is_all_time"], bool)
        self.assertIsInstance(data["range"]["timezone"], str)
        if data["range"]["is_all_time"]:
            self.assertEqual((data["range"]["start_date"], data["range"]["end_date"]), (None, None))
        else:
            self.assertTrue(_is_day(data["range"]["start_date"]) and _is_day(data["range"]["end_date"]))
        self.check_keys(data["years"], {"first", "current"}, "years")
        self.assertTrue(data["years"]["first"] is None or _is_int(data["years"]["first"]))
        self.assertTrue(_is_int(data["years"]["current"]))
        self.assertEqual([row["media_type"] for row in data["media_types"]], PRIMARY)
        for row in data["media_types"]:
            self.check_keys(row, {"media_type", "titles", "logs"}, "media_types[]")
            self.assertTrue(_is_int(row["titles"]) and _is_int(row["logs"]))

        all_time = data["range"]["is_all_time"]
        self.check_header(data["header"], tab, all_time, data["audience"])
        self.check_time(data["time"], tab)
        self.check_top(data["top"], data["audience"])
        self.check_highest_rated(data["highest_rated"])
        self.check_activity(data["activity"], data["range"])
        self.check_ratings(data["ratings"])
        self.check_rated_vs_everyone(data["rated_vs_everyone"])
        self.check_release_years(data["release_years"])
        self.check_genres(data["genres"], tab)
        if tab in (None, "movie", "tv"):
            self.check_languages(data["languages"], tab)
        else:
            self.assertIsNone(data["languages"])
        self.check_rings(data["list_rings"], tab, all_time)
        if tab in ("movie", "game", "book", "anime"):
            self.check_collections(data["collections"], tab, all_time)
        else:
            self.assertIsNone(data["collections"])
        self.check_most_logged(data["most_logged"], tab)
        self.check_milestones(data["milestones"], tab)

    def assert_false(self, value):
        self.assertIsInstance(value, bool)
        self.assertFalse(value)

    def check_keys(self, value, keys, where):
        self.assertIsInstance(value, dict, where)
        self.assertEqual(set(value), set(keys), where)

    def check_media(self, media, where):
        self.check_keys(media, MEDIA_KEYS, where)
        self.check_keys(media["ref"], REF_KEYS, f"{where}.ref")
        self.assertTrue(_is_int(media["ref"]["item_id"]), where)
        self.assertIsNone(media["user_state"], where)

    def check_stars(self, value, where, *, nullable=True):
        if value is None and nullable:
            return
        self.assertTrue(_is_star_string(value), f"{where} is {value!r}")

    def check_by_type(self, by_type, where, tab):
        self.assertIsInstance(by_type, dict, where)
        for media_type, count in by_type.items():
            self.assertIn(media_type, PRIMARY, where)
            self.assertTrue(_is_int(count) and count > 0, where)
            if tab is not None:
                self.assertEqual(media_type, tab, where)

    def check_coverage(self, coverage, where):
        self.check_keys(coverage, {"known", "total"}, where)
        self.assertTrue(_is_int(coverage["known"]) and _is_int(coverage["total"]))
        self.assertLessEqual(coverage["known"], coverage["total"], where)

    def check_header(self, header, tab, all_time, audience):
        self.check_keys(
            header,
            {"titles", "logs", "active_days", "longest_daily_streak", "longest_daily_streak_range", "details"},
            "header",
        )
        for key in ("titles", "logs", "active_days", "longest_daily_streak"):
            self.assertTrue(_is_int(header[key]), key)
        streak_range = header["longest_daily_streak_range"]
        if header["longest_daily_streak"] == 0:
            self.assertIsNone(streak_range)
        else:
            self.check_keys(streak_range, {"start", "end"}, "longest_daily_streak_range")
            start, end = date.fromisoformat(streak_range["start"]), date.fromisoformat(streak_range["end"])
            self.assertEqual((end - start).days + 1, header["longest_daily_streak"])
        allowed = SHARED_DETAILS + TYPE_DETAILS.get(tab, [])
        keys = [detail["key"] for detail in header["details"]]
        self.assertEqual(keys, [key for key in allowed if key in keys], "details order")
        expected = [
            key for key in allowed
            if (all_time or key not in ALL_TIME_ONLY_DETAILS) and (all_time or key not in TODAY_ONLY_DETAILS)
        ]
        # Both ranges tested here are All Time (includes today) and 2025 (doesn't).
        self.assertEqual(keys, expected, "details present")
        for detail in header["details"]:
            self.check_keys(detail, {"key", "label", "value", "unit", "approximate", "drill"}, "details[]")
            self.assertIsInstance(detail["label"], str)
            self.assertTrue(_is_int(detail["value"]), detail["key"])
            self.assertIn(detail["unit"], ("day", "week", None))
            self.assertIsInstance(detail["approximate"], bool)
            drill = detail["drill"]
            # Streaks have no drill at 0; other drills don't depend on the value.
            zero_streak = detail["unit"] is not None and detail["value"] == 0
            if audience != "owner" or detail["key"] not in DRILL_KEYS or zero_streak:
                self.assertIsNone(drill, detail["key"])
            else:
                self.assertEqual(set(drill), DRILL_KEYS[detail["key"]], detail["key"])
                self.assertTrue(all(isinstance(value, str) for value in drill.values()), detail["key"])

    def check_time(self, time, tab):
        self.check_keys(time, {"rows", "hours_total"}, "time")
        order = [row["media_type"] for row in time["rows"]]
        self.assertEqual(order, [media_type for media_type in PRIMARY if media_type in order])
        for row in time["rows"]:
            self.check_keys(row, {"media_type", "unit", "value", "known", "total", "approximate"}, "time.rows[]")
            self.assertEqual(row["unit"], UNITS[row["media_type"]])
            for key in ("value", "known", "total"):
                self.assertTrue(_is_int(row[key]), key)
            self.assertGreater(row["total"], 0)
            self.assertIsInstance(row["approximate"], bool)
            if tab is not None:
                self.assertEqual(row["media_type"], tab)
        if tab is None:
            hours = time["hours_total"]
            self.check_keys(hours, {"minutes", "media_types", "leaves_out", "approximate"}, "hours_total")
            self.assertTrue(_is_int(hours["minutes"]))
            self.assertEqual(hours["media_types"], ["movie", "tv", "anime", "game", "music"])
            self.assertEqual(hours["leaves_out"], ["manga", "book", "comic"])
            self.assertIsInstance(hours["approximate"], bool)
        else:
            self.assertIsNone(time["hours_total"])

    def check_top(self, top, audience):
        self.check_keys(top, {"source", "tag", "tag_hint", "hint_private", "list", "items"}, "top")
        self.assertIn(top["source"], ("list", "highest_rated"))
        self.assertTrue(top["tag"] is None or isinstance(top["tag"], str))
        self.assertIsInstance(top["hint_private"], bool)
        if audience != "owner":
            self.assertIsNone(top["tag_hint"])
            self.assert_false(top["hint_private"])
        if top["source"] == "list":
            self.check_keys(top["list"], {"id", "name", "total"}, "top.list")
            self.assertTrue(_is_int(top["list"]["id"]) and _is_int(top["list"]["total"]))
            self.assertIsNone(top["tag_hint"])
        else:
            self.assertIsNone(top["list"])
            self.assert_false(top["hint_private"])
        self.assertLessEqual(len(top["items"]), 10)
        self.assertEqual([item["rank"] for item in top["items"]], list(range(1, len(top["items"]) + 1)))
        for item in top["items"]:
            self.check_keys(item, {"rank", "media", "stars"}, "top.items[]")
            self.check_media(item["media"], "top.items[].media")
            self.check_stars(item["stars"], "top.items[].stars")

    def check_highest_rated(self, section):
        self.check_keys(section, {"total", "items"}, "highest_rated")
        self.assertTrue(_is_int(section["total"]))
        self.assertLessEqual(len(section["items"]), 12)
        self.assertGreaterEqual(section["total"], len(section["items"]))
        for item in section["items"]:
            self.check_keys(item, {"media", "stars", "day"}, "highest_rated.items[]")
            self.check_media(item["media"], "highest_rated.items[].media")
            self.assertNotEqual(item["media"]["ref"]["media_type"], "episode")
            self.check_stars(item["stars"], "highest_rated.items[].stars", nullable=False)
            self.assertTrue(item["day"] is None or _is_day(item["day"]))

    def check_activity(self, activity, range_payload):
        self.check_keys(activity, {"months", "weekdays", "week_starts_on", "busiest_day", "busiest_month"}, "activity")
        for month in activity["months"]:
            self.check_keys(month, {"month", "count", "by_type"}, "activity.months[]")
            self.assertTrue(_is_month(month["month"]))
            self.assertTrue(_is_int(month["count"]))
            self.assertEqual(sum(month["by_type"].values()), month["count"])
        if range_payload["start_date"] == "2025-01-01":
            self.assertEqual(len(activity["months"]), 12)
        self.assertEqual(len(activity["weekdays"]), 7)
        self.assertTrue(all(_is_int(count) for count in activity["weekdays"]))
        self.assertIn(activity["week_starts_on"], ("monday", "sunday"))
        if activity["busiest_day"] is None:
            self.assertIsNone(activity["busiest_month"])
        else:
            self.check_keys(activity["busiest_day"], {"date", "count"}, "busiest_day")
            self.assertTrue(_is_day(activity["busiest_day"]["date"]))
            self.check_keys(activity["busiest_month"], {"month", "count"}, "busiest_month")
            self.assertTrue(_is_month(activity["busiest_month"]["month"]))

    def check_ratings(self, ratings):
        self.check_keys(ratings, {"average_stars", "rated", "distribution"}, "ratings")
        self.assertTrue(_is_int(ratings["rated"]))
        if ratings["rated"] == 0:
            self.assertIsNone(ratings["average_stars"])
        else:
            self.check_stars(ratings["average_stars"], "ratings.average_stars", nullable=False)
        self.assertEqual(
            [(row["stars"], row["rating_min"], row["rating_max"]) for row in ratings["distribution"]],
            BUCKET_BOUNDS,
        )
        for row in ratings["distribution"]:
            self.check_keys(row, {"stars", "count", "rating_min", "rating_max", "by_type"}, "distribution[]")
            self.assertTrue(_is_int(row["count"]))
            self.assertEqual(sum(row["by_type"].values()), row["count"])
        self.assertEqual(sum(row["count"] for row in ratings["distribution"]), ratings["rated"])

    def check_rated_vs_everyone(self, section):
        self.check_keys(section, {"sources", "higher", "lower"}, "rated_vs_everyone")
        for source in section["sources"]:
            self.check_keys(source, {"media_type", "source", "label"}, "sources[]")
        used = {(source["media_type"], source["source"]) for source in section["sources"]}
        for key in ("higher", "lower"):
            self.assertLessEqual(len(section[key]), 5)
            for row in section[key]:
                self.check_keys(row, {"media", "stars", "everyone_stars", "difference", "source"}, key)
                self.check_media(row["media"], f"{key}[].media")
                self.check_stars(row["stars"], f"{key}[].stars", nullable=False)
                self.assertTrue(_is_one_decimal(row["everyone_stars"]), row["everyone_stars"])
                self.assertTrue(_is_one_decimal(row["difference"]), row["difference"])
                difference = Decimal(row["difference"])
                self.assertTrue(difference > 0 if key == "higher" else difference < 0, row)
                self.assertIn(row["source"], {source for _type, source in used})

    def check_release_years(self, section):
        self.check_keys(section, {"rows", "coverage", "new_vs_older"}, "release_years")
        years = [row["year"] for row in section["rows"]]
        self.assertEqual(years, sorted(set(years)))
        for row in section["rows"]:
            self.check_keys(row, {"year", "titles", "logs", "rated", "average_stars", "by_type"}, "release_years.rows[]")
            for key in ("year", "titles", "logs", "rated"):
                self.assertTrue(_is_int(row[key]), key)
            self.check_stars(row["average_stars"], "release_years.rows[].average_stars")
            self.assertEqual(sum(row["by_type"].values()), row["titles"])
        self.check_coverage(section["coverage"], "release_years.coverage")
        self.check_keys(section["new_vs_older"], {"new", "recent", "older"}, "new_vs_older")

    def check_genres(self, genres, tab):
        self.check_keys(genres, {"rows", "total", "coverage", "music"}, "genres")
        self.assertTrue(_is_int(genres["total"]))
        self.assertLessEqual(len(genres["rows"]), 50)
        for row in genres["rows"]:
            self.check_keys(row, {"key", "name", "values", "titles", "rated", "average_stars", "by_type"}, "genres.rows[]")
            if tab is None:
                self.assertIn(row["key"], GROUP_KEYS)
                self.assertIsNone(row["values"])
                self.assertNotIn("music", row["by_type"])
            else:
                self.assertIsNone(row["key"])
                self.assertIsInstance(row["values"], list)
                self.assertTrue(row["values"] and all(isinstance(value, str) for value in row["values"]))
            self.assertIsInstance(row["name"], str)
            self.assertTrue(_is_int(row["titles"]) and _is_int(row["rated"]))
            self.check_stars(row["average_stars"], "genres.rows[].average_stars")
            self.check_by_type(row["by_type"], "genres.rows[].by_type", tab)
        self.check_coverage(genres["coverage"], "genres.coverage")
        if tab is None:
            self.assertLessEqual(len(genres["music"]), 5)
            for row in genres["music"]:
                self.check_keys(row, {"name", "values", "titles"}, "genres.music[]")
        else:
            self.assertEqual(genres["music"], [])

    def check_languages(self, languages, tab):
        self.check_keys(languages, {"rows", "total", "coverage", "media_types"}, "languages")
        self.assertEqual(languages["media_types"], ["movie", "tv"] if tab is None else [tab])
        for row in languages["rows"]:
            self.check_keys(row, {"name", "titles", "rated", "average_stars", "by_type"}, "languages.rows[]")
            self.check_stars(row["average_stars"], "languages.rows[].average_stars")
            self.check_by_type(row["by_type"], "languages.rows[].by_type", tab)
        self.check_coverage(languages["coverage"], "languages.coverage")

    def check_rings(self, rings, tab, all_time):
        self.assertIsInstance(rings, list)
        for ring in rings:
            self.check_keys(ring, {"id", "name", "media_type", "kind", "seen", "total", "seen_in_range"}, "list_rings[]")
            self.assertTrue(_is_int(ring["id"]) and _is_int(ring["seen"]) and _is_int(ring["total"]))
            if all_time:
                self.assertIsNone(ring["seen_in_range"])
            else:
                self.assertTrue(_is_int(ring["seen_in_range"]))
                self.assertTrue(1 <= ring["seen_in_range"] <= ring["seen"])
            if tab is not None:
                self.assertEqual(ring["media_type"], tab)

    def check_collections(self, collections, tab, all_time):
        self.check_keys(
            collections,
            {"complete_count", "almost_count", "complete", "almost", "in_range"},
            "collections",
        )
        summary_keys = {"id", "source", "media_type", "name", "item_count", "poster_urls", "completion"}
        groups = [(collections, "complete", summary_keys), (collections, "almost", summary_keys)]
        if all_time:
            self.assertIsNone(collections["in_range"])
        else:
            in_range = collections["in_range"]
            self.check_keys(in_range, {"completed_count", "completed", "progressed_count", "progressed"}, "in_range")
            self.assertTrue(_is_int(in_range["completed_count"]) and _is_int(in_range["progressed_count"]))
            groups += [(in_range, key, summary_keys | {"seen_in_range"}) for key in ("completed", "progressed")]
        for parent, key, keys in groups:
            self.assertLessEqual(len(parent[key]), 4)
            for row in parent[key]:
                self.check_keys(row, keys, f"collections.{key}[]")
                self.assertIsInstance(row["id"], str)
                self.assertEqual(row["media_type"], tab)
                self.assertLessEqual(len(row["poster_urls"]), 3)
                self.check_keys(row["completion"], {"completed_count", "total_count"}, "completion")
                if "seen_in_range" in keys:
                    self.assertTrue(1 <= row["seen_in_range"] <= row["completion"]["completed_count"])

    def check_most_logged(self, section, tab):
        self.check_keys(section, {"label", "total", "items"}, "most_logged")
        labels = {
            None: "Most logged",
            "movie": "Most watched",
            "tv": "Most watched",
            "anime": "Most watched",
            "book": "Most read",
            "manga": "Most read",
            "comic": "Most read",
            "game": "Most played",
            "music": "Most listened",
        }
        self.assertEqual(section["label"], labels[tab])
        self.assertLessEqual(len(section["items"]), 12)
        for item in section["items"]:
            self.check_keys(item, {"media", "times"}, "most_logged.items[]")
            self.check_media(item["media"], "most_logged.items[].media")
            self.assertNotIn(item["media"]["ref"]["media_type"], ("season", "episode"))
            self.assertGreaterEqual(item["times"], 2)

    def check_milestones(self, milestones, tab):
        self.assertEqual([row["number"] for row in milestones], sorted(row["number"] for row in milestones))
        for row in milestones:
            self.check_keys(row, {"number", "day", "entry_id", "media_type", "media"}, "milestones[]")
            self.assertTrue(_is_day(row["day"]))
            self.assertTrue(_is_int(row["entry_id"]))
            self.check_media(row["media"], "milestones[].media")
            if tab is not None:
                self.assertEqual(row["media_type"], tab)
