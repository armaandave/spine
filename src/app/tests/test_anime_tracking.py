"""The numbered anime laws at the domain level (docs/anime-tracking-contract.md).

Each test cites its law. MAL and AniList are faked (app.tests.anime_fixtures); the server
is on America/New_York and the account calendar on America/Los_Angeles.
"""

import json
from datetime import UTC, date, datetime, timedelta
from unittest import mock
from uuid import uuid4

from django.db import connection
from django.db.models import QuerySet
from django.test.utils import CaptureQueriesContext

from api.services import media as media_service
from app import anime_listing, anime_tracking
from app.anime_tracking import TVConflict, TVInvalid, TVLogBlocks, TVLogFirst, TVStateChanged
from app.models import Anime, AnimeListing, AnimeMark, AnimeViewing, DiaryEntry, Item, MediaLike, TVMutation
from app.tests.anime_fixtures import OCT_25, AnimeTestCase
from app.tests.tv_fixtures import make_user
from app.tv_listing import TVDetailsUnavailable
from lists.models import CustomList
from social.models import Activity, ProgressChange


def days_after(number):
    return datetime(2026, 10, 25, 19, 0, tzinfo=UTC) + timedelta(days=number)


class LibraryTests(AnimeTestCase):
    """AN-000: TV-002, TV-003, TV-005, TV-006, TV-007. AN-001."""

    def test_tv_002_five_sections_status_alone_decides(self):
        item = self.title()
        anime_tracking.choose(self.user, item, "Planning")
        self.assertEqual(self.row(item).status, "Planning")
        self.clock.advance(minutes=1)
        self.tick(item, 1)
        self.assertEqual(self.row(item).status, "In progress")
        anime_tracking.choose(self.user, item, "Paused")
        self.assertEqual(self.row(item).status, "Paused")
        anime_tracking.choose(self.user, item, "Dropped")
        self.assertEqual(self.row(item).status, "Dropped")
        anime_tracking.choose(self.user, item, "In progress")
        self.assertEqual(self.row(item).status, "In progress")
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.row(item).status, "Completed")
        self.assertEqual(Anime.objects.filter(user=self.user, item=item).count(), 1)

    def test_an_001_a_title_tracked_as_tv_is_a_separate_thing(self):
        """A title is one MAL entry; every write finds the same one record (AN-001)."""
        item = self.title()
        self.tick(item, 1)
        anime_tracking.choose(self.user, item, "Paused")
        self.log(item)
        self.assertEqual(Anime.objects.filter(user=self.user).count(), 1)

    def test_tv_003_removal_says_what_it_removes_and_clears_opinions(self):
        item = self.title()
        self.tick(item, 1, 2, 3)
        lists = CustomList.objects.create(owner=self.user, name="Mine")
        lists.items.add(item)
        self.assertEqual(self.state(item)["remove_count"], 3)
        anime_tracking.remove(self.user, item, mutation_id=uuid4())
        self.assertIsNone(self.row(item))
        self.assertEqual(self.viewings(item), [])
        self.assertTrue(lists.items.filter(pk=item.pk).exists())
        self.assertFalse(ProgressChange.objects.filter(actor=self.user, item=item).exists())

    def test_tv_003_removal_is_blocked_while_a_log_exists(self):
        item = self.title()
        self.log(item)
        with self.assertRaises(TVLogBlocks) as raised:
            anime_tracking.remove(self.user, item, mutation_id=uuid4())
        self.assertEqual(raised.exception.message, "Delete this anime's logs first.")
        state = self.state(item)
        self.assertEqual(state["action_reasons"]["remove"], "Delete this anime's logs first.")
        self.assertNotIn("remove", state["available_actions"])

    def test_tv_003_a_stale_removal_says_the_new_count(self):
        item = self.title()
        self.tick(item, 1)
        version = self.row(item).state_version
        self.clock.advance(minutes=1)
        self.tick(item, 2)
        with self.assertRaises(TVStateChanged) as raised:
            anime_tracking.remove(self.user, item, expected_version=version, mutation_id=uuid4())
        self.assertEqual(raised.exception.extra["count"], 2)

    def test_tv_005_the_menu_of_an_untracked_title(self):
        state = self.state(self.title())
        self.assertCountEqual(state["available_actions"], ["watching", "planning", "pause", "drop", "log", "rate"])
        self.assertEqual(state["action_reasons"], {})

    def test_tv_005_resume_takes_watchings_place_when_paused(self):
        item = self.title()
        self.tick(item, 1)
        anime_tracking.choose(self.user, item, "Paused")
        state = self.state(item)
        self.assertIn("resume", state["available_actions"])
        self.assertNotIn("watching", state["available_actions"])
        self.assertEqual(state["selected_action"], "pause")

    def test_tv_005_a_finished_title_offers_rewatch_and_explains_watching(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        state = self.state(item)
        self.assertIn("rewatch", state["available_actions"])
        self.assertEqual(state["action_reasons"]["watching"], "You've watched everything that has aired.")

    def test_tv_005_nothing_aired_explains(self):
        item = self.title(status="Upcoming", start_date="2027-03-12")
        state = self.state(item)
        for action in ("watching", "log", "rate"):
            self.assertEqual(state["action_reasons"][action], "Nothing has aired yet.")
        with self.assertRaises(TVConflict):
            anime_tracking.choose(self.user, item, "In progress")

    def test_tv_006_planning_is_only_while_nothing_is_watched(self):
        item = self.title()
        anime_tracking.choose(self.user, item, "Planning")
        self.clock.advance(minutes=1)
        self.tick(item, 1)
        with self.assertRaises(TVConflict) as raised:
            anime_tracking.choose(self.user, item, "Planning")
        self.assertEqual(raised.exception.message, "You've started this anime. Pause or drop it instead.")
        self.assertEqual(self.state(item)["action_reasons"]["planning"], "You've started this anime. Pause or drop it instead.")

    def test_tv_007_pause_and_drop_keep_the_place(self):
        item = self.title()
        self.tick(item, 1, 2)
        anime_tracking.choose(self.user, item, "Dropped")
        state = self.state(item)
        self.assertEqual((state["status"], state["watched_count"], state["up_next"]["episode_number"]), ("Dropped", 2, 3))
        anime_tracking.choose(self.user, item, "In progress")
        state = self.state(item)
        self.assertEqual((state["status"], state["watched_count"], state["up_next"]["episode_number"]), ("In progress", 2, 3))

    def test_tv_007_pause_and_drop_invent_nothing_with_nothing_watched(self):
        item = self.title()
        anime_tracking.choose(self.user, item, "Paused")
        self.assertEqual(self.row(item).status, "Paused")
        self.assertEqual(self.viewings(item), [])
        self.assertFalse(self.state(item)["started"])

    def test_tv_007_a_dropped_finished_title_returns_to_completed(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        anime_tracking.choose(self.user, item, "Dropped")
        anime_tracking.choose(self.user, item, "In progress")
        self.assertEqual(self.row(item).status, "Completed")


class TickTests(AnimeTestCase):
    """AN-000: TV-100, TV-101, TV-102. AN-103."""

    def test_tv_100_a_tick_is_one_viewing_dated_now_on_the_users_calendar(self):
        self.clock.set(datetime(2026, 10, 25, 21, 0), tz="America/Los_Angeles")
        item = self.title()
        self.tick(item, 1)
        viewing = self.viewings(item)[0]
        # 9 p.m. in Los Angeles is already Oct 26 in New York.
        self.assertEqual(viewing.watched_on, date(2026, 10, 25))
        self.assertEqual(viewing.watched_at, self.clock.moment)
        self.assertEqual(viewing.kind, "tick")

    def test_tv_101_a_retry_adds_nothing(self):
        item = self.title()
        self.assertTrue(self.tick(item, 1).changed)
        self.assertFalse(self.tick(item, 1).changed)
        self.assertEqual(len(self.viewings(item)), 1)

    def test_tv_102_untick_removes_the_latest_unlogged_viewing(self):
        item = self.title()
        anime_tracking.mark_up_to(self.user, item, 1)
        self.clock.advance(minutes=1)
        AnimeViewing.objects.create(anime=self.row(item), episode_number=1, kind="tick", watched_on=OCT_25, watched_at=self.clock.moment)
        anime_tracking.untick(self.user, item, 1)
        rows = self.viewings(item)
        self.assertEqual([row.kind for row in rows], ["fill"])

    def test_tv_102_an_untick_naming_a_gone_viewing_changes_nothing(self):
        item = self.title()
        self.tick(item, 1)
        result = anime_tracking.untick(self.user, item, 1, viewing_id=999999)
        self.assertFalse(result.changed)
        self.assertEqual(len(self.viewings(item)), 1)

    def test_tv_102_a_logged_episode_says_delete_your_log_first(self):
        item = self.title(total=3)
        self.tick(item, 1)
        self.log(item)
        with self.assertRaises(TVLogBlocks) as raised:
            anime_tracking.untick(self.user, item, 1)
        self.assertEqual(raised.exception.message, "Delete your log first.")
        row = next(row for row in self.rows(item)["episodes"] if row["n"] == 1)
        self.assertEqual(row.get("l"), 1)
        self.assertNotIn("u", row)

    def test_an_103_a_tick_past_a_known_total_names_the_total(self):
        item = self.title(total=25)
        with self.assertRaises(TVInvalid) as raised:
            self.tick(item, 26)
        self.assertEqual(raised.exception.message, "This anime has 25 episodes.")
        self.assertIsNone(self.row(item))

    def test_an_103_a_tick_more_than_a_day_before_its_air_time_is_refused(self):
        moment = self.clock.moment
        item = self.title(total=12, status="Airing", next_episode=8, next_air_at=moment + timedelta(days=3))
        with self.assertRaises(TVInvalid) as raised:
            self.tick(item, 8)
        self.assertEqual(raised.exception.message, "This episode hasn't aired yet.")
        self.assertIsNone(self.row(item))

    def test_an_103_a_tick_within_a_day_of_its_air_time_is_accepted(self):
        moment = self.clock.moment
        item = self.title(total=12, status="Airing", next_episode=8, next_air_at=moment + timedelta(hours=20))
        self.tick(item, 8)
        self.assertEqual(self.watched(item), {8})
        # A viewing makes it aired for this user.
        self.assertEqual(self.state(item)["aired_count"], 8)

    def test_an_103_an_undated_future_episode_is_refused(self):
        item = self.airing()
        with self.assertRaises(TVInvalid):
            self.tick(item, 9)

    def test_an_103_the_ceiling_is_past_the_longest_anime(self):
        self.assertEqual(anime_tracking.MAX_EPISODE, 20000)

    def test_an_103_a_tick_past_the_ceiling_is_refused_with_no_numbers(self):
        item = self.title(total=None)
        with self.assertRaises(TVInvalid):
            anime_tracking.tick(self.user, item, 3000000000)
        self.assertIsNone(self.row(item))

    def test_an_103_mark_up_to_past_the_ceiling_is_refused_and_writes_nothing(self):
        item = self.title(total=None)
        with self.assertRaises(TVInvalid):
            anime_tracking.mark_up_to(self.user, item, 30000)
        self.assertFalse(AnimeViewing.objects.exists())

    def test_an_103_a_number_past_the_total_is_refused_before_anything_is_built(self):
        item = self.title(total=12)
        with (
            mock.patch.object(anime_tracking, "_next_unwatched", side_effect=AssertionError("It built the list")),
            self.assertRaises(TVInvalid) as refused,
        ):
            anime_tracking.write_progress(self.user, item, 8000000)
        self.assertIn("12 episodes", refused.exception.message)

    def test_an_103_a_number_past_the_ceiling_is_refused_before_anything_is_built(self):
        item = self.title(total=None)
        with (
            mock.patch.object(anime_tracking, "_next_unwatched", side_effect=AssertionError("It built the list")),
            self.assertRaises(TVInvalid),
        ):
            anime_tracking.write_progress(self.user, item, 2000000000)
        self.assertIsNone(self.row(item))

    def test_an_103_with_no_total_and_no_schedule_any_number_is_accepted(self):
        item = self.title(total=None, status="Airing")
        self.tick(item, 500)
        self.assertEqual(self.watched(item), {500})

    def test_an_103_a_log_or_mark_with_nothing_aired_is_refused(self):
        item = self.title(status="Upcoming", start_date="2027-03-12")
        for write in (lambda: anime_tracking.mark_watched(self.user, item), lambda: self.log(item)):
            with self.assertRaises(TVInvalid) as raised:
                write()
            self.assertEqual(raised.exception.message, "Premieres Mar 12, 2027")
        self.assertIsNone(self.row(item))

    def test_an_103_a_viewing_past_a_fallen_total_stays_and_counts_for_nothing(self):
        title = self.fake.add(total=12)
        item = self.title(title=title)
        self.tick(item, 11, 12)
        title.total = 10
        anime_tracking.refresh_after_calendar([item])
        row = self.row(item)
        self.assertEqual(row.progress, 0)
        self.assertEqual(self.watched(item), {11, 12})
        self.assertEqual(self.state(item)["aired_count"], 10)


class AiredTests(AnimeTestCase):
    """AN-100, AN-101, AN-102."""

    def test_an_100_mal_finished_within_the_total(self):
        self.assertEqual(self.state(self.title(total=3, status="Finished"))["aired_count"], 3)

    def test_an_100_the_schedule_says_its_time_has_passed(self):
        moment = self.clock.moment
        item = self.title(total=12, status="Airing", schedule={5: moment - timedelta(hours=1), 6: moment + timedelta(days=1)})
        state = self.state(item)
        self.assertEqual(state["aired_count"], 5)
        self.assertEqual(state["next_airing"]["episode_number"], 6)

    def test_an_100_anilist_names_a_later_episode(self):
        self.assertEqual(self.state(self.airing(aired=7))["aired_count"], 7)

    def test_an_100_a_viewing_makes_it_aired(self):
        item = self.airing(aired=2)
        self.clock.set(datetime(2026, 10, 29, 0, 0, tzinfo=UTC))
        self.tick(item, 3)
        self.assertEqual(self.state(item)["aired_count"], 3)

    def test_an_100_a_total_with_no_schedule_counts_every_listed_episode(self):
        self.assertEqual(self.state(self.title(total=13, status="Airing"))["aired_count"], 13)

    def test_an_100_correction_an_upcoming_title_has_nothing_aired(self):
        """MAL's own word beats the no-schedule rule: it says it hasn't started."""
        state = self.state(self.title(total=13, status="Upcoming", start_date="2027-01-05"))
        self.assertEqual(state["aired_count"], 0)
        self.assertEqual(state["eye"]["tap"], "nothing_aired")

    def test_an_100_a_write_with_no_copy_is_refused_with_a_retryable_message(self):
        item = self.title()
        self.fake.fail()
        with self.assertRaises(TVDetailsUnavailable) as raised:
            self.tick(item, 1)
        self.assertEqual(raised.exception.message, "Anime details are unavailable right now")
        self.assertIsNone(self.row(item))

    def test_an_100_a_stored_copy_answers_when_providers_are_down(self):
        item = self.title(total=3)
        self.state(item)
        self.fake.fail()
        self.tick(item, 1)
        self.assertEqual(self.row(item).progress, 1)

    def test_an_101_rows_are_aired_then_the_unaired_spine_knows_of(self):
        moment = self.clock.moment
        item = self.title(
            total=4,
            status="Airing",
            next_episode=3,
            next_air_at=moment + timedelta(days=2),
            schedule={3: moment + timedelta(days=2)},
            episodes={1: ("To You", "https://example.com/1.jpg")},
        )
        rows = self.rows(item)["episodes"]
        self.assertEqual(rows[0], {"n": 1, "t": "To You", "i": "https://example.com/1.jpg", "a": 1})
        self.assertEqual(rows[1], {"n": 2, "a": 1})
        self.assertEqual(rows[2], {"n": 3, "d": "2026-10-27"})
        self.assertEqual(rows[3], {"n": 4})

    def test_an_101_rows_never_wait_a_failed_fetch_leaves_the_number(self):
        self.fake.fail("anilist")
        item = self.title(total=3)
        self.assertEqual(self.rows(item)["episodes"][1], {"n": 2, "a": 1})

    def test_an_101_a_row_names_the_viewing_an_untick_removes(self):
        item = self.title(total=3)
        self.tick(item, 1)
        viewing = self.viewings(item)[0]
        self.assertEqual(self.rows(item)["episodes"][0], {"n": 1, "a": 1, "w": 1, "u": viewing.pk})

    def test_an_101_one_piece_stays_cheap_in_queries_and_bytes(self):
        """Over 1,100 episodes, no total: no per-row query, no per-episode call, small JSON."""
        item = self.runner(
            aired=1180,
            episodes={number: (f"Title {number}", f"https://example.com/{number}.jpg") for number in range(62, 131)},
        )
        self.tick(item, 1, 2, 3)
        self.state(item)
        self.fake.calls.clear()
        with CaptureQueriesContext(connection) as queries:
            rows = self.rows(item)
        self.assertEqual(self.fake.calls, [])
        self.assertLess(len(queries), 8)
        self.assertEqual(rows["count"], 1181)
        self.assertEqual(rows["up_next"], 4)
        self.assertLess(len(json.dumps(rows, separators=(",", ":"))), 40_000)

    def test_an_101_a_mark_of_a_long_runner_is_one_request_in_bulk(self):
        item = self.runner(aired=1180)
        self.state(item)
        self.fake.calls.clear()
        with CaptureQueriesContext(connection) as queries:
            anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.fake.calls, [])
        self.assertLess(len(queries), 40)
        self.assertEqual(self.row(item).progress, 1180)

    def test_an_102_progress_is_distinct_aired_watched_in_the_pass(self):
        item = self.title(total=10)
        self.tick(item, 1, 2, 3, 5)
        state = self.state(item)
        self.assertEqual((state["watched_count"], state["aired_count"]), (4, 10))
        self.assertEqual(self.row(item).progress, 4)

    def test_an_102_every_write_keeps_the_row_equal_to_the_count(self):
        item = self.title(total=10)
        writes = [
            (lambda: self.tick(item, 1), 1),
            (lambda: anime_tracking.mark_up_to(self.user, item, 4), 4),
            (lambda: anime_tracking.untick(self.user, item, 4), 3),
            (lambda: anime_tracking.apply_written_state(self.user, item, {"progress": 5}, entry_point=anime_tracking.LEGACY_APP), 5),
            (lambda: self.log(item), 10),
        ]
        for write, expected in writes:
            self.clock.advance(minutes=1)
            write()
            self.assertEqual(self.row(item).progress, expected)

    def test_an_102_repeat_and_unaired_episodes_dont_count(self):
        item = self.airing(aired=3, total=12)
        self.tick(item, 1)
        self.assertEqual(self.row(item).progress, 1)
        self.log(item)
        self.assertEqual(self.row(item).progress, 3)


class UpNextTests(AnimeTestCase):
    """AN-000: TV-105. AN-700's facts."""

    def test_tv_105_up_next_follows_the_latest_tick(self):
        item = self.title(total=10)
        self.tick(item, 3)
        self.assertEqual(self.state(item)["up_next"]["episode_number"], 4)
        self.clock.advance(minutes=1)
        self.tick(item, 7)
        self.assertEqual(self.state(item)["up_next"]["episode_number"], 8)

    def test_tv_105_after_the_last_it_wraps_to_the_first_unwatched(self):
        item = self.title(total=5)
        self.tick(item, 5)
        self.assertEqual(self.state(item)["up_next"]["episode_number"], 1)

    def test_tv_105_up_next_carries_the_rows_title_and_image(self):
        item = self.title(total=5, episodes={2: ("Two", "https://example.com/2.jpg")})
        self.tick(item, 1)
        up_next = self.state(item)["up_next"]
        self.assertEqual((up_next["title"], up_next["image_url"]), ("Two", "https://example.com/2.jpg"))

    def test_an_700_stored_values_give_every_card_line(self):
        item = self.title(total=5)
        self.tick(item, 1)
        row = self.row(item)
        self.assertEqual((row.up_next_episode, row.watched_count, row.aired_count), (2, 1, 5))
        anime_tracking.choose(self.user, item, "Paused")
        payload = anime_tracking.list_payload(self.row(item))
        self.assertEqual((payload["status"], payload["up_next"]["episode_number"]), ("Paused", 2))

    def test_an_700_the_gap_is_named_on_the_list_too(self):
        item = self.title(total=4)
        self.tick(item, 1, 2, 4)
        self.assertEqual(anime_tracking.list_payload(self.row(item))["first_gap_episode"], 3)


class EarlierQuestionTests(AnimeTestCase):
    """AN-000: TV-107, TV-108."""

    def test_tv_107_a_first_tick_of_e1_asks_nothing(self):
        self.assertIsNone(self.tick(self.title(), 1).earlier_unwatched)

    def test_tv_107_the_question_is_used_up_answered_or_not(self):
        item = self.title()
        self.assertIsNotNone(self.tick(item, 5).earlier_unwatched)
        self.clock.advance(minutes=1)
        anime_tracking.untick(self.user, item, 5)
        # It left the library, so it starts fresh.
        self.assertIsNotNone(self.tick(item, 5).earlier_unwatched)

    def test_tv_107_a_choice_keeps_the_used_up_question_through_an_undo(self):
        item = self.title()
        anime_tracking.choose(self.user, item, "Planning")
        self.clock.advance(minutes=1)
        self.assertIsNotNone(self.tick(item, 5).earlier_unwatched)
        self.clock.advance(minutes=1)
        anime_tracking.untick(self.user, item, 5)
        self.assertEqual(self.row(item).status, "Planning")
        self.clock.advance(minutes=1)
        self.assertIsNone(self.tick(item, 5).earlier_unwatched)

    def test_tv_107_webhooks_and_installed_builds_dont_show_it(self):
        item = self.title()
        result = anime_tracking.tick(self.user, item, 5, entry_point=anime_tracking.LEGACY_APP)
        self.assertIsNone(result.earlier_unwatched)
        self.assertIsNotNone(self.row(item).earlier_question_at)
        self.assertEqual(self.watched(item), {5})

    def test_tv_107_yes_needs_a_mutation_id_and_applies_once(self):
        item = self.title()
        self.tick(item, 5)
        ref = {"episode_number": 1}
        with self.assertRaises(TVInvalid):
            anime_tracking.mark_earlier(self.user, item, from_ref=ref, to_ref={"episode_number": 4})
        mutation = uuid4()
        anime_tracking.mark_earlier(self.user, item, from_ref=ref, to_ref={"episode_number": 4}, mutation_id=mutation)
        replay = anime_tracking.mark_earlier(self.user, item, from_ref=ref, to_ref={"episode_number": 4}, mutation_id=mutation)
        self.assertTrue(replay.replayed)
        self.assertEqual(AnimeMark.objects.filter(anime=self.row(item)).count(), 1)
        self.assertEqual(len(self.viewings(item)), 5)

    def test_tv_107_yes_after_ticking_the_last_first_opens_the_log_screen(self):
        item = self.title(total=5)
        first = self.tick(item, 5)
        self.assertIsNone(first.finished)
        self.assertTrue(first.earlier_unwatched["offer_log"])
        yes = anime_tracking.mark_earlier(
            self.user,
            item,
            from_ref=first.earlier_unwatched["from"],
            to_ref=first.earlier_unwatched["to"],
            offer_log=True,
            mutation_id=uuid4(),
        )
        self.assertEqual(yes.finished["level"], "title")
        self.assertEqual(self.row(item).status, "Completed")

    def test_tv_108_mark_up_to_marks_that_episode_and_every_unwatched_before_it(self):
        item = self.title(total=10)
        self.tick(item, 2)
        self.clock.advance(minutes=1)
        anime_tracking.mark_up_to(self.user, item, 4)
        rows = {viewing.episode_number: viewing.kind for viewing in self.viewings(item)}
        self.assertEqual(rows, {1: "fill", 2: "tick", 3: "fill", 4: "fill"})
        self.assertEqual(AnimeMark.objects.filter(anime=self.row(item), origin="up_to").count(), 1)
        self.assertEqual(self.logs(item), [])

    def test_tv_108_not_on_an_unaired_episode(self):
        item = self.airing(aired=3)
        with self.assertRaises(TVInvalid):
            anime_tracking.mark_up_to(self.user, item, 5)

    def test_tv_108_rows_give_the_first_unwatched_for_the_prompt(self):
        item = self.title(total=10)
        self.tick(item, 2)
        self.assertEqual(self.rows(item)["first_unwatched"], 1)


class FinishingTests(AnimeTestCase):
    """AN-200 to AN-203. TV-203, TV-207, TV-208."""

    def test_an_200_finished_needs_every_episode_watched_and_the_last_aired(self):
        item = self.title(total=3)
        self.tick(item, 3)
        self.assertFalse(self.row(item).finished)
        self.tick(item, 1, 2)
        self.assertTrue(self.row(item).finished)
        self.assertEqual(self.row(item).status, "Completed")

    def test_an_200_a_gap_keeps_it_unfinished(self):
        item = self.title(total=12)
        self.tick(item, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12)
        state = self.state(item)
        self.assertEqual((state["watched_count"], state["first_gap_episode"], state["status"]), (11, 4, "In progress"))

    def test_an_200_a_finished_title_stays_paused_if_chosen_since(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        anime_tracking.choose(self.user, item, "Paused")
        state = self.state(item)
        self.assertEqual((state["status"], state["finished"]), ("Paused", True))

    def test_an_201_caught_up_stays_watching_and_leaves_in_progress(self):
        item = self.airing(aired=2)
        self.tick(item, 1, 2)
        state = self.state(item)
        self.assertEqual((state["status"], state["caught_up"], state["up_next"]), ("In progress", True, None))
        self.assertEqual(state["next_airing"], {"episode_number": 3, "air_date": "2026-10-29"})
        response = self.api.get("/api/v1/tracking/", {"media_type": "anime", "continue_watching": "true"})
        self.assertEqual(response.data["results"], [])

    def test_an_201_a_long_runner_is_never_finished_by_ticking(self):
        item = self.runner(aired=2)
        self.tick(item, 1, 2)
        row = self.row(item)
        self.assertEqual((row.status, row.finished, row.caught_up), ("In progress", False, True))
        self.assertIsNone(self.tick(item, 1).finished)

    def test_an_202_a_new_episode_reopens_a_caught_up_title(self):
        title = self.fake.add(total=12, status="Airing", next_episode=3, next_air_at=days_after(4), schedule={3: days_after(4)})
        item = self.title(title=title)
        self.tick(item, 1, 2)
        self.assertTrue(self.row(item).caught_up)
        self.clock.set(days_after(5))
        title.next_episode = 4
        title.next_air_at = days_after(11)
        title.schedule = {3: days_after(4), 4: days_after(11)}
        anime_tracking.refresh_after_calendar([item])
        row = self.row(item)
        self.assertEqual((row.status, row.caught_up, row.up_next_episode, row.progress), ("In progress", False, 3, 2))
        response = self.api.get("/api/v1/tracking/", {"media_type": "anime", "continue_watching": "true"})
        self.assertEqual([result["tracking"]["anime"]["up_next"]["episode_number"] for result in response.data["results"]], [3])

    def test_an_202_the_hourly_job_reopens_from_the_stored_copy(self):
        item = self.airing(aired=2)
        self.tick(item, 1, 2)
        self.clock.set(datetime(2026, 10, 29, 16, 0, tzinfo=UTC))
        self.fake.calls.clear()
        self.assertEqual(anime_tracking.refresh_due_titles(), 1)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.row(item).up_next_episode, 3)

    def test_an_202_a_raised_total_reopens_a_finished_title(self):
        title = self.fake.add(total=2)
        item = self.title(title=title)
        self.tick(item, 1, 2)
        self.assertEqual(self.row(item).status, "Completed")
        title.total = 3
        anime_tracking.refresh_after_calendar([item])
        row = self.row(item)
        self.assertEqual((row.status, row.finished, row.up_next_episode), ("In progress", False, 3))

    def test_an_202_a_mark_covers_only_what_had_aired(self):
        title = self.fake.add(total=12, status="Airing", next_episode=3, next_air_at=days_after(4))
        item = self.title(title=title)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.watched(item), {1, 2})
        self.clock.set(days_after(5))
        title.next_episode = 4
        title.next_air_at = days_after(11)
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual(self.watched(item), {1, 2})
        self.assertEqual(self.row(item).up_next_episode, 3)

    def test_an_202_planning_paused_and_dropped_dont_move(self):
        title = self.fake.add(total=12, status="Airing", next_episode=3, next_air_at=days_after(4))
        item = self.title(title=title)
        self.tick(item, 1, 2)
        anime_tracking.choose(self.user, item, "Paused")
        self.clock.set(days_after(5))
        title.next_episode = 4
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual(self.row(item).status, "Paused")

    def test_an_203_a_tick_that_finishes_names_the_log_screen_dated_that_day(self):
        self.clock.set(datetime(2026, 10, 25, 21, 0), tz="America/Los_Angeles")
        item = self.title(total=2)
        self.tick(item, 1)
        finished = self.tick(item, 2).finished
        target = finished["composer"]["targets"][0]
        self.assertEqual(target["date"], "2026-10-25")
        self.assertFalse(target["is_rewatch"])

    def test_an_203_it_doesnt_open_for_marks_logs_webhooks_or_installed_builds(self):
        item = self.title(total=2)
        self.tick(item, 1)
        self.assertIsNone(anime_tracking.mark_watched(self.user, item).finished)
        other = self.title(total=1)
        self.assertIsNone(self.log(other).finished)
        third = self.title(total=2)
        self.tick(third, 1)
        self.assertIsNone(anime_tracking.tick(self.user, third, 2, entry_point=anime_tracking.LEGACY_APP).finished)
        fourth = self.title(total=2)
        self.tick(fourth, 1)
        self.assertIsNone(anime_tracking.webhook_event(self.user, fourth, 2, event="watched", at=self.clock.moment).finished)

    def test_an_203_it_doesnt_open_when_a_log_exists_in_this_pass(self):
        title = self.fake.add(total=3, status="Airing", next_episode=3, next_air_at=days_after(4))
        item = self.title(title=title)
        self.log(item)
        self.clock.set(days_after(5))
        # The log covers what had aired; E3 is a new tick that finishes the title.
        result = self.tick(item, 3)
        self.assertEqual(result.row.status, "Completed")
        self.assertIsNone(result.finished)

    def test_an_203_a_new_pass_can_open_it_again(self):
        item = self.title(total=2)
        self.tick(item, 1)
        self.assertIsNotNone(self.tick(item, 2).finished)
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1)
        self.assertIsNotNone(self.tick(item, 2).finished)

    def test_an_203_during_a_rewatch_it_opens_with_rewatch_on(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1)
        target = self.tick(item, 2).finished["composer"]["targets"][0]
        self.assertTrue(target["is_rewatch"])

    def test_tv_203_only_the_user_ends_a_choice(self):
        title = self.fake.add(total=2)
        item = self.title(title=title)
        anime_tracking.choose(self.user, item, "Dropped")
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual(self.row(item).status, "Dropped")
        self.tick(item, 1)
        self.assertEqual(self.row(item).status, "In progress")

    def test_tv_207_working_out_twice_changes_nothing(self):
        item = self.title(total=5)
        self.tick(item, 1)
        row = self.row(item)
        version = row.state_version
        history = row.history.count()
        anime_tracking._lock_and_reconcile(row.pk)  # noqa: SLF001
        anime_tracking._lock_and_reconcile(row.pk)  # noqa: SLF001
        row.refresh_from_db()
        self.assertEqual(row.state_version, version)
        self.assertEqual(row.history.count(), history)

    def test_tv_207_lists_read_stored_values_with_no_provider_call(self):
        item = self.title(total=5)
        self.tick(item, 1)
        self.fake.calls.clear()
        response = self.api.get("/api/v1/tracking/", {"media_type": "anime"})
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(response.data["results"][0]["tracking"]["anime"]["watched_count"], 1)

    def test_tv_208_undoing_the_write_that_ended_a_choice_restores_it(self):
        item = self.title()
        anime_tracking.choose(self.user, item, "Planning")
        self.clock.advance(minutes=1)
        anime_tracking.mark_watched(self.user, item)
        self.clock.advance(minutes=1)
        anime_tracking.unmark(self.user, item)
        self.assertEqual(self.row(item).status, "Planning")

    def test_tv_208_watching_chosen_first_keeps_the_title_in_watching(self):
        item = self.title()
        anime_tracking.choose(self.user, item, "In progress")
        self.clock.advance(minutes=1)
        self.tick(item, 1)
        self.clock.advance(minutes=1)
        anime_tracking.untick(self.user, item, 1)
        self.assertEqual(self.row(item).status, "In progress")
        self.assertEqual(self.row(item).progress, 0)

    def test_tv_208_leaving_the_library_clears_stars_and_the_heart(self):
        item = self.title(total=2)
        anime_tracking.set_like(self.user, item, True)
        anime_tracking.set_rating(self.user, item, 8)
        anime_tracking.unmark(self.user, item)
        self.assertIsNone(self.row(item))
        self.assertFalse(self.liked(item))


