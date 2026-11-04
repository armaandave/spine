# Anime tracking coverage

Status: every law has automated backend tests. The app half isn't built yet, so nothing has run in the simulator or on a phone; the laws about screens rest on the payload keys that drive them.

This matrix maps every numbered law in [the anime tracking contract](anime-tracking-contract.md) to the backend tests that pin it. [App payload](#app-payload) at the end is what the app half builds on.

## Evidence key

Test names below are prefixed with the short name of their file.
A test's name starts with what it pins: `test_an_301_...`, `test_tv_107_...`, `test_n_12_...`.

| Short name | File | What it drives |
| --- | --- | --- |
| `domain` | `src/app/tests/test_anime_tracking.py` | `app.anime_tracking` called directly: `LibraryTests`, `TickTests`, `AiredTests`, `UpNextTests`, `EarlierQuestionTests`, `FinishingTests`, `EyeTests`, `OpinionTests`, `LogTests`, `SingleTests`, `NextTitleTests`, `RewatchTests`, `WrittenStateTests`, `AgreementTests`, `NoNumbersTests`, `PlanningCardTests`, `CalendarEventsTests`. |
| `listing` | `src/app/tests/test_anime_listing.py` | `app.anime_listing` and `anilist.anime_episodes`: parsing, the stored copy, the short timeout, backing off, the calendar job. |
| `schema` | `src/app/tests/test_anime_schema.py` | The old code's raw inserts against the new tables, the historical table too, and the migration's operations. |
| `contract` | `src/api/tests/test_anime_tracking_contract.py` | Through the API: `AcceptanceTests` (N-01 to N-30), `RoutesTests`, `InstalledBuildTests`. |
| `stats` | `src/api/tests/test_anime_stats.py` | Stats (`StatsTests`) and the feed (`ActivityTests`). |
| `imports` | `src/integrations/tests/imports/test_anime_import_contract.py` | MAL, MAL export, AniList, Kitsu, SIMKL, Yamtrack, the export, and webhooks. |
| `web` | `src/app/tests/views/test_anime_web_contract.py` | The web's forms, progress buttons, status actions, log edit and delete. |
| `vendor` | `src/integrations/tests/imports/test_{mal,mal_export,anilist,kitsu,yamtrack}.py`, `src/integrations/tests/test_webhooks_{plex,jellyfin,emby}.py` | The vendor suites, updated to one record per title. |
| `airing` | `src/events/tests/test_tv_airing.py` | The hourly task's schedule. |
| `stats-v2` | `src/api/tests/test_stats_v2_activity.py`, `test_stats_v2_drills.py` | The shared stats suites with anime on viewings. |

`src/app/tests/anime_fixtures.py` holds `FakeAnimeProvider` (MAL and AniList faked, every call counted, failures on demand, any other provider request raises) and `AnimeTestCase`: a fixed clock, a server on America/New_York and an account calendar on America/Los_Angeles.
`src/app/tests/test_anime_export_round_trip.py` holds the round trip `imports` and `contract` share.
Every test runs with no `.env`, no API keys and no network.

## AN-000: what anime inherits from TV

| Area | Rules | Tests |
| --- | --- | --- |
| Library sections | TV-002 | `domain` `test_tv_002_five_sections_status_alone_decides` |
| Membership and removal | TV-003 | `domain` `test_tv_003_removal_says_what_it_removes_and_clears_opinions`, `test_tv_003_removal_is_blocked_while_a_log_exists`, `test_tv_003_a_stale_removal_says_the_new_count`; `web` `test_tv_003_delete_keeps_the_log_guard`; `contract` `test_routes_removal_needs_a_mutation_id_from_the_new_build`, `test_an_900_tracking_removal_goes_through_these_rules` |
| The + menu | TV-005 | `domain` `test_tv_005_the_menu_of_an_untracked_title`, `test_tv_005_resume_takes_watchings_place_when_paused`, `test_tv_005_a_finished_title_offers_rewatch_and_explains_watching`, `test_tv_005_nothing_aired_explains` |
| Planning | TV-006 | `domain` `test_tv_006_planning_is_only_while_nothing_is_watched` |
| Pause and Drop | TV-007 | `domain` `test_tv_007_pause_and_drop_keep_the_place`, `test_tv_007_pause_and_drop_invent_nothing_with_nothing_watched`, `test_tv_007_a_dropped_finished_title_returns_to_completed`; `web` `test_tv_007_pause_resume_and_drop_go_through_the_module` |
| Ticks | TV-100, TV-101, TV-102 | `domain` `test_tv_100_a_tick_is_one_viewing_dated_now_on_the_users_calendar`, `test_tv_101_a_retry_adds_nothing`, `test_tv_102_untick_removes_the_latest_unlogged_viewing`, `test_tv_102_an_untick_naming_a_gone_viewing_changes_nothing`, `test_tv_102_a_logged_episode_says_delete_your_log_first`; N-02, N-05 |
| Aired, up next, refusals | TV-103, TV-105, TV-106 | AN-100, AN-102, AN-103 below; `domain` `test_tv_105_up_next_follows_the_latest_tick`, `test_tv_105_after_the_last_it_wraps_to_the_first_unwatched`, `test_tv_105_up_next_carries_the_rows_title_and_image` |
| The earlier-episodes question | TV-107 | `domain` `test_tv_107_a_first_tick_of_e1_asks_nothing`, `test_tv_107_the_question_is_used_up_answered_or_not`, `test_tv_107_a_choice_keeps_the_used_up_question_through_an_undo`, `test_tv_107_webhooks_and_installed_builds_dont_show_it`, `test_tv_107_yes_needs_a_mutation_id_and_applies_once`, `test_tv_107_yes_after_ticking_the_last_first_opens_the_log_screen`; N-03, N-04 |
| Mark watched up to here | TV-108 | `domain` `test_tv_108_mark_up_to_marks_that_episode_and_every_unwatched_before_it`, `test_tv_108_not_on_an_unaired_episode`, `test_tv_108_rows_give_the_first_unwatched_for_the_prompt`, `test_tv_108_up_to_any_number` |
| Finishing and reopening | TV-200, TV-206 | AN-200, AN-202 below |
| Choices and undoing | TV-203, TV-208 | `domain` `test_tv_203_only_the_user_ends_a_choice`, `test_tv_208_undoing_the_write_that_ended_a_choice_restores_it`, `test_tv_208_watching_chosen_first_keeps_the_title_in_watching`, `test_tv_208_leaving_the_library_clears_stars_and_the_heart`; N-22 |
| One worked-out state | TV-207 | `domain` `test_tv_207_working_out_twice_changes_nothing`, `test_tv_207_lists_read_stored_values_with_no_provider_call` |
| The eye | TV-300, TV-301, TV-302 | AN-300 below; N-09, N-10 |
| Stars and the heart | TV-303, TV-304, TV-500, TV-501, TV-503, TV-504 | AN-301 below; `domain` `test_tv_501_source_rules`, `test_tv_501_editing_a_source_log_updates_the_current_value`, `test_tv_501_a_log_given_new_stars_fills_empty_ones`, `test_tv_503_the_community_rating_counts_each_person_once`, `test_tv_504_off_scale_ratings_are_refused`, `test_tv_800_an_import_on_a_partway_title_only_sets_the_value`; `web` `test_tv_304_a_form_setting_stars_partway_says_log_this_first`; `contract` `test_routes_star_scale_requests_rate_in_stars` |
| Logs | TV-400, TV-401, TV-403 to TV-407 | AN-203, AN-400 below; `domain` `test_tv_400_another_log_keeps_what_it_covered`, `test_tv_404_the_rewatch_default`, `test_tv_405_editing_a_logs_date_moves_no_viewing`, `test_tv_406_deleting_removes_only_its_fills`, `test_tv_406_a_stale_delete_says_the_new_count`, `test_tv_406_deleting_the_only_log_leaves_the_library`, `test_tv_407_a_datetime_converts_to_the_users_calendar`, `test_tv_407_a_future_edit_is_refused`, `test_stat_100_an_old_log_at_a_real_moment_keeps_its_zone`; `web` `test_tv_405_the_webs_log_edit_goes_through_the_module`, `test_tv_406_the_webs_log_delete_goes_through_the_module`, `test_tv_407_a_future_web_edit_is_refused`; `contract` `test_routes_log_edit_and_delete`, `test_routes_a_future_log_is_a_400` |
| Rewatch | TV-600, TV-601, TV-602 | AN-600 below; N-21 |
| Calendar and notifications | TV-704 | `domain` `test_tv_704_planning_watching_and_completed_get_events_and_paused_and_dropped_dont` |
| Agreement and retries | TV-705 | `domain` `test_tv_705_a_logs_mutation_id_applies_it_once`, `test_tv_705_a_mutation_id_for_another_write_is_refused`, `test_tv_705_a_write_with_no_row_leaves_nothing_when_refused`, `test_tv_705_surfaces_agree_after_a_write` |

## The numbered laws

| Law | What it says | Tests |
| --- | --- | --- |
| AN-001 | One card per title | `domain` `test_an_001_a_title_tracked_as_tv_is_a_separate_thing`; `schema` `test_an_001_a_second_row_for_one_title_is_refused` |
| AN-002 | A single works like a movie | `domain` `test_an_002_total_of_one_or_a_movie_with_no_total_is_single`, `test_an_002_no_rows_no_up_next_and_no_question`, `test_an_002_the_eye_and_opinions_mark_it`, `test_an_002_it_has_no_partway`, `test_an_002_watching_again_is_another_log_and_counts_again`, `test_an_002_a_log_after_a_mark_dates_it_and_deleting_gives_the_mark_back`, `test_an_002_an_imported_single_stays_on_its_shelf_until_marked`, `test_an_002_it_never_appears_in_in_progress`, `test_an_002_there_is_no_rewatch_action`; N-23 |
| AN-100 | What has aired | `domain` `test_an_100_mal_finished_within_the_total`, `test_an_100_the_schedule_says_its_time_has_passed`, `test_an_100_anilist_names_a_later_episode`, `test_an_100_a_viewing_makes_it_aired`, `test_an_100_a_total_with_no_schedule_counts_every_listed_episode`, `test_an_100_correction_an_upcoming_title_has_nothing_aired`, `test_an_100_a_write_with_no_copy_is_refused_with_a_retryable_message`, `test_an_100_a_stored_copy_answers_when_providers_are_down`; `listing` `test_an_100_with_no_copy_and_mal_down_get_listing_says_unavailable`, `test_an_100_the_copy_keeps_total_next_episode_and_rows`, `test_an_100_a_partial_start_date_isnt_a_premiere`, `test_an_100_page_metadata_updates_the_numbers_with_no_call`, `test_an_100_a_usual_page_view_writes_nothing`, `test_an_100_mal_down_keeps_the_numbers_and_works_the_rest_out`; `contract` `test_routes_details_unavailable_is_a_retryable_503` |
| AN-101 | What a row shows | `domain` `test_an_101_rows_are_aired_then_the_unaired_spine_knows_of`, `test_an_101_rows_never_wait_a_failed_fetch_leaves_the_number`, `test_an_101_a_row_names_the_viewing_an_untick_removes`, `test_an_101_one_piece_stays_cheap_in_queries_and_bytes`, `test_an_101_a_mark_of_a_long_runner_is_one_request_in_bulk`, `test_an_101_the_list_runs_to_one_past_the_last_watched`, `test_an_101_listed_rows_with_no_schedule_can_all_be_ticked`; `listing` `test_an_101_the_episode_number_is_parsed_out_of_the_title`, `test_an_101_order_isnt_guaranteed_and_a_second_site_doesnt_replace_the_first`, `test_an_101_a_finished_title_has_no_schedule`, `test_an_101_one_request_per_25_titles`, `test_an_101_a_first_open_tries_once_with_a_short_timeout_then_reads_the_copy`, `test_an_101_a_failure_stores_the_numbers_and_backs_off`, `test_an_101_a_page_answers_when_every_provider_is_down`, `test_an_101_a_timeout_keeps_the_numbers`, `test_an_101_a_provider_error_keeps_the_numbers`; N-18 |
| AN-102 | Progress means one thing everywhere | `domain` `test_an_102_progress_is_distinct_aired_watched_in_the_pass`, `test_an_102_every_write_keeps_the_row_equal_to_the_count`, `test_an_102_repeat_and_unaired_episodes_dont_count`, `test_an_102_it_reads_a_count_of_episodes`; `stats` `test_tv_703_a_mark_records_the_new_count_without_moving_it`, `test_tv_703_a_tick_moves_it_by_one`, `test_tv_703_writes_record_no_social_activity_rows` |
| AN-103 | Refusals | `domain` `test_an_103_a_tick_past_a_known_total_names_the_total`, `test_an_103_a_tick_more_than_a_day_before_its_air_time_is_refused`, `test_an_103_a_tick_within_a_day_of_its_air_time_is_accepted`, `test_an_103_an_undated_future_episode_is_refused`, `test_an_103_with_no_total_and_no_schedule_any_number_is_accepted`, `test_an_103_a_log_or_mark_with_nothing_aired_is_refused`, `test_an_103_a_viewing_past_a_fallen_total_stays_and_counts_for_nothing`; `contract` `test_routes_a_refused_tick_is_a_400_naming_the_total` |
| AN-200 | Finished | `domain` `test_an_200_finished_needs_every_episode_watched_and_the_last_aired`, `test_an_200_a_gap_keeps_it_unfinished`, `test_an_200_a_finished_title_stays_paused_if_chosen_since`; N-06, N-07 |
| AN-201 | Caught up | `domain` `test_an_201_caught_up_stays_watching_and_leaves_in_progress`, `test_an_201_a_long_runner_is_never_finished_by_ticking`, `test_an_201_it_stays_caught_up_however_far_it_goes`; N-15, N-17 |
| AN-202 | A new episode reopens | `domain` `test_an_202_a_new_episode_reopens_a_caught_up_title`, `test_an_202_the_hourly_job_reopens_from_the_stored_copy`, `test_an_202_a_raised_total_reopens_a_finished_title`, `test_an_202_a_mark_covers_only_what_had_aired`, `test_an_202_planning_paused_and_dropped_dont_move`; `listing` `test_an_202_the_calendar_job_reopens_a_caught_up_title`, `test_an_202_a_failing_refresh_never_stops_the_events`, `test_an_202_refreshing_asks_anilist_once_for_many_titles`, `test_an_202_the_job_looks_again_at_a_stored_copy_once_a_week`; `airing` `test_anime_has_its_own_hourly_task`; N-14 |
| AN-203 | Ticking the last episode opens the log screen once | `domain` `test_an_203_a_tick_that_finishes_names_the_log_screen_dated_that_day`, `test_an_203_it_doesnt_open_for_marks_logs_webhooks_or_installed_builds`, `test_an_203_it_doesnt_open_when_a_log_exists_in_this_pass`, `test_an_203_a_new_pass_can_open_it_again`, `test_an_203_during_a_rewatch_it_opens_with_rewatch_on`; N-06, N-17 |
| AN-300 | The eye marks the rest, and undoes only that | `domain` `test_an_300_the_eye_marks_the_rest_as_one_mark_with_no_log_and_no_feed_item`, `test_an_300_caught_up_when_more_is_coming`, `test_an_300_nothing_aired_says_when_it_premieres`, `test_an_300_during_a_rewatch_it_marks_the_rest_and_finishes_it`, `test_an_300_case_1_a_log_says_delete_your_log_first`, `test_an_300_case_2_mark_fills_go_and_ticks_stay`, `test_an_300_case_3_it_asks_and_removes_every_viewing`, `test_an_300_undo_works_the_state_out_again`, `test_an_300_the_eye_marks_its_first_row`; N-09, N-10 |
| AN-301 | Stars and the heart mean you finished this title | `domain` `test_an_301_stars_on_a_title_not_started_mark_it`, `test_an_301_a_heart_on_a_title_not_started_marks_it_and_the_eye_undoes_it`, `test_an_301_a_title_not_started_on_an_airing_title_is_caught_up`, `test_an_301_planning_with_nothing_watched_isnt_started`, `test_an_301_partway_refuses_and_changes_nothing`, `test_an_301_watching_chosen_with_nothing_watched_is_partway`, `test_an_301_finished_or_caught_up_only_saves`, `test_an_301_only_saving_never_ends_a_choice`, `test_an_301_during_a_rewatch_partway_opens_the_log_with_rewatch_on`, `test_an_301_clearing_is_only_that_at_any_time`, `test_an_301_nothing_aired_the_heart_saves_and_stars_are_unavailable`, `test_an_301_a_value_already_set_refuses_nothing`, `test_an_301_stars_mark_its_first_row`; `imports` `test_an_301_an_imported_rating_with_no_status_or_history_marks_it`, `test_an_301_an_imported_rating_on_one_with_history_only_sets_the_value`; `contract` `test_routes_a_like_answers_state_and_marks`; N-11, N-12, N-13, N-14 |
| AN-400 | A log means finished | `domain` `test_an_400_a_log_is_one_entry_fills_and_a_stamp`, `test_an_400_a_log_ends_a_choice`, `test_an_400_never_in_the_future_on_the_users_calendar`, `test_an_400_the_toggle_outside_a_rewatch_is_only_the_badge`, `test_an_400_stars_and_the_heart_prefill_from_the_titles_own`, `test_an_400_while_a_log_exists_nothing_unwatches_what_it_covered`, `test_an_400_a_failure_part_way_leaves_nothing`, `test_an_400_a_log_works`; `web` `test_an_400_the_model_compatibility_routes_use_the_rules`; N-08, N-15, N-16, N-28 |
| AN-500 | Spine offers the next title and adds nothing by itself | `domain` `test_an_500_only_once_finished`, `test_an_500_until_the_next_one_is_finished`, `test_an_500_nothing_is_added_until_the_user_acts`, `test_an_500_a_title_with_no_series_has_no_next`, `test_an_500_the_last_in_its_series_has_no_next`, `test_an_500_a_slow_mal_never_breaks_a_page`; N-20 |
| AN-501 | Home's sheet moves on | Server part only: `domain` `test_an_501_has_aired_reads_the_next_titles_own_copy_first`, `test_an_501_the_finish_response_names_the_next_title`; N-19. The sheet itself is the app half's. |
| AN-600 | Rewatch starts at episode 1 | `domain` `test_an_600_rewatch_starts_at_e1_and_records_no_viewing`, `test_an_600_only_a_finished_title_can_be_rewatched`, `test_an_600_one_at_a_time_and_stop_needs_one`, `test_an_600_reaching_the_end_finishes_and_every_viewing_counts_again`, `test_an_600_ticks_in_a_rewatch_dont_see_the_earlier_pass`, `test_an_600_untick_only_touches_the_rewatch`, `test_an_600_pause_and_drop_keep_it`, `test_an_600_a_log_in_a_rewatch_fills_its_rest_and_finishes_it`, `test_an_600_a_log_from_the_first_pass_doesnt_block_the_rewatch`, `test_an_600_starting_a_rewatch_records_a_start_and_no_movement`; `contract` `test_routes_rewatch_and_stop_answer`; N-21 |
| AN-700 | Each Library card says where you are | Stored keys only: `domain` `test_an_700_stored_values_give_every_card_line`, `test_an_700_the_gap_is_named_on_the_list_too`, `test_an_700_planning_shows_the_premiere_while_nothing_has_aired`, `test_an_700_a_first_episodes_airing_stands_in_for_the_premiere`, `test_an_700_once_aired_there_is_no_premiere`. The words are the app half's. |
| AN-701 | Home's Update progress opens the episode sheet | Server part only: `contract` `test_an_701_the_rows_route_never_waits_on_anilist`, `test_an_701_in_progress_lists_watching_with_an_aired_up_next`, `test_an_701_most_recently_watched_first`. The sheet is the app half's. |
| AN-702 | The page | Server part only: `contract` `test_an_702_the_detail_payload_has_state_and_rows`, `test_an_702_the_detail_payload_for_a_title_not_tracked_still_has_them`, `test_an_702_signed_out_has_no_state_and_no_rows`, `test_non_anime_payloads_have_no_anime_keys`. |
| AN-703 | Activity | `stats` `test_an_703_a_days_ticks_are_one_item`, `test_an_703_unticks_count_against_that_days_ticks`, `test_an_703_marks_and_fills_are_never_activity`, `test_an_703_starting_is_activity`, `test_an_703_a_tick_that_puts_it_in_watching_is_progress_over_a_start`, `test_an_703_resuming_isnt_starting`, `test_an_703_starting_a_rewatch_is_activity`, `test_an_703_a_log_is_one_item_on_its_calendar_day`, `test_an_703_imports_and_webhook_marks_start_nothing`, `test_an_703_leaving_the_library_takes_starts_and_progress_with_it`, `test_an_703_a_friends_progress_label_reads_the_count`, `test_ac_a_rating_with_no_log_isnt_activity`; N-01, N-11. See Limits for its last sentence about a log. |
| AN-704 | Stats read viewings | `stats` `test_an_704_a_tick_counts_on_its_day_and_unticking_takes_it_off`, `test_an_704_a_tick_lands_on_the_owners_calendar_day`, `test_an_704_a_logs_fills_count_on_the_logs_date_and_go_with_it`, `test_an_704_a_marks_fills_count_only_in_all_time`, `test_an_704_a_yes_to_the_earlier_question_is_a_mark`, `test_an_704_a_mark_never_makes_a_day_active_or_a_multi_title_day`, `test_an_704_first_year_ignores_marks`, `test_an_704_a_rewatch_counts_again`, `test_an_704_a_stored_number_with_no_viewings_counts_only_in_all_time`, `test_an_704_each_viewing_is_the_titles_runtime_and_never_approximate`, `test_an_704_the_header_says_episodes_watched_exactly`, `test_an_704_a_tick_makes_the_title_count_in_its_year`, `test_an_704_the_global_tab_counts_anime_viewings_too`, `test_stat_105_a_marked_finished_title_is_an_undated_completion_in_all_time`, `test_stat_003_a_private_logs_fills_are_hidden_from_other_viewers`, `test_stat_108_a_log_after_a_mark_saved_as_a_rewatch_is_a_rewatch`, `test_stat_100_an_old_log_at_a_real_moment_keeps_its_day`, `test_stat_100_a_new_log_is_its_calendar_day_for_any_viewer`; `stats-v2` `test_anime_time_reads_viewings_and_a_mark_lands_in_no_year`, `test_anime_episodes_read_viewings_and_a_mark_lands_in_no_year`, `test_each_days_progress_and_starts_are_events`, `MultiTitleDayFilterTests`; N-11, N-21, N-25 |
| AN-800 | Imports end in the state the app would produce | `imports` `test_an_800_a_count_becomes_undated_fills_as_one_mark`, `test_an_800_completed_is_an_undated_mark_of_everything_aired`, `test_an_800_each_rewatch_is_a_finished_earlier_pass_and_one_row`, `test_an_800_a_rewatch_under_way_is_a_rewatch`, `test_an_800_planning_paused_and_dropped_stay_the_users_choice`, `test_an_800_in_progress_with_nothing_watched_means_watching`, `test_an_800_an_import_records_no_progress_change`, `test_an_800_one_history_row_per_title_at_the_sources_date`, `test_an_800_the_sources_dates_stay_until_a_dated_fact_moves_them`, `test_an_800_a_source_date_on_a_planned_title_is_no_start`, `test_an_800_no_provider_call_per_episode_and_no_anilist_at_all`, `test_an_800_the_question_is_used_up`, `test_an_800_a_later_tick_counts_only_what_is_new`, `test_an_800_overwrite_replaces_viewings_marks_and_choices_and_keeps_logs`, `test_an_800_new_mode_leaves_a_tracked_title_alone`, `test_an_800_a_later_job_corrects_the_provisional_numbers`, `test_an_800_a_completed_row_with_no_numbers_keeps_its_shelf`, `test_an_800_anilist_repeats_are_earlier_passes`, `test_an_800_anilist_current_means_watching`, `test_an_800_kitsu_reconsumes_are_earlier_passes`, `test_an_800_simkl_is_one_record_with_undated_fills`, `test_an_800_an_old_yamtrack_export_with_a_row_per_rewatch_is_one_record`, `test_an_800_the_mal_exports_log_dates_its_fills`, `test_an_800_the_mal_exports_rewatches_are_earlier_passes`, `test_an_800_the_mal_exports_log_on_a_rewatch_dates_the_pass_it_finished`, `test_an_800_the_mal_exports_overwrite_keeps_one_record_and_one_log`; `vendor` (imports); N-24, N-25 |
| AN-801 | A webhook play isn't a watch | `imports` `test_an_801_a_watch_ticks_at_the_event_time_with_no_question`, `test_an_801_a_repeated_watch_records_nothing`, `test_an_801_an_unplayed_event_unticks`, `test_an_801_a_play_counts_as_choosing_watching_where_available`, `test_an_801_a_play_never_reopens_a_finished_title`, `test_an_801_a_rejected_event_is_acknowledged`, `test_an_801_details_unavailable_is_acknowledged`, `test_an_801_the_processor_routes_through_the_module`, `test_an_801_an_unplayed_event_for_an_unknown_title_creates_nothing`; `vendor` (webhooks); N-26 |
| AN-802 | The export keeps today's columns | `imports` `test_an_802_one_row_per_title`, `test_an_802_the_new_fields_arent_columns`, `test_an_802_a_round_trip_keeps_counts_shelves_and_stars`, `test_an_802_a_rewatch_in_progress_isnt_exported`; N-30 |
| AN-900 | Installed builds keep working | `domain` `test_an_900_completed_means_mark_it_watched`, `test_an_900_in_progress_means_choose_watching`, `test_an_900_the_other_statuses_are_that_choice`, `test_an_900_raising_a_number_ticks_the_next_unwatched`, `test_an_900_lowering_removes_the_latest_unlogged_viewings`, `test_an_900_lowering_is_refused_when_a_log_covers_them`, `test_an_900_lowering_to_zero_leaves_the_library`, `test_an_900_a_number_past_the_total_is_refused_and_changes_nothing`, `test_an_900_status_and_number_together`, `test_an_900_an_unavailable_status_is_a_conflict_from_the_api_and_kept_from_the_web`, `test_an_900_notes_change_nothing_else`; `contract` `test_an_900_consume_pause_resume_and_drop_keep_a_top_level_tracking_state`, `test_an_900_the_keys_installed_builds_read_are_all_there`, `test_an_900_a_put_with_a_status_works`, `test_an_900_tracking_removal_goes_through_these_rules`, `test_an_900_a_log_ignores_auto_mark_consumed`, `test_an_900_a_retried_log_from_them_can_duplicate_as_today`, `test_an_900_stars_on_a_partway_title_show_the_servers_reason`, `test_an_900_their_progress_max_is_the_aired_count`; `web` `test_an_900_the_form_means_its_actions`, `test_an_900_a_number_in_the_form_means_ticks`, `test_an_900_the_progress_buttons_tick_and_untick`, `test_an_900_the_progress_button_past_the_total_says_why`, `test_an_900_lowering_is_refused_when_a_log_covers_it`, `test_an_900_saving_notes_changes_nothing_else`; N-27, N-28 |
| AN-901 | Expand, then contract | `schema` `test_n_29_old_inserts_save_by_the_database_defaults`, `test_an_901_old_inserts_into_the_history_table_read_back`, `test_an_901_the_migration_only_adds`; `contract` `test_n_29_the_old_codes_insert_shape` |
| AN-902 | There is no data to repair | `domain` `test_an_902_a_stored_number_becomes_an_undated_mark_at_the_first_write`, `test_an_902_a_stored_completed_marks_everything_aired`, `test_an_902_a_stored_choice_stays_the_choice`. The production count wasn't re-checked here; re-check it right before shipping, as the law says. |

## Acceptance scenarios

Each is one test in `contract` (`AcceptanceTests`), through the API and through the webhook or import it names.
N-29 is also in `schema`.

| Case | Test |
| --- | --- |
| N-01 | `test_n_01_tick_e1_of_an_untracked_title` |
| N-02 | `test_n_02_a_double_tap_is_one_viewing` |
| N-03 | `test_n_03_tick_e5_first_and_answer_yes` |
| N-04 | `test_n_04_tick_e5_first_and_answer_no` |
| N-05 | `test_n_05_tick_then_untick_the_only_episode_leaves_the_library` |
| N-06 | `test_n_06_ticking_the_last_episode_opens_the_log_screen_once` |
| N-07 | `test_n_07_skip_e4_and_tick_the_rest` |
| N-08 | `test_n_08_then_log` |
| N-09 | `test_n_09_eye_on_an_untracked_title_then_the_filled_eye` |
| N-10 | `test_n_10_tick_e1_to_e3_eye_then_the_filled_eye` |
| N-11 | `test_n_11_stars_on_an_untracked_title` |
| N-12 | `test_n_12_stars_at_e3_cancel_then_stars_and_save` |
| N-13 | `test_n_13_direct_stars_through_the_api_at_e3` |
| N-14 | `test_n_14_stars_on_a_caught_up_title_then_e8_airs_then_stars_again` |
| N-15 | `test_n_15_log_an_airing_title_with_7_of_12_aired` |
| N-16 | `test_n_16_delete_that_log` |
| N-17 | `test_n_17_tick_to_the_end_of_a_long_runner` |
| N-18 | `test_n_18_no_total_and_no_schedule` |
| N-19 | `test_n_19_finish_from_homes_sheet_with_a_next_title_aired` |
| N-20 | `test_n_20_a_finished_titles_page_offers_next` |
| N-21 | `test_n_21_rewatch_then_stop` |
| N-22 | `test_n_22_pause_at_e8_tick_e8_by_mistake_then_untick` |
| N-23 | `test_n_23_eye_on_a_movie_then_stars_then_log` |
| N-24 | `test_n_24_a_mal_row_with_2_rewatches_and_a_count_of_12` |
| N-25 | `test_n_25_import_with_a_count_of_5_in_progress` |
| N-26 | `test_n_26_webhook_watched_event_for_e4_of_an_untracked_title` |
| N-27 | `test_n_27_a_progress_write_of_5_then_3` |
| N-28 | `test_n_28_log_from_an_installed_builds_request_shape` |
| N-29 | `test_n_29_the_old_codes_insert_shape`; `schema` `test_n_29_old_inserts_save_by_the_database_defaults` |
| N-30 | `test_n_30_export_then_import_into_an_empty_account` |

## Tests changed on purpose

The contract replaced what these described:

- `test_stats_v2_activity`: anime time and episodes read viewings, so `test_anime_time_counts_rises_and_skips_the_creation_row` and `test_anime_episodes_are_approximate_in_a_range` became `test_anime_time_reads_viewings_and_a_mark_lands_in_no_year` and `test_anime_episodes_read_viewings_and_a_mark_lands_in_no_year`. The rises rule moved to manga, which keeps it: `test_manga_time_counts_rises_and_skips_the_creation_row`. `test_each_days_progress_and_starts_are_events` now uses manga for progress, and pins that anime's progress changes aren't events while its start is.
- `test_stats_v2_drills`: the seed's day of anime progress is a tick.
- `test_completion_progress`, `test_stats_v2_ratings`: anime was the last type with a row per watch-through, so "the latest row is canonical" became "the one row is".
- `test_stats_cache`: a like no longer copies onto anime logs (TV-500), so the test is that the module's like write bumps the owner.
- `test_api_v1`: a number on an untracked title is ticks, so it has a progress change (N-27); the feed reads progress changes, so anime writes no `progress_updated` Activity row, as TV doesn't.
- `test_activity_progress`, `test_star_rating_scale`, `test_api_v1`'s diary test: the same assertions, on `FakeAnimeProvider`.
- `test_mal`, `test_kitsu`, `test_mal_export`: one record per title (AN-800).
- `test_webhooks_plex`, `test_webhooks_jellyfin`, `test_webhooks_emby`: the provider mocks now cover AniList and the short timeout.

## Decisions made where the contract was silent

- Errors, entry points and `TVMutation` are TV's own, reused. `TVMutation.show_item` holds the anime `Item`.
- A pass is a stored `pass_number` on the row, viewings, marks and each log's stamp, not a comparison of times.
- A log's coverage is stored as ranges in `progress_snapshot["anime"]` (`{"v": 1, "pass": n, "covers": [[1, 24]]}`). A log with no stamp covers nothing and isn't converted.
- A single's first log takes a free viewing, a mark's fill or a tick. Later logs add their own fill. Deleting a log gives a taken fill back to its mark.
- A title with no numbers: E1 counts as aired until something is watched, so the eye, stars and a log have something to act on. `aired_count` is null, and it is caught up afterwards.
- A later episode's airing implies the earlier ones aired. Aired never passes the total.
- Stars on a value already set refuse nothing, as for TV.
- The web gets "Log this first." as a form error. A web form's other unavailable statuses are ignored, as TV's are.
- The web has no log route for anime, as for manga and comics; its edit and delete go through the module.
- The hourly job works out anime too (TV-206's "within about an hour"), as its own task, "Refresh aired anime". The calendar selector looks again at a stored copy older than 7 days, so a raised total is seen.
- Imports never call a provider. A title with no copy gets a provisional listing from the source's total, corrected by the daily job.
- An imported rating marks only with no status and no history.
- A source's rewatch under way is a rewatch, and a MAL export's finish date on it dates the pass it finished.
- An import keeps the source's start and finish on the row, and writes one history row at the source's date, as the other importers do. A row's `start_date` follows its first dated fact unless an import set it; `end_date` follows its last dated fact while Completed. Only the old web statistics page reads these dates; the app's Stats read viewings.
- The export writes a rewatch in progress as Completed with the count of all its history.
- `next_title` is stored from a page's metadata or the job, never fetched in a write.
- STAT-108 gained anime's mark-fill rule, the same as a TV season log's.
- TV's log-first refusal uses the default conflict code, `tv_action_unavailable`.
- A form's or API's written dates are ignored for anime: dates are worked out (TV-207).

## Final runs

FINAL_RUNS_PLACEHOLDER

## Limits

- The app half isn't built. AN-501, AN-700's words, AN-701's sheet and AN-702's page have only their server part here.
- AN-703 and TV-703 say a log replaces that day's start and progress. AC-2 says a log doesn't, and the shared feed shows both as separate items, for TV too (`test_a_season_log_that_day_keeps_the_days_progress`). Anime follows the feed. The law's wording wasn't changed; the owner should pick one.
- AniList was called for real only by hand, a few times, for Attack on Titan (MAL 16498) and One Piece (MAL 21), to check the query and the parsing. The tests use its shape from those answers.
- No installed (TestFlight) build ran against this backend. AN-900 rests on tests that send the installed builds' request shapes.
- AN-902's production count wasn't re-checked: this work doesn't touch production. Migration 0095 adds a unique constraint on one record per person and title with no data step, so it fails on a database holding duplicate anime rows. Re-check right before shipping.
- `services.set_media_like`'s generic path after the per-type branches is now unreachable for every media type. It wasn't deleted.

## App payload

Everything below is additive. All of it is on the server now.

### Reads

- `GET /api/v1/media/mal/anime/{id}/`: `user_state.anime` is the [state](#state), and the top-level `anime_rows` is the [rows payload](#rows). Signed out, neither is there.
- `GET /api/v1/tracking/mal/anime/{id}/rows/`: `{"anime": state, "rows": rows}`. Home's sheet uses it. It never waits on AniList.
- `GET /api/v1/tracking/?media_type=anime&continue_watching=true`: Home's and Profile's In progress: Watching, with an aired up next, most recently watched first. Each row's TrackingState has the list state under `anime`, with up next's title and image.
- Every TrackingState and every media summary's `user_state` has the list state under `anime`. It comes from stored values only.

### Writes

`POST /api/v1/tracking/{source}/anime/{id}/actions/{action}/` is TV's route with no season.
Every action takes an optional `mutation_id` (a UUID): a retry with the same one applies once and answers the same result.

| Screen | `action` | Body |
| --- | --- | --- |
| A row's empty tick | `tick` | `episode_number` |
| A row's filled tick | `untick` | `episode_number`, `viewing_id` (the row's `u`) |
| The empty eye (`eye.tap` is `mark`) | `mark_watched` | |
| The filled eye (`eye.tap` is `unmark_marks`, or `unmark_confirm` after asking `eye.prompt`) | `unmark` | `confirm: true` for `unmark_confirm`; `expected_version` is the state's `version` |
| Mark watched up to here | `mark_up_to` | `episode_number` |
| Yes to the earlier-episodes question | `mark_earlier` | `from`, `to`, `offer_log` copied from `earlier_unwatched`; `mutation_id` is required |
| + menu | `watching`, `resume`, `planning`, `pause`, `drop` | |
| + menu | `rewatch`, `stop_rewatch` | |
| + menu, Remove from Library | `remove` | `expected_version`; `mutation_id` is required |
| Stars | `rate` | `rating`: 0.5 to 5 with `X-Spine-Rating-Scale: stars`, or null to clear |

The answer is the envelope: `{"tracking": TrackingState or null, "anime": state, "row": the named episode's row or null, "rows": the whole rows payload when the write changed more than one row, else null, "changed": bool, "finished": null or an object, "earlier_unwatched": null or an object}`.
`pause`, `resume`, `drop` and `consume` also flatten the TrackingState at the top level, for installed builds.

The heart is `POST` and `DELETE /api/v1/me/liked-media/` with `{"ref": {"source", "media_type": "anime", "media_id"}}`. The answer is `{"liked", "media" (POST only), "anime", "rows"}`.

A log is `POST /api/v1/diary/` with `ref`, `consumed_at` as `YYYY-MM-DD`, `rating`, `liked`, `is_rewatch`, `review`, `review_title`, `contains_spoilers`, `visibility`, `tags` and a `mutation_id`. The new build must send a date and a mutation ID: a datetime with no mutation ID is an installed build, which gets no log screen. The diary payload adds `anime`, `rows`, `finished`, `earlier_unwatched`.
`PATCH /api/v1/diary/{id}/` adds `anime`, `rows`. `GET /api/v1/diary/{id}/` adds `anime_log`: `{"unmarks_count", "covers_count", "state_version"}`, which says whether to ask "Delete this log? 7 episodes it marked watched will be unmarked."
`DELETE /api/v1/diary/{id}/?expected_version=&mutation_id=` answers 204.

`PATCH`, `PUT` and `DELETE /api/v1/tracking/{source}/anime/{id}/` go through the same rules for installed builds. The new build doesn't need them.

### Errors

| Status | `error.code` | When |
| --- | --- | --- |
| 409 | `tv_action_unavailable` | The action isn't available; the message is the reason, such as "Log this first." or "You've started this anime. Pause or drop it instead." |
| 409 | `tv_log_blocks` | A log covers what the write would undo: "Delete your log first." |
| 409 | `tv_state_changed` | `expected_version` was stale; `error.tv` is `{"state_version", "count"}`, so the app can ask again with the new count. |
| 400 | `validation_error` | A bad field, or a refused write such as "This anime has 25 episodes." |
| 503 | `provider_unavailable` | `error.reason` is `tv_details_unavailable`: "Anime details are unavailable right now". Retryable; nothing changed. |

### State

```json
{
  "version": 1792954800000,
  "in_library": true,
  "status": "In progress",
  "choice": null,
  "started": true,
  "finished": false,
  "caught_up": false,
  "single": false,
  "total": 12,
  "watched_count": 1,
  "aired_count": 7,
  "first_gap_episode": null,
  "up_next": {"episode_number": 6, "title": null, "image_url": null, "air_date": null, "is_new": false},
  "next_airing": {"episode_number": 8, "air_date": "2026-10-29"},
  "premiere": null,
  "rewatch": null,
  "last_watched_on": "2026-10-25",
  "selected_action": "watching",
  "remove_count": 1,
  "rating_source_diary_entry_id": null,
  "like_source_diary_entry_id": null,
  "like_is_independent": false,
  "eye": {"filled": false, "tap": "mark", "count": 6, "prompt": null, "message": null},
  "available_actions": ["pause", "drop", "log", "remove", "rate"],
  "action_reasons": {"planning": "You've started this anime. Pause or drop it instead."},
  "opinion_action": "log",
  "composer": {
    "default_target": "title",
    "targets": [
      {
        "id": "title",
        "level": "title",
        "title": "Attack on Titan",
        "date": "2026-10-25",
        "rating": null,
        "liked": false,
        "is_rewatch": false,
        "available": true,
        "reason": null
      }
    ]
  },
  "next_title": null
}
```

- The list state is the keys from `version` to `last_watched_on` without `single` and `total`, read from stored values.
- `aired_count` is null when Spine doesn't know how many have aired: read "7 episodes".
- `first_gap_episode` names AN-200's "11 of 12 · E4 not watched".
- `opinion_action` says what stars and the heart do, with no second request: `mark` marks at once, `log` opens the log screen holding the value (a direct set is a 409, "Log this first."), `save` only saves. Clearing is always a direct write. **TV has the same key** on its show detail state (`user_state.tv`) and season state (`user_state.tv.season`, and the envelope's `season`), with the same three values.
- `eye.tap` is `mark`, `nothing_aired` (show `message`), `unmark_marks`, `unmark_confirm` (ask `prompt`, count `count`) or `blocked` (show `message`).
- `remove_count` fills "Remove this anime? Your 13 watched episodes will be unmarked." `action_reasons` says why an action on the + menu isn't available.
- `rewatch` is `{"started_at", "pass_number"}` during a rewatch. `composer.targets[0].is_rewatch` is the log screen's default.
- `next_title` is `{"source", "media_type": "anime", "media_id", "title", "image_url", "has_aired"}` once finished, until that one is finished too (AN-500). With `has_aired`, Home's sheet moves on to it (AN-501).
- A single has `single: true`, `up_next: null` and an empty rows list, and its `available_actions` hold no `watching`, `pause`, `drop` or `rewatch` (AN-002).

### Rows

```json
{
  "count": 12,
  "up_next": 7,
  "first_unwatched": 1,
  "log_message": "Delete your log first.",
  "episodes": [
    {"n": 1, "t": "To You", "i": "https://img.example/1.jpg", "a": 1},
    {"n": 2, "a": 1},
    {"n": 5, "a": 1, "w": 1, "u": 4412},
    {"n": 6, "a": 1, "w": 1, "l": 1},
    {"n": 8, "d": "2026-10-29"},
    {"n": 9}
  ]
}
```

Keys are short because One Piece has over 1,100 rows, and a key is left out when it has nothing to say.

| Key | Means |
| --- | --- |
| `n` | The episode's number. |
| `t`, `i` | Its title and image, when AniList lists them. Without a title it reads "Episode 5". |
| `a` | 1 once it has aired: show a tick. |
| `d` | An unaired row's air date on the user's calendar. With neither `a` nor `d` it reads "TBA". |
| `w` | 1 when it is watched in the current pass. |
| `u` | The viewing an untick removes: send it as `viewing_id`. |
| `l` | 1 when a log covers it: an untick says `log_message` and sends nothing. |

`up_next` is where the list opens. `first_unwatched` starts the "Mark E1–E4 watched?" prompt.

### A write that asks the earlier-episodes question

The tick has been saved. Show `question`; Yes sends `mark_earlier` with `from`, `to` and `offer_log`, and No sends nothing.

```json
{
  "tracking": {"status": "In progress"},
  "anime": {"status": "In progress", "watched_count": 1},
  "row": {"n": 5, "a": 1, "w": 1, "u": 4412},
  "rows": null,
  "changed": true,
  "finished": null,
  "earlier_unwatched": {
    "from": {"episode_number": 1},
    "to": {"episode_number": 4},
    "count": 4,
    "question": "Also mark E1–E4 watched?",
    "offer_log": true
  }
}
```

### A write that says the title just finished

Open the log screen from `finished.composer`, dated with its date. Not now sends nothing. `anime.next_title` is what Home's sheet moves on to.

```json
{
  "tracking": {"status": "Completed"},
  "anime": {
    "status": "Completed",
    "finished": true,
    "next_title": {
      "source": "mal",
      "media_type": "anime",
      "media_id": "35760",
      "title": "Attack on Titan Season 3",
      "image_url": "https://img.example/s3.jpg",
      "has_aired": true
    }
  },
  "row": {"n": 2, "a": 1, "w": 1, "u": 4413},
  "rows": null,
  "changed": true,
  "finished": {
    "level": "title",
    "composer": {
      "default_target": "title",
      "targets": [
        {
          "id": "title",
          "level": "title",
          "title": "Attack on Titan Season 2",
          "date": "2026-10-25",
          "rating": null,
          "liked": false,
          "is_rewatch": false,
          "available": true,
          "reason": null
        }
      ]
    }
  },
  "earlier_unwatched": null
}
```

The `tracking` and `anime` objects above are cut down to the keys that matter here; the real ones are whole.
