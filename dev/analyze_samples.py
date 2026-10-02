#!/usr/bin/env python3
"""Summarize the output of `autodl_guard.sh sample`: per-interval rates and their spread.

Usage: python dev/analyze_samples.py FILE [FILE ...]
           [--between START_CS END_CS | --load-window LOG [--load-lead S] | --load-cover LOG]
           [--trim-start N] [--trim-end N] [--window N] [--clk-tck HZ] [--rows] [--json]

A sample run prints a parameter line ("# autodl_guard VERSION sample ... clk_tck=HZ"), the column
names, a baseline row, then one row per interval. A row's GPU columns cover the interval that ends
at that row; its other columns are cumulative counters read at the row's time. Rates are computed
between rows of the same run only (a parameter line starts a new run; files are never paired with
each other): cpu_pct, guard_pct and self_pct are percent of one core, io_Bps and net_Bps are bytes
per second, gpu is the highest utilization in the interval ("na" = not applicable, None = unknown).
Other lines, such as job log banners, are ignored, and so is a byte order mark at the start of a file
(Windows PowerShell writes one).

--between keeps only the rows whose uptime_cs lies in [START_CS, END_CS]; the first row kept is the
baseline of the next interval. --load-window LOG does the same for a load's steady window, from the
"LOAD START" and "LOAD END" lines in LOG (from --load-lead seconds after the start, 10 by default, to
the end). --load-cover LOG keeps every interval that overlaps the load instead, for loads of a few
seconds. With any of the three, finding no complete interval is an error.
--trim-start and --trim-end then drop that many intervals at the start and end of every run (the
load starting and stopping). --window N joins N consecutive intervals into one: the counters give
the rate over the whole window (every row in it must be readable and no counter may go back), and
gpu is the highest of all its samples, which is more than a guard sampling at the window's pace
would see (measure GPU detection at the real pace instead).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys

FIELDS = ("epoch", "uptime_cs", "cpu_usec", "io_bytes", "net_bytes", "gpu_max", "gpu_fail", "guard_ticks", "self_ticks")
METRICS = ("cpu_pct", "io_Bps", "net_Bps", "gpu", "guard_pct", "self_pct")
COUNTERS = (("cpu_usec", "cpu_pct"), ("io_bytes", "io_Bps"), ("net_bytes", "net_Bps"),
            ("guard_ticks", "guard_pct"), ("self_ticks", "self_pct"))
PERCENTILES = (("p1", 1), ("p5", 5), ("p10", 10), ("p50", 50), ("p90", 90), ("p99", 99))
RUN_RE = re.compile(r"^# autodl_guard \S+ sample\b(.*)$")
HZ_RE = re.compile(r"\bclk_tck=([1-9][0-9]*)\b")
NUM_RE = re.compile(r"[0-9]+")
UPTIME_RE = re.compile(r"([0-9]+)\.([0-9]{2})")


def _int(v):
    return int(v) if NUM_RE.fullmatch(v) else None


def load_marks(path):
    """(START, END) in centiseconds of uptime, from the "LOAD START <unix time> <uptime>" and "LOAD END ..."
    lines a calibration load prints. ValueError unless there is exactly one of each and END is after START
    (a rerun appended to the same log would otherwise be read as one long load)."""
    marks = {"START": [], "END": []}
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            p = line.split()
            m = UPTIME_RE.fullmatch(p[3]) if len(p) == 4 and p[0] == "LOAD" and p[1] in marks else None
            if m:
                marks[p[1]].append(int(m.group(1)) * 100 + int(m.group(2)))
    starts, ends = marks["START"], marks["END"]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise ValueError(f"{path}: need one LOAD START and one later LOAD END, found {len(starts)} and {len(ends)}")
    return starts[0], ends[0]


def load_window(path, lead_s=10):
    """The load's steady window: from lead_s seconds after its start to its end."""
    start, end = load_marks(path)
    return start + lead_s * 100, end


def parse_runs(lines):
    """Split sample output into runs: [{"clk_tck": int or None, "rows": [row dict, ...]}, ...]."""
    runs = []
    cur = None
    for line in lines:
        line = line.rstrip("\r\n")
        m = RUN_RE.match(line)
        if m:
            hz = HZ_RE.search(m.group(1))
            cur = {"clk_tck": int(hz.group(1)) if hz else None, "rows": []}
            runs.append(cur)
            continue
        if line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != len(FIELDS) or _int(parts[0]) is None:
            continue
        if cur is None:
            cur = {"clk_tck": None, "rows": []}
            runs.append(cur)
        cur["rows"].append(dict(zip(FIELDS, parts)))
    return runs


def _gpu(row):
    if row["gpu_max"] == "na":
        return "na"
    return _int(row["gpu_max"]) if row["gpu_fail"] == "0" else None


def _uptime_in(row, lo, hi):
    u = _int(row["uptime_cs"])
    return u is not None and lo <= u <= hi


def _cover(rows, start, end):
    """The rows from the last one at or before START to the first one at or after END."""
    ups = [(_int(r["uptime_cs"]), r) for r in rows]
    ups = [(u, r) for u, r in ups if u is not None]
    before = [i for i, (u, _) in enumerate(ups) if u <= start]
    after = [i for i, (u, _) in enumerate(ups) if u >= end]
    if not before or not after:
        return []   # the run did not cover the whole load
    return [r for _, r in ups[before[-1]:after[0] + 1]]


def _window_gpu(values):
    """A window's GPU reading: unknown if any interval is, "na" if none applies, else the highest."""
    if any(v is None for v in values):
        return None
    nums = [v for v in values if v != "na"]
    return max(nums) if nums else "na"


