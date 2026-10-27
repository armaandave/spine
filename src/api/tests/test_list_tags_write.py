"""List tags write rules (STAT-402) and list edits touching updated_at (STAT-403)."""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from app.models import Item, MediaTypes, Sources
from lists.models import CustomList, CustomListItem

OLD = datetime(2020, 1, 1, tzinfo=UTC)


class ListTagsBase(TestCase):
    """An owner, a collaborator and an API client signed in as the owner."""

    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(username="owner", password="strong-password-123")
        self.collaborator = users.create_user(username="helper", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def item(self, media_id):
        return Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title=f"Movie {media_id}",
            image="https://example.com/poster.jpg",
        )

    def make_list(self, **fields):
        custom_list = CustomList.objects.create(owner=self.owner, name="List", **fields)
        custom_list.collaborators.add(self.collaborator)
        return custom_list

    def as_collaborator(self):
        client = APIClient()
        client.force_authenticate(self.collaborator)
        return client


class ListTagsWriteTests(ListTagsBase):
    """STAT-402: shape, normalizing, POST, PATCH and owner only."""

    def test_post_normalizes_tags(self):
        response = self.client.post(
            "/api/v1/lists/",
            {"name": "My 2025", "is_ranked": True,
             "tags": ["top2025", "  Favorites ", "favorites", "Top \t  2025", "   ", "TOP2025"]},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["tags"], ["top2025", "Favorites", "Top 2025"])
        self.assertEqual(CustomList.objects.get(id=response.data["id"]).tags, ["top2025", "Favorites", "Top 2025"])

    def test_post_without_tags_has_none(self):
        response = self.client.post("/api/v1/lists/", {"name": "Plain"}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(CustomList.objects.get(id=response.data["id"]).tags, [])

    def test_patch_replaces_clears_or_keeps(self):
        custom_list = self.make_list(tags=["old"])
        path = f"/api/v1/lists/{custom_list.id}/"
        self.assertEqual(self.client.patch(path, {"name": "Renamed"}, format="json").status_code, 200)
        custom_list.refresh_from_db()
        self.assertEqual(custom_list.tags, ["old"])
        response = self.client.patch(path, {"tags": ["topalltime", " Best "]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        custom_list.refresh_from_db()
        self.assertEqual(custom_list.tags, ["topalltime", "Best"])
        self.assertEqual(self.client.patch(path, {"tags": []}, format="json").status_code, 200)
        custom_list.refresh_from_db()
        self.assertEqual(custom_list.tags, [])

    def test_invalid_tags_are_400_under_tags(self):
        custom_list = self.make_list(tags=["keep"])
        path = f"/api/v1/lists/{custom_list.id}/"
        too_many = [f"tag{index}" for index in range(21)]
        # The limits apply as sent: 21 tags that normalize to fewer still fail.
        repeats = ["same"] * 21
        cases = {
            "too many": too_many,
            "too many before normalizing": repeats,
            "too long": ["x" * 101],
            "null": None,
            "not a list": "top2025",
            "not strings": ["ok", 2025],
            "null inside": [None],
            "object": {"tag": "x"},
        }
        for label, tags in cases.items():
            with self.subTest(label):
                response = self.client.patch(path, {"tags": tags}, format="json")
                self.assertEqual(response.status_code, 400, response.data)
                fields = response.data["error"]["fields"]
                self.assertEqual(set(fields), {"tags"})
                self.assertIsInstance(fields["tags"], list)
        custom_list.refresh_from_db()
        self.assertEqual(custom_list.tags, ["keep"])

    def test_limits_count_trimmed_length_and_allow_exactly_twenty(self):
        custom_list = self.make_list()
        path = f"/api/v1/lists/{custom_list.id}/"
        tags = [f"  {'y' * 98}{index:02d}  " for index in range(20)]
        response = self.client.patch(path, {"tags": tags}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        custom_list.refresh_from_db()
        self.assertEqual(len(custom_list.tags), 20)
        self.assertEqual(len(custom_list.tags[0]), 100)

    def test_a_collaborator_writing_tags_gets_403_and_nothing_is_saved(self):
        # S-17.
        custom_list = self.make_list(tags=["keep"])
        path = f"/api/v1/lists/{custom_list.id}/"
        response = self.as_collaborator().patch(path, {"name": "Changed", "tags": ["top2025"]}, format="json")
        self.assertEqual(response.status_code, 403)
        custom_list.refresh_from_db()
        self.assertEqual((custom_list.name, custom_list.tags), ("List", ["keep"]))
        # Without tags the collaborator still edits the rest.
        response = self.as_collaborator().patch(path, {"name": "Changed"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        custom_list.refresh_from_db()
        self.assertEqual((custom_list.name, custom_list.tags), ("Changed", ["keep"]))


class ListTouchTests(ListTagsBase):
    """STAT-403: adding, removing and reordering items moves updated_at."""

    def age(self, custom_list):
        CustomList.objects.filter(id=custom_list.id).update(updated_at=OLD)

    def assert_touched(self, custom_list):
        custom_list.refresh_from_db()
        self.assertGreater(custom_list.updated_at, OLD)

    def test_api_add_remove_and_reorder_touch_the_list(self):
        first, second = self.item("1"), self.item("2")
        custom_list = self.make_list(is_ranked=True)
        CustomListItem.objects.create(custom_list=custom_list, item=first, position=1)
        for label, request in (
            ("add", lambda: self.as_collaborator().post(
                f"/api/v1/lists/{custom_list.id}/items/",
                {"ref": {"source": "tmdb", "media_type": "movie", "media_id": "2"}},
                format="json",
            )),
            ("reorder", lambda: self.client.patch(
                f"/api/v1/lists/{custom_list.id}/items/reorder/",
                {"item_ids": [second.id, first.id]},
                format="json",
            )),
            ("remove", lambda: self.client.delete(f"/api/v1/lists/{custom_list.id}/items/{first.id}/")),
        ):
            with self.subTest(label):
                self.age(custom_list)
                response = request()
                self.assertLess(response.status_code, 300, getattr(response, "data", None))
                self.assert_touched(custom_list)

    def test_web_add_and_remove_touch_the_list(self):
        item = self.item("1")
        custom_list = self.make_list()
        self.client.force_login(self.owner)
        for label in ("add", "remove"):
            with self.subTest(label):
                self.age(custom_list)
                response = self.client.post(
                    reverse("list_item_toggle"),
                    {"item_id": item.id, "custom_list_id": custom_list.id},
                )
                self.assertEqual(response.status_code, 200)
                self.assert_touched(custom_list)
                self.assertEqual(custom_list.items.filter(id=item.id).exists(), label == "add")
