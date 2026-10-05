"""What cloning adds to ctl on the instance's side (plan task 11.7): the data disk's manifest, the spec, a job's request
ID, and the whole sequence of clone commands in the documented order. The remote side runs for real in a bash (WSL on
Windows) inside a temporary directory, as in tests/test_ticket.py, whose box and fixtures are used here."""
import gzip
import hashlib
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import autodl_ctl as ctl  # noqa: E402
from test_clone import ID, MID, NEW, record, stored, update  # noqa: E402
from test_store import rc_json  # noqa: E402
from test_ticket import ARMED, HOSTS, _have, _sh, box, cl  # noqa: E402,F401  (box and cl are fixtures)

needs_bash = pytest.mark.skipif(not _have(), reason="needs bash with flock, setsid and sha256sum (WSL on Windows)")
GUARD_STUB = """#!/bin/bash
# stands in for the guard: status prints what the test wrote; run notes its arguments and registers the job as the
# guard does (its request, the boot, that it runs)
B="$(dirname "$0")"
case "$1" in
  status) cat "$B/guard_status" ;;
  run)
    name="$2"; shift 2; req=
    while [ $# -gt 0 ]; do case "$1" in --req) req="$2"; shift ;; esac; shift; done
    echo "$name $req" >> "$B/runs"
    mkdir -p "$B/data/.autodl-guard/jobs/$name"
    printf '%s' "$req" > "$B/data/.autodl-guard/jobs/$name/req"
    : > "$B/data/.autodl-guard/jobs/$name/running"
    cat > /dev/null
    echo "started job $name log=$B/data/.autodl-guard/jobs/$name/log" ;;
  *) exit 1 ;;
esac
"""


@pytest.fixture
def inst(box, cl, monkeypatch):
    """The box as an instance with a data disk: ctl's paths lead into it, and the guard is the stub above."""
    box.put("guard.sh", GUARD_STUB, "755")
    box.put("data/keep", "")
    monkeypatch.setattr(ctl, "DATA_DIR", f"{box.d}/data")
    monkeypatch.setattr(ctl, "GUARD_HOME", f"{box.d}/data/.autodl-guard")
    monkeypatch.setattr(ctl, "CGROUP_DIR", f"{box.d}/cg")
    return box


def run(capsys, *args):
    return rc_json(capsys, *args)


# ---- the manifest: its arithmetic ----
def ent(path, size=0, kind="f", mtime=100, target=None, sha=None):
    return [kind, size, mtime, path, target, sha]


def test_the_manifest_digest_ignores_times_and_order():
    a = [ent("a.bin", 10), ent("d", 4096, "d"), ent("d/b.txt", 3), ent("l", 5, "l", target="a.bin")]
    b = [ent("l", 9, "l", 777, target="a.bin"), ent("d/b.txt", 3, mtime=999), ent("d", 64, "d", 5), ent("a.bin", 10, mtime=1)]
    sa, sb = ctl.manifest_summary(a), ctl.manifest_summary(b)
    assert sa == sb and sa["files"] == 2 and sa["dirs"] == 1 and sa["links"] == 1 and sa["bytes"] == 13
    assert ctl.manifest_summary(a[:-1] + [ent("l", 5, "l", target="d/b.txt")])["sha256"] != sa["sha256"]   # another target
    assert ctl.manifest_summary([ent("a.bin", 11)] + a[1:])["sha256"] != sa["sha256"]                    # another size
    assert ctl.manifest_summary(a + [ent("e", 0, "d")])["sha256"] != sa["sha256"]                        # one more entry
    with_hash = [ent("a.bin", 10, sha="0" * 64)] + a[1:]
    assert ctl.manifest_summary(with_hash)["sha256"] != sa["sha256"]


def test_the_manifest_gives_the_ticket_s_duration():
    s = ctl.manifest_summary([ent("big", 1_000_000_001)])
    assert s["copy_estimate_s"] == 21 and s["ticket_deadline"] == "1842s"   # 30 minutes and twice the estimate
    assert ctl.manifest_summary([])["ticket_deadline"] == "1800s"


