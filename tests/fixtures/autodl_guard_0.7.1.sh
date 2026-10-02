#!/usr/bin/env bash
# autodl_guard.sh - instance-side guard of the autodl-gpu skill.
#
# It only decides WHEN to call AutoDL's official shutdown command
# (/usr/bin/shutdown, see https://www.autodl.com/docs/save_money/). It runs inside the
# instance, so it does not depend on SSH, whose connections AutoDL's public ports
# sometimes drop before authentication.
# Rules checked by `tick` (the daemon runs it every interval), in this order:
#   1. deadline reached                          -> shut down, even if work is running
#   2. a shutdown is pending (issued, maybe failed) -> retry it; an idle-type one is
#      cancelled instead if something runs again
#   3. off-when-done requested and nothing runs  -> shut down
#   4. nothing runs and the keep period is over  -> shut down
# "Something runs" = a registered job (its runner, any live process of its process group,
# or any live process that carries the job's tag in its environment, which setsid or a
# double fork does not drop) is alive, any screen/tmux session other than the guard's own
# exists (unreadable session lists count too), or (GPU mode with --util-signal) a recent
# GPU utilization sample is above the threshold or unreadable. Processes are read from /proc
# with bash builtins only; a pass over /proc that cannot read even its own entries counts every
# registered job as maybe running, and so does a job file that is there but cannot be read, a
# session list that cannot be parsed, or a busy check that dies part way.
# 0.7.1 adds a read-only "sample" command that prints the activity counters 0.8 will judge idleness by.
# 0.7.1 also keeps a sync that hangs in the kernel from holding up a shutdown (flush_disks).
set -u

VERSION="0.7.1"
GH="${AUTODL_GUARD_HOME:-/root/autodl-tmp/.autodl-guard}"
ST="$GH/state"
JOBS="$GH/jobs"
LOG="$GH/guard.log"
SELF="$(readlink -f "$0" 2> /dev/null || printf '%s' "$0")"
SHUTDOWN_CMD="${AUTODL_SHUTDOWN_CMD:-/usr/bin/shutdown}"
SYNC_CMD="${AUTODL_SYNC_CMD:-sync}"   # tests only: a stand-in for sync
SYNC_WAIT="${AUTODL_SYNC_WAIT:-60}"   # seconds a shutdown waits for sync before it goes ahead without it
SCREEN_CMD="${AUTODL_SCREEN_CMD:-screen}"
TMUX_CMD="${AUTODL_TMUX_CMD:-tmux}"
NVSMI_CMD="${AUTODL_NVIDIA_SMI_CMD:-nvidia-smi}"
PROBE_TIMEOUT="${AUTODL_PROBE_TIMEOUT:-10}"
FLOCK_CMD="${AUTODL_FLOCK_CMD:-flock}"
PROC="${AUTODL_TEST_PROC:-/proc}"   # tests only: a stand-in for /proc
CG_DIR="${AUTODL_TEST_CGROUP_DIR:-/sys/fs/cgroup}"   # tests only: a stand-in for the cgroup v2 mount
NET_DEV="${AUTODL_TEST_NET_DEV:-/proc/net/dev}"      # tests only: a stand-in for /proc/net/dev
UPTIME_FILE="${AUTODL_TEST_UPTIME:-/proc/uptime}"   # tests only: a stand-in for /proc/uptime
GUARD_SESSION="autodl-guard"
JOB_PREFIX="aj-"
JOB_NAME_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
NOGPU_MEM_MAX=3221225472   # 3 GiB; AutoDL non-GPU mode is capped at 2 GiB
MAX_DURATION=2592000       # 30 days
MIN_EPOCH=1577836800       # 2020-01-01: a stored time before this is corrupt
FIRED=10                   # tick's exit status when it issued a shutdown
E_PENDING=4                # command refused: a shutdown is pending
E_ARMED=5                  # arm refused: already armed in this boot
E_UNCERTAIN=6              # run: whether the job started cannot be told (check status)

