#!/usr/bin/env bash
# Post-deploy check: does nginx see the visitor's real IP, or the Docker gateway?
#
# nginx.conf honours CF-Connecting-IP only when the TCP peer is inside 172.16.0.0/12
# (set_real_ip_from). If the peer lands elsewhere (Docker gave the compose network a
# 192.168.x.x subnet, Docker Desktop changed how loopback-published ports are proxied)
# or Cloudflare strips the header, nginx quietly falls back to the peer address. Every
# visitor then shares one DRF throttle key (auth, refresh, anon, search) and one allauth
# per-IP bucket, and nothing else notices. Run this once the stack is healthy.
#
# It sends two requests, each with a unique query string, and reads the matching nginx
# access-log line back from the app container:
#   A. Host loopback -> 127.0.0.1:8000 carrying CF-Connecting-IP: 203.0.113.250 (a
#      documentation address). The line must start with that address. This uses
#      /api/v1/health/ because /health/ has access_log off.
#   B. The public URL, through Cloudflare. The line must start with an address that
#      differs from its peer=, i.e. Cloudflare delivered the header and nginx used it.
#      The visitor address is never printed (CI logs can be public).
#
# Usage: scripts/verify-real-client-ip.sh [app-container]    (default: spine)
# Exit:  0 ok (or skipped, or warn mode), 1 real-IP trust is broken, 2 could not check.
# Env:   SPINE_REAL_IP_CHECK=fail|warn|skip   default fail; warn reports but exits 0
#        SPINE_LOOPBACK_URL, SPINE_PUBLIC_HEALTH_URL   override the URLs below
#
# See docs/production-networking.md.
set -euo pipefail

container="${1:-${SPINE_APP_CONTAINER:-spine}}"
name="$container" # what messages call it; the deploy scripts pass a container id
mode="${SPINE_REAL_IP_CHECK:-fail}"
loopback_url="${SPINE_LOOPBACK_URL:-http://127.0.0.1:8000/api/v1/health/}"
public_url="${SPINE_PUBLIC_HEALTH_URL:-https://api.spine-api.com/api/v1/health/}"
spoof_ip="203.0.113.250"

case "$mode" in
  fail | warn) ;;
  skip)
    echo "Real-client-IP check skipped (SPINE_REAL_IP_CHECK=skip)."
    exit 0
    ;;
  *)
    echo "SPINE_REAL_IP_CHECK must be fail, warn or skip (got: $mode)" >&2
    exit 2
    ;;
esac

new_token() {
  printf 'realip-%s-%s-%s' "$(date +%s)" "$$" "$RANDOM"
}

with_token() { # url token -> url with ?realip_check=token (or &, if it already has a query)
  local sep="?"
  case "$1" in *\?*) sep="&" ;; esac
  printf '%s%srealip_check=%s' "$1" "$sep" "$2"
}

# The nginx access-log line for the request tagged $1; waits a little for it to appear.
# gunicorn logs the same request too (as 127.0.0.1); only nginx's format has " peer=".
log_line_for() {
  local token="$1" tries=20 line=""
  while [[ "$tries" -gt 0 ]]; do
    line="$(docker logs --since 10m --tail 5000 "$container" 2>&1 | grep -F "realip_check=$token" | grep -F " peer=" | tail -n 1 || true)"
    if [[ -n "$line" ]]; then
      printf '%s\n' "$line"
      return 0
    fi
    tries=$((tries - 1))
    sleep 1
  done
  return 1
}

