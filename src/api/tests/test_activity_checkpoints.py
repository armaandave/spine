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
from types import SimpleNamespace
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
            self.open_the_sheet()
            Follow.objects.create(from_user=self.follower, to_user=seed.dev_user(), status=FollowStatus.ACCEPTED)
        self.dev = seed.dev_user()

    def open_the_sheet(self):
        """Write the sheet's opening; Phase 4's sheet opens on its own seed."""
        seed.seed()

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
        """Run one phone or Mac step today, through commit, a minute after the last.

        Each request is its own moment, as on the phone: a log saved after an eye
        is after it (decision 38).
        """
        self.steps = getattr(self, "steps", 0) + 1
        hour, minute = divmod(self.steps, 60)
        with seed.on(TODAY, at=f"{12 + hour}:{minute:02d}"), self.captureOnCommitCallbacks(execute=True):
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

    def test_the_list_opens_on_j_v_x_in_that_order(self):
        """Their tiles look alike, so the script says the order, and it has to hold."""
        listed = CustomList.objects.get(owner=self.dev, name=seed.LIST_NAME)
        response = self.phone().get(f"/api/v1/lists/{listed.pk}/items/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([row["title"] for row in response.json()["results"][:3]], ["Book J", "Book V", "Book X"])

    def test_it_refuses_a_database_that_isnt_sqlite(self):
        """It would make a claude user and rewrite every account's timezone."""
        before = set(get_user_model().objects.values_list("pk", "calendar_timezone"))
        with (
            patch.object(seed, "connection", SimpleNamespace(vendor="postgresql")),
            override_settings(TESTING=False),
            self.assertRaises(RuntimeError),
        ):
            seed.seed()
        self.assertEqual(set(get_user_model().objects.values_list("pk", "calendar_timezone")), before)

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
        self.assertFalse(Episode.objects.filter(related_season__user=self.dev, item=episode).exists())
        # Only Phase 3's opening is left: T1's season, logged May 1 2025.
        left = Episode.objects.filter(related_season__user=self.dev).values_list("item__media_id", flat=True)
        self.assertEqual(set(left), {seed.media_id("T1")})
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
            self.rows(seed.title(MediaTypes.GAME.value, "Y")),
            [("start", None, False), ("minutes", None, False), ("status", "stopped", False)],
        )

    def test_show_z_ticked_and_unticked(self):
        """tickTVEpisode, then untickTVEpisode with the viewing the episode names."""
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
                ("status", "picked back up", False),
            ],
        )


YEAR_2023 = {"schema": "2", "start_date": "2023-01-01", "end_date": "2023-12-31"}
MANGA = MediaTypes.MANGA.value
COMIC = MediaTypes.COMIC.value
GAME = MediaTypes.GAME.value
ANIME = MediaTypes.ANIME.value


def h(hours):
    """Return hours in minutes."""
    return hours * 60


