# Game tracking coverage

This matrix records automated checks and observed native results. Each result names its evidence and limits.
The approved contract remains unchanged in `game-tracking-contract.md`.

## Evidence key

- **R1–R24**: numbered steps in [root Simulator journal](/private/tmp/spine-game-runtime/simulator-journal.md). Configured iPhone 17: `7990CE55-B413-4CCC-8F92-45FD2A33CF32`; verified mirror on port 3200.
- **A / case or law**: named sections in [independent Simulator journal](/private/tmp/spine-game-runtime/audit-simulator-journal.md). Separate iPhone 17: `F9BB2486-1E78-4197-8A65-81B574413E25`; verified mirror on port 3201.
- **T**: `SpineUITests.testLocalBackendLoginAndLibrarySmoke`, passed against the real local API in 87.795 seconds. It verifies login, library navigation, zero/clear persistence, rating prefill/cancellation, keyboard dismissal, a visible validation error, and draft retention.
- Both native sessions use the new-worktree app, `http://127.0.0.1:8017`, and isolated database `/private/tmp/spine-game-runtime/game.sqlite3`.
- Final native results: `/private/tmp/spine-game-ios-final-verified.xcresult` and `/private/tmp/spine-game-ios-final-verified.log`.
- Backend and Swift tests cover deterministic provider responses and failure injection. They do not replace actual native interaction.

## Contract laws

