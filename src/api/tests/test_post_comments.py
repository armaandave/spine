"""Likes and comments on a log or a day's progress (activity contract AC-7)."""

from datetime import UTC, datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.test import APIClient

from app.models import DiaryEntry, Item, MediaTypes, Sources
from social.models import (
    Block,
    Comment,
    ContentLike,
    Follow,
    FollowStatus,
    ProgressChange,
    ProgressPost,
    SocialAuditLog,
)


def at(day, hour=12):
    """Return a UTC time in October 2026."""
    return datetime(2026, 10, day, hour, tzinfo=UTC)


class PostTestCase(TestCase):
    """A person with posts, someone who follows them, and someone who doesn't."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="owner", password="strong-password-123")
        self.friend = User.objects.create_user(username="friend", password="strong-password-123")
        self.stranger = User.objects.create_user(username="stranger", password="strong-password-123")
        Follow.objects.create(from_user=self.friend, to_user=self.owner, status=FollowStatus.ACCEPTED)
        self.client = APIClient()
        self.movie = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.MOVIE.value,
            media_id="1",
            title="Past Lives",
        )
        self.season = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            media_id="2",
            season_number=1,
            title="Severance",
        )
        self.show = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id="2",
            title="Severance",
        )

    def log(self, **fields):
        return DiaryEntry.objects.create(user=self.owner, item=self.movie, consumed_at=at(24), **fields)

    def progress(self, previous=0, current=4, when=None, item=None):
        change = ProgressChange.objects.create(
            actor=self.owner,
            item=item or self.season,
            previous_progress={"kind": "episodes", "value": previous, "max": 10, "unit": "episode"},
            current_progress={"kind": "episodes", "value": current, "max": 10, "unit": "episode"},
        )
        ProgressChange.objects.filter(pk=change.pk).update(created_at=when or at(24))
        return change

    def as_user(self, user):
        self.client.force_authenticate(user)
        return self.client

    def feed(self, user=None):
        response = self.as_user(user or self.friend).get("/api/v1/feed/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["results"]


class FeedPostFieldsTests(PostTestCase):
    """The feed carries what a post's card shows."""

    def test_a_log_brings_its_review_and_counts(self):
        entry = self.log(review="Wrecked me.", review_title="Quiet", contains_spoilers=True, liked=True, rating=Decimal(9))
        ContentLike.objects.create(user=self.friend, target_type=ContentLike.DIARY_ENTRY, target_id=entry.id)
        ContentLike.objects.create(user=self.stranger, target_type=ContentLike.DIARY_ENTRY, target_id=entry.id)
        Comment.objects.create(user=self.stranger, diary_entry=entry, body="First")

        [activity] = self.feed()

        self.assertEqual(activity["review"], "Wrecked me.")
        self.assertEqual(activity["review_title"], "Quiet")
        self.assertTrue(activity["contains_spoilers"])
        self.assertEqual(activity["like_count"], 2)
        self.assertEqual(activity["comment_count"], 1)
        self.assertTrue(activity["viewer"]["has_liked"])
        # The owner's own heart on the title is a different thing.
        self.assertTrue(activity["object"]["liked"])

    def test_a_day_with_no_reaction_has_zero_counts_and_no_saved_post(self):
        self.progress()

        [activity] = self.feed()

        self.assertEqual(activity["like_count"], 0)
        self.assertEqual(activity["comment_count"], 0)
        self.assertFalse(activity["viewer"]["has_liked"])
        self.assertFalse(ProgressPost.objects.exists())

    def test_counting_more_posts_takes_no_more_queries(self):
        for day in range(20, 23):
            entry = self.log()
            Comment.objects.create(user=self.friend, diary_entry=entry, body="Hi")
            change = self.progress(when=at(day))
            self.as_user(self.friend).post(f"/api/v1/progress/{change.id}/like/")
        with CaptureQueriesContext(connection) as few_posts:
            self.assertEqual(len(self.feed()), 6)

        for day in range(23, 28):
            entry = self.log()
            Comment.objects.create(user=self.friend, diary_entry=entry, body="Hi")
            change = self.progress(when=at(day))
            self.as_user(self.friend).post(f"/api/v1/progress/{change.id}/like/")
        with CaptureQueriesContext(connection) as many_posts:
            self.assertEqual(len(self.feed()), 16)

        self.assertEqual(len(many_posts), len(few_posts))


