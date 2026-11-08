# Ship Spine: feature to production and TestFlight

Canonical workflow for the `spine-ship` skill, used by both Claude Code
(`.claude/skills/`) and Codex (`.agents/skills/`). Those skill files point here; change
the workflow in this file only. Testing a branch on the iPhone before it ships is
`spine-test-on-iphone` (`scripts/ios/TEST_ON_IPHONE.md`).

## The user's prompts

| When | Say |
| --- | --- |
| Starting work | "New feature: <what>. New worktree and branch." |
| Trying it on the phone | "Test this on my iPhone." |
| It's done | "Ship this." (Workflow A) |
| A TestFlight build of what's shipped | "Make a TestFlight build." ("TestFlight builds") |
| Something broke | "Production broke after the last ship. Roll back." (Workflow B) |

## How shipping works

- **A push to `myapp-main` deploys production once CI passes.** GitHub Actions
  (`.github/workflows/ci.yml`) runs "Fast checks" (`scripts/check.sh`: ruff, models
  without a migration, the test suite on SQLite) and "Database checks" (every migration
  and the suite on Postgres 16, like production). When both pass, "Deploy production"
  runs `deploy-backend-desk-mac.yml` on this Mac's self-hosted runner. It backs up the
  production database to `~/projects/spine-backups/pre-deploy-<run>.dump`, rebuilds the
  Docker stack from `~/projects/spine-deploy` at exactly the pushed commit, checks
  health, the deployed commit and `migrate --check`, and clears the cache. Production
  always comes from `myapp-main`.
- **The deploy skips a push that doesn't change the backend** (`src/`, the Dockerfile,
  the production compose file, `pyproject.toml`, `uv.lock`, the deploy scripts), and a
  push that a newer push has overtaken. The job log says which.
- **Migrations run during the deploy**, when the new container starts (`entrypoint.sh`),
  right after the backup. There is no separate step. The site is down for the seconds
  the container takes to restart.
- **A push that changes the iOS app also makes a TestFlight build.** Xcode Cloud starts
  when a pushed commit changes `ios/Spine/Spine/`, `ios/Spine/Spine.xcodeproj/` or
  `ios/Spine/Configs/`, archives the `Spine` scheme and uploads a build with the next
  build number. Never write `[ci skip]` or `[skip ci]` in a commit message, even in a
  sentence about it: GitHub Actions honors it too and would skip CI and the deploy.
- **A new build reaches only the internal "Me" group**, once it's in that group
  (step 9). There is no external tester group yet.
- **Installed builds keep calling today's API.** TestFlight builds run until they
  expire (90 days at most), so the API must keep working for every installed build.

## Workflow A: ship a finished feature

Run from the feature worktree unless a step says otherwise.

1. **Machine first, then status.**
   - Before anything else: if the branch changes the iOS app
     (`git diff --name-only origin/myapp-main... -- ios/Spine`, or uncommitted changes
     there) and `scripts/ios/machine.sh` prints `unset`, ask Armaan right away in the question
     picker ("Build on the laptop or on HomeMac?", `LAPTOP.md` says how), wait, and save the answer:
     `scripts/ios/machine.sh set <laptop|homemac>`. Every iOS build and test below reads
     `scripts/ios/machine.sh`, never memory (`scripts/ios/LAPTOP.md`). Don't ask again later
     in the ship.
   - `git fetch origin`, then `scripts/ios/feature-status.sh`. Merge
   `origin/myapp-main` into the branch if it has moved, and resolve conflicts.
