#!/usr/bin/env bash
# =============================================================================
# restore-check.sh - prove that the newest backup can really be restored
# =============================================================================
#
# A backup you have never restored is only a hope. This script restores a
# database dump into a SEPARATE, DISPOSABLE Postgres container (same image as
# production, no network, no access to any production volume), runs a few sanity
# queries against it, and then throws the container away. Production cannot be
# touched by construction: the restore happens somewhere else. The only thing
# asked of the production database is one read-only user count, printed for
# comparison.
#
# Usage:
#   restore-check.sh                   newest dump in <BACKUP_ROOT>/nightly
#   restore-check.sh /path/to.dump     a specific dump (for example one of the
#                                      pre-deploy-*.dump files from deployments)
#   restore-check.sh --offsite         first download the newest dump and media
#                                      archive from Cloudflare R2, then check those
#
# It checks:
#   - the dump is recent (default: newer than 36 hours) - proves the nightly job
#     is really running                                (not for a specific dump)
#   - pg_restore loads the whole dump without a single error
#   - the restored database has tables, a users table, and at least MIN_USERS users
#   - the newest media archive is a readable .tar.gz
#
# The disposable container stops itself after RESTORE_CONTAINER_MAX_SECONDS even
# if this script is killed without a chance to clean up (its data volume goes
# with it), so nothing can be left running in the production Docker volumes. It
# also runs with a memory, CPU and process limit (RESTORE_CONTAINER_MEMORY, _CPUS,
# _PIDS), and is refused a start unless Docker has enough free disk space for the
# restored copy (Docker Desktop's disk is shared with production).
#
# Stopping it: Ctrl-C in its window stops it at once. `kill <pid>` from another
# window is handled at the end of the step that is running (a restore can take
# minutes), and `kill -9` leaves the container to stop itself after
# RESTORE_CONTAINER_MAX_SECONDS. Whatever happens, the container goes.
#
# Exit status: 0 = every check passed, 1 = a check failed, 2 = bad usage or
# configuration, 129/130/143 = stopped by SIGHUP/SIGINT/SIGTERM.
#
# Uses the same config file as backup-production.sh (~/.config/spine/backup.env,
# override with SPINE_BACKUP_CONFIG). Restoring needs free disk space inside
# Docker of several times the size of the dump, for a few seconds to a few minutes.

set -euo pipefail
umask 077
# A closed terminal or `restore-check.sh | head` must not kill the script before
# it has removed its disposable container: writes to a dead output just fail,
# and logging ignores that failure. (Two independent layers: SIGPIPE is ignored
# here, and every log write happens in a subshell, see log().)
trap '' PIPE

if [[ -z "${HOME:-}" ]]; then
  HOME="$(cd ~ && pwd)"
fi
export HOME

set_path() {
  PATH="${EXTRA_PATH:+$EXTRA_PATH:}/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  export PATH
}
set_path

# macOS's $TMPDIR ends in a slash; normalise it so paths do not contain "//".
TMP_BASE="${TMPDIR:-/tmp}"
TMP_BASE="${TMP_BASE%/}"

usage() {
  cat <<'EOF'
Usage: restore-check.sh [--offsite | /path/to/file.dump]

Restores a database dump into a separate, disposable Postgres container (no
network, no production volumes), runs sanity queries, then removes the container.

  (no arguments)     check the newest dump in the nightly backup folder
  /path/to/file.dump check that specific dump
  --offsite          download the newest dump + media archive from R2 and check them

Config file: ~/.config/spine/backup.env   (override with SPINE_BACKUP_CONFIG)
Docs       : docs/backups.md
EOF
}

MODE=local
DUMP_ARG=""
for arg in "$@"; do
  case "$arg" in
    -h | --help)
      usage
      exit 0
      ;;
    --offsite) MODE=offsite ;;
    -*)
      echo "Unknown option: $arg" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -n "$DUMP_ARG" ]]; then
        echo "Only one dump file can be given." >&2
        usage >&2
        exit 2
      fi
      DUMP_ARG="$arg"
      ;;
  esac
done
if [[ "$MODE" == offsite && -n "$DUMP_ARG" ]]; then
  echo "--offsite and a dump file cannot be combined." >&2
  exit 2
