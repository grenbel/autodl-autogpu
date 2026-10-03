"""Tests for what cloning adds to the local record (scripts/autodl_ctl.py, plan phase 11): the clones part with its
budget groups, clone settings and clone records; daily fees in the ledger; the clone options of the budget check; auth
inherit and auth released. Every test gets its own AUTODL_GPU_HOME (tests/conftest.py). The helpers are those of
tests/test_store.py."""
import contextlib
import copy
import io
import json
import multiprocessing as mp
import os
import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import autodl_ctl as ctl  # noqa: E402
from test_store import (ID, MID, T0, bj, book, charge_rec, check, full_store, gpu_home, grant, log_ev, off_rec,  # noqa: E402
                        on_rec, put_records, rc_json, reserve_rec, show, write_raw)

CTX = mp.get_context("spawn")
NEW = "ffff000000-0000ffff"   # an instance on another host (ffff000000), as a clone of ID would be
QUOTE = "clone it when no GPU is free for half an hour"


def stored() -> dict:
    return json.loads((gpu_home() / "store.json").read_text(encoding="utf-8"))


def clone_cmd(capsys, *extra, iid=ID, quote=QUOTE):
    return rc_json(capsys, "auth", "clone", "--instance", iid, *extra, "--quote", quote)


# ---- Task 11.1: the clones part and the clone settings ----
def test_the_version_is_0_9_0(capsys):
    assert ctl.main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "0.9.0"


def test_a_record_without_clones_stays_without_it(capsys, clock, tmp_path):
    """Whoever never clones keeps a record that the 0.8 helper reads: the new part is written on first use only."""
    clock.set(MID)
    assert grant(capsys)[0] == 0
    rc, res = check(capsys)
    assert rc == 0
    assert log_ev(capsys, "on", tmp_path, MID + 5, req=res["req"])[0] == 0
    clock.advance(700)
    assert log_ev(capsys, "off", tmp_path, MID + 600)[0] == 0
    assert show(capsys)["clone"] is None
    assert "clones" not in stored()


