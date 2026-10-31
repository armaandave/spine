# Stats Contract

Status: **agent-ready product contract**

Last reviewed: **2026-10-29** (round 2: drills, range progress for rings and collections, milestones within a range)

This contract covers the Stats page in Spine: what each number means, who sees what, and the v2 stats API that the backend (Milestone 2) and the iPhone app (Milestone 3) both build against.
Only the numbered laws contain accepted product decisions.
The API section is the backend/iOS contract; change it here first if implementation needs a different shape.
Rule IDs use the `STAT-` prefix because `ST-` already belongs to the [Streaming Availability Contract](streaming-availability-contract.md).

Read this contract with the inherited rules named in STAT-000:

- [Single-Weight Consumption Contract](single-weight-consumption-contract.md)
- [Book Tracking Contract](book-tracking-contract.md)
- [Game Tracking Contract](game-tracking-contract.md)
- [TV Tracking Contract](tv-tracking-contract.md)

## Inherited foundation

### STAT-000: Stats count what the tracking contracts already define

Stats add no new tracking behavior. They read the facts the other contracts define and count them.
The following rules apply without another approval step:

| Area | Existing rules | Application to Stats |
| --- | --- | --- |
| Undated consumption | SW-001, SW-004, BK-405, the game contract's Completion counts row (GM-000), TV-301 | A mark without a log, an undated Mark as Read or completion, and a TV fill have no day. They count only in All Time, never in a year or any other dated range (STAT-105, STAT-107, STAT-201). |
| Repeat history | SW-010, SW-503, BK-405, BK-406, GM-216, GM-217, TV-402, TV-404 | Rewatches are worked out from history under STAT-108. The book and game toggles are presentation only; a movie or album log's saved value counts only where STAT-108 says so. |
| Rating scale | SW-107, TV-504 | Every rating is stored on one 0–10 scale. Stats send stars, which are the stored value halved (STAT-109). |
| Log visibility | SW-400, TV-000 (Privacy and activity row), `visible_diary_entries` | Movie, music, book and game logs follow the account's privacy. TV, anime, manga and comic logs keep per-log visibility. Stats never change either rule (STAT-003). |
| Season labels | TV-004 | A season row reads "Show S2", and Season 0 reads "Show Specials", through `MediaRef.displayTitle` (`ios/Spine/Spine/Core/Models/MediaModels.swift`). |
| Steam playtime | GM-500 | Imported lifetime playtime is never added to game time (STAT-201). |
| Installed builds | TV-900, TV-901 | Payload changes are additions only (STAT-900 onward). |

## Terms

| Term | Meaning |
| --- | --- |
| Owner | The person whose stats are shown. |
| Viewer | The person asking. The owner is a viewer of their own stats. |
| Audience | `owner` (the viewer is the owner), `follower` (an accepted follow), or `public` (anyone else allowed to see the profile). |
| Owner's calendar | `app.user_calendar.calendar_tz(owner)`: the timezone the owner's app last sent, or the server's until it has sent one. |
| Range | All Time, or a start and end date on the owner's calendar, both inclusive. |
| Calendar-year range | A range from 1 January to 31 December of one year, exactly what the app's year pills send. |
| Tab | Global (no media type) or one primary media type. |
| Exposed type | A media type the server exposes (`app.exposure.primary_media_types()`). Music is exposed only when `MUSIC_ENABLED`. |
| Type bucket | The tab a row belongs to. TV, season and episode rows all belong to `tv`; every other type is its own bucket. |
| Log | A `DiaryEntry`. |
| Visible log | A log the viewer may see under `api.services.stats.visible_diary_entries(user=owner, viewer=viewer)`. Every log is visible to the owner. |
| Log day | The calendar day of a log under STAT-100. |
| Title | What a row counts as. A TV season or episode counts as its show; everything else counts as itself (STAT-102). |
| Target | The Item a log is about: an episode, season or show for TV, otherwise the title. |
| Viewing | A TV `Episode` row: one watching of one episode (TV terms). |
| Dated viewing | A viewing with a calendar day: its `watched_on`, or for a viewing that isn't a fill, its `end_date` on the owner's calendar (the same rule as `tv_tracking._viewing_day`). Fills have no day (TV-301). |
| Hidden viewing | A viewing that belongs to a log (`Episode.diary_entry`) the viewer can't see. |
| Event | One title on one day (STAT-106). |
| Undated completion | A title consumed with no log, counted only in All Time (STAT-105). |
| Stored rating | A rating on the stored 0–10 scale, one decimal place. |
| Stars | A stored rating divided by 2, sent as a decimal string (STAT-303). |
| Title rating | The rating Stats use for one Item in a range (STAT-109). |
| Media summary | The common media object the API already sends for posters (STAT-304). |

## Scope and audiences

### STAT-001: Stats describe one person for one range and one tab

Every stats response is about one owner, one range and one tab.
Global covers every exposed type; a type tab covers only rows in that type bucket.
Rows of types the server doesn't expose never appear anywhere in Stats.

### STAT-002: Everyone allowed to see the profile sees the same stats

The gate is `can_view_user_profile` and doesn't change: a private account needs an accepted follow, and a block either way returns 404.
Anyone who passes the gate sees the same stats the owner sees, including time from TV ticks, game sessions and book progress, "right now" counts, collections, and All Time Highest rated with titles rated but never logged.
No section is withheld because of who is asking.

### STAT-003: Only the owner's explicit choices filter what others see

Two choices still apply to other viewers:

- **Logs.** Only visible logs count. A viewing that belongs to a log the viewer can't see counts for nothing, so no number reveals a hidden log. Log-based numbers can therefore differ slightly from viewer to viewer.
- **Lists.** A list that isn't `visibility=public` never feeds Top for anyone but the owner (STAT-206).

Everything not built from logs or lists (tracking, statuses, viewings that belong to no log, sessions, progress history, current ratings and likes) is the same for every viewer.

### STAT-004: Instructions to the owner are shown only to the owner

`top.tag_hint` (how to tag a Top list) and `top.hint_private` ("Only you can see this list.") are sent only when the viewer is the owner.
Other viewers get `null` and `false`.

### STAT-005: Days are on the owner's calendar for every viewer

Every day boundary in Stats (log days, range edges, events, streaks, "today") uses the owner's calendar.
A viewer in another timezone sees the same days and the same numbers.
There is no timezone parameter.
The owner's own requests carry their app's zone in `X-Spine-Timezone`, which is the owner's calendar under `app.user_calendar`; another viewer's header never changes the owner's days.

### STAT-006: A write shows on the next request

Stats responses may be cached.
A change the owner makes (a log, a tick, a status, a rating or like, a session, a list or its items, or a setting), or a collaborator makes to one of the owner's lists, shows in the owner's stats on the next request, for every viewer.
Changes that come from elsewhere, such as the metadata fill or a featured list sync, can take up to an hour to appear.

## Counting

### STAT-100: A log's day is decided per row

If a log's `consumed_at` is exactly 00:00:00 UTC, its day is that UTC date.
Otherwise its day is `consumed_at` on the owner's calendar.

The first case covers logs whose time carries a calendar date: movie, music, book and game logs, TV logs (TV-407), MAL-export anime and manga, and TV logs the repair hasn't converted yet.
The second covers logs that record a real moment, such as anime finished in the app.
The rule applies to stats ranges, events, streaks and milestones, and to the diary's date filters (STAT-400).

For example, a TV log dated 1 January 2025 counts in 2025 for an owner in New York, and an anime log at 23:30 on 31 December 2025 in the owner's zone stays in 2025.

### STAT-101: A range holds the days from its start to its end

A log is in a range when its log day is on or after the start date and on or before the end date.
In queries the upper bound is "before the next day", never `time.max`.
All Time has no bounds and also counts undated facts (STAT-105, STAT-107, STAT-201).

### STAT-102: TV seasons and episodes count as their show

The title of a season or episode row is its show's Item: the TV Item with the same source and media ID and no season or episode number.
When that show Item doesn't exist, the row counts as its own title.
Every other row is its own title.

Title counts, events, genres, languages, release years, most logged and milestone posters use titles.
Highest rated and Rated higher than most use Items instead: a season is its own row there, and episodes are left out (STAT-205).

### STAT-103: Logs count every visible log in the range

`logs` counts visible logs whose log day is in the range, including every TV episode, season and show log (TV-702).
A log counts once whatever its target.

### STAT-104: TV viewings count with their day; a log's fills count on the log's day

Dated viewings count in the range that holds their day.
A fill a log made (TV-301) counts on that log's day (STAT-100): logging a season on 3 April puts its filled episodes, and their hours, on 3 April.
A mark's fills have no day: they never count in a dated range, and count in All Time.
Viewings of specials count like any other viewing; TV-004 keeps specials out of progress, not out of Stats.
Hidden viewings never count (STAT-003).

### STAT-105: Titles in a range include viewings and, in All Time, undated completions

The `titles` of a scope are the distinct titles of:

1. its visible logs;
2. shows with a dated viewing in the range that isn't hidden; and
3. in All Time only, undated completions: titles consumed with no log at all, of any visibility:
   - movies and music: the direct-consumption marker;
   - books and games: `completed_manually`;
   - TV, anime, manga and comics: the latest tracking row is Completed.

A title counts once however many of these it has.

### STAT-106: An event is one title on one day

Events are the distinct (day, title) pairs from visible logs, from dated viewings that aren't hidden, and from each day's progress and starts, as the activity feed counts them (docs/activity-contract.md, AC-1, AC-2).
A day's progress is the owner's recorded progress changes on a title in one local day, kept when it moved forward on net; starting a title counts the same way.
TV counts only its starts this way: its ticks already are events, as dated viewings on the day watched.
Three episodes of one show watched on one day are one event, and so are a manga's chapters read and logged on one day.
Fills and undated completions are never events.
Events drive active days, streaks, "Days with 2+ titles", the activity charts and the busiest day and month.

### STAT-107: Reads and completions always add the undated fact in All Time

Book reads are visible book logs in the range.
Game completions are visible game logs in the range.
In All Time, a book with `completed_manually` adds one read, and a game with `completed_manually` adds one completion, even when the title also has logs (BK-405, GM-000).
DNF reading journeys and dropped playthroughs never count as reads or completions.

### STAT-108: A rewatch is worked out from history

A log is a rewatch when one of these holds:

