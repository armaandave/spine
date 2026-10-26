"""Stats v2 header, time, activity and milestones (docs/stats-contract.md).

STAT-200 (header), STAT-201 and STAT-202 (time, counted in api.services.stats_time),
STAT-203 (activity) and STAT-216 (milestones), with rewatches under STAT-108. Every
function takes an api.services.stats_scope.StatsScope and returns its section's shape;
a section with nothing yet is empty, never null (STAT-302).

Events (STAT-106) come from ``scope.events``: distinct (day, title, type bucket)
tuples, so active days, streaks, "Days with 2+ titles" and the activity charts all
count the same thing.
"""

from collections import Counter, defaultdict
from datetime import timedelta
from itertools import pairwise

from django.db.models import (
    BooleanField,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    OuterRef,
    Q,
    Subquery,
    Window,
)
from django.db.models.functions import RowNumber

from api.services import filters as filter_service
from api.services import stats_time
from api.services.stats_scope import day_string, media_type_bucket, union_ids
from app import exposure
from app.models import (
    TV,
    Book,
    BookSession,
    Episode,
    Game,
    Item,
    MediaLike,
    MediaTypes,
    Movie,
    Music,
    Season,
    Status,
)

DAY = "day"
WEEK = "week"
REWATCH_LABELS = {
    None: "Repeats",
    MediaTypes.MOVIE.value: "Rewatches",
    MediaTypes.TV.value: "Rewatches",
    MediaTypes.ANIME.value: "Rewatches",
    MediaTypes.BOOK.value: "Rereads",
    MediaTypes.MANGA.value: "Rereads",
    MediaTypes.COMIC.value: "Rereads",
    MediaTypes.GAME.value: "Replays",
    MediaTypes.MUSIC.value: "Relistens",
}
FIXED_MILESTONES = (1, 100, 250, 500, 750, 1000)
MILESTONE_STEP = 500


# The header, STAT-200.


def build_header(scope):
    """Build the four big numbers and the details list (STAT-200)."""
    days = sorted({day for day, _title, _bucket in scope.events})
    counts = _log_counts(scope)
    details = []
    if scope.includes_today:
        details.append(_detail("current_daily_streak", "Current daily streak", current_daily_streak(days, scope.today), DAY))
    week_start = scope.week_starts_on
    details.append(_detail("longest_weekly_streak", "Longest weekly streak", longest_weekly_streak(days, week_start), WEEK))
    if scope.includes_today:
        details.append(
            _detail(
                "current_weekly_streak",
                "Current weekly streak",
                current_weekly_streak(days, scope.today, week_start),
                WEEK,
            ),
        )
    details.extend(
        [
            _detail("rewatches", REWATCH_LABELS[scope.media_type], counts["rewatches"]),
            _detail("reviews", "Reviews", counts["reviews"]),
            _detail("likes", "Likes", liked_titles(scope)),
            _detail("days_with_two_plus_titles", "Days with 2+ titles", days_with_two_plus_titles(scope.events)),
        ],
    )
    details.extend(_type_details(scope))
    return {
        "titles": scope.titles.count(),
        "logs": counts["logs"],
        "active_days": len(days),
        "longest_daily_streak": longest_daily_streak(days),
        "details": details,
    }


def _detail(key, label, value, unit=None, *, approximate=False):
    return {"key": key, "label": label, "value": value, "unit": unit, "approximate": approximate}


def _log_counts(scope):
    """Count logs, reviews and rewatches in the range in one query (STAT-103, 108)."""
    entries, rewatch = rewatch_entries(scope)
    # The rewatch test is annotated first: as an aggregate's filter, Django resolves
    # its OuterRefs against the wrong query and counts every log.
    return entries.annotate(
        stats_rewatch=ExpressionWrapper(rewatch, output_field=BooleanField()),
    ).aggregate(
        logs=Count("id"),
        reviews=Count("id", filter=Q(review__gt="") | Q(review_title__gt="")),
        rewatches=Count("id", filter=Q(stats_rewatch=True)),
    )


