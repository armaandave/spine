"""Marks, fills and the earlier-episodes question (docs/tv-tracking-contract.md).

TV-107 (server), TV-108 (server), TV-300 (server), TV-301 and TV-302 (server), with
A-14, A-15, A-23 to A-27, A-49 to A-53 and A-55 at the domain level.
"""

import uuid
from datetime import timedelta

from app import tv_tracking
from app.models import TV, DiaryEntry, Episode, MediaLike, Season
from app.tests.test_tv_tracking_viewings import OCT_25, TVTrackingTestCase
from app.tv_tracking import TVInvalid, TVLogBlocks, TVStateChanged


def fills(rows):
    return [row for row in rows if row.kind == Episode.Kind.FILL]


class TV300ShowEyeTests(TVTrackingTestCase):
    """TV-300: the show and season eyes mark every aired regular episode, undated."""

    def big_show(self):
        # 62 aired regular episodes over five seasons, plus specials.
        return self.make_show({0: 3, 1: 12, 2: 12, 3: 12, 4: 13, 5: 13})

    def test_a_23_marking_a_62_episode_show_watched(self):
        """A-23: confirmed, all aired regular episodes filled undated; Completed."""
        from social.models import Activity

        show = self.big_show()
        eye = self.state(show)["eye"]
        self.assertEqual(eye["tap"], "mark_confirm")
        self.assertEqual(eye["count"], 62)
        self.assertEqual(eye["prompt"], "Mark all 62 aired episodes watched?")

        result = tv_tracking.mark_watched(self.user, show)

        self.assertTrue(result.changed)
        rows = self.viewings(show)
        self.assertEqual(len(rows), 62)
        self.assertTrue(all(row.kind == Episode.Kind.FILL for row in rows))
        self.assertTrue(all(row.end_date is None and row.watched_on is None for row in rows))
        self.assertTrue(all(row.diary_entry_id is None for row in rows))
        self.assertEqual(self.viewings(show, 0), [])
        self.assertEqual(self.status(show), "Completed")
        self.assertEqual(TV.objects.filter(user=self.user).count(), 1)
        cards = tv_tracking.season_card_states(self.user, show, None)
        self.assertEqual({card["state"] for number, card in cards.items() if number}, {"watched"})
        self.assertEqual(cards[0]["state"], "not_started")
        self.assertFalse(DiaryEntry.objects.exists())
        self.assertFalse(Activity.objects.exists())
        self.assertIsNone(self.tv(show).last_watched_on)

    def test_tv_300_a_season_still_airing_reads_airing_and_the_show_caught_up(self):
        show = self.make_show({1: 2, 2: [OCT_25 - timedelta(days=7), OCT_25 + timedelta(days=7)]})
        tv_tracking.mark_watched(self.user, show)
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertTrue(state["caught_up"])
        self.assertTrue(state["eye"]["filled"])
        cards = tv_tracking.season_card_states(self.user, show, None)
        self.assertEqual((cards[1]["state"], cards[2]["state"]), ("watched", "airing"))

    def test_tv_300_an_eye_with_nothing_aired_records_nothing_and_says_when(self):
        premiere = OCT_25 + timedelta(days=138)
        show = self.make_show({1: [premiere, premiere + timedelta(days=7)]})
        eye = self.state(show)["eye"]
        self.assertEqual(eye["tap"], "nothing_aired")
        self.assertEqual(eye["message"], "S1 premieres Mar 12, 2027")
        result = tv_tracking.mark_watched(self.user, show)
        self.assertFalse(result.changed)
        self.assertIsNone(self.tv(show))

    def test_tv_300_the_season_eye_marks_without_a_dialog_and_keeps_ticks(self):
        show = self.make_show({1: 4, 2: 3})
        self.tick(show, 1, 2)
        season = tv_tracking.state_payload(self.user, self.season_item(show, 1))["season"]
        self.assertEqual(season["eye"], {"filled": False, "tap": "mark", "count": 3, "prompt": None, "message": None})
        tv_tracking.mark_watched(self.user, show, season_number=1)
        rows = self.viewings(show, 1)
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(fills(rows)), 3)
        self.assertEqual(self.viewings(show, 1, 2)[0].kind, Episode.Kind.TICK)
        self.assertEqual(self.viewings(show, 2), [])

    def test_tv_300_a_mark_ends_a_choice(self):
        show = self.make_show({1: 2})
        tv_tracking.choose(self.user, show, "Dropped")
        tv_tracking.mark_watched(self.user, show)
        self.assertEqual(self.status(show), "Completed")

    def test_tv_301_the_quick_log_date_preference_no_longer_dates_fills(self):
        self.user.quick_watch_date = "current_date"
        self.user.save(update_fields=["quick_watch_date"])
        show = self.make_show({1: 3})
        tv_tracking.mark_watched(self.user, show)
        self.assertTrue(all(row.end_date is None for row in self.viewings(show)))


