"""Stats v2 ratings sections (docs/stats-contract.md).

STAT-204 (ratings chart), STAT-205 and STAT-109 (highest rated), STAT-217 (rated
higher than most) and STAT-215 (most logged), with their show-all endpoints (STAT-310).
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from api.services import stats_v2_ratings as ratings
from api.services.stats_scope import StatsScope
from api.tests.test_stats_v2_shape import (
    ALL_TIME,
    AUCKLAND,
    LOS_ANGELES,
    NEW_YORK,
    NOW,
    YEAR_2025,
    StatsV2Base,
)
from app.models import (
    Anime,
    ExternalRating,
    MediaTypes,
    Movie,
    Season,
    Status,
)

MAY_2025 = datetime(2025, 5, 4, tzinfo=UTC)
JUN_2025 = datetime(2025, 6, 1, tzinfo=UTC)
JAN_2024 = datetime(2024, 1, 10, tzinfo=UTC)
MAR_2025_OLD = datetime(2025, 3, 2, tzinfo=UTC)


class RatingsBase(StatsV2Base):
    """Helpers for ratings data."""

    def movie_row(self, item, *, score=None, user=None):
        row = Movie(user=user or self.owner, item=item, status=Status.COMPLETED.value, score=score)
        Movie.objects.bulk_create([row])
        return row

    def community(self, item, source, value, max_value, *, votes=None, status="available"):
        return ExternalRating.objects.create(
            item=item,
            rating_source=source,
            value=Decimal(str(value)),
            max_value=Decimal(str(max_value)),
            vote_count=votes,
            status=status,
            last_attempted_at=NOW,
            last_success_at=NOW,
        )

    def stats(self, user, params, *, path=None):
        response = self.get(user, params, path=path or "/api/v1/stats/me/summary/")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def ids(self, rows):
        return [row["media"]["ref"]["item_id"] for row in rows]


class RatingsChartTests(RatingsBase):
    """STAT-204: rated visible logs in ten stored-scale buckets."""

    def test_stored_values_land_in_their_buckets_and_bounds_round_trip_through_the_diary(self):
        # S-09: 0.0, 7.4, 7.5 and 10 land in 0.5, 3.5, 4.0 and 5.0 stars.
        stored = {"0.0": "0.5", "7.4": "3.5", "7.5": "4.0", "10.0": "5.0"}
        entries = {}
        for index, value in enumerate(stored):
            item = self.item(MediaTypes.MOVIE.value, f"m{index}")
            entries[value] = self.log(item, rating=Decimal(value))
        data = self.stats(self.owner, YEAR_2025)["ratings"]
        self.assertEqual(data["rated"], 4)
        counts = {bucket["stars"]: bucket["count"] for bucket in data["distribution"]}
        self.assertEqual({key: value for key, value in counts.items() if value}, dict.fromkeys(stored.values(), 1))
        client = self.client_for(self.owner)
        for bucket in data["distribution"]:
            with self.subTest(stars=bucket["stars"]):
                response = client.get(
                    "/api/v1/diary/",
                    {"rating_min": bucket["rating_min"], "rating_max": bucket["rating_max"], "page_size": 100},
                )
                self.assertEqual(response.status_code, 200)
                expected = sorted(
                    entry.id for value, entry in entries.items() if stored[value] == bucket["stars"]
                )
                self.assertEqual(sorted(row["id"] for row in response.data["results"]), expected)
                self.assertEqual(bucket["count"], len(expected))

    def test_bounds_are_the_contract_table(self):
        data = self.stats(self.owner, ALL_TIME)["ratings"]
        self.assertEqual(
            [(bucket["rating_min"], bucket["rating_max"]) for bucket in data["distribution"]],
            [
                ("0.0", "1.4"),
                ("1.5", "2.4"),
                ("2.5", "3.4"),
                ("3.5", "4.4"),
                ("4.5", "5.4"),
                ("5.5", "6.4"),
                ("6.5", "7.4"),
                ("7.5", "8.4"),
                ("8.5", "9.4"),
                ("9.5", "10.0"),
            ],
        )
        self.assertEqual({bucket["count"] for bucket in data["distribution"]}, {0})
        self.assertIsNone(data["average_stars"])

    def test_average_is_in_stars_and_rounds_half_up(self):
        for index, value in enumerate(("0.0", "7.4", "7.5", "10.0")):
            self.log(self.item(MediaTypes.MOVIE.value, f"m{index}"), rating=Decimal(value))
        # (0 + 7.4 + 7.5 + 10) / 4 = 6.225 stored, 3.1125 stars.
        self.assertEqual(self.stats(self.owner, YEAR_2025)["ratings"]["average_stars"], "3.11")

    def test_by_type_folds_tv_rows_and_a_type_tab_counts_only_its_bucket(self):
        self.log(self.item(MediaTypes.MOVIE.value, "m"), rating=Decimal("7.0"))
        self.log(self.item(MediaTypes.SEASON.value, "show", season=1), rating=Decimal("7.0"))
        self.log(self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1), rating=Decimal("6.6"))
        self.log(self.item(MediaTypes.BOOK.value, "b"))  # unrated: not counted
        data = self.stats(self.owner, YEAR_2025)["ratings"]
        bucket = next(row for row in data["distribution"] if row["stars"] == "3.5")
        self.assertEqual(bucket["by_type"], {"movie": 1, "tv": 2})
        self.assertEqual(data["rated"], 3)
        tv_tab = self.stats(self.owner, {**YEAR_2025, "media_type": "tv"})["ratings"]
        self.assertEqual(tv_tab["rated"], 2)
        self.assertEqual(next(row for row in tv_tab["distribution"] if row["stars"] == "3.5")["by_type"], {"tv": 2})

    def test_other_viewers_count_only_visible_logs(self):
        self.log(self.item(MediaTypes.TV.value, "show"), rating=Decimal("8.0"), visibility="private")
        self.log(self.item(MediaTypes.ANIME.value, "a"), rating=Decimal("6.0"), visibility="followers")
        owner = self.stats(self.owner, YEAR_2025)["ratings"]
        follower = self.stats(self.follower, YEAR_2025, path=self.user_path())["ratings"]
        stranger = self.stats(self.stranger, YEAR_2025, path=self.user_path())["ratings"]
        self.assertEqual((owner["rated"], follower["rated"], stranger["rated"]), (2, 1, 0))
        self.assertEqual(follower["average_stars"], "3.0")

    def test_the_range_uses_each_logs_day(self):
        self.log(self.item(MediaTypes.MOVIE.value, "in"), rating=Decimal("8.0"))
        self.log(self.item(MediaTypes.MOVIE.value, "out"), JAN_2024, rating=Decimal("2.0"))
        self.assertEqual(self.stats(self.owner, YEAR_2025)["ratings"]["rated"], 1)
        self.assertEqual(self.stats(self.owner, ALL_TIME)["ratings"]["rated"], 2)


class HighestRatedTests(RatingsBase):
    """STAT-205 and STAT-109: Items by title rating, on the stored scale."""

    def test_mixed_types_sort_on_stored_values(self):
        # The original bug: TV's 0-10 and movies' stars were compared as they were sent.
        movie = self.item(MediaTypes.MOVIE.value, "movie")
        show = self.item(MediaTypes.TV.value, "show")
        anime = self.item(MediaTypes.ANIME.value, "anime")
        book = self.item(MediaTypes.BOOK.value, "book")
        self.log(movie, rating=Decimal("8.0"))
        self.log(show, rating=Decimal("9.0"))
        self.log(anime, rating=Decimal("10.0"))
        self.log(book, rating=Decimal("3.0"))
        data = self.stats(self.owner, YEAR_2025)["highest_rated"]
        self.assertEqual(self.ids(data["items"]), [anime.id, show.id, movie.id, book.id])
        self.assertEqual([row["stars"] for row in data["items"]], ["5.0", "4.5", "4.0", "1.5"])
        self.assertEqual(data["total"], 4)
        self.assertEqual(data["items"][0]["day"], "2025-03-02")

    def test_the_newest_rated_log_is_the_title_rating(self):
        movie = self.item(MediaTypes.MOVIE.value, "movie")
        self.log(movie, MAR_2025_OLD, rating=Decimal("10.0"))
        newest = self.log(movie, JUN_2025, rating=Decimal("4.0"))
        self.log(movie, datetime(2025, 7, 1, tzinfo=UTC))  # unrated, newer: ignored
        same_day = self.log(movie, JUN_2025, rating=Decimal("6.0"))
        self.assertGreater(same_day.id, newest.id)
        row = self.stats(self.owner, YEAR_2025)["highest_rated"]["items"][0]
        self.assertEqual((row["stars"], row["day"]), ("3.0", "2025-06-01"))
        # In a range, only that range's logs count.
        spring = {"schema": "2", "start_date": "2025-01-01", "end_date": "2025-05-31"}
        self.assertEqual(self.stats(self.owner, spring)["highest_rated"]["items"][0]["stars"], "5.0")

    def test_ties_go_to_the_newest_day_then_logged_rows_then_item_id(self):
        old = self.item(MediaTypes.MOVIE.value, "old")
        new = self.item(MediaTypes.MOVIE.value, "new")
        scored = self.item(MediaTypes.MOVIE.value, "scored")
        first = self.item(MediaTypes.GAME.value, "g1")
        second = self.item(MediaTypes.GAME.value, "g2")
        self.log(old, JAN_2024, rating=Decimal("8.0"))
        self.log(new, JUN_2025, rating=Decimal("8.0"))
        self.movie_row(scored, score=Decimal("8.0"))
        self.log(first, MAY_2025, rating=Decimal("8.0"))
        self.log(second, MAY_2025, rating=Decimal("8.0"))
        items = self.stats(self.owner, ALL_TIME)["highest_rated"]["items"]
        self.assertEqual(self.ids(items), [new.id, first.id, second.id, old.id, scored.id])
        self.assertEqual([row["day"] for row in items], ["2025-06-01", "2025-05-04", "2025-05-04", "2024-01-10", None])

    def test_a_season_is_its_own_row_and_episodes_are_left_out(self):
        # S-10
        season = self.item(MediaTypes.SEASON.value, "show", season=2)
        episode = self.item(MediaTypes.EPISODE.value, "show", season=2, episode=1)
        self.item(MediaTypes.TV.value, "show")
        self.log(season, rating=Decimal("9.0"))
        self.log(episode, rating=Decimal("10.0"))
        for tab in (None, "tv"):
            with self.subTest(tab=tab):
                query = {**YEAR_2025, **({"media_type": tab} if tab else {})}
                data = self.stats(self.owner, query)["highest_rated"]
                self.assertEqual(data["total"], 1)
                row = data["items"][0]
                self.assertEqual(row["media"]["ref"]["media_type"], "season")
                self.assertEqual(row["media"]["ref"]["season_number"], 2)
                self.assertEqual(row["stars"], "4.5")

    def test_a_current_rating_counts_in_all_time_only_for_every_viewer(self):
        # S-11
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.movie_row(film, score=Decimal("10.0"))
        for viewer, path in (
            (self.owner, None),
            (self.follower, self.user_path("summary")),
            (self.stranger, self.user_path("summary")),
        ):
            with self.subTest(viewer=viewer.username):
                all_time = self.stats(viewer, ALL_TIME, path=path)["highest_rated"]
                self.assertEqual(self.ids(all_time["items"]), [film.id])
                self.assertEqual((all_time["items"][0]["stars"], all_time["items"][0]["day"]), ("5.0", None))
                self.assertEqual(self.stats(viewer, YEAR_2025, path=path)["highest_rated"]["items"], [])

    def test_a_rated_log_wins_over_the_current_rating_and_hidden_logs_fall_back(self):
        # Anime logs keep per-log visibility; movie logs follow the account (SW-400).
        film = self.item(MediaTypes.ANIME.value, "film")
        season_item = self.item(MediaTypes.SEASON.value, "show", season=1)
        show_item = self.item(MediaTypes.TV.value, "show")
        Anime.objects.bulk_create([
            Anime(user=self.owner, item=film, status=Status.COMPLETED.value, score=Decimal("9.0")),
        ])
        self.log(film, rating=Decimal("6.0"), visibility="private")
        _tv, season_row = self.show_rows(show_item, season_item)
        Season.objects.filter(pk=season_row.pk).update(score=Decimal("7.0"))
        owner = self.stats(self.owner, ALL_TIME)["highest_rated"]["items"]
        self.assertEqual([(row["media"]["ref"]["item_id"], row["stars"]) for row in owner], [
            (season_item.id, "3.5"),
            (film.id, "3.0"),
        ])
        follower = self.stats(self.follower, ALL_TIME, path=self.user_path())["highest_rated"]["items"]
        self.assertEqual([(row["media"]["ref"]["item_id"], row["stars"]) for row in follower], [
            (film.id, "4.5"),
            (season_item.id, "3.5"),
        ])

    def test_the_latest_tracking_row_holds_the_current_rating(self):
        # Anime keeps one tracking row per watch-through.
        anime = self.item(MediaTypes.ANIME.value, "anime")
        older = Anime(user=self.owner, item=anime, status=Status.COMPLETED.value, score=Decimal("10.0"))
        Anime.objects.bulk_create([older])
        newer = Anime(user=self.owner, item=anime, status=Status.IN_PROGRESS.value, score=Decimal("4.0"))
        Anime.objects.bulk_create([newer])
        items = self.stats(self.owner, {**ALL_TIME, "media_type": "anime"})["highest_rated"]["items"]
        self.assertEqual([row["stars"] for row in items], ["2.0"])

    def test_the_rating_day_is_on_the_owners_calendar(self):
        # A real moment (not a UTC-midnight carrier) is read in the owner's zone.
        moment = datetime(2025, 3, 2, 10, 30, tzinfo=UTC)
        self.log(self.item(MediaTypes.ANIME.value, "a"), moment, rating=Decimal("8.0"))
        expected = {NEW_YORK: "2025-03-02", LOS_ANGELES: "2025-03-02", AUCKLAND: "2025-03-02"}
        late = datetime(2025, 3, 2, 12, 30, tzinfo=UTC)
        self.log(self.item(MediaTypes.ANIME.value, "b"), late, rating=Decimal("6.0"))
        late_expected = {NEW_YORK: "2025-03-02", LOS_ANGELES: "2025-03-02", AUCKLAND: "2025-03-03"}
        for zone, early_day in expected.items():
            with self.subTest(zone=zone):
                self.owner.calendar_timezone = zone
                self.owner.save(update_fields=["calendar_timezone"])
                items = self.stats(self.follower, YEAR_2025, path=self.user_path())["highest_rated"]["items"]
                self.assertEqual([row["day"] for row in items], [early_day, late_expected[zone]])

    def test_show_all_pages_in_the_same_order_for_every_viewer(self):
        made = []
        for index in range(30):
            item = self.item(MediaTypes.MOVIE.value, f"m{index}")
            self.log(item, rating=Decimal(index % 11))
            made.append(item)
        scored = self.item(MediaTypes.MOVIE.value, "scored")
        self.movie_row(scored, score=Decimal("10.0"))
        preview = self.stats(self.owner, ALL_TIME)["highest_rated"]
        self.assertEqual(preview["total"], 31)
        self.assertEqual(len(preview["items"]), 12)
        for viewer, path in ((self.owner, "/api/v1/stats/me/highest-rated/"), (self.stranger, self.user_path("highest-rated"))):
            with self.subTest(viewer=viewer.username):
                first = self.get(viewer, {"start_date": "all", "end_date": "all", "page_size": 20}, path=path).data
                second = self.get(viewer, {"start_date": "all", "end_date": "all", "page_size": 20, "page": 2}, path=path).data
                self.assertEqual(first["count"], 31)
                self.assertIsNotNone(first["next"])
                self.assertIsNone(second["next"])
                rows = first["results"] + second["results"]
                self.assertEqual(len(rows), 31)
                self.assertEqual(self.ids(rows[:12]), self.ids(preview["items"]))
                self.assertIn(scored.id, self.ids(rows))
                self.assertEqual(set(rows[0]), {"media", "stars", "day"})
                stored = [Decimal(row["stars"]) for row in rows]
                self.assertEqual(stored, sorted(stored, reverse=True))

    def test_title_ratings_helper_matches_highest_rated(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        unrated = self.item(MediaTypes.MOVIE.value, "unrated")
        self.log(film, rating=Decimal("7.0"))
        self.log(unrated)
        scope = StatsScope(owner=self.owner, viewer=self.owner)
        self.assertEqual(ratings.title_ratings(scope, [film.id, unrated.id]), {film.id: Decimal("7.0")})



class RatedVsEveryoneTests(RatingsBase):
    """STAT-217: your stars against one community source per type."""

    def test_movies_use_letterboxd_and_skip_titles_without_enough_votes(self):
        # S-27
        first = self.item(MediaTypes.MOVIE.value, "first")
        second = self.item(MediaTypes.MOVIE.value, "second")
        self.log(first, rating=Decimal("9.0"))
        self.log(second, rating=Decimal("2.0"))
        self.community(first, "letterboxd", "3.1", 5, votes=8000)
        self.community(first, "imdb", "6.0", 10, votes=100000)
        self.community(second, "letterboxd", "4.5", 5, votes=30)
        self.community(second, "imdb", "8.0", 10, votes=100000)
        data = self.stats(self.owner, YEAR_2025)["rated_vs_everyone"]
        self.assertEqual(data["sources"], [{"media_type": "movie", "source": "letterboxd", "label": "Letterboxd"}])
        self.assertEqual(len(data["higher"]), 1)
        row = data["higher"][0]
        self.assertEqual(row["media"]["ref"]["item_id"], first.id)
        self.assertEqual(
            (row["stars"], row["everyone_stars"], row["difference"], row["source"]),
            ("4.5", "3.1", "1.4", "letterboxd"),
        )
        self.assertEqual(data["lower"], [])

    def test_each_type_falls_back_to_the_next_source_with_data(self):
        movie = self.item(MediaTypes.MOVIE.value, "movie")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        game = self.item(MediaTypes.GAME.value, "game")
        comic = self.item(MediaTypes.COMIC.value, "comic")
        self.log(movie, rating=Decimal("4.0"))
        self.log(season, rating=Decimal("6.0"))
        self.log(game, rating=Decimal("10.0"))
        self.log(comic, rating=Decimal("10.0"))
        self.community(movie, "letterboxd", "4.0", 5, status="unavailable")  # not usable
        self.community(movie, "imdb", "8.4", 10)  # no votes stored: usable
        self.community(season, "tmdb", "9.0", 10, votes=60)
        self.community(game, "igdb", "70", 100, votes=None)
        data = self.stats(self.owner, YEAR_2025)["rated_vs_everyone"]
        self.assertEqual(
            [(row["media_type"], row["source"], row["label"]) for row in data["sources"]],
            [("movie", "imdb", "IMDb"), ("tv", "tmdb", "TMDB"), ("game", "igdb", "IGDB")],
        )
        self.assertEqual([(row["media"]["ref"]["item_id"], row["difference"]) for row in data["higher"]], [
            (game.id, "1.5"),
        ])
        self.assertEqual(
            [(row["media"]["ref"]["item_id"], row["everyone_stars"], row["difference"]) for row in data["lower"]],
            [(movie.id, "4.2", "-2.2"), (season.id, "4.5", "-1.5")],
        )
        self.assertEqual(data["lower"][1]["media"]["ref"]["media_type"], "season")
        tv_tab = self.stats(self.owner, {**YEAR_2025, "media_type": "tv"})["rated_vs_everyone"]
        self.assertEqual([row["source"] for row in tv_tab["sources"]], ["tmdb"])

    def test_five_each_way_sorted_by_size_and_zero_in_neither(self):
        made = {}
        for index in range(7):
            up = self.item(MediaTypes.MOVIE.value, f"up{index}")
            self.log(up, rating=Decimal("10.0"))
            self.community(up, "letterboxd", Decimal("4.0") - Decimal(index) / 2, 5, votes=100)
            down = self.item(MediaTypes.MOVIE.value, f"down{index}")
            self.log(down, rating=Decimal("1.0"))
            self.community(down, "letterboxd", Decimal("1.0") + Decimal(index) / 2, 5, votes=100)
            made[index] = (up, down)
        even = self.item(MediaTypes.MOVIE.value, "even")
        self.log(even, rating=Decimal("7.0"))
        self.community(even, "letterboxd", "3.52", 5, votes=100)  # 0.02 apart: zero
        data = self.stats(self.owner, YEAR_2025)["rated_vs_everyone"]
        self.assertEqual(self.ids(data["higher"]), [made[index][0].id for index in (6, 5, 4, 3, 2)])
        self.assertEqual(self.ids(data["lower"]), [made[index][1].id for index in (6, 5, 4, 3, 2)])
        self.assertEqual(data["higher"][0]["difference"], "4.0")
        self.assertEqual(data["lower"][0]["difference"], "-3.5")
        self.assertNotIn(even.id, self.ids(data["higher"]) + self.ids(data["lower"]))

    def test_the_current_rating_is_compared_in_all_time(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.movie_row(film, score=Decimal("10.0"))
        self.community(film, "letterboxd", "3.0", 5, votes=100)
        follower = self.stats(self.follower, ALL_TIME, path=self.user_path())["rated_vs_everyone"]
        self.assertEqual(self.ids(follower["higher"]), [film.id])
        self.assertEqual(self.stats(self.owner, YEAR_2025)["rated_vs_everyone"]["higher"], [])

    @override_settings(MUSICBRAINZ_EXTERNAL_RATINGS_ENABLED=False)
    def test_an_unexposed_source_is_never_used(self):
        album = self.item(MediaTypes.MUSIC.value, "album")
        self.log(album, rating=Decimal("10.0"))
        self.community(album, "musicbrainz", "2.0", 5, votes=100)
        data = self.stats(self.owner, YEAR_2025)["rated_vs_everyone"]
        self.assertEqual((data["sources"], data["higher"]), ([], []))

    @override_settings(MUSICBRAINZ_EXTERNAL_RATINGS_ENABLED=True)
    def test_music_compares_when_its_source_is_exposed(self):
        album = self.item(MediaTypes.MUSIC.value, "album")
        self.log(album, rating=Decimal("10.0"))
        self.community(album, "musicbrainz", "2.0", 5, votes=100)
        data = self.stats(self.owner, YEAR_2025)["rated_vs_everyone"]
        self.assertEqual([row["source"] for row in data["sources"]], ["musicbrainz"])
        self.assertEqual(data["higher"][0]["everyone_stars"], "2.0")


class MostLoggedTests(RatingsBase):
    """STAT-215: titles logged at least twice, TV by its most-viewed episode."""

    def test_titles_logged_twice_sorted_by_times_then_newest_day(self):
        movie = self.item(MediaTypes.MOVIE.value, "movie")
        book = self.item(MediaTypes.BOOK.value, "book")
        game = self.item(MediaTypes.GAME.value, "game")
        once = self.item(MediaTypes.MOVIE.value, "once")
        for day in (MAR_2025_OLD, MAY_2025, JUN_2025):
            self.log(movie, day)
        self.log(book, MAR_2025_OLD)
        self.log(book, JUN_2025)
        self.log(game, MAY_2025)
        self.log(game, MAY_2025)
        self.log(once)
        data = self.stats(self.owner, YEAR_2025)["most_logged"]
        self.assertEqual(data["label"], "Most logged")
        self.assertEqual(data["total"], 3)
        self.assertEqual([(row["media"]["ref"]["item_id"], row["times"]) for row in data["items"]], [
            (movie.id, 3),
            (book.id, 2),
            (game.id, 2),
        ])
        labels = {tab: self.stats(self.owner, {**YEAR_2025, "media_type": tab})["most_logged"]["label"] for tab in (
            "movie", "tv", "anime", "book", "manga", "comic", "game", "music",
        )}
        self.assertEqual(labels, {
            "movie": "Most watched",
            "tv": "Most watched",
            "anime": "Most watched",
            "book": "Most read",
            "manga": "Most read",
            "comic": "Most read",
            "game": "Most played",
            "music": "Most listened",
        })
        book_tab = self.stats(self.owner, {**YEAR_2025, "media_type": "book"})["most_logged"]
        self.assertEqual(self.ids(book_tab["items"]), [book.id])

    def test_a_show_counts_its_most_viewed_episode(self):
        # S-25: S1 E1 watched three times in 2025.
        show = self.item(MediaTypes.TV.value, "show")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        first = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        second = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=2)
        _tv, season_row = self.show_rows(show, season)
        for day in (date(2025, 3, 2), date(2025, 4, 2), date(2025, 5, 2)):
            self.viewing(season_row, first, watched_on=day)
        self.viewing(season_row, second, watched_on=date(2025, 6, 2))
        self.viewing(season_row, first, kind="fill")  # no day: All Time only
        self.viewing(season_row, first, watched_on=date(2024, 3, 2))
        for item in (show, season, first, second):
            self.log(item)  # TV logs never count as times
        for tab in (None, "tv"):
            with self.subTest(tab=tab):
                query = {**YEAR_2025, **({"media_type": tab} if tab else {})}
                data = self.stats(self.owner, query)["most_logged"]
                self.assertEqual([(row["media"]["ref"]["item_id"], row["times"]) for row in data["items"]], [
                    (show.id, 3),
                ])
                self.assertEqual(data["items"][0]["media"]["ref"]["media_type"], "tv")
        all_time = self.stats(self.owner, {**ALL_TIME, "media_type": "tv"})["most_logged"]
        self.assertEqual([row["times"] for row in all_time["items"]], [5])

    def test_hidden_viewings_never_count(self):
        show = self.item(MediaTypes.TV.value, "show")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        _tv, season_row = self.show_rows(show, season)
        hidden = self.log(episode, visibility="private")
        self.viewing(season_row, episode, watched_on=date(2025, 3, 2), kind="log", entry=hidden)
        self.viewing(season_row, episode, watched_on=date(2025, 4, 2))
        self.assertEqual(self.ids(self.stats(self.owner, YEAR_2025)["most_logged"]["items"]), [show.id])
        follower = self.stats(self.follower, YEAR_2025, path=self.user_path())["most_logged"]
        self.assertEqual((follower["total"], follower["items"]), (0, []))

    def test_hidden_logs_never_count(self):
        anime = self.item(MediaTypes.ANIME.value, "anime")
        self.log(anime)
        self.log(anime, JUN_2025, visibility="private")
        self.assertEqual(self.stats(self.owner, YEAR_2025)["most_logged"]["total"], 1)
        self.assertEqual(self.stats(self.stranger, YEAR_2025, path=self.user_path())["most_logged"]["total"], 0)

    def test_show_all_folds_to_the_show_and_the_legacy_page_stays(self):
        show = self.item(MediaTypes.TV.value, "show")
        season = self.item(MediaTypes.SEASON.value, "show", season=1)
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        _tv, season_row = self.show_rows(show, season)
        self.viewing(season_row, episode, watched_on=date(2025, 3, 2))
        self.viewing(season_row, episode, watched_on=date(2025, 3, 3))
        self.log(episode)
        self.log(episode, JUN_2025)
        movies = []
        for index in range(14):
            movie = self.item(MediaTypes.MOVIE.value, f"m{index}")
            self.log(movie)
            self.log(movie, JUN_2025)
            movies.append(movie)
        preview = self.stats(self.owner, YEAR_2025)["most_logged"]
        self.assertEqual((preview["total"], len(preview["items"])), (15, 12))
        params = {"schema": "2", "start_date": "2025-01-01", "end_date": "2025-12-31", "page_size": 10}
        first = self.get(self.owner, params, path="/api/v1/stats/me/most-logged/").data
        second = self.get(self.owner, {**params, "page": 2}, path="/api/v1/stats/me/most-logged/").data
        self.assertEqual(first["count"], 15)
        rows = first["results"] + second["results"]
        self.assertEqual(len(rows), 15)
        self.assertEqual(set(rows[0]), {"media", "times"})
        self.assertIn(show.id, self.ids(rows))
        self.assertNotIn(episode.id, self.ids(rows))
        legacy = self.get(
            self.owner,
            {"start_date": "2025-01-01", "end_date": "2025-12-31"},
            path="/api/v1/stats/me/most-logged/",
        ).data
        self.assertEqual(set(legacy["results"][0]), {"media", "log_count"})
        self.assertIn(episode.id, self.ids(legacy["results"]))
        self.assertNotIn(show.id, self.ids(legacy["results"]))
        other = self.get(self.stranger, params, path=self.user_path("most-logged")).data
        self.assertEqual(other["count"], 15)


class RatingsQueryCountTests(RatingsBase):
    """Each section's query count doesn't grow with the library."""

    SECTIONS = (
        ("ratings", ratings.build_ratings),
        ("highest_rated", ratings.build_highest_rated),
        ("rated_vs_everyone", ratings.build_rated_vs_everyone),
        ("most_logged", ratings.build_most_logged),
        ("highest_rated_page", lambda scope: ratings.highest_rated_page(scope, 1, 25)),
        ("most_logged_page", lambda scope: ratings.most_logged_page(scope, 1, 25)),
    )

    def seed(self, count, prefix):
        show = self.item(MediaTypes.TV.value, f"{prefix}show")
        season = self.item(MediaTypes.SEASON.value, f"{prefix}show", season=1)
        _tv, season_row = self.show_rows(show, season)
        self.log(season, rating=Decimal("7.0"), visibility="private")
        for index in range(count):
            movie = self.item(MediaTypes.MOVIE.value, f"{prefix}m{index}")
            self.log(movie, rating=Decimal(index % 11))
            self.log(movie, JUN_2025, rating=Decimal((index + 3) % 11))
            self.movie_row(self.item(MediaTypes.MOVIE.value, f"{prefix}s{index}"), score=Decimal("8.0"))
            self.community(movie, "letterboxd", "3.0", 5, votes=100)
            episode = self.item(MediaTypes.EPISODE.value, f"{prefix}show", season=1, episode=index + 1)
            self.viewing(season_row, episode, watched_on=date(2025, 3, 2))
            self.viewing(season_row, episode, watched_on=date(2025, 3, 3))

    def queries(self, viewer, audience, start, end):
        counts = {}
        for name, build in self.SECTIONS:
            # A fresh scope per section, so no section reuses another's posters.
            scope = StatsScope(owner=self.owner, viewer=viewer, start_date=start, end_date=end, audience=audience)
            with CaptureQueriesContext(connection) as context:
                build(scope)
            counts[name] = len(context.captured_queries)
        return counts

    def test_query_count_is_flat(self):
        cases = []
        for viewer, audience in ((self.owner, "owner"), (self.follower, "follower")):
            for start, end in ((None, None), (date(2025, 1, 1), date(2025, 12, 31))):
                cases.append((viewer, audience, start, end))
        self.seed(14, "a")
        small = [self.queries(*case) for case in cases]
        self.seed(40, "b")
        large = [self.queries(*case) for case in cases]
        for case, before, after in zip(cases, small, large, strict=True):
            with self.subTest(audience=case[1], start=case[2]):
                self.assertEqual(before, after)
