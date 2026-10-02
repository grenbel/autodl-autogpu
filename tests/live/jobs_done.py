"""Check that the named guard jobs have all ended with exit status 0.

Usage: bash scripts/ctl status ALIAS | python tests/live/jobs_done.py JOB [JOB ...]
Reads the JSON that ctl status prints, shows each job's state, and exits 0 only if every named
job is done:0 (a running, failed or missing job exits 1).
"""
import json
import sys

names = sys.argv[1:]
states = {j.get("name"): j.get("state", "") for j in json.load(sys.stdin).get("jobs", [])}
ok = bool(names)
for n in names:
    s = states.get(n, "missing")
    print(f"{n}: {s}")
    ok = ok and s == "done:0"
sys.exit(0 if ok else 1)
