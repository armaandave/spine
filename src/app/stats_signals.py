"""Receivers that invalidate an owner's cached stats when their data changes (STAT-006).

Every save or delete of a row Stats read bumps its owner's stats version once per
transaction, on commit (api.services.stats_cache.bump_user): logs and their tags,
likes, game and book sessions, every tracking row (each Media subclass), the owner's
poster and backdrop picks (STAT-304 summaries show them), lists (bumping the list's
owner, so a collaborator's edit counts) and the user's own settings.

List items have no receiver: adding, removing or reordering them touches the list's
``updated_at`` (STAT-403), which saves the list. Writes that skip signals (``.update()``,
bulk writes, raw deletes) call bump_user themselves.

A child row (a log tag or a session) deleted in a cascade from another model is
skipped: its parent's own receiver bumps the owner, and reading each child's parent
would cost a query per deleted row.
"""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.db.models.signals import post_delete, post_save

from api.services import stats_cache
from app.models import (
    BookSession,
    CustomBackdropPreference,
    CustomPosterPreference,
    DiaryEntry,
    DiaryEntryTag,
    GameSession,
    Media,
    MediaLike,
)


def _owner_through(instance, field_name, parent_model, origin=None):
    """Return the owner's pk through a parent row, reading it only when not loaded.

    Lookups are remembered on the delete's origin, so deleting many rows of one
    parent (``entry.tags.clear()``) reads that parent once.
    """
    field = instance._meta.get_field(field_name)
    if field.is_cached(instance):
        return getattr(instance, field_name).user_id
    parent_id = getattr(instance, field.attname)
    if parent_id is None:
        return None
    known = getattr(origin, "_stats_parent_owners", None) if origin is not None else None
    if known is not None and (parent_model, parent_id) in known:
        return known[parent_model, parent_id]
    # A parent already deleted bumped its owner itself.
    owner_id = parent_model.objects.filter(pk=parent_id).values_list("user_id", flat=True).first()
    if origin is not None:
        if known is None:
            known = {}
            origin._stats_parent_owners = known
        known[parent_model, parent_id] = owner_id
    return owner_id


def _child(field_name, parent_label):
    def owner_of(row, origin=None):
        return _owner_through(row, field_name, apps.get_model(parent_label), origin)

    owner_of.is_child = True
    return owner_of


OWNERS = {
    DiaryEntry: lambda row: row.user_id,
    MediaLike: lambda row: row.user_id,
    CustomPosterPreference: lambda row: row.user_id,
    CustomBackdropPreference: lambda row: row.user_id,
    DiaryEntryTag: _child("diary_entry", "app.DiaryEntry"),
    GameSession: _child("related_game", "app.Game"),
    BookSession: _child("related_book", "app.Book"),
}


def _deleted_in_cascade(sender, origin):
    """Whether a delete started from another model's row or queryset."""
    if origin is None:
        return False
    origin_model = origin.model if isinstance(origin, QuerySet) else type(origin)
    return origin_model is not sender


def _receiver(owner_of, *, deleting=False):
    child = getattr(owner_of, "is_child", False)

    def bump_owner(sender, instance, raw=False, origin=None, **kwargs):  # noqa: ARG001
        if raw or not stats_cache.enabled():
            return
        if child and deleting:
            if not _deleted_in_cascade(sender, origin):
                stats_cache.bump_user(owner_of(instance, origin))
            return
        stats_cache.bump_user(owner_of(instance))

    return bump_owner


def connect():
    """Connect every receiver; called once from AppConfig.ready."""
    owners = dict(OWNERS)
    for model in apps.get_models():
        if issubclass(model, Media):
            owners[model] = lambda row: row.user_id
    owners[apps.get_model("lists", "CustomList")] = lambda row: row.owner_id
    owners[get_user_model()] = lambda row: row.pk
    for model, owner_of in owners.items():
        uid = f"stats_cache_{model._meta.label_lower}"
        post_save.connect(_receiver(owner_of), sender=model, weak=False, dispatch_uid=f"{uid}_saved")
        post_delete.connect(
            _receiver(owner_of, deleting=True),
            sender=model,
            weak=False,
            dispatch_uid=f"{uid}_deleted",
        )
