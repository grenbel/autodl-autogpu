"""push, pull and the off-raw checks against a real bash, tar and file system: WSL stands in for the
instance (which distribution: tests/local_tools.py). Skipped where WSL with bash, tar and python3 is not
available. Nothing leaves this machine."""
import io
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

WSL = local_tools.wsl_command()


def _wsl(*args, **kw):
    return subprocess.run([*WSL, *args], capture_output=True, **kw)


pytestmark = pytest.mark.skipif(not local_tools.have_wsl("tar", "python3"),
                                reason=f"needs {local_tools.wsl_name()} with bash, tar and python3")


INSTANCE = "abcd123456-1234abcd"
OTHER = "ffff000000-0000ffff"


class Remote:
    """A temporary directory in WSL plays the instance's disk, and the remote shell's uname -n says the instance's
    host name (a shell function, so every command's own host check runs for real)."""

    def __init__(self, root):
        self.root = root
        self.drops = 0   # the next N ssh calls fail before the command starts, like sshd MaxStartups
        self.host = f"autodl-container-{INSTANCE}"

    def sh(self, script):
        return _wsl("bash", "-c", script)

    def ls(self, rel):
        return sorted(self.sh(f"ls -A {self.root}/{rel}").stdout.decode().split())

    def cat(self, rel):
        return self.sh(f"cat {self.root}/{rel}").stdout

    def argv(self, ssh_argv):
        if self.drops > 0:   # what ssh would log for a drop before authentication; nothing runs
            self.drops -= 1
            if "-E" in ssh_argv:
                with open(ssh_argv[ssh_argv.index("-E") + 1], "a", encoding="utf-8") as f:
                    f.write("Connection closed by 203.0.113.5 port 40022\r\n")
            return [*WSL, "bash", "-c", "exit 255"]
        return [*WSL, "bash", "-c", f"uname() {{ echo {shlex.quote(self.host)}; }}; " + ssh_argv[-1]]


@pytest.fixture
def remote(monkeypatch):
    r = _wsl("mktemp", "-d", "/tmp/autodl-ctl-test.XXXXXX")
    root = r.stdout.decode().strip()
    assert r.returncode == 0 and root.startswith("/tmp/autodl-ctl-test.")
    rem = Remote(root)
    with ctl.Store() as st:   # as ctl check demo --instance ID leaves it: the commands below are bound to the instance
        st.data["aliases"]["demo"] = {"instance": INSTANCE, "at": ctl.now_s()}
        st.save()
    monkeypatch.setattr(ctl, "POPEN", lambda argv, **kw: subprocess.Popen(rem.argv(argv), **kw), raising=False)
    monkeypatch.setattr(ctl, "RUNNER", lambda argv, **kw: subprocess.run(rem.argv(argv), **kw))
    monkeypatch.setenv("AUTODL_SSH", "autodl-test-no-such-ssh")   # never a real ssh
    monkeypatch.setattr(ctl.time, "sleep", lambda s: None)
    yield rem
    _wsl("rm", "-rf", "--", root)   # only the directory this fixture created


def test_push_places_refuses_then_overwrites_keeping_a_backup(remote, tmp_path):
    src = tmp_path / "code"
    src.mkdir()
    (src / "a.py").write_text("v1")
    dest = remote.root + "/proj"
    assert ctl.main(["push", "demo", str(src), dest]) == 0
    assert remote.cat("proj/code/a.py") == b"v1"
    (src / "a.py").write_text("v2")
    assert ctl.main(["push", "demo", str(src), dest]) == ctl.EXIT_ERR
    assert remote.cat("proj/code/a.py") == b"v1"
    assert ctl.main(["push", "demo", str(src), dest, "--overwrite"]) == 0
    assert remote.cat("proj/code/a.py") == b"v2"
    names = remote.ls("proj")
    assert len(names) == 2 and names[0] == "code" and names[1].startswith("code.bak-")
    assert remote.cat(f"proj/{names[1]}/a.py") == b"v1"


