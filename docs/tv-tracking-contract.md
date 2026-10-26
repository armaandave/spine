# TV Tracking Contract

Status: **agent-ready product contract**

Last reviewed: **2026-10-26**

This contract covers TV shows, their seasons and their episodes in Spine.
It does not cover anime, which Spine tracks as its own media type.
Only the numbered laws below contain accepted product decisions.
Read this contract with the inherited rules named in TV-000 before implementation.

Use the single-weight, book and game contracts as the shared foundation:

- [Single-Weight Consumption Contract](single-weight-consumption-contract.md)
- [Book Tracking Contract](book-tracking-contract.md)
- [Game Tracking Contract](game-tracking-contract.md)
- [Activity Contract](activity-contract.md), for what activity shows (TV-703)

Inherit established behavior where TV introduces no difference.
The TV-specific laws below override conflicting single-weight, book or game behavior.
Ask the user only about genuine TV-specific differences or unresolved conflicts.

## Inherited foundation

### TV-000: Reuse established contracts without repeating settled product questions

TV is the first media type with tracked parts inside a tracked whole.
Map the existing contracts onto its three levels:

- the show is the library card, with the status sections that books and games use;
- the show and each season behave like a single-weight title for marks, stars, hearts and logs; and
- an episode is what the user watches: a tick is dated progress, and the episode's heart follows the movie rule.

Use TV language: Watching, Planning, Paused, Dropped, Completed, tick, log, mark watched, up next, caught up, and rewatch.
Watching is the display name for the shared In progress status; the backend keeps "In progress" as the stored and wire value (TV-900).
The following rules are inherited without another approval step:

