"""The calculation on TV: the Rewatch switch, logging something marked, backdated logs.

Decisions 14, 38, 42, 43, 48, 59 and 61, read when Stats are, with nothing written for
them. Every write is tv_tracking's own, on manual shows with 45-minute episodes, so no
provider is reached. Numbers are minutes, read as Stats read them: a year's rows, and
All Time's rows plus the undated pile.
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from api.services import stats_time
from api.services.activity_rows import list_rows, piled, rows_for
from api.services.stats_scope import build_scope
from app import tv_tracking, user_calendar
from app.models import DiaryEntry, Episode, Item, MediaTypes, Season
from app.tests.tv_fixtures import make_manual_show, make_user, patch_now
from social.models import Follow, FollowStatus

TV = MediaTypes.TV.value
NEW_YORK = "America/New_York"
E = 45


@override_settings(TIME_ZONE=NEW_YORK)
class TVCalcBase(TestCase):
    """tv_tracking's writes on a moving clock, read through the calculation."""

    def setUp(self):
        super().setUp()
        self.clock = patch_now(datetime(2026, 1, 5, 12), tz=NEW_YORK).__enter__()  # noqa: DTZ001
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.user = make_user(calendar=NEW_YORK)
        self.follower = make_user(calendar=NEW_YORK)
        self.stranger = make_user(calendar=NEW_YORK)
        Follow.objects.create(from_user=self.follower, to_user=self.user, status=FollowStatus.ACCEPTED)

    def show(self, *counts, minutes=E):
        """Return a manual show: one season per count, each episode `minutes` long."""
        show = make_manual_show(seasons=dict(enumerate(counts, start=1)))
        Item.objects.filter(media_id=show.media_id, media_type=MediaTypes.EPISODE.value).update(runtime_minutes=minutes)
        return show

    def season(self, show, number=1):
        return Item.objects.get(media_id=show.media_id, media_type=MediaTypes.SEASON.value, season_number=number)

    def episode(self, show, season, number):
        return Item.objects.get(
            media_id=show.media_id,
            media_type=MediaTypes.EPISODE.value,
            season_number=season,
            episode_number=number,
        )

    def on(self, day, hour=12):
        """Move the clock to this hour of a day, New York time."""
        self.clock.set(datetime(day.year, day.month, day.day, hour), tz=NEW_YORK)  # noqa: DTZ001

    def tick(self, show, *numbers, season=1):
        for number in numbers:
            self.clock.advance(minutes=1)
            tv_tracking.tick(self.user, show, season, number)

    def log(self, item, day=None, **kwargs):
        self.clock.advance(minutes=1)
        return tv_tracking.create_log(
            self.user,
            item,
            consumed_at=day or user_calendar.today(self.user),
            **kwargs,
        ).entry

    def eye(self, show, season=1):
        self.clock.advance(minutes=1)
        tv_tracking.mark_watched(self.user, show, season_number=season)

    def flip(self, entry, *, on):
        self.clock.advance(minutes=1)
        tv_tracking.update_log(DiaryEntry.objects.get(pk=entry.pk), {"is_rewatch": on})

    def delete(self, entry):
        self.clock.advance(minutes=1)
        tv_tracking.delete_log(self.user, DiaryEntry.objects.get(pk=entry.pk))

    def number(self, year=None, viewer=None):
        """Return TV minutes in a year, or in All Time with none."""
        calculation = rows_for([self.user], viewer or self.user, types=[TV])
        if year is None:
            rows = list_rows(calculation.rows, media_type=TV)
            return int(sum((row.amount for row in rows), piled(calculation.piles, TV).amount))
        rows = list_rows(calculation.rows, media_type=TV, start_date=date(year, 1, 1), end_date=date(year, 12, 31))
        return int(sum((row.amount for row in rows), Decimal(0)))

    def assert_minutes(self, year, in_year, all_time, viewer=None):
        self.assertEqual((self.number(year, viewer), self.number(viewer=viewer)), (in_year, all_time))
        self.assert_stats_agree(year, viewer)

    def assert_stats_agree(self, year, viewer=None):
        """Check Stats print what the list adds up to, episodes watched included."""
        for start, end in ((f"{year}-01-01", f"{year}-12-31"), ("all", "all")):
            scope = build_scope(self.user, viewer or self.user, {"start_date": start, "end_date": end, "media_type": TV})
            time = stats_time.type_time(scope, TV)
            calculation = rows_for([self.user], viewer or self.user, types=[TV])
            in_range = (None, None) if start == "all" else (date.fromisoformat(start), date.fromisoformat(end))
            rows = list_rows(calculation.rows, media_type=TV, start_date=in_range[0], end_date=in_range[1])
            pile = piled(calculation.piles if start == "all" else [], TV)
            self.assertEqual(time.value, int(sum((row.amount for row in rows), pile.amount)))
            self.assertEqual(time.progress, int(sum((row.told for row in rows), pile.told)))
            self.assertEqual(time.total, time.progress, "TV's note counts episodes")