def test_push_leaves_nothing_behind_when_the_stream_breaks(remote):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("code/big.bin")
        info.size = 20000
        tf.addfile(info, io.BytesIO(b"x" * 20000))
    broken = buf.getvalue()[:5000]
    script = ctl.push_remote_cmd(remote.root + "/proj", "code", False)
    r = subprocess.run([*WSL, "bash", "-c", script], input=broken, capture_output=True)
    assert r.returncode != 0
    assert remote.ls("proj") == []   # neither the target nor a temporary directory


def test_push_puts_the_old_copy_back_when_killed_after_the_backup(remote):
    remote.sh(f"mkdir -p {remote.root}/proj/code && printf old > {remote.root}/proj/code/a.py")
    script = ctl.push_remote_cmd(remote.root + "/proj", "code", True)
    anchor = 'mv -- "$P/$N" "$B" || exit 1'
    assert anchor in script
    script = script.replace(anchor, anchor + "; kill -TERM $$")   # killed right after the backup move
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("code/a.py")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"new"))
    r = subprocess.run([*WSL, "bash", "-c", script], input=buf.getvalue(), capture_output=True)
    assert r.returncode != 0
    assert remote.cat("proj/code/a.py") == b"old"
    assert remote.ls("proj") == ["code"]   # no backup and no temporary directory left


def test_push_survives_a_dropped_connection(remote, tmp_path):
    src = tmp_path / "code"
    src.mkdir()
    (src / "a.py").write_text("v1")
    remote.drops = 1
    assert ctl.main(["push", "demo", str(src), remote.root + "/proj"]) == 0
    assert remote.cat("proj/code/a.py") == b"v1"


# stands in for ssh inside WSL: runs the remote command (the last argument) right there, stdin passed on.
# umask 000 because tar on the instance runs as root and keeps the modes in the archive, where a user's tar
# (WSL's default user) would apply its umask and hide modes that were never normalized
FAKE_SSH_SCRIPT = (b'#!/bin/sh\numask 000\nfor last; do :; done\n'
                   b'exec bash -c "uname() { echo autodl-container-' + INSTANCE.encode() + b'; }; $last"\n')


def test_push_keeps_modes_on_a_real_tar(remote):
    """ctl itself runs in WSL here, a POSIX Python, so a file that is executable there keeps its execute bit."""
    root = remote.root
    setup = (f"mkdir -p {root}/src/code/sub {root}/bin && printf a > {root}/src/code/a.txt && "
             f"printf x > {root}/src/code/sub/run.sh && chmod 777 {root}/src/code/sub && chmod 666 {root}/src/code/a.txt && "
             f"chmod 775 {root}/src/code/sub/run.sh && cat > {root}/bin/ssh && chmod +x {root}/bin/ssh")
    assert subprocess.run([*WSL, "bash", "-c", setup], input=FAKE_SSH_SCRIPT, capture_output=True).returncode == 0
    r = _wsl("wslpath", "-a", Path(ctl.__file__).as_posix())
    ctl_path = r.stdout.decode().strip()
    assert r.returncode == 0 and ctl_path.startswith("/"), r.stderr
    r = remote.sh(f"AUTODL_SSH={root}/bin/ssh AUTODL_AUTOGPU_HOME={root}/gpu-home python3 {shlex.quote(ctl_path)} "
                  f"push demo {root}/src/code {root}/dest --instance {INSTANCE}")
    assert r.returncode == 0, r.stderr
    got = remote.sh(f"cd {root}/dest && stat -c '%a %n' code code/sub code/a.txt code/sub/run.sh").stdout.decode()
    assert got.splitlines() == ["755 code", "755 code/sub", "644 code/a.txt", "755 code/sub/run.sh"]