| Area | Existing rules | Application to TV |
| --- | --- | --- |
| Undated marks with the show and season eyes | SW-001, SW-004, SW-008, SW-011, BK-102 (not its live-journey paragraph), BK-103 (its rule that the eye shows the current state, not lifetime history) | The show and season eyes mark every aired regular episode watched with no date, no log and no feed item, leaving the worked-out status (TV-300, TV-207). An eye is filled while something under it has aired and all of that is watched in the current pass, which overrides BK-103's "filled exactly when Completed". The filled eye never deletes a log and says which log to delete first (TV-302). The episode eye is a tick instead (TV-100). |
| Logs imply tracking; dates; repeated logs | SW-002 and SW-003 (with the worked-out status in place of "completed"), SW-005 (extended to tick dates), SW-006 (a claim defaults to its tick's day, TV-404), SW-007 | Every log at every level puts the show in the library and counts its target as watched (TV-400 to TV-402). Log dates are calendar dates on the user's calendar, required, and never in the future (TV-407). Same-day logs of the same show, season or episode are valid. The last-watched date of an episode, season or show is the latest calendar day among its dated viewings and logs; fills and marks never supply one. |
| Deleting logs, and removal | SW-009, SW-012, BK-002 (except that a real journey blocks removal), GM-304 | Deleting a log removes only its own viewing and fills (TV-406). The show leaves the library only when nothing under TV-003 remains, and only logs block Remove from Library. Removing tracking, or an undo that leaves nothing recorded, clears current stars and hearts and keeps custom-list membership (TV-208). |
| Restoring earlier states | GM-015, BK-102, BK-404 | Undo and deletion never bring back deleted history. They restore an earlier Planning, Watching, Paused or Dropped choice only under TV-208. |
| Show and season stars and hearts | SW-100 to SW-108, SW-200 to SW-203, BK-200 (its picker and unhearting sentences only), BK-202, GM-301, GM-303 | The show and each season have their own current stars and heart, with the shared source rules (TV-501). Stars or a heart mark an unstarted show watched (TV-303) and are opinion only on a started one (TV-304). Clearing stars or turning a heart off never changes status, viewings or marks. |
| Rewatch defaults | SW-010 (except that a claimed tick defaults off, TV-404), GM-217 | The server works out the composer's Rewatch default when it opens; the user can change it before saving (TV-404). On a season or show log the saved toggle controls only the badge. On an episode log it also decides whether the log claims a tick (TV-402). |
| Saving, cancelling, one editor | SW-300 to SW-303 | A write, its fills and the worked-out state succeed together or not at all. Cancelling a composer saves nothing, including "Not now" on the season-finish composer (TV-403). The visible draft is what saves. Every surface opens the same editor. |
| The + menu and statuses | BK-001 (its sections and single current status; TV-700 sets the card lines), BK-100, BK-101, GM-009, GM-011 (its first paragraph), GM-012 | The show's + menu holds the status actions, with base options always visible and unavailable ones explained (TV-005). Planning, Paused and Dropped can be chosen without inventing dates or viewings. Pause saves at once; Drop asks first (TV-007). Planning follows TV-006, which is stricter than GM-011. |
| Pause and Drop keep progress | BK-303, BK-305 (their progress-keeping rules only) | Pausing or dropping keeps every viewing, log and mark. Coming back continues from the same place (TV-007). |
| Imports | SW-500 to SW-504 (an imported rating or like follows TV-303 and TV-304, not SW-501's direct consumption), GM-501 (its rule against inferring status from inactivity) | Imports use the same state model and create no feed activity. Spine never infers Paused or Dropped from provider inactivity. TV-800 to TV-804 add the TV rules. |
| Consistency | BK-500, BK-501, SW-700 | The backend decides every transition. Retries and double taps never duplicate viewings or marks, and logs from the new build never duplicate (TV-705). Every surface shows the same state after a write. |
| Privacy and activity | SW-400 (as the target; this contract doesn't change TV visibility), SW-401 | Activity follows [the activity contract](activity-contract.md): logs, a day's ticks as progress on the show, and starting the show (TV-703). Moving TV logs from per-entry visibility to account-level visibility is outside this contract (Scope boundary). |

Do not inherit:

- **Stored attempts.** Book journeys and game playthroughs, with their start dates, drop dates, Restart, attempt deletion and History screens (BK-104's start date, BK-300 to BK-310, GM-003 to GM-008, GM-010, GM-013, GM-014, GM-400 to GM-403). The progress-keeping rules of BK-303 and BK-305 are the exception, inherited above. For TV, viewings are the history, and a rewatch is a pass rather than an attempt.
- **Hand-entered progress.** Pages, percentages, hours, and progress snapshots on logs (GM-100 to GM-106, GM-203, GM-211, GM-212, GM-222, BK-400's final progress). TV progress is counted from viewings, never typed.
- **"Final progress alone never completes" and "finishing requires the log"** (BK-302, BK-400, BK-401, GM-200, GM-204). A watched episode is a counted fact, so watching every aired episode finishes a season. The log is offered (TV-403), not required.
- **The finish form during a live attempt.** The eye, a heart or stars on a started show do not open a composer (BK-102's live-journey paragraph, BK-201, BK-203, GM-206, GM-300). On a started show the eye marks the rest watched (TV-300), and stars and a heart are opinion only (TV-304).
- **BK-103's other eye rules:** "filled exactly when Completed", the eye moving a book to Read from DNF, and the status-only undo. The TV eye follows what's watched in the current pass (TV-300), and Watching or Resume returns a finished show to Completed (TV-007).
- **Direct consumption from opinions on a started show.** Stars or a heart on a started show, or on any of its seasons, establish no mark (the direct-consumption parts of SW-105, SW-200 and SW-203).
- **An undated episode eye** (SW-001 and SW-004 at the episode level). A tick is dated progress. The episode heart still marks an unwatched episode undated (TV-305).
- **A current rating for episodes** (SW-100 to SW-108 at the episode level). TV-502 defines episode stars.
- **A source log for an episode's heart** (SW-202's source link and SW-203 at the episode level). TV-502 defines the episode heart.
- **Replay derivation from completion chronology** (BK-406, GM-216, GM-218). TV-404 defines the Rewatch default.
- **Planning after Completed or Dropped** (GM-011's last paragraph, the last paragraph of BK-104). TV-006 allows Planning only before anything is watched.
- **Season-level Pause, Drop and Planning.** They are choices about the show (TV-204).

Existing shared behavior needs implementation checks, not repeated product approval.

## Terms

| Term | Meaning |
| --- | --- |
| Show | A TV series. The only TV library card. |
| Season | A numbered season of a show. Season 0 is Specials. |
| Regular episode | An episode in a season numbered 1 or higher. |
| Specials | The episodes of season 0. They are extras (TV-004). |
| Show order | Season number, then episode number, over regular episodes. |
| User's calendar | The timezone the app last sent. Spine stores it on the account and uses it on every path: the app, the web, webhooks, imports, scheduled jobs and the repair. Until the app has sent one, Spine uses the server's timezone. |
| Aired | An episode that counts as aired under TV-103. |
| Viewing | One watching of one episode, dated or undated. An episode can have many. |
| Tick | Tapping an episode watched. It records one viewing at the moment of the tap, which also gives it a day on the user's calendar. Webhook watches and imported plays also count as ticks, with their own dates or none. |
| Log | A diary entry about one episode, one season or the whole show, on a calendar date, with optional stars, heart, review and Rewatch badge. |
| Cover | An episode log covers its episode. A season log covers every episode of its season, and a show log every regular episode, that had aired when it was saved (TV-400). |
| Mark | Marking watched without a log or a date: the show eye, a season eye, Mark watched up to here, a Yes to the earlier-episodes question, or stars or a heart that mark (TV-303, TV-305). Each mark is one action, and it ends when its last fill is removed (TV-301). |
| Fill | An undated viewing that a log or mark adds for an aired episode that wasn't watched. It belongs to the log or mark that made it (TV-301). |
| Unlogged | A viewing that belongs to no log: a tick, or a mark's fill. |
| Claim | An episode log taking an existing tick as its own viewing instead of adding one (TV-402). |
| Rewatch | A new time through a finished show or season (TV-600). |
| Current pass | The viewings that count now: those recorded since the rewatch in progress began, or every viewing when no rewatch is in progress (TV-601). A log is in the current pass when it was saved after that rewatch began, or always when none is in progress. |
| Watched | An episode is watched when it has a viewing in the current pass. |
| Finished | A season under TV-200, or a show under TV-201. Finished is worked out; Completed is the show's status. |
| Started | A show is started when it has a viewing, log or mark, specials included, or when you chose Watching for it. Planning, Paused or Dropped chosen with nothing watched don't start it. A season is started when it has a viewing, log or mark. |
| Choice | Planning, Watching, Paused or Dropped, chosen from the show's + menu. A Watching choice only keeps a show with nothing watched in Watching. |
| Up next | The next episode to watch, under TV-105. |
| Caught up | Every aired regular episode is watched while more are coming (TV-202). |

## Library unit

### TV-001: The show is the library unit

The show is the only TV card in the library.
Each tracked show appears once, in the section for its current status.
Seasons and episodes live inside the show and never become library cards.

Seasons keep their own tracking, stars, heart and logs.
They appear as cards on the show page, as logs in the Diary, and on Home through the show's up-next episode.
Marking a show watched adds the show once and marks its seasons inside it.

Library section counts count unique shows, not seasons, episodes or viewings.

### TV-002: TV has five library sections

TV shows have five library sections:

- Watching;
- Planning;
- Paused;
- Dropped; and
- Completed.

Status alone decides the section; progress, stars and logs never do.
Changing status moves the same card between sections.
Sections are by status, not by level: there is no section for seasons.

### TV-003: Membership follows tracking

A show is in the library while it has at least one of these:

- a viewing of any of its episodes, including specials;
- a log of the show, a season or an episode;
- a mark that hasn't ended (TV-301); or
- a Planning, Watching, Paused or Dropped choice.

Ticking, logging, marking, choosing a status, and stars or hearts that mark all put the show in the library.

Remove from Library is on the show's + menu while the show is in the library.
It asks first and says what it removes, for example "Remove this show? Your 13 watched episodes will be unmarked."
It is unavailable while any log of the show, a season or an episode exists, with the reason "Delete this show's logs first."
Confirming removes every viewing, mark and choice under the show.
It clears the current stars and hearts of the show, its seasons and its episodes, and keeps custom-list membership.
Every entry point enforces the same guard, including the API's tracking delete for shows (SW-012); TV-207 covers the delete for seasons.

### TV-004: Specials never count

Specials are listed last on the show page, as "Specials".
Users can tick, log, rate and heart them.
They never count toward progress, up next, finishing a season or the show, caught up, the earlier-episodes question, or what the show eye marks or unmarks.
A specials tick, log or mark puts the show in the library, starts it and ends a choice, but changes no count, up next, finished state or caught up.

A specials log fills the aired specials under TV-401.
The Specials eye marks the aired specials watched as one mark, and its filled eye follows TV-302.
Specials can read "Watched", which affects nothing else, and finishing them never opens the season-finish composer.
A show whose only history is specials is in Watching, with the first aired regular episode in show order up next.

### TV-005: The + menu on each page holds that level's actions

The show's + menu offers:

- Watching, plus Resume when the show is Paused;
- Planning;
- Pause;
- Drop;
- Rewatch when the show is finished, or Stop rewatching during a rewatch;
- Log, for a season or the whole show; and
- Remove from Library, while the show is in the library.

A season's + menu offers Log Season, and Rewatch Season once that season is finished.
An episode's + menu offers Log Episode.

Base options stay visible whatever the status.
Remove from Library isn't one: a show that isn't in the library has nothing to remove, so the menu leaves it out, and the server sends neither the action nor a reason for it.
The current status shows as selected rather than as a second action.
An unavailable option says why; the server sends the available actions and their reasons with the state.

Choosing Watching for a show with nothing watched saves in one tap without a setup form.
It puts the show in Watching with the first aired regular episode in show order up next.
It records no date and no viewing, and creates no season tracking.
Choosing Watching for a Paused or Dropped show continues it under TV-007.
Watching is unavailable while the show is finished and isn't Paused or Dropped, with the reason "You've watched everything that has aired."
It is also unavailable before any regular episode has aired, with the reason "Nothing has aired yet."

### TV-006: Planning is only for shows you haven't watched

Planning is available only while the show has no viewing, log or mark, specials included.
Otherwise it is unavailable, with the reason "You've started this show. Pause or drop it instead."
Choosing Planning records no date and no viewing, and creates no season tracking.
A Planning show leaves Planning when anything under it is ticked, logged or marked (TV-203).

### TV-007: Pause is immediate and Drop asks first

Pause saves at once without confirmation.
Before dropping, ask: "Drop this show? Your progress will be kept."
Confirming sets Dropped; cancelling leaves the saved state unchanged.

Both keep every viewing, log, mark, star and heart.
Both apply to the whole show (TV-204).
Both can be chosen for a show with nothing watched, without inventing a date or a viewing (GM-009).
A Paused or Dropped show leaves Continue Watching and gets no calendar events or notifications (TV-704).

Resume, or choosing Watching, continues where the user was.
It keeps the same viewings, the same up next and any rewatch in progress.
Ticking, logging or marking anything also ends Paused or Dropped (TV-203).
A show that is finished returns to Completed instead of Watching.

## Viewings and progress

### TV-100: A tick records one viewing, dated now

Ticking an aired episode records one viewing of it, at the moment of the tick.
A tick stores that moment and its day on the user's calendar.
Users can tick on the episode page, in the season's episode list, on the show page's Up next row, and from a Continue Watching card's touch-and-hold menu (TV-701).
Unaired episodes show their air date in place of a tick, or "TBA" when they have none.

A tick creates no log. Activity shows the day's ticks as progress on the show (TV-703).
It puts the show in the library, ends a Planning, Paused or Dropped choice, and leaves the show in its worked-out status: Completed when finished (TV-201), otherwise Watching.
A tick's date is never taken from a preference or the air date.

### TV-101: Ticking a watched episode does nothing

If the episode already has a viewing in the current pass, a tick records nothing and returns the current state.
Double taps and retries never add a second viewing.
This applies to taps and webhooks; imported plays follow TV-800.
To record watching an episode again, log it with Rewatch on (TV-402, TV-603).

### TV-102: Unticking removes your most recent unlogged viewing

Tapping a filled episode tick removes the most recently recorded unlogged viewing of that episode in the current pass: a tick, or a mark's fill.
Recorded means when Spine saved the viewing, never its date.
An untick from the app names the viewing it removes, taken from the episode's watched state; if that viewing is gone, nothing changes.

Change nothing, and say which log to delete first, when:

- every viewing of the episode in the current pass belongs to a log; or
- a season or show log in the current pass covers the episode (TV-400).

Use "Delete your log of this episode first." for an episode log, "Delete your Season 1 log first." for a season log, and "Delete your show log first." for a show log.

Unticking never deletes a log and never removes a viewing from outside the current pass.
After an untick, work out the season and show again; an untick can reopen a finished season and show.
When an untick removes the episode's last viewing in every pass and the episode has no log, its heart is cleared (SW-008).
TV-208 restores an earlier choice when the untick removes the only thing recorded since that choice ended.

### TV-103: Only aired regular episodes count

Progress, up next, finishing and caught up count only aired regular episodes from the provider's episode list for each season.
An episode counts as aired when any of these holds:

- its air date is today or earlier on the user's calendar;
- the user has a viewing of it;
- it has no air date and a later regular episode's air date is today or earlier; or
- the show's source gives no air date for any episode, as with manual entries; then every listed episode counts as aired.

Spine never derives counts from the show's total episode count, the last episode's number, or calendar events.
Seasons not numbered from 1 and episode numbers with gaps count correctly, because Spine counts listed episodes.

Spine asks the provider only for seasons it currently lists.
A tracked season or episode the provider no longer lists keeps its viewings, logs, stars and heart, and counts toward nothing.
Such a season is shown after Specials under its stored number, and such an episode last in its season.

Spine keeps a stored copy of each season's listed episode numbers and air dates, updated whenever it fetches them, and uses it when the provider can't be reached.
A write that needs a season Spine has no copy of is rejected with a retryable "TV details are unavailable right now" error and changes nothing.
Surfaces show no count for such a season rather than an invented 0.

### TV-104: Progress means one thing everywhere

Progress is the number of distinct aired regular episodes watched in the current pass, out of the number of aired regular episodes.
The same definition applies to a season and to the show, on every surface: Library, the show and season pages, season cards, Continue Watching, Profile and the API's progress fields.
Fills count as watched.
Specials, repeat viewings and unaired episodes do not count.
Progress is a count, never an episode number.

For example, watching E1 to E3 and E5 of a ten-episode season shows "4 of 10", not "5".

### TV-105: Up next is the next episode to watch

Up next is the first aired regular episode in show order that comes after the user's latest watched episode and is not watched in the current pass.

- The latest watched episode is the one from the most recent regular tick or episode log in the current pass: by calendar day, then by when it was recorded, then the later episode in show order. Undated ticks count as older than dated ones.
- When the pass has no regular tick or episode log, it is the last episode in show order that is watched in the pass, so logging a season moves up next into the next season.
- When nothing after it is unwatched, up next is the first unwatched aired regular episode in show order.
- When every aired regular episode is watched, there is no up next; the show is finished or caught up (TV-201, TV-202).
- With nothing watched, up next is the first aired regular episode in show order.
- With no aired regular episode, there is no up next, and the show isn't on Continue Watching.

Moving up next into a new season creates nothing for that season (TV-205).

The show page shows up next as a row of its season's episode list, with the same still, number, title, air date and tick.
Above it, "Up next · Season 2" names the season, or "New episode · Season 3" when up next aired after the user's latest viewing of the show.
Tapping the row opens the episode's page.

For example, ticking S1 E3 moves up next to S1 E4.
Skipping S1 E4 and ticking E5 to E10 moves up next to S2 E1, and the Season 1 card reads "9 of 10 · E4 not watched".
Starting at S3 E1 and declining the earlier-episodes question moves up next to S3 E2; after the last aired episode, up next becomes S1 E1.

### TV-106: Spine rejects episodes that don't exist or haven't aired

Reject a tick, log or mark of an episode the provider doesn't list for that season.
Reject a tick or episode log of an episode whose air date is more than one day after today on the user's calendar; the one day covers time zones.
Reject a tick or episode log of an episode with no air date unless it counts as aired under TV-103's third or fourth bullet.
Reject a season or show log when that season or show has no aired episode.
Logs and marks fill only aired episodes.
Reject a season write that names no season number as an invalid request.

A rejected write changes nothing and explains why.

### TV-107: Starting partway asks once about earlier episodes

This law applies to the show's first write that records watching a regular episode.
That write can be a tick, a log, a season mark, or stars or a heart that mark (TV-303, TV-305).
If aired regular episodes before its target in show order are unwatched, the write's response asks once:
"Also mark S1 E1–S2 E10 watched?"
When the range is whole seasons, the question can name them: "Also mark Seasons 1–2 watched?"

- Yes names the range it showed and marks the episodes in it that are still unwatched and aired, undated, as one mark.
- No, or dismissing the question, marks nothing.

The write itself saves before the question appears and stays saved whatever the answer.
That first write uses the question up, answered or not, whatever its entry point; Spine never asks again for that show unless it leaves the library and starts fresh.
Webhooks, imports and installed builds don't show the question, so when one of them makes the first write, nothing earlier is marked.
Later gaps are filled with Mark watched up to here (TV-108) or a season log (TV-401).

Ask this before offering a Log Season composer for the same write.
Then, if the season of the tick or episode log is finished, open its composer (TV-403), whatever the answer; seasons that only the Yes finished get no composer.
The response to Yes names the season to offer, if any.
No and dismissing send nothing, and the app offers the season the first write's response named, if any.

For example, ticking S3 E1 as the first thing on a show asks "Also mark S1 E1–S2 E10 watched?"
Logging Season 3 first asks "Also mark Seasons 1–2 watched?"
Ticking S1 E10 first and answering Yes fills E1 to E9 and opens the Season 1 composer.
Ticking S1 E1, E2 and E5 asks nothing; the season card shows the gap.

### TV-108: Mark watched up to here

Long-pressing an aired regular episode in a season's episode list offers "Mark watched up to here".
It confirms with a native dialog that names the range, such as "Mark S1 E1–S2 E4 watched?"
Confirming marks that episode and every unwatched aired regular episode before it in show order watched, undated, as one mark.
Episodes already watched keep their viewings.

It is not offered on specials or unaired episodes.
It creates no log and no feed item, and never opens the Log Season composer.

## Derived status

### TV-200: A season is finished when you've watched all of it

A season is finished when all of these hold:

- it has at least one aired episode;
- every episode the provider lists for it has aired, and the provider doesn't name an episode of this season as next to air; and
- every one of them is watched in the current pass.

Watching only the finale never finishes a season.
A season with episodes still to air is not finished, even when everything aired is watched.

Season cards and season pages read:

- "Watched" when the season is finished;
- "Airing" when it has aired episodes, every one of them is watched, and more are still to air;
- "N of M" while the season is started and not finished, with M the aired count; and
- "9 of 10 · E4 not watched" when the last aired episode is watched but earlier ones aren't, naming the first gap.

The stored season status follows from this: Completed when finished; otherwise In progress, or the show's Paused or Dropped under TV-204.
A season that isn't started is reported as untracked (TV-205).

### TV-201: A show is finished when every season that has started airing is finished

The show is finished when it has at least one aired regular episode and every regular season with an aired episode is finished.
Seasons that haven't started airing don't count.
Earlier unwatched seasons keep a show unfinished, however the later seasons were watched.

A finished show is Completed unless the user has chosen Paused or Dropped since (TV-203).
A finished show leaves Continue Watching.

### TV-202: Caught up shows when more is coming

A Completed show is caught up when the provider lists a regular episode that hasn't aired, names a next episode to air, or reports the show as returning.
Its line reads "Caught up · S3 on Mar 12" when the provider has the next regular episode's date, and "Caught up" when it doesn't.
It moves back to Watching by itself when that episode airs (TV-206).

A Watching show is caught up when every aired regular episode is watched but a season is still airing.
Its line reads "Caught up · E7 on Thu", naming the next episode with its weekday when it airs within six days and its date otherwise, and "Caught up" when that episode has no date.
It stays in Watching, and it leaves Continue Watching until that episode airs.

The next episode is the first unaired regular episode in the season lists, or the provider's next episode to air when the lists don't include it yet; its date comes from the same place.
Caught up is worked out, never chosen.
A caught-up show's filled eye follows TV-302; its card line says when the next episode airs.

### TV-203: Your Planning, Paused or Dropped choice stays until you act

Planning, Paused and Dropped are the user's choices about the show; Spine never sets or clears them by itself.
A choice stays until the user's next tick, log, mark or status action on the show, or a webhook play (TV-802).
New episodes airing, provider refreshes, calendar jobs, and stars or hearts on a started show never end it.

When a tick, log or mark ends a choice, the show moves to Watching, or to Completed when it is finished.

### TV-204: Seasons show the show's Paused or Dropped

Seasons have no status actions of their own.
While the show is Paused or Dropped, each started season that isn't finished shows the same status.
Finished seasons stay "Watched".
Resuming the show returns those seasons to Watching.
Pausing or dropping never changes any level's viewings.

### TV-205: Spine never creates empty seasons

A season gets tracking only when it gets a viewing, log, mark, stars or heart.
Choosing Watching, moving up next into a new season, and finishing the season before it create nothing for that season.
When a season loses its last viewing, log and mark, its tracking goes and its stars and heart are cleared (SW-008).

A season that isn't started is treated as untracked whatever it stores, including a season from before this contract that holds no viewing, log or mark.
It is never shown as Watching and never appears on Home.
Season lists leave it out, and every payload reports it as untracked; its stars and heart still show (TV-900).
TV-801 keeps such seasons stored.

### TV-206: A newly aired episode reopens a finished show

When an unwatched regular episode airs, or the provider adds an aired regular episode to a season the user had finished, that season reopens and a Completed show moves to Watching.
For a show that was finished, up next becomes that episode, and Continue Watching shows it as new: "S3 E1 · New".
This happens within about an hour of the stored next air date; other provider changes take effect within two days.

A show the provider lists no more episodes for stays Completed.
Planning, Paused and Dropped shows don't move (TV-203).
Specials airing never reopen anything (TV-004).
A mark covers only the episodes that had aired when it was made, and a log only those that had aired when it was saved (TV-400); neither marks a newer episode watched.

### TV-207: Every write path ends in the same state

The app, the web, webhooks, imports, the API and the calendar record facts: viewings, logs, marks and choices.
In the same transaction, Spine works out the rest from those facts and stores it for the show and for each tracked season: status, watched and aired counts, up next, the next air date, and the latest viewing's calendar day.
Lists, Home and Continue Watching read these stored values; they never fetch provider data or count viewings per row.

Working out the state:

- never invents a date;
- never deletes a viewing or a log;
- never changes a Planning, Paused or Dropped choice;
- depends only on stored facts, the provider's lists and today on the user's calendar;
- writes only the rows whose values change; and
- gives the same result however many times it runs.

A status written directly through any entry point means the matching action:

- on a show, Completed means mark the show watched (TV-300), In progress means choose Watching (TV-005), and Planning, Paused and Dropped mean that choice, with the + menu's availability rules; and
- on a season, Completed means mark that season watched, and In progress means choose Watching for the show.

A written status whose action is unavailable changes nothing: the API answers 409 with the reason, and the web, webhooks and imports keep the worked-out status.
A season Planning, Paused or Dropped, and progress or date fields on shows and seasons, are rejected by the API as invalid requests (400) and ignored from the web, webhooks and imports; they never change the show (TV-204).
Deleting a season's tracking removes every viewing and mark of that season, and is refused (409) while any log of the season, its episodes or the show exists.
Deleting a show's tracking is Remove from Library (TV-003).

The same facts reach the same state whatever the entry point.

### TV-208: Undoing the write that ended a choice restores the choice

Apply this rule whenever an untick, log deletion or mark undo leaves less recorded under the show.
If it removes everything recorded since a tick, log or mark ended a Planning, Paused or Dropped choice, the show returns to that choice, unless the user has chosen another status since.
If the user chose Watching before anything was recorded, removing everything leaves the show in Watching with nothing watched.
When an undo or deletion restores a choice and leaves the show with no viewing, log or mark, clear the current stars and hearts of the show, its seasons and its episodes, keeping custom lists (GM-304).
If the show had no choice and nothing under TV-003 remains, it leaves the library, with the same clearing (SW-008, SW-009, GM-304).
Never restore a status from history that no longer exists (GM-015).

For example, a show is Paused at S2 E3.
Ticking S2 E4 by mistake moves it to Watching; unticking S2 E4 returns it to Paused.

## Marks, hearts and direct stars

### TV-300: The show and season eyes mark every aired regular episode watched, undated

Tapping the empty show eye asks with a native confirmation, such as "Mark all 62 aired episodes watched?"
The number counts the show's aired regular episodes.
Confirming marks every aired regular episode that isn't watched in the current pass watched, with no date, as one mark.
Episodes already watched keep their viewings, and specials are left alone.

The show becomes Completed, or Watching as caught up when a season is still airing.
The mark creates no log and no feed item (SW-001, SW-004) and ends a Planning, Paused or Dropped choice.
The optional star picker may follow; dismissing it keeps the mark (SW-011).
Afterwards the Library still shows one card, every season card reads "Watched", or "Airing" for a season still airing, and every aired regular episode is ticked.

The season eye does the same for one season, without the confirmation.
The Specials eye follows TV-004.

The show eye is filled while the show has an aired regular episode and every one is watched in the current pass: whenever the show is finished or caught up, whatever its status.
A season eye is filled when its season reads "Watched" or "Airing" (TV-200).
When nothing under it has aired, the empty eye records nothing and says when it premieres, for example "S1 premieres Mar 12", and "Nothing has aired yet" when no date is known.

During a rewatch, the show eye, or the rewatched season's eye, marks the rest of that rewatch's aired regular episodes watched for the rewatch, which finishes it (TV-602).
The show eye then asks "Mark the rest of this rewatch watched?"

The filled eye follows TV-302.

### TV-301: Fills belong to the log or mark that made them

A fill is an undated viewing a log or mark adds for an aired episode that isn't watched in the current pass.
Each fill belongs to the log or mark that made it.

Fills count as watched for progress, up next and finishing, like any viewing.
They have no date, so they never move a last-watched date and never count in date-based statistics (SW-004).
A mark's fill is unlogged; unticking can remove it (TV-102).
A log's fill goes only when that log is deleted (TV-406).
A mark ends when its last fill is removed; an ended mark counts for nothing under TV-003, TV-006, TV-205, TV-208 and the Started term.
The Quick log date preference no longer applies to TV; no setting gives a fill a date (TV-901).

Fills, and the episode records they need, are written in bulk, with no provider call or query per episode.

### TV-302: Undoing a mark removes only what it added

Tapping a filled show or season eye works through these cases in order:

1. **A log covers that level.** If that level has its own log in the current pass, or, for a regular season, the show has a show log in the current pass, change nothing. Say "Delete your show log first." or "Delete your Season 2 log first." (SW-008)
2. **Marks finished it.** If removing every mark fill inside that level in the current pass would leave an aired episode the eye covers unwatched, remove those fills. This applies whichever action made them: that level's eye, the show eye, Mark watched up to here, a Yes to the earlier-episodes question, or stars or a heart. Keep ticks, logs with their viewings and fills, and mark fills outside that level. Fills of episodes that a season or show log covers stay, and become that log's fills.
3. **Anything else finished it.** Otherwise ask "Mark all 62 episodes unwatched?", counting the watched episodes it will unmark. This option is unavailable while any log under that level exists in the current pass, with the reason "Delete the logs first." Confirming removes, in the current pass, every viewing and mark of the episodes that eye covers.

Then work out the state again and apply TV-208.
The eye never deletes a log.

For example:

- Marking a new show watched and then tapping the filled eye removes the show from the library under case 2.
- Ticking all of Season 1, marking the rest watched with the show eye, then tapping the filled eye unmarks Season 2 onward. Season 1 stays ticked, and the show returns to Watching with S2 E1 up next.
- Ticking all of Season 1, tapping the Season 2 to 5 eyes, then tapping the filled show eye also unmarks Seasons 2 to 5 under case 2, without a dialog.
- Marking the show watched and then tapping the filled Season 2 eye unmarks only Season 2.
- Tapping the filled eye of a show finished by ticking asks "Mark all 62 episodes unwatched?" under case 3.

### TV-303: Stars or a heart on a show you haven't started mark it watched

This is the movie rule (SW-105, SW-200) for a show with nothing watched.
On a show the user hasn't started, setting its stars or turning its heart on does all of these in one atomic step:

- marks every aired regular episode watched, undated, as one mark, as the show eye does but without its confirmation;
- puts the show in the library as Completed, or in Watching as caught up; and
- sets the show's stars or heart, independent of any log.

On a show the user hasn't started, a season's stars or heart do the same for that season alone, and TV-107 then asks about earlier seasons.
On a started show, a season's stars and heart are opinion only (TV-304), whether or not that season has anything watched.
The eye can undo the mark (TV-302).
Clearing the stars or turning the heart off never unmarks anything (SW-201, GM-301).
The optional star picker may follow the heart; dismissing it keeps the mark and the heart (SW-011).
Direct stars write a rating activity row under SW-401, which activity doesn't show (TV-703); a heart writes none.

While nothing under a show or season has aired, its heart is opinion only: it saves and marks nothing.
Its stars are unavailable then, with the reason "Nothing has aired yet."
Installed builds get this law as soon as the server ships: their heart on a show the user hasn't started marks it watched.

### TV-304: On a started show, stars and a heart are your opinion only

On a show the user has started, setting, changing or clearing the stars of the show or of any of its seasons, or turning their hearts on or off, changes only that opinion.
It never marks, finishes or reopens anything, never changes status, never ends a choice and never moves up next.
It establishes no mark.
While the user is watching, the show's stars are a rating so far.

### TV-305: A heart on an unwatched episode marks it watched

Turning the heart on for an aired episode with no viewing in any pass does both of these atomically:

- marks the episode watched, undated, as one mark; and
- sets the episode's heart (SW-200).

When that is the show's first regular watch, TV-107 applies.
On an unaired episode, or one the user has watched in any pass, the heart is opinion only.
Turning the heart off never unmarks the episode (SW-201).
Episodes have no direct stars; their stars open Log Episode (TV-502).

## Logs

### TV-400: Every log puts the show in the library

Saving a log of an episode, a season or the show always:

- creates exactly one dated diary entry;
- puts the show in the library, creating the show's tracking and the target season's tracking when they are missing;
- counts its target as watched under TV-401 or TV-402; and
- works out the show's status again.

For TV, a log's `auto_mark_consumed` value is ignored: every log counts its target as watched.
A log ends a Planning, Paused or Dropped choice.

While a log exists, nothing unwatches the episodes it covered when it was saved:

- an episode log keeps its own viewing; and
- a season or show log keeps every episode of its target that had aired when it was saved, whether it filled that episode or the episode was already watched.

Episodes that air later aren't covered (TV-206).
Installed builds get this law as soon as the server ships (TV-900).

For example, logging Season 1 of a show the user has never tracked puts the show in Watching with S2 E1 up next.
If Season 1 is the show's only season, the show goes to Completed.

### TV-401: A season or show log marks the rest of its aired episodes watched

Saving a season log marks every aired episode of that season that isn't watched in the current pass watched, undated.
These fills belong to the log.
A show log does the same for every regular season.
A specials log does the same for the aired specials.

Episodes already watched keep their viewings and dates.
Unaired episodes are not filled, so a season log in a season that is still airing leaves the show caught up in Watching.
A log of a season already finished in the current pass fills nothing, and still covers the season (TV-400).
A season or show log means the user watched everything in it that had aired, on the log's date.

For example, with E1 to E3 and E5 to E10 ticked, logging Season 1 fills E4 and keeps the nine ticks with their dates.

### TV-402: An episode log describes your tick, or adds a viewing

An episode log claims the most recently recorded tick of that episode in the current pass that belongs to no log.
The claimed tick becomes the log's own viewing.
When the tick's calendar day differs from the log's, the viewing takes the log's calendar day; its recorded time is kept only for ordering.

The log adds a new viewing on its own date instead when either of these holds:

- the current pass has no such tick; or
- the user turns Rewatch on outside a rewatch that covers the episode.

During a rewatch that covers the episode, the toggle never stops a claim.
A fill is never claimed, so logging a marked or season-logged episode adds a viewing.
Ticking and then logging an episode therefore counts one viewing, and the log creates one feed item.
An episode log never reopens a finished season or show (TV-603).

### TV-403: Finishing a season offers its log once

When a tick or an episode log in the app finishes a season, the app opens that season's Log Season composer, dated with the day of that tick or log (TV-404).

- **Save** creates one season log, sets the season's stars and heart under TV-501, and posts one feed item. It fills nothing, because the season is already watched, and it covers the season (TV-400).
- **Not now** saves nothing (SW-301). The season stays finished, and Log Season stays in its + menu.

Spine offers the composer at most once for each season in each pass, so unticking and re-ticking the finale doesn't offer it again.
When the same write also finishes the show, the composer opens for that season, and the user can switch its target to Whole show.
During a rewatch, the composer opens with Rewatch on (TV-404).
TV-107 decides when the composer opens after the earlier-episodes question.

The composer doesn't open when the season already has a log in the current pass.
It doesn't open when a mark, a season or show log, a webhook or an import finishes the season, or for Specials (TV-004).
If the user skipped an episode, the season isn't finished and nothing opens.

The write's response names the season or show it just finished, so the app knows to open the composer.
The app opens it as soon as that response arrives, wherever the write was made, and loads nothing first.
Installed builds ignore that key and open nothing.

### TV-404: The server supplies the composer's defaults

The server works out the composer's target, date, stars, heart and Rewatch default when the composer opens (SW-010).
The user can change all of them before saving.

**Target**

- From an episode page, the target is that episode; from a season page, that season.
- From the show page, the target is Whole show. The user can pick a season instead; Specials are listed last. While only specials have aired, Whole show is unavailable, so the target is Specials.
- From the season-finish composer, the target is that season (TV-403).

**Date**

The date defaults to today on the user's calendar.
An episode log that will claim a tick defaults to that tick's calendar day instead.
The season-finish composer defaults to the calendar day of the tick or log that finished the season.

**Stars and heart**

The composer prefills stars and heart from the target's own current values (SW-302): the season's for a season log, and the show's for a show log.
An episode log starts with the episode's current heart and no stars, unless it was opened from the episode's stars (TV-502).

**Switching the target**

Switching the target replaces every default the user hasn't changed (date, stars, heart and Rewatch) with the new target's defaults.
The server sends defaults for each target the picker offers.

**Rewatch**

The first rule that matches decides:

- During a rewatch that covers the target, the default is on.
- An episode log that will claim a tick defaults off.
- Any other episode log defaults on when the episode already has a viewing or a log, in any pass, and off otherwise.
- Any other season or show log defaults on when a log of that same season, or of the show for a show log, already exists in any pass, or when the target has at least one watched episode and every watched episode in it is watched only by marks. That matches the movie rule for marking watched and logging later. Otherwise the default is off, because the log describes this time through.

On a season or show log, the saved toggle controls only the badge; outside a rewatch it never changes what the log fills.
On an episode log, it also decides whether the log claims a tick (TV-402).
Editing or deleting another log never recalculates a saved value (SW-010).

### TV-405: Editing a log's date moves only that log's own viewing

Changing an episode log's date moves the date of that log's own viewing and no other viewing of the episode.
Changing a season or show log's date moves no viewing; its fills stay undated.
Changing a date never changes which pass a viewing belongs to (TV-601), or whether anything is watched.
TV-407 sets the date limits.

### TV-406: Deleting a log removes only its own viewing and fills

Deleting an episode log removes the viewing it added.
Deleting a season or show log removes the fills it made, including fills it took over from a mark (TV-302).
A tick the log claimed stays as an unlogged tick, on the calendar day it has now.
Other viewings, marks and logs stay.

When the deletion will unmark episodes, ask first, for example "Delete this log? 7 episodes it marked watched will be unmarked." (GM-221)
Then work out the state again and apply TV-208.
Stars and heart follow the source rules: a deleted source log leaves the current value in place, now independent (SW-106, SW-203).
Episode stars and hearts follow TV-502.

For example, E1 to E3 are ticked and a season log fills E4 to E10.
Deleting the log unmarks E4 to E10 and keeps E1 to E3, and the show returns to Watching with S1 E4 up next.
If one season log is the only thing under a show, deleting it removes the show from the library and clears its stars and heart.

### TV-407: Log dates are calendar dates, never in the future

Every TV log has a calendar date on the user's calendar.
The date is required and defaults under TV-404.
Future dates are rejected when creating or editing a log, from every build.
Air dates don't limit log dates; a log can be dated before an episode's listed air date.
Installed builds send a date and time; the server converts it to the user's calendar date (TV-900).
The repair converts logs from before this contract (TV-801).

## Stars and likes

### TV-500: Each level has its own stars and heart

The show, each season and each episode have their own heart.
The show and each season have their own stars.
The show's stars are about the show, and a season's stars are about that season.

Nothing is averaged, copied or rolled up or down.
A season log never sets the show's stars, a show log never sets a season's, and an episode log never sets its season's.
The show's stars appear on the show page, its Library card and its community rating.
A season's stars appear on the season page and its community rating.

### TV-501: Show and season stars and hearts follow the shared source rules

Apply SW-100 through SW-108 and SW-202 through SW-203 to the stars and heart of the show and of each season.
TV-303 and TV-304 decide what direct stars and hearts do to tracking.
The log's level decides which current values it touches.

- Each log keeps its own optional stars and its own heart.
- A rated new log sets that level's current stars and becomes their source, including when backdated (SW-101).
- An unrated new log keeps the current stars and their source (SW-102).
- A new log sets that level's current heart on or off and becomes its source (SW-202).
- Editing a source log updates the corresponding current value; clearing its stars clears the current stars (SW-104).
- Editing another log leaves the current value unchanged, except that a log given new stars fills empty current stars and becomes their source (SW-108).
- Directly changing or clearing a current value makes it independent without changing any log (SW-105, SW-203).
- Stars and heart have separate sources; changing one doesn't remove the other's link.
- Deleting a source log keeps the current value as independent (SW-106).

### TV-502: An episode's stars are those of its newest rated log

Episodes have no current rating of their own.
The episode page's stars show the user's newest rated log of that episode, by log date and then by when it was saved.
Tapping them opens Log Episode with those stars set; nothing changes until the log saves.
Editing or deleting logs updates the stars shown to the newest rated log that remains.

An episode's heart is a current heart under SW-200, SW-201 and TV-305, with no source log.
A new log of the episode sets it to that log's heart (SW-202); editing or deleting logs never changes it.
TV-102, TV-003 and TV-208 clear it.

### TV-503: Community ratings count each person once

A show's or season's community rating counts each person's current stars once.
Spine already does this for shows and seasons (`src/api/services/media.py:4775-4791`).
An episode's community rating counts each person's newest rated log of that episode once.
Today it counts every episode log (`src/api/services/media.py:4828-4832`).
Logging the same show, season or episode again never counts a person twice.
Which accounts and logs count stays as today (Scope boundary).

### TV-504: TV keeps its 0–10 wire scale

TV ratings use the ten half-star values of SW-107.
On the wire, shows, seasons and episodes keep the doubled values 1 to 10 in whole steps, which installed builds read (TV-900).
Storage uses the same doubled scale as movies (`src/app/single_weight.py:47-78`).
New writes from the app and the API accept only those ten values.
Imports keep a source's off-scale score as it is, and stored ratings from before this contract that fall off the scale are kept as they are, not rewritten.

## Rewatch

### TV-600: Rewatching a show or season starts a rewatch

Rewatch, on a finished show's + menu, starts a rewatch of the show.
Rewatch Season, on a finished season's + menu, starts a rewatch of that season.
Starting a rewatch records no viewing and creates no log.

The show moves to Watching.
Its line reads "Rewatching · S1 E1 next" for the show, and "Rewatching S2 · E1 next" for a season.
Earlier viewings and logs stay as history.

A show can have one rewatch in progress at a time.
While one is in progress, Rewatch and Rewatch Season are unavailable, with the reason "Finish or stop your rewatch first."
A show or season that isn't finished can't be rewatched yet.

### TV-601: A viewing counts toward a rewatch when it's recorded after the rewatch began

During a rewatch, the current pass for the rewatched show or season is the viewings recorded, and the logs saved, after the rewatch began.
During a season rewatch, every other season's current pass is all of its viewings.
Outside a rewatch, the current pass is every viewing.

Ticks, episode logs, fills and marks made during the rewatch count toward it.
Membership goes by when a viewing was recorded, not by its date.
A log saved during a rewatch counts toward it even when dated earlier, and editing a date never moves a viewing between passes.

### TV-602: A rewatch ends when it's finished or stopped

A rewatch finishes when its show or season is finished in the rewatch, that is, when every aired regular episode in its scope is watched in it.
Finishing each season during a rewatch offers its log with Rewatch on (TV-403).
After a rewatch finishes, the show returns to the status worked out from all of its history: Completed, or caught up.

Stop rewatching, on the + menu during a rewatch, ends it at once.
It keeps everything watched in the rewatch, and the show returns to the status worked out from all of its history: Completed for a finished show.

After a rewatch ends, every viewing counts again, because the current pass is all history.
Pause and Drop during a rewatch keep it, and Resume continues it (TV-007).

### TV-603: Rewatching one episode never reopens anything

To log watching one episode again, use Log Episode with Rewatch on; it adds a viewing on the log's date (TV-402).
It never reopens a finished season or show, and never starts a rewatch.
A tick on an episode that is already watched does nothing (TV-101).

## Surfaces

### TV-700: Each Library card says where you are

| Section | Card line |
| --- | --- |
| Watching | "S2 E4 next · 13 of 20", "Caught up · E7 on Thu", "Caught up", or "Rewatching · S1 E3 next" |
| Planning | The premiere date while nothing has aired, for example "Premieres Mar 12" |
| Paused | "Paused at S2 E3", naming up next |
| Dropped | "Stopped at S2 E4", naming up next |
| Completed | Nothing extra, or "Caught up · S3 on Mar 12" for a returning show |

Counts follow TV-104, and the card's stars are the show's own (TV-500).
A Paused or Dropped show with nothing watched, or with no up next, names no episode: it reads "Paused" or "Dropped".
Each empty TV section's text describes what appears there correctly.

### TV-701: Continue Watching lists shows

Continue Watching on Home and Profile lists shows, not seasons.
It lists every show in Watching with an aired up-next episode, most recently watched first.
That order goes by the latest calendar day of a tick or log under the show, then by when it was recorded; a show with nothing watched goes by when Watching was chosen.

Each card shows the show's poster and title, then one line, for example "S1 E2 next · 4 of 10".
It reads "S3 E1 · New · 20 of 21" when up next aired after the user's latest viewing of the show.
Where the count doesn't fit, as on Profile, the line leaves it out.
The card has no tick of its own.

Tapping the poster opens the show.
Touching and holding it opens the standard menu, previewing up next's still, or the show's artwork when there is none, with "S2 E5 · Marionettes" under it.
The menu offers:

- Mark S2 E5 Watched, which ticks up next (TV-100), asks the earlier-episodes question (TV-107) and opens the season-finish composer as soon as the tick saves (TV-403);
- Log S2 E5…, which opens the episode page's composer (TV-404);
- Go to Season 2, which opens the season's page; and
- right after a tick from this menu, while the card stays on screen, Mark S2 E4 Unwatched, which unticks the episode it ticked (TV-102).
  It stays through a pull to refresh and through the tick's own earlier-episodes question and Log Season.
  It goes when the card leaves the screen (another tab, or a page or the Log screen opening over it), when up next moves on elsewhere, or when the season's log is saved.
  If the episode changed elsewhere in the meantime, so the untick changes nothing, the card says so, with an error haptic.

VoiceOver offers the same items as actions on the card, and reads the show and its line.
After a write from the menu, the card's line changes where it is, with a success haptic; a failed write restores the card, with an error haptic and a short message (TV-705).
The show keeps its place in the row until the screen next loads it: on the next visit or a pull to refresh.
Continue Watching sends up next's title and still from the stored listing (TV-207).

Caught-up, Planning, Paused, Dropped and Completed shows are not listed.

### TV-702: The Diary shows every TV log

The Diary lists logs of shows, seasons and episodes, and its TV filter includes all three.
Season logs read "Show · Season 1", episode logs read "Show · S1 E4 · Title", and show logs read the show's title.
Each row's stars and heart are that log's own.

### TV-703: Activity follows the activity contract

What people see as activity is defined in [the activity contract](activity-contract.md), and TV follows it:

- A day's ticks on a show, across all its seasons, are one item of progress on the show, told in episodes: "watched 4 episodes" (AC-1).
- Unticks count against that day's ticks. A day that didn't move forward shows nothing (AC-2).
- Marks and fills aren't progress: the show and season eyes, Mark watched up to here, the Yes to the earlier-episodes question, and a log's fills (AC-1).
- Starting the show is activity: choosing Watching, a tick that puts the show in Watching, or starting a rewatch. Resuming a paused show isn't starting it; starting again after finishing or dropping is. Progress that day replaces the start (AC-1, AC-2).
- A log of the show, or of any of its seasons or episodes, is one item on its calendar day (TV-407). It replaces that day's start and progress for everyone who can see the log (AC-2).
- When the show leaves the library, its starts and progress go with it. Deleting a season's tracking removes that season's progress (AC-2).
- Imports, scheduled jobs and the repair start nothing and record no progress.

Every write that changes a season's watched count records one progress change for that season, because installed builds' Home shows the latest one (TV-900).
Its current value is the season's new count. Its previous value is that count less how far the write's ticks moved it, so a mark, fill, log or rewatch records the new count without moving it.
A start is recorded on the show.

As SW-401 notes for other media, the `social.Activity` rows Spine writes aren't what activity shows.
For TV, ticks, unticks, marks and fills write none.
Each log writes one diary row, including its stars (SW-401), and no separate rating row or second row when it adds or claims a viewing.
Directly setting stars on a show or season writes or updates a rating row (SW-401). Hearts write none.

The user's own Home also shows progress through Continue Watching (TV-701).

### TV-704: Calendar and notifications follow the show's status

Every show in Planning, Watching or Completed gets calendar events and notifications for its episodes.
A Paused or Dropped show gets none, as the existing rule already does for those statuses (`src/events/models.py:22-25, 82-143`).
Because seasons have no Pause or Drop of their own, the show's status alone decides.
Shows that have only logs now have tracking, so they get events too.

### TV-705: Every surface agrees after a write

After a successful write, the show, season and episode pages, Library, Continue Watching, Diary, Profile, activity and search agree about:

- status and section;
- each season's finished state;
- progress, up next and caught up;
- which episodes are watched;
- stars and hearts at each level;
- log counts; and
- last-watched dates.

Optimistic display can show a pending tick, but a failure restores the last confirmed state (SW-700).
Ticks go through the same write queue as other changes, so the next screen opens with the tick's result.

Writes to one user's show apply one at a time, without waiting on other users' writes; Spine fetches provider data before a write waits its turn.
Ticks, marks and their undos are explicit actions, never toggles, so a retry gives the same result.
Logs from the new build, the earlier-episodes Yes and confirmed destructive actions carry a mutation ID and apply at most once.
Installed builds send no mutation ID with TV logs, so a retried log from them can still duplicate, as today.
A confirmed destructive action also carries the state version its dialog showed; if the show changed since, Spine changes nothing and returns the new count so the app can ask again.

## Imports, webhooks and legacy data

### TV-800: Imports end in the state the app would produce

Imports may write in bulk, but Spine works out each imported show once afterwards, so its final state matches what the app would produce (SW-500).

- An imported Completed show or season becomes an undated mark, with its missing aired regular episodes filled.
- Imported season statuses other than Completed are ignored, and an imported season with nothing under TV-205 isn't started.
- Imported Planning, Paused and Dropped on a show become the user's choice; imported In progress means Watching.
- Each imported play becomes its own viewing, with the date supplied or none, even of an episode already watched; TV-101 applies only to taps and webhooks.
- An imported rating or like with no status follows TV-303 and TV-304 against the imported history.
- Imports create no feed activity and never ask the earlier-episodes question.
- Spine never infers Paused or Dropped from provider inactivity (GM-501).
- Overwrite replaces the show's viewings, marks and choices with the source's, as today, so it drops marks, choices, a rewatch in progress and star sources made in Spine.
- Logs survive an overwrite, and each log's own viewing and fills are rebuilt (TV-400). Rebuilding an episode log's viewing claims an imported viewing of that episode on the log's calendar day before adding one.

### TV-801: Data from before this contract

Spine applies a legacy rule to a show stored before this contract shipped that it has never worked out:

- a stored Completed season whose viewings don't cover its aired episodes counts as an undated mark of that season; and
- a stored Completed show with no season tracking counts as an undated mark of the show.

Each mark fills the regular episodes that had aired when the row was last set to Completed, from its history, or every aired regular episode when there is no history.
Rating-only imports and finale-only completions left these hollow rows.
Everything else is worked out normally.

Until such a show has been worked out, only the repair or the user's own write in the app or on the web works it out.
Scheduled jobs skip it, and webhooks and imports record their facts without working it out.
Shows first stored after this contract ships are worked out normally from their first write.
Once a show has been worked out, the rule never applies to it again, so a newly aired episode reopens it under TV-206.

A season-level Paused or Dropped value from before this contract is not a choice; the worked-out season status replaces it.
A season holding nothing under TV-205 stays stored but counts as not started.

A one-time repair brings existing data under this contract:

- It runs as a dry run by default, reports every status it would change, and is safe to run again.
- It runs on the test backend first, then on production right after the deploy that ships this contract.
- It gives every existing season and show log its fills under TV-401, for the episodes that had aired when the log was saved, whether or not its target had tracking.
- It links each existing episode log to one viewing of that episode: the one dated exactly at the log's date and time, else an unlinked one on the log's calendar day, else a new one on the log's date. Other viewings stay as ticks, and viewings that bulk completions dated with the completion time keep those dates.
- It converts each existing TV log's date and time to a calendar date on the user's calendar, stored as single-weight dates are, and lists every log whose shown day changes.
- It applies the legacy rule, then works out every other show.
- It applies TV-804 to every show and season it gives tracking to.
- It lists every season-level Paused or Dropped value it replaces.

Statuses change where today's stored status disagrees with these laws.
For example, shows with only logs appear in the Library, and empty "Started" seasons leave Home.

### TV-802: A webhook play isn't a watch

Plex, Jellyfin and Emby events follow the app's rules:

- A watched event ticks that episode, dated at the event time (TV-100). For an episode already watched in the current pass it records nothing (TV-101), so a rewatch needs a log with Rewatch on.
- An unplayed event unticks it (TV-102).
- A play that isn't a watch records no viewing. It counts as choosing Watching (TV-005), so it starts an untracked show and ends Planning, Paused or Dropped. Where Watching is unavailable it changes nothing, so it never reopens a finished show.
- Each event applies in one transaction and creates nothing else: no season tracking and no status of its own.
- A rejected event, such as an unlisted or unaired episode or an untick of a logged viewing, is logged and acknowledged, never answered with an error.
- Webhooks never ask the earlier-episodes question and never open a composer.

### TV-803: The CSV export and Yamtrack re-import keep today's columns

The export's column list and row order stay exactly today's: one row per show, season and viewing (`src/integrations/exports.py:21-82`).
New tracking fields aren't exported and never reuse an existing column name.
Progress and dates are exported as this contract works them out, and fills export as viewings with no date.
A rewatch in progress isn't exported, so its show re-imports as finished.
Exporting and then importing into an empty account gives the same watched episodes, sections and stars, apart from a rewatch in progress.
Logs and likes stay out of the export, as today.

### TV-804: Existing log stars fill empty show and season stars once

A one-time data change, with no network calls, fills the current stars of a show or season that has none.
They come from the user's newest rated log of that same show or season, which becomes their source (SW-108).
It skips a show or season whose history shows its stars were cleared on purpose, and changes nothing else.
The repair applies the same change to the shows and seasons it gives tracking to (TV-801).

## Installed builds

### TV-900: Payload changes are additions only

Every field installed builds read keeps its meaning:

- every endpoint and request shape;
- the status strings, with "In progress" for Watching (`ios/Spine/Spine/Features/Library/LibraryView.swift:39-56`);
- an episode's `is_tracked`, meaning the episode has at least one viewing (`ios/Spine/Spine/Features/MediaDetail/MediaDetailView.swift:2369-2373`);
- the `progress` keys `kind`, `value`, `max` and `unit`, with values corrected under TV-104, so `value` is the number watched and `max` the number aired;
- the seasons list on a show and the episodes list on a season;
- the 0–10 TV rating scale (TV-504), which installed builds halve to draw stars (`ios/Spine/Spine/Core/Models/MediaModels.swift:34-36`; `ios/Spine/Spine/Features/MediaDetail/MediaDetailView.swift:8412-8416`); and
- Home's query for In progress seasons (`ios/Spine/Spine/Core/Repositories/InProgressLibraryLoader.swift:6-19`), which now finds only started, unfinished seasons.

A season that isn't started reports `is_tracked` false and no status, and season lists leave it out; its stars and heart still show (TV-205).

New information arrives in new keys, which installed builds ignore:

- the TV state of shows and seasons: up next, counts, caught up, next air date, rewatch, available actions and their reasons, and the composer's defaults for each target the picker offers;
- each episode's watched state, naming the viewing an untick would remove;
- what a write just finished; and
- whether a write left earlier episodes unwatched, with the range the question names.

Every write that changes a season's watched count writes the progress change that installed builds' Home shows (`src/api/services/tracking.py:458-482`; `ios/Spine/Spine/Core/Models/TrackingModels.swift:114-126`). Only its ticks move it, so a mark, fill or log shows the new count there without being progress in activity (TV-703).

Installed builds get these laws as soon as the server ships:

- every log counts its target watched, and season and show logs create tracking and fills, because they already send every TV log as one that marks watched (`ios/Spine/Spine/Features/MediaDetail/MediaLogView.swift:304-316`) (TV-400, TV-401);
- deleting a log removes only its own viewing and fills (TV-406);
- editing a log's date moves only that log's viewing (TV-405);
- ticking then logging an episode counts once, because those builds default Rewatch from the log count, which is 0 after a tick (`ios/Spine/Spine/Features/MediaDetail/MediaLogView.swift:89`);
- season log stars become the season's stars (TV-501);
- no empty seasons are created (TV-205); and
- a heart on a show the user hasn't started marks it watched (TV-303).

Installed builds keep these limits until the next build:

- Their show and season eyes do nothing, and they can't untick (`ios/Spine/Spine/Features/MediaDetail/MediaDetailView.swift:2347-2352`). So a heart that marks a show watched can't be undone from them.
- They never open the season-finish composer or ask the earlier-episodes question, so nothing earlier is filled.
- They send a date and time for TV logs; the server converts it to the user's calendar date, and a future date returns a validation error. Their logs of a season or show with nothing aired fail (TV-106).
- A season log made from the show page may save with Rewatch on (`ios/Spine/Spine/Features/MediaDetail/MediaLogView.swift:89`), which only labels the log (TV-404).
- A retried TV log from them can still duplicate, because they send no mutation ID with it (`ios/Spine/Spine/Features/MediaDetail/MediaLogView.swift:304-315`; TV-705).
- They delete logs without asking first (TV-406).
- Their Home lists seasons, not shows (`ios/Spine/Spine/Core/Repositories/InProgressLibraryLoader.swift:6-19`). A show chosen as Watching with nothing watched, or one whose next season just aired, appears there only after its first tick, and a caught-up season still airing stays listed, although Continue Watching hides it (TV-701).
- During a rewatch, episodes watched in an earlier pass show as watched and can't be ticked (`ios/Spine/Spine/Features/MediaDetail/MediaDetailView.swift:2369-2371`).
- Their Diary TV filter still leaves out episode logs (`ios/Spine/Spine/Features/Diary/DiaryView.swift:224`).

### TV-901: Expand, then contract

Migrations are additive: nothing is dropped or renamed, and every new field is nullable or has a default.
The old cascade code is removed only after installed builds no longer depend on it.
The next build hides the Quick log date setting (`ios/Spine/Spine/Features/Profile/ProfileView.swift:3082-3085`).
Its stored preference stays, but TV no longer reads it (`src/users/models.py:789-806`).

## Acceptance scenarios

These scenarios check the numbered laws; they do not introduce separate product decisions.
Run shared single-weight, book and game regression cases alongside these TV cases.
Run each scenario through the API, through the model hooks the web and webhooks use, and through an import where one applies; all paths must reach the same end state.
Run date cases under a non-UTC timezone, including a server on America/New_York with an account calendar on America/Los_Angeles.

| Case | Action | Required result | Laws |
| --- | --- | --- | --- |
| A-01 | Tick S1 E1 of an untracked show. | The show is in Watching with S1 E2 up next and "1 of N". No log and no season tracking beyond Season 1. Activity shows "watched 1 episode" for the show. | TV-100, TV-105, TV-205, TV-703 |
| A-02 | Double tap or retry a tick. | One viewing. | TV-101, TV-705 |
| A-03 | Tick, then untick, the only episode of an untracked show. | The show leaves the library. | TV-102, TV-208 |
| A-04 | Untick an episode whose only viewing is a season log's fill. | Nothing changes. The message names the season log. | TV-102 |
| A-05 | Tick a season's last episode, choose Not now, then untick and re-tick it. | The season is finished and the composer opened once. Nothing is saved, Log Season stays in the + menu, and the re-tick opens nothing. | TV-200, TV-403 |
| A-06 | Skip E4 and tick E5 to E10. | The season isn't finished: "9 of 10 · E4 not watched". No composer opens. Up next is S2 E1. | TV-105, TV-200, TV-403 |
| A-07 | Then log Season 1. | E4 is filled undated, the ticks keep their dates, and the season is finished. | TV-401 |
| A-08 | Finish Season 1 when Season 2 has aired. | Up next is S2 E1. No Season 2 tracking exists. | TV-105, TV-205 |
| A-09 | Watch everything aired while the next season hasn't aired; then its first episode airs. | Completed, "Caught up · S3 on Mar 12". Then Watching, with "S3 E1 · New" on Continue Watching. | TV-202, TV-206, TV-701 |
| A-10 | Catch up mid-way through a weekly season. | Watching, "Caught up · E7 on Thu", and absent from Continue Watching until E7 airs. | TV-202, TV-701 |
| A-11 | Watch only a season's finale. | The season isn't finished. Progress reads "1 of 10". | TV-104, TV-200 |
| A-12 | Add an aired episode to a season the user had finished. | The season reopens and the show moves to Watching. | TV-206 |
| A-13 | Log Seasons 1 and 2 of an untracked show, with no ticks. | The show is in the library with S3 E1 up next, or Completed. Each season's stars come from its log. | TV-400, TV-401, TV-501 |
| A-14 | Log Season 3 first, answering Yes, then repeat on another show answering No. | Yes fills Seasons 1 and 2 undated. No fills nothing, and that show never asks again. | TV-107 |
| A-15 | Tick S3 E1 first, answer No, and watch through the last aired episode. | Up next moves to S3 E2, and later to S1 E1. The show isn't finished. | TV-105, TV-107, TV-201 |
| A-16 | Tick E1 to E3, log the season, then delete the log. | The deletion asks first, unmarks E4 to E10, keeps E1 to E3, and returns the show to Watching with E4 up next. | TV-406 |
| A-17 | Delete the only log under a show. | The show leaves the library, and its stars and heart are cleared. | TV-406, TV-208 |
| A-18 | Tick an episode, then log it. | One viewing. The date defaults to the tick's day and Rewatch to off. One feed item. | TV-402, TV-404, TV-703 |
| A-19 | Log a ticked episode with Rewatch turned on. | A second viewing is added. Nothing reopens. | TV-402, TV-603 |
| A-20 | Edit an episode log's date when the episode has several viewings. | Only that log's viewing moves. | TV-405 |
| A-21 | Save or edit a TV log dated in the future, from any build. | Rejected, with no partial change. | TV-407 |
| A-22 | Tick or log an episode the provider doesn't list, or one airing in three days. | Rejected, with no change. | TV-106 |
| A-23 | Mark a 62-episode show watched with the eye. | After confirmation, all aired regular episodes are filled undated. Completed, one card, every season "Watched". No log, no feed item. Specials untouched. | TV-300, TV-004 |
| A-24 | Tap the filled eye of a show that only a show mark tracks. | The show leaves the library, and its stars and heart are cleared. | TV-302, TV-208 |
| A-25 | Tick Season 1, mark the rest with the eye, then undo. | Season 2 onward are unmarked. Season 1 stays ticked. Watching, with S2 E1 up next. | TV-302 |
| A-26 | Tap the filled eye of a show finished by ticking, with and without an episode log. | Without logs it asks "Mark all 62 episodes unwatched?". With a log the option is unavailable. | TV-302 |
| A-27 | Tap the filled eye of a show with a show log. | Nothing changes: "Delete your show log first." | TV-302 |
| A-28 | Heart a show the user hasn't started, from the new build and from an installed build. Dismiss the star picker. | The show is marked watched, Completed and hearted in both. The new build's eye can undo the mark; the installed build's can't. | TV-303, TV-900 |
| A-29 | Set stars on a show at S2 E3. | Opinion only. Status and up next are unchanged. A rating activity row is written, which activity doesn't show. | TV-304, TV-703 |
| A-30 | Set stars on an unwatched Season 4 while watching Season 2. | Opinion only. Season 4 stays unwatched, and up next stays in Season 2. | TV-303, TV-304 |
| A-31 | Heart an unwatched episode, then unheart it. | The episode is marked watched undated, then stays watched. | TV-305 |
| A-32 | Save a rated season log, change the season's stars directly, then edit the log's stars. | The log sets and links the stars. The direct change unlinks them. The edit leaves the current stars alone. | TV-501 |
| A-33 | Save a rated season log. | The show's stars are unchanged. | TV-500 |
| A-34 | Give an episode two rated logs, then delete the newer one. | The stars show the remaining log. The community rating counts the person once. | TV-502, TV-503 |
| A-35 | Rewatch a finished show and tick through it. | "Rewatching · S1 E1 next". Each season's composer opens with Rewatch on. The show returns to Completed at the end. | TV-600, TV-601, TV-602, TV-403 |
| A-36 | Stop rewatching midway. | Everything watched in the rewatch is kept. Completed. | TV-602 |
| A-37 | Pause at S2 E3, tick S2 E4 by mistake, then untick it. | Watching, then Paused again. | TV-007, TV-208 |
| A-38 | Drop a show, then let a new episode air. | It stays Dropped, with no events or notifications. | TV-203, TV-704 |
| A-39 | Choose Planning on a show with a viewing. | Unavailable, with its reason. | TV-006 |
| A-40 | Tick a special on an untracked show, then heart the show. | Watching, with S1 E1 up next and progress unchanged. Planning is unavailable, and the heart is opinion only. | TV-004, TV-006, TV-304 |
| A-41 | Send a webhook play that isn't a watch, for a Completed show and for a Paused one. | The Completed show doesn't change. The Paused show moves to Watching. Neither gets a viewing or season tracking. | TV-802, TV-203 |
| A-42 | Let the hourly job run on a stored Completed show with no episode history that has never been worked out, then tick its S1 E1. | The job leaves it as stored. After the tick it is still Completed: the legacy mark fills it, and the tick records nothing. | TV-801, TV-101 |
| A-43 | Import a Completed show, then reimport. | One undated mark with fills. No duplicates, and no feed activity. | TV-800 |
| A-44 | Remove a show from the library with and without logs. | With logs it is blocked. Without them it asks first, removes everything, clears stars and hearts, and keeps custom lists. | TV-003 |
| A-45 | Export a user's TV data and import it into an empty account. | The same watched episodes, sections and stars. | TV-803 |
| A-46 | Retry a save from the new build, double tap, or hit a failure partway through a fill. | No duplicate viewings, marks or logs. All related changes apply, or none do. | TV-705, TV-207 |
| A-47 | Mark a 30-season, 700-episode show watched, then undo it. | Each is one request with a bounded number of queries and no provider call per episode. | TV-301, TV-302 |
| A-48 | Tick S1 E1 to E10, save the season-finish composer, then untick E10. | Nothing changes: "Delete your Season 1 log first." | TV-400, TV-102 |
| A-49 | Mark the show watched with the eye, log Season 2, then tap the filled show eye. | Every season except Season 2 is unmarked. Season 2 stays watched, and its fills now belong to its log. | TV-302, TV-400 |
| A-50 | Mark a show watched, then tap the filled Season 2 eye. | Only Season 2 is unmarked, with no dialog. Watching, with S2 E1 up next. | TV-302 |
| A-51 | Tick all of Season 1, tap the Season 2 to 5 eyes, then tap the filled show eye. | Seasons 2 to 5 are unmarked, with no dialog. Season 1 stays ticked. | TV-302 |
| A-52 | On a new show, Mark watched up to here at S2 E4, then tap the filled Season 1 eye. | Season 1 is unmarked. S2 E1 to E4 stay watched. | TV-108, TV-302 |
| A-53 | Heart an unstarted show mid-way through a weekly season, then tap its filled show eye. | First Watching, "Caught up · E7 on Thu", with the eye filled. The tap removes the mark, and the show leaves the library with its heart cleared. | TV-202, TV-300, TV-302, TV-208 |
| A-54 | Drop a finished show, then choose Watching. | Completed. | TV-005, TV-007 |
| A-55 | Tick S1 E10 first with E1 to E9 unwatched and answer Yes; repeat on another show answering No. | Yes fills E1 to E9 and opens the Season 1 composer once. No fills nothing and opens no composer. | TV-107, TV-403 |
| A-56 | Log Season 1 of a never-tracked show; then mark Season 2 with its eye and log it. | Rewatch defaults off for the Season 1 log and on for the Season 2 log. | TV-404 |
| A-57 | Write Dropped to a season through the API, and import a season stored as Dropped under a show stored as In progress. | The API answers 400, and the import ignores the season status. The show stays Watching. | TV-207, TV-204, TV-800 |
| A-58 | Tap the Specials eye. | The aired specials are marked. Progress, up next and status are unchanged, and no composer opens. | TV-004 |
| A-59 | Run the repair on a season with E1 and E2 ticked and a season log from before this contract. | E3 is filled as that log's fill, E1 and E2 keep their dates, and the season is finished. | TV-801, TV-401 |
| A-60 | Tick at 9 p.m. Pacific on an account whose app sent America/Los_Angeles, with the server on America/New_York; then let the hourly job run. | The tick's day is the Pacific day, and the job doesn't change the status. | TV-100, TV-103, TV-207 |
| A-61 | Tick the same episode from two devices at once, and send a webhook watch during an app tick. | One viewing each time. | TV-101, TV-705 |
| A-62 | Let TMDB stop listing a tracked season. | Its viewings, logs and stars stay; it counts toward nothing and shows after Specials. Writes to the show still work. | TV-103 |
| A-63 | Track a manual show with no air dates. | Every listed episode counts as aired, and the eyes and season logs work. | TV-103 |
| A-64 | Watch everything TMDB lists for a season whose next episode TMDB names but doesn't list yet. | The season reads "Airing", isn't finished and opens no composer. The show is Watching, "Caught up · E7 on Thu". | TV-200, TV-202, TV-403 |
| A-65 | On PostgreSQL and on SQLite, untick an episode with an undated viewing and a dated one recorded later. | Both remove the later-recorded viewing. | TV-102 |
| A-66 | Export TV data after this contract ships. | The CSV header is exactly today's. | TV-803 |
| A-67 | Run the repair with the server on America/New_York over a TV log saved at 9 p.m. New York time. | Its calendar date is that New York day, and the repair lists every log whose shown day changes. | TV-801, TV-407 |
| A-68 | Log an episode with its heart on, then edit the log's heart off. | The episode stays hearted. | TV-502 |

Check the show, season and episode pages, Library, Continue Watching, Diary, Profile and the feed for consistent results.

## Review status

The user approved the design on 2026-10-25 and chose the recommended answer to each open question:

- finishing a season offers its log once (TV-403);
- a show caught up between seasons waits in Completed and returns to Watching when the next regular episode airs, and one caught up mid-season stays in Watching (TV-202, TV-206);
- starting partway asks once about earlier episodes (TV-107); and
- stars or a heart mark an unstarted show watched, and are opinion only on a started one (TV-303, TV-304).

After trying the app on 2026-10-26, the user asked for four changes, now in the laws:

- Remove from Library shows only for a show in the library (TV-003, TV-005);
- the show page's Up next uses the season list's episode row (TV-105);
- the season-finish composer opens without a wait (TV-403); and
- Log on the show page starts on Whole show (TV-404).

Then the user chose touch and hold for marking progress from Home: Continue Watching's tick gave way to the card's menu (TV-100, TV-701).

Whether ticks post to friends' feeds was first settled by AGENTS.md section 9, whose feed then held logs, ratings and list activity, so ticks didn't post.
The activity contract (`docs/activity-contract.md`) replaced that on 2026-10-26: a day's ticks are progress on the show, and TV-703 now follows it.

Writing the laws settled details the approved design leaves open.
Two reviews, one for consistency and one for feasibility against the code, checked each detail against the design, the five decisions and the inherited contracts; the laws above include their corrections.
Two details go beyond those sources and are provisional until the user confirms them:

- watched specials start a show, so a heart on a show with only specials watched is opinion only (Terms, TV-004, TV-303); and
- an episode's heart has no source log, so editing an episode log's heart never changes it (TV-502), because the approved storage plan gives heart sources only to shows and seasons.

No other product questions remain from this review.
Not verified: whether TMDB accepts the batched season requests Spine makes for shows with more than eight seasons (`src/app/providers/tmdb.py:640-651`); A-47 checks it.

This document does not claim that TV implementation or runtime testing is complete.
No application code was changed while writing this contract.
Follow-up documents, as for books and games: `tv-tracking-coverage.md`, mapping each law to its tests, and a simulator verification document.

## Scope boundary

This contract does not cover:

- the same show tracked as both TV and anime;
- how Stats count TV, apart from the Diary's TV filter (TV-702);
- moving TV logs from per-entry visibility to account-level visibility (SW-400), and privacy filtering of TV community counts and likes;
- mixed rating scales in Stats;
- bugs only in the web's own views and templates;
- which provider a show gets when Spine creates it from a season write;
- how each importer parses its source;
- watch-time statistics; and
- API defects outside these laws: TV list sorting and watched-date filters, listing episode tracking, the first-open season state, and the ignored consume option.

These need separate decisions if they are taken on later.

## Change policy

The numbered laws above are normative product behavior.
Implementation details may change without editing this document, but behavior that contradicts a law requires an explicit product decision and a contract update first.
Add a new TV-specific rule only after the user accepts it.
Record inherited shared rules directly under TV-000 without requesting repeated approval.
Keep unresolved questions separate from accepted rules.
Behavior omitted from this contract is not implicitly decided.
