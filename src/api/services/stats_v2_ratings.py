"""Stats v2 ratings, highest rated, rated vs everyone, most logged (stats contract).

STAT-204 (ratings), STAT-205 (highest rated), STAT-217 (rated higher than most) and
STAT-215 (most logged). Every function takes an api.services.stats_scope.StatsScope.
The page functions serve the show-all endpoints (STAT-310): page is 1-based and they
return ``{"count": int, "results": [...]}``; the view adds ``next`` and ``previous``.

Everything is ranked and counted in SQL on the stored 0-10 scale; Python only formats
the rows a response sends.
"""

from decimal import Decimal
from itertools import pairwise

from django.apps import apps
from django.db.models import (
    Case,
    CharField,
    Count,
    DateField,
    DecimalField,
    F,
    FloatField,
    IntegerField,
    Max,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Cast, Coalesce, Round

from api.services.stats_scope import (
    TV_ITEM_TYPES,
    day_string,
    media_type_bucket,
    one_decimal,
    rating_bound,
    stars,
    union_ids,
)
from app.external_ratings import rating_source_is_exposed, source_label
from app.models import ExternalRating, Item, MediaTypes

PREVIEW_LIMIT = 12
VS_EVERYONE_LIMIT = 5
MOST_LOGGED_MIN_TIMES = 2
# A community rating counts only with this many votes, when votes are stored (STAT-217).
MIN_VOTE_COUNT = 50

# Each star bucket's inclusive stored bounds (STAT-204).
RATING_BUCKETS = (
    ("0.5", Decimal("0.0"), Decimal("1.4")),
    ("1.0", Decimal("1.5"), Decimal("2.4")),
    ("1.5", Decimal("2.5"), Decimal("3.4")),
    ("2.0", Decimal("3.5"), Decimal("4.4")),
    ("2.5", Decimal("4.5"), Decimal("5.4")),
    ("3.0", Decimal("5.5"), Decimal("6.4")),
    ("3.5", Decimal("6.5"), Decimal("7.4")),
    ("4.0", Decimal("7.5"), Decimal("8.4")),
    ("4.5", Decimal("8.5"), Decimal("9.4")),
    ("5.0", Decimal("9.5"), Decimal("10.0")),
)

MOST_LOGGED_LABELS = {
    None: "Most logged",
    MediaTypes.MOVIE.value: "Most watched",
    MediaTypes.TV.value: "Most watched",
    MediaTypes.ANIME.value: "Most watched",
    MediaTypes.BOOK.value: "Most read",
    MediaTypes.MANGA.value: "Most read",
    MediaTypes.COMIC.value: "Most read",
    MediaTypes.GAME.value: "Most played",
    MediaTypes.MUSIC.value: "Most listened",
}

# Each type bucket's community sources, in order of preference (STAT-217).
COMMUNITY_SOURCES = {
    MediaTypes.MOVIE.value: ("letterboxd", "imdb", "tmdb"),
    MediaTypes.TV.value: ("imdb", "tmdb"),
    MediaTypes.ANIME.value: ("mal",),
    MediaTypes.MANGA.value: ("mal",),
    MediaTypes.GAME.value: ("igdb",),
    MediaTypes.BOOK.value: ("hardcover",),
    MediaTypes.MUSIC.value: ("musicbrainz",),
    MediaTypes.COMIC.value: (),
}

STORED_RATING = DecimalField(max_digits=3, decimal_places=1)
# A difference that rounds to "0.0" is a difference of zero (STAT-217, STAT-303).
_ZERO_DIFFERENCE = Decimal("0.05")
# The SQL filter sits just inside the exact threshold, which Python then applies.
_SQL_DIFFERENCE_FLOOR = 0.049


# Ratings chart, STAT-204.


def _bucket_expression():
    """Return a rated log's star bucket, by the bucket its stored value is in."""
    whens = [
        When(rating__lt=next_min, then=Value(bucket_stars))
        for (bucket_stars, _, _), (_, next_min, _) in pairwise(RATING_BUCKETS)
    ]
    return Case(*whens, default=Value(RATING_BUCKETS[-1][0]), output_field=CharField())


def build_ratings(scope):
    """Build rated logs in ten star buckets, with their average (STAT-204).

    One grouped query: count and the exact sum of tenths per (bucket, type bucket).
    """
    rows = (
        scope.entries.filter(rating__isnull=False)
        .annotate(
            stats_bucket=_bucket_expression(),
            stats_type=media_type_bucket("item__media_type"),
        )
        .values("stats_bucket", "stats_type")
        .annotate(
            count=Count("id"),
            tenths=Sum(Round(Cast("rating", FloatField()) * 10), output_field=FloatField()),
        )
        .values_list("stats_bucket", "stats_type", "count", "tenths")
        .order_by()
    )
    by_bucket = {bucket_stars: {} for bucket_stars, _, _ in RATING_BUCKETS}
    rated = 0
    tenths_total = 0
    for bucket_stars, stats_type, count, tenths in rows:
        by_bucket[bucket_stars][stats_type] = by_bucket[bucket_stars].get(stats_type, 0) + count
        rated += count
        tenths_total += round(tenths or 0)
    average = Decimal(tenths_total) / Decimal(rated) / 10 if rated else None
    return {
        "average_stars": stars(average),
        "rated": rated,
        "distribution": [
            {
                "stars": bucket_stars,
                "count": sum(by_bucket[bucket_stars].values()),
                "rating_min": rating_bound(rating_min),
                "rating_max": rating_bound(rating_max),
                "by_type": {
                    media_type: by_bucket[bucket_stars][media_type]
                    for media_type in scope.primary_types
                    if by_bucket[bucket_stars].get(media_type)
                },
            }
            for bucket_stars, rating_min, rating_max in RATING_BUCKETS
        ],
    }


# Highest rated, STAT-205 and STAT-109.


def _rated_item_types(scope):
    """Return the Item types with a title rating: the tab's, without episodes."""
    return [media_type for media_type in scope.item_types if media_type != MediaTypes.EPISODE.value]


def _current_score(scope, media_types):
    """Return an Item's current rating: the score on the owner's latest tracking row."""
    whens = []
    for media_type in media_types:
        latest = (
            apps.get_model("app", media_type)
            .objects.filter(user=scope.owner, item_id=OuterRef("pk"))
            .order_by("-created_at", "-id")
            .values("score")[:1]
        )
        whens.append(When(media_type=media_type, then=Subquery(latest, output_field=STORED_RATING)))
    return Case(*whens, default=Value(None, output_field=STORED_RATING), output_field=STORED_RATING)


def highest_rated_queryset(scope):
    """Return the scope's Items with a title rating, in STAT-205 order.

    Each row is an Item annotated with ``title_rating`` (stored scale), ``rating_day``
    (the rating log's day, None for a current rating) and ``stats_type`` (its type
    bucket). A title rating is the newest rated visible log in the range, by log day
    then ID; in All Time an Item without one falls back to its current rating, for
    every viewer (STAT-002). Seasons are rows of their own; episodes are left out.
    """
    media_types = _rated_item_types(scope)
    rated_logs = scope.entries.filter(rating__isnull=False)
    newest = (
        rated_logs.filter(item_id=OuterRef("pk"))
        .annotate(stats_day=scope.log_day())
        .order_by("-stats_day", "-id")
    )
    candidates = [rated_logs.values("item_id")]
    if scope.is_all_time:
        for media_type in media_types:
            scored = apps.get_model("app", media_type).objects.filter(
                user=scope.owner,
                score__isnull=False,
            )
            candidates.append(scored.values("item_id"))
    items = (
        Item.objects.filter(media_type__in=media_types)
        .filter(id__in=union_ids(*candidates))
        .annotate(
            log_rating=Subquery(newest.values("rating")[:1], output_field=STORED_RATING),
            rating_day=Subquery(newest.values("stats_day")[:1], output_field=DateField()),
            stats_type=media_type_bucket("media_type"),
        )
    )
    if scope.is_all_time:
        title_rating = Coalesce(F("log_rating"), _current_score(scope, media_types), output_field=STORED_RATING)
    else:
        title_rating = F("log_rating")
    return (
        items.annotate(title_rating=title_rating)
        .filter(title_rating__isnull=False)
        .order_by(F("title_rating").desc(), F("rating_day").desc(nulls_last=True), "id")
    )


def title_ratings(scope, item_ids):
    """Return ``{item_id: stored title rating}`` for the given Items in the scope.

    For Top's list items (STAT-207); Items without a title rating are left out.
    """
    return dict(
        highest_rated_queryset(scope)
        .filter(id__in=list(item_ids))
        .values_list("id", "title_rating"),
    )


def _highest_rated_rows(scope, offset, limit):
    rows = highest_rated_queryset(scope).values_list("id", "title_rating", "rating_day")[offset : offset + limit]
    return list(rows)


def _serialize_highest_rated(scope, rows):
    summaries = scope.media_summaries([item_id for item_id, _, _ in rows])
    return [
        {"media": summaries[item_id], "stars": stars(rating), "day": day_string(day)}
        for item_id, rating, day in rows
        if item_id in summaries
    ]


def highest_rated_preview(scope):
    """Return the first 12 Highest rated rows and the total, cached on the scope.

    ``{"total": int, "rows": [(item_id, stored rating, day or None), ...]}``. Top's
    fallback (STAT-208) reads its first 10 rows from here, so both share one query.
    """
    cached = scope.__dict__.get("_highest_rated_preview")
    if cached is None:
        rows = _highest_rated_rows(scope, 0, PREVIEW_LIMIT)
        cached = {"total": highest_rated_queryset(scope).count(), "rows": rows}
        scope.__dict__["_highest_rated_preview"] = cached
    return cached


def highest_rated_items(scope):
    """Return the first 12 Highest rated rows as ``{media, stars, day}`` (STAT-205).

    Top's fallback (STAT-208) takes its first 10 from here.
    """
    return _serialize_highest_rated(scope, highest_rated_preview(scope)["rows"])


def build_highest_rated(scope):
    """Build the first 12 Items by title rating, and the total (STAT-205)."""
    return {"total": highest_rated_preview(scope)["total"], "items": highest_rated_items(scope)}


def highest_rated_page(scope, page, page_size):
    """Return one page of Highest rated, ``{media, stars, day}`` (STAT-205, 310)."""
    rows = _highest_rated_rows(scope, (page - 1) * page_size, page_size)
    return {
        "count": highest_rated_queryset(scope).count(),
        "results": _serialize_highest_rated(scope, rows),
    }


# Rated higher than most, STAT-217.


def _usable_ratings():
    """Return ExternalRating rows usable as everyone's rating (STAT-217)."""
    return ExternalRating.objects.filter(
        status=ExternalRating.Status.AVAILABLE,
        value__isnull=False,
        max_value__gt=0,
    ).filter(Q(vote_count__isnull=True) | Q(vote_count__gte=MIN_VOTE_COUNT))


def _chosen_sources(scope, candidates):
    """Pick each type bucket's source: the first exposed one with data (STAT-217)."""
    wanted = {
        media_type: [source for source in COMMUNITY_SOURCES.get(media_type, ()) if rating_source_is_exposed(source)]
        for media_type in scope.primary_types
    }
    all_sources = {source for sources in wanted.values() for source in sources}
    if not all_sources:
        return {}
    available = set(
        _usable_ratings()
        .filter(rating_source__in=all_sources, item_id__in=candidates.values("id"))
        .annotate(stats_type=media_type_bucket("item__media_type"))
        .values_list("stats_type", "rating_source")
        .distinct()
        .order_by(),
    )
    chosen = {}
    for media_type, sources in wanted.items():
        for source in sources:
            if (media_type, source) in available:
                chosen[media_type] = source
                break
    return chosen


def _compared_rows(scope, candidates, chosen):
    """Return the candidates compared on their type's source, with the difference."""
    source_for_row = Case(
        *[
            When(media_type__in=TV_ITEM_TYPES if media_type == MediaTypes.TV.value else (media_type,), then=Value(source))
            for media_type, source in chosen.items()
        ],
        default=Value(None, output_field=CharField()),
        output_field=CharField(),
    )
    community = _usable_ratings().filter(item_id=OuterRef("pk"), rating_source=OuterRef("stats_source"))
    value_field = DecimalField(max_digits=12, decimal_places=4)
    return (
        candidates.annotate(stats_source=source_for_row)
        .filter(stats_source__isnull=False)
        .annotate(
            everyone_value=Subquery(community.values("value")[:1], output_field=value_field),
            everyone_max=Subquery(community.values("max_value")[:1], output_field=value_field),
        )
        .filter(everyone_value__isnull=False)
        .annotate(
            stats_difference=Cast("title_rating", FloatField()) / 2.0
            - Cast("everyone_value", FloatField()) * 5.0 / Cast("everyone_max", FloatField()),
        )
    )


def _vs_rows(scope, rows):
    """Format compared rows, dropping any whose difference rounds to zero."""
    result = []
    for item_id, rating, value, max_value, source in rows:
        yours = Decimal(str(rating)) / 2
        everyone = Decimal(str(value)) / Decimal(str(max_value)) * 5
        difference = yours - everyone
        if abs(difference) < _ZERO_DIFFERENCE:
            continue
        result.append((item_id, rating, everyone, difference, source))
    summaries = scope.media_summaries([row[0] for row in result])
    return [
        {
            "media": summaries[item_id],
            "stars": stars(rating),
            "everyone_stars": one_decimal(everyone),
            "difference": one_decimal(difference),
            "source": source,
        }
        for item_id, rating, everyone, difference, source in result
        if item_id in summaries
    ]


def build_rated_vs_everyone(scope):
    """Build your stars against everyone's, 5 higher and 5 lower (STAT-217).

    The candidates are every Highest rated row of the scope, with the same title
    ratings. Each type bucket uses one community source; candidates without a usable
    rating from it are left out. A difference that rounds to zero is in neither list.
    """
    candidates = highest_rated_queryset(scope)
    chosen = _chosen_sources(scope, candidates)
    if not chosen:
        return {"sources": [], "higher": [], "lower": []}
    compared = _compared_rows(scope, candidates, chosen)
    fields = ("id", "title_rating", "everyone_value", "everyone_max", "stats_source")
    higher = (
        compared.filter(stats_difference__gt=_SQL_DIFFERENCE_FLOOR)
        .order_by(F("stats_difference").desc(), "id")
        .values_list(*fields)[:VS_EVERYONE_LIMIT]
    )
    lower = (
        compared.filter(stats_difference__lt=-_SQL_DIFFERENCE_FLOOR)
        .order_by(F("stats_difference").asc(), "id")
        .values_list(*fields)[:VS_EVERYONE_LIMIT]
    )
    return {
        "sources": [
            {"media_type": media_type, "source": chosen[media_type], "label": source_label(chosen[media_type])}
            for media_type in scope.primary_types
            if media_type in chosen
        ],
        "higher": _vs_rows(scope, list(higher)),
        "lower": _vs_rows(scope, list(lower)),
    }


# Most logged, STAT-215.


def most_logged_queryset(scope):
    """Return the scope's titles logged at least twice, in STAT-215 order.

    Each row is a title Item annotated with ``times`` and ``last_day``:

    - a non-TV title counts its visible logs in the range, and ``last_day`` is the
      newest of their log days;
    - a TV show (or an episode whose show Item doesn't exist, STAT-102) counts the
      most viewings of any one of its episodes in the scope (STAT-104: dated ones in
      a dated range, fills too in All Time; hidden viewings never), and ``last_day``
      is its newest viewing day.
    """
    non_tv_logs = scope.entries.exclude(item__media_type__in=TV_ITEM_TYPES)
    repeated = non_tv_logs.values("item_id").annotate(times=Count("id")).filter(times__gte=MOST_LOGGED_MIN_TIMES)
    candidates = [repeated.values("item_id")]

    own_logs = non_tv_logs.filter(item_id=OuterRef("pk")).values("item_id")
    times = Subquery(own_logs.annotate(times=Count("id")).values("times")[:1], output_field=IntegerField())
    last_day = Subquery(
        own_logs.annotate(last_day=Max(scope.log_day(), output_field=DateField())).values("last_day")[:1],
        output_field=DateField(),
    )
    if scope.counts_tv:
        # A viewing's title is its show, or the episode itself when the show is missing.
        candidates.append(scope.viewings.annotate(stats_title_id=scope.title_id()).values("stats_title_id"))
        show_viewings = scope.viewings.filter(item__source=OuterRef("source"), item__media_id=OuterRef("media_id"))
        own_viewings = scope.viewings.filter(item_id=OuterRef("pk"))
        times = Case(
            When(media_type=MediaTypes.TV.value, then=_most_viewed_episode(show_viewings)),
            When(media_type=MediaTypes.EPISODE.value, then=_most_viewed_episode(own_viewings)),
            default=times,
            output_field=IntegerField(),
        )
        last_day = Case(
            When(media_type=MediaTypes.TV.value, then=_newest_viewing_day(scope, show_viewings)),
            When(media_type=MediaTypes.EPISODE.value, then=_newest_viewing_day(scope, own_viewings)),
            default=last_day,
            output_field=DateField(),
        )
    return (
        Item.objects.filter(id__in=union_ids(*candidates))
        .annotate(times=times, last_day=last_day)
        .filter(times__gte=MOST_LOGGED_MIN_TIMES)
        .order_by(F("times").desc(), F("last_day").desc(nulls_last=True), "id")
    )


def _most_viewed_episode(viewings):
    """Return the most viewings of any one episode among ``viewings``."""
    per_episode = viewings.values("item_id").annotate(times=Count("id")).order_by("-times").values("times")[:1]
    return Subquery(per_episode, output_field=IntegerField())


def _newest_viewing_day(scope, viewings):
    """Return the newest viewing day among ``viewings``; fills have none."""
    newest = (
        viewings.annotate(stats_day=scope.viewing_day())
        .filter(stats_day__isnull=False)
        .order_by("-stats_day")
        .values("stats_day")[:1]
    )
    return Subquery(newest, output_field=DateField())


def _serialize_most_logged(scope, rows):
    summaries = scope.media_summaries([item_id for item_id, _ in rows])
    return [{"media": summaries[item_id], "times": times} for item_id, times in rows if item_id in summaries]


def build_most_logged(scope):
    """Build titles logged at least twice, folded to the show (STAT-215)."""
    rows = list(most_logged_queryset(scope).values_list("id", "times")[:PREVIEW_LIMIT])
    return {
        "label": MOST_LOGGED_LABELS.get(scope.media_type, "Most logged"),
        "total": most_logged_queryset(scope).count(),
        "items": _serialize_most_logged(scope, rows),
    }


def most_logged_page(scope, page, page_size):
    """Return one page of Most logged, ``{media, times}`` (STAT-215, STAT-310)."""
    offset = (page - 1) * page_size
    rows = list(most_logged_queryset(scope).values_list("id", "times")[offset : offset + page_size])
    return {
        "count": most_logged_queryset(scope).count(),
        "results": _serialize_most_logged(scope, rows),
    }
