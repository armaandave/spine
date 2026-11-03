"""The numbered manga and comic laws through the API.

docs/manga-comics-tracking-contract.md. Every test runs with no provider: the Items
are manual, or Comic Vine's are mocked. Date tests run with the server on
America/New_York, as production is.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import details_for_api
from api.services import stats_time, stats_v2_activity
from api.services.item_metadata import store_item_metadata, store_series_total
from api.services.stats_scope import build_scope
from app import series_tracking
from app.models import (
    Comic,
    ComicAttempt,
    DiaryEntry,
    Item,
    Manga,
    MangaAttempt,
    MediaLike,
    MediaTypes,
    Sources,
    Status,
)
from app.providers import comicvine
from events.models import Event
from lists.models import CustomList
from social.models import Activity, Follow, FollowStatus, ProgressChange

STARS = {"HTTP_X_SPINE_RATING_SCALE": "stars"}
READING = Status.IN_PROGRESS.value
READ = Status.COMPLETED.value


class SeriesContractBase(TestCase):
    """A signed-in reader, and series with and without totals."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reader", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.today = timezone.localdate()
        self.counter = 0

    def manga(self, total=380, title="Berserk"):
        return self.series(MediaTypes.MANGA.value, total, title)

    def comic(self, total=54, title="Saga"):
        return self.series(MediaTypes.COMIC.value, total, title)

    def series(self, media_type, total, title):
        self.counter += 1
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=f"{media_type}-{self.counter}",
            title=title,
            image="https://example.com/cover.jpg",
            series_total=total,
        )

    def url(self, item, suffix=""):
        return f"/api/v1/tracking/{item.source}/{item.media_type}/{item.media_id}/{suffix}"

    def write(self, item, expected=status.HTTP_200_OK, **data):
        response = self.client.patch(self.url(item), data, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response

    def action(self, item, name, expected=status.HTTP_200_OK, **data):
        response = self.client.post(self.url(item, f"actions/{name}/"), data, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response

    def save(self, item, value, expected=status.HTTP_200_OK, **data):
        response = self.client.post(self.url(item, "progress/"), {"value": value, **data}, format="json")
        self.assertEqual(response.status_code, expected, response.data)
        return response

    def log(self, item, expected=status.HTTP_201_CREATED, **data):
        payload = {
            "completion_date": str(data.pop("day", self.today)),
            "mutation_id": str(data.pop("mutation_id", uuid4())),
            **data,
        }
        response = self.client.post(self.url(item, "complete/"), payload, format="json", **STARS)
        self.assertEqual(response.status_code, expected, response.data)
        return response

    def row(self, item):
        return Item.objects.get(pk=item.pk) and (
            Manga if item.media_type == MediaTypes.MANGA.value else Comic
        ).objects.get(user=self.user, item=item)

    def activity(self):
        """Return (title, summary) for each item of the reader's activity."""
        results = self.client.get("/api/v1/users/reader/activity/").data["results"]
        return [
            (result["media"]["title"], result["object"].get("summary", "log"))
            for result in results
        ]


class InheritedRulesTests(SeriesContractBase):
    """MC-000: the book rules, on manga and comics."""

    # MC-000 (BK-001).
    def test_five_sections_one_section_per_series(self):
        for media_type in (MediaTypes.MANGA.value, MediaTypes.COMIC.value):
            for value in (Status.PLANNING.value, READING, Status.PAUSED.value, Status.DROPPED.value, READ):
                item = self.series(media_type, 20, f"{media_type} {value}")
                self.write(item, status=value)
                listed = self.client.get("/api/v1/tracking/", {"media_type": media_type, "status": value}).data
                self.assertIn(item.media_id, [row["media"]["ref"]["media_id"] for row in listed["results"]])
                self.assertEqual(
                    Item.objects.get(pk=item.pk).pk,
                    (Manga if media_type == "manga" else Comic).objects.get(user=self.user, item=item).item_id,
                )

    # MC-000 (BK-001): changing status moves the one record.
    def test_changing_status_moves_the_one_record(self):
        item = self.manga()
        self.write(item, status=Status.PLANNING.value)
        self.write(item, status=READING)
        self.action(item, "pause")
        self.action(item, "drop")
        self.assertEqual(Manga.objects.filter(user=self.user, item=item).count(), 1)
        self.assertEqual(self.row(item).status, Status.DROPPED.value)

    # MC-000 (BK-002): removal.
    def test_simple_removal_needs_nothing_real_and_keeps_custom_lists(self):
        item = self.manga()
        custom = CustomList.objects.create(owner=self.user, name="Favorites")
        custom.items.add(item)
        self.write(item, status=Status.PLANNING.value)
        self.assertEqual(self.client.delete(self.url(item)).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Manga.objects.filter(user=self.user, item=item).exists())
        self.assertTrue(custom.items.filter(pk=item.pk).exists())

    # MC-000 (BK-002): an attempt or a log blocks it.
    def test_removal_is_blocked_while_an_attempt_or_a_log_remains(self):
        attempt_item = self.manga()
        started = self.action(attempt_item, "start")
        self.assertFalse(started.data["series"]["can_remove_tracking"])
        self.assertEqual(self.client.delete(self.url(attempt_item)).status_code, status.HTTP_409_CONFLICT)
        logged = self.comic()
        self.log(logged)
        self.assertEqual(self.client.delete(self.url(logged)).status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Comic.objects.filter(user=self.user, item=logged).exists())

    # MC-000 (BK-100): the plus menu's actions.
    def test_plus_menu_offers_the_book_actions(self):
        item = self.manga()
        actions = self.write(item, status=Status.PLANNING.value).data["series"]["available_actions"]
        for name in ("currently_reading", "planning", "paused", "drop", "log", "mark_read"):
            self.assertIn(name, actions)
        self.write(item, status=READING)
        paused = self.action(item, "pause").data["series"]
        self.assertIn("resume", paused["available_actions"])

    # MC-000 (BK-101).
    def test_direct_statuses_invent_no_attempt_place_date_or_log(self):
        for value in (Status.PLANNING.value, Status.PAUSED.value, Status.DROPPED.value, READ):
            with self.subTest(status=value):
                item = self.manga(title=value)
                response = self.write(item, status=value)
                row = self.row(item)
                self.assertEqual(row.status, value)
                self.assertFalse(row.attempts.exists())
                self.assertEqual(row.progress, 0)
                self.assertIsNone(row.start_date)
                self.assertIsNone(row.end_date)
                self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())
                self.assertIsNone(response.data["series"]["current_journey"])

    # MC-000 (BK-102): the eye marks the series Read with no date and no log.
    def test_the_eye_marks_read_undated_and_the_filled_eye_undoes_only_that(self):
        item = self.comic()
        marked = self.action(item, "mark_read")
        self.assertEqual(marked.data["status"], READ)
        self.assertEqual(marked.data["series"]["status_source"], "undated_read")
        self.assertEqual(self.row(item).progress, 0)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())
        self.action(item, "undo_read", expected=status.HTTP_204_NO_CONTENT)
        self.assertFalse(Comic.objects.filter(user=self.user, item=item).exists())

    # MC-000 (BK-102): the undo restores what was there.
    def test_undoing_the_eye_restores_the_exact_status(self):
        item = self.manga()
        self.write(item, status=Status.PLANNING.value)
        self.action(item, "mark_read")
        restored = self.action(item, "undo_read")
        self.assertEqual(restored.data["status"], Status.PLANNING.value)

    # MC-000 (BK-102): during an attempt the eye opens the log screen.
    def test_the_eye_during_an_attempt_goes_through_the_log(self):
        item = self.manga()
        self.action(item, "start")
        refused = self.action(item, "mark_read", expected=status.HTTP_409_CONFLICT)
        self.assertEqual(refused.data["error"]["code"], "completion_required")
        self.assertEqual(self.row(item).status, READING)

    # MC-000 (BK-102): a filled eye from a log is not undone by the eye.
    def test_the_filled_eye_cannot_undo_a_log(self):
        item = self.manga()
        self.log(item)
        refused = self.action(item, "undo_read", expected=status.HTTP_409_CONFLICT)
        self.assertEqual(refused.data["error"]["code"], "history_exists")

    # MC-000 (BK-103): the eye follows the current status.
    def test_the_eye_is_the_current_status_and_older_history_keeps_it_undoable(self):
        item = self.manga()
        self.log(item, day=self.today - timedelta(days=30))
        self.write(item, status=Status.DROPPED.value)
        overridden = self.action(item, "mark_read")
        self.assertEqual(overridden.data["series"]["status_source"], "history_override")
        restored = self.action(item, "undo_read")
        self.assertEqual(restored.data["status"], Status.DROPPED.value)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=item).count(), 1)

    # MC-000 (BK-310): the undated Read stays manageable.
    def test_an_undated_read_can_be_deleted_from_reading_history(self):
        item = self.manga()
        self.action(item, "mark_read")
        self.action(item, "reread")
        state = self.save(item, 12).data["series"]
        self.assertEqual(state["undated_read"]["id"], "undated")
        deleted = self.action(item, "delete_undated_read")
        self.assertIsNone(deleted.data["series"]["undated_read"])
        self.assertEqual(deleted.data["status"], READING)
        self.assertEqual(self.row(item).progress, 12)

    # MC-000 (BK-104).
    def test_starting_is_one_tap_at_zero_with_today(self):
        item = self.manga()
        started = self.action(item, "start")
        self.assertEqual(started.data["status"], READING)
        journey = started.data["series"]["current_journey"]
        self.assertEqual(journey["start_date"], str(self.today))
        self.assertEqual(journey["progress"]["value"], 0)
        self.assertEqual(journey["origin"], "live")

    # MC-000 (BK-104).
    def test_to_read_is_unavailable_during_an_attempt(self):
        item = self.manga()
        state = self.action(item, "start").data["series"]
        self.assertIn("planning", state["action_reasons"])
        self.write(item, status=Status.PLANNING.value, expected=status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.row(item).status, READING)

    # MC-000 (BK-104): starting after a read with nothing more is a reread.
    def test_starting_after_read_with_nothing_more_is_a_reread(self):
        item = self.manga(total=10)
        self.log(item, day=self.today - timedelta(days=10))
        restarted = self.write(item, status=READING)
        self.assertTrue(restarted.data["series"]["is_rereading"])
        self.assertEqual(self.row(item).progress, 0)

    # MC-000 (BK-105): Log with nothing under way.
    def test_a_direct_log_creates_a_finished_attempt_and_its_log(self):
        item = self.manga()
        logged = self.log(item, rating="4.5", liked=True)
        row = self.row(item)
        self.assertEqual(row.status, READ)
        self.assertEqual(row.progress, 380)
        attempt = row.attempts.get()
        self.assertEqual(attempt.origin, MangaAttempt.Origin.DIRECT_LOG)
        self.assertEqual(attempt.completion_diary_entry_id, logged.data["diary_entry"]["id"])
        self.assertEqual(row.score, Decimal("9.0"))
        self.assertEqual(row.rating_source_id, attempt.completion_diary_entry_id)
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=item).exists())

    # MC-000 (BK-106): Log during an attempt finishes that attempt.
    def test_a_log_finishes_the_attempt_under_way(self):
        item = self.manga()
        journey_id = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.save(item, 200)
        self.log(item, journey_id=journey_id)
        row = self.row(item)
        attempt = row.attempts.get()
        self.assertEqual(attempt.pk, journey_id)
        self.assertEqual(attempt.status, READ)
        self.assertEqual(attempt.place, 380)

    # MC-000 (BK-106): a paused attempt too.
    def test_a_log_finishes_a_paused_attempt(self):
        item = self.comic()
        self.action(item, "start")
        self.action(item, "pause")
        self.log(item)
        self.assertEqual(self.row(item).attempts.get().status, READ)

    # MC-000 (BK-107).
    def test_a_log_after_did_not_finish_keeps_the_closed_attempt(self):
        item = self.manga()
        self.action(item, "start")
        self.save(item, 12)
        self.action(item, "drop")
        logged = self.log(item)
        self.assertFalse(logged.data["diary_entry"]["is_rewatch"], "Did Not Finish alone isn't a read")
        row = self.row(item)
        self.assertEqual(row.attempts.count(), 2)
        self.assertEqual(row.attempts.filter(status=Status.DROPPED.value).get().place, 12)
        self.assertEqual(logged.data["tracking"]["series"]["reading_history"][0]["status"], Status.DROPPED.value)

    # MC-000 (BK-200, BK-203): with nothing under way, stars and a heart mark it Read.
    def test_stars_and_a_heart_with_nothing_under_way_mark_read(self):
        starred = self.manga(title="starred")
        rated = self.client.patch(self.url(starred), {"rating": "4"}, format="json", **STARS)
        self.assertEqual(rated.status_code, status.HTTP_200_OK, rated.data)
        self.assertEqual(rated.data["status"], READ)
        self.assertEqual(rated.data["rating"], "4.0")
        self.assertEqual(self.row(starred).score, Decimal("8.0"))

        hearted = self.comic(title="hearted")
        like = self.client.post(
            "/api/v1/me/liked-media/",
            {"ref": {"source": hearted.source, "media_type": "comic", "media_id": hearted.media_id}},
            format="json",
        )
        self.assertEqual(like.status_code, status.HTTP_200_OK, like.data)
        self.assertEqual(self.row(hearted).status, READ)
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=hearted).exists())

    # MC-000 (BK-200): unhearting keeps Read.
    def test_unhearting_keeps_read(self):
        item = self.manga()
        ref = {"ref": {"source": item.source, "media_type": "manga", "media_id": item.media_id}}
        self.client.post("/api/v1/me/liked-media/", ref, format="json")
        unliked = self.client.delete("/api/v1/me/liked-media/", ref, format="json")
        self.assertEqual(unliked.status_code, status.HTTP_200_OK, unliked.data)
        self.assertEqual(self.row(item).status, READ)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

    # MC-000 (BK-300): one attempt under way.
    def test_one_attempt_is_under_way_at_a_time(self):
        item = self.manga()
        first = self.action(item, "start").data["series"]["current_journey"]["id"]
        second = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.assertEqual(first, second)
        self.assertEqual(self.row(item).attempts.count(), 1)

    # MC-000 (BK-303): pause keeps the place; resume continues it.
    def test_pause_keeps_the_place_and_resume_continues_the_same_attempt(self):
        item = self.comic()
        journey_id = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.save(item, 9)
        paused = self.action(item, "pause")
        self.assertEqual(paused.data["status"], Status.PAUSED.value)
        self.assertEqual(paused.data["progress"]["value"], 9)
        resumed = self.action(item, "resume")
        self.assertEqual(resumed.data["status"], READING)
        self.assertEqual(resumed.data["series"]["current_journey"]["id"], journey_id)
        self.assertEqual(resumed.data["progress"]["value"], 9)

    # MC-000 (BK-303): Currently Reading on a paused attempt is the same attempt.
    def test_currently_reading_continues_a_paused_attempt(self):
        item = self.comic()
        journey_id = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.action(item, "pause")
        again = self.write(item, status=READING)
        self.assertEqual(again.data["series"]["current_journey"]["id"], journey_id)
        self.assertEqual(ProgressChange.objects.filter(actor=self.user, item=item).count(), 1, "One start")

    # MC-000 (BK-303): retrospective Paused resumes as a new attempt at zero.
    def test_resuming_a_retrospective_pause_starts_at_zero(self):
        item = self.manga()
        self.write(item, status=Status.PAUSED.value)
        resumed = self.action(item, "resume")
        self.assertEqual(resumed.data["series"]["current_journey"]["progress"]["value"], 0)

    # MC-000 (BK-304).
    def test_restart_keeps_the_old_attempt_as_did_not_finish(self):
        item = self.manga()
        self.log(item, day=self.today - timedelta(days=60))
        self.write(item, status=Status.PLANNING.value)
        old = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.save(item, 40)
        restarted = self.action(item, "restart")
        state = restarted.data["series"]
        self.assertNotEqual(state["current_journey"]["id"], old)
        self.assertEqual(state["current_journey"]["progress"]["value"], 0)
        self.assertEqual(state["reading_history"][0]["id"], old)
        self.assertEqual(state["reading_history"][0]["status"], Status.DROPPED.value)
        self.assertEqual(state["reading_history"][0]["progress"]["value"], 40)
        self.assertTrue(state["is_rereading"])
        self.assertEqual(restarted.data["status"], READING)

    # MC-000 (BK-306).
    def test_dates_stay_in_order(self):
        item = self.manga()
        journey_id = self.action(item, "start", start_date=str(self.today - timedelta(days=5))).data["series"][
            "current_journey"
        ]["id"]
        self.save(item, 3)
        early = self.log(
            item,
            journey_id=journey_id,
            day=self.today - timedelta(days=6),
            expected=status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn("error", early.data)
        late_start = self.client.patch(
            self.url(item, f"journeys/{journey_id}/"),
            {"start_date": str(self.today + timedelta(days=1))},
            format="json",
        )
        self.assertEqual(late_start.status_code, status.HTTP_400_BAD_REQUEST)
        future = self.log(item, day=self.today + timedelta(days=1), expected=status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", future.data)
        self.assertEqual(self.row(item).status, READING)

    # MC-000 (BK-306): the start date can be edited.
    def test_a_start_date_can_be_edited(self):
        item = self.manga()
        journey_id = self.action(item, "start").data["series"]["current_journey"]["id"]
        day = self.today - timedelta(days=3)
        edited = self.client.patch(self.url(item, f"journeys/{journey_id}/"), {"start_date": str(day)}, format="json")
        self.assertEqual(edited.status_code, status.HTTP_200_OK, edited.data)
        self.assertEqual(edited.data["series"]["current_journey"]["start_date"], str(day))
        self.assertEqual(self.row(item).start_date.date(), day)

    # MC-000 (BK-307): Reading History lists closed attempts.
    def test_reading_history_lists_closed_attempts_but_not_logged_ones(self):
        item = self.manga()
        self.action(item, "start")
        self.save(item, 9)
        self.action(item, "drop")
        self.log(item)
        history = self.client.get(self.url(item)).data["series"]["reading_history"]
        self.assertEqual([row["status"] for row in history], [Status.DROPPED.value])
        self.assertEqual(history[0]["end_date"], str(self.today))
        self.assertEqual(history[0]["progress"]["value"], 9)

    # MC-000 (BK-308): an older closed attempt can be deleted.
    def test_deleting_an_older_closed_attempt_changes_nothing_else(self):
        item = self.manga()
        closed = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.save(item, 9)
        self.action(item, "drop")
        self.log(item)
        response = self.client.delete(self.url(item, f"journeys/{closed}/"))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], READ)
        self.assertFalse(ProgressChange.objects.filter(actor=self.user, item=item).exists(), "Its numbers go too")

    # MC-000 (BK-308): the current closed attempt goes back to what was there.
    def test_deleting_the_current_closed_attempt_restores_the_earlier_status(self):
        item = self.manga()
        self.write(item, status=Status.PLANNING.value)
        closed = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.action(item, "drop")
        response = self.client.delete(self.url(item, f"journeys/{closed}/"))
        self.assertEqual(response.data["status"], Status.PLANNING.value)

    # MC-000 (BK-309).
    def test_deleting_the_attempt_under_way_restores_the_earlier_status(self):
        untracked = self.manga(title="untracked")
        attempt = self.action(untracked, "start").data["series"]["current_journey"]["id"]
        response = self.client.delete(self.url(untracked, f"journeys/{attempt}/"))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Manga.objects.filter(user=self.user, item=untracked).exists())

        read = self.manga(title="read")
        self.log(read, day=self.today - timedelta(days=4))
        attempt = self.action(read, "reread").data["series"]["current_journey"]["id"]
        response = self.client.delete(self.url(read, f"journeys/{attempt}/"))
        self.assertEqual(response.data["status"], READ)
        self.assertEqual(response.data["progress"]["value"], 380)

    # MC-000 (BK-400).
    def test_a_log_and_the_finished_attempt_save_together(self):
        item = self.manga()
        self.action(item, "start")
        self.save(item, 300)
        logged = self.log(item, rating="3.5", review="Great", tags=["Seinen"])
        entry = DiaryEntry.objects.get(pk=logged.data["diary_entry"]["id"])
        self.assertEqual(entry.rating, Decimal("7.0"))
        self.assertEqual(entry.review, "Great")
        self.assertEqual([tag.name for tag in entry.tags.all()], ["seinen"])
        self.assertEqual(logged.data["tracking"]["status"], READ)
        self.assertEqual(logged.data["tracking"]["progress"]["value"], 380)
        self.assertEqual(self.row(item).attempts.get().completion_diary_entry_id, entry.pk)

    # MC-000 (BK-401): a failed save changes nothing; cancelling sends nothing.
    def test_a_failed_log_leaves_the_attempt_unchanged(self):
        item = self.comic()
        self.action(item, "start")
        self.save(item, 7)
        self.log(item, day=self.today + timedelta(days=2), expected=status.HTTP_400_BAD_REQUEST)
        row = self.row(item)
        self.assertEqual(row.status, READING)
        self.assertEqual(row.progress, 7)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())

    # MC-000 (BK-403): deleting the log restores the attempt.
    def test_deleting_the_log_reopens_the_attempt_exactly(self):
        for status_before in (READING, Status.PAUSED.value):
            with self.subTest(status_before=status_before):
                item = self.manga(title=status_before)
                journey_id = self.action(item, "start").data["series"]["current_journey"]["id"]
                self.save(item, 120)
                if status_before == Status.PAUSED.value:
                    self.action(item, "pause")
                logged = self.log(item, rating="5", liked=True)
                response = self.client.delete(f"/api/v1/diary/{logged.data['diary_entry']['id']}/")
                self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
                row = self.row(item)
                self.assertEqual(row.status, status_before)
                self.assertEqual(row.progress, 120)
                self.assertIsNone(row.score)
                self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())
                attempt = row.attempts.get()
                self.assertEqual(attempt.pk, journey_id)
                self.assertEqual(attempt.status, status_before)
                self.assertEqual(attempt.place, 120)
                self.assertIsNone(attempt.end_date)
                self.assertEqual(
                    ProgressChange.objects.filter(actor=self.user, item=item, current_progress__unit="chapter").count(),
                    1,
                    "Its saved numbers stay",
                )

    # MC-000 (BK-403): an older log goes without disturbing newer state.
    def test_deleting_an_older_log_leaves_newer_state_alone(self):
        item = self.manga()
        older = self.log(item, day=self.today - timedelta(days=30))
        newer = self.log(item, day=self.today - timedelta(days=2))
        self.client.delete(f"/api/v1/diary/{older.data['diary_entry']['id']}/")
        row = self.row(item)
        self.assertEqual(row.status, READ)
        self.assertEqual(row.attempts.get().completion_diary_entry_id, newer.data["diary_entry"]["id"])

    # MC-000 (BK-403): editing a log's date moves its attempt's end.
    def test_editing_a_logs_date_moves_the_attempt(self):
        item = self.manga()
        journey_id = self.action(item, "start", start_date=str(self.today - timedelta(days=9))).data["series"][
            "current_journey"
        ]["id"]
        logged = self.log(item, journey_id=journey_id)
        day = self.today - timedelta(days=4)
        edited = self.client.patch(f"/api/v1/diary/{logged.data['diary_entry']['id']}/", {"consumed_at": str(day)}, format="json")
        self.assertEqual(edited.status_code, status.HTTP_200_OK, edited.data)
        self.assertEqual(self.row(item).attempts.get().end_date, day)
        self.assertEqual(self.row(item).end_date.date(), day)
        refused = self.client.patch(
            f"/api/v1/diary/{logged.data['diary_entry']['id']}/",
            {"consumed_at": str(self.today - timedelta(days=20))},
            format="json",
        )
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)

    # MC-000 (BK-404): a direct log's deletion restores what was before it.
    def test_deleting_a_direct_log_restores_what_was_there(self):
        untracked = self.comic(title="untracked")
        logged = self.log(untracked)
        self.client.delete(f"/api/v1/diary/{logged.data['diary_entry']['id']}/")
        self.assertFalse(Comic.objects.filter(user=self.user, item=untracked).exists())

        planned = self.comic(title="planned")
        self.write(planned, status=Status.PLANNING.value)
        logged = self.log(planned)
        self.client.delete(f"/api/v1/diary/{logged.data['diary_entry']['id']}/")
        self.assertEqual(self.row(planned).status, Status.PLANNING.value)

    # MC-000 (BK-405): Read counts unique series, each log one read.
    def test_read_counts_unique_series_and_each_log_one_read(self):
        item = self.manga()
        self.log(item, day=self.today - timedelta(days=9))
        self.action(item, "reread")
        state = self.log(item).data["tracking"]["series"]
        self.assertEqual(state["completed_journey_count"], 2)
        self.assertEqual(state["lifetime_read_count"], 2)
        read = self.client.get("/api/v1/tracking/", {"media_type": "manga", "status": READ}).data
        self.assertEqual(read["count"], 1)

    # MC-000 (BK-406): reread is derived from chronology and the toggle is display only.
    def test_reread_is_derived_and_the_toggle_is_display_only(self):
        item = self.manga()
        first = self.log(item, day=self.today - timedelta(days=9))
        self.assertFalse(first.data["diary_entry"]["is_rewatch"])
        second = self.log(item, is_rewatch=False)
        self.assertFalse(second.data["diary_entry"]["is_rewatch"])
        self.assertEqual(second.data["tracking"]["series"]["lifetime_read_count"], 2)
        journey = self.row(item).attempts.order_by("-end_date").first()
        payload = series_tracking._journey_payload(self.row(item), journey, list(self.row(item).attempts.all()))
        self.assertTrue(payload["is_reread"])

    # MC-000 (SW-100 to SW-108): each log keeps its own stars and heart.
    def test_each_log_keeps_its_own_opinion_and_the_new_one_is_the_source(self):
        item = self.manga()
        first = self.log(item, rating="3", liked=True, day=self.today - timedelta(days=9))
        second = self.log(item, rating="4.5", liked=False)
        self.assertEqual(DiaryEntry.objects.get(pk=first.data["diary_entry"]["id"]).rating, Decimal("6.0"))
        self.assertTrue(DiaryEntry.objects.get(pk=first.data["diary_entry"]["id"]).liked)
        row = self.row(item)
        self.assertEqual(row.score, Decimal("9.0"))
        self.assertEqual(row.rating_source_id, second.data["diary_entry"]["id"])
        self.assertEqual(row.like_source_id, second.data["diary_entry"]["id"])
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

    # MC-000 (SW-104, SW-105): the source log moves current; a direct change decouples.
    def test_editing_the_source_log_updates_current_and_a_direct_change_decouples(self):
        item = self.manga()
        logged = self.log(item, rating="3")
        entry_id = logged.data["diary_entry"]["id"]
        edit = self.client.patch(f"/api/v1/diary/{entry_id}/", {"rating": "4"}, format="json", **STARS)
        self.assertEqual(edit.status_code, status.HTTP_200_OK, edit.data)
        self.assertEqual(self.row(item).score, Decimal("8.0"))
        self.client.patch(self.url(item), {"rating": "1"}, format="json", **STARS)
        row = self.row(item)
        self.assertEqual(row.score, Decimal("2.0"))
        self.assertIsNone(row.rating_source_id)
        self.client.patch(f"/api/v1/diary/{entry_id}/", {"rating": "5"}, format="json", **STARS)
        self.assertEqual(self.row(item).score, Decimal("2.0"))

    # MC-000 (SW-108): a log given a rating fills an empty current one.
    def test_a_rated_log_fills_an_empty_current_rating(self):
        item = self.manga()
        first = self.log(item, day=self.today - timedelta(days=9), rating="3")
        second = self.log(item)
        self.assertEqual(self.row(item).rating_source_id, first.data["diary_entry"]["id"])
        self.client.patch(self.url(item), {"rating": None}, format="json", **STARS)
        self.client.patch(f"/api/v1/diary/{second.data['diary_entry']['id']}/", {"rating": "4"}, format="json", **STARS)
        row = self.row(item)
        self.assertEqual(row.score, Decimal("8.0"))
        self.assertEqual(row.rating_source_id, second.data["diary_entry"]["id"])

    # MC-000 (SW-007): distinct same-day logs are allowed.
    def test_two_logs_on_one_day_are_allowed(self):
        item = self.manga()
        self.log(item)
        self.log(item)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=item).count(), 2)

    # MC-000 (BK-501): retries and double taps.
    def test_retries_create_no_duplicates(self):
        item = self.manga()
        mutation = uuid4()
        first = self.action(item, "start", mutation_id=str(mutation)).data["series"]["current_journey"]["id"]
        self.action(item, "pause")
        retried = self.action(item, "start", mutation_id=str(mutation))
        self.assertEqual(retried.data["series"]["current_journey"]["id"], first)
        self.assertEqual(retried.data["status"], Status.PAUSED.value, "The retried start applied once")

        log_mutation = uuid4()
        one = self.log(item, mutation_id=log_mutation, journey_id=first)
        two = self.log(item, mutation_id=log_mutation)
        self.assertEqual(one.data["diary_entry"]["id"], two.data["diary_entry"]["id"])
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=item).count(), 1)
        self.assertEqual(self.row(item).attempts.count(), 1)

    # MC-000 (BK-501): related changes succeed together.
    def test_a_status_and_a_refused_number_leave_nothing_behind(self):
        item = self.manga(total=10)
        self.write(item, status=READING, progress=11, expected=status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Manga.objects.filter(user=self.user, item=item).exists())
        self.assertFalse(ProgressChange.objects.filter(actor=self.user, item=item).exists())

    # MC-000 (BK-500): every surface agrees.
    def test_the_library_detail_and_list_agree(self):
        item = self.manga()
        self.action(item, "start")
        self.save(item, 120)
        detail = self.client.get(self.url(item)).data
        listed = self.client.get("/api/v1/tracking/", {"media_type": "manga", "status": READING}).data["results"][0]
        self.assertEqual(detail["status"], listed["tracking"]["status"])
        self.assertEqual(detail["progress"], listed["tracking"]["progress"])
        self.assertEqual(detail["series"]["current_journey"]["id"], listed["tracking"]["series"]["current_journey"]["id"])
        media = self.client.get(f"/api/v1/media/manual/manga/{item.media_id}/")
        self.assertEqual(media.status_code, status.HTTP_200_OK, media.data)
        self.assertEqual(media.data["user_state"]["series"]["current_journey"]["id"], detail["series"]["current_journey"]["id"])

    # MC-000 (AC-1): started reading.
    def test_activity_says_started_reading_once_and_not_for_resume(self):
        item = self.manga()
        self.action(item, "start")
        self.action(item, "pause")
        self.action(item, "resume")
        self.assertEqual(self.activity(), [("Berserk", "started reading")])
        self.assertEqual(
            ProgressChange.objects.filter(actor=self.user, item=item, current_progress__kind=ProgressChange.START).count(),
            1,
        )

    # MC-000 (AC-1, AC-2): chapters and issues.
    def test_activity_tells_chapters_and_issues(self):
        manga = self.manga()
        self.action(manga, "start")
        self.save(manga, 5)
        comic = self.comic()
        self.action(comic, "start")
        self.save(comic, 2)
        self.assertCountEqual(self.activity(), [("Berserk", "read 5 chapters"), ("Saga", "read 2 issues")])

    # MC-000 (AC-1): marks are never activity.
    def test_marks_are_never_activity(self):
        marked = self.manga(title="eye")
        self.action(marked, "mark_read")
        starred = self.manga(title="stars")
        self.client.patch(self.url(starred), {"rating": "4"}, format="json", **STARS)
        status_only = self.comic(title="status")
        self.write(status_only, status=Status.DROPPED.value)
        self.assertEqual(self.activity(), [])
        self.assertFalse(ProgressChange.objects.filter(actor=self.user).exists())

    # MC-000 (AC-2): progress replaces that day's start, and a log replaces neither.
    def test_a_log_and_that_days_progress_are_separate_items(self):
        saved = self.manga(title="Saved")
        self.action(saved, "start")
        self.save(saved, 5)
        self.log(saved)
        started = self.comic(title="Started")
        self.action(started, "start")
        self.log(started)
        self.assertCountEqual(
            self.activity(),
            [
                ("Saved", "log"),
                ("Saved", "read 5 chapters"),
                ("Started", "log"),
                ("Started", "started reading"),
            ],
        )

    # MC-000 (AC-6): a day's progress and a log both take likes and comments.
    def test_progress_and_logs_take_likes_and_comments(self):
        friend = get_user_model().objects.create_user(username="friend", password="strong-password-123")
        Follow.objects.create(from_user=friend, to_user=self.user, status=FollowStatus.ACCEPTED)
        other = APIClient()
        other.force_authenticate(friend)
        item = self.manga()
        self.action(item, "start")
        self.save(item, 5)
        [day] = other.get("/api/v1/feed/").data["results"]
        self.assertEqual(day["object"]["summary"], "read 5 chapters")
        path = f"/api/v1/progress/{day['object']['id']}/"
        self.assertEqual(other.post(f"{path}like/").status_code, status.HTTP_200_OK)
        commented = other.post(f"{path}comments/", {"body": "Keep going"})
        self.assertEqual(commented.status_code, status.HTTP_201_CREATED)
        # Later progress that day keeps them.
        self.save(item, 9)
        [day] = other.get("/api/v1/feed/").data["results"]
        self.assertEqual(day["object"]["summary"], "read 9 chapters")
        self.assertEqual((day["like_count"], day["comment_count"]), (1, 1))
        self.assertTrue(day["viewer"]["has_liked"])
        # A log has its own, and deleting it takes only its own.
        entry_id = self.log(item).data["diary_entry"]["id"]
        path = f"/api/v1/diary/{entry_id}/"
        self.assertEqual(other.post(f"{path}like/").status_code, status.HTTP_200_OK)
        self.assertEqual(other.post(f"{path}comments/", {"body": "Done!"}).status_code, status.HTTP_201_CREATED)
        results = other.get("/api/v1/feed/").data["results"]
        self.assertEqual(
            sorted((result["type"], result["like_count"], result["comment_count"]) for result in results),
            [("diary_created", 1, 1), ("progress_updated", 1, 1)],
        )
        self.assertEqual(self.client.delete(path).status_code, status.HTTP_204_NO_CONTENT)
        [day] = other.get("/api/v1/feed/").data["results"]
        self.assertEqual((day["type"], day["like_count"], day["comment_count"]), ("progress_updated", 1, 1))

    # MC-000 (AC-5): manga and comic logs keep their own visibility.
    def test_logs_keep_their_own_visibility(self):
        item = self.manga()
        logged = self.log(item, visibility="private")
        self.assertEqual(logged.data["diary_entry"]["visibility"], "private")
        stranger = get_user_model().objects.create_user(username="stranger", password="strong-password-123")
        other = APIClient()
        other.force_authenticate(stranger)
        results = other.get("/api/v1/users/reader/activity/").data["results"]
        self.assertEqual(results, [])
        activity = Activity.objects.get(target_type="diary", target_id=logged.data["diary_entry"]["id"])
        self.assertEqual(activity.visibility, "private")
        edited = self.client.patch(f"/api/v1/diary/{logged.data['diary_entry']['id']}/", {"visibility": "public"}, format="json")
        self.assertEqual(edited.data["visibility"], "public")
        self.assertEqual(other.get("/api/v1/users/reader/activity/").data["results"][0]["id"], logged.data["diary_entry"]["id"])


