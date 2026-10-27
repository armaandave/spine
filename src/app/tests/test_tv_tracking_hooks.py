"""Model hooks: the safety net for TV writes made outside app.tv_tracking (plan 1.2).

Receivers in app.tv_signals queue one reconcile per show on commit, using the stored
episode lists only; they do nothing during app.tv_tracking's own writes, for raw saves
and for shows stored before the contract (TV-801). Contributes to TV-207.
"""

from datetime import timedelta
from unittest import mock

from django.db import connection
from django.db.models.signals import post_save
from django.test.utils import CaptureQueriesContext

from app import tv_listing, tv_tracking
from app.models import TV, Episode, Season, Status
from app.tests.test_tv_tracking_viewings import OCT_25, TVTrackingTestCase

# Captured before TVTrackingTestCase patches it for every test.
REAL_QUEUE_EXTERNAL_RATINGS = tv_tracking._queue_external_ratings


class HookTestCase(TVTrackingTestCase):
    def tracked_show(self, seasons=None):
        show = self.make_show(seasons or {1: 3, 2: 3})
        tv_listing.get_listing(show)
        self.tick(show, 1, 1)
        return show

    def add_viewing(self, show, season_number, episode_number, **fields):
        tv = self.tv(show)
        season = Season.objects.get(related_tv=tv, item__season_number=season_number)
        return Episode.objects.create(
            item=self.episode_item(show, season_number, episode_number),
            related_season=season,
            **fields,
        )