now() { printf '%(%s)T' -1; }   # bash builtins, no date process
ts() { printf '%(%Y-%m-%d %H:%M:%S %z)T' -1; }
log() { printf '%s %s\n' "$(ts)" "$*" >> "$LOG"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
fail() { local code="$1"; shift; printf 'error: %s\n' "$*" >&2; exit "$code"; }
have() { command -v "$1" > /dev/null 2>&1; }
need2() { [ "$1" -ge 2 ] || die "option '$2' needs a value"; }
is_uint() { [[ "${1:-}" =~ ^(0|[1-9][0-9]{0,11})$ ]]; }   # plain decimal (no leading zero: $(( )) reads that as octal), short enough for shell arithmetic
is_counter() { [[ "${1:-}" =~ ^(0|[1-9][0-9]{0,17})$ ]]; }   # a kernel byte or time counter: plain decimal that fits 64-bit arithmetic
can_read() { { : < "$1"; } 2> /dev/null; }                     # can_read PATH: the file opens for reading
probe() {  # run an external probe with a time limit; 124 = timed out. It holds none of the locks
  if have timeout; then timeout "$PROBE_TIMEOUT" "$@" 6>&- 8>&- 9>&-; else "$@" 6>&- 8>&- 9>&-; fi
}
unlocked() {  # unlocked FUNC [ARGS]: for use inside $(...) only. That subshell first drops the lock
  # descriptors, so nothing it starts (a probe can hang) keeps a lock, or makes a killed daemon look alive
  if [ "$BASHPID" != "$$" ]; then exec 6>&- 8>&- 9>&-; fi
  "$@"
}
nolock() { "$@" 6>&- 8>&- 9>&-; }   # nolock CMD ARGS: an external command that inherits none of the locks
# Small files are read with bash builtins: no cat that could fail, or inherit a lock
fread() {  # fread PATH: print the file's contents, nothing if it cannot be read
  local v=""
  { IFS= read -r -d '' v < "$1"; } 2> /dev/null
  printf '%s' "$v"
}
jread() {  # jread VAR PATH: VAR = the contents of a small file. 0 = read, 1 = no such file,
  # 2 = the file is there but cannot be opened
  local _v=""
  printf -v "$1" '%s' ""
  [ -e "$2" ] || return 1
  { : < "$2"; } 2> /dev/null || return 2
  { IFS= read -r -d '' _v < "$2"; } 2> /dev/null
  printf -v "$1" '%s' "$_v"
}

get() {  # get NAME [DEFAULT]: one state value per file
  local v=""
  [ -f "$ST/$1" ] && { IFS= read -r -d '' v < "$ST/$1"; } 2> /dev/null
  if [ -n "$v" ]; then printf '%s' "$v"; else printf '%s' "${2:-}"; fi
}
num() {  # num NAME DEFAULT: non-negative integer state; prints BAD (and logs) if corrupt
  local v
  v="$(get "$1" "")"
  if [ -z "$v" ]; then
    printf '%s' "$2"
  elif is_uint "$v"; then
    printf '%s' "$v"
  else
    log "STATE CORRUPT $1=[$v]"
    printf 'BAD'
  fi
}
epoch_state() {  # epoch_state NAME [DEFAULT]: a stored unix time, 0 if unset; BAD (and logged) if implausible
  local v
  v="$(num "$1" "${2:-0}")"
  # a week of slack above the longest duration: a clock set back a few days must not void a valid deadline
  if [ "$v" != BAD ] && [ "$v" != 0 ] && { [ "$v" -lt "$MIN_EPOCH" ] || [ "$v" -gt $(($(now) + MAX_DURATION + 604800)) ]; }; then
    log "STATE CORRUPT $1=[$v] (not a plausible time)"
    v=BAD
  fi
  printf '%s' "$v"
}
deadline_state() {  # the deadline; BAD if implausible or earlier than the arm that set it
  local d a
  d="$(epoch_state deadline)"
  a="$(epoch_state armed_at)"
  if [ "$d" != BAD ] && [ "$d" != 0 ] && [ "$a" != BAD ] && [ "$a" != 0 ] && [ "$d" -lt "$a" ]; then
    log "STATE CORRUPT deadline=[$d] (before armed_at=$a)"
    d=BAD
  fi
  printf '%s' "$d"
}
bounded() {  # bounded NAME DEFAULT MIN MAX: integer state clamped to a sane range
  local v
  v="$(num "$1" "$2")"
  if ! is_uint "$v" || [ "$v" -lt "$3" ] || [ "$v" -gt "$4" ]; then v="$2"; fi
  echo "$v"
}
put() {  # put NAME VALUE: atomic replace, non-zero on failure
  local tmp="$ST/.$1.$$"
  if [ -n "${AUTODL_TEST_FAIL_PUT:-}" ] && [ "$1" = "$AUTODL_TEST_FAIL_PUT" ]; then return 1; fi   # tests only
  printf '%s' "$2" > "$tmp" 2> /dev/null && nolock mv -f "$tmp" "$ST/$1" 2> /dev/null
}
putx() { put "$1" "$2" || die "cannot write state '$1' (disk full?)"; }
write_file() {  # write_file PATH VALUE: atomic replace of a job file
  local tmp="$1.$$"
  if [ -n "${AUTODL_TEST_FAIL_JOBFILE:-}" ] && [ "${1##*/}" = "$AUTODL_TEST_FAIL_JOBFILE" ]; then return 1; fi   # tests only
  printf '%s' "$2" > "$tmp" && nolock mv -f "$tmp" "$1"
}

to_seconds() {  # 90s | 30m | 2h | 45 (plain number = minutes); at most 30 days
  local d="$1" n s
  [[ "$d" =~ ^[0-9]{1,7}[smh]?$ ]] || die "bad duration '$d' (use 90s, 30m, 2h or plain minutes)"
  n="${d%[smh]}"
  case "$d" in
    *s) s=$((10#$n)) ;;
    *h) s=$((10#$n * 3600)) ;;
    *) s=$((10#$n * 60)) ;;
  esac
  [ "$s" -le "$MAX_DURATION" ] || die "duration '$d' is longer than 30 days"
  echo "$s"
}

# One lock around every read-modify-write of the state. flock is required (main checks it):
# without it every lock here would silently vanish. A flock process waiting for one lock
# holds none of the other descriptors, so it neither keeps a lock after its caller died nor
# makes a killed daemon look alive.
lock() {
  exec 9> "$ST/.lock" || die "cannot open the state lock"
  "$FLOCK_CMD" 9 6>&- 8>&- || die "cannot take the state lock"
}
unlock() { "$FLOCK_CMD" -u 9 6>&- 8>&-; exec 9>&-; }
# A second lock, taken before the state lock and never by the daemon: it keeps a daemon
# restart apart from commands that rely on a running daemon, without the deadlock of
# stopping a daemon that waits in tick for the state lock.
life_lock() {
  exec 6> "$ST/.life.lock" || die "cannot open the lifecycle lock"
  "$FLOCK_CMD" 6 8>&- 9>&- || die "cannot take the lifecycle lock"
}
life_unlock() { "$FLOCK_CMD" -u 6 8>&- 9>&-; exec 6>&-; }

# ---- part 2: processes, jobs, sessions, GPU ----
# /proc is read with bash builtins only: no external program that could fail halfway through
# decides whether something still runs.
proc_stat() {  # proc_stat PID: sets P_STATE, P_PGRP and P_START from $PROC/PID/stat; 1 if unreadable
  local line=""
  P_STATE="" P_PGRP="" P_START=""
  { IFS= read -r -d '' line < "$PROC/$1/stat"; } 2> /dev/null   # the whole file: a command name may hold a newline
  case "$line" in *") "*) ;; *) return 1 ;; esac
  set -- ${line##*) }   # the fields after the command name: numbers and one state letter, nothing to glob
  [ $# -ge 20 ] || return 1
  P_STATE="$1" P_PGRP="$3" P_START="${20}"
}
proc_start() { proc_stat "$1" && printf '%s' "$P_START"; }
boot_init() {  # BOOT = start time of PID 1, which changes every time the container starts; unknown if unreadable
  if [ -n "${AUTODL_BOOT_MARKER:-}" ]; then BOOT="$AUTODL_BOOT_MARKER"; elif proc_stat 1; then BOOT="$P_START"; else BOOT=unknown; fi
}
# armed for this boot by an arm that finished: arm_incomplete exists from the start of an arm to its end.
# A boot that cannot be told apart from others never counts as armed
armed_now() { [ "$BOOT" != unknown ] && [ "$(get armed_boot '')" = "$BOOT" ] && [ ! -e "$ST/arm_incomplete" ]; }
is_dry() { [ "$(get dry_run 0)" = 1 ] && armed_now; }   # an arm cut short is never a dry run

runner_alive() {  # runner_alive PID START: the same, non-zombie process still runs (in doubt: yes)
  is_uint "$1" && [ "$1" -gt 1 ] || return 1
  kill -0 "$1" 2> /dev/null || return 1
  proc_stat "$1" || return 0   # nothing to compare in /proc (not Linux): trust kill -0
  [ "$P_STATE" != Z ] || return 1
  [ -z "$2" ] || [ "$P_START" = "$2" ]
}
same_process() {  # same_process PID START: PID is, verifiably, the process that started at START (in doubt: no).
  # Only this may decide whether to send a signal: a stale record can name someone else's process
  is_uint "$1" && [ "$1" -gt 1 ] && is_uint "$2" && proc_stat "$1" && [ "$P_STATE" != Z ] && [ "$P_START" = "$2" ]
}

# One pass over /proc per check (clear PROC_OK and the next check passes again): the process
# groups with a live member, and the job tags in the processes' environments. A process whose
# environ cannot be read is skipped: sshd makes its processes non-dumpable, and scanners keep
# sshd forking, so counting those as work would keep every instance on. A pass that cannot read
# even this shell's own entries proves nothing: every registered job then counts as maybe running.
PGIDS=" "
TAGS=""
PROC_OK=""
scan_proc() {
  local f pid line self=0
  PGIDS=" " TAGS="" PROC_OK=unknown
  [ -d "$PROC/1" ] || return 0
  for f in "$PROC"/[0-9]*; do
    pid="${f##*/}"
    proc_stat "$pid" || continue
    [ "$P_STATE" = Z ] || PGIDS+="$P_PGRP "
    { while IFS= read -r -d '' line || [ -n "$line" ]; do
        case "$line" in AUTODL_GUARD_JOB=?*) TAGS+="${line#AUTODL_GUARD_JOB=}"$'\n' ;; esac
      done < "$f/environ"; } 2> /dev/null || continue
    [ "$pid" = "$BASHPID" ] && self=1
  done
  [ "$self" = 1 ] && PROC_OK=yes
  return 0
}
group_alive() {  # group_alive PGID: a live (non-zombie) process is in the group, or /proc cannot tell
  is_uint "$1" && [ "$1" -gt 1 ] || return 1
  if [ ! -d "$PROC/1" ]; then kill -0 -- "-$1" 2> /dev/null; return; fi
  [ -n "$PROC_OK" ] || scan_proc
  [ "$PROC_OK" = yes ] || return 0
  case "$PGIDS" in *" $1 "*) return 0 ;; esac
  return 1
}
tag_alive() {  # tag_alive TAG: a live process carries it in its environment, or /proc cannot tell
  [ -n "${1:-}" ] || return 1
  [ -n "$PROC_OK" ] || scan_proc
  [ "$PROC_OK" = yes ] || return 0
  case $'\n'"$TAGS" in *$'\n'"$1"$'\n'*) return 0 ;; esac
  return 1
}

job_running() {  # job_running DIR: 0 = it runs, or that cannot be told; 1 = it does not run.
  # A job file that is there but cannot be read, or holds no valid value, proves nothing: the job counts
  local d="$1" jb pg tg pid ps start
  jread jb "$d/boot"
  case $? in 1) return 1 ;; 2) return 0 ;; esac   # no boot file: the registration never finished, nothing started
  if [ "$BOOT" != unknown ]; then   # a boot marker that cannot be read proves nothing either
    [ -n "$jb" ] || return 0
    [ "$jb" = "$BOOT" ] || return 1   # an older boot: all its processes are gone
  fi
  # the runner writes end only after the job's group and tagged processes are all gone, so an
  # ended job stays ended even if its old group number is reused
  [ -f "$d/end" ] && return 1
  jread pg "$d/pgid"
  case $? in 2) return 0 ;; 0) is_uint "$pg" || return 0; group_alive "$pg" && return 0 ;; esac   # background children too
  jread tg "$d/tag"
  case $? in 2) return 0 ;; 0) [ -n "$tg" ] || return 0; tag_alive "$tg" && return 0 ;; esac   # and those that left the group
  jread pid "$d/pid"
  case $? in
    2) return 0 ;;
    0)
      is_uint "$pid" || return 0
      jread ps "$d/pstart"
      runner_alive "$pid" "$ps" && return 0
      return 1
      ;;
  esac
  jread start "$d/start" || return 0   # registered, runner not up yet
  is_uint "$start" || return 0
  [ $(($(now) - start)) -lt 120 ]
}

running_jobs() {  # job:NAME for every job that runs, or cannot be told not to
  local d
  for d in "$JOBS"/*/; do
    [ -d "$d" ] || continue
    d="${d%/}"
    job_running "$d" && printf 'job:%s\n' "${d##*/}"
  done
  return 0
}

