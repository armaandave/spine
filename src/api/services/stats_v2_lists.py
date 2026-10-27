"""Stats v2 Top, list progress rings and collections (docs/stats-contract.md).

STAT-206 to STAT-208 (Top), STAT-213 (list rings) and STAT-214 (collections). Every
function takes an api.services.stats_scope.StatsScope. build_collections is called
only on the movie, game, book and anime tabs (``scope.has_collections``); the
assembly sends null elsewhere (STAT-302). collections_page serves the show-all
endpoint (STAT-310): page is 1-based; it returns ``{"count", "results"}``.

Rings and collections are lifetime (they ignore the range). An item is **seen** when
the owner's latest tracking row for it is Completed or the viewer can see a log of
that exact Item (STAT-213): ``seen_q`` builds that as subqueries, so no Item IDs are
ever sent as query parameters.
"""

import hashlib
import re

from django.core.cache import cache
from django.db.models import Count, F, Q, Window
from django.db.models.functions import Lower, RowNumber

from api.services import completion as completion_service
from api.services import stats_v2_ratings as ratings
from api.services.stats_scope import TV_ITEM_TYPES, latest_tracking_rows, stars
from api.views import featured
from app import exposure
from app.models import (
    MediaSeries,
    MediaSeriesItem,
    MediaTypes,
    Sources,
    Status,
)
from lists.models import CustomList, CustomListItem

TOP_LIMIT = 10
COLLECTION_PREVIEW_LIMIT = 4
COLLECTION_POSTER_LIMIT = 3

# A list tag naming a Top list, after casefold().strip() (STAT-206).
TOP_TAG_PATTERN = re.compile(r"^top[\s_-]*(?:(\d{4})|all[\s_-]*time)$")

# The noun in "Tag a ranked movie list top2025" (STAT-208).
TYPE_NOUNS = {
    MediaTypes.MOVIE.value: "movie",
    MediaTypes.TV.value: "TV",
    MediaTypes.ANIME.value: "anime",
    MediaTypes.MANGA.value: "manga",
    MediaTypes.GAME.value: "game",
    MediaTypes.BOOK.value: "book",
    MediaTypes.COMIC.value: "comic",
    MediaTypes.MUSIC.value: "music",
}

# Featured-list kinds that rank lasting merit (STAT-213); charts never show rings.
RING_KINDS = frozenset({"award", "canon", "community", "curator", "editorial"})
# Featured lists that rank popularity, not lasting merit (STAT-213).
RING_EXCLUDED_KEYS = frozenset({
    "mal-most-popular-anime",
    "mal-most-popular-manga",
    "igdb-playing",
    "igdb-want-to-play",
})
RING_MIN_SEEN = 2
# Ring totals are per list content, not per owner: cached by the list's fingerprint.
RING_TOTALS_TIMEOUT = 7 * 24 * 60 * 60
RING_TOTALS_VERSION = 1

COLLECTION_GROUPS = ("complete", "almost")
COLLECTION_ALMOST_MAX_LEFT = 2


def _bucket(media_type):
    """Return a media type's type bucket: seasons and episodes belong to tv."""
    return MediaTypes.TV.value if media_type in TV_ITEM_TYPES else media_type


def seen_q(scope, field="item_id"):
    """Match rows whose Item the owner has seen, as the scope's viewer may count it.

    Seen is a Completed latest tracking row (the same for every viewer) or a log of
    that exact Item the viewer can see (STAT-003). Every part is a subquery.
    """
    condition = Q(**{f"{field}__in": scope.visible_entries.values("item_id")})
    for media_type in exposure.user_owned_media_types():
        completed = latest_tracking_rows(media_type, scope.owner).filter(
            status=Status.COMPLETED.value,
        )
        condition |= Q(**{f"{field}__in": completed.values("item_id")})
    return condition


def top_tag_for(tag):
    """Return the wanted tag a list tag names (top2025, topalltime), or None."""
    if not isinstance(tag, str):
        return None
    match = TOP_TAG_PATTERN.match(tag.casefold().strip())
    if match is None:
        return None
    year = match.group(1)
    return f"top{year}" if year is not None else "topalltime"


def tag_hint(scope):
    """Return how to tag a Top list: owner only, with a wanted tag (STAT-004, 208)."""
    tag = scope.top_tag
    if not scope.is_owner or tag is None:
        return None
    if scope.media_type is None:
        return f"Tag a ranked list with mixed media {tag}"
    return f"Tag a ranked {TYPE_NOUNS[scope.media_type]} list {tag}"


