"""Native-client statistics assembled from locally stored Spine data."""

from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
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
from django.db.models.functions import TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError

from api.serializers.common import media_summary_from_item
from api.services import completion as completion_service
from app import config, exposure
from app.models import (
    DiaryEntry,
    Item,
    ItemFilterFacet,
    MediaLike,
    MediaSeries,
    MediaTypes,
    Status,
)
from app.templatetags import app_tags
from lists.models import CustomList
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
FACET_LIMIT = 10
PROGRESS_COLLECTION_LIMIT = 25


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


def parse_stats_range(params):
    """Parse API date parameters without ambiguous or server-erroring fallbacks."""
    start_value = params.get("start_date")
    end_value = params.get("end_date")

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
            timezone_name=timezone.get_current_timezone_name(),
        )

    today = timezone.localdate()
    start_date = _parse_date_param("start_date", start_value) if start_value else today - relativedelta(years=1)
    end_date = _parse_date_param("end_date", end_value) if end_value else today
    if start_date > end_date:
        raise ValidationError({
            "date_range": ["start_date must be on or before end_date."],
        })

    current_timezone = timezone.get_current_timezone()
    return StatsRange(
        start_date=start_date,
        end_date=end_date,
        start_datetime=timezone.make_aware(datetime.combine(start_date, time.min), current_timezone),
        end_datetime=timezone.make_aware(datetime.combine(end_date, time.max), current_timezone),
        timezone_name=timezone.get_current_timezone_name(),
    )


def _parse_date_param(field, value):
    parsed = parse_date(value)
    if parsed is None:
        raise ValidationError({field: ["Use an ISO 8601 date in YYYY-MM-DD format."]})
    return parsed


def build_stats_payload(*, user, viewer, request, stats_range):
    """Build the additive native stats contract without provider API calls."""
    diary_entries = visible_diary_entries(user=user, viewer=viewer)
    if not stats_range.is_all_time:
        # These media use UTC midnight as a calendar-date carrier, not an instant.
        calendar_types = Q(item__media_type__in=SINGLE_WEIGHT_MEDIA_TYPES)
        diary_entries = diary_entries.filter(
            (calendar_types & Q(
                consumed_at__gte=datetime.combine(stats_range.start_date, time.min, tzinfo=UTC),
                consumed_at__lte=datetime.combine(stats_range.end_date, time.max, tzinfo=UTC),
            )) | (~calendar_types & Q(
                consumed_at__gte=stats_range.start_datetime,
                consumed_at__lte=stats_range.end_datetime,
            )),
        )

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
    activity = _activity_payload(diary_entries, stats_range)
    rating_distribution, ratings_by_type = _rating_distributions(diary_entries)
    top_rated, top_rated_by_type = _top_rated_payloads(diary_entries, request)
    most_logged, most_logged_by_type = _most_logged_payloads(diary_entries, request)
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

    list_progress = _list_progress(user=user, viewer=viewer, request=request)
    series_progress = _series_progress(user=user, request=request)
    return {
        "schema_version": 1,
        "range": stats_range.payload(),
        "overview": overview,
        "media_types": media_types,
        "activity": activity,
        "rating_distribution": rating_distribution,
        "diary_top_rated": top_rated,
        "most_logged": most_logged,
        "release_years": release_years,
        "top_genres": facets[ItemFilterFacet.FacetType.GENRE],
        "top_languages": facets[ItemFilterFacet.FacetType.LANGUAGE],
        "metadata_coverage": coverage,
        "list_progress": list_progress,
        "series_progress": series_progress,
    }


def _list_progress(*, user, viewer, request):
    """Return completion for visible owned and featured media lists."""
    lists = (
        CustomList.objects.filter(list_type=CustomList.ListType.MEDIA)
        .filter(
            Q(owner=user)
            | Q(
                is_featured=True,
                visibility=CustomList.Visibility.PUBLIC,
            ),
        )
        .select_related("owner")
        .prefetch_related("items")
        .distinct()
    )
    if viewer != user:
        lists = lists.exclude(
            owner=user,
            visibility=CustomList.Visibility.PRIVATE,
        ).exclude(
            owner=user,
            visibility=CustomList.Visibility.UNLISTED,
        )

    progress = []
    for custom_list in lists:
        items = [
            item
            for item in custom_list.items.all()
            if item.media_type in exposure.media_types()
        ]
        completion = completion_service.completion_for_items(user, items)
        if not completion or completion["total_count"] == 0:
            continue
        media_types = {item.media_type for item in items}
        posters = []
        for item in items[:3]:
            summary = media_summary_from_item(
                item,
                request=request,
                user=user,
                include_user_state=False,
            )
            poster = summary.get("custom_poster_url") or summary.get("poster_url")
            if poster:
                posters.append(poster)
        progress.append({
            "id": custom_list.id,
            "name": custom_list.name,
            "media_type": (
                next(iter(media_types))
                if len(media_types) == 1
                else None
            ),
            "poster_urls": posters,
            "completion": completion,
        })
    return _sorted_progress(progress)[:PROGRESS_COLLECTION_LIMIT]


