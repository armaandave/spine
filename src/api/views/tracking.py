from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.exceptions import TVValidationError
from api.pagination import StandardResultsSetPagination
from api.serializers.common import (
    media_summary_from_item,
    prime_collection_items,
    tv_season_started,
)
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
    TVActionSerializer,
)
from api.services import completion as completion_service
from api.services import diary as diary_service
from api.services import filters as filter_service
from api.services import tracking as tracking_service
from api.views.mixins import MediaExposureMixin
from app import tv_tracking
from app.models import BasicMedia, Episode, GameSession, MediaTypes, Status
from streaming import filters as streaming_filters


class TrackingListView(MediaExposureMixin, APIView):
    """List tracked media for the current user."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        media_type = request.query_params.get("media_type")
        if not media_type:
            return Response({"media_type": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        status_filter = request.query_params.get("status", "All")
        manager_status_filter = "All" if str(status_filter).lower() == "tracked" else status_filter
        # Continue Watching (TV-701): shows in Watching with an aired up next that
        # aren't caught up, most recently watched first; `ordering` is ignored.
        continue_watching = media_type == MediaTypes.TV.value and str(
            request.query_params.get("continue_watching", ""),
        ).lower() in {"true", "1"}
        if continue_watching:
            manager_status_filter = Status.IN_PROGRESS.value
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
        queryset = _tv_list_filters(queryset, media_type, continue_watching=continue_watching)
        if media_type == MediaTypes.TV.value:
            queryset = _tv_viewing_dates(queryset)
        rating_scope_queryset = queryset
        filter_service.ensure_filter_metadata(queryset, request.query_params)
        queryset = filter_service.apply_item_filters(queryset, request.query_params)
        if search:
            queryset = queryset.filter(item__title__icontains=search)
        queryset = filter_service.apply_rating_range(queryset, request.query_params, "score")
        queryset = filter_service.apply_watched_range(queryset, request.query_params, "end_date")
        queryset, streaming = streaming_filters.apply_service_filter(
            queryset,
            request.query_params,
            user=request.user,
            request=request,
        )
        if continue_watching:
            queryset = queryset.order_by(
                F("last_activity_on").desc(nulls_last=True),
                F("last_activity_at").desc(nulls_last=True),
                "-id",
            )
        elif manager_sort is None or request.query_params.get("direction"):
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
        _prime_progress(page, media_type)
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
        if streaming is not None:
            response.data["streaming"] = streaming
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
        if tv_tracking.supports(media_type):
            # A season that isn't started is untracked whatever it stores (TV-205).
            if media_type == MediaTypes.SEASON.value and not tv_season_started(media):
                return Response(status=status.HTTP_404_NOT_FOUND)
            return Response(tracking_service.serialize_tracking(media, tv_detail=True))
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
        if tv_tracking.supports(media_type):
            return Response(_tv_written_response(media, media_type))
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
        if tv_tracking.supports(media_type):
            return Response(_tv_written_response(media, media_type))
        return Response(tracking_service.serialize_tracking(media))

    def delete(self, request, source, media_type, media_id):
        tracking_service.delete_tracking(
            request.user,
            source=source,
            media_type=media_type,
            media_id=media_id,
            season_number=request.query_params.get("season_number"),
            episode_number=request.query_params.get("episode_number"),
            expected_version=request.query_params.get("expected_version"),
            mutation_id=request.query_params.get("mutation_id"),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class TrackingActionView(MediaExposureMixin, APIView):
    """Generic tracking status actions."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_type, media_id, action):
        if media_type == MediaTypes.TV.value:
            return self._tv_action(request, source, media_id, action)
        if tv_tracking.supports(media_type):
            return self._tv_part_action(request, source, media_type, media_id, action)
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

    def _tv_action(self, request, source, media_id, action):
        """Every TV action of plan 2.3.7; unknown actions are 404 as before."""
        if action not in tracking_service.TV_ACTIONS:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = TVActionSerializer(data=request.data)
        if not serializer.is_valid():
            raise TVValidationError(detail=serializer.errors)
        result = tracking_service.perform_tv_action(
            request.user,
            source=source,
            media_id=media_id,
            action=action,
            data=serializer.validated_data,
        )
        page = tracking_service.tv_page_item(
            result.show_item,
            serializer.validated_data.get("page_season_number"),
            serializer.validated_data.get("page_episode_number"),
        )
        body = tracking_service.tv_action_response(result, page=page)
        if action in tracking_service.TV_PRE_CONTRACT_ACTIONS and body["tracking"] is not None:
            # These routes returned a top-level TrackingState before the contract;
            # keep it beside the new keys, as PATCH does (TV-900).
            body = {**body["tracking"], **body}
        return Response(body)

    def _tv_part_action(self, request, source, media_type, media_id, action):
        """Apply consume, pause, resume or drop to a season or episode (TV-207)."""
        season_number = request.data.get("season_number", request.query_params.get("season_number"))
        episode_number = request.data.get("episode_number", request.query_params.get("episode_number"))
        if action == "consume":
            result = tracking_service.consume_media(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                season_number=season_number,
                episode_number=episode_number,
            )
        elif action in ("pause", "resume", "drop"):
            result = tracking_service.set_status(
                request.user,
                source=source,
                media_type=media_type,
                media_id=media_id,
                status={
                    "pause": Status.PAUSED.value,
                    "resume": Status.IN_PROGRESS.value,
                    "drop": Status.DROPPED.value,
                }[action],
                season_number=season_number,
            )
        else:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(
            tracking_service.tv_tracking_response(result, row=tracking_service.tv_season_row(result)),
        )


