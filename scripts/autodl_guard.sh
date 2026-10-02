#!/usr/bin/env bash
# autodl_guard.sh - instance-side guard of the autodl-gpu skill.
#
# It only decides WHEN to call AutoDL's official shutdown command
# (/usr/bin/shutdown, see https://www.autodl.com/docs/save_money/). It runs inside the
# instance, so it does not depend on SSH, whose connections AutoDL's public ports
# sometimes drop before authentication.
# 0.8 judges idleness by what the container actually does (design 5.2 to 5.9). Every interval the daemon
# reads the activity signals: GPU utilization (K nvidia-smi probes spread over the interval, GPU mode only),
# and the container's CPU time, disk bytes (cgroup v2 cpu.stat, io.stat) and network bytes (/proc/net/dev),
# as rates since the last check. `tick` then decides, in this order (the first row that holds decides):
#   1. no schema-2 state, not armed for this boot, an arm cut short, or no boot marker -> no new decision
#      (a shutdown already issued is still retried: a forced one as it is, any other only when a live sample
#      shows nothing in use, else it is cancelled)
#   2. a shutdown is pending -> retry it; one that was not forced is cancelled if the instance is in use again
#   3. the uptime cannot be read -> in use
#   4. a signal is busy or unknown (unreadable, went back, too old), keep has not run out (void after the
#      deadline), or a live job's quiet period has not run out -> in use, and "last active" is now
#   5. no reliable signal applies -> no shutdown for idleness at all
#   6. past the deadline -> shut down once idle for grace
#   7. off-when-done and no registered job runs -> shut down once idle for grace
#   8. otherwise -> shut down once idle for idle_s
# Durations are kept in kernel uptime seconds ("up"), which a changed clock does not move; in an AutoDL
# container that is the host's uptime, so every stored "up" value belongs to the boot recorded with it and
# is void in any other. The state lives in $GH/state2 (schema 2); the 0.7 guard's $GH/state is never read
# or written, so a 0.7 process can never mix its state with this one. A registered job, or a screen or tmux
# session, is no longer in use by itself; jobs still matter for off-now (refused while one runs),
# off-when-done and quiet periods. Processes are read from /proc with bash builtins only; a pass over /proc
# that cannot read even its own entries counts every registered job as maybe running, and so does a job
# file that is there but cannot be read.
set -u

VERSION="0.8.0"
SCHEMA=2                   # the state layout this version reads and writes
GH="${AUTODL_GUARD_HOME:-/root/autodl-tmp/.autodl-guard}"
ST="$GH/state$SCHEMA"      # state2: 0.7's state/ is left alone (see the top of this file)
JOBS="$GH/jobs"
LOG="$GH/guard.log"
SELF="$(readlink -f "$0" 2> /dev/null || printf '%s' "$0")"
SHUTDOWN_CMD="${AUTODL_SHUTDOWN_CMD:-/usr/bin/shutdown}"
SYNC_CMD="${AUTODL_SYNC_CMD:-sync}"   # tests only: a stand-in for sync
SYNC_WAIT="${AUTODL_SYNC_WAIT:-60}"   # seconds a shutdown waits for sync before it goes ahead without it
SCREEN_CMD="${AUTODL_SCREEN_CMD:-screen}"
NVSMI_CMD="${AUTODL_NVIDIA_SMI_CMD:-nvidia-smi}"
PROBE_TIMEOUT="${AUTODL_PROBE_TIMEOUT:-10}"
FLOCK_CMD="${AUTODL_FLOCK_CMD:-flock}"
TIMEOUT_CMD="${AUTODL_TIMEOUT_CMD:-timeout}"   # required, like flock (main checks it)
PROC="${AUTODL_TEST_PROC:-/proc}"   # tests only: a stand-in for /proc
CG_DIR="${AUTODL_TEST_CGROUP_DIR:-/sys/fs/cgroup}"   # tests only: a stand-in for the cgroup v2 mount
NET_DEV="${AUTODL_TEST_NET_DEV:-/proc/net/dev}"      # tests only: a stand-in for /proc/net/dev
UPTIME_FILE="${AUTODL_TEST_UPTIME:-/proc/uptime}"   # tests only: a stand-in for /proc/uptime
PROFILE_D="${AUTODL_TEST_PROFILE_D:-/etc/profile.d}"   # tests only: a stand-in for /etc/profile.d
HOOK="$PROFILE_D/autodl-gpu-guard.sh"   # the autostart hook (install-autostart, design 5.8)
HOOK_MARK="# autodl-gpu guard autostart"   # its first line starts so: a file without it is not ours
HOOK_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin   # the PATH the hook gives what it starts
GUARD_SESSION="autodl-guard"
JOB_PREFIX="aj-"
JOB_NAME_RE='^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'
NOGPU_MEM_MAX=3221225472   # 3 GiB; AutoDL non-GPU mode is capped at 2 GiB
MAX_DURATION=2592000       # 30 days
FIRED=10                  # tick's exit status when it issued a shutdown
E_PENDING=4                # command refused: a shutdown is pending
E_ARMED=5                  # arm refused: already armed in this boot
E_UNCERTAIN=6              # run: whether the job started cannot be told (check status)
E_DEADLINE=7               # refused: the deadline has passed (run, keep, quiet, deadline)
E_GATED=8                  # refused: an off-now is being prepared (design 5.5)
PREP_MAX=120               # seconds after which an off-now preparation that never finished is void
OFFNOW_SAMPLE_MAX=60       # the longest live sample off-now takes (--sample)
# default thresholds (design 5.2): GPU 5% on any probe; CPU 5% of one core with a GPU, 3% without (in tenths of
# a percent); disk 5e5 B/s; network 1e4 B/s; 3 GPU probes an interval
DEF_THR_GPU=5 DEF_THR_CPU_GPU=50 DEF_THR_CPU_NOGPU=30 DEF_THR_IO=500000 DEF_THR_NET=10000 DEF_PROBES=3

now() {  # bash builtins, no date process; AUTODL_TEST_EPOCH_SHIFT (tests only) moves the wall clock
  local t
  printf -v t '%(%s)T' -1
  printf '%s' $((t + ${AUTODL_TEST_EPOCH_SHIFT:-0}))
}
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
  have "$TIMEOUT_CMD" || return 125   # never an unbounded probe: without timeout it fails, so reads as unknown
  # nor with a limit that is not a positive number: timeout takes 0 as no limit at all
  { [[ "$PROBE_TIMEOUT" =~ ^[0-9]{1,6}(\.[0-9]{1,3})?$ ]] && [[ "$PROBE_TIMEOUT" =~ [1-9] ]]; } || return 125
  "$TIMEOUT_CMD" "$PROBE_TIMEOUT" "$@" 6>&- 8>&- 9>&-
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
tenths() {  # tenths PERCENT: 0 to 100 with at most one decimal, printed in tenths of a percent (2.5 -> 25)
  [[ "${1:-}" =~ ^(0|[1-9][0-9]?|100)(\.([0-9]))?$ ]] || return 1
  local v=$((BASH_REMATCH[1] * 10 + ${BASH_REMATCH[3]:-0}))
  [ "$v" -le 1000 ] || return 1
  printf '%s' "$v"
}
up_now() {  # whole seconds of kernel uptime; nothing if /proc/uptime cannot be read
  local cs
  cs="$(uptime_cs)"
  [ -n "$cs" ] && printf '%s' $((cs / 100))
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

# ---- part 2: processes and jobs ----
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
# armed for this boot by an 0.8 arm that finished: arm_incomplete exists from the start of an arm to its end,
# and a state without schema 2 (0.7's, say) is never read as ours. A boot that cannot be told apart from
# others never counts as armed
armed_now() {
  [ "$BOOT" != unknown ] && [ "$(get schema '')" = "$SCHEMA" ] && [ "$(get armed_boot '')" = "$BOOT" ] &&
    [ ! -e "$ST/arm_incomplete" ]
}
NEEDS_WHY=""   # set by needs_rearm: the first thing it found wrong
needs_rearm() {  # an arm exists but this version cannot use it: another schema, or a value that is missing, out of
  # range, or a time before the arm
  local k v a
  local -a us
  NEEDS_WHY=""
  [ -n "$(get armed_boot '')$(get armed_at '')" ] || return 1   # never armed: nothing to redo
  v="$(get schema '')"
  if [ "$v" != "$SCHEMA" ]; then NEEDS_WHY="schema=[$v]"; return 0; fi
  for k in armed_up idle_s grace_s interval_s gpu_probes thr_gpu thr_cpu thr_io thr_net deadline_up keep_until_up last_active_up; do
    v="$(get "$k" '')"
    NEEDS_WHY="$k=[$v]"
    is_uint "$v" || return 0
    case "$k" in
      idle_s) [ "$v" -ge 1 ] && [ "$v" -le "$MAX_DURATION" ] || return 0 ;;
      grace_s) [ "$v" -le 3600 ] || return 0 ;;
      interval_s) [ "$v" -ge 1 ] && [ "$v" -le 3600 ] || return 0 ;;
      gpu_probes) [ "$v" -le 20 ] || return 0 ;;
      thr_gpu) [ "$v" -le 100 ] || return 0 ;;
      thr_cpu) [ "$v" -le 1000 ] || return 0 ;;
    esac
  done
  a="$(get armed_up '')"
  for k in deadline_up keep_until_up last_active_up; do   # set at the arm or later (0: none)
    v="$(get "$k" '')"
    if [ "$v" -lt "$a" ] && { [ "$v" != 0 ] || [ "$k" = last_active_up ]; }; then
      NEEDS_WHY="$k=[$v] before armed_up=[$a]"
      return 0
    fi
  done
  v="$(get arm_gen '')"   # UP_CS.PID of the arm; the windows are told apart by it
  NEEDS_WHY="arm_gen=[$v]"
  [[ "$v" =~ ^[0-9]{1,12}\.[0-9]{1,10}$ ]] || return 0
  v="$(get mode '')"
  NEEDS_WHY="mode=[$v]"
  case "$v" in gpu | nogpu) ;; *) return 0 ;; esac
  v="$(get unreliable '')"
  NEEDS_WHY="unreliable=[$v]"
  read -r -a us <<< "$v"
  for v in "${us[@]}"; do
    case "$v" in gpu | cpu | io | net) ;; *) return 0 ;; esac
  done
  NEEDS_WHY=""
  return 1
}
UNUSABLE_NOTED=""   # the daemon logs an unusable state of this boot once per reason, not every interval
note_unusable() {  # (tick, row 1) say in the log why this boot's finished arm cannot be used
  local why=""
  if [ "$BOOT" != unknown ] && [ "$(get armed_boot '')" = "$BOOT" ] && [ ! -e "$ST/arm_incomplete" ] && needs_rearm; then
    if [ "$(get schema '')" = "$SCHEMA" ]; then why="STATE CORRUPT $NEEDS_WHY"; else why="STATE OF ANOTHER VERSION $NEEDS_WHY"; fi
  fi
  if [ -n "$why" ] && [ "$why" != "$UNUSABLE_NOTED" ]; then log "$why: no new decision until arm --rearm"; fi
  UNUSABLE_NOTED="$why"
}
# a dry run of this boot never really shuts down, even once its state is unusable; an arm cut short is never
# a dry run (arm cancels a pending shutdown before it marks itself incomplete, so none is left to retry then)
is_dry() {
  [ "$(get dry_run 0)" = 1 ] && [ "$BOOT" != unknown ] && [ "$(get armed_boot '')" = "$BOOT" ] && [ ! -e "$ST/arm_incomplete" ]
}

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

RUNNING=""   # set by running_jobs
running_jobs() {  # RUNNING = job:NAME, one per line, for every job that runs or cannot be told not to. It runs in
  # this shell with builtins only: no subshell that could die part way and so leave a running job out
  local d
  RUNNING=""
  for d in "$JOBS"/*/; do
    [ -d "$d" ] || continue
    d="${d%/}"
    if job_running "$d"; then RUNNING+="job:${d##*/}"$'\n'; fi
  done
  RUNNING="${RUNNING%$'\n'}"
}

cgroup_mem_limit() {
  local f
  for f in "${AUTODL_CGROUP_MEM_FILE:-}" /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory/memory.limit_in_bytes; do
    if [ -n "$f" ] && [ -r "$f" ]; then head -n 1 "$f" 2> /dev/null; return; fi
  done
}

detect_mode_rc() {  # "MODE RC": gpu, nogpu or unknown (never guess nogpu from a failing nvidia-smi alone), and the
  # exit status of the nvidia-smi probe (124: it timed out)
  local m out rc
  out="$(probe "$NVSMI_CMD" -L 2> /dev/null)"
  rc=$?
  if grep -q '^GPU ' <<< "$out"; then printf 'gpu %s' "$rc"; return 0; fi
  m="$(cgroup_mem_limit)"
  if is_uint "$m" && [ "$m" -le "$NOGPU_MEM_MAX" ]; then printf 'nogpu %s' "$rc"; else printf 'unknown %s' "$rc"; fi
}
detect_mode() {  # gpu | nogpu | unknown
  local r
  r="$(detect_mode_rc)"
  echo "${r%% *}"
}

