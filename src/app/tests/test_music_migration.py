from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MusicMigrationTests(TransactionTestCase):
    """The additive music migrations preserve existing media in both directions."""

    migrate_from = [
        ("app", "0070_customlogopreference"),
        ("users", "0058_user_profile_backdrop_url"),
    ]
    migrate_to = [
        ("app", "0071_historicalmusic_music_and_more"),
        ("users", "0059_remove_user_last_search_type_valid_user_hof_music_and_more"),
    ]

    def tearDown(self):
        # Restore every app's latest migration, so the next migration test starts
        # from the leaf (a partial state can force a mixed forwards and backwards plan).
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_defaults_and_reverse_preserve_existing_media(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("users", "User")
        Item = old_apps.get_model("app", "Item")
        Movie = old_apps.get_model("app", "Movie")
        user = User.objects.create(username="existing-listener")
        item = Item.objects.create(
            media_id="550",
            source="tmdb",
            media_type="movie",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        Movie.objects.create(user_id=user.id, item_id=item.id, status="Planning")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        NewUser = new_apps.get_model("users", "User")
        self.assertTrue(NewUser.objects.get(pk=user.id).music_enabled)
        self.assertTrue(new_apps.get_model("app", "Movie").objects.filter(item_id=item.id).exists())

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        reverted_apps = executor.loader.project_state(self.migrate_from).apps
        self.assertTrue(reverted_apps.get_model("users", "User").objects.filter(pk=user.id).exists())
        self.assertTrue(reverted_apps.get_model("app", "Item").objects.filter(pk=item.id).exists())
        self.assertTrue(reverted_apps.get_model("app", "Movie").objects.filter(item_id=item.id).exists())