def test_a_command_does_nothing_on_another_host(remote, tmp_path, capsys):
    """The host check of every bound command, in a real shell: on the instance's own host the command runs; anywhere
    else the remote shell ends before it."""
    src = tmp_path / "code"
    src.mkdir()
    (src / "a.py").write_text("v1")
    remote.sh(f"mkdir -p {remote.root}/out/exp1 && printf v1 > {remote.root}/out/exp1/m.json")
    assert ctl.main(["tail", "demo", "guard"]) == ctl.EXIT_ERR   # it ran (and found no guard there to ask)
    for host in (f"autodl-container-{OTHER}", ""):   # another instance; a host that cannot tell its name
        remote.host = host
        capsys.readouterr()
        assert ctl.main(["tail", "demo", "guard"]) == ctl.EXIT_MISMATCH
        assert ctl.main(["push", "demo", str(src), remote.root + "/proj"]) == ctl.EXIT_MISMATCH
        assert ctl.main(["pull", "demo", remote.root + "/out/exp1", str(tmp_path / "got")]) == ctl.EXIT_MISMATCH
        said = capsys.readouterr().out
        assert said.count('"instance_match": false') == 3 and (host or "could not be read") in said
        assert remote.ls("") == ["out"] and not (tmp_path / "got" / "exp1").exists()   # nothing placed on either side
    remote.host = f"autodl-container-{INSTANCE}"
    assert ctl.main(["push", "demo", str(src), remote.root + "/proj", "--instance", OTHER]) == ctl.EXIT_MISMATCH
    assert ctl.main(["push", "demo", str(src), remote.root + "/proj"]) == 0   # the instance it was verified for
    assert remote.cat("proj/code/a.py") == b"v1"


TMUX_WITHOUT_SERVER = "#!/bin/sh\necho 'no server running on /tmp/tmux-0/default' >&2\nexit 1\n"


def _off_raw_check(remote, screen_stub):
    """Run off-raw's checks with stub screen and tmux in front of PATH; shutdown is replaced by an echo."""
    stub = remote.root + "/stubbin"
    remote.sh(f"mkdir -p {stub} && printf '%s' {shlex.quote(screen_stub)} > {stub}/screen && "
              f"printf '%s' {shlex.quote(TMUX_WITHOUT_SERVER)} > {stub}/tmux && chmod +x {stub}/screen {stub}/tmux")
    script = f"PATH={stub}:$PATH; " + ctl.OFF_RAW_CHECK + "echo WOULD_SHUTDOWN"
    return subprocess.run([*WSL, "bash", "-c", script], capture_output=True)


def test_off_raw_check_passes_on_a_clean_machine(remote):
    r = _off_raw_check(remote, "#!/bin/sh\necho 'No Sockets found in /run/screen/S-root.'\nexit 1\n")
    assert r.returncode == 0 and b"WOULD_SHUTDOWN" in r.stdout


def test_off_raw_check_refuses_a_live_screen_session(remote):
    r = _off_raw_check(remote, "#!/bin/sh\nprintf '\\t123.work\\t(Detached)\\n1 Socket in /run/screen/S-root.\\n'\nexit 1\n")
    assert r.returncode == 3 and b"screen" in r.stdout and b"WOULD_SHUTDOWN" not in r.stdout


def test_off_raw_check_refuses_when_screen_cannot_be_read(remote):
    r = _off_raw_check(remote, "#!/bin/sh\necho 'Cannot open your terminal /dev/pts/0' >&2\nexit 1\n")
    assert r.returncode == 3 and b"screen:unknown" in r.stdout and b"WOULD_SHUTDOWN" not in r.stdout


# the whole of what off-raw runs (the checks, the guard's live sample from stdin, the shutdown) in a real shell
NO_SCREEN = "#!/bin/sh\necho 'No Sockets found in /run/screen/S-root.'\nexit 1\n"
GUARD_BYTES = Path(ctl.LOCAL_GUARD).read_bytes()
NET_DEV = (b"Inter-|   Receive                                                |  Transmit\n"
           b" face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls "
           b"carrier compressed\n"
           b"    lo:    1000      10    0    0    0     0          0         0     1000      10    0    0    0     0"
           b"       0          0\n"
           b"  eth0: 5000    100    0    0    0     0          0         0      300       3    0    0    0     0"
           b"       0          0\n")


