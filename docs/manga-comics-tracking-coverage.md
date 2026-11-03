# Manga and comics tracking coverage

Status: every law has automated tests; the native flows passed in the simulator (2026-11-03), on the code merged with the social feed.

This matrix maps every numbered law in [the manga and comics tracking contract](manga-comics-tracking-contract.md) to the backend and iOS tests that pin it, and to the simulator flows that showed it in the real app.

## Evidence key

### Backend test files

Test names below are prefixed with the short name of their file.
Tests cite their law by ID in a comment above them.

| Short name | File | What it drives |
| --- | --- | --- |
| `contract` | `src/api/tests/test_manga_comics_tracking_contract.py` | The domain rules through the API: `InheritedRulesTests` (MC-000), `CountingTests` and `CountingDaysTests` (MC-001 to MC-007), `FinishingTests` (MC-100 to MC-104), `ContinuingTests` (MC-200, MC-201), `InstalledBuildTests` (MC-400's API side, MC-500, MC-501). Each case runs on manga or comics; many run on both. |
| `imports` | `src/integrations/tests/imports/test_series_import_contract.py` | MyAnimeList, AniList, Kitsu, Yamtrack and the MyAnimeList export file. |
| `mal`, `anilist`, `kitsu` | `src/integrations/tests/imports/test_{mal,anilist,kitsu}.py` | The vendor suites, updated to one record per series. |
| `calendar` | `src/events/tests/calendar/test_comic.py`, class `NewIssueMovesFinishedComicsTests` | The comic calendar job. |
| `web` | `src/app/tests/views/test_series_web_contract.py` | The web's forms, progress buttons and delete. |
| `metadata` | `src/app/tests/test_metadata_fill.py` | The hourly metadata fill. |
| `exports` | `src/integrations/tests/test_exports.py` | The CSV export (the header test stays as it was). |

All backend tests fake their providers, so they need no `.env` and no API keys.
`CountingDaysTests` and `imports` `test_a_source_date_stays_on_its_day_in_new_york` run on America/New_York days through `override_settings`.

### iOS test files

All are in `ios/Spine/SpineTests/`.

| Short name | File |
| --- | --- |
| `Series` | `SeriesTrackingContractTests.swift`: decoding, words, the log screen, the progress sheet, the page's writes, Home. |
| `Badges` | `LibraryPosterBadgeTests.swift`: the poster badge. |

### Simulator flows

Flow names in the matrix are the rows below.
Each ran once, with the dev account on this branch's local backend, against live MyAnimeList and Comic Vine, on a scratch iPhone 17 (iOS 26.5).
Screenshots stayed outside the repo; the accessibility tree and the server's rows were the evidence.

| Flow | What ran |
| --- | --- |
| `Monster` | A finished manga, 162 chapters. Start; the sheet over Home and over the page; a number past the total; lowering a number; reaching the total with Not now, then a later save at the total (no log screen); the eye, stars and heart mid-read; a log; deleting it; Pause and Resume; Did Not Finish and picking up at the place; a new attempt reaching the total with a log; deleting that log; stars once finished; Reread at zero; the heart on a paused attempt; editing the log and deleting it (back to Paused with the old rating). |
| `One Piece` | A running manga, no total. "1100 chapters" with no bar; any number; no log screen by itself; Log with the chapter field prefilled; Keep Reading and Reread offered; Keep Reading at the place; the sheet over Home; Finished Reading; the second log not a reread. |
| `Saga` | A comic, 72 issues on Comic Vine, stored as its total. "12/72 issues" and the bar; "of 72" on the sheet over Home; the server's refusal past the total, gone once the number changed; reaching the total with a log and with Not now; a new issue, by raising the count by hand and calling `series_tracking.new_issue_out` from a shell (back to Currently Reading at the place, no activity, logs kept); the count growing past a Read comic (Keep Reading and Reread offered); Restart from Beginning. |
| `Killing Joke` | A one-issue comic. The sheet has no number and no Save; Finished Reading logs it. |
| `Eye` | A manga on To Read: the eye marks it Read with no date, log or activity; the filled eye restores To Read; a heart marks it Read. |
| `Shelves` | The Library's five sections for manga and for comics, in grid and list: a badge only on started, unfinished titles. To Read, Paused and Did Not Finish rows were set through the tracking write the app sends. |
| `Removal` | A To Read comic: Remove from Library asks, then removes. `Monster` showed it disabled with the reason, and an untracked title doesn't list it. |
| `Feed` | A followed account with manga and comic progress and a log, seeded from a shell: Home's items, a day's progress page, and a like on it. |
| `Surfaces` | Profile's In progress row (posters only) and Recent Activity (each title once); the Diary's rows; a title page's friend ("Reading, 5 of 74 issues"). |

## Contract laws

| Law | Rule | Backend tests | iOS tests | Simulator |
| --- | --- | --- | --- | --- |
| MC-000 | Library sections (BK-001) | `contract` `test_five_sections_one_section_per_series`, `test_changing_status_moves_the_one_record` | `Series` `testFiveShelvesWithTheBooksWords` | `Shelves` |
| MC-000 | Membership and removal (BK-002) | `contract` `test_simple_removal_needs_nothing_real_and_keeps_custom_lists`, `test_removal_is_blocked_while_an_attempt_or_a_log_remains`; `web` `test_delete_keeps_real_history` | None (server rule) | `Removal`, `Monster` |
| MC-000 | Plus menu (BK-100) | `contract` `test_plus_menu_offers_the_book_actions` | `Series` `testThePlusMenuOffersKeepReadingAndReread` | `Monster`, `One Piece`, `Saga`, `Removal` |
| MC-000 | Retrospective status (BK-101) | `contract` `test_direct_statuses_invent_no_attempt_place_date_or_log` | None (server rule) | `Shelves` |
| MC-000 | The eye (BK-102, BK-103, BK-310) | `contract` `test_the_eye_marks_read_undated_and_the_filled_eye_undoes_only_that`, `test_undoing_the_eye_restores_the_exact_status`, `test_the_eye_during_an_attempt_goes_through_the_log`, `test_the_filled_eye_cannot_undo_a_log`, `test_the_eye_is_the_current_status_and_older_history_keeps_it_undoable`, `test_an_undated_read_can_be_deleted_from_reading_history` | `Series` `testTheEyesWritesUseTheSeriesRoute` | `Monster` (mid-read), `Eye` |
| MC-000 | Starting (BK-104) | `contract` `test_starting_is_one_tap_at_zero_with_today`, `test_to_read_is_unavailable_during_an_attempt`, `test_starting_after_read_with_nothing_more_is_a_reread`; `web` `test_the_form_starts_an_attempt_and_saves_its_number` | None beyond the writes in `Series` | `Monster`, `One Piece`, `Saga` |
| MC-000 | Logs (BK-105 to BK-107) | `contract` `test_a_direct_log_creates_a_finished_attempt_and_its_log`, `test_a_log_finishes_the_attempt_under_way`, `test_a_log_finishes_a_paused_attempt`, `test_a_log_after_did_not_finish_keeps_the_closed_attempt` | `Series` `testWithATotalTheLogHasNoNumber`, `testTheRereadToggleStartsFromTheServersWord` | `Monster`, `One Piece`, `Saga` |
| MC-000 | Heart and stars (BK-200 to BK-203) | `contract` `test_stars_and_a_heart_with_nothing_under_way_mark_read`, `test_unhearting_keeps_read` | None beyond MC-104 | `Monster`, `Eye` |
| MC-000 | Attempts (BK-300, BK-303, BK-304, BK-306 to BK-309) | `contract` `test_one_attempt_is_under_way_at_a_time`, `test_pause_keeps_the_place_and_resume_continues_the_same_attempt`, `test_currently_reading_continues_a_paused_attempt`, `test_resuming_a_retrospective_pause_starts_at_zero`, `test_restart_keeps_the_old_attempt_as_did_not_finish`, `test_dates_stay_in_order`, `test_a_start_date_can_be_edited`, `test_reading_history_lists_closed_attempts_but_not_logged_ones`, `test_deleting_an_older_closed_attempt_changes_nothing_else`, `test_deleting_the_current_closed_attempt_restores_the_earlier_status`, `test_deleting_the_attempt_under_way_restores_the_earlier_status`; `web` `test_the_progress_buttons_need_an_attempt` | `Series` `testAttemptsAreEditedAndDeletedOnTheSeriesRoutes` | `Monster`, `Saga` (Restart) |
| MC-000 | Finishing and the diary (BK-400 to BK-406) | `contract` `test_a_log_and_the_finished_attempt_save_together`, `test_a_failed_log_leaves_the_attempt_unchanged`, `test_deleting_the_log_reopens_the_attempt_exactly`, `test_deleting_an_older_log_leaves_newer_state_alone`, `test_editing_a_logs_date_moves_the_attempt`, `test_deleting_a_direct_log_restores_what_was_there`, `test_read_counts_unique_series_and_each_log_one_read`, `test_reread_is_derived_and_the_toggle_is_display_only` | `Series` `testTheRepositoryUsesTheSeriesRoutes`, `testTheRereadToggleStartsFromTheServersWord` | `Monster` |
| MC-000 | Opinions and diary fields (BK-202, SW-100 to SW-108, SW-202, SW-203, SW-300 to SW-303) | `contract` `test_each_log_keeps_its_own_opinion_and_the_new_one_is_the_source`, `test_editing_the_source_log_updates_current_and_a_direct_change_decouples`, `test_a_rated_log_fills_an_empty_current_rating` | `Series` `testALogsRequestCarriesItsVisibilityAndItsNumber` | `Monster`, `Eye` |
| MC-000 | Dates (SW-005 to SW-007) | `contract` `test_two_logs_on_one_day_are_allowed`, `test_an_evening_start_and_log_stay_on_the_local_day`, `test_an_installed_builds_log_datetime_lands_on_the_local_day`; `imports` `test_a_source_date_stays_on_its_day_in_new_york` | `Series` `testLogsAreCalendarDatesAndKeepTheirVisibility` | `Monster`, `One Piece`, `Saga` |
| MC-000 | Saves and retries (BK-500, BK-501) | `contract` `test_retries_create_no_duplicates`, `test_a_status_and_a_refused_number_leave_nothing_behind`, `test_the_library_detail_and_list_agree`; `web` `test_saving_notes_changes_nothing_else` | `Series` `testAFailedKeepReadingRollsBack` | `Monster`, `One Piece`, `Saga`: page, Home, Library and the server agreed |
| MC-000 | Activity and visibility (AC-1 to AC-6) | `contract` `test_activity_says_started_reading_once_and_not_for_resume`, `test_activity_tells_chapters_and_issues`, `test_marks_are_never_activity`, `test_a_log_and_that_days_progress_are_separate_items`, `test_progress_and_logs_take_likes_and_comments`, `test_logs_keep_their_own_visibility` | `Series` `testLogsAreCalendarDatesAndKeepTheirVisibility`, `testALogsRequestCarriesItsVisibilityAndItsNumber` | `Feed`, `Surfaces` |
| MC-001 | Manga count chapters and comics count issues | `contract` `test_the_number_is_a_whole_number`, `test_comics_count_issues`; `imports` `test_reading_with_a_number_is_an_attempt_with_no_start` (never the volumes); `web` `test_the_form_starts_an_attempt_and_saves_its_number` | `Series` `testTheSheetCountsOneWholeNumber`, `testTheSummaryCountsChaptersAndIssues` | `Monster`, `One Piece`, `Saga` |
| MC-002 | A comic's number is how many issues were read | `contract` `test_a_comics_total_is_the_issue_count_not_events`, `test_comic_vine_gives_the_count_as_the_total`; `metadata` `test_a_comic_with_no_issue_count_is_fetched` | `Series` `testACardOpensTheProgressSheetOverHome`, `testAComicPastItsTotalGoesToTheServer` | `Saga` |
| MC-003 | A manga's total comes from its provider, when it has one | `contract` `test_with_no_total_any_number_is_accepted_and_there_is_no_max`, `test_a_total_that_appears_later_applies_at_once`, `test_a_provider_with_no_total_leaves_what_is_stored` | `Series` `testWithNoTotalAnyNumberIsAccepted`, `testTheBarNeedsATotal` | `One Piece` |
| MC-004 | A number past a known total is refused | `contract` `test_a_number_past_a_known_total_is_refused`, `test_a_comic_rereads_the_count_before_refusing`, `test_a_comic_past_the_fresh_count_is_refused_and_a_failed_read_refuses_too`; `web` `test_the_form_refuses_a_number_past_the_total` | `Series` `testAMangaPastItsTotalIsRefusedHere`, `testAComicPastItsTotalGoesToTheServer`, `testTheServersRefusalShowsItsOwnWords` | `Monster` (on the sheet), `Saga` (from the server) |
| MC-005 | A series with a total of one has no number | `contract` `test_a_total_of_one_has_no_number`, `test_a_one_issue_comic_finishes_through_its_log` | `Series` `testATotalOfOneHasNoNumber`, `testTheSheetLeavesOutTheNumberForATotalOfOne` | `Killing Joke` |
| MC-006 | What posters show | `contract` `test_posters_read_the_number_and_the_total` | `Badges` `testAMangaOrAComicShowsWhereYouStoppedOnlyWhileStartedAndUnfinished`; `Series` `testTheBarNeedsATotal` | `Monster`, `One Piece`, `Saga`, `Shelves`, `Surfaces` |
| MC-007 | Every save counts on its day | `contract` `test_a_save_counts_on_its_local_day`, `test_the_first_save_counts_whole_even_if_it_is_catch_up`, `test_lowering_a_number_fixes_a_typo`, `test_a_day_that_only_goes_down_shows_no_progress`, `test_a_log_adds_only_what_is_left`, `test_a_backdated_direct_log_counts_on_its_day`, `test_a_reread_counts_again`, `test_the_eye_counts_on_no_day`; `imports` `test_the_next_save_counts_only_what_is_new` | `Series` `testTheSummaryCountsChaptersAndIssues` | `Monster` (lowering, a reread counting again, read from the server's rows); other days not run |
| MC-100 | Reaching the total opens the log screen | `contract` `test_reaching_the_total_opens_the_log_screen_and_finishes_nothing`, `test_saving_the_log_finishes_the_attempt`, `test_saving_a_row_never_finishes_it`, `test_an_installed_builds_write_at_the_total_stays_currently_reading`; `web` `test_the_progress_buttons_never_finish_a_series` | `Series` `testASaveAtTheTotalSaysTheLogScreenFollows`, `testReachingTheTotalFromHomeOpensTheLogWithNotNow` | `Monster`, `Saga` |
| MC-101 | The log screen opens by itself once per attempt | `contract` `test_the_log_screen_opens_by_itself_once_per_attempt`, `test_a_new_attempt_opens_it_again` | `Series` `testALaterSaveDoesNotOpenIt` | `Monster`, `Saga` |
| MC-102 | With no total, the user decides what finished means | `contract` `test_with_no_total_only_the_user_finishes_and_the_log_takes_the_number`, `test_a_log_with_no_total_and_no_number_keeps_the_place`, `test_with_a_total_a_log_sets_the_place_to_the_total` | `Series` `testWithNoTotalTheLogTakesTheNumber`, `testWithATotalTheLogHasNoNumber`, `testALogsRequestCarriesItsVisibilityAndItsNumber` | `One Piece` |
| MC-103 | A log always means finished | `contract` `test_a_log_always_means_finished`; `web` `test_the_models_mark_consumed_is_the_eye` | `Series` `testALogScreenHasNoProgressMode` | `Monster`, `One Piece`, `Saga` |
| MC-104 | Stars and the heart are given once a series is finished | `contract` `test_stars_and_a_heart_during_an_attempt_open_the_log_screen`, `test_the_log_saved_with_the_value_finishes_the_attempt`, `test_stars_and_the_heart_change_freely_once_finished` | `Series` `testTheLogScreenOpensWithTheStarsOrHeart`, `testTheLogScreenIsBuiltWithTheValuesJustPicked` | `Monster`, `Eye` |
| MC-200 | Keep Reading continues a finished series that has more | `contract` `test_a_read_series_with_more_offers_both`, `test_a_read_series_with_nothing_more_offers_reread_only`, `test_the_eye_marks_the_whole_series`, `test_keep_reading_starts_at_the_old_place_with_no_start_and_is_not_a_reread`, `test_keep_readings_next_save_counts_from_the_old_place`, `test_reread_starts_at_zero_and_counts_again`, `test_deleting_a_keep_reading_log_reopens_that_attempt`, `test_an_installed_builds_write_on_a_read_series_keeps_going_when_there_is_more` | `Series` `testKeepReadingAndRereadShowAtOnce`, `testAFailedKeepReadingRollsBack`, `testThePlusMenuOffersKeepReadingAndReread`, `testTheRereadToggleStartsFromTheServersWord` | `Monster` (Reread only), `One Piece`, `Saga` |
| MC-201 | Picking up a stopped series continues from the place | `contract` `test_picking_up_after_did_not_finish_continues_from_the_place`, `test_restart_begins_at_zero_after_picking_up`, `test_picking_up_a_read_that_was_carrying_on_is_not_a_reread`; `imports` `test_to_read_paused_and_dropped_stay_the_users_choice` | `Series` `testPickingUpShowsThePlaceAtOnce` | `Monster` |
| MC-202 | A new issue moves a finished comic back to Currently Reading | `calendar` `test_a_read_comic_carries_on_by_itself`, `test_the_next_log_is_not_a_reread`, `test_an_eye_marked_comic_moves_at_zero`, `test_other_shelves_do_not_move`, `test_an_attempt_under_way_stays`, `test_the_same_last_issue_moves_nothing`, `test_a_comic_with_no_known_issue_moves_nothing`, `test_every_reader_of_it_moves`, `test_a_run_outside_the_jobs_year_moves_only_by_choice` | None (server job) | `Saga`, by calling the job's function; the daily job itself didn't run |
| MC-300 | The progress sheet is the book's | Server rule only for the attempt: `contract` `test_the_new_routes_answer_only_for_manga_and_comics` | `Series` `testACardOpensTheProgressSheetOverHome`, `testACardWithNothingToUpdateOpensThePage`, `testACardWithNoAttemptOpensThePage`, `testTheSheetCountsOneWholeNumber` | `Monster`, `One Piece`, `Saga` |
| MC-400 | Imports keep one tracking record per series | `imports` (MyAnimeList) `test_a_read_series_with_rereads_is_one_record`, `test_an_imported_undated_read_is_manageable`, `test_reading_with_a_number_is_an_attempt_with_no_start`, `test_the_next_save_counts_only_what_is_new`, `test_read_with_no_date_is_undated`, `test_to_read_paused_and_dropped_stay_the_users_choice`, `test_a_reread_under_way_has_a_finished_read_and_an_attempt`, `test_overwrite_replaces_the_series_and_keeps_hand_made_logs`, `test_new_mode_leaves_a_tracked_series_alone`; (AniList) `test_rereads_and_statuses`; (Kitsu) `test_rereads_are_finished_attempts`; (Yamtrack) `test_an_old_exports_row_per_reread_is_one_record`; (MyAnimeList export) `test_times_read_and_a_finish_date`, `test_a_reread_under_way`, `test_overwriting_twice_keeps_one_record_and_one_log`, `test_a_future_finish_date_is_undated`; `mal`, `anilist`, `kitsu` (one record per series); `contract` `test_writes_keep_one_record_per_series` | None (server rule) | Not run: no import ran in the simulator |
| MC-500 | Installed builds keep working | `contract` `test_a_status_and_progress_write_goes_through_these_rules`, `test_an_installed_builds_write_at_the_total_stays_currently_reading`, `test_an_installed_builds_write_on_a_read_series_keeps_going_when_there_is_more`, `test_a_diary_entry_with_auto_mark_consumed_goes_through_these_rules`, `test_an_installed_builds_future_log_is_a_400`, `test_tracking_removal_goes_through_these_rules`, `test_the_pause_resume_drop_and_consume_actions_still_answer`, `test_the_keys_installed_builds_read_are_all_there`, `test_an_installed_builds_rating_stays_on_its_scale`, `test_the_new_routes_answer_only_for_manga_and_comics`, `test_a_library_page_does_not_query_per_attempt`, `test_an_installed_builds_log_datetime_lands_on_the_local_day`; `exports` `test_a66_the_header_is_exactly_todays`; `web` `test_saving_notes_changes_nothing_else` | `Series` `testASeriesStateDecodesBesideTheBooksKey`, `testAPagesUserStateReadsSeriesAsItsReadingState` | Not run: no installed build ran against this backend |
| MC-501 | There is no data to repair | `contract` `test_the_migration_is_additive_with_no_repair` | None | Not applicable |

## What the simulator found

Each is fixed, with a test:

- The log screen opened with empty stars and heart when they opened it, because its cover read those values only inside its closure (MC-104). The same fix covers "Not now" on the page's sheet (MC-100). The cover is shared, so a book's stars and heart mid-read now reach its log screen too. `Series` `testTheLogScreenIsBuiltWithTheValuesJustPicked`, and seen working in `Monster`.
- A comic's refusal stayed in red beside a corrected number until the next save (MC-004). `Series` `testTheServersRefusalGoesWithItsNumber`.
- Empty manga and comic shelves spoke of reading journeys (MC-000). Copy only; seen in `Shelves`.

## Final runs

- `scripts/check.sh` (run `d51c0918`, at `abd8ec84`, after merging `myapp-main`; later commits touch only the app and docs): ruff clean, no missing migrations, 4,188 tests OK, 34 skipped.
- `SpineTests` on a scratch iPhone 17 (iOS 26.5) simulator (run `f18235dc`, the code of `9f22fb3a`): 1,472 tests, 0 failures. The simulator was deleted afterwards.

## Limits

- The flows ran once each, with one account and one seeded follower, on SQLite. The phone is on iOS 27; the simulator was iOS 26.5.
- The first part of `Monster` ran before the merge with the social feed. Everything was repeated on the merged code except a later save at the total not reopening the log screen (MC-101), which the merge didn't touch.
- Imports, the export, the daily comic job, other time zones and PostgreSQL weren't run in the simulator; their laws rest on the backend tests. PostgreSQL wasn't run locally either: CI's Database checks run it on the first push.
- No installed (TestFlight) build ran against this backend. MC-500 rests on backend tests that send the installed builds' request shapes, and on decoding tests.
- The migration adds a unique constraint on one record per person and series, with no data step (MC-501). It would fail on a database holding duplicate manga or comic rows. Production has none; the always-on test backend's database wasn't checked, because it is off limits to this work.
- The simulator needed its hardware keyboard turned off. The progress sheet closes itself when the keyboard hides (books' too, since 454bedb7), and with a hardware keyboard attached the keyboard hides at once, so the sheet opens and closes. It wasn't changed; a phone with a Bluetooth keyboard would see the same.
