"""Phases 1 to 3's phone sheets: Spine Dev's user, set up through real tracking writes.

scripts/ios/seed-activity-checkpoint.sh runs this on the test backend's throwaway copy,
and test_activity_checkpoints.py runs the same steps in CI. It clears the dev user
first, so it can run again over anything, and it uses only manual titles, which search
can't find: searching "Book V" reaches a real book of that name. So every book a row
names opens from Home's In progress, the Library or the "Checkpoint titles" list, and a
book untouched until its row is on that list. Anything dated in the past, and anything
another person does, is a step here: the sheet says when, and the phone does the rest.
"""

from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.utils import timezone

# Show Z's choice queues its calendar on commit through events.tasks, which the served
# backend has loaded and `manage.py shell` hasn't.
import events.tasks  # noqa: F401
from api.services import activity as activity_service
from api.services import social as social_service
from api.services import stats_cache
from api.services.item_metadata import store_series_total
from app import (
    anime_tracking,
    book_tracking,
    game_tracking,
    series_tracking,
    tv_tracking,
    user_calendar,
)
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
# On the list: J and V are untouched until their rows, so they aren't in the Library.
# They share a length and tiles with no title on them, so the list's order tells them
# apart: J, V, X, first.
LISTED_BOOKS = ("J", "V", "X")
# What the sheet's last row has the phone save on, and what print-rows reads.
STORED_TITLES = ("X", "Y", "Z", "W")
OTHER_TITLES = {
    MediaTypes.GAME.value: "Y",
    MediaTypes.MANGA.value: "W",
    MediaTypes.COMIC.value: "CO",
    MediaTypes.ANIME.value: "AN",
    MediaTypes.MOVIE.value: "F1",
    MediaTypes.MUSIC.value: "P1",
}
ANIME_MINUTES = 24
# Phase 2's titles: each one's type and its total. On the list too, so a title
# untouched until its row opens without search.
PHASE_2 = {
    "M1": (MediaTypes.MANGA.value, {"series_total": 100}),
    "M2": (MediaTypes.MANGA.value, {"series_total": 100}),
    "M4": (MediaTypes.MANGA.value, {}),
    "C1": (MediaTypes.COMIC.value, {}),
    "C2": (MediaTypes.COMIC.value, {"series_total": 10}),
    **{f"G{number}": (MediaTypes.GAME.value, {}) for number in range(1, 9)},
    "G2023": (MediaTypes.GAME.value, {}),
    "Q": (MediaTypes.BOOK.value, {}),
}
# Phase 3's shows, each episode 60 minutes, or every number prints ≈. Manual shows keep
# season logs (TV-408).
PHASE_3_SHOWS = {"T1": 10, "T2": 2, "T3": 4, "T4": 5, "T5": 2, "T6": 3, "T7": 4, "T8": 4}
SHOW_MINUTES = 60
# Phase 3's films and album, and their runtimes.
PHASE_3_TITLES = {
    "F": (MediaTypes.MOVIE.value, 120),
    "F2": (MediaTypes.MOVIE.value, 120),
    "P": (MediaTypes.MUSIC.value, 60),
}


def hours(number):
    """Return hours in minutes."""
    return number * 60


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
        item = Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            media_id=media_id(name),
            title=f"Book {name}",
            image=IMAGE,
            total_pages=pages,
        )
        if name in LISTED_BOOKS:
            titles.append(item)
    runtimes = {MediaTypes.MOVIE.value: 120, MediaTypes.MUSIC.value: 120, MediaTypes.ANIME.value: ANIME_MINUTES}
    for media_type, name in OTHER_TITLES.items():
        titles.append(
            Item.objects.create(
                source=Sources.MANUAL.value,
                media_type=media_type,
                media_id=media_id(name),
                title=f"{media_type.title()} {name}",
                image=IMAGE,
                runtime_minutes=runtimes.get(media_type),
            ),
        )
    for name, (media_type, fields) in PHASE_2.items():
        titles.append(
            Item.objects.create(
                source=Sources.MANUAL.value,
                media_type=media_type,
                media_id=media_id(name),
                title=f"{media_type.title()} {name}",
                image=IMAGE,
                **fields,
            ),
        )
    titles.append(_make_show())
    for name, (media_type, minutes) in PHASE_3_TITLES.items():
        titles.append(
            Item.objects.create(
                source=Sources.MANUAL.value,
                media_type=media_type,
                media_id=media_id(name),
                title=f"{media_type.title()} {name}",
                image=IMAGE,
                runtime_minutes=minutes,
            ),
        )
    titles.extend(_make_show(name, episodes, SHOW_MINUTES) for name, episodes in PHASE_3_SHOWS.items())
    listed = CustomList.objects.create(owner=dev, name=LIST_NAME)
    for position, item in enumerate(titles, start=1):
        CustomListItem.objects.create(custom_list=listed, item=item, position=position)


