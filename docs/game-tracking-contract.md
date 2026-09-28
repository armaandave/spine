# Game Tracking Contract

Status: **agent-ready product contract**

Last reviewed: **2026-09-26**

This contract covers video games in Spine. It does not cover board games.
Only the numbered laws below contain accepted product decisions.
Read this contract with the inherited rules named in GM-000 before implementation.

Use the book and single-weight contracts as the shared foundation:

- [Book Tracking Contract](book-tracking-contract.md)
- [Single-Weight Consumption Contract](single-weight-consumption-contract.md)

Inherit established behavior where games introduce no difference.
The game-specific laws below override conflicting book or single-weight behavior.
Ask the user only about genuine game-specific differences or unresolved conflicts.

## Inherited foundation

### GM-000: Reuse established contracts without repeating settled product questions

Books provide the status and playthrough foundation; single-weight media provide the shared diary and opinion rules.
Use game language: Playing, Planning, Paused, Dropped, Completed, playthrough, and replay.
The following rules are inherited without another approval step:

| Area | Existing rules | Application to games |
| --- | --- | --- |
| Library membership and removal | BK-002 | Any status places the game in one library section. Block simple removal while real playthroughs or logs remain. Never silently delete history or custom-list membership. |
| Plus menu | BK-100 | Show Playing, Planning, Paused, Dropped, and Log Completion. Keep base options visible. Indicate the current status and offer Resume when Paused. Keep undated Mark Completed on the eye. Apply GM-011 to Planning. |
| Direct Planning | BK-101 | Assign Planning without inventing dates, progress, a playthrough, or a log. |
| Progress-screen actions | BK-301, BK-304, BK-309 | Reuse Pause, Drop, Finish, Restart from Beginning, and Delete Playthrough for applicable Playing or Paused attempts. Apply the game laws to their results. |
| Eye rating picker | SW-011, BK-102 | An optional rating picker must not undo an undated completion when dismissed. A real playthrough still uses its completion composer under GM-206. |
| Diary fields and opinions | SW-100 through SW-107, SW-202 through SW-203, SW-300 through SW-303 | Reuse rating values, per-log opinions, source links, visible draft values, cancellation, and the same editor across entry points. Game progress follows GM-100 through GM-106 and GM-200 onward. |
| Dates and repeated logs | SW-005 through SW-007, BK-403 | Use local calendar dates. Permit distinct same-day logs. Derive last completion from the greatest completion date. Update the linked playthrough's completion date atomically when editing its log. Game date limits override book progress-date restrictions. |
| Completion counts | BK-405, BK-406 | Library sections count unique games. Each completion log counts once. The undated fact counts once for lifetime completion, never date-based totals. Dropped attempts do not count as completions. Replay badges do not change counts. |
| Privacy and activity | SW-400, SW-401 | Reuse account-level privacy and existing action-specific activity rules. A completion log creates one diary activity, not an extra rating activity. |
| Existing import infrastructure | SW-500 through SW-504 | Reuse common opinion-source rules, supplied diary values, source-record deduplication, and suppression of social activity. Game laws control replay meaning and status. GM-500 separates lifetime playtime. GM-501 prevents Steam status guesses. |
| Validation, saves, and retries | BK-500, BK-501, SW-700 | The backend enforces transitions. Related changes succeed together. Retries and double taps do not create duplicates. All screens use the same canonical state and recover from failed saves. |

Do not inherit book-only page tracking, page-to-percentage conversion, automatic completion prompts at 100 percent, or forced final progress.
Do not replace game-specific progress, post-completion, date, or deletion rules with book behavior.
Existing shared behavior needs implementation checks, not repeated product approval.

## Terms

| Term | Meaning |
| --- | --- |
| Status | The user's current tracking state for a game. |
| Playthrough | One attempt to play a game, with its status, start date, and optional progress. |
| Completion log | A diary entry recording a playthrough's completion date, rating, heart, review, and optional progress at completion. |
| Percentage | The user's estimate of progress toward finishing that playthrough. |
| Hours | The total time the user reports for that playthrough. |
| Imported lifetime playtime | A provider's total playtime for the game, separate from any individual playthrough. |
| Unknown | A value the user has not supplied, or has explicitly cleared. |

## Status and optional progress

### GM-001: Users can track games through status alone

Users can start and finish a game without entering hours or percentage.
Status actions are the primary tracking controls.
Update Progress is a secondary action.

Starting a playthrough does not invent progress.
Both progress values remain unknown until the user supplies them.

