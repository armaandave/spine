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
     runs after the deploy
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
     now. The user's ship request covers it.
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

### The TV repair, with the ship that adds `src/app/migrations/0085_tv_tracking_state.py`

TV shows stored before the TV tracking contract stay unworked until this repair runs
(`docs/tv-tracking-contract.md`, TV-801). Until then the new build's Continue Watching on
Home and Profile leaves them out, and Library rows count their episodes without the
number aired. It runs on the test backend first, then on production:

1. After `scripts/ios/test-backend.sh update` (step 6), run
   `scripts/ios/test-backend.sh repair-tv`, a dry run that lists every status it would
   change. If its summary shows errors, stop and report. Otherwise run
   `scripts/ios/test-backend.sh repair-tv --apply`.
2. Once production is verified, run the dry run there:
   `docker exec spine python manage.py reconcile_tv`. Read its summary; if any show
   can't be repaired, stop and report. Otherwise run
   `docker exec spine python manage.py reconcile_tv --apply`. It is safe to run again: a
   show already worked out reports nothing.
3. Report the summary's counts (shows checked and changed, newly in the Library, status
   changes) with the ship.

Remove this section once that ship is done.

## Workflow B: roll back after a bad ship

- **Backend.** Redeploy the last good commit of `myapp-main`:
  `gh workflow run deploy-backend-desk-mac.yml -f branch=myapp-main -f ref=<good sha>`,
  then verify (Workflow A, step 8). Production now runs older code than `myapp-main`,
  so fix forward on a branch, or `git revert -m 1 <merge sha>` and push, before the
  next push redeploys the bad code.
- **Database.** A rollback doesn't undo migrations. Additive ones are harmless to leave
  in place. Undoing one means `python manage.py migrate <app> <previous>` inside the
  container, if it's reversible, or restoring its
  `~/projects/spine-backups/pre-deploy-<run>.dump`, which loses every write since. Both
  need the user's yes.
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
