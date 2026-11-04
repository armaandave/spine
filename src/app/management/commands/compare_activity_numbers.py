"""Print today's numbers beside the calculation's, for every account.

docs/activity-contract.md and AGENTS.md section 16: this is the read-only count decision
29 approved, so it prints only counts and user ids, in a transaction it rolls back, with
the Stats cache out of the way. It writes nothing to the database, though the Stats build
may store list sizes in Redis, as any Stats page does. Its row side goes through the
list's own filter function, never through the Stats reader, or it would compare a number
with itself.

It reads production's real rows right after a deploy, so a bad one costs only its own step:
that line says it failed, the rest still report, and the command exits non-zero at the end.
"""

import time
import traceback
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Count
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from api.services import activity as activity_service
from api.services import activity_rows, stats_time
from api.services import stats_v2_activity as activity
from api.services.stats_scope import StatsScope
from api.services.stats_v2 import build_stats_v2
from app import user_calendar
from social.models import Comment, ContentLike, ProgressChange, ProgressPost

# The furthest any zone is from UTC, in hours: a viewer's day on a save's moment.
ZONE_HOURS_AHEAD = 14
ZONE_HOURS_BEHIND = 12
POST_CLASSES = ("a", "b", "c", "d")


class Command(BaseCommand):
    help = "Print today's numbers beside the calculation's, per account, in a read-only transaction"

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            help="Count 14's start, an ISO moment: the preparatory deploy. Default: every row.",
        )
        parser.add_argument("--user", type=int, help="Compare one account by id.")

    def handle(self, *args, **options):
        since = _moment(options["since"])
        self.failed = 0
        with transaction.atomic():
            if connection.vendor == "postgresql":
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            try:
                self._report(options, since)
            finally:
                transaction.set_rollback(True)
        if self.failed:
            raise CommandError(f"{self.failed} steps failed, and their lines say which. The rest reported.")

    def _report(self, options, since):
        self._guarded("jit", self._jit)
        users = get_user_model().objects.order_by("pk")
        if options["user"]:
            users = users.filter(pk=options["user"])
        for user in users:
            self._guarded(f"user {user.pk}", self._account, user)
        self._guarded("count 2", self._count_2)
        self._guarded("count 14", self._count_14, since)

    def _say(self, *parts):
        self.stdout.write(" ".join(str(part) for part in parts))

    def _guarded(self, label, step, *args):
        """Run a step, so a bad row costs it and not the rest; return its result or None.

        The savepoint is what lets the rest run: after an error Postgres refuses every
        later query until one is rolled back. It says the error's type and place, never
        its message, which could quote a row.
        """
        try:
            with transaction.atomic():
                return step(*args)
        except Exception as error:  # noqa: BLE001
            frame = traceback.extract_tb(error.__traceback__)[-1]
            self._say(f"{label}: failed {type(error).__name__} at {Path(frame.filename).name}:{frame.lineno}")
            self.failed += 1
            return None

    def _jit(self):
        """Say whether Postgres plans with JIT: the speed gates ran without it."""
        if connection.vendor != "postgresql":
            self._say("jit: not postgres")
            return
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_jit_available(), current_setting('jit')")
            available, setting = cursor.fetchone()
        self._say(f"jit: available={available} setting={setting}")

    def _account(self, user):
        started = time.perf_counter()
        with CaptureQueriesContext(connection) as queries:
            calculation = activity_rows.rows_for([user], user, types=activity_rows.ADAPTERS)
        walk_ms = (time.perf_counter() - started) * 1000
        scope = StatsScope(owner=user, viewer=user)
        stats_ms = self._guarded(f"user {user.pk} stats", _stats_ms, scope)
        stats = "failed" if stats_ms is None else f"{stats_ms:.0f}"
        self._say(f"user {user.pk}: walk_ms={walk_ms:.0f} walk_queries={len(queries)} stats_ms={stats}")
        for media_type in activity_rows.ADAPTERS:
            self._type(user, scope.with_media_type(media_type), media_type, calculation)

    def _type(self, user, scope, media_type, calculation):
        rows = [row for row in calculation.rows if row.bucket == media_type]
        logs = scope.all_entries.values_list("consumed_at", flat=True)
        zone = user_calendar.calendar_tz(user)
        years = {row.day.year for row in rows} | {activity_rows.log_day(moment, zone).year for moment in logs}
        # A year today's code counts and the rows leave empty would lose its pill at the switch.
        years |= self._guarded(f"user {user.pk} {media_type} years", _event_years, scope) or set()
        for year in sorted(years):
            ranged = scope._sibling(start_date=date(year, 1, 1), end_date=date(year, 12, 31))
            self._guarded(f"user {user.pk} {media_type} {year}", self._range, user, ranged, media_type, str(year), calculation)
        self._guarded(f"user {user.pk} {media_type} all", self._range, user, scope, media_type, "all", calculation)

    def _range(self, user, scope, media_type, label, calculation):
        """Print one type and range: each number today, and what the list gives."""

        def listed(counts=None):
            return activity_rows.list_rows(
                calculation.rows,
                media_type=media_type,
                start_date=scope.start_date,
                end_date=scope.end_date,
                counts=counts,
            )

        today = stats_time._COUNTERS[media_type](scope, media_type)
        pile = activity_rows.piled(calculation.piles if scope.is_all_time else [], media_type)
        titles = listed(activity_rows.TITLES)
        title_ids = {row.title_id for row in titles} | pile.titles
        unknown = {row.title_id for row in listed() if row.counts_title and not row.known} | (pile.titles - pile.known)
        days = sorted({row.day for row in listed(activity_rows.DAYS)})
        today_days = sorted({day for day, _title, _bucket in scope.events})
        week_start = scope.week_starts_on
        pairs = {
            "value": (today.value, int(sum((row.amount for row in listed()), pile.amount))),
            "known": (today.known, len(title_ids - unknown)),
            "total": (today.total, len(title_ids)),
            "titles": (scope.titles.count(), len(title_ids)),
            "active_days": (len(today_days), len(days)),
            "longest_streak": (
                activity.longest_daily_streak_span(today_days).length,
                activity.longest_daily_streak_span(days).length,
            ),
            "longest_weekly_streak": (
                activity.longest_weekly_streak_span(today_days, week_start).length,
                activity.longest_weekly_streak_span(days, week_start).length,
            ),
            "days_with_two_plus_titles": (
                activity.days_with_two_plus_titles(scope.events),
                len({row.day for row in listed(activity_rows.MULTI_TITLE_DAYS)}),
            ),
            "pile": (None, int(pile.amount)),
        }
        fields = " ".join(f"{name}={old}->{new}" for name, (old, new) in pairs.items())
        self._say(f"user {user.pk} {media_type} {label}: {fields}")

    def _count_2(self):
        """Count 2: progress posts, their likes and comments, by class (plan 5.8)."""
        posts = list(ProgressPost.objects.order_by("id"))
        likes = dict(
            ContentLike.objects.filter(target_type=ContentLike.PROGRESS_POST)
            .values("target_id")
            .annotate(count=Count("id"))
            .values_list("target_id", "count"),
        )
        comments = dict(
            Comment.objects.filter(progress_post__isnull=False)
            .order_by()
            .values("progress_post_id")
            .annotate(count=Count("id"))
            .values_list("progress_post_id", "count"),
        )
        totals = {name: Counter() for name in POST_CLASSES}
        days = _qualifying_days({post.actor_id for post in posts})
        for post in posts:
            name = _post_class(post, days.get((post.actor_id, post.title_key)))
            totals[name]["posts"] += 1
            totals[name]["likes"] += likes.get(post.pk, 0)
            totals[name]["comments"] += comments.get(post.pk, 0)
        for name in POST_CLASSES:
            total = totals[name]
            self._say(
                f"count 2 class {name}: posts={total['posts']} likes={total['likes']} comments={total['comments']}",
            )

    def _count_14(self, since):
        """Count 14: a missed writer fails silently, so count rows with no stored day."""
        rows = ProgressChange.objects.order_by()
        if since:
            rows = rows.filter(created_at__gte=since)
        empty = _per_type(rows.filter(saved_on__isnull=True))
        status = _per_type(rows.filter(current_progress__kind=ProgressChange.STATUS))
        began_over = rows.filter(current_progress__has_key="began_over").count()
        self._say(f"count 14: empty_saved_on={empty} status_rows={status} began_over={began_over}")


