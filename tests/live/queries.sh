# Local side of the "idle with queries" calibration: the commands an AI sends to a running instance,
# one every GAP seconds, each logged as "<unix time> <name> rc=<exit status>".
# Usage (Git Bash, repo root): bash tests/live/queries.sh ALIAS OUTFILE GAP_SECONDS HOLD_FIFO HOLD_LIMIT
# The first line of OUTFILE gives the start time and "bound", the most seconds the run can take (every
# query has a local time limit). The off-now must be refused, so it is sent only while the holder job
# (a cat blocked on HOLD_FIFO, a path with a random token, started as "timeout HOLD_LIMIT cat ...") has
# at least 180 s left. The run stops at the first result that is not the expected one. At the end, also
# when stopped by INT, TERM or HUP (bash lets the query in progress end first), OUTFILE.done is written
# whole: "ok" if the results were ssh-true rc=0, status rc=0, wait rc=0 and off-now-refused rc=3, else
# "fail", or "killed". Exit 0 for ok, 1 for fail, 2 for a usage error or when OUTFILE or OUTFILE.done
# already exists (a rerun takes a new name, queries-r2.txt say). AUTODL_SSH and QUERIES_CTL name the
# ssh program and the ctl launcher (default ssh and scripts/ctl).
set -u
if [ $# -ne 5 ]; then
  echo "usage: bash tests/live/queries.sh ALIAS OUTFILE GAP_SECONDS HOLD_FIFO HOLD_LIMIT" >&2
  exit 2
fi
host="$1"
out="$2"
gap="$3"
fifo="$4"
limit="$5"
for v in "$gap" "$limit"; do
  case "$v" in '' | *[!0-9]* | 0?*) echo "queries: GAP_SECONDS and HOLD_LIMIT must be whole seconds" >&2; exit 2 ;; esac
done
if [ -e "$out" ] || [ -e "$out.done" ]; then echo "queries: $out or $out.done already exists" >&2; exit 2; fi
ssh_cmd="${AUTODL_SSH:-ssh}"
ctl="${QUERIES_CTL:-scripts/ctl}"
so=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 --)
finish() {  # finish VERDICT STATUS: write the completion marker whole, then exit
  printf '%s\n' "$1" > "$out.done.tmp" && mv -f "$out.done.tmp" "$out.done"
  exit "$2"
}
trap 'finish killed 130' INT
trap 'finish killed 143' TERM
trap 'finish killed 129' HUP
q() {  # q NAME WANT CMD...: run one query and log it; the run stops unless it exited WANT
  local name="$1" want="$2" rc
  shift 2
  "$@" > /dev/null 2>&1
  rc=$?
  printf '%s %s rc=%s\n' "$(date +%s)" "$name" "$rc" >> "$out"
  [ "$rc" = "$want" ] || finish fail 1
}
skip() {  # skip WHY: no off-now, and the run fails
  printf '%s off-now skipped: %s\n' "$(date +%s)" "$1" >> "$out"
  finish fail 1
}
printf '%s start gap=%s bound=%s\n' "$(date +%s)" "$gap" "$((4 * gap + 450))" >> "$out"   # 450 = 60+90+150+60+90
sleep "$gap"; q ssh-true 0 timeout 60 "$ssh_cmd" "${so[@]}" "$host" true
sleep "$gap"; q status 0 timeout 90 bash "$ctl" status "$host"
sleep "$gap"; q wait 0 timeout 150 bash "$ctl" wait "$host" --timeout 2m
sleep "$gap"
# the holder's age in seconds: its oldest process is the timeout that runs the cat ([c]at: the remote
# shell's own command line must not match)
age="$(timeout 60 "$ssh_cmd" "${so[@]}" "$host" "ps -o etimes= -p \"\$(pgrep -o -f '[c]at $fifo')\"" 2> /dev/null)"
age="${age//[[:space:]]/}"
case "$age" in '' | *[!0-9]*) skip "holder job not running" ;; esac
[ "$age" -le $((limit - 180)) ] || skip "holder job has less than 180 s left"
q off-now-refused 3 timeout 90 bash "$ctl" off-now "$host" --reason 'calibration: this must be refused' --wait 30s
finish ok 0