def top_list(scope):
    """Return the winning Top list for the scope's tab, or None (STAT-206, STAT-207).

    Candidates are the owner's own non-featured media lists with the wanted tag (every
    visibility for the owner, only public ones for anyone else), ranked, with at least
    one exposed item. A list whose exposed items share one type bucket feeds that tab;
    a list with two or more feeds Global. The newest ``updated_at``, then the highest
    ID, wins. The returned list carries ``stats_total``, its exposed item count.
    """
    wanted = scope.top_tag
    if wanted is None:
        return None
    lists = CustomList.objects.filter(
        owner=scope.owner,
        is_featured=False,
        list_type=CustomList.ListType.MEDIA,
    ).exclude(tags=[])
    if not scope.is_owner:
        lists = lists.filter(visibility=CustomList.Visibility.PUBLIC)
    # Tags are a JSON list, so the match runs here, as TaggedListsView does: only the
    # owner's tagged lists' IDs and tags are read.
    rows = lists.order_by().values_list("id", "tags", "is_ranked")
    tagged = {
        list_id: is_ranked
        for list_id, tags, is_ranked in rows
        if isinstance(tags, list) and any(top_tag_for(tag) == wanted for tag in tags)
    }
    if not tagged:
        return None
    buckets, totals, positioned = {}, {}, set()
    members = (
        CustomListItem.objects.filter(
            custom_list_id__in=list(tagged),
            item__media_type__in=exposure.media_types(),
        )
        .order_by()
        .values_list("custom_list_id", "item__media_type")
        .annotate(items=Count("item_id", distinct=True), positions=Count("position"))
    )
    for list_id, media_type, items, positions in members:
        buckets.setdefault(list_id, set()).add(_bucket(media_type))
        totals[list_id] = totals.get(list_id, 0) + items
        if positions:
            positioned.add(list_id)
    fitting = []
    for list_id, list_buckets in buckets.items():
        if not (tagged[list_id] or list_id in positioned):
            continue
        tab = next(iter(list_buckets)) if len(list_buckets) == 1 else None
        if tab == scope.media_type:
            fitting.append(list_id)
    if not fitting:
        return None
    winner = (
        CustomList.objects.filter(id__in=fitting)
        .only("id", "name", "visibility", "updated_at")
        .order_by("-updated_at", "-id")
        .first()
    )
    winner.stats_total = totals[winner.id]
    return winner


def title_ratings(scope, items):
    """Return ``{item_id: stored rating}`` for the given Items in the scope (STAT-109).

    The same rule Highest rated uses (``stats_v2_ratings.title_ratings``): the newest
    rated visible log in the range, else in All Time the current rating. Episodes have
    no title rating.
    """
    item_ids = [item.id for item in items if item.media_type != MediaTypes.EPISODE.value]
    if not item_ids:
        return {}
    return ratings.title_ratings(scope, item_ids)


def _top_from_list(scope):
    """Return (the winning Top list, its first 10 exposed Items), cached on the scope.

    ``(None, [])`` when no list fits (STAT-208).
    """
    cached = scope.__dict__.get("_top_from_list")
    if cached is None:
        winner = top_list(scope)
        rows = featured.preview_items([winner.id], TOP_LIMIT).get(winner.id, []) if winner else []
        cached = (winner, [row.item for row in rows])
        scope.__dict__["_top_from_list"] = cached
    return cached


def top_item_ids(scope):
    """Return the IDs of the Items Top shows from a list, for Highest rated to skip."""
    _winner, items = _top_from_list(scope)
    return [item.id for item in items]


def build_top(scope):
    """Build Top: the newest ranked tagged list, else Highest rated (STAT-206-208)."""
    winner, items = _top_from_list(scope)
    if winner is None:
        # The first rows of Highest rated, from the preview it already loads.
        fallback = ratings.highest_rated_items(scope)[:TOP_LIMIT]
        return {
            "source": "highest_rated",
            "tag": scope.top_tag,
            "tag_hint": tag_hint(scope),
            "hint_private": False,
            "list": None,
            "items": [
                {"rank": rank, "media": row["media"], "stars": row["stars"]}
                for rank, row in enumerate(fallback, start=1)
            ],
        }
    rated = title_ratings(scope, items)
    summaries = scope.media_summaries([item.id for item in items])
    return {
        "source": "list",
        "tag": scope.top_tag,
        "tag_hint": None,
        "hint_private": scope.is_owner and winner.visibility != CustomList.Visibility.PUBLIC,
        "list": {"id": winner.id, "name": winner.name, "total": winner.stats_total},
        "items": [
            {"rank": rank, "media": summaries.get(item.id), "stars": stars(rated.get(item.id))}
            for rank, item in enumerate(items, start=1)
        ],
    }


