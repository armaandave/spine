"""With no pick, a title shows the artwork saved on it everywhere, as list cards do."""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import media_summary_from_item, media_summary_from_provider
from app.models import CustomPosterPreference, Item, MediaTypes, Sources
from app.templatetags.app_tags import get_user_poster_image

SAVED_POSTER = "https://image.tmdb.org/t/p/w500/saved-poster.jpg"
PROVIDER_POSTER = "https://image.tmdb.org/t/p/w500/provider-poster.jpg"
CURATED_POSTER = "https://image.tmdb.org/t/p/original/curated-poster.jpg"
SAVED_STILL = "https://image.tmdb.org/t/p/w500/saved-still.jpg"
FULL_SIZE_SAVED_STILL = "https://image.tmdb.org/t/p/original/saved-still.jpg"
LIVE_STILL = "https://image.tmdb.org/t/p/w500/live-still.jpg"


def _saved_episode(episode_number=1, image=SAVED_STILL):
    return Item.objects.create(
        source=Sources.TMDB.value,
        media_type=MediaTypes.EPISODE.value,
        media_id="1399",
        title="Game of Thrones",
        image=image,
        season_number=1,
        episode_number=episode_number,
    )


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

    @patch("api.services.media.provider_services.get_media_metadata", return_value=_movie_metadata())
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

    @patch("api.services.media.provider_services.get_media_metadata", return_value=_movie_metadata())
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

    @patch("api.services.media.provider_services.get_media_metadata")
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

    @patch("app.providers.tmdb.process_episodes")
    @patch("app.providers.tmdb.get_season_backdrop_images", return_value=[])
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_season_episode_rows_show_saved_stills(self, metadata_mock, _backdrops_mock, episodes_mock, *_mocks):
        metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "season",
            "source": "tmdb",
            "title": "Game of Thrones",
            "season_title": "Season 1",
            "image": PROVIDER_POSTER,
            "season_number": 1,
            "episodes": [{"episode_number": 1}, {"episode_number": 2}],
        }
        episodes_mock.return_value = [
            {"episode_number": 1, "title": "One", "image": LIVE_STILL},
            {"episode_number": 2, "title": "Two", "image": LIVE_STILL},
        ]
        # Saved at full size; the row still loads TMDB's w500 thumbnail.
        _saved_episode(image=FULL_SIZE_SAVED_STILL)

        response = self.client.get("/api/v1/media/tmdb/tv/1399/seasons/1/")

        self.assertEqual(
            [episode["image_url"] for episode in response.data["episodes"]],
            [SAVED_STILL, LIVE_STILL],
        )

    @patch("app.tasks.enrich_external_ratings.delay")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_episode_page_shows_the_saved_still_at_full_size(self, metadata_mock, *_mocks):
        metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "episode",
            "source": "tmdb",
            "title": "Winter Is Coming",
            "image": LIVE_STILL,
            "backdrop_path": "/live-still.jpg",
            "season_number": 1,
            "episode_number": 1,
        }
        _saved_episode()

        response = self.client.get("/api/v1/media/tmdb/episode/1399/?season_number=1&episode_number=1")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["backdrop_url"], FULL_SIZE_SAVED_STILL)

    @patch("app.providers.tmdb.get_episode_backdrop_images")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_episode_still_picker_marks_the_saved_still(self, metadata_mock, backdrops_mock, *_mocks):
        metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "episode",
            "source": "tmdb",
            "title": "Winter Is Coming",
            "image": LIVE_STILL,
            "backdrop_path": "/live-still.jpg",
            "season_number": 1,
            "episode_number": 1,
        }
        backdrops_mock.return_value = [
            {
                "url": "https://image.tmdb.org/t/p/original/live-still.jpg",
                "thumbnail_url": "https://image.tmdb.org/t/p/w780/live-still.jpg",
                "width": 1920,
                "height": 1080,
                "aspect_ratio": 1.778,
                "vote_average": 8,
                "vote_count": 5,
                "language": None,
            },
        ]
        _saved_episode()
        self.client.force_authenticate(self.viewer)

        response = self.client.get("/api/v1/media/tmdb/episode/1399/backdrops/?season_number=1&episode_number=1")

        backdrops = response.data["backdrops"]
        self.assertEqual(backdrops[0]["url"], FULL_SIZE_SAVED_STILL)
        self.assertTrue(backdrops[0]["is_original"])
        self.assertEqual([backdrop["url"] for backdrop in backdrops if backdrop["is_selected"]], [FULL_SIZE_SAVED_STILL])

    @patch("api.services.media.provider_services.get_person_page")
    def test_author_series_covers_use_picks_then_saved_covers(self, person_mock, *_mocks):
        books = [
            {
                "media_type": MediaTypes.BOOK.value,
                "source": Sources.HARDCOVER.value,
                "media_id": str(number),
                "title": f"Book {number}",
                "image": f"https://example.com/provider-{number}.jpg",
            }
            for number in (1, 2, 3)
        ]
        person_mock.return_value = {
            "source": Sources.HARDCOVER.value,
            "person_id": "80626",
            "name": "Dan Wells",
            "known_for_department": "Author",
            "series": [{"series_id": "1185", "source": "hardcover", "name": "John Cleaver", "book_count": 7, "books": books}],
            "credits": [],
        }
        saved_books = {
            media_id: Item.objects.create(
                source=Sources.HARDCOVER.value,
                media_type=MediaTypes.BOOK.value,
                media_id=media_id,
                title=f"Book {media_id}",
                image=SAVED_POSTER,
            )
            for media_id in ("1", "2")
        }
        CustomPosterPreference.objects.create(user=self.curator, item=saved_books["1"], custom_image_url=CURATED_POSTER)

        response = self.client.get("/api/v1/people/hardcover/80626/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["series"][0]["poster_urls"],
            [CURATED_POSTER, SAVED_POSTER, "https://example.com/provider-3.jpg"],
        )


