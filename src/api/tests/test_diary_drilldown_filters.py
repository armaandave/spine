"""The diary list's stats drill-down filters (docs/stats-contract.md STAT-400, 401).

skip_backfill, tv_rollup, genre_group, original_language and the pinned stored-scale
rating bounds. Each is additive: without it the diary behaves as before.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from app.models import DiaryEntry, Item, ItemFilterFacet, MediaTypes, Sources

MAR_2025 = datetime(2025, 3, 2, tzinfo=UTC)
GENRE = ItemFilterFacet.FacetType.GENRE
THEME = ItemFilterFacet.FacetType.THEME
ORIGINAL_LANGUAGE = ItemFilterFacet.FacetType.ORIGINAL_LANGUAGE
SOURCES = {
    MediaTypes.ANIME.value: Sources.MAL.value,
    MediaTypes.GAME.value: Sources.IGDB.value,
    MediaTypes.MUSIC.value: Sources.MUSICBRAINZ.value,
    MediaTypes.BOOK.value: Sources.OPENLIBRARY.value,
}
TV_TYPES = ["tv", "season", "episode"]


@override_settings(MUSIC_ENABLED=True)
class DrilldownBase(TestCase):
    """An owner with a signed-in client and Item, facet and log helpers."""

    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="owner", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

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

    def log(self, item, *, user=None, rating=None):
        return DiaryEntry.objects.create(
            user=user or self.owner,
            item=item,
            consumed_at=MAR_2025,
            rating=rating,
        )

    def ids(self, params):
        response = self.client.get("/api/v1/diary/", {"skip_backfill": "true", **params})
        self.assertEqual(response.status_code, 200, response.data)
        return {row["id"] for row in response.data["results"]}

    def show(self, media_id="100", *, year=1999, genres=(), language=None):
        show = self.item("tv", media_id, year=year, genres=genres, language=language)
        season = self.item("season", media_id, season=1, year=2020)
        episode = self.item("episode", media_id, season=1, episode=1, year=2020)
        return show, season, episode


class TvRollupTests(DrilldownBase):
    """tv_rollup: a show brings its parts; filters match through the show."""

    def test_a_show_item_brings_its_season_and_episode_logs(self):
        show, season, episode = self.show()
        other_show, other_season, _ = self.show("200")
        logs = [self.log(show), self.log(season), self.log(episode)]
        self.log(other_show)
        self.log(other_season)

        rolled = self.ids({"item_id": show.id, "tv_rollup": "true", "media_type": TV_TYPES})
        exact = self.ids({"item_id": show.id, "media_type": TV_TYPES})

        self.assertEqual(rolled, {entry.id for entry in logs})
        self.assertEqual(exact, {logs[0].id})

    def test_a_season_item_stays_exact(self):
        show, season, episode = self.show()
        season_log = self.log(season)
        self.log(show)
        self.log(episode)

        self.assertEqual(self.ids({"item_id": season.id, "tv_rollup": "true"}), {season_log.id})

    def test_a_movie_sharing_the_show_media_id_never_rolls_up(self):
        show, _season, _episode = self.show("300")
        film = self.item("movie", "300")
        self.log(film)
        show_log = self.log(show)

        self.assertEqual(self.ids({"item_id": show.id, "tv_rollup": "true"}), {show_log.id})

    def test_year_matches_season_and_episode_logs_through_the_show(self):
        _show, season, episode = self.show(year=1999)
        season_log = self.log(season)
        episode_log = self.log(episode)
        film_log = self.log(self.item("movie", "1", year=1999))
        self.log(self.item("movie", "2", year=2020))

        self.assertEqual(
            self.ids({"year": "1999", "tv_rollup": "true"}),
            {season_log.id, episode_log.id, film_log.id},
        )
        self.assertEqual(self.ids({"year_min": "1990", "year_max": "1999", "tv_rollup": "true"}), {
            season_log.id, episode_log.id, film_log.id,
        })
        self.assertNotIn(season_log.id, self.ids({"year": "2020", "tv_rollup": "true"}))
        # Without tv_rollup a season matches on its own Item, as before.
        self.assertIn(season_log.id, self.ids({"year": "2020"}))
        self.assertNotIn(season_log.id, self.ids({"year": "1999"}))

    def test_genre_matches_through_the_show(self):
        _show, season, _episode = self.show(genres=["Drama"])
        season_log = self.log(season)

        self.assertEqual(self.ids({"genre": "Drama", "tv_rollup": "true"}), {season_log.id})
        self.assertEqual(self.ids({"genre": "Drama"}), set())

    def test_a_season_without_a_show_item_matches_on_its_own_item(self):
        season = self.item("season", "900", season=1, year=2011)
        season_log = self.log(season)

        self.assertEqual(self.ids({"year": "2011", "tv_rollup": "true"}), {season_log.id})


class GenreGroupTests(DrilldownBase):
    """genre_group: shared groups from the owner's own spellings, per type."""

    def test_the_scifi_group_returns_four_types_and_the_season_log(self):
        # S-20.
        movie_log = self.log(self.item("movie", "1", genres=["Science Fiction"]))
        _show, season, _episode = self.show(genres=["Sci-Fi & Fantasy"])
        season_log = self.log(season)
        anime_log = self.log(self.item("anime", "3", genres=["Sci-Fi"]))
        game_log = self.log(self.item("game", "4", themes=["Science fiction"]))
        self.log(self.item("music", "5", genres=["drama", "Science Fiction"]))
        self.log(self.item("movie", "6", genres=["Comedy"]))

        self.assertEqual(
            self.ids({"genre_group": "scifi_fantasy", "tv_rollup": "true"}),
            {movie_log.id, season_log.id, anime_log.id, game_log.id},
        )
        # Without tv_rollup the season matches on its own Item, which has no genres.
        self.assertEqual(
            self.ids({"genre_group": "scifi_fantasy"}),
            {movie_log.id, anime_log.id, game_log.id},
        )
        # One type's part of the bar adds that type's media types.
        self.assertEqual(
            self.ids({"genre_group": "scifi_fantasy", "tv_rollup": "true", "media_type": TV_TYPES}),
            {season_log.id},
        )

    def test_each_spelling_matches_only_its_own_type(self):
        # MAL's "Game" is Strategy & Puzzle; IGDB has no such genre.
        anime_log = self.log(self.item("anime", "1", genres=["Game"]))
        self.log(self.item("game", "2", genres=["Game"]))

        self.assertEqual(self.ids({"genre_group": "strategy_puzzle"}), {anime_log.id})

    def test_only_the_owner_spellings_are_used(self):
        stranger = get_user_model().objects.create_user(username="stranger", password="strong-password-123")
        self.log(self.item("movie", "1", genres=["Science Fiction"]), user=stranger)
        fantasy_log = self.log(self.item("movie", "2", genres=["Fantasy"]))
        scifi_log = self.log(self.item("movie", "3", genres=["Science Fiction"]))

        self.assertEqual(self.ids({"genre_group": "scifi_fantasy"}), {fantasy_log.id, scifi_log.id})
        # The filter matches exactly the (value, type) pairs found on the owner's logs.
        with patch(
            "api.services.genre_groups.raw_values_for",
            return_value=[("Fantasy", "movie")],
        ) as raw_values:
            self.assertEqual(self.ids({"genre_group": "scifi_fantasy"}), {fantasy_log.id})
        raw_values.assert_called_once_with(self.owner, "scifi_fantasy")

    def test_music_never_matches(self):
        film_log = self.log(self.item("movie", "1", genres=["Music"]))
        self.log(self.item("music", "2", genres=["Music"]))

        self.assertEqual(self.ids({"genre_group": "music_musicals"}), {film_log.id})

    def test_several_keys_match_any_and_an_unknown_key_matches_nothing(self):
        drama_log = self.log(self.item("movie", "1", genres=["Drama"]))
        horror_log = self.log(self.item("movie", "2", genres=["Horror"]))
        self.log(self.item("movie", "3", genres=["Comedy"]))

        self.assertEqual(self.ids({"genre_group": ["drama", "horror"]}), {drama_log.id, horror_log.id})
        self.assertEqual(self.ids({"genre_group": "not_a_group"}), set())


