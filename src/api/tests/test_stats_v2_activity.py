"""Stats v2 header, time, activity and milestones (docs/stats-contract.md).

STAT-200 to STAT-203 and STAT-216, with the counting rules they read: rewatches
(STAT-108), reads and completions (STAT-107), titles (STAT-105) and events (STAT-106).
Date cases run with the server on America/New_York and the owner's calendar on New
York, Los Angeles and Auckland.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from api.services import stats_v2_activity as activity
from api.services.stats_scope import build_scope
from api.tests.test_stats_v2_shape import (
    ALL_TIME,
    AUCKLAND,
    LOS_ANGELES,
    NEW_YORK,
    OWNER_ZONES,
    YEAR_2024,
    YEAR_2025,
    StatsV2Base,
    clear_library,
)
from app.models import (
    TV,
    Anime,
    Book,
    BookSession,
    DiaryEntry,
    Episode,
    Game,
    GameSession,
    Manga,
    MediaLike,
    MediaTypes,
    Movie,
    Season,
    Status,
)
from social.models import ProgressChange

YEAR_2026 = {"schema": "2", "start_date": "2026-01-01", "end_date": "2026-12-31"}


def utc_day(year, month, day):
    """Return a calendar day stored as a UTC-midnight carrier (STAT-100)."""
    return datetime(year, month, day, tzinfo=UTC)


def details(data):
    """Return the header details by key."""
    return {row["key"]: row for row in data["header"]["details"]}


def detail_value(data, key):
    """Return one header detail's value."""
    return details(data)[key]["value"]


def time_row(data, media_type):
    """Return one type's time row, or None."""
    return next((row for row in data["time"]["rows"] if row["media_type"] == media_type), None)


class ActivityBase(StatsV2Base):
    """Helpers for tracking rows, sessions and history the sections read."""

    def summary(self, params, *, viewer=None, tab=None, zone=None):
        # Tests change rows with .update(), which no cache signal sees.
        cache.clear()
        viewer = viewer or self.owner
        query = {**params, **({"media_type": tab} if tab else {})}
        path = "/api/v1/stats/me/summary/" if viewer == self.owner else self.user_path()
        response = self.get(viewer, query, path=path, zone=zone)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def set_item(self, item, **fields):
        """Store Item fields without Item.save side effects."""
        type(item).objects.filter(pk=item.pk).update(**fields)
        item.refresh_from_db()

    def set_zone(self, zone):
        self.owner.calendar_timezone = zone
        self.owner.save(update_fields=["calendar_timezone"])

    def tracking(self, model, item, **fields):
        fields.setdefault("status", Status.COMPLETED.value)
        row = model(user=self.owner, item=item, **fields)
        model.objects.bulk_create([row])
        return row

    def history(self, row, progress, when, kind="~"):
        """Write one history row of a tracking row at a chosen time."""
        type(row).history.model.objects.create(
            id=row.id,
            progress=progress,
            status=row.status,
            notes="",
            history_date=when,
            history_type=kind,
        )

    def session(self, game, **fields):
        fields.setdefault("status", Status.COMPLETED.value)
        fields.setdefault("origin", GameSession.Origin.DIRECT_LOG)
        session = GameSession(related_game=game, **fields)
        GameSession.objects.bulk_create([session])
        return session

    def tv_show(self, media_id="show", seasons=1):
        show = self.item(MediaTypes.TV.value, media_id)
        season_items = [self.item(MediaTypes.SEASON.value, media_id, season=number) for number in range(1, seasons + 1)]
        tv, season = self.show_rows(show, season_items[0])
        return show, season_items[0], tv, season

    def stamp(self, row, created_at):
        type(row).objects.filter(pk=row.pk).update(created_at=created_at)


