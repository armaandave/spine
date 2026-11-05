"""One person's TV write never holds up another's on the same show (TV-705).

SQLite has no row locks and one writer at a time, so these run on Postgres: CI's Database
checks, or a throwaway local cluster.
"""

import threading
from unittest import mock

from django.db import OperationalError, connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext

from app import tv_tracking
from app.models import TV
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user


@skipUnlessDBFeature("has_select_for_update")
class TVWriteLockTests(TransactionTestCase):
    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        patcher = mock.patch("app.models.Item.fetch_releases")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = make_user()
        self.other = make_user()
        self.show = make_show(self.fake, seasons={1: 3})

    def tick(self, user, number):
        tv_tracking.tick(user, self.show, 1, number, entry_point=tv_tracking.WEB)

    def test_tv_705_a_writes_locks_name_only_the_tv_row(self):
        self.tick(self.user, 1)
        tv_id = TV.objects.get(user=self.user, item=self.show).pk
        with CaptureQueriesContext(connection) as queries:
            self.tick(self.user, 2)
            tv_tracking._lock_and_reconcile(tv_id, entry_point=tv_tracking.JOB)  # noqa: SLF001
        locks = [query["sql"] for query in queries if "FOR UPDATE" in query["sql"] and '"app_tv"' in query["sql"]]
        self.assertGreaterEqual(len(locks), 2)
        for sql in locks:
            self.assertIn('FOR UPDATE OF "app_tv"', sql)

    def test_tv_705_one_persons_write_doesnt_hold_up_anothers_on_the_same_show(self):
        self.tick(self.user, 1)
        self.tick(self.other, 1)
        holding = threading.Event()
        release = threading.Event()
        real = tv_tracking._do_tick  # noqa: SLF001

        def hold(ctx, season_number, episode_number, *, at=None):
            if ctx.user == self.user:
                holding.set()
                release.wait(timeout=20)
            return real(ctx, season_number, episode_number, at=at)

        def first():
            try:
                self.tick(self.user, 2)
            finally:
                connection.close()

        results = []

        def second():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '3s'")
                self.tick(self.other, 2)
                results.append("done")
            except OperationalError as error:
                results.append(error)
            finally:
                connection.close()

        with mock.patch.object(tv_tracking, "_do_tick", hold):
            holder = threading.Thread(target=first)
            holder.start()
            self.assertTrue(holding.wait(timeout=20))
            waiter = threading.Thread(target=second)
            waiter.start()
            waiter.join(timeout=30)
            release.set()
            holder.join(timeout=30)
        self.assertEqual(results, ["done"])