# ---- activity signals (design 5.2). The counter readers use bash builtins only, so they may run under the
# state lock, and print nothing when their source cannot be read or does not look exactly as expected: a
# caller can tell "unknown" from a real zero. Only gpu_util_strict starts a program, never under a lock ----
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
  # fails or times out, or any line is not one number from 0 to 100 (blanks around it are fine)
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
sleep_until() {  # sleep_until CS: sleep, holding no lock, until the uptime reaches CS centiseconds; 1 if it cannot be read
  local cur d s
  while :; do
    cur="$(uptime_cs)"
    [ -n "$cur" ] || return 1
    d=$(($1 - cur))
    [ "$d" -gt 0 ] || return 0
    printf -v s '%d.%02d' $((d / 100)) $((d % 100))
    nolock sleep "$s"
  done
}
is_unreliable() { case " $(get unreliable '') " in *" $1 "*) return 0 ;; esac; return 1; }   # is_unreliable SIGNAL
no_reliable() {  # no signal both applies in the armed mode and is reliable: nothing shows whether it is in use
  local s
  for s in cpu io net; do is_unreliable "$s" || return 1; done
  if [ "$(get mode '')" = gpu ] && ! is_unreliable gpu; then return 1; fi
  return 0
}
probes_due() {  # GPU probes per interval: gpu_probes when armed in GPU mode with the GPU reliable, else 0
  if armed_now && [ "$(get mode '')" = gpu ] && ! is_unreliable gpu; then bounded gpu_probes 0 0 20; else echo 0; fi
}
read_counters() {  # read_counters UP_CS: "UP_CS CPU_USEC IO_BYTES NET_BYTES" read now, u for each that cannot be read
  local c i n
  c="$(cpu_usage_usec)" i="$(io_bytes)" n="$(net_bytes)"
  printf '%s %s %s %s' "$1" "${c:-u}" "${i:-u}" "${n:-u}"
}
keep_end() {  # the end of the keep in uptime seconds, 0 if there is none; the deadline ends it early (design 5.4)
  local k d
  k="$(get keep_until_up 0)" d="$(get deadline_up 0)"
  is_uint "$k" || k=0
  is_uint "$d" || d=0
  if [ "$d" -gt 0 ] && [ "$d" -lt "$k" ]; then k="$d"; fi
  printf '%s' "$k"
}
write_quiet() {  # write_quiet DIR UNTIL_UP REASON: a job's quiet period (design 5.3; the caller holds the lock), one file
  # replaced whole: "UNTIL_UP BOOT", then the reason. When it cannot be written, the declaration before stands
  write_file "$1/quiet" "$2 $BOOT"$'\n'"${3//$'\n'/ }"
}
QUIET_END=""   # set by job_quiet
job_quiet() {  # job_quiet DIR: 0 when the job has a quiet period of this boot and runs, or cannot be told not to.
  # QUIET_END is its end in uptime seconds, or bad when the declaration is there but cannot be read, or its first
  # line is not a positive end and a boot. A declaration of another boot, or of a job that has ended, is void (it
  # ends with the job); without the file there is none
  local d="$1" v
  local -a f
  QUIET_END=""
  jread v "$d/quiet"
  case $? in 1) return 1 ;; 2) v="" ;; esac
  read -r -a f <<< "${v%%$'\n'*}"
  if [ ${#f[@]} -eq 2 ] && [ "${f[1]}" != "$BOOT" ]; then return 1; fi   # another boot's
  job_running "$d" || return 1
  if [ ${#f[@]} -eq 2 ] && is_uint "${f[0]}" && [ "${f[0]}" -gt 0 ]; then QUIET_END="${f[0]}"; else QUIET_END=bad; fi
}
PROT_LIVE="" PROT_END=0 PROT_WHY=""   # set by protection_scan
protection_scan() {  # protection_scan UP: what protects the instance at UP besides the signals (design 5.3, 5.4).
  # PROT_LIVE lists what still runs: "keep", "quiet:NAME" for a live job's quiet period, "quiet:NAME:unknown" for
  # one whose end cannot be read (in use: unknown). PROT_END is the latest end not after UP of the keep (the
  # deadline ends it early) or of a live job's quiet period, 0 if none, and PROT_WHY names it. Tick and status
  # both use it: once a protection has ended, the last activity moves on to its end, never back
  local up="$1" d ke
  PROT_LIVE="" PROT_END=0 PROT_WHY=""
  ke="$(keep_end)"
  if [ "$up" -lt "$ke" ]; then PROT_LIVE=keep; elif [ "$ke" -gt 0 ]; then PROT_END="$ke" PROT_WHY=keep; fi
  for d in "$JOBS"/*/; do
    [ -d "$d" ] || continue
    d="${d%/}"
    job_quiet "$d" || continue
    if [ "$QUIET_END" = bad ]; then
      PROT_LIVE="${PROT_LIVE:+$PROT_LIVE }quiet:${d##*/}:unknown"
    elif [ "$up" -lt "$QUIET_END" ]; then
      PROT_LIVE="${PROT_LIVE:+$PROT_LIVE }quiet:${d##*/}"
    elif [ "$QUIET_END" -gt "$PROT_END" ]; then
      PROT_END="$QUIET_END" PROT_WHY="quiet:${d##*/}"
    fi
  done
}
BAD_QUIET_NOTED=""   # the daemon logs quiet periods that cannot be read once, not every interval
note_bad_quiet() {  # (tick) say in the log which live jobs' quiet periods cannot be read
  local b="" w
  local -a ws
  read -r -a ws <<< "$PROT_LIVE"
  for w in "${ws[@]}"; do
    case "$w" in quiet:*:unknown) w="${w#quiet:}"; b="${b:+$b }${w%:unknown}" ;; esac
  done
  if [ -n "$b" ] && [ "$b" != "$BAD_QUIET_NOTED" ]; then
    log "STATE CORRUPT quiet of job(s) [$b]: the declaration cannot be read, counted as in use while the job runs"
  fi
  BAD_QUIET_NOTED="$b"
}

RATE_ST="" RATE_VAL=""   # set by rate_judge
rate_judge() {  # rate_judge NAME DELTA DT_CS THR: RATE_ST busy (at or above the threshold) or idle, RATE_VAL the rate
  # (CPU in percent of one core, disk and network in bytes per second). The rule of design 5.2, for the checks and
  # for off-now's live sample
  local d="$2" dt="$3" thr="$4"
  [ "$d" -le 90000000000000000 ] || d=90000000000000000   # far above any threshold; keeps the products in 64 bits
  if [ "$1" = cpu ]; then   # tenths of a percent of one core, shown as a percent
    if [ "$d" -ge $((thr * dt * 10)) ]; then RATE_ST=busy; else RATE_ST=idle; fi
    d=$((d / (dt * 10)))
    RATE_VAL="$((d / 10)).$((d % 10))"
  else   # bytes per second
    if [ $((d * 100)) -ge $((thr * dt)) ]; then RATE_ST=busy; else RATE_ST=idle; fi
    RATE_VAL=$((d * 100 / dt))
  fi
}
SIG_WHY=""      # set by eval_signals: the signals that count as in use, e.g. "cpu:busy io:unknown"
NO_RELIABLE=""  # set by eval_signals: 1 when no signal both applies and is reliable
eval_signals() {  # eval_signals UP_CS GPU_MAX GPU_FAILS GPU_N (the caller holds the lock). Each signal is busy, idle,
  # unknown, na (does not apply in this mode) or off (marked unreliable at the arm). The counters become rates
  # since the baseline in state2/counters: a counter that cannot be read now or then, went back, or whose baseline
  # is under 1 s old (too soon) or older than 1.5 intervals plus 0.4 of one, at most 10 s (too late), is unknown.
  # A daemon checks every interval (its probes never run past it), so a baseline two intervals old means a check
  # could not store its own, and averaging over that long could water a burst of work down; the limit stays under
  # two intervals for every interval. The GPU is unknown when a probe of the interval failed or none was made. At
  # or above its threshold a signal is busy. Writes state2/signals and the new baseline
  local cs="$1" gmax="$2" gfails="$3" gn="$4" lim ivl dt="" i name st val out="" nrel=0
  local -a bv cv names=(gpu cpu io net)
  ivl="$(bounded interval_s 60 1 3600)"
  lim=$((ivl * 150 + (ivl * 40 < 1000 ? ivl * 40 : 1000)))   # centiseconds
  read -r -a cv <<< "$(read_counters "$cs")"
  read -r -a bv <<< "$(get counters '')"
  if [ ${#bv[@]} -eq 4 ] && is_counter "${bv[0]}"; then dt=$((cs - bv[0])); fi
  SIG_WHY="" NO_RELIABLE=""
  for i in 0 1 2 3; do
    name="${names[i]}" val=""
    if [ "$i" = 0 ] && [ "$(get mode '')" != gpu ]; then
      st=na
    elif is_unreliable "$name"; then
      st=off
    elif [ "$i" = 0 ]; then
      if ! is_uint "$gn" || [ "$gn" = 0 ]; then
        st=unknown val=no-probe
      elif [ "$gfails" != 0 ] || ! is_uint "$gmax"; then
        st=unknown val=probe-failed
      else
        val="$gmax"
        if [ "$gmax" -ge "$(get thr_gpu '')" ]; then st=busy; else st=idle; fi
      fi
    elif ! is_counter "${cv[i]:-}" || ! is_counter "${bv[i]:-}" || [ -z "$dt" ]; then
      st=unknown val=unread
    elif [ "$dt" -lt 0 ] || [ "${cv[i]}" -lt "${bv[i]}" ]; then
      st=unknown val=went-back
    elif [ "$dt" -lt 100 ]; then
      st=unknown val=too-soon
    elif [ "$dt" -gt "$lim" ]; then
      st=unknown val=too-old
    else
      rate_judge "$name" $((cv[i] - bv[i])) "$dt" "$(get "thr_$name" '')"
      st="$RATE_ST" val="$RATE_VAL"
    fi
    out+="$name=$st:$val "
    case "$st" in busy | unknown) SIG_WHY+="$name:$st " ;; esac
    case "$st" in na | off) ;; *) nrel=$((nrel + 1)) ;; esac
  done
  [ "$nrel" -gt 0 ] || NO_RELIABLE=1
  put signals "${out% }" || log "cannot write the signals (disk full?)"
  # the new baseline. Without it the next check would average over a longer time, which can water a burst of work
  # down below the thresholds, so a check that cannot store it counts as in use
  if ! put counters "${cv[*]}"; then
    log "cannot write the counters' baseline (disk full?); this check counts as in use"
    SIG_WHY+="counters:unstored "
  fi
  SIG_WHY="${SIG_WHY% }"
}

# One interval's GPU probes, made holding no lock and handed to tick as "MAX FAILS N"
GW_MAX="" GW_FAILS=0 GW_N=0 GW_NGPU="" GW_GEN=""
gw_reset() { GW_MAX="" GW_FAILS=0 GW_N="$1" GW_NGPU=""; }
gw_probe() {  # one probe: a failure, or another number of GPUs than the interval's first answer, counts as failed
  local r g n
  r="$(unlocked gpu_util_strict)"
  if [ -z "$r" ]; then GW_FAILS=$((GW_FAILS + 1)); return 0; fi
  g="${r% *}" n="${r#* }"
  [ -n "$GW_NGPU" ] || GW_NGPU="$n"
  if [ "$n" != "$GW_NGPU" ]; then GW_FAILS=$((GW_FAILS + 1)); elif [ -z "$GW_MAX" ] || [ "$g" -gt "$GW_MAX" ]; then GW_MAX="$g"; fi
}

# ---- the off-now transaction (design 5.5): a preparation that gates changes, a live sample, then the commit ----
PREP_TOKEN="" PREP_UP="" PREP_BOOT="" PREP_STATE=none   # set by prep_read
prep_read() {  # the off-now preparation, "TOKEN UP BOOT": valid while it is this boot's and at most PREP_MAX s old
  # (also while the uptime cannot be read: it may be), void when older, of another boot, or not readable as such
  local v u
  local -a f
  PREP_TOKEN="" PREP_UP="" PREP_BOOT="" PREP_STATE=none
  [ -e "$ST/prep" ] || return 0
  PREP_STATE=void
  jread v "$ST/prep" || return 0
  read -r -a f <<< "$v"
  { [ ${#f[@]} -eq 3 ] && is_uint "${f[1]}"; } || return 0
  PREP_TOKEN="${f[0]}" PREP_UP="${f[1]}" PREP_BOOT="${f[2]}"
  { [ "$BOOT" != unknown ] && [ "$PREP_BOOT" = "$BOOT" ]; } || return 0
  u="$(up_now)"
  if [ -z "$u" ] || { [ "$u" -ge "$PREP_UP" ] && [ $((u - PREP_UP)) -le "$PREP_MAX" ]; }; then PREP_STATE=valid; fi
}
refuse_if_gated() {  # call with the lock held: exit 8 while an off-now of this boot is being prepared
  prep_read
  if [ "$PREP_STATE" = valid ]; then
    unlock
    fail "$E_GATED" "an off-now is being prepared (void once over ${PREP_MAX}s old, counted on the uptime); try again once it has finished"
  fi
}
drop_prep() {  # drop_prep TOKEN (the caller holds the lock): remove the preparation only while it is still this one
  prep_read
  [ -n "$1" ] && [ "$PREP_TOKEN" = "$1" ] || return 0
  nolock rm -f "$ST/prep" || log "cannot remove the off-now preparation; it is void once over ${PREP_MAX}s old (counted on the uptime)"
}
OFFNOW="" OFFNOW_TOKEN="" OFFNOW_REASON=""   # set by off-now; with OFFNOW, attempt_shutdown says "shutdown issuing"
test_barrier() {  # test_barrier DIR STEP (tests only): mark the Nth arrival at STEP in DIR/STEP.N and, while
  # DIR/STEP.hold exists, wait (30 s at most) for DIR/STEP.N.go
  local n=1 i
  while [ -e "$1/$2.$n" ]; do n=$((n + 1)); done
  : > "$1/$2.$n"
  if [ -e "$1/$2.hold" ]; then
    for ((i = 0; i < 300; i++)); do [ ! -e "$1/$2.$n.go" ] || break; nolock sleep 0.1; done
  fi
}
offnow_step() {  # offnow_step STEP: off-now has reached STEP. Tests only: AUTODL_TEST_OFFNOW_BARRIER=DIR holds it
  # there (test_barrier); AUTODL_TEST_OFFNOW_DIE_AT=STEP kills off-now there, as if its caller had died
  [ -n "$OFFNOW" ] || return 0
  [ -z "${AUTODL_TEST_OFFNOW_BARRIER:-}" ] || test_barrier "$AUTODL_TEST_OFFNOW_BARRIER" "$1"
  if [ "${AUTODL_TEST_OFFNOW_DIE_AT:-}" = "$1" ]; then kill -KILL $$; fi
  return 0
}
offnow_pending() {  # a shutdown of this boot is already pending: say so, exit 4 (the caller holds no lock)
  echo "shutdown already pending (kind=$(get shutdown_kind '') reason=[$(get shutdown_reason '')]); the daemon retries it"
  log "OFF-NOW stopped: a shutdown is already pending reason=[$OFFNOW_REASON]"
  exit "$E_PENDING"
}
offnow_refuse() {  # offnow_refuse LOG_TEXT HEAD LINES: refused, exit 3; only the preparation goes (the caller holds no lock)
  lock
  drop_prep "$OFFNOW_TOKEN"
  unlock
  log "OFF-NOW refused $1 reason=[$OFFNOW_REASON]"
  printf '%s\n%s\n' "$2" "$3"
  exit 3
}
OS_WHY="" OS_END="" OS_NOREL="" OS_SIGNALS=""   # set by offnow_sample
offnow_sample() {  # offnow_sample S: the signals over S seconds, sampled holding no lock and judged as a check judges
  # them (for off-now, for idle-check, and for tick when it retries an issued shutdown in a state it cannot use).
  # Armed for this boot: its mode, thresholds and unreliable signals; otherwise the mode seen now and the
  # default thresholds (a mode that cannot be told is probed as a GPU one: failed probes then count as in use).
  # The GPU is probed K times, at most once a second, the last probe right at the end; the CPU that off-now and
  # the verified daemon used meanwhile is taken off (not when it cannot be read); the rates are over at least S
  # seconds, never less than the time slept. OS_WHY: "name:state" for each busy or unknown signal; OS_END: the
  # uptime (cs) at the end, empty if unreadable; OS_NOREL=1 when no signal both applies and is reliable
  local s="$1" mode k=0 unrel="" thr_gpu thr_cpu thr_io thr_net thr hz dp own0 own1 dm0="" dm1="" sub=0
  local cs0 cs1 dt j slot w i d name st val nrel=0 out="" pt
  local -a c0 c1 names=(gpu cpu io net)
  OS_WHY="" OS_END="" OS_NOREL="" OS_SIGNALS=""
  if armed_now && ! needs_rearm; then
    mode="$(get mode '')" unrel="$(get unreliable '')"
    thr_gpu="$(get thr_gpu '')" thr_cpu="$(get thr_cpu '')" thr_io="$(get thr_io '')" thr_net="$(get thr_net '')"
    if [ "$mode" = gpu ] && [[ " $unrel " != *" gpu "* ]]; then k="$(bounded gpu_probes 0 0 20)"; fi
  else
    mode="$(detect_mode)"
    thr_gpu="$DEF_THR_GPU" thr_io="$DEF_THR_IO" thr_net="$DEF_THR_NET"
    if [ "$mode" = gpu ]; then thr_cpu="$DEF_THR_CPU_GPU"; else thr_cpu="$DEF_THR_CPU_NOGPU"; fi
    if [ "$mode" != nogpu ]; then mode=gpu k="$DEF_PROBES"; fi
  fi
  [ "$k" -le "$s" ] || k="$s"
  hz="$(nolock getconf CLK_TCK 2> /dev/null)"
  { is_uint "$hz" && [ "$hz" -gt 0 ]; } || hz=""
  dp="$(get daemon_pid '')"
  # the daemon's CPU is taken off once: when the daemon itself samples (tick, row 1), it is "own" already
  { [ "$dp" != "$$" ] && same_process "$dp" "$(get daemon_pstart '')"; } || dp=""
  # the counters first and last, the CPU ticks of off-now and the daemon inside them: what is taken off was used
  # within the counters' window, never more
  cs0="$(uptime_cs)"
  read -r -a c0 <<< "$(read_counters "${cs0:-0}")"
  own0="$(proc_cpu_ticks $$)"
  [ -z "$dp" ] || dm0="$(proc_cpu_ticks "$dp")"
  if [ -n "$OFFNOW" ]; then
    offnow_step sample-started
  elif [ -n "${AUTODL_TEST_OFFNOW_BARRIER:-}" ]; then   # tests only: the other callers (idle-check, tick) stop here too
    test_barrier "$AUTODL_TEST_OFFNOW_BARRIER" sample-started
  fi
  gw_reset "$k"
  if [ "$k" -gt 0 ]; then
    slot=$((s * 100 / k))   # centiseconds before each probe
    pt=$((s / k))
    [ "$pt" -ge 1 ] || pt=1
    if is_uint "$PROBE_TIMEOUT" && [ "$PROBE_TIMEOUT" -ge 1 ] && [ "$PROBE_TIMEOUT" -lt "$pt" ]; then pt="$PROBE_TIMEOUT"; fi
    local PROBE_TIMEOUT="$pt"   # probe() sees this through gpu_util_strict
    printf -v w '%d.%02d' $((slot / 100)) $((slot % 100))
    for ((j = 0; j < k; j++)); do
      nolock sleep "$w"
      gw_probe
    done
  else
    nolock sleep "$s"
  fi
  own1="$(proc_cpu_ticks $$)"
  [ -z "$dp" ] || dm1="$(proc_cpu_ticks "$dp")"
  cs1="$(uptime_cs)"
  read -r -a c1 <<< "$(read_counters "${cs1:-0}")"
  OS_END="$cs1"
  dt=$((s * 100))
  if [ -n "$cs0" ] && [ -n "$cs1" ] && [ $((cs1 - cs0)) -gt "$dt" ]; then dt=$((cs1 - cs0)); fi
  if [ -n "$hz" ] && is_counter "$own0" && is_counter "$own1" && [ "$own1" -ge "$own0" ]; then   # ticks of 1/hz s
    sub=$(((own1 - own0) * 1000000 / hz))
    if is_counter "$dm0" && is_counter "$dm1" && [ "$dm1" -ge "$dm0" ]; then sub=$((sub + (dm1 - dm0) * 1000000 / hz)); fi
  fi
  for i in 0 1 2 3; do
    name="${names[i]}" val=""
    if [ "$i" = 0 ] && [ "$mode" != gpu ]; then
      st=na
    elif [[ " $unrel " == *" $name "* ]]; then
      st=off
    elif [ "$i" = 0 ]; then
      if [ "$k" = 0 ]; then
        st=unknown val=no-probe
      elif [ "$GW_FAILS" != 0 ] || ! is_uint "$GW_MAX"; then
        st=unknown val=probe-failed
      else
        val="$GW_MAX"
        if [ "$GW_MAX" -ge "$thr_gpu" ]; then st=busy; else st=idle; fi
      fi
    elif ! is_counter "${c1[i]:-}" || ! is_counter "${c0[i]:-}"; then
      st=unknown val=unread
    elif [ "${c1[i]}" -lt "${c0[i]}" ]; then
      st=unknown val=went-back
    else
      d=$((c1[i] - c0[i]))
      case "$name" in cpu) thr="$thr_cpu" d=$((d > sub ? d - sub : 0)) ;; io) thr="$thr_io" ;; *) thr="$thr_net" ;; esac
      rate_judge "$name" "$d" "$dt" "$thr"
      st="$RATE_ST" val="$RATE_VAL"
    fi
    out+="$name=$st:$val "
    case "$st" in busy | unknown) OS_WHY+="$name:$st " ;; esac
    case "$st" in na | off) ;; *) nrel=$((nrel + 1)) ;; esac
  done
  [ "$nrel" -gt 0 ] || OS_NOREL=1
  OS_WHY="${OS_WHY% }" OS_SIGNALS="${out% }"
}

