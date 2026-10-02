from decimal import Decimal

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.permissions import can_view_user_profile
from api.serializers.common import media_summary_from_item
from api.services import stats as stats_service
from app import statistics as legacy_stats


def stats_payload(user, request):
    """Build legacy chart keys plus the native-client stats contract."""
    stats_range = stats_service.parse_stats_range(request.query_params)
    native_payload = stats_service.build_stats_payload(
        user=user,
        viewer=request.user,
        request=request,
        stats_range=stats_range,
    )
    user_media, media_count = legacy_stats.get_user_media(
        user,
        stats_range.start_datetime,
        stats_range.end_datetime,
    )
    if request.user == user:
        score_distribution, top_rated = legacy_stats.get_score_distribution(user_media)
        score_distribution = _wire_score_distribution(score_distribution, user_media)
        legacy_top_rated = [
            {
                "media": media_summary_from_item(
                    media.item,
                    request=request,
                    user=None,
                    include_user_state=False,
                ),
                "rating": (
                    str(stats_service.wire_rating(media.score, media.item.media_type))
                    if media.score is not None
                    else None
                ),
            }
            for media in top_rated
        ]
    else:
        # Tracking scores have no per-entry visibility. Public legacy fields
        # therefore project the already-filtered diary data instead.
        score_distribution = stats_service.legacy_score_distribution(native_payload)
        legacy_top_rated = native_payload["diary_top_rated"]
    status_distribution = legacy_stats.get_status_distribution(user_media)
    payload = {
        "start_date": stats_range.start_datetime,
        "end_date": stats_range.end_datetime,
        "media_count": media_count,
        "media_type_distribution": legacy_stats.get_media_type_distribution(media_count),
        "score_distribution": score_distribution,
        "status_distribution": status_distribution,
        "top_rated": legacy_top_rated,
    }
    payload.update(native_payload)
    return payload


def _wire_score_distribution(distribution, user_media):
    """Project legacy tracking-score buckets onto the public rating scale."""
    labels = [f"{Decimal(index) / 2:.1f}" for index in range(21)]
    total = Decimal(0)
    count = 0
    datasets = []
    for (media_type, media_list), dataset in zip(
        user_media.items(),
        distribution["datasets"],
        strict=True,
    ):
        values = [0] * len(labels)
        for storage_bucket, bucket_count in enumerate(dataset["data"]):
            index = storage_bucket if media_type in stats_service.SINGLE_WEIGHT_MEDIA_TYPES else storage_bucket * 2
            values[index] += bucket_count
        datasets.append({**dataset, "data": values})
        for rating in media_list.exclude(score__isnull=True).values_list("score", flat=True):
            total += stats_service.wire_rating(rating, media_type)
            count += 1
    return {
        "labels": labels,
        "datasets": datasets,
        "average_score": float(round(total / count, 2)) if count else None,
        "total_scored": count,
    }


class MyStatsSummaryView(APIView):
    """Current user's stats summary."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(stats_payload(request.user, request))


class UserStatsSummaryView(APIView):
    """Public user's stats summary."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        user = get_object_or_404(get_user_model(), username=username)
        if not can_view_user_profile(request.user, user):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(stats_payload(user, request))


def most_logged_response(user, request):
    """Page through every title the user logged at least twice in the range."""
    stats_range = stats_service.parse_stats_range(request.query_params)
    media_type = stats_service.parse_stats_media_type(request.query_params.get("media_type"))
    entries = stats_service.ranged_diary_entries(
        user=user,
        viewer=request.user,
        stats_range=stats_range,
    )
    paginator = StandardResultsSetPagination()
    page = paginator.paginate_queryset(
        stats_service.most_logged_rows(entries, media_type),
        request,
    )
    return paginator.get_paginated_response(
        stats_service.serialize_most_logged_rows(page, request, user),
    )


class MyStatsMostLoggedView(APIView):
    """Current user's complete most-logged list, paged."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return most_logged_response(request.user, request)


class UserStatsMostLoggedView(APIView):
    """Public user's complete most-logged list, paged."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        user = get_object_or_404(get_user_model(), username=username)
        if not can_view_user_profile(request.user, user):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return most_logged_response(user, request)