def man(entries, content=False):
    return {"v": 1, "content": content, "entries": entries, **ctl.manifest_summary(entries)}


def test_compare_lists_what_differs():
    old = [ent("same", 1), ent("gone", 2), ent("size", 3), ent("kind", 4), ent("l", 0, "l", target="x"), ent("d", 0, "d")]
    new = [ent("same", 1, mtime=5), ent("extra", 2), ent("size", 33), ent("kind", 0, "d"), ent("l", 0, "l", target="y"),
           ent("d", 9, "d", mtime=7)]
    got = ctl.manifest_compare(man(old), man(new), None)
    assert got["same"] is False and got["different"] == 5
    assert {(d["path"], d["what"].split(":")[0]) for d in got["differences"]} == {
        ("gone", "missing here"), ("extra", "only here"), ("size", "another size"), ("kind", "another type"),
        ("l", "another link target")}
    assert ctl.manifest_compare(man(old), man(list(reversed(old))), None) == {
        "same": True, "different": 0, "differences": [], "not_compared": 0, "changed_since": 0}
    many = [ent(f"f{i:03d}", 1) for i in range(60)]
    got = ctl.manifest_compare(man(many), man([ent(f"f{i:03d}", 2) for i in range(60)]), None)
    assert got["different"] == 60 and len(got["differences"]) == 20 and got["differences"][0]["path"] == "f000"
    a, b = [ent("x", 1, sha="a" * 64)], [ent("x", 1, sha="b" * 64)]
    assert ctl.manifest_compare(man(a, True), man(b, True), None)["differences"][0]["what"].startswith("another content")
    with pytest.raises(ValueError):
        ctl.manifest_compare(man(a, True), man([ent("x", 1)], False), None)   # one with contents, one without


def test_changed_after_sets_later_files_apart():
    old = [ent("data", 5, mtime=10), ent("log", 100, mtime=10), ent("d", 0, "d")]
    new = [ent("data", 5, mtime=10), ent("log", 250, mtime=2000), ent("new.ckpt", 9, mtime=2100), ent("d", 0, "d", 2100)]
    assert ctl.manifest_compare(man(old), man(new), None)["different"] == 2
    got = ctl.manifest_compare(man(old), man(new), 1500)   # the job began at 1500: what it wrote since is not compared
    assert got["same"] is True and got["not_compared"] == 2 and got["not_compared_first"] == ["log", "new.ckpt"]
    # of the two, log is one the old side has too: its copy can no longer be checked, and that is told apart
    assert got["changed_since"] == 1 and got["changed_since_first"] == ["log"]


def test_changed_after_sets_apart_what_is_only_here_and_what_changed_of_any_type():
    old = [ent("data", 5, mtime=10), ent("d", 0, "d", 10), ent("was_file", 1, mtime=10), ent("l", 0, "l", 10, target="a")]
    new = [ent("data", 5, mtime=10), ent("d", 0, "d", 2100),                 # a directory of both: only its time moved
           ent("jobs", 0, "d", 2000), ent("jobs/run1", 0, "d", 2100), ent("jobs/run1/log", 7, mtime=2100),
           ent("latest", 0, "l", 2100, target="jobs/run1"),                 # a link the job made
           ent("l", 0, "l", 2100, target="b"),                              # a link the job made anew
           ent("was_file", 0, "d", 2100),                                   # a file the job replaced by a directory
           ent("jobs/run0.prev", 0, "d", 50), ent("jobs/run0.prev/log", 3, mtime=50)]   # moved aside: old times, new names
    assert ctl.manifest_compare(man(old), man(new), None)["different"] == 8
    got = ctl.manifest_compare(man(old), man(new), 1500)
    assert got["same"] is True and got["not_compared"] == 8
    assert got["not_compared_first"] == ["jobs", "jobs/run0.prev", "jobs/run0.prev/log", "jobs/run1", "jobs/run1/log",
                                         "l", "latest", "was_file"]
    assert got["changed_since"] == 2 and got["changed_since_first"] == ["l", "was_file"]   # the two the old side has


