"""Game contract regression cases, exercised through authenticated API writes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from app.models import DiaryEntry, Game, Item, MediaLike, Status


class GameTrackingContractTests(TestCase):
    """Validate and expose canonical game playthrough state."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="game-contract")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()
        self.item = Item.objects.create(
            source="manual",
            media_type="game",
            media_id="game-contract",
            title="Contract Game",
        )
        self.url = "/api/v1/tracking/manual/game/game-contract/"

    def action(self, action, **data):
        response = self.client.post(f"{self.url}actions/{action}/", data, format="json")
        self.assertIn(response.status_code, (200, 204), response.data)
        return response

    def progress(self, **data):
        response = self.client.post(f"{self.url}progress/", data, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data["game"]["current_playthrough"]

    def complete(self, **data):
        payload = {
            "completion_date": str(self.today),
            "mutation_id": str(uuid4()),
            **data,
        }
        response = self.client.post(f"{self.url}complete/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def state(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def delete_log(self, entry_id):
        response = self.client.delete(f"/api/v1/diary/{entry_id}/")
        self.assertEqual(response.status_code, 204, response.data)

    def edit_log(self, entry_id, **data):
        response = self.client.patch(f"/api/v1/diary/{entry_id}/", data, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_legacy_playing_can_start_tracking_without_inventing_old_progress(self):
        game = Game.objects.create(
            user=self.user, item=self.item, status=Status.IN_PROGRESS,
            progress=90,
            status_history=[{"status": Status.IN_PROGRESS.value, "kind": "direct_status"}],
        )
        self.assertIsNone(self.state()["game"]["current_playthrough"])
        started = self.action("start").data["game"]["current_playthrough"]
        self.assertEqual(started["start_date"], str(self.today))
        self.assertIsNone(started["total_minutes"])
        self.assertIsNone(started["percentage"])
        self.assertEqual(self.action("start").data["game"]["current_playthrough"]["id"], started["id"])
        game.refresh_from_db()
        self.assertEqual(game.progress, 90)
        self.assertEqual(game.playthroughs.count(), 1)
        self.assertFalse(DiaryEntry.objects.exists())

    def test_g01_status_only_completion_and_g35_retries(self):
        started = self.action("start").data["game"]["current_playthrough"]
        self.assertIsNone(started["total_minutes"])
        self.assertIsNone(started["percentage"])
        self.assertEqual(
            self.action("start").data["game"]["current_playthrough"]["id"],
            started["id"],
        )
        mutation = str(uuid4())
        first = self.complete(playthrough_id=started["id"], mutation_id=mutation)
        second = self.complete(playthrough_id=started["id"], mutation_id=mutation)
        self.assertEqual(first["diary_entry"]["id"], second["diary_entry"]["id"])
        self.assertEqual(first["tracking"]["status"], Status.COMPLETED)
        self.assertIsNone(first["diary_entry"]["total_minutes"])
        self.assertEqual(DiaryEntry.objects.count(), 1)

    def test_g02_g03_g04_g06_g07_independent_optional_progress_totals(self):
        self.action("start")
        self.progress(total_minutes=750)
        state = self.progress(percentage=65)
        self.assertEqual((state["total_minutes"], state["percentage"]), (750, 65))
        self.assertEqual(self.progress(total_minutes=780)["total_minutes"], 780)
        self.assertEqual(
            self.progress(total_minutes=700, percentage=60)["percentage"], 60
        )
        state = self.progress(total_minutes=None)
        self.assertEqual((state["total_minutes"], state["percentage"]), (None, 60))
        self.assertEqual(
            self.progress(total_minutes=0, percentage=0)["total_minutes"], 0
        )
        self.progress(percentage=100)
        self.assertEqual(self.state()["status"], Status.IN_PROGRESS)
        self.assertFalse(DiaryEntry.objects.exists())

    def test_g05_invalid_progress_is_atomic(self):
        self.action("start")
        for value in (-1, 101, 1.5, 65.0, "65.0", True):
            response = self.client.post(
                f"{self.url}progress/",
                {"total_minutes": 44, "percentage": value},
                format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.post(
            f"{self.url}progress/", {"total_minutes": -1}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.state()["game"]["current_playthrough"]["total_minutes"])

    def test_g08_g16_g17_g18_independent_completion_snapshot_coupling(self):
        self.action("start")
        self.progress(total_minutes=2400, percentage=65)
        result = self.complete(total_minutes=2700, percentage=65)
        entry = result["diary_entry"]
        self.assertEqual(
            result["tracking"]["game"]["current_playthrough"]["total_minutes"], 2700
        )
        self.edit_log(entry["id"], total_minutes=2800)
        self.assertEqual(
            self.state()["game"]["current_playthrough"]["total_minutes"], 2800
        )
        self.progress(total_minutes=3000)
        self.edit_log(entry["id"], total_minutes=2400, percentage=70)
        state = self.state()
        self.assertEqual(state["status"], Status.COMPLETED)
        self.assertEqual(
            (
                state["game"]["current_playthrough"]["total_minutes"],
                state["game"]["current_playthrough"]["percentage"],
            ),
            (3000, 70),
        )
        self.progress(percentage=80)
        saved = self.client.get(f"/api/v1/diary/{entry['id']}/").data
        self.assertEqual((saved["total_minutes"], saved["percentage"]), (2400, 70))

    def test_g09_g35_failed_compound_completion_has_no_effects(self):
        self.action("start")
        self.progress(total_minutes=33)
        with (
            patch(
                "app.single_weight._create_diary_activity",
                side_effect=RuntimeError("save failed"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.post(
                f"{self.url}complete/",
                {
                    "completion_date": str(self.today),
                    "mutation_id": str(uuid4()),
                    "total_minutes": 44,
                },
                format="json",
            )
        self.assertEqual(self.state()["status"], Status.IN_PROGRESS)
        self.assertEqual(
            self.state()["game"]["current_playthrough"]["total_minutes"], 33
        )
        self.assertFalse(DiaryEntry.objects.exists())

    def test_g10_g11_g12_g13_status_attempt_lifecycle(self):
        response = self.client.patch(self.url, {"status": "Paused"}, format="json")
        self.assertIsNone(response.data["game"]["current_playthrough"])
        first = self.action("resume").data["game"]["current_playthrough"]["id"]
        self.progress(total_minutes=90)
        self.action("pause")
        self.assertEqual(
            self.action("resume").data["game"]["current_playthrough"]["id"], first
        )
        self.assertEqual(
            self.client.patch(
                self.url, {"status": "Planning"}, format="json"
            ).status_code,
            400,
        )
        self.action("drop")
        second = self.action("start").data["game"]["current_playthrough"]
        self.assertNotEqual(second["id"], first)
        self.assertIsNone(second["total_minutes"])
        mutation = str(uuid4())
        third = self.action("restart", mutation_id=mutation).data["game"][
            "current_playthrough"
        ]
        self.assertEqual(
            self.action("restart", mutation_id=mutation).data["game"][
                "current_playthrough"
            ]["id"],
            third["id"],
        )
        self.assertEqual(len(self.state()["game"]["play_history"]), 2)
        self.assertEqual(self.state()["game"]["lifetime_completion_count"], 0)

    def test_g14_g30_deleted_undated_evidence_cannot_restore(self):
        self.client.patch(self.url, {"status": "Planning"}, format="json")
        self.action("mark_completed")
        current = self.action("start").data["game"]["current_playthrough"]
        self.action("delete_undated_completion")
        response = self.client.delete(f"{self.url}playthroughs/{current['id']}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], Status.PLANNING)
        self.assertIsNone(response.data["game"]["current_playthrough"])

    def test_g15_dropped_progress_edits_do_not_change_newer_attempt(self):
        old = self.action("start").data["game"]["current_playthrough"]
        self.action("drop")
        new = self.action("start").data["game"]["current_playthrough"]
        self.progress(total_minutes=99)
        response = self.client.patch(
            f"{self.url}playthroughs/{old['id']}/",
            {"total_minutes": 20, "percentage": None},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["game"]["current_playthrough"]["id"], new["id"])
        self.assertEqual(
            response.data["game"]["current_playthrough"]["total_minutes"], 99
        )
        self.assertEqual(
            response.data["game"]["play_history"][0]["status"], Status.DROPPED
        )

    def test_g19_g20_g31_g32_backdate_and_start_date_limits(self):
        past = self.today - timedelta(days=10)
        started = self.action("start", start_date=str(past)).data["game"][
            "current_playthrough"
        ]
        self.progress(total_minutes=3000)
        result = self.complete(
            completion_date=str(past + timedelta(days=2)),
            total_minutes=2400,
            percentage=65,
        )
        self.assertEqual(
            result["tracking"]["game"]["current_playthrough"]["total_minutes"], 3000
        )
        self.assertEqual(
            result["tracking"]["game"]["current_playthrough"]["percentage"], 65
        )
        self.edit_log(
            result["diary_entry"]["id"], consumed_at=str(past + timedelta(days=3))
        )
        self.edit_log(result["diary_entry"]["id"], total_minutes=2500)
        self.assertEqual(
            self.state()["game"]["current_playthrough"]["total_minutes"], 3000
        )
        for day in (past - timedelta(days=1), self.today + timedelta(days=1)):
            response = self.client.patch(
                f"/api/v1/diary/{result['diary_entry']['id']}/",
                {"consumed_at": str(day)},
                format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
        for day in (None, str(self.today)):
            response = self.client.patch(
                f"{self.url}playthroughs/{started['id']}/",
                {"start_date": day},
                format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)

    def test_g21_g25_g26_g29_completion_date_selection_and_saved_badges(self):
        latest = self.complete(total_minutes=2400, percentage=65, is_rewatch=False)
        earlier = self.complete(
            completion_date=str(self.today - timedelta(days=2)),
            total_minutes=1200,
            is_rewatch=False,
        )
        state = self.state()["game"]
        self.assertEqual(
            state["current_playthrough"]["id"],
            latest["tracking"]["game"]["current_playthrough"]["id"],
        )
        self.assertTrue(state["current_playthrough"]["is_replay"])
        self.assertFalse(
            self.client.get(f"/api/v1/diary/{latest['diary_entry']['id']}/").data[
                "is_rewatch"
            ]
        )
        replay = self.action("start").data["game"]["current_playthrough"]
        self.progress(total_minutes=300)
        self.action("drop")
        self.action("mark_completed")
        self.assertEqual(
            self.state()["game"]["current_playthrough"]["total_minutes"], 2400
        )
        self.delete_log(latest["diary_entry"]["id"])
        self.assertEqual(
            self.state()["game"]["current_playthrough"]["total_minutes"], 1200
        )
        self.action("undo_completed")
        self.assertEqual(
            self.state()["game"]["current_playthrough"]["id"], replay["id"]
        )
        self.delete_log(earlier["diary_entry"]["id"])
        self.assertEqual(self.state()["status"], Status.DROPPED)

    def test_g22_g23_g24_deletion_preserves_only_supported_state(self):
        self.action("start")
        self.action("pause")
        completed = self.complete(total_minutes=2400)
        self.progress(total_minutes=3000)
        self.delete_log(completed["diary_entry"]["id"])
        self.assertEqual(self.state()["status"], Status.PAUSED)
        self.assertEqual(
            self.state()["game"]["current_playthrough"]["total_minutes"], 3000
        )
        completed = self.complete()
        later = self.action("start").data["game"]["current_playthrough"]
        self.delete_log(completed["diary_entry"]["id"])
        self.assertEqual(self.state()["game"]["current_playthrough"]["id"], later["id"])
        self.client.delete(f"{self.url}playthroughs/{later['id']}/")
        self.assertFalse(Game.objects.filter(user=self.user, item=self.item).exists())
        direct = self.complete(total_minutes=10)
        self.progress(total_minutes=20)
        self.delete_log(direct["diary_entry"]["id"])
        self.assertFalse(Game.objects.filter(user=self.user, item=self.item).exists())

    def test_g27_g28_opinions_require_composer_and_preserve_history_sources(self):
        self.action("mark_completed")
        self.assertIsNone(self.state()["game"]["current_playthrough"])
        self.action("undo_completed")
        self.action("start")
        self.assertEqual(
            self.client.patch(self.url, {"rating": "4.5"}, format="json").status_code,
            409,
        )
        self.assertIsNone(self.state()["rating"])
        log = self.complete(rating="4.0", liked=True)["diary_entry"]
        self.edit_log(log["id"], rating="3.0", liked=False)
        self.assertEqual(self.state()["rating"], "3.0")
        self.assertFalse(MediaLike.objects.exists())
        self.client.patch(self.url, {"rating": "5.0"}, format="json")
        self.edit_log(log["id"], rating="2.0")
        self.assertEqual(self.state()["rating"], "5.0")
        self.delete_log(log["id"])
        self.assertEqual(self.state()["rating"], "5.0")

    def test_explicit_start_clear_cannot_bypass_date_validation_with_repeated_status(
        self,
    ):
        self.action("start")
        for value in (None, (timezone.now() + timedelta(days=1)).isoformat()):
            response = self.client.patch(
                self.url,
                {"status": Status.IN_PROGRESS.value, "start_date": value},
                format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertIsNotNone(self.state()["game"]["current_playthrough"]["start_date"])

    def test_library_stats_count_unique_titles_logs_and_undated_evidence(self):
        self.action("mark_completed")
        self.action("start")
        self.complete(rating="4.5")
        self.complete(rating="3.5")
        response = self.client.get(
            "/api/v1/tracking/",
            {"media_type": "game", "status": Status.COMPLETED.value},
        )
        self.assertEqual(response.data["count"], 1)
        response = self.client.get(
            "/api/v1/stats/me/summary/", {"start_date": "all", "end_date": "all"}
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["overview"]["game_completion_count"], 3)
        game = next(
            row for row in response.data["media_types"] if row["media_type"] == "game"
        )
        self.assertEqual(game["completion_count"], 3)
        self.assertEqual(game["average_rating"], "4.0")
        response = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": str(self.today), "end_date": str(self.today)},
        )
        self.assertEqual(response.data["overview"]["game_completion_count"], 2)

    def test_async_statistics_are_enqueued_after_atomic_completion(self):
        with patch("app.single_weight.update_daily_statistics.delay") as queue:
            with self.captureOnCommitCallbacks(execute=True):
                self.complete()
            queue.assert_called_once_with(
                user_id=self.user.pk, date_str=f"{self.today}T00:00:00+00:00"
            )

    def test_imported_opinions_cannot_bypass_an_unfinished_playthrough(self):
        from app import game_tracking

        self.action("start")
        for kwargs in ({"rating": 8}, {"liked": True}):
            with self.assertRaises(game_tracking.CompletionRequired):
                game_tracking.import_title_state(self.user, self.item, **kwargs)
        self.assertEqual(self.state()["status"], Status.IN_PROGRESS)
        self.assertFalse(MediaLike.objects.exists())
        self.assertIsNone(self.state()["rating"])

    def test_invalid_half_star_rating_rejects_all_game_api_paths(self):
        for url, payload in (
            (self.url, {"rating": "4.2"}),
            (
                f"{self.url}complete/",
                {
                    "completion_date": str(self.today),
                    "mutation_id": str(uuid4()),
                    "rating": "4.2",
                },
            ),
        ):
            response = (
                self.client.patch(url, payload, format="json")
                if url == self.url
                else self.client.post(url, payload, format="json")
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Game.objects.exists())

    def test_remove_only_undated_fact_while_status_reuses_it_cleans_opinions(self):
        self.client.patch(self.url, {"rating": "4.0"}, format="json")
        self.client.patch(self.url, {"status": "Dropped"}, format="json")
        self.action("mark_completed")
        self.action("delete_undated_completion")
        state = self.state()
        self.assertEqual(state["status"], Status.DROPPED)
        self.assertIsNone(state["rating"])
        self.assertFalse(state["liked"])
        self.assertIsNone(state["game"]["undated_completion"])

    def test_calendar_dates_survive_generic_diary_routes_in_western_timezone(self):
        with timezone.override("America/New_York"):
            day = timezone.localdate() - timedelta(days=2)
            response = self.client.post(
                "/api/v1/diary/",
                {
                    "ref": {
                        "source": "manual",
                        "media_type": "game",
                        "media_id": self.item.media_id,
                    },
                    "consumed_at": str(day),
                    "mutation_id": str(uuid4()),
                },
                format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data["consumed_at"], str(day))
            edited = self.edit_log(
                response.data["id"], consumed_at=str(day + timedelta(days=1))
            )
            self.assertEqual(edited["consumed_at"], str(day + timedelta(days=1)))
            self.assertEqual(
                self.state()["game"]["completion_dates"], [str(day + timedelta(days=1))]
            )

    def test_import_cannot_silently_replace_or_ignore_conflicting_live_status(self):
        from app import game_tracking

        self.action("start")
        for status in (
            Status.PLANNING,
            Status.COMPLETED,
            Status.DROPPED,
            Status.PAUSED,
        ):
            with self.assertRaises(game_tracking.BookTrackingConflict):
                game_tracking.import_title_state(self.user, self.item, status=status)
        self.assertEqual(self.state()["status"], Status.IN_PROGRESS)

    def test_restart_requires_retry_identity_before_mutating_history(self):
        self.action("start")
        response = self.client.post(f"{self.url}actions/restart/", {}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.state()["game"]["play_history"], [])

    def test_same_day_selection_uses_completion_order_not_start_order(self):
        from app import game_imports

        live = self.action("start").data["game"]["current_playthrough"]
        game_imports.import_logs(
            self.user,
            self.item,
            [
                {
                    "source_id": "past-play",
                    "consumed_at": self.today,
                    "total_minutes": 90,
                }
            ],
            source="test",
        )
        result = self.complete(total_minutes=120)
        self.assertEqual(
            result["tracking"]["game"]["current_playthrough"]["id"], live["id"]
        )
        self.assertTrue(result["tracking"]["game"]["current_playthrough"]["is_replay"])

    def test_progress_exceeding_database_capacity_is_a_validation_error(self):
        self.action("start")
        response = self.client.post(
            f"{self.url}progress/", {"total_minutes": 10**25}, format="json"
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIsNone(self.state()["game"]["current_playthrough"]["total_minutes"])

    def test_local_progress_date_keeps_same_day_completion_fields_linked(self):
        self.action("start", start_date=str(self.today - timedelta(days=1)))
        session = Game.objects.get(user=self.user, item=self.item).current_session
        local_day = self.today - timedelta(days=1)
        response = self.client.patch(
            f"{self.url}playthroughs/{session.pk}/",
            {
                "total_minutes": 2460,
                "percentage": 65,
                "progressed_on": str(local_day),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        result = self.complete(
            completion_date=str(local_day), total_minutes=2400, percentage=65
        )
        self.assertEqual(
            result["tracking"]["game"]["current_playthrough"]["total_minutes"], 2400
        )
        session.refresh_from_db()
        self.assertEqual(session.minutes_updated_on, local_day)
        self.assertTrue(session.minutes_linked)
        self.assertTrue(session.percentage_linked)

    def test_client_timezone_controls_today_and_future_date_validation(self):
        with patch(
            "django.utils.timezone.now",
            return_value=datetime(2026, 9, 28, 2, 0, tzinfo=UTC),
        ):
            self.client.credentials(HTTP_X_SPINE_TIMEZONE="America/New_York")
            session = self.action("start").data["game"]["current_playthrough"]
            self.assertEqual(session["start_date"], "2026-09-27")
            self.progress(total_minutes=2460, percentage=65)
            result = self.complete(
                completion_date="2026-09-27", total_minutes=2400, percentage=65
            )
            self.assertEqual(
                result["tracking"]["game"]["current_playthrough"]["total_minutes"], 2400
            )
            stats = self.client.get(
                "/api/v1/stats/me/summary/",
                {"start_date": "2026-09-27", "end_date": "2026-09-27"},
            )
            self.assertEqual(stats.data["overview"]["game_completion_count"], 1)
            self.assertEqual(
                stats.data["activity"]["days"], [{"date": "2026-09-27", "count": 1}]
            )
        with patch(
            "django.utils.timezone.now",
            return_value=datetime(2026, 9, 28, 22, 0, tzinfo=UTC),
        ):
            self.client.credentials(HTTP_X_SPINE_TIMEZONE="Pacific/Kiritimati")
            session = self.action("start").data["game"]["current_playthrough"]
            self.assertEqual(session["start_date"], "2026-09-29")
            result = self.complete(completion_date="2026-09-29")
            self.assertEqual(result["tracking"]["status"], Status.COMPLETED)
        self.assertEqual(timezone.get_current_timezone_name(), "UTC")

    def test_invalid_client_timezone_uses_default_without_leaking_request_state(self):
        with patch(
            "django.utils.timezone.now",
            return_value=datetime(2026, 9, 28, 2, 0, tzinfo=UTC),
        ):
            for invalid in ("not/a/timezone", "/etc/passwd", "../UTC"):
                self.client.credentials(HTTP_X_SPINE_TIMEZONE=invalid)
                session = self.action("start").data["game"]["current_playthrough"]
                self.assertEqual(session["start_date"], "2026-09-28")
                self.assertEqual(timezone.get_current_timezone_name(), "UTC")

    def test_completion_date_errors_identify_the_edited_date(self):
        start = self.today - timedelta(days=10)
        session = self.action("start", start_date=str(start)).data["game"][
            "current_playthrough"
        ]
        response = self.client.post(
            f"{self.url}complete/",
            {
                "completion_date": str(start - timedelta(days=1)),
                "mutation_id": str(uuid4()),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(
            "Completion date cannot be before the playthrough start date.",
            str(response.data),
        )
        self.assertFalse(DiaryEntry.objects.exists())
        self.assertEqual(self.state()["status"], Status.IN_PROGRESS)
        completed = self.complete(completion_date=str(start + timedelta(days=2)))
        response = self.client.patch(
            f"/api/v1/diary/{completed['diary_entry']['id']}/",
            {
                "consumed_at": str(start - timedelta(days=1)),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(
            "Completion date cannot be before the playthrough start date.",
            str(response.data),
        )
        self.assertEqual(
            self.state()["game"]["current_playthrough"]["end_date"],
            str(start + timedelta(days=2)),
        )
        response = self.client.patch(
            f"{self.url}playthroughs/{session['id']}/",
            {
                "start_date": str(start + timedelta(days=3)),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(
            "Start date cannot follow the first progress, completion, or drop date.",
            str(response.data),
        )
