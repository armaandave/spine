# Anime Tracking Simulator Verification Record

Overall status: **CHECKS A TO F AND THE REVIEW'S RE-CHECKS PASS, ON iOS 26.5 ONLY; DEFECTS V-01 TO V-07 FIXED AND RE-RUN**

Every flow below matched the laws in [the anime tracking contract](anime-tracking-contract.md) and, for flow E, TV-303 and TV-304 in [the TV contract](tv-tracking-contract.md), in the real Spine Dev app.
The first run found five defects. One was fixed and re-run during that run, and the other four were fixed after it.
A second run, after an independent review of `7dddf58a`, looked at those four in the app, re-checked each of the review's fixes, and found two more defects, both fixed and re-run (see [The second run](#the-second-run) and [Defects](#defects)).
The record omits credentials, tokens and provider keys.

## Run metadata

| Field | Recorded value |
| --- | --- |
| Run | 2026-11-04, 00:10 to 01:17 EST (05:10 to 06:17 UTC) |
| Operator | Claude Code subagent, driving the app with the `axe` tool that XcodeBuildMCP bundles: taps, swipes, typing and screenshots |
| Branch | `worktree-anime` |
| App | Debug build, Spine Dev (`com.armaan.Spine.dev`), the tree at `36bd1ac3`, built with `xcodebuild build -destination 'generic/platform=iOS Simulator'` into `~/Library/Caches/anime/DerivedData/spine-anime` |
| Simulator | Scratch iPhone 17, iOS 26.5, UDID `81C4400B-2174-46C2-898F-F62B4E08F37B`, created and deleted by the cleanup helper; hardware keyboard off |
| Backend | `scripts/ios/local-backend.sh` from this worktree on `127.0.0.1:8001`; this worktree's own SQLite database; Celery worker on Redis DB 5 under this checkout's prefix; dev sign-in. Server code moved under it during the run, as the server review's fixes landed (up to `98954fe5`), and the dev server reloaded them |
| Account | The passwordless dev user `claude` |
| Launch | `SIMCTL_CHILD_SPINE_API_BASE_URL=http://127.0.0.1:8001`, `--dev-sign-in` |
| Time zone | The simulator ran on the Mac's America/New_York clock |
| Provider data | Live MAL and AniList through the local backend, and live TMDB for flow E. Production, port 8000 and the always-on test backend were never contacted |

Titles used:

| Title | Shape | Used for |
| --- | --- | --- |
| Attack on Titan (MAL 16498) | Finished, 25 episodes, next title Season 2 | Flow A, F |
| Attack on Titan Season 2 (MAL 25777) | Finished, 12 episodes | Flow A's No and next title, F, the short title timed against One Piece |
| The Apothecary Diaries Season 3 (MAL 61987) | Airing, 5 aired, E6 on Friday | Flow B |
| One Piece (MAL 21) | Airing, 1,180 aired, 1,189 listed | Flow C |
| Your Name (MAL 32281) | Movie, a single | Flow D |
| Fleabag (TMDB 67070) | 2 finished seasons of 6 | Flow E |

## Method

Each tap was followed by a read of the accessibility tree (`axe describe-ui`, with `--point` under a full-screen cover, where the tree goes stale) and of the server log's writes since the previous tap.
Screenshots confirmed what the tree showed.
App state was corroborated with read-only API calls as the same dev user, tagged `probe=1` so the log separates them from the app's own requests.

The API made one test-setup write: it set One Piece to 800 of 1,180 before flow C, because ticking 800 rows by hand proves nothing.
Everything else was done in the app.

Evidence lives outside source control, as for TV:
`/Users/armaandave/Library/Caches/anime/screens/`