class HeaderTests(ActivityBase):
    """The four big numbers and the details list (STAT-200)."""

    def test_details_come_in_order_with_labels_and_units(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.log(film, utc_day(2025, 3, 2))
        past = self.summary(YEAR_2025)
        self.assertEqual(
            [(row["key"], row["label"], row["unit"]) for row in past["header"]["details"]],
            [
                ("longest_weekly_streak", "Longest weekly streak", "week"),
                ("rewatches", "Repeats", None),
                ("reviews", "Reviews", None),
                ("likes", "Likes", None),
                ("days_with_two_plus_titles", "Days with 2+ titles", None),
            ],
        )
        for row in past["header"]["details"]:
            self.assertEqual(set(row), {"key", "label", "value", "unit", "approximate"})
            self.assertIsInstance(row["value"], int)
            self.assertFalse(row["approximate"])
        current = self.summary(YEAR_2026)
        self.assertEqual(
            [row["key"] for row in current["header"]["details"]][:3],
            ["current_daily_streak", "longest_weekly_streak", "current_weekly_streak"],
        )
        self.assertEqual(details(current)["current_daily_streak"]["unit"], "day")

    def test_rewatch_labels_follow_the_tab(self):
        labels = {
            "movie": "Rewatches",
            "tv": "Rewatches",
            "anime": "Rewatches",
            "book": "Rereads",
            "manga": "Rereads",
            "comic": "Rereads",
            "game": "Replays",
            "music": "Relistens",
        }
        for tab, label in labels.items():
            with self.subTest(tab=tab):
                self.assertEqual(details(self.summary(YEAR_2025, tab=tab))["rewatches"]["label"], label)

    def test_four_big_numbers(self):
        # S-03: a show, its season and an episode logged in 2025 are 1 title and 3 logs.
        show, season_item, _tv, _season = self.tv_show()
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        self.log(show, utc_day(2025, 3, 2))
        self.log(season_item, utc_day(2025, 3, 3))
        self.log(episode, utc_day(2025, 3, 5))
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.log(film, utc_day(2025, 3, 3))
        header = self.summary(YEAR_2025)["header"]
        self.assertEqual(
            (header["titles"], header["logs"], header["active_days"], header["longest_daily_streak"]),
            (2, 4, 3, 2),
        )
        tv_header = self.summary(YEAR_2025, tab="tv")["header"]
        self.assertEqual((tv_header["titles"], tv_header["logs"]), (1, 3))

    def test_reviews_likes_and_days_with_two_plus_titles(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        other = self.item(MediaTypes.MOVIE.value, "other")
        liked_only = self.item(MediaTypes.MOVIE.value, "liked-only")
        show, season_item, _tv, season = self.tv_show()
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        first = self.log(film, utc_day(2025, 3, 2))
        first.review = "Great"
        first.liked = True
        first.save()
        titled = self.log(other, utc_day(2025, 3, 2))
        titled.review_title = "A title"
        titled.save()
        # Two episodes of one show the same day are one title that day (STAT-106).
        self.viewing(season, episode, watched_on=date(2025, 3, 9))
        self.viewing(season, self.item(MediaTypes.EPISODE.value, "show", season=1, episode=2), watched_on=date(2025, 3, 9))
        self.log(other, utc_day(2025, 3, 9))
        MediaLike.objects.create(user=self.owner, item=liked_only)
        # A like on the season counts as its show (STAT-102).
        MediaLike.objects.create(user=self.owner, item=season_item)
        MediaLike.objects.create(user=self.owner, item=show)

        year = self.summary(YEAR_2025)
        self.assertEqual(detail_value(year, "reviews"), 2)
        self.assertEqual(detail_value(year, "likes"), 1)
        self.assertEqual(detail_value(year, "days_with_two_plus_titles"), 2)
        all_time = self.summary(ALL_TIME)
        # The liked log's film, the unlogged like, and the show once.
        self.assertEqual(detail_value(all_time, "likes"), 3)

    def test_hidden_logs_count_for_nothing_in_the_header(self):
        _show, _season_item, _tv, season = self.tv_show()
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        private = self.log(episode, utc_day(2025, 3, 2), visibility="private")
        private.review = "secret"
        private.save()
        self.viewing(season, episode, watched_on=date(2025, 3, 2), kind="log", entry=private)
        followers = self.log(episode, utc_day(2025, 3, 4), visibility="followers")
        self.viewing(season, episode, watched_on=date(2025, 3, 4), kind="log", entry=followers)

        owner = self.summary(YEAR_2025, tab="tv")
        follower = self.summary(YEAR_2025, viewer=self.follower, tab="tv")
        stranger = self.summary(YEAR_2025, viewer=self.stranger, tab="tv")
        self.assertEqual((owner["header"]["logs"], detail_value(owner, "episodes")), (2, 2))
        self.assertEqual(detail_value(owner, "reviews"), 1)
        self.assertEqual((follower["header"]["logs"], detail_value(follower, "episodes")), (1, 1))
        self.assertEqual(detail_value(follower, "reviews"), 0)
        self.assertEqual((stranger["header"]["logs"], stranger["header"]["active_days"]), (0, 0))
        self.assertEqual(detail_value(stranger, "episodes"), 0)
        # The owner's second log follows an earlier one the follower can't see.
        self.assertEqual(detail_value(owner, "rewatches"), 1)
        self.assertEqual(detail_value(follower, "rewatches"), 0)

    def test_tv_details_finish_counts_only_in_all_time(self):
        _show, _season_item, tv, season = self.tv_show()
        Season.objects.filter(pk=season.pk).update(status=Status.COMPLETED.value)
        TV.objects.filter(pk=tv.pk).update(finished=True, status=Status.PAUSED.value)
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        self.viewing(season, episode, watched_on=date(2025, 3, 2))
        self.viewing(season, episode, kind="fill")

        all_time = details(self.summary(ALL_TIME, tab="tv"))
        self.assertEqual(
            list(all_time)[-3:],
            ["episodes", "seasons_finished", "shows_finished"],
        )
        self.assertEqual(
            (all_time["episodes"]["value"], all_time["seasons_finished"]["value"], all_time["shows_finished"]["value"]),
            (2, 1, 1),
        )
        year = details(self.summary(YEAR_2025, tab="tv"))
        self.assertEqual(year["episodes"]["value"], 1)
        self.assertNotIn("seasons_finished", year)
        self.assertNotIn("shows_finished", year)
        self.assertNotIn("episodes", details(self.summary(YEAR_2025)))

    def test_anime_episodes_are_approximate_in_a_range(self):
        series = self.item(MediaTypes.ANIME.value, "series")
        row = self.tracking(Anime, series, progress=12, status=Status.IN_PROGRESS.value)
        self.history(row, 0, datetime(2025, 1, 5, 12, tzinfo=UTC), kind="+")
        self.history(row, 12, datetime(2025, 2, 5, 12, tzinfo=UTC))
        year = details(self.summary(YEAR_2025, tab="anime"))["episodes"]
        self.assertEqual((year["label"], year["value"], year["approximate"]), ("Episodes watched", 12, True))
        all_time = details(self.summary(ALL_TIME, tab="anime"))["episodes"]
        self.assertEqual((all_time["value"], all_time["approximate"]), (12, False))


class ReadsAndCompletionsTests(ActivityBase):
    """Reads and completions add the undated fact in All Time (STAT-107)."""

    def test_book_reads_and_did_not_finish(self):
        # S-05: a book marked read with one dated log.
        novel = self.item(MediaTypes.BOOK.value, "novel")
        book = self.tracking(Book, novel, completed_manually=True)
        self.log(novel, utc_day(2025, 3, 2))
        dropped = self.item(MediaTypes.BOOK.value, "dropped")
        dropped_book = self.tracking(Book, dropped, status=Status.DROPPED.value)
        BookSession.objects.bulk_create(
            [
                BookSession(related_book=dropped_book, status=Status.DROPPED.value, end_date=datetime(2025, 6, 1, 15, tzinfo=UTC)),
                BookSession(related_book=dropped_book, status=Status.DROPPED.value, end_date=None),
                BookSession(related_book=book, status=Status.COMPLETED.value, end_date=datetime(2025, 3, 2, 15, tzinfo=UTC)),
            ],
        )
        all_time = details(self.summary(ALL_TIME, tab="book"))
        self.assertEqual((all_time["reads"]["value"], all_time["did_not_finish"]["value"]), (2, 2))
        self.assertEqual(all_time["reads"]["label"], "Reads")
        year = details(self.summary(YEAR_2025, tab="book"))
        self.assertEqual((year["reads"]["value"], year["did_not_finish"]["value"]), (1, 1))
        # The dropped book has no log and no manual completion: not a title.
        self.assertEqual(self.summary(ALL_TIME, tab="book")["header"]["titles"], 1)

    def test_did_not_finish_on_new_years_day_in_every_owner_zone(self):
        # End dates are UTC-midnight carriers: 1 January stays in 2025 everywhere.
        dropped = self.item(MediaTypes.BOOK.value, "dropped")
        dropped_book = self.tracking(Book, dropped, status=Status.DROPPED.value)
        BookSession.objects.bulk_create(
            [BookSession(related_book=dropped_book, status=Status.DROPPED.value, end_date=utc_day(2025, 1, 1))],
        )
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                self.set_zone(zone)
                year = detail_value(self.summary(YEAR_2025, tab="book"), "did_not_finish")
                before = detail_value(self.summary(YEAR_2024, tab="book"), "did_not_finish")
                self.assertEqual((year, before), (1, 0))

    def test_game_completions_and_abandoned(self):
        title = self.item(MediaTypes.GAME.value, "title")
        game = self.tracking(Game, title, completed_manually=True)
        self.log(title, utc_day(2025, 3, 2))
        self.log(title, utc_day(2024, 3, 2))
        self.session(game, status=Status.DROPPED.value, origin=GameSession.Origin.LIVE, start_date=date(2025, 1, 1), end_date=date(2025, 2, 1))
        self.session(game, status=Status.DROPPED.value, origin=GameSession.Origin.LIVE, start_date=date(2023, 1, 1), end_date=date(2023, 2, 1))
        all_time = details(self.summary(ALL_TIME, tab="game"))
        self.assertEqual((all_time["completions"]["value"], all_time["abandoned"]["value"]), (3, 2))
        year = details(self.summary(YEAR_2025, tab="game"))
        self.assertEqual((year["completions"]["value"], year["abandoned"]["value"]), (1, 1))


class RewatchTests(ActivityBase):
    """A rewatch is worked out from history (STAT-108)."""

    def rewatches(self, params=ALL_TIME, *, viewer=None, tab=None):
        return detail_value(self.summary(params, viewer=viewer, tab=tab), "rewatches")

    def test_an_earlier_log_of_the_same_target(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        first = self.log(film, utc_day(2024, 3, 2))
        second = self.log(film, utc_day(2025, 3, 2))
        # Saved toggles are never read for rule 1.
        DiaryEntry.objects.filter(pk=first.pk).update(is_rewatch=True)
        DiaryEntry.objects.filter(pk=second.pk).update(is_rewatch=False)
        self.assertEqual(self.rewatches(), 1)
        self.assertEqual(self.rewatches(YEAR_2025), 1)
        self.assertEqual(self.rewatches(YEAR_2024), 0)

    def test_same_day_logs_order_by_id(self):
        series = self.item(MediaTypes.ANIME.value, "series")
        self.log(series, utc_day(2025, 3, 2))
        self.log(series, utc_day(2025, 3, 2))
        self.assertEqual(self.rewatches(tab="anime"), 1)

    def test_anime_toggle_alone_is_not_a_rewatch(self):
        series = self.item(MediaTypes.ANIME.value, "series")
        entry = self.log(series, utc_day(2025, 3, 2))
        DiaryEntry.objects.filter(pk=entry.pk).update(is_rewatch=True)
        self.tracking(Anime, series)
        self.assertEqual(self.rewatches(), 0)

    def test_letterboxd_import_marker_needs_the_saved_rewatch(self):
        # S-06: watched.csv sets the marker after the diary row; not a rewatch.
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.tracking(Movie, film, direct_consumption=True)
        entry = self.log(film, utc_day(2025, 3, 2))
        self.assertEqual(self.rewatches(), 0)
        DiaryEntry.objects.filter(pk=entry.pk).update(is_rewatch=True)
        self.assertEqual(self.rewatches(), 1)

    def test_book_completed_manually_makes_the_first_log_a_reread(self):
        # S-08: the reread toggle is off and never read.
        novel = self.item(MediaTypes.BOOK.value, "novel")
        self.tracking(Book, novel, completed_manually=True)
        self.log(novel, utc_day(2025, 3, 2))
        self.assertEqual(self.rewatches(tab="book"), 1)
        title = self.item(MediaTypes.GAME.value, "title")
        self.tracking(Game, title, completed_manually=True)
        self.log(title, utc_day(2025, 3, 2))
        self.assertEqual(self.rewatches(tab="game"), 1)
        self.assertEqual(self.rewatches(), 2)

    def test_episode_logs_count_by_episode_whatever_the_toggle(self):
        # S-07: S1 E1, S1 E2, then S1 E1 again.
        _show, _season_item, _tv, season = self.tv_show()
        first_ep = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        second_ep = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=2)
        for day, episode in ((2, first_ep), (3, second_ep), (4, first_ep)):
            entry = self.log(episode, utc_day(2025, 3, day))
            self.viewing(season, episode, watched_on=date(2025, 3, day), kind="log", entry=entry)
        self.assertEqual(self.rewatches(tab="tv"), 1)
        DiaryEntry.objects.update(is_rewatch=True)
        self.assertEqual(self.rewatches(tab="tv"), 1)

    def test_an_episode_log_after_an_unclaimed_tick_is_a_rewatch(self):
        _show, _season_item, _tv, season = self.tv_show()
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        tick = self.viewing(season, episode, watched_on=date(2025, 3, 1))
        self.stamp(tick, datetime(2025, 3, 1, 12, tzinfo=UTC))
        entry = self.log(episode, utc_day(2025, 3, 2))
        own = self.viewing(season, episode, watched_on=date(2025, 3, 2), kind="log", entry=entry)
        self.stamp(own, datetime(2025, 3, 2, 12, tzinfo=UTC))
        self.assertEqual(self.rewatches(tab="tv"), 1)
        # A claimed tick is the log's own viewing: nothing earlier, no rewatch.
        own.delete()
        Episode.objects.filter(pk=tick.pk).update(diary_entry=entry)
        self.assertEqual(self.rewatches(tab="tv"), 0)

    def test_a_viewing_of_a_hidden_log_never_makes_a_rewatch(self):
        _show, _season_item, _tv, season = self.tv_show()
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        hidden = self.log(episode, utc_day(2025, 3, 1), visibility="private")
        hidden_viewing = self.viewing(season, episode, watched_on=date(2025, 3, 1), kind="log", entry=hidden)
        self.stamp(hidden_viewing, datetime(2025, 3, 1, 12, tzinfo=UTC))
        entry = self.log(episode, utc_day(2025, 3, 2))
        own = self.viewing(season, episode, watched_on=date(2025, 3, 2), kind="log", entry=entry)
        self.stamp(own, datetime(2025, 3, 2, 12, tzinfo=UTC))
        self.assertEqual(self.rewatches(tab="tv"), 1)
        self.assertEqual(self.rewatches(viewer=self.stranger, tab="tv"), 0)

    def test_season_and_show_logs_after_a_mark_need_the_saved_rewatch(self):
        show, season_item, _tv, season = self.tv_show()
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        fill = self.viewing(season, episode, kind="fill")
        self.stamp(fill, datetime(2025, 1, 1, 12, tzinfo=UTC))
        season_log = self.log(season_item, utc_day(2025, 3, 2))
        show_log = self.log(show, utc_day(2025, 3, 3))
        DiaryEntry.objects.filter(pk__in=[season_log.pk, show_log.pk]).update(created_at=datetime(2025, 3, 2, 12, tzinfo=UTC))
        self.assertEqual(self.rewatches(tab="tv"), 0)
        DiaryEntry.objects.filter(pk__in=[season_log.pk, show_log.pk]).update(is_rewatch=True)
        self.assertEqual(self.rewatches(tab="tv"), 2)
        # A fill recorded after the log doesn't come first.
        self.stamp(fill, datetime(2025, 4, 1, 12, tzinfo=UTC))
        self.assertEqual(self.rewatches(tab="tv"), 0)


class TimeTests(ActivityBase):
    """Time per type in its own unit, and total hours (STAT-201, STAT-202)."""

    def test_movies_and_music_count_each_log_and_undated_completions_in_all_time(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.set_item(film, runtime_minutes=120)
        unknown = self.item(MediaTypes.MOVIE.value, "unknown")
        marked = self.item(MediaTypes.MOVIE.value, "marked")
        self.set_item(marked, runtime_minutes=90)
        self.log(film, utc_day(2025, 3, 2))
        self.log(film, utc_day(2025, 4, 2))
        self.log(unknown, utc_day(2025, 4, 2))
        self.tracking(Movie, marked, direct_consumption=True)
        album = self.item(MediaTypes.MUSIC.value, "album")
        self.set_item(album, runtime_minutes=40)
        self.log(album, utc_day(2025, 3, 2))

        year = self.summary(YEAR_2025)
        self.assertEqual(
            time_row(year, "movie"),
            {"media_type": "movie", "unit": "minutes", "value": 240, "known": 2, "total": 3, "approximate": False},
        )
        self.assertEqual((time_row(year, "music")["value"], time_row(year, "music")["total"]), (40, 1))
        all_time = self.summary(ALL_TIME)
        self.assertEqual(
            (time_row(all_time, "movie")["value"], time_row(all_time, "movie")["known"], time_row(all_time, "movie")["total"]),
            (330, 3, 4),
        )
        hours = all_time["time"]["hours_total"]
        self.assertEqual(hours["minutes"], 370)
        self.assertFalse(hours["approximate"])
        self.assertEqual(hours["media_types"], ["movie", "tv", "anime", "game", "music"])
        self.assertEqual(hours["leaves_out"], ["manga", "book", "comic"])
        # Rows come in primary order and only for types with something counted.
        self.assertEqual([row["media_type"] for row in all_time["time"]["rows"]], ["movie", "music"])
        movie_tab = self.summary(ALL_TIME, tab="movie")
        self.assertIsNone(movie_tab["time"]["hours_total"])
        self.assertEqual([row["media_type"] for row in movie_tab["time"]["rows"]], ["movie"])

    def test_tv_fills_count_only_in_all_time_and_runtimes_fall_back(self):
        # S-04 and TV-301.
        show, season_item, _tv, season = self.tv_show()
        self.set_item(show, runtime_minutes=60)
        own = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        self.set_item(own, runtime_minutes=25)
        bare = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=2)
        self.viewing(season, own, watched_on=date(2025, 3, 2))
        self.viewing(season, own, kind="fill")
        self.viewing(season, bare, kind="fill")

        year = time_row(self.summary(YEAR_2025, tab="tv"), "tv")
        self.assertEqual((year["value"], year["known"], year["total"], year["approximate"]), (25, 1, 1, False))
        all_time = time_row(self.summary(ALL_TIME, tab="tv"), "tv")
        # The bare episode takes the show's runtime: approximate.
        self.assertEqual((all_time["value"], all_time["total"], all_time["approximate"]), (110, 3, True))
        self.set_item(season_item, runtime_minutes=30)
        all_time = time_row(self.summary(ALL_TIME, tab="tv"), "tv")
        self.assertEqual(all_time["value"], 80)
        self.assertTrue(self.summary(ALL_TIME)["time"]["hours_total"]["approximate"])

    def test_a_season_logs_fills_count_on_the_logs_day(self):
        # STAT-104: logging a season puts its filled episodes on the log's day.
        _show, season_item, _tv, season = self.tv_show()
        first = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        second = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=2)
        marked = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=3)
        for episode in (first, second, marked):
            self.set_item(episode, runtime_minutes=40)
        season_log = self.log(season_item, utc_day(2025, 4, 3))
        self.viewing(season, first, kind="fill", entry=season_log)
        self.viewing(season, second, kind="fill", entry=season_log)
        # A mark's fill still has no day.
        self.viewing(season, marked, kind="fill")

        year = time_row(self.summary(YEAR_2025, tab="tv"), "tv")
        self.assertEqual((year["value"], year["total"]), (80, 2))
        self.assertIsNone(time_row(self.summary(YEAR_2024, tab="tv"), "tv"))
        self.assertEqual(time_row(self.summary(ALL_TIME, tab="tv"), "tv")["value"], 120)
        self.assertEqual(self.summary(YEAR_2025, tab="tv")["header"]["active_days"], 1)

    def test_tv_time_leaves_out_viewings_of_hidden_logs(self):
        _show, _season_item, _tv, season = self.tv_show()
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        self.set_item(episode, runtime_minutes=30)
        hidden = self.log(episode, utc_day(2025, 3, 1), visibility="private")
        self.viewing(season, episode, watched_on=date(2025, 3, 1), kind="log", entry=hidden)
        self.viewing(season, episode, watched_on=date(2025, 3, 5))
        self.assertEqual(time_row(self.summary(YEAR_2025, tab="tv"), "tv")["value"], 60)
        self.assertEqual(time_row(self.summary(YEAR_2025, viewer=self.follower, tab="tv"), "tv")["value"], 30)

    def test_anime_time_counts_rises_and_skips_the_creation_row(self):
        series = self.item(MediaTypes.ANIME.value, "series")
        self.set_item(series, runtime_minutes=24)
        row = self.tracking(Anime, series, progress=10)
        # Imported with 20 episodes in 2024: the creation row adds nothing.
        self.history(row, 20, datetime(2024, 6, 1, 12, tzinfo=UTC), kind="+")
        self.history(row, 22, datetime(2025, 1, 10, 12, tzinfo=UTC))
        self.history(row, 5, datetime(2025, 2, 10, 12, tzinfo=UTC))
        self.history(row, 10, datetime(2025, 3, 10, 12, tzinfo=UTC))
        rewatch = self.tracking(Anime, series, progress=3)
        self.history(rewatch, 1, datetime(2025, 4, 1, 12, tzinfo=UTC), kind="+")
        self.history(rewatch, 3, datetime(2025, 4, 2, 12, tzinfo=UTC))

        year = time_row(self.summary(YEAR_2025, tab="anime"), "anime")
        # +2, then a drop ignored, then +5; the second watch-through +2.
        self.assertEqual(
            year,
            {"media_type": "anime", "unit": "minutes", "value": 9 * 24, "known": 2, "total": 2, "approximate": True},
        )
        self.assertIsNone(time_row(self.summary(YEAR_2024, tab="anime"), "anime"))
        all_time = time_row(self.summary(ALL_TIME, tab="anime"), "anime")
        self.assertEqual((all_time["value"], all_time["total"], all_time["approximate"]), (13 * 24, 2, False))

    def test_manga_chapters_on_the_owners_calendar(self):
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                Manga.objects.all().delete()
                Manga.history.model.objects.all().delete()
                self.set_zone(zone)
                volume = self.item(MediaTypes.MANGA.value, f"manga-{zone}")
                row = self.tracking(Manga, volume, progress=8)
                self.history(row, 0, datetime(2025, 12, 1, 12, tzinfo=UTC), kind="+")
                # 23:30 on 31 December in the owner's zone stays in 2025.
                late = datetime(2025, 12, 31, 23, 30, tzinfo=ZoneInfo(zone))
                self.history(row, 8, late)
                # A viewer in Auckland sees the owner's days (STAT-005).
                year = time_row(
                    self.summary(YEAR_2025, viewer=self.follower, tab="manga", zone=AUCKLAND),
                    "manga",
                )
                self.assertEqual((year["unit"], year["value"], year["known"], year["total"]), ("chapters", 8, 1, 1))
                self.assertIsNone(time_row(self.summary(YEAR_2026, tab="manga"), "manga"))

    def test_game_minutes_land_on_minutes_updated_on_else_end_date(self):
        title = self.item(MediaTypes.GAME.value, "title")
        game = self.tracking(Game, title, imported_lifetime_minutes=9000, imported_lifetime_source="steam")
        self.session(game, total_minutes=600, minutes_updated_on=date(2025, 2, 1), end_date=date(2024, 12, 1))
        self.session(game, total_minutes=120, end_date=date(2025, 5, 1))
        self.session(game, total_minutes=None, end_date=date(2025, 6, 1))
        self.session(game, total_minutes=60, end_date=date(2024, 6, 1))

        year = time_row(self.summary(YEAR_2025, tab="game"), "game")
        self.assertEqual((year["value"], year["known"], year["total"], year["approximate"]), (720, 2, 3, True))
        all_time = time_row(self.summary(ALL_TIME, tab="game"), "game")
        # Steam's imported lifetime playtime is never added (GM-500).
        self.assertEqual((all_time["value"], all_time["known"], all_time["total"], all_time["approximate"]), (780, 3, 4, False))

    def test_book_pages_from_the_snapshot_else_total_pages(self):
        novel = self.item(MediaTypes.BOOK.value, "novel")
        self.set_item(novel, total_pages=300)
        snap = self.log(novel, utc_day(2025, 3, 2))
        DiaryEntry.objects.filter(pk=snap.pk).update(progress_snapshot={"kind": "pages", "value": 320, "max": 300, "unit": "page"})
        percent = self.log(novel, utc_day(2025, 4, 2))
        DiaryEntry.objects.filter(pk=percent.pk).update(progress_snapshot={"kind": "percentage", "value": 100.0, "max": 100})
        unknown = self.item(MediaTypes.BOOK.value, "unknown")
        self.log(unknown, utc_day(2025, 4, 2))
        manual = self.item(MediaTypes.BOOK.value, "manual")
        self.set_item(manual, total_pages=200)
        self.tracking(Book, manual, completed_manually=True)

        year = time_row(self.summary(YEAR_2025, tab="book"), "book")
        self.assertEqual(
            year,
            {"media_type": "book", "unit": "pages", "value": 620, "known": 2, "total": 3, "approximate": False},
        )
        all_time = self.summary(ALL_TIME)
        self.assertEqual((time_row(all_time, "book")["value"], time_row(all_time, "book")["total"]), (820, 4))
        # Books never add to the total hours.
        self.assertEqual(all_time["time"]["hours_total"]["minutes"], 0)