def test_changed_after_keeps_what_is_missing_or_was_copied_wrong_a_difference():
    old = [ent("d", 0, "d", 10), ent("d/gone", 2, mtime=10), ent("stale", 3, mtime=10), ent("kind", 4, mtime=10),
           ent("l", 0, "l", 10, target="x")]
    new = [ent("d", 0, "d", 2100),                                          # the job wrote into d, or took gone away: unknown
           ent("stale", 33, mtime=10), ent("kind", 0, "d", 10), ent("l", 0, "l", 10, target="y")]   # none touched since
    got = ctl.manifest_compare(man(old), man(new), 1500)
    assert got["same"] is False and got["not_compared"] == 0 and "not_compared_first" not in got
    assert got["changed_since"] == 0 and "changed_since_first" not in got
    assert [(d["path"], d["what"].split(":")[0]) for d in got["differences"]] == [
        ("d/gone", "missing here"), ("kind", "another type"), ("l", "another link target"), ("stale", "another size")]


# ---- the manifest of a real tree ----
TREE = """set -e
cd "$B/data"
mkdir -p .autodl .autodl-guard/state2 .autodl-guard/state .autodl-guard/jobs/train proj/sub proj/empty
printf 'x' > .autodl/autopanel.monitor.db
printf 'x' > .autodl-guard/state2/mode
printf 'x' > .autodl-guard/guard.log
printf 'x' > .autodl-guard/autodl_guard.sh
printf 'x' > .autodl-guard/.deploy-abc123
printf 'x' > .autodl-guard/.cmd.xyz
printf 'old' > .autodl-guard/state/mode
printf 'hello\\n' > .autodl-guard/jobs/train/log
head -c 1000 /dev/zero > proj/a.bin
printf 'bee' > proj/sub/b.txt
ln -s proj/a.bin link
"""
WANT = {".autodl-guard": "d", ".autodl-guard/jobs": "d", ".autodl-guard/jobs/train": "d", ".autodl-guard/jobs/train/log": "f",
        ".autodl-guard/state": "d", ".autodl-guard/state/mode": "f", "keep": "f", "link": "l", "proj": "d", "proj/a.bin": "f",
        "proj/empty": "d", "proj/sub": "d", "proj/sub/b.txt": "f"}


def load(path) -> dict:
    return json.loads(gzip.decompress(pathlib.Path(path).read_bytes()).decode("utf-8"))


@needs_bash
def test_manifest_of_a_real_tree(inst, capsys, tmp_path):
    assert inst.drive(TREE)["_rc"] == 0
    rc, res = run(capsys, "manifest", "demo", "--project", tmp_path)
    assert rc == 0 and res["files"] == 5 and res["dirs"] == 7 and res["links"] == 1, res
    assert res["bytes"] == 0 + 3 + 6 + 1000 + 3 and res["data_bytes"] > 0 and res["system_bytes"] > 0, res
    m = load(res["manifest"])
    assert pathlib.Path(res["manifest"]).parent == tmp_path / ".autodl" and m["instance"] == ID and m["content"] is False
    assert {e[3]: e[0] for e in m["entries"]} == WANT       # nothing of the guard's state, nor the platform's folder
    assert [e[4] for e in m["entries"] if e[0] == "l"] == ["proj/a.bin"]
    assert m["sha256"] == res["sha256"] == ctl.manifest_summary(m["entries"])["sha256"]
    rc, again = run(capsys, "manifest", "demo", "--project", tmp_path, "--compare", res["manifest"])
    assert rc == 0 and again["same"] is True and again["sha256"] == res["sha256"], again
    rc, full = run(capsys, "manifest", "demo", "--project", tmp_path, "--content", "--out", tmp_path / "full.json.gz")
    assert rc == 0 and pathlib.Path(full["manifest"]) == tmp_path / "full.json.gz", full
    by = {e[3]: e for e in load(full["manifest"])["entries"]}
    assert by["proj/a.bin"][5] == hashlib.sha256(b"\0" * 1000).hexdigest() and by["proj/sub/b.txt"][5] == hashlib.sha256(b"bee").hexdigest()
    assert by["proj"][5] is None and by["link"][5] is None
    inst.drive('printf changed > "$B/data/proj/sub/b.txt"; rm "$B/data/keep"; : > "$B/data/.autodl-guard/state2/more"')
    rc, diff = run(capsys, "manifest", "demo", "--project", tmp_path, "--compare", res["manifest"])
    assert rc == ctl.EXIT_ERR and diff["same"] is False and diff["different"] == 2, diff
    assert {d["path"] for d in diff["differences"]} == {"proj/sub/b.txt", "keep"}


