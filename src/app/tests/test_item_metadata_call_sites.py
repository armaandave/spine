"""Web and API paths that create an Item from a full provider record store it (plan 1.3).

Every provider entry point is patched.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from api.serializers.common import get_or_create_item_from_metadata
from api.services import media as media_service
from api.services.item_metadata import CURRENT_METADATA_VERSION
from app import views
from app.models import Item, ItemFilterFacet, MediaTypes, Sources

MOVIE = {
    "title": "Heat",
    "image": "https://example.com/heat.jpg",
    "details": {"release_date": "1995-12-15", "runtime": "2h 50m"},
    "genres": [{"name": "Crime"}],
}
SHOW = {
    "title": "Breaking Bad",
    "image": "https://example.com/bb.jpg",
    "details": {"first_air_date": "2008-01-20"},
    "genres": [{"name": "Drama"}],
}


class CallSiteTestCase(TestCase):
    def assert_stored(self, item, year):
        item.refresh_from_db()
        self.assertEqual(item.release_year, year)
        self.assertTrue(ItemFilterFacet.objects.filter(item=item).exists())
        self.assertEqual(item.metadata_version, CURRENT_METADATA_VERSION[item.media_type])


class ItemMetadataCallSiteTests(CallSiteTestCase):
    def test_the_api_track_path_stores_and_stamps(self):
        ref = {
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "media_id": "949",
            "season_number": None,
            "episode_number": None,
        }

        self.assert_stored(get_or_create_item_from_metadata(ref, MOVIE), 1995)

    def test_the_web_tv_helper_stores_the_show_record(self):
        with mock.patch("app.views.services.get_media_metadata", return_value=SHOW):
            show = views._tv_show_item(Sources.TMDB.value, "1396")

        self.assert_stored(show, 2008)

    def test_fetch_releases_stores_the_show_it_creates(self):
        season = Item(
            media_id="1396",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Breaking Bad",
            image="",
        )
        with (
            mock.patch("app.models.providers.services.get_media_metadata", return_value=SHOW),
            mock.patch("events.tasks.reload_calendar") as reload_calendar,
        ):
            season.fetch_releases(delay=False)

        show = Item.objects.get(media_type=MediaTypes.TV.value, media_id="1396")
        self.assert_stored(show, 2008)
        reload_calendar.assert_called_once_with(items_to_process=[show])


class ListsModalMetadataTests(CallSiteTestCase):
    def test_the_lists_modal_stores_the_record_of_the_item_it_creates(self):
        user = get_user_model().objects.create_user(username="lists", password="12345")
        self.client.force_login(user)

        with mock.patch("lists.views.services.get_media_metadata", return_value=MOVIE):
            response = self.client.get(
                reverse("lists_modal", args=[Sources.TMDB.value, MediaTypes.MOVIE.value, "949"]),
            )

        self.assertEqual(response.status_code, 200)
        self.assert_stored(Item.objects.get(media_id="949"), 1995)


class DetailPageMetadataTests(CallSiteTestCase):
    def test_the_detail_page_stores_and_stamps_an_existing_item(self):
        item = Item.objects.create(
            media_id="949",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Heat",
            image="",
        )
        ref = {
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "media_id": "949",
            "season_number": None,
            "episode_number": None,
        }

        self.assertEqual(media_service._external_rating_item(ref, MOVIE), item)
        self.assert_stored(item, 1995)

    def test_the_detail_page_leaves_a_new_items_series_to_the_view(self):
        ref = {
            "source": Sources.TMDB.value,
            "media_type": MediaTypes.MOVIE.value,
            "media_id": "949",
            "season_number": None,
            "episode_number": None,
        }
        movie = {**MOVIE, "details": {**MOVIE["details"], "series_id": "10"}}

        with (
            mock.patch.object(media_service, "eligible_rating_sources", return_value=["imdb"]),
            mock.patch("api.services.item_metadata._persist_series") as persist_series,
        ):
            item = media_service._external_rating_item(ref, movie)

        # The detail view saves the series once, after the rating payload.
        persist_series.assert_not_called()
        self.assert_stored(item, 1995)
