#!/usr/bin/env bash
# =============================================================================
# backup-production.sh - nightly, verified backup of the Spine production data
# =============================================================================
#
# What it does, in order:
#   1. Takes the lock (one backup at a time), then pings healthchecks.io "/start"
#                                                          (only if HEALTHCHECK_URL is set)
#   2. Preflight: enough free disk space, Docker Desktop answering, the database
#      accepting connections (the app container is NOT needed for the database dump).
#   3. Dumps Postgres with `pg_dump -Fc` inside the DB container (it waits at most
#      DUMP_LOCK_WAIT_SECONDS for a table lock), reads the whole dump back to
#      verify it, runs sanity checks (users present, size not far below the biggest
#      recent healthy dump), then atomically moves it to
#          <BACKUP_ROOT>/nightly/spine-db-<UTC timestamp>.dump
#   4. Archives the media volume (found from the app container's mounts, never
#      hard-coded) to
#          <BACKUP_ROOT>/nightly/spine-media-<UTC timestamp>.tar.gz
#      and verifies the archive is readable and not empty.
#   5. Keeps one copy per month in <BACKUP_ROOT>/monthly, so a long run of bad
#      nights can never push every good backup out of the nightly window.
#   6. Uploads nightly + monthly files to Cloudflare R2 with rclone and checks the
#      upload                                              (only if configured)
#   7. Only after all of that, prunes old local files (never this run's own files,
#      never while anything failed, never when the clock looks wrong).
#   8. Pings healthchecks.io: a one-line summary on success, or ".../fail" with a
#      short log tail on failure                           (only if HEALTHCHECK_URL is set)
#
# Anything that is not configured (R2, healthchecks.io, even the config file
# itself) is skipped with a clear "SKIP" log line - it never causes a failure
# (unless you ask for it: REQUIRE_OFFSITE=1).
#
# Exit status:
#   0    every stage succeeded
#   1    something failed (the log says what), including "the lock could not be
#        used at all" (unwritable backup folder, something odd at the lock path)
#   2    bad configuration
#   75   another backup is genuinely running (it holds the lock): nothing was done,
#        and healthchecks.io is NOT told (that would be a false alarm)
#   124  stopped by the overall time limit (MAX_RUN_SECONDS)
#   129/130/143  stopped by SIGHUP / SIGINT / SIGTERM (cleaned up within a second or two)
#
# Config: ~/.config/spine/backup.env (never in the repo). See
# scripts/backup.env.example for every setting and docs/backups.md for the
# full walkthrough. Values already present in the environment override the
# config file, so a one-off run is easy:
#       KEEP_DB_DUMPS=30 ./backup-production.sh
# Set SPINE_BACKUP_CONFIG=/some/file to read a different config file.
#
# Conventions shared with the deploy workflow's pre-deploy dump (that workflow,
# .github/workflows/deploy-backend-desk-mac.yml, lives on the
# codex/game-tracking-end-to-end branch): umask 077, custom-format dump
# `pg_dump -U spine -d spine -Fc` via `docker exec spine-db`, files stored under
# ~/projects/spine-backups.
#
# This script never reads .env.production and never needs the database
# password (pg_dump talks to Postgres over the container's local socket).
#
# Written for macOS's stock bash 3.2 - no associative arrays, no mapfile - and
# for the stock macOS perl (used to put a time limit on docker and rclone, and to
# take the lock: see "Locking" below).

set -euo pipefail
umask 077
# A closed terminal or `backup-production.sh | head` must not kill this script
# half way (before the lock is released and before healthchecks.io is told):
# writing to a dead output then just fails, and logging ignores that failure.
# (Two independent layers: SIGPIPE is ignored here, and every log write happens in
# a subshell, see log() - each one alone would be enough.)
trap '' PIPE

# -----------------------------------------------------------------------------
# Environment. launchd starts jobs with almost nothing (no Homebrew, no Docker
# Desktop CLI on PATH), so PATH is set explicitly here instead of trusting the
# caller. EXTRA_PATH (config/env) is an opt-in prefix for unusual installs.
# -----------------------------------------------------------------------------
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
SCRIPT_NAME="${0##*/}"

usage() {
  cat <<'EOF'
Usage: backup-production.sh [--help]

Takes a verified backup of the Spine production database and media volume,
uploads it to Cloudflare R2 (if configured), prunes old local copies and reports
to healthchecks.io (if configured).

Config file : ~/.config/spine/backup.env   (override with SPINE_BACKUP_CONFIG)
Docs        : docs/backups.md, scripts/backup.env.example
EOF
}

case "${1:-}" in
  "") ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    echo "Unknown argument: $1" >&2
    usage >&2
    exit 2
    ;;
esac

# -----------------------------------------------------------------------------
# Configuration: defaults < config file < environment variables
# -----------------------------------------------------------------------------
CONFIG_FILE="${SPINE_BACKUP_CONFIG:-$HOME/.config/spine/backup.env}"
CONFIG_KEYS="BACKUP_ROOT NIGHTLY_SUBDIR MONTHLY_SUBDIR KEEP_DB_DUMPS KEEP_MEDIA_ARCHIVES \
KEEP_MONTHLY APP_CONTAINER DB_CONTAINER DB_NAME DB_USER MEDIA_MOUNT_PATH MEDIA_HELPER_IMAGE \
RCLONE_REMOTE R2_BUCKET R2_PREFIX R2_MONTHLY_PREFIX HEALTHCHECK_URL REQUIRE_OFFSITE \
DOCKER_WAIT_SECONDS DOCKER_CMD_TIMEOUT STEP_TIMEOUT_SECONDS MAX_RUN_SECONDS DUMP_LOCK_WAIT_SECONDS \
MIN_FREE_MB MIN_USERS MAX_SIZE_DROP_PERCENT SIZE_BASELINE_DUMPS ALLOW_EMPTY_MEDIA EXTRA_PATH"

