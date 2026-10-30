"""Native-client statistics assembled from locally stored Spine data."""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from dateutil.relativedelta import relativedelta
from django.apps import apps
from django.db.models import (
    Avg,
    Case,
    CharField,
    Count,
    F,
    Max,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from api.serializers.common import media_summary_from_item, prime_collection_items
from api.services import completion as completion_service
from api.services import filters as filter_service
from app import config, exposure, single_weight, user_calendar
from app.models import (
    DiaryEntry,
    Item,
    ItemFilterFacet,
    MediaLike,
    MediaTypes,
    Status,
)
from app.templatetags import app_tags
from social.models import Follow, FollowStatus

TV_DIARY_TYPES = [
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.EPISODE.value,
]

STATUS_KEYS = {
    Status.COMPLETED.value: "completed",
    Status.IN_PROGRESS.value: "in_progress",
    Status.PLANNING.value: "planning",
    Status.PAUSED.value: "paused",
    Status.DROPPED.value: "dropped",
}

RATING_BUCKETS = [Decimal(index) / Decimal(2) for index in range(21)]
SINGLE_WEIGHT_MEDIA_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.MUSIC.value,
    MediaTypes.BOOK.value,
    MediaTypes.GAME.value,
}
TOP_LEVEL_MEDIA_LIMIT = 12
MEDIA_TYPE_MEDIA_LIMIT = 6
MOST_LOGGED_MIN_LOGS = 2
MOST_LOGGED_TYPE_PREVIEW_LIMIT = 8
FACET_LIMIT = 10


def _primary_media_types():
    return exposure.primary_media_types()


@dataclass(frozen=True)
class StatsRange:
    """Validated, inclusive local-date range for diary-backed statistics."""

    start_date: date | None
    end_date: date | None
    start_datetime: datetime | None
    end_datetime: datetime | None
    timezone_name: str

    @property
    def is_all_time(self):
        return self.start_date is None and self.end_date is None

    def payload(self):
        return {
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "timezone": self.timezone_name,
            "is_all_time": self.is_all_time,
        }


def parse_stats_range(params, owner=None):
    """Parse API date parameters without ambiguous or server-erroring fallbacks.

    With an owner, the defaults and the reported timezone are the owner's calendar
    (STAT-005), whoever asks; without one (the legacy payload) they are the
    request's current timezone, as before.
    """
    start_value = params.get("start_date")
    end_value = params.get("end_date")
    if owner is not None:
        current_timezone = user_calendar.calendar_tz(owner)
        timezone_name = current_timezone.key
        today = user_calendar.today(owner)
    else:
        current_timezone = timezone.get_current_timezone()
        timezone_name = timezone.get_current_timezone_name()
        today = timezone.localdate()

    if start_value == "all" or end_value == "all":
        if start_value != "all" or end_value != "all":
            raise ValidationError({
                "date_range": ["Use start_date=all and end_date=all together."],
            })
        return StatsRange(
            start_date=None,
            end_date=None,
            start_datetime=None,
            end_datetime=None,
            timezone_name=timezone_name,
        )

    start_date = _parse_date_param("start_date", start_value) if start_value else today - relativedelta(years=1)
    end_date = _parse_date_param("end_date", end_value) if end_value else today
    if start_date > end_date:
        raise ValidationError({
            "date_range": ["start_date must be on or before end_date."],
        })

    return StatsRange(
        start_date=start_date,
        end_date=end_date,
        start_datetime=timezone.make_aware(datetime.combine(start_date, time.min), current_timezone),
        end_datetime=timezone.make_aware(datetime.combine(end_date, time.max), current_timezone),
        timezone_name=timezone_name,
    )


def _parse_date_param(field, value):
    try:
        parsed = parse_date(value)
    except ValueError:
        # Well formed but not a real day, such as 2025-13-01.
        parsed = None
    if parsed is None:
        raise ValidationError({field: ["Use an ISO 8601 date in YYYY-MM-DD format."]})
    return parsed


