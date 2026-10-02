"""The calibration helpers in tests/live. disk_write.sh runs on the instance and is tested in WSL;
queries.sh and sshread.sh run on this machine in Git Bash and are tested there against stub ssh and ctl
programs; export_logs.py is plain Python. Where WSL and Git Bash come from: tests/local_tools.py. Nothing
here connects to an instance: every ssh is a stub."""
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import local_tools  # noqa: E402

LIVE = Path(__file__).resolve().parent / "live"
GIT_BASH = local_tools.git_bash()
WSL = local_tools.wsl_command()


def _wsl(*args, **kw):
    return subprocess.run([*WSL, *args], capture_output=True, **kw)


needs_wsl = pytest.mark.skipif(not local_tools.have_wsl("dd", "timeout"),
                               reason=f"needs {local_tools.wsl_name()} with bash, dd and timeout")
needs_git_bash = pytest.mark.skipif(GIT_BASH is None, reason="needs Git Bash on Windows")


# ---- disk_write.sh: the file it writes is gone however the run ends (Codex, Phase 2 review, 5) ----
DD_STUB = """#!/bin/bash
# stands in for dd: creates the of= file, then takes DD_STUB_SLEEP seconds like a slow disk; at the
# end it creates DD_STUB_DONE if that is set
for a in "$@"; do case "$a" in of=*) : > "${a#of=}" ;; esac; done
sleep "${DD_STUB_SLEEP:-30}"
if [ -n "${DD_STUB_DONE:-}" ]; then : > "$DD_STUB_DONE"; fi
"""
RM_STUB = """#!/bin/bash
# stands in for rm: removes nothing
exit 0
"""


@pytest.fixture
def wsl_dir():
    r = _wsl("mktemp", "-d", "/tmp/autodl-live-test.XXXXXX")
    d = r.stdout.decode().strip()
    assert r.returncode == 0 and d.startswith("/tmp/autodl-live-test.")
    for name, text in (("dd", DD_STUB), ("rm", RM_STUB)):   # each stub in its own directory: $D/dd, $D/rm
        w = _wsl("bash", "-c", f"mkdir -p {d}/{name} {d}/w && cat > {d}/{name}/{name} && chmod +x {d}/{name}/{name}",
                 input=text.encode())
        assert w.returncode == 0, w.stderr
    yield d
    _wsl("rm", "-rf", "--", d)   # the real rm: only the directory this fixture created


def _disk(d, script, timeout=60):
    """Run a bash snippet in WSL with $DW = disk_write.sh, $D = the test directory, $W = $D/w (empty)."""
    r = _wsl("wslpath", "-a", str(LIVE / "disk_write.sh"))
    assert r.returncode == 0, r.stderr
    pre = f"DW={shlex.quote(r.stdout.decode().strip())}; D={shlex.quote(d)}; W=$D/w; "
    t = time.monotonic()
    r = _wsl("bash", "-c", pre + script, timeout=timeout)
    return r.stdout.decode(), r.stderr.decode(), time.monotonic() - t


@needs_wsl
def test_disk_write_leaves_no_file_after_normal_runs(wsl_dir):
    out, err, _ = _disk(wsl_dir, 'bash "$DW" "$W" 1; echo "rc=$?"; bash "$DW" "$W" 1 buffered 1; echo "rc=$?"; '
                                 'echo "left=$(ls -A "$W" | wc -l)"')
    assert out.count("LOAD START") == 2 and out.count("LOAD END") == 2, (out, err)
    assert out.count("rc=0") == 2 and "left=0" in out, (out, err)


@needs_wsl
@pytest.mark.parametrize("sig", ["TERM", "INT", "HUP"])
def test_disk_write_removes_its_file_when_timeout_ends_a_write(wsl_dir, sig):
    out, err, secs = _disk(wsl_dir, f'PATH="$D/dd:$PATH" timeout -s {sig} 2 bash "$DW" "$W" 8; echo "rc=$?"; '
                                    'echo "left=$(ls -A "$W" | wc -l)"')
    assert "LOAD START" in out and "LOAD END" not in out, (out, err)
    assert "rc=124" in out and "left=0" in out, (out, err)
    assert secs < 20, secs   # the stub dd would take 30 s: timeout's signal reached it too


@needs_wsl
def test_disk_write_removes_its_file_when_timeout_ends_the_buffered_wait(wsl_dir):
    out, err, secs = _disk(wsl_dir, 'timeout 2 bash "$DW" "$W" 1 buffered 30; echo "rc=$?"; '
                                    'echo "left=$(ls -A "$W" | wc -l)"')
    assert "rc=124" in out and "left=0" in out and secs < 20, (out, err, secs)