def _ring_cache_key(custom_list, sync, exposed_key):
    content = sync.fingerprint if sync is not None and sync.fingerprint else custom_list.updated_at.isoformat()
    return f"stats:ring_totals:v{RING_TOTALS_VERSION}:{custom_list.id}:{content}:{exposed_key}"


def ring_totals(lists_with_sync):
    """Return ``{list_id: {"rows", "items", "buckets"}}`` over each list's exposed rows.

    ``rows`` counts exposed rows (as ``featured.is_partial`` reads them), ``items``
    distinct exposed Items (the ring's total) and ``buckets`` their type buckets.
    Cached per list content (the sync fingerprint, else ``updated_at``) and exposure,
    so only lists whose content changed are counted, in one grouped query.
    """
    exposed = exposure.media_types()
    exposed_key = hashlib.sha256(",".join(sorted(exposed)).encode()).hexdigest()[:12]
    keys = {
        custom_list.id: _ring_cache_key(custom_list, sync, exposed_key)
        for custom_list, sync in lists_with_sync
    }
    cached = cache.get_many(list(keys.values()))
    totals = {list_id: cached[key] for list_id, key in keys.items() if key in cached}
    missing = [list_id for list_id in keys if list_id not in totals]
    if missing:
        counted = {list_id: {"rows": 0, "items": 0, "buckets": []} for list_id in missing}
        grouped = (
            CustomListItem.objects.filter(custom_list_id__in=missing, item__media_type__in=exposed)
            .order_by()
            .values_list("custom_list_id", "item__media_type")
            .annotate(rows=Count("id"), items=Count("item_id", distinct=True))
        )
        for list_id, media_type, rows, items in grouped:
            value = counted[list_id]
            value["rows"] += rows
            value["items"] += items
            bucket = _bucket(media_type)
            if bucket not in value["buckets"]:
                value["buckets"].append(bucket)
        cache.set_many({keys[list_id]: value for list_id, value in counted.items()}, RING_TOTALS_TIMEOUT)
        totals.update(counted)
    return totals


def _ring_candidates():
    """Return ``[(custom_list, key, kind, media_type, totals)]`` of lasting lists.

    Every rule of STAT-213 but the 2-seen minimum: public, featured, media, a lasting
    kind (no definition and no sync is editorial), no live windows, not retired or
    partial, not an excluded key, and at least one exposed item.
    """
    lists = (
        CustomList.objects.filter(
            is_featured=True,
            visibility=CustomList.Visibility.PUBLIC,
            list_type=CustomList.ListType.MEDIA,
        )
        .select_related("featured_sync")
        .defer("featured_sync__unresolved", "featured_sync__entry_notes", "featured_sync__item_notes")
        .order_by("id")
    )
    joined = []
    for custom_list in lists:
        definition, sync = featured.definition_for(custom_list)
        if featured.is_retired(definition, sync):
            continue
        if definition is None:
            if sync is not None:
                continue
            kind, key = "editorial", None
        else:
            if definition.live_windows:
                continue
            kind, key = definition.kind, definition.key
        if kind not in RING_KINDS or key in RING_EXCLUDED_KEYS:
            continue
        joined.append((custom_list, definition, sync, kind))
    totals = ring_totals([(custom_list, sync) for custom_list, _, sync, _ in joined])
    candidates = []
    for custom_list, definition, sync, kind in joined:
        counts = totals[custom_list.id]
        if not counts["items"] or featured.is_partial(definition, sync, counts["rows"]):
            continue
        if definition is not None:
            types = definition.media_types
            media_type = types[0] if len(types) == 1 else None
        else:
            media_type = counts["buckets"][0] if len(counts["buckets"]) == 1 else None
        candidates.append((custom_list, kind, media_type, counts["items"]))
    return candidates


def build_list_rings(scope):
    """Build rings for lasting featured lists with at least 2 seen (STAT-213).

    Lifetime: every range shows the same rings. Global shows every ring, a type tab
    the rings of its type. Sorted by seen share, then seen, then name.
    """
    candidates = [
        candidate
        for candidate in _ring_candidates()
        if scope.media_type is None or candidate[2] == scope.media_type
    ]
    if not candidates:
        return []
    seen = dict(
        CustomListItem.objects.filter(
            custom_list_id__in=[custom_list.id for custom_list, *_ in candidates],
            item__media_type__in=exposure.media_types(),
        )
        .filter(seen_q(scope))
        .order_by()
        .values("custom_list_id")
        .annotate(seen=Count("item_id", distinct=True))
        .values_list("custom_list_id", "seen"),
    )
    rings = [
        {
            "id": custom_list.id,
            "name": custom_list.name,
            "media_type": media_type,
            "kind": kind,
            "seen": seen.get(custom_list.id, 0),
            "total": total,
        }
        for custom_list, kind, media_type, total in candidates
        if seen.get(custom_list.id, 0) >= RING_MIN_SEEN
    ]
    rings.sort(key=lambda ring: (-ring["seen"] / ring["total"], -ring["seen"], ring["name"].casefold(), ring["id"]))
    return rings