class StreakTests(ActivityBase):
    """Active days and streaks, on the owner's calendar (STAT-005, STAT-200)."""

    def test_streaks_across_the_year_boundary_in_every_owner_zone(self):
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                DiaryEntry.objects.all().delete()
                self.set_zone(zone)
                local = ZoneInfo(zone)
                moments = [
                    datetime(2025, 12, 30, 20, 0, tzinfo=local),
                    datetime(2025, 12, 31, 23, 30, tzinfo=local),
                    datetime(2026, 1, 1, 0, 30, tzinfo=local),
                    datetime(2026, 1, 2, 12, 0, tzinfo=local),
                    datetime(2026, 1, 6, 12, 0, tzinfo=local),
                ]
                for index, moment in enumerate(moments):
                    self.log(self.item(MediaTypes.ANIME.value, f"{zone}-{index}"), moment)
                for viewer, header_zone in ((self.owner, zone), (self.follower, LOS_ANGELES), (self.stranger, NEW_YORK)):
                    all_time = self.summary(ALL_TIME, viewer=viewer, zone=header_zone)
                    self.assertEqual(
                        (all_time["header"]["active_days"], all_time["header"]["longest_daily_streak"]),
                        (5, 4),
                    )
                    self.assertEqual(detail_value(all_time, "longest_weekly_streak"), 2)
                    year_2025 = self.summary(YEAR_2025, viewer=viewer, zone=header_zone)
                    self.assertEqual(
                        (year_2025["header"]["active_days"], year_2025["header"]["longest_daily_streak"]),
                        (2, 2),
                    )
                    self.assertNotIn("current_daily_streak", details(year_2025))
                    year_2026 = self.summary(YEAR_2026, viewer=viewer, zone=header_zone)
                    self.assertEqual(
                        (year_2026["header"]["active_days"], year_2026["header"]["longest_daily_streak"]),
                        (3, 2),
                    )
                    self.assertEqual(detail_value(year_2026, "longest_weekly_streak"), 2)
                    self.assertEqual(detail_value(year_2026, "current_daily_streak"), 0)

    def test_current_streaks_count_back_from_today_or_yesterday(self):
        # Today is Monday 26 October 2026.
        for day in (date(2026, 10, 25), date(2026, 10, 24), date(2026, 10, 18), date(2026, 10, 10)):
            self.log(self.item(MediaTypes.MOVIE.value, f"film-{day}"), utc_day(day.year, day.month, day.day))
        data = self.summary(ALL_TIME)
        self.assertEqual(detail_value(data, "current_daily_streak"), 2)
        self.assertEqual(detail_value(data, "current_weekly_streak"), 3)
        self.assertEqual(detail_value(data, "longest_weekly_streak"), 3)
        self.owner.week_start_day = "sunday"
        self.owner.save(update_fields=["week_start_day"])
        data = self.summary(ALL_TIME)
        self.assertEqual(detail_value(data, "current_weekly_streak"), 2)
        self.assertEqual(data["activity"]["week_starts_on"], "sunday")
        self.log(self.item(MediaTypes.MOVIE.value, "today"), utc_day(2026, 10, 26))
        self.assertEqual(detail_value(self.summary(ALL_TIME), "current_daily_streak"), 3)

    def test_streak_helpers(self):
        days = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 4)]
        self.assertEqual(activity.longest_daily_streak(days), 2)
        self.assertEqual(activity.longest_daily_streak([]), 0)
        self.assertEqual(activity.current_daily_streak(days, date(2025, 1, 5)), 1)
        self.assertEqual(activity.current_daily_streak(days, date(2025, 1, 6)), 0)
        self.assertEqual(activity.week_start(date(2026, 10, 25), "monday"), date(2026, 10, 19))
        self.assertEqual(activity.week_start(date(2026, 10, 25), "sunday"), date(2026, 10, 25))
        self.assertEqual(activity.week_start(date(2026, 10, 24), "sunday"), date(2026, 10, 18))


