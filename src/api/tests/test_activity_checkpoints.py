"""Each phone sheet, step by step, so a wrong number fails here before Armaan sees it.

With the Stats cache on and each step run through commit, a write that misses a bump
leaves the old number. Everything is read through the real endpoints, as JSON, so a
Decimal rendered as 360.0 shows. The seed is the same one the phone test runs.
"""

from datetime import date, datetime
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.services import stats_cache
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
from app import game_tracking, series_tracking, tv_tracking
from app.models import TV, Book, DiaryEntry, Episode, Item, MediaTypes, Season
from lists.models import CustomList, CustomListItem
from social.models import (
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
        self.dirty_the_dev_user()
        with self.captureOnCommitCallbacks(execute=True):
            seed.seed()
            Follow.objects.create(from_user=self.follower, to_user=seed.dev_user(), status=FollowStatus.ACCEPTED)
        self.dev = seed.dev_user()

    def dirty_the_dev_user(self):
        """Nothing here should reach the sheet: override to leave something behind."""

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
        rows = self.stats(params, user=user)["time"]["rows"]
        return next((row["value"] for row in rows if row["media_type"] == BOOK), 0)

    def assert_sheet(self, in_2025, in_2026, all_time):
        self.assertEqual(
            (self.pages(YEAR_2025), self.pages(YEAR_2026), self.pages(ALL_TIME)),
            (in_2025, in_2026, all_time),
        )
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
        response = self.phone().post(self.book_url(name, "progress/"), {"progress_type": kind, "value": value}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def log(self, name, day=TODAY):
        book = Book.objects.get(user=self.dev, item=seed.book(name))
        live = book.current_session_id if book.status in ("In progress", "Paused") else None
        response = self.phone().post(
            self.book_url(name, "complete/"),
            {"journey_id": live, "completion_date": day.isoformat(), "mutation_id": str(uuid4())},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["diary_entry"]["id"]

    def delete_log(self, entry_id):
        response = self.phone().delete(f"/api/v1/diary/{entry_id}/")
        self.assertEqual(response.status_code, 204, response.content)

    def action(self, name, action):
        response = self.phone().post(self.book_url(name, f"actions/{action}/"), {"mutation_id": str(uuid4())}, format="json")
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
    """It runs twice over a dirtied dev user and opens at the same numbers."""

    def dirty_the_dev_user(self):
        users = get_user_model().objects
        dev, _ = users.get_or_create(username=seed.DEV_USERNAME)
        friend, _ = users.get_or_create(username=seed.FRIEND_USERNAME)
        stray = Item.objects.create(source="manual", media_type="book", media_id="stray", title="Stray", image="x", total_pages=99)
        DiaryEntry.objects.create(user=dev, item=stray, consumed_at=datetime(2026, 8, 1, tzinfo=ZoneInfo("UTC")))
        Follow.objects.create(from_user=dev, to_user=friend)

    def test_the_stray_is_gone_and_the_numbers_open_the_same(self):
        self.assert_sheet(830, 360, 1540)
        with self.captureOnCommitCallbacks(execute=True):
            seed.seed()
        self.assert_sheet(830, 360, 1540)

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


@override_settings(TIME_ZONE="UTC", CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class SeedOnUTCTests(CheckpointBase):
    """A server on UTC runs the same sheet: each day comes from the phone's calendar."""

    def test_the_opening_numbers(self):
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
        self.step(self.action, "S", "start")
        self.assertEqual(self.opens_at("S")["value"], 120)
        self.step(self.update_progress, "S", 150)
        self.assert_sheet(830, 1000, 2180)
        self.step(seed.add_u)
        self.step(self.action, "U", "start")
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
        self.step(self.action, "X", "start")
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

    def test_game_y(self):
        item = seed.title(MediaTypes.GAME.value, "Y")
        self.step(game_tracking.start, self.dev, item)
        self.step(game_tracking.update_progress, self.dev, item, total_minutes=120)
        self.step(game_tracking.drop, self.dev, item)
        self.assertEqual(
            self.rows(item),
            [("start", None, False), ("minutes", None, False), ("status", "stopped", False)],
        )

    def test_show_z_ticked_and_unticked(self):
        show = seed.show_z()
        self.step(tv_tracking.tick, self.dev, show, 1, 1)
        self.assertEqual(self.rows(show), [("start", None, False)])
        self.step(tv_tracking.untick, self.dev, show, 1, 1)
        self.assertEqual(self.rows(show), [])

    def test_manga_w(self):
        item = seed.title(MediaTypes.MANGA.value, "W")
        self.step(series_tracking.start, self.dev, item)
        self.step(series_tracking.update_progress, self.dev, item, value=5)
        self.step(series_tracking.pause, self.dev, item)
        self.step(series_tracking.start, self.dev, item)
        self.assertEqual(
            self.rows(item),
            [
                ("start", None, False),
                ("count", None, False),
                ("status", "paused", False),
                ("status", "picked back up", False),
            ],
        )