other_sessions() {  # every screen/tmux session except the guard's own; unreadable lists count too
  local out rc
  if have "$SCREEN_CMD"; then
    out="$(probe "$SCREEN_CMD" -ls 2>&1)"
    rc=$?
    if [ "$rc" -eq 124 ]; then
      echo "screen:unknown"
    else
      printf '%s\n' "$out" | awk -v g="$(get guard_sty '')" '
        /No Sockets found/ { nosock = 1 }
        /^[ \t]+[0-9]+\./ {
          n++
          if ($0 ~ /Dead/) next
          if ($1 != g) { name = $1; sub(/^[0-9]+\./, "", name); print "screen:" name }
        }
        /[0-9]+ Sockets? in / { match($0, /[0-9]+ Socket/); c = substr($0, RSTART, RLENGTH) + 0; seen = 1 }
        END {
          if (seen && c != n) print "screen:unparsed"
          else if (!seen && n == 0 && !nosock) print "screen:unknown"
        }'
      [ "${PIPESTATUS[1]}" = 0 ] || echo "screen:unknown"   # the parser itself failed
    fi
  fi
  if have "$TMUX_CMD"; then
    out="$(probe "$TMUX_CMD" ls 2>&1)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
      printf '%s\n' "$out" | awk -F: 'NF > 1 { print "tmux:" $1 }'
      [ "${PIPESTATUS[1]}" = 0 ] || echo "tmux:unknown"
    elif [ "$rc" -eq 124 ] || ! printf '%s' "$out" | grep -qiE 'no server running|error connecting|no such file'; then
      echo "tmux:unknown"
    fi
  fi
  return 0
}

cgroup_mem_limit() {
  local f
  for f in "${AUTODL_CGROUP_MEM_FILE:-}" /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory/memory.limit_in_bytes; do
    if [ -n "$f" ] && [ -r "$f" ]; then head -n 1 "$f" 2> /dev/null; return; fi
  done
}

detect_mode() {  # gpu | nogpu | unknown (never guess nogpu from a failing nvidia-smi alone)
  local m
  if probe "$NVSMI_CMD" -L 2> /dev/null | grep -q '^GPU '; then echo gpu; return; fi
  m="$(cgroup_mem_limit)"
  if is_uint "$m" && [ "$m" -le "$NOGPU_MEM_MAX" ]; then echo nogpu; else echo unknown; fi
}

gpu_util_max() {  # highest utilization.gpu over all GPUs, empty if unavailable or timed out
  probe "$NVSMI_CMD" --query-gpu=utilization.gpu --format=csv,noheader,nounits 2> /dev/null |
    awk 'BEGIN { m = -1 } $1 ~ /^[0-9]+$/ { if ($1 + 0 > m) m = $1 + 0 } END { if (m >= 0) print m }'
}

busy_reasons() {  # one reason per line; empty output = idle. Run it through check_busy
  local s thr
  if [ -n "${AUTODL_TEST_BUSY_ABORT:-}" ]; then exit 254; fi   # tests only: this check dies (a failed fork does that)
  running_jobs
  other_sessions
  if [ "$(get util_signal 0)" = 1 ] && [ "$(get mode nogpu)" = gpu ]; then
    thr="$(bounded util_threshold 5 0 100)"
    if [ -n "${UTIL_UNSTORED:-}" ]; then printf 'gpu-util:unknown\n'; fi   # this tick's sample was lost
    for s in $(get util_recent ""); do
      if ! is_uint "$s"; then printf 'gpu-util:unknown\n'; break; fi
      if [ "$s" -gt "$thr" ]; then printf 'gpu-util:%s\n' "$s"; break; fi
    done
  fi
  return 0
}
check_busy() {  # BUSY = the reasons, one per line. The check runs in a subshell without the locks;
  # if it dies part way (a failed fork ends it at once), that counts as a reason too
  BUSY="$(unlocked busy_reasons)" || BUSY="${BUSY}${BUSY:+$'\n'}busy-check:unknown"
}
last_words() {  # last_words N STRING: the last N space-separated words of STRING (never glob-expanded)
  local n="$1"
  set -f
  set -- $2
  set +f
  if [ $# -gt "$n" ]; then shift $(($# - n)); fi
  printf '%s' "$*"
}

# ---- part 3: shutdown, tick, daemon ----
# Shutdown kinds: forced is retried unconditionally, deadline as long as the deadline is reached
# (step 1 of tick); idle, when-done and now (off-now without --force), and a deadline one whose
# deadline is no longer reached, are re-checked before each retry and cancelled if something runs.
request_shutdown() {  # request_shutdown REASON KIND (caller holds the lock)
  # the details first, the pending flag last: a retry never finds a pending shutdown without its kind
  if ! { put shutdown_reason "$1" && put shutdown_kind "$2" && put shutdown_attempts 0 &&
    put last_shutdown_reason "$1" && put shutdown_pending 1; }; then
    if [ "$2" != deadline ]; then
      log "cannot record the pending shutdown, not shutting down: $1"
      return 2
    fi
    log "cannot record the pending shutdown; shutting down anyway because the deadline is reached"
  fi
  attempt_shutdown
}

FLUSH_PID=""   # the last flush this process started; a retried shutdown starts no other while it runs
flush_disks() {  # before a shutdown: flush the filesystem the guard's log and state are on, waiting at most
  # SYNC_WAIT seconds. A sync stuck on a filesystem that does not answer (a network or FUSE mount gone bad)
  # sits in the kernel where no signal reaches it, timeout's included, so the shutdown goes ahead without
  # it, as it does when a healthy disk needs longer than that. The sync runs in a subshell without the lock
  # descriptors or the caller's output (an ssh channel would stay open). When sync -f fails, for one
  # because coreutils older than 8.24 have no -f, every filesystem is synced instead. A daemon retrying a
  # shutdown while its last flush still runs starts no other, so hanging syncs do not pile up
  local pid i wait_s="$SYNC_WAIT"
  is_uint "$wait_s" || wait_s=60
  if [ -n "$FLUSH_PID" ] && kill -0 "$FLUSH_PID" 2> /dev/null; then
    log "an earlier sync is still running; the shutdown goes ahead without starting another"
    return 0
  fi
  ( exec 6>&- 8>&- 9>&- < /dev/null > /dev/null 2>&1; "$SYNC_CMD" -f "$ST" || exec "$SYNC_CMD" ) &
  pid=$!
  FLUSH_PID="$pid"
  for ((i = 0; i < wait_s * 5; i++)); do
    if ! kill -0 "$pid" 2> /dev/null; then wait "$pid" 2> /dev/null; return 0; fi
    nolock sleep 0.2
  done
  log "sync did not finish within ${wait_s}s; the shutdown goes ahead without it"
}

attempt_shutdown() {  # (caller holds the lock) runs the official shutdown command once
  local n rc
  n=$(($(bounded shutdown_attempts 0 0 1000000) + 1))
  put shutdown_attempts "$n"
  put last_shutdown_at "$(now)"
  log "SHUTDOWN attempt=$n kind=$(get shutdown_kind '') reason=[$(get shutdown_reason '')] deadline=$(get deadline 0) keep_until=$(get keep_until 0)"
  # sync and the shutdown command hold none of the locks: if they hang and the daemon is killed,
  # the locks are free for the next daemon; a sync that never returns is not waited for (flush_disks)
  flush_disks
  if is_dry; then
    put dry_run_fired "$(get shutdown_reason '')"
    log "DRY_RUN: would run $SHUTDOWN_CMD"
    return 0
  fi
  if have timeout; then nolock timeout 120 "$SHUTDOWN_CMD"; else nolock "$SHUTDOWN_CMD"; fi
  rc=$?
  [ "$rc" -eq 0 ] || log "shutdown command failed rc=$rc; retrying every interval"
  return "$rc"
}

tick() {
  local t deadline reasons u recent last_busy grace idle keep_until post kind
  PROC_OK=""        # a fresh pass over /proc for this check
  UTIL_UNSTORED=""
  lock
  t="$(now)"
  put heartbeat "$t" || log "cannot write the heartbeat (disk full?)"
  # 1. the deadline comes first, before any external probe can hang
  deadline="$(deadline_state)"
  [ "$deadline" = BAD ] && deadline=0   # cannot enforce it here; the console's scheduled shutdown remains
  if [ "$deadline" -gt 0 ] && [ "$t" -ge "$deadline" ]; then
    if [ "$(get shutdown_pending 0)" = 1 ]; then
      put shutdown_kind deadline
      attempt_shutdown
    else
      request_shutdown "deadline reached" deadline
    fi
    unlock
    return "$FIRED"
  fi
  # an arm cut short: until an arm finishes, only the deadline counts (arm writes it first, so it is
  # the old one or the new one, never a mix)
  if [ -e "$ST/arm_incomplete" ]; then unlock; return 0; fi
  # a fresh GPU sample before anything decides on "in use", the pending retry below included
  if [ "$(get mode nogpu)" = gpu ]; then
    u="$(unlocked gpu_util_max)"
    [ -n "$u" ] || u=u   # unreadable sample, counted as in use when --util-signal is on
    recent="$(last_words "$(bounded util_samples 3 1 20)" "$(get util_recent "") $u")"
    put util_recent "$recent" || UTIL_UNSTORED=1
  fi
  # 2. a pending shutdown: retry, or cancel one that is not forced if something runs again (a deadline
  # one gets here only when the deadline is no longer reached, i.e. the clock went back)
  if [ "$(get shutdown_pending 0)" = 1 ] && ! is_dry; then
    kind="$(get shutdown_kind idle)"
    reasons=""
    if [ "$kind" != forced ]; then check_busy; reasons="$BUSY"; fi
    if [ -z "$reasons" ]; then
      attempt_shutdown
      unlock
      return "$FIRED"
    fi
    put shutdown_pending 0
    log "PENDING SHUTDOWN CANCELLED kind=$kind: in use again [$(echo $reasons)]"
  fi
  check_busy
  reasons="$BUSY"
  if [ -n "$reasons" ]; then
    put last_busy "$t"
    put busy_now "$(echo $reasons)"
    unlock
    return 0
  fi
  put busy_now ""
  last_busy="$(epoch_state last_busy "$t")"
  grace="$(num grace_s 120)"
  keep_until="$(epoch_state keep_until 0)"
  post="$(num post_job_keep_s 0)"
  if [ "$last_busy" = BAD ] || [ "$grace" = BAD ] || [ "$keep_until" = BAD ] || [ "$post" = BAD ]; then
    unlock   # corrupt state: never shut down for idleness, only at the deadline
    return 0
  fi
  idle=$((t - last_busy))
  if [ "$idle" -lt "$grace" ]; then unlock; return 0; fi
  if [ "$(get off_when_done 0)" = 1 ]; then
    request_shutdown "off-when-done ($(get off_when_done_reason ''))" when-done
    [ $? -eq 2 ] && { unlock; return 0; }
    unlock
    return "$FIRED"
  fi
  if [ "$t" -ge "$keep_until" ] && [ "$idle" -ge "$post" ]; then
    request_shutdown "idle, keep period over" idle
    [ $? -eq 2 ] && { unlock; return 0; }
    unlock
    return "$FIRED"
  fi
  unlock
  return 0
}

daemon_alive() {  # the daemon's lock is busy => a daemon holds it
  ("$FLOCK_CMD" -n 7 6>&- 8>&- 9>&- || exit 0; exit 1) 7> "$ST/.daemon.lock"
}

daemon_loop() {
  local rc s dl left
  exec 8> "$ST/.daemon.lock" || die "cannot open the daemon lock"
  "$FLOCK_CMD" -n 8 6>&- 9>&- || { echo "daemon already running"; exit 0; }
  # without its PID and start time on record, revive --restart cannot stop this daemon (it signals
  # only a verified process); guarding matters more, so the daemon runs anyway
  { put daemon_pid "$$" && put daemon_pstart "$(proc_start $$)"; } ||
    log "cannot record the daemon's PID and start time (disk full?): revive --restart cannot stop this daemon"
  put daemon_version "$VERSION"
  put guard_sty "${STY:-}"   # the screen session this daemon runs in, if any
  log "DAEMON start pid=$$ version=$VERSION mode=$(get mode nogpu) interval=$(bounded interval_s 60 1 3600)s sty=${STY:-none}"
  while :; do
    tick
    rc=$?
    if [ "$rc" -eq "$FIRED" ] && is_dry; then
      log "DAEMON exit after DRY_RUN shutdown"
      exit 0
    fi
    s="$(bounded interval_s 60 1 3600)"
    dl="$(deadline_state)"
    if is_uint "$dl" && [ "$dl" -gt 0 ]; then   # never sleep past the deadline
      left=$((dl - $(now)))
      [ "$left" -lt 1 ] && left=1
      [ "$left" -lt "$s" ] && s="$left"
    fi
    nolock sleep "$s"
  done
}

launch() {  # launch SESSION ARGS...: re-run this script detached; lock descriptors are not inherited,
  # and neither is a job tag (a job that calls the guard must not tag the daemon or other jobs)
  local name="$1" how="${AUTODL_LAUNCHER:-auto}"
  shift
  if [ "$how" = auto ]; then
    if have "$SCREEN_CMD"; then how=screen; else how=nohup; fi
  fi
  case "$how" in
    screen) "$SCREEN_CMD" -dmS "$name" env -u AUTODL_GUARD_JOB bash "$SELF" "$@" 6>&- 8>&- 9>&- ;;
    nohup)
      if have setsid; then
        env -u STY -u AUTODL_GUARD_JOB setsid nohup bash "$SELF" "$@" > /dev/null 2>&1 < /dev/null 6>&- 8>&- 9>&- &
      else
        env -u STY -u AUTODL_GUARD_JOB nohup bash "$SELF" "$@" > /dev/null 2>&1 < /dev/null 6>&- 8>&- 9>&- &
      fi
      ;;
    direct) env -u STY -u AUTODL_GUARD_JOB bash "$SELF" "$@" > /dev/null 2>&1 < /dev/null 6>&- 8>&- 9>&- & ;;
    none) : ;;   # tests only: a launcher that never starts anything
    *) die "unknown AUTODL_LAUNCHER '$how'" ;;
  esac
}