# ---- part 3: shutdown, tick, daemon ----
# Shutdown kinds: forced (off-now --force) is retried as it is; idle, deadline, when-done and now (off-now)
# are re-checked before each retry and cancelled if the instance is in use again (row 2 of tick).
# A shutdown notes the boot it was issued in: a flag an earlier boot left behind is never retried, since
# the instance was started again after it (0.7 did not note the boot; then its arm's boot stands in)
pending_here() {
  local b
  [ "$(get shutdown_pending 0)" = 1 ] && [ "$BOOT" != unknown ] || return 1
  b="$(get shutdown_boot '')"
  [ -n "$b" ] || b="$(get armed_boot '')"
  [ "$b" = "$BOOT" ]
}
record_shutdown() {  # record_shutdown REASON KIND (caller holds the lock): the commit; 2 if it could not be recorded
  # the pending flag down first, the details next, the flag up last: a retry never finds a pending shutdown
  # without its kind, and a request that fails half way never leaves an older flag (another boot's, say) standing.
  # The details count only once the flag is up; the last shutdown's reason, which status shows, is written after it
  if ! { put shutdown_pending 0 && put shutdown_boot "$BOOT" && put shutdown_reason "$1" && put shutdown_kind "$2" &&
    put shutdown_attempts 0 && offnow_step metadata && put shutdown_pending 1; }; then
    if [ "$2" != deadline ]; then
      log "cannot record the pending shutdown, not shutting down: $1"
      return 2
    fi
    log "cannot record the pending shutdown; shutting down anyway because the deadline is reached"
  fi
  put last_shutdown_reason "$1" || log "cannot record the reason of this shutdown (disk full?)"
  return 0
}
request_shutdown() {  # request_shutdown REASON KIND (caller holds the lock): record it, then run the shutdown command
  record_shutdown "$1" "$2" || return 2
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
  log "SHUTDOWN attempt=$n kind=$(get shutdown_kind '') reason=[$(get shutdown_reason '')] up=$(up_now) deadline_up=$(get deadline_up 0) keep_until_up=$(get keep_until_up 0)"
  # sync and the shutdown command hold none of the locks: if they hang and the daemon is killed,
  # the locks are free for the next daemon; a sync that never returns is not waited for (flush_disks)
  flush_disks
  if is_dry; then
    put dry_run_fired "$(get shutdown_reason '')"
    log "DRY_RUN: would run $SHUTDOWN_CMD"
    return 0
  fi
  if [ -n "$OFFNOW" ]; then   # after sync, which may take a minute: said earlier, it would mislead the caller
    echo "shutdown issuing"
    offnow_step before-shutdown
  fi
  nolock "$TIMEOUT_CMD" 120 "$SHUTDOWN_CMD"
  rc=$?
  [ "$rc" -eq 0 ] || log "shutdown command failed rc=$rc; retrying every interval"
  return "$rc"
}

tick() {  # tick [GPU_MAX GPU_FAILS GPU_N GEN]: one check in the order of design 5.9 (the rows at the top of this
  # file); the interval's GPU probes were made before, holding no lock, under the arm GEN. FIRED when it issued a
  # shutdown
  local gmax="${1:-}" gfails="${2:-0}" gn="${3:-0}" gen="${4:-}"
  local t cs up="" why rw kw="" la dl kind limit idle_for rc lost="" s
  PROC_OK=""   # a fresh pass over /proc for this check
  PROT_LIVE="" PROT_END=0 PROT_WHY=""
  lock
  t="$(now)"
  put heartbeat "$t" || log "cannot write the heartbeat (disk full?)"
  prep_read   # an off-now preparation left void (older than PREP_MAX, another boot's, unreadable) goes
  if [ "$PREP_STATE" = void ] && nolock rm -f "$ST/prep"; then
    log "OFF-NOW PREPARATION VOID, removed [token=$PREP_TOKEN up=$PREP_UP boot=$PREP_BOOT]"
  fi
  # 1. a state this version cannot use, an arm cut short, or none for this boot: no new decision. A shutdown this
  # boot already issued is still retried. A forced one as it is; any other only while nothing is in use, as in
  # row 2: the policy cannot be read here, so a live sample judges it (the mode seen now, the default thresholds),
  # taken holding no lock. No keep or quiet period can have come since the shutdown was issued (run, keep and quiet
  # are refused while one is pending), so the signals are all there is to look at
  if ! armed_now || needs_rearm; then
    note_unusable
    if pending_here; then
      kind="$(get shutdown_kind '')"
      if [ "$kind" != forced ] && ! is_dry; then
        unlock
        s="${AUTODL_OFFNOW_SAMPLE:-5}"
        { [[ "$s" =~ ^[1-9][0-9]?$ ]] && [ "$s" -le "$OFFNOW_SAMPLE_MAX" ]; } || s=5
        offnow_sample "$s"
        rw="$OS_WHY"
        [ -n "$OS_END" ] || rw="${rw:+$rw }uptime:unknown"
        [ -z "$OS_NOREL" ] || rw="${rw:+$rw }no-reliable-signal"
        lock
        # what the sample was taken for may be gone by now (a rearm, a retry by another check): the next check decides
        if { armed_now && ! needs_rearm; } || ! pending_here || [ "$(get shutdown_kind '')" != "$kind" ]; then
          unlock
          return 0
        fi
        if [ -n "$rw" ]; then
          put shutdown_pending 0
          log "PENDING SHUTDOWN CANCELLED kind=$kind: in use again [$rw] (a live sample: the state cannot be used)"
          unlock
          return 0
        fi
      fi
      attempt_shutdown
      unlock
      return "$FIRED"
    fi
    unlock
    return 0
  fi
  # a window that began under an earlier arm (the old daemon can take the lock between an arm's unlock and its
  # restart): its probes and rates belong to the old settings, so this check decides nothing and keeps the baseline
  if [ "$gen" != "$(get arm_gen '')" ]; then
    log "CHECK SKIPPED: its window began before the last arm"
    unlock
    return 0
  fi
  cs="$(uptime_cs)"
  if [ -n "$cs" ]; then
    up=$((cs / 100))
    eval_signals "$cs" "$gmax" "$gfails" "$gn"
    why="$SIG_WHY"
    protection_scan "$up"   # 4. a keep, or a live job's quiet period, that still runs
    kw="$PROT_LIVE"
    note_bad_quiet
    # checks without the clock counted as in use but could not say when: the first one with it again says now
    if [ "$(get clock_lost 0)" = 1 ]; then why="${why:+$why }uptime:recovered"; lost=1; fi
  else
    why="uptime:unknown"   # 3. without the clock nothing can be timed: in use
    put clock_lost 1 || log "cannot note that the uptime could not be read (disk full?)"
  fi
  # 2. a pending shutdown: a forced one is retried as it is. Any other only while nothing is in use; one the guard
  # decided on by itself (idle, deadline, when-done) also waits for a keep and needs a reliable signal to show it,
  # while an off-now the AI asked for (now) does not wait for a keep, which it clears when it commits
  if pending_here && ! is_dry; then
    kind="$(get shutdown_kind idle)"
    rw="$why"
    case "$kind" in
      forced) rw="" ;;
      now) ;;
      *)
        rw="$rw${kw:+${rw:+ }$kw}"
        [ -z "$NO_RELIABLE" ] || rw="${rw:+$rw }no-reliable-signal"
        ;;
    esac
    if [ -z "$rw" ]; then
      attempt_shutdown
      unlock
      return "$FIRED"
    fi
    # anything unknown counts as in use, rates that came too soon or too late included (a slow check before this
    # one, a long wait for the lock): what happened meanwhile is never assumed idle
    put shutdown_pending 0
    log "PENDING SHUTDOWN CANCELLED kind=$kind: in use again [$rw]"
  fi
  why="$why${kw:+${why:+ }$kw}"
  # 3 and 4. in use: the last activity is now (not timed without the clock)
  if [ -n "$why" ]; then
    put active_why "$why"
    if [ -n "$up" ]; then
      if put last_active_up "$up" && put last_active_at "$t" && [ -n "$lost" ]; then put clock_lost 0; fi
    fi
    unlock
    return 0
  fi
  # 5. nothing shows whether it is in use: no shutdown for idleness at all
  if [ -n "$NO_RELIABLE" ]; then unlock; return 0; fi
  la="$(get last_active_up '')"
  if [ "$la" -gt "$up" ]; then   # later than now in this boot: corrupt; counted as in use, so it heals itself
    log "STATE CORRUPT last_active_up=[$la] is after the uptime $up; counted as in use"
    put last_active_up "$up"
    put last_active_at "$t"
    put active_why state
    unlock
    return 0
  fi
  if [ "$PROT_END" -gt "$la" ]; then   # a keep or a quiet period ended since the last check: in use until its end
    la="$PROT_END"
    put last_active_up "$la"
    put last_active_at $((t - (up - la)))
    put active_why "$PROT_WHY"
  fi
  idle_for=$((up - la))
  dl="$(get deadline_up 0)"
  if [ "$dl" -gt 0 ] && [ "$up" -ge "$dl" ]; then   # 6. past the deadline: once idle for grace
    kind=deadline limit="$(get grace_s 120)"
    why="the deadline has passed and nothing was in use for ${idle_for}s"
  elif [ "$(get off_when_done 0)" = 1 ] && running_jobs && [ -z "$RUNNING" ]; then   # 7. off-when-done, no job runs
    kind=when-done limit="$(get grace_s 120)"
    why="off-when-done ($(get off_when_done_reason '')): no job runs and nothing was in use for ${idle_for}s"
  else   # 8. once idle for idle_s
    kind=idle limit="$(get idle_s '')"
    why="idle: nothing was in use for ${idle_for}s"
  fi
  if [ "$idle_for" -lt "$limit" ]; then unlock; return 0; fi
  request_shutdown "$why" "$kind"
  rc=$?
  unlock
  [ "$rc" -eq 2 ] && return 0
  return "$FIRED"
}

daemon_alive() {  # the daemon's lock is busy => a daemon holds it. Read-only, so status changes nothing: without the
  # lock file no daemon ever ran here (the daemon creates it)
  [ -e "$ST/.daemon.lock" ] || return 1
  ("$FLOCK_CMD" -n 7 6>&- 8>&- 9>&- || exit 0; exit 1) 7< "$ST/.daemon.lock"
}

