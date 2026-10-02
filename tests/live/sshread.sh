# Read-only remote command for the calibration runs, with a time limit and retries: runs CMD on ALIAS
# and keeps its standard output in OUTFILE only when it succeeds (written to OUTFILE.tmp, then renamed).
# Usage (Git Bash): bash tests/live/sshread.sh ALIAS OUTFILE CMD
# Only an ssh failure (exit 255, a connection dropped before authentication for one) or the local time
# limit is tried again, so CMD must not change anything. ssh hands back the remote exit status as it is,
# so CMD runs in a subshell whose 124 or 255 comes back as 125: a command that fails that way is not
# taken for an ssh failure or the time limit. Exit status:
#   0 CMD succeeded and its output is in OUTFILE
#   1 CMD ran and exited with a status other than 0 and 3 (printed; 124 and 255 show as 125), or OUTFILE
#     could not be written
#   2 usage error, or OUTFILE or OUTFILE.tmp already exists
#   3 CMD ran and exited 3 (callers use it for "not there")
#   4 CMD could not be run: every try ended in an ssh failure or the time limit
# SSHREAD_TRIES (default 5), SSHREAD_GAP (seconds between tries, default 10) and SSHREAD_TIMEOUT (seconds
# per try, default 60) change the retries; AUTODL_SSH names the ssh program (default ssh).
set -u
if [ $# -ne 3 ]; then echo "usage: bash tests/live/sshread.sh ALIAS OUTFILE CMD" >&2; exit 2; fi
host="$1"
out="$2"
cmd="$3"
tries="${SSHREAD_TRIES:-5}"
gap="${SSHREAD_GAP:-10}"
lim="${SSHREAD_TIMEOUT:-60}"
for v in "$tries" "$gap" "$lim"; do
  case "$v" in '' | *[!0-9]* | 0?*) echo "sshread: SSHREAD_TRIES, _GAP and _TIMEOUT must be whole numbers" >&2; exit 2 ;; esac
done
if [ "$tries" -lt 1 ] || [ "$lim" -lt 1 ]; then echo "sshread: SSHREAD_TRIES and SSHREAD_TIMEOUT must be at least 1" >&2; exit 2; fi
if [ -e "$out" ] || [ -e "$out.tmp" ]; then echo "sshread: $out or $out.tmp already exists" >&2; exit 2; fi
# the newline before ")" keeps a trailing comment in CMD from swallowing it
remote="( $cmd"$'\n'") ; r=\$?; case \$r in 124|255) exit 125 ;; esac; exit \$r"
i=0
while [ "$i" -lt "$tries" ]; do
  i=$((i + 1))
  timeout "$lim" "${AUTODL_SSH:-ssh}" -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 -- "$host" "$remote" > "$out.tmp"
  rc=$?
  case "$rc" in
    0)
      if mv -f "$out.tmp" "$out"; then exit 0; fi
      echo "sshread: cannot rename $out.tmp" >&2
      exit 1 ;;
    3) rm -f "$out.tmp"; exit 3 ;;
    124 | 255) rm -f "$out.tmp"; echo "sshread: try $i of $tries: ssh failed or timed out (exit $rc)" >&2 ;;
    *) rm -f "$out.tmp"; echo "sshread: the remote command exited $rc" >&2; exit 1 ;;
  esac
  if [ "$i" -lt "$tries" ]; then sleep "$gap"; fi
done
echo "sshread: could not run the command in $tries tries" >&2
exit 4
