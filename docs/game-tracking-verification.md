# Game tracking verification

## Follow-up verification: 2026-09-30

The maintainer authorized deployment and installation after the original review.
An existing Playing title without a playthrough now keeps its Playing action enabled.
That action starts tracking without assigning old progress to an invented attempt.
New backend and native regression tests cover this case.

The full affected backend suite passed 365 tests on isolated PostgreSQL 16.
This includes the representative upgrade from migration 0077 through 0079.
Six deployment workflow tests and Ruff also passed.
The deployment workflow now verifies a private database backup before replacing the app.
It checks the deployed commit, pending migrations, and health, then clears old cache.

Final combined native run: 456 unit tests and one live-backend UI test passed.
No test was skipped. The UI test started from an existing Playing title without a playthrough.
It verified start, zero progress, clearing time, refetch, rating prefill, validation, and cancellation.
The old progress value remained stored. Cancelling completion created no diary entry.
Result: `/Users/armaandave/Library/Developer/XcodeBuildMCP/workspaces/spine-9e3122703ae8/result-bundles/test_sim_2026-09-30T06-14-02-334Z_pid25659_1fc493cd.xcresult`.

The first combined run found a stale fallback-media test after the saved UI changes removed board games.
Its local expectation now matches those saved changes.
The UI size assertion now allows 0.001 points for floating-point reporting of a 44-point control.
One shared-Keychain authentication test failed once, then passed unchanged in the full final run.
Production login and read-only game-library and game-tracking requests also passed.

Production deployed application commit `4a35d2a4` through workflow run `36676590350`.
The workflow verified the running commit, completed migrations, cleared cache, and passed health checks.
Verified backup on the server: `/Users/armaandave/projects/spine-backups/pre-deploy-36676590350-1.dump`.

The signed Debug build installed and launched on Armaans iPhone 14 pro max on 2026-09-30.
Bundle ID: `com.armaan.Spine`. No uninstall or app-data reset occurred.
Build log: `/private/tmp/spine-device-game-tracking-end-to-end.log`.
The build uses the production API default and includes the saved UI changes present at this run's start.
Those UI changes remain uncommitted in the game worktree. The original checkout was not changed by this task.
Later, separate authentication edits appeared in the original checkout. This build excludes that unfinished work.
Installation and launch were verified through device services; physical-screen interaction was not claimed.

The sections below retain the original implementation evidence and its earlier limits.

## Status

Implementation, independent review, automated checks, and real local Simulator verification are complete. All 35 acceptance cases have recorded evidence in the coverage matrix. The limits below distinguish automated checks from observed native actions.

## Source and isolation

- Worktree: `/Users/armaandave/.codex/worktrees/game-tracking-end-to-end/spine`
- Branch: `codex/game-tracking-end-to-end`
- Base: `95b578dfc23b4b0dced3ff35b60ae599f2fdec81` (`myapp-main`; fetched local and remote refs matched).
- Final commit: the commit containing this report; resolve with `git log -1 --format=%H` on this branch.
- Original checkout: no implementation changes made there.
- Backend deployment, push, merge, and pull request: not performed.

The approved documents match the original checkout byte for byte. SHA-256 values:

| Document | SHA-256 |
| --- | --- |
| `game-tracking-contract.md` | `f2e0cb003eaadcf6a07a4eda548abc0a9ad5207d28fe51a978ffd4d4523b237f` |
| `book-tracking-contract.md` | `2790e5b40fce9ba6332e6ef87106d3b2a86d2556944c178a19ab2b1a737bcc18` |
| `single-weight-consumption-contract.md` | `0647f477ff1145c5dab23ef5f92239a205d7b8654fc8ca98b7be77b37cd102e7` |

## Implemented backend behavior

Games use canonical playthrough transitions with separate optional minute totals and percentages. Completion saves the diary snapshot and playthrough atomically. Later progress, independent field source links, backdating, completion ordering, replay counts, restoration, and safe history removal follow the game contract.

Generic tracking, diary, heart, rating, legacy web/form, and import mutations use these transitions. Owner checks, account visibility, half-star ratings, asynchronous statistics updates, library state, and lifetime completion counts remain integrated with shared foundations. Completion and restart use stable mutation identifiers. Same-status drop retries preserve their playthrough and progress.

Steam imports keep lifetime minutes separate. Existing statuses remain unchanged. New Steam games enter Planning. Imports preserve source dates and ambiguous legacy data without fabricating attempts. Explicit title opinions cannot bypass an unfinished playthrough.

