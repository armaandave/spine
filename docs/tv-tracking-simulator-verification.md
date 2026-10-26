# TV Tracking Simulator Verification Record

Overall status: **VERIFIED WITH ONE OPEN APP DEFECT**

Every flow below matched the laws in [the TV tracking contract](tv-tracking-contract.md) in the real Spine Dev app.
One high-severity defect remains open: the Home screen can freeze on its first render (V-01).
The record omits credentials, tokens and provider keys.

## Run metadata

| Field | Recorded value |
| --- | --- |
| Run | 2026-10-26, 09:22 to 10:22 EDT (13:22 to 14:22 UTC) |
| Operator | Claude Code subagent, driving the app through XcodeBuildMCP taps, swipes, typing and screenshots |
| Branch / HEAD | `worktree-tv-tracking` at `c70a7f99` (clean worktree) |
| App | Debug build, Spine Dev (`com.armaan.Spine.dev`), built with `xcodebuild build -destination 'generic/platform=iOS Simulator'` into `~/Library/Developer/Xcode/DerivedData/spine-tv-tracking` |
| Simulator | Scratch iPhone 17, iOS 26.5, UDID `2BD14EA1-4AE4-475C-8FFD-C9C4F53609C6`, created and deleted by the cleanup helper |
| Backend | `scripts/ios/local-backend.sh` from this worktree on `127.0.0.1:8001`; this worktree's own SQLite database, freshly migrated; Celery worker on Redis DB 5 under this checkout's prefix; dev sign-in |
| Account | The passwordless dev user `claude`, created empty by the first sign-in |
| Launch | `SIMCTL_CHILD_SPINE_API_BASE_URL=http://127.0.0.1:8001`, `--dev-sign-in` |
| Server time zone | UTC (local settings); the simulator ran on the Mac's America/New_York clock |
| Provider data | Live TMDB through the local backend; production, port 8000 and the always-on test backend were never contacted |

Shows used (TMDB ids):

| Show | Shape | Used for |
| --- | --- | --- |
| Sherlock (19885) | 4 finished seasons of 3, 9 aired specials | Flow A, E, episode log, opinion stars, Home tick |
| Fleabag (67070) | 2 finished seasons of 6 | Flow B, D |
| Fawlty Towers (2207) | 2 finished seasons of 6 | Flow C |
| Black Books (903) | 3 seasons of 6; 13 specials, 5 aired, 8 with no date | Flow G (special first, earlier-episodes Yes, Mark watched up to here, Specials eye) |
| The IT Crowd (2490) | 4 seasons of 6 | Flow G (Log Season 3 first, No) |
| High Potential (226637) | Caught up between seasons; S3 E1 on 2027-01-06 | D2 between seasons, D4 heart on an unstarted show |
| Abbott Elementary (125935) | Weekly season: S6 E4 airs 2026-10-28 (Wed) | D2 mid-season, Remove from Library |

## Method

Each tap was followed by a read of the accessibility tree (`axe describe-ui`, the tool behind `snapshot_ui`) and of the server log since the previous tap.
Screenshots confirmed what the tree showed; under load they trailed the app, so the tree and the log were treated as the source of truth.
App state was corroborated with read-only API calls as the same dev user, tagged `probe=1` so the log separates them from the app's own requests.

The run used the API for three test-setup writes, all made to isolate V-01, never to complete a flow:

- it deleted diary entries 4 and 5: entry 4 was Flow D's rewatch log, deleted after D was verified; entry 5 was a second Season 1 log, made only to test V-01;
- it paused Sherlock once; the app later chose Watching again.

Evidence lives outside source control, as for games:
`/Users/armaandave/Library/Caches/tv-tracking/sim-verify/`

- `screens/`: 51 screenshots, named by flow (paths below are relative to it).
- `server-requests.log`: the app's requests after each tap.
- `hang-sample-1.txt` to `hang-sample-4-repro.txt`: main-thread samples of V-01.

## Flow results

