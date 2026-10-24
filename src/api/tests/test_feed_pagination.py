"""The activity feed pages in a total order, even through activity sharing a timestamp.

Activity written in the same instant (a bulk import, or a frozen clock in tests) ties on
created_at. Postgres returns tied rows in any order, so without a tiebreaker a cursor
page can repeat or skip them.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from api.pagination import FeedCursorPagination
from social.models import Activity


class FeedCursorPaginationTests(TestCase):
    """Tied activity pages newest id first, with nothing repeated or skipped."""

    def test_the_feed_query_breaks_timestamp_ties_by_id(self):
        # Which order Postgres returns tied rows in depends on the plan and the row
        # layout, so a test of tied rows alone can pass by luck: check the order itself.
        user = get_user_model().objects.create_user(username="feeder", password="strong-password-123")
        Activity.objects.create(actor=user, verb="followed", target_type="user", target_id=1)
        request = Request(APIRequestFactory().get("/feed/"))

        with CaptureQueriesContext(connection) as queries:
            list(FeedCursorPagination().paginate_queryset(Activity.objects.filter(actor=user), request))

        order_by = queries.captured_queries[-1]["sql"].rsplit("ORDER BY", 1)[1]
        table = connection.ops.quote_name(Activity._meta.db_table)
        self.assertIn(f"{table}.{connection.ops.quote_name('created_at')} DESC", order_by)
        self.assertIn(f"{table}.{connection.ops.quote_name('id')} DESC", order_by)

    def test_tied_timestamps_page_newest_id_first_without_gaps(self):
        user = get_user_model().objects.create_user(username="feeder", password="strong-password-123")
        ids = [
            Activity.objects.create(actor=user, verb="followed", target_type="user", target_id=n).id
            for n in range(5)
        ]
        Activity.objects.filter(id__in=ids).update(created_at=timezone.now())

        seen = []
        url = "/feed/?page_size=2"
        while url:
            paginator = FeedCursorPagination()
            request = Request(APIRequestFactory().get(url))
            seen += [activity.id for activity in paginator.paginate_queryset(Activity.objects.filter(actor=user), request)]
            url = paginator.get_next_link()

        self.assertEqual(seen, sorted(ids, reverse=True))
