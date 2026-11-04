"""Phase 1's phone sheet: Spine Dev's user, set up through the real tracking functions.

scripts/ios/seed-activity-checkpoint.sh runs this on the test backend's throwaway copy,
and test_activity_checkpoints.py runs the same steps in CI. It clears the dev user
first, so it can run again over anything, and it uses only manual titles, on one list,
because search can't find those. Anything dated in the past, and anything another person
does, is a step here: the sheet says when, and the phone does the rest.
"""

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from api.services import activity as activity_service
from api.services import social as social_service
from api.services import stats_cache
from app import book_tracking, tv_tracking, user_calendar
from app.models import (
    Book,
    DiaryEntry,
    Episode,
    Item,
    ItemCredit,
    ItemFilterFacet,
    Media,
    MediaLike,
    MediaTypes,
    Sources,
    Status,
)
from lists.models import CustomList, CustomListItem
from social.models import (
    Comment,
    ContentLike,
    Follow,
    FollowStatus,
    ProgressChange,
    ProgressPost,
)

DEV_USERNAME = "claude"
FRIEND_USERNAME = "checkpoint-friend"
TOKYO_USERNAME = "checkpoint-tokyo"
NEW_YORK = "America/New_York"
SHEET_YEAR = 2026
LIST_NAME = "Checkpoint titles"
PREFIX = "checkpoint-"
IMAGE = "https://example.com/checkpoint.jpg"
EPISODE_MINUTES = 45
PLANNING = Status.PLANNING.value
# What each title's length is; None has no page count.
BOOKS = {
    "A": 400,
    "B": 400,
    "C": 300,
    "D": 400,
    "E": 350,
    "F": 400,
    "G": None,
    "H": 200,
    "J": 300,
    "V": 300,
    "K": 400,
    "L": 400,
    "S": 400,
    "U": None,
    "R": 285,
    "X": 300,
    "Friend": 400,
}
OTHER_TITLES = {
    MediaTypes.GAME.value: "Y",
    MediaTypes.MANGA.value: "W",
    MediaTypes.COMIC.value: "CO",
    MediaTypes.ANIME.value: "AN",
    MediaTypes.MOVIE.value: "F1",
    MediaTypes.MUSIC.value: "P1",
}


@contextmanager
def on(day, at="12:00"):
    """Pin the clock to this time on a day, New York time."""
    hour, minute = (int(part) for part in at.split(":"))
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(NEW_YORK))
    with patch("django.utils.timezone.now", return_value=moment), timezone.override(ZoneInfo(NEW_YORK)):
        yield


def media_id(name):
    """Return a title's manual id, shared with every sheet."""
    return f"{PREFIX}{name.lower()}"


def book(name):
    """Return one of the sheet's books."""
    return title(MediaTypes.BOOK.value, name)


def title(media_type, name):
    """Return one of the sheet's titles."""
    return Item.objects.get(source=Sources.MANUAL.value, media_type=media_type, media_id=media_id(name))


def show_z():
    """Return show Z."""
    return title(MediaTypes.TV.value, "Z")


def dev_user():
    """Return Spine Dev's user."""
    return get_user_model().objects.get(username=DEV_USERNAME)


def friend_user():
    """Return the friend the dev user follows."""
    return get_user_model().objects.get(username=FRIEND_USERNAME)


def _settle_users():
    """Put every user on New York, and the dev user's settings as the sheet reads."""
    users = get_user_model()
    users.objects.update(calendar_timezone=NEW_YORK)
    dev, _ = users.objects.get_or_create(username=DEV_USERNAME)
    for field in dev._meta.fields:
        if field.name.endswith("_enabled") and field.name.split("_")[0] in MediaTypes.values:
            setattr(dev, field.name, True)
    dev.week_start_day = "monday"
    dev.calendar_timezone = NEW_YORK
    dev.save()
    for username in (FRIEND_USERNAME, TOKYO_USERNAME):
        person, _ = users.objects.get_or_create(username=username)
        person.calendar_timezone = NEW_YORK
        person.save(update_fields=["calendar_timezone"])
    return dev


