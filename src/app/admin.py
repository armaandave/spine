import contextlib

from django.apps import apps
from django.contrib import admin
from django.contrib.admin.sites import AlreadyRegistered
from django.db.models import Q
from django.utils import timezone

from app.external_ratings import RATING_SOURCES

from app.models import (
    BookSession,
    CustomBackdropPreference,
    CustomLogoPreference,
    CustomPosterPreference,
    DiaryEntry,
    DiaryEntryTag,
    Episode,
    ExternalRating,
    Item,
    ItemFilterFacet,
    MediaLike,
    BookCreditOverride,
    Tag,
    UserMessage,
)


class ExternalRatingFreshnessFilter(admin.SimpleListFilter):
    """Filter rating rows with the same registry freshness rules as tasks."""

    title = "freshness"
    parameter_name = "freshness"

    def lookups(self, request, model_admin):  # noqa: ARG002
        return (("fresh", "Fresh"), ("stale", "Stale"), ("unregistered", "Unregistered"))

    def queryset(self, request, queryset):  # noqa: ARG002
        now = timezone.now()
        terminal = [
            ExternalRating.Status.AVAILABLE,
            ExternalRating.Status.UNAVAILABLE,
        ]
        fresh = Q(pk__isnull=True)
        for source, definition in RATING_SOURCES.items():
            fresh |= Q(
                rating_source=source,
                status__in=terminal,
                last_attempted_at__gte=now - definition["fresh_for"],
            )
        if self.value() == "fresh":
            return queryset.filter(fresh)
        if self.value() == "stale":
            return queryset.filter(rating_source__in=RATING_SOURCES).exclude(fresh)
        if self.value() == "unregistered":
            return queryset.exclude(rating_source__in=RATING_SOURCES)
        return queryset


# Custom ModelAdmin classes with search functionality
@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    """Custom admin for Item model with search and filter options."""

    search_fields = ["title", "media_id", "source"]
    list_display = [
        "title",
        "media_id",
        "season_number",
        "episode_number",
        "media_type",
        "source",
    ]
    list_filter = ["media_type", "source"]


@admin.register(ExternalRating)
class ExternalRatingAdmin(admin.ModelAdmin):
    """Admin visibility for cached external ratings."""

    search_fields = ["item__title", "item__media_id", "rating_source"]
    list_display = [
        "item",
        "rating_source",
        "status",
        "value",
        "max_value",
        "last_attempted_at",
        "last_success_at",
        "freshness",
    ]
    list_filter = [
        "rating_source",
        "item__media_type",
        "item__source",
        "status",
        ExternalRatingFreshnessFilter,
    ]
    list_select_related = ["item"]

    @admin.display(description="Freshness")
    def freshness(self, rating):
        definition = RATING_SOURCES.get(rating.rating_source)
        if definition is None:
            return "unregistered"
        terminal = {
            ExternalRating.Status.AVAILABLE,
            ExternalRating.Status.UNAVAILABLE,
        }
        return (
            "fresh"
            if rating.status in terminal
            and rating.last_attempted_at
            >= timezone.now() - definition["fresh_for"]
            else "stale"
        )


@admin.register(Episode)
class EpisodeAdmin(admin.ModelAdmin):
    """Custom admin for Episode model with search and filter options."""

    search_fields = ["item__title", "related_season__item__title"]
    list_display = ["__str__", "end_date"]


@admin.register(UserMessage)
class UserMessageAdmin(admin.ModelAdmin):
    """Custom admin for persistent user messages."""

    search_fields = ["user__username", "message"]
    list_display = ["message", "level", "user", "created_at", "shown_at"]
    list_filter = ["level", "shown_at"]


class MediaAdmin(admin.ModelAdmin):
    """Custom admin for regular media model with search and filter options."""

    search_fields = ["item__title", "user__username", "notes"]
    list_display = ["__str__", "status", "score", "user"]
    list_filter = ["status"]


@admin.register(MediaLike)
class MediaLikeAdmin(admin.ModelAdmin):
    """Admin for canonical media likes."""

    search_fields = ["item__title", "user__username"]
    list_display = ["user", "item", "created_at"]
    list_filter = ["item__media_type", "created_at"]


