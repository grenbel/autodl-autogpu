"""The clone ticket (scripts/autodl_ctl.py, plan task 11.6) against a real bash with flock: on Windows in WSL (which
distribution: tests/local_tools.py), elsewhere directly. The ticket's text and its loop are the ones ctl writes; only
the paths, the period and the shutdown command are those of the test (a temporary directory, one second, a stub that
notes the call). Nothing is shut down and nothing leaves this machine."""
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import autodl_ctl as ctl  # noqa: E402
import local_tools  # noqa: E402

WINDOWS = os.name == "nt"
PREFIX = local_tools.wsl_command() if WINDOWS else []
NEEDED = ("flock", "setsid", "sha256sum")


def _sh(*args, **kw):
    return subprocess.run([*PREFIX, *args], capture_output=True, **kw)


def _have() -> bool:
    if WINDOWS:
        return local_tools.have_wsl(*NEEDED)
    return all(shutil.which(p) for p in ("bash", *NEEDED))


pytestmark = pytest.mark.skipif(not _have(), reason="needs bash with flock, setsid and sha256sum (on Windows: in "
                                                    f"{local_tools.wsl_name()})")

SOURCE = "abcd123456-1234abcd"        # on the host abcd123456
CLONE = "ffff000000-0000ffff"         # on the host ffff000000, which the ticket allows
MARK = "00112233445566aa"
UNAME_STUB = """#!/bin/bash
# stands in for uname: -n prints the host name the test wrote next to the stubs
if [ "$1" = -n ]; then cat "$(dirname "$0")/../hostname"; else exec /usr/bin/uname "$@"; fi
"""
STOP_STUB = """#!/bin/bash
# stands in for /usr/bin/shutdown: notes the call and stops nothing
date +%s >> "$(dirname "$0")/../stopped"
"""


class Box:
    """A temporary directory that plays the instance: the ticket, its lock files and receipt, the stubs."""

    def __init__(self, d):
        self.d = d
        self.cfg = {"dir": d, "ticket": f"{d}/ticket.sh", "lock": f"{d}/lock", "alive": f"{d}/alive",
                    "receipt": f"{d}/receipt", "period": 1, "shutdown": f"{d}/bin/stop", "path": f"{d}/bin:/usr/bin:/bin"}

    def put(self, rel, text, mode="644"):
        r = _sh("bash", "-c", f"mkdir -p \"$(dirname {self.d}/{rel})\" && cat > {self.d}/{rel} && chmod {mode} {self.d}/{rel}",
                input=text.encode())
        assert r.returncode == 0, r.stderr

    def host(self, iid):
        self.put("hostname", f"autodl-container-{iid}\n")

    def ticket(self, deadline, grace=2, hosts=("ffff000000",), mark=MARK, source=SOURCE):
        text = ctl.ticket_text(mark, f"autodl-container-{source}", list(hosts), deadline, grace, self.cfg)
        self.put("ticket.sh", text)
        return text

    def cat(self, rel) -> str:
        return _sh("bash", "-c", f"cat {self.d}/{rel} 2> /dev/null").stdout.decode()

    def drive(self, script, timeout=60) -> dict:
        """Run a bash snippet with $B (the box), $T $L $A $R (ticket, lock, alive, receipt), the stubs first on PATH,
        and loop, which starts the ticket's loop in the background as the ticket's own hook would ($LP is its PID; it
        is killed when the snippet ends). The key=value words it prints come back as a dict."""
        pre = (f"B={shlex.quote(self.d)}; T=$B/ticket.sh; L=$B/lock; A=$B/alive; R=$B/receipt; "
               'export PATH="$B/bin:$PATH"; LP=; LP2=; '
               'loop() { bash -p -c "$(cat "$B/loop.sh")" autodl-autogpu-clone-ticket-loop "$T" "$L" "$A" "$R" 1 "$B/bin/stop" '
               '< /dev/null > /dev/null 2>&1 & }; '
               "trap 'kill $LP $LP2 2> /dev/null || :' EXIT; ")   # (never the snippet's exit status, set -e or not)
        r = _sh("bash", "-c", pre + script, timeout=timeout)
        out = r.stdout.decode()
        got = dict(w.split("=", 1) for w in out.split() if "=" in w)
        got["_out"], got["_err"], got["_rc"] = out, r.stderr.decode(), r.returncode
        return got


