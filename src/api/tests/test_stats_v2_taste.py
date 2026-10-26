"""Stats v2 release years, genres and languages (docs/stats-contract.md).

STAT-209 (release years), STAT-210 and STAT-211 (genres), STAT-212 (languages), and
the book year as first publication (Open Library, Milestone 1 decision 2).
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from api.services import stats_v2_taste as taste
from api.services.filters import update_item_filter_metadata
from api.services.stats_scope import StatsScope
from app.models import (
    TV,
    DiaryEntry,
    Episode,
    Item,
    ItemFilterFacet,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from app.providers import openlibrary
from social.models import Follow, FollowStatus

NEW_YORK = "America/New_York"
LOS_ANGELES = "America/Los_Angeles"
AUCKLAND = "Pacific/Auckland"
MAR_2025 = datetime(2025, 3, 2, tzinfo=UTC)
NOW = datetime(2026, 10, 26, 16, 0, tzinfo=UTC)
YEAR_2025 = (date(2025, 1, 1), date(2025, 12, 31))
GENRE = ItemFilterFacet.FacetType.GENRE
THEME = ItemFilterFacet.FacetType.THEME
ORIGINAL_LANGUAGE = ItemFilterFacet.FacetType.ORIGINAL_LANGUAGE

SOURCES = {
    MediaTypes.ANIME.value: Sources.MAL.value,
    MediaTypes.MANGA.value: Sources.MAL.value,
    MediaTypes.GAME.value: Sources.IGDB.value,
    MediaTypes.BOOK.value: Sources.OPENLIBRARY.value,
    MediaTypes.COMIC.value: Sources.COMICVINE.value,
    MediaTypes.MUSIC.value: Sources.MUSICBRAINZ.value,
}


@override_settings(TIME_ZONE=NEW_YORK, MUSIC_ENABLED=True)
class TasteBase(TestCase):
    """An owner and a follower, with helpers for Items, facets, logs and viewings."""

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        users = get_user_model().objects
        self.owner = users.create_user(username="owner", password="strong-password-123")
        self.follower = users.create_user(username="follower", password="strong-password-123")
        Follow.objects.create(from_user=self.follower, to_user=self.owner, status=FollowStatus.ACCEPTED)

    def item(self, media_type, media_id, *, year=None, season=None, episode=None, genres=(), themes=(), language=None):
        item = Item.objects.create(
            media_id=media_id,
            source=SOURCES.get(media_type, Sources.TMDB.value),
            media_type=media_type,
            season_number=season,
            episode_number=episode,
            title=f"{media_type} {media_id} {season or ''} {episode or ''}".strip(),
            image="https://example.com/poster.jpg",
            release_year=year,
        )
        facets = [ItemFilterFacet(item=item, facet_type=GENRE, value=value) for value in genres]
        facets += [ItemFilterFacet(item=item, facet_type=THEME, value=value) for value in themes]
        if language:
            facets.append(ItemFilterFacet(item=item, facet_type=ORIGINAL_LANGUAGE, value=language))
        ItemFilterFacet.objects.bulk_create(facets)
        return item

    def log(self, item, consumed_at=MAR_2025, *, rating=None, visibility="public"):
        return DiaryEntry.objects.create(
            user=self.owner,
            item=item,
            consumed_at=consumed_at,
            rating=rating,
            visibility=visibility,
        )

    def show(self, media_id, *, year=None, genres=(), language=None, seasons=1):
        """Create a show Item and season Items 1..seasons, each season dated 2020."""
        show = self.item(MediaTypes.TV.value, media_id, year=year, genres=genres, language=language)
        season_items = [
            self.item(MediaTypes.SEASON.value, media_id, season=number, year=2020)
            for number in range(1, seasons + 1)
        ]
        return show, season_items

    def scope(self, *, media_type=None, dates=(None, None), viewer=None):
        return StatsScope(
            owner=self.owner,
            viewer=viewer or self.owner,
            start_date=dates[0],
            end_date=dates[1],
            media_type=media_type,
        )

    def row(self, rows, name):
        return next(row for row in rows if row["name"] == name)


class GlobalGenreTests(TasteBase):
    """Global genres: 15 groups, music apart (STAT-211)."""

    def seed_scifi(self):
        """S-20: four types reach Sci-Fi & Fantasy; an album's "drama" stays apart."""
        movie = self.item("movie", "1", genres=["Science Fiction"])
        _show, (season,) = self.show("2", genres=["Sci-Fi & Fantasy"])
        anime = self.item("anime", "3", genres=["Sci-Fi"])
        game = self.item("game", "4", genres=["Shooter"], themes=["Science fiction"])
        album = self.item("music", "5", genres=["drama"])
        self.log(movie, rating=Decimal("8.0"))
        self.log(season, rating=Decimal("6.0"))
        self.log(anime)
        self.log(game, rating=Decimal("10.0"))
        self.log(album, rating=Decimal("4.0"))
        return movie, season, anime, game, album

    def test_four_types_reach_one_scifi_bar_and_music_stays_apart(self):
        self.seed_scifi()

        genres = taste.build_genres(self.scope())

        scifi = next(row for row in genres["rows"] if row["key"] == "scifi_fantasy")
        self.assertEqual(scifi["name"], "Sci-Fi & Fantasy")
        self.assertIsNone(scifi["values"])
        self.assertEqual(scifi["titles"], 4)
        self.assertEqual(scifi["by_type"], {"movie": 1, "tv": 1, "anime": 1, "game": 1})
        self.assertEqual(scifi["rated"], 3)
        self.assertEqual(scifi["average_stars"], "4.0")
        action = next(row for row in genres["rows"] if row["key"] == "action_adventure")
        self.assertEqual(action["by_type"], {"game": 1})
        self.assertEqual([row["key"] for row in genres["rows"]], ["scifi_fantasy", "action_adventure"])
        self.assertEqual(genres["total"], 2)
        self.assertNotIn("drama", [row["key"] for row in genres["rows"]])
        self.assertEqual(genres["music"], [{"name": "drama", "titles": 1}])
        # Coverage leaves music out.
        self.assertEqual(genres["coverage"], {"known": 4, "total": 4})

    def test_coverage_counts_titles_that_reach_no_group(self):
        self.seed_scifi()
        self.log(self.item("movie", "6", genres=["TV Movie"]))
        self.log(self.item("movie", "7"))

        genres = taste.build_genres(self.scope())

        self.assertEqual(genres["coverage"], {"known": 4, "total": 6})

    def test_a_title_counts_once_however_many_values_reach_a_group(self):
        film = self.item("movie", "1", genres=["Action", "Adventure", "Western"])
        self.log(film)
        self.log(film)

        rows = taste.build_genres(self.scope())["rows"]

        self.assertEqual(rows[0]["key"], "action_adventure")
        self.assertEqual(rows[0]["titles"], 1)

    def test_music_genres_are_the_top_five_with_case_variants_merged(self):
        for index, genres in enumerate([
            ["Shoegaze"], ["shoegaze"], ["shoegaze"], ["rock"], ["pop"], ["jazz"], ["metal"], ["folk"],
        ]):
            self.log(self.item("music", f"m{index}", genres=genres))

        genres = taste.build_genres(self.scope())

        self.assertEqual(genres["rows"], [])
        self.assertEqual(len(genres["music"]), 5)
        self.assertEqual(genres["music"][0], {"name": "shoegaze", "titles": 3})
        self.assertEqual([row["name"] for row in genres["music"][1:]], ["folk", "jazz", "metal", "pop"])

    def test_music_tab_lists_its_genres_in_rows(self):
        self.log(self.item("music", "m1", genres=["shoegaze"]))

        genres = taste.build_genres(self.scope(media_type="music"))

        self.assertEqual([row["name"] for row in genres["rows"]], ["shoegaze"])
        self.assertEqual(genres["music"], [])


