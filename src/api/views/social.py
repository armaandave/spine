from django.contrib.auth import get_user_model
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.pagination import StandardResultsSetPagination
from api.permissions import can_view_user_profile
from api.serializers.common import user_summary
from api.serializers.social import (
    CommentWriteSerializer,
    LikeSerializer,
    ProgressFixSerializer,
    PushDeviceSerializer,
)
from api.services import activity as activity_service
from api.services import activity_planning
from api.services import social as social_service
from app import exposure
from social.models import Comment, ContentLike, Follow, FollowStatus, PushDevice


class FeedView(APIView):
    """Activity of the people the current user follows, newest first."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return activity_service.feed_response(request)


class UserActivityView(APIView):
    """A profile's activity, newest first; opened from a Stats number, a list of it."""

    permission_classes = [AllowAny]

    def get(self, request, username):
        user = get_object_or_404(get_user_model(), username=username)
        return activity_service.profile_response(request, user)


class ProgressPostView(APIView):
    """A day's progress, as its own page reads it."""

    permission_classes = [IsAuthenticated]

    def get(self, request, progress_id):
        return activity_service.progress_post_response(request, progress_id)

    def patch(self, request, progress_id):
        """Fix an older row's number: its owner only (decision 58)."""
        serializer = ProgressFixSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return activity_service.progress_fix_response(
            request,
            progress_id,
            serializer.validated_data["value"],
            serializer.validated_data.get("unit"),
        )

    def delete(self, request, progress_id):
        """Take back the newest row: its owner, or an admin (decision 52)."""
        return activity_service.progress_delete_response(request, progress_id)


class PlanningDayView(APIView):
    """The titles a person added to Planning on a day: what a Planning row opens."""

    permission_classes = [IsAuthenticated]

    def get(self, request, planning_id):
        return activity_planning.day_response(request, planning_id)


class ProgressPostLikeView(APIView):
    """Like/unlike a day's progress."""

    permission_classes = [IsAuthenticated]

    def post(self, request, progress_id):
        return self.set_like(request, progress_id, liked=True)

    def delete(self, request, progress_id):
        return self.set_like(request, progress_id, liked=False)

    def set_like(self, request, progress_id, *, liked):
        post = activity_service.progress_post(request.user, progress_id)
        return Response(
            social_service.set_like(
                request.user,
                target_type=ContentLike.PROGRESS_POST,
                target_id=post.id,
                liked=liked,
            ),
        )


class PostCommentsView(APIView):
    """List or add comments on one post, oldest first, each followed by its replies."""

    permission_classes = [IsAuthenticated]

    def get(self, request, **target):
        comments = (
            Comment.objects.visible_to(request.user)
            .filter(**self.comment_filter(request, **target))
            .select_related("user", "diary_entry", "progress_post")
            # A thread is its top comment's id, so replies follow it across pages.
            .annotate(thread=Coalesce("parent_id", "id"))
            .order_by("thread", "created_at", "id")
        )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(comments, request, view=self)
        return paginator.get_paginated_response([_comment_payload(comment, request) for comment in page])

    def post(self, request, **target):
        serializer = CommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = social_service.add_comment(
            request.user,
            serializer.validated_data["body"],
            parent_id=serializer.validated_data.get("parent_id"),
            **self.write_target(request, **target),
        )
        return Response(_comment_payload(comment, request), status=status.HTTP_201_CREATED)

    def comment_filter(self, request, **target):
        raise NotImplementedError

    def write_target(self, request, **target):
        raise NotImplementedError


class DiaryCommentsView(PostCommentsView):
    """Comments on a log."""

    def comment_filter(self, request, entry_id):
        return {"diary_entry": self.write_target(request, entry_id)["entry"]}

    def write_target(self, request, entry_id):
        entry = social_service.viewable_diary_entry(request.user, entry_id)
        exposure.require_media_type(entry.item.media_type)
        return {"entry": entry}


class ProgressPostCommentsView(PostCommentsView):
    """Comments on a day's progress."""

    def comment_filter(self, request, progress_id):
        # Reading saves nothing: a day nobody has reacted to has no post yet.
        day = activity_service.progress_day(request.user, progress_id)
        return {
            "progress_post__actor_id": day["actor_id"],
            "progress_post__title_key": day["title_key"],
            "progress_post__day": day["day"],
        }

    def write_target(self, request, progress_id):
        return {"post": activity_service.progress_post(request.user, progress_id)}


class CommentDetailView(APIView):
    """Delete a comment."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, comment_id):
        social_service.delete_comment(request.user, comment_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


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


def _comment_payload(comment, request):
    return {
        "id": comment.id,
        "user": user_summary(comment.user, request=request),
        "body": comment.body,
        "created_at": comment.created_at,
        "can_delete": not comment.is_deleted and social_service.can_delete_comment(request.user, comment),
        "parent_id": comment.parent_id,
        "is_deleted": comment.is_deleted,
    }


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