CONFIG_WARN=""
load_config() {
  local key flag saved mode
  # Remember what the caller put in the environment: it must beat the file.
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
    # The file can hold the healthchecks.io URL, which works like a password.
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
# ACCEPT_SMALLER_DB is for ONE run ("I deleted data on purpose, the smaller
# database is the new normal"): it is taken from the environment only, so that a
# line left behind in the config file cannot switch the size check off for good.
_accept_smaller_db="${ACCEPT_SMALLER_DB:-0}"
unset ACCEPT_SMALLER_DB
load_config
ACCEPT_IN_FILE=0
if [[ -n "${ACCEPT_SMALLER_DB+x}" ]]; then
  ACCEPT_IN_FILE=1
fi
ACCEPT_SMALLER_DB="$_accept_smaller_db"

: "${BACKUP_ROOT:=$HOME/projects/spine-backups}"
: "${NIGHTLY_SUBDIR:=nightly}"
: "${MONTHLY_SUBDIR:=monthly}"
: "${KEEP_DB_DUMPS:=14}"
: "${KEEP_MEDIA_ARCHIVES:=14}"
: "${KEEP_MONTHLY:=3}"
: "${APP_CONTAINER:=spine}"
: "${DB_CONTAINER:=spine-db}"
: "${DB_NAME:=spine}"
: "${DB_USER:=spine}"
: "${MEDIA_MOUNT_PATH:=/yamtrack/media}"
: "${MEDIA_HELPER_IMAGE:=}"
: "${RCLONE_REMOTE:=}"
: "${R2_BUCKET:=}"
: "${R2_PREFIX:=nightly}"
: "${R2_MONTHLY_PREFIX:=monthly}"
: "${HEALTHCHECK_URL:=}"
: "${REQUIRE_OFFSITE:=0}"
: "${DOCKER_WAIT_SECONDS:=60}"
: "${DOCKER_CMD_TIMEOUT:=30}"
: "${STEP_TIMEOUT_SECONDS:=3600}"
: "${MAX_RUN_SECONDS:=10800}"
: "${DUMP_LOCK_WAIT_SECONDS:=120}"
: "${MIN_FREE_MB:=1024}"
: "${MIN_USERS:=1}"
: "${MAX_SIZE_DROP_PERCENT:=50}"
: "${SIZE_BASELINE_DUMPS:=7}"
: "${ALLOW_EMPTY_MEDIA:=0}"
: "${EXTRA_PATH:=}"

# Be forgiving about common typing slips (quoted "~/x", "r2:", "/bucket/").
BACKUP_ROOT="${BACKUP_ROOT/#\~/$HOME}"
BACKUP_ROOT="${BACKUP_ROOT%/}"
RCLONE_REMOTE="${RCLONE_REMOTE%:}"
R2_BUCKET="${R2_BUCKET#/}"
R2_BUCKET="${R2_BUCKET%/}"
R2_PREFIX="${R2_PREFIX#/}"
R2_PREFIX="${R2_PREFIX%/}"
R2_MONTHLY_PREFIX="${R2_MONTHLY_PREFIX#/}"
R2_MONTHLY_PREFIX="${R2_MONTHLY_PREFIX%/}"
set_path

# Every number is checked to be a short run of digits WITHOUT leading zeros: bash
# reads "08" as a broken octal number (and "010" as 8), and a long number would
# wrap around in bash's 64-bit arithmetic and could turn "keep the newest N" into
# "delete everything".
CONFIG_ERRORS=""
cfg_error() { CONFIG_ERRORS="${CONFIG_ERRORS}${CONFIG_ERRORS:+$'\n'}$1"; }
check_int() { # check_int NAME REGEX DESCRIPTION
  local value="${!1}"
  [[ "$value" =~ $2 ]] || cfg_error "config: $1 must be $3 (got '$value')"
  return 0
}
# check_subdir NAME: a plain folder name (no slash, not "." or "..", not hidden).
check_subdir() {
  local value="${!1}"
  [[ "$value" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*$ ]] ||
    cfg_error "config: $1 must be a plain folder name - letters, digits, . _ - and no slash (got '$value')"
  return 0
}
validate_config() {
  check_int KEEP_DB_DUMPS '^[1-9][0-9]{0,3}$' "a whole number from 1 to 9999"
  check_int KEEP_MEDIA_ARCHIVES '^[1-9][0-9]{0,3}$' "a whole number from 1 to 9999"
  check_int KEEP_MONTHLY '^(0|[1-9][0-9]{0,2})$' "a whole number from 0 to 999, without leading zeros (0 turns monthly copies off)"
  check_int DOCKER_WAIT_SECONDS '^(0|[1-9][0-9]{0,4})$' "a whole number of seconds (0 to 99999), without leading zeros"
  check_int DOCKER_CMD_TIMEOUT '^[1-9][0-9]{0,4}$' "a whole number of seconds (1 to 99999)"
  check_int STEP_TIMEOUT_SECONDS '^[1-9][0-9]{0,6}$' "a whole number of seconds (1 to 9999999)"
  check_int MAX_RUN_SECONDS '^[1-9][0-9]{0,6}$' "a whole number of seconds (1 to 9999999)"
  check_int DUMP_LOCK_WAIT_SECONDS '^[1-9][0-9]{0,4}$' "a whole number of seconds (1 to 99999)"
  check_int MIN_FREE_MB '^(0|[1-9][0-9]{0,8})$' "a whole number of megabytes, without leading zeros"
  check_int MIN_USERS '^(0|[1-9][0-9]{0,8})$' "a whole number, without leading zeros (0 turns the check off)"
  check_int MAX_SIZE_DROP_PERCENT '^(0|[1-9][0-9]?|100)$' "a percentage from 0 to 100 (0 turns the check off)"
  check_int SIZE_BASELINE_DUMPS '^[1-9][0-9]{0,2}$' "a whole number from 1 to 999"
  check_int ALLOW_EMPTY_MEDIA '^[01]$' "0 or 1"
  check_int REQUIRE_OFFSITE '^[01]$' "0 or 1"
  check_int ACCEPT_SMALLER_DB '^[01]$' "0 or 1"
  check_subdir NIGHTLY_SUBDIR
  check_subdir MONTHLY_SUBDIR
  # APFS is case-insensitive by default, so "Nightly" and "nightly" are ONE folder.
  if [[ "$(printf '%s' "$NIGHTLY_SUBDIR" | tr '[:upper:]' '[:lower:]')" == "$(printf '%s' "$MONTHLY_SUBDIR" | tr '[:upper:]' '[:lower:]')" ]]; then
    cfg_error "config: NIGHTLY_SUBDIR and MONTHLY_SUBDIR must be different folders (both are '$NIGHTLY_SUBDIR'): the monthly copies would be pruned as nightly files"
  fi
  return 0
}
validate_config

NIGHTLY_DIR="$BACKUP_ROOT/$NIGHTLY_SUBDIR"
MONTHLY_DIR="$BACKUP_ROOT/$MONTHLY_SUBDIR"
# Bookkeeping for the size check (hidden files: never pruned, never uploaded):
#   .suspect-backups     names of files that were flagged as "valid but suspicious"
#   .size-baseline-from  run stamp after which dumps count for the size check
#                        (written by a run with ACCEPT_SMALLER_DB=1)
SUSPECT_LIST="$NIGHTLY_DIR/.suspect-backups"
BASELINE_FROM_FILE="$NIGHTLY_DIR/.size-baseline-from"
DB_FILE_RE='^spine-db-[0-9]{8}T[0-9]{6}Z\.dump$'
MEDIA_FILE_RE='^spine-media-[0-9]{8}T[0-9]{6}Z\.tar\.gz$'
RUN_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# -----------------------------------------------------------------------------
# Logging. Everything goes to stdout so launchd's log file stays in order.
# Secrets never reach the log: the healthchecks URL and the R2 account hostname
# are scrubbed from any captured tool output before it is printed or sent
# anywhere. The URL is passed to awk through the environment, never on its
# command line, so it does not show up in `ps`.
# -----------------------------------------------------------------------------
# A failed write (closed terminal, `| head`) is ignored: logging must never stop a backup.
# The write happens in a SUBSHELL on purpose: when a write fails (the reader is gone),
# bash keeps the unwritten bytes in its stdout buffer, and every later `$(...)` would
# then flush that stale text into its own captured output (a path from mktemp, a
# number from date ...). The subshell's copy of the buffer dies with the subshell.
log() { (printf '%s %-5s %s\n' "$(date '+%Y-%m-%d %H:%M:%S %z')" "$1" "$2") 2>/dev/null || true; }
info() { log INFO "$*"; }
warn() { log WARN "$*"; }
err() { log ERROR "$*"; }
skip() { log SKIP "$*"; }

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

# show_lines FILE [LEVEL] [MAX]: log the first MAX lines of a captured tool output.
show_lines() {
  local file="$1" level="${2:-ERROR}" max="${3:-15}" line
  if [[ -s "$file" ]]; then
    head -n "$max" "$file" | scrub_stream | while IFS= read -r line; do
      log "$level" "  | $line"
    done
  fi
  return 0
}

# rclone starts every line with its own timestamp and level ("2026/09/30 03:31:00 INFO  : ...");
# ours already carries both, so strip them from a captured rclone log (in place).
normalize_rclone_log() {
  sed -E 's#^[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9:]{8} [A-Z]+ *: ##' "$1" >"$SCRATCH_FILE"
  cat "$SCRATCH_FILE" >"$1"
}

file_size() { wc -c <"$1" | tr -d ' '; }

human_size() { # human_size FILE -> "12.3 MB"
  local bytes
  bytes="$(file_size "$1")"
  awk -v b="$bytes" 'BEGIN {
    split("B KB MB GB TB", u, " "); i = 1
    while (b >= 1024 && i < 5) { b /= 1024; i++ }
    if (i == 1) printf "%d %s", b, u[i]; else printf "%.1f %s", b, u[i]
  }'
}

# list_names DIR GLOB REGEX: print the names of matching regular files, oldest
# first (the names embed a UTC timestamp and globs come back sorted).
list_names() {
  local dir="$1" glob="$2" re="$3" f name
  # shellcheck disable=SC2086 # the glob must expand
  for f in "$dir"/$glob; do
    [[ -f "$f" ]] || continue
    name="${f##*/}"
    [[ "$name" =~ $re ]] || continue
    printf '%s\n' "$name"
  done
  return 0
}

# newest_other DIR GLOB REGEX: newest matching name that does not belong to this run.
newest_other() {
  list_names "$1" "$2" "$3" | awk -v s="$RUN_STAMP" 'index($0, s) == 0' | tail -n 1
}

# -----------------------------------------------------------------------------
# Time limits. `docker` and `rclone` are Go programs: they catch SIGALRM and do
# nothing with it, so the classic `perl -e 'alarm N; exec ...'` trick does NOT
# stop them. This wrapper forks the command, asks it to stop with TERM when the
# time is up, and KILLs it 3 seconds later if it still ignores that.
# It also stops the command when the process that supervises this run (the
# top-level script, $$) has disappeared - killed with SIGKILL, say - so a run can
# never carry on unsupervised.
# Exit status: the command's own; 124 when it was stopped for running too long;
# 125 when it was stopped because the supervisor is gone.
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
# healthchecks.io. A failed ping is only a warning: it must never change the
# result of the backup itself. The URL is handed to curl on stdin (-K -) so it
# does not show up in `ps`, and it is never logged. A reply other than exactly
# "OK" (for example "OK (not found)" for a mistyped UUID) is a warning too.
# -----------------------------------------------------------------------------
hc_ping() { # hc_ping SUFFIX [BODY_FILE] [fast]      SUFFIX: "" | /start | /fail
  local suffix="$1" body="${2:-}" fast="${3:-}" url cerr resp answer
  local args=(-fsS -m 10 --retry 3)
  [[ -n "$HEALTHCHECK_URL" ]] || return 0
  if [[ -n "$fast" ]]; then
    args=(-fsS -m 5 --retry 0)
  fi
  url="${HEALTHCHECK_URL%/}$suffix"
  cerr="$(mktemp "$TMP_BASE/spine-backup-curl.XXXXXX")"
  resp="$(mktemp "$TMP_BASE/spine-backup-resp.XXXXXX")"
  args+=(-o "$resp" -K -)
  if [[ -n "$body" ]]; then
    args+=(--data-binary "@$body")
  fi
  if { printf 'url = "%s"\n' "$url"; } 2>/dev/null | curl "${args[@]}" 2>"$cerr"; then
    answer="$(head -c 100 "$resp" | tr -d '\r\n')"
    if [[ "$answer" == "OK" ]]; then
      info "healthchecks.io: ping '${suffix:-/}' sent"
    else
      warn "healthchecks.io answered '$(printf '%s' "$answer" | scrub_stream)' instead of 'OK' - check that HEALTHCHECK_URL is correct (the backup itself is not affected)"
    fi
  else
    warn "healthchecks.io: ping '${suffix:-/}' could not be delivered (the backup result is not affected)"
    show_lines "$cerr" WARN 5
  fi
  rm -f "$cerr" "$resp"
  return 0
}