@needs_bash
def test_names_with_newlines_and_tabs_survive(inst, capsys, tmp_path):
    assert inst.drive("cd \"$B/data\" && printf 1 > $'tab\\there' && printf 22 > $'two\\nlines' && printf 333 > 'sp ace' "
                      "&& ln -s $'two\\nlines' $'a\\tlink'")["_rc"] == 0
    rc, res = run(capsys, "manifest", "demo", "--project", tmp_path, "--content")
    assert rc == 0 and res["files"] == 4 and res["links"] == 1 and res["bytes"] == 6, res
    by = {e[3]: e for e in load(res["manifest"])["entries"]}
    assert by["tab\there"][1] == 1 and by["two\nlines"][5] == hashlib.sha256(b"22").hexdigest() and by["sp ace"][1] == 3
    assert by["a\tlink"][4] == "two\nlines"


@needs_bash
def test_manifest_refuses_while_a_job_runs_or_the_guard_cannot_say(inst, capsys, tmp_path):
    inst.put("guard_status", ARMED + "job.train=running|1|2|/x/log\njob.old=done:0|1|2|/x/log\n")
    rc, res = run(capsys, "manifest", "demo", "--project", tmp_path)
    assert rc == ctl.EXIT_REFUSED and "job.train" in res["error"], res
    inst.put("guard.sh", "#!/bin/bash\nexit 1\n", "755")
    rc, res = run(capsys, "manifest", "demo", "--project", tmp_path)
    assert rc == ctl.EXIT_REFUSED and "status" in res["error"], res
    assert not (tmp_path / ".autodl").exists() or not list((tmp_path / ".autodl").glob("manifest-*"))


def test_digests_that_break_off_are_told_from_a_record_out_of_shape():
    listing = b"f\t1\t1.0\ta\0LISTED\0"
    digest = hashlib.sha256(b"1").hexdigest().encode() + b"  ./a\0"
    assert ctl.manifest_entries(listing + digest + b"HASHED\0", True)[0][5] == hashlib.sha256(b"1").hexdigest()
    with pytest.raises(ValueError, match="broke off.*try again"):
        ctl.manifest_entries(listing + digest, True)                 # sha256sum stopped: no closing mark
    with pytest.raises(ValueError, match="broke off.*try again"):
        ctl.manifest_entries(b"f\t1\t1.0\ta\0", False)                # find stopped: no closing mark
    with pytest.raises(ValueError, match="out of shape"):
        ctl.manifest_entries(b"f\t1\ta\0LISTED\0", False)             # a record that is not a record
    with pytest.raises(ValueError, match="out of shape"):
        ctl.manifest_entries(listing + b"nonsense\0HASHED\0", True)


@needs_bash
def test_a_manifest_that_is_cut_short_leaves_no_file(inst, capsys, tmp_path, cl):
    rem, _ = cl
    rem.stub_path = True
    inst.put("bin/find", "#!/bin/bash\n# a listing that breaks off\nprintf 'f\\t1\\t1.0\\thalf\\0'; exit 1\n", "755")
    rc, res = run(capsys, "manifest", "demo", "--project", tmp_path)
    assert rc == ctl.EXIT_ERR and "incomplete" in res["error"], res
    # it says what happened and what to do: find stops like this when files come and go under it, as while a copy runs
    assert "broke off" in res["error"] and "try again" in res["error"], res
    inst.put("bin/find", "#!/bin/bash\n# a listing that takes too long\nsleep 8\n", "755")
    rc, res = run(capsys, "manifest", "demo", "--project", tmp_path, "--timeout", "2s")
    assert rc == ctl.EXIT_ERR and "time" in res["error"], res
    assert not list((tmp_path / ".autodl").glob("manifest-*"))