class DayTests(TVCalcBase):
    """Ticks on their days; a log's fills on its date."""

    def test_an_untick_comes_off_its_own_day(self):
        show = self.show(3)
        self.on(date(2026, 3, 2))
        self.tick(show, 1, 2)
        self.on(date(2026, 3, 3))
        self.tick(show, 3)
        tv_tracking.untick(self.user, show, 1, 1)
        self.assert_minutes(2026, 2 * E, 2 * E)
        days = [row.day for row in list_rows(rows_for([self.user], self.user, types=[TV]).rows) if row.told]
        self.assertEqual(days, [date(2026, 3, 3), date(2026, 3, 2)])

    def test_two_ticks_then_a_season_log_adds_whats_left(self):
        show = self.show(5)
        self.on(date(2026, 3, 1))
        self.tick(show, 1, 2)
        self.on(date(2026, 4, 3))
        entry = self.log(self.season(show))
        self.assertFalse(entry.is_rewatch)
        self.assert_minutes(2026, 5 * E, 5 * E)
        log = next(row for row in rows_for([self.user], self.user, types=[TV]).rows if row.log_id)
        self.assertEqual((int(log.told), int(log.amount)), (3, 3 * E))

    def test_a_claimed_tick_stays_on_its_day_when_the_log_is_dated_later(self):
        show = self.show(3)
        self.on(date(2026, 3, 1))
        self.tick(show, 1)
        self.on(date(2026, 3, 5))
        entry = self.log(self.episode(show, 1, 1), day=date(2026, 3, 4))
        tick = Episode.objects.get(related_season__user=self.user)
        self.assertEqual((tick.diary_entry_id, tick.watched_on), (entry.pk, date(2026, 3, 1)))
        rows = rows_for([self.user], self.user, types=[TV]).rows
        self.assertEqual([(row.day, int(row.told)) for row in rows if row.told], [(date(2026, 3, 1), 1)])


class ReviewTests(TVCalcBase):
    """Decision 14: a review after a season log describes that watch, adding nothing."""

    def test_a_review_after_a_season_log_opens_off_and_adds_nothing(self):
        show = self.show(3)
        self.on(date(2026, 2, 1))
        self.log(self.season(show))
        self.on(date(2026, 2, 2))
        review = self.log(self.episode(show, 1, 2))
        self.assertFalse(review.is_rewatch)
        self.assertFalse(Episode.objects.filter(diary_entry=review).exists())
        self.assert_minutes(2026, 3 * E, 3 * E)
        log_days = {row.day for row in rows_for([self.user], self.user, types=[TV]).rows if row.log_id}
        self.assertEqual(log_days, {date(2026, 2, 1), date(2026, 2, 2)}, "The review's day is still an active day")

    def test_a_marked_episode_reviewed_off_dates_that_watch(self):
        """Decision 59: year +45, All Time stays 45."""
        show = self.show(3)
        self.on(date(2025, 6, 1))
        tv_tracking.mark_up_to(self.user, show, 1, 1)
        self.assert_minutes(2026, 0, E)
        self.on(date(2026, 2, 1))
        review = self.log(self.episode(show, 1, 1), is_rewatch=False)
        self.assert_minutes(2026, E, E)
        self.delete(review)
        self.assert_minutes(2026, 0, E)

    def test_a_marked_episode_reviewed_as_the_screen_opens_is_a_second_watch(self):
        show = self.show(3)
        self.on(date(2025, 6, 1))
        tv_tracking.mark_up_to(self.user, show, 1, 1)
        self.on(date(2026, 2, 1))
        review = self.log(self.episode(show, 1, 1))
        self.assertTrue(review.is_rewatch, "A-56: on after a mark")
        self.assert_minutes(2026, E, 2 * E)

    def test_a_season_a_rewatch_review_then_the_season_again_is_one_rewatch(self):
        """Decision 61: 270, not 315."""
        show = self.show(3)
        self.on(date(2026, 2, 1))
        self.log(self.season(show))
        self.on(date(2026, 2, 2))
        self.log(self.episode(show, 1, 2), is_rewatch=True)
        self.on(date(2026, 2, 3))
        again = self.log(self.season(show))
        self.assertTrue(again.is_rewatch)
        self.assert_minutes(2026, 6 * E, 6 * E)

    def test_an_untick_under_a_review_of_a_mark_is_refused(self):
        show = self.show(3)
        self.on(date(2026, 2, 1))
        tv_tracking.mark_up_to(self.user, show, 1, 1)
        self.log(self.episode(show, 1, 1), is_rewatch=False)
        with self.assertRaises(tv_tracking.TVLogBlocks):
            tv_tracking.untick(self.user, show, 1, 1)