class TV302EyeUndoTests(TVTrackingTestCase):
    """TV-302: undoing a mark removes only what it added."""

    def test_a_24_undoing_the_only_show_mark_leaves_the_library(self):
        show = self.make_show({1: 3, 2: 3})
        tv_tracking.mark_watched(self.user, show)
        tv_tracking.set_rating(self.user, show, 8)
        tv_tracking.set_like(self.user, show, True)
        eye = self.state(show)["eye"]
        self.assertEqual((eye["filled"], eye["tap"], eye["count"]), (True, "unmark_marks", 6))
        tv_tracking.unmark(self.user, show)
        self.assertIsNone(self.tv(show))
        self.assertFalse(Episode.objects.exists())
        self.assertFalse(MediaLike.objects.filter(item=show).exists())

    def test_a_25_tick_season_1_mark_the_rest_then_undo(self):
        show = self.make_show({1: 3, 2: 3, 3: 2})
        self.ticks(show, 1, (1, 2, 3))
        tv_tracking.mark_watched(self.user, show)
        self.assertEqual(self.status(show), "Completed")
        tv_tracking.unmark(self.user, show)
        self.assertEqual(self.watched(show), {(1, 1), (1, 2), (1, 3)})
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (2, 1))

    def test_a_26_a_show_finished_by_ticking_asks_before_unmarking(self):
        show = self.make_show({1: 3})
        self.ticks(show, 1, (1, 2, 3))
        state = self.state(show)
        self.assertEqual(state["eye"]["tap"], "unmark_confirm")
        self.assertEqual(state["eye"]["prompt"], "Mark all 3 episodes unwatched?")
        with self.assertRaises(TVInvalid):
            tv_tracking.unmark(self.user, show)
        with self.assertRaises(TVStateChanged) as raised:
            tv_tracking.unmark(self.user, show, confirm=True, expected_version=state["version"] - 1)
        self.assertEqual(raised.exception.extra["count"], 3)
        tv_tracking.unmark(
            self.user,
            show,
            confirm=True,
            expected_version=state["version"],
            mutation_id=uuid.uuid4(),
        )
        self.assertIsNone(self.tv(show))

    def test_a_26_with_an_episode_log_the_option_is_unavailable(self):
        show = self.make_show({1: 3})
        self.ticks(show, 1, (1, 2, 3))
        self.log(self.episode_item(show, 1, 2))
        eye = self.state(show)["eye"]
        self.assertEqual((eye["tap"], eye["message"]), ("blocked", "Delete the logs first."))
        with self.assertRaises(TVLogBlocks):
            tv_tracking.unmark(self.user, show, confirm=True)
        self.assertEqual(len(self.viewings(show)), 3)

    def test_a_27_a_show_log_blocks_the_filled_eye(self):
        show = self.make_show({1: 3})
        self.log(show)
        eye = self.state(show)["eye"]
        self.assertEqual((eye["tap"], eye["message"]), ("blocked", "Delete your show log first."))
        with self.assertRaises(TVLogBlocks) as raised:
            tv_tracking.unmark(self.user, show)
        self.assertEqual(raised.exception.message, "Delete your show log first.")
        season_eye = tv_tracking.state_payload(self.user, self.season_item(show, 1))["season"]["eye"]
        self.assertEqual(season_eye["message"], "Delete your show log first.")

    def test_a_49_mark_log_season_2_then_undo_the_show_eye(self):
        show = self.make_show({1: 2, 2: 2, 3: 2})
        tv_tracking.mark_watched(self.user, show)
        result = self.log(self.season_item(show, 2))
        tv_tracking.unmark(self.user, show)
        self.assertEqual(self.watched(show), {(2, 1), (2, 2)})
        season_2 = self.viewings(show, 2)
        self.assertEqual({row.diary_entry_id for row in season_2}, {result.entry.pk})
        self.assertEqual(self.status(show), "In progress")

    def test_a_50_mark_a_show_then_undo_the_season_2_eye(self):
        show = self.make_show({1: 2, 2: 2, 3: 2})
        tv_tracking.mark_watched(self.user, show)
        season = tv_tracking.state_payload(self.user, self.season_item(show, 2))["season"]
        self.assertEqual(season["eye"]["tap"], "unmark_marks")
        tv_tracking.unmark(self.user, show, season_number=2)
        self.assertEqual(self.watched(show), {(1, 1), (1, 2), (3, 1), (3, 2)})
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (2, 1))

    def test_a_51_tick_season_1_mark_seasons_2_to_5_then_undo_the_show_eye(self):
        show = self.make_show({1: 2, 2: 2, 3: 2, 4: 2, 5: 2})
        self.ticks(show, 1, (1, 2))
        for number in (2, 3, 4, 5):
            tv_tracking.mark_watched(self.user, show, season_number=number)
        eye = self.state(show)["eye"]
        self.assertEqual(eye["tap"], "unmark_marks")
        tv_tracking.unmark(self.user, show)
        self.assertEqual(self.watched(show), {(1, 1), (1, 2)})

    def test_a_52_mark_up_to_s2_e4_then_undo_the_season_1_eye(self):
        show = self.make_show({1: 3, 2: 6})
        tv_tracking.mark_up_to(self.user, show, 2, 4)
        tv_tracking.unmark(self.user, show, season_number=1)
        self.assertEqual(self.watched(show), {(2, 1), (2, 2), (2, 3), (2, 4)})

    def test_a_53_heart_a_weekly_show_then_undo_its_filled_eye(self):
        """A-53: Watching, caught up, eye filled; the undo leaves the library."""
        thursday = OCT_25 + timedelta(days=4)
        episodes = [OCT_25 - timedelta(days=7 * (6 - number)) for number in range(1, 7)] + [thursday]
        show = self.make_show({1: episodes}, status="Returning Series")
        tv_tracking.set_like(self.user, show, True)
        state = self.state(show)
        self.assertEqual(state["status"], "In progress")
        self.assertTrue(state["caught_up"])
        self.assertTrue(state["eye"]["filled"])
        self.assertEqual(state["next_airing"]["episode_number"], 7)
        tv_tracking.unmark(self.user, show)
        self.assertIsNone(self.tv(show))
        self.assertFalse(MediaLike.objects.filter(item=show).exists())

    def test_tv_302_a_season_log_blocks_its_own_eye(self):
        show = self.make_show({1: 2, 2: 2})
        self.log(self.season_item(show, 2))
        with self.assertRaises(TVLogBlocks) as raised:
            tv_tracking.unmark(self.user, show, season_number=2)
        self.assertEqual(raised.exception.message, "Delete your Season 2 log first.")

    def test_tv_302_marks_keep_ticks_and_other_seasons_marks(self):
        show = self.make_show({1: 3, 2: 2})
        self.tick(show, 1, 1)
        tv_tracking.mark_watched(self.user, show, season_number=1)
        tv_tracking.mark_watched(self.user, show, season_number=2)
        tv_tracking.unmark(self.user, show, season_number=1)
        self.assertEqual(self.watched(show), {(1, 1), (2, 1), (2, 2)})


