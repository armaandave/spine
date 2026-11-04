"""Likes stay on the day they were on, and go when the title goes (AC-7).

Both bugs of the feed shipped on Nov 2: one like split across two viewers' days, and an
old like coming back on a new save that day. The feed here is still the old reader.
"""

from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from api.services import progress_posts
from api.tests.test_activity_calc_books import BookCalcBase, d
from app import book_tracking, game_tracking, series_tracking
from app.models import Book, GameSession, Manga, MediaTypes, Status
from social.models import Comment, ContentLike, ProgressChange, ProgressPost, SocialAuditLog

NEW_YORK = "America/New_York"
TOKYO = "Asia/Tokyo"
FEED = "/api/v1/feed/"


@override_settings(TIME_ZONE=NEW_YORK)
class LikesBase(BookCalcBase):
    """A New York owner read by a follower from any zone."""

    def change(self, item):
        """Return the newest save on a title: the row a like reaches."""
        return ProgressChange.objects.filter(item=item, current_progress__kind="pages").latest("id")

    def like(self, change, zone=NEW_YORK, viewer=None):
        response = self.client_for(viewer or self.follower, zone).post(f"/api/v1/progress/{change.pk}/like/")
        self.assertEqual(response.status_code, 200, response.content)

    def comment(self, change, zone=NEW_YORK):
        response = self.client_for(self.follower, zone).post(
            f"/api/v1/progress/{change.pk}/comments/",
            {"body": "Nice"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)

    def feed_rows(self, zone=NEW_YORK, viewer=None):
        """Return (day, summary, likes, comments) of each progress row the viewer sees."""
        response = self.client_for(viewer or self.follower, zone).get(FEED)
        self.assertEqual(response.status_code, 200, response.content)
        return [
            (row["consumed_at"], row["object"]["summary"], row["like_count"], row["comment_count"])
            for row in response.json()["results"]
            if row["type"] == "progress_updated"
        ]


class OneDayForEveryViewerTests(LikesBase):
    """The first bug: the day came from the viewer's zone."""

    def test_noon_in_new_york_reads_two_dates_on_the_clock(self):
        """What makes the bug show: noon New York is the next morning's 02:00 in Tokyo."""
        item = self.book("noon")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 1))
        moment = self.change(item).created_at
        self.assertNotEqual(moment.astimezone(ZoneInfo(NEW_YORK)).date(), moment.astimezone(ZoneInfo(TOKYO)).date())

    def test_both_zones_read_one_row_with_both_likes(self):
        item = self.book("noon")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 1))
        change = self.change(item)
        self.like(change, zone=NEW_YORK)
        self.like(change, zone=TOKYO, viewer=self.stranger)
        self.comment(change, zone=NEW_YORK)
        wanted = [("2026-03-01", "read 40 pages", 2, 1)]
        self.assertEqual(self.feed_rows(zone=NEW_YORK), wanted)
        self.assertEqual(self.feed_rows(zone=TOKYO), wanted)
        self.assertEqual(ProgressPost.objects.count(), 1)

    def test_a_row_with_no_stored_day_reads_one_named_zone(self):
        item = self.book("old-row")
        self.begin(item, d(2026, 3, 1), at="23:30")
        self.save(item, 40, d(2026, 3, 1), at="23:30")
        ProgressChange.objects.filter(item=item).update(saved_on=None)
        change = self.change(item)
        self.like(change, zone=TOKYO, viewer=self.stranger)
        self.like(change, zone=NEW_YORK)
        wanted = [("2026-03-01", "read 40 pages", 2, 0)]
        self.assertEqual(self.feed_rows(zone=NEW_YORK), wanted)
        self.assertEqual(self.feed_rows(zone=TOKYO), wanted)

    def test_the_fixed_day_wins_over_the_moment(self):
        item = self.book("fixed")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        ProgressChange.objects.filter(item=item).update(saved_on=d(2026, 2, 27))
        self.assertEqual(self.feed_rows(), [("2026-02-27", "read 40 pages", 0, 0)])