def liked_titles(scope):
    """Count titles with a liked visible log in range; All Time adds MediaLike."""
    liked = [scope.entries.filter(liked=True).annotate(stats_title=scope.title_id()).values("stats_title")]
    if scope.is_all_time:
        likes = MediaLike.objects.filter(scope.tab_q(), user=scope.owner)
        liked.append(likes.annotate(stats_title=scope.title_id()).values("stats_title"))
    return Item.objects.filter(id__in=union_ids(*liked)).count()


def _type_details(scope):
    """Return the tab's own facts, after the shared rows (STAT-200)."""
    media_type = scope.media_type
    if media_type == MediaTypes.TV.value:
        details = [_detail("episodes", "Episodes watched", scope.viewings.count())]
        if scope.is_all_time:
            seasons = Season.objects.filter(user=scope.owner, status=Status.COMPLETED.value)
            shows = TV.objects.filter(user=scope.owner, finished=True)
            details.append(_detail("seasons_finished", "Seasons finished", _distinct_items(seasons)))
            details.append(_detail("shows_finished", "Shows finished", _distinct_items(shows)))
        return details
    if media_type == MediaTypes.ANIME.value:
        anime = stats_time.type_time(scope, media_type)
        return [_detail("episodes", "Episodes watched", anime.progress, approximate=anime.approximate)]
    if media_type == MediaTypes.BOOK.value:
        return [
            _detail("reads", "Reads", _reads_or_completions(scope, Book)),
            _detail("did_not_finish", "Did not finish", _dropped_book_journeys(scope)),
        ]
    if media_type == MediaTypes.GAME.value:
        return [
            _detail("completions", "Completions", _reads_or_completions(scope, Game)),
            _detail("abandoned", "Abandoned", _dropped_playthroughs(scope)),
        ]
    return []


def _distinct_items(rows):
    return rows.values("item_id").distinct().count()


def _reads_or_completions(scope, model):
    """Visible logs in the range, plus each manual completion in All Time (STAT-107)."""
    count = scope.entries.count()
    if scope.is_all_time:
        count += _distinct_items(stats_time.manual_completions(scope, model))
    return count


def _dropped_book_journeys(scope):
    """Count reading journeys that ended Dropped in the range; all in All Time."""
    journeys = BookSession.objects.filter(
        related_book__user=scope.owner,
        status=Status.DROPPED.value,
    )
    if not scope.is_all_time:
        # End dates are UTC-midnight date carriers (book_tracking), read per row.
        journeys = journeys.filter(
            filter_service.log_day_range_q(scope.owner, scope.start_date, scope.end_date, "end_date"),
        )
    return journeys.count()


def _dropped_playthroughs(scope):
    """Playthroughs that ended Dropped in the range; every one in All Time."""
    sessions = stats_time.scope_game_sessions(scope).filter(status=Status.DROPPED.value)
    if not scope.is_all_time:
        sessions = sessions.filter(end_date__gte=scope.start_date, end_date__lte=scope.end_date)
    return sessions.count()


# Rewatches, STAT-108.


