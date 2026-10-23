"""Curated artwork: an admin's pick is everyone's default until they pick their own."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import media_summary_from_item
from app.models import (
    CustomBackdropPreference,
    CustomLogoPreference,
    CustomPosterPreference,
    Item,
    MediaLike,
    MediaTypes,
    Sources,
)
from app.templatetags.app_tags import get_user_poster_image
from app.utils.color import build_accent_palette

PROVIDER_POSTER = "https://example.com/provider-poster.jpg"
CURATED_POSTER = "https://example.com/curated-poster.jpg"
CURATED_BACKDROP = "https://example.com/curated-backdrop.jpg"
CURATED_LOGO = "https://image.tmdb.org/t/p/w500/curated-logo.png"
AUTOMATIC_LOGO = {
    "url": "https://image.tmdb.org/t/p/w500/automatic-logo.png",
    "width": 1200,
    "height": 400,
    "aspect_ratio": 3.0,
}


def _movie_metadata(media_id="550"):
    return {
        "media_id": media_id,
        "media_type": "movie",
        "source": "tmdb",
        "title": "Fight Club",
        "image": PROVIDER_POSTER,
        "backdrop_path": "/provider-backdrop.jpg",
    }


class CuratedArtworkTests(TestCase):
    """An admin's poster, backdrop and logo picks are the default on every surface."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        users = get_user_model().objects
        self.curator = users.create_user(username="curator", password="strong-password-123", is_staff=True)
        self.viewer = users.create_user(username="viewer", password="strong-password-123")
        self.item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            # An importer reset the stored cover after the curator picked one.
            image=PROVIDER_POSTER,
            poster_accent_color="#aa0000",
        )

    def _curate(self, model, url, item=None):
        return model.objects.create(user=self.curator, item=item or self.item, custom_image_url=url)

    @patch("app.providers.tmdb.get_title_logos", return_value=[])
    @patch("app.providers.tmdb.get_title_logo", return_value=AUTOMATIC_LOGO)
    @patch("app.providers.tmdb.get_backdrop_images", return_value=[])
    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata", return_value=_movie_metadata())
    def test_detail_defaults_to_curator_poster_backdrop_and_logo(self, *_mocks):
        self._curate(CustomPosterPreference, CURATED_POSTER)
        self._curate(CustomBackdropPreference, CURATED_BACKDROP)
        self._curate(CustomLogoPreference, CURATED_LOGO)

        self.client.force_authenticate(self.viewer)
        viewer = self.client.get("/api/v1/media/tmdb/movie/550/")
        self.client.force_authenticate(None)
        anonymous = self.client.get("/api/v1/media/tmdb/movie/550/")

        for response in (viewer, anonymous):
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response.data["poster_url"], CURATED_POSTER)
            self.assertEqual(response.data["backdrop_url"], CURATED_BACKDROP)
            self.assertEqual(response.data["logo_url"], CURATED_LOGO)
            self.assertIsNone(response.data["custom_poster_url"])
            self.assertIsNone(response.data["custom_backdrop_url"])
            self.assertIsNone(response.data["custom_logo_url"])

    @patch("app.providers.tmdb.get_title_logos", return_value=[])
    @patch("app.providers.tmdb.get_title_logo", return_value=AUTOMATIC_LOGO)
    @patch("app.providers.tmdb.get_backdrop_images", return_value=[])
    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata", return_value=_movie_metadata())
    def test_viewer_pick_wins_over_curator_pick(self, *_mocks):
        self._curate(CustomPosterPreference, CURATED_POSTER)
        self._curate(CustomLogoPreference, CURATED_LOGO)
        CustomPosterPreference.objects.create(
            user=self.viewer,
            item=self.item,
            custom_image_url="https://example.com/viewer-poster.jpg",
            accent_color="#00bb00",
        )
        CustomLogoPreference.objects.create(
            user=self.viewer,
            item=self.item,
            custom_image_url="https://image.tmdb.org/t/p/w500/viewer-logo.png",
        )
        self.client.force_authenticate(self.viewer)

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.data["custom_poster_url"], "https://example.com/viewer-poster.jpg")
        self.assertEqual(response.data["poster_accent_color"], "#00bb00")
        self.assertEqual(response.data["logo_url"], "https://image.tmdb.org/t/p/w500/viewer-logo.png")
        self.assertEqual(response.data["custom_logo_url"], "https://image.tmdb.org/t/p/w500/viewer-logo.png")

    def test_item_summaries_default_to_curator_artwork(self):
        self._curate(CustomPosterPreference, CURATED_POSTER)
        self._curate(CustomBackdropPreference, CURATED_BACKDROP)

        summary = media_summary_from_item(self.item, user=self.viewer)

        self.assertEqual(summary["poster_url"], CURATED_POSTER)
        self.assertEqual(summary["image_url"], CURATED_POSTER)
        self.assertEqual(summary["backdrop_url"], CURATED_BACKDROP)
        self.assertIsNone(summary["custom_poster_url"])
        self.assertIsNone(summary["custom_backdrop_url"])

    def test_episode_summary_defaults_to_curator_backdrop(self):
        episode = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            media_id="1399",
            title="Winter Is Coming",
            image="https://example.com/provider-still.jpg",
            season_number=1,
            episode_number=1,
        )
        self._curate(CustomBackdropPreference, CURATED_BACKDROP, item=episode)

        summary = media_summary_from_item(episode)

        self.assertIsNone(summary["poster_url"])
        self.assertEqual(summary["backdrop_url"], CURATED_BACKDROP)

    def test_liked_media_page_loads_curator_artwork_in_bounded_queries(self):
        self.client.force_authenticate(self.viewer)

        def curator_queries(count):
            for index in range(count):
                item = Item.objects.create(
                    source=Sources.TMDB.value,
                    media_type=MediaTypes.MOVIE.value,
                    media_id=f"liked-{count}-{index}",
                    title=f"Liked {index}",
                    image=PROVIDER_POSTER,
                )
                self._curate(CustomPosterPreference, CURATED_POSTER, item=item)
                self._curate(CustomBackdropPreference, CURATED_BACKDROP, item=item)
                MediaLike.objects.create(user=self.viewer, item=item)
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get("/api/v1/me/liked-media/")
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            for media in response.data["results"]:
                self.assertEqual(media["poster_url"], CURATED_POSTER)
                self.assertEqual(media["backdrop_url"], CURATED_BACKDROP)
            MediaLike.objects.filter(user=self.viewer).delete()
            # Admin picks are the only artwork queries that join on is_staff.
            return sum("is_staff" in query["sql"] for query in queries.captured_queries)

        self.assertEqual(curator_queries(1), 2)
        self.assertEqual(curator_queries(4), 2)

    @patch("api.services.media.get_poster_accent_from_url", return_value="#112233")
    def test_non_curator_poster_pick_stays_private(self, _accent_mock):
        self.client.force_authenticate(self.viewer)

        response = self.client.put(
            "/api/v1/media/tmdb/movie/550/poster/",
            {"poster_url": "https://example.com/viewer-poster.jpg"},
            format="json",
        )

        accent = build_accent_palette("#112233")["accent"]
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["custom_poster_url"], "https://example.com/viewer-poster.jpg")
        self.assertEqual(response.data["poster_accent_color"], accent)
        self.item.refresh_from_db()
        self.assertEqual(self.item.image, PROVIDER_POSTER)
        self.assertEqual(self.item.poster_accent_color, "#aa0000")
        preference = CustomPosterPreference.objects.get(user=self.viewer, item=self.item)
        self.assertEqual(preference.accent_color, accent)

    @patch("api.services.media.get_poster_accent_from_url", return_value="#445566")
    def test_curator_poster_pick_updates_the_shared_cover(self, _accent_mock):
        self.client.force_authenticate(self.curator)

        response = self.client.put(
            "/api/v1/media/tmdb/movie/550/poster/",
            {"poster_url": CURATED_POSTER},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.item.refresh_from_db()
        self.assertEqual(self.item.image, CURATED_POSTER)
        self.assertEqual(self.item.poster_accent_color, build_accent_palette("#445566")["accent"])

    @patch("app.providers.igdb.get_game_backdrops", return_value=[])
    @patch("app.providers.steamgriddb.get_game_backdrops", return_value=[{"url": CURATED_BACKDROP}])
    @patch("app.providers.igdb.get_game_covers", return_value=[])
    @patch("app.providers.steamgriddb.get_game_posters", return_value=[{"url": CURATED_POSTER}])
    @patch("app.providers.steamgriddb.get_game_logos")
    @patch("app.providers.steamgriddb.get_game_logo")
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_pickers_select_curator_pick_for_viewers_without_one(
        self,
        metadata_mock,
        logo_mock,
        logos_mock,
        _posters_mock,
        _covers_mock,
        _grid_backdrops_mock,
        _igdb_backdrops_mock,
    ):
        game = Item.objects.create(
            source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value,
            media_id="1020",
            title="Space Game",
            image=PROVIDER_POSTER,
        )
        metadata_mock.return_value = {
            "media_id": "1020",
            "media_type": "game",
            "source": "igdb",
            "title": "Space Game",
            "image": PROVIDER_POSTER,
            "backdrop": "https://example.com/provider-backdrop.jpg",
        }
        logo_mock.return_value = {"url": "https://example.com/automatic-logo.png"}
        logos_mock.return_value = [
            {"url": "https://example.com/automatic-logo.png"},
            {"url": "https://example.com/curated-logo.png"},
        ]
        self._curate(CustomPosterPreference, CURATED_POSTER, item=game)
        self._curate(CustomBackdropPreference, CURATED_BACKDROP, item=game)
        self._curate(CustomLogoPreference, "https://example.com/curated-logo.png", item=game)
        self.client.force_authenticate(self.viewer)

        posters = self.client.get("/api/v1/media/igdb/game/1020/posters/").data["posters"]
        backdrops = self.client.get("/api/v1/media/igdb/game/1020/backdrops/").data["backdrops"]
        logos = self.client.get("/api/v1/media/igdb/game/1020/logos/").data["logos"]

        self.assertEqual([poster["url"] for poster in posters if poster["is_selected"]], [CURATED_POSTER])
        self.assertEqual(
            [backdrop["url"] for backdrop in backdrops if backdrop["is_selected"]],
            [CURATED_BACKDROP],
        )
        self.assertEqual(
            [logo["url"] for logo in logos if logo["is_selected"]],
            ["https://example.com/curated-logo.png"],
        )

    @patch("app.providers.tmdb.get_title_logo", return_value=None)
    @patch("app.providers.tmdb.get_backdrop_images", return_value=[])
    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_tv_season_row_shows_viewer_pick_then_curator_pick(self, metadata_mock, *_mocks):
        metadata_mock.return_value = {
            "media_id": "1399",
            "media_type": "tv",
            "source": "tmdb",
            "title": "Game of Thrones",
            "image": PROVIDER_POSTER,
            "related": {
                "seasons": [
                    {"season_number": number, "season_title": f"Season {number}", "image": PROVIDER_POSTER}
                    for number in (1, 2, 3)
                ],
            },
        }
        seasons = {
            number: Item.objects.create(
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                media_id="1399",
                title="Game of Thrones",
                image=PROVIDER_POSTER,
                season_number=number,
            )
            for number in (1, 2)
        }
        self._curate(CustomPosterPreference, CURATED_POSTER, item=seasons[1])
        self._curate(CustomPosterPreference, CURATED_POSTER, item=seasons[2])
        CustomPosterPreference.objects.create(
            user=self.viewer,
            item=seasons[2],
            custom_image_url="https://example.com/viewer-season.jpg",
        )
        self.client.force_authenticate(self.viewer)

        response = self.client.get("/api/v1/media/tmdb/tv/1399/")

        self.assertEqual(
            [season["image_url"] for season in response.data["seasons"]],
            [CURATED_POSTER, "https://example.com/viewer-season.jpg", PROVIDER_POSTER],
        )

    @patch("app.providers.tmdb.process_episodes")
    @patch("app.providers.tmdb.get_title_logo", return_value=None)
    @patch("app.providers.tmdb.get_season_backdrop_images", return_value=[])
    @patch("app.providers.mdblist.get_media_ratings", return_value={})
    @patch("api.services.media.provider_services.get_media_metadata")
    def test_season_episode_rows_show_curator_backdrop(self, metadata_mock, *mocks):
        process_episodes_mock = mocks[-1]
        process_episodes_mock.return_value = [
            {"episode_number": 1, "title": "One", "image": "https://example.com/still-1.jpg"},
            {"episode_number": 2, "title": "Two", "image": "https://example.com/still-2.jpg"},
        ]
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
        episode = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            media_id="1399",
            title="Game of Thrones",
            image="https://example.com/still-2.jpg",
            season_number=1,
            episode_number=2,
        )
        self._curate(CustomBackdropPreference, CURATED_BACKDROP, item=episode)

        response = self.client.get("/api/v1/media/tmdb/tv/1399/seasons/1/")

        self.assertEqual(
            [episode["image_url"] for episode in response.data["episodes"]],
            ["https://example.com/still-1.jpg", CURATED_BACKDROP],
        )