class Phase2SheetTests(CheckpointBase):
    """Phase 2's sheet: games, anime, manga, comics and the note, row by row."""

    def measure(self, params, tab, *, tab_only=True):
        """Return a type's Time row as the phone reads it, or None: plain ints, no ≈."""
        rows = self.stats(params, tab=tab if tab_only else None)["time"]["rows"]
        row = next((row for row in rows if row["media_type"] == tab), None)
        if row is not None:
            for key in ("value", "known", "total"):
                self.assertIs(type(row[key]), int, row)
            self.assertFalse(row["approximate"], row)
        return row

    def value(self, params, tab):
        row = self.measure(params, tab)
        return row["value"] if row else None

    def assert_type(self, tab, in_2025, in_2026, all_time):
        self.assertEqual(
            (self.value(YEAR_2025, tab), self.value(YEAR_2026, tab), self.value(ALL_TIME, tab)),
            (in_2025, in_2026, all_time),
        )
        for params in (YEAR_2025, YEAR_2026, ALL_TIME):
            self.assert_header_numbers(params)
        self.assert_type_adds_up(tab)

    def assert_type_adds_up(self, tab):
        """Check this type's list adds up to its number, for three viewers."""
        for viewer in (self.dev, self.follower, self.other):
            calculation = rows_for([self.dev], viewer, types=[tab])
            for params, start, end in (
                (YEAR_2025, date(2025, 1, 1), date(2025, 12, 31)),
                (YEAR_2026, date(2026, 1, 1), date(2026, 12, 31)),
                (ALL_TIME, None, None),
            ):
                data = self.stats(params, tab=tab, user=None if viewer == self.dev else viewer)
                pile = piled(calculation.piles, tab) if end is None else piled([], tab)
                listed = list_rows(calculation.rows, media_type=tab, start_date=start, end_date=end)
                time_rows = [row["value"] for row in data["time"]["rows"] if row["media_type"] == tab]
                self.assertEqual(int(sum(row.amount for row in listed) + pile.amount), sum(time_rows), (tab, params))
                titles = list_rows(calculation.rows, media_type=tab, start_date=start, end_date=end, counts=TITLES)
                self.assertEqual(len(titles) + len(pile.titles - {row.title_id for row in titles}), data["header"]["titles"])
                days = {row.day for row in list_rows(calculation.rows, media_type=tab, start_date=start, end_date=end, counts=DAYS)}
                self.assertEqual(len(days), data["header"]["active_days"], (tab, params))

    def url(self, media_type, name, suffix=""):
        return f"/api/v1/tracking/manual/{media_type}/{seed.media_id(name)}/{suffix}"

    def send(self, method, path, body=None, expected=200):
        response = getattr(self.phone(), method)(path, body or {}, format="json")
        self.assertEqual(response.status_code, expected, response.content)
        return response.json() if response.content else None

    def series_save(self, media_type, name, value):
        self.step(self.send, "post", self.url(media_type, name, "progress/"), {"value": value, "mutation_id": str(uuid4())})

    def series_log(self, media_type, name):
        body = {"completion_date": TODAY.isoformat(), "mutation_id": str(uuid4())}
        return self.step(self.send, "post", self.url(media_type, name, "complete/"), body, 201)["diary_entry"]["id"]

    def playthrough(self, name):
        return self.send("get", self.url(GAME, name))["game"]["current_playthrough"]

    def game_save(self, name, minutes):
        """Send the game sheet's Save: hours, and the percent it holds, together."""
        playthrough = self.playthrough(name)
        body = {"total_minutes": minutes, "percentage": playthrough["percentage"], "progressed_on": TODAY.isoformat()}
        self.step(self.send, "patch", self.url(GAME, name, f"playthroughs/{playthrough['id']}/"), body)

    def game_log(self, name, minutes, day=TODAY):
        playthrough = self.playthrough(name)
        body = {
            "playthrough_id": playthrough["id"],
            "completion_date": day.isoformat(),
            "total_minutes": minutes,
            "percentage": playthrough["percentage"],
            "review": "",
            "review_title": "",
            "liked": False,
            "is_rewatch": False,
            "contains_spoilers": False,
            "tags": [],
            "mutation_id": str(uuid4()),
        }
        return self.step(self.send, "post", self.url(GAME, name, "complete/"), body, 201)["diary_entry"]["id"]

    def note(self, params, tab, *, global_row=False):
        row = self.measure(params, tab, tab_only=not global_row)
        return (row["known"], row["total"])

    def test_manga(self):
        self.assert_type(MANGA, 30, 20, 150)
        self.series_save(MANGA, "M1", 60)
        self.assert_type(MANGA, 30, 30, 160)
        entry = self.series_log(MANGA, "M1")
        self.assert_type(MANGA, 30, 70, 200)
        self.step(self.delete_log, entry)
        self.assert_type(MANGA, 30, 30, 160)
        self.assertEqual(self.send("get", self.url(MANGA, "M1"))["progress"]["value"], 60)
        self.step(seed.add_m4)
        self.assertEqual(self.value(ALL_TIME, MANGA), 160)
        self.assertEqual(self.note(ALL_TIME, MANGA), (2, 3), "Based on 2 of 3 manga")

    def test_comics(self):
        self.assert_type(COMIC, 30, None, 40)
        self.assertTrue(self.is_nothing_yet(YEAR_2026, COMIC))
        self.series_save(COMIC, "C1", 35)
        self.assert_type(COMIC, 30, 5, 45)
        self.step(seed.new_issue_c2)
        self.assertEqual(self.send("get", self.url(COMIC, "C2"))["progress"]["value"], 10, "Its sheet opens at 10")
        self.series_save(COMIC, "C2", 11)
        self.assert_type(COMIC, 30, 6, 46)
        known, total = self.note(ALL_TIME, COMIC)
        self.assertEqual(known, total, "No note: C1 has no total, but it has a counted amount")

    def is_nothing_yet(self, params, tab):
        header = self.stats(params, tab=tab)["header"]
        return not (header["titles"] or header["logs"] or header["active_days"])

    def test_games(self):
        self.assert_type(GAME, h(90), h(4), h(94))
        self.game_save("G1", h(14))
        self.assert_type(GAME, h(90), h(6), h(96))
        entry = self.game_log("G1", h(16))
        self.assert_type(GAME, h(90), h(8), h(98))
        self.step(self.delete_log, entry)
        self.assert_type(GAME, h(90), h(8), h(98))
        state = self.send("get", self.url(GAME, "G1"))["game"]["current_playthrough"]
        self.assertEqual((state["status"], state["total_minutes"]), ("In progress", h(16)), "GM-219")
        self.game_save("G2", h(40))
        self.assert_type(GAME, h(90), h(8), h(98))
        self.step(seed.add_g6)
        self.assert_type(GAME, h(90), h(18), h(108))
        entry = self.game_log("G6", h(10), date(2025, 12, 20))
        self.assert_type(GAME, h(100), h(8), h(108))
        self.step(self.delete_log, entry)
        self.assert_type(GAME, h(90), h(18), h(108))
        self.step(self.send, "post", self.url(GAME, "G7", "actions/mark_completed/"), {"mutation_id": str(uuid4())})
        self.assert_type(GAME, h(90), h(18), h(108))
        self.assertEqual(self.note(ALL_TIME, GAME), (5, 6), "Based on 5 of 6 games: G4 has only a percent, G7 no hours")
        self.step(seed.add_g8)
        self.step(self.send, "post", self.url(GAME, "G8", "actions/start/"), {"mutation_id": str(uuid4()), "start_date": TODAY.isoformat()})
        self.game_save("G8", h(8))
        self.assert_type(GAME, h(95), h(26), h(121))

    def test_anime(self):
        year = self.stats(YEAR_2026, tab=ANIME)
        self.assertEqual(self.measure(YEAR_2026, ANIME)["value"], 12 * seed.ANIME_MINUTES)
        details = {row["key"]: row["value"] for row in year["header"]["details"]}
        self.assertEqual(details["episodes"], 12)
        self.assert_type(ANIME, None, 12 * seed.ANIME_MINUTES, 12 * seed.ANIME_MINUTES)
        future = {
            "ref": {"source": "manual", "media_type": ANIME, "media_id": seed.media_id("AN")},
            "consumed_at": "2026-11-04",
        }
        self.step(self.send, "post", "/api/v1/diary/", future, 400)

    def test_the_notes(self):
        self.assertEqual(self.measure(YEAR_2026, BOOK)["value"], 360)
        self.assertEqual(self.note(YEAR_2026, BOOK), (4, 5), "Based on 4 of 5 books")
        self.assertEqual(self.note(ALL_TIME, BOOK), (7, 8), "Based on 7 of 8 books")
        self.assertEqual(self.note(YEAR_2026, BOOK, global_row=True), (4, 5), "Global's Books row carries it too")
        self.step(seed.add_r)
        self.assertEqual(self.note(ALL_TIME, BOOK), (8, 9), "Based on 8 of 9 books")

    def test_a_year_with_only_a_percent_or_a_start_keeps_its_header(self):
        self.step(seed.add_q)
        header = self.stats(YEAR_2023)["header"]
        self.assertEqual((header["titles"], header["logs"], header["active_days"]), (1, 0, 1))
        self.assertEqual(self.note(YEAR_2023, BOOK), (0, 1), "Based on 0 of 1 book")
        self.step(seed.add_game_2023)
        games = self.stats(YEAR_2023, tab=GAME)["header"]
        self.assertEqual((games["titles"], games["logs"], games["active_days"]), (0, 0, 1))


TV_TAB = MediaTypes.TV.value
MOVIE = MediaTypes.MOVIE.value
MUSIC = MediaTypes.MUSIC.value


