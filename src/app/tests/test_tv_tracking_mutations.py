"""Mutation IDs, retries and atomic writes (docs/tv-tracking-contract.md, TV-705, A-46).

A write that carries a mutation ID applies at most once; a repeat returns the stored
changed, finished and earlier_unwatched with fresh state. Explicit actions stay
idempotent without one (TV-101). Every write and its fills succeed or fail together.
"""

import uuid
from unittest import mock

from app import tv_tracking
from app.models import TV, DiaryEntry, Episode, Season, TVMutation
from app.tests.test_tv_tracking_viewings import TVTrackingTestCase
from app.tv_tracking import TVInvalid


class TV705MutationTests(TVTrackingTestCase):
    """TV-705 (server): at most once, with the stored result replayed."""

    def test_a_retried_log_returns_its_finished_and_earlier_unwatched(self):
        show = self.make_show({1: 2, 2: 1})
        mutation_id = uuid.uuid4()
        first = self.log(self.episode_item(show, 2, 1), mutation_id=mutation_id)
        self.assertIsNotNone(first.finished)
        self.assertIsNotNone(first.earlier_unwatched)
        retry = self.log(self.episode_item(show, 2, 1), mutation_id=mutation_id)
        self.assertTrue(retry.replayed)
        self.assertEqual(retry.entry.pk, first.entry.pk)
        self.assertEqual(retry.finished, first.finished)
        self.assertEqual(retry.earlier_unwatched, first.earlier_unwatched)
        self.assertEqual(DiaryEntry.objects.count(), 1)
        self.assertEqual(len(self.viewings(show)), 1)
        self.assertEqual(retry.tv.pk, self.tv(show).pk)

    def test_tv_705_a_mutation_id_reused_for_another_choice_is_rejected(self):
        """Pause then Drop with one ID is a reuse (400), not a replay of the Pause."""
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        mutation_id = uuid.uuid4()
        tv_tracking.choose(self.user, show, "Paused", mutation_id=mutation_id)
        retry = tv_tracking.choose(self.user, show, "Paused", mutation_id=mutation_id)
        self.assertTrue(retry.replayed)
        with self.assertRaises(TVInvalid):
            tv_tracking.choose(self.user, show, "Dropped", mutation_id=mutation_id)
        self.assertEqual(self.status(show), "Paused")

    def test_a_retried_tick_with_a_mutation_id_returns_the_first_response(self):
        show = self.make_show({1: 1})
        mutation_id = uuid.uuid4()
        first = self.tick(show, 1, 1, mutation_id=mutation_id)
        retry = self.tick(show, 1, 1, mutation_id=mutation_id)
        self.assertTrue(first.changed and retry.changed)
        self.assertEqual(retry.finished, first.finished)
        self.assertIsNotNone(retry.finished)
        self.assertEqual(len(self.viewings(show)), 1)
        self.assertEqual(TVMutation.objects.get().action, "tick")

    def test_a_reused_mutation_id_for_another_action_or_show_is_invalid(self):
        show = self.make_show({1: 3})
        other = self.make_show({1: 3})
        mutation_id = uuid.uuid4()
        self.tick(show, 1, 1, mutation_id=mutation_id)
        with self.assertRaises(TVInvalid) as raised:
            self.untick(show, 1, 1, mutation_id=mutation_id)
        self.assertEqual(raised.exception.message, "This request was already used for a different change.")
        with self.assertRaises(TVInvalid):
            self.tick(other, 1, 1, mutation_id=mutation_id)
        self.assertEqual(len(self.viewings(show)), 1)
        self.assertIsNone(self.tv(other))

    def test_a_mutation_id_is_per_user(self):
        from app.tests.tv_fixtures import make_user

        show = self.make_show({1: 3})
        other_user = make_user()
        mutation_id = uuid.uuid4()
        self.tick(show, 1, 1, mutation_id=mutation_id)
        self.advance()
        result = tv_tracking.tick(other_user, show, 1, 1, mutation_id=mutation_id)
        self.assertFalse(result.replayed)
        self.assertTrue(TV.objects.filter(user=other_user).exists())

    def test_the_earlier_episodes_yes_applies_once(self):
        show = self.make_show({1: 2, 2: 2})
        question = self.tick(show, 2, 1).earlier_unwatched
        mutation_id = uuid.uuid4()
        for _ in range(2):
            tv_tracking.mark_earlier(
                self.user,
                show,
                from_ref=question["from"],
                to_ref=question["to"],
                mutation_id=mutation_id,
            )
        self.assertEqual(len(self.viewings(show, 1)), 2)

    def test_tv_705_state_version_never_repeats_across_remove_and_re_adding(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        before = self.tv(show).state_version
        tv_tracking.remove(self.user, show, mutation_id=uuid.uuid4())
        tv_tracking.choose(self.user, show, "Planning")  # the same instant as the removal
        after = self.tv(show).state_version
        self.assertGreater(after, before)

    def test_a_replayed_remove_changes_nothing(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        mutation_id = uuid.uuid4()
        tv_tracking.remove(self.user, show, mutation_id=mutation_id)
        self.tick(show, 1, 1)
        retry = tv_tracking.remove(self.user, show, mutation_id=mutation_id)
        self.assertTrue(retry.replayed)
        self.assertIsNotNone(self.tv(show))


class A46AtomicTests(TVTrackingTestCase):
    """A-46: retries, double taps and failures partway leave no duplicates or halves."""

    def test_a_46_a_failure_partway_through_a_fill_changes_nothing(self):
        show = self.make_show({1: 5, 2: 5})
        self.tick(show, 1, 1)
        before = (len(self.viewings(show)), self.tv(show).state_version, Season.objects.count())
        with mock.patch.object(Episode.objects, "bulk_create", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                tv_tracking.mark_watched(self.user, show)
        self.assertEqual(
            (len(self.viewings(show)), self.tv(show).state_version, Season.objects.count()),
            before,
        )

    def test_a_46_a_failure_after_the_log_is_written_rolls_the_log_back(self):
        show = self.make_show({1: 3})
        with mock.patch("app.tv_tracking._create_diary_activity", side_effect=RuntimeError("feed down")):
            with self.assertRaises(RuntimeError):
                self.log(self.season_item(show, 1))
        self.assertFalse(DiaryEntry.objects.exists())
        self.assertIsNone(self.tv(show))
        self.assertFalse(Episode.objects.exists())

    def test_a_46_double_taps_on_marks_and_unmarks_are_explicit_actions(self):
        show = self.make_show({1: 3})
        tv_tracking.mark_watched(self.user, show)
        self.assertFalse(tv_tracking.mark_watched(self.user, show).changed)
        tv_tracking.unmark(self.user, show)
        self.assertFalse(tv_tracking.unmark(self.user, show).changed)
        self.assertIsNone(self.tv(show))


class ErrorPicklingTests(TVTrackingTestCase):
    """The TV errors survive pickling (the parallel test runner and Celery pickle them)."""

    def test_tv_errors_round_trip_through_pickle(self):
        import pickle

        from app.tv_tracking import TVConflict, TVLogBlocks, TVStateChanged

        for error in (
            TVInvalid("This episode hasn't aired yet."),
            TVConflict("Nothing has aired yet."),
            TVLogBlocks("Delete your show log first."),
            TVStateChanged(state_version=7, count=3),
        ):
            copy = pickle.loads(pickle.dumps(error))
            self.assertIs(type(copy), type(error))
            self.assertEqual(copy.message, error.message)
            self.assertEqual(getattr(copy, "code", None), getattr(error, "code", None))
            self.assertEqual(getattr(copy, "extra", None), getattr(error, "extra", None))
