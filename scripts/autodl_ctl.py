#!/usr/bin/env python3
"""Local helper of the autodl-gpu skill.

Everything here goes over SSH with key-based login (BatchMode, never a password)
or touches the project's power log. Powering an instance ON happens in the AutoDL
web console (see SKILL.md); this helper waits for the instance, drives the
instance-side guard, moves files, and keeps the bookkeeping. Whether an instance
is really off, and what it really cost, is read from the console, not from here.

Exit codes: 0 ok, 1 error (a usage error too), 2 unreachable (the command did not run), 3 refused
(still in use), 4 refused (a shutdown is pending), 5 refused (already armed for this boot),
6 uncertain (the command may or may not have run; check status before repeating it), 7 refused
(the deadline has passed), 8 refused (an off-now is being prepared); 10 to 12 are the budget
gate's and the local record's, 13 means the alias is not verified or leads to another instance.

An alias is only a way to connect; which instance it reaches can change. So every command that
takes one (but check, wait and doctor) is bound to an instance, the one named with --instance or
the one the alias was verified for, and each of its remote shells first compares the host name
with autodl-container-<ID>: on any other host it ends there, having done nothing (exit 13).

sshd on AutoDL's public ports drops new connections at random before authentication
when scanners crowd the port (MaxStartups). The client then sees only "Connection
closed" and the remote command never ran. So every remote command first prints a
marker on stderr, and ssh writes its own messages to a log file (-E, LogLevel=VERBOSE).
Exit 255 with no marker counts as "did not run" only when that log shows a failure
before authentication and no sign of a session; anything else is "uncertain". A command
is sent again when it did not run; when uncertain, only if repeating it is harmless; and
never once its marker arrived (a timeout included).
"""
from __future__ import annotations

import argparse
import datetime as dt
import errno
import hashlib
import json
import math
import os
import pathlib
import re
import secrets
import shlex
import shutil
import stat
import statistics
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

CTL_VERSION = "0.8.0"
GUARD_HOME = "/root/autodl-tmp/.autodl-guard"
GUARD_PATH = GUARD_HOME + "/autodl_guard.sh"
LOCAL_GUARD = pathlib.Path(__file__).resolve().parent / "autodl_guard.sh"
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3"]
SSH_LOG_OPTS = ["-o", "LogLevel=VERBOSE"]
MARKER = "__AUTODL_CMD_START__"
MARKER_PREFIX = f"echo {MARKER} >&2; "
WRONG_HOST = "__AUTODL_WRONG_HOST__"
WRONG_HOST_RC = 113   # nothing that is sent to an instance ends with this status by itself
BOUND: str | None = None   # the instance every remote command of this run must reach (main sets it; None for check,
# wait and doctor, which run before an alias is verified or need no instance)
# ssh log lines that mean the connection failed before a session existed (nothing ran), in the
# wording OpenSSH uses only at that stage ("... by HOST port N", "connect to host H port P: ") ...
PRE_AUTH_FAILURE = re.compile(r"Connection closed by \S+ port \d+|Connection reset by \S+ port \d+|"
                              r"kex_exchange_identification|banner exchange|connect to host \S+ port \d+: |"
                              r"Could not resolve hostname|Permission denied \(|Host key verification failed")
# ... and lines that show a session did exist, after which the command may have run
POST_AUTH_SIGN = re.compile(r"Authenticated to|client_loop|channel \d+|Read from remote host|"
                            r"Timeout, server|Connection to \S+ closed")
SSH_TRIES = 3       # attempts when ssh fails before the remote command starts
SSH_RETRY_GAP = 5   # seconds between those attempts
DOWN_PROBES = 5     # failed probes in a row before the instance counts as unreachable
MAX_DURATION_S = 30 * 86400
DUR_RE = re.compile(r"^[0-9]{1,7}[smh]?\Z")
ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}\Z")
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
GIT_BASH_DRIVE_RE = re.compile(r"^/([A-Za-z])(?:/(.*))?$")
NOGPU_MEM_MAX = 3 * 1024 ** 3  # AutoDL non-GPU mode is capped at 2 GiB
RELEASE_DAYS = 15  # AutoDL releases an ordinary instance after 15 days powered off
EXIT_ERR, EXIT_UNREACHABLE, EXIT_REFUSED, EXIT_PENDING, EXIT_ARMED, EXIT_UNCERTAIN = 1, 2, 3, 4, 5, 6
EXIT_PAST_DEADLINE, EXIT_GATED = 7, 8   # the guard's own refusals, passed through
EXIT_BUDGET, EXIT_STORE, EXIT_NO_GRANT, EXIT_MISMATCH = 10, 11, 12, 13   # the local record and the instance check
INSTANCE_RE = re.compile(r"^[0-9a-z]{10}-[0-9a-f]{8}\Z")   # an instance ID as the console writes it
# a line of the guard's off-now output that shows the shutdown was committed (whole lines only: a reason can
# contain the same words); a dry run commits too but never shuts down
COMMIT_LINE = re.compile(r"shutdown (?:committed \(.*\)|issuing|issued \(.*\))")
DRY_RUN_LINE = re.compile(r"dry-run: shutdown not executed \(.*\)")
UNREACHABLE_NOTE = (f"SSH failed {DOWN_PROBES} times in a row. That is not proof of a shutdown: "
                    "confirm in the AutoDL console that the instance is off.")
def _nvsmi_limit(limit_s: int) -> str:
    """Sets $t so that "$t nvidia-smi" runs under a time limit where the instance has timeout (a hung driver must not
    hold the probe, as in the guard's own probes); without timeout, nvidia-smi runs as it is."""
    return f't=; command -v timeout >/dev/null 2>&1 && t="timeout {int(limit_s)}"; '


def mode_probe(limit_s: int = 10) -> str:
    """gpu N when nvidia-smi lists GPUs; otherwise the cgroup memory limit, which is 2 GiB in AutoDL's non-GPU mode.
    Anything else is "unknown", never a guess: an nvidia-smi that timed out lists nothing, and a GPU instance's
    memory limit is far above 2 GiB."""
    return (_nvsmi_limit(limit_s) + 'n=$($t nvidia-smi -L 2>/dev/null | grep -c "^GPU "); '
            'if [ "${n:-0}" -gt 0 ]; then echo "gpu $n"; exit 0; fi; '
            'm=$(head -n 1 /sys/fs/cgroup/memory.max 2>/dev/null || '
            'head -n 1 /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null); '
            'echo "nogpu-check ${m:-none}"')


MODE_PROBE = mode_probe()
RUNNER = subprocess.run  # replaced in tests
POPEN = subprocess.Popen  # replaced in tests


def now_s() -> int:
    """The current unix time in whole seconds. Every time that goes into a record or a decision is read here, so
    tests can set it; timeouts use time.monotonic and backup names time.strftime."""
    return int(time.time())


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def text(b) -> str:
    return (b or b"").decode("utf-8", errors="replace")


def parse_duration_s(d: str) -> int:
    """90s, 30m, 2h or plain minutes; at most 30 days, like the guard."""
    if not DUR_RE.match(d or ""):
        raise ValueError(f"bad duration {d!r}: use 90s, 30m, 2h or plain minutes")
    n = int(d.rstrip("smh"))
    s = n if d.endswith("s") else n * 3600 if d.endswith("h") else n * 60
    if s > MAX_DURATION_S:
        raise ValueError(f"duration {d!r} is longer than 30 days")
    return s


def normalize_local(p: str, windows: bool | None = None) -> str:
    """A local path as given in Git Bash (/c/Users/...) or with ~, made usable by Windows Python."""
    windows = os.name == "nt" if windows is None else windows
    p = os.path.expanduser(p)
    m = GIT_BASH_DRIVE_RE.match(p) if windows else None
    return f"{m.group(1).upper()}:/{m.group(2) or ''}" if m else p


def refuse_windows_path(what: str, value: str | None) -> None:
    """Values meant for the instance never start with a drive letter. If one does, Git Bash
    has rewritten an argument that started with / before it reached Python."""
    if value and WINDOWS_PATH_RE.match(value):
        raise ValueError(f"{what} {value!r} looks like a Windows path, but it is meant for the instance. "
                         "Git Bash rewrites arguments that start with / before they reach Python; "
                         "run the helper through scripts/ctl, which turns that rewriting off")


def find_ssh() -> str:
    env = os.environ.get("AUTODL_SSH")
    if env:
        return env
    found = shutil.which("ssh")
    if found:
        return found
    for cand in ("C:/Windows/System32/OpenSSH/ssh.exe", "C:/Program Files/Git/usr/bin/ssh.exe"):
        if os.path.exists(cand):
            return cand
    raise OSError("ssh not found: install OpenSSH or set AUTODL_SSH to the full path of ssh")


def check_alias(alias: str) -> str:
    if not ALIAS_RE.match(alias or ""):
        raise ValueError(f"bad ssh alias {alias!r}: use a Host name from ~/.ssh/config")
    return alias


def ssh_argv(alias: str, remote_cmd: str, log: str | None = None) -> list:
    extra = ["-E", log, *SSH_LOG_OPTS] if log else []
    return [find_ssh(), *SSH_OPTS, *extra, "--", check_alias(alias), remote_cmd]


class WrongHost(Exception):
    """A remote command found itself on another host than the instance this run is bound to. It did nothing there."""

    def __init__(self, alias: str, host: str):
        super().__init__(alias, host)
        self.alias, self.host = alias, host


class Unverified(Exception):
    """No instance is known for the alias (it was never verified, and none was named): nothing is sent."""


def host_guard(iid: str) -> str:
    """The start of every remote command of a run that is bound to an instance: the container's host name must be
    autodl-container-<ID>, or the remote shell ends right there with exit 113 and a line that names the host it is.
    The check and the command share one remote shell, so nothing can change between them: an alias verified once
    (check ALIAS --instance ID) may lead elsewhere later, when ~/.ssh/config or the instance's port has changed."""
    return (f'[ "$(uname -n 2>/dev/null)" = autodl-container-{iid} ] || '   # no variable is left for the command
            f'{{ echo "{WRONG_HOST} $(uname -n 2>/dev/null)" >&2; exit {WRONG_HOST_RC}; }}; ')


def remote_line(cmd: str) -> str:
    """What ssh is asked to run: the start marker, the host check when this run is bound to an instance, the command."""
    return MARKER_PREFIX + (host_guard(BOUND) if BOUND else "") + cmd


def wrong_host(rc: int, err: bytes) -> str | None:
    """The host name the check of host_guard reported when it ended the remote shell (exit 113 and its line on
    stderr; "" when the name itself could not be read), else None. The line alone, in a job's output say, is not it."""
    if rc != WRONG_HOST_RC:
        return None
    for line in (err or b"").splitlines():
        s = line.decode("utf-8", errors="replace").strip()
        if s == WRONG_HOST or s.startswith(WRONG_HOST + " "):
            return s[len(WRONG_HOST):].strip()
    return None


class SshLog:
    """A fresh file for ssh -E: ssh's own messages go there, so stderr carries only the
    remote command's output, and the log tells whether a session ever existed."""

    def __enter__(self):
        fd, self.path = tempfile.mkstemp(prefix="autodl-ssh-", suffix=".log")
        os.close(fd)
        self.arg = pathlib.Path(self.path).as_posix()   # both Windows OpenSSH and Git's ssh take this form
        return self

    def read(self) -> str:
        try:
            return pathlib.Path(self.path).read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


def classify(rc: int, started: bool, log: str) -> str:
    """started: the marker arrived (or ssh itself did not fail); not_run: the log shows a
    failure before any session; uncertain: everything else, including unknown failures."""
    if started or rc != 255:
        return "started"
    if PRE_AUTH_FAILURE.search(log) and not POST_AUTH_SIGN.search(log):
        return "not_run"
    return "uncertain"


def log_tail(log: str, n: int = 3) -> str:
    return " | ".join(line.strip() for line in log.strip().splitlines()[-n:])


class Result:
    """What one remote command did: exit code, output, whether it started, attempts used,
    and the state (started, not_run or uncertain) with ssh's own log."""

    def __init__(self, rc: int, stdout: bytes, stderr: bytes, started: bool, attempts: int,
                 state: str = "started", log: str = ""):
        self.rc, self.stdout, self.stderr, self.started, self.attempts = rc, stdout, stderr, started, attempts
        self.state, self.log = state, log
        self.authenticated = bool(POST_AUTH_SIGN.search(log))


def split_marker(err: bytes) -> tuple[bool, bytes]:
    """Did the remote command start (its shell printed the marker)? Also return stderr without the marker."""
    lines = (err or b"").splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip(b"\r\n") == MARKER.encode():
            return True, b"".join(lines[:i] + lines[i + 1:])
    return False, err or b""


def ssh_run(alias: str, remote_cmd: str, *, stdin: bytes | None = None, timeout: int = 60,
            tries: int = SSH_TRIES, retry_uncertain: bool = False) -> Result:
    """Run one remote command. It is sent again while ssh fails before any session existed
    (the command did not run). An uncertain outcome is sent again only with retry_uncertain,
    for commands that are harmless to repeat. Once the marker arrived nothing is repeated.
    WrongHost when the run is bound to an instance and the alias led to another host."""
    for attempt in range(1, tries + 1):
        with SshLog() as lg:
            try:
                r = RUNNER(ssh_argv(alias, remote_line(remote_cmd), lg.arg), input=stdin,
                           capture_output=True, timeout=timeout)
                rc, out_b, err_b, timed_out = r.returncode, r.stdout or b"", r.stderr, False
            except subprocess.TimeoutExpired as e:   # the command may well have run: never "did not run"
                rc, out_b, err_b, timed_out = -1, e.stdout or b"", e.stderr or b"", True
            log = lg.read()
        started, err = split_marker(err_b)
        host = wrong_host(rc, err)
        if host is not None:   # the remote shell ended at its host check: nothing ran, and nothing is to follow
            raise WrongHost(alias, host)
        state = "uncertain" if timed_out else classify(rc, started, log)
        # a command whose marker arrived has started: it is never sent again, not even after a timeout
        again = not started and (state == "not_run" or (retry_uncertain and state == "uncertain"))
        if not again or attempt == tries:
            if attempt > 1:
                print(f"note: ssh needed {attempt} attempts (connections that failed before the command confirmed "
                      "that it started)", file=sys.stderr)
            return Result(rc, out_b, err, started, attempt, state, log)
        time.sleep(SSH_RETRY_GAP)
    raise AssertionError("unreachable")


def guard_cmd(*args: str) -> str:
    return " ".join(["bash", shlex.quote(GUARD_PATH), *(shlex.quote(a) for a in args)])


def passthrough(r: Result) -> int:
    sys.stdout.write(text(r.stdout))
    err = text(r.stderr)
    sys.stderr.write(err)
    if "unknown option" in err or "unknown command" in err:
        print("hint: the guard on the instance may be older than this helper; "
              "run deploy, then revive --restart", file=sys.stderr)
    if r.state == "not_run":
        print(f"ssh did not get through ({r.attempts} attempts); the command did not run. ssh: {log_tail(r.log)}",
              file=sys.stderr)
        return EXIT_UNREACHABLE
    if r.state == "uncertain" or r.rc == 255:
        what = ("the command started, but ssh ended before it reported back (timeout or lost connection): "
                "it may or may not have taken effect" if r.started else
                "the connection failed before the command reported back: it may or may not have run")
        print(f"{what}; check status before repeating it. ssh: {log_tail(r.log)}", file=sys.stderr)
        return EXIT_UNCERTAIN
    # the guard's own 6: it cannot tell whether a job started (its message says what to check)
    known = {0: 0, 3: EXIT_REFUSED, 4: EXIT_PENDING, 5: EXIT_ARMED, 6: EXIT_UNCERTAIN, 7: EXIT_PAST_DEADLINE,
             8: EXIT_GATED}
    return known.get(r.rc, EXIT_ERR)


def reachable(alias: str, tries: int = SSH_TRIES) -> bool:
    """True once the instance let us in: the probe started, or ssh authenticated."""
    try:
        r = ssh_run(alias, "true", timeout=25, tries=tries, retry_uncertain=True)
    except subprocess.TimeoutExpired:
        return False
    return r.started or r.rc == 0 or r.authenticated


def watch_down(alias: str, limit: float, gap: float) -> tuple[str, int]:
    """Single probes until DOWN_PROBES fail in a row ("unreachable") or the time limit
    passes ("timeout" after failures, "still reachable" after a success). A random drop by
    sshd must not pass for a stopped instance; even then it is not proof of a shutdown."""
    failed = 0
    while True:
        if reachable(alias, tries=1):
            failed = 0
        else:
            failed += 1
            if failed >= DOWN_PROBES:
                return "unreachable", failed
        left = limit - time.monotonic()
        if left <= 0:
            return ("timeout" if failed else "still reachable"), failed
        time.sleep(min(gap, left))


def mode_of(probe_out: str) -> tuple[str, int]:
    """MODE_PROBE's answer: ("gpu", N), ("nogpu", 0) or ("unknown", 0), never a guess."""
    parts = probe_out.split()
    if len(parts) >= 2 and parts[0] == "gpu" and parts[1].isdigit() and int(parts[1]) > 0:
        return "gpu", int(parts[1])
    if len(parts) >= 2 and parts[0] == "nogpu-check" and parts[1].isdigit() and int(parts[1]) <= NOGPU_MEM_MAX:
        return "nogpu", 0
    return "unknown", 0


def detect_mode(alias: str) -> tuple[str, int]:
    return mode_of(text(ssh_run(alias, MODE_PROBE, retry_uncertain=True).stdout))


def parse_status(s: str) -> dict:
    st: dict = {"jobs": []}
    for line in s.splitlines():
        key, sep, val = line.partition("=")
        if not sep:
            continue
        if key.startswith("job."):
            state, start, end, log = (val.split("|", 3) + ["", "", "", ""])[:4]
            st["jobs"].append({"name": key[4:], "state": state, "start": start, "end": end, "log": log})
        else:
            st[key] = val
    return st


# ---- the local record (plan 5.2, 5.9): outside every project, private, locked, never repaired ----
STORE_SCHEMA = 1
STORE_PARTS = ("schema", "last_seen", "aliases", "grants", "ledger", "calib")
LOCK_WAIT_S = 30.0   # tests make it short
STORE_TMP_PREFIX = ".store.json.tmp-"
SYSTEM_SID, ADMINS_SID = "S-1-5-18", "S-1-5-32-544"
SDDL_ALIASES = {"SY": SYSTEM_SID, "BA": ADMINS_SID, "WD": "S-1-1-0", "AU": "S-1-5-11", "BU": "S-1-5-32-545",
                "OW": "S-1-3-4", "CO": "S-1-3-0", "IU": "S-1-5-4", "AN": "S-1-5-7"}
REQ_RE = re.compile(r"^[0-9a-f]{16}\Z")
SESSION_RE = re.compile(r"^(?:[0-9a-f]{16}|m[0-9a-f]{16}|t[0-9]{1,12}-[0-9]{1,4})\Z")
SERIAL_RE = re.compile(r"^[0-9A-Za-z_.-]{1,64}\Z")
PERIOD_KEY_RE = re.compile(r"^[0-9]{4}-(?:0[1-9]|1[0-2])\Z")
TZ_RE = re.compile(r"^[+-](?:0[0-9]|1[0-4]):00\Z")
_O_BINARY, _O_NOFOLLOW = getattr(os, "O_BINARY", 0), getattr(os, "O_NOFOLLOW", 0)


