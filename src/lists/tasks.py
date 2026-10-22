"""Celery tasks for native Spine lists."""

from celery import shared_task
from django.core.management import call_command

# Registers "Dispatch featured lists" and "Sync featured list" (featured queue).
from lists.featured.dispatch import dispatch_featured_lists, sync_featured_list  # noqa: F401

# Registers "Enrich featured list ratings" (default queue).
from lists.featured.ratings import enrich_featured_ratings  # noqa: F401


# The featured-lists dispatcher syncs the NYT charts too; skip any it synced recently.
NYT_SKIP_SYNCED_WITHIN_HOURS = 12


@shared_task(name="Sync NYT featured lists")
def sync_nyt_featured_lists():
    """Refresh the approved NYT Best Sellers lists owned by Spine (production's beat)."""
    return call_command(
        "sync_nyt_featured_lists",
        owner="Spine",
        skip_synced_within=NYT_SKIP_SYNCED_WITHIN_HOURS,
    )
