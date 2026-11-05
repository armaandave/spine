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

1. **Status.** `git fetch origin`, then `scripts/ios/feature-status.sh`. Merge
   `origin/myapp-main` into the branch if it has moved, and resolve conflicts.
2. **Verify.**
   - Run `scripts/check.sh` (the same checks as CI's Fast checks) and fix anything it
     reports.
   - If iOS changed, build it and run the iOS tests (`ios/AGENTS.md`).
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
   - Claude Code: if the session is isolated in the feature worktree, leave it with
     `ExitWorktree` (keep) first.
   - In the main checkout (`~/projects/spine`), which must be clean and on `myapp-main`:
     `git merge --no-ff <branch>`.
   - Run `scripts/check.sh` on the merged `myapp-main`, then `git push origin myapp-main`.
   - Commit as `armaandave@outlook.com`, with the trailers your instructions require.
   - Run `scripts/ios/test-backend.sh update`, so the always-on test backend (the one
     Spine Dev talks to) serves the new `myapp-main`.
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

Some ships carry a data step that has to run once, right after their deploy is verified
(Workflow A, step 8). Each one says which ship carries it.

### Activity's preparatory ship: each save's day

The ship that adds `src/social/migrations/0005_progresschange_saved_on.py` stores each
save's day and records status moves, with the calculation in switched off, so nothing you'd
see changes. Migration `0005` fills the new column on its own and writes only that column,
so it's additive. Its message (Workflow A, step 5) says that some refusals become successes
here, which is safe on installed code: a save, a Stop or a Restart from a phone a few seconds
ahead or after flying west; a game's Start dated a day ahead; a log or a log's date edit on
the phone's own date after flying west, on a book, game, manga or comic; and a date that was
ahead only of the server's calendar, on an account east of it.

**Each step below waits for the user's request; the ship request doesn't cover it.** The
ship message (step 5) names each as waiting. It can't print a count yet, because the dry runs
need the deployed code: the wrapper stops until `0005` is applied. Armaan's yes on these two
is on record (Activity plan, decision 49), and that yes is the request, so each runs after
the deploy unless he says stop to the counts message below, and only if its count finds any.

1. **Dry runs and the counts message,** after the deploy is verified (step 8):
   `scripts/activity-steps-production.sh undated-reads` and
   `scripts/activity-steps-production.sh past-restarts`. Each prints counts only. Send him a
   short message with every count they print and what `--apply` will do. The first also prints
   Reads with a date and no log, and marked books and games holding an `end_date`: no step
   writes those, and nothing later repairs the dated Reads, so say so if any exist. If the
   undated Reads and past Restarts counts are both zero, the steps are done. Otherwise wait
   10 minutes before `--apply`, and don't run it if he says stop.
2. **Undated imported Reads,** only if the count isn't zero:
   `scripts/activity-steps-production.sh undated-reads --apply`. Goodreads and StoryGraph
   Reads with no date, no log and no mark become marks, on every account. All Time rises and
   no year moves. Turning the eye off on one later keeps the book at To Read with its notes,
   and clears its rating, as on any mark.
3. **Past Restarts,** only if the count isn't zero:
   `scripts/activity-steps-production.sh past-restarts --apply`. That start row gets
   `began_over`. It's an upper bound: at most 3 across accounts.

The wrapper takes a verified `pg_dump` first and keeps the old ids and values in a 600 file
in `~/projects/spine-backups/`, never on the container's disk or under `/yamtrack/media`.

**The checks, which change no data.** `docker exec spine python manage.py
compare_activity_numbers` prints today's number beside the calculation's, per account. Its
`count 14` line must read `empty_saved_on={}`: a writer that forgets its day fails silently.
Run it after the deploy, and again with `--since <the deploy's moment>` after the first real
save of each type. Then bring Armaan the comparison, from a run made after steps 2 and 3, or
once both their counts are zero: before them it reads unflagged Restarts as typo fixes and
leaves out the undated Reads. A step that meets a bad row prints `failed` on its own line, the
rest still report, and the command exits non-zero at the end; bring him those lines too.

Remove this section once Phase 1 has shipped.

### Activity's Phase 1: the pages

Books read the calculation (`CALCULATED_TYPES`). No migration. Its message (Workflow A, step
5) says a refusal becomes a success here, which is safe on installed code: a book log dated
before its journey's start. These keep their type and change meaning, and installed builds
decode them and never show them: a book's `known` and `total` count titles, `years.first` and
`header.titles` count reading with no log, and a book's `progress` after a pick-up is its
place (BK-305).

**The phone test comes first.** Serve the branch with `scripts/ios/test-backend.sh use 12`, then
run `scripts/ios/seed-activity-checkpoint.sh seed` and work through the plan's Phase 1 sheet.
The default of 4 hours can run out mid-sheet, and the phone would read another database with no
warning.

**Before you say this is ready to ship:** the speed gate must have passed on this branch (plan
5.2, against section 6's limits): a Stats miss on Global and Books, All Time and one year,
median of 5, on a throwaway Postgres, under 1.5 seconds and under 2 times today's. This phase
is the first ship that makes `CALCULATED_TYPES` non-empty, so it's the ship that gate guards.

Then run `docker exec spine python manage.py compare_activity_numbers` and read each account's
`book` lines. The plan pins Books 2026 at about 5,182 and All Time at about 59,483 on account
1, and Books 2025 at 3,762. If the real number differs, it doesn't ship until that's explained.
Read count 2 too, the `count 2 class` lines. If class b or c isn't zero, the likes repair goes
with this ship, so it doesn't ship until that's written, with its test first (plan 5.8).

**Each step below waits for the user's request; the ship request doesn't cover it.** The ship
message (step 5) names each as waiting. It can't print count 15 yet, because that dry run needs
the deployed code. Armaan's yes is on record (Activity plan, decisions 50 and 60), and that yes
is the request, so each runs after the deploy unless he says stop to the counts message below,
and only if its count finds any.

1. **Books already picked up,** after the deploy is verified (step 8):
   `scripts/activity-steps-production.sh picked-up-books` prints count 15. Send him a short
   message with the count and what `--apply` will do. If it's zero, the step is done. Otherwise
   wait 10 minutes, then `--apply`, and don't run it if he says stop. Those journeys get the
   place they stopped at. No number and no row changes.
2. **Likes repair.** Read count 2 again just after this deploy. If classes b and c are zero both
   times, there's nothing to do and no repair code was written. If they only turn up on this
   second read, the repair goes with the next ship, as its own change, with its test first.

Remove this section once Phase 2 has shipped.

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
