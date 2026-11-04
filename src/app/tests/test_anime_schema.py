"""Migrations, defaults and the one-record constraint (AN-001, AN-901, AN-902)."""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from app.models import Anime, Item, MediaTypes, Sources, Status
from app.tests.test_tv_schema import insert_like_old_code

APP_BEFORE = ("app", "0094_manga_comic_attempts")
USERS_BEFORE = ("users", "0002_initial")


def make_item(media_id="1"):
    return Item.objects.create(
        media_id=media_id,
        source=Sources.MAL.value,
        media_type=MediaTypes.ANIME.value,
        title="Title",
        image="https://example.com/p.jpg",
    )


class AN901RollbackInsertTests(TestCase):
    """AN-901: code from before 0095 (a rollback) keeps inserting and saving rows.

    The inserts name only the columns the pre-0095 models know, as that code would; the
    database fills in the rest.
    """

    def setUp(self):
        state = MigrationExecutor(connection).loader.project_state([APP_BEFORE])
        self.old = state.apps
        self.user = get_user_model().objects.create_user(username="old-code", password="!")
        self.moment = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

    def test_n_29_old_inserts_save_by_the_database_defaults(self):
        item = make_item()
        insert_like_old_code(
            self.old.get_model("app", "Anime"),
            item_id=item.pk,
            user_id=self.user.pk,
            status=Status.IN_PROGRESS.value,
            progress=5,
            notes="",
        )
        row = Anime.objects.get(item=item, user=self.user)
        self.assertEqual(
            (
                row.like_is_independent,
                row.choice,
                row.ended_choice,
                row.pass_number,
                row.state_version,
                row.up_next_is_new,
                row.finished,
                row.caught_up,
                row.reconciled_at,
            ),
            (False, "", "", 1, 0, False, False, False, None),
        )

    def test_an_901_old_inserts_into_the_history_table_read_back(self):
        item = make_item("2")
        row = Anime(item=item, user=self.user, status=Status.PLANNING.value)
        row.save_base(force_insert=True)
        insert_like_old_code(
            self.old.get_model("app", "HistoricalAnime"),
            id=row.pk,
            status=Status.PLANNING.value,
            progress=0,
            notes="",
            history_date=self.moment,
            history_type="~",
        )
        history = row.history.order_by("history_date").first()
        self.assertEqual(
            (history.choice, history.ended_choice, history.like_is_independent, history.pass_number),
            ("", "", False, 1),
        )


class AN001OneRecordTests(TestCase):
    """AN-001: one tracking record per person and title."""

    def test_an_001_a_second_row_for_one_title_is_refused(self):
        user = get_user_model().objects.create_user(username="once", password="!")
        item = make_item("3")
        Anime(item=item, user=user, status=Status.PLANNING.value).save_base(force_insert=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Anime(item=item, user=user, status=Status.COMPLETED.value).save_base(force_insert=True)
        self.assertEqual(Anime.objects.filter(item=item, user=user).count(), 1)

    def test_an_901_the_migration_only_adds(self):
        """The migration holds no data step and removes nothing (AN-901, AN-902)."""
        from importlib import import_module

        from django.db.migrations import operations

        migration = import_module("app.migrations.0095_anime_tracking").Migration
        allowed = (
            operations.CreateModel,
            operations.AddField,
            operations.AddIndex,
            operations.AddConstraint,
        )
        self.assertTrue(all(isinstance(operation, allowed) for operation in migration.operations))
