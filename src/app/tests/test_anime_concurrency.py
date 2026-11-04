"""Simultaneous writes to one anime title, with real threads and row locks (TV-705).

SQLite has no row locks and one writer at a time, so these run on Postgres: CI's Database
checks, or a throwaway local cluster. test_anime_tracking's SimultaneousWriteTests pin the
same rules on any database by playing the other request's write in line.
"""

import threading
from unittest import mock

from django.db import OperationalError, connection
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext

from app import anime_tracking
from app.models import AnimeViewing, DiaryEntry
from app.tests.anime_fixtures import OCT_25, FakeAnimeProvider, make_item
from app.tests.tv_fixtures import make_user
from social.models import ProgressChange


@skipUnlessDBFeature("has_select_for_update")
class SimultaneousWritesTests(TransactionTestCase):
    def setUp(self):
        self.fake = FakeAnimeProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        patcher = mock.patch("app.models.Item.fetch_releases")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = make_user()
        self.item = make_item(self.fake, total=3)

    def run_together(self, *writes):
        """Run writes in threads, each stopped just before it creates the row."""
        real = anime_tracking._create_row  # noqa: SLF001
        barrier = threading.Barrier(len(writes))

        def gated(*args, **kwargs):
            barrier.wait(timeout=20)
            return real(*args, **kwargs)

        errors = []

        def run(write):
            try:
                write()
            except Exception as error:  # noqa: BLE001 - reported below
                errors.append(error)
            finally:
                connection.close()

        with mock.patch.object(anime_tracking, "_create_row", gated):
            threads = [threading.Thread(target=run, args=(write,)) for write in writes]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
        self.assertEqual(errors, [])

    def tick(self):
        anime_tracking.tick(self.user, self.item, 1, entry_point=anime_tracking.WEB)

    def test_tv_705_two_first_ticks_are_one_viewing(self):
        self.run_together(self.tick, self.tick)
        self.assertEqual(AnimeViewing.objects.filter(anime__user=self.user).count(), 1)
        changes = ProgressChange.objects.filter(actor=self.user).exclude(current_progress__kind="start")
        self.assertEqual(
            [(change.previous_progress["value"], change.current_progress["value"]) for change in changes],
            [(0, 1)],
        )

    def test_tv_705_a_tick_and_a_log_together_keep_one_viewing_of_the_episode(self):
        self.run_together(
            self.tick,
            lambda: anime_tracking.create_log(self.user, self.item, consumed_at=OCT_25),
        )
        self.assertEqual(DiaryEntry.objects.filter(user=self.user).count(), 1)
        episodes = sorted(AnimeViewing.objects.filter(anime__user=self.user).values_list("episode_number", flat=True))
        self.assertEqual(episodes, [1, 2, 3])

    def test_tv_705_a_writes_lock_names_only_its_own_row(self):
        self.tick()
        with CaptureQueriesContext(connection) as queries:
            anime_tracking.tick(self.user, self.item, 2, entry_point=anime_tracking.WEB)
        locks = [query["sql"] for query in queries if "FOR UPDATE" in query["sql"] and "app_anime" in query["sql"]]
        self.assertTrue(locks)
        for sql in locks:
            self.assertIn('FOR UPDATE OF "app_anime"', sql)
            self.assertNotIn("JOIN", sql)

    def test_tv_705_one_persons_write_doesnt_block_another_on_the_same_title(self):
        other = make_user()
        self.tick()
        anime_tracking.tick(other, self.item, 1, entry_point=anime_tracking.WEB)
        holding = threading.Event()
        release = threading.Event()
        real = anime_tracking._do_tick  # noqa: SLF001

        def hold(ctx, number, *, at=None):
            if ctx.user == self.user:
                holding.set()
                release.wait(timeout=20)
            return real(ctx, number, at=at)

        def first():
            try:
                anime_tracking.tick(self.user, self.item, 2, entry_point=anime_tracking.WEB)
            finally:
                connection.close()

        results = []

        def second():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '3s'")
                anime_tracking.tick(other, self.item, 2, entry_point=anime_tracking.WEB)
                results.append("done")
            except OperationalError as error:
                results.append(error)
            finally:
                connection.close()

        with mock.patch.object(anime_tracking, "_do_tick", hold):
            holder = threading.Thread(target=first)
            holder.start()
            self.assertTrue(holding.wait(timeout=20))
            waiter = threading.Thread(target=second)
            waiter.start()
            waiter.join(timeout=30)
            release.set()
            holder.join(timeout=30)
        self.assertEqual(results, ["done"])
