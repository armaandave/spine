"""compare_activity_numbers: today's number beside the calculation's, for every account.

It's the read-only count decision 29 approved. It prints counts and user ids, writes
nothing and calls no provider. It holds counts 2 and 14, so they run in CI.
"""

import io
import re
from datetime import UTC, date, datetime
from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from api.tests.test_activity_calc_books import BookCalcBase, d
from app import book_tracking, game_tracking
from app.models import Anime, GameSession, MediaTypes, Status
from social.models import Comment, ContentLike, ProgressChange, ProgressPost

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
        """Read with no finish: today's Books count nothing, and the rows count 285."""
        self.reading = self.book("reading", pages=500)
        self.begin(self.reading, d(2026, 3, 1))
        self.save(self.reading, 285, d(2026, 3, 2))

    def test_one_known_difference(self):
        self.seed_a_known_difference()
        lines = self.compare()
        line = self.line(lines, f"user {self.owner.pk} book 2026:")
        self.assertIn("value=0->285", line)
        self.assertIn("known=0->1", line)
        self.assertIn("total=0->1", line)
        # Books are switched on, so today's titles and days read the rows already.
        self.assertIn("titles=1->1", line)
        self.assertIn("active_days=2->2", line)
        everything = self.line(lines, f"user {self.owner.pk} book all:")
        self.assertIn("value=0->285", everything)
        self.assertIn("pile=None->0", everything)

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
class YearsOnlyTheOldCounterHasTests(CompareTests):
    """A mark leaving a year moves its amount to the pile: All Time can't show that."""

    def test_an_anime_marked_in_2025(self):
        item = self.item(MediaTypes.ANIME.value, "marked", title="marked")
        self.set_item(item, runtime_minutes=24)
        row = self.tracking(Anime, item, status=Status.COMPLETED.value, progress=12)
        self.history(row, 0, datetime(2025, 3, 1, 17, tzinfo=UTC), kind="+")
        self.history(row, 12, datetime(2025, 3, 2, 17, tzinfo=UTC))
        type(row).history.model.objects.filter(id=row.id).update(history_change_reason="mark")
        line = self.line(self.compare(), f"user {self.owner.pk} anime 2025:")
        self.assertIn("value=288->0", line)

    def test_a_game_whose_hours_read_a_year_its_rows_left(self):
        item = self.item(MediaTypes.GAME.value, "moved", title="moved")
        with self.on(d(2026, 2, 10)):
            game_tracking.start(self.owner, item)
            game_tracking.update_progress(self.owner, item, total_minutes=3000)
        GameSession.objects.filter(related_game__item=item).update(minutes_updated_on=d(2025, 12, 20))
        line = self.line(self.compare(), f"user {self.owner.pk} game 2025:")
        self.assertIn("value=3000->0", line)

    def test_a_year_holding_nothing_either_way_prints_nothing(self):
        self.seed_a_known_difference()
        lines = self.compare()
        self.assertFalse([line for line in lines if line.startswith(f"user {self.owner.pk} book 2025:")])
        self.assertFalse([line for line in lines if line.startswith(f"user {self.owner.pk} anime 2")])


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
