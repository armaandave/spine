from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.permissions import can_view_user_profile
from api.serializers.diary import DiaryEntryWriteSerializer
from api.services import diary as diary_service
from api.services import filters as filter_service
from api.services.social import set_like
from app import exposure, single_weight
from app.models import CustomBackdropPreference, CustomPosterPreference, DiaryEntry
from app.services import delete_diary_entry
from social.models import ContentLike


class DiaryListView(APIView):
    """List or create diary entries."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        viewer_posters = CustomPosterPreference.objects.filter(user=request.user)
        viewer_backdrops = CustomBackdropPreference.objects.filter(user=request.user)
        entries = (
            DiaryEntry.objects.filter(
                user=request.user,
                item__media_type__in=exposure.media_types(),
            )
            .select_related("item", "user")
            .prefetch_related(
                "tags",
                Prefetch(
                    "item__customposterpreference_set",
                    queryset=viewer_posters,
                    to_attr="viewer_custom_poster_preferences",
                ),
                Prefetch(
                    "item__custombackdroppreference_set",
                    queryset=viewer_backdrops,
                    to_attr="viewer_custom_backdrop_preferences",
                ),
            )
            .order_by("-consumed_at", "-id")
        )
        rating_scope_queryset = entries
        item_id = request.query_params.get("item_id")
        tag = request.query_params.get("tag", "").strip().lower()
        has_review = request.query_params.get("has_review") == "true"
        liked = request.query_params.get("liked") == "true"
        if item_id:
            entries = entries.filter(item_id=item_id)
        if tag:
            entries = entries.filter(tags__name=tag)
        if has_review:
            entries = entries.filter(Q(review__gt="") | Q(review_title__gt=""))
        if liked:
            entries = entries.filter(liked=True)
        filter_service.ensure_filter_metadata(entries, request.query_params)
        entries = filter_service.apply_item_filters(entries, request.query_params)
        entries = filter_service.apply_rating_range(entries, request.query_params, "rating")
        entries = filter_service.apply_watched_range(entries, request.query_params, "consumed_at")
        entries = filter_service.apply_user_status_filter(
            entries,
            request.user,
            request.query_params.get("status"),
        )
        entries = filter_service.order_queryset(
            entries,
            request.query_params,
            your_rating_field="rating",
            default_sort="consumed_at",
            extra_sorts={"consumed_at": "consumed_at", "created_at": "created_at"},
            rating_scope_queryset=rating_scope_queryset,
        )

        paginator = StandardResultsSetPagination()
        page = list(paginator.paginate_queryset(entries, request, view=self))
        diary_service.prime_diary_likes(page, request.user)
        return paginator.get_paginated_response(
            [
                diary_service.diary_payload(entry, request=request, viewer=request.user)
                for entry in page
            ],
        )

    def post(self, request):
        serializer = DiaryEntryWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ref = serializer.validated_data.get("ref")
        if ref:
            exposure.require_media_type(ref["media_type"])
        entry = diary_service.create_entry(request.user, serializer.validated_data)
        entry = DiaryEntry.objects.select_related("item", "user").prefetch_related("tags").get(id=entry.id)
        return Response(
            diary_service.diary_payload(entry, request=request, viewer=request.user),
            status=status.HTTP_201_CREATED,
        )


class DiaryDetailView(APIView):
    """Read, update, or delete a diary entry."""

    permission_classes = [IsAuthenticated]

    def get(self, request, entry_id):
        entry = get_object_or_404(
            DiaryEntry.objects.select_related("item", "user").prefetch_related("tags"),
            id=entry_id,
        )
        if entry.user != request.user:
            if not can_view_user_profile(request.user, entry.user):
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not single_weight.uses_half_star_rating(entry.item) and entry.visibility == "private":
                return Response(status=status.HTTP_404_NOT_FOUND)
        exposure.require_media_type(entry.item.media_type)
        return Response(diary_service.diary_payload(entry, request=request, viewer=request.user))

    def patch(self, request, entry_id):
        entry = get_object_or_404(DiaryEntry, id=entry_id, user=request.user)
        exposure.require_media_type(entry.item.media_type)
        serializer = DiaryEntryWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        ref = serializer.validated_data.get("ref")
        if ref:
            exposure.require_media_type(ref["media_type"])
        entry = diary_service.update_entry(entry, serializer.validated_data)
        entry = DiaryEntry.objects.select_related("item", "user").prefetch_related("tags").get(id=entry.id)
        return Response(diary_service.diary_payload(entry, request=request, viewer=request.user))

    def delete(self, request, entry_id):
        entry = get_object_or_404(DiaryEntry, id=entry_id, user=request.user)
        exposure.require_media_type(entry.item.media_type)
        delete_diary_entry(request.user, entry)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DiaryTagsView(APIView):
    """Tag autocomplete for diary entries."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user if request.query_params.get("mine") == "true" else None
        limit = None if request.query_params.get("all") == "true" else 10
        return Response({"results": diary_service.tag_results(request.query_params.get("q", ""), user=user, limit=limit)})


class DiaryLikeView(APIView):
    """Like/unlike a diary entry."""

    permission_classes = [IsAuthenticated]

    def post(self, request, entry_id):
        return Response(
            set_like(
                request.user,
                target_type=ContentLike.DIARY_ENTRY,
                target_id=entry_id,
                liked=True,
            ),
        )

    def delete(self, request, entry_id):
        return Response(
            set_like(
                request.user,
                target_type=ContentLike.DIARY_ENTRY,
                target_id=entry_id,
                liked=False,
            ),
        )