def _make_show(name="Z", episodes=5, minutes=EPISODE_MINUTES):
    """Make a one-season show, a runtime on each episode, or every number prints ≈."""
    show = Item.objects.create(
        source=Sources.MANUAL.value,
        media_type=MediaTypes.TV.value,
        media_id=media_id(name),
        title=f"Show {name}",
        image=IMAGE,
    )
    shared = {"source": Sources.MANUAL.value, "media_id": media_id(name), "image": IMAGE}
    Item.objects.create(media_type=MediaTypes.SEASON.value, season_number=1, title=f"Show {name}", **shared)
    for number in range(1, episodes + 1):
        Item.objects.create(
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=number,
            title=f"Show {name} E{number}",
            runtime_minutes=minutes,
            **shared,
        )
    return show


def show(name):
    """Return one of the sheet's shows."""
    return title(MediaTypes.TV.value, name)


def season(name, number=1):
    """Return one of the sheet's seasons."""
    return Item.objects.get(
        source=Sources.MANUAL.value,
        media_type=MediaTypes.SEASON.value,
        media_id=media_id(name),
        season_number=number,
    )


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
    """Write the sheet's opening state: 830, 360 and 1,540.

    It rewrites every account's timezone and makes users, so it refuses anywhere but the
    test backend's SQLite copy. The image holds this module, and CI's Database checks
    run this suite on Postgres.
    """
    if connection.vendor != "sqlite" and not settings.TESTING:
        msg = f"The checkpoint seed runs only on the test backend's SQLite copy, not {connection.vendor}."
        raise RuntimeError(msg)
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
        _phase_2(dev)
        _phase_3(dev)
        stats_cache.bump_user(dev)


def _phase_3(dev):
    """Phase 3's opening: TV 10 hours in 2025, Nothing here yet in 2026, 10 in All Time.

    T1's 10 episodes were logged as a season on May 1 2025. Everything else starts
    untouched, on the list.
    """
    with on(date(2025, 5, 1)):
        tv_tracking.create_log(dev, season("T1"), consumed_at=date(2025, 5, 1), mutation_id=uuid4())


def _series(user, name, start, *saves):
    """Start a manga or comic on a day and save each (value, day)."""
    item = title(PHASE_2[name][0], name)
    with on(start):
        series_tracking.start(user, item)
    for value, day in saves:
        with on(day):
            series_tracking.update_progress(user, item, value=value)
    return item


def _play(user, name, start, *saves):
    """Start a game on a day and save each (minutes, day), as its sheet does."""
    item = title(MediaTypes.GAME.value, name)
    with on(start):
        game_tracking.start(user, item)
    for minutes, day in saves:
        with on(day):
            game_tracking.update_progress(user, item, total_minutes=minutes, progressed_on=day)
    return item


def _phase_2(dev):
    """Phase 2's opening: manga 30, 20, 150; comics 30, none, 40; games 90h, 4h, 94h."""
    _series(dev, "M1", date(2025, 12, 20), (30, date(2025, 12, 20)), (50, date(2026, 3, 1)))
    with on(date(2026, 1, 15)):
        series_tracking.mark_read(dev, title(MediaTypes.MANGA.value, "M2"))
    _series(dev, "C1", date(2025, 12, 28), (300, date(2025, 12, 28)), (30, date(2026, 1, 2)))
    with on(date(2026, 1, 15)):
        series_tracking.mark_read(dev, title(MediaTypes.COMIC.value, "C2"))
    anime = title(MediaTypes.ANIME.value, OTHER_TITLES[MediaTypes.ANIME.value])
    with on(date(2026, 2, 1)):
        # A manual anime's total is what its owner types (AN-100): 12 ticks that day.
        anime_tracking.write_progress(dev, anime, 12)
        anime_tracking.create_log(dev, anime, consumed_at=date(2026, 2, 1))
    _play(dev, "G1", date(2025, 12, 20), (hours(10), date(2025, 12, 20)), (hours(12), date(2026, 3, 1)))
    _play(dev, "G2", date(2025, 12, 20), (hours(40), date(2025, 12, 20)), (hours(40), date(2026, 1, 2)))
    g3 = _play(dev, "G3", date(2025, 12, 20), (hours(10), date(2025, 12, 20)))
    with on(date(2026, 1, 5)):
        game_tracking.complete(dev, g3, completion_date=date(2026, 1, 5), total_minutes=hours(12), mutation_id=uuid4())
    g4 = _play(dev, "G4", date(2026, 2, 1))
    with on(date(2026, 2, 2)):
        game_tracking.update_progress(dev, g4, percentage=40, progressed_on=date(2026, 2, 2))
    _play(dev, "G5", date(2025, 12, 20), (hours(300), date(2025, 12, 20)), (hours(30), date(2026, 1, 2)))


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


