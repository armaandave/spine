from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from api.services import media
from app.models import CustomBackdropPreference, Item, MediaTypes, Sources

READY_RATINGS = {
    "external_ratings": [],
    "external_ratings_preparation": {"state": "ready"},
}


class MangaDetailEnrichmentTests(TestCase):
    """Verify MAL-first manga detail enrichment and fallback behavior."""

    def setUp(self):
        cache.clear()

    @patch("api.services.media.external_rating_payload", return_value=READY_RATINGS)
    @patch("api.services.media.anilist.manga")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_mal_detail_adds_banner_people_typed_relations_and_english_title(
        self,
        metadata_mock,
        anilist_mock,
        _ratings_mock,
    ):
        metadata_mock.return_value = {
            "media_id": "23390",
            "source": "mal",
            "media_type": "manga",
            "title": "Shingeki no Kyojin",
            "display_title": "Attack on Titan",
            "image": "https://img.example/mal.jpg",
            "posters": [{"url": "https://img.example/mal.jpg"}],
            "synopsis": "MAL synopsis",
            "score": 8.6,
            "score_count": 1000,
            "creators": [
                {
                    "person_id": "mal:11705",
                    "name": "Hajime Isayama",
                    "role": "Story & Art",
                },
            ],
            "details": {
                "start_date": "2009-09-09",
                "end_date": "2021-04-09",
                "status": "Finished",
            },
            "related": {
                "relations": [
                    {
                        "media_id": "16498",
                        "source": "mal",
                        "media_type": "anime",
                        "title": "Shingeki no Kyojin",
                        "relation": "Adaptation",
                    },
                ],
                "recommendations": [],
            },
        }
        anilist_mock.return_value = {
            "source_url": "https://anilist.co/manga/30013",
            "display_title": "Attack on Titan",
            "backdrop": "https://img.example/banner.jpg",
            "posters": [{"url": "https://img.example/anilist.jpg"}],
            "backdrops": [{"url": "https://img.example/banner.jpg"}],
            "rating": {
                "value": 85,
                "vote_count": 200,
                "url": "https://anilist.co/manga/30013",
            },
            "characters": [
                {
                    "person_id": "character:1",
                    "name": "Eren Yeager",
                    "role": "Main",
                    "image": "https://img.example/eren.jpg",
                },
            ],
            "creators": [
                {
                    "person_id": "staff:2",
                    "name": "Hajime Isayama",
                    "role": "Story & Art",
                    "image": "https://img.example/isayama.jpg",
                },
            ],
            "relations": [],
            "recommendations": [],
        }

        result = media.media_detail(
            source=Sources.MAL.value,
            media_type=MediaTypes.MANGA.value,
            media_id="23390",
        )

        self.assertEqual(result["title"], "Shingeki no Kyojin")
        self.assertEqual(result["display_title"], "Attack on Titan")
        self.assertEqual(result["backdrop_url"], "https://img.example/banner.jpg")
        self.assertEqual(result["characters"][0]["name"], "Eren Yeager")
        self.assertEqual(result["crew"][0]["name"], "Hajime Isayama")
        self.assertEqual(
            result["crew"][0]["image_url"],
            "https://img.example/isayama.jpg",
        )
        self.assertEqual(
            [section["id"] for section in result["related_sections"]],
            ["relations"],
        )
        self.assertEqual(
            result["related_sections"][0]["items"][0]["relation"],
            "Adaptation",
        )

    @patch("api.services.media.external_rating_payload", return_value=READY_RATINGS)
    @patch("api.services.media.anilist.manga")
    @patch("api.services.media.mal.manga")
    @patch("api.services.media.mal.match_manga", return_value="23390")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_matched_mangaupdates_keeps_identity_but_uses_mal_first(
        self,
        metadata_mock,
        _match_mock,
        mal_mock,
        anilist_mock,
        ratings_mock,
    ):
        metadata_mock.return_value = {
            "media_id": "mu-1",
            "source": "mangaupdates",
            "media_type": "manga",
            "source_url": "https://www.mangaupdates.com/series/mu-1",
            "title": "MU Title",
            "image": "https://img.example/mu.jpg",
            "posters": [{"url": "https://img.example/mu.jpg"}],
            "synopsis": "MU synopsis",
            "score": 8.1,
            "score_count": 50,
            "details": {"year": "2009", "format": "Manga"},
            "related": {"relations": [], "recommendations": []},
        }
        mal_mock.return_value = {
            "media_id": "23390",
            "source": "mal",
            "media_type": "manga",
            "source_url": "https://myanimelist.net/manga/23390",
            "title": "Shingeki no Kyojin",
            "display_title": "Attack on Titan",
            "image": "https://img.example/mal.jpg",
            "posters": [{"url": "https://img.example/mal.jpg"}],
            "synopsis": "MAL synopsis",
            "score": 8.6,
            "score_count": 1000,
            "details": {
                "start_date": "2009-09-09",
                "end_date": "2021-04-09",
                "status": "Finished",
            },
            "related": {"relations": [], "recommendations": []},
        }
        anilist_mock.return_value = {
            "source_url": "https://anilist.co/manga/30013",
            "posters": [{"url": "https://img.example/anilist.jpg"}],
            "rating": {
                "value": 85,
                "vote_count": 200,
                "url": "https://anilist.co/manga/30013",
            },
        }

        result = media.media_detail(
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            media_id="mu-1",
        )

        self.assertEqual(result["ref"]["source"], "mangaupdates")
        self.assertEqual(result["ref"]["media_id"], "mu-1")
        self.assertEqual(result["title"], "Shingeki no Kyojin")
        self.assertEqual(result["display_title"], "Attack on Titan")
        self.assertEqual(result["synopsis"], "MAL synopsis")
        rating_metadata = ratings_mock.call_args.kwargs["metadata"]
        self.assertIsNone(rating_metadata["score"])
        self.assertEqual(rating_metadata["external_ratings"]["mal"]["value"], 8.6)
        self.assertEqual(
            rating_metadata["external_ratings"]["anilist"]["value"],
            85,
        )

    @patch("api.services.media.external_rating_payload", return_value=READY_RATINGS)
    @patch("api.services.media.anilist.manga")
    @patch("api.services.media.mal.match_manga", return_value=None)
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_unmatched_mangaupdates_keeps_native_related_and_hides_rating(
        self,
        metadata_mock,
        _match_mock,
        anilist_mock,
        ratings_mock,
    ):
        metadata_mock.return_value = {
            "media_id": "mu-2",
            "source": "mangaupdates",
            "media_type": "manga",
            "title": "Unmatched Manga",
            "image": "https://img.example/mu.jpg",
            "score": 7.5,
            "score_count": 10,
            "details": {"year": "2020", "format": "Manga"},
            "related": {
                "relations": [
                    {
                        "media_id": "mu-3",
                        "source": "mangaupdates",
                        "media_type": "manga",
                        "title": "Native Relation",
                    },
                ],
                "recommendations": [],
            },
        }

        result = media.media_detail(
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            media_id="mu-2",
        )

        anilist_mock.assert_not_called()
        self.assertIsNone(ratings_mock.call_args.kwargs["metadata"]["score"])
        self.assertEqual(
            result["related_sections"][0]["items"][0]["relation"],
            "Related",
        )


