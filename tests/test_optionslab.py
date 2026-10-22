import math
from types import SimpleNamespace

import pytest

from trader import optionslab as o

DAY1 = "2026-10-21T15:00:00Z"
DAY2 = "2026-10-22T15:00:00Z"
EXP = "2026-11-27"        # 37 days after day 1
SYMS = ("FHN", "NVDA", "QQQ", "SPCX")
STRIKES = (85, 90, 95, 100, 105, 110, 115)


def history(vol=0.20, n=45):
    x = vol / math.sqrt(252)          # alternating +x/-x log returns -> realized vol ~= vol
    closes, c = [], 100.0
    for i in range(n):
        c *= math.exp(x if i % 2 else -x)
        closes.append(c)
    return closes


def watch(sym="FHN", source="options", opened="2026-10-21"):
    return [SimpleNamespace(symbol=sym, source=source, opened=opened)]


def chain(sym):
    return [{"id": f"{sym}-{t}-{k}", "strike": k, "type": t, "expiration": EXP} for t in ("call", "put") for k in STRIKES]


# puts priced far above what 20% realized volatility justifies; calls priced expensive for buyers
RICH_PUTS = {("put", 95): (3.0, 3.1), ("put", 90): (1.0, 1.05), ("put", 85): (0.4, 0.45),
             ("call", 100): (4.8, 5.0), ("call", 105): (2.5, 2.6), ("call", 110): (1.0, 1.05),
             ("call", 115): (0.4, 0.45)}
CHEAP_PUTS = {**RICH_PUTS, ("put", 95): (0.3, 0.35), ("put", 90): (0.1, 0.12)}


def quotes(table):
    return {f"{s}-{t}-{k}": {"bid": table.get((t, k), (0.5, 0.55))[0], "ask": table.get((t, k), (0.5, 0.55))[1]}
            for s in SYMS for t in ("call", "put") for k in STRIKES}


def full(now=DAY1, spot=100.0, table=RICH_PUTS, vol=0.20):
    return {"now": now, "spots": {s: spot for s in SYMS}, "history": {s: history(vol) for s in SYMS},
            "expirations": {s: [EXP, "2026-10-30"] for s in SYMS}, "instruments": {s: chain(s) for s in SYMS},
            "quotes": quotes(table)}


EMPTY = {"last_run": "", "wheel": {}}


@pytest.fixture(autouse=True)
def no_budget(monkeypatch):
    """The $100 test adds its own symbols; the older tests run without it."""
    monkeypatch.setattr(o, "BUDGET_SYMBOLS", ())


def rolled(inp, exp="2027-01-08"):
    """The same chain one expiration later, for runs after the first contracts expired."""
    inp["expirations"] = {s: [exp] for s in SYMS}
    for rows in inp["instruments"].values():
        for r in rows:
            r["expiration"] = exp
    return inp


def test_realized_vol():
    assert o.realized_vol(history(0.30)) == pytest.approx(0.30, rel=0.05)
    assert o.realized_vol([100.0] * 10) is None


def test_asks_for_data_in_order_and_changes_nothing():
    res, pos, st = o.step({"now": DAY1}, [], EMPTY, watch())
    assert res == {"need": {"spots": list(SYMS)}} and pos == [] and st is EMPTY
    inp = {"now": DAY1, "spots": {s: 100.0 for s in SYMS}}
    assert o.step(inp, [], EMPTY, watch())[0]["need"] == {"history": list(SYMS)}
    inp["history"] = {s: history() for s in SYMS}
    assert o.step(inp, [], EMPTY, watch())[0]["need"] == {"expirations": list(SYMS)}
    inp["expirations"] = {s: [EXP] for s in SYMS}
    need = o.step(inp, [], EMPTY, watch())[0]["need"]["instruments"]
    assert {"symbol": "FHN", "expiration": EXP, "type": "call"} in need
    assert {"symbol": "FHN", "expiration": EXP, "type": "put"} in need
    assert {"symbol": "NVDA", "expiration": EXP, "type": "put"} in need
    assert {"symbol": "NVDA", "expiration": EXP, "type": "call"} not in need


def test_picks_the_structure_with_an_edge():
    res, pos, st = o.step(full(), [], EMPTY, watch())
    flow = next(p for p in pos if p["underlying"] == "FHN")
    assert flow["structure"] == "bull_put_spread"           # rich puts: selling them has the edge
    assert [l["action"] for l in flow["legs"]] == ["sell", "buy"]
    assert flow["legs"][0]["entry"] == 3.0 and flow["legs"][1]["entry"] == 1.05   # sell at bid, buy at ask
    assert flow["pop"] >= o.MIN_POP and flow["ev"] > 0
    assert {p["underlying"] for p in pos if p["structure"] == "cash_secured_put"} == {"NVDA", "QQQ", "SPCX"}
    assert st["last_run"] == "2026-10-21"


def test_skips_when_nothing_has_an_edge():
    res, pos, _ = o.step(full(table=CHEAP_PUTS), [], EMPTY, watch())
    assert [p["strategy"] for p in pos] == ["follow_flow/call"]       # only the unfiltered call test
    assert any(s.startswith("follow_flow FHN: no edge") for s in res["done"]["skipped"])


