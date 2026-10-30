from decimal import Decimal

from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.utils.urls import remove_query_param, replace_query_param
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.permissions import can_view_user_profile
from api.serializers.common import media_summary_from_item
from api.services import stats as stats_service
from api.services import stats_cache, stats_v2_lists, stats_v2_people, stats_v2_ratings
from api.services.stats_scope import COLLECTION_TABS, build_scope
from api.services.stats_v2 import build_stats_v2
from app import statistics as legacy_stats
from app.curation import prime_curated_artwork


def stats_payload(user, request):
    """Build legacy chart keys plus the native-client stats contract."""
    stats_range = stats_service.parse_stats_range(request.query_params, owner=user)
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
        prime_curated_artwork(media.item for media in top_rated)
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


def wants_schema_2(request):
    """Return whether the request asks for the v2 payload (STAT-300)."""
    return request.query_params.get("schema") == "2"


def summary_response(user, request):
    """Answer the v2 payload with schema=2, else the legacy one (STAT-300, STAT-900)."""
    if wants_schema_2(request):
        scope = build_scope(user, request.user, request.query_params, request=request)
        return Response(stats_cache.cached(scope, "summary", lambda: build_stats_v2(scope)))
    return Response(stats_payload(user, request))


def visible_profile_owner(request, username):
    """Return the profile's owner; 404 when unknown or not visible (STAT-002)."""
    user = get_object_or_404(get_user_model(), username=username)
    if not can_view_user_profile(request.user, user):
        raise NotFound
    return user


class MyStatsSummaryView(APIView):
    """Current user's stats summary."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return summary_response(request.user, request)


class UserStatsSummaryView(APIView):
    """Public user's stats summary."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        user = get_object_or_404(get_user_model(), username=username)
        if not can_view_user_profile(request.user, user):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return summary_response(user, request)


def most_logged_response(user, request):
    """Page through every title the user logged at least twice in the range."""
    if wants_schema_2(request):
        scope = build_scope(user, request.user, request.query_params, request=request)
        return paged_response(
            request,
            lambda page, page_size: stats_cache.cached(
                scope,
                "most_logged",
                lambda: stats_v2_ratings.most_logged_page(scope, page, page_size),
                page,
                page_size,
            ),
        )
    stats_range = stats_service.parse_stats_range(request.query_params, owner=user)
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


def highest_rated_response(user, request):
    """Page through Highest rated in STAT-205 order (STAT-310)."""
    scope = build_scope(user, request.user, request.query_params, request=request)
    return paged_response(
        request,
        lambda page, page_size: stats_cache.cached(
            scope,
            "highest_rated",
            lambda: stats_v2_ratings.highest_rated_page(
                scope,
                page,
                page_size,
                exclude_ids=stats_v2_lists.top_item_ids(scope),
            ),
            page,
            page_size,
        ),
    )


class MyStatsHighestRatedView(APIView):
    """Current user's complete Highest rated list, paged."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return highest_rated_response(request.user, request)


class UserStatsHighestRatedView(APIView):
    """Another user's complete Highest rated list, paged."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        return highest_rated_response(visible_profile_owner(request, username), request)


COLLECTION_GROUPS = stats_v2_lists.COLLECTION_GROUPS + stats_v2_lists.IN_RANGE_GROUPS


def collections_response(user, request):
    """Page through one group of one type's collections (STAT-310).

    ``complete`` and ``almost`` are lifetime and ignore dates; ``completed_in_range``
    and ``progressed_in_range`` read the range and need a dated one.
    """
    media_type = request.query_params.get("media_type")
    if media_type not in COLLECTION_TABS:
        raise ValidationError({"media_type": [f"Use one of: {', '.join(COLLECTION_TABS)}."]})
    group = request.query_params.get("group")
    if group not in COLLECTION_GROUPS:
        raise ValidationError({"group": [f"Use one of: {', '.join(COLLECTION_GROUPS)}."]})
    if group in stats_v2_lists.IN_RANGE_GROUPS:
        scope = build_scope(user, request.user, request.query_params, request=request)
        if scope.is_all_time:
            raise ValidationError({"date_range": [f"{group} needs a start_date and an end_date, not all."]})
    else:
        scope = build_scope(
            user,
            request.user,
            {"start_date": "all", "end_date": "all", "media_type": media_type},
            request=request,
        )
    return paged_response(
        request,
        lambda page, page_size: stats_cache.cached(
            scope,
            "collections",
            lambda: stats_v2_lists.collections_page(scope, group, page, page_size),
            group,
            page,
            page_size,
        ),
    )


class MyStatsCollectionsView(APIView):
    """Current user's complete or almost complete collections, paged."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return collections_response(request.user, request)


class UserStatsCollectionsView(APIView):
    """Another user's complete or almost complete collections, paged."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        return collections_response(visible_profile_owner(request, username), request)


def people_response(user, request):
    """Page through one role's people on a people tab (STAT-219, STAT-310)."""
    media_type = request.query_params.get("media_type")
    if media_type not in stats_v2_people.GROUPS:
        raise ValidationError({"media_type": [f"Use one of: {', '.join(stats_v2_people.GROUPS)}."]})
    roles = stats_v2_people.roles_for(media_type)
    role = request.query_params.get("role")
    if role not in roles:
        raise ValidationError({"role": [f"Use one of: {', '.join(roles)}."]})
    sort = request.query_params.get("sort") or stats_v2_people.SORT_TITLES
    if sort not in stats_v2_people.SORTS:
        raise ValidationError({"sort": [f"Use one of: {', '.join(stats_v2_people.SORTS)}."]})
    scope = build_scope(user, request.user, request.query_params, request=request)
    return paged_response(
        request,
        lambda page, page_size: stats_cache.cached(
            scope,
            "people",
            lambda: stats_v2_people.people_page(scope, role, sort, page, page_size),
            role,
            sort,
            page,
            page_size,
        ),
    )


class MyStatsPeopleView(APIView):
    """Current user's people of one role, paged."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return people_response(request.user, request)


class UserStatsPeopleView(APIView):
    """Another user's people of one role, paged."""

    permission_classes = [IsAuthenticated]

    def get(self, request, username):
        return people_response(visible_profile_owner(request, username), request)


def paged_response(request, fetch):
    """Answer one page as ``{count, next, previous, results}`` (STAT-310).

    ``fetch(page, page_size)`` returns ``{"count", "results"}`` for a 1-based page.
    Page and page size follow StandardResultsSetPagination: page_size defaults to 25
    and is capped at 100, and a page that isn't a positive number or is past the last
    page is a 404.
    """
    paginator = StandardResultsSetPagination()
    page_size = paginator.get_page_size(request)
    try:
        page = int(request.query_params.get(paginator.page_query_param, 1))
    except (TypeError, ValueError):
        page = 0
    if page < 1:
        raise NotFound("Invalid page.")
    data = fetch(page, page_size)
    count = data["count"]
    if page > 1 and (page - 1) * page_size >= count:
        raise NotFound("Invalid page.")
    url = request.build_absolute_uri()
    next_url = (
        replace_query_param(url, paginator.page_query_param, page + 1)
        if page * page_size < count
        else None
    )
    if page <= 1:
        previous_url = None
    elif page == 2:
        previous_url = remove_query_param(url, paginator.page_query_param)
    else:
        previous_url = replace_query_param(url, paginator.page_query_param, page - 1)
    return Response({
        "count": count,
        "next": next_url,
        "previous": previous_url,
        "results": data["results"],
    })
