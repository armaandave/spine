"""Time per type in that type's own unit, and total hours (docs/stats-contract.md).

STAT-201 and STAT-202. Every function reads an api.services.stats_scope.StatsScope and
aggregates in SQL: one or two queries per type, whatever the size of the library.

- Movies and music: the Item's runtime over visible logs in the range, plus each
  undated completion once in All Time.
- TV: viewings in the range (dated ones; every one, fills included, in All Time), each
  the episode's runtime, else its season's, else its show's.
- Anime, manga and comics: in a dated range, positive progress changes between
  consecutive history rows of one tracking row, dated by ``history_date`` on the
  owner's calendar; the row that created the tracking row is never a change. In All
  Time, ``progress`` over every tracking row. Anime multiplies by its runtime.
- Games: ``GameSession.total_minutes`` on ``minutes_updated_on``, else ``end_date``.
  Steam's imported lifetime playtime is never read (GM-500).
- Books: pages over visible book logs (the completion snapshot's pages, else the
  book's ``total_pages``), plus ``total_pages`` once per ``completed_manually`` book in
  All Time.

``known`` and ``total`` count the things counted (logs, viewings, tracking rows with a
change or with progress, sessions, undated completions) with a length, out of all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from django.apps import apps
from django.db.models import (
    Case,
    Count,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Coalesce, NullIf

from app import exposure
from app.models import Book, Game, GameSession, Item, MediaTypes

MINUTES = "minutes"
UNITS = {
    MediaTypes.MOVIE.value: MINUTES,
    MediaTypes.TV.value: MINUTES,
    MediaTypes.ANIME.value: MINUTES,
    MediaTypes.MANGA.value: "chapters",
    MediaTypes.GAME.value: MINUTES,
    MediaTypes.BOOK.value: "pages",
    MediaTypes.COMIC.value: "issues",
    MediaTypes.MUSIC.value: MINUTES,
}
# Types counted in minutes, which add up to the total hours (STAT-202).
HOURS_TYPES = tuple(media_type for media_type, unit in UNITS.items() if unit == MINUTES)
PROGRESS_TYPES = (MediaTypes.ANIME.value, MediaTypes.MANGA.value, MediaTypes.COMIC.value)
LOG_RUNTIME_TYPES = (MediaTypes.MOVIE.value, MediaTypes.MUSIC.value)

_CACHE_KEY = "_stats_time_cache"


@dataclass
class TypeTime:
    """One type's time in its own unit (STAT-201)."""

    media_type: str
    value: int = 0
    known: int = 0
    total: int = 0
    approximate: bool = False
    # Anime episodes, manga chapters or comic issues counted, before any runtime.
    progress: int = 0

    @property
    def unit(self):
        return UNITS[self.media_type]

    def payload(self):
        return {
            "media_type": self.media_type,
            "unit": self.unit,
            "value": self.value,
            "known": self.known,
            "total": self.total,
            "approximate": self.approximate,
        }


def type_time(scope, media_type):
    """Return one type's TypeTime in the scope's range, computed once per scope."""
    cache = scope.__dict__.setdefault(_CACHE_KEY, {})
    if media_type not in cache:
        cache[media_type] = _COUNTERS[media_type](scope, media_type)
    return cache[media_type]


def time_rows(scope):
    """Return the TypeTime of each type the scope covers with something counted."""
    rows = (type_time(scope, media_type) for media_type in scope.primary_types)
    return [row for row in rows if row.total > 0]


def hours_total(rows):
    """Add up the minutes of the types counted in minutes (STAT-202).

    ``media_types`` and ``leaves_out`` depend only on what the server exposes.
    """
    exposed = exposure.primary_media_types()
    parts = [row for row in rows if row.media_type in HOURS_TYPES]
    return {
        "minutes": sum(row.value for row in parts),
        "media_types": [media_type for media_type in exposed if media_type in HOURS_TYPES],
        "leaves_out": [media_type for media_type in exposed if media_type not in HOURS_TYPES],
        "approximate": any(row.approximate for row in parts),
    }


# Counters, one per type.


def _log_runtime(scope, media_type):
    """Count movies and music: runtime per visible log, plus undated completions."""
    result = TypeTime(media_type)
    _add(result, _length_totals(scope.entries.filter(item__media_type=media_type), "item__runtime_minutes"))
    if scope.is_all_time:
        undated = scope.with_media_type(media_type).undated_completion_item_ids()
        if undated:
            _add(result, _length_totals(Item.objects.filter(id__in=undated[0]), "runtime_minutes"))
    return result


def _tv_runtime(scope, media_type):
    """Count TV viewings: the episode's runtime, else its season's, else its show's."""
    result = TypeTime(media_type)
    own = _positive("item__runtime_minutes")
    runtime = Coalesce(
        own,
        _positive("related_season__item__runtime_minutes"),
        _positive("related_season__related_tv__item__runtime_minutes"),
    )
    totals = scope.viewings.annotate(stats_runtime=runtime, stats_own=own).aggregate(
        value=Sum("stats_runtime"),
        known=Count("stats_runtime"),
        total=Count("id"),
        stood_in=Count("id", filter=Q(stats_own__isnull=True, stats_runtime__isnull=False)),
    )
    _add(result, totals)
    result.approximate = bool(totals["stood_in"])
    return result