| Law | Required behavior | Implementation area | Verification | Result |
| --- | --- | --- | --- | --- |
| GM-000 | Reuse established contracts without repeating settled product questions | Shared opinions, privacy, API services, imports, library, statistics | Shared Django/Swift regressions; live cross-surface checks | Pass: shared backend/Swift regressions, authorization/privacy/source tests; R1–21 and A verify native cross-surface state. |
| GM-001 | Users can track games through status alone | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R1–3 start and complete with both progress fields unknown; domain/API regression passes. |
| GM-002 | Each status has one library section | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R21 verifies movement through all five library sections; unique-game counts pass API tests. |
| GM-003 | The playthrough supplies Your Progress | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R4–9 and R17–18 verify current progress, separate attempts, and linked completion display. |
| GM-004 | Starting a game requires one tap | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R1 and A/G11 start immediately with a local start date and unknown progress; timezone regressions pass. |
| GM-005 | Only one playthrough can be Playing or Paused | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R5 pause/resume retains one attempt; API retries and the open-attempt constraint pass. |
| GM-006 | Dropped closes the current attempt | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R5 confirms drop, keeps its progress, then starts a separate attempt without inherited progress. |
| GM-007 | Restart from Beginning preserves the previous attempt | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R5 confirms restart and preserves the prior attempt as Dropped; atomic/retry tests pass. |
| GM-008 | Users can delete an accidental playthrough | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: A/G14 confirms accidental deletion and restores valid direct Paused without reviving deleted history. |
| GM-009 | Past Paused and Dropped states need no playthrough | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: A/G11 verifies direct Paused without an attempt; R21 verifies direct Dropped without an attempt. |
| GM-010 | Resume starts tracking when Paused has no playthrough | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: A/G11 resumes direct Paused into one new Playing attempt with unknown progress. |
| GM-011 | Planning cannot replace an unfinished playthrough | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: API rejects Planning with an unfinished attempt; R21 verifies Planning from direct Paused. Disabled-option behavior has Swift/code review coverage. |
| GM-012 | Pause is immediate and Drop requires confirmation | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R5 observes immediate Pause, same-attempt Resume, and destructive Drop confirmation. |
| GM-013 | Tracked drops have an editable date | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R5/R17 observe dated tracked drops; backend tests verify drop-date correction and limits. Date correction itself was not repeated manually. |
| GM-014 | Tracked playthroughs require a valid start date | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: R16 rejects a start after completion; tracked clear is unavailable in UI. API rejects null and invalid dates; A/G23 retains unknown direct start. |
| GM-015 | Restoring status must not restore deleted history | Game/GameSession; game_tracking status and restore logic; media rail and menu | Domain/API transition tests; live status/history flows | Pass: A/G14 and R18 verify restoration skips deleted support and removes deleted progress references; independent domain regressions pass. |
| GM-100 | Hours and percentage are independent | GameSession progress; API serializers; native progress editor | Domain/API validation; Swift payload tests; live field updates | Pass: R4 and T verify independent optional fields; R9 verifies separate field source links. |
| GM-101 | Updates preserve untouched values | GameSession progress; API serializers; native progress editor | Domain/API validation; Swift payload tests; live field updates | Pass: R4 and T verify untouched values, explicit clear, and zero after the clear-button fix. |
| GM-102 | Hours are a playthrough total | GameSession progress; API serializers; native progress editor | Domain/API validation; Swift payload tests; live field updates | Pass: R4 replaces 750 minutes with 780, rather than adding them; input label also checked in T. |
| GM-103 | Display only supplied progress | GameSession progress; API serializers; native progress editor | Domain/API validation; Swift payload tests; live field updates | Pass: R1, R4, R15 and T show status-only, supplied progress, zero, and separate lifetime playtime. |
| GM-104 | Users can correct progress in either direction | GameSession progress; API serializers; native progress editor | Domain/API validation; Swift payload tests; live field updates | Pass: R4 lowers percentage; R16 lowers hours without changing the completion snapshot or attempt. |
| GM-105 | Percentage uses whole numbers from zero to 100 | GameSession progress; API serializers; native progress editor | Domain/API validation; Swift payload tests; live field updates | API/Swift pass for all limits. R4 and T reject 101; zero/100 observed. Negative/decimal UI entry was not achieved by the tool (R22). |
| GM-106 | Playtime uses hours and optional minutes | GameSession progress; API serializers; native progress editor | Domain/API validation; Swift payload tests; live field updates | Pass: R4/T verify hour-minute totals, blank, and explicit zero. Negative input rejection is verified by API/Swift tests, not native typing. |
| GM-200 | Completion is an explicit user decision | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R4 keeps 100% Playing; R1/R8 finish only after explicit completion save, without forced progress. |
| GM-201 | Users define completion, including games without an ending | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: status-only and 65% completion observed in R1/R8; domain accepts completion without an ending or achievement prerequisite. |
| GM-202 | Progress updates can continue after completion | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R7/R9 update the completed attempt while preserving its status, log, and completion date. |
| GM-203 | The completion log preserves progress at completion | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R7/R9/R16 preserve completion snapshots after later progress and downward corrections. |
| GM-204 | Saving the completion log completes the playthrough | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R1/R5 cancellation, R8 save, and R14 real network failure/retry verify atomic completion and preserved drafts. |
| GM-205 | The eye can record an undated past completion | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: A/G27 observes undated completion with no attempt, log, date, or Update Progress. |
| GM-206 | The eye finishes an active or paused playthrough through its log | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R1/R5 and A/G28 route the active eye through the completion composer; cancellation leaves canonical state. |
| GM-207 | The eye reflects the current status | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R17 and A/G28 observe eye state following current status; API/Swift state tests pass. |
| GM-208 | The filled eye can undo undated completion but protects logs | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: A/G27/G30 verifies undated undo; web/API tests protect dated logs from eye removal. |
| GM-209 | The eye reuses earlier completion history | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R17 toggles a status-only return and selects the latest completion by date; R18 removes its selected completion safely. |
| GM-210 | The completion form starts with current values | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R6/R8/R14 and A/G28 observe current progress and selected opinion prefill; first-presentation defect was fixed and retested. |
| GM-211 | Initial completion saves progress to the log and playthrough together | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass after timezone fix: R8 corrects 41h to 40h in the composer and saves both records with field links. |
| GM-212 | Log progress corrections follow only while the field remains linked | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R9 verifies linked corrections, hours-only decoupling, and percentage remaining linked; R10 preserves links on date-only edit. |
| GM-213 | Completion dates use simple calendar limits | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R10 rejects before-start completion and disables future dates; R12 accepts backdating before later progress. API rejects future submissions. |
| GM-214 | Direct Log Completion creates a completed playthrough | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R13 and A/G23 create direct completed attempts with optional unknown start and progress, preserving earlier history. |
| GM-215 | Log Completion finishes an existing unfinished playthrough | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R1/R8 and A/G28 complete the existing live attempt; direct Paused behavior also passes API tests. |
| GM-216 | A replay requires an earlier completion | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R13 observes replay default from real completion history; API tests prove dropped-only attempts do not establish a replay. |
| GM-217 | The diary Replay toggle controls only the badge | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R13 hides the saved Replay badge; API tests verify unchanged history and counts. |
| GM-218 | Completion dates determine replay order | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R13 inserts an older first completion while preserving latest displayed progress and its chosen badge; API tests cover counts and same-day order. |
| GM-219 | Deleting the current completion reopens its tracked playthrough without losing progress | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R7 deletes the controlling live completion and reopens the same attempt with its latest 50h/80%. |
| GM-220 | Deleting older completion history does not disturb newer tracking | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: A/G24 deletes older completion history while preserving current Playing 10h; R18 selects surviving completed progress. |
| GM-221 | Deleting a direct completion log removes its generated playthrough | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: A/G23 confirms deletion removes the direct log, generated attempt, and later progress while preserving custom-list membership. |
| GM-222 | Backdated completion preserves later progress | game_tracking completion/deletion; DiaryEntry snapshots; native log editor | Domain/API history tests; Swift draft tests; live completion/edit/delete flows | Pass: R12 logs historical 40h/65% while preserving later 50h/80% independently; API tests also cover zero/null fields. |
| GM-300 | Rating and heart actions follow book completion rules | Shared opinion sources; game actions; native action rail | Source-provenance regressions; live eye/heart/rating flows | Pass: A/G28 and T verify heart/rating composer prefill, cancellation, and atomic opinion/completion save. |
| GM-301 | Removing an opinion does not change status | Shared opinion sources; game actions; native action rail | Source-provenance regressions; live eye/heart/rating flows | Pass: API source tests verify direct clears preserve status, progress, and historical opinions. This exact clear path was not repeated manually. |
| GM-302 | Optional rating-picker dismissal preserves a direct heart action | Shared opinion sources; game actions; native action rail | Source-provenance regressions; live eye/heart/rating flows | Pass: API/Swift opinion persistence tests; A/G27/G30 verifies direct undated/heart flows and picker dismissal. Optional post-heart picker dismissal is not separately exercised. |
| GM-303 | Current and diary opinions follow the shared source rules | Shared opinion sources; game actions; native action rail | Source-provenance regressions; live eye/heart/rating flows | Pass: API/Swift tests cover all source coupling/decoupling cases; A/G28 saves linked rating/heart together and R14 verifies failed-save preservation. |
| GM-304 | Tracking removal clears current opinions, not custom lists | Shared opinion sources; game actions; native action rail | Source-provenance regressions; live eye/heart/rating flows | Pass: A/G30 removes both current opinions and tracking while retaining the custom list; A/G23 confirms direct-log removal cleanup. |
| GM-400 | Play History shows dropped attempts and undated completion | GameSession/undated history API; native Play History | Domain/API history tests; live history edits and removal | Pass: R5/R17 show dropped attempts; A/G27/GM402 shows the undated history row without invented dates. |
| GM-401 | Users can delete a dropped attempt from Play History | GameSession/undated history API; native Play History | Domain/API history tests; live history edits and removal | Pass: R20 confirms dropped-attempt deletion and preserves current Completed progress/log. R23 repeats deletion on the final binary, preserving a newer Paused attempt. Initial inert interaction did not recur. |
| GM-402 | Users can remove undated completion from Play History | GameSession/undated history API; native Play History | Domain/API history tests; live history edits and removal | Pass: A/GM402 removes undated completion after a replay starts; the active attempt remains unchanged. |
| GM-403 | Users can correct a dropped attempt's progress | GameSession/undated history API; native Play History | Domain/API history tests; live history edits and removal | Pass: R5 edits hours and clears percentage on a dropped attempt while preserving newer tracking. |
| GM-500 | Imported lifetime playtime stays separate from playthrough hours | Steam importer; shared import helpers; separate provider total | Import integration tests; live resulting-state check | Pass: R15 shows 300h lifetime separately from 10h current progress after service refresh and relaunch. Real replay/import variants pass provider-mocked integration tests. |
| GM-501 | Steam imports do not infer the user's tracking status | Steam importer; shared import helpers; separate provider total | Import integration tests; live resulting-state check | Pass: R15 observes new Planning and preserves Playing on refresh. All status variants pass Steam integration tests; no live Steam fetch is claimed. |

