from datetime import timedelta
from io import StringIO
from unittest.mock import call, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from app.models import Item, MediaTypes, Sources
from app.providers import nytbooks
from lists.management.commands.sync_nyt_featured_lists import (
    _resolve_book,
    _sync_chart,
)
from lists.models import CustomList, CustomListItem, FeaturedListSync


def chart_payload(definition, *, count=2):
    return {
        "slug": definition["slug"],
        "name": definition["fallback_name"],
        "published_date": "2026-07-19",
        "books": [
            {
                "rank": rank,
                "title": f"{definition['slug']} {rank}",
                "isbns": [f"{definition['featured_position']}-{rank}-13", f"{rank}-10"],
            }
            for rank in range(1, count + 1)
        ],
    }


def hardcover_result(isbn):
    return {
        "source": Sources.HARDCOVER.value,
        "media_id": isbn,
        "title": f"Book {isbn}",
        "image": f"https://example.com/{isbn}.jpg",
        "total_pages": 300,
    }


class SyncNYTFeaturedListsTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="Spine")

    def test_resolver_tries_every_hardcover_isbn_before_openlibrary(self):
        book = {"isbns": ["ISBN13", "ISBN10"]}
        with (
            patch(
                "lists.management.commands.sync_nyt_featured_lists.hardcover.lookup_book_by_isbn",
                side_effect=[None, None],
            ) as hardcover_lookup,
            patch(
                "lists.management.commands.sync_nyt_featured_lists.openlibrary.lookup_book_by_isbn",
                return_value={"source": Sources.OPENLIBRARY.value, "media_id": "work"},
            ) as openlibrary_lookup,
        ):
            result = _resolve_book(book)

        self.assertEqual(result["media_id"], "work")
        self.assertEqual(
            hardcover_lookup.call_args_list,
            [call("ISBN13"), call("ISBN10")],
        )
        openlibrary_lookup.assert_called_once_with("ISBN13")

    @patch(
        "lists.management.commands.sync_nyt_featured_lists.hardcover.lookup_book_by_isbn",
        side_effect=lambda isbn: hardcover_result(isbn),
    )
    @patch("lists.management.commands.sync_nyt_featured_lists.nytbooks.get_current_chart")
    def test_command_creates_five_native_ranked_lists(self, get_chart, _lookup):
        get_chart.side_effect = [
            chart_payload(definition, count=10) for definition in nytbooks.FEATURED_CHARTS
        ]

        call_command(
            "sync_nyt_featured_lists",
            owner="Spine",
            stdout=StringIO(),
        )

        lists = list(
            CustomList.objects.filter(owner=self.owner, import_source="nyt").order_by(
                "featured_position",
            ),
        )
        self.assertEqual(len(lists), 5)
        self.assertEqual([item.featured_position for item in lists], [2, 3, 4, 5, 6])
        self.assertEqual(
            [item.name for item in lists],
            [definition["display_name"] for definition in nytbooks.FEATURED_CHARTS],
        )
        self.assertTrue(all(item.visibility == CustomList.Visibility.PUBLIC for item in lists))
        self.assertTrue(all(item.is_ranked and item.is_featured for item in lists))
        self.assertTrue(all(item.tags == ["NYT Best Sellers", "Books"] for item in lists))
        self.assertEqual(
            list(
                lists[0].customlistitem_set.values_list(
                    "position",
                    flat=True,
                ),
            ),
            list(range(1, 11)),
        )
        self.assertEqual(get_chart.call_count, 5)

    def test_sync_keeps_resolved_subset_when_some_books_are_unavailable(self):
        definition = nytbooks.FEATURED_CHARTS[0]
        chart = chart_payload(definition, count=5)

        with patch(
            "lists.management.commands.sync_nyt_featured_lists._resolve_book",
            side_effect=[
                hardcover_result("one"),
                hardcover_result("two"),
                hardcover_result("three"),
                None,
                None,
            ],
        ):
            custom_list, stored_count, resolved_count = _sync_chart(
                self.owner,
                definition,
                chart,
            )

        self.assertEqual(stored_count, 3)
        self.assertEqual(resolved_count, 3)
        self.assertEqual(
            list(custom_list.customlistitem_set.values_list("position", flat=True)),
            [1, 2, 3],
        )

    def test_sync_preserves_existing_list_when_nothing_resolves(self):
        definition = nytbooks.FEATURED_CHARTS[0]
        existing_item = Item.objects.create(
            source=Sources.HARDCOVER.value,
            media_type=MediaTypes.BOOK.value,
            media_id="existing",
            title="Existing",
            image="https://example.com/existing.jpg",
        )
        existing_list = CustomList.objects.create(
            owner=self.owner,
            name="Existing",
            import_source="nyt",
            import_source_id=definition["slug"],
        )
        CustomListItem.objects.create(
            custom_list=existing_list,
            item=existing_item,
            position=1,
        )

        with patch(
            "lists.management.commands.sync_nyt_featured_lists._resolve_book",
            return_value=None,
        ):
            with self.assertRaisesMessage(CommandError, "no books resolved"):
                _sync_chart(
                    self.owner,
                    definition,
                    chart_payload(definition, count=2),
                )

        self.assertEqual(
            list(existing_list.items.values_list("media_id", flat=True)),
            ["existing"],
        )

    @patch(
        "lists.management.commands.sync_nyt_featured_lists.hardcover.lookup_book_by_isbn",
        side_effect=lambda isbn: hardcover_result(isbn),
    )
    @patch("lists.management.commands.sync_nyt_featured_lists.nytbooks.get_current_chart")
    def test_rerun_replaces_items_without_replacing_list(self, get_chart, _lookup):
        get_chart.side_effect = [
            chart_payload(definition, count=10)  # a complete chart: NYT lists 10 or 15 books
            for definition in nytbooks.FEATURED_CHARTS
        ]
        call_command("sync_nyt_featured_lists", owner="Spine", stdout=StringIO())
        first = CustomList.objects.get(
            owner=self.owner,
            import_source="nyt",
            import_source_id=nytbooks.FEATURED_CHARTS[0]["slug"],
        )
        first_id = first.id

        get_chart.side_effect = [
            chart_payload(definition, count=15)
            for definition in nytbooks.FEATURED_CHARTS
        ]
        call_command("sync_nyt_featured_lists", owner="Spine", stdout=StringIO())

        first.refresh_from_db()
        self.assertEqual(first.id, first_id)
        self.assertEqual(first.items.count(), 15)

    @patch(
        "lists.management.commands.sync_nyt_featured_lists.hardcover.lookup_book_by_isbn",
        side_effect=lambda isbn: hardcover_result(isbn),
    )
    @patch("lists.management.commands.sync_nyt_featured_lists.nytbooks.get_current_chart")
    def test_celery_task_skips_charts_the_engine_synced_recently(self, get_chart, _lookup):
        from lists.tasks import sync_nyt_featured_lists

        first, *others = nytbooks.FEATURED_CHARTS
        FeaturedListSync.objects.create(key=f"nyt-{first['slug']}", last_success_at=timezone.now() - timedelta(hours=3))
        FeaturedListSync.objects.create(key=f"nyt-{others[0]['slug']}", last_success_at=timezone.now() - timedelta(hours=13))
        get_chart.side_effect = [chart_payload(definition) for definition in others]

        sync_nyt_featured_lists()

        self.assertEqual([call.args[0] for call in get_chart.call_args_list], [definition["slug"] for definition in others])
        self.assertFalse(CustomList.objects.filter(import_source_id=first["slug"]).exists())
        self.assertEqual(CustomList.objects.filter(import_source="nyt").count(), len(others))

    def test_celery_task_uses_the_management_command(self):
        with patch("lists.tasks.call_command", return_value=None) as command:
            from lists.tasks import sync_nyt_featured_lists

            sync_nyt_featured_lists()

        command.assert_called_once_with("sync_nyt_featured_lists", owner="Spine", skip_synced_within=12)
        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULE["sync_nyt_featured_lists"],
            {
                "task": "Sync NYT featured lists",
                "schedule": 60 * 60 * 24,
            },
        )
