#!/usr/bin/env bash
# The ship line (scripts/ios/SHIP.md, Workflow A step 6): merge finished branches into
# myapp-main in the main checkout, run scripts/check.sh, and push. Several chats can say
# "ship this" at once: each ship leaves a ticket and waits for the line (a lock on the main
# checkout that ends with this script, even if it crashes).
#
# Whoever gets the line ships every waiting ticket together: it merges each branch (its own
# merge commit), runs the checks once and pushes once. A branch that conflicts is left out
# and told why. If the batch's checks fail, it falls back to one branch at a time, so the
# branch that broke them is the only one stopped. Each ship prints its own result: SHIPPED or
# STOPPED. A failed ship's merge is undone (never pushed), leaving the main checkout as it
# was. Watching CI and the deploy happens afterwards, outside the line.
#
# Usage: scripts/ship-merge.sh <branch> <merge commit message>
# Run it under agent-cleanup with a deadline that covers the wait (up to 3 hours) plus the
# checks, e.g. --timeout 14400.
set -euo pipefail

[ $# -eq 2 ] || { sed -n '2,17p' "$0"; exit 2; }

main=$(cd "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")" && pwd)
lock="$main/.git/spine-ship.lock"
queue="$main/.git/spine-ship-queue"

# Print a ticket's result and exit with its status.
finish() {
  local t=$1 code
  code=$(head -n 1 "$t/result")
  tail -n +2 "$t/result"
  rm -rf "$t"
  exit "$code"
}

if [ -z "${SPINE_SHIP_TICKET:-}" ]; then
  git rev-parse --verify --quiet "refs/heads/$1" >/dev/null || { echo "STOPPED: no branch $1" >&2; exit 1; }
  mkdir -p "$queue"
  ticket="$queue/$(date +%s)-$$"
  mkdir "$ticket"
  printf '%s\n' "$1" > "$ticket/branch"
  printf '%s' "$2" > "$ticket/message"
  echo $$ > "$ticket/pid"  # this process becomes lockf below (exec keeps the pid)
  lockf -s -t 0 "$lock" true ||
    echo "Waiting:  another ship has the line; this one goes in the next batch (up to 3 hours)"
  SPINE_SHIP_TICKET=$ticket exec lockf -s -t 10800 "$lock" "$0" "$@"
fi

ticket=$SPINE_SHIP_TICKET
# A batch that ran while this ship waited may already have shipped (or stopped) it.
[ -f "$ticket/result" ] && finish "$ticket"

cd "$main"
result() {  # result <ticket> <exit code> <message>
  printf '%s\n%s\n' "$2" "$3" > "$1/result"
}

# The batch: every waiting ticket whose ship is still alive, oldest first. A ship that died
# while waiting (its chat stopped) is dropped, not shipped behind its back.
batch=()
for t in "$queue"/*/; do
  t=${t%/}
  [ -f "$t/result" ] && continue
  if [ "$t" = "$ticket" ] || kill -0 "$(cat "$t/pid")" 2>/dev/null; then
    batch+=("$t")
  else
    rm -rf "$t"
  fi
done
echo "In line:  $(date '+%H:%M:%S'), shipping $(for t in "${batch[@]}"; do printf '%s ' "$(cat "$t/branch")"; done)"

stop_all() {
  for t in "${batch[@]}"; do result "$t" 1 "STOPPED: $1"; done
  finish "$ticket"
}
[ "$(git symbolic-ref --quiet --short HEAD)" = myapp-main ] ||
  stop_all "the main checkout isn't on myapp-main; nothing was merged"
[ -z "$(git status --porcelain)" ] ||
  stop_all "the main checkout has uncommitted changes; nothing was merged"
git fetch -q origin myapp-main
# Unpushed commits here (a ship that died mid-merge, or work by hand) must not ride along.
[ -z "$(git rev-list origin/myapp-main..HEAD)" ] && git merge -q --ff-only origin/myapp-main ||
  stop_all "local myapp-main has commits origin doesn't (a ship that died mid-merge?); nothing was merged. Armaan decides what to do with them."
before=$(git rev-parse HEAD)

# Merge each ticket's branch on top of the current HEAD; a conflict leaves it out.
merge_one() {  # merge_one <ticket>; returns 1 on conflict
  local t=$1 branch
  branch=$(cat "$t/branch")
  if ! git rev-parse --verify --quiet "refs/heads/$branch" >/dev/null; then
    result "$t" 1 "STOPPED: no branch $branch"; return 1
  fi
  if ! git merge -q --no-ff "$branch" -m "$(cat "$t/message")" >/dev/null 2>&1; then
    git merge --abort
    result "$t" 1 "STOPPED: $branch conflicts with myapp-main$( [ "${#batch[@]}" -gt 1 ] && echo ' or a branch shipped with it'). Merge origin/myapp-main into the branch, fix it, and ship again."
    return 1
  fi
  echo "Merged:   $(git log -1 --format='%h %s')"
}

push_or_stop() {  # push_or_stop <base> <tickets...>: push, or undo back to base
  local base=$1; shift
  if git push -q origin myapp-main; then
    local sha; sha=$(git rev-parse HEAD)
    for t in "$@"; do result "$t" 0 "SHIPPED:  $(cat "$t/branch") is in $sha on origin/myapp-main"; done
    echo "Pushed:   $sha"
    return 0
  fi
  git reset -q --hard "$base"
  for t in "$@"; do result "$t" 1 "STOPPED: the push was refused (someone pushed outside the line?); the merge was undone. Ship again."; done
  return 1
}

merged=()
for t in "${batch[@]}"; do merge_one "$t" && merged+=("$t"); done

pushed=0
if [ "${#merged[@]}" -gt 0 ]; then
  if scripts/check.sh; then
    push_or_stop "$before" "${merged[@]}" && pushed=1
  elif [ "${#merged[@]}" -eq 1 ]; then
    git reset -q --hard "$before"
    result "${merged[0]}" 1 "STOPPED: scripts/check.sh failed on the merged result; the merge was undone. Fix the branch and ship again."
  else
    # Find the branch that broke the checks: one at a time, each on top of the ones that pass.
    echo "Batch:    the checks failed with ${#merged[@]} branches; shipping them one at a time"
    git reset -q --hard "$before"
    for t in "${merged[@]}"; do
      base=$(git rev-parse HEAD)
      merge_one "$t" || continue
      if scripts/check.sh; then
        push_or_stop "$base" "$t" && pushed=1
      else
        git reset -q --hard "$base"
        result "$t" 1 "STOPPED: scripts/check.sh failed with $(cat "$t/branch") merged; the merge was undone. Fix the branch and ship again."
      fi
    done
  fi
fi

if [ $pushed -eq 1 ]; then
  scripts/ios/test-backend.sh update || echo "Note:     test-backend.sh update failed; run it again"
fi
finish "$ticket"
