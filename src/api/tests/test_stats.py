from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from app.models import (
    Book,
    CustomPosterPreference,
    DiaryEntry,
    Item,
    ItemFilterFacet,
    MediaLike,
    MediaTypes,
    Movie,
    Music,
    Sources,
    Status,
)
from social.models import Follow, FollowStatus


class StatsAPITests(TestCase):
    """Exercise the additive native stats contract and its privacy rules."""

    def setUp(self):
        self.client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="stats-user",
            password="strong-password-123",
        )

    def test_stats_requires_authentication(self):
        response = self.client.get("/api/v1/stats/me/summary/")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_stats_have_stable_native_shape_and_legacy_keys(self):
        self.client.force_authenticate(self.user)

        response = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "all", "end_date": "all"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for legacy_key in (
            "start_date",
            "end_date",
            "media_count",
            "media_type_distribution",
            "score_distribution",
            "status_distribution",
            "top_rated",
        ):
            self.assertIn(legacy_key, response.data)

        self.assertEqual(response.data["schema_version"], 1)
        self.assertEqual(
            response.data["range"],
            {
                "start_date": None,
                "end_date": None,
                "timezone": timezone.get_current_timezone_name(),
                "is_all_time": True,
            },
        )
        self.assertEqual(response.data["overview"]["tracked_count"], 0)
        self.assertEqual(response.data["overview"]["diary_entry_count"], 0)
        self.assertEqual(response.data["overview"]["average_rating"], None)
        self.assertEqual(response.data["activity"]["days"], [])
        self.assertIsNone(response.data["activity"]["most_active_weekday"])
        self.assertEqual(len(response.data["rating_distribution"]), 21)
        self.assertEqual(response.data["rating_distribution"][1], {"rating": "0.5", "count": 0})
        self.assertEqual(
            [entry["media_type"] for entry in response.data["media_types"]],
            ["movie", "tv", "anime", "manga", "game", "book", "comic", "music"],
        )
        for entry in response.data["media_types"]:
            self.assertEqual(entry["tracked_count"], 0)
            self.assertEqual(entry["rating_distribution"][0], {"rating": "0.0", "count": 0})
            self.assertEqual(entry["top_rated"], [])
            self.assertEqual(entry["most_logged"], [])

    def test_music_populates_every_primary_statistics_bucket(self):
        item = Item.objects.create(
            media_id="3bd76d40-7f0e-36b7-9348-91a33afee20e",
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            title="Year Zero",
            image="https://example.com/year-zero.jpg",
            release_year=2007,
        )
        listened_at = self._aware(2026, 1, 10)
        Music.objects.bulk_create([
            Music(
                user=self.user,
                item=item,
                status=Status.COMPLETED.value,
                score=Decimal("9.0"),
                start_date=listened_at,
                end_date=listened_at,
            ),
        ])
        DiaryEntry.objects.bulk_create([
            DiaryEntry(
                user=self.user,
                item=item,
                consumed_at=listened_at,
                rating=Decimal("8.0"),
                review="Still sounds dangerous.",
                visibility="public",
            ),
            DiaryEntry(
                user=self.user,
                item=item,
                consumed_at=self._aware(2026, 1, 11),
                rating=Decimal("9.0"),
                is_rewatch=True,
                visibility="public",
            ),
        ])
        ItemFilterFacet.objects.bulk_create([
            ItemFilterFacet(
                item=item,
                facet_type=ItemFilterFacet.FacetType.GENRE,
                value="Industrial Rock",
            ),
            ItemFilterFacet(
                item=item,
                facet_type=ItemFilterFacet.FacetType.LANGUAGE,
                value="English",
            ),
        ])
        MediaLike.objects.create(user=self.user, item=item)
        self.client.force_authenticate(self.user)

        response = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["overview"]["tracked_count"], 1)
        self.assertEqual(response.data["overview"]["completed_count"], 1)
        self.assertEqual(response.data["overview"]["diary_entry_count"], 2)
        self.assertEqual(response.data["overview"]["unique_logged_count"], 1)
        self.assertEqual(response.data["overview"]["repeat_count"], 1)
        self.assertEqual(response.data["overview"]["liked_count"], 1)
        self.assertEqual(response.data["media_count"][MediaTypes.MUSIC.value], 1)

        music = self._media_stats(response, MediaTypes.MUSIC.value)
        self.assertEqual(music["tracked_count"], 1)
        self.assertEqual(music["completed_count"], 1)
        self.assertEqual(music["statuses"]["completed"], 1)
        self.assertEqual(music["diary_entry_count"], 2)
        self.assertEqual(music["rating_distribution"][8]["count"], 1)
        self.assertEqual(music["rating_distribution"][9]["count"], 1)
        self.assertEqual(music["release_years"], [{"year": 2007, "count": 1}])
        self.assertEqual(music["top_genres"], [{"name": "Industrial Rock", "count": 1}])
        self.assertEqual(music["top_languages"], [{"name": "English", "count": 1}])
        self.assertEqual(
            music["metadata_coverage"],
            {
                "total_items": 1,
                "release_year_items": 1,
                "genre_items": 1,
                "language_items": 1,
            },
        )
        self.assertEqual(music["top_rated"][0]["rating"], "4.5")
        self.assertEqual(music["most_logged"][0]["log_count"], 2)

    @patch("app.providers.services.get_media_metadata")
    def test_populated_stats_use_local_tracking_diary_and_metadata(self, metadata_mock):
        movie_item = Item.objects.create(
            media_id="550",
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
            release_year=1999,
        )
        book_item = Item.objects.create(
            media_id="book-1",
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            title="A Book",
            image="https://example.com/book.jpg",
            release_year=2020,
        )
        watched_at = self._aware(2026, 1, 10)
        Movie.objects.bulk_create([
            Movie(
                user=self.user,
                item=movie_item,
                status=Status.COMPLETED.value,
                score=Decimal("9.0"),
                start_date=watched_at,
                end_date=watched_at,
            ),
        ])
        Book.objects.bulk_create([
            Book(
                user=self.user,
                item=book_item,
                status=Status.PLANNING.value,
            ),
        ])
        DiaryEntry.objects.bulk_create([
            DiaryEntry(
                user=self.user,
                item=movie_item,
                consumed_at=watched_at,
                rating=Decimal("8.0"),
                review="Sharp and restless.",
                visibility="public",
            ),
            DiaryEntry(
                user=self.user,
                item=movie_item,
                consumed_at=self._aware(2026, 1, 11),
                rating=Decimal("9.0"),
                is_rewatch=True,
                visibility="public",
            ),
        ])
        ItemFilterFacet.objects.bulk_create([
            ItemFilterFacet(
                item=movie_item,
                facet_type=ItemFilterFacet.FacetType.GENRE,
                value="Drama",
            ),
            ItemFilterFacet(
                item=movie_item,
                facet_type=ItemFilterFacet.FacetType.LANGUAGE,
                value="English",
            ),
        ])
        MediaLike.objects.create(user=self.user, item=movie_item)
        self.client.force_authenticate(self.user)

        response = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        metadata_mock.assert_not_called()
        overview = response.data["overview"]
        self.assertEqual(overview["tracked_count"], 2)
        self.assertEqual(overview["completed_count"], 1)
        self.assertEqual(overview["diary_entry_count"], 2)
        self.assertEqual(overview["unique_logged_count"], 1)
        self.assertEqual(overview["repeat_count"], 1)
        self.assertEqual(overview["rated_count"], 2)
        self.assertEqual(overview["average_rating"], "4.25")
        self.assertEqual(overview["review_count"], 1)
        self.assertEqual(overview["liked_count"], 1)
        self.assertEqual(overview["active_days"], 2)
        self.assertEqual(overview["longest_streak_days"], 2)

        distribution = {
            entry["rating"]: entry["count"]
            for entry in response.data["rating_distribution"]
        }
        self.assertEqual(distribution["4.0"], 1)
        self.assertEqual(distribution["4.5"], 1)
        self.assertEqual(response.data["release_years"], [{"year": 1999, "count": 1}])
        self.assertEqual(response.data["top_genres"], [{"name": "Drama", "count": 1}])
        self.assertEqual(response.data["top_languages"], [{"name": "English", "count": 1}])
        self.assertEqual(
            response.data["metadata_coverage"],
            {
                "total_items": 1,
                "release_year_items": 1,
                "genre_items": 1,
                "language_items": 1,
            },
        )
        self.assertEqual(response.data["top_rated"][0]["rating"], "4.5")
        self.assertIsNone(response.data["top_rated"][0]["media"]["user_state"])
        self.assertEqual(response.data["most_logged"][0]["log_count"], 2)
        self.assertEqual(
            response.data["activity"]["most_active_weekday"],
            {
                "weekday": 5,
                "name": "Saturday",
                "active_day_count": 1,
                "percentage": 50.0,
            },
        )

        movie_stats = self._media_stats(response, MediaTypes.MOVIE.value)
        self.assertEqual(movie_stats["tracked_count"], 1)
        self.assertEqual(movie_stats["completed_count"], 1)
        self.assertEqual(movie_stats["statuses"]["completed"], 1)
        self.assertEqual(movie_stats["diary_entry_count"], 2)
        self.assertEqual(movie_stats["top_rated"][0]["rating"], "4.5")
        self.assertEqual(movie_stats["most_logged"][0]["log_count"], 2)
        self.assertEqual(movie_stats["release_years"], [{"year": 1999, "count": 1}])

    def test_stats_validate_dates_including_all_time_and_leap_day(self):
        self.client.force_authenticate(self.user)

        invalid = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "not-a-date", "end_date": "2026-01-01"},
        )
        reversed_range = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "2026-02-01", "end_date": "2026-01-01"},
        )
        mismatched_all = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "all", "end_date": "2026-01-01"},
        )
        leap_day = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "2024-02-29", "end_date": "2024-03-01"},
        )

        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(reversed_range.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(mismatched_all.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(leap_day.status_code, status.HTTP_200_OK)
        self.assertEqual(leap_day.data["range"]["start_date"], "2024-02-29")

    def test_season_and_episode_logs_roll_up_into_stable_tv_bucket(self):
        items = [
            Item.objects.create(
                media_id="tv-rollup",
                source=Sources.TMDB.value,
                media_type=MediaTypes.TV.value,
                title="Rollup Show",
                image="https://example.com/show.jpg",
            ),
            Item.objects.create(
                media_id="tv-rollup",
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                season_number=1,
                title="Rollup Show",
                image="https://example.com/season.jpg",
            ),
            Item.objects.create(
                media_id="tv-rollup",
                source=Sources.TMDB.value,
                media_type=MediaTypes.EPISODE.value,
                season_number=1,
                episode_number=1,
                title="Pilot",
                image="https://example.com/episode.jpg",
            ),
        ]
        DiaryEntry.objects.bulk_create([
            DiaryEntry(
                user=self.user,
                item=item,
                consumed_at=self._aware(2026, 3, index + 1),
                visibility="public",
            )
            for index, item in enumerate(items)
        ])
        self.client.force_authenticate(self.user)

        response = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "2026-03-01", "end_date": "2026-03-31"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tv_stats = self._media_stats(response, MediaTypes.TV.value)
        self.assertEqual(tv_stats["diary_entry_count"], 3)
        self.assertEqual(tv_stats["unique_logged_count"], 3)
        self.assertNotIn(
            MediaTypes.SEASON.value,
            [entry["media_type"] for entry in response.data["media_types"]],
        )
        self.assertNotIn(
            MediaTypes.EPISODE.value,
            [entry["media_type"] for entry in response.data["media_types"]],
        )

    def test_public_user_stats_use_account_visibility_and_omit_target_state(self):
        target = get_user_model().objects.create_user(
            username="public-stats-user",
            password="strong-password-123",
            profile_private=False,
        )
        viewer = get_user_model().objects.create_user(
            username="stats-viewer",
            password="strong-password-123",
        )
        items = [
            Item.objects.create(
                media_id=f"visible-{index}",
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                title=f"Visibility {index}",
                image=f"https://example.com/{index}.jpg",
            )
            for index in range(3)
        ]
        DiaryEntry.objects.bulk_create([
            DiaryEntry(
                user=target,
                item=items[0],
                consumed_at=self._aware(2026, 2, 1),
                rating=Decimal("7.0"),
                visibility="public",
            ),
            DiaryEntry(
                user=target,
                item=items[1],
                consumed_at=self._aware(2026, 2, 2),
                rating=Decimal("8.0"),
                visibility="followers",
            ),
            DiaryEntry(
                user=target,
                item=items[2],
                consumed_at=self._aware(2026, 2, 3),
                rating=Decimal("10.0"),
                visibility="private",
            ),
        ])
        Movie.objects.bulk_create([
            Movie(
                user=target,
                item=items[0],
                status=Status.COMPLETED.value,
                score=Decimal("2.0"),
            ),
            Movie(
                user=target,
                item=items[2],
                status=Status.COMPLETED.value,
                score=Decimal("10.0"),
            ),
        ])
        self.client.force_authenticate(viewer)

        public_response = self.client.get(
            f"/api/v1/users/{target.username}/stats/summary/",
            {"start_date": "all", "end_date": "all"},
        )

        self.assertEqual(public_response.status_code, status.HTTP_200_OK)
        self.assertEqual(public_response.data["overview"]["diary_entry_count"], 3)
        self.assertEqual(public_response.data["diary_top_rated"][0]["rating"], "5.0")
        self.assertIsNone(public_response.data["diary_top_rated"][0]["media"]["user_state"])
        self.assertEqual(public_response.data["score_distribution"]["total_scored"], 3)
        self.assertEqual(public_response.data["score_distribution"]["average_score"], 4.17)
        self.assertEqual(public_response.data["top_rated"][0]["rating"], "5.0")

        Follow.objects.create(
            from_user=viewer,
            to_user=target,
            status=FollowStatus.ACCEPTED,
        )
        follower_response = self.client.get(
            f"/api/v1/users/{target.username}/stats/summary/",
            {"start_date": "all", "end_date": "all"},
        )

        self.assertEqual(follower_response.status_code, status.HTTP_200_OK)
        self.assertEqual(follower_response.data["overview"]["diary_entry_count"], 3)
        self.assertEqual(follower_response.data["diary_top_rated"][0]["rating"], "5.0")
        self.assertEqual(follower_response.data["score_distribution"]["total_scored"], 3)
        self.assertEqual(follower_response.data["score_distribution"]["average_score"], 4.17)
        returned_ids = {
            entry["media"]["ref"]["item_id"]
            for entry in follower_response.data["diary_top_rated"]
        }
        self.assertIn(items[2].id, returned_ids)

    def test_most_logged_keeps_repeat_titles_and_reports_totals(self):
        movies = self._items(MediaTypes.MOVIE.value, 10)
        book = self._items(MediaTypes.BOOK.value, 1)[0]
        once = self._items(MediaTypes.MOVIE.value, 1, prefix="once")[0]
        self._log(movies[0], days=[1, 2, 3])
        for index, item in enumerate(movies[1:], start=4):
            self._log(item, days=[index, index + 10])
        self._log(book, days=[30, 31])
        self._log(once, days=[25])
        self.client.force_authenticate(self.user)

        response = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "all", "end_date": "all"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned = [entry["media"]["ref"]["item_id"] for entry in response.data["most_logged"]]
        self.assertEqual(len(returned), 11)
        self.assertEqual(returned[0], movies[0].id)
        self.assertNotIn(once.id, returned)
        self.assertTrue(all(entry["log_count"] >= 2 for entry in response.data["most_logged"]))
        self.assertEqual(response.data["most_logged_total"], 11)

        movie_stats = self._media_stats(response, MediaTypes.MOVIE.value)
        self.assertEqual(len(movie_stats["most_logged"]), 8)
        self.assertEqual(movie_stats["most_logged_total"], 10)
        self.assertEqual(self._media_stats(response, MediaTypes.BOOK.value)["most_logged_total"], 1)
        self.assertEqual(self._media_stats(response, MediaTypes.TV.value)["most_logged_total"], 0)

    def test_most_logged_endpoint_pages_filters_and_validates(self):
        movies = self._items(MediaTypes.MOVIE.value, 3)
        book = self._items(MediaTypes.BOOK.value, 1)[0]
        self._log(movies[0], days=[1, 2, 3])
        self._log(movies[1], days=[4, 5])
        self._log(movies[2], days=[6], months=[2, 3])
        self._log(book, days=[7, 8])
        self.client.force_authenticate(self.user)

        first = self.client.get(
            "/api/v1/stats/me/most-logged/",
            {"start_date": "all", "end_date": "all", "page_size": 2},
        )
        second = self.client.get(
            "/api/v1/stats/me/most-logged/",
            {"start_date": "all", "end_date": "all", "page_size": 2, "page": 2},
        )
        movies_only = self.client.get(
            "/api/v1/stats/me/most-logged/",
            {"start_date": "all", "end_date": "all", "media_type": MediaTypes.MOVIE.value},
        )
        february = self.client.get(
            "/api/v1/stats/me/most-logged/",
            {"start_date": "2026-02-01", "end_date": "2026-02-28"},
        )
        invalid = self.client.get(
            "/api/v1/stats/me/most-logged/",
            {"start_date": "all", "end_date": "all", "media_type": "episode"},
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["count"], 4)
        self.assertEqual(first.data["results"][0]["media"]["ref"]["item_id"], movies[0].id)
        self.assertEqual(first.data["results"][0]["log_count"], 3)
        self.assertIsNotNone(first.data["next"])
        self.assertEqual(len(second.data["results"]), 2)
        self.assertIsNone(second.data["next"])
        self.assertEqual(movies_only.data["count"], 3)
        self.assertEqual(
            {entry["media"]["ref"]["media_type"] for entry in movies_only.data["results"]},
            {MediaTypes.MOVIE.value},
        )
        self.assertEqual(february.data["count"], 0)
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

    def test_other_user_most_logged_respects_visibility_and_privacy(self):
        target = get_user_model().objects.create_user(
            username="repeat-target",
            password="strong-password-123",
            profile_private=False,
        )
        hidden = get_user_model().objects.create_user(
            username="repeat-hidden",
            password="strong-password-123",
            profile_private=True,
        )
        viewer = get_user_model().objects.create_user(
            username="repeat-viewer",
            password="strong-password-123",
        )
        public_anime, mixed_anime = self._items(MediaTypes.ANIME.value, 2)
        self._log(public_anime, days=[1, 2], user=target)
        self._log(mixed_anime, days=[3], user=target)
        self._log(mixed_anime, days=[4], user=target, visibility="private")
        self.client.force_authenticate(viewer)

        response = self.client.get(
            f"/api/v1/users/{target.username}/stats/most-logged/",
            {"start_date": "all", "end_date": "all"},
        )
        private_profile = self.client.get(
            f"/api/v1/users/{hidden.username}/stats/most-logged/",
            {"start_date": "all", "end_date": "all"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [entry["media"]["ref"]["item_id"] for entry in response.data["results"]],
            [public_anime.id],
        )
        self.assertIsNone(response.data["results"][0]["media"]["user_state"])
        self.assertEqual(private_profile.status_code, status.HTTP_404_NOT_FOUND)

    def test_stats_posters_use_the_stats_owners_custom_artwork(self):
        viewer = get_user_model().objects.create_user(
            username="poster-viewer",
            password="strong-password-123",
        )
        self.user.profile_private = False
        self.user.save(update_fields=["profile_private"])
        movie = self._items(MediaTypes.MOVIE.value, 1)[0]
        DiaryEntry.objects.bulk_create([
            DiaryEntry(
                user=self.user,
                item=movie,
                consumed_at=self._aware(2026, 1, day),
                rating=Decimal("4.5"),
                visibility="public",
            )
            for day in (1, 2)
        ])
        owner_poster = "https://example.com/owner-poster.jpg"
        CustomPosterPreference.objects.create(user=self.user, item=movie, custom_image_url=owner_poster)
        CustomPosterPreference.objects.create(
            user=viewer,
            item=movie,
            custom_image_url="https://example.com/viewer-poster.jpg",
        )
        params = {"start_date": "all", "end_date": "all"}

        self.client.force_authenticate(self.user)
        own = self.client.get("/api/v1/stats/me/summary/", params)
        own_page = self.client.get("/api/v1/stats/me/most-logged/", params)
        self.client.force_authenticate(viewer)
        public = self.client.get(f"/api/v1/users/{self.user.username}/stats/summary/", params)
        public_page = self.client.get(f"/api/v1/users/{self.user.username}/stats/most-logged/", params)

        movie_stats = self._media_stats(own, MediaTypes.MOVIE.value)
        for media in (
            own.data["most_logged"][0]["media"],
            own.data["diary_top_rated"][0]["media"],
            movie_stats["most_logged"][0]["media"],
            movie_stats["top_rated"][0]["media"],
            own_page.data["results"][0]["media"],
            public.data["most_logged"][0]["media"],
            public_page.data["results"][0]["media"],
        ):
            self.assertEqual(media["custom_poster_url"], owner_poster)
            self.assertIsNone(media["user_state"])

    @staticmethod
    def _items(media_type, count, *, prefix="item"):
        return [
            Item.objects.create(
                media_id=f"{prefix}-{media_type}-{index}",
                source=Sources.TMDB.value,
                media_type=media_type,
                title=f"{prefix.title()} {media_type} {index}",
                image=f"https://example.com/{prefix}-{media_type}-{index}.jpg",
            )
            for index in range(count)
        ]

    def _log(self, item, *, days, months=(1,), user=None, visibility="public"):
        DiaryEntry.objects.bulk_create([
            DiaryEntry(
                user=user or self.user,
                item=item,
                consumed_at=self._aware(2026, month, day),
                visibility=visibility,
            )
            for month in months
            for day in days
        ])

    @staticmethod
    def _aware(year, month, day):
        return datetime(
            year,
            month,
            day,
            12,
            tzinfo=timezone.get_current_timezone(),
        )

    @staticmethod
    def _media_stats(response, media_type):
        return next(
            entry
            for entry in response.data["media_types"]
            if entry["media_type"] == media_type
        )
