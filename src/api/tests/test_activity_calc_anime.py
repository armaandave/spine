"""The one calculation on anime: its viewings, as Stats count them (AN-704).

Anime reads the calculation from Phase 2 (docs/activity-contract.md, AC-2). Its
viewings are its saves: a tick on its day, a log's fills on that log's day, a mark's
fills and a stored number in the pile. Every write here is anime_tracking's own, on a
12-episode title at 24 minutes, with the provider faked. The switch moves no number, so
every story also checks the calculation against the viewings counter it replaced.
"""

import io
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.core.management import call_command
from rest_framework.test import APIClient

from api.services import stats_time
from api.services.activity_rows import list_rows, piled, rows_for
from api.services.stats_scope import build_scope
from app import anime_tracking
from app.models import Anime, MediaTypes, Status
from app.tests.anime_fixtures import AnimeTestCase
from app.tests.tv_fixtures import make_user
from social.models import Follow, FollowStatus

ANIME = MediaTypes.ANIME.value
LOS_ANGELES = "America/Los_Angeles"
RUNTIME = 24
YEARS = (2025, 2026)


class AnimeCalcBase(AnimeTestCase):
    """anime_tracking's writes on a fixed clock, read through the calculation."""

    def setUp(self):
        super().setUp()
        self.follower = make_user(calendar=LOS_ANGELES)
        self.stranger = make_user(calendar=LOS_ANGELES)
        Follow.objects.create(from_user=self.follower, to_user=self.user, status=FollowStatus.ACCEPTED)

    def on(self, day):
        """Move the clock to noon on this day, on the account's calendar."""
        self.clock.set(datetime(day.year, day.month, day.day, 12, tzinfo=ZoneInfo(LOS_ANGELES)))

    def calc(self, viewer=None):
        return rows_for([self.user], viewer or self.user, types=[ANIME])

    def number(self, year=None, viewer=None):
        """Return minutes in a year, or in All Time with none."""
        calculation = self.calc(viewer)
        if year is None:
            rows = list_rows(calculation.rows, media_type=ANIME)
            return int(sum((row.amount for row in rows), piled(calculation.piles, ANIME).amount))
        rows = list_rows(calculation.rows, media_type=ANIME, start_date=date(year, 1, 1), end_date=date(year, 12, 31))
        return int(sum((row.amount for row in rows), Decimal(0)))

    def assert_episodes(self, in_2025, in_2026, all_time, viewer=None):
        numbers = (self.number(2025, viewer), self.number(2026, viewer), self.number(viewer=viewer))
        self.assertEqual(numbers, (in_2025 * RUNTIME, in_2026 * RUNTIME, all_time * RUNTIME))

    def lines(self, viewer=None):
        lines = []
        for row in list_rows(self.calc(viewer).rows, media_type=ANIME):
            parts = []
            if row.log_id:
                parts.append(f"log +{int(row.told)}" if row.told else "log")
            elif row.told:
                parts.append(f"watched {int(row.told)}")
            elif row.started:
                parts.append("started")
            if row.started and row.told:
                parts.append("started")
            lines.append(f"{row.day} " + ", ".join(parts + row.moves))
        return lines

    def assert_counter_agrees(self, viewers=None):
        """Check the calculation gives the old counter's minutes and episodes."""
        for viewer in viewers or (self.user, self.follower, self.stranger):
            for year in (*YEARS, None):
                params = {"start_date": "all", "end_date": "all"}
                if year is not None:
                    params = {"start_date": f"{year}-01-01", "end_date": f"{year}-12-31"}
                scope = build_scope(self.user, viewer, {**params, "media_type": ANIME})
                old = stats_time._anime_runtime(scope, ANIME)
                new = stats_time.type_time(scope, ANIME)
                with self.subTest(viewer=viewer.username, year=year):
                    self.assertEqual((new.value, new.progress), (old.value, old.progress))

    def title(self, **values):
        values.setdefault("total", 12)
        values.setdefault("runtime", RUNTIME)
        return super().title(**values)