class ReviewSwitchEditTests(TVCalcBase):
    """TV-402: an episode log's edited switch decides again, as saving it would."""

    def marked_review(self, *, on):
        show = self.show(3)
        self.on(date(2025, 6, 1))
        tv_tracking.mark_up_to(self.user, show, 1, 1)
        self.on(date(2026, 2, 1))
        return show, self.log(self.episode(show, 1, 1), is_rewatch=on)

    def test_a_marked_episodes_review_saved_on_then_flipped_off_dates_the_mark(self):
        """Decision 59, by an edit: off dates the same watch."""
        _show, review = self.marked_review(on=True)
        self.assert_minutes(2026, E, 2 * E)
        self.flip(review, on=False)
        self.assert_minutes(2026, E, E)
        self.flip(review, on=True)
        self.assert_minutes(2026, E, 2 * E)

    def test_a_marked_episodes_review_saved_off_then_flipped_on_is_a_second_watch(self):
        show, review = self.marked_review(on=False)
        self.assert_minutes(2026, E, E)
        self.flip(review, on=True)
        self.assert_minutes(2026, E, 2 * E)
        self.flip(review, on=False)
        self.assert_minutes(2026, E, E)
        with self.assertRaises(tv_tracking.TVLogBlocks):
            tv_tracking.untick(self.user, show, 1, 1)

    def test_a_review_after_a_season_log_flipped_on_then_off(self):
        """Decision 14, by an edit: on is a watch, off adds nothing."""
        show = self.show(3)
        self.on(date(2026, 2, 1))
        self.log(self.season(show))
        self.on(date(2026, 2, 2))
        review = self.log(self.episode(show, 1, 1))
        self.assertFalse(review.is_rewatch)
        self.flip(review, on=True)
        self.assert_minutes(2026, 4 * E, 4 * E)
        self.flip(review, on=False)
        self.assert_minutes(2026, 3 * E, 3 * E)
        self.assertFalse(Episode.objects.filter(diary_entry=review).exists())

    def test_a_ticks_review_saved_on_then_flipped_off_claims_the_tick(self):
        show = self.show(3)
        self.on(date(2026, 2, 1))
        self.tick(show, 1)
        self.on(date(2026, 2, 2))
        review = self.log(self.episode(show, 1, 1), is_rewatch=True)
        self.assert_minutes(2026, 2 * E, 2 * E)
        self.flip(review, on=False)
        self.assert_minutes(2026, E, E)
        self.assertEqual(
            list(Episode.objects.filter(diary_entry=review).values_list("kind", flat=True)),
            [Episode.Kind.TICK],
            "The tick is the log's own",
        )
        days = sorted(row.day for row in list_rows(rows_for([self.user], self.user, types=[TV]).rows) if row.told)
        self.assertEqual(days, [date(2026, 2, 1)], "and keeps its day")
        self.flip(review, on=True)
        self.assert_minutes(2026, 2 * E, 2 * E)

    def test_a_ticks_review_saved_off_then_flipped_on_adds_a_watch(self):
        show = self.show(3)
        self.on(date(2026, 2, 1))
        self.tick(show, 1)
        self.on(date(2026, 2, 2))
        review = self.log(self.episode(show, 1, 1), is_rewatch=False)
        self.assert_minutes(2026, E, E)
        self.flip(review, on=True)
        self.assert_minutes(2026, 2 * E, 2 * E)
        days = sorted(row.day for row in list_rows(rows_for([self.user], self.user, types=[TV]).rows) if row.told)
        self.assertEqual(days, [date(2026, 2, 1), date(2026, 2, 2)])

    def test_a_review_flipped_on_between_two_season_logs_is_still_one_rewatch(self):
        """Decision 61 holds when the review's switch is edited: 270, not 315."""
        show = self.show(3)
        self.on(date(2026, 2, 1))
        self.log(self.season(show))
        self.on(date(2026, 2, 2))
        review = self.log(self.episode(show, 1, 2), is_rewatch=False)
        self.on(date(2026, 2, 3))
        self.log(self.season(show), is_rewatch=True)
        self.assert_minutes(2026, 6 * E, 6 * E)
        self.flip(review, on=True)
        self.assert_minutes(2026, 6 * E, 6 * E)

    def test_a_first_watch_review_counts_once_with_either_switch(self):
        show = self.show(3)
        self.on(date(2026, 2, 1))
        review = self.log(self.episode(show, 1, 1))
        self.assertFalse(review.is_rewatch)
        self.flip(review, on=True)
        self.assert_minutes(2026, E, E)
        self.flip(review, on=False)
        self.assert_minutes(2026, E, E)
        self.assertEqual(Episode.objects.filter(diary_entry=review, kind=Episode.Kind.LOG).count(), 1)

    def test_during_a_rewatch_that_covers_it_the_switch_never_stops_the_claim(self):
        show = self.show(3)
        self.on(date(2025, 5, 1))
        self.log(self.season(show))
        self.on(date(2026, 3, 1))
        self.clock.advance(minutes=1)
        tv_tracking.start_rewatch(self.user, show, season_number=1)
        self.tick(show, 1)
        review = self.log(self.episode(show, 1, 1))
        self.assertTrue(review.is_rewatch)
        self.assert_minutes(2026, E, 4 * E)
        self.flip(review, on=False)
        self.assert_minutes(2026, E, 4 * E)
        self.assertEqual(
            list(Episode.objects.filter(diary_entry=review).values_list("kind", flat=True)),
            [Episode.Kind.TICK],
        )


