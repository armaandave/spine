#!/usr/bin/env bash
set -euo pipefail

branch="${1:-}"
repo_dir="$HOME/projects/spine"

if [[ -z "$branch" ]]; then
  echo "Usage: $0 <branch>" >&2
  exit 2
fi

if [[ "$branch" == -* ]] || ! git check-ref-format --branch "$branch" >/dev/null 2>&1; then
  echo "Invalid branch: $branch" >&2
  exit 2
fi

cd "$repo_dir"

if [[ ! -f .env.production ]]; then
  echo "Missing $repo_dir/.env.production" >&2
  exit 1
fi

compose=(docker compose --env-file .env.production -f docker-compose.production.yml)
build_log="$(mktemp -t spine-deploy-build.XXXXXX)"
trap 'rm -f "$build_log"' EXIT

build_was_all_cached() {
  awk '
    /^#[0-9]+ \[/ {
      id = $1
      if ($0 !~ /\[internal\]/ && $0 !~ /exporting/ && $0 !~ /importing/ && $0 !~ /resolving/) {
        step[id] = 1
      }
    }
    $2 == "CACHED" && step[$1] { cached++ }
    $2 == "DONE" && step[$1] { done++ }
    END { exit !(cached > 0 && done == 0) }
  ' "$1"
}

# The new containers did not become healthy. compose does not roll back, so the old
# app may already be gone: show what is running and why, then stop.
stack_failed() {
  echo >&2
  echo "The stack did not become healthy (docker compose up --wait failed or timed out)." >&2
  echo "The old containers may already be gone, so the site can be down. Current state and last logs:" >&2
  "${compose[@]}" ps -a >&2 || true
  for service in app db redis; do
    echo "--- $service (last 30 log lines)" >&2
    "${compose[@]}" logs --no-color --tail 30 "$service" >&2 || true
  done
  exit 1
}

git fetch origin
if git show-ref --verify --quiet "refs/heads/$branch"; then
  git checkout "$branch"
else
  git checkout --track "origin/$branch"
fi
git pull --ff-only origin "$branch"

export DOCKER_BUILDKIT=1
export BUILDKIT_PROGRESS=plain

echo "Docker Compose $(docker compose version --short)"

# --wait returns once db, redis and app report healthy (db and redis gate the app start
# in the compose file), so the migrate below cannot hit a restarting container.
if ! "${compose[@]}" up -d --build --wait --wait-timeout 240 2>&1 | tee "$build_log"; then
  stack_failed
fi

if build_was_all_cached "$build_log"; then
  echo "Build used only cached layers; rebuilding app with --no-cache."
  "${compose[@]}" build --no-cache app
  "${compose[@]}" up -d --wait --wait-timeout 240 || stack_failed
fi

"${compose[@]}" exec -T app python manage.py migrate

curl --fail --show-error --silent https://api.spine-api.com/api/v1/health/
echo

meta_json="$(curl --fail --show-error --silent https://api.spine-api.com/api/v1/meta/)"
python3 -c 'import json, sys; data = json.load(sys.stdin); missing = ({"music"} - set(data["media_types"])) | ({"musicbrainz"} - set(data["source_choices"])); missing and sys.exit(f"Music rollout missing from API metadata: {sorted(missing)}")' <<<"$meta_json"
echo "Music rollout verified."

# Last, so a failure here reports a degraded (not broken) deploy: the stack is up.
# Fails if nginx is not seeing real visitor IPs; see docs/production-networking.md.
real_ip_check="$repo_dir/scripts/verify-real-client-ip.sh"
if [[ -f "$real_ip_check" ]]; then
  app_container="$("${compose[@]}" ps -q app 2>/dev/null || true)"
  bash "$real_ip_check" "${app_container:-spine}"
else
  echo "Skipping the real-client-IP check: $real_ip_check is not on this branch."
fi