## Acceptance cases

| Case | Action | Laws | Verification layers | Automated result | Simulator result |
| --- | --- | --- | --- | --- | --- |
| G-01 | Start Playing without progress, then finish and save a log without progress. | GM-001, GM-004, GM-204 | Django domain/API; Swift tests; live Simulator | API pass: `test_g01_status_only_completion_and_g35_retries`. | Pass — R1–3: status-only start/completion, one log, unknown progress, relaunch/refetch persistence. |
| G-02 | Save hours only, then percentage only. | GM-100, GM-101 | Django domain/API; Swift tests; live Simulator | API pass: `test_g02_g03_g04_g06_g07_independent_optional_progress_totals`. | Pass — R4: hours-only then percentage update preserves both independent values. |
| G-03 | Clear one progress field; later enter zero. | GM-101, GM-105, GM-106 | Django domain/API; Swift tests; live Simulator | API pass: `test_g02_g03_g04_g06_g07_independent_optional_progress_totals`. | Pass after fix — R4 and T: clearing hours preserves percentage; explicit 0 renders as 0h. |
| G-04 | Lower hours or percentage to correct an error. | GM-104 | Django domain/API; Swift tests; live Simulator | API pass: `test_g02_g03_g04_g06_g07_independent_optional_progress_totals`. | Pass — R4 lowers percentage; R16 lowers hours 50→49 without changing the log snapshot. |
| G-05 | Enter invalid percentage or negative playtime. | GM-000, GM-105, GM-106 | Django domain/API; Swift tests; live Simulator | API pass: `test_g05_invalid_progress_is_atomic`. | Partial native input coverage — R4/T reject 101 without changes. R22 could not insert -1; R24 rejects nonnumeric hours and preserves saved state. Negative/decimal validation passes API/Swift tests. |
| G-06 | Enter 12h 30m, then replace it with 13h. | GM-102, GM-106 | Django domain/API; Swift tests; live Simulator | API pass: `test_g02_g03_g04_g06_g07_independent_optional_progress_totals`. | Pass — R4: 12h 30m stores 750, then 13h replaces it with 780 minutes. |
| G-07 | Save 100 percent while Playing. | GM-000, GM-200 | Django domain/API; Swift tests; live Simulator | API pass: `test_g02_g03_g04_g06_g07_independent_optional_progress_totals`. | Pass — R4: 100% remains Playing with no log or automatic composer. |
| G-08 | Finish at 65 percent and 40 hours. | GM-200, GM-204 | Django domain/API; Swift tests; live Simulator | API pass: `test_g08_g16_g17_g18_independent_completion_snapshot_coupling`. | Pass after date fix — R8: Completed 40h/65%, matching linked log and playthrough values. |
| G-09 | Open Finish from Playing or Paused, then cancel or fail saving. | GM-204 | Django domain/API; Swift tests; live Simulator | API rollback pass: `test_g09_g35_failed_compound_completion_has_no_effects`. | Pass — R1/R5 cancel Playing/Paused; R14 real connection failure preserves state/draft. T verifies visible save error and cancellation. |
| G-10 | Pause and resume an actual attempt. | GM-005, GM-012 | Django domain/API; Swift tests; live Simulator | API pass: `test_g10_g11_g12_g13_status_attempt_lifecycle`. | Pass — R5: pause/resume preserves session 2, start date, and 0h/65%. |
| G-11 | Assign retrospective Paused, then Resume. | GM-009, GM-010 | Django domain/API; Swift tests; live Simulator | API pass: `test_g10_g11_g12_g13_status_attempt_lifecycle`. | Pass — A/G11: retrospective Paused creates no attempt; Resume starts one attempt with unknown progress. |
| G-12 | Drop an attempt, then select Playing. | GM-006, GM-013 | Django domain/API; Swift tests; live Simulator | API pass: `test_g10_g11_g12_g13_status_attempt_lifecycle`. | Pass — R5: confirmed drop preserves session 2; Playing creates session 3 with unknown progress. |
| G-13 | Restart a Playing or Paused attempt. | GM-007 | Django domain/API; Swift tests; live Simulator | API pass: `test_g10_g11_g12_g13_status_attempt_lifecycle`. | Pass — R5: confirmed restart retains session 3 as Dropped and creates session 4 with unknown progress. |
| G-14 | Delete an accidental attempt after deleting its prior completion evidence. | GM-008, GM-015 | Django domain/API; Swift tests; live Simulator | API pass: `test_g14_g30_deleted_undated_evidence_cannot_restore`. | Pass — A/G14: deleting the replay after its older completion restores only valid direct Paused; no deleted history returns. |
| G-15 | Edit or clear a dropped attempt's progress. | GM-403 | Django domain/API; Swift tests; live Simulator | API pass: `test_g15_dropped_progress_edits_do_not_change_newer_attempt`. | Pass — R5: dropped attempt hours change and percentage clears; newer Playing attempt remains unchanged. |
| G-16 | Continue a completed playthrough from 40 hours and 65 percent to 50 hours and 80 percent. | GM-202, GM-203 | Django domain/API; Swift tests; live Simulator | API pass: `test_g08_g16_g17_g18_independent_completion_snapshot_coupling`. | Pass — R7: completed progress increases to 50h/80%; diary keeps 40h/65%. |
| G-17 | Correct hours in the initial completion composer when no later progress exists. | GM-211 | Django domain/API; Swift tests; live Simulator | API pass: `test_g08_g16_g17_g18_independent_completion_snapshot_coupling`. | Pass after date fix — R8: same-day composer correction 41h→40h saves 40h on both records. |
| G-18 | Correct a log's hours before and after separately updating playthrough hours. | GM-212 | Django domain/API; Swift tests; live Simulator | API pass: `test_g08_g16_g17_g18_independent_completion_snapshot_coupling`. | Pass — R9: linked corrections update both records; separate hours update decouples only hours. |
| G-19 | Backdate completion to before an existing later progress update. | GM-213, GM-222 | Django domain/API; Swift tests; live Simulator | API pass: `test_g19_g20_g31_g32_backdate_and_start_date_limits`. | Pass — R12: backdated log 40h/65% preserves later current 50h/80% and independent links. |
| G-20 | Change only the date of a log whose hours are independent. | GM-000, GM-212, GM-213 | Django domain/API; Swift tests; live Simulator | API pass: `test_g19_g20_g31_g32_backdate_and_start_date_limits`. | Pass — R10: date-only log correction preserves current progress, snapshot, and independent links. |
| G-21 | Add an older completion while a more recently completed attempt has progress. | GM-209, GM-218, GM-303 | Django domain/API; Swift tests; live Simulator | API pass: `test_g21_g25_g26_g29_completion_date_selection_and_saved_badges`. | Pass — R13: older direct completion 20h/20% leaves latest completed progress 50h/80% selected. |
| G-22 | Delete the log that currently completes a tracked attempt after recording side-quest progress. | GM-219 | Django domain/API; Swift tests; live Simulator | API pass: `test_g22_g23_g24_deletion_preserves_only_supported_state`. | Pass — R7: confirmed current-log deletion reopens the same tracked attempt with latest 50h/80%. |
| G-23 | Delete a direct-log completion after recording later progress on that generated attempt. | GM-015, GM-221 | Django domain/API; Swift tests; live Simulator | API pass: `test_g22_g23_g24_deletion_preserves_only_supported_state`. | Pass — A/G23: direct completion 4h followed by 6h progress; confirmed log deletion removes generated attempt and restores untracked. |
| G-24 | Delete an older completion while a newer attempt is Playing. | GM-220 | Django domain/API; Swift tests; live Simulator | API pass: `test_g22_g23_g24_deletion_preserves_only_supported_state`. | Pass — A/G24: older log/attempt deletion preserves newer Playing 10h. |
| G-25 | Return from a dropped replay to Completed using the eye. | GM-209 | Django domain/API; Swift tests; live Simulator | API pass: `test_g21_g25_g26_g29_completion_date_selection_and_saved_badges`. | Pass — R17: eye return selects latest completed 49h/80%, not dropped replay 5h; undo restores Dropped 5h. |
| G-26 | Delete the older completion currently selected by that status-only return. | GM-015, GM-209, GM-220 | Django domain/API; Swift tests; live Simulator | API pass: `test_g21_g25_g26_g29_completion_date_selection_and_saved_badges`. | Pass — R18: deleting selected completion removes its attempt; current display selects surviving 20h/20% completion. |
| G-27 | Mark Completed without a playthrough, then dismiss the optional rating picker. | GM-000, GM-205 | Django domain/API; Swift tests; live Simulator | API undated-state pass: `test_g27_g28_opinions_require_composer_and_preserve_history_sources`. | Pass — A/G27: undated eye completion survives picker dismissal with no attempt/log/progress. |
| G-28 | Heart or rate an actual Playing or Paused attempt. | GM-300 | Django domain/API; Swift tests; live Simulator | API pass: `test_g27_g28_opinions_require_composer_and_preserve_history_sources`. | Pass after UI fixes — A/G28 and T: selected heart/rating prefill, clean cancellation, final 2.5-star/heart completion with one log. |
| G-29 | Hide a replay badge, or insert a backdated first completion. | GM-216 through GM-218 | Django domain/API; Swift tests; live Simulator | API pass: `test_g21_g25_g26_g29_completion_date_selection_and_saved_badges`. | Pass — R13: older first completion saved with Replay hidden; latest progress stays selected. Actual chronology/counts are also API-tested. |
| G-30 | Remove all tracking or undo undated completion through the eye. | GM-000, GM-304 | Django domain/API; Swift tests; live Simulator | API pass: `test_g14_g30_deleted_undated_evidence_cannot_restore`. | Pass — A/G30: eye undo clears rating, heart, and tracking while custom-list membership remains. |
| G-31 | Set a future completion date, or one before the known start date. | GM-213 | Django domain/API; Swift tests; live Simulator | API pass: `test_g19_g20_g31_g32_backdate_and_start_date_limits`. | Pass with UI prevention — R10 rejects before-start completion atomically; future dates are disabled. API tests reject future submissions. |
| G-32 | Clear a tracked start date, or move it after the first progress, completion, or drop date. | GM-014 | Django domain/API; Swift tests; live Simulator | API pass: `test_g19_g20_g31_g32_backdate_and_start_date_limits`. | Pass with UI prevention — R16 rejects start after completion and retains saved date. Tracked clear is unavailable; API rejects explicit null. |
| G-33 | Import 300 Steam lifetime hours while a replay has 10 hours. | GM-500 | Django import integration; app resulting state | Pass: `GameImportContractTests.test_g33_refresh_keeps_real_replay_and_completion_snapshot` and `test_g33_refresh_preserves_playthrough_and_completion_progress` | Live resulting-state pass — R15: separate 300h lifetime and 10h Playing survive deterministic import-service refresh/relaunch. Real replay variant is automated; no live Steam fetch. |
| G-34 | Import Steam data for a tracked game, then a previously untracked game. | GM-501 | Django import integration; app resulting state | Pass: `GameImportContractTests.test_g34_steam_preserves_every_existing_status` and `test_g34_new_steam_games_are_planning_regardless_of_playtime` | Live resulting-state pass — R15: new imported state is Planning/no attempt; later import preserves Playing. All other statuses covered by Steam integration tests. |
| G-35 | Retry a save, double tap, or encounter a failed compound mutation. | GM-000, GM-204 | Django domain/API; Swift tests; live Simulator | API pass: `test_g01_status_only_completion_and_g35_retries`; `test_g09_g35_failed_compound_completion_has_no_effects`. | Pass — R14: real server outage preserves draft/state; retry saves once on the same attempt. Duplicate taps and transaction rollback also pass automated tests. |