@pytest.fixture
def box():
    r = _sh("mktemp", "-d", "/tmp/autodl-ticket-test.XXXXXX")
    d = r.stdout.decode().strip()
    assert r.returncode == 0 and d.startswith("/tmp/autodl-ticket-test.")
    b = Box(d)
    b.put("bin/uname", UNAME_STUB, "755")
    b.put("bin/stop", STOP_STUB, "755")
    b.put("loop.sh", ctl.TICKET_LOOP)
    b.host(CLONE)
    yield b
    # a loop that a test left running (ctl ticket start detaches it) is ended by its recorded PID; then only the
    # directory this fixture made is removed
    _sh("bash", "-c", f'p="$(cat {d}/alive 2> /dev/null)"; case "$p" in ""|*[!0-9]*) ;; *) kill "$p" 2> /dev/null ;; esac; '
                      f"rm -rf -- {d}")


def now() -> int:
    return int(time.time())


# ---- the ticket as a file that shells source ----
def test_sourcing_the_ticket_does_nothing_outside_process_1(box):
    text = box.ticket(now() - 60, grace=1)
    assert text.endswith("\n") and not text.endswith("\n\n") and "'" not in ctl.TICKET_LOOP
    got = box.drive('bash -n "$T"; echo "syntax=$?"; v1=; v2=; f1=; f2=; v1="$(compgen -v)"; f1="$(declare -F)"; '
                    '. "$T" > "$B/out" 2>&1; echo "rc=$?"; v2="$(compgen -v)"; f2="$(declare -F)"; '
                    'if [ "$v1" = "$v2" ] && [ "$f1" = "$f2" ]; then echo same=1; else echo same=0; fi; '
                    'echo "printed=$(wc -c < "$B/out")"; sleep 2.5; '
                    'for f in alive receipt stopped; do if [ -e "$B/$f" ]; then echo "$f=1"; else echo "$f=0"; fi; done')
    assert got["syntax"] == "0" and got["rc"] == "0" and got["same"] == "1" and got["printed"] == "0", got
    assert (got["alive"], got["receipt"], got["stopped"]) == ("0", "0", "0"), got   # no loop was started


def _other_shell():
    """A shell that is not bash (it has no BASHPID), as the process 1 of a container might be; None without one."""
    for sh in ("dash", "sh"):
        r = _sh(sh, "-c", 'echo "${BASHPID:-none}"')
        if r.returncode == 0 and r.stdout.decode().strip() == "none":
            return sh
    return None


@pytest.mark.parametrize("shell, name", [("bash", "/init/boot/boot.sh"), ("bash", "another-boot-script"),
                                         ("other", "/init/boot/boot.sh")])
def test_the_hook_starts_the_loop_in_process_1(box, shell, name):
    """A container start that sources the ticket starts the loop, whatever shell process 1 is and whatever its script is
    called: a clone that boots another way than the source did must not be left without the loop."""
    if _sh("unshare", "--user", "--map-root-user", "--pid", "--fork", "true").returncode != 0:
        pytest.skip("this bash cannot make a new PID namespace")
    sh = shell if shell == "bash" else _other_shell()
    if sh is None:
        pytest.skip("no shell other than bash here")
    box.ticket(now() - 60, grace=1)
    got = box.drive(f"unshare --user --map-root-user --pid --fork {sh} -c "
                    "'up=keep; . \"$1\"; echo \"rc=$? up=$up pid=$$\"; sleep 1.5; if flock -n \"$2\" true; then echo loop=0; else "
                    "echo loop=1; fi; sleep 3; echo \"calls=$(wc -l < \"$3\" 2> /dev/null)\"' "
                    f'{name} "$T" "$A" "$B/stopped"')
    assert got["rc"] == "0" and got["up"] == "keep" and got["pid"] == "1" and got["loop"] == "1", got
    assert int(got["calls"]) >= 1 and f"{MARK} shutdown " in box.cat("receipt"), got


