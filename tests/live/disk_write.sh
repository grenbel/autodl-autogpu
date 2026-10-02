# Calibration load: write MIB mebibytes of zeros into DIR, then delete that file. MODE "sync" (the
# default) writes with fsync. "buffered" writes through the page cache and keeps the file WAIT seconds
# (default 45, past the kernel's usual 30 s dirty expiry) so that it is written back before it is
# deleted: a file deleted while still dirty never reaches the disk.
# Usage: bash disk_write.sh DIR MIB [sync|buffered [WAIT]]. Prints LOAD START/END lines like cpu_duty.py.
# The file is removed however the run ends: INT, TERM or HUP (from timeout, say) exit 130, 143 or 129 once
# the command in progress has ended, and a file that is still there at the end makes the exit status 1.
set -u
dir="$1"
mib="$2"
mode="${3:-sync}"
wait_s="${4:-45}"
case "$mode" in
  sync) opt="conv=fsync" ;;
  buffered) opt="conv=notrunc" ;;   # a no-op for a new file, so that dd gets one option either way
  *) echo "usage: bash disk_write.sh DIR MIB [sync|buffered [WAIT]]"; exit 2 ;;
esac
case "$wait_s" in '' | *[!0-9]*) echo "usage: WAIT is whole seconds"; exit 2 ;; esac
mark() { printf 'LOAD %s %s %s\n' "$1" "$(date +%s)" "$(cut -d' ' -f1 /proc/uptime)"; }
f="$dir/autodl-calib-$$.bin"
if [ -e "$f" ]; then echo "refusing: $f already exists"; exit 1; fi
trap 'rm -f "$f"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
mark START
dd if=/dev/zero of="$f" bs=1M count="$mib" "$opt" status=none
rc=$?
if [ "$mode" = buffered ]; then sleep "$wait_s"; fi
mark END
rm -f "$f"
if [ -e "$f" ]; then echo "could not delete $f"; exit 1; fi
exit "$rc"
