from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from app.signals import create_task_result_on_publish
from lists.featured import dispatch, order, registry
from lists.featured.dispatch import backoff, due, plan
from lists.featured.registry import LiveWindow
from lists.models import FeaturedListSync
from lists.tests.featured.helpers import make_definition

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
REPO = Path(settings.BASE_DIR).parent


def sync_row(**fields):
    values = {
        "key": "test-list",
        "last_attempt_at": NOW - timedelta(hours=2),
        "last_success_at": NOW - timedelta(hours=2),
        "definition_hash": "hash",
        "consecutive_failures": 0,
    }
    values.update(fields)
    return FeaturedListSync(**values)


def award(**fields):
    window = LiveWindow(starts_at=NOW + timedelta(minutes=10), ends_at=NOW + timedelta(hours=3), interval=timedelta(seconds=30))
    return make_definition(key="award-night", kind="award", cadence=timedelta(days=365), live_windows=(window,), **fields)


class DueTests(SimpleTestCase):
    def test_never_attempted_is_due(self):
        self.assertEqual(due(make_definition(), None, NOW).reason, "never attempted")

    def test_fresh_and_cadence(self):
        definition = make_definition(cadence=timedelta(days=1))
        self.assertEqual(due(definition, sync_row(), NOW, definition_hash="hash").reason, "fresh")
        stale = sync_row(last_success_at=NOW - timedelta(days=1, minutes=1))
        self.assertEqual(due(definition, stale, NOW, definition_hash="hash").reason, "cadence elapsed")

    def test_definition_hash_change_is_due_even_for_static_lists(self):
        definition = make_definition(cadence=None)
        self.assertFalse(due(definition, sync_row(), NOW, definition_hash="hash").due)
        self.assertEqual(due(definition, sync_row(), NOW, definition_hash="new").reason, "definition changed")

    def test_failures_back_off_exponentially(self):
        self.assertEqual(backoff(1), timedelta(minutes=5))
        self.assertEqual(backoff(3), timedelta(minutes=20))
        self.assertEqual(backoff(20), timedelta(hours=24))
        self.assertEqual(backoff(20, timedelta(hours=1)), timedelta(hours=1))
        definition = make_definition(cadence=timedelta(days=1))
        failing = sync_row(
            last_success_at=NOW - timedelta(days=2),
            last_attempt_at=NOW - timedelta(minutes=10),
            consecutive_failures=3,
        )
        self.assertEqual(due(definition, failing, NOW).reason, "backing off")
        self.assertTrue(due(definition, failing, NOW + timedelta(minutes=11)).due)

    def test_never_succeeded_retries_after_backoff(self):
        failing = sync_row(last_success_at=None, last_attempt_at=NOW - timedelta(minutes=6), consecutive_failures=1)
        self.assertEqual(due(make_definition(), failing, NOW).reason, "never succeeded")

    def test_lease_blocks(self):
        leased = sync_row(last_success_at=None, lease_until=NOW + timedelta(minutes=1))
        self.assertEqual(due(make_definition(), leased, NOW).reason, "leased")

    def test_live_windows_poll_from_twenty_minutes_before(self):
        definition = award()
        recent = sync_row(last_attempt_at=NOW - timedelta(seconds=10))
        self.assertEqual(due(definition, recent, NOW).reason, "live interval")
        decision = due(definition, sync_row(last_attempt_at=NOW - timedelta(seconds=31)), NOW)
        self.assertEqual((decision.due, decision.live), (True, True))
        early = NOW - timedelta(minutes=15)
        self.assertEqual(due(definition, sync_row(), early, definition_hash="hash").reason, "fresh")
        failing = sync_row(last_attempt_at=NOW - timedelta(seconds=40), consecutive_failures=5)
        self.assertTrue(due(definition, failing, NOW).due)