### GM-002: Each status has one library section

Games have five library sections:

- Planning;
- Playing;
- Paused;
- Dropped; and
- Completed.

Each tracked game appears in exactly one section, based on its current status.
Changing status moves the same game between sections.
Percentage and hours never determine its section.

Starting another playthrough moves a completed game to Playing.
Its earlier completion remains in history.

### GM-003: The playthrough supplies Your Progress

A playthrough is the game equivalent of a book's reading journey.
Starting to play creates it automatically; users do not need a separate creation screen.
The media page's Your Progress section shows the current playthrough's status and latest progress values.

Finishing links the completion log to the playthrough.
The playthrough remains available after completion.
Starting a new playthrough keeps its progress separate from the previous attempt.

### GM-004: Starting a game requires one tap

Selecting Playing to start a playthrough saves immediately without a setup form.
Set its start date to today in the user's local timezone.
Leave hours and percentage unknown and create no diary entry.
Users can edit the start date later if they began earlier.
Starting does not require progress values.

### GM-005: Only one playthrough can be Playing or Paused

Each user can have at most one Playing or Paused playthrough for a game.
Pausing and resuming preserve the same playthrough.
Selecting Playing while already Playing must not create a duplicate.
Selecting Playing for a paused playthrough resumes that playthrough rather than creating another.

Earlier completed and dropped attempts remain in history.
Separate simultaneous playthroughs for the same game on different platforms are outside this version's scope.

### GM-006: Dropped closes the current attempt

Dropping an existing playthrough closes that attempt and preserves its progress in history.
It creates no completion log.

Selecting Playing afterward starts a new playthrough under GM-004.
The new playthrough begins with unknown hours and percentage; it does not inherit the dropped attempt's progress.
The dropped attempt remains in history.

Pausing and resuming instead continue the same playthrough with its saved progress under GM-005.

### GM-007: Restart from Beginning preserves the previous attempt

Playing and Paused playthroughs offer Restart from Beginning as a secondary action.
Require confirmation before applying the restart.
Explain that the previous attempt and its progress will remain in history as Dropped.

On confirmation, close the previous attempt as Dropped and create a new Playing playthrough together.
Set the new start date to today in the user's local timezone.
Leave the new hours and percentage unknown.
Users do not need to drop the previous attempt themselves before restarting.

### GM-008: Users can delete an accidental playthrough

Provide Delete Playthrough as a secondary action for Playing and Paused playthroughs.
Require confirmation before deletion.
Delete only that playthrough and its progress; do not turn it into a Dropped attempt.

Restore the game's status from before that playthrough began.
If the game was previously untracked, remove it from the library.
Leave earlier playthroughs and diary logs unchanged.

### GM-009: Past Paused and Dropped states need no playthrough

Users can directly assign Paused or Dropped when no active or paused playthrough exists.
This places the game in the corresponding library section.
It creates no playthrough, diary entry, date, hours, or percentage.
It does not invent an attempt to represent activity from before the user joined Spine.

If an active or paused playthrough already exists, use its normal Pause or Drop action instead.

### GM-010: Resume starts tracking when Paused has no playthrough

If a game is Paused without a recorded playthrough, Resume starts a Playing playthrough under GM-004.
Use today's local date as the start date and leave hours and percentage unknown.
Create no diary entry.
Users can enter existing progress and change the start date if they began earlier.

If a recorded paused playthrough exists, Resume continues that same attempt with its saved progress under GM-005.

### GM-011: Planning cannot replace an unfinished playthrough

Disable Planning while a Playing or Paused playthrough exists.
Explain that the user can pause, drop, or delete an accidental playthrough instead.
A status change must not discard an unfinished attempt.

After dropping the attempt, the user can select Planning for a future attempt.
Planning is available whenever no active or paused playthrough exists.
This includes a directly assigned Paused status with no recorded playthrough.

### GM-012: Pause is immediate and Drop requires confirmation

For an existing playthrough, Pause saves immediately without confirmation.
Resume can reverse the pause while preserving the same attempt and its progress.

Before dropping an existing playthrough, ask:
"Drop this playthrough? Your progress will be kept in history."
Confirming closes the attempt under GM-006 without opening a diary form.
Cancelling the confirmation leaves the saved state unchanged.

### GM-013: Tracked drops have an editable date

Dropping a tracked playthrough records today in the user's local timezone as its drop date.
Show that date in Play History and allow users to edit it later.
The date cannot be in the future or before the playthrough's start date, if known.
Use the same date limits when Restart from Beginning closes the previous attempt as Dropped.