start_daemon() {
  local i
  daemon_alive && return 0
  launch "$GUARD_SESSION" daemon
  for ((i = 1; i <= 10; i++)); do
    nolock sleep 0.5
    daemon_alive && return 0
  done
  die "guard daemon did not start (see $LOG)"
}

stop_daemon() {  # stop our own daemon: a signal goes only to the process its PID and start time verify
  local i p s
  p="$(get daemon_pid "")"
  s="$(get daemon_pstart "")"
  if same_process "$p" "$s"; then kill "$p" 2> /dev/null; fi
  # the daemon sets no trap, so TERM ends it at once, even while it waits for a probe or a hanging shutdown
  # command; KILL after 15 s is the fallback. What the daemon started holds none of its locks, so it may
  # run on (a sync stuck in the kernel, for one)
  for ((i = 1; i <= 50; i++)); do
    daemon_alive || return 0
    if [ "$i" = 30 ] && same_process "$p" "$s"; then kill -KILL "$p" 2> /dev/null; fi
    nolock sleep 0.5
  done
  die "the running guard daemon did not stop (a daemon whose recorded PID and start time do not match is never signalled; see $LOG)"
}

ensure_daemon() { [ -n "${AUTODL_NO_DAEMON:-}" ] || start_daemon; }
require_armed() {  # call with the lifecycle lock held: an arm that was still running has finished by then
  [ "$BOOT" != unknown ] || die "cannot read this boot's marker ($PROC/1/stat), so no arm can count for it"
  [ "$(get armed_boot '')" = "$BOOT" ] || die "not armed for this boot; run arm first"
  [ ! -e "$ST/arm_incomplete" ] ||
    die "the last arm of this boot was cut short (status: arm_incomplete=1); repeat it (the same --req finishes it) or arm again"
}
refuse_if_pending() {  # call with the lock held
  if [ "$(get shutdown_pending 0)" = 1 ]; then
    unlock
    fail "$E_PENDING" "a shutdown is pending ($(get shutdown_reason '')); if the instance is still up, arm --rearm cancels it"
  fi
}

# ---- part 4: commands ----
# Durations are validated before any state is written: `die` inside $(...) only
# ends the subshell, so every `x="$(to_seconds ...)"` needs `|| exit 1`.
arm_daemon() {  # after arm: the configuration is in place, so a daemon that does not start must be said plainly
  if [ -z "${AUTODL_NO_DAEMON:-}" ] && ! (start_daemon) > /dev/null 2>&1; then
    fail 1 "armed for this boot, but the guard daemon did not start (see $LOG); fix that, then run revive"
  fi
}