# ---- the spec ----
NVSMI = """#!/bin/bash
# stands in for nvidia-smi: the query ctl spec makes, answered for two cards
case "$*" in *--query-gpu=name,driver_version*) printf 'NVIDIA GeForce RTX 0000 Ti, 580.105.08\\nNVIDIA GeForce RTX 0000 Ti, 580.105.08\\n' ;; *) exit 1 ;; esac
"""


@needs_bash
def test_spec_reads_and_compares(inst, capsys, cl):
    rem, _ = cl
    rem.stub_path = True
    inst.put("bin/nvidia-smi", NVSMI, "755")
    inst.put("cg/cpu.max", "2000000 100000\n")
    inst.put("cg/memory.max", f"{90 * 2 ** 30}\n")
    rc, res = run(capsys, "spec", "demo")
    assert rc == 0 and res["gpus"] == 2 and res["gpu_model"] == "NVIDIA GeForce RTX 0000 Ti" and res["driver"] == "580.105.08", res
    assert res["cpu_cores"] == 20 and res["memory_bytes"] == 90 * 2 ** 30 and res["system_bytes"] > 0 and res["data_bytes"] > 0
    good = ["--gpu-model", "RTX 0000 Ti", "--gpus", "2", "--driver", "580.105.08", "--cpu-per-gpu", "10", "--mem-per-gpu-gb", "45",
            "--min-system-bytes", "1", "--min-data-bytes", "1"]
    rc, res = run(capsys, "spec", "demo", *good)
    assert rc == 0 and res["match"] is True and res["diff"] == [], res
    for change, word in ((["--gpu-model", "RTX 0000"], None), (["--gpu-model", "RTX 1111"], "gpu-model"), (["--gpus", "1"], "gpus"),
                         (["--driver", "595.71.05"], "driver"), (["--cpu-per-gpu", "12"], "cpu"),
                         (["--mem-per-gpu-gb", "62"], "mem"), (["--min-data-bytes", str(10 ** 18)], "data")):
        rc, res = run(capsys, "spec", "demo", *good, *change)   # the later option wins
        if word is None:
            assert rc == 0 and res["match"] is True, res         # the page's name is part of the card's
        else:
            assert rc == ctl.EXIT_ERR and res["match"] is False and len(res["diff"]) == 1 and word in res["diff"][0], res
    inst.put("bin/nvidia-smi", "#!/bin/bash\nexit 9\n", "755")   # without GPUs (non-GPU mode): nothing to compare with
    rc, res = run(capsys, "spec", "demo", "--gpus", "2")
    assert rc == ctl.EXIT_ERR and res["gpus"] == 0 and res["gpu_model"] is None, res


# ---- a job's request ID ----
def job(capsys, name="train", req="1111222233334444", alias="demo"):
    return run(capsys, "job", alias, name, "--req", req)


def make_job(inst, rel, files: dict):
    for f, text in files.items():
        inst.put(f"data/.autodl-guard/jobs/{rel}/{f}", text)