A directly assigned Dropped status without a playthrough remains undated under GM-009.

### GM-014: Tracked playthroughs require a valid start date

A playthrough started through Playing requires a start date, defaulted under GM-004.
Users can correct that date but cannot clear it.
The date cannot be in the future or after the attempt's first recorded progress, completion, or drop date.
Apply these limits when editing the start date.

A directly logged past completion can keep an unknown start date under GM-214.
If the user supplies a start date, apply the same date limits.

### GM-015: Restoring status must not restore deleted history

Apply this rule whenever an undo or deletion restores an earlier tracking state.
Skip a previous state when its supporting history has been deleted.
Restore the most recent valid earlier state instead.
If none remains, remove the game from the library.
Never recreate deleted history or restore Completed without remaining completion history.
Likewise, do not retain a status or progress reference whose supporting history was deleted.
Preserve an unaffected current state; otherwise select the most recent valid earlier state.

For example, a user marks Completed, starts a replay, deletes the original completion, then deletes the replay.
Do not restore the deleted completion.
Restore an earlier valid state, such as Planning, or remove the game from the library if none remains.

### GM-100: Hours and percentage are independent

A playthrough can have hours, percentage, both values, or neither value.
Users can update either value without switching a tracking mode.
Spine never converts hours into percentage or percentage into hours.

### GM-101: Updates preserve untouched values

The progress editor exposes two optional fields.
Users can change one field or both fields in the same update.
An untouched field keeps its saved value.
An explicit clear action returns that field to unknown.

### GM-102: Hours are a playthrough total

The hours field records total hours for the current playthrough.
It does not add the entered value to the previous total.
Label the field "Total hours this playthrough."
Users do not need to track individual play sessions.

### GM-103: Display only supplied progress

When both values exist, the tracking summary can show "Playing · 65% · 40h."
When one value exists, show only that value beside the status.
When neither value exists, show only the status.
Do not show a progress bar or an invented zero for unknown progress.

### GM-104: Users can correct progress in either direction

Users can increase or decrease saved hours and percentage to correct their reported progress.
Keep the same playthrough and status.
Do not restart the playthrough or change existing completion logs.

For example, changing 40 hours to 35 or 70 percent to 60 corrects the current playthrough only.
Separate updates after completion follow the field-link rules in GM-212.

### GM-105: Percentage uses whole numbers from zero to 100

Accept only whole-number percentages from 0 through 100, inclusive.
Reject negative values, decimals, and values above 100.
Blank means unknown; an explicit zero means zero percent.
Saving 100 percent does not automatically finish the playthrough, as specified in GM-200.

### GM-106: Playtime uses hours and optional minutes

Let users enter hours alone or hours with minutes, such as "12h 30m."
Use minute precision and store supplied playtime as a total number of minutes.
Blank means unknown; an explicit zero means no time played.
Reject negative playtime values.
The input remains a total for the playthrough, not additional session time, as specified in GM-102.

### GM-200: Completion is an explicit user decision

Users can finish a game at any recorded hours or percentage.
Finishing preserves those values, including unknown values, unless the user edits them in the completion form.
It does not force the percentage to 100 or invent total hours.

Saving hours or 100 percent does not automatically finish the game.
The Finish action opens the completion log composer without requiring progress.
GM-204 defines saving and cancellation. GM-219 through GM-222 define related history behavior.

### GM-201: Users define completion, including games without an ending

Completed means the user considers that playthrough finished.
Users can complete any game, including sandbox and multiplayer games without an ending.
Reaching a personal goal can qualify as completion.
Spine does not require credits, achievements, or a known ending.

Paused means the user plans to return to the playthrough.
Dropped means the user stopped without considering the playthrough finished.

### GM-202: Progress updates can continue after completion

Users can update hours and percentage on the same completed playthrough.
For example, they can record additional time spent on side quests after finishing the story.

These updates preserve Completed status, the completion date, and the completion log.
They do not create another completion or count as a replay.
Starting a new playthrough is a separate action that moves the game to Playing.

Expose Update Progress for the completed playthrough selected under GM-209.
Keep it distinct from Playing, which starts a new attempt rather than continuing the completed attempt.
The progress editor must not finish an already completed playthrough again.

### GM-203: The completion log preserves progress at completion

