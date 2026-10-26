import logging

from django.contrib.auth import get_user_model, update_session_auth_hash
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.exceptions import BookTrackingConflict as APIBookTrackingConflict
from api.pagination import StandardResultsSetPagination
from api.permissions import can_view_user_profile
from api.serializers.common import (
    MediaRefSerializer,
    find_item,
    get_or_create_item_from_metadata,
    image_url,
    media_summary_from_item,
)
from api.serializers.profile import (
    PasswordChangeSerializer,
    PreferencesSerializer,
    ProfileUpdateSerializer,
    hof_payload,
    preferences_payload,
    profile_payload,
)
from api.services import tracking as tracking_service
from app import book_tracking, exposure, tv_tracking
from app.curation import prime_curated_artwork
from app.models import MediaLike, MediaTypes
from app.providers import services as provider_services
from app.services import set_media_like
from social.models import SocialAuditLog

logger = logging.getLogger(__name__)

MAX_AVATAR_SIZE = 5 * 1024 * 1024
ALLOWED_AVATAR_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

PROFILE_BACKDROP_MEDIA_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.GAME.value,
}


class HOFItemWriteSerializer(serializers.Serializer):
    """Validate Hall of Fame item writes."""

    ref = MediaRefSerializer()


class ProfileBackdropSerializer(serializers.Serializer):
    """Validate profile backdrop writes."""

    ref = MediaRefSerializer()
    backdrop_url = serializers.URLField(max_length=1000)


class MeView(APIView):
    """Current user profile."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(profile_payload(request.user, request=request, viewer=request.user))

    def patch(self, request):
        old_private = request.user.profile_private
        serializer = ProfileUpdateSerializer(data=request.data, partial=True, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(user=request.user)
        if old_private != request.user.profile_private:
            SocialAuditLog.objects.create(
                actor=request.user,
                action="profile_visibility_update",
                target_user=request.user,
                metadata={"from": old_private, "to": request.user.profile_private},
            )
        return Response(profile_payload(request.user, request=request, viewer=request.user))


class LikedMediaView(APIView):
    """Current user's canonical liked media."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        likes = MediaLike.objects.filter(
            user=request.user,
            item__media_type__in=exposure.media_types(),
        ).select_related("item").order_by("-created_at", "-id")
        media_type = request.query_params.get("media_type")
        if media_type:
            exposure.require_media_type(media_type)
            likes = likes.filter(item__media_type=media_type)
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(likes, request, view=self)
        prime_curated_artwork(like.item for like in page)
        return paginator.get_paginated_response(
            [media_summary_from_item(like.item, request=request, user=request.user) for like in page],
        )

    def post(self, request):
        serializer = HOFItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = self._item_for_ref(serializer.validated_data["ref"], create=True)
        if tv_tracking.supports(item):
            return Response(self._tv_like(request, item, liked=True))
        try:
            set_media_like(request.user, item, liked=True)
        except book_tracking.BookTrackingConflict as error:
            api_error = APIBookTrackingConflict(detail=error.message)
            api_error.default_code = error.code
            raise api_error from error
        return Response({"liked": True, "media": media_summary_from_item(item, request=request, user=request.user)})

    def delete(self, request):
        serializer = HOFItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = self._item_for_ref(serializer.validated_data["ref"], create=False)
        if item is not None and tv_tracking.supports(item):
            payload = self._tv_like(request, item, liked=False)
            payload.pop("media")
            return Response(payload)
        if item is not None:
            set_media_like(request.user, item, liked=False)
        return Response({"liked": False})

    @staticmethod
    def _tv_like(request, item, *, liked):
        """Set a show's, season's or episode's heart (TV-303 to TV-305, TV-501).

        On a show the user hasn't started, a heart marks it watched. The response
        carries the page's detail TV state in media.user_state (the app replaces its
        page state with it) and the envelope keys, with the earlier-episodes question.
        """
        result = tracking_service._call_tv(
            tv_tracking.set_like,
            request.user,
            item,
            liked,
            entry_point=tv_tracking.APP,
        )
        envelope = tracking_service.tv_envelope(result, item)
        media = media_summary_from_item(item, request=request, user=request.user)
        media["user_state"]["tv"] = envelope["tv"]
        return {
            "liked": liked,
            "media": media,
            "tv": envelope["tv"],
            "season": envelope["season"],
            "episode": envelope["episode"],
            "earlier_unwatched": envelope["earlier_unwatched"],
        }

    def _item_for_ref(self, ref, *, create):
        exposure.require_media_type(ref["media_type"])
        if tv_tracking.supports(ref["media_type"]):
            # A season ref needs its number, an episode ref both (TV-106).
            tracking_service.require_tv_numbers(
                ref["media_type"],
                ref.get("season_number"),
                ref.get("episode_number"),
            )
        item = find_item(ref)
        if item is not None or not create:
            return item
        metadata = provider_services.get_media_metadata(
            ref["media_type"],
            ref["media_id"],
            ref["source"],
            [ref.get("season_number")] if ref.get("season_number") is not None else None,
            ref.get("episode_number"),
        )
        return get_or_create_item_from_metadata(ref, metadata)