# ---- the loop ----
ALIVE = 'if kill -0 "$LP" 2> /dev/null; then echo running=1; else echo running=0; fi; '
HELD = 'if flock -n "$A" true; then echo held=0; else echo held=1; fi; '
CALLS = 'echo "calls=$(cat "$B/stopped" 2> /dev/null | wc -l)"; '


def test_the_loop_leaves_at_once_on_a_host_that_is_not_allowed(box):
    box.ticket(now() - 60, grace=1)
    box.host("eeee111111-1111eeee")
    got = box.drive("loop; LP=$!; sleep 2.5; " + ALIVE + CALLS + 'if [ -e "$R" ]; then echo receipt=1; else echo receipt=0; fi')
    assert got["running"] == "0" and got["calls"] == "0" and got["receipt"] == "0", got


def test_the_loop_leaves_at_once_on_the_source(box):
    box.ticket(now() - 60, grace=1, hosts=("abcd123456", "ffff000000"))   # even if its host were allowed
    box.host(SOURCE)
    got = box.drive("loop; LP=$!; sleep 2.5; " + ALIVE + CALLS)
    assert got["running"] == "0" and got["calls"] == "0", got


def test_the_loop_shuts_down_once_due_and_past_the_grace(box):
    box.ticket(now() - 60, grace=3)
    got = box.drive("loop; LP=$!; sleep 2; echo \"early=$(cat \"$B/stopped\" 2> /dev/null | wc -l)\"; sleep 3.5; "
                    + ALIVE + CALLS)
    assert got["early"] == "0", got                     # due, but this boot was not three seconds old yet
    assert int(got["calls"]) >= 1 and got["running"] == "1", got   # it goes on until the instance really stops
    line = box.cat("receipt").splitlines()[0].split()
    assert line[0] == MARK and line[1] == "shutdown" and line[2].isdigit() and abs(int(line[3]) - now()) < 30, line
    assert box.cat("alive").strip() == line[2]          # the loop's own PID is in its lock file


def test_the_loop_does_nothing_before_the_deadline(box):
    box.ticket(now() + 3600, grace=1)
    got = box.drive("loop; LP=$!; sleep 4; " + ALIVE + HELD + CALLS)
    assert got["running"] == "1" and got["held"] == "1" and got["calls"] == "0", got


def test_a_ticket_that_is_gone_ends_the_loop_with_a_receipt(box):
    box.ticket(now() + 3600, grace=1)
    got = box.drive('loop; LP=$!; sleep 1.5; rm -f "$T"; sleep 2.5; ' + ALIVE + HELD + CALLS)
    assert got["running"] == "0" and got["held"] == "0" and got["calls"] == "0", got
    line = box.cat("receipt").split()
    assert line[:2] == [MARK, "gone"] and len(line) == 4, line


def test_another_mark_ends_the_loop(box):
    box.ticket(now() - 60, grace=30)
    other = ctl.ticket_text("ffffeeeeddddcccc", f"autodl-container-{SOURCE}", ["ffff000000"], now() - 60, 1, box.cfg)
    box.put("other.sh", other)
    got = box.drive('loop; LP=$!; sleep 1.5; cp "$B/other.sh" "$T"; sleep 2.5; ' + ALIVE + CALLS)
    assert got["running"] == "0" and got["calls"] == "0", got   # it is not this loop's ticket any more
    assert box.cat("receipt").split()[:2] == [MARK, "gone"]


def test_a_later_deadline_is_read_without_a_restart(box):
    box.ticket(now() - 60, grace=3)   # it would shut down at three seconds
    later = ctl.ticket_text(MARK, f"autodl-container-{SOURCE}", ["ffff000000"], now() + 3600, 3, box.cfg)
    box.put("later.sh", later)
    got = box.drive('loop; LP=$!; sleep 1.5; cp "$B/later.sh" "$T"; sleep 4; ' + ALIVE + CALLS)
    assert got["running"] == "1" and got["calls"] == "0", got


def test_an_unreadable_deadline_counts_as_due(box):
    box.ticket("soon", grace=1)
    assert "# deadline=soon\n" in box.cat("ticket.sh")
    got = box.drive("loop; LP=$!; sleep 4; " + CALLS)
    assert int(got["calls"]) >= 1, got