def parse_stats_media_type(value):
    """Validate an optional primary media-type filter."""
    if value in (None, ""):
        return None
    if value not in _primary_media_types():
        raise ValidationError({"media_type": ["Use a primary media type."]})
    return value


def ranged_diary_entries(*, user, viewer, stats_range):
    """Return visible diary entries inside the requested stats range."""
    diary_entries = visible_diary_entries(user=user, viewer=viewer)
    if not stats_range.is_all_time:
        # Each log's day: a UTC-midnight carrier keeps its date, a moment is read on
        # the owner's calendar, so every viewer counts the same days.
        diary_entries = diary_entries.filter(
            filter_service.log_day_range_q(user, stats_range.start_date, stats_range.end_date),
        )
    return diary_entries


def most_logged_rows(entries, media_type=None):
    """Titles logged at least twice, most logs first, newest log breaking ties."""
    rows = entries.annotate(stats_media_type=_media_type_bucket("item__media_type"))
    if media_type is not None:
        rows = rows.filter(stats_media_type=media_type)
    return (
        rows.values("item_id", "stats_media_type")
        .annotate(log_count=Count("id"), last_consumed_at=Max("consumed_at"))
        .filter(log_count__gte=MOST_LOGGED_MIN_LOGS)
        .order_by("-log_count", "-last_consumed_at", "item_id")
    )


def serialize_most_logged_rows(rows, request, owner):
    """Serialize one page of most-logged rows with the owner's custom posters."""
    items = _items_with_owner_artwork((row["item_id"] for row in rows), owner)
    return _serialize_ranked_rows(rows, request, items, owner=owner, value_key="log_count")


