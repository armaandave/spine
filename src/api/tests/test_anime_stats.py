"""AN-703 and AN-704: what activity shows and what stats count.

A mark must never land in a year. That is the product's core tenet, so it's tested on
every path a mark can take and every number that could show it.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

from api.services import stats_time, stats_v2, stats_v2_activity
from api.services.stats_scope import build_scope
from app import anime_tracking
from app.models import Anime, DiaryEntry
from app.tests.anime_fixtures import AnimeTestCase, make_item
from social.models import Activity, ProgressChange


class StatsTests(AnimeTestCase):
    """AN-704."""

    def scope(self, year=None, media_type="anime", viewer=None):
        params = {"media_type": media_type, "start_date": "all", "end_date": "all"}
        if media_type is None:
            params.pop("media_type")
        if year is not None:
            params.update({"start_date": f"{year}-01-01", "end_date": f"{year}-12-31"})
        return build_scope(self.user, viewer or self.user, params)

    def assert_in_no_year(self, *years):
        for year in years:
            self.assertEqual(self.stats_time(year), (0, 0), year)
            scope = self.scope(year)
            self.assertEqual(scope.events, [], year)
            self.assertEqual(scope.titles.count(), 0, year)
            header = stats_v2_activity.build_header(scope)
            self.assertEqual(header["active_days"], 0, year)
            episodes = next(detail for detail in header["details"] if detail["key"] == "episodes")
            self.assertEqual(episodes["value"], 0, year)

    def test_an_704_a_tick_counts_on_its_day_and_unticking_takes_it_off(self):
        item = self.title(total=3)
        self.tick(item, 1, 2)
        self.assertEqual(self.stats_time(2026), (48, 2))
        anime_tracking.untick(self.user, item, 2)
        self.assertEqual(self.stats_time(2026), (24, 1))

    def test_an_704_a_tick_lands_on_the_owners_calendar_day(self):
        # 9 p.m. in Los Angeles on Dec 31 is already Jan 1 in New York and UTC.
        self.clock.set(datetime(2025, 12, 31, 21, 0), tz="America/Los_Angeles")  # noqa: DTZ001
        item = self.title(total=3)
        self.tick(item, 1)
        self.assertEqual(self.stats_time(2025), (24, 1))
        self.assertEqual(self.stats_time(2026), (0, 0))

    def test_an_704_a_logs_fills_count_on_the_logs_date_and_go_with_it(self):
        item = self.title(total=3)
        self.tick(item, 1)
        entry = self.log(item, day=date(2025, 3, 1)).entry
        self.assertEqual(self.stats_time(2025), (48, 2))
        self.assertEqual(self.stats_time(2026), (24, 1))
        anime_tracking.update_log(entry, {"consumed_at": date(2024, 3, 1)})
        self.assertEqual(self.stats_time(2024), (48, 2))
        self.assertEqual(self.stats_time(2025), (0, 0))
        anime_tracking.delete_log(self.user, entry)
        self.assertEqual(self.stats_time(2024), (0, 0))
        self.assertEqual(self.stats_time()[1], 1)

    def test_an_704_a_marks_fills_count_only_in_all_time(self):
        """The core tenet, on every mark: eye, up to here, opinions, status, imports."""
        marks = [
            lambda item: anime_tracking.mark_watched(self.user, item),
            lambda item: anime_tracking.mark_up_to(self.user, item, 3),
            lambda item: anime_tracking.set_rating(self.user, item, 8),
            lambda item: anime_tracking.set_like(self.user, item, liked=True),
            lambda item: anime_tracking.apply_written_state(
                self.user,
                item,
                {"status": "Completed"},
                entry_point=anime_tracking.LEGACY_APP,
            ),
            lambda item: anime_tracking.import_title(self.user, item, status="Completed", total=3),
            lambda item: anime_tracking.import_title(self.user, item, status="In progress", count=3),
        ]
        expected = 0
        for mark in marks:
            item = self.title(total=3)
            mark(item)
            expected += 3
            self.assert_in_no_year(2024, 2025, 2026)
            self.assertEqual(self.stats_time(), (expected * 24, expected))

    def test_an_704_a_yes_to_the_earlier_question_is_a_mark(self):
        item = self.title(total=5)
        first = self.tick(item, 5)
        anime_tracking.mark_earlier(
            self.user,
            item,
            from_ref=first.earlier_unwatched["from"],
            to_ref=first.earlier_unwatched["to"],
            mutation_id=uuid4(),
        )
        self.assertEqual(self.stats_time(2026), (24, 1))
        self.assertEqual(self.stats_time(), (120, 5))

    def test_an_704_a_mark_never_makes_a_day_active_or_a_multi_title_day(self):
        marked = self.title(total=3)
        watched = self.title(total=3)
        anime_tracking.mark_watched(self.user, marked)
        self.tick(watched, 1)
        scope = self.scope(2026)
        self.assertEqual(len(scope.events), 1)
        self.assertEqual(stats_v2_activity.days_with_two_plus_titles(scope.events), 0)
        self.assertEqual(stats_v2_activity.build_header(scope)["active_days"], 1)

    def test_an_704_first_year_ignores_marks(self):
        item = self.title(total=3)
        anime_tracking.mark_watched(self.user, item)
        self.assertIsNone(stats_v2.first_year(self.scope()))
        other = self.title(total=3)
        self.log(other, day=date(2024, 5, 1))
        self.assertEqual(stats_v2.first_year(self.scope()), 2024)

    def test_an_704_a_rewatch_counts_again(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        anime_tracking.start_rewatch(self.user, item)
        self.tick(item, 1)
        self.assertEqual(self.stats_time(2026), (72, 3))

    def test_an_704_a_stored_number_with_no_viewings_counts_only_in_all_time(self):
        item = self.title(total=12)
        Anime(user=self.user, item=item, status="In progress", progress=5).save_base(force_insert=True)
        self.assertEqual(self.stats_time(), (120, 5))
        self.assert_in_no_year(2026)
        time = stats_time.type_time(self.scope(), "anime")
        self.assertEqual((time.known, time.total), (1, 1))
        anime_tracking.set_rating(self.user, item, None)
        self.assertEqual(self.stats_time(), (120, 5))
        self.assert_in_no_year(2026)

    def test_an_704_each_viewing_is_the_titles_runtime_and_never_approximate(self):
        item = self.title(total=2)
        self.tick(item, 1)
        time = stats_time.type_time(self.scope(2026), "anime")
        self.assertEqual((time.value, time.known, time.total, time.approximate), (24, 1, 1, False))
        unknown = make_item(self.fake, title=self.fake.add(runtime=0, total=2))
        self.tick(unknown, 1)
        time = stats_time.type_time(self.scope(2026), "anime")
        self.assertEqual((time.value, time.known, time.total), (24, 1, 2))

    def test_an_704_the_header_says_episodes_watched_exactly(self):
        item = self.title(total=2)
        self.tick(item, 1)
        detail = next(row for row in stats_v2_activity.build_header(self.scope(2026))["details"] if row["key"] == "episodes")
        self.assertEqual((detail["value"], detail["approximate"]), (1, False))

    def test_an_704_a_tick_makes_the_title_count_in_its_year(self):
        item = self.title(total=2)
        self.tick(item, 1)
        self.assertEqual(list(self.scope(2026).titles.values_list("pk", flat=True)), [item.pk])
        self.assertEqual(self.scope(2025).titles.count(), 0)

    def test_stat_105_a_marked_finished_title_is_an_undated_completion_in_all_time(self):
        item = self.title(total=2)
        anime_tracking.mark_watched(self.user, item)
        self.assertEqual(list(self.scope().titles.values_list("pk", flat=True)), [item.pk])

    def test_an_704_the_global_tab_counts_anime_viewings_too(self):
        item = self.title(total=2)
        self.tick(item, 1)
        scope = self.scope(2026, media_type=None)
        self.assertEqual(len(scope.events), 1)
        self.assertEqual(scope.events[0][2], "anime")

    def test_stat_003_a_private_logs_fills_are_hidden_from_other_viewers(self):
        from app.tests.tv_fixtures import make_user

        item = self.title(total=2)
        self.log(item, visibility="private")
        viewer = make_user()
        self.assertEqual(stats_time.type_time(self.scope(2026), "anime").progress, 2)
        self.assertEqual(stats_time.type_time(self.scope(2026, viewer=viewer), "anime").progress, 0)

    def test_stat_108_a_log_after_a_mark_saved_as_a_rewatch_is_a_rewatch(self):
        item = self.title(total=2)
        anime_tracking.mark_watched(self.user, item)
        self.clock.advance(minutes=1)
        self.log(item)
        self.assertEqual(len(stats_v2_activity.rewatch_ids(self.scope(2026))), 1)

    def test_stat_100_an_old_log_at_a_real_moment_keeps_its_day(self):
        """An anime log from before the contract is read on the owner's calendar."""
        item = self.title(total=2)
        DiaryEntry.objects.create(user=self.user, item=item, consumed_at=datetime(2026, 1, 1, 4, 0, tzinfo=UTC))
        self.assertEqual(self.scope(2025).entries.count(), 1)
        self.assertEqual(self.scope(2026).entries.count(), 0)

    def test_stat_100_a_new_log_is_its_calendar_day_for_any_viewer(self):
        item = self.title(total=2)
        self.log(item, day=date(2026, 1, 1))
        self.assertEqual(self.scope(2026).entries.count(), 1)
        self.assertEqual(self.scope(2025).entries.count(), 0)


