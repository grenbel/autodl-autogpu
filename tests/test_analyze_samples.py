"""Tests for dev/analyze_samples.py (turns `autodl_guard.sh sample` output into rates)."""
import importlib.util
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("analyze_samples", HERE.parent / "dev" / "analyze_samples.py")
an = importlib.util.module_from_spec(spec)
spec.loader.exec_module(an)

RUN = "# autodl_guard 0.7.1 sample every=10 count=2 gpu_samples=3 clk_tck=100\n"
HEADER = "# epoch\tuptime_cs\tcpu_usec\tio_bytes\tnet_bytes\tgpu_max\tgpu_fail\tguard_ticks\tself_ticks\n"


def row(epoch, up, cpu, io, net, gpu="na", fail="na", ticks="", own=""):
    return "\t".join(str(x) for x in (epoch, up, cpu, io, net, gpu, fail, ticks, own)) + "\n"


def one_run(text):
    runs = an.parse_runs(text.splitlines(keepends=True))
    assert len(runs) == 1
    return runs[0]


def test_rates_over_ten_seconds():
    run = one_run(RUN + HEADER + row(100, 10000, 0, 0, 0, ticks=0, own=0)
                  + row(110, 11000, 1_000_000, 10_000, 20_000, 37, 0, 10, 5))
    rs = an.rates(run["rows"], run["clk_tck"])
    assert len(rs) == 1
    r = rs[0]
    assert r["cpu_pct"] == 10.0      # 1 s of CPU in 10 s
    assert r["io_Bps"] == 1000.0     # 10 000 bytes in 10 s
    assert r["net_Bps"] == 2000.0
    assert r["guard_pct"] == 1.0     # 10 ticks at 100 Hz = 0.1 s in 10 s
    assert r["self_pct"] == 0.5
    assert r["gpu"] == 37            # the reading of the interval that ends at the later row
    assert r["span_s"] == 10.0 and r["epoch"] == 110


def test_the_gpu_reading_belongs_to_the_interval_before_it():
    run = one_run(RUN + HEADER + row(100, 10000, 0, 0, 0) + row(110, 11000, 0, 0, 0, 90, 0)
                  + row(120, 12000, 0, 0, 0, 3, 0))
    assert [r["gpu"] for r in an.rates(run["rows"])] == [90, 3]


def test_gpu_is_unknown_if_any_sample_failed_and_na_if_not_applicable():
    run = one_run(RUN + HEADER + row(100, 10000, 0, 0, 0) + row(110, 11000, 0, 0, 0, 37, 1)
                  + row(120, 12000, 0, 0, 0, "", 3) + row(130, 13000, 0, 0, 0, "na", "na"))
    assert [r["gpu"] for r in an.rates(run["rows"])] == [None, None, "na"]


def test_runs_and_files_are_not_paired_across(tmp_path):
    text = ("=== job calib start\n" + RUN + HEADER + row(100, 10000, 0, 0, 0) + row(110, 11000, 100, 0, 0, 1, 0)
            + "junk\tline\n" + RUN + HEADER + row(500, 90000, 0, 0, 0) + row(510, 91000, 100, 0, 0, 1, 0))
    assert [len(r["rows"]) for r in an.parse_runs(text.splitlines(keepends=True))] == [2, 2]
    a = tmp_path / "a.tsv"
    b = tmp_path / "b.tsv"
    a.write_text(text, encoding="utf-8")
    b.write_text(RUN + HEADER + row(900, 50000, 0, 0, 0) + row(910, 51000, 0, 0, 0, 2, 0), encoding="utf-8")
    assert an.analyze([str(a), str(b)])["intervals"] == 3


def test_unreadable_and_reset_counters_give_none():
    run = one_run(RUN + HEADER + row(100, 10000, 900, "", 5_000) + row(110, 11000, 100, 10, 6_000, 1, 0))
    r = an.rates(run["rows"])[0]
    assert r["cpu_pct"] is None      # the counter went back
    assert r["io_Bps"] is None       # unreadable on one side
    assert r["net_Bps"] == 100.0
    assert r["guard_pct"] is None and r["self_pct"] is None


