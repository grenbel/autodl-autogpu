#!/usr/bin/env bash
# Tests for scripts/autodl_guard.sh. Run in Linux bash, optionally naming tests:
#   bash tests/test_guard.sh [TEST...]
# On Windows run it in WSL, from the skill's directory:
#   wsl.exe -e bash tests/test_guard.sh [TEST...]
# screen, tmux, nvidia-smi, the cgroup memory limit and shutdown are stubbed; nothing is powered off.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
GUARD="$HERE/../scripts/autodl_guard.sh"
STUBS="$HERE/stubs"
PASS=0
FAIL=0

g() { bash "$GUARD" "$@"; }
ok() { PASS=$((PASS + 1)); printf 'PASS %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL %s\n' "$1"; }
expect() { local name="$1"; shift; if "$@"; then ok "$name"; else bad "$name"; fi; }
fails() { ! "$@" > /dev/null 2>&1; }
quiet() { "$@" > /dev/null 2>&1; }
fired() { [ -f "$AUTODL_GUARD_HOME/state2/dry_run_fired" ]; }
not_fired() { ! fired; }
reason_has() { grep -q -- "$1" "$AUTODL_GUARD_HOME/state2/dry_run_fired" 2> /dev/null; }
status_has() { g status | grep -q -- "$1"; }
tick_rc() { g tick > /dev/null 2>&1; echo $?; }
rc_of() { "$@" > /dev/null 2>&1; echo $?; }
state() { cat "$AUTODL_GUARD_HOME/state2/$1" 2> /dev/null; }
wait_file() {  # wait_file PATH: wait (max 10s) until the file exists
  local i
  for i in $(seq 1 20); do
    [ -f "$1" ] && return 0
    sleep 0.5
  done
  return 1
}
wait_job() { wait_file "$AUTODL_GUARD_HOME/jobs/$1/end"; }

setup() {
  T="$(mktemp -d)"
  # a stub path exported by an earlier test would point into its deleted directory
  unset AUTODL_TEST_UPTIME AUTODL_TEST_PROC AUTODL_TEST_OFFNOW_BARRIER AUTODL_TEST_OFFNOW_DIE_AT
  unset AUTODL_TEST_BOOT_BARRIER AUTODL_TEST_HOOK_WAIT AUTODL_BOOT_MODE_BUDGET AUTODL_BOOT_GATE_WAIT
  export AUTODL_OFFNOW_SAMPLE=1   # off-now samples 1 s instead of 5
  export AUTODL_BOOT_MODE_WAIT=1   # boot tries again after 1 s instead of 5 when it cannot tell the mode
  export AUTODL_TEST_PROFILE_D="$T/profile.d"   # autostart hooks go here, never into /etc/profile.d
  mkdir -p "$AUTODL_TEST_PROFILE_D"
  export AUTODL_GUARD_HOME="$T/guard"
  export STUB_DIR="$T/stub"
  mkdir -p "$STUB_DIR"
  : > "$STUB_DIR/screen_out"
  : > "$STUB_DIR/tmux_out"
  echo 0 > "$STUB_DIR/gpu"
  echo 0 > "$STUB_DIR/util"
  echo 2147483648 > "$STUB_DIR/cgroup_mem"   # AutoDL non-GPU mode: 2 GiB
  # the activity counters of an idle container (0.8 judges idleness by them); busy_cpu and friends add to them
  mkdir -p "$STUB_DIR/cg"
  echo 1000000 > "$STUB_DIR/c_cpu"
  echo 0 > "$STUB_DIR/c_io"
  echo 5000 > "$STUB_DIR/c_net"
  write_counters
  export AUTODL_TEST_CGROUP_DIR="$STUB_DIR/cg" AUTODL_TEST_NET_DEV="$STUB_DIR/net_dev"
  export AUTODL_SHUTDOWN_CMD="$STUBS/shutdown"
  export AUTODL_SYNC_CMD="$STUBS/sync"
  export AUTODL_SCREEN_CMD="$STUBS/screen"
  export AUTODL_TMUX_CMD="$STUBS/tmux"
  export AUTODL_NVIDIA_SMI_CMD="$STUBS/nvidia-smi"
  export AUTODL_CGROUP_MEM_FILE="$STUB_DIR/cgroup_mem"
  export AUTODL_LAUNCHER=direct
  export AUTODL_LAUNCH_WAIT=5
  export AUTODL_BOOT_MARKER=boot1
  export AUTODL_NO_DAEMON=1
}
teardown() {
  local p i
  p="$(state daemon_pid)"
  [ -n "$p" ] && kill "$p" 2> /dev/null
  # a stub still finishing in the background (a sync that was held up writes sync_done) can put a file into $T
  # while it is being removed: try again for a few seconds before saying so
  for i in 1 2 3 4 5 6; do
    rm -rf "$T" 2> /dev/null && return 0
    sleep 0.5
  done
  rm -rf "$T"
}
write_counters() {  # the stub cpu.stat, io.stat and /proc/net/dev from the totals in $STUB_DIR/c_cpu, c_io, c_net;
  # each file is replaced whole, so a daemon never reads one half written
  local c i n
  read -r c < "$STUB_DIR/c_cpu"
  read -r i < "$STUB_DIR/c_io"
  read -r n < "$STUB_DIR/c_net"
  printf 'usage_usec %s\nuser_usec %s\nsystem_usec 0\n' "$c" "$c" > "$STUB_DIR/cg/.cpu.tmp"
  mv -f "$STUB_DIR/cg/.cpu.tmp" "$STUB_DIR/cg/cpu.stat"
  printf '9:0 rbytes=0 wbytes=%s rios=0 wios=0 dbytes=0 dios=0\n' "$i" > "$STUB_DIR/cg/.io.tmp"
  mv -f "$STUB_DIR/cg/.io.tmp" "$STUB_DIR/cg/io.stat"
  {
    printf 'Inter-|   Receive                                                |  Transmit\n'
    printf ' face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n'
    printf '    lo:    1000      10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0\n'
    printf '  eth0: %s    100    0    0    0     0          0         0      300       3    0    0    0     0       0          0\n' "$n"
  } > "$STUB_DIR/.net.tmp"
  mv -f "$STUB_DIR/.net.tmp" "$STUB_DIR/net_dev"
}
bump() {  # bump NAME N: add N to one stub counter total
  local v
  read -r v < "$STUB_DIR/$1"
  echo $((v + $2)) > "$STUB_DIR/$1"
  write_counters
}
busy_cpu() { bump c_cpu "$1"; }   # busy_cpu USEC: the container used this much more CPU time
busy_io() { bump c_io "$1"; }     # busy_io BYTES: this much more written to disk
busy_net() { bump c_net "$1"; }   # busy_net BYTES: this much more received
gpu_util() { echo "$1" > "$STUB_DIR/util"; }
use_clock() {  # use_clock [UP]: /proc/uptime becomes a file that only adv moves on (default 1000 s)
  export AUTODL_TEST_UPTIME="$STUB_DIR/uptime"
  printf '%s.00 0.00\n' "${1:-1000}" > "$AUTODL_TEST_UPTIME"
}
up_s() { local u; read -r u _ < "$AUTODL_TEST_UPTIME"; printf '%s' "${u%.*}"; }
adv() { printf '%s.00 0.00\n' $(($(up_s) + $1)) > "$AUTODL_TEST_UPTIME"; }   # adv N: N seconds later
tick_for() {  # tick_for SECONDS [STEP]: every STEP seconds (default 60) move the clock on and tick, until SECONDS
  # have passed or a tick fires; prints the last tick's exit status. One jump past the baseline's age limit would
  # make every signal unknown (and so in use), so tests let time pass in steps
  local left="$1" step="${2:-60}" rc=0
  while [ "$left" -gt 0 ]; do
    [ "$left" -lt "$step" ] && step="$left"
    adv "$step"
    left=$((left - step))
    g tick > /dev/null 2>&1
    rc=$?
    [ "$rc" = 10 ] && break
  done
  echo "$rc"
}
cut_short() {  # a run cut short (Ctrl-C, or TERM from an outer timeout) still stops the test's daemon and removes
  # its files: a daemon a test left past its deadline would otherwise retry the shutdown every second for good
  if [ -n "${T:-}" ] && [ -d "$T" ]; then teardown; fi
  exit "$1"
}
trap 'cut_short 130' INT
trap 'cut_short 143' TERM

# ---- group 1: durations, arm, keep, grace, jobs, off-when-done ----
t_durations() {
  setup
  expect "duration 90s" [ "$(g _secs 90s)" = 90 ]
  expect "duration 2m" [ "$(g _secs 2m)" = 120 ]
  expect "duration 1h" [ "$(g _secs 1h)" = 3600 ]
  expect "plain number means minutes" [ "$(g _secs 5)" = 300 ]
  expect "leading zero is decimal" [ "$(g _secs 08m)" = 480 ]
  expect "junk duration rejected" fails g _secs abc
  expect "bad duration in deadline rejected" fails g deadline soon
  teardown
}

# 0.8: --idle is required and --deadline, --keep are optional (design 5.1); a refused arm writes no schema
t_arm_requires_values() {
  setup
  expect "arm needs --idle" fails g arm --deadline 1h --keep 5m
  expect "option without value rejected" fails g arm --idle
  expect "bad duration never arms" fails g arm --idle soon
  expect "zero deadline rejected" fails g arm --idle 5m --deadline 0s
  expect "failed arm left no schema" [ ! -f "$AUTODL_GUARD_HOME/state2/schema" ]
  g arm --idle 5m --dry-run > /dev/null
  expect "non-GPU mode detected" status_has '^mode=nogpu$'
  expect "dry_run recorded" status_has '^dry_run=1$'
  expect "grace default written" [ "$(state grace_s)" = 120 ]
  expect "interval default written" [ "$(state interval_s)" = 60 ]
  teardown
}

# 0.8: keep counts as in use until it ends, then the idle time counts from its end (design 5.2); test clock
t_keep_expiry_shuts_down() {
  setup
  use_clock
  g arm --idle 2m --keep 3m --dry-run > /dev/null
  expect "idle inside keep: no shutdown" [ "$(tick_for 180)" = 0 ]
  expect "not fired yet" not_fired
  expect "no shutdown until --idle after the keep" [ "$(tick_for 119)" = 0 ]
  expect "idle for --idle after the keep: fires" [ "$(tick_for 1)" = 10 ]
  expect "reason says idle" reason_has "idle"
  expect "dry run never calls shutdown" [ ! -f "$STUB_DIR/shutdown_calls" ]
  teardown
}

# 0.8: grace applies only past the deadline and after off-when-done (design 5.4, 5.5); otherwise --idle does
t_grace_period() {
  setup
  use_clock
  g arm --idle 60m --deadline 1m --grace 3m --dry-run > /dev/null
  expect "past the deadline, inside grace: no shutdown" [ "$(tick_for 179)" = 0 ]
  expect "after grace: fires" [ "$(tick_for 1)" = 10 ]
  teardown
  setup
  use_clock
  g arm --idle 5m --grace 1m --dry-run > /dev/null
  expect "without a deadline grace alone does not shut down" [ "$(tick_for 240)" = 0 ]
  teardown
}

# 0.8: a registered job alone is no longer in use (design 5.2, t8_a_registered_job_alone_is_not_in_use), so this
# checks what status says about a job while it runs and after
t_a_running_job_shows_in_status() {
  setup
  g arm --idle 60m --dry-run > /dev/null
  g run j1 -- sleep 2 > /dev/null
  expect "status shows the job running" status_has '^job.j1=running|'
  wait_job j1
  expect "and done once it ended" status_has '^job.j1=done:0|'
  teardown
}

t_off_when_done_waits_for_job() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --grace 1s --dry-run > /dev/null
  g run j2 -- sleep 3 > /dev/null
  g off-when-done --reason "tests done" > /dev/null
  expect "off-when-done while busy: no shutdown" [ "$(tick_rc)" = 0 ]
  wait_job j2
  sleep 2
  expect "after the job: fires despite long keep" [ "$(tick_rc)" = 10 ]
  expect "reason mentions off-when-done" reason_has "off-when-done"
  teardown
}

t_cancel_off_when_done() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  g off-when-done --reason x > /dev/null
  expect "flag set" status_has '^off_when_done=1$'
  g run j3 -- true > /dev/null
  expect "plain run cancels it" status_has '^off_when_done=0$'
  g run j3b --then-off -- true > /dev/null
  expect "run --then-off sets it" status_has '^off_when_done=1$'
  g keep 5m --reason "next run soon" > /dev/null
  expect "keep cancels it" status_has '^off_when_done=0$'
  wait_job j3
  wait_job j3b
  teardown
}

# ---- group 2: deadline, sessions, GPU utilization, off-now, keep after job ----
# 0.8: the deadline no longer cuts work (design 5.4), but a job that shows no activity is not work: past the
# deadline it is shut down once nothing was in use for grace; keep ends at the deadline
t_deadline_shuts_down_a_job_without_activity() {
  setup
  use_clock
  g arm --idle 60m --deadline 2m --keep 30m --grace 1m --dry-run > /dev/null
  g run j4 -- sleep 600 > /dev/null
  expect "keep holds until the deadline" [ "$(tick_for 120)" = 0 ]
  expect "a quiet job past the deadline: fires after grace" [ "$(tick_for 60)" = 10 ]
  expect "reason is deadline" reason_has "deadline"
  pkill -f "sleep 600" 2> /dev/null
  teardown
}

# 0.8: screen and tmux sessions no longer count as in use (design 5.2), whatever their lists say or however
# reading them fails; the idle shutdown goes ahead (this also covers the removed t_screen_errors_count_as_in_use
# and t_a_broken_session_parser_counts_as_in_use)
t_sessions() {
  setup
  use_clock
  g arm --idle 2m --dry-run > /dev/null
  printf 'There are screens on:\n\t12345.train\t(Detached)\n\t222.aj-x\t(Detached)\n\tweird line\n2 Sockets in /run/screen/S-root.\n' > "$STUB_DIR/screen_out"
  echo 'exp: 1 windows (created Sat Sep 27 20:00:00 2026)' > "$STUB_DIR/tmux_out"
  expect "sessions of any kind do not keep it on" [ "$(tick_for 120)" = 10 ]
  expect "status says nothing about sessions" fails status_has 'screen\|tmux'
  teardown
  setup
  use_clock
  g arm --idle 2m --dry-run > /dev/null
  touch "$STUB_DIR/screen_fail"
  expect "a failing screen -ls does not keep it on either" [ "$(tick_for 120)" = 10 ]
  teardown
}

# 0.8: in GPU mode the GPU is always a signal (no --util-signal, design 5.2); a reading that is not a number is
# unknown and so in use; test clock
t_gpu_utilization_is_a_signal() {
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  echo 37 > "$STUB_DIR/util"
  expect "--util-signal is refused" fails g arm --idle 2m --util-signal --dry-run
  g arm --idle 2m --dry-run > /dev/null
  expect "GPU mode detected" status_has '^mode=gpu$'
  expect "utilization keeps it on" [ "$(tick_for 180)" = 0 ]
  echo 0 > "$STUB_DIR/util"
  expect "fires once the GPU was idle for --idle" [ "$(tick_for 120)" = 10 ]
  teardown
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  echo '[N/A]' > "$STUB_DIR/util"
  g arm --idle 2m --dry-run > /dev/null
  expect "unreadable utilization counts as in use" [ "$(tick_for 180)" = 0 ]
  expect "status says gpu:unknown" status_has '^active_why=.*gpu:unknown'
  teardown
}

t_off_now() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  g run j5 -- sleep 3 > /dev/null
  sleep 1
  g off-now --reason "try" > "$T/out" 2>&1
  expect "off-now refused while busy (exit 3)" [ "$?" = 3 ]
  expect "refusal names the job" grep -q 'job:j5' "$T/out"
  expect "refusal does not fire" not_fired
  g off-now --force --reason "runaway" > "$T/out" 2>&1
  expect "forced off-now succeeds" [ "$?" = 0 ]
  expect "forced reason recorded" reason_has "forced"
  expect "dry-run message printed" grep -q 'dry-run' "$T/out"
  wait_job j5
  teardown
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  expect "off-now when idle succeeds" quiet g off-now --reason "stage done"
  expect "idle off-now fired" fired
  teardown
}

# 0.8: keep --after-job is gone (after a job the idle time counts down anyway; plan Task 3.3)
t_keep_after_job_is_refused() {
  setup
  use_clock
  g arm --idle 60m --dry-run > /dev/null
  expect "keep --after-job refused" fails g keep --after-job 4s --reason "pull results"
  expect "and nothing kept" [ "$(state keep_until_up)" = 0 ]
  expect "keep DUR is the way now" quiet g keep 4m --reason "pull results"
  expect "until 4 minutes from now" [ "$(state keep_until_up)" = 1240 ]
  teardown
}

# ---- group 3: stale boot, archive, env and stdin, real shutdown, daemon, status ----
# 0.8: time must pass on the test clock before a check can find the instance idle
t_stale_boot_job_ignored() {
  setup
  use_clock
  g arm --idle 2m --dry-run > /dev/null
  mkdir -p "$AUTODL_GUARD_HOME/jobs/old"
  printf '%s' "$$" > "$AUTODL_GUARD_HOME/jobs/old/pid"
  printf '%s' "$(date +%s)" > "$AUTODL_GUARD_HOME/jobs/old/start"
  printf 'boot0' > "$AUTODL_GUARD_HOME/jobs/old/boot"
  expect "job from a previous boot shows as lost" status_has '^job.old=lost|'
  expect "and does not keep the instance on" [ "$(tick_for 120)" = 10 ]
  teardown
}

t_run_archives_previous() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  g run j7 -- 'echo first' > /dev/null
  wait_job j7
  g run j7 -- 'echo second' > /dev/null
  wait_job j7
  expect "previous run archived, not deleted" bash -c "ls -d '$AUTODL_GUARD_HOME'/jobs/j7.prev-* > /dev/null 2>&1"
  expect "archived log kept" bash -c "grep -q first '$AUTODL_GUARD_HOME'/jobs/j7.prev-*/log"
  expect "new log has the new output" grep -q second "$AUTODL_GUARD_HOME/jobs/j7/log"
  teardown
}

t_job_env_and_stdin() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --env-setup 'export FOO=bar' --dry-run > /dev/null
  g run j8 -- 'echo "val=$FOO"' > /dev/null
  wait_job j8
  expect "env_setup applied" grep -q 'val=bar' "$AUTODL_GUARD_HOME/jobs/j8/log"
  expect "exit code 0 recorded" [ "$(cat "$AUTODL_GUARD_HOME/jobs/j8/rc")" = 0 ]
  printf 'echo from-stdin; exit 4' | g run j9 --cmd-stdin > /dev/null
  wait_job j9
  expect "stdin command ran" grep -q from-stdin "$AUTODL_GUARD_HOME/jobs/j9/log"
  expect "non-zero exit recorded" [ "$(cat "$AUTODL_GUARD_HOME/jobs/j9/rc")" = 4 ]
  teardown
  setup
  g arm --idle 60m --deadline 60s --keep 60s --env-setup 'false' --dry-run > /dev/null
  g run j10 -- 'echo "RAN$((1 + 1))"' > /dev/null
  wait_job j10
  expect "failed env_setup stops the job (rc 97)" [ "$(cat "$AUTODL_GUARD_HOME/jobs/j10/rc")" = 97 ]
  expect "job command did not run" fails grep -q RAN2 "$AUTODL_GUARD_HOME/jobs/j10/log"
  teardown
}

# 0.8: needs --idle and time on the test clock
t_real_shutdown_path() {
  setup
  use_clock
  g arm --idle 1m > /dev/null
  expect "tick fires without dry-run" [ "$(tick_for 60)" = 10 ]
  expect "shutdown command was called" [ -f "$STUB_DIR/shutdown_calls" ]
  teardown
}

# 0.8: the daemon checks an interval after it starts; --idle 2s takes the place of the keep and the grace
t_daemon_end_to_end() {
  setup
  unset AUTODL_NO_DAEMON
  g arm --idle 2s --interval 1s --dry-run > /dev/null
  expect "daemon alive after arm" status_has '^daemon_alive=1$'
  local i hb
  for i in $(seq 1 20); do fired && break; sleep 0.5; done
  expect "daemon fired on its own" fired
  sleep 1.5
  expect "daemon exits after a dry-run shutdown" status_has '^daemon_alive=0$'
  hb="$(g status | sed -n 's/^heartbeat=//p')"
  expect "heartbeat written" [ "${hb:-0}" -gt 0 ]
  export AUTODL_NO_DAEMON=1
  teardown
}

# 0.8: deadline, keep_until and busy_now became deadline_at, keep_until_at and active_why (plan Task 3.6
# tests every key)
t_status_keys() {
  setup
  g arm --idle 5m --deadline 1h --keep 10m --dry-run > /dev/null
  local k
  for k in version now mode schema needs_rearm deadline_at keep_until_at off_when_done heartbeat daemon_alive \
    daemon_version active_why shutdown_pending; do
    expect "status has $k" status_has "^$k="
  done
  teardown
}

# ---- group 4: fixes from the first Codex review ----
t_job_name_rules() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local n
  for n in .train . .. -x 'a b' 'a/b' ''; do
    expect "job name '$n' rejected" fails g run "$n" -- true
  done
  expect "job name a.b-c_1 accepted" quiet g run a.b-c_1 -- true
  wait_job a.b-c_1
  teardown
}

t_pending_blocks_commands() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  g off-now --reason "stage done" > /dev/null
  expect "shutdown pending recorded" [ "$(state shutdown_pending)" = 1 ]
  expect "run refused while shutdown pending (exit 4)" [ "$(rc_of g run late -- true)" = 4 ]
  expect "keep refused while shutdown pending" [ "$(rc_of g keep 5m --reason x)" = 4 ]
  expect "deadline refused while shutdown pending" [ "$(rc_of g deadline 5m)" = 4 ]
  expect "off-when-done refused while shutdown pending" [ "$(rc_of g off-when-done --reason x)" = 4 ]
  expect "second arm in the same boot refused (exit 5)" [ "$(rc_of g arm --idle 60m --deadline 60s --keep 60s --dry-run)" = 5 ]
  expect "arm --rearm accepted" quiet g arm --idle 60m --deadline 60s --keep 60s --dry-run --rearm
  expect "rearm clears the pending shutdown" [ "$(state shutdown_pending)" = 0 ]
  expect "run works again" quiet g run later -- true
  wait_job later
  export AUTODL_BOOT_MARKER=boot2
  expect "arm after a reboot needs no --rearm" quiet g arm --idle 60m --deadline 60s --keep 60s --dry-run
  export AUTODL_BOOT_MARKER=boot1
  teardown
}

# 0.8: time must pass on the test clock between checks; off-now ends the keep, so its retry does not wait for it
t_shutdown_retry() {
  setup
  use_clock
  g arm --idle 1m > /dev/null
  touch "$STUB_DIR/shutdown_fail"
  expect "first shutdown attempt fires" [ "$(tick_for 60)" = 10 ]
  expect "failed attempt leaves shutdown pending" [ "$(state shutdown_pending)" = 1 ]
  expect "next tick retries" [ "$(tick_for 60)" = 10 ]
  expect "shutdown was called twice" [ "$(wc -l < "$STUB_DIR/shutdown_calls")" = 2 ]
  teardown
  setup
  use_clock
  g arm --idle 60m --keep 60m > /dev/null
  touch "$STUB_DIR/shutdown_fail"
  expect "off-now reports the failed shutdown" [ "$(rc_of g off-now --reason x)" != 0 ]
  expect "tick retries although the keep had not run out" [ "$(tick_for 60)" = 10 ]
  expect "retry reached the shutdown command" [ "$(wc -l < "$STUB_DIR/shutdown_calls")" = 2 ]
  teardown
}

# ---- group 5: process tracking, arming, robustness ----
# 0.8: a job no longer keeps the instance on by itself, so this checks the job's status (running while its group
# lives without the runner, lost once the group is gone) and that off-now refuses meanwhile
t_orphan_group_detected() {
  setup
  g arm --idle 60m --dry-run > /dev/null
  g run orphan -- sleep 8 > /dev/null
  wait_file "$AUTODL_GUARD_HOME/jobs/orphan/pgid"
  local runner pg
  runner="$(cat "$AUTODL_GUARD_HOME/jobs/orphan/pid")"
  pg="$(cat "$AUTODL_GUARD_HOME/jobs/orphan/pgid")"
  kill -9 "$runner"
  sleep 0.5
  expect "job still runs while its process group lives" status_has '^job.orphan=running|'
  expect "and off-now refuses" [ "$(rc_of g off-now --reason x)" = 3 ]
  kill -9 -- "-$pg" 2> /dev/null
  sleep 0.5
  expect "job shows as lost once the whole group is gone" status_has '^job.orphan=lost|'
  teardown
}

# 0.8: checked through status, since a job no longer keeps the instance on by itself
t_pid_reuse_ignored() {
  setup
  g arm --idle 60m --dry-run > /dev/null
  mkdir -p "$AUTODL_GUARD_HOME/jobs/reused"
  printf '%s' "$$" > "$AUTODL_GUARD_HOME/jobs/reused/pid"
  printf '1' > "$AUTODL_GUARD_HOME/jobs/reused/pstart"
  printf '%s' "$(date +%s)" > "$AUTODL_GUARD_HOME/jobs/reused/start"
  printf 'boot1' > "$AUTODL_GUARD_HOME/jobs/reused/boot"
  expect "a live pid with a different start time is not the job" status_has '^job.reused=lost|'
  teardown
}

t_launch_failure_reported() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  export AUTODL_LAUNCHER=none AUTODL_LAUNCH_WAIT=1
  expect "run reports a launch that never started" fails g run ghost -- true
  expect "job marked launch-failed" status_has '^job.ghost=done:launch-failed|'
  export AUTODL_LAUNCHER=direct AUTODL_LAUNCH_WAIT=5
  teardown
}

t_requires_arm() {
  setup
  expect "run before arm refused" fails g run early -- true
  expect "keep before arm refused" fails g keep 5m --reason x
  expect "off-when-done before arm refused" fails g off-when-done --reason x
  expect "deadline before arm refused" fails g deadline 5m
  teardown
}

# 0.8: --idle is required, and the state files are deadline_up and keep_until_up now
t_revive_and_autostart() {
  setup
  unset AUTODL_NO_DAEMON
  g arm --idle 60m --deadline 10m --keep 10m --interval 1s --dry-run > /dev/null
  expect "daemon alive after arm" status_has '^daemon_alive=1$'
  expect "daemon records its version" [ "$(g status | sed -n 's/^daemon_version=//p')" = "$(g version)" ]
  local p1 p2 dl
  p1="$(state daemon_pid)"
  kill "$p1"
  sleep 1.5
  expect "daemon gone after kill" status_has '^daemon_alive=0$'
  g keep 5m --reason "still working" > /dev/null
  expect "keep restarts a dead daemon" status_has '^daemon_alive=1$'
  p2="$(state daemon_pid)"
  expect "it is a new daemon process" [ "$p2" != "$p1" ]
  dl="$(state deadline_up)"
  g revive --restart > /dev/null
  expect "revive --restart replaces the daemon" [ "$(state daemon_pid)" != "$p2" ]
  expect "revive leaves the deadline alone" [ "$(state deadline_up)" = "$dl" ]
  expect "revived daemon is alive" status_has '^daemon_alive=1$'
  export AUTODL_NO_DAEMON=1
  teardown
}

# 0.8: a damaged state makes no new decision at all, the deadline included (design 5.7, 5.9 row 1); status says
# needs_rearm=1, the log says what is wrong, and a value that makes sense again brings the decisions back
t_corrupt_state_no_idle_shutdown() {
  setup
  use_clock
  g arm --idle 1m --deadline 2m --dry-run > /dev/null
  printf 'abc' > "$AUTODL_GUARD_HOME/state2/last_active_up"
  expect "garbage last_active_up: no shutdown, even past the deadline" [ "$(tick_for 300)" = 0 ]
  expect "status says needs_rearm=1" status_has '^needs_rearm=1$'
  expect "corruption is logged" grep -q 'STATE CORRUPT last_active_up=\[abc\]' "$AUTODL_GUARD_HOME/guard.log"
  g arm --idle 1m --rearm --dry-run > /dev/null
  printf 'soon' > "$AUTODL_GUARD_HOME/state2/keep_until_up"
  expect "garbage keep_until_up: no shutdown" [ "$(tick_for 120)" = 0 ]
  printf '0' > "$AUTODL_GUARD_HOME/state2/keep_until_up"
  expect "the first check after that finds the baseline too old: in use" [ "$(tick_for 60)" = 0 ]
  expect "then idleness counts again" [ "$(tick_for 60)" = 10 ]
  teardown
}

t_interval_bounds() {
  setup
  expect "interval 0s rejected" fails g arm --idle 60m --deadline 60s --keep 60s --interval 0s --dry-run
  expect "interval above 1h rejected" fails g arm --idle 60m --deadline 60s --keep 60s --interval 2h --dry-run
  expect "grace above 1h rejected" fails g arm --idle 60m --deadline 60s --keep 60s --grace 2h --dry-run
  teardown
}

t_mode_tristate() {
  setup
  echo max > "$STUB_DIR/cgroup_mem"
  expect "no GPU listed and no 2 GiB limit: arm refuses (mode unknown)" fails g arm --idle 60m --deadline 60s --keep 60s --dry-run
  expect "explicit --mode still works" quiet g arm --idle 60m --deadline 60s --keep 60s --mode nogpu --dry-run
  teardown
  setup
  echo 128849018880 > "$STUB_DIR/cgroup_mem"
  expect "GPU-sized memory limit but no GPU listed: arm refuses" fails g arm --idle 60m --deadline 60s --keep 60s --dry-run
  teardown
}

# 0.8: keep --after-job is gone; the end of a job counts as activity instead, so a short job that no check saw
# still restarts the idle time at its end (plan Task 3.3); test clock
t_short_job_between_ticks() {
  setup
  use_clock
  g arm --idle 2m --dry-run > /dev/null
  quiet tick_for 60
  g run quick -- sleep 0.3 > /dev/null
  wait_job quick
  expect "the job's end is the last activity" [ "$(state last_active_up)" = 1060 ]
  expect "no shutdown until --idle after it" [ "$(tick_for 119)" = 0 ]
  expect "then it fires" [ "$(tick_for 1)" = 10 ]
  teardown
}

# ---- group 6: fixes from the second Codex review ----
# 0.8: a job no longer keeps the instance on by itself; still checked: a background child keeps the job running,
# its end is written only after the child exits, and that end counts as the last activity
t_background_child_keeps_job() {
  setup
  g arm --idle 60m --dry-run > /dev/null
  g run bg -- 'sleep 4 & echo started' > /dev/null
  sleep 1
  expect "a background child keeps the job running" status_has '^job.bg=running|'
  wait_job bg
  local st en
  st="$(cat "$AUTODL_GUARD_HOME/jobs/bg/start")"
  en="$(cat "$AUTODL_GUARD_HOME/jobs/bg/end")"
  expect "end is written only after the child exits" [ $((en - st)) -ge 3 ]
  expect "the last activity is the job end (both read the clock just before end is written)" \
    [ "$(state last_active_at)" -ge $((en - 1)) ]
  teardown
}

# 0.8: sessions no longer count, so activity cancels a pending idle shutdown instead; and a deadline shutdown is
# re-checked like any other that is not forced (design 5.5, 5.9 row 2); test clock
t_pending_retry_rechecks() {
  setup
  use_clock
  g arm --idle 1m > /dev/null
  touch "$STUB_DIR/shutdown_fail"
  expect "idle shutdown attempt fails" [ "$(tick_for 60)" = 10 ]
  busy_net 6000000
  expect "network activity cancels the pending idle shutdown" [ "$(tick_for 60)" = 0 ]
  expect "pending cleared" [ "$(state shutdown_pending)" = 0 ]
  expect "no second shutdown call" [ "$(wc -l < "$STUB_DIR/shutdown_calls")" = 1 ]
  teardown
  setup
  use_clock
  g arm --idle 60m --deadline 1m --grace 1m > /dev/null
  touch "$STUB_DIR/shutdown_fail_always"
  expect "deadline shutdown attempt fails" [ "$(tick_for 60)" = 10 ]
  expect "as a deadline shutdown" [ "$(state shutdown_kind)" = deadline ]
  expect "an idle retry goes ahead" [ "$(tick_for 60)" = 10 ]
  expect "reason keeps its original text" \
    [ "$(state shutdown_reason)" = "the deadline has passed and nothing was in use for 60s" ]
  expect "attempts are counted" [ "$(state shutdown_attempts)" = 2 ]
  busy_cpu 60000000
  expect "activity cancels a pending deadline shutdown too" [ "$(tick_for 60)" = 0 ]
  expect "and clears it" [ "$(state shutdown_pending)" = 0 ]
  rm -f "$STUB_DIR/shutdown_fail_always"
  teardown
}

# 0.8: time on the test clock; the deadline shutdown comes once idle for grace past the deadline
t_pending_write_failure() {
  setup
  use_clock
  g arm --idle 1m > /dev/null
  export AUTODL_TEST_FAIL_PUT=shutdown_pending
  expect "idle shutdown skipped when pending cannot be recorded" [ "$(tick_for 60)" != 10 ]
  expect "no shutdown call" [ ! -f "$STUB_DIR/shutdown_calls" ]
  unset AUTODL_TEST_FAIL_PUT
  teardown
  setup
  use_clock
  g arm --idle 60m --deadline 1m --grace 0s > /dev/null
  export AUTODL_TEST_FAIL_PUT=shutdown_pending
  expect "deadline shutdown goes ahead anyway" [ "$(tick_for 60)" = 10 ]
  expect "shutdown was called" [ -f "$STUB_DIR/shutdown_calls" ]
  unset AUTODL_TEST_FAIL_PUT
  teardown
}

# 0.8: the probes run before tick takes the lock, and the deadline no longer comes first: a hanging nvidia-smi is
# cut by its time limit, counts as unknown (in use), and once it answers again the deadline shutdown comes
t_a_hanging_probe_is_unknown_and_blocks_nothing() {
  setup
  use_clock
  export AUTODL_PROBE_TIMEOUT=1
  echo 1 > "$STUB_DIR/gpu"
  g arm --idle 60m --deadline 1m --grace 0s --gpu-probes 2 --dry-run > /dev/null
  touch "$STUB_DIR/hang"
  local t0
  t0="$(date +%s)"
  expect "tick with a hanging nvidia-smi still returns" [ "$(tick_for 60)" = 0 ]
  expect "each probe is cut by the timeout" [ $(($(date +%s) - t0)) -le 5 ]
  expect "a hanging probe counts as unknown" [ "$(sig gpu)" = unknown ]
  rm -f "$STUB_DIR/hang"
  expect "once it answers again, the deadline shutdown comes" [ "$(tick_for 60)" = 10 ]
  unset AUTODL_PROBE_TIMEOUT
  teardown
}