class TheFeedIsStillTheOldOneTests(LikesBase):
    """What a fix does to a liked row: the rows change at Phase 4, not here."""

    def test_a_typo_fixed_later_still_reads_its_old_number_with_its_like(self):
        item = self.book("typo")
        self.begin(item, d(2025, 12, 20))
        self.save(item, 300, d(2025, 12, 28))
        self.like(self.change(item))
        self.save(item, 30, d(2026, 1, 2))
        self.assertEqual(self.feed_rows()[0], ("2025-12-28", "read 300 pages", 1, 0))
        self.assertNotIn("2026-01-02", [row[0] for row in self.feed_rows()])

    def test_a_drop_in_february_leaves_januarys_row_and_its_like(self):
        item = self.book("across")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 100, d(2025, 12, 15))
        self.save(item, 300, d(2026, 1, 15))
        self.like(self.change(item))
        self.save(item, 30, d(2026, 2, 15))
        self.assertIn(("2026-01-15", "read 200 pages", 1, 0), self.feed_rows())


class AReadDeletedTests(LikesBase):
    """The second bug: a like outlived what it was on and came back."""

    def read_with_a_like(self, name="back"):
        item = self.book(name)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        change = self.change(item)
        self.like(change)
        self.comment(change)
        return item

    def test_a_deleted_read_then_a_new_save_that_day_starts_at_nothing(self):
        item = self.read_with_a_like()
        with self.on(d(2026, 3, 2)):
            journey = Book.objects.get(user=self.owner, item=item).current_session_id
            book_tracking.delete_journey(self.owner, item, journey)
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.begin(item, d(2026, 3, 2))
        self.save(item, 10, d(2026, 3, 2))
        self.assertEqual(self.feed_rows()[0], ("2026-03-02", "read 10 pages", 0, 0))

    def test_each_post_removed_says_so_in_the_audit_log(self):
        item = self.read_with_a_like()
        with self.on(d(2026, 3, 2)):
            book_tracking.delete_journey(self.owner, item, Book.objects.get(item=item).current_session_id)
        entry = SocialAuditLog.objects.get(action="progress_post_removed")
        self.assertEqual(entry.actor, self.owner)
        self.assertEqual(entry.metadata, {"title_key": f"item:{item.pk}", "day": "2026-03-02"})

    def test_a_like_stays_when_another_read_holds_that_day(self):
        item = self.book("two")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        first = Book.objects.get(item=item).current_session_id
        self.like(self.change(item))
        with self.on(d(2026, 3, 2)):
            book_tracking.restart(self.owner, item)
        self.save(item, 10, d(2026, 3, 2))
        with self.on(d(2026, 3, 2)):
            book_tracking.delete_journey(self.owner, item, first)
        self.assertEqual(ContentLike.objects.count(), 1)
        self.assertEqual(self.feed_rows()[0], ("2026-03-02", "read 10 pages", 1, 0))

    def test_a_delete_never_sweeps_the_titles_other_days(self):
        item = self.book("sweep")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        stranded = ProgressPost.objects.create(actor=self.owner, title_key=f"item:{item.pk}", day=d(2026, 3, 3))
        self.like(self.change(item))
        with self.on(d(2026, 3, 2)):
            book_tracking.delete_journey(self.owner, item, Book.objects.get(item=item).current_session_id)
        self.assertEqual(list(ProgressPost.objects.values_list("pk", flat=True)), [stranded.pk])

    def test_a_game_and_a_manga_too(self):
        game = self.item(MediaTypes.GAME.value, "game")
        with self.on(d(2026, 3, 1)):
            game_tracking.start(self.owner, game)
            game_tracking.update_progress(self.owner, game, total_minutes=60)
        change = ProgressChange.objects.filter(item=game, current_progress__unit="minute").latest("id")
        self.like(change)
        session = GameSession.objects.get(related_game__user=self.owner)
        with self.on(d(2026, 3, 1)):
            game_tracking.delete_playthrough(self.owner, game, session.pk)
        self.assertEqual(ContentLike.objects.count(), 0)

        manga = self.item(MediaTypes.MANGA.value, "manga")
        with self.on(d(2026, 3, 1)):
            series_tracking.start(self.owner, manga)
            series_tracking.update_progress(self.owner, manga, value=5)
        change = ProgressChange.objects.filter(item=manga, current_progress__kind="count").latest("id")
        self.like(change)
        attempt = Manga.objects.get(user=self.owner, item=manga).current_attempt_id
        with self.on(d(2026, 3, 1)):
            series_tracking.delete_journey(self.owner, manga, attempt)
        self.assertEqual(ContentLike.objects.count(), 0)


