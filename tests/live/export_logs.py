"""Copy the guard's job logs into the repository keeping only the lines the calibration needs (plan Phase 2).

Usage: python tests/live/export_logs.py SRC_DIR DST_DIR
Each regular file of SRC_DIR (logs pulled from the instance, kept outside the repository) is written to
DST_DIR, a directory this creates, with only these lines: the job's start and end lines, the two header
lines and the rows of the sample command, LOAD START/END marks, and the "device" line of gpu_duty.py.
Every other line, first of all the "=== cmd:" line with the job's whole command, stays in SRC_DIR only.
Prints how many lines each file kept and dropped. Exit 0 when done, 1 if SRC_DIR holds anything but
regular files or a file cannot be written, 2 on wrong arguments or when DST_DIR already exists.
"""
import os
import re
import sys

USAGE = "usage: python tests/live/export_logs.py SRC_DIR DST_DIR"
TS = rb"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} [+-][0-9]{4}"
JOB = rb"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
KEEP = [re.compile(p) for p in (
    rb"=== job " + JOB + rb" start " + TS,
    rb"=== job " + JOB + rb" end " + TS + rb" rc=[0-9]+",
    rb"# autodl_guard [0-9A-Za-z.]+ sample every=[0-9]+ count=[0-9]+ gpu_samples=[0-9]+ clk_tck=[0-9]*",
    rb"# epoch\tuptime_cs\tcpu_usec\tio_bytes\tnet_bytes\tgpu_max\tgpu_fail\tguard_ticks\tself_ticks",
    rb"[0-9]+\t[0-9]+(?:\t(?:[0-9]+|na)?){7}",
    rb"LOAD (?:START|END) [0-9]+ [0-9]+(?:\.[0-9]+)?",
    rb"device [ -~]{1,100}",
)]


def keep(line):
    body = line[:-1] if line.endswith(b"\n") else line
    return any(p.fullmatch(body) for p in KEEP)


def main(argv):
    if len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    src, dst = argv
    if not os.path.isdir(src):
        print(f"export_logs: {src} is not a directory", file=sys.stderr)
        return 2
    if os.path.lexists(dst):
        print(f"export_logs: {dst} already exists", file=sys.stderr)
        return 2
    names = sorted(os.listdir(src))
    odd = [n for n in names if os.path.islink(os.path.join(src, n)) or not os.path.isfile(os.path.join(src, n))]
    if odd:
        print(f"export_logs: not regular files in {src}: {odd}", file=sys.stderr)
        return 1
    os.mkdir(dst)
    kept_all = dropped_all = 0
    try:
        for n in names:
            kept, dropped = [], 0
            with open(os.path.join(src, n), "rb") as fh:
                for line in fh:
                    if keep(line):
                        kept.append(line)
                    else:
                        dropped += 1
            with open(os.path.join(dst, n), "xb") as fh:
                fh.writelines(kept)
            print(f"{n} kept={len(kept)} dropped={dropped}")
            kept_all += len(kept)
            dropped_all += dropped
    except OSError as e:
        print(f"export_logs: {e}", file=sys.stderr)
        return 1
    print(f"total files={len(names)} kept={kept_all} dropped={dropped_all}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