def build_stats_payload(*, user, viewer, request, stats_range):
    """Build the additive native stats contract without provider API calls."""
    diary_entries = ranged_diary_entries(user=user, viewer=viewer, stats_range=stats_range)

    diary_summary, diary_by_type = _diary_summaries(diary_entries)
    dated_book_reads = diary_entries.filter(
        item__media_type=MediaTypes.BOOK.value,
    ).count()
    undated_book_reads = 0
    if stats_range.is_all_time:
        Book = apps.get_model("app", "Book")
        undated_book_reads = Book.objects.filter(
            user=user,
            completed_manually=True,
        ).count()
    lifetime_book_reads = dated_book_reads + undated_book_reads
    game_completions = diary_entries.filter(item__media_type=MediaTypes.GAME.value).count()
    if stats_range.is_all_time:
        Game = apps.get_model("app", "Game")
        game_completions += Game.objects.filter(user=user, completed_manually=True).count()
    tracking_by_type = _tracking_summaries(user)
    liked_total, likes_by_type = _like_summaries(user)
    activity = _activity_payload(diary_entries, stats_range, owner=user)
    rating_distribution, ratings_by_type = _rating_distributions(diary_entries)
    top_rated, top_rated_by_type = _top_rated_payloads(diary_entries, request, owner=user)
    most_logged, most_logged_by_type, most_logged_totals = _most_logged_payloads(
        diary_entries,
        request,
        owner=user,
    )
    release_years, release_years_by_type = _release_year_payloads(diary_entries)
    facets, facets_by_type, coverage, coverage_by_type = _facet_payloads(diary_entries)

    overview = {
        "tracked_count": sum(value["tracked_count"] for value in tracking_by_type.values()),
        "completed_count": sum(value["completed_count"] for value in tracking_by_type.values()),
        **diary_summary,
        "liked_count": liked_total,
        "active_days": activity["active_days"],
        "current_streak_days": activity["current_streak_days"],
        "longest_streak_days": activity["longest_streak_days"],
    }
    overview["completion"] = completion_service.completion_payload(
        overview["completed_count"],
        overview["tracked_count"],
    )
    if lifetime_book_reads or tracking_by_type[MediaTypes.BOOK.value]["tracked_count"]:
        overview["book_read_count"] = lifetime_book_reads

    if game_completions or tracking_by_type[MediaTypes.GAME.value]["tracked_count"]:
        overview["game_completion_count"] = game_completions

    media_types = []
    for media_type in _primary_media_types():
        diary_values = diary_by_type[media_type]
        tracking_values = tracking_by_type[media_type]
        media_payload = {
            "media_type": media_type,
            "tracked_count": tracking_values["tracked_count"],
            "completed_count": tracking_values["completed_count"],
            "completion": completion_service.completion_payload(
                tracking_values["completed_count"],
                tracking_values["tracked_count"],
            ),
            **diary_values,
            "liked_count": likes_by_type[media_type],
            "statuses": tracking_values["statuses"],
            "rating_distribution": ratings_by_type[media_type],
            "top_rated": top_rated_by_type[media_type],
            "most_logged": most_logged_by_type[media_type],
            "most_logged_total": most_logged_totals[media_type],
            "release_years": release_years_by_type[media_type],
            "top_genres": facets_by_type[media_type][ItemFilterFacet.FacetType.GENRE],
            "top_languages": facets_by_type[media_type][ItemFilterFacet.FacetType.LANGUAGE],
            "metadata_coverage": coverage_by_type[media_type],
        }
        if media_type == MediaTypes.BOOK.value and (
            lifetime_book_reads or tracking_values["tracked_count"]
        ):
            media_payload["read_count"] = lifetime_book_reads
        if media_type == MediaTypes.GAME.value and (game_completions or tracking_values["tracked_count"]):
            media_payload["completion_count"] = game_completions
        media_types.append(media_payload)

    return {
        "schema_version": 1,
        "range": stats_range.payload(),
        "overview": overview,
        "media_types": media_types,
        "activity": activity,
        "rating_distribution": rating_distribution,
        "diary_top_rated": top_rated,
        "most_logged": most_logged,
        "most_logged_total": most_logged_totals["all"],
        "release_years": release_years,
        "top_genres": facets[ItemFilterFacet.FacetType.GENRE],
        "top_languages": facets[ItemFilterFacet.FacetType.LANGUAGE],
        "metadata_coverage": coverage,
        # Kept for installed builds, which decode them but show neither (STAT-900).
        "list_progress": [],
        "series_progress": [],
    }


def legacy_score_distribution(native_payload):
    """Project visibility-filtered diary ratings into the legacy chart shape."""
    datasets = []
    for media in native_payload["media_types"]:
        counts = dict.fromkeys(RATING_BUCKETS, 0)
        for bucket in media["rating_distribution"]:
            counts[Decimal(bucket["rating"])] += bucket["count"]
        datasets.append({
            "label": app_tags.media_type_readable(media["media_type"]),
            "data": [counts[score] for score in RATING_BUCKETS],
            "background_color": config.get_stats_color(media["media_type"]),
        })

    average = native_payload["overview"]["average_rating"]
    return {
        "labels": [f"{score:.1f}" for score in RATING_BUCKETS],
        "datasets": datasets,
        "average_score": float(average) if average is not None else None,
        "total_scored": native_payload["overview"]["rated_count"],
    }


def visible_diary_entries(*, user, viewer):
    """Return entries visible to a viewer, including followers-only when allowed."""
    entries = DiaryEntry.objects.filter(user=user).select_related("item")
    if viewer and viewer.is_authenticated and viewer == user:
        return entries

    visibility = Q(
        item__media_type__in=list(SINGLE_WEIGHT_MEDIA_TYPES),
    ) | Q(
        ~Q(item__media_type__in=list(SINGLE_WEIGHT_MEDIA_TYPES)),
        visibility="public",
    )
    if (
        viewer
        and viewer.is_authenticated
        and Follow.objects.filter(
            from_user=viewer,
            to_user=user,
            status=FollowStatus.ACCEPTED,
        ).exists()
    ):
        visibility |= Q(
            ~Q(item__media_type__in=list(SINGLE_WEIGHT_MEDIA_TYPES)),
            visibility="followers",
        )
    return entries.filter(visibility)