class StoreError(Exception):
    """The local record cannot be used; it is never repaired automatically (exit 11). kind: repo (it would be inside
    a git repository), unsafe (a link, or others could use it), locked (another ctl holds it), invalid (its content),
    io (it could not be made, read or written)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _hook(point: str, path=None) -> None:
    """Tests replace this to hold a process at a named point; it does nothing otherwise."""


def empty_store() -> dict:
    return {"schema": STORE_SCHEMA, "last_seen": 0, "aliases": {}, "grants": {}, "ledger": {}, "calib": {}}


def store_home() -> pathlib.Path:
    """~/.autodl-gpu, or AUTODL_GPU_HOME. Refused when it, or any directory above it, holds a .git (a directory, or a
    worktree's file), or when that cannot be told: the record must stay out of every project."""
    raw = os.environ.get("AUTODL_GPU_HOME") or str(pathlib.Path.home() / ".autodl-gpu")
    given = normalize_local(raw)
    # before Python 3.13, Windows takes \x or /x as absolute, though it follows the current drive
    if not os.path.isabs(given) or (os.name == "nt" and not os.path.splitdrive(given)[0]):
        raise StoreError("unsafe", f"AUTODL_GPU_HOME={raw!r} is not an absolute path: the local record would move with "
                                   "the working directory; give it an absolute path (on Windows with a drive letter "
                                   "or a UNC share, like C:/Users/NAME/.autodl-gpu or /c/Users/NAME/.autodl-gpu)")
    home = pathlib.Path(os.path.abspath(normalize_local(raw)))
    real = pathlib.Path(os.path.realpath(home))
    hint = "set AUTODL_GPU_HOME to a directory outside every git repository"
    for p in (real, *real.parents):
        try:
            os.lstat(p / ".git")
        except FileNotFoundError:
            continue
        except OSError as e:
            raise StoreError("repo", f"cannot tell whether the local record {home} would be inside a git repository "
                                     f"({p}: {e}); {hint}") from e
        raise StoreError("repo", f"the local record {home} would be inside the git repository at {p}; {hint}")
    return home


_WIN: tuple | None = None
_USER_SID: str | None = None


def _win() -> tuple:
    """ctypes bindings for the DACL checks (Windows only), made on first use, every signature declared (a 64-bit
    handle passed as a plain int is cut short)."""
    global _WIN
    if _WIN is None:
        import ctypes
        from ctypes import wintypes
        a = ctypes.WinDLL("advapi32", use_last_error=True)
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        vp, pw = ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.LocalFree.argtypes = [vp]
        k.LocalFree.restype = vp
        a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, vp, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD)]
        a.ConvertSidToStringSidW.argtypes = [vp, pw]
        a.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(vp)]
        a.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, vp, vp, vp, vp,
                                            ctypes.POINTER(vp)]
        a.GetNamedSecurityInfoW.restype = wintypes.DWORD
        a.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [vp, wintypes.DWORD, wintypes.DWORD, pw, vp]
        for f in (a.OpenProcessToken, a.GetTokenInformation, a.ConvertSidToStringSidW, a.ConvertStringSidToSidW,
                  a.ConvertSecurityDescriptorToStringSecurityDescriptorW):
            f.restype = wintypes.BOOL
        _WIN = (ctypes, wintypes, a, k)
    return _WIN


def _win_str(k, s) -> str:
    try:
        return s.value
    finally:
        k.LocalFree(s)


def win_user_sid() -> str:
    """The current user's SID, S-1-5-21-..."""
    global _USER_SID
    if _USER_SID is None:
        ctypes, wintypes, a, k = _win()
        h = wintypes.HANDLE()
        if not a.OpenProcessToken(k.GetCurrentProcess(), 0x0008, ctypes.byref(h)):   # TOKEN_QUERY
            raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
        try:
            size = wintypes.DWORD()
            a.GetTokenInformation(h, 1, None, 0, ctypes.byref(size))   # TokenUser; the first call gives the size
            buf = ctypes.create_string_buffer(size.value)
            if not a.GetTokenInformation(h, 1, buf, size, ctypes.byref(size)):
                raise OSError(ctypes.get_last_error(), "GetTokenInformation failed")
            s = wintypes.LPWSTR()
            if not a.ConvertSidToStringSidW(ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.byref(s)):
                raise OSError(ctypes.get_last_error(), "ConvertSidToStringSidW failed")
            _USER_SID = _win_str(k, s)
        finally:
            k.CloseHandle(h)
    return _USER_SID


def win_sddl(path) -> str:
    """The owner and DACL of a file or directory, in SDDL."""
    ctypes, wintypes, a, k = _win()
    sd = ctypes.c_void_p()
    rc = a.GetNamedSecurityInfoW(str(path), 1, 0x5, None, None, None, None, ctypes.byref(sd))   # OWNER | DACL
    if rc != 0:
        raise OSError(rc, f"GetNamedSecurityInfoW failed for {path}")
    try:
        s = wintypes.LPWSTR()
        if not a.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 0x5, ctypes.byref(s), None):
            raise OSError(ctypes.get_last_error(), "ConvertSecurityDescriptorToStringSecurityDescriptorW failed")
        return _win_str(k, s)
    finally:
        k.LocalFree(sd)


def win_canon_sid(token: str) -> str | None:
    """An SDDL SID (S-1-... or a two-letter alias such as SY or LA) in its S-1-... form; None when Windows cannot
    read it."""
    ctypes, wintypes, a, k = _win()
    p = ctypes.c_void_p()
    if not a.ConvertStringSidToSidW(token, ctypes.byref(p)):
        return None
    try:
        s = wintypes.LPWSTR()
        return _win_str(k, s) if a.ConvertSidToStringSidW(p, ctypes.byref(s)) else None
    finally:
        k.LocalFree(p)


def _alias_sid(token: str) -> str | None:
    return token if token.startswith("S-1-") else SDDL_ALIASES.get(token)


def sddl_problems(sddl: str, user_sid: str, canon=_alias_sid) -> list:
    """What makes this owner and DACL (SDDL) less private than the user, SYSTEM and Administrators only. Deny
    entries only take rights away; inherit-only entries count, since new files get them."""
    problems = []
    allowed = {user_sid, SYSTEM_SID, ADMINS_SID}
    m = re.match(r"O:(S-1-[0-9-]+|[A-Z]{2})", sddl)
    if not m or canon(m.group(1)) not in (user_sid, ADMINS_SID):
        problems.append(f"its owner is {m.group(1) if m else 'unreadable'}, not you or Administrators")
    d = re.compile(r"D:([A-Z_]*)").search(sddl, m.end() if m else 0)
    if not d:
        return problems + ["it has no DACL"]
    if "NO_ACCESS_CONTROL" in d.group(1):
        problems.append("its DACL is empty (NO_ACCESS_CONTROL): everyone has full access")
    i, aces = d.end(), []
    while i < len(sddl) and sddl[i] == "(":   # entries may hold parentheses of their own (conditions)
        depth, j = 0, i
        while j < len(sddl):
            depth += {"(": 1, ")": -1}.get(sddl[j], 0)
            j += 1
            if depth == 0:
                break
        if depth:
            break
        aces.append(sddl[i + 1:j - 1])
        i = j
    if i < len(sddl) and not sddl.startswith("S:", i):
        problems.append("its DACL cannot be read to the end")
    for ace in aces:
        f = ace.split(";")
        if len(f) < 6:
            problems.append(f"an entry cannot be read: ({ace})")
        elif f[0] in ("D", "OD", "XD"):
            continue
        elif f[0] not in ("A", "OA", "XA", "ZA"):
            problems.append(f"an entry of a kind this check does not know: ({ace})")
        elif canon(f[5]) not in allowed:
            problems.append(f"the entry ({ace}) lets someone else in")
    return problems


def _is_link(st) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)   # 0x400: a reparse point


def win_protect_dir(path) -> None:
    """A DACL that inherits nothing and lets in only the user, SYSTEM and Administrators (set with icacls, by SID)."""
    try:
        r = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{SYSTEM_SID}:(OI)(CI)F",
                            f"*{ADMINS_SID}:(OI)(CI)F", f"*{win_user_sid()}:(OI)(CI)F"], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise OSError(f"icacls did not finish setting the permissions of {path} within 60 s") from None
    if r.returncode != 0:
        raise OSError(f"icacls could not set the permissions of {path} (exit {r.returncode}); the local record needs "
                      "a file system that keeps permissions (NTFS)")


def _private_one(p: pathlib.Path, st, want: int, home: pathlib.Path) -> None:
    """Only the user can use p: POSIX, owned by the user with mode `want`; Windows, the owner and the DACL."""
    if os.name == "nt":
        try:
            problems = sddl_problems(win_sddl(p), win_user_sid(), lambda t: win_canon_sid(t) or _alias_sid(t))
        except OSError as e:
            raise StoreError("unsafe", f"cannot read the permissions of {p} ({e}); the local record needs NTFS") from e
        if problems:
            try:
                sid = win_user_sid()
            except OSError:
                sid = "<your SID>"
            raise StoreError("unsafe", f"{p} is not private: {'; '.join(problems)}. Fix it with: icacls \"{home}\" "
                                       f"/inheritance:r /grant:r *{SYSTEM_SID}:(OI)(CI)F *{ADMINS_SID}:(OI)(CI)F "
                                       f"*{sid}:(OI)(CI)F, remove any other entry (icacls \"{home}\" /remove NAME), "
                                       f"and let each file inherit again (icacls \"FILE\" /reset)")
        return
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid != os.getuid():
        raise StoreError("unsafe", f"{p} belongs to uid {st.st_uid}, not to you; move the directory aside and let "
                                   "ctl make a new one")
    if mode != want:
        raise StoreError("unsafe", f"{p} has mode {mode:o}, not {want:o}; fix it with: chmod {want:o} "
                                   f"{shlex.quote(str(p))} (if chmod does not change it, the file system does not keep "
                                   "permissions: set AUTODL_GPU_HOME to a directory on one that does)")


def check_private(home: pathlib.Path) -> None:
    """Every open: the directory and both files are no links (nor junctions), of the right type, and private.
    Nothing is changed; the error says how to fix it."""
    for p, want in ((home, 0o700), (home / "store.lock", 0o600), (home / "store.json", 0o600)):
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            if p.name == "store.json":
                continue
            raise StoreError("unsafe", f"{p} is missing; it is not made again automatically") from None
        except OSError as e:
            raise StoreError("io", f"cannot read {p}: {e}") from e
        if _is_link(st):
            raise StoreError("unsafe", f"{p} is a link; the local record must be a real directory with real files")
        if not (stat.S_ISDIR(st.st_mode) if p == home else stat.S_ISREG(st.st_mode)):
            raise StoreError("unsafe", f"{p} is not a {'directory' if p == home else 'regular file'}")
        if p == home and not os.path.lexists(home / "store.lock") and not os.path.lexists(home / "store.json"):
            # said before any advice on its permissions: fixing those would not make it a record
            raise StoreError("unsafe", f"{home} holds neither store.lock nor store.json, so it was not made by ctl "
                                       "(made by hand?). If it holds nothing you need, remove it; ctl then makes a "
                                       "new, private one")
        _private_one(p, st, want, home)