## Additional inherited checks

- Independent rating and heart sources; source edits, clears, deletion, and recoupling.
- Distinct same-day logs, local dates, stable completion ordering, half-star validation.
- Account privacy, authorization, one diary activity, and import activity suppression.
- Import source identity, repeat assertions, and independent-opinion protection.
- API omission versus null, failed transactions, retry identities, and concurrent open-attempt constraints.
- Fresh and upgrade migrations; preserve ambiguous legacy data without invented playthroughs.
- Book, movie, and music regression suites; ruff; Django checks; migration consistency.
- Simulator uses the changed backend and isolated database. Screenshots alone do not prove an interaction.

## Import and web checks observed

- `integrations.tests.test_game_tracking_contract`, `test_steam_update`, `imports.test_steam`, `imports.test_hltb`, `imports.test_yamtrack`, and `test_exports`: 29 tests passed on 2026-09-28. Log: `/tmp/spine-game-import-tests.log`.
- `app.tests.test_game_tracking_web`: all eight tests passed within the final affected run. These test real Django web routes and HTMX responses, not Simulator interactions.
- The live-playthrough import-opinion bypass was fixed. `GameImportContractTests.test_title_opinions_cannot_bypass_an_unfinished_playthrough` passes in the combined run.
- Initial failing import tests demonstrated the old Steam progress overwrite, inferred statuses, and destructive overwrite cleanup before those changes.
- Import provider requests use deterministic test mocks. These checks do not claim live provider verification.