class EyeTests(AnimeTestCase):
    """AN-300."""

    def test_an_300_the_eye_marks_the_rest_as_one_mark_with_no_log_and_no_feed_item(self):
        item = self.title(total=5)
        self.tick(item, 1)
        activity_before = len(self.activity())
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(AnimeMark.objects.filter(anime=self.row(item), origin="eye").count(), 1)
        self.assertEqual(self.logs(item), [])
        self.assertEqual(len(self.activity()), activity_before)

    def test_an_300_caught_up_when_more_is_coming(self):
        item = self.airing(aired=3)
        anime_tracking.mark_watched(self.user, item)
        state = self.state(item)
        self.assertEqual((state["status"], state["caught_up"], state["eye"]["filled"]), ("In progress", True, True))

    def test_an_300_nothing_aired_says_when_it_premieres(self):
        self.assertEqual(self.state(self.title(status="Upcoming", start_date="2027-03-12"))["eye"]["message"], "Premieres Mar 12, 2027")
        self.assertEqual(self.state(self.title(status="Upcoming", start_date="2026-12-12"))["eye"]["message"], "Premieres Dec 12")
        self.assertEqual(self.state(self.title(status="Upcoming", start_date=None))["eye"]["message"], "Nothing has aired yet.")

    def test_an_300_during_a_rewatch_it_marks_the_rest_and_finishes_it(self):
        item = self.title(total=3)
        self.tick(item, 1, 2, 3)
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1)
        result = anime_tracking.mark_watched(self.user, item)
        self.assertIsNone(result.row.rewatch_started_at)
        self.assertEqual(result.row.status, "Completed")

    def test_an_300_case_1_a_log_says_delete_your_log_first(self):
        item = self.title(total=3)
        self.log(item)
        self.assertEqual(self.state(item)["eye"]["tap"], "blocked")
        with self.assertRaises(TVLogBlocks) as raised:
            anime_tracking.unmark(self.user, item)
        self.assertEqual(raised.exception.message, "Delete your log first.")
        self.assertEqual(len(self.watched(item)), 3)

    def test_an_300_case_2_mark_fills_go_and_ticks_stay(self):
        item = self.title(total=3)
        self.tick(item, 1)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.state(item)["eye"]["tap"], "unmark_marks")
        anime_tracking.unmark(self.user, item)
        self.assertEqual(self.watched(item), {1})
        self.assertFalse(AnimeMark.objects.filter(anime=self.row(item)).exists())

    def test_an_300_case_3_it_asks_and_removes_every_viewing(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        eye = self.state(item)["eye"]
        self.assertEqual((eye["tap"], eye["prompt"]), ("unmark_confirm", "Mark all 2 episodes unwatched?"))
        with self.assertRaises(TVInvalid):
            anime_tracking.unmark(self.user, item)
        version = self.row(item).state_version
        with self.assertRaises(TVStateChanged):
            anime_tracking.unmark(self.user, item, confirm=True, expected_version=version - 1)
        anime_tracking.unmark(self.user, item, confirm=True, expected_version=version)
        self.assertIsNone(self.row(item))

    def test_an_300_undo_works_the_state_out_again(self):
        item = self.title(total=2)
        anime_tracking.choose(self.user, item, "Paused")
        self.clock.advance(minutes=1)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.row(item).status, "Completed")
        self.clock.advance(minutes=1)
        anime_tracking.unmark(self.user, item)
        self.assertEqual(self.row(item).status, "Paused")