def test_spread_takes_profit_then_expired_spread_settles():
    _, pos, st = o.step(full(), [], EMPTY, watch())
    flow = next(p for p in pos if p["underlying"] == "FHN" and p["strategy"] == "follow_flow")
    q = {f"FHN-put-95": {"bid": 0.2, "ask": 0.25}, f"FHN-put-90": {"bid": 0.05, "ask": 0.1},
         "FHN-call-100": {"bid": 6.0, "ask": 6.2}}
    res, pos2, _ = o.step({"now": DAY2, "spots": {s: 110.0 for s in SYMS}, "quotes": q}, pos, st, [])
    closed = next(p for p in pos2 if p["id"] == flow["id"])
    assert closed["exit_reason"] == "took 50% of max profit"
    assert closed["pnl"] == pytest.approx((-0.25 + 0.05 - flow["net_debit"]) * 100)

    # a spread left open past expiration settles at intrinsic value
    after = "2026-11-30T15:00:00Z"
    res, pos3, _ = o.step(rolled(full(after, spot=92.0)), pos, st, [])
    settled = next(p for p in pos3 if p["id"] == flow["id"])
    assert settled["exit_reason"] == "expired"
    assert settled["pnl"] == pytest.approx((-(95 - 92) + 0 - flow["net_debit"]) * 100)


def test_wheel_assignment_then_covered_call():
    _, pos, st = o.step(full(), [], EMPTY, watch())
    after = "2026-11-30T15:00:00Z"
    res, pos, st = o.step(rolled(full(after, spot=90.0, table={**RICH_PUTS, ("call", 100): (3.0, 3.1)})), pos, st, [])
    assert st["wheel"]["NVDA"]["shares_cost"] == 95
    cc = next(p for p in pos if p["underlying"] == "NVDA" and p["status"] == "open")
    assert cc["structure"] == "covered_call" and cc["legs"][0]["strike"] >= 95 * o.CC_OTM
    assert res["done"]["scoreboard"]["wheel"]["NVDA"]["unrealized_shares_usd"] == pytest.approx(-500.0)


def test_old_single_leg_positions_still_load():
    old = {"id": "a", "strategy": "follow_flow", "underlying": "UAL", "type": "call", "side": "long", "strike": 102.0,
           "expiration": EXP, "instrument_id": "x", "opened": "2026-10-21", "entry": 6.1, "underlying_entry": 101.4,
           "status": "open", "closed": "", "exit": 0.0, "exit_reason": "", "pnl": 0.0, "ret": 0.0}
    p = o._migrate(old)
    assert p["structure"] == "long_call" and p["net_debit"] == 6.1 and p["legs"][0]["action"] == "buy"


def test_runs_once_a_day():
    _, pos, st = o.step(full(), [], EMPTY, watch())
    res, pos2, _ = o.step(full(), pos, st, watch())
    assert res["done"] == {"already_ran": True} and pos2 is pos


def test_raw_call_test_opens_alongside_the_filtered_pick():
    _, pos, _ = o.step(full(), [], EMPTY, watch())
    raw = next(p for p in pos if p["strategy"] == "follow_flow/call")
    assert raw["structure"] == "long_call" and raw["legs"][0]["entry"] == 5.0 and raw["pop"] is not None


def test_pullback_signal_opens_calls_on_a_dip_in_an_uptrend():
    up = [100 * 1.003 ** i for i in range(240)]
    dip = up + [up[-1] * 0.985 ** k for k in range(1, 9)]          # sharp short drop, still above the 200-day
    dip = [x * 100 / dip[-1] for x in dip]                          # end at 100, where the test chain's strikes are
    inp = full()
    inp["long_closes"] = {"NVDA": dip[:-1]}
    inp["spots"] = {**inp["spots"], "NVDA": dip[-1]}
    assert o.pullback_ideas(inp["long_closes"], inp["spots"]) == ["NVDA"]
    _, pos, _ = o.step(inp, [], EMPTY, [])
    assert {p["strategy"] for p in pos if p["underlying"] == "NVDA"} >= {"pullback_calls/call"}


def test_budget100_buys_the_best_spread_under_100(monkeypatch):
    monkeypatch.setattr(o, "BUDGET_SYMBOLS", ("QQQ",))
    monkeypatch.setattr(o, "BUDGET_RULE", "call_spread")
    res, pos, st = o.step(full(), [], EMPTY, [])          # 100C/105C nets ~$2.40: over budget
    assert not [p for p in pos if p["strategy"] == "budget100"]
    assert any(s.startswith("budget100 QQQ") for s in res["done"]["skipped"])
    cheap = {**RICH_PUTS, ("call", 100): (1.0, 1.05), ("call", 105): (0.4, 0.45)}
    res, pos, st = o.step(full(table=cheap), [], EMPTY, [])
    b = [p for p in pos if p["strategy"] == "budget100"]
    assert len(b) == 1 and b[0]["underlying"] == "QQQ"
    assert b[0]["net_debit"] == 0.65                       # buy 100C at the 1.05 ask, sell 105C at the 0.40 bid
    assert b[0]["legs"][0]["action"] == "buy" and b[0]["legs"][1]["action"] == "sell"
    assert "budget100" in res["done"]["scoreboard"]["by_idea"]
