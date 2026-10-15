import json

import numpy as np
import pandas as pd
import pytest

from quantbot.config import load_config
from quantbot.data.robinhood import historicals_to_frames, merge_into_csv
from quantbot.robinhood_stocks import nyse_open, pending, plan, record, ref_id_for
from quantbot.store import Store
from quantbot.risk.killswitch import KillSwitch

SYMS = ["NVDA", "AMD", "PLTR", "QQQ"]


def _write_bars(bars_dir, end="2026-10-14", n=400, crash_last=False):
    """Synthetic, steadily rising daily bars (test-only data, written to tmp_path)."""
    idx = pd.bdate_range(end=end, periods=n, tz="UTC")
    rng = np.random.default_rng(7)
    for k, sym in enumerate(SYMS):
        close = 100 * np.exp(np.cumsum(0.002 + 0.004 * rng.standard_normal(n))) * (1 + k)
        if crash_last:
            close[-1] = close[-2] * 0.80
        df = pd.DataFrame({"timestamp": idx, "open": close, "high": close * 1.004, "low": close * 0.996,
                           "close": close, "volume": 1e6 * (1 + 0.1 * rng.random(n))})
        df["source"] = "synthetic test data"
        df.to_csv(bars_dir / f"{sym}.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTBOT_KILL_FILE", str(tmp_path / "KILL"))
    bars = tmp_path / "bars"
    bars.mkdir()
    _write_bars(bars)
    base = load_config("configs/robinhood_stocks.toml")
    return base.model_copy(update={
        "data": base.data.model_copy(update={"bars_dir": str(bars)}),
        "execution": base.execution.model_copy(update={"db_path": str(tmp_path / "rh.sqlite3")}),
    })


STATE = {"now": "2026-10-16T13:31:00Z", "account_value": 4.19, "cash": 1.93, "positions": {}}


def test_config_is_valid_and_sized_for_small_account():
    c = load_config("configs/robinhood_stocks.toml")
    assert c.sleeve == 0.5 and c.risk.min_order_risk_cap == 0.04
    assert [s.symbol for s in c.symbols] == SYMS
    assert not c.strategies.breakout.enabled


def test_plan_buys_minimum_within_sleeve_and_dedupes(cfg):
    out = plan(cfg, STATE, cfg.sleeve)
    assert out["sleeve_equity"] == pytest.approx(2.10, abs=0.01)
    buys = [o for o in out["orders"] if o["side"] == "buy"]
    assert buys and all(o["dollar_amount"] == "1.00" and o["type"] == "market" for o in buys)
    assert sum(float(o["dollar_amount"]) for o in buys) <= 1.93          # never more than the cash
    assert all(o["ref_id"] == ref_id_for(o["client_order_id"]) for o in buys)
    # Same bar again (e.g. a retried session): nothing new.
    again = plan(cfg, STATE, cfg.sleeve)
    assert again["orders"] == []
    assert {p["client_order_id"] for p in pending(cfg)} == {o["client_order_id"] for o in buys}


def test_record_fill_sets_stop_then_stop_hit_sells(cfg, tmp_path):
    out = plan(cfg, STATE, cfg.sleeve)
    buy = out["orders"][0]
    sym, cid = buy["symbol"], buy["client_order_id"]
    close = float(pd.read_csv(tmp_path / "bars" / f"{sym}.csv")["close"].iloc[-1])
    qty = round(1.0 / close, 6)
    after = {**STATE, "now": "2026-10-16T13:33:00Z", "cash": 0.93,
             "positions": {sym: {"quantity": str(qty), "average_buy_price": str(close), "mark": close}},
             "order_updates": {cid: {"robinhood_order_id": "rh-1", "state": "filled", "filled_quantity": str(qty),
                                     "average_price": str(close), "updated_at": "2026-10-16T13:31:30Z"}}}
    rec = record(cfg, after, cfg.sleeve)
    assert rec["open_orders"] == [] and rec["positions"][sym]["stop"] < close
    stop = rec["positions"][sym]["stop"]

    # Next trading day the last completed bar closes 20% lower -> below the stop -> sell everything.
    _write_bars(tmp_path / "bars", end="2026-10-16", n=402, crash_last=True)
    crash_close = float(pd.read_csv(tmp_path / "bars" / f"{sym}.csv")["close"].iloc[-1])
    if crash_close > stop:
        pytest.skip("synthetic crash did not reach the stop")
    nxt = {"now": "2026-10-19T13:31:00Z", "account_value": 4.0, "cash": 0.93,
           "positions": {sym: {"quantity": str(qty), "average_buy_price": str(close), "mark": crash_close}}}
    out2 = plan(cfg, nxt, cfg.sleeve)
    sells = [o for o in out2["orders"] if o["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["symbol"] == sym and sells[0]["quantity"] == str(qty)
    assert "stop hit" in sells[0]["reason"]


def test_kill_switch_blocks_all_orders(cfg):
    store = Store(cfg.execution.db_path)
    KillSwitch(store).engage("test", "pytest")
    store.close()
    out = plan(cfg, STATE, cfg.sleeve)
    assert out["orders"] == [] and out["report"]["halted"]


def test_market_closed_places_nothing(cfg):
    assert plan(cfg, {**STATE, "now": "2026-10-17T15:00:00Z"}, cfg.sleeve)["orders"] == []   # Saturday
    assert plan(cfg, {**STATE, "market_open": False}, cfg.sleeve)["orders"] == []


def test_cash_beyond_sleeve_is_not_used(cfg):
    # $100 cash but the account is $10 -> sleeve $5; buys stay within $5.
    out = plan(cfg, {**STATE, "account_value": 10.0, "cash": 100.0}, cfg.sleeve)
    assert sum(float(o["dollar_amount"]) for o in out["orders"]) <= 5.0
    assert out["sleeve_equity"] == 5.0


def test_unknown_order_state_is_an_error(cfg):
    out = plan(cfg, STATE, cfg.sleeve)
    cid = out["orders"][0]["client_order_id"]
    with pytest.raises(ValueError):
        record(cfg, {**STATE, "order_updates": {cid: {"state": "weird"}}}, cfg.sleeve)


def test_nyse_hours():
    from datetime import datetime, UTC
    assert nyse_open(datetime(2026, 10, 16, 13, 31, tzinfo=UTC))        # Fri 09:31 ET
    assert not nyse_open(datetime(2026, 10, 16, 13, 29, tzinfo=UTC))    # before the open
    assert not nyse_open(datetime(2026, 11, 26, 15, 0, tzinfo=UTC))     # Thanksgiving


def test_historicals_merge(tmp_path):
    raw = {"data": {"results": [{"symbol": "NVDA", "interval": "day", "bounds": "regular", "bars": [
        {"begins_at": "2026-10-14T00:00:00Z", "open_price": "1", "high_price": "2", "low_price": "0.5",
         "close_price": "1.5", "volume": 10},
        {"begins_at": "2026-10-15T00:00:00Z", "open_price": "1", "high_price": "2", "low_price": "0.5",
         "close_price": "1.6", "volume": 10, "interpolated": True}]}]}}
    frames = historicals_to_frames(raw, "now")
    assert list(frames["NVDA"]["close"]) == [1.5]
    path = tmp_path / "NVDA.csv"
    merge_into_csv(frames["NVDA"], path)
    raw["data"]["results"][0]["bars"][0]["close_price"] = "1.7"          # a re-fetched bar replaces the old one
    merged = merge_into_csv(historicals_to_frames(raw, "now")["NVDA"], path)
    assert list(merged["close"]) == [1.7]
