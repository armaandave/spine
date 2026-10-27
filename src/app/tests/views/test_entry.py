from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)


class CreateEntryViewTests(TestCase):
    """Test the create entry view."""

    def setUp(self):
        """Create a user and log in."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.client.login(**self.credentials)

    def test_create_entry_get(self):
        """Test the GET method of create_entry view."""
        response = self.client.get(reverse("create_entry"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/create_entry.html")
        self.assertIn("media_types", response.context)

        self.assertEqual(response.context["media_types"], MediaTypes.values)

    def test_create_entry_post_movie(self):
        """Test creating a movie entry."""
        form_data = {
            "title": "Test Movie",
            "media_type": MediaTypes.MOVIE.value,
            "status": Status.COMPLETED.value,
            "score": 4,
            "progress": 1,
            "start_date": "2023-01-01T00:00",
            "end_date": "2023-01-02T00:00",
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="Test Movie",
                media_type=MediaTypes.MOVIE.value,
            ).exists(),
        )

        movie = Movie.objects.get(item__title="Test Movie")
        self.assertEqual(movie.status, Status.COMPLETED.value)
        self.assertEqual(movie.score, 8)
        self.assertEqual(movie.progress, 1)
        self.assertTrue(movie.direct_consumption)
        self.assertIsNone(movie.end_date)
        self.assertEqual(movie.user, self.user)

    def test_create_entry_post_tv(self):
        """A manual show with nothing aired can't be Watching or rated yet.

        Rewritten for TV-005, TV-006 and TV-303: the old view saved an In progress TV
        row with stars. Watching and stars need an aired episode, so the show isn't
        added and the user is told; Planning puts it in the library.
        """
        form_data = {
            "title": "Test TV Show",
            "media_type": MediaTypes.TV.value,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))
        self.assertTrue(
            Item.objects.filter(
                title="Test TV Show",
                media_type=MediaTypes.TV.value,
            ).exists(),
        )
        self.assertFalse(TV.objects.filter(item__title="Test TV Show").exists())
        self.assertIn(
            "Nothing has aired yet",
            " ".join(str(message) for message in response.context["messages"]),
        )

        planned = {**form_data, "title": "Planned Show", "status": Status.PLANNING.value}
        planned.pop("score")
        self.client.post(reverse("create_entry"), planned)
        tv = TV.objects.get(item__title="Planned Show")
        self.assertEqual(tv.status, Status.PLANNING.value)
        self.assertEqual(tv.choice, Status.PLANNING.value)
        self.assertEqual(tv.user, self.user)

    def test_create_entry_post_season(self):
        """A manual season's status means its action and creates no empty season.

        Rewritten for TV-205 and TV-207: the old view saved an In progress Season row
        with nothing watched. Now In progress chooses Watching for the show, which
        needs an aired episode, and creates no season tracking; Completed marks the
        season's listed episodes watched (manual episodes count as aired, TV-103).
        """
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.PLANNING.value,
            choice=Status.PLANNING.value,
        )

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "parent_tv": parent_tv.id,
            "status": Status.IN_PROGRESS.value,
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))
        self.assertTrue(
            Item.objects.filter(
                title="TV Show",
                media_type=MediaTypes.SEASON.value,
                season_number=1,
            ).exists(),
        )
        self.assertFalse(Season.objects.filter(item__title="TV Show").exists())
        parent_tv.refresh_from_db()
        self.assertEqual(parent_tv.status, Status.PLANNING.value)

        for episode_number in (1, 2):
            Item.objects.create(
                media_id="1",
                source=Sources.MANUAL.value,
                media_type=MediaTypes.EPISODE.value,
                title="TV Show",
                season_number=2,
                episode_number=episode_number,
            )
        self.client.post(
            reverse("create_entry"),
            {**form_data, "season_number": 2, "status": Status.COMPLETED.value},
        )
        season = Season.objects.get(item__title="TV Show", item__season_number=2)
        self.assertEqual(season.status, Status.COMPLETED.value)
        self.assertEqual(season.related_tv, parent_tv)
        self.assertEqual(season.episodes.count(), 2)

    def test_create_entry_post_episode(self):
        """Test creating an episode entry with parent season.

        TV-205: the form names the season's Item (it used to name the tracking row).
        """
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        parent_season = Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=parent_tv,
            status=Status.IN_PROGRESS.value,
        )

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.EPISODE.value,
            "season_number": 1,
            "episode_number": 1,
            "parent_season": season_item.id,
            "end_date": "2023-01-02T00:00",
        }

        response = self.client.post(reverse("create_entry"), form_data, follow=True)

        self.assertRedirects(response, reverse("create_entry"))

        self.assertTrue(
            Item.objects.filter(
                title="TV Show",
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=1,
            ).exists(),
        )

        episode = Episode.objects.get(item__title="TV Show")
        self.assertEqual(episode.related_season, parent_season)
        end_date_local = timezone.localtime(episode.end_date)
        self.assertEqual(end_date_local.strftime("%Y-%m-%d %H:%M"), "2023-01-02 00:00")

    def test_tv_205_an_episode_added_to_an_untracked_season_ticks_it(self):
        """TV-205, TV-100: a manual season with nothing watched takes its first episode."""
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        TV.objects.create(item=tv_item, user=self.user, status=Status.PLANNING.value)
        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        self.assertFalse(Season.objects.filter(item=season_item).exists())

        response = self.client.post(
            reverse("create_entry"),
            {
                "title": "TV Show",
                "media_type": MediaTypes.EPISODE.value,
                "season_number": 1,
                "episode_number": 1,
                "parent_season": season_item.id,
                "end_date": "2023-01-02T00:00",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("create_entry"))
        episode = Episode.objects.get(item__title="TV Show", item__episode_number=1)
        self.assertEqual(episode.related_season.item, season_item)
        self.assertEqual(episode.related_season.user, self.user)

    def test_create_entry_post_duplicate_item(self):
        """Test creating a duplicate item."""
        tv_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.TV.value,
            title="TV Show",
        )
        parent_tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id="1",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.SEASON.value,
            title="TV Show",
            season_number=1,
        )
        Season.objects.create(
            item=season_item,
            user=self.user,
            related_tv=parent_tv,
            status=Status.IN_PROGRESS.value,
        )

        initial_count = Item.objects.count()

        form_data = {
            "title": "TV Show",
            "media_type": MediaTypes.SEASON.value,
            "season_number": 1,
            "parent_tv": parent_tv.id,
            "status": Status.IN_PROGRESS.value,
            "score": 7,
            "repeats": 0,
        }

        with transaction.atomic():
            self.client.post(reverse("create_entry"), form_data)

        self.assertEqual(Item.objects.count(), initial_count)