cmd_arm() {  # configure this power-on; refused if already armed in this boot unless --rearm
  local deadline_d="" keep_d="" dry=0 util=0 envs="" mode=auto grace=120 interval=60 rearm=0 req="" t dl kp
  while [ $# -gt 0 ]; do
    case "$1" in
      --req) need2 $# "$1"; req="$2"; shift 2 ;;
      --deadline) need2 $# "$1"; deadline_d="$2"; shift 2 ;;
      --keep) need2 $# "$1"; keep_d="$2"; shift 2 ;;
      --dry-run) dry=1; shift ;;
      --util-signal) util=1; shift ;;
      --env-setup) need2 $# "$1"; envs="$2"; shift 2 ;;
      --mode) need2 $# "$1"; mode="$2"; shift 2 ;;
      --grace) need2 $# "$1"; grace="$(to_seconds "$2")" || exit 1; shift 2 ;;
      --interval) need2 $# "$1"; interval="$(to_seconds "$2")" || exit 1; shift 2 ;;
      --rearm) rearm=1; shift ;;
      *) die "arm: unknown option '$1'" ;;
    esac
  done
  [ -n "$deadline_d" ] || die "arm: --deadline is required"
  [ -n "$keep_d" ] || die "arm: --keep is required"
  [ -z "$req" ] || [[ "$req" =~ ^[A-Za-z0-9]{8,64}$ ]] || die "arm: --req must be 8 to 64 letters or digits"
  dl="$(to_seconds "$deadline_d")" || exit 1
  kp="$(to_seconds "$keep_d")" || exit 1
  [ "$dl" -ge 1 ] || die "arm: --deadline must be at least 1s"
  [ "$grace" -le 3600 ] || die "arm: --grace must be at most 1h"
  if [ "$interval" -lt 1 ] || [ "$interval" -gt 3600 ]; then die "arm: --interval must be between 1s and 1h"; fi
  case "$mode" in
    auto)
      mode="$(detect_mode)"
      [ "$mode" != unknown ] || die "arm: cannot tell the mode (nvidia-smi lists no GPU and the memory limit is not the 2 GiB of non-GPU mode); check it and pass --mode gpu or --mode nogpu"
      ;;
    gpu | nogpu) ;;
    *) die "arm: --mode must be auto, gpu or nogpu" ;;
  esac
  # the arm belongs to this boot only; a boot that cannot be told apart from the next one is refused
  [ "$BOOT" != unknown ] || die "arm: cannot read this boot's marker ($PROC/1/stat); refusing to arm"
  life_lock
  lock
  # a resend of an arm or rearm that already took effect in this boot changes nothing
  if [ -n "$req" ] && armed_now && [ "$(get arm_req '')" = "$req" ]; then
    unlock
    arm_daemon
    life_unlock
    echo "armed (this request had already armed this boot; settings unchanged)"
    return 0
  fi
  if armed_now && [ "$rearm" != 1 ]; then   # an arm cut short may be finished by any arm
    unlock
    fail "$E_ARMED" "already armed for this boot (armed_at=$(get armed_at 0)); use keep, deadline or revive, or arm --rearm to replace the whole configuration"
  fi
  t="$(now)"
  # arm_incomplete first and gone last: an arm cut short in between never counts as done (tick then
  # enforces only the deadline, and run, keep and the like refuse), and a resend redoes it. The
  # deadline comes next, valid against the old armed_at and the new one alike, so the deadline in
  # force is always the old one or the new one. This boot's marker comes just before the removal
  putx arm_incomplete "$t"
  putx deadline $((t + dl))
  putx armed_at "$t"
  putx keep_until $((t + kp))
  putx post_job_keep_s 0
  putx off_when_done 0
  putx off_when_done_reason ""
  putx shutdown_pending 0
  putx shutdown_reason ""
  putx shutdown_kind ""
  putx shutdown_attempts 0
  putx last_busy "$t"
  putx mode "$mode"
  putx dry_run "$dry"
  putx util_signal "$util"
  putx util_recent ""
  putx env_setup "$envs"
  putx grace_s "$grace"
  putx interval_s "$interval"
  nolock rm -f "$ST/dry_run_fired"
  putx arm_req "$req"
  putx armed_boot "$BOOT"
  nolock rm -f "$ST/arm_incomplete" || die "cannot finish the arm: $ST/arm_incomplete cannot be removed"
  unlock
  log "ARM mode=$mode deadline=+${dl}s keep=+${kp}s dry_run=$dry util_signal=$util rearm=$rearm"
  arm_daemon
  life_unlock
  echo "armed mode=$mode deadline_in=${dl}s keep_in=${kp}s dry_run=$dry"
}

cmd_revive() {  # revive [--restart]: make sure the daemon runs; never touches the configuration
  local restart=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --restart) restart=1; shift ;;
      *) die "revive: unknown option '$1'" ;;
    esac
  done
  # under the lifecycle lock, which run, arm, keep and deadline also hold, but not under the state
  # lock: a daemon waiting in tick for that lock would not act on TERM
  life_lock
  require_armed
  if [ "$restart" = 1 ]; then stop_daemon; fi
  (start_daemon) > /dev/null 2>&1 ||
    fail 1 "the guard daemon is NOT running now (see $LOG): running jobs and the deadline are unguarded until revive succeeds; the console's scheduled shutdown still applies"
  life_unlock
  echo "daemon running (script version $VERSION)"
}

cmd_run() {  # run NAME [--then-off] [--log PATH] [--req ID] [--cmd-sha256 HEX] (--cmd-stdin | -- CMD...)
  local name="${1:-}" then_off=0 logp="" cmd="" req="" sum="" from_stdin=0 d tmp
  [ $# -gt 0 ] && shift
  [[ "$name" =~ $JOB_NAME_RE ]] || die "run: job name must start with a letter or digit and use only [A-Za-z0-9._-] (max 64)"
  [ "$name" != guard ] || die "run: the job name 'guard' is reserved (logtail guard shows the guard's own log)"
  while [ $# -gt 0 ]; do
    case "$1" in
      --then-off) then_off=1; shift ;;
      --log) need2 $# "$1"; logp="$2"; shift 2 ;;
      --req) need2 $# "$1"; req="$2"; shift 2 ;;
      --cmd-sha256) need2 $# "$1"; sum="$2"; shift 2 ;;
      --cmd-stdin) from_stdin=1; shift ;;
      --) shift; cmd="$*"; break ;;
      *) die "run: unknown option '$1'" ;;
    esac
  done
  [ -z "$req" ] || [[ "$req" =~ ^[A-Za-z0-9]{8,64}$ ]] || die "run: --req must be 8 to 64 letters or digits"
  [ -z "$sum" ] || [[ "$sum" =~ ^[0-9a-f]{64}$ ]] || die "run: --cmd-sha256 must be 64 lowercase hex digits"
  if [ "$from_stdin" = 1 ]; then   # checked byte for byte: a connection cut in transit leaves a shorter command
    tmp="$(mktemp "$GH/.cmd.XXXXXX")" || die "run: cannot store the command"
    cat > "$tmp"
    if [ -n "$sum" ] && [ "$(sha256sum < "$tmp" | cut -d' ' -f1)" != "$sum" ]; then
      rm -f "$tmp"
      die "run: the command arrived incomplete (sha256 mismatch); nothing was registered"
    fi
    cmd="$(cat "$tmp")"
    rm -f "$tmp"
  fi
  [ -n "$cmd" ] || die "run: no command (use --cmd-stdin or -- CMD)"
  d="$JOBS/$name"
  life_lock   # held until the start is confirmed: a resend of this request waits for it
  lock
  require_armed
  # the same request again (the helper resends when it cannot tell whether the first try ran):
  # no second start; finish a first start that certainly never began, report one that may have
  if [ -n "$req" ] && [ "$(fread "$d/boot")" = "$BOOT" ] &&
    { [ "$(fread "$d/req")" = "$req" ] || [ "$(fread "$d/req.pending")" = "$req" ]; }; then
    unlock
    resume_run "$name" "$d"
    life_unlock
    return
  fi
  refuse_if_pending
  ensure_daemon   # a job is only started once a daemon watches over it
  if [ -d "$d" ] && job_running "$d"; then unlock; die "run: job '$name' is already running"; fi
  if [ -d "$d" ]; then nolock mv "$d" "$d.prev-$(now)-$$" || { unlock; die "run: cannot archive the previous run of '$name'"; }; fi
  # req.pending comes last: once it exists, every other job file does too
  if ! { nolock mkdir -p "$d" && write_file "$d/cmd" "$cmd" && write_file "$d/env.sh" "$(get env_setup "")" &&
    write_file "$d/logpath" "${logp:-$d/log}" && write_file "$d/start" "$(now)" &&
    write_file "$d/boot" "$BOOT" && write_file "$d/tag" "$name.$(now).$$" &&
    write_file "$d/owner" "$$ $(proc_start $$)" && write_file "$d/req.pending" "$req"; }; then
    unlock
    die "run: cannot write the job files of '$name'"
  fi
  putx last_busy "$(now)"
  putx off_when_done "$then_off"   # a new job cancels a pending off-when-done unless --then-off
  if [ "$then_off" = 1 ]; then putx off_when_done_reason "after job $name"; else putx off_when_done_reason ""; fi
  unlock
  log "RUN job=$name then_off=$then_off log=${logp:-$d/log}"
  if [ -n "${AUTODL_TEST_EXIT_BEFORE_LAUNCH:-}" ]; then exit 0; fi   # tests only: a session cut off right here
  start_job "$name" "$d" ""
  life_unlock
}

