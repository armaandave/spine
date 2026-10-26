"""The TV tracking schema (docs/tv-tracking-contract.md, TV-901, TV-205, TV-207, TV-705).

Migrations are additive and rollback-safe: every new NOT NULL column has a database
default, so code from before 0085 keeps inserting rows, and those rows come back as
legacy shows (TV-801) and ticks. The model cascades no longer run: app.tv_tracking
works out every status from the facts.
"""

import uuid
from datetime import UTC, datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models import RestrictedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from app.models import (
    TV,
    TV_STATE_CACHE_FIELD_NAMES,
    DiaryEntry,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
    TVListing,
    TVMutation,
)
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user

APP_BEFORE = ("app", "0084_fill_empty_ratings_from_rated_logs")
APP_AFTER = ("app", "0085_tv_tracking_state")
USERS_BEFORE = ("users", "0059_remove_user_last_search_type_valid_user_hof_music_and_more")
USERS_AFTER = ("users", "0060_user_calendar_timezone")
FACT_FIELDS = (
    "rating_source",
    "like_source",
    "like_is_independent",
    "choice",
    "choice_at",
    "ended_choice",
    "ended_choice_at",
    "rewatch_started_at",
    "rewatch_season_number",
    "earlier_question_at",
    "legacy_pending",
)


def season_item(show, number):
    return Item.objects.create(
        media_id=show.media_id,
        source=show.source,
        media_type=MediaTypes.SEASON.value,
        season_number=number,
        title=show.title,
        image=show.image,
    )


def episode_item(show, season_number, episode_number):
    return Item.objects.create(
        media_id=show.media_id,
        source=show.source,
        media_type=MediaTypes.EPISODE.value,
        season_number=season_number,
        episode_number=episode_number,
        title=f"Episode {episode_number}",
        image=show.image,
    )