def test_only_one_loop_runs(box):
    box.ticket(now() + 3600, grace=1)
    got = box.drive('loop; LP=$!; sleep 1; loop; LP2=$!; sleep 1.5; ' + ALIVE +
                    'if kill -0 "$LP2" 2> /dev/null; then echo second=1; else echo second=0; fi; echo "pid=$LP"')
    assert got["running"] == "1" and got["second"] == "0" and box.cat("alive").strip() == got["pid"], got


def test_a_killed_loop_frees_its_lock(box):
    box.ticket(now() + 3600, grace=1)
    got = box.drive('loop; LP=$!; sleep 1.3; ' + HELD.replace("held=", "before=") + 'kill -9 "$LP"; sleep 0.3; ' + HELD)
    assert got["before"] == "1" and got["held"] == "0", got   # its sleep does not hold the lock on


def test_clearing_and_shutting_down_never_both_happen(box):
    # the ticket is due; the test holds the lock the loop decides under, takes the ticket away as a clear does, and
    # lets go: the loop finds the ticket gone and leaves without shutting down
    box.ticket(now() - 60, grace=1)
    got = box.drive('exec 5>> "$L"; flock 5; loop; LP=$!; sleep 3; ' + CALLS.replace("calls=", "waiting=") +
                    'rm -f "$T"; flock -u 5; sleep 2.5; ' + ALIVE + CALLS)
    assert got["waiting"] == "0" and got["calls"] == "0" and got["running"] == "0", got
    assert box.cat("receipt").split()[:2] == [MARK, "gone"]


def test_a_clear_after_the_shutdown_was_issued_is_refused(box):
    box.ticket(now() - 60, grace=1)
    box.put("clear.sh", ctl.ticket_clear_script(MARK, False, box.cfg))
    got = box.drive('loop; LP=$!; sleep 3; ' + CALLS + 'bash "$B/clear.sh"; echo "rc=$?"; '
                    'if [ -e "$T" ]; then echo ticket=1; else echo ticket=0; fi')
    assert int(got["calls"]) >= 1 and got["rc"] == "4" and got["ticket"] == "1", got


def test_a_shutdown_receipt_refuses_the_clear_though_the_loop_is_gone(box, monkeypatch):
    """The loop notes the shutdown and then dies (the shutdown itself may end it). The guard is armed and alive, so
    nothing else would stop the clear: the receipt of this boot alone has to."""
    monkeypatch.setattr(ctl, "GUARD_PATH", f"{box.d}/guard.sh")
    box.put("guard.sh", GUARD_STUB, "755")
    box.put("guard_status", ARMED)
    box.ticket(now() - 60, grace=1)
    box.put("clear.sh", ctl.ticket_clear_script(MARK, False, box.cfg))
    got = box.drive('loop; LP=$!; sleep 3; ' + CALLS + 'kill -9 "$LP"; sleep 0.5; ' + HELD + 'bash "$B/clear.sh"; echo "rc=$?"; '
                    'if [ -e "$T" ]; then echo ticket=1; else echo ticket=0; fi')
    assert int(got["calls"]) >= 1 and got["held"] == "0", got     # the shutdown was issued, and no loop is left
    assert got["rc"] == "4" and got["ticket"] == "1", got


# ---- ctl ticket: the commands, their remote side run for real in the box ----
from test_clone import ID, NEW, advance, record, started, update  # noqa: E402
from test_store import rc_json  # noqa: E402

GUARD_STUB = """#!/bin/bash
# stands in for the guard: status prints what the test wrote
if [ "$1" = status ]; then cat "$(dirname "$0")/guard_status"; else exit 1; fi
"""
ARMED = "armed_this_boot=1\narmed_by=arm\nneeds_rearm=0\ndaemon_alive=1\n"
HOSTS = "ffff000000,eeee111111"