class SavedPosterWebTests(TestCase):
    """The server-rendered pages follow the same saved-artwork rule."""

    def setUp(self):
        self.viewer = get_user_model().objects.create_user(username="viewer", password="strong-password-123")
        Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="129",
            title="Spirited Away",
            image=SAVED_POSTER,
        )

    def test_poster_tag_shows_the_saved_poster_over_live_metadata(self):
        live = {"source": "tmdb", "media_type": "movie", "media_id": "129", "image": PROVIDER_POSTER}

        self.assertEqual(get_user_poster_image(live, self.viewer), SAVED_POSTER)

    def test_poster_tag_reads_the_episode_it_was_given(self):
        _saved_episode(1, image="https://example.com/s1e1.jpg")
        second = _saved_episode(2, image="https://example.com/s1e2.jpg")

        self.assertEqual(get_user_poster_image(second, self.viewer), "https://example.com/s1e2.jpg")

    @patch("app.providers.services.get_media_metadata")
    @patch("app.providers.tmdb.process_episodes")
    def test_season_page_shows_saved_episode_stills(self, episodes_mock, metadata_mock):
        self.client.force_login(self.viewer)
        metadata_mock.return_value = {
            "title": "Game of Thrones",
            "media_id": "1399",
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.TV.value,
            "image": PROVIDER_POSTER,
            "season/1": {
                "title": "Season 1",
                "media_id": "1399",
                "media_type": MediaTypes.SEASON.value,
                "source": Sources.TMDB.value,
                "image": PROVIDER_POSTER,
                "episodes": [],
            },
        }
        episodes_mock.return_value = [
            {
                "media_id": "1399",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": number,
                "title": f"Episode {number}",
                "air_date": "2011-04-17",
                "image": LIVE_STILL,
            }
            for number in (1, 2)
        ]
        _saved_episode(image=FULL_SIZE_SAVED_STILL)

        response = self.client.get(
            reverse(
                "season_details",
                kwargs={"source": Sources.TMDB.value, "media_id": "1399", "title": "game-of-thrones", "season_number": 1},
            ),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [episode["image"] for episode in response.context["media"]["episodes"]],
            [SAVED_STILL, LIVE_STILL],
        )