class OriginalLanguageTests(DrilldownBase):
    """original_language: the stored English name, exactly."""

    def test_matches_the_stored_name_exactly(self):
        japanese_log = self.log(self.item("movie", "1", language="Japanese"))
        english_log = self.log(self.item("movie", "2", language="English"))
        self.log(self.item("movie", "3"))

        self.assertEqual(self.ids({"original_language": "Japanese"}), {japanese_log.id})
        self.assertEqual(
            self.ids({"original_language": ["Japanese", "English"]}),
            {japanese_log.id, english_log.id},
        )
        self.assertEqual(self.ids({"original_language": "japanese"}), set())

    def test_season_logs_match_through_the_show(self):
        _show, season, episode = self.show(language="Japanese")
        season_log = self.log(season)
        episode_log = self.log(episode)

        self.assertEqual(
            self.ids({"original_language": "Japanese", "tv_rollup": "true"}),
            {season_log.id, episode_log.id},
        )
        self.assertEqual(self.ids({"original_language": "Japanese"}), set())

    def test_never_reads_the_spoken_language_facet(self):
        film = self.item("movie", "1")
        ItemFilterFacet.objects.create(item=film, facet_type=ItemFilterFacet.FacetType.LANGUAGE, value="Japanese")
        self.log(film)

        self.assertEqual(self.ids({"original_language": "Japanese"}), set())


