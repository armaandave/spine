"""reconcile_tv: the one-time repair (docs/tv-tracking-contract.md, TV-801 and TV-804).

The repair works out every show stored before the contract the way the user's first
app or web write does (tv_tracking.work_out), then fills empty show and season stars
from logs (TV-804). These tests drive the command itself, with data shaped like the
old code stored it: TV rows with legacy_pending set, season rows with stored statuses,
viewings with only end_date, and logs without the contract's stamp.

The server runs on America/New_York and the account calendar is America/Los_Angeles
unless a test says otherwise (TVTrackingTestCase); the clock is patched through
django.utils.timezone.now.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from io import StringIO
from zoneinfo import ZoneInfo

from django.core.management import CommandError, call_command
from django.test import override_settings

from app import tv_listing, tv_repair, tv_tracking
from app.models import TV, DiaryEntry, Episode, Item, MediaLike, Season, Status, TVMutation
from app.tests.test_tv_tracking_viewings import TVTrackingTestCase
from app.tests.tv_fixtures import make_user
from social.models import Activity, ProgressChange

NEW_YORK = ZoneInfo("America/New_York")
COMPLETED = Status.COMPLETED.value
WATCHING = Status.IN_PROGRESS.value
DROPPED = Status.DROPPED.value


def at(day, hour=20):
    return datetime(2026, 10, day, hour, 0, tzinfo=UTC)


def tracking_tables():
    """Every stored TV fact and derived value, for "nothing changed" checks."""
    return {
        "tv": list(TV.objects.order_by("pk").values()),
        "season": list(Season.objects.order_by("pk").values()),
        "episode": list(Episode.objects.order_by("pk").values()),
        "diary": list(DiaryEntry.objects.order_by("pk").values()),
        "likes": list(MediaLike.objects.order_by("pk").values()),
        "items": Item.objects.count(),
        "history": (
            TV.history.count(),
            Season.history.count(),
            Episode.history.count(),
            DiaryEntry.history.count(),
        ),
        "progress_changes": ProgressChange.objects.count(),
        "activity": Activity.objects.count(),
        "mutations": TVMutation.objects.count(),
    }


def outcome(user, show):
    """The user-visible result for one show, independent of row ids and write times."""
    tv = TV.objects.filter(user=user, item=show).first()
    entries = {
        entry.pk: entry
        for entry in DiaryEntry.objects.filter(user=user).select_related("item")
    }

    def log_key(entry_id):
        entry = entries.get(entry_id)
        if entry is None:
            return None
        return (entry.item.media_type, entry.item.season_number, entry.item.episode_number, entry.consumed_at)

    seasons = Season.objects.filter(related_tv=tv).select_related("item")
    viewings = Episode.objects.filter(related_season__related_tv=tv).select_related("item")
    return {
        "show": (
            tv.status,
            tv.choice,
            tv.legacy_pending,
            tv.watched_count,
            tv.aired_count,
            tv.up_next_season,
            tv.up_next_episode,
            tv.finished,
            tv.caught_up,
            tv.last_watched_on,
            tv.score,
            log_key(tv.rating_source_id),
        ),
        "seasons": sorted(
            (
                row.item.season_number,
                row.status,
                row.is_started,
                row.watched_count,
                row.score,
                log_key(row.rating_source_id),
            )
            for row in seasons
        ),  # season numbers are unique per show
        "viewings": sorted(
            (
                (
                    row.item.season_number,
                    row.item.episode_number,
                    row.kind,
                    log_key(row.diary_entry_id),
                    row.watched_on,
                )
                for row in viewings
            ),
            key=repr,
        ),
        "logs": sorted(
            ((*log_key(entry.pk), str(entry.progress_snapshot), entry.rating) for entry in entries.values()),
            key=repr,
        ),
    }


class RepairTestCase(TVTrackingTestCase):
    """Data as code from before the contract stored it, and the command's output."""

    def old_tv(self, show, user=None, **fields):
        """A TV row stored before the contract (legacy_pending, as 0085's db_default)."""
        fields.setdefault("status", WATCHING)
        tv = TV.objects.create(item=show, user=user or self.user, **fields)
        TV.objects.filter(pk=tv.pk).update(legacy_pending=True)
        tv.refresh_from_db()
        return tv

    def old_season(self, tv, number, status=WATCHING):
        return Season.objects.create(
            item=self.season_item(tv.item, number),
            user=tv.user,
            related_tv=tv,
            status=status,
        )

    def old_viewing(self, season, episode_number, end_date):
        """A viewing as old code stored it: kind defaults to tick, no watched_on."""
        return Episode.objects.create(
            item=self.episode_item(season.related_tv.item, season.item.season_number, episode_number),
            related_season=season,
            end_date=end_date,
        )

    def old_log(self, item, consumed_at, user=None, **fields):
        """A log without the contract's stamp, with its date and time as old code kept it."""
        return DiaryEntry.objects.create(user=user or self.user, item=item, consumed_at=consumed_at, **fields)

    def repair(self, *args):
        out = StringIO()
        call_command("reconcile_tv", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def legacy_library(self):
        """Three shows stored before the contract, one of each common shape."""
        # An empty "Started" Season 2 from the old next-season handoff (T08).
        handoff = self.make_show({1: 2, 2: 2}, title="Handoff")
        tv = self.old_tv(handoff)
        first = self.old_season(tv, 1, COMPLETED)
        self.old_viewing(first, 1, at(1))
        self.old_viewing(first, 2, at(2))
        self.old_season(tv, 2, WATCHING)
        # Logged only: today it isn't in the Library at all (T01).
        logged = self.make_show({1: 2, 2: 2}, title="Logged")
        season_log = self.old_log(self.season_item(logged, 1), at(3), rating=Decimal(8))
        # A season-level Dropped under a show in Watching (TV-204).
        dropped = self.make_show({1: 3, 2: 3}, title="Dropped Season")
        dropped_tv = self.old_tv(dropped)
        dropped_season = self.old_season(dropped_tv, 1, DROPPED)
        self.old_viewing(dropped_season, 1, at(4))
        return {"handoff": handoff, "logged": logged, "season_log": season_log, "dropped": dropped}


class TV801CommandTests(RepairTestCase):
    """TV-801: a dry run by default, a report of every change, and safe to run again."""

    def test_tv_801_a_dry_run_changes_nothing_and_reports_what_apply_saves(self):
        shows = self.legacy_library()
        before = tracking_tables()

        dry = self.repair()

        self.assertEqual(tracking_tables(), before)
        self.assertTrue(dry.startswith("Dry run: nothing is saved."))
        log_id = shows["season_log"].pk
        for line in (
            "tv-user-",  # usernames come from make_user
            "Handoff (tmdb ",
            "  Season 2: In progress → not started",
            "Logged (tmdb ",
            "  Newly in the Library",
            "  Status: not in the Library → In progress",
            "  Season 1: not started → Completed",
            f"  Stars filled: Season 1 8.0 from log {log_id}",
            "  Season 1: Dropped → In progress (season-level Dropped replaced)",
            "Shows changed: 3",
            "Newly in the Library: 1",
            "Season-level Paused or Dropped replaced: 1",
        ):
            self.assertIn(line, dry)

        applied = self.repair("--apply")

        self.assertTrue(applied.startswith("Applying changes."))
        self.assertEqual(applied.splitlines()[1:], dry.splitlines()[1:])
        self.assertNotEqual(tracking_tables(), before)

    def test_tv_801_apply_twice_equals_apply_once(self):
        self.legacy_library()
        self.repair("--apply")
        once = tracking_tables()
        self.advance()

        again = self.repair("--apply")

        self.assertEqual(tracking_tables(), once)
        self.assertIn("Shows changed: 0", again)
        self.assertIn("Status changes: 0", again)

    def test_tv_801_user_and_limit_choose_the_shows(self):
        alice = make_user("alice")
        bob = make_user("bob")
        first = self.make_show({1: 2}, title="First")
        second = self.make_show({1: 2}, title="Second")
        for user in (alice, bob):
            for show in (first, second):
                self.old_log(self.season_item(show, 1), at(5), user=user)

        self.repair("--apply", "--user", "bob", "--limit", "1")

        self.assertFalse(TV.objects.filter(user=alice).exists())
        self.assertEqual(TV.objects.filter(user=bob).count(), 1)
        self.repair("--apply", "--user", "bob")
        self.assertEqual(TV.objects.filter(user=bob).count(), 2)
        with self.assertRaises(CommandError):
            self.repair("--user", "nobody")

    def test_tv_801_a_show_whose_details_are_unavailable_is_reported_and_retried(self):
        reachable = self.make_show({1: 2}, title="Reachable")
        unreachable = self.make_show({1: 2}, title="Unreachable")
        for show in (reachable, unreachable):
            self.old_log(self.season_item(show, 1), at(5))
        self.fake.fail(unreachable.media_id)
        errors = StringIO()

        with self.assertRaises(CommandError), self.assertLogs("app.management.commands.reconcile_tv", "ERROR"):
            call_command("reconcile_tv", "--apply", stdout=StringIO(), stderr=errors)

        self.assertIn("Unreachable: could not repair: TV details are unavailable right now", errors.getvalue())
        self.assertIsNotNone(self.tv(reachable))
        self.assertIsNone(self.tv(unreachable))
        self.fake.recover()
        self.repair("--apply")
        self.assertEqual(self.status(unreachable), COMPLETED)

    def test_tv_801_iter_targets_finds_tv_rows_and_logs_of_any_level(self):
        tracked = self.make_show({1: 2}, title="Tracked")
        self.old_tv(tracked)
        episode_logged = self.make_show({1: 2}, title="Episode Logged")
        self.old_log(self.episode_item(episode_logged, 1, 2), at(5))
        self.make_show({1: 2}, title="Untouched")
        other = make_user("other")
        self.old_log(tracked, at(6), user=other)

        targets = [(user.username, item.title) for user, item in tv_repair.iter_targets()]

        self.assertEqual(
            sorted(targets),
            sorted(
                [
                    (self.user.username, "Tracked"),
                    (self.user.username, "Episode Logged"),
                    ("other", "Tracked"),
                ],
            ),
        )
        self.assertEqual(
            [item.media_type for _, item in tv_repair.iter_targets(other)],
            ["tv"],
        )


class TV801RepairTests(RepairTestCase):
    """TV-801: what the repair does to each shape of old data."""

    def test_t01_logged_only_seasons_and_shows_join_the_library(self):
        """T01: season and show logs made no tracking; after the repair they do (TV-400)."""
        season_logged = self.make_show({1: 3, 2: 3}, title="Season Logged")
        show_logged = self.make_show({1: 2}, title="Show Logged")
        season_log = self.old_log(self.season_item(season_logged, 1), at(1), rating=Decimal(8))
        show_log = self.old_log(show_logged, at(2), rating=Decimal(9))

        output = self.repair("--apply")

        tv = self.tv(season_logged)
        self.assertEqual((tv.status, tv.legacy_pending), (WATCHING, False))
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (2, 1))
        self.assertEqual((tv.watched_count, tv.aired_count), (3, 6))
        fills = self.viewings(season_logged)
        self.assertEqual(
            [(row.item.episode_number, row.kind, row.diary_entry_id, row.watched_on) for row in fills],
            [(number, Episode.Kind.FILL, season_log.pk, None) for number in (1, 2, 3)],
        )
        self.assertTrue(tv_tracking.list_payload(tv)["in_library"])
        self.assertEqual(self.status(show_logged), COMPLETED)
        self.assertEqual(output.count("  Newly in the Library"), 2)
        # TV-804: each log's stars become its own level's stars, and nothing else's.
        season = Season.objects.get(related_tv=tv, item__season_number=1)
        self.assertEqual((season.score, season.rating_source_id), (Decimal(8), season_log.pk))
        self.assertIsNone(tv.score)
        show_tv = self.tv(show_logged)
        self.assertEqual((show_tv.score, show_tv.rating_source_id), (Decimal(9), show_log.pk))

    def test_tv_205_empty_started_seasons_stop_counting(self):
        """T08: an empty "Started" season stays stored but isn't started (TV-205, TV-801)."""
        show = self.make_show({1: 2, 2: 2})
        tv = self.old_tv(show)
        first = self.old_season(tv, 1, COMPLETED)
        self.old_viewing(first, 1, at(1))
        self.old_viewing(first, 2, at(2))
        empty = self.old_season(tv, 2, WATCHING)

        output = self.repair("--apply")

        empty.refresh_from_db()
        self.assertFalse(empty.is_started)
        cards = tv_tracking.season_card_states(self.user, show, tv_listing.get_listing(show))
        self.assertFalse(cards[2]["started"])
        self.assertTrue(cards[1]["started"])
        self.assertFalse(Season.objects.filter(related_tv=tv, is_started=True, status=WATCHING).exists())
        tv.refresh_from_db()
        self.assertEqual((tv.status, tv.up_next_season, tv.up_next_episode), (WATCHING, 2, 1))
        self.assertIn("  Season 2: In progress → not started", output)

    def test_tv_801_a_finale_only_completed_season_becomes_a_mark(self):
        """T07: the old finale rule left a hollow Completed season; it becomes a mark."""
        show = self.make_show({1: 4})
        tv = self.old_tv(show, status=COMPLETED)
        season = self.old_season(tv, 1, COMPLETED)
        finale_at = datetime(2026, 10, 10, 2, 0, tzinfo=UTC)  # Oct 9 in Los Angeles
        finale = self.old_viewing(season, 4, finale_at)

        output = self.repair("--apply")

        finale.refresh_from_db()
        self.assertEqual(
            (finale.kind, finale.end_date, finale.watched_on),
            (Episode.Kind.TICK, finale_at, date(2026, 10, 9)),
        )
        fills = [row for row in self.viewings(show) if row.pk != finale.pk]
        self.assertEqual(
            [(row.item.episode_number, row.kind, row.diary_entry_id, row.end_date) for row in fills],
            [(number, Episode.Kind.FILL, None, None) for number in (1, 2, 3)],
        )
        tv.refresh_from_db()
        self.assertEqual((tv.status, tv.finished), (COMPLETED, True))
        self.assertIn("  Marked watched (legacy rule): Season 1, 3 episodes", output)

    def test_a_59_a_season_log_from_before_fills_the_rest_of_its_season(self):
        """A-59: E3 is that log's fill, E1 and E2 keep their dates, and it's finished."""
        show = self.make_show({1: 3})
        tv = self.old_tv(show)
        season = self.old_season(tv, 1)
        first = self.old_viewing(season, 1, at(1))
        second = self.old_viewing(season, 2, at(2))
        season_log = self.old_log(self.season_item(show, 1), at(3), rating=Decimal(8))

        self.repair("--apply")

        fill = self.viewings(show, 1, 3)[0]
        self.assertEqual(
            (fill.kind, fill.diary_entry_id, fill.end_date, fill.watched_on),
            (Episode.Kind.FILL, season_log.pk, None, None),
        )
        for row, moment in ((first, at(1)), (second, at(2))):
            row.refresh_from_db()
            self.assertEqual((row.kind, row.end_date, row.diary_entry_id), (Episode.Kind.TICK, moment, None))
        season.refresh_from_db()
        self.assertEqual(season.status, COMPLETED)
        self.assertEqual(self.status(show), COMPLETED)
        season_log.refresh_from_db()
        self.assertEqual(season_log.progress_snapshot, {"tv": {"v": 1, "covers": {"1": [1, 2, 3]}}})

    def test_tv_204_a_season_level_dropped_under_a_watching_show_is_listed_and_replaced(self):
        """TV-204, TV-801: a season-level Dropped isn't a choice; the season follows the show."""
        show = self.make_show({1: 3, 2: 3})
        tv = self.old_tv(show)
        season = self.old_season(tv, 1, DROPPED)
        self.old_viewing(season, 1, at(1))

        output = self.repair("--apply")

        season.refresh_from_db()
        self.assertEqual(season.status, WATCHING)
        tv.refresh_from_db()
        self.assertEqual((tv.status, tv.choice), (WATCHING, ""))
        self.assertIn("  Season 1: Dropped → In progress (season-level Dropped replaced)", output)
        self.assertIn("Season-level Paused or Dropped replaced: 1", output)


