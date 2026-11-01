"""A log's favorite: a song on an album log, an episode on a show or season log."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from api.tests.test_tv_tracking_api import TVAPITestCase
from app.models import DiaryEntry

SONG = {"kind": "song", "id": "rec-3", "title": "Track Three", "number": "3"}


class AlbumFavoriteSongTests(TestCase):
    """POST and PATCH diary/ with a favorite song."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="fav", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        patcher = patch(
            "api.services.diary.provider_services.get_media_metadata",
            return_value={"title": "Album", "image": "https://example.com/cover.jpg"},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def log(self, media_type="music", **body):
        ref = {"source": "musicbrainz" if media_type == "music" else "tmdb", "media_type": media_type, "media_id": "a1"}
        return self.client.post("/api/v1/diary/", {"ref": ref, "consumed_at": "2026-10-30", **body}, format="json")

    def test_album_log_saves_reads_changes_and_clears_its_favorite_song(self):
        logged = self.log(favorite=SONG)
        self.assertEqual(logged.status_code, status.HTTP_201_CREATED, logged.data)
        self.assertEqual(logged.data["favorite"], SONG)
        path = f"/api/v1/diary/{logged.data['id']}/"
        self.assertEqual(self.client.get(path).data["favorite"], SONG)

        # Editing other fields leaves it alone.
        edited = self.client.patch(path, {"review": "Great."}, format="json")
        self.assertEqual(edited.data["favorite"], SONG)

        other = {"kind": "song", "id": "rec-7", "title": "Track Seven"}
        changed = self.client.patch(path, {"favorite": other}, format="json")
        self.assertEqual(changed.status_code, status.HTTP_200_OK, changed.data)
        self.assertEqual(changed.data["favorite"], {**other, "number": ""})

        cleared = self.client.patch(path, {"favorite": None}, format="json")
        self.assertIsNone(cleared.data["favorite"])
        self.assertIsNone(DiaryEntry.objects.get(pk=logged.data["id"]).favorite)

    def test_an_incomplete_favorite_on_an_edit_is_a_400_not_a_crash(self):
        path = f"/api/v1/diary/{self.log(favorite=SONG).data['id']}/"
        for favorite in (
            {},
            {"title": "No kind"},
            {"kind": "song", "id": "rec-1"},
            {"kind": "song", "title": "No ID"},
            {"kind": "episode", "title": "No numbers"},
        ):
            with self.subTest(favorite=favorite):
                response = self.client.patch(path, {"favorite": favorite}, format="json")
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.client.get(path).data["favorite"], SONG)

    def test_a_very_long_song_title_still_saves(self):
        favorite = {**SONG, "title": "Symphony No. 9 " * 40}
        logged = self.log(favorite=favorite)
        self.assertEqual(logged.status_code, status.HTTP_201_CREATED, logged.data)
        self.assertEqual(logged.data["favorite"]["title"], favorite["title"].strip())

    def test_log_without_a_favorite_reads_null(self):
        logged = self.log()
        self.assertEqual(logged.status_code, status.HTTP_201_CREATED, logged.data)
        self.assertIsNone(logged.data["favorite"])

    def test_favorites_that_dont_fit_the_log_are_rejected_before_saving(self):
        episode = {"kind": "episode", "season_number": 1, "episode_number": 2, "title": "Pilot"}
        for media_type, favorite in (
            ("movie", SONG),
            ("music", episode),
            ("music", {"kind": "song", "title": "No ID"}),
        ):
            with self.subTest(media_type=media_type, favorite=favorite):
                response = self.log(media_type, favorite=favorite)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(DiaryEntry.objects.exists())


class TVFavoriteEpisodeTests(TVAPITestCase):
    """A show log takes any episode; a season log only its own season's."""

    def episode(self, season, number):
        return {"kind": "episode", "season_number": season, "episode_number": number, "title": f"S{season}E{number}"}

    def test_season_log_takes_an_episode_of_its_season_only(self):
        show = self.make_show()

        wrong = self.log(show, 1, favorite=self.episode(2, 1))
        self.assertEqual(wrong.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(DiaryEntry.objects.exists())

        logged = self.log(show, 1, favorite=self.episode(1, 4))
        self.assertEqual(logged.status_code, status.HTTP_201_CREATED, logged.data)
        self.assertEqual(logged.data["favorite"], self.episode(1, 4))

        path = f"/api/v1/diary/{logged.data['id']}/"
        self.assertEqual(
            self.client.patch(path, {"favorite": self.episode(2, 1)}, format="json").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(self.client.get(path).data["favorite"], self.episode(1, 4))

    def test_show_log_takes_any_episode_and_rejects_a_song(self):
        show = self.make_show()

        logged = self.log(show, favorite=self.episode(2, 8))
        self.assertEqual(logged.status_code, status.HTTP_201_CREATED, logged.data)
        self.assertEqual(logged.data["favorite"], self.episode(2, 8))

        path = f"/api/v1/diary/{logged.data['id']}/"
        self.assertEqual(
            self.client.patch(path, {"favorite": SONG}, format="json").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        changed = self.client.patch(path, {"favorite": self.episode(1, 1), "rating": "4.0"}, format="json")
        self.assertEqual(changed.status_code, status.HTTP_200_OK, changed.data)
        self.assertEqual(changed.data["favorite"], self.episode(1, 1))
