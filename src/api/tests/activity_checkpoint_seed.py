"""Phases 1 to 5's phone sheets: Spine Dev's user, set up through real tracking writes.

scripts/ios/seed-activity-checkpoint.sh runs this on the test backend's throwaway copy,
and test_activity_checkpoints.py runs the same steps in CI. It clears the dev user
first, so it can run again over anything, and it uses only manual titles, which search
can't find: searching "Book V" reaches a real book of that name. So every book a row
names opens from Home's In progress, the Library or the "Checkpoint titles" list, and a
book untouched until its row is on that list. Anything dated in the past, and anything
another person does, is a step here: the sheet says when, and the phone does the rest.
"""

import io
import zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import Client
from django.test.utils import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

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
    single_weight,
    tv_tracking,
    user_calendar,
)
from app.models import (
    AnimeViewing,
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
from integrations.imports.letterboxd.importer import LetterboxdImporter
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
    "N": 300,
    "Friend": 400,
}
# What the seeded friend reads on Phase 4's sheet. None are on the dev user's list.
FRIEND_BOOKS = {"FB1": 400, "FB2": 400, "FB3": 400, "FB4": 400, "FB5": 400}
# The friend's shows (each episode 60 minutes), game, films and a heart's book.
FRIEND_SHOWS = {"FS": 8, "FS2": 5, "FS3": 3, "FS4": 6, "FS5": 5, "FS6": 5}
# On the list: J and V are untouched until their rows, so they aren't in the Library.
# They share a length and tiles with no title on them, so the list's order tells them
# apart: J, V, X, first.
LISTED_BOOKS = ("J", "V", "X", "N")
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
PHASE_3_SHOWS = {"T1": 10, "T2": 2, "T3": 4, "T4": 5, "T5": 2, "T6": 3, "T7": 4, "T8": 4, "T9": 3}
SHOW_MINUTES = 60
# Phase 3's films and album, and their runtimes.
PHASE_3_TITLES = {
    "F": (MediaTypes.MOVIE.value, 120),
    "F2": (MediaTypes.MOVIE.value, 120),
    "P": (MediaTypes.MUSIC.value, 60),
    "F3": (MediaTypes.MOVIE.value, 135),
}
# The friend's films and game, and their runtimes.
FRIEND_TITLES = {
    "FM": (MediaTypes.MOVIE.value, 120),
    "FM2": (MediaTypes.MOVIE.value, 120),
    "FG": (MediaTypes.GAME.value, None),
}


# Phase 5's titles. Three shows to add to Planning and a fourth with episodes to tick,
# a book, a game and a manga to add as To Read, two films the Mac adds as the website
# does, and the friend's three. Book P and manga M3 are being read, from last year.
PHASE_5_SHOWS = {"PS1": 3, "PS2": 3, "PS3": 3, "PS4": 4}
PHASE_5_PLANNING = {
    "PB": (MediaTypes.BOOK.value, {"total_pages": 300}),
    "PG": (MediaTypes.GAME.value, {}),
    "PM": (MediaTypes.MANGA.value, {"series_total": 100}),
}
PHASE_5_READS = {
    "P": (MediaTypes.BOOK.value, {"total_pages": 400}),
    "M3": (MediaTypes.MANGA.value, {"series_total": 100}),
}
PHASE_5_FILMS = ("W1", "W2")
PHASE_5_FRIEND_FILMS = ("FP1", "FP2", "FP3")
FILM_MINUTES = 120
WATCHLIST_FILMS = 3


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
    # Viewings first: a log can't go while a viewing names it (TV-406, AN-901).
    Episode.objects.filter(related_season__user=user).delete()
    AnimeViewing.objects.filter(anime__user=user).delete()
    DiaryEntry.objects.filter(user=user).delete()
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
    for name, pages in FRIEND_BOOKS.items():
        Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            media_id=media_id(name),
            title=f"Book {name}",
            image=IMAGE,
            total_pages=pages,
        )
    for name, (media_type, minutes) in FRIEND_TITLES.items():
        Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=media_id(name),
            title=f"{media_type.title()} {name}",
            image=IMAGE,
            runtime_minutes=minutes,
        )
    for name, episodes in FRIEND_SHOWS.items():
        _make_show(name, episodes, SHOW_MINUTES)
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
    """Write the sheet's opening state: 830, 360 and 1,540, and Phase 3's T1."""
    _seed(_phase_3)