The completion log stores the hours and percentage recorded when the user finishes.
Unknown values remain blank.
Later playthrough progress updates do not change these saved log values.
Users can explicitly edit the log to correct a mistake.
GM-212 defines when those corrections also update playthrough progress.

For example, a log can record completion at 40 hours and 65 percent.
Later side quests can increase that playthrough's progress to 50 hours and 80 percent.
Your Progress shows the later values; the completion log keeps the original values.

### GM-204: Saving the completion log completes the playthrough

Tapping Finish opens the completion log composer without changing the saved status.
A successful save completes the playthrough and creates its diary entry in one transaction.
Both changes succeed together or neither takes effect.

Cancelling or a failed save preserves the previous status and saved progress.
Hours, percentage, rating, and review are optional.
The completion date defaults to today's date in the user's local timezone.

### GM-205: The eye can record an undated past completion

When no active or paused playthrough exists, the eye can mark a game as Completed without a log.
This records that the user completed the game before tracking it.
It moves the game to the Completed library section.
It creates no diary entry, playthrough, date, or progress values.

This action can establish only one undated completion fact for the game.
Repeated use must not create multiple historical completions.
Users must create diary logs to record separate past completions.
GM-208 defines removal. GM-209 defines reuse of earlier completion history.

If the game has only this undated completion and no playthrough, hide Update Progress.
Users can select Playing to track ongoing progress.
Users can create a completion log to record a dated completion with optional final progress.

### GM-206: The eye finishes an active or paused playthrough through its log

When an active or paused playthrough exists, tapping the eye performs the same action as Finish.
It opens that playthrough's completion log composer.
Saving completes the same playthrough and creates its log under GM-204.
Cancelling preserves the saved status and progress.
The eye cannot bypass the composer by creating an undated completion.

### GM-207: The eye reflects the current status

The eye appears filled only when the game's current status is Completed.
Starting a replay moves the game to Playing and makes the eye empty.
Earlier completions remain in history and do not keep the eye filled.

Progress updates after completion preserve Completed status under GM-202.
The eye therefore remains filled during those updates.

### GM-208: The filled eye can undo undated completion but protects logs

When completion exists only through the undated eye action, tapping the filled eye removes that completion fact.
The game returns to the status it held before that action.
If the game was previously untracked, remove it from the library.
Apply the inherited opinion cleanup in GM-304.

When a log produced the current Completed status, tapping the filled eye leaves the state unchanged.
Show the message "Delete this completion log first."
The eye never deletes a diary entry automatically.
GM-209 provides an exception for undoing a status change that reused earlier completion history.

### GM-209: The eye reuses earlier completion history

When no active or paused playthrough exists, tapping the empty eye can reuse an earlier completion.
It moves the game to Completed without creating another completion fact, playthrough, or diary entry.
It preserves all earlier history, including a later dropped replay.

Tapping the filled eye again undoes that status change and restores the previous status, subject to GM-015.
This action does not delete the earlier completion or require deletion of its log.
It cannot undo an actual new completion log under GM-208.

For example, a user completes a game, then drops a replay.
Tapping the eye moves the game from Dropped to Completed using the earlier completion.
Tapping it again restores Dropped and leaves both attempts in history.

While the current status is Completed, Your Progress shows the latest completed playthrough by completion date.
Use the stable creation-order tie-breaker from GM-218 when completion dates match.
Show that playthrough's latest saved progress, not the dropped replay's progress or the completion log's snapshot.
Further progress updates affect that completed playthrough and leave its diary snapshot unchanged under GM-202 and GM-203.
If completion history contains only the undated fact, Update Progress remains unavailable.
Recalculate this selection when completion history changes, using the same completion-date ordering.
Adding an older completion does not replace a more recently completed playthrough's displayed progress.
This selection does not change rating or heart source rules, or which status action an undo reverses.

For example, returning to Completed after dropping a replay at 5 hours can show an earlier completed playthrough's 40 hours.

### GM-210: The completion form starts with current values

The completion form prefills rating and heart from the game's current values.
It prefills hours and percentage from the playthrough being completed.
Unknown progress stays blank.

When a rating or heart action opens the form, use that selection for the corresponding field.
Keep the normal defaults for the other fields.
Users can edit all these fields before saving.

Saving records the visible values on the log, including untouched prefilled values.
Apply the rating and heart source rules in GM-303.
Cancelling saves none of the draft changes.

### GM-211: Initial completion saves progress to the log and playthrough together