# -----------------------------------------------------------------------------
# Locking: one backup at a time.
#
# With perl (every Mac has it) the lock is an operating-system file lock (flock)
# on $BACKUP_ROOT/.backup.lock. The supervisor takes it on file descriptor 8
# BEFORE the worker starts, and every process of the run inherits that
# descriptor, so the lock lasts exactly as long as ANY process of the run is
# alive - even if the supervisor is killed with SIGKILL - and the kernel drops it
# the moment the last one is gone. There is no stale lock to detect or clean up,
# and two runs can never both believe they hold it: the kernel decides. The lock
# FILE is never deleted (deleting a lock file is what makes file locks racy); its
# one line of text ("pid=... started=...") only tells a refused run who holds it.
#
# Without perl the fallback is a symlink whose target is the pid (`ln -s` creates
# it and stores the pid in one atomic step), with a tiny mkdir "mutex" around
# check-and-take so that two runs cannot both decide that the same lock is
# stale. It cannot see a worker that outlives its supervisor, so it is weaker.
#
# acquire_lock returns 0 (taken), 75 (a live run holds it) or 1 (the lock could
# not be used at all: that is a failure, not "somebody else is running").
# $$ is the pid of the top-level script even inside subshells.
# -----------------------------------------------------------------------------
LOCK_FILE="$BACKUP_ROOT/.backup.lock"
LOCK_LINK="$BACKUP_ROOT/.backup.lock.pid"
LOCK_MUTEX="$BACKUP_ROOT/.backup.lock.mutex"
LOCK_HELD=""

lock_holder_is_running() { # (fallback lock only) is PID a live shell running this very script?
  local pid="$1" cmd first word
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  cmd="$(ps -ww -p "$pid" -o command= 2>/dev/null || true)"
  [[ -n "$cmd" ]] || return 1
  first="${cmd%% *}"
  first="${first##*/}"
  case "$first" in
    bash | sh | zsh | dash | ksh) ;;
    *) return 1 ;;
  esac
  # shellcheck disable=SC2086 # deliberate word splitting of the command line
  for word in $cmd; do
    if [[ "$word" == "$SCRIPT_NAME" || "$word" == */"$SCRIPT_NAME" ]]; then
      return 0
    fi
  done
  return 1
}

# Says who holds the lock (read from the lock file) in the log.
report_live_holder() {
  local info pid="" started="" where="" re='^pid=([0-9]+) started=([0-9TZ]+)'
  info="$(sed -n '1p' "$LOCK_FILE" 2>/dev/null || true)"
  if [[ "$info" =~ $re ]]; then
    pid="${BASH_REMATCH[1]}"
    started="${BASH_REMATCH[2]}"
    where=" (pid $pid, started $started)"
    if ! kill -0 "$pid" 2>/dev/null; then
      where=" (started by pid $pid at $started, which is gone - but a process it started still holds the lock and stops by itself within seconds)"
    fi
  fi
  outer_log WARN "another backup is already running$where - this run does nothing"
}

acquire_lock_simple() { # the fallback when perl is missing
  local holder tries=0 missing=0
  # `ln -s` would create its link INSIDE a directory at the lock path (and
  # succeed): anything that is not a symlink there is left alone and reported.
  if [[ -e "$LOCK_LINK" && ! -L "$LOCK_LINK" ]]; then
    outer_log ERROR "$LOCK_LINK is not a lock - look at it and remove it by hand, then run the backup again"
    return 1
  fi
  # The mutex is held for a few milliseconds only; one older than a minute was
  # left behind by a run that was killed inside it. A failed mkdir with no folder
  # there afterwards usually means its holder finished a moment ago (just try
  # again); only a run of such failures means that mkdir cannot work at all.
  while ! mkdir "$LOCK_MUTEX" 2>/dev/null; do
    if [[ -d "$LOCK_MUTEX" ]]; then
      missing=0
      if [[ -n "$(find "$LOCK_MUTEX" -maxdepth 0 -mmin +1 2>/dev/null)" ]]; then
        rmdir "$LOCK_MUTEX" 2>/dev/null || true
      fi
    else
      missing=$((missing + 1))
      if ((missing >= 5)); then
        outer_log ERROR "cannot create $LOCK_MUTEX - is $BACKUP_ROOT writable?"
        return 1
      fi
    fi
    tries=$((tries + 1))
    if ((tries > 60)); then
      outer_log ERROR "could not take the lock mutex $LOCK_MUTEX"
      return 1
    fi
    sleep 0.2
  done
  # Inside the mutex nobody else can be checking or replacing the lock.
  if [[ -L "$LOCK_LINK" ]]; then
    holder="$(readlink "$LOCK_LINK" 2>/dev/null || true)"
    if lock_holder_is_running "$holder"; then
      rmdir "$LOCK_MUTEX" 2>/dev/null || true
      outer_log WARN "another backup is already running (pid $holder) - this run does nothing"
      return 75
    fi
    outer_log WARN "removing a stale lock (pid $holder is not a running backup)"
    rm -f "$LOCK_LINK"
  fi
  if ln -s "$$" "$LOCK_LINK" 2>/dev/null && [[ "$(readlink "$LOCK_LINK" 2>/dev/null || true)" == "$$" ]]; then
    LOCK_HELD=link
    rmdir "$LOCK_MUTEX" 2>/dev/null || true
    return 0
  fi
  rmdir "$LOCK_MUTEX" 2>/dev/null || true
  outer_log ERROR "could not create the lock $LOCK_LINK"
  return 1
}

acquire_lock() {
  local rc=0
  if [[ -L "$LOCK_FILE" || ( -e "$LOCK_FILE" && ! -f "$LOCK_FILE" ) ]]; then
    outer_log ERROR "$LOCK_FILE exists but is not a plain lock file - look at it and remove it by hand, then run the backup again"
    return 1
  fi
  if ((HAVE_PERL == 0)); then
    acquire_lock_simple
    return $?
  fi
  # Open (and create if need be) the lock file on descriptor 8. Appending never
  # truncates what the current holder wrote into it.
  if ! { exec 8>>"$LOCK_FILE"; } 2>/dev/null; then
    outer_log ERROR "cannot open the lock file $LOCK_FILE - is $BACKUP_ROOT writable?"
    return 1
  fi
  perl -e '
    use Fcntl ":flock";
    open(my $fh, "<&=", 8) or exit 3;
    exit 0 if flock($fh, LOCK_EX | LOCK_NB);
    exit(($!{EWOULDBLOCK} || $!{EAGAIN}) ? 1 : 3);
  ' || rc=$?
  case "$rc" in
    0)
      LOCK_HELD=flock
      { printf 'pid=%s started=%s\n' "$$" "$RUN_STAMP" >"$LOCK_FILE"; } 2>/dev/null || true
      return 0
      ;;
    1)
      exec 8>&-
      report_live_holder
      return 75
      ;;
    *)
      exec 8>&-
      outer_log ERROR "could not take the lock on $LOCK_FILE (flock failed - is the backup folder on a network drive?)"
      return 1
      ;;
  esac
}

# Give the lock back. (For the flock lock that means closing descriptor 8; it is
# really released once no other process of the run holds it either.)
release_lock_if_mine() {
  case "$LOCK_HELD" in
    flock) exec 8>&- ;;
    link)
      if [[ -L "$LOCK_LINK" && "$(readlink "$LOCK_LINK" 2>/dev/null || true)" == "$$" ]]; then
        rm -f "$LOCK_LINK"
      fi
      ;;
  esac
  LOCK_HELD=""
  return 0
}

# Created by the top-level shell (see the bottom of the file) so that its EXIT
# trap can always clean them up. (bash 3.2 does not run EXIT traps of a function
# that runs as a pipeline element, so cleanup must not live inside main.)
RUN_LOG=""
ERR_FILE=""
SCRATCH_FILE=""
SUMMARY_FILE=""
SUSPECT_FILE=""
NOMONTHLY_FILE=""
RC_FILE=""
STAGE_TMP=""
FAILURES=""
STAGE_RC=0
ORPHANED=0

summary_add() { printf '%s\n' "$1" >>"$SUMMARY_FILE"; }
# suspect_add FILE-NAME MESSAGE: a finding that makes the run fail (see check_suspects)
suspect_add() { printf '%s\t%s\n' "$1" "$2" >>"$SUSPECT_FILE"; }