class Remote:
    """What ssh would reach: the alias says which instance, and the box plays it (its host name is set accordingly)."""

    def __init__(self, box):
        self.box, self.calls, self.stub_path = box, [], False

    def argv(self, ssh_argv):
        alias = ssh_argv[-2]
        self.calls.append(alias)
        d = self.box.d
        pre = (f"printf '%s\\n' autodl-container-{ {'demo': ID, 'demo-c': NEW}[alias] } > {d}/hostname; "
               f'uname() {{ if [ "$1" = -n ]; then cat {d}/hostname; else command uname "$@"; fi; }}; ')
        if self.stub_path:
            pre += f"export PATH={d}/bin:$PATH; "
        return [*PREFIX, "bash", "-c", pre + ssh_argv[-1]]


@pytest.fixture
def cl(box, capsys, clock, tmp_path, monkeypatch):
    """A clone record opened for ID (hosts ffff000000 and eeee111111), the aliases demo (the source) and demo-c (the new
    instance) verified, and ctl's ssh led into the box: (the remote, the transaction ID)."""
    rem = Remote(box)
    txn = started(capsys, clock, tmp_path)
    with ctl.Store() as st:
        st.data["aliases"]["demo"] = {"instance": ID, "at": ctl.now_s()}
        st.data["aliases"]["demo-c"] = {"instance": NEW, "at": ctl.now_s()}
        st.save()
    monkeypatch.setattr(ctl, "RUNNER", lambda argv, **kw: subprocess.run(rem.argv(argv), **kw))
    monkeypatch.setattr(ctl, "TICKET", box.cfg)
    monkeypatch.setattr(ctl, "GUARD_PATH", f"{box.d}/guard.sh")
    monkeypatch.setenv("AUTODL_SSH", "autodl-test-no-such-ssh")   # never a real ssh
    box.put("guard.sh", GUARD_STUB, "755")
    box.put("guard_status", ARMED)
    return rem, txn


def tk(capsys, *args):
    return rc_json(capsys, "ticket", *args)


def written(capsys, cl) -> str:
    """The ticket written on the source, as the flow does it: the transaction ID."""
    rem, txn = cl
    assert update(capsys, txn, "--stage", "ticket")[0] == 0
    rc, res = tk(capsys, "write", "demo", "--txn", txn, "--hosts", HOSTS, "--deadline", "45m")
    assert rc == 0, res
    return txn


def on_the_clone(capsys, cl) -> str:
    """The ticket written, the clone made and its instance noted: what the new instance carries is the same file."""
    txn = written(capsys, cl)
    advance(capsys, txn, "clicked")
    assert update(capsys, txn, "--set", f"instance={NEW}")[0] == 0
    return txn


def test_ticket_commands_need_the_transaction_and_the_right_instance(box, capsys, cl):
    rem, txn = cl
    write = ["write", "demo", "--txn", txn, "--hosts", HOSTS, "--deadline", "45m"]
    assert tk(capsys, *write)[0] == ctl.EXIT_ERR                      # the record is at opened, not at ticket
    assert update(capsys, txn, "--stage", "ticket")[0] == 0
    for args in (["write", "demo-c", "--txn", txn, "--hosts", HOSTS, "--deadline", "45m"],      # not the source
                 ["write", "demo", "--txn", "ffffeeeeddddcccc", "--hosts", HOSTS, "--deadline", "45m"],
                 ["write", "demo", "--txn", txn, "--hosts", "ffff000000", "--deadline", "45m"],  # other hosts
                 ["write", "demo", "--txn", txn, "--hosts", HOSTS, "--deadline", "30s"],
                 ["write", "demo", "--hosts", HOSTS, "--deadline", "45m"],                       # no transaction
                 ["extend", "demo", "--txn", txn, "--deadline", "30m"],      # no new instance is noted yet
                 ["start", "demo", "--txn", txn],
                 ["clear", "demo", "--txn", txn],                            # the source, without --source
                 ["clear", "demo-c", "--txn", txn, "--source"]):
        rc, res = tk(capsys, *args)
        assert rc == ctl.EXIT_ERR, (args, res)
    assert rem.calls == [] and box.cat("ticket.sh") == ""             # nothing was sent
    assert tk(capsys, *write)[0] == 0 and rem.calls == ["demo"]       # the right one goes through


