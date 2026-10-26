"""Model receivers that catch TV writes made outside app.tv_tracking (plan 1.2, Hooks).

Every web, API, webhook and import path calls app.tv_tracking directly; these receivers
are a safety net for the admin, the shell and leftover helpers. Each one does nothing
while app.tv_tracking itself is writing, for raw (fixture) saves, and for shows stored
before the contract (app.tv_tracking checks that when it runs). The work runs once per
show when the transaction commits, uses the stored episode lists only and never raises
into a model save.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from app import tv_tracking
from app.models import TV, Episode, Season


@receiver(post_save, sender=Episode, dispatch_uid="tv_tracking_episode_saved")
def episode_saved(sender, instance, created, raw=False, **kwargs):  # noqa: ARG001
    """A viewing saved outside app.tv_tracking is a fact as saved; work the show out."""
    if raw or tv_tracking.is_writing():
        return
    tv_tracking.on_episode_saved(instance, created)


@receiver(post_delete, sender=Episode, dispatch_uid="tv_tracking_episode_deleted")
def episode_deleted(sender, instance, origin=None, **kwargs):  # noqa: ARG001
    """A viewing deleted directly; deleting a season, show, user or item does nothing."""
    if tv_tracking.is_writing():
        return
    tv_tracking.on_episode_deleted(instance, origin)


@receiver(post_save, sender=TV, dispatch_uid="tv_tracking_tv_saved")
def tv_saved(sender, instance, created, raw=False, **kwargs):  # noqa: ARG001
    """A new show row records no choice; a written status means its action (TV-207)."""
    if raw or tv_tracking.is_writing():
        return
    if created:
        tv_tracking.on_tv_created(instance)
        return
    if instance.tracker.has_changed("status"):
        tv_tracking.on_status_written(instance, instance.tracker.previous("status"))


@receiver(post_save, sender=Season, dispatch_uid="tv_tracking_season_saved")
def season_saved(sender, instance, created, raw=False, **kwargs):  # noqa: ARG001
    """A season status written outside app.tv_tracking means its action (TV-207)."""
    if raw or created or tv_tracking.is_writing():
        return
    if instance.tracker.has_changed("status"):
        tv_tracking.on_status_written(instance, instance.tracker.previous("status"))