# A worker whose supervisor (the top-level script, $$) has died - SIGKILL, say -
# must not carry on unsupervised: nobody would enforce the time limit, collect its
# result or clean up after it. A killed process that its parent has not collected
# yet (a "zombie") counts as gone. If ps itself fails, assume the supervisor is alive.
supervisor_gone() {
  local st
  kill -0 "$$" 2>/dev/null || return 0
  st="$(ps -o stat= -p "$$" 2>/dev/null || true)"
  st="${st//[[:space:]]/}"
  [[ "$st" == Z* ]]
}

# Stop this (orphaned) worker: remove everything this run made, including the
# supervisor's temporary files that the dead supervisor can no longer remove, and
# stop everything the run started (a command's own child processes included: they
# all sit in the worker's process group, which the supervisor created with
# `set -m`; the supervisor's own group is never touched). The lock is not
# released here: it goes away by itself when the last process of the run is gone.
remove_this_runs_partials() {
  rm -f "$NIGHTLY_DIR"/.partial-*"$RUN_STAMP".* "$MONTHLY_DIR"/.partial-*"$RUN_STAMP".* 2>/dev/null || true
}
SUPERVISOR_PGID=""
worker_abandon() {
  local pgid
  ORPHANED=1
  warn "the process that supervises this backup is gone - stopping and cleaning up (the next run starts from scratch)"
  remove_this_runs_partials
  rm -f "$RUN_LOG" "$ERR_FILE" "$SCRATCH_FILE" "$SUMMARY_FILE" "$SUSPECT_FILE" "$NOMONTHLY_FILE" "$RC_FILE" 2>/dev/null || true
  pgid="$(sh -c 'ps -o pgid= -p $$' 2>/dev/null | tr -d ' ' || true)" # the group of a fresh child of ours = ours
  if [[ "$pgid" =~ ^[0-9]+$ && "$pgid" -gt 1 && -n "$SUPERVISOR_PGID" && "$pgid" != "$SUPERVISOR_PGID" ]]; then
    trap '' TERM
    kill -TERM -- "-$pgid" 2>/dev/null || true
    sleep 2
    kill -KILL -- "-$pgid" 2>/dev/null || true
  fi
  exit 1
}
abort_if_orphaned() {
  if supervisor_gone; then
    worker_abandon
  fi
  return 0
}

# run_stage NAME FUNCTION: run one stage in its own subshell, as a plain
# statement so that `set -e` keeps working inside it (bash silently ignores
# errexit inside anything called from an `if` or `||`). The outcome is left in
# STAGE_RC; failures are also collected in FAILURES.
run_stage() {
  local name="$1" fn="$2"
  abort_if_orphaned
  set +e
  (
    set -eu
    "$fn"
  )
  STAGE_RC=$?
  set -e
  if ((STAGE_RC != 0)); then
    FAILURES="$FAILURES $name"
    err "$name: stage FAILED (exit $STAGE_RC)"
  fi
  return 0
}

# -----------------------------------------------------------------------------
# Stage: preflight - disk space, Docker, the database
# -----------------------------------------------------------------------------
wait_for_docker() {
  local deadline=$((SECONDS + DOCKER_WAIT_SECONDS)) nap announced=0
  until dq info >/dev/null 2>&1; do
    abort_if_orphaned
    if ((SECONDS >= deadline)); then
      return 1
    fi
    if ((announced == 0)); then
      warn "Docker is not answering yet; waiting up to ${DOCKER_WAIT_SECONDS}s for it"
      announced=1
    fi
    nap=5
    if ((deadline - SECONDS < nap)); then
      nap=$((deadline - SECONDS))
    fi
    sleep "$nap"
  done
}

# Ask Postgres itself, over TCP: `.State.Running` only says the container exists,
# and a freshly started database can take a while before it accepts connections.
wait_for_db() {
  local deadline=$((SECONDS + DOCKER_WAIT_SECONDS)) nap announced=0
  until dq exec "$DB_CONTAINER" pg_isready -q -h 127.0.0.1 -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; do
    abort_if_orphaned
    if ((SECONDS >= deadline)); then
      return 1
    fi
    if ((announced == 0)); then
      warn "the database is not accepting connections yet; waiting up to ${DOCKER_WAIT_SECONDS}s for it"
      announced=1
    fi
    nap=2
    if ((deadline - SECONDS < nap)); then
      nap=$((deadline - SECONDS))
    fi
    sleep "$nap"
  done
}

stage_preflight() {
  local free_mb need_mb last_db last_media newest state stale rc

  if ! mkdir -p "$NIGHTLY_DIR"; then
    err "cannot create backup folder $NIGHTLY_DIR"
    return 1
  fi
  info "backup folder: $NIGHTLY_DIR"

  # Room for this run: at least MIN_FREE_MB, and at least twice the size of the
  # last dump plus the last media archive (a dump is never smaller than that for long).
  last_db=0
  last_media=0
  newest="$(list_names "$NIGHTLY_DIR" 'spine-db-*.dump' "$DB_FILE_RE" | tail -n 1)"
  if [[ -n "$newest" ]]; then
    last_db="$(file_size "$NIGHTLY_DIR/$newest")"
  fi
  newest="$(list_names "$NIGHTLY_DIR" 'spine-media-*.tar.gz' "$MEDIA_FILE_RE" | tail -n 1)"
  if [[ -n "$newest" ]]; then
    last_media="$(file_size "$NIGHTLY_DIR/$newest")"
  fi
  need_mb=$(((2 * (last_db + last_media)) / 1048576 + 1))
  if ((need_mb < MIN_FREE_MB)); then
    need_mb=$MIN_FREE_MB
  fi
  free_mb="$(df -Pk "$BACKUP_ROOT" | awk 'NR==2 { printf "%d", $4 / 1024 }')"
  info "disk: ${free_mb} MB free (needed for this run: ${need_mb} MB = the larger of MIN_FREE_MB and twice the last backup)"
  if ((free_mb < need_mb)); then
    err "not enough free disk space under $BACKUP_ROOT"
    return 1
  fi

  # Leftovers of a run that was killed half way (older than a day) are safe to remove.
  stale="$(find "$NIGHTLY_DIR" -maxdepth 1 -type f -name '.partial-*' -mtime +0 -print -delete | wc -l | tr -d ' ')"
  if [[ "$stale" != "0" ]]; then
    warn "removed $stale stale temporary file(s) from an interrupted earlier run"
  fi

  if ! command -v docker >/dev/null 2>&1; then
    err "the docker CLI was not found (PATH is: $PATH)"
    return 1
  fi
  # Right after a reboot or wake-up Docker Desktop can take a while to answer.
  if ! wait_for_docker; then
    err "the Docker daemon is not reachable - is Docker Desktop running?"
    return 1
  fi

  # Only the DATABASE is needed here. The app container matters to the media
  # stage alone, so a deploy in progress cannot stop the database dump.
  rc=0
  state="$(dq inspect -f '{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null)" || rc=$?
  if ((rc == 124)); then
    err "docker did not answer within ${DOCKER_CMD_TIMEOUT}s while looking at '$DB_CONTAINER' - is Docker Desktop stuck?"
    return 1
  fi
  if [[ -z "$state" ]]; then
    err "database container '$DB_CONTAINER' does not exist (check DB_CONTAINER in the config)"
    return 1
  fi
  if [[ "$state" != "true" ]]; then
    err "database container '$DB_CONTAINER' exists but is not running - start the stack first"
    return 1
  fi
  if ! wait_for_db; then
    err "database container '$DB_CONTAINER' is running but the database does not accept connections"
    return 1
  fi
  info "docker: reachable; '$DB_CONTAINER' is running and accepting connections"
}

# -----------------------------------------------------------------------------
# Database: dump -> verify -> sanity checks -> atomic move
# -----------------------------------------------------------------------------

