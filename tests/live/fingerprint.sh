# Read-only environment record for the calibration report; the output names this instance, so it
# is kept outside the repository. Usage: ssh ALIAS bash -s -- DATA_DIR < tests/live/fingerprint.sh
# Each command's output is labelled; a command that fails shows its error and the next one still runs.
data="${1:-/root/autodl-tmp}"
run() { printf '\n### %s\n' "$*"; "$@" 2>&1; }
run date -u
run cat /proc/uptime
run hostname
run uname -a
run cat /etc/os-release
run bash --version
run getconf CLK_TCK
run nproc
run cat /proc/self/cgroup
run grep cgroup /proc/self/mountinfo
run ls -l /proc/self/ns
run cat /sys/fs/cgroup/cgroup.controllers
run cat /sys/fs/cgroup/cpu.max
run cat /sys/fs/cgroup/memory.max
run cat /sys/fs/cgroup/cpu.stat
run cat /sys/fs/cgroup/io.stat
run cat /proc/net/dev
run cat /proc/sys/vm/dirty_expire_centisecs /proc/sys/vm/dirty_writeback_centisecs   # when buffered writes go to disk
run ps -o pid,etimes,lstart,comm -p 1   # the container's age: compare with /proc/uptime above, which may be the host's
run df -B1 / "$data"
run nvidia-smi --query-gpu=name,driver_version,utilization.gpu,memory.used --format=csv
run ps -eo pid,ppid,user,etimes,comm --sort=pid   # names only: arguments can carry tokens (Jupyter's, for one)
