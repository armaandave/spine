"""compare_activity_numbers: Stats' number beside the Activity list's, per account.

It's the read-only count decision 29 approved. It prints counts and user ids, writes
nothing and calls no provider. It holds counts 2 and 14, so they run in CI. Every line
reads "number->list", and the two agree by design (decision 2).
"""

import contextlib
import io
import re
from datetime import UTC, date, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from api.services import activity as activity_service
from api.services import activity_rows, stats_time
from api.services.stats_scope import StatsScope
from api.tests.test_activity_calc_books import BookCalcBase, d
from app import book_tracking, game_tracking, single_weight, tv_tracking
from app.management.commands.compare_activity_numbers import _qualifying_days
from app.models import Anime, DiaryEntry, Item, MediaTypes, Status
from app.tests.tv_fixtures import make_manual_show
from social.models import (
    Comment,
    ContentLike,
    Follow,
    FollowStatus,
    ProgressChange,
    ProgressPost,
)

WRITES = re.compile(r"^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE)\b", re.IGNORECASE)


@override_settings(TIME_ZONE="America/New_York")
class CompareTests(BookCalcBase):
    """One account with a known difference, and two kinds of reader."""

    def compare(self, **options):
        """Run the command and return its lines."""
        out = io.StringIO()
        call_command("compare_activity_numbers", stdout=out, **options)
        return out.getvalue().splitlines()

    def line(self, lines, start):
        """Return the one line that starts this way."""
        found = [line for line in lines if line.startswith(start)]
        self.assertEqual(len(found), 1, lines)
        return found[0]

    def seed_a_known_difference(self):
        """Read with no finish: 285 pages in the year, on Stats and on the list."""
        self.reading = self.book("reading", pages=500)
        self.begin(self.reading, d(2026, 3, 1))
        self.save(self.reading, 285, d(2026, 3, 2))

    def test_one_known_difference(self):
        self.seed_a_known_difference()
        lines = self.compare()
        line = self.line(lines, f"user {self.owner.pk} book 2026:")
        self.assertIn("value=285->285", line)
        self.assertIn("known=1->1", line)
        self.assertIn("total=1->1", line)
        self.assertIn("titles=1->1", line)
        self.assertIn("active_days=2->2", line)
        everything = self.line(lines, f"user {self.owner.pk} book all:")
        self.assertIn("value=285->285", everything)
        self.assertIn("pile=None->0", everything)

    def test_known_says_what_the_reader_says_when_an_older_row_is_unknown(self):
        """A percent with no length, then pages: only the newest row is known."""
        item = self.book("no-length", pages=None)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 25, d(2026, 3, 2), kind="percentage")
        self.save(item, 60, d(2026, 3, 3))
        line = self.line(self.compare(), f"user {self.owner.pk} book 2026:")
        self.assertIn("value=60->60", line)
        self.assertIn("known=0->0", line)
        self.assertIn("total=1->1", line)
        scope = StatsScope(owner=self.owner, viewer=self.owner).with_media_type(MediaTypes.BOOK.value)
        ranged = scope._sibling(start_date=d(2026, 1, 1), end_date=d(2026, 12, 31))
        with patch.object(activity_rows, "CALCULATED_TYPES", frozenset({MediaTypes.BOOK.value})):
            reader = stats_time.type_time(ranged, MediaTypes.BOOK.value)
        self.assertEqual((reader.value, reader.known, reader.total), (60, 0, 1))

    def test_known_reads_every_row_and_leaves_a_marked_game_out(self):
        """A percent, then hours, is unknown, and a mark has no hours to know."""
        played = self.item(MediaTypes.GAME.value, "played", title="played")
        with self.on(d(2026, 2, 1)):
            game_tracking.start(self.owner, played)
        with self.on(d(2026, 2, 2)):
            game_tracking.update_progress(self.owner, played, percentage=40)
        with self.on(d(2026, 2, 3)):
            game_tracking.update_progress(self.owner, played, total_minutes=300)
        marked = self.item(MediaTypes.GAME.value, "marked", title="marked")
        with self.on(d(2026, 2, 4)):
            game_tracking.mark_completed(self.owner, marked)
        line = self.line(self.compare(), f"user {self.owner.pk} game all:")
        self.assertIn("known=0->0 total=1->1 titles=2->2 ", line)

    def test_a_number_that_agrees_reads_the_same_on_both_sides(self):
        logged = self.book("logged")
        self.finish(logged, d(2025, 3, 1))
        marked = self.book("marked", pages=350)
        self.do(book_tracking.mark_read, marked, d(2026, 1, 1))
        lines = self.compare()
        self.assertIn("value=400->400", self.line(lines, f"user {self.owner.pk} book 2025:"))
        everything = self.line(lines, f"user {self.owner.pk} book all:")
        self.assertIn("value=750->750", everything)
        self.assertIn("pile=None->350", everything)

    def test_it_writes_nothing_and_calls_no_provider(self):
        self.seed_a_known_difference()
        with (
            CaptureQueriesContext(connection) as queries,
            patch("app.providers.services.api_request", side_effect=AssertionError("A provider call")),
            patch("requests.Session.request", side_effect=AssertionError("A network call")),
        ):
            self.compare()
        written = [query["sql"] for query in queries if WRITES.match(query["sql"])]
        self.assertEqual(written, [])

    def test_it_prints_only_counts_and_ids(self):
        self.reading = self.book("A Secret Title", pages=500)
        self.begin(self.reading, d(2026, 3, 1))
        printed = "\n".join(self.compare())
        self.assertNotIn("Secret", printed)
        self.assertNotIn(self.owner.username, printed)

    def test_it_prints_the_walk_and_the_stats_build_per_user(self):
        line = self.line(self.compare(), f"user {self.owner.pk}: ")
        self.assertRegex(line, r"walk_ms=\d+ walk_queries=\d+ stats_ms=\d+")

    def test_it_says_whether_jit_is_on(self):
        self.assertTrue(self.line(self.compare(), "jit: "))

    def test_one_account(self):
        lines = self.compare(user=self.follower.pk)
        self.assertTrue(self.line(lines, f"user {self.follower.pk}: "))
        self.assertFalse([line for line in lines if line.startswith(f"user {self.owner.pk}")])