class CommentTests(PostTestCase):
    """Comments on a log, and who can see or delete them."""

    def test_commenting_on_a_log_counts_on_the_log_and_the_feed(self):
        entry = self.log()

        response = self.as_user(self.friend).post(f"/api/v1/diary/{entry.id}/comments/", {"body": "  Finally!  "})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["body"], "Finally!")
        self.assertEqual(response.data["user"]["username"], "friend")
        self.assertTrue(response.data["can_delete"])
        self.assertEqual(self.feed()[0]["comment_count"], 1)
        detail = self.client.get(f"/api/v1/diary/{entry.id}/")
        self.assertEqual(detail.data["comment_count"], 1)
        self.assertTrue(SocialAuditLog.objects.filter(actor=self.friend, action="comment", target_id=entry.id).exists())

    def test_comments_list_oldest_first(self):
        entry = self.log()
        first = Comment.objects.create(user=self.friend, diary_entry=entry, body="One")
        second = Comment.objects.create(user=self.owner, diary_entry=entry, body="Two")

        response = self.as_user(self.friend).get(f"/api/v1/diary/{entry.id}/comments/")

        self.assertEqual([comment["id"] for comment in response.data["results"]], [first.id, second.id])

    def test_an_empty_comment_is_refused(self):
        entry = self.log()

        response = self.as_user(self.friend).post(f"/api/v1/diary/{entry.id}/comments/", {"body": "   "})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.exists())

    def test_a_private_log_cant_be_read_or_commented_on_by_others(self):
        # A TV log, because movie, album, book and game logs are always public.
        entry = DiaryEntry.objects.create(user=self.owner, item=self.show, consumed_at=at(24), visibility="private")

        self.assertEqual(
            self.as_user(self.friend).get(f"/api/v1/diary/{entry.id}/comments/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.post(f"/api/v1/diary/{entry.id}/comments/", {"body": "Hi"}).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_a_followers_log_needs_a_follow(self):
        entry = DiaryEntry.objects.create(user=self.owner, item=self.show, consumed_at=at(24), visibility="followers")

        self.assertEqual(
            self.as_user(self.friend).post(f"/api/v1/diary/{entry.id}/comments/", {"body": "Hi"}).status_code,
            status.HTTP_201_CREATED,
        )
        self.assertEqual(
            self.as_user(self.stranger).post(f"/api/v1/diary/{entry.id}/comments/", {"body": "Hi"}).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_a_private_account_shuts_out_people_who_dont_follow_it(self):
        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])
        entry = self.log()
        change = self.progress()

        for path in (f"/api/v1/diary/{entry.id}/comments/", f"/api/v1/progress/{change.id}/comments/"):
            self.assertEqual(self.as_user(self.stranger).get(path).status_code, status.HTTP_404_NOT_FOUND)
            self.assertEqual(self.as_user(self.friend).get(path).status_code, status.HTTP_200_OK)

    def test_a_block_either_way_hides_that_persons_comments(self):
        entry = self.log()
        Comment.objects.create(user=self.stranger, diary_entry=entry, body="Hidden")
        Comment.objects.create(user=self.owner, diary_entry=entry, body="Shown")
        Block.objects.create(blocker=self.friend, blocked=self.stranger)

        response = self.as_user(self.friend).get(f"/api/v1/diary/{entry.id}/comments/")

        self.assertEqual([comment["body"] for comment in response.data["results"]], ["Shown"])
        self.assertEqual(self.feed()[0]["comment_count"], 1)

        Block.objects.filter(blocker=self.friend).delete()
        Block.objects.create(blocker=self.stranger, blocked=self.friend)
        response = self.client.get(f"/api/v1/diary/{entry.id}/comments/")
        self.assertEqual([comment["body"] for comment in response.data["results"]], ["Shown"])

    def test_the_author_the_posts_owner_and_admins_can_delete(self):
        entry = self.log()
        admin = get_user_model().objects.create_user(username="admin", password="strong-password-123", is_staff=True)

        for deleter in (self.friend, self.owner, admin):
            comment = Comment.objects.create(user=self.friend, diary_entry=entry, body="Bye")
            response = self.as_user(deleter).delete(f"/api/v1/comments/{comment.id}/")
            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
            self.assertFalse(Comment.objects.filter(pk=comment.pk).exists())
        self.assertEqual(SocialAuditLog.objects.filter(action="comment_delete", target_user=self.friend).count(), 3)

    def test_anyone_else_cant_delete_a_comment(self):
        comment = Comment.objects.create(user=self.friend, diary_entry=self.log(), body="Mine")

        response = self.as_user(self.stranger).delete(f"/api/v1/comments/{comment.id}/")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Comment.objects.filter(pk=comment.pk).exists())

    def test_only_those_who_can_delete_are_told_they_can(self):
        entry = self.log()
        Comment.objects.create(user=self.friend, diary_entry=entry, body="Mine")

        [as_author] = self.as_user(self.friend).get(f"/api/v1/diary/{entry.id}/comments/").data["results"]
        [as_owner] = self.as_user(self.owner).get(f"/api/v1/diary/{entry.id}/comments/").data["results"]
        [as_other] = self.as_user(self.stranger).get(f"/api/v1/diary/{entry.id}/comments/").data["results"]

        self.assertTrue(as_author["can_delete"])
        self.assertTrue(as_owner["can_delete"])
        self.assertFalse(as_other["can_delete"])

    def test_deleting_a_log_deletes_its_comments(self):
        entry = self.log()
        Comment.objects.create(user=self.friend, diary_entry=entry, body="Gone with it")

        entry.delete()

        self.assertFalse(Comment.objects.exists())

    def test_a_comment_belongs_to_exactly_one_post(self):
        entry = self.log()
        post = ProgressPost.objects.create(actor=self.owner, title_key="item:1", day=at(24).date())

        for targets in ({}, {"diary_entry": entry, "progress_post": post}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Comment.objects.create(user=self.friend, body="Nowhere", **targets)


class ReplyTests(PostTestCase):
    """Replies: one level deep, counted, and kept readable when a comment goes."""

    def reply(self, user, entry, parent, body="Reply"):
        return self.as_user(user).post(
            f"/api/v1/diary/{entry.id}/comments/",
            {"body": body, "parent_id": parent.id},
        )

    def listed(self, entry, user=None):
        return self.as_user(user or self.friend).get(f"/api/v1/diary/{entry.id}/comments/").data["results"]

    def test_a_reply_points_at_its_comment_and_counts(self):
        entry = self.log()
        comment = Comment.objects.create(user=self.owner, diary_entry=entry, body="Thoughts?")

        response = self.reply(self.friend, entry, comment)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["parent_id"], comment.id)
        self.assertFalse(response.data["is_deleted"])
        self.assertEqual(self.feed()[0]["comment_count"], 2)
        self.assertEqual(self.client.get(f"/api/v1/diary/{entry.id}/").data["comment_count"], 2)

    def test_a_reply_to_a_reply_joins_the_same_thread(self):
        entry = self.log()
        comment = Comment.objects.create(user=self.owner, diary_entry=entry, body="Thoughts?")
        first = Comment.objects.create(user=self.friend, diary_entry=entry, parent=comment, body="Loved it")

        response = self.reply(self.owner, entry, first, "@friend same")

        self.assertEqual(response.data["parent_id"], comment.id)

    def test_replies_list_right_after_their_comment(self):
        entry = self.log()
        first = Comment.objects.create(user=self.owner, diary_entry=entry, body="One")
        second = Comment.objects.create(user=self.friend, diary_entry=entry, body="Two")
        reply = Comment.objects.create(user=self.friend, diary_entry=entry, parent=first, body="Re one")

        ids = [comment["id"] for comment in self.listed(entry)]

        self.assertEqual(ids, [first.id, reply.id, second.id])

    def test_a_reply_must_be_to_a_comment_on_the_same_post(self):
        entry = self.log()
        elsewhere = Comment.objects.create(user=self.owner, diary_entry=self.log(), body="Other log")

        response = self.reply(self.friend, entry, elsewhere)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Comment.objects.filter(diary_entry=entry).exists())

    def test_a_reply_on_a_days_progress(self):
        change = self.progress()
        self.as_user(self.owner)
        comment = self.client.post(f"/api/v1/progress/{change.id}/comments/", {"body": "Four!"}).data

        response = self.as_user(self.friend).post(
            f"/api/v1/progress/{change.id}/comments/",
            {"body": "Nice", "parent_id": comment["id"]},
        )

        self.assertEqual(response.data["parent_id"], comment["id"])
        self.assertEqual(self.feed()[0]["comment_count"], 2)

    def test_deleting_a_comment_with_replies_leaves_a_placeholder(self):
        entry = self.log()
        comment = Comment.objects.create(user=self.friend, diary_entry=entry, body="Secret take")
        Comment.objects.create(user=self.owner, diary_entry=entry, parent=comment, body="Reply")

        response = self.as_user(self.friend).delete(f"/api/v1/comments/{comment.id}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        placeholder, reply = self.listed(entry)
        self.assertTrue(placeholder["is_deleted"])
        self.assertEqual(placeholder["body"], "")
        self.assertFalse(placeholder["can_delete"])
        self.assertEqual(reply["parent_id"], comment.id)
        self.assertEqual(self.feed()[0]["comment_count"], 1)
        # It's already gone, so it can't be deleted again or replied to.
        self.assertEqual(self.client.delete(f"/api/v1/comments/{comment.id}/").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.reply(self.friend, entry, comment).status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_reply_to_a_reply_under_a_deleted_comment_still_joins_the_thread(self):
        entry = self.log()
        comment = Comment.objects.create(user=self.friend, diary_entry=entry, body="Take")
        reply = Comment.objects.create(user=self.owner, diary_entry=entry, parent=comment, body="Reply")
        self.as_user(self.friend).delete(f"/api/v1/comments/{comment.id}/")

        response = self.reply(self.friend, entry, reply, "@owner fair")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["parent_id"], comment.id)

    def test_the_last_reply_going_takes_the_placeholder_with_it(self):
        entry = self.log()
        comment = Comment.objects.create(user=self.friend, diary_entry=entry, body="Take")
        reply = Comment.objects.create(user=self.owner, diary_entry=entry, parent=comment, body="Reply")
        self.as_user(self.friend).delete(f"/api/v1/comments/{comment.id}/")

        self.as_user(self.owner).delete(f"/api/v1/comments/{reply.id}/")

        self.assertFalse(Comment.objects.exists())

    def test_deleting_a_reply_keeps_a_live_comment(self):
        entry = self.log()
        comment = Comment.objects.create(user=self.owner, diary_entry=entry, body="Stays")
        reply = Comment.objects.create(user=self.friend, diary_entry=entry, parent=comment, body="Goes")

        self.as_user(self.friend).delete(f"/api/v1/comments/{reply.id}/")

        self.assertEqual(list(Comment.objects.values_list("body", flat=True)), ["Stays"])

    def test_a_block_hides_replies_under_that_persons_comment(self):
        entry = self.log()
        hidden = Comment.objects.create(user=self.stranger, diary_entry=entry, body="Hidden")
        Comment.objects.create(user=self.owner, diary_entry=entry, parent=hidden, body="Under hidden")
        Block.objects.create(blocker=self.friend, blocked=self.stranger)

        self.assertEqual(self.listed(entry), [])
        self.assertEqual(self.feed()[0]["comment_count"], 0)


class ProgressPostTests(PostTestCase):
    """A day's progress as a post: its page, likes, and comments."""

    def test_a_day_opens_as_its_own_post_for_a_viewer(self):
        change = self.progress()

        response = self.as_user(self.friend).get(f"/api/v1/progress/{change.id}/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["type"], "progress_updated")
        self.assertEqual(response.data["id"], -change.id)
        self.assertEqual(response.data["actor"]["username"], "owner")
        self.assertEqual(response.data["media"]["ref"]["media_type"], "tv")
        self.assertEqual(response.data["object"]["summary"], "watched 4 episodes")
        self.assertFalse(ProgressPost.objects.exists(), "reading saves nothing")

    def test_a_like_counts_on_the_feed_and_can_be_undone(self):
        change = self.progress()
        self.as_user(self.friend)

        liked = self.client.post(f"/api/v1/progress/{change.id}/like/")

        self.assertEqual(liked.data, {"liked": True, "like_count": 1})
        [activity] = self.feed()
        self.assertEqual(activity["like_count"], 1)
        self.assertTrue(activity["viewer"]["has_liked"])
        self.assertTrue(SocialAuditLog.objects.filter(actor=self.friend, action="like").exists())

        unliked = self.client.delete(f"/api/v1/progress/{change.id}/like/")

        self.assertEqual(unliked.data, {"liked": False, "like_count": 0})
        self.assertFalse(self.feed()[0]["viewer"]["has_liked"])

    def test_a_comment_counts_on_the_feed(self):
        change = self.progress()

        response = self.as_user(self.friend).post(f"/api/v1/progress/{change.id}/comments/", {"body": "Four!"})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.feed()[0]["comment_count"], 1)
        listed = self.client.get(f"/api/v1/progress/{change.id}/comments/")
        self.assertEqual([comment["body"] for comment in listed.data["results"]], ["Four!"])
        self.assertTrue(listed.data["results"][0]["can_delete"])

    def test_more_progress_that_day_keeps_likes_and_comments(self):
        first = self.progress(0, 2, at(24, 9))
        self.as_user(self.friend).post(f"/api/v1/progress/{first.id}/like/")
        self.client.post(f"/api/v1/progress/{first.id}/comments/", {"body": "Early"})
        self.progress(2, 5, at(24, 21))

        [activity] = self.feed()

        self.assertEqual(activity["object"]["summary"], "watched 5 episodes")
        self.assertEqual(activity["like_count"], 1)
        self.assertEqual(activity["comment_count"], 1)

    def test_any_change_that_day_finds_the_same_post(self):
        first = self.progress(0, 2, at(24, 9))
        later = self.progress(2, 5, at(24, 21))

        self.as_user(self.friend).post(f"/api/v1/progress/{first.id}/like/")
        self.client.post(f"/api/v1/progress/{later.id}/comments/", {"body": "Same post"})

        self.assertEqual(ProgressPost.objects.count(), 1)

    def test_another_day_or_title_is_another_post(self):
        today = self.progress(0, 2, at(24))
        tomorrow = self.progress(2, 3, at(25))
        book = Item.objects.create(source=Sources.MANUAL.value, media_type=MediaTypes.BOOK.value, media_id="9", title="Book")
        other = ProgressChange.objects.create(
            actor=self.owner,
            item=book,
            previous_progress={"kind": "pages", "value": 0, "max": 100, "unit": "page"},
            current_progress={"kind": "pages", "value": 10, "max": 100, "unit": "page"},
        )
        ProgressChange.objects.filter(pk=other.pk).update(created_at=at(24))

        self.as_user(self.friend).post(f"/api/v1/progress/{today.id}/like/")

        likes = {activity["object"]["id"]: activity["like_count"] for activity in self.feed()}
        self.assertEqual(likes, {today.id: 1, tomorrow.id: 0, other.id: 0})

    def test_logging_that_day_keeps_the_post_and_its_reactions(self):
        change = self.progress()
        self.as_user(self.friend).post(f"/api/v1/progress/{change.id}/comments/", {"body": "How is it?"})
        DiaryEntry.objects.create(user=self.owner, item=self.show, consumed_at=at(24))

        progress = [activity for activity in self.feed() if activity["type"] == "progress_updated"]

        self.assertEqual([activity["comment_count"] for activity in progress], [1])

    def test_a_day_that_netted_to_nothing_isnt_a_post(self):
        change = self.progress(1, 3, at(24, 9))
        self.progress(3, 1, at(24, 10))

        for request in (
            self.as_user(self.friend).get(f"/api/v1/progress/{change.id}/"),
            self.client.post(f"/api/v1/progress/{change.id}/like/"),
            self.client.post(f"/api/v1/progress/{change.id}/comments/", {"body": "Hi"}),
        ):
            self.assertEqual(request.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(ProgressPost.objects.exists())

    def test_a_block_or_private_account_hides_the_post(self):
        change = self.progress()
        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])

        self.assertEqual(
            self.as_user(self.stranger).get(f"/api/v1/progress/{change.id}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.post(f"/api/v1/progress/{change.id}/like/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

        Block.objects.create(blocker=self.owner, blocked=self.friend)
        self.assertEqual(
            self.as_user(self.friend).get(f"/api/v1/progress/{change.id}/").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_the_owner_can_delete_a_comment_on_their_progress(self):
        change = self.progress()
        comment = self.as_user(self.friend).post(f"/api/v1/progress/{change.id}/comments/", {"body": "Hi"}).data

        response = self.as_user(self.owner).delete(f"/api/v1/comments/{comment['id']}/")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.feed()[0]["comment_count"], 0)

    def test_liking_someone_elses_saved_post_by_the_generic_endpoint_isnt_offered(self):
        response = self.as_user(self.friend).post(
            "/api/v1/social/likes/",
            {"target_type": "progress", "target_id": 1},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(TIME_ZONE="America/New_York")
    def test_the_post_is_on_the_local_day(self):
        # 02:00 UTC on the 25th is still the evening of the 24th in New York.
        evening = self.progress(0, 1, datetime(2026, 10, 25, 2, tzinfo=UTC))
        same_evening = self.progress(1, 2, datetime(2026, 10, 25, 3, tzinfo=UTC))
        next_morning = self.progress(2, 3, datetime(2026, 10, 25, 15, tzinfo=UTC))

        self.as_user(self.friend).post(f"/api/v1/progress/{evening.id}/like/")
        self.client.post(f"/api/v1/progress/{same_evening.id}/like/")
        self.assertEqual(self.client.get(f"/api/v1/progress/{evening.id}/").data["consumed_at"], "2026-10-24")

        post = ProgressPost.objects.get()
        self.assertEqual(post.day.isoformat(), "2026-10-24")
        likes = {activity["consumed_at"]: activity["like_count"] for activity in self.feed()}
        self.assertEqual(likes, {"2026-10-25": 0, "2026-10-24": 1})
        self.assertEqual(self.client.get(f"/api/v1/progress/{next_morning.id}/").data["consumed_at"], "2026-10-25")