class ATitleRemovedTests(LikesBase):
    """A tracking row gone: its saves, posts and likes go on commit."""

    def liked(self, name):
        item = self.book(name)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        self.like(self.change(item))
        return item

    def test_the_row_going_takes_the_title_with_it(self):
        item = self.liked("removed")
        with self.captureOnCommitCallbacks(execute=True):
            Book.objects.filter(user=self.owner, item=item).delete()
        self.assertEqual(ProgressChange.objects.filter(item=item).count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.assertEqual(ContentLike.objects.count(), 0)

    def test_someone_elses_row_on_that_title_stays(self):
        item = self.liked("shared")
        with self.on(d(2026, 3, 1)):
            book_tracking.start(self.follower, item)
        with self.captureOnCommitCallbacks(execute=True):
            Book.objects.filter(user=self.owner, item=item).delete()
        self.assertEqual(ProgressChange.objects.filter(item=item, actor=self.follower).count(), 1)

    def test_a_row_recreated_in_the_same_transaction_keeps_its_saves(self):
        """The receiver can't see an overwrite, which is why importers call the helper."""
        item = self.liked("recreated")
        with self.captureOnCommitCallbacks(execute=True):
            Book.objects.filter(user=self.owner, item=item).delete()
            Book.objects.create(user=self.owner, item=item, status=Status.PLANNING.value)
        self.assertEqual(ContentLike.objects.count(), 1)

    def test_deleting_the_person_queues_nothing(self):
        item = self.liked("person")
        with self.captureOnCommitCallbacks() as callbacks:
            self.owner.delete()
        self.assertEqual(callbacks, [])
        self.assertFalse(ProgressChange.objects.filter(item=item).exists())

    def test_a_show_keeps_its_likes_while_any_of_its_rows_is_left(self):
        show, season_item, tv, season = self.tv_show()
        change = ProgressChange.record_start(self.owner, show)
        post = ProgressPost.objects.create(actor=self.owner, title_key=f"tv:{show.source}:{show.media_id}", day=d(2026, 3, 1))
        with self.captureOnCommitCallbacks(execute=True):
            season.delete()
        self.assertTrue(ProgressPost.objects.filter(pk=post.pk).exists())
        with self.captureOnCommitCallbacks(execute=True):
            tv.delete()
        self.assertFalse(ProgressPost.objects.filter(pk=post.pk).exists())
        self.assertFalse(ProgressChange.objects.filter(pk=change.pk).exists())


class OverwriteTests(LikesBase):
    """An overwrite deletes and re-creates inside one transaction."""

    def test_an_overwrite_in_one_transaction_brings_no_like_back(self):
        item = self.book("overwrite")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        self.like(self.change(item))
        with transaction.atomic(), self.captureOnCommitCallbacks(execute=True):
            progress_posts.forget_titles(self.owner.pk, [item])
            Book.objects.filter(user=self.owner, item=item).delete()
            book_tracking.start(self.owner, item)
            book_tracking.update_progress(self.owner, item, progress_type="pages", value=10)
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(self.feed_rows()[0][1:], ("read 10 pages", 0, 0))

    def test_the_budget_50_titles_cost_what_5_cost(self):
        """Asserting the work ran: per row it's about 4,000 extra queries on 850 titles."""

        def seed(names):
            items = [self.book(name) for name in names]
            for item in items:
                book_tracking.start(self.owner, item)
                book_tracking.update_progress(self.owner, item, progress_type="pages", value=10)
                post = ProgressPost.objects.create(actor=self.owner, title_key=f"item:{item.pk}", day=d(2026, 3, 1))
                ContentLike.objects.create(user=self.follower, target_type=ContentLike.PROGRESS_POST, target_id=post.pk)
            return items

        def measure(items):
            with CaptureQueriesContext(connection) as queries:
                progress_posts.forget_titles(self.owner.pk, items)
            self.assertFalse(ProgressChange.objects.filter(item__in=items).exists())
            self.assertFalse(ProgressPost.objects.exists())
            self.assertFalse(ContentLike.objects.exists())
            return len(queries)

        few = measure(seed([f"few-{index}" for index in range(5)]))
        many = measure(seed([f"many-{index}" for index in range(50)]))
        self.assertEqual(few, many)

    def test_the_receiver_flushes_in_one_batch(self):
        with patch.object(progress_posts, "forget_titles", wraps=progress_posts.forget_titles) as forget:
            items = [self.book(f"batch-{index}") for index in range(20)]
            for item in items:
                Book.objects.create(user=self.owner, item=item, status=Status.PLANNING.value)
            with self.captureOnCommitCallbacks(execute=True):
                Book.objects.filter(user=self.owner).delete()
        self.assertEqual(forget.call_count, 1)
        self.assertEqual(len(forget.call_args.args[1]), 20)