@admin.register(ItemFilterFacet)
class ItemFilterFacetAdmin(admin.ModelAdmin):
    """Admin for cached item filter facets."""

    search_fields = ["item__title", "value"]
    list_display = ["item", "facet_type", "value"]
    list_filter = ["facet_type"]


@admin.register(BookCreditOverride)
class BookCreditOverrideAdmin(admin.ModelAdmin):
    """Admin curation for provider person credits."""

    search_fields = ["author_id", "book_id", "display_title", "note"]
    list_display = [
        "display_title",
        "author_id",
        "book_id",
        "disposition",
        "author_source",
    ]
    list_filter = ["disposition", "author_source", "book_source"]


class CustomPosterPreferenceAdmin(admin.ModelAdmin):
    """Custom admin for CustomPosterPreference model."""
    
    search_fields = ["item__title", "user__username"]
    list_display = ["__str__", "user", "item", "updated_at"]
    list_filter = ["user"]


class CustomBackdropPreferenceAdmin(admin.ModelAdmin):
    """Custom admin for CustomBackdropPreference model."""

    search_fields = ["item__title", "user__username"]
    list_display = ["__str__", "user", "item", "updated_at"]
    list_filter = ["user"]


class CustomLogoPreferenceAdmin(admin.ModelAdmin):
    """Custom admin for CustomLogoPreference model."""

    search_fields = ["item__title", "user__username"]
    list_display = ["__str__", "user", "item", "updated_at"]
    list_filter = ["user"]


class DiaryEntryAdmin(admin.ModelAdmin):
    """Custom admin for DiaryEntry model with search and filter options."""
    
    search_fields = ["item__title", "user__username", "review"]
    list_display = ["__str__", "user", "consumed_at", "rating"]
    list_filter = ["user", "consumed_at"]


class TagAdmin(admin.ModelAdmin):
    """Custom admin for Tag model with search and filter options."""
    
    search_fields = ["name"]
    list_display = ["name", "usage_count", "created_at"]
    list_filter = ["created_at"]
    ordering = ["-usage_count", "name"]


class DiaryEntryTagAdmin(admin.ModelAdmin):
    """Custom admin for DiaryEntryTag model with search and filter options."""
    
    search_fields = ["diary_entry__item__title", "tag__name", "diary_entry__user__username"]
    list_display = ["__str__", "diary_entry", "tag", "created_at"]
    list_filter = ["tag", "created_at"]


class BookSessionAdmin(admin.ModelAdmin):
    """Custom admin for BookSession model with search and filter options."""
    
    search_fields = ["related_book__item__title", "related_book__user__username", "notes"]
    list_display = ["__str__", "related_book__user", "status", "pages_read", "percentage_read", "created_at"]
    list_filter = ["status", "created_at"]
    readonly_fields = ["created_at"]


admin.site.register(CustomPosterPreference, CustomPosterPreferenceAdmin)
admin.site.register(CustomBackdropPreference, CustomBackdropPreferenceAdmin)
admin.site.register(CustomLogoPreference, CustomLogoPreferenceAdmin)
admin.site.register(DiaryEntry, DiaryEntryAdmin)
admin.site.register(Tag, TagAdmin)
admin.site.register(DiaryEntryTag, DiaryEntryTagAdmin)
admin.site.register(BookSession, BookSessionAdmin)


# Auto-register remaining models
app_models = apps.get_app_config("app").get_models()
SpecialModels = [
    "Item",
    "Episode",
    "BasicMedia",
    "CustomBackdropPreference",
    "CustomLogoPreference",
    "CustomPosterPreference",
    "DiaryEntry",
    "Tag",
    "DiaryEntryTag",
    "BookSession",
    "GameSession",
    "MediaLike",
    "MediaSeries",
    "MediaSeriesItem",
    "ItemFilterFacet",
    "BookCreditOverride",
    "UserMessage",
]
for model in app_models:
    if (
        not model.__name__.startswith("Historical")
        and model.__name__ not in SpecialModels
    ):
        with contextlib.suppress(AlreadyRegistered):
            admin.site.register(model, MediaAdmin)