class TV107EarlierEpisodesTests(TVTrackingTestCase):
    """TV-107: starting partway asks once about earlier episodes."""

    def test_tv_107_ticking_s3_e1_first_asks_about_the_episode_range(self):
        show = self.make_show({1: 10, 2: 10, 3: 10})
        result = self.tick(show, 3, 1)
        self.assertEqual(
            result.earlier_unwatched,
            {
                "from": {"season_number": 1, "episode_number": 1},
                "to": {"season_number": 2, "episode_number": 10},
                "count": 20,
                "seasons": [1, 2],
                "question": "Also mark S1 E1–S2 E10 watched?",
                "offer_season_number": 3,
            },
        )
        self.assertIsNotNone(self.tv(show).earlier_question_at)

    def test_a_14_logging_season_3_first_yes_fills_seasons_1_and_2(self):
        show = self.make_show({1: 2, 2: 2, 3: 2})
        result = self.log(self.season_item(show, 3))
        question = result.earlier_unwatched
        self.assertEqual(question["question"], "Also mark Seasons 1–2 watched?")
        self.assertIsNone(question["offer_season_number"])
        answer = tv_tracking.mark_earlier(
            self.user,
            show,
            from_ref=question["from"],
            to_ref=question["to"],
            mutation_id=uuid.uuid4(),
        )
        self.assertIsNone(answer.finished)
        self.assertEqual(len(self.watched(show)), 6)
        earlier = [row for row in self.viewings(show) if row.item.season_number in (1, 2)]
        self.assertTrue(all(row.kind == Episode.Kind.FILL and row.end_date is None for row in earlier))
        self.assertEqual(self.status(show), "Completed")

    def test_a_14_answering_no_fills_nothing_and_never_asks_again(self):
        show = self.make_show({1: 2, 2: 2, 3: 2})
        self.assertIsNotNone(self.log(self.season_item(show, 3)).earlier_unwatched)
        self.assertEqual(self.watched(show), {(3, 1), (3, 2)})
        self.assertIsNone(self.tick(show, 2, 2).earlier_unwatched)
        self.assertIsNone(tv_tracking.mark_watched(self.user, show, season_number=2).earlier_unwatched)

    def test_a_15_tick_s3_e1_answer_no_and_watch_through(self):
        show = self.make_show({1: 2, 2: 2, 3: 3})
        self.tick(show, 3, 1)
        state = self.state(show)
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (3, 2))
        self.ticks(show, 3, (2, 3))
        state = self.state(show)
        self.assertEqual((state["up_next"]["season_number"], state["up_next"]["episode_number"]), (1, 1))
        self.assertFalse(state["finished"])
        self.assertEqual(state["status"], "In progress")

    def test_a_55_tick_the_finale_first_and_answer_yes(self):
        """A-55: Yes fills E1 to E9 and opens the Season 1 composer once."""
        show = self.make_show({1: 10, 2: 3})
        first = self.tick(show, 1, 10)
        self.assertIsNone(first.finished)
        question = first.earlier_unwatched
        self.assertEqual(question["question"], "Also mark S1 E1–E9 watched?")
        self.assertEqual(question["offer_season_number"], 1)
        answer = tv_tracking.mark_earlier(
            self.user,
            show,
            from_ref=question["from"],
            to_ref=question["to"],
            offer_season_number=question["offer_season_number"],
            mutation_id=uuid.uuid4(),
        )
        self.assertEqual(answer.finished["season_number"], 1)
        self.assertEqual(answer.finished["composer"]["default_target"], "season:1")
        self.assertEqual(len(fills(self.viewings(show, 1))), 9)
        # The re-tick never offers the composer again (TV-403).
        self.untick(show, 1, 10)
        self.assertIsNone(self.tick(show, 1, 10).finished)

    def test_a_55_answering_no_fills_nothing_and_opens_no_composer(self):
        show = self.make_show({1: 10})
        first = self.tick(show, 1, 10)
        self.assertIsNone(first.finished)
        self.assertEqual(self.watched(show), {(1, 10)})

    def test_tv_107_a_finishing_first_tick_names_its_season(self):
        show = self.make_show({1: 2, 2: 1})
        result = self.tick(show, 2, 1)
        self.assertEqual(result.earlier_unwatched["offer_season_number"], 2)
        self.assertEqual(result.finished["season_number"], 2)
        answer = tv_tracking.mark_earlier(
            self.user,
            show,
            from_ref=result.earlier_unwatched["from"],
            to_ref=result.earlier_unwatched["to"],
            offer_season_number=2,
            mutation_id=uuid.uuid4(),
        )
        # Season 2 was already offered by the tick, and Season 1 only the Yes finished.
        self.assertIsNone(answer.finished)

    def test_tv_107_webhooks_imports_and_installed_builds_dont_ask_but_use_it_up(self):
        show = self.make_show({1: 2, 2: 2})
        result = self.tick(show, 2, 1, entry_point=tv_tracking.LEGACY_APP)
        self.assertIsNone(result.earlier_unwatched)
        self.assertIsNotNone(self.tv(show).earlier_question_at)
        other = self.make_show({1: 2, 2: 2})
        self.advance()
        tv_tracking.webhook_event(self.user, other, 2, 1, event="watched", at=self.clock.moment)
        self.assertEqual(self.watched(other), {(2, 1)})
        self.assertIsNone(self.tick(other, 2, 2).earlier_unwatched)

    def test_tv_107_a_specials_first_write_doesnt_use_the_question_up(self):
        show = self.make_show({0: 2, 1: 2, 2: 2})
        self.assertIsNone(self.tick(show, 0, 1).earlier_unwatched)
        self.assertIsNone(self.tv(show).earlier_question_at)
        self.assertIsNotNone(self.tick(show, 2, 1).earlier_unwatched)

    def test_tv_107_mark_earlier_needs_a_mutation_id_from_the_app(self):
        show = self.make_show({1: 2, 2: 2})
        self.tick(show, 2, 1)
        with self.assertRaises(TVInvalid):
            tv_tracking.mark_earlier(
                self.user,
                show,
                from_ref={"season_number": 1, "episode_number": 1},
                to_ref={"season_number": 1, "episode_number": 2},
            )