# 0.8: sessions no longer count as in use; still checked: the daemon runs inside a screen session and fires on
# its own, and a job can be started through screen
t_screen_launcher() {
  setup
  export AUTODL_SCREEN_CMD="$STUBS/fake-screen" AUTODL_LAUNCHER=screen
  unset AUTODL_NO_DAEMON
  g arm --idle 3s --interval 1s --dry-run > /dev/null
  expect "daemon runs inside a screen session" bash -c "[ -n \"\$(cat '$AUTODL_GUARD_HOME/state2/guard_sty')\" ]"
  g run sjob -- sleep 1 > /dev/null
  wait_job sjob
  expect "a job started through screen ran" [ "$(cat "$AUTODL_GUARD_HOME/jobs/sjob/rc")" = 0 ]
  local i
  for i in $(seq 1 24); do fired && break; sleep 0.5; done
  expect "daemon started through screen fires on its own" fired
  export AUTODL_SCREEN_CMD="$STUBS/screen" AUTODL_LAUNCHER=direct AUTODL_NO_DAEMON=1
  teardown
}

# 0.8: an overlong deadline_up is a damaged state: no new decision (needs_rearm=1), logged, and no crash
t_huge_numbers() {
  setup
  use_clock
  expect "huge duration rejected" fails g _secs 99999999h
  expect "more than 30 days rejected" fails g arm --idle 721h --dry-run
  g arm --idle 1m --dry-run > /dev/null
  printf '99999999999999999999999' > "$AUTODL_GUARD_HOME/state2/deadline_up"
  expect "an overlong deadline does not break tick" [ "$(tick_for 120)" = 0 ]
  expect "status says needs_rearm=1" status_has '^needs_rearm=1$'
  expect "and it is logged as corrupt" grep -q 'STATE CORRUPT deadline_up' "$AUTODL_GUARD_HOME/guard.log"
  teardown
}

t_stale_dry_run_ignored() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  export AUTODL_BOOT_MARKER=boot2
  expect "off-now after a reboot is real despite the old dry_run" quiet g off-now --reason "next boot"
  expect "the shutdown command was called" [ -f "$STUB_DIR/shutdown_calls" ]
  export AUTODL_BOOT_MARKER=boot1
  teardown
}

t_daemon_needed_before_job() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  unset AUTODL_NO_DAEMON
  export AUTODL_LAUNCHER=none AUTODL_LAUNCH_WAIT=1
  expect "run fails when the daemon cannot start" fails g run nodaemon -- true
  expect "and registers no job" [ ! -d "$AUTODL_GUARD_HOME/jobs/nodaemon" ]
  export AUTODL_LAUNCHER=direct AUTODL_LAUNCH_WAIT=5 AUTODL_NO_DAEMON=1
  teardown
}

t_off_now_failure_keeps_daemon() {
  setup
  g arm --idle 60m --deadline 60s --keep 60m > /dev/null
  unset AUTODL_NO_DAEMON
  touch "$STUB_DIR/shutdown_fail"
  expect "off-now reports the failed shutdown" fails g off-now --reason x
  expect "a daemon is running to retry it" status_has '^daemon_alive=1$'
  export AUTODL_NO_DAEMON=1
  teardown
}

# ---- group 6: fixes before the third Codex review ----
t_guard_name_reserved() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  expect "job name 'guard' rejected (logtail guard shows the guard's own log)" fails g run guard -- true
  expect "and no job directory is created for it" [ ! -d "$AUTODL_GUARD_HOME/jobs/guard" ]
  teardown
}

t_run_request_id_is_idempotent() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local count="$AUTODL_GUARD_HOME/rq_count"
  g run rq --req abcdef012345 -- "echo once >> '$count'" > /dev/null
  wait_job rq
  expect "the same request sent again is accepted" quiet g run rq --req abcdef012345 -- "echo once >> '$count'"
  sleep 1
  expect "and the job ran only once" [ "$(wc -l < "$count")" = 1 ]
  expect "and nothing was archived" bash -c "! ls -d '$AUTODL_GUARD_HOME'/jobs/rq.prev-* > /dev/null 2>&1"
  g run rq --req 0123456789ab -- "echo twice >> '$count'" > /dev/null
  wait_job rq
  expect "a new request runs the job again" [ "$(wc -l < "$count")" = 2 ]
  g run rq3 --req feedfacefeed -- sleep 2 > /dev/null
  expect "the same request while the job still runs is not an error" quiet g run rq3 --req feedfacefeed -- sleep 2
  expect "a request id with a space is rejected" fails g run rq4 --req 'a b c d e f g h' -- true
  expect "a too short request id is rejected" fails g run rq4 --req abc -- true
  wait_job rq3
  teardown
}

# ---- group 7: fixes after the third Codex review ----
# 0.8: a job no longer keeps the instance on by itself, so this checks the job's status: a child in its own
# session keeps the job running, also once the runner died, and the job is lost once the child is gone
t_setsid_child_keeps_the_job() {
  setup
  g arm --idle 60m --dry-run > /dev/null
  g run esc -- 'setsid sleep 4 < /dev/null > /dev/null 2>&1 & echo started' > /dev/null
  sleep 1.5
  expect "a child in its own session keeps the job running" status_has '^job.esc=running|'
  kill "$(cat "$AUTODL_GUARD_HOME/jobs/esc/pid")" 2> /dev/null   # the runner dies, the child lives on
  sleep 0.5
  expect "without its runner the tagged child still counts" status_has '^job.esc=running|'
  expect "and off-now refuses" [ "$(rc_of g off-now --reason x)" = 3 ]
  sleep 3.5
  expect "once the child is gone the job shows as lost" status_has '^job.esc=lost|'
  teardown
}

t_run_checks_the_command_it_received() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local sum
  sum="$(printf 'echo whole' | sha256sum | cut -d' ' -f1)"
  expect "a command cut short in transit is refused" \
    fails bash -c "printf 'echo wh' | bash '$GUARD' run cut --req aaaaaaaa1111 --cmd-sha256 $sum --cmd-stdin"
  expect "and nothing is registered" [ ! -d "$AUTODL_GUARD_HOME/jobs/cut" ]
  expect "the complete command is accepted" \
    quiet bash -c "printf 'echo whole' | bash '$GUARD' run whole --req aaaaaaaa2222 --cmd-sha256 $sum --cmd-stdin"
  wait_job whole
  expect "and runs" grep -q '^whole$' "$AUTODL_GUARD_HOME/jobs/whole/log"
  teardown
}

t_run_request_resumes_an_interrupted_launch() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local count="$AUTODL_GUARD_HOME/rs_count"
  AUTODL_TEST_EXIT_BEFORE_LAUNCH=1 g run rs --req 1234567890ab -- "echo run >> '$count'" > /dev/null 2>&1
  expect "the interrupted attempt started nothing" [ ! -f "$AUTODL_GUARD_HOME/jobs/rs/pid" ]
  expect "the same request resumes it" quiet g run rs --req 1234567890ab -- "echo run >> '$count'"
  wait_job rs
  expect "and the job ran exactly once" [ "$(wc -l < "$count")" = 1 ]
  teardown
}

t_arm_request_id() {
  setup
  expect "arm with a request id" quiet g arm --idle 60m --deadline 60s --keep 60s --dry-run --req bbbbbbbb0001
  expect "the same request again is accepted" quiet g arm --idle 60m --deadline 60s --keep 60s --dry-run --req bbbbbbbb0001
  expect "another request is refused (exit 5)" \
    [ "$(rc_of g arm --idle 60m --deadline 60s --keep 60s --dry-run --req bbbbbbbb0002)" = 5 ]
  teardown
}

# 0.8: times are uptime seconds; one before the arm, or one with a leading zero, is corrupt: no new decision,
# needs_rearm=1, logged
t_state_values_must_make_sense() {
  setup
  use_clock
  g arm --idle 1m --grace 0s --dry-run > /dev/null
  printf '1' > "$AUTODL_GUARD_HOME/state2/deadline_up"
  expect "a deadline before the arm is not taken as reached" [ "$(tick_for 60)" = 0 ]
  expect "it is logged as corrupt" grep -q 'STATE CORRUPT deadline_up=\[1\] before armed_up' "$AUTODL_GUARD_HOME/guard.log"
  printf '0%s' 1100 > "$AUTODL_GUARD_HOME/state2/deadline_up"
  expect "a leading zero makes it corrupt too" [ "$(tick_for 60)" = 0 ]
  expect "and that is logged" grep -q 'STATE CORRUPT deadline_up=\[01100\]' "$AUTODL_GUARD_HOME/guard.log"
  teardown
}

t_pending_metadata_is_written_first() {
  setup
  g arm --idle 60m --deadline 60s --keep 60m > /dev/null
  export AUTODL_TEST_FAIL_PUT=shutdown_kind
  expect "off-now does not shut down when the kind cannot be recorded" [ "$(rc_of g off-now --reason x)" = 2 ]
  expect "no shutdown call" [ ! -f "$STUB_DIR/shutdown_calls" ]
  expect "nothing is pending" [ "$(state shutdown_pending)" != 1 ]
  unset AUTODL_TEST_FAIL_PUT
  teardown
}

# 0.8: the GPU is always a signal in GPU mode (no --util-signal); time on the test clock
t_pending_retry_takes_a_fresh_gpu_sample() {
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  echo 0 > "$STUB_DIR/util"
  g arm --idle 1m > /dev/null
  touch "$STUB_DIR/shutdown_fail"
  expect "idle shutdown attempt fails while the GPU is idle" [ "$(tick_for 60)" = 10 ]
  echo 90 > "$STUB_DIR/util"
  expect "new GPU load cancels the pending idle shutdown" [ "$(tick_for 60)" = 0 ]
  expect "pending cleared" [ "$(state shutdown_pending)" = 0 ]
  teardown
}

t_arm_reports_a_daemon_that_did_not_start() {
  setup
  unset AUTODL_NO_DAEMON
  export AUTODL_LAUNCHER=none
  local out rc
  out="$(g arm --idle 60m --deadline 60s --keep 60s --dry-run 2>&1)"
  rc=$?
  expect "arm fails when the daemon cannot start" [ "$rc" != 0 ]
  expect "and says to run revive" bash -c "printf '%s' \"\$1\" | grep -q revive" _ "$out"
  expect "the configuration itself is in place" status_has '^armed_this_boot=1$'
  export AUTODL_LAUNCHER=direct AUTODL_NO_DAEMON=1
  teardown
}

# ---- group 8: fixes after the fourth Codex review ----
t_runner_dying_before_the_command_is_no_start() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local count="$AUTODL_GUARD_HOME/rd_count" out
  export AUTODL_LAUNCH_WAIT=2
  out="$(AUTODL_TEST_RUNNER_DIES_AFTER_PID=1 g run rd --req cccccccc0001 -- "echo ran >> '$count'" 2>&1)"
  expect "a runner that died before the command is reported as no start" \
    bash -c "printf '%s' \"\$1\" | grep -q 'did not'" _ "$out"
  expect "the command never ran" [ ! -s "$count" ]
  expect "the same request is not reported as started later" fails g run rd --req cccccccc0001 -- "echo ran >> '$count'"
  export AUTODL_LAUNCH_WAIT=5
  teardown
}

t_late_runner_does_not_start_the_command() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local count="$AUTODL_GUARD_HOME/lr_count"
  export AUTODL_LAUNCHER=none AUTODL_LAUNCH_WAIT=1
  expect "a start that never came up fails" fails g run lr -- "echo ran >> '$count'"
  export AUTODL_LAUNCHER=direct AUTODL_LAUNCH_WAIT=5
  g _job lr anytoken > /dev/null 2>&1
  expect "a runner arriving late does not start the command" [ ! -s "$count" ]
  teardown
}

t_arm_publishes_the_boot_last() {
  setup
  export AUTODL_TEST_FAIL_PUT=armed_boot
  expect "arm fails when the last write fails" fails g arm --idle 60m --deadline 60s --keep 60s --dry-run --req dddddddd0001
  unset AUTODL_TEST_FAIL_PUT
  expect "and this boot does not count as armed" status_has '^armed_this_boot=0$'
  export AUTODL_TEST_FAIL_PUT=arm_req
  expect "arm fails when the request cannot be recorded" fails g arm --idle 60m --deadline 60s --keep 60s --dry-run --req dddddddd0001
  unset AUTODL_TEST_FAIL_PUT
  expect "still not armed" status_has '^armed_this_boot=0$'
  expect "the same request then arms normally" quiet g arm --idle 60m --deadline 60s --keep 60s --dry-run --req dddddddd0001
  teardown
}

# 0.8: --idle is required, and the state files are deadline_up and keep_until_up now
t_rearm_with_the_same_request_changes_nothing() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  g arm --idle 60m --deadline 1h --keep 60s --dry-run --rearm --req eeeeeeee0001 > /dev/null
  local before
  before="$(state deadline_up)"
  sleep 1.2
  expect "the same rearm again is accepted" quiet g arm --idle 60m --deadline 1h --keep 60s --dry-run --rearm --req eeeeeeee0001
  expect "and does not push the deadline" [ "$(state deadline_up)" = "$before" ]
  teardown
}

# 0.8: the counters' baseline takes the place of the stored GPU samples; a check that cannot store it counts as
# in use, since the next one would average over a longer time
t_a_baseline_that_cannot_be_stored_counts_as_in_use() {
  setup
  use_clock
  g arm --idle 2m --dry-run > /dev/null
  export AUTODL_TEST_FAIL_PUT=counters
  expect "no shutdown while the baseline cannot be stored" [ "$(tick_for 180)" = 0 ]
  expect "active_why says so" grep -q counters:unstored "$AUTODL_GUARD_HOME/state2/active_why"
  unset AUTODL_TEST_FAIL_PUT
  expect "once it can be stored again, idleness counts" [ "$(tick_for 180)" = 10 ]
  teardown
}

t_ended_job_ignores_a_reused_process_group() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  g run ended -- true > /dev/null
  wait_job ended
  printf '%s' "$(ps -o pgid= -p $$ | tr -d ' ')" > "$AUTODL_GUARD_HOME/jobs/ended/pgid"   # a live group now
  expect "an ended job stays ended when its old group number is reused" status_has '^job.ended=done:0|'
  teardown
}

# ---- group 9: fixes after the fifth Codex review ----
# 0.8: a job no longer keeps the instance on by itself, so only the job's status is checked
t_tag_scan_needs_no_find_or_xargs() {
  setup
  g arm --idle 60m --dry-run > /dev/null
  g run esc2 -- 'setsid sleep 6 < /dev/null > /dev/null 2>&1 & echo started' > /dev/null
  sleep 1.5
  kill "$(cat "$AUTODL_GUARD_HOME/jobs/esc2/pid")" 2> /dev/null   # the runner dies, the tagged child lives on
  sleep 1.5
  mkdir -p "$T/badbin"
  printf '#!/bin/sh\nexit 1\n' > "$T/badbin/find"
  printf '#!/bin/sh\nexit 1\n' > "$T/badbin/xargs"
  chmod +x "$T/badbin/find" "$T/badbin/xargs"
  expect "broken find and xargs do not hide a tagged process" \
    bash -c 'PATH="$1:$PATH" bash "$2" status | grep -q "^job.esc2=running|"' _ "$T/badbin" "$GUARD"
  teardown
}

# 0.8: a job no longer keeps the instance on by itself; a job that may be alive still shows as running and still
# makes off-now refuse
t_a_proc_scan_that_reads_nothing_counts_as_maybe_alive() {
  setup
  g arm --idle 60m --dry-run > /dev/null
  local d="$AUTODL_GUARD_HOME/jobs/blind" dead
  true &
  dead=$!
  wait "$dead"
  mkdir -p "$d" "$T/fakeproc/1"   # looks like a /proc, but nothing in it can be read
  printf 'boot1' > "$d/boot"
  printf '%s' $(($(date +%s) - 300)) > "$d/start"
  printf '%s' "$dead" > "$d/pid"
  printf 'blind.1.1' > "$d/tag"
  expect "a /proc pass that cannot read its own entries counts the job as maybe alive" \
    bash -c 'AUTODL_TEST_PROC="$1" bash "$2" status | grep -q "^job.blind=running|"' _ "$T/fakeproc" "$GUARD"
  expect "so off-now refuses" [ "$(AUTODL_TEST_PROC="$T/fakeproc" rc_of g off-now --reason x)" = 3 ]
  expect "with the real /proc the job is lost" status_has '^job.blind=lost|'
  teardown
}

# 0.8: the state files of the 0.8 arm, in the order it writes them, and time on the test clock between checks
t_an_interrupted_rearm_is_not_half_applied() {
  local k
  for k in schema shutdown_reason shutdown_kind shutdown_attempts shutdown_boot clock_lost armed_at armed_up mode \
    idle_s grace_s interval_s gpu_probes thr_gpu thr_cpu thr_io thr_net unreliable calib calib_coverage env_setup \
    dry_run deadline_up deadline_at keep_until_up keep_until_at off_when_done off_when_done_reason last_active_up \
    last_active_at active_why counters arm_gen arm_req armed_boot; do
    setup
    use_clock
    g arm --idle 1m --dry-run > /dev/null
    AUTODL_TEST_FAIL_PUT=$k g arm --rearm --idle 1m --dry-run --req ffffffff0001 > /dev/null 2>&1
    expect "rearm cut at $k: status says the arm is incomplete" status_has '^arm_incomplete=1$'
    expect "rearm cut at $k: no shutdown meanwhile" [ "$(tick_for 120)" = 0 ]
    expect "rearm cut at $k: the same request completes it" quiet g arm --rearm --idle 1m --dry-run --req ffffffff0001
    expect "rearm cut at $k: then idle shutdown works again" [ "$(tick_for 60)" = 10 ]
    teardown
  done
}

# 0.8: --idle is required, and the state files are deadline_up and keep_until_up now
t_an_incomplete_arm_blocks_jobs_until_an_arm_completes() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  AUTODL_TEST_FAIL_PUT=keep_until_up g arm --rearm --idle 60m --deadline 2h --keep 5m --dry-run > /dev/null 2>&1
  expect "run is refused while the arm is incomplete" fails g run inc -- true
  expect "and so is keep" fails g keep 5m --reason x
  expect "status does not count this boot as armed" status_has '^armed_this_boot=0$'
  expect "a plain arm (no --rearm) completes it" quiet g arm --idle 60m --deadline 2h --keep 5m --dry-run
  expect "then run works again" quiet g run inc -- true
  wait_job inc
  teardown
}

# 0.8: --idle is required, and the state files are deadline_up and keep_until_up now
t_a_rearm_that_cannot_begin_changes_nothing() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local dl
  dl="$(state deadline_up)"
  expect "a rearm whose first write fails is refused" \
    fails env AUTODL_TEST_FAIL_PUT=arm_incomplete bash "$GUARD" arm --rearm --idle 60m --deadline 2h --keep 5m --dry-run
  expect "the old configuration still counts as complete" status_has '^arm_incomplete=0$'
  expect "and armed" status_has '^armed_this_boot=1$'
  expect "with its deadline unchanged" [ "$(state deadline_up)" = "$dl" ]
  teardown
}

t_a_runner_dying_after_the_spawn_is_reported_not_restarted() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local count="$AUTODL_GUARD_HOME/sp_count" rc
  export AUTODL_LAUNCH_WAIT=2
  AUTODL_TEST_RUNNER_DIES_AFTER_SPAWN=1 g run sp --req 5555555500a1 -- "echo ran >> '$count'; sleep 3" > /dev/null 2>&1
  rc=$?
  expect "a runner that died after starting the command: uncertain (exit 6)" [ "$rc" = 6 ]
  expect "the command did run" [ "$(wc -l < "$count")" = 1 ]
  expect "the job is not marked as ended" [ ! -f "$AUTODL_GUARD_HOME/jobs/sp/end" ]
  expect "it counts as running while the command runs" status_has '^job.sp=running|'
  expect "the same request is not started again (exit 6)" \
    [ "$(rc_of g run sp --req 5555555500a1 -- "echo ran >> '$count'; sleep 3")" = 6 ]
  sleep 3.5
  expect "the command ran only once" [ "$(wc -l < "$count")" = 1 ]
  export AUTODL_LAUNCH_WAIT=5
  teardown
}

t_a_resend_after_a_cut_off_start_does_not_run_it_twice() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local count="$AUTODL_GUARD_HOME/cut_count"
  AUTODL_TEST_EXIT_AFTER_LAUNCH=1 AUTODL_TEST_RUNNER_DIES_AFTER_SPAWN=1 \
    g run cut2 --req 5555555500b2 -- "echo ran >> '$count'; sleep 2" > /dev/null 2>&1
  wait_file "$AUTODL_GUARD_HOME/jobs/cut2/pgid"   # the runner started the command, then died
  sleep 0.5
  expect "the resend reports an uncertain start (exit 6)" \
    [ "$(rc_of g run cut2 --req 5555555500b2 -- "echo ran >> '$count'; sleep 2")" = 6 ]
  sleep 2.5
  expect "and the command ran only once" [ "$(wc -l < "$count")" = 1 ]
  teardown
}

t_a_start_that_cannot_be_recorded_is_uncertain() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local count="$AUTODL_GUARD_HOME/rf_count" rc
  export AUTODL_LAUNCH_WAIT=2
  AUTODL_TEST_FAIL_JOBFILE=running g run rf --req 5555555500c3 -- "echo ran >> '$count'; sleep 4" > /dev/null 2>&1
  rc=$?
  expect "a start that could not be recorded is uncertain (exit 6)" [ "$rc" = 6 ]
  expect "the same request is not started again (exit 6)" \
    [ "$(rc_of g run rf --req 5555555500c3 -- "echo ran >> '$count'; sleep 4")" = 6 ]
  wait_job rf
  expect "the command ran once" [ "$(wc -l < "$count")" = 1 ]
  expect "and the job still ended normally" [ "$(cat "$AUTODL_GUARD_HOME/jobs/rf/rc" 2> /dev/null)" = 0 ]
  export AUTODL_LAUNCH_WAIT=5
  teardown
}

# 0.8: the daemon reaches the shutdown through --idle 1s instead of a deadline
t_a_hanging_shutdown_does_not_block_revive_restart() {
  setup
  unset AUTODL_NO_DAEMON
  touch "$STUB_DIR/shutdown_hang"
  g arm --idle 1s --interval 1s > /dev/null
  wait_file "$STUB_DIR/shutdown_calls"   # the daemon is now stuck in the shutdown command
  local p1
  p1="$(state daemon_pid)"
  expect "revive --restart replaces a daemon stuck in shutdown" [ "$(rc_of g revive --restart)" = 0 ]
  expect "with a new daemon" [ "$(state daemon_pid)" != "$p1" ]
  expect "that holds the daemon lock" status_has '^daemon_alive=1$'
  rm -f "$STUB_DIR/shutdown_hang"
  export AUTODL_NO_DAEMON=1
  teardown
}

# 0.8: no --util-signal (the daemon probes the GPU in GPU mode anyway); one probe per hour-long interval keeps the
# probe's time limit at AUTODL_PROBE_TIMEOUT, and --mode gpu keeps the hanging stub out of the arm itself
t_a_hanging_probe_does_not_block_revive_restart() {
  setup
  unset AUTODL_NO_DAEMON
  export AUTODL_PROBE_TIMEOUT=60   # the probe must hang longer than revive waits for the old daemon (25s)
  echo 1 > "$STUB_DIR/gpu"
  touch "$STUB_DIR/hang"   # the daemon's first GPU probe hangs (inside a command substitution)
  g arm --idle 60m --interval 1h --gpu-probes 1 --mode gpu --dry-run > /dev/null
  sleep 1.5
  local p1
  p1="$(state daemon_pid)"
  expect "revive --restart replaces a daemon stuck in a probe" [ "$(rc_of g revive --restart)" = 0 ]
  expect "with a new daemon" [ "$(state daemon_pid)" != "$p1" ]
  rm -f "$STUB_DIR/hang"
  unset AUTODL_PROBE_TIMEOUT
  export AUTODL_NO_DAEMON=1
  teardown
}

t_a_killed_command_does_not_keep_the_lifecycle_lock() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local holder k
  flock "$AUTODL_GUARD_HOME/state2/.lock" sleep 6 &   # someone holds the state lock for a while
  holder=$!
  sleep 0.5
  bash "$GUARD" keep 5m --reason x > /dev/null 2>&1 &   # takes the lifecycle lock, then waits for the state lock
  k=$!
  sleep 1
  kill -KILL "$k"
  sleep 0.3
  expect "the lifecycle lock is free once that command is dead" [ "$(rc_of timeout 3 bash "$GUARD" revive)" = 0 ]
  wait "$holder" 2> /dev/null
  teardown
}

t_missing_flock_is_refused() {
  setup
  local out
  out="$(AUTODL_FLOCK_CMD=no-such-flock bash "$GUARD" arm --idle 60m --deadline 60s --keep 60s --dry-run 2>&1)"
  expect "without flock arm refuses" [ "$?" != 0 ]
  expect "and says why" bash -c 'printf "%s" "$1" | grep -q flock' _ "$out"
  expect "and arms nothing" [ ! -f "$AUTODL_GUARD_HOME/state2/armed_boot" ]
  expect "and creates nothing" [ ! -d "$AUTODL_GUARD_HOME" ]
  teardown
}

# ---- group 10: fixes after the sixth Codex review ----
# 0.8: a job no longer keeps the instance on by itself; a job that cannot be told not to run still shows as
# running and still makes off-now refuse
t_unreadable_job_files_count_as_running() {
  setup
  g arm --idle 60m --dry-run > /dev/null
  g run jr -- sleep 6 > /dev/null
  sleep 1
  chmod 000 "$AUTODL_GUARD_HOME/jobs/jr/boot"
  expect "a job whose boot file cannot be read still counts as running" [ "$(rc_of g off-now --reason x)" = 3 ]
  chmod 644 "$AUTODL_GUARD_HOME/jobs/jr/boot"
  : > "$AUTODL_GUARD_HOME/jobs/jr/boot"
  expect "and so does one whose boot file is empty" status_has '^job.jr=running|'
  printf 'boot1' > "$AUTODL_GUARD_HOME/jobs/jr/boot"
  wait_job jr
  teardown
  setup
  g arm --idle 60m --dry-run > /dev/null
  g run esc3 -- 'setsid sleep 6 < /dev/null > /dev/null 2>&1 & echo started' > /dev/null
  sleep 1.5
  kill "$(cat "$AUTODL_GUARD_HOME/jobs/esc3/pid")" 2> /dev/null   # only the tagged child is left
  sleep 1.5
  chmod 000 "$AUTODL_GUARD_HOME/jobs/esc3/tag"
  expect "a job whose tag file cannot be read still counts as running" [ "$(rc_of g off-now --reason x)" = 3 ]
  chmod 644 "$AUTODL_GUARD_HOME/jobs/esc3/tag"
  teardown
}

t_a_job_that_ended_before_its_start_was_recorded_is_reported_started() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local count="$AUTODL_GUARD_HOME/en_count" out rc
  export AUTODL_LAUNCH_WAIT=2
  out="$(AUTODL_TEST_FAIL_JOBFILE=running g run en --req 5555555500d4 -- "echo ran >> '$count'" 2>&1)"
  rc=$?
  expect "a job that ran and ended before its start was recorded: started (exit 0)" [ "$rc" = 0 ]
  expect "and says that it has ended" bash -c 'printf "%s" "$1" | grep -q "ended"' _ "$out"
  expect "a resend is not told that it did not start" \
    [ "$(rc_of g run en --req 5555555500d4 -- "echo ran >> '$count'")" = 0 ]
  expect "the command ran once" [ "$(wc -l < "$count")" = 1 ]
  export AUTODL_LAUNCH_WAIT=5
  teardown
}

t_restart_never_signals_an_unverified_process() {
  setup
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  local victim
  sleep 30 &
  victim=$!
  printf '%s' "$victim" > "$AUTODL_GUARD_HOME/state2/daemon_pid"   # a stale record: someone else's process now
  : > "$AUTODL_GUARD_HOME/state2/daemon_pstart"                     # and no start time to check it against
  g revive --restart > /dev/null 2>&1
  expect "revive --restart does not signal a process it cannot verify" kill -0 "$victim"
  kill "$victim" 2> /dev/null
  wait "$victim" 2> /dev/null
  teardown
}

t_an_unknown_boot_is_never_armed() {
  setup
  unset AUTODL_BOOT_MARKER
  mkdir -p "$T/noboot"   # a /proc stand-in without process 1: the boot marker cannot be read
  expect "arm refuses when the boot marker cannot be read" \
    fails env AUTODL_TEST_PROC="$T/noboot" bash "$GUARD" arm --idle 60m --deadline 60s --keep 60s --dry-run --mode nogpu
  mkdir -p "$AUTODL_GUARD_HOME/state2"
  printf 'unknown' > "$AUTODL_GUARD_HOME/state2/armed_boot"
  expect "a boot recorded as unknown never counts as armed" \
    bash -c 'AUTODL_TEST_PROC="$1" bash "$2" status | grep -q "^armed_this_boot=0$"' _ "$T/noboot" "$GUARD"
  export AUTODL_BOOT_MARKER=boot1
  teardown
}