1. **An earlier log.** Its target has an earlier visible log, ordered by log day, then by ID.
2. **An undated fact before the first log.** It is its target's first visible log, the target was consumed before it without a date, and the type's rule agrees:
   - **Books and games:** `completed_manually` always counts, because an undated completion is earlier than every dated one (BK-406, GM-216). The saved reread or replay toggle is never read (BK-405, GM-217).
   - **Movies and music:** the direct-consumption marker counts only when the log was saved as a rewatch (`DiaryEntry.is_rewatch`, SW-010, SW-503). The saved value is the user's or the import's own statement; the marker alone can't say whether it came first, because imports set it after importing the diary.
   - **TV season and show logs:** a mark's fill under the target, created before the log, counts only when the log was saved as a rewatch.
   - **TV episode logs:** the episode had a viewing created before the log's own viewing that the log didn't claim (TV-402). The saved toggle is never read.
   - **Anime, manga and comics:** only rule 1 applies.

`rewatches` counts the rewatch logs in the range.
For TV the target is the logged episode, season or show, never the whole show, so a log of S1 E2 after a log of S1 E1 is not a rewatch.

### STAT-109: Ratings are stored 0–10 and sent as stars

Every log rating and current rating is stored on the 0–10 scale (SW-107, TV-504); stars are the stored value divided by 2.

An Item's **title rating** in a range is:

1. the rating of its newest rated visible log in the range, ordered by log day, then by ID; or
2. in All Time only, when it has no rated visible log, its current rating (`score` on the owner's tracking row: `Movie.score`, `TV.score`, `Season.score` and so on).

Episodes have no title rating in Stats.
Stored values imported off the scale are kept as they are (TV-504) and can produce stars with two decimals.

## Sections

### STAT-200: The header has four big numbers and a details list

The big numbers are `titles` (STAT-105), `logs` (STAT-103), `active_days` (days with at least one event) and `longest_daily_streak`.

The details list has these rows, in this order, when they apply:

| Key | Label | Where | Meaning |
| --- | --- | --- | --- |
| `current_daily_streak` | Current daily streak | every tab | Days in a row with an event, counting back from today, or from yesterday when today has none. Only when the range includes today. Unit `day`. |
| `longest_weekly_streak` | Longest weekly streak | every tab | The most weeks in a row with an event inside the range. Weeks start on the owner's `week_start_day`. Unit `week`. |
| `current_weekly_streak` | Current weekly streak | every tab | Weeks in a row with an event, counting back from this week, or from last week when this week has none. Only when the range includes today. Unit `week`. |
| `rewatches` | Repeats on Global; Rewatches (movie, TV, anime), Rereads (book, manga, comic), Replays (game), Relistens (music) | every tab | Rewatch logs in the range (STAT-108). |
| `reviews` | Reviews | every tab | Visible logs in the range with review text or a review title. |
| `likes` | Likes | every tab | Distinct titles with a liked visible log in the range. All Time adds titles with a current media like (`MediaLike`). |
| `days_with_two_plus_titles` | Days with 2+ titles | every tab | Days with events for two or more titles. |
| `episodes` | Episodes watched | TV | Viewings counted under STAT-104. |
| `seasons_finished` | Seasons finished | TV, All Time only | Seasons finished now under TV-200 (the stored season status is Completed). |
| `shows_finished` | Shows finished | TV, All Time only | Shows finished now under TV-201 (`TV.finished`), whatever status the user chose since. |
| `episodes` | Episodes watched | anime | The anime progress counted under STAT-201; approximate in a dated range. |
| `reads` | Reads | book | STAT-107. |
| `did_not_finish` | Did not finish | book | Reading journeys (`BookSession`) that ended Dropped with an end date in the range; every one in All Time. |
| `completions` | Completions | game | STAT-107. |
| `abandoned` | Abandoned | game | Playthroughs (`GameSession`) that ended Dropped with an end date in the range; every one in All Time. |

The app leaves out a current streak row (daily or weekly) whose value is 0.

The longest daily streak is the most days in a row with an event inside the range.
A detail whose value doesn't apply (a current streak in a range that doesn't include today) is left out of the list. A zero is sent as zero.

**Drills.** Each detail row has `drill`: the extra diary list parameters (STAT-400) that list what the row counts, as an object of string values, or null when the row has no diary view.
The app opens the diary with the scope's usual parameters (STAT-401) and applies `drill` on top; a key in `drill` replaces the scope parameter of the same name.

| Key | `drill` |
| --- | --- |
| `current_daily_streak` | `{"watched_from": <the streak's first day>, "watched_to": <its last day, today or yesterday>}`; null when the value is 0. |
| `longest_weekly_streak`, `current_weekly_streak` | `{"watched_from": <the first day of the streak's first week>, "watched_to": <the last day of its last week>}`, each clipped to the range (All Time has nothing to clip to); null when the value is 0. |
| `rewatches` | `{"repeat": "true"}` |
| `reviews` | `{"has_review": "true"}` |
| `likes` | `{"liked": "true"}` |
| `days_with_two_plus_titles` | `{"multi_title_days": "true"}` |
| Every per-type row (`episodes`, `seasons_finished`, `shows_finished`, `reads`, `did_not_finish`, `completions`, `abandoned`) | null: no diary filter matches it. |

When two streaks are equally long, the longest daily and weekly streaks are the later ones, as the busiest day is (STAT-203).
Drill-downs are the owner's only (STAT-401): every other viewer gets `drill` null on every row, with the same values.

`longest_daily_streak_range` holds the days of the longest daily streak, `{"start", "end"}`, or null when it is 0; the app's "Longest streak" number opens the diary on those days.
It is a fact like the streak itself, so every viewer gets it.

### STAT-201: Time is counted per type in that type's own unit

| Type | Unit | Dated range | All Time | Approximate |
| --- | --- | --- | --- | --- |
| Movie | minutes | Sum of `runtime_minutes` over visible logs in the range; each log counts, rewatches included. | The same over every visible log, plus each undated completed film once. | Never. |
| TV | minutes | Sum over dated viewings in the range of the episode's runtime, falling back to its season's, then its show's. | Every viewing, fills included. | When a season or show runtime stood in for an episode's own. |
| Anime | minutes | Sum of positive progress changes dated in the range, times the anime's per-episode runtime. | Sum of `progress` over every anime tracking row (one per watch-through), times the runtime. | Always in a dated range. |
| Manga | chapters | Sum of positive progress changes dated in the range. | Sum of `progress` over every manga tracking row. | Always in a dated range. |
| Comic | issues | As manga. | As manga. | Always in a dated range. |
| Game | minutes | Sum of `GameSession.total_minutes` for sessions whose `minutes_updated_on`, or else `end_date`, is in the range. | Sum over every session. | Always in a dated range. |
| Book | pages | Sum over visible book logs in the range of the pages in the completion snapshot, else the book's `total_pages`. | The same over every visible book log, plus `total_pages` once for each book with `completed_manually`. | Never. |
| Music | minutes | Sum of the album's runtime over visible logs in the range. | The same over every visible log, plus each undated completed album once. | Never. |

A progress change is the difference between one history row (`HistoricalAnime`, `HistoricalManga`, `HistoricalComic`) and the previous row of the same tracking row, ordered by `history_date`, dated by its `history_date` on the owner's calendar.
Only increases count. The row that created the tracking row is never a change, so an imported row adds nothing to the year it was imported.
Steam's imported lifetime playtime is never added (GM-500).
Each row reports `known` and `total`: how many of the things it counted (logs, viewings, tracking rows or sessions, plus undated completions) had a length, out of all of them.

### STAT-202: Total hours adds up the types counted in minutes

On Global, `hours_total` adds the minutes of movies, TV, anime, games and music.
It never includes books, manga or comics, which aren't counted in minutes.
`media_types` lists the exposed types it adds and `leaves_out` the exposed types it doesn't, each in `primary_media_types()` order; both depend only on which types the server exposes, never on the viewer or the data.
It is approximate when any part is.
The app builds its note from `leaves_out`, for example "Doesn't include books, manga or comics".

### STAT-203: Activity shows events by month and weekday

- **Months.** A calendar-year range sends its 12 months. Any other range, All Time included, sends every month from the first month with an event to the last, with empty months as zero. Each month carries its event count split by type bucket.
- **Weekdays.** Seven event counts, Monday first, with the owner's `week_start_day` so the app can reorder them.
- **Busiest day and busiest month.** The day and the month with the most events; a tie goes to the later one.

There is no daily heatmap.

### STAT-204: The ratings chart counts rated logs in ten buckets

The distribution counts rated visible logs in the scope, the same rows a rating drill-down returns (STAT-401).
A log falls in the bucket for `s` stars when its stored rating is in `[2s − 0.5, 2s + 0.5)`, except that 0.5★ is `[0, 1.5)` and 5★ is `[9.5, 10]`.
Because stored ratings have one decimal place, each bucket has inclusive stored bounds:

| Stars | 0.5 | 1.0 | 1.5 | 2.0 | 2.5 | 3.0 | 3.5 | 4.0 | 4.5 | 5.0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `rating_min` | 0.0 | 1.5 | 2.5 | 3.5 | 4.5 | 5.5 | 6.5 | 7.5 | 8.5 | 9.5 |
| `rating_max` | 1.4 | 2.4 | 3.4 | 4.4 | 5.4 | 6.4 | 7.4 | 8.4 | 9.4 | 10.0 |

`average_stars` is the average stored rating of the same logs, divided by 2.
`rated` is the number of those logs.

### STAT-205: Highest rated lists Items by their title rating

Highest rated holds every Item in the scope with a title rating (STAT-109), one row per Item:

- seasons are rows of their own, labelled "Show S2";
- episodes are left out; and
- in All Time, Items rated but never logged appear through their current rating, for every viewer (STAT-002).

Rows are sorted by stars, highest first, then by the rating's log day, newest first, with current-rating rows after logged rows of the same stars, then by Item ID.
The section sends the first 12 rows and the total; the show-all endpoint pages through the rest (STAT-310).
The app always shows the Highest rated section when it has rows, just above Most logged, on Global and type tabs alike. When Top comes from a list, Highest rated leaves out the Items Top shows (its 12 rows, its total and the show-all endpoint alike), so the two never repeat each other.

### STAT-206: Top reads one tag from the owner's ranked lists