def _empty_diary_summary():
    return {
        "diary_entry_count": 0,
        "unique_logged_count": 0,
        "repeat_count": 0,
        "rated_count": 0,
        "average_rating": None,
        "review_count": 0,
    }


def _diary_summaries(entries):
    review_filter = Q(review__gt="") | Q(review_title__gt="")
    aggregate = entries.aggregate(
        diary_entry_count=Count("id"),
        unique_logged_count=Count("item_id", distinct=True),
        rated_count=Count("id", filter=Q(rating__isnull=False)),
        average_rating=Avg("rating"),
        review_count=Count("id", filter=review_filter),
    )
    # Every type stores ratings out of 10, so the overall average is in stars.
    if aggregate["average_rating"] is not None:
        aggregate["average_rating"] = Decimal(str(aggregate["average_rating"])) / 2
    summary = _normalized_diary_summary(aggregate)

    by_type = {media_type: _empty_diary_summary() for media_type in _primary_media_types()}
    rows = (
        entries.annotate(stats_media_type=_media_type_bucket("item__media_type"))
        .values("stats_media_type")
        .annotate(
            diary_entry_count=Count("id"),
            unique_logged_count=Count("item_id", distinct=True),
            rated_count=Count("id", filter=Q(rating__isnull=False)),
            average_rating=Avg("rating"),
            review_count=Count("id", filter=review_filter),
        )
    )
    for row in rows:
        media_type = row.pop("stats_media_type")
        if media_type in by_type:
            row["average_rating"] = wire_rating(row.get("average_rating"), media_type)
            by_type[media_type] = _normalized_diary_summary(row)
    return summary, by_type


def _normalized_diary_summary(values):
    entry_count = values.get("diary_entry_count") or 0
    unique_count = values.get("unique_logged_count") or 0
    return {
        "diary_entry_count": entry_count,
        "unique_logged_count": unique_count,
        "repeat_count": max(entry_count - unique_count, 0),
        "rated_count": values.get("rated_count") or 0,
        "average_rating": _decimal_string(values.get("average_rating")),
        "review_count": values.get("review_count") or 0,
    }


def _tracking_summaries(user):
    summaries = {
        media_type: {
            "tracked_count": 0,
            "completed_count": 0,
            "statuses": dict.fromkeys(STATUS_KEYS.values(), 0),
        }
        for media_type in _primary_media_types()
    }
    for media_type in _primary_media_types():
        model = apps.get_model("app", media_type)
        latest_pk = (
            model.objects.filter(user=user, item_id=OuterRef("item_id"))
            .order_by("-created_at", "-id")
            .values("pk")[:1]
        )
        rows = (
            model.objects.filter(user=user, pk=Subquery(latest_pk))
            .values("status")
            .annotate(count=Count("id"))
        )
        for row in rows:
            key = STATUS_KEYS.get(row["status"])
            if key is None:
                continue
            summaries[media_type]["statuses"][key] = row["count"]
            summaries[media_type]["tracked_count"] += row["count"]
        summaries[media_type]["completed_count"] = summaries[media_type]["statuses"]["completed"]
    return summaries


def _like_summaries(user):
    by_type = dict.fromkeys(_primary_media_types(), 0)
    rows = (
        MediaLike.objects.filter(user=user)
        .annotate(stats_media_type=_media_type_bucket("item__media_type"))
        .values("stats_media_type")
        .annotate(count=Count("id"))
    )
    for row in rows:
        if row["stats_media_type"] in by_type:
            by_type[row["stats_media_type"]] = row["count"]
    return sum(by_type.values()), by_type


