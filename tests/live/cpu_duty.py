"""Calibration load: busy for DUTY of every second (0.05 = about 5% of one core) for SECONDS.

Usage: python cpu_duty.py DUTY SECONDS
Prints "LOAD START <unix time> <uptime>" and "LOAD END ..." so that the analysis can find the
load's window (dev/analyze_samples.py --load-window). Without /proc/uptime (a local smoke test)
the monotonic clock stands in for it.
"""
import sys
import time


def mark(what):
    try:
        with open("/proc/uptime") as f:
            up = f.read().split()[0]
    except OSError:
        up = f"{time.monotonic():.2f}"
    print(f"LOAD {what} {int(time.time())} {up}", flush=True)


duty, seconds = float(sys.argv[1]), float(sys.argv[2])
if not (0 < duty < 1 and 0 < seconds <= 3600):
    sys.exit("usage: cpu_duty.py DUTY SECONDS  (0 < DUTY < 1, SECONDS at most 3600)")
mark("START")
end = time.monotonic() + seconds
while time.monotonic() < end:
    t = time.monotonic()
    while time.monotonic() - t < duty:
        pass
    time.sleep(1 - duty)
mark("END")
