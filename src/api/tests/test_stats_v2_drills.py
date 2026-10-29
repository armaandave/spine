"""Stats v2 drill-downs (docs/stats-contract.md STAT-200, STAT-400, STAT-401).

The header details' ``drill``, ``longest_daily_streak_range``, and the diary's
``repeat`` and ``multi_title_days`` filters. A drill opens the diary, so the diary
must list exactly what the number counts: each filter test compares the diary with
the header's number for the same range and tab. Date cases run with the server on
America/New_York and the owner's calendar on New York, Los Angeles and Auckland.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from api.services import stats_v2_activity as activity
from api.services.stats_scope import build_scope
from api.tests.test_stats_v2_activity import YEAR_2026, ActivityBase, details, utc_day
from api.tests.test_stats_v2_shape import (
    ALL_TIME,
    AUCKLAND,
    OWNER_ZONES,
    YEAR_2024,
    YEAR_2025,
)
from app.models import Book, DiaryEntry, Episode, MediaTypes, Movie
from social.models import ProgressChange

TV_TYPES = ["tv", "season", "episode"]


class DrillBase(ActivityBase):
    """Helpers that ask the diary what the app's drill-downs ask it."""

    def drills(self, params, *, viewer=None, tab=None):
        return {row["key"]: row["drill"] for row in self.summary(params, viewer=viewer, tab=tab)["header"]["details"]}

    def scope(self, params, tab=None):
        return build_scope(self.owner, self.owner, {**params, **({"media_type": tab} if tab else {})})

    def scope_params(self, params, tab):
        """Return the diary parameters the app sends for a stats scope (STAT-401)."""
        query = {"skip_backfill": "true", "tv_rollup": "true", "page_size": 100}
        if params["start_date"] != "all":
            query.update(watched_from=params["start_date"], watched_to=params["end_date"])
        if tab == MediaTypes.TV.value:
            query["media_type"] = TV_TYPES
        elif tab:
            query["media_type"] = tab
        return query

    def diary(self, query):
        """Return the diary's logs for the query as ``{entry_id: row}``."""
        zone = self.owner.calendar_timezone
        response = self.client_for(self.owner, zone).get("/api/v1/diary/", query)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertLessEqual(response.data["count"], 100)
        return {row["id"]: row for row in response.data["results"]}

    def log_days(self, scope):
        """Return ``{entry_id: log day}`` of the scope's logs (STAT-100)."""
        return dict(scope.entries.annotate(day=scope.log_day()).values_list("id", "day"))