2. **Verify.**
   - Run `scripts/check.sh` (the same checks as CI's Fast checks) and fix anything it
     reports.
   - If iOS changed, build it and run the iOS tests on the machine `scripts/ios/machine.sh`
     names: HomeMac's `ios` queue (`ios/AGENTS.md`) or the laptop (`LAPTOP.md`).
   - For non-trivial changes, run a code review (`/code-review` in Claude Code) and fix
     the findings.
3. **Compatibility.** Check every changed file under `src/api/` (feature-status lists
   them) against installed builds.
   - Adding endpoints, optional fields and new optional request fields is safe.
   - Removing or renaming fields or endpoints, changing types or status codes, or
     requiring new request fields breaks installed builds. Stop and propose expand and
     contract (below).
4. **Migrations.** List each migration the branch adds.
   - Additive ones (new tables, nullable columns, indexes) ship with "ship this".
   - Destructive ones (drops, renames, new `NOT NULL` on existing rows, tighter
     constraints, data rewrites) are off by default (`AGENTS.md`) and need the user's
     own yes. The pre-deploy backup is the way back.
   - **Test them on Postgres first:** push the branch (`git push -u origin <branch>`)
     and wait for its "Database checks" to pass before merging. `scripts/check.sh` only
     uses SQLite.
5. **Tell, don't re-ask.** The user's "ship this" (or "ship it", "merge it") is the yes
   for this whole workflow, including adding the build to "Me". Tell them in one
   message:
   - what ships, in plain English
   - the backend changes and the compatibility verdict
   - each migration (it runs during the deploy)
   - any one-time data step the ship carries ("One-time data steps", below), which
     runs after the deploy, or waits for the user's request where the step says so
   - that the push deploys production once CI passes, and makes TestFlight build ~N if
     the iOS app changed, which you'll add to "Me"

   Then carry on.
6. **Ship.**
   - Get in the ship line, from the feature worktree, in the background:
     `~/.local/bin/agent-cleanup run --owner <task> --label spine-ship-merge --timeout 14400 -- scripts/ship-merge.sh <branch> "<merge message>"`.
     The message ends with the trailers your instructions require; commits are
     `armaandave@outlook.com`.
   - One ship at a time goes through the line; the rest wait (up to 3 hours, `Waiting:`).
     In its turn it catches the main checkout (`~/projects/spine`, which must be clean and
     on `myapp-main`) up to `origin/myapp-main`, runs `git merge --no-ff <branch>` and
     `scripts/check.sh` on the result, pushes, and runs `scripts/ios/test-backend.sh update`
     (the always-on test backend Spine Dev talks to then serves the new `myapp-main`).
   - It prints `SHIPPED: <sha>`. On a conflict or failing checks it undoes its own merge
     and stops: fix the branch (merge `origin/myapp-main` into it) and run it again. Never
     merge or push `myapp-main` by hand outside the line.
7. **CI and the deploy.** Find the run with `gh run list --commit <sha>`, then
   `gh run watch <run-id> --exit-status`.
   - Red checks: nothing was deployed. Fix forward on a branch and ship again.
   - Red deploy: production may be down or half-deployed. Read the failure
     (`gh run view <run-id> --log-failed`), then fix it or roll back (Workflow B).
8. **Verify production,** when the deploy ran.
   - `docker exec spine printenv SPINE_COMMIT_SHA` is the pushed commit.
   - `https://api.spine-api.com/api/v1/health/` answers 200, and one changed endpoint
     answers as expected (an unauthenticated request to a new route gets 401, not 404).
   - `docker logs --since 10m spine 2>&1 | grep -iE 'error|traceback'` shows nothing new.
   - If the ship carries a one-time data step ("One-time data steps", below), run it
     now. The user's ship request covers it, except a part the step says waits for
     the user's request. Where the step's section says so, run its dry run and send the
     user its count first; that message, not step 5's, is the one to say stop to.
   - Once music albums have moved to Apple Music, one moved album's page answers 200
     with `ref.source` `apple_music`.
9. **Build,** only if the push changed the iOS app; otherwise skip to step 10. If
   `AGENTS.md` ("App Store Connect") has no Spine app ID yet, TestFlight isn't set up:
   skip this step and say so.
   - Poll `ASC_APP_ID=<Spine app id> node ~/.appstoreconnect/asc.mjs status` every few
     minutes until the new build is `VALID`.
   - If "Me" doesn't have build N, add it. Get the build's ID with
     `ASC_APP_ID=<id> node ~/.appstoreconnect/asc.mjs get '/v1/builds?filter[app]=<id>&filter[version]=<N>&fields[builds]=version'`,
     then
     `node ~/.appstoreconnect/asc.mjs post '/v1/betaGroups/<Me group id>/relationships/builds' '{"data":[{"type":"builds","id":"<build-id>"}]}'`.
     It prints `{"status": 204}`. The user's ship request covers this write.
   - Confirm with `asc.mjs status` that "Me" lists the build.
   - Tag the commit `ios-<version>-build<N>` and push the tag. Tags don't trigger
     builds.
10. **Clean up.** If `scripts/ios/test-backend.sh status` says it serves the feature
    worktree, run `scripts/ios/test-backend.sh release` from it. Then remove the feature
    worktree and the merged branch, local and remote.
    Then, from the main checkout, delete any scratch simulators the feature left behind:
    `scripts/ios/delete-scratch-sims.sh .claude/worktrees/<name>`. The cleanup helper deletes
    each run's simulator when the run ends; this catches any it didn't, at a few GB each on
    this Mac's nearly full disk. Report any it skips.
11. **Report** the deploy (or why it was skipped), the CI result, migrations, the build
    number and whether it's in "Me", and anything left to do.

## One-time data steps

Some ships carry a data step or a look that has to run once, around their deploy (Workflow A,
step 8). Each one says which ship carries it.

### Activity's Phase 5: the Planning row, the date picker and row editing

The last phase. A day of titles added to Planning is one row ("Added 3 shows to Planning"),
a book, manga or comic save can be put on the day it happened (games already could), and
an older progress row can be fixed and the newest taken back from its own page. No
migration, no data step. **There is a TestFlight build:** the iPhone app changes
(`ios/Spine/Spine/`), so Xcode Cloud archives `Spine` after the push. Tag the commit behind
the build and check "Me" has it (step 9).

Nothing in this ship touches production's data when it deploys, so there is no look at
production first. Adds to Planning from before this ship can't show (nothing recorded
them), and an import's watchlist never makes one. Installed builds (61 to 71) get exactly
the pages they got: they don't ask for Planning rows, so they never print one as "shared
activity".