def test_auth_clone_keeps_the_settings_outside_the_grant(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    rc, res = clone_cmd(capsys, "--enable")
    assert rc == 0, res
    want = {"enabled": True, "wait_s": 1800, "max": 1, "after": "remind", "quote": QUOTE, "at": MID}
    d = stored()
    assert d["clones"] == {"groups": {ID: {"members": [{"instance": ID, "at": MID}], "settings": want}}, "records": {}}
    assert "clone" not in json.dumps(d["grants"])   # nothing of it is in the grant
    clock.advance(60)
    assert grant(capsys, budget="80yuan")[0] == 0   # a new grant leaves the settings alone
    assert stored()["clones"]["groups"][ID]["settings"] == want
    assert rc_json(capsys, "auth", "revoke", "--instance", ID)[0] == 0   # and so does a revoke
    assert stored()["clones"]["groups"][ID]["settings"] == want


def test_auth_clone_takes_what_the_user_chose(capsys, clock):
    clock.set(MID)
    rc, res = clone_cmd(capsys, "--enable", "--wait", "45m", "--max", "3", "--after", "leave", "--said", "both pay 0.03 a day")
    assert rc == 0, res
    s = stored()["clones"]["groups"][ID]["settings"]
    assert (s["wait_s"], s["max"], s["after"], s["said"]) == (2700, 3, "leave", "both pay 0.03 a day")
    assert res["clone"]["enabled"] is True and res["clone"]["wait_s"] == 2700


@pytest.mark.parametrize("args", [
    ["--enable", "--wait", "30s"], ["--enable", "--wait", "25h"], ["--enable", "--wait", "soon"],
    ["--enable", "--max", "0"], ["--enable", "--max", "21"], ["--enable", "--after", "release"],
    ["--enable", "--disable"], [],
])
def test_auth_clone_checks_its_values(capsys, clock, args):
    clock.set(MID)
    rc, _ = clone_cmd(capsys, *args)
    assert rc == ctl.EXIT_ERR
    rc, _ = rc_json(capsys, "auth", "clone", "--instance", ID, "--enable", "--quote", "  ")
    assert rc == ctl.EXIT_ERR
    rc, _ = rc_json(capsys, "auth", "clone", "--instance", "ABC", "--enable", "--quote", QUOTE)
    assert rc == ctl.EXIT_ERR
    p = gpu_home() / "store.json"   # nothing was set up: no record yet, or one without any group
    assert not p.exists() or stored().get("clones", {"groups": {}})["groups"] == {}
    assert clone_cmd(capsys, "--enable")[0] == 0   # the same command with good values is taken


def test_enabling_again_starts_the_task_anew(capsys, clock):
    clock.set(MID)
    assert clone_cmd(capsys, "--enable", "--max", "2")[0] == 0
    clock.advance(3600)
    assert clone_cmd(capsys, "--enable", quote="again, for the next task")[0] == 0
    g = stored()["clones"]["groups"][ID]
    assert g["settings"] == {"enabled": True, "wait_s": 1800, "max": 1, "after": "remind",
                             "quote": "again, for the next task", "at": MID + 3600}
    assert g["members"] == [{"instance": ID, "at": MID}]   # the group itself is as it was


def test_disabling_keeps_what_was_chosen(capsys, clock):
    clock.set(MID)
    assert clone_cmd(capsys, "--enable", "--wait", "1h", "--max", "2", "--after", "leave")[0] == 0
    clock.advance(10)
    rc, res = clone_cmd(capsys, "--disable", quote="not for now")
    assert rc == 0, res
    assert stored()["clones"]["groups"][ID]["settings"] == {"enabled": False, "wait_s": 3600, "max": 2, "after": "leave",
                                                              "quote": "not for now", "at": MID + 10}
    rc, res = clone_cmd(capsys, "--disable", iid=NEW)   # for an instance never set up: off, with the defaults
    assert rc == 0 and stored()["clones"]["groups"][NEW]["settings"]["enabled"] is False


def test_auth_show_has_the_clone_item(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    assert show(capsys)["clone"] is None
    assert clone_cmd(capsys, "--enable", "--max", "2")[0] == 0
    c = show(capsys)["clone"]
    assert c["group"] == ID and c["enabled"] is True and c["wait_s"] == 1800 and c["max"] == 2 and c["made"] == 0
    assert c["after"] == "remind" and c["quote"] == QUOTE and c["at"] == MID
    assert c["members"] == [{"instance": ID, "at": MID}] and c["open_record"] is None


def _clones(**groups):
    d = full_store()
    d["clones"] = {"groups": groups, "records": {}}
    return d


SET = {"enabled": True, "wait_s": 1800, "max": 1, "after": "remind", "quote": "q", "at": T0}
TXN = "00112233445566aa"


def _clone_variants():
    root = {"instance": ID, "at": T0}
    child = {"instance": NEW, "at": T0 + 9, "from": ID, "txn": TXN}

    def g(members=None, **kw):
        return {"members": [root] if members is None else members, **kw}
    bad = {
        "unknown-field-in-the-part": {"groups": {}, "records": {}, "notes": []},
        "groups-not-an-object": {"groups": [], "records": {}},
        "no-records": {"groups": {}},
        "group-id-not-an-instance": {"groups": {"nope": g()}, "records": {}},
        "group-id-not-its-first-member": {"groups": {NEW: g()}, "records": {}},
        "no-members": {"groups": {ID: g(members=[])}, "records": {}},
        "unknown-field-in-a-group": {"groups": {ID: g(budget=1)}, "records": {}},
        "unknown-field-in-a-member": {"groups": {ID: g(members=[dict(root, alias="x")])}, "records": {}},
        "first-member-with-an-origin": {"groups": {ID: g(members=[dict(root, **{"from": NEW, "txn": TXN})])}, "records": {}},
        "an-instance-twice-in-a-group": {"groups": {ID: g(members=[root, dict(child, instance=ID)])}, "records": {}},
        "an-instance-in-two-groups": {"groups": {ID: g(members=[root, child]), NEW: g(members=[{"instance": NEW, "at": T0}])},
                                      "records": {}},
        "origin-outside-the-group": {"groups": {ID: g(members=[root, dict(child, **{"from": "eeee111111-1111eeee"})])},
                                     "records": {}},
        "clone-without-its-transaction": {"groups": {ID: g(members=[root, {"instance": NEW, "at": T0, "from": ID}])},
                                          "records": {}},
        "bad-transaction": {"groups": {ID: g(members=[root, dict(child, txn="xyz")])}, "records": {}},
        "released-at-not-a-number": {"groups": {ID: g(members=[dict(root, released_at="yesterday")])}, "records": {}},
        "settings-switch-not-a-boolean": {"groups": {ID: g(settings=dict(SET, enabled=1))}, "records": {}},
        "settings-wait-too-short": {"groups": {ID: g(settings=dict(SET, wait_s=59))}, "records": {}},
        "settings-max-zero": {"groups": {ID: g(settings=dict(SET, max=0))}, "records": {}},
        "settings-unknown-after": {"groups": {ID: g(settings=dict(SET, after="release"))}, "records": {}},
        "settings-empty-quote": {"groups": {ID: g(settings=dict(SET, quote=" "))}, "records": {}},
        "settings-unknown-field": {"groups": {ID: g(settings=dict(SET, monthly=3))}, "records": {}},
    }
    return bad


@pytest.mark.parametrize("name", sorted(_clone_variants()))
def test_a_malformed_clones_part_makes_the_record_unusable(name):
    d = full_store()
    d["clones"] = _clone_variants()[name]
    raw = json.dumps(d).encode()
    p = write_raw(raw)
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "invalid", (name, str(e.value))
    assert "unknown fields ['clones']" not in str(e.value)   # refused for what is wrong in it, not for being there
    assert p.read_bytes() == raw


def test_a_well_formed_clones_part_is_read(clock):
    clock.set(T0 + 3000)
    members = [{"instance": ID, "at": T0}, {"instance": NEW, "at": T0 + 9, "from": ID, "txn": TXN, "released_at": T0 + 99}]
    write_raw(json.dumps(_clones(**{ID: {"members": members, "settings": dict(SET, said="told")}})).encode())
    with ctl.Store() as st:
        assert st.data["clones"]["groups"][ID]["members"][1]["from"] == ID


# ---- Task 11.2: daily records, clone reservations, and the daily fee in the spending ----
SEP1 = bj(2026, 9, 1)
DAY = 86400


def eod(day: int, month: int = 9) -> int:
    """23:59:59 Beijing time of that day of 2026: when the platform charges the daily fee of an expanded disk."""
    return bj(2026, month, day, 23, 59, 59)


def daily_rec(at, fen):
    return {"kind": "daily", "key": f"daily:{at}", "at": at, "fen_day": fen}


def daily_cmd(capsys, fee="0.03", since=None, iid=ID):
    return rc_json(capsys, "auth", "daily", "--instance", iid, "--fee", fee, *([] if since is None else ["--from", since]))


def test_the_ledger_takes_daily_records_and_clone_reservations(clock):
    clock.set(T0 + 3000)
    d = full_store()
    d["ledger"][ID] += [daily_rec(T0 + 10, 3), daily_rec(T0 + 20, 0),
                        dict(reserve_rec("aaaabbbbccccdddd", T0 + 30), clone_host="ffff000000", daily_fen=3),
                        dict(reserve_rec("aaaabbbbccccdddf", T0 + 40), clone_host="ffff000000")]
    write_raw(json.dumps(d).encode())
    with ctl.Store() as st:
        assert st.data["ledger"][ID][-2]["daily_fen"] == 3


def _ledger_variants():
    r = dict(reserve_rec("aaaabbbbccccdddd", T0 + 30), clone_host="ffff000000", daily_fen=3)
    return {
        "daily-key-not-its-time": [dict(daily_rec(T0 + 10, 3), key="daily:1")],
        "daily-negative": [daily_rec(T0 + 10, -1)],
        "daily-not-a-whole-number": [daily_rec(T0 + 10, 2.5)],
        "daily-bool": [daily_rec(T0 + 10, True)],
        "daily-unknown-field": [dict(daily_rec(T0 + 10, 3), gb=5)],
        "daily-out-of-order": [daily_rec(T0 + 20, 3), daily_rec(T0 + 10, 4)],
        "daily-twice-at-one-time": [daily_rec(T0 + 10, 3), daily_rec(T0 + 10, 4)],
        "daily-fee-without-a-clone-host": [{k: v for k, v in r.items() if k != "clone_host"}],
        "daily-fee-of-zero-on-a-reservation": [dict(r, daily_fen=0)],
        "clone-host-not-a-host-id": [dict(r, clone_host="ffff000000-0000ffff")],
        "clone-reservation-without-gpus": [dict(reserve_rec("aaaabbbbccccdddd", T0 + 30, mode="nogpu", gpus=0, price=10),
                                                clone_host="ffff000000")],
        "a-charge-marked-false": [dict(charge_rec("SN9", T0 + 50, 3), disk=False)],   # only a disk charge is marked
        "a-charge-marked-otherwise": [dict(charge_rec("SN9", T0 + 50, 3), disk="yes")],
    }


@pytest.mark.parametrize("name", sorted(_ledger_variants()))
def test_a_malformed_daily_record_or_clone_reservation_is_refused(name):
    d = full_store()
    d["ledger"][ID] += _ledger_variants()[name]
    raw = json.dumps(d).encode()
    p = write_raw(raw)
    with pytest.raises(ctl.StoreError) as e:
        with ctl.Store():
            pass
    assert e.value.kind == "invalid", (name, str(e.value))
    # refused for what is wrong in it: not because the kind or the two new fields are unknown to the helper
    assert all(w not in str(e.value) for w in ("unknown kind", "'clone_host'", "'daily_fen'")), str(e.value)
    assert p.read_bytes() == raw


def test_the_daily_charge_falls_at_23_59_59_beijing():
    n = ctl.daily_instants
    assert n(bj(2026, 10, 2), bj(2026, 10, 3)) == 1                       # one day holds one
    assert n(eod(2, 10), eod(2, 10) + 1) == 1 and n(eod(2, 10) + 1, eod(3, 10)) == 0   # the very second counts, once
    assert n(eod(2, 10), eod(3, 10) + 1) == 2
    assert n(bj(2026, 10, 1), bj(2026, 11, 1)) == 31 and n(bj(2026, 9, 1), bj(2026, 10, 1)) == 30
    assert n(bj(2026, 10, 2, 12), bj(2026, 10, 2, 13)) == 0 and n(100, 100) == 0 and n(200, 100) == 0


def test_auth_daily_counts_from_the_start_of_the_period(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    rc, res = daily_cmd(capsys)
    assert rc == 0 and res["recorded"] is True and res["from"] == SEP1 and res["daily_fen"] == 3, res
    assert book()[-1] == daily_rec(SEP1, 3)
    # a budget without periods counts from the full hour of its grant, and so does its daily fee
    clock.set(MID + 1234)
    assert grant(capsys, budget="50yuan", period="none", iid=NEW)[0] == 0
    assert daily_cmd(capsys, iid=NEW)[1]["from"] == MID
    # an instance that is only known by its ledger: the calendar month in +08:00
    other = "eeee111111-1111eeee"
    put_records([], iid=other)
    assert daily_cmd(capsys, iid=other)[1]["from"] == SEP1
    assert daily_cmd(capsys, iid="dddd222222-2222dddd")[0] == ctl.EXIT_ERR   # not known here at all


def test_auth_daily_again_writes_nothing(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0 and daily_cmd(capsys)[0] == 0
    before = book()
    rc, res = daily_cmd(capsys)
    assert rc == 0 and res["recorded"] is False and book() == before
    clock.set(bj(2026, 10, 5))   # next month the default start is later, and the fee in force is the same
    assert rc_json(capsys, "auth", "charges", "--instance", ID, "--json", "[]")[0] == 0
    before = book()
    rc, res = daily_cmd(capsys)
    assert rc == 0 and res["recorded"] is False and book() == before


def test_auth_daily_refuses_a_start_before_the_last_one(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0 and daily_cmd(capsys, since=MID - 3 * DAY)[0] == 0
    before = book()
    assert daily_cmd(capsys, fee="0.05", since=MID - 5 * DAY)[0] == ctl.EXIT_ERR
    assert daily_cmd(capsys, fee="0.05", since=MID - 3 * DAY)[0] == ctl.EXIT_ERR   # the same time with another fee
    assert daily_cmd(capsys, fee="0.05", since=MID + 3600)[0] == ctl.EXIT_ERR      # more than five minutes ahead
    assert daily_cmd(capsys, fee="0.035", since=MID)[0] == ctl.EXIT_ERR            # not whole fen
    assert book() == before
    rc, res = daily_cmd(capsys, fee="0.05", since=MID - DAY)   # a later change of the fee is taken
    assert rc == 0 and book()[-1] == daily_rec(MID - DAY, 5)


def test_a_fee_of_zero_stops_it(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0 and daily_cmd(capsys)[0] == 0
    assert show(capsys)["daily_fen"] == 3
    rc, res = daily_cmd(capsys, fee="0", since=MID - DAY)
    assert rc == 0 and book()[-1] == daily_rec(MID - DAY, 0)
    s = show(capsys)
    # Sep 1 to Sep 8 were charged (8 days); Sep 9 was not, as the fee ended on Sep 9 at noon
    assert s["daily_fen"] == 0 and s["spent_fen"] == 8 * 3
    rc, res = daily_cmd(capsys, fee="0")   # nothing is in force: nothing to stop
    assert rc == 0 and res["recorded"] is False


def test_daily_fees_count_in_a_money_budget(capsys, clock):
    clock.set(MID)   # Sep 10, noon: nine charge times since Sep 1
    assert grant(capsys)[0] == 0 and daily_cmd(capsys)[0] == 0
    assert show(capsys)["spent_fen"] == 9 * 3
    put_records([on_rec("t1-1", MID - 3600, unreserved=True), off_rec("t1-1", MID - 1800)])   # half an hour at 0.98
    s = show(capsys)
    assert s["spent_fen"] == 27 + 49 and s["remaining_fen"] == 5000 - 76
    clock.set(eod(10))   # the very second of the next charge
    assert show(capsys)["spent_fen"] == 30 + 49
    rc, res = check(capsys, hours="1")
    assert rc == 0 and res["periods"][0]["spent_fen"] == 30 + 49


def test_imported_daily_charges_are_not_counted_twice(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0 and daily_cmd(capsys)[0] == 0
    put_records([on_rec("t1-1", MID - 3600, unreserved=True), off_rec("t1-1", MID - 1800)])
    assert show(capsys)["spent_fen"] == 76
    put_records([charge_rec("S1", MID - 1800, 49)])   # only the session's charge was imported: the estimate holds
    assert show(capsys)["spent_fen"] == 76
    put_records([dict(charge_rec(f"D{d}", eod(d), 3), disk=True) for d in range(1, 10)])   # and the nine daily charges
    assert show(capsys)["spent_fen"] == 76
    clock.set(eod(10) + 5)   # a charge time later than every imported charge is estimated
    assert show(capsys)["spent_fen"] == 79


def charges(capsys, rows, iid=ID):
    return rc_json(capsys, "auth", "charges", "--instance", iid, "--json", json.dumps(rows))


def test_charges_imported_without_the_disk_rows_do_not_hide_the_daily_fees(capsys, clock):
    """Charges of power-ons the ledger never saw are imported and the data disk's daily rows are left out: the daily
    fees that are known count on top of those charges, they are not swallowed by them."""
    clock.set(MID)   # Sep 10, noon: nine charge times since Sep 1, 27 fen
    assert grant(capsys)[0] == 0 and daily_cmd(capsys)[0] == 0
    assert show(capsys)["spent_fen"] == 27
    assert charges(capsys, [{"serial": "S1", "instance": ID, "time": "2026-09-10 11:00:00", "amount": "10.00"}])[0] == 0
    assert show(capsys)["spent_fen"] == 1000 + 27


def test_disk_rows_are_imported_as_such_and_counted_once(capsys, clock):
    clock.set(MID)
    assert grant(capsys)[0] == 0 and daily_cmd(capsys)[0] == 0
    rows = [{"serial": f"D{d}", "instance": ID, "time": f"2026-09-{d:02d} 23:59:59", "amount": "0.03", "disk": True}
            for d in range(1, 10)]
    rc, res = charges(capsys, rows + [{"serial": "S1", "instance": ID, "time": "2026-09-10 11:00:00", "amount": "10.00",
                                       "disk": False}])
    assert rc == 0 and res["imported"] == 10, res
    assert book()[-10] == dict(charge_rec("D1", eod(1), 3), disk=True) and book()[-1] == charge_rec("S1", MID - 3600, 1000)
    assert show(capsys)["spent_fen"] == 1000 + 27
    clock.set(eod(10) + 5)   # the next charge time is estimated on top of the imported ones
    assert show(capsys)["spent_fen"] == 1000 + 30
    assert charges(capsys, rows[:1])[1]["already"] == 1                                   # the same row again
    assert charges(capsys, [dict(rows[0], disk=False)])[0] == ctl.EXIT_ERR                 # not the same row


def test_a_row_at_the_daily_charge_time_has_to_say_what_it_is(capsys, clock):
    """At 23:59:59 Beijing time, on an instance with a daily fee, a row may be the data disk's charge or a power-on's
    (a running instance is charged at every XX:59:59, and the two often come in the same second): unless the row says
    which, nothing is imported. Elsewhere, and without a daily fee, a row needs to say nothing."""
    clock.set(MID)
    assert grant(capsys)[0] == 0
    row = {"serial": "X1", "instance": ID, "time": "2026-09-09 23:59:59", "amount": "0.03"}
    assert charges(capsys, [row])[0] == 0                         # no daily fee is known for this instance yet
    assert daily_cmd(capsys)[0] == 0
    before = raw()
    row2 = dict(row, serial="X2", time="2026-09-08 23:59:59")
    assert charges(capsys, [row2])[0] == ctl.EXIT_ERR and raw() == before
    assert charges(capsys, [dict(row2, disk="yes")])[0] == ctl.EXIT_ERR and raw() == before   # true or false only
    assert charges(capsys, [dict(row2, disk=False)])[0] == 0      # a power-on's, as a running instance has every 59:59
    assert book()[-1] == charge_rec("X2", eod(8), 3)
    assert charges(capsys, [dict(row, serial="X3", time="2026-09-09 22:00:00")])[0] == 0   # another time: as before


def test_a_disk_mark_off_the_daily_charge_time_is_refused(capsys, clock):
    """The data disk is charged at 23:59:59 Beijing time only. A row marked as the disk's at any other time is a
    power-on's marked by mistake; taken as the disk's, it would hide the daily fees: the whole batch is refused."""
    clock.set(MID)
    assert grant(capsys)[0] == 0 and daily_cmd(capsys)[0] == 0
    good = {"serial": "D9", "instance": ID, "time": "2026-09-09 23:59:59", "amount": "0.03", "disk": True}
    wrong = {"serial": "S1", "instance": ID, "time": "2026-09-10 11:00:00", "amount": "10.00", "disk": True}
    before = raw()
    rc, res = charges(capsys, [good, wrong])
    assert rc == ctl.EXIT_ERR and raw() == before
    assert charges(capsys, [good, dict(wrong, disk=False)])[0] == 0
    assert show(capsys)["spent_fen"] == 1000 + 27


def test_a_gpu_hour_budget_leaves_daily_fees_out(capsys, clock):
    clock.set(MID)
    assert grant(capsys, budget="10gpuh")[0] == 0 and daily_cmd(capsys)[0] == 0
    s = show(capsys)
    assert s["daily_fen"] == 3 and s["spent_gpu_s"] == 0 and s["remaining_gpu_hours"] == 10.0
    assert check(capsys, hours="1")[0] == 0


# ---- Task 11.3: clone records ----
HOSTS = "ffff000000,eeee111111"
CREQ = "aaaabbbbccccdddd"       # the request ID of the clone's reservation
JREQ = "1111222233334444"       # the request ID of the job's launch
DIGESTS = "19:0a1b2c3d,19:4e5f6a7b"   # what the page script answers for the instance IDs before the creation


def rec_cmd(capsys, verb, *args):
    return rc_json(capsys, "clone-record", verb, *args)


def open_rec(capsys, project, hosts=HOSTS, iid=ID):
    return rec_cmd(capsys, "open", "--instance", iid, "--project", project, "--hosts", hosts)


def update(capsys, txn, *args):
    return rec_cmd(capsys, "update", "--txn", txn, *args)


def record(txn) -> dict:
    return stored()["clones"]["records"][txn]


def edit_store(change) -> None:
    """What later tasks' commands leave in the record (ticket, reservation, membership), written straight into it."""
    with ctl.Store() as st:
        change(st.data)
        st.save()


def started(capsys, clock, tmp_path, **grant_kw) -> str:
    """A grant, cloning enabled, a clone record opened: its transaction ID."""
    clock.set(MID)
    assert grant(capsys, **grant_kw)[0] == 0 and clone_cmd(capsys, "--enable", "--max", "2")[0] == 0
    rc, res = open_rec(capsys, tmp_path)
    assert rc == 0, res
    return res["txn"]


def create_args(req=CREQ) -> list:
    return ["--set", "host=ffff000000", "--set", "gpus=1", "--set", "price=0.98", "--set", "expand-gb=5",
            "--set", "daily=0.03", "--set", f"req={req}", "--set", f"t0={MID + 30}", "--set", f"before={DIGESTS}"]


STEPS = ["opened", "ticket", "reserve", "click", "clicked", "adopted", "taken-over", "launching", "launched", "switched"]


def advance(capsys, txn, upto: str, req=CREQ) -> None:
    """Take a record from where it is to UPTO, one stage after the other, with what each stage stands for put in place."""
    for stage in STEPS[STEPS.index(record(txn)["stage"]) + 1:STEPS.index(upto) + 1]:
        args = ["--stage", stage]
        if stage == "reserve":
            edit_store(lambda d: d["clones"]["records"][txn].update(source_ticket="present", ticket_deadline=MID + 4000))
        elif stage == "click":
            put_records([dict(reserve_rec(req, MID + 20), clone_host="ffff000000", daily_fen=3)])
            args += create_args(req)
        elif stage == "clicked":
            args += ["--set", "answer=created", "--set", "created=yes"]
        elif stage == "adopted":
            def adopt(d):
                d["ledger"][ID].append({"kind": "release", "key": f"release:{req}", "req": req, "at": MID + 60})
                d["clones"]["groups"][ID]["members"].append({"instance": NEW, "at": MID + 60, "from": ID, "txn": txn})
            edit_store(adopt)
            args += ["--set", f"instance={NEW}"]
        elif stage == "taken-over":
            edit_store(lambda d: d["clones"]["records"][txn].update(clone_ticket="cleared"))
        elif stage == "launching":
            args += ["--set", "job=train", "--set", f"job-req={JREQ}"]
        rc, res = update(capsys, txn, *args)
        assert rc == 0, (stage, res)


def test_open_writes_one_record(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    assert ctl.TXN_RE.match(txn)
    r = record(txn)
    assert r == {"txn": txn, "source": ID, "project": str(pathlib.Path(tmp_path).resolve().as_posix()), "opened_at": MID,
                 "hosts": ["ffff000000", "eeee111111"], "stage": "opened"}
    o = show(capsys)["clone"]["open_record"]
    assert o == {"txn": txn, "stage": "opened", "project": r["project"], "source": ID, "instance": None}
    rc, res = rec_cmd(capsys, "show", "--txn", txn)
    assert rc == 0 and res["records"] == [r]


def test_open_needs_the_switch_on_and_a_count_left(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    rc, res = open_rec(capsys, tmp_path)
    assert rc == ctl.EXIT_ERR and "not enabled" in res["reason"] and "clones" not in stored()
    assert clone_cmd(capsys, "--disable")[0] == 0
    assert open_rec(capsys, tmp_path)[0] == ctl.EXIT_ERR and stored()["clones"]["records"] == {}
    assert clone_cmd(capsys, "--enable")[0] == 0   # one clone for this task
    rc, res = open_rec(capsys, tmp_path)
    assert rc == 0
    advance(capsys, res["txn"], "click")
    update(capsys, res["txn"], "--stage", "clicked", "--set", "answer=an error", "--set", "created=no", "--set", "note=the list shows none")
    edit_store(lambda d: d["ledger"][ID].append({"kind": "release", "key": f"release:{CREQ}", "req": CREQ, "at": MID + 99}))
    edit_store(lambda d: d["clones"]["records"][res["txn"]].update(source_ticket="cleared"))
    assert rec_cmd(capsys, "close", "--txn", res["txn"])[0] == 0
    assert show(capsys)["clone"]["made"] == 0   # what was found to have created nothing does not count
    rc2, res2 = open_rec(capsys, tmp_path)
    assert rc2 == 0, res2
    advance(capsys, res2["txn"], "reserve")
    assert show(capsys)["clone"]["made"] == 0   # nor does one that has not reached the click
    advance(capsys, res2["txn"], "click", req=CREQ[::-1])   # this one may have created an instance
    assert show(capsys)["clone"]["made"] == 1
    # the reservation went back and the record is closed as not created: the count is free again, but while it is
    # undecided the task's one clone is used up
    rc3, res3 = update(capsys, res2["txn"], "--stage", "clicked", "--set", "answer=")
    assert rc3 == 0 and show(capsys)["clone"]["made"] == 1


@pytest.mark.parametrize("hosts", ["", "ffff000000,", "ffff000000,ffff000000", "abcd123456", "ffff000000,abcd123456",
                                   "FFFF000000", "ffff000000-0000ffff", ",".join(f"host{i:06d}" for i in range(21))])
def test_open_refuses_bad_hosts(capsys, clock, tmp_path, hosts):
    clock.set(MID)
    assert clone_cmd(capsys, "--enable")[0] == 0
    assert open_rec(capsys, tmp_path, hosts=hosts)[0] == ctl.EXIT_ERR
    assert stored()["clones"]["records"] == {}
    assert open_rec(capsys, tmp_path)[0] == 0


def test_a_second_open_in_the_group_is_refused(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    rc, res = open_rec(capsys, tmp_path / "other-project")   # whichever project asks
    assert rc == ctl.EXIT_ERR and txn in res["reason"] and len(stored()["clones"]["records"]) == 1
    advance(capsys, txn, "adopted")   # NEW is a member now: a clone of the clone has to wait as well
    rc, res = open_rec(capsys, tmp_path, hosts="eeee111111", iid=NEW)
    assert rc == ctl.EXIT_ERR and txn in res["reason"]


def _child_open(home, project, hold, reached, go, q):
    """clone-record open in a process of its own; with HOLD it stops right after deciding, the record's lock held."""
    os.environ["AUTODL_GPU_HOME"] = home
    if hold:
        def hook(point, path=None):
            if point == "clone-open-decided":
                reached.set()
                go.wait(60)
        ctl._hook = hook
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = ctl.main(["clone-record", "open", "--instance", ID, "--project", project, "--hosts", "ffff000000"])
    q.put((rc, buf.getvalue()))


def test_two_opens_at_once_leave_one_record(capsys, tmp_path):
    assert clone_cmd(capsys, "--enable", "--max", "5")[0] == 0   # the real clock: the children use it too
    home = str(gpu_home())
    reached, go, q = CTX.Event(), CTX.Event(), CTX.Queue()
    a = CTX.Process(target=_child_open, args=(home, str(tmp_path / "a"), True, reached, go, q), daemon=True)
    a.start()
    assert reached.wait(60)
    b = CTX.Process(target=_child_open, args=(home, str(tmp_path / "b"), False, reached, go, q), daemon=True)
    b.start()
    time.sleep(1.5)   # b is waiting for the record's lock, which a holds
    assert q.empty()
    go.set()
    got = sorted((q.get(timeout=60) for _ in range(2)), key=lambda x: x[0])
    a.join(30)
    b.join(30)
    assert [rc for rc, _ in got] == [0, ctl.EXIT_ERR], got
    recs = stored()["clones"]["records"]
    assert len(recs) == 1 and json.loads(got[0][1])["txn"] in recs
    assert "not closed yet" in json.loads(got[1][1])["reason"]


def test_update_and_close_need_the_right_transaction(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    before = (gpu_home() / "store.json").read_bytes()
    other = "ffffeeeeddddcccc"
    for args in (["update", "--txn", other, "--stage", "ticket"], ["close", "--txn", other],
                 ["update", "--txn", "nope", "--stage", "ticket"], ["update", "--stage", "ticket"]):
        rc, res = rec_cmd(capsys, *args)
        assert rc == ctl.EXIT_ERR, (args, res)
    assert (gpu_home() / "store.json").read_bytes() == before
    assert rec_cmd(capsys, "close", "--txn", txn)[0] == 0   # nothing was done yet: it closes as it is
    closed = (gpu_home() / "store.json").read_bytes()
    for args in (["update", "--txn", txn, "--stage", "ticket"], ["update", "--txn", txn, "--set", "note=late"],
                 ["close", "--txn", txn]):
        rc, res = rec_cmd(capsys, *args)
        assert rc == ctl.EXIT_ERR and "closed" in res["reason"], (args, res)
    assert (gpu_home() / "store.json").read_bytes() == closed


def test_a_stage_goes_one_step_forward(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    assert update(capsys, txn, "--stage", "reserve")[0] == ctl.EXIT_ERR      # a step skipped
    assert update(capsys, txn, "--stage", "arrived")[0] == ctl.EXIT_ERR      # not a stage
    assert update(capsys, txn, "--stage", "opened")[0] == 0                  # where it is: nothing changes
    advance(capsys, txn, "reserve")
    assert update(capsys, txn, "--stage", "opened")[0] == ctl.EXIT_ERR       # back
    rc, res = update(capsys, txn, "--stage", "ticket")                       # the one way back: to prepare again
    assert rc == 0 and record(txn)["stage"] == "ticket"
    advance(capsys, txn, "click")
    rc, res = update(capsys, txn, "--stage", "ticket")
    assert rc == ctl.EXIT_ERR and record(txn)["stage"] == "click"            # never back from the click
    # with the clone's reservation open, reserve cannot go back to ticket: release it first
    edit_store(lambda d: d["clones"]["records"][txn].update(stage="reserve"))
    rc, res = update(capsys, txn, "--stage", "ticket")
    assert rc == ctl.EXIT_ERR and CREQ in res["reason"], res


def test_going_back_to_ticket_drops_what_was_read_for_the_creation(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, "reserve")
    assert update(capsys, txn, "--set", "host=eeee111111", "--set", "price=1.03", "--set", "gpus=1")[0] == 0
    assert record(txn)["host"] == "eeee111111" and record(txn)["price_fen_h"] == 103
    assert update(capsys, txn, "--set", "hosts=ffff000000")[0] == ctl.EXIT_ERR   # the allowed hosts are fixed by now
    assert update(capsys, txn, "--stage", "ticket")[0] == 0
    r = record(txn)
    assert r["stage"] == "ticket" and "host" not in r and "price_fen_h" not in r and "gpus" not in r
    assert update(capsys, txn, "--set", "hosts=ffff000000")[0] == 0 and record(txn)["hosts"] == ["ffff000000"]


NEEDS = [
    ("reserve", lambda d, t: d["clones"]["records"][t].pop("source_ticket"), "ticket"),
    ("click", lambda d, t: d["clones"]["records"][t].pop("t0"), "t0"),
    ("click", lambda d, t: d["clones"]["records"][t].update(host="eeee111111"), "reservation"),
    ("click", lambda d, t: d["ledger"][ID].append({"kind": "release", "key": f"release:{CREQ}", "req": CREQ, "at": MID + 40}),
     "reservation"),
    ("click", lambda d, t: d["clones"]["records"][t].update(daily_fen=4), "reservation"),
    ("click", lambda d, t: d["clones"]["records"][t].pop("daily_fen"), "daily"),
    ("clicked", lambda d, t: d["clones"]["records"][t].pop("answer"), "answer"),
    ("adopted", lambda d, t: d["clones"]["groups"][ID]["members"].pop(), "inherit"),
    ("adopted", lambda d, t: d["clones"]["records"][t].pop("instance"), "instance"),
    ("taken-over", lambda d, t: d["clones"]["records"][t].pop("clone_ticket"), "ticket"),
    ("taken-over", lambda d, t: d["clones"]["records"][t].update(emergency_timer="2026-09-10 13:00:00"), "timer"),
    ("launching", lambda d, t: d["clones"]["records"][t].pop("job_req"), "job"),
]


@pytest.mark.parametrize("n", range(len(NEEDS)))
def test_each_stage_needs_what_it_stands_for(capsys, clock, tmp_path, n):
    """The record is taken to the stage with everything in place, set back one stage, one thing is taken away, and the
    step is asked for again: refused, with the record as it was. With the thing back, the same step is taken."""
    stage, spoil, word = NEEDS[n]
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, stage)
    good = copy.deepcopy(stored())
    prev = STEPS[STEPS.index(stage) - 1]

    def setback(d):
        d["clones"]["records"][txn]["stage"] = prev
    edit_store(setback)
    edit_store(lambda d: spoil(d, txn))
    before = (gpu_home() / "store.json").read_bytes()
    rc, res = update(capsys, txn, "--stage", stage)
    assert rc == ctl.EXIT_ERR and word in res["reason"], res
    assert (gpu_home() / "store.json").read_bytes() == before
    write_raw(json.dumps(good).encode())
    edit_store(setback)
    assert update(capsys, txn, "--stage", stage)[0] == 0


def close(capsys, txn, *args):
    return rec_cmd(capsys, "close", "--txn", txn, *args)


def test_close_needs_the_source_ticket_gone_and_no_open_reservation(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, "reserve")   # the ticket is on the source
    rc, res = close(capsys, txn)
    assert rc == ctl.EXIT_ERR and "ticket" in res["reason"] and "closed_at" not in record(txn)
    edit_store(lambda d: d["clones"]["records"][txn].update(source_ticket="cleared"))
    edit_store(lambda d: d["ledger"][ID].append(dict(reserve_rec(CREQ, MID + 20), clone_host="ffff000000")))
    rc, res = close(capsys, txn)
    assert rc == ctl.EXIT_ERR and CREQ in res["reason"] and "closed_at" not in record(txn)
    edit_store(lambda d: d["ledger"][ID].append({"kind": "release", "key": f"release:{CREQ}", "req": CREQ, "at": MID + 40}))
    rc, res = close(capsys, txn)
    assert rc == 0 and res["outcome"] == "unused" and record(txn)["closed_at"] == MID


def test_a_record_past_opened_closes_only_once_the_source_ticket_is_noted_as_cleared(capsys, clock, tmp_path):
    """A ticket write that reached the instance and was never noted (the process died in between) leaves the record at
    ticket without source_ticket: the ticket may be on the source. Only a clear that was noted lets the clone be closed."""
    txn = started(capsys, clock, tmp_path)
    assert update(capsys, txn, "--stage", "ticket")[0] == 0 and "source_ticket" not in record(txn)
    before = raw()
    rc, res = close(capsys, txn)
    assert rc == ctl.EXIT_ERR and "ticket clear" in res["reason"] and "--source" in res["reason"], res
    assert raw() == before
    edit_store(lambda d: d["clones"]["records"][txn].update(source_ticket="cleared"))
    rc, res = close(capsys, txn)
    assert rc == 0 and res["outcome"] == "unused", res


def test_a_record_at_opened_closes_as_it_is(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)       # nothing was touched yet: no ticket can be anywhere
    rc, res = close(capsys, txn)
    assert rc == 0 and res["outcome"] == "unused" and "source_ticket" not in record(txn), res


def test_a_click_whose_outcome_is_unknown_cannot_be_closed(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, "click")
    edit_store(lambda d: d["clones"]["records"][txn].update(source_ticket="cleared"))
    edit_store(lambda d: d["ledger"][ID].append({"kind": "release", "key": f"release:{CREQ}", "req": CREQ, "at": MID + 40}))
    rc, res = close(capsys, txn)   # nobody has found out yet whether an instance was created
    assert rc == ctl.EXIT_ERR and "created" in res["reason"]
    assert update(capsys, txn, "--stage", "clicked", "--set", "answer=")[0] == 0
    assert close(capsys, txn)[0] == ctl.EXIT_ERR
    assert update(capsys, txn, "--set", "created=no")[0] == ctl.EXIT_ERR   # how it was found out has to be noted
    assert update(capsys, txn, "--set", "created=no", "--set", "note=the list and the billing detail show none")[0] == 0
    rc, res = close(capsys, txn)
    assert rc == 0 and res["outcome"] == "not-created"


@pytest.mark.parametrize("stage, outcome", [("opened", "unused"), ("ticket", "unused"), ("reserve", "unused"),
                                            ("adopted", "not-switched"), ("launched", "not-switched"),
                                            ("switched", "switched")])
def test_close_names_the_outcome(capsys, clock, tmp_path, stage, outcome):
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, stage)
    edit_store(lambda d: d["clones"]["records"][txn].update(source_ticket="cleared"))
    clock.advance(500)
    if outcome == "not-switched":   # both instances exist and the task did not move: say what becomes of them
        rc, res = close(capsys, txn)
        assert rc == ctl.EXIT_ERR and "--note" in res["reason"]
        rc, res = close(capsys, txn, "--note", "the GPU of the new one failed; both are kept, the user decides")
    else:
        rc, res = close(capsys, txn)
    assert rc == 0 and res["outcome"] == outcome, res
    r = record(txn)
    assert r["outcome"] == outcome and r["closed_at"] == MID + 500 and r["stage"] == stage
    assert show(capsys)["clone"]["open_record"] is None
    assert rec_cmd(capsys, "show")[1]["records"] == [] and rec_cmd(capsys, "show", "--all")[1]["records"] == [r]


def _copy(project) -> pathlib.Path:
    return pathlib.Path(project) / ".autodl" / "clone_pending.json"


def test_the_project_copy_follows_the_record(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    assert json.loads(_copy(tmp_path).read_text(encoding="utf-8")) == record(txn)
    rc, res = update(capsys, txn, "--stage", "ticket", "--set", "note=about to write the ticket")
    assert rc == 0 and res["copy"] == "written"
    assert json.loads(_copy(tmp_path).read_text(encoding="utf-8")) == record(txn)
    # the copy was changed or lost: show with the project puts it back as the record has it
    _copy(tmp_path).write_text("{}", encoding="utf-8")
    rc, res = rec_cmd(capsys, "show", "--txn", txn, "--project", tmp_path)
    assert rc == 0 and res["copy"] == "rewritten" and json.loads(_copy(tmp_path).read_text(encoding="utf-8")) == record(txn)
    assert rec_cmd(capsys, "show", "--txn", txn, "--project", tmp_path)[1]["copy"] == "as the record"
    _copy(tmp_path).unlink()
    assert rec_cmd(capsys, "show", "--project", tmp_path)[1]["copy"] == "rewritten"
    # closing renames it
    edit_store(lambda d: d["clones"]["records"][txn].update(source_ticket="cleared"))
    assert close(capsys, txn)[0] == 0
    assert not _copy(tmp_path).exists()
    kept = pathlib.Path(tmp_path) / ".autodl" / f"clone_{txn}.json"
    assert json.loads(kept.read_text(encoding="utf-8")) == record(txn)
    assert rec_cmd(capsys, "show", "--project", tmp_path)[1]["copy"] == "none"   # nothing is open: no copy, none made


def test_a_copy_that_cannot_be_written_does_not_fail_the_command(capsys, clock, tmp_path):
    clock.set(MID)
    assert clone_cmd(capsys, "--enable")[0] == 0
    blocked = tmp_path / "project"
    blocked.mkdir()
    (blocked / ".autodl").write_text("a file where the folder would be", encoding="utf-8")
    rc, res = open_rec(capsys, blocked)
    assert rc == 0 and res["copy"].startswith("not written") and res["txn"] in stored()["clones"]["records"]
    rc, res = update(capsys, res["txn"], "--stage", "ticket")
    assert rc == 0 and res["copy"].startswith("not written") and record(res["txn"])["stage"] == "ticket"


SETS = [
    ("opened", ["host=ffff000000"]), ("opened", ["answer=x"]), ("opened", ["job=train"]), ("opened", ["instance=" + NEW]),
    ("reserve", ["host=dddd222222"]), ("reserve", ["host=nope"]), ("reserve", ["gpus=0"]), ("reserve", ["price=0"]),
    ("reserve", ["price=0.985"]), ("reserve", ["expand-gb=-1"]), ("reserve", ["req=xyz"]), ("reserve", ["t0=soon"]),
    ("reserve", [f"t0={MID + 9999}"]), ("reserve", ["before=abc"]), ("reserve", ["stage=click"]), ("reserve", ["txn=x"]),
    ("reserve", ["host"]), ("click", ["created=maybe"]), ("click", ["instance=eeee111111-1111eeee"]),
    ("click", ["instance=" + ID]), ("click", ["emergency-timer=tomorrow"]), ("click", ["hosts=ffff000000"]),
    ("taken-over", ["job=guard"]), ("taken-over", ["job-req=1"]), ("opened", ["note="]), ("opened", ["note=" + "x" * 501]),
]


@pytest.mark.parametrize("n", range(len(SETS)))
def test_set_takes_only_what_fits(capsys, clock, tmp_path, n):
    stage, sets = SETS[n]
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, stage)
    before = (gpu_home() / "store.json").read_bytes()
    rc, res = update(capsys, txn, *[w for s in sets for w in ("--set", s)])
    assert rc == ctl.EXIT_ERR, res
    assert (gpu_home() / "store.json").read_bytes() == before
    assert update(capsys, txn, "--set", "note=a note is taken at every stage")[0] == 0


def test_the_instance_once_noted_cannot_become_another(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, "clicked")
    assert update(capsys, txn, "--set", f"instance={NEW}")[0] == 0
    assert update(capsys, txn, "--set", f"instance={NEW}")[0] == 0             # the same again
    rc, res = update(capsys, txn, "--set", "instance=ffff000000-1111ffff")     # another one on that host
    assert rc == ctl.EXIT_ERR and record(txn)["instance"] == NEW
    assert update(capsys, txn, "--set", "emergency-timer=2026-09-10 13:00:00")[0] == 0
    assert record(txn)["emergency_timer"] == "2026-09-10 13:00:00"
    assert update(capsys, txn, "--set", "emergency-timer=none")[0] == 0 and "emergency_timer" not in record(txn)
    # with an instance noted, the record cannot say that nothing was created
    assert update(capsys, txn, "--set", "created=no", "--set", "note=changed my mind")[0] == ctl.EXIT_ERR
    assert record(txn)["created"] is True


def test_notes_are_kept_with_their_times(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    assert update(capsys, txn, "--set", "note=first")[0] == 0
    clock.advance(7)
    assert update(capsys, txn, "--set", "note=second")[0] == 0
    assert record(txn)["notes"] == [{"at": MID, "text": "first"}, {"at": MID + 7, "text": "second"}]


# ---- Task 11.4: the three clone options of the budget check ----
def cl_check(capsys, *extra, mode="gpu", price="0.98", gpus=1, hours="1", iid=ID):
    return rc_json(capsys, "auth", "check", "--instance", iid, "--mode", mode, "--price", price, "--gpus", gpus,
                   "--hours", hours, *extra)


def release(capsys, req, iid=ID):
    return rc_json(capsys, "auth", "release", "--instance", iid, "--req", req)


def test_clone_host_needs_a_record_at_reserve_that_allows_the_host(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    rc, res = cl_check(capsys, "--clone-host", "ffff000000")   # no clone record at all
    assert rc == ctl.EXIT_ERR and "clone record" in res["reason"] and book() == []
    txn = started(capsys, clock, tmp_path)
    assert cl_check(capsys, "--clone-host", "ffff000000")[0] == ctl.EXIT_ERR   # opened is not reserve yet
    advance(capsys, txn, "reserve")
    for extra in (["--clone-host", "dddd222222"], ["--clone-host", "nope"], ["--clone-host", "ffff000000", "--probe"]):
        assert cl_check(capsys, *extra)[0] == ctl.EXIT_ERR, extra
    assert cl_check(capsys, "--clone-host", "ffff000000", mode="nogpu", price="0.10", gpus=0)[0] == ctl.EXIT_ERR
    assert book() == []
    rc, res = cl_check(capsys, "--clone-host", "ffff000000")
    assert rc == 0 and res["clone_host"] == "ffff000000", res
    r = book()[-1]
    assert r["kind"] == "reserve" and r["req"] == res["req"] and r["clone_host"] == "ffff000000" and "daily_fen" not in r
    # a plain check next to it is an ordinary reservation, as before
    rc, plain = check(capsys)
    assert rc == 0 and "clone_host" not in book()[-1] and "clone_host" not in plain


def test_a_second_clone_reservation_is_refused(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, "reserve")
    rc, first = cl_check(capsys, "--clone-host", "ffff000000")
    assert rc == 0
    n = len(book())
    rc, res = cl_check(capsys, "--clone-host", "eeee111111")
    assert rc == ctl.EXIT_ERR and first["req"] in res["reason"] and len(book()) == n
    assert release(capsys, first["req"])[0] == 0
    assert cl_check(capsys, "--clone-host", "eeee111111")[0] == 0


def test_daily_goes_only_with_clone_host(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, "reserve")
    for extra in (["--daily", "0.03"], ["--clone-host", "ffff000000", "--daily", "0"],
                  ["--clone-host", "ffff000000", "--daily", "0.035"], ["--clone-host", "ffff000000", "--daily", "-1"]):
        assert cl_check(capsys, *extra)[0] == ctl.EXIT_ERR, extra
    assert book() == []
    rc, res = cl_check(capsys, "--clone-host", "ffff000000", "--daily", "0.03")
    assert rc == 0 and book()[-1]["daily_fen"] == 3 and book()[-1]["clone_host"] == "ffff000000"


def test_an_open_clone_reservation_brings_its_daily_fee(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)   # Sep 10, noon
    advance(capsys, txn, "reserve")
    rc, res = cl_check(capsys, "--clone-host", "ffff000000", "--daily", "0.03")
    assert rc == 0
    assert show(capsys)["spent_fen"] == 98           # the hour reserved, and no charge time has passed yet
    clock.set(eod(10))
    assert show(capsys)["spent_fen"] == 98 + 3       # the new instance's disk was charged for the first time
    clock.set(eod(11) + 10)
    assert show(capsys)["spent_fen"] == 98 + 6
    assert release(capsys, res["req"])[0] == 0       # nothing was created after all: none of it counts
    assert show(capsys)["spent_fen"] == 0


def test_a_check_with_daily_needs_the_fees_inside_its_window(capsys, clock, tmp_path):
    txn = started(capsys, clock, tmp_path)
    advance(capsys, txn, "reserve")
    clock.set(bj(2026, 9, 10, 23, 30))
    rc, res = cl_check(capsys, "--clone-host", "ffff000000", "--daily", "0.03")   # an hour that crosses 23:59:59
    assert rc == 0 and res["periods"][0]["need_fen"] == 49 + 49 + 3, res
    assert release(capsys, res["req"])[0] == 0
    rc, res = cl_check(capsys, "--clone-host", "ffff000000", "--daily", "0.03", hours="0.25")   # one that does not
    assert rc == 0 and res["periods"][0]["need_fen"] == 25, res


def test_clone_prep_lets_a_gpu_only_grant_start_without_gpus_during_a_clone(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys, usage="gpu")[0] == 0
    nogpu = dict(mode="nogpu", price="0.10", gpus=0, hours="0.2")
    assert cl_check(capsys, **nogpu)[0] == ctl.EXIT_NO_GRANT
    assert cl_check(capsys, "--clone-prep", **nogpu)[0] == ctl.EXIT_NO_GRANT     # cloning is not even enabled
    assert clone_cmd(capsys, "--enable")[0] == 0
    assert cl_check(capsys, "--clone-prep", **nogpu)[0] == ctl.EXIT_NO_GRANT     # enabled, but no clone is under way
    rc, res = open_rec(capsys, tmp_path)
    assert rc == 0
    assert cl_check(capsys, **nogpu)[0] == ctl.EXIT_NO_GRANT                     # without the option: as ever
    assert cl_check(capsys, "--clone-prep")[0] == ctl.EXIT_ERR                   # it is for a start without GPUs
    assert cl_check(capsys, "--clone-prep", "--probe", **nogpu)[0] == ctl.EXIT_ERR
    assert book() == []
    rc, got = cl_check(capsys, "--clone-prep", **nogpu)
    assert rc == 0 and book()[-1]["mode"] == "nogpu" and "clone_host" not in book()[-1], got
    assert release(capsys, got["req"])[0] == 0
    assert rec_cmd(capsys, "close", "--txn", res["txn"])[0] == 0
    assert cl_check(capsys, "--clone-prep", **nogpu)[0] == ctl.EXIT_NO_GRANT     # the clone is over: no more


def test_clone_prep_changes_nothing_for_a_grant_that_allows_the_mode(capsys, clock):
    clock.set(MID)
    assert grant(capsys, usage="both")[0] == 0
    assert cl_check(capsys, "--clone-prep", mode="nogpu", price="0.10", gpus=0, hours="0.2")[0] == 0
    assert grant(capsys, usage="nogpu", iid=NEW)[0] == 0   # no GPU use at all: nothing lets a GPU start through
    assert cl_check(capsys, "--clone-prep", iid=NEW)[0] == ctl.EXIT_ERR
    assert cl_check(capsys, iid=NEW)[0] == ctl.EXIT_NO_GRANT


# ---- Task 11.5: the budget group, auth inherit, auth released, auth release across the group ----
def inherit(capsys, req, *extra, src=ID, to=NEW):
    return rc_json(capsys, "auth", "inherit", "--from", src, "--to", to, "--req", req, *extra)


def clicked(capsys, clock, tmp_path, daily="0.03", upto="clicked", instance=NEW, **grant_kw) -> tuple:
    """A clone up to the click, made with the commands themselves: (transaction ID, the reservation's request ID).
    With upto="reserve" the record stays at reserve, with all that was read for the creation set in it; with
    instance=None the new instance is not noted."""
    txn = started(capsys, clock, tmp_path, **grant_kw)
    advance(capsys, txn, "reserve")
    rc, res = cl_check(capsys, "--clone-host", "ffff000000", *(["--daily", daily] if daily else []))
    assert rc == 0, res
    req = res["req"]
    sets = ["host=ffff000000", "gpus=1", "price=0.98", f"expand-gb={5 if daily else 0}", f"req={req}", f"t0={MID + 30}",
            f"before={DIGESTS}"] + ([f"daily={daily}"] if daily else [])
    if upto == "reserve":
        assert update(capsys, txn, *[w for s in sets for w in ("--set", s)])[0] == 0
        return txn, req
    assert update(capsys, txn, "--stage", "click", *[w for s in sets for w in ("--set", s)])[0] == 0
    assert update(capsys, txn, "--stage", "clicked", "--set", "answer=created", "--set", "created=yes",
                  *(["--set", f"instance={instance}"] if instance else []))[0] == 0
    return txn, req


def raw() -> bytes:
    return (gpu_home() / "store.json").read_bytes()


def test_inherit_copies_the_grant_and_hands_over_the_reservation(capsys, clock, tmp_path):
    txn, req = clicked(capsys, clock, tmp_path)
    assert rc_json(capsys, "auth", "approve", "--instance", ID, "--quote", "yes, go on this month")[0] == 0
    reserved = book()[-1]
    clock.advance(600)
    rc, res = inherit(capsys, req, "--daily", "0.03", "--alias", "autodl-test-c")
    assert rc == 0 and res["inherited"] is True, res
    d = stored()
    old, new = d["grants"][ID], d["grants"][NEW]
    for k in ("usage", "budget", "period", "period_tz", "quote", "approvals"):
        assert new[k] == old[k], k
    assert new["approvals"] == {"2026-09": {"quote": "yes, go on this month", "at": MID}}
    assert new["alias"] == "autodl-test-c" and new["at"] == MID + 600 and new["charges_read"] == MID + 600
    assert d["ledger"][ID][-1] == {"kind": "release", "key": f"release:{req}", "req": req, "at": MID + 600}
    assert d["ledger"][NEW] == [{k: v for k, v in reserved.items() if k != "daily_fen"}, daily_rec(MID, 3)]
    assert d["clones"]["groups"][ID]["members"] == [{"instance": ID, "at": MID},
                                                     {"instance": NEW, "at": MID + 600, "from": ID, "txn": txn}]


def test_inherit_keeps_the_alias_and_the_start_of_a_budget_without_periods(capsys, clock, tmp_path):
    txn, req = clicked(capsys, clock, tmp_path, daily=None, period="none")
    assert inherit(capsys, req)[0] == 0
    d = stored()
    assert d["grants"][NEW]["since"] == d["grants"][ID]["since"] == MID and "charges_read" not in d["grants"][NEW]
    assert d["grants"][NEW]["alias"] == d["grants"][ID]["alias"]
    assert d["ledger"][NEW] == [dict(reserve_rec(req, MID), clone_host="ffff000000")]   # no daily fee: no daily record


def test_inherit_again_changes_nothing(capsys, clock, tmp_path):
    txn, req = clicked(capsys, clock, tmp_path)
    assert inherit(capsys, req, "--daily", "0.03")[0] == 0
    before = raw()
    rc, res = inherit(capsys, req, "--daily", "0.03")
    assert rc == 0 and res["inherited"] is False and raw() == before
    assert log_ev(capsys, "on", tmp_path, MID + 30, req=req, iid=NEW)[0] == 0   # and once the power-on used it
    before = raw()
    assert inherit(capsys, req, "--daily", "0.03")[0] == 0 and raw() == before


@pytest.mark.parametrize("case", ["no-grant", "another-request", "released", "plain-reservation", "not-on-the-host",
                                  "the-source-itself", "another-daily-fee", "no-daily-fee-given", "known-already",
                                  "another-instance-noted", "not-at-the-click-yet", "the-request-noted-at-reserve",
                                  "at-the-click-without-an-answer", "no-instance-noted"])
def test_inherit_refuses_what_does_not_fit(capsys, clock, tmp_path, case):
    if case == "not-at-the-click-yet":
        txn = started(capsys, clock, tmp_path)
        advance(capsys, txn, "reserve")
        req = cl_check(capsys, "--clone-host", "ffff000000", "--daily", "0.03")[1]["req"]
    elif case == "the-request-noted-at-reserve":   # the record knows the request, and nothing was clicked yet
        txn, req = clicked(capsys, clock, tmp_path, upto="reserve")
    elif case == "at-the-click-without-an-answer":  # the click may have been made; the platform's answer is not in
        txn, req = clicked(capsys, clock, tmp_path, upto="reserve")
        assert update(capsys, txn, "--stage", "click")[0] == 0 and update(capsys, txn, "--set", f"instance={NEW}")[0] == 0
    elif case == "no-instance-noted":               # which row is the new instance was never written down
        txn, req = clicked(capsys, clock, tmp_path, instance=None)
    else:
        txn, req = clicked(capsys, clock, tmp_path)
    args, to = [req, "--daily", "0.03"], NEW
    if case == "no-grant":
        assert rc_json(capsys, "auth", "revoke", "--instance", ID)[0] == 0
    elif case == "another-request":
        args[0] = "9999888877776666"
    elif case == "released":
        assert release(capsys, req)[0] == 0
    elif case == "plain-reservation":
        args = [check(capsys)[1]["req"]]
    elif case == "not-on-the-host":
        to = "eeee111111-1111eeee"
    elif case == "the-source-itself":
        to = ID
    elif case == "another-daily-fee":
        args[2] = "0.05"
    elif case == "no-daily-fee-given":
        args = [req]
    elif case == "known-already":
        put_records([on_rec("t5-1", MID - 99, unreserved=True), off_rec("t5-1", MID - 9)], iid=NEW)
    elif case == "another-instance-noted":
        to = "ffff000000-1111ffff"
    before = raw()
    rc, res = inherit(capsys, *args, to=to)
    assert rc == ctl.EXIT_ERR and res["ok"] is False and res["reason"], (case, res)   # refused with its reason
    assert raw() == before


def test_log_on_uses_the_inherited_reservation(capsys, clock, tmp_path):
    txn, req = clicked(capsys, clock, tmp_path)
    assert inherit(capsys, req, "--daily", "0.03")[0] == 0
    rc, res = log_ev(capsys, "on", tmp_path, MID + 30, req=req, iid=NEW)
    assert rc == 0 and res["ledger"] == "recorded" and res["session"] == req, res
    assert update(capsys, txn, "--stage", "adopted")[0] == 0   # the commands themselves satisfy the stage
    s = show(capsys, iid=NEW)
    assert s["open_session"]["session"] == req and s["open_reservations"] == []


def test_a_group_is_judged_by_the_sum_of_its_ledgers(capsys, clock, tmp_path):
    txn, req = clicked(capsys, clock, tmp_path, daily=None, budget="2yuan")
    assert inherit(capsys, req)[0] == 0
    assert log_ev(capsys, "on", tmp_path, MID + 30, req=req, iid=NEW)[0] == 0
    clock.advance(630)   # the new instance has run ten minutes: 17 fen
    assert show(capsys, iid=NEW)["spent_fen"] == 17 and show(capsys)["spent_fen"] == 0
    assert show(capsys)["group_spent_fen"] == 17 and show(capsys, iid=NEW)["group_spent_fen"] == 17
    assert show(capsys)["remaining_fen"] == 200 - 17 == show(capsys, iid=NEW)["remaining_fen"]
    # an hour on the source from 12:10:30 on crosses 13:00, so it is 81 + 18 = 99 fen: 17 + 99 of the 200
    rc, res = check(capsys, hours="1")
    assert rc == 0 and res["periods"][0]["spent_fen"] == 17 and res["periods"][0]["need_fen"] == 99, res
    rc, res = check(capsys, hours="1", iid=NEW)      # and now an hour more on the new one would be 17 + 99 + 99
    assert rc == ctl.EXIT_BUDGET and res["spent_fen"] == 17 + 99, res
    rc, res = rc_json(capsys, "auth", "check", "--probe", "--instance", NEW, "--mode", "gpu", "--price", "0.98",
                      "--gpus", 1, "--hours", "0.4")   # the probe sees the same sum: 116, and 40 more fit
    assert rc == 0 and res["periods"][0]["spent_fen"] == 116 and res["periods"][0]["need_fen"] == 40, res


def test_the_daily_fee_passes_to_the_new_ledger_without_a_gap_or_a_double_count(capsys, clock, tmp_path):
    txn, req = clicked(capsys, clock, tmp_path)
    clock.set(eod(10) + 1)
    assert show(capsys)["spent_fen"] == 98 + 3      # the reservation carries the first charge
    assert inherit(capsys, req, "--daily", "0.03")[0] == 0
    assert show(capsys)["spent_fen"] == 0 and show(capsys, iid=NEW)["spent_fen"] == 98 + 3
    assert show(capsys)["group_spent_fen"] == 98 + 3 and show(capsys, iid=NEW)["daily_fen"] == 3
    clock.set(eod(11) + 1)
    assert show(capsys)["group_spent_fen"] == 98 + 6


def test_release_finds_the_reservation_in_the_group(capsys, clock, tmp_path):
    txn, req = clicked(capsys, clock, tmp_path, daily=None)
    assert inherit(capsys, req)[0] == 0
    rc, res = release(capsys, req)   # asked with the source's ID: it is open in the new instance's ledger
    assert rc == 0 and res["instance"] == NEW and res["released"] == req, res
    assert stored()["ledger"][NEW][-1]["kind"] == "release" and show(capsys)["group_spent_fen"] == 0
    rc, res = release(capsys, req, iid=NEW)
    assert rc == 0 and "before" in res["note"]
    assert release(capsys, "9999888877776666")[0] == ctl.EXIT_ERR


def test_close_waits_for_the_inherited_reservation(capsys, clock, tmp_path):
    """After inherit the clone's reservation is open in the new instance's ledger until the power-on uses it. A close
    that looked at the source's ledger only would leave it behind, counted against the budget with no clone open."""
    txn, req = clicked(capsys, clock, tmp_path)
    assert inherit(capsys, req, "--daily", "0.03")[0] == 0
    assert update(capsys, txn, "--stage", "adopted")[0] == 0
    edit_store(lambda d: d["clones"]["records"][txn].update(source_ticket="cleared"))
    before = raw()
    rc, res = close(capsys, txn, "--note", "the new instance never came up; both are kept")
    assert rc == ctl.EXIT_ERR and req in res["reason"] and NEW in res["reason"], res
    assert raw() == before
    assert log_ev(capsys, "on", tmp_path, MID + 30, req=req, iid=NEW)[0] == 0
    rc, res = close(capsys, txn, "--note", "the new instance never came up; both are kept")
    assert rc == 0 and res["outcome"] == "not-switched", res


def test_released_stops_the_daily_fee_and_drops_the_grant(capsys, clock, tmp_path):
    txn, req = clicked(capsys, clock, tmp_path)
    assert daily_cmd(capsys)[0] == 0 and inherit(capsys, req, "--daily", "0.03")[0] == 0
    gone = bj(2026, 9, 12, 9)
    clock.set(gone + 60)
    rc, res = rc_json(capsys, "auth", "released", "--instance", ID, "--at", gone)
    assert rc == 0, res
    d = stored()
    assert ID not in d["grants"] and NEW in d["grants"]
    assert d["ledger"][ID][-1] == daily_rec(gone, 0)
    assert d["clones"]["groups"][ID]["members"][0] == {"instance": ID, "at": MID, "released_at": gone}
    spent = show(capsys)["spent_fen"]
    assert spent == 11 * 3            # Sep 1 to Sep 11
    clock.set(eod(20))
    assert show(capsys)["spent_fen"] == spent and show(capsys)["grant"] is None
    assert show(capsys, iid=NEW)["clone"]["members"][0]["released_at"] == gone
    before = raw()                    # again: nothing changes
    assert rc_json(capsys, "auth", "released", "--instance", ID, "--at", gone)[0] == 0 and raw() == before


def test_released_refuses_an_open_session_and_a_bad_time(capsys, clock, tmp_path):
    clock.set(MID)
    assert grant(capsys)[0] == 0
    req = check(capsys)[1]["req"]
    assert log_ev(capsys, "on", tmp_path, MID, req=req)[0] == 0
    before = raw()
    assert rc_json(capsys, "auth", "released", "--instance", ID, "--at", MID)[0] == ctl.EXIT_ERR
    assert rc_json(capsys, "auth", "released", "--instance", NEW, "--at", MID)[0] == ctl.EXIT_ERR   # not known here
    assert raw() == before
    clock.advance(100)
    assert log_ev(capsys, "off", tmp_path, MID + 90)[0] == 0
    assert rc_json(capsys, "auth", "released", "--instance", ID, "--at", MID + 9999)[0] == ctl.EXIT_ERR   # ahead of now
    assert rc_json(capsys, "auth", "released", "--instance", ID, "--at", MID + 95)[0] == 0
    assert stored()["clones"]["groups"][ID]["members"] == [{"instance": ID, "at": MID + 100, "released_at": MID + 95}]


def test_auth_show_tells_the_group_and_what_became_of_the_source(capsys, clock, tmp_path):
    txn, req = clicked(capsys, clock, tmp_path)
    assert inherit(capsys, req, "--daily", "0.03")[0] == 0
    assert log_ev(capsys, "on", tmp_path, MID + 30, req=req, iid=NEW)[0] == 0
    a, b = show(capsys)["clone"], show(capsys, iid=NEW)["clone"]
    assert a["group"] == b["group"] == ID and a["members"] == b["members"]
    assert a["members"][1] == {"instance": NEW, "at": MID, "from": ID, "txn": txn, "daily_fen": 3}
    assert a["made"] == b["made"] == 1 and b["enabled"] is True and b["max"] == 2   # the settings are the group's
    assert "note" not in a
    assert update(capsys, txn, "--stage", "adopted")[0] == 0
    advance(capsys, txn, "switched")
    a, b = show(capsys)["clone"], show(capsys, iid=NEW)["clone"]
    assert NEW in a["note"] and "ask the user" in a["note"] and "note" not in b
    edit_store(lambda d: d["clones"]["records"][txn].update(source_ticket="cleared"))
    assert rec_cmd(capsys, "close", "--txn", txn)[0] == 0
    assert NEW in show(capsys)["clone"]["note"] and show(capsys)["clone"]["open_record"] is None
    # the settings can be given through either member
    assert clone_cmd(capsys, "--enable", "--max", "3", iid=NEW)[0] == 0
    assert show(capsys)["clone"]["max"] == 3 and list(stored()["clones"]["groups"]) == [ID]