class HeaderDrillTests(DrillBase):
    """``drill`` on every detail row and the longest streak's days (STAT-200)."""

    def test_every_detail_row_carries_its_drill(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        entry = self.log(film, utc_day(2025, 3, 2))
        entry.review = "Great"
        entry.liked = True
        entry.save()
        # 2 March 2025 is a Sunday: its week starts on Monday 24 February.
        self.assertEqual(
            self.drills(YEAR_2025),
            {
                "longest_weekly_streak": {"watched_from": "2025-02-24", "watched_to": "2025-03-02"},
                "rewatches": {"repeat": "true"},
                "reviews": {"has_review": "true"},
                "likes": {"liked": "true"},
                "days_with_two_plus_titles": {"multi_title_days": "true"},
            },
        )
        # Per-type facts have no matching diary filter.
        for tab, keys in (
            ("tv", ("episodes", "seasons_finished", "shows_finished")),
            ("anime", ("episodes",)),
            ("book", ("reads", "did_not_finish")),
            ("game", ("completions", "abandoned")),
        ):
            with self.subTest(tab=tab):
                drills = self.drills(ALL_TIME, tab=tab)
                for key in keys:
                    self.assertIn(key, drills)
                    self.assertIsNone(drills[key], key)
                self.assertEqual(drills["rewatches"], {"repeat": "true"})

    def test_zero_streaks_have_no_drill_or_range(self):
        data = self.summary(YEAR_2025)
        self.assertIsNone(details(data)["longest_weekly_streak"]["drill"])
        self.assertEqual(data["header"]["longest_daily_streak"], 0)
        self.assertIsNone(data["header"]["longest_daily_streak_range"])
        # Logs earlier in 2026 but none lately: the current streaks are 0.
        self.log(self.item(MediaTypes.MOVIE.value, "march"), utc_day(2026, 3, 2))
        current = details(self.summary(YEAR_2026))
        self.assertEqual((current["current_daily_streak"]["value"], current["current_daily_streak"]["drill"]), (0, None))
        self.assertEqual((current["current_weekly_streak"]["value"], current["current_weekly_streak"]["drill"]), (0, None))
        self.assertIsNotNone(current["longest_weekly_streak"]["drill"])

    def test_streak_drills_and_the_longest_streak_on_the_owners_calendar(self):
        # Now is 16:00 UTC on Monday 26 October 2026: 27 October in Auckland, whose
        # current streak therefore counts back from yesterday.
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                DiaryEntry.objects.all().delete()
                self.set_zone(zone)
                local = ZoneInfo(zone)
                # Moments just either side of midnight, on the owner's calendar, and a
                # carrier on 24 October, which stays on that day in every zone.
                self.log(self.item(MediaTypes.ANIME.value, f"{zone}-late"), datetime(2026, 10, 25, 23, 30, tzinfo=local))
                self.log(self.item(MediaTypes.ANIME.value, f"{zone}-early"), datetime(2026, 10, 26, 0, 30, tzinfo=local))
                self.log(self.item(MediaTypes.MOVIE.value, f"{zone}-film"), utc_day(2026, 10, 24))
                self.log(self.item(MediaTypes.MOVIE.value, f"{zone}-old"), utc_day(2026, 10, 10))
                for params in (ALL_TIME, YEAR_2026):
                    data = self.summary(params, zone=zone)
                    self.assertEqual(data["header"]["longest_daily_streak"], 3)
                    self.assertEqual(data["header"]["longest_daily_streak_range"], {"start": "2026-10-24", "end": "2026-10-26"})
                    drills = {row["key"]: row["drill"] for row in data["header"]["details"]}
                    self.assertEqual(drills["current_daily_streak"], {"watched_from": "2026-10-24", "watched_to": "2026-10-26"})
                    # Monday weeks: 5, 12 (none), 19 and 26 October.
                    self.assertEqual(drills["longest_weekly_streak"], {"watched_from": "2026-10-19", "watched_to": "2026-11-01"})
                    self.assertEqual(drills["current_weekly_streak"], {"watched_from": "2026-10-19", "watched_to": "2026-11-01"})

    def test_weekly_drills_are_clipped_to_the_range_and_ties_go_later(self):
        # 1 January and 31 December 2025 are Wednesdays: two one-week streaks.
        self.log(self.item(MediaTypes.MOVIE.value, "new-year"), utc_day(2025, 1, 1))
        self.log(self.item(MediaTypes.MOVIE.value, "new-years-eve"), utc_day(2025, 12, 31))
        self.assertEqual(
            self.drills(YEAR_2025)["longest_weekly_streak"],
            {"watched_from": "2025-12-29", "watched_to": "2025-12-31"},
        )
        january = {"schema": "2", "start_date": "2025-01-01", "end_date": "2025-01-31"}
        self.assertEqual(
            self.drills(january)["longest_weekly_streak"],
            {"watched_from": "2025-01-01", "watched_to": "2025-01-05"},
        )
        # All Time has no range to clip to.
        self.assertEqual(
            self.drills(ALL_TIME)["longest_weekly_streak"],
            {"watched_from": "2025-12-29", "watched_to": "2026-01-04"},
        )
        self.owner.week_start_day = "sunday"
        self.owner.save(update_fields=["week_start_day"])
        self.assertEqual(
            self.drills(YEAR_2025)["longest_weekly_streak"],
            {"watched_from": "2025-12-28", "watched_to": "2025-12-31"},
        )
        # A current week running past the range's end is clipped too.
        self.log(self.item(MediaTypes.MOVIE.value, "today"), utc_day(2026, 10, 26))
        to_tuesday = {"schema": "2", "start_date": "2026-10-01", "end_date": "2026-10-27"}
        self.assertEqual(
            self.drills(to_tuesday)["current_weekly_streak"],
            {"watched_from": "2026-10-25", "watched_to": "2026-10-27"},
        )

    def test_drills_are_the_owners_only(self):
        film = self.item(MediaTypes.MOVIE.value, "film")
        self.log(film, utc_day(2026, 10, 25))
        self.log(film, utc_day(2026, 10, 26))
        owner = self.summary(ALL_TIME)
        self.assertTrue(all(row["drill"] for row in owner["header"]["details"]))
        for viewer in (self.follower, self.stranger):
            with self.subTest(viewer=viewer.username):
                other = self.summary(ALL_TIME, viewer=viewer)
                self.assertEqual([row["drill"] for row in other["header"]["details"]], [None] * len(owner["header"]["details"]))
                self.assertEqual(
                    [(row["key"], row["value"]) for row in other["header"]["details"]],
                    [(row["key"], row["value"]) for row in owner["header"]["details"]],
                )
                # The streak's days are a fact, not a drill: everyone gets them.
                self.assertEqual(other["header"]["longest_daily_streak_range"], {"start": "2026-10-25", "end": "2026-10-26"})


class RepeatFilterTests(DrillBase):
    """``repeat=true`` lists exactly the logs Stats counts as repeats (STAT-108)."""

    def seed(self, zone):
        # Rule 1, an earlier log: the second and third logs of a film.
        film = self.item(MediaTypes.MOVIE.value, f"{zone}-film")
        self.log(film, utc_day(2024, 3, 2))
        self.log(film, utc_day(2025, 3, 2))
        self.log(film, utc_day(2025, 8, 1))
        # Rule 2 for movies: the marker counts only with the saved rewatch.
        marked = self.item(MediaTypes.MOVIE.value, f"{zone}-marked")
        self.tracking(Movie, marked, direct_consumption=True)
        saved = self.log(marked, utc_day(2025, 4, 1))
        DiaryEntry.objects.filter(pk=saved.pk).update(is_rewatch=True)
        imported = self.item(MediaTypes.MOVIE.value, f"{zone}-imported")
        self.tracking(Movie, imported, direct_consumption=True)
        self.log(imported, utc_day(2025, 4, 2))
        # Rule 2 for books: completed_manually makes the first log a reread.
        novel = self.item(MediaTypes.BOOK.value, f"{zone}-novel")
        self.tracking(Book, novel, completed_manually=True)
        self.log(novel, utc_day(2025, 6, 1))
        # Anime moments either side of midnight on the owner's calendar: the second
        # is a rewatch on 1 January 2026.
        local = ZoneInfo(zone)
        series = self.item(MediaTypes.ANIME.value, f"{zone}-series")
        self.log(series, datetime(2025, 12, 31, 23, 30, tzinfo=local))
        self.log(series, datetime(2026, 1, 1, 0, 30, tzinfo=local))
        # TV: E1, E2, then E1 again; and an E3 log after an unclaimed tick of E3.
        _show, _season_item, _tv, season = self.tv_show(f"{zone}-show")
        episodes = [self.item(MediaTypes.EPISODE.value, f"{zone}-show", season=1, episode=number) for number in (1, 2, 3)]
        for day, episode in ((2, episodes[0]), (3, episodes[1]), (4, episodes[0])):
            entry = self.log(episode, utc_day(2025, 3, day))
            self.viewing(season, episode, watched_on=date(2025, 3, day), kind="log", entry=entry)
        tick = self.viewing(season, episodes[2], watched_on=date(2025, 3, 9))
        self.stamp(tick, datetime(2025, 3, 9, 12, tzinfo=UTC))
        entry = self.log(episodes[2], utc_day(2025, 3, 10))
        own = self.viewing(season, episodes[2], watched_on=date(2025, 3, 10), kind="log", entry=entry)
        self.stamp(own, datetime(2025, 3, 10, 12, tzinfo=UTC))

    def test_repeat_lists_exactly_the_rewatches_counted(self):
        for zone in OWNER_ZONES:
            self.set_zone(zone)
            self.seed(zone)
            for params in (ALL_TIME, YEAR_2024, YEAR_2025, YEAR_2026):
                for tab in (None, "movie", "tv", "book", "anime"):
                    with self.subTest(owner_zone=zone, range=params["start_date"], tab=tab):
                        counted = activity.rewatch_ids(self.scope(params, tab))
                        number = details(self.summary(params, tab=tab, zone=zone))["rewatches"]["value"]
                        listed = self.diary({**self.scope_params(params, tab), "repeat": "true"})
                        self.assertEqual(set(listed), counted)
                        self.assertEqual(len(listed), number)
            Episode.objects.all().delete()
            DiaryEntry.objects.all().delete()

    def test_the_repeats_of_a_year(self):
        self.set_zone(AUCKLAND)
        self.seed(AUCKLAND)
        listed = self.diary({**self.scope_params(YEAR_2025, None), "repeat": "true"})
        self.assertEqual(
            self.titles_and_days(listed),
            sorted([
                (f"book {AUCKLAND}-novel", "2025-06-01"),
                (f"episode {AUCKLAND}-show", "2025-03-04"),
                (f"episode {AUCKLAND}-show", "2025-03-10"),
                (f"movie {AUCKLAND}-film", "2025-03-02"),
                (f"movie {AUCKLAND}-film", "2025-08-01"),
                (f"movie {AUCKLAND}-marked", "2025-04-01"),
            ]),
        )
        # The anime rewatch at 00:30 on 1 January is 2026's.
        self.assertEqual(
            self.titles_and_days(self.diary({**self.scope_params(YEAR_2026, "anime"), "repeat": "true"})),
            [(f"anime {AUCKLAND}-series", "2026-01-01")],
        )
        # Without the filter, or with it off, the diary is unchanged.
        everything = self.diary(self.scope_params(YEAR_2025, None))
        self.assertEqual(set(self.diary({**self.scope_params(YEAR_2025, None), "repeat": "false"})), set(everything))
        self.assertGreater(len(everything), len(listed))

    def titles_and_days(self, listed):
        days = self.log_days(self.scope(ALL_TIME))
        entries = DiaryEntry.objects.filter(id__in=listed).select_related("item")
        return sorted((entry.item.title, days[entry.id].isoformat()) for entry in entries)


class MultiTitleDayFilterTests(DrillBase):
    """``multi_title_days=true``: the logs on days with 2+ titles (STAT-106)."""

    def seed(self, zone):
        local = ZoneInfo(zone)
        films = [self.item(MediaTypes.MOVIE.value, f"{zone}-film-{index}") for index in range(4)]
        # 2 March: two films.
        self.log(films[0], utc_day(2025, 3, 2))
        self.log(films[1], utc_day(2025, 3, 2))
        # 3 March: a film and a tick with no log, two titles on Global only.
        _show, _season_item, _tv, season = self.tv_show(f"{zone}-ticked")
        self.viewing(season, self.item(MediaTypes.EPISODE.value, f"{zone}-ticked", season=1, episode=1),
                     watched_on=date(2025, 3, 3))
        self.log(films[2], utc_day(2025, 3, 3))
        # 4 March: one film twice is one title.
        self.log(films[3], utc_day(2025, 3, 4))
        self.log(films[3], utc_day(2025, 3, 4))
        # 10 June: two episodes of one show and a season of another: two TV titles.
        self.tv_show(f"{zone}-show")
        for number in (1, 2):
            self.log(self.item(MediaTypes.EPISODE.value, f"{zone}-show", season=1, episode=number), utc_day(2025, 6, 10))
        _other, other_season, _tv, _season = self.tv_show(f"{zone}-other")
        self.log(other_season, utc_day(2025, 6, 10))
        # 9 July: a day's anime progress and a film log.
        series = self.item(MediaTypes.ANIME.value, f"{zone}-progress")
        change = ProgressChange.objects.create(
            actor=self.owner,
            item=series,
            previous_progress={"value": 1, "unit": "episode"},
            current_progress={"value": 3, "unit": "episode"},
        )
        ProgressChange.objects.filter(pk=change.pk).update(created_at=datetime(2025, 7, 9, 12, tzinfo=local))
        self.log(films[0], utc_day(2025, 7, 9))
        # New Year's Eve and Day: an anime moment and a film carrier on each, on the
        # owner's calendar.
        self.log(self.item(MediaTypes.ANIME.value, f"{zone}-eve"), datetime(2025, 12, 31, 23, 30, tzinfo=local))
        self.log(films[1], utc_day(2025, 12, 31))
        self.log(self.item(MediaTypes.ANIME.value, f"{zone}-day"), datetime(2026, 1, 1, 0, 30, tzinfo=local))
        self.log(films[2], utc_day(2026, 1, 1))

    def test_multi_title_days_lists_the_logs_on_the_days_counted(self):
        expected_days = {
            None: {"2025-03-02", "2025-03-03", "2025-06-10", "2025-07-09", "2025-12-31", "2026-01-01"},
            "movie": {"2025-03-02"},
            "tv": {"2025-06-10"},
            "anime": set(),
        }
        for zone in OWNER_ZONES:
            self.set_zone(zone)
            self.seed(zone)
            for params in (ALL_TIME, YEAR_2025, YEAR_2026):
                for tab in (None, "movie", "tv", "anime"):
                    with self.subTest(owner_zone=zone, range=params["start_date"], tab=tab):
                        scope = self.scope(params, tab)
                        counted = {day.isoformat() for day in activity.multi_title_days(scope.events)}
                        wanted = {
                            day for day in expected_days[tab]
                            if params["start_date"] == "all" or params["start_date"][:4] == day[:4]
                        }
                        self.assertEqual(counted, wanted)
                        number = details(self.summary(params, tab=tab, zone=zone))["days_with_two_plus_titles"]["value"]
                        listed = self.diary({**self.scope_params(params, tab), "multi_title_days": "true"})
                        days = self.log_days(scope)
                        # Every log of the tab on a counted day, and nothing else.
                        self.assertEqual(
                            set(listed),
                            {entry_id for entry_id, day in days.items() if day.isoformat() in counted},
                        )
                        # Here every counted day has a log of the tab, so the list's
                        # days are the number.
                        self.assertEqual(len({days[entry_id] for entry_id in listed}), number)
            ProgressChange.objects.all().delete()
            Episode.objects.all().delete()
            DiaryEntry.objects.all().delete()

    def test_a_partial_date_range_uses_the_same_days(self):
        self.set_zone(AUCKLAND)
        self.seed(AUCKLAND)
        days = self.log_days(self.scope(ALL_TIME))
        query = {"skip_backfill": "true", "page_size": 100, "multi_title_days": "true"}
        everything = self.diary(query)
        from_june = self.diary({**query, "watched_from": "2025-06-01"})
        self.assertEqual(
            {days[entry_id].isoformat() for entry_id in from_june},
            {"2025-06-10", "2025-07-09", "2025-12-31", "2026-01-01"},
        )
        self.assertTrue(set(from_june) < set(everything))
        # Media types from two type buckets count days on Global: 10 June had only
        # TV logs, so no movie or anime log is listed that day.
        mixed = self.diary({**query, "media_type": ["movie", "anime"]})
        self.assertEqual(
            {days[entry_id].isoformat() for entry_id in mixed},
            {"2025-03-02", "2025-03-03", "2025-07-09", "2025-12-31", "2026-01-01"},
        )