def _collections(scope):
    """Annotate the tab's stored series with released and seen member counts.

    A member is released when its release date is on or before today on the owner's
    calendar, or it has no date and its source isn't TMDB. Only series with at least
    2 released members and a seen member are kept. Everything stays in SQL.
    """
    released = Q(memberships__item__release_date__lte=scope.today) | (
        Q(memberships__item__release_date__isnull=True) & ~Q(memberships__item__source=Sources.TMDB.value)
    )
    touched = MediaSeriesItem.objects.filter(seen_q(scope)).values("series_id")
    return (
        MediaSeries.objects.filter(media_type=scope.media_type, id__in=touched)
        .annotate(
            stats_released=Count("memberships", filter=released, distinct=True),
            stats_seen=Count("memberships", filter=released & seen_q(scope, "memberships__item_id"), distinct=True),
        )
        .filter(stats_released__gte=2)
    )


def _collection_group(scope, group):
    """Return one group's series in STAT-214 order."""
    series = _collections(scope)
    if group == "complete":
        return series.filter(stats_seen=F("stats_released")).order_by("-stats_released", Lower("name"), "id")
    return (
        series.annotate(
            stats_left=F("stats_released") - F("stats_seen"),
            stats_double_seen=F("stats_seen") * 2,
        )
        .filter(
            stats_left__gte=1,
            stats_left__lte=COLLECTION_ALMOST_MAX_LEFT,
            stats_double_seen__gt=F("stats_released"),
        )
        .order_by("stats_left", "-stats_released", Lower("name"), "id")
    )


def _collection_rows(scope, series_rows):
    """Serialize series as media series summaries, with posters for these rows only."""
    series_rows = list(series_rows)
    if not series_rows:
        return []
    released = Q(item__release_date__lte=scope.today) | (
        Q(item__release_date__isnull=True) & ~Q(item__source=Sources.TMDB.value)
    )
    members = (
        MediaSeriesItem.objects.filter(released, series_id__in=[series.id for series in series_rows])
        .annotate(
            poster_row=Window(
                RowNumber(),
                partition_by=[F("series_id")],
                order_by=[F("position").asc(nulls_last=True), F("id").asc()],
            ),
        )
        .filter(poster_row__lte=COLLECTION_POSTER_LIMIT)
        .order_by("series_id", "poster_row")
        .values_list("series_id", "item_id")
    )
    members = list(members)
    summaries = scope.media_summaries([item_id for _, item_id in members])
    posters = {}
    for series_id, item_id in members:
        summary = summaries.get(item_id) or {}
        poster = summary.get("custom_poster_url") or summary.get("poster_url")
        if poster:
            posters.setdefault(series_id, []).append(poster)
    return [
        {
            "id": series.series_id,
            "source": series.source,
            "media_type": series.media_type,
            "name": series.name,
            "item_count": series.stats_released,
            "poster_urls": posters.get(series.id, []),
            "completion": completion_service.completion_payload(series.stats_seen, series.stats_released),
        }
        for series in series_rows
    ]


def build_collections(scope):
    """Build complete and almost complete series of the tab's type (STAT-214)."""
    complete = _collection_group(scope, "complete")
    almost = _collection_group(scope, "almost")
    complete_count = complete.count()
    almost_count = almost.count()
    shown_complete = list(complete[:COLLECTION_PREVIEW_LIMIT]) if complete_count else []
    shown_almost = list(almost[:COLLECTION_PREVIEW_LIMIT]) if almost_count else []
    rows = _collection_rows(scope, shown_complete + shown_almost)
    return {
        "complete_count": complete_count,
        "almost_count": almost_count,
        "complete": rows[: len(shown_complete)],
        "almost": rows[len(shown_complete) :],
    }


def collections_page(scope, group, page, page_size):
    """Return one page of the ``complete`` or ``almost`` group (STAT-214, 310)."""
    series = _collection_group(scope, group)
    count = series.count()
    start = (page - 1) * page_size
    shown = list(series[start : start + page_size]) if start < count else []
    return {"count": count, "results": _collection_rows(scope, shown)}