@needs_bash
def test_job_tells_started_from_never_started(inst, capsys):
    q = "1111222233334444"
    rc, res = job(capsys)
    assert rc == 0 and res["started"] is False and res["boot"].isdigit(), res   # no record at all
    boot = res["boot"]
    make_job(inst, "train", {"req": "9999888877776666", "boot": "12345", "end": "1", "rc": "0", "spawning": ""})
    assert job(capsys)[1]["started"] is False                                   # a record of another request (copied)
    make_job(inst, "train.prev-1-2", {"req": q, "boot": "777", "end": "5", "rc": "0", "spawning": "", "start": "1"})
    rc, res = job(capsys)                                                       # it ran in an earlier boot, and ended
    assert rc == 0 and res["started"] is True and res["state"] == "ended" and res["rc"] == "0" and res["this_boot"] is False
    assert res["where"] == "train.prev-1-2", res
    make_job(inst, "j2", {"req.pending": q, "boot": "777"})
    assert job(capsys, "j2")[1]["started"] is False                             # registered in an earlier boot, never begun
    make_job(inst, "j3", {"req.pending": q, "boot": "777", "spawning": ""})
    rc, res = job(capsys, "j3")                                                 # it was being started when that boot ended
    assert rc == ctl.EXIT_UNCERTAIN and res["started"] is None, res
    make_job(inst, "j4", {"req.pending": q, "boot": boot})
    rc, res = job(capsys, "j4")                                                 # this boot: the guard itself goes on with it
    assert rc == 0 and res["started"] is False and res["starting"] is True, res
    make_job(inst, "j5", {"req": q, "boot": boot, "running": ""})
    inst.put("guard_status", ARMED + f"job.j5=running|1|0|{inst.d}/log\n")
    rc, res = job(capsys, "j5")
    assert rc == 0 and res["started"] is True and res["state"] == "running" and res["this_boot"] is True, res
    make_job(inst, "j6", {"req": q, "boot": "777", "running": ""})
    assert job(capsys, "j6")[1]["state"] == "lost"                              # it was running when an earlier boot ended
    assert run(capsys, "job", "demo", "train", "--req", "nope")[0] == ctl.EXIT_ERR


def runs(inst) -> list:
    return inst.cat("runs").split("\n")[:-1]


@needs_bash
def test_run_with_a_request_starts_once_across_boots(inst, capsys):
    q = "1111222233334444"
    rc, _ = run(capsys, "run", "demo", "train", "--req", q, "--cmd", "echo hi")
    assert rc == 0 and runs(inst) == [f"train {q}"]                # nothing knew the request: it was started
    rc, res = run(capsys, "run", "demo", "train", "--req", q, "--cmd", "echo hi")
    assert rc == 0 and res["already"] is True and runs(inst) == [f"train {q}"], res   # again: not a second start
    inst.put("data/.autodl-guard/jobs/train/boot", "777")         # the instance was started again since
    inst.put("data/.autodl-guard/jobs/train/end", "5")
    inst.put("data/.autodl-guard/jobs/train/spawning", "")
    rc, res = run(capsys, "run", "demo", "train", "--req", q, "--cmd", "echo hi")
    assert rc == 0 and res["already"] is True and res["state"] == "ended" and runs(inst) == [f"train {q}"], res


@needs_bash
def test_run_with_a_new_request_starts_next_to_a_copied_record(inst, capsys):
    make_job(inst, "train", {"req": "9999888877776666", "boot": "12345", "end": "1", "rc": "0", "spawning": ""})
    q = "1111222233334444"
    rc, _ = run(capsys, "run", "demo", "train", "--req", q, "--cmd", "echo hi")
    assert rc == 0 and runs(inst) == [f"train {q}"]   # the source's record of the same name is another request's


@needs_bash
def test_run_cannot_tell_then_sends_nothing(inst, capsys):
    q = "1111222233334444"
    make_job(inst, "train", {"req.pending": q, "boot": "777", "spawning": ""})
    rc, res = run(capsys, "run", "demo", "train", "--req", q, "--cmd", "echo hi")
    assert rc == ctl.EXIT_UNCERTAIN and res["started"] is None and inst.cat("runs") == "", res
    assert run(capsys, "run", "demo", "train", "--req", "short", "--cmd", "echo hi")[0] == ctl.EXIT_ERR


@needs_bash
def test_run_without_req_is_as_before(inst, capsys):
    rc, _ = run(capsys, "run", "demo", "train", "--cmd", "echo hi")
    name, req = runs(inst)[0].split()
    assert rc == 0 and name == "train" and ctl.REQ_RE.match(req)
    rc, _ = run(capsys, "run", "demo", "train", "--cmd", "echo hi")
    assert len(runs(inst)) == 2 and runs(inst)[1].split()[1] != req   # a request of its own every time, and no query


# ---- the whole sequence, in the order reference/clone.md gives it ----
JREQ = "1111222233334444"
DIG = "19:0a1b2c3d,19:4e5f6a7b"