class PlanTests(SimpleTestCase):
    def test_live_first_then_stalest_capped(self):
        definitions = [make_definition(key=f"list-{number}", position=100 + number) for number in range(8)]
        syncs = {
            f"list-{number}": sync_row(key=f"list-{number}", last_success_at=NOW - timedelta(days=2, hours=number))
            for number in range(8)
        }
        live = award()
        chosen = plan([*definitions, live], syncs, NOW, max_non_live=5)
        self.assertEqual([item.key for item in chosen], ["award-night", "list-7", "list-6", "list-5", "list-4", "list-3"])

    def test_never_synced_lists_come_first(self):
        definitions = [make_definition(key="old", position=101), make_definition(key="new", position=102)]
        syncs = {"old": sync_row(key="old", last_success_at=NOW - timedelta(days=3))}
        self.assertEqual([item.key for item in plan(definitions, syncs, NOW, max_non_live=5)], ["new", "old"])

    def test_equally_stale_lists_follow_the_shelf_order_then_position(self):
        definitions = [
            make_definition(key="movie-extra", position=101),
            make_definition(key="movie-second", position=102),
            make_definition(key="book-first", position=300, media_types=("book",)),
            make_definition(key="movie-first", position=103),
            make_definition(key="book-extra", position=301, media_types=("book",)),
            make_definition(key="synced", position=100),
        ]
        syncs = {"synced": sync_row(key="synced", last_success_at=NOW - timedelta(days=3))}
        shelves = {"movie": ["movie-first", "movie-second"], "book": ["book-first"]}
        with patch.object(order, "SHELF_ORDER", shelves):
            chosen = plan(definitions, syncs, NOW, max_non_live=10)
        # Never-synced first: each shelf's first list, then the second, then the
        # uncurated by position; lists that synced before still go stalest first.
        self.assertEqual(
            [item.key for item in chosen],
            ["movie-first", "book-first", "movie-second", "movie-extra", "book-extra", "synced"],
        )

    def test_one_non_live_sync_at_a_time_by_default(self):
        definitions = [make_definition(key=f"list-{number}", position=100 + number) for number in range(3)]
        self.assertEqual([item.key for item in plan(definitions, {}, NOW)], ["list-0"])
        live = award()
        live_now = NOW + timedelta(minutes=15)
        busy = {"list-0": sync_row(key="list-0", lease_until=live_now + timedelta(minutes=5))}
        self.assertEqual([item.key for item in plan([*definitions, live], busy, live_now)], ["award-night"])

    def test_live_windows_poll_every_interval_between_ticks(self):
        live, now = award(), NOW + timedelta(minutes=15)
        self.assertEqual(dispatch.poll_offsets(live, now), [0, 30])
        self.assertEqual(dispatch.poll_offsets(live, NOW - timedelta(minutes=30)), [0])
        self.assertEqual(dispatch.poll_offsets(make_definition(), now), [0])
        fast = LiveWindow(starts_at=NOW, ends_at=NOW + timedelta(hours=1), interval=timedelta(seconds=20))
        self.assertEqual(dispatch.poll_offsets(make_definition(live_windows=(fast,)), NOW), [0, 20, 40])
        slow = LiveWindow(starts_at=NOW, ends_at=NOW + timedelta(hours=1), interval=timedelta(minutes=2))
        self.assertEqual(dispatch.poll_offsets(make_definition(live_windows=(slow,)), NOW), [0])

    def test_in_flight_non_live_work_uses_up_slots(self):
        definitions = [make_definition(key=f"list-{number}", position=100 + number) for number in range(4)]
        syncs = {
            "list-0": sync_row(key="list-0", lease_until=NOW + timedelta(minutes=5)),
            "list-1": sync_row(key="list-1", lease_until=NOW + timedelta(minutes=5)),
        }
        chosen = plan(definitions, syncs, NOW, max_non_live=3)
        self.assertEqual([item.key for item in chosen], ["list-2"])


