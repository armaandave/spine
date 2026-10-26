from rest_framework import serializers

from api.serializers.common import (
    CompletionProgressSerializer,
    MediaRefSerializer,
)
from app.providers import services as provider_services
from lists.models import CustomList

MAX_LIST_TAGS = 20
MAX_LIST_TAG_LENGTH = 100


class CustomListWriteSerializer(serializers.Serializer):
    """Validate custom list writes."""

    name = serializers.CharField(max_length=255)
    slug = serializers.SlugField(max_length=255, required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    list_type = serializers.ChoiceField(
        choices=CustomList.ListType.choices,
        required=False,
    )
    visibility = serializers.ChoiceField(
        choices=["public", "unlisted", "private"],
        required=False,
    )
    is_ranked = serializers.BooleanField(required=False)
    collaborator_usernames = serializers.ListField(
        child=serializers.CharField(max_length=150),
        required=False,
    )
    # Owner only (the views check). STAT-402: at most 20 strings of at most 100
    # characters once trimmed, checked as sent; then normalized.
    tags = serializers.ListField(
        child=serializers.JSONField(allow_null=True),
        required=False,
        max_length=MAX_LIST_TAGS,
    )

    def validate_tags(self, value):
        """Check each tag as sent, then return the normalized tags (STAT-402)."""
        for tag in value:
            if not isinstance(tag, str):
                raise serializers.ValidationError("Each tag must be a string.")
            if len(tag.strip()) > MAX_LIST_TAG_LENGTH:
                msg = f"Each tag must be at most {MAX_LIST_TAG_LENGTH} characters."
                raise serializers.ValidationError(msg)
        return normalize_list_tags(value)


def normalize_list_tags(tags):
    """Trim tags, collapse inner whitespace, drop empties and case-insensitive repeats.

    The first spelling of tags that differ only in case is kept (STAT-402).
    """
    normalized, seen = [], set()
    for tag in tags:
        value = " ".join(tag.split())
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            normalized.append(value)
    return normalized


class ListItemWriteSerializer(serializers.Serializer):
    """Validate list item writes."""

    ref = MediaRefSerializer()


class ListItemsReorderSerializer(serializers.Serializer):
    """Validate list item reorder writes."""

    item_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
    )


class PersonRefSerializer(serializers.Serializer):
    """Stable public identity for a provider-backed person."""

    source = serializers.ChoiceField(choices=provider_services.SUPPORTED_PERSON_SOURCES)
    id = serializers.CharField(max_length=255)


class ListPersonWriteSerializer(serializers.Serializer):
    """Validate a person-list membership write."""

    ref = PersonRefSerializer()


class ListPeopleReorderSerializer(serializers.Serializer):
    """Validate person-list membership ordering."""

    entry_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
    )


class PersonListEntrySerializer(serializers.Serializer):
    """Document the stored person-list entry response."""

    entry_id = serializers.IntegerField()
    id = serializers.CharField()
    source = serializers.CharField()
    name = serializers.CharField()
    profile_url = serializers.URLField(allow_null=True)
    known_for_department = serializers.CharField(allow_null=True)
    position = serializers.IntegerField(allow_null=True)
    date_added = serializers.DateTimeField()
    completion = CompletionProgressSerializer(required=False, allow_null=True)
    # Featured lists only: the entry's note ("Inducted 2019").
    note = serializers.CharField(required=False, allow_null=True)


class ListPersonWriteResponseSerializer(serializers.Serializer):
    """Document idempotent person-list writes."""

    created = serializers.BooleanField()
    person = PersonListEntrySerializer()


class ListPeoplePageSerializer(serializers.Serializer):
    """Document paginated people-list responses."""

    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = PersonListEntrySerializer(many=True)


class CollaboratorSerializer(serializers.Serializer):
    """Validate collaborator writes."""

    username = serializers.CharField(max_length=150)
