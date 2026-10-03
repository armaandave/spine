import logging
from decimal import Decimal
from statistics import median
from time import perf_counter

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from api.services import filters as filter_service
from app.models import ExternalRating, Item, MediaTypes, Movie, Sources, Status

logger = logging.getLogger(__name__)


class ExternalRatingPerformanceTests(TestCase):
    """Exercise realistic stored-rating collection cardinality."""

    ROW_COUNT = 5000

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="rating-performance")
        Item.objects.bulk_create([
            Item(
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                media_id=f"perf-{index}",
                title=f"Movie {index:05d}",
                release_year=2000 + index % 25,
            )
            for index in range(cls.ROW_COUNT)
        ])
        items = list(Item.objects.filter(media_id__startswith="perf-").order_by("pk"))
        Movie.objects.bulk_create([
            Movie(user=cls.user, item=item, status=Status.COMPLETED.value)
            for item in items
        ])
        now = timezone.now()
        ExternalRating.objects.bulk_create([
            ExternalRating(
                item=item,
                rating_source="imdb",
                value=Decimal(index % 101) / 10,
                max_value=10,
                status=ExternalRating.Status.AVAILABLE,
                last_attempted_at=now,
                last_success_at=now,
            )
            for index, item in enumerate(items)
        ])
        if connection.vendor == "postgresql":
            # Bulk-created rows have no planner statistics yet. Production tables are
            # analyzed automatically, so analyze here to time realistic query plans.
            with connection.cursor() as cursor:
                for model in (Item, Movie, ExternalRating):
                    cursor.execute(f"ANALYZE {model._meta.db_table}")

    def _request(self, sort, page_size):
        client = APIClient()
        # A fresh user per request, as real authentication gives; completed_item_ids()
        # memoizes on the user object, so reusing self.user skews later query counts.
        client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        return client.get(
            "/api/v1/tracking/",
            {
                "media_type": MediaTypes.MOVIE.value,
                "sort": sort,
                "page_size": page_size,
            },
        )

    def test_collection_query_count_is_constant_and_latency_is_bounded(self):
        query_counts = {}
        for sort in ("title", "rating:imdb"):
            for page_size in (25, 100):
                with CaptureQueriesContext(connection) as queries:
                    response = self._request(sort, page_size)
                self.assertEqual(response.status_code, 200)
                query_counts[(sort, page_size)] = len(queries)
            self.assertEqual(query_counts[(sort, 25)], query_counts[(sort, 100)])

        timings = {}
        for sort in ("title", "rating:imdb"):
            self._request(sort, 100)
            samples = []
            for _ in range(5):
                started = perf_counter()
                self._request(sort, 100)
                samples.append(perf_counter() - started)
            timings[sort] = median(samples)
        logger.info(
            "external_rating_performance rows=%s queries=%s title_ms=%.2f rating_ms=%.2f delta_ms=%.2f",
            self.ROW_COUNT,
            query_counts,
            timings["title"] * 1000,
            timings["rating:imdb"] * 1000,
            (timings["rating:imdb"] - timings["title"]) * 1000,
        )
        self.assertLessEqual(timings["rating:imdb"] - timings["title"], 0.05)

    def test_sqlite_plan_uses_exact_rating_index(self):
        queryset = Movie.objects.filter(user=self.user)
        ordered = filter_service.order_queryset(
            queryset,
            {"sort": "rating:imdb"},
            rating_scope_queryset=queryset,
        )
        if connection.vendor == "sqlite":
            plan = ordered.explain()
            self.assertIn("sqlite_autoindex_app_externalrating_1", plan)
            self.assertIn("USE TEMP B-TREE FOR ORDER BY", plan)