window_end() {  # window_end BASE END: END, or the deadline if it falls after BASE and before END (no lock held)
  local dl
  dl="$(get deadline_up 0)"
  if armed_now && is_uint "$dl" && [ "$dl" -gt 0 ] && [ $((dl * 100)) -gt "$1" ] && [ $((dl * 100)) -lt "$2" ]; then
    printf '%s' $((dl * 100))
  else
    printf '%s' "$2"
  fi
}
gpu_window() {  # the daemon's interval, holding no lock: K GPU probes spread over it as sample spreads them, then the
  # rest of it. Every wait is at most 5 s and rereads the deadline, so one set meanwhile (deadline, a rearm) ends the
  # window within seconds, and the probes still due are spread again over what is left, one a second at most. Each
  # probe's limit is at most its share, 5 s and what is left of the window, never more than the probe timeout. The
  # first probe is made at the window's start, so every window has a reading; probes the window has no room for are
  # not failures. A window cut to under a second gives a rate too soon to use, so that check counts as in use.
  # Without the uptime it just sleeps (tick then counts the check as in use). GW_GEN is the arm the window began under
  local s k base end ne j up slot win rem d w next lim sbase swin sk sj
  GW_GEN="$(get arm_gen '')"   # before any setting (the arm writes it after them)
  s="$(bounded interval_s 60 1 3600)"
  k="$(probes_due)"
  gw_reset "$k"
  base="$(uptime_cs)"
  if [ -z "$base" ]; then nolock sleep "$s"; return 0; fi
  end="$(window_end "$base" $((base + s * 100)))"
  win=$((end - base))
  # the schedule: sk probes spread over swin centiseconds from sbase, sj of them made
  sbase="$base" swin="$win" sk="$k" sj=0 j=0
  slot=1
  if [ "$k" -gt 0 ]; then
    slot=$((win / 100 / k))
    [ "$slot" -ge 1 ] || slot=1
    if is_uint "$PROBE_TIMEOUT" && [ "$PROBE_TIMEOUT" -ge 1 ] && [ "$PROBE_TIMEOUT" -lt "$slot" ]; then slot="$PROBE_TIMEOUT"; fi
  fi
  local PROBE_TIMEOUT="$slot"   # probe() sees this through gpu_util_strict
  while :; do
    up="$(uptime_cs)"
    if [ -z "$up" ]; then nolock sleep "$s"; break; fi
    ne="$(window_end "$base" "$end")"
    if [ "$ne" -lt "$end" ]; then   # a deadline set meanwhile: the probes still due go into what is left
      end="$ne"
      if [ "$sj" -lt "$sk" ] && [ "$up" -lt "$end" ]; then
        sk=$((sk - sj))
        [ "$sk" -le $(((end - up) / 100)) ] || sk=$(((end - up) / 100))
        [ "$sk" -ge 1 ] || sk=1
        sbase="$up" swin=$((end - up)) sj=0
      fi
    fi
    [ "$up" -lt "$end" ] || break
    next="$end"
    if [ "$sj" -lt "$sk" ]; then
      next=$((sbase + sj * swin / sk))
      if [ "$up" -ge "$next" ]; then
        # never past the window's end, never longer than 5 s: the next check's rates span one interval, and a
        # deadline set meanwhile is seen soon
        lim=$((slot * 100))
        [ $((swin / sk)) -ge "$lim" ] || lim=$((swin / sk))   # the share once a shortened window was spread again
        [ "$lim" -ge 1 ] || lim=1   # a window of a few centiseconds: 0 would be no limit at all to timeout
        rem=$((end - up))
        [ "$rem" -ge "$lim" ] || lim="$rem"
        [ "$lim" -le 500 ] || lim=500
        printf -v PROBE_TIMEOUT '%d.%02d' $((lim / 100)) $((lim % 100))
        gw_probe
        sj=$((sj + 1)) j=$((j + 1))
        continue
      fi
    fi
    [ "$next" -le "$end" ] || next="$end"   # a probe planned past the end is not waited for
    d=$((next - up))
    [ "$d" -le 500 ] || d=500
    printf -v w '%d.%02d' $((d / 100)) $((d % 100))
    nolock sleep "$w"
  done
  GW_N="$j"   # the probes made; a failed one makes the GPU unknown, and so does a window without any
}

daemon_loop() {
  local rc
  exec 8> "$ST/.daemon.lock" || die "cannot open the daemon lock"
  "$FLOCK_CMD" -n 8 6>&- 9>&- || { echo "daemon already running"; exit 0; }
  if seven_blocks; then   # design 5.7: never next to a live 0.7 daemon
    log "DAEMON not started: $(seven_why)"
    exit 0
  fi
  # without its PID and start time on record, revive --restart cannot stop this daemon (it signals
  # only a verified process); guarding matters more, so the daemon runs anyway
  { put daemon_pid "$$" && put daemon_pstart "$(proc_start $$)"; } ||
    log "cannot record the daemon's PID and start time (disk full?): revive --restart cannot stop this daemon"
  put daemon_version "$VERSION"
  put guard_sty "${STY:-}"   # the screen session this daemon runs in, if any
  put heartbeat "$(now)" || log "cannot write the heartbeat (disk full?)"
  log "DAEMON start pid=$$ version=$VERSION mode=$(get mode '') interval=$(bounded interval_s 60 1 3600)s gpu_probes=$(probes_due) sty=${STY:-none}"
  # a shutdown of this boot pending, or the deadline passed: check at once instead of after the first interval
  if pending_here || past_deadline_now; then
    check_now
    rc=$?
    if [ "$rc" -eq "$FIRED" ] && is_dry; then
      log "DAEMON exit after DRY_RUN shutdown"
      exit 0
    fi
  fi
  # every interval: its GPU probes, then one check of the signals as rates over the interval
  while :; do
    gpu_window
    tick "$GW_MAX" "$GW_FAILS" "$GW_N" "$GW_GEN"
    rc=$?
    if [ "$rc" -eq "$FIRED" ] && is_dry; then
      log "DAEMON exit after DRY_RUN shutdown"
      exit 0
    fi
  done
}

