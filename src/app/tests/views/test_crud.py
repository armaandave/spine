import datetime
import unittest
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from app.models import (
    TV,
    Anime,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from app.tests.tv_fixtures import FakeTVProvider


class CreateMedia(TestCase):
    """Test the creation of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.external_credentials = {"username": "test2", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.external_user = get_user_model().objects.create_user(
            **self.external_credentials
        )
        self.client.login(**self.credentials)

    @override_settings(MEDIA_ROOT=("create_media"))
    @patch("app.views.services.get_media_metadata")
    def test_create_anime(self, mock_get_metadata):
        """Test the creation of a TV object."""
        mock_get_metadata.return_value = {
            "title": "Test Anime",
            "image": "http://example.com/image.jpg",
            "max_progress": 26,
        }
        Item.objects.create(
            media_id="1",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title="Test Anime",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "1",
                "source": Sources.MAL.value,
                "media_type": MediaTypes.ANIME.value,
                "status": Status.PLANNING.value,
                "progress": 0,
                "repeats": 0,
            },
        )
        self.assertEqual(
            Anime.objects.filter(item__media_id="1", user=self.user).exists(),
            True,
        )

    @override_settings(MEDIA_ROOT=("create_media"))
    @patch("app.views.services.get_media_metadata")
    def test_create_tv(self, mock_get_metadata):
        """Test the creation of a TV object through views."""
        mock_get_metadata.return_value = {
            "title": "Friends",
            "image": "http://example.com/image.jpg",
            "max_progress": 10,
            "related": {"seasons": []},
        }
        Item.objects.create(
            media_id="5895",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )
        self.client.post(
            reverse("media_save"),
            {
                "media_id": "5895",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.TV.value,
                "status": Status.PLANNING.value,
            },
        )
        self.assertEqual(
            TV.objects.filter(item__media_id="5895", user=self.user).exists(),
            True,
        )

    def test_create_season(self):
        """A season's written status means its action; a Planning one creates nothing.

        Rewritten for TV-207 and TV-205: the old view saved a Planning Season row.
        Seasons have no Planning, Paused or Dropped of their own, so the web ignores it
        and no empty season is created; Completed marks the season's aired episodes.
        """
        with FakeTVProvider() as fake:
            fake.add_show("1668", title="Friends", seasons={1: 3, 2: 3})
            form = {
                "media_id": "1668",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.SEASON.value,
                "season_number": 1,
                "status": Status.PLANNING.value,
            }
            self.client.post(reverse("media_save"), form)
            self.assertFalse(Season.objects.filter(item__media_id="1668", user=self.user).exists())
            self.assertFalse(TV.objects.filter(item__media_id="1668", user=self.user).exists())

            self.client.post(reverse("media_save"), {**form, "status": Status.COMPLETED.value})

        season = Season.objects.get(item__media_id="1668", item__season_number=1, user=self.user)
        self.assertEqual(season.status, Status.COMPLETED.value)
        self.assertEqual(season.episodes.count(), 3)
        self.assertFalse(season.episodes.exclude(kind=Episode.Kind.FILL).exists())
        self.assertEqual(season.related_tv.status, Status.IN_PROGRESS.value)

    def test_create_episodes(self):
        """The episode form ticks the episode on the typed day (TV-100).

        Rewritten for TV-100 and TV-101: the old view created a Season row and added a
        repeat viewing on every save. Now the tick records one viewing dated with the
        typed day, and saving it again records nothing.
        """
        with FakeTVProvider() as fake:
            fake.add_show("1668", title="Friends", seasons={1: 2})
            form = {
                "media_id": "1668",
                "season_number": 1,
                "episode_number": 1,
                "source": Sources.TMDB.value,
                "end_date": "2023-06-01",
            }
            self.client.post(reverse("episode_save"), form)
            self.client.post(reverse("episode_save"), form)
        episodes = Episode.objects.filter(
            item__media_id="1668",
            related_season__user=self.user,
            item__episode_number=1,
        )
        self.assertEqual(episodes.count(), 1)
        self.assertEqual(episodes.get().watched_on, datetime.date(2023, 6, 1))


class EditMedia(TestCase):
    """Test the editing of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.external_credentials = {"username": "test2", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.external_user = get_user_model().objects.create_user(
            **self.external_credentials
        )
        self.client.login(**self.credentials)

    def test_edit_movie_score(self):
        """Test the editing of a movie score."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.user,
            score=9,
            progress=1,
            status=Status.PLANNING.value,
            notes="Nice",
            start_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
            end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
        )

        self.client.post(
            reverse("media_save"),
            {
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 5,
                "progress": 1,
                "status": Status.PLANNING.value,
                "notes": "Nice",
            },
        )
        self.assertEqual(Movie.objects.get(item__media_id="10494").score, 10)
        self.assertTrue(Movie.objects.get(item__media_id="10494").direct_consumption)

    def test_cannot_edit_another_users_media(self):
        """Test users cannot edit another user's media by instance ID."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.external_user,
            score=9,
            progress=0,
            status=Status.PLANNING.value,
            notes="Nice",
        )

        response = self.client.post(
            reverse("media_save"),
            {
                "instance_id": movie.id,
                "media_id": "10494",
                "source": Sources.TMDB.value,
                "media_type": MediaTypes.MOVIE.value,
                "score": 5,
                "progress": 0,
                "status": Status.PLANNING.value,
                "notes": "Changed",
            },
        )

        self.assertEqual(response.status_code, 404)
        movie.refresh_from_db()
        self.assertEqual(movie.score, 9)
        self.assertEqual(movie.notes, "Nice")

    @unittest.skip("update_media_score URL was removed; media_save now handles score updates.")
    def test_cannot_update_another_users_media_score(self):
        """Test users cannot update another user's score by instance ID."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.external_user,
            score=9,
            progress=0,
            status=Status.PLANNING.value,
        )

        response = self.client.post(
            reverse(
                "update_media_score",
                kwargs={
                    "media_type": MediaTypes.MOVIE.value,
                    "instance_id": movie.id,
                },
            ),
            {"score": 10},
        )

        self.assertEqual(response.status_code, 404)
        movie.refresh_from_db()
        self.assertEqual(movie.score, 9)


class DeleteMedia(TestCase):
    """Test the deletion of media objects through views."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.external_credentials = {"username": "test2", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.external_user = get_user_model().objects.create_user(
            **self.external_credentials
        )
        self.client.login(**self.credentials)
        # TV deletes run through app.tv_tracking, which reads the episode lists.
        fake = FakeTVProvider().__enter__()
        self.addCleanup(fake.__exit__, None, None, None)
        fake.add_show("1668", title="Friends", seasons={1: 2})

        self.item_tv = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )
        self.tv = TV.objects.create(
            item=self.item_tv,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.item_season = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=self.item_season,
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )

        self.item_ep = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        self.episode = Episode.objects.bulk_create(
            [
                Episode(
                    item=self.item_ep,
                    related_season=self.season,
                    end_date=datetime.datetime(2023, 6, 1, 0, 0, tzinfo=datetime.UTC),
                ),
            ],
        )[0]

    def test_delete_tv(self):
        """Test the deletion of a tv through views."""
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        tv_obj = TV.objects.get(user=self.user)

        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": tv_obj.id,
                "media_type": MediaTypes.TV.value,
            },
        )

        self.assertEqual(Movie.objects.filter(user=self.user).count(), 0)

    def test_delete_season(self):
        """Test the deletion of a season through views."""
        self.client.post(
            reverse(
                "media_delete",
            ),
            data={"instance_id": self.season.id, "media_type": MediaTypes.SEASON.value},
        )

        self.assertEqual(Season.objects.filter(user=self.user).count(), 0)
        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )

    def test_cannot_delete_another_users_media(self):
        """Test users cannot delete another user's media by instance ID."""
        item = Item.objects.create(
            media_id="10494",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Perfect Blue",
            image="http://example.com/image.jpg",
        )
        movie = Movie.objects.create(
            item=item,
            user=self.external_user,
            progress=0,
            status=Status.PLANNING.value,
        )

        response = self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": movie.id,
                "media_type": MediaTypes.MOVIE.value,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Movie.objects.filter(id=movie.id).exists())

    def test_unwatch_episode(self):
        """Test unwatching of an episode through views."""
        self.client.post(
            reverse("media_delete"),
            data={
                "instance_id": self.episode.id,
                "media_type": MediaTypes.EPISODE.value,
            },
        )

        self.assertEqual(
            Episode.objects.filter(related_season__user=self.user).count(),
            0,
        )