class ActivityChartTests(ActivityBase):
    """Events by month and weekday, busiest day and month (STAT-203)."""

    def test_a_calendar_year_sends_twelve_months_split_by_type(self):
        _show, season_item, _tv, season = self.tv_show()
        first = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        second = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=2)
        # S-04: three viewings of one show on one day plus a season log are one event.
        self.viewing(season, first, watched_on=date(2025, 3, 2))
        self.viewing(season, second, watched_on=date(2025, 3, 2))
        self.viewing(season, second, kind="fill")
        self.log(season_item, utc_day(2025, 3, 2))
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.log(film, utc_day(2025, 3, 2))
        self.log(film, utc_day(2025, 3, 2))
        self.log(self.item(MediaTypes.MOVIE.value, "other"), utc_day(2025, 8, 4))

        data = self.summary(YEAR_2025)["activity"]
        self.assertEqual([month["month"] for month in data["months"]], [f"2025-{month:02d}" for month in range(1, 13)])
        march = data["months"][2]
        self.assertEqual(march, {"month": "2025-03", "count": 2, "by_type": {"movie": 1, "tv": 1}})
        self.assertEqual(data["months"][0], {"month": "2025-01", "count": 0, "by_type": {}})
        # 2 March 2025 is a Sunday, 4 August a Monday.
        self.assertEqual(data["weekdays"], [1, 0, 0, 0, 0, 0, 2])
        self.assertEqual(data["week_starts_on"], "monday")
        self.assertEqual(data["busiest_day"], {"date": "2025-03-02", "count": 2})
        self.assertEqual(data["busiest_month"], {"month": "2025-03", "count": 2})

    def test_other_ranges_span_first_to_last_month_and_ties_go_later(self):
        self.log(self.item(MediaTypes.MOVIE.value, "a"), utc_day(2024, 11, 5))
        self.log(self.item(MediaTypes.MOVIE.value, "b"), utc_day(2025, 2, 7))
        data = self.summary(ALL_TIME)["activity"]
        self.assertEqual(
            [(month["month"], month["count"]) for month in data["months"]],
            [("2024-11", 1), ("2024-12", 0), ("2025-01", 0), ("2025-02", 1)],
        )
        self.assertEqual(data["busiest_day"], {"date": "2025-02-07", "count": 1})
        self.assertEqual(data["busiest_month"], {"month": "2025-02", "count": 1})
        empty = self.summary({"schema": "2", "start_date": "2023-02-01", "end_date": "2023-05-01"})["activity"]
        self.assertEqual((empty["months"], empty["busiest_day"], empty["busiest_month"]), ([], None, None))
        self.assertEqual(empty["weekdays"], [0] * 7)

    def change(self, item, before, after, when, unit="episode"):
        """Record one progress change, as tracking does, at a chosen time."""
        row = ProgressChange.objects.create(
            actor=self.owner,
            item=item,
            previous_progress={"value": before, "unit": unit},
            current_progress={"value": after, "unit": unit},
        )
        ProgressChange.objects.filter(pk=row.pk).update(created_at=when)

    def test_each_days_progress_and_starts_are_events(self):
        series = self.item(MediaTypes.ANIME.value, "series")
        # Two changes on one day are one event, on the owner's (New York) day.
        self.change(series, 0, 2, datetime(2025, 3, 2, 15, tzinfo=UTC))
        self.change(series, 2, 4, datetime(2025, 3, 3, 3, tzinfo=UTC))
        # A day that went back on net isn't; a day in another year is outside 2025.
        self.change(series, 4, 5, datetime(2025, 3, 9, 15, tzinfo=UTC))
        self.change(series, 5, 3, datetime(2025, 3, 9, 16, tzinfo=UTC))
        self.change(series, 3, 6, datetime(2024, 6, 1, 15, tzinfo=UTC))
        # Starting a title is activity, as in the feed.
        self.change(self.item(MediaTypes.ANIME.value, "started"), 0, 1, datetime(2025, 4, 1, 15, tzinfo=UTC), "start")
        # A book's pages count. TV ticks don't (its viewings are its events), but a TV
        # start does.
        self.change(self.item(MediaTypes.BOOK.value, "novel"), 10, 40, datetime(2025, 3, 2, 18, tzinfo=UTC), "page")
        show = self.item(MediaTypes.TV.value, "show")
        self.change(show, 0, 3, datetime(2025, 5, 5, 15, tzinfo=UTC))
        self.change(show, 0, 1, datetime(2025, 5, 6, 15, tzinfo=UTC), "start")

        data = self.summary(YEAR_2025)
        self.assertEqual(data["header"]["active_days"], 3)
        self.assertEqual(data["activity"]["busiest_day"], {"date": "2025-03-02", "count": 2})
        self.assertEqual(data["activity"]["months"][2]["by_type"], {"anime": 1, "book": 1})
        self.assertEqual(data["activity"]["months"][4]["by_type"], {"tv": 1})
        anime = self.summary(YEAR_2025, tab="anime")
        self.assertEqual(anime["header"]["active_days"], 2)
        # The day's two changes are one event, tied with the start: the later day wins.
        self.assertEqual(anime["activity"]["busiest_day"], {"date": "2025-04-01", "count": 1})
        self.assertEqual(self.summary(ALL_TIME, tab="anime")["header"]["active_days"], 3)
        # Progress is visible to anyone who can see the profile (AC-5).
        self.assertEqual(self.summary(YEAR_2025, viewer=self.stranger)["header"]["active_days"], 3)

    def test_tv_fills_and_undated_completions_are_never_events(self):
        _show, _season_item, _tv, season = self.tv_show()
        self.viewing(season, self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1), kind="fill")
        self.tracking(Movie, self.item(MediaTypes.MOVIE.value, "marked"), direct_consumption=True)
        data = self.summary(ALL_TIME)
        self.assertEqual(data["header"]["titles"], 1)
        self.assertEqual(data["header"]["active_days"], 0)
        self.assertIsNone(data["activity"]["busiest_day"])
        self.assertEqual(data["activity"]["months"], [])


