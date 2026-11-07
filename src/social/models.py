from django.conf import settings
from django.db import models
from django.db.models import Q

from app import user_calendar


class FollowStatus(models.TextChoices):
    """Status values for one-way follow relationships."""

    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"


class Visibility(models.TextChoices):
    """Visibility values shared by social surfaces."""

    PUBLIC = "public", "Public"
    FOLLOWERS = "followers", "Followers"
    PRIVATE = "private", "Private"
    UNLISTED = "unlisted", "Unlisted"


class Follow(models.Model):
    """One-way follow relationship between users."""

    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="following_edges",
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="follower_edges",
    )
    status = models.CharField(
        max_length=20,
        choices=FollowStatus.choices,
        default=FollowStatus.ACCEPTED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["from_user", "to_user"],
                name="social_follow_unique_from_to",
            ),
            models.CheckConstraint(
                condition=~Q(from_user=models.F("to_user")),
                name="social_follow_no_self_follow",
            ),
        ]
        indexes = [
            models.Index(fields=["from_user", "status", "-created_at"]),
            models.Index(fields=["to_user", "status", "-created_at"]),
        ]


class Block(models.Model):
    """A user block relationship."""

    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="blocks_sent",
    )
    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="blocks_received",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["blocker", "blocked"],
                name="social_block_unique_blocker_blocked",
            ),
            models.CheckConstraint(
                condition=~Q(blocker=models.F("blocked")),
                name="social_block_no_self_block",
            ),
        ]
        indexes = [
            models.Index(fields=["blocker", "-created_at"]),
            models.Index(fields=["blocked", "-created_at"]),
        ]


class ContentLike(models.Model):
    """Like for a supported social target."""

    DIARY_ENTRY = "diary"
    CUSTOM_LIST = "list"
    PROGRESS_POST = "progress"

    TARGET_CHOICES = [
        (DIARY_ENTRY, "Diary entry"),
        (CUSTOM_LIST, "Custom list"),
        (PROGRESS_POST, "Progress post"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    target_type = models.CharField(max_length=20, choices=TARGET_CHOICES)
    target_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "target_type", "target_id"],
                name="social_contentlike_unique_user_target",
            ),
        ]
        indexes = [
            models.Index(fields=["target_type", "target_id", "-created_at"]),
            models.Index(fields=["user", "-created_at"]),
        ]