class TicksTests(AnimeCalcBase):
    """A tick counts on its day, and unticking takes it off."""

    def test_an_untick_comes_off_its_own_day(self):
        item = self.title()
        self.on(date(2026, 3, 2))
        self.tick(item, 1, 2)
        self.on(date(2026, 3, 3))
        self.tick(item, 3)
        anime_tracking.untick(self.user, item, 1)
        self.assert_episodes(0, 2, 2)
        self.assertEqual(self.lines(), ["2026-03-03 watched 1", "2026-03-02 watched 1, started"])
        self.assert_counter_agrees()

    def test_no_runtime_is_told_and_unknown(self):
        item = self.title(runtime=0)
        self.on(date(2026, 3, 2))
        self.tick(item, 1, 2)
        row = next(row for row in self.calc().rows if row.told)
        self.assertEqual((int(row.told), int(row.amount), row.known, row.counts_title), (2, 0, False, True))
        self.assert_counter_agrees()

    def test_a_rewatch_counts_again(self):
        item = self.title()
        self.on(date(2025, 6, 1))
        self.tick(item, *range(1, 13))
        self.on(date(2026, 2, 1))
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1, 2)
        self.assert_episodes(12, 2, 14)
        self.assert_counter_agrees()


class LogTests(AnimeCalcBase):
    """A log's fills count on the log's calendar date (AN-400, AN-704)."""

    def test_a_log_counts_whole_on_its_date(self):
        item = self.title()
        self.log(item, day=date(2026, 2, 1))
        self.assert_episodes(0, 12, 12)
        self.assertEqual(self.lines(), ["2026-02-01 log +12"])
        log = next(row for row in self.calc().rows if row.log_id)
        self.assertEqual((int(log.amount), log.told_unit, log.known), (12 * RUNTIME, "episode", True))
        self.assert_counter_agrees()

    def test_a_backdated_log_takes_the_ticks_of_its_pass(self):
        """Decision 43 for anime (decision 62): the old counter left them there."""
        item = self.title()
        self.on(date(2026, 3, 1))
        self.tick(item, 1, 2, 3)
        entry = self.log(item, day=date(2025, 12, 20)).entry
        self.assert_episodes(12, 0, 12)
        anime_tracking.delete_log(self.user, entry)
        self.assert_episodes(0, 3, 3)
        self.assert_counter_agrees()

    def test_a_log_dated_after_the_ticks_leaves_them(self):
        item = self.title()
        self.on(date(2026, 3, 1))
        self.tick(item, 1, 2, 3)
        self.on(date(2026, 3, 5))
        self.log(item, day=date(2026, 3, 4))
        self.assert_episodes(0, 12, 12)
        self.assert_counter_agrees()

    def test_a_log_that_fills_nothing_still_counts_its_day_and_title(self):
        item = self.title()
        self.on(date(2026, 3, 1))
        self.tick(item, *range(1, 13))
        self.on(date(2026, 4, 1))
        self.log(item, day=date(2026, 4, 1))
        self.assert_episodes(0, 12, 12)
        log = next(row for row in self.calc().rows if row.log_id)
        self.assertEqual((log.told, log.counts_title, log.is_event), (0, True, True))
        self.assert_counter_agrees()

    def test_deleting_a_log_takes_its_fills_off(self):
        item = self.title()
        entry = self.log(item, day=date(2026, 2, 1)).entry
        anime_tracking.delete_log(self.user, entry)
        self.assert_episodes(0, 0, 0)
        self.assertEqual(self.lines(), [])
        self.assert_counter_agrees()

    def test_a_private_log_counts_only_for_its_owner(self):
        private, followers = self.title(), self.title()
        self.log(private, day=date(2026, 2, 1), visibility="private")
        self.log(followers, day=date(2026, 2, 2), visibility="followers")
        self.assert_episodes(0, 24, 24)
        self.assert_episodes(0, 12, 12, viewer=self.follower)
        self.assert_episodes(0, 0, 0, viewer=self.stranger)
        self.assertEqual(self.lines(self.stranger), [])
        self.assert_counter_agrees()


