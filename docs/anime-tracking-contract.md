# Anime Tracking Contract

Status: **agent-ready product contract**

Last reviewed: **2026-11-03**

This contract covers anime in Spine.
Only the numbered laws below contain accepted product decisions.
Read it with the inherited rules named in AN-000 before implementation.

An anime title works like one TV season with no show around it.
MyAnimeList (MAL) lists each season as its own title, so each season is its own Library card.
Someone who wants one show with seasons inside can track it under TV instead; Spine keeps the two apart.

Use these contracts as the shared foundation:

- [TV Tracking Contract](tv-tracking-contract.md)
- [Single-Weight Consumption Contract](single-weight-consumption-contract.md)
- [Activity Contract](activity-contract.md)

## Inherited foundation

### AN-000: Reuse the TV rules without repeating settled product questions

The following rules are inherited without another approval step.
Read "season" in them as the title, and leave out everything about the show around it.

| Area | Existing rules | Application to anime |
| --- | --- | --- |
| Library sections | TV-002 | Watching, Planning, Paused, Dropped and Completed. Status alone decides the section. |
| Membership and removal | TV-003 | "Remove this anime? Your 13 watched episodes will be unmarked." Unavailable while a log exists, with the reason "Delete this anime's logs first." |
| The + menu | TV-005 | Watching, or Resume when Paused; Planning; Pause; Drop; Rewatch, or Stop rewatching during a rewatch; Log; Remove from Library while in the library. An unavailable option says why. |
| Planning | TV-006 | Only while nothing is watched. |
| Pause and Drop | TV-007 | Pause saves at once. Drop asks "Drop this anime? Your progress will be kept." Both keep the place. Resume, or Watching on a Dropped title, continues there. |
| Ticks | TV-100, TV-101, TV-102 | One viewing, dated now. A double tap adds nothing. Tapping a filled tick removes the latest unlogged viewing. A logged episode says "Delete your log first." |
| Aired, up next, refusals | TV-103, TV-105, TV-106 | As AN-100, AN-102 and AN-103 apply them. |
| The earlier-episodes question | TV-107 | The title's first tick asks once: "Also mark E1–E4 watched?" A title's earlier title is never asked about. |
| Mark watched up to here | TV-108 | Touch and hold a row. "Mark E1–E4 watched?" |
| Finishing and reopening | TV-200, TV-206 | As AN-200 and AN-202 apply them. |
| Choices and undoing | TV-203, TV-208 | A choice stays until the next tick, log, mark or status action. Undoing the write that ended it restores it. |
| One worked-out state | TV-207 | Every entry point records facts, and the same work-out sets everything else in the same transaction. Lists read stored values; they never fetch from a provider or count viewings per row. |
| The eye | TV-300 (the season eye), TV-301, TV-302 | AN-300. |
| Stars and the heart | TV-303, TV-304, TV-500, TV-501, TV-503, TV-504 | AN-301. The title has its own stars and heart, following the shared source rules. |
| Logs | TV-400, TV-401, TV-403, TV-404, TV-405, TV-406, TV-407 | AN-203 and AN-400. |
| Rewatch | TV-600, TV-601, TV-602 | AN-600. |
| Calendar and notifications | TV-704 | Planning, Watching and Completed get events. Paused and Dropped don't. |
| Agreement and retries | TV-705 | Every surface agrees after a write. A retry never adds a second viewing, mark or log from the new build. |

**Do not inherit:**