UNSURE_START="died while starting the command, which may or may not have run; check status and logtail. This request will not start it again"

start_job() {  # start_job NAME DIR NOTE: launch a registered job. It counts as started only when its
  # runner has handed the command to a process (the file "running"); a runner of an older launch
  # finds a different token and does nothing. A start that may have happened is reported (exit 6),
  # never repeated: the runner writes "spawning" before it starts the command
  local name="$1" d="$2" note="$3" i token
  token="$(now).$$.$RANDOM"
  lock
  write_file "$d/launch" "$token"
  unlock
  launch "$JOB_PREFIX$name" _job "$name" "$token"
  if [ -n "${AUTODL_TEST_EXIT_AFTER_LAUNCH:-}" ]; then exit 0; fi   # tests only: a session cut off here
  for ((i = 1; i <= 2 * ${AUTODL_LAUNCH_WAIT:-10}; i++)); do
    [ -f "$d/running" ] && break
    nolock sleep 0.5
  done
  if [ ! -f "$d/running" ]; then
    lock
    if [ -f "$d/running" ]; then
      unlock   # it came up just now
    elif [ -f "$d/end" ] && [ -f "$d/spawning" ]; then
      unlock   # the command ran and has ended; only the note that it started is missing
      note="$note (it has already ended, rc=$(fread "$d/rc"))"
    elif [ -f "$d/pid" ] && runner_alive "$(fread "$d/pid")" "$(fread "$d/pstart")"; then
      unlock
      fail "$E_UNCERTAIN" "run: the runner of job '$name' has not confirmed the start within ${AUTODL_LAUNCH_WAIT:-10}s; it may be starting it; check status"
    elif [ -f "$d/spawning" ]; then
      unlock
      log "RUN job=$name start uncertain: the runner died while starting the command"
      fail "$E_UNCERTAIN" "run: the runner of job '$name' $UNSURE_START"
    else   # no runner, or one that died before it began to start the command: give the start up for good
      write_file "$d/launch" gave-up
      write_file "$d/rc" launch-failed
      write_file "$d/end" "$(now)"
      unlock
      log "RUN job=$name launch failed"
      die "run: job '$name' did not start within ${AUTODL_LAUNCH_WAIT:-10}s"
    fi
  fi
  [ ! -f "$d/req.pending" ] || nolock mv -f "$d/req.pending" "$d/req"
  echo "started job $name log=$(fread "$d/logpath")$note"
}

resume_run() {  # resume_run NAME DIR: the same request came again (the caller holds the lifecycle lock)
  local name="$1" d="$2" i owner
  for ((i = 1; i <= 2 * ${AUTODL_LAUNCH_WAIT:-10}; i++)); do   # an earlier try may still be starting it
    if [ -f "$d/running" ] || [ -f "$d/end" ]; then break; fi
    owner="$(fread "$d/owner")"
    runner_alive "${owner%% *}" "${owner##* }" || break
    nolock sleep 0.5
  done
  if [ -f "$d/running" ] || { [ -f "$d/end" ] && [ -f "$d/spawning" ]; }; then
    [ ! -f "$d/req.pending" ] || nolock mv -f "$d/req.pending" "$d/req"
    if [ -f "$d/running" ]; then
      echo "started job $name log=$(fread "$d/logpath") (this request had already started it)"
    else   # it ran and has ended; only the note that it started is missing
      echo "started job $name log=$(fread "$d/logpath") (this request had already started it; it has ended, rc=$(fread "$d/rc"))"
    fi
    return 0
  fi
  [ ! -f "$d/end" ] || die "run: job '$name' did not start ($(fread "$d/rc"))"
  lock   # the earlier try ended before its runner confirmed the start
  owner="$(fread "$d/owner")"
  if runner_alive "${owner%% *}" "${owner##* }"; then
    unlock
    fail "$E_UNCERTAIN" "run: an earlier try of this request is still starting job '$name'; check status"
  fi
  if [ -f "$d/pid" ] && runner_alive "$(fread "$d/pid")" "$(fread "$d/pstart")"; then
    unlock
    fail "$E_UNCERTAIN" "run: the runner of job '$name' has not confirmed the start yet; check status"
  fi
  if [ -f "$d/spawning" ]; then
    unlock
    fail "$E_UNCERTAIN" "run: the runner of job '$name' $UNSURE_START"
  fi
  # only now is it certain that the command never started: take the start over
  [ ! -f "$d/pid" ] || nolock mv -f "$d/pid" "$d/pid.dead-$(now)"   # a runner that died before the command; kept for the record
  write_file "$d/owner" "$$ $(proc_start $$)"
  refuse_if_pending
  ensure_daemon
  unlock
  log "RUN job=$name resumed after an interrupted start"
  start_job "$name" "$d" " (resumed)"
}

cmd_job() {  # internal: runs one registered job in its own process group (when setsid exists)
  local name="${1:-}" token="${2:-}" d logp child pgid="" rc tag
  d="$JOBS/$name"
  [ -n "$name" ] && [ -d "$d" ] || die "_job: unknown job '$name'"
  lock   # only the runner of the current launch starts the command, and only once
  if [ -f "$d/end" ] || [ -f "$d/pid" ] || [ "$(fread "$d/launch")" != "$token" ]; then
    unlock
    exit 0
  fi
  write_file "$d/pstart" "$(proc_start $$)"
  write_file "$d/pid" "$$"
  unlock
  if [ -n "${AUTODL_TEST_RUNNER_DIES_AFTER_PID:-}" ]; then exit 0; fi   # tests only: a runner that dies here
  logp="$(fread "$d/logpath")"
  mkdir -p "$(dirname "$logp")"   # no lock is held here
  printf '=== job %s start %s\n=== cmd: %s\n' "$name" "$(ts)" "$(fread "$d/cmd")" >> "$logp"
  # env_setup and the command are separate files: a failing env_setup stops the job (rc 97)
  set -- 'if [ -s "$1" ]; then . "$1" || { echo "=== env_setup failed" >&2; exit 97; }; fi; . "$2"' \
    autodl-job "$d/env.sh" "$d/cmd"
  # every process of the job inherits the tag, also one that leaves the process group
  tag="$(fread "$d/tag")"
  # recorded before the command can start: from here on, a runner that dies before "running" leaves
  # a start that is reported as uncertain, never one that is started again
  if ! write_file "$d/spawning" "$(now)"; then
    log "RUN job=$name not started: cannot record the start (disk full?)"
    exit 1
  fi
  if have setsid; then
    AUTODL_GUARD_JOB="$tag" setsid bash -c "$@" >> "$logp" 2>&1 < /dev/null &
    child=$!
    pgid="$child"
    write_file "$d/pgid" "$pgid"
  else
    AUTODL_GUARD_JOB="$tag" bash -c "$@" >> "$logp" 2>&1 < /dev/null &
    child=$!
  fi
  if [ -n "${AUTODL_TEST_RUNNER_DIES_AFTER_SPAWN:-}" ]; then exit 0; fi   # tests only: a runner that dies here
  # the command is in a process now: this confirms the start (if it cannot be written, run reports exit 6)
  write_file "$d/running" "$child" || log "RUN job=$name: cannot record that the command started (disk full?)"
  wait "$child"
  rc=$?
  # background children of the command keep the job alive, those that left its process group too
  while PROC_OK=""; group_alive "$pgid" || tag_alive "$tag"; do sleep 1; done
  printf '=== job %s end %s rc=%s\n' "$name" "$(ts)" "$rc" >> "$logp"
  write_file "$d/rc" "$rc"
  lock   # last_busy first, then end: tick never sees an ended job with an older last_busy
  put last_busy "$(now)"
  write_file "$d/end" "$(now)"
  unlock
  log "JOB END job=$name rc=$rc"
}

# ---- part 5: keep, off-when-done, off-now, deadline, status, logtail, entry point ----
cmd_keep() {  # keep DUR [--after-job] [--reason TEXT]
  local after=0 dur="" reason="" s
  while [ $# -gt 0 ]; do
    case "$1" in
      --after-job) after=1; shift ;;
      --reason) need2 $# "$1"; reason="$2"; shift 2 ;;
      *) [ -z "$dur" ] || die "keep: unexpected argument '$1'"; dur="$1"; shift ;;
    esac
  done
  [ -n "$dur" ] || die "keep: a duration is required, e.g. keep 30m --reason 'next run soon'"
  s="$(to_seconds "$dur")" || exit 1
  life_lock
  lock
  require_armed
  refuse_if_pending
  ensure_daemon
  if [ "$after" = 1 ]; then putx post_job_keep_s "$s"; else putx keep_until $(($(now) + s)); fi
  putx off_when_done 0
  putx off_when_done_reason ""
  unlock
  life_unlock
  if [ "$after" = 1 ]; then log "KEEP ${s}s after the last job reason=[$reason]"; else log "KEEP ${s}s from now reason=[$reason]"; fi
  echo "ok"
}

