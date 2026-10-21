#!/usr/bin/env bash
# Run this checkout's Django backend on the Mac so the iPhone can reach it over the
# same Wi-Fi, to test backend changes without deploying them. Production is never
# touched:
#   - The server uses this checkout's own SQLite database (src/db/db.sqlite3, new and
#     empty in a new worktree) and migrates it, so a branch's migrations are safe here.
#     It refuses to start if the settings would use anything but SQLite.
#   - Redis traffic (Celery included) goes to Redis DB 5 under this checkout's own
#     prefix, never DB 0, where an old dev backlog sits.
#   - It never uses port 8000, which is the production stack on this Mac.
# Dev sign-in is on, so Debug builds launched with --dev-sign-in open signed in as the
# passwordless dev user.
#
# It stays in the foreground (the server plus a Celery worker). Run it under the
# cleanup wrapper, in the background:
#   ~/.local/bin/agent-cleanup run --owner <task> --label spine-local-backend -- scripts/ios/local-backend.sh
# Once it answers it prints "LOCAL_API: http://<this Mac>.local:<port>".
#
# Usage: scripts/ios/local-backend.sh [--port <n>]  (default: first free port from 8001)
#        scripts/ios/local-backend.sh --status      print this checkout's LOCAL_API if it runs
set -euo pipefail

port=""
status=0
while [ $# -gt 0 ]; do
  case "$1" in
    --port) port=$2; shift ;;
    --status) status=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

die() { echo "STOPPED: $*" >&2; exit 1; }

top=$(git rev-parse --show-toplevel)
host="$(scutil --get LocalHostName).local"

# Ports that Python processes started in this checkout's src/ listen on.
own_ports() {
  { lsof -nP -a -iTCP -sTCP:LISTEN -c '/python/i' 2>/dev/null || true; } |
    awk 'NR > 1 { n = split($9, a, ":"); print $2, a[n] }' | sort -u |
    while read -r pid p; do
      cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
      if [ "$cwd" = "$top/src" ]; then echo "$p"; fi
    done | sort -un
}

ready() { [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$1/api/v1/health/")" = 200 ]; }

running=""
for p in $(own_ports); do
  if ready "http://127.0.0.1:$p"; then running=$p; break; fi
done

if [ $status -eq 1 ]; then
  [ -n "$running" ] || { echo "No local backend is running from $top." >&2; exit 1; }
  echo "LOCAL_API: http://$host:$running"
  exit 0
fi
if [ -n "$running" ]; then
  echo "This checkout's backend is already running on port $running."
  echo "LOCAL_API: http://$host:$running"
  exit 0
fi

[ -z "${DB_HOST:-}" ] || die "DB_HOST is set, which would point the server at Postgres. Unset it."

in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }
if [ -n "$port" ]; then
  [ "$port" != 8000 ] || die "port 8000 is the production stack on this Mac"
  in_use "$port" && die "port $port is already in use"
else
  port=8001
  while in_use "$port"; do port=$((port + 1)); [ $port -lt 8100 ] || die "no free port between 8001 and 8099"; done
fi

export DEBUG=True
export SPINE_DEV_SIGN_IN=1
export REDIS_URL=redis://localhost:6379/5
export CELERY_REDIS_URL=redis://localhost:6379/5
export REDIS_PREFIX="local-$(basename "$top"):"

cd "$top/src"
vendor=$(uv run --locked python manage.py shell -c 'from django.db import connection; print(connection.vendor)' 2>/dev/null | tail -n 1)
[ "$vendor" = sqlite ] || die "the settings use '$vendor', not this checkout's SQLite database"
echo "Migrating this checkout's SQLite database..."
uv run --locked python manage.py migrate --noinput >/dev/null

pids=()
cleanup() { [ ${#pids[@]} -eq 0 ] || kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT

uv run --locked celery -A config worker -l warning --concurrency 2 &
pids+=($!)
uv run --locked python manage.py runserver "0.0.0.0:$port" &
server=$!
pids+=($server)

for _ in $(seq 1 60); do
  ready "http://127.0.0.1:$port" && break
  kill -0 "$server" 2>/dev/null || die "the server exited while starting"
  sleep 2
done
ready "http://127.0.0.1:$port" || die "the server on port $port did not answer"
ready "http://$host:$port" || die "the server answers locally but not at $host:$port; check the Mac's network"
echo "The iPhone must be on the same Wi-Fi as this Mac (or the Mac on the iPhone's hotspot)."
echo "LOCAL_API: http://$host:$port"
wait "$server"
