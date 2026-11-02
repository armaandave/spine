"""The people you follow on a title's page (docs/activity-contract.md, AC-6)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.services.social import block_user
from app.models import (
    TV,
    Book,
    BookSession,
    DiaryEntry,
    Episode,
    Game,
    GameSession,
    Item,
    Manga,
    MediaLike,
    MediaTypes,
    Movie,
    Season,
    Sources,
    Status,
)
from social.models import Follow, FollowStatus


class MediaFriendsTests(TestCase):
    """Who shows on a title's page, what each shows, and in what order."""

    def setUp(self):
        self.viewer = self.user("viewer")
        self.client = APIClient()
        self.client.force_authenticate(self.viewer)

    def user(self, username, **fields):
        return get_user_model().objects.create_user(username=username, password="strong-password-123", **fields)

    def friend(self, username, **fields):
        person = self.user(username, **fields)
        Follow.objects.create(from_user=self.viewer, to_user=person, status=FollowStatus.ACCEPTED)
        return person

    def item(self, media_type=MediaTypes.MOVIE.value, **fields):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=f"{media_type}-1",
            title="Title",
            image="https://example.com/poster.jpg",
            **fields,
        )

    def log(self, item, user, consumed_at=None, **fields):
        return DiaryEntry.objects.create(
            user=user,
            item=item,
            consumed_at=consumed_at or datetime(2026, 10, 14, tzinfo=UTC),
            **fields,
        )

    def get(self, item, **params):
        response = self.client.get(
            f"/api/v1/media/{item.source}/{item.media_type}/{item.media_id}/friends/",
            params,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def only_friend(self, item, **params):
        data = self.get(item, **params)
        self.assertEqual(data["count"], 1)
        return data["results"][0]

    def test_requires_sign_in(self):
        response = APIClient().get("/api/v1/media/manual/movie/movie-1/friends/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unknown_title_has_no_friends(self):
        response = self.client.get("/api/v1/media/manual/movie/nothing/friends/")
        self.assertEqual(response.data, {"count": 0, "results": []})

    def test_log_shows_its_rating_heart_and_review(self):
        item = self.item()
        maya = self.friend("maya")
        entry = self.log(
            item,
            maya,
            rating=Decimal(9),
            liked=True,
            is_rewatch=True,
            review_title="Wow",
            review="Wrecked me.",
            contains_spoilers=True,
        )
        friend = self.only_friend(item)
        self.assertEqual(friend["user"]["username"], "maya")
        self.assertEqual(friend["diary_entry_id"], entry.id)
        self.assertEqual(friend["consumed_at"], "2026-10-14")
        self.assertEqual(friend["rating"], "4.5")
        self.assertTrue(friend["liked"])
        self.assertTrue(friend["is_rewatch"])
        self.assertEqual(friend["review_title"], "Wow")
        self.assertEqual(friend["review"], "Wrecked me.")
        self.assertTrue(friend["contains_spoilers"])
        self.assertIsNone(friend["progress_label"])

    def test_newest_log_with_a_review_wins_over_a_newer_one_without(self):
        item = self.item()
        maya = self.friend("maya")
        reviewed = self.log(item, maya, datetime(2024, 1, 1, tzinfo=UTC), review="Loved it.", rating=Decimal(8))
        self.log(item, maya, datetime(2023, 1, 1, tzinfo=UTC), review="Older words.")
        self.log(item, maya, datetime(2026, 1, 1, tzinfo=UTC), rating=Decimal(10))
        friend = self.only_friend(item)
        self.assertEqual(friend["diary_entry_id"], reviewed.id)
        self.assertEqual(friend["rating"], "4.0")

    def test_log_without_a_rating_shows_the_title_rating(self):
        item = self.item()
        maya = self.friend("maya")
        Movie.objects.create(user=maya, item=item, status=Status.COMPLETED.value, score=Decimal(7))
        self.log(item, maya)
        self.assertEqual(self.only_friend(item)["rating"], "3.5")

    def test_book_without_a_log_shows_where_they_stand(self):
        item = self.item(MediaTypes.BOOK.value, total_pages=476)
        theo = self.friend("theo")
        book = Book.objects.create(user=theo, item=item, status=Status.IN_PROGRESS.value, progress=212)
        BookSession.objects.create(related_book=book, status=Status.IN_PROGRESS.value, pages_read=212)
        friend = self.only_friend(item)
        self.assertIsNone(friend["diary_entry_id"])
        self.assertIsNone(friend["rating"])
        self.assertFalse(friend["liked"])
        self.assertEqual(friend["review"], "")
        self.assertEqual(friend["status"], Status.IN_PROGRESS.value)
        self.assertEqual(friend["progress_label"], "212 of 476 pages")

    def test_stopped_book_says_how_far_they_got_by_percent(self):
        item = self.item(MediaTypes.BOOK.value)
        sam = self.friend("sam")
        book = Book.objects.create(user=sam, item=item, status=Status.DROPPED.value)
        session = BookSession.objects.create(related_book=book, status=Status.DROPPED.value, percentage_read=Decimal(38))
        Book.objects.filter(pk=book.pk).update(current_session=session)
        friend = self.only_friend(item)
        self.assertEqual(friend["status"], Status.DROPPED.value)
        self.assertEqual(friend["progress_label"], "38%")

    def test_finished_without_a_log_shows_no_progress(self):
        item = self.item(MediaTypes.BOOK.value, total_pages=476)
        Book.objects.create(user=self.friend("sam"), item=item, status=Status.COMPLETED.value)
        friend = self.only_friend(item)
        self.assertEqual(friend["status"], Status.COMPLETED.value)
        self.assertIsNone(friend["progress_label"])

    def test_game_tells_playtime(self):
        item = self.item(MediaTypes.GAME.value)
        game = Game.objects.create(user=self.friend("jonah"), item=item, status=Status.IN_PROGRESS.value)
        session = GameSession.objects.create(
            related_game=game,
            status=Status.IN_PROGRESS.value,
            start_date=date(2026, 10, 1),
            total_minutes=150,
        )
        Game.objects.filter(pk=game.pk).update(current_session=session)
        self.assertEqual(self.only_friend(item)["progress_label"], "2h 30m")

    def test_manga_counts_chapters(self):
        item = self.item(MediaTypes.MANGA.value)
        Manga.objects.create(user=self.friend("jonah"), item=item, status=Status.PAUSED.value, progress=12)
        self.assertEqual(self.only_friend(item)["progress_label"], "12 chapters")

    def test_wanting_to_get_to_a_title_does_not_count(self):
        item = self.item()
        Movie.objects.create(user=self.friend("maya"), item=item, status=Status.PLANNING.value)
        self.assertEqual(self.get(item), {"count": 0, "results": []})

    def test_a_rating_or_heart_without_a_log_counts(self):
        item = self.item()
        Movie.objects.create(user=self.friend("maya"), item=item, status=Status.PLANNING.value, score=Decimal(7))
        MediaLike.objects.create(user=self.friend("theo"), item=item)
        data = self.get(item)
        self.assertEqual(data["count"], 2)
        by_name = {friend["user"]["username"]: friend for friend in data["results"]}
        self.assertEqual(by_name["maya"]["rating"], "3.5")
        self.assertIsNone(by_name["maya"]["status"])
        self.assertTrue(by_name["theo"]["liked"])

    def test_only_people_the_viewer_follows_show(self):
        item = self.item()
        self.log(item, self.user("stranger"))
        requested = self.user("requested")
        Follow.objects.create(from_user=self.viewer, to_user=requested, status=FollowStatus.PENDING)
        self.log(item, requested)
        self.log(item, self.viewer)
        self.assertEqual(self.get(item)["count"], 0)

    def test_blocked_people_do_not_show(self):
        item = self.item()
        self.log(item, self.friend("blocked"))
        self.log(item, self.friend("blocker"))
        block_user(self.viewer, "blocked")
        block_user(get_user_model().objects.get(username="blocker"), "viewer")
        self.assertEqual(self.get(item)["count"], 0)

    def test_private_account_you_follow_shows(self):
        item = self.item()
        self.log(item, self.friend("quiet", profile_private=True), rating=Decimal(8))
        self.assertEqual(self.only_friend(item)["user"]["username"], "quiet")

    def test_followers_only_tv_log_shows_and_private_one_hides_its_taste(self):
        item = self.item(MediaTypes.TV.value)
        open_friend = self.friend("open")
        self.log(item, open_friend, visibility="followers", rating=Decimal(8), review="Great.")
        secret = self.friend("secret")
        self.log(item, secret, visibility="private", rating=Decimal(2), liked=True, review="Hidden words.")
        TV.objects.create(user=secret, item=item, status=Status.IN_PROGRESS.value, score=Decimal(2))
        MediaLike.objects.create(user=secret, item=item)
        by_name = {friend["user"]["username"]: friend for friend in self.get(item)["results"]}
        self.assertEqual(by_name["open"]["review"], "Great.")
        self.assertIsNone(by_name["secret"]["diary_entry_id"])
        self.assertEqual(by_name["secret"]["review"], "")
        self.assertIsNone(by_name["secret"]["rating"])
        self.assertFalse(by_name["secret"]["liked"])
        self.assertEqual(by_name["secret"]["status"], Status.IN_PROGRESS.value)

    def test_private_log_alone_is_not_shown(self):
        item = self.item(MediaTypes.TV.value)
        self.log(item, self.friend("secret"), visibility="private", rating=Decimal(2))
        self.assertEqual(self.get(item)["count"], 0)

    def test_season_page_counts_a_started_season_only(self):
        tv_item = self.item(MediaTypes.TV.value)
        season_item = Item.objects.create(
            source=tv_item.source,
            media_type=MediaTypes.SEASON.value,
            media_id=tv_item.media_id,
            season_number=1,
            title="Title",
            image="https://example.com/poster.jpg",
        )
        started = self.friend("started")
        started_tv = TV.objects.create(user=started, item=tv_item, status=Status.IN_PROGRESS.value)
        Season.objects.create(
            user=started,
            item=season_item,
            related_tv=started_tv,
            status=Status.IN_PROGRESS.value,
            is_started=True,
            watched_count=6,
            aired_count=10,
        )
        waiting = self.friend("waiting")
        waiting_tv = TV.objects.create(user=waiting, item=tv_item, status=Status.PLANNING.value)
        Season.objects.create(
            user=waiting,
            item=season_item,
            related_tv=waiting_tv,
            status=Status.IN_PROGRESS.value,
            is_started=False,
        )
        response = self.client.get(
            f"/api/v1/media/{season_item.source}/season/{season_item.media_id}/friends/",
            {"season_number": 1},
        )
        self.assertEqual(response.data["count"], 1)
        friend = response.data["results"][0]
        self.assertEqual(friend["user"]["username"], "started")
        self.assertEqual(friend["progress_label"], "6 of 10 episodes")

    def test_episode_page_shows_a_viewing_as_finished(self):
        tv_item = self.item(MediaTypes.TV.value)
        season_item = Item.objects.create(
            source=tv_item.source,
            media_type=MediaTypes.SEASON.value,
            media_id=tv_item.media_id,
            season_number=1,
            title="Title",
            image="https://example.com/poster.jpg",
        )
        episode_item = Item.objects.create(
            source=tv_item.source,
            media_type=MediaTypes.EPISODE.value,
            media_id=tv_item.media_id,
            season_number=1,
            episode_number=2,
            title="Title",
            image="https://example.com/poster.jpg",
        )
        person = self.friend("watcher")
        tv = TV.objects.create(user=person, item=tv_item, status=Status.IN_PROGRESS.value)
        season = Season.objects.create(user=person, item=season_item, related_tv=tv, status=Status.IN_PROGRESS.value)
        Episode.objects.create(item=episode_item, related_season=season, end_date=timezone.now())
        response = self.client.get(
            f"/api/v1/media/{episode_item.source}/episode/{episode_item.media_id}/friends/",
            {"season_number": 1, "episode_number": 2},
        )
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["status"], Status.COMPLETED.value)
        self.assertIsNone(response.data["results"][0]["progress_label"])

    def test_newest_comes_first(self):
        item = self.item(MediaTypes.BOOK.value, total_pages=476)
        self.log(item, self.friend("old"), datetime(2020, 1, 1, tzinfo=UTC))
        self.log(item, self.friend("recent"), datetime(2026, 1, 1, tzinfo=UTC))
        reading = Book.objects.create(user=self.friend("reading"), item=item, status=Status.IN_PROGRESS.value)
        Book.objects.filter(pk=reading.pk).update(progressed_at=timezone.now())
        names = [friend["user"]["username"] for friend in self.get(item)["results"]]
        self.assertEqual(names, ["reading", "recent", "old"])

    @override_settings(TIME_ZONE="America/New_York")
    def test_log_date_is_the_calendar_day_in_production_timezone(self):
        item = self.item()
        self.log(self.item(MediaTypes.BOOK.value), self.friend("other"))
        self.log(item, self.friend("maya"), datetime(2026, 10, 14, tzinfo=UTC))
        self.assertEqual(self.only_friend(item)["consumed_at"], date(2026, 10, 14).isoformat())

    def test_queries_do_not_grow_with_logged_friends(self):
        item = self.item()
        self.log(item, self.friend("one"), rating=Decimal(8))
        with CaptureQueriesContext(connection) as one:
            self.get(item)
        for index in range(5):
            self.log(item, self.friend(f"more{index}"), rating=Decimal(8))
        with CaptureQueriesContext(connection) as six:
            self.assertEqual(self.get(item)["count"], 6)
        self.assertEqual(len(six), len(one))

    def test_a_heart_without_a_tracking_row_sorts_by_when_it_was_given(self):
        item = self.item()
        MediaLike.objects.create(user=self.friend("heart"), item=item, created_at=timezone.now() - timedelta(days=1))
        self.log(item, self.friend("older"), datetime(2020, 1, 1, tzinfo=UTC))
        names = [friend["user"]["username"] for friend in self.get(item)["results"]]
        self.assertEqual(names, ["heart", "older"])
