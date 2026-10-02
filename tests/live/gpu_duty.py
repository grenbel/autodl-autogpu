"""Calibration load: BUSY seconds of matrix products every PERIOD seconds, COUNT times.

Usage: python gpu_duty.py BUSY PERIOD COUNT   (3 47 8: short bursts; 0.2 1 180: light and steady)
Prints LOAD START/END lines like cpu_duty.py, and the GPU name at the end.
"""
import sys
import time

import torch


def mark(what):
    try:
        with open("/proc/uptime") as f:
            up = f.read().split()[0]
    except OSError:
        up = f"{time.monotonic():.2f}"
    print(f"LOAD {what} {int(time.time())} {up}", flush=True)


busy, period, count = float(sys.argv[1]), float(sys.argv[2]), int(sys.argv[3])
if not (0 < busy < period and count > 0 and period * count <= 3600):
    sys.exit("usage: gpu_duty.py BUSY PERIOD COUNT  (BUSY < PERIOD, at most an hour in all)")
x = torch.randn(4096, 4096, device="cuda")
torch.cuda.synchronize()
mark("START")
t0 = time.monotonic()
for i in range(count):
    while time.monotonic() < t0 + i * period:
        time.sleep(0.01)
    t = time.monotonic()
    while time.monotonic() - t < busy:
        x @ x
        torch.cuda.synchronize()   # the GPU works only while we wait here, so BUSY is what it gets
mark("END")
print("device", torch.cuda.get_device_name(0), flush=True)
