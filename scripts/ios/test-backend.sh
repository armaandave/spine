#!/usr/bin/env bash
# The always-on test backend: the server the Spine Dev app (Debug builds) talks to, so Spine Dev
# works whenever it's opened, not only during a test. launchd keeps it running on this Mac on port
# 8010, at low priority. It is scripts/ios/local-backend.sh underneath, so production is never
# touched: SQLite only, Redis DB 5 under its own prefix, never port 8000.
#
# Spine Dev reaches it through Tailscale at https://<this Mac's tailnet name>:8445 (tailscale serve,
# tailnet only), so it works anywhere the iPhone's Tailscale app is connected. Without that Tailscale
# entry, builds fall back to http://<this Mac>.local:8010, which works only on the Mac's Wi-Fi.
#
# Normally it serves myapp-main from its own checkout, .claude/test-backend in the main checkout,
# whose src/db/db.sqlite3 is the lasting test database (test accounts only, never personal data).
# "use" points the same address at a feature checkout's backend for a few hours, on a fresh copy
# of that database, so a branch's backend changes can be tried in Spine Dev without rebuilding it
# elsewhere. Then it goes back to myapp-main by itself; what was written meanwhile is dropped.
#
# Usage: scripts/ios/test-backend.sh <command>
#   status          print TEST_API and what it serves; exits 1 if it doesn't answer
#   api             print the address Spine Dev builds use (Tailscale when set up)
#   use [hours]     serve this checkout's backend there for [hours] (default 4, at most 12)
#   release [--any] go back to myapp-main now (only from the checkout being served, unless --any)
#   update          move it to origin/myapp-main, after a ship
#   install [ref]   create its checkout (at origin/myapp-main, or ref) and launchd job; once
#   uninstall       stop it and remove the launchd job; the checkout and database stay
#   serve           what launchd runs; not for direct use
set -euo pipefail

port=8010
tailnet_port=8445
label=com.armaan.spine-test-backend
plist="$HOME/Library/LaunchAgents/$label.plist"
log="$HOME/Library/Logs/spine-test-backend.log"
state_dir="$HOME/Library/Application Support/spine-test-backend"
state="$state_dir/serving"
here=$(cd "$(dirname "$0")" && pwd)
main_root=$(cd "$(git rev-parse --path-format=absolute --git-common-dir)/.." && pwd)
test_checkout="$main_root/.claude/test-backend"
test_db="$test_checkout/src/db/db.sqlite3"
host="$(scutil --get LocalHostName).local"
api="http://$host:$port"
domain="gui/$(id -u)"

die() { echo "STOPPED: $*" >&2; exit 1; }
answers() { [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:$port/api/v1/health/")" = 200 ]; }

# The Tailscale CLI: on PATH, or inside the Mac app.
tailscale_cli() {
  if command -v tailscale >/dev/null 2>&1; then echo tailscale
  elif [ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]; then echo /Applications/Tailscale.app/Contents/MacOS/Tailscale
  fi
}

# The address Spine Dev uses: this Mac's Tailscale name when Tailscale serves the test backend there,
# else the local-network address.
spine_dev_api() {
  local ts name
  ts=$(tailscale_cli)
  if [ -n "$ts" ] && "$ts" serve status 2>/dev/null | grep -q ":$tailnet_port (tailnet only)"; then
    name=$("$ts" status --json 2>/dev/null | python3 -I -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)
    if [ -n "$name" ]; then echo "https://$name:$tailnet_port"; return; fi
  fi
  echo "$api"
}

# The checkout whose server listens on the port, from the listening process's working directory.
served_checkout() {
  local pid cwd
  for pid in $(lsof -nP -t -iTCP:"$port" -sTCP:LISTEN 2>/dev/null); do
    cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    case "$cwd" in */src) echo "${cwd%/src}"; return ;; esac
  done
}

# Wait until the server answers from the given checkout.
wait_for() {
  for _ in $(seq 1 90); do
    if answers && [ "$(served_checkout)" = "$1" ]; then return 0; fi
    sleep 2
  done
  die "it didn't come up serving $1 within 3 minutes; see $log"
}

restart() { launchctl kickstart -k "$domain/$label" >/dev/null; }
installed() { [ -f "$plist" ] || die "the test backend isn't installed. Run: scripts/ios/test-backend.sh install"; }

status() {
  local served until=""
  answers || die "the test backend at $api isn't answering. Check: launchctl print $domain/$label, and $log"
  served=$(served_checkout)
  echo "TEST_API: $(spine_dev_api)"
  echo "SERVED_CHECKOUT: $served"
  if [ "$served" = "$test_checkout" ]; then
    echo "SERVING: myapp-main ($(git -C "$test_checkout" log -1 --format='%h %s'))"
  else
    [ -f "$state" ] && read -r _ until < "$state"
    echo "SERVING: $(git -C "$served" symbolic-ref --quiet --short HEAD 2>/dev/null || echo detached) from $served${until:+, until $(date -r "$until" '+%a %H:%M')}"
  fi
}