class OpinionTests(AnimeTestCase):
    """AN-301."""

    def test_an_301_stars_on_a_title_not_started_mark_it(self):
        item = self.title(total=3)
        result = anime_tracking.set_rating(self.user, item, 8)
        row = result.row
        self.assertEqual((row.status, str(row.score), row.rating_source_id), ("Completed", "8.0", None))
        self.assertEqual(self.watched(item), {1, 2, 3})
        self.assertIsNone(result.finished)
        self.assertEqual(Activity.objects.filter(actor=self.user, verb="rating_updated").count(), 1)
        self.assertEqual(self.logs(item), [])

    def test_an_301_a_heart_on_a_title_not_started_marks_it_and_the_eye_undoes_it(self):
        item = self.title(total=3)
        anime_tracking.set_like(self.user, item, True)
        self.assertEqual(self.row(item).status, "Completed")
        self.assertTrue(self.liked(item))
        self.assertTrue(self.row(item).like_is_independent)
        self.assertFalse(Activity.objects.filter(actor=self.user, verb="rating_updated").exists())
        anime_tracking.unmark(self.user, item)
        self.assertIsNone(self.row(item))

    def test_an_301_the_state_says_whether_the_heart_is_on(self):
        item = self.title(total=3)
        anime_tracking.choose(self.user, item, "Planning")
        self.assertFalse(self.state(item)["liked"])
        anime_tracking.set_like(self.user, item, True)
        self.assertTrue(self.state(item)["liked"])
        self.assertEqual(self.row(item).status, "Completed")
        undone = anime_tracking.write_envelope(anime_tracking.unmark(self.user, item))
        self.assertEqual(self.row(item).status, "Planning", "The eye undoes the mark and the choice comes back")
        self.assertFalse(undone["anime"]["liked"], "The heart goes with it")

    def test_an_301_a_title_not_started_on_an_airing_title_is_caught_up(self):
        item = self.airing(aired=3)
        anime_tracking.set_rating(self.user, item, 8)
        row = self.row(item)
        self.assertEqual((row.status, row.caught_up), ("In progress", True))

    def test_an_301_planning_with_nothing_watched_isnt_started(self):
        item = self.title(total=3)
        anime_tracking.choose(self.user, item, "Planning")
        self.assertEqual(self.state(item)["opinion_action"], "mark")
        self.clock.advance(minutes=1)
        anime_tracking.set_rating(self.user, item, 7)
        self.assertEqual(self.row(item).status, "Completed")

    def test_an_301_partway_refuses_and_changes_nothing(self):
        item = self.title(total=3)
        self.tick(item, 1)
        version = self.row(item).state_version
        for write in (
            lambda: anime_tracking.set_rating(self.user, item, 8),
            lambda: anime_tracking.set_like(self.user, item, True),
        ):
            with self.assertRaises(TVLogFirst) as raised:
                write()
            self.assertEqual(raised.exception.message, "Log this first.")
        row = self.row(item)
        self.assertEqual((row.score, row.state_version), (None, version))
        self.assertFalse(self.liked(item))

    def test_an_301_watching_chosen_with_nothing_watched_is_partway(self):
        item = self.title(total=3)
        anime_tracking.choose(self.user, item, "In progress")
        self.assertEqual(self.state(item)["opinion_action"], "log")

    def test_an_301_finished_or_caught_up_only_saves(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        before = self.row(item).status
        self.assertEqual(self.state(item)["opinion_action"], "save")
        anime_tracking.set_rating(self.user, item, 6)
        anime_tracking.set_like(self.user, item, True)
        row = self.row(item)
        self.assertEqual((row.status, str(row.score)), (before, "6.0"))
        self.assertEqual(len(self.viewings(item)), 2)

    def test_an_301_only_saving_never_ends_a_choice(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        anime_tracking.choose(self.user, item, "Dropped")
        anime_tracking.set_rating(self.user, item, 6)
        self.assertEqual(self.row(item).status, "Dropped")

    def test_an_301_during_a_rewatch_partway_opens_the_log_with_rewatch_on(self):
        item = self.title(total=3)
        self.tick(item, 1, 2, 3)
        anime_tracking.start_rewatch(self.user, item)
        state = self.state(item)
        self.assertEqual(state["opinion_action"], "log")
        self.assertTrue(state["composer"]["targets"][0]["is_rewatch"])
        with self.assertRaises(TVLogFirst):
            anime_tracking.set_rating(self.user, item, 8)

    def test_an_301_clearing_is_only_that_at_any_time(self):
        item = self.title(total=3)
        anime_tracking.set_rating(self.user, item, 8)
        anime_tracking.set_like(self.user, item, True)
        self.clock.advance(minutes=1)
        anime_tracking.untick(self.user, item, 3)
        self.assertEqual(self.state(item)["opinion_action"], "log")
        anime_tracking.set_rating(self.user, item, None)
        anime_tracking.set_like(self.user, item, False)
        row = self.row(item)
        self.assertEqual((row.score, row.status), (None, "In progress"))
        self.assertEqual(len(self.viewings(item)), 2)

    def test_an_301_nothing_aired_the_heart_saves_and_stars_are_unavailable(self):
        item = self.title(status="Upcoming")
        anime_tracking.set_like(self.user, item, True)
        self.assertTrue(self.liked(item))
        self.assertIsNone(self.row(item))
        self.assertEqual(self.state(item)["opinion_action"], "save")
        with self.assertRaises(TVConflict) as raised:
            anime_tracking.set_rating(self.user, item, 8)
        self.assertEqual(raised.exception.message, "Nothing has aired yet.")

    def test_an_301_a_value_already_set_refuses_nothing(self):
        item = self.title(total=3)
        anime_tracking.set_rating(self.user, item, 8)
        anime_tracking.set_like(self.user, item, True)
        self.clock.advance(minutes=1)
        anime_tracking.untick(self.user, item, 3)
        anime_tracking.set_rating(self.user, item, 8)
        anime_tracking.set_like(self.user, item, True)
        self.assertEqual(self.row(item).progress, 2)

    def test_tv_503_the_community_rating_counts_each_person_once(self):
        item = self.title(total=2)
        anime_tracking.set_rating(self.user, item, 8)
        anime_tracking.create_log(self.user, item, consumed_at=OCT_25, rating=6)
        anime_tracking.create_log(self.user, item, consumed_at=OCT_25, rating=4, is_rewatch=True)
        stats = media_service.community_stats(source=item.source, media_type="anime", media_id=item.media_id)
        self.assertEqual((stats["rating_count"], stats["diary_count"]), (1, 2))

    def test_tv_504_off_scale_ratings_are_refused(self):
        item = self.title(total=3)
        for value in (0, 11, "7.5", "x"):
            with self.assertRaises(TVInvalid):
                anime_tracking.set_rating(self.user, item, value)
        self.assertIsNone(self.row(item))

    def test_tv_800_an_import_on_a_partway_title_only_sets_the_value(self):
        """Imports never open anything."""
        item = self.title(total=3)
        self.tick(item, 1)
        anime_tracking.set_rating(self.user, item, 8, entry_point=anime_tracking.IMPORT)
        row = self.row(item)
        self.assertEqual((str(row.score), row.progress), ("8.0", 1))


class LogTests(AnimeTestCase):
    """AN-400. TV-404 to TV-407, TV-501."""

    def test_an_400_a_log_is_one_entry_fills_and_a_stamp(self):
        item = self.title(total=3)
        self.tick(item, 1)
        result = self.log(item, rating=8, liked=True, review="Good")
        entry = result.entry
        self.assertEqual(entry.consumed_at, datetime(2026, 10, 25, tzinfo=UTC))
        self.assertEqual(entry.progress_snapshot["anime"], {"v": 1, "pass": 1, "covers": [[1, 3]]})
        fills = [viewing for viewing in self.viewings(item) if viewing.kind == "fill"]
        self.assertEqual({viewing.episode_number for viewing in fills}, {2, 3})
        self.assertTrue(all(viewing.diary_entry_id == entry.pk for viewing in fills))
        row = self.row(item)
        self.assertEqual((row.status, str(row.score), row.rating_source_id, row.like_source_id), ("Completed", "8.0", entry.pk, entry.pk))
        self.assertTrue(self.liked(item))

    def test_an_400_a_log_ends_a_choice(self):
        item = self.title(total=3)
        anime_tracking.choose(self.user, item, "Dropped")
        self.clock.advance(minutes=1)
        self.log(item)
        self.assertEqual(self.row(item).status, "Completed")

    def test_an_400_never_in_the_future_on_the_users_calendar(self):
        self.clock.set(datetime(2026, 10, 25, 21, 0), tz="America/Los_Angeles")
        item = self.title(total=3)
        with self.assertRaises(TVInvalid) as raised:
            self.log(item, day=date(2026, 10, 26))
        self.assertEqual(raised.exception.message, "Log dates can't be in the future.")
        self.assertIsNone(self.row(item))
        self.log(item, day=date(2026, 10, 25))

    def test_tv_407_a_datetime_converts_to_the_users_calendar(self):
        item = self.title(total=3)
        result = self.log(item, day=datetime(2026, 10, 25, 2, 0, tzinfo=UTC))
        # 2 a.m. UTC on Oct 25 is the evening of Oct 24 in Los Angeles.
        self.assertEqual(result.entry.consumed_at, datetime(2026, 10, 24, tzinfo=UTC))

    def test_tv_404_the_rewatch_default(self):
        fresh = self.title(total=2)
        self.assertFalse(self.state(fresh)["composer"]["targets"][0]["is_rewatch"])
        ticked = self.title(total=2)
        self.tick(ticked, 1)
        self.assertFalse(self.state(ticked)["composer"]["targets"][0]["is_rewatch"])
        marked = self.title(total=2)
        anime_tracking.mark_watched(self.user, marked)
        self.assertTrue(self.state(marked)["composer"]["targets"][0]["is_rewatch"])
        logged = self.title(total=2)
        self.log(logged)
        self.assertTrue(self.state(logged)["composer"]["targets"][0]["is_rewatch"])

    def test_an_400_the_toggle_outside_a_rewatch_is_only_the_badge(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        result = self.log(item, is_rewatch=True)
        self.assertTrue(result.entry.is_rewatch)
        self.assertEqual(len(self.viewings(item)), 2)

    def test_an_400_stars_and_the_heart_prefill_from_the_titles_own(self):
        item = self.title(total=2)
        anime_tracking.set_rating(self.user, item, 8)
        anime_tracking.set_like(self.user, item, True)
        target = self.state(item)["composer"]["targets"][0]
        self.assertEqual((target["rating"], target["liked"]), ("8.0", True))

    def test_tv_501_source_rules(self):
        item = self.title(total=2)
        entry = self.log(item, rating=8, liked=True).entry
        self.assertEqual(self.row(item).rating_source_id, entry.pk)
        # A direct change makes it independent, and the log keeps its own.
        anime_tracking.set_rating(self.user, item, 6)
        self.assertIsNone(self.row(item).rating_source_id)
        anime_tracking.update_log(entry, {"rating": 4})
        self.assertEqual(str(self.row(item).score), "6.0")

    def test_tv_501_editing_a_source_log_updates_the_current_value(self):
        item = self.title(total=2)
        entry = self.log(item, rating=8, liked=True).entry
        anime_tracking.update_log(entry, {"rating": 4, "liked": False})
        self.assertEqual(str(self.row(item).score), "4.0")
        self.assertFalse(self.liked(item))
        anime_tracking.update_log(entry, {"rating": None})
        self.assertIsNone(self.row(item).score)
        self.assertIsNone(self.row(item).rating_source_id)

    def test_tv_501_a_log_given_new_stars_fills_empty_ones(self):
        item = self.title(total=2)
        entry = self.log(item).entry
        anime_tracking.update_log(entry, {"rating": 6})
        self.assertEqual((str(self.row(item).score), self.row(item).rating_source_id), ("6.0", entry.pk))

    def test_tv_405_editing_a_logs_date_moves_no_viewing(self):
        item = self.title(total=2)
        self.tick(item, 1)
        entry = self.log(item).entry
        before = [(viewing.watched_on, viewing.diary_entry_id) for viewing in self.viewings(item)]
        anime_tracking.update_log(entry, {"consumed_at": date(2026, 1, 2)})
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at, datetime(2026, 1, 2, tzinfo=UTC))
        self.assertEqual([(viewing.watched_on, viewing.diary_entry_id) for viewing in self.viewings(item)], before)

    def test_tv_407_a_future_edit_is_refused(self):
        item = self.title(total=2)
        entry = self.log(item).entry
        with self.assertRaises(TVInvalid):
            anime_tracking.update_log(entry, {"consumed_at": date(2027, 1, 2)})

    def test_tv_406_deleting_removes_only_its_fills(self):
        item = self.title(total=4)
        self.tick(item, 1)
        entry = self.log(item, rating=8).entry
        info = anime_tracking.log_info(entry)
        self.assertEqual((info["unmarks_count"], info["covers_count"]), (3, 4))
        anime_tracking.delete_log(self.user, entry)
        self.assertEqual(self.watched(item), {1})
        row = self.row(item)
        self.assertEqual((row.status, str(row.score), row.rating_source_id), ("In progress", "8.0", None))

    def test_tv_406_a_stale_delete_says_the_new_count(self):
        item = self.title(total=4)
        entry = self.log(item).entry
        with self.assertRaises(TVStateChanged) as raised:
            anime_tracking.delete_log(self.user, entry, expected_version=1)
        self.assertEqual(raised.exception.extra["count"], 4)
        self.assertEqual(len(self.logs(item)), 1)

    def test_tv_406_deleting_the_only_log_leaves_the_library(self):
        item = self.title(total=2)
        entry = self.log(item, rating=8, liked=True).entry
        anime_tracking.delete_log(self.user, entry)
        self.assertIsNone(self.row(item))
        self.assertFalse(self.liked(item))
        self.assertFalse(DiaryEntry.objects.filter(pk=entry.pk).exists())

    def test_tv_400_another_log_keeps_what_it_covered(self):
        item = self.title(total=2)
        first = self.log(item).entry
        self.clock.advance(minutes=1)
        second = self.log(item, is_rewatch=True).entry
        anime_tracking.delete_log(self.user, first)
        self.assertEqual(self.watched(item), {1, 2})
        self.assertTrue(all(viewing.diary_entry_id == second.pk for viewing in self.viewings(item)))

    def test_an_400_while_a_log_exists_nothing_unwatches_what_it_covered(self):
        item = self.airing(aired=2)
        self.log(item)
        for number in (1, 2):
            with self.assertRaises(TVLogBlocks):
                anime_tracking.untick(self.user, item, number)
        with self.assertRaises(TVLogBlocks):
            anime_tracking.unmark(self.user, item)
        # An episode that aired later isn't covered.
        self.clock.set(datetime(2026, 10, 29, 16, 0, tzinfo=UTC))
        self.tick(item, 3)
        anime_tracking.untick(self.user, item, 3)
        self.assertEqual(self.watched(item), {1, 2})

    def test_tv_705_a_logs_mutation_id_applies_it_once(self):
        item = self.title(total=2)
        mutation = uuid4()
        first = self.log(item, mutation_id=mutation)
        again = self.log(item, mutation_id=mutation)
        self.assertTrue(again.replayed)
        self.assertEqual(again.entry.pk, first.entry.pk)
        self.assertEqual(len(self.logs(item)), 1)

    def test_tv_705_a_mutation_id_for_another_write_is_refused(self):
        item = self.title(total=2)
        mutation = uuid4()
        self.log(item, mutation_id=mutation)
        with self.assertRaises(TVInvalid):
            anime_tracking.mark_watched(self.user, item, mutation_id=mutation)
        self.assertEqual(TVMutation.objects.filter(mutation_id=mutation).count(), 1)

    def test_an_400_a_failure_part_way_leaves_nothing(self):
        item = self.title(total=2)
        with self.assertRaises(TVInvalid):
            self.log(item, rating=99)
        self.assertEqual(self.logs(item), [])
        self.assertIsNone(self.row(item))

    def test_stat_100_an_old_log_at_a_real_moment_keeps_its_zone(self):
        """An old log isn't converted (AN-902); its day reads on the user's calendar."""
        item = self.title(total=2)
        moment = datetime(2026, 1, 1, 7, 30, tzinfo=UTC)
        entry = DiaryEntry.objects.create(user=self.user, item=item, consumed_at=moment)
        self.tick(item, 1)
        entry.refresh_from_db()
        self.assertEqual(entry.consumed_at, moment)
        state = self.state(item)
        # 07:30 UTC on Jan 1 is 23:30 on Dec 31 in Los Angeles, and newer than nothing.
        self.assertEqual(state["last_watched_on"], "2026-10-25")
        self.assertEqual(anime_tracking.log_info(entry)["covers_count"], 0)


class SingleTests(AnimeTestCase):
    """AN-002."""

    def movie(self, **values):
        return self.title(title=self.fake.add(total=values.pop("total", 1), format="Movie", title="Your Name", **values))

    def test_an_002_total_of_one_or_a_movie_with_no_total_is_single(self):
        self.assertTrue(self.state(self.movie())["single"])
        self.assertTrue(self.state(self.movie(total=None))["single"])
        self.assertTrue(self.state(self.title(total=1))["single"])
        self.assertFalse(self.state(self.title(total=None, status="Airing"))["single"])
        self.assertFalse(self.state(self.title(total=2, format="Movie"))["single"])

    def test_an_002_no_rows_no_up_next_and_no_question(self):
        item = self.movie()
        state = self.state(item)
        self.assertIsNone(state["up_next"])
        self.assertEqual(self.rows(item)["episodes"], [])
        self.assertEqual(self.rows(item)["count"], 0)
        self.assertEqual(sorted(state["available_actions"]), ["log", "planning", "rate"])

    def test_an_002_the_eye_and_opinions_mark_it(self):
        for write in (
            lambda item: anime_tracking.mark_watched(self.user, item),
            lambda item: anime_tracking.set_rating(self.user, item, 8),
            lambda item: anime_tracking.set_like(self.user, item, True),
        ):
            item = self.movie()
            write(item)
            self.assertEqual(self.row(item).status, "Completed")
            self.assertIsNone(self.viewings(item)[0].watched_on)

    def test_an_002_it_has_no_partway(self):
        item = self.movie()
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.state(item)["opinion_action"], "save")
        anime_tracking.set_rating(self.user, item, 8)
        self.assertEqual(self.row(item).status, "Completed")
        imported = self.movie()
        anime_tracking.import_title(self.user, imported, status="In progress")
        self.assertEqual(self.row(imported).status, "In progress")
        self.assertEqual(self.state(imported)["opinion_action"], "mark")

    def test_an_002_watching_again_is_another_log_and_counts_again(self):
        item = self.movie()
        self.log(item, day=date(2025, 3, 1))
        self.assertEqual(self.stats_time(2025), (24, 1))
        self.log(item, day=date(2026, 3, 1), is_rewatch=True)
        self.assertEqual(self.stats_time(2026), (24, 1))
        self.assertEqual(self.stats_time()[1], 2)
        self.assertEqual(self.state(item)["status"], "Completed")

    def test_an_002_a_log_after_a_mark_dates_it_and_deleting_gives_the_mark_back(self):
        """With Rewatch off, a log dates the marked watch (decision 38, as TV's)."""
        item = self.movie()
        anime_tracking.mark_watched(self.user, item)
        entry = self.log(item, day=date(2025, 3, 1), is_rewatch=False).entry
        self.assertEqual(self.stats_time(2025), (24, 1))
        self.assertEqual(self.stats_time()[1], 1)
        anime_tracking.delete_log(self.user, entry)
        self.assertEqual(self.row(item).status, "Completed")
        self.assertEqual(self.stats_time(2025), (0, 0))
        self.assertEqual(self.stats_time()[1], 1)

    def test_an_002_a_log_after_a_mark_with_rewatch_on_is_a_second_watch(self):
        """The switch opens on over a mark (TV-404): the year gains it, All Time holds both."""
        item = self.movie()
        anime_tracking.mark_watched(self.user, item)
        entry = self.log(item, day=date(2025, 3, 1)).entry
        self.assertTrue(entry.is_rewatch)
        self.assertEqual(self.stats_time(2025), (24, 1))
        self.assertEqual(self.stats_time()[1], 2)

    def test_an_002_an_imported_single_stays_on_its_shelf_until_marked(self):
        item = self.movie()
        anime_tracking.import_title(self.user, item, status="Dropped")
        self.assertEqual(self.row(item).status, "Dropped")
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.row(item).status, "Completed")

    def test_an_002_a_single_has_no_watching_pause_or_drop(self):
        for item in (self.movie(), self.title(total=1)):
            for status in ("In progress", "Paused", "Dropped"):
                with self.assertRaises(TVConflict) as refused:
                    anime_tracking.choose(self.user, item, status)
                self.assertEqual(refused.exception.message, anime_tracking.MSG_SINGLE_CHOICE)
            self.assertIsNone(self.row(item))
            anime_tracking.choose(self.user, item, "Planning")
            self.assertEqual(self.row(item).status, "Planning")

    def test_an_002_a_watched_single_doesnt_offer_a_pause_or_a_drop_it_hasnt_got(self):
        item = self.title(total=1)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.state(item)["action_reasons"]["planning"], anime_tracking.MSG_SINGLE_WATCHED)
        with self.assertRaises(TVConflict) as refused:
            anime_tracking.choose(self.user, item, "Planning")
        self.assertEqual(refused.exception.message, anime_tracking.MSG_SINGLE_WATCHED)

    def test_an_002_a_written_status_on_a_single_is_refused_and_kept_from_the_web(self):
        item = self.title(total=1)
        with self.assertRaises(TVConflict):
            anime_tracking.apply_written_state(self.user, item, {"status": "Paused"}, entry_point=anime_tracking.LEGACY_APP)
        anime_tracking.apply_written_state(self.user, item, {"status": "Paused"}, entry_point=anime_tracking.WEB)
        self.assertIsNone(self.row(item))

    def test_an_002_a_webhook_play_of_a_single_does_nothing(self):
        item = self.title(total=1)
        anime_tracking.webhook_event(self.user, item, 1, event="play", at=self.clock.moment)
        self.assertIsNone(self.row(item))
        anime_tracking.choose(self.user, item, "Planning")
        anime_tracking.webhook_event(self.user, item, 1, event="play", at=self.clock.moment)
        self.assertEqual(self.row(item).status, "Planning")

    def test_an_002_it_never_appears_in_in_progress(self):
        item = self.movie()
        anime_listing.get_listing(item)
        anime_tracking.import_title(self.user, item, status="In progress")
        response = self.api.get("/api/v1/tracking/", {"media_type": "anime", "continue_watching": "true"})
        self.assertEqual(response.data["results"], [])

    def test_an_002_there_is_no_rewatch_action(self):
        item = self.movie()
        anime_tracking.mark_watched(self.user, item)
        with self.assertRaises(TVConflict):
            anime_tracking.start_rewatch(self.user, item)


