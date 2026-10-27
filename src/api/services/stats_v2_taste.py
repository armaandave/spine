"""Stats v2 release years, genres and languages (docs/stats-contract.md).

STAT-209 (release years), STAT-210 and STAT-211 (genres) and STAT-212 (languages).
Every function takes an api.services.stats_scope.StatsScope and returns its section's
shape. build_languages is called only on tabs that have Languages
(``scope.has_languages``); the assembly sends null elsewhere (STAT-302).

Everything is counted on titles (STAT-102, STAT-105): a season or episode row counts
through its show's Item, so its year, genres and language are the show's. Counting
is done in SQL. Python only folds the vocabulary (the distinct stored genre spellings,
never one row per title or log) into rows, and formats what is sent.
"""

from collections import defaultdict
from functools import reduce
from operator import or_

from django.db.models import (
    Avg,
    BooleanField,
    Count,
    Exists,
    ExpressionWrapper,
    OuterRef,
    Q,
    Subquery,
)
from django.db.models.functions import ExtractYear

from api.services import genre_groups
from api.services.stats_scope import (
    SHOW_PART_TYPES,
    TV_ITEM_TYPES,
    StatsScope,
    media_type_bucket,
    stars,
)
from app import exposure
from app.models import Item, ItemFilterFacet, MediaTypes

# Rows sent before the cap (STAT-210, STAT-212); Global music genres (STAT-211).
MAX_ROWS = 50
MAX_MUSIC_ROWS = 5
# New vs older (STAT-209): released the log's year or later, 1 to 7 years before, 8+.
RECENT_YEARS = 7

GENRE = ItemFilterFacet.FacetType.GENRE
ORIGINAL_LANGUAGE = ItemFilterFacet.FacetType.ORIGINAL_LANGUAGE


# The release years section, STAT-209


def build_release_years(scope):
    """Build titles per release year, with coverage and new vs older (STAT-209)."""
    titles = defaultdict(lambda: defaultdict(int))
    known = total = 0
    for year, bucket, count in (
        scope.titles.annotate(stats_bucket=media_type_bucket("media_type"))
        .values("release_year", "stats_bucket")
        .annotate(count=Count("id"))
        .values_list("release_year", "stats_bucket", "count")
    ):
        total += count
        if year is not None:
            known += count
            titles[year][bucket] += count

    logs = {
        row["title_year"]: row
        for row in _entries_with_title(scope.entries)
        .annotate(title_year=_title_field("release_year"))
        .values("title_year")
        .annotate(
            logs=Count("id"),
            rated=Count("id", filter=Q(rating__isnull=False)),
            average=Avg("rating"),
        )
    }
    rows = []
    for year in sorted(titles):
        by_type = titles[year]
        log_row = logs.get(year) or {}
        rows.append({
            "year": year,
            "titles": sum(by_type.values()),
            "logs": log_row.get("logs", 0),
            "rated": log_row.get("rated", 0),
            "average_stars": stars(log_row.get("average")),
            "by_type": _ordered_by_type(by_type),
        })
    return {
        "rows": rows,
        "coverage": {"known": known, "total": total},
        "new_vs_older": _new_vs_older(scope),
    }


def _new_vs_older(scope):
    """Sort titles with a dated log or viewing in the range by age when first seen.

    A title's class comes from its earliest day in the range, so it is "new" when any
    of its days is in or before its release year, "recent" when the earliest is 1 to
    7 years after, else "older". Undated completions and titles with no year are left
    out. One aggregate over the scope's titles, with correlated EXISTS per title.
    """
    titles = scope.titles.filter(release_year__isnull=False)
    counts = titles.aggregate(
        seen=Count("id", filter=_has_dated_event(scope, None)),
        new=Count("id", filter=_has_dated_event(scope, 0)),
        within_recent=Count("id", filter=_has_dated_event(scope, RECENT_YEARS)),
    )
    return {
        "new": counts["new"],
        "recent": counts["within_recent"] - counts["new"],
        "older": counts["seen"] - counts["within_recent"],
    }