See `game-tracking-coverage.md` for the law matrix and G-01 through G-35. Backend tests cover every acceptance case. Simulator rows record observed native actions separately.

## Automated checks

Runtime: `/Users/armaandave/projects/spine/.venv/bin/python` (Python 3.12.13, Django 5.2.14). Commands ran from this worktree with isolated test databases. External provider responses use deterministic fixtures. These tests do not establish live provider or native Simulator verification.

The affected backend run passed **330 tests** before the final review regressions. Log: `/private/tmp/game-backend-final.log`.

Final clean affected run on 2026-09-28: **364 tests passed**, zero failures, in 41.242 seconds. This includes the completion-date error regression. Django created and destroyed a fresh test database. Authoritative log: `/private/tmp/game-backend-authoritative-final.log`. Earlier 363-test and 355-test clean runs also passed; logs: `/private/tmp/game-backend-calendar-final.log` and `/private/tmp/game-backend-clean-final.log`. Command:

```sh
python src/manage.py test \
  api.tests.test_game_tracking_contract \
  api.tests.test_game_tracking_entrypoints \
  api.tests.test_book_tracking_contract \
  api.tests.test_single_weight_consumption_contract \
  api.tests.test_stats \
  app.tests.test_game_tracking_migration \
  app.tests.models.test_game \
  integrations.tests.test_game_tracking_contract \
  integrations.tests.imports.test_steam \
  integrations.tests.test_steam_update \
  integrations.tests.imports.test_hltb \
  integrations.tests.imports.test_yamtrack \
  integrations.tests.test_exports \
  app.tests.test_forms \
  app.tests.test_middleware \
  app.tests.test_game_tracking_web \
  app.tests.test_game_tracking_review \
  api.tests.test_completion_progress \
  api.tests.test_api_v1 \
  api.tests.test_music_phase7 \
  api.tests.test_music_cache
```

Earlier independent combined review passed 76 tests. The final run includes failing-then-fixed regressions for dropped-playthrough retries, unchanged form rating sources, and local calendar dates. Earlier targeted review passed 42 tests, including same-day completion ordering. Logs: `/private/tmp/game-final-review.log`, `/private/tmp/spine-game-contract-combined-tests.log`, and `/private/tmp/spine-game-drop-retry-review.log`.

- `ruff check src`: passed in the final run.
- `python src/manage.py check`: passed.
- `python src/manage.py makemigrations --check --dry-run`: passed; no changes detected.
- `git diff --check`: passed. Final check log: `/private/tmp/game-backend-authoritative-checks.log`.
- Fresh isolated SQLite database: all migrations applied; no pending migration plan; system and model consistency checks passed. Final log: `/private/tmp/game-backend-authoritative-fresh-migrations.log`. Fresh database: `/var/folders/30/fq1d3w9d3_n1j2nzfy_9lp880000gn/T/spine-game-fresh-41itz8wf/db.sqlite3`.
- Existing book upgrade regression: one test passed independently. Log: `/private/tmp/game-book-migration-regression.log`.
- Representative game upgrade: passed within the final 364-test run. The migration test starts at `0077`, applies `0078` and `0079`, and checks retained rows, history, dates, progress, uniqueness, and absence of fabricated playthroughs.

Live Simulator testing found a local-day error: progress recorded late in New York used the server UTC date. A same-day completion correction then appeared backdated. Game progress requests now accept `progressed_on`. The native client also sends `X-Spine-Timezone`. API middleware validates that optional IANA timezone, scopes it to one request, and restores the prior timezone. Missing or invalid values retain the server default. Date-only diary statistics retain the selected calendar date. Tests cover New York, UTC+14, invalid timezone names, context reset, same-day coupling, and statistics. No schema change is needed. Evidence: `/private/tmp/game-calendar-before.log`, `/private/tmp/game-calendar-stats-before.log`, and the final clean run.

A later native validation review found that an invalid completion date named the start date in its error. The backend now reports "Completion date cannot be before the playthrough start date." for completion creation and diary date edits. Start-date edits retain their own validation message. The regression failed before the fix. Afterward, `python src/manage.py test api.tests.test_game_tracking_contract app.tests.test_game_tracking_web app.tests.test_game_tracking_review --keepdb` passed **42 tests**. Logs: `/private/tmp/game-completion-date-message-before.log` and `/private/tmp/game-completion-date-message-after.log`. Ruff and whitespace checks passed again.

Generated logs and local databases stay outside source control.