def new_issue_c2():
    """Run the new-issue job on C2, as the calendar does: an 11th issue is out."""
    item = title(MediaTypes.COMIC.value, "C2")
    store_series_total(item, {"max_progress": 11})
    series_tracking.new_issue_out(item, previous_total=10)


def add_g6():
    """Add G6: started and 10 hours on Mar 1 2026."""
    _play(dev_user(), "G6", date(2026, 3, 1), (hours(10), date(2026, 3, 1)))


def add_g8():
    """Add G8: 5 hours on Dec 20 2025, stopped Dec 21."""
    item = _play(dev_user(), "G8", date(2025, 12, 20), (hours(5), date(2025, 12, 20)))
    with on(date(2025, 12, 21)):
        game_tracking.drop(dev_user(), item, end_date=date(2025, 12, 21))


def add_m4():
    """Add M4: a manga with no total, marked with the eye."""
    with on(date(2026, 1, 15)):
        series_tracking.mark_read(dev_user(), title(MediaTypes.MANGA.value, "M4"))


def add_q():
    """Add Q: a book with no page count, 25% on Jun 2 2023."""
    item = title(MediaTypes.BOOK.value, "Q")
    with on(date(2023, 6, 2)):
        book_tracking.start(dev_user(), item)
        book_tracking.update_progress(dev_user(), item, progress_type="percentage", value=25)


def add_t4():
    """Add T4: 3 of its 5 episodes ticked on Mar 2 2026."""
    with on(date(2026, 3, 2)):
        for number in (1, 2, 3):
            tv_tracking.tick(dev_user(), show("T4"), 1, number, mutation_id=uuid4())


def add_t6():
    """Add T6: its season logged May 1 2025, then rewatched by 3 ticks on Jun 1 2026."""
    dev = dev_user()
    with on(date(2025, 5, 1)):
        tv_tracking.create_log(dev, season("T6"), consumed_at=date(2025, 5, 1), mutation_id=uuid4())
    with on(date(2026, 6, 1)):
        tv_tracking.start_rewatch(dev, show("T6"), season_number=1, mutation_id=uuid4())
        for number in (1, 2, 3):
            tv_tracking.tick(dev, show("T6"), 1, number, mutation_id=uuid4())


def add_game_2023():
    """Add a game started Jul 1 2023 with no hours."""
    _play(dev_user(), "G2023", date(2023, 7, 1))


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
    """Delete the friend's reading, then save 10 pages for them on that same day.

    The day comes from their save: a sheet can run past midnight, and on another day
    this would read 0 likes even with the bug back.
    """
    friend = friend_user()
    item = book("Friend")
    day = _friend_save().saved_on
    with on(day, at="13:00"):
        book_tracking.delete_journey(friend, item, Book.objects.get(user=friend, item=item).current_session_id)
    with on(day, at="14:00"):
        book_tracking.start(friend, item)
        book_tracking.update_progress(friend, item, progress_type="pages", value=10)


def print_rows():
    """Print every row the sheet's last row stored, and flag any not saved today."""
    names = [media_id(name) for name in STORED_TITLES]
    rows = ProgressChange.objects.filter(actor=dev_user(), item__media_id__in=names).select_related("item").order_by("id")
    today = timezone.now().astimezone(ZoneInfo(NEW_YORK)).date()
    for change in rows:
        saved = change.current_progress
        began_over = " began_over" if saved.get("began_over") else ""
        not_today = "" if change.saved_on == today else " NOT TODAY"
        print(change.saved_on, change.item.title, saved.get("kind"), saved.get("move", saved.get("value")), f"{began_over}{not_today}")  # noqa: T201


STEPS = {
    "seed": seed,
    "add-k-l": add_k_and_l,
    "add-s": add_s,
    "add-u": add_u,
    "add-r": add_r,
    "like-from-tokyo": like_from_tokyo,
    "friend-starts-over": friend_starts_over,
    "print-rows": print_rows,
    "new-issue-c2": new_issue_c2,
    "add-g6": add_g6,
    "add-g8": add_g8,
    "add-m4": add_m4,
    "add-q": add_q,
    "add-game-2023": add_game_2023,
    "add-t4": add_t4,
    "add-t6": add_t6,
}