| Flow | Result | Observed (law) | Evidence |
| --- | --- | --- | --- |
| A1. Tick S1 E1 to E3 on the season page | PASS | Each tap sent one `POST .../actions/tick/` (200). After E1: Watching, "1 of 3", "Started October 26, 2026", S1 E2 up next, no Season 2 tracking (A-01, TV-100, TV-205). | `A01-s1e1-ticked.jpg`, `A02-show-2of12-upnext-s1e3.jpg` |
| A2. Finale opens Log Season once | PASS | Ticking E3 opened Log Season dated Oct 26, Rewatch off. Closing it with ✕ ("Not now") sent nothing; the diary stayed empty; Season 1 read "Watched", 100%. Unticking and re-ticking E3 opened nothing (A-05, TV-403). | `A03-season-finish-composer.jpg`, `A04-after-not-now-s1-watched.jpg` |
| A3. Log it from + | PASS | Season + menu offered Log Season and Rewatch Season. Saving 4 stars sent one `POST /api/v1/diary/` (201); Season 1 stars became 8.0; the show's stars stayed empty (TV-500, TV-501). | `A05-season-plus-menu-log-season.jpg` |
| A4. Into Season 2 | PASS | Up next moved to S2 E1. The Season 2 card kept "3 episodes" and the API reported it `not_started` (TV-105, TV-205). Ticking S2 E1 from the show's Up next row: one request, "4 of 12", Season 2 "1 of 3". | `A07-show-upnext-s2e1-s1-watched.jpg`, `A07b-show-watching-3of12-upnext-s2e1.jpg` |
| A5. Untick | PASS | Unticking S2 E1 on the Season 2 page sent one `POST .../actions/untick/`. Season 2 went back to not started, and up next to S2 E1 (TV-102). Unticking the season-logged S1 E3 sent nothing and showed "Delete your Season 1 log first." (A-48). | `A08-s2-unticked-0pct.jpg`, `A06-untick-logged-ep-delete-season-log-first.jpg` |
| A6. Log a ticked episode | PASS | Log Episode on S1 E1 defaulted to the tick's day with Rewatch off. Saving it left one viewing, now linked to the log (`viewing_count` 1, `log_count` 1); the episode's stars showed that log's 3 stars (A-18, TV-402, TV-502). | `A12-log-episode-s1e1-claims-tick.jpg` |
| B. Seasons-only logging (Fleabag) | PASS | Show + menu, Log, default target Season 1, 4.5 stars. The first save hit SQLite locking (V-02) and changed nothing; the app kept the draft, and Save again sent one 201. The show went to Watching with S2 E1 up next and Season 1 Watched with its stars (A-13). Logging Season 2 (the default target; picker: Season 1, Season 2, Whole show) made the show Completed. The filled show eye then said "Delete the logs first." (TV-302 case 3). | `B00`–`B05`, `L03-library-completed-fleabag-fawlty.jpg` |
| C. Show eye and undo (Fawlty Towers) | PASS | The empty eye asked "Mark all 12 aired episodes watched?". Mark Watched sent one request: Completed, one Library card, both seasons Watched, no activity (A-23). Tapping the filled eye sent one `unmark` with no dialog, and the show left the Library (A-24). | `C01-show-eye-confirmation.jpg`, `C02-fawlty-marked-completed.jpg`, `L03-...`, `C03-fawlty-after-undo-untracked.jpg` |
| C (extra). Season eye | PASS | An accidental tap on Sherlock's Season 2 eye marked it with no dialog and no composer: up next moved to S3 E1. Tapping the filled season eye unmarked only Season 2 and restored S2 E1 (TV-300, TV-302 case 2). | server log 13:41:08 and 13:41:26 |
| D. Rewatch a season (Fleabag) | PASS | Rewatch Season on Season 1 gave "Rewatching S1 · E1 next" and "0 of 6" (TV-600). Ticking E1 to E6 sent one request each. E6 opened Log Season with Rewatch on and the season's 4.5 stars filled in (TV-404). Saving it gave `is_rewatch: true`, and the show returned to Completed (A-35). | `D01-fleabag-rewatch-s1-0of6.jpg`, `D02-rewatch-finish-composer-rewatch-on.jpg` |
| D (extra). Show rewatch, then stop | PASS | Rewatch gave "Rewatching · S1 E1 next". Rewatch was then unavailable: "Finish or stop your rewatch first." One tick, then Stop Rewatching: Completed (A-36). | `D03-rewatch-menu-stop-rewatching.jpg` |
| E. Pause and drop (Sherlock) | PASS | Pause saved at once and left Continue Watching. The Library card read "Paused at S2 E1". A mistaken tick made it Watching; unticking restored Paused (A-37). Drop asked "Drop this show? Your progress will be kept."; Dropped kept "3 of 12" and read "Stopped at S2 E1". The Paused menu offered Watching and Resume and showed Pause as selected; both continued at S2 E1. | `E01`–`E03`, `L04-library-dropped-stopped-at-s2e1.jpg`, `L05-library-paused-at-s2e1.jpg` |
| G1. Special first (Black Books) | PASS | Ticking special E4 first: Watching, "0 of 18 watched", S1 E1 up next, Specials "1 of 5". Planning showed "You've started this show. Pause or drop it instead." No question (A-40, TV-004, TV-006). Specials with no air date show "TBA". | `G00-black-books-specials-tba.jpg`, `G01-special-first-0of18-upnext-s1e1.jpg` |
| G2. Start partway, Yes | PASS | The first regular tick (S2 E1) asked "Also mark S1 E1–E6 watched?". Mark Watched sent one `mark_earlier`: Season 1 filled, "7 of 18", S2 E2 up next, no composer (TV-107). | `G02-earlier-episodes-question.jpg` |
| G3. Mark watched up to here | PASS | Long press offered "Mark Watched Up to Here". The dialog named the range, "Mark S2 E2–E3 watched?"; one request; up next S2 E4 (TV-108). | `G03-mark-up-to-here-confirmation.jpg` |
| G4. Specials eye | PASS | Marked the 5 aired specials. Progress (9), up next and status didn't change; no composer (A-58). | `G04-specials-eye-airing-title-S0.jpg` |
| G5. Log Season 3 first, No (The IT Crowd) | PASS | Saving asked "Also mark Seasons 1–2 watched?". Not Now sent nothing: Seasons 1 and 2 stayed unstarted, and up next was S4 E1. The next tick (S4 E1) asked nothing (A-14). | `G05-log-s3-first-question-seasons-1-2.jpg` |
| D2 between seasons, D4 heart (High Potential) | PASS | A heart on the unstarted show sent one request: Completed, all 31 aired episodes watched, eye filled. The card read "Caught up · S3 on Jan 6" on the show page and in Library Completed (TV-202, TV-303). The filled eye then unmarked everything; the show left the Library and its heart was cleared (TV-302, TV-208). | `AIR1-high-potential-heart-completed.jpg`, `L07-library-completed-caught-up-s3-jan6.jpg` |
| D2 mid-season (Abbott Elementary) | PASS | The eye asked "Mark all 95 aired episodes watched?". Afterwards: Watching, "Caught up · E4 on Wed", Season 6 Airing, and absent from Continue Watching (A-10). Remove from Library asked "Remove this show? Your 95 watched episodes will be unmarked." and removed it (TV-003). | `AIR2-abbott-caught-up-e4-wed.jpg`, `L06-library-watching-card-lines.jpg`, `R01-remove-from-library-confirmation.jpg` |
| D4 on a started show (Sherlock) | PASS | 4 show stars sent one `rate`. Status, choice, up next and "3 of 12" didn't change, and one `rating_updated` activity was created (A-29). The feed labels it "shared activity" (V-03). | `H02-home-tick-s2e1-now-s2e2-next.jpg` |
| Library shelves and card lines | PASS | Watching, Planning, Paused, Dropped and Completed chips. List lines matched TV-700: "S2 E2 next · 4 of 12", "Caught up · E4 on Wed", "Paused at S2 E1", "Stopped at S2 E1", "Caught up · S3 on Jan 6". Each show had one card. | `L01`–`L07` |
| Home Continue Watching with its tick | PASS, see V-01 | Lists shows only, most recent first (IT Crowd, Black Books, Sherlock); caught-up, paused, dropped and completed shows left out (TV-701). The row tick on Sherlock S2 E1 sent one request, and the row became "S2 E2 next". Profile shows the same rail. | `H01`–`H03`, `P01-profile-continue-watching.jpg` |
| Diary TV filter | PASS | The TV filter asks for `media_type=tv&media_type=season&media_type=episode`. It lists the episode log ("Sherlock · S1 E1 · A Study in Pink") with the season logs ("Fleabag · Season 2"), each with its own stars or heart (TV-702). | `V01-diary-tv-filter-episode-and-season-logs.jpg` |
| Feed (D5) | PASS | After every flow, activity held only the logs (`diary_created`) and the one direct rating. Ticks, unticks, marks, fills, pause, drop and rewatch created none (TV-703). | API reads of `/users/claude/activity/` |

