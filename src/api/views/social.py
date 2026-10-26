from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.social import LikeSerializer
from api.services import activity as activity_service
from api.services import social as social_service
from app import exposure
from social.models import Follow, FollowStatus


class FeedView(APIView):
    """Activity of the people the current user follows, newest first."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        logs, progress = activity_service.feed_sources(request.user)
        media_types = exposure.media_types()
        media_type = request.query_params.get("media_type")
        if media_type:
            exposure.require_media_type(media_type)
            media_types = activity_service.media_type_family(media_type)
        return activity_service.page_response(
            request,
            logs.filter(item__media_type__in=media_types),
            progress.filter(item__media_type__in=media_types),
        )


class UserActivityView(APIView):
    """A profile's activity, newest first."""

    permission_classes = [AllowAny]

    def get(self, request, username):
        user = get_object_or_404(get_user_model(), username=username)
        sources = activity_service.profile_sources(request.user, user)
        if sources is None:
            return Response({"next_cursor": None, "previous_cursor": None, "results": []})
        logs, progress = sources
        media_types = exposure.media_types()
        return activity_service.page_response(
            request,
            logs.filter(item__media_type__in=media_types),
            progress.filter(item__media_type__in=media_types),
        )


class FollowView(APIView):
    """Follow/unfollow a user."""

    permission_classes = [IsAuthenticated]

    def post(self, request, username):
        follow = social_service.follow_user(request.user, username)
        return Response({"state": "requested" if follow.status == FollowStatus.PENDING else "following"})

    def delete(self, request, username):
        social_service.unfollow_user(request.user, username)
        return Response({"state": "none"})


class FollowRequestsView(APIView):
    """Inbound follow requests."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        requests = Follow.objects.filter(to_user=request.user, status=FollowStatus.PENDING).select_related("from_user")
        return Response(
            {
                "results": [
                    {
                        "id": follow.id,
                        "from_user": {
                            "id": follow.from_user.id,
                            "username": follow.from_user.username,
                            "display_name": follow.from_user.display_name or follow.from_user.username,
                        },
                        "created_at": follow.created_at,
                    }
                    for follow in requests
                ],
            },
        )


class FollowRequestActionView(APIView):
    """Accept or reject a follow request."""

    permission_classes = [IsAuthenticated]

    def post(self, request, request_id, action):
        follow = get_object_or_404(Follow, id=request_id, to_user=request.user, status=FollowStatus.PENDING)
        if action == "accept":
            follow.status = FollowStatus.ACCEPTED
            follow.save(update_fields=["status", "updated_at"])
            return Response({"state": "following"})
        if action == "reject":
            follow.delete()
            return Response({"state": "rejected"})
        return Response(status=status.HTTP_404_NOT_FOUND)


class BlockView(APIView):
    """Block/unblock a user."""

    permission_classes = [IsAuthenticated]

    def post(self, request, username):
        social_service.block_user(request.user, username)
        return Response({"blocked": True})

    def delete(self, request, username):
        social_service.unblock_user(request.user, username)
        return Response({"blocked": False})


class GenericLikeView(APIView):
    """Generic like endpoint."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LikeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(social_service.set_like(request.user, liked=True, **serializer.validated_data))

    def delete(self, request):
        serializer = LikeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(social_service.set_like(request.user, liked=False, **serializer.validated_data))