check_now() {  # one check now, its GPU probes made at once before it, holding no lock (the tick command; the
  # daemon when it starts with a shutdown pending or past the deadline). A forced shutdown pending is retried
  # without them: it does not look at the signals, and probes that hang would only hold it up
  local j k
  GW_GEN="${AUTODL_TEST_WINDOW_GEN:-$(get arm_gen '')}"   # before any setting; the variable: tests only
  k="$(probes_due)"
  if pending_here && [ "$(get shutdown_kind '')" = forced ]; then k=0; fi
  gw_reset "$k"
  for ((j = 0; j < k; j++)); do gw_probe; done
  tick "$GW_MAX" "$GW_FAILS" "$GW_N" "$GW_GEN"
}
past_deadline_now() {  # armed for this boot, with a deadline the uptime has reached (read without the lock)
  local d u
  armed_now || return 1
  d="$(get deadline_up 0)" u="$(up_now)"
  is_uint "$d" && [ "$d" -gt 0 ] && [ -n "$u" ] && [ "$u" -ge "$d" ]
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
  if seven_blocks; then die "$(seven_why): no 0.8 daemon is started next to it (two daemons would each shut the instance down by their own rules)"; fi
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
  [ "$(get schema '')" = "$SCHEMA" ] ||
    die "this boot was armed by an older guard, whose state this version does not read (status: needs_rearm=1); run arm --rearm"
  ! needs_rearm || die "the state of this boot is damaged (status: needs_rearm=1); run arm --rearm"
}
UP_NOW=""   # the uptime refuse_past_deadline read
refuse_past_deadline() {  # call with the lock held, after require_armed: exit 7 once the deadline has passed (design 5.4)
  local d
  UP_NOW="$(up_now)"
  if [ -z "$UP_NOW" ]; then
    unlock
    die "cannot read the kernel uptime ($UPTIME_FILE), on which every duration is counted"
  fi
  d="$(get deadline_up 0)"
  if [ "$d" -gt 0 ] && [ "$UP_NOW" -ge "$d" ]; then
    unlock
    fail "$E_DEADLINE" "the deadline has passed: no new job, keep, quiet period or deadline; the instance shuts down once nothing is in use for $(get grace_s 120)s"
  fi
}
SEVEN_RC=0   # set by seven_blocks: flock's exit status
seven_blocks() {  # 0 when no 0.8 daemon may run here (design 5.7): a 0.7 guard daemon holds its lock (0.7's
  # state/.daemon.lock), or the lock cannot be tried. Only the lock tells (0.7 takes it before it writes its PID);
  # without the lock file no 0.7 daemon ever ran here, and none is created in 0.7's directory
  local lf="$GH/state/.daemon.lock"
  SEVEN_RC=0
  [ -e "$lf" ] || return 1
  "$FLOCK_CMD" -n -E 99 "$lf" true 6>&- 7>&- 8>&- 9>&- 2> /dev/null
  SEVEN_RC=$?
  [ "$SEVEN_RC" != 0 ]
}
seven_why() {  # why seven_blocks said so
  if [ "$SEVEN_RC" = 99 ]; then
    printf 'a 0.7 guard daemon runs in this boot (it holds %s)' "$GH/state/.daemon.lock"
  else
    printf 'cannot tell whether a 0.7 guard daemon runs (%s could not be tried, flock rc=%s)' "$GH/state/.daemon.lock" "$SEVEN_RC"
  fi
}
refuse_next_to_07() {  # arm and revive: refused next to a live 0.7 daemon, as two daemons would each shut the instance
  # down by their own rules (every start of a 0.8 daemon is refused there too: start_daemon, daemon_loop)
  seven_blocks || return 0
  die "$(seven_why): two daemons would each shut the instance down by their own rules; upgrade on a fresh boot (shut down, start, deploy, arm), or let 0.7 finish first"
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
arm_daemon() {  # arm_daemon [restart]: after arm the configuration is in place, so a daemon that does not start must
  # be said plainly. After a new configuration a running daemon is restarted: its window was planned with the old
  # one, and its next check would mix the old window's GPU probes with the new baseline, thresholds and interval
  [ -z "${AUTODL_NO_DAEMON:-}" ] || return 0
  if [ "${1:-}" = restart ] && daemon_alive && ! (stop_daemon) > /dev/null 2>&1; then
    fail 1 "armed for this boot, but the running guard daemon could not be restarted with the new settings (see $LOG); run revive --restart"
  fi
  if ! (start_daemon) > /dev/null 2>&1; then
    fail 1 "armed for this boot, but the guard daemon did not start (see $LOG); fix that, then run revive"
  fi
}

# The configuration write_arm writes: cmd_arm sets it from its options, boot from the settings an arm kept
A_MODE="" A_IDLE="" A_GRACE="" A_INTERVAL="" A_PROBES="" A_THR_GPU="" A_THR_CPU="" A_THR_IO="" A_THR_NET=""
A_UNREL="" A_CALIB="" A_COVERAGE="" A_ENVS="" A_DRY=0 A_DL=0 A_KP="" A_REQ=""
write_arm() {  # write_arm WHY (arm, rearm or boot), both locks held: the whole configuration of this boot, from A_*;
  # 1, with nothing changed, when the uptime cannot be read. A pending shutdown is cancelled first (what --rearm is
  # for), so an arm cut short never leaves one to retry. Then arm_incomplete ("T KIND MODE"), gone last: an arm cut
  # short in between is never taken as done (tick decides nothing, run, keep and the like refuse, boot does not arm)
  # and a resend redoes it. Before it goes, an arm or rearm also keeps its settings for the next start in its mode
  # (design 5.8); an arm that finds an earlier arm's arm_incomplete first drops that mode's kept settings, which may
  # never have been committed. This boot's marker comes just before arm_incomplete goes
  local t cs up kind=arm u v=""
  local -a f
  [ "$1" != boot ] || kind=boot
  t="$(now)"
  cs="$(uptime_cs)"
  [ -n "$cs" ] || return 1
  up=$((cs / 100))
  if [ "$kind" = arm ] && [ -e "$ST/arm_incomplete" ]; then
    jread v "$ST/arm_incomplete"
    [[ "$v" != *$'\n'* ]] || v=""   # one line, as write_arm writes it, or not readable as such
    read -r -a f <<< "$v"
    if [ ${#f[@]} -eq 3 ] && is_uint "${f[0]}" && [ "${f[1]}" = boot ] && { [ "${f[2]}" = gpu ] || [ "${f[2]}" = nogpu ]; }; then
      :   # a boot arm cut short: boot keeps no settings
    elif [ ${#f[@]} -eq 3 ] && is_uint "${f[0]}" && [ "${f[1]}" = arm ] && { [ "${f[2]}" = gpu ] || [ "${f[2]}" = nogpu ]; }; then
      nolock rm -f "$ST/boot.${f[2]}" || die "arm: cannot drop the settings kept by an arm cut short"
    else   # not readable as such: the settings of either mode may be uncommitted
      nolock rm -f "$ST/boot.gpu" "$ST/boot.nogpu" || die "arm: cannot drop the settings kept by an arm cut short"
    fi
  fi
  putx shutdown_pending 0
  putx arm_incomplete "$t $kind $A_MODE"
  putx schema "$SCHEMA"
  putx shutdown_reason ""
  putx shutdown_kind ""
  putx shutdown_attempts 0
  putx shutdown_boot ""
  putx clock_lost 0
  putx armed_at "$t"
  putx armed_up "$up"
  putx mode "$A_MODE"
  putx idle_s "$A_IDLE"
  putx grace_s "$A_GRACE"
  putx interval_s "$A_INTERVAL"
  putx gpu_probes "$A_PROBES"
  putx thr_gpu "$A_THR_GPU"
  putx thr_cpu "$A_THR_CPU"
  putx thr_io "$A_THR_IO"
  putx thr_net "$A_THR_NET"
  putx unreliable "$A_UNREL"
  putx calib "$A_CALIB"
  putx calib_coverage "$A_COVERAGE"
  putx env_setup "$A_ENVS"
  putx dry_run "$A_DRY"
  if [ "$A_DL" -gt 0 ]; then
    putx deadline_up $((up + A_DL))
    putx deadline_at $((t + A_DL))
  else
    putx deadline_up 0
    putx deadline_at 0
  fi
  if [ -n "$A_KP" ]; then
    putx keep_until_up $((up + A_KP))
    putx keep_until_at $((t + A_KP))
  else
    putx keep_until_up 0
    putx keep_until_at 0
  fi
  putx off_when_done 0
  putx off_when_done_reason ""
  putx last_active_up "$up"   # the arm counts as the last activity (design 5.2)
  putx last_active_at "$t"
  putx active_why "$1"
  putx counters "$(read_counters "$cs")"   # the counters' baseline, read at the moment of the arm
  nolock rm -f "$ST/signals" "$ST/dry_run_fired" || die "arm: cannot remove the last check's results"
  # this arm's generation, after every setting: a window that reads it before any setting has the settings of this
  # arm, or of a later one whose generation discards the window (tick)
  putx arm_gen "$cs.$$"
  putx arm_req "$A_REQ"
  putx armed_by "$kind"
  if [ "$kind" = arm ]; then   # what the next start in this mode arms with (boot's settings are not the user's choice)
    u="${A_UNREL// /,}"
    putx "boot.$A_MODE" "$A_IDLE $A_GRACE $A_INTERVAL $A_PROBES $A_THR_GPU $A_THR_CPU $A_THR_IO $A_THR_NET $A_CALIB $A_COVERAGE ${u:--} $A_DRY"
  fi
  putx armed_boot "$BOOT"
  nolock rm -f "$ST/arm_incomplete" || die "cannot finish the arm: $ST/arm_incomplete cannot be removed"
}

cmd_arm() {  # configure this power-on (design 5.1); refused if already armed in this boot unless --rearm. Every
  # option is checked before anything is written
  local idle_d="" deadline_d="" keep_d="" dry=0 envs="" mode=auto grace=120 interval=60 rearm=0 req="" calib=default
  local probes="" thr_gpu="" thr_cpu="" thr_io="$DEF_THR_IO" thr_net="$DEF_THR_NET" unrel="" u="" w idle dl=0 kp=""
  local why
  local coverage=""
  local -a ul
  while [ $# -gt 0 ]; do
    case "$1" in
      --req) need2 $# "$1"; req="$2"; shift 2 ;;
      --idle) need2 $# "$1"; idle_d="$2"; shift 2 ;;
      --deadline) need2 $# "$1"; deadline_d="$2"; shift 2 ;;
      --keep) need2 $# "$1"; keep_d="$2"; shift 2 ;;
      --dry-run) dry=1; shift ;;
      --env-setup) need2 $# "$1"; envs="$2"; shift 2 ;;
      --mode) need2 $# "$1"; mode="$2"; shift 2 ;;
      --grace) need2 $# "$1"; grace="$(to_seconds "$2")" || exit 1; shift 2 ;;
      --interval) need2 $# "$1"; interval="$(to_seconds "$2")" || exit 1; shift 2 ;;
      --gpu-probes)
        need2 $# "$1"
        { is_uint "$2" && [ "$2" -le 20 ]; } || die "arm: --gpu-probes must be 0 to 20"
        probes="$2"
        shift 2
        ;;
      --thr-gpu)
        need2 $# "$1"
        { is_uint "$2" && [ "$2" -le 100 ]; } || die "arm: --thr-gpu must be a whole percent from 0 to 100"
        thr_gpu="$2"
        shift 2
        ;;
      --thr-cpu)
        need2 $# "$1"
        thr_cpu="$(tenths "$2")" || die "arm: --thr-cpu must be a percent of one core from 0 to 100, with at most one decimal"
        shift 2
        ;;
      --thr-io) need2 $# "$1"; is_uint "$2" || die "arm: --thr-io must be a whole number of bytes per second"; thr_io="$2"; shift 2 ;;
      --thr-net) need2 $# "$1"; is_uint "$2" || die "arm: --thr-net must be a whole number of bytes per second"; thr_net="$2"; shift 2 ;;
      --unreliable) need2 $# "$1"; unrel="$2"; shift 2 ;;
      --calib)
        need2 $# "$1"
        [[ "$2" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || die "arm: --calib must be 1 to 64 characters from A-Z a-z 0-9 . _ -"
        calib="$2"
        shift 2
        ;;
      --calib-coverage)   # what the calibration checked, as ctl judged it (design 5.2)
        need2 $# "$1"
        case "$2" in verified | unverified) ;; *) die "arm: --calib-coverage must be verified or unverified" ;; esac
        coverage="$2"
        shift 2
        ;;
      --rearm) rearm=1; shift ;;
      --util-signal) die "arm: --util-signal was removed in 0.8.0: in GPU mode the GPU is always one of the signals" ;;
      *) die "arm: unknown option '$1'" ;;
    esac
  done
  if [ "$calib" = default ]; then
    [ -z "$coverage" ] || die "arm: --calib-coverage goes with --calib"
    coverage=default
  elif [ -z "$coverage" ]; then
    coverage=unverified   # a calibration that measured idleness only says nothing about work
  fi
  [ -n "$idle_d" ] || die "arm: --idle is required: how long nothing may be in use before the shutdown, e.g. --idle 15m"
  idle="$(to_seconds "$idle_d")" || exit 1
  [ "$idle" -ge 1 ] || die "arm: --idle must be at least 1s"
  if [ -n "$deadline_d" ]; then
    dl="$(to_seconds "$deadline_d")" || exit 1
    [ "$dl" -ge 1 ] || die "arm: --deadline must be at least 1s"
  fi
  if [ -n "$keep_d" ]; then kp="$(to_seconds "$keep_d")" || exit 1; fi
  [ -z "$req" ] || [[ "$req" =~ ^[A-Za-z0-9]{8,64}$ ]] || die "arm: --req must be 8 to 64 letters or digits"
  [ "$grace" -le 3600 ] || die "arm: --grace must be at most 1h"
  if [ "$interval" -lt 1 ] || [ "$interval" -gt 3600 ]; then die "arm: --interval must be between 1s and 1h"; fi
  IFS=, read -r -a ul <<< "$unrel"
  for w in "${ul[@]}"; do
    case "$w" in gpu | cpu | io | net) ;; *) die "arm: --unreliable takes a comma-separated list of gpu, cpu, io and net" ;; esac
  done
  for w in gpu cpu io net; do   # stored in this order, space-separated
    case ",$unrel," in *",$w,"*) u+="${u:+ }$w" ;; esac
  done
  case "$mode" in
    auto)
      mode="$(detect_mode)"
      [ "$mode" != unknown ] || die "arm: cannot tell the mode (nvidia-smi lists no GPU and the memory limit is not the 2 GiB of non-GPU mode); check it and pass --mode gpu or --mode nogpu"
      ;;
    gpu | nogpu) ;;
    *) die "arm: --mode must be auto, gpu or nogpu" ;;
  esac
  # unless given, the thresholds of the mode (design 5.2, calibrated on 2026-09-30); CPU in tenths of a percent
  [ -n "$thr_gpu" ] || thr_gpu="$DEF_THR_GPU"
  if [ "$mode" = gpu ]; then
    [ -n "$thr_cpu" ] || thr_cpu="$DEF_THR_CPU_GPU"
    [ -n "$probes" ] || probes="$DEF_PROBES"
    if [ "$probes" = 0 ] && [[ " $u " != *" gpu "* ]]; then
      die "arm: --gpu-probes 0 leaves the GPU unread, which counts as in use; to leave the GPU out on purpose, add --unreliable gpu"
    fi
  else
    [ -n "$thr_cpu" ] || thr_cpu="$DEF_THR_CPU_NOGPU"
    probes=0   # nvidia-smi cannot run without a GPU
  fi
  # the arm belongs to this boot only: a boot that cannot be told apart from the next one is refused, and so is
  # an arm without the uptime, on which every duration is counted
  [ "$BOOT" != unknown ] || die "arm: cannot read this boot's marker ($PROC/1/stat); refusing to arm"
  [ -n "$(uptime_cs)" ] || die "arm: cannot read the kernel uptime ($UPTIME_FILE), on which every duration is counted; refusing to arm"
  refuse_next_to_07
  life_lock
  lock
  refuse_if_gated
  # a resend of an arm or rearm that already took effect in this boot changes nothing
  if [ -n "$req" ] && armed_now && [ "$(get arm_req '')" = "$req" ]; then
    unlock
    arm_daemon
    life_unlock
    echo "armed (this request had already armed this boot; settings unchanged)"
    return 0
  fi
  if armed_now && [ "$rearm" != 1 ] && [ "$(get armed_by arm)" != boot ]; then   # boot's arm is replaced as it is;
    # an arm cut short may be finished by any arm
    unlock
    fail "$E_ARMED" "already armed for this boot (armed_at=$(get armed_at 0)); use keep, deadline or revive, or arm --rearm to replace the whole configuration"
  fi
  A_MODE="$mode" A_IDLE="$idle" A_GRACE="$grace" A_INTERVAL="$interval" A_PROBES="$probes" A_THR_GPU="$thr_gpu"
  A_THR_CPU="$thr_cpu" A_THR_IO="$thr_io" A_THR_NET="$thr_net" A_UNREL="$u" A_CALIB="$calib" A_COVERAGE="$coverage"
  A_ENVS="$envs" A_DRY="$dry" A_DL="$dl" A_KP="$kp" A_REQ="$req"
  if [ "$rearm" = 1 ]; then why=rearm; else why=arm; fi
  if ! write_arm "$why"; then
    unlock
    die "arm: the kernel uptime ($UPTIME_FILE) can no longer be read; nothing was changed"
  fi
  unlock
  log "ARM mode=$mode idle=${idle}s deadline=+${dl}s keep=+${kp:-0}s grace=${grace}s interval=${interval}s gpu_probes=$probes thr_gpu=$thr_gpu thr_cpu=$thr_cpu thr_io=$thr_io thr_net=$thr_net unreliable=[$u] calib=$calib coverage=$coverage dry_run=$dry rearm=$rearm"
  if no_reliable; then
    printf 'warning: in %s mode no signal is both applicable and reliable, so the guard cannot see whether the instance is in use and never shuts it down for idleness (status: no_reliable_signal=1)\n' "$mode" >&2
  fi
  if [ -n "${AUTODL_TEST_ARM_GAP:-}" ]; then   # tests only: the gap between the unlock and the restart, held open
    local i
    for ((i = 0; i < 300; i++)); do [ ! -e "$AUTODL_TEST_ARM_GAP" ] || break; nolock sleep 0.1; done
  fi
  arm_daemon restart
  life_unlock
  echo "armed mode=$mode idle=${idle}s deadline_in=${dl}s keep_in=${kp:-0}s dry_run=$dry"
}

cmd_revive() {  # revive [--restart]: make sure the daemon runs; never touches the configuration
  local restart=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --restart) restart=1; shift ;;
      *) die "revive: unknown option '$1'" ;;
    esac
  done
  refuse_next_to_07
  # under the lifecycle lock, which run, arm, keep and deadline also hold, but not under the state
  # lock: a daemon waiting in tick for that lock would not act on TERM
  life_lock
  require_armed
  # the gate, read without the state lock, which revive never waits for (above). A preparation is put in place
  # whole, and one written just after this look only meets a daemon starting, which changes no policy
  prep_read
  [ "$PREP_STATE" != valid ] ||
    fail "$E_GATED" "an off-now is being prepared (void once over ${PREP_MAX}s old, counted on the uptime); try again once it has finished"
  if [ "$restart" = 1 ]; then stop_daemon; fi
  (start_daemon) > /dev/null 2>&1 ||
    fail 1 "the guard daemon is NOT running now (see $LOG): running jobs and the deadline are unguarded until revive succeeds; the console's scheduled shutdown still applies"
  life_unlock
  echo "daemon running (script version $VERSION)"
}