class CountingTests(SeriesContractBase):
    """MC-001 to MC-007."""

    # MC-001: a whole number, no volumes, pages or percent.
    def test_the_number_is_a_whole_number(self):
        item = self.manga()
        self.action(item, "start")
        for value in ("10.5", 10.5, -1, "many", True):
            with self.subTest(value=value):
                self.save(item, value, expected=status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.save(item, 10).data["progress"], {"kind": "count", "value": 10, "max": 380, "unit": "chapter"})
        self.assertEqual(self.row(item).progress, 10)

    # MC-001: comics count issues.
    def test_comics_count_issues(self):
        item = self.comic()
        self.action(item, "start")
        self.assertEqual(self.save(item, 12).data["progress"], {"kind": "count", "value": 12, "max": 54, "unit": "issue"})

    # MC-002: Comic Vine's count, never events or the last issue's number.
    def test_a_comics_total_is_the_issue_count_not_events(self):
        item = self.comic(total=54)
        Event.objects.create(item=item, content_number=70, datetime=timezone.now() - timedelta(days=1))
        self.action(item, "start")
        self.assertEqual(self.client.get(self.url(item)).data["progress"]["max"], 54)
        row = self.client.get("/api/v1/tracking/", {"media_type": "comic", "status": READING}).data["results"][0]
        self.assertEqual(row["tracking"]["progress"]["max"], 54)
        self.assertEqual(row["tracking"]["series"]["total"], 54)

    # MC-002: the provider's total is its count.
    @patch("app.providers.comicvine.services.api_request")
    def test_comic_vine_gives_the_count_as_the_total(self, api_request):
        api_request.return_value = {
            "results": {
                "site_detail_url": "https://comicvine.gamespot.com/saga/4050-1/",
                "name": "Saga",
                "last_issue": {"id": 9, "issue_number": "66", "name": "Last"},
                "count_of_issues": 54,
                "date_last_updated": "2026-10-01 00:00:00",
            },
        }
        cache.delete("comicvine_comic_mc-002")
        data = comicvine.comic("mc-002")
        self.assertEqual(data["max_progress"], 54)
        self.assertEqual(data["max_issue_number"], 66)
        item = self.comic(total=None)
        store_series_total(item, data)
        self.assertEqual(Item.objects.get(pk=item.pk).series_total, 54)

    # MC-003: no total.
    def test_with_no_total_any_number_is_accepted_and_there_is_no_max(self):
        item = self.manga(total=None, title="One Piece")
        self.action(item, "start")
        saved = self.save(item, 1100)
        self.assertEqual(saved.data["progress"], {"kind": "count", "value": 1100, "max": None, "unit": "chapter"})
        self.assertIsNone(saved.data["series"]["total"])

    # MC-003: a total that appears later applies at once.
    def test_a_total_that_appears_later_applies_at_once(self):
        item = self.manga(total=None)
        self.action(item, "start")
        self.save(item, 120)
        store_item_metadata(Item.objects.get(pk=item.pk), {"max_progress": 380})
        self.assertEqual(self.client.get(self.url(item)).data["progress"]["max"], 380)

    # MC-003: providers that list none never erase what is stored.
    def test_a_provider_with_no_total_leaves_what_is_stored(self):
        item = self.manga(total=380)
        store_item_metadata(item, {"max_progress": None, "details": {}})
        self.assertEqual(Item.objects.get(pk=item.pk).series_total, 380)

    # MC-004: refused, naming the total.
    def test_a_number_past_a_known_total_is_refused(self):
        item = self.manga(total=380)
        self.action(item, "start")
        refused = self.save(item, 381, expected=status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refused.data["error"]["message"], "This manga has 380 chapters.")
        self.assertEqual(self.row(item).progress, 0)
        self.assertFalse(ProgressChange.objects.filter(actor=self.user, item=item, current_progress__unit="chapter").exists())

    # MC-004: a comic re-reads Comic Vine's count once, finding the new issue.
    @patch("app.series_tracking.comicvine.fresh_issue_count")
    def test_a_comic_rereads_the_count_before_refusing(self, count):
        item = Item.objects.create(
            source=Sources.COMICVINE.value,
            media_type="comic",
            media_id="46568",
            title="Saga",
            image="https://example.com/saga.jpg",
            series_total=54,
        )
        Comic.objects.create(user=self.user, item=item, status=Status.PLANNING.value)
        self.action(item, "start")
        count.return_value = 55
        accepted = self.save(item, 55)
        count.assert_called_once_with("46568")
        self.assertEqual(accepted.data["progress"]["value"], 55)
        self.assertEqual(accepted.data["progress"]["max"], 55)

    # MC-004: only past the count, and still refused when there's no new issue.
    @patch("app.series_tracking.comicvine.fresh_issue_count")
    def test_a_comic_past_the_fresh_count_is_refused_and_a_failed_read_refuses_too(self, count):
        item = Item.objects.create(
            source=Sources.COMICVINE.value,
            media_type="comic",
            media_id="46569",
            title="Saga",
            image="https://example.com/saga.jpg",
            series_total=54,
        )
        Comic.objects.create(user=self.user, item=item, status=Status.PLANNING.value)
        self.action(item, "start")
        self.save(item, 54)
        count.assert_not_called()
        count.return_value = 54
        refused = self.save(item, 60, expected=status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refused.data["error"]["message"], "This comic has 54 issues so far.")
        self.assertEqual(count.call_count, 1)
        count.side_effect = comicvine.services.ProviderAPIError("comicvine", None)
        self.save(item, 60, expected=status.HTTP_400_BAD_REQUEST)

    # MC-005: a one-issue comic has status actions and no number.
    def test_a_total_of_one_has_no_number(self):
        for item in (self.comic(total=1, title="One Shot"), self.manga(total=1, title="One Chapter")):
            with self.subTest(item=item.title):
                state = self.action(item, "start").data["series"]
                self.assertEqual(state["total"], 1)
                self.assertNotIn("update_progress", state["available_actions"])
                for action in ("finish", "restart", "delete_journey", "pause", "log", "drop"):
                    self.assertIn(action, state["available_actions"])

    # MC-005: a one-issue comic still logs and marks.
    def test_a_one_issue_comic_finishes_through_its_log(self):
        item = self.comic(total=1, title="One Shot")
        self.action(item, "start")
        logged = self.log(item)
        self.assertEqual(logged.data["tracking"]["progress"]["value"], 1)
        self.assertFalse(logged.data["tracking"]["series"]["offers_keep_reading"])

    # MC-006: what posters read comes from this shape.
    def test_posters_read_the_number_and_the_total(self):
        item = self.manga()
        self.action(item, "start")
        self.save(item, 120)
        self.action(item, "drop")
        row = self.client.get("/api/v1/tracking/", {"media_type": "manga", "status": Status.DROPPED.value}).data["results"][0]
        self.assertEqual(row["tracking"]["progress"], {"kind": "count", "value": 120, "max": 380, "unit": "chapter"})
        self.assertEqual(row["tracking"]["status"], Status.DROPPED.value)


