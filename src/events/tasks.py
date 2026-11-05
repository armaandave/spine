import logging

from celery import shared_task

from app import anime_titles, anime_tracking
from events import notifications, tv_airing
from events.calendar.main import fetch_releases

logger = logging.getLogger(__name__)


@shared_task(name="Reload calendar")
def reload_calendar(user=None, items_to_process=None):
    """Refresh the calendar with latest dates for all users."""
    if user:
        logger.info("Reloading calendar for user: %s", user.username)
    else:
        logger.info("Reloading calendar for all users")

    return fetch_releases(
        user=user,
        items_to_process=items_to_process,
    )


@shared_task(name="Send release notifications")
def send_release_notifications():
    """Send notifications for recently released media."""
    logger.info("Starting recent release notification task")

    return notifications.send_releases()


@shared_task(name="Send daily digest")
def send_daily_digest_notifications():
    """Send daily digest of today's releases."""
    logger.info("Starting daily digest task")

    return notifications.send_daily_digest()


@shared_task(name="Refresh aired TV")
def refresh_aired_tv():
    """Reopen shows whose next episode aired; work out shows left unworked (TV-206)."""
    logger.info("Starting aired TV refresh")

    return tv_airing.refresh_aired_shows()


@shared_task(name="Refresh aired anime")
def refresh_aired_anime():
    """Reopen anime whose next episode aired (AN-202, TV-206)."""
    logger.info("Starting aired anime refresh")

    return {"changed": anime_tracking.refresh_due_titles()}


@shared_task(name="Fill anime titles")
def fill_anime_titles():
    """Fill the English title of every anime Spine has read none for (AN-705)."""
    logger.info("Starting anime title fill")

    return anime_titles.fill_missing()