def _activity_payload(entries, stats_range, *, owner):
    rows = list(
        entries.annotate(activity_date=filter_service.log_day_expression(owner))
        .values("activity_date")
        .annotate(count=Count("id"))
        .order_by("activity_date")
    )
    day_counts = {
        row["activity_date"]: row["count"]
        for row in rows
        if row["activity_date"] is not None
    }
    month_counts = defaultdict(int)
    for activity_date, count in day_counts.items():
        month_counts[activity_date.strftime("%Y-%m")] += count

    today = user_calendar.today(owner)
    reference_date = min(stats_range.end_date or today, today)
    current_streak, longest_streak = _streaks(set(day_counts), reference_date)
    weekday_counts = Counter(activity_date.weekday() for activity_date in day_counts)
    most_active_weekday = None
    if weekday_counts:
        weekday = min(
            weekday_counts,
            key=lambda weekday: (-weekday_counts[weekday], weekday),
        )
        active_day_count = weekday_counts[weekday]
        most_active_weekday = {
            "weekday": weekday,
            "name": calendar.day_name[weekday],
            "active_day_count": active_day_count,
            "percentage": round(active_day_count / len(day_counts) * 100, 1),
        }

    return {
        "days": [
            {"date": activity_date.isoformat(), "count": count}
            for activity_date, count in day_counts.items()
        ],
        "months": [
            {"month": month, "count": count}
            for month, count in sorted(month_counts.items())
        ],
        "active_days": len(day_counts),
        "current_streak_days": current_streak,
        "longest_streak_days": longest_streak,
        "most_active_weekday": most_active_weekday,
    }


def _streaks(active_dates, reference_date):
    if not active_dates:
        return 0, 0

    ordered = sorted(active_dates)
    longest = 1
    running = 1
    for previous, current in pairwise(ordered):
        if (current - previous).days == 1:
            running += 1
            longest = max(longest, running)
        else:
            running = 1

    current = 0
    cursor = reference_date
    while cursor in active_dates:
        current += 1
        cursor -= timezone.timedelta(days=1)
    return current, longest


def _rating_distributions(entries):
    overall_counts = Counter()
    by_type_counts = {media_type: Counter() for media_type in _primary_media_types()}
    rows = (
        entries.exclude(rating__isnull=True)
        .annotate(stats_media_type=_media_type_bucket("item__media_type"))
        .values("stats_media_type", "rating")
        .annotate(count=Count("id"))
    )
    for row in rows:
        media_type = row["stats_media_type"]
        bucket = _rating_bucket(wire_rating(row["rating"], media_type))
        overall_counts[bucket] += row["count"]
        if media_type in by_type_counts:
            by_type_counts[media_type][bucket] += row["count"]

    def payload(counts):
        return [
            {"rating": f"{bucket:.1f}", "count": counts[bucket]}
            for bucket in RATING_BUCKETS
        ]

    return payload(overall_counts), {
        media_type: payload(counts)
        for media_type, counts in by_type_counts.items()
    }


def _rating_bucket(value):
    value = Decimal(value)
    bucket = (value * 2).quantize(Decimal(1), rounding=ROUND_HALF_UP) / 2
    return min(max(bucket, RATING_BUCKETS[0]), RATING_BUCKETS[-1])


def wire_rating(value, media_type):
    """Return the user-facing scale without changing legacy storage."""
    if value is None:
        return None
    rating = Decimal(value)
    return rating / 2 if uses_stars(media_type) else rating


def uses_stars(media_type):
    """Return whether this request's legacy stats show a type's ratings in stars."""
    return media_type in SINGLE_WEIGHT_MEDIA_TYPES or single_weight.REQUEST_STARS.get()