def test_a_non_increasing_uptime_is_skipped():
    run = one_run(RUN + HEADER + row(100, 11000, 0, 0, 0) + row(110, 11000, 5, 5, 5, 1, 0)
                  + row(120, 12000, 10, 10, 10, 1, 0))
    assert len(an.rates(run["rows"])) == 1


def test_clock_ticks_per_second_come_from_the_run_unless_given(tmp_path):
    f = tmp_path / "s.tsv"
    f.write_text(RUN.replace("clk_tck=100", "clk_tck=250") + HEADER + row(100, 10000, 0, 0, 0, ticks=0)
                 + row(110, 11000, 0, 0, 0, 1, 0, ticks=25), encoding="utf-8")
    assert an.analyze([str(f)])["rows"][0]["guard_pct"] == 1.0            # 25 ticks at 250 Hz = 0.1 s in 10 s
    assert an.analyze([str(f)], clk_tck=100)["rows"][0]["guard_pct"] == 2.5
    g = tmp_path / "old.tsv"   # no parameter line: 100 Hz
    g.write_text(HEADER + row(100, 10000, 0, 0, 0, ticks=0) + row(110, 11000, 0, 0, 0, 1, 0, ticks=25), encoding="utf-8")
    assert an.analyze([str(g)])["rows"][0]["guard_pct"] == 2.5
    h = tmp_path / "bom.tsv"   # saved by Windows PowerShell: a byte order mark before the parameter line
    h.write_text("﻿" + f.read_text(encoding="utf-8"), encoding="utf-8")
    assert an.analyze([str(h)])["rows"][0]["guard_pct"] == 1.0


def test_trim_and_window():
    rows = [row(100 + 10 * i, 10000 + 1000 * i, 1_000_000 * i, 0, 0, *(("na", "na") if i == 0 else (i, 0)))
            for i in range(7)]
    run = one_run(RUN + HEADER + "".join(rows))
    assert len(an.rates(run["rows"])) == 6
    assert [r["gpu"] for r in an.rates(run["rows"], trim_start=1, trim_end=1)] == [2, 3, 4, 5]
    ws = an.rates(run["rows"], trim_start=1, trim_end=1, window=2)
    assert [r["gpu"] for r in ws] == [3, 5]          # the highest reading in each window
    assert [r["span_s"] for r in ws] == [20.0, 20.0]
    assert ws[0]["cpu_pct"] == 10.0                  # 2 s of CPU in 20 s
    # a load's steady window by uptime: rows 2 to 5 are kept, the first of them is the baseline
    assert [r["gpu"] for r in an.rates(run["rows"], between=(12000, 15000))] == [3, 4, 5]
    # a load of 7 s (uptime 125.00 to 132.00 s) holds no whole interval, but two intervals overlap it
    assert an.rates(run["rows"], between=(12500, 13200)) == []
    assert [r["gpu"] for r in an.rates(run["rows"], cover=(12500, 13200))] == [3, 4]


def test_a_window_needs_every_counter_in_between():
    # cpu goes 0, 1000, 10 (a reset), 2000: the two ends alone would look like a valid increase
    run = one_run(RUN + HEADER + row(100, 10000, 0, 0, 0) + row(110, 11000, 1000, 0, 0, 1, 0)
                  + row(120, 12000, 10, 0, 0, 1, 0) + row(130, 13000, 2000, 0, 0, 1, 0))
    w = an.rates(run["rows"], window=3)[0]
    assert w["cpu_pct"] is None
    assert w["io_Bps"] == 0.0