def _has_dated_event(scope, years_after):
    """Match title Items with a visible log or dated viewing in the range.

    With ``years_after``, only events whose year is at most that many years after the
    title's release year count.
    """
    logs = scope.entries.annotate(event_year=ExtractYear(scope.log_day()))
    viewings = scope.dated_viewings.annotate(event_year=ExtractYear(scope.viewing_day()))
    if years_after is not None:
        logs = logs.filter(event_year__lte=OuterRef("release_year") + years_after)
        viewings = viewings.filter(event_year__lte=OuterRef("release_year") + years_after)
    condition = Exists(logs.filter(item_id=OuterRef("id")))
    if scope.counts_tv:
        # A show's title also holds its season and episode rows (STAT-102).
        condition = (
            condition
            | Exists(viewings.filter(item_id=OuterRef("id")))
            | (Q(media_type=MediaTypes.TV.value) & (
                Exists(logs.filter(_show_part_of_outer()))
                | Exists(viewings.filter(_show_part_of_outer()))
            ))
        )
    return Q(condition)


def _show_part_of_outer():
    """Match season and episode rows of the outer show Item."""
    return Q(
        item__media_type__in=SHOW_PART_TYPES,
        item__source=OuterRef("source"),
        item__media_id=OuterRef("media_id"),
    )


# Genre sections, STAT-210 and STAT-211


def build_genres(scope):
    """Build genre names on a type tab, 15 groups and music on Global (STAT-210-211)."""
    if scope.media_type is None:
        return _global_genres(scope)
    return _type_genres(scope)


def _type_genres(scope):
    """Build a type tab's own genre names, case variants merged (STAT-210)."""
    titles = scope.titles
    rows = _native_rows(titles, GENRE)
    total = len(rows)
    rows = rows[:MAX_ROWS]
    rated = _rated_by_row(scope.entries, [_facet_items(GENRE, row["values"]) for row in rows])
    coverage = titles.aggregate(total=Count("id"), known=Count("id", filter=_has_facet(GENRE)))
    return {
        "rows": [
            {
                "key": None,
                "name": row["name"],
                "values": row["values"],
                "titles": row["titles"],
                "rated": rated[index][0],
                "average_stars": stars(rated[index][1]),
                "by_type": {scope.media_type: row["titles"]} if row["titles"] else {},
            }
            for index, row in enumerate(rows)
        ],
        "total": total,
        "coverage": {"known": coverage["known"], "total": coverage["total"]},
        "music": [],
    }


def _global_genres(scope):
    """Build the 15 shared groups over non-music titles, music apart (STAT-211)."""
    music = MediaTypes.MUSIC.value
    titles = scope.titles.exclude(media_type=music)
    pairs_by_group = defaultdict(lambda: defaultdict(set))
    for value, media_type in (
        ItemFilterFacet.objects.filter(
            facet_type__in=genre_groups.GENRE_FACET_TYPES,
            item_id__in=titles.values("id"),
        )
        .values_list("value", "item__media_type")
        .distinct()
    ):
        for group in genre_groups.groups_for(media_type, value):
            pairs_by_group[group][media_type].add(value)

    groups = [key for key, _label in genre_groups.GROUPS if key in pairs_by_group]
    # Every group's values together: a title is known when it has any of them.
    any_group = defaultdict(set)
    for by_type in pairs_by_group.values():
        for media_type, values in by_type.items():
            any_group[media_type] |= values
    by_group = defaultdict(dict)
    known = 0
    if groups:
        # One pass over the facets of the scope's titles; each title has one bucket.
        aggregates = {
            f"group_{index}": Count("item_id", distinct=True, filter=_pairs_q(pairs_by_group[key]))
            for index, key in enumerate(groups)
        }
        for row in (
            ItemFilterFacet.objects.filter(
                facet_type__in=genre_groups.GENRE_FACET_TYPES,
                item_id__in=titles.values("id"),
            )
            .annotate(stats_bucket=media_type_bucket("item__media_type"))
            .values("stats_bucket")
            .annotate(known=Count("item_id", distinct=True, filter=_pairs_q(any_group)), **aggregates)
            .order_by()
        ):
            known += row["known"]
            for index, key in enumerate(groups):
                if row[f"group_{index}"]:
                    by_group[key][row["stats_bucket"]] = row[f"group_{index}"]
    total = titles.count()

    groups = [key for key in groups if by_group[key]]
    rated = _rated_by_row(
        scope.entries.exclude(item__media_type=music),
        [_group_items(pairs_by_group[key]) for key in groups],
    )
    rows = [
        {
            "key": key,
            "name": genre_groups.GROUP_LABELS[key],
            "values": None,
            "titles": sum(by_group[key].values()),
            "rated": rated[index][0],
            "average_stars": stars(rated[index][1]),
            "by_type": _ordered_by_type(by_group[key]),
        }
        for index, key in enumerate(groups)
    ]
    rows.sort(key=lambda row: (-row["titles"], row["name"]))

    music_rows = []
    if music in scope.primary_types:
        music_rows = [
            {"name": row["name"], "values": row["values"], "titles": row["titles"]}
            for row in _native_rows(scope.titles.filter(media_type=music), GENRE)[:MAX_MUSIC_ROWS]
        ]
    return {
        "rows": rows,
        "total": len(rows),
        "coverage": {"known": known, "total": total},
        "music": music_rows,
    }


