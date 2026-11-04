"""Every write a book's number reads moves its owner's stats version (STAT-006).

A `ProgressChange` write is covered only when a tracked row saves in the same
transaction, so these check the writes that save something else: a length, a history
row, a one-time step. With the cache on, a miss here leaves Stats on the old number for
an hour.
"""

import io
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.admin import site
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.urls import reverse

from api.services import stats_cache
from api.services.item_metadata import fill_book_pages, store_item_metadata
from api.tests.test_activity_calc_books import NEW_YORK, BookCalcBase, d
from app import book_tracking
from app.admin import ItemAdmin
from app.models import Book, Item, Status
from social.models import ProgressChange

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "activity-book-cache"}}


@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class BumpTests(BookCalcBase):
    """Each write moves the version of everyone holding the title."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        # Creating the users queued bumps, in a transaction that never commits here.
        setattr(connection, stats_cache.PENDING_ATTR, (None, None))

    def version(self, user):
        return cache.get(stats_cache.user_version_key(user.pk))

    def assert_bumps(self, user, write):
        """Run a write through commit and check the owner's version moved."""
        before = self.version(user)
        with self.captureOnCommitCallbacks(execute=True):
            write()
        self.assertNotEqual(self.version(user), before)

    def holder(self, name="held", pages=None):
        item = self.book(name, pages=pages)
        # On commit, or a bump still queued from here hides the one a test looks for.
        with self.captureOnCommitCallbacks(execute=True):
            self.begin(item, d(2026, 3, 1))
            self.save(item, 25, d(2026, 3, 2), kind="percentage")
        return item

    def test_a_blank_length_filled_bumps_whoever_holds_the_book(self):
        item = self.holder()
        reader = self.follower
        with self.on(d(2026, 3, 1)), self.captureOnCommitCallbacks(execute=True):
            book_tracking.start(reader, item)
        before = (self.version(self.owner), self.version(reader))
        with self.captureOnCommitCallbacks(execute=True):
            fill_book_pages(item, 400)
        self.assertNotEqual(self.version(self.owner), before[0])
        self.assertNotEqual(self.version(reader), before[1])

    def test_a_page_count_edited_in_admin_bumps_whoever_holds_the_book(self):
        """STAT-006, and the one way to give a manual book a length."""
        item = self.holder("admin")
        before = self.version(self.owner)
        item.total_pages = 400
        admin_page = ItemAdmin(Item, site)
        with self.captureOnCommitCallbacks(execute=True):
            admin_page.save_model(None, item, SimpleNamespace(changed_data=["total_pages"]), change=True)
        self.assertNotEqual(self.version(self.owner), before)

    def test_another_edit_in_admin_bumps_no_one(self):
        item = self.holder("admin-title")
        before = self.version(self.owner)
        item.title = "A new title"
        admin_page = ItemAdmin(Item, site)
        with self.captureOnCommitCallbacks(execute=True):
            admin_page.save_model(None, item, SimpleNamespace(changed_data=["title"]), change=True)
        self.assertEqual(self.version(self.owner), before)

    def test_someone_with_only_a_log_counts_as_holding_it(self):
        item = self.book("logged", pages=None)
        with self.captureOnCommitCallbacks(execute=True), patch("app.signals.update_daily_statistics.delay"):
            self.log(item, datetime(2025, 3, 1, tzinfo=UTC))
        self.assert_bumps(self.owner, lambda: fill_book_pages(item, 400))

    def test_store_item_metadata_fills_through_the_same_helper(self):
        item = self.holder("through-metadata")
        self.assert_bumps(self.owner, lambda: store_item_metadata(item, {"title": item.title, "max_progress": 400}))
        item.refresh_from_db()
        self.assertEqual(item.total_pages, 400)

    def test_a_provider_never_replaces_a_length(self):
        item = self.holder("kept", pages=320)
        before = self.version(self.owner)
        with self.captureOnCommitCallbacks(execute=True):
            store_item_metadata(item, {"title": item.title, "max_progress": 999})
            fill_book_pages(item, 999)
        item.refresh_from_db()
        self.assertEqual(item.total_pages, 320)
        self.assertEqual(self.version(self.owner), before)

    def test_the_web_history_delete(self):
        item = self.holder("history")
        book = Book.objects.get(user=self.owner, item=item)
        row = book.history.first()
        row.history_user = self.owner
        with self.captureOnCommitCallbacks(execute=True):
            self.client.force_login(self.owner)
            row.save()

        def delete():
            response = self.client.delete(reverse("delete_history_record", args=["book", row.history_id]))
            self.assertEqual(response.status_code, 200, response.content)

        self.assert_bumps(self.owner, delete)
        self.assertFalse(Book.history.filter(history_id=row.history_id).exists())

    def test_the_undated_reads_step(self):
        item = self.book("undated-step")
        Book.objects.bulk_create([Book(user=self.owner, item=item, status=Status.COMPLETED.value)])
        self.assert_bumps(
            self.owner,
            lambda: call_command("fill_undated_reads", "--apply", stdout=io.StringIO(), stderr=io.StringIO()),
        )

    def test_the_past_restarts_step(self):
        item = self.book("restart-step")
        with self.captureOnCommitCallbacks(execute=True):
            self.begin(item, d(2026, 3, 1))
            self.do(book_tracking.restart, item, d(2026, 3, 1))
        # An old Restart: its start row has no flag.
        change = ProgressChange.objects.filter(item=item, current_progress__has_key="began_over").get()
        del change.current_progress["began_over"]
        change.save(update_fields=["current_progress"])
        self.assert_bumps(
            self.owner,
            lambda: call_command("flag_past_restarts", "--apply", stdout=io.StringIO(), stderr=io.StringIO()),
        )