peer_of() { # access-log line -> value of its peer= field
  local re='peer=([^[:space:]]+)'
  if [[ "$1" =~ $re ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
}

finish() { # exit code to use when the mode is "fail", one-line summary for the CI annotation
  local rc="$1" summary="$2" level="error"
  if [[ "$mode" == "warn" ]]; then
    level="warning"
  fi
  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    echo "::$level title=Real client IP check::$summary See the log above and docs/production-networking.md."
  fi
  if [[ "$mode" == "warn" ]]; then
    echo "SPINE_REAL_IP_CHECK=warn: reported above, not failing the deploy." >&2
    exit 0
  fi
  echo "Re-run alone: bash scripts/verify-real-client-ip.sh $name   (SPINE_REAL_IP_CHECK=warn or skip continues past it)" >&2
  exit "$rc"
}

if ! docker inspect "$container" >/dev/null 2>&1; then
  echo "Cannot run the real-client-IP check: no container '$container'. Pass the app container as the first argument or set SPINE_APP_CONTAINER." >&2
  finish 2 "The real-client-IP check could not run (no such container)."
fi
name="$(docker inspect --format '{{.Name}}' "$container" 2>/dev/null || true)"
name="${name#/}"
name="${name:-$container}"

a_status="ok"
a_detail=""
line_a=""
client_a=""
peer_a=""
token_a="$(new_token)"
if ! curl_err="$(curl --silent --show-error --fail --max-time 10 --output /dev/null \
  -H "CF-Connecting-IP: $spoof_ip" "$(with_token "$loopback_url" "$token_a")" 2>&1)"; then
  a_status="error"
  a_detail="the request to $loopback_url failed: ${curl_err##*$'\n'}"
elif ! line_a="$(log_line_for "$token_a")"; then
  a_status="error"
  a_detail="no access-log line for the request showed up in 'docker logs $name' within 20 s"
else
  client_a="${line_a%% *}"
  peer_a="$(peer_of "$line_a")"
  if [[ "$client_a" != "$spoof_ip" ]]; then
    a_status="untrusted"
  fi
fi

b_status="ok"
b_detail=""
line_b=""
client_b=""
peer_b=""
token_b="$(new_token)"
if ! curl_err="$(curl --silent --show-error --fail --max-time 20 --retry 5 --retry-delay 3 --retry-all-errors \
  --output /dev/null "$(with_token "$public_url" "$token_b")" 2>&1)"; then
  b_status="error"
  b_detail="the request to $public_url failed: ${curl_err##*$'\n'}"
elif ! line_b="$(log_line_for "$token_b")"; then
  b_status="error"
  b_detail="no access-log line for the public request showed up in 'docker logs $name' within 20 s"
else
  client_b="${line_b%% *}"
  peer_b="$(peer_of "$line_b")"
  if [[ -z "$peer_b" || "$client_b" == "$peer_b" ]]; then
    b_status="stripped"
  fi
fi

if [[ "$a_status" == "ok" && "$b_status" == "ok" ]]; then
  echo "Real client IP check OK ($name)."
  echo "  loopback: CF-Connecting-IP $spoof_ip was logged as the client (peer $peer_a)."
  echo "  public:   the client differs from peer $peer_b, so the visitor address reached nginx (not printed)."
  exit 0
fi

failed=0
errored=0
if [[ "$a_status" == "untrusted" || "$b_status" == "stripped" ]]; then
  failed=1
fi
if [[ "$a_status" == "error" || "$b_status" == "error" ]]; then
  errored=1
fi

{
  echo
  echo "=============================================================================="
  if [[ "$failed" -eq 1 ]]; then
    echo "REAL CLIENT IP CHECK FAILED ($name)"
    echo "The stack is up and serving; nothing was rolled back. But nginx is not using the"
    echo "visitor's address, so ALL visitors share one DRF throttle key (auth, refresh,"
    echo "anon, search) and one allauth per-IP bucket until this is fixed."
  else
    echo "REAL CLIENT IP CHECK COULD NOT RUN ($name)"
    echo "The stack is up and serving, but whether nginx sees visitor addresses is unverified."
  fi
  echo "=============================================================================="
} >&2

exit_code=1
if [[ "$failed" -eq 0 ]]; then
  exit_code=2
fi

if [[ "$errored" -eq 1 ]]; then
  echo "Could not complete part of the check:" >&2
  if [[ "$a_status" == "error" ]]; then
    echo "  - loopback: $a_detail" >&2
  fi
  if [[ "$b_status" == "error" ]]; then
    echo "  - public:   $b_detail" >&2
  fi
fi

if [[ "$a_status" == "untrusted" ]]; then
  echo "- Loopback request sent CF-Connecting-IP: $spoof_ip, but nginx logged the client as $client_a (TCP peer $peer_a)." >&2
  echo "  $line_a" >&2
  net="$(docker inspect --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$container" 2>/dev/null || true)"
  net="${net%% *}"
  subnet=""
  if [[ -n "$net" ]]; then
    subnet="$(docker network inspect --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$net" 2>/dev/null || true)"
    subnet="${subnet% }"
  fi
  case "$peer_a" in
    172.1[6-9].* | 172.2[0-9].* | 172.3[01].*)
      echo "  The peer is inside set_real_ip_from 172.16.0.0/12, so the running nginx is not using the deployed nginx.conf." >&2
      echo "  Check: docker exec $name nginx -T | grep -n real_ip" >&2
      ;;
    192.168.65.*)
      echo "  192.168.65.x is what Docker Desktop shows for ports published on non-loopback interfaces, and nginx.conf" >&2
      echo "  deliberately does not trust it. Check that docker-compose.production.yml publishes \"127.0.0.1:8000:8000\"" >&2
      echo "  (docker port $name should show 127.0.0.1:8000), then redeploy." >&2
      ;;
    *)
      echo "  The peer is outside set_real_ip_from (172.16.0.0/12). Docker most likely gave the compose network a" >&2
      echo "  subnet from its 192.168.0.0/16 default pool, or default-address-pools was changed." >&2
      echo "  The app container's network is ${net:-unknown}, subnet ${subnet:-unknown} (docker network inspect ${net:-<network>})." >&2
      echo "  Add that subnet as another set_real_ip_from line in nginx.conf, only while the port stays bound to" >&2
      echo "  127.0.0.1, and redeploy." >&2
      ;;
  esac
fi

if [[ "$a_status" == "ok" && "$b_status" == "stripped" ]]; then
  echo "- The loopback check passed, so nginx's trust is fine, but the request through Cloudflare was logged with the" >&2
  echo "  client equal to its peer ($peer_b): CF-Connecting-IP did not reach nginx on tunnel traffic." >&2
  echo "  $line_b" >&2
  echo "  Check the Cloudflare dashboard: Rules > Managed Transforms > \"Remove visitor IP headers\" must be OFF, and no" >&2
  echo "  Transform or WAF rule may remove CF-Connecting-IP. Also confirm this hostname is served by the Cloudflare tunnel." >&2
elif [[ "$b_status" == "stripped" ]]; then
  echo "- The public request was also logged with the client equal to its peer ($peer_b)." >&2
fi

if [[ "$failed" -eq 1 ]]; then
  finish "$exit_code" "nginx is not seeing real visitor IPs, so visitors share throttle buckets."
else
  finish "$exit_code" "The real-client-IP check could not complete, so the real-IP trust is unverified."
fi