class TypeGenreTests(TasteBase):
    """Type-tab genres: native names, case variants merged (STAT-210)."""

    def test_spellings_differing_in_case_are_one_row(self):
        # S-21: MAL's "Sci-Fi" and MangaUpdates' "Sci-fi".
        for index in range(2):
            self.log(self.item("manga", f"mal{index}", genres=["Sci-Fi"]), rating=Decimal("8.0"))
        self.log(self.item("manga", "mu1", genres=["Sci-fi", "Romance"]), rating=Decimal("7.0"))

        genres = taste.build_genres(self.scope(media_type="manga"))

        scifi = genres["rows"][0]
        self.assertEqual(scifi["name"], "Sci-Fi")
        self.assertEqual(scifi["values"], ["Sci-Fi", "Sci-fi"])
        self.assertIsNone(scifi["key"])
        self.assertEqual(scifi["titles"], 3)
        self.assertEqual(scifi["rated"], 3)
        self.assertEqual(scifi["average_stars"], "3.83")
        self.assertEqual(scifi["by_type"], {"manga": 3})
        self.assertEqual(genres["total"], 2)
        self.assertEqual(genres["music"], [])

    def test_a_show_counts_once_through_its_own_genres(self):
        show, seasons = self.show("10", genres=["Drama"], seasons=2)
        episode = self.item("episode", "10", season=1, episode=1, genres=["Comedy"])
        self.log(show, rating=Decimal("6.0"))
        self.log(seasons[0], rating=Decimal("8.0"))
        self.log(seasons[1])
        self.log(episode, rating=Decimal("10.0"))

        genres = taste.build_genres(self.scope(media_type="tv"))

        self.assertEqual([row["name"] for row in genres["rows"]], ["Drama"])
        drama = genres["rows"][0]
        self.assertEqual(drama["titles"], 1)
        self.assertEqual(drama["rated"], 3)
        self.assertEqual(drama["average_stars"], "4.0")
        self.assertEqual(genres["coverage"], {"known": 1, "total": 1})

    def test_game_tab_reads_genres_not_themes(self):
        self.log(self.item("game", "g1", genres=["Shooter"], themes=["Horror"]))

        rows = taste.build_genres(self.scope(media_type="game"))["rows"]

        self.assertEqual([row["name"] for row in rows], ["Shooter"])

    def test_rows_are_capped_at_fifty_with_the_total(self):
        film = self.item("movie", "1", genres=[f"Genre {index:02d}" for index in range(55)])
        self.log(film)

        genres = taste.build_genres(self.scope(media_type="movie"))

        self.assertEqual(len(genres["rows"]), 50)
        self.assertEqual(genres["total"], 55)

    def test_hidden_logs_count_for_nothing_for_a_follower(self):
        _show, (season,) = self.show("10", genres=["Drama"])
        self.log(season, rating=Decimal("8.0"), visibility="private")

        owner_rows = taste.build_genres(self.scope(media_type="tv"))["rows"]
        follower_rows = taste.build_genres(self.scope(media_type="tv", viewer=self.follower))["rows"]

        self.assertEqual(owner_rows[0]["rated"], 1)
        self.assertEqual(follower_rows, [])


