"""Fresh and legacy game schemas preserve evidence without invented attempts."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class GameTrackingMigrationTests(TransactionTestCase):
    """Upgrade representative ambiguous rows and enforce new database guards."""

    migrate_from = [("app", "0077_media_series_membership")]
    migrate_to = [("app", "0079_preserve_legacy_game_duplicates")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_preserves_legacy_values_duplicates_diary_and_history(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old = executor.loader.project_state(self.migrate_from).apps
        Item = old.get_model("app", "Item")
        Game = old.get_model("app", "Game")
        Diary = old.get_model("app", "DiaryEntry")
        History = old.get_model("app", "HistoricalGame")
        user = get_user_model().objects.create_user(username="game-migration")
        item = Item.objects.create(
            source="manual", media_type="game", media_id="legacy", title="Legacy"
        )
        dated_item = Item.objects.create(
            source="manual", media_type="game", media_id="dated", title="Dated"
        )
        now = timezone.now()
        first = Game.objects.create(
            user_id=user.pk,
            item_id=item.pk,
            status="In progress",
            progress=50,
            start_date=now,
        )
        last = Game.objects.create(
            user_id=user.pk,
            item_id=item.pk,
            status="Completed",
            progress=999,
            end_date=now,
        )
        history = History.objects.create(
            id=first.pk,
            history_type="+",
            history_date=now,
            status="In progress",
            progress=50,
        )
        dated = Game.objects.create(
            user_id=user.pk, item_id=dated_item.pk, status="Completed", progress=300
        )
        entry = Diary.objects.create(
            user_id=user.pk,
            item_id=dated_item.pk,
            consumed_at=now,
            progress_snapshot={"playtime_minutes": 300},
        )
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new = executor.loader.project_state(self.migrate_to).apps
        NewGame = new.get_model("app", "Game")
        Session = new.get_model("app", "GameSession")
        self.assertEqual(NewGame.all_objects.count(), 3)
        self.assertTrue(NewGame.all_objects.get(pk=first.pk).legacy_archived)
        self.assertEqual(NewGame.all_objects.get(pk=first.pk).progress, 50)
        canonical = NewGame.all_objects.get(pk=last.pk)
        self.assertFalse(canonical.legacy_archived)
        self.assertTrue(canonical.completed_manually)
        self.assertEqual(canonical.progress, 999)
        self.assertEqual(canonical.end_date, now)
        self.assertFalse(NewGame.all_objects.get(pk=dated.pk).completed_manually)
        self.assertEqual(
            new.get_model("app", "DiaryEntry")
            .objects.get(pk=entry.pk)
            .progress_snapshot,
            {"playtime_minutes": 300},
        )
        self.assertTrue(
            new.get_model("app", "HistoricalGame")
            .objects.filter(pk=history.pk)
            .exists()
        )
        self.assertFalse(Session.objects.exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            NewGame.all_objects.create(user_id=user.pk, item_id=item.pk)
        current = Session.objects.create(
            related_game_id=last.pk, status="In progress", start_date=now.date()
        )
        for kwargs in (
            {"status": "Paused", "start_date": now.date()},
            {"status": "Dropped", "percentage": 101, "start_date": now.date()},
            {"status": "Dropped", "start_date": None},
        ):
            with self.assertRaises(IntegrityError), transaction.atomic():
                Session.objects.create(related_game_id=last.pk, **kwargs)
        self.assertTrue(Session.objects.filter(pk=current.pk).exists())