class MarkTests(AnimeCalcBase):
    """A mark's fills are All Time's pile only, and never a row (decision 11)."""

    def test_the_eye_moves_no_year(self):
        item = self.title()
        anime_tracking.mark_watched(self.user, item)
        self.assert_episodes(0, 0, 12)
        self.assertEqual(self.lines(), [])
        pile = piled(self.calc().piles, ANIME)
        self.assertEqual((pile.titles, pile.known, int(pile.told)), ({item.pk}, {item.pk}, 12))
        self.assert_counter_agrees()

    def test_a_marked_title_logged_as_the_screen_opens_is_a_second_watch(self):
        """Decision 38 for anime: the log opens on after a mark, so 2 watches."""
        item = self.title()
        self.on(date(2026, 1, 15))
        anime_tracking.mark_watched(self.user, item)
        self.on(date(2026, 2, 1))
        entry = self.log(item, day=date(2026, 2, 1)).entry
        self.assertTrue(entry.is_rewatch)
        self.assert_episodes(0, 12, 24)
        anime_tracking.update_log(entry, {"is_rewatch": False})
        self.assert_episodes(0, 12, 12)
        anime_tracking.delete_log(self.user, entry)
        self.assert_episodes(0, 0, 12)
        self.assert_counter_agrees()

    def test_a_marked_title_logged_off_then_on_is_two_watches_not_three(self):
        item = self.title()
        self.on(date(2026, 1, 15))
        anime_tracking.mark_watched(self.user, item)
        self.on(date(2026, 2, 1))
        self.log(item, is_rewatch=False)
        self.assert_episodes(0, 12, 12)
        self.on(date(2026, 2, 2))
        self.log(item, is_rewatch=True)
        self.assert_episodes(0, 24, 24)

    def test_a_marked_single_logged_on_is_two_watches(self):
        item = self.title(total=1)
        self.on(date(2026, 1, 15))
        anime_tracking.mark_watched(self.user, item)
        self.on(date(2026, 2, 1))
        entry = self.log(item, day=date(2026, 2, 1)).entry
        self.assertTrue(entry.is_rewatch)
        self.assert_episodes(0, 1, 2)
        anime_tracking.update_log(entry, {"is_rewatch": False})
        self.assert_episodes(0, 1, 1)
        self.assert_counter_agrees()


    def test_a_stored_number_with_no_viewings_sits_in_the_pile(self):
        item = self.title()
        Anime.objects.bulk_create([Anime(user=self.user, item=item, status=Status.IN_PROGRESS.value, progress=7)])
        self.assert_episodes(0, 0, 7)
        self.assert_counter_agrees()

    def test_an_import_with_no_date_is_the_pile(self):
        item = self.title()
        anime_tracking.import_title(self.user, item, status=Status.IN_PROGRESS.value, count=5)
        self.assert_episodes(0, 0, 5)
        self.assertEqual(self.lines(), [])
        self.assert_counter_agrees()


class RewatchSwitchTests(AnimeCalcBase):
    """Decision 62: anime's Rewatch switch works as a TV season log's (decision 48)."""

    def test_logged_again_with_rewatch_on_adds_the_title_again(self):
        item = self.title()
        self.log(item, day=date(2026, 2, 1))
        self.on(date(2026, 3, 1))
        entry = self.log(item).entry
        self.assertTrue(entry.is_rewatch, "A log exists, so the switch opens on")
        self.assert_episodes(0, 24, 24)
        anime_tracking.update_log(entry, {"is_rewatch": False})
        self.assert_episodes(0, 12, 12)
        anime_tracking.update_log(entry, {"is_rewatch": True})
        anime_tracking.delete_log(self.user, entry)
        self.assert_episodes(0, 12, 12)

    def test_a_rewatch_by_ticks_then_its_finish_log_adds_nothing_more(self):
        """Decision 42: the ticks already counted the rewatch."""
        item = self.title()
        self.on(date(2025, 6, 1))
        self.log(item, day=date(2025, 6, 1))
        self.on(date(2026, 2, 1))
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, *range(1, 13))
        self.on(date(2026, 2, 2))
        self.log(item, is_rewatch=True)
        self.assert_episodes(12, 12, 24)

    def test_a_private_rewatch_log_adds_nothing_for_a_stranger(self):
        item = self.title()
        self.log(item, day=date(2026, 2, 1))
        self.on(date(2026, 3, 1))
        self.log(item, is_rewatch=True, visibility="private")
        self.assert_episodes(0, 24, 24)
        self.assert_episodes(0, 12, 12, viewer=self.stranger)