t_no_external_command_holds_a_lock() {
  setup
  export AUTODL_SYNC_CMD=sync   # the real sync through the wrappers below; sync -f touches only the test directory's filesystem
  local n p
  mkdir -p "$T/fdbin"
  # every wrapped command notes any descriptor it inherited that points at one of the guard's lock files
  # (compared by inode: the test shell may hold unrelated descriptors), then runs the real command
  cat > "$T/fdstub" << 'EOF'
#!/bin/bash
for p in /proc/$$/fd/*; do
  for l in .lock .life.lock .daemon.lock; do
    if [ "$p" -ef "$AUTODL_GUARD_HOME/state2/$l" ]; then printf '%s fd%s %s\n' "${0##*/}" "${p##*/}" "$l" >> "$FDLOG"; fi
  done
done
real="$(PATH="$REALPATH" command -v "${0##*/}")"   # PATH itself stays wrapped for what the command starts
exec "$real" "$@"
EOF
  for n in cat date mv rm mkdir dirname sleep seq sed tr grep tail head awk cut timeout sync env setsid nohup \
    sha256sum mktemp readlink; do
    cp "$T/fdstub" "$T/fdbin/$n"
    chmod +x "$T/fdbin/$n"
  done
  export FDLOG="$T/fdlog" REALPATH="$PATH"
  p="$T/fdbin:$PATH"
  PATH="$p" g arm --idle 60m --deadline 60s --keep 0s --grace 0s --dry-run > /dev/null
  PATH="$p" g run fdj --req 5555555500e5 -- 'sleep 1' > /dev/null
  PATH="$p" g tick > /dev/null
  PATH="$p" g keep 5m --reason x > /dev/null
  wait_job fdj
  PATH="$p" g off-now --reason x > /dev/null
  expect "no external command started under a lock inherits a lock descriptor" [ ! -s "$FDLOG" ]
  if [ -s "$FDLOG" ]; then sort "$FDLOG" | uniq -c | sed 's/^/    /'; fi
  teardown
}

# ---- group 11 (0.7.1): read-only activity sampling ----
is_num() { [[ "${1:-}" =~ ^[0-9]+$ ]]; }
in_range() { [ "$1" -ge "$2" ] && [ "$1" -le "$3" ]; }                  # in_range N LOW HIGH
col() { printf '%s\n' "$2" | awk -F'\t' -v i="$1" '{ print $i }'; }   # col N LINE: the Nth tab-separated field
data_rows() { sed -n '/^#/!p' "$1"; }                                   # data_rows FILE: the rows, without comment lines
row_n() { data_rows "$1" | sed -n "${2}p"; }                            # row_n FILE N: the Nth row
first_row() { g sample "$@" 2> /dev/null | sed -n '/^#/!p' | sed -n 1p; }
second_row() { g sample "$@" 2> /dev/null | sed -n '/^#/!p' | sed -n 2p; }
refused_with() {  # refused_with TEXT ARGS...: the guard refuses at once, saying TEXT (5 s cap: a missing check must not hang the suite)
  local text="$1"
  shift
  timeout 5 bash "$GUARD" "$@" 2>&1 | grep -q -- "$text"
}
fake_signals() {  # a fake cgroup directory, /proc/net/dev and /proc/uptime for the sample command
  mkdir -p "$STUB_DIR/cg"
  printf 'usage_usec 123456\nuser_usec 100000\nsystem_usec 23456\n' > "$STUB_DIR/cg/cpu.stat"
  printf '8:0 rbytes=1000 wbytes=2000 rios=1 wios=2 dbytes=0 dios=0\n259:0 rbytes=5 wbytes=5 rios=1 wios=1 dbytes=0 dios=0\n' > "$STUB_DIR/cg/io.stat"
  {
    printf 'Inter-|   Receive                                                |  Transmit\n'
    printf ' face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n'
    printf '    lo:    9999      10    0    0    0     0          0         0     9999      10    0    0    0     0       0          0\n'
    printf '  eth0:123456789  100    0    0    0     0          0         0      300       3    0    0    0     0       0          0\n'
  } > "$STUB_DIR/net_dev"
  printf '12345.67 99999.99\n' > "$STUB_DIR/uptime"
  export AUTODL_TEST_CGROUP_DIR="$STUB_DIR/cg" AUTODL_TEST_NET_DEV="$STUB_DIR/net_dev" AUTODL_TEST_UPTIME="$STUB_DIR/uptime"
}
real_uptime() { unset AUTODL_TEST_UPTIME; }   # a run that waits between rows needs a clock that moves
unfake_signals() { unset AUTODL_TEST_CGROUP_DIR AUTODL_TEST_NET_DEV AUTODL_TEST_UPTIME AUTODL_TEST_PROC; }

t_sample_reads_the_counters() {
  setup
  fake_signals
  g sample --count 0 > "$T/out" 2> /dev/null
  local r
  r="$(row_n "$T/out" 1)"
  expect "sample --count 0: just the baseline row" [ "$(data_rows "$T/out" | wc -l)" -eq 1 ]
  expect "sample: nine columns" [ "$(printf '%s\n' "$r" | awk -F'\t' '{ print NF }')" = 9 ]
  expect "sample: epoch column" is_num "$(col 1 "$r")"
  expect "sample: uptime in centiseconds" [ "$(col 2 "$r")" = 1234567 ]
  expect "sample: cgroup cpu usage_usec" [ "$(col 3 "$r")" = 123456 ]
  expect "sample: io bytes summed over devices" [ "$(col 4 "$r")" = 3010 ]
  expect "sample: net bytes without lo, name joined to the number" [ "$(col 5 "$r")" = 123457089 ]
  expect "sample: the baseline row covers no gpu interval" [ "$(col 6 "$r")" = na ]
  expect "sample: nor any gpu failure" [ "$(col 7 "$r")" = na ]
  expect "sample: no daemon, no guard cpu" [ -z "$(col 8 "$r")" ]
  expect "sample: its own cpu, from /proc" is_num "$(col 9 "$r")"
  expect "sample: the parameter line" grep -q '^# autodl_guard [0-9][0-9.]* sample every=10 count=0 gpu_samples=3 clk_tck=[0-9]' "$T/out"
  expect "sample: the column names" grep -qx $'# epoch\tuptime_cs\tcpu_usec\tio_bytes\tnet_bytes\tgpu_max\tgpu_fail\tguard_ticks\tself_ticks' "$T/out"
  unfake_signals
  teardown
}

t_sample_leaves_unreadable_or_malformed_counters_empty() {
  setup
  fake_signals
  local r
  rm -f "$STUB_DIR/cg/cpu.stat" "$STUB_DIR/cg/io.stat"
  printf 'h1\nh2\n  eth0: x 1 2\n' > "$STUB_DIR/net_dev"
  r="$(first_row --count 0)"
  expect "sample: missing cpu.stat is empty" [ -z "$(col 3 "$r")" ]
  expect "sample: missing io.stat is empty" [ -z "$(col 4 "$r")" ]
  expect "sample: a malformed net line gives no partial sum" [ -z "$(col 5 "$r")" ]
  expect "sample: the uptime is still read" [ "$(col 2 "$r")" = 1234567 ]
  printf 'user_usec 1\nsystem_usec 2\n' > "$STUB_DIR/cg/cpu.stat"                # no usage_usec
  printf '8:0 rbytes=1000 wbytes=2000\n259:0 rbytes=5' > "$STUB_DIR/cg/io.stat"   # cut off: no wbytes, no newline
  printf 'h1\nh2\n  eth0:123456789  100    0' > "$STUB_DIR/net_dev"                  # cut off after three fields
  r="$(first_row --count 0)"
  expect "sample: cpu.stat without usage_usec is empty" [ -z "$(col 3 "$r")" ]
  expect "sample: a cut-off io.stat line gives no partial sum" [ -z "$(col 4 "$r")" ]
  expect "sample: a cut-off net line gives no partial sum" [ -z "$(col 5 "$r")" ]
  printf '8:0 rbytes=1 rbytes=2 wbytes=3\n' > "$STUB_DIR/cg/io.stat"
  expect "sample: a repeated io key is malformed" [ -z "$(col 4 "$(first_row --count 0)")" ]
  printf 'total rbytes=1 wbytes=2\n' > "$STUB_DIR/cg/io.stat"
  expect "sample: an io line must start with a device" [ -z "$(col 4 "$(first_row --count 0)")" ]
  printf '8:0 rbytes=1000 wbytes=2000' > "$STUB_DIR/cg/io.stat"                    # complete, only the newline is missing
  expect "sample: a whole last line without a newline still counts" [ "$(col 4 "$(first_row --count 0)")" = 3000 ]
  unfake_signals
  teardown
}

t_sample_rows_cover_the_interval_before_them() {
  setup
  fake_signals
  real_uptime
  echo 1 > "$STUB_DIR/gpu"
  echo 37 > "$STUB_DIR/util"
  g sample --every 1s --count 2 --gpu-samples 2 > "$T/out" 2> /dev/null
  local u1 u2 u3
  u1="$(col 2 "$(row_n "$T/out" 1)")"
  u2="$(col 2 "$(row_n "$T/out" 2)")"
  u3="$(col 2 "$(row_n "$T/out" 3)")"
  expect "sample: a baseline row and one row per interval" [ "$(data_rows "$T/out" | wc -l)" -eq 3 ]
  expect "sample: the baseline has no gpu reading" [ "$(col 6 "$(row_n "$T/out" 1)")" = na ]
  expect "sample: interval 1 has its gpu reading" [ "$(col 6 "$(row_n "$T/out" 2)")" = 37 ]
  expect "sample: interval 1 had no failed gpu sample" [ "$(col 7 "$(row_n "$T/out" 2)")" = 0 ]
  expect "sample: interval 2 has its gpu reading" [ "$(col 6 "$(row_n "$T/out" 3)")" = 37 ]
  expect "sample: two gpu samples per interval" [ "$(grep -c utilization "$STUB_DIR/nvsmi_calls" 2> /dev/null)" = 4 ]
  expect "sample: the first interval ends one interval after the baseline" in_range $((u2 - u1)) 100 200
  expect "sample: the second one interval later" in_range $((u3 - u1)) 200 300
  unfake_signals
  teardown
}

t_sample_gpu_is_unknown_if_any_reading_fails() {
  setup
  fake_signals
  real_uptime
  echo 1 > "$STUB_DIR/gpu"
  local r
  printf '37\nfail\n' > "$STUB_DIR/util_seq"   # the second of two samples fails
  r="$(second_row --every 1s --count 1 --gpu-samples 2)"
  expect "sample: one failed sample makes the interval unknown" [ -z "$(col 6 "$r")" ]
  expect "sample: and it is counted" [ "$(col 7 "$r")" = 1 ]
  printf '37\n80\n' > "$STUB_DIR/util"          # two GPUs
  r="$(second_row --every 1s --count 1 --gpu-samples 1)"
  expect "sample: the busiest of several GPUs" [ "$(col 6 "$r")" = 80 ]
  printf '37\n[N/A]\n' > "$STUB_DIR/util"       # one GPU line is not a number
  r="$(second_row --every 1s --count 1 --gpu-samples 1)"
  expect "sample: one bad GPU line makes the reading unknown" [ -z "$(col 6 "$r")" ]
  expect "sample: counted as a failed sample" [ "$(col 7 "$r")" = 1 ]
  printf '3 7\n' > "$STUB_DIR/util"             # a blank inside the number
  expect "sample: a number with a blank inside is not a reading" [ -z "$(col 6 "$(second_row --every 1s --count 1 --gpu-samples 1)")" ]
  printf '101\n' > "$STUB_DIR/util"             # more than 100%
  expect "sample: utilization above 100 is not a reading" [ -z "$(col 6 "$(second_row --every 1s --count 1 --gpu-samples 1)")" ]
  printf '37;80\n37\n' > "$STUB_DIR/util_seq"   # the second sample answers for one GPU fewer
  r="$(second_row --every 1s --count 1 --gpu-samples 2)"
  expect "sample: a GPU missing from one sample makes the interval unknown" [ -z "$(col 6 "$r")" ]
  expect "sample: and that sample is counted as failed" [ "$(col 7 "$r")" = 1 ]
  echo 0 > "$STUB_DIR/gpu"                      # nvidia-smi fails, as in non-GPU mode
  r="$(second_row --every 1s --count 1 --gpu-samples 2)"
  expect "sample: failing nvidia-smi gives no gpu value" [ -z "$(col 6 "$r")" ]
  expect "sample: both samples counted as failed" [ "$(col 7 "$r")" = 2 ]
  unfake_signals
  teardown
}

t_sample_without_gpu_never_calls_nvidia_smi() {
  setup
  fake_signals
  real_uptime
  local r
  r="$(second_row --every 1s --count 1 --gpu-samples 0)"
  expect "sample --gpu-samples 0: gpu not applicable" [ "$(col 6 "$r")" = na ]
  expect "sample --gpu-samples 0: no failures either" [ "$(col 7 "$r")" = na ]
  expect "sample --gpu-samples 0: the interval still has its counters" [ "$(col 3 "$r")" = 123456 ]
  expect "sample --gpu-samples 0: nvidia-smi never runs" [ ! -e "$STUB_DIR/nvsmi_calls" ]
  unfake_signals
  teardown
}

t_sample_keeps_its_schedule_when_nvidia_smi_hangs() {
  setup
  fake_signals
  real_uptime
  echo 1 > "$STUB_DIR/gpu"
  touch "$STUB_DIR/hang"
  export AUTODL_PROBE_TIMEOUT=10   # the default: K samples of 10 s each would not fit in the interval
  local t0 el u1 u3
  # K x timeout (2 x 10 s) is far more than the 2 s interval: each probe gets its 1 s share only
  t0="$(date +%s)"
  g sample --every 2s --count 2 --gpu-samples 2 > "$T/out" 2> /dev/null
  el=$(($(date +%s) - t0))
  u1="$(col 2 "$(row_n "$T/out" 1)")"
  u3="$(col 2 "$(row_n "$T/out" 3)")"
  expect "sample: every row despite the hanging probe" [ "$(data_rows "$T/out" | wc -l)" -eq 3 ]
  expect "sample: a timed-out probe is a failed sample" [ "$(col 7 "$(row_n "$T/out" 2)")" = 2 ]
  expect "sample: a probe gets only its share of the interval" in_range $((u3 - u1)) 400 700
  expect "sample: the run takes about count x every" [ "$el" -le 8 ]
  # an interval too short for even one probe per sample: what does not fit is skipped and counted as failed
  g sample --every 1s --count 2 --gpu-samples 3 > "$T/out" 2> /dev/null
  u1="$(col 2 "$(row_n "$T/out" 1)")"
  u3="$(col 2 "$(row_n "$T/out" 3)")"
  expect "sample: samples that no longer fit are counted as failed" [ "$(col 7 "$(row_n "$T/out" 2)")" = 3 ]
  expect "sample: and the rows keep their pace" in_range $((u3 - u1)) 200 400
  rm -f "$STUB_DIR/hang"
  unset AUTODL_PROBE_TIMEOUT
  unfake_signals
  teardown
}

t_sample_refuses_without_a_clock() {
  setup
  fake_signals
  printf 'not-a-number 1\n' > "$STUB_DIR/uptime"
  expect "sample refuses when the uptime cannot be read" refused_with "cannot read" sample --every 1s --count 1
  expect "and prints nothing on stdout" [ -z "$(g sample --every 1s --count 1 2> /dev/null)" ]
  rm -f "$STUB_DIR/uptime"
  expect "sample refuses when the uptime file is missing" refused_with "cannot read" sample --count 0
  unfake_signals
  teardown
}

t_sample_needs_no_lock_and_writes_nothing() {
  setup
  fake_signals
  : > "$STUB_DIR/cg/io.stat"   # no I/O yet: a readable empty file means 0, not unknown
  local rc
  AUTODL_FLOCK_CMD=/nonexistent-flock g sample --count 0 > "$T/out" 2> /dev/null
  rc=$?
  expect "sample runs without flock" [ "$rc" = 0 ]
  expect "sample: empty io.stat reads as 0" [ "$(col 4 "$(row_n "$T/out" 1)")" = 0 ]
  expect "sample creates no guard directory" [ ! -e "$AUTODL_GUARD_HOME" ]
  expect "sample rejects a negative count" refused_with "count must be" sample --count -1
  expect "sample rejects a count that is not a number" refused_with "count must be" sample --count x
  expect "sample rejects more than 20 gpu samples" refused_with "gpu-samples must be" sample --gpu-samples 21
  expect "sample rejects an interval under 1s" refused_with "at least 1s" sample --every 0s
  expect "sample rejects a run longer than 30 days" refused_with "30 days" sample --every 1h --count 721
  expect "sample rejects an unknown option" refused_with "unknown option" sample --bogus
  unfake_signals
  teardown
}

t_sample_counts_the_guard_cpu_only_for_the_verified_daemon() {
  setup
  fake_signals
  mkdir -p "$AUTODL_GUARD_HOME/state2" "$T/proc/4242"
  printf '4242' > "$AUTODL_GUARD_HOME/state2/daemon_pid"      # the guard stores values without a newline
  printf '5000' > "$AUTODL_GUARD_HOME/state2/daemon_pstart"
  # a command name with spaces and parentheses; utime stime cutime cstime = 11 22 33 44; start time 5000
  printf '4242 (bash (x) y) S 1 4242 4242 0 -1 4194560 100 0 0 0 11 22 33 44 20 0 1 0 5000 0 0\n' > "$T/proc/4242/stat"
  export AUTODL_TEST_PROC="$T/proc"
  expect "sample: guard cpu = utime+stime+cutime+cstime" [ "$(col 8 "$(first_row --count 0)")" = 110 ]
  printf '4999' > "$AUTODL_GUARD_HOME/state2/daemon_pstart"   # the PID now belongs to another process
  expect "sample: a reused PID is not the guard" [ -z "$(col 8 "$(first_row --count 0)")" ]
  rm -f "$AUTODL_GUARD_HOME/state2/daemon_pid"   # the pid is fake: teardown must not signal it
  unfake_signals
  teardown
}

# ---- group 12 (0.7.1): a sync that hangs in the kernel does not hold up a shutdown ----
t_shutdown_does_not_wait_for_a_hanging_sync() {
  setup
  export AUTODL_SYNC_WAIT=2
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  touch "$STUB_DIR/sync_hang"
  local t0 el
  t0="$(date +%s)"
  timeout -k 5 30 bash "$GUARD" off-now --reason "stage done" > /dev/null 2>&1   # the old code waits here for good
  el=$(($(date +%s) - t0))
  expect "a hanging sync: the shutdown still goes ahead" fired
  expect "after the sync wait, not the old 60 s or more" [ "$el" -le 10 ]
  expect "one sync, for the guard's own filesystem only" [ "$(cat "$STUB_DIR/sync_calls")" = "sync -f $AUTODL_GUARD_HOME/state2" ]
  expect "the log says the shutdown went ahead without sync" grep -q "sync did not finish" "$AUTODL_GUARD_HOME/guard.log"
  rm -f "$STUB_DIR/sync_hang"   # the stub still waiting in the background ends
  unset AUTODL_SYNC_WAIT
  teardown
}

t_shutdown_waits_for_a_sync_that_finishes() {
  setup
  echo 1 > "$STUB_DIR/sync_delay"   # a healthy disk that needs a second
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  expect "off-now when idle succeeds" quiet timeout -k 5 30 bash "$GUARD" off-now --reason "stage done"
  expect "the shutdown fired" fired
  expect "the sync had finished when off-now returned" [ -s "$STUB_DIR/sync_done" ]
  expect "one sync, for the guard's filesystem only" [ "$(cat "$STUB_DIR/sync_calls")" = "sync -f $AUTODL_GUARD_HOME/state2" ]
  expect "no complaint about sync" fails grep -q "sync did not finish" "$AUTODL_GUARD_HOME/guard.log"
  teardown
  setup
  touch "$STUB_DIR/sync_no_f"   # coreutils older than 8.24: no sync -f
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  quiet timeout -k 5 30 bash "$GUARD" off-now --reason "stage done"
  expect "without sync -f: falls back to one plain sync" [ "$(cat "$STUB_DIR/sync_calls")" = "$(printf 'sync -f %s\nsync' "$AUTODL_GUARD_HOME/state2")" ]
  expect "and the shutdown fires" fired
  teardown
}

# 0.8: the daemon reaches the shutdown through --idle 1s instead of a deadline
t_a_hanging_sync_does_not_block_revive_restart() {
  setup
  unset AUTODL_NO_DAEMON
  touch "$STUB_DIR/sync_hang"
  g arm --idle 1s --interval 1s > /dev/null
  wait_file "$STUB_DIR/sync_calls"   # the daemon now waits in flush_disks (60 s by default)
  local p1 t0 rc el
  p1="$(state daemon_pid)"
  t0="$(date +%s)"
  rc="$(rc_of g revive --restart)"
  el=$(($(date +%s) - t0))
  expect "revive --restart replaces a daemon waiting for a hanging sync" [ "$rc" = 0 ]
  expect "at once: the hanging sync does not hold the daemon lock" [ "$el" -le 10 ]   # the stub gives up after 25 s
  expect "with a new daemon" [ "$(state daemon_pid)" != "$p1" ]
  expect "that holds the daemon lock" status_has '^daemon_alive=1$'
  rm -f "$STUB_DIR/sync_hang"
  export AUTODL_NO_DAEMON=1
  teardown
}

t_a_hanging_sync_does_not_hold_the_callers_output() {
  setup
  export AUTODL_SYNC_WAIT=2
  g arm --idle 60m --deadline 60s --keep 60s --dry-run > /dev/null
  touch "$STUB_DIR/sync_hang"
  local t0 el out
  t0="$(date +%s)"
  out="$(timeout -k 5 30 bash "$GUARD" off-now --reason "stage done" 2>&1)"   # read the way an ssh channel reads it
  el=$(($(date +%s) - t0))
  expect "the caller gets its output back though sync still hangs" [ "$el" -le 10 ]
  expect "and the output says what happened" grep -q "dry-run" <<< "$out"
  rm -f "$STUB_DIR/sync_hang"
  unset AUTODL_SYNC_WAIT
  teardown
}

# 0.8: the daemon reaches the shutdown through --idle 1s instead of a deadline; an idle retry is re-checked, and the
# counters stay idle, so it is retried every interval
t_retried_shutdowns_do_not_pile_up_hanging_syncs() {
  setup
  unset AUTODL_NO_DAEMON
  export AUTODL_SYNC_WAIT=1
  touch "$STUB_DIR/sync_hang" "$STUB_DIR/shutdown_fail_always"
  g arm --idle 1s --interval 1s > /dev/null   # idle at the first check: the shutdown fails and is retried
  sleep 8
  local n
  n="$(sed -n '$=' "$STUB_DIR/shutdown_calls" 2> /dev/null)"
  expect "the failing shutdown is retried" [ "${n:-0}" -ge 2 ]
  expect "while one sync hangs, the retries start no other" [ "$(sed -n '$=' "$STUB_DIR/sync_calls")" = 1 ]
  expect "and the log says so" grep -q "an earlier sync is still running" "$AUTODL_GUARD_HOME/guard.log"
  rm -f "$STUB_DIR/sync_hang" "$STUB_DIR/shutdown_fail_always"
  unset AUTODL_SYNC_WAIT
  export AUTODL_NO_DAEMON=1
  teardown
}

# ---- group 13 (0.8): arm, schema 2 and uptime-based times (plan Task 3.1) ----
t8_arm_requires_idle() {
  setup
  use_clock 1000
  expect "arm without --idle refused" fails g arm --dry-run
  expect "a refused arm leaves no schema" [ ! -f "$AUTODL_GUARD_HOME/state2/schema" ]
  expect "--idle 0s refused" fails g arm --idle 0s --dry-run
  expect "--idle alone is enough" quiet g arm --idle 5m --dry-run
  expect "no deadline unless asked" [ "$(state deadline_up)" = 0 ]
  expect "no keep unless asked" [ "$(state keep_until_up)" = 0 ]
  teardown
}

t8_arm_writes_schema_2_and_uptimes() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --deadline 1h --keep 10m --dry-run
  expect "schema is 2" [ "$(state schema)" = 2 ]
  expect "armed_up is the uptime of the arm" [ "$(state armed_up)" = 1000 ]
  expect "the arm counts as activity" [ "$(state last_active_up)" = 1000 ]
  expect "idle_s" [ "$(state idle_s)" = 300 ]
  expect "deadline_up" [ "$(state deadline_up)" = 4600 ]
  expect "keep_until_up" [ "$(state keep_until_up)" = 1600 ]
  local da aa
  da="$(state deadline_at)"
  aa="$(state armed_at)"
  expect "deadline_at is a wall-clock time an hour on" [ "$((${da:-0} - ${aa:-0}))" = 3600 ]
  expect "the counters' baseline is taken at the arm" [ "$(state counters)" = "100000 1000000 0 5300" ]
  teardown
}

t8_arm_defaults_by_mode() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  expect "non-GPU: CPU threshold 3.0%" [ "$(state thr_cpu)" = 30 ]
  expect "non-GPU: no GPU probes" [ "$(state gpu_probes)" = 0 ]
  expect "disk threshold" [ "$(state thr_io)" = 500000 ]
  expect "network threshold" [ "$(state thr_net)" = 10000 ]
  expect "calibration: default" [ "$(state calib)" = default ]
  teardown
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  quiet g arm --idle 5m --dry-run
  expect "GPU: mode" [ "$(state mode)" = gpu ]
  expect "GPU: CPU threshold 5.0%" [ "$(state thr_cpu)" = 50 ]
  expect "GPU: GPU threshold 5%" [ "$(state thr_gpu)" = 5 ]
  expect "GPU: three probes per interval" [ "$(state gpu_probes)" = 3 ]
  teardown
}

t8_arm_threshold_options() {
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  quiet g arm --idle 5m --thr-cpu 2.5 --thr-io 1000000 --thr-net 20000 --thr-gpu 10 --gpu-probes 6 \
    --unreliable net,cpu --calib c1 --dry-run
  expect "--thr-cpu 2.5 is 25 tenths" [ "$(state thr_cpu)" = 25 ]
  expect "--thr-io" [ "$(state thr_io)" = 1000000 ]
  expect "--thr-net" [ "$(state thr_net)" = 20000 ]
  expect "--thr-gpu" [ "$(state thr_gpu)" = 10 ]
  expect "--gpu-probes" [ "$(state gpu_probes)" = 6 ]
  expect "--unreliable in a fixed order" [ "$(state unreliable)" = "cpu net" ]
  expect "--calib" [ "$(state calib)" = c1 ]
  teardown
  setup
  use_clock
  expect "--thr-cpu abc refused" fails g arm --idle 5m --thr-cpu abc --dry-run
  expect "--thr-cpu 101 refused" fails g arm --idle 5m --thr-cpu 101 --dry-run
  expect "--thr-cpu 2.55 refused" fails g arm --idle 5m --thr-cpu 2.55 --dry-run
  expect "--thr-io 05 refused" fails g arm --idle 5m --thr-io 05 --dry-run
  expect "--unreliable foo refused" fails g arm --idle 5m --unreliable foo --dry-run
  expect "--gpu-probes 21 refused" fails g arm --idle 5m --gpu-probes 21 --dry-run
  expect "--calib with a space refused" fails g arm --idle 5m --calib 'a b' --dry-run
  expect "nothing written by the refused arms" [ ! -f "$AUTODL_GUARD_HOME/state2/schema" ]
  teardown
}

t8_arm_refuses_without_uptime() {
  setup
  export AUTODL_TEST_UPTIME="$STUB_DIR/no-such-uptime"
  expect "arm refused without a clock" fails g arm --idle 5m --dry-run
  expect "no schema written" [ ! -f "$AUTODL_GUARD_HOME/state2/schema" ]
  expect "no boot written" [ ! -f "$AUTODL_GUARD_HOME/state2/armed_boot" ]
  teardown
}

# 0.7 kept its state in state/, 0.8 keeps its own in state2/ (schema 2): an arm never touches the old one
t8_arm_leaves_the_07_state_alone() {
  setup
  use_clock
  mkdir -p "$AUTODL_GUARD_HOME/state"
  local f before after
  for f in deadline keep_until last_busy busy_now post_job_keep_s util_signal armed_boot shutdown_pending; do
    echo 1 > "$AUTODL_GUARD_HOME/state/$f"
  done
  before="$(cd "$AUTODL_GUARD_HOME/state" && ls -A && sha256sum -- *)"
  quiet g arm --idle 5m --dry-run
  after="$(cd "$AUTODL_GUARD_HOME/state" && ls -A && sha256sum -- *)"
  expect "the 0.7 state directory is untouched" [ "$before" = "$after" ]
  expect "the 0.8 state is in state2" [ "$(state schema)" = 2 ]
  teardown
}

t8_status_reports_schema_and_needs_rearm() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  expect "status: schema=2" status_has '^schema=2$'
  expect "status: needs_rearm=0" status_has '^needs_rearm=0$'
  expect "status: armed_this_boot=1" status_has '^armed_this_boot=1$'
  rm -f "$AUTODL_GUARD_HOME/state2/schema"   # what a 0.7 state looks like to 0.8
  expect "0.7 state: needs_rearm=1" status_has '^needs_rearm=1$'
  expect "0.7 state: not armed" status_has '^armed_this_boot=0$'
  teardown
}

t8_values_of_another_boot_are_void() {
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  export AUTODL_BOOT_MARKER=boot2
  expect "another boot: not armed" status_has '^armed_this_boot=0$'
  expect "another boot: no decision in 10 idle minutes" [ "$(tick_for 600)" = 0 ]
  expect "another boot: nothing fired" not_fired
  export AUTODL_BOOT_MARKER=boot1
  teardown
}

# ---- group 13 (0.8): activity signals (plan Task 3.2) ----
sig() {  # sig NAME: the state of one signal as the last tick left it in state/signals
  local w
  for w in $(cat "$AUTODL_GUARD_HOME/state2/signals" 2> /dev/null); do
    case "$w" in "$1="*) w="${w#*=}"; printf '%s' "${w%%:*}"; return ;; esac
  done
}

t8_still_counters_are_idle() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --dry-run
  adv 60
  quiet g tick
  expect "cpu idle" [ "$(sig cpu)" = idle ]
  expect "io idle" [ "$(sig io)" = idle ]
  expect "net idle" [ "$(sig net)" = idle ]
  expect "gpu not applicable without a GPU" [ "$(sig gpu)" = na ]
  expect "no activity recorded" [ "$(state last_active_up)" = 1000 ]
  teardown
}

t8_cpu_threshold() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  adv 60; busy_cpu 1800000; quiet g tick
  expect "non-GPU: 3.0% of a core is in use" [ "$(sig cpu)" = busy ]
  adv 60; busy_cpu 1700000; quiet g tick
  expect "non-GPU: 2.8% of a core is idle" [ "$(sig cpu)" = idle ]
  teardown
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  quiet g arm --idle 5m --dry-run
  adv 60; busy_cpu 3000000; quiet g tick
  expect "GPU mode: 5.0% is in use" [ "$(sig cpu)" = busy ]
  adv 60; busy_cpu 2900000; quiet g tick
  expect "GPU mode: 4.8% is idle" [ "$(sig cpu)" = idle ]
  teardown
}

t8_io_and_net_thresholds() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  adv 60; busy_io 30000000; quiet g tick
  expect "disk 5e5 B/s is in use" [ "$(sig io)" = busy ]
  adv 60; busy_io 29000000; quiet g tick
  expect "disk 4.8e5 B/s is idle" [ "$(sig io)" = idle ]
  adv 60; busy_net 600000; quiet g tick
  expect "network 1e4 B/s is in use" [ "$(sig net)" = busy ]
  adv 60; busy_net 590000; quiet g tick
  expect "network 9.8e3 B/s is idle" [ "$(sig net)" = idle ]
  teardown
}

# steady background noise just under every threshold, all the time, is still idle: the shutdown comes after --idle
# (the test list of design 10)
t8_steady_noise_under_the_thresholds_still_idles() {
  local i rc=0
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  gpu_util 4
  quiet g arm --idle 3m --dry-run
  for i in 1 2 3; do
    adv 60; busy_cpu 2900000; busy_io 29000000; busy_net 590000
    g tick > /dev/null 2>&1
    rc=$?
    [ "$rc" = 10 ] && break
  done
  expect "GPU mode: CPU 4.8%, disk 4.8e5 B/s, network 9.8e3 B/s and GPU 4% for 3 minutes shut it down [rc=$rc]" [ "$rc" = 10 ]
  expect "at the third check, after --idle [i=$i]" [ "$i" = 3 ]
  expect "as idle" [ "$(fired_kind)" = idle ]
  teardown
  setup
  use_clock
  quiet g arm --idle 3m --dry-run
  for i in 1 2 3; do
    adv 60; busy_cpu 1700000; busy_io 29000000; busy_net 590000
    g tick > /dev/null 2>&1
    rc=$?
    [ "$rc" = 10 ] && break
  done
  expect "non-GPU: CPU 2.8%, disk and network just under theirs for 3 minutes shut it down [rc=$rc]" [ "$rc" = 10 ]
  expect "at the third check, after --idle [i=$i]" [ "$i" = 3 ]
  teardown
}

t8_unreadable_counter_is_unknown_and_busy() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --dry-run
  rm -f "$STUB_DIR/cg/cpu.stat"
  adv 60
  quiet g tick
  expect "cpu unknown" [ "$(sig cpu)" = unknown ]
  expect "unknown counts as activity" [ "$(state last_active_up)" = 1060 ]
  expect "the reason is recorded" grep -q 'cpu:unknown' "$AUTODL_GUARD_HOME/state2/active_why"
  teardown
}

t8_counter_going_back_is_unknown() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --dry-run
  echo 10 > "$STUB_DIR/c_cpu"
  write_counters
  adv 60
  quiet g tick
  expect "a counter that went back is unknown" [ "$(sig cpu)" = unknown ]
  adv 60
  quiet g tick
  expect "the next interval compares with the new baseline" [ "$(sig cpu)" = idle ]
  teardown
}

t8_stale_baseline_is_unknown() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --interval 60s --dry-run
  adv 241
  quiet g tick
  expect "a baseline older than 1.5 intervals plus 10 s is unknown" [ "$(sig cpu)" = unknown ]
  expect "and counts as activity" [ "$(state last_active_up)" = 1241 ]
  adv 60
  quiet g tick
  expect "with a fresh baseline it is idle again" [ "$(sig cpu)" = idle ]
  teardown
}

t8_gpu_signal() {
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  quiet g arm --idle 5m --dry-run
  gpu_util 5; adv 60; quiet g tick
  expect "GPU 5% is in use" [ "$(sig gpu)" = busy ]
  gpu_util 4; adv 60; quiet g tick
  expect "GPU 4% is idle" [ "$(sig gpu)" = idle ]
  printf '0\nfail\n0\n' > "$STUB_DIR/util_seq"
  adv 60; quiet g tick
  expect "one failed probe: unknown" [ "$(sig gpu)" = unknown ]
  : > "$STUB_DIR/util_seq"
  touch "$STUB_DIR/hang"
  adv 60
  expect "a hanging nvidia-smi does not hang tick" quiet timeout 30 env AUTODL_PROBE_TIMEOUT=1 bash "$GUARD" tick
  rm -f "$STUB_DIR/hang"
  expect "a hanging probe is unknown" [ "$(sig gpu)" = unknown ]
  teardown
}

# an intermittent GPU load: one probe of the interval at or above the threshold, on any GPU, is enough (design 5.2,
# the test list of design 10); a probe that answers for another number of GPUs counts as failed
t8_one_busy_probe_makes_the_gpu_in_use() {
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  gpu_util 0
  quiet g arm --idle 5m --gpu-probes 3 --dry-run
  printf '0\n9\n0\n' > "$STUB_DIR/util_seq"
  adv 60; quiet g tick
  expect "the middle probe of three at 9%: in use" [ "$(sig gpu)" = busy ]
  expect "and the busiest probe is what is kept" grep -q 'gpu=busy:9 ' "$AUTODL_GUARD_HOME/state2/signals"
  printf '9\n0\n0\n' > "$STUB_DIR/util_seq"
  adv 60; quiet g tick
  expect "the first probe busy: in use" [ "$(sig gpu)" = busy ]
  printf '0\n0\n9\n' > "$STUB_DIR/util_seq"
  adv 60; quiet g tick
  expect "the last probe busy: in use" [ "$(sig gpu)" = busy ]
  printf '0;0\n0;9\n0;0\n' > "$STUB_DIR/util_seq"
  adv 60; quiet g tick
  expect "the second of two GPUs busy in one probe: in use" [ "$(sig gpu)" = busy ]
  printf '0;0\n0\n0;0\n' > "$STUB_DIR/util_seq"
  adv 60; quiet g tick
  expect "a probe that answers for one GPU fewer: unknown" [ "$(sig gpu)" = unknown ]
  printf '0\n4\n0\n' > "$STUB_DIR/util_seq"
  adv 60; quiet g tick
  expect "every probe under the threshold: idle" [ "$(sig gpu)" = idle ]
  expect "the three probes of each check were used up" [ ! -s "$STUB_DIR/util_seq" ]
  teardown
}

t8_unreliable_signal_is_off() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --unreliable cpu --dry-run
  adv 60; busy_cpu 60000000; quiet g tick
  expect "an unreliable signal is off" [ "$(sig cpu)" = off ]
  expect "and not activity" [ "$(state last_active_up)" = 1000 ]
  teardown
}

t8_no_reliable_signal_never_idles() {
  setup
  use_clock
  quiet g arm --idle 2m --unreliable cpu,io,net --dry-run
  expect "no shutdown in 10 idle minutes" [ "$(tick_for 600)" = 0 ]
  expect "nothing fired" not_fired
  expect "status says why" status_has '^no_reliable_signal=1$'
  teardown
}

t8_daemon_spreads_gpu_probes() {
  setup
  echo 1 > "$STUB_DIR/gpu"
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --interval 3s --gpu-probes 3 --dry-run
  sleep 7
  local n gaps
  n="$(grep -c query "$STUB_DIR/nvsmi_times" 2> /dev/null)"
  expect "at least 5 probes in 7 s [$n]" [ "${n:-0}" -ge 5 ]
  expect "at most 8 probes in 7 s [$n]" [ "${n:-0}" -le 8 ]
  gaps="$(grep query "$STUB_DIR/nvsmi_times" | awk 'NR > 1 && $1 - p < 0.5 { bad++ } { p = $1 } END { print bad + 0 }')"
  expect "no two probes closer than 0.5 s [$gaps]" [ "$gaps" = 0 ]
  export AUTODL_NO_DAEMON=1
  teardown
}

t8_nogpu_daemon_never_calls_nvidia_smi() {
  setup
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --interval 2s --dry-run
  sleep 5
  expect "no utilization query without a GPU" fails grep -q query "$STUB_DIR/nvsmi_calls"
  export AUTODL_NO_DAEMON=1
  teardown
}

# ---- group 13 (0.8): the decision order of design 5.9, keep, deadline, off-when-done (plan Task 3.3) ----
fired_kind() { cat "$AUTODL_GUARD_HOME/state2/shutdown_kind" 2> /dev/null; }

t8_idle_shutdown_after_idle() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --grace 2m --dry-run
  expect "no shutdown before 5 idle minutes" [ "$(tick_for 299)" = 0 ]
  expect "nothing fired yet" not_fired
  expect "the 300th idle second fires" [ "$(tick_for 1)" = 10 ]
  expect "the reason says idle" reason_has idle
  expect "kind idle" [ "$(fired_kind)" = idle ]
  teardown
}

t8_activity_restarts_the_idle_count() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --dry-run
  quiet tick_for 240
  busy_cpu 60000000
  expect "activity at 240 s: in use" [ "$(tick_for 60)" = 0 ]
  expect "last activity at 1300" [ "$(state last_active_up)" = 1300 ]
  expect "no shutdown until 5 minutes after it" [ "$(tick_for 299)" = 0 ]
  expect "fires 300 s after the activity" [ "$(tick_for 1)" = 10 ]
  teardown
}

t8_a_registered_job_alone_is_not_in_use() {
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  quiet g run j1 -- sleep 600
  expect "a quiet job does not stop the idle shutdown" [ "$(tick_for 180)" = 10 ]
  expect "fired as idle" [ "$(fired_kind)" = idle ]
  pkill -f "sleep 600" 2> /dev/null
  teardown
}

t8_sessions_are_not_in_use() {
  setup
  use_clock
  echo "There is a screen on:
	123.work	(Detached)
1 Socket in /run/screen/S-root." > "$STUB_DIR/screen_out"
  quiet g arm --idle 2m --dry-run
  expect "an open screen session does not stop the idle shutdown" [ "$(tick_for 180)" = 10 ]
  teardown
}

t8_keep_holds_until_it_ends() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --keep 10m --dry-run
  expect "no shutdown while keep runs" [ "$(tick_for 600)" = 0 ]
  expect "keep counts as in use until its end" [ "$(state last_active_up)" = 1600 ]
  expect "no shutdown until --idle after the keep's end" [ "$(tick_for 299)" = 0 ]
  expect "then it fires" [ "$(tick_for 1)" = 10 ]
  teardown
}

t8_deadline_waits_for_activity_then_grace() {
  setup
  use_clock 1000
  quiet g arm --idle 60m --deadline 5m --grace 2m --dry-run
  local i
  for i in 1 2 3 4 5 6 7 8; do busy_cpu 60000000; adv 60; quiet g tick; done
  expect "past the deadline but in use: no shutdown" not_fired
  expect "still no shutdown 1 idle minute later" [ "$(tick_for 60)" = 0 ]
  expect "fires after the grace" [ "$(tick_for 60)" = 10 ]
  expect "kind deadline" [ "$(fired_kind)" = deadline ]
  teardown
}

t8_deadline_voids_keep() {
  setup
  use_clock
  quiet g arm --idle 60m --keep 30m --deadline 5m --grace 1m --dry-run
  expect "keep ends at the deadline: no shutdown before grace after it" [ "$(tick_for 359)" = 0 ]
  expect "fires 1 idle minute after the deadline" [ "$(tick_for 1)" = 10 ]
  expect "kind deadline" [ "$(fired_kind)" = deadline ]
  teardown
}