### Server log: one request per tap

Across the run the app sent 48 writes, and each tap that changed state sent exactly one of them:

- 17 ticks, 3 unticks, 4 marks, 3 unmarks, and 1 each of mark earlier and mark up to here;
- 2 rewatches (one season, one show), 2 pauses, and 1 each of drop, resume, watching, stop rewatch, rate and remove;
- 1 like and 8 diary saves (one of them the 500 below).

Two ticks share a timestamp at 13:46:48; they are separate taps on E4 and E5.
Taps that only showed a server-provided reason, such as an untick of a logged episode, sent nothing.
Reads that follow a write (show, season, Home rails, activity) are the app's refresh, not repeats.

Two requests answered 500, and both were SQLite locking (V-02):

- 13:43:51, `POST /api/v1/diary/`: changed nothing; the retry succeeded;
- 14:03:24, `GET /api/v1/media/tmdb/episode/19885/?season_number=1&episode_number=1`: Try Again loaded it.

There were no other 5xx answers and no tracebacks from TV request handlers.
Log noise unrelated to TV:

- ComicVine, MAL and MusicBrainz connection errors during all-media search;
- naive-datetime warnings from a scheduled history task.

### reconcile_tv dry run

`python manage.py reconcile_tv -v 2` against this worktree's SQLite database ran twice under the cleanup wrapper (receipts `05fd9441` and `bce25258`, both clean).
A wrapper script first checks that Django is using this worktree's SQLite file. `--apply` was never used.

