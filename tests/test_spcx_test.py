import pytest

from trader.spcx_test import DEFAULT_LEDGER, plan, record

OPEN = "2026-10-21T17:40:00Z"     # Wednesday 1:40 PM ET
CLOSED = "2026-10-21T21:00:00Z"   # 5 PM ET


def state(**kw):
    return {"now": OPEN, "cash": "1.20", "mark": 160.0, "quantity": "0", "open_order": False, **kw}


def test_buys_once_cash_covers_amount():
    out = plan(dict(DEFAULT_LEDGER), state())
    [o] = out["orders"]
    assert (o["symbol"], o["side"], o["dollar_amount"], o["market_hours"]) == ("SPCX", "buy", "1.00", "regular_hours")


@pytest.mark.parametrize("kw", [{"cash": "0.99"}, {"now": CLOSED}, {"open_order": True}, {"quantity": "0.01"}])
def test_no_buy_when_blocked(kw):
    assert plan(dict(DEFAULT_LEDGER), state(**kw))["orders"] == []


def test_no_buy_when_disabled():
    assert plan({**DEFAULT_LEDGER, "enabled": False}, state())["orders"] == []


def test_stop_sells_whole_position_then_done():
    held = record(dict(DEFAULT_LEDGER), "buy", "b1", "0.00625", "160.00", OPEN)
    assert held["status"] == "holding" and held["stop"] == pytest.approx(147.2)
    assert plan(held, state(quantity="0.00625", mark=150.0))["orders"] == []
    [o] = plan(held, state(quantity="0.00625", mark=147.0))["orders"]
    assert (o["side"], o["quantity"]) == ("sell", "0.00625")
    done = record(held, "sell", "s1", "0.00625", "147.00", OPEN)
    assert done["status"] == "done"
    assert plan(done, state(cash="5.00"))["orders"] == []


def test_holding_but_nothing_held_does_not_trade():
    held = record(dict(DEFAULT_LEDGER), "buy", "b1", "0.00625", "160.00", OPEN)
    assert plan(held, state(quantity="0", mark=100.0))["orders"] == []


def test_record_rejects_out_of_order():
    with pytest.raises(ValueError):
        record(dict(DEFAULT_LEDGER), "sell", "s1", "1", "1", OPEN)


def test_profit_only_ledger_holds_through_drops_and_sells_at_target():
    ledger = {**DEFAULT_LEDGER, "stop_pct": None, "target_pct": 0.08}
    held = record(ledger, "buy", "b1", "0.00625", "160.00", OPEN)
    assert held["stop"] is None and held["target"] == pytest.approx(172.8)
    assert plan(held, state(quantity="0.00625", mark=100.0))["orders"] == []      # no stop
    assert plan(held, state(quantity="0.00625", mark=172.0))["orders"] == []
    [o] = plan(held, state(quantity="0.00625", mark=173.0))["orders"]
    assert o["side"] == "sell" and o["reason"].startswith("target hit")


def test_trailing_exit_arms_at_target_and_keeps_the_gain():
    ledger = {**DEFAULT_LEDGER, "stop_pct": None, "target_pct": 0.05, "trail_pct": 0.03}
    held = record(ledger, "buy", "b1", "0.00625", "100.00", OPEN)
    q = "0.00625"
    assert held["target"] == pytest.approx(105.0)
    out = plan(held, state(quantity=q, mark=104.0))              # not armed yet
    assert out["orders"] == [] and "ledger" not in out
    out = plan(held, state(quantity=q, mark=106.0))              # armed: peak 106, exit at 105 (target floor)
    assert out["orders"] == [] and out["ledger"]["peak"] == 106.0
    held = out["ledger"]
    out = plan(held, state(quantity=q, mark=112.0))              # higher: exit moves to 112 x 0.97 = 108.64
    assert out["orders"] == [] and out["ledger"]["peak"] == 112.0
    held = out["ledger"]
    assert plan(held, state(quantity=q, mark=109.0))["orders"] == []
    [o] = plan(held, state(quantity=q, mark=108.5))["orders"]
    assert o["side"] == "sell" and o["reason"].startswith("trailing exit")


def test_trailing_exit_never_sells_below_the_target_floor():
    ledger = {**DEFAULT_LEDGER, "stop_pct": None, "target_pct": 0.05, "trail_pct": 0.03}
    held = {**record(ledger, "buy", "b1", "0.00625", "100.00", OPEN), "peak": 106.0}
    assert plan(held, state(quantity="0.00625", mark=105.5))["orders"] == []
    [o] = plan(held, state(quantity="0.00625", mark=105.0))["orders"]
    assert o["side"] == "sell"