def seed_phase_4():
    """Write Phase 4's opening: Phase 1 and 2's titles, and what it adds."""
    _seed(_phase_4, friend_reads=False)


def seed_phase_5():
    """Write Phase 5's opening: no books, logs or saves but P and M3, from last year.

    Everything else is untracked and on the list: 3 shows, a fourth with episodes, a
    book, a game, a manga and films of 120 minutes. The dates are last year's on
    purpose, so the sheet works on any day.
    """
    _refuse_unless_sqlite()
    with transaction.atomic():
        dev = _settle_users()
        for person in (dev, friend_user()):
            _clear_person(person)
        _make_phase_5_titles(dev)
        Follow.objects.create(from_user=dev, to_user=friend_user(), status=FollowStatus.ACCEPTED)
        _read(dev, "P", date(2025, 11, 1), (100, date(2025, 11, 2)))
        manga = title(MediaTypes.MANGA.value, "M3")
        with on(date(2025, 11, 1)):
            series_tracking.start(dev, manga)
        with on(date(2025, 11, 2)):
            series_tracking.update_progress(dev, manga, value=10)
        stats_cache.bump_user(dev)


def _make_phase_5_titles(dev):
    """Reset the Items Phase 5 owns, and put the ones the phone opens on one list."""
    Item.objects.filter(source=Sources.MANUAL.value, media_id__startswith=PREFIX).delete()

    def make(media_type, name, **fields):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=media_id(name),
            title=f"{media_type.title()} {name}",
            image=IMAGE,
            **fields,
        )

    titles = [_make_show(name, episodes, SHOW_MINUTES) for name, episodes in PHASE_5_SHOWS.items()]
    titles.extend(make(media_type, name, **fields) for name, (media_type, fields) in PHASE_5_PLANNING.items())
    titles.extend(make(media_type, name, **fields) for name, (media_type, fields) in PHASE_5_READS.items())
    titles.extend(make(MediaTypes.MOVIE.value, name, runtime_minutes=FILM_MINUTES) for name in PHASE_5_FILMS)
    for name in PHASE_5_FRIEND_FILMS:
        make(MediaTypes.MOVIE.value, name, runtime_minutes=FILM_MINUTES)
    listed = CustomList.objects.create(owner=dev, name=LIST_NAME)
    for position, item in enumerate(titles, start=1):
        CustomListItem.objects.create(custom_list=listed, item=item, position=position)


def _seed(*openings, friend_reads=True):
    """Write the sheet's opening state, and then each phase's own.

    It rewrites every account's timezone and makes users, so it refuses anywhere but the
    test backend's SQLite copy. The image holds this module, and CI's Database checks
    run this suite on Postgres.
    """
    _refuse_unless_sqlite()
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
        if friend_reads:
            _friend_reads(friend_user())
        _phase_2(dev)
        for opening in openings:
            opening(dev)
        stats_cache.bump_user(dev)


def _refuse_unless_sqlite():
    if connection.vendor != "sqlite" and not settings.TESTING:
        msg = f"The checkpoint seed runs only on the test backend's SQLite copy, not {connection.vendor}."
        raise RuntimeError(msg)


def _phase_3(dev):
    """Phase 3's opening: TV 10 hours in 2025, Nothing here yet in 2026, 10 in All Time.

    T1's 10 episodes were logged as a season on May 1 2025. Everything else starts
    untouched, on the list.
    """
    with on(date(2025, 5, 1)):
        tv_tracking.create_log(dev, season("T1"), consumed_at=date(2025, 5, 1), mutation_id=uuid4())


def _phase_4(dev):
    """Phase 4's opening, on Phase 1 and 2's titles: no T1 log, so TV starts at 3 hours.

    Show T2 has 2 ticks at 60 minutes, T3 a season eye on 4 episodes, a 135-minute film
    is logged on Mar 1 2026, G7 has the eye and M2 and C2 their marks (Phase 2's). T9, 3
    episodes, has a season log and a review of S1 E2 in 2025.
    """
    with on(date(2026, 1, 15)):
        game_tracking.mark_completed(dev, title(MediaTypes.GAME.value, "G7"))
    with on(date(2026, 3, 2)):
        for number in (1, 2):
            tv_tracking.tick(dev, show("T2"), 1, number, mutation_id=uuid4())
    with on(date(2026, 1, 15)):
        tv_tracking.mark_watched(dev, show("T3"), season_number=1, mutation_id=uuid4())
    with on(date(2026, 3, 1)):
        single_weight.create_log(dev, title(MediaTypes.MOVIE.value, "F3"), consumed_at=date(2026, 3, 1))
    with on(date(2025, 5, 1)):
        tv_tracking.create_log(dev, season("T9"), consumed_at=date(2025, 5, 1), mutation_id=uuid4())
    episode = Item.objects.get(
        source=Sources.MANUAL.value,
        media_type=MediaTypes.EPISODE.value,
        media_id=media_id("T9"),
        season_number=1,
        episode_number=2,
    )
    with on(date(2025, 5, 2)):
        tv_tracking.create_log(
            dev,
            episode,
            consumed_at=date(2025, 5, 2),
            review="Quiet and strange.",
            is_rewatch=False,
            mutation_id=uuid4(),
        )


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


