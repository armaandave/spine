"""Who made each search row, as each provider names them: the line under a result's title."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from app.models import MediaTypes
from app.providers import comicvine, hardcover, igdb, mal, tmdb


class SearchMadeByTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("app.providers.igdb.get_access_token", return_value="token")
    @patch("app.providers.igdb.services.api_request")
    def test_games_name_their_developers_and_keep_their_release_date(self, api_request, _token):
        api_request.side_effect = [
            [
                {
                    "id": 1,
                    "name": "Hollow Knight: Silksong",
                    "first_release_date": 1756944000,
                    "involved_companies": [
                        {"company": {"name": "Team Cherry"}, "developer": True},
                        {"company": {"name": "Porting House"}, "developer": False},
                    ],
                },
            ],
            {"count": 1},
        ]

        game = igdb.search("silksong", 1)["results"][0]

        self.assertEqual(game["made_by"], ["Team Cherry"])
        self.assertEqual(game["release_date"], "2025-09-04")
        self.assertIn("involved_companies.developer", api_request.call_args_list[0].kwargs["data"])

    @patch("app.providers.mal.services.api_request")
    def test_anime_name_their_studios_and_manga_their_authors(self, api_request):
        api_request.side_effect = [
            {"data": [{"node": {"id": 52991, "title": "Sousou no Frieren", "studios": [{"name": "Madhouse"}]}}]},
            {
                "data": [
                    {
                        "node": {
                            "id": 126287,
                            "title": "Sousou no Frieren",
                            "authors": [
                                {"node": {"first_name": "Kanehito", "last_name": "Yamada"}, "role": "Story"},
                                {"node": {"first_name": "Tsukasa", "last_name": "Abe"}, "role": "Art"},
                            ],
                        },
                    },
                ],
            },
        ]

        anime = mal.search(MediaTypes.ANIME.value, "frieren", 1)["results"][0]
        manga = mal.search(MediaTypes.MANGA.value, "frieren", 1)["results"][0]

        self.assertEqual(anime["made_by"], ["Madhouse"])
        self.assertEqual(manga["made_by"], ["Kanehito Yamada", "Tsukasa Abe"])
        self.assertIn("studios", api_request.call_args_list[0].kwargs["params"]["fields"])
        self.assertIn("authors{first_name,last_name}", api_request.call_args_list[1].kwargs["params"]["fields"])

    @patch("app.providers.hardcover.services.api_request")
    def test_books_name_their_authors_not_their_translators(self, api_request):
        api_request.return_value = {
            "data": {
                "search": {
                    "results": {
                        "hits": [
                            {
                                "document": {
                                    "id": "1",
                                    "title": "James",
                                    "release_year": 2024,
                                    "author_names": ["Percival Everett", "Anne-Laure Tissut"],
                                    "contributions": [
                                        {"author": {"name": "Percival Everett"}, "contribution": "Author"},
                                        {"author": {"name": "Anne-Laure Tissut"}, "contribution": "Translator"},
                                    ],
                                },
                            },
                        ],
                        "found": 1,
                    },
                },
            },
        }

        book = hardcover.search("james", 1)["results"][0]

        self.assertEqual(book["made_by"], ["Percival Everett"])
        self.assertEqual(book["release_year"], 2024)

    @patch("app.providers.comicvine.services.api_request")
    def test_comics_name_their_publisher_and_keep_their_start_year(self, api_request):
        api_request.return_value = {
            "number_of_total_results": 1,
            "results": [
                {
                    "id": 3622,
                    "name": "Watchmen",
                    "start_year": "1986",
                    "count_of_issues": 12,
                    "publisher": {"name": "DC Comics"},
                    "image": {"medium_url": "https://example.com/3622.jpg"},
                },
            ],
        }

        comic = comicvine.search("watchmen", 1)["results"][0]

        self.assertEqual(comic["made_by"], ["DC Comics"])
        self.assertEqual(comic["start_year"], "1986")

    @patch("app.providers.tmdb.services.api_request")
    def test_movies_name_their_directors_and_shows_their_networks(self, api_request):
        api_request.side_effect = [
            {
                "crew": [
                    {"id": 1, "name": "Mary Parent", "job": "Producer"},
                    {"id": 2, "name": "Denis Villeneuve", "job": "Director"},
                ],
            },
            {"networks": [{"name": "Apple TV"}]},
        ]

        self.assertEqual(tmdb.made_by(MediaTypes.MOVIE.value, 438631), ["Denis Villeneuve"])
        self.assertEqual(tmdb.made_by(MediaTypes.TV.value, 95396), ["Apple TV"])
        self.assertTrue(api_request.call_args_list[0].args[2].endswith("/movie/438631/credits"))
