"""Local record of one calibration autostart probe: a small JSON file per probe, always replaced
whole (write a temporary file, fsync, rename), so an interrupted update never leaves it unreadable.

Usage: python probe_state.py set FILE KEY=VALUE ...   create or update FILE (state, path, tmp, marker, ...)
       python probe_state.py pending DIR              for the probe-*.json files in DIR that are not
                                                      "cleaned": print PCOUNT=<n>, then PFILE, PROBE, PTMP,
                                                      PMARK and PSHA of the newest one (nothing more if n = 0)
"""
import json
import os
import pathlib
import sys
import time


def write(path, data):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main(argv):
    if len(argv) >= 3 and argv[0] == "set":
        path = pathlib.Path(argv[1])
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        for kv in argv[2:]:
            key, sep, value = kv.partition("=")
            if not sep or not key:
                sys.exit(f"bad field {kv!r} (use KEY=VALUE)")
            data[key] = value
        data["t"] = int(time.time())
        write(path, data)
        return 0
    if len(argv) == 2 and argv[0] == "pending":
        recs = []
        for p in sorted(pathlib.Path(argv[1]).glob("probe-*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("state") != "cleaned":
                recs.append((int(d.get("t", 0)), p, d))
        print(f"PCOUNT={len(recs)}")
        if recs:
            _, p, d = max(recs, key=lambda r: r[0])
            print(f"PFILE={p.as_posix()}")
            for key, name in (("path", "PROBE"), ("tmp", "PTMP"), ("marker", "PMARK"), ("sha256", "PSHA")):
                print(f"{name}={d[key]}")
        return 0
    sys.exit(__doc__)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