Saving the new completion log applies its visible hours and percentage to the linked playthrough as well.
GM-222 provides an exception when progress was recorded after a backdated completion date.
Save both records as part of the completion transaction under GM-204.
This includes user corrections and fields explicitly cleared to unknown.

For example, correcting 40 hours to 45 in the completion form saves 45 hours to both records.
Later progress updates may change the playthrough while the log retains its completion values under GM-203.
Cancelling or a failed save changes neither record.

### GM-212: Log progress corrections follow only while the field remains linked

Treat hours and percentage separately when editing a saved completion log.
If a field has not been updated separately since completion, apply its correction to the log and linked playthrough.
Once that playthrough field has been updated separately, later log corrections change only the log's value.
Fields preserved under GM-222 are already independent when the completion log is created.
Updating hours separately does not break the percentage link, and updating percentage separately does not break the hours link.
A correction never changes a different playthrough.
Editing only a completion date does not change progress values or reconnect an independent progress field to its log.

For example, a log and its playthrough both show 40 hours after completion.
Correcting the log to 45 hours updates both if no separate hours update has occurred.
If the playthrough has since reached 50 hours, the same correction leaves its 50-hour value unchanged.

### GM-213: Completion dates use simple calendar limits

Every completion log has a date in the user's local timezone.
The new log form defaults to today.
Users can choose any date that is not in the future.
If the playthrough has a start date, completion cannot precede that date.
Apply these limits when creating or editing a completion log.

Progress-update dates do not restrict the completion date.
Users can log a past completion even after recording more playtime.
Later progress updates do not change the completion date.

### GM-214: Direct Log Completion creates a completed playthrough

When no active or paused playthrough exists, users can log a past completion directly.
They do not need to select Playing first.

Saving Log Completion creates a completed playthrough and its dated diary entry together.
Move the game to Completed.
The start date remains unknown unless the user supplies it.
Hours and percentage are optional.
Apply the completion-date limits in GM-213 and the current-opinion rules in GM-303.

Preserve earlier attempts and diary entries.
Cancelling or a failed save creates nothing and leaves the saved state unchanged.

### GM-215: Log Completion finishes an existing unfinished playthrough

When a Playing or Paused playthrough exists, Log Completion opens the same form as Finish.
Prefill progress from that playthrough under GM-210.
Saving completes that same attempt and links its new diary entry to it.
Do not create a second playthrough.
Cancelling leaves the existing attempt unchanged.

A directly assigned Paused status without a playthrough follows the direct-log flow in GM-214.

### GM-216: A replay requires an earlier completion

A playthrough counts as a replay only when the user previously completed the game.
An earlier completion log or the undated completion fact supplies that history.
A dropped attempt alone does not make the next playthrough a replay.

For example, dropping a game twice and then finishing it produces the user's first completion.
The two dropped attempts remain in history but do not count as completions.

### GM-217: The diary Replay toggle controls only the badge

Default the completion form's Replay toggle from the completion history described in GM-216.
Users can change the toggle before saving.
The saved toggle controls only the replay badge on that diary entry.

Changing the toggle does not change actual completion history, completion counts, or future replay defaults.
Keep the display choice separate from whether history establishes a replay.

### GM-218: Completion dates determine replay order

Order dated completions by their user-selected completion dates.
Use stable creation order to break ties on the same date.
Treat the undated completion fact as history earlier than dated completions.

Recalculate actual replay history when completion history changes, including logs entered out of order.
Preserve each diary entry's saved Replay badge choice during recalculation under GM-217.
This ordering does not replace the rating and heart source rules in GM-303.

For example, a user enters a 2025 completion before entering their first completion from 2023.
The 2023 completion becomes first in the actual history, and the 2025 completion becomes a replay.

### GM-219: Deleting the current completion reopens its tracked playthrough without losing progress

This rule applies when a log finished a Playing or Paused playthrough that still controls the current Completed status.
Deleting that log removes the completion and restores the playthrough's previous Playing or Paused status.
Keep its latest saved hours and percentage, including progress recorded after completion.
Do not restore older progress values merely because the completion log was deleted.

For example, a user finishes at 40 hours and later records 50 hours through side quests.
Deleting that completion log restores Playing with 50 hours when Playing was the pre-finish status.
Apply the shared source-deletion rules in GM-303 to current rating and heart.
GM-220 defines deletion of older completions. GM-221 defines deletion of direct-log playthroughs.

### GM-220: Deleting older completion history does not disturb newer tracking

When newer tracking exists, deleting an older completion removes its log and linked playthrough.
Delete that older playthrough's progress as part of the same operation.
Require confirmation that explains the removal of the older attempt's progress.