def _native_rows(titles, facet_type):
    """Return a facet's rows over titles, spellings differing in case merged (STAT-210).

    Each row is {name, values, titles}: the name is the spelling most titles carry
    (ties: the first alphabetically), values every stored spelling, most used first.
    Rows are sorted by titles, then name.
    """
    facets = ItemFilterFacet.objects.filter(facet_type=facet_type, item_id__in=titles.values("id"))
    spellings = defaultdict(list)
    for value, count in (
        facets.values("value")
        .annotate(count=Count("item_id", distinct=True))
        .values_list("value", "count")
    ):
        spellings[value.casefold()].append((value, count))

    rows = []
    merged = {}
    for variants in spellings.values():
        variants.sort(key=lambda variant: (-variant[1], variant[0]))
        values = [value for value, _count in variants]
        row = {"name": values[0], "values": values, "titles": variants[0][1]}
        if len(values) > 1:
            # A title may carry two spellings, so count the merged row's titles once.
            merged[f"merged_{len(merged)}"] = (row, values)
        rows.append(row)
    if merged:
        counts = facets.aggregate(
            **{
                key: Count("item_id", distinct=True, filter=Q(value__in=values))
                for key, (_row, values) in merged.items()
            },
        )
        for key, (row, _values) in merged.items():
            row["titles"] = counts[key]
    rows.sort(key=lambda row: (-row["titles"], row["name"].casefold(), row["name"]))
    return rows


def _facet_items(facet_type, values):
    """Facets of the type whose value is one of values (for _rated_by_row)."""
    return ItemFilterFacet.objects.filter(facet_type=facet_type, value__in=values)


def _pairs_q(values_by_type):
    """Match genre or theme facets whose value is in the set for its Item's type."""
    return reduce(
        or_,
        (
            Q(item__media_type=media_type, value__in=sorted(values))
            for media_type, values in sorted(values_by_type.items())
        ),
    )


def _group_items(values_by_type):
    """Genre and theme facets in the group, each matched on its own type."""
    return ItemFilterFacet.objects.filter(facet_type__in=genre_groups.GENRE_FACET_TYPES).filter(
        _pairs_q(values_by_type),
    )


def _has_facet(facet_type):
    """Match Items with a facet of the type: one index probe per Item, never a scan."""
    return Q(Exists(ItemFilterFacet.objects.filter(item_id=OuterRef("id"), facet_type=facet_type)))


# The languages section, STAT-212