class LanguageTests(TasteBase):
    """Languages: original language of movies and TV (STAT-212)."""

    def test_a_film_and_a_show_with_four_seasons_are_two_japanese_titles(self):
        # S-22.
        film = self.item("movie", "1", language="Japanese")
        _show, seasons = self.show("2", language="Japanese", seasons=4)
        english = self.item("movie", "3", language="English")
        self.log(film, rating=Decimal("9.0"))
        for season in seasons:
            self.log(season, rating=Decimal("7.0"))
        self.log(english)
        self.log(self.item("anime", "4", language="Japanese"))

        languages = taste.build_languages(self.scope())

        japanese = self.row(languages["rows"], "Japanese")
        self.assertEqual(japanese["titles"], 2)
        self.assertEqual(japanese["by_type"], {"movie": 1, "tv": 1})
        self.assertEqual(japanese["rated"], 5)
        self.assertEqual(japanese["average_stars"], "3.7")
        self.assertEqual([row["name"] for row in languages["rows"]], ["Japanese", "English"])
        self.assertEqual(languages["total"], 2)
        self.assertEqual(languages["media_types"], ["movie", "tv"])
        self.assertEqual(languages["coverage"], {"known": 3, "total": 3})

        tv = taste.build_languages(self.scope(media_type="tv"))
        self.assertEqual(tv["rows"], [{"name": "Japanese", "titles": 1, "rated": 4, "average_stars": "3.5", "by_type": {"tv": 1}}])
        self.assertEqual(tv["media_types"], ["tv"])

    def test_titles_without_an_original_language_count_in_coverage(self):
        self.log(self.item("movie", "1", language="French"))
        self.log(self.item("movie", "2"))

        languages = taste.build_languages(self.scope(media_type="movie"))

        self.assertEqual(languages["coverage"], {"known": 1, "total": 2})
        self.assertEqual(languages["rows"][0]["average_stars"], None)


