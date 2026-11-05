# Manga and Comics Tracking Contract

Status: **agent-ready product contract**

Last reviewed: **2026-11-02**

This contract covers manga (MyAnimeList, MangaUpdates) and comics (Comic Vine) in Spine.
It does not cover anime ([Anime Tracking Contract](anime-tracking-contract.md)).
Only the numbered laws below contain accepted product decisions.
Read it with the inherited rules named in MC-000 before implementation.

Manga and comics are books counted in chapters or issues.
A series is one thing, so finishing it is its log, as with a book.
A TV season's log is optional because a season is part of a show; Spine knows no parts of a series.

Use these contracts as the shared foundation:

- [Book Tracking Contract](book-tracking-contract.md)
- [Single-Weight Consumption Contract](single-weight-consumption-contract.md)
- [Activity Contract](activity-contract.md)

## Inherited foundation

### MC-000: Reuse the book rules without repeating settled product questions

The following rules are inherited without another approval step.
"Journey" in the book contract is "attempt" here.

| Area | Existing rules | Application to manga and comics |
| --- | --- | --- |
| Library sections | BK-001 | Five sections with the book words: To Read, Currently Reading, Paused, Did Not Finish, Read. One section per series. MC-006 defines what posters show. |
| Library membership and removal | BK-002 | Any status places the series in the library. Simple removal is blocked while a real attempt or log remains. Custom lists are never touched. |
| Plus menu | BK-100 | Currently Reading, To Read, Paused, Did Not Finish and Log, with Resume when Paused. Mark as Read stays on the eye. |
| Retrospective status | BK-101 | Direct To Read, Paused, Did Not Finish and Read invent no attempt, place, date or log. |
| The eye | BK-102, BK-103, BK-310 | With no attempt under way, the eye marks the series Read with no date and no log, and the filled eye undoes only that mark. During an attempt it opens the log screen. |
| Starting | BK-104 | One tap, at place zero, with today as the start date. Planning is unavailable during an attempt. Starting after Read is a reread, except under MC-200. |
| Logs | BK-105, BK-106, BK-107 | Log with no attempt under way creates a finished attempt and its log. Log during an attempt finishes it. Log after Did Not Finish keeps the closed attempt. |
| Heart and stars | BK-200 to BK-203 | With no attempt under way they mark the series Read. During an attempt they open the log screen with that value set (MC-104). |
| Attempts | BK-300, BK-303, BK-304, BK-306, BK-307, BK-308, BK-309 | One attempt under way at a time. Pause keeps the place. Restart from Beginning keeps the old attempt as Did Not Finish. Reading History lists closed attempts. Attempts can be deleted explicitly. BK-306's rules for a log's date reach these types with their counting, not before. |
| Finishing and the diary | BK-400 to BK-406 | The log and the finished attempt save together. Cancelling changes nothing. Deleting the log restores the attempt. Reread is derived. Read counts unique series. |
| Opinions and diary fields | BK-202, SW-100 to SW-108, SW-202, SW-203, SW-300 to SW-303 | Each log keeps its own stars and heart. A new log becomes the current source. The same editor is used everywhere. |
| Dates | SW-005 to SW-007 | Local calendar dates. Distinct same-day logs are allowed. |
| Saves and retries | BK-500, BK-501 | The backend enforces transitions. Related changes succeed together. Retries and double taps create no duplicates. All screens agree. |
| Activity and visibility | AC-1 to AC-7 | "started reading", "read 5 chapters", "read 2 issues". Marks are never activity. A log and that day's progress are separate items, and both take likes and comments. Another person's list shows where a series stands, never what a log they can't see set. |

**Do not inherit:**

