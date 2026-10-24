from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.services import media as media_service
from app import book_tracking
from app.models import Book, BookSession, DiaryEntry, Item, MediaLike, Sources, Status


class BookTrackingContractTests(TestCase):
    """Focused API/ORM coverage for the canonical numbered book laws."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="book-contract",
            password="strong-password-123",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.item_counter = 0
        self.today = timezone.localdate()

    def make_item(self, suffix=None, *, total_pages=300):
        self.item_counter += 1
        media_id = suffix or f"book-{self.item_counter}"
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type="book",
            media_id=media_id,
            title=f"Contract Book {media_id}",
            image="https://example.com/book.jpg",
            total_pages=total_pages,
        )

    def detail_url(self, item):
        return f"/api/v1/tracking/manual/book/{item.media_id}/"

    def action_url(self, item, action):
        return f"{self.detail_url(item)}actions/{action}/"

    def progress_url(self, item):
        return f"/api/v1/tracking/manual/book/{item.media_id}/progress/"

    def complete_url(self, item):
        return f"/api/v1/tracking/manual/book/{item.media_id}/complete/"

    def journey_url(self, item, journey_id):
        return f"/api/v1/tracking/manual/book/{item.media_id}/journeys/{journey_id}/"

    def ref(self, item):
        return {
            "source": Sources.MANUAL.value,
            "media_type": "book",
            "media_id": item.media_id,
        }

    def assert_status(self, response, expected):
        self.assertEqual(response.status_code, expected, response.data)

    def set_status(self, item, value):
        response = self.client.patch(
            self.detail_url(item),
            {"status": value},
            format="json",
        )
        self.assert_status(response, status.HTTP_200_OK)
        return response

    def action(self, item, name, data=None, *, expected=status.HTTP_200_OK):
        response = self.client.post(
            self.action_url(item, name),
            data or {},
            format="json",
        )
        self.assert_status(response, expected)
        return response

    def complete(self, item, *, completion_date=None, mutation_id=None, **extra):
        payload = {
            "completion_date": str(completion_date or self.today),
            "mutation_id": str(mutation_id or uuid4()),
            **extra,
        }
        return self.client.post(self.complete_url(item), payload, format="json")

    def test_book_row_lock_does_not_join_nullable_current_session(self):
        item = self.make_item("postgres-lock")
        Book.objects.create(user=self.user, item=item, status=Status.PLANNING.value)

        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            locked = book_tracking._locked_book(self.user, item)

        self.assertIsNotNone(locked)
        self.assertNotIn("app_booksession", queries[0]["sql"])

    # BK-001, BK-002, BK-100, BK-101.
    def test_five_statuses_have_one_library_placement_without_synthetic_history(self):
        cases = {
            Status.PLANNING.value: ("direct_status", 0, False),
            Status.IN_PROGRESS.value: ("journey", 1, False),
            Status.PAUSED.value: ("direct_status", 0, False),
            Status.DROPPED.value: ("direct_status", 0, False),
            Status.COMPLETED.value: ("undated_read", 0, True),
        }
        items = {}

        for value, (source, session_count, undated_read) in cases.items():
            with self.subTest(status=value):
                item = self.make_item(value.replace(" ", "-").lower())
                items[value] = item
                response = self.set_status(item, value)
                book = Book.objects.get(user=self.user, item=item)

                self.assertEqual(response.data["status"], value)
                self.assertEqual(response.data["book"]["status_source"], source)
                self.assertEqual(book.reading_sessions.count(), session_count)
                self.assertEqual(book.completed_manually, undated_read)
                self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())
                if value in {
                    Status.PLANNING.value,
                    Status.PAUSED.value,
                    Status.DROPPED.value,
                    Status.COMPLETED.value,
                }:
                    self.assertIsNone(book.start_date)
                    self.assertIsNone(book.end_date)

        for value, item in items.items():
            response = self.client.get(
                "/api/v1/tracking/",
                {"media_type": "book", "status": value},
            )
            self.assert_status(response, status.HTTP_200_OK)
            ids = {
                row["media"]["ref"]["media_id"]
                for row in response.data["results"]
            }
            self.assertIn(item.media_id, ids)
            self.assertTrue(
                all(
                    Book.objects.get(user=self.user, item__media_id=media_id).status == value
                    for media_id in ids
                ),
            )

        planning = items[Status.PLANNING.value]
        removed = self.client.delete(self.detail_url(planning))
        self.assert_status(removed, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Book.objects.filter(user=self.user, item=planning).exists())

    # BK-102, BK-103, BK-200, BK-203, BK-310.
    def test_undated_read_undo_restores_exact_state_and_preserves_older_completion(self):
        untracked = self.make_item("untracked-marker")
        marked = self.action(untracked, "mark_read")
        self.assertEqual(marked.data["book"]["status_source"], "undated_read")
        self.assertTrue(Book.objects.get(user=self.user, item=untracked).completed_manually)
        self.assertFalse(BookSession.objects.filter(related_book__item=untracked).exists())
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=untracked).exists())

        undone = self.action(
            untracked,
            "undo_read",
            expected=status.HTTP_204_NO_CONTENT,
        )
        self.assertIsNone(undone.data)
        self.assertFalse(Book.objects.filter(user=self.user, item=untracked).exists())

        planned = self.make_item("planned-marker")
        self.set_status(planned, Status.PLANNING.value)
        self.action(planned, "mark_read")
        liked = self.client.post(
            "/api/v1/me/liked-media/",
            {"ref": self.ref(planned)},
            format="json",
        )
        self.assert_status(liked, status.HTTP_200_OK)
        rated = self.client.patch(
            self.detail_url(planned),
            {"rating": "4.5"},
            format="json",
        )
        self.assert_status(rated, status.HTTP_200_OK)
        restored = self.action(planned, "undo_read")
        self.assertEqual(restored.data["status"], Status.PLANNING.value)
        book = Book.objects.get(user=self.user, item=planned)
        self.assertFalse(book.completed_manually)
        self.assertIsNone(book.score)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=planned).exists())

        history_item = self.make_item("older-completion-override")
        first = self.complete(
            history_item,
            completion_date=self.today - timedelta(days=20),
        )
        self.assert_status(first, status.HTTP_201_CREATED)
        completion_id = first.data["diary_entry"]["id"]
        self.set_status(history_item, Status.DROPPED.value)
        overridden = self.action(history_item, "mark_read")
        self.assertEqual(overridden.data["book"]["status_source"], "history_override")
        self.assertIsNone(overridden.data["book"]["undated_read"])
        restored = self.action(history_item, "undo_read")
        self.assertEqual(restored.data["status"], Status.DROPPED.value)
        self.assertTrue(DiaryEntry.objects.filter(pk=completion_id).exists())
        self.assertEqual(restored.data["book"]["completed_journey_count"], 1)

    # BK-102, BK-103, SW-008.
    def test_untracked_eye_and_rating_can_be_repeatedly_undone_without_planning_fallback(self):
        eye_item = self.make_item("repeat-eye-toggle")
        for _ in range(3):
            marked = self.action(eye_item, "mark_read")
            self.assertEqual(marked.data["status"], Status.COMPLETED.value)
            self.assertTrue(Book.objects.get(user=self.user, item=eye_item).completed_manually)

            undone = self.action(
                eye_item,
                "undo_read",
                expected=status.HTTP_204_NO_CONTENT,
            )
            self.assertIsNone(undone.data)
            self.assertFalse(Book.objects.filter(user=self.user, item=eye_item).exists())

        rated_item = self.make_item("rated-eye-toggle")
        rated = self.client.patch(
            self.detail_url(rated_item),
            {"rating": "4.5"},
            format="json",
        )
        self.assert_status(rated, status.HTTP_200_OK)
        self.assertEqual(rated.data["status"], Status.COMPLETED.value)
        book = Book.objects.get(user=self.user, item=rated_item)
        self.assertFalse(book.undated_read_previous_tracked)

        undone = self.action(
            rated_item,
            "undo_read",
            expected=status.HTTP_204_NO_CONTENT,
        )
        self.assertIsNone(undone.data)
        self.assertFalse(Book.objects.filter(user=self.user, item=rated_item).exists())

        marked_then_rated_item = self.make_item("marked-then-rated-eye-toggle")
        self.action(marked_then_rated_item, "mark_read")
        rated = self.client.patch(
            self.detail_url(marked_then_rated_item),
            {"rating": "4.5"},
            format="json",
        )
        self.assert_status(rated, status.HTTP_200_OK)

        undone = self.action(
            marked_then_rated_item,
            "undo_read",
            expected=status.HTTP_204_NO_CONTENT,
        )
        self.assertIsNone(undone.data)
        self.assertFalse(
            Book.objects.filter(user=self.user, item=marked_then_rated_item).exists(),
        )

    # BK-104, BK-300, BK-301, BK-303, BK-304, BK-305, BK-307,
    # BK-308, BK-309.
    def test_live_journey_pause_drop_restart_delete_and_removal_conflict(self):
        item = self.make_item("journey-lifecycle")
        self.set_status(item, Status.PLANNING.value)
        started = self.action(
            item,
            "start",
            {
                "start_date": str(self.today - timedelta(days=8)),
                "mutation_id": str(uuid4()),
            },
        )
        first_id = started.data["book"]["current_journey"]["id"]
        percentage = self.client.post(
            self.progress_url(item),
            {
                "progress_type": "percentage",
                "value": "25",
                "progressed_on": str(self.today - timedelta(days=7)),
            },
            format="json",
        )
        self.assert_status(percentage, status.HTTP_200_OK)
        self.assertEqual(percentage.data["progress"]["value"], 75)

        blocked_remove = self.client.delete(self.detail_url(item))
        self.assert_status(blocked_remove, status.HTTP_409_CONFLICT)
        self.assertTrue(BookSession.objects.filter(pk=first_id).exists())

        paused = self.action(item, "pause")
        self.assertEqual(paused.data["status"], Status.PAUSED.value)
        self.assertEqual(paused.data["book"]["current_journey"]["id"], first_id)
        resumed = self.action(item, "resume")
        self.assertEqual(resumed.data["status"], Status.IN_PROGRESS.value)
        self.assertEqual(resumed.data["book"]["current_journey"]["id"], first_id)
        dropped = self.action(
            item,
            "drop",
            {"end_date": str(self.today - timedelta(days=6))},
        )
        self.assertEqual(dropped.data["status"], Status.DROPPED.value)
        self.assertEqual(dropped.data["book"]["reading_history"][0]["id"], first_id)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())

        second = self.action(
            item,
            "start",
            {
                "start_date": str(self.today - timedelta(days=5)),
                "mutation_id": str(uuid4()),
            },
        )
        second_id = second.data["book"]["current_journey"]["id"]
        pages = self.client.post(
            self.progress_url(item),
            {
                "progress_type": "pages",
                "value": 60,
                "progressed_on": str(self.today - timedelta(days=4)),
            },
            format="json",
        )
        self.assert_status(pages, status.HTTP_200_OK)
        restarted = self.action(
            item,
            "restart",
            {
                "end_date": str(self.today - timedelta(days=3)),
                "start_date": str(self.today - timedelta(days=2)),
                "mutation_id": str(uuid4()),
            },
        )
        third_id = restarted.data["book"]["current_journey"]["id"]
        self.assertNotEqual(third_id, second_id)
        self.assertEqual(restarted.data["book"]["current_journey"]["progress"], None)
        second_session = BookSession.objects.get(pk=second_id)
        self.assertEqual(second_session.status, Status.DROPPED.value)
        self.assertEqual(second_session.pages_read, 60)

        delete_third = self.client.delete(self.journey_url(item, third_id))
        self.assert_status(delete_third, status.HTTP_200_OK)
        self.assertEqual(delete_third.data["book"]["current_journey"]["id"], second_id)
        delete_second = self.client.delete(self.journey_url(item, second_id))
        self.assert_status(delete_second, status.HTTP_200_OK)
        self.assertEqual(delete_second.data["book"]["current_journey"]["id"], first_id)
        delete_first = self.client.delete(self.journey_url(item, first_id))
        self.assert_status(delete_first, status.HTTP_200_OK)
        self.assertEqual(delete_first.data["status"], Status.PLANNING.value)
        self.assertFalse(BookSession.objects.filter(related_book__item=item).exists())

        removed = self.client.delete(self.detail_url(item))
        self.assert_status(removed, status.HTTP_204_NO_CONTENT)

    # BK-302.
    def test_final_progress_requires_completion_without_auto_completing(self):
        item = self.make_item("final-progress")
        self.set_status(item, Status.PLANNING.value)
        started = self.action(item, "start", {"mutation_id": str(uuid4())})
        journey_id = started.data["book"]["current_journey"]["id"]

        final = self.client.post(
            self.progress_url(item),
            {"progress_type": "pages", "value": 300},
            format="json",
        )
        self.assert_status(final, status.HTTP_200_OK)
        self.assertTrue(final.data["book"]["completion_required"])
        self.assertEqual(final.data["status"], Status.IN_PROGRESS.value)
        self.assertEqual(final.data["book"]["current_journey"]["id"], journey_id)
        self.assertEqual(final.data["book"]["completed_journey_count"], 0)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())
        session = BookSession.objects.get(pk=journey_id)
        self.assertEqual(session.status, Status.IN_PROGRESS.value)
        self.assertEqual(session.pages_read, 300)

    # BK-105, BK-106, BK-107, BK-400, BK-401, BK-402, BK-405,
    # BK-406, BK-501.
    def test_direct_and_live_completions_are_atomic_idempotent_and_derived_rereads(self):
        item = self.make_item("atomic-completions")
        first_mutation = uuid4()
        first_date = self.today - timedelta(days=30)
        first = self.complete(
            item,
            completion_date=first_date,
            mutation_id=first_mutation,
            rating="4.0",
        )
        self.assert_status(first, status.HTTP_201_CREATED)
        first_entry_id = first.data["diary_entry"]["id"]
        first_journey_id = first.data["tracking"]["book"]["current_journey"]["id"]
        first_session = BookSession.objects.get(pk=first_journey_id)
        self.assertEqual(first_session.origin, BookSession.Origin.DIRECT_LOG)
        self.assertEqual(first_session.completion_diary_entry_id, first_entry_id)

        retry = self.complete(
            item,
            completion_date=first_date,
            mutation_id=first_mutation,
            rating="4.0",
        )
        self.assert_status(retry, status.HTTP_201_CREATED)
        self.assertEqual(retry.data["diary_entry"]["id"], first_entry_id)
        self.assertEqual(BookSession.objects.filter(related_book__item=item).count(), 1)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=item).count(), 1)

        reread = self.action(
            item,
            "start",
            {
                "start_date": str(self.today - timedelta(days=10)),
                "mutation_id": str(uuid4()),
            },
        )
        live_id = reread.data["book"]["current_journey"]["id"]
        self.assertTrue(reread.data["book"]["is_rereading"])
        progress = self.client.post(
            self.progress_url(item),
            {
                "progress_type": "pages",
                "value": 120,
                "progressed_on": str(self.today - timedelta(days=2)),
            },
            format="json",
        )
        self.assert_status(progress, status.HTTP_200_OK)
        second_mutation = uuid4()
        second = self.complete(
            item,
            completion_date=self.today - timedelta(days=1),
            mutation_id=second_mutation,
            journey_id=live_id,
            rating="4.5",
            liked=True,
            is_rewatch=False,
        )
        self.assert_status(second, status.HTTP_201_CREATED)
        second_entry_id = second.data["diary_entry"]["id"]
        self.assertEqual(second.data["tracking"]["book"]["current_journey"]["id"], live_id)
        self.assertEqual(second.data["tracking"]["book"]["completed_journey_count"], 2)
        self.assertEqual(second.data["tracking"]["book"]["lifetime_read_count"], 2)
        self.assertTrue(BookSession.objects.get(pk=live_id).completion_diary_entry_id)
        self.assertTrue(BookSession.objects.get(pk=live_id).status == Status.COMPLETED.value)
        self.assertTrue(
            next(
                row
                for row in [
                    first.data["tracking"]["book"]["current_journey"],
                    second.data["tracking"]["book"]["current_journey"],
                ]
                if row["id"] == live_id
            )["is_reread"],
        )
        self.assertFalse(DiaryEntry.objects.get(pk=second_entry_id).is_rewatch)
        self.assertEqual(second.data["tracking"]["rating"], "4.5")
        self.assertTrue(second.data["tracking"]["liked"])
        community = media_service.community_stats(
            source=item.source,
            media_type=item.media_type,
            media_id=item.media_id,
        )
        # One reader, counted once at their current rating, however many logs they made.
        self.assertEqual(community["average_rating"], "4.50")
        self.assertEqual(community["rating_distribution"], [{"rating": "4.5", "count": 1}])

        second_retry = self.complete(
            item,
            completion_date=self.today - timedelta(days=1),
            mutation_id=second_mutation,
            journey_id=live_id,
            rating="4.5",
            liked=True,
            is_rewatch=False,
        )
        self.assert_status(second_retry, status.HTTP_201_CREATED)
        self.assertEqual(second_retry.data["diary_entry"]["id"], second_entry_id)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=item).count(), 2)

        dnf_item = self.make_item("dnf-then-direct")
        self.set_status(dnf_item, Status.PLANNING.value)
        self.action(dnf_item, "start", {"mutation_id": str(uuid4())})
        self.action(dnf_item, "drop")
        after_dnf = self.complete(
            dnf_item,
            completion_date=self.today,
            mutation_id=uuid4(),
        )
        self.assert_status(after_dnf, status.HTTP_201_CREATED)
        self.assertFalse(after_dnf.data["diary_entry"]["is_rewatch"])
        self.assertEqual(after_dnf.data["tracking"]["book"]["completed_journey_count"], 1)
        self.assertEqual(len(after_dnf.data["tracking"]["book"]["reading_history"]), 1)

    # BK-306.
    def test_date_validation_rejects_invalid_mutations_without_partial_writes(self):
        item = self.make_item("date-validation")
        self.set_status(item, Status.PLANNING.value)
        future_start = self.action(
            item,
            "start",
            {"start_date": str(self.today + timedelta(days=1))},
            expected=status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn("future", str(future_start.data).lower())
        self.assertFalse(BookSession.objects.filter(related_book__item=item).exists())

        started = self.action(
            item,
            "start",
            {"start_date": str(self.today - timedelta(days=5))},
        )
        journey_id = started.data["book"]["current_journey"]["id"]
        invalid_progress = self.client.post(
            self.progress_url(item),
            {
                "progress_type": "pages",
                "value": 20,
                "progressed_on": str(self.today - timedelta(days=6)),
            },
            format="json",
        )
        self.assert_status(invalid_progress, status.HTTP_400_BAD_REQUEST)
        session = BookSession.objects.get(pk=journey_id)
        self.assertIsNone(session.pages_read)
        self.assertIsNone(session.progressed_on)

        valid_progress = self.client.post(
            self.progress_url(item),
            {
                "progress_type": "pages",
                "value": 20,
                "progressed_on": str(self.today - timedelta(days=4)),
            },
            format="json",
        )
        self.assert_status(valid_progress, status.HTTP_200_OK)
        invalid_drop = self.action(
            item,
            "drop",
            {"end_date": str(self.today - timedelta(days=5))},
            expected=status.HTTP_400_BAD_REQUEST,
        )
        self.assertIn("progress", str(invalid_drop.data).lower())
        self.assertEqual(BookSession.objects.get(pk=journey_id).status, Status.IN_PROGRESS.value)

        invalid_completion = self.complete(
            item,
            completion_date=self.today - timedelta(days=5),
            mutation_id=uuid4(),
            journey_id=journey_id,
        )
        self.assert_status(invalid_completion, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())
        self.assertEqual(BookSession.objects.get(pk=journey_id).status, Status.IN_PROGRESS.value)

        invalid_edit = self.client.patch(
            self.journey_url(item, journey_id),
            {"start_date": str(self.today - timedelta(days=3))},
            format="json",
        )
        self.assert_status(invalid_edit, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            BookSession.objects.get(pk=journey_id).start_date.date(),
            self.today - timedelta(days=5),
        )

        direct_item = self.make_item("future-direct-completion")
        self.set_status(direct_item, Status.PLANNING.value)
        future_completion = self.complete(
            direct_item,
            completion_date=self.today + timedelta(days=1),
            mutation_id=uuid4(),
        )
        self.assert_status(future_completion, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=direct_item).exists())
        self.assertFalse(BookSession.objects.filter(related_book__item=direct_item).exists())

    # BK-201, BK-203.
    def test_live_rating_and_heart_require_completion_without_preliminary_mutation(self):
        live_item = self.make_item("live-heart-rating")
        self.set_status(live_item, Status.PLANNING.value)
        self.action(live_item, "start", {"mutation_id": str(uuid4())})
        blocked_rating = self.client.patch(
            self.detail_url(live_item),
            {"rating": "4.0"},
            format="json",
        )
        self.assert_status(blocked_rating, status.HTTP_409_CONFLICT)
        self.client.raise_request_exception = False
        blocked_heart = self.client.post(
            "/api/v1/me/liked-media/",
            {"ref": self.ref(live_item)},
            format="json",
        )
        self.client.raise_request_exception = True
        self.assert_status(blocked_heart, status.HTTP_409_CONFLICT)
        live_book = Book.objects.get(user=self.user, item=live_item)
        self.assertIsNone(live_book.score)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=live_item).exists())
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=live_item).exists())

    # BK-202, BK-203.
    def test_rating_and_heart_wire_provenance_decouples_and_recouples(self):
        item = self.make_item("rating-heart-provenance")
        completed = self.complete(
            item,
            completion_date=self.today - timedelta(days=2),
            mutation_id=uuid4(),
            rating="4.0",
            liked=True,
        )
        self.assert_status(completed, status.HTTP_201_CREATED)
        entry_id = completed.data["diary_entry"]["id"]
        book = Book.objects.get(user=self.user, item=item)
        self.assertEqual(book.score, 8)
        self.assertEqual(book.rating_source_id, entry_id)
        self.assertEqual(book.like_source_id, entry_id)
        self.assertFalse(book.like_is_independent)
        self.assertEqual(completed.data["tracking"]["rating"], "4.0")

        source_edit = self.client.patch(
            f"/api/v1/diary/{entry_id}/",
            {"rating": "5.0", "liked": False},
            format="json",
        )
        self.assert_status(source_edit, status.HTTP_200_OK)
        book.refresh_from_db()
        self.assertEqual(book.score, 10)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

        independent_rating = self.client.patch(
            self.detail_url(item),
            {"rating": "3.0"},
            format="json",
        )
        self.assert_status(independent_rating, status.HTTP_200_OK)
        independent_heart = self.client.delete(
            "/api/v1/me/liked-media/",
            {"ref": self.ref(item)},
            format="json",
        )
        self.assert_status(independent_heart, status.HTTP_200_OK)
        book.refresh_from_db()
        self.assertIsNone(book.rating_source_id)
        self.assertIsNone(book.like_source_id)
        self.assertTrue(book.like_is_independent)

        source_after_decouple = self.client.patch(
            f"/api/v1/diary/{entry_id}/",
            {"rating": "4.5", "liked": True},
            format="json",
        )
        self.assert_status(source_after_decouple, status.HTTP_200_OK)
        book.refresh_from_db()
        self.assertEqual(book.score, 6)
        self.assertFalse(MediaLike.objects.filter(user=self.user, item=item).exists())

        recouple_rating = self.client.patch(
            self.detail_url(item),
            {"rating": "4.5"},
            format="json",
        )
        self.assert_status(recouple_rating, status.HTTP_200_OK)
        recouple_heart = self.client.post(
            "/api/v1/me/liked-media/",
            {"ref": self.ref(item)},
            format="json",
        )
        self.assert_status(recouple_heart, status.HTTP_200_OK)
        book.refresh_from_db()
        self.assertEqual(book.rating_source_id, entry_id)
        self.assertEqual(book.like_source_id, entry_id)
        self.assertFalse(book.like_is_independent)

        propagated_again = self.client.patch(
            f"/api/v1/diary/{entry_id}/",
            {"rating": "5.0", "liked": False},
            format="json",
        )
        self.assert_status(propagated_again, status.HTTP_200_OK)
        detail = self.client.get(self.detail_url(item))
        self.assert_status(detail, status.HTTP_200_OK)
        self.assertEqual(detail.data["rating"], "5.0")
        self.assertFalse(detail.data["liked"])

        invalid_rating = self.client.patch(
            self.detail_url(item),
            {"rating": "4.25"},
            format="json",
        )
        self.assert_status(invalid_rating, status.HTTP_400_BAD_REQUEST)

    # BK-403, BK-404.
    def test_completion_edit_delete_reopens_live_and_older_delete_preserves_newer(self):
        live_item = self.make_item("delete-live-completion")
        self.set_status(live_item, Status.PLANNING.value)
        prior_rating = self.client.patch(
            self.detail_url(live_item),
            {"rating": "3.0"},
            format="json",
        )
        self.assert_status(prior_rating, status.HTTP_200_OK)
        started = self.action(
            live_item,
            "start",
            {
                "start_date": str(self.today - timedelta(days=5)),
                "mutation_id": str(uuid4()),
            },
        )
        journey_id = started.data["book"]["current_journey"]["id"]
        progress = self.client.post(
            self.progress_url(live_item),
            {
                "progress_type": "pages",
                "value": 120,
                "progressed_on": str(self.today - timedelta(days=4)),
            },
            format="json",
        )
        self.assert_status(progress, status.HTTP_200_OK)
        self.action(live_item, "pause")
        completed = self.complete(
            live_item,
            completion_date=self.today - timedelta(days=2),
            mutation_id=uuid4(),
            journey_id=journey_id,
            rating="4.0",
            liked=True,
        )
        self.assert_status(completed, status.HTTP_201_CREATED)
        entry_id = completed.data["diary_entry"]["id"]

        edited = self.client.patch(
            f"/api/v1/diary/{entry_id}/",
            {
                "consumed_at": str(self.today - timedelta(days=1)),
                "rating": "4.5",
                "liked": False,
            },
            format="json",
        )
        self.assert_status(edited, status.HTTP_200_OK)
        self.assertEqual(
            BookSession.objects.get(pk=journey_id).end_date.date(),
            self.today - timedelta(days=1),
        )
        deleted = self.client.delete(f"/api/v1/diary/{entry_id}/")
        self.assert_status(deleted, status.HTTP_204_NO_CONTENT)
        reopened = BookSession.objects.get(pk=journey_id)
        self.assertEqual(reopened.status, Status.PAUSED.value)
        self.assertEqual(reopened.pages_read, 120)
        self.assertIsNone(reopened.completion_diary_entry_id)
        book = Book.objects.get(user=self.user, item=live_item)
        self.assertEqual(book.current_session_id, journey_id)
        self.assertEqual(book.status, Status.PAUSED.value)
        self.assertEqual(book.score, 6)

        history_item = self.make_item("delete-older-completion")
        first = self.complete(
            history_item,
            completion_date=self.today - timedelta(days=20),
            mutation_id=uuid4(),
        )
        self.assert_status(first, status.HTTP_201_CREATED)
        first_entry = first.data["diary_entry"]["id"]
        second = self.complete(
            history_item,
            completion_date=self.today - timedelta(days=10),
            mutation_id=uuid4(),
        )
        self.assert_status(second, status.HTTP_201_CREATED)
        second_entry = second.data["diary_entry"]["id"]
        second_journey = second.data["tracking"]["book"]["current_journey"]["id"]
        delete_older = self.client.delete(f"/api/v1/diary/{first_entry}/")
        self.assert_status(delete_older, status.HTTP_204_NO_CONTENT)
        book = Book.objects.get(user=self.user, item=history_item)
        self.assertEqual(book.current_session_id, second_journey)
        self.assertEqual(book.completion_diary_entry_id, second_entry)
        self.assertTrue(DiaryEntry.objects.filter(pk=second_entry).exists())
        self.assertEqual(
            BookSession.objects.filter(
                related_book=book,
                status=Status.COMPLETED.value,
            ).count(),
            1,
        )

        direct_restore = self.make_item("delete-latest-direct")
        self.set_status(direct_restore, Status.PLANNING.value)
        direct = self.complete(
            direct_restore,
            completion_date=self.today,
            mutation_id=uuid4(),
        )
        self.assert_status(direct, status.HTTP_201_CREATED)
        delete_direct = self.client.delete(
            f"/api/v1/diary/{direct.data['diary_entry']['id']}/",
        )
        self.assert_status(delete_direct, status.HTTP_204_NO_CONTENT)
        self.assertEqual(
            Book.objects.get(user=self.user, item=direct_restore).status,
            Status.PLANNING.value,
        )

    # BK-500 plus state/count/chronology coverage for BK-405 and BK-406.
    def test_state_count_ordering_matches_detail_library_and_media_projection(self):
        item = self.make_item("cross-endpoint-state")
        liked = self.client.post(
            "/api/v1/me/liked-media/",
            {"ref": self.ref(item)},
            format="json",
        )
        self.assert_status(liked, status.HTTP_200_OK)
        self.client.delete(
            "/api/v1/me/liked-media/",
            {"ref": self.ref(item)},
            format="json",
        )

        newer_date = self.today - timedelta(days=10)
        older_date = self.today - timedelta(days=20)
        newer = self.complete(
            item,
            completion_date=newer_date,
            mutation_id=uuid4(),
            is_rewatch=False,
        )
        self.assert_status(newer, status.HTTP_201_CREATED)
        older = self.complete(
            item,
            completion_date=older_date,
            mutation_id=uuid4(),
            is_rewatch=False,
        )
        self.assert_status(older, status.HTTP_201_CREATED)
        self.action(
            item,
            "start",
            {
                "start_date": str(self.today - timedelta(days=5)),
                "mutation_id": str(uuid4()),
            },
        )
        self.action(item, "drop", {"end_date": str(self.today - timedelta(days=1))})

        detail = self.client.get(self.detail_url(item))
        self.assert_status(detail, status.HTTP_200_OK)
        book_state = detail.data["book"]
        self.assertEqual(book_state["completed_journey_count"], 2)
        self.assertEqual(book_state["lifetime_read_count"], 3)
        self.assertIsNotNone(book_state["undated_read"])
        self.assertEqual(
            book_state["completion_dates"],
            [str(older_date), str(newer_date)],
        )
        self.assertEqual(len(book_state["reading_history"]), 1)
        self.assertEqual(book_state["reading_history"][0]["status"], Status.DROPPED.value)

        library = self.client.get(
            "/api/v1/tracking/",
            {"media_type": "book", "status": Status.DROPPED.value},
        )
        self.assert_status(library, status.HTTP_200_OK)
        library_row = next(
            row
            for row in library.data["results"]
            if row["media"]["ref"]["media_id"] == item.media_id
        )
        self.assertEqual(library_row["tracking"]["book"], book_state)

        media = self.client.get(f"/api/v1/media/manual/book/{item.media_id}/")
        self.assert_status(media, status.HTTP_200_OK)
        self.assertEqual(media.data["user_state"]["book"], book_state)

        diary = self.client.get("/api/v1/diary/", {"item_id": item.id})
        self.assert_status(diary, status.HTTP_200_OK)
        self.assertEqual(
            [row["consumed_at"] for row in diary.data["results"]],
            [str(newer_date), str(older_date)],
        )

        stats = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "all", "end_date": "all"},
        )
        self.assert_status(stats, status.HTTP_200_OK)
        self.assertEqual(stats.data["overview"]["book_read_count"], 3)
        book_stats = next(
            row
            for row in stats.data["media_types"]
            if row["media_type"] == "book"
        )
        self.assertEqual(book_stats["read_count"], 3)
        self.assertEqual(book_stats["tracked_count"], 1)