@override_settings(TIME_ZONE="America/New_York")
class YearsTests(CompareTests):
    """A mark leaves every year and lands in the pile."""

    def test_an_anime_marked_in_2025_is_in_no_year_on_either_side(self):
        """Anime's mark leaves its fills out of every year (AN-704)."""
        item = self.item(MediaTypes.ANIME.value, "marked", title="marked")
        self.set_item(item, runtime_minutes=24)
        row = self.tracking(Anime, item, status=Status.COMPLETED.value, progress=12)
        self.anime_mark(row, range(1, 13))
        self.history(row, 0, datetime(2025, 3, 1, 17, tzinfo=UTC), kind="+")
        self.history(row, 12, datetime(2025, 3, 2, 17, tzinfo=UTC))
        lines = self.compare()
        self.assertFalse([line for line in lines if line.startswith(f"user {self.owner.pk} anime 2025:")])
        self.assertIn("value=288->288", self.line(lines, f"user {self.owner.pk} anime all:"))

    def test_a_year_holding_nothing_either_way_prints_nothing(self):
        self.seed_a_known_difference()
        lines = self.compare()
        self.assertFalse([line for line in lines if line.startswith(f"user {self.owner.pk} book 2025:")])
        self.assertFalse([line for line in lines if line.startswith(f"user {self.owner.pk} anime 2")])


