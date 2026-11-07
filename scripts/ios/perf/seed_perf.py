"""Perf harness data: a repeatable Home feed for the dev user, through real tracking writes.

Run by scripts/ios/perf-harness.sh seed (python manage.py shell). It clears the dev user
("claude") and the four perf friends, then writes:
  - the dev user's In progress rail: a book (written last, so first), a show, a game, a manga;
  - the friends' activity over the last 13 days: logs with ratings, hearts, reviews,
    spoilers and rewatches (movies, albums, books, games, episodes), progress days
    (pages, episodes, hours, chapters) and starts, plus a few likes.
Everything is a manual title with a local poster URL (PERF_POSTER_BASE), so no network is
used. Dates are relative to today (New York), so the shape is the same every run.

SQLite only; refuses anything else (same guard as the checkpoint seed).
"""
import os
import random
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction

import events.tasks  # noqa: F401
from api.services import social as social_service
from api.services import stats_cache
from api.tests.activity_checkpoint_seed import NEW_YORK, _clear_person, hours, on, today
from app import book_tracking, game_tracking, series_tracking, single_weight, tv_tracking
from app.models import DiaryEntry, Item, MediaTypes, Sources
from social.models import ContentLike, Follow, FollowStatus, ProgressPost

POSTER_BASE = os.environ.get("PERF_POSTER_BASE", "http://127.0.0.1:8093")
DEV = "claude"
FRIENDS = ["perf-ava", "perf-ben", "perf-cy", "perf-dee"]
REVIEWS = [
    "Quietly devastating, and the last twenty minutes are the best thing I have seen all year.",
    "Overlong but gorgeous. I would watch the first act again tonight.",
    "Not for everyone. I loved it anyway.",
    "A comfort rewatch. Still holds up.",
    "The middle sags, the ending lands, and the score does half the work.",
    "Short and sharp.",
]
poster_n = [0]


def poster(square=False):
    n = poster_n[0] % 40
    poster_n[0] += 1
    return f"{POSTER_BASE}/{'s' if square else 'p'}{n:02d}.jpg"


def item(media_type, name, **fields):
    return Item.objects.create(
        source=Sources.MANUAL.value,
        media_type=media_type,
        media_id=f"perf-{name.lower().replace(' ', '-')}",
        title=name,
        image=poster(media_type == MediaTypes.MUSIC.value),
        **fields,
    )


def make_show(name, episodes):
    show = item(MediaTypes.TV.value, name)
    shared = {"source": Sources.MANUAL.value, "media_id": show.media_id, "image": show.image}
    Item.objects.create(media_type=MediaTypes.SEASON.value, season_number=1, title=name, **shared)
    for number in range(1, episodes + 1):
        Item.objects.create(
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=number,
            title=f"{name} E{number}",
            runtime_minutes=45,
            **shared,
        )
    return show


def episode(show, number):
    return Item.objects.get(
        source=Sources.MANUAL.value,
        media_type=MediaTypes.EPISODE.value,
        media_id=show.media_id,
        season_number=1,
        episode_number=number,
    )