# ---- autostart (design 5.8): boot runs at every container start, started by the hook of install-autostart ----
boot_step() {  # boot_step STEP: boot has reached STEP. Tests only: AUTODL_TEST_BOOT_BARRIER=DIR holds it there
  [ -z "${AUTODL_TEST_BOOT_BARRIER:-}" ] || test_barrier "$AUTODL_TEST_BOOT_BARRIER" "$1"
  return 0
}
boot_env() {  # boot_env NAME DEFAULT MIN MAX: the plain whole number in the environment variable NAME, from MIN to
  # MAX; otherwise DEFAULT, logged when NAME was set to something else
  local v="${!1:-}"
  if [ -n "$v" ]; then
    if is_uint "$v" && [ "$v" -ge "$3" ] && [ "$v" -le "$4" ]; then printf '%s' "$v"; return 0; fi
    log "BOOT ignores $1=[$v]: not a whole number from $3 to $4; $2 instead"
  fi
  printf '%s' "$2"
}
read_boot_settings() {  # read_boot_settings MODE [quiet]: 0 with A_* set to the settings the last arm in MODE kept,
  # 1 when there are none, 2 when they cannot be used, logged as STATE CORRUPT unless quiet (status reads them too).
  # They must pass arm's own checks, the cross-field ones included. Nothing else carries over: no env_setup, deadline
  # or keep
  local m="$1" v="" why="" w u=""
  local -a f us
  jread v "$ST/boot.$m"
  case $? in
    1) return 1 ;;
    2) why="cannot be read" ;;
  esac
  if [ -z "$why" ]; then
    read -r -a f <<< "$v"
    if [[ "$v" == *$'\n'* ]] || [ ${#f[@]} -ne 12 ]; then
      why="not one line of 12 fields"
    elif ! is_uint "${f[0]}" || [ "${f[0]}" -lt 1 ] || [ "${f[0]}" -gt "$MAX_DURATION" ]; then
      why="idle"
    elif ! is_uint "${f[1]}" || [ "${f[1]}" -gt 3600 ]; then
      why="grace"
    elif ! is_uint "${f[2]}" || [ "${f[2]}" -lt 1 ] || [ "${f[2]}" -gt 3600 ]; then
      why="interval"
    elif ! is_uint "${f[3]}" || [ "${f[3]}" -gt 20 ]; then
      why="GPU probes"
    elif ! is_uint "${f[4]}" || [ "${f[4]}" -gt 100 ]; then
      why="GPU threshold"
    elif ! is_uint "${f[5]}" || [ "${f[5]}" -gt 1000 ]; then
      why="CPU threshold"
    elif ! is_uint "${f[6]}" || ! is_uint "${f[7]}"; then
      why="disk or network threshold"
    elif ! [[ "${f[8]}" =~ ^[A-Za-z0-9._-]{1,64}$ ]]; then
      why="calibration"
    elif { [ "${f[8]}" = default ] && [ "${f[9]}" != default ]; } ||
      { [ "${f[8]}" != default ] && [ "${f[9]}" != verified ] && [ "${f[9]}" != unverified ]; }; then
      why="calibration coverage"
    elif [ "${f[11]}" != 0 ] && [ "${f[11]}" != 1 ]; then
      why="dry run"
    elif [ "${f[10]}" != - ]; then
      IFS=, read -r -a us <<< "${f[10]}"
      [ ${#us[@]} -gt 0 ] || why="unreliable signals"
      for w in "${us[@]}"; do
        case "$w" in gpu | cpu | io | net) ;; *) why="unreliable signals" ;; esac
      done
    fi
  fi
  if [ -z "$why" ]; then
    for w in gpu cpu io net; do   # in arm's order, space-separated
      case ",${f[10]}," in *",$w,"*) u+="${u:+ }$w" ;; esac
    done
    if [ "$m" = nogpu ] && [ "${f[3]}" != 0 ]; then
      why="GPU probes without a GPU"
    elif [ "$m" = gpu ] && [ "${f[3]}" = 0 ] && [[ " $u " != *" gpu "* ]]; then
      why="no GPU probes while the GPU is not marked unreliable"
    fi
  fi
  if [ -n "$why" ]; then
    [ "${2:-}" = quiet ] || log "STATE CORRUPT boot.$m ($why): [${v//$'\n'/ }]"
    return 2
  fi
  A_MODE="$m" A_IDLE="${f[0]}" A_GRACE="${f[1]}" A_INTERVAL="${f[2]}" A_PROBES="${f[3]}" A_THR_GPU="${f[4]}"
  A_THR_CPU="${f[5]}" A_THR_IO="${f[6]}" A_THR_NET="${f[7]}" A_CALIB="${f[8]}" A_COVERAGE="${f[9]}" A_UNREL="$u"
  A_DRY="${f[11]}" A_ENVS="" A_DL=0 A_KP="" A_REQ=""
  return 0
}
boot_defaults() {  # boot_defaults MODE: a start in a mode no arm kept settings for keeps the other mode's times and
  # dry run, but not what is calibrated per mode: thresholds, GPU probes and unreliable signals are this mode's defaults
  A_MODE="$1" A_THR_GPU="$DEF_THR_GPU" A_THR_IO="$DEF_THR_IO" A_THR_NET="$DEF_THR_NET" A_UNREL="" A_CALIB=default
  A_COVERAGE=default
  if [ "$1" = gpu ]; then A_PROBES="$DEF_PROBES" A_THR_CPU="$DEF_THR_CPU_GPU"; else A_PROBES=0 A_THR_CPU="$DEF_THR_CPU_NOGPU"; fi
}
BOOT_WHY=""   # set by boot_check: why boot does not arm now, empty when it may
boot_check() {  # both locks held: a live 0.7 daemon, an arm of this boot, a shutdown pending in this boot, an arm cut
  # short (it may have kept settings it never committed), an off-now being prepared; the first that holds decides
  BOOT_WHY=""
  if seven_blocks; then
    BOOT_WHY=skipped:07
  elif armed_now; then
    BOOT_WHY=skipped:armed
  elif pending_here; then
    BOOT_WHY=skipped:pending
  elif [ -e "$ST/arm_incomplete" ]; then
    BOOT_WHY=skipped:incomplete
  else
    prep_read
    [ "$PREP_STATE" != valid ] || BOOT_WHY=skipped:gated
  fi
}
BOOT_MODE=""   # set by boot_detect: gpu, nogpu or unknown
boot_detect() {  # boot_detect BUDGET PAUSE (seconds), holding no lock: the mode, tried until it is known or the budget is
  # spent. A probe is charged the uptime it took but at least 1 cs, as /proc/uptime counts in 10 ms steps and a quick
  # probe often shows none, and its whole limit only when it timed out or the uptime cannot be read; a pause is charged
  # at least what it asked for. So a quick failure costs next to nothing, and detection still ends in about BUDGET
  # seconds on a clock that stands still
  local left=$(($1 * 100)) pause=$(($2 * 100)) lim a b pt r rc spent
  BOOT_MODE=unknown
  while [ "$left" -gt 0 ]; do
    lim=$((left < 1000 ? left : 1000))   # a probe takes 10 s at most, and no more than is left
    printf -v pt '%d.%02d' $((lim / 100)) $((lim % 100))
    a="$(uptime_cs)"
    r="$(PROBE_TIMEOUT="$pt" unlocked detect_mode_rc)"
    b="$(uptime_cs)"
    BOOT_MODE="${r%% *}" rc="${r##* }"
    if [ "$rc" = 124 ] || ! is_uint "$a" || ! is_uint "$b"; then
      spent="$lim"
    elif [ "$b" -gt "$a" ]; then
      spent=$((b - a))
    else
      spent=1
    fi
    left=$((left - spent))
    case "$BOOT_MODE" in gpu | nogpu) return 0 ;; esac
    BOOT_MODE=unknown
    [ "$left" -gt 0 ] || break
    lim=$((left < pause ? left : pause))
    printf -v pt '%d.%02d' $((lim / 100)) $((lim % 100))
    a="$(uptime_cs)"
    nolock sleep "$pt"
    b="$(uptime_cs)"
    if is_uint "$a" && is_uint "$b" && [ $((b - a)) -gt "$lim" ]; then spent=$((b - a)); else spent="$lim"; fi
    left=$((left - spent))
  done
  BOOT_MODE=unknown
}
boot_note() { put autostart "$BOOT $1" || log "cannot note the autostart result (disk full?)"; }   # both locks held
boot_clean_env() {  # boot runs itself again once without what the start environment may carry for shells: BASH_ENV,
  # ENV, SHELLOPTS, BASHOPTS, CDPATH, GLOBIGNORE and exported functions. bash -p keeps them out of the guard's own
  # shells, not out of the programs they start; a bash script among those (a shutdown command may be one) would read
  # BASH_ENV or take the functions. Once they are gone this finds none, so it runs itself again at most once
  local e n
  local -a u=()
  [ -r /proc/self/environ ] || return 0
  while IFS= read -r -d '' e; do
    n="${e%%=*}"
    case "$n" in BASH_FUNC_* | BASH_ENV | ENV | SHELLOPTS | BASHOPTS | CDPATH | GLOBIGNORE) u+=(-u "$n") ;; esac
  done < /proc/self/environ
  [ ${#u[@]} -gt 0 ] || return 0
  exec env "${u[@]}" bash -p "$SELF" boot 6>&- 8>&- 9>&-
}
boot_daemon() {  # boot ends as the daemon of this boot (none in tests without a daemon)
  [ -z "${AUTODL_NO_DAEMON:-}" ] || exit 0
  exec env -u STY -u AUTODL_GUARD_JOB bash -p "$SELF" daemon 6>&- 8>&- 9>&-   # -p: no BASH_ENV, no imported functions
}
boot_why() {  # boot_why RESULT: what the log says for a start that is not armed
  case "$1" in
    skipped:07) seven_why ;;
    skipped:armed) printf 'this boot is armed already; the daemon runs on' ;;
    skipped:pending) printf 'a shutdown of this boot is pending; the daemon retries it' ;;
    skipped:incomplete) printf 'an arm was cut short (arm_incomplete), and settings it kept may never have been committed; an arm finishes it' ;;
    skipped:gated) printf 'an off-now was still being prepared' ;;
    skipped:no-settings) printf 'no arm has kept settings to arm with (every arm keeps them for the next start in its mode)' ;;
    skipped:mode-unknown) printf 'cannot tell the mode (nvidia-smi lists no GPU and the memory limit is not that of non-GPU mode); counted as in use until an arm' ;;
    *) printf '%s' "$1" ;;
  esac
}
cmd_boot() {  # boot: started, detached, by the autostart hook at every container start (design 5.8). Unless this boot is
  # armed already, it arms it with the settings the last arm kept for the mode it finds, then runs as the daemon. Every
  # round checks under both locks first (boot_check); the mode is told holding no lock, then everything is checked
  # again. Without settings, or when the mode cannot be told, it arms nothing and starts no daemon: with nothing to
  # decide by, that counts as in use until an arm
  local budget pause gate_left s usable="" waited="" other from why
  [ $# -eq 0 ] || die "boot takes no options"
  boot_clean_env
  exec 2>> "$LOG"   # nothing reads its output (the hook sends it nowhere): errors go to the log
  log "BOOT start pid=$$ boot=$BOOT"
  boot_step start
  if [ "$BOOT" = unknown ]; then
    log "BOOT does not arm: cannot read this boot's marker ($PROC/1/stat)"
    exit 0
  fi
  budget="$(boot_env AUTODL_BOOT_MODE_BUDGET 60 1 600)"
  pause="$(boot_env AUTODL_BOOT_MODE_WAIT 5 1 60)"
  gate_left="$(boot_env AUTODL_BOOT_GATE_WAIT 130 0 600)"
  if [ -z "$(uptime_cs)" ]; then   # every duration is counted on it, and write_arm needs it
    life_lock
    lock
    boot_note skipped:no-uptime
    unlock
    life_unlock
    log "BOOT does not arm: cannot read the kernel uptime ($UPTIME_FILE)"
    exit 0
  fi
  BOOT_MODE=""
  while :; do
    life_lock
    lock
    boot_check
    if [ "$BOOT_WHY" = skipped:gated ] && [ "$gate_left" -gt 0 ]; then   # wait for it to commit, give up or go void
      unlock
      life_unlock
      [ -n "$waited" ] || log "BOOT waits: an off-now is being prepared"
      waited=1
      s=$((gate_left < 2 ? gate_left : 2))
      nolock sleep "$s"
      gate_left=$((gate_left - s))
      continue
    fi
    if [ -z "$BOOT_WHY" ] && [ -z "$usable" ]; then   # the settings of either mode, checked (a bad one logged) once
      if read_boot_settings gpu; then usable+=" gpu"; fi
      if read_boot_settings nogpu; then usable+=" nogpu"; fi
      [ -n "$usable" ] || BOOT_WHY=skipped:no-settings
    fi
    if [ -z "$BOOT_WHY" ] && [ -z "$BOOT_MODE" ]; then   # the mode, holding no lock; then all is checked again
      unlock
      life_unlock
      boot_detect "$budget" "$pause"
      boot_step detected
      continue
    fi
    [ -n "$BOOT_WHY" ] || [ "$BOOT_MODE" != unknown ] || BOOT_WHY=skipped:mode-unknown
    if [ -z "$BOOT_WHY" ]; then
      if read_boot_settings "$BOOT_MODE" quiet; then
        from=saved
      else
        if [ "$BOOT_MODE" = gpu ]; then other=nogpu; else other=gpu; fi
        if read_boot_settings "$other" quiet; then
          from=fallback
          boot_defaults "$BOOT_MODE"
        else
          BOOT_WHY=skipped:no-settings
        fi
      fi
    fi
    if [ -z "$BOOT_WHY" ] && ! write_arm boot; then BOOT_WHY=skipped:no-uptime; fi
    if [ -n "$BOOT_WHY" ]; then
      boot_note "$BOOT_WHY"
      why="$(boot_why "$BOOT_WHY")"
      unlock
      life_unlock
      log "BOOT does not arm: $why"
      case "$BOOT_WHY" in skipped:armed | skipped:pending) boot_daemon ;; esac
      exit 0
    fi
    boot_note "armed:$from"
    unlock
    life_unlock
    log "BOOT armed mode=$A_MODE from=$from idle=${A_IDLE}s grace=${A_GRACE}s interval=${A_INTERVAL}s gpu_probes=$A_PROBES thr_gpu=$A_THR_GPU thr_cpu=$A_THR_CPU thr_io=$A_THR_IO thr_net=$A_THR_NET unreliable=[$A_UNREL] calib=$A_CALIB coverage=$A_COVERAGE dry_run=$A_DRY"
    [ "$A_DRY" = 0 ] || log "BOOT the settings kept are a dry run, so this boot is armed as one"
    boot_daemon
  done
}

hook_wait() {  # the seconds the hook's detached shell waits for the guard script: 60, which only tests may change
  # (AUTODL_TEST_HOOK_WAIT, read only with AUTODL_TEST_PROFILE_D); 1 when that is not a whole number from 1 to 60
  local v="${AUTODL_TEST_HOOK_WAIT:-}"
  if [ -z "${AUTODL_TEST_PROFILE_D:-}" ] || [ -z "$v" ]; then printf '60'; return 0; fi
  [[ "$v" =~ ^[1-9][0-9]?$ ]] && [ "$v" -le 60 ] || return 1
  printf '%s' "$v"
}
hook_text() {  # the hook install-autostart writes for this script and this AUTODL_GUARD_HOME, without its last newline.
  # 1 when a path holds a quote or a newline (both go into it in single quotes), 2 when one is not absolute, 3 when
  # the test wait is bad, 4 when its text could not be read in (a here-document bash could not make)
  local w t="" pre q="'"
  case "$GH$SELF" in *"'"* | *$'\n'*) return 1 ;; esac
  [ "${GH:0:1}" = / ] && [ "${SELF:0:1}" = / ] || return 2
  w="$(hook_wait)" || return 3
  IFS= read -r -d '' t << 'EOF' || :
# autodl-gpu guard autostart (written by install-autostart of autodl_guard.sh; its uninstall-autostart removes it)
# AutoDL starts a container with /init/boot/boot.sh as process 1, which sources /etc/profile and so this file; login
# shells source it too. Only there, as process 1, does it start the guard's boot command, detached, and return at
# once: nothing on the data disk is read in process 1 (the detached shell waits up to a minute for the guard script),
# the sourcing shell's variables and options stay as they were, it prints nothing itself and never fails.
if [ "${BASHPID:-0}" = 1 ] && [ "$0" = /init/boot/boot.sh ]; then
    ( /usr/bin/env AUTODL_GUARD_HOME=@GH@ PATH=@PATH@ setsid /bin/bash -p -c 'i=0; until [ -r "$1" ]; do i=$((i + 1)); [ "$i" -le @WAIT@ ] || exit 0; sleep 1; done; exec /bin/bash -p "$1" boot' autodl-guard-boot @SELF@ < /dev/null > /dev/null 2>&1 & ) > /dev/null 2>&1 || :