def test_write_puts_the_ticket_and_notes_it(box, capsys, cl, tmp_path):
    rem, txn = cl
    assert update(capsys, txn, "--stage", "ticket")[0] == 0
    rc, res = tk(capsys, "write", "demo", "--txn", txn, "--hosts", "eeee111111,ffff000000", "--deadline", "45m")
    assert rc == 0 and abs(res["deadline"] - (now() + 2700)) < 30 and res["grace_s"] == 900, res
    want = ctl.ticket_text(txn, f"autodl-container-{ID}", ["ffff000000", "eeee111111"], res["deadline"], 900, box.cfg)
    assert box.cat("ticket.sh") == want
    r = record(txn)
    assert r["source_ticket"] == "present" and r["ticket_deadline"] == res["deadline"]
    assert '"source_ticket": "present"' in (tmp_path / ".autodl" / "clone_pending.json").read_text(encoding="utf-8")
    rc, got = tk(capsys, "read", "demo", "--txn", txn)
    assert rc == 0 and got["present"] and got["mark_matches"] and got["is_source"] and not got["allowed"], got
    assert got["loop"] is False and got["hosts"] == ["ffff000000", "eeee111111"] and got["deadline"] == res["deadline"]
    assert tk(capsys, "read", "demo", "--txn", "ffffeeeeddddcccc")[0] == ctl.EXIT_ERR
    time.sleep(1.1)
    rc, again = tk(capsys, "write", "demo", "--txn", txn, "--hosts", HOSTS, "--deadline", "2h", "--grace", "20m")
    assert rc == 0 and again["deadline"] > res["deadline"] + 4000 and record(txn)["ticket_deadline"] == again["deadline"]
    assert "# grace=1200\n" in box.cat("ticket.sh")


def test_write_leaves_another_clones_ticket_alone(box, capsys, cl):
    rem, txn = cl
    assert update(capsys, txn, "--stage", "ticket")[0] == 0
    theirs = box.ticket(now() + 999, mark="ffffeeeeddddcccc")
    rc, res = tk(capsys, "write", "demo", "--txn", txn, "--hosts", HOSTS, "--deadline", "45m")
    assert rc == ctl.EXIT_REFUSED and "another clone" in res["error"], res
    assert box.cat("ticket.sh") == theirs and "source_ticket" not in record(txn)


def test_a_ticket_that_does_not_read_back_is_not_counted_as_written(box, capsys, cl):
    rem, txn = cl
    assert update(capsys, txn, "--stage", "ticket")[0] == 0
    box.put("bin/sha256sum", "#!/bin/bash\necho \"" + "0" * 64 + "  -\"\n", "755")   # the instance answers another digest
    rem.stub_path = True
    rc, res = tk(capsys, "write", "demo", "--txn", txn, "--hosts", HOSTS, "--deadline", "45m")
    assert rc == ctl.EXIT_ERR and "read back" in res["error"], res
    assert "source_ticket" not in record(txn)
    assert update(capsys, txn, "--stage", "reserve")[0] == ctl.EXIT_ERR   # so the clone cannot go on


def test_without_flock_no_ticket_is_written(box):
    script = ctl.ticket_write_script(MARK, 60, box.cfg)
    r = _sh("bash", "-c", "PATH=/nonexistent; " + script, input=b"x\n")
    assert r.returncode == 3 and b"flock" in r.stderr and box.cat("ticket.sh") == ""


LOOP_TOOLS = ("setsid", "flock", "uname", "date", "sleep")


@pytest.mark.parametrize("lack", [*LOOP_TOOLS, "a-date-that-tells-no-time", "the-shutdown-command"])
def test_without_what_the_loop_needs_no_ticket_is_written(box, lack):
    """The loop runs with a PATH of its own, calls these and ends with the shutdown command. A ticket whose loop could
    not start, could not tell the time or could not shut down protects nothing: it is not written, so the clone stops.
    (The tools are links in a directory of the box; nothing is ever written through them.)"""
    have = [t for t in LOOP_TOOLS if t != lack and not (t == "date" and lack == "a-date-that-tells-no-time")]
    got = box.drive('mkdir -p "$B/tools" && for t in ' + " ".join(have) +
                    '; do ln -s "$(command -v "$t")" "$B/tools/$t" || exit 1; done; echo made=1')
    assert got.get("made") == "1", got
    if lack == "a-date-that-tells-no-time":
        box.put("tools/date", "#!/bin/bash\necho soon\n", "755")
    cfg = dict(box.cfg, path=f"{box.d}/tools")
    if lack == "the-shutdown-command":
        cfg["shutdown"] = f"{box.d}/bin/no-such-shutdown"
    r = _sh("bash", "-c", ctl.ticket_write_script(MARK, 60, cfg), input=b"x\n")
    word = {"a-date-that-tells-no-time": "date", "the-shutdown-command": "no-such-shutdown"}.get(lack, lack)
    assert r.returncode == 3 and word.encode() in r.stderr and box.cat("ticket.sh") == "", (lack, r.returncode, r.stderr)