- **The tag.** A list tag names a Top list when, after `casefold().strip()`, it matches `^top[\s_-]*(?:(\d{4})|all[\s_-]*time)$`. So "top2025", "Top 2025", "top-2025", "topalltime" and "TOP all time" match, and "top20255" and "top 25" don't.
- **The wanted tag.** `top{YEAR}` for a calendar-year range and `topalltime` for All Time. Any other range has no Top tag (STAT-208).
- **Candidates.** `CustomList` rows with `owner` = the owner, `is_featured` false, `list_type` media, a matching tag, and at least one exposed item. The owner's own view considers every visibility; every other viewer only `visibility=public`. Lists the owner only collaborates on are never candidates.
- **Ranked.** A candidate counts only when it is ranked: `is_ranked` is true, or any of its items has a `position` (Letterboxd imports keep positions without setting `is_ranked`).
- **Tab.** A list whose exposed items all fall in one type bucket feeds that type's tab. A list with two or more buckets feeds Global.

### STAT-207: The newest ranked list wins and shows its first ten items

When several candidates fit a tab, the one with the newest `updated_at` wins, then the one with the highest ID.
Top shows the winner's first 10 exposed items in list order (position, nulls last, then date added, then ID), the order of `api.views.featured.preview_items`.
Each item carries its rank from 1 to 10 and its title rating in the scope (STAT-109), or none.
`list.total` is the number of exposed items in the list, for "See full list".
When the winner isn't public, the owner gets `hint_private: true`, shown as "Only you can see this list."

### STAT-208: Without a list, Top shows Highest rated and tells the owner how to tag one

When no candidate fits, or the range has no Top tag, Top's items are the first 10 rows of Highest rated, ranked 1 to 10, and `list` is null.
For a calendar-year or All Time range, the owner also gets `tag_hint`:

- on Global: "Tag a ranked list with mixed media top2025";
- on a type tab: "Tag a ranked movie list top2025", with the type's noun: movie, TV, anime, manga, game, book, comic or music.

The tag in the hint is the wanted tag, so All Time reads "topalltime".
Other viewers, and other ranges, get no hint.

The app doesn't draw these fallback items: without a list, Top is an empty card that shows the owner's hint ("Tag a ranked movie list top2025 to show it here."), and other viewers see no Top section. Highest rated keeps its own place lower on the page.

### STAT-209: Release years count titles by the year they came out

Every release year with at least one title in the scope is its own row; the server never folds years into decades.
Each row counts its titles, its visible logs in the range, how many of those logs are rated, their average stars, and its titles per type bucket.
A season or episode row uses its show's year.
Titles with no release year are left out of the rows and counted in `coverage`.

**New vs older** sorts the scope's logged titles by how old they were when first logged in the range:

- `new`: released the same calendar year as that log day, or later;
- `recent`: released 1 to 7 years earlier;
- `older`: released 8 or more years earlier.

Titles without a release year, and undated completions, aren't counted there.

### STAT-210: A type tab shows that type's own genre names

On a type tab, genres are the `genre` facet values of the scope's titles, in the provider's own words: TMDB's for movies and TV, MAL's or MangaUpdates' for anime and manga, IGDB's genres (not its themes) for games, Hardcover's or Open Library's for books, ComicVine's for comics, and MusicBrainz's for music.

- Spellings that differ only in case are one row, named by the most common stored spelling (ties: the first alphabetically). `values` lists every stored spelling in the row.
- A show counts once: season and episode rows count through the show's genres.
- Each row counts its titles, the rated visible logs of those titles in the range, and their average stars.

Rows are sorted by titles, most first, then by name. At most 50 are sent; `total` is how many there are.
The app's order control sorts these rows itself: its first segment is named for the tab ("Most watched" for movies, TV and anime, "Most read" for books, manga and comics, "Most played" for games, "Most listened" for music, "Most titles" on Global), the second is "Highest rated"; "Highest rated" ranks rows with at least 3 rated logs by average stars, then by titles.

### STAT-211: Global genres use 15 shared groups, with music kept apart

On Global, every non-music title's `genre` and `theme` facet values are mapped to these groups by `api.services.genre_groups.groups_for(media_type, value)`:

| Key | Label |
| --- | --- |
| `drama` | Drama |
| `comedy` | Comedy |
| `action_adventure` | Action & Adventure (includes Western) |
| `scifi_fantasy` | Sci-Fi & Fantasy |
| `horror` | Horror |
| `thriller_crime` | Thriller & Crime |
| `mystery` | Mystery |
| `romance` | Romance |
| `documentary_nonfiction` | Documentary & Nonfiction (includes Reality, Talk and News) |
| `family_kids` | Family & Kids |
| `history_war` | History & War |
| `slice_of_life` | Slice of Life |
| `sports` | Sports |
| `music_musicals` | Music & Musicals |
| `strategy_puzzle` | Strategy & Puzzle |

The label sent is the plain name without the parenthesis.
There is no Animation group.
A title counts once in a group however many of its values reach it, and may count in several groups.
Each group row counts its titles, split per type bucket, and the rated visible logs of those titles with their average stars.
Every group with at least one title is sent, sorted by titles, then label.
Music never reaches a group. Music titles' genres are listed separately in `music`: the top 5 by titles, case variants merged as in STAT-210.
`coverage` counts the non-music titles that reach at least one group, out of all non-music titles.

### STAT-212: Languages count each movie and show once, by original language

The Languages card uses the `original_language` facet (`ItemFilterFacet.FacetType.ORIGINAL_LANGUAGE`): one English language name per title, the title's original language.
The name comes from TMDB's `original_language` code, named through the title's `spoken_languages`, then a static ISO 639-1 table; a code neither names is stored as the code itself.
It never reads the `language` facet, which the library and diary filters keep using.

- **Movie and TV tabs:** that type's titles, one row per language. Season and episode rows count through the show.
- **Global:** movies and TV together, with `media_types` naming them so the app can say "From movies and TV".
- **Other tabs:** no Languages section.

Each row counts its titles, split per type bucket, and the rated visible logs of those titles with their average stars.
Rows are sorted by titles, then name; at most 50 are sent, and `total` is how many there are.
The card has the same order control as genres, with the same labels: "Most watched" on the movie and TV tabs, "Most titles" on Global (STAT-210).

### STAT-213: List progress rings use Spine's own lasting featured lists

A featured list shows a ring when:

- it is public, `is_featured`, and a media list;
- its definition's `kind` is award, canon, community, curator or editorial; a featured list with no definition and no sync counts as editorial;
- its definition has no live windows;
- it isn't retired or partial (`api.views.featured.is_retired`, `is_partial`);
- its key isn't in `RING_EXCLUDED_KEYS`: `mal-most-popular-anime`, `mal-most-popular-manga`, `igdb-playing` and `igdb-want-to-play`, which rank popularity, not lasting merit; and
- the owner has seen at least 2 of its items.

An item is **seen** when the owner's latest tracking row for it is Completed, or the owner has a visible log of that exact Item.
`total` counts the list's distinct exposed items.
A ring's type is its definition's single media type, or, for a list with no definition, the single type bucket of its items; otherwise it has none.
Global shows every ring; a type tab shows the rings of its type.
`seen` and `total` are lifetime in every range.

A title is **first seen** on its earliest dated day: the earliest log day of its visible logs, and for TV the earliest of those and its dated viewing days that aren't hidden (STAT-104). Titles fold to their show (STAT-102), so a season or episode list item uses its show's day.
Undated completions (STAT-105) and a mark's fills have no day, so they never count in a dated range.

- **All Time** sends every qualifying ring with `seen_in_range` null, sorted by the seen share, highest first, then by seen, then by name.
- **A dated range** adds `seen_in_range`: the ring's seen items whose title was first seen in the range. Only rings with `seen_in_range` of 1 or more are sent, sorted by `seen_in_range`, most first, then by the seen share, seen and name. The 2-seen minimum still applies, and every viewer counts the logs and viewings they can see (STAT-003).

Every qualifying ring is sent; the app shows 6, then "Show all".

### STAT-214: Collections show complete and almost complete series

Collections appear on the movie, game, book and anime tabs, from stored `MediaSeries` of that type:

- A member counts when it has been released: its release date is on or before today on the owner's calendar, or it has no release date and its source isn't TMDB. A TMDB member with no date isn't out yet.
- A series counts when it has at least 2 released members and the owner has seen at least one (seen as in STAT-213).
- **Complete:** every released member is seen.
- **Almost complete:** 1 or 2 released members are unseen, and more than half are seen. So 2 of 3 and 18 of 20 count; 1 of 3 and 1 of 2 don't.

Complete series are sorted by size, largest first, then by name; almost complete ones by fewest left, then largest, then name.
The section sends both counts and the first 4 of each; the show-all endpoint pages through the rest (STAT-310).
Complete and almost complete are lifetime: every range shows the same ones.

**Progress in a range.** A dated range adds `in_range`; All Time sends it null. It uses STAT-213's first-seen rule, over a series' released members that are seen:

- **Completed in the range:** the series is complete now, and its last member to be seen was first seen in the range: at least one member was first seen in the range and none after it. A member seen only undated came before every dated one, so it never moves the completion later.
- **Progressed in the range:** every other series with at least one member first seen in the range, complete or not; for example, a series whose last member was first seen after the range progressed in it.
- Each row adds `seen_in_range`, its seen members first seen in the range.

Both groups are sorted by `seen_in_range`, most first, then by size, largest first, then by name.
The section sends both counts and the first 4 of each; the show-all endpoint pages through the rest (STAT-310).
In a dated range the app shows `in_range` instead of the complete and almost complete groups.

### STAT-215: Most logged lists titles logged at least twice

A title is in Most logged when it has 2 or more visible logs in the scope; `times` is that count.
For TV, `times` is the most viewings of any one episode of the show in the scope (STAT-104), and the show is in the list when that is 2 or more.
Rows are sorted by `times`, most first, then by the newest log day, then by Item ID.
The label is "Most watched" for movies, TV and anime, "Most read" for books, manga and comics, "Most played" for games, "Most listened" for music, and "Most logged" on Global.
The section sends the first 12 rows and the total; the show-all endpoint pages through the rest (STAT-310).

### STAT-216: Milestones mark the 1st, 100th, 250th, 500th, 750th and 1,000th log, then every 500th

Visible logs are numbered in log-day order, then by ID: all of them on Global, that type bucket's on a type tab.
All Time numbers every log the viewer can see. A dated range numbers only its own logs, starting again at 1 with its first log, so a year pill shows that year's 1st, 100th and so on.
The milestones are numbers 1, 100, 250, 500, 750 and 1,000, then 1,500, 2,000 and every 500 after.
Each milestone shows the title's poster (TV as its show), the number and the day, for example "100th log · 12 Mar 2024", or in a year "100th log of 2025".
Each viewer sees milestones numbered over the logs they can see (STAT-003).