t8_after_the_deadline_run_keep_deadline_refused() {
  setup
  use_clock
  quiet g arm --idle 60m --deadline 2m --grace 10m --dry-run
  quiet tick_for 180
  expect "run refused after the deadline" [ "$(rc_of g run j1 -- true)" = 7 ]
  expect "keep refused after the deadline" [ "$(rc_of g keep 30m)" = 7 ]
  expect "deadline cannot be moved once reached" [ "$(rc_of g deadline 1h)" = 7 ]
  expect "no job registered" [ ! -d "$AUTODL_GUARD_HOME/jobs/j1" ]
  teardown
}

t8_after_the_deadline_a_rearm_sets_a_new_one() {  # the one way to stay on past a deadline that has come: the
  # whole configuration again, on the user's word (reference/ssh.md, "keep、最晚关机、跑完就关、安静期")
  setup
  use_clock
  quiet g arm --idle 60m --deadline 2m --grace 10m --dry-run
  quiet tick_for 180
  expect "past the deadline: keep is refused" [ "$(rc_of g keep 30m)" = 7 ]
  expect "a rearm is accepted" [ "$(rc_of g arm --idle 60m --deadline 2h --grace 10m --dry-run --rearm)" = 0 ]
  expect "the deadline is ahead again" status_has '^past_deadline=0$'
  expect "and counts from the rearm" status_has '^deadline_in_s=7200$'
  expect "the old deadline's grace no longer applies" [ "$(tick_for 660)" = 0 ]
  expect "nothing fired" not_fired
  expect "keep is accepted again" [ "$(rc_of g keep 30m)" = 0 ]
  expect "and run" [ "$(rc_of g run j1 -- true)" = 0 ]
  teardown
}

t8_off_when_done_waits_for_jobs_then_grace() {
  setup
  use_clock
  quiet g arm --idle 60m --grace 1m --dry-run
  quiet g run j1 --then-off -- sleep 3
  expect "the job runs: grace does not apply yet" [ "$(tick_for 120)" = 0 ]
  wait_job j1
  expect "the job ended: fires after the grace" [ "$(tick_for 60)" = 10 ]
  expect "kind when-done" [ "$(fired_kind)" = when-done ]
  teardown
}

t8_off_when_done_does_not_protect_an_idle_job() {
  setup
  use_clock
  quiet g arm --idle 3m --grace 1m --dry-run
  quiet g run j1 --then-off -- sleep 600
  expect "an idle job is still shut down after idle_s" [ "$(tick_for 240)" = 10 ]
  expect "kind idle" [ "$(fired_kind)" = idle ]
  pkill -f "sleep 600" 2> /dev/null
  teardown
}

t8_pending_retry_rechecks_activity() {
  setup
  use_clock
  quiet g arm --idle 2m
  echo 1 > "$STUB_DIR/shutdown_fail"   # the shutdown stub fails while this exists
  expect "idle: a shutdown is attempted" [ "$(tick_for 180)" = 10 ]
  expect "and left pending" [ "$(state shutdown_pending)" = 1 ]
  busy_cpu 60000000
  adv 60
  quiet g tick
  expect "activity again: the pending idle shutdown is cancelled" [ "$(state shutdown_pending)" = 0 ]
  rm -f "$STUB_DIR/shutdown_fail"
  teardown
}

t8_incomplete_arm_decides_nothing() {
  setup
  use_clock
  quiet g arm --idle 2m --deadline 1m --grace 1m --dry-run
  echo 1 > "$AUTODL_GUARD_HOME/state2/arm_incomplete"
  expect "an arm cut short: no decision at all" [ "$(tick_for 600)" = 0 ]
  expect "nothing fired" not_fired
  teardown
}

t8_wall_clock_jumps_do_not_matter() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  quiet tick_for 120
  expect "a day forward on the wall clock changes nothing" [ "$(AUTODL_TEST_EPOCH_SHIFT=86400 tick_for 60)" = 0 ]
  expect "a day back changes nothing" [ "$(AUTODL_TEST_EPOCH_SHIFT=-86400 tick_for 60)" = 0 ]
  expect "still fires at 300 idle seconds" [ "$(tick_for 60)" = 10 ]
  teardown
}

t8_uptime_unreadable_is_in_use() {
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  quiet tick_for 60
  export AUTODL_TEST_UPTIME="$STUB_DIR/no-such-uptime"
  expect "no clock: no shutdown" [ "$(tick_rc)" = 0 ]
  expect "status says why" status_has '^active_why=uptime:unknown$'
  teardown
}

t8_old_state_decides_nothing_but_retries_pending() {
  setup
  use_clock
  quiet g arm --idle 2m
  rm -f "$AUTODL_GUARD_HOME/state2/schema"
  expect "0.7 state: no new decision" [ "$(tick_for 600)" = 0 ]
  echo 1 > "$AUTODL_GUARD_HOME/state2/shutdown_pending"
  echo idle > "$AUTODL_GUARD_HOME/state2/shutdown_kind"
  expect "a shutdown already issued is still retried" [ "$(tick_rc)" = 10 ]
  expect "the shutdown stub was called" [ -s "$STUB_DIR/shutdown_calls" ]
  teardown
}

t8_rearm_resets_the_baseline_and_the_idle_count() {
  setup
  use_clock 1000
  quiet g arm --idle 5m --dry-run
  quiet tick_for 240
  busy_cpu 5000000
  quiet g arm --idle 5m --rearm --dry-run
  expect "rearm takes a fresh baseline" [ "$(state counters)" = "124000 6000000 0 5300" ]
  expect "rearm counts as activity" [ "$(state last_active_up)" = 1240 ]
  expect "no shutdown until 5 minutes after the rearm" [ "$(tick_for 299)" = 0 ]
  expect "then it fires" [ "$(tick_for 1)" = 10 ]
  teardown
}

t8_a_rearm_leaves_the_jobs_and_their_quiet_periods_and_drops_keep_and_off_when_done() {  # the configuration is
  # replaced whole; the registered jobs are no part of it (reference/ssh.md, "keep、最晚关机、跑完就关、安静期")
  setup
  use_clock
  quiet g arm --idle 5m --keep 30m --dry-run
  quiet g run j --quiet 20m -- sleep 903
  expect "before: a keep" status_has '^keep_in_s=1800$'
  quiet tick_for 60
  expect "the rearm is accepted" quiet g arm --idle 5m --rearm --dry-run
  expect "the keep is gone" status_has '^keep_in_s=$'
  expect "the job is still registered and running" status_has '^job.j=running|'
  expect "its quiet period still ends when it was declared to" status_has '^quiet.j=1140$'
  quiet g off-when-done --reason test
  expect "before: off-when-done" status_has '^off_when_done=1$'
  expect "another rearm is accepted" quiet g arm --idle 5m --rearm --dry-run
  expect "off-when-done is gone" status_has '^off_when_done=0$'
  expect "the quiet period still holds: no shutdown in 6 silent minutes" [ "$(tick_for 360)" = 0 ]
  expect "nothing fired" not_fired
  pkill -f "sleep 903" 2> /dev/null
  wait_job j
  teardown
}

# ---- group 13 (0.8): shutdowns already issued, and a GPU left out on purpose ----
t8_gpu_probes_0_needs_the_gpu_marked_unreliable() {
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  expect "GPU mode: --gpu-probes 0 alone refused" fails g arm --idle 5m --gpu-probes 0 --dry-run
  expect "nothing written" [ ! -f "$AUTODL_GUARD_HOME/state2/schema" ]
  expect "with --unreliable gpu it is accepted" quiet g arm --idle 5m --gpu-probes 0 --unreliable gpu --dry-run
  : > "$STUB_DIR/nvsmi_calls"
  adv 60
  quiet g tick
  expect "the GPU is off" [ "$(sig gpu)" = off ]
  expect "and never queried" fails grep -q query "$STUB_DIR/nvsmi_calls"
  teardown
}

t8_a_pending_shutdown_of_another_boot_is_not_retried() {
  setup
  use_clock
  quiet g arm --idle 2m
  echo 1 > "$STUB_DIR/shutdown_fail"   # the shutdown command fails once
  expect "boot1: idle, a shutdown is attempted" [ "$(tick_for 180)" = 10 ]
  expect "and left pending" [ "$(state shutdown_pending)" = 1 ]
  export AUTODL_BOOT_MARKER=boot2   # started again: the flag is the last boot's
  : > "$STUB_DIR/shutdown_calls"
  expect "boot2: the last boot's shutdown is not retried" [ "$(tick_rc)" = 0 ]
  rm -f "$AUTODL_GUARD_HOME/state2/shutdown_boot"   # as 0.7 left it: the arm's boot stands in
  expect "nor one without its boot noted" [ "$(tick_rc)" = 0 ]
  expect "the shutdown command was not called" [ ! -s "$STUB_DIR/shutdown_calls" ]
  export AUTODL_BOOT_MARKER=boot1
  teardown
}

t8_a_forced_pending_shutdown_is_retried_while_in_use() {
  setup
  use_clock
  quiet g arm --idle 60m
  echo 1 > "$STUB_DIR/shutdown_fail"
  quiet g off-now --force --reason test
  expect "the forced shutdown failed and is pending" [ "$(state shutdown_pending)" = 1 ]
  expect "kind forced" [ "$(state shutdown_kind)" = forced ]
  busy_cpu 60000000
  adv 60
  : > "$STUB_DIR/shutdown_calls"
  expect "in use, yet the forced shutdown is retried" [ "$(tick_rc)" = 10 ]
  expect "the shutdown command was called again" [ -s "$STUB_DIR/shutdown_calls" ]
  teardown
}

# ---- group 13 (0.8): after the Codex review of the Phase 3 plan (docs/reviews/2026-09-30-codex-phase3-plan.md) ----
# design 5.4: past the deadline the idle time needed drops from --idle to grace, counted from the last activity,
# so an instance idle since long before the deadline is shut down at the deadline itself (review item 1, kept)
t8_idle_before_the_deadline_counts_toward_grace() {
  setup
  use_clock
  quiet g arm --idle 60m --deadline 5m --grace 2m --dry-run
  expect "idle, but before the deadline: no shutdown" [ "$(tick_for 299)" = 0 ]
  expect "at the deadline, idle for 5 minutes already: fires" [ "$(tick_for 1)" = 10 ]
  expect "kind deadline" [ "$(fired_kind)" = deadline ]
  teardown
}

# a check without the clock counts as in use; when the clock is back, that check restarts the idle time (item 2)
t8_a_lost_clock_restarts_the_idle_time_when_it_returns() {
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  quiet tick_for 60
  export AUTODL_TEST_UPTIME="$STUB_DIR/no-such-uptime"
  expect "no clock: in use" [ "$(tick_rc)" = 0 ]
  export AUTODL_TEST_UPTIME="$STUB_DIR/uptime"
  expect "the clock is back a minute later: that check counts as in use too" [ "$(tick_for 60)" = 0 ]
  expect "the idle time restarts there" [ "$(state last_active_up)" = 1120 ]
  expect "no shutdown until --idle after it" [ "$(tick_for 119)" = 0 ]
  expect "then it fires" [ "$(tick_for 1)" = 10 ]
  teardown
}

# a shutdown request that cannot be recorded in full leaves no pending flag, not even an old one (item 5)
t8_a_failed_shutdown_request_never_revives_an_old_pending_flag() {
  setup
  use_clock
  quiet g arm --idle 2m
  echo 1 > "$STUB_DIR/shutdown_fail"
  expect "boot1: a shutdown attempted and left pending" [ "$(tick_for 180)" = 10 ]
  export AUTODL_BOOT_MARKER=boot2   # started again; not armed in this boot
  : > "$STUB_DIR/shutdown_calls"
  expect "an off-now that cannot record its request" [ "$(AUTODL_TEST_FAIL_PUT=shutdown_reason rc_of g off-now --reason x)" = 2 ]
  expect "leaves no pending flag" [ "$(state shutdown_pending)" = 0 ]
  expect "so a check retries nothing" [ "$(tick_rc)" = 0 ]
  expect "and the shutdown command was not called" [ ! -s "$STUB_DIR/shutdown_calls" ]
  export AUTODL_BOOT_MARKER=boot1
  teardown
}

# off-when-done cancels a keep that still runs: it was in use until that moment, so grace counts from there (item 9)
t8_off_when_done_ends_a_running_keep_now() {
  setup
  use_clock
  quiet g arm --idle 60m --keep 30m --grace 1m --dry-run
  quiet tick_for 120
  adv 30
  quiet g off-when-done --reason done
  expect "the keep counted as in use until the command" [ "$(state last_active_up)" = 1150 ]
  expect "no shutdown before grace since then" [ "$(tick_for 59)" = 0 ]
  expect "then it fires" [ "$(tick_for 1)" = 10 ]
  expect "kind when-done" [ "$(fired_kind)" = when-done ]
  teardown
}

# with no reliable signal nothing shows idleness: a pending automatic shutdown is cancelled, not retried; an
# off-now the AI asked for is still retried (item 10)
t8_no_reliable_signal_cancels_an_automatic_retry() {
  setup
  use_clock
  quiet g arm --idle 1m
  echo 1 > "$STUB_DIR/shutdown_fail"
  expect "an idle shutdown is attempted and fails" [ "$(tick_for 60)" = 10 ]
  printf 'cpu io net' > "$AUTODL_GUARD_HOME/state2/unreliable"   # as if no signal were reliable in this mode
  expect "no reliable signal: the idle retry is cancelled" [ "$(tick_for 60)" = 0 ]
  expect "pending cleared" [ "$(state shutdown_pending)" = 0 ]
  teardown
  setup
  use_clock
  quiet g arm --idle 60m
  echo 1 > "$STUB_DIR/shutdown_fail"
  quiet g off-now --reason done
  expect "the off-now shutdown failed and is pending" [ "$(state shutdown_pending)" = 1 ]
  printf 'cpu io net' > "$AUTODL_GUARD_HOME/state2/unreliable"
  expect "an off-now the AI asked for is still retried" [ "$(tick_for 60)" = 10 ]
  teardown
}

# a baseline more than 1.5 intervals plus 10 s old is too old: after a check that could not store its baseline,
# the next one would otherwise average over two intervals and could water a burst down (item 11)
t8_a_baseline_two_intervals_old_is_unknown() {
  setup
  use_clock
  quiet g arm --idle 5m --interval 60s --dry-run
  adv 100
  quiet g tick
  expect "100 s (1.5 intervals plus 10 s) is still usable" [ "$(sig cpu)" = idle ]
  adv 101
  quiet g tick
  expect "101 s is too old" [ "$(sig cpu)" = unknown ]
  teardown
}

# the calibration's coverage as ctl passes it: default thresholds, a calibration of idle only, or one that also
# checked light work (item 12)
t8_calibration_coverage_is_reported() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  expect "no calibration: the defaults" status_has '^calib_coverage=default$'
  quiet g arm --idle 5m --calib c1 --rearm --dry-run
  expect "a calibration of idle only: coverage unverified" status_has '^calib_coverage=unverified$'
  quiet g arm --idle 5m --calib c2 --calib-coverage verified --rearm --dry-run
  expect "one that also checked work: verified" status_has '^calib_coverage=verified$'
  expect "--calib-coverage without --calib refused" fails g arm --idle 5m --calib-coverage verified --rearm --dry-run
  expect "an unknown coverage refused" fails g arm --idle 5m --calib c3 --calib-coverage maybe --rearm --dry-run
  teardown
}

# two checks less than 1 s apart give no rate: unknown, so in use (item 13)
t8_two_checks_under_a_second_apart_are_unknown() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  adv 60
  quiet g tick
  expect "idle after a minute" [ "$(sig cpu)" = idle ]
  quiet g tick   # the clock has not moved
  expect "a check less than 1 s after the last one is unknown" [ "$(sig cpu)" = unknown ]
  expect "and counts as in use" [ "$(state last_active_up)" = 1060 ]
  teardown
}

# a counter file that does not look exactly as expected is unknown, so in use (item 13)
t8_malformed_counters_are_unknown() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  printf 'usage_usec 12x\n' > "$STUB_DIR/cg/cpu.stat"
  printf '9:0 rbytes=0\n' > "$STUB_DIR/cg/io.stat"   # no wbytes
  printf 'Inter-|\n face |\n  eth0: 1 2 3\n' > "$STUB_DIR/net_dev"   # too few fields
  adv 60
  quiet g tick
  expect "cpu unknown" [ "$(sig cpu)" = unknown ]
  expect "io unknown" [ "$(sig io)" = unknown ]
  expect "net unknown" [ "$(sig net)" = unknown ]
  expect "in use" [ "$(state last_active_up)" = 1060 ]
  teardown
}

# a real 0.7.1 arm (the frozen script in tests/fixtures) keeps its state in state/, which 0.8 never reads: 0.8 is
# not armed, decides nothing, and its commands ask for an arm (item 13)
t8_a_real_07_arm_is_not_taken_for_an_08_one() {
  setup
  use_clock
  quiet bash "$HERE/fixtures/autodl_guard_0.7.1.sh" arm --deadline 1m --keep 0s --grace 0s
  expect "0.7 wrote its own state" [ -f "$AUTODL_GUARD_HOME/state/armed_boot" ]
  expect "0.8 is not armed" status_has '^armed_this_boot=0$'
  expect "no decision, even past the 0.7 deadline" [ "$(tick_for 180)" = 0 ]
  expect "the shutdown command was not called" [ ! -f "$STUB_DIR/shutdown_calls" ]
  expect "0.8 commands ask for an arm" fails g keep 5m
  teardown
}

# a daemon that starts with a shutdown of this boot pending, or past the deadline, checks at once instead of
# after its first interval (item 14)
t8_a_daemon_starting_with_a_pending_shutdown_checks_at_once() {
  setup
  quiet g arm --idle 60m --interval 1h
  echo 1 > "$STUB_DIR/shutdown_fail"
  quiet g off-now --force --reason test   # the command fails: a forced shutdown is pending
  expect "a forced shutdown is pending" [ "$(state shutdown_pending)" = 1 ]
  unset AUTODL_NO_DAEMON
  quiet g revive
  local i n=0
  for i in $(seq 1 20); do
    n="$(sed -n '$=' "$STUB_DIR/shutdown_calls" 2> /dev/null)"
    [ "${n:-0}" -ge 2 ] && break
    sleep 0.5
  done
  expect "the new daemon retried it at once, not an hour later [$n]" [ "${n:-0}" -ge 2 ]
  export AUTODL_NO_DAEMON=1
  teardown
}

t8_a_daemon_starting_past_the_deadline_checks_at_once() {
  setup
  use_clock
  quiet g arm --idle 60m --deadline 1m --grace 0s --interval 1h --dry-run
  adv 90
  unset AUTODL_NO_DAEMON
  quiet g revive
  wait_file "$AUTODL_GUARD_HOME/state2/dry_run_fired"
  expect "the new daemon checked at once and, idle past the deadline, fired" fired
  expect "as a deadline shutdown" [ "$(fired_kind)" = deadline ]
  export AUTODL_NO_DAEMON=1
  teardown
}

# a deadline within the daemon's interval ends that interval: the daemon checks at the deadline, not an interval
# later (item 13)
t8_the_daemon_wakes_at_the_deadline() {
  setup
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --deadline 3s --interval 60s --dry-run
  wait_file "$AUTODL_GUARD_HOME/state2/signals"
  expect "the first check came at the deadline, not a minute later" [ -f "$AUTODL_GUARD_HOME/state2/signals" ]
  export AUTODL_NO_DAEMON=1
  teardown
}

# ---- group 13 (0.8): after round 2 of the Codex review of the Phase 3 plan ----
# the staleness limit, 1.5 intervals plus at most 10 s and at most 0.4 intervals, stays under two intervals for
# every interval, so a baseline two intervals old is always too old (round 2, item 11)
t8_a_baseline_two_intervals_old_is_unknown_at_any_interval() {
  local i
  for i in 1 10 20; do
    setup
    use_clock
    quiet g arm --idle 60m --interval "${i}s" --dry-run
    adv "$i"
    quiet g tick
    expect "interval ${i}s: one interval old is usable" [ "$(sig cpu)" = idle ]
    adv $((2 * i))
    quiet g tick
    expect "interval ${i}s: two intervals old is too old" [ "$(sig cpu)" = unknown ]
    teardown
  done
}

# a check that could not store its baseline counts as in use, and the next one must not average a burst away over
# two intervals (round 2, item 11)
t8_a_burst_after_an_unstored_baseline_is_not_watered_down() {
  setup
  use_clock
  quiet g arm --idle 60m --interval 10s --dry-run
  adv 10
  AUTODL_TEST_FAIL_PUT=counters quiet g tick   # this check cannot store its baseline
  busy_cpu 300000                              # 3% of a core over 10 s, 1.5% over 20 s
  adv 10
  quiet g tick
  expect "the next check finds the baseline too old: unknown" [ "$(sig cpu)" = unknown ]
  expect "so in use" [ "$(state last_active_up)" = 1020 ]
  teardown
}

# a committed off-now (kind now) that has to be retried does not wait for a keep: the AI asked to shut down, and
# the keep may still be set if the command died between its commit point and clearing the keep (round 2, item 4)
t8_a_committed_off_now_ignores_keep_on_retry() {
  setup
  use_clock
  quiet g arm --idle 60m
  echo 1 > "$STUB_DIR/shutdown_fail"
  quiet g off-now --reason done
  expect "the off-now shutdown failed and is pending" [ "$(state shutdown_pending)" = 1 ]
  printf '%s' 4000 > "$AUTODL_GUARD_HOME/state2/keep_until_up"   # as if it died before clearing the keep
  expect "the retry goes ahead despite the keep" [ "$(tick_for 60)" = 10 ]
  teardown
}

# an off-now that cannot record its shutdown leaves the keep as it was: nothing is changed before the commit point
t8_an_off_now_that_cannot_commit_keeps_the_keep() {
  setup
  use_clock
  quiet g arm --idle 60m --keep 30m
  expect "an off-now whose pending flag cannot be written" [ "$(AUTODL_TEST_FAIL_PUT=shutdown_pending rc_of g off-now --reason x)" = 2 ]
  expect "leaves the keep as it was" [ "$(state keep_until_up)" = 2800 ]
  teardown
}

# a daemon that starts with a forced shutdown pending retries it at once, without first waiting for GPU probes
# that may hang (round 2, item 14)
t8_a_forced_retry_at_daemon_start_waits_for_no_probe() {
  setup
  echo 1 > "$STUB_DIR/gpu"
  quiet g arm --idle 60m --interval 1h --gpu-probes 20 --mode gpu
  echo 1 > "$STUB_DIR/shutdown_fail"
  quiet g off-now --force --reason test   # the command fails: a forced shutdown is pending
  touch "$STUB_DIR/hang"                   # every probe would now hang until its time limit
  export AUTODL_PROBE_TIMEOUT=10
  unset AUTODL_NO_DAEMON
  quiet g revive
  local i n=0
  for i in $(seq 1 16); do
    n="$(sed -n '$=' "$STUB_DIR/shutdown_calls" 2> /dev/null)"
    [ "${n:-0}" -ge 2 ] && break
    sleep 0.5
  done
  expect "retried within seconds, not after 20 probes of 10 s [$n]" [ "${n:-0}" -ge 2 ]
  rm -f "$STUB_DIR/hang"
  unset AUTODL_PROBE_TIMEOUT
  export AUTODL_NO_DAEMON=1
  teardown
}

# the interval that the deadline ends: no check before it, one at it, and in GPU mode the probes spread over the
# shortened window (round 2, item 13)
# the interval that the deadline ends: one check at the deadline, none before it, and in GPU mode the probes spread
# over the shortened window; the check's time, read afterwards from the file, is compared with the deadline itself,
# so a slow test machine can only make it look later, never earlier (rounds 3 and 4, item 7)
t8_the_daemon_checks_at_the_deadline_not_before() {
  setup
  echo 1 > "$STUB_DIR/gpu"
  unset AUTODL_NO_DAEMON
  local t1
  quiet g arm --idle 60m --deadline 4s --interval 60s --gpu-probes 2 --mode gpu --dry-run
  wait_file "$AUTODL_GUARD_HOME/state2/signals"
  expect "a check at the deadline" [ -f "$AUTODL_GUARD_HOME/state2/signals" ]
  t1="$(date -r "$AUTODL_GUARD_HOME/state2/signals" +%s.%N)"
  # deadline_at is the whole second of the arm plus 4 s; the deadline itself falls within the second before it
  expect "not before it" awk -v a="$(state deadline_at)" -v b="$t1" 'BEGIN { exit !(b >= a - 1.1) }'
  expect "both probes fitted into the shortened window" [ "$(sig gpu)" = idle ]
  expect "and were made [$(grep -c query "$STUB_DIR/nvsmi_calls")]" [ "$(grep -c query "$STUB_DIR/nvsmi_calls")" -ge 2 ]
  export AUTODL_NO_DAEMON=1
  teardown
}

# a pending retry is neither made nor cancelled by a check whose rates came too soon or too late (a slow check
# before it, a long wait for the lock): the shutdown stays pending and the next check decides
# a check whose rates came too late is unknown, so in use (design 5.9): a pending shutdown that is not forced is
# cancelled and the idle time restarts there; whatever happened in that uncertain stretch is never assumed idle
# (round 3 of the Codex review of the Phase 3 plan, item 1: an earlier rule that kept the retry lost that stretch)
t8_a_check_that_came_too_late_cancels_a_retry() {
  setup
  use_clock
  quiet g arm --idle 1m
  echo 1 > "$STUB_DIR/shutdown_fail"
  expect "an idle shutdown is attempted and fails" [ "$(tick_for 60)" = 10 ]
  : > "$STUB_DIR/shutdown_calls"
  adv 300   # far past the staleness limit of a 60 s interval
  expect "a check with no usable rate: no retry" [ "$(tick_rc)" = 0 ]
  expect "the pending shutdown is cancelled" [ "$(state shutdown_pending)" = 0 ]
  expect "and the idle time restarts at that check" [ "$(state last_active_up)" = 1360 ]
  expect "no shutdown until --idle after it" [ "$(tick_for 59)" = 0 ]
  expect "then a new one" [ "$(tick_for 1)" = 10 ]
  expect "the shutdown command was called once more" [ "$(wc -l < "$STUB_DIR/shutdown_calls")" = 1 ]
  teardown
}

# a deadline set while the daemon sleeps through a long interval is seen within a few seconds: the daemon rereads
# it while it sleeps (round 3 of the Codex review of the Phase 3 plan, item 2)
t8_an_earlier_deadline_set_mid_window_wakes_the_daemon() {
  setup
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --interval 60s --dry-run
  sleep 1
  quiet g deadline 3s
  local i
  for i in $(seq 1 24); do
    [ -f "$AUTODL_GUARD_HOME/state2/signals" ] && break
    sleep 0.5
  done
  expect "the daemon checked at the new deadline, not at the end of its minute" [ -f "$AUTODL_GUARD_HOME/state2/signals" ]
  export AUTODL_NO_DAEMON=1
  teardown
}

# a probe's time limit never reaches past its window, so a hanging nvidia-smi cannot push the check late: the limits
# the daemon gives its probes, logged by a stand-in for timeout, all end by the deadline (round 3, item 13)
t8_a_probe_never_runs_past_its_window() {
  setup
  echo 1 > "$STUB_DIR/gpu"
  printf '#!/bin/sh\nread -r up _ < /proc/uptime\necho "$up $1" >> "%s/timeouts"\nexec timeout "$@"\n' "$STUB_DIR" \
    > "$STUB_DIR/timeout-log"
  chmod +x "$STUB_DIR/timeout-log"
  export AUTODL_TIMEOUT_CMD="$STUB_DIR/timeout-log"
  touch "$STUB_DIR/hang"   # every probe runs to its limit (--mode gpu keeps nvidia-smi out of the arm itself)
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --deadline 2s --interval 60s --gpu-probes 3 --mode gpu --dry-run
  wait_file "$AUTODL_GUARD_HOME/state2/signals"
  local dl late
  dl="$(state deadline_up)"
  # the probes that started before the deadline (the next window's, after the check, are not bounded by it)
  late="$(awk -v d="$dl" '$1 < d && $1 + $2 > d + 0.1 { n++ } END { print n + 0 }' "$STUB_DIR/timeouts")"
  expect "every probe of the window ends by the deadline [$(tr '\n' ';' < "$STUB_DIR/timeouts")]" [ "$late" = 0 ]
  expect "at least one probe was made in it" awk -v d="$dl" '$1 < d { f = 1 } END { exit !f }' "$STUB_DIR/timeouts"
  expect "a probe late in the window got less than its 1 s share" awk '$2 < 1 { f = 1 } END { exit !f }' "$STUB_DIR/timeouts"
  expect "hanging probes leave the GPU unknown" [ "$(sig gpu)" = unknown ]
  rm -f "$STUB_DIR/hang"
  unset AUTODL_TIMEOUT_CMD
  export AUTODL_NO_DAEMON=1
  teardown
}

# timeout (coreutils) is required like flock: without it a probe that hangs would hold the daemon for good, and the
# limits above would not hold (round 3, item 5)
t8_timeout_is_required() {
  setup
  use_clock
  local out
  out="$(AUTODL_TIMEOUT_CMD=no-such-timeout bash "$GUARD" arm --idle 5m --dry-run 2>&1)"
  expect "without timeout arm refuses" [ "$?" != 0 ]
  expect "and says why" bash -c 'printf "%s" "$1" | grep -q timeout' _ "$out"
  expect "and arms nothing" [ ! -f "$AUTODL_GUARD_HOME/state2/armed_boot" ]
  teardown
}

# in GPU mode too, a deadline set while the daemon waits between two probes of a long interval is seen within a few
# seconds: the waits between probes are cut into steps of at most 5 s that reread it (round 4, item 1)
t8_an_earlier_deadline_between_probes_wakes_the_daemon() {
  setup
  echo 1 > "$STUB_DIR/gpu"
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --interval 1h --gpu-probes 20 --mode gpu --dry-run   # a probe every 180 s
  sleep 1
  quiet g deadline 3s
  local i
  for i in $(seq 1 24); do
    [ -f "$AUTODL_GUARD_HOME/state2/signals" ] && break
    sleep 0.5
  done
  expect "the daemon checked at the new deadline, not at its next probe" [ -f "$AUTODL_GUARD_HOME/state2/signals" ]
  export AUTODL_NO_DAEMON=1
  teardown
}

# a rearm while the daemon is in the middle of a window restarts it, so no check mixes the old window's GPU probes
# with the new baseline, thresholds and interval (round 4, item 2)
t8_a_rearm_restarts_the_running_daemon() {
  setup
  echo 1 > "$STUB_DIR/gpu"
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --interval 60s --gpu-probes 3 --mode gpu --dry-run
  local p1 p2
  p1="$(state daemon_pid)"
  sleep 1
  quiet g arm --rearm --idle 60m --interval 30s --gpu-probes 3 --mode gpu --dry-run
  p2="$(state daemon_pid)"
  expect "the rearm recorded a daemon" [ -n "$p2" ]
  expect "a new one" [ "${p2:-x}" != "${p1:-x}" ]
  expect "and the old one is gone" fails kill -0 "$p1"
  expect "the daemon lock is held" status_has '^daemon_alive=1$'
  export AUTODL_NO_DAEMON=1
  teardown
}

# sample makes GPU probes, so it needs timeout too; without GPU samples it does not (round 4, item 3)
t8_sample_needs_timeout_for_gpu_samples() {
  setup
  expect "sample with GPU samples refuses without timeout" \
    fails env AUTODL_TIMEOUT_CMD=no-such-timeout bash "$GUARD" sample --every 1s --count 1
  expect "and says why" bash -c 'AUTODL_TIMEOUT_CMD=no-such-timeout bash "$1" sample --every 1s --count 1 2>&1 | grep -q timeout' _ "$GUARD"
  expect "without GPU samples it runs" quiet env AUTODL_TIMEOUT_CMD=no-such-timeout bash "$GUARD" sample --every 1s --count 1 --gpu-samples 0
  teardown
}

# a check whose window began before the last arm is discarded: no decision, and neither the baseline nor the
# signals change (the old daemon can take the state lock between the arm's unlock and its restart; round 5, item 2)
t8_a_window_from_before_the_arm_is_discarded() {
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  quiet tick_for 60
  local c s
  c="$(state counters)" s="$(state signals)"
  adv 60
  expect "a check of a window that began under another arm" [ "$(AUTODL_TEST_WINDOW_GEN=stale tick_rc)" = 0 ]
  expect "leaves the baseline alone" [ "$(state counters)" = "$c" ]
  expect "and the signals" [ "$(state signals)" = "$s" ]
  expect "and the last activity" [ "$(state last_active_up)" = 1000 ]
  expect "a check of this arm's window then decides as usual" [ "$(tick_rc)" = 10 ]
  teardown
}

# a window shortened by a deadline set meanwhile spreads the probes it still has room for over what is left (the
# probes it has no room for are not failures), so quick idle probes leave the GPU idle and the deadline shutdown
# comes at the deadline instead of an interval later (round 5, new item 1)
t8_a_shortened_window_spreads_its_remaining_probes() {
  setup
  echo 1 > "$STUB_DIR/gpu"
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --grace 1s --interval 1h --gpu-probes 20 --mode gpu --dry-run   # a probe every 180 s
  sleep 1
  quiet g deadline 8s   # seen at the daemon's next 5 s step, with time left to spread probes over
  local i
  for i in $(seq 1 30); do
    fired && break
    sleep 0.5
  done
  expect "the deadline shutdown came at the deadline" fired
  expect "with the GPU seen idle" [ "$(sig gpu)" = idle ]
  expect "after more than the one probe of the old schedule [$(grep -c query "$STUB_DIR/nvsmi_calls")]" \
    [ "$(grep -c query "$STUB_DIR/nvsmi_calls")" -ge 2 ]
  export AUTODL_NO_DAEMON=1
  teardown
}

# an arm writes its generation after every setting, so a window that reads the generation before any setting reads
# the settings of that arm, or of a later one whose generation discards it (round 5, item 2)
t8_an_arm_writes_its_generation_after_its_settings() {
  setup
  use_clock
  quiet g arm --idle 1m --dry-run
  local gen
  gen="$(state arm_gen)"
  expect "an arm writes a generation" [ -n "$gen" ]
  adv 1
  AUTODL_TEST_FAIL_PUT=counters g arm --rearm --idle 2m --dry-run > /dev/null 2>&1
  expect "a rearm cut at its last setting has written the settings before it" [ "$(state idle_s)" = 120 ]
  expect "but not its generation" [ "$(state arm_gen)" = "$gen" ]
  quiet g arm --rearm --idle 2m --dry-run
  expect "a rearm that finishes writes a new one" [ "$(state arm_gen)" != "$gen" ]
  teardown
}