cmd_off_when_done() {  # off-when-done [--reason TEXT]
  local reason=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --reason) need2 $# "$1"; reason="$2"; shift 2 ;;
      *) die "off-when-done: unknown option '$1'" ;;
    esac
  done
  life_lock
  lock
  require_armed
  refuse_if_pending
  ensure_daemon
  putx off_when_done 1
  putx off_when_done_reason "$reason"
  putx keep_until 0
  putx post_job_keep_s 0
  unlock
  life_unlock
  log "OFF-WHEN-DONE reason=[$reason]"
  echo "ok: will shut down once nothing is running"
}

cmd_off_now() {  # off-now [--force] [--reason TEXT]; exit 3 = refused because busy
  local force=0 reason="" reasons why kind rc
  while [ $# -gt 0 ]; do
    case "$1" in
      --force) force=1; shift ;;
      --reason) need2 $# "$1"; reason="$2"; shift 2 ;;
      *) die "off-now: unknown option '$1'" ;;
    esac
  done
  lock
  check_busy
  reasons="$BUSY"
  if [ -n "$reasons" ] && [ "$force" != 1 ]; then
    unlock
    log "OFF-NOW refused busy=[$(echo $reasons)] reason=[$reason]"
    printf 'refused: still in use\n%s\n' "$reasons"
    exit 3
  fi
  if [ "$force" = 1 ]; then why="off-now forced ($reason)"; kind=forced; else why="off-now ($reason)"; kind=now; fi
  request_shutdown "$why" "$kind"
  rc=$?
  unlock
  if is_dry; then
    echo "dry-run: shutdown not executed ($why)"
  elif [ "$rc" -eq 0 ]; then
    echo "shutdown issued ($why)"
  elif [ "$rc" -eq 2 ]; then
    echo "not shut down: the pending shutdown could not be recorded (disk full?)"
  else
    echo "shutdown command failed rc=$rc ($why); a daemon keeps retrying"
    ensure_daemon
  fi
  exit "$rc"
}

cmd_deadline() {  # deadline DUR: latest shutdown = now + DUR
  local s
  [ $# -eq 1 ] || die "deadline: give exactly one duration, e.g. deadline 90m"
  s="$(to_seconds "$1")" || exit 1
  [ "$s" -ge 1 ] || die "deadline: must be at least 1s"
  life_lock
  lock
  require_armed
  refuse_if_pending
  ensure_daemon
  putx deadline $(($(now) + s))
  unlock
  life_unlock
  log "DEADLINE now+${s}s"
  echo "ok: deadline in ${s}s"
}

cmd_status() {  # key=value lines; job lines are job.NAME=STATE|START|END|LOG
  local d n state
  echo "version=$VERSION"
  echo "now=$(now)"
  echo "mode=$(get mode unknown)"
  echo "mode_now=$(detect_mode)"
  echo "armed_at=$(get armed_at 0)"
  echo "boot=$BOOT"
  if armed_now; then echo "armed_this_boot=1"; else echo "armed_this_boot=0"; fi
  if [ -e "$ST/arm_incomplete" ]; then echo "arm_incomplete=1"; else echo "arm_incomplete=0"; fi
  echo "deadline=$(get deadline 0)"
  echo "keep_until=$(get keep_until 0)"
  echo "post_job_keep_s=$(get post_job_keep_s 0)"
  echo "off_when_done=$(get off_when_done 0)"
  echo "shutdown_pending=$(get shutdown_pending 0)"
  echo "shutdown_kind=$(get shutdown_kind '')"
  echo "shutdown_attempts=$(get shutdown_attempts 0)"
  echo "grace_s=$(get grace_s 120)"
  echo "interval_s=$(get interval_s 60)"
  echo "dry_run=$(get dry_run 0)"
  echo "dry_run_fired=$(get dry_run_fired '')"
  echo "util_signal=$(get util_signal 0)"
  echo "util_recent=$(get util_recent '')"
  echo "last_busy=$(get last_busy 0)"
  check_busy
  echo "busy_now=$(echo $BUSY)"
  echo "heartbeat=$(get heartbeat 0)"
  if daemon_alive; then echo "daemon_alive=1"; else echo "daemon_alive=0"; fi
  echo "daemon_version=$(get daemon_version '')"
  echo "last_shutdown_reason=$(get last_shutdown_reason '')"
  echo "last_shutdown_at=$(get last_shutdown_at 0)"
  n="$(grep -c 'STATE CORRUPT' "$LOG" 2> /dev/null)"
  echo "state_corrupt_logged=${n:-0}"
  for d in "$JOBS"/*/; do
    [ -d "$d" ] || continue
    d="${d%/}"
    n="${d##*/}"
    if job_running "$d"; then
      state=running
    elif [ -f "$d/end" ]; then
      state="done:$(fread "$d/rc")"
    else
      state=lost
    fi
    echo "job.$n=$state|$(fread "$d/start")|$(fread "$d/end")|$(fread "$d/logpath")"
  done
}

cmd_logtail() {  # logtail NAME [LINES]; NAME "guard" shows the guard's own log
  local name="${1:-}" n="${2:-50}" d
  is_uint "$n" || die "logtail: bad line count"
  if [ "$name" = guard ]; then tail -n "$n" -- "$LOG"; return; fi
  [[ "$name" =~ $JOB_NAME_RE ]] || [[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*\.prev-[0-9]+-[0-9]+$ ]] || die "logtail: bad job name"
  d="$JOBS/$name"
  [ -f "$d/logpath" ] || die "logtail: unknown job '$name'"
  tail -n "$n" -- "$(fread "$d/logpath")"
}