def test_with_all_the_loop_needs_in_its_own_path_the_ticket_is_written(box):
    got = box.drive('mkdir -p "$B/tools" && for t in ' + " ".join(LOOP_TOOLS) +
                    '; do ln -s "$(command -v "$t")" "$B/tools/$t" || exit 1; done; echo made=1')
    assert got.get("made") == "1", got
    r = _sh("bash", "-c", ctl.ticket_write_script(MARK, 60, dict(box.cfg, path=f"{box.d}/tools")), input=b"x\n")
    assert r.returncode == 0 and box.cat("ticket.sh") == "x\n", r.stderr


class Died(BaseException):
    """The process is gone: nothing after this point of the command happens."""


def test_a_write_that_was_not_noted_still_has_to_be_cleared_before_the_close(box, capsys, cl, monkeypatch):
    """The ticket reaches the source and the process dies before the record is told. The record stays at ticket without
    source_ticket, and the clone is not closed over the ticket: ticket clear --source comes first, and finds it."""
    rem, txn = cl
    assert update(capsys, txn, "--stage", "ticket")[0] == 0

    def dies(*a, **kw):
        raise Died
    with monkeypatch.context() as m:
        m.setattr(ctl, "ticket_note", dies)
        with pytest.raises(Died):
            ctl.main(["ticket", "write", "demo", "--txn", txn, "--hosts", HOSTS, "--deadline", "45m"])
    capsys.readouterr()
    assert box.cat("ticket.sh") != "" and "source_ticket" not in record(txn)
    rc, res = rc_json(capsys, "clone-record", "close", "--txn", txn)
    assert rc == ctl.EXIT_ERR and "ticket clear" in res["reason"], res
    rc, res = tk(capsys, "clear", "demo", "--txn", txn, "--source")
    assert rc == 0 and res["removed"] is True and box.cat("ticket.sh") == "", res
    assert rc_json(capsys, "clone-record", "close", "--txn", txn)[0] == 0


def test_start_extend_and_clear_on_the_new_instance(box, capsys, cl):
    rem, txn = cl
    txn = on_the_clone(capsys, cl)
    rc, got = tk(capsys, "read", "demo-c", "--txn", txn)
    assert rc == 0 and got["allowed"] and not got["is_source"] and got["loop"] is False, got
    rc, res = tk(capsys, "start", "demo-c", "--txn", txn)
    assert rc == 0 and res["loop"] is True and res["started"] is True, res
    rc, res = tk(capsys, "start", "demo-c", "--txn", txn)
    assert rc == 0 and res["loop"] is True and res["started"] is False, res   # it runs already: not a second one
    rc, got = tk(capsys, "read", "demo-c")
    assert got["loop"] is True and got["loop_pid"].isdigit(), got
    before = box.cat("ticket.sh")
    rc, res = tk(capsys, "extend", "demo-c", "--txn", txn, "--deadline", "3h")
    assert rc == 0 and abs(res["deadline"] - (now() + 10800)) < 30, res
    after = box.cat("ticket.sh")
    changed = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(changed) == 1 and changed[0][1] == f"# deadline={res['deadline']}", changed
    assert record(txn)["ticket_deadline"] == res["deadline"]
    box.put("guard_status", ARMED.replace("armed_by=arm", "armed_by=boot"))   # armed by the autostart only
    rc, res = tk(capsys, "clear", "demo-c", "--txn", txn)
    assert rc == ctl.EXIT_REFUSED and "armed_by=arm" in res["error"], res
    assert box.cat("ticket.sh") == after and "clone_ticket" not in record(txn)
    box.put("guard_status", ARMED)
    rc, res = tk(capsys, "clear", "demo-c", "--txn", txn)
    assert rc == 0 and res["removed"] is True and res["handoff"] == "complete", res
    assert box.cat("ticket.sh") == "" and record(txn)["clone_ticket"] == "cleared"
    assert box.cat("receipt").split()[:2] == [txn, "gone"]
    assert tk(capsys, "read", "demo-c")[1]["loop"] is False
    rc, res = tk(capsys, "clear", "demo-c", "--txn", txn)        # again: nothing is left to do
    assert rc == 0 and res["removed"] is False and res["handoff"] == "complete", res