@needs_wsl
def test_disk_write_cleans_up_after_a_term_sent_to_the_script_alone(wsl_dir):
    # bash lets the write in progress finish (3 s here), then its trap removes the file and exits 143
    out, err, _ = _disk(wsl_dir, 'PATH="$D/dd:$PATH" DD_STUB_SLEEP=3 DD_STUB_DONE="$D/dd_done" bash "$DW" "$W" 8 & '
                                 'p=$!; sleep 1; kill -TERM "$p"; wait "$p"; echo "rc=$?"; '
                                 'echo "left=$(ls -A "$W" | wc -l)"; if [ -e "$D/dd_done" ]; then echo dd-ended-first; fi')
    assert "rc=143" in out and "left=0" in out and "dd-ended-first" in out, (out, err)


# ---- autostart_probe.profile.in: the container's boot script (PID 1) sources /etc/profile, which
# sources /etc/profile.d/*.sh; the probe writes its marker only there, never in a login shell ----
PROFILE_PROBE_CHECK = r"""
D="$1"; P="$D/p.sh"; Q="$D/q.sh"; U="$D/s.sh"; M="$D/m/probe-abcd1234"; MS="$D/m/probe-sub"
bash -n "$P"; echo "syntax=$?"
. "$P"; echo "sourced_rc=$?"
if [ -e "$M" ]; then echo "written-by-login"; else echo "not-written-by-login"; fi
unshare --user --map-root-user --pid --fork bash -c 'up=keep; idle=keep; . "$1"; echo "pid1_rc=$? vars=$up,$idle"' pid1 "$P"
echo "lines=$(wc -l < "$M")"; echo "fields1=$(sed -n 1p "$M" | wc -w)"; echo "stamp=$(sed -n 2p "$M")"; echo "now=$(date +%s)"
unshare --user --map-root-user --pid --fork bash -c '( . "$1" ); echo "sub_rc=$?"' pid1 "$U"
if [ -e "$MS" ]; then echo "written-by-subshell"; else echo "not-written-by-subshell"; fi
unshare --user --map-root-user --pid --fork bash -c '. "$1"; echo "nodir_rc=$?"' pid1 "$Q" 2> "$D/nodir.err"
echo "nodir_err_bytes=$(wc -c < "$D/nodir.err")"
"""


def _profile_probe(token, marker):
    t = (LIVE / "autostart_probe.profile.in").read_text(encoding="utf-8")
    return t.replace("@TOKEN@", token).replace("@MARKER@", marker)


@needs_wsl
def test_profile_probe_writes_its_marker_only_when_sourced_by_pid_1(wsl_dir):
    if _wsl("unshare", "--user", "--map-root-user", "--pid", "--fork", "true").returncode != 0:
        pytest.skip("this WSL cannot make a new PID namespace")
    for name, marker in (("p.sh", f"{wsl_dir}/m/probe-abcd1234"), ("q.sh", f"{wsl_dir}/no-such-dir/probe-x"),
                         ("s.sh", f"{wsl_dir}/m/probe-sub")):
        w = _wsl("bash", "-c", f"mkdir -p {wsl_dir}/m && cat > {wsl_dir}/{name}",
                 input=_profile_probe("abcd1234", marker).encode())
        assert w.returncode == 0, w.stderr
    r = _wsl("bash", "-c", PROFILE_PROBE_CHECK, "check", wsl_dir, timeout=60)
    out = dict(line.split("=", 1) for line in r.stdout.decode().split() if "=" in line)
    assert out["syntax"] == "0" and out["sourced_rc"] == "0", r
    assert "not-written-by-login" in r.stdout.decode(), r
    assert out["pid1_rc"] == "0" and out["lines"] == "2" and out["fields1"] == "2", r
    assert out["vars"] == "keep,keep", r   # it runs in a subshell: PID 1's variables are untouched
    assert abs(int(out["stamp"]) - int(out["now"])) <= 5, r
    assert out["sub_rc"] == "0" and "not-written-by-subshell" in r.stdout.decode(), r   # BASHPID, not $$
    assert out["nodir_rc"] == "0" and out["nodir_err_bytes"] == "0", r   # a missing directory stays silent