class MilestoneTests(ActivityBase):
    """The 1st, 100th, 250th, ... log (STAT-216)."""

    def test_milestone_numbers(self):
        self.assertEqual(activity.milestone_numbers(0), [])
        self.assertEqual(activity.milestone_numbers(99), [1])
        self.assertEqual(activity.milestone_numbers(260), [1, 100, 250])
        self.assertEqual(activity.milestone_numbers(2400), [1, 100, 250, 500, 750, 1000, 1500, 2000])

    def test_a_year_shows_the_milestones_reached_in_it(self):
        # S-26: 260 logs, the 250th on 4 May 2025.
        films = [self.item(MediaTypes.MOVIE.value, f"film-{index}") for index in range(3)]
        start = date(2024, 8, 28)
        entries = [
            DiaryEntry(
                user=self.owner,
                item=films[index % 3],
                consumed_at=utc_day(*(start + timedelta(days=index)).timetuple()[:3]),
            )
            for index in range(260)
        ]
        DiaryEntry.objects.bulk_create(entries)
        ordered = list(DiaryEntry.objects.order_by("consumed_at", "id"))
        self.assertEqual(ordered[249].consumed_at.date(), date(2025, 5, 4))

        year = self.summary(YEAR_2025)["milestones"]
        self.assertEqual([row["number"] for row in year], [250])
        self.assertEqual(
            {key: year[0][key] for key in ("number", "day", "entry_id", "media_type")},
            {"number": 250, "day": "2025-05-04", "entry_id": ordered[249].id, "media_type": "movie"},
        )
        self.assertEqual(year[0]["media"]["ref"]["item_id"], ordered[249].item_id)
        all_time = self.summary(ALL_TIME)["milestones"]
        self.assertEqual([row["number"] for row in all_time], [1, 100, 250])
        self.assertEqual(all_time[0]["day"], "2024-08-28")

    def test_type_tabs_number_their_own_logs_and_tv_shows_the_show(self):
        show, _season_item, _tv, _season = self.tv_show()
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.log(film, utc_day(2025, 1, 1))
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        tv_log = self.log(episode, utc_day(2025, 1, 2))
        tv_tab = self.summary(ALL_TIME, tab="tv")["milestones"]
        self.assertEqual([(row["number"], row["entry_id"]) for row in tv_tab], [(1, tv_log.id)])
        self.assertEqual(tv_tab[0]["media_type"], "tv")
        self.assertEqual(tv_tab[0]["media"]["ref"]["item_id"], show.id)
        self.assertIsNone(tv_tab[0]["media"]["user_state"])
        global_tab = self.summary(ALL_TIME)["milestones"]
        self.assertEqual([(row["number"], row["media_type"]) for row in global_tab], [(1, "movie")])

    def test_each_viewer_counts_the_logs_they_can_see(self):
        self.tv_show()
        episode = self.item(MediaTypes.EPISODE.value, "show", season=1, episode=1)
        self.log(episode, utc_day(2025, 1, 1), visibility="private")
        visible = self.log(episode, utc_day(2025, 1, 2))
        owner = self.summary(ALL_TIME)["milestones"]
        stranger = self.summary(ALL_TIME, viewer=self.stranger)["milestones"]
        self.assertNotEqual(owner[0]["entry_id"], visible.id)
        self.assertEqual([(row["number"], row["entry_id"]) for row in stranger], [(1, visible.id)])