## Earlier combined automated evidence

On 2026-09-28, all 76 tests passed in this command:

```text
python src/manage.py test api.tests.test_game_tracking_contract api.tests.test_game_tracking_entrypoints app.tests.test_game_tracking_web app.tests.test_game_tracking_review integrations.tests.test_game_tracking_contract integrations.tests.test_steam_update integrations.tests.imports.test_steam integrations.tests.imports.test_hltb integrations.tests.imports.test_yamtrack integrations.tests.test_exports --verbosity 1
```

Output: `/tmp/spine-game-contract-combined-tests.log`. No system check issues.
Case names above belong to `api.tests.test_game_tracking_contract.GameTrackingContractTests`, unless an import class is named.
These tests cover backend results. They do not prove native action routing, dismissal, rendering, or interaction.

Additional independent sequences passed in `app.tests.test_game_tracking_review.GameTrackingReviewTests`:

- `test_percentage_clear_decouples_only_percentage_and_log_clear_keeps_hours_link`
- `test_delete_direct_completion_restores_direct_paused_without_a_playthrough`
- `test_deleted_dropped_attempt_cannot_return_after_newer_attempt_is_deleted`
- `test_source_deletion_preserves_both_opinions_and_new_log_recouples_them`
- `test_backdate_preserves_later_zero_and_unknown_fields_independently`
- `test_drop_date_correction_has_start_limit_without_changing_newer_progress`