class NextTitleTests(AnimeTestCase):
    """AN-500, AN-501."""

    def pair(self, *, sequel_start="2017-04-01", sequel_status="Finished"):
        sequel = self.fake.add(title="Season 2", total=12, start_date=sequel_start, status=sequel_status)
        sequel_item = self.title(title=sequel)
        first = self.fake.add(total=2)
        first.series = [
            {"media_id": first.media_id, "position": 1, "title": "Season 1"},
            {"media_id": sequel.media_id, "position": 2, "title": "Season 2", "display_title": "Attack on Titan Season 2", "image": "https://example.com/2.jpg", "release_date": sequel_start},
        ]
        item = self.title(title=first)
        anime_tracking.refresh_after_calendar([item])
        return item, sequel_item

    def test_an_500_only_once_finished(self):
        item, _ = self.pair()
        self.assertIsNone(self.state(item)["next_title"])
        self.tick(item, 1)
        self.assertIsNone(self.state(item)["next_title"])
        self.tick(item, 2)
        self.assertEqual(self.state(item)["next_title"]["title"], "Attack on Titan Season 2")

    def test_an_500_until_the_next_one_is_finished(self):
        item, sequel = self.pair()
        anime_tracking.mark_watched(self.user, item)
        self.assertIsNotNone(self.state(item)["next_title"])
        anime_tracking.mark_watched(self.user, sequel)
        self.assertIsNone(self.state(item)["next_title"])

    def test_an_500_nothing_is_added_until_the_user_acts(self):
        item, sequel = self.pair()
        anime_tracking.mark_watched(self.user, item)
        self.state(item)
        self.assertIsNone(self.row(sequel))

    def test_an_500_a_title_with_no_series_has_no_next(self):
        item = self.title(total=1)
        anime_tracking.mark_watched(self.user, item)
        self.assertIsNone(self.state(item)["next_title"])

    def test_an_500_the_last_in_its_series_has_no_next(self):
        last = self.fake.add(total=1)
        last.series = [{"media_id": "1", "position": 1}, {"media_id": last.media_id, "position": 2}]
        item = self.title(title=last)
        anime_tracking.refresh_after_calendar([item])
        anime_tracking.mark_watched(self.user, item)
        self.assertIsNone(self.state(item)["next_title"])

    def test_an_500_a_slow_mal_never_breaks_a_page(self):
        title = self.fake.add(total=1)
        title.series = [{"media_id": title.media_id, "position": 1}, {"media_id": "99", "position": 2}]
        item = self.title(title=title)
        self.state(item)
        original = anime_listing.mal.anime_series

        def down(_media_id):
            raise anime_listing.services.ProviderAPIError("mal", RuntimeError("slow"))

        anime_listing.mal.anime_series = down
        self.addCleanup(setattr, anime_listing.mal, "anime_series", original)
        anime_tracking.refresh_after_calendar([item])
        anime_tracking.mark_watched(self.user, item)
        self.assertIsNone(self.state(item)["next_title"])

    def test_an_501_has_aired_reads_the_next_titles_own_copy_first(self):
        item, sequel = self.pair(sequel_start="2030-01-01", sequel_status="Upcoming")
        anime_tracking.mark_watched(self.user, item)
        self.assertFalse(self.state(item)["next_title"]["has_aired"])
        self.state(sequel)
        self.assertFalse(self.state(item)["next_title"]["has_aired"])

    def test_an_501_a_year_or_month_alone_has_aired_only_once_it_has_passed(self):
        for released, expected in (
            ("2025", True),
            ("2026", False),
            ("2027", False),
            ("2026-09", True),
            ("2026-10", False),
            ("2026-11", False),
            ("2026-10-25", True),
            ("2026-10-26", False),
            ("", False),
            (None, False),
        ):
            stored = {"media_id": "1", "release_date": released}
            self.assertEqual(anime_tracking._next_title_has_aired(stored, OCT_25, None), expected, released)  # noqa: SLF001

    def test_an_501_a_next_title_known_only_by_this_year_reads_not_aired(self):
        item, _ = self.pair(sequel_start="2026")
        anime_tracking.mark_watched(self.user, item)
        self.assertFalse(self.state(item)["next_title"]["has_aired"])

    def test_an_501_a_next_title_that_is_a_single_says_so(self):
        movie = self.fake.add(title="The Movie", total=1, format="Movie", start_date="2017-04-01", status="Finished")
        movie_item = self.title(title=movie)
        first = self.fake.add(total=2)
        first.series = [
            {"media_id": first.media_id, "position": 1, "title": "Season 1"},
            {"media_id": movie.media_id, "position": 2, "title": "The Movie", "release_date": "2017-04-01", "single": True},
        ]
        item = self.title(title=first)
        anime_tracking.refresh_after_calendar([item])
        anime_tracking.mark_watched(self.user, item)
        next_title = self.state(item)["next_title"]
        self.assertTrue(next_title["single"], "From MAL's series order, with no copy of the movie")
        self.assertTrue(next_title["has_aired"], "The page's Next: row still shows it")

        first.series[1]["single"] = False
        anime_tracking.refresh_after_calendar([item])
        self.state(movie_item)
        self.assertTrue(self.state(item)["next_title"]["single"], "The movie's own copy goes first")

    def test_an_501_a_next_title_with_rows_isnt_a_single(self):
        item, _ = self.pair()
        anime_tracking.mark_watched(self.user, item)
        self.assertFalse(self.state(item)["next_title"]["single"])

    def test_an_501_the_finish_response_names_the_next_title(self):
        item, sequel = self.pair()
        self.tick(item, 1)
        envelope = anime_tracking.write_envelope(self.tick(item, 2))
        self.assertIsNotNone(envelope["finished"])
        self.assertEqual(envelope["anime"]["next_title"]["media_id"], sequel.media_id)
        self.assertTrue(envelope["anime"]["next_title"]["has_aired"])


