from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404

from api.permissions import can_view_user_profile, users_blocked
from app import single_weight
from app.models import DiaryEntry
from lists.models import CustomList
from social.models import (
    Block,
    ContentLike,
    Follow,
    FollowStatus,
    SocialAuditLog,
    Visibility,
)


def follow_user(actor, username):
    """Follow or request to follow a user."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    status = FollowStatus.PENDING if target.profile_private else FollowStatus.ACCEPTED
    follow, _ = Follow.objects.update_or_create(
        from_user=actor,
        to_user=target,
        defaults={"status": status},
    )
    SocialAuditLog.objects.create(
        actor=actor,
        action="follow" if status == FollowStatus.ACCEPTED else "follow_request",
        target_user=target,
    )
    return follow


def unfollow_user(actor, username):
    """Remove a follow edge."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    Follow.objects.filter(from_user=actor, to_user=target).delete()
    SocialAuditLog.objects.create(actor=actor, action="unfollow", target_user=target)


def block_user(actor, username):
    """Block a user and remove follow edges both ways."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    with transaction.atomic():
        block, _ = Block.objects.get_or_create(blocker=actor, blocked=target)
        Follow.objects.filter(from_user__in=[actor, target], to_user__in=[actor, target]).delete()
        SocialAuditLog.objects.create(actor=actor, action="block", target_user=target)
    return block


def unblock_user(actor, username):
    """Remove a block."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    Block.objects.filter(blocker=actor, blocked=target).delete()
    SocialAuditLog.objects.create(actor=actor, action="unblock", target_user=target)


def set_like(user, *, target_type, target_id, liked):
    """Create/delete a like for a supported target."""
    if target_type == ContentLike.DIARY_ENTRY:
        entry = get_object_or_404(DiaryEntry.objects.select_related("user"), id=target_id)
        if not _can_view_diary_entry(user, entry):
            raise Http404
    elif target_type == ContentLike.CUSTOM_LIST:
        custom_list = get_object_or_404(
            CustomList.objects.select_related("owner").prefetch_related("collaborators"),
            id=target_id,
        )
        if users_blocked(user, custom_list.owner) or (
            custom_list.visibility == CustomList.Visibility.PRIVATE
            and not custom_list.user_can_view(user)
        ):
            raise Http404
    if liked:
        ContentLike.objects.get_or_create(
            user=user,
            target_type=target_type,
            target_id=target_id,
        )
        action = "like"
    else:
        ContentLike.objects.filter(
            user=user,
            target_type=target_type,
            target_id=target_id,
        ).delete()
        action = "unlike"
    SocialAuditLog.objects.create(
        actor=user,
        action=action,
        target_type=target_type,
        target_id=target_id,
    )
    return {
        "liked": liked,
        "like_count": ContentLike.objects.filter(
            target_type=target_type,
            target_id=target_id,
        ).count(),
    }


def _can_view_diary_entry(viewer, entry):
    if not can_view_user_profile(viewer, entry.user):
        return False
    if viewer == entry.user:
        return True
    if single_weight.uses_half_star_rating(entry.item):
        return True
    if entry.visibility == Visibility.PUBLIC:
        return True
    return entry.visibility == Visibility.FOLLOWERS and Follow.objects.filter(
        from_user=viewer,
        to_user=entry.user,
        status=FollowStatus.ACCEPTED,
    ).exists()