class CuratedArtworkWebTests(TestCase):
    """The server-rendered pages follow the same curator rule."""

    def setUp(self):
        users = get_user_model().objects
        self.curator = users.create_user(username="curator", password="strong-password-123", is_staff=True)
        self.viewer = users.create_user(username="viewer", password="strong-password-123")
        self.item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image=PROVIDER_POSTER,
            poster_accent_color="#aa0000",
        )

    def test_poster_tag_falls_back_to_curator_pick(self):
        CustomPosterPreference.objects.create(user=self.curator, item=self.item, custom_image_url=CURATED_POSTER)

        self.assertEqual(get_user_poster_image(self.item, self.viewer), CURATED_POSTER)

    @patch("app.views.get_poster_accent_from_url", return_value="#112233")
    def test_non_curator_web_poster_pick_stays_private(self, _accent_mock):
        self.client.force_login(self.viewer)

        response = self.client.post(
            reverse("save_poster_preference"),
            {
                "media_type": MediaTypes.MOVIE.value,
                "media_id": "550",
                "source": Sources.TMDB.value,
                "poster_url": "https://example.com/viewer-poster.jpg",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.image, PROVIDER_POSTER)
        self.assertEqual(self.item.poster_accent_color, "#aa0000")
        self.assertEqual(
            CustomPosterPreference.objects.get(user=self.viewer, item=self.item).custom_image_url,
            "https://example.com/viewer-poster.jpg",
        )