def _top_rated_payloads(entries, request, *, owner):
    # Ranked on the stored values, which share one scale; the legacy wire keeps
    # each type's own scale because installed builds guess it from the value.
    rows = list(
        entries.exclude(rating__isnull=True)
        .annotate(stats_media_type=_media_type_bucket("item__media_type"))
        .values("item_id", "stats_media_type")
        .annotate(rating=Max("rating"), last_consumed_at=Max("consumed_at"))
        .order_by("-rating", "-last_consumed_at", "item_id")
    )
    for row in rows:
        row["rating"] = wire_rating(row["rating"], row["stats_media_type"])
    overall_rows = rows[:TOP_LEVEL_MEDIA_LIMIT]
    type_rows = _limited_rows_by_type(rows, MEDIA_TYPE_MEDIA_LIMIT)
    items = _ranked_items(overall_rows, type_rows, owner)
    return _serialize_ranked_rows(overall_rows, request, items, owner=owner, value_key="rating"), {
        media_type: _serialize_ranked_rows(
            type_rows[media_type],
            request,
            items,
            owner=owner,
            value_key="rating",
        )
        for media_type in _primary_media_types()
    }


def _most_logged_payloads(entries, request, *, owner):
    """Preview the most-logged titles; the paged endpoint serves the rest."""
    rows = list(most_logged_rows(entries))
    totals = Counter(row["stats_media_type"] for row in rows)
    overall_rows = rows[:TOP_LEVEL_MEDIA_LIMIT]
    type_rows = _limited_rows_by_type(rows, MOST_LOGGED_TYPE_PREVIEW_LIMIT)
    items = _ranked_items(overall_rows, type_rows, owner)
    by_type = {
        media_type: _serialize_ranked_rows(
            type_rows[media_type],
            request,
            items,
            owner=owner,
            value_key="log_count",
        )
        for media_type in _primary_media_types()
    }
    return (
        _serialize_ranked_rows(overall_rows, request, items, owner=owner, value_key="log_count"),
        by_type,
        {
            "all": len(rows),
            **{media_type: totals[media_type] for media_type in _primary_media_types()},
        },
    )


def _limited_rows_by_type(rows, limit):
    result = {media_type: [] for media_type in _primary_media_types()}
    for row in rows:
        media_type = row["stats_media_type"]
        if media_type in result and len(result[media_type]) < limit:
            result[media_type].append(row)
    return result


def _ranked_items(overall_rows, type_rows, owner):
    item_ids = {row["item_id"] for row in overall_rows}
    for rows in type_rows.values():
        item_ids.update(row["item_id"] for row in rows)
    return _items_with_owner_artwork(item_ids, owner)


def _items_with_owner_artwork(item_ids, owner):
    """Load ranked items with the owner's custom artwork choices prefetched."""
    items = Item.objects.in_bulk(item_ids)
    prime_collection_items(items.values(), owner)
    return items


def _serialize_ranked_rows(rows, request, items, *, owner, value_key):
    result = []
    for row in rows:
        item = items.get(row["item_id"])
        if item is None:
            continue
        payload = {
            # Like Hall of Fame: the owner's custom artwork, never their tracking state.
            "media": media_summary_from_item(
                item,
                request=request,
                user=owner,
                include_user_state=False,
            ),
        }
        if value_key == "rating":
            payload["rating"] = _decimal_string(row[value_key])
        else:
            payload[value_key] = row[value_key]
        result.append(payload)
    return result


def _release_year_payloads(entries):
    logged_item_ids = entries.values("item_id")
    rows = (
        Item.objects.filter(id__in=Subquery(logged_item_ids), release_year__isnull=False)
        .annotate(stats_media_type=_media_type_bucket("media_type"))
        .values("stats_media_type", "release_year")
        .annotate(count=Count("id"))
        .order_by("release_year")
    )
    overall = Counter()
    by_type = {media_type: Counter() for media_type in _primary_media_types()}
    for row in rows:
        overall[row["release_year"]] += row["count"]
        if row["stats_media_type"] in by_type:
            by_type[row["stats_media_type"]][row["release_year"]] += row["count"]

    def payload(counts):
        return [
            {"year": year, "count": counts[year]}
            for year in sorted(counts)
        ]

    return payload(overall), {
        media_type: payload(counts)
        for media_type, counts in by_type.items()
    }


