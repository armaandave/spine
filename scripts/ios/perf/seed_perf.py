"""Perf harness data: a repeatable Home feed for the dev user, through real tracking writes.

Run by scripts/ios/perf-harness.sh seed (python manage.py shell). It clears the dev user
("claude") and the four perf friends, then writes:
  - the dev user's In progress rail: a book (written last, so first), a show, a game, a manga;
  - the friends' activity over the last 13 days: logs with ratings, hearts, reviews,
    spoilers and rewatches (movies, albums, books, games, episodes), progress days
    (pages, episodes, hours, chapters) and starts, plus a few likes.
  - enrich() (below): every card kind the Home redesign draws, made through the same
    tracking writes the app uses: a Planning day of 4 movies and other Planning adds, Start
    rows, status moves (Paused with progress, Stopped alone, Picked back up alone), a
    1,150-episode anime and a 40-episode show, a 10-episode season with a 3-episode day,
    finishing logs on the day of the matching progress, logs of every mark combination,
    album logs on square art, episode logs on landscape stills, Trending data (the dev
    user and friends logging the same titles), and more In progress titles for the dev user.
    Part of the media gets a poster accent color (light posters with light accents, dark
    with dark, two on purpose wrong), part none.
Everything is a manual title with a local poster URL (PERF_POSTER_BASE), so no network is
used. Dates are relative to today (New York), so the shape is the same every run.

SQLite only; refuses anything else (same guard as the checkpoint seed).
"""
import os
import random
import subprocess
import sys
import urllib.request
import zlib
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction

import events.tasks  # noqa: F401
from api.services import social as social_service
from api.services import stats_cache
from api.services import tracking as api_tracking
from api.tests.activity_checkpoint_seed import NEW_YORK, _clear_person, hours, on, today
from app import anime_tracking, book_tracking, game_tracking, series_tracking, single_weight, tv_tracking
from app.models import Anime, DiaryEntry, Item, MediaTypes, Sources
from app.utils.color import FALLBACK_COLOUR, get_poster_accent_from_url
from social.models import Comment, ContentLike, Follow, FollowStatus, ProgressPost

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


# The accent a title is given on purpose (True: always, False: never); None leaves it to
# accent_pass(), which gives about 60 percent of the rest one.
accent_wanted = {}


def item(media_type, name, art=None, accent=None, **fields):
    """Make a manual title. `art` is a full poster URL; without one the next p/s file is used."""
    created = Item.objects.create(
        source=Sources.MANUAL.value,
        media_type=media_type,
        media_id=f"perf-{name.lower().replace(' ', '-')}",
        title=name,
        image=art or poster(media_type == MediaTypes.MUSIC.value),
        **fields,
    )
    if accent is not None:
        accent_wanted[created.media_id] = accent
    return created


def make_show(name, episodes, art=None, accent=None, stills=None):
    """Make a one-season show. `stills` is a function of the episode number: an episode's image."""
    show = item(MediaTypes.TV.value, name, art=art, accent=accent)
    shared = {"source": Sources.MANUAL.value, "media_id": show.media_id, "image": show.image}
    Item.objects.create(media_type=MediaTypes.SEASON.value, season_number=1, title=name, **shared)
    for number in range(1, episodes + 1):
        Item.objects.create(
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=number,
            title=f"{name} E{number}",
            runtime_minutes=45,
            **{**shared, "image": stills(number) if stills else show.image},
        )
    return show


def season_of(show):
    return Item.objects.get(
        source=Sources.MANUAL.value,
        media_type=MediaTypes.SEASON.value,
        media_id=show.media_id,
        season_number=1,
    )


def episode(show, number):
    return Item.objects.get(
        source=Sources.MANUAL.value,
        media_type=MediaTypes.EPISODE.value,
        media_id=show.media_id,
        season_number=1,
        episode_number=number,
    )


# ---------------------------------------------------------------------------------------
# The Home redesign's data (enrich), written through the same tracking writes the app uses.
# ---------------------------------------------------------------------------------------

