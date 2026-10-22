import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from app.models import MediaTypes, Sources
from app.providers import (
    comicvine,
    hardcover,
    igdb,
    mal,
    mangaupdates,
    openlibrary,
    tmdb,
)
from app.providers.search_rank import normalize_search_text, rank_results

mock_path = Path(__file__).resolve().parent.parent / "mock_data"
RUN_PROVIDER_TESTS = os.environ.get("RUN_PROVIDER_TESTS") == "1"
requires_provider_network = unittest.skipUnless(
    RUN_PROVIDER_TESTS,
    "Set RUN_PROVIDER_TESTS=1 to run live provider API tests.",
)


def mal_genre_page_html(media_type, media_id, title):
    if media_type == MediaTypes.ANIME.value:
        card_class = "js-seasonal-anime js-anime-type-1"
        details = """
            <div class="prodsrc"><div class="info">TV (64 eps)</div></div>
            <span class="js-start_date">20090405</span>
            <span class="js-score">9.1</span>
            <span class="js-members">3M</span>
        """
    else:
        card_class = "js-seasonal-anime"
        details = """
            <div class="prodsrc">
                <div class="info"><span class="item">Manga, 1989</span></div>
            </div>
            <span class="js-chapter">380</span>
            <span class="js-volume">42</span>
            <span class="scormem-item score">9.47</span>
            <span class="scormem-item member">700K</span>
        """
    return f"""
        <html>
            <head>
                <meta property="og:url"
                      content="https://myanimelist.net/{media_type}/genre/46/Award_Winning">
                <link rel="next" href="?page=2">
            </head>
            <body>
                <div class="js-categories-seasonal">
                    <div class="{card_class}">
                        <div class="title">
                            <a class="link-title"
                               href="https://myanimelist.net/{media_type}/{media_id}/{title}">
                                {title}
                            </a>
                        </div>
                        <div class="image">
                            <img data-src="https://cdn.myanimelist.net/{media_type}/{media_id}.jpg">
                        </div>
                        {details}
                        <a href="/{media_type}/genre/46/Award_Winning"
                           title="Award Winning">Award Winning</a>
                        <div class="synopsis"><div class="preline">Synopsis.</div></div>
                    </div>
                </div>
                <div class="pagination">
                    <a>1 - 100</a>
                    <a>201 - 259</a>
                </div>
            </body>
        </html>
    """


