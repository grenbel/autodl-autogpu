"""Shared by every pytest file here: no test touches the real local record, and ctl's clock can be set."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


@pytest.fixture(autouse=True)
def _private_gpu_home(tmp_path, monkeypatch):
    """The local record (~/.autodl-autogpu) of every test is a fresh directory of its own; subprocesses inherit it."""
    monkeypatch.setenv("AUTODL_AUTOGPU_HOME", str(tmp_path / "gpu-home"))


class Clock:
    """Stands in for ctl.now_s(): whole unix seconds that only the test moves on."""

    def __init__(self, t: int = 1790000000):
        self.t = t

    def set(self, t: int) -> None:
        self.t = t

    def advance(self, s: int) -> None:
        self.t += s

    def __call__(self) -> int:
        return self.t


@pytest.fixture
def clock(monkeypatch):
    import autodl_ctl as ctl
    c = Clock()
    monkeypatch.setattr(ctl, "now_s", c)
    return c


def pytest_terminal_summary(terminalreporter):
    """These files test the local helper only. Every run ends by saying so, and by naming the two suites that are run
    apart: pytest passing must not read as "everything is tested"."""
    terminalreporter.write_sep("-", "two more test suites, not run by pytest")
    terminalreporter.write_line("the guard's tests: bash tests/test_guard.sh (Linux; on Windows: wsl.exe -e bash "
                                "tests/test_guard.sh; never two runs at once)")
    terminalreporter.write_line("the page script's offline tests: python tests/console/run_headless.py (it needs Edge or "
                                "Chrome); or open tests/console/run.html in a browser (how: the top of that file)")