Its message (Workflow A, step 5) names these, which keep their type and change meaning, and
which installed builds decode and never show as numbers:

- A book, manga or comic save sent with `progressed_on` is on that day: its row's day
  (`saved_on`), so Stats and the Activity list read it there. Before, the date only moved
  the journey's latest progress and the row stayed on the day it was made. Installed builds
  never send it for those types, so nothing they do changes. A day before the previous
  save is refused, as is one before the start (BK-306, decision 66), and the app's calendar
  starts at the later of the two (`progressed_on`, new and optional on a journey).
- `viewer` on a progress day's own page (`GET /api/v1/progress/<id>/`) gains `can_edit`,
  `can_delete` and `edit`, for the owner, and `can_delete` for an admin. The feed and the
  lists never carry them, and anyone else's page has the two keys it had.
- `/api/v1/progress/<id>/` answers `PATCH` and `DELETE`, which were 405. `PATCH` is the
  owner's only (403 for anyone who can see the row, an admin too); `DELETE` is the owner's
  or an admin's, and an admin's writes a `SocialAuditLog` row (`progress_delete`). 400 for a
  row with nothing to fix (TV, anime, a read that ended), 409 when the row isn't the newest
  any more. Deleting calls `bump_user`, so Stats move at once.
- New optional, only for a request that asks: `include=planning` on `/api/v1/feed/` and a
  profile's plain `/api/v1/users/<username>/activity/` adds rows with `type`
  `planning_added` and `object.type` `planning`, with `id` a negative address at or above
  2^44 that opens no progress page, the titles' names (`object.titles`) and up to three
  posters (`object.posters`), and `GET /api/v1/planning/<id>/` lists the titles. A
  list opened from a number ignores it, and the profile row's `counts.activity` and the
  rail don't ask.