@needs_wsl
def test_disk_write_fails_when_its_file_is_still_there_at_the_end(wsl_dir):
    out, err, _ = _disk(wsl_dir, 'PATH="$D/dd:$D/rm:$PATH" DD_STUB_SLEEP=0 bash "$DW" "$W" 8; echo "rc=$?"')
    assert "LOAD END" in out and "rc=1" in out, (out, err)
    assert "could not delete" in out + err, (out, err)


# ---- queries.sh: four exact results, a holder with time left, a completion marker (review 2, 3) ----
Q_SSH_STUB = r"""#!/bin/bash
# stands in for ssh: logs its arguments, answers from files in $STUB_DIR
printf '%s\n' "$*" >> "$STUB_DIR/ssh.log"
case "${!#}" in
  true)
    if [ -f "$STUB_DIR/true_sleep" ]; then sleep "$(cat "$STUB_DIR/true_sleep")"; fi
    echo finished >> "$STUB_DIR/true_done"
    exit "$(cat "$STUB_DIR/true_rc" 2> /dev/null || echo 0)" ;;
  *etimes*)
    [ -f "$STUB_DIR/holder_age" ] || exit 1
    echo "  $(cat "$STUB_DIR/holder_age")" ;;
esac
exit 0
"""
Q_CTL_STUB = r"""#!/bin/bash
# stands in for scripts/ctl: logs its arguments; off-now answers 3 (refused) unless told otherwise
printf '%s\n' "$*" >> "$STUB_DIR/ctl.log"
if [ "$1" = off-now ]; then def=3; else def=0; fi
exit "$(cat "$STUB_DIR/ctl_$1_rc" 2> /dev/null || echo "$def")"
"""
SSH_OPTS = "-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -- demo-alias"
FIFO = "/root/calib/hold-abcd.fifo"


@pytest.fixture
def stubs(tmp_path):
    d = tmp_path / "stubs"
    d.mkdir()
    return d


def _write_stub(d, name, text):
    (d / name).write_bytes(text.encode("utf-8"))   # no newline translation (write_text's newline= needs 3.10)


def _gb_env(d, **extra):
    env = dict(os.environ, STUB_DIR=d.as_posix(), AUTODL_SSH=(d / "ssh").as_posix(),
               QUERIES_CTL=(d / "ctl").as_posix(), **extra)
    env["PATH"] = str(GIT_BASH.parent) + os.pathsep + env.get("PATH", "")   # coreutils timeout, not Windows'
    return env


def _lines(p):
    return Path(p).read_text(encoding="utf-8").splitlines() if Path(p).exists() else []


def _queries(d, out, *args, limit="1200"):
    _write_stub(d, "ssh", Q_SSH_STUB)
    _write_stub(d, "ctl", Q_CTL_STUB)
    argv = list(args) if args else ["demo-alias", out.as_posix(), "0", FIFO, limit]
    return subprocess.run([str(GIT_BASH), (LIVE / "queries.sh").as_posix(), *argv],
                          capture_output=True, env=_gb_env(d), timeout=60)


def _results(out):
    return [line.split(" ", 1)[1] for line in _lines(out)]


@needs_git_bash
@pytest.mark.parametrize("age", ["100", "1020"])
def test_queries_ok_when_the_four_results_are_as_expected(stubs, tmp_path, age):
    (stubs / "holder_age").write_text(age)
    out = tmp_path / "q.txt"
    r = _queries(stubs, out)
    assert r.returncode == 0, r.stderr
    assert _results(out) == ["start gap=0 bound=450", "ssh-true rc=0", "status rc=0", "wait rc=0",
                             "off-now-refused rc=3"]
    assert _lines(f"{out}.done") == ["ok"] and not Path(f"{out}.done.tmp").exists()
    assert [c.split()[0] for c in _lines(stubs / "ctl.log")] == ["status", "wait", "off-now"]
    ssh = _lines(stubs / "ssh.log")
    assert len(ssh) == 2 and all(s.startswith(SSH_OPTS + " ") for s in ssh), ssh
    assert f"pgrep -o -f '[c]at {FIFO}'" in ssh[1]


@needs_git_bash
def test_queries_fail_when_the_off_now_is_accepted(stubs, tmp_path):
    (stubs / "holder_age").write_text("100")
    (stubs / "ctl_off-now_rc").write_text("0")
    out = tmp_path / "q.txt"
    assert _queries(stubs, out).returncode == 1
    assert _results(out)[-1] == "off-now-refused rc=0" and _lines(f"{out}.done") == ["fail"]