### STAT-217: Rated higher than most compares your stars with everyone's

The candidates are the Highest rated rows of the scope (STAT-205), with the same title ratings.
Each is compared with one community rating from `ExternalRating`:

- **Source.** Each type uses one source: the first in its list below that the server exposes (`rating_source_is_exposed`) and that has a usable rating for at least one candidate of that type in the scope. Candidates without a usable rating from that source are left out.

  | Type | Sources, in order |
  | --- | --- |
  | Movie | letterboxd, imdb, tmdb |
  | TV (shows and seasons) | imdb, tmdb |
  | Anime | mal |
  | Manga | mal |
  | Game | igdb |
  | Book | hardcover |
  | Music | musicbrainz |
  | Comic | none |

- **Usable.** `status` is available, `value` and `max_value` are set, `max_value` is above 0, and `vote_count` is at least 50 when it is stored.
- **Everyone's stars.** `value / max_value × 5`.
- **Difference.** Your stars minus everyone's stars as sent (rounded to one decimal, STAT-303), so a row never shows a tie such as "4.5" against "4.5".
- **Big enough to count.** A title is higher or lower than most only when the difference is at least 0.5 stars. Ties and smaller gaps are in neither list.

`higher` lists the 5 largest positive differences and `lower` the 5 largest negative ones, each sorted by the size of the difference, then by Item ID.
Each row shows the poster, the title, your stars, then "Everyone 3.4".
The section is titled "Rated higher than most", with a segmented Higher / Lower control.

### STAT-218: Each tab has a fixed set of sections, in a fixed order

| Section (response key) | Global | Type tab |
| --- | --- | --- |
| Header (`header`) | without per-type details | with that type's details |
| Time (`time`) | every type, plus total hours | that type's row only |
| Top (`top`) | mixed lists | single-type lists |
| Highest rated (`highest_rated`) | yes | yes |
| Activity (`activity`) | yes | yes |
| Ratings (`ratings`) | yes | yes |
| Rated higher than most (`rated_vs_everyone`) | yes | yes |
| Release years (`release_years`) | yes | yes |
| Genres (`genres`) | 15 groups, plus music | that type's genre names |
| Languages (`languages`) | movies and TV | movie and TV tabs only |
| People (`people`) | no | movie, TV, music and book tabs only (STAT-219) |
| List progress (`list_rings`) | every ring | that type's rings |
| Collections (`collections`) | no | movie, game, book and anime tabs only |
| Most logged (`most_logged`) | yes | yes, labelled per type |
| Milestones (`milestones`) | yes | yes |

The app shows them in this order:

- **Global:** Header, Time, Top, Activity, Ratings, Rated higher than most, Release years, Genres and Music genres, Languages, List progress, Highest rated (only when Top is a list), Most logged, Milestones.
- **Type tab:** Header (its third big number is the tab's own measure from `time`, and Active days is the first detail row), Top, Ratings, Rated higher than most, Release years, Genres, Languages, People, Activity, List progress, Collections, Highest rated (only when Top is a list), Most watched / read / played / listened, Milestones. The Time card is Global-only; a type tab still receives `time` for its header.

The app hides an empty section (STAT-302).

### STAT-219: People lists who you logged most, per role

People is a section of its own, in the response key `people`, on the tabs STAT-218 lists.

- **Tabs and groups.** Movies: Directors, then Cast. TV: Cast, then Creators. Music: Artists. Books: Authors. `people` is null on Global and on the anime, manga, game and comic tabs (STAT-302). On its tabs every group is always sent, with empty `rows` and `rated_rows` when nothing in the scope credits anyone.
- **Credits.** `app.models.ItemCredit` holds each title's people, stored by `store_item_metadata` from the provider metadata it already gets, never from an extra call:
  - movies (TMDB): the directors and the top 15 billed cast;
  - TV shows (TMDB): the creators and the top 15 cast of the show's own credits; seasons and episodes have none;
  - albums: the album artists, by Apple Music artist ID or MusicBrainz artist MBID; Various Artists and MusicBrainz's [unknown] are no one;
  - books: the authors, by Hardcover or Open Library author ID.

  A person is a source and an ID, so one person known to two sources is two rows. A role is replaced whenever the metadata carries its list, and kept when the list is missing.
- **Counting.** A person's `titles` are the scope's titles (STAT-105) that credit them in the role: a season or episode counts through its show's credits (STAT-102), and a title counts once however many logs it has. `rated` is how many of those titles have a title rating (STAT-109: the rating Highest rated uses, STAT-205; for a show its own rating, not its seasons') and `average_stars` is their average, null when none is rated.
- **Visibility.** People count titles like every title number: other viewers lose only titles reached through logs they can't see (STAT-003). Undated completions and current ratings count for everyone (STAT-002).
- **Order.** Rows are sorted by titles, most first, then by average stars (none last), then by name. Each group sends its first 10 rows; `total` is how many people the group has.
- **Highest rated.** The card has an order control like genres' (STAT-210): its first segment is named for the tab ("Most watched" for movies and TV, "Most listened" for music, "Most read" for books) and shows `rows`; the second is "Highest rated" and shows `rated_rows`: the people with at least 3 rated titles, sorted by average stars, highest first, then by titles, then by name, the first 10 of them. They needn't be among the 10 with the most titles. Show-all pages through either order (`sort=titles` or `sort=rating`).
- **Freshness.** Credits change only when metadata is stored: by the metadata fill, whose finished chain bumps the stats epoch (STAT-006), or by a detail page, which shows within the hour.

