"""Data from before this contract (TV-801): conversion, the legacy rule and gating.

backend-repair owns the reconcile_tv command; these tests pin the primitives it and the
first app or web write share (convert_legacy_logs, apply_legacy_rule, plan_legacy,
work_out) and the gating of each entry point. A-42, A-59 and A-67 at the domain level.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.test import override_settings

from app import tv_listing, tv_tracking
from app.models import TV, DiaryEntry, Episode, Season, Status
from app.tests.test_tv_tracking_viewings import OCT_25, TVTrackingTestCase
from app.tests.tv_fixtures import make_user

NEW_YORK = ZoneInfo("America/New_York")


def snapshot(user, show):
    """The facts and worked-out state that must match whatever order things ran in."""
    tv = TV.objects.filter(user=user, item=show).first()
    viewings = sorted(
        (
            row.item.season_number,
            row.item.episode_number,
            row.kind,
            "log" if row.diary_entry_id else "",
            row.watched_on,
        )
        for row in Episode.objects.filter(related_season__related_tv=tv).select_related("item")
    )
    logs = sorted(
        (entry.item.season_number, entry.item.episode_number, entry.consumed_at, str(entry.progress_snapshot))
        for entry in DiaryEntry.objects.filter(user=user).select_related("item")
    )
    seasons = sorted(Season.objects.filter(related_tv=tv).values_list("item__season_number", "status"))
    return {
        "status": tv.status if tv else None,
        "choice": tv.choice if tv else None,
        "legacy": tv.legacy_pending if tv else None,
        "counts": (tv.watched_count, tv.aired_count) if tv else None,
        "up_next": (tv.up_next_season, tv.up_next_episode) if tv else None,
        "viewings": viewings,
        "logs": logs,
        "seasons": seasons,
    }


class LegacyTestCase(TVTrackingTestCase):
    def old_tv(self, show, user=None, **fields):
        """A TV row as stored before the contract (legacy_pending from its db_default)."""
        fields.setdefault("status", Status.IN_PROGRESS.value)
        tv = TV.objects.create(item=show, user=user or self.user, **fields)
        TV.objects.filter(pk=tv.pk).update(legacy_pending=True)
        tv.refresh_from_db()
        return tv

    def old_season(self, tv, number, status=Status.IN_PROGRESS.value):
        return Season.objects.create(
            item=self.season_item(tv.item, number),
            user=tv.user,
            related_tv=tv,
            status=status,
        )

    def old_viewing(self, season, episode_number, end_date):
        return Episode.objects.create(
            item=self.episode_item(season.related_tv.item, season.item.season_number, episode_number),
            related_season=season,
            end_date=end_date,
        )

    def old_log(self, item, consumed_at, user=None, **fields):
        return DiaryEntry.objects.create(
            user=user or self.user,
            item=item,
            consumed_at=consumed_at,
            **fields,
        )

    def repair(self, show, user=None):
        """What the repair does for one show (backend-repair's tv_repair calls the same)."""
        user = user or self.user
        listing = tv_listing.get_listing(show)
        tv_tracking.prepare_items(user, show, listing)
        with transaction.atomic():
            tv = TV.objects.select_for_update().filter(user=user, item=show).first()
            if tv is None:
                tv = tv_tracking.ensure_tv(user, show)
            return tv_tracking.work_out(tv, listing)


class TV801LegacyRuleTests(LegacyTestCase):
    """TV-801: hollow Completed rows count as undated marks; the rest is worked out."""

    def test_a_42_the_job_leaves_a_legacy_show_then_a_tick_finds_it_marked(self):
        show = self.make_show({1: 3, 2: 2})
        tv_listing.get_listing(show)
        tv = self.old_tv(show, status=Status.COMPLETED.value)
        self.assertFalse(tv_tracking.refresh_due(tv))
        tv.refresh_from_db()
        self.assertEqual((tv.status, tv.legacy_pending, tv.reconciled_at), ("Completed", True, None))
        self.assertEqual(self.viewings(show), [])

        result = self.tick(show, 1, 1)

        tv = self.tv(show)
        self.assertEqual((tv.status, tv.legacy_pending), ("Completed", False))
        rows = self.viewings(show)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row.kind == Episode.Kind.FILL and row.diary_entry_id is None for row in rows))
        self.assertIsNone(result.earlier_unwatched)

    def test_tv_801_a_hollow_completed_season_fills_what_had_aired_by_then(self):
        show = self.make_show({1: [date(2026, 9, 1), date(2026, 9, 8), date(2026, 10, 20)]})
        tv = self.old_tv(show)
        self.clock.set(datetime(2026, 9, 10, 12, 0), tz="America/Los_Angeles")
        season = self.old_season(tv, 1, Status.IN_PROGRESS.value)
        self.old_viewing(season, 1, self.clock.moment)
        season.status = Status.COMPLETED.value
        season.save()  # set Completed on Sep 10, when E1 and E2 had aired
        self.clock.set(datetime(2026, 10, 25, 21, 0), tz="America/Los_Angeles")
        marks = self.repair(show)["marks"]
        self.assertEqual(marks, [{"level": "season", "season_number": 1, "count": 1}])
        self.assertEqual(self.watched(show), {(1, 1), (1, 2)})
        self.assertEqual(self.status(show), "In progress")

    def test_tv_801_a_completed_show_with_no_season_tracking_is_a_show_mark(self):
        show = self.make_show({1: 2, 2: 2})
        tv = self.old_tv(show, status=Status.COMPLETED.value)
        self.old_season(tv, 2)  # an empty "Started" season counts as no tracking
        marks = self.repair(show)["marks"]
        self.assertEqual(marks, [{"level": "show", "season_number": None, "count": 4}])
        self.assertEqual(self.status(show), "Completed")

    def test_tv_801_show_level_choices_survive_and_season_values_dont(self):
        show = self.make_show({1: 3, 2: 3})
        paused = self.old_tv(show, status=Status.PAUSED.value)
        season = self.old_season(paused, 1, Status.DROPPED.value)
        self.old_viewing(season, 1, datetime(2026, 10, 1, tzinfo=UTC))
        self.repair(show)
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.choice), ("Paused", "Paused"))
        self.assertEqual(Season.objects.get(pk=season.pk).status, "Paused")

        other = self.make_show({1: 3})
        watching = self.old_tv(other, status=Status.IN_PROGRESS.value)
        dropped_season = self.old_season(watching, 1, Status.DROPPED.value)
        self.old_viewing(dropped_season, 1, datetime(2026, 10, 1, tzinfo=UTC))
        self.repair(other)
        self.assertEqual(self.status(other), "In progress")
        self.assertEqual(Season.objects.get(pk=dropped_season.pk).status, "In progress")

    def test_tv_801_an_in_progress_show_with_nothing_stays_watching(self):
        show = self.make_show({1: 3})
        self.old_tv(show, status=Status.IN_PROGRESS.value)
        self.repair(show)
        tv = self.tv(show)
        self.assertEqual((tv.status, tv.choice, tv.legacy_pending), ("In progress", "In progress", False))

    def test_tv_801_once_worked_out_the_rule_never_applies_again(self):
        show = self.make_show({1: 2})
        tv = self.old_tv(show, status=Status.COMPLETED.value)
        self.repair(show)
        tv.refresh_from_db()
        self.assertEqual(tv_tracking.apply_legacy_rule(tv, tv_listing.get_listing(show)), [])
        tv_tracking.unmark(self.user, show)
        self.assertIsNone(self.tv(show))