class EpisodeHookTests(HookTestCase):
    """Episode post_save and post_delete (plan 1.2, Hooks)."""

    def test_a_viewing_saved_directly_is_a_fact_and_the_show_is_worked_out(self):
        show = self.tracked_show({1: 2})
        self.advance()
        with self.captureOnCommitCallbacks(execute=True):
            self.add_viewing(show, 1, 2, end_date=self.clock.moment)
        tv = self.tv(show)
        self.assertEqual(tv.status, "Completed")
        self.assertEqual(tv.watched_count, 2)
        viewing = self.viewings(show, 1, 2)[0]
        self.assertEqual((viewing.kind, viewing.watched_on), (Episode.Kind.TICK, OCT_25))

    def test_tv_101_a_duplicate_is_kept_and_counted_once(self):
        show = self.tracked_show({1: 2})
        with self.captureOnCommitCallbacks(execute=True):
            self.add_viewing(show, 1, 1, end_date=self.clock.moment)
        self.assertEqual(len(self.viewings(show, 1, 1)), 2)
        self.assertEqual(self.tv(show).watched_count, 1)
        state = tv_tracking.episode_states(self.user, show, 1, None)[1]
        self.assertEqual(state["viewing_count"], 2)

    def test_tv_106_an_invalid_row_is_kept_logged_and_counts_toward_nothing(self):
        show = self.tracked_show({1: 2})
        with self.assertLogs("app.tv_tracking", level="WARNING") as logs, self.captureOnCommitCallbacks(execute=True):
            self.add_viewing(show, 1, 9, end_date=self.clock.moment)
        self.assertIn("unlisted", logs.output[0])
        self.assertEqual(len(self.viewings(show, 1, 9)), 1)
        self.assertEqual((self.tv(show).watched_count, self.tv(show).aired_count), (1, 2))

    def test_a_direct_delete_reconciles_the_show_once(self):
        show = self.tracked_show({1: 2})
        self.tick(show, 1, 2)
        self.assertEqual(self.status(show), "Completed")
        viewing = self.viewings(show, 1, 2)[0]
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            viewing.delete()
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(self.status(show), "In progress")

    def test_deleting_a_30_episode_season_does_no_work_per_episode(self):
        show = self.make_show({1: 30, 2: 2})
        tv_listing.get_listing(show)
        tv_tracking.mark_watched(self.user, show, season_number=1)
        self.tick(show, 2, 1)
        season = Season.objects.get(item__season_number=1)
        with CaptureQueriesContext(connection) as queries, self.captureOnCommitCallbacks(execute=True) as callbacks:
            season.delete()
        self.assertEqual(callbacks, [])
        # The cascade's own queries (simple_history clears each row's history), but the
        # hooks read nothing: no query touches the show or its other seasons.
        self.assertFalse([query for query in queries.captured_queries if '"app_tv"' in query["sql"]])
        # The cascade's owner works the show out (delete_season_tracking does); the hook
        # leaves the stored values alone.
        self.assertEqual(self.tv(show).watched_count, 31)

    def test_a_queryset_delete_of_episodes_reconciles_the_show_once(self):
        """TV-207, TV-003, TV-205: the show is worked out once, when the delete commits.

        Rewritten: it used to expect a Watching row with nothing watched. With every
        viewing gone and no log, mark or choice left, nothing keeps the show in the
        library (TV-003), so it leaves it with its seasons, as unticking each would
        (A-03 through hooks).
        """
        show = self.tracked_show({1: 3})
        self.ticks(show, 1, (2, 3))
        tv = self.tv(show)
        with CaptureQueriesContext(connection) as queries, self.captureOnCommitCallbacks(execute=False) as callbacks:
            Episode.objects.filter(related_season__related_tv=tv).delete()
        hook_queries = [query for query in queries.captured_queries if '"app_tv"' in query["sql"]]
        self.assertEqual(hook_queries, [])
        with mock.patch("app.tv_tracking._hook_reconcile", wraps=tv_tracking._hook_reconcile) as reconcile:
            for callback in callbacks:
                callback()
        self.assertEqual(reconcile.call_count, 1)
        self.assertIsNone(self.tv(show))
        self.assertFalse(Season.objects.filter(user=self.user, item__media_id=show.media_id).exists())

    def test_tv_205_a_direct_delete_of_a_seasons_last_viewing_removes_the_season(self):
        """TV-205, TV-207: the hook path drops a season left with nothing, as an untick does."""
        show = self.tracked_show({1: 2, 2: 2})
        self.ticks(show, 1, (2,))
        self.tick(show, 2, 1)
        viewing = self.viewings(show, 2, 1)[0]
        with self.captureOnCommitCallbacks(execute=True):
            viewing.delete()
        self.assertFalse(Season.objects.filter(user=self.user, item__season_number=2).exists())
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.watched_count), ("In progress", 2))

    def test_tv_208_a_direct_delete_restores_the_choice_it_ended(self):
        """TV-208: deleting the viewing that ended Paused restores Paused through hooks too."""
        show = self.tracked_show({1: 3})
        tv_tracking.choose(self.user, show, "Paused")
        self.advance()
        self.tick(show, 1, 2)
        self.assertEqual(self.status(show), "In progress")
        viewing = self.viewings(show, 1, 2)[0]
        with self.captureOnCommitCallbacks(execute=True):
            viewing.delete()
        self.assertEqual(self.status(show), "Paused")
        self.assertEqual(self.tv(show).watched_count, 1)

    def test_work_queued_after_an_earlier_uncommitted_callback_still_runs(self):
        show = self.tracked_show({1: 3})
        self.add_viewing(show, 1, 2, end_date=self.clock.moment)  # queued, not run
        with self.captureOnCommitCallbacks(execute=True):
            self.add_viewing(show, 1, 3, end_date=self.clock.moment)
        self.assertEqual(self.tv(show).watched_count, 3)

    def test_raw_saves_and_tv_tracking_writes_queue_nothing(self):
        show = self.tracked_show({1: 3})
        viewing = self.viewings(show)[0]
        with self.captureOnCommitCallbacks() as callbacks:
            post_save.send(sender=Episode, instance=viewing, created=False, raw=True)
            self.tick(show, 1, 2)
        self.assertFalse(any(isinstance(callback, tv_tracking._PendingCallback) for callback in callbacks))

    def test_a_missing_stored_season_leaves_reconciled_at_empty_and_raises_nothing(self):
        show = self.tracked_show({1: 2, 2: 2})
        from app.models import TVListing

        listing = TVListing.objects.get(item=show)
        listing.seasons.pop("2")
        listing.save()
        with self.captureOnCommitCallbacks(execute=True):
            self.add_viewing(show, 1, 2, end_date=self.clock.moment)
        self.assertIsNone(self.tv(show).reconciled_at)