fi
:
EOF
  [[ "$t" == "$HOOK_MARK"* && "$t" == *@SELF@* ]] || return 4
  # @SELF@ comes after @WAIT@, @PATH@ and @GH@: each value goes in once and is never searched for a placeholder again
  pre="${t%%@SELF@*}" t="${t#*@SELF@}"
  pre="${pre//@WAIT@/"$w"}"
  pre="${pre//@PATH@/"$HOOK_PATH"}"
  pre="${pre//@GH@/"$q$GH$q"}"   # the replacement quoted: bash 5.2 would put the match in place of a & in it
  t="$pre$q$SELF$q$t"
  printf '%s' "${t%$'\n'}"
}
hook_state() {  # the hook now: none; installed, as install-autostart would write it now; stale, ours (by its first
  # line) but not that; foreign, not ours or not readable
  local cur want
  [ -e "$HOOK" ] || { echo none; return 0; }
  if ! jread cur "$HOOK" || [[ "$cur" != "$HOOK_MARK"* ]]; then echo foreign; return 0; fi
  if want="$(hook_text)" && [ "${cur%$'\n'}" = "$want" ]; then echo installed; else echo stale; fi
}
hook_has() {  # hook_has NAME: an executable regular file NAME in a directory of the hook's PATH, as exec finds it
  local d IFS=:
  for d in $HOOK_PATH; do
    [ -f "$d/$1" ] && [ -x "$d/$1" ] && return 0
  done
  return 1
}
hook_needs() {  # what the hook runs is there: this script as a readable file, /usr/bin/env and /bin/bash, and setsid,
  # sleep, and boot's own flock and timeout on the hook's PATH. Prints what is missing and returns 1 when something is
  local miss=""
  [ -f "$SELF" ] && [ -r "$SELF" ] || miss+=" $SELF (not a readable file)"
  [ -f /usr/bin/env ] && [ -x /usr/bin/env ] || miss+=" /usr/bin/env"
  [ -f /bin/bash ] && [ -x /bin/bash ] || miss+=" /bin/bash"
  hook_has setsid || miss+=" setsid"
  hook_has sleep || miss+=" sleep"
  hook_has flock || miss+=" flock"
  hook_has timeout || miss+=" timeout"
  [ -z "$miss" ] || { printf '%s' "${miss# }"; return 1; }
}
boot_kept() {  # what the next start arms with, as status's boot_settings shows it: MODE:IDLEs for each mode boot would
  # arm, with :fallback for a mode whose own kept settings are missing or unusable, which then takes the other mode's
  # times and dry run (boot_defaults), and :dry-run for a dry run; nothing while an arm is unfinished, as boot then
  # arms nothing
  local m o fb out=""
  [ ! -e "$ST/arm_incomplete" ] || return 0
  for m in gpu nogpu; do
    fb=""
    if ! read_boot_settings "$m" quiet; then
      if [ "$m" = gpu ]; then o=nogpu; else o=gpu; fi
      read_boot_settings "$o" quiet || continue
      fb=":fallback"
    fi
    out+=" $m:${A_IDLE}s$fb"
    [ "$A_DRY" = 0 ] || out+=":dry-run"
  done
  printf '%s' "${out# }"
}
cmd_install_autostart() {  # install-autostart: the hook that runs boot at every container start (design 5.8). Again:
  # nothing changes; our own older hook is replaced; a file of that name that is not ours is left alone. Nothing is
  # made or written before the paths are known to fit the hook (main leaves the home to this)
  local want st tmp rc miss v
  [ $# -eq 0 ] || die "install-autostart takes no options"
  want="$(hook_text)"
  rc=$?
  case "$rc" in
    0) ;;
    1) die "install-autostart: the hook names the guard script ($SELF) and AUTODL_GUARD_HOME ($GH) in single quotes, so neither may hold a quote or a newline; nothing was changed" ;;
    2) die "install-autostart: the guard script's path ($SELF) and AUTODL_GUARD_HOME ($GH) must be absolute; nothing was changed" ;;
    3) die "install-autostart: AUTODL_TEST_HOOK_WAIT must be a whole number from 1 to 60; nothing was changed" ;;
    *) die "install-autostart: cannot build the hook's text (bash could not read its here-document); nothing was changed" ;;
  esac
  miss="$(hook_needs)" ||
    die "install-autostart: the hook could not run the guard at container start, missing: $miss; nothing was changed"
  [ -d "$PROFILE_D" ] || die "install-autostart: $PROFILE_D does not exist, so nothing would run the hook at container start; nothing was changed"
  st="$(hook_state)"
  case "$st" in
    installed) echo "autostart already installed ($HOOK)" ;;
    foreign) die "install-autostart: $HOOK exists and was not written by this script; it is left alone" ;;
    *)
      mkdir -p "$ST" "$JOBS" 2> /dev/null || die "install-autostart: cannot create $GH; nothing was changed"
      tmp="$HOOK.tmp.$$"   # not *.sh: /etc/profile never runs it
      if ! { printf '%s\n' "$want" > "$tmp" && nolock chmod 644 "$tmp" && nolock mv -f "$tmp" "$HOOK"; } 2> /dev/null; then
        nolock rm -f "$tmp"
        die "install-autostart: cannot write $HOOK; it was not changed"
      fi
      [ "$(hook_state)" = installed ] || die "install-autostart: $HOOK does not read back as it was written"
      log "AUTOSTART installed $HOOK (it was: $st)"
      echo "autostart installed ($HOOK): at every container start the guard arms with the settings the last arm kept for that mode"
      ;;
  esac
  v="$(boot_kept)"
  if [ -e "$ST/arm_incomplete" ]; then
    echo "note: the last arm was cut short (status: arm_incomplete=1); no start arms until an arm finishes"
  elif [ -z "$v" ]; then
    echo "note: no arm has kept settings yet; each start arms once an arm has kept them (every arm does, for its mode)"
  else
    echo "the next start arms with the settings the last arm kept for its mode: $v"
    case " $v" in
      *:fallback*) echo "(fallback: that mode kept no usable settings, so a start in it takes the other mode's idle, grace, interval and dry run, with its own mode's defaults for thresholds, GPU probes and unreliable signals)" ;;
    esac
  fi
}
cmd_uninstall_autostart() {  # uninstall-autostart: remove our hook; again: nothing changes; not ours: left alone
  local st
  [ $# -eq 0 ] || die "uninstall-autostart takes no options"
  st="$(hook_state)"
  case "$st" in
    none) echo "autostart not installed ($HOOK)"; return 0 ;;
    foreign) die "uninstall-autostart: $HOOK was not written by this script; it is left alone" ;;
  esac
  nolock rm -f "$HOOK" 2> /dev/null
  [ ! -e "$HOOK" ] || die "uninstall-autostart: cannot remove $HOOK"
  if [ -d "$GH" ]; then log "AUTOSTART uninstalled $HOOK (it was: $st)"; fi   # main makes no home for this
  echo "autostart uninstalled ($HOOK)"
}

cmd_run() {  # run NAME [--then-off] [--quiet DUR] [--log PATH] [--req ID] [--cmd-sha256 HEX] (--cmd-stdin | -- CMD...)
  local name="${1:-}" then_off=0 logp="" cmd="" req="" sum="" from_stdin=0 d tmp qs=""
  [ $# -gt 0 ] && shift
  [[ "$name" =~ $JOB_NAME_RE ]] || die "run: job name must start with a letter or digit and use only [A-Za-z0-9._-] (max 64)"
  [ "$name" != guard ] || die "run: the job name 'guard' is reserved (logtail guard shows the guard's own log)"
  while [ $# -gt 0 ]; do
    case "$1" in
      --then-off) then_off=1; shift ;;
      --quiet)   # the job counts as in use for this long from its start while it runs (design 5.3)
        need2 $# "$1"
        qs="$(to_seconds "$2")" || exit 1
        [ "$qs" -ge 1 ] || die "run: --quiet must be at least 1s"
        shift 2
        ;;
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
  refuse_if_gated
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
  refuse_past_deadline
  if [ "$(get armed_by arm)" = boot ]; then   # boot's arm has no env_setup: the job would run without it (plan 5.1)
    unlock
    die "run: this boot was armed at container start by autostart, without env_setup; arm first (it replaces the boot arm)"
  fi
  ensure_daemon   # a job is only started once a daemon watches over it
  if [ -d "$d" ] && job_running "$d"; then unlock; die "run: job '$name' is already running"; fi
  if [ -d "$d" ]; then nolock mv "$d" "$d.prev-$(now)-$$" || { unlock; die "run: cannot archive the previous run of '$name'"; }; fi
  # req.pending comes last: once it exists, every other job file does too
  if ! { nolock mkdir -p "$d" && write_file "$d/cmd" "$cmd" && write_file "$d/env.sh" "$(get env_setup "")" &&
    write_file "$d/logpath" "${logp:-$d/log}" && write_file "$d/start" "$(now)" &&
    write_file "$d/boot" "$BOOT" && write_file "$d/tag" "$name.$(now).$$" &&
    write_file "$d/owner" "$$ $(proc_start $$)" && { [ -z "$qs" ] || write_quiet "$d" $((UP_NOW + qs)) ""; } &&
    write_file "$d/req.pending" "$req"; }; then
    unlock
    die "run: cannot write the job files of '$name'"
  fi
  putx last_active_up "$UP_NOW"   # the start counts as activity: time for the next command before any idle count
  putx last_active_at "$(now)"
  putx active_why "run:$name"
  putx off_when_done "$then_off"   # a new job cancels a pending off-when-done unless --then-off
  if [ "$then_off" = 1 ]; then putx off_when_done_reason "after job $name"; else putx off_when_done_reason ""; fi
  unlock
  log "RUN job=$name then_off=$then_off log=${logp:-$d/log}"
  [ -z "$qs" ] || log "QUIET job=$name until_up=$((UP_NOW + qs)) reason=[]"
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
  local name="${1:-}" token="${2:-}" d logp child pgid="" rc tag up
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
  lock   # the last activity first, then end: tick never sees an ended job with an older last activity
  up="$(up_now)"
  if [ -n "$up" ]; then
    put last_active_up "$up"
    put last_active_at "$(now)"
    put active_why "job-end:$name"
  fi
  write_file "$d/end" "$(now)"
  unlock
  log "JOB END job=$name rc=$rc"
}

# ---- part 5: quiet, keep, off-when-done, off-now, deadline, status, logtail, entry point ----
cmd_quiet() {  # quiet NAME DUR [--reason TEXT]: the job counts as in use for DUR from now while it runs (design 5.3).
  # A new declaration replaces the last one, longer or shorter; refused once the deadline has passed
  local name="${1:-}" dur="" reason="" s d
  [ $# -gt 0 ] && shift
  [[ "$name" =~ $JOB_NAME_RE ]] || die "quiet: bad job name '$name'"
  while [ $# -gt 0 ]; do
    case "$1" in
      --reason) need2 $# "$1"; reason="$2"; shift 2 ;;
      *) [ -z "$dur" ] || die "quiet: unexpected argument '$1'"; dur="$1"; shift ;;
    esac
  done
  [ -n "$dur" ] || die "quiet: a duration is required, e.g. quiet train 2h --reason 'waits for the queue'"
  s="$(to_seconds "$dur")" || exit 1
  [ "$s" -ge 1 ] || die "quiet: must be at least 1s"
  d="$JOBS/$name"
  life_lock
  lock
  require_armed
  refuse_if_pending
  refuse_if_gated
  refuse_past_deadline
  if [ ! -d "$d" ]; then unlock; die "quiet: no job '$name'"; fi
  if ! job_running "$d"; then unlock; die "quiet: job '$name' is not running"; fi
  ensure_daemon
  if ! write_quiet "$d" $((UP_NOW + s)) "$reason"; then
    unlock
    die "quiet: cannot write the quiet period of '$name' (disk full?)"
  fi
  unlock
  life_unlock
  log "QUIET job=$name until_up=$((UP_NOW + s)) reason=[$reason]"
  echo "ok: job $name counts as in use for ${s}s while it runs"
}

cmd_keep() {  # keep DUR [--reason TEXT]: in use for DUR from now; the deadline ends it early (design 5.4)
  local dur="" reason="" s
  while [ $# -gt 0 ]; do
    case "$1" in
      --after-job) die "keep: --after-job was removed in 0.8.0: after a job the idle time counts down anyway; use keep DUR" ;;
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
  refuse_if_gated
  refuse_past_deadline
  ensure_daemon
  putx keep_until_up $((UP_NOW + s))
  putx keep_until_at $(($(now) + s))
  putx off_when_done 0   # keep cancels off-when-done
  putx off_when_done_reason ""
  unlock
  life_unlock
  log "KEEP ${s}s from now (until up=$((UP_NOW + s))) reason=[$reason]"
  echo "ok"
}

cmd_off_when_done() {  # off-when-done [--reason TEXT]
  local reason="" up
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
  refuse_if_gated
  ensure_daemon
  up="$(up_now)"
  if [ -n "$up" ] && [ "$up" -lt "$(keep_end)" ]; then   # a keep that still runs ends now: in use until now
    putx last_active_up "$up"
    putx last_active_at "$(now)"
    putx active_why keep
  fi
  putx off_when_done 1
  putx off_when_done_reason "$reason"
  putx keep_until_up 0   # off-when-done cancels keep
  putx keep_until_at 0
  unlock
  life_unlock
  log "OFF-WHEN-DONE reason=[$reason]"
  echo "ok: will shut down once no registered job runs and nothing is in use for the grace period"
}

offnow_daemon() {  # before an off-now does anything: a 0.8 daemon must run, to retry the shutdown if its attempt fails
  # or this command dies after the commit point; one left pending without a daemon is retried by the daemon started
  # here, at once. Started holding no lock, never next to a live 0.7 daemon
  [ -z "${AUTODL_NO_DAEMON:-}" ] || return 0   # tests only
  daemon_alive && return 0
  if seven_blocks; then die "off-now: $(seven_why): no 0.8 daemon can run next to it to retry a failed shutdown; nothing was changed"; fi
  (start_daemon) > /dev/null 2>&1 ||
    die "off-now: the guard daemon, which retries a failed shutdown, cannot be started (see $LOG); nothing was changed (the console can still shut the instance down)"
}
cmd_off_now() {  # off-now [--force] [--sample SECONDS] [--reason TEXT] (design 5.5). Exit 3 = refused: a registered job
  # runs, or the live sample shows activity; 4 = a shutdown is already pending; 8 = another off-now is being
  # prepared; 2 = the shutdown could not be recorded. Before the commit point no policy state changes: only the
  # preparation, and the shutdown's details, which count only once shutdown_pending is 1
  local force=0 s="${AUTODL_OFFNOW_SAMPLE:-5}" why kind rc cs pass
  while [ $# -gt 0 ]; do
    case "$1" in
      --force) force=1; shift ;;
      --sample) need2 $# "$1"; s="$2"; shift 2 ;;
      --reason) need2 $# "$1"; OFFNOW_REASON="$2"; shift 2 ;;
      *) die "off-now: unknown option '$1'" ;;
    esac
  done
  { [[ "$s" =~ ^[1-9][0-9]?$ ]] && [ "$s" -le "$OFFNOW_SAMPLE_MAX" ]; } ||
    die "off-now: --sample must be whole seconds from 1 to $OFFNOW_SAMPLE_MAX"
  if [ "$force" = 1 ]; then why="off-now forced ($OFFNOW_REASON)" kind=forced; else why="off-now ($OFFNOW_REASON)" kind=now; fi
  offnow_daemon
  OFFNOW=1
  # 1. under the lock: a shutdown already pending, or another off-now being prepared, stops it; otherwise its
  # preparation gates every command that changes anything until it commits or gives up (void once over PREP_MAX old
  # on the uptime; while the uptime cannot be read it stays)
  lock
  if pending_here; then unlock; offnow_pending; fi
  prep_read
  if [ "$PREP_STATE" = valid ]; then unlock; fail "$E_GATED" "off-now: another off-now is being prepared; try again once it has finished"; fi
  cs="$(uptime_cs)"
  if [ -z "$cs" ]; then unlock; die "off-now: cannot read the kernel uptime ($UPTIME_FILE); nothing was changed"; fi
  OFFNOW_TOKEN="$$.$cs.$RANDOM"
  if ! put prep "$OFFNOW_TOKEN $((cs / 100)) $BOOT"; then unlock; die "off-now: cannot record its preparation (disk full?); nothing was changed"; fi
  [ "$PREP_STATE" != void ] || log "OFF-NOW takes over a void preparation [token=$PREP_TOKEN up=$PREP_UP boot=$PREP_BOOT]"
  unlock
  offnow_step prep-written
  # 2. holding no lock (not with --force): no registered job may run, and a live sample must show no activity.
  # 3. under the lock: still this preparation, still no shutdown pending and no job, and the sample ended at most
  # 1 s ago (rates cannot be taken again under the lock: its readings would be milliseconds apart); an older
  # sample is taken once more
  for pass in 1 2; do
    if [ "$force" != 1 ]; then
      running_jobs
      [ -z "$RUNNING" ] || offnow_refuse "jobs=[${RUNNING//$'\n'/ }]" "refused: still in use" "$RUNNING"
      offnow_sample "$s"
      offnow_step sampled
      [ -z "$OS_WHY" ] || offnow_refuse "activity=[$OS_SIGNALS]" "refused: still in use" "${OS_WHY// /$'\n'}"
      if [ -n "$OS_NOREL" ] && [ "$pass" = 1 ]; then
        echo "note: no signal both applies and is reliable here, so only the registered jobs were checked"
      fi
    fi
    lock
    prep_read
    if [ "$PREP_TOKEN" != "$OFFNOW_TOKEN" ]; then
      unlock
      log "OFF-NOW stopped: its preparation was taken over meanwhile reason=[$OFFNOW_REASON]"
      fail "$E_GATED" "off-now: another off-now took over meanwhile; nothing was shut down"
    fi
    if [ "$PREP_STATE" != valid ]; then   # it outlived PREP_MAX: the gate lapsed, so what was checked may have changed
      unlock
      offnow_refuse "prep=[expired]" "refused: its preparation expired (over ${PREP_MAX}s) before the check" "prep:expired"
    fi
    if [ -z "${AUTODL_NO_DAEMON:-}" ] && ! daemon_alive; then   # no commit without a daemon to retry it
      drop_prep "$OFFNOW_TOKEN"
      unlock
      die "off-now: the guard daemon stopped meanwhile, so a failed shutdown would not be retried; nothing was committed (revive, then try again)"
    fi
    if pending_here; then drop_prep "$OFFNOW_TOKEN"; unlock; offnow_pending; fi
    [ "$force" != 1 ] || break
    running_jobs
    if [ -n "$RUNNING" ]; then unlock; offnow_refuse "jobs=[${RUNNING//$'\n'/ }]" "refused: still in use" "$RUNNING"; fi
    cs="$(uptime_cs)"
    if [ -n "$cs" ] && [ -n "$OS_END" ] && [ "$cs" -ge "$OS_END" ] && [ $((cs - OS_END)) -le 100 ]; then break; fi
    unlock
    log "OFF-NOW: the live sample was over 1 s old at the check (pass $pass)"
    [ "$pass" = 1 ] ||
      offnow_refuse "sample=[over 1 s old twice]" "refused: the live sample could not be checked within 1 s of its end" "sample:too-old"
  done
  offnow_step checked
  # the commit point is shutdown_pending=1 (record_shutdown); only after it the keep ends and the preparation goes
  if ! record_shutdown "$why" "$kind"; then
    drop_prep "$OFFNOW_TOKEN"
    unlock
    echo "not shut down: the shutdown could not be committed (disk full?); the policy state is unchanged"
    exit 2
  fi
  echo "shutdown committed ($why)"
  offnow_step committed
  if armed_now; then
    put keep_until_up 0
    put keep_until_at 0
  fi
  offnow_step keep-cleared
  drop_prep "$OFFNOW_TOKEN"
  offnow_step prep-removed
  attempt_shutdown
  rc=$?
  unlock
  if is_dry; then
    echo "dry-run: shutdown not executed ($why)"
  elif [ "$rc" -eq 0 ]; then
    echo "shutdown issued ($why)"
  else
    echo "shutdown command failed rc=$rc ($why); a daemon keeps retrying"
    ensure_daemon
  fi
  exit "$rc"
}