class RewatchSwitchTests(TVCalcBase):
    """Decisions 14, 38 and 48: the switch decides what a season log adds."""

    def test_a_season_logged_again_with_rewatch_on_adds_it_again(self):
        show = self.show(10)
        self.on(date(2025, 5, 1))
        self.log(self.season(show))
        self.on(date(2026, 3, 1))
        again = self.log(self.season(show))
        self.assertTrue(again.is_rewatch, "A season log exists, so it opens on")
        self.assert_minutes(2026, 10 * E, 20 * E)
        self.flip(again, on=False)
        self.assert_minutes(2026, 0, 10 * E)
        self.assertEqual(
            [row.day for row in rows_for([self.user], self.user, types=[TV]).rows if row.day.year == 2026],
            [date(2026, 3, 1)],
            "Off, the log adds nothing but keeps its active day",
        )
        self.flip(again, on=True)
        self.delete(again)
        self.assert_minutes(2026, 0, 10 * E)

    def test_a_first_time_log_saved_on_then_flipped_off_still_counts_once(self):
        show = self.show(3)
        self.on(date(2026, 3, 1))
        entry = self.log(self.season(show), is_rewatch=True)
        self.assert_minutes(2026, 3 * E, 3 * E)
        self.flip(entry, on=False)
        self.assert_minutes(2026, 3 * E, 3 * E)

    def test_a_season_eye_then_a_log_as_it_opens_then_a_review(self):
        show = self.show(3)
        self.on(date(2026, 1, 15))
        self.eye(show)
        self.assert_minutes(2026, 0, 3 * E)
        self.on(date(2026, 3, 1))
        entry = self.log(self.season(show))
        self.assertTrue(entry.is_rewatch, "Only marks under it, so it opens on")
        self.assert_minutes(2026, 3 * E, 6 * E)
        self.on(date(2026, 3, 2))
        self.log(self.episode(show, 1, 1))
        self.assert_minutes(2026, 3 * E, 6 * E)

    def test_the_same_log_with_rewatch_off_dates_the_mark(self):
        show = self.show(3)
        self.on(date(2026, 1, 15))
        self.eye(show)
        self.on(date(2026, 3, 1))
        entry = self.log(self.season(show), is_rewatch=False)
        self.assert_minutes(2026, 3 * E, 3 * E)
        moved = rows_for([self.user], self.user, types=[TV]).moved
        self.assertEqual(set(moved.values()), {date(2026, 3, 1)})
        self.delete(entry)
        self.assert_minutes(2026, 0, 3 * E)

    def test_eye_first_log_off_second_on_is_two_watches_not_three(self):
        show = self.show(3)
        self.on(date(2026, 1, 15))
        self.eye(show)
        self.on(date(2026, 3, 1))
        first = self.log(self.season(show), is_rewatch=False)
        self.on(date(2026, 3, 2))
        self.log(self.season(show), is_rewatch=True)
        self.assert_minutes(2026, 6 * E, 6 * E)
        self.delete(first)
        self.assert_minutes(2026, 3 * E, 6 * E)

    def test_two_seasons_by_the_show_eye_then_the_first_logged_off(self):
        show = self.show(3, 2)
        self.on(date(2026, 1, 15))
        self.clock.advance(minutes=1)
        tv_tracking.mark_watched(self.user, show)
        self.on(date(2026, 3, 1))
        self.log(self.season(show, 1), is_rewatch=False)
        self.assert_minutes(2026, 3 * E, 5 * E)