# the old daemon can take the state lock in the gap between an arm's unlock and its restart of the daemon; with the
# gap held open, the old daemon's check of its window from before the arm is discarded there (round 5, item 2)
t8_in_a_rearms_gap_the_old_window_is_discarded() {
  setup
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --interval 2s --dry-run
  wait_file "$AUTODL_GUARD_HOME/state2/signals"   # the old daemon runs its windows
  local pid i
  AUTODL_TEST_ARM_GAP="$STUB_DIR/gap" bash "$GUARD" arm --rearm --idle 60m --interval 3s --dry-run > /dev/null 2>&1 &
  pid=$!
  for i in $(seq 1 30); do
    grep -q "CHECK SKIPPED" "$AUTODL_GUARD_HOME/guard.log" && break
    sleep 0.5
  done
  expect "in the gap the old daemon's check of a window from before the rearm is discarded" \
    grep -q "CHECK SKIPPED: its window began before the last arm" "$AUTODL_GUARD_HOME/guard.log"
  touch "$STUB_DIR/gap"
  expect "the rearm then finishes" wait "$pid"
  expect "and a daemon runs" status_has '^daemon_alive=1$'
  export AUTODL_NO_DAEMON=1
  teardown
}

# ---- group 13 (0.8): quiet periods (plan Task 3.4, design 5.3) ----
# a job declared quiet at its start counts as in use until the declared end; the idle time counts from that end
t8_run_quiet_holds_until_expiry() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  quiet g run j --quiet 10m -- sleep 901
  expect "the declaration is logged" grep -qF 'QUIET job=j until_up=1600 reason=[]' "$AUTODL_GUARD_HOME/guard.log"
  expect "no shutdown in 6 silent minutes" [ "$(tick_for 360)" = 0 ]
  expect "nor until the quiet period ends" [ "$(tick_for 240)" = 0 ]
  expect "its end is the last activity" [ "$(state last_active_up)" = 1600 ]
  expect "no shutdown until --idle after it" [ "$(tick_for 299)" = 0 ]
  expect "then it fires" [ "$(tick_for 1)" = 10 ]
  pkill -f "sleep 901" 2> /dev/null
  wait_job j
  teardown
}

# a job that ends takes its quiet period with it: the idle time counts from the job's end
t8_quiet_ends_with_the_job() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  quiet g run j --quiet 30m -- "while [ ! -f '$STUB_DIR/stop' ]; do sleep 0.2; done"
  quiet tick_for 120
  touch "$STUB_DIR/stop"
  wait_job j
  expect "the job's end is the last activity" [ "$(state last_active_up)" = 1120 ]
  expect "status shows no quiet period for an ended job" fails status_has '^quiet.j='
  expect "an ended job's quiet period no longer holds" [ "$(tick_for 299)" = 0 ]
  expect "so it fires --idle after the job's end" [ "$(tick_for 1)" = 10 ]
  teardown
}

# quiet declares, lengthens or shortens the quiet period of a running job until the deadline; one declared before
# the deadline holds past it, to its end, and grace counts from there
t8_quiet_command_before_and_after_the_deadline() {
  setup
  use_clock
  quiet g arm --idle 60m --deadline 10m --grace 1m --dry-run
  quiet g run j -- sleep 902
  quiet tick_for 60
  expect "quiet before the deadline" quiet g quiet j 20m --reason "a long wait"
  expect "until 20 minutes from now" [ "$(quiet_end j)" = 2260 ]
  expect "logged with its reason" grep -qF 'QUIET job=j until_up=2260 reason=[a long wait]' "$AUTODL_GUARD_HOME/guard.log"
  expect "and it can be shortened" quiet g quiet j 10m
  expect "to 10 minutes from now" [ "$(quiet_end j)" = 1660 ]
  expect "declared before the deadline, it holds past it" [ "$(tick_for 600)" = 0 ]
  expect "after the deadline no quiet is declared any more" [ "$(rc_of g quiet j 30m)" = 7 ]
  expect "nor extended" [ "$(quiet_end j)" = 1660 ]
  expect "grace counts from its end" [ "$(tick_for 59)" = 0 ]
  expect "then it fires" [ "$(tick_for 1)" = 10 ]
  expect "as a deadline shutdown" [ "$(fired_kind)" = deadline ]
  pkill -f "sleep 902" 2> /dev/null
  wait_job j
  teardown
}

t8_quiet_refuses_unknown_or_ended_jobs() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  expect "no such job" fails g quiet nojob 10m
  quiet g run fin -- true
  wait_job fin
  expect "an ended job" fails g quiet fin 10m
  expect "a bad job name" fails g quiet 'a b' 10m
  expect "a bad duration" fails g quiet fin soon
  expect "nothing written for the ended job" [ ! -f "$AUTODL_GUARD_HOME/jobs/fin/quiet" ]
  expect "run with a bad --quiet is refused" fails g run q1 --quiet soon -- true
  expect "and so is --quiet 0s" fails g run q2 --quiet 0s -- true
  expect "the first registers no job" [ ! -d "$AUTODL_GUARD_HOME/jobs/q1" ]
  expect "nor the second" [ ! -d "$AUTODL_GUARD_HOME/jobs/q2" ]
  quiet g off-now --reason x
  expect "quiet refused while a shutdown is pending (exit 4)" [ "$(rc_of g quiet fin 10m)" = 4 ]
  teardown
}

t8_quiet_of_another_boot_is_void() {
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  quiet g run j --quiet 30m -- sleep 903
  printf '%s boot0\n' "$(quiet_end j)" > "$AUTODL_GUARD_HOME/jobs/j/quiet"
  expect "a quiet period of another boot does not hold" [ "$(tick_for 120)" = 10 ]
  pkill -f "sleep 903" 2> /dev/null
  wait_job j
  teardown
}

# a job that hangs holds the instance only until its quiet period ends
t8_a_stuck_quiet_job_holds_only_until_expiry() {
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  quiet g run j --quiet 5m -- sleep 904
  expect "held while the quiet period runs" [ "$(tick_for 300)" = 0 ]
  expect "then no shutdown until --idle after its end" [ "$(tick_for 119)" = 0 ]
  expect "fires though the job still runs" [ "$(tick_for 1)" = 10 ]
  pkill -f "sleep 904" 2> /dev/null
  wait_job j
  teardown
}

t8_the_latest_of_several_quiet_periods_counts() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  quiet g run a --quiet 170s -- sleep 905
  quiet g run b --quiet 150s -- sleep 906
  quiet tick_for 120
  expect "both ended by this check" [ "$(tick_for 60)" = 0 ]
  expect "the later end is the last activity" [ "$(state last_active_up)" = 1170 ]
  pkill -f "sleep 905" 2> /dev/null
  wait_job a
  pkill -f "sleep 906" 2> /dev/null
  wait_job b
  teardown
}

# keep and quiet periods end at one protection end, the latest that has passed (plan Task 3.4, round 2 new 10)
t8_quiet_and_keep_end_at_different_times() {
  setup
  use_clock
  quiet g arm --idle 60m --keep 3m --dry-run
  quiet g run j --quiet 2m -- sleep 907
  quiet tick_for 180
  expect "quiet before keep: the keep's later end is the last activity" [ "$(state last_active_up)" = 1180 ]
  pkill -f "sleep 907" 2> /dev/null
  wait_job j
  teardown
  setup
  use_clock
  quiet g arm --idle 60m --keep 2m --dry-run
  quiet g run j --quiet 3m -- sleep 908
  quiet tick_for 180
  expect "keep before quiet: the quiet period's later end is the last activity" [ "$(state last_active_up)" = 1180 ]
  pkill -f "sleep 908" 2> /dev/null
  wait_job j
  teardown
  setup
  use_clock
  quiet g arm --idle 60m --keep 10m --deadline 2m --grace 1m --dry-run
  quiet g run j --quiet 3m -- sleep 909
  expect "the deadline ends the keep, the quiet period declared before it goes on" [ "$(tick_for 180)" = 0 ]
  expect "its end is the last activity" [ "$(state last_active_up)" = 1180 ]
  expect "grace after it" [ "$(tick_for 59)" = 0 ]
  expect "then the deadline shutdown" [ "$(tick_for 1)" = 10 ]
  pkill -f "sleep 909" 2> /dev/null
  wait_job j
  teardown
}

# an ended protection moves the last activity to its end, never back: activity after that end stays the last
t8_an_ended_quiet_period_never_moves_the_last_activity_back() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  quiet g run j --quiet 90s -- sleep 911
  quiet tick_for 60
  busy_cpu 3000000
  quiet tick_for 60
  expect "activity after the quiet period's end is the last activity" [ "$(state last_active_up)" = 1120 ]
  quiet tick_for 60
  expect "and the quiet period's earlier end does not move it back" [ "$(state last_active_up)" = 1120 ]
  pkill -f "sleep 911" 2> /dev/null
  wait_job j
  teardown
}

# a quiet period whose end cannot be read counts as in use while its job runs, and is logged
t8_an_unreadable_quiet_period_counts_as_in_use() {
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  quiet g run j --quiet 1m -- sleep 912
  printf 'soon' > "$AUTODL_GUARD_HOME/jobs/j/quiet"
  expect "no shutdown while the job runs" [ "$(tick_for 300)" = 0 ]
  expect "it counts as in use" [ "$(state active_why)" = quiet:j:unknown ]
  expect "status says it cannot be read" status_has '^quiet.j=unknown$'
  expect "it is logged" grep -qF 'STATE CORRUPT quiet of job(s) [j]' "$AUTODL_GUARD_HOME/guard.log"
  pkill -f "sleep 912" 2> /dev/null
  wait_job j
  teardown
}

t8_status_shows_quiet_periods() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  quiet g run j --quiet 10m -- sleep 910
  adv 100
  expect "status gives the seconds left of a job's quiet period" status_has '^quiet.j=500$'
  expect "and no time to the shutdown while it runs" status_has '^shutdown_in_s=$'
  adv 500
  expect "0 once it has run out while the job still runs" status_has '^quiet.j=0$'
  expect "the idle time counts from its end" status_has '^idle_for_s=0$'
  expect "and the shutdown comes --idle after it" status_has '^shutdown_in_s=300$'
  pkill -f "sleep 910" 2> /dev/null
  wait_job j
  teardown
}

# ---- group 13 (0.8): the off-now transaction and the gate (plan Task 3.5, design 5.5) ----
ob_dir() { mkdir -p "$T/bar"; export AUTODL_TEST_OFFNOW_BARRIER="$T/bar"; }   # off-now marks each step it reaches
ob_hold() { touch "$T/bar/$1.hold"; }   # ob_hold STEP: off-now waits at every STEP until let go
ob_reached() { wait_file "$T/bar/$1"; }   # ob_reached STEP.N: wait (max 10 s) until off-now is at STEP the Nth time
ob_go() { touch "$T/bar/$1.go"; }   # ob_go STEP.N: let it go on
prep_now() { printf '%s %s %s' "$1" "$(up_s)" "${2:-boot1}" > "$AUTODL_GUARD_HOME/state2/prep"; }   # TOKEN [BOOT]

t8_off_now_refuses_a_running_job() {
  setup
  use_clock
  quiet g arm --idle 60m --keep 30m --dry-run
  quiet g run j -- sleep 913
  g off-now --reason x > "$T/out" 2>&1
  expect "off-now refuses while a registered job runs (exit 3)" [ "$?" = 3 ]
  expect "and names the job" grep -q 'job:j' "$T/out"
  expect "its preparation is removed" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  expect "the keep is untouched" [ "$(state keep_until_up)" = 2800 ]
  expect "nothing is pending" [ "$(state shutdown_pending)" = 0 ]
  pkill -f "sleep 913" 2> /dev/null
  wait_job j
  teardown
}

t8_off_now_refuses_live_activity() {
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  ob_dir
  ob_hold sample-started
  g off-now --reason x > "$T/out" 2>&1 &
  local pid=$!
  ob_reached sample-started.1
  busy_cpu 5000000000
  ob_go sample-started.1
  wait "$pid"
  expect "off-now refuses activity seen while it samples (exit 3)" [ "$?" = 3 ]
  expect "and says it is the CPU" grep -q '^cpu:busy' "$T/out"
  expect "nothing fired" not_fired
  expect "its preparation is removed" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  teardown
}

t8_off_now_idle_shuts_down_after_saying_so() {
  setup
  use_clock
  quiet g arm --idle 60m
  ob_dir
  ob_hold before-shutdown
  g off-now --reason x > "$T/out" 2>&1 &
  local pid=$!
  ob_reached before-shutdown.1
  expect "it says it is issuing the shutdown" grep -q '^shutdown issuing' "$T/out"
  expect "before the shutdown command is called" [ ! -s "$STUB_DIR/shutdown_calls" ]
  ob_go before-shutdown.1
  wait "$pid"
  expect "then the command is called" [ -s "$STUB_DIR/shutdown_calls" ]
  teardown
}

t8_off_now_force_skips_the_checks() {
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  quiet g run j -- sleep 914
  ob_dir
  expect "off-now --force goes ahead though a job runs" quiet g off-now --force --reason runaway
  expect "it fired" fired
  expect "as a forced shutdown" [ "$(fired_kind)" = forced ]
  expect "without a live sample" [ ! -e "$T/bar/sample-started.1" ]
  pkill -f "sleep 914" 2> /dev/null
  wait_job j
  teardown
}

t8_gate_refuses_changes_but_not_reads() {
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  quiet g run j -- sleep 915
  prep_now other
  expect "arm is gated (exit 8)" [ "$(rc_of g arm --rearm --idle 60m --dry-run)" = 8 ]
  expect "revive is gated" [ "$(rc_of g revive)" = 8 ]
  expect "run is gated" [ "$(rc_of g run k -- true)" = 8 ]
  expect "keep is gated" [ "$(rc_of g keep 5m)" = 8 ]
  expect "quiet is gated" [ "$(rc_of g quiet j 5m)" = 8 ]
  expect "deadline is gated" [ "$(rc_of g deadline 1h)" = 8 ]
  expect "off-when-done is gated" [ "$(rc_of g off-when-done)" = 8 ]
  expect "another off-now is gated" [ "$(rc_of g off-now --reason x)" = 8 ]
  expect "status is not" quiet g status
  expect "nor logtail" quiet g logtail guard
  expect "nor sample" quiet env AUTODL_TEST_UPTIME= bash "$GUARD" sample --every 1s --count 1 --gpu-samples 0
  expect "the gated commands changed nothing" [ "$(state keep_until_up)" = 0 ]
  expect "no job was registered" [ ! -d "$AUTODL_GUARD_HOME/jobs/k" ]
  expect "and the preparation is left alone" grep -q '^other ' "$AUTODL_GUARD_HOME/state2/prep"
  pkill -f "sleep 915" 2> /dev/null
  wait_job j
  teardown
}

t8_a_stale_prep_is_taken_over() {
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  printf 'old %s boot1' 880 > "$AUTODL_GUARD_HOME/state2/prep"
  expect "a preparation 120 s old still gates" [ "$(rc_of g keep 5m)" = 8 ]
  printf 'old %s boot1' 879 > "$AUTODL_GUARD_HOME/state2/prep"
  expect "one 121 s old gates nothing" quiet g keep 5m
  expect "and off-now goes ahead" quiet g off-now --reason x
  expect "it fired" fired
  teardown
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  printf 'old %s boot1' 879 > "$AUTODL_GUARD_HOME/state2/prep"
  quiet tick_for 60
  expect "a check removes a void preparation" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  expect "and logs it" grep -q 'OFF-NOW PREPARATION VOID' "$AUTODL_GUARD_HOME/guard.log"
  teardown
}

t8_a_prep_of_another_boot_is_void() {
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  prep_now other boot0
  expect "a preparation of another boot gates nothing" quiet g keep 5m
  expect "and off-now goes ahead" quiet g off-now --reason x
  expect "it fired" fired
  teardown
}

t8_off_now_keeps_keep_when_refused() {
  setup
  use_clock
  quiet g arm --idle 60m --keep 30m --dry-run
  quiet g run j -- "while [ ! -f '$STUB_DIR/stop' ]; do sleep 0.2; done"
  expect "refused while the job runs" [ "$(rc_of g off-now --reason x)" = 3 ]
  expect "the keep is as it was" [ "$(state keep_until_up)" = 2800 ]
  touch "$STUB_DIR/stop"
  wait_job j
  expect "off-now goes ahead once it has ended" quiet g off-now --reason x
  expect "and the keep is cleared" [ "$(state keep_until_up)" = 0 ]
  teardown
}

t8_off_now_rechecks_under_the_lock() {
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  ob_dir
  ob_hold sampled
  g off-now --reason x > "$T/out" 2>&1 &
  local pid=$!
  ob_reached sampled.1
  prep_now someone-else
  ob_go sampled.1
  wait "$pid"
  expect "an off-now whose preparation another took over stops (exit 8)" [ "$?" = 8 ]
  expect "nothing fired" not_fired
  expect "nothing is pending" [ "$(state shutdown_pending)" = 0 ]
  expect "the other preparation is left alone" grep -q '^someone-else ' "$AUTODL_GUARD_HOME/state2/prep"
  teardown
  setup
  use_clock
  quiet g arm --idle 60m --keep 30m --dry-run
  ob_dir
  ob_hold sampled
  g off-now --reason x > "$T/out" 2>&1 &
  pid=$!
  ob_reached sampled.1
  adv 121   # the preparation outlives its 120 s: the gate lapses
  quiet g keep 1h   # and a change gets through
  ob_go sampled.1
  wait "$pid"
  expect "an off-now whose preparation expired meanwhile is refused (exit 3)" [ "$?" = 3 ]
  expect "nothing fired" not_fired
  expect "the keep set meanwhile stands" [ "$(state keep_until_up)" = 4721 ]
  expect "its preparation is removed" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  teardown
}

t8_off_now_without_arm_uses_defaults() {
  setup
  use_clock
  expect "off-now works without an arm" quiet g off-now --reason x
  expect "the shutdown command was called" [ -s "$STUB_DIR/shutdown_calls" ]
  expect "as an off-now" [ "$(state shutdown_kind)" = now ]
  teardown
  setup
  use_clock
  ob_dir
  ob_hold sample-started
  g off-now --reason x > "$T/out" 2>&1 &
  local pid=$!
  ob_reached sample-started.1
  busy_cpu 5000000000
  ob_go sample-started.1
  wait "$pid"
  expect "without an arm it still refuses activity (exit 3)" [ "$?" = 3 ]
  expect "the shutdown command was not called" [ ! -s "$STUB_DIR/shutdown_calls" ]
  teardown
}

# the check under the lock needs a sample that ended at most 1 s before; an older one is taken again once
t8_off_now_resamples_a_stale_sample_once() {
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  ob_dir
  ob_hold sampled
  g off-now --reason x > "$T/out" 2>&1 &
  local pid=$!
  ob_reached sampled.1
  adv 2
  ob_go sampled.1
  ob_reached sampled.2
  adv 1
  ob_go sampled.2
  wait "$pid"
  expect "a sample 2 s old is taken again, and one exactly 1 s old is used" [ "$?" = 0 ]
  expect "so the shutdown goes ahead" fired
  teardown
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  ob_dir
  ob_hold sampled
  g off-now --reason x > "$T/out" 2>&1 &
  pid=$!
  ob_reached sampled.1
  adv 2
  ob_go sampled.1
  ob_reached sampled.2
  adv 2
  ob_go sampled.2
  wait "$pid"
  expect "twice too old: refused (exit 3)" [ "$?" = 3 ]
  expect "nothing fired" not_fired
  expect "its preparation is removed" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  teardown
}

t8_off_now_reports_a_shutdown_committed_meanwhile() {
  setup
  use_clock
  quiet g arm --idle 1m --dry-run
  ob_dir
  ob_hold sampled
  g off-now --reason x > "$T/out" 2>&1 &
  local pid=$!
  ob_reached sampled.1
  expect "meanwhile a check shuts down for idleness" [ "$(tick_for 60)" = 10 ]
  ob_go sampled.1
  wait "$pid"
  expect "off-now reports the shutdown committed meanwhile (exit 4)" [ "$?" = 4 ]
  expect "and says so" grep -q 'already pending' "$T/out"
  expect "without committing another" [ "$(state shutdown_kind)" = idle ]
  expect "that shutdown was attempted once" [ "$(state shutdown_attempts)" = 1 ]
  expect "its preparation is removed" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  teardown
}

# an off-now that dies part way leaves, before its commit point, only a preparation that is void after 120 s,
# and after it, an off-now shutdown that the next check retries (the keep may still be set: kind now ignores it)
t8_off_now_dies_at_each_step() {
  local step
  for step in prep-written sample-started sampled checked metadata; do
    setup
    use_clock
    quiet g arm --idle 60m --keep 30m --dry-run
    AUTODL_TEST_OFFNOW_DIE_AT=$step g off-now --reason x > /dev/null 2>&1
    expect "died at $step: nothing is pending" [ "$(state shutdown_pending)" = 0 ]
    expect "died at $step: the keep is as it was" [ "$(state keep_until_up)" = 2800 ]
    expect "died at $step: no shutdown reason was recorded" [ -z "$(state last_shutdown_reason)" ]
    expect "died at $step: its preparation is left" [ -e "$AUTODL_GUARD_HOME/state2/prep" ]
    expect "died at $step: and gates for now" [ "$(rc_of g keep 5m)" = 8 ]
    adv 121
    expect "died at $step: but not after 120 s" quiet g keep 5m
    teardown
  done
  for step in committed keep-cleared prep-removed before-shutdown; do
    setup
    use_clock
    quiet g arm --idle 60m --keep 30m
    AUTODL_TEST_OFFNOW_DIE_AT=$step g off-now --reason x > /dev/null 2>&1
    expect "died at $step: an off-now shutdown is pending" [ "$(state shutdown_pending)" = 1 ]
    expect "died at $step: of kind now" [ "$(state shutdown_kind)" = now ]
    expect "died at $step: the command was not called" [ ! -s "$STUB_DIR/shutdown_calls" ]
    expect "died at $step: the next check retries it" [ "$(tick_for 60)" = 10 ]
    expect "died at $step: and calls the command" [ -s "$STUB_DIR/shutdown_calls" ]
    teardown
  done
}

t8_shutdown_committed_before_issuing() {
  setup
  use_clock
  quiet g arm --idle 60m
  echo 2 > "$STUB_DIR/sync_delay"   # sync takes 2 s
  ob_dir
  ob_hold prep-removed
  ob_hold before-shutdown
  g off-now --reason x > "$T/out" 2>&1 &
  local pid=$!
  ob_reached prep-removed.1
  expect "it has said the shutdown is committed" grep -q '^shutdown committed' "$T/out"
  ob_go prep-removed.1
  sleep 1
  expect "while sync runs it does not say it is issuing the shutdown" fails grep -q '^shutdown issuing' "$T/out"
  ob_reached before-shutdown.1
  expect "and then that it is issuing it" \
    [ "$(grep -n '^shutdown issuing' "$T/out" | cut -d: -f1)" -gt "$(grep -n '^shutdown committed' "$T/out" | cut -d: -f1)" ]
  expect "after sync finished" [ -s "$STUB_DIR/sync_done" ]
  expect "and before the shutdown command" [ ! -s "$STUB_DIR/shutdown_calls" ]
  ob_go before-shutdown.1
  wait "$pid"
  expect "then the command is called" [ -s "$STUB_DIR/shutdown_calls" ]
  teardown
}

t8_off_now_sample_option() {
  setup
  use_clock
  quiet g arm --idle 60m --dry-run
  expect "--sample 0 is refused" fails g off-now --sample 0 --reason x
  expect "--sample 61 is refused" fails g off-now --sample 61 --reason x
  expect "--sample 5s is refused (plain seconds)" fails g off-now --sample 5s --reason x
  expect "and leaves no preparation" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  expect "nor anything pending" [ "$(state shutdown_pending)" = 0 ]
  teardown
}

# with a shutdown of this boot already pending, off-now says so at once: no preparation, no sample (step 1)
t8_off_now_with_a_pending_shutdown_reports_it_at_once() {
  setup
  use_clock
  quiet g arm --idle 1m --dry-run
  expect "a check shuts down for idleness" [ "$(tick_for 60)" = 10 ]
  ob_dir
  g off-now --reason x > "$T/out" 2>&1
  expect "off-now reports the pending shutdown (exit 4)" [ "$?" = 4 ]
  expect "and says so" grep -q 'already pending' "$T/out"
  expect "before any sample" [ ! -e "$T/bar/sample-started.1" ]
  expect "and without a preparation" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  expect "the pending shutdown is as it was" [ "$(state shutdown_kind)" = idle ]
  teardown
}

# ---- group 13 (0.8): status (plan Task 3.6) ----
state_digest() { (cd "$AUTODL_GUARD_HOME" && find state2 jobs -type f | LC_ALL=C sort | xargs md5sum); }   # names, contents

t8_status_keys() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  local k
  for k in version now up mode mode_now boot schema needs_rearm armed_at armed_this_boot arm_incomplete idle_s grace_s \
    interval_s gpu_probes thr_gpu thr_cpu thr_io thr_net unreliable calib calib_coverage no_reliable_signal sig.gpu \
    sig.cpu sig.io sig.net deadline_at deadline_in_s past_deadline keep_until_at keep_in_s last_active_at idle_for_s \
    shutdown_in_s active_why gated off_when_done shutdown_pending shutdown_kind shutdown_attempts dry_run \
    dry_run_fired heartbeat daemon_alive daemon_version last_shutdown_reason last_shutdown_at state_corrupt_logged; do
    expect "status has $k" status_has "^$k="
  done
  expect "without a deadline deadline_in_s is empty" status_has '^deadline_in_s=$'
  expect "and past_deadline is 0" status_has '^past_deadline=0$'
  expect "no key of 0.7 is left" fails status_has '^\(deadline\|keep_until\|last_busy\|busy_now\|post_job_keep_s\|util_[a-z_]*\)='
  expect "gated is empty while no off-now is being prepared" status_has '^gated=$'
  prep_now x
  expect "and prep while one is" status_has '^gated=prep$'
  printf 'x %s boot1' 879 > "$AUTODL_GUARD_HOME/state2/prep"
  expect "but not for a void one" status_has '^gated=$'
  teardown
}

# a keep that ended between two checks: status counts the idle time from its end, as the next check will
t8_status_counts_from_the_same_point_as_tick() {
  setup
  use_clock
  quiet g arm --idle 5m --keep 90s --dry-run
  quiet tick_for 60
  adv 60
  expect "status counts the idle time from the keep's end" status_has '^idle_for_s=30$'
  expect "and the shutdown --idle after it" status_has '^shutdown_in_s=270$'
  quiet g tick
  expect "as the next check does" [ "$(state last_active_up)" = 1090 ]
  teardown
}

t8_status_counts_down() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  quiet tick_for 120
  expect "idle for 120 s" status_has '^idle_for_s=120$'
  expect "shut down in 180 s" status_has '^shutdown_in_s=180$'
  teardown
  setup
  use_clock
  quiet g arm --idle 60m --deadline 3m --grace 5m --dry-run
  quiet tick_for 120
  expect "the deadline in 60 s" status_has '^deadline_in_s=60$'
  expect "before it, the rest of --idle" status_has '^shutdown_in_s=3480$'
  quiet tick_for 60
  expect "then past the deadline" status_has '^past_deadline=1$'
  expect "and the rest of --grace" status_has '^shutdown_in_s=120$'
  teardown
}

# status changes nothing: not a file of the state or of the jobs, and it creates none
t8_status_is_read_only() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  quiet g run j --quiet 5m -- sleep 932
  quiet tick_for 60
  local before
  before="$(state_digest)"
  quiet g status
  expect "status changes no file and creates none" [ "$(state_digest)" = "$before" ]
  pkill -f "sleep 932" 2> /dev/null
  wait_job j
  teardown
}

# ---- group 14 (0.8): no 0.8 daemon next to a live 0.7 one (plan Task 3.8, design 5.7) ----
lock_busy() { ! flock -n "$1" true 2> /dev/null; }   # lock_busy FILE: someone holds the lock on FILE
wait_lock_busy() {  # wait_lock_busy FILE: wait (max 10 s) until someone holds the lock on FILE
  local i
  for i in $(seq 1 20); do
    [ -e "$1" ] && lock_busy "$1" && return 0
    sleep 0.5
  done
  return 1
}
state07_digest() { (cd "$AUTODL_GUARD_HOME/state" && find . -type f | LC_ALL=C sort | xargs md5sum); }

t14_arm_refuses_next_to_a_live_07_daemon() {
  setup
  unset AUTODL_NO_DAEMON
  quiet bash "$HERE/fixtures/autodl_guard_0.7.1.sh" arm --deadline 60m --keep 60m --grace 0s --dry-run   # keep: its daemon stays
  export AUTODL_NO_DAEMON=1
  expect "the 0.7.1 daemon holds its lock" wait_lock_busy "$AUTODL_GUARD_HOME/state/.daemon.lock"
  local out
  out="$(g arm --idle 60m --dry-run 2>&1)"
  expect "0.8 arm is refused next to it" [ "$?" != 0 ]
  expect "and says why" grep -q '0.7' <<< "$out"
  expect "revive is refused too" fails g revive
  expect "nothing was written to state2" [ -z "$(ls -A "$AUTODL_GUARD_HOME/state2" 2> /dev/null)" ]
  kill "$(cat "$AUTODL_GUARD_HOME/state/daemon_pid")" 2> /dev/null
  teardown
}

t14_the_07_lock_alone_decides() {
  setup
  local lf="$AUTODL_GUARD_HOME/state/.daemon.lock" holder live
  mkdir -p "$AUTODL_GUARD_HOME/state"
  ( exec 7> "$lf" && flock -n 7 && exec sleep 931 ) &
  holder=$!
  expect "the lock is held" wait_lock_busy "$lf"
  expect "a held 0.7 daemon lock without a daemon_pid makes arm refuse" fails g arm --idle 60m --dry-run
  kill "$holder"
  wait "$holder" 2> /dev/null
  sleep 931 &
  live=$!
  printf '%s' "$live" > "$AUTODL_GUARD_HOME/state/daemon_pid"
  expect "a free lock lets arm go ahead, whatever daemon_pid names" quiet g arm --rearm --idle 60m --dry-run
  kill "$live"
  teardown
}

t14_an_07_state_of_an_earlier_boot_does_not_block() {
  setup
  export AUTODL_BOOT_MARKER=boot0
  quiet bash "$HERE/fixtures/autodl_guard_0.7.1.sh" arm --deadline 60m --keep 0s --grace 0s --dry-run
  export AUTODL_BOOT_MARKER=boot1
  local before
  before="$(state07_digest)"
  expect "0.7 left its state" [ -n "$before" ]
  expect "0.8 arms as usual" quiet g arm --idle 60m --dry-run
  expect "and revive works" quiet g revive
  expect "0.7's directory is as it was" [ "$(state07_digest)" = "$before" ]
  teardown
}

# revive of an armed 0.8 is refused too, for the 0.7 daemon and not for want of an arm, and starts no daemon
t14_revive_refuses_next_to_a_live_07_daemon() {
  setup
  quiet g arm --idle 60m --dry-run
  unset AUTODL_NO_DAEMON
  quiet bash "$HERE/fixtures/autodl_guard_0.7.1.sh" arm --deadline 60m --keep 60m --grace 0s --dry-run
  export AUTODL_NO_DAEMON=1
  expect "the 0.7.1 daemon holds its lock" wait_lock_busy "$AUTODL_GUARD_HOME/state/.daemon.lock"
  local out
  out="$(g revive 2>&1)"
  expect "revive of an armed 0.8 is refused next to it" [ "$?" != 0 ]
  expect "and says why" grep -q '0.7 guard daemon' <<< "$out"
  expect "no 0.8 daemon was started" status_has '^daemon_alive=0$'
  kill "$(cat "$AUTODL_GUARD_HOME/state/daemon_pid")" 2> /dev/null
  teardown
}

# a 0.7 daemon lock that cannot be tried (here: a file this user cannot open) makes arm refuse, writing nothing:
# two daemons would be worse than a refusal. As root any file opens, so the case cannot be made there
t14_a_07_lock_that_cannot_be_tried_refuses() {
  setup
  if [ "$(id -u)" = 0 ]; then
    printf 'SKIP %s\n' "a 0.7 lock that cannot be tried: root opens any file"
    teardown
    return
  fi
  local lf="$AUTODL_GUARD_HOME/state/.daemon.lock" out
  mkdir -p "$AUTODL_GUARD_HOME/state"
  : > "$lf"
  chmod 000 "$lf"
  out="$(g arm --idle 60m --dry-run 2>&1)"
  expect "arm refuses when the 0.7 lock cannot be tried" [ "$?" != 0 ]
  expect "and says so" grep -q 'cannot tell whether a 0.7 guard daemon runs' <<< "$out"
  expect "writing nothing" [ -z "$(ls -A "$AUTODL_GUARD_HOME/state2" 2> /dev/null)" ]
  chmod 600 "$lf"
  teardown
}

# ---- group 13 (0.8): after the Codex review of the Phase 3 code (docs/reviews/2026-09-30-codex-phase3-code.md) ----
quiet_end() { local e=""; { read -r e _ < "$AUTODL_GUARD_HOME/jobs/$1/quiet"; } 2> /dev/null; printf '%s' "$e"; }   # quiet_end NAME

# a declaration is one file written whole: when writing it fails, the old one stands (item 2)
t8_a_failed_quiet_leaves_the_old_declaration() {
  setup
  use_clock
  quiet g arm --idle 5m --dry-run
  quiet g run j --quiet 20m -- sleep 933
  expect "shortening it fails when the declaration cannot be written" \
    fails env AUTODL_TEST_FAIL_JOBFILE=quiet bash "$GUARD" quiet j 1m
  expect "the old end stands" [ "$(quiet_end j)" = 2200 ]
  quiet tick_for 120
  expect "so the job is in use past the shorter end that failed" [ "$(state last_active_up)" = 1120 ]
  pkill -f "sleep 933" 2> /dev/null
  wait_job j
  teardown
}

# a declaration whose end is not a positive number, or that lacks its boot, or is empty, counts as in use while the
# job runs (item 2)
t8_a_quiet_declaration_without_a_valid_end_counts_as_in_use() {
  local c
  for c in '0 boot1' '1500' ''; do
    setup
    use_clock
    quiet g arm --idle 2m --dry-run
    quiet g run j --quiet 1m -- sleep 934
    printf '%s\n' "$c" > "$AUTODL_GUARD_HOME/jobs/j/quiet"
    expect "[$c]: no shutdown while the job runs" [ "$(tick_for 180)" = 0 ]
    expect "[$c]: it counts as in use" [ "$(state active_why)" = quiet:j:unknown ]
    pkill -f "sleep 934" 2> /dev/null
    wait_job j
    teardown
  done
}

# arm_gen is part of every arm: missing, empty or malformed, the state counts as damaged (item 4)
t8_a_damaged_arm_gen_needs_a_rearm() {
  local c base
  for c in missing empty malformed; do
    setup
    use_clock
    quiet g arm --idle 1m --dry-run
    case "$c" in
      missing) rm -f "$AUTODL_GUARD_HOME/state2/arm_gen" ;;
      empty) : > "$AUTODL_GUARD_HOME/state2/arm_gen" ;;
      malformed) printf 'x.y' > "$AUTODL_GUARD_HOME/state2/arm_gen" ;;
    esac
    base="$(state counters)"
    expect "$c arm_gen: status says rearm" status_has '^needs_rearm=1$'
    expect "$c arm_gen: no shutdown however idle" [ "$(tick_for 180)" = 0 ]
    expect "$c arm_gen: the baseline is left alone" [ "$(state counters)" = "$base" ]
    teardown
  done
}