# Phase 4's friend: what they do, run from the Mac. Each step names its days, and those
# that say "the day before" read from today.


def today():
    """Return today in New York, from the clock as it is."""
    return timezone.now().astimezone(ZoneInfo(NEW_YORK)).date()


def friend_item(media_type, name):
    """Return one of the friend's titles."""
    return title(media_type, name)


def add_n():
    """Add book N: started Aug 1 2026, with no reading."""
    with on(date(2026, 8, 1)):
        book_tracking.start(dev_user(), book("N"))


def friend_day_order():
    """Book save 9 am, movie log 10 am, book save 10 pm, book save next morning."""
    friend, item, film = friend_user(), book("FB1"), friend_item(MediaTypes.MOVIE.value, "FM")
    before, after = today() - timedelta(days=2), today() - timedelta(days=1)
    with on(before, at="09:00"):
        book_tracking.start(friend, item)
        book_tracking.update_progress(friend, item, progress_type="pages", value=60)
    with on(before, at="10:00"):
        single_weight.create_log(friend, film, consumed_at=before)
    with on(before, at="22:00"):
        book_tracking.update_progress(friend, item, progress_type="pages", value=100)
    with on(after, at="08:00"):
        book_tracking.update_progress(friend, item, progress_type="pages", value=130)


def friend_typo():
    """300 pages on Dec 28 2025, which the dev user likes from the Mac."""
    friend, item = friend_user(), book("FB2")
    with on(date(2025, 12, 28)):
        book_tracking.start(friend, item)
        book_tracking.update_progress(friend, item, progress_type="pages", value=300)
    dev = dev_user()
    change = ProgressChange.objects.filter(actor=friend, item=item, current_progress__kind="pages").latest("id")
    post = activity_service.progress_post(dev, change.pk)
    social_service.set_like(dev, target_type=ContentLike.PROGRESS_POST, target_id=post.pk, liked=True)


def friend_typo_fixed():
    """Fix that typo to 30 on Jan 2 2026."""
    with on(date(2026, 1, 2)):
        book_tracking.update_progress(friend_user(), book("FB2"), progress_type="pages", value=30)


def friend_save_and_finish():
    """100 pages and a finish of a 400-page book, on one day."""
    friend, item = friend_user(), book("FB3")
    day = today() - timedelta(days=1)
    with on(day, at="09:00"):
        book_tracking.start(friend, item)
        book_tracking.update_progress(friend, item, progress_type="pages", value=100)
    with on(day, at="10:00"):
        book_tracking.complete(friend, item, completion_date=day, mutation_id=uuid4())


def friend_pause_stop_pick_up():
    """Save 40 and pause Monday, resume Tuesday, stop Wednesday, restart Thursday."""
    friend, item = friend_user(), book("FB4")
    monday = date(2026, 10, 12)
    with on(monday):
        book_tracking.start(friend, item)
        book_tracking.update_progress(friend, item, progress_type="pages", value=40)
        book_tracking.pause(friend, item)
    with on(monday + timedelta(days=1)):
        book_tracking.resume(friend, item)
    with on(monday + timedelta(days=2)):
        book_tracking.drop(friend, item)
    with on(monday + timedelta(days=3)):
        book_tracking.start(friend, item)


def friend_game_stops_then_plays():
    """Stop a game, then choose Playing the next day."""
    friend, game = friend_user(), friend_item(MediaTypes.GAME.value, "FG")
    day = date(2026, 10, 12)
    with on(day):
        game_tracking.start(friend, game)
    with on(day + timedelta(days=1)):
        game_tracking.drop(friend, game)
    with on(day + timedelta(days=2)):
        game_tracking.start(friend, game)