class Phase3SheetTests(CheckpointBase):
    """Phase 3's sheet: TV, movies and music, and anime's Rewatch switch, row by row."""

    measure = Phase2SheetTests.measure
    value = Phase2SheetTests.value
    assert_type = Phase2SheetTests.assert_type
    assert_type_adds_up = Phase2SheetTests.assert_type_adds_up
    url = Phase2SheetTests.url
    send = Phase2SheetTests.send
    note = Phase2SheetTests.note
    is_nothing_yet = Phase2SheetTests.is_nothing_yet

    def tv_url(self, name, suffix=""):
        return f"/api/v1/tracking/manual/tv/{seed.media_id(name)}/{suffix}"

    def tv_action(self, name, action, **body):
        body["mutation_id"] = str(uuid4())
        return self.step(self.send, "post", self.tv_url(name, f"actions/{action}/"), body)

    def tick(self, name, *numbers):
        result = None
        for number in numbers:
            result = self.tv_action(name, "tick", season_number=1, episode_number=number)
        return result

    def tv_log(self, name, *, episode=None, day=TODAY, rewatch=None):
        """Send the log screen's save: the switch as it opens sends no is_rewatch."""
        ref = {
            "source": "manual",
            "media_type": MediaTypes.EPISODE.value if episode else MediaTypes.SEASON.value,
            "media_id": seed.media_id(name),
            "season_number": 1,
            "episode_number": episode,
        }
        body = {"ref": ref, "consumed_at": day.isoformat(), "mutation_id": str(uuid4())}
        if rewatch is not None:
            body["is_rewatch"] = rewatch
        return self.step(self.send, "post", "/api/v1/diary/", body, 201)["id"]

    def log_of(self, media_type, name, *, rewatch):
        """Send a film's or album's log as DiaryCreateView does: the switch is sent."""
        ref = {"source": "manual", "media_type": media_type, "media_id": seed.media_id(name)}
        body = {"ref": ref, "consumed_at": TODAY.isoformat(), "is_rewatch": rewatch}
        return self.step(self.send, "post", "/api/v1/diary/", body, 201)["id"]

    def flip(self, entry_id, *, on):
        self.step(self.send, "patch", f"/api/v1/diary/{entry_id}/", {"is_rewatch": on})

    def eye(self, media_type, name):
        body = {"mutation_id": str(uuid4())}
        self.step(self.send, "post", self.url(media_type, name, "actions/consume/"), body)

    def is_rewatch(self, entry_id):
        return DiaryEntry.objects.get(pk=entry_id).is_rewatch

    def active_days(self, params, tab):
        return self.stats(params, tab=tab)["header"]["active_days"]

    def test_tv(self):  # noqa: PLR0915
        self.assert_type(TV_TAB, h(10), None, h(10))
        self.assertTrue(self.is_nothing_yet(YEAR_2026, TV_TAB))
        again = self.tv_log("T1")
        self.assertTrue(self.is_rewatch(again), "A season log exists, so the switch opens on")
        self.assert_type(TV_TAB, h(10), h(10), h(20))
        self.flip(again, on=False)
        self.assert_type(TV_TAB, h(10), None, h(10))
        self.assertEqual(self.active_days(YEAR_2026, TV_TAB), 1, "1 active day")
        self.flip(again, on=True)
        self.step(self.delete_log, again)
        self.assert_type(TV_TAB, h(10), None, h(10))
        self.assertTrue(self.is_nothing_yet(YEAR_2026, TV_TAB))
        review = self.tv_log("T1", episode=2)
        self.assertFalse(self.is_rewatch(review), "A review after a season log opens off")
        self.assert_type(TV_TAB, h(10), None, h(10))
        self.assertEqual(self.active_days(YEAR_2026, TV_TAB), 1)
        self.tick("T2", 1, 2)
        self.assert_type(TV_TAB, h(10), h(2), h(12))
        self.tv_action("T3", "mark_watched", season_number=1)
        self.assert_type(TV_TAB, h(10), h(2), h(16))
        first = self.tv_log("T3", rewatch=False)
        self.assert_type(TV_TAB, h(10), h(6), h(16))
        second = self.tv_log("T3", rewatch=True)
        self.assert_type(TV_TAB, h(10), h(10), h(20))
        self.step(self.delete_log, second)
        self.step(self.delete_log, first)
        self.assert_type(TV_TAB, h(10), h(2), h(16))
        self.step(seed.add_t4)
        self.assert_type(TV_TAB, h(10), h(5), h(19))
        backdated = self.tv_log("T4", day=date(2025, 12, 20))
        self.assertFalse(self.is_rewatch(backdated))
        self.assert_type(TV_TAB, h(15), h(2), h(21))
        self.step(self.delete_log, backdated)
        self.assert_type(TV_TAB, h(10), h(5), h(19))
        self.tv_action("T5", "mark_watched", season_number=1)
        self.assert_type(TV_TAB, h(10), h(5), h(21))
        marked = self.tv_log("T5")
        self.assertTrue(self.is_rewatch(marked), "Only marks under it, so it opens on")
        self.assert_type(TV_TAB, h(10), h(7), h(23))
        self.assertFalse(self.is_rewatch(self.tv_log("T5", episode=1)))
        self.assert_type(TV_TAB, h(10), h(7), h(23))
        self.step(seed.add_t6)
        self.assert_type(TV_TAB, h(13), h(10), h(29))
        finish = self.tv_log("T6")
        self.assertTrue(self.is_rewatch(finish))
        self.assert_type(TV_TAB, h(13), h(10), h(29))
        self.tv_action("T7", "mark_up_to", season_number=1, episode_number=3)
        self.assert_type(TV_TAB, h(13), h(10), h(32))
        question = self.tick("T8", 3)["earlier_unwatched"]
        self.tv_action("T8", "mark_earlier", **{"from": question["from"], "to": question["to"]})
        self.assert_type(TV_TAB, h(13), h(11), h(35))

    def test_tv_reviews_with_the_switch_edited(self):
        """TV-402: editing a review's switch counts as saving it that way."""
        review = self.tv_log("T1", episode=2)
        self.assertFalse(self.is_rewatch(review), "A review after a season log opens off")
        self.flip(review, on=True)
        self.assert_type(TV_TAB, h(10), h(1), h(11))
        self.flip(review, on=False)
        self.assert_type(TV_TAB, h(10), None, h(10))
        self.assertEqual(self.active_days(YEAR_2026, TV_TAB), 1)
        self.tick("T2", 1)
        ticked = self.tv_log("T2", episode=1, rewatch=True)
        self.assert_type(TV_TAB, h(10), h(2), h(12))
        self.flip(ticked, on=False)
        self.assert_type(TV_TAB, h(10), h(1), h(11))
        self.flip(ticked, on=True)
        self.assert_type(TV_TAB, h(10), h(2), h(12))
        self.tv_action("T7", "mark_up_to", season_number=1, episode_number=1)
        self.assert_type(TV_TAB, h(10), h(2), h(13))
        marked = self.tv_log("T7", episode=1)
        self.assertTrue(self.is_rewatch(marked), "A marked episode's review opens on")
        self.assert_type(TV_TAB, h(10), h(3), h(14))
        self.flip(marked, on=False)
        self.assert_type(TV_TAB, h(10), h(3), h(13))
        self.flip(marked, on=True)
        self.assert_type(TV_TAB, h(10), h(3), h(14))

    def test_movies(self):
        self.assert_type(MOVIE, None, None, None)
        self.eye(MOVIE, "F2")
        self.assert_type(MOVIE, None, None, h(2))
        entry = self.log_of(MOVIE, "F2", rewatch=True)
        self.assert_type(MOVIE, None, h(2), h(4))
        self.flip(entry, on=False)
        self.assert_type(MOVIE, None, h(2), h(2))
        self.step(self.delete_log, entry)
        self.assert_type(MOVIE, None, None, h(2))
        first = self.log_of(MOVIE, "F2", rewatch=False)
        self.assert_type(MOVIE, None, h(2), h(2))
        second = self.log_of(MOVIE, "F2", rewatch=True)
        self.assert_type(MOVIE, None, h(4), h(4))
        self.assertEqual(self.note(ALL_TIME, MOVIE), (2, 2), "2 watches, not 3")
        self.step(self.delete_log, first)
        self.assert_type(MOVIE, None, h(2), h(4))
        self.step(self.delete_log, second)
        self.assert_type(MOVIE, None, None, h(2))
        self.log_of(MOVIE, "F", rewatch=False)
        self.assert_type(MOVIE, None, h(2), h(4))

    def test_music(self):
        self.eye(MUSIC, "P")
        self.assert_type(MUSIC, None, None, h(1))
        entry = self.log_of(MUSIC, "P", rewatch=True)
        self.assert_type(MUSIC, None, h(1), h(2))
        self.flip(entry, on=False)
        self.assert_type(MUSIC, None, h(1), h(1))
        self.step(self.delete_log, entry)
        self.assert_type(MUSIC, None, None, h(1))

    def test_anime_logged_again_with_the_switch_as_it_opens(self):
        """Decision 62: anime's switch counts as a TV season log's."""
        body = {
            "ref": {"source": "manual", "media_type": ANIME, "media_id": seed.media_id("AN")},
            "consumed_at": TODAY.isoformat(),
            "mutation_id": str(uuid4()),
        }
        entry = self.step(self.send, "post", "/api/v1/diary/", body, 201)["id"]
        self.assertTrue(self.is_rewatch(entry))
        self.assert_type(ANIME, None, 24 * seed.ANIME_MINUTES, 24 * seed.ANIME_MINUTES)
        self.flip(entry, on=False)
        self.assert_type(ANIME, None, 12 * seed.ANIME_MINUTES, 12 * seed.ANIME_MINUTES)
        self.step(self.delete_log, entry)
        self.assert_type(ANIME, None, 12 * seed.ANIME_MINUTES, 12 * seed.ANIME_MINUTES)


