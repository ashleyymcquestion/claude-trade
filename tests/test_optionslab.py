from types import SimpleNamespace

import pytest

from trader import optionslab as o

DAY1 = "2026-10-21T15:00:00Z"
DAY2 = "2026-10-22T15:00:00Z"
EXP = "2026-11-27"        # 37 days after day 1
SPOTS = {"NVDA": 200.0, "QQQ": 700.0, "SPCX": 160.0, "FHN": 23.0}


def watch(sym="FHN", source="options", opened="2026-10-21"):
    return [SimpleNamespace(symbol=sym, source=source, opened=opened)]


def chain(sym, spot, typ):
    return [{"id": f"{sym}-{typ}-{k}", "strike": k, "type": typ, "expiration": EXP}
            for k in (spot * 0.9, spot * 0.95, spot, spot * 1.05, spot * 1.1)]


def full_input(now=DAY1, spots=SPOTS, quotes=None, typ=None):
    insts = {s: chain(s, p, (typ or {}).get(s, "put" if s in o.WHEEL else "call")) for s, p in SPOTS.items()}
    q = quotes or {i["id"]: {"bid": 1.0, "ask": 1.1} for rows in insts.values() for i in rows}
    return {"now": now, "spots": spots, "expirations": {s: [EXP, "2026-10-30"] for s in SPOTS},
            "instruments": insts, "quotes": q}


def test_asks_for_data_in_order_and_changes_nothing():
    res, pos, st = o.step({"now": DAY1}, [], {"last_run": "", "wheel": {}}, watch())
    assert res == {"need": {"spots": ["FHN", "NVDA", "QQQ", "SPCX"]}} and pos == []
    res, *_ = o.step({"now": DAY1, "spots": SPOTS}, [], {"last_run": "", "wheel": {}}, watch())
    assert res["need"]["expirations"] == ["FHN", "NVDA", "QQQ", "SPCX"]
    res, *_ = o.step({"now": DAY1, "spots": SPOTS, "expirations": {s: [EXP] for s in SPOTS}}, [],
                     {"last_run": "", "wheel": {}}, watch())
    assert {"symbol": "FHN", "expiration": EXP, "type": "call"} in res["need"]["instruments"]


def test_opens_atm_call_at_ask_and_otm_puts_at_bid():
    res, pos, st = o.step(full_input(), [], {"last_run": "", "wheel": {}}, watch())
    by = {p["underlying"]: p for p in pos}
    assert by["FHN"]["strike"] == 23.0 and by["FHN"]["entry"] == 1.1 and by["FHN"]["side"] == "long"
    assert by["NVDA"]["type"] == "put" and by["NVDA"]["strike"] == pytest.approx(190.0) and by["NVDA"]["entry"] == 1.0
    assert st["last_run"] == "2026-10-21"


def test_skips_wide_spreads():
    inp = full_input()
    inp["quotes"] = {k: {"bid": 0.1, "ask": 1.0} for k in inp["quotes"]}
    res, pos, _ = o.step(inp, [], {"last_run": "", "wheel": {}}, watch())
    assert pos == [] and len(res["done"]["skipped"]) == 4


def test_long_call_target_and_put_assignment_then_covered_call():
    _, pos, st = o.step(full_input(), [], {"last_run": "", "wheel": {}}, watch())
    call = next(p for p in pos if p["underlying"] == "FHN")
    q2 = {call["instrument_id"]: {"bid": 2.5, "ask": 2.6}}
    _, pos, st = o.step({"now": DAY2, "spots": SPOTS, "quotes": q2}, pos, st, [])
    assert call["status"] == "open"                      # the arguments were not modified
    call = next(p for p in pos if p["underlying"] == "FHN")
    assert call["exit_reason"] == "target" and call["pnl"] == pytest.approx(140.0)

    # NVDA falls below the 190 put strike; after expiration it is assigned and a covered call follows
    after = "2026-11-30T15:00:00Z"
    low = {**SPOTS, "NVDA": 180.0}
    inp = full_input(after, low, typ={"NVDA": "call", "QQQ": "put", "SPCX": "put"})
    inp["instruments"]["NVDA"] = chain("NVDA", 200.0, "call")
    inp["expirations"] = {s: ["2027-01-08"] for s in SPOTS}
    for rows in inp["instruments"].values():
        for r in rows:
            r["expiration"] = "2027-01-08"
    inp["quotes"] = {i["id"]: {"bid": 1.0, "ask": 1.1} for rows in inp["instruments"].values() for i in rows}
    res, pos, st = o.step(inp, pos, st, [])
    assert st["wheel"]["NVDA"]["shares_cost"] == pytest.approx(190.0)
    cc = next(p for p in pos if p["underlying"] == "NVDA" and p["status"] == "open")
    assert cc["type"] == "call" and cc["strike"] >= 190.0 * 1.05
    assert res["done"]["scoreboard"]["wheel"]["NVDA"]["unrealized_shares_usd"] == pytest.approx(-1000.0)


def test_runs_once_a_day():
    _, pos, st = o.step(full_input(), [], {"last_run": "", "wheel": {}}, watch())
    res, pos2, _ = o.step(full_input(), pos, st, watch())
    assert res["done"] == {"already_ran": True} and pos2 is pos