class Search(TestCase):
    """Test the external API calls for media search."""

    @requires_provider_network
    def test_anime(self):
        """Test the search method for anime.

        Assert that all required keys are present in each entry.
        """
        response = mal.search(MediaTypes.ANIME.value, "Cowboy Bebop", 1)

        required_keys = {"media_id", "media_type", "title", "image"}

        for anime in response["results"]:
            self.assertTrue(all(key in anime for key in required_keys))

    @requires_provider_network
    def test_anime_not_found(self):
        """Test the search method for anime with no results."""
        response = mal.search(MediaTypes.ANIME.value, "q", 1)

        self.assertEqual(response["results"], [])

    @override_settings(MAL_NSFW=False)
    @patch(
        "app.providers.mal.cached_anime_posters",
        return_value={"5114": "https://cdn.myanimelist.net/canonical.jpg"},
    )
    @patch("app.providers.mal.services.api_request")
    def test_anime_discover_by_genre(
        self,
        api_request,
        _cached_posters,
    ):
        cache.clear()
        api_request.side_effect = [
            {
                "data": [
                    {"mal_id": 62, "name": "Isekai"},
                ],
            },
            {
                "pagination": {
                    "last_visible_page": 2,
                    "has_next_page": True,
                    "items": {
                        "count": 1,
                        "total": 26,
                        "per_page": 25,
                    },
                },
                "data": [
                    {
                        "mal_id": 5114,
                        "url": "https://myanimelist.net/anime/5114",
                        "title": "Hagane no Renkinjutsushi: Fullmetal Alchemist",
                        "title_english": "Fullmetal Alchemist: Brotherhood",
                        "images": {
                            "jpg": {
                                "large_image_url": "https://cdn.jikan.moe/5114.jpg",
                            },
                        },
                        "aired": {"from": "2009-04-05T00:00:00+00:00"},
                        "type": "TV",
                        "episodes": 64,
                        "synopsis": "Two brothers search for the Philosopher's Stone.",
                        "genres": [{"name": "Action"}],
                        "score": 9.1,
                        "scored_by": 2_000_000,
                        "members": 3_000_000,
                        "studios": [
                            {"mal_id": 4, "name": "Bones"},
                        ],
                    },
                ],
            },
        ]

        result = mal.discover_anime(
            page=1,
            page_size=25,
            genre="isekai",
        )

        genre_request = api_request.call_args_list[0]
        self.assertEqual(
            genre_request.args[2],
            "https://api.jikan.moe/v4/genres/anime",
        )
        self.assertEqual(genre_request.kwargs["params"], {})

        discover_request = api_request.call_args_list[1]
        self.assertEqual(
            discover_request.args[2],
            "https://api.jikan.moe/v4/anime",
        )
        self.assertEqual(
            discover_request.kwargs["params"],
            {
                "genres": "62",
                "page": 1,
                "limit": 25,
                "order_by": "scored_by",
                "sort": "desc",
                "sfw": True,
            },
        )
        self.assertEqual(discover_request.kwargs["timeout"], 3)
        self.assertFalse(discover_request.kwargs["retry_rate_limits"])
        self.assertEqual(result["total_results"], 26)
        self.assertEqual(result["per_page"], 25)
        self.assertEqual(result["results"][0]["media_id"], "5114")
        self.assertEqual(result["results"][0]["source"], Sources.MAL.value)
        self.assertEqual(
            result["results"][0]["display_title"],
            "Fullmetal Alchemist: Brotherhood",
        )
        self.assertEqual(
            result["results"][0]["image"],
            "https://cdn.myanimelist.net/canonical.jpg",
        )

    @patch("app.providers.mal.services.api_request")
    @patch(
        "app.providers.mal._jikan_anime_genres",
        return_value=[{"id": 1, "name": "Action"}],
    )
    def test_anime_discover_rejects_unknown_genre(
        self,
        _genres,
        api_request,
    ):
        with self.assertRaisesMessage(
            ValueError,
            "Unknown MAL anime genre: Not A Genre",
        ):
            mal.discover_anime(genre="Not A Genre")

        api_request.assert_not_called()

    @override_settings(MAL_NSFW=False)
    @patch(
        "app.providers.mal.cached_anime_posters",
        return_value={"16498": "https://cdn.myanimelist.net/aot.jpg"},
    )
    @patch("app.providers.anilist.anime_genre_page")
    @patch(
        "app.providers.mal._mal_genre_discovery_page",
        side_effect=requests.Timeout("MAL page unavailable"),
    )
    @patch(
        "app.providers.mal._jikan_anime_discovery_page",
        side_effect=requests.Timeout("Jikan unavailable"),
    )
    @patch(
        "app.providers.mal._jikan_anime_genres",
        return_value=[{"id": 1, "name": "Action"}],
    )
    def test_anime_discover_falls_back_to_anilist(
        self,
        _genres,
        _jikan_discover,
        _mal_page,
        anilist_discover,
        _cached_posters,
    ):
        cache.clear()
        anilist_discover.return_value = {
            "page_info": {
                "total": 100,
                "perPage": 25,
                "currentPage": 2,
                "lastPage": 4,
                "hasNextPage": True,
            },
            "media": [
                {
                    "idMal": 16498,
                    "title": {
                        "english": "Attack on Titan",
                        "romaji": "Shingeki no Kyojin",
                        "native": "進撃の巨人",
                    },
                    "coverImage": {
                        "extraLarge": "https://img.anilist.co/aot.jpg",
                    },
                    "release_date": "2013-04-07",
                    "format": "TV",
                    "episodes": 25,
                    "description": "Humanity fights for survival.",
                    "genres": ["Action", "Drama"],
                    "averageScore": 84,
                    "popularity": 800_000,
                },
            ],
        }

        result = mal.discover_anime(
            page=2,
            page_size=25,
            genre="Action",
        )

        anilist_discover.assert_called_once_with(
            "Action",
            page=2,
            page_size=25,
            include_adult=False,
            timeout=3,
        )
        self.assertEqual(result["total_results"], 100)
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["results"][0]["media_id"], "16498")
        self.assertEqual(result["results"][0]["source"], Sources.MAL.value)
        self.assertEqual(
            result["results"][0]["display_title"],
            "Attack on Titan",
        )
        self.assertEqual(
            result["results"][0]["image"],
            "https://cdn.myanimelist.net/aot.jpg",
        )
        self.assertTrue(
            cache.get(mal.ANIME_DISCOVER_JIKAN_FAILURE_KEY),
        )

    @override_settings(MAL_NSFW=False)
    @patch(
        "app.providers.mal.cached_manga_posters",
        return_value={"2": "https://cdn.myanimelist.net/berserk.jpg"},
    )
    @patch("app.providers.mal.services.api_request")
    def test_manga_discover_by_genre(
        self,
        api_request,
        _cached_posters,
    ):
        cache.clear()
        api_request.side_effect = [
            {
                "data": [
                    {"mal_id": 1, "name": "Action"},
                    {"mal_id": 40, "name": "Isekai"},
                    {"mal_id": 27, "name": "Seinen"},
                ],
            },
            {
                "pagination": {
                    "last_visible_page": 2,
                    "has_next_page": True,
                    "items": {
                        "count": 1,
                        "total": 26,
                        "per_page": 25,
                    },
                },
                "data": [
                    {
                        "mal_id": 2,
                        "url": "https://myanimelist.net/manga/2",
                        "title": "Berserk",
                        "title_english": "Berserk",
                        "images": {
                            "jpg": {
                                "large_image_url": (
                                    "https://cdn.jikan.moe/berserk.jpg"
                                ),
                            },
                        },
                        "published": {
                            "from": "1989-08-25T00:00:00+00:00",
                        },
                        "type": "Manga",
                        "chapters": 380,
                        "volumes": 42,
                        "synopsis": "A lone swordsman seeks revenge.",
                        "genres": [{"name": "Action"}],
                        "themes": [{"name": "Military"}],
                        "demographics": [{"name": "Seinen"}],
                        "score": 9.47,
                        "scored_by": 400_000,
                        "members": 700_000,
                    },
                ],
            },
        ]

        result = mal.discover_manga(
            page=1,
            page_size=25,
            genre="seinen",
        )

        genre_request = api_request.call_args_list[0]
        self.assertEqual(
            genre_request.args[2],
            "https://api.jikan.moe/v4/genres/manga",
        )
        self.assertEqual(genre_request.kwargs["params"], {})

        discover_request = api_request.call_args_list[1]
        self.assertEqual(
            discover_request.args[2],
            "https://api.jikan.moe/v4/manga",
        )
        self.assertEqual(
            discover_request.kwargs["params"],
            {
                "genres": "27",
                "page": 1,
                "limit": 25,
                "order_by": "scored_by",
                "sort": "desc",
                "sfw": True,
            },
        )
        self.assertEqual(discover_request.kwargs["timeout"], 3)
        self.assertFalse(discover_request.kwargs["retry_rate_limits"])
        self.assertEqual(result["total_results"], 26)
        self.assertEqual(result["per_page"], 25)
        manga = result["results"][0]
        self.assertEqual(manga["media_id"], "2")
        self.assertEqual(manga["source"], Sources.MAL.value)
        self.assertEqual(manga["media_type"], MediaTypes.MANGA.value)
        self.assertEqual(manga["title"], "Berserk")
        self.assertEqual(manga["display_title"], "Berserk")
        self.assertEqual(
            manga["image"],
            "https://cdn.myanimelist.net/berserk.jpg",
        )
        self.assertEqual(
            manga["genres"],
            ["Action", "Military", "Seinen"],
        )
        self.assertEqual(manga["max_progress"], 380)
        self.assertEqual(manga["details"]["number_of_volumes"], 42)

    @patch("app.providers.mal.services.api_request")
    @patch(
        "app.providers.mal._jikan_manga_genres",
        return_value=[{"id": 1, "name": "Action"}],
    )
    def test_manga_discover_rejects_unknown_genre(
        self,
        _genres,
        api_request,
    ):
        with self.assertRaisesMessage(
            ValueError,
            "Unknown MAL manga genre: Not A Genre",
        ):
            mal.discover_manga(genre="Not A Genre")

        api_request.assert_not_called()

    @override_settings(MAL_NSFW=False)
    @patch(
        "app.providers.mal.cached_manga_posters",
        return_value={"2": "https://cdn.myanimelist.net/berserk.jpg"},
    )
    @patch("app.providers.anilist.manga_genre_page")
    @patch(
        "app.providers.mal._mal_genre_discovery_page",
        side_effect=requests.Timeout("MAL page unavailable"),
    )
    @patch(
        "app.providers.mal._jikan_manga_discovery_page",
        side_effect=requests.Timeout("Jikan unavailable"),
    )
    @patch(
        "app.providers.mal._jikan_manga_genres",
        return_value=[{"id": 1, "name": "Action"}],
    )
    def test_manga_discover_falls_back_to_anilist(
        self,
        _genres,
        _jikan_discover,
        _mal_page,
        anilist_discover,
        _cached_posters,
    ):
        cache.clear()
        anilist_discover.return_value = {
            "page_info": {
                "total": 100,
                "perPage": 25,
                "currentPage": 2,
                "lastPage": 4,
                "hasNextPage": True,
            },
            "media": [
                {
                    "idMal": 2,
                    "title": {
                        "english": "Berserk",
                        "romaji": "Berserk",
                        "native": "ベルセルク",
                    },
                    "coverImage": {
                        "extraLarge": "https://img.anilist.co/berserk.jpg",
                    },
                    "release_date": "1989-08-25",
                    "format": "MANGA",
                    "chapters": 380,
                    "volumes": 42,
                    "description": "A lone swordsman seeks revenge.",
                    "genres": ["Action", "Drama"],
                    "averageScore": 93,
                    "popularity": 700_000,
                },
                {
                    "idMal": None,
                    "title": {"english": "AniList only"},
                },
            ],
        }

        result = mal.discover_manga(
            page=2,
            page_size=25,
            genre="Action",
        )

        anilist_discover.assert_called_once_with(
            "Action",
            page=2,
            page_size=25,
            include_adult=False,
            timeout=3,
        )
        self.assertEqual(result["total_results"], 100)
        self.assertEqual(result["page"], 2)
        self.assertEqual(len(result["results"]), 1)
        manga = result["results"][0]
        self.assertEqual(manga["media_id"], "2")
        self.assertEqual(manga["source"], Sources.MAL.value)
        self.assertEqual(manga["media_type"], MediaTypes.MANGA.value)
        self.assertEqual(manga["display_title"], "Berserk")
        self.assertEqual(
            manga["image"],
            "https://cdn.myanimelist.net/berserk.jpg",
        )
        self.assertEqual(manga["details"]["number_of_chapters"], 380)
        self.assertEqual(manga["details"]["number_of_volumes"], 42)
        self.assertTrue(
            cache.get(mal.MANGA_DISCOVER_JIKAN_FAILURE_KEY),
        )

    @patch(
        "app.providers.anilist.manga_genre_page",
        side_effect=RuntimeError("AniList unavailable"),
    )
    @patch(
        "app.providers.mal._mal_genre_discovery_page",
        side_effect=requests.Timeout("MAL page unavailable"),
    )
    @patch(
        "app.providers.mal._jikan_manga_discovery_page",
        side_effect=requests.Timeout("Jikan unavailable"),
    )
    @patch(
        "app.providers.mal._jikan_manga_genres",
        return_value=[{"id": 1, "name": "Action"}],
    )
    def test_manga_discover_returns_empty_when_all_providers_fail(
        self,
        _genres,
        _jikan_discover,
        _mal_page,
        _anilist_discover,
    ):
        cache.clear()

        result = mal.discover_manga(
            page=2,
            page_size=25,
            genre="Action",
        )

        self.assertEqual(
            result,
            {
                "page": 2,
                "per_page": 25,
                "total_results": 0,
                "total_pages": 0,
                "results": [],
            },
        )
        self.assertTrue(
            cache.get(mal.MANGA_DISCOVER_JIKAN_FAILURE_KEY),
        )

    @override_settings(MAL_NSFW=False)
    def test_award_winning_uses_mal_page_for_anime_and_manga(self):
        scenarios = (
            {
                "media_type": MediaTypes.ANIME.value,
                "media_id": "5114",
                "title": "Fullmetal Alchemist",
                "display_title": "Fullmetal Alchemist: Brotherhood",
                "discover": mal.discover_anime,
                "genres": "_jikan_anime_genres",
                "jikan": "_jikan_anime_discovery_page",
                "anilist": "_anilist_anime_discovery_page",
                "posters": "cached_anime_posters",
            },
            {
                "media_type": MediaTypes.MANGA.value,
                "media_id": "2",
                "title": "Berserk",
                "display_title": "Berserk",
                "discover": mal.discover_manga,
                "genres": "_jikan_manga_genres",
                "jikan": "_jikan_manga_discovery_page",
                "anilist": "_anilist_manga_discovery_page",
                "posters": "cached_manga_posters",
            },
        )
        for scenario in scenarios:
            with self.subTest(media_type=scenario["media_type"]):
                cache.clear()
                canonical_poster = (
                    f"https://cdn.myanimelist.net/canonical-"
                    f"{scenario['media_type']}.jpg"
                )
                cache_version = (
                    mal.ANIME_CACHE_VERSION
                    if scenario["media_type"] == MediaTypes.ANIME.value
                    else mal.MANGA_CACHE_VERSION
                )
                cache.set(
                    f"mal_{scenario['media_type']}_{scenario['media_id']}_"
                    f"{cache_version}",
                    {"display_title": scenario["display_title"]},
                )
                response = Mock(
                    text=mal_genre_page_html(
                        scenario["media_type"],
                        scenario["media_id"],
                        scenario["title"],
                    ),
                )
                response.raise_for_status.return_value = None
                with (
                    patch.object(
                        mal,
                        scenario["genres"],
                        return_value=[
                            {
                                "id": 46,
                                "name": "Award Winning",
                                "count": 259,
                            },
                        ],
                    ),
                    patch.object(
                        mal,
                        scenario["jikan"],
                        side_effect=requests.Timeout("Jikan unavailable"),
                    ) as jikan_discover,
                    patch.object(
                        mal.services.session,
                        "get",
                        return_value=response,
                    ) as page_request,
                    patch.object(mal, scenario["anilist"]) as anilist_discover,
                    patch.object(
                        mal,
                        scenario["posters"],
                        return_value={scenario["media_id"]: canonical_poster},
                    ),
                ):
                    result = scenario["discover"](
                        page=1,
                        page_size=25,
                        genre="Award Winning",
                    )

                jikan_discover.assert_called_once_with(
                    page=1,
                    page_size=25,
                    genre_id="46",
                )
                self.assertEqual(
                    page_request.call_args_list[0],
                    unittest.mock.call(
                        f"https://myanimelist.net/"
                        f"{scenario['media_type']}/genre/46",
                        params={"page": 1, "sfw": "1"},
                        headers={"User-Agent": "Spine/1.0"},
                        timeout=3,
                    ),
                )
                anilist_discover.assert_not_called()
                self.assertEqual(result["total_results"], 259)
                self.assertEqual(result["total_pages"], 11)
                item = result["results"][0]
                self.assertEqual(item["media_id"], scenario["media_id"])
                self.assertEqual(item["source"], Sources.MAL.value)
                self.assertEqual(item["media_type"], scenario["media_type"])
                self.assertEqual(item["image"], canonical_poster)
                self.assertEqual(item["display_title"], scenario["display_title"])

    def test_mal_genre_page_slices_requested_pagination(self):
        def page_data(media_type, genre_id, page):
            first_id = (page - 1) * 100 + 1
            item_count = 96 if page == 1 else 100 if page == 2 else 59
            return {
                "page": page,
                "per_page": 100,
                "total_results": 259,
                "has_next_page": page < 3,
                "results": [
                    {"media_id": str(media_id)}
                    for media_id in range(first_id, first_id + item_count)
                ],
            }

        with patch(
            "app.providers.mal._mal_genre_page_data",
            side_effect=page_data,
        ) as genre_page:
            result = mal._mal_genre_discovery_page(
                media_type=MediaTypes.MANGA.value,
                genre_id="46",
                page=7,
                page_size=15,
            )

        self.assertEqual(
            [item["media_id"] for item in result["results"]],
            [
                *[str(media_id) for media_id in range(91, 97)],
                *[str(media_id) for media_id in range(101, 110)],
            ],
        )
        self.assertEqual(result["page"], 7)
        self.assertEqual(result["per_page"], 15)
        self.assertEqual(result["total_results"], 259)
        self.assertEqual(result["total_pages"], 18)
        self.assertEqual(
            genre_page.call_args_list,
            [
                unittest.mock.call(MediaTypes.MANGA.value, "46", 1),
                unittest.mock.call(MediaTypes.MANGA.value, "46", 2),
            ],
        )

    @override_settings(MAL_NSFW=False)
    @patch(
        "app.providers.mal.services.session.get",
        side_effect=requests.Timeout("MAL unavailable"),
    )
    def test_mal_genre_page_uses_stale_cache_after_timeout(self, page_request):
        cache.clear()
        stale = {
            "page": 1,
            "per_page": 100,
            "total_results": 259,
            "has_next_page": True,
            "results": [{"media_id": "2"}],
        }
        prefix = mal._mal_genre_page_cache_prefix(
            MediaTypes.MANGA.value,
            "46",
            1,
        )
        cache.set(f"{prefix}:stale", stale, 60)

        result = mal._mal_genre_page_data(
            MediaTypes.MANGA.value,
            "46",
            1,
        )

        self.assertEqual(result, stale)
        page_request.assert_called_once_with(
            "https://myanimelist.net/manga/genre/46",
            params={"page": 1, "sfw": "1"},
            headers={"User-Agent": "Spine/1.0"},
            timeout=3,
        )

    def test_mal_genre_page_rejects_malformed_html(self):
        html = """
            <meta property="og:url"
                  content="https://myanimelist.net/manga/genre/46/Award_Winning">
            <div>MAL changed this page.</div>
        """

        with self.assertRaisesRegex(ValueError, "malformed manga genre page"):
            mal._parse_mal_genre_page(
                html,
                media_type=MediaTypes.MANGA.value,
                genre_id="46",
                page=1,
            )

    def test_mal_genre_index_parses_exact_names_and_ids(self):
        result = mal._parse_mal_genre_index(
            '<a href="/manga/genre/46/Award_Winning">'
            "Award Winning (507)</a>",
            MediaTypes.MANGA.value,
        )

        self.assertEqual(
            result,
            [{"id": "46", "name": "Award Winning"}],
        )

    def test_mal_genre_discovery_uses_fallback_order(self):
        order = []

        def jikan_provider(**_kwargs):
            order.append("jikan")
            raise requests.Timeout("Jikan unavailable")

        def mal_provider(**_kwargs):
            order.append("mal")
            raise ValueError("Malformed MAL page")

        def anilist_provider(**kwargs):
            order.append("anilist")
            self.assertEqual(kwargs["genre"], "Award Winning")
            return {
                "page": kwargs["page"],
                "per_page": kwargs["page_size"],
                "total_results": 1,
                "total_pages": 1,
                "results": [{"media_id": "2"}],
            }

        cache.clear()
        with (
            patch(
                "app.providers.mal._mal_genre_index",
                return_value=[
                    {"id": 46, "name": "Award Winning"},
                ],
            ) as genre_index,
            patch(
                "app.providers.mal._mal_genre_discovery_page",
                side_effect=mal_provider,
            ),
        ):
            result = mal._discover_mal_genre(
                media_type=MediaTypes.MANGA.value,
                page=1,
                page_size=25,
                genre="award winning",
                genres_provider=Mock(return_value=[]),
                jikan_provider=jikan_provider,
                anilist_provider=anilist_provider,
                jikan_failure_key="test:mal-genre:jikan-failure",
            )

        genre_index.assert_called_once_with(MediaTypes.MANGA.value)
        self.assertEqual(order, ["jikan", "mal", "anilist"])
        self.assertEqual(result["results"], [{"media_id": "2"}])

    def test_anime_genre_discovery_rejects_false_empty_first_page(self):
        empty_page = {
            "page": 1,
            "per_page": 25,
            "total_results": 0,
            "total_pages": 0,
            "results": [],
        }
        cache.clear()
        with (
            patch(
                "app.providers.mal._mal_genre_discovery_page",
                return_value=empty_page,
            ),
            self.assertRaisesRegex(
                mal.services.ProviderAPIError,
                "Anime genre discovery is temporarily unavailable",
            ),
        ):
            mal._discover_mal_genre(
                media_type=MediaTypes.ANIME.value,
                page=1,
                page_size=25,
                genre="Award Winning",
                genres_provider=Mock(
                    return_value=[
                        {"id": 46, "name": "Award Winning", "count": 259},
                    ],
                ),
                jikan_provider=Mock(return_value=empty_page),
                anilist_provider=Mock(return_value=empty_page),
                jikan_failure_key="test:mal-genre:false-empty",
            )

    @requires_provider_network
    def test_mangaupdates(self):
        """Test the search method for manga.

        Assert that all required keys are present in each entry.
        """
        response = mangaupdates.search("One Piece", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for manga in response["results"]:
            self.assertTrue(all(key in manga for key in required_keys))

    @requires_provider_network
    def test_manga_not_found(self):
        """Test the search method for manga with no results."""
        response = mangaupdates.search("", 1)

        self.assertEqual(response["results"], [])

    @requires_provider_network
    def test_tv(self):
        """Test the search method for TV shows.

        Assert that all required keys are present in each entry.
        """
        response = tmdb.search(MediaTypes.TV.value, "Breaking Bad", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for tv in response["results"]:
            self.assertTrue(all(key in tv for key in required_keys))

    @requires_provider_network
    def test_games(self):
        """Test the search method for games.

        Assert that all required keys are present in each entry.
        """
        response = igdb.search("Persona 5", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for game in response["results"]:
            self.assertTrue(all(key in game for key in required_keys))

    @requires_provider_network
    def test_books(self):
        """Test the search method for books.

        Assert that all required keys are present in each entry.
        """
        response = openlibrary.search("The Name of the Wind", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for book in response["results"]:
            self.assertTrue(all(key in book for key in required_keys))

    @requires_provider_network
    def test_comics(self):
        """Test the search method for comics.

        Assert that all required keys are present in each entry.
        """
        response = comicvine.search("Batman", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        for comic in response["results"]:
            self.assertTrue(all(key in comic for key in required_keys))

    @patch("app.providers.comicvine.cache")
    @patch("app.providers.comicvine.services.api_request")
    def test_comic_search_promotes_canonical_watchmen(
        self,
        mock_api_request,
        mock_cache,
    ):
        """Test localized editions no longer bury the canonical comic volume."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = {
            "number_of_total_results": 6,
            "results": [
                self._comicvine_volume(
                    53871,
                    "Watchmen",
                    "1999",
                    12,
                    "Abril",
                    "Brazilian publication in the Portuguese language.",
                ),
                self._comicvine_volume(
                    79545,
                    "Watchmen",
                    "1987",
                    12,
                    "Ediciones Zinco",
                    "Spanish publication of Watchmen",
                ),
                self._comicvine_volume(
                    3622,
                    "Watchmen",
                    "1986",
                    12,
                    "DC Comics",
                ),
                self._comicvine_volume(
                    29927,
                    "Watchmen",
                    "1987",
                    1,
                    "DC Comics",
                ),
                self._comicvine_volume(
                    106703,
                    "Watchmen Annotated",
                    "2017",
                    1,
                    "DC Comics",
                ),
                self._comicvine_volume(
                    44421,
                    "Watchmen - Die Wächter",
                    "1989",
                    6,
                    "Carlsen Verlag",
                ),
            ],
        }

        response = comicvine.search("watchmen", 1)

        media_ids = [result["media_id"] for result in response["results"]]
        self.assertEqual(media_ids[:3], ["3622", "29927", "106703"])
        self.assertCountEqual(
            media_ids,
            ["53871", "79545", "3622", "29927", "106703", "44421"],
        )
        self.assertGreater(media_ids.index("44421"), media_ids.index("106703"))
        self.assertEqual(
            response["results"][0]["subtitle"],
            "1986 · DC Comics · 12 issues",
        )
        self.assertNotIn("provider_rank_boost", response["results"][0])
        self.assertNotIn("first_publish_year", response["results"][0])

        params = mock_api_request.call_args.kwargs["params"]
        self.assertEqual(params["resources"], "volume")
        self.assertEqual(params["page"], 1)
        self.assertEqual(
            params["field_list"],
            "id,name,image,deck,publisher,start_year,count_of_issues",
        )
        self.assertTrue(
            mock_cache.set.call_args.args[0].startswith("search_v3_comicvine_comic_"),
        )
        # Real covers came back, so there's no extra cover request.
        mock_api_request.assert_called_once()

    @patch("app.providers.comicvine.cache")
    @patch("app.providers.comicvine.services.api_request")
    def test_comic_search_replaces_placeholder_covers(
        self,
        mock_api_request,
        mock_cache,
    ):
        """Test search's stand-in covers are swapped for the volumes' real covers."""
        mock_cache.get.return_value = None
        placeholder = {
            "medium_url": "https://comicvine.gamespot.com/a/uploads/"
            "scale_medium/11122/111222211/6373148-blank.png",
        }
        search_results = [
            self._comicvine_volume(17993, "Invincible", "2003", 144, "Image"),
            self._comicvine_volume(91662, "Invincible", "2005", 1, "Image"),
            self._comicvine_volume(5, "Invincible", "2019", 1, "Other"),
        ]
        search_results[0]["image"] = placeholder
        search_results[1]["image"] = placeholder
        mock_api_request.side_effect = [
            {"number_of_total_results": 3, "results": search_results},
            {
                "results": [
                    {"id": 17993, "image": {"medium_url": "https://cv/17993.jpg"}},
                    {"id": 91662, "image": {"medium_url": "https://cv/91662.jpg"}},
                ],
            },
        ]

        response = comicvine.search("invincible", 1)

        images = {result["media_id"]: result["image"] for result in response["results"]}
        self.assertEqual(
            images,
            {
                "17993": "https://cv/17993.jpg",
                "91662": "https://cv/91662.jpg",
                "5": "https://example.com/5.jpg",
            },
        )
        covers_call = mock_api_request.call_args_list[1]
        self.assertTrue(covers_call.args[2].endswith("/volumes/"))
        self.assertEqual(covers_call.kwargs["params"]["filter"], "id:17993|91662")

    @patch("app.providers.comicvine.cache")
    @patch("app.providers.comicvine.services.api_request")
    def test_comic_search_keeps_results_when_cover_request_fails(
        self,
        mock_api_request,
        mock_cache,
    ):
        """Test a failed cover request still returns the search results."""
        mock_cache.get.return_value = None
        volume = self._comicvine_volume(17993, "Invincible", "2003", 144, "Image")
        volume["image"] = {"medium_url": "https://cv/6373148-blank.png"}
        mock_api_request.side_effect = [
            {"number_of_total_results": 1, "results": [volume]},
            requests.exceptions.Timeout(),
        ]

        response = comicvine.search("invincible", 1)

        self.assertEqual(
            [result["image"] for result in response["results"]],
            ["https://cv/6373148-blank.png"],
        )

    @patch("app.providers.comicvine.cache")
    @patch("app.providers.comicvine.services.api_request")
    def test_comic_search_does_not_penalize_all_localized_title_group(
        self,
        mock_api_request,
        mock_cache,
    ):
        """Test foreign-title searches stay relevant when no primary edition exists."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = {
            "number_of_total_results": 3,
            "results": [
                self._comicvine_volume(
                    1,
                    "El Eternauta",
                    "1957",
                    1,
                    "Editorial Frontera",
                    "Spanish publication.",
                ),
                self._comicvine_volume(
                    2,
                    "El Eternauta",
                    "1969",
                    1,
                    "Ediciones Record",
                    "Argentine edition.",
                ),
                self._comicvine_volume(
                    3,
                    "El Eternauta Companion",
                    "2025",
                    1,
                    "Example Press",
                ),
            ],
        }

        response = comicvine.search("El Eternauta", 1)

        self.assertEqual(
            [result["media_id"] for result in response["results"][:2]],
            ["1", "2"],
        )

    def test_comic_search_does_not_treat_english_translation_as_localized(self):
        """Test desired English editions are not caught by foreign-edition markers."""
        self.assertFalse(
            comicvine._is_likely_localized_edition(  # noqa: SLF001
                {
                    "deck": "New English translations of the classic stories.",
                    "publisher": {"name": "Mad Cave Studios"},
                },
            ),
        )
        self.assertTrue(
            comicvine._is_likely_localized_edition(  # noqa: SLF001
                {
                    "deck": "Brazilian publication in the Portuguese language.",
                    "publisher": {"name": "Abril"},
                },
            ),
        )

    @staticmethod
    def _comicvine_volume(
        media_id,
        name,
        start_year,
        issue_count,
        publisher,
        deck=None,
    ):
        return {
            "id": media_id,
            "name": name,
            "start_year": start_year,
            "count_of_issues": issue_count,
            "publisher": {"name": publisher},
            "deck": deck,
            "image": {"medium_url": f"https://example.com/{media_id}.jpg"},
        }

    @requires_provider_network
    def test_hardcover(self):
        """Test the search method for books from Hardcover.

        Assert that all required keys are present in each entry.
        """
        response = hardcover.search("1984 George Orwell", 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        self.assertTrue(len(response["results"]) > 0)

        for book in response["results"]:
            self.assertTrue(all(key in book for key in required_keys))

    @requires_provider_network
    def test_hardcover_not_found(self):
        """Test the search method for books from Hardcover with no results."""
        response = hardcover.search("xjkqzptmvnsieurytowahdbfglc", 1)
        self.assertEqual(response["results"], [])

    @patch("app.providers.hardcover.services.api_request")
    def test_hardcover_title_query_is_capped(self, mock_api_request):
        """Test the long title is capped before search."""
        query = (
            "The Short Story of Architecture: A Pocket Guide to Key Styles, "
            "Buildings, Elements & Materials (Architectural History Introduction, "
            "A Guide to Architecture)"
        )
        capped_query = "The Short Story of Architecture: A Pocket Guide to"
        cache.delete(
            f"search_{Sources.HARDCOVER.value}_{MediaTypes.BOOK.value}_"
            f"{capped_query}_1",
        )
        mock_api_request.return_value = {
            "data": {
                "search": {
                    "results": {
                        "hits": [
                            {
                                "document": {
                                    "id": "123",
                                    "title": "The Short Story of Architecture",
                                    "image": {"url": "https://example.com/cover.jpg"},
                                },
                            },
                        ],
                        "found": 1,
                    },
                },
            },
        }

        response = hardcover.search(query, 1)
        required_keys = {"media_id", "media_type", "title", "image"}

        self.assertEqual(len(query), 156)
        self.assertEqual(hardcover.cap_search_query(query), capped_query)
        _, kwargs = mock_api_request.call_args
        self.assertEqual(kwargs["params"]["variables"]["query"], capped_query)
        self.assertTrue(len(response["results"]) > 0)

        for book in response["results"]:
            self.assertTrue(all(key in book for key in required_keys))

    def test_hardcover_title_query_cap_stops_at_word_boundary(self):
        """Test the long title cap does not split words."""
        query = "one two three four five six seven eight nine ten eleven twelve"

        self.assertEqual(
            hardcover.cap_search_query(query),
            "one two three four five six seven eight nine ten",
        )

    def test_search_text_normalization_is_forgiving(self):
        """Test search matching ignores accents, symbols, and case."""
        self.assertEqual(normalize_search_text("Pokémon: Blue!"), "pokemon blue")
        self.assertEqual(normalize_search_text("  HARRY--Potter  "), "harry potter")

    def test_ranking_does_not_overvalue_leading_filler_words(self):
        """Test titles with leading articles rank like close title matches."""
        results = [
            {
                "title": "Batman: The Long Halloween",
                "media_type": MediaTypes.MOVIE.value,
                "popularity": 50,
                "vote_count": 100,
            },
            {
                "title": "The Batman",
                "media_type": MediaTypes.MOVIE.value,
                "image": "https://example.com/the-batman.jpg",
                "popularity": 40,
                "vote_count": 100,
            },
        ]

        ranked = rank_results("batman", results, MediaTypes.MOVIE.value)

        self.assertEqual(ranked[0]["title"], "The Batman")

    def test_book_ranking_prefers_real_metadata_over_bare_exact_title(self):
        """Test useful book records outrank low-information exact-title shells."""
        results = [
            {"title": "Harry Potter", "media_type": MediaTypes.BOOK.value},
            {
                "title": "Harry Potter and the Philosopher's Stone",
                "media_type": MediaTypes.BOOK.value,
                "image": "https://example.com/hp1.jpg",
                "first_publish_year": 1997,
                "ratings_count": 100000,
                "author_name": ["J. K. Rowling"],
            },
        ]

        ranked = rank_results("harry potter", results, MediaTypes.BOOK.value)

        self.assertEqual(ranked[0]["title"], "Harry Potter and the Philosopher's Stone")

    def test_game_ranking_balances_popularity_and_relevance(self):
        """Test official popular games outrank low-signal spin-offs."""
        results = [
            {
                "title": "Pokemon Random Side Story",
                "media_type": MediaTypes.GAME.value,
                "total_rating_count": 2,
                "game_type": 6,
            },
            {
                "title": "Pokémon Red Version",
                "media_type": MediaTypes.GAME.value,
                "image": "https://example.com/red.jpg",
                "total_rating_count": 5000,
                "game_type": 0,
            },
        ]

        ranked = rank_results("pokemon", results, MediaTypes.GAME.value)

        self.assertEqual(ranked[0]["title"], "Pokémon Red Version")

    def test_exact_relevance_still_beats_unrelated_popularity(self):
        """Test popularity cannot bury an exact obscure title under unrelated hits."""
        results = [
            {
                "title": "The Popular Unrelated Game",
                "media_type": MediaTypes.GAME.value,
                "total_rating_count": 1000000,
                "game_type": 0,
            },
            {
                "title": "Obscure Quest",
                "media_type": MediaTypes.GAME.value,
                "image": "https://example.com/obscure.jpg",
                "game_type": 0,
            },
        ]

        ranked = rank_results("obscure quest", results, MediaTypes.GAME.value)

        self.assertEqual(ranked[0]["title"], "Obscure Quest")

    @patch("app.providers.igdb.cache")
    @patch("app.providers.igdb.get_access_token")
    @patch("app.providers.igdb.services.api_request")
    def test_igdb_search_uses_full_text_search(
        self,
        mock_api_request,
        mock_get_access_token,
        mock_cache,
    ):
        """Test game search does not use exact title substring matching."""
        mock_cache.get.return_value = None
        mock_get_access_token.return_value = "token"
        mock_api_request.side_effect = [
            [
                {
                    "id": 1,
                    "name": "Pokémon",
                },
            ],
            {"count": 1},
        ]

        response = igdb.search('pokemon "blue"', 1)

        self.assertEqual(response["results"][0]["title"], "Pokémon")
        search_request = mock_api_request.call_args_list[0]
        count_request = mock_api_request.call_args_list[1]
        self.assertTrue(search_request.args[2].endswith("/games"))
        self.assertTrue(count_request.args[2].endswith("/games/count"))
        self.assertIn('search "pokemon \\"blue\\"";', search_request.kwargs["data"])
        self.assertIn("total_rating_count", search_request.kwargs["data"])
        self.assertIn("game_type", search_request.kwargs["data"])
        self.assertNotIn("name ~", search_request.kwargs["data"])

    @patch("app.providers.igdb.cache")
    @patch("app.providers.igdb.get_access_token", return_value="token")
    @patch("app.providers.igdb.services.api_request")
    def test_igdb_preserved_search_skips_count_request(
        self,
        mock_api_request,
        _mock_get_access_token,
        mock_cache,
    ):
        mock_cache.get.return_value = None
        mock_api_request.return_value = [
            {
                "id": 1,
                "name": "Dune",
                "total_rating_count": 500,
                "total_rating": 80,
                "game_type": 0,
            },
        ]

        response = igdb.search("dune", 1, preserve_ranking_fields=True)

        self.assertEqual(mock_api_request.call_count, 1)
        self.assertTrue(mock_api_request.call_args.args[2].endswith("/games"))
        self.assertEqual(response["total_results"], 1)
        self.assertEqual(response["results"][0]["total_rating_count"], 500)

    @patch("app.providers.igdb.cache")
    @patch("app.providers.igdb.handle_error", return_value={"retry": True})
    @patch("app.providers.igdb.get_access_token", side_effect=["old", "new"])
    @patch("app.providers.igdb.services.api_request")
    def test_igdb_preserved_auth_retry_still_skips_count_request(
        self,
        mock_api_request,
        _mock_get_access_token,
        _mock_handle_error,
        mock_cache,
    ):
        mock_cache.get.return_value = None
        mock_api_request.side_effect = [
            requests.exceptions.HTTPError("unauthorized"),
            [{"id": 1, "name": "Dune", "game_type": 0}],
        ]

        response = igdb.search("dune", 1, preserve_ranking_fields=True)

        self.assertEqual(mock_api_request.call_count, 2)
        self.assertTrue(
            all(call.args[2].endswith("/games") for call in mock_api_request.call_args_list),
        )
        self.assertEqual(response["total_results"], 1)

    @patch("app.providers.openlibrary.cache")
    @patch("app.providers.openlibrary.services.api_request")
    def test_openlibrary_search_requests_ranking_fields(
        self,
        mock_api_request,
        mock_cache,
    ):
        """Test OpenLibrary search asks for fields used by ranking."""
        mock_cache.get.return_value = None
        mock_api_request.return_value = {
            "numFound": 1,
            "docs": [
                {
                    "title": "Harry Potter and the Philosopher's Stone",
                    "editions": {
                        "docs": [
                            {
                                "key": "/books/OL82563M",
                                "title": "Harry Potter and the Philosopher's Stone",
                                "cover_i": 123,
                            },
                        ],
                    },
                    "ratings_count": 1000,
                    "ratings_average": 4.5,
                    "edition_count": 30,
                    "first_publish_year": 1997,
                    "author_name": ["J. K. Rowling"],
                },
            ],
        }

        response = openlibrary.search("harry potter", 1)

        fields = mock_api_request.call_args.kwargs["params"]["fields"]
        self.assertIn("ratings_count", fields)
        self.assertIn("edition_count", fields)
        self.assertNotIn("ratings_count", response["results"][0])
