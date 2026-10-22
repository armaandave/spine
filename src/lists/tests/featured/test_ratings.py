from unittest.mock import patch

from django.test import override_settings

from app.models import Item, MediaTypes, Sources
from app.providers import quota, services
from lists.featured import dispatch, ratings, registry
from lists.tests.featured.helpers import FeaturedTestCase

HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"


def item(source, media_type, media_id):
    return Item.objects.create(source=source, media_type=media_type, media_id=media_id, title=media_id, image="https://x/i.jpg")


class RatingsTrickleTests(FeaturedTestCase):
    def test_one_batch_at_a_time(self):
        ratings.add_pending(range(1, 121))
        with patch.object(ratings.enrich_featured_ratings, "delay") as delay:
            first = ratings.drain()
            self.assertEqual(len(first), ratings.BATCH_SIZE)
            self.assertEqual(ratings.drain(), [])  # the first batch is still in flight
            services.redis_db.delete(ratings.IN_FLIGHT_KEY)  # what the finished batch does
            second = ratings.drain()
        self.assertEqual(delay.call_count, 2)
        self.assertFalse(set(first) & set(second))
        self.assertEqual(ratings.pending_count(), 120 - 2 * ratings.BATCH_SIZE)

    def test_nothing_pending_queues_nothing_and_frees_the_slot(self):
        with patch.object(ratings.enrich_featured_ratings, "delay") as delay:
            self.assertEqual(ratings.drain(), [])
        delay.assert_not_called()
        self.assertFalse(services.redis_db.exists(ratings.IN_FLIGHT_KEY))

    @override_settings(FEATURED_HARDCOVER_DAILY_BUDGET=0)
    def test_books_wait_while_the_featured_budget_is_spent(self):
        movie = item(Sources.TMDB.value, MediaTypes.MOVIE.value, "550")
        book = item(Sources.HARDCOVER.value, MediaTypes.BOOK.value, "123")
        ratings.add_pending([movie.pk, book.pk])
        with patch.object(ratings.enrich_featured_ratings, "delay") as delay:
            self.assertEqual(ratings.drain(), [movie.pk])
        delay.assert_called_once_with([movie.pk])
        self.assertEqual([int(value) for value in services.redis_db.smembers(ratings.PENDING_KEY)], [book.pk])

    def test_a_blocked_provider_holds_its_books(self):
        book = item(Sources.HARDCOVER.value, MediaTypes.BOOK.value, "123")
        ratings.add_pending([book.pk])
        services.redis_db.set(quota._key("blocked", "hardcover"), 9_999_999_999)
        with patch.object(ratings.enrich_featured_ratings, "delay") as delay:
            self.assertEqual(ratings.drain(), [])
        delay.assert_not_called()
        self.assertEqual(ratings.pending_count(), 1)

    def test_batches_spend_the_featured_budget_and_free_the_slot(self):
        book = item(Sources.HARDCOVER.value, MediaTypes.BOOK.value, "123")
        ratings.add_pending([book.pk])

        def batch(item_ids):
            quota.before_request("hardcover", HARDCOVER_URL)  # a metadata request
            return {"requested": len(item_ids)}

        with patch.object(ratings, "enrich_external_ratings_batch", side_effect=batch) as enrich:
            self.assertEqual(ratings.drain(), [book.pk])  # eager in tests: the task runs now
        enrich.assert_called_once_with([book.pk])
        self.assertEqual(quota.used_today("featured", "hardcover"), 1)
        self.assertFalse(services.redis_db.exists(ratings.IN_FLIGHT_KEY))
        quota.before_request("hardcover", HARDCOVER_URL)  # the app's own request: not counted
        self.assertEqual(quota.used_today("featured", "hardcover"), 1)

    def test_the_dispatcher_drains(self):
        ratings.add_pending([1])
        with (
            registry.override_definitions([]),
            patch.object(ratings.enrich_featured_ratings, "delay") as delay,
        ):
            dispatch.dispatch()
        delay.assert_called_once_with([1])
