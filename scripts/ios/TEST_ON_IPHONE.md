# Test a Spine feature on the iPhone

Canonical workflow for the `spine-test-on-iphone` skill, used by both Claude Code
(`.claude/skills/`) and Codex (`.agents/skills/`). Those skill files are pointers to
this guide; change the workflow here only.

Use it when the user asks to test, try, run, or build a feature, branch, or worktree on
their iPhone or phone. Shipping to production afterwards is the `spine-ship` skill
(`scripts/ios/SHIP.md`).

## How the pieces fit

- The phone has two Spine apps side by side:
  - **Spine** (`com.armaan.Spine`, the plain icon) comes from TestFlight. It's the user's
    real app: production, `https://api.spine-api.com`, their real account. This workflow
    never installs, replaces, or signs into it.
  - **Spine Dev** (`com.armaan.Spine.dev`, the icon with the orange DEV band) is what this
    workflow installs. It's the Debug build, and Debug builds never talk to production
    (`AppConfig`). Each install replaces the previous Spine Dev build, never Spine.
- Every phone test, iOS-only or not, uses Spine Dev against this checkout's own backend
  on the Mac. Test accounts, ratings and likes made while testing stay out of
  production's data.
  - The backend is `scripts/ios/local-backend.sh`: this checkout's Django server on
    `0.0.0.0:8001` or the next free port (never 8000, which is production), plus a
    Celery worker.
  - It uses this checkout's own SQLite database and migrates it on start, so a branch's
    migrations are safe to try; a new worktree starts with an empty one. Redis traffic
    goes to DB 5 under the checkout's own prefix. It refuses to start if the settings
    would use Postgres. Nothing reaches production.
  - The build bakes the backend's address (`http://<Mac>.local:<port>`) into the app's
    `SpineAPIBaseURL` Info.plist key, so it stays on that backend however it's opened.
  - It opens signed in as the passwordless dev user (`--dev-sign-in`), the test account.
    Only a local backend answers that (DEBUG, SQLite and `SPINE_DEV_SIGN_IN=1`);
    production answers 404.
  - The backend's catalog data (TMDB and the other providers) is real; the dev user's
    tracking, ratings and lists start empty in a new worktree, and the dev user adds what
    a test needs.
  - The iPhone must be on the same Wi-Fi as the Mac (or the Mac on the iPhone's
    hotspot), and iOS asks once for Local Network access, which the user must allow.
- Production only ever changes through `spine-ship`. This workflow never merges,
  pushes, or deploys.

## Workflow

Run every command from the checkout the user means: the current worktree, or the one
they named (`cd` there first). The scripts act on the checkout you run them in. If that
checkout predates these scripts, run them by path from the main checkout
(`~/projects/spine/scripts/ios/...`) while staying in the feature checkout's directory.

### 1. Check what the branch changes

```bash
scripts/ios/feature-status.sh
```

It lists committed and uncommitted changes, for the report. The last line,
`VERDICT: <value>`, says whether the branch has backend changes production doesn't have
(`local-backend`), has them already live (`backend-live`), or has none (`no-backend`).
Every verdict builds the same way: steps 2 and 3.

Migrations need nothing special here: the local backend applies them to its own
database.

### 2. Start the local backend

In the background, under the cleanup wrapper, so it gets stopped later:

```bash
~/.local/bin/agent-cleanup run --owner <task> --label spine-local-backend -- scripts/ios/local-backend.sh
```

Wait for `LOCAL_API:` in its log; `STOPPED:` means it refused or failed, and says why.
`scripts/ios/local-backend.sh --status` prints the address of one that's running.

### 3. Build to the iPhone

Tell the user Spine Dev is going onto their phone next to their real Spine app, signed
in as the dev user. The phone must be on the Mac's Wi-Fi, and iOS may ask once to find
devices on the local network, which they should allow. Then run:

```bash
scripts/ios/build-to-iphone.sh
```

- It builds the current checkout, including uncommitted changes, into its own build
  cache under `~/Library/Developer/Xcode/DerivedData/`, then installs and opens the
  app. It prints the API address the build uses.
- It refuses to build unless this checkout's local backend is running, and to install
  a build that doesn't point at it. (`--local` is still accepted and changes nothing.)
- Report success only after it prints `INSTALLED:`.

### 4. Report

Keep it short:

- What's on the phone: Spine Dev, the branch and commit, and the local backend's
  address.
- The backend keeps running for more testing. Stop it with
  `~/.local/bin/agent-cleanup stop <run_id>` when the user is done.
- What's next: when the user is happy, "ship this" (`spine-ship`) merges it and takes
  it live.

## Rules

- Never merge to `myapp-main`, push, deploy, or touch production in this workflow.
  Never point a build or server at port 8000: that's production. Never test with the
  user's real account or in the TestFlight Spine app.
- Never copy the main checkout's dev database into a worktree; it holds personal data.
  A local backend starts empty, and the dev user adds what a test needs.
- Codex: the build needs Apple device services, Xcode caches, and the network, and the
  local backend needs to listen on a port. Request
  `sandbox_permissions: "require_escalated"` for them from the first call instead of
  probing inside the sandbox.
- Stop after two failures of the same step and report what's needed. Common causes:
  - the phone is locked or off the Mac's Wi-Fi
  - Developer Mode is off
  - a signing prompt is waiting in Xcode
  - Local Network access for Spine Dev is off: Settings → Privacy & Security → Local Network
- Don't change signing, the team, or the bundle IDs. Don't uninstall either app,
  archive, or upload to TestFlight in this workflow.