@override_settings(TIME_ZONE="America/New_York")
class CountingDaysBase(SeriesContractBase):
    """Writes at a chosen moment on the server's calendar: America/New_York."""

    def at(self, day, hour):
        return datetime(2026, 10, day, hour, tzinfo=UTC)

    def counted(self, day):
        scope = build_scope(self.user, self.user, {"start_date": day, "end_date": day, "schema": "2"})
        return stats_time.type_time(scope, "manga").value

    def setUp(self):
        with timezone.override("America/New_York"):
            super().setUp()
        self.item = self.manga()
        override = timezone.override("America/New_York")
        override.__enter__()
        self.addCleanup(override.__exit__, None, None, None)

    def write_at(self, moment, call, *args, **kwargs):
        with patch("django.utils.timezone.now", return_value=moment):
            return call(*args, **kwargs)


class CountingDaysTests(CountingDaysBase):
    """MC-007, on the server's calendar: America/New_York."""

    # MC-007: each save counts on the day it was saved, the local day.
    def test_a_save_counts_on_its_local_day(self):
        self.write_at(self.at(20, 12), self.action, self.item, "start")
        # 01:30 UTC on the 21st is 21:30 on the 20th in New York.
        self.write_at(self.at(21, 1), self.save, self.item, 30)
        self.assertEqual(self.counted("2026-10-20"), 30)
        self.assertEqual(self.counted("2026-10-21"), 0)
        activity = self.write_at(self.at(22, 12), self.client.get, "/api/v1/users/reader/activity/").data["results"][0]
        self.assertEqual(activity["consumed_at"], "2026-10-20")
        self.assertEqual(activity["object"]["summary"], "read 30 chapters")

    # MC-007: the first save counts whole, and typing 1,100 counts 1,100 that day.
    def test_the_first_save_counts_whole_even_if_it_is_catch_up(self):
        one_piece = self.manga(total=None, title="One Piece")
        self.write_at(self.at(20, 12), self.action, one_piece, "start")
        self.write_at(self.at(20, 13), self.save, one_piece, 1100)
        self.assertEqual(self.counted("2026-10-20"), 1100)
        summary = self.write_at(self.at(20, 14), self.client.get, "/api/v1/users/reader/activity/").data["results"][0]
        self.assertEqual(summary["object"]["summary"], "read 1100 chapters")

    # MC-007: lowering a number fixes a typo; it isn't a new read.
    def test_lowering_a_number_fixes_a_typo(self):
        self.write_at(self.at(20, 12), self.action, self.item, "start")
        self.write_at(self.at(20, 13), self.save, self.item, 50)
        self.write_at(self.at(20, 14), self.save, self.item, 40)
        self.assertEqual(self.row(self.item).progress, 40)
        self.assertEqual(self.row(self.item).attempts.count(), 1)
        summary = self.write_at(self.at(20, 15), self.client.get, "/api/v1/users/reader/activity/").data["results"][0]
        self.assertEqual(summary["object"]["summary"], "read 40 chapters", "The day's net change")

    # MC-007: going down all day shows nothing new.
    def test_a_day_that_only_goes_down_shows_no_progress(self):
        self.write_at(self.at(20, 12), self.action, self.item, "start")
        self.write_at(self.at(20, 13), self.save, self.item, 50)
        self.write_at(self.at(22, 13), self.save, self.item, 40)
        results = self.write_at(self.at(22, 14), self.client.get, "/api/v1/users/reader/activity/").data["results"]
        self.assertEqual([row["consumed_at"] for row in results], ["2026-10-20"])

    # MC-007: a log adds only what is left, on the log's day.
    def test_a_log_adds_only_what_is_left(self):
        self.write_at(self.at(20, 12), self.action, self.item, "start")
        self.write_at(self.at(20, 13), self.save, self.item, 300)
        self.write_at(self.at(21, 13), self.log, self.item, day=date(2026, 10, 21))
        self.assertEqual(self.counted("2026-10-20"), 300)
        self.assertEqual(self.counted("2026-10-21"), 80)

    # MC-007: a backdated log still counts what's left, without losing it.
    def test_a_backdated_direct_log_counts_on_its_day(self):
        self.write_at(self.at(21, 13), self.log, self.item, day=date(2026, 10, 18))
        self.assertEqual(self.counted("2026-10-18"), 380)
        self.assertEqual(self.counted("2026-10-21"), 0)
        entry = DiaryEntry.objects.get(user=self.user, item=self.item)
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 18, tzinfo=UTC))
        activity = self.write_at(self.at(21, 14), self.client.get, "/api/v1/users/reader/activity/").data["results"][0]
        self.assertEqual(activity["consumed_at"], "2026-10-18", "A log stays on its day west of UTC")

    # MC-007: a reread counts again.
    def test_a_reread_counts_again(self):
        self.write_at(self.at(20, 13), self.log, self.item, day=date(2026, 10, 20))
        self.write_at(self.at(21, 13), self.action, self.item, "reread")
        self.write_at(self.at(21, 14), self.save, self.item, 25)
        self.assertEqual(self.counted("2026-10-21"), 25)
        self.assertEqual(self.row(self.item).progress, 25)

    # MC-007: the eye is undated.
    def test_the_eye_counts_on_no_day(self):
        self.write_at(self.at(20, 13), self.action, self.item, "mark_read")
        self.assertEqual(self.counted("2026-10-20"), 0)
        self.assertEqual(self.write_at(self.at(20, 14), self.client.get, "/api/v1/users/reader/activity/").data["results"], [])

    # MC-000 (SW-006): dates are calendar dates on the user's calendar.
    def test_an_evening_start_and_log_stay_on_the_local_day(self):
        started = self.write_at(self.at(21, 1), self.action, self.item, "start")
        self.assertEqual(started.data["series"]["current_journey"]["start_date"], "2026-10-20")
        self.write_at(self.at(21, 2), self.action, self.item, "pause")
        logged = self.write_at(self.at(21, 3), self.log, self.item, day=date(2026, 10, 20))
        self.assertEqual(self.row(self.item).end_date, datetime(2026, 10, 20, tzinfo=UTC))
        self.assertEqual(logged.data["diary_entry"]["consumed_at"], datetime(2026, 10, 20, tzinfo=UTC))
        future = self.write_at(self.at(21, 3), self.log, self.manga(), day=date(2026, 10, 21), expected=status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", future.data)

    # MC-007, MC-500: an installed build's datetime is read on the user's calendar.
    def test_an_installed_builds_log_datetime_lands_on_the_local_day(self):
        response = self.write_at(
            self.at(21, 1),
            self.client.post,
            "/api/v1/diary/",
            {
                "ref": {"source": "manual", "media_type": "manga", "media_id": self.item.media_id},
                "consumed_at": "2026-10-21T01:00:00Z",
                "auto_mark_consumed": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(DiaryEntry.objects.get(pk=response.data["id"]).consumed_at, datetime(2026, 10, 20, tzinfo=UTC))


class FinishingTests(SeriesContractBase):
    """MC-100 to MC-104."""

    # MC-100: reaching the total saves, then opens the log screen.
    def test_reaching_the_total_opens_the_log_screen_and_finishes_nothing(self):
        item = self.manga(total=380)
        self.action(item, "start")
        below = self.save(item, 379)
        self.assertFalse(below.data["series"]["opens_log"])
        reached = self.save(item, 380)
        self.assertTrue(reached.data["series"]["opens_log"])
        self.assertEqual(reached.data["status"], READING, "Not now leaves it Currently Reading")
        self.assertEqual(reached.data["progress"]["value"], 380)
        row = self.row(item)
        self.assertEqual(row.status, READING)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())

    # MC-100: saving the log finishes it.
    def test_saving_the_log_finishes_the_attempt(self):
        item = self.manga(total=380)
        self.action(item, "start")
        self.save(item, 380)
        logged = self.log(item)
        self.assertEqual(logged.data["tracking"]["status"], READ)

    # MC-100: a row saved directly never auto-completes, whatever the provider says.
    @patch("app.providers.services.get_media_metadata", return_value={"max_progress": 10})
    def test_saving_a_row_never_finishes_it(self, metadata):
        item = self.manga(total=10)
        row = Manga.objects.create(user=self.user, item=item, status=READING, progress=3)
        row.progress = 10
        row.save()
        row.refresh_from_db()
        self.assertEqual(row.status, READING)
        self.assertIsNone(row.end_date)
        metadata.assert_not_called()

    # MC-101: once per attempt.
    def test_the_log_screen_opens_by_itself_once_per_attempt(self):
        item = self.comic(total=10)
        self.action(item, "start")
        self.assertTrue(self.save(item, 10).data["series"]["opens_log"])
        self.save(item, 9)
        self.assertFalse(self.save(item, 10).data["series"]["opens_log"], "Going down and back opens nothing")
        Item.objects.filter(pk=item.pk).update(series_total=11)
        self.assertFalse(self.save(item, 11).data["series"]["opens_log"], "A monthly run opens it once")
        self.assertIn("finish", self.client.get(self.url(item)).data["series"]["available_actions"])

    # MC-100, MC-101: a retry after a lost answer still opens the log screen.
    def test_a_retried_save_at_the_total_still_opens_the_log_screen(self):
        item = self.comic(total=10)
        self.action(item, "start")
        mutation_id = str(uuid4())
        self.assertTrue(self.save(item, 10, mutation_id=mutation_id).data["series"]["opens_log"])
        changes = ProgressChange.objects.filter(actor=self.user, item=item).count()
        self.assertTrue(self.save(item, 10, mutation_id=mutation_id).data["series"]["opens_log"], "The retry")
        self.assertEqual(ProgressChange.objects.filter(actor=self.user, item=item).count(), changes)
        self.assertFalse(self.save(item, 10, mutation_id=str(uuid4())).data["series"]["opens_log"], "Another save")

    # MC-101: a new attempt can open it again.
    def test_a_new_attempt_opens_it_again(self):
        item = self.comic(total=10)
        self.action(item, "start")
        self.save(item, 10)
        self.log(item)
        self.action(item, "reread")
        self.assertTrue(self.save(item, 10).data["series"]["opens_log"])

    # MC-102: no total never opens it, and the log takes the number.
    def test_with_no_total_only_the_user_finishes_and_the_log_takes_the_number(self):
        item = self.manga(total=None, title="One Piece")
        self.action(item, "start")
        self.assertFalse(self.save(item, 1100).data["series"]["opens_log"])
        logged = self.log(item, place=1120)
        self.assertEqual(logged.data["tracking"]["status"], READ)
        self.assertEqual(logged.data["tracking"]["progress"]["value"], 1120)
        self.assertEqual(self.row(item).progress, 1120)

    # MC-102: with no number, the place so far.
    def test_a_log_with_no_total_and_no_number_keeps_the_place(self):
        item = self.manga(total=None, title="One Piece")
        self.action(item, "start")
        self.save(item, 1100)
        logged = self.log(item)
        self.assertEqual(logged.data["tracking"]["progress"]["value"], 1100)

    # MC-102: with a total a log sets the place to the total.
    def test_with_a_total_a_log_sets_the_place_to_the_total(self):
        item = self.manga(total=380)
        self.action(item, "start")
        self.save(item, 12)
        logged = self.log(item, place=20)
        self.assertEqual(logged.data["tracking"]["progress"]["value"], 380)

    # MC-103: a log always means finished, whatever an installed build sends.
    def test_a_log_always_means_finished(self):
        item = self.manga(total=380)
        self.action(item, "start")
        self.save(item, 12)
        response = self.client.post(
            "/api/v1/diary/",
            {
                "ref": {"source": "manual", "media_type": "manga", "media_id": item.media_id},
                "consumed_at": str(self.today),
                "auto_mark_consumed": False,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(self.row(item).status, READ)
        self.assertEqual(self.row(item).attempts.get().status, READ)

    # MC-104: stars and the heart during an attempt, including paused and no total.
    def test_stars_and_a_heart_during_an_attempt_open_the_log_screen(self):
        for label, item, paused in (
            ("reading", self.manga(title="reading"), False),
            ("paused", self.comic(title="paused"), True),
            ("no total", self.manga(total=None, title="running"), False),
        ):
            with self.subTest(label):
                self.action(item, "start")
                if paused:
                    self.action(item, "pause")
                stars = self.client.patch(self.url(item), {"rating": "4"}, format="json", **STARS)
                self.assertEqual(stars.status_code, status.HTTP_409_CONFLICT, stars.data)
                self.assertEqual(stars.data["error"]["code"], "completion_required")
                heart = self.client.post(
                    "/api/v1/me/liked-media/",
                    {"ref": {"source": item.source, "media_type": item.media_type, "media_id": item.media_id}},
                    format="json",
                )
                self.assertEqual(heart.status_code, status.HTTP_409_CONFLICT, heart.data)
                row = self.row(item)
                self.assertIsNone(row.score, "Cancelling changes nothing and the value isn't saved")
                self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())
                self.assertIn(row.status, (READING, Status.PAUSED.value))

    # MC-104: saving the log with that value finishes the attempt.
    def test_the_log_saved_with_the_value_finishes_the_attempt(self):
        item = self.manga(total=None)
        self.action(item, "start")
        logged = self.log(item, rating="4", liked=True)
        row = self.row(item)
        self.assertEqual(logged.data["tracking"]["status"], READ)
        self.assertEqual(row.score, Decimal("8.0"))
        self.assertTrue(MediaLike.objects.filter(user=self.user, item=item).exists())

    # MC-104: once finished, they can change at any time.
    def test_stars_and_the_heart_change_freely_once_finished(self):
        item = self.manga()
        self.log(item)
        changed = self.client.patch(self.url(item), {"rating": "2"}, format="json", **STARS)
        self.assertEqual(changed.status_code, status.HTTP_200_OK, changed.data)
        self.assertEqual(self.row(item).score, Decimal("4.0"))
        ref = {"ref": {"source": item.source, "media_type": "manga", "media_id": item.media_id}}
        self.assertEqual(self.client.post("/api/v1/me/liked-media/", ref, format="json").status_code, status.HTTP_200_OK)
        self.assertEqual(self.row(item).status, READ)


class ContinuingTests(SeriesContractBase):
    """MC-200 to MC-201; MC-202 runs through the calendar job in events.tests."""

    # MC-200: more means Keep Reading and Reread.
    def test_a_read_series_with_more_offers_both(self):
        running = self.manga(total=None, title="One Piece")
        self.action(running, "start")
        self.save(running, 1100)
        state = self.log(running).data["tracking"]["series"]
        self.assertTrue(state["offers_keep_reading"])
        self.assertIn("keep_reading", state["available_actions"])
        self.assertIn("reread", state["available_actions"])

        grown = self.comic(total=10)
        self.log(grown)
        Item.objects.filter(pk=grown.pk).update(series_total=11)
        state = self.client.get(self.url(grown)).data["series"]
        self.assertTrue(state["offers_keep_reading"])

    # MC-200: nothing more means Reread only.
    def test_a_read_series_with_nothing_more_offers_reread_only(self):
        item = self.manga(total=380)
        state = self.log(item).data["tracking"]["series"]
        self.assertFalse(state["offers_keep_reading"])
        self.assertNotIn("keep_reading", state["available_actions"])
        self.assertIn("reread", state["available_actions"])
        self.action(item, "keep_reading", expected=status.HTTP_400_BAD_REQUEST)

    # MC-200: an eye-marked series with a total has nothing more; without one, it has.
    def test_the_eye_marks_the_whole_series(self):
        finished = self.manga(total=380)
        state = self.action(finished, "mark_read").data["series"]
        self.assertFalse(state["offers_keep_reading"])
        running = self.manga(total=None)
        state = self.action(running, "mark_read").data["series"]
        self.assertTrue(state["offers_keep_reading"])
        self.action(running, "keep_reading")
        self.assertEqual(self.row(running).progress, 0, "With no total there is no place to carry on from")

    # MC-200: Keep Reading.
    def test_keep_reading_starts_at_the_old_place_with_no_start_and_is_not_a_reread(self):
        item = self.manga(total=None, title="One Piece")
        self.action(item, "start", start_date=str(self.today - timedelta(days=5)))
        self.save(item, 1100, progressed_on=str(self.today - timedelta(days=4)))
        first = self.log(item, day=self.today - timedelta(days=3))
        starts = ProgressChange.objects.filter(actor=self.user, item=item, current_progress__kind=ProgressChange.START).count()

        kept = self.action(item, "keep_reading")
        state = kept.data["series"]
        self.assertEqual(kept.data["status"], READING)
        self.assertEqual(kept.data["progress"]["value"], 1100)
        self.assertEqual(state["current_journey"]["progress"]["value"], 1100)
        self.assertEqual(state["current_journey"]["start_date"], str(self.today))
        self.assertFalse(state["is_rereading"])
        self.assertFalse(state["current_journey"]["is_reread"])
        self.assertEqual(
            ProgressChange.objects.filter(actor=self.user, item=item, current_progress__kind=ProgressChange.START).count(),
            starts,
            "Records no start",
        )
        self.assertTrue(DiaryEntry.objects.filter(pk=first.data["diary_entry"]["id"]).exists(), "Earlier logs stay")

        self.save(item, 1110)
        second = self.log(item)
        self.assertFalse(second.data["diary_entry"]["is_rewatch"], "Another diary entry, not a reread")
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=item).count(), 2)
        self.assertEqual(second.data["tracking"]["progress"]["value"], 1110)

    # MC-200: Keep Reading's numbers count from the old place.
    def test_keep_readings_next_save_counts_from_the_old_place(self):
        item = self.manga(total=None)
        self.action(item, "start", start_date=str(self.today - timedelta(days=5)))
        self.save(item, 100, progressed_on=str(self.today - timedelta(days=4)))
        self.log(item, day=self.today - timedelta(days=3))
        self.action(item, "keep_reading")
        ProgressChange.objects.filter(actor=self.user, item=item).delete()
        self.save(item, 110)
        self.assertEqual(self.activity()[0][1], "read 10 chapters")

    # MC-200: Reread.
    def test_reread_starts_at_zero_and_counts_again(self):
        item = self.manga(total=None)
        self.action(item, "start", start_date=str(self.today - timedelta(days=5)))
        self.save(item, 100, progressed_on=str(self.today - timedelta(days=4)))
        self.log(item, day=self.today - timedelta(days=3))
        ProgressChange.objects.filter(actor=self.user, item=item).delete()
        reread = self.action(item, "reread")
        self.assertEqual(reread.data["progress"]["value"], 0)
        self.assertTrue(reread.data["series"]["is_rereading"])
        self.assertTrue(
            ProgressChange.objects.filter(actor=self.user, item=item, current_progress__kind=ProgressChange.START).exists(),
        )
        self.save(item, 8)
        logged = self.log(item)
        self.assertTrue(logged.data["diary_entry"]["is_rewatch"])

    # MC-200: logging Keep Reading again and deleting the log reopens it.
    def test_deleting_a_keep_reading_log_reopens_that_attempt(self):
        item = self.manga(total=None)
        self.log(item, day=self.today - timedelta(days=3), place=100)
        self.action(item, "keep_reading")
        self.save(item, 110)
        logged = self.log(item)
        self.client.delete(f"/api/v1/diary/{logged.data['diary_entry']['id']}/")
        row = self.row(item)
        self.assertEqual(row.status, READING)
        self.assertEqual(row.progress, 110)
        self.assertTrue(row.current_attempt.continues)

    # MC-201: Currently Reading after Did Not Finish continues.
    def test_picking_up_after_did_not_finish_continues_from_the_place(self):
        item = self.manga()
        closed = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.save(item, 120)
        self.action(item, "drop")
        picked_up = self.write(item, status=READING)
        state = picked_up.data["series"]
        self.assertEqual(picked_up.data["progress"]["value"], 120)
        self.assertNotEqual(state["current_journey"]["id"], closed)
        self.assertEqual(state["current_journey"]["progress"]["value"], 120)
        self.assertEqual([row["id"] for row in state["reading_history"]], [closed])
        self.assertEqual(state["reading_history"][0]["status"], Status.DROPPED.value)
        self.assertFalse(state["is_rereading"], "Did Not Finish alone isn't a read")

    # MC-201: only Restart begins at zero.
    def test_restart_begins_at_zero_after_picking_up(self):
        item = self.manga()
        self.action(item, "start")
        self.save(item, 120)
        self.action(item, "drop")
        self.action(item, "start")
        restarted = self.action(item, "restart")
        self.assertEqual(restarted.data["progress"]["value"], 0)
        self.assertEqual(self.row(item).attempts.filter(status=Status.DROPPED.value).count(), 2)

    # MC-201: picking up counts what comes next; a read carrying on keeps carrying on.
    def test_picking_up_a_read_that_was_carrying_on_is_not_a_reread(self):
        item = self.manga(total=None)
        self.log(item, day=self.today - timedelta(days=5), place=100)
        self.action(item, "keep_reading")
        self.save(item, 110)
        self.action(item, "drop")
        picked_up = self.action(item, "start")
        self.assertFalse(picked_up.data["series"]["is_rereading"])
        self.assertEqual(picked_up.data["progress"]["value"], 110)
        self.assertFalse(self.log(item).data["diary_entry"]["is_rewatch"])


class InstalledBuildTests(SeriesContractBase):
    """MC-500: what installed builds read and send keeps working."""

    # MC-500: a tracking write with status and progress.
    def test_a_status_and_progress_write_goes_through_these_rules(self):
        item = self.manga(total=380)
        started = self.write(item, status=READING, progress=12, notes="Good so far")
        self.assertEqual(started.data["status"], READING)
        self.assertEqual(started.data["progress"], {"kind": "count", "value": 12, "max": 380, "unit": "chapter"})
        self.assertEqual(started.data["notes"], "Good so far")
        row = self.row(item)
        self.assertEqual(row.progress, 12)
        self.assertEqual(row.attempts.get().place, 12)
        self.assertEqual(ProgressChange.objects.filter(actor=self.user, item=item, current_progress__unit="chapter").count(), 1)

    # MC-500, MC-100: reaching the total from an installed build finishes nothing.
    def test_an_installed_builds_write_at_the_total_stays_currently_reading(self):
        item = self.manga(total=10)
        written = self.write(item, status=READING, progress=10)
        self.assertEqual(written.data["status"], READING)
        self.assertEqual(self.row(item).status, READING)

    # MC-500, MC-200: writing In progress on a Read series with more keeps going.
    def test_an_installed_builds_write_on_a_read_series_keeps_going_when_there_is_more(self):
        item = self.manga(total=None)
        self.log(item, day=self.today - timedelta(days=3), place=100)
        written = self.write(item, status=READING, progress=105)
        row = self.row(item)
        self.assertEqual(written.data["progress"]["value"], 105)
        self.assertTrue(row.current_attempt.continues)
        self.assertFalse(written.data["series"]["is_rereading"])

    # MC-500: a diary entry with auto_mark_consumed.
    def test_a_diary_entry_with_auto_mark_consumed_goes_through_these_rules(self):
        item = self.comic()
        self.write(item, status=READING, progress=5)
        response = self.client.post(
            "/api/v1/diary/",
            {
                "ref": {"source": "manual", "media_type": "comic", "media_id": item.media_id},
                "consumed_at": f"{self.today}T12:00:00Z",
                "rating": 8,
                "auto_mark_consumed": True,
                "tags": ["space"],
                "visibility": "followers",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data["rating"], "8.0", "Installed builds keep the 0-10 scale")
        row = self.row(item)
        self.assertEqual(row.status, READ)
        self.assertEqual(row.progress, 54)
        self.assertEqual(row.score, Decimal(8))
        attempt = row.attempts.get()
        self.assertEqual(attempt.status, READ)
        self.assertEqual(attempt.completion_diary_entry_id, response.data["id"])
        entry = DiaryEntry.objects.get(pk=response.data["id"])
        self.assertEqual(entry.visibility, "followers")
        self.assertEqual(Activity.objects.filter(target_type="diary", target_id=entry.pk).count(), 1, "One, not two")

    # MC-500: an installed build's log is refused cleanly in the future.
    def test_an_installed_builds_future_log_is_a_400(self):
        item = self.comic()
        response = self.client.post(
            "/api/v1/diary/",
            {
                "ref": {"source": "manual", "media_type": "comic", "media_id": item.media_id},
                "consumed_at": str(self.today + timedelta(days=2)),
                "auto_mark_consumed": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

    # MC-500: tracking removal.
    def test_tracking_removal_goes_through_these_rules(self):
        item = self.manga()
        self.write(item, status=Status.PLANNING.value)
        self.assertEqual(self.client.delete(self.url(item)).status_code, status.HTTP_204_NO_CONTENT)
        started = self.manga(title="started")
        self.write(started, status=READING)
        self.assertEqual(self.client.delete(self.url(started)).status_code, status.HTTP_409_CONFLICT)

    # MC-500: the pre-contract actions.
    def test_the_pause_resume_drop_and_consume_actions_still_answer(self):
        item = self.manga()
        self.write(item, status=READING, progress=5)
        self.assertEqual(self.action(item, "pause").data["status"], Status.PAUSED.value)
        self.assertEqual(self.action(item, "resume").data["status"], READING)
        self.assertEqual(self.action(item, "drop").data["status"], Status.DROPPED.value)
        consumed = self.action(self.manga(title="new"), "consume", consumed_at=f"{self.today}T12:00:00Z")
        self.assertEqual(consumed.data["status"], READ)

    # MC-500: every field installed builds read keeps its meaning.
    def test_the_keys_installed_builds_read_are_all_there(self):
        item = self.manga()
        written = self.write(item, status=READING, progress=5)
        for key in (
            "tracking_id",
            "status",
            "rating",
            "progress",
            "repeats",
            "start_date",
            "end_date",
            "notes",
            "updated_at",
            "latest_progress_change",
        ):
            self.assertIn(key, written.data)
        self.assertEqual(written.data["progress"]["kind"], "count")
        self.assertEqual(written.data["latest_progress_change"]["current"]["value"], 5)
        self.assertNotIn("book", written.data, "Installed builds' book decoder never sees a manga")

    # MC-500: an installed build's 0-10 rating on a finished series.
    def test_an_installed_builds_rating_stays_on_its_scale(self):
        item = self.manga()
        self.log(item)
        rated = self.client.patch(self.url(item), {"rating": "7.5"}, format="json")
        self.assertEqual(rated.status_code, status.HTTP_200_OK, rated.data)
        self.assertEqual(rated.data["rating"], "7.5")
        self.assertEqual(self.row(item).score, Decimal("7.5"))

    # MC-500: the progress endpoints are only for manga and comics.
    def test_the_new_routes_answer_only_for_manga_and_comics(self):
        item = Item.objects.create(source=Sources.MANUAL.value, media_type="anime", media_id="a1", title="Anime", image="https://example.com/a.jpg")
        for suffix in ("progress/", "complete/", "journeys/1/"):
            response = self.client.post(self.url(item, suffix), {}, format="json")
            self.assertIn(response.status_code, (status.HTTP_404_NOT_FOUND, status.HTTP_405_METHOD_NOT_ALLOWED), suffix)
        self.assertEqual(self.client.post(self.url(item, "progress/"), {"value": 1}, format="json").status_code, status.HTTP_404_NOT_FOUND)

    def page_queries(self, status_filter):
        """Return a list page's queries, its caches warm, in a list that stays put."""
        params = {"media_type": "manga", "status": status_filter}
        self.client.get("/api/v1/tracking/", params)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/v1/tracking/", params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [query["sql"] for query in queries]

    # MC-500: a list page stays bounded.
    def test_a_library_page_does_not_query_per_attempt(self):
        def add_rows(prefix):
            for number in range(3):
                item = self.manga(title=f"{prefix}{number}")
                self.action(item, "start")
                self.save(item, 5)

        add_rows("a")
        three = self.page_queries(READING)
        add_rows("b")
        six = self.page_queries(READING)
        self.assertLessEqual(len([sql for sql in six if "app_mangaattempt" in sql]), 1, "One prefetch for the page")
        self.assertEqual(len(six), len(three), six)

    # MC-500: rows with no attempts cost no query each either.
    def test_a_to_read_page_does_not_query_per_row(self):
        for number in range(3):
            self.write(self.manga(title=f"t{number}"), status=Status.PLANNING.value)
        three = self.page_queries(Status.PLANNING.value)
        for number in range(3):
            self.write(self.manga(title=f"s{number}"), status=Status.PLANNING.value)
        six = self.page_queries(Status.PLANNING.value)
        self.assertEqual(len(six), len(three), six)

    # MC-501: no data to repair.
    def test_the_migration_is_additive_with_no_repair(self):
        from importlib import import_module

        from django.db.migrations import operations

        module = import_module("app.migrations.0094_manga_comic_attempts")
        allowed = (operations.CreateModel, operations.AddField, operations.AddConstraint)
        for operation in module.Migration.operations:
            self.assertIsInstance(operation, allowed, operation)

    # MC-400 (the API): one record, whatever is written.
    def test_writes_keep_one_record_per_series(self):
        item = self.manga()
        self.write(item, status=READING)
        self.write(item, status=Status.PAUSED.value)
        self.write(item, status=READING, progress=2)
        self.assertEqual(Manga.objects.filter(user=self.user, item=item).count(), 1)
        self.assertEqual(MangaAttempt.objects.filter(tracking__user=self.user).count(), 1)
        self.assertEqual(ComicAttempt.objects.count(), 0)


class OtherPeoplesListsTests(SeriesContractBase):
    """AC-5, AC-6: another's list tells nothing a log the viewer can't see set."""

    def setUp(self):
        super().setUp()
        self.stranger = get_user_model().objects.create_user(username="stranger", password="strong-password-123")
        self.other = APIClient()
        self.other.force_authenticate(self.stranger)

    def their_row(self, status_filter=READ):
        results = self.other.get(
            "/api/v1/users/reader/tracking/",
            {"media_type": "manga", "status": status_filter},
        ).data["results"]
        self.assertEqual(len(results), 1)
        return results[0]["tracking"]

    def assert_no_log_facts(self, tracking):
        for key in ("series", "liked", "diary_count", "rating_source_diary_entry_id", "like_source_diary_entry_id"):
            self.assertNotIn(key, tracking)

    def test_a_private_log_leaves_no_trace(self):
        self.log(self.manga(), rating=5, liked=True, visibility="private")
        tracking = self.their_row()
        self.assert_no_log_facts(tracking)
        self.assertIsNone(tracking["rating"])
        self.assertIsNone(tracking["end_date"])
        self.assertEqual(tracking["status"], READ, "Where they stand still shows (AC-6)")

    def test_a_followers_log_shows_only_to_a_follower(self):
        self.log(self.manga(), rating=4, visibility="followers")
        self.assertIsNone(self.their_row()["rating"])
        Follow.objects.create(from_user=self.stranger, to_user=self.user, status=FollowStatus.ACCEPTED)
        tracking = self.their_row()
        self.assert_no_log_facts(tracking)
        self.assertIsNotNone(tracking["rating"])
        self.assertIsNotNone(tracking["end_date"])

    def test_a_public_log_keeps_its_rating_and_date(self):
        self.log(self.manga(), rating=4)
        tracking = self.their_row()
        self.assert_no_log_facts(tracking)
        self.assertIsNotNone(tracking["rating"])
        self.assertIsNotNone(tracking["end_date"])

    def test_the_owner_sees_everything(self):
        self.log(self.manga(), rating=5, liked=True, visibility="private")
        tracking = self.client.get("/api/v1/tracking/", {"media_type": "manga", "status": READ}).data["results"][0]["tracking"]
        self.assertTrue(tracking["liked"])
        self.assertEqual(tracking["diary_count"], 1)
        self.assertIn("series", tracking)
        self.assertIsNotNone(tracking["rating"])


class UndoneWritesTests(CountingDaysBase):
    """MC-007: what is undone counts on no day, and what stands counts once."""

    def test_undoing_the_eye_counts_nothing(self):
        self.write_at(self.at(20, 12), self.action, self.item, "start")
        self.write_at(self.at(20, 13), self.save, self.item, 30)
        self.write_at(self.at(20, 14), self.action, self.item, "drop")
        self.write_at(self.at(20, 15), self.action, self.item, "mark_read")
        self.write_at(self.at(20, 16), self.action, self.item, "undo_read")
        self.assertEqual(self.row(self.item).progress, 30)
        self.assertEqual(self.counted("2026-10-20"), 30)

    def test_undoing_the_eye_over_older_reads_counts_nothing(self):
        self.write_at(self.at(20, 12), self.log, self.item, day=date(2026, 10, 20))
        self.write_at(self.at(21, 12), self.action, self.item, "reread")
        self.write_at(self.at(21, 13), self.save, self.item, 30)
        self.write_at(self.at(21, 14), self.action, self.item, "drop")
        self.write_at(self.at(21, 15), self.action, self.item, "mark_read")
        self.write_at(self.at(21, 16), self.action, self.item, "undo_read")
        self.assertEqual(self.row(self.item).progress, 30)
        self.assertEqual(self.counted("2026-10-21"), 30)

    def test_deleting_a_reread_leaves_the_first_reads_count(self):
        self.write_at(self.at(20, 12), self.log, self.item, day=date(2026, 10, 20))
        attempt_id = self.write_at(self.at(21, 12), self.action, self.item, "reread").data["series"]["current_journey"]["id"]
        self.write_at(self.at(21, 13), self.client.delete, self.url(self.item, f"journeys/{attempt_id}/"))
        self.assertEqual(self.row(self.item).progress, 380)
        self.assertEqual(self.counted("2026-10-20"), 380)
        self.assertEqual(self.counted("2026-10-21"), 0)

    def test_deleting_a_log_and_logging_again_counts_once(self):
        self.write_at(self.at(20, 12), self.action, self.item, "start")
        self.write_at(self.at(20, 13), self.save, self.item, 120)
        entry_id = self.write_at(self.at(20, 14), self.log, self.item, day=date(2026, 10, 20)).data["diary_entry"]["id"]
        self.write_at(self.at(20, 15), self.client.delete, f"/api/v1/diary/{entry_id}/")
        self.assertEqual(self.row(self.item).progress, 120)
        self.assertEqual(self.counted("2026-10-20"), 120)
        self.write_at(self.at(20, 16), self.log, self.item, day=date(2026, 10, 20))
        self.assertEqual(self.counted("2026-10-20"), 380)

    def test_deleting_an_attempt_removes_its_count(self):
        self.write_at(self.at(20, 12), self.write, self.item, status=Status.PLANNING.value)
        attempt_id = self.write_at(self.at(20, 13), self.action, self.item, "start").data["series"]["current_journey"]["id"]
        self.write_at(self.at(20, 14), self.save, self.item, 120)
        self.write_at(self.at(20, 15), self.client.delete, self.url(self.item, f"journeys/{attempt_id}/"))
        self.assertEqual(self.row(self.item).status, Status.PLANNING.value)
        self.assertEqual(self.counted("2026-10-20"), 0)

    def test_deleting_a_direct_log_removes_its_count(self):
        entry_id = self.write_at(self.at(20, 12), self.log, self.item, day=date(2026, 10, 20)).data["diary_entry"]["id"]
        self.assertEqual(self.counted("2026-10-20"), 380)
        self.write_at(self.at(21, 12), self.write, self.item, status=Status.PLANNING.value)
        self.write_at(self.at(21, 13), self.client.delete, f"/api/v1/diary/{entry_id}/")
        self.assertEqual(self.counted("2026-10-20"), 0)

    def test_a_save_after_an_undo_counts_from_the_place(self):
        self.write_at(self.at(20, 12), self.action, self.item, "start")
        self.write_at(self.at(20, 13), self.save, self.item, 30)
        self.write_at(self.at(20, 14), self.action, self.item, "drop")
        self.write_at(self.at(20, 15), self.action, self.item, "mark_read")
        self.write_at(self.at(20, 16), self.action, self.item, "undo_read")
        self.write_at(self.at(21, 12), self.action, self.item, "start")
        self.write_at(self.at(21, 13), self.save, self.item, 35)
        self.assertEqual(self.counted("2026-10-21"), 5)

    # MC-007: a reread by a direct log counts again, on its day.
    def test_a_direct_log_reread_counts_again(self):
        self.write_at(self.at(20, 12), self.log, self.item, day=date(2026, 10, 20))
        logged = self.write_at(self.at(21, 12), self.log, self.item, day=date(2026, 10, 21))
        self.assertTrue(logged.data["diary_entry"]["is_rewatch"])
        self.assertEqual(self.counted("2026-10-20"), 380)
        self.assertEqual(self.counted("2026-10-21"), 380)
        self.assertEqual(self.row(self.item).progress, 380)

    def test_a_backdated_direct_log_reread_counts_on_its_day(self):
        self.write_at(self.at(20, 12), self.log, self.item, day=date(2026, 10, 15))
        self.write_at(self.at(21, 12), self.log, self.item, day=date(2026, 10, 18))
        self.assertEqual(self.counted("2026-10-15"), 380)
        self.assertEqual(self.counted("2026-10-18"), 380)
        self.assertEqual(self.counted("2026-10-21"), 0)

    def test_deleting_a_direct_log_reread_counts_nothing(self):
        self.write_at(self.at(20, 12), self.log, self.item, day=date(2026, 10, 20))
        entry_id = self.write_at(self.at(21, 12), self.log, self.item, day=date(2026, 10, 21)).data["diary_entry"]["id"]
        self.write_at(self.at(21, 13), self.client.delete, f"/api/v1/diary/{entry_id}/")
        self.assertEqual(self.row(self.item).progress, 380)
        self.assertEqual(self.counted("2026-10-21"), 0)
        self.assertEqual(self.counted("2026-10-20"), 380)

    def test_a_direct_log_after_the_eye_counts_once(self):
        self.write_at(self.at(20, 12), self.action, self.item, "mark_read")
        logged = self.write_at(self.at(21, 12), self.log, self.item, day=date(2026, 10, 21))
        self.assertTrue(logged.data["diary_entry"]["is_rewatch"], "BK-400: a read came first")
        self.assertEqual(self.counted("2026-10-21"), 380)

    # MC-007: a log counts on its own day, whatever the series was doing since.
    def test_a_backdated_log_on_a_to_read_series_counts_on_its_day(self):
        self.write_at(self.at(21, 12), self.write, self.item, status=Status.PLANNING.value)
        self.write_at(self.at(22, 12), self.log, self.item, day=date(2026, 3, 1))
        self.assertEqual(self.counted("2026-03-01"), 380)
        self.assertEqual(self.counted("2026-10-21"), 0)
        self.assertEqual(self.counted("2026-10-22"), 0)

    def test_a_log_from_last_year_counts_last_year(self):
        self.write_at(self.at(21, 12), self.write, self.item, status=Status.PLANNING.value)
        self.write_at(self.at(22, 12), self.log, self.item, day=date(2025, 3, 1))
        self.assertEqual(self.counted("2025-03-01"), 380)
        scope = build_scope(self.user, self.user, {"start_date": "2026-01-01", "end_date": "2026-12-31", "schema": "2"})
        self.assertEqual(stats_time.type_time(scope, "manga").value, 0)

    def test_a_log_dated_before_a_saved_number_still_counts_after_it(self):
        self.write_at(self.at(20, 12), self.action, self.item, "start")
        self.write_at(self.at(20, 13), self.save, self.item, 30)
        self.write_at(self.at(20, 14), self.action, self.item, "drop")
        self.write_at(self.at(21, 12), self.log, self.item, day=date(2026, 3, 1))
        self.assertEqual(self.counted("2026-10-20"), 380, "What the log adds follows the number, on its day")
        self.assertEqual(self.counted("2026-03-01"), 0)
        self.assertEqual(self.counted("2026-10-21"), 0)

    def test_editing_a_logs_date_moves_its_count(self):
        entry_id = self.write_at(self.at(22, 12), self.log, self.item, day=date(2026, 10, 22)).data["diary_entry"]["id"]
        self.assertEqual(self.counted("2026-10-22"), 380)
        self.write_at(self.at(22, 13), self.client.patch, f"/api/v1/diary/{entry_id}/", {"consumed_at": "2026-10-10"}, format="json")
        self.assertEqual(self.counted("2026-10-10"), 380)
        self.assertEqual(self.counted("2026-10-22"), 0)
        self.write_at(self.at(22, 14), self.client.patch, f"/api/v1/diary/{entry_id}/", {"consumed_at": "2026-10-15"}, format="json")
        self.assertEqual(self.counted("2026-10-15"), 380)
        self.assertEqual(self.counted("2026-10-10"), 0)

    def test_editing_a_live_reads_log_moves_only_what_the_log_added(self):
        self.write_at(self.at(10, 12), self.action, self.item, "start")
        self.write_at(self.at(10, 13), self.save, self.item, 300)
        entry_id = self.write_at(self.at(22, 12), self.log, self.item, day=date(2026, 10, 22)).data["diary_entry"]["id"]
        self.write_at(self.at(22, 13), self.client.patch, f"/api/v1/diary/{entry_id}/", {"consumed_at": "2026-10-15"}, format="json")
        self.assertEqual(self.counted("2026-10-10"), 300)
        self.assertEqual(self.counted("2026-10-15"), 80)
        self.assertEqual(self.counted("2026-10-22"), 0)


class ImportedNumberTests(SeriesContractBase):
    """MC-201, BK-102, BK-404: an imported number stays through what is undone."""

    def imported(self, status_value, place, **fields):
        item = self.manga(title=f"imported {status_value}")
        series_tracking.import_title(self.user, item, status=status_value, place=place, import_source="test", **fields)
        return item

    def test_deleting_a_picked_up_attempt_keeps_the_number(self):
        item = self.imported(Status.DROPPED.value, 20)
        attempt_id = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.assertEqual(self.row(item).progress, 20)
        self.client.delete(self.url(item, f"journeys/{attempt_id}/"))
        row = self.row(item)
        self.assertEqual((row.status, row.progress), (Status.DROPPED.value, 20))

    def test_writing_the_same_status_keeps_the_number(self):
        for status_value in (Status.DROPPED.value, Status.PAUSED.value):
            with self.subTest(status=status_value):
                item = self.imported(status_value, 20)
                self.write(item, status=status_value)
                self.assertEqual(self.row(item).progress, 20)

    def test_dropping_an_imported_paused_series_keeps_the_number(self):
        item = self.imported(Status.PAUSED.value, 50)
        self.action(item, "drop")
        row = self.row(item)
        self.assertEqual((row.status, row.progress), (Status.DROPPED.value, 50))
        self.assertFalse(row.attempts.exists())

    def test_the_eye_and_its_undo_keep_the_number(self):
        item = self.imported(Status.PAUSED.value, 50)
        self.action(item, "mark_read")
        self.action(item, "undo_read")
        row = self.row(item)
        self.assertEqual((row.status, row.progress), (Status.PAUSED.value, 50))

    def test_the_eye_over_an_older_read_and_its_undo_keep_the_number(self):
        item = self.manga(title="older read")
        series_tracking.import_title(
            self.user,
            item,
            status=Status.PAUSED.value,
            place=50,
            earlier=[{"status": READ, "place": 380}],
            import_source="test",
        )
        self.action(item, "mark_read")
        self.action(item, "undo_read")
        row = self.row(item)
        self.assertEqual((row.status, row.progress), (Status.PAUSED.value, 50))

    def test_deleting_a_log_on_an_imported_read_keeps_the_number(self):
        item = self.imported(READ, 200)
        entry_id = self.log(item).data["diary_entry"]["id"]
        self.client.delete(f"/api/v1/diary/{entry_id}/")
        row = self.row(item)
        self.assertEqual((row.status, row.progress), (READ, 200))

    def test_going_to_read_clears_the_number(self):
        item = self.imported(Status.PAUSED.value, 50)
        self.write(item, status=Status.PLANNING.value)
        self.assertEqual(self.row(item).progress, 0)

    # BK-001: undoing the eye on an imported Read with an earlier undated attempt.
    def test_undoing_the_eye_over_an_imported_read_zeroes_the_row(self):
        item = self.manga(title="undated")
        series_tracking.import_title(
            self.user,
            item,
            status=READ,
            place=380,
            earlier=[{"status": READ, "place": 380}],
            import_source="test",
        )
        self.action(item, "undo_read")
        row = self.row(item)
        self.assertEqual((row.status, row.progress), (Status.PLANNING.value, 0))

    # BK-002: an imported undated read is no real history, as a book's isn't.
    def test_an_imported_undated_read_doesnt_block_removal(self):
        item = self.manga(title="undated")
        series_tracking.import_title(
            self.user,
            item,
            status=Status.PLANNING.value,
            earlier=[{"status": READ, "place": 380}],
            import_source="test",
        )
        state = self.client.get(self.url(item)).data["series"]
        self.assertTrue(state["can_remove_tracking"])
        self.assertEqual(self.client.delete(self.url(item)).status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Manga.objects.filter(user=self.user, item=item).exists())


class AttemptEdgeTests(SeriesContractBase):
    """Stale ids, dates and places on an attempt."""

    # BK-300, BK-501: a stale journey id finishes what is under way.
    def test_a_log_naming_a_closed_attempt_finishes_the_one_under_way(self):
        item = self.manga()
        closed_id = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.save(item, 30)
        self.action(item, "drop")
        self.action(item, "start")
        self.save(item, 40)
        self.log(item, journey_id=closed_id)
        row = self.row(item)
        self.assertEqual(row.status, READ)
        self.assertFalse(row.attempts.filter(status__in=[READING, Status.PAUSED.value]).exists())
        self.assertEqual(row.attempts.count(), 2)
        self.assertEqual(row.attempts.get(pk=closed_id).status, Status.DROPPED.value)
        self.assertEqual(self.client.post(self.url(item, "actions/start/"), {}, format="json").status_code, status.HTTP_200_OK)
        self.assertEqual(self.row(item).status, READING, "Nothing is stuck")

    # BK-306: dates stay in order, and a live attempt's stay what they are.
    def test_a_live_attempts_start_cant_be_cleared(self):
        item = self.manga()
        attempt_id = self.action(item, "start").data["series"]["current_journey"]["id"]
        response = self.client.patch(self.url(item, f"journeys/{attempt_id}/"), {"start_date": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertEqual(self.row(item).current_attempt.start_date, self.today)

    def test_a_live_attempt_takes_no_end_date(self):
        item = self.manga()
        attempt_id = self.action(item, "start").data["series"]["current_journey"]["id"]
        response = self.client.patch(self.url(item, f"journeys/{attempt_id}/"), {"end_date": str(self.today)}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertIsNone(self.row(item).current_attempt.end_date)

    def test_a_did_not_finish_end_date_cant_be_cleared(self):
        item = self.manga()
        attempt_id = self.action(item, "start").data["series"]["current_journey"]["id"]
        self.action(item, "drop")
        response = self.client.patch(self.url(item, f"journeys/{attempt_id}/"), {"end_date": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertEqual(self.row(item).current_attempt.end_date, self.today)

    # MC-200: Keep Reading starts where the row says, whatever the logs' dates.
    def test_keep_reading_starts_at_the_current_read_not_the_latest_dated(self):
        item = self.manga(total=None, title="One Piece")
        self.log(item, place=100, day=self.today)
        self.action(item, "keep_reading", start_date=str(self.today - timedelta(days=5)))
        self.log(item, place=110, day=self.today - timedelta(days=3))
        self.assertEqual(self.row(item).progress, 110)
        state = self.client.get(self.url(item)).data["series"]
        self.assertTrue(state["offers_keep_reading"])
        kept = self.action(item, "keep_reading")
        self.assertEqual(kept.data["series"]["current_journey"]["progress"]["value"], 110)
        self.assertEqual(self.row(item).progress, 110)

    # MC-200: a read that carried on is the same read: no lifetime read, no Reread.
    def test_carrying_on_is_not_another_read(self):
        item = self.manga(total=None, title="One Piece")
        first = self.log(item, place=100).data["diary_entry"]["id"]
        self.action(item, "keep_reading")
        second = self.log(item, place=110).data["diary_entry"]["id"]
        self.action(item, "keep_reading")
        third = self.log(item, place=120).data["diary_entry"]["id"]
        state = self.client.get(self.url(item)).data["series"]
        self.assertEqual(state["lifetime_read_count"], 1)
        scope = build_scope(self.user, self.user, {"schema": "2"})
        self.assertEqual(stats_v2_activity.rewatch_ids(scope), set())
        repeats = self.client.get("/api/v1/diary/", {"repeat": "true"}).data["results"]
        self.assertEqual(repeats, [])
        self.action(item, "reread")
        fourth = self.log(item, place=5).data["diary_entry"]["id"]
        self.assertEqual(self.client.get(self.url(item)).data["series"]["lifetime_read_count"], 2)
        self.assertEqual(stats_v2_activity.rewatch_ids(build_scope(self.user, self.user, {"schema": "2"})), {fourth})
        self.assertLess(first, second)
        self.assertLess(second, third)

    def test_an_eye_mark_carried_on_by_a_new_issue_is_one_read(self):
        item = self.comic()
        self.action(item, "mark_read")
        Item.objects.filter(pk=item.pk).update(series_total=55)
        item.refresh_from_db()
        series_tracking.new_issue_out(item, previous_total=54)
        entry_id = self.log(item).data["diary_entry"]["id"]
        self.assertEqual(self.client.get(self.url(item)).data["series"]["lifetime_read_count"], 1)
        self.assertNotIn(entry_id, stats_v2_activity.rewatch_ids(build_scope(self.user, self.user, {"schema": "2"})))


class ComicVineRereadTests(SeriesContractBase):
    """MC-004: the re-read answers in time, and never costs a right number."""

    def comic_vine_comic(self):
        item = Item.objects.create(
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            media_id="46568",
            title="Saga",
            image="https://example.com/saga.jpg",
            series_total=54,
        )
        Comic.objects.create(user=self.user, item=item, status=Status.PLANNING.value)
        self.action(item, "start")
        return item

    def test_it_asks_once_quickly_with_no_retries_and_no_publisher_list(self):
        item = self.comic_vine_comic()
        with patch(
            "app.providers.comicvine.services.api_request",
            return_value={"results": {"count_of_issues": 55}},
        ) as request:
            self.save(item, 55)
        self.assertEqual(request.call_count, 1, "No publisher list")
        kwargs = request.call_args.kwargs
        self.assertLessEqual(kwargs["timeout"], 5)
        self.assertFalse(kwargs["retry_rate_limits"])
        self.assertEqual(self.row(item).progress, 55)
        self.assertEqual(Item.objects.get(pk=item.pk).series_total, 55)

    def test_a_null_publisher_on_a_page_is_no_error(self):
        volume = {
            "results": {
                "publisher": None,
                "site_detail_url": "https://example.com/saga",
                "name": "Saga",
                "last_issue": {"id": 9, "issue_number": "55", "name": "Last"},
                "image": {"medium_url": "https://example.com/saga.jpg"},
                "description": "",
                "concepts": [],
                "start_year": "2012",
                "count_of_issues": 55,
                "people": [],
                "date_last_updated": "2026-01-01 00:00:00",
            },
        }
        cache.delete("comicvine_comic_99999")
        with patch("app.providers.comicvine.services.api_request", return_value=volume):
            metadata = comicvine.comic("99999")
        self.assertEqual(metadata["max_progress"], 55)

    def test_a_re_read_leaves_the_pages_cached_copy(self):
        cache.set("comicvine_comic_46570", {"max_progress": 54, "related": {"recommendations": ["kept"]}})
        with patch(
            "app.providers.comicvine.services.api_request",
            return_value={"results": {"count_of_issues": 55}},
        ):
            comicvine.fresh_issue_count("46570")
        self.assertEqual(cache.get("comicvine_comic_46570")["related"], {"recommendations": ["kept"]})

    def test_any_failure_refuses_cleanly(self):
        item = self.comic_vine_comic()
        for error in (ValueError("bad json"), KeyError("results"), requests.exceptions.ReadTimeout()):
            with self.subTest(error=error), patch("app.providers.comicvine.services.api_request", side_effect=error):
                refused = self.save(item, 60, expected=status.HTTP_400_BAD_REQUEST)
                self.assertEqual(refused.data["error"]["message"], "This comic has 54 issues so far.")


class ProviderDownTests(SeriesContractBase):
    """With the provider down, a series Spine already knows still takes writes."""

    def setUp(self):
        super().setUp()
        patcher = patch(
            "app.providers.services.get_media_metadata",
            side_effect=comicvine.services.ProviderAPIError("mal", None),
        )
        self.provider = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_status_write_with_no_row_uses_the_item(self):
        item = self.manga()
        self.write(item, status=Status.PLANNING.value)
        self.assertEqual(self.row(item).status, Status.PLANNING.value)
        self.provider.assert_not_called()

    def test_a_diary_entry_with_a_row_uses_the_item(self):
        item = self.manga()
        self.write(item, status=Status.PLANNING.value)
        response = self.client.post(
            "/api/v1/diary/",
            {
                "ref": {"source": item.source, "media_type": item.media_type, "media_id": item.media_id},
                "consumed_at": str(self.today),
                "auto_mark_consumed": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(self.row(item).status, READ)
        self.provider.assert_not_called()


class PageDetailsTests(SimpleTestCase):
    """MC-102, MC-003: a page tells the app a series' total, whichever provider lists it."""

    def test_a_finished_series_with_no_chapter_detail_gets_its_total(self):
        details = details_for_api({"media_type": "manga", "max_progress": 162, "details": {"format": "Manga"}})
        self.assertEqual(details["number_of_chapters"], 162)

    def test_a_providers_own_count_stays_and_no_total_adds_nothing(self):
        kept = details_for_api({"media_type": "manga", "max_progress": 162, "details": {"number_of_chapters": 150}})
        self.assertEqual(kept["number_of_chapters"], 150)
        self.assertNotIn("number_of_chapters", details_for_api({"media_type": "manga", "max_progress": None}))
        self.assertNotIn("number_of_chapters", details_for_api({"media_type": "book", "max_progress": 300}))