## Existing music fixture mismatch

An untouched archive of base commit `95b578d` reproduces three music test failures. Evidence: `/private/tmp/game-proven-baseline.log`. Two fail because generic media summaries emit an unnecessary `position: null`. The shared serializer now emits `position` only when supplied, and both tests pass without fixture changes.

One frozen response test still fails: `api.tests.test_music_phase9_contract.MusicPhase9ContractTests.test_ios_music_contracts_match_frozen_responses`. It also fails on the untouched base. The response contains existing additive fields absent from the frozen fixture. No existing field values differ. Exact extra paths:

- `/activity/results/0/object/name` and `/activity/results/{0,1,2}/person`.
- `/album_detail/completion` and `/album_detail/external_ratings_preparation`.
- `/list_create/{entries_count,list_type,people_count}`.
- `/list_detail/{completion,entries_count,list_type,people,people_count}`.
- `/lists/results/0/{entries_count,list_type,people_count,preview_people}`.
- `/statistics/list_progress`, `/statistics/series_progress`, and `/statistics/overview/completion`.
- `/statistics/media_types/0..7/completion`.

Examples: album preparation is `{state: "ready", retry_after_seconds: 2}`; list/overview completion is `{completed_count: 1, total_count: 1}`. Evidence: `/private/tmp/game-music-fixed.log` and `/private/tmp/game-music-diff.log`. The frozen fixture and its assertion remain unchanged. A separate final rerun of this exact test still has **one failure**, matching the known baseline mismatch. Latest log: `/private/tmp/game-backend-authoritative-baseline.log`. This known failure is separate from the 364 passing affected tests. No new backend test failure remains.

## Migration and deployment handoff

The user must deploy the backend after review and merge. Do not deploy from this task.

1. Back up the production database using the normal deployment procedure.
2. Deploy the merged backend code and dependencies.
3. Run `python src/manage.py migrate` in the backend environment.
4. Run `python src/manage.py check` and restart the backend and existing Celery workers.
5. Build the native app from the merged code.

Migration `0078_game_playthrough_tracking` adds playthroughs, nullable progress, source links, import fields, and constraints. It preserves old progress and dates. It creates no attempts from ambiguous legacy data. Existing completed titles receive only supported completion evidence.

Migration `0079_preserve_legacy_game_duplicates` retains every existing Game row. It marks older duplicate rows as `legacy_archived` and enforces one canonical row per user/item. The newest row remains canonical, matching prior library selection. Archived data and audit history remain stored and accessible through the unfiltered manager. No history is silently deleted.

No provider backfill, production data reset, or temporary API configuration is required. The app keeps its production API default. Local runtime overrides and credentials are not committed.

## Native implementation and tests

Native changes cover the media detail action rail, plus menu, progress editor, completion composer, diary editor, Play History, five library sections, Home, profile, and statistics summaries. Game hours and percentage remain independent. Editors preserve unknown versus zero. Save failures retain drafts. Cancellation restores current opinions. Clear controls, keyboard dismissal, the accessible rating Slider and value, a 44-point rating confirmation target, and destructive confirmations were checked.

Final clean build and test run on 2026-09-28 passed **451 unit tests and one real-backend UI test**, with zero failures. The unit suite includes 17 game contract tests and existing model, networking, book, movie, music, repository, and view-model regressions. The live UI test took 87.795 seconds. Results: `/private/tmp/spine-game-ios-final-verified.xcresult`; log: `/private/tmp/spine-game-ios-final-verified.log`.

```sh
xcodebuild -project ios/Spine/Spine.xcodeproj -scheme Spine \
  -destination 'platform=iOS Simulator,id=CD120422-A6A5-49CD-8A8C-B19A276C1C77' \
  -derivedDataPath /private/tmp/spine-game-ios-build \
  -resultBundlePath /private/tmp/spine-game-ios-final-verified.xcresult \
  -parallel-testing-enabled NO \
  -only-testing:SpineTests \
  -only-testing:SpineUITests/SpineUITests/testLocalBackendLoginAndLibrarySmoke \
  clean test
```

The live test receives local credentials through temporary runtime environment variables. No credentials enter source control. It signs in to the changed Django backend, opens the QA library record, saves 0h/0%, clears hours while retaining 0%, refetches, cancels edits, selects 4.5 stars, confirms composer prefill, dismisses the numeric keyboard, checks a visible 101% validation error and retained draft, then cancels without changing current opinions.

