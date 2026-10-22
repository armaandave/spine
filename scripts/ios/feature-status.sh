#!/usr/bin/env bash
# Summarize what the current checkout's branch changed relative to origin/myapp-main,
# which is what production runs, and whether a phone test needs the test backend to serve
# this checkout (scripts/ios/TEST_ON_IPHONE.md).
# Read-only: never modifies the repo, the phone, or production.
#
# Usage: run from inside the checkout (worktree) you want to test.
#   scripts/ios/feature-status.sh
#
# Last line is machine-readable: "VERDICT: <value>" where value is one of
#   no-backend     no backend changes
#   backend-live   the backend part is already on origin/myapp-main, so production runs it
#   local-backend  backend changes production doesn't have (committed or not); a phone
#                  test serves them first (scripts/ios/test-backend.sh use); a ship deploys them
set -euo pipefail
source "$(dirname "$0")/lib/changes.sh"

top=$(git rev-parse --show-toplevel)
cd "$top"
base=$(spine_base_ref)
branch=$(git symbolic-ref --quiet --short HEAD || echo "(detached HEAD)")

echo "Checkout: $top"
echo "Branch:   $branch  ($(git log -1 --format='%h %s'))"
echo "Compared: $base...HEAD  (changes made on this branch since it left $base)"
echo "          Run 'git fetch origin' first if $base may be stale."

ios=() backend=() tests=() migrations=() docs=() tooling=() api=()
while IFS=$'\t' read -r status path; do
  [ -n "$path" ] || continue
  case "$(spine_area "$path")" in
    ios) ios+=("$status $path") ;;
    backend) backend+=("$status $path") ;;
    tests) tests+=("$status $path") ;;
    migration) migrations+=("$status $path") ;;
    docs) docs+=("$status $path") ;;
    tooling) tooling+=("$status $path") ;;
  esac
  if spine_is_api "$path"; then api+=("$status $path"); fi
done < <(spine_committed_changes)

section() {
  local title=$1; shift
  echo
  echo "$title ($#)"
  [ $# -gt 0 ] || { echo "  none"; return; }
  printf '  %s\n' "$@"
}
section "iOS app" ${ios[@]+"${ios[@]}"}
section "Backend (production builds it from myapp-main)" ${backend[@]+"${backend[@]}"}
section "Database migrations" ${migrations[@]+"${migrations[@]}"}
section "Backend tests" ${tests[@]+"${tests[@]}"}
section "Docs" ${docs[@]+"${docs[@]}"}
section "Tooling (CI, scripts, skills)" ${tooling[@]+"${tooling[@]}"}
section "API code the app calls (read the diff for compatibility with installed builds)" ${api[@]+"${api[@]}"}

dirty=() dirty_backend=() dirty_migrations=()
while IFS= read -r path; do
  [ -n "$path" ] || continue
  dirty+=("$path")
  case "$(spine_area "$path")" in
    backend) dirty_backend+=("$path") ;;
    migration) dirty_migrations+=("$path") ;;
  esac
done < <(spine_uncommitted_paths)
if [ ${#dirty[@]} -gt 0 ]; then
  section "Uncommitted changes (included in a phone build, not in a ship)" "${dirty[@]}"
fi

# Is every committed backend file already identical on the base, so production has it?
live=yes
for entry in ${backend[@]+"${backend[@]}"} ${migrations[@]+"${migrations[@]}"}; do
  path=${entry#* }
  if ! git diff --quiet "$base" HEAD -- "$path"; then live=no; break; fi
done

needs_local=no
if [ ${#dirty_backend[@]} -gt 0 ] || [ ${#dirty_migrations[@]} -gt 0 ]; then needs_local=yes; fi
if { [ ${#backend[@]} -gt 0 ] || [ ${#migrations[@]} -gt 0 ]; } && [ "$live" = no ]; then needs_local=yes; fi

echo
if [ ${#migrations[@]} -gt 0 ] || [ ${#dirty_migrations[@]} -gt 0 ]; then
  echo "Migrations: a local backend applies them to this checkout's own SQLite database."
  echo "Production applies them when a deploy's new container starts, after the pre-deploy backup."
fi
if [ "$needs_local" = yes ]; then
  echo "Backend changes that production doesn't have. For a phone test, serve them from the test"
  echo "backend first (scripts/ios/test-backend.sh use); nothing needs to be deployed."
  verdict=local-backend
elif [ ${#backend[@]} -gt 0 ] || [ ${#migrations[@]} -gt 0 ]; then
  echo "The backend part is already on $base, so production runs it."
  verdict=backend-live
else
  echo "No backend changes."
  verdict=no-backend
fi
echo "VERDICT: $verdict"
