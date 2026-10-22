"""Celery tasks for native Spine lists."""

from celery import shared_task
from django.core.management import call_command

# Registers "Dispatch featured lists" and "Sync featured list" (featured queue).
from lists.featured.dispatch import dispatch_featured_lists, sync_featured_list  # noqa: F401

LETTERBOXD_TOP_500_MDBLIST_URL = "https://mdblist.com/lists/davearmaan12/external/155837"


@shared_task(name="Sync NYT featured lists")
def sync_nyt_featured_lists():
    """Refresh the approved NYT Best Sellers lists owned by Spine."""
    return call_command("sync_nyt_featured_lists", owner="Spine")


@shared_task(name="Sync Letterboxd Top 500 featured list")
def sync_letterboxd_top_500_featured_list():
    """Refresh Spine's Letterboxd Top 500 list from its MDBList mirror."""
    return call_command(
        "sync_mdblist_featured_list",
        LETTERBOXD_TOP_500_MDBLIST_URL,
        owner="Spine",
    )
