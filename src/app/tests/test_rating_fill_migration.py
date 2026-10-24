from datetime import date
from decimal import Decimal
from importlib import import_module
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase
from django.utils import timezone

from app import book_tracking, game_tracking, single_weight
from app.models import Book, DiaryEntry, Game, Item, MediaTypes, Movie, Sources

MIGRATION = "0084_fill_empty_ratings_from_rated_logs"
migration = import_module(f"app.migrations.{MIGRATION}")


def run_migration():
    """Run the repair with the models as they stand at this migration, as a deploy does."""
    state = MigrationExecutor(connection).loader.project_state(("app", MIGRATION))
    migration.fill_empty_ratings_from_rated_logs(state.apps, None)


class FillEmptyRatingsMigrationTests(TestCase):
    """Titles whose logs were rated after the fact get their rating once (SW-108)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="rating-fill")

    def item(self, media_id, media_type=MediaTypes.MOVIE.value):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=media_id,
            title=media_id,
            image="https://example.com/poster.jpg",
        )

    def log(self, item, day):
        return single_weight.create_log(self.user, item, consumed_at=day)[0]

    def rate_without_title(self, entry, rating):
        """Reproduce the old edit, which rated the log but not the title."""
        DiaryEntry.objects.filter(pk=entry.pk).update(rating=rating)

    def test_fills_titles_that_never_had_a_rating(self):
        broken = self.item("broken")
        older = self.log(broken, date(2026, 1, 1))
        newer = self.log(broken, date(2026, 2, 1))
        self.log(broken, date(2026, 3, 1))
        self.rate_without_title(older, Decimal(4))
        self.rate_without_title(newer, Decimal(6))

        cleared = self.item("cleared")
        cleared_log = self.log(cleared, date(2026, 1, 1))
        single_weight.set_rating(self.user, cleared, Decimal(8))
        single_weight.set_rating(self.user, cleared, None)
        self.rate_without_title(cleared_log, Decimal(2))

        unrated = self.item("unrated")
        self.log(unrated, date(2026, 1, 1))

        independent = self.item("independent")
        independent_log = self.log(independent, date(2026, 1, 1))
        single_weight.set_rating(self.user, independent, Decimal(10))
        self.rate_without_title(independent_log, Decimal(2))

        run_migration()

        def current(item):
            tracking = Movie.objects.get(user=self.user, item=item)
            return tracking.score, tracking.rating_source_id

        self.assertEqual(current(broken), (Decimal(6), newer.id))
        self.assertEqual(current(cleared), (None, None))
        self.assertEqual(current(unrated), (None, None))
        self.assertEqual(current(independent), (Decimal(10), None))

    def test_fills_books_and_games(self):
        today = timezone.localdate()
        book_item = self.item("book", MediaTypes.BOOK.value)
        _, book_log = book_tracking.complete(
            self.user, book_item, completion_date=today, mutation_id=uuid4(),
        )
        self.rate_without_title(book_log, Decimal(7))

        game_item = self.item("game", MediaTypes.GAME.value)
        game_tracking.start(self.user, game_item)
        _, game_log = game_tracking.complete(self.user, game_item, completion_date=today)
        self.rate_without_title(game_log, Decimal(9))

        run_migration()

        book = Book.objects.get(user=self.user, item=book_item)
        game = Game.objects.get(user=self.user, item=game_item)
        self.assertEqual((book.score, book.rating_source_id), (Decimal(7), book_log.id))
        self.assertEqual((game.score, game.rating_source_id), (Decimal(9), game_log.id))