def _series_progress(*, user, request):
    """Return completion for persisted series touched by the stats subject."""
    tracked_ids = completion_service.tracked_item_ids(user)
    if not tracked_ids:
        return []
    series_rows = (
        MediaSeries.objects.filter(items__id__in=tracked_ids)
        .prefetch_related("memberships__item")
        .distinct()
    )
    progress = []
    for series in series_rows:
        memberships = list(series.memberships.all())
        items = [membership.item for membership in memberships]
        completion = completion_service.completion_for_items(user, items)
        if not completion or completion["total_count"] == 0:
            continue
        posters = []
        for item in items[:3]:
            summary = media_summary_from_item(
                item,
                request=request,
                user=user,
                include_user_state=False,
            )
            poster = summary.get("custom_poster_url") or summary.get("poster_url")
            if poster:
                posters.append(poster)
        progress.append({
            "id": series.series_id,
            "source": series.source,
            "media_type": series.media_type,
            "name": series.name,
            "item_count": completion["total_count"],
            "poster_urls": posters,
            "completion": completion,
        })
    return _sorted_progress(progress)[:PROGRESS_COLLECTION_LIMIT]


def _sorted_progress(progress):
    return sorted(
        progress,
        key=lambda value: (
            -(
                value["completion"]["completed_count"]
                / value["completion"]["total_count"]
            ),
            value["name"].casefold(),
        ),
    )


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
    rating_values = [
        wire_rating(rating, media_type)
        for media_type, rating in entries.exclude(rating__isnull=True).values_list(
            "item__media_type",
            "rating",
        )
    ]
    aggregate["average_rating"] = (
        sum(rating_values, Decimal(0)) / len(rating_values)
        if rating_values
        else None
    )
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


def _activity_payload(entries, stats_range):
    rows = list(
        entries.annotate(
            activity_date=Case(
                When(item__media_type__in=SINGLE_WEIGHT_MEDIA_TYPES, then=TruncDate("consumed_at", tzinfo=UTC)),
                default=TruncDate("consumed_at", tzinfo=timezone.get_current_timezone()),
            ),
        )
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

    reference_date = stats_range.end_date or timezone.localdate()
    reference_date = min(reference_date, timezone.localdate())
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
    return rating / 2 if media_type in SINGLE_WEIGHT_MEDIA_TYPES else rating


def _top_rated_payloads(entries, request):
    rows = list(
        entries.exclude(rating__isnull=True)
        .annotate(stats_media_type=_media_type_bucket("item__media_type"))
        .values("item_id", "stats_media_type")
        .annotate(rating=Max("rating"), last_consumed_at=Max("consumed_at"))
    )
    for row in rows:
        row["rating"] = wire_rating(row["rating"], row["stats_media_type"])
    rows.sort(
        key=lambda row: (
            -Decimal(row["rating"]),
            -row["last_consumed_at"].timestamp(),
            row["item_id"],
        ),
    )
    overall_rows = rows[:TOP_LEVEL_MEDIA_LIMIT]
    type_rows = _limited_rows_by_type(rows, MEDIA_TYPE_MEDIA_LIMIT)
    items = _ranked_items(overall_rows, type_rows)
    return _serialize_ranked_rows(overall_rows, request, items, value_key="rating"), {
        media_type: _serialize_ranked_rows(
            type_rows[media_type],
            request,
            items,
            value_key="rating",
        )
        for media_type in _primary_media_types()
    }


def _most_logged_payloads(entries, request):
    rows = list(
        entries.annotate(stats_media_type=_media_type_bucket("item__media_type"))
        .values("item_id", "stats_media_type")
        .annotate(log_count=Count("id"), last_consumed_at=Max("consumed_at"))
    )
    rows.sort(
        key=lambda row: (
            -row["log_count"],
            -row["last_consumed_at"].timestamp(),
            row["item_id"],
        ),
    )
    overall_rows = rows[:TOP_LEVEL_MEDIA_LIMIT]
    type_rows = _limited_rows_by_type(rows, MEDIA_TYPE_MEDIA_LIMIT)
    items = _ranked_items(overall_rows, type_rows)
    return _serialize_ranked_rows(overall_rows, request, items, value_key="log_count"), {
        media_type: _serialize_ranked_rows(
            type_rows[media_type],
            request,
            items,
            value_key="log_count",
        )
        for media_type in _primary_media_types()
    }


def _limited_rows_by_type(rows, limit):
    result = {media_type: [] for media_type in _primary_media_types()}
    for row in rows:
        media_type = row["stats_media_type"]
        if media_type in result and len(result[media_type]) < limit:
            result[media_type].append(row)
    return result


def _ranked_items(overall_rows, type_rows):
    item_ids = {row["item_id"] for row in overall_rows}
    for rows in type_rows.values():
        item_ids.update(row["item_id"] for row in rows)
    return Item.objects.in_bulk(item_ids)


def _serialize_ranked_rows(rows, request, items, *, value_key):
    result = []
    for row in rows:
        item = items.get(row["item_id"])
        if item is None:
            continue
        payload = {
            "media": media_summary_from_item(
                item,
                request=request,
                user=None,
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
        ItemFilterFacet.objects.filter(item_id__in=Subquery(logged_item_ids))
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

    coverage_rows = (
        ItemFilterFacet.objects.filter(item_id__in=Subquery(logged_item_ids))
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
