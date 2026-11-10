"""A day's progress on a title is one activity item (docs/activity-contract.md)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import latest_progress_change_for
from api.services import tracking as tracking_service
from api.tests.feed_fixtures import show_with_episodes, tick
from app import (
    book_tracking,
    game_tracking,
    series_tracking,
    single_weight,
    tv_tracking,
)
from app.models import TV, Anime, DiaryEntry, Item, MediaTypes, Sources, Status
from app.tests.anime_fixtures import FakeAnimeProvider
from social.models import Follow, FollowStatus, ProgressChange


def at(day, hour=12):
    """Return a UTC time in October 2026."""
    return datetime(2026, 10, day, hour, tzinfo=UTC)


class DailyProgressTests(TestCase):
    """Progress rolls up per title and day; logs and progress share one order.

    A TV day comes from its ticks and a book's from its saves, so these write through
    the functions the phone's requests do (the Activity calculation reads nothing else).
    """

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="watcher", password="strong-password-123")
        self.viewer = User.objects.create_user(username="friend", password="strong-password-123")
        self.client = APIClient()
        self.show = show_with_episodes()

    def item(self, media_type, media_id="one", **fields):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=f"{media_type}-{media_id}",
            title=f"{media_type} {media_id}",
            **fields,
        )

    def tick(self, episodes, when, season=1, show=None):
        tick(self.owner, show or self.show, episodes, when, season)

    def save(self, function, item, when, *args, **kwargs):
        """Save through a tracking function at this moment."""
        with patch("django.utils.timezone.now", return_value=when):
            return function(self.owner, item, *args, **kwargs)

    def log(self, item, consumed_at, **fields):
        return DiaryEntry.objects.create(user=self.owner, item=item, consumed_at=consumed_at, **fields)

    def activity(self, viewer=None, **params):
        self.client.force_authenticate(viewer or self.owner)
        response = self.client.get("/api/v1/users/watcher/activity/", params)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["results"]

    def summaries(self, results):
        return [
            (activity["consumed_at"], activity["media"]["title"], activity["object"].get("summary", "log"))
            for activity in results
        ]

    def test_a_days_episodes_are_one_item_for_the_show(self):
        self.tick([1, 2], at(24, 9))
        self.tick([3], at(24, 10))
        self.tick([1], at(24, 21), season=2)

        [activity] = self.activity()

        self.assertEqual(activity["type"], "progress_updated")
        # A show's day is addressed by its person, title and day (plan 5.8).
        self.assertEqual(activity["id"], -activity["object"]["id"])
        self.assertEqual(activity["object"]["id"] >> 44, self.owner.pk)
        self.assertEqual(activity["consumed_at"], "2026-10-24")
        self.assertEqual(activity["created_at"].replace(microsecond=0), at(24, 21))
        self.assertEqual(activity["media"]["ref"]["media_type"], "tv")
        self.assertEqual(activity["media"]["title"], "Severance")
        self.assertEqual(
            activity["object"],
            {
                "type": "progress_day",
                "id": activity["object"]["id"],
                "summary": "watched 4 episodes",
                "label": "4 episodes",
                "amount": 180,
                "unit": "minutes",
                "time_label": "3h",
                "moves": [],
                "percent": None,
                "place": {"season": 2, "episodes": 1},
            },
        )

    def test_each_day_is_its_own_item_newest_day_first(self):
        self.tick([1], at(23))
        self.tick([2, 3], at(24))

        self.assertEqual(
            self.summaries(self.activity()),
            [
                ("2026-10-24", "Severance", "watched 2 episodes"),
                ("2026-10-23", "Severance", "watched 1 episode"),
            ],
        )

    @override_settings(TIME_ZONE="America/New_York")
    def test_the_day_is_the_local_day(self):
        # 02:00 UTC on the 25th is still the evening of the 24th in New York.
        self.tick([1], datetime(2026, 10, 25, 2, tzinfo=UTC))

        [activity] = self.activity()

        self.assertEqual(activity["consumed_at"], "2026-10-24")

    @override_settings(TIME_ZONE="America/New_York")
    def test_an_evening_log_keeps_its_local_day(self):
        # 21:00 on the 25th in New York is 01:00 UTC on the 26th.
        episode = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            media_id="other-show",
            season_number=1,
            episode_number=1,
            title="Other Show",
        )
        self.log(episode, datetime(2026, 10, 26, 1, tzinfo=UTC))
        self.tick([1], datetime(2026, 10, 26, 16, tzinfo=UTC))

        self.assertEqual(
            self.summaries(self.activity()),
            [
                ("2026-10-26", "Severance", "watched 1 episode"),
                ("2026-10-25", "Other Show", "log"),
            ],
        )

    def test_a_log_the_viewer_cant_see_leaves_only_that_days_progress(self):
        show = show_with_episodes("Two Episodes", seasons={1: 2})
        self.tick([1, 2], at(24), show=show)
        season = Item.objects.get(media_id=show.media_id, media_type=MediaTypes.SEASON.value)
        self.save(tv_tracking.create_log, season, at(24, 20), consumed_at=date(2026, 10, 24), visibility="followers")

        self.assertEqual(
            [activity["object"].get("summary", "log") for activity in self.activity(viewer=self.viewer)],
            ["watched 2 episodes"],
        )
        self.assertEqual(
            [activity["type"] for activity in self.activity()],
            ["diary_created", "progress_updated"],
        )

    def test_removing_a_title_removes_its_progress(self):
        self.tick([1], at(24))
        self.assertEqual(len(self.activity()), 1)

        tracking_service.delete_tracking(self.owner, source="manual", media_type="tv", media_id=self.show.media_id)

        self.assertEqual(self.activity(), [])

    def test_a_fix_that_lowers_a_number_takes_it_off_the_newest_save(self):
        book = self.item(MediaTypes.BOOK.value)
        self.save(book_tracking.start, book, at(20))
        self.save(book_tracking.update_progress, book, at(24), progress_type="pages", value=300)
        self.save(book_tracking.update_progress, book, at(25), progress_type="pages", value=30)

        rows = [row for row in self.summaries(self.activity()) if row[2] != "started reading"]

        # The fix took its pages off the 24th, and the 25th reads nothing.
        self.assertEqual(rows, [("2026-10-24", "book one", "read 30 pages")])

    def test_logging_the_title_that_day_keeps_its_progress_as_its_own_item(self):
        book = self.item(MediaTypes.BOOK.value)
        self.save(book_tracking.start, book, at(24))
        self.save(book_tracking.update_progress, book, at(24), progress_type="percentage", value=20)
        book_log = self.log(book, single_weight.calendar_datetime(date(2026, 10, 24)))
        episode = Item.objects.get(
            media_id=self.show.media_id,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=3,
        )
        self.tick([3], at(24, 20))
        episode_log = self.log(episode, at(24, 20))

        logs = {
            activity["object"]["id"]
            for activity in self.activity()
            if activity["type"] == "diary_created"
        }
        summaries = {
            activity["object"]["summary"]
            for activity in self.activity()
            if activity["type"] == "progress_updated"
        }

        self.assertEqual(logs, {book_log.id, episode_log.id})
        self.assertEqual(summaries, {"read 20%", "watched 1 episode"})

    def test_each_kind_of_progress_reads_naturally(self):
        manga, comic = self.item(MediaTypes.MANGA.value), self.item(MediaTypes.COMIC.value)
        pages, percent = self.item(MediaTypes.BOOK.value, "pages"), self.item(MediaTypes.BOOK.value, "percent")
        time, percentage = self.item(MediaTypes.GAME.value, "time"), self.item(MediaTypes.GAME.value, "percent")
        decimal = self.item(MediaTypes.GAME.value, "decimal")
        for item, function in (
            (manga, series_tracking.start),
            (comic, series_tracking.start),
            (pages, book_tracking.start),
            (percent, book_tracking.start),
            (time, game_tracking.start),
            (percentage, game_tracking.start),
            (decimal, game_tracking.start),
        ):
            self.save(function, item, at(23))
        self.save(series_tracking.update_progress, manga, at(24, 1), value=12)
        self.save(series_tracking.update_progress, comic, at(24, 2), value=1)
        self.save(book_tracking.update_progress, pages, at(24, 3), progress_type="pages", value=40)
        self.save(book_tracking.update_progress, percent, at(24, 4), progress_type="percentage", value=15)
        self.save(game_tracking.update_progress, time, at(24, 5), total_minutes=150)
        self.save(game_tracking.update_progress, percentage, at(24, 6), percentage=Decimal(15))
        self.save(game_tracking.update_progress, decimal, at(24, 7), percentage=Decimal("2.25"))

        summaries = {
            activity["media"]["title"]: activity["object"]["summary"]
            for activity in self.activity()
            if activity["consumed_at"] == "2026-10-24"
        }

        self.assertEqual(
            summaries,
            {
                "manga one": "read 12 chapters",
                "comic one": "read 1 issue",
                "book pages": "read 40 pages",
                "book percent": "read 15%",
                "game time": "played 2h 30m",
                "game percent": "progressed 15%",
                "game decimal": "progressed 2.25%",
            },
        )

    def test_a_game_played_and_moved_forward_reads_as_time_played(self):
        game = self.item(MediaTypes.GAME.value)
        self.save(game_tracking.start, game, at(23))
        self.save(game_tracking.update_progress, game, at(24, 9), total_minutes=45)
        self.save(game_tracking.update_progress, game, at(24, 10), percentage=Decimal(20))

        [activity] = [activity for activity in self.activity() if activity["consumed_at"] == "2026-10-24"]

        self.assertEqual(activity["object"]["summary"], "played 45m")

    def test_logs_and_progress_share_one_date_order(self):
        self.tick([1, 2], at(24, 18))
        older_log = self.log(self.item(MediaTypes.MOVIE.value), single_weight.calendar_datetime(date(2026, 10, 23)))
        # A log for the same date, written after the day's progress, comes first.
        same_day_log = self.log(self.item(MediaTypes.MUSIC.value), single_weight.calendar_datetime(date(2026, 10, 24)))
        DiaryEntry.objects.filter(pk=same_day_log.pk).update(created_at=at(24, 19))
        DiaryEntry.objects.filter(pk=older_log.pk).update(created_at=at(25))

        self.assertEqual(
            self.summaries(self.activity()),
            [
                ("2026-10-24", "music one", "log"),
                ("2026-10-24", "Severance", "watched 2 episodes"),
                ("2026-10-23", "movie one", "log"),
            ],
        )

    def test_pages_cover_logs_and_progress_without_gaps(self):
        day = single_weight.calendar_datetime(date(2026, 10, 24))
        expected = []
        for number in range(3):
            show = show_with_episodes(f"Show {number}", seasons={1: 3})
            self.tick([1], at(24, 9), show=show)
            expected.append(
                -(self.owner.pk << 44 | show.pk << 20 | date(2026, 10, 24).toordinal()),
            )
            entry = self.log(self.item(MediaTypes.MUSIC.value, f"album-{number}"), day)
            DiaryEntry.objects.filter(pk=entry.pk).update(created_at=at(24, 9))
            expected.append(entry.id)
        self.client.force_authenticate(self.owner)

        seen = []
        url = "/api/v1/users/watcher/activity/?page_size=2"
        while url:
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertLessEqual(len(response.data["results"]), 2)
            seen += [activity["id"] for activity in response.data["results"]]
            url = response.data["next_cursor"]

        self.assertEqual(sorted(seen), sorted(expected))
        self.assertEqual(len(seen), len(set(seen)))
        # Logs first on one date and write time, then each kind by its newest write.
        self.assertEqual([i > 0 for i in seen], [True] * 3 + [False] * 3)

    def test_a_bad_cursor_is_not_found(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get("/api/v1/users/watcher/activity/", {"cursor": "not-a-cursor"})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_progress_follows_profile_privacy_and_reaches_followers_feeds(self):
        self.tick([1, 2], at(24))
        manga = self.item(MediaTypes.MANGA.value)
        self.save(series_tracking.start, manga, at(24))
        self.save(series_tracking.update_progress, manga, at(24, 13), value=3)
        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])

        self.assertEqual(self.activity(viewer=self.viewer), [])

        Follow.objects.create(from_user=self.viewer, to_user=self.owner, status=FollowStatus.ACCEPTED)
        self.client.force_authenticate(self.viewer)
        feed = self.client.get("/api/v1/feed/")
        tv_feed = self.client.get("/api/v1/feed/", {"media_type": "tv"})

        self.assertEqual(len(self.activity(viewer=self.viewer)), 2)
        self.assertEqual(len(feed.data["results"]), 2)
        self.assertEqual([activity["media"]["title"] for activity in tv_feed.data["results"]], ["Severance"])


class ProgressHistoryTests(TestCase):
    """The progress that daily activity reads is recorded where it was missing."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="recorder", password="strong-password-123")

    def test_game_playtime_and_percentage_are_recorded(self):
        game = Item.objects.create(source=Sources.MANUAL.value, media_type=MediaTypes.GAME.value, media_id="game", title="Game")
        game_tracking.start(self.user, game)

        game_tracking.update_progress(self.user, game, total_minutes=90)
        game_tracking.update_progress(self.user, game, total_minutes=150, percentage=Decimal(20))
        game_tracking.update_progress(self.user, game, total_minutes=150)

        self.assertEqual(
            [
                (change.current_progress["unit"], change.previous_progress["value"], change.current_progress["value"])
                for change in ProgressChange.objects.filter(actor=self.user)
                .exclude(current_progress__kind=ProgressChange.START)
                .order_by("id")
            ],
            # The same 150 sent again is a retype: one touch row (plan 5.3).
            [("minute", 0, 90), ("minute", 90, 150), ("percent", 0, 20), ("touch", 0, 0)],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_the_first_episode_of_a_new_season_counts(self, metadata_mock):
        # The metadata fake also answers the show request TV's stored episode lists
        # need (TV-103); the ProgressChange assertion is unchanged.
        show = Item.objects.create(source=Sources.TMDB.value, media_type=MediaTypes.TV.value, media_id="77", title="Show")
        season = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            media_id="77",
            season_number=2,
            title="Show",
        )
        TV.objects.create(user=self.user, item=show, status=Status.IN_PROGRESS.value)
        episodes = {
            "episodes": [
                {"episode_number": 1, "air_date": "2020-01-01"},
                {"episode_number": 2, "air_date": "2020-01-08"},
            ],
        }
        metadata_mock.side_effect = lambda media_type, *_args: {
            "tv": {"related": {"seasons": [{"season_number": 2, "first_air_date": "2020-01-01"}]}},
            "season": {"title": "Show", "image": "", "max_progress": 2, **episodes},
            "tv_with_seasons": {"season/2": episodes},
        }[media_type]

        tracking_service.watch_episode(self.user, source="tmdb", media_id="77", season_number=2, episode_number=1)

        change = ProgressChange.objects.exclude(current_progress__kind=ProgressChange.START).get(
            actor=self.user,
            item=season,
        )
        self.assertEqual((change.previous_progress["value"], change.current_progress["value"]), (0, 1))