class Activity(models.Model):
    """Materialized activity item for reverse-chronological feeds."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="activities",
    )
    verb = models.CharField(max_length=50)
    target_type = models.CharField(max_length=50)
    target_id = models.PositiveBigIntegerField()
    item = models.ForeignKey(
        "app.Item",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activities",
    )
    visibility = models.CharField(
        max_length=20,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
    )
    snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at", "-id"]),
            models.Index(fields=["actor", "-created_at", "-id"]),
            models.Index(fields=["item", "-created_at", "-id"]),
        ]


class ProgressChange(models.Model):
    """Durable progress delta for tracked media."""

    # Starting a title is recorded as a step from 0 to 1 of this kind, so activity can
    # show "started reading" before any progress. It isn't progress itself.
    START = "start"
    # Rows that say what happened, not how far: a status move (paused, stopped, picked
    # back up), a retype of the same number, a title added to Planning. They hold value
    # 0 in previous and current, so they add nothing to any number.
    STATUS = "status"
    TOUCH = "touch"
    PLANNING = "planning"
    # What installed builds must never get as their latest progress change.
    NOT_PROGRESS = (START, STATUS, TOUCH, PLANNING)
    # What a status row says (AC-1).
    PAUSED = "paused"
    STOPPED = "stopped"
    PICKED_BACK_UP = "picked back up"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="progress_changes",
    )
    item = models.ForeignKey(
        "app.Item",
        on_delete=models.CASCADE,
        related_name="progress_changes",
    )
    previous_progress = models.JSONField()
    current_progress = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    # The phone's date when this was saved (docs/activity-contract.md, AC-2). Set by
    # each writer, never in save(): fixtures create a row and then move its created_at.
    # Empty only on rows written by code from before this column.
    saved_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["actor", "item", "-created_at"]),
            models.Index(fields=["-created_at", "id"]),
        ]

    @classmethod
    def record_start(cls, actor, item, **context):
        """Record starting a title; context such as a journey id rides along."""

        def snapshot(value):
            return {
                "kind": cls.START,
                "value": value,
                "max": 1,
                "unit": cls.START,
                **context,
            }

        return cls.objects.create(
            actor=actor,
            item=item,
            previous_progress=snapshot(0),
            current_progress=snapshot(1),
            saved_on=user_calendar.today(actor),
        )

    @classmethod
    def record_status(cls, actor, item, move, **context):
        """Record a status move the person chose; a journey id or such rides along.

        It holds value 0 in previous and current and adds nothing to any number.
        """
        snapshot = {
            "kind": cls.STATUS,
            "value": 0,
            "max": None,
            "unit": cls.STATUS,
            "move": move,
            **context,
        }
        return cls.objects.create(
            actor=actor,
            item=item,
            previous_progress=snapshot,
            current_progress=snapshot,
            saved_on=user_calendar.today(actor),
        )

    @classmethod
    def record_planning(cls, actor, item):
        """Record the person asking for Planning on a title they weren't planning.

        It says "on this day this person added this title to Planning" and nothing
        else: whether the title is in Planning now is the tracking row's status. It
        holds value 0 in previous and current and adds nothing to any number.
        """
        snapshot = {"kind": cls.PLANNING, "value": 0, "max": None, "unit": cls.PLANNING}
        return cls.objects.create(
            actor=actor,
            item=item,
            previous_progress=snapshot,
            current_progress=snapshot,
            saved_on=user_calendar.today(actor),
        )


class ProgressPost(models.Model):
    """What likes and comments on a day's progress attach to.

    A day's progress is computed from ``ProgressChange`` (docs/activity-contract.md),
    so it has no row of its own until someone first likes or comments on it.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="progress_posts",
    )
    # "tv:<source>:<media_id>" for a show, "item:<id>" for anything else.
    title_key = models.CharField(max_length=120)
    day = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["actor", "title_key", "day"],
                name="social_progresspost_unique_actor_title_day",
            ),
        ]


class CommentQuerySet(models.QuerySet):
    """Comments as a given person sees them."""

    def visible_to(self, viewer):
        """Leave out comments, and replies under them, from anyone across a block."""
        if not viewer or not viewer.is_authenticated:
            return self
        return (
            self.exclude(user__blocks_sent__blocked=viewer)
            .exclude(user__blocks_received__blocker=viewer)
            .exclude(parent__user__blocks_sent__blocked=viewer)
            .exclude(parent__user__blocks_received__blocker=viewer)
        )

    def counted(self):
        """Leave out deleted comments that stay only to hold their replies."""
        return self.filter(is_deleted=False)


class Comment(models.Model):
    """A comment on a log or on a day's progress, or a reply to one.

    Replies go one level deep: a reply's parent is always a top-level comment.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    diary_entry = models.ForeignKey(
        "app.DiaryEntry",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    progress_post = models.ForeignKey(
        ProgressPost,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="replies",
    )
    body = models.TextField()
    # A deleted comment with replies stays, emptied, as "Comment deleted" above them.
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CommentQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(diary_entry__isnull=False, progress_post__isnull=True)
                    | Q(diary_entry__isnull=True, progress_post__isnull=False)
                ),
                name="social_comment_on_one_post",
            ),
        ]


class PushDevice(models.Model):
    """A phone's Apple push token, for the app it was issued to."""

    class Environment(models.TextChoices):
        SANDBOX = "sandbox", "Sandbox"
        PRODUCTION = "production", "Production"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_devices",
    )
    # A token belongs to one install, so signing in as someone else moves it.
    token = models.CharField(max_length=200, unique=True)
    bundle_id = models.CharField(max_length=200)
    environment = models.CharField(max_length=20, choices=Environment.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class UserReport(models.Model):
    """A user reporting another user to the project owner."""

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="reports_sent",
    )
    reported = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports_received",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class SocialAuditLog(models.Model):
    """Audit log for important social actions."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="social_audit_actions",
    )
    action = models.CharField(max_length=80)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="social_audit_targets",
    )
    target_type = models.CharField(max_length=50, blank=True, default="")
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["target_user", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
        ]
