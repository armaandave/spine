"""Logs as activity, newest log date first, on profiles, Home and the feed.

Besides a day's progress (test_activity_progress), nothing else is activity: not
ratings without a log, marking something watched, status changes, or list adds.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from app import single_weight
from app.models import DiaryEntry, Item, MediaTypes, Sources
from social.models import Activity, Block, ContentLike, Follow, FollowStatus


class ActivityIsLogsTests(TestCase):
    """The activity endpoints return logs and only logs."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="logger", password="strong-password-123")
        self.viewer = User.objects.create_user(username="viewer", password="strong-password-123")
        self.client = APIClient()

    def item(self, media_type=MediaTypes.MUSIC.value, media_id="one"):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=f"{media_type}-{media_id}",
            title=f"{media_type} {media_id}",
            image="https://example.com/poster.jpg",
        )

    def log(self, item, consumed_at, user=None, **fields):
        return DiaryEntry.objects.create(
            user=user or self.owner,
            item=item,
            consumed_at=consumed_at,
            **fields,
        )

    def activity(self, username="logger", viewer=None):
        self.client.force_authenticate(viewer or self.owner)
        response = self.client.get(f"/api/v1/users/{username}/activity/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["results"]

    def ids(self, results):
        return [activity["object"]["id"] for activity in results]

    def follow(self, follower, followed):
        Follow.objects.create(from_user=follower, to_user=followed, status=FollowStatus.ACCEPTED)

    def test_newest_log_date_comes_first_not_newest_posted(self):
        recent = self.log(self.item(media_id="recent"), datetime(2026, 10, 24, tzinfo=UTC))
        # A backfilled log, posted after the recent one, for an album from years ago.
        backfilled = self.log(self.item(media_id="backfilled"), datetime(2019, 5, 4, tzinfo=UTC))
        same_day_first = self.log(self.item(media_id="same-day-1"), datetime(2026, 10, 25, tzinfo=UTC))
        same_day_second = self.log(self.item(media_id="same-day-2"), datetime(2026, 10, 25, tzinfo=UTC))

        self.assertEqual(
            self.ids(self.activity()),
            [same_day_second.id, same_day_first.id, recent.id, backfilled.id],
        )

    def test_ratings_watched_marks_and_list_adds_are_not_activity(self):
        rated = self.item(media_id="rated")
        watched = self.item(media_type=MediaTypes.MOVIE.value, media_id="watched")
        single_weight.set_rating(self.owner, rated, Decimal(8))
        single_weight.mark_consumed(self.owner, watched)
        Activity.objects.create(
            actor=self.owner,
            verb="list_item_added",
            target_type=ContentLike.CUSTOM_LIST,
            target_id=1,
            item=watched,
            snapshot={"list_name": "Favorites"},
        )
        self.assertTrue(Activity.objects.filter(actor=self.owner, verb="rating_updated").exists())

        self.assertEqual(self.activity(), [])

        entry = self.log(watched, datetime(2026, 10, 1, tzinfo=UTC))
        self.assertEqual(self.ids(self.activity()), [entry.id])

    def test_a_log_is_one_item_in_the_shape_installed_apps_read(self):
        entry = self.log(
            self.item(),
            single_weight.calendar_datetime(date(2019, 5, 4)),
            rating=Decimal(9),
            liked=True,
        )
        ContentLike.objects.create(user=self.viewer, target_type=ContentLike.DIARY_ENTRY, target_id=entry.id)

        [activity] = self.activity(viewer=self.viewer)

        self.assertEqual(activity["id"], entry.id)
        self.assertEqual(activity["type"], "diary_created")
        self.assertEqual(activity["consumed_at"], "2019-05-04")
        self.assertEqual(activity["created_at"], entry.created_at)
        self.assertEqual(activity["actor"]["username"], "logger")
        self.assertEqual(activity["media"]["title"], "music one")
        self.assertIsNone(activity["person"])
        self.assertEqual(
            activity["object"],
            {"type": "diary", "id": entry.id, "rating": "4.5", "liked": True, "is_rewatch": False},
        )
        self.assertEqual(activity["viewer"], {"can_view": True, "has_liked": True})

    def test_a_rewatch_log_says_so(self):
        entry = self.log(self.item(), datetime(2026, 10, 1, tzinfo=UTC), is_rewatch=True)

        [activity] = self.activity()

        self.assertEqual(activity["id"], entry.id)
        self.assertTrue(activity["object"]["is_rewatch"])

    def test_imported_logs_are_activity_at_their_own_date(self):
        recent = self.log(self.item(media_id="recent"), datetime(2026, 10, 24, tzinfo=UTC))
        imported = self.log(
            self.item(media_id="imported"),
            datetime(2018, 3, 1, tzinfo=UTC),
            import_source="musicboard",
            import_source_id="1",
        )

        self.assertEqual(self.ids(self.activity()), [recent.id, imported.id])

    def test_other_viewers_see_logs_their_visibility_allows(self):
        anime = self.item(media_type=MediaTypes.ANIME.value)
        day = datetime(2026, 10, 1, tzinfo=UTC)
        public = self.log(anime, day, visibility="public")
        followers_only = self.log(anime, day, visibility="followers")
        self.log(anime, day, visibility="private")
        # Half-star media logs are always public.
        album = self.log(self.item(), day, visibility="followers")

        self.assertEqual(self.ids(self.activity(viewer=self.viewer)), [album.id, public.id])
        self.follow(self.viewer, self.owner)
        self.assertEqual(
            self.ids(self.activity(viewer=self.viewer)),
            [album.id, followers_only.id, public.id],
        )
        self.assertEqual(len(self.activity()), 4)

    def test_private_and_blocked_profiles_show_no_activity(self):
        self.log(self.item(), datetime(2026, 10, 1, tzinfo=UTC))
        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])
        self.assertEqual(self.activity(viewer=self.viewer), [])

        self.owner.profile_private = False
        self.owner.save(update_fields=["profile_private"])
        Block.objects.create(blocker=self.owner, blocked=self.viewer)
        self.assertEqual(self.activity(viewer=self.viewer), [])

    def test_feed_is_followed_users_logs_newest_log_date_first(self):
        stranger = get_user_model().objects.create_user(username="stranger", password="strong-password-123")
        self.follow(self.viewer, self.owner)
        old = self.log(self.item(media_id="old"), datetime(2019, 1, 1, tzinfo=UTC))
        new = self.log(self.item(media_id="new"), datetime(2026, 10, 1, tzinfo=UTC))
        movie = self.log(
            self.item(media_type=MediaTypes.MOVIE.value),
            datetime(2026, 9, 1, tzinfo=UTC),
        )
        self.log(self.item(media_id="own"), datetime(2026, 10, 2, tzinfo=UTC), user=self.viewer)
        self.log(self.item(media_id="stranger"), datetime(2026, 10, 3, tzinfo=UTC), user=stranger)
        self.client.force_authenticate(self.viewer)

        feed = self.client.get("/api/v1/feed/")
        music = self.client.get("/api/v1/feed/", {"media_type": "music"})

        self.assertEqual(feed.status_code, status.HTTP_200_OK)
        self.assertEqual(self.ids(feed.data["results"]), [new.id, movie.id, old.id])
        self.assertEqual(self.ids(music.data["results"]), [new.id, old.id])