class TV801LogConversionTests(LegacyTestCase):
    """TV-801: each old log gets a calendar date, coverage, fills and a viewing link."""

    def test_a_59_an_old_season_log_with_e1_and_e2_ticked(self):
        """A-59 (domain): E3 is that log's fill, E1 and E2 keep their dates, finished."""
        show = self.make_show({1: 3})
        tv = self.old_tv(show)
        season = self.old_season(tv, 1)
        first = datetime(2026, 10, 1, 20, 0, tzinfo=UTC)
        second = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)
        self.old_viewing(season, 1, first)
        self.old_viewing(season, 2, second)
        entry = self.old_log(self.season_item(show, 1), datetime(2026, 10, 3, 20, 0, tzinfo=UTC), rating=8)
        self.repair(show)
        fill = self.viewings(show, 1, 3)[0]
        self.assertEqual((fill.kind, fill.diary_entry_id, fill.end_date), (Episode.Kind.FILL, entry.pk, None))
        self.assertEqual(self.viewings(show, 1, 1)[0].end_date, first)
        self.assertEqual(self.viewings(show, 1, 1)[0].watched_on, date(2026, 10, 1))
        self.assertEqual(self.status(show), "Completed")
        entry.refresh_from_db()
        self.assertEqual(entry.progress_snapshot, {"tv": {"v": 1, "covers": {"1": [1, 2, 3]}}})
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 3, tzinfo=UTC))

    def test_tv_801_an_old_episode_log_links_one_viewing(self):
        show = self.make_show({1: 3})
        tv = self.old_tv(show)
        season = self.old_season(tv, 1)
        at = datetime(2026, 10, 4, 3, 0, tzinfo=UTC)  # Oct 3 in Los Angeles
        exact = self.old_viewing(season, 1, at)
        other = self.old_viewing(season, 1, at - timedelta(hours=1))
        exact_log = self.old_log(self.episode_item(show, 1, 1), at)
        same_day = self.old_viewing(season, 2, datetime(2026, 10, 5, 18, 0, tzinfo=UTC))
        day_log = self.old_log(self.episode_item(show, 1, 2), datetime(2026, 10, 5, 22, 0, tzinfo=UTC))
        no_viewing_log = self.old_log(self.episode_item(show, 1, 3), datetime(2026, 10, 6, 22, 0, tzinfo=UTC))
        self.repair(show)
        exact.refresh_from_db()
        other.refresh_from_db()
        same_day.refresh_from_db()
        self.assertEqual(exact.diary_entry_id, exact_log.pk)
        self.assertIsNone(other.diary_entry_id)
        self.assertEqual(same_day.diary_entry_id, day_log.pk)
        added = self.viewings(show, 1, 3)[0]
        self.assertEqual((added.kind, added.diary_entry_id, added.watched_on), (Episode.Kind.LOG, no_viewing_log.pk, date(2026, 10, 6)))

    @override_settings(TIME_ZONE="America/New_York")
    def test_a_67_a_9pm_new_york_log_keeps_its_new_york_day(self):
        """A-67 (domain): no stored calendar, server on New York."""
        user = make_user(calendar="")
        show = self.make_show({1: 2})
        nine_pm = datetime(2026, 10, 25, 21, 0, tzinfo=NEW_YORK)
        entry = self.old_log(self.season_item(show, 1), nine_pm, user=user)
        changes = self.repair(show, user=user)["shown_day_changes"]
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))
        self.assertEqual(changes, [])

    @override_settings(TIME_ZONE="America/New_York")
    def test_a_67_a_log_whose_shown_day_changes_is_listed(self):
        show = self.make_show({1: 2})
        one_am = datetime(2026, 10, 26, 1, 0, tzinfo=NEW_YORK)  # 10 p.m. Oct 25 in Los Angeles
        entry = self.old_log(self.season_item(show, 1), one_am)
        changes = self.repair(show)["shown_day_changes"]
        self.assertEqual(changes, [{"entry_id": entry.pk, "before": "2026-10-26", "after": "2026-10-25"}])

    def test_tv_801_a_legacy_log_dated_in_the_future_keeps_its_day(self):
        show = self.make_show({1: 2})
        entry = self.old_log(self.season_item(show, 1), datetime(2026, 12, 1, 20, 0, tzinfo=UTC))
        self.repair(show)
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at, datetime(2026, 12, 1, tzinfo=UTC))


