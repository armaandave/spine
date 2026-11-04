# Anime tracking coverage

Status: every law has automated backend tests. This file covers the server; the laws about screens rest here on the payload keys that drive them, and the app half has its own tests (`ios/Spine/SpineTests/AnimeTrackingTests.swift`).

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
| `concurrency` | `src/app/tests/test_anime_concurrency.py` | Simultaneous writes on real threads, with real row locks. Postgres only: CI's Database checks, or a throwaway local cluster. |
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
| Pause and Drop | TV-007 | `domain` `test_tv_007_pause_and_drop_keep_the_place`, `test_tv_007_pause_and_drop_invent_nothing_with_nothing_watched`, `test_tv_007_a_dropped_finished_title_returns_to_completed`; `web` `test_tv_007_pause_resume_and_drop_go_through_the_module`; `web` `test_tv_007_pause_resume_and_drop_work_from_the_copy_with_mal_down`, `test_tv_007_a_title_spine_doesnt_have_with_mal_down_says_to_try_again`, `test_tv_007_a_title_with_no_copy_and_mal_down_is_refused_and_nothing_is_saved`; `web` `test_tv_007_with_an_item_and_a_copy_they_never_ask_mal`, `test_tv_007_a_title_with_an_item_and_no_copy_asks_once_with_a_short_timeout`, `test_tv_007_a_title_with_no_item_asks_mal_with_a_short_timeout` |
| Ticks | TV-100, TV-101, TV-102 | `domain` `test_tv_100_a_tick_is_one_viewing_dated_now_on_the_users_calendar`, `test_tv_101_a_retry_adds_nothing`, `test_tv_102_untick_removes_the_latest_unlogged_viewing`, `test_tv_102_an_untick_naming_a_gone_viewing_changes_nothing`, `test_tv_102_a_logged_episode_says_delete_your_log_first`; N-02, N-05; `listing` `test_tv_102_an_untick` |
| Aired, up next, refusals | TV-103, TV-105, TV-106 | AN-100, AN-102, AN-103 below; `domain` `test_tv_105_up_next_follows_the_latest_tick`, `test_tv_105_after_the_last_it_wraps_to_the_first_unwatched`, `test_tv_105_up_next_carries_the_rows_title_and_image` |
| The earlier-episodes question | TV-107 | `domain` `test_tv_107_a_first_tick_of_e1_asks_nothing`, `test_tv_107_the_question_is_used_up_answered_or_not`, `test_tv_107_a_choice_keeps_the_used_up_question_through_an_undo`, `test_tv_107_webhooks_and_installed_builds_dont_show_it`, `test_tv_107_yes_needs_a_mutation_id_and_applies_once`, `test_tv_107_yes_after_ticking_the_last_first_opens_the_log_screen`; N-03, N-04 |
| Mark watched up to here | TV-108 | `domain` `test_tv_108_mark_up_to_marks_that_episode_and_every_unwatched_before_it`, `test_tv_108_not_on_an_unaired_episode`, `test_tv_108_rows_give_the_first_unwatched_for_the_prompt`, `test_tv_108_up_to_any_number` |
| Finishing and reopening | TV-200, TV-206 | AN-200, AN-202 below |
| Choices and undoing | TV-203, TV-208 | `domain` `test_tv_203_only_the_user_ends_a_choice`, `test_tv_208_undoing_the_write_that_ended_a_choice_restores_it`, `test_tv_208_watching_chosen_first_keeps_the_title_in_watching`, `test_tv_208_leaving_the_library_clears_stars_and_the_heart`; N-22 |
| One worked-out state | TV-207 | `domain` `test_tv_207_working_out_twice_changes_nothing`, `test_tv_207_lists_read_stored_values_with_no_provider_call` |
| The eye | TV-300, TV-301, TV-302 | AN-300 below; N-09, N-10 |
| Stars and the heart | TV-303, TV-304, TV-500, TV-501, TV-503, TV-504 | AN-301 below; `domain` `test_tv_501_source_rules`, `test_tv_501_editing_a_source_log_updates_the_current_value`, `test_tv_501_a_log_given_new_stars_fills_empty_ones`, `test_tv_503_the_community_rating_counts_each_person_once`, `test_tv_504_off_scale_ratings_are_refused`, `test_tv_800_an_import_on_a_partway_title_only_sets_the_value`; `src/api/tests/test_star_rating_scale.py` `test_an_000_a_new_write_off_the_half_star_grid_is_refused`; `web` `test_tv_304_a_form_setting_stars_partway_says_log_this_first`; `contract` `test_routes_star_scale_requests_rate_in_stars` |
| Logs | TV-400, TV-401, TV-403 to TV-407 | AN-203, AN-400 below; `domain` `test_tv_400_another_log_keeps_what_it_covered`, `test_tv_404_the_rewatch_default`, `test_tv_405_editing_a_logs_date_moves_no_viewing`, `test_tv_406_deleting_removes_only_its_fills`, `test_tv_406_a_stale_delete_says_the_new_count`, `test_tv_406_deleting_the_only_log_leaves_the_library`, `test_tv_407_a_datetime_converts_to_the_users_calendar`, `test_tv_407_a_future_edit_is_refused`, `test_stat_100_an_old_log_at_a_real_moment_keeps_its_zone`; `web` `test_tv_405_the_webs_log_edit_goes_through_the_module`, `test_tv_406_the_webs_log_delete_goes_through_the_module`, `test_tv_407_a_future_web_edit_is_refused`; `contract` `test_routes_log_edit_and_delete`, `test_routes_a_future_log_is_a_400`; `listing` `test_tv_405_a_log_edit` |
| Rewatch | TV-600, TV-601, TV-602 | AN-600 below; N-21 |
| Calendar and notifications | TV-704 | `domain` `test_tv_704_planning_watching_and_completed_get_events_and_paused_and_dropped_dont` |
| Agreement and retries | TV-705 | `domain` `test_tv_705_a_logs_mutation_id_applies_it_once`, `test_tv_705_a_mutation_id_for_another_write_is_refused`, `test_tv_705_a_write_with_no_row_leaves_nothing_when_refused`, `test_tv_705_surfaces_agree_after_a_write`; `domain` `test_tv_705_a_first_tick_that_loses_the_race_runs_again`, `test_tv_705_a_first_log_that_loses_the_race_sees_the_tick`, `test_tv_705_a_write_locks_only_its_own_row`; `concurrency` `test_tv_705_two_first_ticks_are_one_viewing`, `test_tv_705_a_tick_and_a_log_together_keep_one_viewing_of_the_episode`, `test_tv_705_a_writes_lock_names_only_its_own_row`, `test_tv_705_one_persons_write_doesnt_block_another_on_the_same_title` |

## The numbered laws

