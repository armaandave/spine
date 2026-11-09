#!/usr/bin/env bash
# A/B perf session: the frozen baseline app (A) against this checkout's app (B), in alternating legs, so machine
# state (this Mac also serves production) hits both the same way.
#
#   scripts/ios/perf-ab.sh <session> <rounds> <baseline tree> <testName>...
#
#   <session>        results go to ~/Library/Caches/spine-perf-results/<session>/{A,B}-r<round>-<stamp>/
#   <rounds>         legs per variant; round r runs A then B
#   <baseline tree>  a checkout of the old app plus the shared harness files (not a git repo is fine):
#                    ~/Library/Caches/home-redesign-baseline/tree
#   <testName>       HomePerfTests methods, e.g. testB_FeedScroll testB2_FeedScrollColdImages
#
# Each leg is one scratch-simulator run through the ios queue:
#   agent-cleanup run ... --queue ios -- scripts/ios/perf-harness.sh test <testName>...
# with PERF_TOP (the tree to build), PERF_LABEL=<A|B>-r<round>, PERF_RESULTS_DIR and PERF_ITERATIONS (default 6).
#
# Environment: PERF_ITERATIONS (default 6), PERF_COLD_IMAGES=1 (passed through to the harness),
#              PERF_AB_ONLY=A or B (run only that variant's legs: B for scenarios the baseline does not have).
# Resumable: a leg whose directory already has summary.md and no FAILED file is skipped. Failed legs are retried on the
# next run. At the end it prints the A/B table (scripts/ios/perf/report.py --ab) and exits 1 if a leg failed.
# The backend (scripts/ios/perf-harness.sh serve, port 8021) must be running; this script is not itself wrapped, so
# start it in the background with a long outer timeout (2 hours or more).
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
top=$(cd "$here/../.." && pwd)
root=${PERF_RESULTS_ROOT:-$HOME/Library/Caches/spine-perf-results}

die() { echo "perf-ab: $*" >&2; exit 2; }
[ $# -ge 4 ] || die "usage: perf-ab.sh <session> <rounds> <baseline tree> <testName>..."
session=$1
rounds=$2
baseline=$3
shift 3
case "$rounds" in ''|*[!0-9]*) die "rounds must be a number" ;; esac
[ -f "$baseline/ios/Spine/Spine.xcodeproj/project.pbxproj" ] || die "no Xcode project in baseline tree $baseline"
only=${PERF_AB_ONLY:-}
case "$only" in ''|A|B) ;; *) die "PERF_AB_ONLY must be A or B" ;; esac

session_dir=$root/$session
mkdir -p "$session_dir"
export PERF_ITERATIONS=${PERF_ITERATIONS:-6}
failed=0

leg_done() {
  local d
  for d in "$session_dir/$1"-[0-9]*; do
    [ -f "$d/summary.md" ] && [ ! -e "$d/FAILED" ] && return 0
  done
  return 1
}

run_leg() {
  local variant=$1 round=$2 tree=$3
  shift 3
  local label=$variant-r$round
  if leg_done "$label"; then
    echo "perf-ab: $label already done, skipping"
    return 0
  fi
  echo "perf-ab: $label on $tree ($(uptime | sed 's/.*load/load/'))"
  if PERF_TOP=$tree PERF_LABEL=$label PERF_RESULTS_DIR=$session_dir \
    "$HOME/.local/bin/agent-cleanup" run --owner home-redesign --label "perf-ab-$label" \
    --timeout 5400 --queue ios --queue-wait 3600 \
    --sim-runtime com.apple.CoreSimulator.SimRuntime.iOS-26-5 \
    --sim-device-type com.apple.CoreSimulator.SimDeviceType.iPhone-17 \
    -- "$here/perf-harness.sh" test "$@"; then
    echo "perf-ab: $label finished"
  else
    echo "perf-ab: $label FAILED (exit $?); its directory keeps the log" >&2
    failed=1
  fi
}

for round in $(seq 1 "$rounds"); do
  if [ -z "$only" ] || [ "$only" = A ]; then run_leg A "$round" "$baseline" "$@"; fi
  if [ -z "$only" ] || [ "$only" = B ]; then run_leg B "$round" "$top" "$@"; fi
done

python3 "$here/perf/report.py" --ab "$session_dir" | tee "$session_dir/ab.md"
python3 "$here/perf/report.py" --ab "$session_dir" --json > "$session_dir/ab.json"
exit $failed
