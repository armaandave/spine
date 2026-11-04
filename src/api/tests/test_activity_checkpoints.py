"""Each phone sheet, step by step, so a wrong number fails here before Armaan sees it.

With the Stats cache on and each step run through commit, a write that misses a bump
leaves the old number. Everything is read through the real endpoints, as JSON, and
every number on a sheet is checked as a plain int, so a Decimal rendered as 360.0, which
the app reads as 0, fails here. So does one marked approximate, which the app prints
with ≈. Each step sends the request the phone sends. The seed is the phone test's own.
"""

import contextlib
import io
import os
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from api.services import stats_cache, stats_time
from api.services.activity_rows import (
    DAYS,
    MULTI_TITLE_DAYS,
    TITLES,
    list_rows,
    piled,
    rows_for,
)
from api.services.stats_v2_activity import longest_daily_streak_span
from api.tests import activity_checkpoint_seed as seed
from api.tests.test_stats_v2_shape import ALL_TIME, YEAR_2024, YEAR_2025
from app import book_tracking
from app.models import (
    TV,
    DiaryEntry,
    Episode,
    Item,
    MediaLike,
    MediaTypes,
    Movie,
    Season,
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

BOOK = MediaTypes.BOOK.value
NEW_YORK = "America/New_York"
TODAY = date(2026, 11, 3)
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=ZoneInfo(NEW_YORK))
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "activity-checkpoints"}}
YEAR_2026 = {"schema": "2", "start_date": "2026-01-01", "end_date": "2026-12-31"}


