from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from api.services import media
from app.models import (
    CustomBackdropPreference,
    CustomPosterPreference,
    Item,
    MediaTypes,
    Sources,
)


class AnimeDetailEnrichmentTests(TestCase):
    """Verify MAL-first anime detail enrichment."""

    def setUp(self):
        cache.clear()

    @patch(
        "api.services.media.external_rating_payload",
        return_value={
            "external_ratings": [],
            "external_ratings_preparation": {"state": "ready"},
        },
    )
    @patch("api.services.media.anilist.anime")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_detail_merges_anilist_without_overwriting_mal(
        self,
        metadata_mock,
        anilist_mock,
        _ratings_mock,
    ):
        metadata_mock.return_value = {
            "media_id": "16498",
            "source": "mal",
            "media_type": "anime",
            "title": "Shingeki no Kyojin",
            "display_title": None,
            "image": "https://img.example/mal.jpg",
            "synopsis": "MAL synopsis",
            "details": {
                "season": "Spring 2013",
                "episodes": 25,
                "studios": ["Wit Studio"],
                "company_credits": [
                    {
                        "id": "1",
                        "source": "mal",
                        "name": "Wit Studio",
                        "roles": ["Studio"],
                    },
                ],
            },
            "posters": [
                {
                    "url": "https://img.example/mal.jpg",
                    "provider_name": "MyAnimeList",
                    "is_original": True,
                },
            ],
            "related": {
                "relations": [
                    {
                        "media_id": "100",
                        "source": "mal",
                        "media_type": "anime",
                        "title": "MAL Prequel",
                        "relation": "Prequel",
                    },
                ],
                "recommendations": [
                    {
                        "media_id": "6",
                        "source": "mal",
                        "media_type": "anime",
                        "title": "Recommendation",
                    },
                ],
            },
        }
        anilist_mock.return_value = {
            "display_title": "Attack on Titan",
            "source_url": "https://anilist.co/anime/16498",
            "backdrop": "https://img.example/banner.jpg",
            "posters": [
                {
                    "url": "https://img.example/anilist.jpg",
                    "provider_name": "AniList",
                },
            ],
            "backdrops": [
                {
                    "url": "https://img.example/banner.jpg",
                    "provider_name": "AniList",
                },
            ],
            "characters": [
                {
                    "person_id": "character:1",
                    "name": "Eren Yeager",
                    "role": "Main",
                    "image": "https://img.example/eren.jpg",
                },
            ],
            "cast": [
                {
                    "person_id": "110665",
                    "person_source": "anilist",
                    "name": "Yuki Kaji",
                    "character": "Eren Yeager",
                    "image": "https://img.example/kaji.jpg",
                },
            ],
            "rating_summary": {
                "average_score": 84,
                "rating_count": 200,
                "score_distribution": [
                    {"score": 80, "count": 120},
                    {"score": 90, "count": 80},
                ],
                "has_reviews": True,
                "url": "https://anilist.co/anime/16498",
            },
            "relations": [
                {
                    "media_id": "200",
                    "source": "mal",
                    "media_type": "manga",
                    "title": "Shingeki no Kyojin",
                    "display_title": "Attack on Titan",
                    "relation": "Source",
                    "image": "https://img.example/manga.jpg",
                },
                {
                    "media_id": "100",
                    "source": "mal",
                    "media_type": "anime",
                    "title": "AniList Prequel",
                    "display_title": "The Prequel",
                    "relation": "Prequel",
                },
            ],
        }

        with patch(
            "api.services.media.mal.anime_series",
            return_value={
                "series_id": "100",
                "source": "mal",
                "media_type": "anime",
                "name": "Attack on Titan",
                "item_count": 2,
                "items": [
                    {
                        "media_id": "100",
                        "source": "mal",
                        "media_type": "anime",
                        "title": "MAL Prequel",
                        "display_title": "The Prequel",
                        "image": "https://img.example/prequel.jpg",
                        "subtitle": "Anime · 2012 · 12 episodes",
                        "position": 1,
                    },
                    {
                        "media_id": "16498",
                        "source": "mal",
                        "media_type": "anime",
                        "title": "Shingeki no Kyojin",
                        "display_title": "Attack on Titan",
                        "image": "https://img.example/mal.jpg",
                        "subtitle": "Anime · 2013 · 25 episodes",
                        "position": 2,
                    },
                ],
            },
        ):
            result = media.media_detail(
                source=Sources.MAL.value,
                media_type=MediaTypes.ANIME.value,
                media_id="16498",
            )

        self.assertEqual(result["title"], "Shingeki no Kyojin")
        self.assertEqual(result["display_title"], "Attack on Titan")
        self.assertEqual(result["synopsis"], "MAL synopsis")
        self.assertEqual(result["backdrop_url"], "https://img.example/banner.jpg")
        self.assertEqual(result["cast"][0]["id"], "110665")
        self.assertEqual(result["cast"][0]["name"], "Yuki Kaji")
        self.assertEqual(result["cast"][0]["person_source"], "anilist")
        self.assertEqual(result["characters"][0]["role"], "Main")
        self.assertIsNone(result["characters"][0]["person_source"])
        self.assertEqual(result["details"]["anilist_rating"]["average_score"], 84)
        self.assertTrue(result["details"]["anilist_rating"]["has_reviews"])
        self.assertEqual(
            [section["id"] for section in result["related_sections"]],
            ["series", "relations", "recommendations"],
        )
        self.assertEqual(result["details"]["series_id"], "100")
        self.assertEqual(result["details"]["series_position"], 2)
        self.assertEqual(result["details"]["studios"], ["Wit Studio"])
        self.assertEqual(
            result["details"]["company_credits"][0]["id"],
            "1",
        )
        self.assertEqual(result["related_sections"][0]["title"], "Attack on Titan")
        relations = result["related_sections"][1]["items"]
        self.assertEqual([relation["relation"] for relation in relations], ["Source"])
        self.assertEqual(relations[0]["display_title"], "Attack on Titan")

    @patch(
        "api.services.media.external_rating_payload",
        return_value={
            "external_ratings": [],
            "external_ratings_preparation": {"state": "ready"},
        },
    )
    @patch("api.services.media.mal.anime_cast", return_value=[])
    @patch("api.services.media.anilist.anime", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_detail_still_returns_mal_when_anilist_has_no_data(
        self,
        metadata_mock,
        _anilist_mock,
        _cast_mock,
        _ratings_mock,
    ):
        metadata_mock.return_value = {
            "media_id": "1",
            "source": "mal",
            "media_type": "anime",
            "title": "Cowboy Bebop",
            "image": "https://img.example/bebop.jpg",
            "details": {"season": "Spring 1998"},
        }

        result = media.media_detail(
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            media_id="1",
        )

        self.assertEqual(result["title"], "Cowboy Bebop")
        self.assertIsNone(result["backdrop_url"])
        self.assertEqual(result["cast"], [])
        self.assertEqual(result["characters"], [])
        self.assertNotIn("anilist_rating", result["details"])

    @patch("api.services.media.anilist.anime", return_value={})
    @patch("api.services.media.mal.anime_cast")
    def test_detail_uses_jikan_cast_when_anilist_is_unavailable(
        self,
        cast_mock,
        _anilist_mock,
    ):
        cast_mock.return_value = [
            {
                "person_id": "11",
                "person_source": "mal",
                "name": "Yuki Kaji",
                "character": "Eren Yeager",
            },
        ]

        result = media._enrich_anime_metadata(
            {
                "media_id": "16498",
                "source": "mal",
                "media_type": "anime",
                "title": "Shingeki no Kyojin",
            },
            Sources.MAL.value,
        )

        self.assertEqual(result["cast"][0]["person_id"], "11")
        self.assertEqual(result["cast"][0]["person_source"], "mal")

    @patch("api.services.media.mal.anime_series")
    def test_person_series_uses_shared_canonical_name_and_complete_count(
        self,
        series_mock,
    ):
        series_mock.return_value = {
            "series_id": "1",
            "source": "mal",
            "media_type": "anime",
            "name": "Canonical Series Name",
            "item_count": 3,
            "items": [
                {
                    "media_id": "1",
                    "media_type": "anime",
                    "source": "mal",
                    "title": "Example Series",
                    "image": "https://img.example/one.jpg",
                },
                {
                    "media_id": "2",
                    "media_type": "anime",
                    "source": "mal",
                    "title": "Example Series 2",
                    "image": "https://img.example/two.jpg",
                },
                {
                    "media_id": "3",
                    "media_type": "anime",
                    "source": "mal",
                    "title": "Example Series 3",
                    "image": "https://img.example/three.jpg",
                },
            ],
        }
        raw_credits = [
            {
                "media_id": "1",
                "media_type": "anime",
                "title": "Example Series",
                "image": "https://img.example/one.jpg",
                "release_date": "2020-01-01",
                "vote_count": 100,
                "series_links": [{"media_id": "2", "relation": "Sequel"}],
            },
            {
                "media_id": "2",
                "media_type": "anime",
                "title": "Example Series 2",
                "image": "https://img.example/two.jpg",
                "release_date": "2021-01-01",
                "vote_count": 50,
                "series_links": [{"media_id": "1", "relation": "Prequel"}],
            },
        ]

        result = media._anime_person_series(raw_credits)

        self.assertEqual(result[0]["id"], "1")
        self.assertEqual(result[0]["media_type"], "anime")
        self.assertEqual(result[0]["name"], "Canonical Series Name")
        self.assertEqual(result[0]["item_count"], 3)
        self.assertEqual(
            result[0]["poster_urls"],
            [
                "https://img.example/one.jpg",
                "https://img.example/two.jpg",
                "https://img.example/three.jpg",
            ],
        )
        series_mock.assert_called_once_with("1")

    @patch("api.services.media.mal.anime_series")
    def test_person_series_uses_the_viewers_custom_poster(self, series_mock):
        user = get_user_model().objects.create_user(
            username="anime-person-series",
            password="strong-password-123",
        )
        item = Item.objects.create(
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            media_id="1",
            title="Example Series",
            image="https://cdn.myanimelist.net/default.jpg",
        )
        CustomPosterPreference.objects.create(
            user=user,
            item=item,
            custom_image_url="https://example.com/custom.jpg",
        )
        raw_credits = [
            {
                "media_id": "1",
                "media_type": "anime",
                "title": "Example Series",
                "image": "https://cdn.myanimelist.net/default.jpg",
                "release_date": "2020-01-01",
                "series_links": [{"media_id": "2", "relation": "Sequel"}],
                "_catalog_item": item,
            },
            {
                "media_id": "2",
                "media_type": "anime",
                "title": "Example Series 2",
                "image": "https://cdn.myanimelist.net/two.jpg",
                "release_date": "2021-01-01",
                "series_links": [{"media_id": "1", "relation": "Prequel"}],
            },
        ]
        series_mock.return_value = {
            "series_id": "1",
            "source": "mal",
            "media_type": "anime",
            "name": "Example Series",
            "item_count": 2,
            "items": [
                {
                    "media_id": "1",
                    "media_type": "anime",
                    "source": "mal",
                    "title": "Example Series",
                    "image": "https://cdn.myanimelist.net/default.jpg",
                },
                {
                    "media_id": "2",
                    "media_type": "anime",
                    "source": "mal",
                    "title": "Example Series 2",
                    "image": "https://cdn.myanimelist.net/two.jpg",
                },
            ],
        }

        result = media._anime_person_series(raw_credits, user=user)

        self.assertEqual(
            result[0]["poster_urls"],
            ["https://example.com/custom.jpg", "https://cdn.myanimelist.net/two.jpg"],
        )

    @patch(
        "api.services.media.mal.anime_series",
        side_effect=media.provider_services.ProviderAPIError(
            "mal",
            RuntimeError("provider unavailable"),
        ),
    )
    def test_person_series_failure_omits_only_the_series_group(self, _series_mock):
        raw_credits = [
            {
                "media_id": "1",
                "media_type": "anime",
                "title": "Example Series",
                "series_links": [{"media_id": "2", "relation": "Sequel"}],
            },
            {
                "media_id": "2",
                "media_type": "anime",
                "title": "Example Series 2",
                "series_links": [{"media_id": "1", "relation": "Prequel"}],
            },
        ]

        self.assertEqual(media._anime_person_series(raw_credits), [])


class AnimeArtworkTests(TestCase):
    """Verify anime uses the existing artwork preference flow."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="anime-art",
            password="strong-password-123",
        )
        self.item = Item.objects.create(
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            media_id="16498",
            title="Shingeki no Kyojin",
            image="https://img.example/mal.jpg",
        )

    @patch("api.services.media.anilist.anime")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_anime_artwork_combines_role_correct_sources(
        self,
        metadata_mock,
        anilist_mock,
    ):
        metadata_mock.return_value = {
            "media_id": "16498",
            "title": "Shingeki no Kyojin",
            "image": self.item.image,
            "posters": [
                {
                    "url": self.item.image,
                    "provider_name": "MyAnimeList",
                    "is_original": True,
                },
            ],
        }
        anilist_mock.return_value = {
            "posters": [
                {
                    "url": "https://img.example/anilist.jpg",
                    "provider_name": "AniList",
                    "provider_url": "https://anilist.co/anime/16498",
                },
            ],
            "backdrop": "https://img.example/banner.jpg",
            "backdrops": [
                {
                    "url": "https://img.example/banner.jpg",
                    "provider_name": "AniList",
                    "provider_url": "https://anilist.co/anime/16498",
                },
            ],
        }

        posters = media.poster_options(
            source="mal",
            media_type="anime",
            media_id="16498",
            user=self.user,
        )["posters"]
        backdrops = media.backdrop_options(
            source="mal",
            media_type="anime",
            media_id="16498",
            user=self.user,
        )["backdrops"]

        self.assertEqual(
            [poster["provider_name"] for poster in posters],
            ["MyAnimeList", "AniList"],
        )
        self.assertEqual([backdrop["url"] for backdrop in backdrops], ["https://img.example/banner.jpg"])
        self.assertTrue(backdrops[0]["is_selected"])

    @patch("api.services.media.build_accent_palette", return_value={"accent": "#abcdef"})
    @patch("api.services.media.get_poster_accent_from_url", return_value="#abcdef")
    def test_anime_artwork_preferences_use_existing_models(
        self,
        _accent_mock,
        _palette_mock,
    ):
        media.save_poster_preference(
            source="mal",
            media_type="anime",
            media_id="16498",
            poster_url="https://img.example/custom-poster.jpg",
            user=self.user,
        )
        media.save_backdrop_preference(
            source="mal",
            media_type="anime",
            media_id="16498",
            backdrop_url="https://img.example/custom-banner.jpg",
            user=self.user,
        )

        self.assertEqual(
            CustomPosterPreference.objects.get(user=self.user, item=self.item).custom_image_url,
            "https://img.example/custom-poster.jpg",
        )
        self.assertEqual(
            CustomBackdropPreference.objects.get(user=self.user, item=self.item).custom_image_url,
            "https://img.example/custom-banner.jpg",
        )