def _tv_list_filters(queryset, media_type, *, continue_watching):
    """Leave out unstarted seasons; Continue Watching lists shows (TV-205, TV-701)."""
    if media_type == MediaTypes.SEASON.value:
        queryset = _started_seasons(queryset)
    if continue_watching:
        # Shows not yet worked out are absent until the repair or the user's next write.
        queryset = queryset.filter(
            legacy_pending=False,
            caught_up=False,
            up_next_season__isnull=False,
        )
    return queryset


def _tv_viewing_dates(queryset):
    """Read each show's first and last viewing time in SQL (TV-207, TV-701).

    The list's start_date and end_date need only these two values, so the page doesn't
    load every viewing of every show; get_media_list's seasons and episodes prefetch is
    dropped. Specials don't count, as in the TV model's start_date and end_date.
    """
    viewings = Episode.objects.filter(
        related_season__related_tv=OuterRef("pk"),
        end_date__isnull=False,
    ).exclude(related_season__item__season_number=0)
    return queryset.prefetch_related(None).annotate(
        tv_first_viewing_end=Subquery(viewings.order_by("end_date").values("end_date")[:1]),
        tv_last_viewing_end=Subquery(viewings.order_by("-end_date").values("end_date")[:1]),
    )


def _prime_progress(page, media_type):
    """Set list progress: stored TV counts (TV-104, TV-207), else calendar events."""
    if media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        # One query for rows not yet worked out; no provider calls.
        tv_tracking.prime_progress(page)
    else:
        BasicMedia.objects.annotate_max_progress(page, media_type)


def _started_seasons(queryset):
    """Leave out seasons that aren't started (TV-205, TV-900).

    A worked-out season says so in is_started; one not yet worked out counts as started
    when it has a viewing, or is stored Completed (the legacy rule's mark, TV-801).
    """
    has_viewing = Exists(Episode.objects.filter(related_season=OuterRef("pk")))
    return queryset.filter(
        Q(is_started=True)
        | (Q(is_started__isnull=True) & (Q(status=Status.COMPLETED.value) | has_viewing)),
    )


def _tv_written_response(result, media_type):
    """Return a show's or season's TrackingState plus the envelope keys (TV-207)."""
    if media_type == MediaTypes.TV.value:
        row = result.tv
    else:
        row = tracking_service.tv_season_row(result)
    return tracking_service.tv_tracking_response(result, row=row)


class TVStartView(APIView):
    """Start tracking a TV show: choose Watching (TV-005)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id):
        result = tracking_service.set_status(
            request.user,
            source=source,
            media_type=MediaTypes.TV.value,
            media_id=media_id,
            status=Status.IN_PROGRESS.value,
        )
        return Response(tracking_service.tv_tracking_response(result, row=result.tv))


class SeasonStartView(APIView):
    """Choose Watching for the show from a season, creating no season (TV-207)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id, season_number):
        result = tracking_service.set_status(
            request.user,
            source=source,
            media_type=MediaTypes.SEASON.value,
            media_id=media_id,
            status=Status.IN_PROGRESS.value,
            season_number=season_number,
        )
        season = tracking_service.tv_season_row(result, season_number)
        row = season if season is not None and tv_season_started(season) else result.tv
        return Response(tracking_service.tv_tracking_response(result, row=row))


class SeasonWatchView(APIView):
    """Mark a whole season watched, or undo its mark (TV-300, TV-302)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id, season_number):
        result = tracking_service.watch_season(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
        )
        return Response(
            tracking_service.tv_tracking_response(result, row=tracking_service.tv_season_row(result)),
        )

    def delete(self, request, source, media_id, season_number):
        result = tracking_service.unwatch_season(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
        )
        if result is None:
            return Response({}, status=status.HTTP_200_OK)
        return Response(
            tracking_service.tv_tracking_response(result, row=tracking_service.tv_season_row(result)),
            status=status.HTTP_200_OK,
        )


class EpisodeWatchView(APIView):
    """Tick or untick one episode (TV-100, TV-102); installed builds call the POST."""

    permission_classes = [IsAuthenticated]

    def post(self, request, source, media_id, season_number, episode_number):
        serializer = EpisodeWatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = tracking_service.watch_episode(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
            watched_at=serializer.validated_data.get("watched_at"),
        )
        return Response(
            tracking_service.tv_tracking_response(result, row=tracking_service.tv_season_row(result)),
        )

    def delete(self, request, source, media_id, season_number, episode_number):
        result = tracking_service.unwatch_episode(
            request.user,
            source=source,
            media_id=media_id,
            season_number=season_number,
            episode_number=episode_number,
        )
        if result is None:
            return Response({}, status=status.HTTP_200_OK)
        return Response(
            tracking_service.tv_tracking_response(result, row=tracking_service.tv_season_row(result)),
            status=status.HTTP_200_OK,
        )


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