POSTER_DIR = Path(os.environ.get("PERF_POSTER_DIR", str(Path.home() / "Library" / "Caches" / "spine-perf-posters")))
# l light poster, d dark poster, m mid-tone poster, q square album art, e landscape still, a avatar.
ART_COUNTS = {"l": 8, "d": 8, "m": 4, "q": 8, "e": 6, "a": 5}
art_next = dict.fromkeys(ART_COUNTS, 0)
LONG_REVIEW = (
    "I did not expect to still be thinking about this three days later. The first hour is patient to the point of "
    "provocation: long takes, almost no music, people saying less than they mean. Then the film turns on a single "
    "line of dialogue and everything before it rearranges itself. The performances are quiet and exact, the sound "
    "design does half the storytelling, and the ending refuses the easy kindness a lesser film would have grabbed. "
    "I argued about it all the way home, which is the highest compliment I know how to give. Some of it is "
    "frustrating, and I would not blame anyone for walking out at the midpoint. I would also tell them they are "
    "wrong, and that the last twenty minutes are the reason this kind of cinema exists."
)
COLOR_CACHE = {}
catalog = []


def art(kind, n=None):
    """Return the next file of a family of the redesign's art (or file n of it)."""
    if n is None:
        n = art_next[kind]
        art_next[kind] += 1
    return f"{POSTER_BASE}/{kind}{n % ART_COUNTS[kind]:02d}.jpg"


def ensure_art():
    """Write the redesign's art when the poster server has none of it (a fresh machine)."""
    try:
        urllib.request.urlopen(f"{POSTER_BASE}/q07.jpg", timeout=5).read(1)
        return
    except OSError:
        pass
    script = Path(settings.BASE_DIR).parent / "scripts" / "ios" / "perf" / "make_posters.py"
    subprocess.run([sys.executable, str(script), str(POSTER_DIR), "40"], check=True)


def set_avatars(people):
    """Give each person a profile picture served from MEDIA_ROOT (what an upload does)."""
    folder = Path(settings.MEDIA_ROOT) / "profile_pictures"
    folder.mkdir(parents=True, exist_ok=True)
    for number, person in enumerate(people):
        name = f"profile_pictures/{person.username}.jpg"
        (Path(settings.MEDIA_ROOT) / name).write_bytes(urllib.request.urlopen(art("a", number), timeout=10).read())
        person.profile_picture = name
        person.save(update_fields=["profile_picture"])


def accent_pass():
    """Give part of the media a poster accent color, as opening a title's page does.

    The color is what the page computes (ColorThief on the poster, kept between luminance
    0.12 and 0.8), so light posters get light accents and dark posters dark ones. A title
    marked accent=True always gets one, accent=False never, a hex string that color, and
    the rest about 60 percent by a fixed hash, so the same titles are bare every run.
    Episodes get none (their page doesn't compute one either).
    """
    rows = Item.objects.filter(source=Sources.MANUAL.value, media_id__startswith="perf-")
    for row in rows.exclude(media_type=MediaTypes.EPISODE.value):
        want = accent_wanted.get(row.media_id)
        if want is None:
            want = zlib.crc32(row.media_id.encode()) % 5 < 3
        color = ""
        if isinstance(want, str):
            color = want
        elif want:
            if row.image not in COLOR_CACHE:
                COLOR_CACHE[row.image] = get_poster_accent_from_url(row.image)
            color = "" if COLOR_CACHE[row.image] == FALLBACK_COLOUR else COLOR_CACHE[row.image]
        if row.poster_accent_color != color:
            Item.objects.filter(pk=row.pk).update(poster_accent_color=color)


def note(kind, who, title, day, detail=""):
    catalog.append((kind, who.username, title, day.isoformat(), detail))


def add_to_planning(person, titles, day, hour, minute):
    """Add titles to Planning one minute apart, the way the app does (docs/activity-contract.md, AC-1)."""
    for number, title in enumerate(titles):
        with on(day, at=f"{hour}:{minute + number:02d}"):
            if title.media_type == MediaTypes.TV.value:
                tv_tracking.choose(person, title, "Planning", entry_point=tv_tracking.APP)
            else:
                api_tracking.create_or_update_tracking(
                    person,
                    source=Sources.MANUAL.value,
                    media_type=title.media_type,
                    media_id=title.media_id,
                    data={"status": "Planning"},
                )


