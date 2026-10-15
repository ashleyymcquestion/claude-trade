"""run_cycle with SimulatedBroker, a tmp Store, a fake bar source and toy strategies.
All bars here are synthetic test fixtures; nothing is written to data/bars/."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from quantbot.broker.base import BrokerError
from quantbot.broker.sim import SimulatedBroker
from quantbot.config import AppConfig, SymbolConfig
from quantbot.data.sources import BarSource
from quantbot.engine.runner import make_client_order_id, run_cycle
from quantbot.models import AssetClass, OrderRequest, OrderStatus, Side
from quantbot.risk.killswitch import KillSwitch
from quantbot.risk.limits import FLAG_DAILY, RiskManager
from quantbot.store import Store
from quantbot.strategies.base import Strategy, empty_signals

# 01:00 UTC: the 2026-10-14 crypto bar (00:00 UTC) is complete and 25h old (< 36h stale limit)
NOW = datetime(2026, 10, 15, 1, 0, tzinfo=UTC)
LAST_BAR = pd.Timestamp("2026-10-14", tz="UTC")


def make_bars(last=LAST_BAR, n=120, start=100.0, step=0.1, closes=None):
    idx = pd.date_range(end=last, periods=n, freq="D", tz="UTC")
    close = np.asarray(closes, float) if closes is not None else start + step * np.arange(n)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                         "volume": 1000.0}, index=idx)


class FakeSource(BarSource):
    def __init__(self, bars: dict):
        self.bars = dict(bars)
        self.calls = 0

    def get_bars(self, sym, start, end=None):
        self.calls += 1
        b = self.bars[sym.symbol]
        if isinstance(b, Exception):
            raise b
        if b is None:
            return None
        out = b[b.index >= pd.Timestamp(start)]
        return out[out.index <= pd.Timestamp(end)] if end is not None else out


class Toy(Strategy):
    """Signals only on the LAST row, controlled by attributes."""

    def __init__(self, name="toy", entry=True, exit=False, stop=5.0, trail=float("nan"), max_hold=float("nan"),
                 warmup=20):
        self.name, self.entry, self.exit, self.stop = name, entry, exit, stop
        self.trail, self.max_hold, self._warm = trail, max_hold, warmup

    @property
    def warmup_bars(self):
        return self._warm

    def signals(self, bars):
        out = empty_signals(bars.index)
        out.iloc[-1, out.columns.get_loc("entry")] = bool(self.entry)
        out.iloc[-1, out.columns.get_loc("exit")] = bool(self.exit)
        out["stop_distance"] = self.stop
        out["trail_distance"] = self.trail
        out["max_hold_bars"] = self.max_hold
        return out


class FakeAlerter:
    def __init__(self, store=None):
        self.sent = []
        self.store = store

    def send(self, level, kind, message, **fields):
        self.sent.append((level, kind, message))
        if self.store is not None:
            self.store.record_event(level, kind, message, **fields)
        return True

    def kinds(self):
        return [k for _, k, _ in self.sent]


def cfg_for(*symbols, **kw):
    syms = [SymbolConfig(symbol=s, asset_class=AssetClass.CRYPTO if "/" in s else AssetClass.STOCK)
            for s in symbols]
    return AppConfig(symbols=tuple(syms), initial_capital=1000.0, **kw)


@pytest.fixture(autouse=True)
def _kill_file(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTBOT_KILL_FILE", str(tmp_path / "KILL"))


class Env:
    def __init__(self, tmp_path, symbols=("BTC/USD",), bars=None, strategies=None, cash=1000.0):
        self.path = tmp_path / "q.db"
        self.cfg = cfg_for(*symbols)
        self.store = Store(self.path)
        self.ks = KillSwitch(self.store)
        self.risk = RiskManager(self.cfg.risk, self.store, self.ks, initial_capital=1000.0)
        self.broker = SimulatedBroker(cash, self.cfg.costs)
        self.broker.set_time(NOW)
        self.source = FakeSource(bars or {s: make_bars() for s in symbols})
        self.strategies = strategies or [Toy()]
        self.alerter = FakeAlerter(self.store)
        self.sync_prices()

    def sync_prices(self):
        for sym, b in self.source.bars.items():
            if isinstance(b, pd.DataFrame) and not b.empty:
                self.broker.set_price(sym, float(b["close"].iloc[-1]))

    def run(self, now=NOW):
        self.broker.set_time(now)
        return run_cycle(self.cfg, self.broker, self.store, self.source, self.strategies, self.risk, now,
                         alerter=self.alerter)


def test_client_order_id_is_deterministic_and_sanitized():
    cid = make_client_order_id("trend", "BTC/USD", LAST_BAR.to_pydatetime(), Side.BUY)
    assert cid == "trend-BTCUSD-20261014-buy"
    long = make_client_order_id("x" * 60, "BTC/USD", LAST_BAR.to_pydatetime(), "sell")
    assert len(long) <= 48 and long.endswith("-20261014-sell")
    assert long == make_client_order_id("x" * 60, "BTC/USD", LAST_BAR.to_pydatetime(), "sell")


def test_entry_submits_once_and_sets_position_meta(tmp_path):
    env = Env(tmp_path)
    rep = env.run()
    cid = "toy-BTCUSD-20261014-buy"
    assert rep.orders_submitted == [cid] and rep.entries == ["BTC/USD"] and not rep.errors
    o = env.store.get_order(cid)
    assert o.status is OrderStatus.FILLED and o.strategy == "toy"
    (fill,) = env.store.fills()
    meta = env.store.get_position_meta("BTC/USD")
    assert meta["strategy"] == "toy"
    assert meta["stop_price"] == pytest.approx(fill.price - 5.0)
    assert env.store.heartbeats()[0]["component"] == "runner"
    assert env.store.heartbeats()[0]["status"] == "ok"
    assert env.store.latest_regimes()["BTC/USD"][1] in ("unknown", "bull_quiet", "bull_volatile")

    # same bar again: nothing new
    calls = env.broker.submit_calls
    rep2 = env.run()
    assert rep2.orders_submitted == [] and env.broker.submit_calls == calls


def test_same_bar_after_flat_is_duplicate_prevented_and_survives_restart(tmp_path):
    env = Env(tmp_path)
    env.run()
    # position closed outside the runner; strategy still says "enter" on the same bar
    env.broker.submit_order(OrderRequest("manual-close", "BTC/USD", Side.SELL,
                                         env.broker.get_account().positions[0].qty))
    calls = env.broker.submit_calls
    rep = env.run()
    assert rep.orders_submitted == [] and env.broker.submit_calls == calls
    assert "already exists" in rep.skipped["BTC/USD"]
    assert env.store.get_position_meta("BTC/USD") is None
    env.store.close()

    # restart: new Store object on the same db, fresh broker object
    env2 = Env(tmp_path)
    rep = env2.run()
    assert rep.orders_submitted == [] and env2.broker.submit_calls == 0
    assert any(e["kind"] == "duplicate_prevented" for e in env2.store.events())


def test_stale_data_no_orders_and_alert(tmp_path):
    env = Env(tmp_path, bars={"BTC/USD": make_bars(last=LAST_BAR - pd.Timedelta(days=4))})
    rep = env.run()
    assert rep.orders_submitted == [] and env.broker.submit_calls == 0
    assert "stale" in rep.skipped["BTC/USD"]
    assert ("warning", "data_invalid") in [(lv, k) for lv, k, _ in env.alerter.sent]


def test_invalid_data_skips_symbol_but_others_trade(tmp_path):
    bad = make_bars()
    bad.iloc[-3, bad.columns.get_loc("high")] = 1.0      # high < low
    env = Env(tmp_path, symbols=("BTC/USD", "ETH/USD"), bars={"BTC/USD": bad, "ETH/USD": make_bars()})
    rep = env.run()
    assert "high_below_low" in rep.skipped["BTC/USD"]
    assert rep.entries == ["ETH/USD"]


def test_per_symbol_exception_does_not_stop_others(tmp_path):
    env = Env(tmp_path, symbols=("BTC/USD", "ETH/USD"),
              bars={"BTC/USD": ValueError("feed broke"), "ETH/USD": make_bars()})
    env.source.bars["BTC/USD"] = ValueError("feed broke")
    rep = env.run()
    assert "data fetch failed" in rep.skipped["BTC/USD"]
    assert rep.entries == ["ETH/USD"]
    assert "data_error" in env.alerter.kinds()


def test_kill_switch_blocks_everything(tmp_path):
    env = Env(tmp_path)
    env.run()                                           # holds BTC now
    env.strategies[0].exit = True
    env.ks.engage("test", by="pytest")
    calls = env.broker.submit_calls
    rep = env.run(NOW + timedelta(days=1))
    assert rep.halted and rep.orders_submitted == [] and env.broker.submit_calls == calls
    assert rep.skipped["BTC/USD"] == "kill switch engaged"
    assert "breaker" in env.alerter.kinds()


def test_daily_breaker_blocks_entries_allows_exits(tmp_path):
    env = Env(tmp_path, symbols=("BTC/USD", "ETH/USD"))
    # first cycle: hold only BTC (ETH data unavailable for now)
    env.source.bars["ETH/USD"] = ValueError("not yet")
    env.run()
    assert env.broker.get_account().positions[0].symbol == "BTC/USD"

    ny_date = NOW.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    env.store.set_flag(FLAG_DAILY, ny_date, "test")
    env.source.bars["ETH/USD"] = make_bars()
    env.sync_prices()
    env.strategies[0].exit = True
    rep = env.run()
    assert rep.exits == ["BTC/USD"]
    assert "ETH/USD" not in rep.entries and "daily loss breaker" in rep.skipped["ETH/USD"]
    assert env.broker.get_account().positions == ()
    assert "breaker" in env.alerter.kinds()


def test_stop_hit_exits_and_records_realized_pnl(tmp_path):
    env = Env(tmp_path)
    env.run()
    meta = env.store.get_position_meta("BTC/USD")
    env.strategies[0].entry = False
    # next day closes below the stop
    b = env.source.bars["BTC/USD"]
    crash = meta["stop_price"] - 1.0
    nxt = pd.DataFrame({"open": [crash], "high": [crash * 1.01], "low": [crash * 0.99], "close": [crash],
                        "volume": [1000.0]}, index=[LAST_BAR + pd.Timedelta(days=1)])
    env.source.bars["BTC/USD"] = pd.concat([b, nxt])
    env.sync_prices()
    rep = env.run(NOW + timedelta(days=1))
    assert rep.exits == ["BTC/USD"]
    sell = env.store.get_order("toy-BTCUSD-20261015-sell")
    assert sell.status is OrderStatus.FILLED
    assert any("stop hit" in s.reason for s in rep.signals)
    assert env.store.get_position_meta("BTC/USD") is None
    pnl = env.store.realized_pnl_records()
    assert len(pnl) == 1 and pnl[0]["pnl"] < 0 and pnl[0]["strategy"] == "toy"
    # matches the broker's cash change exactly (fees included)
    assert pnl[0]["pnl"] == pytest.approx(env.broker.cash - 1000.0, abs=1e-6)


def test_trailing_stop_ratchets_up_only(tmp_path):
    env = Env(tmp_path, strategies=[Toy(trail=3.0, stop=10.0)])
    env.run()
    s0 = env.store.get_position_meta("BTC/USD")["stop_price"]
    env.strategies[0].entry = False
    b = env.source.bars["BTC/USD"]
    last = float(b["close"].iloc[-1])
    for i, c in enumerate([last + 5, last + 4], start=1):     # up, then a small dip
        row = pd.DataFrame({"open": [c], "high": [c * 1.01], "low": [c * 0.99], "close": [c], "volume": [1e3]},
                           index=[LAST_BAR + pd.Timedelta(days=i)])
        env.source.bars["BTC/USD"] = pd.concat([env.source.bars["BTC/USD"], row])
        env.sync_prices()
        env.run(NOW + timedelta(days=i))
        meta = env.store.get_position_meta("BTC/USD")
        if i == 1:
            s1 = meta["stop_price"]
            assert s1 == pytest.approx(last + 5 - 3.0) and s1 > s0
            assert meta["highest_close"] == pytest.approx(last + 5)
        else:
            assert meta["stop_price"] == pytest.approx(s1)       # did not move down
    assert env.broker.get_account().positions                   # dip of 1 < trail 3: still held


def test_max_hold_exit(tmp_path):
    env = Env(tmp_path, strategies=[Toy(max_hold=2)])
    env.run()
    env.strategies[0].entry = False
    rep = None
    for i in (1, 2):
        c = float(env.source.bars["BTC/USD"]["close"].iloc[-1]) + 0.1
        row = pd.DataFrame({"open": [c], "high": [c * 1.01], "low": [c * 0.99], "close": [c], "volume": [1e3]},
                           index=[LAST_BAR + pd.Timedelta(days=i)])
        env.source.bars["BTC/USD"] = pd.concat([env.source.bars["BTC/USD"], row])
        env.sync_prices()
        rep = env.run(NOW + timedelta(days=i))
    assert rep.exits == ["BTC/USD"] and "max hold" in rep.signals[-1].reason


def test_partial_fill_reconciled_into_meta_and_fills(tmp_path):
    env = Env(tmp_path)
    env.broker.partial_fill_next(0.5)
    rep = env.run()
    cid = rep.orders_submitted[0]
    o = env.store.get_order(cid)
    assert o.status is OrderStatus.PARTIALLY_FILLED
    assert env.store.get_position_meta("BTC/USD")["strategy"] == "toy"
    assert len(env.store.fills()) == 1 and env.store.fills()[0].qty == pytest.approx(o.qty / 2)

    rep = env.run()                                     # still open: no new order for the symbol
    assert rep.orders_submitted == [] and rep.skipped["BTC/USD"] == "open order outstanding"

    env.broker.fill_open(cid, price=float(env.source.bars["BTC/USD"]["close"].iloc[-1]) + 1)
    rep = env.run()
    assert rep.reconciled == 1
    o = env.store.get_order(cid)
    assert o.status is OrderStatus.FILLED
    fills = env.store.fills()
    assert len(fills) == 2 and sum(f.qty for f in fills) == pytest.approx(o.qty)
    assert sum(f.qty * f.price for f in fills) / o.qty == pytest.approx(o.filled_avg_price)
    rep = env.run()                                     # reconciling again never duplicates fills
    assert len(env.store.fills()) == 2


def test_rejected_order_recorded_and_alerted(tmp_path):
    env = Env(tmp_path)
    env.broker.reject_next("insufficient buying power (sim)")
    rep = env.run()
    cid = "toy-BTCUSD-20261014-buy"
    assert rep.orders_rejected == [cid] and rep.entries == []
    o = env.store.get_order(cid)
    assert o.status is OrderStatus.REJECTED and "insufficient" in o.reject_reason
    assert ("warning", "order_rejected") in [(lv, k) for lv, k, _ in env.alerter.sent]
    assert env.store.get_position_meta("BTC/USD") is None
    assert env.store.get_flag("entry_plan:" + cid) is None


def test_order_timeout_is_cancelled(tmp_path):
    env = Env(tmp_path)
    env.broker.keep_open_next()
    rep = env.run()
    cid = rep.orders_submitted[0]
    assert env.store.get_order(cid).status is OrderStatus.NEW
    rep = env.run(NOW + timedelta(seconds=env.cfg.execution.order_timeout_seconds - 5))
    assert rep.cancelled == []
    rep = env.run(NOW + timedelta(seconds=env.cfg.execution.order_timeout_seconds + 5))
    assert rep.cancelled == [cid]
    assert env.store.get_order(cid).status is OrderStatus.CANCELED
    assert env.broker.get_order(cid).status is OrderStatus.CANCELED
    assert "order_timeout" in env.alerter.kinds()


def test_submit_timeout_after_acceptance_is_reconciled_not_resubmitted(tmp_path):
    env = Env(tmp_path)
    env.broker.fail_next_submit(BrokerError("timeout after acceptance"))
    rep = env.run()
    assert rep.aborted
    cid = "toy-BTCUSD-20261014-buy"
    assert env.store.get_order(cid).status is OrderStatus.PENDING
    calls = env.broker.submit_calls
    rep = env.run()                                     # reconcile finds it by client_order_id
    assert not rep.aborted and env.broker.submit_calls == calls
    assert env.store.get_order(cid).status is OrderStatus.FILLED
    assert env.store.get_position_meta("BTC/USD")["strategy"] == "toy"


def test_broker_failure_aborts_cycle(tmp_path):
    env = Env(tmp_path)

    def down():
        raise BrokerError("connection refused")
    env.broker.get_account = down
    rep = env.run()
    assert rep.aborted and env.broker.submit_calls == 0
    hb = {h["component"]: h for h in env.store.heartbeats()}
    assert hb["runner"]["status"] == "error"
    assert ("critical", "cycle_aborted") in [(lv, k) for lv, k, _ in env.alerter.sent]


def test_closed_stock_market_is_skipped(tmp_path):
    env = Env(tmp_path, symbols=("NVDA", "BTC/USD"),
              bars={"NVDA": make_bars(last=pd.Timestamp("2026-10-14 04:00", tz="UTC")), "BTC/USD": make_bars()})
    env.broker.set_market_open(AssetClass.STOCK, False)
    rep = env.run()
    assert rep.skipped["NVDA"] == "stock market closed"
    assert rep.entries == ["BTC/USD"]


def test_smoke_with_real_strategies(tmp_path):
    from quantbot.config import StrategiesConfig
    from quantbot.strategies import build_strategies
    rng = np.random.default_rng(7)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 400)))
    env = Env(tmp_path, symbols=("BTC/USD", "ETH/USD"),
              bars={"BTC/USD": make_bars(n=400, closes=closes), "ETH/USD": make_bars(n=400, closes=closes[::-1])},
              strategies=build_strategies(StrategiesConfig()))
    rep = env.run()
    assert not rep.aborted and rep.errors == []
    assert set(rep.symbols_checked) == {"BTC/USD", "ETH/USD"}
    rep2 = env.run()
    assert rep2.orders_submitted == []
