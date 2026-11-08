#!/usr/bin/env bash
# The ship line (scripts/ios/SHIP.md, Workflow A step 6): merge a finished branch into
# myapp-main in the main checkout, run scripts/check.sh on the result, and push. One ship at
# a time: the others wait in line, so several chats can say "ship this" at once. The line is
# a lock on the main checkout that ends with this script, even if it crashes.
#
# Before merging it catches the main checkout up to origin/myapp-main, so each ship includes
# the ones before it. If the merge conflicts or the checks fail, it undoes its own merge
# (never pushed) and stops, leaving the main checkout as it found it for the next ship.
# Watching CI and the deploy happens afterwards, outside the line.
#
# Usage: scripts/ship-merge.sh <branch> <merge commit message>
# Run it under agent-cleanup with a deadline that covers the wait (up to 3 hours) plus the
# checks, e.g. --timeout 14400.
set -euo pipefail

die() { echo "STOPPED: $*" >&2; exit 1; }

[ $# -eq 2 ] || { sed -n '2,15p' "$0"; exit 2; }
branch=$1
message=$2

main=$(cd "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")" && pwd)
lock="$main/.git/spine-ship.lock"

if [ -z "${SPINE_SHIP_IN_LINE:-}" ]; then
  lockf -s -t 0 "$lock" true ||
    echo "Waiting:  another ship is merging; this one goes when it ends (up to 3 hours)"
  SPINE_SHIP_IN_LINE=1 exec lockf -s -t 10800 "$lock" "$0" "$@"
fi

cd "$main"
echo "In line:  $(date '+%H:%M:%S'), shipping $branch from $main"
[ "$(git symbolic-ref --quiet --short HEAD)" = myapp-main ] ||
  die "the main checkout isn't on myapp-main; nothing was merged"
[ -z "$(git status --porcelain)" ] ||
  die "the main checkout has uncommitted changes; nothing was merged"
git rev-parse --verify --quiet "refs/heads/$branch" >/dev/null || die "no branch $branch"

git fetch -q origin myapp-main
git merge -q --ff-only origin/myapp-main ||
  die "local myapp-main has commits origin doesn't; nothing was merged"
before=$(git rev-parse HEAD)

if ! git merge -q --no-ff "$branch" -m "$message"; then
  git merge --abort
  die "$branch conflicts with myapp-main. Merge origin/myapp-main into the branch, fix it, and ship again."
fi
echo "Merged:   $(git log -1 --format='%h %s')"

if ! scripts/check.sh; then
  git reset -q --hard "$before"
  die "scripts/check.sh failed on the merged result; the merge was undone. Fix the branch and ship again."
fi

if ! git push -q origin myapp-main; then
  git reset -q --hard "$before"
  die "the push was refused (someone pushed outside the line?); the merge was undone. Ship again."
fi
echo "SHIPPED:  $(git rev-parse HEAD) is on origin/myapp-main"

scripts/ios/test-backend.sh update || echo "Note:     test-backend.sh update failed; run it again"
