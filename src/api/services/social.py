from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from api.permissions import can_view_user_profile, user_blocks, users_blocked
from app import single_weight
from app.models import DiaryEntry
from lists.models import CustomList
from social import push
from social.models import (
    Block,
    Comment,
    ContentLike,
    Follow,
    FollowStatus,
    ProgressPost,
    SocialAuditLog,
    UserReport,
    Visibility,
)


def follow_user(actor, username):
    """Follow a user, or ask to when their account is private.

    Following again keeps an accepted follow, and accepts a request to an account that
    has since gone public. A new follow or request pushes to the person followed.
    """
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    if target == actor or users_blocked(actor, target):
        raise Http404
    status = FollowStatus.PENDING if target.profile_private else FollowStatus.ACCEPTED
    follow, created = Follow.objects.get_or_create(
        from_user=actor,
        to_user=target,
        defaults={"status": status},
    )
    if not created:
        if follow.status == FollowStatus.ACCEPTED or status == FollowStatus.PENDING:
            return follow
        follow.status = FollowStatus.ACCEPTED
        follow.save(update_fields=["status", "updated_at"])
    SocialAuditLog.objects.create(
        actor=actor,
        action="follow" if status == FollowStatus.ACCEPTED else "follow_request",
        target_user=target,
    )
    if status == FollowStatus.ACCEPTED:
        push.notify(target, f"{_name(actor)} started following you.", _profile_route(actor))
    else:
        push.notify(target, f"{_name(actor)} wants to follow you.", {"kind": "follow_requests"})
    return follow


def unfollow_user(actor, username):
    """Unfollow a user, or take back a request to follow them."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    Follow.objects.filter(from_user=actor, to_user=target).delete()
    SocialAuditLog.objects.create(actor=actor, action="unfollow", target_user=target)


def answer_follow_request(owner, request_id, accept):
    """Accept or decline a request to follow ``owner``; accepting tells them."""
    follow = get_object_or_404(
        Follow.objects.select_related("from_user"),
        id=request_id,
        to_user=owner,
        status=FollowStatus.PENDING,
    )
    requester = follow.from_user
    if accept:
        follow.status = FollowStatus.ACCEPTED
        follow.save(update_fields=["status", "updated_at"])
        SocialAuditLog.objects.create(actor=owner, action="follow_request_accept", target_user=requester)
        push.notify(requester, f"{_name(owner)} accepted your follow request.", _profile_route(owner))
    else:
        follow.delete()
        SocialAuditLog.objects.create(actor=owner, action="follow_request_reject", target_user=requester)


def remove_follower(owner, username):
    """Stop a user following ``owner``, without telling them."""
    User = get_user_model()
    follower = get_object_or_404(User, username=username)
    Follow.objects.filter(from_user=follower, to_user=owner).delete()
    SocialAuditLog.objects.create(actor=owner, action="remove_follower", target_user=follower)


def report_user(reporter, username):
    """Report a user to the project owner, who gets a push about it."""
    User = get_user_model()
    reported = get_object_or_404(User, username=username)
    if reported == reporter or user_blocks(reported, reporter):
        raise Http404
    report = UserReport.objects.create(reporter=reporter, reported=reported)
    SocialAuditLog.objects.create(actor=reporter, action="report", target_user=reported)
    for owner in User.objects.filter(is_superuser=True, is_active=True).exclude(id=reported.id):
        push.notify(owner, f"{_name(reporter)} reported @{reported.username}.", _profile_route(reported))
    return report


def _name(user):
    return user.display_name or user.username


def _profile_route(user):
    return {"kind": "profile", "username": user.username}


def block_user(actor, username):
    """Block a user and remove follow edges both ways."""
    User = get_user_model()
    target = get_object_or_404(User, username=username)
    if target == actor:
        raise Http404
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
    elif target_type == ContentLike.PROGRESS_POST:
        post = get_object_or_404(ProgressPost.objects.select_related("actor"), id=target_id)
        if not can_view_user_profile(user, post.actor):
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


def viewable_diary_entry(viewer, entry_id):
    """Return a log the viewer may see, or 404."""
    entry = get_object_or_404(DiaryEntry.objects.select_related("user", "item"), id=entry_id)
    if not _can_view_diary_entry(viewer, entry):
        raise Http404
    return entry


def add_comment(user, body, *, entry=None, post=None, parent_id=None):
    """Comment on a log or a day's progress the user can see, or reply to a comment.

    A reply to a reply joins its thread, so replies stay one level deep.
    """
    parent = None
    if parent_id is not None:
        parent = (
            Comment.objects.visible_to(user)
            .filter(diary_entry=entry, progress_post=post, is_deleted=False)
            .select_related("parent")
            .filter(id=parent_id)
            .first()
        )
        if parent is None:
            raise ValidationError({"parent_id": "That comment isn't here anymore."})
        parent = parent.parent or parent
    comment = Comment.objects.create(user=user, body=body, diary_entry=entry, progress_post=post, parent=parent)
    SocialAuditLog.objects.create(
        actor=user,
        action="comment",
        target_type=ContentLike.DIARY_ENTRY if entry else ContentLike.PROGRESS_POST,
        target_id=entry.id if entry else post.id,
    )
    return comment


def can_delete_comment(user, comment):
    """Let a comment's author, the post's owner, and admins delete it."""
    owner_id = comment.diary_entry.user_id if comment.diary_entry_id else comment.progress_post.actor_id
    return user.is_staff or user.id in {comment.user_id, owner_id}


def delete_comment(user, comment_id):
    """Delete a comment the user can see and is allowed to remove."""
    comment = get_object_or_404(
        Comment.objects.visible_to(user).select_related("diary_entry", "progress_post"),
        id=comment_id,
        is_deleted=False,
    )
    if not can_delete_comment(user, comment):
        raise Http404
    SocialAuditLog.objects.create(
        actor=user,
        action="comment_delete",
        target_user=comment.user,
        target_type=ContentLike.DIARY_ENTRY if comment.diary_entry_id else ContentLike.PROGRESS_POST,
        target_id=comment.diary_entry_id or comment.progress_post_id,
    )
    if comment.replies.exists():
        # Its replies still need something to answer: it stays as "Comment deleted".
        comment.body = ""
        comment.is_deleted = True
        comment.save(update_fields=["body", "is_deleted"])
        return
    comment.delete()
    parent = comment.parent
    if parent and parent.is_deleted and not parent.replies.exists():
        parent.delete()


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