# verify_dump FILE. `pg_restore --list` proves the header and table of contents
# are readable, but it never touches the data blocks, so a dump that was cut off
# half way would pass. Reading the whole archive as a SQL script (sent to
# /dev/null) forces every block to be read and decompressed as well.
# Afterwards DUMP_TABLES and DUMP_USERS hold the number of tables and the number
# of rows in users_user ("" when the dump has no such table).
verify_dump() {
  local f="$1" rc=0
  DUMP_TABLES=0
  DUMP_USERS=""
  if [[ ! -s "$f" ]]; then
    err "db: verification FAILED - the dump is empty"
    return 1
  fi
  run_timeout "$STEP_TIMEOUT_SECONDS" docker exec -i "$DB_CONTAINER" pg_restore --list <"$f" >"$SCRATCH_FILE" 2>"$ERR_FILE" || rc=$?
  if ((rc == 124)); then
    err "db: verification timed out after ${STEP_TIMEOUT_SECONDS}s"
    return 1
  fi
  if ((rc != 0)); then
    err "db: verification FAILED - pg_restore --list cannot read the dump"
    show_lines "$ERR_FILE"
    return 1
  fi
  DUMP_TABLES="$(grep -c ' TABLE DATA ' "$SCRATCH_FILE" || true)"
  if ((DUMP_TABLES < 1)); then
    err "db: verification FAILED - the dump contains no tables (is DB_NAME='$DB_NAME' the right database?)"
    return 1
  fi
  rc=0
  run_timeout "$STEP_TIMEOUT_SECONDS" docker exec -i "$DB_CONTAINER" pg_restore -f /dev/null <"$f" 2>"$ERR_FILE" || rc=$?
  if ((rc == 124)); then
    err "db: verification timed out after ${STEP_TIMEOUT_SECONDS}s"
    return 1
  fi
  if ((rc != 0)); then
    err "db: verification FAILED - the dump is damaged or incomplete (full read failed)"
    show_lines "$ERR_FILE"
    return 1
  fi
  # How many users does the dump hold? (Only that one table is read back out.)
  if grep -qE ' TABLE DATA [^ ]+ users_user ' "$SCRATCH_FILE"; then
    rc=0
    run_timeout "$STEP_TIMEOUT_SECONDS" docker exec -i "$DB_CONTAINER" pg_restore --data-only --table=users_user -f - <"$f" 2>"$ERR_FILE" |
      awk '/^COPY /{ inblock = 1; next } /^\\\.$/{ inblock = 0 } inblock { n++ } END { print n + 0 }' >"$SCRATCH_FILE" || rc=$?
    if ((rc != 0)); then
      err "db: verification FAILED - could not read the users table back out of the dump"
      show_lines "$ERR_FILE"
      return 1
    fi
    DUMP_USERS="$(tr -d ' \n' <"$SCRATCH_FILE")"
  fi
  info "db: verified - $DUMP_TABLES tables, all data blocks readable, users_user rows: ${DUMP_USERS:-none (no such table)}"
}

# The timestamp part of a backup file name: spine-db-20260930T073001Z.dump -> 20260930T073001Z
name_stamp() {
  local n="${1#spine-db-}"
  n="${n#spine-media-}"
  printf '%s' "${n%%.*}"
}

is_flagged() { # is this file name on the list of flagged backups?
  [[ -f "$SUSPECT_LIST" ]] && grep -qxF -- "$1" "$SUSPECT_LIST"
}

# Yardstick for the size check. Comparing with the PREVIOUS file alone fails to
# notice a slow bleed (each night 40% smaller than the last) or a bad night whose
# small dump then becomes the next night's yardstick. So the yardstick is the
# LARGEST of the newest SIZE_BASELINE_DUMPS healthy nightly dumps and the newest
# monthly dump. Dumps that were flagged, and dumps from before an
# ACCEPT_SMALLER_DB=1 run, do not count.
# Sets BASELINE_NAME / BASELINE_SIZE (empty / 0 when there is nothing to compare
# with) and BASELINE_UNVERIFIED=1 when earlier dumps exist but every one of them
# was flagged, so that this night cannot be checked at all.
baseline_candidates() { # "dir|name" lines, newest nightly first, then the newest monthly
  local name
  list_names "$NIGHTLY_DIR" 'spine-db-*.dump' "$DB_FILE_RE" | awk -v s="$RUN_STAMP" 'index($0, s) == 0' | sort -r |
    while IFS= read -r name; do
      printf '%s|%s\n' "$NIGHTLY_DIR" "$name"
    done
  name="$(list_names "$MONTHLY_DIR" 'spine-db-*.dump' "$DB_FILE_RE" | tail -n 1)"
  if [[ -n "$name" ]]; then
    printf '%s|%s\n' "$MONTHLY_DIR" "$name"
  fi
}

compute_size_baseline() {
  local from="" line dir name size trusted=0 seen=0
  BASELINE_NAME=""
  BASELINE_SIZE=0
  BASELINE_UNVERIFIED=0
  if [[ -s "$BASELINE_FROM_FILE" ]]; then
    from="$(sed -n '1p' "$BASELINE_FROM_FILE")"
  fi
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    dir="${line%%|*}"
    name="${line#*|}"
    if [[ -n "$from" && "$(name_stamp "$name")" < "$from" ]]; then
      continue
    fi
    seen=$((seen + 1))
    if is_flagged "$name"; then
      continue
    fi
    if [[ "$dir" == "$NIGHTLY_DIR" ]]; then
      if ((trusted >= SIZE_BASELINE_DUMPS)); then
        continue
      fi
      trusted=$((trusted + 1))
    fi
    size="$(file_size "$dir/$name")"
    if ((size > BASELINE_SIZE)); then
      BASELINE_SIZE=$size
      BASELINE_NAME=$name
    fi
  done < <(baseline_candidates)
  if [[ -z "$BASELINE_NAME" ]] && ((seen > 0)); then
    BASELINE_UNVERIFIED=1
  fi
  return 0
}

stage_db() {
  local final="$NIGHTLY_DIR/spine-db-$RUN_STAMP.dump" t0=$SECONDS rc=0 size
  STAGE_TMP="$(mktemp "$NIGHTLY_DIR/.partial-db-$RUN_STAMP.XXXXXX")"
  trap 'rm -f "${STAGE_TMP:-}"' EXIT

  info "db: dumping '$DB_NAME' from container '$DB_CONTAINER' (pg_dump -Fc)"
  # --lock-wait-timeout: pg_dump first takes a share lock on every table. If a
  # migration (or anything else) holds a conflicting lock, it would wait for as
  # long as that lasts - and stopping only the docker CLIENT when the time limit
  # is reached would leave that waiting pg_dump running inside the database
  # container. With the option it gives up by itself, and no dump process is left.
  run_timeout "$STEP_TIMEOUT_SECONDS" docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc \
    --lock-wait-timeout="${DUMP_LOCK_WAIT_SECONDS}s" >"$STAGE_TMP" 2>"$ERR_FILE" || rc=$?
  if ((rc == 124)); then
    err "db: pg_dump timed out after ${STEP_TIMEOUT_SECONDS}s"
    return 1
  fi
  if ((rc != 0)); then
    err "db: pg_dump FAILED"
    if grep -qiE 'statement timeout|lock timeout' "$ERR_FILE"; then
      err "db: pg_dump gave up after waiting ${DUMP_LOCK_WAIT_SECONDS}s for a table lock (DUMP_LOCK_WAIT_SECONDS): a migration or another long job holds it. Nothing was kept from this attempt"
    fi
    show_lines "$ERR_FILE"
    return 1
  fi
  if [[ -s "$ERR_FILE" ]]; then
    warn "db: pg_dump printed warnings:"
    show_lines "$ERR_FILE" WARN
  fi
  info "db: dump written ($(human_size "$STAGE_TMP")); verifying"
  verify_dump "$STAGE_TMP" || return 1

  # Sanity checks. The file is a valid dump, so it is kept either way - but a
  # dump with no users, or one that is suddenly much smaller than the recent ones,
  # makes this run FAIL so that somebody looks, and old backups are not pruned.
  if [[ -z "$DUMP_USERS" ]]; then
    if ((MIN_USERS > 0)); then
      suspect_add "${final##*/}" "db: the dump has no users_user table - is this the right database?"
    fi
  elif ((DUMP_USERS < MIN_USERS)); then
    suspect_add "${final##*/}" "db: the dump holds only $DUMP_USERS user(s) (expected at least $MIN_USERS, see MIN_USERS)"
  fi
  if ((MAX_SIZE_DROP_PERCENT > 0)) && [[ "$ACCEPT_SMALLER_DB" != 1 ]]; then
    compute_size_baseline
    size="$(file_size "$STAGE_TMP")"
    if [[ -n "$BASELINE_NAME" ]]; then
      if ((size * 100 < BASELINE_SIZE * (100 - MAX_SIZE_DROP_PERCENT))); then
        suspect_add "${final##*/}" "db: the dump is $size bytes, more than ${MAX_SIZE_DROP_PERCENT}% smaller than the biggest recent healthy dump ($BASELINE_NAME, $BASELINE_SIZE bytes). If you deleted data on purpose, run once with ACCEPT_SMALLER_DB=1"
      fi
    elif ((BASELINE_UNVERIFIED == 1)); then
      warn "db: size check skipped - every recent dump was flagged, so there is nothing trustworthy to compare with (after checking, run once with ACCEPT_SMALLER_DB=1)"
      printf 'unverified\n' >"$NOMONTHLY_FILE"
    fi
  fi

  if [[ -e "$final" ]]; then
    err "db: $final already exists - refusing to overwrite it"
    return 1
  fi
  mv "$STAGE_TMP" "$final" || return 1 # same folder, so this is an atomic rename
  info "db: OK $final ($(human_size "$final"), $((SECONDS - t0))s)"
  summary_add "db=$(human_size "$final" | tr -d ' ') tables=$DUMP_TABLES users=${DUMP_USERS:-none}"

  # "The database really is smaller now": from this dump on, only this dump and
  # the ones after it are the yardstick (unless it was flagged for another reason).
  if [[ "$ACCEPT_SMALLER_DB" == 1 ]]; then
    if grep -qF "${final##*/}"$'\t' "$SUSPECT_FILE" 2>/dev/null; then
      warn "db: ACCEPT_SMALLER_DB=1 was not applied: this dump was flagged for another reason (see above)"
    else
      printf '%s\n' "$RUN_STAMP" >"$BASELINE_FROM_FILE"
      info "db: ACCEPT_SMALLER_DB=1 - from this dump on, the size check compares with this dump and newer ones only"
    fi
  fi
}