cmd_idle_check() {  # idle-check [--sample SECONDS]: read-only, for a shutdown that does not go through off-now (ctl's
  # off-raw, which sends this script over stdin). Exit 0 when a live sample shows nothing in use; 3, with a line for
  # each reason, when a signal is busy or cannot be read, when the uptime cannot be read, or when no signal both
  # applies and is reliable (nothing can show idleness then). The sample is off-now's: by this boot's arm when there
  # is a usable one, else by the mode seen now and the default thresholds. It takes no lock and writes nothing
  local s="${AUTODL_OFFNOW_SAMPLE:-5}" why
  while [ $# -gt 0 ]; do
    case "$1" in
      --sample) need2 $# "$1"; s="$2"; shift 2 ;;
      *) die "idle-check: unknown option '$1'" ;;
    esac
  done
  { [[ "$s" =~ ^[1-9][0-9]?$ ]] && [ "$s" -le "$OFFNOW_SAMPLE_MAX" ]; } ||
    die "idle-check: --sample must be whole seconds from 1 to $OFFNOW_SAMPLE_MAX"
  boot_init
  offnow_sample "$s"
  why="$OS_WHY"
  [ -n "$OS_END" ] || why="${why:+$why }uptime:unknown"
  if [ -n "$why" ]; then
    printf 'in use or cannot tell (a %ss live sample: %s)\n%s\n' "$s" "$OS_SIGNALS" "${why// /$'\n'}"
    exit 3
  fi
  if [ -n "$OS_NOREL" ]; then
    printf 'in use or cannot tell: no signal both applies and is reliable here, so nothing can show idleness\n'
    exit 3
  fi
  echo "idle: $OS_SIGNALS"
}

cmd_deadline() {  # deadline DUR: the deadline becomes now + DUR; refused once the deadline has passed (design 5.4)
  local s
  [ $# -eq 1 ] || die "deadline: give exactly one duration, e.g. deadline 90m"
  s="$(to_seconds "$1")" || exit 1
  [ "$s" -ge 1 ] || die "deadline: must be at least 1s"
  life_lock
  lock
  require_armed
  refuse_if_pending
  refuse_if_gated
  refuse_past_deadline
  ensure_daemon
  putx deadline_up $((UP_NOW + s))
  putx deadline_at $(($(now) + s))
  unlock
  life_unlock
  log "DEADLINE now+${s}s (up=$((UP_NOW + s)))"
  echo "ok: deadline in ${s}s"
}

cmd_status() {  # key=value lines, read-only; job lines are job.NAME=STATE|START|END|LOG, and a live job with a quiet
  # period also has quiet.NAME=SECONDS_LEFT (unknown if it cannot be read). *_at are unix times for display; *_s
  # and *_in_s are seconds counted on the uptime, empty when they do not apply
  local d n state t up armed=0 dl="" ke="" la="" lim sin="" idle_for="" name v w
  local -a ws
  t="$(now)" up="$(up_now)"
  if armed_now && ! needs_rearm; then armed=1; fi
  echo "version=$VERSION"
  echo "now=$t"
  echo "up=$up"
  echo "mode=$(get mode unknown)"
  echo "mode_now=$(detect_mode)"
  echo "boot=$BOOT"
  echo "schema=$(get schema '')"
  if needs_rearm; then echo "needs_rearm=1"; else echo "needs_rearm=0"; fi
  echo "armed_at=$(get armed_at 0)"
  if armed_now; then echo "armed_this_boot=1"; else echo "armed_this_boot=0"; fi
  if armed_now; then echo "armed_by=$(get armed_by arm)"; else echo "armed_by="; fi
  echo "autostart=$(hook_state)"
  echo "boot_settings=$(boot_kept)"
  v="$(get autostart '')"   # "BOOT RESULT": what boot did, shown for this boot only
  if [ "$BOOT" != unknown ] && [ "${v%% *}" = "$BOOT" ]; then echo "autostart_this_boot=${v#* }"; else echo "autostart_this_boot="; fi
  if [ -e "$ST/arm_incomplete" ]; then echo "arm_incomplete=1"; else echo "arm_incomplete=0"; fi
  echo "idle_s=$(get idle_s '')"
  echo "grace_s=$(get grace_s '')"
  echo "interval_s=$(get interval_s '')"
  echo "gpu_probes=$(get gpu_probes '')"
  echo "thr_gpu=$(get thr_gpu '')"
  v="$(get thr_cpu '')"
  if is_uint "$v"; then echo "thr_cpu=$((v / 10)).$((v % 10))"; else echo "thr_cpu=$v"; fi   # percent of one core
  echo "thr_io=$(get thr_io '')"
  echo "thr_net=$(get thr_net '')"
  echo "unreliable=$(get unreliable '')"
  echo "calib=$(get calib '')"
  echo "calib_coverage=$(get calib_coverage '')"
  if [ "$armed" = 1 ] && no_reliable; then echo "no_reliable_signal=1"; else echo "no_reliable_signal=0"; fi
  read -r -a ws <<< "$(get signals '')"   # the last check's result, STATE:VALUE per signal
  for name in gpu cpu io net; do
    v=""
    for w in "${ws[@]}"; do case "$w" in "$name="*) v="${w#*=}" ;; esac; done
    echo "sig.$name=$v"
  done
  if [ "$armed" = 1 ] && [ -n "$up" ]; then
    dl="$(get deadline_up 0)" ke="$(keep_end)" la="$(get last_active_up 0)"
    protection_scan "$up"
    if [ "$PROT_END" -gt "$la" ]; then la="$PROT_END"; fi   # as tick counts a keep or quiet period that has ended
  fi
  echo "deadline_at=$(get deadline_at 0)"
  if [ -n "$dl" ] && [ "$dl" -gt 0 ]; then
    echo "deadline_in_s=$((dl > up ? dl - up : 0))"
    if [ "$up" -ge "$dl" ]; then echo "past_deadline=1"; else echo "past_deadline=0"; fi
  else
    echo "deadline_in_s="
    echo "past_deadline=0"
  fi
  echo "keep_until_at=$(get keep_until_at 0)"
  if [ -n "$ke" ] && [ "$ke" -gt 0 ]; then echo "keep_in_s=$((ke > up ? ke - up : 0))"; else echo "keep_in_s="; fi
  echo "last_active_at=$(get last_active_at 0)"
  [ -z "$la" ] || idle_for=$((up > la ? up - la : 0))
  echo "idle_for_s=$idle_for"
  # seconds to the shutdown if nothing is in use from now on: empty while the last check, a keep or a quiet period
  # says in use, or when no signal can show idleness
  if [ -n "$la" ] && [ -z "$PROT_LIVE" ] && ! no_reliable && [[ " ${ws[*]} " != *"=busy:"* ]] &&
    [[ " ${ws[*]} " != *"=unknown:"* ]]; then
    if [ "$dl" -gt 0 ] && [ "$up" -ge "$dl" ]; then
      lim="$(get grace_s 120)"
    elif [ "$(get off_when_done 0)" = 1 ] && running_jobs && [ -z "$RUNNING" ]; then
      lim="$(get grace_s 120)"
    else
      lim="$(get idle_s '')"
    fi
    sin=$((lim > idle_for ? lim - idle_for : 0))
  fi
  echo "shutdown_in_s=$sin"
  echo "active_why=$(get active_why '')"
  prep_read   # an off-now being prepared gates the commands that change anything (exit 8)
  if [ "$PREP_STATE" = valid ]; then echo "gated=prep"; else echo "gated="; fi
  echo "off_when_done=$(get off_when_done 0)"
  echo "shutdown_pending=$(get shutdown_pending 0)"
  echo "shutdown_kind=$(get shutdown_kind '')"
  echo "shutdown_attempts=$(get shutdown_attempts 0)"
  echo "dry_run=$(get dry_run 0)"
  echo "dry_run_fired=$(get dry_run_fired '')"
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
    if job_quiet "$d"; then   # a live job's quiet period: seconds left, 0 once it has run out
      if [ "$QUIET_END" = bad ] || [ -z "$up" ]; then v=unknown; else v=$((QUIET_END > up ? QUIET_END - up : 0)); fi
      echo "quiet.$n=$v"
    fi
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

# ---- the read-only sample command (0.7.1): nothing here takes a lock or writes state ----
wait_until() { sleep_until "$1" || die "sample: $UPTIME_FILE can no longer be read"; }   # wait_until CS
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
  if [ "$k" -gt 0 ] && ! have "$TIMEOUT_CMD"; then   # a GPU probe without a time limit could hang the run
    die "sample: GPU samples need timeout (from coreutils); install it, or pass --gpu-samples 0"
  fi
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
  arm --idle DUR [--deadline DUR] [--keep DUR] [--grace DUR] [--interval DUR] [--mode auto|gpu|nogpu]
      [--gpu-probes K] [--thr-gpu PCT] [--thr-cpu PCT] [--thr-io B/s] [--thr-net B/s]
      [--unreliable gpu,cpu,io,net] [--calib ID [--calib-coverage verified|unverified]] [--env-setup STR]
      [--dry-run] [--rearm] [--req ID]
                                              exit 5 = already armed for this boot (not for the same --req)
  revive [--restart]                          start the daemon again; settings unchanged
  boot                                        run at container start by the autostart hook: unless armed already,
                                              arm with the settings the last arm kept for the mode found, then run
                                              the daemon
  install-autostart | uninstall-autostart     add or remove the hook in /etc/profile.d that runs boot at every
                                              container start (AutoDL's start script sources it)
  run NAME [--then-off] [--quiet DUR] [--log PATH] [--req ID] [--cmd-sha256 HEX] (--cmd-stdin | -- CMD...)
                                              the same --req again is not started again; exit 6 =
                                              whether it started cannot be told (see status, logtail)
  quiet NAME DUR [--reason TEXT]              the running job NAME is in use for DUR from now
  keep DUR [--reason TEXT]                    in use for DUR from now (the deadline ends it early)
  off-when-done [--reason TEXT]
  off-now [--force] [--sample SECONDS] [--reason TEXT]
                                              exit 3 = refused: a registered job runs, or a live sample
                                              (5 s by default) shows activity; --force checks neither
  deadline DUR
  sample [--every DUR] [--count N] [--gpu-samples K]
                                              read-only: a baseline row of activity counters, then one per interval
  idle-check [--sample SECONDS]               read-only: exit 0 when a live sample (off-now's) shows nothing in use,
                                              3 when a signal is busy or cannot be read
  status | logtail NAME|guard [LINES] | version | tick | daemon
  exit 4 = refused because a shutdown is pending; 7 = refused because the deadline has passed;
  8 = refused because an off-now is being prepared (void once over 120 s old, counted on the uptime)
It shuts the instance down once nothing is in use for --idle: no GPU, CPU, disk or network activity at or
above the thresholds (GPU 5%; CPU 5% of one core with a GPU, 3% without; disk 5e5 B/s; network 1e4 B/s),
no keep or quiet period running. After the deadline, and after off-when-done once no registered job runs, --grace is enough.
DUR is 90s, 30m, 2h or a plain number of minutes, at most 30 days. flock (util-linux) and timeout
(coreutils) are required.
EOF
}

main() {
  local c="${1:-help}"
  [ $# -gt 0 ] && shift
  case "$c" in   # without flock every lock would silently vanish: refuse before touching anything
    version | _secs | help | -h | --help | sample | idle-check | _hook_text | uninstall-autostart) ;;
    *)
      have "$FLOCK_CMD" || die "flock (from util-linux) is required: install it; nothing was changed"
      have "$TIMEOUT_CMD" || die "timeout (from coreutils) is required: install it; nothing was changed"
      if [ "$c" != install-autostart ]; then   # it makes the home itself, once the paths are known to fit the hook
        mkdir -p "$ST" "$JOBS" || die "cannot create $GH"
        boot_init
      fi
      ;;
  esac
  case "$c" in
    arm) cmd_arm "$@" ;;
    revive) cmd_revive "$@" ;;
    boot) cmd_boot "$@" ;;
    install-autostart) cmd_install_autostart "$@" ;;
    uninstall-autostart) cmd_uninstall_autostart "$@" ;;
    _hook_text) hook_text || die "the hook cannot be written for these paths" ;;   # tests only
    run) cmd_run "$@" ;;
    _job) cmd_job "$@" ;;
    quiet) cmd_quiet "$@" ;;
    keep) cmd_keep "$@" ;;
    off-when-done) cmd_off_when_done "$@" ;;
    off-now) cmd_off_now "$@" ;;
    deadline) cmd_deadline "$@" ;;
    status) cmd_status ;;
    logtail) cmd_logtail "$@" ;;
    tick) check_now; exit $? ;;
    daemon) daemon_loop ;;
    sample) cmd_sample "$@" ;;
    idle-check) cmd_idle_check "$@" ;;
    version) echo "$VERSION" ;;
    _secs) to_seconds "${1:-}" ;;
    help | -h | --help) usage ;;
    *) die "unknown command '$c' (see: $0 help)" ;;
  esac
}

main "$@"