class TV108MarkUpToTests(TVTrackingTestCase):
    """TV-108: Mark watched up to here."""

    def test_tv_108_marks_the_range_undated_and_keeps_watched_viewings(self):
        show = self.make_show({1: 3, 2: 6})
        self.tick(show, 1, 2)
        prompt = tv_tracking.episode_states(self.user, show, 2, None)[4]["mark_up_to_prompt"]
        self.assertEqual(prompt, "Mark S1 E1–S2 E4 watched?")
        result = tv_tracking.mark_up_to(self.user, show, 2, 4)
        self.assertIsNone(result.finished)
        self.assertIsNone(result.earlier_unwatched)
        self.assertEqual(
            self.watched(show),
            {(1, 1), (1, 2), (1, 3), (2, 1), (2, 2), (2, 3), (2, 4)},
        )
        self.assertEqual(self.viewings(show, 1, 2)[0].kind, Episode.Kind.TICK)
        self.assertEqual(len(fills(self.viewings(show))), 6)
        self.assertFalse(DiaryEntry.objects.exists())

    def test_tv_108_not_offered_on_specials_or_unaired_episodes(self):
        show = self.make_show({0: 2, 1: [OCT_25 - timedelta(days=7), OCT_25 + timedelta(days=7)]})
        states = tv_tracking.episode_states(self.user, show, 0, None)
        self.assertFalse(states[1]["can_mark_up_to"])
        states = tv_tracking.episode_states(self.user, show, 1, None)
        self.assertTrue(states[1]["can_mark_up_to"])
        self.assertFalse(states[2]["can_mark_up_to"])
        with self.assertRaises(TVInvalid):
            tv_tracking.mark_up_to(self.user, show, 0, 1)
        with self.assertRaises(TVInvalid):
            tv_tracking.mark_up_to(self.user, show, 1, 2)
        self.assertIsNone(self.tv(show))


class TV301FillsTests(TVTrackingTestCase):
    """TV-301: fills belong to the log or mark that made them."""

    def test_tv_301_fills_count_as_watched_and_never_move_last_watched(self):
        show = self.make_show({1: 3})
        self.tick(show, 1, 1)
        last = self.tv(show).last_watched_on
        tv_tracking.mark_watched(self.user, show)
        tv = self.tv(show)
        self.assertEqual(tv.last_watched_on, last)
        self.assertEqual((tv.watched_count, tv.aired_count), (3, 3))

    def test_tv_301_a_mark_ends_when_its_last_fill_is_removed(self):
        show = self.make_show({1: 2})
        tv_tracking.mark_watched(self.user, show)
        self.untick(show, 1, 1)
        self.assertEqual(self.status(show), "In progress")
        self.untick(show, 1, 2)
        self.assertIsNone(self.tv(show))
        self.assertFalse(Season.objects.exists())