```
Dry run: nothing is saved. Run with --apply to save these changes.
Server timezone: UTC
claude · Abbott Elementary (tmdb 125935)
claude · Sherlock (tmdb 19885)
claude · The IT Crowd (tmdb 2490)
claude · Fleabag (tmdb 67070)
claude · Black Books (tmdb 903)
Shows checked: 5
Shows changed: 0
Newly in the Library: 0
Status changes: 0
Season-level Paused or Dropped replaced: 0
Logs whose shown day changes: 0
Legacy marks: 0
Stars filled: 0
Errors: 0
Users with no stored calendar (their logs convert on UTC): none
```

The final run, after Abbott Elementary was removed, checked the other 4 shows with the same zero counts.
Everything the new app wrote was already worked out, as TV-207 requires: the repair would change nothing.

## Defects

| ID | Severity | Steps | Expected (law) | Observed |
| --- | --- | --- | --- | --- |
| V-01 | High | Reach Home with Continue Watching showing one show (Sherlock, "S2 E1 next") and exactly four activity rows, before their images are in memory. Seen after "Go to Home" (13:54 UTC) and on three cold launches (13:57, 14:01 and 14:04 UTC). | Home renders and stays usable, so every surface agrees after a write (TV-705, TV-701). | The app froze: main thread at 100% CPU for minutes, taps ignored, empty accessibility tree. The tab bar sometimes vanished. The main-thread samples show an endless SwiftUI render loop. It runs from `_UIHostingView.layoutSubviews` through `ViewGraphRootValueUpdater.render`, repeating `ActivityItem` copies, `LazyImage.onAppear` and `FetchImage.load` (`hang-sample-*.txt`). It also froze with four distinct activity rows (entries 1, 2, 3 and 6), so duplicate posters aren't the cause. No freeze with 3 rows, with 4 rows and Sherlock paused (empty rail), or in the final state (3 shows, 6 rows). The trigger looks like a lazy-stack loop near the bottom edge that depends on layout. The rows and artwork are unchanged on this branch. The Continue Watching poster and its new tick line did change (`ios/Spine/Spine/Features/Home/HomeView.swift:88-117`, `:720-787`), and that changes the rail's height. Not checked against a `myapp-main` build, so it isn't proven to be new on this branch. Screens: `X01-hang-library-no-tabbar-100cpu.jpg`, `X02-relaunch-home-renders-then-spins.jpg`, `X03-...`, `X04-...`. |
| V-02 | Medium (local and test backends only) | Save a TV log, or open an episode page, while the Celery worker or a neighbouring request writes to the same SQLite file. | Writes apply in full or not at all and succeed (TV-207, TV-705). | SQLite answered "database is locked", in four places. (1) A Fleabag Season 1 log save got a 500 (the atomic write at `src/app/tv_tracking.py:1376`, failing in `_create_tv` at `:1280`). It changed nothing, and the retry saved. (2) An episode page GET got a 500, because external ratings save during a GET (`src/api/services/media.py:4780`, `src/app/external_ratings.py:737`). (3) Several "Enrich external ratings" Celery tasks failed. (4) The TV calendar refresh failed (`src/events/calendar/tv.py:54` into `src/app/tv_listing.py:634`). The SQLite settings set no busy timeout (`src/config/settings.py:241-248`). Production runs PostgreSQL, so this hits only local backends and the always-on test backend that Spine Dev uses. |
| V-03 | Low (not new) | Set stars directly on a show. | The rating activity reads as a rating (SW-401, TV-703). | The activity is created, but Home's feed labels it "shared activity": `actionText` has no case for `rating_updated` (`ios/Spine/Spine/Features/Home/ActivityFeedView.swift:428-446`; the file is unchanged on this branch). |
| V-04 | Low (cosmetic) | Finish a season from its season page, so Log Season opens. | The composer names its target (TV-403, TV-404). | Its heading reads only the show title ("Sherlock"); only the season poster tells which season it is. From the show page the heading reads "Fleabag · Season 1" with a target picker. `selectedTitle` adds the season only when the detail is the show (`ios/Spine/Spine/Features/MediaDetail/MediaLogView.swift:214-220`). |
| V-05 | Low (cosmetic, accessibility) | Open Black Books' Specials. | Specials appear as "Specials", and an undated unaired episode shows "TBA" (TV-004, TV-100). | The show page card says "Specials", but the season page header reads "Black Books S0" with a "Season 0" chip. "TBA" is drawn but missing from the row's accessibility label. Separately, 8 undated deleted-scene specials keep the Specials season "Airing" after its 5 aired specials are marked; that is literal TV-200 and changes no count. |
| V-06 | Low (efficiency) | Switch the Library's media type to TV. | One read per list. | `filter-options` and `tracking/?media_type=tv&status=In progress` were each requested twice at 13:41:59, with no error. |