def test_the_load_window_comes_from_the_markers_a_load_prints(tmp_path):
    log = tmp_path / "load.log"
    log.write_text("LOAD START 1790000000 12345.67\nwork\nLOAD END 1790000180 12525.70\n", encoding="utf-8")
    assert an.load_marks(str(log)) == (1234567, 1252570)
    assert an.load_window(str(log)) == (1235567, 1252570)       # from 10 s after the start to the end
    assert an.load_window(str(log), lead_s=0) == (1234567, 1252570)
    (tmp_path / "none.log").write_text("no markers\n", encoding="utf-8")
    with pytest.raises(ValueError):
        an.load_window(str(tmp_path / "none.log"))
    # a rerun appended to the same log: two loads in one file must not be read as one long load
    (tmp_path / "twice.log").write_text("LOAD START 1 10.00\nLOAD END 2 20.00\nLOAD START 3 30.00\nLOAD END 4 40.00\n",
                                        encoding="utf-8")
    with pytest.raises(ValueError):
        an.load_marks(str(tmp_path / "twice.log"))
    (tmp_path / "backwards.log").write_text("LOAD START 1 20.00\nLOAD END 2 10.00\n", encoding="utf-8")
    with pytest.raises(ValueError):
        an.load_marks(str(tmp_path / "backwards.log"))
    # a log saved by Windows PowerShell starts with a byte order mark and ends its lines with CR LF
    (tmp_path / "bom.log").write_bytes("﻿LOAD START 1 10.00\r\nLOAD END 2 20.00\r\n".encode("utf-8"))
    assert an.load_marks(str(tmp_path / "bom.log")) == (1000, 2000)


def test_summary_nearest_rank_percentiles():
    rs = [{"cpu_pct": float(v), "io_Bps": None, "net_Bps": 0.0, "gpu": "na", "guard_pct": 0.0, "self_pct": 0.0}
          for v in range(1, 11)]
    s = an.summarize(rs)
    assert s["cpu_pct"] == {"n": 10, "missing": 0, "na": 0, "min": 1.0, "p1": 1.0, "p5": 1.0, "p10": 1.0,
                            "p50": 5.0, "p90": 9.0, "p99": 10.0, "max": 10.0}
    assert s["io_Bps"] == {"n": 0, "missing": 10, "na": 0}
    assert s["gpu"] == {"n": 0, "missing": 0, "na": 10}


def test_main_lists_intervals_and_rejects_bad_options(tmp_path, capsys):
    f = tmp_path / "s.tsv"
    f.write_text(RUN + HEADER + row(100, 10000, 0, 0, 0) + row(110, 11000, 1_000_000, 0, 0, 37, 0), encoding="utf-8")
    assert an.main([str(f), "--rows"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "\t".join([str(f), "0", "110", "10", "10", "0", "0", "37", "", ""]) in lines
    assert "intervals: 1" in lines
    log = tmp_path / "load.log"
    log.write_text("LOAD START 1 90.00\nLOAD END 2 110.00\n", encoding="utf-8")   # window 10000 to 11000
    assert an.main([str(f), "--load-window", str(log)]) == 0
    assert "intervals: 1" in capsys.readouterr().out.splitlines()
    short = tmp_path / "short.log"
    short.write_text("LOAD START 1 101.00\nLOAD END 2 104.00\n", encoding="utf-8")   # 3 s inside one interval
    assert an.main([str(f), "--load-window", str(short)]) == 1                 # nothing left: an error, not zeros
    assert an.main([str(f), "--load-window", str(short), "--load-lead", "0"]) == 1
    assert an.main([str(f), "--load-cover", str(short)]) == 0                  # the interval around it
    with pytest.raises(SystemExit):
        an.main([str(f), "--window", "0"])
    with pytest.raises(SystemExit):
        an.main([str(f), "--between", "5", "4"])
    with pytest.raises(SystemExit):
        an.main([str(f), "--between", "1", "2", "--load-window", str(log)])
    with pytest.raises(SystemExit):
        an.main([str(f), "--load-cover", str(short), "--load-window", str(log)])
