from django.contrib import admin

from lists.models import (
    CustomList,
    CustomListItem,
    FeaturedListSnapshot,
    FeaturedListSync,
    PersonListItem,
)


@admin.register(CustomList)
class CustomListAdmin(admin.ModelAdmin):
    """Admin configuration for CustomList model."""

    search_fields = ["name", "description", "owner__username", "import_source_id"]
    list_display = [
        "name",
        "list_type",
        "owner",
        "is_featured",
        "item_count",
        "get_last_update",
    ]
    list_filter = ["list_type", "owner", "is_featured", "import_source"]
    raw_id_fields = ["owner"]
    autocomplete_fields = ["collaborators"]
    filter_horizontal = ["collaborators"]

    @admin.display(description="Number of items")
    def item_count(self, obj):
        """Return the number of items in the list."""
        if obj.list_type == CustomList.ListType.PEOPLE:
            return obj.person_items.count()
        return obj.items.count()

    @admin.display(description="Last updated")
    def get_last_update(self, obj):
        """Return the date of the last item added."""
        if obj.list_type == CustomList.ListType.PEOPLE:
            last_item = obj.person_items.order_by("-date_added").first()
            last_update = last_item.date_added if last_item else None
        else:
            last_update = CustomListItem.objects.get_last_added_date(obj)
        return last_update or "-"

    def get_readonly_fields(self, request, obj=None):
        """Keep a list's entry type immutable after creation."""
        readonly_fields = list(super().get_readonly_fields(request, obj))
        if obj:
            readonly_fields.append("list_type")
        return readonly_fields


@admin.register(CustomListItem)
class CustomListItemAdmin(admin.ModelAdmin):
    """Admin configuration for CustomListItem model."""

    search_fields = ["item__title", "custom_list__name", "item__media_id"]
    list_display = ["item", "custom_list", "date_added", "get_media_type"]
    list_filter = ["custom_list", "item__media_type", "custom_list__owner"]
    raw_id_fields = ["item", "custom_list"]
    autocomplete_fields = ["item", "custom_list"]
    readonly_fields = ["date_added"]

    @admin.display(description="Media Type")
    def get_media_type(self, obj):
        """Return the media type of the item."""
        return obj.item.get_media_type_display()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Only offer media lists for media memberships."""
        if db_field.name == "custom_list":
            kwargs["queryset"] = CustomList.objects.filter(
                list_type=CustomList.ListType.MEDIA,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(PersonListItem)
class PersonListItemAdmin(admin.ModelAdmin):
    """Admin configuration for provider-backed person list entries."""

    search_fields = ["name", "person_id", "custom_list__name"]
    list_display = ["name", "source", "custom_list", "position", "date_added"]
    list_filter = ["source", "custom_list", "custom_list__owner"]
    raw_id_fields = ["custom_list"]
    readonly_fields = ["date_added"]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Only offer people lists for person memberships."""
        if db_field.name == "custom_list":
            kwargs["queryset"] = CustomList.objects.filter(
                list_type=CustomList.ListType.PEOPLE,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ReadOnlyAdmin(admin.ModelAdmin):
    """Admin that shows engine-owned rows without letting anyone edit them."""

    def has_add_permission(self, request):  # noqa: ARG002
        """Rows are written by the featured-lists engine only."""
        return False

    def has_change_permission(self, request, obj=None):  # noqa: ARG002
        """Rows are written by the featured-lists engine only."""
        return False

    def has_delete_permission(self, request, obj=None):  # noqa: ARG002
        """Rows are written by the featured-lists engine only."""
        return False


@admin.register(FeaturedListSync)
class FeaturedListSyncAdmin(ReadOnlyAdmin):
    """Read-only sync state and health for featured lists."""

    search_fields = ["key", "custom_list__name"]
    list_display = [
        "key",
        "last_status",
        "resolved_count",
        "entry_count",
        "consecutive_failures",
        "last_success_at",
        "content_changed_at",
    ]
    list_filter = ["last_status"]
    raw_id_fields = ["custom_list"]


@admin.register(FeaturedListSnapshot)
class FeaturedListSnapshotAdmin(ReadOnlyAdmin):
    """Read-only daily snapshots of featured lists."""

    search_fields = ["sync__key", "edition"]
    list_display = ["sync", "taken_on", "edition", "fingerprint"]
    list_filter = ["taken_on"]
    raw_id_fields = ["sync"]