class RewatchTests(AnimeTestCase):
    """AN-600."""

    def finished(self, total=3):
        item = self.title(total=total)
        self.tick(item, *range(1, total + 1))
        return item

    def test_an_600_rewatch_starts_at_e1_and_records_no_viewing(self):
        item = self.finished()
        before = len(self.viewings(item))
        state = anime_tracking.write_envelope(anime_tracking.start_rewatch(self.user, item))["anime"]
        self.assertEqual((state["status"], state["up_next"]["episode_number"], state["watched_count"]), ("In progress", 1, 0))
        self.assertEqual(state["rewatch"]["pass_number"], 2)
        self.assertEqual(len(self.viewings(item)), before)

    def test_an_600_only_a_finished_title_can_be_rewatched(self):
        item = self.title(total=3)
        self.tick(item, 1)
        with self.assertRaises(TVConflict) as raised:
            anime_tracking.start_rewatch(self.user, item)
        self.assertEqual(raised.exception.message, "Finish this anime before rewatching it.")

    def test_an_600_one_at_a_time_and_stop_needs_one(self):
        item = self.finished()
        with self.assertRaises(TVConflict):
            anime_tracking.stop_rewatch(self.user, item)
        anime_tracking.start_rewatch(self.user, item)
        with self.assertRaises(TVConflict) as raised:
            anime_tracking.start_rewatch(self.user, item)
        self.assertEqual(raised.exception.message, "Finish or stop your rewatch first.")
        self.assertEqual(self.state(item)["action_reasons"]["rewatch"], "Finish or stop your rewatch first.")

    def test_an_600_reaching_the_end_finishes_and_every_viewing_counts_again(self):
        item = self.finished()
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1, 2, 3)
        row = self.row(item)
        self.assertEqual((row.status, row.rewatch_started_at, row.progress), ("Completed", None, 3))
        self.assertEqual(len(self.viewings(item)), 6)
        self.assertEqual({viewing.pass_number for viewing in self.viewings(item)}, {1, 2})

    def test_an_600_ticks_in_a_rewatch_dont_see_the_earlier_pass(self):
        item = self.finished()
        anime_tracking.start_rewatch(self.user, item)
        self.assertTrue(self.tick(item, 1).changed)
        row = self.rows(item)["episodes"][1]
        self.assertNotIn("w", row)

    def test_an_600_untick_only_touches_the_rewatch(self):
        item = self.finished()
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1)
        anime_tracking.untick(self.user, item, 1)
        self.assertEqual(len(self.viewings(item)), 3)
        self.assertEqual(anime_tracking.untick(self.user, item, 2).changed, False)

    def test_an_600_pause_and_drop_keep_it(self):
        item = self.finished()
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1)
        anime_tracking.choose(self.user, item, "Paused")
        state = self.state(item)
        self.assertEqual(state["status"], "Paused")
        self.assertIsNotNone(state["rewatch"])
        self.assertEqual(state["up_next"]["episode_number"], 2)
        anime_tracking.choose(self.user, item, "In progress")
        self.assertIsNotNone(self.state(item)["rewatch"])

    def test_an_600_a_log_in_a_rewatch_fills_its_rest_and_finishes_it(self):
        item = self.finished()
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1)
        entry = self.log(item).entry
        self.assertTrue(entry.is_rewatch)
        self.assertEqual(entry.progress_snapshot["anime"]["pass"], 2)
        row = self.row(item)
        self.assertEqual((row.rewatch_started_at, row.status), (None, "Completed"))

    def test_an_600_a_log_from_the_first_pass_doesnt_block_the_rewatch(self):
        item = self.title(total=2)
        self.log(item)
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1)
        anime_tracking.untick(self.user, item, 1)
        self.assertEqual(len(self.viewings(item)), 2)

    def test_an_600_starting_a_rewatch_records_a_start_and_no_movement(self):
        item = self.finished()
        ProgressChange.objects.filter(actor=self.user).delete()
        anime_tracking.start_rewatch(self.user, item)
        kinds = list(ProgressChange.objects.filter(actor=self.user).values_list("current_progress__kind", "previous_progress__value", "current_progress__value"))
        self.assertIn(("count", 0, 0), kinds)
        self.assertIn(("start", 0, 1), kinds)