def _put(path: str, data: bytes) -> None:
    subprocess.run([*WSL, "bash", "-c", f"cat > {path}"], input=data, check=True)


class Machine:
    """What the guard reads, as an idle non-GPU container shows it (the stand-ins tests/test_guard.sh uses), with stub
    screen and tmux in front of PATH. The shutdown at the end is replaced by an echo; the sample takes 1 s and marks
    its start in g/bar."""

    def __init__(self, remote, screen_stub=NO_SCREEN):
        self.remote, self.g = remote, remote.root + "/g"
        remote.sh(f"mkdir -p {self.g}/cg {self.g}/bin {self.g}/bar")
        self.cpu(1000000)
        _put(f"{self.g}/cg/io.stat", b"9:0 rbytes=0 wbytes=0 rios=0 wios=0 dbytes=0 dios=0\n")
        _put(f"{self.g}/net_dev", NET_DEV)
        _put(f"{self.g}/mem", b"2147483648\n")
        _put(f"{self.g}/uptime", b"1000.00 0.00\n")
        _put(f"{self.g}/bin/screen", screen_stub.encode())
        _put(f"{self.g}/bin/tmux", TMUX_WITHOUT_SERVER.encode())
        remote.sh(f"chmod +x {self.g}/bin/screen {self.g}/bin/tmux")
        env = (f"export PATH={self.g}/bin:$PATH AUTODL_TEST_CGROUP_DIR={self.g}/cg AUTODL_TEST_NET_DEV={self.g}/net_dev "
               f"AUTODL_CGROUP_MEM_FILE={self.g}/mem AUTODL_TEST_UPTIME={self.g}/uptime "
               f"AUTODL_NVIDIA_SMI_CMD={self.g}/bin/no-nvidia-smi AUTODL_GUARD_HOME={self.g}/home "
               f"AUTODL_TEST_OFFNOW_BARRIER={self.g}/bar; ")
        self.argv = [*WSL, "bash", "-c", env + ctl.off_raw_script(1, final="echo WOULD_SHUTDOWN")]

    def cpu(self, usec: int) -> None:
        _put(f"{self.g}/cg/cpu.stat", f"usage_usec {usec}\nuser_usec {usec}\nsystem_usec 0\n".encode())

    def has(self, rel: str) -> bool:
        return self.remote.sh(f"test -e {self.g}/{rel}").returncode == 0

    def run(self, script=GUARD_BYTES):
        return subprocess.run(self.argv, input=script, capture_output=True, timeout=120)


def test_off_raw_shuts_an_idle_machine_down_after_its_live_sample(remote):
    m = Machine(remote)
    r = m.run()
    assert r.returncode == 0 and b"WOULD_SHUTDOWN" in r.stdout, (r.stdout, r.stderr)
    assert m.has("bar/sample-started.1") and not m.has("home")   # the sample was taken, and it made nothing