# once a deadline set meanwhile shortens the window, each probe's limit is its share of what is left, not of the old
# window (item 5)
t8_a_shortened_window_limits_its_probes_to_the_new_share() {
  setup
  echo 1 > "$STUB_DIR/gpu"
  printf '#!/bin/sh\nread -r up _ < /proc/uptime\necho "$up $1" >> "%s/timeouts"\nexec timeout "$@"\n' "$STUB_DIR" \
    > "$STUB_DIR/timeout-log"
  chmod +x "$STUB_DIR/timeout-log"
  export AUTODL_TIMEOUT_CMD="$STUB_DIR/timeout-log"
  touch "$STUB_DIR/hang"   # every probe runs to its limit
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --interval 1h --gpu-probes 20 --mode gpu --dry-run   # a probe every 180 s, each up to 5 s
  sleep 1
  quiet g deadline 8s   # seen when the first probe gives up, 5 s into the window
  wait_file "$AUTODL_GUARD_HOME/state2/signals"
  local dl n m
  dl="$(state deadline_up)"
  n="$(awk -v d="$dl" '$1 < d { n++ } END { print n + 0 }' "$STUB_DIR/timeouts")"
  m="$(awk -v d="$dl" '$1 < d { n++; if (n > 1 && $2 > m) m = $2 } END { print m + 0 }' "$STUB_DIR/timeouts")"
  expect "probes were made in the shortened window [$(tr '\n' ';' < "$STUB_DIR/timeouts")]" [ "$n" -ge 2 ]
  expect "none of them was given more than its share of what was left [$m]" awk -v m="$m" 'BEGIN { exit !(m <= 1.5) }'
  rm -f "$STUB_DIR/hang"
  unset AUTODL_TIMEOUT_CMD
  export AUTODL_NO_DAEMON=1
  teardown
}

# off-now makes sure a daemon runs before it commits: one that dies after its commit point is retried by it (item 1)
t8_off_now_starts_a_daemon_to_retry() {
  setup
  # no daemon (AUTODL_NO_DAEMON) and no dry run: the shutdown stub is called. At 5 s the daemon's first check is
  # well inside the baseline's age limit (9.5 s), so its rates are good and the retry is not cancelled
  quiet g arm --idle 60m --interval 5s
  unset AUTODL_NO_DAEMON
  expect "no daemon yet" status_has '^daemon_alive=0$'
  AUTODL_TEST_OFFNOW_DIE_AT=committed g off-now --reason x > /dev/null 2>&1
  expect "off-now started one" status_has '^daemon_alive=1$'
  expect "and died with its shutdown committed" [ "$(state shutdown_pending)" = 1 ]
  expect "the daemon retries it" wait_file "$STUB_DIR/shutdown_calls"
  export AUTODL_NO_DAEMON=1
  teardown
}

# a shutdown left pending with no daemon (an off-now that died after its commit point, nothing armed) is not a dead
# end: the next off-now brings a daemon up, which retries it at once, and says the shutdown is pending (item 1)
t8_off_now_resolves_a_pending_shutdown_without_a_daemon() {
  setup
  AUTODL_TEST_OFFNOW_DIE_AT=committed g off-now --reason x > /dev/null 2>&1
  expect "a shutdown is pending" [ "$(state shutdown_pending)" = 1 ]
  expect "with no daemon" status_has '^daemon_alive=0$'
  unset AUTODL_NO_DAEMON
  local out
  out="$(g off-now --reason again 2>&1)"
  expect "off-now says it is pending (exit 4)" [ "$?" = 4 ]
  expect "and why" grep -q 'already pending' <<< "$out"
  expect "the daemon it brought up retries it at once" wait_file "$STUB_DIR/shutdown_calls"
  export AUTODL_NO_DAEMON=1
  teardown
}

# no 0.8 daemon is started next to a live 0.7 one, by off-now or any other command (item 1)
t8_off_now_starts_no_daemon_next_to_a_live_07_one() {
  setup
  quiet g arm --idle 60m --dry-run
  local lf="$AUTODL_GUARD_HOME/state/.daemon.lock" holder out
  mkdir -p "$AUTODL_GUARD_HOME/state"
  ( exec 7> "$lf" && flock -n 7 && exec sleep 935 ) &
  holder=$!
  expect "the 0.7 lock is held" wait_lock_busy "$lf"
  unset AUTODL_NO_DAEMON
  out="$(g off-now --reason x 2>&1)"
  expect "off-now refuses without a daemon to retry" [ "$?" = 1 ]
  expect "and says why" grep -q '0.7 guard daemon' <<< "$out"
  expect "nothing is pending" [ "$(state shutdown_pending)" = 0 ]
  expect "no preparation is left" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  out="$(g run k -- true 2>&1)"
  expect "run starts no daemon next to it either" [ "$?" != 0 ]
  expect "and says it is for the 0.7 daemon" grep -q '0.7 guard daemon' <<< "$out"
  expect "no 0.8 daemon runs" status_has '^daemon_alive=0$'
  export AUTODL_NO_DAEMON=1
  kill "$holder"
  wait "$holder" 2> /dev/null
  teardown
}

# off-now commits only while a daemon runs to retry its shutdown: one that stopped meanwhile stops it (item 1)
t8_off_now_commits_only_while_a_daemon_runs() {
  setup
  unset AUTODL_NO_DAEMON
  quiet g arm --idle 60m --dry-run
  expect "a daemon runs" status_has '^daemon_alive=1$'
  ob_dir
  ob_hold sampled
  g off-now --reason x > "$T/out" 2>&1 &
  local pid=$! i
  ob_reached sampled.1
  kill "$(state daemon_pid)"
  for i in $(seq 1 20); do
    status_has '^daemon_alive=0$' && break
    sleep 0.5
  done
  ob_go sampled.1
  wait "$pid"
  expect "off-now does not commit without it (exit 1)" [ "$?" = 1 ]
  expect "and says why" grep -q 'stopped meanwhile' "$T/out"
  expect "nothing is pending" [ "$(state shutdown_pending)" = 0 ]
  expect "no preparation is left" [ ! -e "$AUTODL_GUARD_HOME/state2/prep" ]
  export AUTODL_NO_DAEMON=1
  teardown
}

# a 0.8 daemon started next to a live 0.7 one, other than through the commands, exits at once and says why (item 1)
t14_a_daemon_does_not_run_next_to_a_live_07_one() {
  setup
  quiet g arm --idle 60m --dry-run
  local lf="$AUTODL_GUARD_HOME/state/.daemon.lock" holder
  mkdir -p "$AUTODL_GUARD_HOME/state"
  ( exec 7> "$lf" && flock -n 7 && exec sleep 936 ) &
  holder=$!
  expect "the 0.7 lock is held" wait_lock_busy "$lf"
  timeout 10 bash "$GUARD" daemon > /dev/null 2>&1
  expect "a daemon started next to it exits at once" [ "$?" = 0 ]
  expect "and says why in the log" grep -q 'DAEMON not started: a 0.7 guard daemon runs' "$AUTODL_GUARD_HOME/guard.log"
  expect "holding no daemon lock" status_has '^daemon_alive=0$'
  kill "$holder"
  wait "$holder" 2> /dev/null
  teardown
}

# a probe never runs without a positive time limit: timeout takes 0 as none, so a zero limit fails the probe (found
# while fixing item 5 of the code review: a window a few centiseconds long gave its probes a share of 0)
t8_a_zero_probe_limit_fails_the_probe() {
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"
  quiet g arm --idle 60m --mode gpu --dry-run
  touch "$STUB_DIR/hang"
  adv 60
  AUTODL_PROBE_TIMEOUT=0 timeout 10 bash "$GUARD" tick > /dev/null 2>&1
  expect "a check with a zero probe limit does not hang" [ "$?" != 124 ]
  expect "and counts the GPU as unknown" [ "$(sig gpu)" = unknown ]
  rm -f "$STUB_DIR/hang"
  teardown
}

# ---- group 15 (0.8): autostart at container start (plan Phase 4, design 5.8) ----
new_boot() { export AUTODL_BOOT_MARKER="$1"; }   # new_boot NAME: the container started again (another boot marker)
bb_dir() { mkdir -p "$T/bb"; export AUTODL_TEST_BOOT_BARRIER="$T/bb"; }   # boot marks each step it reaches
bb_hold() { touch "$T/bb/$1.hold"; }   # bb_hold STEP: boot waits at every STEP until let go
bb_reached() { wait_file "$T/bb/$1"; }   # bb_reached STEP.N: wait (max 10 s) until boot is at STEP the Nth time
bb_go() { touch "$T/bb/$1.go"; }   # bb_go STEP.N: let it go on
wait_for() {  # wait_for FILE PATTERN: wait (max 10 s) until FILE has a line matching PATTERN
  local i
  for i in $(seq 1 40); do
    grep -q -- "$2" "$1" 2> /dev/null && return 0
    sleep 0.25
  done
  return 1
}
have_pid1_ns() {  # a bash that is process 1 of a new PID namespace, with /proc to match, can be had here
  unshare -rpf --mount-proc bash -c '[ "$BASHPID" = 1 ] && [ "$(cat /proc/1/comm)" = bash ]' 2> /dev/null
}
as_pid1() {  # as_pid1 SCRIPT: run SCRIPT in bash as process 1 of a new PID namespace, $0 being AutoDL's start script.
  # Everything in the namespace dies with it, so SCRIPT waits for what it started before it ends
  unshare -rpf --mount-proc bash -c "$1" /init/boot/boot.sh
}
saved() { cat "$AUTODL_GUARD_HOME/state2/boot.$1" 2> /dev/null; }   # saved MODE: the settings kept for the next start

# an arm keeps its settings for the next start in its mode (plan Task 4.1 and 4.3, design 5.8)
t15_arm_saves_the_boot_settings_of_its_mode() {
  setup
  use_clock
  quiet g arm --idle 7m --grace 90s --interval 30s --thr-cpu 2.5 --unreliable net --dry-run
  expect "the no-GPU settings are kept [$(saved nogpu)]" [ "$(saved nogpu)" = "420 90 30 0 5 25 500000 10000 default default net 1" ]
  expect "and none for GPU mode" [ ! -e "$AUTODL_GUARD_HOME/state2/boot.gpu" ]
  expect "armed by the AI" [ "$(state armed_by)" = arm ]
  teardown
}

t15_a_rearm_in_the_other_mode_keeps_the_first_modes_settings() {
  setup
  use_clock
  quiet g arm --idle 7m --grace 90s --interval 30s --thr-cpu 2.5 --unreliable net --dry-run
  echo 1 > "$STUB_DIR/gpu"
  echo 85899345920 > "$STUB_DIR/cgroup_mem"   # 80 GiB: GPU mode
  quiet g arm --rearm --idle 9m
  expect "the GPU settings are kept [$(saved gpu)]" [ "$(saved gpu)" = "540 120 60 3 5 50 500000 10000 default default - 0" ]
  expect "the no-GPU ones stay as they were" [ "$(saved nogpu)" = "420 90 30 0 5 25 500000 10000 default default net 1" ]
  teardown
}

t15_an_arm_whose_settings_cannot_be_saved_is_not_done() {
  setup
  use_clock
  AUTODL_TEST_FAIL_PUT=boot.nogpu bash "$GUARD" arm --idle 5m --req savefail01 > /dev/null 2>&1
  expect "an arm whose settings cannot be kept fails" [ "$?" != 0 ]
  expect "and is not done" status_has '^arm_incomplete=1$'
  expect "so this boot is not armed" status_has '^armed_this_boot=0$'
  expect "the same request, sent again, finishes it" quiet g arm --idle 5m --req savefail01
  expect "armed now" status_has '^armed_this_boot=1$'
  expect "and the settings are kept" [ "$(saved nogpu)" = "300 120 60 0 5 30 500000 10000 default default - 0" ]
  teardown
}

# an arm cut short leaves no settings that the next start could use (round 2 of the Phase 4 plan review, item 1)
t15_a_failed_rearm_leaves_no_settings_behind() {
  setup
  use_clock
  quiet g arm --idle 5m
  AUTODL_TEST_FAIL_PUT=armed_boot bash "$GUARD" arm --rearm --idle 6m --dry-run > /dev/null 2>&1
  expect "a rearm cut at the boot marker fails" [ "$?" != 0 ]
  expect "and leaves who and in which mode [$(state arm_incomplete)]" \
    grep -Eq '^[1-9][0-9]* arm nogpu$' "$AUTODL_GUARD_HOME/state2/arm_incomplete"
  expect "its dry-run settings were written, but never committed" [ "$(saved nogpu)" = "360 120 60 0 5 30 500000 10000 default default - 1" ]
  echo 1 > "$STUB_DIR/gpu"
  echo 85899345920 > "$STUB_DIR/cgroup_mem"
  expect "an arm in GPU mode goes ahead" quiet g arm --idle 7m
  expect "and drops the no-GPU settings of the arm cut short" [ ! -e "$AUTODL_GUARD_HOME/state2/boot.nogpu" ]
  expect "keeps its own" [ "$(saved gpu)" = "420 120 60 3 5 50 500000 10000 default default - 0" ]
  expect "and is done" [ ! -e "$AUTODL_GUARD_HOME/state2/arm_incomplete" ]
  teardown
  setup
  use_clock
  quiet g arm --idle 5m
  mkdir "$AUTODL_GUARD_HOME/state2/arm_incomplete"   # mv puts the new marker inside it, rm -f cannot remove it
  expect "a rearm whose marker cannot be removed fails" fails g arm --rearm --idle 6m --dry-run
  expect "and is not done" status_has '^arm_incomplete=1$'
  teardown
}

# what an earlier arm cut short left is read strictly (round 3 of the Phase 4 plan review, item 5)
t15_an_arm_reads_what_an_arm_cut_short_left() {
  local c n
  for c in '1' 'x arm nogpu' '1 arm nogpu extra' '1 boot garbage' $'1 arm nogpu\n2 arm nogpu'; do
    n="${c//$'\n'/ NL }"   # the name shown: one line per result
    setup
    use_clock
    quiet g arm --idle 5m
    echo 1 > "$STUB_DIR/gpu"
    echo 85899345920 > "$STUB_DIR/cgroup_mem"
    quiet g arm --rearm --idle 7m
    echo 0 > "$STUB_DIR/gpu"
    echo 2147483648 > "$STUB_DIR/cgroup_mem"
    printf '%s' "$c" > "$AUTODL_GUARD_HOME/state2/arm_incomplete"
    quiet g arm --idle 6m
    expect "[$n] is not understood: the GPU settings are dropped" [ ! -e "$AUTODL_GUARD_HOME/state2/boot.gpu" ]
    expect "[$n] and the arm keeps its own" [ "$(saved nogpu)" = "360 120 60 0 5 30 500000 10000 default default - 0" ]
    teardown
  done
  setup
  use_clock
  quiet g arm --idle 5m
  echo 1 > "$STUB_DIR/gpu"
  echo 85899345920 > "$STUB_DIR/cgroup_mem"
  quiet g arm --rearm --idle 7m
  printf '123 boot nogpu' > "$AUTODL_GUARD_HOME/state2/arm_incomplete"   # boot's arm in the other mode, cut short
  quiet g arm --idle 8m
  expect "a boot arm cut short: the no-GPU settings stay" [ "$(saved nogpu)" = "300 120 60 0 5 30 500000 10000 default default - 0" ]
  expect "and the GPU ones are this arm's" [ "$(saved gpu)" = "480 120 60 3 5 50 500000 10000 default default - 0" ]
  printf '123 arm nogpu' > "$AUTODL_GUARD_HOME/state2/arm_incomplete"   # an AI arm in no-GPU mode, cut short
  to_nogpu
  quiet g arm --idle 9m
  expect "an arm cut short in one mode drops only that mode's: the GPU ones stay" \
    [ "$(saved gpu)" = "480 120 60 3 5 50 500000 10000 default default - 0" ]
  expect "and the no-GPU ones are this arm's" [ "$(saved nogpu)" = "540 120 60 0 5 30 500000 10000 default default - 0" ]
  teardown
}

# boot arms a new start with what the last arm kept (plan Task 4.2, design 5.8)
hold_07_lock() {  # hold_07_lock: a process holds 0.7's daemon lock, as a live 0.7 daemon would; HOLDER is its PID
  local lf="$AUTODL_GUARD_HOME/state/.daemon.lock"
  mkdir -p "$AUTODL_GUARD_HOME/state"
  ( exec 7> "$lf" && flock -n 7 && exec sleep 937 ) &
  HOLDER=$!
  wait_lock_busy "$lf"
}
release_07_lock() { kill "$HOLDER" 2> /dev/null; wait "$HOLDER" 2> /dev/null; }
lcalls() { grep -c '^-L' "$STUB_DIR/nvsmi_calls" 2> /dev/null; }   # how often nvidia-smi was asked to list GPUs
to_gpu() { echo 1 > "$STUB_DIR/gpu"; echo 85899345920 > "$STUB_DIR/cgroup_mem"; }   # the next start has a GPU
to_nogpu() { echo 0 > "$STUB_DIR/gpu"; echo 2147483648 > "$STUB_DIR/cgroup_mem"; }   # and this one has not

t15_boot_arms_with_the_saved_settings_of_its_mode() {
  setup
  use_clock 1000
  quiet g arm --idle 7m --keep 30m --deadline 2h --thr-cpu 2.5
  quiet g off-when-done
  new_boot boot2
  adv 30
  expect "boot runs" quiet g boot
  expect "this boot is armed" status_has '^armed_this_boot=1$'
  expect "by boot" status_has '^armed_by=boot$'
  expect "in no-GPU mode" status_has '^mode=nogpu$'
  expect "with the idle time kept" status_has '^idle_s=420$'
  expect "and the CPU threshold kept" status_has '^thr_cpu=2.5$'
  expect "for real" status_has '^dry_run=0$'
  expect "no keep carries over" status_has '^keep_in_s=$'
  expect "no deadline either" status_has '^deadline_in_s=$'
  expect "nor off-when-done" status_has '^off_when_done=0$'
  expect "the last activity is boot's moment" [ "$(state last_active_up)" = 1030 ]
  expect "status says what boot did" status_has '^autostart_this_boot=armed:saved$'
  expect "and the log" grep -q 'BOOT armed mode=nogpu from=saved idle=420s' "$AUTODL_GUARD_HOME/guard.log"
  teardown
}

t15_boot_keeps_a_dry_run() {
  setup
  use_clock 1000
  quiet g arm --idle 3m --dry-run
  new_boot boot2
  quiet g boot
  expect "boot's arm is a dry run too" status_has '^dry_run=1$'
  expect "and the log says why" grep -q 'BOOT the settings kept are a dry run' "$AUTODL_GUARD_HOME/guard.log"
  expect "3 idle minutes later it fires" [ "$(tick_for 180)" = 10 ]
  expect "as a dry run" fired
  expect "without calling shutdown" [ ! -e "$STUB_DIR/shutdown_calls" ]
  teardown
}

# after a switch of mode: the other mode's times and dry run, this mode's thresholds (round 3 of the plan review, item 6)
t15_boot_after_a_mode_switch_uses_the_times_and_that_modes_defaults() {
  local k
  setup
  use_clock 1000
  to_gpu
  quiet g arm --idle 3m --grace 90s --interval 30s --dry-run --gpu-probes 5 --thr-gpu 20 --thr-cpu 8 --thr-io 1000000 \
    --thr-net 20000 --unreliable io --calib c1
  to_nogpu
  new_boot boot2
  quiet g boot
  for k in mode=nogpu idle_s=180 grace_s=90 interval_s=30 dry_run=1 thr_gpu=5 thr_cpu=3.0 thr_io=500000 thr_net=10000 \
    gpu_probes=0 unreliable= calib=default calib_coverage=default autostart_this_boot=armed:fallback; do
    expect "GPU arm, then a no-GPU start: $k" status_has "^$k\$"
  done
  expect "idle for 3 minutes it fires" [ "$(tick_for 180 30)" = 10 ]
  expect "as the dry run it inherited" fired
  expect "without calling shutdown" [ ! -e "$STUB_DIR/shutdown_calls" ]
  teardown
  setup
  use_clock 1000
  quiet g arm --idle 3m
  to_gpu
  new_boot boot2
  quiet g boot
  for k in mode=gpu thr_cpu=5.0 gpu_probes=3 autostart_this_boot=armed:fallback; do
    expect "no-GPU arm, then a GPU start: $k" status_has "^$k\$"
  done
  teardown
}

t15_boot_leaves_nothing_of_the_last_boot() {
  local n
  setup
  use_clock 1000
  quiet g arm --idle 3m
  quiet g run j --quiet 60m -- sleep 917
  touch "$STUB_DIR/shutdown_fail_always"
  quiet g off-now --force --reason x   # its shutdown fails: pending in boot1
  expect "a shutdown was pending in the last boot" [ "$(state shutdown_pending)" = 1 ]
  rm -f "$STUB_DIR/shutdown_fail_always"
  n="$(wc -l < "$STUB_DIR/shutdown_calls")"
  new_boot boot2
  printf 'boot1 armed:saved' > "$AUTODL_GUARD_HOME/state2/autostart"
  expect "what boot did in the last boot is not shown" status_has '^autostart_this_boot=$'
  quiet g boot
  expect "boot arms the new one" status_has '^autostart_this_boot=armed:saved$'
  expect "the last boot's quiet period is gone" fails status_has '^quiet\.j='
  expect "and so is its pending shutdown" status_has '^shutdown_pending=0$'
  adv 60
  quiet g tick
  expect "which is never retried" [ "$(wc -l < "$STUB_DIR/shutdown_calls")" = "$n" ]
  pkill -f "sleep 917" 2> /dev/null
  wait_job j
  teardown
}

t15_boot_without_usable_settings_arms_nothing() {
  setup
  use_clock
  mkdir -p "$AUTODL_GUARD_HOME/state"
  echo 1200 > "$AUTODL_GUARD_HOME/state/idle_s"   # a 0.7 state is no 0.8 arm
  new_boot boot2
  : > "$STUB_DIR/nvsmi_calls"
  unset AUTODL_NO_DAEMON
  timeout 20 bash "$GUARD" boot > /dev/null 2>&1
  expect "boot ends at once, as no daemon [rc=$?]" [ "$?" = 0 ]
  export AUTODL_NO_DAEMON=1
  expect "without asking nvidia-smi" [ "$(lcalls)" = 0 ]
  expect "not armed" status_has '^armed_this_boot=0$'
  expect "status says why" status_has '^autostart_this_boot=skipped:no-settings$'
  expect "no daemon" status_has '^daemon_alive=0$'
  teardown
  setup
  use_clock
  quiet g arm --idle 5m
  printf '0 120 60 0 5 30 500000 10000 default default - 0' > "$AUTODL_GUARD_HOME/state2/boot.nogpu"   # idle 0
  printf '300 120 60 0 5 50 500000 10000 default default - 0' > "$AUTODL_GUARD_HOME/state2/boot.gpu"   # no probes, GPU not off
  new_boot boot2
  quiet g boot
  expect "bad settings are not used" status_has '^autostart_this_boot=skipped:no-settings$'
  expect "the no-GPU ones logged once" [ "$(grep -c 'STATE CORRUPT boot.nogpu' "$AUTODL_GUARD_HOME/guard.log")" = 1 ]
  expect "the GPU ones logged once" [ "$(grep -c 'STATE CORRUPT boot.gpu' "$AUTODL_GUARD_HOME/guard.log")" = 1 ]
  quiet g status
  expect "status logs nothing more" [ "$(grep -c 'STATE CORRUPT boot' "$AUTODL_GUARD_HOME/guard.log")" = 2 ]
  teardown
}

# bad settings of this mode count as none: the other mode's times are taken, and the bad ones are logged once
t15_bad_settings_of_this_mode_are_passed_over() {
  setup
  use_clock
  quiet g arm --idle 5m
  to_gpu
  quiet g arm --rearm --idle 7m
  to_nogpu
  printf 'bad' > "$AUTODL_GUARD_HOME/state2/boot.nogpu"
  new_boot boot2
  quiet g boot
  expect "bad settings of this mode: the other mode's times" status_has '^autostart_this_boot=armed:fallback$'
  expect "the GPU arm's idle time" status_has '^idle_s=420$'
  expect "the bad ones logged once" [ "$(grep -c 'STATE CORRUPT boot.nogpu' "$AUTODL_GUARD_HOME/guard.log")" = 1 ]
  teardown
}

t15_boot_with_an_unknown_mode_arms_nothing() {
  setup
  quiet g arm --idle 5m
  new_boot boot2
  echo 85899345920 > "$STUB_DIR/cgroup_mem"   # not the 2 GiB of non-GPU mode, and no GPU listed: the mode is unknown
  : > "$STUB_DIR/nvsmi_calls"
  env -u AUTODL_NO_DAEMON AUTODL_BOOT_MODE_BUDGET=3 AUTODL_BOOT_MODE_WAIT=1 timeout 20 bash "$GUARD" boot > /dev/null 2>&1
  expect "boot ends, with a daemon allowed, as it starts none [rc=$?]" [ "$?" = 0 ]
  expect "no daemon runs" status_has '^daemon_alive=0$'
  expect "after trying more than once [$(lcalls)]" [ "$(lcalls)" -ge 2 ]
  expect "not armed" status_has '^armed_this_boot=0$'
  expect "status says why" status_has '^autostart_this_boot=skipped:mode-unknown$'
  expect "the log too" grep -q 'BOOT does not arm: cannot tell the mode' "$AUTODL_GUARD_HOME/guard.log"
  use_clock
  expect "no shutdown follows" [ "$(tick_for 600)" = 0 ]
  teardown
}

# the budget ends detection even when nvidia-smi hangs, on a clock that runs or stands still (round 2, item 2)
t15_mode_detection_stays_within_its_budget() {
  local t0
  setup
  quiet g arm --idle 5m
  new_boot boot2
  to_gpu
  touch "$STUB_DIR/hang"
  t0=$SECONDS
  AUTODL_BOOT_MODE_BUDGET=4 timeout 30 bash "$GUARD" boot > /dev/null 2>&1
  expect "a hanging nvidia-smi: boot ends within its budget [$((SECONDS - t0)) s]" [ $((SECONDS - t0)) -le 10 ]
  rm -f "$STUB_DIR/hang"   # status asks nvidia-smi too
  expect "and cannot tell the mode" status_has '^autostart_this_boot=skipped:mode-unknown$'
  use_clock
  new_boot boot3
  touch "$STUB_DIR/hang"
  t0=$SECONDS
  AUTODL_BOOT_MODE_BUDGET=4 timeout 30 bash "$GUARD" boot > /dev/null 2>&1
  expect "on a clock that stands still too [$((SECONDS - t0)) s]" [ $((SECONDS - t0)) -le 10 ]
  rm -f "$STUB_DIR/hang"
  expect "with the same result" status_has '^autostart_this_boot=skipped:mode-unknown$'
  new_boot boot4
  echo 0 > "$STUB_DIR/gpu"   # nvidia-smi now fails at once, and the memory limit still tells no mode
  : > "$STUB_DIR/nvsmi_calls"
  t0=$SECONDS
  AUTODL_BOOT_MODE_BUDGET=3 AUTODL_BOOT_MODE_WAIT=1 timeout 30 bash "$GUARD" boot > /dev/null 2>&1
  expect "quick failures on a clock that stands still: the pauses use the budget up [$((SECONDS - t0)) s]" \
    [ $((SECONDS - t0)) -le 10 ]
  expect "after a few tries [$(lcalls)]" [ "$(lcalls)" -ge 2 ]
  expect "and cannot tell the mode" status_has '^autostart_this_boot=skipped:mode-unknown$'
  teardown
}

# a GPU whose driver answers late is still found within the budget (round 3, item 1)
t15_a_gpu_that_answers_late_is_still_found() {
  setup
  to_gpu
  quiet g arm --idle 5m
  new_boot boot2
  echo 2 > "$STUB_DIR/l_fail"
  : > "$STUB_DIR/nvsmi_calls"
  AUTODL_BOOT_MODE_BUDGET=10 AUTODL_BOOT_MODE_WAIT=1 timeout 30 bash "$GUARD" boot > /dev/null 2>&1
  expect "after 3 listings [$(lcalls)]" [ "$(lcalls)" = 3 ]
  expect "the GPU is found" status_has '^autostart_this_boot=armed:saved$'
  expect "in GPU mode" status_has '^mode=gpu$'
  teardown
}

# a probe that fails at once costs next to nothing even when the uptime shows no movement: /proc/uptime counts in
# 10 ms steps, so a quick probe often reads as no time at all (the stand-in review of round 4, item 1)
t15_quick_failures_on_a_still_clock_leave_the_budget_alone() {
  setup
  use_clock   # the uptime stands still, as it does across a probe shorter than 10 ms
  to_gpu
  quiet g arm --idle 5m
  new_boot boot2
  echo 2 > "$STUB_DIR/l_fail"
  : > "$STUB_DIR/nvsmi_calls"
  AUTODL_BOOT_MODE_BUDGET=10 AUTODL_BOOT_MODE_WAIT=1 timeout 30 bash "$GUARD" boot > /dev/null 2>&1
  expect "two quick failures, then the GPU on the third listing [$(lcalls)]" [ "$(lcalls)" = 3 ]
  expect "the GPU is found" status_has '^autostart_this_boot=armed:saved$'
  teardown
}

# nothing of the start environment reaches what boot runs: BASH_ENV and exported functions (bash -p keeps them out of
# the guard's own shells only; the nvidia-smi stub is a bash script, as a shutdown command may be)
t15_boot_leaves_the_start_environment_behind() {
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  printf 'echo ran >> "%s"\n' "$T/bash_env_ran" > "$T/bash_env"
  (
    eval "cat() { echo ran >> '$T/fn_ran'; command cat \"\$@\"; }"   # the path fixed now: T is not exported
    export -f cat
    BASH_ENV="$T/bash_env" bash -p "$GUARD" boot > /dev/null 2>&1   # -p, as the hook starts it
  )
  expect "boot arms" status_has '^autostart_this_boot=armed:saved$'
  expect "the nvidia-smi stub never read BASH_ENV" [ ! -e "$T/bash_env_ran" ]
  expect "nor took the exported function" [ ! -e "$T/fn_ran" ]
  teardown
}

t15_boot_settings_of_the_environment_are_checked() {
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  AUTODL_BOOT_MODE_BUDGET=abc AUTODL_BOOT_MODE_WAIT=0 AUTODL_BOOT_GATE_WAIT=-1 bash "$GUARD" boot > /dev/null 2>&1
  expect "a budget that is no number is ignored" grep -q 'BOOT ignores AUTODL_BOOT_MODE_BUDGET=\[abc\]' "$AUTODL_GUARD_HOME/guard.log"
  expect "so is a wait of 0" grep -q 'BOOT ignores AUTODL_BOOT_MODE_WAIT=\[0\]' "$AUTODL_GUARD_HOME/guard.log"
  expect "and a negative gate wait" grep -q 'BOOT ignores AUTODL_BOOT_GATE_WAIT=\[-1\]' "$AUTODL_GUARD_HOME/guard.log"
  expect "boot still arms" status_has '^autostart_this_boot=armed:saved$'
  new_boot boot3
  AUTODL_BOOT_MODE_BUDGET=060 bash "$GUARD" boot > /dev/null 2>&1
  expect "a leading zero is not taken either" grep -q 'BOOT ignores AUTODL_BOOT_MODE_BUDGET=\[060\]' "$AUTODL_GUARD_HOME/guard.log"
  teardown
}

t15_boot_without_the_uptime_arms_nothing() {
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  rm -f "$AUTODL_TEST_UPTIME"   # the uptime cannot be read
  : > "$STUB_DIR/nvsmi_calls"
  env -u AUTODL_NO_DAEMON timeout 20 bash "$GUARD" boot > /dev/null 2>&1
  expect "boot ends, with a daemon allowed, as it starts none [rc=$?]" [ "$?" = 0 ]
  expect "boot does not look for the mode" [ "$(lcalls)" = 0 ]   # before any status, which lists GPUs itself
  expect "and does not arm" status_has '^armed_this_boot=0$'
  expect "status says why" status_has '^autostart_this_boot=skipped:no-uptime$'
  expect "no daemon runs" status_has '^daemon_alive=0$'
  teardown
}

# an arm cut short, by the AI or by boot itself, stops the next start from arming (round 2 item 1, round 3 item 5)
t15_boot_after_a_failed_arm() {
  setup
  use_clock
  quiet g arm --idle 5m
  AUTODL_TEST_FAIL_PUT=armed_boot bash "$GUARD" arm --rearm --idle 6m --dry-run > /dev/null 2>&1
  new_boot boot2
  env -u AUTODL_NO_DAEMON timeout 20 bash "$GUARD" boot > /dev/null 2>&1
  expect "boot ends, with a daemon allowed, as it starts none [rc=$?]" [ "$?" = 0 ]
  expect "no daemon runs" status_has '^daemon_alive=0$'
  expect "after an arm cut short boot does not arm" status_has '^autostart_this_boot=skipped:incomplete$'
  expect "this boot is not armed" status_has '^armed_this_boot=0$'
  to_gpu
  quiet g arm --idle 7m   # a GPU arm drops the no-GPU settings the arm cut short may have written
  to_nogpu
  new_boot boot3
  quiet g boot
  expect "a no-GPU start after that takes the GPU arm's times" status_has '^autostart_this_boot=armed:fallback$'
  expect "its idle time" status_has '^idle_s=420$'
  expect "for real, not the dry run that was never committed" status_has '^dry_run=0$'
  teardown
  setup
  use_clock
  quiet g arm --idle 5m
  mkdir "$AUTODL_GUARD_HOME/state2/arm_incomplete"   # mv puts the new marker inside it, rm -f cannot remove it
  quiet g arm --rearm --idle 6m --dry-run
  new_boot boot2
  quiet g boot
  expect "the same when the marker of the arm could not be removed" status_has '^autostart_this_boot=skipped:incomplete$'
  teardown
  setup
  use_clock
  quiet g arm --idle 5m
  to_gpu
  quiet g arm --rearm --idle 7m
  to_nogpu
  new_boot boot2
  AUTODL_TEST_FAIL_PUT=armed_boot bash "$GUARD" boot > /dev/null 2>&1
  expect "boot's own arm cut short leaves who and in which mode [$(state arm_incomplete)]" \
    grep -Eq '^[1-9][0-9]* boot nogpu$' "$AUTODL_GUARD_HOME/state2/arm_incomplete"
  new_boot boot3
  quiet g boot
  expect "the next start does not arm either" status_has '^autostart_this_boot=skipped:incomplete$'
  quiet g arm --idle 8m
  expect "an arm then drops nothing: the GPU settings stay" [ "$(saved gpu)" = "420 120 60 3 5 50 500000 10000 default default - 0" ]
  expect "and it keeps its own" [ "$(saved nogpu)" = "480 120 60 0 5 30 500000 10000 default default - 0" ]
  teardown
}

# signals that cannot be read after boot count as in use (plan 4.1, design 5.8; round 1 of the plan review, item 9)
t15_unreadable_signals_after_boot_count_as_in_use() {
  setup
  use_clock 1000
  quiet g arm --idle 3m
  new_boot boot2
  mv "$STUB_DIR/cg/cpu.stat" "$STUB_DIR/cg/cpu.stat.away"
  quiet g boot
  expect "boot arms all the same" status_has '^autostart_this_boot=armed:saved$'
  expect "no shutdown while the CPU cannot be read" [ "$(tick_for 600)" = 0 ]
  expect "the CPU is unknown" [ "$(sig cpu)" = unknown ]
  expect "which is why it is in use" grep -q 'cpu:unknown' "$AUTODL_GUARD_HOME/state2/active_why"
  expect "and there is no countdown to a shutdown" status_has '^shutdown_in_s=$'
  write_counters   # the CPU counter can be read again
  expect "idle counts from then on" [ "$(tick_for 300)" = 10 ]
  teardown
}

