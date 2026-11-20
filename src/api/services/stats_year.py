"""The profile's This Year section: the owner's year so far, in Stats' own numbers.

It reads the same scope the Stats page builds for this calendar year on Global
(docs/stats-contract.md STAT-001, STAT-300), on the owner's calendar (STAT-005), so each
type's numbers are the ones the Stats page shows for this year:

- ``media_types``: each type the owner has turned on, with its titles and logs
  (STAT-301), zero included.
- ``time``: each type's time in its own unit, from the calculation (STAT-201, STAT-202):
  pages read this year count whether or not the book was finished.
- ``titles``: the turned-on types' titles added up, so the total is what the rows add up
  to. Global's header (STAT-105) also counts the types the owner turned off; the section
  shows only the turned-on ones (STAT-312).

Only what the section shows is worked out, not the whole Stats page.

``posters`` is the pool the wall is drawn from: a random pick of this year's titles of
the turned-on types that have artwork, at most WALL_LIMIT. Each type's titles are
shuffled, then dealt one per type in turn, so every type gets its share however many
titles another type has. The pick changes whenever the answer is worked out again (the
cache keeps one for up to an hour, STAT-006), and the app shuffles it again on every
load. The posters are a backdrop with no tap, so they carry no viewer state, which keeps
the cached answer the same for everyone in an audience.
"""

from __future__ import annotations

import random
from collections import defaultdict, deque
from datetime import date
from itertools import islice

from django.conf import settings

from api.services import stats_v2_activity
from api.services.stats_scope import TV_ITEM_TYPES, StatsScope
from api.services.stats_v2 import media_type_rows
from app import user_calendar
from app.models import MediaTypes

# More than the app's largest wall (6 across by 8 down on a tall one), so it has spares
# to shuffle from.
WALL_LIMIT = 60
# Each round loads this many titles of every type ahead, in one batch.
_LOOKAHEAD = 3


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
    rows = media_type_rows(scope)
    media_types = [row["media_type"] for row in rows]
    return {
        "year": scope.start_date.year,
        "range": scope.range_payload(),
        "audience": scope.audience,
        "titles": sum(row["titles"] for row in rows),
        "media_types": rows,
        "time": stats_v2_activity.build_time(scope),
        "posters": wall_posters(scope, media_types),
    }


def shuffled_titles(scope, media_types, rng=random):
    """Return a shuffled queue of this year's titles for each of ``media_types``."""
    by_type = defaultdict(list)
    for title_id, media_type in scope.titles.values_list("id", "media_type"):
        by_type[MediaTypes.TV.value if media_type in TV_ITEM_TYPES else media_type].append(title_id)
    queues = []
    for media_type in media_types:
        titles = sorted(by_type.get(media_type, []))
        rng.shuffle(titles)
        if titles:
            queues.append(deque(titles))
    return queues


def wall_posters(scope, media_types, limit=WALL_LIMIT, rng=random):
    """Return up to ``limit`` media summaries of ``media_types`` for the wall.

    Each round takes one title of each type, in ``media_types``' order, from its
    shuffled queue. A title with no artwork is passed over for the type's next one, so
    the wall has no blank tiles and a type keeps its turn.
    """
    queues = shuffled_titles(scope, media_types, rng)
    posters = []
    while queues:
        # Load the next few of every type in one batch.
        scope.media_summaries([title_id for queue in queues for title_id in islice(queue, _LOOKAHEAD)])
        for queue in queues:
            while queue:
                # Titles with no artwork in a row load in batches too.
                scope.media_summaries(list(islice(queue, _LOOKAHEAD)))
                summary = scope.media_summary(queue.popleft())
                if summary is not None and has_poster(summary):
                    posters.append({**summary, "user_state": None})
                    break
            if len(posters) == limit:
                return posters
        queues = [queue for queue in queues if queue]
    return posters


def has_poster(summary):
    """Return whether a media summary has real artwork, not the no-image placeholder."""
    url = summary.get("custom_poster_url") or summary.get("poster_url")
    return bool(url) and url != settings.IMG_NONE