**The phone test comes first.** Serve the branch with `scripts/ios/test-backend.sh use 12`,
then run `scripts/ios/seed-activity-checkpoint.sh seed-phase-5` (not `seed`) and work
through the plan's Phase 5 sheet. Its Mac steps are `add-two-movies` (the website's Save),
`letterboxd-watchlist` (print how many it added; it must be above 0),
`friend-adds-three-movies` and `like-p-row` (a like on book P's Dec 20 row, before the
trash). After a Mac step, pull to refresh on Home, Stats and the list. Dates on the sheet
are last year's, so it works on any day, but the seed warns when today isn't in 2026.

Rolling back is a redeploy: old code ignores a planning row (it is never a latest progress
change and adds nothing), and a dated save's row keeps its day.

Remove this section once Phase 5 has shipped.

### The metadata fill, with the ship that adds `src/app/migrations/0091_itemcredit.py`

The first fill ran with the 0088 ship. This ship bumps the movie, TV and music metadata
versions to 3 and books to 4, so the fill revisits every tracked or logged movie, show,
album and book once more to store its people (`ItemCredit`: directors, cast, creators,
artists, authors) for the People sections. The sweep is already on in production, so it
restarts the chain within the hour by itself; the steps below start it right away and
check it. Expect about an hour for films and shows; Open Library books go at about 250 a
day.

Stats need each tracked or logged title's year, runtime, genres, themes, original
language and people. Titles stored before this ship are filled once by a paced fill
(`src/app/metadata_fill.py`, command `fill_item_metadata`): progress lives in the
database, and its own pacing keeps MusicBrainz at one call every 3 s. It fills blanks
and also refreshes stored years, runtimes and genres from the provider (book years move
to the first publication); Apple Music albums keep their stored dates and genres. Its
hourly sweep (beat "Sweep item metadata") does nothing until a full fill is started by
hand below, so a deploy never starts it early. The user's "ship this" covers both runs
below.

1. **Canary, before the ship (optional).** From the feature worktree:
   `scripts/ios/test-backend.sh use 4`, then `scripts/ios/test-backend.sh fill-metadata`
   (a dry run in the checkout being served, with no provider calls). Check its counts,
   then `scripts/ios/test-backend.sh release`.
2. **Test backend, with the ship.** After `scripts/ios/test-backend.sh update` (step 6),
   run `scripts/ios/test-backend.sh fill-metadata`. If it fails, stop and report.
   Otherwise run `scripts/ios/test-backend.sh fill-metadata --apply`: in-process and
   paced, minutes for the test accounts. Once it finishes, its beat runs the hourly
   sweep.
   Finish this before production's fill starts: both call MusicBrainz from this Mac's IP.
3. **Production,** after the deploy is verified (step 8):
   - `docker exec spine python manage.py fill_item_metadata`: the dry run. Read the
     pending, stamp-only and failed-before counts, the estimated calls and minutes by
     type and provider, and the rough number of new series members (each is queued for
     external ratings).
   - `docker exec spine python manage.py fill_item_metadata --apply --limit 20`, then
     spot-check years, runtimes, genres and people (`ItemCredit` rows) on a few of those
     titles: expect changed values (refreshed from the provider), not only new ones. It can fill more
     than 20: a TV show fills all its pending seasons and episodes at once.
   - `docker exec spine python manage.py fill_item_metadata --apply --queue` starts the
     Celery chain on the featured worker and turns on the hourly sweep, which from then
     on fills recent titles and restarts the chain whenever titles are pending. Watch it
     with `docker logs -f spine 2>&1 | grep "metadata fill"`: one line per ~30 s chunk.
   - **Expect about 2-4 h**; the dry run's figures take precedence. Comics go at 60 an
     hour and albums run last, about 15 s each.
   - **A deploy that interrupts it is fine:** progress is in the database, and the
     hourly sweep starts the chain again. If the dry run says the sweep is off (Redis
     lost its key), run `--apply --queue` again.
4. **Finish.** Re-run the dry run until only titles waiting for a retry remain (a
   provider that was down; they are retried after 7 days), plus any for a provider out
   of its daily budget (the sweep resumes them once it resets). Report the
   counts by type. The pre-deploy backup is the way back: the fill replaces stored values, not only blanks.

Remove this section once the production fill has finished.

