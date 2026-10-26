from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from app.models import MusicGenreAlbum

MIGRATION = "0085_musicgenrealbum_search_text"
migration = import_module(f"app.migrations.{MIGRATION}")


class MusicGenreAlbumSearchTextMigrationTests(TestCase):
    """Albums saved before music search used the genre table become searchable."""

    def test_fills_search_text_for_saved_albums(self):
        MusicGenreAlbum.objects.create(
            genre="dance-pop",
            release_group_mbid="renaissance",
            title="RENAISSANCE",
            artist="Beyoncé",
            listeners=11_320,
            listens=453_186,
        )

        state = MigrationExecutor(connection).loader.project_state(("app", MIGRATION))
        migration.fill_search_text(state.apps, None)

        self.assertEqual(MusicGenreAlbum.objects.get().search_text, " renaissance beyonce ")
