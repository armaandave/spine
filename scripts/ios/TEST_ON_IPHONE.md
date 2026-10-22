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
- Spine Dev talks to the **test backend**, which is always running on the Mac, so Spine Dev
  works whenever the user opens it, not only during a test. Test accounts, ratings and likes
  stay out of production's data.
  - It is `scripts/ios/test-backend.sh`, kept running by launchd at
    `http://<Mac>.local:8010` at low priority. Underneath it is `scripts/ios/local-backend.sh`:
    DEBUG, SQLite only, Redis DB 5 under its own prefix, never port 8000. Nothing reaches
    production.
  - Normally it serves `myapp-main` from its own checkout (`.claude/test-backend` in the main
    checkout), whose SQLite database is the lasting test database: the dev user and whatever
    the user and agents add to it.
  - For a branch with backend changes, `scripts/ios/test-backend.sh use` points the same
    address at this checkout's backend for 4 hours, on a fresh copy of the test database,
    migrations included. Then it goes back to `myapp-main` by itself (or sooner with
    `release`), and what was written meanwhile is dropped. Because the address never changes,
    Spine Dev keeps working afterwards without a rebuild.
  - The build bakes the test backend's address into the app's `SpineAPIBaseURL` Info.plist key.
  - It opens signed in as the passwordless dev user (`--dev-sign-in`), the test account.
    Only a local backend answers that (DEBUG, SQLite and `SPINE_DEV_SIGN_IN=1`);
    production answers 404.
  - Catalog data (TMDB and the other providers) is real.
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

| Verdict | Meaning | Step 2 |
| --- | --- | --- |
| `no-backend` | No backend changes | skip |
| `backend-live` | The backend part is already on `origin/myapp-main` | skip |
| `local-backend` | Backend changes production doesn't have | serve this checkout |

### 2. Serve this checkout from the test backend (`local-backend` only)

```bash
scripts/ios/test-backend.sh use
```

It copies the test database into this checkout, points the test backend's address at this
checkout's backend for 4 hours (`use <hours>` for up to 12), applies the branch's migrations
to the copy, and waits until it answers. `scripts/ios/test-backend.sh status` says what the
test backend serves. For other verdicts, it serves `myapp-main`, which is what the branch
expects.

### 3. Build to the iPhone

Tell the user Spine Dev is going onto their phone next to their real Spine app, signed
in as the dev user. The phone must be on the Mac's Wi-Fi, and iOS may ask once to find
devices on the local network, which they should allow. Then run it in the background,
in the `ios` queue (heavy iOS work on this Mac takes turns; it may wait for its turn first):

```bash
~/.local/bin/agent-cleanup run --owner <task> --label spine-phone-build --queue ios -- scripts/ios/build-to-iphone.sh
```

- It builds the current checkout, including uncommitted changes, into its own build
  cache under `~/Library/Developer/Xcode/DerivedData/`, then installs and opens the
  app. It prints the API address and what the test backend serves.
- It refuses to build when the test backend isn't answering, or when the branch has
  backend changes and the test backend isn't serving this checkout.
- Its output is in the run's log (the `log` path in the first line). Report success only after
  it prints `INSTALLED:`.

### 4. Report

Keep it short:

- What's on the phone: Spine Dev, the branch and commit, and what the test backend serves
  (`myapp-main`, or this checkout until when).
- If this checkout is served: it goes back to `myapp-main` on its own at that time, or now
  with `scripts/ios/test-backend.sh release` when the user is done.
- What's next: when the user is happy, "ship this" (`spine-ship`) merges it and takes
  it live.

## Rules

- Never merge to `myapp-main`, push, deploy, or touch production in this workflow.
  Never point a build or server at port 8000: that's production. Never test with the
  user's real account or in the TestFlight Spine app.
- Never copy the main checkout's dev database into a worktree; it holds personal data.
  `test-backend.sh use` copies only the test database, and refuses the main checkout.
- Don't stop, uninstall, or reinstall the test backend in this workflow. If it isn't
  answering, report it (`status` says where its log is).
- Codex: the build needs Apple device services, Xcode caches, and the network, and
  `test-backend.sh` needs launchctl. Request
  `sandbox_permissions: "require_escalated"` for them from the first call instead of
  probing inside the sandbox.
- Stop after two failures of the same step and report what's needed. Common causes:
  - the phone is locked or off the Mac's Wi-Fi
  - Developer Mode is off
  - a signing prompt is waiting in Xcode
  - Local Network access for Spine Dev is off: Settings → Privacy & Security → Local Network
- Don't change signing, the team, or the bundle IDs. Don't uninstall either app,
  archive, or upload to TestFlight in this workflow.