Inherited API checks passed in `api.tests.test_game_tracking_entrypoints.GameTrackingEntryPointTests`:

- `test_generic_diary_retry_is_idempotent_but_same_day_logs_are_distinct`
- `test_generic_completion_and_like_cannot_bypass_an_unfinished_playthrough`
- `test_removal_protects_history_and_keeps_custom_lists`
- `test_game_history_and_diary_mutations_are_owner_scoped`
- `test_account_privacy_and_blocks_hide_completion_history`
- `test_opinion_fields_decouple_independently_and_source_clear_is_scoped`
- `test_invalid_generic_diary_write_does_not_create_partial_tracking`
- `test_unauthenticated_mutations_are_rejected`

`GameDiaryImportContractTests.test_imported_completion_replaces_direct_planning_or_paused_state` passed after review fixed imported completion status. Existing live attempts remain protected by `test_import_keeps_independent_opinions_and_current_playthrough`.

## Independent review findings

The review covered the full contract and shared import and opinion rules.
It examined restoration history separately from displayed completion progress.

Fixed findings with passing regression tests:

- Steam overwrote playthrough hours and inferred Playing or Paused from provider activity.
- Import overwrite cleanup could delete existing game history.
- Imported title opinions bypassed an unfinished playthrough's completion composer.
- Imported completion history did not move a direct Planning or Paused state to Completed.
- Generic web form saves bypassed game completion rules.
- Explicit invalid web completion dates saved other fields instead of rejecting the mutation.
- Same-day completion selection used attempt creation order instead of completion creation order.