@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase4Base(CheckpointBase):
    """Phase 4's opening, and the phone's reads of an Activity list."""

    def open_the_sheet(self):
        seed.seed_phase_4()

    def activity(self, params=None, *, viewer=None, user=seed.DEV_USERNAME, view="list"):
        """Return a list as the phone reads it: the whole of it, page after page."""
        client = self.phone(viewer or self.dev)
        query = {**(params or {}), **({"view": view} if view else {})}
        page = client.get(f"/api/v1/users/{user}/activity/", query)
        self.assertEqual(page.status_code, 200, page.content)
        body = page.json()
        results = list(body["results"])
        while body["next_cursor"]:
            body = {**body, **client.get(body["next_cursor"]).json()}
            results += body["results"]
        return {**body, "results": results}

    def rows(self, page):
        """Return a list's rows as (title, summary, label, time), newest first."""
        return [
            (
                row["media"]["title"],
                row["object"].get("summary", "log"),
                row["object"].get("label"),
                row["object"].get("time_label"),
            )
            for row in page["results"]
        ]



@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase4SheetTests(Phase4Base):
    """Phase 4's sheet: the Activity list opens from the numbers, row by row."""

    def test_the_profile_menu_list_has_every_type_and_no_numbers(self):
        page = self.activity()
        for key in ("top_line", "total", "undated"):
            self.assertNotIn(key, page)
        self.assertGreater(len(page["results"]), 20)
        types = {row["media"]["ref"]["media_type"] for row in page["results"]}
        # A log of a season or an episode names that Item; its row is the show's.
        self.assertEqual(types, {"book", "game", "manga", "comic", "anime", "tv", "season", "episode", "movie"})
        days = [row["consumed_at"] for row in page["results"]]
        self.assertEqual(days, sorted(days, reverse=True))

    def test_books_2026_pages(self):
        page = self.activity({**RANGE_2026, "media_type": BOOK})
        self.assertEqual(page["top_line"], "360 pages")
        pages = [row["object"]["amount"] for row in page["results"] if row["object"]["amount"]]
        self.assertEqual(pages, [50, 120, 50, 100, 40])
        self.assertEqual(sum(pages), 360)
        self.assertIsNone(page["undated"])
        summaries = {(row["media"]["title"], row["consumed_at"]): row["object"]["summary"] for row in page["results"]}
        self.assertEqual(summaries["Book D", "2026-04-01"], "picked back up")
        self.assertEqual(summaries["Book G", "2026-06-02"], "read 25%")
        self.assertEqual(summaries["Book G", "2026-06-01"], "started reading")
        picked = next(row for row in page["results"] if row["consumed_at"] == "2026-04-01")
        self.assertEqual((picked["object"]["amount"], picked["object"]["label"]), (0, None))

    def test_books_all_time_1540_with_the_pile_named(self):
        page = self.activity({"media_type": BOOK})
        self.assertEqual(page["top_line"], "1,540 pages")
        self.assertEqual(page["undated"]["label"], "Includes 1 book and 350 pages with no date")
        self.assertEqual(sum(row["object"]["amount"] for row in page["results"]), 1190)
        self.assertNotIn("Book E", {row["media"]["title"] for row in page["results"]})

    def test_books_all_time_titles(self):
        page = self.activity({"media_type": BOOK, "counts": "titles"})
        self.assertEqual(page["top_line"], "8 books")
        self.assertEqual(len(page["results"]), 7)
        self.assertEqual(page["undated"]["label"], "Includes 1 book with no date")

    def test_book_n_started_is_in_no_title_count_but_its_start_shows(self):
        self.step(seed.add_n)
        titles = self.activity({**RANGE_2026, "media_type": BOOK, "counts": "titles"})
        self.assertEqual(titles["top_line"], "5 books")
        self.assertEqual(
            [(row["media"]["title"], row["consumed_at"]) for row in titles["results"]],
            [("Book G", "2026-06-02"), ("Book F", "2026-05-11"), ("Book D", "2026-04-02"), ("Book A", "2026-03-01"), ("Book C", "2026-02-10")],
        )
        pages = self.activity({**RANGE_2026, "media_type": BOOK})
        self.assertEqual(pages["top_line"], "360 pages")
        self.assertEqual(pages["results"][0]["media"]["title"], "Book N")
        self.assertEqual(pages["results"][0]["object"]["summary"], "started reading")
        self.assertEqual(pages["results"][0]["object"]["amount"], 0)

    def test_tv_2026_hours_and_episodes(self):
        hours = self.activity({**RANGE_2026, "media_type": TV_TAB, "counts": "hours"})
        self.assertEqual(hours["top_line"], "2 hours")
        self.assertEqual(self.rows(hours), [("Show T2", "watched 2 episodes", "2 episodes", "2h")])
        episodes = self.activity({**RANGE_2026, "media_type": TV_TAB, "counts": "episodes"})
        self.assertEqual(episodes["top_line"], "2 episodes")

    def test_tv_all_time_episodes(self):
        page = self.activity({"media_type": TV_TAB, "counts": "episodes"})
        self.assertEqual(page["top_line"], "9 episodes")
        self.assertEqual(
            sorted(row["object"].get("label") or "log" for row in page["results"]),
            ["2 episodes", "3 episodes"],
        )
        self.assertEqual(page["undated"]["label"], "Includes 1 show, 4 episodes and 4 hours with no date")

    def test_games_2026_hours_and_all_time_titles(self):
        hours = self.activity({**RANGE_2026, "media_type": GAME})
        self.assertEqual(hours["top_line"], "4 hours")
        summaries = {
            (row["media"]["title"], row["consumed_at"]): row["object"].get("summary")
            for row in hours["results"]
            if row["type"] == "progress_updated"
        }
        self.assertEqual(summaries["Game G1", "2026-03-01"], "played 2h")
        self.assertEqual(summaries["Game G4", "2026-02-02"], "progressed 40%")
        self.assertEqual(sum(row["object"]["amount"] for row in hours["results"]), 240)
        self.assertIn("log", [row["object"].get("summary", "log") for row in hours["results"]])
        titles = self.activity({"media_type": GAME, "counts": "titles"})
        self.assertEqual(titles["top_line"], "6 games")
        self.assertEqual(len(titles["results"]), 5)
        self.assertEqual(titles["undated"]["label"], "Includes 1 game with no date")

    def test_movies_2026_hours(self):
        page = self.activity({**RANGE_2026, "media_type": MOVIE})
        self.assertEqual(page["top_line"], "2 hours (2h 15m)")
        [row] = page["results"]
        self.assertEqual(row["object"]["time_label"], "2h 15m")
        self.assertIsNone(row["object"]["label"], "A film log sends no label")

    def test_manga_and_comics(self):
        year = self.activity({**RANGE_2026, "media_type": MANGA})
        self.assertEqual(year["top_line"], "20 chapters")
        self.assertEqual(self.rows(year)[0][1], "read 20 chapters")
        everything = self.activity({"media_type": MANGA})
        self.assertEqual(everything["top_line"], "150 chapters")
        self.assertEqual([row["object"]["amount"] for row in everything["results"]], [20, 30])
        self.assertEqual(everything["undated"]["label"], "Includes 1 manga and 100 chapters with no date")
        comics = self.activity({"media_type": COMIC})
        self.assertEqual(comics["top_line"], "40 issues")
        self.assertEqual([row["object"]["amount"] for row in comics["results"]], [30])
        self.assertEqual(comics["undated"]["label"], "Includes 1 comic and 10 issues with no date")

    def test_anime_2026_hours(self):
        page = self.activity({**RANGE_2026, "media_type": ANIME})
        self.assertEqual(page["top_line"], "5 hours (4h 48m)")
        summaries = [(row["object"].get("summary", "log"), row["object"].get("label"), row["object"]["time_label"]) for row in page["results"]]
        self.assertEqual(summaries, [("log", None, None), ("watched 12 episodes", "12 episodes", "4h 48m")])

    def test_active_days_and_streaks(self):
        self.step(seed.add_n)
        days = self.activity({**RANGE_2026, "media_type": BOOK, "counts": "days"})
        self.assertEqual(days["top_line"], "11 active days")
        self.assertEqual(
            sorted({row["consumed_at"] for row in days["results"]}),
            ["2026-02-01", "2026-02-10", "2026-03-01", "2026-04-02", "2026-05-01", "2026-05-02", "2026-05-10", "2026-05-11", "2026-06-01", "2026-06-02", "2026-08-01"],
        )
        header = self.stats(YEAR_2026)["header"]
        self.assertEqual(header["longest_daily_streak"], 2)
        self.assertEqual(header["longest_daily_streak_range"], {"start": "2026-06-01", "end": "2026-06-02"})
        streak = self.activity({**RANGE_2026, "media_type": BOOK, "start_date": "2026-06-01", "end_date": "2026-06-02", "counts": "days"})
        self.assertEqual(streak["top_line"], "2 active days")
        weekly = next(row for row in header["details"] if row["key"] == "longest_weekly_streak")
        self.assertEqual(weekly["value"], 3)
        weeks = self.activity({"media_type": BOOK, "counts": "weeks", "start_date": weekly["range"]["start"], "end_date": weekly["range"]["end"]})
        self.assertEqual(weeks["top_line"], "3 weeks")

    def test_did_not_finish_opens_nothing_and_counts_the_stop(self):
        for params, wanted in ((YEAR_2025, 1), (YEAR_2026, 1)):
            details = {row["key"]: row for row in self.stats(params)["header"]["details"]}
            self.assertEqual(details["did_not_finish"]["value"], wanted)

    def test_global_titles(self):
        year = self.activity({**RANGE_2026, "counts": "titles"})
        self.assertEqual(year["top_line"], "12 titles")
        self.assertEqual(
            sorted(row["media"]["title"] for row in year["results"]),
            sorted(["Anime AN", "Book A", "Book C", "Book D", "Book F", "Book G", "Game G1", "Game G3", "Game G4", "Movie F3", "Manga M1", "Show T2"]),
        )
        everything = self.activity({"counts": "titles"})
        self.assertEqual(everything["top_line"], "23 titles")
        self.assertEqual(len(everything["results"]), 18)
        self.assertEqual(everything["undated"]["label"], "Includes 5 titles with no date")

    def test_global_total_hours(self):
        year = self.activity({**RANGE_2026, "counts": "hours"})
        self.assertEqual(year["top_line"], "13 hours (13h 3m)")
        self.assertEqual(sum(row["object"]["amount"] for row in year["results"]), 783)
        self.assertEqual({row["media"]["ref"]["media_type"] for row in year["results"]}, {"game", "tv", "movie", "anime"})
        everything = self.activity({"counts": "hours"})
        self.assertEqual(everything["top_line"], "110 hours (110h 3m)")
        self.assertEqual(everything["undated"]["label"], "Includes 4 hours with no date")

    def test_t9_reads_as_the_diary_does_with_no_review_and_no_edit(self):
        page = self.activity({"media_type": TV_TAB})
        logs = [row for row in page["results"] if row["type"] == "diary_created"]
        titles = {(row["media"]["title"], row["media"]["show_title"], row["media"]["episode_title"]) for row in logs}
        self.assertIn(("Show T9", "Show T9", None), titles)
        self.assertIn(("Show T9 E2", "Show T9", "Show T9 E2"), titles)
        for row in logs:
            self.assertEqual((row["review"], row["review_title"]), ("", ""))
            self.assertNotIn("editable", row["object"])

    def test_a_follower_reads_the_same_numbers_a_stranger_the_public_part(self):
        for viewer in (self.follower, self.other):
            page = self.activity({**RANGE_2026, "media_type": BOOK}, viewer=viewer)
            self.assertEqual(page["top_line"], "360 pages")


