from trader import gap_live

OPEN_945 = "2026-10-27T13:45:00Z"    # 9:45 AM New York, a Tuesday
LATE_1145 = "2026-10-27T15:45:00Z"   # 11:45 AM
CLOSE_1535 = "2026-10-27T19:35:00Z"  # 3:35 PM


def on(**kw):
    return {**gap_live.DEFAULT_LEDGER, "enabled": True, **kw}


def state(now=OPEN_945, **quotes):
    q = {"NVDA": {"open": 211.0, "prev_close": 210.0, "mark": 211.0},
         "AMD": {"open": 500.0, "prev_close": 520.0, "mark": 501.0},    # -3.8%: too small
         "PLTR": {"open": 186.0, "prev_close": 200.0, "mark": 187.0},   # -7%
         "QQQ": {"open": 700.0, "prev_close": 704.0, "mark": 701.0}}
    q.update(quotes)
    return {"now": now, "cash": "1.53", "open_order": False, "quotes": q}


def test_off_by_default_places_nothing():
    out = gap_live.plan(dict(gap_live.DEFAULT_LEDGER), state())
    assert out["orders"] == [] and "disabled" in out["notes"][0]


def test_buys_one_dollar_of_the_deepest_5pct_gap():
    (o,) = gap_live.plan(on(), state())["orders"]
    assert (o["symbol"], o["side"], o["dollar_amount"], o["prev_close"]) == ("PLTR", "buy", "1.00", 200.0)


def test_no_new_trade_after_11_or_twice_a_day():
    assert gap_live.plan(on(), state(now=LATE_1145))["orders"] == []
    assert gap_live.plan(on(last_day="2026-10-27"), state())["orders"] == []


def test_sells_only_its_own_shares_at_the_fill_or_end_of_day():
    led = gap_live.record(on(), "buy", "PLTR", "b1", "0.005376", "186.0", OPEN_945, prev_close=200.0)
    assert gap_live.plan(led, state())["orders"] == []                                   # 187 < 200
    (o,) = gap_live.plan(led, state(PLTR={"open": 186.0, "prev_close": 200.0, "mark": 200.5}))["orders"]
    assert (o["side"], o["quantity"]) == ("sell", "0.005376")
    (o,) = gap_live.plan(led, state(now=CLOSE_1535))["orders"]
    assert o["reason"] == "end of day"
    led = gap_live.record(led, "sell", "PLTR", "s1", "0.005376", "190.0", CLOSE_1535)
    assert led["status"] == "flat" and led["trades"][0]["pnl"] == round(0.005376 * 4.0, 4)