# -----------------------------------------------------------------------------
# Media: find the volume from the app container's mounts, tar it from a
# throwaway container (read-only mount, no network), verify the archive.
# -----------------------------------------------------------------------------
stage_media() {
  local final="$NIGHTLY_DIR/spine-media-$RUN_STAMP.tar.gz" t0=$SECONDS rc=0
  local mount_info mtype mname msource src image entries

  dq inspect "$APP_CONTAINER" >/dev/null 2>"$ERR_FILE" || rc=$?
  if ((rc == 124)); then
    err "media: docker did not answer within ${DOCKER_CMD_TIMEOUT}s while looking at '$APP_CONTAINER'"
    return 1
  fi
  if ((rc != 0)); then
    err "media: app container '$APP_CONTAINER' was not found (a deploy in progress?) - the database dump is not affected"
    return 1
  fi
  if ! mount_info="$(dq inspect -f "{{range .Mounts}}{{if eq .Destination \"$MEDIA_MOUNT_PATH\"}}{{.Type}}|{{.Name}}|{{.Source}}{{end}}{{end}}" "$APP_CONTAINER" 2>"$ERR_FILE")"; then
    err "media: could not inspect container '$APP_CONTAINER'"
    show_lines "$ERR_FILE"
    return 1
  fi
  if [[ -z "$mount_info" ]]; then
    err "media: container '$APP_CONTAINER' has nothing mounted at $MEDIA_MOUNT_PATH"
    return 1
  fi
  IFS='|' read -r mtype mname msource <<<"$mount_info"
  case "$mtype" in
    volume) src="$mname" ;;
    bind) src="$msource" ;;
    *)
      err "media: unsupported mount type '$mtype' at $MEDIA_MOUNT_PATH"
      return 1
      ;;
  esac

  # Reuse the database container's image: it is already on this machine (so
  # nothing is ever pulled) and it ships tar and gzip.
  image="$MEDIA_HELPER_IMAGE"
  if [[ -z "$image" ]]; then
    if ! image="$(dq inspect -f '{{.Config.Image}}' "$DB_CONTAINER" 2>"$ERR_FILE")"; then
      err "media: could not determine a helper image from '$DB_CONTAINER'"
      show_lines "$ERR_FILE"
      return 1
    fi
  fi

  STAGE_TMP="$(mktemp "$NIGHTLY_DIR/.partial-media-$RUN_STAMP.XXXXXX")"
  trap 'rm -f "${STAGE_TMP:-}"' EXIT

  info "media: archiving $mtype '$src' (mounted at $MEDIA_MOUNT_PATH in '$APP_CONTAINER')"
  # --init: tar would otherwise be the container's pid 1, which ignores TERM, and
  # the time limit could not stop it.
  # The volume is mounted at a path that does not exist in the image: Docker
  # copies an image's own files into a still-EMPTY volume on first mount (even a
  # read-only one), and Alpine ships /media/cdrom, /media/floppy and /media/usb.
  run_timeout "$STEP_TIMEOUT_SECONDS" docker run --rm --init --pull=never --network none --entrypoint tar \
    -v "$src:/spine-backup-src:ro" "$image" -C /spine-backup-src -czf - . >"$STAGE_TMP" 2>"$ERR_FILE" || rc=$?
  if ((rc == 124)); then
    err "media: archiving timed out after ${STEP_TIMEOUT_SECONDS}s"
    return 1
  fi
  if ((rc != 0)); then
    err "media: archiving FAILED"
    show_lines "$ERR_FILE"
    return 1
  fi
  if [[ -s "$ERR_FILE" ]]; then
    warn "media: tar printed warnings:"
    show_lines "$ERR_FILE" WARN
  fi

  if ! gzip -t "$STAGE_TMP" 2>"$ERR_FILE"; then
    err "media: verification FAILED - the archive is not a valid gzip file"
    show_lines "$ERR_FILE"
    return 1
  fi
  if ! entries="$(tar -tzf "$STAGE_TMP" 2>"$ERR_FILE" | wc -l | tr -d ' ')"; then
    err "media: verification FAILED - the archive cannot be listed"
    show_lines "$ERR_FILE"
    return 1
  fi
  info "media: verified - archive lists $entries entries ($(human_size "$STAGE_TMP"))"
  # A volume that holds only its top folder ("./") is valid but suspicious: the
  # app creates profile_pictures/ at start-up, so a real volume has more.
  if ((entries <= 1)) && [[ "$ALLOW_EMPTY_MEDIA" != 1 ]]; then
    suspect_add "${final##*/}" "media: the archive holds only $entries entry - an empty or wrong volume? (set ALLOW_EMPTY_MEDIA=1 if that is expected)"
  fi

  if [[ -e "$final" ]]; then
    err "media: $final already exists - refusing to overwrite it"
    return 1
  fi
  mv "$STAGE_TMP" "$final" || return 1
  info "media: OK $final ($(human_size "$final"), $((SECONDS - t0))s)"
  summary_add "media=$(human_size "$final" | tr -d ' ') entries=$entries"
}

# Reports this run's "valid but suspicious" findings as a failure (see stage_db)
# and remembers which files were flagged (they never serve as a size yardstick).
check_suspects() {
  local name msg
  if [[ -s "$SUSPECT_FILE" ]]; then
    while IFS=$'\t' read -r name msg; do
      err "sanity: $msg"
      if [[ -n "$name" ]] && ! is_flagged "$name"; then
        printf '%s\n' "$name" >>"$SUSPECT_LIST" || warn "could not record $name in $SUSPECT_LIST"
      fi
    done <"$SUSPECT_FILE"
    err "sanity: the new files were kept (they are valid archives) but this run is reported as FAILED so that you look at it; old backups are not pruned"
    FAILURES="$FAILURES sanity"
  fi
}

# -----------------------------------------------------------------------------
# Monthly copy: the first good run of each month is also kept in <BACKUP_ROOT>/monthly
# (hard-linked, so it costs no extra space until the nightly file is pruned).
# Uploaded and pruned separately, so 14 bad nights cannot push out every good backup.
# -----------------------------------------------------------------------------
copy_monthly() { # copy_monthly SOURCE DEST
  local src="$1" dst="$2" tmp
  if ln "$src" "$dst" 2>/dev/null; then
    return 0
  fi
  tmp="$(mktemp "$MONTHLY_DIR/.partial-monthly-$RUN_STAMP.XXXXXX")"
  cp -p "$src" "$tmp"
  mv "$tmp" "$dst"
}

stage_monthly() {
  local month="${RUN_STAMP:0:6}" have=0
  if ((KEEP_MONTHLY == 0)); then
    return 0
  fi
  if [[ -s "$NOMONTHLY_FILE" ]]; then
    warn "monthly: no monthly copy from this run - its size could not be checked against a trustworthy earlier dump"
    return 0
  fi
  if ! mkdir -p "$MONTHLY_DIR"; then
    err "monthly: cannot create $MONTHLY_DIR"
    return 1
  fi
  have="$(list_names "$MONTHLY_DIR" '*' '^spine-(db|media)-[0-9]{8}T[0-9]{6}Z\.(dump|tar\.gz)$' | grep -c -E "^spine-(db|media)-$month" || true)"
  if ((have > 0)); then
    info "monthly: this month ($month) already has a monthly copy"
    return 0
  fi
  copy_monthly "$NIGHTLY_DIR/spine-db-$RUN_STAMP.dump" "$MONTHLY_DIR/spine-db-$RUN_STAMP.dump"
  copy_monthly "$NIGHTLY_DIR/spine-media-$RUN_STAMP.tar.gz" "$MONTHLY_DIR/spine-media-$RUN_STAMP.tar.gz"
  info "monthly: kept this run as the monthly copy for $month in $MONTHLY_DIR"
  summary_add "monthly=created"
}

# -----------------------------------------------------------------------------
# Off-site: rclone copy (never sync - a deleted local file must not delete the
# off-site copy; the R2 lifecycle rule does the expiring) + rclone check.
# -----------------------------------------------------------------------------
offsite_sync() { # offsite_sync LABEL SOURCE_DIR DESTINATION
  local label="$1" src="$2" dest="$3" rc=0
  local filters=(--include '/spine-db-*.dump' --include '/spine-media-*.tar.gz')

  info "off-site: uploading the $label files from $src to $dest"
  run_timeout "$STEP_TIMEOUT_SECONDS" rclone copy "$src" "$dest" "${filters[@]}" \
    --s3-no-check-bucket --stats 0 --log-level INFO 2>"$ERR_FILE" || rc=$?
  normalize_rclone_log "$ERR_FILE"
  if ((rc == 124)); then
    err "off-site: the $label upload timed out after ${STEP_TIMEOUT_SECONDS}s"
    return 1
  fi
  if ((rc != 0)); then
    err "off-site: the $label upload FAILED"
    show_lines "$ERR_FILE"
    return 1
  fi
  show_lines "$ERR_FILE" INFO 20

  info "off-site: verifying the uploaded $label copies (size and checksum)"
  rc=0
  run_timeout "$STEP_TIMEOUT_SECONDS" rclone check "$src" "$dest" "${filters[@]}" --one-way \
    --s3-no-check-bucket --log-level NOTICE 2>"$ERR_FILE" || rc=$?
  normalize_rclone_log "$ERR_FILE"
  if ((rc == 124)); then
    err "off-site: verifying the $label copies timed out after ${STEP_TIMEOUT_SECONDS}s"
    return 1
  fi
  if ((rc != 0)); then
    err "off-site: verification FAILED - the $label copy in R2 does not match the local file"
    show_lines "$ERR_FILE"
    return 1
  fi
}