def _moment(value):
    """Return --since as a moment, saying so before any work if it can't be read as one."""
    if not value:
        return None
    try:
        moment = parse_datetime(value)
    except ValueError:
        moment = None
    if moment is None:
        raise CommandError("--since takes an ISO moment, such as 2026-11-04T12:00:00-05:00.")
    return timezone.make_aware(moment) if timezone.is_naive(moment) else moment


def _event_years(scope):
    """Return the years today's code counts an active day in."""
    return {day.year for day, _title, _bucket in scope.events}


def _stats_ms(scope):
    """Return how long the Stats build takes, in milliseconds."""
    started = time.perf_counter()
    build_stats_v2(scope)
    return (time.perf_counter() - started) * 1000


def _per_type(rows):
    """Return a queryset's row counts by Item type, as a plain sorted dict."""
    counts = rows.values("item__media_type").annotate(count=Count("id")).values_list("item__media_type", "count")
    return dict(sorted(counts))


def _moved(change):
    """Return how far a change moved. With no number on either side it's 0, as the feed sums it."""
    current = activity_rows.saved_number(change.current_progress.get("value"))
    previous = activity_rows.saved_number(change.previous_progress.get("value"))
    return 0 if current is None or previous is None else current - previous


def _qualifying_days(actor_ids):
    """Return, per person and title, each day with a row, and when its rows were saved.

    A day qualifies as the feed's does: some counted unit moved forward on net. A title
    with change rows and no qualifying day holds an empty dict.
    """
    actors = {user.pk: user for user in get_user_model().objects.filter(pk__in=actor_ids)}
    nets = defaultdict(lambda: defaultdict(Counter))
    moments = defaultdict(lambda: defaultdict(list))
    changes = ProgressChange.objects.filter(actor__in=actor_ids).select_related("item")
    for change in changes.order_by("id"):
        key = (change.actor_id, activity_service._title_key(change.item))
        day = activity_rows.saved_day(change, user_calendar.calendar_tz(actors[change.actor_id]))
        unit = change.current_progress.get("unit")
        nets[key][day][unit] += _moved(change)
        moments[key][day].append(change.created_at)
    return {
        key: {
            day: moments[key][day]
            for day, units in by_day.items()
            if any(units[unit] > 0 for unit in activity_service.UNITS)
        }
        for key, by_day in nets.items()
    }


def _post_class(post, days):
    """Return a post's class: a has a row, b a viewer's day, c nothing left, d an empty day."""
    if days is None:
        return "c"
    if post.day in days:
        return "a"
    adjacent = [day for day in days if abs((day - post.day).days) == 1]
    if len(adjacent) == 1 and any(_can_read_as(moment, post.day) for moment in days[adjacent[0]]):
        return "b"
    return "d"


def _can_read_as(moment, day):
    """Return whether this moment could read as this day in some zone."""
    earliest = (moment - timedelta(hours=ZONE_HOURS_BEHIND)).date()
    latest = (moment + timedelta(hours=ZONE_HOURS_AHEAD)).date()
    return earliest <= day <= latest