@needs_git_bash
def test_queries_stop_at_the_first_unexpected_result(stubs, tmp_path):
    (stubs / "holder_age").write_text("100")
    (stubs / "ctl_status_rc").write_text("255")
    out = tmp_path / "q.txt"
    assert _queries(stubs, out).returncode == 1
    assert _results(out) == ["start gap=0 bound=450", "ssh-true rc=0", "status rc=255"]
    assert _lines(stubs / "ctl.log") == ["status demo-alias"] and len(_lines(stubs / "ssh.log")) == 1
    assert _lines(f"{out}.done") == ["fail"]


@needs_git_bash
@pytest.mark.parametrize("age,why", [(None, "holder job not running"), ("1021", "holder job has less than 180 s left")])
def test_queries_send_no_off_now_without_a_holder_that_has_time_left(stubs, tmp_path, age, why):
    if age:
        (stubs / "holder_age").write_text(age)
    out = tmp_path / "q.txt"
    assert _queries(stubs, out).returncode == 1
    assert _results(out)[-1] == f"off-now skipped: {why}" and _lines(f"{out}.done") == ["fail"]
    assert [c.split()[0] for c in _lines(stubs / "ctl.log")] == ["status", "wait"]


@needs_git_bash
@pytest.mark.parametrize("existing", ["q.txt", "q.txt.done"])
def test_queries_refuse_to_reuse_an_output_file(stubs, tmp_path, existing):
    (tmp_path / existing).write_text("old\n")
    out = tmp_path / "q.txt"
    assert _queries(stubs, out).returncode == 2
    assert _lines(tmp_path / existing) == ["old"] and not (stubs / "ssh.log").exists()


@needs_git_bash
@pytest.mark.parametrize("argv", [["demo-alias", "q.txt", "0", FIFO], ["demo-alias", "q.txt", "x", FIFO, "1200"],
                                  ["demo-alias", "q.txt", "0", FIFO, "1e3"]])
def test_queries_usage_errors(stubs, tmp_path, argv):
    argv = list(argv)
    argv[1] = (tmp_path / argv[1]).as_posix()
    assert _queries(stubs, tmp_path / "q.txt", *argv).returncode == 2
    assert not (tmp_path / "q.txt").exists() and not (stubs / "ssh.log").exists()


@needs_git_bash
def test_queries_mark_a_killed_run_after_the_query_in_flight_has_ended(stubs, tmp_path):
    (stubs / "true_sleep").write_text("3")
    _write_stub(stubs, "ssh", Q_SSH_STUB)
    _write_stub(stubs, "ctl", Q_CTL_STUB)
    out = tmp_path / "q.txt"
    script = (f'bash {shlex.quote((LIVE / "queries.sh").as_posix())} demo-alias {shlex.quote(out.as_posix())} 0 '
              f'{FIFO} 1200 & p=$!; sleep 1; kill -TERM "$p"; wait "$p"; echo "rc=$?"')
    r = subprocess.run([str(GIT_BASH), "-c", script], capture_output=True, env=_gb_env(stubs), timeout=60)
    assert "rc=143" in r.stdout.decode(), (r.stdout, r.stderr)
    assert _lines(f"{out}.done") == ["killed"]
    assert _lines(stubs / "true_done") == ["finished"]   # the query in flight ran to its end first
    assert not (stubs / "ctl.log").exists()   # and nothing was sent after it


# ---- sshread.sh: read-only, bounded, retried on ssh failures only, output kept only on success ----
S_SSH_STUB = r"""#!/bin/bash
# stands in for ssh: logs its arguments on one line (a newline shows as \n); the Nth call answers with
# line N of $STUB_DIR/answers (the last line again once they run out): "RC", "RC OUTPUT", "sleep SECONDS",
# or "run", which hands the remote command to bash here the way the instance's shell would run it
printf '%s\n' "${*//$'\n'/\\n}" >> "$STUB_DIR/ssh.log"
n=$(wc -l < "$STUB_DIR/ssh.log")
a="$(sed -n "${n}p" "$STUB_DIR/answers")"
[ -n "$a" ] || a="$(tail -n 1 "$STUB_DIR/answers")"
case "$a" in
  run) bash -c "${!#}"; exit $? ;;
  sleep\ *) sleep "${a#sleep }"; exit 0 ;;
  *\ *) printf '%s\n' "${a#* }"; exit "${a%% *}" ;;
  *) exit "$a" ;;
esac
"""
# the remote command as sshread.sh sends it: in a subshell, with 124 and 255 turned into 125
WRAPPED = " ( {}\\n) ; r=$?; case $r in 124|255) exit 125 ;; esac; exit $r"