class WrittenStateTests(AnimeTestCase):
    """AN-900, AN-902."""

    def write(self, item, entry_point=anime_tracking.LEGACY_APP, **data):
        return anime_tracking.apply_written_state(self.user, item, data, entry_point=entry_point)

    def test_an_900_completed_means_mark_it_watched(self):
        item = self.title(total=3)
        self.write(item, status="Completed")
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 3))
        self.assertTrue(all(viewing.kind == "fill" for viewing in self.viewings(item)))

    def test_an_900_in_progress_means_choose_watching(self):
        item = self.title(total=3)
        self.write(item, status="In progress")
        self.assertEqual(self.row(item).status, "In progress")
        self.assertEqual(self.viewings(item), [])

    def test_an_900_the_other_statuses_are_that_choice(self):
        for status in ("Planning", "Paused", "Dropped"):
            item = self.title(total=3)
            self.write(item, status=status)
            self.assertEqual((self.row(item).status, self.row(item).choice), (status, status))

    def test_an_900_raising_a_number_ticks_the_next_unwatched(self):
        item = self.title(total=5)
        self.tick(item, 2)
        self.clock.advance(minutes=1)
        self.write(item, progress=3)
        self.assertEqual(self.watched(item), {1, 2, 3})
        self.assertTrue(all(viewing.kind == "tick" for viewing in self.viewings(item)))

    def test_an_900_lowering_removes_the_latest_unlogged_viewings(self):
        item = self.title(total=5)
        self.tick(item, 1)
        self.clock.advance(minutes=1)
        self.tick(item, 3)
        self.clock.advance(minutes=1)
        self.write(item, progress=1)
        self.assertEqual(self.watched(item), {1})

    def test_an_900_lowering_is_refused_when_a_log_covers_them(self):
        item = self.title(total=3)
        self.log(item)
        with self.assertRaises(TVLogBlocks) as raised:
            self.write(item, progress=1)
        self.assertEqual(raised.exception.message, "Delete your log first.")
        self.assertEqual(self.row(item).progress, 3)

    def test_an_900_lowering_to_zero_leaves_the_library(self):
        item = self.title(total=3)
        self.write(item, progress=2)
        self.clock.advance(minutes=1)
        result = self.write(item, progress=0)
        self.assertIsNone(result.row)
        self.assertIsNone(self.row(item))

    def test_an_900_a_number_past_the_total_is_refused_and_changes_nothing(self):
        item = self.title(total=3)
        with self.assertRaises(TVInvalid) as raised:
            self.write(item, progress=5)
        self.assertEqual(raised.exception.message, "This anime has 3 episodes.")
        self.assertIsNone(self.row(item))

    def test_an_900_a_number_that_finishes_it_outlives_the_status_sent_with_it(self):
        item = self.title(total=12)
        anime_tracking.apply_written_state(
            self.user,
            item,
            {"status": "In progress", "progress": 12},
            entry_point=anime_tracking.LEGACY_APP,
        )
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 12))
        self.assertTrue(all(viewing.kind == "tick" for viewing in self.viewings(item)))

    def test_an_900_a_number_makes_a_sent_planning_out_of_date_too(self):
        item = self.title(total=12)
        anime_tracking.apply_written_state(
            self.user,
            item,
            {"status": "Planning", "progress": 3},
            entry_point=anime_tracking.LEGACY_APP,
        )
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("In progress", 3))

    def test_an_900_status_and_number_together(self):
        item = self.title(total=5)
        self.write(item, status="Paused", progress=2)
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Paused", 2))

    def test_an_900_an_unavailable_status_is_a_conflict_from_the_api_and_kept_from_the_web(self):
        item = self.title(total=3)
        self.tick(item, 1)
        with self.assertRaises(TVConflict):
            self.write(item, status="Planning")
        self.write(item, entry_point=anime_tracking.WEB, status="Planning")
        self.assertEqual(self.row(item).status, "In progress")

    def test_an_900_notes_change_nothing_else(self):
        item = self.title(total=3)
        self.tick(item, 1)
        self.write(item, notes="Good so far")
        row = self.row(item)
        self.assertEqual((row.notes, row.status, row.progress), ("Good so far", "In progress", 1))

    def test_an_902_a_stored_number_becomes_an_undated_mark_at_the_first_write(self):
        item = self.title(total=12)
        row = Anime(user=self.user, item=item, status="In progress", progress=5)
        row.save_base(force_insert=True)
        # Before the first write, every surface shows what it will store.
        self.assertEqual(self.state(item)["watched_count"], 5)
        anime_tracking._lock_and_reconcile(row.pk)  # noqa: SLF001
        self.assertEqual(self.viewings(item), [])
        self.tick(item, 6)
        rows = self.viewings(item)
        self.assertEqual({viewing.episode_number for viewing in rows if viewing.kind == "fill"}, {1, 2, 3, 4, 5})
        self.assertEqual(self.row(item).progress, 6)
        self.assertEqual(AnimeMark.objects.get(anime=row).origin, "stored")

    def test_an_902_a_stored_completed_marks_everything_aired(self):
        item = self.title(total=12)
        Anime(user=self.user, item=item, status="Completed", progress=3).save_base(force_insert=True)
        anime_tracking.set_rating(self.user, item, 8)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, str(row.score)), ("Completed", 12, "8.0"))

    def test_an_902_a_stored_choice_stays_the_choice(self):
        item = self.title(total=12)
        Anime(user=self.user, item=item, status="Paused", progress=3).save_base(force_insert=True)
        anime_tracking.set_rating(self.user, item, None)
        row = self.row(item)
        self.assertEqual((row.status, row.choice, row.progress), ("Paused", "Paused", 3))


