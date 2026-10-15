from datetime import UTC, datetime

import pandas as pd
import pytest

from quantbot.engine.scheduler import RunContext, due_actions, marker_flag, run_loop
from quantbot.models import AssetClass
from quantbot.risk.killswitch import KillSwitch
from quantbot.risk.limits import RiskManager
from quantbot.store import Store

from .test_runner import FakeSource, Toy, cfg_for, make_bars
from quantbot.broker.sim import SimulatedBroker


@pytest.fixture(autouse=True)
def _kill_file(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTBOT_KILL_FILE", str(tmp_path / "KILL"))


# Thursday 2026-10-15; New York is UTC-4 (EDT)
def utc(h, m=0, day=15):
    return datetime(2026, 10, day, h, m, tzinfo=UTC)


def test_crypto_once_per_utc_day_after_0005():
    assert "crypto" not in due_actions(utc(0, 3), {})
    assert "crypto" in due_actions(utc(0, 6), {})
    assert "crypto" not in due_actions(utc(0, 6), {"crypto": "2026-10-15"})
    assert "crypto" in due_actions(utc(0, 6, day=16), {"crypto": "2026-10-15"})


def test_stocks_window_weekday_and_weekend():
    assert "stocks" not in due_actions(utc(13, 34), {})          # 09:34 NY
    assert "stocks" in due_actions(utc(13, 35), {})              # 09:35 NY
    assert "stocks" in due_actions(utc(19, 55), {})              # 15:55 NY
    assert "stocks" not in due_actions(utc(19, 56), {})
    assert "stocks" not in due_actions(utc(14, 0), {"stocks": "2026-10-15"})
    sat = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
    assert "stocks" not in due_actions(sat, {})
    assert "crypto" in due_actions(sat, {})
    with pytest.raises(ValueError):
        due_actions(datetime(2026, 10, 15, 14, 0), {})


class Ctx:
    def __init__(self, tmp_path, symbols=("BTC/USD", "NVDA"), now=None):
        self.cfg = cfg_for(*symbols)
        self.path = tmp_path / "q.db"
        self.now = now or utc(14, 0)
        self.builds = 0

    def build(self):
        self.builds += 1
        store = Store(self.path)
        ks = KillSwitch(store)
        broker = SimulatedBroker(1000.0, self.cfg.costs)
        broker.set_time(self.now)
        bars = {"BTC/USD": make_bars(last=pd.Timestamp("2026-10-14", tz="UTC")),
                "NVDA": make_bars(last=pd.Timestamp("2026-10-14 04:00", tz="UTC"))}
        for s, b in bars.items():
            broker.set_price(s, float(b["close"].iloc[-1]))
        self.broker, self.store = broker, store
        return RunContext(broker, store, FakeSource(bars), [Toy(entry=False)],
                          RiskManager(self.cfg.risk, store, ks, initial_capital=1000.0))


def test_holiday_decided_by_broker_clock_and_marker_not_set(tmp_path):
    c = Ctx(tmp_path, symbols=("NVDA",))
    orig = c.build

    def build():
        ctx = orig()
        ctx.broker.set_market_open(AssetClass.STOCK, False)    # e.g. a market holiday on a weekday
        return ctx
    run_loop(c.cfg, build, 60, max_iterations=1, sleep=lambda s: None, now_fn=lambda: c.now)
    assert c.store.get_flag(marker_flag("stocks")) is None
    assert not any(e["kind"] == "cycle_halted" for e in c.store.events())


def test_markers_persist_across_restart(tmp_path):
    c = Ctx(tmp_path, now=utc(14, 0))
    sleeps = []
    run_loop(c.cfg, c.build, 60, max_iterations=2, sleep=sleeps.append, now_fn=lambda: c.now)
    assert c.store.get_flag(marker_flag("stocks")) == "2026-10-15"
    assert c.store.get_flag(marker_flag("crypto")) == "2026-10-15"
    assert sleeps == [60]
    hist = c.store.flag_history(marker_flag("crypto"))
    assert len(hist) == 1                                      # second iteration did not re-run
    c.store.close()

    # restart (new process, same db): still nothing due today
    c2 = Ctx(tmp_path, now=utc(15, 0))
    run_loop(c2.cfg, c2.build, 60, max_iterations=1, sleep=lambda s: None, now_fn=lambda: c2.now)
    assert len(c2.store.flag_history(marker_flag("crypto"))) == 1
    assert len(c2.store.flag_history(marker_flag("stocks"))) == 1
    # next UTC day: crypto due again
    c2.store.close()
    c3 = Ctx(tmp_path, now=utc(0, 10, day=16))
    run_loop(c3.cfg, c3.build, 60, max_iterations=1, sleep=lambda s: None, now_fn=lambda: c3.now)
    assert c3.store.get_flag(marker_flag("crypto")) == "2026-10-16"


def test_kill_switch_halts_loop(tmp_path):
    c = Ctx(tmp_path)
    ctx = c.build()
    KillSwitch(ctx.store).engage("stop now", by="pytest")
    sleeps = []
    why = run_loop(c.cfg, lambda: ctx, 60, max_iterations=10, sleep=sleeps.append, now_fn=lambda: c.now)
    assert why == "halted" and sleeps == []
    hb = {h["component"]: h for h in ctx.store.heartbeats()}
    assert hb["scheduler"]["status"] == "halted"
    assert ctx.store.get_flag(marker_flag("crypto")) is None
    assert c.broker.submit_calls == 0


def test_exceptions_back_off_and_loop_continues(tmp_path):
    c = Ctx(tmp_path)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("build failed")
        return c.build()
    sleeps = []
    why = run_loop(c.cfg, flaky, 10, max_iterations=4, sleep=sleeps.append, now_fn=lambda: c.now)
    assert why == "max_iterations"
    assert sleeps == [20, 40, 10]                              # exponential back-off, then normal poll
    assert c.store.get_flag(marker_flag("crypto")) == "2026-10-15"


def test_aborted_cycle_does_not_set_marker(tmp_path):
    from quantbot.broker.base import BrokerError
    c = Ctx(tmp_path, symbols=("BTC/USD",))
    ctx = c.build()

    def down():
        raise BrokerError("connection refused")
    ctx.broker.get_account = down
    sleeps = []
    run_loop(c.cfg, lambda: ctx, 30, max_iterations=2, sleep=sleeps.append, now_fn=lambda: c.now)
    assert ctx.store.get_flag(marker_flag("crypto")) is None
    assert sleeps == [60]
    hb = {h["component"]: h for h in ctx.store.heartbeats()}
    assert hb["scheduler"]["status"] == "error"