def friend_ticks():
    """Tick E1 and E2 Monday; tick E3 and untick E1 Tuesday."""
    friend, shown = friend_user(), show("FS")
    monday = date(2026, 10, 19)
    with on(monday):
        for number in (1, 2):
            tv_tracking.tick(friend, shown, 1, number, mutation_id=uuid4())
    with on(monday + timedelta(days=1)):
        tv_tracking.tick(friend, shown, 1, 3, mutation_id=uuid4())
        tv_tracking.untick(friend, shown, 1, 1, mutation_id=uuid4())


def friend_late_tick():
    """Tick E4, dated that Monday."""
    friend = friend_user()
    moment = datetime(2026, 10, 19, 20, 0, tzinfo=ZoneInfo(NEW_YORK))
    with on(date(2026, 10, 21)):
        tv_tracking.tick(
            friend,
            show("FS"),
            1,
            4,
            at=moment,
            entry_point=tv_tracking.WEBHOOK,
            mutation_id=uuid4(),
        )


def friend_import_last_week():
    """Import show plays dated last week, as a file does (decision 47)."""
    friend, shown = friend_user(), show("FS3")
    week_ago = today() - timedelta(days=7)
    with on(today()):
        for number in (1, 2):
            tv_tracking.tick(
                friend,
                shown,
                1,
                number,
                at=week_ago,
                entry_point=tv_tracking.IMPORT,
                mutation_id=uuid4(),
            )


def friend_marks():
    """Tap an eye on a movie, a heart, a season eye and Mark watched up to here."""
    friend = friend_user()
    with on(today()):
        single_weight.mark_consumed(friend, friend_item(MediaTypes.MOVIE.value, "FM2"))
        MediaLike.objects.get_or_create(user=friend, item=book("FB5"))
        tv_tracking.mark_watched(friend, show("FS5"), season_number=1, mutation_id=uuid4())
        tv_tracking.mark_up_to(friend, show("FS6"), 1, 3, mutation_id=uuid4())


def friend_earlier_question():
    """Tick E3 of a new show, and say Yes to the earlier-episodes question."""
    friend, shown = friend_user(), show("FS4")
    with on(today() - timedelta(days=3)):
        question = tv_tracking.tick(friend, shown, 1, 3, mutation_id=uuid4()).earlier_unwatched
        tv_tracking.mark_earlier(friend, shown, from_ref=question["from"], to_ref=question["to"], mutation_id=uuid4())


def friend_private_season():
    """Two ticks, then a private log of the season adding the other three episodes."""
    friend = friend_user()
    day = date(2026, 4, 6)
    with on(day):
        for number in (1, 2):
            tv_tracking.tick(friend, show("FS2"), 1, number, mutation_id=uuid4())
    with on(day + timedelta(days=1)):
        tv_tracking.create_log(
            friend,
            season("FS2"),
            consumed_at=day + timedelta(days=1),
            visibility="private",
            mutation_id=uuid4(),
        )


def comment_on_my_row():
    """Comment on the dev user's newest row, as the friend."""
    dev = dev_user()
    client = APIClient()
    client.force_authenticate(friend_user())
    newest = client.get(f"/api/v1/users/{DEV_USERNAME}/activity/", {"page_size": 1}).json()["results"][0]
    if newest["type"] == "progress_updated":
        post = activity_service.progress_post(friend_user(), newest["object"]["id"])
        social_service.add_comment(friend_user(), "Nice one.", post=post)
    else:
        entry = DiaryEntry.objects.get(pk=newest["object"]["id"], user=dev)
        social_service.add_comment(friend_user(), "Nice one.", entry=entry)


# Phase 5's Mac steps: the website's Planning add, a watchlist import, and the friend's
# adds. Each prints what it did.


def add_two_movies():
    """Add W1 and W2 to Planning as the website's Save button does, for the dev user."""
    dev = dev_user()
    browser = Client()
    browser.force_login(dev)
    with override_settings(ALLOWED_HOSTS=["*"]):
        for name in PHASE_5_FILMS:
            film = title(MediaTypes.MOVIE.value, name)
            response = browser.post(
                "/media_save",
                {
                    "media_id": film.media_id,
                    "source": film.source,
                    "media_type": film.media_type,
                    "status": PLANNING,
                    "notes": "",
                },
            )
            if response.status_code >= 400:
                msg = f"The website refused {film.title}: {response.status_code}"
                raise RuntimeError(msg)
    print(f"Added {', '.join(PHASE_5_FILMS)} to {DEV_USERNAME}'s Planning.")  # noqa: T201