`GameTrackingWebTests.test_completion_edit_rejects_explicit_blank_or_invalid_date_atomically` first failed for blank and invalid dates.
After the fix, it passes for blank, invalid text, and an impossible calendar date.
`test_completion_edit_omitted_date_preserves_the_saved_calendar_date` confirms omission remains different from clearing.
`test_completion_create_rejects_missing_date_and_decimal_progress_atomically` confirms failed writes keep Playing state and create no log.

Native review found direct-log start-date clearing and hidden diary deletion failures.
The native agent added nullable date writes and a deletion retry alert with duplicate-action protection.
The review also found retry identity cleared before a failed refetch.
The native agent retains identity until refresh succeeds.
These are code review observations. Native tests and Simulator evidence remain separate.

## Final automated reruns after review fixes

- Backend: **364 tests passed**, zero failures, in 41.242 seconds. Log: `/private/tmp/game-backend-authoritative-final.log`.
- This run includes all game API/import/web/review tests, inherited book/movie/music foundations, statistics, middleware, and upgrade migration checks.
- `ruff check src`, Django system checks, `makemigrations --check --dry-run`, and whitespace checks passed. Log: `/private/tmp/game-backend-authoritative-checks.log`.
- A fresh database migrated completely with no pending model changes. Log: `/private/tmp/game-backend-authoritative-fresh-migrations.log`.
- Exact backend commands and migration details are in `game-tracking-verification.md`.
- One separate, proven baseline failure remains: `MusicPhase9ContractTests.test_ios_music_contracts_match_frozen_responses`. Existing additive response fields are absent from its frozen fixture. It also fails on the untouched base. Latest log: `/private/tmp/game-backend-authoritative-baseline.log`.
- Final clean native build and test: **451 unit tests passed**, including 17 `GameTrackingContractTests`, plus **one real local-backend UI test passed** in 87.795 seconds. Result: `/private/tmp/spine-game-ios-final-verified.xcresult`; log: `/private/tmp/spine-game-ios-final-verified.log`.
- Native recovery tests cover deletion failure/retry, an already-deleted 404 response, stable mutation identity after failed refetch, and preserved completion drafts.
- Review regressions also cover repeated Drop retaining its attempt, unchanged form ratings preserving sources, same-day completion order, precise date errors, and local calendar boundaries.