class PhaseThreeTypesTests(CompareTests):
    """TV, movies and music: Stats beside the list."""

    def test_a_marked_film_logged_with_rewatch_on_rises_in_all_time_only(self):
        film = Item.objects.create(source="manual", media_type=MediaTypes.MOVIE.value, media_id="film", title="Film", runtime_minutes=120)
        with self.on(d(2026, 1, 10)):
            single_weight.mark_consumed(self.owner, film)
        with self.on(d(2026, 3, 1)):
            single_weight.create_log(self.owner, film, consumed_at=d(2026, 3, 1), is_rewatch=True)
        lines = self.compare()
        self.assertIn("value=120->120", self.line(lines, f"user {self.owner.pk} movie 2026:"))
        everything = self.line(lines, f"user {self.owner.pk} movie all:")
        self.assertIn("value=240->240", everything)
        self.assertIn("known=2->2", everything)
        self.assertIn("total=2->2", everything)

    def test_it_times_the_feed_for_an_account_that_follows_someone(self):
        friend = get_user_model().objects.create_user(username="friend", calendar_timezone="America/New_York")
        Follow.objects.create(from_user=self.owner, to_user=friend, status=FollowStatus.ACCEPTED)
        line = self.line(self.compare(), f"user {self.owner.pk} feed:")
        self.assertRegex(line, r"following=1 rows=0 feed_ms=\d+ feed_queries=\d+$")
        self.assertFalse([found for found in self.compare() if found.startswith(f"user {friend.pk} feed:")])

    def test_a_tick_a_backdated_season_log_takes_leaves_its_year(self):
        show = make_manual_show(seasons={1: 2})
        Item.objects.filter(media_id=show.media_id, media_type=MediaTypes.EPISODE.value).update(runtime_minutes=45)
        with self.on(d(2026, 3, 1)):
            tv_tracking.tick(self.owner, show, 1, 1)
        season = Item.objects.get(media_id=show.media_id, media_type=MediaTypes.SEASON.value)
        with self.on(d(2026, 3, 2)):
            tv_tracking.create_log(self.owner, season, consumed_at=d(2025, 12, 20))
        lines = self.compare()
        self.assertIn("value=90->90", self.line(lines, f"user {self.owner.pk} tv 2025:"))
        self.assertFalse([line for line in lines if line.startswith(f"user {self.owner.pk} tv 2026:")])
        everything = self.line(lines, f"user {self.owner.pk} tv all:")
        self.assertIn("value=90->90", everything)
        self.assertIn("total=2->2", everything)


class CountTwoTests(CompareTests):
    """Count 2: progress posts by class (plan 5.8)."""

    def post(self, item, day):
        """Save a post with one like and one comment."""
        post = ProgressPost.objects.create(actor=self.owner, title_key=f"item:{item.pk}", day=day)
        ContentLike.objects.create(user=self.follower, target_type=ContentLike.PROGRESS_POST, target_id=post.pk)
        Comment.objects.create(user=self.follower, progress_post=post, body="Nice")
        return post

    def counts(self):
        """Return count 2's line of each class."""
        lines = self.compare()
        return {name: self.line(lines, f"count 2 class {name}:") for name in "abcd"}

    def test_each_class(self):
        item = self.book("posts")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2), at="20:00")
        self.save(item, 40, d(2026, 3, 2), at="21:00")
        self.save(item, 30, d(2026, 3, 8))
        self.post(item, d(2026, 3, 2))  # a: a row that day
        self.post(item, d(2026, 3, 3))  # b: a viewer's day, the one after the row
        self.post(item, d(2026, 2, 1))  # d: rows exist, none that day
        gone = self.book("gone")
        self.post(gone, d(2026, 3, 2))  # c: nothing left
        counts = self.counts()
        for name in "abcd":
            self.assertIn("posts=1 likes=1 comments=1", counts[name])

    def test_an_emptied_day_is_class_d(self):
        item = self.book("emptied")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 5))
        self.save(item, 0, d(2026, 3, 5))
        self.post(item, d(2026, 3, 5))
        self.assertIn("posts=1", self.counts()["d"])


