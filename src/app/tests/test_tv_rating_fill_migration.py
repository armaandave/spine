"""TV-804: existing log stars fill empty show and season stars once (migration 0086).

docs/tv-tracking-contract.md, TV-804: a one-time data change, with no network calls,
gives a show or season with no stars the user's newest rated log of that same show or
season as its stars and their source (SW-108). It skips rows whose history shows the
stars were cleared on purpose and changes nothing else. Modelled on
test_rating_fill_migration.py (0084).
"""

from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from app.models import TV, DiaryEntry, Item, MediaTypes, Season, Sources, Status

MIGRATION = "0086_fill_tv_ratings_from_logs"
migration = import_module(f"app.migrations.{MIGRATION}")


def run_migration():
    """Run the fill with the models as they stand at this migration, as a deploy does."""
    state = MigrationExecutor(connection).loader.project_state(("app", MIGRATION))
    migration.fill_tv_ratings_from_logs(state.apps, None)


class FillTVRatingsMigrationTests(TestCase):
    """TV-804 for rows stored before this contract (the deploy's one-time fill)."""

    def setUp(self):
        patcher = mock.patch("app.models.Item.fetch_releases")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = get_user_model().objects.create_user(username="tv-rating-fill")
        self.other = get_user_model().objects.create_user(username="tv-rating-fill-other")

    # Builders: rows and logs as code from before this contract stored them.

    def show(self, media_id):
        return Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id=media_id,
            title=media_id,
            image="https://example.com/poster.jpg",
        )

    def season_item(self, show, number):
        return Item.objects.create(
            source=show.source,
            media_type=MediaTypes.SEASON.value,
            media_id=show.media_id,
            season_number=number,
            title=show.title,
            image=show.image,
        )

    def tv(self, show, user=None, **fields):
        fields.setdefault("status", Status.IN_PROGRESS.value)
        return TV.objects.create(item=show, user=user or self.user, **fields)

    def season(self, tv, item, **fields):
        fields.setdefault("status", Status.IN_PROGRESS.value)
        return Season.objects.create(item=item, user=tv.user, related_tv=tv, **fields)

    def log(self, item, day, rating=None, user=None):
        return DiaryEntry.objects.create(
            user=user or self.user,
            item=item,
            consumed_at=datetime(2026, 10, day, 20, 0, tzinfo=UTC),
            rating=rating,
        )

    @staticmethod
    def current(row):
        row.refresh_from_db()
        return row.score, row.rating_source_id

    # Tests

    def test_tv_804_empty_show_and_season_stars_take_the_newest_rated_log(self):
        show = self.show("filled")
        tv = self.tv(show)
        season_one = self.season_item(show, 1)
        season = self.season(tv, season_one)
        self.log(show, 1, Decimal(6))
        newest_show_log = self.log(show, 3, Decimal(8))
        self.log(show, 4)  # a newer log without stars doesn't count
        # Saved later but dated earlier: "newest" goes by the log's date.
        self.log(show, 2, Decimal(2))
        season_log = self.log(season_one, 5, Decimal(7))

        run_migration()

        self.assertEqual(self.current(tv), (Decimal(8), newest_show_log.id))
        self.assertEqual(self.current(season), (Decimal(7), season_log.id))

    def test_tv_804_stars_cleared_on_purpose_stay_empty(self):
        show = self.show("cleared")
        tv = self.tv(show)
        season_one = self.season_item(show, 1)
        season = self.season(tv, season_one)
        for row in (tv, season):
            row.score = Decimal(5)
            row.save()
            row.score = None
            row.save()
        self.log(show, 1, Decimal(9))
        self.log(season_one, 1, Decimal(9))

        run_migration()

        self.assertEqual(self.current(tv), (None, None))
        self.assertEqual(self.current(season), (None, None))

    def test_tv_804_changes_nothing_else(self):
        """TV-500: nothing rolls up or down; another user's log is theirs; stars kept."""
        show = self.show("left-alone")
        season_one = self.season_item(show, 1)
        rated = self.tv(show, score=Decimal(10))
        self.log(show, 1, Decimal(2))
        season = self.season(rated, season_one)
        self.log(show, 2, Decimal(4))  # a show log never sets a season's stars

        other_show = self.show("only-season-logs")
        other_tv = self.tv(other_show)
        self.log(self.season_item(other_show, 1), 1, Decimal(6))  # nor a season log the show's

        theirs = self.show("their-log")
        mine = self.tv(theirs)
        self.log(theirs, 1, Decimal(3), user=self.other)

        unrated = self.show("unrated")
        unrated_tv = self.tv(unrated)
        self.log(unrated, 1)

        status_before = TV.objects.order_by("pk").values_list("status", flat=True)
        status_before = list(status_before)

        run_migration()

        self.assertEqual(self.current(rated), (Decimal(10), None))
        self.assertEqual(self.current(season), (None, None))
        self.assertEqual(self.current(other_tv), (None, None))
        self.assertEqual(self.current(mine), (None, None))
        self.assertEqual(self.current(unrated_tv), (None, None))
        self.assertEqual(list(TV.objects.order_by("pk").values_list("status", flat=True)), status_before)

    def test_tv_804_the_fill_is_recorded_so_a_later_clear_sticks(self):
        """The repair repeats TV-804 later; a clear after this fill must stay cleared."""
        show = self.show("history")
        tv = self.tv(show)
        season_one = self.season_item(show, 1)
        season = self.season(tv, season_one)
        self.log(show, 1, Decimal(8))
        self.log(season_one, 1, Decimal(6))

        run_migration()

        for row in (tv, season):
            row.refresh_from_db()
            self.assertIsNotNone(row.score)
            filled = type(row).history.filter(id=row.pk).order_by("-history_date", "-history_id").first()
            self.assertEqual(filled.score, row.score)
            self.assertEqual(filled.rating_source_id, row.rating_source_id)
            self.assertEqual(filled.status, row.status)
            row.score = None
            row.rating_source = None
            row.save()

        run_migration()

        self.assertEqual(self.current(tv), (None, None))
        self.assertEqual(self.current(season), (None, None))

    def test_tv_804_makes_no_provider_call(self):
        show = self.show("offline")
        tv = self.tv(show)
        self.log(show, 1, Decimal(4))
        with mock.patch(
            "app.providers.services.get_media_metadata",
            side_effect=AssertionError("TV-804 must not call a provider"),
        ) as provider:
            run_migration()
        provider.assert_not_called()
        self.assertEqual(self.current(tv)[0], Decimal(4))

    def test_tv_804_reverse_is_a_no_op(self):
        (operation,) = migration.Migration.operations
        self.assertIsInstance(operation, migrations.RunPython)
        self.assertIs(operation.reverse_code, migrations.RunPython.noop)
        self.assertIn(("app", "0085_tv_tracking_state"), migration.Migration.dependencies)