Notes that aren't defects:

- "Not now" is the composer's ✕ Close button (`MediaLogView.swift:627-634`), which saves nothing. That matches the native toolbar pattern.
- No star picker followed the eye or the heart; TV-300 and TV-303 make it optional.

## Limits

- The scenarios ran once each with one account; the acceptance suite covers input permutations.
- Not run here: webhooks, imports, export, other time zones, PostgreSQL, the hourly airing job.
- Episodes airing while a show is caught up (TV-206) weren't observed live; neither show's next episode aired during the run.
- B's Watching state after the first season log was checked through the API and the Continue Watching payload, not with a Library screenshot. Its Completed state was checked in the Library.
- V-01's cause is unconfirmed; the next step is reproducing it on a `myapp-main` build, or with Instruments' SwiftUI template, in the same data state.

## Runs and cleanup

| Run | Purpose | Receipt |
| --- | --- | --- |
| `fb7964f8` | Simulator build (`--queue ios`) | `command_exit` 0, exit 125: one detached Xcode helper, gone when checked afterwards |
| `ae54a535` | Local backend (`--timeout 14400`) | Stopped; `cleanup_complete` true, no leftovers; port 8001 free afterwards |
| `8483cf0d` | Scratch simulator (`--queue ios --lane sim`); installed, launched and slept; never compiled | Stopped; `cleanup_complete` true, no leftovers; simulator deleted |
| `05fd9441`, `bce25258` | `reconcile_tv` dry runs | `command_exit` 0, `cleanup_complete` true, no leftovers |

No code was changed, and nothing was pushed, deployed or installed on a phone.