class ActivityTests(AnimeTestCase):
    """AN-703."""

    def summaries(self):
        return [(row["type"], row["object"].get("summary")) for row in self.activity()]

    def test_an_703_a_days_ticks_are_one_item(self):
        item = self.title(total=5)
        for number in (1, 2, 3, 4):
            self.action(item, "tick", episode_number=number)
        self.assertEqual(self.summaries(), [("progress_updated", "watched 4 episodes")])

    def test_an_703_unticks_count_against_that_days_ticks(self):
        item = self.title(total=5)
        self.action(item, "tick", episode_number=1)
        self.action(item, "tick", episode_number=2)
        self.action(item, "untick", episode_number=2)
        self.assertEqual(self.summaries(), [("progress_updated", "watched 1 episode")])

    def test_an_703_marks_and_fills_are_never_activity(self):
        item = self.title(total=5)
        self.action(item, "mark_watched")
        self.assertEqual(self.summaries(), [])
        other = self.title(total=5)
        self.write(other, status="Completed")
        self.assertEqual(self.summaries(), [])

    def test_tv_703_a_mark_records_the_new_count_without_moving_it(self):
        item = self.title(total=5)
        self.action(item, "tick", episode_number=1)
        self.action(item, "mark_watched")
        change = ProgressChange.objects.filter(actor=self.user, item=item).order_by("-id").first()
        self.assertEqual(change.current_progress["value"], 5)
        self.assertEqual(change.previous_progress["value"], 5)
        self.assertEqual(self.summaries(), [("progress_updated", "watched 1 episode")])

    def test_tv_703_a_tick_moves_it_by_one(self):
        item = self.title(total=5)
        self.action(item, "tick", episode_number=1)
        change = ProgressChange.objects.filter(actor=self.user, item=item, current_progress__unit="episode").get()
        self.assertEqual((change.previous_progress["value"], change.current_progress["value"]), (0, 1))
        self.assertEqual((change.current_progress["kind"], change.current_progress["max"]), ("count", 5))

    def test_tv_703_writes_record_no_social_activity_rows(self):
        item = self.title(total=5)
        self.action(item, "tick", episode_number=1)
        self.action(item, "mark_watched")
        self.assertFalse(Activity.objects.filter(actor=self.user, item=item).exists())

    def test_an_703_starting_is_activity(self):
        item = self.title(total=5)
        self.action(item, "watching")
        self.assertEqual(self.summaries(), [("progress_updated", "started watching")])

    def test_an_703_a_tick_that_puts_it_in_watching_is_progress_over_a_start(self):
        item = self.title(total=5)
        self.action(item, "tick", episode_number=1)
        self.assertEqual(self.summaries(), [("progress_updated", "watched 1 episode")])

    def test_an_703_resuming_isnt_starting(self):
        item = self.title(total=5)
        self.action(item, "pause")
        self.action(item, "resume")
        self.assertEqual(self.summaries(), [])

    def test_an_703_starting_a_rewatch_is_activity(self):
        item = self.title(total=2)
        self.action(item, "mark_watched")
        self.action(item, "rewatch")
        self.assertEqual(self.summaries(), [("progress_updated", "started watching")])

    def test_an_703_a_log_is_one_item_on_its_calendar_day(self):
        item = self.title(total=2)
        self.post_log(item, consumed_at="2026-03-01", mutation_id=str(uuid4()))
        activity = self.activity()
        self.assertEqual(len(activity), 1)
        self.assertEqual((activity[0]["type"], activity[0]["consumed_at"]), ("diary_created", "2026-03-01"))
        self.assertEqual(Activity.objects.filter(actor=self.user, verb="diary_created").count(), 1)

    def test_an_703_imports_and_webhook_marks_start_nothing(self):
        item = self.title(total=2)
        anime_tracking.import_title(self.user, item, status="In progress", count=1)
        self.assertEqual(self.summaries(), [])

    def test_an_703_leaving_the_library_takes_starts_and_progress_with_it(self):
        item = self.title(total=2)
        self.action(item, "tick", episode_number=1)
        self.action(item, "untick", episode_number=1)
        self.assertFalse(ProgressChange.objects.filter(actor=self.user, item=item).exists())
        self.assertEqual(self.summaries(), [])

    def test_ac_a_rating_with_no_log_isnt_activity(self):
        item = self.title(total=2)
        self.action(item, "rate", rating=8)
        self.assertEqual(self.summaries(), [])
        self.assertTrue(Activity.objects.filter(actor=self.user, verb="rating_updated").exists())

    def test_an_703_a_friends_progress_label_reads_the_count(self):
        item = self.title(total=5)
        self.action(item, "tick", episode_number=1)
        tracking = self.api.get(self.url(item)).data
        self.assertEqual(tracking["latest_progress_change"]["current"]["value"], 1)
