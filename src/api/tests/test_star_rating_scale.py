"""One star scale on the API for clients that send ``X-Spine-Rating-Scale: stars``.

Storage stays 0-10 for every type. With the header, TV, anime, manga and comic ratings
go out as exact halves of storage (an imported 7.5 is "3.75") and come in as 0.5-5
half stars; without it, installed builds keep 0-10 for those types.
"""

from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from api.tests.test_tv_tracking_api import TVAPITestCase
from app.models import Anime, DiaryEntry, Item, MediaTypes, Movie, Sources, Status


class TVStarScaleTests(TVAPITestCase):
    """TV writes and payloads on the star scale."""

    def setUp(self):
        super().setUp()
        self.stars = self.client_for(self.user)
        self.stars.credentials(HTTP_X_SPINE_TIMEZONE="America/Los_Angeles", HTTP_X_SPINE_RATING_SCALE="stars")

    def test_rate_action_takes_and_returns_stars(self):
        show = self.make_show()
        self.tick(show, 1, 1)

        rated = self.action(show, "rate", client=self.stars, rating=4.5)
        season = self.action(show, "rate", client=self.stars, season_number=1, rating="3.5")

        self.assertEqual(rated.status_code, status.HTTP_200_OK, rated.data)
        self.assertEqual(rated.data["tracking"]["rating"], "4.5")
        self.assertEqual(self.tv(show).score, Decimal("9.0"))
        self.assertEqual(season.data["season"]["rating"], "3.5")
        self.assertEqual(self.tv(show).seasons.get(item__season_number=1).score, Decimal("7.0"))

    def test_rate_action_rejects_values_off_the_half_star_grid(self):
        show = self.make_show()
        self.tick(show, 1, 1)

        for rating in (4.25, 6, "eight", True):
            response = self.action(show, "rate", client=self.stars, rating=rating)
            self.assert_error(response, 400, "validation_error")
            self.assertIn("rating", response.data["error"]["fields"])
        cleared = self.action(show, "rate", client=self.stars, rating=None)

        self.assertEqual(cleared.status_code, status.HTTP_200_OK, cleared.data)
        self.assertIsNone(cleared.data["tracking"]["rating"])

    def test_installed_builds_keep_the_0_to_10_scale(self):
        show = self.make_show()
        self.tick(show, 1, 1)

        rated = self.action(show, "rate", rating=9)

        self.assertEqual(rated.data["tracking"]["rating"], "9.0")
        self.assertEqual(self.tv(show).score, Decimal("9.0"))
        self.assertEqual(self.detail_with(self.stars, show)["rating"], "4.5")

    def test_season_patch_takes_stars(self):
        show = self.make_show()
        self.tick(show, 1, 1)

        response = self.stars.patch(
            f"/api/v1/tracking/tmdb/season/{show.media_id}/",
            {"season_number": 2, "rating": "4"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["rating"], "4.0")
        self.assertEqual(self.tv(show).seasons.get(item__season_number=2).score, Decimal("8.0"))

    def test_logs_take_and_return_stars(self):
        show = self.make_show()

        created = self.log(show, 1, 2, client=self.stars, rating="3.5")
        entry = DiaryEntry.objects.get(pk=created.data["id"])
        edited = self.stars.patch(f"/api/v1/diary/{entry.pk}/", {"rating": "5"}, format="json")
        bad = self.stars.patch(f"/api/v1/diary/{entry.pk}/", {"rating": "8"}, format="json")

        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        self.assertEqual(created.data["rating"], "3.5")
        self.assertEqual(edited.data["rating"], "5.0")
        entry.refresh_from_db()
        self.assertEqual(entry.rating, Decimal("10.0"))
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        episode = self.stars.get(
            f"/api/v1/media/{show.source}/episode/{show.media_id}/",
            {"season_number": 1, "episode_number": 2},
        )
        self.assertEqual(episode.data["user_state"]["tv"]["episode"]["user_rating"], "5.0")

    def test_community_ratings_are_exact_stars(self):
        show = self.make_show()
        created = self.log(show, 1, 1, rating=8)
        DiaryEntry.objects.filter(pk=created.data["id"]).update(rating=Decimal("7.5"))
        url = f"/api/v1/media/tmdb/episode/{show.media_id}/community/?season_number=1&episode_number=1"

        stars = self.stars.get(url)
        legacy = self.client.get(url)

        self.assertEqual(stars.data["average_rating"], "3.75")
        self.assertEqual(stars.data["rating_distribution"], [{"rating": "3.75", "count": 1}])
        self.assertEqual(legacy.data["average_rating"], "7.50")

    def test_imported_ratings_go_out_exactly(self):
        """An import's 7.5 out of 10 is 3.75 stars; the app rounds when drawing them."""
        show = self.make_show()
        self.tick(show, 1, 1)
        self.tv(show).__class__.objects.filter(pk=self.tv(show).pk).update(score=Decimal("7.5"))

        self.assertEqual(self.detail_with(self.stars, show)["rating"], "3.75")
        self.assertEqual(self.detail_with(self.client, show)["rating"], "7.5")

    def detail_with(self, client, show):
        response = client.get(f"/api/v1/media/{show.source}/tv/{show.media_id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data["user_state"]


@patch("app.models.providers.services.get_media_metadata", return_value={"max_progress": None})
class AnimeStarScaleTests(TestCase):
    """Anime (and so manga and comics) through the generic tracking and diary writes."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="stars", password="strong-password-123")
        self.item = Item.objects.create(
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            media_id="1",
            title="Anime",
        )
        self.anime = Anime.objects.create(user=self.user, item=self.item, status=Status.IN_PROGRESS.value)
        self.stars = APIClient()
        self.stars.force_authenticate(self.user)
        self.stars.credentials(HTTP_X_SPINE_RATING_SCALE="stars")
        self.legacy = APIClient()
        self.legacy.force_authenticate(self.user)

    def test_tracking_takes_and_returns_stars(self, _metadata):
        response = self.stars.patch("/api/v1/tracking/mal/anime/1/", {"rating": "4.5"}, format="json")
        bad = self.stars.patch("/api/v1/tracking/mal/anime/1/", {"rating": "9"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["rating"], "4.5")
        self.anime.refresh_from_db()
        self.assertEqual(self.anime.score, Decimal("9.0"))
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)

    def test_installed_builds_keep_the_0_to_10_scale(self, _metadata):
        response = self.legacy.patch("/api/v1/tracking/mal/anime/1/", {"rating": "7.5"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["rating"], "7.5")
        self.anime.refresh_from_db()
        self.assertEqual(self.anime.score, Decimal("7.5"))
        stars = self.stars.get("/api/v1/tracking/mal/anime/1/")
        self.assertEqual(stars.data["rating"], "3.75")

    @patch("api.services.diary.provider_services.get_media_metadata", return_value={"title": "Anime"})
    def test_logs_take_and_return_stars(self, _diary_metadata, _metadata):
        ref = {"source": Sources.MAL.value, "media_type": MediaTypes.ANIME.value, "media_id": "1"}

        created = self.stars.post("/api/v1/diary/", {"ref": ref, "rating": "4"}, format="json")
        entry = DiaryEntry.objects.get(pk=created.data["id"])
        edited = self.stars.patch(f"/api/v1/diary/{entry.pk}/", {"rating": "2.5"}, format="json")
        legacy = self.legacy.get(f"/api/v1/diary/{entry.pk}/")

        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        self.assertEqual(created.data["rating"], "4.0")
        self.assertEqual(edited.data["rating"], "2.5")
        entry.refresh_from_db()
        self.assertEqual(entry.rating, Decimal("5.0"))
        self.assertEqual(legacy.data["rating"], "5.0")


class CommunityStarScaleTests(TestCase):
    """Movies were already stars; the header changes nothing for them."""

    def test_movies_are_stars_either_way(self):
        user = get_user_model().objects.create_user(username="movies", password="strong-password-123")
        item = Item.objects.create(source=Sources.TMDB.value, media_type=MediaTypes.MOVIE.value, media_id="3", title="M")
        Movie.objects.create(user=user, item=item, status=Status.COMPLETED.value, score=Decimal(9))
        client = APIClient()
        client.force_authenticate(user)

        stars = client.get("/api/v1/media/tmdb/movie/3/community/", HTTP_X_SPINE_RATING_SCALE="stars")
        legacy = client.get("/api/v1/media/tmdb/movie/3/community/")

        self.assertEqual(stars.data["average_rating"], legacy.data["average_rating"])
