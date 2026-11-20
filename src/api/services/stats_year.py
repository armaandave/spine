"""The profile's This Year section: the owner's year so far, in Stats' own numbers.

It reads the same scope the Stats page builds for this calendar year on Global
(docs/stats-contract.md STAT-001, STAT-300), on the owner's calendar (STAT-005), so each
number is the one the Stats page shows for this year:

- ``titles``: the header's titles (STAT-105), every type included.
- ``media_types``: each type the owner has turned on, with its titles and logs
  (STAT-301), zero included.
- ``time``: each type's time in its own unit, from the calculation (STAT-201, STAT-202):
  pages read this year count whether or not the book was finished.

Only what the section shows is worked out, not the whole Stats page.

``posters`` is the wall behind the numbers: this year's titles that have artwork, each
type newest activity first (its latest event, STAT-106), dealt one per type in turn in
the app's type order, at most WALL_LIMIT. They are a backdrop with no tap, so they carry
no viewer state, which keeps the cached answer the same for everyone in an audience.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import date

from django.conf import settings

from api.services import stats_v2_activity
from api.services.stats_scope import TV_ITEM_TYPES, StatsScope
from api.services.stats_v2 import media_type_rows
from app import user_calendar
from app.models import Item, MediaTypes

# The app's largest wall: 6 across by 6 down.
WALL_LIMIT = 36
# Summaries are loaded this many titles at a time, until the wall is full.
_SUMMARY_BATCH = 48


def year_scope(owner, viewer, *, request=None):
    """Return the Global scope of this calendar year on the owner's calendar."""
    year = user_calendar.today(owner).year
    return StatsScope(
        owner=owner,
        viewer=viewer,
        request=request,
        start_date=date(year, 1, 1),
        end_date=date(year, 12, 31),
    )


def build_year(scope):
    """Return the This Year payload for a year scope."""
    return {
        "year": scope.start_date.year,
        "range": scope.range_payload(),
        "audience": scope.audience,
        "titles": scope.titles.count(),
        "media_types": media_type_rows(scope),
        "time": stats_v2_activity.build_time(scope),
        "posters": wall_posters(scope),
    }


def wall_order(scope):
    """Return this year's title IDs in wall order.

    Each type's titles are ordered by their latest event, newest first (a tie goes to
    the higher ID); a title with no event goes after its type's dated ones. The types
    are then dealt one title each in turn, in the app's type order, until each runs out.
    """
    title_ids = set(scope.titles.values_list("id", flat=True))
    latest = {}
    for day, title_id, bucket in scope.events:
        if title_id in title_ids and (title_id not in latest or day > latest[title_id][0]):
            latest[title_id] = (day, bucket)
    by_type = defaultdict(list)
    for title_id, (day, bucket) in latest.items():
        by_type[bucket].append((day, title_id))
    undated = defaultdict(list)
    for title_id, media_type in Item.objects.filter(id__in=title_ids - latest.keys()).values_list("id", "media_type"):
        undated[MediaTypes.TV.value if media_type in TV_ITEM_TYPES else media_type].append(title_id)

    queues = []
    others = sorted((set(by_type) | set(undated)) - set(scope.primary_types))
    for media_type in [*scope.primary_types, *others]:
        dated = [title_id for _day, title_id in sorted(by_type.get(media_type, []), reverse=True)]
        queue = deque(dated + sorted(undated.get(media_type, []), reverse=True))
        if queue:
            queues.append(queue)
    order = []
    while queues:
        order.extend(queue.popleft() for queue in queues)
        queues = [queue for queue in queues if queue]
    return order


def wall_posters(scope, limit=WALL_LIMIT):
    """Return up to ``limit`` media summaries for the wall, in wall order.

    A title with no artwork is skipped, so the wall has no blank tiles.
    """
    order = wall_order(scope)
    posters = []
    for start in range(0, len(order), _SUMMARY_BATCH):
        batch = order[start : start + _SUMMARY_BATCH]
        summaries = scope.media_summaries(batch)
        for title_id in batch:
            summary = summaries.get(title_id)
            if summary is None or not has_poster(summary):
                continue
            posters.append({**summary, "user_state": None})
            if len(posters) == limit:
                return posters
    return posters


def has_poster(summary):
    """Return whether a media summary has real artwork, not the no-image placeholder."""
    url = summary.get("custom_poster_url") or summary.get("poster_url")
    return bool(url) and url != settings.IMG_NONE