def run():
    if connection.vendor != "sqlite" and not settings.TESTING:
        raise RuntimeError("The perf seed runs only on a local SQLite database.")
    rng = random.Random(7)
    users = get_user_model()
    with transaction.atomic():
        Item.objects.filter(source=Sources.MANUAL.value, media_id__startswith="perf-").delete()
        users.objects.update(calendar_timezone=NEW_YORK)
        dev, _ = users.objects.get_or_create(username=DEV)
        dev.calendar_timezone = NEW_YORK
        for field in dev._meta.fields:
            if field.name.endswith("_enabled") and field.name.split("_")[0] in MediaTypes.values:
                setattr(dev, field.name, True)
        dev.save()
        friends = [users.objects.get_or_create(username=name)[0] for name in FRIENDS]
        for person in (dev, *friends):
            person.calendar_timezone = NEW_YORK
            person.save(update_fields=["calendar_timezone"])
            _clear_person(person)
        for friend in friends:
            Follow.objects.create(from_user=dev, to_user=friend, status=FollowStatus.ACCEPTED)
        day0 = today()

        def d(back):
            return day0 - timedelta(days=back)

        def friend(n):
            return friends[n % len(friends)]

        # Movies: logs with half-star ratings, hearts, reviews, spoilers, rewatches.
        for k in range(12):
            film = item(MediaTypes.MOVIE.value, f"Perf Film {k + 1}", runtime_minutes=rng.choice([95, 110, 128, 142]))
            day = d(k // 2)
            with on(day, at=f"{18 + k % 5}:{10 + k}"):
                single_weight.create_log(
                    friend(k),
                    film,
                    consumed_at=day,
                    rating=rng.randint(2, 10),
                    review=REVIEWS[k % len(REVIEWS)] if k % 3 != 2 else "",
                    liked=k % 2 == 0,
                    is_rewatch=k % 5 == 4,
                    contains_spoilers=k % 6 == 1,
                )
        # Albums.
        for k in range(4):
            album = item(MediaTypes.MUSIC.value, f"Perf Album {k + 1}", runtime_minutes=45)
            day = d(1 + k)
            with on(day, at="21:00"):
                single_weight.create_log(
                    friend(k + 1),
                    album,
                    consumed_at=day,
                    rating=rng.randint(4, 10),
                    review=REVIEWS[k] if k % 2 == 0 else "",
                    liked=k % 2 == 1,
                )
        # Books: three in progress over several days, three finished with reviews.
        for k in range(3):
            novel = item(MediaTypes.BOOK.value, f"Perf Novel {k + 1}", total_pages=320)
            who = friend(k)
            with on(d(9 - k)):
                book_tracking.start(who, novel)
            pages = 0
            for back in (7, 4, 2, k % 2):
                pages += rng.randint(30, 80)
                with on(d(back), at="22:00"):
                    book_tracking.update_progress(who, novel, progress_type="pages", value=pages)
        for k in range(3):
            novel = item(MediaTypes.BOOK.value, f"Perf Finished {k + 1}", total_pages=280)
            day = d(2 + k * 2)
            with on(day, at="23:00"):
                book_tracking.complete(
                    friend(k + 2),
                    novel,
                    completion_date=day,
                    rating=rng.randint(5, 10),
                    liked=k == 0,
                    review=REVIEWS[k + 2],
                )
        # Games: two in progress, two finished.
        for k in range(2):
            game = item(MediaTypes.GAME.value, f"Perf Game {k + 1}")
            who = friend(k + 1)
            with on(d(10)):
                game_tracking.start(who, game)
            for back, h in ((6, 8), (3, 15), (1, 21 + k)):
                with on(d(back), at="20:00"):
                    game_tracking.update_progress(who, game, total_minutes=hours(h), progressed_on=d(back))
        for k in range(2):
            game = item(MediaTypes.GAME.value, f"Perf Finished Game {k + 1}")
            day = d(3 + k)
            with on(day, at="20:30"):
                game_tracking.complete(
                    friend(k),
                    game,
                    completion_date=day,
                    total_minutes=hours(30 + k),
                    rating=rng.randint(6, 10),
                    review=REVIEWS[k + 1],
                )
        # Shows: ticks on several days, plus two episode logs with reviews.
        for k in range(5):
            total = 12 if k % 2 == 0 else 8
            show = make_show(f"Perf Show {k + 1}", total)
            who = friend(k)
            number = 0
            for back in (6, 3, 1, 0):
                for _ in range(rng.randint(1, 3)):
                    if number >= total - 1:
                        break
                    number += 1
                    with on(d(back), at="22:30"):
                        tv_tracking.tick(who, show, 1, number, mutation_id=uuid4())
        for k in range(2):
            show = make_show(f"Perf Series {k + 1}", 8)
            with on(d(k), at="21:30"):
                tv_tracking.create_log(
                    friend(k + 1),
                    episode(show, 3 + k),
                    consumed_at=d(k),
                    review=REVIEWS[k],
                    is_rewatch=False,
                    mutation_id=uuid4(),
                )
        # Manga chapters.
        for k in range(2):
            manga = item(MediaTypes.MANGA.value, f"Perf Manga {k + 1}", series_total=120)
            who = friend(k)
            with on(d(8)):
                series_tracking.start(who, manga)
            chapter = 0
            for back in (5, 2, 0):
                chapter += rng.randint(3, 12)
                with on(d(back), at="19:00"):
                    series_tracking.update_progress(who, manga, value=chapter)

        # A few likes from other friends.
        for e in list(DiaryEntry.objects.filter(user__in=friends).order_by("id")[::2]):
            for liker in friends:
                if liker.pk != e.user_id and rng.random() < 0.6:
                    social_service.set_like(liker, target_type=ContentLike.DIARY_ENTRY, target_id=e.pk, liked=True)
        for post in list(ProgressPost.objects.filter(actor__in=friends).order_by("id")[::3]):
            liker = friends[(post.actor_id + 1) % len(friends)]
            if liker.pk != post.actor_id:
                social_service.set_like(liker, target_type=ContentLike.PROGRESS_POST, target_id=post.pk, liked=True)

        # The dev user's In progress rail. The last write comes first: the book.
        manga = item(MediaTypes.MANGA.value, "Perf My Manga", series_total=90)
        with on(d(9)):
            series_tracking.start(dev, manga)
        with on(d(2)):
            series_tracking.update_progress(dev, manga, value=41)
        game = item(MediaTypes.GAME.value, "Perf My Game")
        with on(d(8)):
            game_tracking.start(dev, game)
        with on(d(3), at="20:00"):
            game_tracking.update_progress(dev, game, total_minutes=hours(14), progressed_on=d(3))
        show = make_show("Perf My Show", 10)
        for number in (1, 2, 3, 4):
            with on(d(1), at="21:00"):
                tv_tracking.tick(dev, show, 1, number, mutation_id=uuid4())
        mine = item(MediaTypes.BOOK.value, "Perf My Book", total_pages=400)
        with on(d(5)):
            book_tracking.start(dev, mine)
        with on(day0, at="09:00"):
            book_tracking.update_progress(dev, mine, progress_type="pages", value=212)
        for person in (dev, *friends):
            stats_cache.bump_user(person)
    print(f"perf seed done: {DiaryEntry.objects.count()} logs, {ProgressPost.objects.count()} progress posts")


run()
