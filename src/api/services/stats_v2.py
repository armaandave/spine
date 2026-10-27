"""Assemble the stats v2 payload (docs/stats-contract.md STAT-300 to 302, STAT-218).

Every top-level key is always present. A section is null only when it doesn't belong
to the tab (STAT-302): ``languages`` off Global, movie and TV, ``collections`` off the
movie, game, book and anime tabs. Sections that belong but have nothing yet are empty,
which each section builder decides.
"""

from django.db.models import Count, Min

from api.services import stats_v2_activity as activity
from api.services import stats_v2_lists as lists
from api.services import stats_v2_ratings as ratings
from api.services import stats_v2_taste as taste
from api.services.stats_scope import media_type_bucket

SCHEMA_VERSION = 2


def build_stats_v2(scope):
    """Return the v2 payload for one scope."""
    return {
        "schema_version": SCHEMA_VERSION,
        "range": scope.range_payload(),
        "media_type": scope.media_type,
        "audience": scope.audience,
        "years": {"first": first_year(scope), "current": scope.today.year},
        "media_types": media_type_rows(scope),
        "header": activity.build_header(scope),
        "time": activity.build_time(scope),
        "top": lists.build_top(scope),
        # Leaves out what Top already shows from a list (STAT-205).
        "highest_rated": ratings.build_highest_rated(scope, exclude_ids=lists.top_item_ids(scope)),
        "activity": activity.build_activity(scope),
        "ratings": ratings.build_ratings(scope),
        "rated_vs_everyone": ratings.build_rated_vs_everyone(scope),
        "release_years": taste.build_release_years(scope),
        "genres": taste.build_genres(scope),
        "languages": taste.build_languages(scope) if scope.has_languages else None,
        "list_rings": lists.build_list_rings(scope),
        "collections": lists.build_collections(scope) if scope.has_collections else None,
        "most_logged": ratings.build_most_logged(scope),
        "milestones": activity.build_milestones(scope),
    }


def media_type_rows(scope):
    """Count titles (STAT-105) and logs (STAT-103) per exposed type (STAT-301).

    Always over every type, whatever the tab, so the app's type picker never needs a
    Global load.
    """
    everything = scope.global_scope()
    titles = dict(
        everything.titles.annotate(stats_bucket=media_type_bucket("media_type"))
        .values("stats_bucket")
        .annotate(count=Count("id"))
        .values_list("stats_bucket", "count"),
    )
    logs = dict(
        everything.entries.annotate(stats_bucket=media_type_bucket("item__media_type"))
        .values("stats_bucket")
        .annotate(count=Count("id"))
        .values_list("stats_bucket", "count"),
    )
    return [
        {
            "media_type": media_type,
            "titles": titles.get(media_type, 0),
            "logs": logs.get(media_type, 0),
        }
        for media_type in everything.primary_types
    ]


def first_year(scope):
    """Return the year of the earliest log day or dated viewing the viewer can count."""
    everything = scope.global_scope().all_time()
    days = [everything.all_entries.aggregate(first=Min(everything.log_day()))["first"]]
    if everything.counts_tv:
        days.append(everything.dated_viewings.aggregate(first=Min(everything.viewing_day()))["first"])
    days = [day for day in days if day is not None]
    return min(days).year if days else None
