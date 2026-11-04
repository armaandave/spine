"""Likes and comments go with their day or title (docs/activity-contract.md, AC-7).

A post is what likes and comments on a day's progress attach to. It holds no foreign key
to the saves, so without this a like outlives them and comes back on a later save that
day.

- A deleted attempt or season drops the posts of the days it leaves with no row, and
  never any other day's: a plain log delete runs through here, and another day's post
  can sit on a day its row moved from.
- A removed title loses its saves, posts, likes and comments. Each tracking row's delete
  queues its person and title once per transaction, and the work runs on commit in
  batches, because an overwrite deletes hundreds of rows at once. A row still there by
  then means the title stayed. An overwrite deletes and re-creates inside one
  transaction, so the importers call forget_titles themselves.
"""

from collections import defaultdict

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, QuerySet
from django.db.models.signals import post_delete

from app import user_calendar
from app.models import Item, Media, MediaTypes
from social.models import (
    Activity,
    ContentLike,
    ProgressChange,
    ProgressPost,
    SocialAuditLog,
)

SHOW_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value)
# Titles queued in the current transaction, kept on the connection.
PENDING_ATTR = "_progress_posts_pending"
BATCH = 500


def _title_key(item):
    """Return the key posts are saved under, as the feed builds it."""
    from api.services.activity import (
        _title_key as title_key,
    )

    return title_key(item)


def _progress_days(changes):
    from api.services.activity import (
        _progress_days as progress_days,
    )

    return progress_days(changes)


def _delete_posts(posts):
    """Delete these posts with their likes and comments, and say so in the audit log."""
    posts = list(posts)
    if not posts:
        return
    ids = [post.pk for post in posts]
    # A like has no foreign key to its post; comments go with it.
    ContentLike.objects.filter(target_type=ContentLike.PROGRESS_POST, target_id__in=ids).delete()
    ProgressPost.objects.filter(pk__in=ids).delete()
    SocialAuditLog.objects.bulk_create(
        [
            SocialAuditLog(
                actor_id=post.actor_id,
                action="progress_post_removed",
                target_type="progress_post",
                target_id=post.pk,
                metadata={"title_key": post.title_key, "day": post.day.isoformat()},
            )
            for post in posts
        ],
    )


def drop_empty_posts(actor_id, title_key, days):
    """Delete this title's posts on these days, when no row is left on the day."""
    if not days:
        return
    posts = ProgressPost.objects.filter(actor_id=actor_id, title_key=title_key, day__in=days)
    if not posts.exists():
        return
    rows = _progress_days(ProgressChange.objects.filter(actor_id=actor_id)).filter(title_key=title_key, day__in=days)
    _delete_posts(posts.exclude(day__in={row["day"] for row in rows}))


def delete_changes(actor_id, changes):
    """Delete one person's change rows; a post they leave with no row goes too."""
    days = defaultdict(set)
    ids = []
    for change in changes.select_related("item"):
        ids.append(change.pk)
        days[_title_key(change.item)].add(_saved_day(change))
    if not ids:
        return
    Activity.objects.filter(target_type="progress_change", target_id__in=ids).delete()
    ProgressChange.objects.filter(pk__in=ids).delete()
    for title_key, title_days in days.items():
        drop_empty_posts(actor_id, title_key, title_days)


def _saved_day(change):
    return change.saved_on or change.created_at.astimezone(user_calendar.server_tz()).date()


def forget_titles(user_id, items):
    """Delete a person's saves, posts, likes and comments on titles they've left."""
    items = list(items)
    for start in range(0, len(items), BATCH):
        _forget_batch(user_id, items[start : start + BATCH])


def _forget_batch(user_id, items):
    plain = [item.pk for item in items if item.media_type not in SHOW_TYPES]
    shows = {(item.source, item.media_id) for item in items if item.media_type in SHOW_TYPES}
    keys = [_title_key(item) for item in items]
    matches = Q(item_id__in=plain)
    for source, media_id in shows:
        matches |= Q(item__source=source, item__media_id=media_id, item__media_type__in=SHOW_TYPES)
    changes = ProgressChange.objects.filter(matches, actor_id=user_id)
    ids = list(changes.values_list("pk", flat=True))
    Activity.objects.filter(target_type="progress_change", target_id__in=ids).delete()
    ProgressChange.objects.filter(pk__in=ids).delete()
    _delete_posts(ProgressPost.objects.filter(actor_id=user_id, title_key__in=keys))


# The receiver on tracking rows.


def _started_from_user(origin):
    """Return whether a delete began at the User: all of theirs goes in a cascade."""
    if origin is None:
        return False
    model = origin.model if isinstance(origin, QuerySet) else type(origin)
    return model is get_user_model()


def _tracking_row_deleted(sender, instance, origin=None, **kwargs):
    if _started_from_user(origin):
        return
    connection = transaction.get_connection()
    hooks, pending = getattr(connection, PENDING_ATTR, (None, None))
    # As stats_cache.bump_user: a pending set tied to another list of hooks has ended.
    if hooks is not connection.run_on_commit:
        pending = set()
    if not pending:
        transaction.on_commit(lambda queued=pending: _flush(queued), robust=True)
    setattr(connection, PENDING_ATTR, (connection.run_on_commit, pending))
    pending.add((instance.user_id, instance.item_id))


def _flush(pending):
    """Forget the queued titles no row holds any more, in batches."""
    by_user = defaultdict(set)
    for user_id, item_id in pending:
        by_user[user_id].add(item_id)
    pending.clear()
    for user_id, item_ids in by_user.items():
        items = list(Item.objects.filter(pk__in=item_ids))
        gone = _untracked(user_id, items)
        if gone:
            with transaction.atomic():
                forget_titles(user_id, gone)


def _untracked(user_id, items):
    """Return which of these titles have no tracking row left for this person."""
    held = set()
    by_type = defaultdict(list)
    for item in items:
        by_type[item.media_type].append(item)
    shows = by_type.pop(MediaTypes.SEASON.value, []) + by_type.pop(MediaTypes.TV.value, [])
    if shows:
        for media_type in SHOW_TYPES:
            held |= set(
                apps.get_model("app", media_type)
                .objects.filter(user_id=user_id, item__media_id__in={item.media_id for item in shows})
                .values_list("item__source", "item__media_id"),
            )
    gone = [item for item in shows if (item.source, item.media_id) not in held]
    for media_type, typed in by_type.items():
        tracked = set(
            apps.get_model("app", media_type)
            .objects.filter(user_id=user_id, item__in=typed)
            .values_list("item_id", flat=True),
        )
        gone.extend(item for item in typed if item.pk not in tracked)
    return gone


def connect():
    """Connect the receiver to every tracking row; called once from AppConfig.ready.

    A season isn't one: it goes with its show, and on its own it leaves the title there.
    """
    for model in apps.get_models():
        if issubclass(model, Media) and model._meta.model_name != MediaTypes.SEASON.value:
            post_delete.connect(
                _tracking_row_deleted,
                sender=model,
                weak=False,
                dispatch_uid=f"progress_posts_{model._meta.label_lower}",
            )