@override_settings(TIME_ZONE="America/New_York")
class CountFourteenTests(CompareTests):
    """Count 14: rows with no stored day, status rows per type, and Restart's flag."""

    def test_rows_with_no_day_status_rows_and_flags(self):
        item = self.book("fourteen")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 2))
        self.do(book_tracking.pause, item, d(2026, 3, 3))
        self.do(book_tracking.restart, item, d(2026, 3, 4))
        self.assertEqual(
            self.line(self.compare(), "count 14:"),
            "count 14: empty_saved_on={} status_rows={'book': 1} began_over=1",
        )
        ProgressChange.objects.filter(item=item, current_progress__kind="pages").update(saved_on=None)
        self.assertIn("empty_saved_on={'book': 1}", self.line(self.compare(), "count 14:"))

    def test_since_leaves_out_older_rows(self):
        item = self.book("since")
        self.begin(item, d(2026, 3, 1))
        ProgressChange.objects.filter(item=item).update(
            saved_on=None,
            created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        self.assertIn(
            "empty_saved_on={}",
            self.line(self.compare(since=date(2026, 4, 1).isoformat() + "T00:00:00+00:00"), "count 14:"),
        )


@override_settings(TIME_ZONE="America/New_York")
class BadRowTests(BookCalcBase):
    """It runs on production right after the deploy, over rows nothing ever checked."""

    compare = CompareTests.compare
    line = CompareTests.line

    def compare_failing(self, **options):
        """Run the command, which must end by saying a step failed; return its lines."""
        out = io.StringIO()
        with self.assertRaises(CommandError):
            call_command("compare_activity_numbers", stdout=out, **options)
        return out.getvalue().splitlines()

    def post(self, item, day):
        return ProgressPost.objects.create(actor=self.owner, title_key=f"item:{item.pk}", day=day)

    def change(self, item, value):
        return ProgressChange.objects.get(item=item, current_progress__value=value)

    def blank(self, change, side, value=None):
        """Save this row holding a null, or no value at all if `value` is missing."""
        snapshot = getattr(change, side)
        if value == "missing":
            snapshot.pop("value")
        else:
            snapshot["value"] = value
        change.save(update_fields=[side])

    def assert_every_count_reports(self, lines):
        for name in "abcd":
            self.line(lines, f"count 2 class {name}:")
        self.line(lines, "count 14:")

    def test_a_null_or_missing_value_doesnt_stop_counts_2_and_14(self):
        for side in ("current_progress", "previous_progress"):
            for value in (None, "missing"):
                with self.subTest(side=side, value=value):
                    item = self.book(f"{side}-{value}")
                    self.begin(item, d(2026, 3, 1))
                    self.save(item, 40, d(2026, 3, 5))
                    self.blank(self.change(item, 40), side, value)
                    self.post(item, d(2026, 3, 5))
                    self.assert_every_count_reports(self.compare())

    def test_a_row_holding_no_number_moves_nothing_on_its_day(self):
        """A day with another row still qualifies; one with only this row doesn't."""
        item = self.book("nulls")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 5), at="10:00")
        self.save(item, 60, d(2026, 3, 5), at="11:00")
        self.blank(self.change(item, 60), "current_progress")
        self.save(item, 70, d(2026, 3, 20))
        self.blank(self.change(item, 70), "previous_progress")
        days = _qualifying_days({self.owner.pk})[self.owner.pk, f"item:{item.pk}"]
        self.assertEqual(sorted(days), [d(2026, 3, 1), d(2026, 3, 5)])
        self.post(item, d(2026, 3, 5))
        self.post(item, d(2026, 3, 20))
        lines = self.compare()
        self.assertIn("posts=1", self.line(lines, "count 2 class a:"))
        self.assertIn("posts=1", self.line(lines, "count 2 class d:"))

    def test_its_days_are_the_feeds(self):
        """SQLite casts a null previous value to 0, so that side is a missing key."""
        item = self.book("feed")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 5))
        self.save(item, 60, d(2026, 3, 6))
        self.blank(self.change(item, 60), "current_progress")
        self.save(item, 70, d(2026, 3, 7))
        self.blank(self.change(item, 70), "previous_progress", "missing")
        changes = ProgressChange.objects.filter(actor=self.owner)
        feed = {(row["title_key"], row["day"]) for row in activity_service._progress_days(changes)}
        days = _qualifying_days({self.owner.pk})
        self.assertEqual({(key[1], day) for key, by_day in days.items() for day in by_day}, feed)

    def test_a_value_that_is_no_number_moves_nothing(self):
        item = self.book("words")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 5))
        self.blank(self.change(item, 40), "current_progress", "abc")
        days = _qualifying_days({self.owner.pk})[self.owner.pk, f"item:{item.pk}"]
        self.assertEqual(sorted(days), [d(2026, 3, 1)])

    def test_a_log_holding_a_null_or_0_counts_its_length_on_both_sides(self):
        """The old counter's own guards, which the row side now holds too."""
        for value in (None, 0):
            with self.subTest(value=value):
                DiaryEntry.objects.filter(user=self.owner).delete()
                item = self.book(f"log-{value}", pages=300)
                _, entry = self.finish(item, d(2025, 3, 2))
                DiaryEntry.objects.filter(pk=entry.pk).update(
                    progress_snapshot={"kind": "pages", "value": value, "max": 300},
                )
                lines = self.compare()
                self.assertIn("value=300->300", self.line(lines, f"user {self.owner.pk} book 2025:"))
                self.assert_every_count_reports(lines)

    def fails_for(self, user, error):
        """Return a calculation that fails for one person, as a bad row would."""
        real = activity_rows.rows_for

        def rows_for(owners, *args, **kwargs):
            if owners == [user]:
                raise error
            return real(owners, *args, **kwargs)

        return patch("api.services.activity_rows.rows_for", side_effect=rows_for)

    def test_one_accounts_bad_row_leaves_the_rest_reported(self):
        item = self.book("theirs")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 5))
        with self.fails_for(self.follower, ValueError("A Secret Title")):
            lines = self.compare_failing()
        self.assertTrue(self.line(lines, f"user {self.owner.pk} book 2026:"))
        self.assertRegex(self.line(lines, f"user {self.follower.pk}:"), r"failed ValueError at \S+:\d+$")
        self.assert_every_count_reports(lines)
        self.assertNotIn("Secret", "\n".join(lines))

    def test_a_database_error_leaves_later_queries_working(self):
        """Postgres refuses every later query until a savepoint is rolled back to."""

        def rows_for(owners, *args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SELECT no_such_column FROM no_such_table")

        self.book("after")
        with patch("api.services.activity_rows.rows_for", side_effect=rows_for):
            lines = self.compare_failing()
        self.assertIn("failed", self.line(lines, f"user {self.owner.pk}:"))
        self.assert_every_count_reports(lines)

    def test_a_value_the_database_cant_cast_leaves_the_rest_reported(self):
        """Postgres refuses the old counter's cast of it, and SQLite reads it as 0."""
        item = self.book("words", pages=300)
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 5))
        self.blank(self.change(item, 40), "current_progress", "abc")
        _, entry = self.finish(item, d(2026, 3, 9))
        DiaryEntry.objects.filter(pk=entry.pk).update(progress_snapshot={"kind": "pages", "value": "abc", "max": 300})
        out = io.StringIO()
        with contextlib.suppress(CommandError):
            call_command("compare_activity_numbers", stdout=out)
        lines = out.getvalue().splitlines()
        self.assertTrue(self.line(lines, f"user {self.owner.pk}:"))
        self.assertTrue(self.line(lines, f"user {self.follower.pk}:"))
        self.assert_every_count_reports(lines)

    def test_todays_events_failing_leaves_the_rest_of_the_account(self):
        """Postgres refuses their cast of a bad value, so they get their own step."""
        item = self.book("events")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 40, d(2026, 3, 5))
        real = StatsScope.progress_events

        def progress_events(scope):
            if scope.is_all_time and scope.owner == self.owner and scope.media_type == MediaTypes.BOOK.value:
                raise ValueError("A bad row")
            return real(scope)

        with patch.object(StatsScope, "progress_events", progress_events):
            lines = self.compare_failing()
        self.assertIn("failed ValueError", self.line(lines, f"user {self.owner.pk} book years:"))
        self.assertIn("value=", self.line(lines, f"user {self.owner.pk} book 2026:"))
        self.assertIn("failed ValueError", self.line(lines, f"user {self.owner.pk} book all:"))
        self.assertTrue(self.line(lines, f"user {self.owner.pk}: "))
        self.assertIn("value=", self.line(lines, f"user {self.follower.pk} book all:"))

    def test_one_range_failing_leaves_the_others(self):
        item = self.book("ranges")
        self.begin(item, d(2025, 3, 1))
        self.save(item, 40, d(2025, 3, 5))
        self.save(item, 80, d(2026, 3, 5))
        real = stats_time.type_time

        def reader(scope, media_type):
            if not scope.is_all_time and scope.start_date.year == 2025:
                raise ValueError("A bad row")
            return real(scope, media_type)

        with patch.object(stats_time, "type_time", reader):
            lines = self.compare_failing()
        self.assertIn("failed ValueError", self.line(lines, f"user {self.owner.pk} book 2025:"))
        self.assertIn("value=", self.line(lines, f"user {self.owner.pk} book 2026:"))
        self.assertIn("value=", self.line(lines, f"user {self.owner.pk} book all:"))

    def test_the_stats_build_failing_leaves_the_numbers(self):
        item = self.book("stats")
        self.begin(item, d(2026, 3, 1))
        with patch(
            "app.management.commands.compare_activity_numbers.build_stats_v2",
            side_effect=ValueError("A bad row"),
        ):
            lines = self.compare_failing()
        self.assertRegex(self.line(lines, f"user {self.owner.pk}:"), r"walk_ms=\d+ walk_queries=\d+ stats_ms=failed")
        self.assertIn("value=", self.line(lines, f"user {self.owner.pk} book 2026:"))

    def test_count_2_failing_leaves_count_14(self):
        with patch(
            "app.management.commands.compare_activity_numbers._qualifying_days",
            side_effect=ValueError("A bad row"),
        ):
            lines = self.compare_failing()
        self.assertIn("failed ValueError", self.line(lines, "count 2:"))
        self.assertTrue(self.line(lines, "count 14:"))

    def test_count_14_failing_is_said(self):
        with patch(
            "app.management.commands.compare_activity_numbers._per_type",
            side_effect=ValueError("A bad row"),
        ):
            lines = self.compare_failing()
        self.assertIn("failed ValueError", self.line(lines, "count 14:"))

    def test_a_since_with_no_zone_is_on_the_servers(self):
        item = self.book("zoneless")
        self.begin(item, d(2026, 3, 1))
        ProgressChange.objects.filter(item=item).update(saved_on=None)
        for since, expected in (("2026-03-01", "{'book': 1}"), ("2026-03-02", "{}")):
            with self.subTest(since=since):
                self.assertIn(f"empty_saved_on={expected}", self.line(self.compare(since=since), "count 14:"))

    def test_a_since_it_cant_read_stops_it_before_any_work(self):
        for since in ("yesterday", "2026-13-40T00:00:00"):
            with self.subTest(since=since):
                out = io.StringIO()
                with self.assertRaisesMessage(CommandError, "--since"):
                    call_command("compare_activity_numbers", stdout=out, since=since)
                self.assertEqual(out.getvalue(), "")