def _facet_payloads(entries):
    logged_item_ids = entries.values("item_id")
    facets = (
        ItemFilterFacet.objects.filter(
            item_id__in=Subquery(logged_item_ids),
            facet_type__in=[ItemFilterFacet.FacetType.GENRE, ItemFilterFacet.FacetType.LANGUAGE],
        )
        .annotate(stats_media_type=_media_type_bucket("item__media_type"))
        .values("stats_media_type", "facet_type", "value")
        .annotate(count=Count("item_id", distinct=True))
    )
    overall_counts = {
        ItemFilterFacet.FacetType.GENRE: Counter(),
        ItemFilterFacet.FacetType.LANGUAGE: Counter(),
    }
    by_type_counts = {
        media_type: {
            ItemFilterFacet.FacetType.GENRE: Counter(),
            ItemFilterFacet.FacetType.LANGUAGE: Counter(),
        }
        for media_type in _primary_media_types()
    }
    for row in facets:
        facet_type = row["facet_type"]
        if facet_type not in overall_counts:
            continue
        overall_counts[facet_type][row["value"]] += row["count"]
        media_type = row["stats_media_type"]
        if media_type in by_type_counts:
            by_type_counts[media_type][facet_type][row["value"]] += row["count"]

    # The legacy payload counts genre and language rows only (no theme rows and so on).
    coverage_rows = (
        ItemFilterFacet.objects.filter(
            item_id__in=Subquery(logged_item_ids),
            facet_type__in=[ItemFilterFacet.FacetType.GENRE, ItemFilterFacet.FacetType.LANGUAGE],
        )
        .annotate(stats_media_type=_media_type_bucket("item__media_type"))
        .values("stats_media_type", "facet_type")
        .annotate(count=Count("item_id", distinct=True))
    )
    coverage_by_type = {
        media_type: {
            "total_items": 0,
            "release_year_items": 0,
            "genre_items": 0,
            "language_items": 0,
        }
        for media_type in _primary_media_types()
    }
    item_coverage_rows = (
        Item.objects.filter(id__in=Subquery(logged_item_ids))
        .annotate(stats_media_type=_media_type_bucket("media_type"))
        .values("stats_media_type")
        .annotate(
            total_items=Count("id"),
            release_year_items=Count("id", filter=Q(release_year__isnull=False)),
        )
    )
    for row in item_coverage_rows:
        media_type = row["stats_media_type"]
        if media_type in coverage_by_type:
            coverage_by_type[media_type]["total_items"] = row["total_items"]
            coverage_by_type[media_type]["release_year_items"] = row["release_year_items"]
    for row in coverage_rows:
        media_type = row["stats_media_type"]
        if media_type not in coverage_by_type:
            continue
        key = "genre_items" if row["facet_type"] == ItemFilterFacet.FacetType.GENRE else "language_items"
        coverage_by_type[media_type][key] = row["count"]

    coverage = {
        key: sum(values[key] for values in coverage_by_type.values())
        for key in ("total_items", "release_year_items", "genre_items", "language_items")
    }

    def facet_payload(counts):
        ranked = sorted(counts.items(), key=lambda value: (-value[1], value[0].casefold()))
        return [
            {"name": name, "count": count}
            for name, count in ranked[:FACET_LIMIT]
        ]

    return (
        {facet_type: facet_payload(counts) for facet_type, counts in overall_counts.items()},
        {
            media_type: {
                facet_type: facet_payload(counts)
                for facet_type, counts in media_counts.items()
            }
            for media_type, media_counts in by_type_counts.items()
        },
        coverage,
        coverage_by_type,
    )


def _media_type_bucket(field):
    return Case(
        When(**{f"{field}__in": TV_DIARY_TYPES}, then=Value(MediaTypes.TV.value)),
        default=F(field),
        output_field=CharField(),
    )


def _decimal_string(value):
    if value is None:
        return None
    decimal_value = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{decimal_value:.2f}".rstrip("0")
    return f"{text}0" if text.endswith(".") else text
