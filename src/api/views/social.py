from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.permissions import can_view_user_profile
from api.serializers.common import user_summary
from api.serializers.social import LikeSerializer, PushDeviceSerializer
from api.services import activity as activity_service
from api.services import social as social_service
from app import exposure
from social.models import Follow, FollowStatus, PushDevice


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
        requests = (
            Follow.objects.filter(to_user=request.user, status=FollowStatus.PENDING)
            .select_related("from_user")
            .order_by("-created_at")
        )
        return Response(
            {
                "results": [
                    {
                        "id": follow.id,
                        "from_user": user_summary(follow.from_user, request=request),
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
        if action not in {"accept", "reject"}:
            return Response(status=status.HTTP_404_NOT_FOUND)
        social_service.answer_follow_request(request.user, request_id, accept=action == "accept")
        return Response({"state": "following" if action == "accept" else "rejected"})


class FollowListView(APIView):
    """A user's followers or the people they follow, newest first."""

    permission_classes = [IsAuthenticated]
    direction = None  # "followers" or "following"

    def get(self, request, username):
        target = get_object_or_404(get_user_model(), username=username)
        if not can_view_user_profile(request.user, target):
            return Response(status=status.HTTP_404_NOT_FOUND)
        edges = Follow.objects.filter(status=FollowStatus.ACCEPTED)
        if self.direction == "followers":
            edges = edges.filter(to_user=target).select_related("from_user")
            person = "from_user"
        else:
            edges = edges.filter(from_user=target).select_related("to_user")
            person = "to_user"
        # Nobody across a block from the viewer shows up.
        edges = (
            edges.exclude(**{f"{person}__blocks_sent__blocked": request.user})
            .exclude(**{f"{person}__blocks_received__blocker": request.user})
            .order_by("-created_at", "-id")
        )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(edges, request, view=self)
        users = [getattr(edge, person) for edge in page]
        return paginator.get_paginated_response(_user_rows(request, users))


class RemoveFollowerView(APIView):
    """Stop someone following you."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, username):
        social_service.remove_follower(request.user, username)
        return Response({"state": "removed"})


class ReportView(APIView):
    """Report a user to the project owner."""

    permission_classes = [IsAuthenticated]

    def post(self, request, username):
        social_service.report_user(request.user, username)
        return Response({"reported": True}, status=status.HTTP_201_CREATED)


class PushDeviceView(APIView):
    """Register this phone for push notifications, or stop them on sign out."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PushDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        PushDevice.objects.update_or_create(
            token=data["token"],
            defaults={"user": request.user, "bundle_id": data["bundle_id"], "environment": data["environment"]},
        )
        return Response({"registered": True})

    def delete(self, request, token):
        PushDevice.objects.filter(user=request.user, token=token).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _user_rows(request, users):
    """Serialize people with how the viewer is connected to each."""
    viewer = request.user
    ids = [user.id for user in users]
    following = dict(
        Follow.objects.filter(from_user=viewer, to_user_id__in=ids).values_list("to_user_id", "status"),
    )
    followed_by = set(
        Follow.objects.filter(to_user=viewer, from_user_id__in=ids, status=FollowStatus.ACCEPTED).values_list(
            "from_user_id",
            flat=True,
        ),
    )
    return [
        {
            **user_summary(user, request=request),
            "is_private": user.profile_private,
            "viewer_relationship": {
                "following": following.get(user.id) == FollowStatus.ACCEPTED,
                "requested": following.get(user.id) == FollowStatus.PENDING,
                "followed_by": user.id in followed_by,
            },
        }
        for user in users
    ]


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