@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase4FriendTests(Phase4Base):
    """Phase 4's seeded friend: what Home and their list show as the Mac does things."""

    def feed(self, viewer=None):
        """Return Home's pages, all of them, as (day, summary, likes, comments)."""
        client = self.phone(viewer or self.dev)
        page = client.get("/api/v1/feed/", {"page_size": 100})
        self.assertEqual(page.status_code, 200, page.content)
        return page.json()["results"]

    def friend_list(self, params=None, viewer=None):
        return self.activity(params, viewer=viewer, user=seed.FRIEND_USERNAME, view=None)

    def short(self, items):
        return [
            (item["consumed_at"], item["media"]["title"], item["object"].get("summary", "log"))
            for item in items
        ]

    def like(self, item, viewer=None):
        response = self.phone(viewer or self.dev).post(f"/api/v1/progress/{item['object']['id']}/like/")
        self.assertEqual(response.status_code, 200, response.content)

    def test_saves_and_a_movie_log_order_by_day_then_by_when(self):
        self.step(seed.friend_day_order)
        self.assertEqual(
            self.short(self.feed()),
            [
                ("2026-11-02", "Book FB1", "read 30 pages"),
                ("2026-11-01", "Book FB1", "read 100 pages"),
                ("2026-11-01", "Movie FM", "log"),
            ],
        )

    def test_a_typo_fixed_later_keeps_its_like_and_their_stats_agree(self):
        self.step(seed.friend_typo)
        [row] = self.feed()
        self.assertEqual((row["object"]["summary"], row["like_count"]), ("read 300 pages", 1))
        self.step(seed.friend_typo_fixed)
        [row] = self.feed()
        self.assertEqual((row["consumed_at"], row["object"]["summary"], row["like_count"]), ("2025-12-28", "read 30 pages", 1))
        for params, wanted in ((YEAR_2025, 30), (YEAR_2026, 0)):
            self.assertEqual(self.friend_pages(params), wanted)

    def friend_pages(self, params):
        response = self.phone().get(f"/api/v1/users/{seed.FRIEND_USERNAME}/stats/summary/", {"schema": "2", **params, "media_type": BOOK})
        self.assertEqual(response.status_code, 200, response.content)
        row = next((row for row in response.json()["time"]["rows"] if row["media_type"] == BOOK), None)
        return row["value"] if row else 0

    def test_100_pages_and_a_finish_on_one_day_are_two_items(self):
        self.step(seed.friend_save_and_finish)
        items = self.feed()
        self.assertEqual(
            sorted((item["type"], item["object"].get("summary", "log")) for item in items),
            [("diary_created", "log"), ("progress_updated", "read 100 pages")],
        )
        log = next(item for item in items if item["type"] == "diary_created")
        self.assertEqual(log["object"]["label"], "300 pages")

    def test_pause_resume_stop_and_restart_are_four_rows_and_one_active_day(self):
        self.step(seed.friend_pause_stop_pick_up)
        rows = self.feed()
        self.assertEqual(
            [(row["consumed_at"], row["object"]["summary"]) for row in rows],
            [
                ("2026-10-15", "picked back up"),
                ("2026-10-14", "stopped"),
                ("2026-10-13", "picked back up"),
                ("2026-10-12", "read 40 pages, paused"),
            ],
        )
        stopped = rows[1]
        self.assertIsNone(stopped["object"]["label"])
        page = self.phone().get(f"/api/v1/progress/{stopped['object']['id']}/")
        self.assertEqual(page.status_code, 200, page.content)
        self.like(stopped)
        response = self.phone().get(f"/api/v1/users/{seed.FRIEND_USERNAME}/stats/summary/", {"schema": "2", **YEAR_2026, "media_type": BOOK})
        self.assertEqual(response.json()["header"]["active_days"], 1)

    def test_a_stopped_game_chosen_playing_is_a_new_playthrough(self):
        self.step(seed.friend_game_stops_then_plays)
        self.assertEqual(
            [(row["consumed_at"], row["object"]["summary"]) for row in self.feed()],
            [("2026-10-14", "started playing"), ("2026-10-13", "stopped"), ("2026-10-12", "started playing")],
        )

    def test_ticks_and_an_untick_leave_one_episode_a_day(self):
        self.step(seed.friend_ticks)
        self.assertEqual(
            [(row["consumed_at"], row["object"]["summary"]) for row in self.feed()],
            [("2026-10-20", "watched 1 episode"), ("2026-10-19", "watched 1 episode")],
        )

    def test_a_late_tick_dated_that_monday_keeps_the_like_and_comment(self):
        self.step(seed.friend_ticks)
        monday = self.feed()[1]
        self.like(monday)
        response = self.phone().post(f"/api/v1/progress/{monday['object']['id']}/comments/", {"body": "Nice"}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.step(seed.friend_late_tick)
        rows = {row["consumed_at"]: row for row in self.feed()}
        row = rows["2026-10-19"]
        self.assertEqual(row["object"]["summary"], "watched 2 episodes")
        self.assertEqual((row["like_count"], row["comment_count"]), (1, 1))
        self.assertEqual(rows["2026-10-20"]["object"]["summary"], "watched 1 episode")

    def test_plays_dated_last_week_sit_in_the_past(self):
        self.step(seed.friend_import_last_week)
        self.step(seed.friend_day_order)
        days = [(row["consumed_at"], row["media"]["title"]) for row in self.feed()]
        # Written first or last, dated a week ago: under the newer rows, never on top.
        self.assertEqual(days[-1], ("2026-10-27", "Show FS3"))
        self.assertEqual([day for day, _ in days], sorted((day for day, _ in days), reverse=True))

    def test_marks_make_no_row(self):
        self.step(seed.friend_marks)
        self.assertEqual(self.feed(), [])
        self.assertEqual(self.friend_list()["results"], [])

    def test_a_tick_and_the_earlier_question_make_one_row(self):
        self.step(seed.friend_earlier_question)
        [row] = self.feed()
        self.assertEqual((row["object"]["summary"], row["object"]["amount"]), ("watched 1 episode", 60))

    def test_a_private_season_log_adds_to_their_numbers_but_not_to_yours(self):
        self.step(seed.friend_private_season)
        friend_path = f"/api/v1/users/{seed.FRIEND_USERNAME}/stats/summary/"
        mine = self.phone().get(friend_path, {"schema": "2", **YEAR_2026, "media_type": TV_TAB}).json()
        tv = next(row for row in mine["time"]["rows"] if row["media_type"] == TV_TAB)
        self.assertEqual(tv["value"], 120)
        page = self.friend_list({**RANGE_2026, "media_type": TV_TAB})
        self.assertEqual(page["top_line"], "2 hours")
        self.assertEqual(self.short(page["results"]), [("2026-04-06", "Show FS2", "watched 2 episodes")])
        own = self.phone(self.friend_account()).get(friend_path, {"schema": "2", **YEAR_2026, "media_type": TV_TAB}).json()
        tv = next(row for row in own["time"]["rows"] if row["media_type"] == TV_TAB)
        self.assertEqual(tv["value"], 300)
        theirs = self.friend_list({**RANGE_2026, "media_type": TV_TAB}, viewer=self.friend_account())
        self.assertEqual(theirs["top_line"], "5 hours")

    def friend_account(self):
        return seed.friend_user()

    def test_a_comment_from_the_mac_is_there_when_the_row_opens(self):
        newest = self.activity()["results"][0]
        self.step(seed.comment_on_my_row)
        page = self.phone().get(f"/api/v1/diary/{newest['object']['id']}/comments/" if newest["type"] == "diary_created" else f"/api/v1/progress/{newest['object']['id']}/comments/")
        self.assertEqual([comment["body"] for comment in page.json()["results"]], ["Nice one."])


RANGE_2026 = {"start_date": "2026-01-01", "end_date": "2026-12-31"}


@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase5Base(CheckpointBase):
    """Phase 5's opening: no books, logs or saves but P and M3, from last year."""

    measure = Phase2SheetTests.measure
    value = Phase2SheetTests.value
    url = Phase2SheetTests.url
    send = Phase2SheetTests.send

    def open_the_sheet(self):
        seed.seed_phase_5()

    def home(self, viewer=None, params=None):
        """Return Home's rows as the new build asks for them."""
        page = self.phone(viewer or self.dev).get("/api/v1/feed/", {"include": "planning", "page_size": 100, **(params or {})})
        self.assertEqual(page.status_code, 200, page.content)
        return page.json()["results"]

    def my_list(self, *, planning=True):
        """Return the profile's Activity list, whole, as the new build asks."""
        query = {"view": "list", "page_size": 100, **({"include": "planning"} if planning else {})}
        client = self.phone()
        body = client.get(f"/api/v1/users/{seed.DEV_USERNAME}/activity/", query).json()
        return body["results"]

    def planning(self, rows):
        return [row for row in rows if row["type"] == "planning_added"]

    def summaries(self, rows=None):
        return [(row["consumed_at"], row["object"]["summary"]) for row in (rows if rows is not None else self.my_list())]

    def everything(self):
        """Return every Stats payload that could move: the year, all time, each tab."""
        payloads = {}
        for name, params in (("2025", YEAR_2025), ("2026", YEAR_2026), ("all", ALL_TIME)):
            for tab in (None, BOOK, "tv", "movie", "game", "manga", "music"):
                payloads[name, tab] = self.stats(params, tab=tab)
        return payloads

    def tracking(self, media_type, name, suffix=""):
        return self.url(media_type, name, suffix)

    def to_planning(self, media_type, name):
        """Send the app's Add to Planning: a show's action, anything else's status."""
        if media_type == MediaTypes.TV.value:
            body = {"mutation_id": str(uuid4())}
            return self.step(self.send, "post", self.tracking(media_type, name, "actions/planning/"), body)
        return self.step(self.send, "put", self.tracking(media_type, name), {"status": "Planning"})

    def save_p(self, value, day):
        body = {"progress_type": "pages", "value": value, "progressed_on": day.isoformat()}
        self.step(self.send, "post", self.tracking(BOOK, "P", "progress/"), body)

    def row_on(self, title, day):
        rows = [
            row
            for row in self.my_list()
            if row["type"] == "progress_updated" and row["media"]["title"] == title and row["consumed_at"] == day
        ]
        return rows[0] if rows else None

    def post_page(self, row):
        response = self.phone().get(f"/api/v1/progress/{row['object']['id']}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase5SeedTests(Phase5Base):
    """What Phase 5's seed promises."""

    def test_the_opening(self):
        self.assertEqual(self.pages(YEAR_2025), 100)
        self.assertEqual(self.pages(ALL_TIME), 100)
        self.assertEqual(self.pages(YEAR_2026), 0)
        self.assertEqual(self.value(YEAR_2025, "manga"), 10)
        self.assertEqual(self.planning(self.my_list()), [])
        self.assertEqual(ContentLike.objects.count(), 0)
        self.assertEqual(
            {name: getattr(self.dev, f"{name}_enabled") for name in ("book", "manga", "movie", "tv", "game")},
            dict.fromkeys(("book", "manga", "movie", "tv", "game"), True),
        )

    def test_the_list_holds_what_the_phone_opens(self):
        listed = CustomList.objects.get(owner=self.dev, name=seed.LIST_NAME)
        items = [row.item for row in CustomListItem.objects.filter(custom_list=listed).select_related("item")]
        self.assertEqual(
            sorted(item.title for item in items),
            sorted(
                [
                    *("Show PS1", "Show PS2", "Show PS3", "Show PS4"),
                    *("Book PB", "Game PG", "Manga PM", "Book P", "Manga M3"),
                    *("Movie W1", "Movie W2"),
                ],
            ),
        )
        for item in Item.objects.filter(media_type="movie", source="manual", media_id__startswith=seed.PREFIX):
            self.assertEqual(item.runtime_minutes, 120)

    def test_it_runs_again_over_whatever_was_left(self):
        self.to_planning(MediaTypes.MOVIE.value, "W1")
        self.save_p(150, date(2025, 12, 20))
        with self.captureOnCommitCallbacks(execute=True):
            seed.seed_phase_5()
        self.test_the_opening()
        self.assertFalse(ProgressChange.objects.filter(current_progress__kind="planning").exists())

    def test_it_refuses_a_database_that_isnt_sqlite(self):
        with (
            patch.object(seed, "connection", SimpleNamespace(vendor="postgresql")),
            override_settings(TESTING=False),
            self.assertRaises(RuntimeError),
        ):
            seed.seed_phase_5()


@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase5PlanningSheetTests(Phase5Base):
    """Phase 5's first sheet: a day of planning reads as one line."""

    def test_the_sheet(self):
        before = self.everything()
        # Must: add 3 shows. One row on the list, and tapping shows those 3.
        for name in ("PS1", "PS2", "PS3"):
            self.to_planning(MediaTypes.TV.value, name)
        rows = self.planning(self.my_list())
        self.assertEqual([row["object"]["summary"] for row in rows], ["added 3 shows to Planning"])
        self.assertEqual(rows[0]["consumed_at"], TODAY.isoformat())
        opened = self.phone().get(f"/api/v1/planning/{rows[0]['object']['id']}/")
        self.assertEqual([title["title"] for title in opened.json()["results"]], ["Show PS1", "Show PS2", "Show PS3"])
        # Without the opt-in, an installed build sees none.
        self.assertEqual(self.planning(self.my_list(planning=False)), [])
        self.assertEqual(self.everything(), before)
        # The book, the game and the manga (To Read) too. Remove the book.
        self.to_planning(BOOK, "PB")
        self.to_planning(MediaTypes.GAME.value, "PG")
        self.to_planning(MediaTypes.MANGA.value, "PM")
        self.assertEqual(self.planning(self.my_list())[0]["object"]["summary"], "added 6 titles to Planning")
        self.step(self.send, "delete", self.tracking(BOOK, "PB"), expected=204)
        self.assertEqual(self.planning(self.my_list())[0]["object"]["summary"], "added 5 titles to Planning")
        self.assertEqual(self.everything(), before)
        # The Mac adds 2 movies, as the website does.
        self.step(seed.add_two_movies)
        self.assertEqual(self.planning(self.my_list())[0]["object"]["summary"], "added 7 titles to Planning")
        self.assertEqual(self.everything(), before)
        # A Letterboxd watchlist file: Planning in the Library rises, the row stays.
        planned = Movie.objects.filter(user=self.dev, status="Planning").count()
        added = self.step(seed.letterboxd_watchlist)
        self.assertGreater(added, 0)
        self.assertEqual(Movie.objects.filter(user=self.dev, status="Planning").count(), planned + added)
        self.assertEqual(self.planning(self.my_list())[0]["object"]["summary"], "added 7 titles to Planning")
        self.assertEqual(self.everything(), before)
        # Log one of the 2 movies: the row stays, plus the log. Movies: 2 hours.
        film = {"source": "manual", "media_type": "movie", "media_id": seed.media_id("W1")}
        body = {"ref": film, "consumed_at": TODAY.isoformat(), "is_rewatch": False}
        self.step(self.send, "post", "/api/v1/diary/", body, 201)
        kinds = sorted(row["type"] for row in self.my_list() if row["media"]["ref"]["media_type"] != "book" and row["type"] != "progress_updated")
        self.assertEqual(kinds, ["diary_created", "planning_added"])
        self.assertEqual(self.planning(self.my_list())[0]["object"]["summary"], "added 7 titles to Planning")
        self.assertEqual(self.value(YEAR_2026, "movie"), 120)

    def test_a_friend_adds_three_movies_and_home_shows_one_row(self):
        self.step(seed.friend_adds_three_movies)
        rows = self.planning(self.home())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["actor"]["username"], seed.FRIEND_USERNAME)
        self.assertEqual(row["media"]["title"], "Movie FP1")
        self.assertEqual(row["object"]["summary"], "added 3 movies to Planning")
        self.assertEqual((row["like_count"], row["comment_count"]), (0, 0))
        opened = self.phone().get(f"/api/v1/planning/{row['object']['id']}/").json()
        self.assertEqual([title["title"] for title in opened["results"]], ["Movie FP1", "Movie FP2", "Movie FP3"])
        # An installed build's Home has none of it.
        plain = self.phone().get("/api/v1/feed/", {"page_size": 100}).json()["results"]
        self.assertEqual(self.planning(plain), [])
        # And the friend's own list carries it, for a viewer who follows them.
        theirs = self.phone().get(f"/api/v1/users/{seed.FRIEND_USERNAME}/activity/", {"include": "planning"}).json()["results"]
        self.assertEqual(len(self.planning(theirs)), 1)

    def test_a_follower_sees_my_row_too_and_a_stranger_the_public_profile(self):
        self.to_planning(MediaTypes.TV.value, "PS1")
        for viewer in (self.follower, self.other):
            page = self.phone(viewer).get(f"/api/v1/users/{seed.DEV_USERNAME}/activity/", {"include": "planning"}).json()
            self.assertEqual(len(self.planning(page["results"])), 1)


@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase5RowsSheetTests(Phase5Base):
    """Phase 5's second sheet: a save on the right day, and a row fixed from the row."""

    def test_the_sheet(self):
        dec_20, dec_27 = date(2025, 12, 20), date(2025, 12, 27)
        # Must: P to 120, dated Dec 20 2025. This year's column doesn't move.
        self.save_p(120, dec_20)
        self.assert_sheet(120, 0, 120)
        row = self.row_on("Book P", "2025-12-20")
        self.assertEqual(row["object"]["summary"], "read 20 pages")
        # Must: open that row, tap Edit, change 120 to 110 on the normal sheet.
        edit = self.post_page(row)["viewer"]["edit"]
        self.assertEqual((edit["mode"], edit["value"], edit["date"], edit["unit"]), ("save", 120, "2025-12-20", "page"))
        self.save_p(110, dec_20)
        self.assert_sheet(110, 0, 110)
        self.assertEqual(self.row_on("Book P", "2025-12-20")["object"]["summary"], "read 10 pages")
        # Must: a like from the Mac, then the trash (it's the newest on this title).
        self.step(seed.like_p_row)
        row = self.row_on("Book P", "2025-12-20")
        self.assertEqual(row["like_count"], 1)
        viewer = self.post_page(row)["viewer"]
        self.assertEqual((viewer["can_edit"], viewer["can_delete"]), (True, True))
        self.step(self.send, "delete", f"/api/v1/progress/{row['object']['id']}/", expected=204)
        self.assert_sheet(100, 0, 100)
        self.assertIsNone(self.row_on("Book P", "2025-12-20"))
        self.assertEqual(self.opens_at_p(), 100)
        self.assertFalse(ContentLike.objects.filter(target_type=ContentLike.PROGRESS_POST).exists())
        # The row is back with 0 likes, because it was deleted on purpose.
        self.save_p(120, dec_20)
        self.assert_sheet(120, 0, 120)
        self.assertEqual(self.row_on("Book P", "2025-12-20")["like_count"], 0)
        # P to 200 on Dec 27; fix the Dec 20 row from 120 to 110. It has no trash.
        self.save_p(200, dec_27)
        older = self.row_on("Book P", "2025-12-20")
        page = self.post_page(older)["viewer"]
        self.assertEqual((page["can_delete"], page["edit"]["mode"], page["edit"]["min"], page["edit"]["max"]), (False, "number", 100, 200))
        self.step(self.send, "patch", f"/api/v1/progress/{older['object']['id']}/", {"value": 110})
        self.assertEqual(self.row_on("Book P", "2025-12-20")["object"]["summary"], "read 10 pages")
        self.assertEqual(self.row_on("Book P", "2025-12-27")["object"]["summary"], "read 90 pages")
        self.assert_sheet(200, 0, 200)
        self.assertEqual(self.opens_at_p(), 200)
        self.step(self.send, "delete", f"/api/v1/progress/{older['object']['id']}/", expected=409)
        # M3 to 12, dated Dec 20 2025.
        body = {"value": 12, "progressed_on": "2025-12-20", "mutation_id": str(uuid4())}
        self.step(self.send, "post", self.tracking(MediaTypes.MANGA.value, "M3", "progress/"), body)
        self.assertEqual(self.row_on("Manga M3", "2025-12-20")["object"]["summary"], "read 2 chapters")
        self.assertEqual(self.value(YEAR_2025, "manga"), 12)
        # A show's tick has neither control.
        self.step(
            self.send,
            "post",
            self.tracking(MediaTypes.TV.value, "PS4", "actions/tick/"),
            {"season_number": 1, "episode_number": 1, "mutation_id": str(uuid4())},
        )
        tick = next(row for row in self.my_list() if row["media"]["title"] == "Show PS4")
        viewer = self.post_page(tick)["viewer"]
        self.assertEqual((viewer["can_edit"], viewer["can_delete"], viewer["edit"]), (False, False, None))
        self.assertEqual(self.phone().delete(f"/api/v1/progress/{tick['object']['id']}/").status_code, 400)

    def assert_sheet(self, in_2025, in_2026, all_time):
        self.assertEqual((self.pages(YEAR_2025), self.pages(YEAR_2026), self.pages(ALL_TIME)), (in_2025, in_2026, all_time))
        self.assert_adds_up()

    def opens_at_p(self):
        """Return where the sheet for P opens: its place."""
        return self.opens_at("P")["value"]

    def test_a_friend_cannot_fix_or_delete_my_row(self):
        self.save_p(120, date(2025, 12, 20))
        row = self.row_on("Book P", "2025-12-20")
        friend = self.phone(self.follower)
        self.assertEqual(friend.delete(f"/api/v1/progress/{row['object']['id']}/").status_code, 403)
        self.assertEqual(friend.patch(f"/api/v1/progress/{row['object']['id']}/", {"value": 110}, format="json").status_code, 403)
        self.assertEqual(set(self.phone(self.follower).get(f"/api/v1/progress/{row['object']['id']}/").json()["viewer"]), {"can_view", "has_liked"})


@override_settings(TIME_ZONE="UTC", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase5PlanningSheetOnUTCTests(Phase5PlanningSheetTests):
    """The test backend's settings resolve to UTC, so the phone test runs there."""


@override_settings(TIME_ZONE="UTC", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase5RowsSheetOnUTCTests(Phase5RowsSheetTests):
    """The test backend's settings resolve to UTC, so the phone test runs there."""


@override_settings(TIME_ZONE="UTC", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase4SheetOnUTCTests(Phase4SheetTests):
    """The test backend's settings resolve to UTC, so the phone test runs there."""


@override_settings(TIME_ZONE="UTC", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase4FriendOnUTCTests(Phase4FriendTests):
    """The friend's steps on the test backend's zone."""


@override_settings(TIME_ZONE="UTC", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase3SheetOnUTCTests(Phase3SheetTests):
    """The test backend's settings resolve to UTC, so the phone test runs there."""


@override_settings(TIME_ZONE="UTC", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class Phase2SheetOnUTCTests(Phase2SheetTests):
    """The test backend's settings resolve to UTC, so the phone test runs there."""


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

    def run_it(self, checkout, *, served, **extra):
        status = f'echo "SERVED_CHECKOUT: {served}"\necho "SERVING: feature from {served}, until Wed 21:40"\n'
        self.write_script(checkout / "scripts" / "ios" / "test-backend.sh", status)
        environment = {key: value for key, value in os.environ.items() if key != "DB_HOST"}
        environment["PATH"] = f"{self.bin}{os.pathsep}{environment['PATH']}"
        environment.update(extra)
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
        self.assertIn("Book J, Book V and Book X, in that order", result.stdout)
        self.assertIn("until Wed 21:40", result.stdout)

    def seeded(self):
        """Return whether the stand-in for uv was asked to run a step."""
        calls = self.root / "uv-calls"
        return calls.exists() and "STEPS" in calls.read_text()

    def test_a_database_that_isnt_sqlite_is_refused(self):
        feature = self.worktree(".claude/worktrees/feature")
        self.write_script(self.bin / "uv", f'echo "$*" >> "{self.root}/uv-calls"\necho postgresql\n')
        result = self.run_it(feature, served=feature)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("postgresql", result.stderr)
        self.assertFalse(self.seeded())

    def test_db_host_is_refused(self):
        feature = self.worktree(".claude/worktrees/feature")
        self.write_script(self.bin / "uv", f'echo "$*" >> "{self.root}/uv-calls"\necho sqlite\n')
        result = self.run_it(feature, served=feature, DB_HOST="somewhere")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("DB_HOST", result.stderr)
        self.assertFalse(self.seeded())

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