def _sshread(d, out, answers, cmd="hostname", tries="3", lim="5", argv=None):
    _write_stub(d, "ssh", S_SSH_STUB)
    (d / "answers").write_bytes(answers.encode("utf-8"))
    env = _gb_env(d, SSHREAD_TRIES=tries, SSHREAD_GAP="0", SSHREAD_TIMEOUT=lim)
    argv = argv if argv is not None else ["demo-alias", out.as_posix(), cmd]
    t = time.monotonic()
    r = subprocess.run([str(GIT_BASH), (LIVE / "sshread.sh").as_posix(), *argv], capture_output=True, env=env,
                       timeout=60)
    return r, time.monotonic() - t


@needs_git_bash
def test_sshread_keeps_the_output_of_a_command_that_succeeds(stubs, tmp_path):
    out = tmp_path / "h.txt"
    r, _ = _sshread(stubs, out, "0 autodl-container-demo\n")
    assert r.returncode == 0, r.stderr
    assert out.read_bytes() == b"autodl-container-demo\n" and not Path(f"{out}.tmp").exists()
    assert _lines(stubs / "ssh.log") == [SSH_OPTS + WRAPPED.format("hostname")]


@needs_git_bash
@pytest.mark.parametrize("cmd,code,text", [("echo hi", 0, ["hi"]), ("exit 3", 3, None), ("exit 255", 1, None),
                                           ("exit 124", 1, None), ("if false; then :; else exit 3; fi", 3, None)])
def test_sshread_runs_the_command_as_the_instance_shell_would(stubs, tmp_path, cmd, code, text):
    # a remote exit status of 255 or 124 is not taken for an ssh failure or the local time limit
    out = tmp_path / "h.txt"
    r, _ = _sshread(stubs, out, "run\n", cmd=cmd)
    assert r.returncode == code, r.stderr
    assert len(_lines(stubs / "ssh.log")) == 1
    assert (_lines(out) == text) if text else not out.exists()


@needs_git_bash
def test_sshread_retries_ssh_failures(stubs, tmp_path):
    out = tmp_path / "h.txt"
    r, _ = _sshread(stubs, out, "255\n255\n0 x\n")
    assert r.returncode == 0 and _lines(out) == ["x"] and len(_lines(stubs / "ssh.log")) == 3


@needs_git_bash
@pytest.mark.parametrize("answers,lim", [("255\n", "5"), ("sleep 10\n", "1")])
def test_sshread_gives_up_after_the_last_try(stubs, tmp_path, answers, lim):
    out = tmp_path / "h.txt"
    r, secs = _sshread(stubs, out, answers, tries="2", lim=lim)
    assert r.returncode == 4 and len(_lines(stubs / "ssh.log")) == 2 and secs < 8, (r.stderr, secs)
    assert not out.exists() and not Path(f"{out}.tmp").exists()


@needs_git_bash
@pytest.mark.parametrize("answers,code", [("3\n", 3), ("1 oops\n", 1)])
def test_sshread_passes_on_a_remote_failure_without_retrying(stubs, tmp_path, answers, code):
    out = tmp_path / "h.txt"
    r, _ = _sshread(stubs, out, answers)
    assert r.returncode == code and len(_lines(stubs / "ssh.log")) == 1
    assert not out.exists() and not Path(f"{out}.tmp").exists()


@needs_git_bash
@pytest.mark.parametrize("existing", ["h.txt", "h.txt.tmp"])
def test_sshread_refuses_an_existing_output_file(stubs, tmp_path, existing):
    (tmp_path / existing).write_text("old\n")
    r, _ = _sshread(stubs, tmp_path / "h.txt", "0 new\n")
    assert r.returncode == 2 and _lines(tmp_path / existing) == ["old"] and not (stubs / "ssh.log").exists()


@needs_git_bash
def test_sshread_usage_error(stubs, tmp_path):
    r, _ = _sshread(stubs, tmp_path / "h.txt", "0 x\n", argv=["demo-alias", (tmp_path / "h.txt").as_posix()])
    assert r.returncode == 2 and not (stubs / "ssh.log").exists()