- The show and seasons around a title: TV-001, TV-201, TV-204, TV-205, TV-408, the show eye's confirmation, the target picker on the log screen, Whole show, season cards, and Rewatch Season.
- Specials (TV-004). MAL lists them as titles of their own.
- Episode pages, episode logs, episode stars and episode hearts (TV-305, TV-402, TV-502, TV-603, and the episode parts of TV-404 and TV-405). A row only ticks.
- A finished title reading caught up (TV-202's Completed paragraph). The next season is another title, offered under AN-500.
- The data repairs (TV-801, TV-804). AN-902 explains why.

## Terms

| Term | Meaning |
| --- | --- |
| Title | One MAL entry: a season, a movie, a special. |
| Single | A title whose total is one, or a movie with no total (AN-002). |
| Total | The episode count MAL lists. A long runner may have none. |
| Aired | An episode that counts as aired under AN-100. |
| Viewing | One watching of one episode, dated or undated. |
| Tick | Tapping an episode watched. It records one viewing at that moment. Webhook watches count as ticks. |
| Mark | Marking watched without a log or a date: the eye, Mark watched up to here, a Yes to the earlier-episodes question, or stars or a heart that mark. |
| Fill | An undated viewing that a log or mark adds for an aired episode that wasn't watched. It belongs to the log or mark that made it. |
| Log | A diary entry about the whole title, on a calendar date. |
| Current pass | Every viewing, or during a rewatch, those recorded since it began. |
| Watched | An episode with a viewing in the current pass. |
| Started | The title has a viewing, log or mark, or the user chose Watching for it. Planning, Paused or Dropped chosen with nothing watched don't start it. |
| Partway | Started, with an aired episode not watched in the current pass. |
| Finished | AN-200. |
| Caught up | AN-201. |
| Next title | AN-500. |

## Library unit

### AN-001: One card per title

Each MAL title is its own card, once, on its status's shelf.
A person has one tracking record per title: a rewatch is a new pass over that record, never another record (AN-600, AN-800).
A show tracked under both TV and anime stays two separate things, and Spine doesn't link them.

### AN-002: A single works like a movie

A single has no episode rows, no up next, no earlier-episodes question and no number anywhere.
It has the eye, stars, the heart, and a + menu with Planning, Log and Remove from Library.

- The eye and direct stars or a heart mark it watched, undated, and it moves to Completed.
- It has no partway: once marked or logged, stars and the heart just save.
- Watching it again is another log with Rewatch on (SW-010). There's no Rewatch action.
- It has no badge or bar, and it never appears in In progress.

A single imported as In progress, Paused or Dropped stays on that shelf until the user marks or logs it.

## Episodes and progress

### AN-100: What has aired

An episode counts as aired when any of these holds:

- MAL says the title has finished airing, and the episode is within the total;
- AniList's schedule says its time has passed;
- AniList names a later episode as next to air;
- the user has a viewing of it; or
- the title has a total, AniList has no schedule for it, as with an older title, and MAL doesn't list it as not yet aired; then every listed episode counts as aired.

Spine keeps a stored copy of the title's total, aired count, next episode and rows, updated whenever it fetches them, and uses that copy when a provider can't be reached.
A write that needs a title Spine has no copy of is refused with a retryable "Anime details are unavailable right now", and changes nothing.

### AN-101: What a row shows

The page and the Home sheet list one row per aired episode, then the unaired episodes Spine knows of.

- A row shows the episode's number, its title and image when AniList lists them, and a tick. Without them it reads "Episode 5".
- Rows never wait for AniList. If a fetch fails, the number stays.
- An unaired row shows its air date in place of a tick, or "TBA".
- With no total and no schedule, the list runs to one past the last watched episode, so ticking still works.
- Tapping a row's tick ticks or unticks it. Tapping its body does nothing, because episodes have no pages.
- A title with a thousand episodes opens at up next as fast as a short one, and every episode stays reachable.

### AN-102: Progress means one thing everywhere

Progress is the number of distinct aired episodes watched in the current pass, out of the number aired.
It reads "7 of 24", or "7 episodes" when Spine doesn't know how many have aired.
Fills count. Repeat viewings and unaired episodes don't.
Every write keeps the tracking record's progress equal to this count, on every entry point.

### AN-103: Refusals

A tick, log or mark of an episode past a known total is refused, and the message names the total.
A tick of an episode whose air time is more than one day away is refused; the one day covers time zones.
With no total and no schedule, any episode number is accepted.
A log or mark of a title with nothing aired is refused.
A refused write changes nothing.
If a total later falls below an episode the user watched, that viewing stays and counts toward nothing.

## Finishing

### AN-200: Finished

A title is finished when it has a total, the last episode has aired, and every episode is watched in the current pass.
A finished title is Completed unless the user has chosen Paused or Dropped since (TV-203), and it leaves In progress.
Skipping an episode keeps it unfinished: "11 of 12 · E4 not watched".

### AN-201: Caught up

A title is caught up when every aired episode is watched and more are coming.
It stays in Watching, reads "Caught up · E8 on Thu" (the weekday within six days, the date after that, and just "Caught up" with no date), and leaves In progress until that episode airs.
A long runner with no total is never finished by ticking. It stays caught up, however far it goes.

### AN-202: A new episode reopens

When an unwatched episode airs on a caught-up title, it's partway again: up next is that episode and it returns to In progress.
When MAL raises the total of a finished title and the new episodes have aired, it reopens the same way.
A mark covers only what had aired when it was made, and a log only what had aired when it was saved.
Planning, Paused and Dropped titles don't move.

### AN-203: Ticking the last episode opens the log screen once

When a tick in the app finishes a title, the log screen opens as soon as the response arrives, dated with that tick's day.

- Save creates one log and fills nothing, because everything is watched.
- Not now saves nothing. The title stays Completed with no diary entry, and Log stays on the + menu.

It opens at most once per pass, so unticking and re-ticking the last episode doesn't open it again.
It doesn't open when a mark, a log, a webhook or an import finishes the title, or when the title already has a log in this pass.
A long runner with no total never opens it. The user taps Log when they choose.
During a rewatch it opens with Rewatch on.

## The eye, stars and the heart

### AN-300: The eye marks the rest, and undoes only that

The empty eye marks every aired episode that isn't watched in the current pass, undated, as one mark, with no question, no log and no feed item.
The title becomes Completed, or Watching as caught up. A choice ends.
With nothing aired, the eye records nothing and says when it premieres: "Premieres Mar 12", or "Nothing has aired yet".
During a rewatch it marks the rest of the rewatch, which finishes it.

The eye is filled while the title has an aired episode and every one is watched in the current pass.
Tapping the filled eye follows TV-302:

1. If the title has a log in the current pass, nothing changes: "Delete your log first."
2. If removing mark fills would leave an aired episode unwatched, they go. Ticks stay.
3. Otherwise it asks "Mark all 24 episodes unwatched?" and removes every viewing in the current pass. This is unavailable while a log exists.

Then the state is worked out again under TV-208.

### AN-301: Stars and the heart mean you finished this title

This is the rule every media type follows, set by the maintainer on 2026-11-03.

- **Not started.** Setting stars or turning the heart on marks every aired episode watched, undated, as one mark, with no log screen, and sets the value. The title is Completed, or Watching as caught up. The eye can undo the mark.
- **Partway.** It opens the log screen with that value set. Nothing changes until the log saves. Saving marks the rest and sets the value. Cancelling saves nothing, including the value.
- **Finished or caught up.** The eye is filled. It only changes the value, and changes nothing else.

During a rewatch, partway means partway through the rewatch, and the log screen opens with Rewatch on.
When a new episode airs, the stars and heart stay, and the title is partway until the user catches up.
Clearing the stars or turning the heart off is only that, at any time.
While nothing has aired, the heart saves and marks nothing, and stars are unavailable with the reason "Nothing has aired yet."

The server refuses a direct set on a partway title with the reason "Log this first."
Imports never open anything: an imported rating on a title with no imported history marks it, and on one with history only sets the value.

## Logs

### AN-400: A log means finished

A log is about the whole title. The log screen has no target picker and no Progress mode.
Saving one from any page or build does all of these together:

- creates one diary entry, on a calendar date, never in the future (TV-407);
- puts the title in the library and ends a choice;
- fills every aired episode that isn't watched, undated, as the log's own fills; and
- leaves the title Completed, or Watching as caught up when more is coming. It never pretends unaired episodes were watched.

The date defaults to today. Stars and the heart prefill from the title's own, and a rated log becomes their source (TV-501).
The Rewatch default follows TV-404's rule for a season log. Outside a rewatch, the saved toggle controls only the badge.

Editing a log's date moves no viewing (TV-405).
Deleting a log removes only its fills, and asks first when it will unmark anything: "Delete this log? 7 episodes it marked watched will be unmarked." (TV-406)
While a log exists, nothing unwatches the episodes it covered when it was saved.

## The next title

### AN-500: Spine offers the next title and adds nothing by itself

The next title is the one after this one in MAL's prequel and sequel order, the same order the anime's series page shows.

Once a title is finished, and while the user hasn't finished the next one, its page shows "Next: Attack on Titan Season 2", which opens that title's page.
Nothing is added to the library until the user acts on that title.

### AN-501: Home's sheet moves on

When the log screen from AN-203 closes on Home's sheet, saved or not, and the next title has an aired episode, the sheet shows the next title's rows, with up next at the top.
Closing the sheet without ticking adds nothing.
The first tick there puts the next title in Watching, under the rules of any first tick.
With no next title, or none that has aired, the sheet stays on the finished title, every tick filled.

## Rewatch

### AN-600: Rewatch starts at episode 1

Rewatch is on a finished title's + menu. It moves the title to Watching with E1 up next and records no viewing.
Earlier viewings and logs stay as history.
The line reads "Rewatching · E3 next", and progress counts the rewatch.
Reaching the end finishes it and opens the log screen with Rewatch on (AN-203). After a rewatch ends, by finishing or by Stop rewatching, every viewing counts again and the title returns to the status its history gives it.
Pause and Drop during a rewatch keep it.
A rewatch counts again in activity and stats (AN-703, AN-704).

## Surfaces

### AN-700: Each Library card says where you are

| Section | Card line |
| --- | --- |
| Watching | "E8 next · 7 of 24", "Caught up · E8 on Thu", "Caught up", or "Rewatching · E3 next" |
| Planning | The premiere date while nothing has aired, for example "Premieres Mar 12" |
| Paused | "Paused at E8", naming up next |
| Dropped | "Stopped at E8", naming up next |
| Completed | Nothing extra |

A Paused or Dropped title with nothing watched reads "Paused" or "Dropped".
A grid poster's badge names up next, as "E8", only once an episode has been watched in the current pass, while the title is Watching, Paused or Dropped and not caught up. Every other poster has no badge.

### AN-701: Home's Update progress opens the episode sheet

An anime in Watching with an aired up next is in In progress, on Home and on Profile, following TV-701: poster, title, "Update progress ›", the poster's bar and badge on Home, and posters only on Profile.
The bar needs a known aired count.

The line opens a half-height sheet of the title's rows, titled with the title, opening with up next's row at the top every time.
It behaves as TV-701's sheet does, with these differences: a row's body does nothing (AN-101), its ··· menu offers Log… and Open Anime, and AN-501 replaces moving on to a later season.

### AN-702: The page

An anime page shows Up next as a row, the episode list, the eye, stars, the heart and the + menu, and "Next:" under AN-500.
The series page keeps working as it does today.
A single follows AN-002.

### AN-703: Activity

As TV-703: a day's ticks on a title are one item, "watched 4 episodes". Unticks count against that day's ticks.
Marks and fills are never activity.
Starting a title is activity: choosing Watching, a tick that puts it in Watching, or starting a rewatch.
A log is one item on its calendar day, separate from that day's start or progress (AC-2).
Imports and webhooks' marks start nothing.

### AN-704: Stats read viewings

An anime counts as a show does (STAT-201's TV row, STAT-104):

- A tick counts on its day. Unticking takes it off.
- A log's fills count on the log's date. Deleting the log takes them off.
- A mark's fills count only in All Time, never in a year.
- A rewatch counts again, because its viewings are new.
- A stored progress number with no viewings counts as it does today, in All Time only.

Each viewing's length is the title's runtime.

## Imports, webhooks and export

### AN-800: Imports end in the state the app would produce

MAL, MAL export, AniList, Kitsu, SIMKL and Yamtrack imports create one tracking record per title.

- A count becomes undated fills of that many episodes, as one mark, never activity.
- Completed becomes an undated mark of everything aired. Any log the import makes today, it still makes, and that log's date dates its fills.
- Each rewatch in the source becomes a finished earlier pass, never another record.
- Planning, Paused and Dropped stay the user's choice. In progress means Watching. Spine never infers a stop from inactivity (GM-501).
- Imports create no feed activity, never ask the earlier-episodes question and never open anything.
- They write in bulk, with no provider call per episode.
- Overwrite replaces viewings, marks and choices with the source's. Logs survive and their fills are rebuilt (TV-800).

### AN-801: A webhook play isn't a watch

Plex, Jellyfin and Emby events follow TV-802: a watched event ticks that episode at the event's time, an unplayed event unticks it, and a play that isn't a watch counts as choosing Watching where that's available.
A rejected event is logged and acknowledged. Webhooks never ask a question or open anything.

### AN-802: The export keeps today's columns

The CSV's columns and row shape stay exactly today's, with one row per title.
Exporting and importing into an empty account gives the same watched counts, shelves and stars, apart from a rewatch in progress.

## Installed builds and existing data

### AN-900: Installed builds keep working

Every field installed builds read keeps its meaning, and new information arrives in new keys.
What they send still works: a tracking write with a status or a progress number, a log with `auto_mark_consumed`, and tracking removal all go through these same rules.

- A written Completed means mark it watched. In progress means choose Watching. Planning, Paused and Dropped mean that choice.
- A progress number from the web or the API means ticks. Raising it ticks the next unwatched episodes, dated now. Lowering it removes the latest unlogged viewings, and is refused when a log covers them.

Installed builds keep these limits until the next build: their anime eye does nothing, they have no rows, Home's card opens the page, and their stars and heart on a partway title show the server's reason.

### AN-901: Expand, then contract

Migrations are additive. Every new NOT NULL column has a database default, so code from before these columns keeps saving after a rollback (TV-901, MC-500).

### AN-902: There is no data to repair

No account on production had an anime tracking row or anime log on 2026-11-03. Re-check that count right before shipping.
A stored progress number that does turn up on a development database is worked out as an undated mark the first time the user writes to that title.

## Acceptance scenarios

Run each through the API, and through the web, a webhook or an import where one applies. Run date cases under a non-UTC timezone, with an account calendar that differs from the server's.

| Case | Action | Required result | Laws |
| --- | --- | --- | --- |
| N-01 | Tick E1 of an untracked 25-episode title. | Watching, "E2 next · 1 of 25". No log. Activity shows "watched 1 episode". | AN-102, AN-703 |
| N-02 | Double tap a tick. | One viewing. | TV-101 |
| N-03 | Tick E5 first, and answer Yes. | E1–E4 filled undated, E5 dated, "E6 next · 5 of 25". The question never asks again. | TV-107 |
| N-04 | Tick E5 first, and answer No. Then tick E6. | "E7 next · 2 of 25". No second question. | TV-107 |
| N-05 | Tick, then untick, the only episode. | The title leaves the library. | TV-102, TV-208 |
| N-06 | Tick the last episode of a finished-airing title. Choose Not now. Untick and re-tick. | Completed, no log, the log screen opened once. Log stays on +. | AN-200, AN-203 |
| N-07 | Skip E4 and tick the rest. | "24 of 25 · E4 not watched". No log screen. | AN-200, AN-203 |
| N-08 | Then Log. | E4 is filled undated, the ticks keep their dates, Completed. | AN-400 |
| N-09 | Eye on an untracked title. Then tap the filled eye. | Completed with no log and no activity, then gone from the library. | AN-300 |
| N-10 | Tick E1–E3, eye, then tap the filled eye. | E4 onward unmarked, E1–E3 stay, E4 up next. | AN-300 |
| N-11 | Stars on an untracked title. | Marked with those stars, Completed, no log screen, no activity. All Time rises; no year does. | AN-301, AN-704 |
| N-12 | Stars on a title at E3. Cancel. Then stars and Save. | The first changes nothing. The second makes one log, fills the rest, Completed. | AN-301 |
| N-13 | Direct stars through the API on a title at E3. | 409, "Log this first." Nothing changes. | AN-301 |
| N-14 | Stars on a caught-up airing title. Then E8 airs. Then stars again. | The first only saves. After E8 the rating stays, and stars open the log screen. | AN-301, AN-202 |
| N-15 | Log an airing title with 7 of 12 aired. | Watching, "Caught up · E8 on Thu". Fills for what aired. | AN-400, AN-201 |
| N-16 | Delete that log. | Its fills go, ticks stay, and the title returns to where it was. | AN-400 |
| N-17 | Tick to the end of a long runner with no total. | Caught up. No log screen. Log on + still works. | AN-201, AN-203 |
| N-18 | A title with no total and no schedule, nothing watched. | One row, E1. Tick it: two rows, and "1 episode". | AN-101, AN-102 |
| N-19 | Finish a title from Home's sheet, with a next title aired. Choose Not now. | The sheet shows the next title's rows. Closing adds nothing. A tick there puts it in Watching. | AN-501 |
| N-20 | Open a finished title's page, with a next title. | "Next: …" opens its page. Nothing is added. | AN-500 |
| N-21 | Finished title, Rewatch, tick E1 and E2, then Stop rewatching. | Rewatching read "1 of 25" and "2 of 25". Afterwards Completed with every tick from both passes kept. Stats counted the two again. | AN-600, AN-704 |
| N-22 | Pause at E8. Tick E8 by mistake, then untick it. | Paused again at E8. | TV-208 |
| N-23 | Eye on a movie. Then stars. Then Log. | Completed; stars only save; one log. No rows, no number. | AN-002 |
| N-24 | Import a MAL row with 2 rewatches and a count of 12, Completed. | One tracking row, three passes, no activity. | AN-800 |
| N-25 | Import with a count of 5, In progress. | Watching, "E6 next · 5 of 12", 5 undated fills. Stats' year unchanged, All Time rises. | AN-800, AN-704 |
| N-26 | Webhook watched event for E4 of an untracked title. | A tick at the event's time, no question. | AN-801 |
| N-27 | A progress write of 5 through the API on an untracked title, then 3. | Five ticks dated now, then the two latest gone. | AN-900 |
| N-28 | Log from an installed build's request shape on a title never tracked. | One log, one record, fills, Completed. | AN-400, AN-900 |
| N-29 | Run the old code's insert shape against the new tables. | It saves, by the database defaults. | AN-901 |
| N-30 | Export, then import into an empty account. | The same counts, shelves and stars. The header is unchanged. | AN-802 |

## Scope boundary

This contract doesn't cover filler marking, merging a show tracked as both TV and anime, episode pages or single-episode logs, a favorite episode on an anime log, or sub and dub.

## Contract change policy

Numbered laws are normative. New product decisions update this contract before implementation.
