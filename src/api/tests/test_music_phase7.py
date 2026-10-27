from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from app.models import DiaryEntry, Item, MediaLike, MediaTypes, Music, Sources, Status
from lists.models import CustomList, CustomListItem
from social.models import Activity, ContentLike, Follow, FollowStatus


@override_settings(MUSIC_CATALOG_SOURCE="musicbrainz")
class MusicListsSocialTests(TestCase):
    """Music uses the existing Item-based list and social contracts."""

    def setUp(self):
        self.client = APIClient()
        self.owner = get_user_model().objects.create_user(username="music-owner")
        self.follower = get_user_model().objects.create_user(username="music-follower")
        self.outsider = get_user_model().objects.create_user(username="music-outsider")
        Follow.objects.create(
            from_user=self.follower,
            to_user=self.owner,
            status=FollowStatus.ACCEPTED,
        )
        self.music_item = Item.objects.create(
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id="3bd76d40-7f0e-36b7-9348-91a33afee20e",
            title="Year Zero",
            image="https://example.com/year-zero.jpg",
        )
        self.movie_item = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="550",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        Music.objects.bulk_create([
            Music(
                user=self.owner,
                item=self.music_item,
                status=Status.COMPLETED.value,
                score=9,
            ),
        ])

    def ref(self, item):
        return {
            "source": item.source,
            "media_type": item.media_type,
            "media_id": item.media_id,
        }

    @patch("api.serializers.common.resolved_item_backdrop_urls", return_value=(None, None))
    def test_ranked_lists_filters_likes_and_feed_serialize_music(self, _backdrop_mock):
        self.client.force_authenticate(self.owner)
        created = self.client.post(
            "/api/v1/lists/",
            {"name": "Albums", "visibility": "public", "is_ranked": True},
            format="json",
        )
        list_id = created.data["id"]

        for item in [self.music_item, self.movie_item]:
            response = self.client.post(
                f"/api/v1/lists/{list_id}/items/",
                {"ref": self.ref(item)},
                format="json",
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        preview = self.client.get("/api/v1/lists/").data["results"][0]
        filtered = self.client.get(
            f"/api/v1/lists/{list_id}/items/",
            {"status": Status.COMPLETED.value, "rating_min": "8"},
        )
        reordered = self.client.patch(
            f"/api/v1/lists/{list_id}/items/reorder/",
            {"item_ids": [self.movie_item.id, self.music_item.id]},
            format="json",
        )
        liked_media = self.client.post(
            "/api/v1/me/liked-media/",
            {"ref": self.ref(self.music_item)},
            format="json",
        )

        entry = DiaryEntry.objects.create(
            user=self.owner,
            item=self.music_item,
            consumed_at=timezone.now(),
            rating=9,
            visibility="public",
        )
        Activity.objects.create(
            actor=self.owner,
            verb="diary_created",
            target_type=ContentLike.DIARY_ENTRY,
            target_id=entry.id,
            item=self.music_item,
            visibility="public",
            snapshot={"rating": "9.0", "liked": False},
        )

        self.client.force_authenticate(self.follower)
        diary_like = self.client.post(
            "/api/v1/social/likes/",
            {"target_type": "diary", "target_id": entry.id},
            format="json",
        )
        list_like = self.client.post(
            "/api/v1/social/likes/",
            {"target_type": "list", "target_id": list_id},
            format="json",
        )
        feed = self.client.get("/api/v1/feed/", {"media_type": "music"})

        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(preview["items_count"], 2)
        self.assertEqual(preview["preview_items"][0]["title"], "Year Zero")
        self.assertTrue(preview["image_url"].endswith("year-zero.jpg"))
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual([item["title"] for item in filtered.data["results"]], ["Year Zero"])
        self.assertEqual(reordered.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [item["position"] for item in reordered.data["items"]],
            [1, 2],
        )
        self.assertTrue(liked_media.data["liked"])
        self.assertTrue(MediaLike.objects.filter(user=self.owner, item=self.music_item).exists())
        self.assertEqual(diary_like.status_code, status.HTTP_200_OK)
        self.assertEqual(list_like.status_code, status.HTTP_200_OK)
        # Activity is logs only: adding the album to a list isn't in the feed.
        self.assertEqual(
            [activity["type"] for activity in feed.data["results"]],
            ["diary_created"],
        )
        self.assertTrue(all(activity["media"]["title"] == "Year Zero" for activity in feed.data["results"]))
        self.assertTrue(all(activity["viewer"]["has_liked"] for activity in feed.data["results"]))

        self.client.force_authenticate(self.owner)
        removed = self.client.delete(
            f"/api/v1/lists/{list_id}/items/{self.music_item.id}/",
        )
        self.assertEqual(removed.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            CustomListItem.objects.filter(
                custom_list_id=list_id,
                item=self.music_item,
            ).exists(),
        )
        self.assertEqual(
            CustomListItem.objects.get(
                custom_list_id=list_id,
                item=self.movie_item,
            ).position,
            1,
        )

    def test_activity_visibility_sync_blocking_and_like_authorization(self):
        custom_list = CustomList.objects.create(
            owner=self.owner,
            name="Private albums",
            visibility="public",
        )
        CustomListItem.objects.create(custom_list=custom_list, item=self.music_item)
        list_activity = Activity.objects.create(
            actor=self.owner,
            verb="list_item_added",
            target_type=ContentLike.CUSTOM_LIST,
            target_id=custom_list.id,
            item=self.music_item,
            visibility="public",
            snapshot={"list_name": custom_list.name},
        )
        entry = DiaryEntry.objects.create(
            user=self.owner,
            item=self.music_item,
            consumed_at=timezone.now(),
            rating=8.5,
            visibility="public",
        )
        diary_activity = Activity.objects.create(
            actor=self.owner,
            verb="diary_created",
            target_type=ContentLike.DIARY_ENTRY,
            target_id=entry.id,
            item=self.music_item,
            visibility="public",
            snapshot={"rating": "8.5", "liked": False},
        )

        self.client.force_authenticate(self.owner)
        self.client.patch(
            f"/api/v1/lists/{custom_list.id}/",
            {"visibility": "private"},
            format="json",
        )
        self.client.patch(
            f"/api/v1/diary/{entry.id}/",
            {"visibility": "followers"},
            format="json",
        )
        list_activity.refresh_from_db()
        diary_activity.refresh_from_db()
        self.assertEqual(list_activity.visibility, "private")
        self.assertEqual(diary_activity.visibility, "public")

        self.client.force_authenticate(self.follower)
        follower_activity = self.client.get(
            f"/api/v1/users/{self.owner.username}/activity/",
        )
        private_list_like = self.client.post(
            "/api/v1/social/likes/",
            {"target_type": "list", "target_id": custom_list.id},
            format="json",
        )
        follower_diary_like = self.client.post(
            "/api/v1/social/likes/",
            {"target_type": "diary", "target_id": entry.id},
            format="json",
        )

        self.assertEqual(
            [activity["type"] for activity in follower_activity.data["results"]],
            ["diary_created"],
        )
        self.assertEqual(private_list_like.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(follower_diary_like.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.outsider)
        outsider_activity = self.client.get(
            f"/api/v1/users/{self.owner.username}/activity/",
        )
        outsider_diary_like = self.client.post(
            "/api/v1/social/likes/",
            {"target_type": "diary", "target_id": entry.id},
            format="json",
        )
        self.assertEqual(
            [activity["type"] for activity in outsider_activity.data["results"]],
            ["diary_created"],
        )
        self.assertEqual(outsider_diary_like.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.follower)
        blocked = self.client.post(f"/api/v1/users/{self.owner.username}/block/")
        blocked_activity = self.client.get(
            f"/api/v1/users/{self.owner.username}/activity/",
        )
        blocked_like = self.client.post(
            "/api/v1/social/likes/",
            {"target_type": "diary", "target_id": entry.id},
            format="json",
        )
        self.assertEqual(blocked.status_code, status.HTTP_200_OK)
        self.assertEqual(blocked_activity.status_code, status.HTTP_200_OK)
        self.assertEqual(blocked_activity.data["results"], [])
        self.assertEqual(blocked_like.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(MUSIC_CATALOG_SOURCE="musicbrainz")
class MusicManualAndImportBoundaryTests(TestCase):
    """Manual creation stays generic and streaming imports stay unsupported."""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(username="music-boundary")
        self.client.force_authenticate(self.user)

    def test_manual_music_creation_and_unsupported_streaming_imports(self):
        created = self.client.post(
            "/api/v1/media/manual/",
            {
                "media_type": MediaTypes.MUSIC.value,
                "title": "Manual Album",
                "image_url": "https://example.com/manual.jpg",
            },
            format="json",
        )

        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(created.data["ref"]["media_type"], MediaTypes.MUSIC.value)
        self.assertEqual(created.data["ref"]["source"], Sources.MANUAL.value)
        for source in ["apple-music", "spotify", "lastfm"]:
            with self.subTest(source=source):
                response = self.client.post(
                    f"/api/v1/imports/{source}/",
                    {"mode": "new"},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