Do not reopen the older playthrough.
Preserve unaffected current status, current progress, and newer diary entries.
If the deleted attempt supplied the displayed completed progress, select from surviving completions under GM-209.
If that deletion removes the evidence for the current status, apply GM-015 instead of retaining unsupported state.
Recalculate derived replay history from the remaining completions under GM-218.
Apply current rating and heart source-deletion rules under GM-303.

### GM-221: Deleting a direct completion log removes its generated playthrough

Deleting a log created through GM-214 also deletes the playthrough created with that log.
Delete all progress belonging to that playthrough, including updates recorded after completion.
Require confirmation that explains this progress deletion.

If that completion still controls current tracking, restore the status from before the log was created.
If the game was previously untracked, remove it from the library.
If newer tracking exists, preserve it under GM-220 instead of restoring the earlier status.
Leave other playthroughs and diary entries unchanged.

### GM-222: Backdated completion preserves later progress

Apply this rule separately to hours and percentage when creating a backdated completion log.
If a field has a saved update after the selected completion date, preserve its current playthrough value.
Save the completion form's visible value on the log without replacing that later progress.
Keep the preserved playthrough field independent from later log corrections under GM-212.
If no later update exists for that field, apply GM-211 normally.

For example, a user finishes at 40 hours, then records 50 hours after playing side quests.
A later-created log for the earlier completion can record 40 hours while the playthrough keeps 50 hours.
Use the same rule for percentage.

## Rating and heart controls

### GM-300: Rating and heart actions follow book completion rules

Setting a rating or turning the heart on implies completion, as it does for books.

With an active or paused playthrough, either action opens that playthrough's completion log composer.
Prefill the selected rating or heart in the composer.
Saving completes the same playthrough, creates its log, and applies the submitted opinion together under GM-204.
Cancelling or a failed save changes nothing.

Without an active or paused playthrough, move the game to Completed and apply the rating or heart.
Reuse existing completion history when available; otherwise establish the undated completion fact under GM-205.
Do not create a new diary entry, playthrough, date, or progress through this direct action.
When the action only reuses earlier history, the eye can undo that status change under GM-209.

### GM-301: Removing an opinion does not change status

Turning the current heart off or clearing the current rating preserves the game's status and progress.
These actions do not remove completion history or change historical diary ratings and hearts.

### GM-302: Optional rating-picker dismissal preserves a direct heart action

Without an active or paused playthrough, turning the heart on may show the existing optional rating picker.
Dismissing that picker preserves the saved Completed status and heart.
This differs from cancelling a completion composer under GM-300, which saves nothing.

### GM-303: Current and diary opinions follow the shared source rules

Reuse the rating and heart source rules from the single-weight contract, as books do.
Apply SW-100 through SW-107 and SW-202 through SW-203 to those values and their sources.
Game completion and status transitions still follow this game contract.

- Each diary entry keeps its own optional rating and its own heart state.
- A newly created rated log sets the current rating and becomes its source, including when backdated.
- A new unrated log preserves the current rating and its source.
- A new log sets the current heart on or off and becomes its source.
- Editing a source log updates the corresponding current value.
- Clearing the source log's rating also clears the current rating.
- Editing a log that is not the source leaves the corresponding current value unchanged.
- Directly changing or clearing a current value removes its link to older logs without changing those logs.
- Rating and heart have separate source links. Changing one does not remove the other's link.
- A later rated new log links the current rating again. Every later new log links the current heart again.

Source-log deletion follows the shared rules when tracking remains.
It preserves the current value as independent rather than selecting an older log's value.
Apply GM-015 when restoring earlier tracking states.
Apply GM-304 when tracking is removed.

### GM-304: Tracking removal clears current opinions, not custom lists

When the game becomes completely untracked, clear its current rating, current heart, and both source links.
Keep independent custom-list membership unchanged.
Deleting one log while other tracking remains does not trigger this cleanup; apply GM-303 instead.

Removing undated completion through the eye also follows BK-102 and SW-008.
Clear the current rating, heart, and source links even when restoring an earlier non-completed library status.
This does not delete other playthroughs or diary entries.

## Play History

### GM-400: Play History shows dropped attempts and undated completion

The game's media page includes a Play History section for dropped attempts and the undated completion fact.
Show each dropped attempt's dates and final progress when known.
Show undated completion as "Completed — date unknown."
Do not invent missing dates or progress.

