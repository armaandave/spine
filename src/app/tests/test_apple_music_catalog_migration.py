from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

MBID = "3bd76d40-7f0e-36b7-9348-91a33afee20e"


class AppleMusicCatalogMigrationTests(TransactionTestCase):
    """The Apple Music catalog migrations are additive, and reversible while unused."""

    migrate_from = [
        ("app", "0085_musicgenrealbum_search_text"),
        ("lists", "0011_customlistitem_occurrence_note"),
    ]
    migrate_to = [
        ("app", "0086_apple_music_catalog"),
        ("lists", "0012_personlistitem_apple_music"),
    ]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def create_item(self, Item, **fields):
        return Item.objects.create(
            media_type="music",
            title="Year Zero",
            image="https://example.com/year-zero.jpg",
            **fields,
        )

    def test_forward_accepts_apple_music_and_reverse_keeps_existing_items(self):
        executor = MigrationExecutor(connection)
        # Reverse the metadata-version branch first: on SQLite, reversing 0086 rebuilds
        # the Item table from a state without metadata_version, so 0088_item_metadata_version
        # would then have no column left to drop.
        executor.migrate([("app", "0087_merge_tv_tracking_and_music_search")])
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldItem = old_apps.get_model("app", "Item")
        album = self.create_item(OldItem, media_id=MBID, source="musicbrainz")
        movie = OldItem.objects.create(
            media_id="550",
            source="tmdb",
            media_type="movie",
            title="Fight Club",
            image="https://example.com/fight-club.jpg",
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_item(OldItem, media_id="1440826538", source="apple_music")

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new_apps = executor.loader.project_state(self.migrate_to).apps
        Item = new_apps.get_model("app", "Item")
        self.assertEqual(
            set(Item.objects.values_list("pk", "source")),
            {(album.pk, "musicbrainz"), (movie.pk, "tmdb")},
        )
        apple = self.create_item(Item, media_id="1440826538", source="apple_music")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create_item(Item, media_id="1", source="not_a_source")
        tables = connection.introspection.table_names()
        self.assertIn("app_musicalbumlink", tables)
        self.assertIn("app_applealbum", tables)
        MusicAlbumLink = new_apps.get_model("app", "MusicAlbumLink")
        AppleAlbum = new_apps.get_model("app", "AppleAlbum")
        link = MusicAlbumLink.objects.create(
            release_group_mbid=MBID,
            apple_album_id="1440826538",
            item_id=apple.pk,
            status="applied",
            method="upc_exact",
            tier=1,
        )
        AppleAlbum.objects.create(apple_album_id="1147788150", canonical_id="1440826538")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MusicAlbumLink.objects.create(release_group_mbid=MBID, status="matched")

        # Deleting an Item leaves its link row, without the Item.
        apple.delete()
        link.refresh_from_db()
        self.assertIsNone(link.item_id)

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        reverted_apps = executor.loader.project_state(self.migrate_from).apps
        self.assertEqual(
            set(reverted_apps.get_model("app", "Item").objects.values_list("pk", flat=True)),
            {album.pk, movie.pk},
        )
        tables = connection.introspection.table_names()
        self.assertNotIn("app_musicalbumlink", tables)
        self.assertNotIn("app_applealbum", tables)