fi

# -----------------------------------------------------------------------------
# Configuration: defaults < config file < environment variables
# -----------------------------------------------------------------------------
CONFIG_FILE="${SPINE_BACKUP_CONFIG:-$HOME/.config/spine/backup.env}"
CONFIG_KEYS="BACKUP_ROOT NIGHTLY_SUBDIR DB_CONTAINER DB_NAME DB_USER \
RCLONE_REMOTE R2_BUCKET R2_PREFIX HEALTHCHECK_URL DOCKER_WAIT_SECONDS DOCKER_CMD_TIMEOUT \
STEP_TIMEOUT_SECONDS MIN_USERS RESTORE_CHECK_MAX_AGE_HOURS RESTORE_CHECK_IMAGE \
RESTORE_CONTAINER_MAX_SECONDS RESTORE_CONTAINER_MEMORY RESTORE_CONTAINER_CPUS RESTORE_CONTAINER_PIDS \
RESTORE_MIN_FREE_MB EXTRA_PATH"

CONFIG_WARN=""
load_config() {
  local key flag saved mode
  # shellcheck disable=SC2086 # CONFIG_KEYS is a deliberate space-separated list
  for key in $CONFIG_KEYS; do
    if [[ -n "${!key+x}" ]]; then
      printf -v "_saved_$key" '%s' "${!key}"
      printf -v "_has_$key" '%s' 1
    fi
  done
  CONFIG_SOURCE="built-in defaults (no config file at $CONFIG_FILE)"
  if [[ -e "$CONFIG_FILE" ]]; then
    if [[ ! -f "$CONFIG_FILE" || ! -r "$CONFIG_FILE" ]]; then
      echo "Config file exists but is not a readable file: $CONFIG_FILE" >&2
      exit 2
    fi
    # shellcheck source=/dev/null
    . "$CONFIG_FILE"
    CONFIG_SOURCE="$CONFIG_FILE"
    mode="$(stat -f '%Lp' "$CONFIG_FILE" 2>/dev/null || stat -c '%a' "$CONFIG_FILE" 2>/dev/null || true)"
    if [[ "$mode" =~ ^[0-7]{3,4}$ ]] && (((8#$mode & 8#077) != 0)); then
      CONFIG_WARN="config file $CONFIG_FILE is readable by other users (mode $mode) - run: chmod 600 \"$CONFIG_FILE\""
    fi
  fi
  # shellcheck disable=SC2086
  for key in $CONFIG_KEYS; do
    flag="_has_$key"
    if [[ "${!flag:-}" == 1 ]]; then
      saved="_saved_$key"
      printf -v "$key" '%s' "${!saved}"
    fi
  done
}
load_config

: "${BACKUP_ROOT:=$HOME/projects/spine-backups}"
: "${NIGHTLY_SUBDIR:=nightly}"
: "${DB_CONTAINER:=spine-db}"
: "${DB_NAME:=spine}"
: "${DB_USER:=spine}"
: "${RCLONE_REMOTE:=}"
: "${R2_BUCKET:=}"
: "${R2_PREFIX:=nightly}"
: "${HEALTHCHECK_URL:=}"
: "${DOCKER_WAIT_SECONDS:=60}"
: "${DOCKER_CMD_TIMEOUT:=30}"
: "${STEP_TIMEOUT_SECONDS:=3600}"
: "${MIN_USERS:=1}"
: "${RESTORE_CHECK_MAX_AGE_HOURS:=36}"
: "${RESTORE_CHECK_IMAGE:=}"
: "${RESTORE_CONTAINER_MAX_SECONDS:=4200}"
: "${RESTORE_CONTAINER_MEMORY:=2g}"
: "${RESTORE_CONTAINER_CPUS:=2}"
: "${RESTORE_CONTAINER_PIDS:=256}"
: "${RESTORE_MIN_FREE_MB:=2048}"
: "${EXTRA_PATH:=}"

# Every setting is checked before anything runs. Numbers are plain whole numbers
# WITHOUT leading zeros: bash reads "08" as a broken octal number (and "010" as
# 8), and a long one would wrap around in bash's 64-bit arithmetic.
check_setting() { # check_setting NAME REGEX DESCRIPTION
  local value="${!1}" re="$2"
  if [[ ! "$value" =~ $re ]]; then
    echo "config: $1 must be $3 (got '$value')" >&2
    exit 2
  fi
}
for _setting in DOCKER_WAIT_SECONDS RESTORE_CHECK_MAX_AGE_HOURS MIN_USERS RESTORE_MIN_FREE_MB; do
  check_setting "$_setting" '^(0|[1-9][0-9]{0,8})$' "a whole number without leading zeros"
done
for _setting in DOCKER_CMD_TIMEOUT STEP_TIMEOUT_SECONDS RESTORE_CONTAINER_MAX_SECONDS; do
  check_setting "$_setting" '^[1-9][0-9]{0,6}$' "a whole number of seconds, at least 1, without leading zeros"
done
check_setting RESTORE_CONTAINER_MEMORY '^[1-9][0-9]{0,5}[mMgG]$' "an amount like 512m or 2g"
check_setting RESTORE_CONTAINER_CPUS '^(0\.[1-9][0-9]?|0\.0[1-9]|[1-9][0-9]?(\.[0-9]{1,2})?)$' "a number of CPUs like 2 or 0.5"
check_setting RESTORE_CONTAINER_PIDS '^([3-9][0-9]|[1-9][0-9]{2,5})$' "a process limit of at least 30"
check_setting NIGHTLY_SUBDIR '^[A-Za-z0-9_][A-Za-z0-9._-]*$' "a plain folder name (letters, digits, . _ - and no slash)"

BACKUP_ROOT="${BACKUP_ROOT/#\~/$HOME}"
BACKUP_ROOT="${BACKUP_ROOT%/}"
RCLONE_REMOTE="${RCLONE_REMOTE%:}"
R2_BUCKET="${R2_BUCKET#/}"
R2_BUCKET="${R2_BUCKET%/}"
R2_PREFIX="${R2_PREFIX#/}"
R2_PREFIX="${R2_PREFIX%/}"
set_path

NIGHTLY_DIR="$BACKUP_ROOT/$NIGHTLY_SUBDIR"
DB_FILE_RE='^spine-db-[0-9]{8}T[0-9]{6}Z\.dump$'
MEDIA_FILE_RE='^spine-media-[0-9]{8}T[0-9]{6}Z\.tar\.gz$'

# -----------------------------------------------------------------------------
# Logging (same look as backup-production.sh). Secrets are scrubbed from any
# captured tool output before it is printed; the URL goes to awk through the
# environment, never on its command line.
# -----------------------------------------------------------------------------
# A failed write (closed terminal, `| head`) is ignored: logging must never stop the script.
# The write happens in a SUBSHELL on purpose: when a write fails (the reader is gone),
# bash keeps the unwritten bytes in its stdout buffer, and every later `$(...)` would
# then flush that stale text into its own captured output (a path from mktemp, a
# number from date ...). The subshell's copy of the buffer dies with the subshell.
log() { (printf '%s %-5s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$1" "$2") 2>/dev/null || true; }
info() { log INFO "$*"; }
warn() { log WARN "$*"; }
err() { log ERROR "$*"; }
pass() { log PASS "$*"; }
STATUS=0
fail() {
  log FAIL "$*"
  STATUS=1
}
die() {
  err "$*"
  STATUS=1
  exit 1
}

scrub_stream() {
  HC_SCRUB_URL="$HEALTHCHECK_URL" awk '{
    hc = ENVIRON["HC_SCRUB_URL"]
    if (hc != "") {
      while ((i = index($0, hc)) > 0) {
        $0 = substr($0, 1, i - 1) "<healthchecks-url>" substr($0, i + length(hc))
      }
    }
    gsub(/[A-Za-z0-9.-]+\.r2\.cloudflarestorage\.com/, "<r2-host>")
    gsub(/X-Amz-(Signature|Credential|Security-Token)=[^& \t"]*/, "<redacted-aws-param>")
    print
  }'
}

show_lines() { # show_lines FILE [LEVEL] [MAX]
  local file="$1" level="${2:-ERROR}" max="${3:-15}" line
  if [[ -s "$file" ]]; then
    head -n "$max" "$file" | scrub_stream | while IFS= read -r line; do
      log "$level" "  | $line"
    done
  fi
  return 0
}

# rclone starts every line with its own timestamp and level; ours already carries both.
strip_rclone_prefix() {
  sed -E 's#^[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9:]{8} [A-Z]+ *: ##' "$1" >"$1.norm" && mv "$1.norm" "$1"
}

human_size() {
  local bytes
  bytes="$(wc -c <"$1" | tr -d ' ')"
  awk -v b="$bytes" 'BEGIN {
    split("B KB MB GB TB", u, " "); i = 1
    while (b >= 1024 && i < 5) { b /= 1024; i++ }
    if (i == 1) printf "%d %s", b, u[i]; else printf "%.1f %s", b, u[i]
  }'
}

# newest_matching DIR GLOB REGEX -> path of the newest matching file (or nothing).
# Names embed a UTC timestamp and globs come back sorted, so the last match wins.
newest_matching() {
  local dir="$1" glob="$2" re="$3" f name best=""
  # shellcheck disable=SC2086 # the glob must expand
  for f in "$dir"/$glob; do
    [[ -f "$f" ]] || continue
    name="${f##*/}"
    [[ "$name" =~ $re ]] || continue
    best="$f"
  done
  if [[ -n "$best" ]]; then
    printf '%s\n' "$best"
  fi
  return 0
}

# 20260930T073001Z -> epoch seconds (BSD date on macOS, GNU date as a fallback)
stamp_to_epoch() {
  local s="$1"
  date -j -u -f '%Y%m%dT%H%M%SZ' "$s" '+%s' 2>/dev/null ||
    date -u -d "${s:0:4}-${s:4:2}-${s:6:2} ${s:9:2}:${s:11:2}:${s:13:2}" '+%s' 2>/dev/null ||
    true
}

# age_hours FILE -> whole hours since the timestamp embedded in its name (or "")
age_hours() {
  local n="${1##*/}" epoch now
  n="${n#spine-db-}"
  n="${n#spine-media-}"
  n="${n%%.*}"
  epoch="$(stamp_to_epoch "$n")"
  if [[ -z "$epoch" ]]; then
    return 0
  fi
  now="$(date -u +%s)"
  printf '%d' $(((now - epoch) / 3600))
}

# -----------------------------------------------------------------------------
# Time limits (identical to backup-production.sh). `docker` and `rclone` are Go
# programs that ignore SIGALRM, so the classic `perl alarm` trick cannot stop
# them: this wrapper forks, sends TERM when the time is up, and KILLs 3 seconds
# later. It also stops the command if this script is killed outright (SIGKILL).
# Exit status: the command's own; 124 when it ran too long; 125 when it was
# stopped because this script is gone.
# -----------------------------------------------------------------------------
HAVE_PERL=0
if command -v perl >/dev/null 2>&1; then
  HAVE_PERL=1
fi

run_timeout() { # run_timeout SECONDS COMMAND [ARGS...]
  local secs="$1"
  shift
  if ((HAVE_PERL == 0)); then
    "$@"
    return $?
  fi
  SPINE_SUPERVISOR_PID="$$" perl -e '
    use strict; use warnings; no warnings "exec"; use POSIX ":sys_wait_h";
    my ($secs, $sup) = (shift @ARGV, $ENV{SPINE_SUPERVISOR_PID} || 0);
    my $pid = fork();
    die "fork failed: $!" unless defined $pid;
    if (!$pid) { exec { $ARGV[0] } @ARGV; print STDERR "cannot run $ARGV[0]: $!\n"; exit 127; }
    my ($why, $kill_at, $ticks, $t0) = (0, 0, 0, time);
    my $gone = sub {
      return 1 unless kill(0, $sup) || $!{EPERM};
      return 0 if ++$ticks % 5;
      my $st = `ps -o stat= -p $sup 2>/dev/null`;
      return (defined $st && $st =~ /^\s*Z/) ? 1 : 0;
    };
    my $stop = sub { kill "TERM", $pid; $kill_at = time + 3; };
    $SIG{ALRM} = sub {
      my $now = time;
      if ($kill_at) { kill "KILL", $pid if $now >= $kill_at; }
      elsif ($now - $t0 >= $secs) { $why = 124; $stop->(); }
      elsif ($sup && $gone->()) { $why = 125; $stop->(); }
      alarm 1;
    };
    $SIG{TERM} = $SIG{INT} = $SIG{HUP} = sub { $stop->() unless $kill_at; };
    alarm 1;
    my $r;
    do { $r = waitpid($pid, 0); } while ($r == -1 && !$!{ECHILD});
    alarm 0;
    exit 1 if $r == -1;
    exit $why if $why;
    exit(WIFEXITED($?) ? WEXITSTATUS($?) : 128 + WTERMSIG($?));
  ' "$secs" "$@"
}

dq() { run_timeout "$DOCKER_CMD_TIMEOUT" docker "$@"; } # quick docker calls

# -----------------------------------------------------------------------------
# Cleanup: always remove the disposable container (and with it its data volume),
# whatever happened, and turn the final outcome (including unexpected errors)
# into the exit status.
# -----------------------------------------------------------------------------
CNAME=""
WORK_DIR=""
ERR_FILE=""

remove_container() {
  local exists
  if [[ -n "$CNAME" ]]; then
    # It may never have been created, or may already have removed itself (--rm).
    exists="$(run_timeout 30 docker ps -a -q --filter "name=^${CNAME}\$" 2>/dev/null || true)"
    if [[ -z "$exists" ]]; then
      :
    elif run_timeout 60 docker rm -f -v "$CNAME" >/dev/null 2>&1; then
      info "cleanup: disposable container '$CNAME' removed"
    else
      err "cleanup: could not remove '$CNAME' - remove it by hand: docker rm -f -v $CNAME (it also stops by itself after ${RESTORE_CONTAINER_MAX_SECONDS}s)"
      STATUS=1
    fi
    CNAME=""
  fi
}

cleanup() {
  local rc=$?
  if ((rc != 0)); then
    STATUS=$rc
  fi
  remove_container
  if [[ -n "$WORK_DIR" && "$WORK_DIR" == */spine-restore-check.* ]]; then
    rm -rf "$WORK_DIR"
  fi
  if ((STATUS == 0)); then
    info "RESULT: PASS - the backup restores cleanly"
  else
    err "RESULT: FAIL - do not trust this backup until the problem above is fixed"
  fi
  exit "$STATUS"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

# -----------------------------------------------------------------------------
# 1. Which dump (and media archive) are we checking?
# -----------------------------------------------------------------------------
info "=== Spine restore check ==="
info "config: $CONFIG_SOURCE"
if [[ -n "$CONFIG_WARN" ]]; then
  warn "$CONFIG_WARN"
fi
if ((HAVE_PERL == 0)); then
  warn "perl was not found: the time limits on single docker/rclone calls are switched off"
fi

# A run that was killed outright (SIGKILL, power cut) cannot clean up after itself;
# with --offsite its temporary folder may hold a downloaded dump. Sweep old ones.
find "$TMP_BASE" -maxdepth 1 -type d -name 'spine-restore-check.*' -mtime +0 -exec rm -rf {} + 2>/dev/null || true

WORK_DIR="$(mktemp -d "$TMP_BASE/spine-restore-check.XXXXXX")"
ERR_FILE="$WORK_DIR/stderr.txt"
DUMP=""
MEDIA=""
CHECK_AGE=1

case "$MODE" in
  offsite)
    if [[ -z "$RCLONE_REMOTE" || -z "$R2_BUCKET" ]]; then
      die "--offsite needs RCLONE_REMOTE and R2_BUCKET to be set in $CONFIG_FILE"
    fi
    command -v rclone >/dev/null 2>&1 || die "rclone is not installed (run: brew install rclone)"
    dest="${RCLONE_REMOTE}:${R2_BUCKET}${R2_PREFIX:+/$R2_PREFIX}"
    info "off-site: listing $dest"
    rc=0
    listing="$(run_timeout "$STEP_TIMEOUT_SECONDS" rclone lsf "$dest" --files-only 2>"$ERR_FILE")" || rc=$?
    if ((rc != 0)); then
      strip_rclone_prefix "$ERR_FILE"
      show_lines "$ERR_FILE"
      die "off-site: could not list the bucket (rclone exit status $rc)"
    fi
    db_name="$(printf '%s\n' "$listing" | grep -E "$DB_FILE_RE" | sort | tail -n 1 || true)"
    media_name="$(printf '%s\n' "$listing" | grep -E "$MEDIA_FILE_RE" | sort | tail -n 1 || true)"
    [[ -n "$db_name" ]] || die "off-site: no database dump found in $dest"
    info "off-site: downloading $db_name"
    rc=0
    run_timeout "$STEP_TIMEOUT_SECONDS" rclone copyto "$dest/$db_name" "$WORK_DIR/$db_name" 2>"$ERR_FILE" || rc=$?
    if ((rc != 0)); then
      strip_rclone_prefix "$ERR_FILE"
      show_lines "$ERR_FILE"
      die "off-site: download of $db_name failed"
    fi
    DUMP="$WORK_DIR/$db_name"
    if [[ -n "$media_name" ]]; then
      info "off-site: downloading $media_name"
      rc=0
      run_timeout "$STEP_TIMEOUT_SECONDS" rclone copyto "$dest/$media_name" "$WORK_DIR/$media_name" 2>"$ERR_FILE" || rc=$?
      if ((rc != 0)); then
        strip_rclone_prefix "$ERR_FILE"
        show_lines "$ERR_FILE"
        die "off-site: download of $media_name failed"
      fi
      MEDIA="$WORK_DIR/$media_name"
    else
      fail "off-site: no media archive found in $dest"
    fi
    ;;
  local)
    if [[ -n "$DUMP_ARG" ]]; then
      [[ -f "$DUMP_ARG" ]] || die "dump file not found: $DUMP_ARG"
      DUMP="$DUMP_ARG"
      CHECK_AGE=0
    else
      DUMP="$(newest_matching "$NIGHTLY_DIR" 'spine-db-*.dump' "$DB_FILE_RE")"
      [[ -n "$DUMP" ]] || die "no database dump found in $NIGHTLY_DIR - has the nightly backup ever run?"
      MEDIA="$(newest_matching "$NIGHTLY_DIR" 'spine-media-*.tar.gz' "$MEDIA_FILE_RE")"
    fi
    ;;
esac

DUMP_AGE="$(age_hours "$DUMP")"
info "dump: $DUMP ($(human_size "$DUMP")${DUMP_AGE:+, ${DUMP_AGE}h old})"
if ((CHECK_AGE == 1)) && [[ -n "$DUMP_AGE" ]]; then
  if ((DUMP_AGE > RESTORE_CHECK_MAX_AGE_HOURS)); then
    fail "freshness: the newest dump is ${DUMP_AGE}h old (limit ${RESTORE_CHECK_MAX_AGE_HOURS}h) - the nightly backup may have stopped running"
  else
    pass "freshness: newest dump is ${DUMP_AGE}h old (limit ${RESTORE_CHECK_MAX_AGE_HOURS}h)"
  fi
fi

# -----------------------------------------------------------------------------
# 2. Docker, and the image for the disposable container
# -----------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "the docker CLI was not found (PATH is: $PATH)"
deadline=$((SECONDS + DOCKER_WAIT_SECONDS))
until dq info >/dev/null 2>&1; do
  if ((SECONDS >= deadline)); then
    die "the Docker daemon is not reachable - is Docker Desktop running?"
  fi
  nap=5
  if ((deadline - SECONDS < nap)); then
    nap=$((deadline - SECONDS))
  fi
  sleep "$nap"
done

# The same image as production, so the restore is tried on the same Postgres
# version. It is already on this machine, so nothing is ever pulled.
IMAGE="$RESTORE_CHECK_IMAGE"
if [[ -z "$IMAGE" ]]; then
  IMAGE="$(dq inspect -f '{{.Config.Image}}' "$DB_CONTAINER" 2>/dev/null || true)"
fi
if [[ -z "$IMAGE" ]]; then
  IMAGE="postgres:16-alpine"
fi
dq image inspect "$IMAGE" >/dev/null 2>&1 ||
  die "the image '$IMAGE' is not on this machine (set RESTORE_CHECK_IMAGE in $CONFIG_FILE to an installed postgres image)"

leftovers="$(dq ps -a --filter label=com.spine.restore-check=1 --format '{{.Names}}' 2>/dev/null | tr '\n' ' ' || true)"
if [[ -n "${leftovers// /}" ]]; then
  warn "disposable container(s) from an interrupted earlier check: $leftovers"
  warn "  they remove themselves after ${RESTORE_CONTAINER_MAX_SECONDS}s; to remove one now: docker rm -f -v <name>"
fi

# -----------------------------------------------------------------------------
# 3. Start the disposable Postgres and restore into it
# -----------------------------------------------------------------------------
CNAME="restore-check-$(date +%s)-$$"
info "restore: starting a disposable Postgres container '$CNAME' from $IMAGE (no network, no production volumes)"
# --rm removes it (and its anonymous data volume) when it stops. The `timeout`
# wrapper makes it stop by itself even if this script is killed outright. It
# sends SIGINT, not the default SIGTERM: Postgres answers SIGTERM with a "smart"
# shutdown that waits for every open session (a stuck restore never ends), but
# SIGINT with a "fast" one that disconnects them. The memory, CPU and process
# limits keep a runaway restore from starving production, which shares this Docker.
# fsync etc. are off: the data is thrown away, so durability buys nothing.
if ! run_timeout 120 docker run -d --rm --pull=never --network none --name "$CNAME" \
  --label com.spine.restore-check=1 \
  --memory "$RESTORE_CONTAINER_MEMORY" --cpus "$RESTORE_CONTAINER_CPUS" --pids-limit "$RESTORE_CONTAINER_PIDS" \
  -e "POSTGRES_USER=$DB_USER" -e POSTGRES_DB=restorecheck -e POSTGRES_HOST_AUTH_METHOD=trust \
  --entrypoint timeout "$IMAGE" -s INT "$RESTORE_CONTAINER_MAX_SECONDS" docker-entrypoint.sh postgres \
  -c fsync=off -c synchronous_commit=off -c full_page_writes=off >/dev/null 2>"$ERR_FILE"; then
  show_lines "$ERR_FILE"
  die "restore: could not start the disposable container"
fi

# The image first runs a temporary server that listens on the unix socket only,
# then restarts; asking over TCP (-h 127.0.0.1) only succeeds for the real one.
deadline=$((SECONDS + 120))
until dq exec "$CNAME" pg_isready -q -h 127.0.0.1 -U "$DB_USER" -d restorecheck >/dev/null 2>&1; do
  if ((SECONDS >= deadline)); then
    die "restore: the disposable database did not become ready within 120s"
  fi
  sleep 1
done

# Room for the restored copy? Docker Desktop keeps all containers - production's
# included - on one virtual disk, and a restored database is several times the
# size of its compressed dump (tables plus rebuilt indexes). Measured from inside
# the container, on the very disk its data folder lives on. Refuse to start a
# restore that could fill that disk.
dump_bytes="$(wc -c <"$DUMP" | tr -d ' ')"
need_mb=$(((5 * dump_bytes) / 1048576 + 512))
if ((need_mb < RESTORE_MIN_FREE_MB)); then
  need_mb=$RESTORE_MIN_FREE_MB
fi
free_kb="$(dq exec "$CNAME" df -Pk /var/lib/postgresql/data 2>/dev/null | awk 'NR == 2 { print $4 }' || true)"
if [[ ! "$free_kb" =~ ^[0-9]+$ ]]; then
  die "restore: could not measure the free disk space inside Docker"
fi
free_mb=$((free_kb / 1024))
if ((free_mb < need_mb)); then
  die "restore: not enough free disk space inside Docker: ${free_mb} MB free, about ${need_mb} MB needed (5 times the dump size plus 512 MB, at least RESTORE_MIN_FREE_MB). Free some space (see: docker system df) and run the check again; nothing was restored"
fi
info "restore: free disk space inside Docker: ${free_mb} MB (needed: about ${need_mb} MB)"

rc_psql() { # rc_psql SQL -> single value from the disposable database
  dq exec "$CNAME" psql -X -At -v ON_ERROR_STOP=1 -U "$DB_USER" -d restorecheck -c "$1"
}

SECONDS=0
info "restore: loading the dump with pg_restore (stops at the first error)"
rc=0
run_timeout "$STEP_TIMEOUT_SECONDS" docker exec -i "$CNAME" pg_restore -U "$DB_USER" -d restorecheck --no-owner --no-acl --exit-on-error \
  <"$DUMP" >/dev/null 2>"$ERR_FILE" || rc=$?
if ((rc == 0)); then
  pass "restore: the whole dump loaded without errors (${SECONDS}s)"
  RESTORED=1
elif ((rc == 124)); then
  fail "restore: pg_restore timed out after ${STEP_TIMEOUT_SECONDS}s"
  RESTORED=0
else
  fail "restore: pg_restore reported an error - this dump could NOT be restored"
  show_lines "$ERR_FILE"
  RESTORED=0
fi

# -----------------------------------------------------------------------------
# 4. Sanity queries against the restored copy
# -----------------------------------------------------------------------------
if ((RESTORED == 1)); then
  tables="$(rc_psql "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') AND table_type = 'BASE TABLE'")"
  if ((tables >= 1)); then
    pass "tables: $tables tables in the restored database"
  else
    fail "tables: the restored database has no tables"
  fi

  has_users="$(rc_psql "SELECT to_regclass('public.users_user') IS NOT NULL")"
  if [[ "$has_users" == "t" ]]; then
    users="$(rc_psql "SELECT count(*) FROM users_user")"
    # The one thing asked of production: a read-only count, for comparison.
    live_users=""
    if [[ "$(dq inspect -f '{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null || true)" == "true" ]]; then
      live_users="$(dq exec -e 'PGOPTIONS=-c default_transaction_read_only=on' "$DB_CONTAINER" \
        psql -X -At -U "$DB_USER" -d "$DB_NAME" -c "SELECT count(*) FROM users_user" 2>/dev/null || true)"
    fi
    if ((users >= MIN_USERS)); then
      pass "users: $users user(s) in the restored copy (live database right now: ${live_users:-unavailable})"
    else
      fail "users: the restored copy holds $users user(s), expected at least $MIN_USERS - is this really production data?"
    fi
  else
    fail "users: there is no users_user table in the restored database"
  fi

  has_migrations="$(rc_psql "SELECT to_regclass('public.django_migrations') IS NOT NULL")"
  if [[ "$has_migrations" == "t" ]]; then
    n_migrations="$(rc_psql "SELECT count(*) FROM django_migrations")"
    latest_migration="$(rc_psql "SELECT app || '.' || name FROM django_migrations ORDER BY id DESC LIMIT 1")"
    info "migrations: $n_migrations applied (latest: ${latest_migration:-none})"
  else
    warn "migrations: no django_migrations table found (not a Django database?)"
  fi
fi

# -----------------------------------------------------------------------------
# 5. The media archive
# -----------------------------------------------------------------------------
if [[ -n "$DUMP_ARG" ]]; then
  info "media: not checked (a specific dump file was given)"
elif [[ -z "$MEDIA" ]]; then
  if [[ "$MODE" == local ]]; then
    fail "media: no media archive found in $NIGHTLY_DIR"
  fi
else
  MEDIA_AGE="$(age_hours "$MEDIA")"
  if ! gzip -t "$MEDIA" 2>"$ERR_FILE"; then
    fail "media: ${MEDIA##*/} is not a valid gzip file"
    show_lines "$ERR_FILE"
  elif ! entries="$(tar -tzf "$MEDIA" 2>"$ERR_FILE" | wc -l | tr -d ' ')"; then
    fail "media: ${MEDIA##*/} cannot be listed"
    show_lines "$ERR_FILE"
  else
    pass "media: ${MEDIA##*/} is readable ($entries entries, $(human_size "$MEDIA")${MEDIA_AGE:+, ${MEDIA_AGE}h old})"
    if [[ "$CHECK_AGE" == 1 && -n "$MEDIA_AGE" ]] && ((MEDIA_AGE > RESTORE_CHECK_MAX_AGE_HOURS)); then
      fail "freshness: the newest media archive is ${MEDIA_AGE}h old (limit ${RESTORE_CHECK_MAX_AGE_HOURS}h)"
    fi
  fi
fi

# The EXIT trap removes the disposable container and prints the final verdict.