def build_languages(scope):
    """Build titles per original language, movies and TV only (STAT-212)."""
    language_types = list(scope.language_types)
    item_types = [
        item_type
        for media_type in language_types
        for item_type in (TV_ITEM_TYPES if media_type == MediaTypes.TV.value else (media_type,))
    ]
    titles = scope.titles.filter(media_type__in=item_types)
    by_language = defaultdict(lambda: defaultdict(int))
    for value, bucket, count in (
        ItemFilterFacet.objects.filter(facet_type=ORIGINAL_LANGUAGE, item_id__in=titles.values("id"))
        .annotate(stats_bucket=media_type_bucket("item__media_type"))
        .values("value", "stats_bucket")
        .annotate(count=Count("item_id", distinct=True))
        .values_list("value", "stats_bucket", "count")
    ):
        by_language[value][bucket] += count
    coverage = titles.aggregate(total=Count("id"), known=Count("id", filter=_has_facet(ORIGINAL_LANGUAGE)))
    rated = {
        row["title_language"]: row
        for row in _entries_with_title(scope.entries.filter(item__media_type__in=item_types))
        .filter(rating__isnull=False)
        .annotate(title_language=_title_language())
        .values("title_language")
        .annotate(rated=Count("id"), average=Avg("rating"))
    }
    rows = [
        {
            "name": name,
            "titles": sum(by_type.values()),
            "rated": (rated.get(name) or {}).get("rated", 0),
            "average_stars": stars((rated.get(name) or {}).get("average")),
            "by_type": _ordered_by_type(by_type),
        }
        for name, by_type in by_language.items()
    ]
    rows.sort(key=lambda row: (-row["titles"], row["name"].casefold(), row["name"]))
    return {
        "rows": rows[:MAX_ROWS],
        "total": len(rows),
        "coverage": {"known": coverage["known"], "total": coverage["total"]},
        "media_types": language_types,
    }


def _title_language():
    """Return a log's title's original language (one per title, STAT-212)."""
    return Subquery(
        ItemFilterFacet.objects.filter(item_id=OuterRef("stats_title_id"), facet_type=ORIGINAL_LANGUAGE)
        .order_by("value")
        .values("value")[:1],
    )


# Shared


def _entries_with_title(entries):
    """Annotate logs with their title Item ID as ``stats_title_id`` (STAT-102)."""
    return entries.annotate(stats_title_id=StatsScope.title_id())


def _title_field(field):
    """Return a field of the log's title Item (after _entries_with_title)."""
    return Subquery(Item.objects.filter(id=OuterRef("stats_title_id")).values(field)[:1])


def _rated_by_row(entries, row_facets):
    """Return [(rated logs, average stored rating)] per row, in one aggregate.

    ``row_facets`` holds, per row, a queryset of the facets that put an Item in that
    row. A log counts in a row when it is rated and its title has one (STAT-210):
    a correlated EXISTS per log and row, an index probe on (item, facet type).
    """
    if not row_facets:
        return []
    flags = {
        f"stats_in_row_{index}": ExpressionWrapper(
            Exists(facets.filter(item_id=OuterRef("stats_title_id"))),
            output_field=BooleanField(),
        )
        for index, facets in enumerate(row_facets)
    }
    aggregates = {}
    for index in range(len(row_facets)):
        in_row = Q(**{f"stats_in_row_{index}": True})
        aggregates[f"rated_{index}"] = Count("id", filter=in_row)
        aggregates[f"average_{index}"] = Avg("rating", filter=in_row)
    # The flags are annotated first, as in stats_v2_activity._log_counts.
    result = (
        _entries_with_title(entries.filter(rating__isnull=False))
        .annotate(**flags)
        .aggregate(**aggregates)
    )
    return [(result[f"rated_{index}"], result[f"average_{index}"]) for index in range(len(row_facets))]


def _ordered_by_type(by_type):
    """Titles per type bucket in primary type order, leaving out zeros."""
    order = {media_type: index for index, media_type in enumerate(exposure.primary_media_types())}
    return {
        bucket: count
        for bucket, count in sorted(by_type.items(), key=lambda pair: order.get(pair[0], len(order)))
        if count
    }