| Law | What it says | Tests |
| --- | --- | --- |
| AN-001 | One card per title | `domain` `test_an_001_a_title_tracked_as_tv_is_a_separate_thing`; `schema` `test_an_001_a_second_row_for_one_title_is_refused` |
| AN-002 | A single works like a movie | `domain` `test_an_002_total_of_one_or_a_movie_with_no_total_is_single`, `test_an_002_no_rows_no_up_next_and_no_question`, `test_an_002_the_eye_and_opinions_mark_it`, `test_an_002_it_has_no_partway`, `test_an_002_watching_again_is_another_log_and_counts_again`, `test_an_002_a_log_after_a_mark_dates_it_and_deleting_gives_the_mark_back`, `test_an_002_an_imported_single_stays_on_its_shelf_until_marked`, `test_an_002_it_never_appears_in_in_progress`, `test_an_002_there_is_no_rewatch_action`; N-23; `domain` `test_an_002_a_single_has_no_watching_pause_or_drop`, `test_an_002_a_watched_single_doesnt_offer_a_pause_or_a_drop_it_hasnt_got`, `test_an_002_a_written_status_on_a_single_is_refused_and_kept_from_the_web`, `test_an_002_a_webhook_play_of_a_single_does_nothing` |
| AN-100 | What has aired | `domain` `test_an_100_mal_finished_within_the_total`, `test_an_100_the_schedule_says_its_time_has_passed`, `test_an_100_anilist_names_a_later_episode`, `test_an_100_a_viewing_makes_it_aired`, `test_an_100_a_total_with_no_schedule_counts_every_listed_episode`, `test_an_100_correction_an_upcoming_title_has_nothing_aired`, `test_an_100_a_write_with_no_copy_is_refused_with_a_retryable_message`, `test_an_100_a_stored_copy_answers_when_providers_are_down`; `listing` `test_an_100_with_no_copy_and_mal_down_get_listing_says_unavailable`, `test_an_100_the_copy_keeps_total_next_episode_and_rows`, `test_an_100_a_partial_start_date_isnt_a_premiere`, `test_an_100_page_metadata_updates_the_numbers_with_no_call`, `test_an_100_a_usual_page_view_writes_nothing`, `test_an_100_mal_down_keeps_the_numbers_and_works_the_rest_out`, `test_a_manual_title_has_no_numbers_and_is_never_unavailable`; `contract` `test_routes_details_unavailable_is_a_retryable_503`; `domain` `test_an_100_a_written_completed_with_a_number_stays_completed`, `test_an_100_a_written_completed_with_no_number_uses_what_is_watched`, `test_an_100_a_higher_number_raises_the_total`, `test_an_100_a_lower_number_keeps_the_total_and_reopens_it`, `test_an_100_a_number_with_in_progress_sets_no_total`, `test_an_100_the_eye_stars_a_heart_and_a_log_finish_it`, `test_an_100_a_log_after_ticks_finishes_at_what_is_watched`, `test_an_100_a_number_past_the_ceiling_is_still_refused`, `test_an_100_the_owners_total_is_theirs_and_no_copy_is_stored`; `listing` `test_an_100_ninety_days_on_an_untracked_title_counts_what_has_aired`, `test_an_100_a_copy_read_today_makes_no_call`, `test_an_100_an_airing_copy_is_stale_after_a_day`, `test_an_100_an_airing_time_passing_makes_it_stale_at_once`, `test_an_100_a_finished_copy_waits_a_week`, `test_an_100_a_stale_read_keeps_to_the_short_timeout`, `test_an_100_a_stale_read_that_fails_keeps_the_copy_and_backs_off`, `test_an_100_a_read_of_only_one_provider_backs_off_only_when_it_fails`, `test_an_100_a_first_open_doesnt_count_every_listed_episode`, `test_an_100_a_tick_still_works_and_the_rows_run_to_one_past_it`, `test_an_100_the_rows_stop_at_the_total`, `test_an_100_a_tick_past_the_total_is_still_refused`, `test_an_100_whatever_fills_the_rest_is_refused_and_retryable`, `test_an_100_the_refusal_is_the_retryable_503`, `test_an_100_a_webs_written_completed_says_why`, `test_an_100_once_anilist_answers_the_eye_fills_only_what_aired`, `test_an_100_announced_then_airing_waits_for_anilist`, `test_an_100_a_status_change_on_the_page_makes_anilists_old_answer_count_for_nothing`, `test_an_100_a_finished_title_counts_every_listed_episode_with_anilist_down`, `test_an_100_a_job_with_every_provider_down_leaves_no_copy`, `test_an_100_a_write_on_an_empty_copy_is_refused_when_mal_is_down`, `test_an_100_an_empty_copy_backing_off_still_reads_mal_for_a_write`, `test_an_100_anilists_answer_alone_is_no_copy`, `test_an_100_a_job_works_nobody_out_from_an_empty_copy`; `imports` `test_an_100_an_imported_manual_title_stays_completed_at_the_next_save`; `web` `test_an_100_a_manual_title_saved_completed_with_a_number_stays_completed`; `domain` `test_an_100_a_listed_episode_counts_as_evidence_it_aired`, `test_an_100_a_log_covers_what_is_listed`, `test_an_100_an_upcoming_titles_list_is_no_evidence`, `test_an_100_a_total_a_log_worked_out_goes_with_the_log`, `test_an_100_the_fills_of_a_log_go_with_the_total_it_worked_out`, `test_an_100_a_total_the_eye_worked_out_goes_when_its_mark_is_undone`, `test_an_100_another_log_or_mark_keeps_a_worked_out_total`, `test_an_100_a_number_the_owner_wrote_stays_when_a_mark_is_undone`; `listing` `test_an_100_a_title_on_a_break_asks_for_its_last_aired_entry`, `test_an_100_a_hiatus_is_a_break_too`, `test_an_100_one_more_request_for_every_title_on_a_break_in_a_chunk`, `test_an_100_a_title_with_a_next_episode_or_not_under_way_costs_no_second_request`, `test_an_100_a_failed_second_request_fails_the_read_so_the_copy_stays`, `test_an_100_a_second_answer_with_no_data_is_a_failed_read`, `test_an_100_a_title_with_a_total_counts_what_aired_before_the_break`, `test_an_100_a_long_runner_counts_every_episode_up_to_it`, `test_an_100_a_long_break_still_counts_within_half_a_year`, `test_an_100_past_half_a_year_anilist_has_stopped_following_a_title_with_a_total`, `test_an_100_past_half_a_year_a_long_runners_entry_is_still_evidence`, `test_an_100_a_failed_read_during_a_break_keeps_the_count`, `test_an_100_a_finished_title_ignores_its_old_entry`, `test_an_100_a_stale_copy_isnt_read_again_for_them_either`, `test_an_100_what_needs_the_numbers_is_still_refused`; `contract` `test_routes_a_title_with_no_item_and_mal_timing_out_is_a_retryable_503`; `domain` `test_an_100_a_typed_total_stays_through_a_filled_eye_and_an_untick`, `test_an_100_a_typed_total_stays_through_a_deleted_log`, `test_an_100_an_imported_total_stays_through_an_untick`, `test_an_100_a_raised_total_is_written_and_a_lowered_number_is_not_a_total`, `test_an_100_a_worked_out_total_goes_when_nothing_is_left`, `test_an_100_a_worked_out_total_with_nothing_behind_it_goes_with_an_untick`, `test_an_100_a_stored_completed_on_a_manual_title_is_a_written_total`; `listing` `test_an_100_a_failed_last_aired_read_costs_only_the_titles_it_was_for`, `test_an_100_a_failed_last_aired_read_keeps_every_chunk_that_answered`, `test_an_100_a_chunk_that_fails_ends_the_asking_and_keeps_the_ones_before_it`, `test_an_100_a_title_on_a_break_loses_its_answer_when_a_later_page_fails` |
| AN-101 | What a row shows | `domain` `test_an_101_rows_are_aired_then_the_unaired_spine_knows_of`, `test_an_101_rows_never_wait_a_failed_fetch_leaves_the_number`, `test_an_101_a_row_names_the_viewing_an_untick_removes`, `test_an_101_one_piece_stays_cheap_in_queries_and_bytes`, `test_an_101_a_mark_of_a_long_runner_is_one_request_in_bulk`, `test_an_101_the_list_runs_from_e1_to_one_past_the_last_watched`, `test_an_101_a_write_that_changes_the_list_sends_it`, `test_an_101_listed_rows_with_no_schedule_can_all_be_ticked`; `listing` `test_an_101_the_episode_number_is_parsed_out_of_the_title`, `test_an_101_order_isnt_guaranteed_and_a_second_site_doesnt_replace_the_first`, `test_an_101_a_finished_title_has_no_schedule`, `test_an_101_one_request_per_25_titles`, `test_an_101_a_first_open_tries_once_with_a_short_timeout_then_reads_the_copy`, `test_an_101_a_failure_stores_the_numbers_and_backs_off`, `test_an_101_a_page_answers_when_every_provider_is_down`, `test_an_101_a_timeout_keeps_the_numbers`, `test_an_101_a_provider_error_keeps_the_numbers`; N-18; `domain` `test_an_101_a_webhook_watch_of_e5_leaves_e1_to_e4_tickable`; `listing` `test_an_101_a_list_running_past_the_total_isnt_this_titles`; `domain` `test_an_101_with_a_list_it_runs_to_the_highest_listed_with_no_gaps`, `test_an_101_one_pieces_list_with_no_next_episode_has_no_gaps`, `test_an_101_the_list_runs_on_to_one_past_the_last_watched_when_that_is_later`, `test_an_101_any_number_can_still_be_ticked_past_the_list`; `listing` `test_an_101_a_full_page_stops_once_every_title_has_answered`, `test_an_101_a_title_still_to_answer_is_looked_for_on_the_next_page`, `test_an_101_a_later_season_with_its_prequels_list_has_no_titles_or_images`, `test_an_101_a_later_season_with_its_own_list_keeps_it`, `test_an_101_a_first_seasons_list_stays_though_a_later_ova_is_filed_as_its_prequel`, `test_an_101_a_special_out_before_it_counts_as_much_as_a_season`, `test_an_101_one_list_holding_the_other_is_the_same_list`, `test_an_101_one_title_in_common_isnt_the_same_list`, `test_an_101_only_a_prequel_is_compared`, `test_an_101_a_prequel_with_no_list_or_no_date_changes_nothing`, `test_an_101_a_season_not_out_yet_with_its_prequels_list_has_no_titles_either`, `test_an_101_the_schedule_stays_when_the_list_goes`, `test_an_101_the_request_asks_for_what_the_comparison_needs`, `test_an_101_a_long_runner_on_a_break_can_still_tick_what_anilist_hasnt_listed`, `test_an_101_a_list_past_the_next_episode_leaves_no_gaps`, `test_an_101_a_viewing_past_the_next_episode_leaves_no_gaps_and_stays_in_order` |
| AN-102 | Progress means one thing everywhere | `domain` `test_an_102_progress_is_distinct_aired_watched_in_the_pass`, `test_an_102_every_write_keeps_the_row_equal_to_the_count`, `test_an_102_repeat_and_unaired_episodes_dont_count`, `test_an_102_it_reads_a_count_of_episodes`; `stats` `test_tv_703_a_mark_records_the_new_count_without_moving_it`, `test_tv_703_a_tick_moves_it_by_one`, `test_tv_703_writes_record_no_social_activity_rows` |
| AN-103 | Refusals | `domain` `test_an_103_a_tick_past_a_known_total_names_the_total`, `test_an_103_a_tick_more_than_a_day_before_its_air_time_is_refused`, `test_an_103_a_tick_within_a_day_of_its_air_time_is_accepted`, `test_an_103_an_undated_future_episode_is_refused`, `test_an_103_with_no_total_and_no_schedule_any_number_is_accepted`, `test_an_103_a_log_or_mark_with_nothing_aired_is_refused`, `test_an_103_a_viewing_past_a_fallen_total_stays_and_counts_for_nothing`; `contract` `test_routes_a_refused_tick_is_a_400_naming_the_total`; `domain` `test_an_103_the_ceiling_is_past_the_longest_anime`, `test_an_103_a_tick_past_the_ceiling_is_refused_with_no_numbers`, `test_an_103_mark_up_to_past_the_ceiling_is_refused_and_writes_nothing`, `test_an_103_a_number_past_the_total_is_refused_before_anything_is_built`, `test_an_103_a_number_past_the_ceiling_is_refused_before_anything_is_built`; `imports` `test_an_103_a_sources_number_past_the_ceiling_is_dropped_not_filled`; `listing` `test_an_103_an_episode_after_the_break_isnt_ticked_with_a_total` |
| AN-200 | Finished | `domain` `test_an_200_finished_needs_every_episode_watched_and_the_last_aired`, `test_an_200_a_gap_keeps_it_unfinished`, `test_an_200_a_finished_title_stays_paused_if_chosen_since`; N-06, N-07 |
| AN-201 | Caught up | `domain` `test_an_201_caught_up_stays_watching_and_leaves_in_progress`, `test_an_201_a_long_runner_is_never_finished_by_ticking`, `test_an_201_it_stays_caught_up_however_far_it_goes`; N-15, N-17 |
| AN-202 | A new episode reopens | `domain` `test_an_202_a_new_episode_reopens_a_caught_up_title`, `test_an_202_the_hourly_job_reopens_from_the_stored_copy`, `test_an_202_a_raised_total_reopens_a_finished_title`, `test_an_202_a_mark_covers_only_what_had_aired`, `test_an_202_planning_paused_and_dropped_dont_move`; `listing` `test_an_202_the_calendar_job_reopens_a_caught_up_title`, `test_an_202_a_failing_refresh_never_stops_the_events`, `test_an_202_refreshing_asks_anilist_once_for_many_titles`, `test_an_202_the_job_looks_again_at_a_stored_copy_once_a_week`; `airing` `test_anime_has_its_own_hourly_task`; N-14; `imports` `test_an_202_a_page_can_reopen_a_caught_up_title_before_the_job`; `listing` `test_an_202_a_failing_schedule_read_still_reads_a_raised_total`, `test_an_202_a_failing_schedule_read_stops_nothing_else_in_the_job`, `test_an_202_one_titles_failed_read_keeps_the_others_events`, `test_an_202_the_break_ends_and_the_next_one_is_named`; `listing` `test_an_202_a_failed_read_of_some_titles_keeps_the_answers_of_the_rest`, `test_an_202_a_hanging_mal_costs_one_short_read_in_a_run`, `test_an_202_a_mal_timeout_in_one_run_doesnt_stop_the_next`, `test_an_202_a_refused_mal_keeps_being_asked`, `test_an_202_the_jobs_reads_are_short` |
| AN-203 | Ticking the last episode opens the log screen once | `domain` `test_an_203_a_tick_that_finishes_names_the_log_screen_dated_that_day`, `test_an_203_it_doesnt_open_for_marks_logs_webhooks_or_installed_builds`, `test_an_203_it_doesnt_open_when_a_log_exists_in_this_pass`, `test_an_203_a_new_pass_can_open_it_again`, `test_an_203_during_a_rewatch_it_opens_with_rewatch_on`; N-06, N-17 |
| AN-300 | The eye marks the rest, and undoes only that | `domain` `test_an_300_the_eye_marks_the_rest_as_one_mark_with_no_log_and_no_feed_item`, `test_an_300_caught_up_when_more_is_coming`, `test_an_300_nothing_aired_says_when_it_premieres`, `test_an_300_during_a_rewatch_it_marks_the_rest_and_finishes_it`, `test_an_300_case_1_a_log_says_delete_your_log_first`, `test_an_300_case_2_mark_fills_go_and_ticks_stay`, `test_an_300_case_3_it_asks_and_removes_every_viewing`, `test_an_300_undo_works_the_state_out_again`, `test_an_300_the_eye_marks_its_first_row`; N-09, N-10; `listing` `test_an_300_the_filled_eye` |
| AN-301 | Stars and the heart mean you finished this title | `domain` `test_an_301_stars_on_a_title_not_started_mark_it`, `test_an_301_a_heart_on_a_title_not_started_marks_it_and_the_eye_undoes_it`, `test_an_301_the_state_says_whether_the_heart_is_on`, `test_an_301_a_title_not_started_on_an_airing_title_is_caught_up`, `test_an_301_planning_with_nothing_watched_isnt_started`, `test_an_301_partway_refuses_and_changes_nothing`, `test_an_301_watching_chosen_with_nothing_watched_is_partway`, `test_an_301_finished_or_caught_up_only_saves`, `test_an_301_only_saving_never_ends_a_choice`, `test_an_301_during_a_rewatch_partway_opens_the_log_with_rewatch_on`, `test_an_301_clearing_is_only_that_at_any_time`, `test_an_301_nothing_aired_the_heart_saves_and_stars_are_unavailable`, `test_an_301_a_value_already_set_refuses_nothing`, `test_an_301_stars_mark_its_first_row`; `imports` `test_an_301_an_imported_rating_with_no_status_or_history_marks_it`, `test_an_301_an_imported_rating_on_one_with_history_only_sets_the_value`; `contract` `test_routes_a_like_answers_state_and_marks`; N-11, N-12, N-13, N-14; `listing` `test_an_301_clearing_stars`, `test_an_301_turning_the_heart_off`, `test_an_301_through_the_api`; `contract` `test_routes_a_heart_on_a_title_with_no_item_and_mal_timing_out_is_a_retryable_503`, `test_routes_a_heart_on_a_title_with_no_item_still_creates_it` |
| AN-400 | A log means finished | `domain` `test_an_400_a_log_is_one_entry_fills_and_a_stamp`, `test_an_400_a_log_ends_a_choice`, `test_an_400_never_in_the_future_on_the_users_calendar`, `test_an_400_the_toggle_outside_a_rewatch_is_only_the_badge`, `test_an_400_stars_and_the_heart_prefill_from_the_titles_own`, `test_an_400_while_a_log_exists_nothing_unwatches_what_it_covered`, `test_an_400_a_failure_part_way_leaves_nothing`, `test_an_400_a_log_works`; `web` `test_an_400_the_model_compatibility_routes_use_the_rules`; N-08, N-15, N-16, N-28; `listing` `test_an_400_a_log_during_a_break_covers_what_aired`; `web`, the history page, which lists these rows: `test_an_400_history_opens_after_a_log_is_deleted`, `test_an_400_each_history_row_of_a_write_has_its_own_moment_in_order`, `test_an_400_the_creation_row_is_dated_first`, `test_an_400_rows_at_one_moment_dont_stop_the_page_and_each_shows`, `test_an_400_internal_fields_arent_shown` |
| AN-500 | Spine offers the next title and adds nothing by itself | `domain` `test_an_500_only_once_finished`, `test_an_500_until_the_next_one_is_finished`, `test_an_500_nothing_is_added_until_the_user_acts`, `test_an_500_a_title_with_no_series_has_no_next`, `test_an_500_the_last_in_its_series_has_no_next`, `test_an_500_a_slow_mal_never_breaks_a_page`; N-20; `listing` `test_an_500_an_airing_next_title_with_no_answer_goes_by_its_release_date`; `contract` `test_n_20_opening_the_page_stores_the_next_title_with_no_job`, `test_n_20_a_page_with_cached_details_stores_the_next_title_too`, `test_n_20_a_usual_page_view_doesnt_write_the_next_title_again` |
| AN-501 | Home's sheet moves on | Server part only: `domain` `test_an_501_has_aired_reads_the_next_titles_own_copy_first`, `test_an_501_the_finish_response_names_the_next_title`, `test_an_501_a_next_title_that_is_a_single_says_so`, `test_an_501_a_next_title_with_rows_isnt_a_single`; `listing` `test_an_501_a_series_item_says_whether_it_is_a_single`; N-19. The sheet itself is the app half's. |
| AN-600 | Rewatch starts at episode 1 | `domain` `test_an_600_rewatch_starts_at_e1_and_records_no_viewing`, `test_an_600_only_a_finished_title_can_be_rewatched`, `test_an_600_one_at_a_time_and_stop_needs_one`, `test_an_600_reaching_the_end_finishes_and_every_viewing_counts_again`, `test_an_600_ticks_in_a_rewatch_dont_see_the_earlier_pass`, `test_an_600_untick_only_touches_the_rewatch`, `test_an_600_pause_and_drop_keep_it`, `test_an_600_a_log_in_a_rewatch_fills_its_rest_and_finishes_it`, `test_an_600_a_log_from_the_first_pass_doesnt_block_the_rewatch`, `test_an_600_starting_a_rewatch_records_a_start_and_no_movement`; `contract` `test_routes_rewatch_and_stop_answer`; N-21; `listing` `test_an_600_stopping_a_rewatch` |
| AN-700 | Each Library card says where you are | Stored keys only: `domain` `test_an_700_stored_values_give_every_card_line`, `test_an_700_the_gap_is_named_on_the_list_too`, `test_an_700_planning_shows_the_premiere_while_nothing_has_aired`, `test_an_700_a_first_episodes_airing_stands_in_for_the_premiere`, `test_an_700_once_aired_there_is_no_premiere`. The words are the app half's. |
| AN-701 | Home's Update progress opens the episode sheet | Server part only: `contract` `test_an_701_the_rows_route_never_waits_on_anilist`, `test_an_701_in_progress_lists_watching_with_an_aired_up_next`, `test_an_701_most_recently_watched_first`. The sheet is the app half's. |
| AN-702 | The page | Server part only: `contract` `test_an_702_the_detail_payload_has_state_and_rows`, `test_an_702_the_detail_payload_for_a_title_not_tracked_still_has_them`, `test_an_702_signed_out_has_no_state_and_no_rows`, `test_non_anime_payloads_have_no_anime_keys`, and the routes: `test_routes_every_write_answers_the_envelope`, `test_routes_an_unknown_action_is_a_404`, `test_routes_tick_needs_its_number`, `test_routes_a_stale_version_says_the_new_count`.; `contract` `test_routes_an_absurd_episode_number_is_a_clean_400`, `test_routes_a_single_refuses_watching_pause_and_drop_with_a_reason` |
| AN-703 | Activity | `stats` `test_an_703_a_days_ticks_are_one_item`, `test_an_703_unticks_count_against_that_days_ticks`, `test_an_703_marks_and_fills_are_never_activity`, `test_an_703_starting_is_activity`, `test_an_703_a_tick_that_puts_it_in_watching_is_progress_over_a_start`, `test_an_703_resuming_isnt_starting`, `test_an_703_starting_a_rewatch_is_activity`, `test_an_703_a_log_is_one_item_on_its_calendar_day`, `test_an_703_imports_and_webhook_marks_start_nothing`, `test_an_703_leaving_the_library_takes_starts_and_progress_with_it`, `test_an_703_a_friends_progress_label_reads_the_count`, `test_ac_a_rating_with_no_log_isnt_activity`; N-01, N-11. See Limits for its last sentence about a log.; `contract` `test_ac_5_a_followers_only_log_says_nothing_to_a_stranger`, `test_ac_5_a_private_log_says_nothing_to_a_stranger_or_a_follower`, `test_ac_5_a_follower_sees_a_followers_only_log`, `test_ac_5_anyone_sees_a_public_log`, `test_ac_5_the_owner_still_sees_everything`, `test_ac_5_ticks_stay_visible_with_no_hidden_log` |
| AN-704 | Stats read viewings | `stats` `test_an_704_a_tick_counts_on_its_day_and_unticking_takes_it_off`, `test_an_704_a_tick_lands_on_the_owners_calendar_day`, `test_an_704_a_logs_fills_count_on_the_logs_date_and_go_with_it`, `test_an_704_a_marks_fills_count_only_in_all_time`, `test_an_704_a_yes_to_the_earlier_question_is_a_mark`, `test_an_704_a_mark_never_makes_a_day_active_or_a_multi_title_day`, `test_an_704_first_year_ignores_marks`, `test_an_704_a_rewatch_counts_again`, `test_an_704_a_stored_number_with_no_viewings_counts_only_in_all_time`, `test_an_704_each_viewing_is_the_titles_runtime_and_never_approximate`, `test_an_704_the_header_says_episodes_watched_exactly`, `test_an_704_a_tick_makes_the_title_count_in_its_year`, `test_an_704_the_global_tab_counts_anime_viewings_too`, `test_stat_105_a_marked_finished_title_is_an_undated_completion_in_all_time`, `test_stat_003_a_private_logs_fills_are_hidden_from_other_viewers`, `test_stat_108_a_log_after_a_mark_saved_as_a_rewatch_is_a_rewatch`, `test_stat_100_an_old_log_at_a_real_moment_keeps_its_day`, `test_stat_100_a_new_log_is_its_calendar_day_for_any_viewer`, `test_stat_215_most_watched_reads_viewings_as_a_shows_does`, `test_stat_215_a_mark_counts_toward_most_watched_only_in_all_time`; `stats-v2` `test_anime_time_reads_viewings_and_a_mark_lands_in_no_year`, `test_anime_episodes_read_viewings_and_a_mark_lands_in_no_year`, `test_each_days_progress_and_starts_are_events`, `MultiTitleDayFilterTests`; N-11, N-21, N-25; `stats` `test_stat_215_a_hidden_logs_fills_never_count_toward_most_watched` |
| AN-800 | Imports end in the state the app would produce | `imports` `test_an_800_a_count_becomes_undated_fills_as_one_mark`, `test_an_800_completed_is_an_undated_mark_of_everything_aired`, `test_an_800_each_rewatch_is_a_finished_earlier_pass_and_one_row`, `test_an_800_a_rewatch_under_way_is_a_rewatch`, `test_an_800_planning_paused_and_dropped_stay_the_users_choice`, `test_an_800_in_progress_with_nothing_watched_means_watching`, `test_an_800_an_import_records_no_progress_change`, `test_an_800_one_history_row_per_title_at_the_sources_date`, `test_an_800_the_sources_dates_stay_until_a_dated_fact_moves_them`, `test_an_800_a_source_date_on_a_planned_title_is_no_start`, `test_an_800_no_provider_call_per_episode_and_no_anilist_at_all`, `test_an_800_the_question_is_used_up`, `test_an_800_a_later_tick_counts_only_what_is_new`, `test_an_800_overwrite_replaces_viewings_marks_and_choices_and_keeps_logs`, `test_an_800_new_mode_leaves_a_tracked_title_alone`, `test_an_800_a_later_job_corrects_the_provisional_numbers`, `test_an_800_a_completed_row_with_no_numbers_keeps_its_shelf`, `test_an_800_anilist_repeats_are_earlier_passes`, `test_an_800_anilist_current_means_watching`, `test_an_800_kitsu_reconsumes_are_earlier_passes`, `test_an_800_simkl_is_one_record_with_undated_fills`, `test_an_800_an_old_yamtrack_export_with_a_row_per_rewatch_is_one_record`, `test_an_800_the_mal_exports_log_dates_its_fills`, `test_an_800_the_mal_exports_rewatches_are_earlier_passes`, `test_an_800_the_mal_exports_log_on_a_rewatch_dates_the_pass_it_finished`, `test_an_800_the_mal_exports_overwrite_keeps_one_record_and_one_log`; `vendor` (imports), with `test_a_reread_under_way_with_no_count_has_a_finished_attempt_and_a_log` pinning Kitsu's manga under MC-400; N-24, N-25; `listing` `test_an_800_an_import_isnt_refused`; `imports` `test_an_800_paused_or_dropped_after_an_earlier_pass_keeps_its_place`, `test_an_800_paused_after_an_earlier_pass_with_no_number_has_no_place_to_keep`, `test_an_800_a_persons_file_never_sets_the_shared_copy`, `test_an_800_with_no_copy_the_page_shows_their_own_numbers`, `test_an_800_a_stored_copy_wins_over_the_number_in_a_file`, `test_an_800_overwrite_rebuilds_a_kept_log_in_the_pass_it_finished`, `test_an_800_overwrite_moves_a_kept_rewatch_log_out_of_a_pass_the_source_says_is_open`, `test_an_800_a_page_stores_what_it_knows_about_an_imported_title`, `test_an_800_the_detail_page_does_too`, `test_an_800_a_page_with_nothing_new_writes_nothing`, `test_an_800_a_read_that_cant_store_it_still_answers`, `test_an_800_no_copy_changes_no_row`, `test_an_800_the_imports_end_reads_its_titles_copies`, `test_an_800_a_date_past_the_users_today_becomes_today_and_aborts_nothing`, `test_an_800_a_new_builds_future_log_is_still_refused`, `test_an_800_an_imported_log_of_a_title_with_nothing_aired_aborts_nothing`; `imports` `test_an_800_a_place_the_source_gives_wins_over_a_kept_log`, `test_an_800_watching_with_no_number_and_a_kept_log_starts_over`, `test_an_800_paused_with_no_number_and_a_kept_log_has_no_place_to_keep`, `test_an_800_plan_to_watch_with_a_kept_log_keeps_the_worked_out_status`, `test_an_800_a_number_that_reaches_what_the_log_covers_stays_in_its_pass`, `test_an_800_a_place_after_two_kept_logs_leaves_each_in_its_own_pass`, `test_an_800_kitsu_reconsuming_with_no_count_has_a_finished_pass_before_it`, `test_an_800_a_later_yamtrack_row_with_no_stars_or_notes_keeps_the_newest_it_has`, `test_an_800_a_later_yamtrack_rows_own_stars_and_notes_win`, `test_an_800_the_mal_exports_log_of_a_single_is_one_watch`, `test_an_800_a_singles_earlier_pass_stays_an_undated_mark_under_the_log`, `test_an_800_an_imported_log_of_a_single_with_nothing_to_claim_adds_its_own_watch`, `test_an_800_a_date_on_the_users_today_that_is_the_servers_tomorrow_is_a_log`, `test_an_800_a_date_further_out_isnt_real_and_counts_as_no_date`, `test_an_800_every_importers_dates_are_judged_on_the_users_calendar`, `test_an_800_completed_past_what_anilist_lists_keeps_the_sources_number`, `test_an_800_a_later_yamtrack_row_with_a_score_of_0_has_no_stars` |
| AN-801 | A webhook play isn't a watch | `imports` `test_an_801_a_watch_ticks_at_the_event_time_with_no_question`, `test_an_801_a_repeated_watch_records_nothing`, `test_an_801_an_unplayed_event_unticks`, `test_an_801_a_play_counts_as_choosing_watching_where_available`, `test_an_801_a_play_never_reopens_a_finished_title`, `test_an_801_a_rejected_event_is_acknowledged`, `test_an_801_details_unavailable_is_acknowledged`, `test_an_801_the_processor_routes_through_the_module`, `test_an_801_an_unplayed_event_for_an_unknown_title_creates_nothing`; `vendor` (webhooks); N-26; `imports` `test_an_801_a_title_with_no_item_and_mal_down_is_logged_and_acknowledged` |
| AN-802 | The export keeps today's columns | `imports` `test_an_802_one_row_per_title`, `test_an_802_the_new_fields_arent_columns`, `test_an_802_a_round_trip_keeps_counts_shelves_and_stars`, `test_an_802_a_rewatch_in_progress_isnt_exported`; N-30 |
| AN-900 | Installed builds keep working | `domain` `test_an_900_completed_means_mark_it_watched`, `test_an_900_in_progress_means_choose_watching`, `test_an_900_the_other_statuses_are_that_choice`, `test_an_900_raising_a_number_ticks_the_next_unwatched`, `test_an_900_lowering_removes_the_latest_unlogged_viewings`, `test_an_900_lowering_is_refused_when_a_log_covers_them`, `test_an_900_lowering_to_zero_leaves_the_library`, `test_an_900_a_number_past_the_total_is_refused_and_changes_nothing`, `test_an_900_status_and_number_together`, `test_an_900_an_unavailable_status_is_a_conflict_from_the_api_and_kept_from_the_web`, `test_an_900_notes_change_nothing_else`; `contract` `test_an_900_consume_pause_resume_and_drop_keep_a_top_level_tracking_state`, `test_an_900_the_keys_installed_builds_read_are_all_there`, `test_an_900_a_put_with_a_status_works`, `test_an_900_tracking_removal_goes_through_these_rules`, `test_an_900_a_log_ignores_auto_mark_consumed`, `test_an_900_a_retried_log_from_them_can_duplicate_as_today`, `test_an_900_stars_on_a_partway_title_show_the_servers_reason`, `test_an_900_their_progress_max_is_the_aired_count`; `web` `test_an_900_the_form_means_its_actions`, `test_an_900_a_number_in_the_form_means_ticks`, `test_an_900_the_progress_buttons_tick_and_untick`, `test_an_900_the_progress_button_past_the_total_says_why`, `test_an_900_lowering_is_refused_when_a_log_covers_it`, `test_an_900_saving_notes_changes_nothing_else`; N-27, N-28; `domain` `test_an_900_a_number_that_finishes_it_outlives_the_status_sent_with_it`, `test_an_900_a_number_makes_a_sent_planning_out_of_date_too`; `contract` `test_an_900_a_patch_that_reaches_the_total_with_in_progress_is_a_200`; `contract` `test_an_900_a_status_sent_with_progress_0_applies_once_the_viewings_go`, `test_an_900_progress_0_with_a_status_starts_again_from_nothing`, `test_an_900_progress_0_with_a_status_clears_stars_and_the_heart_as_leaving_does`, `test_an_900_progress_0_with_no_status_still_leaves_the_library`, `test_an_900_progress_0_with_a_status_that_cant_apply_leaves_the_library`, `test_an_900_a_log_still_refuses_progress_0_with_a_status`; `web` `test_an_900_a_form_with_a_status_and_a_number_of_0_keeps_the_status`, `test_an_900_a_finished_titles_form_set_to_0_empties_it`; `listing` `test_an_900_writing_notes_or_clearing_stars`; `domain` `test_an_900_starting_a_manual_title_over_takes_its_total_with_it`; `contract` `test_an_900_progress_0_with_in_progress_after_a_restored_choice_records_a_start`, `test_an_900_progress_0_with_the_choice_it_had_leaves_no_activity`; `listing` `test_an_900_a_lowered_number_is_an_untick`, `test_an_900_the_webs_minus_button_is_an_untick`, `test_an_900_a_raised_number_or_a_status_with_it_still_needs_the_numbers` |
| AN-901 | Expand, then contract | `schema` `test_n_29_old_inserts_save_by_the_database_defaults`, `test_an_901_old_inserts_into_the_history_table_read_back`, `test_an_901_the_migration_only_adds`; `contract` `test_n_29_the_old_codes_insert_shape`; `schema` `test_an_901_deleting_a_tracking_row_with_viewings_commits_and_leaves_nothing_read`, `test_an_901_deleting_a_log_with_fills_commits_and_its_fills_read_as_undated`, `test_an_901_deleting_a_title_with_a_stored_copy_commits` |
| AN-902 | There is no data to repair | `domain` `test_an_902_a_stored_number_becomes_an_undated_mark_at_the_first_write`, `test_an_902_a_stored_completed_marks_everything_aired`, `test_an_902_a_stored_choice_stays_the_choice`, `test_an_902_a_stored_completed_past_what_anilist_lists_keeps_its_number`; `listing` `test_an_902_a_write_with_no_copy_doesnt_work_a_stored_completed_out`, `test_an_902_clearing_stars_or_an_untick_with_no_copy_doesnt_either`, `test_an_902_a_write_with_no_copy_keeps_a_stored_completeds_dates`, `test_an_902_a_manual_titles_write_still_works_it_out`. The production count wasn't re-checked here; re-check it right before shipping, as the law says. |

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
| N-20 | `test_n_20_a_finished_titles_page_offers_next`; `test_n_20_opening_the_page_stores_the_next_title_with_no_job`; `test_n_20_a_page_with_cached_details_stores_the_next_title_too`; `test_n_20_a_usual_page_view_doesnt_write_the_next_title_again` |
| N-21 | `test_n_21_rewatch_then_stop` |
| N-22 | `test_n_22_pause_at_e8_tick_e8_by_mistake_then_untick` |
| N-23 | `test_n_23_eye_on_a_movie_then_stars_then_log` |
| N-24 | `test_n_24_a_mal_row_with_2_rewatches_and_a_count_of_12`; `test_n_24_a_dated_row_of_the_mal_export_is_a_log_on_its_own_date` |
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
- `test_stats_v2_ratings`' `test_hidden_logs_never_count` shows the shared log rule on a manga, because anime's Most watched now reads viewings.
- `test_completion_progress`, `test_stats_v2_ratings`: anime was the last type with a row per watch-through, so "the latest row is canonical" became "the one row is".
- `test_stats_cache`: a like no longer copies onto anime logs (TV-500), so the test is that the module's like write bumps the owner.
- `test_api_v1`: a number on an untracked title is ticks, so it has a progress change (N-27); the feed reads progress changes, so anime writes no `progress_updated` Activity row, as TV doesn't.
- `test_activity_progress`, `test_star_rating_scale`, `test_api_v1`'s diary test: the same assertions, on `FakeAnimeProvider`.
- `test_mal`, `test_kitsu`, `test_mal_export`: one record per title (AN-800).
- `test_anime_tracking`'s `test_an_101_the_list_runs_from_e1_to_one_past_the_last_watched`: it pinned rows 3 and 4 for a tick of E3, which left E1 and E2 out of reach (AN-101).
- `test_tv_tracking_acceptance`'s A-17, A-33, A-44, A-45, A-62 and TV-000: they set stars on a partway show, which TV-304 refuses.
- `test_webhooks_plex`, `test_webhooks_jellyfin`, `test_webhooks_emby`: the provider mocks now cover AniList and the short timeout.
- `test_anime_import_contract`'s `test_an_800_overwrite_replaces_viewings_marks_and_choices_and_keeps_logs`: it pinned progress 4 for a source at 1 with a kept log. The source's place now wins (AN-800).
- `test_mal_export`'s `test_full_anime_import`: it pinned an end date of 2099-01-01 from a finish date in the file. A date that far out isn't real (AN-800).