class RatingBoundTests(DrilldownBase):
    """rating_min and rating_max: inclusive stored bounds (STAT-204)."""

    def test_bucket_bounds_return_the_bucket_logs_on_the_stored_scale(self):
        # S-09: 0.0, 7.4, 7.5 and 10 land in 0.5, 3.5, 4.0 and 5.0 stars.
        logs = {
            rating: self.log(self.item("movie", str(index)), rating=Decimal(rating))
            for index, rating in enumerate(["0.0", "7.4", "7.5", "10.0"])
        }
        self.log(self.item("tv", "tv1"), rating=Decimal("7.0"))

        self.assertEqual(self.ids({"rating_min": "0.0", "rating_max": "1.4"}), {logs["0.0"].id})
        self.assertEqual(self.ids({"rating_min": "6.5", "rating_max": "7.4", "media_type": "movie"}), {logs["7.4"].id})
        self.assertEqual(self.ids({"rating_min": "7.5", "rating_max": "8.4"}), {logs["7.5"].id})
        self.assertEqual(self.ids({"rating_min": "9.5", "rating_max": "10.0"}), {logs["10.0"].id})


class SkipBackfillTests(DrilldownBase):
    """skip_backfill: stored values only, no provider call."""

    def test_skip_backfill_makes_no_provider_call(self):
        film_log = self.log(self.item("movie", "1"))

        with patch(
            "app.providers.services.get_media_metadata",
            side_effect=AssertionError("provider called"),
        ) as provider:
            ids = self.ids({
                "skip_backfill": "true",
                "year": "1999",
                "genre": "Drama",
                "language": "English",
                "sort": "release_date",
                "tv_rollup": "true",
                "genre_group": "drama",
                "original_language": "English",
            })

        provider.assert_not_called()
        self.assertEqual(ids, set())
        self.assertTrue(DiaryEntry.objects.filter(id=film_log.id).exists())

    def test_without_skip_backfill_the_diary_still_backfills(self):
        self.log(self.item("movie", "1"))

        with patch("app.providers.services.get_media_metadata", return_value={}) as provider:
            response = self.client.get("/api/v1/diary/", {"year": "1999"})

        self.assertEqual(response.status_code, 200)
        provider.assert_called_once()


class UnchangedWithoutDrilldownTests(DrilldownBase):
    """Without the new parameters the diary is unchanged."""

    def test_plain_item_id_and_filters_behave_as_before(self):
        show, season, _episode = self.show(genres=["Drama"])
        show_log = self.log(show)
        self.log(season)

        response = self.client.get("/api/v1/diary/", {"item_id": show.id})

        self.assertEqual({row["id"] for row in response.data["results"]}, {show_log.id})
        self.assertEqual(self.ids({"genre": "Drama"}), {show_log.id})