def rewatch_entries(scope):
    """Return (the scope's logs, annotated, and a Q matching the rewatches among them).

    A log is a rewatch when its target has an earlier visible log (by log day, then
    ID), or, as its first visible log, when an undated fact came first under its
    type's rule. The target is the logged Item: an episode, season or show for TV.
    """
    owner = scope.owner
    types = set(scope.item_types)
    entries = scope.entries.annotate(stats_day=scope.log_day())
    earlier = (
        scope.visible_entries.filter(item_id=OuterRef("item_id"))
        .annotate(stats_earlier_day=scope.log_day())
        .filter(
            Q(stats_earlier_day__lt=OuterRef("stats_day"))
            | Q(stats_earlier_day=OuterRef("stats_day"), id__lt=OuterRef("id")),
        )
    )
    rewatch = Q(Exists(earlier))

    for media_type, model in ((MediaTypes.BOOK.value, Book), (MediaTypes.GAME.value, Game)):
        if media_type in types:
            manual = stats_time.manual_completions(scope, model).filter(item_id=OuterRef("item_id"))
            rewatch |= Q(item__media_type=media_type) & Q(Exists(manual))

    for media_type, model in ((MediaTypes.MOVIE.value, Movie), (MediaTypes.MUSIC.value, Music)):
        if media_type in types:
            marker = model.objects.filter(user=owner, item_id=OuterRef("item_id"), direct_consumption=True)
            rewatch |= Q(item__media_type=media_type, is_rewatch=True) & Q(Exists(marker))

    if MediaTypes.TV.value in types:
        mark_fills = Episode.objects.filter(
            related_season__user=owner,
            kind=Episode.Kind.FILL,
            diary_entry__isnull=True,
            created_at__lt=OuterRef("created_at"),
        )
        season_fills = mark_fills.filter(related_season__item_id=OuterRef("item_id"))
        show_fills = mark_fills.filter(related_season__related_tv__item_id=OuterRef("item_id"))
        rewatch |= Q(item__media_type=MediaTypes.SEASON.value, is_rewatch=True) & Q(Exists(season_fills))
        rewatch |= Q(item__media_type=MediaTypes.TV.value, is_rewatch=True) & Q(Exists(show_fills))

        own_viewing = Episode.objects.filter(
            diary_entry_id=OuterRef("id"),
            item_id=OuterRef("item_id"),
        ).order_by("created_at", "id")
        entries = entries.annotate(
            stats_own_viewing_at=Subquery(own_viewing.values("created_at")[:1]),
            stats_own_viewing_id=Subquery(own_viewing.values("id")[:1]),
        )
        earlier_viewing = Episode.objects.filter(
            Q(created_at__lt=OuterRef("stats_own_viewing_at"))
            | Q(created_at=OuterRef("stats_own_viewing_at"), id__lt=OuterRef("stats_own_viewing_id")),
            related_season__user=owner,
            item_id=OuterRef("item_id"),
        ).exclude(diary_entry_id=OuterRef("id"))
        if not scope.is_owner:
            # A viewing of a log the viewer can't see counts for nothing (STAT-003).
            earlier_viewing = earlier_viewing.filter(
                Q(diary_entry__isnull=True) | Q(diary_entry_id__in=scope.visible_entries.values("id")),
            )
        rewatch |= Q(item__media_type=MediaTypes.EPISODE.value) & Q(Exists(earlier_viewing))
    return entries, rewatch


# Streaks, STAT-200.


def longest_daily_streak(days):
    """Return the most days in a row among sorted event days."""
    return _longest_run(days, timedelta(days=1))


def current_daily_streak(days, today):
    """Days in a row back from today, or from yesterday when today has none."""
    return _current_run(set(days), today, timedelta(days=1))


def week_start(day, week_starts_on):
    """Return the first day of the day's week, weeks starting Monday or Sunday."""
    offset = (day.weekday() + 1) % 7 if week_starts_on == "sunday" else day.weekday()
    return day - timedelta(days=offset)


def longest_weekly_streak(days, week_starts_on):
    """Return the most weeks in a row with an event."""
    weeks = sorted({week_start(day, week_starts_on) for day in days})
    return _longest_run(weeks, timedelta(weeks=1))


def current_weekly_streak(days, today, week_starts_on):
    """Weeks in a row back from this week, or from last week when this week has none."""
    weeks = {week_start(day, week_starts_on) for day in days}
    return _current_run(weeks, week_start(today, week_starts_on), timedelta(weeks=1))


def _longest_run(ordered, step):
    if not ordered:
        return 0
    longest = running = 1
    for previous, current in pairwise(ordered):
        running = running + 1 if current - previous == step else 1
        longest = max(longest, running)
    return longest


def _current_run(present, reference, step):
    cursor = reference if reference in present else reference - step
    run = 0
    while cursor in present:
        run += 1
        cursor -= step
    return run


