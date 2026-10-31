from django.contrib import admin

from social.models import UserReport


@admin.register(UserReport)
class UserReportAdmin(admin.ModelAdmin):
    """Admin configuration for UserReport model."""

    list_display = ["reported", "reporter", "created_at"]
    search_fields = ["reported__username", "reporter__username"]
    raw_id_fields = ["reporter", "reported"]