stage_offsite() {
  local t0=$SECONDS

  if [[ -z "$RCLONE_REMOTE" && -z "$R2_BUCKET" ]]; then
    if [[ "$REQUIRE_OFFSITE" == 1 ]]; then
      err "off-site upload is REQUIRED (REQUIRE_OFFSITE=1) but R2 is not configured - set RCLONE_REMOTE and R2_BUCKET in $CONFIG_FILE"
      summary_add "offsite=MISSING(required)"
      return 1
    fi
    skip "off-site upload: R2 is not configured (set RCLONE_REMOTE and R2_BUCKET in $CONFIG_FILE to enable it)"
    summary_add "offsite=SKIPPED(not_configured)"
    return 0
  fi
  if [[ -z "$RCLONE_REMOTE" || -z "$R2_BUCKET" ]]; then
    err "off-site upload: R2 is only half configured - set BOTH RCLONE_REMOTE and R2_BUCKET (or clear both) in $CONFIG_FILE"
    summary_add "offsite=FAILED(half_configured)"
    return 1
  fi
  if ! command -v rclone >/dev/null 2>&1; then
    err "off-site upload: rclone is not installed (run: brew install rclone) - looked in PATH=$PATH"
    summary_add "offsite=FAILED(no_rclone)"
    return 1
  fi

  offsite_sync "nightly" "$NIGHTLY_DIR" "${RCLONE_REMOTE}:${R2_BUCKET}${R2_PREFIX:+/$R2_PREFIX}" || return 1
  if ((KEEP_MONTHLY > 0)) && [[ -n "$(list_names "$MONTHLY_DIR" '*' '^spine-(db|media)-[0-9]{8}T[0-9]{6}Z\.(dump|tar\.gz)$' | sed -n '1p')" ]]; then
    offsite_sync "monthly" "$MONTHLY_DIR" "${RCLONE_REMOTE}:${R2_BUCKET}${R2_MONTHLY_PREFIX:+/$R2_MONTHLY_PREFIX}" || return 1
  fi
  info "off-site: OK - every local backup file is present in R2 and matches ($((SECONDS - t0))s)"
  summary_add "offsite=verified"
}

# -----------------------------------------------------------------------------
# Retention: keep the newest N of each kind. Only files whose names exactly
# match what this script creates are ever considered - anything else in the
# folder (and the deploy workflow's pre-deploy dumps one level up) is left alone.
# Safety rules, learned the hard way:
#   * files created by THIS run are never deleted, whatever the numbers say;
#   * if this run's timestamp sorts BEFORE an existing file, the clock is wrong
#     and "oldest" cannot be trusted, so nothing is deleted and the run fails.
# -----------------------------------------------------------------------------
prune_kind() { # prune_kind DIR LABEL GLOB REGEX KEEP
  local dir="$1" label="$2" glob="$3" re="$4" keep="$5"
  local names=() name i total removed=0 newest stamp
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    names[${#names[@]}]="$name"
  done < <(list_names "$dir" "$glob" "$re")
  total=${#names[@]}
  if ((total > 0)); then
    newest="$(newest_other "$dir" "$glob" "$re")"
    if [[ -n "$newest" ]]; then
      stamp="${newest#spine-db-}"
      stamp="${stamp#spine-media-}"
      stamp="${stamp%%.*}"
      if [[ "$RUN_STAMP" < "$stamp" ]]; then
        err "prune: the system clock looks wrong: this run is stamped $RUN_STAMP but $newest in $dir is newer - NOT deleting anything"
        return 1
      fi
    fi
  fi
  # Names embed a UTC timestamp, so the order is oldest first, and the files
  # outside the newest KEEP are the first total-KEEP names. Files of this run are
  # skipped WITHOUT making up for them by deleting newer ones.
  for ((i = 0; i < total - keep; i++)); do
    name="${names[$i]}"
    if [[ "$name" == *"$RUN_STAMP"* ]]; then
      continue
    fi
    rm -f -- "$dir/$name"
    info "prune: removed old $label $name"
    removed=$((removed + 1))
  done
  info "prune: $label - $((total - removed)) kept, $removed removed (limit $keep)"
}

# Forget flagged files that no longer exist (they were pruned).
tidy_suspect_list() {
  local tmp name
  [[ -f "$SUSPECT_LIST" ]] || return 0
  tmp="$(mktemp "$NIGHTLY_DIR/.partial-suspects-$RUN_STAMP.XXXXXX")"
  while IFS= read -r name; do
    if [[ -n "$name" && -e "$NIGHTLY_DIR/$name" ]]; then
      printf '%s\n' "$name"
    fi
  done <"$SUSPECT_LIST" >"$tmp" || true
  mv "$tmp" "$SUSPECT_LIST"
}

stage_prune() {
  local failed=0
  prune_kind "$NIGHTLY_DIR" "database dump" 'spine-db-*.dump' "$DB_FILE_RE" "$KEEP_DB_DUMPS" || failed=1
  prune_kind "$NIGHTLY_DIR" "media archive" 'spine-media-*.tar.gz' "$MEDIA_FILE_RE" "$KEEP_MEDIA_ARCHIVES" || failed=1
  if ((KEEP_MONTHLY > 0)) && [[ -d "$MONTHLY_DIR" ]]; then
    prune_kind "$MONTHLY_DIR" "monthly database dump" 'spine-db-*.dump' "$DB_FILE_RE" "$KEEP_MONTHLY" || failed=1
    prune_kind "$MONTHLY_DIR" "monthly media archive" 'spine-media-*.tar.gz' "$MEDIA_FILE_RE" "$KEEP_MONTHLY" || failed=1
  fi
  tidy_suspect_list
  return "$failed"
}

# -----------------------------------------------------------------------------
# main: runs as a background job (see the bottom of the file), so everything it
# prints is also captured for the healthchecks.io failure ping.
# -----------------------------------------------------------------------------
main() {
  set -euo pipefail
  SECONDS=0

  info "=== Spine backup starting (run id $RUN_STAMP) ==="
  info "config: $CONFIG_SOURCE"
  if ((HAVE_PERL == 0)); then
    warn "perl was not found: the time limits on single docker/rclone calls are switched off (the overall MAX_RUN_SECONDS limit still applies)"
  fi
  if [[ -n "$CONFIG_WARN" ]]; then
    warn "$CONFIG_WARN"
    summary_add "warning=config_file_mode"
  fi
  if [[ -n "$CONFIG_ERRORS" ]]; then
    printf '%s\n' "$CONFIG_ERRORS" | while IFS= read -r line; do err "$line"; done
    err "=== Backup FAILED: fix the configuration above ==="
    return 2
  fi

  # (The lock was taken by the supervisor before this worker started, so a run
  # that is refused because another backup is in progress never gets here and
  # never sends a "/start" ping.)
  if ((ACCEPT_IN_FILE == 1)); then
    warn "ACCEPT_SMALLER_DB in the config file is ignored: it is for a single run, so give it on the command line (ACCEPT_SMALLER_DB=1 backup-production.sh)"
  fi
  if [[ "$ACCEPT_SMALLER_DB" == 1 ]]; then
    warn "ACCEPT_SMALLER_DB=1: the size check is skipped for this run, and its dump becomes the new size yardstick"
  fi

  hc_ping /start
  if [[ -z "$HEALTHCHECK_URL" ]]; then
    skip "healthchecks.io: HEALTHCHECK_URL is not set - monitoring pings skipped"
  fi

  run_stage preflight stage_preflight
  if ((STAGE_RC != 0)); then
    err "=== Backup FAILED before anything was written (${SECONDS}s) ==="
    return 1
  fi

  run_stage db stage_db
  run_stage media stage_media
  check_suspects

  if [[ -z "$FAILURES" ]]; then
    run_stage monthly stage_monthly
  fi

  # Off-site comes BEFORE pruning, and runs even after a local failure: it only
  # uploads what exists, and it catches up on any earlier night that did not go
  # through.
  run_stage offsite stage_offsite

  if [[ -z "$FAILURES" ]]; then
    run_stage prune stage_prune
  else
    warn "prune: skipped because an earlier stage failed (old backups are left untouched)"
  fi

  if [[ -n "$FAILURES" ]]; then
    err "=== Backup FAILED (${SECONDS}s). Failed stages:$FAILURES ==="
    return 1
  fi
  info "=== Backup finished OK (${SECONDS}s) ==="
}

# The worker: runs main and records its exit status in a file (the status of a
# background pipeline is not otherwise available to the supervisor). If the
# supervisor has died in the meantime nobody is left to read the result, so the
# worker cleans up after the run instead.
worker_exit_trap() {
  local rc=$?
  if supervisor_gone; then
    ORPHANED=1
    rm -f "$RUN_LOG" "$ERR_FILE" "$SCRATCH_FILE" "$SUMMARY_FILE" "$SUSPECT_FILE" "$NOMONTHLY_FILE" "$RC_FILE" 2>/dev/null || true
    remove_this_runs_partials
  else
    echo "$rc" >"$RC_FILE"
  fi
}
run_worker() {
  trap worker_exit_trap EXIT
  set +e
  main
  exit $?
}

# -----------------------------------------------------------------------------
# Entry point (the "supervisor"). It takes the lock, then runs the worker as a
# background job in its OWN process group, and keeps polling instead of sitting
# in a foreground wait: bash holds a signal trap back while it waits for a
# foreground command, so a plain `main | tee` would ignore SIGTERM (launchd's
# "stop") until the backup finished - and then launchd would SIGKILL it, leaving
# half-written files. Polling every half second means TERM/INT/HUP - and the
# overall time limit - are acted on within a second: the whole worker group is
# told to stop, then killed if it does not.
# If the supervisor itself is ever killed outright (SIGKILL), the worker notices
# within seconds (supervisor_gone), cleans up and stops, and the lock it inherited
# keeps any new run out until then.
# -----------------------------------------------------------------------------
RUN_LOG="$(mktemp "$TMP_BASE/spine-backup-run.XXXXXX")"
ERR_FILE="$(mktemp "$TMP_BASE/spine-backup-err.XXXXXX")"
SCRATCH_FILE="$(mktemp "$TMP_BASE/spine-backup-scratch.XXXXXX")"
SUMMARY_FILE="$(mktemp "$TMP_BASE/spine-backup-summary.XXXXXX")"
SUSPECT_FILE="$(mktemp "$TMP_BASE/spine-backup-suspect.XXXXXX")"
NOMONTHLY_FILE="$(mktemp "$TMP_BASE/spine-backup-nomonthly.XXXXXX")"
RC_FILE="$(mktemp "$TMP_BASE/spine-backup-rc.XXXXXX")"

outer_log() { # a log line from the supervisor: to the screen AND to the run log
  local line
  line="$(log "$1" "$2")"
  (printf '%s\n' "$line" >>"$RUN_LOG") 2>/dev/null || true
  (printf '%s\n' "$line") 2>/dev/null || true
}

# A `tee` that a closed screen cannot kill: if the terminal is closed or the
# output is piped into `head`, the screen copy is dropped, but the run log (which
# feeds the failure ping) keeps receiving every line.
tee_log() {
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    (printf '%s\n' "$line" >>"$RUN_LOG") 2>/dev/null || true
    (printf '%s\n' "$line") 2>/dev/null || true
  done
}

cleanup_outer() {
  exec >/dev/null 2>&1 # nobody can be told anything now, and nothing below may fail on a dead output
  rm -f "$RUN_LOG" "$ERR_FILE" "$SCRATCH_FILE" "$SUMMARY_FILE" "$SUSPECT_FILE" "$NOMONTHLY_FILE" "$RC_FILE"
  release_lock_if_mine
}
trap cleanup_outer EXIT

TERM_REQ=""
trap 'TERM_REQ=TERM' TERM
trap 'TERM_REQ=INT' INT
trap 'TERM_REQ=HUP' HUP

# Keep the Mac from falling asleep while a backup is running (macOS only). The
# helper exits by itself when this script's pid does, so it can never linger.
# (8>&-: it must not inherit the lock descriptor and so keep the lock alive.)
if command -v caffeinate >/dev/null 2>&1; then
  caffeinate -i -w $$ >/dev/null 2>&1 8>&- &
fi

WATCHDOG_SECONDS="$MAX_RUN_SECONDS"
if [[ ! "$WATCHDOG_SECONDS" =~ ^[1-9][0-9]{0,6}$ ]]; then
  WATCHDOG_SECONDS=10800 # the value in the config is invalid; main reports that
fi

REASON=""
rc=0
LIVE_HOLDER=0
START_WORKER=1
if [[ -n "$TERM_REQ" ]]; then
  exit 143
fi

# Leftovers of a run that was killed outright (SIGKILL, power cut) cannot remove
# their own temporary files. We hold the lock, so nothing of an earlier run is
# still working; only the final ping of the run that just ended can still be using
# a few files, and those are seconds old (hence the hour).
sweep_stale_temp_files() {
  find "$TMP_BASE" -maxdepth 1 -type f -name 'spine-backup-*' -mmin +60 -delete 2>/dev/null || true
}

# Step 1 of the run: the backup folder and the lock. A configuration error is
# reported by the worker (exit 2) and needs no lock. Only a lock that a LIVE run
# really holds ends the run quietly (exit 75, no ping); a lock that cannot be used
# at all is a failure like any other (exit 1, "/fail" ping).
if [[ -z "$CONFIG_ERRORS" ]]; then
  if ! mkdir -p "$BACKUP_ROOT" 2>/dev/null; then
    outer_log ERROR "cannot create $BACKUP_ROOT"
    outer_log ERROR "=== Backup FAILED before anything was written ==="
    rc=1
    START_WORKER=0
  else
    lock_rc=0
    acquire_lock || lock_rc=$?
    case "$lock_rc" in
      0) sweep_stale_temp_files ;;
      75)
        LIVE_HOLDER=1
        rc=75
        START_WORKER=0
        ;;
      *)
        outer_log ERROR "=== Backup FAILED before anything was written: the lock could not be used ==="
        rc=1
        START_WORKER=0
        ;;
    esac
  fi
