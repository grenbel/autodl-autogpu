"""Tests for scripts/autodl_ctl.py. SSH is replaced by FakeRunner; nothing leaves this machine."""
import datetime as dt
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import autodl_ctl as ctl  # noqa: E402
import local_tools  # noqa: E402


# a name that is on no PATH: a test that slips past the fakes can never reach a real ssh
FAKE_SSH = "autodl-test-no-such-ssh"


SSH_SAYS = {  # what ssh writes into its -E log file in each situation
    "started": 'Authenticated to 203.0.113.5 ([203.0.113.5]:40022) using "publickey".\r\n',
    "dropped": "Connection closed by 203.0.113.5 port 40022\r\n",   # before authentication
    "lost": 'Authenticated to 203.0.113.5 ([203.0.113.5]:40022) using "publickey".\r\n',  # after it
    "unknown": "ssh: something nobody has seen before\r\n",
    # a reset of an established session: shares words with pre-authentication failures
    "reset_after": "Read from remote host 203.0.113.5: Connection reset by peer\r\n",
    "timeout_after": "ssh: connect to host 203.0.113.5 port 40022: Connection timed out\r\n",
}


def _situation(rc, flag):
    if flag is True or flag is None and rc != 255:
        return "started"
    return "dropped" if flag in (False, None) else flag


def _write_ssh_log(argv, situation):
    if "-E" in argv:
        with open(argv[argv.index("-E") + 1], "a", encoding="utf-8") as f:
            f.write(SSH_SAYS[situation])


class FakeRunner:
    """Stands in for subprocess.run. The last argv item (the remote command, without the
    start marker ctl puts in front) is matched exactly first, then by substring, against
    the table keys. A list value is a script of results consumed one per call (the last
    one repeats). A result is (rc, stdout, stderr) or (rc, stdout, stderr, situation).
    situation True or "started": the command started, so the marker comes first on stderr;
    False or "dropped": sshd dropped the connection before authentication; "lost": the
    connection failed after authentication and the marker never arrived; "unknown": ssh
    failed in a way ctl does not recognise. By default only rc 255 means "dropped"."""

    def __init__(self, table, default=(0, b"", b"")):
        self.table = table
        self.default = default
        self.calls = []
        self.checked = []   # per call: the instance whose host name the remote command checks first, or None

    def __call__(self, argv, input=None, capture_output=True, timeout=None):
        self.calls.append((argv, input))
        prefix = getattr(ctl, "MARKER_PREFIX", None)
        remote = argv[-1]
        marked = bool(prefix) and remote.startswith(prefix)
        body = remote[len(prefix):] if marked else remote
        iid, body = _unguard(body)
        self.checked.append(iid)
        hit = self.table.get(body)
        if hit is None:
            hit = next((v for k, v in self.table.items() if k in body), self.default)
        if isinstance(hit, list):
            hit = hit.pop(0) if len(hit) > 1 else hit[0]
        rc, out, err, *rest = hit
        if rest and rest[0] == "timeout":   # ssh hung until subprocess.run gave up; the command may have run
            _write_ssh_log(argv, "started")
            raise subprocess.TimeoutExpired(argv, timeout or 60, output=out, stderr=err)
        situation = _situation(rc, rest[0] if rest else None)
        _write_ssh_log(argv, situation)
        if marked and situation == "started":
            err = (ctl.MARKER + "\n").encode() + err
        return subprocess.CompletedProcess(argv, rc, out, err)


def _no_popen(argv, **kw):
    raise AssertionError(f"unexpected streaming ssh call: {argv}")


def _unguard(body):
    """(the instance whose host name a remote command checks before anything else, or None; the command without
    that check). The fakes match on the command itself."""
    for iid in (INSTANCE, OTHER):
        guard = ctl.host_guard(iid)
        if body.startswith(guard):
            return iid, body[len(guard):]
    return None, body


def _unbound(monkeypatch):
    """The tests of what a command does run it without the host-name check (as check, wait and doctor always run); the
    tests of the check itself are at the end of this file."""
    monkeypatch.setattr(ctl, "bound_instance", lambda a: None)


def use(monkeypatch, table, default=(0, b"", b""), bound=False):
    fake = FakeRunner(table, default)
    monkeypatch.setattr(ctl, "RUNNER", fake)
    monkeypatch.setattr(ctl, "POPEN", _no_popen, raising=False)
    monkeypatch.setenv("AUTODL_SSH", FAKE_SSH)
    monkeypatch.setattr(ctl.time, "sleep", lambda s: None)
    if not bound:
        _unbound(monkeypatch)
    return fake


class _Sink(io.BytesIO):
    """stdin of FakePopen: keeps what was written after ctl closes it."""
    def close(self):
        self.data = self.getvalue()
        super().close()