Completed playthroughs remain represented by their diary entries.
Dropped attempts create no diary entry or social activity.

### GM-401: Users can delete a dropped attempt from Play History

Allow deletion of a dropped playthrough from Play History.
Require confirmation that the attempt and its progress will be deleted.

If the attempt controls the current Dropped status, restore the status from before that attempt began.
If newer tracking exists, leave the current status and progress unchanged.
Leave other playthroughs and diary entries untouched.

### GM-402: Users can remove undated completion from Play History

Allow users to delete "Completed — date unknown" even after another playthrough has started.
Remove only the undated completion fact.
Preserve the current playthrough and diary entries.
Recalculate actual replay history under GM-218 without changing saved Replay badge choices.

If the undated fact alone controls the current Completed status, apply the eye's undo behavior in GM-208.

### GM-403: Users can correct a dropped attempt's progress

Allow users to edit or clear a dropped attempt's hours and percentage from Play History.
Apply the same progress validation used elsewhere.
Keep the attempt Dropped; a correction does not reopen it.
Preserve the game's current status and any newer playthrough's progress.

## Game-specific imports

### GM-500: Imported lifetime playtime stays separate from playthrough hours

Keep imported lifetime playtime as separate provider data for the user's game.
It is not the hours total of an individual playthrough.
Importing or refreshing that total must not replace a playthrough's saved hours or completion log's hours.
Do not split a lifetime total into invented playthroughs or completions.

For example, importing 300 lifetime hours from Steam leaves a replay's recorded 10 hours unchanged.
Reimporting refreshes the separate provider total; it does not add that total to a playthrough.

### GM-501: Steam imports do not infer the user's tracking status

Do not select Playing or Paused from Steam's total or recent playtime.
Preserve an already tracked game's current status when importing or refreshing Steam data.
Place a newly imported, previously untracked game in Planning until the user chooses another status.
This creates no playthrough, completion log, or invented start date.

Inactivity on Steam is not a user instruction to pause or drop a game.
Keep imported lifetime playtime separate under GM-500 regardless of the current status.
These Steam rules do not replace the shared handling of statuses explicitly supplied by other import sources.

## Acceptance scenarios

These scenarios check the numbered laws; they do not introduce separate product decisions.
Run shared book and single-weight regression cases alongside these game cases.