def insert_like_old_code(model, **values):
    """INSERT a row with raw SQL naming only the columns a pre-0085 model knows."""
    instance = model(**values)
    fields = [
        field
        for field in model._meta.concrete_fields
        if not (field.primary_key and getattr(instance, field.attname) is None)
    ]
    columns = ", ".join(connection.ops.quote_name(field.column) for field in fields)
    params = [
        field.get_db_prep_save(field.pre_save(instance, add=True), connection)
        for field in fields
    ]
    placeholders = ", ".join(["%s"] * len(fields))
    table = connection.ops.quote_name(model._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", params)  # noqa: S608


class NoCalendarTestCase(TestCase):
    """Keep status saves from running the calendar (eager Celery) and its provider calls."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch("app.models.Item.fetch_releases")
        self.fetch_releases = patcher.start()
        self.addCleanup(patcher.stop)


class TV901DefaultsTests(NoCalendarTestCase):
    """TV-901: new fields' Python defaults, and rows new code creates aren't legacy."""

    def test_tv_901_python_defaults(self):
        tv = TV()
        self.assertEqual(
            (
                tv.choice,
                tv.ended_choice,
                tv.like_is_independent,
                tv.legacy_pending,
                tv.state_version,
                tv.up_next_is_new,
                tv.finished,
                tv.caught_up,
                tv.finish_offers,
                tv.reconciled_at,
                tv.watched_count,
            ),
            ("", "", False, False, 0, False, False, False, None, None, None),
        )
        season = Season()
        self.assertEqual((season.like_is_independent, season.is_started), (False, None))
        episode = Episode()
        self.assertEqual(
            (episode.kind, episode.diary_entry_id, episode.watched_on),
            (Episode.Kind.TICK, None, None),
        )
        self.assertEqual(get_user_model()().calendar_timezone, "")

    def test_tv_901_rows_created_through_the_orm_are_not_legacy(self):
        user = make_user()
        tv = TV.objects.create(item=make_show(), user=user, status=Status.IN_PROGRESS.value)
        tv.refresh_from_db()
        self.assertFalse(tv.legacy_pending)
        self.assertEqual(tv.state_version, 0)
        imported = TV(item=make_show(), user=user, status=Status.COMPLETED.value)
        TV.save_base(imported)
        imported.refresh_from_db()
        self.assertFalse(imported.legacy_pending)


class TV901RollbackInsertTests(TestCase):
    """TV-901: code from before 0085 (a rollback) keeps inserting rows.

    The inserts name only the columns the pre-0085 models know, as that code would;
    the database fills in the rest.
    """

    def setUp(self):
        state = MigrationExecutor(connection).loader.project_state([APP_BEFORE, USERS_BEFORE])
        self.old = state.apps
        self.moment = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

    def test_tv_901_old_inserts_read_back_with_database_defaults(self):
        insert_like_old_code(
            self.old.get_model("users", "User"),
            username="old-code-user",
            password="!",
        )
        user = get_user_model().objects.get(username="old-code-user")
        self.assertEqual(user.calendar_timezone, "")

        show = make_show(media_id="1001")
        insert_like_old_code(
            self.old.get_model("app", "TV"),
            item_id=show.pk,
            user_id=user.pk,
            status=Status.COMPLETED.value,
            notes="",
        )
        tv = TV.objects.get(item=show, user=user)
        self.assertEqual(
            (
                tv.legacy_pending,
                tv.state_version,
                tv.choice,
                tv.ended_choice,
                tv.like_is_independent,
                tv.up_next_is_new,
                tv.finished,
                tv.caught_up,
                tv.finish_offers,
                tv.reconciled_at,
            ),
            (True, 0, "", "", False, False, False, False, None, None),
        )

        insert_like_old_code(
            self.old.get_model("app", "Season"),
            item_id=season_item(show, 1).pk,
            user_id=user.pk,
            related_tv_id=tv.pk,
            status=Status.IN_PROGRESS.value,
            notes="",
        )
        season = Season.objects.get(related_tv=tv)
        self.assertEqual((season.like_is_independent, season.is_started), (False, None))

        insert_like_old_code(
            self.old.get_model("app", "Episode"),
            item_id=episode_item(show, 1, 1).pk,
            related_season_id=season.pk,
            end_date=self.moment,
        )
        episode = Episode.objects.get(related_season=season)
        self.assertEqual(
            (episode.kind, episode.diary_entry_id, episode.watched_on),
            ("tick", None, None),
        )

    def test_tv_901_old_history_inserts_read_back_with_database_defaults(self):
        user = make_user()
        history = {
            "history_date": self.moment,
            "history_type": "+",
            "history_user_id": user.pk,
        }
        insert_like_old_code(
            self.old.get_model("app", "HistoricalTV"),
            id=9001,
            status=Status.COMPLETED.value,
            notes="",
            **history,
        )
        insert_like_old_code(
            self.old.get_model("app", "HistoricalSeason"),
            id=9002,
            status=Status.COMPLETED.value,
            notes="",
            **history,
        )
        insert_like_old_code(
            self.old.get_model("app", "HistoricalEpisode"),
            id=9003,
            end_date=self.moment,
            **history,
        )
        tv_history = TV.history.get(id=9001)
        self.assertEqual(
            (tv_history.legacy_pending, tv_history.choice, tv_history.ended_choice),
            (True, "", ""),
        )
        self.assertFalse(tv_history.like_is_independent)
        self.assertFalse(Season.history.get(id=9002).like_is_independent)
        self.assertEqual(Episode.history.get(id=9003).kind, "tick")


class TV901LegacyMigrationTests(TransactionTestCase):
    """TV-801/TV-901: shows that existed when 0085 ran are legacy; new ones aren't."""

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_tv_901_existing_rows_become_legacy(self):
        executor = MigrationExecutor(connection)
        executor.migrate([APP_BEFORE, USERS_BEFORE])
        old = executor.loader.project_state([APP_BEFORE, USERS_BEFORE]).apps
        user = old.get_model("users", "User").objects.create(username="before-0085")
        old_item = old.get_model("app", "Item")
        show = old_item.objects.create(
            media_id="77",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Before",
        )
        old_tv = old.get_model("app", "TV").objects.create(
            item_id=show.pk,
            user_id=user.pk,
            status=Status.COMPLETED.value,
        )

        executor = MigrationExecutor(connection)
        executor.migrate([APP_AFTER, USERS_AFTER])
        new = executor.loader.project_state([APP_AFTER, USERS_AFTER]).apps
        new_tv = new.get_model("app", "TV")
        self.assertTrue(new_tv.objects.get(pk=old_tv.pk).legacy_pending)
        self.assertEqual(new_tv.objects.get(pk=old_tv.pk).state_version, 0)
        self.assertEqual(
            new.get_model("users", "User").objects.get(pk=user.pk).calendar_timezone,
            "",
        )

        later = old_item.objects.create(
            media_id="78",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="After",
        )
        created = new_tv.objects.create(item_id=later.pk, user_id=user.pk)
        self.assertFalse(new_tv.objects.get(pk=created.pk).legacy_pending)


class TV705MutationTests(TestCase):
    """TV-705: a mutation ID applies at most once per user."""

    def test_tv_705_a_mutation_id_is_unique_per_user(self):
        first, second = make_user(), make_user()
        show = make_show()
        mutation_id = uuid.uuid4()
        TVMutation.objects.create(
            user=first,
            mutation_id=mutation_id,
            action="tick",
            show_item=show,
            result={"changed": True},
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TVMutation.objects.create(
                user=first,
                mutation_id=mutation_id,
                action="tick",
                show_item=show,
            )
        TVMutation.objects.create(
            user=second,
            mutation_id=mutation_id,
            action="tick",
            show_item=show,
        )
        self.assertEqual(TVMutation.objects.filter(mutation_id=mutation_id).count(), 2)

    def test_a_listing_is_one_row_per_show(self):
        show = make_show()
        TVListing.objects.create(item=show)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TVListing.objects.create(item=show)


class TV207HistoryTests(NoCalendarTestCase):
    """TV-207: worked-out values are caches and keep no history; facts keep it."""

    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.tv = TV.objects.create(
            item=make_show(),
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

    def test_tv_207_cache_columns_have_no_history(self):
        tv_history_fields = {field.name for field in TV.history.model._meta.fields}
        season_history_fields = {field.name for field in Season.history.model._meta.fields}
        for name in TV_STATE_CACHE_FIELD_NAMES:
            self.assertNotIn(name, tv_history_fields)
            self.assertNotIn(name, season_history_fields)
        for name in FACT_FIELDS:
            self.assertIn(name, tv_history_fields)

    def test_tv_207_writing_cache_columns_adds_no_history_row(self):
        before = self.tv.history.count()
        TV.objects.filter(pk=self.tv.pk).update(
            watched_count=3,
            aired_count=10,
            state_version=1793051512345,
            reconciled_at=timezone.now(),
            finish_offers={"1": "2026-10-25T12:00:00+00:00"},
        )
        self.assertEqual(self.tv.history.count(), before)

    def test_tv_207_facts_keep_history(self):
        self.tv.choice = Status.PAUSED.value
        self.tv.save()
        self.assertEqual(self.tv.history.first().choice, Status.PAUSED.value)


class TV205NoCascadeTests(NoCalendarTestCase):
    """TV-205/TV-207: saving or deleting a row no longer cascades into other rows.

    app.tv_tracking works out statuses from the facts instead.
    """

    def setUp(self):
        super().setUp()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.user = make_user()
        self.show = make_show(self.fake, seasons={1: 3, 2: 3})
        self.tv = TV.objects.create(
            item=self.show,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        self.season = Season.objects.create(
            item=season_item(self.show, 1),
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )
        self.fake.reset_calls()
        self.fetch_releases.reset_mock()

    def assert_rows(self, seasons, episodes):
        self.assertEqual(Season.objects.filter(related_tv=self.tv).count(), seasons)
        self.assertEqual(
            Episode.objects.filter(related_season__related_tv=self.tv).count(),
            episodes,
        )

    def test_tv_205_saving_the_finale_finishes_nothing_and_starts_nothing(self):
        for number in (1, 2, 3):
            Episode.objects.create(
                item=episode_item(self.show, 1, number),
                related_season=self.season,
                end_date=timezone.now(),
            )
        self.season.refresh_from_db()
        self.tv.refresh_from_db()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
        self.assert_rows(seasons=1, episodes=3)
        self.assertEqual(self.fake.count(), 0)

    def test_tv_205_a_completed_season_fills_nothing_and_leaves_the_show(self):
        self.season.status = Status.COMPLETED.value
        self.season.save()
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
        self.assert_rows(seasons=1, episodes=0)
        self.assertEqual(self.fake.count(), 0)

    def test_tv_204_a_season_status_never_changes_the_show(self):
        for status in (Status.DROPPED.value, Status.PAUSED.value):
            self.season.status = status
            self.season.save()
            self.tv.refresh_from_db()
            self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)

    def test_tv_207_a_show_status_cascades_nowhere(self):
        self.tv.status = Status.COMPLETED.value
        self.tv.save()
        self.assert_rows(seasons=1, episodes=0)

        self.tv.status = Status.DROPPED.value
        self.tv.save()
        self.season.refresh_from_db()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)

        self.season.delete()
        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()
        self.assert_rows(seasons=0, episodes=0)
        self.assertEqual(self.fake.count(), 0)
        # Calendar refreshes on a status change are kept.
        self.assertEqual(self.fetch_releases.call_count, 3)

    def test_tv_102_deleting_a_viewing_reopens_nothing_by_itself(self):
        episode = Episode.objects.create(
            item=episode_item(self.show, 1, 3),
            related_season=self.season,
        )
        Season.objects.filter(pk=self.season.pk).update(status=Status.COMPLETED.value)
        TV.objects.filter(pk=self.tv.pk).update(status=Status.COMPLETED.value)
        episode.delete()
        self.season.refresh_from_db()
        self.tv.refresh_from_db()
        self.assertEqual(
            (self.season.status, self.tv.status),
            (Status.COMPLETED.value, Status.COMPLETED.value),
        )

    def test_a_season_without_a_show_row_still_gets_one(self):
        other = make_show(self.fake, seasons={1: 2})
        season = Season.objects.create(
            item=season_item(other, 1),
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )
        self.assertEqual(season.related_tv.item, other)
        self.assertEqual(Season.objects.filter(related_tv=season.related_tv).count(), 1)


class TV406RestrictTests(NoCalendarTestCase):
    """TV-406: a log that owns viewings is deleted only through app.tv_tracking."""

    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.show = make_show()
        self.tv = TV.objects.create(
            item=self.show,
            user=self.user,
            status=Status.COMPLETED.value,
        )
        self.season_item = season_item(self.show, 1)
        self.season = Season.objects.create(
            item=self.season_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.COMPLETED.value,
        )
        self.log = DiaryEntry.objects.create(
            user=self.user,
            item=self.season_item,
            consumed_at=datetime(2026, 10, 25, tzinfo=UTC),
            progress_snapshot={"tv": {"v": 1, "covers": {"1": [1, 2]}}},
        )
        self.episode_items = [episode_item(self.show, 1, number) for number in (1, 2)]
        self.fills = [
            Episode.objects.create(
                item=item,
                related_season=self.season,
                kind=Episode.Kind.FILL,
                diary_entry=self.log,
            )
            for item in self.episode_items
        ]

    def test_tv_406_deleting_a_log_that_owns_viewings_is_refused(self):
        with self.assertRaises(RestrictedError), transaction.atomic():
            self.log.delete()
        self.assertEqual(self.log.tv_viewings.count(), 2)

    def test_deleting_the_user_still_cascades(self):
        self.user.delete()
        self.assertFalse(Episode.objects.exists())
        self.assertFalse(DiaryEntry.objects.exists())

    def test_deleting_the_season_or_its_item_still_cascades(self):
        self.season.delete()
        self.assertFalse(Episode.objects.exists())
        self.assertTrue(DiaryEntry.objects.filter(pk=self.log.pk).exists())

        self.season_item.delete()
        self.assertFalse(DiaryEntry.objects.exists())

    def test_deleting_an_episode_item_still_cascades(self):
        self.episode_items[0].delete()
        self.assertEqual(self.log.tv_viewings.count(), 1)
