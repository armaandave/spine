# Test a Spine feature on the iPhone

Canonical workflow for the `spine-test-on-iphone` skill, used by both Claude Code
(`.claude/skills/`) and Codex (`.agents/skills/`). Those skill files are pointers to
this guide; change the workflow here only.

Use it when the user asks to test, try, run, or build a feature, branch, or worktree on
their iPhone or phone. Shipping to production afterwards is the `spine-ship` skill
(`scripts/ios/SHIP.md`).

## How the pieces fit

- A normal phone build (Debug) talks to production, `https://api.spine-api.com`, with
  the user's real account. That is the right build for iOS-only changes.
- A **local** phone build (`--local`) talks to this checkout's own backend on the Mac.
  That's how backend changes get tested without deploying.
  - The backend is `scripts/ios/local-backend.sh`: this checkout's Django server on
    `0.0.0.0:8001` or the next free port (never 8000, which is production), plus a
    Celery worker.
  - It uses this checkout's own SQLite database and migrates it on start, so a branch's
    migrations are safe to try; a new worktree starts with an empty one. Redis traffic
    goes to DB 5 under the checkout's own prefix. It refuses to start if the settings
    would use Postgres. Nothing reaches production.
  - The build bakes the backend's address (`http://<Mac>.local:<port>`) into the app's
    `SpineAPIBaseURL` Info.plist key, so it stays on that backend however it's opened.
  - It opens signed in as the passwordless dev user (`--dev-sign-in`). Only a local
    backend answers that (DEBUG, SQLite and `SPINE_DEV_SIGN_IN=1`); production answers
    404. Going back to a normal build means signing in again.
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

It lists committed and uncommitted changes. The last line is `VERDICT: <value>`:

| Verdict | Meaning | Build with |
| --- | --- | --- |
| `no-backend` | No backend changes | `scripts/ios/build-to-iphone.sh` |
| `backend-live` | The backend part is already on `origin/myapp-main`, so production runs it | `scripts/ios/build-to-iphone.sh` |
| `local-backend` | Backend changes production doesn't have | step 2, then `scripts/ios/build-to-iphone.sh --local` |

Migrations need nothing special here: the local backend applies them to its own
database.

### 2. Start the local backend (`local-backend` only)

In the background, under the cleanup wrapper, so it gets stopped later:

```bash
~/.local/bin/agent-cleanup run --owner <task> --label spine-local-backend -- scripts/ios/local-backend.sh
```

Wait for `LOCAL_API:` in its log; `STOPPED:` means it refused or failed, and says why.
`scripts/ios/local-backend.sh --status` prints the address of one that's running.

### 3. Build to the iPhone

Tell the user the install will replace and reopen Spine on their phone (their data
stays; a local build signs in as the dev user). For `--local`, also tell them the phone
must be on the Mac's Wi-Fi, and that iOS may ask once to find devices on the local
network, which they should allow. Then run the build command from step 1.

- It builds the current checkout, including uncommitted changes, into its own build
  cache under `~/Library/Developer/Xcode/DerivedData/`, then installs and opens the
  app. It prints the API address the build uses.
- With `--local` it refuses to install a build that doesn't point at the running
  backend.
- Report success only after it prints `INSTALLED:`.

### 4. Report

Keep it short:

- What's on the phone: the branch and commit, and whether it talks to production or
  the local backend (with its address).
- For a local build: the backend keeps running for more testing. Stop it with
  `~/.local/bin/agent-cleanup stop <run_id>` when the user is done.
- What's next: when the user is happy, "ship this" (`spine-ship`) merges it and takes
  it live.

## Rules

- Never merge to `myapp-main`, push, deploy, or touch production in this workflow.
  Never point a build or server at port 8000: that's production.
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
  - Local Network access for Spine is off: Settings → Privacy & Security → Local Network
- Don't change signing, the team, or the bundle ID. Don't uninstall the app, archive, or
  upload to TestFlight in this workflow.