def letterboxd_watchlist():
    """Import a Letterboxd file of only a watchlist, and print how many it added."""
    films = {
        f"https://boxd.it/checkpoint-watch-{number}": (str(990000 + number), f"Watchlist Film {number}")
        for number in range(1, WATCHLIST_FILMS + 1)
    }
    rows = "".join(f"2026-01-0{number},{name},2001,{uri}\n" for number, (uri, (_, name)) in enumerate(films.items(), 1))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("watchlist.csv", f"Date,Name,Year,Letterboxd URI\n{rows}")

    class Resolver:
        def resolve_rows(self, _rows):
            return {uri: {"tmdb_id": tmdb_id, "confidence": "scrape"} for uri, (tmdb_id, _) in films.items()}

    def movie(tmdb_id):
        name = next(name for found, name in films.values() if found == str(tmdb_id))
        return {"title": name, "image": IMAGE}

    with (
        patch("integrations.imports.letterboxd.importer.tmdb.movie", side_effect=movie),
        patch("app.models.providers.services.get_media_metadata", return_value={"max_progress": 1}),
        patch("app.services.update_daily_statistics.delay"),
        patch("app.signals.update_daily_statistics.delay"),
    ):
        counts, _ = LetterboxdImporter(buffer.getvalue(), dev_user(), "new", resolver=Resolver()).import_data()
    print(f"The watchlist import added {counts['watchlist']} (Planning in your Library should rise by that).")  # noqa: T201
    return counts["watchlist"]


def friend_adds_three_movies():
    """Add three films to the friend's Planning, the way the app's request does."""
    friend = friend_user()
    client = APIClient()
    client.force_authenticate(friend)
    for name in PHASE_5_FRIEND_FILMS:
        film = title(MediaTypes.MOVIE.value, name)
        response = client.put(f"/api/v1/tracking/{film.source}/movie/{film.media_id}/", {"status": PLANNING}, format="json")
        if response.status_code >= 400:
            msg = f"The friend's add of {film.title} failed: {response.status_code}"
            raise RuntimeError(msg)


def like_p_row():
    """Like the dev user's book P row of Dec 20 2025, as the friend."""
    friend = friend_user()
    client = APIClient()
    client.force_authenticate(friend)
    page = client.get(f"/api/v1/users/{DEV_USERNAME}/activity/", {"page_size": 100}).json()
    row = next(
        row
        for row in page["results"]
        if row["type"] == "progress_updated"
        and row["media"]["title"] == "Book P"
        and row["consumed_at"] == "2025-12-20"
    )
    post = activity_service.progress_post(friend, row["object"]["id"])
    social_service.set_like(friend, target_type=ContentLike.PROGRESS_POST, target_id=post.pk, liked=True)


def print_rows():
    """Print every row the sheet's last row stored, and flag any not saved today."""
    names = [media_id(name) for name in STORED_TITLES]
    # The seed's own Planning choice (show Z) is setup, not what the phone stored.
    rows = (
        ProgressChange.objects.filter(actor=dev_user(), item__media_id__in=names)
        .exclude(current_progress__kind=ProgressChange.PLANNING)
        .select_related("item")
        .order_by("id")
    )
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
    "seed-phase-4": seed_phase_4,
    "add-n": add_n,
    "friend-day-order": friend_day_order,
    "friend-typo": friend_typo,
    "friend-typo-fixed": friend_typo_fixed,
    "friend-save-and-finish": friend_save_and_finish,
    "friend-pause-stop-pick-up": friend_pause_stop_pick_up,
    "friend-game-stops-then-plays": friend_game_stops_then_plays,
    "friend-ticks": friend_ticks,
    "friend-late-tick": friend_late_tick,
    "friend-import-last-week": friend_import_last_week,
    "friend-marks": friend_marks,
    "friend-earlier-question": friend_earlier_question,
    "friend-private-season": friend_private_season,
    "comment-on-my-row": comment_on_my_row,
    "seed-phase-5": seed_phase_5,
    "add-two-movies": add_two_movies,
    "letterboxd-watchlist": letterboxd_watchlist,
    "friend-adds-three-movies": friend_adds_three_movies,
    "like-p-row": like_p_row,
}
