from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class BookTrackingMigrationTests(TransactionTestCase):
    """Verify legacy book rows are safe before journey constraints apply."""

    migrate_from = [("app", "0074_migrate_legacy_external_ratings")]
    migrate_to = [
        (
            "app",
            "0075_book_current_session_book_like_is_independent_and_more",
        ),
    ]

    def tearDown(self):
        # Restore every app's latest migration, so the next migration test starts
        # from the leaf (a partial state can force a mixed forwards and backwards plan).
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_normalizes_legacy_book_rows_without_discarding_progress(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        Item = old_apps.get_model("app", "Item")
        Book = old_apps.get_model("app", "Book")
        BookSession = old_apps.get_model("app", "BookSession")
        DiaryEntry = old_apps.get_model("app", "DiaryEntry")
        HistoricalBookSession = old_apps.get_model(
            "app",
            "HistoricalBookSession",
        )
        user = get_user_model().objects.create_user(
            username="book-migration",
            password="password",
        )
        now = timezone.now()

        def make_item(media_id):
            return Item.objects.create(
                source="manual",
                media_type="book",
                media_id=media_id,
                title=f"Book {media_id}",
                image="https://example.com/book.jpg",
                total_pages=300,
            )

        matching_item = make_item("matching-completion")
        matching_diary = DiaryEntry.objects.create(
            user_id=user.pk,
            item_id=matching_item.pk,
            consumed_at=now - timedelta(days=10),
        )
        matching_book = Book.objects.create(
            user_id=user.pk,
            item_id=matching_item.pk,
            status="Completed",
            completion_diary_entry_id=matching_diary.pk,
        )
        matching_session = BookSession.objects.create(
            related_book_id=matching_book.pk,
            status="Completed",
            pages_read=300,
            percentage_read=100,
            end_date=matching_diary.consumed_at,
        )
        unmatched_session = BookSession.objects.create(
            related_book_id=matching_book.pk,
            status="Completed",
            pages_read=250,
            percentage_read=83,
            end_date=now - timedelta(days=20),
        )

        missing_item = make_item("missing-completion")
        missing_diary = DiaryEntry.objects.create(
            user_id=user.pk,
            item_id=missing_item.pk,
            consumed_at=now - timedelta(days=5),
        )
        missing_book = Book.objects.create(
            user_id=user.pk,
            item_id=missing_item.pk,
            status="Completed",
            completion_diary_entry_id=missing_diary.pk,
        )

        manual_item = make_item("manual-completion")
        manual_book = Book.objects.create(
            user_id=user.pk,
            item_id=manual_item.pk,
            status="Completed",
            completed_manually=True,
        )
        planning_item = make_item("planning")
        planning_book = Book.objects.create(
            user_id=user.pk,
            item_id=planning_item.pk,
            status="Planning",
            completed_manually=True,
        )

        duplicate_item = make_item("duplicate-open")
        duplicate_book = Book.objects.create(
            user_id=user.pk,
            item_id=duplicate_item.pk,
            status="Paused",
        )
        oldest = BookSession.objects.create(
            related_book_id=duplicate_book.pk,
            status="In progress",
            pages_read=25,
            percentage_read=10,
        )
        middle = BookSession.objects.create(
            related_book_id=duplicate_book.pk,
            status="Paused",
            pages_read=50,
            percentage_read=20,
        )
        newest = BookSession.objects.create(
            related_book_id=duplicate_book.pk,
            status="In progress",
            pages_read=75,
            percentage_read=30,
        )
        BookSession.objects.filter(pk=oldest.pk).update(
            created_at=now - timedelta(days=3),
        )
        BookSession.objects.filter(pk=middle.pk).update(
            created_at=now - timedelta(days=2),
        )
        BookSession.objects.filter(pk=newest.pk).update(
            created_at=now - timedelta(days=1),
        )

        ambiguous_item = make_item("ambiguous-open")
        ambiguous_book = Book.objects.create(
            user_id=user.pk,
            item_id=ambiguous_item.pk,
            status="Planning",
        )
        ambiguous_session = BookSession.objects.create(
            related_book_id=ambiguous_book.pk,
            status="In progress",
            pages_read=12,
        )
        historical_session = HistoricalBookSession.objects.create(
            id=999,
            status="In progress",
            history_date=now,
            history_type="+",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewBook = new_apps.get_model("app", "Book")
        NewBookSession = new_apps.get_model("app", "BookSession")
        NewHistoricalBookSession = new_apps.get_model(
            "app",
            "HistoricalBookSession",
        )

        linked_match = NewBookSession.objects.get(pk=matching_session.pk)
        self.assertEqual(
            linked_match.completion_diary_entry_id,
            matching_diary.pk,
        )
        self.assertEqual(linked_match.origin, "legacy")
        self.assertIsNone(
            NewBookSession.objects.get(pk=unmatched_session.pk).completion_diary_entry_id,
        )
        self.assertFalse(
            NewBook.objects.get(pk=matching_book.pk).completed_manually,
        )

        created_completion = NewBookSession.objects.get(
            related_book_id=missing_book.pk,
            completion_diary_entry_id=missing_diary.pk,
        )
        self.assertEqual(created_completion.status, "Completed")
        self.assertEqual(created_completion.origin, "legacy")
        self.assertEqual(created_completion.end_date, missing_diary.consumed_at)

        normalized_manual = NewBook.objects.get(pk=manual_book.pk)
        self.assertTrue(normalized_manual.completed_manually)
        self.assertIsNone(normalized_manual.start_date)
        self.assertIsNone(normalized_manual.end_date)
        self.assertFalse(
            NewBookSession.objects.filter(related_book_id=manual_book.pk).exists(),
        )
        self.assertFalse(
            NewBook.objects.get(pk=planning_book.pk).completed_manually,
        )

        normalized_duplicate = NewBook.objects.get(pk=duplicate_book.pk)
        self.assertEqual(normalized_duplicate.current_session_id, newest.pk)
        open_sessions = NewBookSession.objects.filter(
            related_book_id=duplicate_book.pk,
            status__in=["In progress", "Paused"],
        )
        self.assertEqual(open_sessions.count(), 1)
        self.assertEqual(open_sessions.get().pk, newest.pk)
        self.assertEqual(open_sessions.get().status, "Paused")
        stale_sessions = NewBookSession.objects.filter(
            pk__in=[oldest.pk, middle.pk],
        ).order_by("pk")
        self.assertEqual(
            [(row.status, row.origin, row.pages_read) for row in stale_sessions],
            [("Dropped", "legacy", 25), ("Dropped", "legacy", 50)],
        )

        self.assertIsNone(
            NewBook.objects.get(pk=ambiguous_book.pk).current_session_id,
        )
        preserved_ambiguous = NewBookSession.objects.get(pk=ambiguous_session.pk)
        self.assertEqual(preserved_ambiguous.status, "In progress")
        self.assertEqual(preserved_ambiguous.pages_read, 12)
        self.assertEqual(preserved_ambiguous.origin, "legacy")
        self.assertEqual(
            NewHistoricalBookSession.objects.get(
                pk=historical_session.pk,
            ).origin,
            "legacy",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            NewBookSession.objects.create(
                related_book_id=duplicate_book.pk,
                status="In progress",
            )