class ReleaseYearTests(TasteBase):
    """Release years and new vs older (STAT-209)."""

    def test_every_year_is_its_own_row_and_tv_uses_the_show_year(self):
        _show, (season,) = self.show("2", year=1999)
        film_1999 = self.item("movie", "1", year=1999)
        film_2003 = self.item("movie", "3", year=2003)
        anime_2024 = self.item("anime", "4", year=2024)
        self.log(season, rating=Decimal("8.0"))
        self.log(film_1999, rating=Decimal("7.0"))
        self.log(film_1999)
        self.log(film_2003)
        self.log(anime_2024, rating=Decimal("9.0"))
        self.log(self.item("movie", "5"))

        section = taste.build_release_years(self.scope())

        self.assertEqual([row["year"] for row in section["rows"]], [1999, 2003, 2024])
        first = section["rows"][0]
        self.assertEqual(first, {
            "year": 1999,
            "titles": 2,
            "logs": 3,
            "rated": 2,
            "average_stars": "3.75",
            "by_type": {"movie": 1, "tv": 1},
        })
        self.assertIsNone(section["rows"][1]["average_stars"])
        self.assertEqual(section["rows"][2]["by_type"], {"anime": 1})
        self.assertEqual(section["coverage"], {"known": 4, "total": 5})

    def test_new_vs_older_by_age_when_first_seen_in_the_range(self):
        new = self.item("movie", "1", year=2025)
        recent = self.item("movie", "2", year=2018)
        older = self.item("movie", "3", year=2017)
        later_release = self.item("movie", "4", year=2026)
        self.log(new)
        self.log(recent)
        self.log(older)
        self.log(later_release)
        # Undated completion: a title in All Time, never in new vs older.
        undated = self.item("movie", "5", year=2025)
        Movie.objects.bulk_create([
            Movie(user=self.owner, item=undated, status=Status.COMPLETED.value, direct_consumption=True),
        ])

        in_2025 = taste.build_release_years(self.scope(dates=YEAR_2025))
        all_time = taste.build_release_years(self.scope())

        self.assertEqual(in_2025["new_vs_older"], {"new": 2, "recent": 1, "older": 1})
        self.assertEqual(all_time["new_vs_older"], {"new": 2, "recent": 1, "older": 1})
        self.assertEqual(all_time["coverage"], {"known": 5, "total": 5})
        self.assertEqual(in_2025["coverage"], {"known": 4, "total": 4})

    def test_a_title_is_new_when_its_earliest_day_in_the_range_is(self):
        film = self.item("movie", "1", year=2010)
        self.log(film, datetime(2010, 5, 1, tzinfo=UTC))
        self.log(film, MAR_2025)

        all_time = taste.build_release_years(self.scope())
        in_2025 = taste.build_release_years(self.scope(dates=YEAR_2025))

        self.assertEqual(all_time["new_vs_older"], {"new": 1, "recent": 0, "older": 0})
        self.assertEqual(in_2025["new_vs_older"], {"new": 0, "recent": 0, "older": 1})

    def test_dated_viewings_count_their_show(self):
        show, (season_item,) = self.show("2", year=2024)
        episode_item = self.item("episode", "2", season=1, episode=1)
        tv = TV(user=self.owner, item=show, status=Status.IN_PROGRESS.value)
        TV.objects.bulk_create([tv])
        season = Season(user=self.owner, item=season_item, related_tv=tv, status=Status.IN_PROGRESS.value)
        Season.objects.bulk_create([season])
        Episode.objects.bulk_create([
            Episode(related_season=season, item=episode_item, watched_on=date(2025, 6, 1), kind="tick"),
        ])

        section = taste.build_release_years(self.scope(dates=YEAR_2025))

        self.assertEqual(section["rows"], [
            {"year": 2024, "titles": 1, "logs": 0, "rated": 0, "average_stars": None, "by_type": {"tv": 1}},
        ])
        self.assertEqual(section["new_vs_older"], {"new": 0, "recent": 1, "older": 0})

    def test_log_days_follow_the_owner_calendar(self):
        # A real moment: 23:30 on 31 Dec 2025 in Los Angeles is 1 Jan 2026 in Auckland.
        film = self.item("movie", "1", year=2025)
        self.log(film, datetime(2025, 12, 31, 23, 30, tzinfo=ZoneInfo(LOS_ANGELES)))
        for zone, expected_2025 in ((LOS_ANGELES, 1), (NEW_YORK, 0), (AUCKLAND, 0)):
            with self.subTest(owner_zone=zone):
                self.owner.calendar_timezone = zone
                self.owner.save(update_fields=["calendar_timezone"])
                section = taste.build_release_years(self.scope(dates=YEAR_2025))
                self.assertEqual(section["new_vs_older"]["new"], expected_2025)
                in_2026 = taste.build_release_years(self.scope(dates=(date(2026, 1, 1), date(2026, 12, 31))))
                self.assertEqual(in_2026["new_vs_older"]["recent"], 1 - expected_2025)


