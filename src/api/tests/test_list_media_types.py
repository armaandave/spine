from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from app.models import Item, MediaTypes, Sources
from lists.models import CustomList, CustomListItem


class ListMediaTypesAPITests(TestCase):
    """Lists report the media types they hold, for their icons."""

    def setUp(self):
        self.client = APIClient()
        self.owner = get_user_model().objects.create_user(username="owner")
        self.client.force_authenticate(self.owner)
        self.media_list = CustomList.objects.create(owner=self.owner, name="Mixed")

    def add(self, media_type, media_id, **extra):
        item = Item.objects.create(
            source=Sources.MAL.value
            if media_type == MediaTypes.ANIME.value
            else Sources.TMDB.value,
            media_type=media_type,
            media_id=media_id,
            title=f"{media_type} {media_id}",
            **extra,
        )
        CustomListItem.objects.create(custom_list=self.media_list, item=item)

    def test_seasons_and_episodes_count_as_tv(self):
        self.add(MediaTypes.SEASON.value, "1399", season_number=1)
        self.add(
            MediaTypes.EPISODE.value,
            "1399",
            season_number=1,
            episode_number=2,
        )
        self.add(MediaTypes.MOVIE.value, "550")

        response = self.client.get(f"/api/v1/lists/{self.media_list.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["media_types"], ["movie", "tv"])

    def test_every_list_payload_reports_media_types(self):
        self.add(MediaTypes.ANIME.value, "1")
        self.add(MediaTypes.TV.value, "1399")
        self.add(MediaTypes.MOVIE.value, "550")
        empty = CustomList.objects.create(owner=self.owner, name="Empty")
        people = CustomList.objects.create(
            owner=self.owner,
            name="People",
            list_type=CustomList.ListType.PEOPLE,
        )

        response = self.client.get("/api/v1/lists/", {"list_type": "all"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {result["id"]: result["media_types"] for result in response.data["results"]},
            {
                self.media_list.id: ["movie", "tv", "anime"],
                empty.id: [],
                people.id: [],
            },
        )

    @override_settings(MUSIC_ENABLED=False)
    def test_hidden_media_types_are_not_shown(self):
        self.add(MediaTypes.MOVIE.value, "550")
        CustomListItem.objects.create(
            custom_list=self.media_list,
            item=Item.objects.create(
                source=Sources.MUSICBRAINZ.value,
                media_type=MediaTypes.MUSIC.value,
                media_id="release-1",
                title="Album",
            ),
        )

        response = self.client.get(f"/api/v1/lists/{self.media_list.id}/")

        self.assertEqual(response.data["media_types"], ["movie"])
