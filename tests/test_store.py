"""Tests for the local record (Store in scripts/autodl_ctl.py): where it may live, what it accepts, how it is locked,
created and written. Every test gets its own AUTODL_GPU_HOME (tests/conftest.py). Child processes start with
spawn, so their entry points are module-level functions. The POSIX checks run in WSL on Windows
(tests/store_posix_check.py, with the record in WSL's own /tmp), and directly on a POSIX machine."""
import copy
import datetime as dt
import json
import multiprocessing as mp
import os
import pathlib
import re
import subprocess
import sys
import time

import pytest

SCRIPTS = str(pathlib.Path(__file__).resolve().parents[1] / "scripts")
sys.path.insert(0, SCRIPTS)
import autodl_ctl as ctl  # noqa: E402

WINDOWS = os.name == "nt"
CTX = mp.get_context("spawn")
ID = "abcd123456-1234abcd"
REQ = "0123456789abcdef"
T0 = 1_790_000_000


def gpu_home() -> pathlib.Path:
    h = os.environ.get("AUTODL_GPU_HOME")   # read into a local: a failing lookup must not print the environment
    assert h, "tests/conftest.py sets AUTODL_GPU_HOME for every test"
    return pathlib.Path(h)


def full_store() -> dict:
    """A record with every part filled in the formats the helper writes."""
    d = ctl.empty_store()
    d["last_seen"] = T0
    d["aliases"]["autodl-test"] = {"instance": ID, "at": T0}
    d["grants"][ID] = {"alias": "autodl-test", "usage": "both", "budget": {"kind": "fen", "value": 5000},
                       "period": "month", "period_tz": "+08:00", "quote": "this month, within 50 yuan", "at": T0,
                       "approvals": {"2026-09": {"quote": "yes, go on", "at": T0}}}
    d["ledger"][ID] = [
        {"kind": "reserve", "key": f"reserve:{REQ}", "req": REQ, "at": T0, "expires": T0 + 4200, "mode": "gpu",
         "gpus": 1, "price_fen_h": 218, "window_s": 3600},
        {"kind": "on", "key": f"on:{REQ}", "sid": REQ, "at": T0 + 60, "mode": "gpu", "gpus": 1, "price_fen_h": 218,
         "req": REQ},
        {"kind": "off", "key": f"off:{REQ}", "sid": REQ, "at": T0 + 1800},
        {"kind": "charge", "key": "charge:SN0001", "serial": "SN0001", "at": T0 + 1800, "fen": 110},
        {"kind": "release", "key": "release:fedcba9876543210", "req": "fedcba9876543210", "at": T0 + 1900},
        {"kind": "on", "key": f"on:t{T0 + 2000}-1", "sid": f"t{T0 + 2000}-1", "at": T0 + 2000, "mode": "nogpu",
         "gpus": 0, "price_fen_h": 10, "unreserved": True},
    ]
    d["calib"][ID] = [{"id": "c0123456789", "mode": "nogpu", "fingerprint": "0123456789ab", "guard": "ba9876543210",
                       "at": T0, "intervals": 5, "thresholds": {"cpu": 3.0, "io": 500000, "net": 10000},
                       "unreliable": [], "max": {"cpu": 1.5, "io": 1000.0, "net": 500.0},
                       "median": {"cpu": 1.0, "io": 900.0, "net": 400.0}}]
    return d


def write_raw(data: bytes) -> pathlib.Path:
    """A private record that opens, then store.json replaced by these bytes."""
    with ctl.Store():
        pass
    p = gpu_home() / "store.json"
    p.write_bytes(data)
    return p


# ---- child processes (module level: spawn imports this module) ----
def _child_env(home: str) -> None:
    os.environ["AUTODL_GPU_HOME"] = home


def _child_stuck_after_tmp(home, reached):
    _child_env(home)

    def hook(point, path=None):
        if point == "tmp-written" and b"half-written" in pathlib.Path(path).read_bytes():
            reached.set()
            time.sleep(600)   # the test kills this process here
    ctl._hook = hook
    with ctl.Store() as st:
        st.data["aliases"]["half-written"] = {"instance": ID, "at": T0}
        st.save()


def _child_add_aliases(home, prefix, n):
    _child_env(home)
    for i in range(n):
        with ctl.Store() as st:
            st.data["aliases"][f"{prefix}{i}"] = {"instance": ID, "at": T0}
            st.save()


def _child_read_then_wait(home, reached, go):
    _child_env(home)
    with ctl.Store() as st:
        reached.set()
        go.wait(60)
        st.data["aliases"]["from-a"] = {"instance": ID, "at": T0}
        st.save()


def _child_read(home, started, done, q):
    _child_env(home)
    started.set()
    with ctl.Store() as st:
        q.put(sorted(st.data["aliases"]))
    done.set()


def _child_hold_lock(home, reached):
    _child_env(home)
    with ctl.Store():
        reached.set()
        time.sleep(600)   # killed or terminated by the test


def _child_first_open(home, made, go1, ready, go2, q):
    _child_env(home)

    def hook(point):
        if point == "tmpdir-made":
            made.set()
            go1.wait(60)
        elif point == "before-rename":
            ready.set()
            go2.wait(60)
    ctl._hook = lambda point, path=None: hook(point)
    try:
        with ctl.Store() as st:
            q.put(("ok", str(st.home)))
    except ctl.StoreError as e:
        q.put(("error", f"{e.kind}: {e}"))


def _start(target, *args):
    p = CTX.Process(target=target, args=args, daemon=True)
    p.start()
    return p


# ---- what the record accepts ----
def test_a_full_store_is_read(clock):
    clock.set(T0 + 3000)
    p = write_raw(json.dumps(full_store()).encode())
    with ctl.Store() as st:
        assert st.data["ledger"][ID][3]["fen"] == 110 and st.data["aliases"]["autodl-test"]["instance"] == ID
    assert json.loads(p.read_bytes())["grants"][ID]["budget"] == {"kind": "fen", "value": 5000}


def _variants():
    def bad(change):
        d = full_store()
        change(d)
        return json.dumps(d).encode()

    def two_open(d):
        d["ledger"][ID].append({"kind": "on", "key": "on:m" + REQ, "sid": "m" + REQ, "at": T0 + 2100, "mode": "gpu",
                                "gpus": 1, "price_fen_h": 218, "attempted_req": REQ})
    ok = json.dumps(full_store())
    return {
        "garbled": b"\x00\x81 not json",
        "duplicate-key": ('{"schema": 1, ' + ok[1:]).encode(),
        # in a calibration's statistics, where any number is taken: only the parser can refuse these
        "nan": ok.replace('"cpu": 1.5', '"cpu": NaN').encode(),
        "huge-float": ok.replace('"cpu": 1.5', '"cpu": 1e400').encode(),
        "calib-unknown-signal": bad(lambda d: d["calib"][ID][0].update(unreliable=["disk"])),
        "calib-threshold-missing": bad(lambda d: d["calib"][ID][0]["thresholds"].pop("net")),
        "schema-2": bad(lambda d: d.update(schema=2)),
        "grants-not-object": bad(lambda d: d.update(grants=[])),
        "unknown-kind": bad(lambda d: d["ledger"][ID].append({"kind": "refund", "key": "refund:x", "at": T0})),
        "missing-field": bad(lambda d: d["ledger"][ID][1].pop("price_fen_h")),
        "same-key-twice": bad(lambda d: d["ledger"][ID].append(copy.deepcopy(d["ledger"][ID][3]))),
        "two-open-sessions": bad(two_open),
        "off-without-on": bad(lambda d: d["ledger"][ID].append({"kind": "off", "key": "off:m" + REQ, "sid": "m" + REQ,
                                                                 "at": T0 + 2200})),
        "bool-for-int": bad(lambda d: d["ledger"][ID][3].update(fen=True)),
        "negative-fen": bad(lambda d: d["ledger"][ID][3].update(fen=-1)),
        "bad-instance-id": bad(lambda d: d["aliases"]["autodl-test"].update(instance="not-an-id")),
        "extra-part": bad(lambda d: d.update(notes={})),
        "old-approval-field": bad(lambda d: d["grants"][ID].update(approval=None)),
        "approval-bad-period": bad(lambda d: d["grants"][ID]["approvals"].update({"2026-13": {"quote": "y", "at": T0}})),
        "approval-no-quote": bad(lambda d: d["grants"][ID]["approvals"]["2026-09"].update(quote=" ")),
        "approval-wrong-scheme": bad(lambda d: d["grants"][ID]["approvals"].update({"all": {"quote": "y", "at": T0}})),
        "calib-cpu-over-100": bad(lambda d: d["calib"][ID][0]["thresholds"].update(cpu=100.5)),
        "window-over-30-days": bad(lambda d: d["ledger"][ID][0].update(window_s=30 * 86400 + 1,
                                                                        expires=T0 + 30 * 86400 + 601)),
        "alias-with-newline": bad(lambda d: d["aliases"].update({"autodl-test\n": {"instance": ID, "at": T0}})),
        "serial-with-newline": bad(lambda d: d["ledger"][ID][3].update(serial="SN0001\n", key="charge:SN0001\n")),
    }


@pytest.mark.parametrize("name", sorted(_variants()))
def test_an_unreadable_store_is_refused_and_left_alone(name):
    raw = _variants()[name]
    p = write_raw(raw)
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "invalid", (name, e.value.kind, str(e.value))
    assert str(p) in str(e.value)
    assert p.read_bytes() == raw


def test_last_seen_only_goes_up(clock):
    assert ctl.empty_store()["last_seen"] == 0
    clock.set(T0)
    with ctl.Store() as st:   # opened without saving anything: last_seen is written all the same
        assert st.prev_last_seen == 0
    assert json.loads((gpu_home() / "store.json").read_bytes())["last_seen"] == T0
    clock.set(T0 - 3600)   # the clock went back
    with ctl.Store() as st:
        assert st.prev_last_seen == T0 and st.data["last_seen"] == T0
    assert json.loads((gpu_home() / "store.json").read_bytes())["last_seen"] == T0


def _git_dir(p: pathlib.Path) -> None:
    (p / ".git").mkdir(parents=True)