# ---- export_logs.py: what goes into the repository keeps only allow-listed lines (review 4) ----
RAW_LOG = (
    "=== job gpu-idle start 2026-09-29 23:00:00 +0800\n"
    "=== cmd: timeout 1860 bash /root/autodl-tmp/.autodl-guard/autodl_guard.sh sample --every 10s\n"
    "# autodl_guard 0.7.1 sample every=10 count=2 gpu_samples=3 clk_tck=100\n"
    "# epoch\tuptime_cs\tcpu_usec\tio_bytes\tnet_bytes\tgpu_max\tgpu_fail\tguard_ticks\tself_ticks\n"
    "1790700000\t100\t5\t6\t7\tna\tna\t\t3\n"
    "1790700010\t1100\t15\t16\t17\t4\t0\t2\t4\n"
    "1790700020\t2100\t25\t26\t27\t\t1\t\t5\n"
    "1790700030\t3100\t35\t36\t37\t4\t0\t\t/root/x\n"
    "LOAD START 1790700001 101.00\n"
    "Traceback (most recent call last):\n"
    '  File "/root/autodl-tmp/calib/live/gpu_duty.py", line 1\n'
    "=== env_setup failed\n"
    "LOAD END 1790700019 2099.50\n"
    "device NVIDIA GeForce RTX 3080 Ti\n"
    "=== job gpu-idle end 2026-09-29 23:31:00 +0800 rc=0\n"
)
KEPT = [0, 2, 3, 4, 5, 6, 8, 12, 13, 14]   # the line numbers above that are kept


def _export(*argv):
    return subprocess.run([sys.executable, str(LIVE / "export_logs.py"), *map(str, argv)], capture_output=True,
                          timeout=60)


def test_export_keeps_only_allow_listed_lines(tmp_path):
    src, dst = tmp_path / "raw", tmp_path / "repo"
    src.mkdir()
    (src / "gpu-idle.log").write_bytes(RAW_LOG.encode())
    (src / "load-gpu-cpu1.log").write_bytes(b"=== cmd: python x.py\nLOAD START 1 2.00\n")
    r = _export(src, dst)
    assert r.returncode == 0, r.stderr
    lines = RAW_LOG.splitlines(keepends=True)
    assert (dst / "gpu-idle.log").read_bytes() == "".join(lines[i] for i in KEPT).encode()
    assert (dst / "load-gpu-cpu1.log").read_bytes() == b"LOAD START 1 2.00\n"
    assert sorted(p.name for p in dst.iterdir()) == ["gpu-idle.log", "load-gpu-cpu1.log"]
    assert r.stdout.decode().splitlines() == ["gpu-idle.log kept=10 dropped=5", "load-gpu-cpu1.log kept=1 dropped=1",
                                              "total files=2 kept=11 dropped=6"]


def test_export_output_reads_the_same_in_the_analyzer(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
    import analyze_samples as a
    src, dst = tmp_path / "raw", tmp_path / "repo"
    src.mkdir()
    (src / "x.log").write_bytes(RAW_LOG.encode())
    assert _export(src, dst).returncode == 0
    ok = RAW_LOG.replace("1790700030\t3100\t35\t36\t37\t4\t0\t\t/root/x\n", "")   # the one row it drops
    assert a.parse_runs((dst / "x.log").read_text(encoding="utf-8").splitlines()) == a.parse_runs(ok.splitlines())
    assert a.load_marks(dst / "x.log") == a.load_marks(src / "x.log") == (10100, 209950)


def test_export_refuses_an_existing_destination(tmp_path):
    src, dst = tmp_path / "raw", tmp_path / "repo"
    src.mkdir()
    dst.mkdir()
    (src / "x.log").write_bytes(RAW_LOG.encode())
    (dst / "keep.txt").write_bytes(b"mine\n")
    r = _export(src, dst)
    assert r.returncode == 2 and b"already exists" in r.stderr, r.stderr
    assert [p.name for p in dst.iterdir()] == ["keep.txt"] and (dst / "keep.txt").read_bytes() == b"mine\n"


def test_export_refuses_anything_but_regular_files(tmp_path):
    src, dst = tmp_path / "raw", tmp_path / "repo"
    (src / "sub").mkdir(parents=True)
    (src / "x.log").write_bytes(RAW_LOG.encode())
    r = _export(src, dst)
    assert r.returncode == 1 and b"not regular files" in r.stderr and not dst.exists(), r.stderr


def test_export_usage_errors(tmp_path):
    r = _export(tmp_path)
    assert r.returncode == 2 and b"usage" in r.stderr, r.stderr
    r = _export(tmp_path / "no-such-dir", tmp_path / "repo")
    assert r.returncode == 2 and b"is not a directory" in r.stderr and not (tmp_path / "repo").exists(), r.stderr
