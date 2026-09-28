"""Independent sequence checks for game history and source restoration."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from app import game_tracking
from app.models import DiaryEntry, Game, Item, MediaLike, Status


class GameTrackingReviewTests(TestCase):
    """Exercise contract boundaries with independent mutation sequences."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="game-review")
        self.item = Item.objects.create(
            source="manual", media_type="game", media_id="game-review", title="Review game",
        )
        self.today = timezone.localdate()

    def game(self):
        return Game.objects.get(user=self.user, item=self.item)

    def test_percentage_clear_decouples_only_percentage_and_log_clear_keeps_hours_link(self):
        game_tracking.start(self.user, self.item)
        _, log = game_tracking.complete(
            self.user, self.item, completion_date=self.today,
            total_minutes=600, percentage=65,
        )
        game_tracking.update_progress(self.user, self.item, percentage=None)
        game_tracking.update_completion(log, {"total_minutes": None, "percentage": 0})
        current = self.game().current_session
        log.refresh_from_db()
        self.assertIsNone(current.total_minutes)
        self.assertIsNone(current.percentage)
        self.assertEqual(log.progress_snapshot, {"total_minutes": None, "percentage": 0})
        game_tracking.update_completion(log, {"total_minutes": 0, "percentage": 40})
        current.refresh_from_db()
        self.assertEqual(current.total_minutes, 0)
        self.assertIsNone(current.percentage)

    def test_delete_direct_completion_restores_direct_paused_without_a_playthrough(self):
        game_tracking.assign_status(self.user, self.item, Status.PAUSED.value)
        _, log = game_tracking.complete(
            self.user, self.item, completion_date=self.today, total_minutes=600,
        )
        game_tracking.update_progress(self.user, self.item, total_minutes=700)
        game_tracking.delete_completion(self.user, log)
        game = self.game()
        self.assertEqual(game.status, Status.PAUSED.value)
        self.assertIsNone(game.current_session_id)
        self.assertFalse(game.playthroughs.exists())
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=self.item).exists())

    def test_deleted_dropped_attempt_cannot_return_after_newer_attempt_is_deleted(self):
        game_tracking.assign_status(self.user, self.item, Status.PLANNING.value)
        first = game_tracking.start(self.user, self.item).current_session
        game_tracking.drop(self.user, self.item)
        newer = game_tracking.start(self.user, self.item).current_session
        game_tracking.delete_playthrough(self.user, self.item, first.pk)
        self.assertEqual(self.game().current_session_id, newer.pk)
        game_tracking.delete_playthrough(self.user, self.item, newer.pk)
        game = self.game()
        self.assertEqual(game.status, Status.PLANNING.value)
        self.assertIsNone(game.current_session_id)
        self.assertFalse(game.playthroughs.exists())

    def test_source_deletion_preserves_both_opinions_and_new_log_recouples_them(self):
        game_tracking.start(self.user, self.item)
        _, original = game_tracking.complete(
            self.user, self.item, completion_date=self.today, rating=8, liked=True,
        )
        game_tracking.delete_completion(self.user, original)
        game = self.game()
        self.assertEqual(game.score, 8)
        self.assertIsNone(game.rating_source_id)
        self.assertIsNone(game.like_source_id)
        self.assertTrue(game.like_is_independent)
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=self.item).exists())
        _, new_log = game_tracking.complete(
            self.user, self.item, completion_date=self.today, rating=6, liked=False,
        )
        game = self.game()
        self.assertEqual(game.rating_source_id, new_log.pk)
        self.assertEqual(game.like_source_id, new_log.pk)
        game_tracking.update_completion(new_log, {"rating": 10, "liked": True})
        game = self.game()
        self.assertEqual(game.score, 10)
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=self.item).exists())

    def test_backdate_preserves_later_zero_and_unknown_fields_independently(self):
        start = self.today - timedelta(days=4)
        game_tracking.start(self.user, self.item, start_date=start)
        game_tracking.update_progress(self.user, self.item, total_minutes=0, percentage=None)
        _, log = game_tracking.complete(
            self.user, self.item, completion_date=start + timedelta(days=1),
            total_minutes=600, percentage=65,
        )
        game_tracking.update_completion(log, {"total_minutes": 700, "percentage": 70})
        current = self.game().current_session
        self.assertEqual(current.total_minutes, 0)
        self.assertIsNone(current.percentage)
        self.assertEqual(log.progress_snapshot, {"total_minutes": 700, "percentage": 70})

    def test_drop_date_correction_has_start_limit_without_changing_newer_progress(self):
        start = self.today - timedelta(days=4)
        old = game_tracking.start(self.user, self.item, start_date=start).current_session
        game_tracking.drop(self.user, self.item)
        newer = game_tracking.start(self.user, self.item).current_session
        game_tracking.update_progress(self.user, self.item, total_minutes=70)
        with self.assertRaises(ValidationError):
            game_tracking.update_playthrough(
                self.user, self.item, old.pk, end_date=start - timedelta(days=1),
                total_minutes=999,
            )
        game_tracking.update_playthrough(
            self.user, self.item, old.pk, end_date=start + timedelta(days=1),
            total_minutes=0, percentage=None,
        )
        game = self.game()
        old.refresh_from_db()
        self.assertEqual(game.current_session_id, newer.pk)
        self.assertEqual(game.current_session.total_minutes, 70)
        self.assertEqual(old.status, Status.DROPPED.value)
        self.assertEqual(old.total_minutes, 0)
        self.assertEqual(old.end_date, start + timedelta(days=1))

    def test_repeated_drop_preserves_the_current_dropped_attempt_and_progress(self):
        game_tracking.start(self.user, self.item)
        game_tracking.update_progress(self.user, self.item, total_minutes=600, percentage=65)
        dropped = game_tracking.drop(self.user, self.item)
        session_id = dropped.current_session_id
        previous_history = dropped.status_history
        game_tracking.drop(self.user, self.item)
        game_tracking.assign_status(self.user, self.item, Status.DROPPED.value)
        game = self.game()
        self.assertEqual(game.current_session_id, session_id)
        self.assertEqual(game.current_session.total_minutes, 600)
        self.assertEqual(game.current_session.percentage, 65)
        self.assertEqual(game.status_history, previous_history)
        self.assertEqual(game.playthroughs.count(), 1)
