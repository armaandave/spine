#!/usr/bin/env bash
# Set up an Activity phone sheet on the test backend's throwaway copy of its database,
# then run its steps from the Mac (src/api/tests/activity_checkpoint_seed.py).
#
#   scripts/ios/seed-activity-checkpoint.sh [seed|add-k-l|add-s|add-u|add-r|
#                                            like-from-tokyo|friend-starts-over|print-rows|
#                                            new-issue-c2|add-g6|add-g8|add-m4|add-q|
#                                            add-game-2023|add-t4|add-t6|
#                                            seed-phase-4|add-n|friend-day-order|friend-typo|
#                                            friend-typo-fixed|friend-save-and-finish|
#                                            friend-pause-stop-pick-up|
#                                            friend-game-stops-then-plays|friend-ticks|
#                                            friend-late-tick|friend-import-last-week|
#                                            friend-marks|friend-earlier-question|
#                                            friend-private-season|comment-on-my-row]
#
# Phase 4's sheet opens with seed-phase-4 (not seed), and its Mac steps are the friend-*
# steps and comment-on-my-row, each named after the row of the sheet it runs.
#
# Run it from the feature worktree the test backend is serving ("use"). It clears the dev
# user's tracking, logs, saves, likes, comments, lists and follows, so it refuses to run
# anywhere else: production, the main checkout, the test backend's own checkout (its lasting
# test database), or a database that isn't SQLite.
set -euo pipefail

step=${1:-seed}
top=$(git rev-parse --show-toplevel)
main_root=$(git rev-parse --path-format=absolute --git-common-dir | sed 's|/.git$||')
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

die() { echo "error: $*" >&2; exit 1; }

[ "$top" != "$main_root" ] || die "the main checkout's database holds personal data. Run this from a feature worktree."
[ "$top" != "$main_root/.claude/test-backend" ] || die "that's the test backend's own checkout, and its lasting test database. Run this from a feature worktree."
[ -z "${DB_HOST:-}" ] || die "DB_HOST is set, which would point this at Postgres. Unset it."
backend=$("$script_dir/test-backend.sh" status)
served=$(echo "$backend" | sed -n 's/^SERVED_CHECKOUT: //p')
[ "$served" = "$top" ] || die "the test backend is serving $served, not $top. Run: scripts/ios/test-backend.sh use 12"
until_when=$(echo "$backend" | sed -n 's/^SERVING: .*, until //p')

year=$(date +%Y)
[ "$year" = 2026 ] || echo "warning: this sheet's steps put \"today\" in 2026, and today is $year. A today step lands in another column."

cd "$top/src"
# The served backend's settings (test-backend.sh fill_metadata), so its Stats cache sees
# each write. Without them the phone keeps the old number for an hour.
export REDIS_URL=redis://localhost:6379/5 CELERY_REDIS_URL=redis://localhost:6379/5
export REDIS_PREFIX="local-$(basename "$top"):"
export DEBUG=True DEV_CACHE=redis

vendor=$(uv run --locked python manage.py shell -c 'from django.db import connection; print(connection.vendor)' 2>/dev/null | tail -n 1)
[ "$vendor" = sqlite ] || die "the settings use '$vendor', not the test backend's SQLite database"

uv run --locked python manage.py shell -c "from api.tests.activity_checkpoint_seed import STEPS; STEPS['$step']()"
echo "Ran $step. Pull to refresh on Stats, Home and the list: the app holds pages in memory."
echo "Open the sheet's books from Home's In progress, the Library or the Checkpoint titles list, never from search: it finds real books of the same name."
echo "On that list the first three tiles are Book J, Book V and Book X, in that order. They look alike, and they're all 300 pages, so a wrong tap gives the same number."
echo "The test backend serves this until ${until_when:-you release it}. Then it goes back to myapp-main by itself and what was written is dropped, so reseed and restart the sheet from the top."
