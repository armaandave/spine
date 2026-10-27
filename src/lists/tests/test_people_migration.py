from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PeopleListsMigrationTests(TransactionTestCase):
    migrate_from = [
        ("app", "0076_bookcreditoverride"),
        ("users", "0059_remove_user_last_search_type_valid_user_hof_music_and_more"),
        ("lists", "0007_customlist_featured_import"),
    ]
    migrate_to = [
        ("app", "0076_bookcreditoverride"),
        ("users", "0059_remove_user_last_search_type_valid_user_hof_music_and_more"),
        (
            "lists",
            "0008_personlistitem_customlist_list_type_and_more",
        ),
    ]

    def tearDown(self):
        # Restore every app's latest migration, so later tests in this process don't
        # run against a partly migrated database (the pinned state predates users 0060).
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_and_reverse_preserve_existing_media_lists(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("users", "User")
        Item = old_apps.get_model("app", "Item")
        CustomList = old_apps.get_model("lists", "CustomList")
        CustomListItem = old_apps.get_model("lists", "CustomListItem")

        user = User.objects.create(username="legacy-list-owner")
        custom_list = CustomList.objects.create(
            owner_id=user.pk,
            name="Legacy Ranked List",
            is_ranked=True,
        )
        first_item = Item.objects.create(
            source="tmdb",
            media_type="movie",
            media_id="legacy-1",
            title="Legacy One",
            image="https://example.com/legacy-1.jpg",
        )
        second_item = Item.objects.create(
            source="tmdb",
            media_type="movie",
            media_id="legacy-2",
            title="Legacy Two",
            image="https://example.com/legacy-2.jpg",
        )
        first_membership = CustomListItem.objects.create(
            custom_list_id=custom_list.pk,
            item_id=first_item.pk,
            position=1,
        )
        second_membership = CustomListItem.objects.create(
            custom_list_id=custom_list.pk,
            item_id=second_item.pk,
            position=2,
        )
        expected_memberships = list(
            CustomListItem.objects.filter(custom_list_id=custom_list.pk)
            .order_by("position")
            .values_list("id", "item_id", "position", "date_added"),
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewCustomList = new_apps.get_model("lists", "CustomList")
        NewCustomListItem = new_apps.get_model("lists", "CustomListItem")
        PersonListItem = new_apps.get_model("lists", "PersonListItem")

        migrated_list = NewCustomList.objects.get(pk=custom_list.pk)
        self.assertEqual(migrated_list.list_type, "media")
        self.assertEqual(
            list(
                NewCustomListItem.objects.filter(custom_list_id=custom_list.pk)
                .order_by("position")
                .values_list("id", "item_id", "position", "date_added"),
            ),
            expected_memberships,
        )
        self.assertEqual(
            [first_membership.pk, second_membership.pk],
            [row[0] for row in expected_memberships],
        )

        backward_writer_list = CustomList.objects.create(
            owner_id=user.pk,
            name="Created By Old Code",
        )
        self.assertEqual(
            NewCustomList.objects.get(pk=backward_writer_list.pk).list_type,
            "media",
        )

        PersonListItem.objects.create(
            custom_list_id=custom_list.pk,
            source="tmdb",
            person_id="31",
            name="Tom Hanks",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        reverted_apps = executor.loader.project_state(self.migrate_from).apps
        RevertedCustomList = reverted_apps.get_model("lists", "CustomList")
        RevertedCustomListItem = reverted_apps.get_model(
            "lists",
            "CustomListItem",
        )

        self.assertTrue(
            RevertedCustomList.objects.filter(pk=custom_list.pk).exists(),
        )
        self.assertTrue(
            RevertedCustomList.objects.filter(pk=backward_writer_list.pk).exists(),
        )
        self.assertEqual(
            list(
                RevertedCustomListItem.objects.filter(
                    custom_list_id=custom_list.pk,
                )
                .order_by("position")
                .values_list("id", "item_id", "position", "date_added"),
            ),
            expected_memberships,
        )