class SimultaneousWriteTests(AnimeTestCase):
    """TV-705 on any database. app.tests.test_anime_concurrency has real threads on Postgres."""

    def race(self, rival):
        """Run rival just before this write's row is created, as if it committed first."""
        real = anime_tracking._create_row  # noqa: SLF001
        calls = []

        def rival_goes_first(*args, **kwargs):
            if not calls:
                calls.append(1)
                with mock.patch.object(anime_tracking, "_create_row", real):
                    rival()
            return real(*args, **kwargs)

        return mock.patch.object(anime_tracking, "_create_row", rival_goes_first)

    def test_tv_705_a_first_tick_that_loses_the_race_runs_again(self):
        item = self.title(total=3)
        with self.race(lambda: anime_tracking.tick(self.user, item, 1, entry_point=anime_tracking.WEB)):
            anime_tracking.tick(self.user, item, 1, entry_point=anime_tracking.WEB)
        self.assertEqual([viewing.episode_number for viewing in self.viewings(item)], [1])
        self.assertEqual(self.row(item).progress, 1)
        changes = ProgressChange.objects.filter(actor=self.user).exclude(current_progress__kind="start")
        self.assertEqual(
            [(change.previous_progress["value"], change.current_progress["value"]) for change in changes],
            [(0, 1)],
        )

    def test_tv_705_a_first_log_that_loses_the_race_sees_the_tick(self):
        item = self.title(total=3)
        with self.race(lambda: anime_tracking.tick(self.user, item, 1, entry_point=anime_tracking.WEB)):
            anime_tracking.create_log(self.user, item, consumed_at=OCT_25)
        self.assertEqual(sorted(viewing.episode_number for viewing in self.viewings(item)), [1, 2, 3])

    def test_tv_705_a_write_locks_only_its_own_row(self):
        item = self.title(total=3)
        anime_tracking.tick(self.user, item, 1)
        real = QuerySet.select_for_update
        locks = []

        def recording(queryset, *args, **kwargs):
            if queryset.model is Anime:
                locks.append((kwargs.get("of"), queryset))
            return real(queryset, *args, **kwargs)

        with mock.patch.object(QuerySet, "select_for_update", recording):
            anime_tracking.tick(self.user, item, 2)
            anime_tracking.refresh_due_titles(self.clock.moment + timedelta(days=400))
            anime_tracking._lock_and_reconcile(self.row(item).pk)  # noqa: SLF001
            other = self.title(total=3)
            anime_tracking.tick(self.user, other, 1)
        self.assertGreaterEqual(len(locks), 3)
        self.assertEqual({of for of, _ in locks}, {("self",)})


class AgreementTests(AnimeTestCase):
    """AN-000: TV-705. What installed builds read keeps its meaning (AN-900)."""

    def test_tv_705_a_write_with_no_row_leaves_nothing_when_refused(self):
        item = self.title(total=2)
        with self.assertRaises(TVInvalid):
            self.tick(item, 9)
        self.assertFalse(Anime.objects.filter(item=item).exists())
        self.assertFalse(MediaLike.objects.exists())

    def test_tv_705_surfaces_agree_after_a_write(self):
        item = self.title(total=5)
        self.action(item, "tick", episode_number=1)
        tracking = self.api.get(self.url(item)).data
        listed = self.api.get("/api/v1/tracking/", {"media_type": "anime"}).data["results"][0]["tracking"]
        detail = self.state(item)
        self.assertEqual(tracking["progress"]["value"], 1)
        self.assertEqual(tracking["progress"]["max"], 5)
        self.assertEqual(listed["anime"]["up_next"]["episode_number"], detail["up_next"]["episode_number"])
        self.assertEqual(listed["status"], detail["status"])


class ManualTitleTests(AnimeTestCase):
    """AN-100: a manual title has no provider, so the number its owner writes is its total."""

    def manual(self):
        return Item.objects.create(
            source="manual",
            media_type="anime",
            media_id="manual-1",
            title="Mine",
            image="https://example.com/p.jpg",
        )

    def written(self, item, **data):
        return anime_tracking.apply_written_state(self.user, item, data, entry_point=anime_tracking.WEB)

    def test_an_100_a_written_completed_with_a_number_stays_completed(self):
        item = self.manual()
        self.written(item, status="Completed", progress=12)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.finished), ("Completed", 12, True))
        self.assertEqual(self.state(item)["total"], 12)
        self.assertTrue(all(viewing.kind == "fill" for viewing in self.viewings(item)))
        self.written(item, notes="Loved it")
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.notes), ("Completed", 12, "Loved it"))

    def test_an_100_a_written_completed_with_no_number_uses_what_is_watched(self):
        item = self.manual()
        self.tick(item, 1, 2, 3)
        self.written(item, status="Completed")
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 3))
        self.assertEqual(self.state(item)["total"], 3)

    def test_an_100_a_higher_number_raises_the_total(self):
        item = self.manual()
        self.written(item, status="Completed", progress=12)
        self.written(item, progress=15)
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 15))
        self.assertEqual(self.state(item)["total"], 15)

    def test_an_100_a_lower_number_keeps_the_total_and_reopens_it(self):
        item = self.manual()
        self.written(item, status="Completed", progress=12)
        self.clock.advance(minutes=1)
        self.written(item, status="In progress", progress=10)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.up_next_episode), ("In progress", 10, 11))
        self.assertEqual(self.state(item)["total"], 12)

    def test_an_100_a_number_with_in_progress_sets_no_total(self):
        item = self.manual()
        self.written(item, status="In progress", progress=5)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.aired_count), ("In progress", 5, None))
        self.assertIsNone(self.state(item)["total"])

    def test_an_100_the_eye_stars_a_heart_and_a_log_finish_it(self):
        for name, finish in (
            ("eye", lambda item: anime_tracking.mark_watched(self.user, item)),
            ("heart", lambda item: anime_tracking.set_like(self.user, item, True)),
            ("stars", lambda item: anime_tracking.set_rating(self.user, item, 8)),
            ("log", lambda item: anime_tracking.create_log(self.user, item, consumed_at=OCT_25)),
        ):
            with self.subTest(name=name):
                item = Item.objects.create(
                    source="manual",
                    media_type="anime",
                    media_id=f"manual-{name}",
                    title="Mine",
                    image="https://example.com/p.jpg",
                )
                finish(item)
                self.assertEqual((self.row(item).status, self.row(item).finished), ("Completed", True))

    def test_an_100_a_log_after_ticks_finishes_at_what_is_watched(self):
        item = self.manual()
        self.tick(item, 1, 2)
        anime_tracking.create_log(self.user, item, consumed_at=OCT_25)
        row = self.row(item)
        self.assertEqual((row.status, row.progress), ("Completed", 2))

    def assert_no_total(self, item, watched):
        row = self.row(item)
        self.assertEqual((row.status, row.finished, row.aired_count), ("In progress", False, None))
        state = self.state(item)
        self.assertEqual((state["total"], state["aired_count"], state["watched_count"]), (None, None, watched))

    def test_an_100_a_total_a_log_worked_out_goes_with_the_log(self):
        item = self.manual()
        self.tick(item, 1, 2, 3)
        entry = self.log(item).entry
        self.assertEqual((self.row(item).status, self.state(item)["total"]), ("Completed", 3))
        anime_tracking.delete_log(self.user, entry)
        self.assert_no_total(item, 3)
        self.tick(item, 4)
        self.assertEqual(self.row(item).progress, 4)
        self.assertEqual([row["n"] for row in self.rows(item)["episodes"]], [1, 2, 3, 4, 5])

    def test_an_100_the_fills_of_a_log_go_with_the_total_it_worked_out(self):
        item = self.manual()
        self.tick(item, 3)
        entry = self.log(item).entry
        self.assertEqual(self.watched(item), {1, 2, 3})
        anime_tracking.delete_log(self.user, entry)
        self.assert_no_total(item, 1)
        self.assertEqual(self.watched(item), {3})
        self.assertEqual([row["n"] for row in self.rows(item)["episodes"]], [1, 2, 3, 4])

    def test_an_100_a_total_the_eye_worked_out_goes_when_its_mark_is_undone(self):
        item = self.manual()
        self.tick(item, 3)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual((self.row(item).status, self.state(item)["total"]), ("Completed", 3))
        anime_tracking.unmark(self.user, item)
        self.assert_no_total(item, 1)
        self.tick(item, 4)
        self.assertEqual(self.watched(item), {3, 4})

    def test_an_100_another_log_or_mark_keeps_a_worked_out_total(self):
        item = self.manual()
        self.tick(item, 1, 2, 3)
        first = self.log(item).entry
        second = self.log(item).entry
        anime_tracking.delete_log(self.user, first)
        self.assertEqual((self.row(item).status, self.state(item)["total"]), ("Completed", 3))
        anime_tracking.delete_log(self.user, second)
        self.assert_no_total(item, 3)

        marked = Item.objects.create(
            source="manual",
            media_type="anime",
            media_id="manual-2",
            title="Mine too",
            image="https://example.com/p.jpg",
        )
        self.tick(marked, 3)
        anime_tracking.mark_watched(self.user, marked)
        entry = self.log(marked).entry
        anime_tracking.delete_log(self.user, entry)
        self.assertEqual((self.row(marked).status, self.state(marked)["total"]), ("Completed", 3))

    def test_an_900_starting_a_manual_title_over_takes_its_total_with_it(self):
        item = self.manual()
        self.tick(item, 1, 2)
        self.written(item, status="Completed")
        self.assertEqual(self.state(item)["total"], 2)
        self.written(item, status="Planning", progress=0)
        state = self.state(item)
        self.assertEqual((state["status"], state["total"], self.row(item).aired_count), ("Planning", None, None))
        self.tick(item, 3)
        self.assertEqual(self.row(item).progress, 1)

    def another_manual(self, name):
        return Item.objects.create(
            source="manual",
            media_type="anime",
            media_id=f"manual-{name}",
            title="Mine",
            image="https://example.com/p.jpg",
        )

    def assert_written_total_reads_a_gap(self, item):
        state = self.state(item)
        self.assertEqual(
            (state["status"], state["total"], state["watched_count"], state["aired_count"], state["first_gap_episode"]),
            ("In progress", 12, 11, 12, 5),
        )
        self.assertFalse(state["caught_up"])
        self.assertEqual(state["up_next"]["episode_number"], 5)
        self.assertEqual(self.rows(item)["count"], 12)

    def test_an_100_a_typed_total_stays_through_a_filled_eye_and_an_untick(self):
        item = self.manual()
        self.tick(item, 1)
        self.written(item, status="Completed", progress=12)
        anime_tracking.unmark(self.user, item)
        self.tick(item, *range(2, 13))
        self.assertEqual(self.row(item).status, "Completed")
        anime_tracking.untick(self.user, item, 5)
        self.assert_written_total_reads_a_gap(item)

    def test_an_100_a_typed_total_stays_through_a_deleted_log(self):
        item = self.manual()
        self.tick(item, 1)
        self.written(item, status="Completed", progress=3)
        anime_tracking.unmark(self.user, item)
        self.tick(item, 2, 3)
        entry = self.log(item).entry
        anime_tracking.delete_log(self.user, entry)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.aired_count), ("Completed", 3, 3))

    def test_an_100_an_imported_total_stays_through_an_untick(self):
        item = self.manual()
        anime_tracking.import_title(self.user, item, status="Completed", count=12)
        anime_tracking.untick(self.user, item, 5)
        self.assert_written_total_reads_a_gap(item)

    def test_an_100_a_raised_total_is_written_and_a_lowered_number_is_not_a_total(self):
        item = self.manual()
        self.tick(item, 3)
        anime_tracking.mark_watched(self.user, item)
        self.assertFalse(self.row(item).total_is_written, "The eye worked it out")
        self.written(item, progress=2)
        self.assertFalse(self.row(item).total_is_written, "A number below the total only removes")
        self.written(item, progress=5)
        self.assertTrue(self.row(item).total_is_written)
        self.assertEqual(self.state(item)["total"], 5)

    def test_an_100_a_worked_out_total_goes_when_nothing_is_left(self):
        def by_log(item):
            anime_tracking.delete_log(self.user, self.log(item).entry)

        def by_eye(item):
            anime_tracking.mark_watched(self.user, item)
            anime_tracking.unmark(self.user, item)

        def by_stars(item):
            anime_tracking.set_rating(self.user, item, 8)
            anime_tracking.unmark(self.user, item)

        def by_heart(item):
            anime_tracking.set_like(self.user, item, True)
            anime_tracking.unmark(self.user, item)

        for choice, undos in (
            ("Planning", (by_log, by_eye, by_stars, by_heart)),
            ("Paused", (by_log, by_eye, by_stars, by_heart)),
            ("Dropped", (by_log, by_eye)),
            # Stars and a heart on a title chosen as In progress are refused as partway (AN-301).
            ("In progress", (by_log, by_eye)),
        ):
            for undo in undos:
                with self.subTest(choice=choice, undo=undo.__name__):
                    item = self.another_manual(f"{choice}-{undo.__name__}")
                    self.written(item, status=choice)
                    undo(item)
                    state = self.state(item)
                    self.assertEqual((state["status"], state["total"], state["single"]), (choice, None, False))
                    self.assertEqual(self.row(item).aired_count, None)
                    self.assertEqual([row["n"] for row in self.rows(item)["episodes"]], [1])
                    anime_tracking.choose(self.user, item, "In progress")
                    self.assertEqual(self.row(item).status, "In progress")

    def test_an_100_a_worked_out_total_with_nothing_behind_it_goes_with_an_untick(self):
        item = self.manual()
        self.tick(item, 1, 2, 3)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.row(item).status, "Completed")
        anime_tracking.untick(self.user, item, 3)
        state = self.state(item)
        self.assertEqual((state["status"], state["total"], state["watched_count"]), ("In progress", None, 2))

    def test_an_100_a_stored_completed_on_a_manual_title_is_a_written_total(self):
        item = self.manual()
        Anime.objects.bulk_create([Anime(user=self.user, item=item, status="Completed", progress=12)])
        anime_tracking.untick(self.user, item, 5)
        self.assert_written_total_reads_a_gap(item)

    def test_an_100_a_number_the_owner_wrote_stays_when_a_mark_is_undone(self):
        item = self.manual()
        self.tick(item, 1)
        self.written(item, status="Completed", progress=12)
        self.assertEqual(self.state(item)["total"], 12)
        anime_tracking.unmark(self.user, item)
        row = self.row(item)
        self.assertEqual((row.status, row.progress, row.up_next_episode, row.aired_count), ("In progress", 1, 2, 12))
        with self.assertRaises(TVInvalid):
            self.tick(item, 13)

    def test_an_100_a_number_past_the_ceiling_is_still_refused(self):
        item = self.manual()
        with self.assertRaises(TVInvalid):
            self.written(item, status="Completed", progress=30000)
        self.assertIsNone(self.row(item))

    def test_an_100_the_owners_total_is_theirs_and_no_copy_is_stored(self):
        item = self.manual()
        self.written(item, status="Completed", progress=12)
        self.assertFalse(AnimeListing.objects.filter(item=item).exists())
        other = make_user()
        self.assertIsNone(anime_tracking.state_payload(other, item)["total"])