class QueryTests(ActivityBase):
    """These sections aggregate in SQL: query counts don't grow with the library."""

    def seed(self, count):
        for index in range(count):
            film = self.item(MediaTypes.MOVIE.value, f"film-{count}-{index}")
            self.set_item(film, runtime_minutes=100)
            self.log(film, utc_day(2025, 3, index + 1))
            self.log(film, utc_day(2025, 4, index + 1))
            _show, _season_item, _tv, season = self.tv_show(f"show-{count}-{index}")
            episode = self.item(MediaTypes.EPISODE.value, f"show-{count}-{index}", season=1, episode=1)
            entry = self.log(episode, utc_day(2025, 5, index + 1))
            self.viewing(season, episode, watched_on=date(2025, 5, index + 1), kind="log", entry=entry)
            self.viewing(season, episode, kind="fill")
            novel = self.item(MediaTypes.BOOK.value, f"book-{count}-{index}")
            self.tracking(Book, novel, completed_manually=True)
            self.log(novel, utc_day(2025, 6, index + 1))
            series = self.item(MediaTypes.ANIME.value, f"anime-{count}-{index}")
            row = self.tracking(Anime, series, progress=4)
            self.history(row, 0, datetime(2025, 1, 1, 12, tzinfo=UTC), kind="+")
            self.history(row, 4, datetime(2025, 2, 1, 12, tzinfo=UTC))
            game = self.tracking(Game, self.item(MediaTypes.GAME.value, f"game-{count}-{index}"))
            self.session(game, total_minutes=60, end_date=date(2025, 7, 1))

    def queries(self, count, params, tab):
        clear_library()
        Anime.history.model.objects.all().delete()
        self.seed(count)
        counts = []
        for build in (activity.build_header, activity.build_time, activity.build_activity, activity.build_milestones):
            scope = build_scope(self.owner, self.follower, {**params, **({"media_type": tab} if tab else {})})
            scope.audience  # noqa: B018 - resolve the audience outside the count
            with CaptureQueriesContext(connection) as captured:
                build(scope)
            counts.append(len(captured))
        return counts

    def test_query_counts_are_flat(self):
        for params in (YEAR_2025, ALL_TIME):
            for tab in (None, "movie", "tv", "book", "anime", "game"):
                with self.subTest(range=params["start_date"], tab=tab):
                    self.assertEqual(self.queries(5, params, tab), self.queries(2, params, tab))

