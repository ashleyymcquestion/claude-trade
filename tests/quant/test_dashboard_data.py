"""Dashboard numbers computed from a tmp Store filled with synthetic test records."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from quantbot.dashboard import data as D
from quantbot.models import AccountSnapshot, Fill, Order, OrderStatus, Position, Side
from quantbot.risk.killswitch import KillSwitch
from quantbot.risk.limits import FLAG_DAILY, FLAG_DRAWDOWN, FLAG_WEEKLY

# 2026-10-15 is a Thursday; 00:00 New York (EDT) = 04:00 UTC
NOW = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
NY_MIDNIGHT = datetime(2026, 10, 15, 4, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTBOT_KILL_FILE", str(tmp_path / "KILL"))
    from quantbot.store import Store
    s = Store(tmp_path / "d.db")
    yield s
    s.close()


def _snap(ts, equity, cash, positions=()):
    return AccountSnapshot(ts=ts, equity=equity, cash=cash, positions=tuple(positions))


def _populate(s):
    s.record_equity(_snap(NOW - timedelta(days=2), 1000.0, 1000.0))
    s.record_equity(_snap(NOW - timedelta(days=1), 1100.0, 900.0))        # peak
    s.record_equity(_snap(NY_MIDNIGHT - timedelta(hours=1), 990.0, 800.0))  # last before NY midnight -> SOD
    s.record_equity(_snap(NY_MIDNIGHT + timedelta(hours=2), 1045.0, 800.0))
    pos = Position("NVDA", 2.0, 100.0, 110.0, strategy="trend", stop_price=95.0)
    s.record_equity(_snap(NOW - timedelta(minutes=5), 1020.0, 800.0, [pos]))   # latest
    s.set_position_meta("NVDA", "trend", 96.0, NOW - timedelta(days=3), 112.0)
    s.set_position_meta("AMD", "breakout", 50.0, NOW - timedelta(days=1), 55.0)  # orphaned meta
    s.record_order(Order("trend-NVDA-2026-10-12-buy", "NVDA", Side.BUY, 2.0, OrderStatus.FILLED,
                         filled_qty=2.0, filled_avg_price=100.0, strategy="trend"))
    s.record_order(Order("mean_reversion-AMD-2026-10-15-buy", "AMD", Side.BUY, 1.0, OrderStatus.NEW,
                         strategy="mean_reversion"))
    s.record_fill(Fill("trend-NVDA-2026-10-12-buy", "NVDA", Side.BUY, 2.0, 100.0, 0.1, NOW - timedelta(days=3)))
    s.record_fill(Fill("unknown-order", "PLTR", Side.SELL, 1.0, 20.0, 0.0, NOW - timedelta(days=1)))
    s.record_realized_pnl(NOW - timedelta(days=1), "PLTR", "breakout", 15.0)
    s.record_realized_pnl(NOW - timedelta(hours=2), "AMD", "mean_reversion", -5.0)
    s.record_realized_pnl(NOW - timedelta(hours=1), "AMD", "mean_reversion", 2.0)
    s.record_regime("NVDA", NOW - timedelta(days=2), "bull_quiet")
    s.record_regime("NVDA", NOW - timedelta(days=1), "bull_volatile")
    s.record_regime("AMD", NOW - timedelta(days=1), "bear_quiet")


# ---------------------------------------------------------------- empty database
def test_empty_store_gives_none_and_empty_tables(store):
    assert D.account_summary(store) == {"equity": None, "cash": None, "gross": None, "ts": None}
    p = D.pnl_summary(store, NOW, 1000.0)
    assert p["daily_pnl"] is None and p["total_pnl"] is None and p["unrealized"] is None
    assert p["realized_total"] == 0.0
    assert D.max_drawdown(store) is None
    for df in (D.positions_table(store), D.open_orders_table(store), D.regimes_table(store),
               D.events_table(store), D.trade_history(store)["fills"], D.trade_history(store)["realized"]):
        assert df.empty and len(df.columns) > 0
    perf = D.strategy_performance(store, "/nonexistent/reports")
    assert perf["live"].empty and perf["research"].empty and perf["research_meta"] is None
    b = D.breaker_status(store, NOW)
    assert b["kill_switch"]["engaged"] is False and b["entries_allowed"] is True
    h = D.health(store, NOW)
    assert h["runner"] == "missing" and h["table"].empty


# ---------------------------------------------------------------- populated
def test_account_and_pnl(store):
    _populate(store)
    a = D.account_summary(store)
    assert a["equity"] == 1020.0 and a["cash"] == 800.0 and a["gross"] == pytest.approx(220.0)
    assert a["ts"] == NOW - timedelta(minutes=5)
    p = D.pnl_summary(store, NOW, 1000.0)
    assert p["start_of_day_equity"] == 990.0
    assert p["daily_pnl"] == pytest.approx(30.0)
    assert p["total_pnl"] == pytest.approx(20.0)
    assert p["total_return"] == pytest.approx(0.02)
    assert p["realized_total"] == pytest.approx(12.0)
    assert p["realized_today"] == pytest.approx(-3.0)
    assert p["unrealized"] == pytest.approx(20.0)


def test_daily_pnl_uses_first_snapshot_of_day_when_none_before(store):
    store.record_equity(_snap(NY_MIDNIGHT + timedelta(hours=1), 500.0, 500.0))
    store.record_equity(_snap(NOW, 480.0, 480.0))
    assert D.pnl_summary(store, NOW, 500.0)["daily_pnl"] == pytest.approx(-20.0)


def test_max_drawdown(store):
    _populate(store)
    # peak 1100 -> trough 990 = 10%
    assert D.max_drawdown(store) == pytest.approx(0.10)
    assert D.current_drawdown(store) == pytest.approx(1 - 1020 / 1100)


def test_positions_join_meta(store):
    _populate(store)
    t = D.positions_table(store).set_index("symbol")
    nvda = t.loc["NVDA"]
    assert nvda["qty"] == 2.0 and nvda["strategy"] == "trend"
    assert nvda["stop_price"] == 96.0                      # meta wins over snapshot stop
    assert nvda["unrealized_pnl"] == pytest.approx(20.0)
    assert nvda["unrealized_pct"] == pytest.approx(0.10)
    assert t.loc["AMD"]["qty"] == 0.0 and t.loc["AMD"]["strategy"] == "breakout"


def test_orders_and_trade_history(store):
    _populate(store)
    oo = D.open_orders_table(store)
    assert list(oo["client_order_id"]) == ["mean_reversion-AMD-2026-10-15-buy"]
    h = D.trade_history(store)
    f = h["fills"]
    assert list(f["symbol"]) == ["PLTR", "NVDA"]           # newest first
    assert f.iloc[1]["strategy"] == "trend" and f.iloc[0]["strategy"] == ""
    assert f.iloc[1]["notional"] == pytest.approx(200.0)
    assert list(h["realized"]["pnl"]) == [2.0, -5.0, 15.0]


def test_strategy_performance_live_and_research(store, tmp_path):
    _populate(store)
    reports = tmp_path / "reports"
    reports.mkdir()
    payload = {
        "kind": "backtest", "generated_at": "2026-10-14T00:00:00", "warning": "research only",
        "portfolio": {"trend": {"status": "ok", "metrics": {"num_trades": 12, "total_return": 0.1,
                                                            "sharpe": 0.8, "max_drawdown": 0.05}}},
        "symbols": {"NVDA": {"buy_and_hold": {"num_trades": 1, "total_return": 0.3},
                             "strategies": {"trend": {"status": "ok", "metrics": {"num_trades": 4}},
                                            "breakout": {"status": "error", "reason": "x"}}}},
    }
    (reports / "latest_backtest.json").write_text(json.dumps(payload))
    perf = D.strategy_performance(store, reports)
    live = perf["live"].set_index("strategy")
    assert live.loc["mean_reversion"]["realized_pnl"] == pytest.approx(-3.0)
    assert live.loc["mean_reversion"]["closed_trades"] == 2
    assert live.loc["mean_reversion"]["win_rate"] == pytest.approx(0.5)
    assert live.loc["breakout"]["realized_pnl"] == pytest.approx(15.0)
    r = perf["research"]
    assert len(r) == 4
    port = r[r["scope"] == "portfolio"].iloc[0]
    assert port["strategy"] == "trend" and port["trades"] == 12
    assert set(r[r["scope"] == "NVDA"]["strategy"]) == {"trend", "breakout", "buy_and_hold"}
    assert perf["research_meta"]["generated_at"] == "2026-10-14T00:00:00"


def test_strategy_performance_bad_report(store, tmp_path):
    (tmp_path / "latest_backtest.json").write_text("{not json")
    perf = D.strategy_performance(store, tmp_path)
    assert perf["research_error"] and perf["research"].empty


def test_regimes_and_events(store):
    _populate(store)
    r = D.regimes_table(store).set_index("symbol")
    assert r.loc["NVDA"]["regime"] == "bull_volatile" and r.loc["AMD"]["regime"] == "bear_quiet"
    store.record_event("critical", "kill_switch_engaged", "test", by="me")
    e = D.events_table(store)
    assert e.iloc[0]["kind"] == "kill_switch_engaged" and "me" in e.iloc[0]["fields"]


def test_breaker_status(store):
    KillSwitch(store).engage("test", by="pytest")
    store.set_flag(FLAG_DAILY, "2026-10-15", "daily tripped")
    store.set_flag(FLAG_WEEKLY, "2026-W41", "last week")       # earlier ISO week -> expired
    store.set_flag(FLAG_DRAWDOWN, "2026-10-01T00:00:00+00:00", "dd tripped")
    b = D.breaker_status(store, NOW)
    assert b["kill_switch"]["engaged"] and b["kill_switch"]["by"] == "pytest"
    assert b["breakers"][FLAG_DAILY]["active"] and b["breakers"][FLAG_DAILY]["reason"] == "daily tripped"
    assert b["breakers"][FLAG_WEEKLY]["active"] is False and b["breakers"][FLAG_WEEKLY]["expired"]
    assert b["breakers"][FLAG_DRAWDOWN]["active"]
    assert b["entries_allowed"] is False
    KillSwitch(store).release("pytest")
    store.set_flag(FLAG_DAILY, None)
    store.set_flag(FLAG_DRAWDOWN, None)
    b = D.breaker_status(store, NOW)
    assert not b["kill_switch"]["engaged"] and not b["any_breaker_active"] and b["entries_allowed"]


def test_health_ok_stale_error(store):
    store.heartbeat("runner", "ok", "cycle done")
    store.heartbeat("dashboard_probe", "ok")
    now = datetime.now(UTC)
    h = D.health(store, now, poll_seconds=60)
    assert h["runner"] == "ok" and not h["any_stale"]
    assert h["stale_after_seconds"] == 15 * 60               # max(2*60s, 15 min)
    assert D.health(store, now + timedelta(minutes=16), poll_seconds=60)["runner"] == "stale"
    # long poll interval: threshold is 2*poll
    assert D.health(store, now + timedelta(minutes=16), poll_seconds=900)["runner"] == "ok"
    assert D.health(store, now + timedelta(minutes=31), poll_seconds=900)["runner"] == "stale"
    store.heartbeat("runner", "error", "boom")
    assert D.health(store, datetime.now(UTC))["runner"] == "error"