class TicksThenLogTests(TVCalcBase):
    """Decision 42: ticks that already counted a rewatch."""

    def test_a_rewatch_ticked_to_the_end_then_the_finish_log_on(self):
        show = self.show(3)
        self.on(date(2025, 5, 1))
        self.log(self.season(show))
        self.on(date(2026, 3, 1))
        self.clock.advance(minutes=1)
        tv_tracking.start_rewatch(self.user, show, season_number=1)
        self.tick(show, 1, 2, 3)
        entry = self.log(self.season(show), is_rewatch=True)
        self.assert_minutes(2026, 3 * E, 6 * E)
        self.assertTrue(entry.is_rewatch)

    def test_the_eye_a_rewatch_by_ticks_then_a_log_off(self):
        show = self.show(3)
        self.on(date(2025, 5, 1))
        self.eye(show)
        self.on(date(2026, 3, 1))
        self.clock.advance(minutes=1)
        tv_tracking.start_rewatch(self.user, show, season_number=1)
        self.tick(show, 1, 2, 3)
        self.log(self.season(show), is_rewatch=False)
        self.assert_minutes(2026, 3 * E, 6 * E)


class OldLogTests(TVCalcBase):
    """Decision 48: old Rewatch-on logs count again, with no flip."""

    def game_of_thrones(self):
        show = self.show(3)
        self.on(date(2026, 1, 15))
        self.eye(show)
        self.on(date(2026, 4, 3))
        self.clock.advance(minutes=1)
        tv_tracking.start_rewatch(self.user, show, season_number=1)
        return show, self.log(self.season(show), is_rewatch=True)

    def test_a_mark_then_a_rewatch_log_holding_its_own_fills_is_two_watches(self):
        _show, entry = self.game_of_thrones()
        self.assertEqual(Episode.objects.filter(diary_entry=entry).count(), 3)
        self.assert_minutes(2026, 3 * E, 6 * E)

    def test_flipped_off_it_reads_once_when_its_marks_are_older(self):
        _show, entry = self.game_of_thrones()
        self.flip(entry, on=False)
        self.assert_minutes(2026, 3 * E, 3 * E)

    def test_two_marked_and_the_third_the_logs_own_reads_135_and_225(self):
        show = self.show(3)
        self.on(date(2026, 1, 15))
        self.clock.advance(minutes=1)
        tv_tracking.mark_up_to(self.user, show, 1, 2)
        self.on(date(2026, 3, 1))
        self.log(self.season(show), is_rewatch=True)
        self.assert_minutes(2026, 3 * E, 5 * E)

    def test_an_installed_builds_rewatch_on_stays_a_label(self):
        show = self.show(3)
        self.on(date(2025, 5, 1))
        self.log(self.season(show))
        self.on(date(2026, 3, 1))
        self.clock.advance(minutes=1)
        entry = tv_tracking.create_log(
            self.user,
            self.season(show),
            consumed_at=date(2026, 3, 1),
            is_rewatch=True,
            entry_point=tv_tracking.LEGACY_APP,
        ).entry
        self.assertTrue(entry.progress_snapshot["tv"]["label_only"])
        self.assert_minutes(2026, 0, 3 * E)