def enrich(dev, friends, day0):  # noqa: C901, PLR0915
    ava, ben, cy, dee = friends
    first_entry = DiaryEntry.objects.order_by("-id").values_list("id", flat=True).first() or 0
    first_post = ProgressPost.objects.order_by("-id").values_list("id", flat=True).first() or 0
    movie, music, book, game = (MediaTypes.MOVIE.value, MediaTypes.MUSIC.value, MediaTypes.BOOK.value, MediaTypes.GAME.value)

    def d(back):
        return day0 - timedelta(days=back)

    def film(name, art_url, accent=None, runtime=110):
        return item(movie, name, art=art_url, accent=accent, runtime_minutes=runtime)

    def album(name, accent=None):
        return item(music, name, art=art("q"), accent=accent, runtime_minutes=44)

    def log(person, title, day, at, **marks):
        with on(day, at=at):
            single_weight.create_log(person, title, consumed_at=day, **{"is_rewatch": False, **marks})

    set_avatars([dev, *friends])

    # --- Planning (AC-8): a day of four movies, one show, mixed titles, games, an album, a book.
    plan_films = [
        film("Perf Plan Film 1", art("l"), accent=True),
        film("Perf Plan Film 2", art("d"), accent=True),
        film("Perf Plan Film 3", art("m")),
        film("Perf Plan Film 4", art("l"), accent=False),
    ]
    add_to_planning(cy, plan_films, d(0), 23, 50)
    note("Planning day, 4 movies", cy, "Perf Plan Film 1-4", d(0), "face: Plan Film 1 (light poster)")
    plan_show = make_show("Perf Plan Show", 6, art=art("d"), accent=True)
    add_to_planning(ava, [plan_show], d(0), 23, 20)
    note("Planning, 1 show", ava, "Perf Plan Show", d(0))
    mixed = [
        item(book, "Perf Plan Book", art=art("l"), accent=True, total_pages=340),
        item(game, "Perf Plan Game", art=art("d")),
        make_show("Perf Plan Show Two", 6, art=art("m")),
    ]
    add_to_planning(ben, mixed, d(1), 23, 40)
    note("Planning, 3 titles of 3 types", ben, "Perf Plan Book / Game / Show Two", d(1))
    plan_games = [item(game, f"Perf Plan Game {k + 2}", art=art("l" if k else "d")) for k in range(2)]
    add_to_planning(ava, plan_games, d(3), 20, 5)
    note("Planning, 2 games", ava, "Perf Plan Game 2-3", d(3))
    add_to_planning(dee, [item(music, "Perf Plan Album", art=art("q"), accent=True, runtime_minutes=42)], d(6), 18, 10)
    note("Planning, 1 album (square)", dee, "Perf Plan Album", d(6))
    add_to_planning(cy, [item(book, "Perf Plan Book Two", art=art("d"), total_pages=280)], d(5), 21, 0)
    note("Planning, 1 book", cy, "Perf Plan Book Two", d(5))

    # --- Start rows (a start alone), and a start with the day's first pages.
    fresh_book = item(book, "Perf Fresh Book", art=art("l"), accent=True, total_pages=320)
    with on(d(0), at="22:50"):
        book_tracking.start(dee, fresh_book)
    note("Start, book", dee, "Perf Fresh Book", d(0))
    fresh_game = item(game, "Perf Fresh Game", art=art("d"), accent=True)
    with on(d(0), at="22:45"):
        game_tracking.start(ben, fresh_game)
    note("Start, game", ben, "Perf Fresh Game", d(0))
    same_day = item(book, "Perf Same Day Book", art=art("m"), total_pages=260)
    with on(d(2), at="20:00"):
        book_tracking.start(ava, same_day)
    with on(d(2), at="20:40"):
        book_tracking.update_progress(ava, same_day, progress_type="pages", value=45)
    note("Start and 45 pages the same day, book", ava, "Perf Same Day Book", d(2))

    # --- Status moves.
    pause_book = item(book, "Perf Pause Book", art=art("d"), accent=True, total_pages=360)
    with on(d(7)):
        book_tracking.start(dee, pause_book)
    with on(d(5), at="21:00"):
        book_tracking.update_progress(dee, pause_book, progress_type="pages", value=80)
    with on(d(1), at="22:00"):
        book_tracking.update_progress(dee, pause_book, progress_type="pages", value=135)
    with on(d(1), at="22:05"):
        book_tracking.pause(dee, pause_book)
    note("Paused with 55 pages the same day, book", dee, "Perf Pause Book", d(1))
    stopped = item(game, "Perf Stopped Game", art=art("l"), accent=True)
    with on(d(8)):
        game_tracking.start(ben, stopped)
    with on(d(6), at="20:00"):
        game_tracking.update_progress(ben, stopped, total_minutes=hours(6), progressed_on=d(6))
    with on(d(3), at="20:00"):
        game_tracking.update_progress(ben, stopped, total_minutes=hours(14), progressed_on=d(3))
    with on(d(1), at="21:00"):
        game_tracking.drop(ben, stopped, end_date=d(1))
    note("Stopped alone, game", ben, "Perf Stopped Game", d(1))
    resumed = item(book, "Perf Resumed Book", art=art("m"), accent=True, total_pages=300)
    with on(d(9)):
        book_tracking.start(cy, resumed)
    with on(d(8), at="21:00"):
        book_tracking.update_progress(cy, resumed, progress_type="pages", value=70)
    with on(d(3), at="20:00"):
        book_tracking.pause(cy, resumed)
    with on(d(1), at="20:00"):
        book_tracking.resume(cy, resumed)
    note("Paused alone, book", cy, "Perf Resumed Book", d(3))
    note("Picked back up alone, book", cy, "Perf Resumed Book", d(1))

    # --- Long anime and shows. An anime or show over 30 episodes shows a 20-tick window.
    long_anime = item(MediaTypes.ANIME.value, "Perf Long Anime", art=art("d"), accent=True, runtime_minutes=24)
    with on(d(5), at="22:00"):
        anime_tracking.write_progress(dee, long_anime, 1065, mutation_id=uuid4())
    # A manual anime's length is the number its owner wrote (AN-100): 1,150, kept on the row.
    Anime.objects.filter(user=dee, item=long_anime).update(aired_count=1150)
    with on(d(0), at="23:45"):
        anime_tracking.write_progress(dee, long_anime, 1089, mutation_id=uuid4())
    note("Progress, long anime +24 episodes (1,089 of 1,150)", dee, "Perf Long Anime", d(0))
    short_anime = item(MediaTypes.ANIME.value, "Perf Short Anime", art=art("l"), runtime_minutes=24)
    with on(d(5), at="19:00"):
        anime_tracking.write_progress(ava, short_anime, 12, mutation_id=uuid4())
    Anime.objects.filter(user=ava, item=short_anime).update(aired_count=26)
    with on(d(1), at="19:30"):
        anime_tracking.write_progress(ava, short_anime, 14, mutation_id=uuid4())
    note("Progress, anime +2 episodes (14 of 26)", ava, "Perf Short Anime", d(1))
    season_show = make_show("Perf Season Show", 10, art=art("m"), accent=True)
    with on(d(3), at="21:00"):
        for number in range(1, 5):
            tv_tracking.tick(ben, season_show, 1, number, mutation_id=uuid4())
    with on(d(0), at="23:30"):
        for number in range(5, 8):
            tv_tracking.tick(ben, season_show, 1, number, mutation_id=uuid4())
    note("Progress, 10-episode season +3 episodes (7 of 10)", ben, "Perf Season Show", d(0))
    long_show = make_show("Perf Long Show", 40, art=art("l"), accent=True)
    with on(d(6), at="22:00"):
        for number in range(1, 31):
            tv_tracking.tick(cy, long_show, 1, number, mutation_id=uuid4())
    with on(d(2), at="20:00"):
        for number in range(31, 34):
            tv_tracking.tick(cy, long_show, 1, number, mutation_id=uuid4())
    note("Progress, 40-episode show +3 episodes (33 of 40)", cy, "Perf Long Show", d(2))

    # --- A finished book and a finished season, each with a log on the day of its last progress.
    finishing = item(book, "Perf Finishing Book", art=art("d"), accent=True, total_pages=320)
    with on(d(6)):
        book_tracking.start(ava, finishing)
    with on(d(4), at="21:00"):
        book_tracking.update_progress(ava, finishing, progress_type="pages", value=120)
    with on(d(2), at="21:00"):
        book_tracking.update_progress(ava, finishing, progress_type="pages", value=250)
    with on(d(1), at="23:20"):
        book_tracking.update_progress(ava, finishing, progress_type="pages", value=320)
    with on(d(1), at="23:30"):
        book_tracking.complete(
            ava,
            finishing,
            completion_date=d(1),
            rating=9,
            liked=True,
            is_rewatch=False,
            review="Held its breath all the way to the last page.",
        )
    note("Progress +70 pages, then its finishing log (separate cards)", ava, "Perf Finishing Book", d(1))
    finale = make_show("Perf Finale Show", 10, art=art("l"), accent=True)
    with on(d(4), at="21:00"):
        for number in range(1, 6):
            tv_tracking.tick(ben, finale, 1, number, mutation_id=uuid4())
    with on(d(1), at="22:20"):
        for number in range(6, 11):
            tv_tracking.tick(ben, finale, 1, number, mutation_id=uuid4())
    with on(d(1), at="22:30"):
        tv_tracking.create_log(
            ben,
            season_of(finale),
            consumed_at=d(1),
            rating=9,
            liked=True,
            is_rewatch=False,
            review="A finale that earned every minute.",
            mutation_id=uuid4(),
        )
    note("Progress +5 episodes, then its season log (separate cards)", ben, "Perf Finale Show", d(1))

    # --- Logs: every mark combination, on movies.
    long_review_film = film("Perf Long Review Film", art("l"), accent=True, runtime=142)
    log(dee, long_review_film, d(0), "23:00", rating=9, liked=True, review=LONG_REVIEW,
        review_title="Worth every minute", contains_spoilers=True)
    note("Log: stars + heart, long review, review title, spoilers chip", dee, "Perf Long Review Film", d(0))
    log(cy, film("Perf Title Only Film", art("d"), accent=True), d(0), "22:55", rating=7,
        review_title="Sharper than it looks")
    note("Log: stars, review title only", cy, "Perf Title Only Film", d(0))
    log(ben, film("Perf Heart Only Film", art("m")), d(1), "20:00", liked=True)
    note("Log: heart only", ben, "Perf Heart Only Film", d(1))
    log(cy, film("Perf Rewatch Only Film", art("l"), accent=True), d(1), "19:45", is_rewatch=True)
    note("Log: rewatch glyph only", cy, "Perf Rewatch Only Film", d(1))
    log(dee, film("Perf No Marks Film", art("d")), d(2), "21:00")
    note("Log: no marks at all (no headline row)", dee, "Perf No Marks Film", d(2))
    log(ben, film("Perf Spoiler Flag Film", art("l")), d(2), "20:30", rating=5, contains_spoilers=True)
    note("Log: stars, spoiler flag with no text (no chip)", ben, "Perf Spoiler Flag Film", d(2))
    log(ava, film("Perf Stars Only Film", art("m"), accent=True), d(3), "21:15", rating=3)
    note("Log: stars only (1.5)", ava, "Perf Stars Only Film", d(3))
    log(cy, film("Perf All Marks Film", art("d"), accent=True), d(3), "22:10", rating=8, liked=True, is_rewatch=True,
        review="Better the second time, and I did not think that was possible.")
    note("Log: stars + heart + rewatch + short review", cy, "Perf All Marks Film", d(3))
    # The ink's first guess can be wrong: a dark poster with a light accent, and the reverse.
    log(dee, film("Perf Dark Poster Light Accent", art("d"), accent="#E9E1CC"), d(1), "21:30", rating=6)
    log(dee, film("Perf Light Poster Dark Accent", art("l"), accent="#2B3050"), d(1), "21:35", rating=6)
    note("Log: dark poster, light accent (guess wrong)", dee, "Perf Dark Poster Light Accent", d(1))
    note("Log: light poster, dark accent (guess wrong)", dee, "Perf Light Poster Dark Accent", d(1))

    # --- Album logs on square art.
    log(ava, album("Perf Album No Marks"), d(0), "23:10")
    note("Log: album, no marks, square art", ava, "Perf Album No Marks", d(0))
    log(cy, album("Perf Album Heart", True), d(2), "22:00", rating=9, liked=True)
    note("Log: album, stars + heart, square art", cy, "Perf Album Heart", d(2))
    log(dee, album("Perf Album Long Review", True), d(2), "21:30", rating=7, review=LONG_REVIEW)
    note("Log: album, stars, long review, square art", dee, "Perf Album Long Review", d(2))
    log(ava, album("Perf Album Rewatch"), d(2), "20:00", rating=8, is_rewatch=True)
    note("Log: album, stars + rewatch, square art", ava, "Perf Album Rewatch", d(2))

    # --- Episode and season logs. Episode art is a landscape still.
    still_show = make_show("Perf Still Show", 8, art=art("d"), accent=True, stills=lambda number: art("e", number))
    still_episode = {
        number: Item.objects.get(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            media_id=still_show.media_id,
            season_number=1,
            episode_number=number,
        )
        for number in (2, 4, 5)
    }
    with on(d(1), at="19:30"):
        tv_tracking.create_log(ava, still_episode[4], consumed_at=d(1), is_rewatch=True, mutation_id=uuid4())
    note("Log: episode, rewatch only, landscape still", ava, "Perf Still Show E4", d(1))
    with on(d(2), at="19:00"):
        tv_tracking.create_log(ben, still_episode[5], consumed_at=d(2), review=LONG_REVIEW, mutation_id=uuid4())
    note("Log: episode, long review, landscape still", ben, "Perf Still Show E5", d(2))
    with on(d(3), at="20:30"):
        tv_tracking.create_log(cy, still_episode[2], consumed_at=d(3), review="Short and sharp.", mutation_id=uuid4())
    note("Log: episode, short review", cy, "Perf Still Show E2", d(3))
    season_log = make_show("Perf Season Log Show", 6, art=art("l"), accent=True)
    with on(d(3), at="21:30"):
        tv_tracking.create_log(
            dee,
            season_of(season_log),
            consumed_at=d(3),
            review="The whole season in one weekend. No notes.",
            is_rewatch=False,
            mutation_id=uuid4(),
        )
    note("Log: season, review, no stars", dee, "Perf Season Log Show", d(3))

    # --- A finished book and game without progress rows of their own.
    rated_book = item(book, "Perf Rated Book", art=art("m"), accent=True, total_pages=240)
    with on(d(2), at="23:00"):
        book_tracking.complete(ava, rated_book, completion_date=d(2), rating=7, is_rewatch=False)
    note("Log: finished book, stars only", ava, "Perf Rated Book", d(2))
    logged_game = item(game, "Perf Logged Game", art=art("d"))
    with on(d(2), at="22:30"):
        game_tracking.complete(dee, logged_game, completion_date=d(2), total_minutes=hours(20), rating=8)
    note("Log: finished game, stars only", dee, "Perf Logged Game", d(2))

    # --- Trending: titles the dev user and the people he follows logged in the last week.
    trending = [
        ("Perf Trend Film 1", art("l"), [(dev, 1, 9, True), (ava, 0, 8, False), (ben, 2, 10, False), (cy, 3, 7, False)]),
        ("Perf Trend Film 2", art("d"), [(dev, 2, 6, False), (dee, 1, 8, False), (ben, 1, 7, False)]),
        ("Perf Trend Film 3", art("m"), [(dev, 0, None, True), (cy, 0, 5, False)]),
        ("Perf Trend Film 4", art("l"), [(ava, 2, 9, False), (dee, 4, 8, False)]),
        ("Perf Trend Film 5", art("d"), [(dee, 0, 8, False)]),
    ]
    for name, art_url, logs in trending:
        title = film(name, art_url, accent=True)
        for who, back, rating, liked in logs:
            log(who, title, d(back), f"{17 + len(who.username) % 3}:{10 + back * 7:02d}", rating=rating, liked=liked)
        note("Trending title", dev, name, d(0), f"{len(logs)} people")
    trend_album = album("Perf Trend Album", True)
    for who, back, rating, liked in ((dev, 3, 9, False), (ben, 1, 8, True), (cy, 0, 7, False)):
        log(who, trend_album, d(back), "18:20", rating=rating, liked=liked)
    note("Trending title (album)", dev, "Perf Trend Album", d(0), "3 people")
    trend_show = make_show("Perf Trend Show", 8, art=art("d"), accent=True)
    for who, back, numbers in ((dev, 1, (1, 2)), (ava, 0, (1, 2)), (ben, 2, (1,))):
        with on(d(back), at="18:40"):
            for number in numbers:
                tv_tracking.tick(who, trend_show, 1, number, mutation_id=uuid4())
    note("Trending title (show ticks)", dev, "Perf Trend Show", d(0), "3 people")

    # --- More In progress titles for the dev user; the original four stay as they were.
    my_anime = item(MediaTypes.ANIME.value, "Perf My Anime", art=art("l"), accent=True, runtime_minutes=24)
    with on(d(6), at="22:00"):
        anime_tracking.write_progress(dev, my_anime, 1030, mutation_id=uuid4())
    Anime.objects.filter(user=dev, item=my_anime).update(aired_count=1150)
    with on(d(2), at="08:00"):
        anime_tracking.write_progress(dev, my_anime, 1088, mutation_id=uuid4())
    percent_game = item(game, "Perf My Percent Game", art=art("d"), accent=True)
    with on(d(9)):
        game_tracking.start(dev, percent_game)
    with on(d(4), at="20:00"):
        game_tracking.update_progress(dev, percent_game, percentage=37.5, progressed_on=d(4))
    comic = item(MediaTypes.COMIC.value, "Perf My Comic", art=art("m"), accent=True, series_total=40)
    with on(d(7)):
        series_tracking.start(dev, comic)
    with on(d(5), at="19:00"):
        series_tracking.update_progress(dev, comic, value=12)
    note("In progress: anime 1,088 of 1,150", dev, "Perf My Anime", d(2))
    note("In progress: game in percent", dev, "Perf My Percent Game", d(4))
    note("In progress: comic 12 of 40", dev, "Perf My Comic", d(5))

    # --- Likes and comments on what enrich wrote, so cards show counts.
    rng = random.Random(23)
    people = [dev, *friends]
    entries = list(DiaryEntry.objects.filter(id__gt=first_entry).order_by("id"))
    for entry in entries:
        for liker in people:
            if liker.pk != entry.user_id and rng.random() < 0.45:
                social_service.set_like(liker, target_type=ContentLike.DIARY_ENTRY, target_id=entry.pk, liked=True)
    for entry in entries[::4]:
        commenter = next(person for person in people if person.pk != entry.user_id)
        first = social_service.add_comment(commenter, "Adding this to my list.", entry=entry)
        if entry.pk % 2:
            social_service.add_comment(entry.user, "Do. It earns the runtime.", entry=entry, parent_id=first.pk)
    by_id = {person.pk: index for index, person in enumerate(people)}
    for post in ProgressPost.objects.filter(id__gt=first_post, actor__in=friends).order_by("id")[::2]:
        liker = people[(by_id[post.actor_id] + 1) % len(people)]
        social_service.set_like(liker, target_type=ContentLike.PROGRESS_POST, target_id=post.pk, liked=True)

    print(f"enrich: {len(entries)} logs, {Comment.objects.count()} comments, {len(catalog)} catalogued kinds")
    for row in catalog:
        print("  CATALOG | " + " | ".join(str(part) for part in row if part != ""))


def run():
    if connection.vendor != "sqlite" and not settings.TESTING:
        raise RuntimeError("The perf seed runs only on a local SQLite database.")
    ensure_art()
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

        # The Home redesign's cards (enrich above), before the rail so the book stays the last write.
        enrich(dev, friends, day0)

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
        accent_pass()
        for person in (dev, *friends):
            stats_cache.bump_user(person)
    print(f"perf seed done: {DiaryEntry.objects.count()} logs, {ProgressPost.objects.count()} progress posts")


run()
