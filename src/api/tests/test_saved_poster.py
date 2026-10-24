"""With no pick, a title shows the poster saved on it everywhere, as list cards do."""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import media_summary_from_item, media_summary_from_provider
from app.models import CustomPosterPreference, Item, MediaTypes, Sources

SAVED_POSTER = "https://image.tmdb.org/t/p/w500/saved-poster.jpg"
PROVIDER_POSTER = "https://image.tmdb.org/t/p/w500/provider-poster.jpg"
CURATED_POSTER = "https://image.tmdb.org/t/p/original/curated-poster.jpg"


def _movie_metadata(media_id="129"):
    return {
        "media_id": media_id,
        "media_type": "movie",
        "source": "tmdb",
        "title": "Spirited Away",
        "image": PROVIDER_POSTER,
        "backdrop_path": "/provider-backdrop.jpg",
    }


@patch("app.providers.tmdb.get_title_logos", return_value=[])
@patch("app.providers.tmdb.get_title_logo", return_value=None)
@patch("app.providers.tmdb.get_backdrop_images", return_value=[])
@patch("app.providers.mdblist.get_media_ratings", return_value={})
@patch("api.services.media.provider_services.get_media_metadata", return_value=_movie_metadata())
class SavedPosterTests(TestCase):
    """A pick wins; without one, the saved poster beats the provider's current one."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        users = get_user_model().objects
        self.curator = users.create_user(username="curator", password="strong-password-123", is_staff=True)
        self.viewer = users.create_user(username="viewer", password="strong-password-123")
        self.item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="129",
            title="Spirited Away",
            image=SAVED_POSTER,
        )

    def test_media_page_matches_list_cards_when_nobody_picked(self, *_mocks):
        self.client.force_authenticate(self.viewer)
        viewer = self.client.get("/api/v1/media/tmdb/movie/129/")
        self.client.force_authenticate(None)
        anonymous = self.client.get("/api/v1/media/tmdb/movie/129/")

        list_card = media_summary_from_item(self.item, user=self.viewer)
        self.assertEqual(list_card["poster_url"], SAVED_POSTER)
        for response in (viewer, anonymous):
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["poster_url"], SAVED_POSTER)
            self.assertEqual(response.data["image_url"], SAVED_POSTER)
            self.assertIsNone(response.data["custom_poster_url"])

    def test_picks_still_win_over_the_saved_poster(self, *_mocks):
        CustomPosterPreference.objects.create(user=self.curator, item=self.item, custom_image_url=CURATED_POSTER)

        response = self.client.get("/api/v1/media/tmdb/movie/129/")

        self.assertEqual(response.data["poster_url"], CURATED_POSTER)

    def test_search_cards_use_the_provider_poster_only_without_a_saved_one(self, *_mocks):
        placeholder = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="4935",
            title="Howl's Moving Castle",
            image=settings.IMG_NONE,
        )
        episode = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            media_id="1399",
            title="Winter Is Coming",
            image="https://example.com/saved-still.jpg",
            season_number=1,
            episode_number=1,
        )

        saved = media_summary_from_provider(_movie_metadata(), "movie", "tmdb")
        missing = media_summary_from_provider(_movie_metadata(placeholder.media_id), "movie", "tmdb")
        not_in_spine = media_summary_from_provider(_movie_metadata("12477"), "movie", "tmdb")
        episode_card = media_summary_from_provider(
            {"media_id": "1399", "season_number": 1, "episode_number": 1, "image": PROVIDER_POSTER},
            "episode",
            "tmdb",
            item=episode,
        )

        self.assertEqual(saved["poster_url"], SAVED_POSTER)
        self.assertEqual(missing["poster_url"], PROVIDER_POSTER)
        self.assertEqual(not_in_spine["poster_url"], PROVIDER_POSTER)
        self.assertIsNone(episode_card["poster_url"])

    def test_tv_season_rows_show_saved_season_posters(self, metadata_mock, *_mocks):
        metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "tv",
            "source": "tmdb",
            "title": "Game of Thrones",
            "image": PROVIDER_POSTER,
            "related": {
                "seasons": [
                    {"season_number": number, "season_title": f"Season {number}", "image": PROVIDER_POSTER}
                    for number in (1, 2, 3, 4)
                ],
            },
        }
        seasons = {
            number: Item.objects.create(
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                media_id="1399",
                title="Game of Thrones",
                image=image,
                season_number=number,
            )
            for number, image in ((1, SAVED_POSTER), (2, settings.IMG_NONE), (3, SAVED_POSTER))
        }
        CustomPosterPreference.objects.create(user=self.curator, item=seasons[3], custom_image_url=CURATED_POSTER)

        response = self.client.get("/api/v1/media/tmdb/tv/1399/")

        self.assertEqual(
            [season["image_url"] for season in response.data["seasons"]],
            [SAVED_POSTER, PROVIDER_POSTER, CURATED_POSTER, PROVIDER_POSTER],
        )