class TasteApiTests(TasteBase):
    """The sections through the summary endpoint."""

    def test_summary_sends_the_sections_for_a_follower(self):
        film = self.item("movie", "1", year=1999, genres=["Drama"], language="English")
        self.log(film, rating=Decimal("8.0"))
        client = APIClient()
        client.force_authenticate(self.follower)

        data = client.get(
            "/api/v1/users/owner/stats/summary/",
            {"schema": "2", "start_date": "all", "end_date": "all"},
        ).data

        self.assertEqual(data["release_years"]["rows"][0]["year"], 1999)
        self.assertEqual(data["genres"]["rows"][0]["key"], "drama")
        self.assertEqual(data["languages"]["rows"][0]["name"], "English")

    def test_query_count_does_not_grow_with_the_library(self):
        def seed(count, offset):
            for index in range(offset, offset + count):
                film = self.item("movie", f"f{index}", year=1990 + index % 30, genres=["Drama", "Comedy"], language="English")
                _show, (season,) = self.show(f"s{index}", year=2000 + index % 20, genres=["Mystery"], language="Japanese")
                self.log(film, rating=Decimal("7.0"))
                self.log(season, rating=Decimal("8.0"))

        def count_queries(scope):
            with CaptureQueriesContext(connection) as queries:
                taste.build_release_years(scope)
                taste.build_genres(scope)
                taste.build_languages(scope)
            return len(queries)

        seed(4, 0)
        small = {tab: count_queries(self.scope(media_type=tab)) for tab in (None, "movie", "tv")}
        seed(8, 4)
        large = {tab: count_queries(self.scope(media_type=tab)) for tab in (None, "movie", "tv")}

        self.assertEqual(small, large)


class OpenLibraryFirstPublicationTests(SimpleTestCase):
    """Open Library details carry the work's first publication."""

    def test_first_publish_date_forms(self):
        cases = {
            "July 29, 1954": "1954-07-29",
            "Jul 29, 1954": "1954-07-29",
            "29 July 1954": "1954-07-29",
            "1954": "1954",
            "September 1937": "1937-09",
            "Sep 1937": "1937-09",
            "1937-09-21": "1937-09-21",
            "circa 1600?": "1600",
            "": None,
            "unknown": None,
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(openlibrary.get_first_publish_date({"first_publish_date": value}), expected)
        self.assertIsNone(openlibrary.get_first_publish_date({}))

    @patch("app.providers.openlibrary.get_ratings", new_callable=AsyncMock, return_value=(None, 0))
    @patch("app.providers.openlibrary.get_editions", new_callable=AsyncMock, return_value=[])
    @patch("app.providers.openlibrary.get_authors", new_callable=AsyncMock, return_value=[])
    @patch("app.providers.openlibrary.cache")
    @patch("app.providers.openlibrary.services.api_request")
    def test_book_details_keep_the_first_publication_apart_from_the_edition(
        self, api_request, cache, *_mocks,
    ):
        cache.get.return_value = None
        book = {
            "key": "/books/OL1M",
            "title": "The Fellowship of the Ring",
            "publish_date": "Oct 01, 2017",
            "works": [{"key": "/works/OL1W"}],
        }
        work = {"key": "/works/OL1W", "first_publish_date": "July 29, 1954"}
        api_request.side_effect = lambda *args, **_kwargs: work if "/works/" in args[2] else book

        metadata = openlibrary.book("OL1M")

        self.assertEqual(metadata["details"]["release_date"], "2017-10-01")
        self.assertEqual(metadata["details"]["original_release_date"], "1954-07-29")
        self.assertTrue(cache.set.call_args.args[0].endswith("_v4"))


class OpenLibraryBookYearTests(TestCase):
    """A book's stored year is its first publication."""

    def test_book_year_is_the_first_publication(self):
        item = Item.objects.create(
            media_id="OL1M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="The Fellowship of the Ring",
            image="https://example.com/poster.jpg",
        )
        metadata = {
            "details": {
                "publish_date": "2017-10-01",
                "release_date": "2017-10-01",
                "original_release_date": openlibrary.get_first_publish_date({"first_publish_date": "1954"}),
            },
        }

        update_item_filter_metadata(item, metadata)

        item.refresh_from_db()
        self.assertEqual(item.release_year, 1954)
        self.assertEqual(item.release_date, date(1954, 1, 1))

    def test_a_work_without_a_date_keeps_the_edition_year(self):
        item = Item.objects.create(
            media_id="OL2M",
            source=Sources.OPENLIBRARY.value,
            media_type=MediaTypes.BOOK.value,
            title="Undated work",
            image="https://example.com/poster.jpg",
        )

        update_item_filter_metadata(item, {"details": {"release_date": "2017-10-01", "original_release_date": None}})

        item.refresh_from_db()
        self.assertEqual(item.release_year, 2017)