class TV801DateTests(RepairTestCase):
    """A-67 and TV-407: old log dates become calendar dates on the user's calendar."""

    def test_a_67_a_9pm_new_york_log_keeps_its_new_york_day(self):
        """A-67: with or without a stored calendar, the day is never the UTC day."""
        self.clock.set(datetime(2026, 10, 26, 12, 0), tz="America/New_York")
        no_calendar = make_user("no-calendar", calendar="")
        pacific = make_user("pacific", calendar="America/Los_Angeles")
        show = self.make_show({1: 2})
        nine_pm = datetime(2026, 10, 25, 21, 0, tzinfo=NEW_YORK)  # 01:00 UTC on Oct 26
        entries = [self.old_log(self.season_item(show, 1), nine_pm, user=user) for user in (no_calendar, pacific)]

        output = self.repair("--apply")

        for entry in entries:
            entry.refresh_from_db()
            self.assertEqual(entry.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))
        self.assertNotIn("): shown day", output)
        self.assertIn("Logs whose shown day changes: 0", output)
        self.assertIn(
            "Users with no stored calendar (their logs convert on America/New_York): no-calendar\n",
            output,
        )

    @override_settings(TIME_ZONE="UTC")
    def test_a_67_on_a_utc_server_the_stored_calendar_decides_the_day(self):
        """Without a stored calendar the server's zone decides; with one, the user's."""
        self.clock.set(datetime(2026, 10, 26, 12, 0), tz="UTC")
        no_calendar = make_user("utc-no-calendar", calendar="")
        pacific = make_user("utc-pacific", calendar="America/Los_Angeles")
        show = self.make_show({1: 2})
        nine_pm = datetime(2026, 10, 25, 21, 0, tzinfo=NEW_YORK)  # 01:00 UTC on Oct 26
        server_day = self.old_log(self.season_item(show, 1), nine_pm, user=no_calendar)
        pacific_day = self.old_log(self.season_item(show, 1), nine_pm, user=pacific)

        output = self.repair("--apply")

        server_day.refresh_from_db()
        pacific_day.refresh_from_db()
        self.assertEqual(server_day.consumed_at, datetime(2026, 10, 26, tzinfo=UTC))
        self.assertEqual(pacific_day.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))
        self.assertIn(f"  Log {pacific_day.pk} (Season 1): shown day 2026-10-26 → 2026-10-25", output)
        self.assertNotIn(f"  Log {server_day.pk} ", output)
        self.assertIn("(their logs convert on UTC): utc-no-calendar\n", output)

    def test_a_67_the_report_lists_every_log_whose_shown_day_changes(self):
        """A 1 a.m. New York log is 10 p.m. the day before on a Los Angeles calendar."""
        self.clock.set(datetime(2026, 10, 26, 12, 0), tz="America/New_York")
        show = self.make_show({1: 2, 2: 2})
        one_am = datetime(2026, 10, 26, 1, 0, tzinfo=NEW_YORK)
        season_log = self.old_log(self.season_item(show, 1), one_am)
        episode_log = self.old_log(self.episode_item(show, 2, 1), one_am)

        dry = self.repair()

        self.assertIn(f"  Log {season_log.pk} (Season 1): shown day 2026-10-26 → 2026-10-25", dry)
        self.assertIn(f"  Log {episode_log.pk} (S2 E1): shown day 2026-10-26 → 2026-10-25", dry)
        self.assertIn("Logs whose shown day changes: 2", dry)
        self.assertIn("(their logs convert on America/New_York): none", dry)
        season_log.refresh_from_db()
        self.assertEqual(season_log.consumed_at, one_am)

        self.repair("--apply")

        season_log.refresh_from_db()
        self.assertEqual(season_log.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))
        logged_viewing = self.viewings(show, 2, 1)[0]
        self.assertEqual((logged_viewing.kind, logged_viewing.watched_on), (Episode.Kind.LOG, date(2026, 10, 25)))