def _git_file(p: pathlib.Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    (p / ".git").write_text("gitdir: ../main/.git/worktrees/wt\n")


@pytest.mark.parametrize("case", ["cwd", "other", "home", "worktree"])
def test_a_home_inside_the_repository_is_refused(case, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    if case == "cwd":   # a relative AUTODL_GPU_HOME inside the repository the current directory is in
        _git_dir(repo)
        (repo / "sub").mkdir()
        monkeypatch.chdir(repo / "sub")
        monkeypatch.setenv("AUTODL_GPU_HOME", ".autodl-gpu")
        target = repo / "sub" / ".autodl-gpu"
    elif case == "other":
        _git_dir(repo)
        target = repo / "deep" / "gpu-home"
        monkeypatch.setenv("AUTODL_GPU_HOME", str(target))
    elif case == "home":   # the default location, in a home directory that is itself a repository
        _git_dir(repo)
        monkeypatch.delenv("AUTODL_GPU_HOME")
        monkeypatch.setenv("USERPROFILE", str(repo))
        monkeypatch.setenv("HOME", str(repo))
        target = repo / ".autodl-gpu"
    else:
        _git_file(repo)
        target = repo / "gpu-home"
        monkeypatch.setenv("AUTODL_GPU_HOME", str(target))
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    # a relative path is refused before anything else: it would move with the working directory
    assert e.value.kind == ("unsafe" if case == "cwd" else "repo") and "AUTODL_GPU_HOME" in str(e.value)
    assert not target.exists()   # nothing was made: no record and no half-made directory next to it
    assert not target.parent.exists() or not any(".new-" in q.name for q in target.parent.iterdir())


USER = "S-1-5-21-1111111111-2222222222-3333333333-1001"
GOOD = f"O:{USER}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;{USER})"


@pytest.mark.parametrize("sddl, ok", [
    (GOOD, True),
    (f"O:BAD:PAI(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FA;;;{USER})", True),   # a file inheriting them; owner Administrators
    (GOOD + "(D;;FA;;;WD)", True),   # a deny entry only takes rights away
    (GOOD + "(A;;FR;;;WD)", False),   # Everyone may read
    (GOOD + "(A;OICIIO;FA;;;WD)", False),   # inherit only: it would go to new files
    (f"O:{USER}D:NO_ACCESS_CONTROL", False),   # no DACL at all: everyone has full access
    (f"O:S-1-5-21-1-2-3-500D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{USER})", False),   # someone else owns it
    (f"O:{USER}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;OW)", False),   # owner rights is not the user
    (f"O:{USER}", False),   # no DACL part
    (f"O:{USER}D:P(ML;;NW;;;LW)(A;OICI;FA;;;SY)", False),   # an entry of a kind the check does not know
])
def test_the_dacl_check_reads_sddl_right(sddl, ok):
    problems = ctl.sddl_problems(sddl, USER)
    assert (problems == []) == ok, problems


# ---- crashes and more than one process ----
def _tmp_files():
    return [q for q in gpu_home().iterdir() if q.name.startswith(ctl.STORE_TMP_PREFIX)]


def test_a_killed_writer_leaves_the_old_store():
    with ctl.Store() as st:
        st.data["aliases"]["before"] = {"instance": ID, "at": T0}
        st.save()
    p = gpu_home() / "store.json"
    reached = CTX.Event()
    child = _start(_child_stuck_after_tmp, str(gpu_home()), reached)
    assert reached.wait(60)   # its temporary file is written; the child may have saved last_seen before that
    old = p.read_bytes()
    child.kill()
    child.join(30)
    assert p.read_bytes() == old and b"half-written" not in old
    tmps = _tmp_files()   # the half-written file is left until the next open
    assert len(tmps) == 1 and b"half-written" in tmps[0].read_bytes()
    with ctl.Store() as st:
        assert "before" in st.data["aliases"] and "half-written" not in st.data["aliases"]
    assert _tmp_files() == []


def test_two_processes_lose_no_writes():
    home = str(gpu_home())
    reached, go, started, done = CTX.Event(), CTX.Event(), CTX.Event(), CTX.Event()
    q = CTX.Queue()
    a = _start(_child_read_then_wait, home, reached, go)
    assert reached.wait(60)   # A holds the lock and has read
    b = _start(_child_read, home, started, done, q)
    assert started.wait(60)
    assert not done.wait(2)   # B is waiting for the lock
    go.set()   # A writes, then lets go
    assert done.wait(60)
    assert "from-a" in q.get(timeout=30)   # B read what A wrote
    for p in (a, b):
        p.join(30)
        assert p.exitcode == 0
    writers = [_start(_child_add_aliases, home, prefix, 30) for prefix in ("x-", "y-")]
    for p in writers:
        p.join(180)
        assert p.exitcode == 0
    with ctl.Store() as st:
        assert sum(name.startswith(("x-", "y-")) for name in st.data["aliases"]) == 60


def test_a_held_lock_times_out():
    reached = CTX.Event()
    child = _start(_child_hold_lock, str(gpu_home()), reached)
    try:
        assert reached.wait(60)
        with pytest.raises(ctl.StoreError) as e:
            with ctl.Store(wait=0.5):
                pass
        assert e.value.kind == "locked"
    finally:
        child.kill()
        child.join(30)


def test_a_killed_lock_holder_releases_the_lock():
    reached = CTX.Event()
    child = _start(_child_hold_lock, str(gpu_home()), reached)
    assert reached.wait(60)
    with pytest.raises(ctl.StoreError) as e:   # held while the holder lives
        with ctl.Store(wait=0.5):
            pass
    assert e.value.kind == "locked"
    child.kill()
    child.join(30)
    with ctl.Store(wait=10) as st:
        assert st.data["schema"] == 1


def test_the_first_creation_is_safe_when_concurrent():
    home = gpu_home()
    assert not home.exists()
    events = [[CTX.Event() for _ in range(4)] for _ in range(2)]   # made, go1, ready, go2 for each process
    q = CTX.Queue()
    procs = [_start(_child_first_open, str(home), *ev, q) for ev in events]
    try:
        assert all(ev[0].wait(60) for ev in events)   # both made a private temporary directory
        for ev in events:
            ev[1].set()
        assert all(ev[2].wait(60) for ev in events)   # both are about to rename it into place
        events[0][3].set()
        first = q.get(timeout=60)
        events[1][3].set()   # the second finds the place taken and uses the first one's directory
        second = q.get(timeout=60)
    finally:
        for p in procs:
            p.join(60)
    assert first[0] == "ok" and second[0] == "ok", (first, second)
    assert sorted(x.name for x in home.iterdir()) == ["store.json", "store.lock"]
    assert (home / "store.lock").stat().st_size == 1
    assert not any(".new-" in x.name for x in home.parent.iterdir())


# ---- Windows: the DACL, a replace another program blocks, a junction ----
windows_only = pytest.mark.skipif(not WINDOWS, reason="Windows only; the POSIX side is in store_posix_check.py")


def _whoami_sid() -> str:
    r = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True)
    sid = r.stdout.decode("utf-8", "replace").strip().split(",")[-1].strip('"')
    assert r.returncode == 0 and sid.startswith("S-1-5-"), r.returncode
    return sid


def _aces(sddl: str) -> list:
    return sorted(re.findall(r"\(([^()]*)\)", sddl))


@windows_only
def test_a_new_store_is_private_on_windows(tmp_path):
    with ctl.Store() as st:
        st.save()
    home = gpu_home()
    out = tmp_path / "acl.txt"   # read back with icacls itself, not with the checker under test
    assert subprocess.run(["icacls", str(home), "/save", str(out), "/T"], capture_output=True).returncode == 0
    lines = out.read_bytes().decode("utf-16-le").splitlines()
    saved = {lines[i]: lines[i + 1] for i in range(0, len(lines) - 1, 2)}
    sid = _whoami_sid()
    root = saved.pop(home.name)
    assert root.startswith("D:P") and _aces(root) == sorted(f"A;OICI;FA;;;{s}" for s in (sid, "BA", "SY")), root
    assert sorted(saved) == [f"{home.name}\\store.json", f"{home.name}\\store.lock"]
    for name, sddl in saved.items():
        assert _aces(sddl) == sorted(f"A;ID;FA;;;{s}" for s in (sid, "BA", "SY")), (name, sddl)


@windows_only
def test_a_blocked_replace_keeps_the_old_store():
    with ctl.Store() as st:
        st.data["aliases"]["before"] = {"instance": ID, "at": T0}
        st.save()
    p = gpu_home() / "store.json"
    old = p.read_bytes()
    with open(p, "rb"):   # Python opens without FILE_SHARE_DELETE: the file cannot be replaced meanwhile
        with pytest.raises(ctl.StoreError) as e:
            with ctl.Store() as st:
                st.data["aliases"]["after"] = {"instance": ID, "at": T0}
                st.save()
        assert e.value.kind == "io" and p.read_bytes() == old
    assert _tmp_files() == []
    with ctl.Store() as st:
        st.data["aliases"]["after"] = {"instance": ID, "at": T0}
        st.save()
    assert "after" in json.loads(p.read_bytes())["aliases"]


@windows_only
def test_a_junction_as_the_record_is_refused(tmp_path, monkeypatch):
    import _winapi
    real, link = tmp_path / "real-home", tmp_path / "linked-home"
    monkeypatch.setenv("AUTODL_GPU_HOME", str(real))
    with ctl.Store():
        pass
    _winapi.CreateJunction(str(real), str(link))
    try:
        monkeypatch.setenv("AUTODL_GPU_HOME", str(link))
        with pytest.raises(ctl.StoreError) as e:
            with ctl.Store():
                pass
        assert e.value.kind == "unsafe" and "link" in str(e.value), str(e.value)
    finally:
        os.rmdir(link)   # removes the junction only


@windows_only
@pytest.mark.parametrize("what", ["home", "store.json", "store.lock"])
def test_loose_permissions_are_refused_on_windows(what):
    with ctl.Store() as st:
        st.save()
    p = gpu_home() if what == "home" else gpu_home() / what
    assert subprocess.run(["icacls", str(p), "/grant", "*S-1-1-0:(R)"], capture_output=True).returncode == 0
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "unsafe" and "icacls" in str(e.value), str(e.value)


# ---- POSIX: modes, owner, symbolic links, fcntl locks (in WSL on Windows) ----
POSIX_CHECK = pathlib.Path(__file__).with_name("store_posix_check.py")
POSIX_SCENARIOS = ["new-store-private", "loose-dir-mode", "loose-json-mode", "loose-lock-mode", "hand-made-dir",
                   "linked-dir", "linked-json", "linked-lock", "unreadable-parent", "no-lost-writes",
                   "held-lock-times-out", "killed-holder-releases"]


@pytest.fixture(scope="module")
def posix_results():
    if not WINDOWS:
        sys.path.insert(0, str(POSIX_CHECK.parent))
        import store_posix_check
        return store_posix_check.run_all()
    sys.path.insert(0, str(POSIX_CHECK.parent))
    import local_tools
    wsl, need = local_tools.wsl_command(), f"needs {local_tools.wsl_name()} with python3 for the POSIX checks"
    if not local_tools.have_wsl("python3"):
        pytest.skip(need)
    try:
        r = subprocess.run([*wsl, "wslpath", "-a", POSIX_CHECK.as_posix()], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip(need)
    if r.returncode != 0:
        pytest.skip(need)
    r = subprocess.run([*wsl, "python3", "-B", r.stdout.decode().strip()], capture_output=True, timeout=900)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    return json.loads(r.stdout.decode("utf-8"))


@pytest.mark.parametrize("name", POSIX_SCENARIOS)
def test_posix_privacy_links_and_locks(posix_results, name):
    ok, detail = posix_results[name]
    assert ok, detail


# ---- grants and the ledger (Task 5.4) ----
BJ = dt.timezone(dt.timedelta(hours=8))


def bj(*when) -> int:
    """Unix time of a Beijing wall-clock time."""
    return int(dt.datetime(*when, tzinfo=BJ).timestamp())


MID = bj(2026, 9, 10, 12)   # the middle of a month, on a full hour


def rc_json(capsys, *argv):
    """(exit code, parsed JSON output or None); a usage error (SystemExit) counts as its exit code."""
    try:
        rc = ctl.main([str(x) for x in argv])
    except SystemExit as e:
        rc = e.code
    out = capsys.readouterr().out.strip()
    return rc, (json.loads(out) if out.startswith("{") else None)


def grant(capsys, budget="50yuan", usage="both", period="month", tz=None, iid=ID, read=True):
    """A grant and, unless read is False, the import a monthly money budget needs before its first check (here: the
    billing detail showed no charge of this period)."""
    got = rc_json(capsys, "auth", "grant", "--instance", iid, "--alias", "autodl-test", "--usage", usage,
                  "--budget", budget, "--period", period, "--quote", "the user's words",
                  *(["--period-tz", tz] if tz else []))
    if read and got[0] == 0:
        assert rc_json(capsys, "auth", "charges", "--instance", iid, "--json", "[]")[0] == 0
    return got


def check(capsys, mode="gpu", price="0.98", gpus=1, hours="1", iid=ID):
    return rc_json(capsys, "auth", "check", "--instance", iid, "--mode", mode, "--price", price, "--gpus", gpus,
                   "--hours", hours)


def log_ev(capsys, event, project, at, mode="gpu", price="0.98", gpus=1, req=None, iid=ID):
    fields = [] if event != "on" else [f"mode={mode}", f"price={price}"] + ([f"gpus={gpus}"] if gpus is not None else [])
    return rc_json(capsys, "log", event, "--instance", iid, "--project", project, "--at", at,
                   *[w for f in fields for w in ("--field", f)], *(["--req", req] if req else []))


def show(capsys, iid=ID) -> dict:
    rc, res = rc_json(capsys, "auth", "show", "--instance", iid)
    assert rc == 0, res
    return res["grants"][iid]


def book(iid=ID):
    with ctl.Store() as st:
        return copy.deepcopy(st.data["ledger"].get(iid))


def put_records(records, iid=ID):
    """Records written straight into the ledger, for the arithmetic tests."""
    with ctl.Store() as st:
        st.data["ledger"].setdefault(iid, []).extend(records)
        st.save()


def on_rec(sid, at, price=98, mode="gpu", gpus=1, **kw):
    return {"kind": "on", "key": f"on:{sid}", "sid": sid, "at": at, "mode": mode, "gpus": gpus, "price_fen_h": price,
            **kw}


def off_rec(sid, at):
    return {"kind": "off", "key": f"off:{sid}", "sid": sid, "at": at}


def charge_rec(serial, at, fen):
    return {"kind": "charge", "key": f"charge:{serial}", "serial": serial, "at": at, "fen": fen}


def reserve_rec(req, at, window_s=3600, price=98, mode="gpu", gpus=1):
    return {"kind": "reserve", "key": f"reserve:{req}", "req": req, "at": at, "expires": at + window_s + 600,
            "mode": mode, "gpus": gpus, "price_fen_h": price, "window_s": window_s}


def test_grant_show_revoke(capsys, clock):
    clock.set(MID)
    assert grant(capsys, budget="50yuan")[0] == 0
    g = show(capsys)["grant"]
    assert g["budget"] == {"kind": "fen", "value": 5000} and g["usage"] == "both" and g["period"] == "month"
    assert g["period_tz"] == "+08:00" and g["quote"] == "the user's words" and g["approvals"] == {}
    assert rc_json(capsys, "auth", "approve", "--instance", ID, "--quote", "yes, go on")[0] == 0
    assert show(capsys)["grant"]["approvals"] == {"2026-09": {"quote": "yes, go on", "at": MID}}
    assert grant(capsys, budget="12.5gpuh", usage="gpu", tz="+00:00")[0] == 0   # replaces it
    g = show(capsys)["grant"]
    assert g["budget"] == {"kind": "gpu_mh", "value": 12500} and g["usage"] == "gpu" and g["period_tz"] == "+00:00"
    assert g["approvals"] == {} and book() == []   # the consent is gone; the ledger was made and left alone
    assert grant(capsys, budget="none", period="none")[0] == 0 and show(capsys)["grant"]["budget"] == {"kind": "none"}
    assert rc_json(capsys, "auth", "revoke", "--instance", ID)[0] == 0
    assert show(capsys)["grant"] is None and book() == []


@pytest.mark.parametrize("change", [
    {"--instance": "ABC"}, {"--usage": "all"}, {"--budget": "50"}, {"--budget": "50.123yuan"}, {"--budget": "-5yuan"},
    {"--budget": "nanyuan"}, {"--budget": "infyuan"}, {"--budget": "1e3yuan"}, {"--budget": "1.2345gpuh"},
    {"--budget": "5 yuan"}, {"--period": "week"}, {"--period-tz": "+08:30"}, {"--period-tz": "+15:00"},
    {"--period-tz": "8"}, {"--quote": "  "},
])
def test_grant_rejects_bad_values(capsys, change):
    args = {"--instance": ID, "--alias": "autodl-test", "--usage": "both", "--budget": "50yuan", "--period": "month",
            "--quote": "the user's words", **change}
    rc, _ = rc_json(capsys, "auth", "grant", *[w for k, v in args.items() for w in (k, v)])
    assert rc == ctl.EXIT_ERR
    with ctl.Store() as st:
        assert st.data["grants"] == {} and st.data["ledger"] == {}
    assert grant(capsys)[0] == 0   # the same command with good values is accepted: only the bad value was refused


def _project_lines(project) -> list:
    return (pathlib.Path(project) / ".autodl" / "power_log.jsonl").read_text(encoding="utf-8").splitlines()


def test_log_writes_the_ledger_first(capsys, clock, tmp_path):
    clock.set(MID)
    rc, res = log_ev(capsys, "on", tmp_path / "p1", MID)
    assert rc == 0 and res["ledger"] == "recorded" and res["session"] == f"t{MID}-1"
    assert json.loads(_project_lines(tmp_path / "p1")[-1])["event"] == "on"
    clock.set(MID + 600)
    assert log_ev(capsys, "off", tmp_path / "p1", MID + 600)[0] == 0
    (tmp_path / "p2" / ".autodl" / "power_log.jsonl").mkdir(parents=True)   # this project log cannot be written
    clock.set(MID + 1200)
    rc, res = log_ev(capsys, "on", tmp_path / "p2", MID + 1200)
    assert rc == ctl.EXIT_ERR and [r["kind"] for r in book()] == ["on", "off", "on"] and book()[-1]["at"] == MID + 1200


def test_ledger_ids_make_resends_harmless(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    req = check(capsys)[1]["req"]
    for _ in range(2):
        rc, res = log_ev(capsys, "on", tmp_path, MID, req=req)
        assert rc == 0 and res["session"] == req
    assert log_ev(capsys, "on", tmp_path, MID + 5, req=req)[0] == ctl.EXIT_ERR   # the same request at another time
    clock.set(MID + 900)
    for _ in range(2):
        assert log_ev(capsys, "off", tmp_path, MID + 900)[0] == 0
    assert [r["key"] for r in book()] == [f"reserve:{req}", f"on:{req}", f"off:{req}"]
    assert len(_project_lines(tmp_path)) == 2   # a resend adds nothing to the project log either


@pytest.mark.parametrize("fields", [["price=0.98", "gpus=1"], ["mode=gpu", "gpus=1"], ["mode=gpu", "price=0.98"],
                                    ["mode=nogpu", "price=0.10", "gpus=1"], ["mode=gpu", "price=abc", "gpus=1"],
                                    ["mode=gpu", "price=0.981", "gpus=1"], ["mode=gpu", "price=0.98", "gpus=0"]])
def test_log_on_needs_mode_price_and_gpus(capsys, clock, tmp_path, fields):
    clock.set(MID)
    rc, _ = rc_json(capsys, "log", "on", "--instance", ID, "--project", tmp_path, "--at", MID,
                    *[w for f in fields for w in ("--field", f)])
    assert rc == ctl.EXIT_ERR and not (tmp_path / ".autodl").exists() and book() is None


def test_a_matching_reservation_is_used_and_a_mismatch_is_kept(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    r1 = check(capsys)[1]["req"]
    rc, res = log_ev(capsys, "on", tmp_path, MID, req=r1)
    assert rc == 0 and res["session"] == r1 and show(capsys)["open_reservations"] == []
    assert rc_json(capsys, "auth", "release", "--instance", ID, "--req", r1)[0] == ctl.EXIT_ERR   # used: not released
    clock.set(MID + 1800)
    assert log_ev(capsys, "off", tmp_path, MID + 1800)[0] == 0   # 1800 s at 0.98: 49 fen
    clock.set(MID + 3600)
    r2 = check(capsys)[1]["req"]
    rc, res = log_ev(capsys, "on", tmp_path, MID + 3600, price="1.20", req=r2)
    assert rc == ctl.EXIT_ERR and res["session"] == "m" + r2 and "release" in res["note"]
    on = book()[-1]
    assert on["sid"] == "m" + r2 and on["attempted_req"] == r2 and "req" not in on
    clock.set(MID + 5400)
    s = show(capsys)   # the first session, 1800 s at 1.20 (60 fen), and the reservation still held (98 fen)
    assert s["open_reservations"] == [r2] and s["spent_fen"] == 49 + 60 + 98
    assert rc_json(capsys, "auth", "release", "--instance", ID, "--req", r2)[0] == 0
    assert show(capsys)["spent_fen"] == 49 + 60


def test_log_on_without_a_req_is_unreserved(capsys, clock, tmp_path):
    t = MID + 123
    clock.set(t)
    rc, res = log_ev(capsys, "on", tmp_path, t)
    assert rc == 0 and res["session"] == f"t{t}-1"
    assert log_ev(capsys, "off", tmp_path, t)[0] == 0   # a balancing pair written in the same second
    rc, res = log_ev(capsys, "on", tmp_path, t)
    assert rc == 0 and res["session"] == f"t{t}-2"
    rc, res = log_ev(capsys, "on", tmp_path, t)   # the same power-on again
    assert rc == 0 and res["ledger"] == "resend"
    ons = [r for r in book() if r["kind"] == "on"]
    assert [r["sid"] for r in ons] == [f"t{t}-1", f"t{t}-2"] and all(r["unreserved"] is True for r in ons)


def test_log_on_while_a_session_is_open_is_refused(capsys, clock, tmp_path):
    clock.set(MID)
    assert log_ev(capsys, "on", tmp_path, MID)[0] == 0
    before = book()
    rc, res = log_ev(capsys, "on", tmp_path, MID + 100)
    assert rc == ctl.EXIT_ERR and "off" in res["error"] and book() == before and len(_project_lines(tmp_path)) == 1


def test_log_off_closes_the_open_session(capsys, clock, tmp_path):
    clock.set(MID)
    assert log_ev(capsys, "on", tmp_path, MID)[0] == 0
    rc, res = log_ev(capsys, "off", tmp_path, MID - 60)   # before the power-on: refused, nothing written
    assert rc == ctl.EXIT_ERR and len(book()) == 1 and len(_project_lines(tmp_path)) == 1
    rc, res = log_ev(capsys, "off", tmp_path, MID + 60)
    assert rc == 0 and res["session"] == f"t{MID}-1" and book()[-1] == off_rec(f"t{MID}-1", MID + 60)
    rc, res = log_ev(capsys, "off", tmp_path, MID + 120)   # nothing open: only the project log
    assert rc == 0 and res["ledger"] == "no open session" and len(book()) == 2 and len(_project_lines(tmp_path)) == 3


def test_revoke_keeps_the_ledger_going(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    req = check(capsys)[1]["req"]
    assert log_ev(capsys, "on", tmp_path, MID, req=req)[0] == 0
    assert rc_json(capsys, "auth", "revoke", "--instance", ID)[0] == 0
    clock.set(MID + 1800)
    assert log_ev(capsys, "off", tmp_path, MID + 1800)[0] == 0
    rows = json.dumps([{"serial": "SN1", "instance": ID, "time": "2026-09-10 12:29:59", "amount": "0.49"}])
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", rows)[0] == 0
    rc, res = rc_json(capsys, "usage", "--instance", ID)
    assert rc == 0 and res["gpu_hours"] == 0.5 and res["charged_yuan"] == 0.49
    assert check(capsys)[0] == ctl.EXIT_NO_GRANT


def test_log_without_a_known_instance_only_writes_the_project_log(capsys, clock, tmp_path):
    clock.set(MID)
    rc, res = log_ev(capsys, "on", tmp_path, MID, iid="some-alias")
    assert rc == 0 and res["ledger"] == "unknown instance" and "check" in res["note"] and _project_lines(tmp_path)
    with ctl.Store() as st:
        assert st.data["ledger"] == {}


def test_a_literal_id_records_without_an_alias_or_a_grant(capsys, clock, tmp_path):
    clock.set(MID)
    assert log_ev(capsys, "on", tmp_path, MID)[0] == 0
    with ctl.Store() as st:
        assert st.data["aliases"] == {} and st.data["grants"] == {} and len(st.data["ledger"][ID]) == 1
    assert check(capsys)[0] == ctl.EXIT_NO_GRANT


def test_grant_does_not_verify_an_alias(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0   # --alias autodl-test is only shown
    rc, res = log_ev(capsys, "on", tmp_path, MID, iid="autodl-test")
    assert rc == 0 and res["ledger"] == "unknown instance" and book() == []
    with ctl.Store() as st:   # what a successful check autodl-test --instance ID writes
        st.data["aliases"]["autodl-test"] = {"instance": ID, "at": MID}
        st.save()
    rc, res = log_ev(capsys, "on", tmp_path, MID + 60, iid="autodl-test")
    assert rc == 0 and res["ledger"] == "recorded" and book()[-1]["at"] == MID + 60


def test_a_revoked_instance_is_still_known(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0 and rc_json(capsys, "auth", "revoke", "--instance", ID)[0] == 0
    rows = json.dumps([{"serial": "SN9", "instance": ID, "time": "2026-09-10 11:00:00", "amount": 0.01}])
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", rows)[0] == 0
    rc, res = rc_json(capsys, "usage", "--instance", ID)
    assert rc == 0 and res["charged_yuan"] == 0.01
    other = "ffff000000-0000ffff"   # never granted nor logged: not known
    assert rc_json(capsys, "auth", "charges", "--instance", other, "--json", rows.replace(ID, other))[0] == ctl.EXIT_ERR
    assert rc_json(capsys, "usage", "--instance", other)[0] == ctl.EXIT_ERR


def test_log_with_a_broken_store_still_writes_the_project_log(capsys, clock, tmp_path):
    clock.set(MID)
    write_raw(b"not json")
    rc, res = log_ev(capsys, "on", tmp_path, MID)
    assert rc == ctl.EXIT_STORE and "store.json" in res["ledger"] and len(_project_lines(tmp_path)) == 1


# ---- the check before a power-on ----
def test_check_needs_everything(capsys, clock):
    clock.set(MID)
    for argv in (["--mode", "gpu", "--gpus", 1, "--hours", 1], ["--mode", "gpu", "--price", "0.98", "--gpus", 1],
                 ["--mode", "gpu", "--price", "0.98", "--hours", 1],
                 ["--mode", "gpu", "--price", "0.98", "--gpus", 0, "--hours", 1],
                 ["--mode", "nogpu", "--price", "0.10", "--gpus", 1, "--hours", 1],
                 ["--mode", "gpu", "--price", "0.98", "--gpus", 1, "--hours", 0],
                 ["--mode", "gpu", "--price", "-1", "--gpus", 1, "--hours", 1]):
        assert rc_json(capsys, "auth", "check", "--instance", ID, *argv)[0] == ctl.EXIT_ERR, argv
    assert check(capsys)[0] == ctl.EXIT_NO_GRANT
    assert grant(capsys, usage="nogpu")[0] == 0
    assert check(capsys)[0] == ctl.EXIT_NO_GRANT and book() == []   # nothing reserved


def test_check_reserves_and_refuses_when_short(capsys, clock):
    clock.set(MID)
    assert grant(capsys, budget="5yuan")[0] == 0
    rc, res = check(capsys)
    assert rc == 0 and res["ok"] is True and ctl.REQ_RE.match(res["req"])
    assert res["remaining_fen"] == 500 - 98 and res["hours_left"] == pytest.approx(402 / 98, abs=0.01)
    rc, res = check(capsys, hours="5")   # 490 more on top of the 98 reserved
    assert rc == ctl.EXIT_BUDGET and res["ok"] is False and res["spent_fen"] == 98 and res["remaining_fen"] == 402
    assert [r["kind"] for r in book()] == ["reserve"]


def test_check_with_a_broken_store_is_11(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    p = gpu_home() / "store.json"
    good = p.read_bytes()
    p.write_bytes(b"not json")
    assert check(capsys)[0] == ctl.EXIT_STORE
    d = json.loads(good)
    d["ledger"][ID] = [on_rec("m" + REQ, MID - 100), on_rec(f"t{MID}-1", MID - 50, unreserved=True)]
    p.write_bytes(json.dumps(d).encode())
    assert check(capsys)[0] == ctl.EXIT_STORE   # two open sessions on one instance
    p.write_bytes(good)
    if WINDOWS:
        assert subprocess.run(["icacls", str(p), "/grant", "*S-1-1-0:(R)"], capture_output=True).returncode == 0
    else:
        os.chmod(p, 0o644)
    assert check(capsys)[0] == ctl.EXIT_STORE
    assert json.loads(p.read_bytes())["ledger"][ID] == []   # nothing was reserved


def test_a_clock_moved_back_blocks_the_check(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0 and check(capsys)[0] == 0   # last_seen is MID now
    clock.set(MID - 600)
    assert check(capsys)[0] == ctl.EXIT_STORE   # ten minutes back
    clock.set(MID - 540)
    assert check(capsys)[0] == 0   # nine minutes back in the same month
    assert log_ev(capsys, "on", tmp_path, MID - 540)[0] == 0 and log_ev(capsys, "off", tmp_path, MID - 500)[0] == 0
    first = bj(2026, 10, 1, 0, 1)
    clock.set(first)
    show(capsys)   # last_seen is just past the start of October
    clock.set(first - 540)   # nine minutes back, in September
    assert check(capsys)[0] == ctl.EXIT_STORE
    assert len([r for r in book() if r["kind"] == "reserve"]) == 2


def test_a_check_covers_every_period_its_window_touches(capsys, clock):
    clock.set(bj(2026, 9, 30, 23, 30))
    assert grant(capsys, budget="5yuan")[0] == 0
    rc, res = check(capsys, hours="2")   # 196 fen, in full in September and in October
    assert rc == 0 and [p["period"] for p in res["periods"]] == ["2026-09", "2026-10"]
    assert rc_json(capsys, "auth", "release", "--instance", ID, "--req", res["req"])[0] == 0
    t = bj(2026, 10, 1, 0, 10)
    put_records([on_rec(f"t{t}-1", t, unreserved=True), off_rec(f"t{t}-1", t + 4 * 3600)])   # October has 393 fen
    rc, res = check(capsys, hours="2")
    assert rc == ctl.EXIT_BUDGET and res["period"] == "2026-10" and res["spent_fen"] == 393


def test_near_the_end_the_user_is_asked_first(capsys, clock):
    clock.set(MID)
    assert grant(capsys, budget="6yuan")[0] == 0
    t = MID - 4 * 3600
    put_records([on_rec(f"t{t}-1", t, unreserved=True), off_rec(f"t{t}-1", MID - 60)])   # 391 fen
    rc, res = check(capsys)   # 391 + 98 would leave 111 of 600, under 20 %
    assert rc == ctl.EXIT_BUDGET and "approve" in res["reason"] and [r["kind"] for r in book()] == ["on", "off"]
    assert rc_json(capsys, "auth", "approve", "--instance", ID, "--quote", "ok, go on")[0] == 0
    r1, r2 = check(capsys), check(capsys)
    assert r1[0] == 0 and r2[0] == 0   # two in a row, still within the budget
    for r in (r1, r2):
        assert rc_json(capsys, "auth", "release", "--instance", ID, "--req", r[1]["req"])[0] == 0
    assert grant(capsys, budget="6yuan")[0] == 0 and check(capsys)[0] == ctl.EXIT_BUDGET   # a new grant, a new consent
    assert rc_json(capsys, "auth", "approve", "--instance", ID, "--quote", "ok")[0] == 0   # for September
    nxt = bj(2026, 10, 10, 12)
    clock.set(nxt)
    t = nxt - 4 * 3600
    put_records([on_rec(f"t{t}-1", t, unreserved=True), off_rec(f"t{t}-1", nxt - 60)])
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", "[]")[0] == 0   # October's look at the charges
    assert check(capsys)[0] == ctl.EXIT_BUDGET   # September's consent does not reach October


def _child_check(home, now, hold, started, reached, go, q):
    _child_env(home)
    ctl.now_s = lambda: now   # a fixed time: a real clock near a month's end would bring the next month in
    if hold:
        def hook(point, path=None):
            if point == "check-decided":
                reached.set()
                go.wait(60)
        ctl._hook = hook
    started.set()
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        q.put(ctl.main(["auth", "check", "--instance", ID, "--mode", "gpu", "--price", "0.98", "--gpus", "1",
                        "--hours", "1"]))


def test_two_checks_at_once_only_one_passes(capsys, clock):
    clock.set(MID)
    assert grant(capsys, budget="1.5yuan")[0] == 0   # one hour at 0.98 leaves 52 fen (over 20 %); two do not fit
    home = str(gpu_home())
    ev = [CTX.Event() for _ in range(6)]
    q = CTX.Queue()
    _start(_child_check, home, MID, True, ev[0], ev[1], ev[2], q)
    assert ev[1].wait(60)   # A has decided inside the lock
    _start(_child_check, home, MID, False, ev[3], ev[4], ev[5], q)
    assert ev[3].wait(60)
    time.sleep(1)   # B is checking meanwhile, and must wait for A
    ev[2].set()
    assert sorted([q.get(timeout=60), q.get(timeout=60)]) == [0, ctl.EXIT_BUDGET]


# ---- what has been spent ----
FAR = 2 ** 40


def test_estimates_round_up_each_billing_segment():
    h = MID
    assert list(ctl.segments(h, h + 7200)) == [(h, h + 3600), (h + 3600, h + 7200)]   # a start on the hour
    assert list(ctl.segments(h - 5, h + 3600)) == [(h - 5, h), (h, h + 3600)]   # an end on the hour
    m = bj(2026, 10, 1)
    assert list(ctl.segments(m - 1800, m + 1800)) == [(m - 1800, m), (m, m + 1800)]   # across a month

    def spent(t_on, t_off, price, mode):
        recs = [on_rec("t1-1", t_on, price=price, mode=mode, gpus=1 if mode == "gpu" else 0), off_rec("t1-1", t_off)]
        return ctl.spent_fen(recs, 0, FAR, t_off)
    assert spent(h - 10, h + 10, 10, "nogpu") == 2   # two segments of 10 s, at least 1 fen each
    # sessions of the test instance, estimated and (in the comment) charged: 16 + 10, 5, 1 fen
    assert spent(h - 600, h + 378, 98, "gpu") == 17 + 11
    assert spent(h + 100, h + 293, 98, "gpu") == 6
    assert spent(h + 100, h + 269, 10, "nogpu") == 1


def test_spent_takes_the_larger_of_estimate_and_charges():
    h = MID
    sess = [on_rec("t1-1", h), off_rec("t1-1", h + 3000)]   # 3000 s at 0.98: 82 fen

    def f(recs):
        return ctl.spent_fen(recs, 0, FAR, h + 10 ** 5)
    assert f(sess) == 82
    assert f(sess + [charge_rec("S1", h + 2999, 50)]) == 82   # fewer charges than estimated: the estimate
    assert f(sess + [charge_rec("S1", h + 2999, 90)]) == 90   # more: the charges
    later = [on_rec("t2-1", h + 3600), off_rec("t2-1", h + 5400)]   # 49 fen after the last charge
    assert f(sess + later + [charge_rec("S1", h + 2999, 90)]) == 90 + 49


def test_charges_of_the_next_period_do_not_lower_this_one():
    s, e = bj(2026, 9, 1), bj(2026, 10, 1)
    recs = [on_rec("t1-1", bj(2026, 9, 20, 10)), off_rec("t1-1", bj(2026, 9, 20, 10, 50)),
            charge_rec("S1", bj(2026, 9, 20, 10, 50), 100),   # above the estimate of 82
            on_rec("t2-1", bj(2026, 9, 25, 10)), off_rec("t2-1", bj(2026, 9, 25, 10, 30))]   # 49, not charged yet
    assert ctl.spent_fen(recs, s, e, e + 10 ** 5) == 100 + 49
    assert ctl.spent_fen(recs + [charge_rec("S2", bj(2026, 10, 2), 5)], s, e, e + 10 ** 5) == 100 + 49


def test_a_power_off_on_the_hour_adds_a_fen():
    h = MID
    sess = [on_rec("t1-1", h - 600), off_rec("t1-1", h)]   # 17 fen, and 1 for a charge at the power-off

    def f(recs):
        return ctl.spent_fen(recs, 0, FAR, h + 10 ** 5)
    assert f(sess) == 18
    assert f(sess + [charge_rec("S1", h - 1, 20)]) == 20 + 1   # the hour's charge; the power-off one may still come
    assert f(sess + [charge_rec("S1", h - 1, 20), charge_rec("S2", h, 3)]) == 23   # it came: nothing is added


def test_a_reservation_counts_until_it_is_used_or_released():
    h = MID
    res = [reserve_rec(REQ, h)]   # 3600 s at 0.98: 98 fen
    assert ctl.spent_fen(res, 0, FAR, h) == 98 and ctl.spent_fen(res, 0, FAR, h + 10 ** 6) == 98   # expired: counted
    assert ctl.spent_fen(res + [charge_rec("S1", h + 10 ** 5, 5)], 0, FAR, h + 10 ** 6) == 98 + 5
    released = res + [{"kind": "release", "key": f"release:{REQ}", "req": REQ, "at": h + 60}]
    assert ctl.spent_fen(released, 0, FAR, h + 10 ** 6) == 0
    used = res + [on_rec(REQ, h + 60, req=REQ), off_rec(REQ, h + 1860)]   # used: only the session counts
    assert ctl.spent_fen(used, 0, FAR, h + 10 ** 6) == 49
    late = bj(2026, 9, 30, 23, 30)
    across = [reserve_rec(REQ, late, window_s=7200)]   # 196 fen, in full in both months
    assert ctl.spent_fen(across, bj(2026, 9, 1), bj(2026, 10, 1), late) == 196
    assert ctl.spent_fen(across, bj(2026, 10, 1), bj(2026, 11, 1), late) == 196


def test_charges_are_checked_as_a_batch(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    good = {"serial": "SN1", "instance": ID, "time": "2026-09-10 11:59:59", "amount": "0.16"}
    for bad in ({"instance": "ffff000000-0000ffff"}, {"amount": "-0.16"}, {"amount": "0.161"}, {"amount": "0"},
                {"time": "2026-09-10 12:05:01"}, {"serial": "bad serial"}, {"time": "yesterday"}):
        rows = json.dumps([{**good, "serial": "SN0"}, {**good, **bad}])
        assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", rows)[0] == ctl.EXIT_ERR, bad
    assert book() == []   # a batch with one bad row writes nothing
    rows = json.dumps([good, {**good, "serial": "SN2", "time": "2026-09-10T11:00:00+08:00", "amount": 0.1}])
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", rows)[0] == 0
    f = tmp_path / "rows.json"
    f.write_text(rows, encoding="utf-8")
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--file", f)[0] == 0   # again, from a file: no twice
    assert [(r["serial"], r["at"], r["fen"]) for r in book()] == [("SN1", MID - 1, 16), ("SN2", bj(2026, 9, 10, 11), 10)]
    other = json.dumps([{**good, "amount": "0.17"}])   # the same serial with other content
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", other)[0] == ctl.EXIT_ERR


def test_periods_follow_the_grants_time_zone():
    t_on, t_off = bj(2026, 9, 30, 23), bj(2026, 10, 1, 1)
    # two hours at 0.98, across midnight in Beijing; the power-off is on the hour, so its period gets 1 fen more
    recs = [on_rec("t1-1", t_on), off_rec("t1-1", t_off)]
    key, s, e = ctl.period_of(t_off - 1, {"period": "month", "period_tz": "+08:00"})
    assert key == "2026-10" and s == bj(2026, 10, 1) and ctl.spent_fen(recs, s, e, t_off) == 98 + 1
    g0 = {"period": "month", "period_tz": "+00:00"}   # in UTC the month turns at 08:00 Beijing time
    key, s, e = ctl.period_of(t_off - 1, g0)
    assert key == "2026-09" and s == int(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc).timestamp())
    assert ctl.spent_fen(recs, s, e, t_off) == 196 + 1
    assert ctl.period_of(bj(2026, 10, 1, 7, 59, 59), g0)[0] == "2026-09"
    assert ctl.period_of(bj(2026, 10, 1, 8), g0)[0] == "2026-10"
    assert ctl.period_of(t_off, {"period": "none", "period_tz": "+08:00"})[0] == "all"


def test_gpu_hours_count_gpu_seconds(capsys, clock):
    h = MID

    def g(recs):
        return ctl.spent_gpu_s(recs, 0, FAR, h + 10 ** 5)
    assert g([on_rec("t1-1", h, gpus=2), off_rec("t1-1", h + 3600)]) == 7200
    assert g([on_rec("t1-1", h), off_rec("t1-1", h + 3)]) == 3
    short = [r for i in range(3) for r in (on_rec(f"t{i}-1", h + i * 100), off_rec(f"t{i}-1", h + i * 100 + 3))]
    assert g(short) == 9
    assert g([on_rec("t1-1", h, price=10, mode="nogpu", gpus=0), off_rec("t1-1", h + 3600)]) == 0
    assert g([reserve_rec(REQ, h, window_s=7200, gpus=2)]) == 14400
    clock.set(h + 100)   # 0.001 GPU hours is 3.6 GPU seconds: 3 pass, 4 do not (a non-GPU check needs none)
    assert grant(capsys, budget="0.001gpuh")[0] == 0
    assert rc_json(capsys, "auth", "approve", "--instance", ID, "--quote", "ok")[0] == 0   # so small, always near its end
    put_records([on_rec("t1-1", h, unreserved=True), off_rec("t1-1", h + 3)])
    assert check(capsys, mode="nogpu", price="0.10", gpus=0)[0] == 0
    put_records([on_rec("t2-1", h + 10, unreserved=True), off_rec("t2-1", h + 11)])
    assert check(capsys, mode="nogpu", price="0.10", gpus=0)[0] == ctl.EXIT_BUDGET


def test_usage_of_an_instance_reads_the_ledger(capsys, clock, tmp_path):
    clock.set(MID)
    for i, proj in enumerate((tmp_path / "a", tmp_path / "b")):   # two projects, one instance; the second with 2 GPUs
        t = MID + i * 7200
        clock.set(t + 1800)   # both written afterwards, from the billing detail
        assert log_ev(capsys, "on", proj, t, gpus=1 + i)[0] == 0 and log_ev(capsys, "off", proj, t + 1800)[0] == 0
    clock.set(MID + 10000)
    rc, res = rc_json(capsys, "usage", "--instance", ID)   # GPU hours count each GPU; the price is per instance hour
    assert rc == 0 and res["gpu_hours"] == 0.5 + 1.0 and res["est_cost_yuan"] == 0.98


# ---- after the Phase 5 code review (docs/reviews/2026-10-01-subagent-phase5-code-triage.md) ----
def test_a_charge_before_1970_is_refused_and_the_record_stays_usable(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    for when in ("1969-12-31 23:00:00", "1926-09-10 12:00:00"):
        rows = json.dumps([{"serial": "SN1", "instance": ID, "time": when, "amount": "0.16"}])
        assert ctl.main(["auth", "charges", "--instance", ID, "--json", rows]) == ctl.EXIT_ERR, when
        assert "row SN1: its time is before 1970" in capsys.readouterr().err   # said by the row check itself
    assert book() == [] and show(capsys)["spent_fen"] == 0 and check(capsys)[0] == 0


def test_save_refuses_what_a_read_would_refuse(clock):
    clock.set(MID)
    with ctl.Store() as st:
        st.data["aliases"]["autodl-test"] = {"instance": ID, "at": MID}
        st.save()
    p = gpu_home() / "store.json"
    good = p.read_bytes()
    for change in (lambda d: d["ledger"].update({ID: [charge_rec("S1", -5, 10)]}),
                   lambda d: d["aliases"].update({"bad alias": {"instance": ID, "at": MID}}),
                   lambda d: d["ledger"].update({ID: [on_rec("t-5-1", MID)]})):
        with ctl.Store() as st:   # the same time: opening saves nothing
            change(st.data)
            with pytest.raises(ctl.StoreError, match="nothing was written") as e:
                st.save()
            assert e.value.kind == "invalid"
        assert p.read_bytes() == good
    with ctl.Store() as st:
        assert st.data["aliases"] == {"autodl-test": {"instance": ID, "at": MID}}


@pytest.mark.parametrize("at", [0, -5, MID + 301])
def test_log_at_must_be_a_time_up_to_now(capsys, clock, tmp_path, at):
    clock.set(MID)
    for event in ("on", "off", "note"):
        fields = ["--field", "mode=gpu", "--field", "price=0.98", "--field", "gpus=1"] if event == "on" else []
        rc = ctl.main(["log", event, "--instance", ID, "--project", str(tmp_path), "--at", str(at), *fields])
        # refused by the range check itself (on Windows the time 0 would also fail later, in the local time zone)
        assert rc == ctl.EXIT_ERR and f"--at {at}: a unix time" in capsys.readouterr().err, event
    assert book() is None and not (tmp_path / ".autodl").exists()
    assert log_ev(capsys, "on", tmp_path, MID + 300)[0] == 0   # five minutes ahead is taken, as for charges


@pytest.mark.parametrize("key", ["t", "event", "instance", "req"])
def test_log_fields_cannot_replace_the_logs_own_keys(capsys, clock, tmp_path, key):
    clock.set(MID)
    rc, _ = rc_json(capsys, "log", "on", "--instance", ID, "--project", tmp_path, "--at", MID, "--field", "mode=gpu",
                    "--field", "price=0.98", "--field", "gpus=1", "--field", f"{key}=x")
    assert rc == ctl.EXIT_ERR and book() is None and not (tmp_path / ".autodl").exists()


def test_a_resend_is_told_to_use_the_same_time(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    rc, res = check(capsys)
    assert rc == 0 and "--at <T0>" in res["next"]
    assert log_ev(capsys, "on", tmp_path, MID, req=res["req"])[0] == 0
    rc, out = log_ev(capsys, "on", tmp_path, MID + 5, req=res["req"])
    assert rc == ctl.EXIT_ERR and "the same --at" in out["error"]


def approve(capsys, *period, quote="ok"):
    return rc_json(capsys, "auth", "approve", "--instance", ID, "--quote", quote, *(["--period", *period] if period else []))


def test_consent_is_kept_per_period(capsys, clock):
    clock.set(bj(2026, 10, 31, 20))
    assert grant(capsys, budget="50yuan")[0] == 0
    # four hours at 10.50 (4200 fen), in full in October and in November: each would keep under 20 % of 5000
    rc, res = check(capsys, price="10.50", hours="4")
    assert rc == ctl.EXIT_BUDGET and res["period"] == "2026-10" and "--period 2026-10" in res["reason"]
    assert approve(capsys, "2026-10")[0] == 0
    rc, res = check(capsys, price="10.50", hours="4")
    assert rc == ctl.EXIT_BUDGET and res["period"] == "2026-11" and "--period 2026-11" in res["reason"]
    assert approve(capsys, "2026-11", quote="ok for november")[0] == 0
    rc, res = check(capsys, price="10.50", hours="4")
    assert rc == 0 and [p["period"] for p in res["periods"]] == ["2026-10", "2026-11"]
    ap = show(capsys)["grant"]["approvals"]
    assert sorted(ap) == ["2026-10", "2026-11"] and ap["2026-11"]["quote"] == "ok for november"
    assert ap["2026-10"]["at"] == bj(2026, 10, 31, 20)


def test_approve_takes_only_a_period_a_window_can_reach(capsys, clock):
    clock.set(bj(2026, 10, 31, 20))
    assert approve(capsys)[0] == ctl.EXIT_NO_GRANT   # no grant yet
    assert grant(capsys)[0] == 0
    for bad in ("2026-09", "2026-12", "2026-13", "2026-1", "all", "2026-11\n"):
        assert approve(capsys, bad)[0] == ctl.EXIT_ERR, bad
    assert show(capsys)["grant"]["approvals"] == {}
    assert approve(capsys)[0] == 0 and list(show(capsys)["grant"]["approvals"]) == ["2026-10"]   # this period
    assert grant(capsys)[0] == 0 and show(capsys)["grant"]["approvals"] == {}   # a new grant, no consent
    clock.set(bj(2027, 1, 31, 20))   # thirty days from here reach into March
    assert approve(capsys, "2027-03")[0] == 0 and approve(capsys, "2027-04")[0] == ctl.EXIT_ERR
    assert grant(capsys, period="none")[0] == 0
    assert approve(capsys, "2027-01")[0] == ctl.EXIT_ERR and approve(capsys)[0] == 0
    assert list(show(capsys)["grant"]["approvals"]) == ["all"]


def test_a_power_off_at_the_start_of_a_period_adds_its_fen_there():
    m = bj(2026, 11, 1)
    recs = [on_rec("t1-1", m - 1800), off_rec("t1-1", m)]   # 49 fen in October; 1 more for the power-off, in November
    assert ctl.spent_fen(recs, bj(2026, 10, 1), m, m + 60) == 49
    assert ctl.spent_fen(recs, m, bj(2026, 12, 1), m + 60) == 1
    later = recs + [charge_rec("S1", m, 1)]   # that charge came: nothing is added after it
    assert ctl.spent_fen(later, m, bj(2026, 12, 1), m + 60) == 1


@pytest.mark.parametrize("argv", [
    ["auth", "show", "--instance", ID + "\n"],
    ["auth", "check", "--instance", ID, "--mode", "gpu", "--price", "0.98\n", "--gpus", "1", "--hours", "1"],
    ["auth", "check", "--instance", ID, "--mode", "gpu", "--price", "0.98", "--gpus", "1", "--hours", "1\n"],
    ["auth", "grant", "--instance", ID, "--alias", "autodl-test\n", "--usage", "both", "--budget", "50yuan",
     "--period", "month", "--quote", "q"],
])
def test_values_with_a_trailing_newline_are_refused(capsys, clock, argv):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    assert rc_json(capsys, *argv)[0] == ctl.EXIT_ERR and book() == []   # nothing reserved
    assert show(capsys)["grant"]["alias"] == "autodl-test"
    rows = json.dumps([{"serial": "SN1\n", "instance": ID, "time": "2026-09-10 11:00:00", "amount": "0.16"}])
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", rows)[0] == ctl.EXIT_ERR and book() == []


def test_a_directory_made_by_hand_is_explained(capsys, clock):
    home = gpu_home()
    with ctl.Store():
        pass
    (home / "store.lock").unlink()   # a record whose lock file was deleted: not made again
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "unsafe" and "not made again" in str(e.value) and "remove" not in str(e.value)
    (home / "store.json").unlink()   # nothing of ctl's left in it, as in a directory made by hand
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "unsafe" and "not made by ctl" in str(e.value) and "remove it" in str(e.value)


def test_a_refused_save_still_writes_the_project_log(monkeypatch, capsys, clock, tmp_path):
    """Only a bug can reach a refused save (every input is checked first); the power-on still reaches the project log."""
    clock.set(MID)
    real = ctl.validate_ledger

    def picky(records, where):
        if any(r["kind"] == "on" for r in records):
            raise ValueError("a record a bug made")
        real(records, where)
    monkeypatch.setattr(ctl, "validate_ledger", picky)
    rc, res = log_ev(capsys, "on", tmp_path, MID)
    assert rc == ctl.EXIT_STORE and "nothing was written" in res["ledger"] and len(_project_lines(tmp_path)) == 1
    assert book() is None


@windows_only
@pytest.mark.parametrize("value", ["/autodl-test-rootless-home", "\\autodl-test-rootless-home"])
def test_a_home_without_a_drive_is_refused_on_windows(monkeypatch, value):
    monkeypatch.setenv("AUTODL_GPU_HOME", value)   # before 3.13 os.path.isabs takes it: it follows the current drive
    # taken, it would make a record at the current drive's root: fail before anything is made there
    monkeypatch.setattr(ctl, "publish_if_missing", lambda home: pytest.fail(f"{value!r} was taken as {home}"))
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "unsafe" and "drive letter" in str(e.value)


@windows_only
def test_a_directory_made_by_hand_is_explained_first(tmp_path, monkeypatch):
    home = tmp_path / "by-hand"
    os.mkdir(home)   # inherits the DACL of its parent: whether private or not, removing it is the advice
    monkeypatch.setenv("AUTODL_GPU_HOME", str(home))
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "unsafe" and "not made by ctl" in str(e.value) and "icacls" not in str(e.value)


def test_a_relative_gpu_home_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTODL_GPU_HOME", "rel-home")
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "unsafe" and "absolute" in str(e.value) and not (tmp_path / "rel-home").exists()


def test_an_unreadable_home_is_a_store_error(monkeypatch, capsys, clock, tmp_path):
    clock.set(MID)
    with ctl.Store():
        pass
    real, home = os.scandir, str(gpu_home())

    def refuse(path="."):
        if str(path) == home:
            raise PermissionError(13, "Permission denied", home)
        return real(path)
    monkeypatch.setattr(ctl.os, "scandir", refuse)
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "io"
    rc, res = log_ev(capsys, "on", tmp_path, MID)   # the project log is written all the same
    assert rc == ctl.EXIT_STORE and len(_project_lines(tmp_path)) == 1


@windows_only
def test_an_icacls_timeout_is_a_store_error(monkeypatch):
    real = ctl.subprocess.run

    def slow(argv, *a, **kw):
        if argv and argv[0] == "icacls":
            raise subprocess.TimeoutExpired(argv, 60)
        return real(argv, *a, **kw)
    monkeypatch.setattr(ctl.subprocess, "run", slow)
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "io" and "icacls" in str(e.value) and not gpu_home().exists()


def test_a_clock_behind_last_seen_is_explained(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    clock.set(MID - 3600)
    rc, res = check(capsys)
    assert rc == ctl.EXIT_STORE and "last_seen" in res["reason"] and str(gpu_home() / "store.json") in res["reason"]


def test_release_twice_and_an_unknown_request(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    req = check(capsys)[1]["req"]
    assert rc_json(capsys, "auth", "release", "--instance", ID, "--req", req)[0] == 0
    rc, res = rc_json(capsys, "auth", "release", "--instance", ID, "--req", req)
    assert rc == 0 and "before" in res["note"] and [r["kind"] for r in book()] == ["reserve", "release"]
    assert rc_json(capsys, "auth", "release", "--instance", ID, "--req", REQ)[0] == ctl.EXIT_ERR
    assert rc_json(capsys, "auth", "release", "--instance", ID, "--req", "xyz")[0] == ctl.EXIT_ERR
    assert [r["kind"] for r in book()] == ["reserve", "release"]


def test_log_off_names_its_session(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    req = check(capsys)[1]["req"]
    assert log_ev(capsys, "on", tmp_path, MID, req=req)[0] == 0
    assert log_ev(capsys, "off", tmp_path, MID + 60, req=REQ)[0] == ctl.EXIT_ERR   # another request: refused
    assert [r["kind"] for r in book()] == ["reserve", "on"] and len(_project_lines(tmp_path)) == 1
    rc, res = log_ev(capsys, "off", tmp_path, MID + 60, req=req)
    assert rc == 0 and res["session"] == req and book()[-1] == off_rec(req, MID + 60)
    rc, res = log_ev(capsys, "off", tmp_path, MID + 60, req=req)
    assert rc == 0 and res["ledger"] == "resend" and len(book()) == 3


def test_charge_rows_need_exactly_their_keys(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    good = {"serial": "SN1", "instance": ID, "time": "2026-09-10 11:00:00", "amount": "0.16"}
    for rows in [[{x: v for x, v in good.items() if x != k}] for k in good] + [[{**good, "note": "x"}]]:
        assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", json.dumps(rows))[0] == ctl.EXIT_ERR, rows
    assert book() == []


def test_a_grant_without_a_budget_reserves_and_passes(capsys, clock):
    clock.set(MID)
    assert grant(capsys, budget="none")[0] == 0
    rc, res = check(capsys, hours="100")
    assert rc == 0 and ctl.REQ_RE.match(res["req"]) and "hours_left" not in res
    assert [r["kind"] for r in book()] == ["reserve"]


def test_a_gpu_hour_budget_counts_a_gpu_power_on(capsys, clock):
    clock.set(MID)
    assert grant(capsys, budget="2gpuh")[0] == 0
    rc, res = check(capsys, gpus=2, hours="0.5")   # one GPU hour of two
    assert rc == 0 and res["hours_left"] == 0.5 and res["periods"][0]["need_gpu_s"] == 3600
    rc, res = check(capsys, gpus=2, hours="0.5")   # two of two would leave nothing: ask first
    assert rc == ctl.EXIT_BUDGET and "approve" in res["reason"] and res["remaining_gpu_hours"] == 1.0
    assert approve(capsys)[0] == 0 and check(capsys, gpus=2, hours="0.5")[0] == 0
    rc, res = check(capsys, gpus=2, hours="0.5")   # a third would go over
    assert rc == ctl.EXIT_BUDGET and "over the budget" in res["reason"]


# ---- after the document review of Phase 6 (docs/reviews/2026-10-01-codex-phase6-docs-triage.md) ----
PROBE = ("auth", "check", "--instance", ID, "--mode", "gpu", "--price", "0.98", "--gpus", 1, "--probe", "--hours")


def test_a_probe_judges_like_a_check_and_reserves_nothing(capsys, clock):
    clock.set(MID)
    assert rc_json(capsys, *PROBE, 1)[0] == ctl.EXIT_NO_GRANT
    assert grant(capsys, budget="5yuan")[0] == 0
    put_records([on_rec(f"t{MID}-1", MID)])   # a probe is about an instance that is on: its boot is on the books
    rc, res = rc_json(capsys, *PROBE, 1)
    assert rc == 0 and res["ok"] is True and res["probe"] is True and "req" not in res and "next" not in res
    assert res["remaining_fen"] == 500 - 98 and len(book()) == 1   # what a check would say, and nothing is held
    rc, res = rc_json(capsys, *PROBE, 5)   # 490 fen: under 20 % would be left, ask first
    assert rc == ctl.EXIT_BUDGET and "approve" in res["reason"] and len(book()) == 1
    rc, res = rc_json(capsys, *PROBE, 6)   # 588 fen: over
    assert rc == ctl.EXIT_BUDGET and "over the budget" in res["reason"] and len(book()) == 1
    assert check(capsys)[0] == 0 and [r["kind"] for r in book()] == ["on", "reserve"]   # a check without --probe reserves


def test_a_probe_counts_the_session_that_is_open(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys, budget="2gpuh")[0] == 0
    assert log_ev(capsys, "on", tmp_path, MID)[0] == 0
    clock.set(MID + 3600)   # one GPU hour used so far, one left
    rc, res = rc_json(capsys, *PROBE, "0.5")
    assert rc == 0 and res["periods"][0]["spent_gpu_s"] == 3600 and res["hours_left"] == 0.5
    rc, res = rc_json(capsys, *PROBE, "0.9")   # a tenth of an hour would be left: ask first
    assert rc == ctl.EXIT_BUDGET and "approve" in res["reason"]
    assert rc_json(capsys, *PROBE, "1.5")[0] == ctl.EXIT_BUDGET   # over
    assert [r["kind"] for r in book()] == ["on"]


def test_a_probe_with_a_broken_store_is_11(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    (gpu_home() / "store.json").write_bytes(b"not json")
    assert rc_json(capsys, *PROBE, 1)[0] == ctl.EXIT_STORE


def test_show_and_usage_give_the_open_session(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0 and show(capsys)["open_session"] is None
    assert log_ev(capsys, "on", tmp_path, MID, price="1.20", gpus=2)[0] == 0
    clock.set(MID + 600)
    want = {"session": f"t{MID}-1", "on": MID, "mode": "gpu", "gpus": 2, "price": 1.2}
    assert show(capsys)["open_session"] == want
    rc, res = rc_json(capsys, "usage", "--instance", ID)
    assert rc == 0 and res["open_session"] == want and res["running_since"] == MID
    assert log_ev(capsys, "off", tmp_path, MID + 600)[0] == 0
    assert show(capsys)["open_session"] is None
    rc, res = rc_json(capsys, "usage", "--instance", ID)
    assert rc == 0 and res["open_session"] is None and "running_since" not in res


def boot_verdict(capsys, booted):
    rc, res = rc_json(capsys, "auth", "show", "--instance", ID, "--booted-at", booted)
    assert rc == 0, res
    return res["grants"][ID]["this_boot"]


def test_show_says_whether_this_boot_is_recorded(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    assert "this_boot" not in show(capsys)   # only when asked with the time this boot began
    assert boot_verdict(capsys, MID - 30) == "not recorded"
    assert log_ev(capsys, "on", tmp_path, MID)[0] == 0   # T0 is noted before the click; the container starts after it
    clock.set(MID + 4000)
    assert boot_verdict(capsys, MID + 15) == "recorded"
    assert boot_verdict(capsys, MID + 120) == "recorded"   # up to two minutes after the session began
    assert boot_verdict(capsys, MID + 121) == "an earlier session is still open"   # this boot began later than that
    assert boot_verdict(capsys, MID - 3600) == "recorded"   # the session began during this boot (a take-over, say)
    assert log_ev(capsys, "off", tmp_path, MID + 3000)[0] == 0
    assert boot_verdict(capsys, MID + 3500) == "not recorded"
    assert rc_json(capsys, "auth", "show", "--booted-at", MID)[0] == ctl.EXIT_ERR   # of which instance?
    for bad in (0, -5, MID + 4000 + 301, "soon"):
        assert rc_json(capsys, "auth", "show", "--instance", ID, "--booted-at", bad)[0] == ctl.EXIT_ERR, bad


def take_over(capsys, project, booted, *more, fields=True):
    f = ["--field", "mode=gpu", "--field", "price=0.98", "--field", "gpus=1"] if fields else []
    return rc_json(capsys, "log", "on", "--instance", ID, "--project", project, "--booted-at", booted, *f, *more)


def test_a_take_over_is_recorded_from_the_boot_and_only_once(capsys, clock, tmp_path):
    clock.set(MID + 1200)   # the user powered it on twenty minutes ago
    rc, res = take_over(capsys, tmp_path, MID)
    assert rc == 0 and res["ledger"] == "recorded" and res["session"] == f"t{MID}-1"
    assert book() == [on_rec(f"t{MID}-1", MID, unreserved=True)] and len(_project_lines(tmp_path)) == 1
    assert json.loads(_project_lines(tmp_path)[0])["t"] == dt.datetime.fromtimestamp(MID).astimezone().isoformat(timespec="seconds")
    before = book()
    clock.set(MID + 5000)
    for booted, fields in ((MID, True), (MID + 60, True), (MID, False)):   # another conversation, the same boot
        rc, res = take_over(capsys, tmp_path / "other", booted, fields=fields)
        assert rc == 0 and res["ledger"] == "already" and res["session"] == f"t{MID}-1", res
        assert "not written" in res["project_log"] and book() == before
    assert not (tmp_path / "other").exists() and len(_project_lines(tmp_path)) == 1


def test_a_take_over_does_not_close_or_reuse_an_older_session(capsys, clock, tmp_path):
    clock.set(MID)
    assert log_ev(capsys, "on", tmp_path, MID)[0] == 0   # a power-on whose power-off was never noted
    before = book()
    clock.set(MID + 9000)
    rc, res = take_over(capsys, tmp_path, MID + 4000)   # this boot began long after that session did
    assert rc == ctl.EXIT_ERR and res["logged"] is None and "log off" in res["error"] and str(MID + 4000) in res["error"]
    assert f"after {MID}" in res["error"] and "this boot's" in res["error"]   # the charge to look for, and what if there is none
    assert book() == before and len(_project_lines(tmp_path)) == 1
    assert log_ev(capsys, "off", tmp_path, MID + 3000)[0] == 0   # its power-off, from the billing detail
    rc, res = take_over(capsys, tmp_path, MID + 4000)
    assert rc == 0 and res["ledger"] == "recorded" and book()[-1] == on_rec(f"t{MID + 4000}-1", MID + 4000, unreserved=True)


def test_a_take_over_needs_its_values_and_takes_no_request_or_time(capsys, clock, tmp_path):
    clock.set(MID + 1200)
    rc, res = take_over(capsys, tmp_path, MID, fields=False)   # nothing recorded yet: the price is needed
    assert rc == ctl.EXIT_ERR and book() is None and not (tmp_path / ".autodl").exists()
    assert take_over(capsys, tmp_path, MID, "--req", REQ)[0] == ctl.EXIT_ERR   # a take-over has no reservation
    assert take_over(capsys, tmp_path, MID, "--at", MID + 60)[0] == ctl.EXIT_ERR   # and begins at the boot
    for bad in (0, -5, MID + 1200 + 301, "soon"):
        assert take_over(capsys, tmp_path, bad)[0] == ctl.EXIT_ERR, bad
    assert book() is None and not (tmp_path / ".autodl").exists()
    assert rc_json(capsys, "log", "off", "--instance", ID, "--project", tmp_path, "--booted-at", MID)[0] == ctl.EXIT_ERR


def test_the_clock_command_lowers_last_seen(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0   # last_seen is MID now
    clock.set(MID - 3600)
    rc, res = check(capsys)
    assert rc == ctl.EXIT_STORE and "ctl auth clock" in res["reason"] and "by hand" not in res["reason"]
    assert rc_json(capsys, "auth", "clock")[0] == ctl.EXIT_ERR   # the user's words are required
    assert rc_json(capsys, "auth", "clock", "--quote", "  ")[0] == ctl.EXIT_ERR
    with ctl.Store() as st:
        assert st.data["last_seen"] == MID
    rc, res = rc_json(capsys, "auth", "clock", "--quote", "my clock is right now")
    assert rc == 0 and res == {"last_seen": MID - 3600, "was": MID, "lowered": True, "quote": "my clock is right now"}
    with ctl.Store() as st:
        assert st.data["last_seen"] == MID - 3600
    assert check(capsys)[0] == 0
    rc, res = rc_json(capsys, "auth", "clock", "--quote", "again")
    assert rc == 0 and res == {"last_seen": MID - 3600, "was": MID - 3600, "lowered": False, "quote": "again"}
    clock.set(MID + 50)   # a clock that is ahead of last_seen needs nothing: every open raises last_seen by itself
    rc, res = rc_json(capsys, "auth", "clock", "--quote", "later")
    assert rc == 0 and res["lowered"] is False and res["last_seen"] == MID + 50 and res["was"] == MID - 3600


# ---- after the second look at the documents (docs/reviews/2026-10-01-subagent-phase6-docs-r2-triage.md) ----
def test_a_probe_tells_which_open_session_it_counted(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys, budget="5yuan")[0] == 0
    for hours in (1, 6):   # nothing is recorded as on: the judgement would lack the boot so far, so there is none
        rc, res = rc_json(capsys, *PROBE, hours)
        assert rc == ctl.EXIT_ERR and res["ok"] is False and res["probe"] is True and res["open_session"] is None
        assert "log on --instance" in res["reason"] and "--booted-at" in res["reason"] and "periods" not in res
    assert book() == []
    assert log_ev(capsys, "on", tmp_path, MID)[0] == 0
    clock.set(MID + 600)
    rc, res = rc_json(capsys, *PROBE, 1)
    assert rc == 0 and "note" not in res
    assert res["open_session"] == {"session": f"t{MID}-1", "on": MID, "mode": "gpu", "gpus": 1, "price": 0.98}
    rc, res = rc_json(capsys, *PROBE, 6)
    assert rc == ctl.EXIT_BUDGET and res["open_session"]["on"] == MID and "note" not in res
    rc, res = check(capsys)   # a check before a power-on is not about an instance that is on
    assert rc == 0 and "open_session" not in res and "note" not in res
    rc, res = check(capsys, hours="6")   # whether it passes or not
    assert rc == ctl.EXIT_BUDGET and "open_session" not in res and "note" not in res


def take_over_as(capsys, project, booted, mode, price, gpus):
    return rc_json(capsys, "log", "on", "--instance", ID, "--project", project, "--booted-at", booted,
                   "--field", f"mode={mode}", "--field", f"price={price}", "--field", f"gpus={gpus}")


def test_a_take_over_in_another_mode_is_not_the_open_session_s_boot(capsys, clock, tmp_path):
    clock.set(MID)
    assert log_ev(capsys, "on", tmp_path, MID, mode="nogpu", price="0.10", gpus=0)[0] == 0   # its power-off was never noted
    before = book()
    clock.set(MID + 7100)
    rc, res = take_over_as(capsys, tmp_path, MID + 100, "gpu", "0.98", 1)   # a GPU boot that began 100 s after that session
    assert rc == ctl.EXIT_ERR and res["logged"] is None and "nogpu" in res["error"] and "log off" in res["error"], res
    assert f"after {MID} and before this boot ({MID + 100})" in res["error"] and "one of the two is wrong" in res["error"]
    assert book() == before and len(_project_lines(tmp_path)) == 1
    rc, res = take_over_as(capsys, tmp_path, MID + 100, "nogpu", "0.10", 0)   # the same mode and GPUs: this boot's
    assert rc == 0 and res["ledger"] == "already"
    rc, res = take_over_as(capsys, tmp_path, MID + 100, "nogpu", "0.20", 0)   # a price may be an estimate: not held against it
    assert rc == 0 and res["ledger"] == "already" and book() == before


def test_a_take_over_with_another_gpu_count_is_not_this_boot_either(capsys, clock, tmp_path):
    clock.set(MID)
    assert log_ev(capsys, "on", tmp_path, MID, gpus=2)[0] == 0
    before = book()
    clock.set(MID + 600)
    rc, res = take_over_as(capsys, tmp_path, MID + 10, "gpu", "0.98", 1)
    assert rc == ctl.EXIT_ERR and "2 GPU" in res["error"] and book() == before, res
    rc, res = take_over(capsys, tmp_path, MID + 10, fields=False)   # asked without values, it still answers
    assert rc == 0 and res["ledger"] == "already" and res["open_session"]["gpus"] == 2


def test_a_take_over_without_values_writes_nothing_when_the_record_cannot_be_used(capsys, clock, tmp_path):
    clock.set(MID + 1200)
    assert grant(capsys)[0] == 0
    (gpu_home() / "store.json").write_bytes(b"not json")
    rc, res = take_over(capsys, tmp_path, MID, fields=False)   # whether it is recorded cannot be told: the values are needed
    assert rc == ctl.EXIT_ERR and not (tmp_path / ".autodl").exists()
    rc, res = take_over(capsys, tmp_path, MID)   # with its values: the project log is written, the ledger is not
    assert rc == ctl.EXIT_STORE and res["project_log"] == "written" and len(_project_lines(tmp_path)) == 1


def test_a_take_over_under_a_name_that_is_no_known_instance_goes_to_the_project_log_only(capsys, clock, tmp_path):
    clock.set(MID + 1200)
    rc, res = rc_json(capsys, "log", "on", "--instance", "some-alias", "--project", tmp_path, "--booted-at", MID,
                      "--field", "mode=gpu", "--field", "price=0.98", "--field", "gpus=1")
    assert rc == 0 and res["ledger"] == "unknown instance" and book() is None and len(_project_lines(tmp_path)) == 1
    rc, res = rc_json(capsys, "log", "on", "--instance", "some-alias", "--project", tmp_path / "p2", "--booted-at", MID)
    assert rc == ctl.EXIT_ERR and not (tmp_path / "p2").exists()   # without values nothing is written


def void(capsys, project, *more, quote="yes, close it"):
    words = [] if quote is None else ["--quote", quote]
    return rc_json(capsys, "log", "off", "--instance", ID, "--project", project, "--void", *words, *more)


def test_a_session_that_no_time_can_close_is_closed_at_its_own_start(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys, budget="5yuan")[0] == 0
    assert void(capsys, tmp_path)[0] == ctl.EXIT_ERR and book() == []   # nothing is open
    assert log_ev(capsys, "on", tmp_path, MID)[0] == 0
    rc, res = log_ev(capsys, "off", tmp_path, MID - 10)   # the last charge is before the start of the session
    assert rc == ctl.EXIT_ERR and "before the power-on" in res["error"]
    clock.set(MID + 86400)
    assert show(capsys)["spent_fen"] > 500   # left open, it is counted up to now
    before = book()
    for more in (("--at", MID), ("--req", REQ), ("--booted-at", MID)):   # no time is given; REQ is not this session's
        assert void(capsys, tmp_path, *more)[0] == ctl.EXIT_ERR
    for event, fields in (("on", ("--field", "mode=gpu", "--field", "price=0.98", "--field", "gpus=1")), ("note", ())):
        assert rc_json(capsys, "log", event, "--instance", ID, "--project", tmp_path, "--void", "--quote", "yes", *fields)[0] == ctl.EXIT_ERR
    for words in (None, "  "):                # the user's own words are needed, as for a grant
        assert void(capsys, tmp_path, quote=words)[0] == ctl.EXIT_ERR
    assert rc_json(capsys, "log", "off", "--instance", ID, "--project", tmp_path, "--at", MID + 5, "--quote", "yes")[0] == ctl.EXIT_ERR
    assert book() == before and len(_project_lines(tmp_path)) == 1      # --quote goes with --void and with nothing else
    rc, res = void(capsys, tmp_path)
    assert rc == 0 and res["ledger"] == "recorded" and res["session"] == f"t{MID}-1" and book()[-1] == off_rec(f"t{MID}-1", MID)
    last = json.loads(_project_lines(tmp_path)[-1])
    assert last["event"] == "off" and last["time"] == "void" and last["quote"] == "yes, close it"
    assert last["t"] == dt.datetime.fromtimestamp(MID).astimezone().isoformat(timespec="seconds")
    assert show(capsys)["open_session"] is None and show(capsys)["spent_fen"] <= 1
    assert void(capsys, tmp_path)[0] == ctl.EXIT_ERR   # once


def test_a_void_may_name_its_session(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    req = check(capsys)[1]["req"]
    assert log_ev(capsys, "on", tmp_path, MID, req=req)[0] == 0
    rc, res = void(capsys, tmp_path, "--req", req)
    assert rc == 0 and res["session"] == req and book()[-1] == off_rec(req, MID)


def test_a_session_dated_ahead_of_the_clock_can_be_voided(capsys, clock, tmp_path):
    clock.set(MID + 86400)   # a run while this clock was a day ahead
    assert grant(capsys)[0] == 0
    req = check(capsys)[1]["req"]
    assert log_ev(capsys, "on", tmp_path, MID + 86400)[0] == 0   # noted without its request: the reservation stays open
    clock.set(MID)
    rc, res = rc_json(capsys, "auth", "clock", "--quote", "the clock is right now")
    assert rc == 0 and res["lowered"] is True
    assert res["dated_ahead"] == [{"instance": ID, "kind": "on", "at": MID + 86400, "session": f"t{MID + 86400}-1"},
                                  {"instance": ID, "kind": "reserve", "at": MID + 86400, "req": req}]
    assert "--void" in res["note"] and "auth release" in res["note"]
    assert "--quote" in res["note"] and "--booted-at" in res["note"]   # the user's word, and what if that instance is still on
    assert log_ev(capsys, "off", tmp_path, MID + 60)[0] == ctl.EXIT_ERR   # before its start
    assert log_ev(capsys, "off", tmp_path, MID + 86400)[0] == ctl.EXIT_ERR   # its start is not a time that can be given
    rc, res = void(capsys, tmp_path)
    assert rc == 0 and res["ledger"] == "recorded" and show(capsys)["open_session"] is None
    assert rc_json(capsys, "auth", "release", "--instance", ID, "--req", req)[0] == 0
    assert check(capsys)[0] == 0 and log_ev(capsys, "on", tmp_path, MID)[0] == 0   # a reservation and a session of now
    rc, res = rc_json(capsys, "auth", "clock", "--quote", "again")
    assert rc == 0 and res["lowered"] is False and "dated_ahead" not in res and "note" not in res


def test_the_clock_command_refuses_when_the_ledger_proves_the_clock_is_behind(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys, budget="5yuan")[0] == 0
    assert log_ev(capsys, "on", tmp_path, MID)[0] == 0
    clock.set(MID + 7200)
    assert log_ev(capsys, "off", tmp_path, MID + 7200)[0] == 0
    # the charge of that power-off, dated by the platform: MID + 7200
    row = {"serial": "SN1", "instance": ID, "time": "2026-09-10 14:00:00", "amount": "1.96"}
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", json.dumps([row]))[0] == 0
    clock.set(MID - 15 * 86400)   # this machine's clock now says two weeks earlier, in the month before
    assert check(capsys)[0] == ctl.EXIT_STORE
    rc, res = rc_json(capsys, "auth", "clock", "--quote", "my clock is right")
    assert rc == ctl.EXIT_ERR and res["lowered"] is False and "behind" in res["error"] and ID in res["error"], res
    assert "charge" in res["error"] and str(MID + 7200) in res["error"]
    with ctl.Store() as st:
        assert st.data["last_seen"] == MID + 7200   # nothing was lowered
    assert check(capsys)[0] == ctl.EXIT_STORE   # and the gate still refuses


def test_a_power_off_dated_ahead_proves_nothing_about_the_clock(capsys, clock, tmp_path):
    clock.set(MID + 86400)   # a power-on and its power-off while this clock was a day ahead, both dated by it
    assert grant(capsys)[0] == 0 and log_ev(capsys, "on", tmp_path, MID + 86400)[0] == 0
    clock.set(MID + 90000)
    assert rc_json(capsys, "log", "off", "--instance", ID, "--project", tmp_path)[0] == 0   # no --at: this clock's now
    # its charge carries the true time, MID + 3600
    row = {"serial": "SN1", "instance": ID, "time": "2026-09-10 13:00:00", "amount": "0.98"}
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", json.dumps([row]))[0] == 0
    clock.set(MID + 3700)   # the clock was put right
    assert check(capsys)[0] == ctl.EXIT_STORE
    rc, res = rc_json(capsys, "auth", "clock", "--quote", "the clock is right now")
    assert rc == 0 and res["lowered"] is True, res   # the charge is not ahead of this clock, the power-off is no proof
    assert "dated_ahead" not in res   # a closed session needs nothing done
    assert check(capsys)[0] == 0


def test_a_charge_is_proof_only_beyond_the_five_minutes_of_slack(capsys, clock, tmp_path):
    clock.set(MID + 3600)
    assert grant(capsys)[0] == 0 and log_ev(capsys, "on", tmp_path, MID)[0] == 0
    assert log_ev(capsys, "off", tmp_path, MID + 3600)[0] == 0
    # the charge of the power-off: MID + 3600
    row = {"serial": "SN1", "instance": ID, "time": "2026-09-10 13:00:00", "amount": "0.98"}
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", json.dumps([row]))[0] == 0
    clock.set(MID + 4000)
    assert check(capsys)[0] == 0   # last_seen is MID + 4000 now
    clock.set(MID + 3299)   # the charge is 301 s ahead of this clock
    assert check(capsys)[0] == ctl.EXIT_STORE
    assert rc_json(capsys, "auth", "clock", "--quote", "it is right")[0] == ctl.EXIT_ERR
    clock.set(MID + 3300)   # 300 s: what an import allows, so no proof
    rc, res = rc_json(capsys, "auth", "clock", "--quote", "it is right")
    assert rc == 0 and res["lowered"] is True and res["was"] == MID + 4000, res


def test_a_voided_session_does_not_stand_in_the_way_of_the_clock_command_later(capsys, clock, tmp_path):
    clock.set(MID + 86400)
    assert grant(capsys)[0] == 0 and log_ev(capsys, "on", tmp_path, MID + 86400)[0] == 0
    clock.set(MID)
    assert rc_json(capsys, "auth", "clock", "--quote", "right now")[0] == 0 and void(capsys, tmp_path)[0] == 0
    clock.set(MID + 7200)
    assert check(capsys)[0] == 0   # last_seen is MID + 7200 now
    clock.set(MID + 3600)   # and the clock steps back once more
    assert check(capsys)[0] == ctl.EXIT_STORE
    rc, res = rc_json(capsys, "auth", "clock", "--quote", "right again")
    assert rc == 0 and res["lowered"] is True, res


def test_a_void_writes_nothing_when_it_cannot_tell_which_session(capsys, clock, tmp_path):
    clock.set(MID)
    rc, res = rc_json(capsys, "log", "off", "--instance", "some-alias", "--project", tmp_path / "p1", "--void", "--quote", "yes")
    assert rc == ctl.EXIT_ERR and "some-alias" in res["error"] and not (tmp_path / "p1").exists()
    assert grant(capsys)[0] == 0 and log_ev(capsys, "on", tmp_path / "p2", MID)[0] == 0
    (gpu_home() / "store.json").write_bytes(b"not json")
    rc, res = void(capsys, tmp_path / "p3")   # which session, and from when, is only in the record
    assert rc == ctl.EXIT_STORE and res["logged"] is None and not (tmp_path / "p3").exists()


# ---- after the reviewer confirmed that round (docs/reviews/2026-10-01-subagent-phase6-docs-r3-triage.md) ----
@pytest.mark.parametrize("later", [12, 0], ids=["the container started 12 s after T0", "at T0 to the second"])
def test_log_on_refused_by_an_open_session_says_which_power_on_that_is(capsys, clock, tmp_path, later):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    req = check(capsys)[1]["req"]                    # this conversation is about to power on: T0 is MID
    clock.set(MID + 300)
    assert take_over(capsys, tmp_path / "other", MID + later)[0] == 0   # meanwhile another conversation took the boot over
    before = book()
    rc, res = log_ev(capsys, "on", tmp_path, MID, req=req)
    assert rc == ctl.EXIT_ERR and book() == before, res
    assert "this very boot" in res["error"] and f"on since {MID + later}" in res["error"] and "auth release" in res["error"]
    assert "log off" not in res["error"]             # nothing is to be closed
    assert rc_json(capsys, "auth", "release", "--instance", ID, "--req", req)[0] == 0
    clock.set(MID + 9000)                            # a session that began before this power-on is an earlier one, as before
    rc, res = log_ev(capsys, "on", tmp_path, MID + 8000)
    assert rc == ctl.EXIT_ERR and "log off" in res["error"] and "this very boot" not in res["error"]


def test_after_a_void_a_boot_that_is_still_on_is_recorded_again_as_a_take_over(capsys, clock, tmp_path):
    clock.set(MID + 86400)                           # the power-on was logged while this clock was a day ahead
    assert grant(capsys, budget="2gpuh")[0] == 0 and log_ev(capsys, "on", tmp_path, MID + 86400)[0] == 0
    clock.set(MID + 3600)                            # an hour into the boot the clock is right again
    assert rc_json(capsys, "auth", "clock", "--quote", "the clock is right now")[0] == 0
    assert void(capsys, tmp_path)[0] == 0
    rc, res = rc_json(capsys, *PROBE, 1.5)           # the boot is off the books: the probe refuses, and says that none is open
    assert rc == ctl.EXIT_ERR and res["open_session"] is None and "--booted-at" in res["reason"]
    rc, res = take_over(capsys, tmp_path, MID + 15)  # by this clock the boot began at MID + 15
    assert rc == 0 and res["ledger"] == "recorded"
    rc, res = rc_json(capsys, *PROBE, 1.5)           # an hour is used: 1.5 more would go over the 2 GPU hours
    assert rc == ctl.EXIT_BUDGET and res["open_session"]["on"] == MID + 15


# ---- after the review of the whole release (docs/reviews/2026-10-01-codex-phase7-triage.md): where a budget counts
# from, and a probe that needs the boot on the books ----
CHARGES = ("auth", "charges", "--instance", ID, "--json")


def test_a_monthly_money_budget_needs_this_periods_charges_before_any_check(capsys, clock):
    clock.set(MID)
    rc, res = grant(capsys, budget="50yuan", read=False)
    assert rc == 0 and "auth charges" in res["note"] and "'[]'" in res["note"] and "2026-09" in res["note"]
    assert "charges_read" not in show(capsys)["grant"] and "auth charges" in show(capsys)["baseline"]
    for argv in (("auth", "check", "--instance", ID, "--mode", "gpu", "--price", "0.98", "--gpus", 1, "--hours", 1),
                 (*PROBE, 1)):
        rc, res = rc_json(capsys, *argv)   # what was spent before the grant would count as nothing
        assert rc == ctl.EXIT_ERR and res["ok"] is False and "auth charges" in res["reason"] and "'[]'" in res["reason"]
        assert "periods" not in res
    assert book() == []                    # nothing was reserved
    # the billing detail was read and shows no charge of this period: an empty import says so
    rc, res = rc_json(capsys, *CHARGES, "[]")
    assert rc == 0 and res == {"instance": ID, "imported": 0, "already": 0, "charges_read": MID}
    assert show(capsys)["grant"]["charges_read"] == MID and "baseline" not in show(capsys) and book() == []
    assert check(capsys)[0] == 0
    # every new grant asks again: it is when the books are looked at anew
    clock.advance(600)
    rc, res = grant(capsys, budget="80yuan", read=False)
    assert rc == 0 and "auth charges" in res["note"] and check(capsys)[0] == ctl.EXIT_ERR
    row = {"serial": "SN7", "instance": ID, "time": "2026-09-10 11:59:59", "amount": "40.00"}
    rc, res = rc_json(capsys, *CHARGES, json.dumps([row]))   # an import with rows settles it as well
    assert rc == 0 and res["imported"] == 1 and res["charges_read"] == MID + 600
    rc, res = check(capsys)
    assert rc == 0 and res["periods"][0]["spent_fen"] == 4000 + 98   # the 40 yuan of before, and the open reservation


def test_an_import_without_a_grant_marks_nothing(capsys, clock, tmp_path):
    clock.set(MID)
    assert log_ev(capsys, "on", tmp_path, MID - 7200)[0] == 0 and log_ev(capsys, "off", tmp_path, MID - 3600)[0] == 0
    rc, res = rc_json(capsys, *CHARGES, "[]")   # the ledger is known (a power-on was logged), but nothing is granted
    assert rc == 0 and res == {"instance": ID, "imported": 0, "already": 0}
    rc, res = grant(capsys, read=False)          # the grant that follows still wants its own look at the charges
    assert "auth charges" in res["note"] and check(capsys)[0] == ctl.EXIT_ERR


def test_a_gpu_hour_budget_says_what_it_counts_and_needs_no_import(capsys, clock):
    clock.set(MID)
    put_records([charge_rec("SN1", MID - 86400, 9000)])   # 90 yuan charged earlier this month, by hand
    rc, res = grant(capsys, budget="10gpuh", read=False)
    assert rc == 0 and "only the power-ons this ledger records" in res["note"] and "time=estimated" in res["note"]
    assert "Tell the user" in res["note"] and "baseline" not in show(capsys)
    rc, res = check(capsys)                               # charges cannot give GPU hours: nothing to wait for
    assert rc == 0 and res["periods"][0]["spent_gpu_s"] == 0


@pytest.mark.parametrize("budget, need", [("50yuan", "spent_fen"), ("10gpuh", "spent_gpu_s")])
def test_a_budget_without_periods_counts_from_the_hour_of_its_grant(capsys, clock, budget, need):
    clock.set(MID + 1234)
    put_records([on_rec("t1-1", MID - 7 * 86400), off_rec("t1-1", MID - 7 * 86400 + 7200),   # two GPU hours a week ago
                 charge_rec("SN0", MID - 86400, 4000)])                                       # and 40 yuan yesterday
    rc, res = grant(capsys, budget=budget, period="none", read=False)
    assert rc == 0 and "counts from" in res["note"] and "revoke" in res["note"]
    g = show(capsys)["grant"]
    assert g["since"] == MID and show(capsys)["period"] == "all" and show(capsys)[need] == 0   # the full hour; no history
    assert "baseline" not in show(capsys) and check(capsys)[0] == 0                            # and no import is owed
    rc, res = rc_json(capsys, "auth", "release", "--instance", ID, "--req", check(capsys)[1]["req"])
    with ctl.Store() as st:   # drop the first reservation too: only sessions and charges are looked at below
        st.data["ledger"][ID] = [r for r in st.data["ledger"][ID] if r["kind"] not in ("reserve", "release")]
        st.save()
    put_records([on_rec("t2-1", MID + 3600), off_rec("t2-1", MID + 7000), charge_rec("SN2", MID + 7000, 93)])   # 3400 s at 0.98
    clock.set(MID + 86400)
    assert show(capsys)[need] == (93 if need == "spent_fen" else 3400)       # what came after the grant counts
    # a new amount is a new total: it goes on counting from the same start
    assert grant(capsys, budget=budget.replace("50", "80").replace("10g", "20g"), period="none", read=False)[0] == 0
    assert show(capsys)["grant"]["since"] == MID and show(capsys)[need] == (93 if need == "spent_fen" else 3400)
    # to start over: revoke, then grant
    assert rc_json(capsys, "auth", "revoke", "--instance", ID)[0] == 0
    assert grant(capsys, budget=budget, period="none", read=False)[0] == 0
    assert show(capsys)["grant"]["since"] == MID + 86400 and show(capsys)[need] == 0


def test_since_is_kept_only_between_budgets_without_periods_of_one_kind(capsys, clock):
    clock.set(MID + 5)
    assert grant(capsys, budget="50yuan", period="none", read=False)[0] == 0 and show(capsys)["grant"]["since"] == MID
    clock.set(MID + 7200)
    assert grant(capsys, budget="10gpuh", period="none", read=False)[0] == 0      # another unit: a new start
    assert show(capsys)["grant"]["since"] == MID + 7200
    clock.set(MID + 10800)
    assert grant(capsys, budget="none", period="none", read=False)[0] == 0 and "since" not in show(capsys)["grant"]
    assert grant(capsys, budget="50yuan", period="month", read=False)[0] == 0 and "since" not in show(capsys)["grant"]
    assert grant(capsys, budget="50yuan", period="none", read=False)[0] == 0      # from a monthly budget: a new start
    assert show(capsys)["grant"]["since"] == MID + 10800


def test_a_record_with_a_grant_of_the_old_shape_still_opens(capsys, clock):
    clock.set(MID)
    assert grant(capsys, budget="50yuan", period="none", read=False)[0] == 0
    with ctl.Store() as st:   # as records written before this change are: no since, no charges_read
        del st.data["grants"][ID]["since"]
        st.save()
    assert show(capsys)["spent_fen"] == 0 and check(capsys)[0] == 0   # all time, as it was
    for bad in ({"since": -1}, {"since": "x"}, {"charges_read": 1.5}):
        with ctl.Store() as st:
            st.data["grants"][ID].update(bad)
            with pytest.raises(ctl.StoreError):
                st.save()
    with ctl.Store() as st:   # a start is for a budget without periods only
        st.data["grants"][ID].update(period="month", since=MID)
        with pytest.raises(ctl.StoreError):
            st.save()


# ---- the second round of that review (docs/reviews/2026-10-01-codex-phase7-r2-triage.md) ----
def test_a_new_month_needs_its_own_look_at_the_charges(capsys, clock):
    clock.set(MID)
    assert grant(capsys, budget="50yuan")[0] == 0 and check(capsys)[0] == 0   # September: imported, so judged
    clock.set(MID + 30 * 86400)                                               # 10 October, the same grant
    assert "auth charges" in show(capsys)["baseline"] and "2026-10" in show(capsys)["baseline"]
    for argv in (("auth", "check", "--instance", ID, "--mode", "gpu", "--price", "0.98", "--gpus", 1, "--hours", 1),
                 (*PROBE, 1)):
        rc, res = rc_json(capsys, *argv)   # what October was charged before this check is in no import yet
        assert rc == ctl.EXIT_ERR and "auth charges" in res["reason"] and "periods" not in res
    row = {"serial": "SN8", "instance": ID, "time": "2026-10-03 09:59:59", "amount": "30.00"}
    assert rc_json(capsys, *CHARGES, json.dumps([row]))[0] == 0
    assert "baseline" not in show(capsys)
    rc, res = check(capsys)
    assert rc == 0 and res["periods"][0]["spent_fen"] == 3000


def test_a_new_amount_for_a_budget_of_the_old_shape_keeps_its_history(capsys, clock):
    clock.set(MID)
    put_records([charge_rec("SN0", MID - 86400, 4000)])   # 40 yuan yesterday
    assert grant(capsys, budget="50yuan", period="none", read=False)[0] == 0
    with ctl.Store() as st:   # as a record written before the start was kept: it counts everything
        del st.data["grants"][ID]["since"]
        st.save()
    assert show(capsys)["spent_fen"] == 4000
    clock.advance(7200)
    rc, res = grant(capsys, budget="80yuan", period="none", read=False)   # only the amount changes
    assert rc == 0 and show(capsys)["grant"]["since"] == 0 and show(capsys)["spent_fen"] == 4000
    assert "everything" in res["note"] and "revoke" in res["note"]
    clock.advance(7200)   # another unit starts anew, as for any grant
    assert grant(capsys, budget="10gpuh", period="none", read=False)[0] == 0
    assert show(capsys)["grant"]["since"] == MID + 14400
