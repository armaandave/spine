from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from app import game_tracking, views
from app.models import DiaryEntry, Game, Item, MediaTypes, Sources, Status


class GameTrackingWebTests(TestCase):
    """Existing web URLs must obey the same game laws as native API routes."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="game-web")
        self.client.force_login(self.user)
        self.item = Item.objects.create(
            media_id="game-web", source=Sources.MANUAL.value,
            media_type=MediaTypes.GAME.value, title="Web game", image="",
        )
        metadata = {"media_id": self.item.media_id, "source": "manual",
                    "media_type": "game", "title": self.item.title,
                    "image": "", "max_progress": None}
        self.provider = patch("app.providers.services.get_media_metadata", return_value=metadata)
        self.provider.start()
        self.addCleanup(self.provider.stop)

    def action(self, name):
        args = [self.item.source, self.item.media_id]
        if name in {"pause_media", "resume_media", "drop_media"}:
            args.insert(1, "game")
        return self.client.post(reverse(name, args=args), HTTP_HX_REQUEST="true")

    def test_start_pause_resume_drop_use_real_playthroughs(self):
        self.assertEqual(self.action("start_playing_game").status_code, 200)
        self.assertEqual(self.action("start_playing_game").status_code, 200)
        game = Game.objects.get(user=self.user, item=self.item)
        first = game.current_session
        self.assertIsNotNone(first)
        self.assertEqual(game.playthroughs.count(), 1)
        self.assertIsNone(first.total_minutes)
        self.assertIsNone(first.percentage)
        self.assertEqual(self.action("pause_media").status_code, 200)
        first.refresh_from_db()
        self.assertEqual(first.status, Status.PAUSED.value)
        self.assertEqual(self.action("resume_media").status_code, 200)
        first.refresh_from_db()
        self.assertEqual(first.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.action("drop_media").status_code, 200)
        first.refresh_from_db()
        self.assertEqual(first.status, Status.DROPPED.value)
        self.assertEqual(first.end_date, timezone.localdate())
        self.assertEqual(self.action("start_playing_game").status_code, 200)
        game.refresh_from_db()
        self.assertNotEqual(game.current_session_id, first.pk)
        self.assertEqual(game.playthroughs.count(), 2)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=self.item).exists())

    def test_eye_is_undated_and_undo_restores_planning(self):
        game_tracking.assign_status(self.user, self.item, Status.PLANNING.value)
        self.assertEqual(self.action("mark_game_played").status_code, 200)
        game = Game.objects.get(user=self.user, item=self.item)
        self.assertTrue(game.completed_manually)
        self.assertIsNone(game.end_date)
        self.assertFalse(game.playthroughs.exists())
        self.assertEqual(self.action("unmark_game_completed").status_code, 200)
        game.refresh_from_db()
        self.assertFalse(game.completed_manually)
        self.assertEqual(game.status, Status.PLANNING.value)

    def test_eye_score_and_generic_save_cannot_bypass_completion_composer(self):
        game = game_tracking.start(self.user, self.item)
        response = self.action("mark_game_completed")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("HX-Retarget"), "#log-modal-container")
        request = RequestFactory().post("/score", {"score": "4"})
        request.user = self.user
        response = views.update_media_score(request, "game", game.pk)
        self.assertIn(response.status_code, [400, 409])
        response = self.client.post(reverse("media_save"), {
            "instance_id": game.pk, "media_id": self.item.media_id,
            "source": "manual", "media_type": "game", "status": Status.COMPLETED.value,
            "score": "", "notes": "",
        })
        self.assertIn(response.status_code, [302, 400, 409])
        game.refresh_from_db()
        self.assertEqual(game.status, Status.IN_PROGRESS.value)
        self.assertIsNone(game.score)
        self.assertFalse(game.completed_manually)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=self.item).exists())

    def test_library_delete_and_eye_preserve_completion_history(self):
        game, log = game_tracking.complete(
            self.user, self.item, completion_date=timezone.localdate(), total_minutes=600,
        )
        response = self.client.post(reverse("media_delete"), {
            "instance_id": game.pk, "media_type": "game",
        })
        self.assertIn(response.status_code, [302, 400, 409])
        self.assertTrue(Game.objects.filter(pk=game.pk).exists())
        self.assertTrue(DiaryEntry.objects.filter(pk=log.pk).exists())
        self.assertIn(self.action("unmark_game_completed").status_code, [400, 409])
        self.assertTrue(game.playthroughs.filter(completion_diary_entry=log).exists())

    def test_web_completion_retries_and_diary_corrections_share_sources(self):
        game = game_tracking.start(self.user, self.item)
        payload = {
            "watch_date": str(timezone.localdate()), "rating": "4", "liked": "true",
            "total_minutes": "600", "percentage": "65", "mutation_id": str(uuid4()),
        }
        url = reverse("add_movie_diary_entry", args=["manual", "game", self.item.media_id])
        for _ in range(2):
            response = self.client.post(url, payload)
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=self.item).count(), 1)
        log = DiaryEntry.objects.get(user=self.user, item=self.item)
        game.refresh_from_db()
        self.assertEqual(game.score, 8)
        self.assertEqual(log.rating, 8)
        self.assertEqual(game.current_session.total_minutes, 600)
        response = self.client.post(reverse("update_diary_entry", args=[log.pk]), {
            "watch_date": str(timezone.localdate()), "rating": "3", "liked": "false",
            "total_minutes": "750", "percentage": "60",
        })
        self.assertEqual(response.status_code, 200, response.content)
        game.refresh_from_db()
        self.assertEqual(game.score, 6)
        self.assertEqual(game.current_session.total_minutes, 750)
        game_tracking.update_progress(self.user, self.item, total_minutes=800)
        response = self.client.post(reverse("delete_diary_entry", args=[log.pk]))
        self.assertEqual(response.status_code, 200)
        game.refresh_from_db()
        self.assertEqual(game.status, Status.IN_PROGRESS.value)
        self.assertEqual(game.current_session.total_minutes, 800)

    def test_completion_edit_rejects_explicit_blank_or_invalid_date_atomically(self):
        game, log = game_tracking.complete(
            self.user, self.item, completion_date=timezone.localdate(), total_minutes=600,
        )
        original_date = log.consumed_at
        for value in ("", "not-a-date", "2026-02-30"):
            with self.subTest(date=value):
                response = self.client.post(reverse("update_diary_entry", args=[log.pk]), {
                    "watch_date": value, "total_minutes": "999", "rating": "4",
                })
                self.assertEqual(response.status_code, 400, response.content)
                log.refresh_from_db()
                game.refresh_from_db()
                self.assertEqual(log.consumed_at, original_date)
                self.assertEqual(log.progress_snapshot["total_minutes"], 600)
                self.assertEqual(game.current_session.total_minutes, 600)
                self.assertIsNone(game.score)

    def test_completion_edit_omitted_date_preserves_the_saved_calendar_date(self):
        _, log = game_tracking.complete(
            self.user, self.item, completion_date="2024-01-01", total_minutes=600,
        )
        original_date = log.consumed_at
        response = self.client.post(reverse("update_diary_entry", args=[log.pk]), {
            "total_minutes": "650", "percentage": "0",
        })
        self.assertEqual(response.status_code, 200)
        log.refresh_from_db()
        self.assertEqual(log.consumed_at, original_date)
        self.assertEqual(log.progress_snapshot, {"total_minutes": 650, "percentage": 0})

    def test_completion_create_rejects_missing_date_and_decimal_progress_atomically(self):
        game_tracking.start(self.user, self.item)
        url = reverse("add_movie_diary_entry", args=["manual", "game", self.item.media_id])
        for invalid in ({}, {"watch_date": ""}, {"watch_date": "not-a-date"},
                        {"watch_date": str(timezone.localdate()), "percentage": "65.0"}):
            response = self.client.post(url, {"mutation_id": str(uuid4()), **invalid})
            self.assertEqual(response.status_code, 400)
            self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=self.item).exists())
            self.assertEqual(Game.objects.get(user=self.user, item=self.item).status, Status.IN_PROGRESS.value)
