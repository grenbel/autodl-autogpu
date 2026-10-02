"""Where the tests find the tools of the machine they run on. Nothing here names a particular machine.

WSL (Windows only) stands in for the instance in some tests: they use WSL's default distribution, or the one
named in the environment variable AUTODL_TEST_WSL_DISTRO. Git Bash (Windows only) is found the way ctl finds it:
next to git, then in the default install directory. A test whose tool is missing is skipped.
"""
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import autodl_ctl as ctl  # noqa: E402


def _distro() -> str:
    return os.environ.get("AUTODL_TEST_WSL_DISTRO", "").strip()


def wsl_command() -> list:
    """The argv prefix that runs a program in WSL."""
    distro = _distro()
    return ["wsl.exe", *(["-d", distro] if distro else []), "-e"]


def wsl_name() -> str:
    """For skip messages: which distribution the tests look for."""
    distro = _distro()
    return f"the WSL distribution {distro}" if distro else "WSL's default distribution"


def have_wsl(*programs: str) -> bool:
    """On Windows: WSL answers and has bash and every one of PROGRAMS."""
    if os.name != "nt" or not shutil.which("wsl.exe"):
        return False
    script = " && ".join(f"command -v {p} > /dev/null" for p in ("bash", *programs))
    try:
        return subprocess.run([*wsl_command(), "bash", "-c", script], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def git_bash():
    """<Git>/usr/bin/bash.exe of the Git for Windows that ctl would use, as a Path; None when there is none."""
    if os.name != "nt":
        return None
    found = ctl.find_bash()
    if not found:
        return None
    p = pathlib.Path(found)
    for cand in (p, p.parent.parent / "usr" / "bin" / "bash.exe"):
        if cand.parent.name == "bin" and cand.parent.parent.name == "usr" and cand.exists():
            return cand
    return None