def _progress(scope, media_type):
    """Count anime, manga and comics: progress changes, or all progress in All Time."""
    result = TypeTime(media_type)
    model = apps.get_model("app", media_type)
    counts_minutes = media_type == MediaTypes.ANIME.value
    if scope.is_all_time:
        rows = model.objects.filter(user=scope.owner, progress__gt=0).annotate(
            stats_change=F("progress"),
            stats_runtime=_positive("item__runtime_minutes"),
        )
    else:
        rows = _progress_changes(scope, model)
    totals = rows.aggregate(
        progress=Sum("stats_change"),
        minutes=Sum(
            ExpressionWrapper(F("stats_change") * F("stats_runtime"), output_field=IntegerField()),
        ),
        total=Count("id", distinct=True),
        known=Count("id", distinct=True, filter=Q(stats_runtime__isnull=False)),
    )
    result.progress = totals["progress"] or 0
    result.total = totals["total"] or 0
    if counts_minutes:
        result.value = totals["minutes"] or 0
        result.known = totals["known"] or 0
    else:
        # A chapter or issue count needs no length: everything counted is known.
        result.value = result.progress
        result.known = result.total
    result.approximate = not scope.is_all_time
    return result


def _progress_changes(scope, model):
    """History rows in the range that raised a tracking row's progress, with the rise.

    The previous row is the same tracking row's history row before it, by
    ``history_date`` then history ID, wherever it falls. A row with no previous row
    created the tracking row and is never a change; drops are ignored.
    """
    history = model.history.model
    start, before = moment_bounds(scope)
    previous = (
        history.objects.filter(id=OuterRef("id"))
        .filter(
            Q(history_date__lt=OuterRef("history_date"))
            | Q(history_date=OuterRef("history_date"), history_id__lt=OuterRef("history_id")),
        )
        .order_by("-history_date", "-history_id")
        .values("progress")[:1]
    )
    runtime = model.objects.filter(pk=OuterRef("id")).values("item__runtime_minutes")[:1]
    return (
        history.objects.filter(
            id__in=model.objects.filter(user=scope.owner).values("id"),
            history_date__gte=start,
            history_date__lt=before,
        )
        .exclude(history_type="-")
        .annotate(stats_previous=Subquery(previous, output_field=IntegerField()))
        .annotate(
            stats_change=ExpressionWrapper(
                F("progress") - F("stats_previous"),
                output_field=IntegerField(),
            ),
        )
        .filter(stats_previous__isnull=False, stats_change__gt=0)
        .annotate(stats_runtime=NullIf(Subquery(runtime, output_field=IntegerField()), Value(0)))
    )


def _game_minutes(scope, media_type):
    """Games: session minutes on ``minutes_updated_on``, else ``end_date``."""
    result = TypeTime(media_type)
    sessions = scope_game_sessions(scope)
    if not scope.is_all_time:
        sessions = sessions.annotate(
            stats_day=Coalesce("minutes_updated_on", "end_date"),
        ).filter(stats_day__gte=scope.start_date, stats_day__lte=scope.end_date)
    _add(result, _length_totals(sessions, "total_minutes", positive=False))
    result.approximate = not scope.is_all_time
    return result


def _book_pages(scope, media_type):
    """Count book pages per visible log, plus each manual completion in All Time."""
    result = TypeTime(media_type)
    snapshot_pages = Case(
        When(
            progress_snapshot__kind="pages",
            progress_snapshot__value__isnull=False,
            then=Cast(KT("progress_snapshot__value"), IntegerField()),
        ),
        default=Value(None, output_field=IntegerField()),
        output_field=IntegerField(),
    )
    pages = Coalesce(
        NullIf(snapshot_pages, Value(0)),
        _positive("item__total_pages"),
        output_field=IntegerField(),
    )
    logs = scope.entries.filter(item__media_type=media_type).annotate(stats_length=pages)
    _add(
        result,
        logs.aggregate(value=Sum("stats_length"), known=Count("stats_length"), total=Count("id")),
    )
    if scope.is_all_time:
        manual = Item.objects.filter(id__in=manual_completions(scope, Book).values("item_id"))
        _add(result, _length_totals(manual, "total_pages"))
    return result


_COUNTERS = {
    MediaTypes.MOVIE.value: _log_runtime,
    MediaTypes.MUSIC.value: _log_runtime,
    MediaTypes.TV.value: _tv_runtime,
    MediaTypes.ANIME.value: _progress,
    MediaTypes.MANGA.value: _progress,
    MediaTypes.COMIC.value: _progress,
    MediaTypes.GAME.value: _game_minutes,
    MediaTypes.BOOK.value: _book_pages,
}


# Shared helpers, also used by the header (api.services.stats_v2_activity).


def moment_bounds(scope):
    """Return the range as [start, before) datetimes on the owner's calendar."""
    start = datetime.combine(scope.start_date, time.min, tzinfo=scope.zone)
    before = datetime.combine(scope.end_date + timedelta(days=1), time.min, tzinfo=scope.zone)
    return start, before


def scope_game_sessions(scope):
    """Return the owner's playthroughs of current (not legacy-archived) game rows."""
    return GameSession.objects.filter(
        related_game__user=scope.owner,
        related_game__legacy_archived=False,
    ).order_by()


def manual_completions(scope, model):
    """Return the owner's book or game rows completed with no date (BK-405, GM-000)."""
    rows = model.objects.filter(user=scope.owner, completed_manually=True)
    if model is Game:
        rows = rows.filter(legacy_archived=False)
    return rows.order_by()


def _positive(field):
    """Return a length that is unknown when missing or zero."""
    return NullIf(F(field), Value(0), output_field=IntegerField())


def _length_totals(queryset, field, *, positive=True):
    length = _positive(field) if positive else F(field)
    return queryset.annotate(stats_length=length).aggregate(
        value=Sum("stats_length"),
        known=Count("stats_length"),
        total=Count("id"),
    )


def _add(result, totals):
    result.value += totals["value"] or 0
    result.known += totals["known"] or 0
    result.total += totals["total"] or 0