def test_extend_never_brings_the_deadline_nearer(box, capsys, cl):
    """A ticket written with a long deadline (a large data disk) is not cut short by the 30 minutes the take-over asks
    for: the later of the two stays, the ticket is not rewritten, and the answer says that it was kept."""
    rem, txn = cl
    txn = on_the_clone(capsys, cl)
    rc, res = tk(capsys, "extend", "demo-c", "--txn", txn, "--deadline", "3h")      # the instance's own clock counts
    assert rc == 0 and res["kept"] is False and abs(res["deadline"] - (now() + 10800)) < 30, res
    late = res["deadline"]
    before = box.cat("ticket.sh")
    rc, res = tk(capsys, "extend", "demo-c", "--txn", txn, "--deadline", "5m")
    assert rc == 0 and res["deadline"] == late and res["kept"] is True, res
    assert box.cat("ticket.sh") == before and record(txn)["ticket_deadline"] == late
    rc, res = tk(capsys, "extend", "demo-c", "--txn", txn, "--deadline", "4h")
    assert rc == 0 and res["kept"] is False and abs(res["deadline"] - (now() + 14400)) < 30, res
    assert record(txn)["ticket_deadline"] == res["deadline"]


def test_clear_does_not_count_a_loop_that_stays(box, capsys, cl):
    rem, txn = cl
    txn = on_the_clone(capsys, cl)
    holder = subprocess.Popen([*PREFIX, "bash", "-c", f"flock {box.d}/alive sleep 25"])   # something holds the loop's lock
    try:
        time.sleep(1.5)
        rc, res = tk(capsys, "clear", "demo-c", "--txn", txn)
        assert rc == ctl.EXIT_UNCERTAIN and res["handoff"] == "pending", res
        assert "clone_ticket" not in record(txn)   # the ticket is gone, the hand-over is not counted
    finally:
        holder.terminate()
        holder.wait(30)


def test_clear_source_works_on_the_source_only(box, capsys, cl):
    rem, txn = cl
    txn = written(capsys, cl)
    rc, res = tk(capsys, "clear", "demo", "--txn", txn, "--source")
    assert rc == 0 and res["removed"] is True and box.cat("ticket.sh") == "", res
    assert record(txn)["source_ticket"] == "cleared"
    rc, res = tk(capsys, "clear", "demo", "--txn", txn, "--source")   # again, with the ticket gone
    assert rc == 0 and res["removed"] is False, res
    assert rc_json(capsys, "clone-record", "close", "--txn", txn)[0] == 0
    assert tk(capsys, "clear", "demo", "--txn", txn, "--source")[0] == 0   # and after the clone was closed


@pytest.mark.parametrize("case", ["a-host-of-the-ticket", "not-the-source", "another-mark"])
def test_clear_source_refuses_where_the_ticket_is_live_or_not_its_own(box, case):
    hosts = ("abcd123456",) if case == "a-host-of-the-ticket" else ("ffff000000",)
    text = box.ticket(now() + 999, hosts=hosts, mark="ffffeeeeddddcccc" if case == "another-mark" else MARK)
    box.host(CLONE if case == "not-the-source" else SOURCE)
    box.put("clear.sh", ctl.ticket_clear_script(MARK, True, box.cfg))
    got = box.drive('bash "$B/clear.sh"; echo "rc=$?"')
    assert got["rc"] == "3" and box.cat("ticket.sh") == text, got
