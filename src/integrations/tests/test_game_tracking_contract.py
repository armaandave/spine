from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from app.models import DiaryEntry, Game, Item, MediaTypes, Sources, Status
from integrations.imports import helpers, steam


@override_settings(STEAM_API_KEY="local-test-key")
class GameImportContractTests(TestCase):
    """GM-500/501 keep provider totals separate from tracking decisions."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="game-import")
        self.item = Item.objects.create(
            media_id="game-import", source=Sources.IGDB.value,
            media_type=MediaTypes.GAME.value, title="Import game", image="",
        )

    def import_steam(self, minutes=18000, recent=20, mode="overwrite"):
        game_data = [{"appid": 1, "name": self.item.title,
                      "playtime_forever": minutes, "playtime_2weeks": recent}]
        metadata = {"media_id": self.item.media_id, "title": self.item.title, "image": ""}
        with patch.object(steam.SteamImporter, "_get_owned_games", return_value=game_data), \
             patch.object(steam.SteamImporter, "_match_with_igdb", return_value=metadata):
            return steam.importer("local-test-id", self.user, mode)

    def test_g33_refresh_preserves_playthrough_and_completion_progress(self):
        # Legacy progress also remains untouched until a user creates a real attempt.
        game = Game.objects.create(user=self.user, item=self.item,
                                   status=Status.IN_PROGRESS.value, progress=600)
        self.import_steam()
        game.refresh_from_db()
        self.assertEqual(game.progress, 600)
        self.assertEqual(game.imported_lifetime_minutes, 18000)
        self.assertEqual(game.imported_lifetime_source, "steam")
        self.import_steam(minutes=18100)
        self.import_steam(minutes=18100)
        game.refresh_from_db()
        self.assertEqual(game.imported_lifetime_minutes, 18100)
        self.assertEqual(game.progress, 600)
        self.assertEqual(Game.objects.filter(user=self.user, item=self.item).count(), 1)

    def test_g33_refresh_keeps_real_replay_and_completion_snapshot(self):
        from django.utils import timezone

        from app import game_tracking

        _, log = game_tracking.complete(
            self.user, self.item, completion_date=timezone.localdate(),
            total_minutes=2400, percentage=65,
        )
        snapshot = log.progress_snapshot.copy()
        game = game_tracking.start(self.user, self.item)
        game_tracking.update_progress(self.user, self.item, total_minutes=600)
        replay_id = game.current_session_id
        self.import_steam()
        self.import_steam()
        game.refresh_from_db()
        log.refresh_from_db()
        self.assertEqual(game.current_session_id, replay_id)
        self.assertEqual(game.current_session.total_minutes, 600)
        self.assertEqual(game.status, Status.IN_PROGRESS.value)
        self.assertEqual(game.imported_lifetime_minutes, 18000)
        self.assertEqual(log.progress_snapshot, snapshot)
        self.assertEqual(game.playthroughs.count(), 2)

    def test_invalid_provider_playtime_does_not_change_tracking(self):
        game = Game.objects.create(user=self.user, item=self.item, status=Status.PAUSED.value)
        for minutes in (-1, 1.5, True):
            with self.subTest(minutes=minutes):
                counts, warnings = self.import_steam(minutes=minutes)
                game.refresh_from_db()
                self.assertFalse(counts)
                self.assertIn("non-negative whole minutes", warnings)
                self.assertIsNone(game.imported_lifetime_minutes)
                self.assertEqual(game.status, Status.PAUSED.value)

    def test_g34_steam_preserves_every_existing_status(self):
        for status in Status.values:
            with self.subTest(status=status):
                Game.objects.filter(user=self.user, item=self.item).delete()
                game = Game.objects.create(user=self.user, item=self.item, status=status)
                original_start = game.start_date
                self.import_steam(recent=0)
                game.refresh_from_db()
                self.assertEqual(game.status, status)
                self.assertEqual(game.start_date, original_start)
                self.assertEqual(game.imported_lifetime_minutes, 18000)

    def test_g34_new_steam_games_are_planning_regardless_of_playtime(self):
        for minutes, recent in [(0, 0), (600, 0), (600, 60)]:
            with self.subTest(minutes=minutes, recent=recent):
                Game.objects.filter(user=self.user, item=self.item).delete()
                self.import_steam(minutes=minutes, recent=recent)
                game = Game.objects.get(user=self.user, item=self.item)
                self.assertEqual(game.status, Status.PLANNING.value)
                self.assertIsNone(game.start_date)
                self.assertIsNone(game.end_date)
                self.assertEqual(game.progress, 0)
                self.assertEqual(game.imported_lifetime_minutes, minutes)
                self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=self.item).exists())

    def test_overwrite_cleanup_never_deletes_game_history(self):
        game = Game.objects.create(user=self.user, item=self.item, status=Status.PLANNING.value)
        entry = DiaryEntry.objects.create(user=self.user, item=self.item,
                                         consumed_at="2024-01-01T00:00:00Z")
        helpers.cleanup_existing_media({"game": {Sources.IGDB.value: {self.item.media_id}}}, self.user)
        self.assertTrue(Game.objects.filter(pk=game.pk).exists())
        self.assertTrue(DiaryEntry.objects.filter(pk=entry.pk).exists())

    def test_title_opinions_cannot_bypass_an_unfinished_playthrough(self):
        from django.core.exceptions import ValidationError

        from app import game_tracking
        from app.book_tracking import BookTrackingConflict
        from app.models import MediaLike

        game = game_tracking.start(self.user, self.item)
        with self.assertRaises((ValidationError, BookTrackingConflict)):
            game_tracking.import_title_state(self.user, self.item, rating=8, liked=True)
        game.refresh_from_db()
        self.assertEqual(game.status, Status.IN_PROGRESS.value)
        self.assertIsNone(game.score)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=self.item).exists())
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=self.item).exists())


class GameDiaryImportContractTests(TestCase):
    """Inherited source identity and independent opinions apply to games."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="game-diary-import")
        self.item = Item.objects.create(
            media_id="game-diary-import", source=Sources.MANUAL.value,
            media_type=MediaTypes.GAME.value, title="Diary import game", image="",
        )

    def test_same_day_source_records_reconcile_and_reimport_without_duplicates(self):
        from app.game_imports import import_logs
        from app.models import MediaLike
        from social.models import Activity

        rows = [
            {"source_id": "first", "source_order": 1, "consumed_at": "2024-01-01",
             "rating": 6, "liked": True, "total_minutes": 600},
            {"source_id": "second", "source_order": 2, "consumed_at": "2024-01-01",
             "rating": 8, "liked": False, "is_rewatch": False},
        ]
        entries = import_logs(self.user, self.item, rows, source="test-games")
        game = Game.objects.get(user=self.user, item=self.item)
        self.assertEqual(len(entries), 2)
        self.assertEqual([entry.is_rewatch for entry in entries], [False, False])
        self.assertEqual(game.status, Status.COMPLETED.value)
        self.assertEqual(game.score, 8)
        self.assertEqual(game.rating_source_id, entries[1].pk)
        self.assertEqual(game.like_source_id, entries[1].pk)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=self.item).exists())
        self.assertFalse(Activity.objects.filter(actor=self.user).exists())
        self.assertEqual(import_logs(self.user, self.item, rows, source="test-games"), [])
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=self.item).count(), 2)

    def test_import_keeps_independent_opinions_and_current_playthrough(self):
        from app import game_tracking
        from app.game_imports import import_logs
        from app.models import MediaLike

        game_tracking.import_title_state(self.user, self.item, rating=10, liked=False)
        game = game_tracking.start(self.user, self.item)
        before = game_tracking.state_payload(game)["current_playthrough"]
        entries = import_logs(self.user, self.item, [{
            "source_id": "old-completion", "consumed_at": "2024-01-01",
            "rating": 2, "liked": True, "total_minutes": 600,
        }], source="test-games")
        game.refresh_from_db()
        self.assertEqual(game.score, 10)
        self.assertIsNone(game.rating_source_id)
        self.assertTrue(game.like_is_independent)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=self.item).exists())
        self.assertEqual(game.status, Status.IN_PROGRESS.value)
        self.assertEqual(game_tracking.state_payload(game)["current_playthrough"], before)
        self.assertTrue(entries[0].is_rewatch)  # The undated fact precedes imported dates.

    def test_imported_completion_replaces_direct_planning_or_paused_state(self):
        from app import game_tracking
        from app.game_imports import import_logs

        for status in (Status.PLANNING.value, Status.PAUSED.value):
            with self.subTest(status=status):
                game_tracking.assign_status(self.user, self.item, status)
                entries = import_logs(self.user, self.item, [{
                    "source_id": status, "consumed_at": "2024-01-01",
                    "total_minutes": 600,
                }], source="test-games")
                game = Game.objects.get(user=self.user, item=self.item)
                self.assertEqual(game.status, Status.COMPLETED.value)
                self.assertEqual(game.current_session.completion_diary_entry_id, entries[0].pk)
                self.assertIsNone(game.current_session.start_date)
                self.assertEqual(game.current_session.total_minutes, 600)

    def test_invalid_import_rolls_back_the_whole_batch(self):
        from django.core.exceptions import ValidationError

        from app.game_imports import import_logs

        with self.assertRaises(ValidationError):
            import_logs(self.user, self.item, [
                {"source_id": "valid", "consumed_at": "2024-01-01", "rating": 6},
                {"source_id": "invalid", "consumed_at": "2024-01-02", "percentage": 101},
            ], source="test-games")
        self.assertFalse(Game.objects.filter(user=self.user, item=self.item).exists())
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=self.item).exists())

    def test_source_identity_cannot_silently_match_a_different_game(self):
        from django.core.exceptions import ValidationError

        from app.game_imports import import_logs

        row = {"source_id": "same-source-record", "consumed_at": "2024-01-01"}
        import_logs(self.user, self.item, [row], source="test-games")
        other = Item.objects.create(
            media_id="other-game", source=Sources.MANUAL.value,
            media_type=MediaTypes.GAME.value, title="Other game", image="",
        )
        with self.assertRaises(ValidationError):
            import_logs(self.user, other, [row], source="test-games")
        self.assertFalse(Game.objects.filter(user=self.user, item=other).exists())


class GameCSVImportContractTests(TestCase):
    """Legacy CSV totals survive without becoming invented playthroughs."""

    def test_export_style_minutes_and_lifetime_fields_survive_import(self):
        from io import BytesIO

        from integrations.imports import yamtrack

        user = get_user_model().objects.create_user(username="game-csv")
        csv_data = (
            'media_id,source,media_type,title,image,season_number,episode_number,'
            'score,progress,status,start_date,end_date,notes,imported_lifetime_minutes,'
            'imported_lifetime_source\n'
            'csv-game,manual,game,CSV Game,https://example.com/game.jpg,,,,'
            '12h 30min,Planning,,,Old playtime,18000,steam\n'
        )
        counts, warnings = yamtrack.importer(BytesIO(csv_data.encode()), user, "new")
        self.assertEqual(counts["game"], 1)
        self.assertEqual(warnings, "")
        game = Game.objects.get(user=user)
        self.assertEqual(game.progress, 750)
        self.assertEqual(game.imported_lifetime_minutes, 18000)
        self.assertEqual(game.imported_lifetime_source, "steam")
        self.assertEqual(game.status, Status.PLANNING.value)
        self.assertFalse(game.playthroughs.exists())
