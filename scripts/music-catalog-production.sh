#!/usr/bin/env bash
# Run one step of the music data move (manage.py music_catalog) on production.
#
# Usage: scripts/music-catalog-production.sh <step> [args...] [--confirm]
#   check [--repair]               test the Apple Music key and count what needs attention
#   map [--limit N]                match albums (link rows only); run it at night
#   report                         counts here; every album's row to a private CSV
#   link --mbid M --apple A        record a manual pick
#   apply [--limit N]              move matched albums
#   revert --mbid M | --all        undo moves (--all only with the catalog on musicbrainz)
#
# Run it only when Armaan asks, from the server Mac (AGENTS.md section 16), and never
# while a deploy could start: a deploy recreates the container and kills the run, which
# then resumes when run again. Steps that change data (link, apply, revert, check
# --repair) need --confirm, and first write a verified pg_dump to
# ~/projects/spine-deploy/backups/music-catalog/. Reports go to the same private
# folder; the console shows counts only. See docs/music/apple-music-catalog.md.
set -euo pipefail
umask 077

repo_dir="${SPINE_DEPLOY_DIR:-$HOME/projects/spine-deploy}"
env_file="${SPINE_DEPLOY_ENV_FILE:-$HOME/projects/spine/.env.production}"
state_dir="$repo_dir/backups/music-catalog"

die() {
  echo "$1" >&2
  exit 1
}

step="${1:-}"
if (($#)); then
  shift
fi
confirm=false
args=()
for arg in "$@"; do
  if [[ "$arg" == "--confirm" ]]; then
    confirm=true
  else
    args+=("$arg")
  fi
done

case "$step" in
  check | map | report | link | apply | revert) ;;
  *) die "Usage: scripts/music-catalog-production.sh <check|map|report|link|apply|revert> [args...] [--confirm]" ;;
esac

changes_data=false
case "$step" in
  link | apply | revert) changes_data=true ;;
  check)
    for arg in "${args[@]+"${args[@]}"}"; do
      if [[ "$arg" == "--repair" ]]; then
        changes_data=true
      fi
    done
    ;;
  report)
    for arg in "${args[@]+"${args[@]}"}"; do
      if [[ "$arg" == --csv* ]]; then
        die "report writes its CSV to $state_dir itself"
      fi
    done
    ;;
esac
if [[ "$changes_data" == true && "$confirm" != true ]]; then
  die "$step changes production data: run it again with --confirm"
fi

[[ -d "$repo_dir/.git" ]] || die "Missing deployed repository: $repo_dir"
[[ -f "$env_file" ]] || die "Missing production environment file"
deployed_sha="$(git -C "$repo_dir" rev-parse HEAD)"

compose=(
  docker compose
  --project-name spine
  --env-file "$env_file"
  -f "$repo_dir/docker-compose.production.yml"
)

manage() {
  "${compose[@]}" exec -T app python manage.py "$@"
}

preflight() {
  curl --fail --silent --show-error http://127.0.0.1:8000/health/ >/dev/null
  container_sha="$("${compose[@]}" exec -T app printenv SPINE_COMMIT_SHA)"
  [[ "$container_sha" == "$deployed_sha" ]] || \
    die "Running app container $container_sha does not match deployed commit $deployed_sha"

  migrations="$(manage showmigrations app)"
  grep -q '\[X\] 0086_apple_music_catalog' <<<"$migrations" || \
    die "Migration app 0086_apple_music_catalog isn't applied"
}

backup() {
  mkdir -p "$state_dir"
  chmod 700 "$state_dir"
  database_bytes="$("${compose[@]}" exec -T db psql -U spine -d spine -Atc "SELECT pg_database_size(current_database())")"
  available_bytes="$(df -Pk "$state_dir" | awk 'NR == 2 {printf "%.0f\n", $4 * 1024}')"
  (( available_bytes >= database_bytes * 2 )) || die "Insufficient disk space for a verified production backup"

  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_file="$state_dir/spine-music-catalog-$timestamp-${deployed_sha:0:12}-$step.dump"
  partial="$backup_file.partial"
  trap 'rm -f "$partial"' EXIT
  "${compose[@]}" exec -T db pg_dump -U spine -d spine -Fc > "$partial"
  "${compose[@]}" exec -T db pg_restore --list < "$partial" >/dev/null
  mv "$partial" "$backup_file"
  chmod 600 "$backup_file"
  trap - EXIT
  echo "backup: $(basename "$backup_file") (verified with pg_restore --list)"
}

preflight
echo "music_catalog $step on deployed commit ${deployed_sha:0:12}"

if [[ "$changes_data" == true ]]; then
  backup
fi

if [[ "$step" == "report" ]]; then
  mkdir -p "$state_dir"
  chmod 700 "$state_dir"
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  report_file="$state_dir/music-catalog-report-$timestamp-${deployed_sha:0:12}.csv"
  # The CSV comes on standard output and the counts on standard error.
  manage music_catalog report --csv - > "$report_file"
  chmod 600 "$report_file"
  echo "report: $report_file"
else
  manage music_catalog "$step" "${args[@]+"${args[@]}"}"
fi

curl --fail --silent --show-error http://127.0.0.1:8000/health/ >/dev/null