class DispatchTaskTests(TestCase):
    def test_dispatch_leases_and_queues_on_the_featured_queue(self):
        definitions = [make_definition(key="one", position=101), make_definition(key="two", position=102)]
        with (
            registry.override_definitions(definitions),
            patch.object(dispatch.sync_featured_list, "apply_async") as apply_async,
        ):
            queued = dispatch.dispatch(now=NOW)
            again = dispatch.dispatch(now=NOW + timedelta(seconds=60))

        self.assertEqual(queued, ["one"])  # one non-live sync at a time
        self.assertEqual(again, [])  # "one" still holds its lease, so "two" waits
        kwargs = apply_async.call_args_list[0].kwargs
        self.assertEqual(kwargs["args"], ["one"])
        self.assertEqual(kwargs["queue"], "featured")
        self.assertEqual(kwargs["soft_time_limit"], 600)
        self.assertEqual(kwargs["expires"], 720)
        self.assertEqual(FeaturedListSync.objects.get(key="one").lease_until, NOW + timedelta(seconds=720))

    def test_live_polls_are_queued_beside_a_running_sync(self):
        busy = make_definition(key="busy", position=101)
        live = award()
        now = NOW + timedelta(minutes=15)
        FeaturedListSync.objects.create(key="busy", last_attempt_at=now, lease_until=now + timedelta(minutes=5))
        with (
            registry.override_definitions([busy, live]),
            patch.object(dispatch.sync_featured_list, "apply_async") as apply_async,
        ):
            queued = dispatch.dispatch(now=now)
        self.assertEqual(queued, ["award-night"])
        calls = [call.kwargs for call in apply_async.call_args_list]
        self.assertEqual([call["countdown"] for call in calls], [None, 30])
        self.assertEqual(
            [call["kwargs"]["dispatched_at"] for call in calls],
            [now.isoformat(), (now + timedelta(seconds=30)).isoformat()],
        )
        self.assertEqual({call["queue"] for call in calls}, {"featured"})

    def test_sync_task_skips_work_that_already_ran(self):
        definition = make_definition()
        FeaturedListSync.objects.create(key=definition.key, last_attempt_at=NOW, lease_until=NOW + timedelta(minutes=5))
        with registry.override_definitions([definition]), patch("lists.featured.engine.run") as run:
            result = dispatch.sync_featured_list(definition.key, dispatched_at=(NOW - timedelta(seconds=30)).isoformat())
        run.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertIsNone(FeaturedListSync.objects.get().lease_until)

    def test_sync_task_runs_the_engine(self):
        definition = make_definition()
        with registry.override_definitions([definition]), patch("lists.featured.engine.run") as run:
            run.return_value.status = "ok"
            run.return_value.message = ""
            result = dispatch.sync_featured_list(definition.key)
        run.assert_called_once_with(definition, trigger="dispatch")
        self.assertEqual(result, {"key": "test-list", "status": "ok", "message": ""})

    def test_unknown_keys_release_their_lease(self):
        FeaturedListSync.objects.create(key="gone", lease_until=NOW)
        self.assertEqual(dispatch.sync_featured_list("gone")["status"], "unknown")
        self.assertIsNone(FeaturedListSync.objects.get().lease_until)


class SchedulingConfigTests(SimpleTestCase):
    def test_beat_routes_and_task_names(self):
        self.assertEqual(
            settings.CELERY_BEAT_SCHEDULE["dispatch_featured_lists"],
            {"task": "Dispatch featured lists", "schedule": 60, "options": {"queue": "featured", "expires": 55}},
        )
        self.assertEqual(settings.CELERY_TASK_ROUTES["Dispatch featured lists"], {"queue": "featured"})
        self.assertEqual(settings.CELERY_TASK_ROUTES["Sync featured list"], {"queue": "featured"})
        self.assertEqual(dispatch.dispatch_featured_lists.name, "Dispatch featured lists")
        self.assertTrue(dispatch.dispatch_featured_lists.ignore_result)
        self.assertEqual(dispatch.sync_featured_list.name, "Sync featured list")
        for legacy in ("sync_nyt_featured_lists", "sync_letterboxd_top_500_featured_list"):
            self.assertIn(legacy, settings.CELERY_BEAT_SCHEDULE)

    def test_dispatcher_publishes_no_task_result(self):
        with patch("app.signals.TaskResult.objects.store_result") as store:
            create_task_result_on_publish(headers={"task": "Dispatch featured lists", "id": "1"})
            store.assert_not_called()
            create_task_result_on_publish(headers={"task": "Sync featured list", "id": "2"})
            store.assert_called_once()

    def test_supervisord_runs_a_featured_worker(self):
        config = (REPO / "supervisord.conf").read_text()
        block = config.split("[program:celery-featured]", 1)[1].split("[program:", 1)[0]
        self.assertIn("-Q featured -n featured@%%h", block)
        self.assertIn("--concurrency 2", block)  # one slot always free for live polls
        self.assertIn("--without-mingle --without-gossip", block)
        self.assertIn("stopasgroup=true", block)
        self.assertIn("stopwaitsecs=60", block)