def rates(rows, clk_tck=100, window=1, trim_start=0, trim_end=0, between=None, cover=None):
    """Rates over consecutive windows of one run's rows (see the module docstring)."""
    if between is not None:
        rows = [x for x in rows if _uptime_in(x, *between)]
    if cover is not None:
        rows = _cover(rows, *cover)
    rows = rows[trim_start:len(rows) - trim_end]
    out = []
    for i in range(0, len(rows) - window, window):
        span = rows[i:i + window + 1]
        ua, ub = _int(span[0]["uptime_cs"]), _int(span[-1]["uptime_cs"])
        if ua is None or ub is None or ub <= ua:
            continue
        dt = ub - ua
        r = {"epoch": _int(span[-1]["epoch"]), "span_s": dt / 100, "gpu": _window_gpu([_gpu(x) for x in span[1:]])}
        for field, name in COUNTERS:
            vals = [_int(x[field]) for x in span]
            if any(v is None for v in vals) or any(b < a for a, b in zip(vals, vals[1:])):
                r[name] = None   # unreadable somewhere in the window, or the counter went back
            elif field == "cpu_usec":
                r[name] = (vals[-1] - vals[0]) / dt / 100                  # microseconds over centiseconds: percent of one core
            elif field.endswith("_ticks"):
                r[name] = (vals[-1] - vals[0]) * 10000 / (clk_tck * dt)    # clock ticks: percent of one core
            else:
                r[name] = (vals[-1] - vals[0]) * 100 / dt                  # bytes per second
        out.append(r)
    return out


def percentile(values, p):
    """Nearest-rank percentile of a non-empty list."""
    s = sorted(values)
    return s[max(1, math.ceil(p * len(s) / 100)) - 1]


def summarize(rs):
    res = {}
    for name in METRICS:
        vals = [r[name] for r in rs if r[name] is not None and r[name] != "na"]
        s = {"n": len(vals), "missing": sum(1 for r in rs if r[name] is None),
             "na": sum(1 for r in rs if r[name] == "na")}
        if vals:
            s["min"] = min(vals)
            for key, p in PERCENTILES:
                s[key] = percentile(vals, p)
            s["max"] = max(vals)
        res[name] = s
    return res


def analyze(paths, window=1, trim_start=0, trim_end=0, clk_tck=None, between=None, cover=None):
    rows = []
    for path in paths:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            runs = parse_runs(fh)
        for n, run in enumerate(runs):
            hz = clk_tck or run["clk_tck"] or 100
            for r in rates(run["rows"], hz, window, trim_start, trim_end, between, cover):
                rows.append({"file": path, "run": n, **r})
    return {"intervals": len(rows), "summary": summarize(rows), "rows": rows}


def _fmt(v):
    if v is None:
        return ""
    return f"{v:.4g}" if isinstance(v, float) else str(v)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Summarize autodl_guard.sh sample output.")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--between", type=int, nargs=2, metavar=("START_CS", "END_CS"),
                    help="keep only the rows whose uptime_cs lies in this range")
    ap.add_argument("--load-window", metavar="LOG", help="like --between, the steady window of the load in LOG")
    ap.add_argument("--load-lead", type=int, default=10, metavar="S",
                    help="seconds after LOAD START where --load-window begins (default 10)")
    ap.add_argument("--load-cover", metavar="LOG", help="every interval that overlaps the load in LOG (short loads)")
    ap.add_argument("--trim-start", type=int, default=0, metavar="N", help="drop N intervals at the start of every run")
    ap.add_argument("--trim-end", type=int, default=0, metavar="N", help="drop N intervals at the end of every run")
    ap.add_argument("--window", type=int, default=1, metavar="N", help="join N consecutive intervals into one")
    ap.add_argument("--clk-tck", type=int, default=None, metavar="HZ",
                    help="clock ticks per second (default: the run's parameter line, else 100)")
    ap.add_argument("--rows", action="store_true", help="also list every interval")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    a = ap.parse_args(argv)
    if a.trim_start < 0 or a.trim_end < 0 or a.load_lead < 0 or a.window < 1 or (a.clk_tck is not None and a.clk_tck < 1):
        ap.error("--trim-start, --trim-end and --load-lead must be at least 0, --window and --clk-tck at least 1")
    if sum(x is not None for x in (a.between, a.load_window, a.load_cover)) > 1:
        ap.error("give only one of --between, --load-window and --load-cover")
    if a.between and a.between[0] > a.between[1]:
        ap.error("--between needs START_CS <= END_CS")
    between, cover = a.between, None
    try:
        if a.load_window:
            between = load_window(a.load_window, a.load_lead)
        if a.load_cover:
            cover = load_marks(a.load_cover)
    except (OSError, ValueError) as e:
        ap.error(str(e))
    res = analyze(a.files, a.window, a.trim_start, a.trim_end, a.clk_tck, between, cover)
    if (between or cover) and res["intervals"] == 0:
        print("error: the chosen window holds no complete interval (for a load of a few seconds use --load-cover)",
              file=sys.stderr)
        return 1
    if a.json:
        if not a.rows:
            del res["rows"]
        print(json.dumps(res, indent=1))
        return 0
    cols = ("file", "run", "epoch", "span_s") + METRICS
    if a.rows:
        print("\t".join(cols))
        for r in res["rows"]:
            print("\t".join(_fmt(r[c]) for c in cols))
    print(f"intervals: {res['intervals']}")
    for name in METRICS:
        s = res["summary"][name]
        line = f"{name:9s} n={s['n']} missing={s['missing']} na={s['na']}"
        if s["n"]:
            keys = ("min",) + tuple(k for k, _ in PERCENTILES) + ("max",)
            line += " " + " ".join(f"{k}={s[k]:.4g}" for k in keys)
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