def test_off_raw_refuses_work_that_no_session_and_no_job_shows(remote):
    import threading
    m = Machine(remote)
    remote.sh(f"touch {m.g}/bar/sample-started.hold")   # the sample waits after its first reading
    got = []
    p = subprocess.Popen(m.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    t = threading.Thread(target=lambda: got.append(p.communicate(GUARD_BYTES)), daemon=True)
    t.start()
    for _ in range(150):
        if m.has("bar/sample-started.1"):
            break
        time.sleep(0.2)
    else:
        p.kill()
        pytest.fail("the live sample never started")
    m.cpu(5001000000)   # a computation someone started by hand, say in Jupyter: no screen, no tmux, no registered job
    remote.sh(f"touch {m.g}/bar/sample-started.1.go")
    t.join(timeout=60)
    assert got and p.returncode == 3, (p.returncode, got)
    out = got[0][0]
    assert b"refused: in use or cannot tell" in out and b"cpu:busy" in out and b"WOULD_SHUTDOWN" not in out


def test_off_raw_refuses_what_its_sample_cannot_read_and_a_session_before_any_sample(remote):
    m = Machine(remote)
    remote.sh(f"rm {m.g}/cg/cpu.stat")
    r = m.run()
    assert r.returncode == 3 and b"cpu:unknown" in r.stdout and b"WOULD_SHUTDOWN" not in r.stdout
    live = "#!/bin/sh\nprintf '\\t123.work\\t(Detached)\\n1 Socket in /run/screen/S-root.\\n'\nexit 1\n"
    remote.sh(f"rm -rf {m.g}")
    m = Machine(remote, screen_stub=live)
    r = m.run()
    assert r.returncode == 3 and b"screen" in r.stdout and b"WOULD_SHUTDOWN" not in r.stdout
    assert not m.has("bar/sample-started.1")   # refused by the session: no sample was needed


@pytest.mark.parametrize("script", [b"", GUARD_BYTES[:20000], b"echo 'idle: looks fine'; exit 1\n", b"exit 0\n"],
                         ids=["nothing arrived", "cut short", "idle in words, but failing", "silent success"])
def test_off_raw_goes_on_only_after_the_sample_itself_said_idle(remote, script):
    r = Machine(remote).run(script)   # bash ends an empty or cut-off script quietly with 0: that is not an answer
    assert r.returncode == 3 and b"refused" in r.stdout and b"WOULD_SHUTDOWN" not in r.stdout, (r.stdout, r.stderr)


def test_off_raw_keeps_the_script_from_what_its_checks_start(remote):
    greedy = "#!/bin/sh\ncat > /dev/null\necho 'No Sockets found in /run/screen/S-root.'\nexit 1\n"   # it reads its stdin
    r = Machine(remote, screen_stub=greedy).run()
    assert r.returncode == 0 and b"WOULD_SHUTDOWN" in r.stdout, (r.stdout, r.stderr)


# `screen -ls` on the test instance, whose /run/screen kept the sockets of 13 earlier boots
# (docs/tests/2026-09-30-phase4-live.md, section 4); it exited 0 there. In screen's source a listing ends with
# exit 1 in 4.2.0 and exit 0 in 4.6.2 and the current source; "No Sockets found" is exit 1 in all three.
DEAD_NAMES = ("4282.autodl-guard", "1295.autodl-guard", "1482.autodl-guard", "1290.autodl-guard", "1278.autodl-guard",
              "1447.autodl-guard", "1973.aj-gpu-idle", "1286.autodl-guard", "1180.autodl-guard", "1156.autodl-guard",
              "1171.autodl-guard", "1329.autodl-guard", "1282.autodl-guard")


def _listing(entries, count=None):
    """What `screen -ls` prints for these (name, status) pairs, with the line endings screen writes."""
    out = b"There are screens on:\r\n" if len(entries) > 1 else b"There is a screen on:\r\n"
    out += b"".join(f"\t{name}\t(02/10/26 20:13:07)\t({status})\n".encode() for name, status in entries)
    if any(status == "Dead ???" for _, status in entries):
        out += b"Remove dead screens with 'screen -wipe'.\r\n"
    n = len(entries) if count is None else count
    return out + f"{n} Socket{'s' if n > 1 else ''} in /run/screen/S-root.\r\n".encode()


INSTANCE_DEAD_ONLY = _listing([(name, "Dead ???") for name in DEAD_NAMES])


def _screen_stub(remote, out: bytes, rc: int) -> str:
    """A screen that prints `out` byte for byte and exits with rc."""
    path = remote.root + "/screen_out"
    subprocess.run([*WSL, "bash", "-c", f"cat > {path}"], input=out, check=True)
    return f"#!/bin/sh\ncat {path}\nexit {rc}\n"


@pytest.mark.parametrize("out, rc", [
    (INSTANCE_DEAD_ONLY, 0),
    (INSTANCE_DEAD_ONLY, 1),
    (INSTANCE_DEAD_ONLY.replace(b")\n", b")\r\n"), 0),   # through a terminal every line ends in CR LF
], ids=["exit-0", "exit-1", "crlf"])
def test_off_raw_check_ignores_dead_screen_sockets(remote, out, rc):
    r = _off_raw_check(remote, _screen_stub(remote, out, rc))
    assert r.returncode == 0 and b"WOULD_SHUTDOWN" in r.stdout, r.stdout


@pytest.mark.parametrize("status", ["Detached", "Attached", "Multi, detached", "Remote or dead", "Private"])
def test_off_raw_check_refuses_a_live_session_among_dead_ones(remote, status):
    entries = [(name, "Dead ???") for name in DEAD_NAMES[:3]] + [("5555.work (Dead ???)", status)]
    r = _off_raw_check(remote, _screen_stub(remote, _listing(entries), 0))
    assert r.returncode == 3 and b"tell: screen (" in r.stdout and b"WOULD_SHUTDOWN" not in r.stdout, r.stdout


@pytest.mark.parametrize("out, rc", [
    (b"\x8f\x02not a listing\xff\n", 0),
    (INSTANCE_DEAD_ONLY[:INSTANCE_DEAD_ONLY.index(b"\t1447")], 124),   # cut short by the 10 s timeout
    (b"No Sockets found in /run/screen/S-root.\r\n\r\n", 0),   # screen never ends that with exit 0
    (_listing([(name, "Dead ???") for name in DEAD_NAMES], count=14), 0),   # more sockets than lines
    (INSTANCE_DEAD_ONLY[:INSTANCE_DEAD_ONLY.index(b"13 Sockets")], 0),   # no count line: not the whole list
    (INSTANCE_DEAD_ONLY, 2),
], ids=["garbled", "timed-out", "no-sockets-exit-0", "count-mismatch", "no-count-line", "exit-2"])
def test_off_raw_check_refuses_a_screen_listing_it_cannot_read(remote, out, rc):
    r = _off_raw_check(remote, _screen_stub(remote, out, rc))
    assert r.returncode == 3 and b"tell: screen:unknown (" in r.stdout and b"WOULD_SHUTDOWN" not in r.stdout, r.stdout


def test_pull_roundtrip_and_backup(remote, tmp_path):
    remote.sh(f"mkdir -p {remote.root}/out/exp1 && printf v1 > {remote.root}/out/exp1/m.json")
    assert ctl.main(["pull", "demo", remote.root + "/out/exp1", str(tmp_path)]) == 0
    assert (tmp_path / "exp1" / "m.json").read_text() == "v1"
    remote.sh(f"printf v2 > {remote.root}/out/exp1/m.json")
    assert ctl.main(["pull", "demo", remote.root + "/out/exp1", str(tmp_path), "--overwrite"]) == 0
    assert (tmp_path / "exp1" / "m.json").read_text() == "v2"
    backups = [p for p in tmp_path.iterdir() if p.name.startswith("exp1.bak-")]
    assert len(backups) == 1 and (backups[0] / "m.json").read_text() == "v1"


def test_pull_keeps_the_local_copy_when_tar_fails_midway(remote, tmp_path):
    out = remote.root + "/out/exp1"
    remote.sh(f"mkdir -p {out} && printf new > {out}/a.json && printf secret > {out}/z.bin && chmod 000 {out}/z.bin")
    (tmp_path / "exp1").mkdir()
    (tmp_path / "exp1" / "a.json").write_text("old")
    assert ctl.main(["pull", "demo", out, str(tmp_path), "--overwrite"]) == ctl.EXIT_ERR
    assert (tmp_path / "exp1" / "a.json").read_text() == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["exp1", "gpu-home"]   # the copy and this test's local record
