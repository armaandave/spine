"""Receivers that invalidate an owner's cached stats when their data changes (STAT-006).

Every save or delete of a row Stats read bumps its owner's stats version once per
transaction, on commit (api.services.stats_cache.bump_user): logs and their tags,
likes, game and book sessions, every tracking row (each Media subclass), lists (bumping
the list's owner, so a collaborator's edit counts) and the user's own settings.

List items have no receiver: adding, removing or reordering them touches the list's
``updated_at`` (STAT-403), which saves the list. Writes that skip signals (``.update()``,
bulk writes, raw deletes) call bump_user themselves.
"""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_save

from api.services import stats_cache
from app.models import BookSession, DiaryEntry, DiaryEntryTag, GameSession, Media, MediaLike


def _owner_through(instance, field_name, parent_model):
    """Return the owner's pk through a parent row, reading it only when not loaded."""
    field = instance._meta.get_field(field_name)
    if field.is_cached(instance):
        return getattr(instance, field_name).user_id
    parent_id = getattr(instance, field.attname)
    if parent_id is None:
        return None
    # A parent already deleted bumped its owner itself.
    return parent_model.objects.filter(pk=parent_id).values_list("user_id", flat=True).first()


OWNERS = {
    DiaryEntry: lambda row: row.user_id,
    MediaLike: lambda row: row.user_id,
    DiaryEntryTag: lambda row: _owner_through(row, "diary_entry", DiaryEntry),
    GameSession: lambda row: _owner_through(row, "related_game", apps.get_model("app", "Game")),
    BookSession: lambda row: _owner_through(row, "related_book", apps.get_model("app", "Book")),
}


def _receiver(owner_of):
    def bump_owner(sender, instance, raw=False, **kwargs):  # noqa: ARG001
        if raw or not stats_cache.enabled():
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
        receiver = _receiver(owner_of)
        uid = f"stats_cache_{model._meta.label_lower}"
        post_save.connect(receiver, sender=model, weak=False, dispatch_uid=f"{uid}_saved")
        post_delete.connect(receiver, sender=model, weak=False, dispatch_uid=f"{uid}_deleted")