## TestFlight builds

- **Automatic:** every push to `myapp-main` that changes the iOS app starts an Xcode Cloud
  build. Its "Auto-cancel builds" setting is on, so when ships push in a row, a newer push
  cancels the build still running and one build carries them all. Every build spends Xcode
  Cloud hours; ships don't need to wait for each other to save them.
- **On request:** when Armaan says "make a TestFlight build", run
  `scripts/ios/testflight-build.sh`. It builds `myapp-main` as it is now, whether or not the
  last push changed the app. His request is the yes for it and for step 9 of Workflow A
  (add the build to "Me", tag the commit), which comes next. Never start one otherwise.

## Workflow B: roll back after a bad ship

- **Backend.** Redeploy the last good commit of `myapp-main`:
  `gh workflow run deploy-backend-desk-mac.yml -f branch=myapp-main -f ref=<good sha>`,
  then verify (Workflow A, step 8). Production now runs older code than `myapp-main`,
  so fix forward on a branch, or `git revert -m 1 <merge sha>` and push, before the
  next push redeploys the bad code.
- **Music albums moved to Apple Music.** Once `scripts/music-catalog-production.sh apply`
  has moved any album, a commit without that script (from before the Apple Music
  catalog's step 2) sends the moved albums' Apple Music IDs to MusicBrainz and breaks
  their pages. Don't deploy such a commit until the fallback has run
  (`docs/music/apple-music-catalog.md`, "Falling back to MusicBrainz": the catalog
  setting back to MusicBrainz, then `revert --all`); otherwise fix forward.
- **Database.** A rollback doesn't undo migrations. Additive ones are harmless to leave
  in place. Undoing one means `python manage.py migrate <app> <previous>` inside the
  container, if it's reversible, or restoring its
  `~/projects/spine-backups/pre-deploy-<run>.dump`, which loses every write since. Both
  need the user's yes.
- **Each save's day (`social` `0005`).** Old code writes no `saved_on`, and reads rows
  through its own zone until the next ship. It has neither of these commands nor the steps'
  wrapper, so after the ship that brings this code back, run
  `docker exec spine python manage.py fill_progress_days`, then
  `compare_activity_numbers --since <the roll-forward's moment>`, whose `count 14` line
  must read `empty_saved_on={}` (it isn't empty before the fill). Never run
  `migrate social 0004` after real saves exist: it deletes each save's phone date. A
  Restart made during a rollback has no `began_over` and reads as a typo fix, so run
  `scripts/activity-steps-production.sh past-restarts` for rows since the rollback and flag
  any with the user's yes. Status moves made during a rollback never show. A
  `git revert -m 1` re-ship must keep the migration's file name,
  `0005_progresschange_saved_on`, or `migrate` re-adds an existing column and
  `entrypoint.sh` (`set -e`) stops the container, which CI's empty database can't catch.
- **App.** Installed builds can't be recalled. Expire the bad TestFlight build and ship
  a fixed one.

## Expand and contract

For a breaking API or schema change while builds are installed:

1. **Expand.** The backend serves both shapes, for example by keeping the old field
   with a compatible value. Ship it.
2. **Move.** Ship an app build that uses only the new shape.
3. **Contract.** Once no installed build uses the old shape (for TestFlight, after the
   old builds are expired), remove it, with the user's yes for any destructive
   migration.

## Rules

- Never push, deploy, or write to App Store Connect without the user's explicit yes in
  this conversation, for this ship. "Ship this" is that yes for Workflow A; destructive
  migrations and rollbacks each need their own.
- Claude Code's permissions for Workflow A are in `.claude/settings.json`. If a step is
  still blocked, report it; don't route around the block.
- Stop after two failures of the same step and report.
- Don't change signing, the team, or the bundle ID. Never print or commit the App Store
  Connect key, `.env` or `.env.production`.
- Never run `docker compose` against the production stack by hand, and never `down -v`.
  Deploys go through the workflow.
- Codex: git push, GitHub, Docker and App Store Connect need the network. Request
  `sandbox_permissions: "require_escalated"` for them.
