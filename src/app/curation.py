"""Curated artwork: an admin's artwork pick is everyone's default until they pick their own."""

from django.db.models import Prefetch, Q, prefetch_related_objects

from app.models import CustomBackdropPreference, CustomPosterPreference

# Item relation and attribute that hold prefetched admin picks, per kind of artwork.
_PREFETCHED = {
    CustomPosterPreference: ("customposterpreference_set", "curated_poster_preferences"),
    CustomBackdropPreference: ("custombackdroppreference_set", "curated_backdrop_preferences"),
}


def is_curator(user):
    """Admins curate artwork for everyone."""
    return bool(user and user.is_authenticated and user.is_staff)


def curated_preferences(model):
    """Admin picks of one kind of artwork, newest first."""
    return model.objects.filter(user__is_staff=True).order_by("-updated_at")


def curated_prefetches(prefix=""):
    """Prefetch admin poster and backdrop picks, through ``prefix`` (e.g. ``item__``)."""
    return [
        Prefetch(f"{prefix}{lookup}", queryset=curated_preferences(model), to_attr=to_attr)
        for model, (lookup, to_attr) in _PREFETCHED.items()
    ]


def prime_curated_artwork(items):
    """Load admin poster and backdrop picks for a page of Items in two queries."""
    items = [item for item in items if item is not None]
    if items:
        prefetch_related_objects(items, *curated_prefetches())


def curated_image_url(model, item):
    """Return the newest admin pick of this kind for an Item, or None."""
    if item is None or item.pk is None:
        return None
    to_attr = _PREFETCHED.get(model, (None, None))[1]
    prefetched = getattr(item, to_attr, None) if to_attr else None
    if prefetched is None:
        preference = curated_preferences(model).filter(item=item).first()
    else:
        preference = prefetched[0] if prefetched else None
    return preference.custom_image_url if preference else None


def picked_image_urls(items, model, user):
    """Map Item ids to the pick each viewer sees: their own, else an admin's."""
    item_ids = [item.pk for item in items]
    if not item_ids:
        return {}
    viewer_id = user.pk if user is not None and user.is_authenticated else None
    owners = Q(user__is_staff=True) | Q(user_id=viewer_id) if viewer_id else Q(user__is_staff=True)
    own, curated = {}, {}
    for preference in model.objects.filter(owners, item_id__in=item_ids).order_by("-updated_at"):
        picks = own if preference.user_id == viewer_id else curated
        picks.setdefault(preference.item_id, preference.custom_image_url)
    return {**curated, **own}