class TV804RepairStarTests(RepairTestCase):
    """TV-804: the repair fills the stars of every show and season it works out."""

    def test_tv_804_empty_show_and_season_stars_take_the_newest_rated_log(self):
        show = self.make_show({1: 2, 2: 2})
        season_item = self.season_item(show, 1)
        self.old_log(season_item, at(1), rating=Decimal(6))
        newest = self.old_log(season_item, at(5), rating=Decimal(8))
        self.old_log(season_item, at(7))  # newer, but without stars
        show_log = self.old_log(show, at(8), rating=Decimal(9))
        activity_before = Activity.objects.count()

        output = self.repair("--apply")

        tv = self.tv(show)
        self.assertEqual((tv.score, tv.rating_source_id), (Decimal(9), show_log.pk))
        seasons = {row.item.season_number: row for row in Season.objects.filter(related_tv=tv)}
        self.assertEqual((seasons[1].score, seasons[1].rating_source_id), (Decimal(8), newest.pk))
        self.assertEqual((seasons[2].score, seasons[2].rating_source_id), (None, None))
        self.assertIn(f"  Stars filled: Season 1 8.0 from log {newest.pk}", output)
        self.assertIn(f"  Stars filled: the show 9.0 from log {show_log.pk}", output)
        filled = TV.history.filter(id=tv.pk).order_by("-history_date", "-history_id").first()
        self.assertEqual((filled.score, filled.history_change_reason), (Decimal(9), tv_repair.STAR_FILL_REASON))
        self.assertEqual(Activity.objects.count(), activity_before)  # TV-804 posts nothing

    def test_tv_804_stars_cleared_on_purpose_stay_empty(self):
        show = self.make_show({1: 2})
        tv = self.old_tv(show)
        season = self.old_season(tv, 1)
        self.old_viewing(season, 1, at(1))
        for row in (tv, season):
            row.score = Decimal(6)
            row.save()
            row.score = None
            row.save()
        self.old_log(show, at(2), rating=Decimal(9))
        self.old_log(self.season_item(show, 1), at(3), rating=Decimal(8))

        output = self.repair("--apply")

        for row in (tv, season):
            row.refresh_from_db()
            self.assertEqual((row.score, row.rating_source_id), (None, None))
        self.assertNotIn("Stars filled:", output.split("\n\nShows checked")[0])
        self.assertIn("Stars filled: 0", output)


