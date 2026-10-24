from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase

from app.models import MediaTypes, Sources
from app.providers import (
    igdb,
    mal,
    services,
    tmdb,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class ServicesTests(TestCase):
    """Test the services module functions."""

    @patch("app.providers.services.musicbrainz.person_page")
    def test_get_person_page_dispatches_musicbrainz(self, person_page):
        person_page.return_value = {"person_id": "artist-1"}

        result = services.get_person_page(Sources.MUSICBRAINZ.value, "artist-1")

        self.assertEqual(result, {"person_id": "artist-1"})
        person_page.assert_called_once_with("artist-1")

    @patch("app.providers.anilist.person_page")
    @patch("app.providers.services.mangaupdates.person_page")
    @patch("app.providers.services.mal.person_page")
    def test_get_person_page_dispatches_manga_people_sources(
        self,
        mal_person,
        mangaupdates_person,
        anilist_person,
    ):
        mal_person.return_value = {"person_id": "1"}
        mangaupdates_person.return_value = {"person_id": "2"}
        anilist_person.return_value = {"person_id": "3"}

        self.assertEqual(
            services.get_person_page(Sources.MAL.value, "1"),
            {"person_id": "1"},
        )
        self.assertEqual(
            services.get_person_page(Sources.MANGAUPDATES.value, "2"),
            {"person_id": "2"},
        )
        self.assertEqual(
            services.get_person_page("anilist", "3"),
            {"person_id": "3"},
        )

        mal_person.assert_called_once_with("1")
        mangaupdates_person.assert_called_once_with("2")
        anilist_person.assert_called_once_with("3")

    @patch("app.providers.services.session.get")
    def test_api_request_get(self, mock_get):
        """Test the api_request function with GET method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        mock_get.return_value = mock_response

        result = services.api_request(
            "TEST",
            "GET",
            "https://example.com/api",
            params={"param": "value"},
            timeout=2,
        )

        self.assertEqual(result, {"data": "test"})

        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        self.assertEqual(kwargs["url"], "https://example.com/api")
        self.assertEqual(kwargs["params"], {"param": "value"})
        self.assertEqual(kwargs["timeout"], 2)

    @patch("app.providers.services.session.post")
    def test_api_request_post(self, mock_post):
        """Test the api_request function with POST method."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": "test"}
        mock_post.return_value = mock_response

        result = services.api_request(
            "TEST",
            "POST",
            "https://example.com/api",
            params={"json_param": "value"},
            data={"form_data": "value"},
        )

        self.assertEqual(result, {"data": "test"})

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["url"], "https://example.com/api")
        self.assertEqual(kwargs["json"], {"json_param": "value"})
        self.assertEqual(kwargs["data"], {"form_data": "value"})
        self.assertIn("timeout", kwargs)

    @patch("app.providers.services.time.sleep")
    @patch("app.providers.services.session.get")
    def test_api_request_stops_after_rate_limit_retries(self, mock_get, mock_sleep):
        """Repeated rate limits stop after the bounded retry count."""
        mock_response = MagicMock()
        mock_response.status_code = 429  # Too many requests
        mock_response.headers = {"Retry-After": "5"}
        error = requests.exceptions.HTTPError("429 Too Many Requests")
        error.response = mock_response
        mock_response.raise_for_status.side_effect = error
        mock_get.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            services.api_request("TEST", "GET", "https://example.com/api")

        self.assertEqual(mock_get.call_count, services.RATE_LIMIT_MAX_RETRIES + 1)
        self.assertEqual(mock_sleep.call_count, services.RATE_LIMIT_MAX_RETRIES)

    @patch("app.providers.services.time.sleep")
    @patch("app.providers.services.session.post")
    def test_rate_limit_longer_than_max_wait_fails_at_once(self, mock_post, mock_sleep):
        """A daily cap that asks for hours fails without sleeping or retrying."""
        mock_post.side_effect = self._rate_limited_then_ok("68281")

        with self.assertRaises(requests.exceptions.HTTPError):
            services.api_request(
                Sources.HARDCOVER.value,
                "POST",
                "https://api.hardcover.app/v1/graphql",
                params={"query": "{}"},
            )

        mock_post.assert_called_once()
        mock_sleep.assert_not_called()

    def _rate_limited_then_ok(self, retry_after):
        limited = MagicMock(status_code=429, headers={"Retry-After": retry_after})
        error = requests.exceptions.HTTPError("429 Too Many Requests")
        error.response = limited
        limited.raise_for_status.side_effect = error
        ok = MagicMock()
        ok.json.return_value = {"data": {}}
        return [limited, ok]

    @patch("app.providers.services.time.sleep")
    @patch("app.providers.services.session.post")
    def test_hardcover_rate_limit_waits_only_as_long_as_asked(self, mock_post, mock_sleep):
        mock_post.side_effect = self._rate_limited_then_ok("1")

        services.api_request(
            Sources.HARDCOVER.value,
            "POST",
            "https://api.hardcover.app/v1/graphql",
            params={"query": "{}"},
        )

        mock_sleep.assert_called_once_with(1)

    @patch("app.providers.services.time.sleep")
    @patch("app.providers.services.session.get")
    def test_other_providers_keep_rate_limit_padding(self, mock_get, mock_sleep):
        mock_get.side_effect = self._rate_limited_then_ok("1")

        services.api_request(Sources.MAL.value, "GET", "https://api.myanimelist.net/v2/anime/1")

        mock_sleep.assert_called_once_with(1 + services.RATE_LIMIT_RETRY_PADDING_SECONDS)

    def test_hardcover_429_does_not_mark_the_minute_budget_spent(self):
        for session in (services.session, services.person_search_session):
            adapter = session.get_adapter("https://api.hardcover.app/v1/graphql")
            self.assertEqual(tuple(adapter.limit_statuses), ())

    @patch("app.providers.services.time.sleep")
    @patch("app.providers.services.session.get")
    def test_api_request_can_skip_rate_limit_retries(self, mock_get, mock_sleep):
        mock_response = MagicMock(status_code=429, headers={"Retry-After": "60"})
        error = requests.exceptions.HTTPError("429 Too Many Requests")
        error.response = mock_response
        mock_response.raise_for_status.side_effect = error
        mock_get.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            services.api_request(
                "TEST",
                "GET",
                "https://example.com/api",
                retry_rate_limits=False,
            )

        mock_get.assert_called_once()
        mock_sleep.assert_not_called()

    @patch("app.providers.igdb.cache.delete")
    def test_handle_error_igdb_unauthorized(
        self,
        mock_cache_delete,
    ):
        """Test the handle_error function with IGDB unauthorized error."""
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized

        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response

        result = igdb.handle_error(error)

        mock_cache_delete.assert_called_once_with("igdb_access_token")

        self.assertEqual(result, {"retry": True})

    def test_handle_error_igdb_bad_request(self):
        """Test the handle_error function with IGDB bad request error."""
        mock_response = MagicMock()
        mock_response.status_code = 400  # Bad Request
        mock_response.json.return_value = {"message": "Invalid query"}

        error = requests.exceptions.HTTPError("400 Bad Request")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            igdb.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.IGDB.value)

    @patch("app.providers.igdb.cache")
    @patch("app.providers.igdb.services.api_request")
    def test_igdb_token_refused_with_unauthorized_raises_provider_error(
        self,
        mock_api_request,
        mock_cache,
    ):
        """Test a 401 from the IGDB token endpoint raises a provider error."""
        mock_cache.get.return_value = None
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized

        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response
        mock_api_request.side_effect = error

        with self.assertRaises(services.ProviderAPIError) as cm:
            igdb.get_access_token()

        self.assertEqual(cm.exception.provider, Sources.IGDB.value)
        mock_cache.set.assert_not_called()

    def test_handle_error_tmdb_unauthorized(self):
        """Test the handle_error function with TMDB unauthorized error."""
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized
        mock_response.json.return_value = {"status_message": "Invalid API key"}

        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            tmdb.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.TMDB.value)

    def test_handle_error_mal_forbidden(self):
        """Test the handle_error function with MAL forbidden error."""
        mock_response = MagicMock()
        mock_response.status_code = 403  # Forbidden
        mock_response.json.return_value = {"message": "Forbidden"}

        error = requests.exceptions.HTTPError("403 Forbidden")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            mal.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.MAL.value)

    def test_provider_api_error_without_response(self):
        """Test ProviderAPIError with network errors that have no response."""
        error = requests.exceptions.ConnectionError("Connection aborted")

        exception = services.ProviderAPIError(Sources.OPENLIBRARY.value, error)

        self.assertEqual(exception.provider, Sources.OPENLIBRARY.value)
        self.assertIsNone(exception.status_code)
        self.assertIn("Open Library API (network error)", str(exception))

    @patch("app.providers.mal.anime")
    def test_get_media_metadata_anime(self, mock_anime):
        """Test the get_media_metadata function for anime."""
        mock_anime.return_value = {"title": "Test Anime"}

        result = services.get_media_metadata(
            MediaTypes.ANIME.value,
            "1",
            Sources.MAL.value,
        )

        self.assertEqual(result, {"title": "Test Anime"})

        mock_anime.assert_called_once_with("1")

    @patch("app.providers.mangaupdates.manga")
    def test_get_media_metadata_manga_mangaupdates(self, mock_manga):
        """Test the get_media_metadata function for manga from MangaUpdates."""
        mock_manga.return_value = {"title": "Test Manga"}

        result = services.get_media_metadata(
            MediaTypes.MANGA.value,
            "1",
            Sources.MANGAUPDATES.value,
        )

        self.assertEqual(result, {"title": "Test Manga"})

        mock_manga.assert_called_once_with("1")

    @patch("app.providers.mal.manga")
    def test_get_media_metadata_manga_mal(self, mock_manga):
        """Test the get_media_metadata function for manga from MAL."""
        mock_manga.return_value = {"title": "Test Manga"}

        result = services.get_media_metadata(
            MediaTypes.MANGA.value,
            "1",
            Sources.MAL.value,
        )

        self.assertEqual(result, {"title": "Test Manga"})

        mock_manga.assert_called_once_with("1")

    @patch("app.providers.tmdb.tv")
    def test_get_media_metadata_tv(self, mock_tv):
        """Test the get_media_metadata function for TV shows."""
        mock_tv.return_value = {"title": "Test TV"}

        result = services.get_media_metadata(
            MediaTypes.TV.value,
            "1",
            Sources.TMDB.value,
        )

        self.assertEqual(result, {"title": "Test TV"})

        mock_tv.assert_called_once_with("1")

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_get_media_metadata_tv_with_seasons(self, mock_tv_with_seasons):
        """Test the get_media_metadata function for TV shows with seasons."""
        mock_tv_with_seasons.return_value = {"title": "Test TV with Seasons"}

        result = services.get_media_metadata(
            "tv_with_seasons",
            "1",
            Sources.TMDB.value,
            season_numbers=[1, 2],
        )

        self.assertEqual(result, {"title": "Test TV with Seasons"})

        mock_tv_with_seasons.assert_called_once_with("1", [1, 2])

    @patch("app.providers.tmdb.tv_with_seasons")
    def test_get_media_metadata_season(self, mock_tv_with_seasons):
        """Test the get_media_metadata function for TV seasons."""
        mock_tv_with_seasons.return_value = {
            "season/1": {"title": "Test Season"},
        }

        result = services.get_media_metadata(
            MediaTypes.SEASON.value,
            "1",
            Sources.TMDB.value,
            season_numbers=[1],
        )

        self.assertEqual(result, {"title": "Test Season"})

        mock_tv_with_seasons.assert_called_once_with("1", [1])

    @patch("app.providers.tmdb.episode")
    def test_get_media_metadata_episode(self, mock_episode):
        """Test the get_media_metadata function for TV episodes."""
        mock_episode.return_value = {"title": "Test Episode"}

        result = services.get_media_metadata(
            MediaTypes.EPISODE.value,
            "1",
            Sources.TMDB.value,
            season_numbers=[1],
            episode_number="2",
        )

        self.assertEqual(result, {"title": "Test Episode"})

        mock_episode.assert_called_once_with("1", 1, "2")

    @patch("app.providers.tmdb.movie")
    def test_get_media_metadata_movie(self, mock_movie):
        """Test the get_media_metadata function for movies."""
        mock_movie.return_value = {"title": "Test Movie"}

        result = services.get_media_metadata(
            MediaTypes.MOVIE.value,
            "1",
            Sources.TMDB.value,
        )

        self.assertEqual(result, {"title": "Test Movie"})

        mock_movie.assert_called_once_with("1")

    @patch("app.providers.igdb.game")
    def test_get_media_metadata_game(self, mock_game):
        """Test the get_media_metadata function for games."""
        mock_game.return_value = {"title": "Test Game"}

        result = services.get_media_metadata(
            MediaTypes.GAME.value,
            "1",
            Sources.IGDB.value,
        )

        self.assertEqual(result, {"title": "Test Game"})

        mock_game.assert_called_once_with("1")

    @patch("app.providers.comicvine.comic")
    def test_get_media_metadata_comic(self, mock_comic):
        """Test the get_media_metadata function for comics."""
        mock_comic.return_value = {"title": "Test Comic"}

        result = services.get_media_metadata(
            MediaTypes.COMIC.value,
            "1",
            Sources.COMICVINE.value,
        )

        self.assertEqual(result, {"title": "Test Comic"})

        mock_comic.assert_called_once_with("1")

    @patch("app.providers.musicbrainz.music")
    def test_get_media_metadata_music(self, mock_music):
        mock_music.return_value = {"title": "Year Zero"}

        result = services.get_media_metadata(
            MediaTypes.MUSIC.value,
            "release-group-id",
            Sources.MUSICBRAINZ.value,
        )

        self.assertEqual(result, {"title": "Year Zero"})
        mock_music.assert_called_once_with("release-group-id")

    @patch("app.providers.openlibrary.book")
    def test_get_media_metadata_book(self, mock_book):
        """Test the get_media_metadata function for books."""
        mock_book.return_value = {"title": "Test Book"}

        result = services.get_media_metadata(
            MediaTypes.BOOK.value,
            "1",
            Sources.OPENLIBRARY.value,
        )

        self.assertEqual(result, {"title": "Test Book"})

        mock_book.assert_called_once_with("1")

    @patch("app.providers.manual.metadata")
    def test_get_media_metadata_manual(self, mock_metadata):
        """Test the get_media_metadata function for manual media."""
        mock_metadata.return_value = {"title": "Test Manual"}

        result = services.get_media_metadata(
            MediaTypes.MOVIE.value,
            "1",
            Sources.MANUAL.value,
        )

        self.assertEqual(result, {"title": "Test Manual"})

        mock_metadata.assert_called_once_with("1", MediaTypes.MOVIE.value)

    @patch("app.providers.manual.season")
    def test_get_media_metadata_manual_season(self, mock_season):
        """Test the get_media_metadata function for manual seasons."""
        mock_season.return_value = {"title": "Test Manual Season"}

        result = services.get_media_metadata(
            MediaTypes.SEASON.value,
            "1",
            Sources.MANUAL.value,
            season_numbers=[1],
        )

        self.assertEqual(result, {"title": "Test Manual Season"})

        mock_season.assert_called_once_with("1", 1)

    @patch("app.providers.manual.episode")
    def test_get_media_metadata_manual_episode(self, mock_episode):
        """Test the get_media_metadata function for manual episodes."""
        mock_episode.return_value = {"title": "Test Manual Episode"}

        result = services.get_media_metadata(
            MediaTypes.EPISODE.value,
            "1",
            Sources.MANUAL.value,
            season_numbers=[1],
            episode_number="2",
        )

        self.assertEqual(result, {"title": "Test Manual Episode"})

        mock_episode.assert_called_once_with("1", 1, "2")

    @patch("app.providers.tmdb.episode")
    def test_get_media_metadata_tmdb_episode_not_found(self, mock_episode):
        """Test the get_media_metadata function for TMDB episodes that don't exist."""
        mock_response = type(
            "Response",
            (),
            {"status_code": 404, "text": "Episode not found"},
        )()
        mock_error = type("Error", (), {"response": mock_response})()
        mock_episode.side_effect = services.ProviderAPIError(
            Sources.TMDB.value,
            mock_error,
        )

        with self.assertRaises(services.ProviderAPIError) as cm:
            services.get_media_metadata(
                MediaTypes.EPISODE.value,
                "1396",
                Sources.TMDB.value,
                season_numbers=[1],
                episode_number="3",
            )

        self.assertEqual(cm.exception.provider, Sources.TMDB.value)

        mock_episode.assert_called_once_with("1396", 1, "3")

    @patch("app.providers.hardcover.book")
    def test_get_media_metadata_hardcover_book(self, mock_book):
        """Test the get_media_metadata function for books from Hardcover."""
        mock_book.return_value = {"title": "Test Hardcover Book"}

        result = services.get_media_metadata(
            MediaTypes.BOOK.value,
            "1",
            Sources.HARDCOVER.value,
        )

        self.assertEqual(result, {"title": "Test Hardcover Book"})

        mock_book.assert_called_once_with("1")

    @patch("app.providers.mal.search")
    def test_search_anime(self, mock_search):
        """Test the search function for anime."""
        mock_search.return_value = [{"title": "Test Anime"}]

        result = services.search(MediaTypes.ANIME.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Anime"}])

        mock_search.assert_called_once_with(MediaTypes.ANIME.value, "test", 1)

    @patch("app.providers.mangaupdates.search")
    def test_search_manga_mangaupdates(self, mock_search):
        """Test the search function for manga from MangaUpdates."""
        mock_search.return_value = [{"title": "Test Manga"}]

        result = services.search(
            MediaTypes.MANGA.value,
            "test",
            1,
            source=Sources.MANGAUPDATES.value,
        )

        self.assertEqual(result, [{"title": "Test Manga"}])

        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.mal.search")
    def test_search_manga_mal(self, mock_search):
        """Test the search function for manga from MAL."""
        mock_search.return_value = [{"title": "Test Manga"}]

        result = services.search(MediaTypes.MANGA.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Manga"}])

        mock_search.assert_called_once_with(MediaTypes.MANGA.value, "test", 1)

    @patch("app.providers.tmdb.search")
    def test_search_tv(self, mock_search):
        """Test the search function for TV shows."""
        mock_search.return_value = [{"title": "Test TV"}]

        result = services.search(MediaTypes.TV.value, "test", 1)

        self.assertEqual(result, [{"title": "Test TV"}])

        mock_search.assert_called_once_with(MediaTypes.TV.value, "test", 1)

    @patch("app.providers.tmdb.search")
    def test_search_movie(self, mock_search):
        """Test the search function for movies."""
        mock_search.return_value = [{"title": "Test Movie"}]

        result = services.search(MediaTypes.MOVIE.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Movie"}])

        mock_search.assert_called_once_with(MediaTypes.MOVIE.value, "test", 1)

    @patch("app.providers.igdb.search")
    def test_search_game(self, mock_search):
        """Test the search function for games."""
        mock_search.return_value = [{"title": "Test Game"}]

        result = services.search(MediaTypes.GAME.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Game"}])

        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.hardcover.search")
    def test_search_hardcover_book(self, mock_search):
        """Test the search function for books from Hardcover."""
        mock_search.return_value = [{"title": "Test Hardcover Book"}]

        result = services.search(
            MediaTypes.BOOK.value,
            "test",
            1,
            source=Sources.HARDCOVER.value,
        )

        self.assertEqual(result, [{"title": "Test Hardcover Book"}])

        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.openlibrary.search")
    def test_search_openlibrary_book(self, mock_search):
        """Test the search function for books."""
        mock_search.return_value = [{"title": "Test Book"}]

        result = services.search(
            MediaTypes.BOOK.value,
            "test",
            1,
            source=Sources.OPENLIBRARY.value,
        )

        self.assertEqual(result, [{"title": "Test Book"}])

        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.comicvine.search")
    def test_search_comic(self, mock_search):
        """Test the search function for comics."""
        mock_search.return_value = [{"title": "Test Comic"}]

        result = services.search(MediaTypes.COMIC.value, "test", 1)

        self.assertEqual(result, [{"title": "Test Comic"}])

        mock_search.assert_called_once_with("test", 1)

    @patch("app.providers.services.mal.discover_anime")
    def test_discover_mal_anime_genre(self, discover):
        discover.return_value = {"results": [{"title": "Cowboy Bebop"}]}

        result = services.discover(
            MediaTypes.ANIME.value,
            source=Sources.MAL.value,
            page=2,
            page_size=25,
            genre="Action",
        )

        self.assertEqual(result, discover.return_value)
        discover.assert_called_once_with(
            page=2,
            page_size=25,
            genre="Action",
        )

    def test_discover_mal_anime_rejects_other_filters(self):
        with self.assertRaisesMessage(
            ValueError,
            "year discovery is not supported for anime.",
        ):
            services.discover(
                MediaTypes.ANIME.value,
                source=Sources.MAL.value,
                genre="Action",
                year="2024",
            )
        with self.assertRaisesMessage(
            ValueError,
            "platform discovery is only supported for games.",
        ):
            services.discover(
                MediaTypes.ANIME.value,
                source=Sources.MAL.value,
                genre="Action",
                platform="TV",
            )

    @patch("app.providers.services.mal.discover_manga")
    def test_discover_mal_manga_genre(self, discover):
        discover.return_value = {"results": [{"title": "Berserk"}]}

        result = services.discover(
            MediaTypes.MANGA.value,
            source=Sources.MAL.value,
            page=2,
            page_size=25,
            genre="Action",
        )

        self.assertEqual(result, discover.return_value)
        discover.assert_called_once_with(
            page=2,
            page_size=25,
            genre="Action",
        )

    def test_discover_mal_manga_rejects_other_filters(self):
        with self.assertRaisesMessage(
            ValueError,
            "year discovery is not supported for manga.",
        ):
            services.discover(
                MediaTypes.MANGA.value,
                source=Sources.MAL.value,
                genre="Action",
                year="2024",
            )
        with self.assertRaisesMessage(
            ValueError,
            "platform discovery is only supported for games.",
        ):
            services.discover(
                MediaTypes.MANGA.value,
                source=Sources.MAL.value,
                genre="Action",
                platform="Kindle",
            )

    @patch("app.providers.musicbrainz.search")
    def test_search_music(self, mock_search):
        mock_search.return_value = [{"title": "Year Zero"}]

        result = services.search(MediaTypes.MUSIC.value, "year zero", 1)

        self.assertEqual(result, [{"title": "Year Zero"}])
        mock_search.assert_called_once_with("year zero", 1)

    @patch("app.providers.services.musicbrainz.discover")
    def test_discover_musicbrainz_genre(self, discover):
        discover.return_value = {"results": [{"title": "Year Zero"}]}

        result = services.discover(
            MediaTypes.MUSIC.value,
            source=Sources.MUSICBRAINZ.value,
            page=2,
            page_size=25,
            genre="Industrial Rock",
        )

        self.assertEqual(result, discover.return_value)
        discover.assert_called_once_with(
            page=2,
            page_size=25,
            genre="Industrial Rock",
        )

    def test_discover_musicbrainz_requires_genre(self):
        with self.assertRaisesMessage(
            ValueError,
            "genre is required for MusicBrainz discovery.",
        ):
            services.discover(
                MediaTypes.MUSIC.value,
                source=Sources.MUSICBRAINZ.value,
                genre="   ",
            )

    def test_discover_musicbrainz_rejects_year_and_platform(self):
        with self.assertRaisesMessage(
            ValueError,
            "year discovery is not supported for music.",
        ):
            services.discover(
                MediaTypes.MUSIC.value,
                source=Sources.MUSICBRAINZ.value,
                genre="Rock",
                year="2007",
            )
        with self.assertRaisesMessage(
            ValueError,
            "platform discovery is only supported for games.",
        ):
            services.discover(
                MediaTypes.MUSIC.value,
                source=Sources.MUSICBRAINZ.value,
                genre="Rock",
                platform="Spotify",
            )


class MusicBrainzSessionTests(TestCase):
    """A search waiting for MusicBrainz drops out once its person searches again."""

    url = "https://musicbrainz.org/ws/2/release-group?query=dark"

    def setUp(self):
        self.session = services.MusicBrainzSession(per_second=1)
        self.addCleanup(self.session.close)

    def searching(self, superseded):
        token = services.search_superseded.set(superseded)
        self.addCleanup(services.search_superseded.reset, token)

    @patch("requests.Session.send")
    def test_waiting_search_gives_up_its_turn_once_replaced(self, send):
        self.session.limiter.try_acquire("musicbrainz.org")  # this second's turn is taken
        self.searching(MagicMock(side_effect=[False, True]))

        with self.assertRaises(services.SearchSuperseded):
            self.session.get(self.url)

        send.assert_not_called()

    @patch("requests.Session.send")
    def test_current_search_takes_one_turn(self, send):
        send.return_value = MagicMock(status_code=200)
        self.searching(lambda: False)

        with patch.object(
            self.session.limiter,
            "try_acquire",
            wraps=self.session.limiter.try_acquire,
        ) as acquire:
            self.session.get(self.url)

        acquire.assert_called_once()
        send.assert_called_once()
