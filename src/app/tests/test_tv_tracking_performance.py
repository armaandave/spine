"""Bounded work for big shows (docs/tv-tracking-contract.md, TV-301, TV-302, A-47).

Marking a 30-season, 700-episode show watched and undoing it are each one request with
a bounded number of queries and no provider call per episode. The same writes on a
5-season show run the same number of queries, so the bound doesn't grow with the size.
"""

import math
from datetime import date, timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext

from app import tv_listing, tv_tracking
from app.models import Episode, Item
from app.tests.test_tv_tracking_viewings import TVTrackingTestCase

SEASONS = 30
EPISODES = 700
# Upper bounds measured on SQLite with the batch sizes Django uses there.
MARK_QUERY_BOUND = 60
UNDO_QUERY_BOUND = 40


def big_spec(seasons=SEASONS, episodes=EPISODES):
    """`episodes` weekly episodes from 2000 over `seasons` seasons, all aired."""
    base, extra = divmod(episodes, seasons)
    spec = {}
    day = date(2000, 1, 3)
    for number in range(1, seasons + 1):
        count = base + (1 if number <= extra else 0)
        spec[number] = [day + timedelta(weeks=index) for index in range(count)]
        day += timedelta(weeks=count)
    return spec


class A47BigShowTests(TVTrackingTestCase):
    """A-47: mark a 30-season, 700-episode show watched, then undo it."""

    def queries(self, function):
        with CaptureQueriesContext(connection) as context:
            result = function()
        return result, len(context.captured_queries)

    def test_a_47_cold_listing_fetches_at_most_eight_seasons_per_request(self):
        show = self.make_show(big_spec())
        tv_tracking.mark_watched(self.user, show)
        self.assertEqual(self.fake.count("tv"), 1)
        self.assertLessEqual(self.fake.count("tv_with_seasons"), math.ceil(SEASONS / 8))
        self.assertTrue(all(len(request) <= 8 for request in self.fake.season_requests()))
        self.assertEqual(len(self.viewings(show)), EPISODES)

    def test_a_47_mark_and_undo_are_bounded_and_make_no_provider_call(self):
        show = self.make_show(big_spec())
        tv_listing.get_listing(show)
        self.fake.reset_calls()

        result, mark_queries = self.queries(lambda: tv_tracking.mark_watched(self.user, show))
        self.assertTrue(result.changed)
        self.assertEqual(self.fake.count(), 0)
        self.assertEqual(Episode.objects.count(), EPISODES)
        self.assertEqual(Item.objects.filter(media_type="episode").count(), EPISODES)
        self.assertLessEqual(mark_queries, MARK_QUERY_BOUND)

        result, undo_queries = self.queries(lambda: tv_tracking.unmark(self.user, show))
        self.assertTrue(result.changed)
        self.assertEqual(self.fake.count(), 0)
        self.assertFalse(Episode.objects.exists())
        self.assertIsNone(self.tv(show))
        self.assertLessEqual(undo_queries, UNDO_QUERY_BOUND)

    def test_a_47_the_bound_doesnt_grow_with_the_show(self):
        counts = []
        for seasons, episodes in ((5, 50), (SEASONS, EPISODES)):
            show = self.make_show(big_spec(seasons, episodes))
            tv_listing.get_listing(show)
            # Items exist already, as after a first open of the show.
            tv_tracking.prepare_items(self.user, show, tv_listing.get_listing(show))
            _, mark = self.queries(lambda show=show: tv_tracking.mark_watched(self.user, show))
            _, undo = self.queries(lambda show=show: tv_tracking.unmark(self.user, show))
            counts.append((mark, undo))
        small, big = counts
        # Bulk inserts batch by the database's parameter limit; allow those batches only.
        self.assertLessEqual(big[0] - small[0], math.ceil(EPISODES / 100))
        self.assertLessEqual(big[1] - small[1], 2)