```json
"people": {
  "groups": [
    {
      "role": "director",
      "label": "Directors",
      "rows": [
        {
          "person": { "source": "tmdb", "id": "525", "name": "Christopher Nolan", "image_url": "https://image.tmdb.org/t/p/w500/xuAIuYSmsUzKlUMBFGVZaWsY3DZ.jpg" },
          "titles": 9,
          "rated": 8,
          "average_stars": "4.19"
        }
      ],
      "rated_rows": [
        {
          "person": { "source": "tmdb", "id": "1032", "name": "Martin Scorsese", "image_url": null },
          "titles": 4,
          "rated": 4,
          "average_stars": "4.75"
        }
      ],
      "total": 412
    },
    { "role": "cast", "label": "Cast", "rows": [ ], "rated_rows": [ ], "total": 0 }
  ]
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `groups[]` | array | The tab's groups, in the order above. |
| `groups[].role` | string | `director`, `cast`, `creator`, `artist` or `author`. |
| `groups[].label` | string | "Directors", "Cast", "Creators", "Artists" or "Authors", ready to show. |
| `groups[].rows[]` | array, at most 10 | One person each, by titles (the Order rule above). |
| `groups[].rated_rows[]` | array, at most 10 | The same row shape, Highest rated: people with at least 3 rated titles, by average stars, then titles, then name. |
| `rows[].person` | object | `source` (`tmdb`, `apple_music`, `musicbrainz`, `hardcover` or `openlibrary`) and `id` (a string) name the person, for the drill-down and person pages; `name`; `image_url`, the provider's picture or null. |
| `rows[].titles` | int | Distinct titles in the scope that credit them. |
| `rows[].rated` | int | Those with a title rating. |
| `rows[].average_stars` | string or null | The average of those ratings as stars (STAT-303). |
| `groups[].total` | int | People in the group, before the 10-row cap. |

**Show-all.** `GET stats/me/people/` and `GET users/{username}/stats/people/` page through one group with STAT-310's pagination and gate:

| Parameter | Meaning |
| --- | --- |
| `media_type` | Required: `movie`, `tv`, `music` or `book`. |
| `role` | Required: one of that tab's roles. |
| `start_date`, `end_date` | As STAT-300. |
| `sort` | `titles` (the default, the section's order) or `rating` (Highest rated: at least 3 rated titles, by average stars, then titles, then name). |

`results[]` are rows as above; `count` is how many people the order lists. A missing or wrong `media_type`, `role` or `sort` is a 400 under that key (STAT-311).

**Drill-down.** `GET diary/?credit=<source>:<id>` (repeatable; any of them matches) keeps the owner's logs of titles that credit that person; `role=<role>` (repeatable) narrows it to those roles. With `tv_rollup=true` a season or episode log matches through its show's credits. A row sends `credit=<person.source>:<person.id>&role=<group.role>` with STAT-401's usual parameters: `skip_backfill=true`, `tv_rollup=true`, the range's dates and the tab's media types (for TV `media_type=tv&media_type=season&media_type=episode`).

**What the credits cost to fill.** Storing credits bumped `CURRENT_METADATA_VERSION` for movies and TV to 3, music to 3 and books to 4, so the metadata fill visits every movie, show, album and book anyone tracks or logged once more:

- a movie is one TMDB detail fetch (about 2 calls with its collection), under the fill's overall cap of 2 calls a second;
- a show is one TMDB call (its seasons are already current); the show's cast now comes with every season-batched request too;
- an Apple Music album is one background call (1 a second), now with its artists;
- a MusicBrainz album that has its length is one call without the tracklist enrichment, spaced 3 s apart; one without its length still takes the full fetch;
- Hardcover books come 50 to a batch call, which now asks for their authors; an Open Library book is its usual fetch of about 4 calls (10 a minute, 1,000 calls a day);
- an album or book that has everything but its credits is fetched instead of only stamped.

With about 3,000 distinct films that is roughly 6,000 TMDB calls, about 50 minutes, plus a few minutes for shows and the music and book time above. `fill_item_metadata` without `--apply` prints the exact counts and minutes for the data at hand.

## API (v2)

All endpoints are under `/api/v1/` and require authentication.

### STAT-300: The request asks for schema 2

```
GET /api/v1/stats/me/summary/?schema=2&start_date=2025-01-01&end_date=2025-12-31[&media_type=movie]
GET /api/v1/users/{username}/stats/summary/?schema=2&start_date=…&end_date=…[&media_type=…]
```

- `schema=2` returns the v2 payload. Without it, or with any other value, the server returns the legacy payload (STAT-900).
- `start_date` and `end_date` are ISO dates on the owner's calendar. `start_date=all&end_date=all` asks for All Time; both must say `all`. A missing `start_date` defaults to one year before today and a missing `end_date` to today, on the owner's calendar, as `parse_stats_range` does today.
- `media_type` is an optional primary media type and selects a type tab. Without it the response is Global. `season`, `episode` and unexposed types are rejected.
- The `users/{username}/` form applies STAT-002's gate.

### STAT-301: Every v2 response has the same top-level keys

```json
{
  "schema_version": 2,
  "range": { "start_date": "2025-01-01", "end_date": "2025-12-31", "timezone": "America/New_York", "is_all_time": false },
  "media_type": null,
  "audience": "owner",
  "years": { "first": 2014, "current": 2026 },
  "media_types": [
    { "media_type": "movie", "titles": 120, "logs": 151 },
    { "media_type": "tv", "titles": 18, "logs": 40 }
  ],
  "header": { },
  "time": { },
  "top": { },
  "highest_rated": { },
  "activity": { },
  "ratings": { },
  "rated_vs_everyone": { },
  "release_years": { },
  "genres": { },
  "languages": { },
  "list_rings": [ ],
  "collections": null,
  "most_logged": { },
  "milestones": [ ]
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | int | Always `2`. The legacy payload sends `1`. |
| `range.start_date`, `range.end_date` | string or null | The range's dates; both null in All Time. |
| `range.timezone` | string | The owner's calendar (STAT-005). |
| `range.is_all_time` | bool | |
| `media_type` | string or null | The tab: null on Global. |
| `audience` | string | `owner`, `follower` or `public`. |
| `years.first` | int or null | The year of the earliest log day or dated viewing the viewer can count, of any type; null when there is none. |
| `years.current` | int | Today's year on the owner's calendar. |
| `media_types[]` | array | One row per exposed primary type the owner has turned on in Media & Preferences, in `primary_media_types()` order, whatever the tab, with that type's `titles` (STAT-105) and `logs` (STAT-103) in the range. The app builds its type picker from it. A type the owner turned off has no row, yet Global still counts it. |

Every section key is always present; STAT-302 says when it is null.

### STAT-302: Null means "not on this tab"; empty means "nothing yet"

- A section is `null` only when it doesn't belong to the tab under STAT-218: `collections` on Global and on the TV, manga, comic and music tabs; `languages` on tabs other than Global, movie and TV, and on Global when neither movies nor TV are exposed; `time.hours_total` on type tabs.
- A section is never null because of who is asking (STAT-002). Owner-only fields inside `top` are `null` or `false` for other viewers (STAT-004).
- A section that belongs to the tab but has no data is empty, and the app hides it:

  | Section | Empty when |
  | --- | --- |
  | `header` | `titles` and `logs` are 0 |
  | `time` | `rows` is empty |
  | `top`, `highest_rated`, `most_logged` | `items` is empty |
  | `activity` | `busiest_day` is null |
  | `ratings` | `rated` is 0 |
  | `rated_vs_everyone` | `higher` and `lower` are empty |
  | `release_years`, `languages` | `rows` is empty |
  | `genres` | `rows` and `music` are empty |
  | `list_rings`, `milestones` | the array is empty |
  | `collections` | both counts are 0; in a dated range, both `in_range` counts are 0 (STAT-214) |

- Inside a section, a value with nothing to measure is null, such as `average_stars` with no rated logs.

### STAT-303: Stars are decimal strings

- **Stars** (`stars`, `average_stars`) are strings: the stored value divided by 2, rounded half up to two decimals, with trailing zeros dropped down to one decimal: `"4.0"`, `"3.5"`, `"3.65"`. Log and title stars run from `"0.5"` to `"5.0"` in half steps, except imported off-scale values (STAT-109).
- **Everyone's stars and differences** are strings rounded half up to one decimal: `"3.4"`, `"-1.1"`.
- **Stored bounds** (`rating_min`, `rating_max`) are strings with one decimal on the stored 0–10 scale: `"6.5"`, `"7.4"`.
- **Days** are `"YYYY-MM-DD"` and months `"YYYY-MM"`.
- **Counts and minutes** are integers.

### STAT-304: Titles are sent as media summaries with the owner's artwork

Every `media` field is the media summary from `api.serializers.common.media_summary_from_item(item, request=request, user=owner, include_user_state=False)`, with the Items loaded and primed for the owner through `prime_collection_items(items, owner)`: the same object the legacy stats payload sends today through `stats._items_with_owner_artwork` and `stats._serialize_ranked_rows` (iOS `MediaSummary`).
It carries the owner's custom poster and backdrop, never anyone's tracking state, and makes no provider call.

```json
{
  "ref": { "item_id": 812, "source": "tmdb", "media_type": "movie", "media_id": "693134", "season_number": null, "episode_number": null },
  "title": "Dune: Part Two",
  "subtitle": null,
  "overview": null,
  "image_url": "https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg",
  "poster_url": "https://image.tmdb.org/t/p/w500/1pdfLvkbY9ohJlCjQH2CZjjYVvJ.jpg",
  "backdrop_url": null,
  "poster_aspect_ratio": null,
  "poster_width": null,
  "poster_height": null,
  "poster_orientation": null,
  "poster_accent_color": "#2A1F14",
  "release_date": null,
  "default_source": "tmdb",
  "custom_poster_url": null,
  "custom_backdrop_url": null,
  "user_state": null
}
```

A season's summary has `ref.media_type` `season` and its `season_number`; the app labels it with `MediaRef.displayTitle` ("Show S2").
Examples below write a media summary as `{…}`.

### Section shapes

#### `header`

```json
"header": {
  "titles": 212,
  "logs": 268,
  "active_days": 141,
  "longest_daily_streak": 9,
  "longest_daily_streak_range": { "start": "2026-03-02", "end": "2026-03-10" },
  "details": [
    { "key": "current_daily_streak", "label": "Current daily streak", "value": 3, "unit": "day", "approximate": false,
      "drill": { "watched_from": "2026-10-24", "watched_to": "2026-10-26" } },
    { "key": "longest_weekly_streak", "label": "Longest weekly streak", "value": 21, "unit": "week", "approximate": false,
      "drill": { "watched_from": "2026-02-02", "watched_to": "2026-06-28" } },
    { "key": "current_weekly_streak", "label": "Current weekly streak", "value": 4, "unit": "week", "approximate": false,
      "drill": { "watched_from": "2026-10-05", "watched_to": "2026-11-01" } },
    { "key": "rewatches", "label": "Repeats", "value": 17, "unit": null, "approximate": false, "drill": { "repeat": "true" } },
    { "key": "reviews", "label": "Reviews", "value": 46, "unit": null, "approximate": false, "drill": { "has_review": "true" } },
    { "key": "likes", "label": "Likes", "value": 58, "unit": null, "approximate": false, "drill": { "liked": "true" } },
    { "key": "days_with_two_plus_titles", "label": "Days with 2+ titles", "value": 38, "unit": null, "approximate": false,
      "drill": { "multi_title_days": "true" } }
  ]
}
```

`longest_daily_streak_range`: `{start, end}` days of the longest daily streak, or null when it is 0 (STAT-200).
`details[]`: `key` (string), `label` (string, ready to show), `value` (int), `unit` (`"day"`, `"week"` or null; the app pluralizes), `approximate` (bool; the app shows "≈"), `drill` (object of string diary parameters, or null; always null for other viewers, STAT-200).
The app shows each detail as a label/value row and must not depend on which keys are present.
On the TV tab in All Time, for example, the list continues with `episodes`, `seasons_finished` and `shows_finished` (STAT-200), each with `drill` null.

#### `time`

```json
"time": {
  "rows": [
    { "media_type": "movie", "unit": "minutes", "value": 18240, "known": 148, "total": 151, "approximate": false },
    { "media_type": "tv", "unit": "minutes", "value": 20400, "known": 1100, "total": 1204, "approximate": true },
    { "media_type": "book", "unit": "pages", "value": 6120, "known": 18, "total": 19, "approximate": false }
  ],
  "hours_total": { "minutes": 38640, "media_types": ["movie", "tv", "anime", "game", "music"], "leaves_out": ["manga", "book", "comic"], "approximate": true }
}
```

`rows[]`: `media_type`, `unit` (`"minutes"`, `"pages"`, `"chapters"` or `"issues"`), `value`, `known`, `total`, `approximate` (STAT-201).
Rows come in `primary_media_types()` order, only for types with something counted (`total` above 0). A type tab sends at most its own row.
`hours_total` (STAT-202) is null on type tabs.

#### `top`

From a list:

```json
"top": {
  "source": "list",
  "tag": "top2025",
  "tag_hint": null,
  "hint_private": false,
  "list": { "id": 81, "name": "My 2025", "total": 24 },
  "items": [
    { "rank": 1, "media": {…}, "stars": "5.0" },
    { "rank": 2, "media": {…}, "stars": null }
  ]
}
```

The fallback, as the owner sees it:

```json
"top": {
  "source": "highest_rated",
  "tag": "top2024",
  "tag_hint": "Tag a ranked movie list top2024",
  "hint_private": false,
  "list": null,
  "items": [ { "rank": 1, "media": {…}, "stars": "5.0" } ]
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `source` | string | `"list"` or `"highest_rated"` (STAT-207, STAT-208). |
| `tag` | string or null | The wanted tag; null for a range that has none. |
| `tag_hint` | string or null | Owner only, fallback only (STAT-208). |
| `hint_private` | bool | Owner only: the winning list isn't public (STAT-207). |
| `list` | object or null | `{id, name, total}` of the winning list; null in the fallback. |
| `items[]` | array, at most 10 | `rank` (1–10), `media`, `stars` (the title rating, or null). |

#### `highest_rated`

```json
"highest_rated": {
  "total": 140,
  "items": [
    { "media": {…}, "stars": "5.0", "day": "2025-03-02" },
    { "media": {…}, "stars": "4.5", "day": null }
  ]
}
```

`items[]` (at most 12): `media`, `stars`, `day` (the rating log's day; null for a current rating, STAT-109). `total` counts every row (STAT-205).

#### `activity`

```json
"activity": {
  "months": [
    { "month": "2025-01", "count": 14, "by_type": { "movie": 9, "tv": 5 } },
    { "month": "2025-02", "count": 0, "by_type": {} }
  ],
  "weekdays": [31, 18, 22, 19, 25, 40, 44],
  "week_starts_on": "monday",
  "busiest_day": { "date": "2025-08-02", "count": 6 },
  "busiest_month": { "month": "2025-08", "count": 31 }
}
```

`months[]`: `month`, `count` (events), `by_type` (events per type bucket; types with none are left out).
`weekdays`: seven event counts, Monday first, whatever `week_starts_on` says (`"monday"` or `"sunday"`).
`busiest_day` and `busiest_month` are null when there are no events (STAT-203).

#### `ratings`

```json
"ratings": {
  "average_stars": "3.65",
  "rated": 140,
  "distribution": [
    { "stars": "0.5", "count": 0, "rating_min": "0.0", "rating_max": "1.4", "by_type": {} },
    { "stars": "3.5", "count": 37, "rating_min": "6.5", "rating_max": "7.4", "by_type": { "movie": 30, "tv": 7 } },
    { "stars": "5.0", "count": 12, "rating_min": "9.5", "rating_max": "10.0", "by_type": { "movie": 12 } }
  ]
}
```

`distribution` always has the ten buckets of STAT-204, from `"0.5"` to `"5.0"`; the example shows three.
`by_type` counts rated logs per type bucket, leaving out types with none.
`average_stars` is null when `rated` is 0.

#### `rated_vs_everyone`

```json
"rated_vs_everyone": {
  "sources": [
    { "media_type": "movie", "source": "letterboxd", "label": "Letterboxd" },
    { "media_type": "tv", "source": "imdb", "label": "IMDb" }
  ],
  "higher": [
    { "media": {…}, "stars": "4.5", "everyone_stars": "3.1", "difference": "1.4", "source": "letterboxd" }
  ],
  "lower": [
    { "media": {…}, "stars": "2.0", "everyone_stars": "4.2", "difference": "-2.2", "source": "imdb" }
  ]
}
```

`sources[]`: the source each type used (STAT-217), with its display label (`app.external_ratings.source_label`), only for types with at least one compared row.
`higher[]` and `lower[]` (at most 5 each): `media`, `stars` (yours), `everyone_stars`, `difference`, `source`.

#### `release_years`

```json
"release_years": {
  "rows": [
    { "year": 1999, "titles": 4, "logs": 5, "rated": 3, "average_stars": "4.0", "by_type": { "movie": 3, "tv": 1 } },
    { "year": 2024, "titles": 31, "logs": 33, "rated": 30, "average_stars": "3.45", "by_type": { "movie": 25, "anime": 6 } }
  ],
  "coverage": { "known": 110, "total": 120 },
  "new_vs_older": { "new": 12, "recent": 40, "older": 58 }
}
```

`rows[]` are sorted by year, oldest first, one per year with titles (STAT-209); `by_type` counts titles.
`coverage.known` counts the scope's titles with a release year, out of `coverage.total`.
The app folds years into decades itself.

#### `genres`

Global:

```json
"genres": {
  "rows": [
    { "key": "scifi_fantasy", "name": "Sci-Fi & Fantasy", "values": null, "titles": 41, "rated": 33, "average_stars": "3.8", "by_type": { "movie": 20, "tv": 6, "anime": 9, "game": 6 } }
  ],
  "total": 13,
  "coverage": { "known": 380, "total": 412 },
  "music": [ { "name": "shoegaze", "values": ["shoegaze", "Shoegaze"], "titles": 7 } ]
}
```

A type tab:

```json
"genres": {
  "rows": [
    { "key": null, "name": "Sci-Fi", "values": ["Sci-Fi", "Sci-fi"], "titles": 22, "rated": 25, "average_stars": "3.9", "by_type": { "manga": 22 } }
  ],
  "total": 19,
  "coverage": { "known": 60, "total": 64 },
  "music": []
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `rows[].key` | string or null | The group key on Global (STAT-211); null on type tabs. |
| `rows[].name` | string | The group label, or the genre's shown spelling (STAT-210). |
| `rows[].values` | array of strings or null | Type tabs: every stored spelling in the row, for the drill-down. Null on Global. |
| `rows[].titles` | int | Distinct titles. |
| `rows[].rated` | int | Rated visible logs of those titles in the range. |
| `rows[].average_stars` | string or null | Their average stars. |
| `rows[].by_type` | object | Titles per type bucket. |
| `total` | int | Rows there are before the 50-row cap. |
| `coverage` | object | `known` titles that reach a row, out of `total` titles. On Global both leave out music. |
| `music` | array | Global only, top 5 music genres `{name, values, titles}` (`values` are the stored spellings, as on a type tab, for the drill-down); empty on every type tab, including music, whose genres are in `rows`. |

#### `languages`

```json
"languages": {
  "rows": [
    { "name": "English", "titles": 140, "rated": 120, "average_stars": "3.6", "by_type": { "movie": 120, "tv": 20 } },
    { "name": "Japanese", "titles": 9, "rated": 8, "average_stars": "4.1", "by_type": { "movie": 6, "tv": 3 } }
  ],
  "total": 14,
  "coverage": { "known": 150, "total": 158 },
  "media_types": ["movie", "tv"]
}
```

`rows[]` as in genres, without `key` and `values`: the name is the stored English name, which the drill-down sends as it is.
`media_types` names the exposed types counted: both movie and TV on Global, the tab's type on a type tab (STAT-212).
`null` on other tabs (STAT-302).

#### `list_rings`

```json
"list_rings": [
  { "id": 5, "name": "Letterboxd's Top 500 Films", "media_type": "movie", "kind": "community", "seen": 155, "total": 500, "seen_in_range": 12 },
  { "id": 12, "name": "Booker Prize Winners", "media_type": "book", "kind": "award", "seen": 9, "total": 57, "seen_in_range": 2 }
]
```

`id` is the `CustomList` ID, which the app opens; `media_type` is null for a ring with no single type; `kind` is the definition's kind (STAT-213). No posters.
`seen` and `total` are lifetime; `seen_in_range` (int or null) is null in All Time and, in a dated range, the seen titles first seen in it, 1 or more (STAT-213). The app reads it as "+12 in 2025 · 155 of 500".

#### `collections`

```json
"collections": {
  "complete_count": 33,
  "almost_count": 7,
  "complete": [
    {
      "id": "119",
      "source": "tmdb",
      "media_type": "movie",
      "name": "The Lord of the Rings Collection",
      "item_count": 3,
      "poster_urls": ["https://image.tmdb.org/t/p/w500/a.jpg", "https://image.tmdb.org/t/p/w500/b.jpg", "https://image.tmdb.org/t/p/w500/c.jpg"],
      "completion": { "completed_count": 3, "total_count": 3 }
    }
  ],
  "almost": [ ],
  "in_range": {
    "completed_count": 2,
    "completed": [ { "id": "119", "source": "tmdb", "media_type": "movie", "name": "The Lord of the Rings Collection", "item_count": 3,
                     "poster_urls": ["…"], "completion": { "completed_count": 3, "total_count": 3 }, "seen_in_range": 1 } ],
    "progressed_count": 5,
    "progressed": [ { "id": "10", "source": "tmdb", "media_type": "movie", "name": "Star Wars Collection", "item_count": 9,
                      "poster_urls": ["…"], "completion": { "completed_count": 6, "total_count": 9 }, "seen_in_range": 3 } ]
  }
}
```

Each row is a media series summary, the shape the legacy `series_progress` rows use (iOS `MediaSeriesSummary`): `id` (the provider series ID), `source`, `media_type`, `name`, `item_count` (released members), up to 3 `poster_urls` (the first members, with the owner's custom posters), and `completion` (`completed_count` seen of `total_count` released, STAT-214).
`complete` and `almost` hold at most 4 rows each.
`in_range` is null in All Time. In a dated range it holds `completed_count` and `progressed_count` and at most 4 rows each of `completed` and `progressed`: media series summaries plus `seen_in_range` (int, the members first seen in the range, STAT-214).

#### `most_logged`

```json
"most_logged": {
  "label": "Most watched",
  "total": 7,
  "items": [ { "media": {…}, "times": 5 } ]
}
```

`items[]` (at most 12): `media` (the title; TV as its show) and `times` (STAT-215). `total` counts every qualifying title.

#### `milestones`

```json
"milestones": [
  { "number": 1, "day": "2014-03-02", "entry_id": 11, "media_type": "movie", "media": {…} },
  { "number": 100, "day": "2024-03-12", "entry_id": 9312, "media_type": "tv", "media": {…} }
]
```

Sorted by `number`. `entry_id` is the milestone log, which the viewer may open; `media_type` is its type bucket; `media` is its title, TV as its show (STAT-216).
In a dated range, `number` counts that range's logs only.

### STAT-310: Show-all endpoints page through long sections

All three use the standard pagination: `page` (from 1) and `page_size` (default 25, at most 100), answered as `{count, next, previous, results}`.
`users/{username}/` forms apply STAT-002's gate.

| Endpoint | Parameters | `results[]` |
| --- | --- | --- |
| `GET stats/me/highest-rated/`, `GET users/{username}/stats/highest-rated/` | `start_date`, `end_date`, optional `media_type`, as STAT-300 | `{media, stars, day}` in STAT-205 order. In All Time, current-rating rows are included for every viewer. |
| `GET stats/me/collections/`, `GET users/{username}/stats/collections/` | `media_type` (movie, game, book or anime) and `group`, both required. `complete` and `almost` are lifetime and ignore dates. `completed_in_range` and `progressed_in_range` take `start_date` and `end_date` as STAT-300 and need a dated range | Media series summaries in STAT-214 order; the in-range groups add `seen_in_range` to each. |
| `GET stats/me/most-logged/?schema=2`, `GET users/{username}/stats/most-logged/?schema=2` | `start_date`, `end_date`, optional `media_type` | `{media, times}`, folded to the show, in STAT-215 order. |

```json
{
  "count": 140,
  "next": "https://api.spine-api.com/api/v1/stats/me/highest-rated/?end_date=all&page=2&start_date=all",
  "previous": null,
  "results": [ { "media": {…}, "stars": "5.0", "day": "2025-03-02" } ]
}
```

Without `schema=2`, `most-logged/` keeps its legacy rows `{media, log_count}`, one per Item and not folded (STAT-901).

### STAT-311: Errors

| Status | When |
| --- | --- |
| 400 | A date that isn't ISO (body key `start_date` or `end_date`), `start_date` after `end_date` or only one `all` (`date_range`), a `media_type` that isn't an exposed primary type (`media_type`), a collections request with a missing or wrong `media_type` or `group` (that key), or an in-range collections group asked for All Time (`date_range`). The body is `{key: [message]}`, as `parse_stats_range` and `parse_stats_media_type` answer today. |
| 401 | Not signed in. |
| 404 | `users/{username}/` for an unknown user, a private account the viewer doesn't follow, or a block either way. |

## Drill-downs and list tags

### STAT-400: The diary list gains additive filters for drill-downs

`GET /api/v1/diary/` (the signed-in user's own diary) accepts these parameters.
Installed builds never send them; without them the diary behaves as today.

| Parameter | Meaning |
| --- | --- |
| `skip_backfill=true` | Skip `ensure_filter_metadata`: filter on stored values only, with no provider call, so the list matches the stats number. |
| `tv_rollup=true` | When `item_id` is a TV show Item, also return that show's season and episode logs; any other `item_id`, a season included, stays exact. With `year`, `year_min`, `year_max`, `genre`, `genre_group` or `original_language`, a season or episode log matches through its show's Item (STAT-102), falling back to its own Item when the show Item doesn't exist. Other logs are unaffected. Media types are still chosen by `media_type`, which the app already sends as `tv`, `season` and `episode`. |
| `genre_group=<key>` | Repeatable, a STAT-211 key. Logs whose title has a `genre` or `theme` facet value in that group for its media type. The server turns each key into the exact stored spellings found on the user's own logged Items (`genre_groups.raw_values_for(owner, key)`, `(value, media_type)` pairs) and matches each pair on its own type. Music never matches. An unknown key matches nothing. |
| `original_language=<name>` | Repeatable. Logs whose title has an `original_language` facet value equal to one of the names exactly. |
| `rating_min`, `rating_max` | Existing; pinned here: inclusive bounds on the stored 0–10 scale for every type. A ratings bar sends its bucket's bounds as they are (STAT-204). |
| `watched_from`, `watched_to` | Existing; they now use STAT-100's per-row day rule, on the user's calendar, with "before the next day" as the upper bound. |
| `repeat=true` | Only the logs Stats counts as repeats (STAT-108), worked out by the same code as the header's `rewatches`, so the list matches the number for the same dates and media types. |
| `multi_title_days=true` | Only the logs on days Stats counts as days with 2+ titles (STAT-106 events, on the user's calendar), worked out by the same code as `days_with_two_plus_titles`. The days are those of the stats tab the request's `media_type` values name: values in one type bucket (`tv`, `season` and `episode` are one) are that tab, anything else is Global. Within `watched_from`/`watched_to` when given. |

Several values of one parameter match any of them; different parameters must all match.
Whether a log is a repeat, and which titles a day holds, never depend on the range, so `repeat` and `multi_title_days` give the same logs whether the dates are a year, part of one or absent; the dates only narrow the list.
A day can count for `multi_title_days` through events that aren't logs (a tick, a day's progress), so it lists that day's logs even when only one of its titles was logged.

### STAT-401: Each stats row opens the diary with matching filters

Drill-downs exist only on the owner's own stats. On someone else's stats, title rows open the title page and charts aren't tappable.
Every drill-down sends `skip_backfill=true`, `tv_rollup=true` and the range's dates (`watched_from=2025-01-01&watched_to=2025-12-31`; none for All Time).
`tv_rollup` makes season and episode logs match genres, languages and release years through their show on Global too, as Stats counts them.
A type tab, or one type's part of a bar on Global, adds that type's media types: `media_type=<type>`, or for TV `media_type=tv&media_type=season&media_type=episode`.

| Row | Adds |
| --- | --- |
| Genre on a type tab | `genre=<each of values>` |
| Music genre on Global | `genre=<each of values>&media_type=music` |
| Genre group on Global, or one type's part of its bar | `genre_group=<key>`, plus that part's media types |
| Language | `original_language=<name>`, plus the part's media types on Global |
| Release year, or decade | `year=1999`, or `year_min=1990&year_max=1999` |
| Ratings bar, or one type's part of it | `rating_min=<rating_min>&rating_max=<rating_max>`, plus that part's media types |
| A title in Top, Highest rated, Rated higher than most or Most logged | `item_id=<id>`; a show also returns its season and episode logs through `tv_rollup`, and a season row stays exact |
| A header detail with a `drill` (streaks, Repeats, Reviews, Likes, Days with 2+ titles) | its `drill`, whose keys replace the scope's parameters of the same name (STAT-200) |
| The big Titles, Logs and the type tab's primary-measure numbers | nothing more: the scope's diary |
| Longest streak | `longest_daily_streak_range`'s `start` and `end` as `watched_from` and `watched_to` |
| An activity month | that month's first and last day as `watched_from` and `watched_to` |
| The busiest day | that day as both `watched_from` and `watched_to` |
| Milestone | Opens the log (`entry_id`). |
| List ring, collection | Opens the list or the series page, not the diary. |
| Most logged "Show all", Highest rated "Show all", Collections "Show all" | The show-all endpoints (STAT-310). |

The diary lists logs, so a row counted in titles can open more logs than its number, and titles counted only as undated completions don't appear.
A title row with no log in the range opens an empty diary: an All Time Highest rated title rated without a log, or a Top list title logged only in another year. The payload doesn't say whether a title has a log in the range, so the row can't tell in advance.

### STAT-402: Only a list's owner writes its tags

`CustomListWriteSerializer` gains `tags`, used by `POST /api/v1/lists/` and `PATCH /api/v1/lists/{id}/`:

- **Shape.** A list of at most 20 strings, each at most 100 characters once its ends are trimmed; more, `null`, or a value that isn't a string is a 400 under the `tags` key. The limits apply to the list as sent, before normalizing.
- **Normalizing.** Each tag is trimmed with inner runs of whitespace collapsed to one space; empty tags are dropped; tags that differ only in case are duplicates, and the first spelling is kept.
- **POST** sets the tags; without `tags` the list has none.
- **PATCH** replaces the tags when `tags` is present, and `[]` clears them. Without `tags` they stay as they are, so installed builds, which never send it, never clear them.
- **Owner only.** A request that includes `tags` from anyone but the list's owner gets 403 and saves nothing, even from a collaborator who may edit the rest of the list.

The response is unchanged: every list payload already returns `tags`.

```json
{ "name": "My 2025", "is_ranked": true, "tags": ["top2025", "  Favorites ", "favorites"] }
```

saves `["top2025", "Favorites"]`.

### STAT-403: Editing a list's items counts as updating the list

Adding, removing or reordering a list's media items moves the list's `updated_at`, through the API and through the web.
Top's tie-break (STAT-207) and the stats cache both rely on it.

## Installed builds

### STAT-900: Without `schema=2` the legacy payload stays, with fixes inside

`GET stats/me/summary/` and `users/{username}/stats/summary/` without `schema=2` return today's keys and shapes with `schema_version: 1`.
These fixes apply inside them:

- Days follow STAT-100 on the owner's calendar, so a TV log dated 1 January counts in that year, and another viewer's timezone changes nothing.
- `diary_top_rated` and each `media_types[].top_rated` are sorted on stored ratings, but each `rating` keeps its type's legacy wire scale: stars for movies, music, books and games, and 0–10 for TV, anime, manga and comics. Installed builds guess the scale from the type.
- `overview.average_rating` is the average stored rating divided by 2, as stars. Each `media_types[].average_rating` keeps its type's wire scale.
- `list_progress` and `series_progress` are empty arrays. No build shows them.
- The web-chart keys `start_date`, `end_date` (datetimes), `media_count`, `media_type_distribution`, `score_distribution`, `status_distribution` and `top_rated` keep their shapes, built from the native payload and tracking counts. Installed builds read only `top_rated`, and only as a fallback when `diary_top_rated` is missing, which never happens; `top_rated` keeps `diary_top_rated`'s row shape `{media, rating}`.
- `most-logged/` without `schema=2` keeps its rows and paging.

### STAT-901: These legacy fields keep their meaning

Installed builds decode these keys (`ios/Spine/Spine/Core/Models/StatsModels.swift`, `ios/Spine/Spine/Features/Stats/StatsView.swift`), and they keep their names, types and meanings:

- `schema_version`; `range` with `start_date`, `end_date`, `timezone` and `is_all_time`;
- `overview` with `tracked_count`, `completed_count`, `diary_entry_count`, `unique_logged_count`, `review_count`, `repeat_count`, `rated_count`, `average_rating`, `liked_count`, `active_days`, `current_streak_days`, `longest_streak_days` and `completion`;
- `media_types[]` with `media_type`, the same counts, `statuses`, `rating_distribution[]` (`rating`, `count`), `top_rated[]` (`media`, `rating`), `most_logged[]` (`media`, `log_count`), `most_logged_total`, `release_years[]` (`year`, `count`), `top_genres[]` and `top_languages[]` (`name`, `count`), `metadata_coverage` (`total_items`, `release_year_items`, `genre_items`, `language_items`) and `completion`;
- `activity` with `days[]` (`date`, `count`), which the installed weekday card reads, `months[]` (`month`, `count`), `active_days`, `current_streak_days`, `longest_streak_days` and `most_active_weekday`;
- the top-level `rating_distribution`, `release_years`, `top_genres`, `top_languages`, `metadata_coverage`, `diary_top_rated` (with `top_rated` as their fallback), `most_logged`, `most_logged_total`, `list_progress` and `series_progress`;
- each rating's type-dependent wire scale (STAT-900), which they read through `StatsCopy.stars` and `SWStatsRatingChart.normalizedPoints`; and
- `most-logged/` pages of `{count, next, previous, results: [{media, log_count}]}`.

### STAT-902: The new app falls back to the legacy payload

The new app always sends `schema=2`.
An older server ignores it and answers with `schema_version: 1`; the app then reads the header from `overview`, activity from the legacy `months`, and genres from `top_genres`, and hides the sections the legacy payload can't fill, Ratings included.
The v2 `media_types` key has a different shape from the legacy one; clients read it only when `schema_version` is 2.

### STAT-903: Expand, then contract

Every change here is additive: new keys, new parameters, new endpoints and new nullable or defaulted fields.
The legacy payload, `list_progress`, `series_progress` and the web-chart keys are removed only in a later contract step, after installed builds no longer read them.

## Acceptance scenarios

These scenarios check the numbered laws; they add no product decisions.
Run date cases with the server on America/New_York and the owner's calendar on America/Los_Angeles, Pacific/Auckland and America/New_York, including a viewer whose app sends a third zone.
Run each case for the owner, a follower and a public viewer where it applies.

| Case | Setup and action | Required result | Laws |
| --- | --- | --- | --- |
| S-01 | A movie log and a TV episode log dated 1 Jan 2025, and a MAL-export anime log stored at 2025-01-01T00:00Z. | All three count in 2025, in stats and in the diary's `watched_from=2025-01-01`. | STAT-100, STAT-400 |
| S-02 | An anime finished in the app at 23:30 on 31 Dec 2025 in the owner's zone, viewed by someone in Auckland. | It counts in 2025 for both. | STAT-100, STAT-005 |
| S-03 | A show with a show log, a season log and an episode log in 2025. | 1 title, 3 logs. | STAT-102, STAT-103 |
| S-04 | Three episodes of one show ticked on one day, and a season log of that show the same day that fills the rest of the season. | 1 event. The fills count in episodes watched and time only in All Time. | STAT-104, STAT-106 |
| S-05 | A film marked watched with no log; a book marked read with one dated log. | All Time: the film adds 1 title; the book counts 1 title and 2 reads. 2025: neither undated fact counts. | STAT-105, STAT-107 |
| S-06 | A Letterboxd import of a film with one diary row (not a rewatch) that is also in watched.csv. | 0 rewatches. | STAT-108 |
| S-07 | Logs of S1 E1 and S1 E2; then a second log of S1 E1 with Rewatch saved on and off. | Only the second S1 E1 log is a rewatch, whatever its toggle. | STAT-108 |
| S-08 | A book with `completed_manually` and one dated log saved with the reread toggle off. | The dated log is a reread. | STAT-108 |
| S-09 | Ratings stored 0.0, 7.4, 7.5 and 10. | Buckets 0.5★, 3.5★, 4.0★ and 5★; each bucket's bounds return the same logs from the diary. | STAT-204, STAT-400 |
| S-10 | A season log rated 9 and an episode log rated 10. | Highest rated shows the season as "Show S2", 4.5★, and not the episode. | STAT-205, STAT-109 |
| S-11 | A film with `Movie.score` 10 and no log. | It is in All Time Highest rated for every viewer, and in no year. | STAT-205, STAT-002 |
| S-12 | A private ranked movie list tagged "Top 2025". | The owner's 2025 movie tab shows it with "Only you can see this list."; a follower sees Highest rated with no hint. | STAT-206, STAT-207, STAT-004 |
| S-13 | A public ranked list tagged `top2025` holding movies and TV. | It feeds Global 2025, not the movie tab. | STAT-206 |
| S-14 | Two public ranked movie lists tagged `top2025`, then an item added to the older one. | The one just edited wins. | STAT-207, STAT-403 |
| S-15 | A Letterboxd-imported list with positions and `is_ranked` false, tagged `topalltime`. | It is Top in All Time. | STAT-206 |
| S-16 | No tagged list, owner view of the 2024 anime tab. | Highest rated items with "Tag a ranked anime list top2024". | STAT-208 |
| S-17 | A collaborator PATCHes `tags` on the owner's list. | 403; nothing saved. | STAT-402 |
| S-18 | A follower opens stats with a followers-only and a private TV log. | The followers-only log counts and the private one doesn't, nor do its viewings. Time from ticks, collections and status counts match the owner's view. | STAT-002, STAT-003 |
| S-19 | A blocked viewer or a non-follower of a private account opens stats or any show-all endpoint. | 404. | STAT-002, STAT-311 |
| S-20 | A movie with genre `Science Fiction`, a logged TV season whose show has `Sci-Fi & Fantasy`, an anime with `Sci-Fi`, a game with theme `Science fiction`, and an album with genre `drama`. | Global: one `scifi_fantasy` row with 4 titles across 4 types; the album appears only in `music`. The row's drill-down (`genre_group=scifi_fantasy&tv_rollup=true`) returns the season log. | STAT-211, STAT-401 |
| S-21 | Manga genres `Sci-Fi` (MAL) and `Sci-fi` (MangaUpdates). | One manga row with both spellings in `values`. | STAT-210 |
| S-22 | A Japanese film and a Japanese show with four logged seasons. | Global Languages: Japanese, 2 titles. | STAT-212 |
| S-23 | Featured lists of every kind, a live award list, a partial list, `igdb-playing`, and a list with 1 item seen. | Only lasting lists with 2 or more seen show rings. | STAT-213 |
| S-24 | Series where 2 of 3, 18 of 20, 1 of 3 and 1 of 2 are seen, plus an undated TMDB member. | The first two are almost complete; the undated member isn't counted. Works with more than 1,000 tracked Items. | STAT-214 |
| S-25 | A show whose S1 E1 has 3 viewings in 2025. | Most watched lists the show with `times` 3. | STAT-215 |
| S-26 | 260 logs, one a day from 28 August 2024, the 250th on 4 May 2025. | All Time shows the 1st, 100th and 250th. The 2025 pill numbers 2025's 134 logs: its 1st on 1 January and its 100th on 10 April. | STAT-216 |
| S-27 | A film rated 4.5★ with Letterboxd 3.1/5 (8,000 votes) and IMDb 6.0/10, and a second rated film whose Letterboxd rating has 30 votes. | Movies use Letterboxd: `higher` shows the first film with everyone 3.1; the second film is left out, not compared on IMDb. | STAT-217 |
| S-28 | Each tab requested for an owner with data in every type. | Every key present; `collections` and `languages` null exactly as STAT-302 says; empty sections empty. | STAT-301, STAT-302 |
| S-29 | The same request without `schema`. | Today's legacy keys and scales, with STAT-900's fixes. | STAT-900, STAT-901 |
| S-30 | A log saved, then stats requested again by the owner and by a follower. | Both see the new log. | STAT-006 |
| S-31 | Repeats of every kind (an earlier log, a book with `completed_manually`, a movie marker with the saved rewatch, an episode log after an unclaimed tick, an anime moment at 00:30 on 1 January). | The diary with `repeat=true` and each scope's dates and media types lists exactly the logs the Repeats number counts, in every owner zone; the 00:30 log is the next year's. | STAT-108, STAT-400 |
| S-32 | Two films on one day, a film and a tick on another, one film twice on a third, two TV titles on a fourth, a day's anime progress and a film. | Days with 2+ titles counts the first, second, fourth and fifth on Global, only the first on the movie tab and the fourth on the TV tab; `multi_title_days=true` lists every log of the tab on those days. | STAT-106, STAT-400 |
| S-33 | A list ring's films: one logged in 2024 and again in 2025, one logged in 2025, one Completed with no log. | The 2025 ring has `seen_in_range` 1 and lifetime `seen` 3; 2024 has 1; a year with none sends no ring; All Time sends null. | STAT-213 |
| S-34 | A series completed in 2025 by its last film, one whose last film was seen in 2026, and one half seen in 2025. | 2025: one completed, two progressed; 2026: the second completed. | STAT-214 |
| S-35 | The owner's and a follower's header. | Same values; the follower's `drill` is null on every row. | STAT-200, STAT-401 |

## Review status

The user settled the plan's open questions on 2026-10-26:

- others see everything the owner sees, except logs hidden by their visibility and private lists in Top; `tag_hint` stays owner only (STAT-002 to STAT-004);
- a book's year is its first publication (Milestone 1, outside this contract);
- milestones, Rated higher than most and original-language Languages are in v1 (STAT-212, STAT-216, STAT-217).

Writing the laws settled details those decisions leave open. These are provisional until the user confirms them:

- **Rewatch rule (STAT-108).** The plan said a log is a rewatch when its title has an earlier log or an undated fact, never reading the toggle. Taken literally, that makes every Letterboxd-imported first log a rewatch, because the importer sets the direct-consumption marker from watched.csv after importing the diary; and it makes every TV episode log after the first a rewatch, because TV titles fold to the show. STAT-108 counts by target and lets a movie's, album's or TV season's saved toggle confirm an undated fact, following SW-010 and SW-503.
- **Section placement (STAT-218).** Rated higher than most sits after Ratings, Languages after Genres, and Milestones last.
- **One community source per type (STAT-217).** Each type uses the first exposed source with data in the scope, rather than a different source per title.
- **Genre and language rows (STAT-210, STAT-212).** Up to 50 rows are sent so the app can sort by "Highest rated"; rated counts and averages use logs, like the Ratings chart, so each row opens the same logs.
- **Header details (STAT-200).** The list of per-type details and their labels, and that "right now" TV counts appear only in All Time.
- **Specials (STAT-104).** Viewings of specials count in time, events and episodes watched.
- **Group keys and labels (STAT-211).** The keys are fixed here; the labels follow the plan ("Thriller & Crime", "Music & Musicals"). Milestone 1 built them in `api.services.genre_groups` (`GROUPS`).

Round 2 (2026-10-29: drills, range progress, milestones within a range) settled these details the round 2 plan left open; they are provisional too:

- **Ties (STAT-200).** Of two equally long streaks, the later one gives the drill and `longest_daily_streak_range`, as the busiest day does.
- **Per-type details (STAT-200).** Episodes watched, seasons and shows finished, reads, did not finish, completions and abandoned have no drill: no diary filter lists exactly what they count.
- **Rings in a range (STAT-213).** The 2-seen minimum still applies, and `seen_in_range` counts only seen items. A season or episode list item is first seen on its show's day.
- **Collections in a range (STAT-214).** A member seen only undated counts as earlier than every dated one (as BK-406 and GM-216 treat undated completions), so it never stops a series from being completed in a range. Both groups sort by `seen_in_range`, then size, then name.
- **Show-all (STAT-310, STAT-311).** An in-range collections group asked for All Time is a 400 rather than an empty page.

Milestone 1 built the data these laws read: the `theme` and `original_language` facets, `Item.metadata_version`, the paced metadata fill and the genre groups. This document does not claim that any of the rest is implemented.
Follow-up documents, as for the tracking contracts: `stats-coverage.md`, mapping each law to its tests, and a simulator verification document.

## Scope boundary

This contract does not cover:

- the web's statistics page;
- drill-downs and a read-only diary on other people's stats, which need `GET /users/{username}/diary/`;
- countries and the world map;
- streaming-service mix, themes and nanogenres as a stat, platforms and book format;
- the activity heatmap, first and last log of the year, and the media mix bar;
- a "Your lists" block and a Top list picker sheet;
- re-checking collections for new sequels, and a background anime franchise crawl;
- removing the legacy payload's keys (STAT-903); and
- the statuses the legacy payload already shows to other viewers.

These need separate decisions if they are taken on later.

## Change policy

The numbered laws above are normative product behavior.
Implementation details may change without editing this document, but behavior that contradicts a law requires an explicit product decision and a contract update first.
Add a new Stats rule only after the user accepts it.
Record inherited shared rules directly under STAT-000 without requesting repeated approval.
Keep unresolved questions separate from accepted rules.
Behavior omitted from this contract is not implicitly decided.