class NoNumbersTests(AnimeTestCase):
    """AN-101, AN-103: a title with no total and no schedule still works."""

    def bare(self):
        return self.title(total=None, status="Airing")

    def test_an_101_the_list_runs_from_e1_to_one_past_the_last_watched(self):
        item = self.bare()
        self.tick(item, 3)
        rows = self.rows(item)
        self.assertEqual([row["n"] for row in rows["episodes"]], [1, 2, 3, 4])
        self.assertTrue(all(row["a"] == 1 for row in rows["episodes"]))
        self.assertEqual(rows["first_unwatched"], 1)
        self.assertEqual(rows["count"], 4)

    def listed(self, *numbers, **values):
        episodes = {number: (f"Title {number}", f"https://example.com/{number}.jpg") for number in numbers}
        return self.title(total=None, status="Airing", episodes=episodes, **values)

    def test_an_101_with_a_list_it_runs_to_the_highest_listed_with_no_gaps(self):
        # Chibi Maruko-chan's list, nothing watched: it read E1, then E1419 to E1448.
        rows = self.rows(self.listed(*range(1419, 1449)))
        self.assertEqual([row["n"] for row in rows["episodes"]], list(range(1, 1449)))
        self.assertEqual(rows["count"], 1448)
        self.assertEqual(rows["episodes"][0], {"n": 1, "a": 1})
        self.assertEqual(rows["episodes"][1418]["t"], "Title 1419")
        self.assertEqual(rows["episodes"][1418]["i"], "https://example.com/1419.jpg")
        self.assertTrue(all(row["a"] == 1 for row in rows["episodes"]))
        self.assertEqual((rows["up_next"], rows["first_unwatched"]), (1, 1))

    def test_an_101_one_pieces_list_with_no_next_episode_has_no_gaps(self):
        rows = self.rows(self.listed(*range(62, 131)))
        self.assertEqual([row["n"] for row in rows["episodes"]], list(range(1, 131)))

    def test_an_101_the_list_runs_on_to_one_past_the_last_watched_when_that_is_later(self):
        item = self.listed(1, 2, 3)
        self.tick(item, 2)
        self.assertEqual([row["n"] for row in self.rows(item)["episodes"]], [1, 2, 3])
        self.tick(item, 3)
        self.assertEqual([row["n"] for row in self.rows(item)["episodes"]], [1, 2, 3, 4])
        self.tick(item, 9)
        self.assertEqual([row["n"] for row in self.rows(item)["episodes"]], list(range(1, 11)))

    def test_an_100_a_listed_episode_counts_as_evidence_it_aired(self):
        item = self.listed(1, 2, 3)
        state = self.state(item)
        self.assertIsNone(state["aired_count"], "How many have aired stays unknown")
        self.assertEqual(state["eye"]["count"], 3)
        self.tick(item, 1)
        self.assertEqual((self.row(item).up_next_episode, self.row(item).caught_up), (2, False))
        anime_tracking.mark_watched(self.user, item)
        row = self.row(item)
        self.assertEqual((row.progress, row.caught_up, row.finished, row.aired_count), (3, True, False, None))
        self.assertEqual(self.watched(item), {1, 2, 3})

    def test_an_100_a_log_covers_what_is_listed(self):
        item = self.listed(5, 6)
        self.log(item)
        self.assertEqual(self.watched(item), {1, 2, 3, 4, 5, 6})

    def test_an_101_any_number_can_still_be_ticked_past_the_list(self):
        item = self.listed(1, 2, 3)
        self.tick(item, 500)
        self.assertEqual(self.row(item).progress, 1)

    def test_an_100_an_upcoming_titles_list_is_no_evidence(self):
        item = self.title(total=None, status="Upcoming", start_date="2027-03-12", episodes={1: ("First", ""), 2: ("Second", "")})
        state = self.state(item)
        self.assertEqual((state["aired_count"], state["eye"]["tap"]), (None, "nothing_aired"))
        rows = self.rows(item)
        self.assertEqual([row["n"] for row in rows["episodes"]], [1, 2])
        self.assertFalse(any(row.get("a") for row in rows["episodes"]))

    def test_an_101_a_list_past_the_next_episode_leaves_no_gaps(self):
        item = self.runner(aired=7, episodes={25: ("Later", ""), 26: ("Later still", "")})
        rows = self.rows(item)["episodes"]
        self.assertEqual([row["n"] for row in rows], list(range(1, 27)))
        self.assertEqual(rows[7], {"n": 8, "d": "2026-10-29"})
        self.assertEqual(rows[8], {"n": 9}, "It reads TBA")
        self.assertEqual(rows[24], {"n": 25, "t": "Later"})

    def test_an_101_a_viewing_past_the_next_episode_leaves_no_gaps_and_stays_in_order(self):
        title = self.fake.add(total=None, status="Airing", last_aired=(3, self.clock.moment - timedelta(days=10)))
        item = self.title(title=title)
        self.tick(item, 9)
        title.last_aired = None
        title.next_episode = 5
        title.next_air_at = self.clock.moment + timedelta(days=1)
        title.schedule = {5: title.next_air_at}
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual([row["n"] for row in self.rows(item)["episodes"]], list(range(1, 10)))

    def test_an_902_a_stored_completed_past_what_anilist_lists_keeps_its_number(self):
        item = self.listed(1, 2, 3)
        Anime.objects.bulk_create([Anime(user=self.user, item=item, status="Completed", progress=12)])
        self.assertEqual(self.state(item)["watched_count"], 12)
        anime_tracking.set_like(self.user, item, True)
        self.assertEqual(self.watched(item), set(range(1, 13)))

    def test_an_101_a_write_that_changes_the_list_sends_it(self):
        item = self.bare()
        ticked = anime_tracking.write_envelope(self.tick(item, 1))
        self.assertEqual([row["n"] for row in ticked["rows"]["episodes"]], [1, 2])
        unchanged = anime_tracking.write_envelope(self.tick(item, 1))
        self.assertIsNone(unchanged["rows"], "Ticking it again lists the same rows")
        left = anime_tracking.write_envelope(anime_tracking.untick(self.user, item, 1))
        self.assertIsNone(self.row(item), "It left the library")
        self.assertEqual([row["n"] for row in left["rows"]["episodes"]], [1], "So E2 goes from the list")

    def test_an_101_a_webhook_watch_of_e5_leaves_e1_to_e4_tickable(self):
        item = self.bare()
        anime_tracking.webhook_event(self.user, item, 5, event="watched", at=self.clock.moment)
        self.assertEqual([row["n"] for row in self.rows(item)["episodes"]], [1, 2, 3, 4, 5, 6])
        self.tick(item, 2)
        self.assertEqual(self.watched(item), {2, 5})
        self.assertEqual([row["n"] for row in self.rows(item)["episodes"] if row.get("w")], [2, 5])

    def test_an_102_it_reads_a_count_of_episodes(self):
        item = self.bare()
        self.tick(item, 1)
        state = self.state(item)
        self.assertEqual((state["aired_count"], state["watched_count"], state["caught_up"]), (None, 1, True))
        self.assertEqual(self.row(item).aired_count, None)

    def test_an_201_it_stays_caught_up_however_far_it_goes(self):
        item = self.bare()
        self.tick(item, 1, 2, 3)
        row = self.row(item)
        self.assertEqual((row.status, row.finished, row.caught_up), ("In progress", False, True))

    def test_an_300_the_eye_marks_its_first_row(self):
        item = self.bare()
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(self.watched(item), {1})
        self.assertTrue(self.state(item)["eye"]["filled"])

    def test_an_301_stars_mark_its_first_row(self):
        item = self.bare()
        anime_tracking.set_rating(self.user, item, 8)
        self.assertEqual(self.watched(item), {1})
        self.assertEqual(str(self.row(item).score), "8.0")
        self.assertEqual(self.row(item).status, "In progress")

    def test_an_400_a_log_works(self):
        item = self.bare()
        self.log(item)
        self.assertEqual(self.watched(item), {1})
        self.assertEqual(len(self.logs(item)), 1)

    def test_tv_108_up_to_any_number(self):
        item = self.bare()
        anime_tracking.mark_up_to(self.user, item, 4)
        self.assertEqual(self.watched(item), {1, 2, 3, 4})

    def test_an_101_listed_rows_with_no_schedule_can_all_be_ticked(self):
        item = self.title(total=None, status="Airing", episodes={7: ("Seven", None)})
        rows = {row["n"]: row for row in self.rows(item)["episodes"]}
        self.assertEqual(rows[7]["a"], 1)
        self.tick(item, 7)
        self.assertEqual(self.watched(item), {7})


class PlanningCardTests(AnimeTestCase):
    """AN-700: a Planning card's premiere date, from stored values."""

    def test_an_700_planning_shows_the_premiere_while_nothing_has_aired(self):
        item = self.title(total=12, status="Upcoming", start_date="2027-03-12")
        anime_tracking.choose(self.user, item, "Planning")
        payload = anime_tracking.list_payload(self.row(item))
        self.assertEqual(payload["premiere"], {"air_date": "2027-03-12"})
        self.assertEqual(payload["next_airing"], {"episode_number": 1, "air_date": "2027-03-12"})

    def test_an_700_a_first_episodes_airing_stands_in_for_the_premiere(self):
        moment = self.clock.moment
        item = self.title(total=12, status="Upcoming", start_date="2027", next_episode=1, next_air_at=moment + timedelta(days=9))
        state = self.state(item)
        self.assertEqual(state["premiere"], {"air_date": "2026-11-03"})

    def test_an_700_once_aired_there_is_no_premiere(self):
        item = self.title(total=12)
        self.tick(item, 1)
        self.assertIsNone(anime_tracking.list_payload(self.row(item))["premiere"])


class CalendarEventsTests(AnimeTestCase):
    """AN-000: TV-704."""

    def test_tv_704_planning_watching_and_completed_get_events_and_paused_and_dropped_dont(self):
        from events.models import Event

        day = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
        expected = {}
        for status in ("Planning", "In progress", "Completed", "Paused", "Dropped"):
            item = self.title(total=2)
            Event.objects.create(item=item, content_number=1, datetime=day)
            expected[item.pk] = status not in ("Paused", "Dropped")
            if status == "Completed":
                anime_tracking.mark_watched(self.user, item)
            else:
                anime_tracking.choose(self.user, item, status)
        events = Event.objects.get_user_events(self.user, date(2026, 10, 30), date(2026, 11, 2))
        self.assertEqual({event.item_id for event in events}, {pk for pk, shown in expected.items() if shown})
