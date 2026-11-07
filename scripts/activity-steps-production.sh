#!/usr/bin/env bash
# Run one of Activity's one-time data steps on production.
#
# Usage: scripts/activity-steps-production.sh <undated-reads|past-restarts|picked-up-books|twerp|old-eye-taps> [--apply]
#   undated-reads    undated imported Reads become marks (manage.py fill_undated_reads)
#   past-restarts    past book Restarts get their flag (manage.py flag_past_restarts)
#   picked-up-books  books already picked up get their place back (manage.py fill_picked_up_books)
#   twerp            Twerp, a Read on account 1 with no log, gets the eye (manage.py mark_twerp)
#   old-eye-taps     the old TV eye's dated episodes become marks with no date (manage.py undate_old_eye_taps)
#
# Without --apply it's a dry run that prints counts. With --apply it first writes a verified
# pg_dump, then keeps the step's old ids and values in a 600 file beside the dump. They
# never go on the container's disk, which the next deploy recreates, or under
# /yamtrack/media, which nginx serves publicly. The console shows counts only.
#
# Run it only on request, from the server Mac (AGENTS.md section 16, scripts/ios/SHIP.md),
# and never while a deploy could start.
set -euo pipefail
umask 077

repo_dir="${SPINE_DEPLOY_DIR:-$HOME/projects/spine-deploy}"
env_file="${SPINE_DEPLOY_ENV_FILE:-$HOME/projects/spine/.env.production}"
state_dir="${SPINE_BACKUP_DIR:-$HOME/projects/spine-backups}"

die() {
  echo "$1" >&2
  exit 1
}

step="${1:-}"
case "$step" in
  undated-reads) command=fill_undated_reads ;;
  past-restarts) command=flag_past_restarts ;;
  picked-up-books) command=fill_picked_up_books ;;
  twerp) command=mark_twerp ;;
  old-eye-taps) command=undate_old_eye_taps ;;
  *) die "Usage: scripts/activity-steps-production.sh <undated-reads|past-restarts|picked-up-books|twerp|old-eye-taps> [--apply]" ;;
esac
apply=false
if [[ "${2:-}" == "--apply" ]]; then
  apply=true
elif [[ -n "${2:-}" ]]; then
  die "Usage: scripts/activity-steps-production.sh <undated-reads|past-restarts|picked-up-books|twerp|old-eye-taps> [--apply]"
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

  migrations="$(manage showmigrations social)"
  grep -q '\[X\] 0005_progresschange_saved_on' <<<"$migrations" || \
    die "Migration social 0005_progresschange_saved_on isn't applied"
}

backup() {
  mkdir -p "$state_dir"
  chmod 700 "$state_dir"
  database_bytes="$("${compose[@]}" exec -T db psql -U spine -d spine -Atc "SELECT pg_database_size(current_database())")"
  available_bytes="$(df -Pk "$state_dir" | awk 'NR == 2 {printf "%.0f\n", $4 * 1024}')"
  (( available_bytes >= database_bytes * 2 )) || die "Insufficient disk space for a verified production backup"

  backup_file="$state_dir/spine-activity-$step-$timestamp-${deployed_sha:0:12}.dump"
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
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
echo "$command on deployed commit ${deployed_sha:0:12}"

if [[ "$apply" == true ]]; then
  backup
  old_values="$state_dir/spine-activity-$step-$timestamp-old-values.jsonl"
  # The old values come on standard output and the counts on standard error.
  manage "$command" --apply > "$old_values"
  chmod 600 "$old_values"
  echo "old values: $(basename "$old_values")"
else
  manage "$command"
fi

curl --fail --silent --show-error http://127.0.0.1:8000/health/ >/dev/null