t15_boot_never_replaces_an_arm_of_this_boot() {
  setup
  use_clock 1000
  quiet g arm --idle 5m
  new_boot boot2
  quiet g arm --idle 9m   # the AI arms the new start first
  quiet g boot
  expect "boot leaves the AI's arm" status_has '^idle_s=540$'
  expect "armed by the AI" status_has '^armed_by=arm$'
  expect "and says so" status_has '^autostart_this_boot=skipped:armed$'
  teardown
  setup
  use_clock 1000
  quiet g arm --idle 5m
  new_boot boot2
  quiet g boot
  adv 60
  quiet g boot
  expect "a second boot in one start changes nothing" [ "$(state last_active_up)" = 1000 ]
  expect "and says so" status_has '^autostart_this_boot=skipped:armed$'
  teardown
}

t15_an_arm_replaces_the_boot_arm_without_rearm() {
  setup
  use_clock
  quiet g arm --idle 7m
  new_boot boot2
  quiet g boot
  expect "an arm after boot's needs no --rearm" quiet g arm --idle 5m
  expect "armed by the AI now" status_has '^armed_by=arm$'
  expect "with its settings" status_has '^idle_s=300$'
  expect "a second arm of the AI is refused as before (exit 5)" [ "$(rc_of g arm --idle 6m)" = 5 ]
  teardown
}

# a shutdown committed before boot armed (an off-now needs no arm) is never cancelled by it (round 1, item 5)
t15_boot_keeps_a_shutdown_pending_in_this_boot() {
  local n
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  touch "$STUB_DIR/shutdown_fail_always"
  quiet g off-now --force --reason x
  expect "a shutdown of this boot is pending" [ "$(state shutdown_pending)" = 1 ]
  quiet g boot
  expect "boot leaves it pending" [ "$(state shutdown_pending)" = 1 ]
  expect "and says so" status_has '^autostart_this_boot=skipped:pending$'
  expect "without arming" status_has '^armed_this_boot=0$'
  rm -f "$STUB_DIR/shutdown_fail_always"
  n="$(wc -l < "$STUB_DIR/shutdown_calls")"
  adv 60
  quiet g tick
  expect "the next check retries it" [ "$(wc -l < "$STUB_DIR/shutdown_calls")" -gt "$n" ]
  teardown
}

# an off-now being prepared: boot waits for it, then decides again; the wait counts down on its own (round 3, item 2)
t15_boot_waits_out_the_gate() {
  local b w t0 rc
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  prep_now tok boot2
  bash "$GUARD" boot > /dev/null 2>&1 &
  b=$!
  expect "boot waits for the off-now being prepared" wait_for "$AUTODL_GUARD_HOME/guard.log" 'BOOT waits: an off-now is being prepared'
  expect "without arming meanwhile" status_has '^armed_this_boot=0$'
  rm -f "$AUTODL_GUARD_HOME/state2/prep"
  wait "$b"
  expect "once it is gone boot arms" status_has '^autostart_this_boot=armed:saved$'
  for w in 1 3; do   # the clock stands still, so the preparation never goes void
    new_boot "gate$w"
    prep_now tok "gate$w"
    t0=$SECONDS
    env -u AUTODL_NO_DAEMON AUTODL_BOOT_GATE_WAIT=$w timeout 20 bash "$GUARD" boot > /dev/null 2>&1
    rc=$?
    expect "a gate wait of $w s ends [$((SECONDS - t0)) s]" [ $((SECONDS - t0)) -le $((w + 3)) ]
    expect "a gate wait of $w s: boot ends with a daemon allowed, as it starts none [rc=$rc]" [ "$rc" = 0 ]
    expect "a gate wait of $w s: not armed" status_has '^autostart_this_boot=skipped:gated$'
    expect "a gate wait of $w s: no daemon runs" status_has '^daemon_alive=0$'
  done
  teardown
}

# whatever happened while the mode was being told is checked again, known mode or not (round 1 item 5, round 2 item 3)
boot_held() {  # boot_held [ENV=VALUE...]: start boot with the detected barrier held; BOOTPID is its PID
  rm -rf "$T/bb"   # a fresh barrier: an earlier boot's go marker must not let this one through
  bb_dir
  bb_hold detected
  env "$@" bash "$GUARD" boot > /dev/null 2>&1 &
  BOOTPID=$!
  bb_reached detected.1
}
boot_go() { bb_go detected.1; wait "$BOOTPID"; }   # let the held boot go on, and wait for it to end

t15_boot_rechecks_after_detecting_the_mode() {
  local u
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  boot_held
  quiet g arm --idle 9m
  boot_go
  expect "an AI arm meanwhile: boot leaves it" status_has '^autostart_this_boot=skipped:armed$'
  expect "with the AI's settings" status_has '^idle_s=540$'
  new_boot boot3
  touch "$STUB_DIR/shutdown_fail_always"
  boot_held
  quiet g off-now --force --reason x
  boot_go
  expect "an off-now committed meanwhile: boot leaves it pending" status_has '^autostart_this_boot=skipped:pending$'
  expect "it is still pending" [ "$(state shutdown_pending)" = 1 ]
  rm -f "$STUB_DIR/shutdown_fail_always"
  new_boot boot4
  boot_held
  hold_07_lock
  boot_go
  expect "a 0.7 daemon meanwhile: boot does not arm" status_has '^autostart_this_boot=skipped:07$'
  release_07_lock
  new_boot boot5
  boot_held
  rm -rf "$T/bb"
  unset AUTODL_TEST_BOOT_BARRIER
  quiet g boot   # a second boot that runs through first
  u="$(state last_active_up)"
  adv 30
  export AUTODL_TEST_BOOT_BARRIER="$T/bb"
  mkdir -p "$T/bb"
  touch "$T/bb/detected.1.go"
  wait "$BOOTPID"
  expect "a second boot meanwhile: the first leaves its arm" status_has '^autostart_this_boot=skipped:armed$'
  expect "which was written once" [ "$(state last_active_up)" = "$u" ]
  new_boot boot6
  boot_held
  prep_now tok boot6
  bb_go detected.1
  expect "an off-now prepared meanwhile: boot waits" wait_for "$AUTODL_GUARD_HOME/guard.log" 'BOOT waits'
  rm -f "$AUTODL_GUARD_HOME/state2/prep"
  wait "$BOOTPID"
  expect "and arms once it is gone" status_has '^autostart_this_boot=armed:saved$'
  echo 85899345920 > "$STUB_DIR/cgroup_mem"   # from here on the mode cannot be told
  new_boot boot7
  boot_held AUTODL_BOOT_MODE_BUDGET=1
  quiet g arm --idle 9m --mode nogpu   # the AI tells the mode it cannot find either
  boot_go
  expect "mode unknown, but an AI arm meanwhile: that decides" status_has '^autostart_this_boot=skipped:armed$'
  new_boot boot8
  touch "$STUB_DIR/shutdown_fail_always"
  boot_held AUTODL_BOOT_MODE_BUDGET=1
  quiet g off-now --force --reason x
  boot_go
  expect "mode unknown, but an off-now committed meanwhile: that decides" status_has '^autostart_this_boot=skipped:pending$'
  rm -f "$STUB_DIR/shutdown_fail_always"
  teardown
}

t15_boot_stops_next_to_07() {
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  hold_07_lock
  : > "$STUB_DIR/nvsmi_calls"
  unset AUTODL_NO_DAEMON
  timeout 20 bash "$GUARD" boot > /dev/null 2>&1
  expect "boot ends at once next to a live 0.7 daemon [rc=$?]" [ "$?" = 0 ]
  export AUTODL_NO_DAEMON=1
  expect "without looking for the mode" [ "$(lcalls)" = 0 ]
  expect "not armed, and says why" status_has '^autostart_this_boot=skipped:07$'
  expect "no 0.8 daemon" status_has '^daemon_alive=0$'
  release_07_lock
  teardown
}

t15_the_boot_arm_shuts_down_after_idle() {
  setup
  use_clock 1000
  quiet g arm --idle 3m
  new_boot boot2
  quiet g boot
  expect "3 idle minutes after boot it shuts down" [ "$(tick_for 180)" = 10 ]
  expect "calling shutdown once" [ "$(wc -l < "$STUB_DIR/shutdown_calls")" = 1 ]
  expect "for idleness" [ "$(fired_kind)" = idle ]
  teardown
}

t15_boot_becomes_the_daemon() {
  local b i
  setup
  quiet g arm --idle 60m
  new_boot boot2
  unset AUTODL_NO_DAEMON
  bash "$GUARD" boot > /dev/null 2>&1 &
  b=$!
  for i in $(seq 1 40); do status_has '^daemon_alive=1$' && break; sleep 0.25; done
  expect "boot runs on as the daemon" status_has '^daemon_alive=1$'
  expect "in the same process [$(state daemon_pid) vs $b]" [ "$(state daemon_pid)" = "$b" ]
  expect "the log says so" grep -q 'DAEMON start' "$AUTODL_GUARD_HOME/guard.log"
  quiet g revive --restart
  expect "revive --restart replaces it" [ "$(state daemon_pid)" != "$b" ]
  expect "and a daemon runs" status_has '^daemon_alive=1$'
  export AUTODL_NO_DAEMON=1
  teardown
}

# the autostart hook: install-autostart, uninstall-autostart, status (plan Task 4.3, design 5.8)
hookf() { printf '%s' "$AUTODL_TEST_PROFILE_D/autodl-autogpu-guard.sh"; }   # where the hook goes in these tests
oldhookf() { printf '%s' "$AUTODL_TEST_PROFILE_D/autodl-gpu-guard.sh"; }   # its name before the skill was renamed
old_hook() {  # old_hook: a hook as the guard up to 0.8 wrote it, under the former name (its first line is what counts)
  printf '%s\n' '# autodl-gpu guard autostart (written by install-autostart of autodl_guard.sh; its uninstall-autostart removes it)' \
    'if [ "${BASHPID:-0}" = 1 ] && [ "$0" = /init/boot/boot.sh ]; then :; fi' ':' > "$(oldhookf)"
}
wait_status() {  # wait_status PATTERN: wait (max 10 s) until status has a line matching PATTERN
  local i
  for i in $(seq 1 40); do
    status_has "$1" && return 0
    sleep 0.25
  done
  return 1
}

t15_install_writes_the_hook() {
  local out rc self want
  setup
  out="$(umask 077; g install-autostart 2>&1)"   # a tight umask: the mode must come from install itself
  rc=$?
  expect "install-autostart succeeds [rc=$rc]" [ "$rc" = 0 ]
  expect "and says so" grep -q 'autostart installed' <<< "$out"
  expect "its first line is the mark" [ "$(head -n 1 "$(hookf)" | cut -c1-32)" = "# autodl-autogpu guard autostart" ]
  self="$(readlink -f "$GUARD")"
  want="$(printf '%s\n' 'if [ "${BASHPID:-0}" = 1 ] && [ "$0" = /init/boot/boot.sh ]; then' \
    "    ( /usr/bin/env AUTODL_GUARD_HOME='$AUTODL_GUARD_HOME' PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin setsid /bin/bash -p -c 'i=0; until [ -r \"\$1\" ]; do i=\$((i + 1)); [ \"\$i\" -le 60 ] || exit 0; sleep 1; done; exec /bin/bash -p \"\$1\" boot' autodl-guard-boot '$self' < /dev/null > /dev/null 2>&1 & ) > /dev/null 2>&1 || :" \
    'fi' ':')"
  expect "after its five lines of comment, its code is the plan's word for word" [ "$(sed -n '6,$p' "$(hookf)")" = "$want" ]
  expect "readable by all, written by root only" [ "$(stat -c %a "$(hookf)")" = 644 ]
  expect "bash reads it" bash -n "$(hookf)"
  expect "dash too" dash -n "$(hookf)"
  expect "nothing else is left there" [ "$(ls -A "$AUTODL_TEST_PROFILE_D")" = autodl-autogpu-guard.sh ]
  expect "status says installed" status_has '^autostart=installed$'
  expect "the log says so" grep -q 'AUTOSTART installed' "$AUTODL_GUARD_HOME/guard.log"
  teardown
}

t15_install_again_changes_nothing_and_replaces_its_own_old_hook() {
  local ino out
  setup
  quiet g install-autostart
  ino="$(stat -c %i "$(hookf)")"
  out="$(g install-autostart 2>&1)"
  expect "a second install says it is there already" grep -q 'already installed' <<< "$out"
  expect "and leaves the file as it was" [ "$(stat -c %i "$(hookf)")" = "$ino" ]
  sed -i "s|AUTODL_GUARD_HOME='[^']*'|AUTODL_GUARD_HOME='/old/home'|" "$(hookf)"
  expect "our own hook for another home is stale" status_has '^autostart=stale$'
  quiet g install-autostart
  expect "installing again brings it up to date" status_has '^autostart=installed$'
  teardown
}

t15_a_foreign_file_is_left_alone() {
  local out rc
  setup
  printf 'echo not ours\n' > "$(hookf)"
  expect "status calls it foreign" status_has '^autostart=foreign$'
  out="$(g install-autostart 2>&1)"
  rc=$?
  expect "install refuses it [rc=$rc]" [ "$rc" = 1 ]
  expect "saying it is not ours" grep -q 'not written by this script' <<< "$out"
  out="$(g uninstall-autostart 2>&1)"
  rc=$?
  expect "so does uninstall [rc=$rc]" [ "$rc" = 1 ]
  expect "saying so too" grep -q 'not written by this script' <<< "$out"
  expect "and the file is as it was" [ "$(cat "$(hookf)")" = "echo not ours" ]
  teardown
}

t15_uninstall_removes_the_hook() {
  local out rc
  setup
  quiet g install-autostart
  expect "uninstall succeeds" quiet g uninstall-autostart
  expect "the hook is gone" [ ! -e "$(hookf)" ]
  expect "status says none" status_has '^autostart=none$'
  expect "the log says so" grep -q 'AUTOSTART uninstalled' "$AUTODL_GUARD_HOME/guard.log"
  out="$(g uninstall-autostart 2>&1)"
  rc=$?
  expect "again: exit 0 [rc=$rc]" [ "$rc" = 0 ]
  expect "saying it is not installed" grep -q 'not installed' <<< "$out"
  teardown
}

# the skill was called autodl-gpu once, and so was the hook: an instance set up then still has it under that name
t15_install_takes_over_from_the_hook_under_its_former_name() {
  local out rc
  setup
  old_hook
  expect "status calls our hook under the former name stale" status_has '^autostart=stale$'
  out="$(g install-autostart 2>&1)"
  rc=$?
  expect "install succeeds [rc=$rc]" [ "$rc" = 0 ]
  expect "the hook is there under the new name" status_has '^autostart=installed$'
  expect "the one under the former name is gone" [ ! -e "$(oldhookf)" ]
  expect "and install says so" grep -q "former name.*removed" <<< "$out"
  expect "nothing else is left there" [ "$(ls -A "$AUTODL_TEST_PROFILE_D")" = autodl-autogpu-guard.sh ]
  expect "the log says so" grep -q 'AUTOSTART removed the hook under its former name' "$AUTODL_GUARD_HOME/guard.log"
  old_hook   # it comes back, say with a system disk copied from an older instance: the next install removes it again
  out="$(g install-autostart 2>&1)"
  expect "an install that finds the new hook in place still removes it" [ ! -e "$(oldhookf)" ]
  expect "and says both" grep -q 'already installed' <<< "$out"
  expect "the hook itself is as it was" status_has '^autostart=installed$'
  teardown
}

t15_a_foreign_file_under_the_former_name_is_left_alone() {
  local out rc
  setup
  printf 'echo not ours\n' > "$(oldhookf)"
  expect "it does not count as a hook of ours" status_has '^autostart=none$'
  out="$(g install-autostart 2>&1)"
  rc=$?
  expect "install succeeds [rc=$rc]" [ "$rc" = 0 ]
  expect "the file is as it was" [ "$(cat "$(oldhookf)")" = "echo not ours" ]
  expect "and install says that it left it" grep -q "not written by this script" <<< "$out"
  quiet g uninstall-autostart
  expect "uninstall leaves it too" [ "$(cat "$(oldhookf)")" = "echo not ours" ]
  expect "and removes ours" [ ! -e "$(hookf)" ]
  teardown
}

t15_uninstall_removes_the_hook_under_its_former_name_too() {
  local out rc
  setup
  quiet g install-autostart
  old_hook
  out="$(g uninstall-autostart 2>&1)"
  rc=$?
  expect "uninstall succeeds [rc=$rc]" [ "$rc" = 0 ]
  expect "both are gone" [ -z "$(ls -A "$AUTODL_TEST_PROFILE_D")" ]
  expect "status says none" status_has '^autostart=none$'
  old_hook
  out="$(g uninstall-autostart 2>&1)"
  rc=$?
  expect "with only the one under the former name: it is removed [rc=$rc]" [ "$rc" = 0 ]
  expect "nothing is left" [ -z "$(ls -A "$AUTODL_TEST_PROFILE_D")" ]
  expect "and uninstall names it" grep -q "former name" <<< "$out"
  teardown
}

# install refuses what the hook cannot hold, before it makes or writes anything
t15_install_needs_profile_d_and_plain_paths() {
  local out rc
  setup
  rmdir "$AUTODL_TEST_PROFILE_D"
  out="$(g install-autostart 2>&1)"
  rc=$?
  expect "without the directory install refuses [rc=$rc]" [ "$rc" = 1 ]
  expect "saying why" grep -q 'does not exist' <<< "$out"
  expect "and makes nothing: no directory" [ ! -e "$AUTODL_TEST_PROFILE_D" ]
  expect "no guard home" [ ! -e "$AUTODL_GUARD_HOME" ]
  mkdir -p "$AUTODL_TEST_PROFILE_D"
  out="$(cd "$T" && AUTODL_GUARD_HOME=relhome bash "$GUARD" install-autostart 2>&1)"
  rc=$?
  expect "a relative guard home is refused [rc=$rc]" [ "$rc" = 1 ]
  expect "as not absolute" grep -q 'must be absolute' <<< "$out"
  expect "and is not made" [ ! -e "$T/relhome" ]
  mkdir -p "$T/it's"
  out="$(AUTODL_GUARD_HOME="$T/it's" bash "$GUARD" install-autostart 2>&1)"
  rc=$?
  expect "so is one with a quote in it [rc=$rc]" [ "$rc" = 1 ]
  expect "as holding a quote" grep -q 'quote or a newline' <<< "$out"
  out="$(AUTODL_GUARD_HOME="$T/two
lines" bash "$GUARD" install-autostart 2>&1)"
  rc=$?
  expect "and one with a newline [rc=$rc]" [ "$rc" = 1 ]
  expect "as holding a newline" grep -q 'quote or a newline' <<< "$out"
  mkdir -p "$T/q'd"
  cp "$GUARD" "$T/q'd/g.sh"
  out="$(bash "$T/q'd/g.sh" install-autostart 2>&1)"
  rc=$?
  expect "a guard script whose path has a quote is refused too [rc=$rc]" [ "$rc" = 1 ]
  expect "for the same reason" grep -q 'quote or a newline' <<< "$out"
  expect "and nothing was written" [ -z "$(ls -A "$AUTODL_TEST_PROFILE_D")" ]
  teardown
}

# the paths go into the hook as they are, whatever else they hold: spaces, a &, a $, the hook's own placeholders
t15_install_takes_paths_as_they_are() {
  local d h self p
  setup
  d="$T/x @GH@ & \$y"
  h="$T/h @SELF@ @WAIT@ & \$z"
  mkdir -p "$d" "$h"
  cp "$GUARD" "$d/g.sh"
  self="$(readlink -f "$d/g.sh")"
  export AUTODL_GUARD_HOME="$h"
  quiet bash "$d/g.sh" install-autostart
  expect "the home goes in as it is" grep -qF "AUTODL_GUARD_HOME='$h' PATH=" "$(hookf)"
  expect "so does the script" grep -qF "autodl-guard-boot '$self' <" "$(hookf)"
  expect "the wait is 60 all the same" grep -qF '[ "$i" -le 60 ]' "$(hookf)"
  expect "bash reads it" bash -n "$(hookf)"
  expect "and it reads back as installed" grep -q '^autostart=installed$' <(bash "$d/g.sh" status)
  if have_pid1_ns; then
    quiet bash "$d/g.sh" arm --idle 5m
    new_boot boot2
    as_pid1 ". '$(hookf)'; i=0; while [ ! -e '$T/done' ] && [ \$i -lt 300 ]; do sleep 0.1; i=\$((i + 1)); done" &
    p=$!
    expect "as process 1 it starts boot with these paths, which arms" wait_status '^autostart_this_boot=armed:saved$'
    touch "$T/done"
    wait "$p"
  else
    echo "SKIP the hook with these paths as process 1: no PID namespace can be made here"
  fi
  teardown
}

t15_install_says_when_there_is_nothing_to_arm_with() {
  local out
  setup
  out="$(g install-autostart 2>&1)"
  expect "install works before any arm" status_has '^autostart=installed$'
  expect "but says the first arm is still to come" grep -q 'no arm has kept settings yet' <<< "$out"
  quiet g arm --idle 5m
  out="$(g install-autostart 2>&1)"
  expect "after an arm it says no such thing" fails grep -q 'no arm has kept' <<< "$out"
  teardown
}

t15_status_shows_the_autostart_keys() {
  local k d1 d2 out
  setup
  use_clock
  for k in autostart armed_by boot_settings autostart_this_boot; do
    expect "status has $k" status_has "^$k="
  done
  expect "not armed: armed_by is empty" status_has '^armed_by=$'
  expect "nothing kept yet: boot_settings is empty" status_has '^boot_settings=$'
  quiet g arm --idle 5m
  to_gpu
  quiet g arm --rearm --idle 7m
  to_nogpu
  expect "both modes' settings are listed, with their idle times" status_has '^boot_settings=gpu:420s nogpu:300s$'
  out="$(g install-autostart 2>&1)"
  expect "install lists them too [$out]" grep -q 'gpu:420s nogpu:300s' <<< "$out"
  expect "with no fallback to explain" fails grep -q 'fallback' <<< "$out"
  printf 'bad' > "$AUTODL_GUARD_HOME/state2/boot.gpu"
  expect "a mode whose own are bad falls back to the other's, as boot does (round 2, item 3)" \
    status_has '^boot_settings=gpu:300s:fallback nogpu:300s$'
  printf 'bad' > "$AUTODL_GUARD_HOME/state2/boot.nogpu"
  expect "with neither usable, nothing" status_has '^boot_settings=$'
  quiet g install-autostart
  d1="$(state_digest; md5sum < "$AUTODL_GUARD_HOME/guard.log"; md5sum < "$(hookf)")"
  quiet g status
  d2="$(state_digest; md5sum < "$AUTODL_GUARD_HOME/guard.log"; md5sum < "$(hookf)")"
  expect "status writes nothing, not to the log, not to the hook" [ "$d1" = "$d2" ]
  teardown
}

# the test-only wait of the hook is checked, and read only in tests (round 3 of the plan review, item 4)
t15_install_checks_the_test_wait() {
  local v rc out sum
  setup
  quiet g install-autostart
  sum="$(md5sum < "$(hookf)")"
  for v in abc 0 61 07 '1;touch x'; do
    out="$(AUTODL_TEST_HOOK_WAIT="$v" bash "$GUARD" install-autostart 2>&1)"
    rc=$?
    expect "a test wait of [$v] is refused [rc=$rc]" [ "$rc" = 1 ]
    expect "[$v] as a bad test wait" grep -q 'AUTODL_TEST_HOOK_WAIT' <<< "$out"
    expect "[$v] leaves the hook as it was" [ "$(md5sum < "$(hookf)")" = "$sum" ]
  done
  AUTODL_TEST_HOOK_WAIT=2 bash "$GUARD" install-autostart > /dev/null 2>&1
  expect "a test wait of 2 goes in" grep -qF '[ "$i" -le 2 ]' "$(hookf)"
  expect "without the test profile.d it is always 60" \
    grep -qF '[ "$i" -le 60 ]' <(env -u AUTODL_TEST_PROFILE_D AUTODL_TEST_HOOK_WAIT=2 bash "$GUARD" _hook_text)
  teardown
}

# the hook as AutoDL's process 1 sources it: a bash that is process 1 of a new PID namespace, $0 its start script.
# Barriers and xtrace show what it does, not fixed waits (round 1 of the plan review item 10, round 2 item 4)
t15_the_hook_starts_boot_only_as_the_container_start_script() {
  local p rc
  if ! have_pid1_ns; then echo "SKIP the hook as process 1: no PID namespace can be made here"; return 0; fi
  setup
  quiet g arm --idle 5m
  quiet g install-autostart
  new_boot boot2
  bb_dir
  bb_hold start
  as_pid1 "exec {fd}> '$T/trace0'; BASH_XTRACEFD=\$fd; set -x; . '$(hookf)'; set +x; echo returned > '$T/returned'; i=0; while [ ! -e '$T/done' ] && [ \$i -lt 300 ]; do sleep 0.1; i=\$((i + 1)); done" &
  p=$!
  expect "sourcing the hook as process 1 returns" wait_file "$T/returned"
  expect "while the boot it started waits at its start" bb_reached start.1
  expect "the trace shows it was started" grep -q setsid "$T/trace0"
  bb_go start.1
  expect "boot then arms" wait_status '^autostart_this_boot=armed:saved$'
  touch "$T/done"
  wait "$p"
  new_boot boot3
  # the bash and the dash below have the start script as $0, so only the process 1 half of the condition keeps
  # them out; process 1 in between has another $0
  bash -c "exec {fd}> '$T/trace1'; BASH_XTRACEFD=\$fd; set -x; . '$(hookf)'; set +x" /init/boot/boot.sh > "$T/out1" 2>&1
  rc=$?
  expect "a bash that is not process 1: sourcing returns 0 [rc=$rc]" [ "$rc" = 0 ]
  expect "prints nothing" [ ! -s "$T/out1" ]
  expect "tests the condition" grep -qF ' = 1 ' "$T/trace1"
  expect "and starts nothing" fails grep -q setsid "$T/trace1"
  unshare -rpf --mount-proc bash -c "exec {fd}> '$T/trace2'; BASH_XTRACEFD=\$fd; set -x; . '$(hookf)'; set +x" /usr/bin/other > "$T/out2" 2>&1
  rc=$?
  expect "process 1 of another start script: returns 0 [rc=$rc]" [ "$rc" = 0 ]
  expect "prints nothing either" [ ! -s "$T/out2" ]
  expect "tests the start script" grep -qF '/usr/bin/other' "$T/trace2"
  expect "and starts nothing" fails grep -q setsid "$T/trace2"
  dash -c "set -x; . '$(hookf)'" /init/boot/boot.sh > "$T/out3" 2> "$T/trace3"
  rc=$?
  expect "dash: returns 0 [rc=$rc]" [ "$rc" = 0 ]
  expect "tests the condition" grep -qF '[ 0 = 1 ]' "$T/trace3"
  expect "and starts nothing" fails grep -q setsid "$T/trace3"
  expect "none of them armed boot3" status_has '^armed_this_boot=0$'
  teardown
}

t15_the_hook_changes_nothing_in_process_1() {
  local p
  if ! have_pid1_ns; then echo "SKIP the hook as process 1: no PID namespace can be made here"; return 0; fi
  setup
  quiet g arm --idle 5m
  quiet g install-autostart
  new_boot boot2
  as_pid1 "set -eu; op=\$PATH; PATH=/nonexistent; unset AUTODL_GUARD_HOME; o1=\"\$- \$(set -o; shopt -p)\"; . '$(hookf)' > '$T/hookout' 2>&1; o2=\"\$- \$(set -o; shopt -p)\"; r=''; [ \"\$o1\" = \"\$o2\" ] && r=\"\$r same-options\"; [ \"\$PATH\" = /nonexistent ] && r=\"\$r same-path\"; [ -z \"\${AUTODL_GUARD_HOME+x}\" ] && r=\"\$r no-home\"; PATH=\$op; echo \"\$r alive\" > '$T/r1'; i=0; while [ ! -e '$T/done' ] && [ \$i -lt 300 ]; do sleep 0.1; i=\$((i + 1)); done" &
  p=$!
  expect "process 1 runs on after sourcing it, under set -eu" wait_file "$T/r1"
  expect "with its options as they were [$(cat "$T/r1" 2> /dev/null)]" grep -q same-options "$T/r1"
  expect "its PATH as it was" grep -q same-path "$T/r1"
  expect "and no AUTODL_GUARD_HOME of its own" grep -q no-home "$T/r1"
  expect "sourcing printed nothing" [ ! -s "$T/hookout" ]
  expect "boot ran all the same, with PATH broken in process 1" wait_status '^autostart_this_boot=armed:saved$'
  touch "$T/done"
  wait "$p"
  teardown
}

# BASH_ENV and exported functions of process 1 reach neither the detached shell nor what boot runs, the stubs included
# (round 3 of the plan review item 3, round 4 item 2); the guard script arrives late, so the wait's loop runs too
t15_the_hook_keeps_the_start_environment_out() {
  local p g2
  if ! have_pid1_ns; then echo "SKIP the hook as process 1: no PID namespace can be made here"; return 0; fi
  setup
  g2="$T/g.sh"
  cp "$GUARD" "$g2"
  quiet bash "$g2" arm --idle 5m
  quiet bash "$g2" install-autostart   # the hook names the copy
  mv "$g2" "$T/g.away"                 # which is not there yet at the start
  printf 'echo ran >> "%s"\nexit 1\n' "$T/bash_env_ran" > "$T/bash_env"
  new_boot boot2
  as_pid1 "export BASH_ENV='$T/bash_env'; sleep() { echo ran >> '$T/fn_ran'; command sleep \"\$@\"; }; export -f sleep; . '$(hookf)'; echo returned > '$T/returned'; i=0; while [ ! -e '$T/done' ] && [ \$i -lt 300 ]; do command sleep 0.1; i=\$((i + 1)); done" &
  p=$!
  expect "sourcing returns though the guard script is not there" wait_file "$T/returned"
  expect "a shell waits for it" quiet pgrep -f "autodl-guard-boot $g2"
  sleep 2   # its loop goes round a few times
  mv "$T/g.away" "$g2"
  expect "boot arms once the script is there" wait_status '^autostart_this_boot=armed:saved$'
  expect "BASH_ENV never ran" [ ! -e "$T/bash_env_ran" ]
  expect "nor the exported function" [ ! -e "$T/fn_ran" ]
  touch "$T/done"
  wait "$p"
  teardown
}

# the detached shell waits for a guard script that is not there yet, and gives up by itself on one that never comes
t15_the_hook_waits_for_a_late_guard_script() {
  local p i gone="" loop
  if ! have_pid1_ns; then echo "SKIP the hook as process 1: no PID namespace can be made here"; return 0; fi
  setup
  loop="i=0; while [ ! -e '$T/done' ] && [ \$i -lt 300 ]; do sleep 0.1; i=\$((i + 1)); done"
  cp "$GUARD" "$T/g.sh"
  quiet bash "$T/g.sh" arm --idle 5m
  quiet bash "$T/g.sh" install-autostart
  mv "$T/g.sh" "$T/g.away"
  new_boot boot2
  as_pid1 ". '$(hookf)'; echo returned > '$T/returned'; $loop" > "$T/pid1_out" 2>&1 &
  p=$!
  expect "sourcing returns though the guard script is not there" wait_file "$T/returned"
  expect "a shell waits for it" quiet pgrep -f "autodl-guard-boot $T/g.sh"
  sleep 2   # a wait of its loop
  expect "no result while it is away" status_has '^autostart_this_boot=$'
  mv "$T/g.away" "$T/g.sh"
  expect "once it is there, boot arms" wait_status '^autostart_this_boot=armed:saved$'
  touch "$T/done"
  wait "$p"
  rm -f "$T/done" "$T/returned"
  AUTODL_TEST_HOOK_WAIT=2 bash "$T/g.sh" install-autostart > /dev/null 2>&1
  expect "a hook that waits 2 s" grep -qF '[ "$i" -le 2 ]' "$(hookf)"
  rm -f "$T/g.sh"
  new_boot boot3
  as_pid1 ". '$(hookf)'; echo returned > '$T/returned'; $loop" > "$T/pid1_out2" 2>&1 &
  p=$!
  expect "a script that never comes: sourcing returns" wait_file "$T/returned"
  expect "a shell waits for it" quiet pgrep -f "autodl-guard-boot $T/g.sh"
  for i in $(seq 1 40); do
    pgrep -f "autodl-guard-boot $T/g.sh" > /dev/null || { gone=1; break; }
    sleep 0.25
  done
  expect "and gives up by itself" [ -n "$gone" ]
  expect "while process 1 lives on" kill -0 "$p"
  expect "nothing was armed" status_has '^armed_this_boot=0$'
  expect "no result" status_has '^autostart_this_boot=$'
  touch "$T/done"
  wait "$p"
  expect "nothing was printed the first time" [ ! -s "$T/pid1_out" ]
  expect "nor the second" [ ! -s "$T/pid1_out2" ]
  teardown
}

# boot runs the daemon of a start that is armed already, or has a shutdown pending, when none runs; the other results
# start none (review of the Phase 4 code, finding 1)
t15_boot_runs_the_daemon_of_an_armed_or_pending_start() {
  local b i n
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  quiet g arm --idle 9m   # the AI armed this start; no daemon runs (AUTODL_NO_DAEMON)
  expect "no daemon before boot" status_has '^daemon_alive=0$'
  env -u AUTODL_NO_DAEMON bash "$GUARD" boot > /dev/null 2>&1 &
  b=$!
  expect "boot leaves the AI's arm" wait_status '^autostart_this_boot=skipped:armed$'
  expect "and runs on as its daemon" wait_status '^daemon_alive=1$'
  expect "in the same process [$(state daemon_pid) vs $b]" [ "$(state daemon_pid)" = "$b" ]
  expect "with the AI's settings" status_has '^idle_s=540$'
  teardown
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  touch "$STUB_DIR/shutdown_fail_always"
  quiet g off-now --force --reason x   # pending in this start, and no daemon runs to retry it
  rm -f "$STUB_DIR/shutdown_fail_always"
  n="$(wc -l < "$STUB_DIR/shutdown_calls")"
  env -u AUTODL_NO_DAEMON bash "$GUARD" boot > /dev/null 2>&1 &
  b=$!
  expect "boot leaves the shutdown pending" wait_status '^autostart_this_boot=skipped:pending$'
  for i in $(seq 1 40); do
    [ "$(wc -l < "$STUB_DIR/shutdown_calls")" -gt "$n" ] && break
    sleep 0.25
  done
  expect "and runs on as the daemon, which retries it at once [$n, then $(wc -l < "$STUB_DIR/shutdown_calls")]" \
    [ "$(wc -l < "$STUB_DIR/shutdown_calls")" -gt "$n" ]
  expect "in the same process [$(state daemon_pid) vs $b]" [ "$(state daemon_pid)" = "$b" ]
  teardown
}

