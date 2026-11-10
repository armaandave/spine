#!/usr/bin/env bash
# Run Spine's iOS work (builds, simulators, test runs, phone installs) on Armaan's laptop
# over Tailscale instead of this Mac, when he picks the laptop for the chat
# (scripts/ios/LAPTOP.md). Code stays here: each job sends this checkout, uncommitted and
# new files included, to the laptop's agent-only copy (~/spine-agent, never his own
# ~/projects/spine) and runs there. Laptop jobs never use this Mac's ios queue; they wait in
# the laptop's agent-cleanup ios queue, two builds at a time (one per checkout).
#
# Usage: scripts/ios/laptop.sh <command>
#   check              is the laptop reachable? prints LAPTOP: ok or STOPPED: ...
#   sync               send this checkout to the laptop; prints REMOTE_DIR
#   run <cmd...>       sync, then run cmd in the laptop copy, waiting for the laptop's line
#   exec <cmd...>      run cmd in the laptop copy now: no sync, no line (simctl, ui, screenshots)
#   sim start|app|stop start: boot this checkout's simulator, invisible; prints SIM_ID
#                      app: build Spine Dev, install it there, open it signed in (dev user)
#                      stop: shut it down and delete it
#   pull <remote> <local>   copy a file from the laptop (relative to the laptop copy, or absolute)
#
# SPINE_LAPTOP overrides the laptop's address (default armaandave@100.78.1.2).
set -euo pipefail

laptop=${SPINE_LAPTOP:-armaandave@100.78.1.2}
ssh_opts=(-o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=30 -o LogLevel=QUIET)
remote_repo=spine-agent
sim_idle_minutes=30

die() { echo "STOPPED: $*" >&2; exit 1; }

top=$(git rev-parse --show-toplevel)
name=$(basename "$top")
remote_dir="$remote_repo/checkouts/$name"
sim_name="Spine Agent $name"

touch_sim="mkdir -p ~/$remote_repo/.sims; for u in \$(xcrun simctl list devices | grep -F '$sim_name (' | sed -E 's/.*\\(([0-9A-F-]{36})\\).*/\\1/'); do touch ~/$remote_repo/.sims/\$u; done"

quote() { local out="" a; for a in "$@"; do out+=" $(printf '%q' "$a")"; done; printf '%s' "${out# }"; }

# Remote commands run in a login-like PATH (Homebrew for xcodebuildmcp). -tt gives the
# remote side a terminal, so stopping this script (or its agent-cleanup deadline) hangs up
# the job on the laptop too instead of leaving it running there.
remote() {
  ssh "${ssh_opts[@]}" -tt "$laptop" \
    "stty -onlcr 2>/dev/null; export PATH=/opt/homebrew/bin:/usr/local/bin:\$PATH TERM=dumb PAGER=cat GIT_PAGER=cat; $1"
}

check() {
  ssh "${ssh_opts[@]}" "$laptop" true 2>/dev/null ||
    die "the laptop isn't reachable over Tailscale (asleep, lid closed, or offline). Ask Armaan to wake it, or to say \"use HomeMac\"."
}

# Shut down and delete agent simulators nobody has touched for a while, so a forgotten one
# doesn't sit on Armaan's laptop. Each sim command touches ~/spine-agent/.sims/<udid>.
reap_sims() {
  remote "mkdir -p ~/$remote_repo/.sims; for f in \$(find ~/$remote_repo/.sims -type f -mmin +$sim_idle_minutes); do u=\$(basename \$f); xcrun simctl shutdown \$u >/dev/null 2>&1; xcrun simctl delete \$u >/dev/null 2>&1; rm -f \$f; echo \"Reaped idle simulator \$u\"; done" || true
}

sync() {
  check
  # Snapshot the checkout without touching its index or files: a commit of the working tree
  # (tracked changes plus new files, minus ignored ones) on top of HEAD.
  local idx tree commit
  idx=$(mktemp)
  trap 'rm -f "$idx"' RETURN
  GIT_INDEX_FILE=$idx git -C "$top" read-tree HEAD
  GIT_INDEX_FILE=$idx git -C "$top" add -A
  tree=$(GIT_INDEX_FILE=$idx git -C "$top" write-tree)
  commit=$(git -C "$top" commit-tree "$tree" -p HEAD -m "laptop snapshot of $name")
  GIT_SSH_COMMAND="ssh ${ssh_opts[*]}" git -C "$top" push -q --force \
    "$laptop:$remote_repo" "$commit:refs/agent/$name" ||
    die "couldn't send the checkout to the laptop"
  remote "set -e; cd ~/$remote_repo; if [ -d checkouts/$name ]; then git -C checkouts/$name checkout -q -f --detach $commit; git -C checkouts/$name clean -fdq; else git worktree add -q --detach checkouts/$name $commit; fi" >/dev/null ||
    die "couldn't check out the snapshot on the laptop"
  echo "Synced:     $name ($(git -C "$top" log -1 --format='%h %s'))$( [ -n "$(git -C "$top" status --porcelain)" ] && echo ', with uncommitted changes')"
  echo "REMOTE_DIR: ~/$remote_dir"
}