class TV801GatingTests(LegacyTestCase):
    """TV-801: which entry points work a legacy show out, and order independence."""

    def setup_log_only_show(self, user):
        show = self.show_for_order
        self.old_log(self.season_item(show, 1), datetime(2026, 10, 1, 20, 0, tzinfo=UTC), user=user, rating=7)
        self.old_log(self.episode_item(show, 2, 1), datetime(2026, 10, 3, 20, 0, tzinfo=UTC), user=user)
        return show

    def test_tv_801_tick_then_repair_equals_repair_then_tick(self):
        self.show_for_order = self.make_show({1: 2, 2: 3})
        first = make_user(calendar="America/Los_Angeles")
        second = make_user(calendar="America/Los_Angeles")
        show = self.setup_log_only_show(first)
        self.setup_log_only_show(second)

        self.advance()
        tv_tracking.tick(first, show, 2, 2)
        self.repair(show, user=first)

        self.repair(show, user=second)
        tv_tracking.tick(second, show, 2, 2)

        one, two = snapshot(first, show), snapshot(second, show)
        self.assertEqual(one, two)
        self.assertEqual(one["status"], "In progress")
        self.assertFalse(one["legacy"])
        self.assertEqual(one["up_next"], (2, 3))

    def test_tv_801_a_webhook_or_import_write_leaves_a_log_only_show_legacy(self):
        """Neither works the show out; a webhook first records the old log's facts.

        The log's fills are facts about the show from before the webhook, so they come
        first and the watch applies on top (TV-207: the repair, run before or after,
        then reaches the same state). Nothing is worked out: no stored status or counts.
        """
        show = self.make_show({1: 2, 2: 2})
        log = self.old_log(self.season_item(show, 1), datetime(2026, 10, 1, 20, 0, tzinfo=UTC))
        self.advance()
        tv_tracking.webhook_event(self.user, show, 2, 1, event="watched", at=self.clock.moment)
        tv = self.tv(show)
        self.assertTrue(tv.legacy_pending)
        self.assertIsNone(tv.reconciled_at)
        self.assertEqual(
            sorted((row.item.season_number, row.item.episode_number, row.kind, row.diary_entry_id) for row in self.viewings(show)),
            [(1, 1, Episode.Kind.FILL, log.pk), (1, 2, Episode.Kind.FILL, log.pk), (2, 1, Episode.Kind.TICK, None)],
        )
        self.assertEqual((tv.watched_count, tv.up_next_season), (None, None))
        tv_tracking.reconcile_imported(self.user, [show])
        tv.refresh_from_db()
        self.assertTrue(tv.legacy_pending)

    def test_tv_801_an_unstamped_log_after_the_row_exists_converts_on_the_next_write(self):
        show = self.make_show({1: 3, 2: 2})
        self.tick(show, 1, 1)
        rollback_log = self.old_log(self.season_item(show, 1), datetime(2026, 10, 20, 20, 0, tzinfo=UTC))
        self.tick(show, 2, 1)
        rollback_log.refresh_from_db()
        self.assertEqual(rollback_log.progress_snapshot["tv"]["v"], 1)
        self.assertEqual(self.watched(show), {(1, 1), (1, 2), (1, 3), (2, 1)})

    def test_tv_705_a_legacy_page_shows_what_the_first_write_stores(self):
        show = self.make_show({1: 3, 2: 2})
        tv = self.old_tv(show, status=Status.COMPLETED.value)
        season = self.old_season(tv, 1, Status.COMPLETED.value)
        self.old_viewing(season, 3, datetime(2026, 10, 1, 20, 0, tzinfo=UTC))
        self.old_log(self.season_item(show, 2), datetime(2026, 10, 2, 20, 0, tzinfo=UTC), rating=6)
        before = self.state(show)
        season_before = self.state(self.season_item(show, 1))["season"]
        self.assertTrue(tv_tracking.plan_legacy(tv, tv_listing.get_listing(show)).mark_fills)
        # An app write that changes nothing itself: Season 2 is already logged.
        self.assertFalse(tv_tracking.mark_watched(self.user, show, season_number=2).changed)
        after = self.state(show)
        self.assertFalse(self.tv(show).legacy_pending)
        for key in ("status", "finished", "watched_count", "aired_count", "up_next", "eye", "available_actions"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(season_before["state"], self.state(self.season_item(show, 1))["season"]["state"])


class TV703OldSeasonProgressChangeTests(LegacyTestCase):
    """TV-703, TV-900: the write that moves a stored season's count records the change.

    A season row stored before the contract has no stored count; the write compares
    against the count worked out before it ran, so installed builds' Home sees the
    first tick, and working out the old data records nothing of its own.
    """

    def progress_changes(self):
        from social.models import ProgressChange  # noqa: PLC0415

        return [
            (change.item.season_number, change.previous_progress["value"], change.current_progress["value"])
            for change in ProgressChange.objects.filter(actor=self.user).select_related("item").order_by("id")
        ]

    def test_tv_703_the_first_tick_on_a_legacy_shows_season_records_a_progress_change(self):
        show = self.make_show({1: 3})
        tv = self.old_tv(show)
        season = self.old_season(tv, 1)
        self.old_viewing(season, 1, self.clock.moment - timedelta(days=2))
        self.tick(show, 1, 2)
        self.assertEqual(self.progress_changes(), [(1, 1, 2)])

    def test_tv_703_the_first_tick_on_an_unworked_season_row_records_a_progress_change(self):
        show = self.make_show({1: 3})
        tv = self.old_tv(show)
        TV.objects.filter(pk=tv.pk).update(legacy_pending=False)
        season = self.old_season(tv, 1)
        self.old_viewing(season, 1, self.clock.moment - timedelta(days=2))
        self.tick(show, 1, 2)
        self.tick(show, 1, 3)
        self.assertEqual(self.progress_changes(), [(1, 1, 2), (1, 2, 3)])

    def test_tv_703_a_write_that_leaves_an_old_seasons_count_records_nothing(self):
        show = self.make_show({1: 3, 2: 2})
        tv = self.old_tv(show)
        season = self.old_season(tv, 1)
        self.old_viewing(season, 1, self.clock.moment - timedelta(days=2))
        tv_tracking.choose(self.user, show, "Paused")
        self.assertEqual(self.progress_changes(), [])