class MarkMatrixTests(AnimeCalcBase):
    """Matrix 11 on anime: each mark leaves every year and the list as they were."""

    LIFETIME_SECTIONS = ("list_rings", "collections")

    def setUp(self):
        super().setUp()
        # Something counted in each year, so a moved year would show.
        counted = self.title()
        self.on(date(2025, 6, 1))
        self.tick(counted, 1)
        self.on(date(2026, 2, 1))
        self.tick(counted, 2)
        self.on(date(2026, 3, 1))

    def client_of(self, viewer):
        client = APIClient()
        client.force_authenticate(viewer)
        return client

    def snapshot(self):
        seen = []
        for viewer in (self.user, self.follower, self.stranger):
            client = self.client_of(viewer)
            path = "/api/v1/stats/me/summary/" if viewer == self.user else f"/api/v1/users/{self.user.username}/stats/summary/"
            for year in YEARS:
                for tab in ({}, {"media_type": ANIME}):
                    params = {"schema": "2", "start_date": f"{year}-01-01", "end_date": f"{year}-12-31", **tab}
                    response = client.get(path, params)
                    self.assertEqual(response.status_code, 200, response.content)
                    seen.append({key: value for key, value in response.json().items() if key not in self.LIFETIME_SECTIONS})
            response = client.get(f"/api/v1/users/{self.user.username}/activity/")
            self.assertEqual(response.status_code, 200, response.content)
            seen.append(response.json())
        return seen

    def check(self, mark, adds, *, planning_too=True):
        """Run a mark on a new title and on one first set to Planning."""
        for planning in (False, True) if planning_too else (False,):
            with self.subTest(planning=planning):
                item = self.title()
                if planning:
                    anime_tracking.choose(self.user, item, anime_tracking.PLANNING)
                before, value = self.snapshot(), self.number()
                mark(item)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.number(), value + adds * RUNTIME)
                self.assertFalse([row for row in self.calc().rows if row.title_id == item.pk])
                self.assert_counter_agrees(viewers=(self.user,))

    def test_the_eye(self):
        self.check(lambda item: anime_tracking.mark_watched(self.user, item), 12)

    def test_the_eye_through_the_api(self):
        self.check(lambda item: self.action(item, "mark_watched"), 12)

    def test_a_heart(self):
        self.check(lambda item: anime_tracking.set_like(self.user, item, liked=True), 12)

    def test_stars(self):
        self.check(lambda item: anime_tracking.set_rating(self.user, item, Decimal("7.0")), 12)

    def test_mark_watched_up_to_here(self):
        self.check(lambda item: anime_tracking.mark_up_to(self.user, item, 6), 6)

    def test_status_set_to_completed_through_patch(self):
        self.check(lambda item: self.write(item, status=Status.COMPLETED.value), 12)

    def test_an_undated_import(self):
        # An import leaves a title already tracked as it is, so only a new one.
        def imported(item):
            anime_tracking.import_title(self.user, item, status=Status.COMPLETED.value, count=12)

        self.check(imported, 12, planning_too=False)

    def test_unhearting_keeps_the_mark(self):
        item = self.title()
        anime_tracking.set_like(self.user, item, liked=True)
        before, value = self.snapshot(), self.number()
        anime_tracking.set_like(self.user, item, liked=False)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.number(), value)


class AnimeTrackingWritesTests(AnimeCalcBase):
    """Every kind of anime write reads, and the comparison still prints both sides."""

    def test_the_adapter_and_the_comparison_read_every_kind_of_write(self):
        watched, marked, logged, removed, imported = (self.title() for _ in range(5))
        self.tick(watched, 1, 2, 3)
        anime_tracking.untick(self.user, watched, 3)
        anime_tracking.mark_watched(self.user, watched)
        anime_tracking.start_rewatch(self.user, watched)
        self.tick(watched, 1)
        anime_tracking.mark_watched(self.user, marked)
        self.log(logged)
        self.tick(removed, 1)
        anime_tracking.remove(self.user, removed, mutation_id=str(uuid4()))
        anime_tracking.import_title(self.user, imported, status=Status.IN_PROGRESS.value, count=5)

        calculation = rows_for([self.user], self.user, types=[ANIME])
        self.assertEqual(
            {row.title_id for row in calculation.rows} | piled(calculation.piles, ANIME).titles,
            {watched.pk, marked.pk, logged.pk, imported.pk},
        )
        self.assert_counter_agrees()
        printed = io.StringIO()
        call_command("compare_activity_numbers", user=self.user.pk, stdout=printed)
        self.assertNotIn("failed", printed.getvalue())
        self.assertIn(f"user {self.user.pk} anime all:", printed.getvalue())