- Pages, percent, and page-to-percent conversion (BK-300, BK-400's 100 percent). MC-001 replaces them.
- A new attempt at zero after Did Not Finish (BK-305). MC-201 replaces it.
- From TV: the optional finish log (TV-403), Mark watched up to here (TV-108), Caught up (TV-202), and ticking single units (TV-100).
- From games: finishing that never follows from reaching the end (GM-200) and progress saves after finishing (GM-202).

## Terms

| Term | Meaning |
| --- | --- |
| Series | One manga, or one comic run as Comic Vine lists it. Batman (2011) and Batman (2016) are two series. |
| Attempt | One read of a series, with its status, start date and place. |
| Place | How many chapters or issues the user has read in this attempt. |
| Total | The chapters a manga's provider lists, or the issues Comic Vine says are out so far. A manga may have none. |

## Counting

### MC-001: Manga count chapters and comics count issues

The number is a whole number.
There are no volumes, pages or percent.
A manga page may still show its volume count as information.
A chapter such as 10.5 is entered as 10.

### MC-002: A comic's number is how many issues were read

It is never an issue number, because some runs skip numbers.
The total is Comic Vine's issue count.
The Library, Home, the progress sheet and the log screen all use that count, never the last issue's number or calendar events.

### MC-003: A manga's total comes from its provider, when it has one

Providers usually list no chapter total until a series ends.
With no total, screens show the number alone ("120 chapters"), there is no progress bar, and any number is accepted.
A total that appears later applies at once ("120 of 380").

### MC-004: A number past a known total is refused

The message names the total.
A comic re-reads Comic Vine's count once before refusing, because a new issue can be out before Spine's copy knows it.

### MC-005: A series with a total of one has no number

A one-issue comic or a one-chapter manga has status actions and no number field.

### MC-006: What posters show

As BK-001: only while a series is started and unfinished (Currently Reading, Paused or Did Not Finish), leaving out a place of zero.
The badge reads "120/380 chapters", "120 chapters" with no total, or "12/54 issues".
On the Library's small grid poster it leaves out the unit, on one line: "120/380", "12/54", or "Ch. 120" and "Iss. 12" with no total. Library rows and Home keep the full words.
The progress bar needs a total.
Profile's In progress row stays posters only.

### MC-007: Every save counts on its day

These are the maintainer's counting decisions of 2026-11-02, and the same as books:

- Each save counts on the day it was saved.
- Lowering a number fixes a typo. It is not a new read.
- The first save on an attempt counts whole on that day, even if it is catch-up.
- A log adds only what is left, on the log's day. Editing its date moves that count.
- A reread counts again, by a direct log too.
- What is undone counts on no day: an undone eye, a deleted attempt, a deleted log.

There is no undated "already read up to" option, because books have none.
Typing 1,100 counts 1,100 that day.
The eye is the only undated action, and it marks the whole series.
If an undated option is wanted later, it goes on books and on these types together.

## Finishing

### MC-100: Reaching the total opens the log screen

Saving a number equal to the total saves it, then opens the log screen.

- Saving the log finishes the attempt and moves the series to Read.
- Not now leaves it Currently Reading at that number, with no log.

Reaching the total alone never moves a series to Read (BK-302).

### MC-101: The log screen opens by itself once per attempt

A comic's total grows, so someone following a monthly run reaches the latest issue every month.
After the first time in an attempt, later saves don't open the log screen. A retry of that first save is still the first time, so it opens it.
Finished on the progress sheet and Log on the plus menu stay available.

### MC-102: With no total, the user decides what finished means

A manga with no total never opens the log screen by itself.
Log or Finished moves it to Read whenever the user chooses (as GM-201).
The log screen has an optional chapter field, prefilled with the place.
With a total, a log sets the place to the total (BK-400).

### MC-103: A log always means finished

There are no logs of part of a series: none up to a chapter, and none for a volume or a single chapter.
The log screen has no Progress mode for these types; progress lives on the progress sheet (MC-300).

### MC-104: Stars and the heart are given once a series is finished

During an attempt, including a paused one, stars or a heart open the log screen with that value set.
Saving finishes the attempt. Cancelling changes nothing, and the value isn't saved.
This includes a manga with no total: rating it means logging it as finished.
Once it is finished, stars and the heart can change at any time.

## Continuing

### MC-200: Keep Reading continues a finished series that has more

A Read series has more when it has no total, or when its place is below its total.
Its plus menu offers both Keep Reading and Reread.

- **Keep Reading** starts a new attempt at the old place. It is not a reread and records no start. Earlier logs stay. The next log is another diary entry, not marked as a reread, and carrying on doesn't add a lifetime read.
- **Reread** starts a new attempt at zero, and counts again.

A Read series with nothing more offers Reread only.

### MC-201: Picking up a stopped series continues from the place

Choosing Currently Reading after Did Not Finish starts a new attempt at the old place (the maintainer's decision of 2026-11-02: only Restart begins at zero).
The closed attempt stays in Reading History.
Restart from Beginning starts at zero.

### MC-202: A new issue moves a finished comic back to Currently Reading

When the daily calendar job finds a newer last issue for a comic on the Read shelf, the comic continues as under MC-200, by itself, once the issue is out: a store date still to come waits for that day. A cover date never holds an issue back.
The attempt starts on the issue's store date.
It adds no activity, its logs stay, and its eye empties (BK-103).
A comic already read up to the new total stays Read.
A comic marked Read with the eye had every issue so far, so it continues from the previous total, and only what comes next counts (MC-007).
Paused, Did Not Finish and To Read comics don't move.
The calendar job only checks comics with an issue in the past year, so a run that returns after a longer gap moves only when the user chooses Keep Reading.
Manga don't do this, because no provider says when a running series has a new chapter.

## Screens

### MC-300: The progress sheet is the book's

"Update Progress" opens the same native sheet as books (BK-301): a number field ready to type in, "of 380" when there is a total, Pause, Did Not Finish, Finished, Restart from Beginning and Delete.
It needs an attempt to update, so a series imported as Paused has Resume on the plus menu instead. While an attempt is paused the sheet has no number: it offers Resume, and the number follows.
The "Update progress ›" line on a series' In progress card on Home opens this sheet over Home (TV-701).
With no attempt to update, the line opens the page.

## Imports

### MC-400: Imports keep one tracking record per series

MyAnimeList, AniList, Kitsu and Yamtrack imports create one tracking record per series.

- Each reread in the source becomes a finished attempt, never another record.
- Reading with a number becomes an attempt at that place, with no start.
- Read with a date lands on that date. Read with no date is undated.
- To Read, Paused and Dropped stay the user's choice (GM-501).
- Imported numbers are not activity (SW-500).
- Volume counts in the source are ignored (MC-001).

## Installed builds and existing data

### MC-500: Installed builds keep working

Every field installed builds read keeps its meaning. New fields are additions.
What installed builds send still works: a tracking write with status and progress, a diary entry with `auto_mark_consumed`, and tracking removal go through these same rules.
Every new NOT NULL column has a database default, so code from before these columns keeps saving after a rollback.

### MC-501: There is no data to repair

No account on production had a manga or comic record on 2026-11-02, so this contract ships with no repair.

## Scope boundary

This contract doesn't cover anime, volumes, chapter or issue lists, the same series tracked under two sources, or a manga also tracked as a book.
How Stats add these numbers up belongs to the activity and stats work (`docs/stats-contract.md`), which follows MC-007.

## Contract change policy

Numbered laws are normative. New product decisions update this contract before implementation.
