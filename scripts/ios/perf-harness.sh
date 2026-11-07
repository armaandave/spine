#!/usr/bin/env bash
# Perf harness for the iOS Home tab and feed (ios/Spine/SpineUITests/HomePerfTests.swift).
#
# It never touches production or the shared test backend: the backend is this checkout's own
# (scripts/ios/local-backend.sh, own SQLite file) on a fixed port, and the posters come from a
# tiny local file server, so nothing leaves the Mac.
#
#   serve   foreground: poster server (127.0.0.1:8093) + local backend (127.0.0.1:8021)
#           ~/.local/bin/agent-cleanup run --owner <task> --label perf-serve --timeout 14400 -- scripts/ios/perf-harness.sh serve
#   seed    write the repeatable data set (scripts/ios/perf/seed_perf.py) into the served database
#   test [testName ...]   build and run HomePerfTests on a scratch simulator
#           ~/.local/bin/agent-cleanup run --owner <task> --label perf-test --timeout 7200 --queue ios \
#             --sim-runtime com.apple.CoreSimulator.SimRuntime.iOS-26-5 \
#             --sim-device-type com.apple.CoreSimulator.SimDeviceType.iPhone-17 \
#             -- scripts/ios/perf-harness.sh test
#   report <result dir>   print every measured metric (median, p90, min, max, samples) as a table
#
# Environment: PERF_ITERATIONS (default 8), PERF_LABEL (result directory prefix, e.g. before / after),
#              PERF_XCODEBUILD_ARGS (extra xcodebuild arguments, e.g. build settings).
# Results land in ~/Library/Caches/spine-perf-results/<label>-<timestamp>/ (xcodebuild.log, probe.txt,
# result.xcresult, summary.md).
set -euo pipefail

API_PORT=${PERF_API_PORT:-8021}
POSTER_PORT=${PERF_POSTER_PORT:-8093}
POSTER_DIR=${PERF_POSTER_DIR:-$HOME/Library/Caches/spine-perf-posters}
top=$(git rev-parse --show-toplevel)
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cmd=${1:-}
shift || true

die() { echo "perf-harness: $*" >&2; exit 1; }
[ "$API_PORT" != 8000 ] || die "port 8000 is production"
[ "$POSTER_PORT" != 8000 ] || die "port 8000 is production"

case "$cmd" in
  serve)
    [ -f "$POSTER_DIR/p39.jpg" ] || python3 "$here/perf/make_posters.py" "$POSTER_DIR" 40
    mkdir -p "$top/src/db"
    python3 -m http.server "$POSTER_PORT" --bind 127.0.0.1 --directory "$POSTER_DIR" >/dev/null 2>&1 &
    poster_pid=$!
    trap 'kill $poster_pid 2>/dev/null || true' EXIT
    echo "POSTERS: http://127.0.0.1:$POSTER_PORT"
    "$here/local-backend.sh" --port "$API_PORT"
    ;;
  seed)
    [ -z "${DB_HOST:-}" ] || die "DB_HOST is set; this seeds SQLite only"
    curl -s -o /dev/null --max-time 5 "http://127.0.0.1:$POSTER_PORT/p00.jpg" || die "start 'serve' first (poster server)"
    export DEBUG=True SPINE_DEV_SIGN_IN=1
    export REDIS_URL=redis://localhost:6379/5 CELERY_REDIS_URL=redis://localhost:6379/5
    export REDIS_PREFIX="local-$(basename "$top"):" DEV_CACHE=redis
    export PERF_POSTER_BASE="http://127.0.0.1:$POSTER_PORT"
    cd "$top/src"
    uv run --locked python manage.py shell -c "exec(open('$here/perf/seed_perf.py').read())"
    uv run --locked python manage.py shell -c 'from django.core.cache import cache; cache.delete_pattern("*")' >/dev/null
    ;;
  test)
    [ -n "${AGENT_CLEANUP_SIM_UDID:-}" ] || die "run under agent-cleanup with --sim-runtime/--sim-device-type (see header)"
    curl -s -o /dev/null --max-time 5 "http://127.0.0.1:$API_PORT/api/v1/health/" || die "start 'serve' first (backend)"
    stamp=$(date +%Y%m%d-%H%M%S)
    dir="${PERF_RESULTS_DIR:-$HOME/Library/Caches/spine-perf-results}/${PERF_LABEL:-run}-$stamp"
    mkdir -p "$dir"
    only=()
    if [ $# -eq 0 ]; then
      only=(-only-testing:SpineUITests/HomePerfTests -skip-testing:SpineUITests/HomePerfTests/testZ_DumpAccessibilityTrees)
    else
      for t in "$@"; do only+=("-only-testing:SpineUITests/HomePerfTests/$t"); done
    fi
    export TEST_RUNNER_SPINE_API_BASE_URL="http://127.0.0.1:$API_PORT"
    export TEST_RUNNER_PERF_PROBE_FILE="$dir/probe.txt"
    [ -z "${PERF_ITERATIONS:-}" ] || export TEST_RUNNER_PERF_ITERATIONS="$PERF_ITERATIONS"
    : > "$dir/probe.txt"
    echo "RESULT_DIR: $dir"
    # A quiet machine matters: close other simulators and builds before a run.
    uptime > "$dir/load.txt"
    set +e
    # shellcheck disable=SC2086
    xcodebuild test -project "$top/ios/Spine/Spine.xcodeproj" -scheme Spine \
      -destination "id=$AGENT_CLEANUP_SIM_UDID" "${only[@]}" \
      -parallel-testing-enabled NO -resultBundlePath "$dir/result.xcresult" -jobs 2 \
      ${PERF_XCODEBUILD_ARGS:-} > "$dir/xcodebuild.log" 2>&1
    status=$?
    set -e
    uptime >> "$dir/load.txt"
    grep -E "^Test Case .*(passed|failed)|error:|\*\* TEST" "$dir/xcodebuild.log" | tail -30 || true
    python3 "$here/perf/report.py" "$dir" | tee "$dir/summary.md" || true
    exit $status
    ;;
  report)
    python3 "$here/perf/report.py" "$@"
    ;;
  *)
    sed -n '2,24p' "$0"; exit 2 ;;
esac