class MangaPosterTests(TestCase):
    """Verify manga poster providers share the existing picker contract."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="manga-art",
            password="strong-password-123",
        )
        self.item = Item.objects.create(
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            media_id="mu-1",
            title="Attack on Titan",
            image="https://img.example/mu.jpg",
        )

    @patch("api.services.media.anilist.manga")
    @patch("api.services.media.mal.manga")
    @patch("api.services.media.mal.match_manga", return_value="23390")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_poster_options_combine_mal_anilist_and_mangaupdates(
        self,
        metadata_mock,
        _match_mock,
        mal_mock,
        anilist_mock,
    ):
        metadata_mock.return_value = {
            "media_id": "mu-1",
            "source": "mangaupdates",
            "media_type": "manga",
            "title": "Attack on Titan",
            "image": self.item.image,
            "posters": [
                {
                    "url": self.item.image,
                    "provider_name": "MangaUpdates",
                },
            ],
            "details": {"year": "2009", "format": "Manga"},
        }
        mal_mock.return_value = {
            "media_id": "23390",
            "title": "Shingeki no Kyojin",
            "image": "https://img.example/mal.jpg",
            "posters": [
                {
                    "url": "https://img.example/mal.jpg",
                    "provider_name": "MyAnimeList",
                },
            ],
            "details": {},
        }
        anilist_mock.return_value = {
            "posters": [
                {
                    "url": "https://img.example/anilist.jpg",
                    "provider_name": "AniList",
                },
            ],
        }

        posters = media.poster_options(
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            media_id="mu-1",
            user=self.user,
        )["posters"]

        self.assertEqual(
            [poster["provider_name"] for poster in posters],
            ["MyAnimeList", "AniList", "MangaUpdates"],
        )
        # With no pick, the picker marks the saved poster, which the page shows.
        self.assertEqual(
            [poster["url"] for poster in posters if poster["is_selected"]],
            [self.item.image],
        )

    @patch("api.services.media.anilist.manga")
    @patch("api.services.media.mal.manga")
    @patch("api.services.media.mal.match_manga", return_value="23390")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_single_manga_backdrop_can_be_opened_and_saved(
        self,
        metadata_mock,
        _match_mock,
        mal_mock,
        anilist_mock,
    ):
        metadata_mock.return_value = {
            "media_id": "mu-1",
            "source": "mangaupdates",
            "media_type": "manga",
            "title": "Attack on Titan",
            "image": self.item.image,
            "details": {"year": "2009", "format": "Manga"},
        }
        mal_mock.return_value = {
            "media_id": "23390",
            "title": "Shingeki no Kyojin",
            "details": {},
        }
        anilist_mock.return_value = {
            "backdrop": "https://img.example/banner.jpg",
            "backdrops": [{"url": "https://img.example/banner.jpg"}],
        }

        backdrops = media.backdrop_options(
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            media_id="mu-1",
            user=self.user,
        )["backdrops"]
        media.save_backdrop_preference(
            source=Sources.MANGAUPDATES.value,
            media_type=MediaTypes.MANGA.value,
            media_id="mu-1",
            backdrop_url=backdrops[0]["url"],
            user=self.user,
        )

        self.assertEqual(len(backdrops), 1)
        self.assertTrue(backdrops[0]["is_selected"])
        self.assertEqual(
            CustomBackdropPreference.objects.get(
                user=self.user,
                item=self.item,
            ).custom_image_url,
            "https://img.example/banner.jpg",
        )