class TV801OrderTests(RepairTestCase):
    """TV-801: the repair and the user's own app or web write can come in either order."""

    def logged_show(self, users):
        show = self.make_show({1: 3, 2: 3})
        for user in users:
            self.old_log(self.season_item(show, 1), at(1), user=user, rating=Decimal(7))
            self.old_log(self.episode_item(show, 2, 1), at(3), user=user)
        return show

    def assert_order_independent(self, write):
        first = make_user("write-first")
        second = make_user("repair-first")
        show = self.logged_show((first, second))

        self.advance()
        write(first, show)
        self.advance()
        self.repair("--apply", "--user", "write-first")

        self.advance()
        self.repair("--apply", "--user", "repair-first")
        self.advance()
        write(second, show)

        result = outcome(first, show)
        self.assertEqual(result, outcome(second, show))
        self.assertEqual(result["show"][:3], (WATCHING, "", False))
        self.assertEqual(result["seasons"][0][4:], (Decimal(7), ("season", 1, None, datetime(2026, 10, 1, tzinfo=UTC))))
        return result

    def test_tv_801_an_app_tick_then_the_repair_equals_the_repair_then_the_tick(self):
        result = self.assert_order_independent(
            lambda user, show: tv_tracking.tick(user, show, 2, 2),
        )
        self.assertEqual(result["show"][5:7], (2, 3))

    def test_tv_801_a_web_write_then_the_repair_equals_the_repair_then_the_web_write(self):
        result = self.assert_order_independent(
            lambda user, show: tv_tracking.tick(user, show, 2, 2, at=date(2026, 10, 20), entry_point=tv_tracking.WEB),
        )
        self.assertIn((2, 2, Episode.Kind.TICK, None, date(2026, 10, 20)), result["viewings"])