class TVAndSeasonHookTests(HookTestCase):
    """TV and Season saves: a new row records no choice; a written status is its action."""

    def test_season_save_creating_a_tv_row_records_no_choice(self):
        show = self.make_show({1: 2})
        tv_listing.get_listing(show)
        with self.captureOnCommitCallbacks(execute=True):
            season = Season.objects.create(
                item=self.season_item(show, 1),
                user=self.user,
                status=Status.COMPLETED.value,
            )
        tv = TV.objects.get(pk=season.related_tv_id)
        self.assertEqual(tv.choice, "")
        self.assertFalse(tv.legacy_pending)
        # No viewing, log or mark: the season holds nothing and isn't started.
        season.refresh_from_db()
        self.assertFalse(season.is_started)

    def test_a_new_tv_row_for_a_show_with_old_logs_becomes_legacy(self):
        from app.models import DiaryEntry

        show = self.make_show({1: 2})
        tv_listing.get_listing(show)
        DiaryEntry.objects.create(user=self.user, item=self.season_item(show, 1), consumed_at=self.clock.moment)
        with self.captureOnCommitCallbacks(execute=True):
            tv = TV.objects.create(item=show, user=self.user, status=Status.IN_PROGRESS.value)
        tv.refresh_from_db()
        self.assertTrue(tv.legacy_pending)
        self.assertIsNone(tv.reconciled_at)

    def test_tv_207_a_show_status_saved_as_completed_marks_it_watched(self):
        show = self.tracked_show({1: 3})
        tv = self.tv(show)
        tv.status = Status.COMPLETED.value
        with self.captureOnCommitCallbacks(execute=True):
            tv.save()
        self.assertEqual(self.status(show), "Completed")
        self.assertEqual(len(self.viewings(show)), 3)

    def test_tv_207_a_show_status_saved_as_dropped_is_the_choice(self):
        show = self.tracked_show({1: 3})
        tv = self.tv(show)
        tv.status = Status.DROPPED.value
        with self.captureOnCommitCallbacks(execute=True):
            tv.save()
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.choice), ("Dropped", "Dropped"))

    def test_tv_204_a_season_status_saved_as_paused_is_ignored(self):
        show = self.tracked_show({1: 3})
        season = Season.objects.get(item__season_number=1)
        season.status = Status.PAUSED.value
        with self.captureOnCommitCallbacks(execute=True):
            season.save()
        season.refresh_from_db()
        self.assertEqual(season.status, "In progress")
        self.assertEqual(self.status(show), "In progress")

    def test_tv_207_a_season_status_saved_as_completed_marks_that_season(self):
        show = self.tracked_show({1: 3, 2: 2})
        season = Season.objects.get(item__season_number=1)
        season.status = Status.COMPLETED.value
        with self.captureOnCommitCallbacks(execute=True):
            season.save()
        self.assertEqual(self.watched(show), {(1, 1), (1, 2), (1, 3)})

    def test_tv_801_hooks_leave_a_legacy_show_alone(self):
        show = self.tracked_show({1: 3})
        TV.objects.filter(pk=self.tv(show).pk).update(legacy_pending=True, reconciled_at=None)
        version = self.tv(show).state_version
        with self.captureOnCommitCallbacks(execute=True):
            self.add_viewing(show, 1, 2, end_date=self.clock.moment - timedelta(days=1))
        tv = self.tv(show)
        self.assertIsNone(tv.reconciled_at)
        self.assertEqual(tv.state_version, version)
        self.assertEqual(tv.watched_count, 1)


class OnCommitWorkTests(TVTrackingTestCase):
    """Celery work tv_tracking queues on commit (AGENTS.md 4.5)."""

    def test_the_calendar_refreshes_for_a_new_show_and_a_resumed_one(self):
        from app.models import Item

        show = self.make_show({1: 3})
        with mock.patch.object(Item, "fetch_releases") as fetch, self.captureOnCommitCallbacks(execute=True):
            self.tick(show, 1, 1)
        fetch.assert_called_once_with(delay=True)
        with mock.patch.object(Item, "fetch_releases") as fetch, self.captureOnCommitCallbacks(execute=True):
            self.tick(show, 1, 2)
            tv_tracking.choose(self.user, show, "Paused")
        fetch.assert_not_called()
        with mock.patch.object(Item, "fetch_releases") as fetch, self.captureOnCommitCallbacks(execute=True):
            tv_tracking.choose(self.user, show, "In progress")
        fetch.assert_called_once_with(delay=True)

    def test_new_episode_items_queue_external_ratings_once(self):
        show = self.make_show({1: 3})
        with (
            mock.patch("app.tv_tracking._queue_external_ratings", REAL_QUEUE_EXTERNAL_RATINGS),
            mock.patch("app.tasks.enqueue_external_rating_batches") as enqueue,
            self.captureOnCommitCallbacks(execute=True),
        ):
            tv_tracking.mark_watched(self.user, show)
            tv_tracking.unmark(self.user, show)
            tv_tracking.mark_watched(self.user, show)
        enqueue.assert_called_once()
        self.assertEqual(len(enqueue.call_args.args[0]), 4)  # the season and its 3 episodes

    def test_deleting_a_log_queues_statistics_for_its_day(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        entry = self.log(self.season_item(show, 1)).entry
        with (
            mock.patch("app.single_weight.update_daily_statistics.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            tv_tracking.delete_log(self.user, entry)
        delay.assert_called_once_with(user_id=self.user.pk, date_str="2026-10-25")