class FakePopen:
    """Stands in for subprocess.Popen in pull and push: stdout carries `out`, the process
    ends with `rc`, and the start marker is on stderr when the command started."""

    def __init__(self, argv, rc=0, out=b"", err=b"", started=None):
        situation = _situation(rc, started)
        _write_ssh_log(argv, situation)
        prefix = getattr(ctl, "MARKER_PREFIX", None)
        if prefix and argv[-1].startswith(prefix) and situation == "started":
            err = (ctl.MARKER + "\n").encode() + err
        self.args, self._rc, self.returncode = argv, rc, None
        self.stdout, self.stderr, self.stdin = io.BytesIO(out), io.BytesIO(err), _Sink()

    def wait(self, timeout=None):
        self.returncode = self._rc
        return self._rc

    def kill(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def popen_script(monkeypatch, results, bound=False):
    """Each streaming ssh call takes the next (rc, out, err[, started]); the last one repeats."""
    procs = []

    def factory(argv, **kw):
        rc, out, err, *rest = results.pop(0) if len(results) > 1 else results[0]
        procs.append(FakePopen(argv, rc, out, err, rest[0] if rest else None))
        return procs[-1]
    monkeypatch.setattr(ctl, "POPEN", factory, raising=False)
    monkeypatch.setenv("AUTODL_SSH", FAKE_SSH)
    monkeypatch.setattr(ctl.time, "sleep", lambda s: None)
    if not bound:
        _unbound(monkeypatch)
    return procs


# sshd dropped the connection before authentication (MaxStartups): nothing ran
DROP = (255, b"", b"", False)
# the connection failed after authentication and before the marker arrived: it may have run
LOST = (255, b"", b"", "lost")
OK = (0, b"", b"")


def test_parse_duration():
    assert ctl.parse_duration_s("90s") == 90
    assert ctl.parse_duration_s("2m") == 120
    assert ctl.parse_duration_s("1h") == 3600
    assert ctl.parse_duration_s("5") == 300
    for bad in ("", "abc", "1d", "-5", "1.5h", "5 m"):
        with pytest.raises(ValueError):
            ctl.parse_duration_s(bad)


def test_guard_cmd_quoting_roundtrips():
    reason = "it's a \"test\" $HOME; rm -rf /"
    cmd = ctl.guard_cmd("keep", "30m", "--reason", reason)
    assert shlex.split(cmd) == ["bash", ctl.GUARD_PATH, "keep", "30m", "--reason", reason]


def test_parse_status_with_jobs():
    text = "\n".join([
        "version=0.1.0", "now=1000", "mode=gpu", "deadline=4600", "keep_until=1600",
        "busy_now=job:train ", "daemon_alive=1",
        "job.train=running|990||/root/autodl-tmp/.autodl-guard/jobs/train/log",
        "job.prep=done:0|900|950|/root/logs/prep.log",
    ])
    st = ctl.parse_status(text)
    assert st["mode"] == "gpu" and st["daemon_alive"] == "1"
    assert st["jobs"][0] == {"name": "train", "state": "running", "start": "990", "end": "",
                             "log": "/root/autodl-tmp/.autodl-guard/jobs/train/log"}
    assert st["jobs"][1]["state"] == "done:0"


# ---- behaviour of the SSH-backed commands ----
def test_status_unreachable(monkeypatch, capsys):
    use(monkeypatch, {}, default=(255, b"", b"ssh: connect to host refused"))
    assert ctl.main(["status", "autodl-test"]) == ctl.EXIT_UNREACHABLE
    assert json.loads(capsys.readouterr().out)["reachable"] is False


def test_status_reports_times_and_mode(monkeypatch, capsys):
    status = b"now=1000\ndeadline_at=4600\ndeadline_in_s=3600\nkeep_in_s=600\nheartbeat=990\ndaemon_alive=1\n"
    # order matters for substring matching: keep "true" last
    use(monkeypatch, {"nvidia-smi": (0, b"gpu 1\n", b""),
                      "autodl_guard.sh status": (0, status, b""),
                      "true": (0, b"", b"")})
    assert ctl.main(["status", "autodl-test"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["detected_mode"] == "gpu" and res["gpus"] == 1
    assert res["deadline_in_s"] == "3600" and res["keep_in_s"] == "600"   # the 0.8 guard's own values, as given
    assert res["heartbeat_age_s"] == 10


BOOT_STATUS = b"now=1790000000\nup=20000000\nboot=1999940000\ndaemon_alive=1\n"   # PID 1 began 19999400 s after the kernel


def test_status_tells_when_this_boot_began(monkeypatch, capsys):
    fake = use(monkeypatch, {"nvidia-smi": (0, b"gpu 1\n", b""),
                             "autodl_guard.sh status": (0, BOOT_STATUS + b"clk_tck=100\n", b""), "true": (0, b"", b"")})
    monkeypatch.setattr(ctl, "now_s", lambda: 1790000300)     # this machine's clock is five minutes ahead of the instance's
    assert ctl.main(["status", "autodl-test"]) == 0
    res = json.loads(capsys.readouterr().out)
    # the container has been up for ten minutes: counted back on this machine's clock, the clock of T0 and of the ledger
    assert res["booted_at"] == 1790000300 - 600
    use(monkeypatch, {"nvidia-smi": (0, b"gpu 1\n", b""), "true": (0, b"", b""),      # the same boot on a kernel with another tick
                      "autodl_guard.sh status": (0, b"now=1790000000\nup=20000000\nboot=4999850000\nclk_tck=250\n", b"")})
    assert ctl.main(["status", "autodl-test"]) == 0
    assert json.loads(capsys.readouterr().out)["booted_at"] == 1790000300 - 600
    sent = [argv[-1] for argv, _ in fake.calls if "autodl_guard.sh status" in argv[-1]]
    assert sent and all(s.endswith('status && echo "clk_tck=$(getconf CLK_TCK 2>/dev/null)"') for s in sent), sent


@pytest.mark.parametrize("status", [
    BOOT_STATUS,                                                                      # the clock tick was not told
    b"now=1790000000\nup=20000000\nboot=unknown\nclk_tck=100\n",                      # PID 1 could not be read
    b"now=1790000000\nup=\nboot=1999940000\nclk_tck=100\n",
    b"now=1790000000\nup=100\nboot=1999940000\nclk_tck=100\n",                        # it would have begun after now
    BOOT_STATUS + b"clk_tck=0\n", BOOT_STATUS + b"clk_tck=\n",
    BOOT_STATUS + b"clk_tck=10\n",                                                    # PID 1 would have begun after the kernel's uptime
], ids=["no tick", "no boot", "no uptime", "after now", "tick 0", "tick empty", "a tick that puts the start after now"])
def test_status_gives_no_boot_time_it_cannot_work_out(monkeypatch, capsys, status):
    use(monkeypatch, {"nvidia-smi": (0, b"gpu 1\n", b""), "autodl_guard.sh status": (0, status, b""), "true": (0, b"", b"")})
    assert ctl.main(["status", "autodl-test"]) == 0
    assert json.loads(capsys.readouterr().out)["booted_at"] is None


def test_a_status_without_the_guard_stays_a_failure(monkeypatch, capsys):
    use(monkeypatch, {"nvidia-smi": (0, b"gpu 1\n", b""),
                      "autodl_guard.sh status": (127, b"", b"bash: /root/autodl-tmp/.autodl-guard/autodl_guard.sh: No such file\n"),
                      "true": (0, b"", b"")})
    assert ctl.main(["status", "autodl-test"]) == ctl.EXIT_ERR   # a failed status stays one (the && before the echo is pinned above)
    res = json.loads(capsys.readouterr().out)
    assert res["guard"] == "not deployed or failed" and "booted_at" not in res


def test_deploy_refuses_crlf(tmp_path, monkeypatch):
    f = tmp_path / "autodl_guard.sh"
    f.write_bytes(b"#!/usr/bin/env bash\r\necho hi\r\n")
    monkeypatch.setattr(ctl, "LOCAL_GUARD", f)
    fake = use(monkeypatch, {})
    assert ctl.main(["deploy", "autodl-test"]) == ctl.EXIT_ERR
    assert fake.calls == []


def test_deploy_verifies_hash(tmp_path, monkeypatch):
    data = b"#!/usr/bin/env bash\necho hi\n"
    f = tmp_path / "autodl_guard.sh"
    f.write_bytes(data)
    monkeypatch.setattr(ctl, "LOCAL_GUARD", f)
    good = hashlib.sha256(data).hexdigest().encode() + b"  /root/autodl-tmp/.autodl-guard/autodl_guard.sh\n"
    fake = use(monkeypatch, {"sha256sum": (0, good, b"")})
    assert ctl.main(["deploy", "autodl-test", "--no-autostart"]) == 0   # the hook has tests of its own
    assert fake.calls[0][1] == data
    use(monkeypatch, {"sha256sum": (0, b"deadbeef  x\n", b"")})
    assert ctl.main(["deploy", "autodl-test"]) == ctl.EXIT_ERR


def test_run_sends_command_on_stdin(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh run": (0, b"started job train\n", b"")})
    assert ctl.main(["run", "autodl-test", "train", "--cmd", "python train.py --lr 1e-3", "--then-off"]) == 0
    argv, stdin = fake.calls[0]
    assert stdin == b"python train.py --lr 1e-3"
    assert "--cmd-stdin" in argv[-1] and "--then-off" in argv[-1]


def test_off_now_refused_passes_exit_3(monkeypatch):
    use(monkeypatch, {"off-now": (3, b"refused: still in use\njob:train\n", b"")})
    assert ctl.main(["off-now", "autodl-test", "--reason", "stage done"]) == ctl.EXIT_REFUSED


def test_off_now_dry_run_does_not_wait(monkeypatch):
    fake = use(monkeypatch, {"off-now": (0, b"dry-run: shutdown not executed (off-now (x))\n", b"")})
    assert ctl.main(["off-now", "autodl-test", "--reason", "x"]) == 0
    assert len(fake.calls) == 1


def test_keep_rejects_bad_duration_locally(monkeypatch):
    fake = use(monkeypatch, {})
    assert ctl.main(["keep", "autodl-test", "soon", "--reason", "x"]) == ctl.EXIT_ERR
    assert fake.calls == []


# ---- tar extraction safety, power log and usage ----
def _tar(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf


def test_extract_stream_ok(tmp_path):
    ctl.extract_stream(_tar([("exp1/metrics.json", b"{}")]), tmp_path)
    assert (tmp_path / "exp1" / "metrics.json").read_bytes() == b"{}"


def test_extract_stream_rejects_path_traversal(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(tarfile.TarError):
        ctl.extract_stream(_tar([("../evil.txt", b"x")]), dest)
    assert not (tmp_path / "evil.txt").exists()


def test_log_and_usage(tmp_path, capsys):
    base = 1_790_000_000
    events = [("on", base, ["mode=gpu", "price=2.0", "gpus=1"]), ("off", base + 5400, ["reason=done"]),
              ("on", base + 6000, ["mode=nogpu", "price=0.1"]), ("off", base + 7800, [])]
    for ev, at, fields in events:
        args = ["log", ev, "--instance", "demo", "--project", str(tmp_path), "--at", str(at)]
        for f in fields:
            args += ["--field", f]
        assert ctl.main(args) == 0
    capsys.readouterr()
    lines = (tmp_path / ".autodl" / "power_log.jsonl").read_text(encoding="utf-8").splitlines()
    recs = [json.loads(x) for x in lines]
    assert len(recs) == 4 and recs[0]["mode"] == "gpu"
    now = dt.datetime.fromtimestamp(base + 7800 + 2 * 86400).astimezone()
    s = ctl.summarize(recs, now)["demo"]
    assert s["gpu_hours"] == 1.5 and s["nogpu_hours"] == 0.5
    assert s["est_cost_yuan"] == 3.05
    assert s["days_off"] == 2.0 and s["release_in_days_est"] == 13.0


def test_log_rejects_malformed_field(tmp_path):
    args = ["log", "note", "--instance", "demo", "--project", str(tmp_path), "--field", "novalue"]
    assert ctl.main(args) == ctl.EXIT_ERR


# ---- fixes from the first Codex review ----
def test_alias_that_looks_like_an_option_is_rejected(monkeypatch):
    fake = use(monkeypatch, {})
    with pytest.raises(ValueError):
        ctl.ssh_argv("-oProxyCommand=calc", "true")
    # argparse already refuses it as an option; past "--" the alias check still refuses it
    assert ctl.main(["status", "--", "-oProxyCommand=calc"]) == ctl.EXIT_ERR
    assert fake.calls == []


def test_ssh_argv_ends_options_before_the_alias(monkeypatch):
    fake = use(monkeypatch, {"true": (0, b"", b"")}, default=(1, b"", b""))
    ctl.main(["status", "autodl-test"])
    argv = fake.calls[0][0]
    assert argv[argv.index("autodl-test") - 1] == "--"


@pytest.mark.parametrize("probe_out, expected", [
    (b"gpu 2\n", ("gpu", 2)),
    (b"nogpu-check 2147483648\n", ("nogpu", 0)),
    (b"nogpu-check max\n", ("unknown", 0)),
    (b"nogpu-check 128849018880\n", ("unknown", 0)),
    (b"", ("unknown", 0)),
])
def test_detect_mode_is_tristate(monkeypatch, probe_out, expected):
    use(monkeypatch, {"nvidia-smi": (0, probe_out, b"")})
    assert ctl.detect_mode("autodl-test") == expected


def test_wait_down_needs_five_failures_in_a_row(monkeypatch, capsys):
    fail, ok = (255, b"", b"timeout"), (0, b"", b"")
    fake = use(monkeypatch, {"true": [fail, ok, fail, fail, fail, fail, fail]})
    assert ctl.main(["wait", "autodl-test", "--state", "down", "--every", "0"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["state"] == "unreachable" and "console" in res["note"]
    assert len(fake.calls) == 7


def test_wait_up_refuses_unknown_mode(monkeypatch, capsys):
    use(monkeypatch, {"nvidia-smi": (0, b"nogpu-check max\n", b""), "true": (0, b"", b"")})
    assert ctl.main(["wait", "autodl-test", "--every", "0"]) == ctl.EXIT_ERR
    assert json.loads(capsys.readouterr().out)["mode"] == "unknown"


def test_usage_counts_the_running_session_and_gpu_count():
    t0 = dt.datetime.fromtimestamp(1_790_000_000).astimezone()
    recs = [{"t": t0.isoformat(), "event": "on", "instance": "demo", "mode": "gpu", "price": "2.0", "gpus": "2"}]
    s = ctl.summarize(recs, t0 + dt.timedelta(hours=1))["demo"]
    assert s["gpu_hours"] == 2.0 and s["est_cost_yuan"] == 2.0
    assert s["includes_running_time"] is True and s["running_mode"] == "gpu"


def test_usage_since_keeps_only_later_sessions():
    base = dt.datetime.fromtimestamp(1_790_000_000).astimezone()
    recs = []
    for start in (0, 7200):
        recs.append({"t": (base + dt.timedelta(seconds=start)).isoformat(), "event": "on",
                     "instance": "demo", "mode": "gpu", "price": "1"})
        recs.append({"t": (base + dt.timedelta(seconds=start + 3600)).isoformat(), "event": "off",
                     "instance": "demo"})
    s = ctl.summarize(recs, base + dt.timedelta(hours=5), since=base + dt.timedelta(seconds=5000))["demo"]
    assert s["gpu_hours"] == 1.0


def test_usage_pairs_records_in_time_order():
    base = dt.datetime.fromtimestamp(1_790_000_000).astimezone()
    later = base + dt.timedelta(hours=5)
    gpu = {"mode": "gpu", "price": "2.0"}

    def rec(event, sec, **kw):
        return {"t": (base + dt.timedelta(seconds=sec)).isoformat(), "event": event, "instance": "demo", **kw}

    # a power-off written late, after the next power-on: paired by time, not by place in the file
    s = ctl.summarize([rec("on", 0, **gpu), rec("on", 7200, **gpu), rec("off", 3600), rec("off", 9000)], later)["demo"]
    assert s["gpu_hours"] == 1.5 and "closed_by_next_on" not in s and "includes_running_time" not in s
    # on, then off, in the same second: 0 seconds, and nothing is left running
    s = ctl.summarize([rec("on", 0, **gpu), rec("off", 0)], later)["demo"]
    assert s["gpu_hours"] == 0 and "includes_running_time" not in s
    # off, then on, in the same second (a change of mode): the off ends the old session, the on starts the new one
    recs = [rec("on", 0, **gpu), rec("off", 3600), rec("on", 3600, mode="nogpu", price="0.1"), rec("off", 5400)]
    s = ctl.summarize(recs, later)["demo"]
    assert s["gpu_hours"] == 1.0 and s["nogpu_hours"] == 0.5 and "closed_by_next_on" not in s
    # two power-ons with no power-off between: the first counts until the second, and is named in the result
    recs = [rec("on", 0, **gpu), rec("on", 1800, **gpu), rec("off", 3600)]
    s = ctl.summarize(recs, later)["demo"]
    assert s["gpu_hours"] == 1.0 and s["closed_by_next_on"] == [recs[0]["t"]]
    # a time written without an offset is local time, and sorts among the others
    naive = (base + dt.timedelta(seconds=1800)).replace(tzinfo=None).isoformat()
    recs = [rec("on", 0, **gpu), rec("off", 3600), {"t": naive, "event": "off", "instance": "demo"}]
    assert ctl.summarize(recs, later)["demo"]["gpu_hours"] == 0.5


def test_pull_remote_cmd_ends_tar_options():
    base, cmd = ctl.pull_remote_cmd("/root/autodl-tmp/out/-weird")
    assert base == "-weird" and " -- " in cmd and shlex.split(cmd)[-1] == "-weird"
    for bad in ("/root/.", "/root/..", "/", ""):
        with pytest.raises(ValueError):
            ctl.pull_remote_cmd(bad)


def test_pull_rejects_an_unsafe_archive(tmp_path, monkeypatch):
    dest = tmp_path / "dest"
    dest.mkdir()
    stream = _tar([("exp1/a.txt", b"a"), ("../evil.txt", b"x")]).getvalue()
    popen_script(monkeypatch, [(0, stream, b"")])
    assert ctl.main(["pull", "autodl-test", "/root/autodl-tmp/exp1", str(dest)]) == ctl.EXIT_ERR
    assert not (tmp_path / "evil.txt").exists() and list(dest.iterdir()) == []


def _staged(root, content):
    staged = root / f".stage-{content}" / "exp1"
    staged.mkdir(parents=True)
    (staged / "m.txt").write_text(content)
    return staged


def test_commit_staged_keeps_every_old_copy_under_a_unique_name(tmp_path):
    for content in ("v1", "v2", "v3"):
        ctl.commit_staged(_staged(tmp_path, content), tmp_path / "exp1", overwrite=True)
    assert (tmp_path / "exp1" / "m.txt").read_text() == "v3"
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("exp1.bak-")]
    assert sorted((b / "m.txt").read_text() for b in backups) == ["v1", "v2"]


def test_commit_staged_needs_overwrite_for_an_existing_target(tmp_path):
    ctl.commit_staged(_staged(tmp_path, "old"), tmp_path / "exp1", overwrite=False)
    with pytest.raises(FileExistsError):
        ctl.commit_staged(_staged(tmp_path, "new"), tmp_path / "exp1", overwrite=False)
    assert (tmp_path / "exp1" / "m.txt").read_text() == "old"


def test_commit_staged_restores_the_old_copy_when_the_move_fails(tmp_path, monkeypatch):
    ctl.commit_staged(_staged(tmp_path, "old"), tmp_path / "exp1", overwrite=False)
    staged = _staged(tmp_path, "new")
    real_rename, calls = os.rename, []

    def flaky(src, dst):
        calls.append((src, dst))
        if len(calls) == 2:
            raise OSError("simulated failure while moving the new copy in")
        return real_rename(src, dst)
    monkeypatch.setattr(ctl.os, "rename", flaky)
    with pytest.raises(OSError):
        ctl.commit_staged(staged, tmp_path / "exp1", overwrite=True)
    assert (tmp_path / "exp1" / "m.txt").read_text() == "old"
    assert not [p for p in tmp_path.iterdir() if ".bak-" in p.name]


def test_check_reports_the_resolved_config_only_when_asked(monkeypatch, capsys):
    resolved = b"hostname connect.example.com\nport 40022\nuser root\nidentityfile ~/.ssh/id_demo\n"
    table = {"-V": (0, b"", b"OpenSSH_10.2p1\n"), "autodl-test": (0, resolved, b""), "true": (0, b"", b"")}
    fake = use(monkeypatch, table)
    assert ctl.main(["check", "autodl-test"]) == 0
    said = capsys.readouterr().out
    res = json.loads(said)
    assert res["reachable"] is True and res["ssh"] == FAKE_SSH and "config" not in res
    assert "40022" not in said and "id_demo" not in said and "connect.example.com" not in said   # nothing of how it connects
    assert not any("-G" in argv for argv, _ in fake.calls)                                      # nor was it asked for
    assert ctl.main(["check", "autodl-test", "--config"]) == 0                                    # to find out why an alias fails
    res = json.loads(capsys.readouterr().out)
    assert res["config"] == {"hostname": "connect.example.com", "port": "40022", "user": "root",
                             "identityfile": "~/.ssh/id_demo"}


INSTANCE = "abcd123456-1234abcd"
OTHER = "ffff000000-0000ffff"   # another instance of the same account


def _check_table(hostname: bytes) -> dict:
    return {"-V": (0, b"", b"OpenSSH_10.2p1\n"), "autodl-test": (0, b"hostname connect.example.com\nport 40022\n", b""),
            "true": OK, "uname -n": (0, hostname, b"")}


def _record() -> Path:
    h = os.environ.get("AUTODL_AUTOGPU_HOME")   # a local: a failing lookup must not print the environment
    assert h
    return Path(h)


def test_check_instance_records_the_alias(monkeypatch, capsys, clock):
    clock.set(1_790_000_000)
    use(monkeypatch, _check_table(f"autodl-container-{INSTANCE}\n".encode()))
    assert ctl.main(["check", "autodl-test", "--instance", INSTANCE]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["instance_match"] is True and res["recorded"] is True
    with ctl.Store() as st:
        assert st.data["aliases"]["autodl-test"] == {"instance": INSTANCE, "at": 1_790_000_000}


def test_check_instance_mismatch_is_exit_13(monkeypatch, capsys):
    use(monkeypatch, _check_table(b"autodl-container-ffff000000-0000ffff\n"))
    assert ctl.main(["check", "autodl-test", "--instance", INSTANCE]) == ctl.EXIT_MISMATCH
    res = json.loads(capsys.readouterr().out)
    assert res["instance_match"] is False and res["hostname"] == "autodl-container-ffff000000-0000ffff"
    assert not _record().exists()   # nothing recorded; the record is not even made


def test_check_instance_needs_a_well_formed_id_and_a_hostname(monkeypatch, capsys):
    fake = use(monkeypatch, _check_table(b""))
    assert ctl.main(["check", "autodl-test", "--instance", "not-an-id"]) == ctl.EXIT_ERR
    assert fake.calls == []
    use(monkeypatch, {**_check_table(b""), "uname -n": DROP})
    assert ctl.main(["check", "autodl-test", "--instance", INSTANCE]) == ctl.EXIT_UNREACHABLE
    use(monkeypatch, {**_check_table(b""), "uname -n": LOST})
    assert ctl.main(["check", "autodl-test", "--instance", INSTANCE]) == ctl.EXIT_UNCERTAIN
    assert not _record().exists()


def test_check_without_instance_is_unchanged(monkeypatch, capsys):
    fake = use(monkeypatch, _check_table(f"autodl-container-{INSTANCE}\n".encode()))
    assert ctl.main(["check", "autodl-test"]) == 0
    assert "instance_match" not in json.loads(capsys.readouterr().out)
    assert not any("uname" in c[0][-1] for c in fake.calls) and not _record().exists()


def test_check_instance_with_a_broken_store_is_11(monkeypatch, capsys):
    with ctl.Store():
        pass
    (_record() / "store.json").write_bytes(b"not json")
    use(monkeypatch, _check_table(f"autodl-container-{INSTANCE}\n".encode()))
    assert ctl.main(["check", "autodl-test", "--instance", INSTANCE]) == ctl.EXIT_STORE
    res = json.loads(capsys.readouterr().out)
    assert res["instance_match"] is True and res["recorded"] is False and "store.json" in res["store_error"]


def test_push_reports_an_existing_remote_target(tmp_path, monkeypatch, capsys):
    (tmp_path / "data").mkdir()
    popen_script(monkeypatch, [(3, b"", b"push: /root/autodl-tmp/data already exists\n")])
    assert ctl.main(["push", "autodl-test", str(tmp_path / "data"), "/root/autodl-tmp"]) == ctl.EXIT_ERR
    assert "--overwrite" in capsys.readouterr().err


def test_push_sends_a_tar_to_a_temporary_directory_first(tmp_path, monkeypatch, capsys):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.txt").write_text("hello")
    procs = popen_script(monkeypatch, [(0, b"pushed=/root/autodl-tmp/data\n", b"")])
    assert ctl.main(["push", "autodl-test", str(tmp_path / "data"), "/root/autodl-tmp"]) == 0
    remote = procs[0].args[-1]
    assert "mktemp -d" in remote and "trap" in remote
    with tarfile.open(fileobj=io.BytesIO(procs[0].stdin.data)) as tf:
        assert tf.extractfile("data/a.txt").read() == b"hello"
    assert json.loads(capsys.readouterr().out)["to"] == "/root/autodl-tmp/data"


def test_push_retries_when_the_connection_never_started(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    procs = popen_script(monkeypatch, [DROP, (0, b"pushed=/root/autodl-tmp/data\n", b"")])
    assert ctl.main(["push", "autodl-test", str(tmp_path / "data"), "/root/autodl-tmp"]) == 0
    assert len(procs) == 2


def test_push_normalizes_modes(tmp_path, monkeypatch):
    src = tmp_path / "data"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("a")
    (src / "tool.bat").write_text("@echo off\n")   # Windows reports its own execute bits for .bat; they are not sent
    (src / "sub" / "run.sh").write_text("#!/bin/sh\n")
    if os.name != "nt":
        os.chmod(src / "sub", 0o777)
        os.chmod(src / "a.txt", 0o666)
        os.chmod(src / "sub" / "run.sh", 0o775)
    procs = popen_script(monkeypatch, [(0, b"pushed=/root/autodl-tmp/data\n", b"")])
    assert ctl.main(["push", "autodl-test", str(src), "/root/autodl-tmp"]) == 0
    with tarfile.open(fileobj=io.BytesIO(procs[0].stdin.data)) as tf:
        got = {m.name: m for m in tf.getmembers()}
    assert set(got) == {"data", "data/sub", "data/a.txt", "data/tool.bat", "data/sub/run.sh"}
    for m in got.values():
        assert (m.uid, m.gid, m.uname, m.gname) == (0, 0, "", ""), m.name
    assert (got["data"].mode, got["data/sub"].mode, got["data/a.txt"].mode, got["data/tool.bat"].mode) == \
        (0o755, 0o755, 0o644, 0o644)
    assert got["data/sub/run.sh"].mode == (0o644 if os.name == "nt" else 0o755)   # executable only where it is
    ti = tarfile.TarInfo("x")   # on Windows tarfile leaves owner names empty anyway: check the filter itself
    ti.uid, ti.gid, ti.uname, ti.gname = 1000, 100, "someone", "users"
    ti = ctl.push_filter(ti)
    assert (ti.uid, ti.gid, ti.uname, ti.gname) == (0, 0, "", "")


def test_tail_asks_the_guard_for_the_job_log(monkeypatch):
    fake = use(monkeypatch, {"logtail": (0, b"epoch 3 loss 0.1\n", b"")})
    assert ctl.main(["tail", "autodl-test", "train", "-n", "20"]) == 0
    assert shlex.split(fake.calls[0][0][-1])[-3:] == ["logtail", "train", "20"]


def test_guard_exit_codes_pass_through(monkeypatch):
    use(monkeypatch, {"autodl_guard.sh keep": (4, b"", b"error: a shutdown is pending\n"),
                      "autodl_guard.sh arm": (5, b"", b"error: already armed\n")})
    assert ctl.main(["keep", "autodl-test", "10m", "--reason", "x"]) == ctl.EXIT_PENDING
    assert ctl.main(["arm", "autodl-test", "--idle", "15m"]) == ctl.EXIT_ARMED


# ---- fixes before the third Codex review: sshd MaxStartups drops, limits, paths ----
def test_remote_commands_start_with_the_marker(monkeypatch):
    fake = use(monkeypatch, {"true": OK})
    ctl.ssh_run("autodl-test", "true")
    assert fake.calls[0][0][-1] == ctl.MARKER_PREFIX + "true"


def test_ssh_run_retries_a_command_that_never_started(monkeypatch):
    fake = use(monkeypatch, {"true": [DROP, DROP, OK]})
    r = ctl.ssh_run("autodl-test", "true")
    assert r.started and r.rc == 0 and r.attempts == 3 and len(fake.calls) == 3


def test_ssh_run_gives_up_after_three_attempts(monkeypatch):
    fake = use(monkeypatch, {"true": DROP})
    r = ctl.ssh_run("autodl-test", "true")
    assert not r.started and r.rc == 255 and len(fake.calls) == 3


def test_ssh_run_never_repeats_a_command_that_started(monkeypatch):
    fake = use(monkeypatch, {"keep": (255, b"", b"", True)})
    r = ctl.ssh_run("autodl-test", "keep")
    assert r.started and r.rc == 255 and len(fake.calls) == 1


def test_ssh_run_notes_extra_attempts_on_stderr(monkeypatch, capsys):
    use(monkeypatch, {"true": [DROP, OK]})
    ctl.ssh_run("autodl-test", "true")
    assert "2 attempts" in capsys.readouterr().err
    use(monkeypatch, {"true": OK})
    ctl.ssh_run("autodl-test", "true")
    assert capsys.readouterr().err == ""


def test_ssh_run_hides_the_marker(monkeypatch):
    use(monkeypatch, {"x": (0, b"out\n", b"warn\n")})
    r = ctl.ssh_run("autodl-test", "x")
    assert (r.stdout, r.stderr) == (b"out\n", b"warn\n")


def test_status_survives_dropped_connections(monkeypatch, capsys):
    status = b"now=1000\ndeadline=4600\nkeep_until=1600\nheartbeat=990\ndaemon_alive=1\n"
    use(monkeypatch, {"nvidia-smi": [DROP, (0, b"gpu 1\n", b"")],
                      "autodl_guard.sh status": [DROP, (0, status, b"")],
                      "true": [DROP, DROP, OK]})
    assert ctl.main(["status", "autodl-test"]) == 0
    assert json.loads(capsys.readouterr().out)["detected_mode"] == "gpu"


def test_off_now_is_sent_again_when_it_never_arrived(monkeypatch, capsys):
    fake = use(monkeypatch, {"off-now": [DROP, (0, b"shutdown issued (off-now (x))\n", b"")], "true": DROP})
    assert ctl.main(["off-now", "autodl-test", "--reason", "x"]) == 0
    assert len([c for c in fake.calls if "off-now" in c[0][-1]]) == 2
    assert json.loads(capsys.readouterr().out)["state"] == "unreachable"


def test_off_now_that_never_arrived_is_not_reported_as_off(monkeypatch, capsys):
    fake = use(monkeypatch, {"off-now": DROP, "true": DROP})
    assert ctl.main(["off-now", "autodl-test", "--reason", "x"]) == ctl.EXIT_UNREACHABLE
    assert json.loads(capsys.readouterr().out)["state"] == "not sent"
    assert all("off-now" in c[0][-1] for c in fake.calls)


def test_off_now_waits_for_five_failures_in_a_row(monkeypatch):
    probes = [DROP, DROP, DROP, OK, DROP, DROP, DROP, DROP, DROP]
    fake = use(monkeypatch, {"off-now": (0, b"shutdown issued (off-now (x))\n", b""), "true": probes})
    assert ctl.main(["off-now", "autodl-test", "--reason", "x"]) == 0
    assert len([c for c in fake.calls if c[0][-1].endswith("; true")]) == 9


def test_off_raw_runs_the_official_command_and_waits_until_down(monkeypatch, capsys):
    fake = use(monkeypatch, {"/usr/bin/shutdown": (255, b"", b"", True), "true": DROP})
    assert ctl.main(["off-raw", "autodl-test", "--reason", "wait failed after power-on"]) == 0
    assert fake.calls[0][0][-1].startswith(ctl.MARKER_PREFIX) and "screen -ls" in fake.calls[0][0][-1]
    res = json.loads(capsys.readouterr().out)
    assert res["state"] == "unreachable" and res["confirmed"] is False


def test_off_raw_that_never_arrived_is_not_reported_as_off(monkeypatch, capsys):
    use(monkeypatch, {"/usr/bin/shutdown": DROP, "true": DROP})
    assert ctl.main(["off-raw", "autodl-test", "--reason", "x"]) == ctl.EXIT_UNREACHABLE
    assert json.loads(capsys.readouterr().out)["state"] == "not sent"


def test_off_raw_refuses_while_something_runs(monkeypatch, capsys):
    use(monkeypatch, {"/usr/bin/shutdown": (3, b"refused: in use: screen\n", b"")})
    assert ctl.main(["off-raw", "autodl-test", "--reason", "x"]) == ctl.EXIT_REFUSED
    assert "screen" in capsys.readouterr().out


def test_off_raw_force_skips_the_checks(monkeypatch):
    fake = use(monkeypatch, {"/usr/bin/shutdown": (255, b"", b"", True), "true": DROP})
    assert ctl.main(["off-raw", "autodl-test", "--reason", "user agreed", "--force"]) == 0
    cmd, stdin = fake.calls[0][0][-1], fake.calls[0][1]
    assert "screen -ls" not in cmd and "idle-check" not in cmd and stdin is None   # no check, no sample, no script sent


# after the review of the whole release (finding 5): screen, tmux and registered jobs do not show work started in
# Jupyter or over a plain ssh, so off-raw also takes the guard's live sample, in the same remote shell
def test_off_raw_takes_a_live_sample_in_the_same_remote_shell_before_it_shuts_down(monkeypatch, capsys):
    fake = use(monkeypatch, {"/usr/bin/shutdown": (255, b"", b"", True), "true": DROP})
    assert ctl.main(["off-raw", "autodl-test", "--reason", "the guard does not start"]) == 0
    cmd, stdin = fake.calls[0][0][-1], fake.calls[0][1]
    assert cmd == ctl.MARKER_PREFIX + ctl.off_raw_script()
    # the three checks, then the sample, then the shutdown; the guard script travels on stdin and is read whole first
    assert cmd.index("screen -ls") < cmd.index("tmux ls") < cmd.index("idle-check") < cmd.rindex("/usr/bin/shutdown")
    assert cmd.startswith(ctl.MARKER_PREFIX + 'G="$(cat)"; ') and cmd.endswith("/usr/bin/shutdown")
    assert 'printf "%s\\n" "$G" | bash -s -- idle-check --sample 5 ' in cmd and stdin == ctl.LOCAL_GUARD.read_bytes()
    after = cmd.split("idle-check")[1]   # only the sample's own "idle: ..." with exit 0 goes on; anything else ends it
    assert 'case "$rc:$s" in "0:idle: "*) ;; *)' in after and after.index("exit 3") < after.rindex("/usr/bin/shutdown")


def test_off_raw_refused_by_the_live_sample_says_which_signal(monkeypatch, capsys):
    said = (b"refused: in use or cannot tell (the live sample; --force only with the user's consent):\n"
            b"in use or cannot tell (a 5s live sample: gpu=na: cpu=busy:41.0 io=idle:0 net=idle:0)\ncpu:busy\n")
    fake = use(monkeypatch, {"/usr/bin/shutdown": (3, said, b"")})
    assert ctl.main(["off-raw", "autodl-test", "--reason", "x"]) == ctl.EXIT_REFUSED
    assert "cpu:busy" in capsys.readouterr().out and len(fake.calls) == 1   # refused: no waiting for it to go down


def test_off_raw_sends_no_guard_script_with_cr_bytes(tmp_path, monkeypatch, capsys):
    f = tmp_path / "autodl_guard.sh"
    f.write_bytes(b"#!/usr/bin/env bash\r\necho hi\r\n")
    monkeypatch.setattr(ctl, "LOCAL_GUARD", f)
    fake = use(monkeypatch, {})
    assert ctl.main(["off-raw", "autodl-test", "--reason", "x", "--wait", "0s"]) == ctl.EXIT_ERR and fake.calls == []
    assert "CR" in capsys.readouterr().err
    ctl.main(["off-raw", "autodl-test", "--reason", "x", "--force", "--wait", "0s"])
    assert fake.calls and fake.calls[0][1] is None   # forced: nothing is sampled, so no script is needed


def test_parse_duration_is_capped_at_30_days():
    assert ctl.parse_duration_s("43200m") == ctl.parse_duration_s("720h") == 2592000
    for bad in ("43201m", "721h", "2592001s", "12345678", "99999999999999999999h"):
        with pytest.raises(ValueError):
            ctl.parse_duration_s(bad)


def test_run_retries_with_the_same_request_id(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh run": [DROP, (0, b"started job train\n", b"")]})
    assert ctl.main(["run", "autodl-test", "train", "--cmd", "python train.py"]) == 0
    reqs = [re.search(r"--req (\S+)", argv[-1]).group(1) for argv, _ in fake.calls]
    assert len(reqs) == 2 and reqs[0] == reqs[1] and len(reqs[0]) >= 8
    assert fake.calls[0][1] == fake.calls[1][1] == b"python train.py"


def test_old_guard_gets_a_deploy_hint(monkeypatch, capsys):
    use(monkeypatch, {"autodl_guard.sh run": (1, b"", b"error: run: unknown option '--req'\n")})
    assert ctl.main(["run", "autodl-test", "j", "--cmd", "true"]) == ctl.EXIT_ERR
    assert "deploy" in capsys.readouterr().err


def test_tail_guard_shows_the_guard_log(monkeypatch):
    fake = use(monkeypatch, {"logtail": (0, b"2026-09-28 ARM ...\n", b"")})
    assert ctl.main(["tail", "autodl-test", "guard"]) == 0
    assert shlex.split(fake.calls[0][0][-1])[-3:] == ["logtail", "guard", "50"]


def test_now_prints_unix_seconds(capsys):
    before = int(time.time())
    assert ctl.main(["now"]) == 0
    assert before <= int(capsys.readouterr().out.strip()) <= int(time.time())


def test_pull_keeps_the_local_copy_when_the_remote_side_fails(tmp_path, monkeypatch):
    (tmp_path / "exp1").mkdir()
    (tmp_path / "exp1" / "m.json").write_text("keep me")
    stream = _tar([("exp1/m.json", b"partial")]).getvalue()   # a valid archive, but tar exits 2
    popen_script(monkeypatch, [(2, stream, b"tar: exp1/big.bin: Cannot open: Permission denied\n")])
    assert ctl.main(["pull", "autodl-test", "/root/autodl-tmp/exp1", str(tmp_path), "--overwrite"]) == ctl.EXIT_ERR
    assert (tmp_path / "exp1" / "m.json").read_text() == "keep me"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["exp1"]


def test_pull_retries_when_the_connection_never_started(tmp_path, monkeypatch):
    stream = _tar([("exp1/m.json", b"{}")]).getvalue()
    procs = popen_script(monkeypatch, [DROP, (0, stream, b"")])
    assert ctl.main(["pull", "autodl-test", "/root/autodl-tmp/exp1", str(tmp_path)]) == 0
    assert (tmp_path / "exp1" / "m.json").read_text() == "{}" and len(procs) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["exp1"]


@pytest.mark.parametrize("args", [
    ["pull", "autodl-test", "C:/Program Files/Git/root/autodl-tmp/out", "."],
    ["push", "autodl-test", ".", "C:/Program Files/Git/root/autodl-tmp"],
    ["run", "autodl-test", "j", "--cmd", "C:/Program Files/Git/root/run.sh"],
    ["run", "autodl-test", "j", "--cmd", "true", "--log", "C:/Program Files/Git/root/x.log"],
    ["arm", "autodl-test", "--idle", "15m", "--env-setup", "C:/Program Files/Git/root/env.sh"],
])
def test_windows_paths_meant_for_the_instance_are_refused(monkeypatch, capsys, args):
    fake = use(monkeypatch, {})
    procs = popen_script(monkeypatch, [OK])
    assert ctl.main(args) == ctl.EXIT_ERR
    assert fake.calls == [] and procs == []
    assert "scripts/ctl" in capsys.readouterr().err


@pytest.mark.parametrize("given, expected", [
    ("/c/Users/x/data", "C:/Users/x/data"), ("/d/", "D:/"), ("/e", "E:/"),
    ("relative/dir", "relative/dir"), ("/root/x", "/root/x"), ("C:/Users/x", "C:/Users/x"),
])
def test_git_bash_drive_paths_are_normalized_on_windows(given, expected):
    assert ctl.normalize_local(given, windows=True) == expected


def test_local_paths_elsewhere_only_expand_the_home_directory():
    assert ctl.normalize_local("/c/Users/x", windows=False) == "/c/Users/x"
    assert ctl.normalize_local("~/x", windows=False) == os.path.expanduser("~/x")


GIT_BASH = local_tools.git_bash()   # <Git>/usr/bin/bash.exe wherever Git for Windows is installed, or None


def _git_bash_env(env):
    """The environment for a Git Bash that a test starts: Git's own tools first on PATH, as in a Git Bash that
    the user opens. One started from PowerShell or cmd inherits the Windows PATH instead, and there `bash` may be
    WSL's, which cannot read a drive path, while cygpath and dirname are not found at all (seen 2026-10-08: six
    launcher tests failed when pytest was started from PowerShell, and the nvidia-smi test passed without its stub)."""
    env = dict(env)
    env["PATH"] = str(GIT_BASH.parent) + os.pathsep + env.get("PATH", "")
    return env


@pytest.mark.skipif(GIT_BASH is None, reason="needs Git Bash on Windows")
def test_launcher_passes_remote_paths_unchanged(tmp_path):
    launcher = (Path(ctl.__file__).resolve().parent / "ctl").as_posix()
    env = _git_bash_env({k: v for k, v in os.environ.items() if k not in ("MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL")})
    env["AUTODL_PYTHON"] = sys.executable
    r = subprocess.run([str(GIT_BASH), launcher, "log", "note", "--instance", "demo", "--project", str(tmp_path),
                        "--field", "path=/root/autodl-tmp/x", "--field", "/root/y=z"],
                       capture_output=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    rec = json.loads((tmp_path / ".autodl" / "power_log.jsonl").read_text(encoding="utf-8"))
    assert rec["path"] == "/root/autodl-tmp/x" and rec["/root/y"] == "z"


# ---- fixes after the third Codex review: an absent marker is not proof that nothing ran ----
def test_ssh_messages_go_to_a_log_file_that_is_removed(monkeypatch):
    fake = use(monkeypatch, {"true": OK})
    ctl.ssh_run("autodl-test", "true")
    argv = fake.calls[0][0]
    assert "-E" in argv and "LogLevel=VERBOSE" in argv
    assert not os.path.exists(argv[argv.index("-E") + 1])


def test_a_drop_after_authentication_is_uncertain_and_keep_is_not_resent(monkeypatch, capsys):
    fake = use(monkeypatch, {"autodl_guard.sh keep": LOST})
    assert ctl.main(["keep", "autodl-test", "30m", "--reason", "x"]) == ctl.EXIT_UNCERTAIN
    assert len(fake.calls) == 1 and "may or may not" in capsys.readouterr().err


def test_a_reset_of_an_established_session_is_not_taken_for_a_drop(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh keep": (255, b"", b"", "reset_after")})
    assert ctl.main(["keep", "autodl-test", "30m", "--reason", "x"]) == ctl.EXIT_UNCERTAIN
    assert len(fake.calls) == 1


def test_a_failed_tcp_connect_counts_as_not_run(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh keep": [(255, b"", b"", "timeout_after"), OK]})
    assert ctl.main(["keep", "autodl-test", "30m", "--reason", "x"]) == 0
    assert len(fake.calls) == 2


def test_an_unrecognised_ssh_failure_is_uncertain(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh deadline": (255, b"", b"", "unknown")})
    assert ctl.main(["deadline", "autodl-test", "90m"]) == ctl.EXIT_UNCERTAIN
    assert len(fake.calls) == 1


def test_run_is_resent_after_an_uncertain_try_with_its_checksum(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh run": [LOST, (0, b"started job train\n", b"")]})
    assert ctl.main(["run", "autodl-test", "train", "--cmd", "python train.py"]) == 0
    assert len(fake.calls) == 2
    want = hashlib.sha256(b"python train.py").hexdigest()
    assert all(re.search(r"--cmd-sha256 " + want, argv[-1]) for argv, _ in fake.calls)


def test_arm_sends_a_request_id_and_is_resent_after_an_uncertain_try(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh arm": [LOST, (0, b"armed\n", b"")]})
    assert ctl.main(["arm", "autodl-test", "--idle", "15m"]) == 0
    arms = [argv[-1] for argv, _ in fake.calls if "autodl_guard.sh arm" in argv[-1]]   # not the fingerprint probe
    reqs = [re.search(r"--req (\S+)", a).group(1) for a in arms]
    assert len(reqs) == 2 and reqs[0] == reqs[1]


def test_deploy_checks_the_upload_before_moving_it(tmp_path, monkeypatch):
    data = b"#!/usr/bin/env bash\necho hi\n"
    f = tmp_path / "autodl_guard.sh"
    f.write_bytes(data)
    monkeypatch.setattr(ctl, "LOCAL_GUARD", f)
    digest = hashlib.sha256(data).hexdigest()
    fake = use(monkeypatch, {"sha256sum": (0, digest.encode() + b"  x\n", b"")})
    assert ctl.main(["deploy", "autodl-test", "--no-autostart"]) == 0   # the hook has tests of its own
    remote = fake.calls[0][0][-1]
    assert digest in remote and remote.index("sha256sum -c") < remote.index("mv -f")


def test_off_now_after_an_uncertain_try_waits_instead_of_saying_not_sent(monkeypatch, capsys):
    fake = use(monkeypatch, {"off-now": LOST, "true": DROP})
    assert ctl.main(["off-now", "autodl-test", "--reason", "x"]) == 0
    assert len([c for c in fake.calls if "off-now" in c[0][-1]]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "unreachable"


def test_wait_for_down_stops_at_its_time_limit(monkeypatch, capsys):
    fake = use(monkeypatch, {"off-now": (0, b"shutdown issued\n", b""), "true": DROP})
    assert ctl.main(["off-now", "autodl-test", "--reason", "x", "--wait", "0s"]) == ctl.EXIT_ERR
    assert len([c for c in fake.calls if c[0][-1].endswith("; true")]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "timeout"


def test_a_failure_report_shows_what_ssh_said(monkeypatch, capsys):
    use(monkeypatch, {"autodl_guard.sh keep": DROP})
    assert ctl.main(["keep", "autodl-test", "30m", "--reason", "x"]) == ctl.EXIT_UNREACHABLE
    assert "Connection closed by" in capsys.readouterr().err


# ---- fixes after the fourth Codex review ----
def test_a_timeout_is_uncertain_not_unreachable(monkeypatch, capsys):
    fake = use(monkeypatch, {"autodl_guard.sh keep": (255, b"", b"", "timeout")})
    assert ctl.main(["keep", "autodl-test", "30m", "--reason", "x"]) == ctl.EXIT_UNCERTAIN
    assert len(fake.calls) == 1 and "may or may not" in capsys.readouterr().err


def test_revive_restart_is_not_resent_when_uncertain(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh revive": LOST})
    assert ctl.main(["revive", "autodl-test", "--restart"]) == ctl.EXIT_UNCERTAIN
    assert len(fake.calls) == 1
    fake = use(monkeypatch, {"autodl_guard.sh revive": [LOST, (0, b"daemon running\n", b"")]})
    assert ctl.main(["revive", "autodl-test"]) == 0
    assert len(fake.calls) == 2


def test_deploy_uses_its_own_temporary_file_and_reports_uncertain(tmp_path, monkeypatch):
    f = tmp_path / "autodl_guard.sh"
    f.write_bytes(b"#!/usr/bin/env bash\necho hi\n")
    monkeypatch.setattr(ctl, "LOCAL_GUARD", f)
    fake = use(monkeypatch, {"sha256sum": (255, b"", b"", "unknown")})
    assert ctl.main(["deploy", "autodl-test"]) == ctl.EXIT_UNCERTAIN
    remote = fake.calls[0][0][-1]
    assert "mktemp" in remote and ".tmp" not in remote


def test_push_that_timed_out_after_it_began_is_uncertain(tmp_path, monkeypatch, capsys):
    (tmp_path / "data").mkdir()
    _unbound(monkeypatch)
    monkeypatch.setattr(ctl, "push_once",
                        lambda *a: (1, "started", "timed out after 60s", b"", b"", "", True))
    assert ctl.main(["push", "autodl-test", str(tmp_path / "data"), "/root/autodl-tmp"]) == ctl.EXIT_UNCERTAIN
    assert "may or may not" in capsys.readouterr().err


def test_commit_staged_restores_the_old_copy_on_ctrl_c(tmp_path, monkeypatch):
    ctl.commit_staged(_staged(tmp_path, "old"), tmp_path / "exp1", overwrite=False)
    staged = _staged(tmp_path, "new")
    real_rename, calls = os.rename, []

    def interrupted(src, dst):
        calls.append((src, dst))
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real_rename(src, dst)
    monkeypatch.setattr(ctl.os, "rename", interrupted)
    with pytest.raises(KeyboardInterrupt):
        ctl.commit_staged(staged, tmp_path / "exp1", overwrite=True)
    assert (tmp_path / "exp1" / "m.txt").read_text() == "old"


# ---- fixes after the fifth Codex review ----
def test_a_timeout_after_the_marker_is_not_resent(monkeypatch, capsys):
    marker = (ctl.MARKER + "\n").encode()
    fake = use(monkeypatch, {"autodl_guard.sh run": [(255, b"", marker, "timeout"), (0, b"started job train\n", b"")]})
    assert ctl.main(["run", "autodl-test", "train", "--cmd", "python train.py"]) == ctl.EXIT_UNCERTAIN
    assert len(fake.calls) == 1 and "may or may not" in capsys.readouterr().err


def test_an_uncertain_answer_from_the_guard_is_exit_6(monkeypatch):
    use(monkeypatch, {"autodl_guard.sh run": (6, b"", b"error: job train: the start is uncertain\n")})
    assert ctl.main(["run", "autodl-test", "train", "--cmd", "python train.py"]) == ctl.EXIT_UNCERTAIN


# ---- fixes after the sixth Codex review ----
def test_a_status_that_did_not_report_back_is_uncertain_not_unreachable(monkeypatch, capsys):
    use(monkeypatch, {"true": OK, "autodl_guard.sh status": (255, b"", b"", "timeout")})
    assert ctl.main(["status", "autodl-test"]) == ctl.EXIT_UNCERTAIN
    assert "did not get through" not in capsys.readouterr().out
    use(monkeypatch, {"true": OK, "autodl_guard.sh status": (255, b"", b"", "started")})   # marker seen, then lost
    assert ctl.main(["status", "autodl-test"]) == ctl.EXIT_UNCERTAIN
    assert "not deployed" not in capsys.readouterr().out


# ---- ctl v0.8 (plan Phase 5) ----
def test_the_local_record_is_isolated(tmp_path):
    home = os.environ.get("AUTODL_AUTOGPU_HOME")   # a plain value: a failure must not print the whole environment
    assert home == str(tmp_path / "gpu-home")


def test_the_clock_can_be_set(clock, capsys):
    clock.set(1790000000)
    assert ctl.now_s() == 1790000000
    assert ctl.main(["now"]) == 0
    assert capsys.readouterr().out.strip() == "1790000000"


def sent_args(fake, i=-1):
    """The words of the guard command in call i, the start marker taken off."""
    return shlex.split(fake.calls[i][0][-1][len(ctl.MARKER_PREFIX):])


def test_version_prints_the_ctl_version(capsys):
    assert ctl.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.10.0"


def test_a_usage_error_is_exit_1_not_2(monkeypatch):
    fake = use(monkeypatch, {})
    for argv in (["arm", "autodl-test"], ["arm", "autodl-test", "--idle", "15m", "--util-signal"],
                 ["no-such-command"], ["quiet", "autodl-test", "train"]):
        with pytest.raises(SystemExit) as e:
            ctl.main(argv)
        assert e.value.code == ctl.EXIT_ERR, argv
    assert fake.calls == []


def test_arm_sends_only_what_was_given(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh arm": OK})
    assert ctl.main(["arm", "autodl-test", "--idle", "15m"]) == 0
    sent = sent_args(fake)
    i = sent.index("arm")
    assert sent[i:i + 5] == ["arm", "--idle", "15m", "--mode", "auto"]
    assert "--deadline" not in sent and "--keep" not in sent and "--util-signal" not in sent
    assert re.fullmatch(r"[0-9a-f]{16}", sent[sent.index("--req") + 1])
    given = [("--deadline", "2h"), ("--keep", "10m"), ("--grace", "3m"), ("--interval", "30s"), ("--gpu-probes", "3"),
             ("--thr-gpu", "7"), ("--thr-cpu", "4.5"), ("--thr-io", "600000"), ("--thr-net", "20000"),
             ("--unreliable", "net"), ("--calib", "c0123456789"), ("--calib-coverage", "unverified")]
    assert ctl.main(["arm", "autodl-test", "--idle", "15m", *[w for p in given for w in p], "--dry-run", "--rearm"]) == 0
    sent = sent_args(fake)
    for opt, val in given:
        assert sent[sent.index(opt) + 1] == val, opt
    assert "--dry-run" in sent and "--rearm" in sent


def test_arm_checks_durations_here(monkeypatch):
    fake = use(monkeypatch, {})
    for argv in (["--idle", "soon"], ["--idle", "15m", "--deadline", "31d"], ["--idle", "15m", "--keep", "x"]):
        assert ctl.main(["arm", "autodl-test", *argv]) == ctl.EXIT_ERR, argv
    assert fake.calls == []


def test_run_passes_quiet(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh run": (0, b"started job t\n", b"")})
    assert ctl.main(["run", "autodl-test", "t", "--cmd", "sleep 1", "--quiet", "30m"]) == 0
    sent = sent_args(fake)
    assert sent[sent.index("--quiet") + 1] == "30m"
    assert ctl.main(["run", "autodl-test", "t", "--cmd", "sleep 1", "--quiet", "later"]) == ctl.EXIT_ERR


def test_quiet_is_sent_with_its_reason(monkeypatch):
    fake = use(monkeypatch, {"autodl_guard.sh quiet": (0, b"ok\n", b"")})
    assert ctl.main(["quiet", "autodl-test", "train", "2h", "--reason", "saving a checkpoint"]) == 0
    sent = sent_args(fake)
    i = sent.index("quiet")
    assert sent[i:i + 3] == ["quiet", "train", "2h"] and sent[sent.index("--reason") + 1] == "saving a checkpoint"
    n = len(fake.calls)
    assert ctl.main(["quiet", "autodl-test", "bad name", "2h", "--reason", "x"]) == ctl.EXIT_ERR
    assert ctl.main(["quiet", "autodl-test", "train", "soon", "--reason", "x"]) == ctl.EXIT_ERR
    assert len(fake.calls) == n


def test_keep_has_no_after_job(monkeypatch):
    fake = use(monkeypatch, {})
    with pytest.raises(SystemExit) as e:
        ctl.main(["keep", "autodl-test", "5m", "--reason", "x", "--after-job"])
    assert e.value.code == ctl.EXIT_ERR and fake.calls == []


def test_off_now_passes_sample(monkeypatch):
    fake = use(monkeypatch, {"off-now": (3, b"refused: activity (cpu)\n", b"")})
    assert ctl.main(["off-now", "autodl-test", "--reason", "x", "--sample", "10"]) == ctl.EXIT_REFUSED
    sent = sent_args(fake)
    assert sent[sent.index("--sample") + 1] == "10"


def test_guard_exit_codes_7_and_8_pass_through(monkeypatch):
    for rc in (7, 8):
        use(monkeypatch, {"autodl_guard.sh keep": (rc, b"", b"error: refused\n")})
        assert ctl.main(["keep", "autodl-test", "5m", "--reason", "x"]) == rc
    assert (ctl.EXIT_PAST_DEADLINE, ctl.EXIT_GATED) == (7, 8)


def test_status_keeps_the_guards_deadline_in_s(monkeypatch, capsys):
    status = b"now=1000\ndeadline_at=1120\ndeadline_in_s=120\nkeep_in_s=\nheartbeat=990\narmed_by=boot\n"
    use(monkeypatch, {"nvidia-smi": (0, b"gpu 1\n", b""), "autodl_guard.sh status": (0, status, b""), "true": OK})
    assert ctl.main(["status", "autodl-test"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["deadline_in_s"] == "120" and res["keep_in_s"] == "" and res["heartbeat_age_s"] == 10
    assert "autostart" in res["note"] and "arm" in res["note"]


def _guard_file(tmp_path, monkeypatch):
    data = b"#!/usr/bin/env bash\necho hi\n"
    f = tmp_path / "autodl_guard.sh"
    f.write_bytes(data)
    monkeypatch.setattr(ctl, "LOCAL_GUARD", f)
    return hashlib.sha256(data).hexdigest().encode() + b"  /root/autodl-tmp/.autodl-guard/autodl_guard.sh\n"


def test_deploy_installs_autostart(tmp_path, monkeypatch, capsys):
    good = _guard_file(tmp_path, monkeypatch)
    cases = [((0, b"autostart installed (/etc/profile.d/autodl-autogpu-guard.sh): at every container start ...\n", b""),
              0, "installed"),
             ((0, b"autostart already installed (/etc/profile.d/autodl-autogpu-guard.sh)\n", b""), 0, "already"),
             ((1, b"", b"error: install-autostart: /etc/profile.d/autodl-autogpu-guard.sh exists and was not written by "
                       b"this script; it is left alone\n"), 1, "failed"),
             (DROP, 2, "not sent"),
             (LOST, 6, "uncertain")]
    for answer, rc, state in cases:
        fake = use(monkeypatch, {"sha256sum": (0, good, b""), "install-autostart": answer})
        assert ctl.main(["deploy", "autodl-test"]) == rc, state
        res = json.loads(capsys.readouterr().out)
        assert res["deployed"] is True and res["autostart"].startswith(state), (state, res)
        assert any("autodl_guard.sh install-autostart" in c[0][-1] for c in fake.calls), state
    fake = use(monkeypatch, {"sha256sum": (0, good, b"")})
    assert ctl.main(["deploy", "autodl-test", "--no-autostart"]) == 0
    assert json.loads(capsys.readouterr().out)["autostart"] == "skipped"
    assert not any("install-autostart" in c[0][-1] for c in fake.calls)


def test_deploy_keeps_the_guards_words_when_autostart_fails(tmp_path, monkeypatch, capsys):
    good = _guard_file(tmp_path, monkeypatch)
    use(monkeypatch, {"sha256sum": (0, good, b""),
                      "install-autostart": (1, b"", b"error: install-autostart: /etc/profile.d does not exist\n")})
    assert ctl.main(["deploy", "autodl-test"]) == ctl.EXIT_ERR
    assert "/etc/profile.d does not exist" in json.loads(capsys.readouterr().out)["autostart"]


def test_autostart_command_calls_the_guard(monkeypatch):
    for sub in ("install", "uninstall"):
        fake = use(monkeypatch, {f"autodl_guard.sh {sub}-autostart": (0, b"ok\n", b"")})
        assert ctl.main(["autostart", "autodl-test", sub]) == 0
        assert f"autodl_guard.sh {sub}-autostart" in fake.calls[-1][0][-1]
    use(monkeypatch, {"uninstall-autostart": (1, b"", b"error: uninstall-autostart: not written by this script\n")})
    assert ctl.main(["autostart", "autodl-test", "uninstall"]) == ctl.EXIT_ERR


def test_off_now_reports_a_committed_shutdown(monkeypatch, capsys):
    committed = b"shutdown committed (off-now (x))\nshutdown issuing\n"
    for answer in ((255, committed, b"", True), (1, committed + b"shutdown command failed rc=1 (x)\n", b""),
                   (0, b"shutdown committed (off-now (x))\nshutdown issued (off-now (x))\n", b""),
                   (255, committed, b"", "timeout")):
        use(monkeypatch, {"off-now": answer, "true": DROP})
        assert ctl.main(["off-now", "autodl-test", "--reason", "x"]) == 0, answer
        assert json.loads(capsys.readouterr().out)["shutdown"] == "committed", answer
    use(monkeypatch, {"off-now": (255, b"", b"", True), "true": DROP})   # the marker arrived, then nothing
    assert ctl.main(["off-now", "autodl-test", "--reason", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["shutdown"] == "started"
    use(monkeypatch, {"off-now": LOST, "true": DROP})
    assert ctl.main(["off-now", "autodl-test", "--reason", "x"]) == 0
    assert json.loads(capsys.readouterr().out)["shutdown"] == "uncertain"


def test_off_now_refusals_are_not_mistaken_for_a_commit(monkeypatch):
    # only a whole line is a commit: a refusal line that contains the words is not ("--wait 1s" keeps a wrong wait short)
    for rc in (3, 4, 7, 8):
        for reason in ("shutdown committed", "shutdown issuing", "shutdown committed (x)"):
            fake = use(monkeypatch, {"off-now": (rc, f"refused (off-now ({reason}))\n".encode(), b"")})
            assert ctl.main(["off-now", "autodl-test", "--reason", reason, "--wait", "1s"]) == rc, (rc, reason)
            assert len(fake.calls) == 1, (rc, reason)   # no waiting for the instance to go down
    dry = b"shutdown committed (off-now (x))\ndry-run: shutdown not executed (off-now (x))\n"
    fake = use(monkeypatch, {"off-now": (0, dry, b"")})
    assert ctl.main(["off-now", "autodl-test", "--reason", "x", "--wait", "1s"]) == 0
    assert len(fake.calls) == 1


def test_arm_passes_the_upgrade_refusal_through(monkeypatch, capsys):
    msg = (b"error: a 0.7 guard daemon runs in this boot (it holds /root/autodl-tmp/.autodl-guard/state/.daemon.lock): "
           b"two daemons would each shut the instance down by their own rules; upgrade on a fresh boot (shut down, "
           b"start, deploy, arm), or let 0.7 finish first\n")
    use(monkeypatch, {"autodl_guard.sh arm": (1, b"", msg)})
    assert ctl.main(["arm", "autodl-test", "--idle", "15m"]) == ctl.EXIT_ERR
    assert "upgrade on a fresh boot" in capsys.readouterr().err


# ---- calibrate, and arm with a matching calibration (Task 5.5) ----
T5 = 1_790_000_000
GUARD12 = "0123456789ab"
STATUS_IDLE = b"version=0.8.0\narmed_at=100\nlast_active_at=200\njob.train=done:0|1000|2000|/root/train.log\n"


def _verify(alias="autodl-test", iid=INSTANCE):
    with ctl.Store() as st:   # what a successful check ALIAS --instance ID writes
        st.data["aliases"][alias] = {"instance": iid, "at": 1}
        st.save()


def _probe(mode="gpu", gpu=("gpu=NVIDIA GeForce RTX 4090, 550.54.14",), os_name='"Ubuntu"', os_version='"22.04"',
           cpu="max 100000", mem="max", guard=GUARD12, host=f"autodl-container-{INSTANCE}") -> bytes:
    first = "gpu 1" if mode == "gpu" else "nogpu-check 2147483648"
    lines = [first, "===", f"os_name={os_name}", f"os_version={os_version}", *gpu, f"cpu.max={cpu}", f"memory.max={mem}",
             "===", guard, "===", host]
    return ("\n".join(lines) + "\n").encode()


def _sample(n=5, every=60, k=3, cpu=(1.0, 4.2, 2.0, 3.0, 1.5), io=(1000,) * 5, net=(500,) * 5, gpu=(0, 2, 1, 0, 3),
            dts=(6000, 5900, 6100, 6000, 5950), header=None, rows_edit=None) -> bytes:
    """What the guard's sample prints: CPU in percent of one core, disk and network in bytes per second, over
    intervals of dts centiseconds (not exactly 60 s)."""
    head = header or f"# autodl_guard 0.8.0 sample every={every} count={n} gpu_samples={k} clk_tck=100"
    cols = "# epoch\tuptime_cs\tcpu_usec\tio_bytes\tnet_bytes\tgpu_max\tgpu_fail\tguard_ticks\tself_ticks"
    up, c, i_, ne = 100000, 10 ** 9, 10 ** 8, 10 ** 8
    rows = [[str(T5), str(up), str(c), str(i_), str(ne), "na", "na", "5", "3"]]
    for j in range(len(dts)):
        up += dts[j]
        c += round(cpu[j] * dts[j] * 100)
        i_ += round(io[j] * dts[j] / 100)
        ne += round(net[j] * dts[j] / 100)
        g = "na" if k == 0 else ("" if gpu[j] is None else str(gpu[j]))
        f = "na" if k == 0 else ("1" if gpu[j] is None else "0")
        rows.append([str(T5 + 60 * (j + 1)), str(up), str(c), str(i_), str(ne), g, f, "5", "3"])
    if rows_edit:
        rows_edit(rows)
    return ("\n".join([head, cols] + ["\t".join(r) for r in rows]) + "\n").encode()


def _cal_table(status=STATUS_IDLE, probe=None, sample=None) -> dict:
    return {"autodl_guard.sh status": status if isinstance(status, list) else (0, status, b""),
            "os-release": (0, probe or _probe(), b""), "autodl_guard.sh sample": (0, sample or _sample(), b"")}


def _calibs(iid=INSTANCE) -> list:
    with ctl.Store() as st:
        return st.data["calib"].get(iid, [])


def test_calibrate_sets_thresholds_above_the_noise(monkeypatch, capsys, clock):
    clock.set(T5)
    _verify()
    fake = use(monkeypatch, _cal_table())
    assert ctl.main(["calibrate", "autodl-test", "--minutes", "5"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["thresholds"] == {"cpu": 6.3, "io": 500000, "net": 10000, "gpu": 5} and res["unreliable"] == []
    assert res["max"]["cpu"] == 4.2 and res["median"]["cpu"] == 2.0 and res["max"]["gpu"] == 3 and res["intervals"] == 5
    (e,) = _calibs()
    assert e["thresholds"] == res["thresholds"] and re.match(r"^c[0-9a-f]{10}$", e["id"]) and e["mode"] == "gpu"
    assert e["guard"] == GUARD12 and re.match(r"^[0-9a-f]{12}$", e["fingerprint"]) and e["at"] == T5
    sample = next(c[0][-1] for c in fake.calls if "autodl_guard.sh sample" in c[0][-1])
    assert "--every 60s --count 5 --gpu-samples 3" in sample


def test_calibrate_marks_a_noisy_signal_unreliable(monkeypatch, capsys):
    _verify()
    use(monkeypatch, _cal_table(sample=_sample(net=(700000, 1000, 1000, 1000, 1000))))
    assert ctl.main(["calibrate", "autodl-test"]) == 0   # 1.5 x 7e5 is above the lightest work seen (9.024e5)
    res = json.loads(capsys.readouterr().out)
    assert res["unreliable"] == ["net"] and res["thresholds"]["net"] == 1050000
    use(monkeypatch, _cal_table(sample=_sample(cpu=(80.0, 1.0, 1.0, 1.0, 1.0))))
    assert ctl.main(["calibrate", "autodl-test"]) == 0   # the guard takes at most 100 % of a core
    res = json.loads(capsys.readouterr().out)
    assert res["unreliable"] == ["cpu"] and res["thresholds"]["cpu"] == 100.0


def test_calibrate_skips_failed_gpu_readings(monkeypatch, capsys):
    _verify()
    use(monkeypatch, _cal_table(sample=_sample(gpu=(0, None, 60, None, 1))))
    assert ctl.main(["calibrate", "autodl-test"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["max"]["gpu"] == 60 and res["thresholds"]["gpu"] == 90 and "gpu" not in res["unreliable"]
    use(monkeypatch, _cal_table(sample=_sample(gpu=(None,) * 5)))
    assert ctl.main(["calibrate", "autodl-test"]) == 0
    assert json.loads(capsys.readouterr().out)["unreliable"] == ["gpu"]


def _set(i, col, value):
    def edit(rows):
        rows[i][col] = value
    return edit


@pytest.mark.parametrize("sample", [
    _sample(header="# autodl_guard 0.8.0 sample every=30 count=5 gpu_samples=3 clk_tck=100"),
    _sample(header="# autodl_guard 0.8.0 sample every=60 count=4 gpu_samples=3 clk_tck=100"),
    _sample(header="# autodl_guard 0.8.0 sample every=60 count=5 gpu_samples=0 clk_tck=100"),
    _sample(rows_edit=_set(2, 3, "")),   # a disk reading is missing
    _sample(rows_edit=_set(3, 2, "1")),   # the CPU counter went back
    _sample(rows_edit=lambda rows: rows[3].__setitem__(1, rows[2][1])),   # the uptime did not increase
    _sample(dts=(6000, 5900, 6100, 6000)),   # one interval short
    _sample(rows_edit=lambda rows: rows[2].pop()),   # a column short
], ids=["every", "count", "gpu_samples", "empty-io", "cpu-back", "uptime-flat", "rows", "columns"])
def test_calibrate_rejects_malformed_samples(monkeypatch, capsys, sample):
    _verify()
    use(monkeypatch, _cal_table(sample=sample))
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_ERR and _calibs() == []


def test_calibrate_needs_a_complete_run(monkeypatch, capsys):
    _verify()
    partial = _sample(dts=(6000, 5900, 6100))   # three intervals, then the connection was lost
    use(monkeypatch, {**_cal_table(), "autodl_guard.sh sample": (255, partial, b"", "lost")})
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_UNCERTAIN and _calibs() == []
    use(monkeypatch, {**_cal_table(), "autodl_guard.sh sample": (255, partial, b"", "timeout")})   # ssh hung
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_UNCERTAIN and _calibs() == []
    use(monkeypatch, {**_cal_table(), "autodl_guard.sh status": (255, b"", b"", True)})   # started, then dropped
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_UNCERTAIN and _calibs() == []


def test_calibrate_refuses_while_a_job_runs(monkeypatch, capsys):
    _verify()
    busy = STATUS_IDLE + b"job.eval=running|3000||/root/eval.log\n"
    fake = use(monkeypatch, _cal_table(status=busy))
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_REFUSED and _calibs() == []
    assert not any("sample" in c[0][-1] for c in fake.calls)


STATUS_FULL = STATUS_IDLE + b"deadline_at=0\nkeep_until_at=0\noff_when_done=0\nactive_why=\nsig.cpu=idle:1.0\n"


@pytest.mark.parametrize("after, name", [
    (STATUS_FULL.replace(b"1000|2000", b"2500|2900"), "jobs"),   # the same job ran again meanwhile
    (STATUS_FULL + b"job.eval=done:0|2500|2600|/root/eval.log\n", "jobs"),   # a short job came and went
    (STATUS_FULL.replace(b"armed_at=100", b"armed_at=150"), "armed_at"),
    (STATUS_FULL.replace(b"deadline_at=0", b"deadline_at=9000"), "deadline_at"),
    (STATUS_FULL.replace(b"keep_until_at=0", b"keep_until_at=9000"), "keep_until_at"),
    (STATUS_FULL.replace(b"off_when_done=0", b"off_when_done=1"), "off_when_done"),
], ids=["job-again", "job-new", "armed_at", "deadline_at", "keep_until_at", "off_when_done"])
def test_calibrate_discards_a_run_disturbed_by_others(monkeypatch, capsys, after, name):
    _verify()
    use(monkeypatch, _cal_table(status=[(0, STATUS_FULL, b""), (0, after, b"")]))
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_ERR and _calibs() == []
    err = json.loads(capsys.readouterr().out)["error"]
    assert err.startswith(name + " changed") and "nothing was kept" in err


def test_calibrate_keeps_a_run_the_guard_counted_as_in_use(monkeypatch, capsys):
    """Over the current thresholds the guard marks the instance in use at every check: that is why one calibrates."""
    _verify()
    after = (STATUS_FULL.replace(b"last_active_at=200", b"last_active_at=560")
             .replace(b"active_why=", b"active_why=cpu:busy").replace(b"sig.cpu=idle:1.0", b"sig.cpu=busy:6.0"))
    use(monkeypatch, _cal_table(status=[(0, STATUS_FULL, b""), (0, after, b"")]))
    assert ctl.main(["calibrate", "autodl-test"]) == 0 and len(_calibs()) == 1


def test_calibrate_needs_a_verified_alias(monkeypatch, capsys):
    fake = use(monkeypatch, _cal_table())
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_ERR and fake.calls == []


@pytest.mark.parametrize("probe, rc", [(DROP, ctl.EXIT_UNREACHABLE), (LOST, ctl.EXIT_UNCERTAIN),
                                       ((0, b"", b"", "timeout"), ctl.EXIT_UNCERTAIN), ((1, b"", b""), ctl.EXIT_ERR)],
                         ids=["not-sent", "lost", "timeout", "failed"])
def test_calibrate_maps_a_failed_probe_like_other_commands(monkeypatch, capsys, probe, rc):
    _verify()
    fake = use(monkeypatch, {**_cal_table(), "os-release": probe})
    assert ctl.main(["calibrate", "autodl-test"]) == rc and _calibs() == []
    assert not any("sample" in c[0][-1] for c in fake.calls)


def test_calibrate_refuses_an_alias_that_leads_elsewhere(monkeypatch, capsys):
    _verify()   # verified long ago; the alias now leads to another instance
    fake = use(monkeypatch, _cal_table(probe=_probe(host="autodl-container-ffff000000-0000ffff")))
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_MISMATCH and _calibs() == []
    assert not any("sample" in c[0][-1] for c in fake.calls) and "check autodl-test --instance" in capsys.readouterr().out


def test_calibrate_refuses_a_probe_without_a_host_name(monkeypatch, capsys):
    _verify()   # whether the alias still leads to the verified instance cannot be told
    fake = use(monkeypatch, _cal_table(probe=_probe(host="")))
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_ERR and _calibs() == []
    assert not any("sample" in c[0][-1] for c in fake.calls) and "host name" in capsys.readouterr().out


def test_arm_uses_no_calibration_without_a_host_name(monkeypatch, capsys, clock):
    _calibrated(monkeypatch, capsys, clock)
    rc, sent, said, _ = _arm(monkeypatch, capsys, _probe(host=""))
    assert rc == 0 and "--calib" not in sent and "default thresholds" in said and "host name" in said


def test_calibrate_caps_the_gpu_threshold_at_100(monkeypatch, capsys):
    _verify()
    use(monkeypatch, _cal_table(sample=_sample(gpu=(0, 80, 1, 0, 3))))
    assert ctl.main(["calibrate", "autodl-test"]) == 0   # 1.5 x 80 is over the guard's range
    res = json.loads(capsys.readouterr().out)
    assert res["thresholds"]["gpu"] == 100 and res["max"]["gpu"] == 80 and "gpu" in res["unreliable"]


def _calibrated(monkeypatch, capsys, clock, probe=None) -> dict:
    clock.set(T5)
    _verify()
    use(monkeypatch, _cal_table(probe=probe, sample=_sample(net=(700000, 1000, 1000, 1000, 1000))))
    assert ctl.main(["calibrate", "autodl-test"]) == 0
    capsys.readouterr()
    return _calibs()[0]


def _arm(monkeypatch, capsys, probe, *extra, arm_rc=0) -> tuple:
    fake = use(monkeypatch, {"os-release": probe if isinstance(probe, tuple) else (0, probe, b""),
                             "autodl_guard.sh arm": (arm_rc, b"armed\n", b"")})
    rc = ctl.main(["arm", "autodl-test", "--idle", "15m", *extra])
    captured = capsys.readouterr()
    arm = next(c[0][-1] for c in fake.calls if "autodl_guard.sh arm" in c[0][-1])
    return rc, shlex.split(arm), captured.out + captured.err, fake


def test_arm_uses_a_matching_calibration(monkeypatch, capsys, clock):
    e = _calibrated(monkeypatch, capsys, clock)
    rc, sent, said, _ = _arm(monkeypatch, capsys, _probe())
    assert rc == 0 and e["id"] in said
    for opt, val in (("--thr-cpu", "6.3"), ("--thr-io", "500000"), ("--thr-net", "1050000"), ("--thr-gpu", "5"),
                     ("--unreliable", "net"), ("--calib", e["id"]), ("--calib-coverage", "unverified")):
        assert sent[sent.index(opt) + 1] == val, opt


def test_arm_says_which_signals_the_calibration_turns_off(monkeypatch, capsys, clock):
    _calibrated(monkeypatch, capsys, clock)   # the network is unreliable there
    rc, sent, said, _ = _arm(monkeypatch, capsys, _probe())
    line = next(x for x in said.splitlines() if x.startswith("calibration:"))
    assert rc == 0 and "turned off: net" in line and "quiet or keep" in line


def test_arm_ignores_the_calibration_when_the_alias_leads_elsewhere(monkeypatch, capsys, clock):
    _calibrated(monkeypatch, capsys, clock)
    rc, sent, said, _ = _arm(monkeypatch, capsys, _probe(host="autodl-container-ffff000000-0000ffff"))
    assert rc == 0 and "--calib" not in sent and "default thresholds" in said and "leads to" in said


@pytest.mark.parametrize("rhythm", [["--interval", "30s"], ["--gpu-probes", "5"]], ids=["interval", "gpu-probes"])
def test_arm_with_its_own_rhythm_looks_nothing_up(monkeypatch, capsys, clock, rhythm):
    _calibrated(monkeypatch, capsys, clock)   # measured once a minute with three GPU probes
    rc, sent, said, fake = _arm(monkeypatch, capsys, _probe(), *rhythm)
    assert rc == 0 and "--calib" not in sent and not any("os-release" in c[0][-1] for c in fake.calls)
    assert "60 s" in next(x for x in said.splitlines() if x.startswith("calibration:"))


def test_arm_uses_the_newest_matching_calibration(monkeypatch, capsys, clock):
    e = _calibrated(monkeypatch, capsys, clock)
    with ctl.Store() as st:   # an older one of the same instance, mode, environment and guard
        st.data["calib"][INSTANCE].insert(0, dict(e, id="c0000000001", at=T5 - 86400,
                                                  thresholds=dict(e["thresholds"], cpu=9.9)))
        st.data["calib"][INSTANCE].append(dict(e, id="c0000000002", at=T5 - 3600))
        st.save()
    rc, sent, said, _ = _arm(monkeypatch, capsys, _probe())
    assert rc == 0 and sent[sent.index("--calib") + 1] == e["id"] and sent[sent.index("--thr-cpu") + 1] == "6.3"


def test_arm_with_an_unverified_alias_uses_the_defaults(monkeypatch, capsys, clock):
    _calibrated(monkeypatch, capsys, clock)
    with ctl.Store() as st:
        st.data["aliases"].clear()
        st.save()
    rc, sent, said, _ = _arm(monkeypatch, capsys, _probe())
    assert rc == 0 and "--calib" not in sent and "is not verified" in said


@pytest.mark.parametrize("change", ["old", "fingerprint", "guard", "mode", "explicit-mode"])
def test_arm_ignores_an_old_or_other_calibration(monkeypatch, capsys, clock, change):
    _calibrated(monkeypatch, capsys, clock)
    probe, extra = _probe(), []
    if change == "explicit-mode":   # the same environment, but arm is told another mode
        extra = ["--mode", "nogpu"]
    elif change == "old":
        clock.set(T5 + 31 * 86400)
    elif change == "fingerprint":
        probe = _probe(os_version='"24.04"')
    elif change == "guard":
        probe = _probe(guard="ffffffffffff")
    elif change == "mode":
        probe = _probe(mode="nogpu", gpu=("gpu=absent",), mem="2147483648")
    rc, sent, said, _ = _arm(monkeypatch, capsys, probe, *extra)
    assert rc == 0 and "--calib" not in sent and "--thr-cpu" not in sent and "default thresholds" in said


def test_calibrate_needs_a_whole_fingerprint(monkeypatch, capsys):
    _verify()   # two environments that both could not read a part would look alike: nothing is kept
    fake = use(monkeypatch, _cal_table(probe=_probe(os_name="absent")))
    assert ctl.main(["calibrate", "autodl-test"]) == ctl.EXIT_ERR and _calibs() == []
    assert not any("sample" in c[0][-1] for c in fake.calls)


@pytest.mark.parametrize("probe", [
    _probe(gpu=("gpu=absent",)), _probe(os_name="absent"), _probe(os_version="absent"), _probe(cpu="absent"),
    _probe(mem="absent"), _probe(guard=""),
], ids=["gpu", "os_name", "os_version", "cpu.max", "memory.max", "guard"])
def test_a_fingerprint_with_missing_parts_finds_nothing(monkeypatch, capsys, clock, probe):
    _calibrated(monkeypatch, capsys, clock, probe=_probe())
    rc, sent, said, _ = _arm(monkeypatch, capsys, probe)
    assert rc == 0 and "--calib" not in sent and "default thresholds" in said


def _two_instances_calibrated():
    other = "ffff000000-0000ffff"
    with ctl.Store() as st:
        e = {"id": "c0123456789", "mode": "nogpu", "fingerprint": "0123456789ab", "guard": GUARD12, "at": T5,
             "intervals": 5, "thresholds": {"cpu": 3.0, "io": 500000, "net": 10000}, "unreliable": [],
             "max": {"cpu": 1.0, "io": 10.0, "net": 10.0}, "median": {"cpu": 1.0, "io": 10.0, "net": 10.0}}
        st.data["calib"][INSTANCE] = [e]
        st.data["calib"][other] = [dict(e, id="c9876543210")]
        st.data["aliases"]["autodl-test"] = {"instance": INSTANCE, "at": 1}
        st.save()
    return other


def test_a_new_hook_install_forgets_the_calibrations(tmp_path, monkeypatch, capsys):
    good = _guard_file(tmp_path, monkeypatch)
    other = _two_instances_calibrated()
    use(monkeypatch, {"sha256sum": (0, good, b""), "install-autostart": (0, b"autostart already installed (x)\n", b"")})
    assert ctl.main(["deploy", "autodl-test"]) == 0 and len(_calibs()) == 1   # already there: kept
    capsys.readouterr()
    use(monkeypatch, {"sha256sum": (0, good, b""), "install-autostart": (0, b"autostart installed (x): ...\n", b"")})
    assert ctl.main(["deploy", "autodl-test"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["calibration_forget"] == f"forgot 1 calibration(s) of {INSTANCE}: a new hook install hints at a new image"
    assert _calibs() == [] and len(_calibs(other)) == 1
    with ctl.Store() as st:   # an alias never verified: nothing can be found to forget
        st.data["aliases"].clear()
        st.save()
    assert ctl.main(["deploy", "autodl-test"]) == 0
    assert json.loads(capsys.readouterr().out)["calibration_forget"].startswith("nothing forgotten")


def test_a_forget_that_cannot_be_written_fails_the_deploy(tmp_path, monkeypatch, capsys):
    good = _guard_file(tmp_path, monkeypatch)
    _two_instances_calibrated()
    (_record() / "store.json").write_bytes(b"not json")
    use(monkeypatch, {"sha256sum": (0, good, b""), "install-autostart": (0, b"autostart installed (x): ...\n", b"")})
    assert ctl.main(["deploy", "autodl-test"]) == ctl.EXIT_ERR
    res = json.loads(capsys.readouterr().out)
    assert res["deployed"] is True and res["calibration_forget"].startswith("failed")


def test_calibrate_forget(monkeypatch, capsys):
    other = _two_instances_calibrated()
    fake = use(monkeypatch, {})
    assert ctl.main(["calibrate", "autodl-test", "--forget"]) == 0
    assert fake.calls == [] and _calibs() == [] and len(_calibs(other)) == 1


def test_arm_with_manual_thresholds_looks_nothing_up(monkeypatch, capsys, clock):
    _calibrated(monkeypatch, capsys, clock)
    rc, sent, said, fake = _arm(monkeypatch, capsys, _probe(), "--thr-cpu", "4.5")
    assert rc == 0 and not any("os-release" in c[0][-1] for c in fake.calls) and "--calib" not in sent


@pytest.mark.parametrize("failure", ["dropped", "lost", "store", "unlistable"])
def test_arm_goes_on_when_the_lookup_fails(monkeypatch, capsys, clock, failure):
    _calibrated(monkeypatch, capsys, clock)
    probe = DROP if failure == "dropped" else LOST if failure == "lost" else _probe()
    if failure == "store":
        (_record() / "store.json").write_bytes(b"not json")
    elif failure == "unlistable":   # the record's directory cannot be listed
        real, home = os.scandir, str(_record())

        def scandir(p="."):
            if str(p) == home:
                raise PermissionError(13, "Permission denied", home)
            return real(p)
        monkeypatch.setattr(ctl.os, "scandir", scandir)
    rc, sent, said, _ = _arm(monkeypatch, capsys, probe, arm_rc=5)
    assert rc == ctl.EXIT_ARMED and "--calib" not in sent and "default thresholds" in said   # the guard's own exit code


# ---- doctor and the launcher (Task 5.6) ----
def _doctor(monkeypatch, capsys, table, *argv) -> tuple:
    use(monkeypatch, table)
    rc = ctl.main(["doctor", *argv])
    res = json.loads(capsys.readouterr().out)
    return rc, {c["check"]: c for c in res["checks"]}, res


DOCTOR_OK = {"--version": (0, b"GNU bash, version 5.2.26\n", b""), "-V": (0, b"", b"OpenSSH_10.2p1\n"),
             "autodl-doctor-probe": (0, b"user root\n", b"")}


def test_doctor_reports_each_check(monkeypatch, capsys):
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")   # as scripts/ctl sets it
    rc, checks, res = _doctor(monkeypatch, capsys, DOCTOR_OK)
    assert rc == 0 and res["ok"] is True
    assert set(checks) == {"python", "bash", "ssh", "paths", "local record", "launcher"}
    assert all(c["ok"] for c in checks.values()) and "OpenSSH_10.2p1" in checks["ssh"]["detail"]


def test_doctor_checks_ssh_by_running_it(monkeypatch, capsys):
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")
    rc, checks, _ = _doctor(monkeypatch, capsys, {**DOCTOR_OK, "autodl-doctor-probe": (1, b"", b"bad option\n")})
    assert rc == ctl.EXIT_ERR and not checks["ssh"]["ok"]
    use(monkeypatch, DOCTOR_OK)
    fake = ctl.RUNNER
    monkeypatch.setattr(ctl, "RUNNER", lambda argv, **kw: (subprocess.CompletedProcess(argv, 0, b"", b"")
                                                           if "-G" in argv else fake(argv, **kw)))   # no log written
    assert ctl.main(["doctor"]) == ctl.EXIT_ERR
    assert not {c["check"]: c for c in json.loads(capsys.readouterr().out)["checks"]}["ssh"]["ok"]
    rc, checks, _ = _doctor(monkeypatch, capsys, {**DOCTOR_OK, "-V": (0, b"", b"OpenSSH_6.0 very old\n")})
    assert rc == 0 and checks["ssh"]["ok"]   # the version is only reported


def test_doctor_flags_a_bash_or_python_that_will_not_do(monkeypatch, capsys):
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")
    rc, checks, _ = _doctor(monkeypatch, capsys, {**DOCTOR_OK, "--version": (127, b"", b"not found\n")})
    assert rc == ctl.EXIT_ERR and not checks["bash"]["ok"] and checks["python"]["ok"]
    monkeypatch.delattr(ctl.tarfile, "data_filter")   # as in a Python before 3.8.17 (or the matching patch release)
    rc, checks, _ = _doctor(monkeypatch, capsys, DOCTOR_OK)
    assert rc == ctl.EXIT_ERR and not checks["python"]["ok"] and "data_filter" in checks["python"]["detail"]


def test_doctor_ssh_check_really_works_here():
    ok, detail = ctl.ssh_check()   # the real ssh; -G only prints the settings, nothing connects
    assert ok, detail


def test_doctor_ssh_check_really_works_in_wsl():
    if not local_tools.have_wsl("python3", "ssh"):   # asked here, not when the file is collected: other tests need no WSL
        pytest.skip(f"needs {local_tools.wsl_name()} with python3 and ssh")
    wsl = local_tools.wsl_command()
    r = subprocess.run([*wsl, "wslpath", "-a", Path(ctl.__file__).parent.as_posix()], capture_output=True, timeout=120)
    assert r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace")   # wsl.exe words its own errors in UTF-16
    scripts = r.stdout.decode().strip()
    code = f"import sys; sys.path.insert(0, {scripts!r}); import autodl_ctl as c; print(c.ssh_check()[0])"
    r = subprocess.run([*wsl, "python3", "-B", "-c", code], capture_output=True, timeout=120)
    assert r.stdout.decode().strip() == "True", r.stderr.decode(errors="replace")


def test_doctor_flags_git_bash_without_the_launcher(monkeypatch, capsys):
    monkeypatch.setenv("MSYSTEM", "MINGW64")
    monkeypatch.delenv("MSYS_NO_PATHCONV", raising=False)
    rc, checks, _ = _doctor(monkeypatch, capsys, DOCTOR_OK)
    assert rc == ctl.EXIT_ERR and not checks["launcher"]["ok"] and "scripts/ctl" in checks["launcher"]["detail"]


def test_doctor_flags_an_unsafe_store(monkeypatch, capsys):
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")
    with ctl.Store():
        pass
    if os.name == "nt":
        assert subprocess.run(["icacls", str(_record()), "/grant", "*S-1-1-0:(R)"], capture_output=True).returncode == 0
    else:
        os.chmod(_record(), 0o755)
    rc, checks, _ = _doctor(monkeypatch, capsys, DOCTOR_OK)
    assert rc == ctl.EXIT_ERR and not checks["local record"]["ok"]


def test_doctor_with_an_alias_checks_it_answers(monkeypatch, capsys):
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")
    rc, checks, _ = _doctor(monkeypatch, capsys, {**DOCTOR_OK, "true": OK}, "autodl-test")
    assert rc == 0 and checks["alias"]["ok"] and "autodl-test" in checks["alias"]["detail"]
    rc, checks, _ = _doctor(monkeypatch, capsys, {**DOCTOR_OK, "true": DROP}, "autodl-test")
    assert rc == ctl.EXIT_ERR and not checks["alias"]["ok"]


@pytest.mark.parametrize("error", [FileNotFoundError(2, "No such file or directory"),
                                   subprocess.TimeoutExpired(["ssh", "-V"], 15)], ids=["missing", "hung"])
def test_doctor_reports_an_ssh_that_does_not_run(monkeypatch, capsys, error):
    monkeypatch.setenv("MSYS_NO_PATHCONV", "1")
    use(monkeypatch, DOCTOR_OK)
    fake = ctl.RUNNER

    def runner(argv, **kw):
        if "-V" in argv:
            raise error
        return fake(argv, **kw)
    monkeypatch.setattr(ctl, "RUNNER", runner)
    assert ctl.main(["doctor"]) == ctl.EXIT_ERR
    checks = {c["check"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert set(checks) == {"python", "bash", "ssh", "paths", "local record", "launcher"} and not checks["ssh"]["ok"]


@pytest.mark.skipif(os.name != "nt", reason="the layouts of Git for Windows")
@pytest.mark.parametrize("git", ["mingw64/bin/git.exe", "cmd/git.exe", "bin/git.exe"])
def test_find_bash_walks_up_from_git(tmp_path, monkeypatch, git):
    root = tmp_path / "Git"   # not under C:/Program Files: only the walk from git can find it
    (root / git).parent.mkdir(parents=True)
    (root / git).write_bytes(b"")
    (root / "bin").mkdir(exist_ok=True)
    (root / "bin" / "bash.exe").write_bytes(b"")
    monkeypatch.setattr(ctl.shutil, "which", lambda name: str(root / git) if name == "git" else None)
    assert ctl.find_bash() == str(root / "bin" / "bash.exe")


def test_launcher_has_no_cr():
    assert b"\r" not in (Path(ctl.__file__).resolve().parent / "ctl").read_bytes()


git_bash_only = pytest.mark.skipif(GIT_BASH is None, reason="needs Git Bash on Windows")


def _stub(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("#!/bin/sh\n" + body + "\n").encode())
    return path


def _launch(tmp_path, *argv, python3="exit 49", python=None, autodl_python=None) -> subprocess.CompletedProcess:
    """scripts/ctl through the real Git Bash, with python3 and python in front of PATH: the Windows Store's python3
    exits 49 without a word; `python=None` makes python fail too."""
    stubs = tmp_path / "stubs"
    _stub(stubs / "python3", python3)
    _stub(stubs / "python", python or "exit 9009")
    env = _git_bash_env({k: v for k, v in os.environ.items() if k not in ("MSYS_NO_PATHCONV", "MSYS2_ARG_CONV_EXCL", "AUTODL_PYTHON")})
    env["PATH"] = str(stubs) + os.pathsep + env["PATH"]
    if autodl_python:
        env["AUTODL_PYTHON"] = autodl_python
    launcher = (Path(ctl.__file__).resolve().parent / "ctl").as_posix()
    return subprocess.run([str(GIT_BASH), "-c", 'PATH="$(cygpath -u "$STUBS"):$PATH"; exec bash "$0" "$@"', launcher,
                           *[str(a) for a in argv]], capture_output=True, env={**env, "STUBS": str(stubs)}, timeout=120)


REAL_PY = 'exec "$(cygpath -u "$AUTODL_TEST_REAL_PY")" "$@"'


@git_bash_only
def test_a_hanging_nvidia_smi_does_not_hold_the_probes(tmp_path):
    stubs = tmp_path / "stubs"
    _stub(stubs / "nvidia-smi", "sleep 30")
    t0 = time.monotonic()
    r = subprocess.run([str(GIT_BASH), "-c", 'PATH="$(cygpath -u "$STUBS"):$PATH"; ' + ctl.fingerprint_probe(1)],
                       capture_output=True, env=_git_bash_env({**os.environ, "STUBS": str(stubs)}), timeout=25)
    took = time.monotonic() - t0
    parts = r.stdout.decode(errors="replace").split("===")
    assert took < 10 and len(parts) == 4 and "gpu=absent" in parts[1], (took, r.stdout, r.stderr)
    assert ctl.FINGERPRINT_PROBE == ctl.fingerprint_probe(10) and ctl.MODE_PROBE == ctl.mode_probe(10)


@git_bash_only
def test_launcher_skips_a_python_that_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODL_TEST_REAL_PY", sys.executable)
    r = _launch(tmp_path, "version", python=REAL_PY)
    assert r.returncode == 0 and r.stdout.decode().strip() == "0.10.0", r.stderr.decode(errors="replace")


@git_bash_only
def test_launcher_prefers_python3(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODL_TEST_REAL_PY", sys.executable)
    r = _launch(tmp_path, "version", python3=REAL_PY)   # python fails here: only python3 can answer
    assert r.returncode == 0 and r.stdout.decode().strip() == "0.10.0", r.stderr.decode(errors="replace")


@git_bash_only
def test_launcher_says_what_to_do_when_no_python_works(tmp_path):
    r = _launch(tmp_path, "version")
    err = r.stderr.decode(errors="replace")
    assert r.returncode == 1 and "Python" in err and "AUTODL_PYTHON" in err


@git_bash_only
def test_launcher_takes_only_autodl_python_when_given(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODL_TEST_REAL_PY", sys.executable)
    bad = _stub(tmp_path / "py dir 空格" / "badpy", "exit 3")
    r = _launch(tmp_path, "version", python=REAL_PY, autodl_python=bad.as_posix())
    assert r.returncode == 1 and "AUTODL_PYTHON" in r.stderr.decode(errors="replace")


@git_bash_only
def test_launcher_passes_paths_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTODL_TEST_REAL_PY", sys.executable)
    good = _stub(tmp_path / "py dir 空格" / "goodpy", REAL_PY)   # AUTODL_PYTHON in a directory with spaces
    odd = tmp_path / "dir with spaces 中文"
    odd.mkdir()
    r = _launch(tmp_path, "log", "note", "--instance", "demo", "--project", odd, "--field", "path=/root/autodl-tmp/x",
                autodl_python=good.as_posix())
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    rec = json.loads((odd / ".autodl" / "power_log.jsonl").read_text(encoding="utf-8"))
    assert rec["path"] == "/root/autodl-tmp/x"
    rows = odd / "rows 文件.json"   # a local file argument in such a directory reaches Python unchanged too
    rows.write_text(json.dumps([{"serial": "SN1", "instance": INSTANCE, "time": "2026-09-01 10:00:00", "amount": "0.01"}]),
                    encoding="utf-8")
    grant = ["auth", "grant", "--instance", INSTANCE, "--alias", "demo", "--usage", "both", "--budget", "none",
             "--period", "none", "--quote", "ok"]
    assert _launch(tmp_path, *grant, autodl_python=good.as_posix()).returncode == 0
    r = _launch(tmp_path, "auth", "charges", "--instance", INSTANCE, "--file", rows, autodl_python=good.as_posix())
    assert r.returncode == 0 and json.loads(r.stdout)["imported"] == 1, r.stderr.decode(errors="replace")


# ---- every remote command checks, in its own remote shell, that it has reached its instance (the review of the
# whole release, finding 4): an alias verified once can lead elsewhere later ----
WRONG = (113, b"", f"__AUTODL_WRONG_HOST__ autodl-container-{OTHER}\n".encode())
BOUND_COMMANDS = {   # every command that takes an alias, but for check, wait and doctor
    "status": [], "deploy": ["--no-autostart"], "autostart": ["install"], "arm": ["--idle", "15m"], "calibrate": [],
    "revive": [], "run": ["t1", "--cmd", "true"], "quiet": ["t1", "10m", "--reason", "x"], "tail": ["t1"],
    "keep": ["10m", "--reason", "x"], "off-when-done": ["--reason", "x"], "off-now": ["--reason", "x", "--wait", "0s"],
    "off-raw": ["--reason", "x", "--wait", "0s"], "deadline": ["1h"],
    "job": ["t1", "--req", "1111222233334444"], "manifest": [], "spec": [],
    "ticket read": [],   # of the ticket commands the one that needs no clone record; all five take the alias the same way
}


def _argv(name: str) -> list:
    return [*name.split(), "autodl-test", *BOUND_COMMANDS[name]]


def _verified(alias="autodl-test", iid=INSTANCE):
    """As ctl check ALIAS --instance IID leaves the local record."""
    with ctl.Store() as st:
        st.data["aliases"][alias] = {"instance": iid, "at": ctl.now_s()}
        st.save()


def _subcommands() -> dict:
    import argparse
    sub = next(a for a in ctl.build_parser()._actions if isinstance(a, argparse._SubParsersAction))
    return sub.choices


def test_every_command_with_an_alias_is_bound_to_an_instance_but_three():
    with_alias = {name: sp for name, sp in _subcommands().items() if any(a.dest == "alias" for a in sp._actions)}
    unbound = {name for name, sp in with_alias.items() if sp.get_default("func") in ctl.UNBOUND}
    assert unbound == {"check", "wait", "doctor"}
    top = {k for k in BOUND_COMMANDS if " " not in k}
    assert set(with_alias) - unbound == top | {"pull", "push"}   # the tests below cover each of them
    for name in set(with_alias) - unbound:   # each can be told its instance, without the local record
        assert any(a.dest == "instance" for a in with_alias[name]._actions), name
    # the ticket's commands sit one level down: every one of them takes the alias and the instance the same way
    import argparse
    nested = next(a for a in _subcommands()["ticket"]._actions if isinstance(a, argparse._SubParsersAction)).choices
    assert set(nested) == {"write", "read", "extend", "start", "clear"}
    for name, sp in nested.items():
        assert {"alias", "instance"} <= {a.dest for a in sp._actions} and sp.get_default("func") not in ctl.UNBOUND, name


@pytest.mark.parametrize("name", sorted(BOUND_COMMANDS))
def test_a_bound_command_checks_the_host_name_in_every_remote_shell(monkeypatch, capsys, clock, name):
    fake = use(monkeypatch, {}, bound=True)
    _verified()
    ctl.main(_argv(name))
    assert fake.calls and fake.checked == [INSTANCE] * len(fake.calls), (fake.checked, [c[0][-1][:80] for c in fake.calls])
    for argv, _ in fake.calls:   # right after the start marker, before anything of the command itself
        assert argv[-1].startswith(ctl.MARKER_PREFIX + ctl.host_guard(INSTANCE))


def test_the_host_check_ends_the_remote_shell_before_the_command():
    guard = ctl.host_guard(INSTANCE)
    assert f'= autodl-container-{INSTANCE} ]' in guard and "uname -n" in guard
    assert "exit 113" in guard and "__AUTODL_WRONG_HOST__" in guard and guard.endswith("; ")
    assert ctl.remote_line("true") == ctl.MARKER_PREFIX + "true"   # nothing bound: as check, wait and doctor run


@pytest.mark.parametrize("name", sorted(BOUND_COMMANDS))
def test_an_alias_nobody_verified_is_refused_before_any_connection(monkeypatch, capsys, name):
    fake = use(monkeypatch, {}, bound=True)
    assert ctl.main(_argv(name)) == ctl.EXIT_MISMATCH
    res = json.loads(capsys.readouterr().out)
    assert fake.calls == [] and "ctl check autodl-test --instance" in res["error"]


@pytest.mark.parametrize("name", sorted(BOUND_COMMANDS))
def test_a_command_that_reaches_another_host_does_nothing_and_exits_13(monkeypatch, capsys, clock, name):
    fake = use(monkeypatch, {}, default=WRONG, bound=True)
    _verified()
    assert ctl.main(_argv(name)) == ctl.EXIT_MISMATCH
    res = json.loads(capsys.readouterr().out)
    assert len(fake.calls) == 1, [c[0][-1][:80] for c in fake.calls]   # nothing follows: no retry, no next step, no wait
    assert res["instance_match"] is False and res["hostname"] == f"autodl-container-{OTHER}" and res["instance"] == INSTANCE
    assert "nothing" in res["error"] and "tell the user" in res["error"]


def test_pull_and_push_are_bound_too(tmp_path, monkeypatch, capsys, clock):
    src = tmp_path / "data.txt"
    src.write_text("x")
    dest = tmp_path / "dest"
    for argv in (["pull", "autodl-test", "/root/autodl-tmp/exp1", str(dest)],
                 ["push", "autodl-test", str(src), "/root/autodl-tmp"]):
        procs = popen_script(monkeypatch, [(0, b"", b"")], bound=True)
        assert ctl.main(argv) == ctl.EXIT_MISMATCH and procs == []      # nobody verified the alias
        assert "ctl check autodl-test --instance" in json.loads(capsys.readouterr().out)["error"]
        _verified()
        procs = popen_script(monkeypatch, [WRONG], bound=True)
        assert ctl.main(argv) == ctl.EXIT_MISMATCH
        assert len(procs) == 1 and procs[0].args[-1].startswith(ctl.MARKER_PREFIX + ctl.host_guard(INSTANCE))
        assert json.loads(capsys.readouterr().out)["hostname"] == f"autodl-container-{OTHER}"
        with ctl.Store() as st:   # for the next command of the loop
            del st.data["aliases"]["autodl-test"]
            st.save()
    assert not dest.exists() or list(dest.iterdir()) == []   # nothing was put in place


def test_the_instance_named_on_the_command_line_needs_no_record(monkeypatch, capsys, clock):
    fake = use(monkeypatch, {}, bound=True)
    _verified()   # for the one instance; the command names the other
    assert ctl.main(["keep", "autodl-test", "10m", "--reason", "x", "--instance", OTHER]) == 0
    assert fake.checked == [OTHER]
    _record().joinpath("store.json").write_text("{ not json", encoding="utf-8")   # a record that cannot be used
    fake = use(monkeypatch, {}, bound=True)
    assert ctl.main(["keep", "autodl-test", "10m", "--reason", "x", "--instance", INSTANCE]) == 0
    assert fake.checked == [INSTANCE]
    fake = use(monkeypatch, {}, bound=True)
    assert ctl.main(["keep", "autodl-test", "10m", "--reason", "x"]) == ctl.EXIT_STORE   # which instance? it cannot tell
    assert fake.calls == [] and "--instance" in capsys.readouterr().err
    assert ctl.main(["keep", "autodl-test", "10m", "--reason", "x", "--instance", "nonsense"]) == ctl.EXIT_ERR
    assert fake.calls == []


def test_check_wait_and_doctor_run_without_the_host_check(monkeypatch, capsys):
    fake = use(monkeypatch, {"nvidia-smi": (0, b"gpu 1\n", b"")}, bound=True)   # no alias is verified here
    assert ctl.main(["wait", "autodl-test", "--every", "0"]) == 0
    assert ctl.main(["check", "autodl-test"]) == 0
    assert ctl.main(["doctor", "autodl-test"]) in (0, ctl.EXIT_ERR)   # whatever this machine lacks; the alias answers
    assert fake.calls and set(fake.checked) == {None}


def test_only_the_check_itself_counts_as_the_wrong_host(monkeypatch, capsys, clock):
    _verified()
    # the marker's words in a job's log (stderr of a tail that succeeded) are just output
    use(monkeypatch, {"logtail": (0, b"", b"__AUTODL_WRONG_HOST__ autodl-container-x\n")}, bound=True)
    assert ctl.main(["tail", "autodl-test", "t1"]) == 0
    # and exit 113 without the marker is a command that failed in its own way
    use(monkeypatch, {"logtail": (113, b"", b"something else\n")}, bound=True)
    assert ctl.main(["tail", "autodl-test", "t1"]) == ctl.EXIT_ERR
    capsys.readouterr()


# ---- what the guard says about the next start is kept for the instance, so that a power-on knows whether anything
# will watch it before the arm (the review of the whole release, finding 1) ----
def _status_bytes(autostart="installed", boot_settings="gpu:900s nogpu:900s:fallback") -> bytes:
    return (f"now=1790000000\nup=20000000\nboot=1999940000\nautostart={autostart}\nboot_settings={boot_settings}\n"
            "daemon_alive=1\n").encode()


def _guard_at_boot(capsys, iid=INSTANCE) -> list:
    capsys.readouterr()
    assert ctl.main(["auth", "show", "--instance", iid]) == 0
    return json.loads(capsys.readouterr().out)["grants"][iid]["guard_at_boot"]


def _status_table(status: bytes, **more) -> dict:
    return {"nvidia-smi": (0, b"gpu 1\n", b""), "autodl_guard.sh status": (0, status, b""), **more, "true": OK}


@pytest.mark.parametrize("autostart, boot_settings, modes", [
    ("installed", "gpu:900s nogpu:900s:fallback", ["gpu", "nogpu"]),
    ("installed", "gpu:120s:fallback nogpu:120s", ["gpu", "nogpu"]),
    ("installed", "gpu:120s:dry-run nogpu:120s", ["nogpu"]),            # a dry run never shuts down
    ("installed", "gpu:900s nogpu:900s:fallback:dry-run", ["gpu"]),
    ("installed", "", []),                                              # no settings kept: a start arms nothing
    ("none", "gpu:900s nogpu:900s:fallback", []),                       # no hook: nothing runs at the start
    ("stale", "gpu:900s nogpu:900s:fallback", []),
    ("foreign", "gpu:900s nogpu:900s:fallback", []),
    ("installed", "gpu:soon nogpu", []),                                # nothing this version can read
])
def test_auth_show_names_the_modes_in_which_the_next_start_is_guarded(monkeypatch, capsys, clock, autostart,
                                                                      boot_settings, modes):
    assert _guard_at_boot(capsys) == []          # nothing is known of this instance: no mode
    _verified()
    use(monkeypatch, _status_table(_status_bytes(autostart, boot_settings)), bound=True)
    assert ctl.main(["status", "autodl-test"]) == 0
    assert _guard_at_boot(capsys) == modes
    with ctl.Store() as st:
        assert st.data["guards"] == {INSTANCE: {"autostart": autostart, "boot_settings": boot_settings, "at": ctl.now_s()}}
    assert _guard_at_boot(capsys, OTHER) == []   # per instance


def test_an_arm_and_an_autostart_command_bring_the_note_up_to_date(monkeypatch, capsys, clock):
    _verified()
    fresh = _status_bytes("installed", "")       # before the first arm: the hook is there, no settings are kept yet
    use(monkeypatch, _status_table(fresh), bound=True)
    assert ctl.main(["status", "autodl-test"]) == 0 and _guard_at_boot(capsys) == []
    armed = _status_table(_status_bytes(), **{"autodl_guard.sh arm": (0, b"armed mode=gpu idle=900s\n", b"")})
    fake = use(monkeypatch, armed, bound=True)
    assert ctl.main(["arm", "autodl-test", "--idle", "15m"]) == 0
    assert "autodl_guard.sh status" in fake.calls[-1][0][-1]           # asked once the arm had succeeded
    assert _guard_at_boot(capsys) == ["gpu", "nogpu"]
    gone = _status_table(_status_bytes("none"), **{"uninstall-autostart": (0, b"autostart uninstalled\n", b"")})
    use(monkeypatch, gone, bound=True)
    assert ctl.main(["autostart", "autodl-test", "uninstall"]) == 0 and _guard_at_boot(capsys) == []


def test_an_arm_that_failed_changes_no_note_and_asks_nothing_more(monkeypatch, capsys, clock):
    _verified()
    use(monkeypatch, _status_table(_status_bytes()), bound=True)
    assert ctl.main(["status", "autodl-test"]) == 0
    refused = _status_table(_status_bytes("none", ""), **{"autodl_guard.sh arm": (5, b"", b"already armed\n")})
    fake = use(monkeypatch, refused, bound=True)
    assert ctl.main(["arm", "autodl-test", "--idle", "15m"]) == ctl.EXIT_ARMED
    assert not any("autodl_guard.sh status" in argv[-1] for argv, _ in fake.calls)
    assert _guard_at_boot(capsys) == ["gpu", "nogpu"]


def test_the_note_never_fails_the_command(monkeypatch, capsys, clock):
    _verified()
    # the status after a good arm does not come through: the arm stays a success, and says what is not known
    lost = {"autodl_guard.sh arm": (0, b"armed mode=gpu idle=900s\n", b""), "autodl_guard.sh status": DROP, "true": OK}
    use(monkeypatch, lost, bound=True)
    assert ctl.main(["arm", "autodl-test", "--idle", "15m"]) == 0
    assert "provisional" in capsys.readouterr().err and _guard_at_boot(capsys) == []
    # a record that cannot be written: the status is still given (the instance is named, the record is not needed)
    _record().joinpath("store.json").write_text("{ not json", encoding="utf-8")
    use(monkeypatch, _status_table(_status_bytes()), bound=True)
    assert ctl.main(["status", "autodl-test", "--instance", INSTANCE]) == 0
    got = capsys.readouterr()
    assert json.loads(got.out)["autostart"] == "installed" and "provisional" in got.err


def test_a_note_that_cannot_be_brought_up_to_date_is_forgotten(monkeypatch, capsys, clock):
    # an older answer says the next start is guarded; the hook is then taken out, and the status after it is lost.
    # The old answer must not stand: a power-on that trusted it would have neither guard nor timer
    _verified()
    use(monkeypatch, _status_table(_status_bytes()), bound=True)
    assert ctl.main(["status", "autodl-test"]) == 0 and _guard_at_boot(capsys) == ["gpu", "nogpu"]
    lost = {"uninstall-autostart": (0, b"autostart uninstalled\n", b""), "autodl_guard.sh status": DROP, "true": OK}
    use(monkeypatch, lost, bound=True)
    assert ctl.main(["autostart", "autodl-test", "uninstall"]) == 0
    assert "provisional" in capsys.readouterr().err and _guard_at_boot(capsys) == []


def test_an_arm_that_went_wrong_asks_the_guard_what_the_next_start_will_do(monkeypatch, capsys, clock):
    _verified()
    use(monkeypatch, _status_table(_status_bytes()), bound=True)
    assert ctl.main(["status", "autodl-test"]) == 0
    # the arm was cut short on the instance: what it kept for the next start is gone, and the guard says so
    cut = _status_table(_status_bytes("installed", ""), **{"autodl_guard.sh arm": (1, b"", b"arm: cut short\n")})
    fake = use(monkeypatch, cut, bound=True)
    assert ctl.main(["arm", "autodl-test", "--idle", "15m"]) == ctl.EXIT_ERR
    assert "autodl_guard.sh status" in fake.calls[-1][0][-1] and _guard_at_boot(capsys) == []
    # nobody knows whether the arm ran, and the guard cannot be asked either: the old answer is dropped
    use(monkeypatch, _status_table(_status_bytes()), bound=True)
    assert ctl.main(["status", "autodl-test"]) == 0 and _guard_at_boot(capsys) == ["gpu", "nogpu"]
    use(monkeypatch, {"autodl_guard.sh arm": LOST, "autodl_guard.sh status": DROP, "true": OK}, bound=True)
    assert ctl.main(["arm", "autodl-test", "--idle", "15m"]) == ctl.EXIT_UNCERTAIN
    assert "provisional" in capsys.readouterr().err and _guard_at_boot(capsys) == []


def test_a_status_the_guard_could_not_give_forgets_the_note(monkeypatch, capsys, clock):
    _verified()
    use(monkeypatch, _status_table(_status_bytes()), bound=True)
    assert ctl.main(["status", "autodl-test"]) == 0
    # the connection fails, or the answer is lost: nothing was learned, so the note stays
    for hit, rc in ((DROP, ctl.EXIT_UNREACHABLE), (LOST, ctl.EXIT_UNCERTAIN)):
        use(monkeypatch, {"autodl_guard.sh status": hit, "true": OK}, bound=True)
        assert ctl.main(["status", "autodl-test"]) == rc and _guard_at_boot(capsys) == ["gpu", "nogpu"]
    # the instance answers and the guard does not (its script is gone): nothing will arm the next start either
    gone = {"autodl_guard.sh status": (127, b"", b"bash: autodl_guard.sh: No such file or directory\n"), "true": OK}
    use(monkeypatch, gone, bound=True)
    assert ctl.main(["status", "autodl-test"]) == ctl.EXIT_ERR and _guard_at_boot(capsys) == []


def test_what_a_status_learns_is_kept_before_anything_else_is_asked(monkeypatch, capsys, clock):
    # the second thing status asks of the instance (its mode) may fail: what the guard said, or that it did not
    # answer, must be in the record by then
    def no_answer(alias):
        raise OSError("the connection is gone")
    _verified()
    use(monkeypatch, _status_table(_status_bytes()), bound=True)
    assert ctl.main(["status", "autodl-test"]) == 0 and _guard_at_boot(capsys) == ["gpu", "nogpu"]
    gone = {"autodl_guard.sh status": (127, b"", b"bash: autodl_guard.sh: No such file or directory\n"), "true": OK}
    use(monkeypatch, gone, bound=True)
    monkeypatch.setattr(ctl, "detect_mode", no_answer)
    assert ctl.main(["status", "autodl-test"]) == ctl.EXIT_ERR
    assert _guard_at_boot(capsys) == []                  # the guard is gone: the old "guarded" does not stand
    use(monkeypatch, _status_table(_status_bytes()), bound=True)
    assert ctl.main(["status", "autodl-test"]) == ctl.EXIT_ERR
    assert _guard_at_boot(capsys) == ["gpu", "nogpu"]    # and a good answer is kept though the command then fails


def test_a_command_without_an_instance_keeps_no_note(monkeypatch, capsys):
    use(monkeypatch, _status_table(_status_bytes()))    # as the tests above this section run: bound to nothing
    assert ctl.main(["status", "autodl-test"]) == 0
    assert not _record().exists()                        # the local record was not even made


def test_a_record_from_before_the_notes_opens_and_a_bad_note_does_not(capsys, clock):
    with ctl.Store() as st:
        assert "guards" not in st.data                   # a new record has none until a status is kept
        st.save()
    assert json.loads(_record().joinpath("store.json").read_text(encoding="utf-8")).keys() == set(ctl.STORE_PARTS)
    assert _guard_at_boot(capsys) == []
    for bad in ({"autostart": "installed", "boot_settings": "gpu:900s"},                        # no time
                {"autostart": "installed", "boot_settings": "gpu:900s", "at": "now"},
                {"autostart": 1, "boot_settings": "gpu:900s", "at": 5},
                {"autostart": "installed", "boot_settings": "x" * 300, "at": 5},
                {"autostart": "installed", "boot_settings": "gpu:900s\n", "at": 5}):
        with ctl.Store() as st:
            st.data["guards"] = {INSTANCE: bad}
            with pytest.raises(ctl.StoreError):
                st.save()
    with ctl.Store() as st:
        st.data["guards"] = {"not-an-id": {"autostart": "installed", "boot_settings": "", "at": 5}}
        with pytest.raises(ctl.StoreError):
            st.save()
