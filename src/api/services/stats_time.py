"""Time per type in that type's own unit, and total hours (docs/stats-contract.md).

STAT-201 and STAT-202. Every function reads an api.services.stats_scope.StatsScope and
aggregates in SQL: one or two queries per type, whatever the size of the library.

- Every type: the calculation's rows (api.services.activity_rows), plus its pile in All
  Time. Movies and music give each log its runtime on its date, and a marked title once
  in All Time unless its first log has Rewatch off (decision 38). TV gives each viewing
  the episode's runtime, else its season's, else its show's, with the Rewatch switch's
  rules (STAT-104). Anime reads its viewings as AN-704 does, with TV's rules (decision
  62). Steam's imported lifetime playtime is never read (GM-500).

``_anime_runtime`` is the one old counter left: the anime calculation tests use it as
their oracle. It counts anime viewings: a tick on its day, a log's fills on the log's
day, a mark's fills only in All Time, which also adds a stored progress number with no
viewings.

``known`` and ``total`` count what's counted with a length, out of all: episodes on TV,
watches on movies, listens on music, titles on the rest. Every type is in
api.services.activity_rows.CALCULATED_TYPES and reads the calculation's rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import (
    Count,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import NullIf

from api.services import activity_rows
from app import exposure
from app.models import Anime, AnimeViewing, Game, GameSession, MediaTypes

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
        calculation = scope.calculation()
        rows = activity_rows.in_range(
            calculation.rows,
            media_type=media_type,
            start_date=scope.start_date,
            end_date=scope.end_date,
        )
        cache[media_type] = _from_rows(rows, calculation.piles if scope.is_all_time else [], media_type)
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


def _from_rows(rows, piles, media_type):
    """Add up one type's rows and undated piles.

    Everything is a plain int: a Decimal renders as 360.0, which the app reads as 0.
    """
    pile = activity_rows.piled(piles, media_type)
    result = TypeTime(media_type)
    result.value = int(sum((row.amount for row in rows), pile.amount))
    result.known, result.total = activity_rows.known_and_total(rows, pile, media_type)
    # A TV episode counted at its season's or show's runtime is an estimate (STAT-201).
    result.approximate = pile.approximate or any(row.approximate for row in rows)
    episodes = pile.told if media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value) else 0
    result.progress = int(sum((row.told for row in rows if row.told_unit == activity_rows.EPISODE), episodes))
    return result


# Counters, one per type.


def _anime_runtime(scope, media_type):
    """Count anime viewings: each the title's runtime (AN-704).

    All Time adds a stored progress number with no viewings, which counts as it always
    did, and only there.
    """
    result = TypeTime(media_type)
    totals = scope.anime_viewings.annotate(stats_runtime=_positive("anime__item__runtime_minutes")).aggregate(
        value=Sum("stats_runtime"),
        known=Count("stats_runtime"),
        total=Count("id"),
    )
    _add(result, totals)
    result.progress = totals["total"] or 0
    if scope.is_all_time:
        stored = (
            Anime.objects.filter(user=scope.owner, progress__gt=0)
            .exclude(Exists(AnimeViewing.objects.filter(anime=OuterRef("pk"))))
            .annotate(stats_count=F("progress"), stats_runtime=_positive("item__runtime_minutes"))
            .aggregate(
                episodes=Sum("stats_count"),
                value=Sum(ExpressionWrapper(F("stats_count") * F("stats_runtime"), output_field=IntegerField())),
                known=Count("id", filter=Q(stats_runtime__isnull=False)),
                total=Count("id"),
            )
        )
        result.progress += stored["episodes"] or 0
        _add(result, stored)
    return result


# Shared helpers, also used by the header (api.services.stats_v2_activity).


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


def _add(result, totals):
    result.value += totals["value"] or 0
    result.known += totals["known"] or 0
    result.total += totals["total"] or 0
