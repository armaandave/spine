"""Real saves for the feed's tests: a day's progress comes from the calculation now.

A raw ProgressChange row isn't read for a TV day's amount (it comes from the viewings),
and a raw count row is no save of a book, so these tests write through the same
functions the phone's requests do, under a pinned clock.
"""

from unittest.mock import patch

from app import tv_tracking
from app.models import Item, MediaTypes
from app.tests.tv_fixtures import make_manual_show


def show_with_episodes(title="Severance", seasons=None, runtime=45):
    """Return a manual show whose episodes run `runtime` minutes."""
    show = make_manual_show(title=title, seasons=seasons or {1: 10, 2: 10})
    Item.objects.filter(media_id=show.media_id, media_type=MediaTypes.EPISODE.value).update(runtime_minutes=runtime)
    return show


def tick(user, show, episodes, when, season=1):
    """Tick these episode numbers of a season, as if at this moment."""
    for number in episodes:
        with patch("django.utils.timezone.now", return_value=when):
            tv_tracking.tick(user, show, season, number)


def address(response_item):
    """Return the id a feed item's page, like and comments are reached by."""
    return response_item["object"]["id"]


def address_of(user, show, day):
    """Return the id a show's day is reached by: person, title and day (plan 5.8)."""
    return (user.pk << 44) | (show.pk << 20) | day.toordinal()
