from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.serializers.common import media_summary_from_item, prime_collection_items
from api.serializers.tracking import (
    BookActionSerializer,
    BookCompletionSerializer,
    BookJourneyWriteSerializer,
    BookProgressSerializer,
    ConsumeSerializer,
    EpisodeWatchSerializer,
    GameCompletionSerializer,
    GamePlaythroughSerializer,
    GameProgressSerializer,
    TrackingWriteSerializer,
)
from api.services import completion as completion_service
from api.services import diary as diary_service
from api.services import filters as filter_service
from api.services import tracking as tracking_service
from api.views.mixins import MediaExposureMixin
from app.models import BasicMedia, GameSession, MediaTypes, Status


class TrackingListView(MediaExposureMixin, APIView):
    """List tracked media for the current user."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        media_type = request.query_params.get("media_type")
        if not media_type:
            return Response({"media_type": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        status_filter = request.query_params.get("status", "All")
        manager_status_filter = "All" if str(status_filter).lower() == "tracked" else status_filter
        ordering = request.query_params.get("ordering") or request.query_params.get("sort") or "release_date"
        search = request.query_params.get("q")
        manager_sort = ordering if ordering in {"score", "progress", "start_date", "end_date", "title"} else None
        queryset = BasicMedia.objects.get_media_list(
            request.user,
            media_type,
            manager_status_filter,
            manager_sort,
            search=None,
        )
        if str(status_filter).lower() == "tracked":
            queryset = queryset.exclude(status=Status.PLANNING.value)
        rating_scope_queryset = queryset
        filter_service.ensure_filter_metadata(queryset, request.query_params)
        queryset = filter_service.apply_item_filters(queryset, request.query_params)
        if search:
            queryset = queryset.filter(item__title__icontains=search)
        queryset = filter_service.apply_rating_range(queryset, request.query_params, "score")
        queryset = filter_service.apply_watched_range(queryset, request.query_params, "end_date")
        if manager_sort is None or request.query_params.get("direction"):
            queryset = filter_service.order_queryset(
                queryset,
                request.query_params,
                your_rating_field="score",
                default_sort="release_date",
                extra_sorts={
                    "score": "score",
                    "start_date": "start_date",
                    "end_date": "end_date",
                },
                rating_scope_queryset=rating_scope_queryset,
            )
        completion = completion_service.completion_for_items(
            request.user,
            queryset.values_list("item_id", flat=True),
        )
        paginator = StandardResultsSetPagination()
        page = list(paginator.paginate_queryset(queryset, request, view=self))
        BasicMedia.objects.annotate_max_progress(page, media_type)
        prime_collection_items(
            [media.item for media in page],
            request.user,
            media_by_item={media.item_id: media for media in page},
        )
        response = paginator.get_paginated_response(
            [
                {
                    "media": media_summary_from_item(media.item, request=request, user=request.user),
                    "tracking": tracking_service.serialize_tracking(media),
                }
                for media in page
            ],
        )
        response.data["completion"] = completion
        return response


class TrackingDetailView(MediaExposureMixin, APIView):
    """Retrieve, upsert, patch, or delete tracking state."""

    permission_classes = [IsAuthenticated]

    def get(self, request, source, media_type, media_id):
        media = tracking_service.get_tracking(
            request.user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=request.query_params.get("season_number"),
            episode_number=request.query_params.get("episode_number"),
        )
        if media is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(tracking_service.serialize_tracking(media))

    def put(self, request, source, media_type, media_id):
        serializer = TrackingWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        media = tracking_service.create_or_update_tracking(
            request.user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            data=serializer.validated_data,
            partial=False,
        )
        return Response(tracking_service.serialize_tracking(media))

    def patch(self, request, source, media_type, media_id):
        serializer = TrackingWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        media = tracking_service.create_or_update_tracking(
            request.user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            data=serializer.validated_data,
        )
        return Response(tracking_service.serialize_tracking(media))

    def delete(self, request, source, media_type, media_id):
        tracking_service.delete_tracking(
            request.user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=request.query_params.get("season_number"),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class TrackingActionView(MediaExposureMixin, APIView):
    """Generic tracking status actions."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_type, media_id, action):
        if media_type == MediaTypes.GAME.value:
            serializer = BookActionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            media = tracking_service.perform_game_action(request.user, source=source, media_id=media_id, action=action, data=serializer.validated_data)
        elif media_type == MediaTypes.BOOK.value:
            serializer = BookActionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            media = tracking_service.perform_book_action(
                request.user,
                source=source,
                media_id=media_id,
                action=action,
                data=serializer.validated_data,
            )
        elif action == "consume":
            serializer = ConsumeSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            media = tracking_service.consume_media(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                consumed_at=serializer.validated_data.get("consumed_at"),
            )
        elif action == "pause":
            media = tracking_service.set_status(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                status=Status.PAUSED.value,
            )
        elif action == "resume":
            media = tracking_service.set_status(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                status=Status.IN_PROGRESS.value,
            )
        elif action == "drop":
            media = tracking_service.set_status(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                status=Status.DROPPED.value,
            )
        else:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if media is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(tracking_service.serialize_tracking(media))


