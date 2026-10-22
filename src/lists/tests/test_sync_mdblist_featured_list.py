from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from app.models import Item
from lists.models import CustomList


class SyncMDBListFeaturedListTests(TestCase):
    """Manual MDBList featured-list synchronization."""

    def test_sync_creates_and_replaces_native_ranked_list(self):
        owner = get_user_model().objects.create_user(username="Spine")
        metadata = {
            "id": 155837,
            "name": "Letterboxd's Top 500 Films",
            "items": 2,
        }
        items = [
            {
                "id": 2,
                "ids": {"tmdb": 2},
                "rank": 2,
                "title": "Second",
                "poster_path": "/second.jpg",
                "release_year": 2002,
                "spine_media_type": "movie",
            },
            {
                "id": 1,
                "ids": {"tmdb": 1},
                "rank": 1,
                "title": "First",
                "poster_path": "/first.jpg",
                "release_year": 2001,
                "spine_media_type": "movie",
            },
        ]

        with (
            patch("app.providers.mdblist.get_external_list", return_value=metadata),
            patch("app.providers.mdblist.get_external_list_items", return_value=items),
        ):
            call_command(
                "sync_mdblist_featured_list",
                "https://mdblist.com/lists/davearmaan12/external/155837",
                owner="Spine",
                stdout=StringIO(),
            )

        custom_list = CustomList.objects.get(owner=owner, import_source_id="155837")
        self.assertTrue(custom_list.is_featured)
        self.assertTrue(custom_list.is_ranked)
        self.assertEqual(custom_list.visibility, CustomList.Visibility.PUBLIC)
        self.assertEqual(
            list(custom_list.customlistitem_set.values_list("item__media_id", "position")),
            [("1", 1), ("2", 2)],
        )
        self.assertEqual(Item.objects.get(media_id="1").image, "https://image.tmdb.org/t/p/original/first.jpg")

        metadata["items"] = 1
        with (
            patch("app.providers.mdblist.get_external_list", return_value=metadata),
            patch("app.providers.mdblist.get_external_list_items", return_value=items[:1]),
        ):
            call_command(
                "sync_mdblist_featured_list",
                "155837",
                owner="Spine",
                stdout=StringIO(),
            )

        self.assertEqual(CustomList.objects.filter(owner=owner).count(), 1)
        self.assertEqual(
            list(custom_list.customlistitem_set.values_list("item__media_id", flat=True)),
            ["2"],
        )
