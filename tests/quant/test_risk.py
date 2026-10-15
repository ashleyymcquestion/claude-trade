from datetime import UTC, datetime, timedelta

import pytest

from quantbot.config import RiskConfig
from quantbot.models import AccountSnapshot, AssetClass, AssetInfo, Order, OrderStatus, Position, Side
from quantbot.risk.killswitch import KillSwitch
from quantbot.risk.limits import RiskManager, ny_day_bounds
from quantbot.risk.sizing import size_position
from quantbot.store import Store

# Thursday 2026-10-15, 10:00 New York (EDT, UTC-4)
NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
CFG = RiskConfig()
STOCK = AssetInfo("NVDA", AssetClass.STOCK, tradable=True, fractionable=True)


@pytest.fixture(autouse=True)
def _kill_file(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTBOT_KILL_FILE", str(tmp_path / "KILL"))


def _setup(tmp_path, initial_capital=None):
    store = Store(tmp_path / "q.db")
    ks = KillSwitch(store)
    rm = RiskManager(CFG, store, ks, initial_capital=initial_capital)
    # equity before the start of the week (and day)
    store.record_equity(AccountSnapshot(datetime(2026, 10, 9, 21, 0, tzinfo=UTC), 1000.0, 1000.0))
    return store, ks, rm


def _snap(equity=1000.0, cash=None, positions=(), ts=NOW):
    return AccountSnapshot(ts, equity, equity if cash is None else cash, tuple(positions))


def _pos(sym="AMD", qty=1.0, entry=100.0, mkt=100.0):
    return Position(sym, qty, entry, mkt, "trend", entry * 0.9)


# ---------------------------------------------------------------- kill switch
def test_killswitch_persists_across_instances(tmp_path):
    path = tmp_path / "q.db"
    s = Store(path)
    ks = KillSwitch(s)
    assert not ks.is_engaged()
    ks.engage("manual test", by="alice")
    s.close()

    s2 = Store(path)
    ks2 = KillSwitch(s2)
    assert ks2.is_engaged()
    st = ks2.status()
    assert st["engaged"] and st["reason"] == "manual test" and st["by"] == "alice"
    assert st["since"].tzinfo is not None
    ks2.release(by="bob")
    assert not ks2.is_engaged() and not ks2.status()["engaged"]
    kinds = [e["kind"] for e in s2.events()]
    assert kinds[:2] == ["kill_switch_released", "kill_switch_engaged"]


def test_killswitch_file_override(tmp_path):
    s = Store(tmp_path / "q.db")
    ks = KillSwitch(s)
    kill = tmp_path / "KILL"
    kill.touch()
    assert ks.is_engaged()
    assert ks.status()["by"] == "file"
    ks.release(by="bob")              # cannot release while the file exists
    assert ks.is_engaged()
    kill.unlink()
    assert not ks.is_engaged()


def test_kill_switch_blocks_entries_and_exits(tmp_path):
    store, ks, rm = _setup(tmp_path)
    snap = _snap(positions=[_pos()])
    ks.engage("test", by="t")
    assert not rm.evaluate(snap, NOW).trading_allowed
    d = rm.approve_entry(snap, "NVDA", 100.0, 95.0, STOCK, 5.0, NOW)
    assert not d.approved and "kill switch" in d.reason
    assert not rm.approve_exit(snap, "AMD", NOW).approved


# ---------------------------------------------------------------- daily breaker
def test_daily_breaker_trips_at_exact_limit_blocks_entries_allows_exits(tmp_path):
    store, ks, rm = _setup(tmp_path)
    store.record_realized_pnl(NOW - timedelta(hours=1), "AMD", "trend", -14.99)
    snap = _snap(equity=985.01, positions=[_pos()])
    assert rm.evaluate(snap, NOW).trading_allowed

    store.record_realized_pnl(NOW - timedelta(minutes=30), "AMD", "trend", -0.01)   # total exactly -15.00
    st = rm.evaluate(_snap(equity=985.0, positions=[_pos()]), NOW)
    assert st.daily_realized == pytest.approx(-15.0)
    assert st.daily_breaker and not st.trading_allowed
    assert any("daily" in r for r in st.reasons)
    assert store.get_flag("breaker_daily") == "2026-10-15"
    assert [e["kind"] for e in store.events()].count("breaker_daily_tripped") == 1
    assert store.events()[0]["level"] == "critical"

    snap = _snap(equity=985.0, positions=[_pos()])
    d = rm.approve_entry(snap, "NVDA", 100.0, 95.0, STOCK, 5.0, NOW)
    assert not d.approved and "daily" in d.reason
    ex = rm.approve_exit(snap, "AMD", NOW)
    assert ex.approved and ex.qty == 1.0

    # no duplicate critical event on re-evaluation
    rm.evaluate(snap, NOW + timedelta(minutes=5))
    assert [e["kind"] for e in store.events()].count("breaker_daily_tripped") == 1


def test_daily_breaker_survives_restart_and_clears_next_ny_day(tmp_path):
    store, ks, rm = _setup(tmp_path)
    store.record_realized_pnl(NOW - timedelta(hours=1), "AMD", "trend", -20.0)
    assert rm.evaluate(_snap(equity=980.0), NOW).daily_breaker
    store.close()

    store2 = Store(tmp_path / "q.db")
    rm2 = RiskManager(CFG, store2, KillSwitch(store2))
    # later the same NY day, still latched even though nothing new happened
    assert rm2.evaluate(_snap(equity=980.0), NOW + timedelta(hours=5)).daily_breaker
    # 2026-10-16 00:30 New York = 04:30 UTC
    nxt = datetime(2026, 10, 16, 4, 30, tzinfo=UTC)
    st = rm2.evaluate(_snap(equity=980.0, ts=nxt), nxt)
    assert not st.daily_breaker and st.trading_allowed and st.daily_realized == 0.0
    assert store2.get_flag("breaker_daily") is None


def test_ny_day_boundary_uses_new_york_not_utc(tmp_path):
    store, ks, rm = _setup(tmp_path)
    # 23:30 New York on Oct 15 is already 03:30 UTC on Oct 16
    late = datetime(2026, 10, 16, 3, 30, tzinfo=UTC)
    start, end = ny_day_bounds(late)
    assert start == datetime(2026, 10, 15, 4, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 16, 4, 0, tzinfo=UTC)

    store.record_realized_pnl(datetime(2026, 10, 15, 13, 0, tzinfo=UTC), "AMD", "trend", -10.0)  # Oct 15 NY
    store.record_realized_pnl(datetime(2026, 10, 15, 3, 0, tzinfo=UTC), "AMD", "trend", -7.0)    # Oct 14 NY
    st = rm.evaluate(_snap(equity=983.0, ts=late), late)
    assert st.daily_realized == pytest.approx(-10.0)
    assert not st.daily_breaker

    store.record_realized_pnl(datetime(2026, 10, 16, 3, 0, tzinfo=UTC), "AMD", "trend", -5.0)    # 23:00 NY Oct 15
    st = rm.evaluate(_snap(equity=978.0, ts=late), late)
    assert st.daily_realized == pytest.approx(-15.0) and st.daily_breaker
    assert store.get_flag("breaker_daily") == "2026-10-15"


def test_naive_now_rejected(tmp_path):
    store, ks, rm = _setup(tmp_path)
    with pytest.raises(ValueError):
        rm.evaluate(_snap(), datetime(2026, 10, 15, 12, 0))


# ---------------------------------------------------------------- weekly breaker
def test_weekly_breaker_trips_at_4pct_and_clears_next_iso_week(tmp_path):
    store, ks, rm = _setup(tmp_path)
    # Mon..Thu of ISO week 2026-W42, -10 each (never hits the daily limit of -15)
    for day in (12, 13, 14):
        store.record_realized_pnl(datetime(2026, 10, day, 15, 0, tzinfo=UTC), "AMD", "trend", -10.0)
    st = rm.evaluate(_snap(equity=970.0), NOW)
    assert st.weekly_realized == pytest.approx(-30.0) and st.trading_allowed

    store.record_realized_pnl(NOW - timedelta(minutes=1), "AMD", "trend", -10.0)
    st = rm.evaluate(_snap(equity=960.0, positions=[_pos()]), NOW)
    assert st.weekly_realized == pytest.approx(-40.0)
    assert st.weekly_breaker and not st.daily_breaker and not st.trading_allowed
    assert store.get_flag("breaker_weekly") == "2026-W42"
    assert not rm.approve_entry(_snap(equity=960.0), "NVDA", 100.0, 95.0, STOCK, 5.0, NOW).approved
    assert rm.approve_exit(_snap(equity=960.0, positions=[_pos()]), "AMD", NOW).approved

    sat = datetime(2026, 10, 17, 16, 0, tzinfo=UTC)
    assert rm.evaluate(_snap(equity=960.0, ts=sat), sat).weekly_breaker   # same ISO week
    # Monday 2026-10-19 00:05 New York = 04:05 UTC
    mon = datetime(2026, 10, 19, 4, 5, tzinfo=UTC)
    st = rm.evaluate(_snap(equity=960.0, ts=mon), mon)
    assert not st.weekly_breaker and st.trading_allowed
    # Sunday 23:59 NY (Monday 03:59 UTC) would still have been the old week
    assert rm.evaluate(_snap(equity=960.0), datetime(2026, 10, 19, 3, 59, tzinfo=UTC)).weekly_realized == pytest.approx(-40.0)


# ---------------------------------------------------------------- drawdown breaker
def test_drawdown_uses_unrealized_and_latches_until_reset(tmp_path):
    store, ks, rm = _setup(tmp_path)
    # peak 1000 recorded; now 919 purely from an unrealized loss, no realized P&L
    pos = Position("NVDA", 1.0, 181.0, 100.0, "trend", 90.0)
    snap = _snap(equity=919.0, cash=819.0, positions=[pos])
    st = rm.evaluate(snap, NOW)
    assert st.daily_realized == 0.0 and st.weekly_realized == 0.0
    assert st.unrealized == pytest.approx(-81.0)
    assert st.peak_equity == 1000.0
    assert st.drawdown == pytest.approx(0.081)
    assert st.drawdown_breaker and not st.trading_allowed
    assert store.events()[0]["kind"] == "breaker_drawdown_tripped"
    assert rm.approve_exit(snap, "NVDA", NOW).approved

    store.close()
    store2 = Store(tmp_path / "q.db")
    rm2 = RiskManager(CFG, store2, KillSwitch(store2))
    later = NOW + timedelta(days=8)
    recovered = _snap(equity=990.0, ts=later)
    st = rm2.evaluate(recovered, later)               # recovered, new week, still latched
    assert st.drawdown_breaker and not st.trading_allowed
    assert not rm2.approve_entry(recovered, "NVDA", 100.0, 95.0, STOCK, 5.0, later).approved

    rm2.reset_drawdown(by="ops", now=later)
    st = rm2.evaluate(recovered, later)
    assert not st.drawdown_breaker and st.trading_allowed
    assert store2.get_flag("breaker_drawdown") is None


def test_initial_capital_counts_toward_peak(tmp_path):
    store = Store(tmp_path / "q.db")
    rm = RiskManager(CFG, store, KillSwitch(store), initial_capital=1000.0)
    st = rm.evaluate(_snap(equity=900.0), NOW)       # no equity curve yet
    assert st.peak_equity == 1000.0 and st.drawdown_breaker


# ---------------------------------------------------------------- approve_entry
def test_approve_entry_rejects_held_symbol(tmp_path):
    store, ks, rm = _setup(tmp_path)
    snap = _snap(cash=900.0, positions=[_pos("NVDA")])
    d = rm.approve_entry(snap, "NVDA", 100.0, 95.0, STOCK, 5.0, NOW)
    assert not d.approved and "already held" in d.reason


def test_approve_entry_rejects_open_buy_order(tmp_path):
    store, ks, rm = _setup(tmp_path)
    store.record_order(Order("trend-NVDA-2026-10-15-buy", "NVDA", Side.BUY, 1.0, OrderStatus.PENDING))
    d = rm.approve_entry(_snap(), "NVDA", 100.0, 95.0, STOCK, 5.0, NOW)
    assert not d.approved and "open buy order" in d.reason


def test_approve_entry_rejects_at_max_open_positions(tmp_path):
    store, ks, rm = _setup(tmp_path)
    positions = [_pos(f"S{i}", qty=0.1, entry=100.0, mkt=100.0) for i in range(CFG.max_open_positions)]
    snap = _snap(cash=940.0, positions=positions)
    d = rm.approve_entry(snap, "NVDA", 100.0, 95.0, STOCK, 5.0, NOW)
    assert not d.approved and "max open positions" in d.reason


def test_approve_entry_returns_size_position_result(tmp_path):
    store, ks, rm = _setup(tmp_path)
    snap = _snap(equity=1000.0, cash=900.0, positions=[_pos("AMD", qty=1.0, entry=100.0, mkt=100.0)])
    d = rm.approve_entry(snap, "NVDA", 100.0, 95.0, STOCK, 5.0, NOW)
    expected = size_position(equity=1000.0, cash=900.0, price=100.0, stop_price=95.0, asset=STOCK,
                             current_gross=100.0, current_symbol_exposure=0.0, risk=CFG, one_way_cost_bps=5.0)
    assert d == expected and d.approved and d.qty > 0
    # size_position's own rejection passes through unchanged
    bad = rm.approve_entry(snap, "NVDA", 100.0, None, STOCK, 5.0, NOW)
    assert not bad.approved and "stop" in bad.reason


def test_approve_exit_full_qty_and_not_held(tmp_path):
    store, ks, rm = _setup(tmp_path)
    snap = _snap(positions=[_pos("AMD", qty=2.5)])
    d = rm.approve_exit(snap, "AMD", NOW)
    assert d.approved and d.qty == 2.5
    assert not rm.approve_exit(snap, "NVDA", NOW).approved