- 131 screenshots, named by flow (paths below are relative to it).
- `timing/`: the frames behind the times in [One Piece's times](#one-pieces-times).

## Flow results

| Flow | Result | Observed (law) | Evidence |
| --- | --- | --- | --- |
| A1. Tick and untick | PASS | Ticking E1 sent one `POST .../actions/tick/`: Watching, "1 of 25 watched", up next E2. Unticking it left the library (TV-100, TV-102, AN-102). | `A01-e1-ticked-row.png`, `A02-e1-ticked-top.png`, `A03-e1-unticked-left-library.png` |
| A2. First tick at E5, Yes | PASS | "Also mark E1–E4 watched?". Yes sent one `mark_earlier`: 5 of 25, up next E6 (TV-107). | `A04-e5-first-tick-question.png`, `A05-yes-e1-e5-marked.png` |
| A3. First tick at E5 on another title, No | PASS | On Season 2, No sent nothing: only E5 watched, up next E6. Rows with no title read "Episode 5" (TV-107, AN-101). | `A07-rows-with-no-title.png`, `A08-s2-e5-question.png`, `A09-s2-no-only-e5.png` |
| A4. Hold for up to here | PASS | Holding E10 offered Mark Watched Up to Here, then "Mark E6–E10 watched?" (TV-108). | `A11-hold-context-menu.png`, `A12-up-to-here-dialog.png`, `A13-up-to-here-done.png` |
| A5. The eye and the filled eye | PASS | The eye marked the rest: Completed, with "Next: Attack on Titan Season 2" as a plain row. The filled eye removed the marks and kept the tick on E5 (AN-300, AN-500). | `A14-eye-marks-rest.png`, `A15-completed-next-row.png`, `A16-filled-eye-undoes-marks.png` |
| A6. The + menu | PASS | Options from `available_actions`, reasons from `action_reasons`. Remove asked "Remove this anime? Your 1 watched episode will be unmarked." (TV-005, TV-003). | `A17-plus-menu-partway.png`, `A18-remove-prompt.png` |
| A7. Stars when not started | PASS | One `rate`: Completed, no log screen (AN-301). | `A19-star-picker.png`, `A20-star-picked.png`, `A21-stars-not-started-marks.png` |
| A8. Stars partway, cancel, then save | PASS | Log Anime opened with 4 stars and no request. Cancel sent nothing; rating stayed null and the picker empty. Saving sent one `POST /api/v1/diary/` (201): Completed (AN-301, AN-400). | `A22-partway-stars-open-log.png`, `A23-cancelled-log-nothing-changed.png`, `A23b-cancelled-picker-empty.png`, `A24-log-saved-completed.png` |
| A9. With a log | PASS | The filled eye and a filled tick said "Delete your log first." and sent nothing (AN-300, TV-102). | `A25-filled-eye-delete-log-first.png`, `A26-filled-tick-with-log-says-delete.png` |
| A10. Stars when finished | PASS | Only `rate`, 5 stars, no log screen (AN-301). | `A27-finished-picker-shows-stars.png`, `A28-finished-stars-only-save.png` |
| A11. Diary, edit and delete | PASS | Diary row and entry. Edit: `PATCH` 200. Delete asked "Delete this log? 24 episodes it marked watched will be unmarked.", then `DELETE` with `expected_version` and `mutation_id`, back to 1 of 25 (TV-405, TV-406). | `A29-diary-tab.png` to `A34-after-delete.png` |
| A12. Library | PASS | Five shelves. Lines "E6 next · 1 of 12", "E2 next · 1 of 25", "5 stars". Grid badges E6 and E2 (AN-700). | `A35-library-open.png`, `A36-library-grid-badges.png`, `A37-library-list-lines.png` |
| A13. Home's card | PASS | Poster bar and badge. VoiceOver label "Shingeki no Kyojin, update progress, E11 next, 10 of 25" (AN-701, TV-701). | `A38-home-card.png` |
| A14. Sheet at up next, both heights, every open | PASS | Medium: title bar bottom y=468, E11 at y=486. Large: bar bottom 118, E11 at 136. The same over 3 medium opens, 2 large, and back to medium. | `A40-sheet-open-medium-loaded.png` to `A44-sheet-back-to-medium.png` |
| A15. In the sheet | PASS | A tick sent one request and the list didn't move. A body tap sent nothing. Hold reached "Mark E12 watched?". ··· offered Log… and Open Anime, and Open Anime opened the page; Home's card then read "E13 next, 12 of 25". | `A45-sheet-ticked-e11-body-tap-nothing.png` to `A51-home-after-sheet.png` |
| A16. The last tick, Not now, the next title | PASS | The last row sat at the top at both heights. Its tick opened the log screen with Not now, stars and heart filled in, dated Nov 4. Not now sent nothing; the sheet moved to "Attack on Titan Season 2" with E1 at the top, and Season 2 stayed out of the library. The first tick there was an ordinary tick on 25777: Watching. Back on the first title, + still had Log and Rewatch, and untick then re-tick opened nothing (AN-203, AN-501). | `A52-sheet-up-next-last-row.png` to `A58-retick-no-log-screen.png` |
| B. Airing title | PASS | The eye marked what had aired: Watching, caught up, 5 of 5. Library read "Caught up · E6 on Fri". Stars sent only `rate`. Log: `POST` 201, still caught up, "Logged on November 4, 2026" (AN-201, AN-300, AN-301, AN-400). | `B01-airing-page.png` to `B07-log-leaves-caught-up.png` |
| C1. One Piece opens at up next | PASS, see the times below | Page: "800 of 1180 watched", UP NEXT E801, rows to E810, then a plain "All 1,189 Episodes" row and Related. Its sheet and Home's opened on E801, E1091 and E1180, each at the top. | `C01-one-piece-page.png`, `C02-page-window-all-episodes.png`, `C03-all-episodes-sheet-medium.png`, `C05-sheet-from-page.png`, `C14-home-sheet-onepiece.png` |
| C2. Scrolling | PASS | 13 flings from E801 to E1189, then 35 from there to E1, each moving about 33 rows, with no stall or jump. The accessibility tree never held more than 35 rows, the page's 11 behind the sheet included. CPU was 15 to 26% during flings, with a peak of 42% at E60 to E92, among the rows with stills (E62 to E130), and 0.0% at rest. | `C04-sheet-last-row.png`, server log |
| C3. A tick and up to here, far down | PASS | Tick E801: one `tick`, list unmoved. Hold E1090: "Mark E802–E1090 watched?", one `mark_up_to`, 48 KB, list unmoved. Page's rows followed up next to E1091 to E1100 (AN-101, TV-108). | `C06-sheet-tick.png` to `C09-up-to-here-done.png` |
| C4. Stars open the log | PASS | Log Anime, "One Piece", 4 stars, no picker, no request. Cancel sent nothing; rating null, picker empty (AN-301). | `C10-stars-picker.png` to `C12b-stars-after-cancel.png` |
| C5. No log screen by itself | PASS | Up to E1179, then tick E1180, the last aired: caught up, `opinion_action` `save`, no log screen. Unticking it went back to up next E1180 (AN-201, AN-203). | `C13-last-aired-tick-no-log.png` |
| D. Your Name | PASS | No rows and no up next. + offered Planning and Log, then with it in the library Log and Remove from Library. The eye: one `mark_watched`, Completed, no number. Stars on a watched single: only `rate`. Log: one target, no picker, a date, `POST` 201. The filled eye then said "Delete your log first." with no request. Deleting the log gave the mark back; the eye removed it. After Remove, stars marked it with no log screen; the heart saved. Library: Completed, "4 stars", no badge (AN-002). | `D01-single-page.png` to `D16-library-completed.png` |
| E1. Stars on an unstarted season | PASS | Fleabag Series 2: one `rate`, marked Watched, no composer. TV's "Also mark Season 1 watched?" followed; Not Now sent nothing (TV-303, TV-205). | `E01-unstarted-season-page.png` to `E03-unstarted-season-marked.png` |
| E2. Stars on a finished season | PASS | One `rate`, 4.0 to 5.0, no composer (TV-303). | `E04-finished-season-stars.png`, `E05-finished-season-saved.png` |
| E3. Stars on a partway show | PASS | Log TV Show, target "Whole show", 4 stars, no request. Cancel sent nothing; the show's rating stayed null and its picker empty (TV-303). | `E06-show-partway.png` to `E09-show-picker-after-cancel.png` |
| E4. Heart on a partway season | PASS | Log Season, "Fleabag · Season 1", heart on, no stars, no request. Cancel sent nothing; the rail's heart stayed empty and `liked` false (TV-304). | `E10-season-partway.png` to `E12-season-heart-cancelled.png` |
| F1. Pause and Resume | PASS | Pause: one request, page "Paused", Library "Paused at E2". + then read Resume, and Resume put it back on the Watching shelf (TV-007, AN-700). | `F01-menu-watching.png` to `F05-resumed-page.png` |
| F2. Drop and Watching | PASS | "Drop this anime? Your progress will be kept.", then Dropped; Library "Stopped at E2", grid badge E2. Watching brought it back to "E2 next · 1 of 12" (TV-007, AN-700). | `F06-dropped-page.png` to `F10-library-watching-again.png` |
| F3. Rewatch and Stop Rewatching | PASS | Rewatch on a Completed title: "Rewatching · E1 next", "0 of 25 watched". Two ticks: "Rewatching · E3 next", "2 of 25". Library the same, with "5 stars". Stop Rewatching: Completed, 25 of 25, every tick kept (AN-600). | `F11-menu-completed.png` to `F16-stop-rewatching-page.png` |
| F4. Remove from Library | PASS | "Remove this anime? Your 1 watched episode will be unmarked.", one `remove`, back to not started, gone from Library (TV-003). | `F17-remove-prompt.png` to `F19-library-after-remove.png` |

## One Piece's times

Measured with frames taken as fast as `simctl` allows, about one every 0.26 s, so each time is good to about that. Times run from the end of the tap command, because `axe tap` itself takes about 0.6 s. Debug build, on the Mac that was also running this work's other jobs.

| Open | One Piece | Short title (Season 2, 12 rows) |
| --- | --- | --- |
| Home's sheet, up next's row at the top | First frame 0.50 s, settled 1.34 s; row 1,180 of 1,189 | First frame 0.70 s, settled 1.44 s |
| The page from Home's poster, whole page on screen | 1.07 s, 1.19 s, 0.97 s | 0.70 s, 0.61 s, and one open still dark after 1.98 s |
| The All Episodes sheet from the page | First frame about 0.95 s, settled about 1.45 s, under heavier load | Has no such sheet |

- The sheet opens as fast as a short title's.
- The page opens about 0.4 s behind a short title's, and the cause wasn't found. The server's read takes 0.11 s against 0.10 s, and decoding takes 15 ms against 3.5 ms (166 KB against 41 KB). Rows on screen are 10 either way, and their state lookups are by dictionary. The artwork differs, which may be all of it: no profile was taken before the backend stopped.
- The first read of the page, with nothing stored for the title, reads MAL and AniList: 5.47 s from the start of the tap, when the tap alone took about 3 s.
- On the final app (`ba0940fe`, second run) the All Episodes sheet changed the first frame about 0.2 s and settled about 1.0 s after the tap command ended, with E1180 at the top. Ten flings up to E860 and fourteen back to the end moved about 33 rows each with no stall or jump: CPU 18 to 42% during flings, 0.0% at rest, and at most 34 rows in the accessibility tree, the page's 10 behind the sheet included (`R75-one-piece-sheet-final.png`, `R75c-one-piece-sheet-large.png`).

## The second run

| Field | Recorded value |
| --- | --- |
| Run | 2026-11-04, 03:11 to 04:41 EST |
| App | Debug build, Spine Dev. `R01` on the tree at `6313000b`. `P06` to `P09`, `R02` to `R56`, `R60-ax-sheet-scrolled.png` and `R61` to `R70` on the code committed as `cdf5dcdf`. `P01` to `P05b`, every other `R57` to `R60` file, and `R71` to `R75c` on `ba0940fe`, the final app code, which differs from `cdf5dcdf` only in a still's layout at accessibility text sizes |
| Simulator | Scratch iPhone 17s, iOS 26.5, created and deleted by the cleanup helper; hardware keyboard off |
| Backend | `scripts/ios/local-backend.sh` from this worktree on `127.0.0.1:8001`. Server code moved under it as another agent committed server fixes |
| Proxy | A small proxy on `127.0.0.1:8002` in front of the backend, the app's base URL. It could hold a write for up to 8 s, so that a tap lands before the answer (the app gives up at 10 s), answer a write with a 503, answer as a server older than this branch (no `anime` or `anime_rows` keys, 404 on the anime routes, `continue_watching` ignored for anime), and break one row of a list and one list state |
| AniList outage | One backend run with `HTTPS_PROXY` on a closed port and MAL in `NO_PROXY` |
| Time zone | America/New_York, and Pacific/Honolulu for `R28` |

The second run's screenshots are the `R` and `P` files in the same folder.

### The review's findings

| Finding | Commits | Result | Observed (law) | Evidence |
| --- | --- | --- | --- | --- |
| 1. A next title that's a single | `25752a1b`, `c5fd6546` | PASS | Demon Slayer (MAL 38000), whose next title is the movie (40456, `single` true, `has_aired` true). Closing the last tick's log screen left the sheet on the finished title, every tick filled. The page's "Next:" row shows the movie (AN-501). | `R12-season-sheet-up-next.png` to `R15-page-next-row-shows-the-movie.png` |
| 2. ✕ on the page's sheet before the answer | `6313000b` | PASS | Last row ticked in All Episodes, ✕ before the answer: the log screen opened over the page, and after Not now the page was up to date with no reopen. A failed write said so over the page, and the earlier-episodes question asked there (TV-701, TV-705). | `R04-closed-before-the-answer.png` to `R08-question-over-the-page.png` |
| 3. The heart, then the filled eye | `25752a1b`, `c5fd6546` | PASS | Cowboy Bebop (MAL 1): the heart marked it Completed, the filled eye put it back to Planning and took the heart off at once, and the next tap was a `POST` giving the heart (AN-301). | `R25-planning.png` to `R27-eye-undoes-heart-off.png` |
| 4. The day it started | `c5fd6546` | PASS | A first tick at 22:46 on Nov 3 in Honolulu, Nov 4 in UTC, read "Started November 3, 2026". TV reads through the same line, so it has the fix too. | `R28-started-on-the-users-day.png` |
| 5. A server older than this branch | `c5fd6546` | PASS, through the proxy | Home listed only In progress anime. + and the eye opened the log screen, the rail had no stars, and the heart and a log saved. Library read as usual. | `R30-old-server-home.png` to `R36-old-server-library.png` |
| 6. All Episodes with a tick on its way | `6313000b` | PASS | E52 ticked in the page's list, All Episodes tapped before the answer: E52 filled in the sheet at once, and still after the answer (TV-705). | `R02-all-episodes-opens-with-tick-on-its-way.png`, `R03-all-episodes-after-the-answer.png` |
| 7. Leaving the page before the last tick's answer | Not fixed, by decision | Not run | Same as TV's season page; one line added to AN-203 (`25752a1b`). | |
| 8. Log with the last tick on its way | `6313000b` | PASS | + Log on the page and ··· Log… in Home's sheet, chosen before the answer: one screen, the last tick's log screen, and after Not now nothing else opened (AN-203). | `R09a-log-tapped-before-the-answer.png`, `R09-one-log-screen.png`, `R10-after-not-now-one-screen.png`, `R16-sheet-menu.png`, `R17a-log-chosen-before-the-answer.png`, `R17-sheet-one-log-screen.png` |
| 9. "Next:" after finishing the next title | `c5fd6546` | PASS | Spy x Family: the "Next:" row opened Cour 2, it was finished there, and back on the first part the row was gone (AN-500). | `R21-next-row-before.png` to `R24-next-row-gone.png` |
| 10. The second card on the rail | `c5fd6546` | PASS | Cards for both parts of Spy x Family. The first finished in its sheet, the sheet moved on to Part 2, a tick there and ✕: Part 2's card read "E4 next, 3 of 13", with badge E4 and its bar moved, and the proxy's log shows no read of the rail (TV-701). | `R11-rail-before.png`, `R18-sheet-moved-on-to-part-2.png` to `R20-rail-second-card-moved.png` |
| 11. A title with no numbers leaving the library | `25752a1b` | PASS | Firefly Wedding: one row, a tick added a second, and unticking took the added row away with the title out of the library (AN-101). | `R44-no-numbers-one-row.png`, `R45-tick-adds-a-row.png`, `R49-untick-the-added-row-goes.png` |
| 12. A bad anime value | `6d6a725b` | PASS, through the proxy | A bad list state on Naruto's row: Home's rail still showed every card, and Naruto's read "2 of 220 episodes". A bad row: Attack on Titan listed E1, E3, E4 and on. | `R71-corrupt-home.png`, `R72-corrupt-page.png`, `R73-corrupt-list.png` |
| The sheet on iOS 27 | `6313000b`, `cdf5dcdf` | PASS on iOS 26.5 | The sheet now scrolls to a row and reads `contentOffset` plus `contentInsets`, so it doesn't depend on how an OS counts the bars over it. Home's sheet: bar bottom y=468 and E4 at 486 at medium, 118 and 136 at large, the same on first open, reopen, back to medium, and a third open. The page's sheet: 474 and E47 at 491, 128 and 146, on first open and reopen. One Piece's: 474 and E1180 at 491, 128 and 146. | `P01-home-first-medium.png` to `P05b-home-third-open-medium.png`, `P06-page-sheet-medium.png` to `P09-page-sheet-reopen-large.png`, `R75c-one-piece-sheet-large.png` |

### What the first run couldn't check

| Check | Result | Observed (law) | Evidence |
| --- | --- | --- | --- |
| "E1183" badges, "All 1189 Episodes" | PASS | Plain numbers on the badges and the row (V-02). | `R01-all-1189-plain-badges.png` |
| A single's page | PASS | No "1 episodes" chip and no Episodes row in Details; Planning's reason read "You've already watched this anime." (V-03, V-05, AN-002). | `R53-single-no-episodes-chip.png`, `R54-single-planning-reason.png`, `R55-single-details.png` |
| The log title | PASS | "Attack on Titan" over the page of that name (V-04). | `R56-log-title-reads-the-pages.png` |
| A failed write's message on the 503 | PASS | Firefly Wedding with AniList unreachable. The eye, stars and up to here said "Tracking Update Failed" and "Anime details are unavailable right now", the heart said so under "Like Update Failed", and the log screen said so on its save. The picker came back empty, and ticks still worked (AN-100, TV-705). | `R40-airing-no-anilist-page.png` to `R43-503-on-heart.png`, `R46-503-on-up-to-here.png` to `R48-503-on-a-log.png` |
| A title with no numbers | PASS | Firefly Wedding as above. Black Clover Season 2, with no total: one row, then two after a tick, and tracking lines with no total (N-18). | `R44-no-numbers-one-row.png`, `R50-no-total-one-row.png` to `R52-no-total-tracking-lines.png` |
| Accessibility text sizes | PASS after V-07 | At the Accessibility XL size and XXXL, the largest, on the final app: Home's cards, and the sheet with up next at the top at both heights (bar 468, row at 497; bar 118, row at 148), tick on screen. A tick at XL sent one `tick`. The page's rows, "All 220 Episodes", and its sheet at XXXL (474 and 503, 128 and 158). TV's sheet at XXXL, whose rows share the still. | `R58-ax-sheet-medium.png`, `R58b-ax-after-tick.png`, `R59-ax-sheet-large.png`, `R60-ax3-home.png`, `R60b-ax3-sheet-medium.png`, `R60c-ax3-sheet-large.png`, `R57-ax3-page-top.png` to `R57e-ax3-page-all-row.png`, `R60f-ax3-page-sheet-medium.png`, `R60g-ax3-page-sheet-large.png`, `R60d-ax3-tv-sheet-medium.png`, `R60e-ax3-tv-sheet-large.png` |
| Profile's In progress row | PASS | Posters only, Naruto and Fleabag, with no bar, badge or line (AN-701). | `R61-profile.png` |

### Nearby, once more

| Check | Result | Observed (law) | Evidence |
| --- | --- | --- | --- |
| TV: stars on a partway show | PASS | Log TV Show on Whole show. Cancel sent nothing, and the stars stayed off (TV-303). | `R62-tv-show-stars-whole-show.png`, `R63-tv-show-stars-cancelled.png` |
| TV: a heart on a partway season | PASS | That season's composer with the heart on. Cancel sent nothing, and the heart stayed off (TV-304). | `R64-tv-season-heart-opens-its-composer.png`, `R65-tv-season-heart-cancelled.png` |
| A movie | PASS | Its page, its eye and + opening the log screen as before. | `R66-movie-page.png` to `R68-movie-plus-opens-log.png` |
| A book | PASS | Its page and its menu as before. | `R69-book-page.png`, `R70-book-menu.png` |

## Defects

| # | What | Fix | Re-run in the simulator |
| --- | --- | --- | --- |
| V-01 | Season 2's rows took the first season's episode titles: AniList files those 25 under Season 2's id, which has 12. | `anime_listing._apply_episodes` drops a list running past the total (`62a2d01d`, with `test_an_101_a_list_running_past_the_total_isnt_this_titles`). | Yes, flow A |
| V-02 | Badges on long titles read "E1,183" beside "Episode 1183": `Text` formats a number it interpolates. | `Text(verbatim:)`, and "All 1189 Episodes" to match every other number here (`e1c4b37d`). | Yes, `R01` |
| V-03 | A single's page had a "1 episodes" chip and an Episodes row in Details, against "no number anywhere" (AN-002). | Both are left out on a single (`e1c4b37d`). | Yes, `R53`, `R55` |
| V-04 | The log screen read "Shingeki no Kyojin" over a page reading "Attack on Titan". | It reads the page's title (`e1c4b37d`, `testAN400TheLogScreenReadsTheTitleThePageReads`). | Yes, `R56` |
| V-05 | A watched single's Planning reason said "Pause or drop it instead", which a single can't do. | "You've already watched this anime." (`d5cbf660`, `test_an_002_a_watched_single_doesnt_offer_a_pause_or_a_drop_it_hasnt_got`). | Yes, `R54` |
| V-06 | Once the sheet named a row to scroll to, the page's sheet opened at E1 the first time: its first reading of the scroll view moved the built rows off up next's row, and a position set before the first layout doesn't move a list that has its rows from the start. Home's sheet hid it, because its list changes after loading. | The built rows stay around that row until the list gets there, and the list scrolls to it on appearing (`cdf5dcdf`). | Yes, `P06` to `P09`, and One Piece's `R75` |
| V-07 | At accessibility text sizes a row's still ignored the row's width under a fixed height: ticks at x=-88 and rows wider than the list. | The still takes its height from the row's width (`ba0940fe`, `testAN101ARowAtAnAccessibilitySizeStaysInsideItsList`, which failed first at 602 against 370 pt). The broken state is `R60-ax-sheet-scrolled.png`. | Yes, the accessibility row above |

## Limits

- Simulator on iOS 26.5; the owner's phone runs iOS 27, and these runs say nothing about it. The sheet no longer relies on how an OS counts its bars, which removes the known risk, not every risk.
- The older server was imitated by the proxy, not run: `myapp-main` itself never answered the app here.
- The page's extra 0.4 s against a short title wasn't profiled.
- TV's Home sheet has the collision finding 8 fixed on anime: its `openLog` in `ContinueWatching.swift` doesn't wait for a tick on its way. TV pages got the wait through `presentAfterSettling`; that sheet was left alone, as out of scope, and wasn't run.
- MAL dropped a connection once in the second run, and the page read "Could not load media, Request to 127.0.0.1 failed (HTTP 500)"; Try Again loaded it. `services.api_request` lets a connection error through for every provider, and this branch doesn't change it.
- Watching, Resume, Pause and Drop on a single answering 409 needs nothing from the app: flow D's menu never offers them.
- Lists have no `display_title`, so Home, Library, the sheet and the diary read MAL's romaji ("Shingeki no Kyojin") where the page reads "Attack on Titan". That predates this work and is unchanged. The brief's example VoiceOver label therefore reads with the romaji title on this data.
- A title airing an episode during the runs wasn't observed (AN-202).
- At the largest text sizes, the title under a Home card breaks inside a word ("Naru" / "to", `R60b-ax3-sheet-medium.png`), Fleabag's as well. That row is every type's and this work doesn't change it.
- Not run here: webhooks, imports, export, PostgreSQL.

## Runs and cleanup

| Run | Purpose | Receipt |
| --- | --- | --- |
| `12b86aaf` | Local backend | Killed at the harness's 2-hour limit. The wrapper died with it, so its journal stays `cleaning`, `review_required` true, and `stop` refuses it. Checked by hand: process group 73192 is empty and port 8001 is free |
| `535feb06` | Scratch simulator (`--queue ios --lane sim`); installed, launched and waited; never compiled | Stopped; cause `cancelled`, exit 143, no leftovers; simulator deleted |
| `66dcbf7c`, `29ea1cdd`, `4a47b71c` | Simulator builds (`--queue ios`) | `66dcbf7c` a compile failure, fixed; the other two `command_exit` 0. Each exit 125: Xcode's detached helpers, left alone by the wrapper, gone when checked afterwards |
| `1d630a86` | The whole of SpineTests on the final code, 1,567 tests, no failures (`--queue ios`, its own scratch simulator) | `command_exit` 0, `cleanup_complete` true, no leftovers |
| `eff49601` | `scripts/check.sh`: ruff, no missing migration, 4,732 tests OK (38 skipped) | `command_exit` 0, `cleanup_complete` true, no leftovers |
| `6428f8a8`, `43692cf5` | Second run: local backend | Stopped; cause `cancelled`, exit 143, no leftovers |
| `e97d5955` | Second run: local backend with AniList unreachable | Stopped; cause `cancelled`, exit 143, no leftovers |
| `b0a07260`, `31648768` | Second run: the proxy | Stopped; cause `cancelled`, exit 143, no leftovers |
| `5b13c0ac`, `305d16da`, `e259608e`, `364f0224` | Second run: scratch simulators (`--queue ios --lane sim`); installed, launched and waited; never compiled | Stopped; cause `cancelled`, exit 143, no leftovers; simulators deleted |
| `188aa6f6`, `38f7f194`, `af175774`, `466d8e43` | Second run: simulator builds (`--queue ios`) | `command_exit` 0. Each exit 125: Xcode's detached helpers, left alone by the wrapper, gone when checked afterwards |
| `1bd64642` | The whole of SpineTests on the final app code, 1,588 tests, no failures (`--queue ios`, its own scratch simulator) | `command_exit` 0, `cleanup_complete` true, no leftovers |
| `04d5de38` | `scripts/check.sh` at `ba0940fe`, in a clean checkout: ruff, no missing migration, 4,835 tests OK (38 skipped) | `command_exit` 0, `cleanup_complete` true, no leftovers |