class AvatarView(APIView):
    """Upload current user's avatar."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        avatar = request.FILES.get("avatar")
        if avatar is None:
            return Response({"avatar": ["This field is required."]}, status=status.HTTP_400_BAD_REQUEST)
        if avatar.content_type not in ALLOWED_AVATAR_CONTENT_TYPES:
            return Response({"avatar": ["Upload a JPEG, PNG, or WebP image."]}, status=status.HTTP_400_BAD_REQUEST)
        if avatar.size > MAX_AVATAR_SIZE:
            return Response({"avatar": ["Avatar must be 5 MB or smaller."]}, status=status.HTTP_400_BAD_REQUEST)
        old_avatar = request.user.profile_picture
        request.user.profile_picture = avatar
        try:
            request.user.save(update_fields=["profile_picture"])
        except OSError:
            logger.exception("Failed to save avatar for user: %s", request.user.username)
            return Response(
                {"avatar": ["Could not save your avatar. Please try again later."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if old_avatar and old_avatar.name != request.user.profile_picture.name:
            try:
                old_avatar.delete(save=False)
            except OSError:
                logger.warning("Failed to delete replaced avatar for user: %s", request.user.username)
        return Response({"avatar_url": image_url(request, request.user.profile_picture)})

    def delete(self, request):
        old_avatar = request.user.profile_picture
        request.user.profile_picture = None
        request.user.save(update_fields=["profile_picture"])
        if old_avatar:
            try:
                old_avatar.delete(save=False)
            except OSError:
                logger.warning("Failed to delete avatar for user: %s", request.user.username)
        return Response({"avatar_url": None})


class ProfileBackdropView(APIView):
    """Save or clear the current user's profile backdrop."""

    permission_classes = [IsAuthenticated]

    def put(self, request):
        serializer = ProfileBackdropSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ref = serializer.validated_data["ref"]
        if ref["media_type"] not in PROFILE_BACKDROP_MEDIA_TYPES:
            return Response({"ref": ["Unsupported profile backdrop media type."]}, status=status.HTTP_400_BAD_REQUEST)
        item = find_item(ref)
        if item is None:
            metadata = provider_services.get_media_metadata(
                ref["media_type"],
                ref["media_id"],
                ref["source"],
                [ref.get("season_number")] if ref.get("season_number") is not None else None,
                ref.get("episode_number"),
            )
            item = get_or_create_item_from_metadata(ref, metadata)
        request.user.profile_backdrop_url = serializer.validated_data["backdrop_url"]
        request.user.profile_backdrop_item = item
        request.user.save(update_fields=["profile_backdrop_url", "profile_backdrop_item"])
        return Response(
            {
                "profile_backdrop_url": request.user.profile_backdrop_url,
                "profile_backdrop_item": media_summary_from_item(item, request=request, user=request.user),
            }
        )

    def delete(self, request):
        request.user.profile_backdrop_url = ""
        request.user.profile_backdrop_item = None
        request.user.save(update_fields=["profile_backdrop_url", "profile_backdrop_item"])
        return Response({"profile_backdrop_url": None, "profile_backdrop_item": None})


class PreferencesView(APIView):
    """Mobile preference subset."""

    permission_classes = [IsAuthenticated]

    def patch(self, request):
        serializer = PreferencesSerializer(data=request.data, partial=True, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(user=request.user)
        return Response(preferences_payload(request.user))


class PasswordChangeView(APIView):
    """Change the current user's password."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.save()
        update_session_auth_hash(request, user)
        return Response({"detail": "Password updated."})


class PublicProfileView(APIView):
    """Public profile by username."""

    permission_classes = [AllowAny]

    def get(self, request, username):
        User = get_user_model()
        user = get_object_or_404(User, username=username)
        if not can_view_user_profile(request.user, user):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(profile_payload(user, request=request, viewer=request.user))


class UserSearchView(APIView):
    """Search users by username/display name."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        User = get_user_model()
        query = request.query_params.get("q", "").strip()
        users = User.objects.all()
        if query:
            users = users.filter(username__icontains=query)
        users = [user for user in users[:25] if can_view_user_profile(request.user, user)]
        return Response(
            {
                "results": [
                    {
                        "id": user.id,
                        "username": user.username,
                        "display_name": user.display_name or user.username,
                        "avatar_url": image_url(request, user.profile_picture) if user.profile_picture else None,
                    }
                    for user in users
                ],
            },
        )


class MeHOFView(APIView):
    """Current user's Hall of Fame map."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"items": hof_payload(request.user, request=request)})


class HOFItemView(APIView):
    """Set or clear a Hall of Fame item."""

    permission_classes = [IsAuthenticated]

    def put(self, request, media_type):
        exposure.require_media_type(media_type)
        if media_type not in exposure.primary_media_types():
            return Response({"media_type": ["Unsupported Hall of Fame media type."]}, status=status.HTTP_400_BAD_REQUEST)
        serializer = HOFItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ref = serializer.validated_data["ref"]
        if ref["media_type"] != media_type:
            return Response({"ref": ["media_type must match URL media_type."]}, status=status.HTTP_400_BAD_REQUEST)
        item = find_item(ref)
        if item is None:
            metadata = provider_services.get_media_metadata(
                ref["media_type"],
                ref["media_id"],
                ref["source"],
                [ref.get("season_number")] if ref.get("season_number") is not None else None,
                ref.get("episode_number"),
            )
            item = get_or_create_item_from_metadata(ref, metadata)
        request.user.set_hall_of_fame_item(media_type, item)
        request.user.save(update_fields=[f"hof_{media_type}"])
        return Response({"items": hof_payload(request.user, request=request)})

    def delete(self, request, media_type):
        exposure.require_media_type(media_type)
        if media_type not in exposure.primary_media_types():
            return Response({"media_type": ["Unsupported Hall of Fame media type."]}, status=status.HTTP_400_BAD_REQUEST)
        request.user.clear_hall_of_fame_item(media_type)
        request.user.save(update_fields=[f"hof_{media_type}"])
        return Response({"items": hof_payload(request.user, request=request)})


class UserHOFView(APIView):
    """Public user's Hall of Fame map."""

    permission_classes = [AllowAny]

    def get(self, request, username):
        User = get_user_model()
        user = get_object_or_404(User, username=username)
        if not can_view_user_profile(request.user, user):
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response({"items": hof_payload(user, request=request)})