@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class CheckpointBase(TestCase):
    """The sheet's opening state, and the phone's requests."""

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        cache.clear()
        self.addCleanup(cache.clear)
        users = get_user_model().objects
        self.other = users.create_user(username="someone", password="strong-password-123")
        self.follower = users.create_user(username="a-follower", password="strong-password-123")
        setattr(connection, stats_cache.PENDING_ATTR, (None, None))
        with self.captureOnCommitCallbacks(execute=True):
            self.dirty_the_dev_user()
            seed.seed()
            Follow.objects.create(from_user=self.follower, to_user=seed.dev_user(), status=FollowStatus.ACCEPTED)
        self.dev = seed.dev_user()

    def dirty_the_dev_user(self):
        """Leave what a finished sheet leaves, in another year: none of it shows."""
        users = get_user_model().objects
        dev, _ = users.get_or_create(username=seed.DEV_USERNAME)
        friend, _ = users.get_or_create(username=seed.FRIEND_USERNAME)

        def stray(name, media_type="book", **fields):
            return Item.objects.create(source="manual", media_type=media_type, media_id=f"stray-{name}", title=name, image="x", **fields)

        reading, logged, marked = stray("reading", total_pages=99), stray("logged", total_pages=99), stray("marked", total_pages=99)
        film = stray("film", media_type=MediaTypes.MOVIE.value, runtime_minutes=90)
        day = date(2019, 8, 1)
        with seed.on(day):
            book_tracking.start(dev, reading)
            book_tracking.update_progress(dev, reading, progress_type="pages", value=40)
            book_tracking.complete(dev, logged, completion_date=day)
            book_tracking.mark_read(dev, marked)
            post = ProgressPost.objects.create(actor=dev, title_key=f"item:{reading.pk}", day=day)
            ContentLike.objects.create(user=friend, target_type=ContentLike.PROGRESS_POST, target_id=post.pk)
            Comment.objects.create(user=friend, progress_post=post, body="Nice")
            Movie.objects.bulk_create([Movie(user=dev, item=film, status="Completed")])
            DiaryEntry.objects.create(user=dev, item=film, consumed_at=datetime(2019, 8, 1, tzinfo=ZoneInfo("UTC")))
            MediaLike.objects.create(user=dev, item=film)
        CustomList.objects.create(owner=dev, name="Left over")
        Follow.objects.create(from_user=dev, to_user=friend)

    def phone(self, user=None, zone=NEW_YORK):
        client = APIClient()
        client.force_authenticate(user or self.dev)
        client.credentials(HTTP_X_SPINE_TIMEZONE=zone)
        return client

    def stats(self, params, *, tab=BOOK, user=None):
        path = "/api/v1/stats/me/summary/" if user is None else f"/api/v1/users/{seed.DEV_USERNAME}/stats/summary/"
        query = {"schema": "2", **params, **({"media_type": tab} if tab else {})}
        response = self.phone(user).get(path, query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def pages(self, params, user=None):
        """Return the number as the phone prints it: an int, pages, not approximate."""
        rows = self.stats(params, user=user)["time"]["rows"]
        row = next((row for row in rows if row["media_type"] == BOOK), None)
        if row is None:
            return 0
        self.assertIs(type(row["value"]), int, row)
        self.assertEqual(row["unit"], "pages", row)
        self.assertFalse(row["approximate"], row)
        return row["value"]

    def assert_header_numbers(self, params):
        """Check the header's numbers are plain ints: the app reads 5.0 as 0 too."""
        header = self.stats(params)["header"]
        for key in ("titles", "logs", "active_days", "longest_daily_streak"):
            self.assertIs(type(header[key]), int, (key, header[key]))

    def assert_sheet(self, in_2025, in_2026, all_time):
        self.assertEqual(
            (self.pages(YEAR_2025), self.pages(YEAR_2026), self.pages(ALL_TIME)),
            (in_2025, in_2026, all_time),
        )
        for params in (YEAR_2025, YEAR_2026, ALL_TIME):
            self.assert_header_numbers(params)
        self.assert_adds_up()

    def assert_adds_up(self):
        """Check the list adds up to the number, for three viewers (decision 2)."""
        for viewer in (self.dev, self.follower, self.other):
            calculation = rows_for([self.dev], viewer, types=[BOOK])
            for params, start, end in (
                (YEAR_2024, date(2024, 1, 1), date(2024, 12, 31)),
                (YEAR_2025, date(2025, 1, 1), date(2025, 12, 31)),
                (YEAR_2026, date(2026, 1, 1), date(2026, 12, 31)),
                (ALL_TIME, None, None),
            ):
                data = self.stats(params, user=None if viewer == self.dev else viewer)
                pile = piled(calculation.piles, BOOK) if end is None else piled([], BOOK)
                listed = list_rows(calculation.rows, media_type=BOOK, start_date=start, end_date=end)
                time_rows = [row["value"] for row in data["time"]["rows"] if row["media_type"] == BOOK]
                self.assertEqual(int(sum(row.amount for row in listed) + pile.amount), sum(time_rows))
                titles = list_rows(calculation.rows, media_type=BOOK, start_date=start, end_date=end, counts=TITLES)
                self.assertEqual(len(titles) + len(pile.titles - {row.title_id for row in titles}), data["header"]["titles"])
                days = sorted(
                    {row.day for row in list_rows(calculation.rows, media_type=BOOK, start_date=start, end_date=end, counts=DAYS)},
                )
                self.assertEqual(len(days), data["header"]["active_days"])
                self.assertEqual(longest_daily_streak_span(days).length, data["header"]["longest_daily_streak"])
                multi = list_rows(calculation.rows, media_type=BOOK, start_date=start, end_date=end, counts=MULTI_TITLE_DAYS)
                details = {row["key"]: row["value"] for row in data["header"]["details"]}
                self.assertEqual(len({row.day for row in multi}), details["days_with_two_plus_titles"])

    def step(self, request, *args, **kwargs):
        """Run one phone or Mac step today, through commit."""
        with seed.on(TODAY), self.captureOnCommitCallbacks(execute=True):
            return request(*args, **kwargs)

    def book_url(self, name, suffix=""):
        return f"/api/v1/tracking/manual/book/{seed.media_id(name)}/{suffix}"

    def update_progress(self, name, value, kind="pages"):
        response = self.phone().post(
            self.book_url(name, "progress/"),
            {"progress_type": kind, "value": value, "notes": ""},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def log(self, name, day=TODAY):
        """Send what MediaLogView's log sends: a journey id only for a live read."""
        book = self.phone().get(self.book_url(name)).json().get("book") or {}
        journey = book.get("current_journey") or {}
        body = {
            "completion_date": day.isoformat(),
            "review": "",
            "review_title": "",
            "liked": False,
            "is_rewatch": bool(book.get("is_rereading")),
            "contains_spoilers": False,
            "tags": [],
            "mutation_id": str(uuid4()),
        }
        if journey.get("status") in ("In progress", "Paused"):
            body["journey_id"] = journey["id"]
        response = self.phone().post(self.book_url(name, "complete/"), body, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["diary_entry"]["id"]

    def delete_log(self, entry_id):
        response = self.phone().delete(f"/api/v1/diary/{entry_id}/")
        self.assertEqual(response.status_code, 204, response.content)

    def currently_reading(self, name):
        """performQuickAction(.currently) off Paused: a PATCH, not an action."""
        response = self.phone().patch(
            self.book_url(name),
            {"status": "In progress", "start_date": TODAY.isoformat(), "mutation_id": str(uuid4())},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def action(self, name, action):
        """Send a book action as performBookAction does, with the dates it sends."""
        body = {"mutation_id": str(uuid4())}
        if action in ("resume", "restart"):
            body["start_date"] = TODAY.isoformat()
        if action in ("drop", "restart"):
            body["end_date"] = TODAY.isoformat()
        response = self.phone().post(self.book_url(name, f"actions/{action}/"), body, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def opens_at(self, name):
        response = self.phone().get(self.book_url(name))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["progress"]


class SeedTests(CheckpointBase):
    """What the seed promises."""

    def test_the_settings_a_sheet_needs(self):
        for field in ("tv", "season", "movie", "anime", "manga", "game", "book", "comic", "music"):
            self.assertTrue(getattr(self.dev, f"{field}_enabled"), field)
        self.assertEqual(self.dev.week_start_day, "monday")
        self.assertEqual(
            set(get_user_model().objects.exclude(username__in=["someone", "a-follower"]).values_list("calendar_timezone", flat=True)),
            {NEW_YORK},
        )

    def test_a_number_rendered_as_a_float_fails_here(self):
        real = stats_time._from_rows

        def floated(*args, **kwargs):
            result = real(*args, **kwargs)
            result.value = float(result.value)
            return result

        with patch.object(stats_time, "_from_rows", floated), self.assertRaises(AssertionError):
            self.pages(YEAR_2026)

    def test_a_number_marked_approximate_fails_here(self):
        real = stats_time._from_rows

        def approximate(*args, **kwargs):
            result = real(*args, **kwargs)
            result.approximate = True
            return result

        with patch.object(stats_time, "_from_rows", approximate), self.assertRaises(AssertionError):
            self.pages(YEAR_2026)

    def test_one_list_holds_a_manual_title_of_each_type(self):
        listed = CustomList.objects.get(owner=self.dev, name=seed.LIST_NAME)
        items = [row.item for row in CustomListItem.objects.filter(custom_list=listed).select_related("item")]
        self.assertEqual(
            {item.media_type for item in items},
            {"book", "game", "manga", "comic", "anime", "movie", "music", "tv"},
        )
        self.assertEqual({item.source for item in items}, {"manual"})

    def test_a_shows_runtimes_sit_on_its_episodes(self):
        episodes = seed.title(MediaTypes.TV.value, "Z")
        self.assertEqual(episodes.title, "Show Z")
        minutes = type(episodes).objects.filter(media_type="episode", media_id=episodes.media_id).values_list("runtime_minutes", flat=True)
        self.assertEqual(set(minutes), {seed.EPISODE_MINUTES})


class DirtySeedTests(CheckpointBase):
    """It runs twice over a dirtied dev user and opens the same."""

    def assert_it_opens_clean(self):
        self.assert_sheet(830, 360, 1540)
        for params, books, logs in ((YEAR_2026, 5, 0), (YEAR_2025, 5, 2), (ALL_TIME, 8, 2)):
            header = self.stats(params)["header"]
            self.assertEqual((header["titles"], header["logs"]), (books, logs), params)
        self.assertEqual(self.stats(ALL_TIME)["years"]["first"], 2025)
        self.assertEqual(self.stats(ALL_TIME, tab=None)["years"]["first"], 2025)
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(Comment.objects.count(), 0)
        self.assertEqual(ProgressPost.objects.count(), 0)
        self.assertFalse(MediaLike.objects.filter(user=self.dev).exists())
        self.assertEqual(list(CustomList.objects.filter(owner=self.dev).values_list("name", flat=True)), [seed.LIST_NAME])

    def test_the_dirt_is_gone_and_it_opens_the_same(self):
        self.assert_it_opens_clean()
        with self.captureOnCommitCallbacks(execute=True):
            seed.seed()
        self.assert_it_opens_clean()

    def test_the_whole_sheet_left_behind_is_gone_too(self):
        self.step(self.update_progress, "A", 250)
        self.step(self.log, "A")
        self.step(self.action, "J", "mark_read")
        change = next(row for row in self.phone().get("/api/v1/feed/").json()["results"] if row["type"] == "progress_updated")
        self.step(self.phone().post, f"/api/v1/progress/{change['object']['id']}/like/")
        self.step(self.phone().post, f"/api/v1/progress/{change['object']['id']}/comments/", {"body": "Nice"}, format="json")
        with self.captureOnCommitCallbacks(execute=True):
            seed.seed()
        self.assert_it_opens_clean()

    def test_a_stray_viewing_goes_too(self):
        season_item = seed.title(MediaTypes.SEASON.value, "Z")
        tv = TV.objects.get(user=self.dev, item=seed.show_z())
        season = Season(user=self.dev, item=season_item, related_tv=tv, status="In progress")
        Season.objects.bulk_create([season])
        episode = type(season_item).objects.get(media_type="episode", media_id=season_item.media_id, episode_number=1)
        Episode.objects.bulk_create([Episode(related_season=season, item=episode, watched_on=date(2026, 8, 1))])
        with self.captureOnCommitCallbacks(execute=True):
            seed.seed()
        self.assertFalse(Episode.objects.filter(related_season__user=self.dev).exists())
        self.assert_sheet(830, 360, 1540)


class BooksSheetTests(CheckpointBase):
    """Stats, Books, switching the year pill: every row, in order."""

    def test_the_opening(self):
        self.assert_sheet(830, 360, 1540)
        for params, books, logs in ((YEAR_2026, 5, 0), (YEAR_2025, 5, 2), (ALL_TIME, 8, 2)):
            header = self.stats(params)["header"]
            self.assertEqual((header["titles"], header["logs"]), (books, logs), params)

    def test_every_row_in_order(self):
        self.step(self.update_progress, "A", 250)
        self.assert_sheet(830, 410, 1590)
        a_log = self.step(self.log, "A")
        self.assert_sheet(830, 560, 1740)
        self.step(self.update_progress, "B", 60)
        self.assert_sheet(830, 590, 1770)
        self.step(self.update_progress, "D", 300)
        self.assert_sheet(830, 640, 1820)
        self.step(self.update_progress, "D", 260)
        self.assert_sheet(830, 600, 1780)
        self.step(self.delete_log, a_log)
        self.assert_sheet(830, 450, 1630)
        self.assertEqual(self.opens_at("A")["value"], 250)
        self.step(self.log, "H")
        self.assert_sheet(830, 650, 1830)
        self.step(seed.add_k_and_l)
        self.assert_sheet(830, 850, 2030)
        k_log = self.step(self.log, "K", date(2025, 12, 20))
        self.assert_sheet(1230, 750, 2330)
        self.step(self.delete_log, k_log)
        l_log = self.step(self.log, "L", date(2025, 12, 20))
        self.assert_sheet(1130, 850, 2330)
        self.step(self.delete_log, l_log)
        self.step(seed.add_s)
        self.assert_sheet(830, 970, 2150)
        self.step(self.currently_reading, "S")
        self.assertEqual(self.opens_at("S")["value"], 120)
        self.step(self.update_progress, "S", 150)
        self.assert_sheet(830, 1000, 2180)
        self.step(seed.add_u)
        self.step(self.currently_reading, "U")
        opened = self.opens_at("U")
        self.assertEqual((opened["kind"], float(opened["value"])), ("percentage", 25.0))
        self.assert_sheet(830, 1000, 2180)
        self.step(self.action, "J", "mark_read")
        self.assert_sheet(830, 1000, 2480)
        response = self.step(
            self.phone().post,
            "/api/v1/me/liked-media/",
            {"ref": {"source": "manual", "media_type": BOOK, "media_id": seed.media_id("V")}},
            format="json",
        )
        self.assertLess(response.status_code, 300, response.content)
        self.assert_sheet(830, 1000, 2780)

    def test_a_year_of_reading_with_no_log(self):
        before = self.pages(ALL_TIME)
        self.step(seed.add_r)
        data = self.stats(YEAR_2024)
        self.assertEqual((data["header"]["titles"], data["header"]["logs"]), (1, 0))
        self.assertEqual(self.pages(YEAR_2024), 285)
        self.assertEqual(data["years"]["first"], 2024)
        self.assertEqual(data["genres"]["rows"][0]["titles"], 1)
        names = [person["person"]["name"] for group in data["people"]["groups"] for person in group["rows"]]
        self.assertEqual(names, ["Checkpoint Author"])
        self.assertEqual(self.pages(ALL_TIME), before + 285)
        self.assert_adds_up()


class EveryRowCanBeReachedTests(CheckpointBase):
    """He opens a book from Home's In progress, the Library or the list, not search."""

    def reachable(self):
        listed = CustomList.objects.get(owner=self.dev, name=seed.LIST_NAME)
        on_list = set(CustomListItem.objects.filter(custom_list=listed).values_list("item__media_id", flat=True))
        library = self.phone().get("/api/v1/tracking/", {"media_type": BOOK, "page_size": 100})
        self.assertEqual(library.status_code, 200, library.content)
        return on_list | {row["media"]["ref"]["media_id"] for row in library.json()["results"]}

    def assert_reachable(self, *names):
        reachable = self.reachable()
        for name in names:
            self.assertIn(seed.media_id(name), reachable, name)

    def test_each_book_a_row_names_once_its_row_comes_up(self):
        self.assert_reachable("A", "B", "D", "H", "J", "V", "X")
        self.step(seed.add_k_and_l)
        self.assert_reachable("K", "L")
        self.step(seed.add_s)
        self.assert_reachable("S")
        self.step(seed.add_u)
        self.assert_reachable("U")


class LikesSheetTests(CheckpointBase):
    """The two like bugs, as the phone sees them."""

    def friend_row(self):
        response = self.phone().get("/api/v1/feed/")
        self.assertEqual(response.status_code, 200, response.content)
        return next(row for row in response.json()["results"] if row["type"] == "progress_updated")

    def test_two_likes_one_comment_one_date(self):
        row = self.friend_row()
        self.assertEqual((row["object"]["summary"], row["consumed_at"]), ("read 40 pages", "2026-11-02"))
        change = row["object"]["id"]
        self.step(self.phone().post, f"/api/v1/progress/{change}/like/")
        self.step(self.phone().post, f"/api/v1/progress/{change}/comments/", {"body": "Nice"}, format="json")
        self.step(seed.like_from_tokyo)
        row = self.friend_row()
        self.assertEqual((row["like_count"], row["comment_count"], row["consumed_at"]), (2, 1, "2026-11-02"))
        self.assertEqual(ProgressPost.objects.count(), 1)

    def test_reading_deleted_then_a_new_save_that_day_starts_at_nothing(self):
        change = self.friend_row()["object"]["id"]
        self.step(self.phone().post, f"/api/v1/progress/{change}/like/")
        self.step(self.phone().post, f"/api/v1/progress/{change}/comments/", {"body": "Nice"}, format="json")
        with self.captureOnCommitCallbacks(execute=True):
            seed.friend_starts_over()
        row = self.friend_row()
        self.assertEqual((row["object"]["summary"], row["like_count"], row["comment_count"]), ("read 10 pages", 0, 0))
        self.assertEqual(ContentLike.objects.count(), 0)

    def test_run_after_midnight_the_new_save_stays_on_the_friends_day(self):
        """A sheet lasts up to 12 hours: this row must read the day the like was on."""
        self.assertEqual(self.friend_row()["consumed_at"], "2026-11-02")
        after_midnight = datetime(2026, 11, 4, 0, 30, tzinfo=ZoneInfo(NEW_YORK))
        with patch("django.utils.timezone.now", return_value=after_midnight), self.captureOnCommitCallbacks(execute=True):
            seed.friend_starts_over()
        row = self.friend_row()
        self.assertEqual((row["object"]["summary"], row["consumed_at"]), ("read 10 pages", "2026-11-02"))


class TitlesOpenTests(CheckpointBase):
    """Later sheets open a manual title of each type."""

    def test_each_page_opens(self):
        listed = CustomList.objects.get(owner=self.dev, name=seed.LIST_NAME)
        for row in CustomListItem.objects.filter(custom_list=listed).select_related("item"):
            item = row.item
            response = self.phone().get(f"/api/v1/media/manual/{item.media_type}/{item.media_id}/")
            self.assertEqual(response.status_code, 200, (item.media_type, response.content))


class StoredRowsTests(CheckpointBase):
    """The sheet's last row: each stored row is today's, of the right kind and move."""

    def rows(self, item):
        stored = ProgressChange.objects.filter(actor=self.dev, item=item).order_by("id")
        for change in stored:
            self.assertEqual(change.saved_on, TODAY)
        return [
            (saved.get("kind"), saved.get("move"), bool(saved.get("began_over")))
            for saved in (change.current_progress for change in stored)
        ]

    def test_book_x(self):
        self.step(self.currently_reading, "X")
        self.step(self.update_progress, "X", 10)
        self.step(self.action, "X", "pause")
        self.step(self.action, "X", "resume")
        self.step(self.action, "X", "restart")
        self.assertEqual(
            self.rows(seed.book("X")),
            [
                ("start", None, False),
                ("pages", None, False),
                ("status", "paused", False),
                ("status", "picked back up", False),
                ("start", None, True),
            ],
        )

    def test_the_mac_prints_each_stored_row(self):
        self.step(self.currently_reading, "X")
        self.step(self.action, "X", "restart")
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.step(seed.print_rows)
        lines = printed.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(line.startswith("2026-11-03 Book X start") for line in lines), lines)
        self.assertIn("began_over", lines[1])

    def post(self, media_type, name, suffix, body=None):
        response = self.phone().post(f"/api/v1/tracking/manual/{media_type}/{seed.media_id(name)}/{suffix}", body or {}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_game_y(self):
        today = TODAY.isoformat()
        state = self.step(self.post, "game", "Y", "actions/start/", {"mutation_id": str(uuid4()), "start_date": today})
        playthrough = state["game"]["current_playthrough"]["id"]
        response = self.step(
            self.phone().patch,
            f"/api/v1/tracking/manual/game/{seed.media_id('Y')}/playthroughs/{playthrough}/",
            {"total_minutes": 120, "progressed_on": today},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.step(self.post, "game", "Y", "actions/drop/", {"mutation_id": str(uuid4()), "end_date": today})
        self.assertEqual(
        self.assertNotIn("NOT TODAY", printed.getvalue())

    def printed_rows(self):
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.step(seed.print_rows)
        return printed.getvalue().splitlines()

    def test_a_row_on_another_day_prints_and_says_so(self):
        self.step(self.currently_reading, "X")
        self.step(self.update_progress, "X", 10)
        saved = ProgressChange.objects.filter(item=seed.book("X"), current_progress__kind="pages")
        saved.update(saved_on=date(2026, 11, 2))
        lines = self.printed_rows()
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(lines[1].startswith("2026-11-02 Book X pages 10"), lines)
        self.assertIn("NOT TODAY", lines[1])
        self.assertNotIn("NOT TODAY", lines[0])

    def test_a_row_with_no_day_prints_and_says_so(self):
        self.step(self.currently_reading, "X")
        self.step(self.update_progress, "X", 10)
        ProgressChange.objects.filter(item=seed.book("X"), current_progress__kind="pages").update(saved_on=None)
        lines = self.printed_rows()
        self.assertTrue(lines[1].startswith("None Book X pages 10"), lines)
        self.assertIn("NOT TODAY", lines[1])

    def test_it_prints_only_the_titles_the_row_names(self):
        self.step(self.update_progress, "A", 250)
        self.step(self.currently_reading, "X")
        lines = self.printed_rows()
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("Book X", lines[0])
            self.rows(seed.title(MediaTypes.GAME.value, "Y")),
            [("start", None, False), ("minutes", None, False), ("status", "stopped", False)],
        )

    def test_show_z_ticked_and_unticked(self):
        show = seed.show_z()
        episode = {"season_number": 1, "episode_number": 1}
        self.step(self.post, "tv", "Z", "actions/tick/", {**episode, "mutation_id": str(uuid4())})
        self.assertEqual(self.rows(show), [("start", None, False)])
        listed = self.phone().get(f"/api/v1/media/manual/tv/{seed.media_id('Z')}/seasons/1/episodes/")
        self.assertEqual(listed.status_code, 200, listed.content)
        first = next(row for row in listed.json()["episodes"] if row["episode_number"] == 1)
        viewing = first["tv"]["untick_viewing_id"]
        self.step(self.post, "tv", "Z", "actions/untick/", {**episode, "viewing_id": viewing, "mutation_id": str(uuid4())})
        self.assertEqual(self.rows(show), [])

    def test_manga_w(self):
        today = TODAY.isoformat()
        self.step(self.post, "manga", "W", "actions/start/", {"mutation_id": str(uuid4()), "start_date": today})
        self.step(self.post, "manga", "W", "progress/", {"value": 5, "mutation_id": str(uuid4())})
        self.step(self.post, "manga", "W", "actions/pause/", {"mutation_id": str(uuid4())})
        self.step(self.post, "manga", "W", "actions/start/", {"mutation_id": str(uuid4()), "start_date": today})
        self.assertEqual(
            self.rows(seed.title(MediaTypes.MANGA.value, "W")),
            [
                ("start", None, False),
                ("count", None, False),
                ("status", "paused", False),
        """tickTVEpisode, then untickTVEpisode naming the viewing the episode's state names."""
                ("status", "picked back up", False),
            ],
        )


@override_settings(TIME_ZONE="UTC", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class BooksSheetOnUTCTests(BooksSheetTests):
    """The test backend's settings resolve to UTC, so the phone test runs there."""


@override_settings(TIME_ZONE="UTC", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class LikesSheetOnUTCTests(LikesSheetTests):
    """The same two like bugs on the test backend's zone."""


class SeedScriptTests(SimpleTestCase):
    """It clears the dev user, so it runs only in a feature worktree being served."""

    SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ios" / "seed-activity-checkpoint.sh"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(os.path.realpath(folder.name))
        self.main = self.root / "repo"
        self.main.mkdir()
        self.git(self.main, "init", "-q")
        self.git(self.main, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "first")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        # A stand-in for uv: the script asks it for the database's vendor, then seeds.
        self.write_script(self.bin / "uv", "echo sqlite\n")
        self.install(self.main)

    def git(self, where, *args):
        subprocess.run(["git", *args], cwd=where, check=True, capture_output=True)  # noqa: S603, S607

    def write_script(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"#!/bin/sh\n{body}")
        path.chmod(0o755)

    def install(self, checkout):
        (checkout / "src").mkdir(exist_ok=True)
        (checkout / "scripts" / "ios").mkdir(parents=True, exist_ok=True)
        shutil.copy(self.SCRIPT, checkout / "scripts" / "ios" / self.SCRIPT.name)

    def worktree(self, path):
        checkout = self.main / path
        self.git(self.main, "worktree", "add", "-q", "--detach", str(checkout))
        self.install(checkout)
        return checkout

    def run_it(self, checkout, *, served):
        self.write_script(checkout / "scripts" / "ios" / "test-backend.sh", f'echo "SERVED_CHECKOUT: {served}"\n')
        environment = {key: value for key, value in os.environ.items() if key != "DB_HOST"}
        environment["PATH"] = f"{self.bin}{os.pathsep}{environment['PATH']}"
        return subprocess.run(  # noqa: S603
            ["bash", str(checkout / "scripts" / "ios" / self.SCRIPT.name)],  # noqa: S607
            cwd=checkout,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_served_feature_worktree_runs_it(self):
        feature = self.worktree(".claude/worktrees/feature")
        result = self.run_it(feature, served=feature)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran seed", result.stdout)
        self.assertIn("never from search", result.stdout)

    def test_the_test_backends_own_checkout_is_refused_though_its_served(self):
        own = self.worktree(".claude/test-backend")
        result = self.run_it(own, served=own)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("test backend's own checkout", result.stderr)

    def test_the_main_checkout_is_refused(self):
        result = self.run_it(self.main, served=self.main)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("personal data", result.stderr)

    def test_a_feature_worktree_not_being_served_is_refused(self):
        feature = self.worktree(".claude/worktrees/feature")
        result = self.run_it(feature, served=self.main / ".claude" / "test-backend")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("is serving", result.stderr)
