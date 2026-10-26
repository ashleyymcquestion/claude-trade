from trader import chip_test

AFTER = "2026-10-29T00:00:00Z"   # after Oct 28 4:46 PM PT
BEFORE = "2026-10-28T12:00:00Z"


def state(**kw):
    return {"now": AFTER, "cash": "1.53", "bid": 0.055, "ask": 0.056, "quantity": "0",
            "open_order": False, "paper_closed": True, **kw}


def test_waits_for_the_paper_test_to_close():
    led = dict(chip_test.DEFAULT_LEDGER)
    assert chip_test.plan(led, state(now=BEFORE))["orders"] == []
    assert chip_test.plan(led, state(paper_closed=False))["orders"] == []


def test_buys_25_cents_once_when_above_the_paper_start():
    out = chip_test.plan(dict(chip_test.DEFAULT_LEDGER), state())
    (o,) = out["orders"]
    assert (o["symbol"], o["side"], o["type"], o["dollar_amount"]) == ("CHIP-USD", "buy", "market", "0.25")


def test_skips_for_good_when_not_above_the_paper_start():
    out = chip_test.plan(dict(chip_test.DEFAULT_LEDGER), state(bid=0.045))
    assert out["orders"] == [] and out["ledger"]["status"] == "skipped"


def test_no_stop_and_no_trail_never_sells():
    led = chip_test.record({**chip_test.DEFAULT_LEDGER, "trail_pct": None}, "buy", "x", "4.5", "0.0556", AFTER)
    assert led["status"] == "holding" and led["stop"] is None
    assert chip_test.plan(led, state(bid=0.01, quantity="4.5"))["orders"] == []


def test_stop_sells_when_set():
    led = chip_test.record({**chip_test.DEFAULT_LEDGER, "stop_pct": 0.15, "trail_pct": None}, "buy", "x", "4.5", "0.06", AFTER)
    assert led["stop"] == 0.051
    assert chip_test.plan(led, state(bid=0.052, quantity="4.5"))["orders"] == []
    (o,) = chip_test.plan(led, state(bid=0.05, quantity="4.5"))["orders"]
    assert (o["side"], o["quantity"]) == ("sell", "4.5")


def test_trailing_exit_keeps_the_climb_and_sells_25_pct_off_the_peak():
    led = chip_test.record(dict(chip_test.DEFAULT_LEDGER), "buy", "x", "4.5", "0.05", AFTER)
    assert led["peak"] == 0.05 and led["trail_pct"] == 0.25
    out = chip_test.plan(led, state(bid=0.08, quantity="4.5"))      # climbs: new peak, no sell
    assert out["orders"] == [] and out["ledger"]["peak"] == 0.08
    led = out["ledger"]
    assert chip_test.plan(led, state(bid=0.061, quantity="4.5"))["orders"] == []   # 23.75% off
    (o,) = chip_test.plan(led, state(bid=0.06, quantity="4.5"))["orders"]           # 25% off
    assert (o["side"], o["quantity"]) == ("sell", "4.5")
