"""Media pages send the shown poster's palette, counted in the background at first."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from app.models import CustomPosterPreference, Item, MediaTypes, PosterPalette, Sources

PROVIDER_POSTER = "https://example.com/provider-poster.jpg"
VIEWER_POSTER = "https://example.com/viewer-poster.jpg"
PALETTE = [{"hex": "#0A0B0B", "share": 0.957}, {"hex": "#525152", "share": 0.03}]
METADATA = {
    "media_id": "550",
    "media_type": "movie",
    "source": "tmdb",
    "title": "Fight Club",
    "image": PROVIDER_POSTER,
}


@patch("app.providers.tmdb.get_title_logos", return_value=[])
@patch("app.providers.tmdb.get_title_logo", return_value=None)
@patch("app.providers.tmdb.get_backdrop_images", return_value=[])
@patch("app.providers.mdblist.get_media_ratings", return_value={})
@patch("api.services.media.provider_services.get_media_metadata", return_value=METADATA)
class PosterPaletteDetailTests(TestCase):
    """The palette belongs to the picture the viewer sees."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.viewer = get_user_model().objects.create_user(username="viewer", password="strong-password-123")
        self.client.force_authenticate(self.viewer)

    @override_settings(POSTER_PALETTE_COUNTING=True)
    @patch("app.utils.color.poster_palette_from_url", return_value=PALETTE)
    def test_first_open_counts_in_the_background_and_later_opens_send_it(self, count, *_mocks):
        first = self.client.get("/api/v1/media/tmdb/movie/550/")
        second = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertIsNone(first.data["poster_palette"])
        self.assertEqual(second.data["poster_palette"], PALETTE)
        count.assert_called_once_with(PROVIDER_POSTER)

    def test_viewer_pick_sends_that_poster_palette(self, *_mocks):
        item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image=PROVIDER_POSTER,
        )
        CustomPosterPreference.objects.create(user=self.viewer, item=item, custom_image_url=VIEWER_POSTER)
        PosterPalette.objects.create(image_url=PROVIDER_POSTER, colors=[{"hex": "#FFFFFF", "share": 1.0}])
        PosterPalette.objects.create(image_url=VIEWER_POSTER, colors=PALETTE)

        response = self.client.get("/api/v1/media/tmdb/movie/550/")

        self.assertEqual(response.data["poster_palette"], PALETTE)
