from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CustomListItemOccurrenceMigrationTests(TransactionTestCase):
    """``0011`` lets award histories repeat an item and moves notes onto rows."""

    pinned = [
        ("app", "0079_preserve_legacy_game_duplicates"),
        ("users", "0059_remove_user_last_search_type_valid_user_hof_music_and_more"),
    ]
    migrate_from = [*pinned, ("lists", "0010_featured_deferral_and_entry_notes")]
    migrate_to = [*pinned, ("lists", "0011_customlistitem_occurrence_note")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_rows_get_occurrence_zero_and_their_featured_note(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("users", "User")
        Item = old_apps.get_model("app", "Item")
        CustomList = old_apps.get_model("lists", "CustomList")
        CustomListItem = old_apps.get_model("lists", "CustomListItem")
        FeaturedListSync = old_apps.get_model("lists", "FeaturedListSync")

        owner = User.objects.create(username="Spine")
        items = [
            Item.objects.create(source="tmdb", media_type="tv", media_id=f"show-{index}", title=f"Show {index}")
            for index in range(2)
        ]
        featured = CustomList.objects.create(owner_id=owner.pk, name="Emmys", is_featured=True)
        mine = CustomList.objects.create(owner_id=owner.pk, name="Mine")
        for position, item in enumerate(items, start=1):
            CustomListItem.objects.create(custom_list_id=featured.pk, item_id=item.pk, position=position)
        CustomListItem.objects.create(custom_list_id=mine.pk, item_id=items[0].pk)
        FeaturedListSync.objects.create(
            key="emmys",
            custom_list_id=featured.pk,
            entry_notes={str(items[0].pk): "2023, 2022", "not-an-id": "x"},
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewCustomListItem = new_apps.get_model("lists", "CustomListItem")

        self.assertEqual(
            list(
                NewCustomListItem.objects.order_by("custom_list_id", "position").values_list(
                    "custom_list_id",
                    "item_id",
                    "occurrence",
                    "note",
                ),
            ),
            [
                (featured.pk, items[0].pk, 0, "2023, 2022"),
                (featured.pk, items[1].pk, 0, ""),
                (mine.pk, items[0].pk, 0, ""),
            ],
        )
        # A second win is a second row; the same occurrence twice is still refused.
        NewCustomListItem.objects.create(custom_list_id=featured.pk, item_id=items[0].pk, position=3, occurrence=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            NewCustomListItem.objects.create(custom_list_id=mine.pk, item_id=items[0].pk)
        # Code from before the migration inserts without the new columns: the database fills them in.
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO lists_customlistitem (item_id, custom_list_id, position, date_added) "
                "VALUES (%s, %s, %s, CURRENT_TIMESTAMP)",
                [items[1].pk, mine.pk, 2],
            )
        self.assertEqual(
            NewCustomListItem.objects.filter(custom_list_id=mine.pk, item_id=items[1].pk).values_list("occurrence", "note").get(),
            (0, ""),
        )