# ---- read-only activity sampling (0.7.1). Nothing here takes a lock or writes state, and tick still
# decides by the 0.7 rules. Each reader prints nothing when its source cannot be read or does not look
# exactly as expected, so a caller can tell "unknown" from a real zero. ----
uptime_cs() {  # kernel uptime in centiseconds (/proc/uptime: not moved by clock changes; in an AutoDL container
  # it is the host's and does not restart with the container, so compare readings of one boot only)
  local up="" rest=""
  { read -r up rest < "$UPTIME_FILE" || [ -n "$up" ]; } 2> /dev/null || return 0
  [[ "$up" =~ ^([0-9]{1,10})\.([0-9]{2})$ ]] || return 0
  printf '%s' "$((10#${BASH_REMATCH[1]} * 100 + 10#${BASH_REMATCH[2]}))"
}
cpu_usage_usec() {  # CPU time the container has used, in microseconds (cgroup v2 cpu.stat, usage_usec)
  local k v
  can_read "$CG_DIR/cpu.stat" || return 0
  while read -r k v || [ -n "$k" ]; do
    if [ "$k" = usage_usec ]; then is_counter "$v" && printf '%s' "$v"; return 0; fi
  done < "$CG_DIR/cpu.stat"
}
io_bytes() {  # bytes the container has read and written on all its devices (cgroup v2 io.stat); 0 before
  # any I/O. Every line must be "MAJ:MIN key=value ..." with exactly one rbytes and one wbytes
  local total=0 f nr nw
  local -a w
  can_read "$CG_DIR/io.stat" || return 0
  while read -r -a w || [ ${#w[@]} -gt 0 ]; do   # a last line without a newline is checked too
    [[ "${w[0]:-}" =~ ^[0-9]+:[0-9]+$ ]] || return 0
    nr=0 nw=0
    for f in "${w[@]:1}"; do
      case "$f" in
        rbytes=*) is_counter "${f#*=}" || return 0; nr=$((nr + 1)); total=$((total + ${f#*=})) ;;
        wbytes=*) is_counter "${f#*=}" || return 0; nw=$((nw + 1)); total=$((total + ${f#*=})) ;;
      esac
    done
    { [ "$nr" = 1 ] && [ "$nw" = 1 ]; } || return 0
  done < "$CG_DIR/io.stat"
  printf '%s' "$total"
}
net_bytes() {  # bytes received and sent on every interface except lo (/proc/net/dev: 2 header lines,
  # then "NAME: 8 receive counters 8 transmit counters"; a large receive count follows the colon at once)
  local line name total=0 n=0
  local -a v
  can_read "$NET_DEV" || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    [ "$n" -le 2 ] && continue
    case "$line" in *:*) ;; *) return 0 ;; esac
    name="${line%%:*}"
    name="${name//[[:space:]]/}"
    [ "$name" = lo ] && continue
    set -f
    v=(${line#*:})
    set +f
    { [ ${#v[@]} -eq 16 ] && is_counter "${v[0]}" && is_counter "${v[8]}"; } || return 0
    total=$((total + v[0] + v[8]))
  done < "$NET_DEV"
  [ "$n" -ge 2 ] || return 0
  printf '%s' "$total"
}
proc_cpu_ticks() {  # proc_cpu_ticks PID: CPU time of PID and of the children it has waited for, in clock ticks
  local line=""
  { IFS= read -r -d '' line < "$PROC/$1/stat"; } 2> /dev/null   # the whole file, as proc_stat reads it
  case "$line" in *") "*) ;; *) return 0 ;; esac
  set -- ${line##*) }   # $1 is field 3 (state); utime, stime, cutime, cstime are fields 14 to 17
  [ $# -ge 15 ] || return 0
  { is_counter "${12}" && is_counter "${13}" && is_counter "${14}" && is_counter "${15}"; } || return 0
  printf '%s' "$((${12} + ${13} + ${14} + ${15}))"
}
gpu_util_strict() {  # "MAX COUNT": the highest utilization.gpu and how many GPUs answered; nothing if nvidia-smi
  # fails or times out, or any line is not one number from 0 to 100 (blanks around it are fine). tick still uses gpu_util_max
  local out line m=-1 n=0
  out="$(probe "$NVSMI_CMD" --query-gpu=utilization.gpu --format=csv,noheader,nounits 2> /dev/null)" || return 0
  [ -n "$out" ] || return 0
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*(0|[1-9][0-9]?|100)[[:space:]]*$ ]] || return 0
    n=$((n + 1))
    if [ "${BASH_REMATCH[1]}" -gt "$m" ]; then m="${BASH_REMATCH[1]}"; fi
  done <<< "$out"
  printf '%s %s' "$m" "$n"
}
wait_until() {  # wait_until CS: sleep until the uptime reaches CS centiseconds
  local now d s
  while :; do
    now="$(uptime_cs)"
    [ -n "$now" ] || die "sample: $UPTIME_FILE can no longer be read"
    d=$(($1 - now))
    [ "$d" -gt 0 ] || return 0
    printf -v s '%d.%02d' $((d / 100)) $((d % 100))
    sleep "$s"
  done
}
sample_row() {  # sample_row UPTIME_CS GPU_MAX GPU_FAIL: one row, the counters read now
  local pid ticks=""
  pid="$(get daemon_pid "")"
  if same_process "$pid" "$(get daemon_pstart "")"; then ticks="$(proc_cpu_ticks "$pid")"; fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(now)" "$1" "$(cpu_usage_usec)" "$(io_bytes)" \
    "$(net_bytes)" "$2" "$3" "$ticks" "$(proc_cpu_ticks $$)"
}
cmd_sample() {  # sample [--every DUR] [--count N] [--gpu-samples K]: read-only. A baseline row, then one row per
  # interval: K GPU samples spread over the interval, then the counters at its end. The schedule follows the
  # kernel uptime; a GPU sample gets at most its share of the interval, and samples that no longer fit are
  # skipped. A failed or skipped GPU sample, or a change in the number of GPUs, makes the interval unknown
  local every=10 count=6 k=3 t0 i j base end g gmax gfail up r n ngpu="" slot
  while [ $# -gt 0 ]; do
    case "$1" in
      --every) need2 $# "$1"; every="$(to_seconds "$2")" || exit 1; shift 2 ;;
      --count) need2 $# "$1"; is_uint "$2" || die "sample: --count must be a whole number"; count="$2"; shift 2 ;;
      --gpu-samples) need2 $# "$1"; { is_uint "$2" && [ "$2" -le 20 ]; } || die "sample: --gpu-samples must be 0 to 20"; k="$2"; shift 2 ;;
      *) die "sample: unknown option '$1'" ;;
    esac
  done
  [ "$every" -ge 1 ] || die "sample: --every must be at least 1s"
  [ $((every * count)) -le "$MAX_DURATION" ] || die "sample: --every times --count must be at most 30 days"
  if [ "$k" -gt 0 ]; then   # each GPU probe gets its share of the interval (at least 1 s), never more than PROBE_TIMEOUT
    slot=$((every / k))
    [ "$slot" -ge 1 ] || slot=1
    if is_uint "$PROBE_TIMEOUT" && [ "$PROBE_TIMEOUT" -ge 1 ] && [ "$PROBE_TIMEOUT" -lt "$slot" ]; then slot="$PROBE_TIMEOUT"; fi
    local PROBE_TIMEOUT="$slot"   # probe() sees this through gpu_util_strict
  fi
  t0="$(uptime_cs)"
  [ -n "$t0" ] || die "sample: cannot read $UPTIME_FILE, which the schedule follows"
  printf '# autodl_guard %s sample every=%s count=%s gpu_samples=%s clk_tck=%s\n' "$VERSION" "$every" "$count" "$k" \
    "$(getconf CLK_TCK 2> /dev/null)"
  printf '# epoch\tuptime_cs\tcpu_usec\tio_bytes\tnet_bytes\tgpu_max\tgpu_fail\tguard_ticks\tself_ticks\n'
  sample_row "$t0" na na
  for ((i = 1; i <= count; i++)); do
    base=$((t0 + (i - 1) * every * 100))
    end=$((base + every * 100))
    gmax="" gfail=0
    for ((j = 0; j < k; j++)); do
      wait_until $((base + j * every * 100 / k))
      up="$(uptime_cs)"
      if [ -z "$up" ] || [ "$up" -ge "$end" ]; then gfail=$((gfail + k - j)); break; fi   # out of time: the rest count as failed
      r="$(gpu_util_strict)"
      if [ -z "$r" ]; then gfail=$((gfail + 1)); continue; fi
      g="${r% *}" n="${r#* }"
      [ -n "$ngpu" ] || ngpu="$n"   # the first good sample of the run sets how many GPUs answer
      if [ "$n" != "$ngpu" ]; then gfail=$((gfail + 1)); elif [ -z "$gmax" ] || [ "$g" -gt "$gmax" ]; then gmax="$g"; fi
    done
    if [ "$k" = 0 ]; then gmax=na gfail=na; elif [ "$gfail" != 0 ]; then gmax=""; fi
    wait_until "$end"
    up="$(uptime_cs)"
    [ -n "$up" ] || die "sample: $UPTIME_FILE can no longer be read"
    sample_row "$up" "$gmax" "$gfail"
  done
}

usage() {
  cat << 'EOF'
autodl_guard.sh - decides when to call AutoDL's official /usr/bin/shutdown.
  arm --deadline DUR --keep DUR [--dry-run] [--util-signal] [--env-setup STR]
      [--mode auto|gpu|nogpu] [--grace DUR] [--interval DUR] [--rearm] [--req ID]
                                              exit 5 = already armed for this boot (not for the same --req)
  revive [--restart]                          start the daemon again; settings unchanged
  run NAME [--then-off] [--log PATH] [--req ID] [--cmd-sha256 HEX] (--cmd-stdin | -- CMD...)
                                              the same --req again is not started again; exit 6 =
                                              whether it started cannot be told (see status, logtail)
  keep DUR [--after-job] [--reason TEXT]
  off-when-done [--reason TEXT]
  off-now [--force] [--reason TEXT]           exit 3 = refused, still in use
  deadline DUR
  sample [--every DUR] [--count N] [--gpu-samples K]
                                              read-only: a baseline row of activity counters, then one per interval
  status | logtail NAME|guard [LINES] | version | tick | daemon
  exit 4 = refused because a shutdown is pending
DUR is 90s, 30m, 2h or a plain number of minutes, at most 30 days. flock (util-linux) is required.
EOF
}

main() {
  local c="${1:-help}"
  [ $# -gt 0 ] && shift
  case "$c" in   # without flock every lock would silently vanish: refuse before touching anything
    version | _secs | help | -h | --help | sample) ;;
    *)
      have "$FLOCK_CMD" || die "flock (from util-linux) is required: install it; nothing was changed"
      mkdir -p "$ST" "$JOBS" || die "cannot create $GH"
      boot_init
      ;;
  esac
  case "$c" in
    arm) cmd_arm "$@" ;;
    revive) cmd_revive "$@" ;;
    run) cmd_run "$@" ;;
    _job) cmd_job "$@" ;;
    keep) cmd_keep "$@" ;;
    off-when-done) cmd_off_when_done "$@" ;;
    off-now) cmd_off_now "$@" ;;
    deadline) cmd_deadline "$@" ;;
    status) cmd_status ;;
    logtail) cmd_logtail "$@" ;;
    tick) tick; exit $? ;;
    daemon) daemon_loop ;;
    sample) cmd_sample "$@" ;;
    version) echo "$VERSION" ;;
    _secs) to_seconds "${1:-}" ;;
    help | -h | --help) usage ;;
    *) die "unknown command '$c' (see: $0 help)" ;;
  esac
}

main "$@"