serve() {
  local checkout=$test_checkout path="" until=0 pid
  if [ -f "$log" ] && [ "$(stat -f %z "$log")" -gt 52428800 ]; then : > "$log"; fi
  if [ -f "$state" ]; then
    read -r path until < "$state" || true
    if [ -d "$path/src" ] && [ "${until:-0}" -gt "$(date +%s)" ]; then checkout=$path; else rm -f "$state"; until=0; fi
  fi
  echo "$(date '+%F %T') serving $checkout"
  cd "$checkout"
  if [ "$until" -eq 0 ]; then
    exec "$here/local-backend.sh" --port "$port"
  fi
  "$here/local-backend.sh" --port "$port" &
  pid=$!
  trap 'kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; exit 0' TERM INT
  while kill -0 "$pid" 2>/dev/null && [ "$(date +%s)" -lt "$until" ]; do sleep 20 & wait $! || true; done
  if kill -0 "$pid" 2>/dev/null; then
    echo "$(date '+%F %T') time's up for $checkout; going back to myapp-main"
    kill "$pid"; wait "$pid" 2>/dev/null || true
  else
    echo "$(date '+%F %T') the backend for $checkout stopped; going back to myapp-main"
  fi
  rm -f "$state"
  exit 0 # launchd starts it again, serving myapp-main
}

use() {
  local hours=${1:-4} top own
  case "$hours" in ''|*[!0-9]*) die "hours must be a whole number" ;; esac
  [ "$hours" -ge 1 ] && [ "$hours" -le 12 ] || die "hours must be between 1 and 12"
  installed
  top=$(git rev-parse --show-toplevel)
  [ "$top" != "$main_root" ] || die "the main checkout's database holds personal data. Run this from a feature worktree."
  [ "$top" != "$test_checkout" ] || die "that's the test backend's own checkout"
  own=$("$here/local-backend.sh" --status 2>/dev/null | sed -n 's/^LOCAL_API: //p' || true)
  if [ -n "$own" ] && [ "${own##*:}" != "$port" ]; then
    die "this checkout's own backend is running at $own. Stop it first; this replaces its database."
  fi
  [ -f "$test_db" ] || die "the test database $test_db doesn't exist yet"
  # A fresh copy of the test database, so test accounts and their sign-ins carry over.
  mkdir -p "$top/src/db"
  rm -f "$top/src/db/db.sqlite3" "$top/src/db/db.sqlite3-wal" "$top/src/db/db.sqlite3-shm"
  sqlite3 "$test_db" ".backup '$top/src/db/db.sqlite3'"
  mkdir -p "$state_dir"
  printf '%s %s\n' "$top" "$(( $(date +%s) + hours * 3600 ))" > "$state"
  restart
  echo "Starting this checkout's backend on the test backend's address (migrations included)..."
  wait_for "$top"
  status
}

release() {
  local path=""
  installed
  [ -f "$state" ] && read -r path _ < "$state"
  if [ -z "$path" ]; then echo "It already serves myapp-main."; status; return; fi
  if [ "${1:-}" != --any ] && [ "$path" != "$(git rev-parse --show-toplevel)" ]; then
    die "it serves $path, not this checkout. Use 'release --any' to end that anyway."
  fi
  rm -f "$state"
  restart
  wait_for "$test_checkout"
  status
}

update() {
  installed
  git -C "$test_checkout" fetch --quiet origin
  git -C "$test_checkout" checkout --quiet --detach origin/myapp-main
  if [ -f "$state" ]; then
    echo "Moved to $(git -C "$test_checkout" log -1 --format='%h %s'). A branch is being served right now;"
    echo "the new myapp-main takes over when that ends."
    status
    return
  fi
  restart
  wait_for "$test_checkout"
  status
}

install() {
  local ref=${1:-origin/myapp-main}
  if [ ! -d "$test_checkout" ]; then
    git -C "$main_root" fetch --quiet origin
    git -C "$main_root" worktree add --quiet --detach "$test_checkout" "$ref"
  fi
  mkdir -p "$(dirname "$plist")" "$(dirname "$log")" "$state_dir"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$label</string>
	<key>ProgramArguments</key>
	<array>
		<string>/bin/bash</string>
		<string>$test_checkout/scripts/ios/test-backend.sh</string>
		<string>serve</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$test_checkout</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>15</integer>
	<key>Nice</key>
	<integer>10</integer>
	<key>StandardOutPath</key>
	<string>$log</string>
	<key>StandardErrorPath</key>
	<string>$log</string>
</dict>
</plist>
EOF
  launchctl bootout "$domain/$label" 2>/dev/null || true
  launchctl bootstrap "$domain" "$plist"
  local ts
  ts=$(tailscale_cli)
  if [ -n "$ts" ]; then
    "$ts" serve --bg --https="$tailnet_port" "http://127.0.0.1:$port" >/dev/null || echo "Tailscale didn't take the $tailnet_port entry; Spine Dev will need the Mac's Wi-Fi."
  fi
  echo "Installed; starting (the first start installs its Python packages)..."
  wait_for "$test_checkout"
  status
}

uninstall() {
  launchctl bootout "$domain/$label" 2>/dev/null || true
  rm -f "$plist" "$state"
  echo "Stopped and removed the launchd job. The checkout and its database stay at $test_checkout."
}

case "${1:-}" in
  status) status ;;
  api) spine_dev_api ;;
  use) use "${2:-}" ;;
  release) release "${2:-}" ;;
  update) update ;;
  install) install "${2:-}" ;;
  uninstall) uninstall ;;
  serve) serve ;;
  -h|--help|"") sed -n '2,20p' "$0" ;;
  *) echo "Unknown command: $1" >&2; exit 2 ;;
esac