# the uptime is read when boot starts and again when it arms: lost in between, nothing is armed and no daemon is
# started (review, finding 2; round 2, item 1: run as at a real start, where a daemon would be started)
t15_boot_arms_nothing_when_the_uptime_goes_before_the_arm() {
  local rc
  setup
  use_clock
  quiet g arm --idle 5m
  new_boot boot2
  boot_held -u AUTODL_NO_DAEMON timeout 20
  rm -f "$AUTODL_TEST_UPTIME"
  boot_go
  rc=$?
  expect "boot ends at once [rc=$rc]" [ "$rc" = 0 ]
  expect "not armed" status_has '^armed_this_boot=0$'
  expect "status says why" status_has '^autostart_this_boot=skipped:no-uptime$'
  expect "and no arm is left half done" [ ! -e "$AUTODL_GUARD_HOME/state2/arm_incomplete" ]
  expect "no daemon runs" status_has '^daemon_alive=0$'
  teardown
}

# without the boot marker boot does nothing: no arm, no result, no daemon, a line in the log (review, finding 2)
t15_boot_without_the_boot_marker_does_nothing() {
  setup
  use_clock
  quiet g arm --idle 5m
  mkdir -p "$T/noproc"   # no 1/stat in it
  env -u AUTODL_BOOT_MARKER -u AUTODL_NO_DAEMON AUTODL_TEST_PROC="$T/noproc" timeout 20 bash "$GUARD" boot \
    > /dev/null 2>&1
  expect "boot ends at once [rc=$?]" [ "$?" = 0 ]
  expect "the log says why" grep -q "BOOT does not arm: cannot read this boot's marker" "$AUTODL_GUARD_HOME/guard.log"
  expect "no result is written" [ ! -e "$AUTODL_GUARD_HOME/state2/autostart" ]
  expect "the arm of the start before is as it was" [ "$(state armed_boot)" = boot1 ]
  expect "no daemon runs" status_has '^daemon_alive=0$'
  teardown
}

# install checks what the hook will run: this script as a readable file, /usr/bin/env and /bin/bash, and setsid and
# sleep on the hook's PATH (review of the Phase 4 code, finding 4), and flock and timeout there, which boot needs
# (round 2, items 2 and 4); those two are also on the installer's PATH here, so only the hook's check can refuse
t15_install_checks_what_the_hook_runs() {
  local out rc p
  setup
  out="$(cd "$T" && bash -s install-autostart < "$GUARD" 2>&1)"   # read from stdin: no file of its own
  rc=$?
  expect "a guard with no file of its own is refused [rc=$rc]" [ "$rc" = 1 ]
  expect "saying so" grep -q 'not a readable file' <<< "$out"
  expect "and nothing is written" [ -z "$(ls -A "$AUTODL_TEST_PROFILE_D")" ]
  if unshare -rm true 2> /dev/null; then   # hide a program: /dev/null bound over it, in a namespace of our own
    out="$(unshare -rm bash -c 'mount --bind /dev/null /usr/bin/setsid && exec bash "$1" install-autostart' _ "$GUARD" 2>&1)"
    rc=$?
    expect "without setsid on the hook's PATH it is refused [rc=$rc]" [ "$rc" = 1 ]
    expect "naming it [$out]" grep -q 'setsid' <<< "$out"
    out="$(unshare -rm bash -c 'mount --bind /dev/null /usr/bin/env && exec bash "$1" install-autostart' _ "$GUARD" 2>&1)"
    rc=$?
    expect "without /usr/bin/env too [rc=$rc]" [ "$rc" = 1 ]
    expect "naming it [$out]" grep -q '/usr/bin/env' <<< "$out"
    out="$(unshare -rm bash -c 'mount --bind /dev/null /usr/bin/sleep && exec bash "$1" install-autostart' _ "$GUARD" 2>&1)"
    rc=$?
    expect "without sleep too [rc=$rc]" [ "$rc" = 1 ]
    expect "naming it [$out]" grep -q 'missing: .*sleep' <<< "$out"
    mkdir -p "$T/xbin"
    cp /usr/bin/flock /usr/bin/timeout "$T/xbin/"   # copies, off the hook's PATH: they stay when the originals go
    for p in flock timeout; do
      out="$(PATH="$T/xbin:$PATH" unshare -rm bash -c 'mount --bind /dev/null "/usr/bin/$2" && exec bash "$1" install-autostart' _ "$GUARD" "$p" 2>&1)"
      rc=$?
      expect "with $p on the installer's PATH but not the hook's, it is refused [rc=$rc]" [ "$rc" = 1 ]
      expect "naming it [$out]" grep -q "missing: .*$p" <<< "$out"
    done
    expect "and nothing is written any time" [ -z "$(ls -A "$AUTODL_TEST_PROFILE_D")" ]
  else
    echo "SKIP hiding a program from the hook: no user and mount namespace can be made here"
  fi
  teardown
}

# status and install show what the next start in each mode arms with, its idle time and a dry run, and nothing while
# an arm is unfinished, as boot then arms nothing (review of the Phase 4 code, findings 3 and 6); a mode that kept
# nothing is shown as falling back to the other mode's times and dry run, as boot does (round 2, item 3)
t15_boot_settings_show_what_the_next_start_uses() {
  local out
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  expect "a dry run is marked, and the mode that kept nothing falls back to it" \
    status_has '^boot_settings=gpu:120s:fallback:dry-run nogpu:120s:dry-run$'
  out="$(g install-autostart 2>&1)"
  expect "install says what the next start arms with [$out]" grep -q 'gpu:120s:fallback:dry-run nogpu:120s:dry-run' <<< "$out"
  expect "and what the fallback is" grep -q 'fallback: ' <<< "$out"
  echo "123 arm nogpu" > "$AUTODL_GUARD_HOME/state2/arm_incomplete"
  expect "while an arm is unfinished none count" status_has '^boot_settings=$'
  out="$(g install-autostart 2>&1)"
  expect "and install says why" grep -q 'cut short' <<< "$out"
  teardown
}

# a hook text that cannot be built (its here-document fails) is refused as such, not blamed on the paths (review,
# finding 7); an exported read that fails stands in for the here-document bash could not make
t15_install_tells_a_failed_hook_text_apart() {
  local out rc
  setup
  out="$(read() { return 1; }; export -f read; bash "$GUARD" install-autostart 2>&1)"
  rc=$?
  expect "refused [rc=$rc]" [ "$rc" = 1 ]
  expect "saying the hook text could not be built [$out]" grep -q 'cannot build the hook' <<< "$out"
  expect "not blaming the paths" fails grep -q 'quote or a newline' <<< "$out"
  expect "and nothing is written" [ -z "$(ls -A "$AUTODL_TEST_PROFILE_D")" ]
  teardown
}

# ---- group 16: what ctl v0.8 needs of the guard (plan Phase 5) ----
boot_armed_start() {  # boot_armed_start [ARM OPTION...]: setup, an AI arm in boot1 (which keeps its settings), then
  # a new start that boot arms from them: armed_by=boot, no env_setup
  setup
  use_clock
  quiet g arm --idle 5m "$@"
  new_boot boot2
  quiet bash "$GUARD" boot
}

# a start armed by autostart has no env_setup, so run is refused until the AI arms; the rest works (plan 5.1)
t16_run_is_refused_in_a_boot_armed_start() {
  local out rc
  boot_armed_start
  expect "boot armed this start" status_has '^armed_by=boot$'
  out="$(g run t1 -- true 2>&1)"
  rc=$?
  expect "run is refused [rc=$rc]" [ "$rc" = 1 ]
  expect "saying why [$out]" grep -q 'armed at container start' <<< "$out"
  expect "no job is registered" [ ! -e "$AUTODL_GUARD_HOME/jobs/t1" ]
  expect "keep works" quiet g keep 5m --reason x
  expect "deadline works" quiet g deadline 30m
  expect "off-when-done works" quiet g off-when-done --reason x
  quiet g arm --idle 5m
  expect "after the AI's arm the same run starts" quiet g run t1 -- true
  teardown
}

# the refusal under a boot arm comes after the gate (8), a pending shutdown (4) and the deadline (7) (plan review
# round 1, finding 14)
t16_a_boot_arm_keeps_the_order_of_refusals() {
  boot_armed_start
  prep_now other boot2
  expect "gated: 8" [ "$(rc_of g run t1 -- true)" = 8 ]
  teardown
  boot_armed_start --dry-run
  quiet g off-now --reason x   # a dry run: the shutdown stays pending in this start
  expect "a shutdown is pending" [ "$(state shutdown_pending)" = 1 ]
  expect "pending: 4" [ "$(rc_of g run t1 -- true)" = 4 ]
  teardown
  boot_armed_start
  quiet g deadline 2m
  adv 180
  expect "past the deadline: 7" [ "$(rc_of g run t1 -- true)" = 7 ]
  teardown
}

# ---- group 17 (0.8): after the review of the whole release (docs/reviews/2026-10-01-codex-phase7-triage.md) ----
# idle-check is off-raw's live sample (finding 5): the judgement of off-now's sample, read-only, and it runs from
# stdin too (ctl sends the script over, so an instance without a guard, or with an older one, is judged alike)
t17_idle_check_passes_on_an_idle_instance() {
  setup
  use_clock
  local out rc
  out="$(g idle-check 2>&1)"
  rc=$?
  expect "nothing in use: exit 0" [ "$rc" = 0 ]
  expect "it says idle and shows the signals" grep -q '^idle: gpu=na: cpu=idle:' <<< "$out"
  expect "it made nothing, not even the guard's directory" [ ! -e "$AUTODL_GUARD_HOME" ]
  teardown
}

t17_idle_check_refuses_activity_seen_while_it_samples() {
  setup
  use_clock
  ob_dir
  ob_hold sample-started
  g idle-check > "$T/out" 2>&1 &
  local pid=$!
  ob_reached sample-started.1
  busy_net 5000000
  ob_go sample-started.1
  wait "$pid"
  expect "activity while it samples: exit 3" [ "$?" = 3 ]
  expect "it says what that means" grep -q '^in use or cannot tell' "$T/out"
  expect "and names the signal" grep -q '^net:busy' "$T/out"
  teardown
}

t17_idle_check_refuses_what_it_cannot_read() {
  setup
  use_clock
  rm -f "$STUB_DIR/cg/cpu.stat"
  g idle-check > "$T/out" 2>&1
  expect "a counter that cannot be read: exit 3" [ "$?" = 3 ]
  expect "named as unknown" grep -q '^cpu:unknown' "$T/out"
  teardown
  setup
  use_clock
  echo 1 > "$STUB_DIR/gpu"           # GPU mode
  echo fail > "$STUB_DIR/util_seq"   # the one probe of a 1 s sample fails
  g idle-check > "$T/out" 2>&1
  expect "a GPU probe that fails: exit 3" [ "$?" = 3 ]
  expect "named as unknown" grep -q '^gpu:unknown' "$T/out"
  teardown
  setup
  export AUTODL_TEST_UPTIME="$STUB_DIR/no-such-uptime"
  g idle-check > "$T/out" 2>&1
  expect "without the uptime nothing can be timed: exit 3" [ "$?" = 3 ]
  expect "and says so" grep -q '^uptime:unknown' "$T/out"
  teardown
}

t17_idle_check_judges_by_the_arm_of_this_boot() {
  setup
  use_clock
  quiet g arm --idle 60m --thr-net 9000000 --dry-run   # the network counts from 9 MB/s here
  ob_dir
  ob_hold sample-started
  g idle-check > "$T/out" 2>&1 &
  local pid=$!
  ob_reached sample-started.1
  busy_net 5000000                                      # 5 MB in the 1 s sample: under the arm's own threshold
  ob_go sample-started.1
  wait "$pid"
  expect "under the threshold of this boot's arm: idle" [ "$?" = 0 ]
  teardown
  setup
  use_clock
  quiet g arm --idle 60m --unreliable cpu,io,net --dry-run   # non-GPU mode: no signal can show idleness
  g idle-check > "$T/out" 2>&1
  expect "no signal both applies and is reliable: exit 3" [ "$?" = 3 ]
  expect "and says so" grep -q 'no signal both applies and is reliable' "$T/out"
  teardown
}

t17_idle_check_runs_from_stdin_and_needs_no_flock() {
  setup
  use_clock
  local rc
  AUTODL_FLOCK_CMD="$T/no-flock" AUTODL_TIMEOUT_CMD="$T/no-timeout" bash -s -- idle-check < "$GUARD" > "$T/out" 2>&1
  rc=$?
  expect "sent over stdin, with neither flock nor timeout: exit 0" [ "$rc" = 0 ]
  expect "idle" grep -q '^idle: ' "$T/out"
  expect "it made nothing" [ ! -e "$AUTODL_GUARD_HOME" ]
  expect "a bad --sample is refused" [ "$(rc_of g idle-check --sample 0)" = 1 ]
  expect "one over the limit too" [ "$(rc_of g idle-check --sample 61)" = 1 ]
  expect "and an unknown option" [ "$(rc_of g idle-check --force)" = 1 ]
  teardown
}

# a shutdown already issued, once the state cannot be used (finding 6): one that was not forced goes ahead only
# while a live sample shows nothing in use, as it does in a state that can be used (t8_pending_retry_rechecks_activity)
t17_an_issued_shutdown_is_cancelled_when_in_use_though_the_state_cannot_be_used() {
  setup
  use_clock
  quiet g arm --idle 2m
  echo 1 > "$STUB_DIR/shutdown_fail"
  expect "idle: a shutdown is attempted and stays pending" [ "$(tick_for 180)" = 10 ]
  rm -f "$AUTODL_GUARD_HOME/state2/schema"   # from here on the state cannot be used
  : > "$STUB_DIR/shutdown_calls"
  ob_dir
  ob_hold sample-started
  g tick > /dev/null 2>&1 &
  local pid=$!
  ob_reached sample-started.1
  busy_cpu 5000000000                        # work someone started by hand
  ob_go sample-started.1
  wait "$pid"
  expect "in use again: this check fires nothing" [ "$?" = 0 ]
  expect "the shutdown command was not called" [ ! -s "$STUB_DIR/shutdown_calls" ]
  expect "the pending shutdown is cancelled" [ "$(state shutdown_pending)" = 0 ]
  expect "and the log says why" grep -q 'PENDING SHUTDOWN CANCELLED kind=idle: in use again \[cpu:busy\]' "$AUTODL_GUARD_HOME/guard.log"
  teardown
}

t17_an_issued_shutdown_is_retried_while_idle_and_a_forced_one_always() {
  setup
  use_clock
  quiet g arm --idle 2m
  echo 1 > "$STUB_DIR/shutdown_fail"
  expect "idle: a shutdown is attempted and stays pending" [ "$(tick_for 180)" = 10 ]
  rm -f "$AUTODL_GUARD_HOME/state2/schema"
  : > "$STUB_DIR/shutdown_calls"
  ob_dir
  expect "nothing in use: it is retried" [ "$(tick_rc)" = 10 ]
  expect "after a live sample" [ -e "$T/bar/sample-started.1" ]
  expect "the shutdown command was called" [ -s "$STUB_DIR/shutdown_calls" ]
  teardown
  setup
  use_clock
  quiet g arm --idle 60m
  echo 1 > "$STUB_DIR/shutdown_fail"
  quiet g off-now --force --reason test
  expect "a forced shutdown failed and is pending" [ "$(state shutdown_kind)$(state shutdown_pending)" = forced1 ]
  rm -f "$AUTODL_GUARD_HOME/state2/schema"
  : > "$STUB_DIR/shutdown_calls"
  ob_dir
  busy_cpu 5000000000
  expect "a forced one is retried as it is" [ "$(tick_rc)" = 10 ]
  expect "without a sample" [ ! -e "$T/bar/sample-started.1" ]
  expect "the shutdown command was called" [ -s "$STUB_DIR/shutdown_calls" ]
  teardown
}

t17_an_off_now_on_a_start_nobody_armed_is_retried_only_while_idle() {
  setup
  use_clock
  echo 1 > "$STUB_DIR/shutdown_fail"
  quiet g off-now --reason test              # not armed: it commits, and its shutdown command fails
  expect "the off-now shutdown is pending" [ "$(state shutdown_kind)$(state shutdown_pending)" = now1 ]
  : > "$STUB_DIR/shutdown_calls"
  ob_dir
  ob_hold sample-started
  g tick > /dev/null 2>&1 &
  local pid=$!
  ob_reached sample-started.1
  busy_io 500000000
  ob_go sample-started.1
  wait "$pid"
  expect "in use again: nothing fires" [ "$?" = 0 ]
  expect "the shutdown command was not called" [ ! -s "$STUB_DIR/shutdown_calls" ]
  expect "the off-now shutdown is cancelled" [ "$(state shutdown_pending)" = 0 ]
  teardown
}

t17_an_issued_shutdown_is_not_retried_without_the_uptime() {
  setup
  use_clock
  quiet g arm --idle 2m
  echo 1 > "$STUB_DIR/shutdown_fail"
  expect "idle: a shutdown is attempted and stays pending" [ "$(tick_for 180)" = 10 ]
  rm -f "$AUTODL_GUARD_HOME/state2/schema"
  : > "$STUB_DIR/shutdown_calls"
  export AUTODL_TEST_UPTIME="$STUB_DIR/no-such-uptime"
  expect "nothing can be timed: no retry" [ "$(tick_rc)" = 0 ]
  expect "the shutdown command was not called" [ ! -s "$STUB_DIR/shutdown_calls" ]
  expect "cancelled, as in a state that can be used" [ "$(state shutdown_pending)" = 0 ]
  teardown
}

t17_a_rearm_while_the_retry_samples_takes_the_decision_away() {
  setup
  use_clock
  quiet g arm --idle 2m
  echo 1 > "$STUB_DIR/shutdown_fail"
  expect "idle: a shutdown is attempted and stays pending" [ "$(tick_for 180)" = 10 ]
  rm -f "$AUTODL_GUARD_HOME/state2/schema"
  : > "$STUB_DIR/shutdown_calls"
  ob_dir
  ob_hold sample-started
  g tick > /dev/null 2>&1 &
  local pid=$!
  ob_reached sample-started.1
  quiet g arm --idle 60m --rearm             # the boot is configured anew meanwhile: that cancels the shutdown
  ob_go sample-started.1
  wait "$pid"
  expect "the check that sampled does nothing more" [ "$?" = 0 ]
  expect "the shutdown command was not called" [ ! -s "$STUB_DIR/shutdown_calls" ]
  expect "the new arm stands" status_has '^idle_s=3600$'
  expect "with nothing pending" [ "$(state shutdown_pending)" = 0 ]
  teardown
}

t17_the_daemon_takes_its_own_cpu_off_once_when_it_samples() {  # in row 1 the sampling process is the daemon itself:
  # counted as "this process" and once more as "the daemon", its CPU time would hide as much of somebody else's work
  setup
  use_clock
  quiet g arm --idle 2m
  echo 1 > "$STUB_DIR/shutdown_fail"
  expect "idle: a shutdown is attempted and stays pending" [ "$(tick_for 180)" = 10 ]
  rm -f "$AUTODL_GUARD_HOME/state2/schema"
  : > "$STUB_DIR/shutdown_calls"
  ob_dir
  ob_hold sample-started
  local hz half p
  hz="$(getconf CLK_TCK)"
  half=$((hz / 2))
  # the check runs as the recorded daemon, in a /proc this test writes: its PID and start time are the daemon's, and
  # its CPU time so far is 100 ticks
  mkdir -p "$T/fp/1"
  export AUTODL_TEST_PROC="$T/fp"
  bash -c 'd="$AUTODL_TEST_PROC/$$"; mkdir -p "$d"
    printf "%s (bash) S 1 1 1 0 -1 0 0 0 0 0 100 0 0 0 20 0 1 0 777 0 0\n" "$$" > "$d/stat"
    printf "%s" "$$" > "$1/state2/daemon_pid"; printf 777 > "$1/state2/daemon_pstart"; printf "%s" "$$" > "$2"
    exec bash "$3" tick' _ "$AUTODL_GUARD_HOME" "$T/tickpid" "$GUARD" > /dev/null 2>&1 &
  local pid=$!
  ob_reached sample-started.1
  # while it samples for 1 s, the check itself uses half a second of CPU and the container 0.6 s in all: 0.1 s is
  # somebody else's, 10 % of a core, above the 3 % of non-GPU mode. Taken off twice, nothing would be left of it
  p="$(cat "$T/tickpid")"
  printf '%s (bash) S 1 1 1 0 -1 0 0 0 0 0 %s 0 0 0 20 0 1 0 777 0 0\n' "$p" "$((100 + half))" > "$T/fp/$p/stat"
  busy_cpu 600000
  ob_go sample-started.1
  wait "$pid"
  expect "somebody else's work is seen: nothing fires" [ "$?" = 0 ]
  expect "the shutdown command was not called" [ ! -s "$STUB_DIR/shutdown_calls" ]
  expect "the pending shutdown is cancelled" [ "$(state shutdown_pending)" = 0 ]
  expect "for the CPU" grep -q 'PENDING SHUTDOWN CANCELLED kind=idle: in use again \[cpu:busy\]' "$AUTODL_GUARD_HOME/guard.log"
  teardown
}

t17_a_dry_run_is_retried_as_it_is() {  # a dry run shuts nothing down, here as in a state that can be used: its
  # "shutdown" is written to the log again, with no sample taken for it
  setup
  use_clock
  quiet g arm --idle 2m --dry-run
  expect "idle: the dry run fires" [ "$(tick_for 180)" = 10 ]
  expect "and is pending" [ "$(state shutdown_pending)" = 1 ]
  rm -f "$AUTODL_GUARD_HOME/state2/schema"
  rm -f "$AUTODL_GUARD_HOME/state2/dry_run_fired"
  ob_dir
  busy_cpu 5000000000
  quiet g tick
  expect "it fires again" fired
  expect "without a sample" [ ! -e "$T/bar/sample-started.1" ]
  teardown
}

TESTS=(t_durations t_arm_requires_values t_keep_expiry_shuts_down t_grace_period
  t_a_running_job_shows_in_status t_off_when_done_waits_for_job t_cancel_off_when_done
  t_deadline_shuts_down_a_job_without_activity t_sessions t_gpu_utilization_is_a_signal t_off_now t_keep_after_job_is_refused
  t_stale_boot_job_ignored t_run_archives_previous t_job_env_and_stdin t_real_shutdown_path
  t_daemon_end_to_end t_status_keys t_job_name_rules t_pending_blocks_commands t_shutdown_retry
  t_orphan_group_detected t_pid_reuse_ignored t_launch_failure_reported t_requires_arm
  t_revive_and_autostart t_corrupt_state_no_idle_shutdown t_interval_bounds t_mode_tristate
  t_short_job_between_ticks t_background_child_keeps_job t_pending_retry_rechecks
  t_pending_write_failure t_a_hanging_probe_is_unknown_and_blocks_nothing
  t_screen_launcher t_huge_numbers t_stale_dry_run_ignored t_daemon_needed_before_job
  t_off_now_failure_keeps_daemon t_guard_name_reserved t_run_request_id_is_idempotent
  t_setsid_child_keeps_the_job t_run_checks_the_command_it_received t_run_request_resumes_an_interrupted_launch
  t_arm_request_id t_state_values_must_make_sense t_pending_metadata_is_written_first
  t_pending_retry_takes_a_fresh_gpu_sample t_arm_reports_a_daemon_that_did_not_start
  t_runner_dying_before_the_command_is_no_start t_late_runner_does_not_start_the_command
  t_arm_publishes_the_boot_last t_rearm_with_the_same_request_changes_nothing
  t_a_baseline_that_cannot_be_stored_counts_as_in_use t_ended_job_ignores_a_reused_process_group
  t_tag_scan_needs_no_find_or_xargs t_a_proc_scan_that_reads_nothing_counts_as_maybe_alive
  t_an_interrupted_rearm_is_not_half_applied t_an_incomplete_arm_blocks_jobs_until_an_arm_completes
 t_a_rearm_that_cannot_begin_changes_nothing
  t_a_runner_dying_after_the_spawn_is_reported_not_restarted
  t_a_resend_after_a_cut_off_start_does_not_run_it_twice t_a_start_that_cannot_be_recorded_is_uncertain
  t_a_hanging_shutdown_does_not_block_revive_restart t_a_hanging_probe_does_not_block_revive_restart
  t_a_killed_command_does_not_keep_the_lifecycle_lock t_missing_flock_is_refused
  t_unreadable_job_files_count_as_running
 t_a_job_that_ended_before_its_start_was_recorded_is_reported_started
  t_restart_never_signals_an_unverified_process t_an_unknown_boot_is_never_armed
  t_no_external_command_holds_a_lock t_sample_reads_the_counters t_sample_leaves_unreadable_or_malformed_counters_empty
  t_sample_rows_cover_the_interval_before_them t_sample_gpu_is_unknown_if_any_reading_fails
  t_sample_without_gpu_never_calls_nvidia_smi t_sample_keeps_its_schedule_when_nvidia_smi_hangs
  t_sample_refuses_without_a_clock t_sample_needs_no_lock_and_writes_nothing
  t_sample_counts_the_guard_cpu_only_for_the_verified_daemon t_shutdown_does_not_wait_for_a_hanging_sync
  t_shutdown_waits_for_a_sync_that_finishes t_a_hanging_sync_does_not_block_revive_restart
  t_a_hanging_sync_does_not_hold_the_callers_output t_retried_shutdowns_do_not_pile_up_hanging_syncs
  t8_arm_requires_idle t8_arm_writes_schema_2_and_uptimes t8_arm_defaults_by_mode t8_arm_threshold_options
  t8_arm_refuses_without_uptime t8_arm_leaves_the_07_state_alone t8_status_reports_schema_and_needs_rearm
  t8_values_of_another_boot_are_void t8_still_counters_are_idle t8_cpu_threshold t8_io_and_net_thresholds
  t8_unreadable_counter_is_unknown_and_busy t8_counter_going_back_is_unknown t8_stale_baseline_is_unknown
  t8_gpu_signal t8_unreliable_signal_is_off t8_no_reliable_signal_never_idles t8_daemon_spreads_gpu_probes
  t8_nogpu_daemon_never_calls_nvidia_smi t8_idle_shutdown_after_idle t8_activity_restarts_the_idle_count
  t8_a_registered_job_alone_is_not_in_use t8_sessions_are_not_in_use t8_keep_holds_until_it_ends
  t8_deadline_waits_for_activity_then_grace t8_deadline_voids_keep
  t8_after_the_deadline_run_keep_deadline_refused t8_after_the_deadline_a_rearm_sets_a_new_one
  t8_off_when_done_waits_for_jobs_then_grace
  t8_off_when_done_does_not_protect_an_idle_job t8_pending_retry_rechecks_activity
  t8_incomplete_arm_decides_nothing t8_wall_clock_jumps_do_not_matter t8_uptime_unreadable_is_in_use
  t8_old_state_decides_nothing_but_retries_pending t8_rearm_resets_the_baseline_and_the_idle_count
  t8_a_rearm_leaves_the_jobs_and_their_quiet_periods_and_drops_keep_and_off_when_done
  t8_gpu_probes_0_needs_the_gpu_marked_unreliable t8_a_pending_shutdown_of_another_boot_is_not_retried
  t8_a_forced_pending_shutdown_is_retried_while_in_use
  t8_idle_before_the_deadline_counts_toward_grace t8_a_lost_clock_restarts_the_idle_time_when_it_returns
  t8_a_failed_shutdown_request_never_revives_an_old_pending_flag t8_off_when_done_ends_a_running_keep_now
  t8_no_reliable_signal_cancels_an_automatic_retry t8_a_baseline_two_intervals_old_is_unknown
  t8_calibration_coverage_is_reported t8_two_checks_under_a_second_apart_are_unknown
  t8_malformed_counters_are_unknown t8_a_real_07_arm_is_not_taken_for_an_08_one
  t8_a_daemon_starting_with_a_pending_shutdown_checks_at_once
  t8_a_daemon_starting_past_the_deadline_checks_at_once t8_the_daemon_wakes_at_the_deadline
  t8_a_baseline_two_intervals_old_is_unknown_at_any_interval
  t8_a_burst_after_an_unstored_baseline_is_not_watered_down t8_a_committed_off_now_ignores_keep_on_retry
  t8_an_off_now_that_cannot_commit_keeps_the_keep t8_a_forced_retry_at_daemon_start_waits_for_no_probe
  t8_the_daemon_checks_at_the_deadline_not_before
  t8_a_check_that_came_too_late_cancels_a_retry
  t8_an_earlier_deadline_set_mid_window_wakes_the_daemon t8_a_probe_never_runs_past_its_window
  t8_timeout_is_required
  t8_an_earlier_deadline_between_probes_wakes_the_daemon t8_a_rearm_restarts_the_running_daemon
  t8_sample_needs_timeout_for_gpu_samples
  t8_a_window_from_before_the_arm_is_discarded t8_a_shortened_window_spreads_its_remaining_probes
  t8_an_arm_writes_its_generation_after_its_settings t8_in_a_rearms_gap_the_old_window_is_discarded
  t8_run_quiet_holds_until_expiry t8_quiet_ends_with_the_job
  t8_quiet_command_before_and_after_the_deadline t8_quiet_refuses_unknown_or_ended_jobs
  t8_quiet_of_another_boot_is_void t8_a_stuck_quiet_job_holds_only_until_expiry
  t8_the_latest_of_several_quiet_periods_counts t8_quiet_and_keep_end_at_different_times
  t8_an_ended_quiet_period_never_moves_the_last_activity_back t8_an_unreadable_quiet_period_counts_as_in_use
  t8_status_shows_quiet_periods
  t8_off_now_refuses_a_running_job t8_off_now_refuses_live_activity
  t8_off_now_idle_shuts_down_after_saying_so t8_off_now_force_skips_the_checks
  t8_gate_refuses_changes_but_not_reads t8_a_stale_prep_is_taken_over
  t8_a_prep_of_another_boot_is_void t8_off_now_keeps_keep_when_refused
  t8_off_now_rechecks_under_the_lock t8_off_now_without_arm_uses_defaults
  t8_off_now_resamples_a_stale_sample_once t8_off_now_reports_a_shutdown_committed_meanwhile
  t8_off_now_dies_at_each_step t8_shutdown_committed_before_issuing
  t8_off_now_sample_option t8_off_now_with_a_pending_shutdown_reports_it_at_once
  t8_status_keys t8_status_counts_from_the_same_point_as_tick
  t8_status_counts_down t8_status_is_read_only
  t14_arm_refuses_next_to_a_live_07_daemon t14_the_07_lock_alone_decides
  t14_an_07_state_of_an_earlier_boot_does_not_block t14_revive_refuses_next_to_a_live_07_daemon
  t14_a_07_lock_that_cannot_be_tried_refuses
  t8_a_failed_quiet_leaves_the_old_declaration t8_a_quiet_declaration_without_a_valid_end_counts_as_in_use
  t8_a_damaged_arm_gen_needs_a_rearm t8_a_shortened_window_limits_its_probes_to_the_new_share
  t8_off_now_starts_a_daemon_to_retry t8_off_now_resolves_a_pending_shutdown_without_a_daemon
  t8_off_now_starts_no_daemon_next_to_a_live_07_one
  t8_off_now_commits_only_while_a_daemon_runs t14_a_daemon_does_not_run_next_to_a_live_07_one
  t8_a_zero_probe_limit_fails_the_probe
  t8_steady_noise_under_the_thresholds_still_idles t8_one_busy_probe_makes_the_gpu_in_use
  t15_arm_saves_the_boot_settings_of_its_mode t15_a_rearm_in_the_other_mode_keeps_the_first_modes_settings
  t15_an_arm_whose_settings_cannot_be_saved_is_not_done t15_a_failed_rearm_leaves_no_settings_behind
  t15_an_arm_reads_what_an_arm_cut_short_left
  t15_boot_arms_with_the_saved_settings_of_its_mode t15_boot_keeps_a_dry_run
  t15_boot_after_a_mode_switch_uses_the_times_and_that_modes_defaults t15_boot_leaves_nothing_of_the_last_boot
  t15_boot_without_usable_settings_arms_nothing t15_boot_with_an_unknown_mode_arms_nothing
  t15_mode_detection_stays_within_its_budget t15_a_gpu_that_answers_late_is_still_found
  t15_boot_settings_of_the_environment_are_checked t15_boot_without_the_uptime_arms_nothing
  t15_boot_after_a_failed_arm t15_unreadable_signals_after_boot_count_as_in_use
  t15_boot_never_replaces_an_arm_of_this_boot t15_an_arm_replaces_the_boot_arm_without_rearm
  t15_boot_keeps_a_shutdown_pending_in_this_boot t15_boot_waits_out_the_gate
  t15_boot_rechecks_after_detecting_the_mode t15_boot_stops_next_to_07 t15_the_boot_arm_shuts_down_after_idle
  t15_boot_becomes_the_daemon t15_bad_settings_of_this_mode_are_passed_over
  t15_quick_failures_on_a_still_clock_leave_the_budget_alone t15_boot_leaves_the_start_environment_behind
  t15_install_writes_the_hook t15_install_again_changes_nothing_and_replaces_its_own_old_hook
  t15_a_foreign_file_is_left_alone t15_uninstall_removes_the_hook t15_install_takes_over_from_the_hook_under_its_former_name
  t15_a_foreign_file_under_the_former_name_is_left_alone t15_uninstall_removes_the_hook_under_its_former_name_too
  t15_install_needs_profile_d_and_plain_paths
  t15_install_takes_paths_as_they_are t15_install_says_when_there_is_nothing_to_arm_with
  t15_status_shows_the_autostart_keys t15_install_checks_the_test_wait
  t15_the_hook_starts_boot_only_as_the_container_start_script t15_the_hook_changes_nothing_in_process_1
  t15_the_hook_keeps_the_start_environment_out t15_the_hook_waits_for_a_late_guard_script
  t15_boot_runs_the_daemon_of_an_armed_or_pending_start t15_boot_arms_nothing_when_the_uptime_goes_before_the_arm
  t15_boot_without_the_boot_marker_does_nothing t15_install_checks_what_the_hook_runs
  t15_boot_settings_show_what_the_next_start_uses t15_install_tells_a_failed_hook_text_apart
  t16_run_is_refused_in_a_boot_armed_start t16_a_boot_arm_keeps_the_order_of_refusals
  t17_idle_check_passes_on_an_idle_instance t17_idle_check_refuses_activity_seen_while_it_samples
  t17_idle_check_refuses_what_it_cannot_read t17_idle_check_judges_by_the_arm_of_this_boot
  t17_idle_check_runs_from_stdin_and_needs_no_flock t17_an_issued_shutdown_is_cancelled_when_in_use_though_the_state_cannot_be_used
  t17_an_issued_shutdown_is_retried_while_idle_and_a_forced_one_always t17_an_off_now_on_a_start_nobody_armed_is_retried_only_while_idle
  t17_an_issued_shutdown_is_not_retried_without_the_uptime t17_a_rearm_while_the_retry_samples_takes_the_decision_away
  t17_the_daemon_takes_its_own_cpu_off_once_when_it_samples t17_a_dry_run_is_retried_as_it_is)
[ $# -eq 0 ] || TESTS=("$@")   # test_guard.sh [TEST...]: run only the named tests
for t in "${TESTS[@]}"; do "$t"; done
printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -eq 0 ]; then echo "ALL PASSED"; fi
[ "$FAIL" -eq 0 ]