class StartedActivityTests(TestCase):
    """Starting a title is activity until that day brings progress or a log."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="starter", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def item(self, media_type, media_id="one", source=Sources.MANUAL.value):
        return Item.objects.create(source=source, media_type=media_type, media_id=media_id, title=f"{media_type} {media_id}")

    def activity(self):
        response = self.client.get("/api/v1/users/starter/activity/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [(activity["media"]["title"], activity["object"].get("summary", "log")) for activity in response.data["results"]]

    def starts(self, item):
        return ProgressChange.objects.filter(actor=self.user, item=item, current_progress__kind=ProgressChange.START).count()

    def test_starting_a_book_or_game_is_activity_but_resuming_is_not(self):
        book = self.item(MediaTypes.BOOK.value)
        game = self.item(MediaTypes.GAME.value)

        book_tracking.start(self.user, book)
        game_tracking.start(self.user, game)
        book_tracking.pause(self.user, book)
        book_tracking.resume(self.user, book)
        game_tracking.pause(self.user, game)
        game_tracking.resume(self.user, game)

        self.assertEqual(
            sorted(self.activity()),
            [("book one", "started reading"), ("game one", "started playing")],
        )
        self.assertEqual((self.starts(book), self.starts(game)), (1, 1))
        self.assertEqual(self.client.get("/api/v1/users/starter/activity/").data["results"][0]["object"]["label"], "Started")

    def test_starting_again_records_a_new_start(self):
        book = self.item(MediaTypes.BOOK.value)
        book_tracking.start(self.user, book)

        book_tracking.restart(self.user, book)

        self.assertEqual(self.starts(book), 2)
        self.assertEqual(self.activity(), [("book one", "started reading")])

    def test_shows_anime_and_manga_start_when_set_in_progress_not_when_resumed(self):
        anime_item = self.item(MediaTypes.ANIME.value, source=Sources.MAL.value)
        anime = Anime.objects.create(user=self.user, item=anime_item, status=Status.PLANNING.value)

        with FakeAnimeProvider() as fake:
            fake.add("one", total=12)
            tracking_service.set_status(self.user, source="mal", media_type="anime", media_id="one", status=Status.IN_PROGRESS.value)
            tracking_service.set_status(self.user, source="mal", media_type="anime", media_id="one", status=Status.PAUSED.value)
            tracking_service.set_status(self.user, source="mal", media_type="anime", media_id="one", status=Status.IN_PROGRESS.value)

        anime.refresh_from_db()
        self.assertEqual(anime.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.starts(anime_item), 1)
        self.assertEqual(self.activity(), [("anime one", "started watching")])

    def test_a_days_progress_replaces_its_start_but_a_log_doesnt(self):
        book = self.item(MediaTypes.BOOK.value, "progressed")
        game = self.item(MediaTypes.GAME.value, "logged")
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="percentage", value=Decimal(10))
        game_tracking.start(self.user, game)
        DiaryEntry.objects.create(user=self.user, item=game, consumed_at=single_weight.calendar_datetime(timezone.localdate()))

        self.assertEqual(
            sorted(self.activity()),
            [("book progressed", "read 10%"), ("game logged", "log"), ("game logged", "started playing")],
        )

    def test_a_shows_start_and_its_progress_are_one_item(self):
        show = show_with_episodes("tv 77", seasons={1: 8})
        tick(self.user, show, [1, 2], datetime(2026, 10, 24, 12, tzinfo=UTC))

        self.assertEqual(self.activity(), [("tv 77", "watched 2 episodes")])

    def test_deleting_a_playthrough_removes_its_start_and_progress(self):
        game = self.item(MediaTypes.GAME.value)
        game_tracking.start(self.user, game)
        game_tracking.update_progress(self.user, game, total_minutes=60)
        playthrough = game_tracking._locked_game(self.user, game).current_session

        game_tracking.delete_playthrough(self.user, game, playthrough.pk)

        self.assertFalse(ProgressChange.objects.filter(actor=self.user, item=game).exists())
        self.assertEqual(self.activity(), [])

    def test_a_start_is_not_a_titles_latest_progress(self):
        book = self.item(MediaTypes.BOOK.value)
        tracked = book_tracking.start(self.user, book)

        self.assertIsNone(latest_progress_change_for(tracked))


class InProgressOrderTests(TestCase):
    """A listed title's updated_at is when it last moved: Home's In progress order."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reader", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def started(self, media_type, start, when):
        item = Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=f"{media_type}-1",
            title=media_type,
        )
        with patch("django.utils.timezone.now", return_value=when):
            start(self.user, item)
        # progressed_at's default reads the real clock.
        apps.get_model("app", media_type).objects.filter(item=item).update(progressed_at=when)
        return item

    def updated_at(self, media_type):
        response = self.client.get("/api/v1/tracking/", {"media_type": media_type, "status": "In progress"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [row] = response.data["results"]
        return row["tracking"]["updated_at"]

    def test_a_progress_update_moves_the_title(self):
        book = self.started(MediaTypes.BOOK.value, book_tracking.start, at(20))
        game = self.started(MediaTypes.GAME.value, game_tracking.start, at(20))

        with patch("django.utils.timezone.now", return_value=at(24)):
            book_tracking.update_progress(self.user, book, progress_type="pages", value=40)
            game_tracking.update_progress(self.user, game, total_minutes=90)

        self.assertEqual(self.updated_at(MediaTypes.BOOK.value), at(24))
        self.assertEqual(self.updated_at(MediaTypes.GAME.value), at(24))
