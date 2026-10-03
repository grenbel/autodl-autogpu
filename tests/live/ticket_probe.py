"""Development only (plan task 11.14), not in the released package: put a clone ticket on an instance, or take it
away, with no clone record, to see live that a ticket nobody takes over shuts its instance down.

  python tests/live/ticket_probe.py put ALIAS --instance ID --mark MARK --in SECONDS [--grace SECONDS]
  python tests/live/ticket_probe.py remove ALIAS --instance ID --mark MARK

The ticket is ctl's own text, written by ctl's own remote script, which refuses where the loop could not run and where
a ticket with another mark is. It names the instance's own host, so its loop runs from the next container start, and a
source that no instance is, so the loop never takes the instance for its source. Its deadline is the instance's clock
plus --in; --grace (default 180) is how long each boot runs before the loop may shut it down. put compares what the
instance holds afterwards with what it meant to write. remove takes the lock the loop decides under, refuses when the
loop has issued the shutdown in this boot already (exit 4, the ticket stays: remove it in the next boot), checks the
mark, removes the file, then waits for a loop of this boot to leave; it answers the receipt's last lines. Every remote
command first checks the instance's host name (ctl's host guard). Prints one JSON object; exit 0 when done, else
ctl's exit codes (13: the alias leads to another instance, nothing was done)."""
import argparse
import hashlib
import json
import pathlib
import shlex
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
import autodl_ctl as ctl  # noqa: E402

SOURCE = "autodl-container-ticket-probe"   # the host name of no instance


def remove_script(mark: str, cfg: dict) -> str:
    """Remote: under the ticket's lock, remove the ticket that carries MARK (another one is left alone); then wait
    for a loop of this boot to leave, and answer whether one still runs and the receipt's last lines."""
    q = shlex.quote
    wait_s = 2 * int(cfg["period"]) + 10
    # as ctl ticket clear does: a shutdown noted in the receipt was issued in this boot, whether or not the loop that
    # issued it still runs, so the instance is going down and nothing is reported as taken away
    return ctl._ticket_sh(cfg) + f"""take_lock
if grep -q "^{mark} shutdown " "$R" 2> /dev/null; then
  echo "error: the ticket's loop has issued the shutdown of this instance already: the ticket stays" >&2
  exit 4
fi
if [ -e "$T" ]; then
  [ "$(tfield mark)" = {q(mark)} ] || {{ echo "error: the ticket here carries another mark: it is left alone" >&2; exit 3; }}
  rm -f -- "$T"
  [ ! -e "$T" ] || {{ echo "error: cannot remove $T" >&2; exit 1; }}
  echo removed=1
else
  echo removed=0
fi
flock -u 9
exec 9>&-
i=0
while loop_alive && [ "$i" -lt {wait_s} ]; do i=$((i + 1)); sleep 1; done
if loop_alive; then echo loop=1; else echo loop=0; fi
if [ -r "$R" ]; then tail -n 20 "$R" | while IFS= read -r line; do echo "receipt=$line"; done; fi
"""


def put(alias: str, iid: str, mark: str, secs: int, grace: int) -> tuple:
    cfg = ctl.TICKET
    hosts = [ctl.host_of(iid)]
    rc, kv, said = ctl.ticket_remote(alias, ctl.ticket_write_script(mark, secs, cfg),
                                     stdin=ctl.ticket_text(mark, SOURCE, hosts, "@DEADLINE@", grace, cfg).encode())
    if rc != 0:
        return rc, {"ok": False, "error": said or "the remote command failed"}
    d = kv.get("deadline", "")
    want = hashlib.sha256(ctl.ticket_text(mark, SOURCE, hosts, d, grace, cfg).encode()).hexdigest() if d.isdigit() else None
    if want is None or kv.get("sha256") != want:
        return ctl.EXIT_ERR, {"ok": False, "error": "the ticket does not read back as it was written: remove it",
                              "deadline": d, "sha256": kv.get("sha256")}
    return 0, {"ok": True, "mark": mark, "source": SOURCE, "hosts": hosts, "deadline": int(d), "grace_s": grace,
               "sha256": want}


def remove(alias: str, mark: str) -> tuple:
    cfg = ctl.TICKET
    rc, kv, said = ctl.ticket_remote(alias, remove_script(mark, cfg), timeout=2 * int(cfg["period"]) + 100)
    res = {"removed": kv.get("removed") == "1", "loop": kv.get("loop") == "1", "receipts": kv["receipts"]}
    if rc != 0:
        return rc, {"ok": False, **res, "error": said or "the remote command failed"}
    if res["loop"]:
        return ctl.EXIT_UNCERTAIN, {"ok": False, **res, "error": "the ticket is gone, but the loop has not left"}
    return 0, {"ok": True, **res}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ticket_probe.py", description="put a clone ticket on an instance, or take it "
                                "away, with no clone record (development only)")
    p.add_argument("what", choices=("put", "remove"))
    p.add_argument("alias")
    p.add_argument("--instance", required=True)
    p.add_argument("--mark", required=True, help="16 hex digits, not a clone's transaction ID")
    p.add_argument("--in", dest="secs", type=int, help="put: the deadline, this many seconds from now (60 to 3600)")
    p.add_argument("--grace", type=int, default=180, help="put: seconds each boot runs first (60 to 3600)")
    a = p.parse_args(argv)
    if not ctl.ALIAS_RE.match(a.alias):
        p.error(f"bad ssh alias {a.alias!r}")
    if not ctl.INSTANCE_RE.match(a.instance):
        p.error(f"--instance {a.instance!r}: an instance ID as the console writes it")
    if not ctl.TXN_RE.match(a.mark):
        p.error("--mark: 16 hex digits")
    if a.what == "put" and (a.secs is None or not 60 <= a.secs <= 3600 or not 60 <= a.grace <= 3600):
        p.error("put: --in from 60 to 3600 seconds, --grace from 60 to 3600 seconds")
    ctl.BOUND = a.instance   # every remote command first checks that the alias leads to this instance
    try:
        rc, res = put(a.alias, a.instance, a.mark, a.secs, a.grace) if a.what == "put" else remove(a.alias, a.mark)
    except ctl.WrongHost as e:
        rc, res = ctl.EXIT_MISMATCH, {"ok": False, "error": f"{e.alias} leads to {e.host or 'a host that gives no name'}, "
                                                            f"not to autodl-container-{a.instance}: nothing was done"}
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
