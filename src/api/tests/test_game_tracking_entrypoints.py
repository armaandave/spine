"""Independent checks for alternate game mutation paths and shared opinions."""

from decimal import Decimal
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from app.models import DiaryEntry, Game, GameSession, Item, MediaLike, Status
from lists.models import CustomList, CustomListItem
from social.models import Activity, Block


class GameTrackingEntryPointTests(TestCase):
    """Keep alternate entry points inside the same game contract."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="game-owner")
        self.other = get_user_model().objects.create_user(username="game-viewer")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.item = Item.objects.create(
            source="manual", media_type="game", media_id="entrypoints", title="Game entry points",
        )
        self.ref = {"source": "manual", "media_type": "game", "media_id": self.item.media_id}
        self.url = "/api/v1/tracking/manual/game/entrypoints/"
        self.day = str(timezone.localdate())

    def action(self, name):
        response = self.client.post(f"{self.url}actions/{name}/", {}, format="json")
        self.assertIn(response.status_code, (200, 204), response.data)
        return response

    def log(self, **data):
        response = self.client.post(
            f"{self.url}complete/",
            {"completion_date": self.day, "mutation_id": str(uuid4()), **data},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["diary_entry"]["id"]

    def test_generic_diary_retry_is_idempotent_but_same_day_logs_are_distinct(self):
        """G-35 and SW-007 apply to generic diary POST as well as complete."""
        payload = {
            "ref": self.ref, "consumed_at": self.day, "mutation_id": str(uuid4()),
            "total_minutes": 0, "percentage": None, "rating": "4.5", "liked": True,
        }
        first = self.client.post("/api/v1/diary/", payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        again = self.client.post("/api/v1/diary/", payload, format="json")
        self.assertEqual(again.status_code, 201, again.data)
        self.assertEqual(first.data["id"], again.data["id"])
        payload["mutation_id"] = str(uuid4())
        distinct = self.client.post("/api/v1/diary/", payload, format="json")
        self.assertEqual(distinct.status_code, 201, distinct.data)
        self.assertNotEqual(first.data["id"], distinct.data["id"])
        self.assertEqual(DiaryEntry.objects.count(), 2)
        self.assertEqual(GameSession.objects.count(), 2)
        self.assertEqual(Game.objects.count(), 1)
        self.assertEqual(Activity.objects.filter(verb="diary_created").count(), 2)
        self.assertFalse(Activity.objects.filter(verb="rating_changed").exists())

    def test_generic_completion_and_like_cannot_bypass_an_unfinished_playthrough(self):
        """G-28 protects both generic status and title-heart entry points."""
        self.action("start")
        for url, payload, method in (
            (self.url, {"status": "Completed", "rating": "4.0"}, self.client.patch),
            (f"{self.url}actions/consume/", {}, self.client.post),
            ("/api/v1/me/liked-media/", {"ref": self.ref}, self.client.post),
        ):
            with self.subTest(url=url):
                response = method(url, payload, format="json")
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(Game.objects.get().status, Status.IN_PROGRESS)
                self.assertIsNone(Game.objects.get().score)
                self.assertFalse(DiaryEntry.objects.exists())
                self.assertFalse(MediaLike.objects.exists())

    def test_removal_protects_history_and_keeps_custom_lists(self):
        """G-30 blocks generic DELETE until recorded history is removed."""
        custom_list = CustomList.objects.create(name="Keep", owner=self.user)
        membership = CustomListItem.objects.create(custom_list=custom_list, item=self.item)
        self.action("start")
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(GameSession.objects.count(), 1)
        entry_id = self.log(rating="4.0", liked=True)
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(DiaryEntry.objects.filter(pk=entry_id).exists())
        self.assertTrue(CustomListItem.objects.filter(pk=membership.pk).exists())

    def test_game_history_and_diary_mutations_are_owner_scoped(self):
        """A playthrough identifier cannot grant access to another account."""
        entry_id = self.log(total_minutes=80, percentage=40)
        playthrough = GameSession.objects.get()
        self.client.force_authenticate(self.other)
        response = self.client.patch(
            f"{self.url}playthroughs/{playthrough.pk}/", {"total_minutes": 500}, format="json",
        )
        self.assertIn(response.status_code, (404, 409), response.data)
        response = self.client.delete(f"{self.url}playthroughs/{playthrough.pk}/")
        self.assertIn(response.status_code, (404, 409), response.data)
        for method in (self.client.patch, self.client.delete):
            response = method(f"/api/v1/diary/{entry_id}/", {}, format="json")
            self.assertEqual(response.status_code, 404, response.data)
        playthrough.refresh_from_db()
        self.assertEqual(playthrough.total_minutes, 80)
        self.assertTrue(DiaryEntry.objects.filter(pk=entry_id).exists())
        self.assertFalse(Game.objects.filter(user=self.other).exists())

    def test_account_privacy_and_blocks_hide_completion_history(self):
        """SW-400 keeps public diary data inside account visibility."""
        entry_id = self.log(rating="4.0", liked=True)
        self.user.profile_private = True
        self.user.save(update_fields=["profile_private"])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/v1/diary/{entry_id}/").status_code, 404)
        self.user.profile_private = False
        self.user.save(update_fields=["profile_private"])
        self.assertEqual(self.client.get(f"/api/v1/diary/{entry_id}/").status_code, 200)
        Block.objects.create(blocker=self.user, blocked=self.other)
        self.assertEqual(self.client.get(f"/api/v1/diary/{entry_id}/").status_code, 404)

    def test_opinion_fields_decouple_independently_and_source_clear_is_scoped(self):
        """GM-303 preserves diary hearts when the title heart changes."""
        entry_id = self.log(rating="4.0", liked=True)
        response = self.client.delete("/api/v1/me/liked-media/", {"ref": self.ref}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        entry = DiaryEntry.objects.get(pk=entry_id)
        self.assertTrue(entry.liked)
        response = self.client.patch(
            f"/api/v1/diary/{entry_id}/", {"rating": "5.0", "liked": True}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        game = Game.objects.get()
        self.assertEqual(game.score, Decimal(10))
        self.assertEqual(game.rating_source_id, entry_id)
        self.assertIsNone(game.like_source_id)
        self.assertFalse(MediaLike.objects.exists())
        response = self.client.patch(f"/api/v1/diary/{entry_id}/", {"rating": None}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        game.refresh_from_db()
        self.assertIsNone(game.score)
        self.assertIsNone(game.rating_source_id)

    def test_invalid_generic_diary_write_does_not_create_partial_tracking(self):
        """A failed alternate composer save leaves no orphaned state."""
        response = self.client.post(
            "/api/v1/diary/",
            {"ref": self.ref, "consumed_at": self.day, "mutation_id": str(uuid4()),
             "total_minutes": 5, "percentage": 101, "liked": True, "rating": "4.0"},
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Game.objects.exists())
        self.assertFalse(GameSession.objects.exists())
        self.assertFalse(DiaryEntry.objects.exists())
        self.assertFalse(MediaLike.objects.exists())

    def test_unauthenticated_mutations_are_rejected(self):
        """Game routes retain API authentication requirements."""
        self.client.force_authenticate(None)
        for path, payload in (
            (f"{self.url}actions/start/", {}),
            (f"{self.url}progress/", {"total_minutes": 60}),
            (f"{self.url}complete/", {"completion_date": self.day, "mutation_id": str(uuid4())}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, payload, format="json").status_code, 401)
        self.assertFalse(Game.objects.exists())