def days_with_two_plus_titles(events):
    """Days with events for two or more titles."""
    titles = Counter(day for day, _title, _bucket in events)
    return sum(1 for count in titles.values() if count >= 2)


# Time, STAT-201 and STAT-202.


def build_time(scope):
    """Build time per type, plus total hours on Global (STAT-201, STAT-202)."""
    rows = stats_time.time_rows(scope)
    return {
        "rows": [row.payload() for row in rows],
        "hours_total": stats_time.hours_total(rows) if scope.media_type is None else None,
    }


# Activity, STAT-203.


def build_activity(scope):
    """Build events by month and weekday, busiest day and month (STAT-203)."""
    events = scope.events
    by_month = defaultdict(Counter)
    by_day = Counter()
    weekdays = [0] * 7
    for day, _title, bucket in events:
        by_month[(day.year, day.month)][bucket] += 1
        by_day[day] += 1
        weekdays[day.weekday()] += 1

    if scope.calendar_year is not None:
        months = [(scope.calendar_year, month) for month in range(1, 13)]
    elif by_month:
        months = _month_span(min(by_month), max(by_month))
    else:
        months = []
    order = exposure.primary_media_types()
    month_rows = []
    for year, month in months:
        counts = by_month.get((year, month), Counter())
        month_rows.append(
            {
                "month": f"{year:04d}-{month:02d}",
                "count": sum(counts.values()),
                "by_type": {media_type: counts[media_type] for media_type in order if counts[media_type]},
            },
        )

    busiest_day = None
    if by_day:
        # Most events; a tie goes to the later day.
        day = max(by_day, key=lambda value: (by_day[value], value))
        busiest_day = {"date": day_string(day), "count": by_day[day]}
    busiest_month = None
    if by_month:
        key = max(by_month, key=lambda value: (sum(by_month[value].values()), value))
        busiest_month = {"month": f"{key[0]:04d}-{key[1]:02d}", "count": sum(by_month[key].values())}
    return {
        "months": month_rows,
        "weekdays": weekdays,
        "week_starts_on": scope.week_starts_on,
        "busiest_day": busiest_day,
        "busiest_month": busiest_month,
    }


def _month_span(first, last):
    months = []
    year, month = first
    while (year, month) <= last:
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


# Milestones, STAT-216.


def milestone_numbers(count):
    """1, 100, 250, 500, 750 and 1,000, then every 500th, up to ``count``."""
    numbers = [number for number in FIXED_MILESTONES if number <= count]
    numbers.extend(range(FIXED_MILESTONES[-1] + MILESTONE_STEP, count + 1, MILESTONE_STEP))
    return numbers


def build_milestones(scope):
    """Build the 1st, 100th, 250th, 500th, 750th, 1,000th, then every 500th log.

    STAT-216: visible logs of the tab are numbered over all time by log day, then
    ID; a range shows the milestones whose log falls in it.
    """
    history = scope.all_time()
    logs = history.all_entries
    numbers = milestone_numbers(logs.count())
    if not numbers:
        return []
    rows = list(
        logs.annotate(stats_day=history.log_day())
        .annotate(
            stats_number=Window(RowNumber(), order_by=[F("stats_day").asc(), F("id").asc()]),
            stats_title=history.title_id(),
            stats_bucket=media_type_bucket("item__media_type"),
        )
        .filter(stats_number__in=numbers)
        .values_list("stats_number", "stats_day", "id", "stats_bucket", "stats_title")
        .order_by("stats_number"),
    )
    if not scope.is_all_time:
        rows = [row for row in rows if scope.start_date <= row[1] <= scope.end_date]
    summaries = scope.media_summaries([row[4] for row in rows])
    return [
        {
            "number": number,
            "day": day_string(day),
            "entry_id": entry_id,
            "media_type": bucket,
            "media": summaries.get(title_id),
        }
        for number, day, entry_id, bucket, title_id in rows
    ]