Seven app compiler warnings remain in the existing FilterModels, StatsModels, and ProfileView actor-isolation code. Existing test actor-isolation warnings also remain. Earlier host sleep interrupted UI runs and caused Accessibility timeouts. Those runs are not reported as passes. The final awake clean run succeeded.

## Real local integration

- Backend: this worktree, `http://127.0.0.1:8017`, isolated SQLite database `/private/tmp/spine-game-runtime/game.sqlite3`.
- Local settings, fixtures, and credentials: outside the repository. Cache is local memory; Celery tasks run eagerly for this disposable environment. Automated tests check task dispatch. A separate Redis/Celery worker was not exercised live.
- Primary Simulator: iPhone 17, iOS 26.5, UDID `7990CE55-B413-4CCC-8F92-45FD2A33CF32`.
- Independent audit Simulator: `F9BB2486-1E78-4197-8A65-81B574413E25`.
- XCTest Simulator: iPhone 17 Pro, `CD120422-A6A5-49CD-8A8C-B19A276C1C77`.
- Final app installed and launched on the primary device: `/private/tmp/spine-game-ios-build/Build/Products/Debug-iphonesimulator/Spine.app`.
- App launch override: `SPINE_API_BASE_URL=http://127.0.0.1:8017`. The production default is unchanged.

Native taps and text edits used XcodeBuildMCP. The real primary and audit devices were mirrored at localhost ports 3200 and 3201. Browser frames and device IDs were checked. All product actions used the real changed API. Direct database reads corroborated saved state; they did not replace UI interaction.

Observed flows include status-only completion; independent hours and percentage; zero, clear, replacement totals, and downward corrections; pause/resume/drop/restart; accidental attempt deletion; direct retrospective states; undated completion; eye/heart/rating cancellation and save; continued post-completion progress; linked and independent log corrections; backdating; replay badge choices; completion-date selection; current, direct, and older log deletion; Play History editing/removal; library movement through all five sections; diary/activity/current-opinion refresh; navigation, refetch, and relaunch persistence.

A controlled local server outage tested a completion draft with progress and heart. Failed save left the original playthrough unchanged and created no log. Restarting the server and retrying saved exactly one completion. The first error appeared below the scroll area. The fixed footer now shows errors above Save; the final live XCTest verified visible error text and retained draft without scrolling.

The live import fixture used the canonical import service. The app showed 300 imported lifetime hours separately from a 10-hour playthrough. Repeating the import preserved the playthrough and status. New imported state appeared in Planning. Automated integration tests cover Steam provider mapping and every existing status using deterministic provider responses. No external Steam account was contacted.

## Review and evidence

Independent review covered the complete contract, inherited opinion/privacy rules, mutation entry points, imports, constraints, migrations, restoration, and native state handling. Root reviewed the combined changes and reran affected live flows. Actionable findings were fixed and retested. No unresolved contract defect remains.

Evidence is preserved outside source control:
`/Users/armaandave/.codex/visualizations/2026/09/28/01a0e5c3-3297-7630-99c8-d366688a55ba/game-tracking-evidence/`.

This directory contains both interaction journals, final backend/build logs, and screenshots. Useful files: `completion-selection-after-delete.jpg`, `completed-library.jpg`, `steam-lifetime-separate.jpg`, `audit-g28-rating-heart-saved.jpg`, `ios-ui-visible-error.png`, and `ios-ui-cancel-preserves-zero.png`. The older network-error screenshot is labelled as preceding the footer fix.

## Verification limits

- G-01 through G-35 all have passing automated coverage and recorded native or import-result evidence. Read the matrix for exact actions, not a claim that every input permutation was entered manually.
- G-05: live 101% and nonnumeric hours rejection passed. The native input tool could not enter negative or decimal text reliably. Swift validation and backend tests cover these inputs; a manual negative/decimal paste is not claimed.
- G-31: native future calendar dates are disabled. Before-start completion rejection passed live. Backend tests reject submitted future dates.
- G-32: tracked start dates have no clear control. A later-than-progress/completion edit failed live. Backend tests reject explicit null and all date-order violations.
- Duplicate submissions, same-day log distinctions, invalid null/omitted fields, privacy, and alternate mutation paths have automated coverage. The live outage tested one retry; no manual simultaneous double-tap timing claim is made.
- Migrations were verified on fresh and representative upgraded SQLite databases. PostgreSQL was not run locally.
- The one pre-existing music snapshot mismatch remains documented above. No production deploy, external provider verification, push, merge, or pull request was performed.
