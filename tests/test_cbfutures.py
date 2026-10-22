from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from trader import cbfutures as cf

NOW = datetime(2026, 10, 22, 20, 0, tzinfo=timezone.utc)


def history(btc_rate=0.003, eth_rate=0.003, days=260):
    idx = pd.date_range(end="2026-10-20", periods=days, freq="D")
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.01, days)
    return pd.DataFrame({"BTC": 80000 * np.exp(np.cumsum(btc_rate + noise)),
                         "ETH": 2400 * np.exp(np.cumsum(eth_rate + noise))}, index=idx)


def candles(price, day="2026-10-21"):
    t = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
    return {"candles": [{"start": str(t), "close": str(price)}, {"start": str(t + 86400), "close": "1"}]}


def book(btc, eth):
    return {"pricebooks": [{"product_id": "BIP-20DEC30-CDE", "bids": [{"price": str(btc)}], "asks": [{"price": str(btc)}]},
                           {"product_id": "ETP-20DEC30-CDE", "bids": [{"price": str(eth)}], "asks": [{"price": str(eth)}]}]}


def test_target_weight_long_only_and_capped():
    up = history()["BTC"]
    w, info = cf.target_weight(up)
    assert info["signal"] == 1.0 and 0 < w <= cf.CFG["max_lev"]
    down = history(btc_rate=-0.004)["BTC"]
    assert cf.target_weight(down)[0] == 0.0                 # below every average: flat, never short


def test_daily_rebalance_then_mark_to_market():
    h = history()
    ledger = cf.new_ledger()
    last = h.iloc[-1]
    inp = {"now": cf._iso(NOW), "best_bid_ask": book(last.BTC, last.ETH),
           "candles_1d": {"BTC-USDC": candles(last.BTC * 1.01), "ETH-USDC": candles(last.ETH * 1.01)}}
    rep = cf.step(ledger, inp, h)
    assert rep["trades"] and all(t["to"] >= 0 for t in rep["trades"])
    assert "2026-10-21" in str(rep["history"].index[-1])       # today's unfinished candle is dropped
    assert rep["equity"] == pytest.approx(1000 - sum(t["fee"] for t in rep["trades"]), abs=0.01)

    # an hour later: no new day, so no trades; price up 10% -> profit on the contracts, minus funding
    n_btc = ledger["contracts"]["BTC"]
    rep2 = cf.step(ledger, {"now": cf._iso(NOW + timedelta(hours=1)),
                            "best_bid_ask": book(ledger["entry"]["BTC"] * 1.1, ledger["entry"]["ETH"])}, h)
    assert rep2["trades"] == []
    gain = n_btc * 0.01 * ledger["entry"]["BTC"] * 0.1
    assert rep2["equity"] == pytest.approx(rep["equity"] + gain, rel=0.01)


def test_new_day_needs_candles():
    with pytest.raises(ValueError):
        cf.step(cf.new_ledger(), {"now": cf._iso(NOW), "best_bid_ask": book(80000, 2400)}, history())


def test_needs_asks_for_recent_candles_only():
    n = cf.needs(cf.new_ledger(), NOW, history())
    assert n["day_due"] and n["candles_1d"]["start"].startswith("2026-10-17")