cmd=${1:-}
[ $# -gt 0 ] && shift
case "$cmd" in
  check)
    check
    echo "LAPTOP: ok ($(ssh "${ssh_opts[@]}" "$laptop" 'xcodebuild -version | head -1'))"
    ;;
  sync)
    sync
    ;;
  run)
    [ $# -gt 0 ] || die "run needs a command"
    # One job per checkout at a time, so a second job's sync never rewrites files under a
    # build that's still running. The lock lives in this checkout's git folder, here.
    if [ -z "${SPINE_LAPTOP_CHECKOUT_LOCKED:-}" ]; then
      checkout_lock="$(git -C "$top" rev-parse --absolute-git-dir)/spine-laptop.lock"
      lockf -s -t 0 "$checkout_lock" true ||
        echo "Waiting:    another laptop job from this checkout is running; this one starts when it ends"
      SPINE_LAPTOP_CHECKOUT_LOCKED=1 exec lockf -s "$checkout_lock" "$0" run "$@"
    fi
    sync
    reap_sims
    # Then the laptop's line: the build lane of its agent-cleanup "ios" queue, which Armaan's
    # own agents on the laptop share, two builds at once (it has 32 GB). lockf frees a slot
    # when its job ends or is hung up; it exits 75 when the slot is busy.
    queue_dir="~/.local/state/agent-cleanup/queues"
    remote "$touch_sim; mkdir -p $queue_dir; cd ~/$remote_dir && said=
      while :; do
        for slot in ios.build.lock ios.build.2.lock; do
          lockf -s -t 0 $queue_dir/\$slot $(quote "$@"); rc=\$?
          [ \$rc -ne 75 ] && exit \$rc
        done
        [ -n \"\$said\" ] || { echo 'Waiting:    two builds are using the laptop; this one starts when one ends'; said=1; }
        sleep 10
      done"
    ;;
  exec)
    [ $# -gt 0 ] || die "exec needs a command"
    check
    remote "$touch_sim; cd ~/$remote_dir && $(quote "$@")"
    ;;
  sim)
    check
    case "${1:-}" in
      start)
        reap_sims
        remote "set -e
          u=\$(xcrun simctl list devices | grep -F '$sim_name (' | head -1 | sed -E 's/.*\\(([0-9A-F-]{36})\\).*/\\1/')
          [ -n \"\$u\" ] || u=\$(xcrun simctl create '$sim_name' com.apple.CoreSimulator.SimDeviceType.iPhone-17 com.apple.CoreSimulator.SimRuntime.iOS-26-5)
          xcrun simctl bootstatus \$u -b >/dev/null
          mkdir -p ~/$remote_repo/.sims; touch ~/$remote_repo/.sims/\$u
          echo \"SIM_ID: \$u\""
        ;;
      app)
        # Build Spine Dev for this checkout's simulator (start it first), install it, and
        # open it signed in as the test backend's dev user. Waits in the laptop's line.
        sim_id=$(remote "xcrun simctl list devices booted | grep -F '$sim_name (' | sed -E 's/.*\\(([0-9A-F-]{36})\\).*/\\1/' | head -1" | tr -d '\r')
        [ -n "$sim_id" ] || die "no simulator running for $name; run: scripts/ios/laptop.sh sim start"
        api=$("$(dirname "$0")/test-backend.sh" api)
        derived="../../derived.noindex/$name-sim"  # from the laptop copy; ~/spine-agent/derived.noindex/<name>-sim (.noindex: Spotlight skips it)
        "$0" run zsh -c "mkdir -p ../../derived.noindex && xcodebuild -project ios/Spine/Spine.xcodeproj -scheme Spine -configuration Debug -destination id=$sim_id -derivedDataPath $derived SPINE_API_BASE_URL=$api build > $derived.log 2>&1 || { grep -E 'error:|BUILD FAILED' $derived.log | head -30; echo STOPPED: build failed, log on the laptop at $derived.log; exit 1; }"
        remote "cd ~/$remote_dir && xcrun simctl install $sim_id $derived/Build/Products/Debug-iphonesimulator/Spine.app && xcrun simctl launch --terminate-running-process $sim_id com.armaan.Spine.dev --dev-sign-in >/dev/null" ||
          die "couldn't install or open Spine Dev on the simulator"
        echo "SIM_APP: Spine Dev is open on simulator $sim_id, signed in as the dev user, talking to $api"
        ;;
      stop)
        remote "for u in \$(xcrun simctl list devices | grep -F '$sim_name (' | sed -E 's/.*\\(([0-9A-F-]{36})\\).*/\\1/'); do xcrun simctl shutdown \$u >/dev/null 2>&1 || true; xcrun simctl delete \$u; rm -f ~/$remote_repo/.sims/\$u; echo \"Deleted simulator \$u\"; done"
        ;;
      *) die "sim needs start or stop" ;;
    esac
    ;;
  pull)
    [ $# -eq 2 ] || die "pull needs <remote path> <local path>"
    check
    case "$1" in /*) src=$1 ;; *) src=$remote_dir/$1 ;; esac
    scp -q "${ssh_opts[@]}" "$laptop:$src" "$2" || die "couldn't copy $1 from the laptop"
    echo "Pulled:     $2"
    ;;
  -h|--help|"")
    sed -n '2,19p' "$0"
    ;;
  *)
    die "unknown command: $cmd (see --help)"
    ;;
esac
