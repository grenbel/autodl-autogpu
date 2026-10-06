"""Tests for tests/local_tools.py, and a static check that no test file is written for one particular machine."""
import os
import pathlib
import re
import subprocess
import sys

import pytest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
import local_tools  # noqa: E402


def test_wsl_command_uses_the_default_distribution(monkeypatch):
    monkeypatch.delenv("AUTODL_TEST_WSL_DISTRO", raising=False)
    assert local_tools.wsl_command() == ["wsl.exe", "-e"]
    monkeypatch.setenv("AUTODL_TEST_WSL_DISTRO", "  ")
    assert local_tools.wsl_command() == ["wsl.exe", "-e"]
    assert "default" in local_tools.wsl_name()


def test_wsl_command_takes_the_named_distribution(monkeypatch):
    monkeypatch.setenv("AUTODL_TEST_WSL_DISTRO", "Debian")
    assert local_tools.wsl_command() == ["wsl.exe", "-d", "Debian", "-e"]
    assert "Debian" in local_tools.wsl_name()


def test_have_wsl_is_false_without_wsl(monkeypatch):
    monkeypatch.setattr(local_tools.shutil, "which", lambda name: None)
    assert local_tools.have_wsl() is False


@pytest.mark.skipif(os.name != "nt", reason="the layouts of Git for Windows")
@pytest.mark.parametrize("found", ["bin/bash.exe", "usr/bin/bash.exe"])
def test_git_bash_is_the_usr_bin_bash_next_to_what_ctl_finds(tmp_path, monkeypatch, found):
    root = tmp_path / "Git"   # not the default install directory: nothing here depends on where Git lives
    for rel in {found, "usr/bin/bash.exe"}:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"")
    monkeypatch.setattr(local_tools.ctl, "find_bash", lambda: str(root / found))
    assert local_tools.git_bash() == root / "usr" / "bin" / "bash.exe"


@pytest.mark.skipif(os.name != "nt", reason="the layouts of Git for Windows")
def test_git_bash_is_none_when_there_is_no_usr_bin_bash(tmp_path, monkeypatch):
    root = tmp_path / "Git"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "bash.exe").write_bytes(b"")
    monkeypatch.setattr(local_tools.ctl, "find_bash", lambda: str(root / "bin" / "bash.exe"))
    assert local_tools.git_bash() is None
    monkeypatch.setattr(local_tools.ctl, "find_bash", lambda: None)
    assert local_tools.git_bash() is None


# What ties a test file to one machine: a fixed WSL distribution, a fixed install directory, somebody's home.
# The made-up homes x and NAME (C:/Users/x, /c/Users/NAME) are the ones the tests use on purpose.
ONE_MACHINE = [
    ("a fixed WSL distribution", re.compile(r"""["']-d["'],\s*["'][A-Za-z]|wsl(?:\.exe)?\s+-d\s""")),
    ("a fixed Git install directory", re.compile(r"Program Files[\\/]+Git[\\/]+(?:usr|bin|cmd|mingw64)\b")),
    ("a home on Windows", re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!x\b|NAME\b)[A-Za-z0-9_.-]+"
                                     r"|/mnt/[a-z]/Users/|(?<![A-Za-z:])/[a-z]/Users/(?!x\b|NAME\b)")),
    ("a home on Linux or macOS", re.compile(r"/home/[A-Za-z0-9_.-]+|(?<![A-Za-z:/])/Users/(?!x\b|NAME\b)[A-Za-z0-9_.-]+")),
]


def _test_files():
    return sorted(p for p in TESTS.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.name != "test_local_tools.py")


def test_no_test_file_is_written_for_one_machine():
    found = []
    for p in _test_files():
        text = p.read_bytes().decode("utf-8")   # every file under tests/ is text
        for n, line in enumerate(text.split("\n"), 1):
            for what, rx in ONE_MACHINE:
                if rx.search(line):
                    found.append(f"{p.relative_to(TESTS).as_posix()}:{n}: {what}: {line.strip()[:120]}")
    assert found == []


def test_the_static_check_sees_each_kind_of_machine_detail():
    samples = {
        "a fixed WSL distribution": ['WSL = ["wsl.exe", "-d", "Ubuntu", "-e"]', "#   wsl.exe -d Ubuntu -- bash x.sh"],
        "a fixed Git install directory": [r'Path(r"C:\Program Files\Git\usr\bin\bash.exe")',
                                          "C:/Program Files/Git/bin/bash.exe"],
        "a home on Windows": [r"C:\Users\someone\x", "C:/Users/someone/x", "/mnt/c/Users/someone/x",
                              "bash /c/Users/someone/x"],
        "a home on Linux or macOS": ["/home/someone/x", "open /Users/someone/x"],
    }
    harmless = ['("/c/Users/x/data", "C:/Users/x/data")', "like C:/Users/NAME/.autodl-autogpu or /c/Users/NAME/.autodl-autogpu",
                '["pull", "autodl-test", "C:/Program Files/Git/root/autodl-tmp/out", "."]',
                "wsl.exe -e bash tests/test_guard.sh", '["wsl.exe", *(["-d", distro] if distro else []), "-e"]',
                '_wsl("mktemp", "-d", "/tmp/autodl-ctl-test.XXXXXX")']
    by_name = dict(ONE_MACHINE)
    assert sorted(by_name) == sorted(samples)
    for what, lines in samples.items():
        for line in lines:
            assert by_name[what].search(line), (what, line)
    for line in harmless:
        assert not any(rx.search(line) for _, rx in ONE_MACHINE), line


def test_a_pytest_run_ends_by_naming_the_two_suites_it_does_not_run():
    """pytest passing must not read as "everything is tested": the guard's tests and the page script's are run apart."""
    here = pathlib.Path(__file__).resolve()
    r = subprocess.run([sys.executable, "-m", "pytest", str(here), "-q", "-p", "no:cacheprovider", "-k",
                        "test_have_wsl_is_false_without_wsl"], capture_output=True, text=True, timeout=120,
                       cwd=str(here.parent.parent))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "bash tests/test_guard.sh" in r.stdout
    assert "python tests/console/run_headless.py" in r.stdout and "tests/console/run.html" in r.stdout
