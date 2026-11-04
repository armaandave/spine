# Anime Tracking Simulator Verification Record

Overall status: **CHECKS A TO F PASS; FOUR LATER FIXES NOT RE-RUN IN THE SIMULATOR**

Every flow below matched the laws in [the anime tracking contract](anime-tracking-contract.md) and, for flow E, TV-303 and TV-304 in [the TV contract](tv-tracking-contract.md), in the real Spine Dev app.
The run found five defects. One was fixed and re-run during the run. The other four were fixed after the last flow, and by then the local backend had hit its 2-hour limit, so they have tests or a code read but no second look in the app (see [Defects](#defects) and [Limits](#limits)).
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

## Defects

| # | What | Fix | Re-run in the simulator |
| --- | --- | --- | --- |
| V-01 | Season 2's rows took the first season's episode titles: AniList files those 25 under Season 2's id, which has 12. | `anime_listing._apply_episodes` drops a list running past the total (`62a2d01d`, with `test_an_101_a_list_running_past_the_total_isnt_this_titles`). | Yes, flow A |
| V-02 | Badges on long titles read "E1,183" beside "Episode 1183": `Text` formats a number it interpolates. | `Text(verbatim:)`, and "All 1189 Episodes" to match every other number here (`e1c4b37d`). | No |
| V-03 | A single's page had a "1 episodes" chip and an Episodes row in Details, against "no number anywhere" (AN-002). | Both are left out on a single (`e1c4b37d`). | No |
| V-04 | The log screen read "Shingeki no Kyojin" over a page reading "Attack on Titan". | It reads the page's title (`e1c4b37d`, `testAN400TheLogScreenReadsTheTitleThePageReads`). | No |
| V-05 | A watched single's Planning reason said "Pause or drop it instead", which a single can't do. | "You've already watched this anime." (`d5cbf660`, `test_an_002_a_watched_single_doesnt_offer_a_pause_or_a_drop_it_hasnt_got`). | No |

## Limits

- **The local backend hit the 2-hour limit on background commands at about 01:25 EST, and it was not restarted, because the limit's notice says not to.** So V-02 to V-05 weren't looked at in the app, and the page's extra 0.4 s wasn't profiled.
- The server review's retryable 503 came in after the flows ran, and wasn't seen in the app: while MAL says airing and AniList hasn't answered, anything that fills the rest is refused. It takes the path of any failed write, which restores the row and shows the server's message: `testTV100ATickGoesThroughTheQueueShowsAtOnceAndRollsBackOnFailure` and `testTV705AFailedTickInTheSheetRestoresTheRowAndSaysSo`. Watching, Resume, Pause and Drop on a single answering 409 needs nothing from the app: flow D's menu never offers them.
- Profile's In progress row wasn't opened. It uses the same loader as Home's and draws posters only, for every media type.
- Accessibility text sizes weren't run. `AnimeListMetrics` and the fixed 460 pt rows have tests only.
- Lists have no `display_title`, so Home, Library, the sheet and the diary read MAL's romaji ("Shingeki no Kyojin") where the page reads "Attack on Titan". That predates this work and is unchanged. The brief's example VoiceOver label therefore reads with the romaji title on this data.
- A title with no numbers (N-18) wasn't seen in the app: no such title was to hand. `AnimeTrackingTests` covers a tick adding a row.
- Simulator on iOS 26.5; the owner's phone runs iOS 27, and this run says nothing about it.
- A title airing an episode during the run wasn't observed (AN-202).
- Not run here: webhooks, imports, export, other time zones, PostgreSQL.

## Runs and cleanup

| Run | Purpose | Receipt |
| --- | --- | --- |
| `12b86aaf` | Local backend | Killed at the harness's 2-hour limit. The wrapper died with it, so its journal stays `cleaning`, `review_required` true, and `stop` refuses it. Checked by hand: process group 73192 is empty and port 8001 is free |
| `535feb06` | Scratch simulator (`--queue ios --lane sim`); installed, launched and waited; never compiled | Stopped; cause `cancelled`, exit 143, no leftovers; simulator deleted |
| `66dcbf7c`, `29ea1cdd`, `4a47b71c` | Simulator builds (`--queue ios`) | `66dcbf7c` a compile failure, fixed; the other two `command_exit` 0. Each exit 125: Xcode's detached helpers, left alone by the wrapper, gone when checked afterwards |
| `1d630a86` | The whole of SpineTests on the final code, 1,567 tests, no failures (`--queue ios`, its own scratch simulator) | `command_exit` 0, `cleanup_complete` true, no leftovers |
| `eff49601` | `scripts/check.sh`: ruff, no missing migration, 4,732 tests OK (38 skipped) | `command_exit` 0, `cleanup_complete` true, no leftovers |
