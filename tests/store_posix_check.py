"""POSIX checks of the local record, for tests/test_store.py: run in WSL on Windows (the record lives in WSL's own
/tmp, since modes on /mnt/c are simulated), or imported directly on a POSIX machine. As a script it prints one JSON
object, scenario -> [ok, detail]. Every scenario makes and removes its own directory."""
import json
import multiprocessing as mp
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import time
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import autodl_ctl as ctl  # noqa: E402

ID = "abcd123456-1234abcd"
CTX = mp.get_context("spawn")


def _fresh() -> tuple:
    base = tempfile.mkdtemp(prefix="autodl-store-check-")
    home = pathlib.Path(base) / "gpu-home"
    os.environ["AUTODL_GPU_HOME"] = str(home)
    return base, home


def _made(home: pathlib.Path) -> None:
    os.environ["AUTODL_GPU_HOME"] = str(home)
    with ctl.Store() as st:
        st.save()


def _refused(kind: str, needle: str = "") -> tuple:
    try:
        with ctl.Store():
            pass
    except ctl.StoreError as e:
        if e.kind != kind or needle not in str(e):
            return False, f"expected {kind} with {needle!r}, got {e.kind}: {e}"
        return True, str(e)
    return False, "opened without an error"


def new_store_private(base, home):
    _made(home)
    for p, want in ((home, 0o700), (home / "store.json", 0o600), (home / "store.lock", 0o600)):
        st = os.lstat(p)
        if stat.S_IMODE(st.st_mode) != want or st.st_uid != os.getuid():
            return False, f"{p.name}: mode {oct(stat.S_IMODE(st.st_mode))}, uid {st.st_uid}"
    return True, "700 and 600, owned by the user"


def _loose(name, mode):
    def scenario(base, home):
        _made(home)
        os.chmod(home if name == "home" else home / name, mode)
        return _refused("unsafe", "chmod")
    return scenario


def hand_made_dir(base, home):
    """A directory made by hand (755, no files): told it can be removed, before any chmod advice."""
    os.mkdir(home)
    os.chmod(home, 0o755)
    ok, detail = _refused("unsafe", "not made by ctl")
    return ok and "chmod" not in detail, detail


def _linked(name):
    def scenario(base, home):
        if name == "home":
            real = pathlib.Path(base) / "real-home"
            _made(real)
            home.symlink_to(real, target_is_directory=True)
            os.environ["AUTODL_GPU_HOME"] = str(home)
        else:
            _made(home)
            moved = pathlib.Path(base) / f"moved-{name}"
            os.replace(home / name, moved)
            (home / name).symlink_to(moved)
        return _refused("unsafe", "link")   # and it says so
    return scenario


def unreadable_parent(base, home):
    """Whether a .git is above cannot be told when a directory on the way cannot be searched: refused."""
    locked = pathlib.Path(base) / "locked"
    locked.mkdir()
    os.environ["AUTODL_GPU_HOME"] = str(locked / "gpu-home")
    os.chmod(locked, 0)
    try:
        if os.access(locked, os.X_OK):   # root can search it anyway: nothing to check here
            return True, "skipped: this user can search a directory with mode 000"
        return _refused("repo", "AUTODL_GPU_HOME")
    finally:
        os.chmod(locked, 0o700)


def _child_add(home, prefix, n):
    os.environ["AUTODL_GPU_HOME"] = home
    for i in range(n):
        with ctl.Store() as st:
            st.data["aliases"][f"{prefix}{i}"] = {"instance": ID, "at": 1}
            st.save()


def _child_hold(home, reached):
    os.environ["AUTODL_GPU_HOME"] = home
    with ctl.Store():
        reached.set()
        time.sleep(600)


def no_lost_writes(base, home):
    _made(home)
    procs = [CTX.Process(target=_child_add, args=(str(home), p, 30), daemon=True) for p in ("x-", "y-")]
    for p in procs:
        p.start()
    for p in procs:
        p.join(180)
        if p.exitcode != 0:
            return False, f"a writer ended with {p.exitcode}"
    with ctl.Store() as st:
        n = sum(k.startswith(("x-", "y-")) for k in st.data["aliases"])
    return n == 60, f"{n} of 60 entries"


def _holding(home):
    reached = CTX.Event()
    p = CTX.Process(target=_child_hold, args=(str(home), reached), daemon=True)
    p.start()
    if not reached.wait(60):
        p.kill()
        raise RuntimeError("the holder never got the lock")
    return p


def held_lock_times_out(base, home):
    _made(home)
    p = _holding(home)
    try:
        try:
            with ctl.Store(wait=0.5):
                pass
        except ctl.StoreError as e:
            return e.kind == "locked", f"{e.kind}: {e}"
        return False, "opened while another process held the lock"
    finally:
        p.kill()
        p.join(30)


def killed_holder_releases(base, home):
    _made(home)
    p = _holding(home)
    p.kill()
    p.join(30)
    with ctl.Store(wait=10) as st:
        return st.data["schema"] == 1, "opened after the holder was killed"


SCENARIOS = {
    "new-store-private": new_store_private,
    "loose-dir-mode": _loose("home", 0o755),
    "loose-json-mode": _loose("store.json", 0o644),
    "loose-lock-mode": _loose("store.lock", 0o644),
    "hand-made-dir": hand_made_dir,
    "linked-dir": _linked("home"),
    "linked-json": _linked("store.json"),
    "linked-lock": _linked("store.lock"),
    "unreadable-parent": unreadable_parent,
    "no-lost-writes": no_lost_writes,
    "held-lock-times-out": held_lock_times_out,
    "killed-holder-releases": killed_holder_releases,
}


def run_all() -> dict:
    results = {}
    saved = os.environ.get("AUTODL_GPU_HOME")
    for name, scenario in SCENARIOS.items():
        base, home = _fresh()
        try:
            ok, detail = scenario(base, home)
            results[name] = [bool(ok), detail]
        except Exception:
            results[name] = [False, traceback.format_exc()[-1500:]]
        finally:
            shutil.rmtree(base, ignore_errors=True)
    if saved is None:
        os.environ.pop("AUTODL_GPU_HOME", None)
    else:
        os.environ["AUTODL_GPU_HOME"] = saved
    return results


if __name__ == "__main__":
    print(json.dumps(run_all()))
