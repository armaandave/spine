# iOS work on Armaan's laptop

This Mac (HomeMac) also runs production, the test backend and other agents, so iOS builds,
simulators and test runs here are slow and wait in the `ios` queue. Armaan's laptop (MacBook
Pro, M1 Pro, 32 GB, `armaans-macbook-pro-2` on Tailscale) can do that work instead.
`scripts/ios/laptop.sh` runs it there over SSH; the code still lives and is edited here.

## Ask first, once per checkout

Before the **first** iOS build, simulator or test run, check the saved answer:

```bash
scripts/ios/machine.sh          # laptop, homemac or unset
```

If it prints `unset`, ask Armaan right away, wait, and save his answer. **Always ask in the
app's question picker, never as plain text:** `AskUserQuestion` in Claude Code
(`request_user_input` in Codex), one single-choice question with exactly two options, no
"(Recommended)":

- Question: "Build <checkout name> on the laptop or on HomeMac?", plus what it's for in a
  few words, e.g. "(phone build for the ship)". The picker can hide the chat text above it,
  so the question must make sense on its own.
- Header: "Machine"
- Options: "Laptop" (description: "Faster; the laptop must stay awake") and "HomeMac"
  (description: "Waits in HomeMac's iOS queue").
- If no picker tool exists in the session, ask the same question as plain text.

```bash
scripts/ios/machine.sh set laptop     # or: set homemac
```

- Every later iOS job reads `scripts/ios/machine.sh`, not memory, so the answer survives the
  chat being summarized and is never asked twice. It lives in the checkout's git folder, so
  each worktree (normally one chat) has its own, and removing the worktree removes it.
- He can switch at any time ("use the laptop", "use HomeMac"): `machine.sh set` the new one.
- Never pick for him.
- HomeMac means the usual way: `agent-cleanup ... --queue ios` (`TEST_ON_IPHONE.md`,
  `ios/AGENTS.md`).
- A ship asks before anything else, as its first step (`SHIP.md`).

## How the laptop works

- Every `run` sends this checkout, including uncommitted and new files, to the laptop's
  agent-only copy `~/spine-agent/checkouts/<checkout name>` (no commit, no GitHub, no CI),
  then runs the command there.
- Laptop jobs skip this Mac's `ios` queue. On the laptop they wait in their own line, one at a
  time. Armaan's own agents on the laptop don't use that line, so a build of his can run at the
  same time.
- Wrap long laptop jobs in `agent-cleanup run` **without** `--queue ios`, for the deadline;
  stopping the wrapper also stops the job on the laptop.
- Simulators on the laptop are invisible (no Simulator window), one per checkout, named
  `Spine Agent <checkout name>`. One left untouched for 30 minutes is deleted by the next laptop
  job.
- Spine Dev on the laptop's simulator or the phone talks to the test backend on this Mac over
  Tailscale, like any Spine Dev build. Nothing touches production.
- If the laptop is asleep or offline, `laptop.sh` stops with `STOPPED: the laptop isn't
  reachable`. Tell Armaan and wait. Don't fall back to HomeMac unless he says so.

## Commands

Run from the checkout you mean, like the other scripts.

Phone (Spine Dev): steps 1-2 of `TEST_ON_IPHONE.md` as usual, then:

```bash
~/.local/bin/agent-cleanup run --owner <task> --label spine-phone-build-laptop -- scripts/ios/build-to-iphone.sh --laptop
```

The phone must be plugged into the laptop, or on the same Wi-Fi as the laptop. Signing needs
a one-time `scripts/ios/laptop-signing-setup.sh`, run by Armaan in Terminal on the laptop (over
SSH the login keychain stays locked); the build stops and says so if it hasn't been run. The test-backend
checks still run here first. Report success only after it prints `INSTALLED:`.

Simulator:

```bash
scripts/ios/laptop.sh sim start      # boots it, prints SIM_ID (first boot takes a minute)
~/.local/bin/agent-cleanup run --owner <task> --label spine-sim-laptop -- scripts/ios/laptop.sh sim app
                                     # builds Spine Dev, installs it, opens it as the dev user
scripts/ios/laptop.sh exec xcodebuildmcp ui-automation snapshot-ui --simulator-id <SIM_ID>
                                     # rows like "e66|tap|tab|Library|0|"; refs come from the latest snapshot
scripts/ios/laptop.sh exec xcodebuildmcp ui-automation tap --simulator-id <SIM_ID> --element-ref e66
scripts/ios/laptop.sh exec xcrun simctl io <SIM_ID> screenshot /tmp/spine-agent-shot.png
scripts/ios/laptop.sh pull /tmp/spine-agent-shot.png <scratchpad>/shot.png
scripts/ios/laptop.sh sim stop       # when done: shuts it down and deletes it
```

The laptop has a newer XcodeBuildMCP than this Mac (2.7 vs 2.3), so its flags differ: check
`scripts/ios/laptop.sh exec xcodebuildmcp ui-automation <command> --help` there, not here
(type-text, swipe, gesture, button and the rest). The
XcodeBuildMCP tools in this session only reach HomeMac's simulators; use these commands for the
laptop's.

Tests:

```bash
scripts/ios/laptop.sh sim start
~/.local/bin/agent-cleanup run --owner <task> --label spine-tests-laptop -- \
  scripts/ios/laptop.sh run xcodebuild test -project ios/Spine/Spine.xcodeproj -scheme Spine \
  -destination id=<SIM_ID> -derivedDataPath ../../derived.noindex/<checkout name>-sim \
  -only-testing:SpineTests -parallel-testing-enabled NO
```

Anything else: `scripts/ios/laptop.sh run <cmd>` (sync + line) or `exec <cmd>` (no sync, no
line, for quick commands).

## Rules

- Only ever touch `~/spine-agent` and the `Spine Agent *` simulators on the laptop. Never
  Armaan's own checkouts (`~/projects/...`), his other simulators, or his apps.
- Don't open Simulator.app or other windows on the laptop.
- Stop your simulator (`sim stop`) when you're done.
- Never merge, push, deploy or point anything at production from the laptop. The rules in
  `TEST_ON_IPHONE.md` and `ios/AGENTS.md` all still apply.
- Phone builds still need Armaan to ask for one, as on HomeMac.