class UndatedPlayTests(TVCalcBase):
    """An imported play with no date is a mark: only a log dates it (decision 38)."""

    def undated(self, show, *numbers):
        """Add plays as an import with no date saves them: ticks with no day."""
        row = Season.objects.get(user=self.user, item=self.season(show))
        for number in numbers:
            self.clock.advance(minutes=1)
            Episode.objects.create(item=self.episode(show, 1, number), related_season=row, end_date=None)

    def test_an_episode_log_over_an_undated_play_dates_it(self):
        show = self.show(3)
        self.on(date(2026, 3, 1))
        self.tick(show, 3)
        self.undated(show, 1)
        self.assert_minutes(2026, E, 2 * E)
        self.on(date(2026, 3, 5))
        entry = self.log(self.episode(show, 1, 1))
        self.assertFalse(entry.is_rewatch, "It claims the play")
        self.assert_minutes(2026, 2 * E, 2 * E)
        log = next(row for row in rows_for([self.user], self.user, types=[TV]).rows if row.log_id)
        self.assertEqual((log.day, int(log.told), int(log.amount)), (date(2026, 3, 5), 1, E))
        self.delete(entry)
        self.assert_minutes(2026, E, 2 * E)

    def test_a_season_log_off_dates_undated_plays_whatever_a_ticks_day(self):
        show = self.show(4)
        self.on(date(2026, 3, 1))
        self.tick(show, 4)
        self.undated(show, 1, 2, 3)
        self.assert_minutes(2026, E, 4 * E)
        self.on(date(2026, 3, 5))
        entry = self.log(self.season(show), day=date(2026, 3, 4), is_rewatch=False)
        self.assert_minutes(2026, 4 * E, 4 * E)
        self.delete(entry)
        self.on(date(2026, 3, 6))
        self.log(self.season(show), day=date(2025, 6, 1), is_rewatch=False)
        self.assert_minutes(2025, 4 * E, 4 * E)
        self.assertEqual(self.number(2026), 0)

    def test_undated_plays_stay_in_the_pile_under_a_rewatch_on_log(self):
        show = self.show(4)
        self.on(date(2026, 3, 1))
        self.tick(show, 4)
        self.undated(show, 1, 2, 3)
        self.on(date(2026, 3, 5))
        self.log(self.season(show), is_rewatch=True)
        self.assert_minutes(2026, 5 * E, 8 * E)


class BackdatedTests(TVCalcBase):
    """Decision 43: a season log dated before its first tick takes the ticks."""

    def test_three_ticks_then_a_season_log_dated_before_them(self):
        show = self.show(5)
        self.on(date(2026, 3, 1))
        self.tick(show, 1, 2, 3)
        entry = self.log(self.season(show), day=date(2025, 12, 20))
        self.assertFalse(entry.is_rewatch)
        self.assert_minutes(2025, 5 * E, 5 * E)
        self.assertEqual(self.number(2026), 0)
        self.delete(entry)
        self.assert_minutes(2026, 3 * E, 3 * E)
        self.assertEqual(self.number(2025), 0)

    def test_a_log_dated_after_the_ticks_leaves_them(self):
        show = self.show(5)
        self.on(date(2026, 3, 1))
        self.tick(show, 1, 2, 3)
        self.on(date(2026, 4, 1))
        self.log(self.season(show))
        self.assertEqual((self.number(2026), self.number(2025)), (5 * E, 0))

    def test_a_private_backdated_log_moves_nothing_for_a_stranger(self):
        show = self.show(5)
        self.on(date(2026, 3, 1))
        self.tick(show, 1, 2)
        self.log(self.season(show), day=date(2025, 12, 20), visibility="private")
        self.assert_minutes(2025, 5 * E, 5 * E)
        self.assert_minutes(2025, 0, 2 * E, viewer=self.stranger)
        self.assertEqual(self.number(2026, viewer=self.stranger), 2 * E)
        self.assert_minutes(2025, 0, 2 * E, viewer=self.follower)


@override_settings(TIME_ZONE=NEW_YORK)
class NewYearTests(TVCalcBase):
    """A tick at 00:10 Jan 1 and a log on Dec 31, read from Tokyo, keep their years."""

    def test_the_owners_calendar_decides(self):
        show = self.show(3)
        self.clock.set(datetime(2026, 1, 1, 0, 10, tzinfo=ZoneInfo(NEW_YORK)))
        self.tick(show, 1)
        self.log(self.season(show), day=date(2025, 12, 31))
        token = user_calendar.REQUEST_TIMEZONE.set(ZoneInfo("Asia/Tokyo"))
        try:
            self.assertEqual((self.number(2025), self.number(2026)), (3 * E, 0))
        finally:
            user_calendar.REQUEST_TIMEZONE.reset(token)