def publish_if_missing(home: pathlib.Path) -> None:
    """First creation: a private temporary directory next to the place, with the lock file and its one byte, is
    renamed into place. A process that finds the place taken meanwhile removes its own and uses that one, so a
    directory under the real name is always complete."""
    if os.path.lexists(home):
        return
    tmp = home.parent / f".{home.name}.new-{secrets.token_hex(6)}"
    try:
        home.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.mkdir(tmp)   # not mode 0o700: on Windows that makes a DACL for "owner rights", not for the user
            win_protect_dir(tmp)
        else:
            os.mkdir(tmp, 0o700)
            os.chmod(tmp, 0o700)
        _private_one(tmp, os.lstat(tmp), 0o700, tmp)
        _hook("tmpdir-made")
        fd = os.open(tmp / "store.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY | _O_NOFOLLOW, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            os.write(fd, b"\0")
            os.fsync(fd)
        finally:
            os.close(fd)
        _hook("before-rename")
        try:
            os.rename(tmp, home)
            tmp = None
        except OSError:
            if not os.path.lexists(home):
                raise
    except OSError as e:
        raise StoreError("io", f"cannot make the local record {home}: {e}") from e
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


def open_lock(path: pathlib.Path) -> int:
    """The lock file, opened without following a link (O_NOFOLLOW; on Windows, lstat before and fstat after must
    show the same regular file)."""
    try:
        before = os.lstat(path)
        fd = os.open(path, os.O_RDWR | _O_BINARY | _O_NOFOLLOW)
    except OSError as e:
        raise StoreError("unsafe" if e.errno == errno.ELOOP else "io", f"cannot open {path}: {e}") from e
    try:
        after = os.fstat(fd)
        if (_is_link(before) or not stat.S_ISREG(after.st_mode)
                or (after.st_ino, after.st_dev) != (before.st_ino, before.st_dev)):
            raise StoreError("unsafe", f"{path} is not a regular file, or was replaced while it was opened")
    except BaseException:
        os.close(fd)
        raise
    return fd


def take_lock(fd: int, wait: float) -> None:
    deadline = time.monotonic() + wait
    while True:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise StoreError("locked", f"the local record is in use by another ctl (its lock was not free for "
                                           f"{wait:g} s); try again") from None
            time.sleep(0.1)


def drop_lock(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


def _pairs(pairs) -> dict:
    d = {}
    for k, v in pairs:
        if k in d:
            raise ValueError(f"the key {k!r} appears twice")
        d[k] = v
    return d


def _finite(s: str) -> float:
    v = float(s)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"{s} is not a finite number")
    return v


def _no_constant(s: str):
    raise ValueError(f"{s} is not allowed")


def _need(ok: bool, what: str) -> None:
    if not ok:
        raise ValueError(what)


def _int(v, lo: int = 0) -> bool:
    return type(v) is int and v >= lo   # bool is not a number here


def _match(rx, v) -> bool:
    return isinstance(v, str) and bool(rx.match(v))


def _fields(obj, required: set, optional: set = frozenset(), where: str = "") -> None:
    _need(isinstance(obj, dict), f"{where} is not an object")
    keys = set(obj)
    _need(required <= keys, f"{where} lacks {sorted(required - keys)}")
    _need(keys <= required | optional, f"{where} has unknown fields {sorted(keys - required - optional)}")


# the ledger's kinds of record: (required fields, optional fields); every record's key is "<kind>:<its id>"
LEDGER_FIELDS = {
    "reserve": ({"kind", "key", "req", "at", "expires", "mode", "gpus", "price_fen_h", "window_s"}, set()),
    "release": ({"kind", "key", "req", "at"}, set()),
    "on": ({"kind", "key", "sid", "at", "mode", "gpus", "price_fen_h"}, {"req", "attempted_req", "unreserved"}),
    "off": ({"kind", "key", "sid", "at"}, set()),
    "charge": ({"kind", "key", "serial", "at", "fen"}, set()),
}


def period_key_ok(key, grant: dict) -> bool:
    """A period's name in the grant's scheme: YYYY-MM for a month, "all" without periods."""
    return key == "all" if grant["period"] == "none" else _match(PERIOD_KEY_RE, key)


def validate_grant(g, where: str) -> None:
    """since: where a budget without periods counts from (the full hour of its grant; a record written before this
    field has none and counts everything). charges_read: when charges were last imported under this grant (a monthly
    money budget is not judged without it)."""
    _fields(g, {"alias", "usage", "budget", "period", "period_tz", "quote", "at", "approvals"},
            {"since", "charges_read"}, where=where)
    _need("charges_read" not in g or _int(g["charges_read"]), f"{where}: charges_read")
    _need("since" not in g or (_int(g["since"]) and g["period"] == "none"
                               and isinstance(g["budget"], dict) and g["budget"].get("kind") in ("fen", "gpu_mh")),
          f"{where}: since (a whole number of seconds, and only for a budget without periods)")
    _need(_match(ALIAS_RE, g["alias"]), f"{where}: alias")
    _need(g["usage"] in ("both", "gpu", "nogpu"), f"{where}: usage")
    b = g["budget"]
    _need(isinstance(b, dict) and b.get("kind") in ("none", "fen", "gpu_mh"), f"{where}: budget")
    _fields(b, {"kind"} if b["kind"] == "none" else {"kind", "value"}, where=f"{where}.budget")
    _need(b["kind"] == "none" or _int(b["value"]), f"{where}: the budget is not a whole number")
    _need(g["period"] in ("month", "none"), f"{where}: period")
    _need(_match(TZ_RE, g["period_tz"]), f"{where}: period_tz")
    _need(isinstance(g["quote"], str) and g["quote"].strip() != "", f"{where}: quote")
    _need(_int(g["at"]), f"{where}: at")
    aps = g["approvals"]   # the user's yes near the end of the budget, per period
    _need(isinstance(aps, dict), f"{where}: approvals is not an object")
    for key, ap in aps.items():
        _need(period_key_ok(key, g), f"{where}: approvals has the period {key!r}, not one of this grant's")
        _fields(ap, {"quote", "at"}, where=f"{where}.approvals[{key}]")
        _need(isinstance(ap["quote"], str) and ap["quote"].strip() != "", f"{where}: approvals[{key}] has no quote")
        _need(_int(ap["at"]), f"{where}: approvals[{key}]: at")


def validate_ledger(records, where: str) -> None:
    """Every record well formed, every key once, at most one open session, every off closing the open one."""
    _need(isinstance(records, list), f"{where} is not a list")
    keys, open_at = set(), {}
    for i, r in enumerate(records):
        w = f"{where}[{i}]"
        kind = r.get("kind") if isinstance(r, dict) else None
        _need(isinstance(kind, str) and kind in LEDGER_FIELDS, f"{w}: unknown kind of record ({kind!r})")
        _fields(r, *LEDGER_FIELDS[kind], where=w)
        _need(_int(r["at"]), f"{w}: at")
        if kind in ("reserve", "release"):
            _need(_match(REQ_RE, r["req"]), f"{w}: req")
            ident = r["req"]
        elif kind in ("on", "off"):
            _need(_match(SESSION_RE, r["sid"]), f"{w}: sid")
            ident = r["sid"]
        else:
            _need(_match(SERIAL_RE, r["serial"]), f"{w}: serial")
            _need(_int(r["fen"], 1), f"{w}: fen is not a whole positive number")
            ident = r["serial"]
        _need(r["key"] == f"{kind}:{ident}", f"{w}: the key does not match the record")
        _need(r["key"] not in keys, f"{w}: {r['key']} appears twice")
        keys.add(r["key"])
        if kind in ("reserve", "on"):
            _need(r["mode"] in ("gpu", "nogpu"), f"{w}: mode")
            _need(_int(r["gpus"], 1) if r["mode"] == "gpu" else r["gpus"] == 0 and _int(r["gpus"]), f"{w}: gpus")
            _need(_int(r["price_fen_h"]), f"{w}: price_fen_h")
        if kind == "reserve":   # a window as auth check makes it: at most 30 days
            _need(_int(r["window_s"], 1) and r["window_s"] <= MAX_DURATION_S and _int(r["expires"], r["at"]),
                  f"{w}: window_s or expires")
        elif kind == "on":
            for f in ("req", "attempted_req"):
                _need(f not in r or _match(REQ_RE, r[f]), f"{w}: {f}")
            _need(r.get("unreserved", True) is True, f"{w}: unreserved")
            _need(not open_at, f"{w}: a second session is open on the same instance")
            open_at[r["sid"]] = r["at"]
        elif kind == "off":
            _need(r["sid"] in open_at, f"{w}: it closes a session that is not open")
            _need(r["at"] >= open_at.pop(r["sid"]), f"{w}: it ends before the session began")


GUARD_NOTE_RE = re.compile(r"^[ -~]{0,200}\Z")   # what a status line of the guard holds: printable ASCII, short


def validate_store(d) -> None:
    """The whole record, strictly: any part out of shape makes it unusable (never repaired). guards came later and
    may be missing: per instance, what the guard's last status said about the next start (note_guard)."""
    _fields(d, set(STORE_PARTS), {"guards"}, where="the record")
    _need(type(d["schema"]) is int and d["schema"] == STORE_SCHEMA, f"schema is {d['schema']!r}, not {STORE_SCHEMA}")
    _need(_int(d["last_seen"]), "last_seen is not a whole number of seconds")
    for part in ("aliases", "grants", "ledger", "calib", *(["guards"] if "guards" in d else [])):
        _need(isinstance(d[part], dict), f"{part} is not an object")
    for iid, v in d.get("guards", {}).items():
        _need(bool(INSTANCE_RE.match(iid)), f"guards: {iid!r} is not an instance ID")
        _fields(v, {"autostart", "boot_settings", "at"}, where=f"guards[{iid}]")
        _need(_match(GUARD_NOTE_RE, v["autostart"]) and _match(GUARD_NOTE_RE, v["boot_settings"]) and _int(v["at"]),
              f"guards[{iid}]: autostart, boot_settings or at")
    for alias, v in d["aliases"].items():
        _need(bool(ALIAS_RE.match(alias)), f"aliases: {alias!r} is not an ssh alias")
        _fields(v, {"instance", "at"}, where=f"aliases[{alias}]")
        _need(_match(INSTANCE_RE, v["instance"]) and _int(v["at"]), f"aliases[{alias}]: instance or at")
    for part, check in (("grants", validate_grant), ("ledger", validate_ledger)):
        for iid, v in d[part].items():
            _need(bool(INSTANCE_RE.match(iid)), f"{part}: {iid!r} is not an instance ID")
            check(v, f"{part}[{iid}]")
    for iid, entries in d["calib"].items():
        _need(bool(INSTANCE_RE.match(iid)), f"calib: {iid!r} is not an instance ID")
        validate_calib(entries, f"calib[{iid}]")


CALIB_RE = re.compile(r"^c[0-9a-f]{10}\Z")
FP_RE = re.compile(r"^[0-9a-f]{12}\Z")
CALIB_FIELDS = {"id", "mode", "fingerprint", "guard", "at", "intervals", "thresholds", "unreliable", "max", "median"}


def _signals(mode: str) -> set:
    return {"gpu", "cpu", "io", "net"} if mode == "gpu" else {"cpu", "io", "net"}


def validate_calib(entries, where: str) -> None:
    """Calibrations of one instance: thresholds for every signal of the mode (CPU may have a fraction, the others are
    whole), the unreliable ones among them, the statistics (numbers or null; the parser already refused NaN)."""
    _need(isinstance(entries, list), f"{where} is not a list")
    ids = set()
    for i, e in enumerate(entries):
        w = f"{where}[{i}]"
        _fields(e, CALIB_FIELDS, where=w)
        _need(_match(CALIB_RE, e["id"]) and e["id"] not in ids, f"{w}: id")
        ids.add(e["id"])
        _need(e["mode"] in ("gpu", "nogpu"), f"{w}: mode")
        _need(_match(FP_RE, e["fingerprint"]) and _match(FP_RE, e["guard"]), f"{w}: fingerprint or guard")
        _need(_int(e["at"]) and _int(e["intervals"], 2), f"{w}: at or intervals")
        names, th = _signals(e["mode"]), e["thresholds"]
        _need(isinstance(th, dict) and set(th) == names, f"{w}: thresholds")
        _need(type(th["cpu"]) in (int, float) and 0 <= th["cpu"] <= 100 and all(_int(th[k]) for k in names - {"cpu"})
              and th.get("gpu", 0) <= 100, f"{w}: a threshold (CPU and GPU at most 100, the guard's range)")
        u = e["unreliable"]
        _need(isinstance(u, list) and all(isinstance(x, str) for x in u) and len(set(u)) == len(u) and set(u) <= names,
              f"{w}: unreliable")
        for part in ("max", "median"):
            v = e[part]
            _need(isinstance(v, dict) and set(v) == names and all(x is None or type(x) in (int, float) for x in v.values()),
                  f"{w}: {part}")


def read_store(path: pathlib.Path) -> dict:
    """The record as it is on disk, checked in full; a missing file is an empty record."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return empty_store()
    except OSError as e:
        raise StoreError("io", f"cannot read {path}: {e}") from e
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_no_constant,
                          parse_float=_finite)
        validate_store(data)
    except ValueError as e:   # also JSONDecodeError and UnicodeDecodeError
        raise StoreError("invalid", f"{path} cannot be used: {e}. It was left as it is; fix it by hand or move it "
                                    "aside (ctl then starts a new, empty record)") from e
    return data


def write_replace(path: pathlib.Path, content: str) -> None:
    """A temporary file in the same directory (private before anything is written), flushed and synced, then put in
    place with os.replace. Anything failing before the replace leaves the old file and removes the temporary one."""
    tmp = path.parent / f"{STORE_TMP_PREFIX}{secrets.token_hex(6)}"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY | _O_NOFOLLOW, 0o600)
    except OSError as e:
        raise StoreError("io", f"cannot write the local record {path}: {e}; it was left as it was") from e
    try:
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            _private_one(tmp, os.fstat(fd), 0o600, path.parent)
            view = memoryview(content.encode("utf-8"))
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        _hook("tmp-written", tmp)
        os.replace(tmp, path)
        tmp = None
    except OSError as e:
        raise StoreError("io", f"cannot write the local record {path}: {e}; it was left as it was") from e
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if os.name != "nt":   # the rename reaches the disk; if this fails, a crash can only bring back the old record
        try:
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass


def sweep_tmp(home: pathlib.Path) -> None:
    """Under the lock: the temporary files a killed writer left, when they are provably ours (the prefix, a regular
    file, private); anything else is left where it is."""
    try:
        entries = list(os.scandir(home))
    except OSError as e:
        raise StoreError("io", f"cannot list {home}: {e}") from e
    for e in entries:
        if not e.name.startswith(STORE_TMP_PREFIX):
            continue
        p = home / e.name
        try:
            st = os.lstat(p)
            if _is_link(st) or not stat.S_ISREG(st.st_mode):
                continue
            _private_one(p, st, 0o600, home)
            os.unlink(p)
        except (OSError, StoreError):
            continue


class Store:
    """The local record outside every project: verified alias -> instance pairs, grants and the ledger per
    instance, calibrations. One JSON file, read and written under an exclusive lock; a write goes to a temporary
    file in the same directory, which then replaces the old one. Every open records last_seen, the latest time
    ctl has seen (prev_last_seen keeps the value found, for the clock check)."""

    def __init__(self, wait: float | None = None):
        self.wait = LOCK_WAIT_S if wait is None else wait
        self.fd = None

    def __enter__(self):
        self.home = store_home()
        publish_if_missing(self.home)
        check_private(self.home)
        fd = open_lock(self.home / "store.lock")
        try:
            take_lock(fd, self.wait)
            try:
                sweep_tmp(self.home)
                self.path = self.home / "store.json"
                self.data = read_store(self.path)
                self.prev_last_seen = self.data["last_seen"]
                now = now_s()
                if now > self.prev_last_seen:
                    self.data["last_seen"] = now
                    self.save()
            except BaseException:
                drop_lock(fd)
                raise
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd
        return self

    def save(self) -> None:
        """Checked by the rules a read applies, so no path can write what the next open would refuse. Only a bug gets
        here (inputs are checked before); it counts as a record that cannot be used, so log still writes the project
        log."""
        try:
            validate_store(self.data)
        except ValueError as e:
            raise StoreError("invalid", f"this change would leave a local record that cannot be read back ({e}); "
                                        "nothing was written") from None
        write_replace(self.path, json.dumps(self.data, ensure_ascii=False, indent=1, allow_nan=False))

    def __exit__(self, *exc):
        try:
            drop_lock(self.fd)
        finally:
            os.close(self.fd)
            self.fd = None
        return False


# ---- grants, the check before a power-on, the ledger (plan 5.2, 5.3) ----
DEFAULT_PERIOD = {"period": "month", "period_tz": "+08:00"}   # for an instance without a grant
YUAN_RE = re.compile(r"^[0-9]{1,9}(?:\.[0-9]{1,2})?\Z")
HOURS_RE = re.compile(r"^[0-9]{1,4}(?:\.[0-9]{1,3})?\Z")


class Refused(Exception):
    """A request the ledger turns down; nothing is written (exit 1)."""


def yuan_to_fen(s, what: str) -> int:
    """A price or an amount in yuan, at most two decimals (0.98, 12, 0.1), as whole fen."""
    if isinstance(s, bool) or not isinstance(s, (str, int, float)) or not YUAN_RE.match(str(s)):
        raise ValueError(f"{what} {s!r}: use yuan with at most two decimals, like 0.98")
    whole, _, frac = str(s).partition(".")
    return int(whole) * 100 + int((frac + "00")[:2])


def fen_to_yuan(f: int) -> float:
    return round(f / 100, 2)


def parse_budget(s: str) -> dict:
    """none, <yuan>yuan (to fen) or <hours>gpuh (to thousandths of a GPU hour)."""
    if s == "none":
        return {"kind": "none"}
    if s.endswith("yuan") and YUAN_RE.match(s[:-4]):
        return {"kind": "fen", "value": yuan_to_fen(s[:-4], "--budget")}
    m = re.match(r"^([0-9]{1,9})(?:\.([0-9]{1,3}))?gpuh\Z", s)
    if m:
        return {"kind": "gpu_mh", "value": int(m.group(1)) * 1000 + int(((m.group(2) or "") + "000")[:3])}
    raise ValueError(f"--budget {s!r}: use none, an amount like 50yuan (two decimals at most) or GPU hours like "
                     "12.5gpuh (three decimals at most)")


def parse_hours_s(s: str) -> int:
    """--hours H (three decimals at most) as whole seconds, rounded up."""
    if not HOURS_RE.match(s or ""):
        raise ValueError(f"--hours {s!r}: use hours like 2 or 1.5")
    whole, _, frac = s.partition(".")
    thousandths = int(whole) * 1000 + int((frac + "000")[:3])
    secs = -(-thousandths * 3600 // 1000)
    if not 1 <= secs <= MAX_DURATION_S:
        raise ValueError(f"--hours {s!r}: more than 0 and at most 30 days")
    return secs


def need_instance(s: str) -> str:
    if not INSTANCE_RE.match(s or ""):
        raise ValueError(f"bad instance ID {s!r}: use the ID the console shows, like abcd123456-1234abcd")
    return s


def tz_offset_s(tz: str) -> int:
    return (-1 if tz[0] == "-" else 1) * int(tz[1:3]) * 3600


def period_of(t: int, grant: dict) -> tuple:
    """(key, start, end) of the budget period holding t: a calendar month in the grant's time zone, or, without
    periods, everything from the hour of the grant on (its since)."""
    if grant["period"] == "none":
        return "all", grant.get("since", 0), 2 ** 62
    z = dt.timezone(dt.timedelta(seconds=tz_offset_s(grant["period_tz"])))
    d = dt.datetime.fromtimestamp(t, z)
    start = dt.datetime(d.year, d.month, 1, tzinfo=z)
    end = dt.datetime(d.year + d.month // 12, d.month % 12 + 1, 1, tzinfo=z)
    return f"{d.year:04d}-{d.month:02d}", int(start.timestamp()), int(end.timestamp())


def periods_touching(a: int, b: int, grant: dict) -> list:
    """Every period the window [a, b) touches, in order."""
    found = [period_of(a, grant)]
    while found[-1][2] < b:
        found.append(period_of(found[-1][2], grant))
    return found


def segments(t_on: int, t_off: int):
    """A session cut at every full hour of Beijing time, as AutoDL charges it (one charge per clock hour and one at
    power-off). UTC+8 is a whole number of hours away, so these are also full hours of unix time; a month boundary
    in any whole-hour time zone is one of them, so no segment crosses one."""
    t = t_on
    while t < t_off:
        nxt = min(t_off, (t // 3600 + 1) * 3600)
        yield t, nxt
        t = nxt


def segment_fen(secs: int, price_fen_h: int) -> int:
    """A segment's estimate: rounded up, and at least 1 fen, since every charge is at least 0.01."""
    return 0 if price_fen_h == 0 else max(1, -(-secs * price_fen_h // 3600))


def window_fen(t: int, secs: int, price_fen_h: int) -> int:
    return sum(segment_fen(hi - lo, price_fen_h) for lo, hi in segments(t, t + secs))


def session_list(records: list) -> list:
    """The sessions in the order they began: sid, on, off (None while open), mode, gpus, price_fen_h."""
    found, index = [], {}
    for r in records:
        if r["kind"] == "on":
            index[r["sid"]] = len(found)
            found.append({"sid": r["sid"], "on": r["at"], "off": None, "mode": r["mode"], "gpus": r["gpus"],
                          "price_fen_h": r["price_fen_h"]})
        elif r["kind"] == "off" and r["sid"] in index:
            found[index[r["sid"]]]["off"] = r["at"]
    return found


def open_reservations(records: list) -> list:
    """Reservations neither used by a power-on nor released: they count, expired or not."""
    done = {r["req"] for r in records if r["kind"] == "on" and "req" in r}
    done |= {r["req"] for r in records if r["kind"] == "release"}
    return [r for r in records if r["kind"] == "reserve" and r["req"] not in done]


def _estimate_and_charged(records: list, start: int, end: int, now: int) -> tuple:
    """(a) every session segment beginning in [start, end), an open session up to now, and 1 fen more for a power-off
    right on the hour; (b) this period's imported charges plus the segments after the last of them."""
    sess = session_list(records)

    def estimate(after: int, off_at_after: bool) -> int:
        tot = 0
        for s in sess:
            t_off = now if s["off"] is None else s["off"]
            tot += sum(segment_fen(hi - lo, s["price_fen_h"])
                       for lo, hi in segments(s["on"], t_off) if start <= lo < end and lo >= after)
            if (s["off"] is not None and s["off"] % 3600 == 0 and start <= s["off"] < end
                    and (s["off"] >= after if off_at_after else s["off"] > after)):
                tot += 1   # a power-off right on the hour may bring one more charge (at a period's start too; after
                # the last charge only when later than it: a charge at that very time is that one)
        return tot
    pc = [r for r in records if r["kind"] == "charge" and start <= r["at"] < end]   # this period's charges only
    a = estimate(start, True)
    b = a if not pc else sum(c["fen"] for c in pc) + estimate(max(c["at"] for c in pc), False)
    return a, b


def spent_fen(records: list, start: int, end: int, now: int) -> int:
    """Fen spent in [start, end), never below the ledger's own estimate: the larger of (a) and (b) above, plus every
    open reservation whose window touches the period, in full."""
    a, b = _estimate_and_charged(records, start, end, now)
    held = sum(window_fen(r["at"], r["window_s"], r["price_fen_h"])
               for r in open_reservations(records) if r["at"] < end and r["expires"] > start)
    return max(a, b) + held


def spent_gpu_s(records: list, start: int, end: int, now: int) -> int:
    """GPU seconds in [start, end): GPU-mode session seconds times GPUs, plus open GPU reservations in full."""
    tot = 0
    for s in session_list(records):
        if s["mode"] == "gpu":
            t_off = now if s["off"] is None else s["off"]
            tot += sum(hi - lo for lo, hi in segments(s["on"], t_off) if start <= lo < end) * s["gpus"]
    return tot + sum(r["window_s"] * r["gpus"] for r in open_reservations(records)
                     if r["mode"] == "gpu" and r["at"] < end and r["expires"] > start)


# ---- calibration (plan 5.6): the idle noise of one instance, mode, environment and guard ----
# the guard's default thresholds, and the lightest work phase 2 measured on the test instance (60 s windows): a
# threshold that reaches the latter cannot tell that work from idle, so the signal is unreliable
DEFAULT_THR = {"gpu": {"gpu": 5, "cpu": 5.0, "io": 500000, "net": 10000}, "nogpu": {"cpu": 3.0, "io": 500000, "net": 10000}}
LIGHTEST = {"gpu": {"gpu": 99, "cpu": 8.523, "io": 2665000, "net": 902400},
            "nogpu": {"cpu": 6.693, "io": 2665000, "net": 902400}}
CALIB_MAX_AGE_S = 30 * 86400
SAMPLE_COLS = ("epoch", "uptime_cs", "cpu_usec", "io_bytes", "net_bytes", "gpu_max", "gpu_fail", "guard_ticks",
               "self_ticks")
def fingerprint_probe(limit_s: int = 10) -> str:
    """One read-only SSH in four parts: the mode; the environment's lines with their keys (hashed into the
    fingerprint); the guard's digest; the host name (autodl-container-<ID>: is this still the verified instance?).
    Neither the image nor the running services go in: services still start in the first seconds after boot."""
    return ("(" + mode_probe(limit_s) + '); echo "==="; ' + _nvsmi_limit(limit_s) + (   # the mode probe exits early
        'n=$(sed -n "s/^NAME=//p" /etc/os-release 2>/dev/null); echo "os_name=${n:-absent}"; '
        'v=$(sed -n "s/^VERSION_ID=//p" /etc/os-release 2>/dev/null); echo "os_version=${v:-absent}"; '
        '$t nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | sort | sed "s/^/gpu=/" '
        '| grep . || echo "gpu=absent"; '
        'echo "cpu.max=$(cat /sys/fs/cgroup/cpu.max 2>/dev/null || echo absent)"; '
        'echo "memory.max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null || echo absent)"; '
        'echo "==="; sha256sum ' + shlex.quote(GUARD_PATH) + ' 2>/dev/null | cut -c1-12; echo "==="; uname -n'))


FINGERPRINT_PROBE = fingerprint_probe()


def parse_probe(out: str) -> dict:
    """mode, gpus, fingerprint, guard and host (each None when it cannot be told), and why not."""
    parts = [[]]
    for ln in out.splitlines():
        if ln.strip() == "===":
            parts.append([])
        elif ln.strip():
            parts[-1].append(ln.strip())
    if len(parts) != 4:
        return {"mode": "unknown", "gpus": 0, "fingerprint": None, "guard": None, "host": None,
                "why": "the fingerprint probe's answer is not in its four parts"}
    host = parts[3][0] if len(parts[3]) == 1 else None
    mode, gpus = mode_of(" ".join(parts[0]))
    lines = parts[1]
    keys = [ln.split("=", 1)[0] for ln in lines]
    why = []
    if [k for k in keys if k != "gpu"] != ["os_name", "os_version", "cpu.max", "memory.max"] or "gpu" not in keys:
        why.append("the environment's lines are not the expected ones")
    why += [f"{ln.split('=', 1)[0]} could not be read" for ln in lines
            if ln.endswith("=absent") and not (ln == "gpu=absent" and mode == "nogpu")]
    guard = parts[2][0] if parts[2] and FP_RE.match(parts[2][0]) else None
    if guard is None:
        why.append("the guard's digest could not be read (is it deployed?)")
    if mode == "unknown":
        why.append("the mode cannot be told")
    if host is None:
        why.append("the host name could not be read")
    fp = None if why else hashlib.sha256("\n".join(lines).encode()).hexdigest()[:12]
    return {"mode": mode, "gpus": gpus, "fingerprint": fp, "guard": guard, "host": host, "why": "; ".join(why)}


def parse_sample(out: str, count: int, every: int, k: int) -> list:
    """The intervals of one complete sample run: cpu in percent of one core, io and net in bytes per second, over the
    real uptime between rows; gpu the highest reading, or None where a probe failed. Anything else is ValueError."""
    lines = [ln.rstrip("\r") for ln in out.splitlines() if ln.strip()]
    m = re.match(r"^# autodl_guard (\S+) sample every=([0-9]+) count=([0-9]+) gpu_samples=([0-9]+) clk_tck=[0-9]*$",
                 lines[0] if lines else "")
    if not m or (int(m.group(2)), int(m.group(3)), int(m.group(4))) != (every, count, k):
        raise ValueError(f"the sample's first line does not match what was asked: {(lines or [''])[0]!r}")
    if lines[1:2] != ["# " + "\t".join(SAMPLE_COLS)]:
        raise ValueError("the sample's columns are not the expected ones")
    rows = [ln.split("\t") for ln in lines[2:]]
    if len(rows) != count + 1 or any(len(r) != len(SAMPLE_COLS) for r in rows):
        raise ValueError(f"the sample has {len(rows)} rows for {count} intervals, or a row with other columns")

    def num(row, i):
        if not re.match(r"^[0-9]+$", row[i]):
            raise ValueError(f"a sample row has no {SAMPLE_COLS[i]}")
        return int(row[i])
    found = []
    for a, b in zip(rows, rows[1:]):
        dt_cs = num(b, 1) - num(a, 1)
        if dt_cs <= 0:
            raise ValueError("the uptime did not increase between two rows")
        iv = {}
        for i, name in ((2, "cpu"), (3, "io"), (4, "net")):
            delta = num(b, i) - num(a, i)
            if delta < 0:
                raise ValueError(f"the {SAMPLE_COLS[i]} counter went back")
            iv[name] = delta / dt_cs / 100 if name == "cpu" else delta * 100 / dt_cs
        if k == 0 and (b[5], b[6]) != ("na", "na"):
            raise ValueError("GPU readings where none were asked for")
        iv["gpu"] = int(b[5]) if k and b[6] == "0" and re.match(r"^[0-9]+$", b[5]) else None
        found.append(iv)
    return found


def calib_result(intervals: list, mode: str) -> dict:
    """Per signal max(the guard's default, 1.5 x the highest idle reading), CPU to a tenth and the others to whole
    numbers, rounded up; at most 100 for CPU and GPU (the guard's range). A signal whose threshold reaches the
    lightest work, or with no reading at all, is unreliable."""
    th, unrel, mx, md = {}, [], {}, {}
    for name in sorted(_signals(mode)):
        vals = [iv[name] for iv in intervals if iv[name] is not None]
        base = DEFAULT_THR[mode][name]
        if not vals:   # only the GPU: no interval had every probe answer
            th[name], mx[name], md[name] = base, None, None
            unrel.append(name)
            continue
        raw = round(1.5 * max(vals), 6)   # so that 1.5 x 4.2 stays 6.3
        t = max(base, math.ceil(round(raw * 10, 6)) / 10) if name == "cpu" else max(base, math.ceil(raw))
        th[name] = min(t, 100.0 if name == "cpu" else 100) if name in ("cpu", "gpu") else t
        mx[name], md[name] = round(max(vals), 3), round(statistics.median(vals), 3)
        if th[name] >= LIGHTEST[mode][name]:
            unrel.append(name)
    return {"intervals": len(intervals), "thresholds": th, "unreliable": sorted(unrel), "max": mx, "median": md}


def calibration_args(a) -> tuple:
    """arm's thresholds from a calibration of this instance, mode, environment and guard at most 30 days old:
    (extra arguments, what to say). Every failure leaves the guard's defaults: arm must never wait for this."""
    if any(getattr(a, n) for n in ("thr_gpu", "thr_cpu", "thr_io", "thr_net", "unreliable", "calib")):
        return [], "thresholds given by hand; no calibration looked up"
    if a.interval or a.gpu_probes:   # the calibration's readings are of another rhythm
        return [], ("none looked up: arm sets its own --interval or --gpu-probes, while a calibration is measured once "
                    "every 60 s with 3 GPU probes (none without a GPU); the guard's default thresholds")
    default = "none used, the guard's default thresholds"
    r = ssh_run(a.alias, FINGERPRINT_PROBE, retry_uncertain=True)
    if r.rc != 0:
        return [], f"{default}: the fingerprint probe did not answer ({r.state})"
    p = parse_probe(text(r.stdout))
    mode = a.mode if a.mode != "auto" else p["mode"]
    if p["fingerprint"] is None or mode not in ("gpu", "nogpu"):
        return [], f"{default}: {p['why'] or 'the mode cannot be told'}"
    try:
        with Store() as st:
            iid = BOUND or (st.data["aliases"].get(a.alias) or {}).get("instance")
            now = now_s()
            found = [e for e in st.data["calib"].get(iid or "", []) if e["mode"] == mode
                     and e["fingerprint"] == p["fingerprint"] and e["guard"] == p["guard"]
                     and 0 <= now - e["at"] <= CALIB_MAX_AGE_S]
    except StoreError as e:
        return [], f"{default}: the local record cannot be used ({e.kind}: {e})"
    if iid is None:
        return [], f"{default}: {a.alias} is not verified (ctl check {a.alias} --instance ID)"
    if p["host"] != f"autodl-container-{iid}":
        return [], (f"{default}: {a.alias} now leads to {p['host']}, not to {iid} it was verified for (ctl check "
                    f"{a.alias} --instance ID)")
    if not found:
        return [], (f"{default}: no calibration of this instance, mode, environment and guard in the last 30 days; "
                    f"run ctl calibrate {a.alias} while it is idle")
    e = max(found, key=lambda x: x["at"])
    args = []
    for name in ("gpu", "cpu", "io", "net"):
        if name in e["thresholds"]:
            v = e["thresholds"][name]
            args += [f"--thr-{name}", f"{v:.1f}" if name == "cpu" else str(v)]
    said = f"{e['id']} (measured idle at {e['at']}; the working side is not verified)"
    if e["unreliable"]:
        args += ["--unreliable", ",".join(e["unreliable"])]
        said += (f"; turned off: {','.join(e['unreliable'])} (their idle noise reaches light work), so work that shows "
                 "only on them needs quiet or keep")
    return args + ["--calib", e["id"], "--calib-coverage", "unverified"], said


def forget_calibrations(alias: str) -> tuple:
    """After a new hook install (a sign of a new image): this instance's calibrations go. (what happened, exit code)"""
    try:
        with Store() as st:
            iid = BOUND or (st.data["aliases"].get(alias) or {}).get("instance")
            if iid is None:
                return f"nothing forgotten: {alias} is not verified, its calibrations cannot be found", 0
            n = len(st.data["calib"].pop(iid, []))
            st.save()
    except StoreError as e:
        return (f"failed: {e}; after fixing the local record run ctl calibrate {alias} --forget, or calibrate "
                "again", EXIT_ERR)
    return f"forgot {n} calibration(s) of {iid}: a new hook install hints at a new image", 0


def resolve_instance(st: Store, name: str) -> str | None:
    """The instance a log names: an instance ID as written, or an alias verified with check --instance."""
    if INSTANCE_RE.match(name or ""):
        return name
    v = st.data["aliases"].get(name)
    return v["instance"] if v else None


def bound_instance(a) -> str:
    """The instance a command with an alias is meant for, which every remote shell of the run then checks (host_guard):
    --instance when given, else the one the alias was verified for (check ALIAS --instance ID). Unverified without
    either; StoreError when the record that would tell cannot be used."""
    alias = check_alias(a.alias)
    given = getattr(a, "instance", None)
    if given is not None:
        return need_instance(given)
    try:
        with Store() as st:
            v = st.data["aliases"].get(alias)
    except StoreError as e:
        raise StoreError(e.kind, f"{e}. So ctl cannot tell which instance {alias} was verified for: name it with "
                                 f"--instance ID (the command then checks the host name itself)") from e
    if v is None:
        raise Unverified(f"{alias} was not verified for any instance: run ctl check {alias} --instance ID first (its "
                         f"host name must be autodl-container-ID), or name the instance with --instance ID. Nothing "
                         "was sent")
    return v["instance"]


GUARD_SETTING_RE = re.compile(r"^(gpu|nogpu):[0-9]{1,7}s(:fallback)?(:dry-run)?\Z")   # one entry of boot_settings


def guarded_modes(note: dict | None) -> list:
    """The modes in which the instance's next start will be armed for real by its autostart hook, as far as the
    guard's last status tells: the hook is installed, and a start in that mode has settings to arm with (its own, or
    the other mode's by the fallback) that are not a dry run. In any other mode, and without a note, nothing watches a
    power-on between the click and the arm: it needs a console timer first."""
    if not note or note["autostart"] != "installed":
        return []
    found = [GUARD_SETTING_RE.match(tok) for tok in note["boot_settings"].split()]
    return sorted({m.group(1) for m in found if m and not m.group(3)})


def note_guard(st: dict) -> None:
    """Keep what a status of the guard says about the next start (autostart, boot_settings) in the local record, for
    the instance this run is bound to; auth show answers guard_at_boot from it. It never fails the command: a note
    that cannot be kept only means one provisional timer more."""
    if BOUND is None:
        return
    try:
        with Store() as s:
            s.data.setdefault("guards", {})[BOUND] = {"autostart": st.get("autostart", ""),
                                                      "boot_settings": st.get("boot_settings", ""), "at": now_s()}
            s.save()
    except StoreError as e:
        print(f"note: the local record could not be updated ({e.kind}), so it does not know whether the next start of "
              "this instance is guarded: the next power-on sets a provisional console timer", file=sys.stderr)


def forget_guard_note() -> None:
    """Drop what is kept about the next start of the instance this run is bound to. For when the guard could not say
    what it will do now, while an older answer may no longer hold (the hook taken out, an arm cut short, the script
    gone): with nothing kept, auth show names no guarded mode, and the next power-on sets a provisional timer instead
    of trusting the old answer."""
    if BOUND is None:
        return
    try:
        with Store() as s:
            if s.data.get("guards", {}).pop(BOUND, None) is not None:
                s.save()
    except StoreError:
        pass   # a record that cannot be used answers no auth show either, so no power-on goes by the old note


def note_guard_now(alias: str) -> None:
    """After a command that may have changed what the next start will do (an arm or an autostart command that
    succeeded, went wrong on the instance, or whose outcome is unknown): ask the guard and keep the answer. When the
    guard cannot be asked, the older answer is forgotten."""
    if BOUND is None:
        return
    try:
        r = ssh_run(alias, guard_cmd("status"), retry_uncertain=True)
    except (subprocess.TimeoutExpired, OSError, WrongHost):
        r = None
    if r is not None and r.rc == 0:
        note_guard(parse_status(text(r.stdout)))
    else:
        forget_guard_note()
        print("note: the guard's status could not be read after this command, so the local record does not know "
              "whether the next start of this instance is guarded (ctl status ALIAS brings it up to date): until "
              "then a power-on sets a provisional console timer", file=sys.stderr)


def _open_session(records: list) -> dict | None:
    return next((s for s in session_list(records) if s["off"] is None), None)


SAME_BOOT_S = 120   # T0 is noted before the power-on click; the container starts some seconds after it


def session_view(s: dict | None) -> dict | None:
    """The open session as show and usage print it."""
    return None if s is None else {"session": s["sid"], "on": s["on"], "mode": s["mode"], "gpus": s["gpus"],
                                   "price": fen_to_yuan(s["price_fen_h"])}


def boot_time(v: int, now: int) -> int:
    """--booted-at: the booted_at that ctl status gave, a unix time not after now (five minutes of slack, as for --at)."""
    if not 0 < v <= now + 300:
        raise ValueError(f"--booted-at {v}: the booted_at of ctl status, a unix time up to now ({now})")
    return v


def of_this_boot(op: dict | None, booted: int) -> bool:
    """Is the open session the power-on of the boot that began at BOOTED? Such a session began at its T0, shortly
    before the container started, or later (a take-over noted afterwards). One that began more than SAME_BOOT_S
    before the boot belongs to an earlier power-on whose power-off was never recorded."""
    return op is not None and op["on"] >= booted - SAME_BOOT_S


def ledger_on(recs: list, at: int, mode: str, gpus: int, price: int, req: str | None) -> tuple:
    """Record a power-on: (session id, "recorded" | "resend" | "mismatch", note). A resend of the same power-on is
    recognized by its key (with --req), or without --req as the open session with the same time, mode, price and
    GPUs; after a closed session the same values make a new one. Refused leaves everything as it was."""
    same = (at, mode, gpus, price)
    ons = [r for r in recs if r["kind"] == "on"]
    if req:
        for sid in (req, "m" + req):
            old = next((r for r in ons if r["sid"] == sid), None)
            if old is not None:
                if (old["at"], old["mode"], old["gpus"], old["price_fen_h"]) != same:
                    raise Refused(f"request {req} already has a power-on with another time, mode, price or GPU count: "
                                  "a resend must give the same --at and fields as the first time")
                return sid, "resend" if sid == req else "mismatch", None if sid == req else _mismatch_note(req)
    op = _open_session(recs)
    if not req and op is not None and op["sid"].startswith("t") and (op["on"], op["mode"], op["gpus"],
                                                                       op["price_fen_h"]) == same:
        return op["sid"], "resend", None   # the open session is this very power-on (a closed one would not be)
    if op is not None:
        if op["on"] >= at:    # it cannot be an earlier power-on: a conversation that took this boot over recorded it
            raise Refused(f"session {op['sid']} (on since {op['on']}) is open on this instance and began no earlier than "
                          f"this power-on ({at}): it is this very boot, recorded by a conversation that took the instance "
                          "over. Record nothing more for it: check its mode, price and GPU count (ctl auth show), then "
                          "release your own request if you hold one (ctl auth release), and tell the user")
        raise Refused(f"session {op['sid']} (on since {op['on']}) is still open on this instance: record its power-off "
                      "first (ctl log off --at <the time from the console's billing detail>), then this power-on")
    rec = {"kind": "on", "at": at, "mode": mode, "gpus": gpus, "price_fen_h": price}
    if req:
        r = next((x for x in open_reservations(recs) if x["req"] == req), None)
        if r is not None and (r["mode"], r["gpus"], r["price_fen_h"]) == (mode, gpus, price):
            sid, state, note = req, "recorded", None
            rec["req"] = req
        else:
            sid, state, note = "m" + req, "mismatch", _mismatch_note(req)
            rec["attempted_req"] = req
    else:
        n = 1 + sum(r["sid"].startswith(f"t{at}-") for r in ons)
        sid, state, note = f"t{at}-{n}", "recorded", None
        rec["unreserved"] = True
    recs.append({"kind": "on", "key": f"on:{sid}", "sid": sid, **{k: v for k, v in rec.items() if k != "kind"}})
    return sid, state, note


def _mismatch_note(req: str) -> str:
    return (f"the power-on does not match reservation {req} (another mode, price or GPU count, or the reservation is "
            f"gone): the session is recorded apart and the reservation still counts; check it, then run ctl auth release "
            f"--instance ID --req {req}")


def ledger_off(recs: list, at: int, req: str | None) -> tuple:
    """Record a power-off of the open session: (session id or None, "recorded" | "resend" | "no open session", note)."""
    names = {req, "m" + req} if req else None
    op = _open_session(recs)
    if op is None or (names and op["sid"] not in names):
        offs = [r for r in recs if r["kind"] == "off" and (names is None or r["sid"] in names)]
        if offs and offs[-1]["at"] == at:
            return offs[-1]["sid"], "resend", None
        if op is not None:
            raise Refused(f"the open session is {op['sid']}, not {req}")
        return None, "no open session", "no session is open on this instance: only the project log was written"
    if at < op["on"]:
        raise Refused(f"the power-off ({at}) is before the power-on of session {op['sid']} ({op['on']})")
    recs.append({"kind": "off", "key": f"off:{op['sid']}", "sid": op["sid"], "at": at})
    return op["sid"], "recorded", None


def cmd_auth_grant(a) -> int:
    iid = need_instance(a.instance)
    check_alias(a.alias)
    budget = parse_budget(a.budget)
    if not TZ_RE.match(a.period_tz or ""):
        raise ValueError(f"--period-tz {a.period_tz!r}: a whole-hour offset like +08:00")
    if not a.quote.strip():
        raise ValueError("--quote: the user's own words are needed")
    with Store() as st:
        now = now_s()
        old = st.data["grants"].get(iid)
        # a new grant drops every earlier consent, and the mark that the charges were read (charges_read)
        g = {"alias": a.alias, "usage": a.usage, "budget": budget, "period": a.period, "period_tz": a.period_tz,
             "quote": a.quote, "at": now, "approvals": {}}
        if a.period == "none" and budget["kind"] != "none":
            # "this much in all, from now on": it counts from the full hour of the grant. Another amount is another
            # total and keeps the start; another unit, a grant after a monthly one, or after a revoke, starts anew.
            # A grant from before the start was kept counted everything the ledger holds, and goes on doing so (0)
            same = old is not None and old["period"] == "none" and old["budget"]["kind"] == budget["kind"]
            g["since"] = old.get("since", 0) if same else now // 3600 * 3600
        st.data["grants"][iid] = g
        st.data["ledger"].setdefault(iid, [])   # the instance stays known after a revoke
        st.save()
    res = {"instance": iid, "grant": g}
    note = grant_note(g, iid, now)
    if note:
        res["note"] = note
    out(res)
    return 0


BASELINE = ("the charges of this period have not been imported under this grant yet (a new grant asks for it, and so "
            "does every new month), so what the instance spent in the period before now would count as nothing: read "
            "this instance's rows of this period in the console's billing detail and import them with ctl auth charges "
            "--instance {iid} --json '[...]' (an empty list, '[]', when there are none); auth check refuses until then")


def baseline_owed(g: dict | None, now: int) -> bool:
    """A monthly money budget covers everything the instance was charged in the month, so it is not judged before
    that month's charges were imported once under the grant in force: an import of an earlier month does not serve."""
    if not g or g["budget"]["kind"] != "fen" or g["period"] != "month":
        return False
    read = g.get("charges_read")
    return read is None or period_of(read, g)[0] != period_of(now, g)[0]


def grant_note(g: dict, iid: str, now: int) -> str | None:
    """What a new grant leaves to do, or to tell the user: where its budget counts from."""
    kind = g["budget"]["kind"]
    notes = []
    if baseline_owed(g, now):
        notes.append(f"{period_of(now, g)[0]}: " + BASELINE.format(iid=iid))
    if kind == "gpu_mh":
        notes.append("a GPU-hour budget counts only the power-ons this ledger records (from this computer, or taken over "
                     "here): what the instance used before, by hand or from another computer, is not in it, and charges "
                     "cannot add it. Tell the user so. To count earlier use, record it as a closed session at its true "
                     "times: ctl log on --instance ID --at <start> --field mode=gpu --field price=<yuan per hour> --field "
                     "gpus=<count> --field time=estimated, then ctl log off --instance ID --at <end>")
    if g.get("since") == 0:
        notes.append("this budget without periods was first granted before ctl kept a start: it counts everything the "
                     "ledger holds, as it did, and another amount keeps it so; to count from now on, ctl auth revoke "
                     "and grant again")
    elif "since" in g:
        notes.append(f"a budget without periods counts from {g['since']} (the full hour of its first grant): sessions and "
                     "charges before that are not in it, and none need importing. A later grant with another amount "
                     "changes the total and keeps this start; to start over, ctl auth revoke and grant again")
    return " | ".join(notes) or None


def cmd_auth_show(a) -> int:
    if a.booted_at is not None and not a.instance:
        raise ValueError("--booted-at goes with --instance: the boot of which instance?")
    with Store() as st:
        now = now_s()
        booted = None if a.booted_at is None else boot_time(a.booted_at, now)
        ids = [need_instance(a.instance)] if a.instance else sorted(set(st.data["grants"]) | set(st.data["ledger"]))
        res = {}
        for iid in ids:
            g = st.data["grants"].get(iid)
            recs = st.data["ledger"].get(iid, [])
            key, s, e = period_of(now, g or DEFAULT_PERIOD)
            op = _open_session(recs)
            item = {"grant": g, "period": key, "spent_fen": spent_fen(recs, s, e, now),
                    "spent_gpu_s": spent_gpu_s(recs, s, e, now),
                    "open_reservations": [r["req"] for r in open_reservations(recs)],
                    "open_session": session_view(op),
                    "guard_at_boot": guarded_modes(st.data.get("guards", {}).get(iid))}
            if booted is not None:   # when taking over a running instance: is its power-on in the ledger?
                item["this_boot"] = ("recorded" if of_this_boot(op, booted) else
                                     "not recorded" if op is None else "an earlier session is still open")
            if g and g["budget"]["kind"] == "fen":
                item["remaining_fen"] = g["budget"]["value"] - item["spent_fen"]
            elif g and g["budget"]["kind"] == "gpu_mh":
                item["remaining_gpu_hours"] = round((g["budget"]["value"] * 18 - item["spent_gpu_s"] * 5) / 18000, 4)
            if baseline_owed(g, now):   # the figures above lack what was spent in the period before any import
                item["baseline"] = f"{key}: " + BASELINE.format(iid=iid)
            res[iid] = item
    out({"now": now, "grants": res})
    return 0


def cmd_auth_revoke(a) -> int:
    iid = need_instance(a.instance)
    with Store() as st:
        had = st.data["grants"].pop(iid, None) is not None
        st.save()
    out({"instance": iid, "revoked": had, "note": "the ledger is kept and goes on recording"})
    return 0


def cmd_auth_approve(a) -> int:
    iid = need_instance(a.instance)
    if not a.quote.strip():
        raise ValueError("--quote: the user's own words are needed")
    with Store() as st:
        g = st.data["grants"].get(iid)
        if g is None:
            out({"ok": False, "reason": f"no grant for {iid}"})
            return EXIT_NO_GRANT
        now = now_s()
        reach = [k for k, _, _ in periods_touching(now, now + MAX_DURATION_S + 600, g)]   # the longest window's
        key = reach[0] if a.period is None else a.period
        if key not in reach:
            raise ValueError(f"--period {key!r}: a consent is recorded for {' or '.join(reach)} (this period, or one "
                             "a check made now can reach)")
        g["approvals"][key] = {"quote": a.quote, "at": now}
        st.save()
    out({"instance": iid, "period": key, "approval": g["approvals"][key]})
    return 0


def cmd_auth_clock(a) -> int:
    """For a record that refuses because this clock is behind last_seen (an earlier run saw a clock that was ahead):
    once the user has confirmed that the clock is right now, last_seen is lowered to the current time, inside the
    record's own lock and write path. Never by editing the file. Refused when an imported charge is dated ahead of
    now; open sessions and reservations that the earlier clock dated ahead are listed (dated_ahead)."""
    if not a.quote.strip():
        raise ValueError("--quote: the user's own words are needed")
    with Store() as st:
        now, was = now_s(), st.prev_last_seen
        ledgers = sorted(st.data["ledger"].items())
        # a charge is dated by the platform, and was not ahead of this clock when it was imported: one that lies ahead
        # now proves the clock behind. (A power-off proves nothing: its time may be this machine's own.)
        proof = next(((iid, r) for iid, recs in ledgers for r in recs
                      if r["kind"] == "charge" and r["at"] > now + 300), None)
        if now < was and proof is not None:
            out({"last_seen": was, "was": was, "lowered": False, "quote": a.quote,
                 "error": f"this clock ({now}) is behind, whatever was said: the ledger of {proof[0]} holds a charge dated "
                          f"{proof[1]['at']}, and that time came from the platform. Fix the clock; last_seen stays as it is"})
            return EXIT_ERR
        if now < was:
            st.data["last_seen"] = now
            st.save()
        res = {"last_seen": st.data["last_seen"], "was": was, "lowered": now < was, "quote": a.quote}
        ahead = []   # what a clock that was ahead left behind and still counts: open sessions and open reservations
        for iid, recs in ledgers:
            op = _open_session(recs)
            if op is not None and op["on"] > now + 300:
                ahead.append({"instance": iid, "kind": "on", "at": op["on"], "session": op["sid"]})
            ahead += [{"instance": iid, "kind": "reserve", "at": r["at"], "req": r["req"]}
                      for r in open_reservations(recs) if r["at"] > now + 300]
        if ahead:
            res.update(dated_ahead=ahead, note=(
                "written while this clock was ahead: such an open session counts nothing until its time comes and cannot be "
                "logged off at the true time. Tell the user; with their word close it with ctl log off --instance ID "
                "--void --quote '<their words>' and import the charges. If that instance is still on, its boot is then "
                "off the books: record it again as a take-over does (ctl status, then ctl log on --instance ID "
                "--booted-at <booted_at> --field ...). Release such a reservation with ctl auth release"))
    out(res)
    return 0


def _judge(g: dict, recs: list, now: int, expires: int, mode: str, gpus: int, price: int, window_s: int) -> tuple:
    """Every period the window touches: (periods, failure or None). A failure is the first period over the budget,
    or left under 20 % of it without the user's consent for that period."""
    b, periods = g["budget"], []
    need_f, need_s = window_fen(now, window_s, price), window_s * gpus if mode == "gpu" else 0
    for key, s, e in periods_touching(now, expires, g):
        p = {"period": key}
        over = near = False
        if b["kind"] == "fen":
            spent = spent_fen(recs, s, e, now)
            rem = b["value"] - spent - need_f
            p.update(budget_fen=b["value"], spent_fen=spent, need_fen=need_f, remaining_fen=rem)
            over, near = rem < 0, 5 * rem < b["value"]
        elif b["kind"] == "gpu_mh":   # compared in whole numbers: spent_s * 5 <= budget_mh * 18
            spent = spent_gpu_s(recs, s, e, now)
            units = b["value"] * 18 - (spent + need_s) * 5
            p.update(budget_gpu_mh=b["value"], spent_gpu_s=spent, need_gpu_s=need_s,
                     remaining_gpu_hours=round(units / 18000, 4))
            over, near = units < 0, 5 * units < b["value"] * 18
        periods.append(p)
        if over:
            fail = dict(p, reason=f"over the budget for {key}")
        elif near and key not in g["approvals"]:   # a consent counts for its own period only
            fail = dict(p, reason=f"this would leave under 20 % of the budget for {key}: ask the user first, and record "
                                  f"a yes with ctl auth approve --instance ID --period {key} --quote '<their words>'")
        else:
            continue
        if b["kind"] == "fen":   # what is left now, without this power-on
            fail["remaining_fen"] = b["value"] - fail["spent_fen"]
        else:
            fail["remaining_gpu_hours"] = round((b["value"] * 18 - fail["spent_gpu_s"] * 5) / 18000, 4)
        return periods, fail
    return periods, None


def cmd_auth_check(a) -> int:
    """The gate before a power-on (design 4.2 step 4): exit 0 with a reservation, 10 over or near the budget, 11 when
    the record cannot be trusted, 12 without a grant for this use. With --probe the same judgement and nothing
    reserved: for going on with an instance that is already on past what the last check or probe covered (a take-over,
    another job, a quiet period, a keep, a later deadline), where the open session is the only thing that can spend
    this instance's budget; the answer names the open session it counted. Exit 1, with nothing judged, when the
    judgement would lack something: a monthly money budget whose period's charges were not imported since the grant,
    or a probe for an instance whose boot is not on the books."""
    iid = need_instance(a.instance)
    price = yuan_to_fen(a.price, "--price")
    if (a.mode == "gpu" and a.gpus < 1) or (a.mode == "nogpu" and a.gpus != 0):
        raise ValueError("--gpus: at least 1 in GPU mode, 0 without GPUs")
    window_s = parse_hours_s(a.hours)
    try:
        with Store() as st:
            now = now_s()
            g = st.data["grants"].get(iid)
            if g is None or g["usage"] not in ("both", a.mode):
                out({"ok": False, "instance": iid, "reason": f"no grant for {a.mode} use of {iid}: ask the user, "
                                                             "then ctl auth grant"})
                return EXIT_NO_GRANT
            prev = st.prev_last_seen
            if now < prev and (period_of(now, g)[0] != period_of(prev, g)[0] or prev - now >= 600):
                out({"ok": False, "instance": iid, "reason": (
                    f"this clock ({now}) is behind the latest time ctl has seen ({prev}), so the budget cannot be "
                    "judged. Either this clock is wrong now (fix it), or an earlier run saw a clock that was ahead: "
                    "then, once the user confirms this clock is right, run ctl auth clock --quote '<their words>', "
                    f"which lowers last_seen in {st.path} under the record's lock (do not edit the file)")})
                return EXIT_STORE
            recs = st.data["ledger"].setdefault(iid, [])
            said = {"ok": False, "instance": iid, **({"probe": True} if a.probe else {})}
            if baseline_owed(g, now):
                out({**said, "reason": f"{period_of(now, g)[0]}: " + BASELINE.format(iid=iid)})
                return EXIT_ERR
            counted = {}
            if a.probe:   # a probe is about an instance that is on: its boot must be on the books, and is named
                counted["open_session"] = session_view(_open_session(recs))
                if counted["open_session"] is None:
                    out({**said, "open_session": None, "reason": (
                        "no session is open on this instance in the ledger, so the judgement would lack what this boot "
                        "has used so far. Record the power-on first: ctl status ALIAS gives booted_at, then ctl log on "
                        f"--instance {iid} --booted-at <booted_at> --field mode=... --field price=... --field gpus=...; "
                        "then probe again")})
                    return EXIT_ERR
            expires = now + window_s + 600
            periods, fail = _judge(g, recs, now, expires, a.mode, a.gpus, price, window_s)
            if fail is not None:
                out({"ok": False, "instance": iid, **fail, "periods": periods, **counted})
                return EXIT_BUDGET
            if not a.probe:
                _hook("check-decided")
                req = secrets.token_hex(8)
                recs.append({"kind": "reserve", "key": f"reserve:{req}", "req": req, "at": now, "expires": expires,
                             "mode": a.mode, "gpus": a.gpus, "price_fen_h": price, "window_s": window_s})
                st.save()
    except StoreError as e:
        out({"ok": False, "instance": iid, "reason": f"the local record cannot be used, so no power-on: {e}"})
        return EXIT_STORE
    if a.probe:
        res = {"ok": True, "probe": True, "instance": iid, "periods": periods, **counted}
    else:
        res = {"ok": True, "instance": iid, "req": req, "expires": expires, "periods": periods,
               "next": f"note T0 with ctl now and power on, then ctl log on --instance ID --req {req} --at <T0> "
                       f"--field ...; a resend gives the same --at. If it does not come on, ctl auth release "
                       f"--instance ID --req {req}"}
    if g["budget"]["kind"] == "fen":
        res["remaining_fen"] = min(p["remaining_fen"] for p in periods)
        res["hours_left"] = round(res["remaining_fen"] / price, 2) if price else None
    elif g["budget"]["kind"] == "gpu_mh" and a.mode == "gpu":
        res["hours_left"] = round(min(p["remaining_gpu_hours"] for p in periods) / a.gpus, 3)
    out(res)
    return 0


def cmd_auth_release(a) -> int:
    iid = need_instance(a.instance)
    if not REQ_RE.match(a.req or ""):
        raise ValueError(f"--req {a.req!r}: the request ID auth check gave")
    with Store() as st:
        recs = st.data["ledger"].get(iid)
        if recs is None or not any(r["key"] == f"reserve:{a.req}" for r in recs):
            raise ValueError(f"no reservation {a.req} for {iid}")
        if any(r["key"] == f"release:{a.req}" for r in recs):
            out({"instance": iid, "released": a.req, "note": "it was released before"})
            return 0
        if any(r["kind"] == "on" and r.get("req") == a.req for r in recs):
            raise ValueError(f"reservation {a.req} was used by a power-on; power off and log off instead")
        recs.append({"kind": "release", "key": f"release:{a.req}", "req": a.req, "at": now_s()})
        st.save()
    out({"instance": iid, "released": a.req})
    return 0


def _charge_row(row, iid: str, now: int) -> dict:
    if not isinstance(row, dict) or set(row) != {"serial", "instance", "time", "amount"}:
        raise ValueError(f"a row needs exactly serial, instance, time and amount: {row!r}")
    if not _match(SERIAL_RE, row["serial"]):
        raise ValueError(f"serial {row['serial']!r}: letters, digits and ._- only")
    if row["instance"] != iid:
        raise ValueError(f"row {row['serial']} is for {row['instance']!r}, not {iid}")
    try:
        t = dt.datetime.fromisoformat(str(row["time"]))
    except ValueError:
        raise ValueError(f"row {row['serial']}: time {row['time']!r} is not a date and time") from None
    at = int((t if t.tzinfo else t.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))).timestamp())
    if at > now + 300:
        raise ValueError(f"row {row['serial']}: its time is in the future")
    if at < 0:
        raise ValueError(f"row {row['serial']}: its time is before 1970")
    fen = yuan_to_fen(row["amount"], f"row {row['serial']}: amount")
    if fen < 1:
        raise ValueError(f"row {row['serial']}: the amount must be positive")
    return {"kind": "charge", "key": f"charge:{row['serial']}", "serial": row["serial"], "at": at, "fen": fen}


def cmd_auth_charges(a) -> int:
    """Charges read from the console's billing detail, checked as a batch: one bad row and nothing is written. An
    empty list says that the billing detail was read and shows none. Under a grant, the time of this import is kept
    (charges_read): a monthly money budget is not judged before it."""
    iid = need_instance(a.instance)
    raw = a.json if a.json is not None else pathlib.Path(normalize_local(a.file)).read_text(encoding="utf-8")
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError("the charges are a JSON list of rows ([] when the billing detail shows none)")
    with Store() as st:
        now = now_s()
        new = [_charge_row(r, iid, now) for r in rows]
        recs = st.data["ledger"].get(iid)
        if recs is None:
            raise ValueError(f"{iid} is not known here: grant it, or log a power-on for it, first")
        have = {r["key"]: r for r in recs}
        added = 0
        for c in new:
            old = have.get(c["key"])
            if old is not None and old != c:
                raise ValueError(f"charge {c['serial']} is already recorded with another time or amount")
            if old is None:
                have[c["key"]] = c
                recs.append(c)
                added += 1
        res = {"instance": iid, "imported": added, "already": len(new) - added}
        g = st.data["grants"].get(iid)
        if g is not None:
            g["charges_read"] = res["charges_read"] = now
        if added or g is not None:
            st.save()
    out(res)
    return 0


# ---- status, check, wait, deploy ----
CLK_TCK_ECHO = ' && echo "clk_tck=$(getconf CLK_TCK 2>/dev/null)"'   # only after a status that worked


def booted_at(st: dict, now: int) -> int | None:
    """The unix time at which this boot of the container began, on this machine's clock, or None when it cannot be
    worked out. The guard gives up (whole seconds of kernel uptime) and boot (the start of PID 1 in clock ticks after
    the kernel's start), the instance its clock tick: their difference is how long the container has been up, a
    duration that no clock setting touches. NOW is this machine's time when that answer arrived. So the result is on
    the clock that T0 and the ledger use, however far the instance's own clock is off; it is late by the few seconds
    the answer took. A take-over counts the session from here."""
    up, boot, tck = (st.get(k, "") for k in ("up", "boot", "clk_tck"))
    if not all(v.isdigit() for v in (up, boot, tck)) or int(tck) == 0:
        return None
    age = int(up) - int(boot) // int(tck)
    return now - age if age >= 0 else None


def cmd_status(a) -> int:
    if not reachable(a.alias):
        out({"alias": a.alias, "reachable": False,
             "note": "not reachable over SSH; read the instance state in the AutoDL console"})
        return EXIT_UNREACHABLE
    r = ssh_run(a.alias, guard_cmd("status") + CLK_TCK_ECHO, retry_uncertain=True)
    answered = now_s()   # before anything else is asked of the instance: booted_at counts back from here
    # What this answer says about the next start goes into the local record at once: the next question may fail
    # (a connection lost, another host), and a power-on must not go by an older answer then
    st = parse_status(text(r.stdout)) if r.rc == 0 else None
    if st is not None:
        note_guard(st)
    elif r.state != "not_run" and r.state != "uncertain" and r.rc != 255:
        forget_guard_note()   # the instance answered and the guard did not: nothing will arm its next start either
    mode, ngpu = detect_mode(a.alias)
    res = {"alias": a.alias, "reachable": True, "detected_mode": mode, "gpus": ngpu}
    if r.rc != 0:
        if r.state == "not_run":
            res.update(guard=f"unknown: ssh did not get through ({r.attempts} attempts); try again",
                       ssh_said=log_tail(r.log))
            out(res)
            return EXIT_UNREACHABLE
        if r.state == "uncertain" or r.rc == 255:   # it may have run, but its answer never arrived
            res.update(guard="unknown: the status command did not report back (timeout or lost connection); "
                             "try again", ssh_said=log_tail(r.log))
            out(res)
            return EXIT_UNCERTAIN
        res.update(guard="not deployed or failed", stderr=text(r.stderr)[-400:])
        out(res)
        return EXIT_ERR
    # the 0.8 guard gives its own *_in_s values; they are kept as given
    now, hb = st.get("now", ""), st.get("heartbeat", "")
    st["heartbeat_age_s"] = int(now) - int(hb) if now.isdigit() and hb.isdigit() and int(hb) else None
    st["booted_at"] = booted_at(st, answered)
    if st.get("armed_by") == "boot":
        st["note"] = ("armed at container start by autostart, without env_setup: arm before running jobs "
                      "(the arm replaces the autostart's)")
    res.update(st)
    out(res)
    return 0


def cmd_check(a) -> int:
    alias = check_alias(a.alias)
    if a.instance is not None and not INSTANCE_RE.match(a.instance):
        raise ValueError(f"bad instance ID {a.instance!r}: use the ID the console shows, like abcd123456-1234abcd")
    ssh_path = find_ssh()
    res: dict = {"alias": alias, "ssh": ssh_path}
    v = RUNNER([ssh_path, "-V"], input=None, capture_output=True, timeout=15)
    res["ssh_version"] = (text(v.stderr) or text(v.stdout)).strip()
    if a.config:   # how ssh resolves the alias: not needed to verify an instance, asked for when an alias does not connect
        g = RUNNER([ssh_path, "-G", "--", alias], input=None, capture_output=True, timeout=15)
        cfg = {}
        for line in text(g.stdout).splitlines():
            k, _, val = line.partition(" ")
            if k in ("hostname", "port", "user", "identityfile", "batchmode") and k not in cfg:
                cfg[k] = val
        res["config"] = cfg
    res["reachable"] = reachable(alias)
    if res["reachable"] and a.instance is not None:
        return check_instance(alias, a.instance, res)
    out(res)
    return 0 if res["reachable"] else EXIT_UNREACHABLE


def find_bash() -> str | None:
    """The bash that runs scripts/ctl: Git Bash on Windows (the bash.exe in System32 is WSL's, not this one)."""
    if os.name != "nt":
        return shutil.which("bash")
    cands = []
    git = shutil.which("git")
    if git:   # <Git>/cmd/git.exe, <Git>/bin/git.exe, or <Git>/mingw64/bin/git.exe inside Git Bash: look a few levels up
        here = pathlib.Path(git).resolve().parent
        for root in (here, *list(here.parents)[:3]):
            cands += [root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"]
    cands += [pathlib.Path("C:/Program Files/Git/bin/bash.exe"), pathlib.Path("C:/Program Files/Git/usr/bin/bash.exe")]
    return next((str(c) for c in cands if c.exists()), None)


def ssh_check() -> tuple:
    """(ok, what): ssh takes -G and -E the way ctl uses them. -G only prints the settings, so nothing connects."""
    try:
        ssh = find_ssh()
    except OSError as e:
        return False, str(e)
    try:
        v = RUNNER([ssh, "-V"], input=None, capture_output=True, timeout=15)
        version = (text(v.stderr) or text(v.stdout)).strip()
        with tempfile.TemporaryDirectory() as td:
            log = pathlib.Path(td) / "ssh.log"
            r = RUNNER([ssh, "-G", "-E", log.as_posix(), "--", "autodl-doctor-probe"], input=None, capture_output=True,
                       timeout=15)
            ok = r.returncode == 0 and log.exists()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{ssh} does not run: {e}"
    return ok, (f"{ssh} ({version}): " + ("-G and -E work" if ok else
                f"ssh -G -E exited {r.returncode} or wrote no log file; ctl needs both (OpenSSH 7 or later)"))


def cmd_doctor(a) -> int:
    """Each thing ctl relies on, checked here and now: ok when all are."""
    checks = []

    def item(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
    good = sys.version_info >= (3, 8) and hasattr(tarfile, "data_filter")
    item("python", good, f"{sys.version.split()[0]} at {sys.executable}" + ("" if good else ": ctl needs 3.8 or later "
         "with tarfile.data_filter (3.8.17, 3.9.17, 3.10.12, 3.11.4, 3.12 and later)"))
    bash = find_bash()
    if bash is None:
        item("bash", False, "no bash found" + (" (install Git for Windows: its Git Bash runs scripts/ctl)"
                                               if os.name == "nt" else ""))
    else:
        try:
            r = RUNNER([bash, "--version"], input=None, capture_output=True, timeout=15)
            said = (text(r.stdout).splitlines() or [""])[0]
            item("bash", r.returncode == 0 and "bash" in said, f"{bash}: {said or 'no answer'}")
        except (OSError, subprocess.TimeoutExpired) as e:
            item("bash", False, f"{bash} does not run: {e}")
    item("ssh", *ssh_check())
    try:
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td) / "a dir with spaces 中文"
            d.mkdir()
            (d / "a file 文件.txt").write_text("autodl", encoding="utf-8")
            item("paths", (d / "a file 文件.txt").read_text(encoding="utf-8") == "autodl",
                 "a directory and a file named with spaces and Chinese: written and read back")
    except OSError as e:
        item("paths", False, f"a name with spaces and Chinese could not be used: {e}")
    try:
        with Store() as st:
            item("local record", True, f"{st.home} opens and is private")
    except StoreError as e:
        item("local record", False, f"{e.kind}: {e}")
    if os.environ.get("MSYSTEM") and os.environ.get("MSYS_NO_PATHCONV") != "1":
        item("launcher", False, "Git Bash without scripts/ctl would turn /root/... arguments into Windows paths: run "
                                "ctl through scripts/ctl")
    else:
        item("launcher", True, "arguments reach ctl as written")
    if a.alias:
        ok = reachable(check_alias(a.alias))
        item("alias", ok, f"{a.alias} " + ("answers over SSH" if ok else "does not answer over SSH (is it on?)"))
    all_ok = all(c["ok"] for c in checks)
    out({"ok": all_ok, "checks": checks})
    return 0 if all_ok else EXIT_ERR


def check_instance(alias: str, iid: str, res: dict) -> int:
    """Does ALIAS lead to the instance IID? Its host name is autodl-container-<ID>. A match is remembered in the
    local record (the only way an alias becomes a verified name for an instance); a mismatch is exit 13."""
    r = ssh_run(alias, "uname -n", retry_uncertain=True)   # read-only, safe to repeat
    res["instance"] = iid
    if r.rc != 0:
        res.update(instance_match=None, ssh_said=log_tail(r.log), stderr=text(r.stderr)[-400:])
        out(res)
        if r.state == "not_run":
            return EXIT_UNREACHABLE
        return EXIT_UNCERTAIN if r.state == "uncertain" or r.rc == 255 else EXIT_ERR
    res["hostname"] = text(r.stdout).strip()
    res["instance_match"] = res["hostname"] == f"autodl-container-{iid}"
    if not res["instance_match"]:
        res["note"] = f"{alias} leads to {res['hostname']}, not to the instance {iid}: fix the alias or the ID"
        out(res)
        return EXIT_MISMATCH
    try:
        with Store() as st:
            st.data["aliases"][alias] = {"instance": iid, "at": now_s()}
            st.save()
        res["recorded"] = True
    except StoreError as e:
        res.update(recorded=False, store_error=str(e))
        out(res)
        return EXIT_STORE
    out(res)
    return 0


def cmd_wait(a) -> int:
    check_alias(a.alias)
    limit = time.monotonic() + parse_duration_s(a.timeout)
    while True:
        if a.state == "up":
            if reachable(a.alias, tries=1):   # the loop itself retries every --every seconds
                mode, n = detect_mode(a.alias)
                res = {"alias": a.alias, "state": "up", "mode": mode, "gpus": n}
                if mode == "unknown":
                    res["error"] = "cannot tell GPU from non-GPU mode; check nvidia-smi and the console"
                    out(res)
                    return EXIT_ERR
                if a.mode and a.mode != mode:
                    res["error"] = f"expected {a.mode} mode, instance is in {mode} mode"
                    out(res)
                    return EXIT_ERR
                out(res)
                return 0
        else:
            state, failed = watch_down(a.alias, limit, a.every)
            if state == "unreachable":
                out({"alias": a.alias, "state": "unreachable", "confirmed": False, "note": UNREACHABLE_NOTE})
                return 0
            out({"alias": a.alias, "state": "timeout", "wanted": "down", "last": state, "failed_in_a_row": failed})
            return EXIT_ERR
        if time.monotonic() >= limit:
            out({"alias": a.alias, "state": "timeout", "wanted": a.state})
            return EXIT_ERR
        time.sleep(a.every)


def cmd_deploy(a) -> int:
    data = LOCAL_GUARD.read_bytes()
    if data.count(13):
        print("refusing to deploy: autodl_guard.sh contains CR bytes (CRLF line endings)", file=sys.stderr)
        return EXIT_ERR
    digest = hashlib.sha256(data).hexdigest()
    q = shlex.quote
    # each deploy writes its own temporary file and checks it before it replaces the guard, so a
    # connection cut mid-way, or a second deploy at the same time, never installs a partial file
    remote = (f"mkdir -p {q(GUARD_HOME)} && T=\"$(mktemp {q(GUARD_HOME)}/.deploy-XXXXXX)\" && "
              "trap 'rm -f -- \"$T\"' EXIT && cat > \"$T\" && "
              f"echo '{digest}  '\"$T\" | sha256sum -c --status - && mv -f -- \"$T\" {q(GUARD_PATH)} && "
              f"sha256sum {q(GUARD_PATH)}")
    r = ssh_run(a.alias, remote, stdin=data, retry_uncertain=True)   # safe to repeat: whole file or nothing
    if r.rc != 0:
        print(text(r.stderr) + f"ssh: {log_tail(r.log)}", file=sys.stderr)
        if r.state == "not_run":
            return EXIT_UNREACHABLE
        return EXIT_UNCERTAIN if r.state == "uncertain" or r.rc == 255 else EXIT_ERR
    got = (text(r.stdout).split() or [""])[0]
    if got != digest:
        print(f"sha256 mismatch: local {digest} remote {got}", file=sys.stderr)
        return EXIT_ERR
    res = {"deployed": True, "path": GUARD_PATH, "sha256": digest,
           "note": "a daemon of the old version runs on until the next start; to upgrade from 0.7, shut down, start "
                   "again, then deploy and arm (0.8 refuses to arm next to a running 0.7 daemon)"}
    rc = 0
    if a.no_autostart:
        res["autostart"] = "skipped"
    else:
        res["autostart"], rc = install_autostart(a.alias)
        if res["autostart"] == "installed":   # newly: the system disk was likely replaced (a new image)
            res["calibration_forget"], frc = forget_calibrations(a.alias)
            rc = rc or frc
    out(res)
    return rc


def install_autostart(alias: str) -> tuple[str, int]:
    """The guard's install-autostart after a deploy: (what happened, exit code). Repeating it is harmless."""
    r = ssh_run(alias, guard_cmd("install-autostart"), retry_uncertain=True)
    said = " ".join((text(r.stdout) + text(r.stderr)).split())
    if r.state == "not_run":
        return f"not sent: ssh did not get through ({r.attempts} attempts): {log_tail(r.log)}", EXIT_UNREACHABLE
    if r.state == "uncertain" or r.rc == 255:
        return f"uncertain: the install did not report back: {log_tail(r.log)}", EXIT_UNCERTAIN
    if r.rc == 0 and text(r.stdout).startswith("autostart already installed"):
        return "already", 0
    if r.rc == 0 and text(r.stdout).startswith("autostart installed"):
        return "installed", 0
    return f"failed: {said}", EXIT_ERR


def _guard_status(alias: str) -> tuple:
    """(parsed status, 0) or (None, exit code) after reporting why."""
    r = ssh_run(alias, guard_cmd("status"), retry_uncertain=True)
    if r.rc == 0:
        return parse_status(text(r.stdout)), 0
    out({"alias": alias, "error": "the guard's status could not be read", "ssh_said": log_tail(r.log),
         "stderr": text(r.stderr)[-400:]})
    if r.state == "not_run":
        return None, EXIT_UNREACHABLE
    return None, EXIT_UNCERTAIN if r.state == "uncertain" or r.rc == 255 else EXIT_ERR


def _undisturbed(st: dict) -> dict:
    """What other commands change, and so must not change while the instance is sampled: the jobs (with their times),
    the arm, the deadline, the keep, off-when-done. Not the last activity: the guard moves it at every check that
    finds a signal over its threshold, and that is just the idle noise calibrating is for."""
    return {"jobs": sorted((j["name"], j["state"], j["start"], j["end"]) for j in st["jobs"]),
            **{k: st.get(k) for k in ("armed_at", "deadline_at", "keep_until_at", "off_when_done")}}


def cmd_calibrate(a) -> int:
    """Measure the idle noise with the guard's own sample (one reading a minute) and keep thresholds for this
    instance, mode, environment and guard; --forget deletes them. Only a complete, undisturbed run is kept."""
    alias = check_alias(a.alias)
    if not a.forget and not 2 <= a.minutes <= 30:
        raise ValueError("--minutes: from 2 to 30")
    with Store() as st:
        iid = BOUND or (st.data["aliases"].get(alias) or {}).get("instance")
        if iid is None:
            raise ValueError(f"{alias} is not verified: run ctl check {alias} --instance ID first")
        if a.forget:
            n = len(st.data["calib"].pop(iid, []))
            st.save()
            out({"instance": iid, "forgot": n})
            return 0
    before, rc = _guard_status(alias)
    if rc:
        return rc
    running = [j["name"] for j in before["jobs"] if j["state"] == "running"]
    if running:
        out({"instance": iid, "refused": f"jobs are running ({', '.join(running)}): calibrate while the instance is "
                                         "idle, and do not use it meanwhile"})
        return EXIT_REFUSED
    r = ssh_run(alias, FINGERPRINT_PROBE, retry_uncertain=True)
    if r.rc != 0:
        out({"instance": iid, "error": f"cannot calibrate: the fingerprint probe failed ({r.state})",
             "ssh_said": log_tail(r.log)})
        if r.state == "not_run":
            return EXIT_UNREACHABLE
        return EXIT_UNCERTAIN if r.state == "uncertain" or r.rc == 255 else EXIT_ERR
    p = parse_probe(text(r.stdout))
    if p["host"] is None:   # checked here, not left to the fingerprint's reasons
        out({"instance": iid, "error": f"cannot calibrate: the host name could not be read, so whether {alias} still "
                                       f"leads to {iid} cannot be told"})
        return EXIT_ERR
    if p["host"] != f"autodl-container-{iid}":
        out({"instance": iid, "error": f"{alias} now leads to {p['host']}, not to the instance {iid} it was verified "
                                       f"for: nothing was measured; check it again with ctl check {alias} --instance ID"})
        return EXIT_MISMATCH
    if p["fingerprint"] is None:
        out({"instance": iid, "error": "cannot calibrate: " + p["why"]})
        return EXIT_ERR
    k = 3 if p["mode"] == "gpu" else 0   # the guard's own rhythm: 3 GPU probes a minute with a GPU
    r = ssh_run(alias, guard_cmd("sample", "--every", "60s", "--count", str(a.minutes), "--gpu-samples", str(k)),
                timeout=a.minutes * 60 + 120)
    if r.rc != 0:
        out({"instance": iid, "error": "the sample did not finish: nothing was kept", "ssh_said": log_tail(r.log)})
        if r.state == "not_run":
            return EXIT_UNREACHABLE
        return EXIT_UNCERTAIN if r.state == "uncertain" or r.rc == 255 else EXIT_ERR
    try:
        intervals = parse_sample(text(r.stdout), a.minutes, 60, k)
    except ValueError as e:
        out({"instance": iid, "error": f"the sample cannot be used, nothing was kept: {e}"})
        return EXIT_ERR
    after, rc = _guard_status(alias)
    if rc:
        return rc
    u0, u1 = _undisturbed(before), _undisturbed(after)
    changed = [k for k in u0 if u0[k] != u1[k]]
    if changed:
        out({"instance": iid, "error": f"{', '.join(changed)} changed while the instance was sampled (another command "
                                       "used it): nothing was kept; calibrate again while nobody uses it"})
        return EXIT_ERR
    entry = {"id": "c" + secrets.token_hex(5), "mode": p["mode"], "fingerprint": p["fingerprint"],
             "guard": p["guard"], "at": now_s(), **calib_result(intervals, p["mode"])}
    with Store() as st:
        st.data["calib"].setdefault(iid, []).append(entry)
        st.save()
    out({"instance": iid, **entry, "lightest_work": LIGHTEST[p["mode"]],
         "note": "the idle side only; arm passes these with --calib-coverage unverified"})
    return 0


# ---- guard commands ----
ARM_VALUE_OPTIONS = ("deadline", "keep", "grace", "interval", "gpu_probes", "thr_gpu", "thr_cpu", "thr_io", "thr_net",
                     "unreliable", "calib", "calib_coverage", "env_setup")
# How an arm or an autostart command ended when the guard is to be asked what the next start will do: it succeeded,
# failed on the instance (possibly half-way), or nobody knows. Not after a refusal (3, 4, 5, 7, 8), which changes
# nothing, nor when the command never got there (2)
NEXT_START_MAY_DIFFER = (0, EXIT_ERR, EXIT_UNCERTAIN)


def cmd_arm(a) -> int:
    for d in (a.idle, a.deadline, a.keep, a.grace, a.interval):
        if d:
            parse_duration_s(d)
    refuse_windows_path("--env-setup", a.env_setup)
    args = ["arm", "--idle", a.idle, "--mode", a.mode]
    for name in ARM_VALUE_OPTIONS:   # only what was given; the guard checks the values
        val = getattr(a, name)
        if val:
            args += ["--" + name.replace("_", "-"), val]
    args += [flag for flag, on in (("--dry-run", a.dry_run), ("--rearm", a.rearm)) if on]
    extra, said = calibration_args(a)
    args += extra
    print(f"calibration: {said}")
    # with a request id the guard answers a resend of the same arm with success, not exit 5
    args += ["--req", secrets.token_hex(8)]
    rc = passthrough(ssh_run(a.alias, guard_cmd(*args), retry_uncertain=True))
    if rc in NEXT_START_MAY_DIFFER:   # the arm kept settings for the next start in this mode, or was cut short
        note_guard_now(a.alias)
    return rc


def cmd_revive(a) -> int:
    # a plain revive is harmless to repeat; a repeated --restart would stop the daemon the first one started
    return passthrough(ssh_run(a.alias, guard_cmd("revive", *(["--restart"] if a.restart else [])),
                               retry_uncertain=not a.restart))


def cmd_run(a) -> int:
    if not JOB_RE.match(a.name) or a.name == "guard":
        raise ValueError(f"bad job name {a.name!r} (letters, digits and ._-, at most 64; 'guard' is reserved)")
    refuse_windows_path("--cmd", a.cmd)
    refuse_windows_path("--log", a.log)
    cmd = a.cmd if a.cmd is not None else pathlib.Path(normalize_local(a.cmd_file)).read_text(encoding="utf-8")
    data = cmd.encode("utf-8")
    # the request id makes a resend harmless (the guard does not start the same request again, and
    # answers exit 6 when it cannot tell whether the first start happened), and the checksum makes
    # the guard refuse a command that arrived incomplete
    if a.quiet:
        parse_duration_s(a.quiet)
    args = ["run", a.name, "--req", secrets.token_hex(8), "--cmd-sha256", hashlib.sha256(data).hexdigest(),
            "--cmd-stdin"]
    if a.then_off:
        args.append("--then-off")
    if a.quiet:
        args += ["--quiet", a.quiet]
    if a.log:
        args += ["--log", a.log]
    return passthrough(ssh_run(a.alias, guard_cmd(*args), stdin=data, retry_uncertain=True))


def cmd_keep(a) -> int:
    parse_duration_s(a.duration)
    return passthrough(ssh_run(a.alias, guard_cmd("keep", a.duration, "--reason", a.reason)))


def cmd_quiet(a) -> int:
    """The running job NAME is in use for DUR from now (design 5.3); like keep, not sent again when uncertain."""
    if not JOB_RE.match(a.name) or a.name == "guard":
        raise ValueError(f"bad job name {a.name!r}")
    parse_duration_s(a.duration)
    return passthrough(ssh_run(a.alias, guard_cmd("quiet", a.name, a.duration, "--reason", a.reason)))


def cmd_off_when_done(a) -> int:
    return passthrough(ssh_run(a.alias, guard_cmd("off-when-done", "--reason", a.reason), retry_uncertain=True))


def cmd_deadline(a) -> int:
    parse_duration_s(a.duration)
    return passthrough(ssh_run(a.alias, guard_cmd("deadline", a.duration)))


def cmd_autostart(a) -> int:
    """install or uninstall the guard's autostart hook (the guard does the work and says what happened)."""
    rc = passthrough(ssh_run(a.alias, guard_cmd(f"{a.action}-autostart"), retry_uncertain=True))
    if rc in NEXT_START_MAY_DIFFER:
        note_guard_now(a.alias)
    return rc


def cmd_version(a) -> int:
    print(CTL_VERSION)
    return 0


def cmd_tail(a) -> int:
    if not JOB_RE.match(a.name):   # "guard" shows the guard's own log
        raise ValueError(f"bad job name {a.name!r}")
    return passthrough(ssh_run(a.alias, guard_cmd("logtail", a.name, str(a.lines)), retry_uncertain=True))


def not_sent(alias: str, what: str, r: Result) -> int:
    out({"alias": alias, "state": "not sent", "attempts": r.attempts, "ssh_said": log_tail(r.log),
         "note": f"{what} never reached the instance: ssh failed before any session existed. "
                 "The instance may already be off, or sshd is refusing new connections (MaxStartups); "
                 f"check the AutoDL console, then send {what} again."})
    sys.stderr.write(text(r.stderr)[-400:])
    return EXIT_UNREACHABLE


def wait_down(alias: str, limit: float, said: str, hint: str, shutdown: str | None = None) -> int:
    """After a shutdown was issued, or may have been: wait until SSH stays down. Even then
    the result is "confirmed": false; the console is where a shutdown is confirmed."""
    state, failed = watch_down(alias, limit, 10)
    extra = {"shutdown": shutdown} if shutdown else {}
    if state == "unreachable":
        out({"alias": alias, "state": "unreachable", "confirmed": False, **extra, "said": said,
             "note": UNREACHABLE_NOTE})
        return 0
    out({"alias": alias, "state": state, "failed_in_a_row": failed, **extra, "said": said, "hint": hint})
    return EXIT_ERR


def off_now_outcome(r: Result | None) -> str:
    """What an off-now that did not end in a refusal did: "committed" (the guard said so), "started" (it began on
    the instance, then the connection dropped, as it does when the instance stops) or "uncertain"."""
    if r is None:
        return "uncertain"
    if any(COMMIT_LINE.fullmatch(ln.strip()) for ln in text(r.stdout).splitlines()):
        return "committed"
    return "started" if r.started or r.rc == 0 else "uncertain"


def cmd_off_now(a) -> int:
    args = ["off-now", "--reason", a.reason] + (["--force"] if a.force else [])
    if a.sample is not None:
        args += ["--sample", str(a.sample)]
    limit = time.monotonic() + parse_duration_s(a.wait)
    said = ""
    try:
        r = ssh_run(a.alias, guard_cmd(*args))   # uncertain is not resent: the wait below finds out
    except subprocess.TimeoutExpired:
        r = None   # the guard may be stopping the container; find out below
    if r is not None:
        if r.state == "not_run":
            return not_sent(a.alias, "off-now", r)
        lines = [ln.strip() for ln in text(r.stdout).splitlines()]
        if any(DRY_RUN_LINE.fullmatch(ln) for ln in lines):   # committed, but a dry run never shuts down
            return passthrough(r)
        committed = any(COMMIT_LINE.fullmatch(ln) for ln in lines)
        if not committed and r.state == "started" and r.rc not in (0, 255):   # refused (3, 4, 7, 8) or failed
            return passthrough(r)
        said = text(r.stdout).strip() or f"uncertain whether off-now ran (ssh: {log_tail(r.log)})"
    # committed, the connection dropped as the container stopped, uncertain, or timed out
    return wait_down(a.alias, limit, said, "read the guard log (tail ALIAS guard) and check the console",
                     off_now_outcome(r))


# off-raw refuses (exit 3) while anything could be work: a screen session that is not dead (a socket
# that `screen -ls` marks "(Dead ???)" is left over from an earlier boot), a tmux session, a job the guard
# counts as running, or any of these checks that cannot give a clear answer (fail closed). A screen listing
# counts only when it is whole: its last line counts every session line, and screen ended the way it does
# after a listing (exit 1 in screen 4.2.0, 0 in 4.6.2 and the current source); "No Sockets found" is exit 1
# in all three.
OFF_RAW_CHECK = (
    't() { if command -v timeout > /dev/null 2>&1; then timeout 10 "$@"; else "$@"; fi; }; busy=""; '
    'if command -v screen > /dev/null 2>&1; then s="$(t screen -ls 2>&1)"; rc=$?; '
    's="$(printf "%s\\n" "$s" | tr -d "\\r")"; '
    'all="$(printf "%s\\n" "$s" | grep -cE "^[[:space:]]+[0-9]+\\.[^[:space:]]")"; '
    'dead="$(printf "%s\\n" "$s" | grep -E "^[[:space:]]+[0-9]+\\.[^[:space:]]" | '
    'grep -cE "[[:space:]]\\(Dead \\?\\?\\?\\)$")"; '
    'if [ "$all" != "$dead" ]; then busy="$busy screen"; '
    'elif [ "$all" = 0 ]; then { [ "$rc" = 1 ] && printf "%s\\n" "$s" | grep -q "^No Sockets found in "; } '
    '|| busy="$busy screen:unknown"; '
    'else { { [ "$rc" = 0 ] || [ "$rc" = 1 ]; } && printf "%s\\n" "$s" | grep -qE "^$all Sockets? in "; } '
    '|| busy="$busy screen:unknown"; fi; fi; '
    'if command -v tmux > /dev/null 2>&1; then s="$(t tmux ls 2>&1)"; rc=$?; '
    'if [ "$rc" = 0 ]; then busy="$busy tmux"; '
    'elif ! printf "%s\\n" "$s" | grep -qiE "no server running|error connecting|no such file"; '
    'then busy="$busy tmux:unknown"; fi; fi; '
    f'if [ -f {GUARD_PATH} ]; then s="$(t bash {GUARD_PATH} status 2>&1)"; rc=$?; '
    'if [ "$rc" != 0 ]; then busy="$busy guard:unknown"; '
    'elif printf "%s\\n" "$s" | grep -q "^job\\..*=running|"; then busy="$busy guard-job"; fi; fi; '
    'if [ -n "$busy" ]; then echo "refused: in use or cannot tell:$busy (use off-now if a guard runs; '
    '--force only with the user\'s consent)"; exit 3; fi; '
)
OFF_RAW_SAMPLE_S = 5   # the live sample's seconds, as off-now's


def off_raw_script(sample_s: int = OFF_RAW_SAMPLE_S, final: str = "/usr/bin/shutdown") -> str:
    """What an off-raw that is not forced runs, in one remote shell, with this guard script on its stdin: the checks
    above, then the guard's read-only idle-check, then the shutdown. Sessions and registered jobs do not show work
    started in Jupyter or over a plain ssh; the live sample (off-now's: the activity signals over a few seconds, by
    this boot's arm when it has a usable one, else by the mode seen and the default thresholds) does while it is
    active. The script comes from here, so an instance without a guard, or with an older one, is judged alike. It
    is read whole before anything else: nothing the checks start can read it away, and a refusal by them does not
    leave the sender with input nobody takes. Only the sample's own "idle: ..." line with exit 0 lets the shutdown
    through; anything else (in use, cannot tell, a script that did not arrive or not whole, which bash would end
    quietly with 0) ends the shell with exit 3."""
    return ('G="$(cat)"; ' + OFF_RAW_CHECK +
            f's="$(printf "%s\\n" "$G" | bash -s -- idle-check --sample {int(sample_s)} 2>&1)"; rc=$?; '
            'case "$rc:$s" in "0:idle: "*) ;; *) echo "refused: in use or cannot tell (the live sample; --force only '
            'with the user\'s consent):"; printf "%s\\n" "$s"; exit 3 ;; esac; ' + final)


def cmd_off_raw(a) -> int:
    """For an instance on which the guard cannot work (it is not deployed, or its daemon does not start): run the
    official shutdown directly. Unless --force (only with the user's on-the-spot consent) it refuses while a screen
    or tmux session or a registered job exists, and while a live sample shows activity (off_raw_script)."""
    limit = time.monotonic() + parse_duration_s(a.wait)
    said = ""
    remote, script = "/usr/bin/shutdown", None
    if not a.force:
        script = LOCAL_GUARD.read_bytes()
        if script.count(13):   # bash would not read it: the sample would fail, and every off-raw be refused
            print("off-raw: autodl_guard.sh contains CR bytes (CRLF line endings), so the live sample cannot run; "
                  "nothing was sent", file=sys.stderr)
            return EXIT_ERR
        remote = off_raw_script()
    try:
        r = ssh_run(a.alias, remote, stdin=script)
    except subprocess.TimeoutExpired:
        r = None
    if r is not None:
        if r.state == "not_run":
            return not_sent(a.alias, "off-raw", r)
        if r.state == "started" and r.rc not in (0, 255):
            return passthrough(r)
        said = ((text(r.stdout) + text(r.stderr)).strip()
                or f"uncertain whether off-raw ran (ssh: {log_tail(r.log)})")
    return wait_down(a.alias, limit, f"{a.reason}: {said}",
                     "check the console; if it still runs, use its power-off button")


# ---- file transfer ----
def extract_stream(fileobj, dest: pathlib.Path) -> None:
    """Extract a tar stream; the 'data' filter rejects absolute paths, '..' and links out of dest."""
    with tarfile.open(fileobj=fileobj, mode="r|") as tf:
        tf.extractall(dest, filter="data")


def commit_staged(got: pathlib.Path, target: pathlib.Path, overwrite: bool) -> pathlib.Path | None:
    """Move a finished download into place. An existing target is first renamed to
    <name>.bak-<time>-<random> (only with overwrite) and put back if the move fails or is
    interrupted (Ctrl+C included). Nothing is deleted. Returns the backup path, if one was made."""
    backup = None
    if target.exists() or target.is_symlink():
        if not overwrite:
            raise FileExistsError(f"{target} already exists; add --overwrite to replace it")
        backup = target.with_name(f"{target.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}")
    try:
        if backup is not None:
            os.rename(target, backup)
        os.rename(got, target)
    except BaseException:
        if backup is not None and os.path.lexists(backup) and not os.path.lexists(target):
            os.rename(backup, target)
        raise
    return backup


def pull_remote_cmd(remote: str) -> tuple[str, str]:
    remote = (remote or "").rstrip("/")
    parent, _, base = remote.rpartition("/")
    if base in ("", ".", "..") or any(ord(c) < 32 for c in remote):
        raise ValueError(f"bad remote path {remote!r}: name a file or directory")
    parent = parent or ("/" if remote.startswith("/") else ".")
    return base, f"tar -C {shlex.quote(parent)} -cf - -- {shlex.quote(base)}"


def _drain(stream, sink: list) -> threading.Thread:
    t = threading.Thread(target=lambda: sink.append(stream.read()), daemon=True)
    t.start()
    return t


def pull_once(alias: str, remote_cmd: str, staging: pathlib.Path, timeout_s: int):
    """One download into `staging`. Returns (exit code, state, problem, stderr, ssh log)."""
    err: list = []
    fired: list = []
    problem = None
    with SshLog() as lg:
        with POPEN(ssh_argv(alias, remote_line(remote_cmd), lg.arg),
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
            drain = _drain(p.stderr, err)

            def on_timeout():
                fired.append(1)
                p.kill()
            timer = threading.Timer(timeout_s, on_timeout)
            timer.daemon = True   # never keeps ctl alive after an interrupt
            timer.start()
            try:
                extract_stream(p.stdout, staging)
            except (tarfile.TarError, OSError) as e:
                problem = str(e)
                p.kill()
            finally:
                try:
                    rc = p.wait()
                finally:
                    timer.cancel()
                drain.join(timeout=5)
        log = lg.read()
    started, errb = split_marker(b"".join(err))
    host = wrong_host(rc, errb)
    if host is not None:
        raise WrongHost(alias, host)
    if fired:
        problem = f"timed out after {timeout_s}s"
    return rc, classify(rc, started, log), problem, errb, log


def cmd_pull(a) -> int:
    refuse_windows_path("remote path", a.remote_path)
    base, remote_cmd = pull_remote_cmd(a.remote_path)
    dest = pathlib.Path(normalize_local(a.local_dir)).resolve()
    target = dest / base
    if (target.exists() or target.is_symlink()) and not a.overwrite:
        print(f"pull: {target} already exists; add --overwrite to replace it (the old copy is kept as a backup)",
              file=sys.stderr)
        return EXIT_ERR
    timeout_s = parse_duration_s(a.timeout)
    dest.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, SSH_TRIES + 1):
        staging = pathlib.Path(tempfile.mkdtemp(prefix=".autodl-pull-", dir=dest))
        try:
            rc, state, problem, errb, log = pull_once(a.alias, remote_cmd, staging, timeout_s)
            never_started = state == "not_run"
            if rc == 255 and attempt < SSH_TRIES:   # nothing was put in place, so trying again is harmless
                time.sleep(SSH_RETRY_GAP)
                continue
            if rc == 0 and not problem:
                got = staging / base
                if got.exists() or got.is_symlink():
                    backup = commit_staged(got, target, a.overwrite)   # only after ssh and tar succeeded
                    res = {"pulled": a.remote_path, "to": str(target)}
                    if backup is not None:
                        res["backup"] = str(backup)
                    out(res)
                    return 0
                problem = f"the archive does not contain {base!r}"
            why = (f"ssh did not get through ({attempt} attempts)" if never_started
                   else problem or f"ssh/tar exit code {rc}")
            print(f"pull failed: {why}; nothing local was changed. ssh: {log_tail(log)}", file=sys.stderr)
            print(text(errb)[-800:], file=sys.stderr)
            return EXIT_UNREACHABLE if never_started else EXIT_ERR
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    raise AssertionError("unreachable")


def push_remote_cmd(parent: str, name: str, overwrite: bool) -> str:
    """Instance side of push: unpack into a new temporary directory next to the target and
    move the result in only when tar succeeded. An existing target is refused (exit 3) or,
    with overwrite, renamed to <name>.bak-<time>-<pid>; if the script ends early, even by a
    signal, the exit trap puts that copy back and removes the temporary directory."""
    q = shlex.quote
    return "\n".join([
        f"P={q(parent)}; N={q(name)}; O={1 if overwrite else 0}; B=\"\"",
        'mkdir -p -- "$P" || exit 1',
        'if [ "$O" != 1 ] && { [ -e "$P/$N" ] || [ -L "$P/$N" ]; }; then',
        '  echo "push: $P/$N already exists" >&2; exit 3',
        'fi',
        'T="$(mktemp -d "$P/.autodl-push-XXXXXX")" || exit 1',
        "trap 'if [ -n \"$B\" ] && [ ! -e \"$P/$N\" ] && [ ! -L \"$P/$N\" ]; then mv -- \"$B\" \"$P/$N\"; fi; "
        "rm -rf -- \"$T\"' EXIT",
        "trap 'exit 1' HUP INT TERM PIPE",
        'tar --no-same-owner -C "$T" -xf - || exit 1',
        '[ -e "$T/$N" ] || [ -L "$T/$N" ] || { echo "push: the archive does not contain $N" >&2; exit 1; }',
        'if [ -e "$P/$N" ] || [ -L "$P/$N" ]; then',
        '  [ "$O" = 1 ] || { echo "push: $P/$N appeared meanwhile" >&2; exit 3; }',
        '  B="$P/$N.bak-$(date +%Y%m%d-%H%M%S)-$$"',
        '  mv -- "$P/$N" "$B" || exit 1',
        'fi',
        'if ! mv -- "$T/$N" "$P/$N"; then [ -z "$B" ] || mv -- "$B" "$P/$N"; exit 1; fi',
        '[ -z "$B" ] || echo "backup=$B"',
        'echo "pushed=$P/$N"',
    ])


def push_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    """Modes as the instance should see them (its tar runs as root and keeps them): 755 for directories, 644 for
    files, 755 for a file that is executable here. Never on Windows, where execute bits follow the file name."""
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if ti.isdir():
        ti.mode = 0o755
    elif ti.isreg():
        ti.mode = 0o755 if os.name != "nt" and ti.mode & 0o111 else 0o644
    return ti


def push_once(alias: str, src: pathlib.Path, remote_cmd: str, timeout_s: int):
    """One upload. Returns (exit code, state, problem, stdout, stderr, ssh log, timed out)."""
    outb: list = []
    err: list = []
    fired: list = []
    problem = None
    with SshLog() as lg:
        with POPEN(ssh_argv(alias, remote_line(remote_cmd), lg.arg),
                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
            drains = (_drain(p.stdout, outb), _drain(p.stderr, err))

            def on_timeout():
                fired.append(1)
                p.kill()
            timer = threading.Timer(timeout_s, on_timeout)   # covers the upload itself, not only the wait
            timer.daemon = True   # never keeps ctl alive after an interrupt
            timer.start()
            try:
                with tarfile.open(fileobj=p.stdin, mode="w|") as tf:
                    tf.add(str(src), arcname=src.name, filter=push_filter)
            except (OSError, tarfile.TarError) as e:   # also a remote side that stopped reading early
                problem = str(e)
            finally:
                try:
                    p.stdin.close()
                except OSError:
                    pass
                try:
                    rc = p.wait()
                finally:
                    timer.cancel()
                for d in drains:
                    d.join(timeout=5)
        log = lg.read()
    started, errb = split_marker(b"".join(err))
    host = wrong_host(rc, errb)
    if host is not None:
        raise WrongHost(alias, host)
    if fired:
        problem = f"timed out after {timeout_s}s"
    return rc, classify(rc, started, log), problem, b"".join(outb), errb, log, bool(fired)


def cmd_push(a) -> int:
    refuse_windows_path("remote directory", a.remote_dir)
    src = pathlib.Path(normalize_local(a.local_path)).resolve()
    if not src.exists():
        print(f"push: {src} does not exist", file=sys.stderr)
        return EXIT_ERR
    parent = (a.remote_dir or "").rstrip("/") or "/"
    if not a.remote_dir or any(ord(c) < 32 for c in parent):
        raise ValueError(f"bad remote directory {a.remote_dir!r}")
    target = f"{parent.rstrip('/')}/{src.name}"
    remote_cmd = push_remote_cmd(parent, src.name, a.overwrite)
    timeout_s = parse_duration_s(a.timeout)
    for attempt in range(1, SSH_TRIES + 1):
        rc, state, problem, outb, errb, log, timed_out = push_once(a.alias, src, remote_cmd, timeout_s)
        if state == "not_run" and not timed_out and attempt < SSH_TRIES:   # only then did nothing happen
            time.sleep(SSH_RETRY_GAP)
            continue
        break
    if rc == 3 and not timed_out:
        print(f"push: {target} already exists on the instance; add --overwrite to replace it "
              f"(the old copy is kept as {src.name}.bak-*)", file=sys.stderr)
        return EXIT_ERR
    if state == "not_run" and not timed_out:
        print(f"push failed: ssh did not get through ({attempt} attempts); nothing was sent. "
              f"ssh: {log_tail(log)}", file=sys.stderr)
        return EXIT_UNREACHABLE
    if rc == 255 or (timed_out and state != "not_run"):
        print(f"push: the connection failed before the instance reported back, so the upload may or may not "
              f"be in place. Check {target} (and any {src.name}.bak-* next to it) before repeating. "
              f"ssh: {log_tail(log)}", file=sys.stderr)
        return EXIT_UNCERTAIN
    if rc != 0 or problem:
        print(f"push failed: {problem or f'ssh/tar exit code {rc}'}; the target on the instance was not changed",
              file=sys.stderr)
        print(text(errb)[-800:], file=sys.stderr)
        return EXIT_ERR
    info = dict(line.split("=", 1) for line in text(outb).splitlines() if "=" in line)
    res = {"pushed": str(src), "to": info.get("pushed", target)}
    if "backup" in info:
        res["backup"] = info["backup"]
    out(res)
    return 0


# ---- power log and usage ----
def log_path(project: str) -> pathlib.Path:
    return pathlib.Path(normalize_local(project)).resolve() / ".autodl" / "power_log.jsonl"


def cmd_now(a) -> int:
    print(now_s())   # for T0 in shells without `date +%s` (PowerShell)
    return 0


def append_project_log(p: pathlib.Path, rec: dict) -> bool:
    """Append rec as one JSON line, unless that very line is there already (a resend); True when written."""
    line = json.dumps(rec, ensure_ascii=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and line in p.read_text(encoding="utf-8").splitlines():
        return False
    with p.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return True


def log_on_values(rec: dict) -> tuple:
    """The mode, GPU count and price (fen per hour) of a power-on, all required: without them nothing is written."""
    mode = rec.get("mode")
    if mode not in ("gpu", "nogpu"):
        raise ValueError("log on needs --field mode=gpu or --field mode=nogpu")
    if "price" not in rec:
        raise ValueError("log on needs --field price=<yuan per hour, as the confirmation box shows it>")
    price = yuan_to_fen(rec["price"], "price")
    g = rec.get("gpus", "0" if mode == "nogpu" else None)
    if g is None or not re.match(r"^[0-9]{1,3}\Z", g) or (int(g) < 1 if mode == "gpu" else int(g) != 0):
        raise ValueError("log on needs --field gpus=<count>: at least 1 in GPU mode, 0 (or none) without GPUs")
    return mode, int(g), price


def cmd_log(a) -> int:
    """The project log, and for a power-on or power-off of a known instance (an instance ID, or an alias verified
    with check --instance) the ledger first: the budget never counts less than was used."""
    now = now_s()
    if a.booted_at is not None:   # a take-over: the session is the boot's, counted from its start
        if a.event != "on" or a.req is not None or a.at is not None:
            raise ValueError("--booted-at is for log on when taking over an instance that is already on: without --req "
                             "(nothing was reserved) and without --at (the session begins at the boot)")
        at = boot_time(a.booted_at, now)
    else:
        at = now if a.at is None else a.at
    if a.void and (a.event != "off" or a.at is not None):
        raise ValueError("--void is for log off, without --at: it closes the open session at its own start, for a "
                         "session that no time can close")
    if bool(a.void) != (a.quote is not None) or (a.void and not a.quote.strip()):
        raise ValueError("--quote goes with --void and with nothing else, and --void needs it: the user's own words "
                         "for closing a session without its time on the books")
    if not 0 < at <= now + 300:   # as for charges: a time already past, or at most five minutes ahead of this clock
        raise ValueError(f"--at {at}: a unix time after 1970 and at most five minutes after now ({now})")

    def stamp(t: int) -> str:
        return dt.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")

    rec = {"t": stamp(at), "event": a.event, "instance": a.instance}
    for kv in a.field or []:
        k, sep, v = kv.partition("=")
        if not sep or not k:
            print(f"log: field {kv!r} must look like key=value", file=sys.stderr)
            return EXIT_ERR
        if k in ("t", "event", "instance", "req"):
            raise ValueError(f"--field {k}=...: t, event, instance and req are the log's own keys")
        rec[k] = v
    if a.req is not None:
        if not REQ_RE.match(a.req):
            raise ValueError(f"--req {a.req!r}: the request ID auth check gave")
        rec["req"] = a.req
    on = missing = None
    if a.event == "on":
        try:
            on = log_on_values(rec)
        except ValueError as e:
            if a.booted_at is None:
                raise
            missing = e   # a take-over whose power-on is already in the ledger needs no values; decided below
    res, rc = {"logged": rec}, 0
    if a.event in ("on", "off"):
        try:
            with Store() as st:
                iid = resolve_instance(st, a.instance)
                op = None if iid is None else _open_session(st.data["ledger"].get(iid, []))
                if a.void:   # a session that cannot be dated (its start is later than every time that could close it)
                    if iid is None:
                        raise Refused(f"{a.instance} is not an instance ID nor an alias verified with ctl check ALIAS "
                                      "--instance ID: nothing to void")
                    if op is None:
                        raise Refused("no session is open on this instance: there is nothing to void")
                    at = op["on"]
                    rec.update(t=stamp(at), time="void", quote=a.quote)
                if a.booted_at is not None and of_this_boot(op, at):
                    if on is not None and on[1] != op["gpus"]:   # close in time, but other GPUs (0 is the mode without)
                        raise Refused(f"session {op['sid']} (on since {op['on']}, {op['mode']}, {op['gpus']} GPU(s)) began "
                                      f"close to this boot ({at}), but this boot is given as {on[0]} with {on[1]} GPU(s). "
                                      "If the console's billing detail shows a power-off charge of this instance after "
                                      f"{op['on']} and before this boot ({at}), that session is an earlier power-on: record "
                                      "its power-off first (ctl log off --at <that charge>), then this power-on. If it "
                                      "shows none, the session is this boot's and one of the two is wrong: tell the user")
                    out({"logged": None, "instance_id": iid, "ledger": "already", "session": op["sid"],
                         "open_session": session_view(op),
                         "project_log": "not written: this power-on was recorded before",
                         "note": "the open session is this boot's: nothing was added, and its power-off will close it"})
                    return 0
                if a.booted_at is not None and op is not None:
                    raise Refused(f"session {op['sid']} (on since {op['on']}) began before this boot ({at}) and was "
                                  "never logged off: record its power-off first (ctl log off --at <the last charge of "
                                  f"this instance after {op['on']} and before this boot, from the console's billing "
                                  "detail>), then this power-on. If the billing detail shows no such charge, the "
                                  "instance has not been off since: the session is this boot's, and nothing is to be "
                                  "recorded")
                if missing is not None:
                    raise missing
                if iid is None:
                    res.update(ledger="unknown instance",
                               note=f"{a.instance} is not an instance ID nor an alias verified with ctl check ALIAS "
                                    "--instance ID: only the project log was written")
                else:
                    recs = st.data["ledger"].setdefault(iid, [])
                    n = len(recs)
                    sid, state, note = ledger_on(recs, at, *on, a.req) if on else ledger_off(recs, at, a.req)
                    if len(recs) != n:
                        st.save()
                    res.update(instance_id=iid, ledger=state, session=sid)
                    if note:
                        res["note"] = note
                    rc = EXIT_ERR if state == "mismatch" else 0
        except Refused as e:
            out({"logged": None, "error": str(e)})
            return EXIT_ERR
        except StoreError as e:
            if missing is not None:   # whether this boot is recorded cannot be told: its values are needed after all
                raise missing
            res["ledger"] = f"not recorded, the local record cannot be used: {e}"
            rc = EXIT_STORE
            if a.void:   # which session, and from when, is only in the record: nothing to write without it
                out(dict(res, logged=None))
                return rc
    try:
        res["project_log"] = "written" if append_project_log(log_path(a.project), rec) else "already there"
    except OSError as e:
        res["project_log"] = f"not written: {e}"
        out(res)
        return EXIT_STORE if rc == EXIT_STORE else EXIT_ERR
    out(res)
    return rc


def summarize(records: list, now: dt.datetime, since: dt.datetime | None = None) -> dict:
    """GPU hours = GPU-mode powered-on hours x GPU count; cost uses the hourly price logged at power-on.
    Records are paired in time order, those with the same time in the order they were written, so a record
    written late still pairs right. A session with no off record yet is counted up to now and flagged; one
    followed by another power-on before any power-off counts until that power-on and is named in
    closed_by_next_on."""
    per: dict = {}

    def close(s, t_end):
        t0, mode, price, gpus = s["open"]
        secs = max(0.0, (t_end - t0).total_seconds())
        if mode == "gpu":
            s["gpu_s"] += secs * gpus
        else:
            s["nogpu_s"] += secs
        s["cost"] += secs / 3600 * price

    timed = [(parse_when(r["t"]), r) for r in records]   # a time without an offset is local time
    timed.sort(key=lambda tr: tr[0])   # stable: records with the same time keep the written order
    for t, r in timed:
        if since is not None and t < since:
            continue
        s = per.setdefault(r.get("instance", "?"),
                           {"gpu_s": 0.0, "nogpu_s": 0.0, "cost": 0.0, "open": None, "last_off": None, "cut": []})
        if r.get("event") == "on":
            if s["open"]:   # no power-off since the last power-on: that session ends here
                s["cut"].append(s["open"][0].isoformat(timespec="seconds"))
                close(s, t)
            g = str(r.get("gpus") or "1")
            gpus = int(g) if g.isdigit() and int(g) > 0 else 1
            s["open"] = (t, r.get("mode", "gpu"), float(r.get("price") or 0), gpus)
        elif r.get("event") == "off":
            if s["open"]:
                close(s, t)
                s["open"] = None
            s["last_off"] = t
    res = {}
    for inst, s in per.items():
        o: dict = {}
        if s["open"]:
            close(s, now)
            o.update(running_since=s["open"][0].isoformat(timespec="seconds"),
                     running_mode=s["open"][1], includes_running_time=True)
        elif s["last_off"]:
            days = (now - s["last_off"]).total_seconds() / 86400
            o.update(days_off=round(days, 2), release_in_days_est=round(RELEASE_DAYS - days, 2))
        o.update(gpu_hours=round(s["gpu_s"] / 3600, 3), nogpu_hours=round(s["nogpu_s"] / 3600, 3),
                 est_cost_yuan=round(s["cost"], 2))
        if s["cut"]:
            o["closed_by_next_on"] = s["cut"]
        res[inst] = o
    return res


def parse_when(v: str) -> dt.datetime:
    if v.isdigit():
        return dt.datetime.fromtimestamp(int(v)).astimezone()
    w = dt.datetime.fromisoformat(v)
    return w if w.tzinfo else w.astimezone()


def usage_of_instance(name: str) -> int:
    """One instance across every project, from the ledger: hours, the estimate (never below the charges), charges."""
    with Store() as st:
        iid = resolve_instance(st, name)
        if iid is None or iid not in st.data["ledger"]:
            raise ValueError(f"the ledger has no records for {name} (an alias counts once ctl check ALIAS --instance "
                             "ID has verified it)")
        recs, now = st.data["ledger"][iid], now_s()
    secs = {"gpu": 0, "nogpu": 0}
    for s in session_list(recs):
        span = (now if s["off"] is None else s["off"]) - s["on"]
        secs[s["mode"]] += span * s["gpus"] if s["mode"] == "gpu" else span
    a_, b_ = _estimate_and_charged(recs, 0, 2 ** 62, now)
    res = {"instance": iid, "gpu_hours": round(secs["gpu"] / 3600, 3), "nogpu_hours": round(secs["nogpu"] / 3600, 3),
           "est_cost_yuan": fen_to_yuan(max(a_, b_)),
           "charged_yuan": fen_to_yuan(sum(r["fen"] for r in recs if r["kind"] == "charge")),
           "open_reservations": [r["req"] for r in open_reservations(recs)]}
    op = _open_session(recs)
    res["open_session"] = session_view(op)
    if op is not None:
        res["running_since"] = op["on"]
    out(res)
    return 0


def cmd_usage(a) -> int:
    if a.instance:
        return usage_of_instance(a.instance)
    p = log_path(a.project)
    recs = []
    if p.exists():
        recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    since = parse_when(a.since) if a.since else None
    out(summarize(recs, dt.datetime.fromtimestamp(now_s()).astimezone(), since))
    return 0


# ---- command line ----
# every other command that takes an alias is bound to an instance: check does the verifying itself, wait runs before
# it (and waits for an instance to go down, when nothing answers), doctor asks only whether the alias answers
UNBOUND = (cmd_check, cmd_wait, cmd_doctor)


class Parser(argparse.ArgumentParser):
    """A usage error is exit 1, like every other error: argparse's own 2 would read as "not sent, send again"."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERR, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = Parser(prog="autodl_ctl.py",
               description="Local helper of the autodl-gpu skill (SSH, guard, files, power log).")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, func, help_text, alias=True):
        sp = sub.add_parser(name, help=help_text)
        sp.set_defaults(func=func)
        if alias:
            sp.add_argument("alias", help="Host alias from ~/.ssh/config")
            if func not in UNBOUND:
                sp.add_argument("--instance", help="the instance ALIAS must lead to; default: the one it was verified "
                                                   "for (check ALIAS --instance ID). Every remote command first checks "
                                                   "that the host name is autodl-container-ID (exit 13 if not)")
        return sp

    add("status", cmd_status, "reachability, detected mode and guard state as JSON")
    s = add("check", cmd_check, "which ssh is used and whether the alias answers; with --instance, whether it "
                                "leads to that instance")
    s.add_argument("--config", action="store_true", help="also print how ssh resolves the alias (host, port, user, key "
                                                         "file): to find out why it does not connect")
    s.add_argument("--instance", help="the instance ID from the console: check that ALIAS leads to it (its host name "
                                      "is autodl-container-ID) and remember the pair in the local record")

    s = add("wait", cmd_wait, "wait until the instance is up (after power-on) or unreachable")
    s.add_argument("--state", choices=["up", "down"], default="up")
    s.add_argument("--mode", choices=["gpu", "nogpu"])
    s.add_argument("--timeout", default="10m")
    s.add_argument("--every", type=int, default=10, help="seconds between probes")

    s = add("deploy", cmd_deploy, "upload scripts/autodl_guard.sh, verify its sha256 and install the autostart hook")
    s.add_argument("--no-autostart", action="store_true", help="do not install the hook that runs the guard at start")

    s = add("autostart", cmd_autostart, "install or uninstall the hook that runs the guard at every container start")
    s.add_argument("action", choices=["install", "uninstall"])

    s = add("arm", cmd_arm, "configure this power-on (idle time and more) and start the guard daemon")
    s.add_argument("--idle", required=True, help="shut down once nothing is in use for this long, e.g. 15m")
    s.add_argument("--deadline")
    s.add_argument("--keep")
    s.add_argument("--grace")
    s.add_argument("--interval")
    s.add_argument("--mode", choices=["auto", "gpu", "nogpu"], default="auto")
    s.add_argument("--gpu-probes")
    s.add_argument("--thr-gpu")
    s.add_argument("--thr-cpu")
    s.add_argument("--thr-io")
    s.add_argument("--thr-net")
    s.add_argument("--unreliable", help="signals not to judge by, e.g. net or gpu,io")
    s.add_argument("--calib")
    s.add_argument("--calib-coverage", choices=["verified", "unverified"])
    s.add_argument("--env-setup")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--rearm", action="store_true", help="replace the configuration of this boot")

    s = add("calibrate", cmd_calibrate, "measure the idle noise (while nobody uses the instance) and keep thresholds")
    s.add_argument("--minutes", type=int, default=5, help="2 to 30; one reading a minute")
    s.add_argument("--forget", action="store_true", help="delete this instance's calibrations (after a new image)")

    s = add("revive", cmd_revive, "start the guard daemon again without changing any setting")
    s.add_argument("--restart", action="store_true", help="stop the running daemon first (after deploy)")

    s = add("run", cmd_run, "start a registered job in its own screen session")
    s.add_argument("name")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--cmd")
    g.add_argument("--cmd-file")
    s.add_argument("--then-off", action="store_true")
    s.add_argument("--quiet", help="the job counts as in use for this long from its start, e.g. 2h")
    s.add_argument("--log")

    s = add("quiet", cmd_quiet, "the running job NAME counts as in use for DUR from now")
    s.add_argument("name")
    s.add_argument("duration")
    s.add_argument("--reason", required=True)

    s = add("tail", cmd_tail, "show the end of a registered job's log (name 'guard': the guard's own log)")
    s.add_argument("name")
    s.add_argument("-n", "--lines", type=int, default=50)

    s = add("keep", cmd_keep, "keep the instance on for DUR from now")
    s.add_argument("duration")
    s.add_argument("--reason", required=True)

    s = add("off-when-done", cmd_off_when_done, "shut down once nothing is running")
    s.add_argument("--reason", required=True)

    s = add("off-now", cmd_off_now, "shut down now; refused (exit 3) while in use unless --force")
    s.add_argument("--reason", required=True)
    s.add_argument("--force", action="store_true", help="only with the user's explicit consent")
    s.add_argument("--sample", type=int, help="seconds of live sampling before deciding (the guard's default is 5)")
    s.add_argument("--wait", default="3m", help="how long to wait for SSH to stay down")

    s = add("off-raw", cmd_off_raw, "run /usr/bin/shutdown directly, only for an instance on which the guard cannot "
                                     "work; refused (exit 3) while a session or a registered job exists, or a live "
                                     "sample shows activity")
    s.add_argument("--reason", required=True)
    s.add_argument("--force", action="store_true", help="skip the checks; only with the user's explicit consent")
    s.add_argument("--wait", default="3m", help="how long to wait for SSH to stay down")

    s = add("deadline", cmd_deadline, "set the latest shutdown to now + DUR")
    s.add_argument("duration")

    s = add("pull", cmd_pull, "copy a remote file or directory into a local directory (tar over ssh)")
    s.add_argument("remote_path")
    s.add_argument("local_dir")
    s.add_argument("--overwrite", action="store_true", help="replace an existing copy, keeping it as .bak-*")
    s.add_argument("--timeout", default="60m")

    s = add("push", cmd_push, "copy a local file or directory into a remote directory (tar over ssh)")
    s.add_argument("local_path")
    s.add_argument("remote_dir", help="the directory that will contain it (its parent), e.g. /root/autodl-tmp")
    s.add_argument("--overwrite", action="store_true", help="replace an existing copy, keeping it as .bak-*")
    s.add_argument("--timeout", default="60m")

    s = add("log", cmd_log, "append an event to <project>/.autodl/power_log.jsonl", alias=False)
    s.add_argument("event", choices=["consent", "on", "off", "note"])
    s.add_argument("--instance", required=True)
    s.add_argument("--project", default=".")
    s.add_argument("--field", action="append", metavar="KEY=VALUE")
    s.add_argument("--at", type=int, help="unix time of the event (T0 for a power-on), at most five minutes ahead; "
                                          "default now. A resend gives the same --at")
    s.add_argument("--req", help="the request ID auth check gave for this power-on")
    s.add_argument("--booted-at", type=int, help="log on when taking over an instance that is already on: the "
                                                 "booted_at of ctl status. The session begins there; a power-on "
                                                 "already in the ledger is not recorded twice")
    s.add_argument("--void", action="store_true", help="log off without --at: close the open session at its own start, "
                                                       "for a session that no time can close (the last charge is before "
                                                       "its start, or its start is dated ahead of this clock); needs "
                                                       "--quote")
    s.add_argument("--quote", help="with --void: the user's own words")

    s = add("usage", cmd_usage, "GPU and non-GPU hours, estimated cost, release countdown", alias=False)
    s.add_argument("--project", default=".")
    s.add_argument("--since", help="only sessions started at or after this time (ISO or unix time)")
    s.add_argument("--instance", help="one instance across all projects, from the ledger (an ID or a verified alias)")

    s = add("auth", None, "the user's grants, the budget check before a power-on, the ledger's charges", alias=False)
    asub = s.add_subparsers(dest="auth_cmd", required=True)
    g = asub.add_parser("grant", help="record (or replace) what the user allows for one instance")
    g.set_defaults(func=cmd_auth_grant)
    g.add_argument("--instance", required=True)
    g.add_argument("--alias", required=True, help="shown only; check ALIAS --instance ID verifies an alias")
    g.add_argument("--usage", choices=["both", "gpu", "nogpu"], required=True)
    g.add_argument("--budget", required=True, help="none, an amount like 50yuan, or GPU hours like 12.5gpuh")
    g.add_argument("--period", choices=["month", "none"], required=True)
    g.add_argument("--period-tz", default="+08:00", help="the time zone of the month's boundary (AutoDL bills in +08:00)")
    g.add_argument("--quote", required=True, help="the user's own words")
    g = asub.add_parser("show", help="grants, this period's spending, open reservations, the open session")
    g.set_defaults(func=cmd_auth_show)
    g.add_argument("--instance")
    g.add_argument("--booted-at", type=int, help="with --instance, the booted_at of ctl status: also say whether "
                                                 "the power-on of this boot is in the ledger (this_boot)")
    g = asub.add_parser("clock", help="after the user confirmed this clock is right: lower last_seen to now")
    g.set_defaults(func=cmd_auth_clock)
    g.add_argument("--quote", required=True, help="the user's own words")
    g = asub.add_parser("revoke", help="remove the grant; the ledger stays and goes on recording")
    g.set_defaults(func=cmd_auth_revoke)
    g.add_argument("--instance", required=True)
    g = asub.add_parser("approve", help="the user's yes to go on near the end of a period's budget")
    g.set_defaults(func=cmd_auth_approve)
    g.add_argument("--instance", required=True)
    g.add_argument("--quote", required=True)
    g.add_argument("--period", help="the period auth check named (YYYY-MM, or all); default this period")
    g = asub.add_parser("check", help="before the power-on click: judge the budget and reserve (exit 0 only)")
    g.set_defaults(func=cmd_auth_check)
    g.add_argument("--instance", required=True)
    g.add_argument("--mode", choices=["gpu", "nogpu"], required=True)
    g.add_argument("--price", required=True, help="yuan per hour, as the confirmation box shows it")
    g.add_argument("--gpus", type=int, required=True, help="the GPU count in the instance's row; 0 without GPUs")
    g.add_argument("--hours", required=True, help="the planned window, e.g. 2 or 1.5")
    g.add_argument("--probe", action="store_true", help="judge only, reserve nothing: before anything that keeps an "
                                                         "instance on longer than the last check or probe covered (a "
                                                         "take-over, another job, a quiet period, a keep, a later "
                                                         "deadline), with --hours from now to the new expected end. "
                                                         "Refused (exit 1) while the ledger has no open session for "
                                                         "the instance: record its boot first")
    g = asub.add_parser("release", help="drop a reservation whose power-on did not happen")
    g.set_defaults(func=cmd_auth_release)
    g.add_argument("--instance", required=True)
    g.add_argument("--req", required=True)
    g = asub.add_parser("charges", help="import charge rows from the console's billing detail (a JSON list; [] when it "
                                             "shows none for this period)")
    g.set_defaults(func=cmd_auth_charges)
    g.add_argument("--instance", required=True)
    src = g.add_mutually_exclusive_group(required=True)
    src.add_argument("--json", help='[{"serial": ..., "instance": ..., "time": ..., "amount": ...}, ...]')
    src.add_argument("--file")

    add("now", cmd_now, "print the current unix time (for T0)", alias=False)
    add("version", cmd_version, "print the version of this helper", alias=False)
    s = add("doctor", cmd_doctor, "check python, bash, ssh, paths, the local record and the launcher", alias=False)
    s.add_argument("alias", nargs="?", help="also check that this alias answers over SSH")
    return p


def main(argv=None) -> int:
    global BOUND
    a = build_parser().parse_args(argv)
    BOUND = None
    try:
        if getattr(a, "alias", None) is not None and a.func not in UNBOUND:
            BOUND = bound_instance(a)
        return a.func(a)
    except Unverified as e:
        out({"alias": a.alias, "instance": None, "error": str(e)})
        return EXIT_MISMATCH
    except WrongHost as e:
        out({"alias": e.alias, "instance": BOUND, "instance_match": False, "hostname": e.host or None,
             "error": f"{e.alias} leads to {e.host or 'a host whose name could not be read'}, not to the instance "
                      f"{BOUND}: the command did nothing there. Stop and tell the user; once the alias is right, verify it "
                      f"with ctl check {e.alias} --instance {BOUND}"})
        return EXIT_MISMATCH
    except StoreError as e:   # a command that needs the local record and did not say more itself
        print(f"error: the local record cannot be used ({e.kind}): {e}", file=sys.stderr)
        return EXIT_STORE
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERR
    except subprocess.TimeoutExpired as e:
        print(f"error: ssh timed out: {e}", file=sys.stderr)
        return EXIT_UNREACHABLE
    except OSError as e:  # ssh binary missing, unreadable --cmd-file, ...
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERR
    finally:
        BOUND = None   # a binding lasts for one command


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
