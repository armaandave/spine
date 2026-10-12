import json
import os
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from app.models import Episode, Item, MediaTypes, Sources
from app.providers import (
    comicvine,
    hardcover,
    igdb,
    mal,
    mangaupdates,
    manual,
    openlibrary,
    services,
    steam,
    steamgriddb,
    tmdb,
)

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
RUN_PROVIDER_TESTS = os.environ.get("RUN_PROVIDER_TESTS") == "1"
requires_provider_network = unittest.skipUnless(
    RUN_PROVIDER_TESTS,
    "Set RUN_PROVIDER_TESTS=1 to run live provider API tests.",
)


class Metadata(TestCase):
    """Test the external API calls for media details."""

    @requires_provider_network
    def test_anime(self):
        """Test the metadata method for anime."""
        response = mal.anime("1")
        self.assertEqual(response["title"], "Cowboy Bebop")
        self.assertEqual(response["details"]["start_date"], "1998-04-03")
        self.assertEqual(response["details"]["status"], "Finished")
        self.assertEqual(response["details"]["episodes"], 26)

    @patch("requests.Session.get")
    def test_anime_unknown(self, mock_data):
        """Test the metadata method for anime with mostly unknown data."""
        with Path(mock_path / "metadata_anime_unknown.json").open() as file:
            anime_response = json.load(file)
        mock_data.return_value.json.return_value = anime_response
        mock_data.return_value.status_code = 200

        # anime without picture, synopsis, duration, or number of episodes
        response = mal.anime("0")
        self.assertEqual(response["title"], "Unknown Example")
        self.assertEqual(response["image"], settings.IMG_NONE)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["details"]["episodes"], None)
        self.assertEqual(response["details"]["runtime"], None)

    @requires_provider_network
    def test_manga(self):
        """Test the metadata method for manga."""
        response = mal.manga("1")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["details"]["start_date"], "1994-12-05")
        self.assertEqual(response["details"]["status"], "Finished")
        self.assertEqual(response["details"]["number_of_chapters"], 162)

    @requires_provider_network
    def test_mangaupdates(self):
        """Test the metadata method for manga from mangaupdates."""
        response = mangaupdates.manga("72274276213")
        self.assertEqual(response["title"], "Monster")
        self.assertEqual(response["details"]["year"], "1994")
        self.assertEqual(response["details"]["format"], "Manga")

    @requires_provider_network
    def test_tv(self):
        """Test the metadata method for TV shows."""
        response = tmdb.tv("1396")
        self.assertEqual(response["title"], "Breaking Bad")
        self.assertEqual(response["details"]["first_air_date"], "2008-01-20")
        self.assertEqual(response["details"]["status"], "Ended")
        self.assertEqual(response["details"]["episodes"], 62)

    def test_tmdb_crew_keeps_profile_image(self):
        """Test grouped TMDB crew credits keep person images."""
        credits = {
            "crew": [
                {"id": 1, "name": "Denis Villeneuve", "job": "Screenplay", "profile_path": "/denis.jpg"},
                {"id": 1, "name": "Denis Villeneuve", "job": "Director", "profile_path": "/denis.jpg"},
            ]
        }

        crew = tmdb.get_crew(credits)

        self.assertEqual(crew[0]["image"], "https://image.tmdb.org/t/p/w500/denis.jpg")

    def test_tmdb_directors_and_creators_preserve_order_and_deduplicate(self):
        credits = {
            "crew": [
                {"id": 1, "name": " First Director ", "job": "Director"},
                {"id": 2, "name": "Second Director", "job": "Director"},
                {"id": 1, "name": "First Director", "job": "Director"},
                {"id": 3, "name": "Writer", "job": "Writer"},
                {"id": 4, "name": "", "job": "Director"},
            ]
        }
        creators = [
            {"id": 10, "name": "Creator One"},
            {"id": 11, "name": "Creator Two"},
            {"id": 12, "name": "Creator Three"},
            {"id": 13, "name": "Creator Four"},
            {"id": 14, "name": "Creator Five"},
            {"id": 10, "name": "Creator One"},
            {"id": 15, "name": ""},
        ]

        self.assertEqual(
            tmdb.get_directors(credits),
            [{"name": "First Director", "id": "1"}, {"name": "Second Director", "id": "2"}],
        )
        self.assertEqual(
            tmdb.get_creators(creators),
            [
                {"name": "Creator One", "id": "10"},
                {"name": "Creator Two", "id": "11"},
                {"name": "Creator Three", "id": "12"},
                {"name": "Creator Four", "id": "13"},
                {"name": "Creator Five", "id": "14"},
            ],
        )

    def test_tmdb_person_credit_role_groups_common_jobs(self):
        self.assertEqual(tmdb._person_credit_role("Screenplay", "Writing"), "Writer")
        self.assertEqual(tmdb._person_credit_role("Executive Producer", "Production"), "Producer")
        self.assertEqual(tmdb._person_credit_role("Director", "Directing"), "Director")

    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.services.api_request")
    def test_tmdb_season_backdrops_use_episode_stills(self, mock_api_request, mock_tv_with_seasons):
        cache.clear()
        mock_tv_with_seasons.return_value = {
            "season/1": {
                "episodes": [
                    {"episode_number": 1},
                    {"episode_number": 2},
                ],
            },
        }
        mock_api_request.side_effect = [
            {
                "stills": [
                    {
                        "file_path": "/still-1.jpg",
                        "width": 1920,
                        "height": 1080,
                        "aspect_ratio": 1.778,
                        "vote_average": 8,
                        "vote_count": 4,
                    },
                ],
            },
            {
                "stills": [
                    {"file_path": "/still-1.jpg", "width": 1920, "height": 1080},
                    {"file_path": "/still-2.jpg", "width": 1280, "height": 720},
                ],
            },
        ]

        backdrops = tmdb.get_season_backdrop_images("1399", 1)

        self.assertEqual(
            [backdrop["url"] for backdrop in backdrops],
            [
                "https://image.tmdb.org/t/p/original/still-1.jpg",
                "https://image.tmdb.org/t/p/original/still-2.jpg",
            ],
        )
        self.assertEqual(backdrops[0]["thumbnail_url"], "https://image.tmdb.org/t/p/w780/still-1.jpg")
        self.assertEqual(backdrops[0]["episode_number"], 1)
        self.assertEqual(backdrops[1]["episode_number"], 2)

    @patch("app.providers.tmdb.services.api_request")
    def test_tmdb_episode_backdrops_use_only_selected_episode_stills(self, mock_api_request):
        cache.clear()
        mock_api_request.return_value = {
            "stills": [
                {
                    "file_path": "/alternate.jpg",
                    "width": 1280,
                    "height": 720,
                    "aspect_ratio": 1.778,
                    "vote_average": 7,
                    "vote_count": 3,
                },
                {
                    "file_path": "/preferred.jpg",
                    "width": 1920,
                    "height": 1080,
                    "aspect_ratio": 1.778,
                    "vote_average": 9,
                    "vote_count": 12,
                    "iso_639_1": None,
                },
            ],
        }

        backdrops = tmdb.get_episode_backdrop_images("1399", 1, 2)

        self.assertEqual(
            [backdrop["url"] for backdrop in backdrops],
            [
                "https://image.tmdb.org/t/p/original/preferred.jpg",
                "https://image.tmdb.org/t/p/original/alternate.jpg",
            ],
        )
        self.assertEqual(backdrops[0]["thumbnail_url"], "https://image.tmdb.org/t/p/w780/preferred.jpg")
        self.assertEqual(backdrops[0]["episode_number"], 2)
        request_url = mock_api_request.call_args.args[2]
        self.assertTrue(request_url.endswith("/tv/1399/season/1/episode/2/images"))
        self.assertNotIn("language", mock_api_request.call_args.kwargs["params"])

    @patch("app.providers.tmdb.timezone.localdate")
    @patch("app.providers.tmdb.services.api_request")
    def test_tv_changes(self, mock_api_request, mock_localdate):
        """Test fetching changed TV ids from TMDB."""
        mock_localdate.return_value = date(2026, 4, 5)
        mock_api_request.return_value = {
            "results": [{"id": 1}, {"id": 2}],
            "total_pages": 1,
        }

        result = tmdb.tv_changes()

        self.assertEqual(result, {"1", "2"})
        _, kwargs = mock_api_request.call_args
        self.assertEqual(kwargs["params"]["start_date"], "2026-04-02")
        self.assertEqual(kwargs["params"]["end_date"], "2026-04-05")
        self.assertEqual(kwargs["params"]["page"], 1)

    @patch("app.providers.tmdb.timezone.localdate")
    @patch("app.providers.tmdb.services.api_request")
    def test_tv_changes_across_pages(self, mock_api_request, mock_localdate):
        """Test TMDB TV changes pagination and deduplication."""
        mock_localdate.return_value = date(2026, 4, 5)
        mock_api_request.side_effect = [
            {
                "results": [{"id": 1}, {"id": 2}],
                "total_pages": 2,
            },
            {
                "results": [{"id": 2}, {"id": 3}],
                "total_pages": 2,
            },
        ]

        result = tmdb.tv_changes()

        self.assertEqual(result, {"1", "2", "3"})
        self.assertEqual(mock_api_request.call_count, 2)

    @patch("app.providers.tmdb.services.api_request")
    def test_title_logo_prefers_language_then_votes(self, mock_api_request):
        """Test TMDB title logo selection."""
        cache.clear()
        mock_api_request.return_value = {
            "logos": [
                {
                    "file_path": "/fallback.png",
                    "width": 2000,
                    "height": 500,
                    "aspect_ratio": 4,
                    "vote_average": 10,
                    "vote_count": 20,
                    "iso_639_1": None,
                },
                {
                    "file_path": "/low-votes.png",
                    "width": 1000,
                    "height": 400,
                    "aspect_ratio": 2.5,
                    "vote_average": 7,
                    "vote_count": 1,
                    "iso_639_1": settings.TMDB_LANG,
                },
                {
                    "file_path": "/best.png",
                    "width": 1493,
                    "height": 482,
                    "aspect_ratio": 3.1,
                    "vote_average": 7,
                    "vote_count": 5,
                    "iso_639_1": settings.TMDB_LANG,
                },
            ],
        }

        logo = tmdb.get_title_logo("550", MediaTypes.MOVIE.value)

        self.assertEqual(
            logo,
            {
                "url": "https://image.tmdb.org/t/p/w500/best.png",
                "width": 1493,
                "height": 482,
                "aspect_ratio": 3.1,
            },
        )
        self.assertEqual(mock_api_request.call_count, 1)

    @patch("app.providers.tmdb.services.api_request")
    def test_title_logos_return_all_languages_sorted_and_cached(self, mock_api_request):
        """Test TMDB title logo option normalization, ordering, and caching."""
        cache.clear()
        mock_api_request.return_value = {
            "logos": [
                {
                    "file_path": "/smaller.png",
                    "width": 1000,
                    "height": 400,
                    "aspect_ratio": 2.5,
                    "vote_average": 8,
                    "vote_count": 4,
                    "iso_639_1": "fr",
                },
                {
                    "file_path": "/best.png",
                    "width": 1600,
                    "height": 500,
                    "aspect_ratio": 3.2,
                    "vote_average": 8,
                    "vote_count": 4,
                    "iso_639_1": "en",
                },
                {
                    "file_path": "/neutral.png",
                    "width": 1200,
                    "height": 400,
                    "aspect_ratio": 3,
                    "vote_average": 7,
                    "vote_count": 10,
                    "iso_639_1": None,
                },
            ],
        }

        logos = tmdb.get_title_logos("550", MediaTypes.MOVIE.value)
        cached = tmdb.get_title_logos("550", MediaTypes.MOVIE.value)

        self.assertEqual([logo["language"] for logo in logos], ["en", "fr", None])
        self.assertEqual(logos[0]["url"], "https://image.tmdb.org/t/p/w500/best.png")
        self.assertEqual(logos[0]["thumbnail_url"], "https://image.tmdb.org/t/p/w300/best.png")
        self.assertEqual(cached, logos)
        mock_api_request.assert_called_once()

    @patch("app.providers.tmdb.timezone.localdate")
    @patch("app.providers.tmdb.services.api_request")
    def test_movie_changes(self, mock_api_request, mock_localdate):
        """Test fetching changed movie ids from TMDB."""
        mock_localdate.return_value = date(2026, 4, 5)
        mock_api_request.return_value = {
            "results": [{"id": 10}, {"id": 20}],
            "total_pages": 1,
        }

        result = tmdb.movie_changes()

        self.assertEqual(result, {"10", "20"})
        _, kwargs = mock_api_request.call_args
        self.assertEqual(kwargs["params"]["start_date"], "2026-04-02")
        self.assertEqual(kwargs["params"]["end_date"], "2026-04-05")
        self.assertEqual(kwargs["params"]["page"], 1)

    @patch("app.providers.tmdb.timezone.localdate")
    @patch("app.providers.tmdb.services.api_request")
    def test_movie_changes_across_pages(self, mock_api_request, mock_localdate):
        """Test TMDB movie changes pagination and deduplication."""
        mock_localdate.return_value = date(2026, 4, 5)
        mock_api_request.side_effect = [
            {
                "results": [{"id": 10}, {"id": 20}],
                "total_pages": 2,
            },
            {
                "results": [{"id": 20}, {"id": 30}],
                "total_pages": 2,
            },
        ]

        result = tmdb.movie_changes()

        self.assertEqual(result, {"10", "20", "30"})
        self.assertEqual(mock_api_request.call_count, 2)

    def test_tmdb_process_episodes(self):
        """Test the process_episodes function for TMDB episodes."""
        Item.objects.create(
            media_id="5",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Process Episodes Test",
            image="http://example.com/process.jpg",
        )

        Item.objects.create(
            media_id="5",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Process Episodes Test",
            image="http://example.com/process_s1.jpg",
            season_number=1,
        )

        for i in range(1, 4):
            Item.objects.create(
                media_id="5",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Process Episode {i}",
                image=f"http://example.com/process_s1e{i}.jpg",
                season_number=1,
                episode_number=i,
            )

        season_metadata = {
            "media_id": "1396",  # Breaking Bad
            "season_number": 1,
            "episodes": [
                {
                    "episode_number": 1,
                    "air_date": "2008-01-20",
                    "still_path": "/path/to/still1.jpg",
                    "name": "Pilot",
                    "overview": "overview of the episode",
                    "runtime": 90,
                },
                {
                    "episode_number": 2,
                    "air_date": "2008-01-27",
                    "still_path": "/path/to/still2.jpg",
                    "name": "Cat's in the Bag...",
                    "overview": "overview of the episode",
                    "runtime": 23,
                },
                {
                    "episode_number": 3,
                    "air_date": "2008-02-10",
                    "still_path": "/path/to/still3.jpg",
                    "name": "...And the Bag's in the River",
                    "overview": "overview of the episode",
                    "runtime": 23,
                },
            ],
        }
        episode_item_1 = Item.objects.get(
            media_id="5",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )
        episode_1 = Episode(item=episode_item_1)

        episode_item_2 = Item.objects.get(
            media_id="5",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
        )
        episode_2 = Episode(item=episode_item_2)

        episodes_in_db = [episode_1, episode_2]

        # Call process_episodes
        result = tmdb.process_episodes(season_metadata, episodes_in_db)

        self.assertEqual(len(result), 3)

        self.assertEqual(result[0]["episode_number"], 1)
        self.assertEqual(result[0]["title"], "Pilot")
        self.assertEqual(result[0]["air_date"], "2008-01-20")
        self.assertEqual(result[0]["runtime"], "1h 30m")
        self.assertTrue(result[0]["history"], [episode_1])

        self.assertEqual(result[1]["episode_number"], 2)
        self.assertEqual(result[1]["title"], "Cat's in the Bag...")
        self.assertEqual(result[1]["air_date"], "2008-01-27")
        self.assertEqual(result[1]["runtime"], "23m")
        self.assertTrue(result[1]["history"], [episode_2])

        self.assertEqual(result[2]["episode_number"], 3)
        self.assertEqual(result[2]["title"], "...And the Bag's in the River")
        self.assertEqual(result[2]["air_date"], "2008-02-10")
        self.assertFalse(result[2]["history"], [])

    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.services.api_request")
    def test_tmdb_episode(self, mock_api_request, mock_tv_with_seasons):
        """Test the episode method for TMDB episodes."""
        cache.clear()
        mock_api_request.return_value = {
            "id": 62085,
            "name": "Pilot",
            "overview": "A chemistry teacher receives life-changing news.",
            "air_date": "2008-01-20",
            "runtime": 58,
            "production_code": "101",
            "still_path": "/path/to/still1.jpg",
            "vote_average": 8.9,
            "vote_count": 421,
            "guest_stars": [
                {
                    "id": 1,
                    "name": "Guest Actor",
                    "character": "Guest",
                    "profile_path": "/guest.jpg",
                    "order": 0,
                },
            ],
            "crew": [
                {
                    "id": 2,
                    "name": "Episode Director",
                    "job": "Director",
                    "profile_path": "/director.jpg",
                },
            ],
            "external_ids": {
                "imdb_id": "tt0959621",
                "tvdb_id": 349232,
            },
        }
        mock_tv_with_seasons.return_value = {
            "title": "Breaking Bad",
            "season/1": {
                "title": "Breaking Bad",
                "season_title": "Season 1",
                "episodes": [
                    {
                        "episode_number": 1,
                        "name": "Pilot",
                        "still_path": "/path/to/still1.jpg",
                    },
                    {
                        "episode_number": 2,
                        "name": "Cat's in the Bag...",
                        "still_path": "/path/to/still2.jpg",
                    },
                ],
            },
        }

        result = tmdb.episode("1396", "1", "1")

        self.assertEqual(result["title"], "Pilot")
        self.assertEqual(result["subtitle"], "Breaking Bad • S1 E1")
        self.assertEqual(result["series_title"], "Breaking Bad")
        self.assertEqual(result["season_title"], "Season 1")
        self.assertEqual(result["episode_title"], "Pilot")
        self.assertEqual(result["image"], tmdb.get_image_url("/path/to/still1.jpg"))
        self.assertEqual(result["backdrop_path"], "/path/to/still1.jpg")
        self.assertEqual(result["synopsis"], "A chemistry teacher receives life-changing news.")
        self.assertEqual(result["release_date"], "2008-01-20")
        self.assertEqual(result["score"], 8.9)
        self.assertEqual(result["score_count"], 421)
        self.assertEqual(result["details"]["runtime"], "58m")
        self.assertEqual(result["details"]["production_code"], "101")
        self.assertEqual(result["cast"][0]["name"], "Guest Actor")
        self.assertEqual(result["crew"][0]["name"], "Episode Director")
        self.assertEqual(
            result["external_links"]["IMDb"],
            "https://www.imdb.com/title/tt0959621/",
        )
        self.assertEqual(result["imdb_id"], "tt0959621")
        self.assertNotIn("TVDB", result["external_links"])
        self.assertIsNone(result["external_ratings"]["imdb"]["value"])
        self.assertEqual(result["parent"]["show"]["ref"]["media_type"], "tv")
        self.assertEqual(result["parent"]["season"]["ref"]["season_number"], 1)

        request_args, request_kwargs = mock_api_request.call_args
        self.assertEqual(request_args[:3], (Sources.TMDB.value, "GET", "https://api.themoviedb.org/3/tv/1396/season/1/episode/1"))
        self.assertEqual(request_kwargs["params"]["append_to_response"], "external_ids")
        mock_tv_with_seasons.assert_called_once_with("1396", [1])

    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.services.api_request")
    def test_tmdb_episode_not_found(self, mock_api_request, mock_tv_with_seasons):
        """TMDB episode 404s retain useful season and episode context."""
        cache.clear()
        response = requests.Response()
        response.status_code = 404
        response._content = b'{"status_message":"Not found"}'
        mock_api_request.side_effect = requests.exceptions.HTTPError(response=response)

        with self.assertRaises(services.ProviderAPIError) as cm:
            tmdb.episode("1396", "1", "3")

        self.assertEqual(cm.exception.status_code, 404)
        self.assertIn("Episode 3 not found in season 1", str(cm.exception))
        self.assertIn("The Movie Database with ID 1396", str(cm.exception))
        mock_tv_with_seasons.assert_not_called()

    @patch("app.providers.tmdb.tv_with_seasons")
    @patch("app.providers.tmdb.services.api_request")
    def test_tmdb_episode_without_still_keeps_persistence_placeholder(
        self,
        mock_api_request,
        mock_tv_with_seasons,
    ):
        """Provider metadata distinguishes missing stills from stored placeholders."""
        cache.clear()
        mock_api_request.return_value = {
            "name": "No Still",
            "overview": "",
            "air_date": "2025-01-01",
            "runtime": 42,
            "still_path": None,
            "vote_average": 0,
            "vote_count": 0,
            "guest_stars": [],
            "crew": [],
            "external_ids": {},
        }
        mock_tv_with_seasons.return_value = {
            "title": "Example Show",
            "season/1": {
                "title": "Example Show",
                "season_title": "Season 1",
                "episodes": [],
            },
        }

        result = tmdb.episode("123", 1, 1)

        self.assertEqual(result["image"], settings.IMG_NONE)
        self.assertIsNone(result["backdrop_path"])
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_count"], 0)

    def test_tmdb_find_next_episode(self):
        """Test the find_next_episode function."""
        episodes_metadata = [
            {"episode_number": 1, "title": "Episode 1"},
            {"episode_number": 2, "title": "Episode 2"},
            {"episode_number": 3, "title": "Episode 3"},
        ]

        next_episode = tmdb.find_next_episode(1, episodes_metadata)
        self.assertEqual(next_episode, 2)

        next_episode = tmdb.find_next_episode(3, episodes_metadata)
        self.assertIsNone(next_episode)

        next_episode = tmdb.find_next_episode(5, episodes_metadata)
        self.assertIsNone(next_episode)

    @requires_provider_network
    def test_movie(self):
        """Test the metadata method for movies."""
        response = tmdb.movie("10494")
        self.assertEqual(response["title"], "Perfect Blue")
        self.assertEqual(response["details"]["release_date"], "1998-02-28")
        self.assertEqual(response["details"]["status"], "Released")

    @patch("app.providers.tmdb.services.api_request")
    def test_movie_exposes_collection_series_reference(self, mock_api_request):
        cache.clear()
        mock_api_request.side_effect = [
            {
                "id": 11,
                "title": "Movie One",
                "poster_path": None,
                "backdrop_path": None,
                "overview": "",
                "genres": [],
                "vote_average": 0,
                "vote_count": 0,
                "revenue": 0,
                "popularity": 0,
                "release_date": "2020-01-01",
                "status": "Released",
                "runtime": 90,
                "release_dates": {},
                "production_companies": [],
                "production_countries": [],
                "spoken_languages": [],
                "credits": {"cast": [], "crew": []},
                "recommendations": {"results": []},
                "external_ids": {},
                "watch/providers": {"results": {}},
                "belongs_to_collection": {
                    "id": 1234,
                    "name": "Exact Collection Name",
                },
            },
            {
                "id": 1234,
                "name": "Exact Collection Name",
                "parts": [],
            },
        ]

        response = tmdb.movie("11")

        self.assertEqual(response["details"]["series_id"], "1234")
        self.assertEqual(response["details"]["series_source"], Sources.TMDB.value)
        self.assertEqual(
            response["details"]["series_media_type"],
            MediaTypes.MOVIE.value,
        )
        self.assertEqual(
            response["details"]["series_name"],
            "Exact Collection Name",
        )

    @patch("requests.Session.get")
    def test_movie_unknown(self, mock_data):
        """Test the metadata method for movies with mostly unknown data."""
        with Path(mock_path / "metadata_movie_unknown.json").open() as file:
            movie_response = json.load(file)
        mock_data.return_value.json.return_value = movie_response
        mock_data.return_value.status_code = 200

        response = tmdb.movie("0")
        self.assertEqual(response["title"], "Unknown Movie")
        self.assertEqual(response["image"], settings.IMG_NONE)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["details"]["release_date"], None)
        self.assertEqual(response["details"]["runtime"], None)
        self.assertEqual(response["genres"], None)
        self.assertEqual(response["details"]["studios"], None)
        self.assertEqual(response["details"]["country"], None)
        self.assertEqual(response["details"]["languages"], None)

    @requires_provider_network
    def test_games(self):
        """Test the metadata method for games."""
        response = igdb.game("1942")
        self.assertEqual(response["title"], "The Witcher 3: Wild Hunt")
        self.assertEqual(response["details"]["format"], "Main game")
        self.assertEqual(response["details"]["release_date"], "2015-05-19")
        self.assertEqual(
            response["details"]["themes"],
            ["Action", "Fantasy", "Open world"],
        )
        self.assertIsNotNone(response["time_to_beat"])
        self.assertIn("normally", response["time_to_beat"])
        self.assertEqual(
            list(response["time_to_beat"].keys()),
            ["hastily", "normally", "completely"],
        )

    @patch("app.providers.igdb.get_access_token", return_value="token")
    @patch("app.providers.igdb.services.api_request")
    def test_game_metadata_keeps_artwork_age_rating_and_franchise(self, mock_api_request, _token_mock):
        cache.clear()
        mock_api_request.return_value = [
            {
                "name": "GameData",
                "result": [
                    {
                        "id": 1020,
                        "name": "Space Game",
                        "url": "https://www.igdb.com/games/space-game",
                        "cover": {"image_id": "cover"},
                        "artworks": [{"image_id": "wide-art", "width": 1920, "height": 1080}],
                        "summary": "Fly through space.",
                        "game_type": 0,
                        "first_release_date": int(datetime(2020, 9, 17, 12, tzinfo=UTC).timestamp()),
                        "total_rating": 92.68,
                        "total_rating_count": 5000,
                        "genres": [{"name": "Adventure"}],
                        "themes": [{"name": "Sci-Fi"}],
                        "platforms": [{"name": "PC"}],
                        "age_ratings": [{"category": 1, "rating": 11}],
                        "franchises": [{"name": "Space Franchise"}],
                        "collections": [
                            {
                                "id": 500,
                                "name": "Space Collection",
                                "games": [
                                    {
                                        "id": 1021,
                                        "name": "Space Game 2",
                                        "cover": {"image_id": "cover-2"},
                                        "game_type": 0,
                                        "first_release_date": int(datetime(2022, 5, 6, tzinfo=UTC).timestamp()),
                                    },
                                    {
                                        "id": 1022,
                                        "name": "Space Game DLC",
                                        "cover": {"image_id": "cover-dlc"},
                                        "game_type": 1,
                                    },
                                    {
                                        "id": 1020,
                                        "name": "Space Game",
                                        "cover": {"image_id": "cover"},
                                        "game_type": 0,
                                        "first_release_date": int(datetime(2020, 9, 17, tzinfo=UTC).timestamp()),
                                    },
                                    {
                                        "id": 1023,
                                        "name": "Space Game: Deluxe Edition",
                                        "cover": {"image_id": "cover-deluxe"},
                                        "game_type": 0,
                                        "version_parent": 1020,
                                    },
                                ],
                            },
                        ],
                        "involved_companies": [
                            {"developer": True, "company": {"id": 77, "name": "Space Studio"}},
                            {"publisher": True, "company": {"id": 77, "name": "Space Studio"}},
                            {"developer": True, "company": {"id": 88, "name": "Orbit Works"}},
                        ],
                    },
                ],
            },
            {"name": "TTBData", "result": [{"id": 1, "normally": 3600}]},
        ]

        response = igdb.game("1020")

        self.assertEqual(response["artworks"], [{"image_id": "wide-art", "width": 1920, "height": 1080}])
        self.assertEqual(response["details"]["release_date"], "2020-09-17")
        self.assertEqual(response["details"]["age_rating"], "ESRB M")
        self.assertEqual(response["details"]["age_ratings"], ["ESRB M"])
        self.assertEqual(response["details"]["franchise"], "Space Franchise")
        self.assertEqual(response["details"]["franchises"], ["Space Franchise"])
        self.assertEqual(response["details"]["collection"], "Space Collection")
        self.assertEqual(response["details"]["series_id"], "500")
        self.assertEqual(response["details"]["series_source"], Sources.IGDB.value)
        self.assertEqual(response["details"]["series_media_type"], MediaTypes.GAME.value)
        self.assertEqual(response["details"]["series_name"], "Space Collection")
        self.assertEqual(
            [game["title"] for game in response["related"]["collection"]],
            ["Space Game", "Space Game 2"],
        )
        self.assertIn("collections.games.version_parent", mock_api_request.call_args.kwargs["data"])
        self.assertEqual(
            response["details"]["company_credits"],
            [
                {"id": "77", "source": "igdb", "name": "Space Studio", "roles": ["Developer", "Publisher"]},
                {"id": "88", "source": "igdb", "name": "Orbit Works", "roles": ["Developer"]},
            ],
        )

    @patch("app.providers.igdb.get_access_token", return_value="token")
    @patch("app.providers.igdb._post_igdb")
    def test_company_catalog_keeps_roles_and_omits_missing_games(self, post_igdb, _token_mock):
        cache.clear()
        post_igdb.side_effect = [
            [
                {
                    "id": 77,
                    "name": "Space Studio",
                    "developed": [10, 11, 10],
                    "published": [12],
                },
            ],
            [
                {
                    "id": 11,
                    "name": "Newer Game",
                    "cover": {"image_id": "newer"},
                    "first_release_date": int(datetime(2024, 1, 1, tzinfo=UTC).timestamp()),
                    "platforms": [{"name": "PlayStation 5"}],
                    "game_type": 0,
                },
                {
                    "id": 10,
                    "name": "Older Game",
                    "cover": {"image_id": "older"},
                    "first_release_date": int(datetime(2020, 1, 1, tzinfo=UTC).timestamp()),
                    "game_type": 0,
                },
            ],
        ]

        catalog = igdb.company_catalog("77", "developed")

        self.assertEqual([game["media_id"] for game in catalog], [10, 11])
        self.assertTrue(all(game["roles"] == ["Developer"] for game in catalog))
        self.assertEqual(catalog[1]["platforms"], ["PlayStation 5"])
        self.assertIn("platforms.name", post_igdb.call_args_list[1].args[1])
        self.assertEqual(igdb.company_catalog_count(igdb.company("77"), "developed"), 2)
        self.assertEqual(post_igdb.call_count, 2)

    @requires_provider_network
    def test_external_game_steam(self):
        """Test the external_game method for Steam games."""
        igdb_game_id = igdb.external_game("292030", igdb.ExternalGameSource.STEAM)

        self.assertEqual(igdb_game_id, 1942)

    @requires_provider_network
    def test_external_game_not_found(self):
        """Test the external_game method with non-existent Steam ID."""
        igdb_game_id = igdb.external_game("999999999", igdb.ExternalGameSource.STEAM)

        self.assertIsNone(igdb_game_id)

    @override_settings(STEAMGRIDDB_API_KEY="test-key")
    @patch("app.providers.steamgriddb.igdb.steam_app_id", return_value="1245620")
    @patch("app.providers.steamgriddb.services.api_request")
    def test_steamgriddb_game_posters_use_steam_external_id(self, api_request_mock, _steam_id_mock):
        """Test SteamGridDB poster normalization."""
        cache.clear()
        api_request_mock.return_value = {
            "success": True,
            "data": [
                {
                    "id": 207777,
                    "score": 12,
                    "style": "alternate",
                    "width": 600,
                    "height": 900,
                    "url": "https://cdn2.steamgriddb.com/grid/poster.png",
                    "thumb": "https://cdn2.steamgriddb.com/thumb/poster.jpg",
                },
            ],
        }

        posters = steamgriddb.get_game_posters("1020")

        self.assertEqual(posters[0]["url"], "https://cdn2.steamgriddb.com/grid/poster.png")
        self.assertEqual(posters[0]["thumbnail_url"], "https://cdn2.steamgriddb.com/thumb/poster.jpg")
        self.assertEqual(posters[0]["width"], 600)
        self.assertEqual(posters[0]["height"], 900)
        self.assertEqual(posters[0]["aspect_ratio"], 0.667)
        self.assertEqual(posters[0]["source"], "steamgriddb")
        api_request_mock.assert_called_once()
        self.assertIn("/grids/steam/1245620", api_request_mock.call_args.args[2])

    @override_settings(STEAMGRIDDB_API_KEY="test-key")
    @patch("app.providers.steamgriddb.igdb.steam_app_id", return_value="1245620")
    @patch("app.providers.steamgriddb.services.api_request")
    def test_steamgriddb_game_logo_prefers_official(self, api_request_mock, _steam_id_mock):
        """Test SteamGridDB logo selection."""
        cache.clear()
        api_request_mock.return_value = {
            "success": True,
            "data": [
                {
                    "id": 2,
                    "score": 100,
                    "style": "white",
                    "width": 1000,
                    "height": 250,
                    "url": "https://cdn2.steamgriddb.com/logo/plain.png",
                },
                {
                    "id": 1,
                    "score": 1,
                    "style": "official",
                    "width": 600,
                    "height": 215,
                    "url": "https://cdn2.steamgriddb.com/logo/official.png",
                },
            ],
        }

        logo = steamgriddb.get_game_logo("1020")

        self.assertEqual(logo["url"], "https://cdn2.steamgriddb.com/logo/official.png")
        self.assertEqual(logo["aspect_ratio"], 2.791)
        api_request_mock.assert_called_once()
        self.assertIn("/logos/steam/1245620", api_request_mock.call_args.args[2])

    @override_settings(STEAMGRIDDB_API_KEY="test-key")
    @patch("app.providers.steamgriddb.igdb.steam_app_id", return_value="1245620")
    @patch("app.providers.steamgriddb.services.api_request")
    def test_steamgriddb_game_logos_return_style_then_score_order(self, api_request_mock, _steam_id_mock):
        """Test SteamGridDB logo options use stable style and score ordering."""
        cache.clear()
        api_request_mock.return_value = {
            "success": True,
            "data": [
                {"id": 1, "score": 100, "style": "white", "url": "https://example.com/white.png"},
                {"id": 2, "score": 5, "style": "official", "url": "https://example.com/official-low.png"},
                {"id": 3, "score": 10, "style": "official", "url": "https://example.com/official-high.png"},
                {"id": 4, "score": 50, "style": "custom", "url": "https://example.com/custom.png"},
            ],
        }

        logos = steamgriddb.get_game_logos("1020")

        self.assertEqual(
            [logo["url"] for logo in logos],
            [
                "https://example.com/official-high.png",
                "https://example.com/official-low.png",
                "https://example.com/custom.png",
                "https://example.com/white.png",
            ],
        )

    @override_settings(STEAMGRIDDB_API_KEY="")
    @patch("app.providers.steamgriddb.igdb.steam_app_id")
    def test_steamgriddb_game_logos_without_credentials_are_empty(self, steam_id_mock):
        """Test logo options degrade cleanly when SteamGridDB is not configured."""
        cache.clear()

        self.assertEqual(steamgriddb.get_game_logos("1020"), [])
        self.assertIsNone(steamgriddb.get_game_logo("1020"))
        steam_id_mock.assert_not_called()

    @override_settings(STEAMGRIDDB_API_KEY="test-key")
    @patch("app.providers.steamgriddb.igdb.game", return_value={"title": "Ghost of Tsushima"})
    @patch("app.providers.steamgriddb.igdb.steam_app_id", return_value=None)
    @patch("app.providers.steamgriddb.services.api_request")
    def test_steamgriddb_backdrops_fallback_to_exact_title_search(
        self,
        api_request_mock,
        _steam_id_mock,
        _game_mock,
    ):
        """Test SteamGridDB title search fallback for games without Steam IDs."""
        cache.clear()

        def api_request_side_effect(_provider, _method, url, **_kwargs):
            if "/search/autocomplete/Ghost%20of%20Tsushima" in url:
                return {
                    "success": True,
                    "data": [
                        {"id": 111, "name": "Ghost of a Tale"},
                        {"id": 222, "name": "Ghost of Tsushima"},
                    ],
                }
            if "/heroes/game/222" in url:
                return {
                    "success": True,
                    "data": [
                        {
                            "id": 333,
                            "score": 42,
                            "width": 3840,
                            "height": 1240,
                            "url": "https://cdn2.steamgriddb.com/hero/ghost.jpg",
                        },
                    ],
                }
            return {"success": True, "data": []}

        api_request_mock.side_effect = api_request_side_effect

        backdrops = steamgriddb.get_game_backdrops("75235")

        self.assertEqual(backdrops[0]["url"], "https://cdn2.steamgriddb.com/hero/ghost.jpg")
        self.assertEqual(backdrops[0]["aspect_ratio"], 3.097)
        requested_urls = [call.args[2] for call in api_request_mock.call_args_list]
        self.assertIn("https://www.steamgriddb.com/api/v2/search/autocomplete/Ghost%20of%20Tsushima", requested_urls)
        self.assertIn("https://www.steamgriddb.com/api/v2/heroes/game/222", requested_urls)

    @patch("app.providers.steam.igdb.steam_app_id", return_value="1245620")
    @patch("app.providers.steam.services.api_request")
    def test_steam_metacritic_rating_uses_steam_external_id(self, api_request_mock, _steam_id_mock):
        """Test Steam Metacritic rating normalization."""
        cache.clear()
        api_request_mock.return_value = {
            "1245620": {
                "success": True,
                "data": {
                    "metacritic": {
                        "score": 94,
                        "url": "https://www.metacritic.com/game/pc/elden-ring",
                    },
                },
            },
        }

        rating = steam.get_metacritic_rating("119133")

        self.assertEqual(rating["value"], 94)
        self.assertEqual(rating["url"], "https://www.metacritic.com/game/pc/elden-ring")
        api_request_mock.assert_called_once()
        self.assertIn("/appdetails", api_request_mock.call_args.args[2])
        self.assertEqual(api_request_mock.call_args.kwargs["params"]["appids"], "1245620")

    @patch("app.providers.steam.igdb.steam_app_id")
    def test_steam_metacritic_failure_can_be_raised(self, steam_id_mock):
        cache.clear()
        steam_id_mock.side_effect = requests.ConnectionError("temporary outage")

        self.assertIsNone(steam.get_metacritic_rating("119133"))
        with self.assertRaisesRegex(RuntimeError, "Cached Steam Metacritic lookup failure"):
            steam.get_metacritic_rating("119133", raise_errors=True)

    @patch("app.providers.steam.igdb.steam_app_id", return_value="1245620")
    @patch("app.providers.steam.services.api_request")
    def test_steam_review_rating_uses_lifetime_steam_purchase_summary(
        self,
        api_request_mock,
        _steam_id_mock,
    ):
        cache.clear()
        api_request_mock.return_value = {
            "success": 1,
            "query_summary": {
                "total_positive": 925,
                "total_negative": 75,
                "total_reviews": 1000,
            },
        }

        rating = steam.get_review_rating("119133")
        cached_rating = steam.get_review_rating("119133")

        self.assertEqual(rating["value"], 93)
        self.assertEqual(rating["vote_count"], 1000)
        self.assertEqual(rating["url"], "https://store.steampowered.com/app/1245620/")
        self.assertEqual(cached_rating, rating)
        api_request_mock.assert_called_once_with(
            "steam",
            "GET",
            "https://store.steampowered.com/appreviews/1245620",
            params={
                "json": 1,
                "filter": "all",
                "language": "all",
                "day_range": 365,
                "review_type": "all",
                "purchase_type": "steam",
                "num_per_page": 1,
            },
        )

    @patch("app.providers.steam.services.api_request")
    @patch("app.providers.steam.igdb.steam_app_id", return_value=None)
    def test_steam_review_rating_without_mapping_is_unavailable(
        self,
        _steam_id_mock,
        api_request_mock,
    ):
        cache.clear()

        self.assertIsNone(steam.get_review_rating("119133"))
        api_request_mock.assert_not_called()

    @patch("app.providers.steam.igdb.steam_app_id", return_value="1245620")
    @patch("app.providers.steam.services.api_request")
    def test_steam_review_rating_empty_or_unsuccessful_is_unavailable(
        self,
        api_request_mock,
        _steam_id_mock,
    ):
        for response in (
            {"success": 0},
            {
                "success": 1,
                "query_summary": {
                    "total_positive": 0,
                    "total_negative": 0,
                    "total_reviews": 0,
                },
            },
        ):
            with self.subTest(response=response):
                cache.clear()
                api_request_mock.return_value = response
                self.assertIsNone(steam.get_review_rating("119133"))

    @patch("app.providers.steam.igdb.steam_app_id", return_value="1245620")
    @patch("app.providers.steam.services.api_request")
    def test_steam_review_rating_rejects_malformed_counts(
        self,
        api_request_mock,
        _steam_id_mock,
    ):
        cache.clear()
        api_request_mock.return_value = {
            "success": 1,
            "query_summary": {
                "total_positive": 10,
                "total_negative": 2,
                "total_reviews": 11,
            },
        }

        with self.assertRaisesRegex(ValueError, "Malformed Steam review counts"):
            steam.get_review_rating("119133", raise_errors=True)

    @patch("app.providers.steam.igdb.steam_app_id")
    def test_steam_review_failure_can_be_raised(self, steam_id_mock):
        cache.clear()
        steam_id_mock.side_effect = requests.ConnectionError("temporary outage")

        self.assertIsNone(steam.get_review_rating("119133"))
        with self.assertRaisesRegex(RuntimeError, "Cached Steam review lookup failure"):
            steam.get_review_rating("119133", raise_errors=True)

    @patch("app.providers.igdb.external_game_uid")
    def test_steam_app_id_requires_positive_numeric_uid(self, external_uid_mock):
        for uid, expected in (
            ("1245620", "1245620"),
            ("001", "1"),
            ("0", None),
            ("-1", None),
            ("not-an-app", None),
            (None, None),
        ):
            with self.subTest(uid=uid):
                external_uid_mock.return_value = uid
                self.assertEqual(igdb.steam_app_id("119133"), expected)

    @requires_provider_network
    def test_book(self):
        """Test the metadata method for books."""
        response = openlibrary.book("OL21733390M")
        self.assertEqual(response["title"], "Nineteen Eighty-Four")
        self.assertEqual(response["details"]["author"], "George Orwell")

    def test_openlibrary_publish_date_with_abbreviated_month(self):
        """Test Open Library publish dates with abbreviated month names."""
        response = openlibrary.get_publish_date({"publish_date": "Oct 01, 2017"})
        self.assertEqual(response, "2017-10-01")

    @requires_provider_network
    def test_comic(self):
        """Test the metadata method for comics."""
        response = comicvine.comic("155969")
        self.assertEqual(response["title"], "Ultimate Spider-Man")

    @requires_provider_network
    def test_hardcover_book(self):
        """Test the metadata method for books from Hardcover."""
        response = hardcover.book("377193")
        self.assertEqual(response["title"], "The Great Gatsby")
        self.assertEqual(response["details"]["author"], "F. Scott Fitzgerald")
        self.assertIn("Fiction", response["genres"])
        self.assertIn("Young Adult", response["genres"])
        self.assertIn("Classics", response["genres"])
        self.assertAlmostEqual(response["score"], 3.7, delta=0.1)

    def test_hardcover_featured_series_uses_nested_series(self):
        series = hardcover.get_featured_series(
            {
                "id": 1108,
                "series": {
                    "id": 981,
                    "name": "A Song of Ice and Fire",
                },
                "position": 2.0,
            }
        )

        self.assertEqual(
            series,
            {"id": 981, "name": "A Song of Ice and Fire", "position": 2.0},
        )

    @patch("app.providers.hardcover.services.api_request")
    def test_hardcover_book_suggests_author_books_without_recommendation_queries(self, api_request_mock):
        api_request_mock.side_effect = [
            {
                "data": {
                    "books_by_pk": {
                        "id": 999001,
                        "title": "Dune",
                        "slug": "dune",
                        "cached_contributors": "Frank Herbert",
                        "cached_featured_series": None,
                        "contributions": [],
                        "default_cover_edition": None,
                    },
                },
            },
            {"data": {"books": [{"id": 999002, "title": "Dune Messiah", "cached_image": None}]}},
        ]

        response = hardcover.book("999001")

        self.assertEqual(api_request_mock.call_count, 2)
        self.assertIn("BooksByAuthor", api_request_mock.call_args.kwargs["params"]["query"])
        self.assertEqual(
            [book["title"] for book in response["related"]["recommendations"]],
            ["Dune Messiah"],
        )

    @patch("app.providers.hardcover.services.api_request")
    def test_hardcover_series_books_dedupe_uses_nested_book_read_count(self, api_request_mock):
        api_request_mock.return_value = {
            "data": {
                "series_by_pk": {
                    "book_series": [
                        {
                            "position": 1,
                            "book": {
                                "id": 1,
                                "title": "Low Read Edition",
                                "users_read_count": 1,
                                "cached_image": None,
                            },
                        },
                        {
                            "position": 1,
                            "book": {
                                "id": 2,
                                "title": "Preferred Edition",
                                "users_read_count": 10,
                                "cached_image": "https://example.com/book.jpg",
                            },
                        },
                    ],
                },
            },
        }

        series_books = hardcover.get_series_books(981)

        self.assertEqual(len(series_books), 1)
        self.assertEqual(series_books[0]["media_id"], 2)
        self.assertEqual(series_books[0]["title"], "Preferred Edition")

    @requires_provider_network
    def test_hardcover_book_unknown(self):
        """Test the metadata method for books from Hardcover with minimal data."""
        response = hardcover.book("1265528")
        self.assertEqual(response["title"], "MiNRS")
        self.assertEqual(response["details"]["author"], "Kevin Sylvester")
        self.assertEqual(response["details"]["publish_date"], "2015-09-22")
        # These fields should be None or default values
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["details"]["format"], "Unknown")
        self.assertIsNone(response["genres"])

    def test_manual_tv(self):
        """Test the metadata method for manually created TV shows."""
        Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Manual TV Show",
            image="http://example.com/manual.jpg",
        )

        Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Manual TV Show",
            image="http://example.com/manual_s1.jpg",
            season_number=1,
        )

        for i in range(1, 4):
            Item.objects.create(
                media_id="1",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Episode {i}",
                image=f"http://example.com/manual_s1e{i}.jpg",
                season_number=1,
                episode_number=i,
            )

        response = manual.metadata("1", MediaTypes.TV.value)

        self.assertEqual(response["title"], "Manual TV Show")
        self.assertEqual(response["media_id"], "1")
        self.assertEqual(response["source"], Sources.MANUAL.value)
        self.assertEqual(response["media_type"], MediaTypes.TV.value)
        self.assertEqual(response["synopsis"], "No synopsis available.")

        self.assertEqual(response["details"]["seasons"], 1)
        self.assertEqual(response["details"]["episodes"], 3)
        self.assertEqual(response["max_progress"], 3)
        self.assertEqual(len(response["related"]["seasons"]), 1)

        season_data = response["season/1"]
        self.assertEqual(season_data["season_number"], 1)
        self.assertEqual(season_data["max_progress"], 3)
        self.assertEqual(len(season_data["episodes"]), 3)

    def test_manual_movie(self):
        """Test the metadata method for manually created movies."""
        Item.objects.create(
            media_id="2",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MOVIE.value,
            title="Manual Movie",
            image="http://example.com/manual_movie.jpg",
        )

        response = manual.metadata("2", MediaTypes.MOVIE.value)

        self.assertEqual(response["title"], "Manual Movie")
        self.assertEqual(response["media_id"], "2")
        self.assertEqual(response["source"], Sources.MANUAL.value)
        self.assertEqual(response["media_type"], MediaTypes.MOVIE.value)
        self.assertEqual(response["synopsis"], "No synopsis available.")
        self.assertEqual(response["max_progress"], 1)

    def test_manual_season(self):
        """Test the season method for manually created seasons."""
        Item.objects.create(
            media_id="3",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Another TV Show",
            image="http://example.com/another.jpg",
        )

        Item.objects.create(
            media_id="3",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Another TV Show",
            image="http://example.com/another_s1.jpg",
            season_number=1,
        )

        for i in range(1, 3):
            Item.objects.create(
                media_id="3",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Episode {i}",
                image=f"http://example.com/another_s1e{i}.jpg",
                season_number=1,
                episode_number=i,
            )

        response = manual.season("3", 1)

        self.assertEqual(response["season_number"], 1)
        self.assertEqual(response["title"], "Another TV Show")
        self.assertEqual(response["season_title"], "Season 1")
        self.assertEqual(response["max_progress"], 2)
        self.assertEqual(len(response["episodes"]), 2)

    def test_manual_episode(self):
        """Test the episode method for manually created episodes."""
        Item.objects.create(
            media_id="4",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Third TV Show",
            image="http://example.com/third.jpg",
        )

        Item.objects.create(
            media_id="4",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Third TV Show",
            image="http://example.com/third_s1.jpg",
            season_number=1,
        )

        Item.objects.create(
            media_id="4",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            title="Special Episode",
            image="http://example.com/third_s1e1.jpg",
            season_number=1,
            episode_number=1,
        )

        response = manual.episode("4", 1, 1)

        self.assertEqual(response["media_type"], MediaTypes.EPISODE.value)
        self.assertEqual(response["title"], "Third TV Show")
        self.assertEqual(response["season_title"], "Season 1")
        self.assertEqual(response["episode_title"], "Special Episode")

        result = manual.episode("4", 1, 2)
        self.assertIsNone(result)

    def test_manual_process_episodes(self):
        """Test the process_episodes function for manual episodes."""
        Item.objects.create(
            media_id="5",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="Process Episodes Test",
            image="http://example.com/process.jpg",
        )

        Item.objects.create(
            media_id="5",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="Process Episodes Test",
            image="http://example.com/process_s1.jpg",
            season_number=1,
        )

        for i in range(1, 4):
            Item.objects.create(
                media_id="5",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.EPISODE.value,
                title=f"Process Episode {i}",
                image=f"http://example.com/process_s1e{i}.jpg",
                season_number=1,
                episode_number=i,
            )

        season_metadata = {
            "season_number": 1,
            "episodes": [
                {
                    "media_id": "5",
                    "episode_number": 1,
                    "air_date": "2025-01-01",
                    "image": "http://example.com/process_s1e1.jpg",
                    "title": "Process Episode 1",
                },
                {
                    "media_id": "5",
                    "episode_number": 2,
                    "air_date": "2025-01-08",
                    "image": "http://example.com/process_s1e2.jpg",
                    "title": "Process Episode 2",
                },
                {
                    "media_id": "5",
                    "episode_number": 3,
                    "air_date": "2025-01-15",
                    "image": "http://example.com/process_s1e3.jpg",
                    "title": "Process Episode 3",
                },
            ],
        }

        ep_item1 = Item.objects.get(
            media_id="5",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )
        ep_item2 = Item.objects.get(
            media_id="5",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
        )

        episode_1 = Episode(item=ep_item1)
        episode_2 = Episode(item=ep_item2)

        episodes_in_db = [episode_1, episode_2]

        # Call process_episodes
        result = manual.process_episodes(season_metadata, episodes_in_db)

        self.assertEqual(len(result), 3)

        self.assertEqual(result[0]["episode_number"], 1)
        self.assertEqual(result[0]["title"], "Process Episode 1")
        self.assertEqual(result[0]["air_date"], "2025-01-01")
        self.assertTrue(result[0]["history"], [episode_1])

        self.assertEqual(result[1]["episode_number"], 2)
        self.assertEqual(result[1]["title"], "Process Episode 2")
        self.assertEqual(result[1]["air_date"], "2025-01-08")
        self.assertTrue(result[0]["history"], [episode_2])

        self.assertEqual(result[2]["episode_number"], 3)
        self.assertEqual(result[2]["title"], "Process Episode 3")
        self.assertEqual(result[2]["air_date"], "2025-01-15")
        self.assertFalse(result[2]["history"], [])

    def test_hardcover_get_tags(self):
        """Test the get_tags function from Hardcover provider."""
        tags_data = [{"tag": "Science Fiction"}, {"tag": "Fantasy"}]
        result = hardcover.get_tags(tags_data)
        self.assertEqual(result, ["Science Fiction", "Fantasy"])

        self.assertIsNone(hardcover.get_tags(None))

    def test_hardcover_get_ratings(self):
        """Test the get_ratings function from Hardcover provider."""
        self.assertEqual(hardcover.get_ratings(4.5), 4.5)

        self.assertIsNone(hardcover.get_ratings(None))

    @patch("app.providers.hardcover.services.api_request")
    def test_hardcover_author_books_exclude_catalog_clutter(self, mock_request):
        book = {
            "title": "Book",
            "book_status_id": 1,
            "canonical_id": None,
            "compilation": False,
            "is_partial_book": False,
            "contributions": [],
        }
        mock_request.return_value = {
            "data": {
                "books": [
                    {**book, "id": 1},
                    {**book, "id": 2, "book_status_id": 4, "canonical_id": 1},
                    {**book, "id": 3, "compilation": True},
                    {**book, "id": 4, "is_partial_book": True},
                ],
            },
        }

        credits = hardcover.get_author_books({"id": 80626, "name": "J.K. Rowling"})

        self.assertEqual([credit["media_id"] for credit in credits], ["1"])
        request = mock_request.call_args.kwargs["params"]
        self.assertNotIn("by_name", request["query"])
        self.assertNotIn("author_name", request["variables"])
        for constraint in (
            "book_status_id: {_eq: 1}",
            "canonical_id: {_is_null: true}",
            "compilation: {_eq: false}",
            "is_partial_book: {_eq: false}",
        ):
            self.assertIn(constraint, request["query"])

    def test_hardcover_series_keeps_only_primary_numbered_books(self):
        rows = [
            {"position": 0.5, "book": {"id": 1, "title": "Prequel", "users_read_count": 100}},
            {"position": 1, "book": {"id": 2, "title": "Edition", "users_read_count": 1}},
            {"position": 1, "book": {"id": 3, "title": "Book One", "users_read_count": 100}},
            {
                "position": 1,
                "compilation": True,
                "book": {"id": 4, "title": "Box Set", "users_read_count": 200},
            },
            {"position": 1.5, "book": {"id": 5, "title": "Holiday Story", "users_read_count": 100}},
            {"position": 2, "book": {"id": 6, "title": "Book Two", "users_read_count": 90}},
            {"position": 3, "book": {"id": 7, "title": "Continuation", "users_read_count": 80}},
            {"position": None, "book": {"id": 8, "title": "Unnumbered", "users_read_count": 70}},
        ]

        books = hardcover._primary_series_books(rows, primary_books_count=2)

        self.assertEqual(
            [(book["position"], book["title"]) for book in books],
            [(1, "Book One"), (2, "Book Two")],
        )

    def test_hardcover_author_series_dedupes_positions(self):
        series = {"id": 10, "name": "Series", "primary_books_count": 2}
        credits = [
            {
                "media_id": "edition",
                "title": "Edition",
                "is_author_role": True,
                "users_count": 1,
                "series": {**series, "position": 1},
            },
            {
                "media_id": "one",
                "title": "Book One",
                "is_author_role": True,
                "users_count": 100,
                "series": {**series, "position": 1},
            },
            {
                "media_id": "two",
                "title": "Book Two",
                "is_author_role": True,
                "users_count": 90,
                "series": {**series, "position": 2},
            },
        ]

        result = hardcover.get_author_series(credits)

        self.assertEqual(
            [book["media_id"] for book in result[0]["books"]],
            ["one", "two"],
        )

    def test_hardcover_author_series_sorts_by_total_readership(self):
        credits = [
            {
                "is_author_role": True,
                "users_count": readers,
                "series": {
                    "id": series_id,
                    "name": name,
                    "position": position,
                    "primary_books_count": 2,
                },
            }
            for series_id, name, position, readers in [
                (1, "Niche Series", 1, 10),
                (1, "Niche Series", 2, 20),
                (2, "Popular Series", 1, 100),
                (2, "Popular Series", 2, 200),
            ]
        ]

        result = hardcover.get_author_series(credits)

        self.assertEqual(
            [series["name"] for series in result],
            ["Popular Series", "Niche Series"],
        )

    def test_igdb_get_score(self):
        """Test the get_score function from IGDB provider."""
        self.assertEqual(igdb.get_score({"total_rating": 92.70730625238252}), 92.7)
        self.assertIsNone(igdb.get_score({}))

    def test_hardcover_get_edition_details(self):
        """Test the get_edition_details function from Hardcover provider."""
        edition_data = {
            "edition_format": "Paperback",
            "isbn_13": "9781234567890",
            "isbn_10": "1234567890",
            "publisher": {"name": "Test Publisher"},
        }

        result = hardcover.get_edition_details(edition_data)
        self.assertEqual(result["format"], "Paperback")
        self.assertEqual(result["publisher"], "Test Publisher")
        self.assertEqual(result["isbn"], ["1234567890", "9781234567890"])

        self.assertEqual(hardcover.get_edition_details(None), {})

        no_publisher = {
            "edition_format": "Paperback",
            "isbn_13": "9781234567890",
        }
        result = hardcover.get_edition_details(no_publisher)
        self.assertEqual(result["publisher"], None)

    def test_handle_error_hardcover_unauthorized(self):
        """Test the handle_error function with Hardcover unauthorized error."""
        mock_response = MagicMock()
        mock_response.status_code = 401  # Unauthorized
        mock_response.json.return_value = {"error": "Invalid API key"}

        error = requests.exceptions.HTTPError("401 Unauthorized")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            hardcover.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.HARDCOVER.value)

    def test_handle_error_hardcover_other(self):
        """Test the handle_error function with Hardcover other error."""
        mock_response = MagicMock()
        mock_response.status_code = 500  # Server error
        mock_response.json.return_value = {"error": "Server error"}

        error = requests.exceptions.HTTPError("500 Server Error")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            hardcover.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.HARDCOVER.value)

    def test_handle_error_hardcover_json_error(self):
        """Test the handle_error function with JSON decode error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.side_effect = requests.exceptions.JSONDecodeError(
            "Invalid JSON",
            "",
            0,
        )

        error = requests.exceptions.HTTPError("500 Server Error")
        error.response = mock_response

        with self.assertRaises(services.ProviderAPIError) as cm:
            hardcover.handle_error(error)

        self.assertEqual(cm.exception.provider, Sources.HARDCOVER.value)
