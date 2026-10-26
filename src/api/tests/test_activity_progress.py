"""A day's progress on a title is one activity item (docs/activity-contract.md)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import latest_progress_change_for
from api.services import tracking as tracking_service
from app import book_tracking, game_tracking, single_weight
from app.models import TV, Anime, DiaryEntry, Item, MediaTypes, Sources, Status
from social.models import Follow, FollowStatus, ProgressChange


def at(day, hour=12):
    """Return a UTC time in October 2026."""
    return datetime(2026, 10, day, hour, tzinfo=UTC)


class DailyProgressTests(TestCase):
    """Progress rolls up per title and day; logs and progress share one order."""

    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(username="watcher", password="strong-password-123")
        self.viewer = User.objects.create_user(username="friend", password="strong-password-123")
        self.client = APIClient()
        self.show = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            media_id="1396",
            title="Severance",
        )
        self.seasons = {
            number: Item.objects.create(
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                media_id="1396",
                season_number=number,
                title="Severance",
            )
            for number in (1, 2)
        }

    def item(self, media_type, media_id="one", **fields):
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=f"{media_type}-{media_id}",
            title=f"{media_type} {media_id}",
            **fields,
        )

    def change(self, item, previous, current, when, *, unit="episode", user=None):
        change = ProgressChange.objects.create(
            actor=user or self.owner,
            item=item,
            previous_progress={"kind": "count", "value": previous, "max": None, "unit": unit},
            current_progress={"kind": "count", "value": current, "max": None, "unit": unit},
        )
        ProgressChange.objects.filter(pk=change.pk).update(created_at=when)
        return change

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
        first = self.change(self.seasons[1], 2, 4, at(24, 9))
        self.change(self.seasons[1], 4, 5, at(24, 10))
        self.change(self.seasons[2], 0, 1, at(24, 21))

        [activity] = self.activity()

        self.assertEqual(activity["type"], "progress_updated")
        self.assertEqual(activity["id"], -first.id)
        self.assertEqual(activity["consumed_at"], "2026-10-24")
        self.assertEqual(activity["created_at"], at(24, 21))
        self.assertEqual(activity["media"]["ref"]["media_type"], "tv")
        self.assertEqual(activity["media"]["title"], "Severance")
        self.assertEqual(
            activity["object"],
            {"type": "progress_day", "id": first.id, "summary": "watched 4 episodes", "label": "4 episodes"},
        )

    def test_each_day_is_its_own_item_newest_day_first(self):
        self.change(self.seasons[1], 0, 1, at(23))
        self.change(self.seasons[1], 1, 3, at(24))

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
        self.change(self.seasons[1], 0, 1, datetime(2026, 10, 25, 2, tzinfo=UTC))

        [activity] = self.activity()

        self.assertEqual(activity["consumed_at"], "2026-10-24")

    def test_going_backwards_shows_nothing(self):
        self.change(self.seasons[1], 5, 4, at(24))
        self.change(self.seasons[2], 3, 5, at(25, 9))
        self.change(self.seasons[2], 5, 3, at(25, 10))

        self.assertEqual(self.activity(), [])

    def test_logging_the_title_that_day_replaces_its_progress(self):
        book = self.item(MediaTypes.BOOK.value)
        self.change(book, 10, 30, at(24), unit="percent")
        book_log = self.log(book, single_weight.calendar_datetime(date(2026, 10, 24)))
        episode = Item.objects.create(
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            media_id="1396",
            season_number=1,
            episode_number=3,
            title="Severance",
        )
        self.change(self.seasons[1], 2, 3, at(24, 20))
        episode_log = self.log(episode, at(24, 20))
        self.change(self.seasons[1], 3, 4, at(25))

        results = self.activity()

        self.assertEqual(
            [(activity["type"], activity["object"]["id"]) for activity in results[1:]],
            [("diary_created", episode_log.id), ("diary_created", book_log.id)],
        )
        self.assertEqual(results[0]["object"]["summary"], "watched 1 episode")

    def test_each_kind_of_progress_reads_naturally(self):
        cases = [
            (self.item(MediaTypes.ANIME.value), 3, 5, "episode", "watched 2 episodes"),
            (self.item(MediaTypes.MANGA.value), 10, 22, "chapter", "read 12 chapters"),
            (self.item(MediaTypes.COMIC.value), 4, 5, "issue", "read 1 issue"),
            (self.item(MediaTypes.BOOK.value, "pages"), 10, 50, "page", "read 40 pages"),
            (self.item(MediaTypes.BOOK.value, "percent"), 12.5, 27.5, "percent", "read 15%"),
            (self.item(MediaTypes.GAME.value, "time"), 30, 180, "minute", "played 2h 30m"),
            (self.item(MediaTypes.GAME.value, "percent"), 40, 55, "percent", "progressed 15%"),
        ]
        for hour, (item, previous, current, unit, _) in enumerate(cases):
            self.change(item, previous, current, at(24, hour), unit=unit)

        summaries = {activity["media"]["title"]: activity["object"]["summary"] for activity in self.activity()}

        self.assertEqual(summaries, {item.title: summary for item, *_, summary in cases})

    def test_a_game_played_and_moved_forward_reads_as_time_played(self):
        game = self.item(MediaTypes.GAME.value)
        self.change(game, 0, 45, at(24, 9), unit="minute")
        self.change(game, 10, 20, at(24, 10), unit="percent")

        [activity] = self.activity()

        self.assertEqual(activity["object"]["summary"], "played 45m")

    def test_logs_and_progress_share_one_date_order(self):
        self.change(self.seasons[1], 0, 2, at(24, 18))
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
            show = Item.objects.create(source=Sources.TMDB.value, media_type=MediaTypes.SEASON.value, media_id=f"show-{number}", season_number=1, title=f"Show {number}")
            Item.objects.create(source=Sources.TMDB.value, media_type=MediaTypes.TV.value, media_id=f"show-{number}", title=f"Show {number}")
            expected.append(-self.change(show, 0, 1, at(24, 9)).id)
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
        # Same date and write time: logs first, then newest id first within each kind.
        logs, days = sorted([i for i in expected if i > 0], reverse=True), sorted([i for i in expected if i < 0])
        self.assertEqual(seen, logs + days)

    def test_a_bad_cursor_is_not_found(self):
        self.client.force_authenticate(self.owner)

        response = self.client.get("/api/v1/users/watcher/activity/", {"cursor": "not-a-cursor"})

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_progress_follows_profile_privacy_and_reaches_followers_feeds(self):
        self.change(self.seasons[1], 0, 2, at(24))
        self.change(self.item(MediaTypes.MANGA.value), 1, 3, at(24), unit="chapter")
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
            [("minute", 0, 90), ("minute", 90, 150), ("percent", 0, 20)],
        )

    @patch("app.providers.services.get_media_metadata")
    def test_the_first_episode_of_a_new_season_counts(self, metadata_mock):
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

        tracking_service.set_status(self.user, source="mal", media_type="anime", media_id="one", status=Status.IN_PROGRESS.value)
        tracking_service.set_status(self.user, source="mal", media_type="anime", media_id="one", status=Status.PAUSED.value)
        tracking_service.set_status(self.user, source="mal", media_type="anime", media_id="one", status=Status.IN_PROGRESS.value)

        anime.refresh_from_db()
        self.assertEqual(anime.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.starts(anime_item), 1)
        self.assertEqual(self.activity(), [("anime one", "started watching")])

    def test_a_days_progress_or_log_replaces_its_start(self):
        book = self.item(MediaTypes.BOOK.value, "progressed")
        game = self.item(MediaTypes.GAME.value, "logged")
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="percentage", value=Decimal(10))
        game_tracking.start(self.user, game)
        DiaryEntry.objects.create(user=self.user, item=game, consumed_at=single_weight.calendar_datetime(timezone.localdate()))

        self.assertEqual(
            sorted(self.activity()),
            [("book progressed", "read 10%"), ("game logged", "log")],
        )

    def test_a_shows_start_and_its_seasons_progress_are_one_item(self):
        show = self.item(MediaTypes.TV.value, "77", source=Sources.TMDB.value)
        season = Item.objects.create(source=Sources.TMDB.value, media_type=MediaTypes.SEASON.value, media_id="77", season_number=1, title="tv 77")
        ProgressChange.record_start(self.user, show)
        ProgressChange.objects.create(
            actor=self.user,
            item=season,
            previous_progress={"kind": "episodes", "value": 0, "max": 8, "unit": "episode"},
            current_progress={"kind": "episodes", "value": 2, "max": 8, "unit": "episode"},
        )

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