def _clear_person(user):
    """Clear what a person has, so a sheet opens the same however it was left."""
    DiaryEntry.objects.filter(user=user).delete()
    Episode.objects.filter(related_season__user=user).delete()
    for model in apps.get_models():
        if issubclass(model, Media):
            model.objects.filter(user=user).delete()
    MediaLike.objects.filter(user=user).delete()
    posts = ProgressPost.objects.filter(actor=user)
    ContentLike.objects.filter(target_type=ContentLike.PROGRESS_POST, target_id__in=posts.values("id")).delete()
    posts.delete()
    ProgressChange.objects.filter(actor=user).delete()
    ContentLike.objects.filter(user=user).delete()
    Comment.objects.filter(user=user).delete()
    CustomList.objects.filter(owner=user).delete()
    Follow.objects.filter(from_user=user).delete()
    Follow.objects.filter(to_user=user).delete()
    stats_cache.bump_user(user)


def _make_titles(dev):
    """Reset the Items the sheet owns, and put one of each type on one list."""
    Item.objects.filter(source=Sources.MANUAL.value, media_id__startswith=PREFIX).delete()
    titles = []
    for name, pages in BOOKS.items():
        titles.append(
            Item.objects.create(
                source=Sources.MANUAL.value,
                media_type=MediaTypes.BOOK.value,
                media_id=media_id(name),
                title=f"Book {name}",
                image=IMAGE,
                total_pages=pages,
            ),
        )
    for media_type, name in OTHER_TITLES.items():
        titles.append(
            Item.objects.create(
                source=Sources.MANUAL.value,
                media_type=media_type,
                media_id=media_id(name),
                title=f"{media_type.title()} {name}",
                image=IMAGE,
                runtime_minutes=120 if media_type in (MediaTypes.MOVIE.value, MediaTypes.MUSIC.value) else None,
            ),
        )
    titles.append(_make_show())
    listed = CustomList.objects.create(owner=dev, name=LIST_NAME)
    for item in titles:
        if item.title.startswith("Book ") and not item.title.endswith(" X"):
            continue
        CustomListItem.objects.create(custom_list=listed, item=item)


def _make_show():
    """Make show Z, with runtimes on each episode, or every number prints ≈."""
    show = Item.objects.create(
        source=Sources.MANUAL.value,
        media_type=MediaTypes.TV.value,
        media_id=media_id("Z"),
        title="Show Z",
        image=IMAGE,
    )
    shared = {"source": Sources.MANUAL.value, "media_id": media_id("Z"), "image": IMAGE}
    Item.objects.create(media_type=MediaTypes.SEASON.value, season_number=1, title="Show Z", **shared)
    for number in range(1, 6):
        Item.objects.create(
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=number,
            title=f"Show Z E{number}",
            runtime_minutes=EPISODE_MINUTES,
            **shared,
        )
    return show


def _read(user, name, start, *saves, stop_on=None):
    """Start a book on a day, save each (value, day), and stop it if asked."""
    item = book(name)
    with on(start):
        book_tracking.start(user, item)
    for value, day, *kind in saves:
        with on(day):
            book_tracking.update_progress(user, item, progress_type=kind[0] if kind else "pages", value=value)
    if stop_on:
        with on(stop_on):
            book_tracking.drop(user, item)
    return item


def _log(user, item, day):
    with on(day):
        return book_tracking.complete(user, item, completion_date=day, mutation_id=uuid4())


def seed():
    """Write the sheet's opening state: 830, 360 and 1,540."""
    with transaction.atomic():
        dev = _settle_users()
        for person in (dev, friend_user()):
            _clear_person(person)
        _make_titles(dev)
        _read(dev, "A", date(2025, 12, 1), (100, date(2025, 12, 20)), (200, date(2026, 3, 1)))
        _read(dev, "B", date(2025, 12, 20), (300, date(2025, 12, 28)), (30, date(2026, 1, 2)))
        _log(dev, book("C"), date(2025, 6, 1))
        _read(dev, "C", date(2026, 2, 1), (40, date(2026, 2, 10)))
        d_book = _read(dev, "D", date(2025, 11, 1), (200, date(2025, 11, 10)), stop_on=date(2025, 11, 15))
        with on(date(2026, 4, 1)):
            book_tracking.start(dev, d_book)
        with on(date(2026, 4, 2)):
            book_tracking.update_progress(dev, d_book, progress_type="pages", value=250)
        with on(date(2026, 1, 15)):
            book_tracking.mark_read(dev, book("E"))
        f_book = _read(dev, "F", date(2026, 5, 1), (120, date(2026, 5, 2)))
        with on(date(2026, 5, 10)):
            book_tracking.restart(dev, f_book)
        with on(date(2026, 5, 11)):
            book_tracking.update_progress(dev, f_book, progress_type="pages", value=50)
        _read(dev, "G", date(2026, 6, 1), (25, date(2026, 6, 2), "percentage"))
        _log(dev, book("H"), date(2025, 3, 1))
        with on(date(2026, 6, 15)):
            tv_tracking.choose(dev, show_z(), PLANNING, entry_point=tv_tracking.APP)
        Follow.objects.create(from_user=dev, to_user=friend_user(), status=FollowStatus.ACCEPTED)
        _friend_reads(friend_user())
        stats_cache.bump_user(dev)