## Decisions made where the contract was silent

- Errors, entry points and `TVMutation` are TV's own, reused. `TVMutation.show_item` holds the anime `Item`.
- A pass is a stored `pass_number` on the row, viewings, marks and each log's stamp, not a comparison of times.
- A log's coverage is stored as ranges in `progress_snapshot["anime"]` (`{"v": 1, "pass": n, "covers": [[1, 24]]}`). A log with no stamp covers nothing and isn't converted.
- A single's first log takes a free viewing, a mark's fill or a tick. Later logs add their own fill. Deleting a log gives a taken fill back to its mark.
- A title with no numbers: E1 counts as aired until something is watched, so the eye, stars and a log have something to act on. `aired_count` is null, and it is caught up afterwards.
- A later episode's airing implies the earlier ones aired. Aired never passes the total.
- Sending the stars or heart a partway title already has refuses nothing, so a retry stays a 200. A different value is a 409, as for TV.
- The web gets "Log this first." as a form error. A web form's other unavailable statuses are ignored, as TV's are.
- The web has no log route for anime, as for manga and comics; its edit and delete go through the module.
- The hourly job works out anime too (TV-206's "within about an hour"), as its own task, "Refresh aired anime". The calendar selector looks again at a stored copy older than 7 days, so a raised total is seen.
- Imports never call a provider. A title with no copy gets a provisional listing from the source's total, corrected by the daily job.
- An imported rating marks only with no status and no history.
- A source's rewatch under way is a rewatch, and a MAL export's finish date on it dates the pass it finished.
- An import keeps the source's start and finish on the row, and writes one history row at the source's date, as the other importers do. A row's `start_date` follows its first dated fact unless an import set it; `end_date` follows its last dated fact while Completed. The app's Stats never read these dates, only viewings, so a mark still lands in no year there; the old web statistics page does read them, as it always has.
- The export writes a rewatch in progress as Completed with the count of all its history.
- `next_title` is stored from a page's metadata or the job, never fetched in a write.
- STAT-108 gained anime's mark-fill rule, the same as a TV season log's, and STAT-215's Most watched reads anime's viewings as it does a show's, so a rewatch ticked with no log counts.
- TV's log-first refusal uses the default conflict code, `tv_action_unavailable`.
- A form's or API's written dates are ignored for anime: dates are worked out (TV-207).
- A copy's ages are TV's: a day while airing or announced, a week once finished, and at once when a known airing time has passed. A write reads a stale copy too, under the same short timeout.
- A row with only AniList's answer counts as no copy, like an empty one. A write on it reads MAL again whatever the back-off says, because the answer is what the write is waiting on.
- While nobody knows how many have aired, the state still offers the eye and `opinion_action`: `mark`; tapping gets the retryable 503, so no key changed.
- A listing no copy backs takes its total from the person's own row, as the aired count. That holds a manual title's number and an imported title's until MAL is read, with no new column.
- A manual title with no number takes what's watched as its total, or 1, which makes it a single. A higher number raises it; a lower one keeps it.
- A kept log whose pass the source says is still open moves to the last finished pass, and its stamp with it.
- A kept log covering more than the source's place makes that place a rewatch, with as many finished passes before it as the logs' highest. A source with no number for Paused or Dropped has no place to win, and a single has no rewatch, so both keep the log where it is. A log covering no more than the source's number stays in its pass.
- A written status with a progress of 0 starts the title over from that status: what leaving the library clears goes too, stars, heart, starts and progress, and In progress records a new start. A written total stays. The web form sends its status beside a 0, except Completed, which would mark everything again; set to 0, a finished title leaves the library as before.
- A date from a source is judged on the person's calendar in `import_title`, for every importer, not only in the MAL export. A real date stays on the row as the source gave it.
- An import's log of a single takes its own pass's mark fill, or adds one. It never takes an earlier pass's.
- A list counts as another title's when it equals a prequel's or one holds the other, comparing AniList's whole title strings. One title in common doesn't count. Only a prequel that came out before this title counts, whatever its format: Attack on Titan's OVA came out after Season 1 and has its list, and Tonikawa's Season 2 follows a special with Season 1's list. Sequels aren't compared: a first season's list that is really its sequel's isn't caught.
- A title under way on AniList (releasing, on hiatus or cancelled) with nothing named next costs one more request per 25 titles, for its last aired entry. If that fails, those titles keep their copies and back off, because read alone the first answer would look like a title with no schedule, and every other title keeps its answer. A chunk that fails ends the asking, because AniList is down or hanging, and keeps the chunks before it.
- "More than half a year" is 183 days, chosen from 105 releasing titles with no next episode sampled on 2026-11-04. Real breaks seen run to about 4 months (Yamato yo Towa ni: REBEL3199's parts); schedules AniList plainly stopped following run from about 6 months (Zhe Tian) to 6 years (Nintama Rantarou).
- With no total and nothing named next, a tick past AniList's last entry works and the rows run one past it, because AniList may just be behind. With a total, an episode past the last entry reads TBA and a tick of it is refused, as it is for any unaired episode with no date.
- Rows always run from E1 to the last one with no gaps, on every title. A number in a gap that isn't known to have aired reads TBA.
- A source's Completed on a title with no way to know how many have aired marks up to its own number or the highest listed, whichever is later.
- `total_is_written`, a column in 0095, says a manual title's total is a number its owner wrote, a file gave or old code stored. No undo erases it. A number below the total only removes, so it doesn't count as writing one. Any other total was worked out, and an undo drops it when no log and no eye, stars or heart's mark is left. The column writes no history. A database that already ran the earlier 0095, as this worktree's did, needs it added by hand.
- Each history row of one write gets its own moment, a microsecond apart, the creation row first: `prev_record` and the page's order go by date alone. The history page walks the rows themselves, so rows at one moment can't stop it, on any media type.
- The history page doesn't list what anime keeps to work a title out: sources of stars and the heart, choices and when they were made, the rewatch and its pass, the earlier-episodes question and the log screen's offer.
- A page stores the next title from the series order it holds, even with cached details, when the copy's is missing or a week old.
- A year or month alone as a next title's release date reads as aired only once that year or month has passed.
- The calendar job keeps a title's events as they are when a read fails, and goes on with the rest. Its reads wait 10 seconds, and after one MAL timeout the rest of that run asks MAL nothing, so a hung MAL costs one short read. A refusal costs nothing, so it doesn't stop the run.
- An old Yamtrack file's stars and notes are the newest non-empty across a title's rows, as the single-weight path does.
- Writes that work from what's stored, with no provider call and no refusal: untick, a lowered number, the filled eye, clearing stars, turning the heart off, stopping a rewatch, a log edit, removal, deleting a log, and a form's notes or cleared stars.
- The web's pause, resume and drop ask MAL nothing when the title has an Item. With no Item they read it under the short timeout with no rate-limit retry, and answer the retryable message if that fails. A choice reads a stored copy as it is, stale or not, on the API too; with no copy it takes the usual short read.
- A write with no copy doesn't count as working out a row never worked out: its stored number waits for a write that has the real ones (AN-902). The job leaves such a row alone, as before.
- An imported number past the ceiling is dropped with a warning, not capped: 20,000 fills of a typo would be worse than none.
- A page read that finds the row behind its copy works the row out then and there, so a GET can write once. It never fails the read, and with no copy it does nothing.

## Final runs

- `scripts/check.sh` (run `31f95843`, at `0e304ca5`, after the third round's fixes): ruff clean, no missing migrations, 4,864 tests OK, 38 skipped. The four extra skips are `concurrency`, which needs row locks.
- A throwaway PostgreSQL 15: every migration on an empty database, then every anime file including `concurrency`, the TV acceptance, payload and API files, all of `integrations` and `events`, the stats files, `test_api_v1` and the web views: 1,622 tests OK, 1 skipped, at `cf57dbae`. Then the anime files, the MAL export, Kitsu and Yamtrack vendor suites, the history page and the calendar: 668 tests OK, at `dc50fe6e`; and with the series import contract, with 0095's new column, 719 tests OK, at `61b37f5a`. The history page's 500 is pinned there with a real clock. `concurrency` failed there before its fix in the first review: two first ticks made two viewings, and one person's write held up another on the same title. The cluster was deleted afterwards.

## Limits

- AN-501, AN-700's words, AN-701's sheet and AN-702's page have only their server part here. The app's are in `ios/Spine/SpineTests/AnimeTrackingTests.swift` and [the simulator record](anime-tracking-simulator-verification.md).
- AniList was called for real only by hand, at 3 seconds or more apart. On 2026-11-04, through the real `anilist.anime_episodes`: Kaguya-sama S2 and S3, Overlord II and Mob Psycho II lose their lists, and their first seasons, Attack on Titan S1 and Monogatari Second Season keep theirs; 25 titles in one request stayed under AniList's complexity limit (174 KB, no errors). Through the real read and rules with nothing stored: One Piece, Chibi Maruko-chan, Nintama Rantarou, Anpanman, Chiikawa, Ninjala, Yamato yo Towa ni, Zhe Tian, Li Xiongmao and Ni Tian Zhizun came out as this file says. The tests use its shape from those answers.
- A schedule AniList stopped following less than half a year ago still counts. Li Xiongmao has 18 episodes and its last entry is E6, from August, so it reads 6 of 18 with E7 onward TBA, whatever has really aired. It corrects itself when an entry arrives or half a year passes.
- An airing title AniList has no schedule for counts everything listed, in about 7% of the releasing titles the review sampled (AN-100 says so; kept on purpose).
- A page GET or the job moving a status adds a history row, which the old web heatmap counts. The app doesn't read that (kept on purpose).
- Tick every episode of a manual title and tap the eye: that adds no fill, so no mark, and nothing records that the eye finished it. The worked-out total goes at the next undo, an untick or a deleted log, and it reads "3 episodes" until the eye is tapped again.
- Locally PostgreSQL ran those chunks only, on version 15 because 16 isn't installed here, with Django's connection pool off. Three attempts at the whole suite on it stopped partway, because agent-cleanup ended the server with a RuntimeError each time; the likely cause is its process watch racing backends that leave the server's group, which isn't proven. CI's Database checks run the whole suite on 16 on the first push.
- No installed (TestFlight) build ran against this backend. AN-900 rests on tests that send the installed builds' request shapes.
- AN-902's production count wasn't re-checked: this work doesn't touch production. Migration 0095 adds a unique constraint on one record per person and title with no data step, so it fails on a database holding duplicate anime rows. Re-check right before shipping.
- TV-800 still says imports create no feed activity, as AN-800 did. AC-1 and the shared feed say an imported log is a log, for TV too; only AN-800 and N-24 were corrected here.
- TV's lock has the same joins this work removed for anime (`tv_tracking.py:1454`): one person's write holds up everyone on the same show, on Postgres. TV's `_create_tv` swallows the same IntegrityError, but its season row's own constraint sends the write round again, so threads left one viewing in every case tried. Neither was changed.
- `services.set_media_like`'s generic path after the per-type branches is now unreachable for every media type. It wasn't deleted.

## App payload

Everything below is additive. All of it is on the server now.

### Reads

- `GET /api/v1/media/mal/anime/{id}/`: `user_state.anime` is the [state](#state), and the top-level `anime_rows` is the [rows payload](#rows). Signed out, neither is there.
- `GET /api/v1/tracking/mal/anime/{id}/rows/`: `{"anime": state, "rows": rows}`. Home's sheet uses it. A first open with no stored copy tries AniList once, for about 3 seconds; after that it reads the stored copy.
- `GET /api/v1/tracking/?media_type=anime&continue_watching=true`: Home's and Profile's In progress: Watching, with an aired up next, most recently watched first. The app also sends `status=In progress`, because a server older than this branch ignores `continue_watching` for anime. Each row's TrackingState has the list state under `anime`, with up next's title and image.
- An anime's TrackingState and its media summary's `user_state` have the list state under `anime`, from stored values only. Other media types have no such key.

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

The answer is the envelope: `{"tracking": TrackingState or null, "anime": state, "row": the named episode's row or null, "rows": the whole rows payload when the write changed more than one row or the list itself, leaving the library included, else null, "changed": bool, "finished": null or an object, "earlier_unwatched": null or an object}`.
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
| 400 | `validation_error` | A bad field, or a refused write such as "This anime has 25 episodes." An episode number or a written number over 20,000 is one too. |
| 503 | `provider_unavailable` | `error.reason` is `tv_details_unavailable`: "Anime details are unavailable right now". Retryable; nothing changed. It answers a write with no copy, and, while MAL says airing and AniList hasn't answered, anything that fills the rest: the eye, stars or a heart that mark, a log, Mark watched up to here, a written Completed. A tick still works then. |

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
  "liked": false,
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
- `aired_count` is null when Spine doesn't know how many have aired: read "7 episodes". That includes an airing title AniList hasn't answered about, whatever `total` says, and a title with no total and no schedule, whatever AniList lists.
- `first_gap_episode` names AN-200's "11 of 12 · E4 not watched".
- `liked` says whether the heart is on after the write, because the TrackingState has no such key. `opinion_action` says what stars and the heart do, with no second request: `mark` marks at once, `log` opens the log screen holding the value (a direct set is a 409, "Log this first."), `save` only saves. Clearing is always a direct write. **TV has the same key** on its show detail state (`user_state.tv`) and season state (`user_state.tv.season`, and the envelope's `season`), with the same three values.
- `eye.tap` is `mark`, `nothing_aired` (show `message`), `unmark_marks`, `unmark_confirm` (ask `prompt`, count `count`) or `blocked` (show `message`).
- `remove_count` fills "Remove this anime? Your 13 watched episodes will be unmarked." `action_reasons` says why an action on the + menu isn't available.
- `rewatch` is `{"started_at", "pass_number"}` during a rewatch. `composer.targets[0].is_rewatch` is the log screen's default.
- `next_title` is `{"source", "media_type": "anime", "media_id", "title", "image_url", "has_aired", "single"}` once finished, until that one is finished too (AN-500). With `has_aired` and not `single`, Home's sheet moves on to it (AN-501).
- A single has `single: true`, `up_next: null` and an empty rows list, and its `available_actions` hold no `watching`, `pause`, `drop` or `rewatch` (AN-002). Sending one of those answers 409 with its reason.
- On another person's tracking list, a log the viewer can't see leaves `rating`, `start_date`, `end_date` and `anime.last_watched_on` null (AC-5).

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
