#!/usr/bin/env bash
# Delete the scratch simulators left behind for one checkout: the ones agent-cleanup created
# for runs started inside it. scripts/ios/SHIP.md runs this when a feature ships. The helper
# deletes each run's simulator when the run ends; this catches finished runs' simulators it
# didn't delete (runs from before it did that, or a failed delete), which keep a few GB each
# on this Mac's nearly full disk. A crashed run's simulator is left for review.
# It reads the helper's run receipts and deletes a simulator only when its run has finished,
# it is shut down, and it still has the name the helper gave it (agent-cleanup-<run_id>).
# Simulators from runs started in other checkouts, including the worktrees under the main
# checkout's .claude/, and simulators the helper didn't create are never touched.
#
# Usage: scripts/ios/delete-scratch-sims.sh [checkout]
#   checkout   default: the current checkout. A worktree's path still works after it's removed.
set -euo pipefail

checkout=${1:-$(git rev-parse --show-toplevel)}
case "$checkout" in /*) ;; *) checkout="$PWD/$checkout" ;; esac
state=${AGENT_CLEANUP_STATE:-$HOME/.local/state/agent-cleanup}

python3 - "$checkout" "$state" <<'PY'
import json
import os
import pathlib
import subprocess
import sys

checkout, state = os.path.normpath(sys.argv[1]), pathlib.Path(sys.argv[2])


def started_in_checkout(cwd):
    # Worktrees and the test backend's checkout live under the main checkout's .claude/.
    if cwd == checkout:
        return True
    return cwd.startswith(checkout + "/") and not cwd.startswith(checkout + "/.claude/")


listing = subprocess.run(
    ["xcrun", "simctl", "list", "devices", "--json"], capture_output=True, text=True, check=True
)
devices = {d["udid"]: d for runtime in json.loads(listing.stdout)["devices"].values() for d in runtime}

deleted = 0
for path in sorted(state.glob("*/receipt.json")):
    try:
        receipt = json.loads(path.read_text())
    except (OSError, ValueError):
        continue  # a run still writing its receipt
    sim = receipt.get("simulator") or {}
    device = devices.get(sim.get("udid"))
    if not device or not started_in_checkout(receipt.get("cwd") or ""):
        continue
    name = device["name"]
    if receipt.get("state") != "finished":
        print(f"SKIPPED: {name}: its run hasn't finished (still going, or crashed); "
              f"see agent-cleanup status {receipt.get('run_id')}")
    elif device["state"] != "Shutdown":
        print(f"SKIPPED: {name} is {device['state']}, not shut down")
    elif name != f"agent-cleanup-{receipt.get('run_id')}":
        print(f"SKIPPED: {sim['udid']} was renamed to {name}")
    else:
        subprocess.run(["xcrun", "simctl", "delete", sim["udid"]], check=True)
        print(f"DELETED: {name}")
        deleted += 1
print(f"Deleted {deleted} scratch simulator(s) made in {checkout}.")
PY