class TVStartView(APIView):
    """Start tracking a TV show."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id):
        media = tracking_service.set_status(
            request.user,
            source=source,
            media_type=MediaTypes.TV.value,
            media_id=media_id,
            status=Status.IN_PROGRESS.value,
        )
        return Response(tracking_service.serialize_tracking(media))


class SeasonStartView(APIView):
    """Start tracking a season."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id, season_number):
        media = tracking_service.create_or_update_tracking(
            request.user,
            source=source,
            media_type=MediaTypes.SEASON.value,
            media_id=media_id,
            data={"season_number": season_number, "status": Status.IN_PROGRESS.value},
        )
        return Response(tracking_service.serialize_tracking(media))


class SeasonWatchView(APIView):
    """Watch or unwatch a whole season."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id, season_number):
        media = tracking_service.watch_season(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
        )
        return Response(tracking_service.serialize_tracking(media))

    def delete(self, request, source, media_id, season_number):
        media = tracking_service.unwatch_season(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
        )
        return Response(tracking_service.serialize_tracking(media) if media else {}, status=status.HTTP_200_OK)


class EpisodeWatchView(APIView):
    """Watch or unwatch one episode."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id, season_number, episode_number):
        serializer = EpisodeWatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        season = tracking_service.watch_episode(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
            watched_at=serializer.validated_data.get("watched_at"),
        )
        return Response(tracking_service.serialize_tracking(season))

    def delete(self, request, source, media_id, season_number, episode_number):
        season = tracking_service.unwatch_episode(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
        )
        return Response(tracking_service.serialize_tracking(season) if season else {}, status=status.HTTP_200_OK)


class BookProgressView(APIView):
    """Log book progress."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id):
        serializer = BookProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        book = tracking_service.log_book_progress(
            request.user,
            source=source,
            media_id=media_id,
            progress_type=serializer.validated_data["progress_type"],
            value=serializer.validated_data["value"],
            notes=serializer.validated_data.get("notes", ""),
            progressed_on=serializer.validated_data.get("progressed_on"),
        )
        return Response(tracking_service.serialize_tracking(book))


class BookCompleteView(APIView):
    """Save an atomic completion, with legacy undated Mark Read support."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id):
        if "completion_date" not in request.data and "mutation_id" not in request.data:
            media = tracking_service.consume_media(
                request.user,
                source=source,
                media_type=MediaTypes.BOOK.value,
                media_id=media_id,
            )
            return Response(tracking_service.serialize_tracking(media))
        serializer = BookCompletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        media, entry = tracking_service.complete_book(
            request.user,
            source=source,
            media_id=media_id,
            data=serializer.validated_data,
        )
        return Response(
            {
                "tracking": tracking_service.serialize_tracking(media),
                "diary_entry": diary_service.diary_payload(
                    entry,
                    request=request,
                    viewer=request.user,
                ),
            },
            status=status.HTTP_201_CREATED,
        )


class BookJourneyView(APIView):
    """Edit or delete one book reading journey."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, source, media_id, journey_id):
        serializer = BookJourneyWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        book = tracking_service.update_book_journey(
            request.user,
            source=source,
            media_id=media_id,
            journey_id=journey_id,
            data=serializer.validated_data,
        )
        return Response(tracking_service.serialize_tracking(book))

    def delete(self, request, source, media_id, journey_id):
        book = tracking_service.delete_book_journey(
            request.user,
            source=source,
            media_id=media_id,
            journey_id=journey_id,
        )
        if book is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(tracking_service.serialize_tracking(book))


class GameProgressView(APIView):
    """Validate and expose canonical game playthrough state."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id):
        from app import game_tracking

        serializer = GameProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        game = tracking_service._call_book(game_tracking.update_progress, request.user, tracking_service._game_item(request.user, source, media_id), **serializer.validated_data)
        return Response(tracking_service.serialize_tracking(game))


class GameCompleteView(APIView):
    """Validate and expose canonical game playthrough state."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id):
        serializer = GameCompletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        game, entry = tracking_service.complete_game(request.user, source=source, media_id=media_id, data=serializer.validated_data)
        return Response({"tracking": tracking_service.serialize_tracking(game), "diary_entry": diary_service.diary_payload(entry, request=request, viewer=request.user)}, status=status.HTTP_201_CREATED)


class GamePlaythroughView(APIView):
    """Validate and expose canonical game playthrough state."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, source, media_id, playthrough_id):
        get_object_or_404(GameSession, pk=playthrough_id, related_game__user=request.user, related_game__item__source=source, related_game__item__media_id=media_id)
        from app import game_tracking

        serializer = GamePlaythroughSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop("playthrough_id", None)
        game = tracking_service._call_book(game_tracking.update_playthrough, request.user, tracking_service._game_item(request.user, source, media_id), playthrough_id, **data)
        return Response(tracking_service.serialize_tracking(game))

    def delete(self, request, source, media_id, playthrough_id):
        get_object_or_404(GameSession, pk=playthrough_id, related_game__user=request.user, related_game__item__source=source, related_game__item__media_id=media_id)
        from app import game_tracking

        game = tracking_service._call_book(game_tracking.delete_playthrough, request.user, tracking_service._game_item(request.user, source, media_id), playthrough_id)
        return Response(tracking_service.serialize_tracking(game)) if game else Response(status=status.HTTP_204_NO_CONTENT)
