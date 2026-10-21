#!/usr/bin/env bash
# The CI "Fast checks", run locally from any checkout or worktree before calling work
# done: lint, models without a migration, and the full Django test suite (SQLite, test
# settings). Uses uv.lock, the same dependencies as CI and the production image.
#
# Usage: scripts/check.sh   (SPINE_TEST_PARALLEL sets the test processes, default 4)
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
echo "== ruff"
uv run --locked ruff check src

cd src
echo "== migrations"
uv run --locked python manage.py makemigrations --check --dry-run
echo "== tests"
uv run --locked python manage.py test --parallel "${SPINE_TEST_PARALLEL:-4}" --noinput
