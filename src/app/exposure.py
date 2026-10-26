"""Client-facing media exposure policy."""

from django.conf import settings
from django.http import Http404

from app import config
from app.models import (
    PRIMARY_MEDIA_TYPES,
    USER_OWNED_MEDIA_TYPES,
    MediaTypes,
    Sources,
)


def media_types():
    """Return media types currently exposed to clients."""
    values = list(MediaTypes.values)
    if not settings.MUSIC_ENABLED:
        values.remove(MediaTypes.MUSIC.value)
    return values


def filter_media_types(values):
    """Remove media types that are not exposed."""
    allowed = set(media_types())
    return [value for value in values if value in allowed]


def primary_media_types():
    """Return exposed top-level media types in stable client order."""
    return filter_media_types(PRIMARY_MEDIA_TYPES)


def user_owned_media_types():
    """Return exposed media types whose tracking rows belong to a user."""
    return filter_media_types(USER_OWNED_MEDIA_TYPES)


def media_type_choices():
    """Return exposed media type choices."""
    allowed = set(media_types())
    return [choice for choice in MediaTypes.choices if choice[0] in allowed]


def source_map():
    """Return exposed provider sources keyed by media type."""
    return {
        media_type: [source.value for source in config.get_sources(media_type)]
        for media_type in media_types()
        if media_type in config.MEDIA_TYPE_CONFIG
    }


def source_values():
    """Return source choices currently exposed to clients."""
    values = list(Sources.values)
    if not settings.MUSIC_ENABLED:
        values.remove(Sources.MUSICBRAINZ.value)
        values.remove(Sources.APPLE_MUSIC.value)
    return values


def require_media_type(media_type):
    """Hide disabled media entry points as not found."""
    if media_type is not None and media_type not in media_types():
        raise Http404