fi

if ((START_WORKER == 1)); then
  SUPERVISOR_PGID="$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' ' || true)"
  set -m # job control: the worker gets its own process group
  run_worker 2>&1 | tee_log &
  TEE_PID=$!
  WORKER_PGID="$(ps -o pgid= -p "$TEE_PID" 2>/dev/null | tr -d ' ' || true)"
  SECONDS=0
  KILL_AT=""
  DONE_AT=""
  {
    while [[ -n "$WORKER_PGID" ]] && kill -0 -- "-$WORKER_PGID" 2>/dev/null; do
      # The worker has reported its result but something in its process group is
      # still alive (it should be gone within milliseconds): give it 10 seconds to
      # drain the log, then stop waiting for it.
      if [[ -s "$RC_FILE" && -z "$REASON" ]]; then
        if [[ -z "$DONE_AT" ]]; then
          DONE_AT=$SECONDS
        elif ((SECONDS - DONE_AT >= 10)); then
          kill -KILL -- "-$WORKER_PGID" 2>/dev/null || true
        fi
      fi
      if [[ -z "$REASON" ]]; then
        if [[ -n "$TERM_REQ" ]]; then
          REASON="signal $TERM_REQ"
        elif ((SECONDS >= WATCHDOG_SECONDS)); then
          REASON="the time limit of ${WATCHDOG_SECONDS}s (MAX_RUN_SECONDS) was reached"
        fi
        if [[ -n "$REASON" ]]; then
          kill -TERM -- "-$WORKER_PGID" 2>/dev/null || true
          KILL_AT=$((SECONDS + 10))
        fi
      elif [[ -n "$KILL_AT" ]] && ((SECONDS >= KILL_AT)); then
        kill -KILL -- "-$WORKER_PGID" 2>/dev/null || true
        KILL_AT=""
      fi
      sleep 0.5
    done
  } 2>/dev/null
  wait "$TEE_PID" 2>/dev/null || true
  set +m

  rc="$(cat "$RC_FILE" 2>/dev/null || true)"
  if [[ -n "$REASON" ]]; then
    remove_this_runs_partials
    case "$REASON" in
      "signal TERM") rc=143 ;;
      "signal INT") rc=130 ;;
      "signal HUP") rc=129 ;;
      *) rc=124 ;;
    esac
    outer_log ERROR "=== Backup INTERRUPTED by $REASON; temporary files removed ==="
  elif [[ ! "$rc" =~ ^[0-9]+$ ]]; then
    rc=1
    outer_log ERROR "=== Backup worker ended without reporting a result ==="
  fi
fi

# Free the lock before talking to the network, so a slow ping never blocks the
# next run.
release_lock_if_mine

if ((LIVE_HOLDER == 1)); then
  : # another backup really was running: nothing happened, nothing to report
elif ((rc == 0)); then
  body_file="$(mktemp "$TMP_BASE/spine-backup-body.XXXXXX")"
  printf 'OK run=%s %s\n' "$RUN_STAMP" "$(tr '\n' ' ' <"$SUMMARY_FILE")" >"$body_file"
  hc_ping "" "$body_file"
  rm -f "$body_file"
else
  body_file="$(mktemp "$TMP_BASE/spine-backup-body.XXXXXX")"
  tail -n 40 "$RUN_LOG" | tail -c 20000 | scrub_stream >"$body_file"
  fast=""
  if [[ -n "$REASON" ]]; then
    fast=1 # being stopped (maybe shut down): do not spend long on the network
  fi
  hc_ping /fail "$body_file" "$fast"
  rm -f "$body_file"
fi
exit "$rc"