| Case | Action | Required result | Laws |
| --- | --- | --- | --- |
| G-01 | Start Playing without progress, then finish and save a log without progress. | One completed playthrough and one log. Both progress fields remain unknown. | GM-001, GM-004, GM-204 |
| G-02 | Save hours only, then percentage only. | Preserve the untouched field. Store both values on the same attempt. | GM-100, GM-101 |
| G-03 | Clear one progress field; later enter zero. | Clear means unknown. Explicit zero remains a supplied value. Preserve the other field. | GM-101, GM-105, GM-106 |
| G-04 | Lower hours or percentage to correct an error. | Keep the same attempt and status. Do not alter an existing completion log. | GM-104 |
| G-05 | Enter invalid percentage or negative playtime. | Reject invalid input without partial changes. Accept whole percentages from 0 through 100. | GM-000, GM-105, GM-106 |
| G-06 | Enter 12h 30m, then replace it with 13h. | Store totals of 750, then 780 minutes, not their sum. | GM-102, GM-106 |
| G-07 | Save 100 percent while Playing. | Stay Playing. Do not create a log or automatically open a completion composer. | GM-000, GM-200 |
| G-08 | Finish at 65 percent and 40 hours. | Complete through one saved log. Preserve those values; do not force 100 percent. | GM-200, GM-204 |
| G-09 | Open Finish from Playing or Paused, then cancel or fail saving. | Preserve the original state and saved progress. Create no log. | GM-204 |
| G-10 | Pause and resume an actual attempt. | Use the same attempt, start date, and saved progress. | GM-005, GM-012 |
| G-11 | Assign retrospective Paused, then Resume. | The direct status has no invented attempt or dates. Resume starts one attempt with unknown progress. | GM-009, GM-010 |
| G-12 | Drop an attempt, then select Playing. | Preserve the dropped attempt. Start a new attempt with unknown progress. No completion is implied. | GM-006, GM-013 |
| G-13 | Restart a Playing or Paused attempt. | Confirm first. Atomically retain the old attempt as Dropped and start a new one. | GM-007 |
| G-14 | Delete an accidental attempt after deleting its prior completion evidence. | Never restore deleted evidence. Restore a valid earlier state or remove tracking. | GM-008, GM-015 |
| G-15 | Edit or clear a dropped attempt's progress. | Keep that attempt Dropped. Do not affect a newer attempt or current library status. | GM-403 |
| G-16 | Continue a completed playthrough from 40 hours and 65 percent to 50 hours and 80 percent. | Keep Completed. Update Your Progress; preserve the log's original 40 hours and 65 percent. | GM-202, GM-203 |
| G-17 | Correct hours in the initial completion composer when no later progress exists. | Save the correction to both the log and linked playthrough. | GM-211 |
| G-18 | Correct a log's hours before and after separately updating playthrough hours. | Update both while linked. After a separate update, change the log only. Percentage has its own link. | GM-212 |
| G-19 | Backdate completion to before an existing later progress update. | Save historical progress on the log. Preserve later playthrough values field by field. | GM-213, GM-222 |
| G-20 | Change only the date of a log whose hours are independent. | Update the linked completion date. Preserve both progress values and the independent link state. | GM-000, GM-212, GM-213 |
| G-21 | Add an older completion while a more recently completed attempt has progress. | Keep Your Progress on the latest completion by date. Apply normal new-log opinion-source rules separately. | GM-209, GM-218, GM-303 |
| G-22 | Delete the log that currently completes a tracked attempt after recording side-quest progress. | Reopen its prior Playing or Paused state with latest progress, not the pre-finish values. | GM-219 |
| G-23 | Delete a direct-log completion after recording later progress on that generated attempt. | Confirm and delete that log, generated attempt, and its progress. Restore valid prior tracking when applicable. | GM-015, GM-221 |
| G-24 | Delete an older completion while a newer attempt is Playing. | Remove only the older log and linked attempt. Preserve the newer attempt and its progress. | GM-220 |
| G-25 | Return from a dropped replay to Completed using the eye. | Show the latest completed attempt's progress, not the dropped replay's progress. A second eye tap restores valid prior status. | GM-209 |
| G-26 | Delete the older completion currently selected by that status-only return. | Do not retain a deleted progress target. Select surviving completion history or restore a valid earlier state. | GM-015, GM-209, GM-220 |
| G-27 | Mark Completed without a playthrough, then dismiss the optional rating picker. | Keep undated completion. Create no log, date, or progress. Hide Update Progress. | GM-000, GM-205 |
| G-28 | Heart or rate an actual Playing or Paused attempt. | Open its completion composer with that choice. Save all effects together, or preserve all prior state on cancellation. | GM-300 |
| G-29 | Hide a replay badge, or insert a backdated first completion. | Recalculate actual chronology and counts without overwriting saved badge choices. Dropped attempts never count as completions. | GM-216 through GM-218 |
| G-30 | Remove all tracking or undo undated completion through the eye. | Apply current-opinion cleanup. Preserve independent custom lists and protected history. | GM-000, GM-304 |
| G-31 | Set a future completion date, or one before the known start date. | Reject it. Later progress does not prevent a valid earlier completion date. | GM-213 |
| G-32 | Clear a tracked start date, or move it after the first progress, completion, or drop date. | Reject the edit. Direct completion logs may retain an unknown start date. | GM-014 |
| G-33 | Import 300 Steam lifetime hours while a replay has 10 hours. | Keep the replay at 10 hours. Store the provider total separately. A repeat import does not add hours. | GM-500 |
| G-34 | Import Steam data for a tracked game, then a previously untracked game. | Preserve the tracked status. Put the new game in Planning, with no invented attempt or start date. | GM-501 |
| G-35 | Retry a save, double tap, or encounter a failed compound mutation. | Avoid duplicate attempts and logs. Apply all related changes or none. Reconcile every screen to canonical state. | GM-000, GM-204 |

Implementation must also retain the inherited rating, heart, privacy, activity, import-deduplication, and same-day-log tests.
Check the media page, progress editor, diary editor, library sections, and profile surfaces for consistent results.

## Review status

The product interview is complete for this scope.
Shared behavior is inherited under GM-000; game-specific decisions and their acceptance cases are recorded above.
No known product questions remain from this review.
Implementation planning must map these laws to the actual backend and iOS code without silently changing them.

This document does not claim that game implementation or runtime testing is complete.
No application code was changed during this contract review.

## Change policy

Add a new game-specific rule only after the user accepts it.
Record inherited shared rules directly under GM-000 without requesting repeated approval.
Keep unresolved questions separate from accepted rules.
Do not change application code during this interview.