class Flow:
    """The commands of one clone, stage by stage. mark(stage) writes the stage into the record (which comes before the
    stage's action); act(stage) is what the stage names. The page's part (the dialog, the click, the new row) is not
    here: its results are given as values."""

    def __init__(self, capsys, inst, txn, project):
        self.capsys, self.inst, self.txn, self.project, self.req = capsys, inst, txn, project, None

    def ok(self, *args):
        rc, res = run(self.capsys, *args)
        assert rc == 0, (args, res)
        return res

    def up(self, *args):
        return self.ok("clone-record", "update", "--txn", self.txn, *args)

    def mark(self, stage):
        if stage == "click":
            self.up("--stage", "click", "--set", "host=ffff000000", "--set", "gpus=1", "--set", "price=0.98", "--set",
                    "expand-gb=5", "--set", "daily=0.03", "--set", f"req={self.req}", "--set", f"t0={MID + 30}", "--set",
                    f"before={DIG}")
        elif stage == "clicked":
            self.up("--stage", "clicked", "--set", "answer=创建成功", "--set", "created=yes", "--set", f"instance={NEW}")
        elif stage == "launching":
            self.up("--stage", "launching", "--set", "job=train", "--set", f"job-req={JREQ}")
        elif stage != "opened":
            self.up("--stage", stage)

    def act(self, stage):
        if stage == "ticket":
            self.ok("ticket", "write", "demo", "--txn", self.txn, "--hosts", HOSTS, "--deadline", "45m")
        elif stage == "reserve":
            self.ok("auth", "daily", "--instance", ID, "--fee", "0.03")
            self.req = self.ok("auth", "check", "--instance", ID, "--mode", "gpu", "--price", "0.98", "--gpus", "1",
                               "--hours", "1", "--clone-host", "ffff000000", "--daily", "0.03")["req"]
        elif stage == "clicked":
            assert self.ok("ticket", "read", "demo-c", "--txn", self.txn)["allowed"] is True
            self.ok("ticket", "extend", "demo-c", "--txn", self.txn, "--deadline", "30m")
            self.ok("auth", "inherit", "--from", ID, "--to", NEW, "--req", self.req, "--daily", "0.03")
            self.ok("log", "on", "--instance", NEW, "--project", self.project, "--req", self.req, "--at", MID + 30,
                    "--field", "mode=gpu", "--field", "price=0.98", "--field", "gpus=1")
        elif stage == "adopted":
            self.ok("ticket", "clear", "demo-c", "--txn", self.txn)
        elif stage == "launching":
            assert run(self.capsys, "run", "demo-c", "train", "--req", JREQ, "--cmd", "echo hi")[0] == 0
        elif stage == "switched":
            self.ok("ticket", "clear", "demo", "--txn", self.txn, "--source")
            self.ok("clone-record", "close", "--txn", self.txn)


STAGES = ["opened", "ticket", "reserve", "click", "clicked", "adopted", "taken-over", "launching", "launched", "switched"]


def settled(inst, capsys) -> None:
    """Nothing of a clone is left to clean up: no open record, no open reservation, no ticket."""
    rc, res = run(capsys, "clone-record", "show")
    assert rc == 0 and res["records"] == [], res
    d = stored()
    for iid in (ID, NEW):
        assert ctl.open_reservations(d["ledger"].get(iid, [])) == [], iid
    assert inst.cat("ticket.sh") == ""


@needs_bash
def test_the_whole_sequence_runs_in_the_documented_order(inst, capsys, cl, tmp_path):
    rem, txn = cl
    f = Flow(capsys, inst, txn, tmp_path)
    for stage in STAGES:
        f.mark(stage)
        f.act(stage)
    settled(inst, capsys)
    r = stored()["clones"]["records"][txn]
    assert r["outcome"] == "switched" and r["stage"] == "switched" and r["instance"] == NEW and r["created"] is True
    assert (r["source_ticket"], r["clone_ticket"]) == ("cleared", "cleared")
    assert runs(inst) == [f"train {JREQ}"] and stored()["ledger"][NEW][-1]["kind"] == "on"
    assert (tmp_path / ".autodl" / f"clone_{txn}.json").exists() and not (tmp_path / ".autodl" / "clone_pending.json").exists()


