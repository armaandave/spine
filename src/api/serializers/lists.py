from rest_framework import serializers

from api.serializers.common import (
    CompletionProgressSerializer,
    MediaRefSerializer,
)
from app.providers import services as provider_services
from lists.models import CustomList


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
