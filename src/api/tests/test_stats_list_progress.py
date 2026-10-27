"""Legacy stats keep ``list_progress`` and ``series_progress``, always empty (STAT-900).

Installed builds decode both keys but no build shows them, so the server no longer
reads lists or series for them.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from app.models import (
    Item,
    MediaSeries,
    MediaSeriesItem,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from lists.models import CustomList, CustomListItem


@override_settings(STATS_CACHE_ENABLED=False)
class LegacyProgressTests(TestCase):
    """The legacy summary sends both progress keys empty and never builds them."""

    def setUp(self):
        users = get_user_model().objects
        self.curator = users.create_user(username="curator")
        self.owner = users.create_user(username="owner")
        self.viewer = users.create_user(username="viewer")
        self.counter = 0

    def item(self):
        self.counter += 1
        return Item.objects.create(
            media_id=str(self.counter),
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            title=f"Film {self.counter}",
            image=f"https://example.com/{self.counter}.jpg",
        )

    def add_lists_and_a_series(self):
        """Add lists and a series the old builders showed, each 1 of 2 done."""
        done, other = self.item(), self.item()
        Movie.objects.create(user=self.owner, item=done, status=Status.COMPLETED.value)
        for owner, featured in ((self.owner, False), (self.curator, True)):
            custom_list = CustomList.objects.create(
                owner=owner,
                name=f"List {CustomList.objects.count()}",
                is_featured=featured,
                visibility=CustomList.Visibility.PUBLIC,
            )
            CustomListItem.objects.bulk_create([
                CustomListItem(custom_list=custom_list, item=member, position=index)
                for index, member in enumerate((done, other), start=1)
            ])
        series = MediaSeries.objects.create(
            source=Sources.TMDB.value,
            series_id=f"series-{self.counter}",
            media_type=MediaTypes.MOVIE.value,
            name="Series",
            item_count=2,
        )
        MediaSeriesItem.objects.bulk_create([
            MediaSeriesItem(series=series, item=done, position=1),
            MediaSeriesItem(series=series, item=other, position=2),
        ])

    def summary(self, viewer, url):
        client = APIClient()
        client.force_authenticate(viewer)
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_both_keys_are_empty_for_the_owner_and_other_viewers(self):
        self.add_lists_and_a_series()
        own = reverse("api-my-stats")
        theirs = reverse("api-user-stats", kwargs={"username": self.owner.username})
        for viewer, url in ((self.owner, own), (self.viewer, theirs)):
            with self.subTest(viewer=viewer.username):
                data = self.summary(viewer, url)
                self.assertEqual(data["schema_version"], 1)
                self.assertEqual((data["list_progress"], data["series_progress"]), ([], []))

    def test_lists_and_series_are_never_read(self):
        self.add_lists_and_a_series()
        with CaptureQueriesContext(connection) as queries:
            self.summary(self.owner, reverse("api-my-stats"))
        read = [query["sql"] for query in queries.captured_queries]
        self.assertFalse([sql for sql in read if "lists_customlist" in sql or "app_mediaseries" in sql])