def yesterday():
    """Return yesterday in New York, from the clock as it is."""
    return timezone.now().astimezone(ZoneInfo(NEW_YORK)).date() - timedelta(days=1)


def _friend_reads(friend):
    day = yesterday()
    _read(friend, "Friend", day, (40, day))


# The sheet's steps. Each runs from the Mac, by name.


def add_k_and_l():
    """Add K (started in March) and L (started last December)."""
    dev = dev_user()
    _read(dev, "K", date(2026, 3, 1), (100, date(2026, 3, 10)))
    _read(dev, "L", date(2025, 12, 1), (100, date(2026, 1, 10)))


def add_s():
    """Add S, stopped at 120 of 400."""
    _read(dev_user(), "S", date(2026, 10, 1), (120, date(2026, 10, 2)), stop_on=date(2026, 10, 3))


def add_u():
    """Add U, with no page count, stopped at 25%."""
    _read(dev_user(), "U", date(2026, 7, 1), (25, date(2026, 7, 2), "percentage"), stop_on=date(2026, 7, 3))


def add_r():
    """Add R: a year with reading and no log, with its genre and author."""
    item = book("R")
    ItemFilterFacet.objects.get_or_create(item=item, facet_type=ItemFilterFacet.FacetType.GENRE, value="Fantasy")
    ItemCredit.objects.get_or_create(
        item=item,
        role=ItemCredit.Role.AUTHOR.value,
        person_source="manual",
        person_id="checkpoint-author",
        defaults={"name": "Checkpoint Author", "order": 0},
    )
    day = date(2024, 3, 10)
    _read(dev_user(), "R", day, (285, day))


def _friend_save():
    item = book("Friend")
    friend = friend_user()
    return ProgressChange.objects.filter(actor=friend, item=item, current_progress__kind="pages").latest("id")


def like_from_tokyo():
    """Like the friend's row as a second person, on a phone in Tokyo."""
    liker = get_user_model().objects.get(username=TOKYO_USERNAME)
    with timezone.override(ZoneInfo("Asia/Tokyo")):
        token = user_calendar.REQUEST_TIMEZONE.set(ZoneInfo("Asia/Tokyo"))
        try:
            post = activity_service.progress_post(liker, _friend_save().pk)
            social_service.set_like(liker, target_type=ContentLike.PROGRESS_POST, target_id=post.pk, liked=True)
        finally:
            user_calendar.REQUEST_TIMEZONE.reset(token)


def friend_starts_over():
    """Delete the friend's reading, then save 10 pages for them on that same day."""
    friend = friend_user()
    item = book("Friend")
    day = yesterday()
    with on(day, at="13:00"):
        book_tracking.delete_journey(friend, item, Book.objects.get(user=friend, item=item).current_session_id)
    with on(day, at="14:00"):
        book_tracking.start(friend, item)
        book_tracking.update_progress(friend, item, progress_type="pages", value=10)


def print_rows():
    """Print what the status moves stored, for the sheet's last row."""
    rows = ProgressChange.objects.filter(actor=dev_user(), item__media_id__startswith=PREFIX).order_by("id")
    today = timezone.now().astimezone(ZoneInfo(NEW_YORK)).date()
    for change in rows.select_related("item").filter(saved_on=today):
        saved = change.current_progress
        began_over = " began_over" if saved.get("began_over") else ""
        print(change.saved_on, change.item.title, saved.get("kind"), saved.get("move", saved.get("value")), began_over)  # noqa: T201


STEPS = {
    "seed": seed,
    "add-k-l": add_k_and_l,
    "add-s": add_s,
    "add-u": add_u,
    "add-r": add_r,
    "like-from-tokyo": like_from_tokyo,
    "friend-starts-over": friend_starts_over,
    "print-rows": print_rows,
}