## Live fixes and verification limits

Manual interaction found independent-clear, local-date coupling, first-presentation opinion prefill, and cancelled-rating display defects. Fixes passed retests in R4/R8/A/G28 and the final native test. R14 exposed a buried save error; T confirms the fixed error is visible without scrolling and the draft remains. A dropped-history sheet briefly ignored taps in R19; reopening it restored interaction and deletion passed in R20. This did not reproduce as an app defect.

G-05 has partial native input coverage. The tool did not insert a negative or decimal value. The UI rejected 101; API and Swift tests reject negative, decimal, and out-of-range values. Future completion dates are disabled in the native picker, and tracked start dates have no clear action. API tests explicitly reject those inputs. Backend tests also cover drop-date editing and direct opinion clearing that were not repeated manually.

G-33/G-34 use the real canonical `game_tracking.import_title_state` service with deterministic Steam data (18,000 minutes) in the isolated database. The fixture script asserts the database path and refuses existing tracking/history. Native interaction then starts Playing, saves 10h, checks a repeat service import, and verifies separate 300h lifetime playtime after relaunch. Steam network fetching and all imported status variants use mocked-provider integration tests. No live external Steam fetch is claimed.

Every acceptance case has recorded verification. A native prevention control, an automated rejection, and an entered invalid value are distinguished above.