@needs_bash
@pytest.mark.parametrize("skipped", ["ticket", "reserve", "clicked", "adopted", "switched"])
def test_a_step_out_of_order_is_refused(inst, capsys, cl, tmp_path, skipped):
    """The action of one stage is left out: the next stage's mark, or its own closing, is then refused."""
    rem, txn = cl
    f = Flow(capsys, inst, txn, tmp_path)
    for stage in STAGES[:STAGES.index(skipped)]:
        f.mark(stage)
        f.act(stage)
    f.mark(skipped)
    if skipped == "ticket":      # no ticket was written
        assert update(capsys, txn, "--stage", "reserve")[0] == ctl.EXIT_ERR
    elif skipped == "reserve":   # no budget check was made
        assert update(capsys, txn, "--stage", "click", "--set", "host=ffff000000", "--set", "gpus=1", "--set", "price=0.98",
                      "--set", "expand-gb=0", "--set", "req=aaaabbbbccccdddd", "--set", f"t0={MID}",
                      "--set", f"before={DIG}")[0] == ctl.EXIT_ERR
    elif skipped == "clicked":   # no inherit
        assert update(capsys, txn, "--stage", "adopted")[0] == ctl.EXIT_ERR
    elif skipped == "adopted":   # the ticket on the new instance was not cleared
        assert update(capsys, txn, "--stage", "taken-over")[0] == ctl.EXIT_ERR
    else:                        # the source's ticket was not cleared
        assert run(capsys, "clone-record", "close", "--txn", txn)[0] == ctl.EXIT_ERR
    f.act(skipped)               # with the action done, the sequence goes on to its end
    for stage in STAGES[STAGES.index(skipped) + 1:]:
        f.mark(stage)
        f.act(stage)
    settled(inst, capsys)


@needs_bash
@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("acted", [False, True])
def test_stopping_after_any_stage_leaves_something_the_next_run_can_finish(inst, capsys, cl, tmp_path, stage, acted):
    """The conversation ends right after a stage was written (acted False), or after that stage's action as well
    (acted True). A new conversation reads the stage and does what the end of design 5.6 says for it."""
    rem, txn = cl
    f = Flow(capsys, inst, txn, tmp_path)
    for s in STAGES[:STAGES.index(stage)]:
        f.mark(s)
        f.act(s)
    f.mark(stage)
    if acted:
        f.act(stage)
    if acted and stage == "switched":
        return settled(inst, capsys)
    # ---- the new conversation: all it has is the record ----
    g = Flow(capsys, inst, txn, tmp_path)
    rec = record(txn)
    g.req = rec.get("req")
    at = STAGES.index(rec["stage"])
    if at <= STAGES.index("click"):
        # nothing was created, or (at click) nobody knows: here the look at the console finds no new instance
        held = [r["req"] for r in ctl.open_reservations(stored()["ledger"].get(ID, [])) if "clone_host" in r]
        for req in held:
            g.ok("auth", "release", "--instance", ID, "--req", req)
        if rec["stage"] == "click":
            g.up("--stage", "clicked", "--set", "answer=", "--set", "created=no", "--set", "note=no new row, no charge since T0")
        if at >= STAGES.index("ticket"):
            g.ok("ticket", "clear", "demo", "--txn", txn, "--source")
        res = g.ok("clone-record", "close", "--txn", txn)
        assert res["outcome"] == ("not-created" if rec["stage"] == "click" else "unused")
        return settled(inst, capsys)
    # the clone exists: go on from the stage's own action; what was done already is done again without harm
    if rec["stage"] == "launching":
        rc, seen = run(capsys, "job", "demo-c", "train", "--req", JREQ)
        assert rc == 0 and seen["started"] is acted, seen   # it asks first whether this request was started
    g.act(rec["stage"])
    for s in STAGES[at + 1:]:
        g.mark(s)
        g.act(s)
    settled(inst, capsys)
    assert runs(inst) == [f"train {JREQ}"]   # the job was started once, whenever the first conversation ended
