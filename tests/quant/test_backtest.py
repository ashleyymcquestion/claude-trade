"""Engine tests on small hand-built bars with scripted strategies (no network, no files)."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from quantbot.backtest.engine import buy_and_hold, run_backtest, run_portfolio
from quantbot.config import AppConfig, CostConfig, RiskConfig, SymbolConfig
from quantbot.models import AssetClass, AssetInfo
from quantbot.strategies.base import Strategy, empty_signals

COST_BPS = 10.0
C = COST_BPS / 10_000
CAPITAL = 10_000.0


# ------------------------------------------------------------------ helpers (also used by other test files)

def make_cfg(symbols=("TEST",), asset_class="stock", **risk) -> AppConfig:
    r = {"risk_per_trade": 0.02, "max_position_pct": 1.0, "max_gross_pct": 1.0, **risk}
    return AppConfig(
        initial_capital=CAPITAL,
        symbols=tuple(SymbolConfig(symbol=s, asset_class=asset_class) for s in symbols),
        costs=CostConfig(stock_commission_bps=0.0, stock_half_spread_bps=5.0, stock_slippage_bps=5.0,
                         crypto_fee_bps=5.0, crypto_half_spread_bps=3.0, crypto_slippage_bps=2.0),
        risk=RiskConfig(**r),
    )


def make_asset(symbol="TEST", fractionable=True, tradable=True, asset_class=AssetClass.STOCK) -> AssetInfo:
    return AssetInfo(symbol=symbol, asset_class=asset_class, tradable=tradable, fractionable=fractionable)


def make_bars(opens, closes=None, highs=None, lows=None, start="2024-01-01", freq="B") -> pd.DataFrame:
    o = np.asarray(opens, float)
    c = o.copy() if closes is None else np.asarray(closes, float)
    h = np.maximum(o, c) + 0.01 if highs is None else np.asarray(highs, float)
    lo = np.minimum(o, c) - 0.01 if lows is None else np.asarray(lows, float)
    idx = pd.date_range(start, periods=len(o), freq=freq, tz="UTC")
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": 1e6}, index=idx)


def random_bars(n=300, seed=0, start="2020-01-01", freq="B") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, n)))
    open_ = close * np.exp(rng.normal(0, 0.004, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.01, n))
    return make_bars(open_, close, high, low, start=start, freq=freq)


class Scripted(Strategy):
    """Enter on the given rows, exit on the given rows; constant stop/trail/max-hold."""
    name = "scripted"

    def __init__(self, entries=(), exits=(), stop=50.0, trail=float("nan"), max_hold=float("nan")):
        self.entries, self.exits = set(entries), set(exits)
        self.stop, self.trail, self.max_hold = stop, trail, max_hold

    @property
    def warmup_bars(self) -> int:
        return 0

    def signals(self, bars):
        s = empty_signals(bars.index)
        pos = np.arange(len(bars))
        s["entry"] = np.isin(pos, list(self.entries))
        s["exit"] = np.isin(pos, list(self.exits))
        s["stop_distance"] = self.stop
        s["trail_distance"] = self.trail
        s["max_hold_bars"] = self.max_hold
        return s


class MACross(Strategy):
    """Backward-looking toy strategy for look-ahead / accounting tests."""
    name = "macross"

    def __init__(self, fast=5, slow=20):
        self.fast, self.slow = fast, slow

    @property
    def warmup_bars(self) -> int:
        return self.slow

    def signals(self, bars):
        c = bars["close"]
        f, s_ = c.rolling(self.fast).mean(), c.rolling(self.slow).mean()
        sig = empty_signals(bars.index)
        sig["entry"] = (f > s_) & (f.shift(1) <= s_.shift(1))
        sig["exit"] = (f < s_) & (f.shift(1) >= s_.shift(1))
        sig.iloc[: self.slow, sig.columns.get_loc("entry")] = False
        sig["stop_distance"] = 2.5 * c.diff().abs().rolling(14).mean()
        sig["trail_distance"] = 4.0 * c.diff().abs().rolling(14).mean()
        sig["max_hold_bars"] = 40.0
        return sig


SYM = SymbolConfig(symbol="TEST", asset_class="stock")


def run(bars, strat, cfg=None, asset=None, **kw):
    return run_backtest(bars, strat, SYM, asset or make_asset(), cfg or make_cfg(), CAPITAL, **kw)


# ------------------------------------------------------------------ execution timing

def test_signal_at_t_fills_at_next_open_with_costs():
    opens = [100 + i for i in range(10)]
    closes = [o + 0.5 for o in opens]
    bars = make_bars(opens, closes)
    res = run(bars, Scripted(entries=[3], exits=[6]))
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    assert t.entry_ts == bars.index[4]
    assert t.entry_price == pytest.approx(opens[4] * (1 + C))
    assert t.entry_price != pytest.approx(closes[3] * (1 + C))
    assert t.exit_ts == bars.index[7]
    assert t.exit_price == pytest.approx(opens[7] * (1 - C))
    assert t.exit_reason == "signal"
    # flat before the fill: nothing happens at bar 3
    assert (res.equity.iloc[:4] == CAPITAL).all()


def test_entry_and_exit_on_same_row_does_not_trade():
    bars = make_bars([100.0] * 10)
    res = run(bars, Scripted(entries=[3], exits=[3]))
    assert res.trades.empty and not res.open_positions and res.costs_paid == 0


def test_exit_signal_on_entry_bar_exits_next_open_not_same_open():
    bars = make_bars([100.0 + i for i in range(10)])
    res = run(bars, Scripted(entries=[2], exits=[3]))
    t = res.trades.iloc[0]
    assert t.entry_ts == bars.index[3] and t.exit_ts == bars.index[4] and t.bars_held == 1


def test_round_trip_on_flat_price_loses_exactly_two_one_way_costs():
    bars = make_bars([100.0] * 10)
    res = run(bars, Scripted(entries=[2], exits=[5], stop=5.0))
    t = res.trades.iloc[0]
    notional = t.qty * 100.0
    assert t.pnl == pytest.approx(-2 * C * notional)
    assert t.return_pct == pytest.approx((1 - C) / (1 + C) - 1)
    assert t.costs == pytest.approx(2 * C * notional)
    assert res.costs_paid == pytest.approx(2 * C * notional)
    assert res.equity.iloc[-1] == pytest.approx(CAPITAL - 2 * C * notional)


# ------------------------------------------------------------------ stops

def test_intrabar_stop_fills_at_stop():
    n = 10
    lows = [99.9] * n
    lows[6] = 90.0
    bars = make_bars([100.0] * n, lows=lows)
    res = run(bars, Scripted(entries=[2], stop=5.0))
    t = res.trades.iloc[0]
    stop = 100 * (1 + C) - 5.0
    assert t.exit_ts == bars.index[6]
    assert t.exit_price == pytest.approx(stop * (1 - C))
    assert t.exit_reason == "stop"


def test_gap_below_stop_fills_at_open():
    n = 10
    opens, lows = [100.0] * n, [99.9] * n
    opens[6], lows[6] = 90.0, 89.0
    bars = make_bars(opens, closes=[100.0] * 6 + [91.0] + [100.0] * 3, lows=lows)
    res = run(bars, Scripted(entries=[2], stop=5.0))
    t = res.trades.iloc[0]
    assert t.exit_ts == bars.index[6]
    assert t.exit_price == pytest.approx(90.0 * (1 - C))


def test_trailing_stop_ratchets_up_only():
    #          0     1     2     3     4     5      6      7
    opens = [100, 100, 100, 101, 106, 109, 107.0, 100]
    closes = [100, 100, 100, 105, 110, 108, 106.5, 100]
    lows = [99.9, 99.9, 99.9, 100.5, 105.5, 106.5, 105.0, 99]
    bars = make_bars(opens, closes, lows=lows)
    res = run(bars, Scripted(entries=[1], stop=20.0, trail=4.0))
    t = res.trades.iloc[0]
    # highest close 110 -> trail 106; close 108 must NOT lower it to 104
    assert t.exit_ts == bars.index[6]
    assert t.exit_reason == "trail_stop"
    assert t.exit_price == pytest.approx(106.0 * (1 - C))


def test_max_hold_bars_exit():
    bars = make_bars([100.0] * 12)
    res = run(bars, Scripted(entries=[2], max_hold=3.0, stop=5.0))
    t = res.trades.iloc[0]
    assert t.entry_ts == bars.index[3]
    assert t.exit_ts == bars.index[6]
    assert t.exit_reason == "max_hold" and t.bars_held == 3


# ------------------------------------------------------------------ sizing

def test_sizing_honors_risk_per_trade():
    bars = make_bars([100.0] * 10)
    res = run(bars, Scripted(entries=[2], stop=2.0), cfg=make_cfg(risk_per_trade=0.01))
    expected = 0.01 * CAPITAL / (2.0 + 2 * C * 100.0)       # 45.45...
    assert res.open_positions[0]["qty"] == pytest.approx(expected)


def test_sizing_honors_max_position_pct():
    bars = make_bars([100.0] * 10)
    res = run(bars, Scripted(entries=[2], stop=2.0),
              cfg=make_cfg(risk_per_trade=0.01, max_position_pct=0.25, max_gross_pct=0.5))
    assert res.open_positions[0]["qty"] == pytest.approx(0.25 * CAPITAL / 100.0)


@pytest.mark.parametrize("strat,asset", [
    (Scripted(entries=[2], stop=float("nan")), make_asset()),         # no stop -> rejected
    (Scripted(entries=[2], stop=2.0), make_asset(tradable=False)),     # not tradable -> rejected
    (Scripted(entries=[2], stop=2.0), make_asset(fractionable=False)), # < 1 share fits -> rejected
])
def test_rejected_size_creates_no_trade(strat, asset):
    price = 50_000.0 if not asset.fractionable else 100.0
    bars = make_bars([price] * 10)
    res = run(bars, strat, asset=asset, cfg=make_cfg(risk_per_trade=0.01, max_position_pct=0.25, max_gross_pct=0.5))
    assert res.trades.empty and not res.open_positions
    assert res.costs_paid == 0 and (res.equity == CAPITAL).all()
    assert res.meta["rejected_entries"] == 1


def test_whole_share_asset_gets_integer_qty():
    bars = make_bars([100.0] * 10)
    res = run(bars, Scripted(entries=[2], exits=[5], stop=2.0), asset=make_asset(fractionable=False),
              cfg=make_cfg(risk_per_trade=0.01))
    q = res.trades.iloc[0].qty
    assert q == 45.0 and float(q).is_integer()


# ------------------------------------------------------------------ accounting and look-ahead

def test_equity_accounting_identity():
    bars = random_bars(400, seed=1)
    res = run(bars, MACross())
    assert len(res.trades) > 3
    unreal = sum(p["unrealized_pnl"] for p in res.open_positions)
    assert res.equity.iloc[-1] == pytest.approx(CAPITAL + res.trades["pnl"].sum() + unreal)
    assert res.costs_paid >= res.trades["costs"].sum() - 1e-9
    # with a position still open at the end
    sc = run(bars, Scripted(entries=[10, 50, 380], exits=[30, 90], stop=8.0, trail=10.0))
    assert len(sc.open_positions) == 1 and len(sc.trades) >= 2
    unreal = sc.open_positions[0]["unrealized_pnl"]
    assert unreal != 0
    assert sc.equity.iloc[-1] == pytest.approx(CAPITAL + sc.trades["pnl"].sum() + unreal)
    # close_at_end turns the open position into a trade and keeps the identity
    res2 = run(bars, MACross(), close_at_end=True)
    assert not res2.open_positions
    assert res2.equity.iloc[-1] == pytest.approx(CAPITAL + res2.trades["pnl"].sum())


def test_no_look_ahead_future_prices_do_not_change_the_past():
    bars = random_bars(400, seed=2)
    t = 250
    changed = bars.copy()
    rng = np.random.default_rng(99)
    factor = np.exp(rng.normal(0, 0.05, len(bars) - t - 1)).cumprod()
    for col in ("open", "high", "low", "close"):
        changed.iloc[t + 1:, changed.columns.get_loc(col)] *= factor
    a, b = run(bars, MACross()), run(changed, MACross())
    pd.testing.assert_series_equal(a.equity.iloc[: t + 1], b.equity.iloc[: t + 1])
    cut = bars.index[t]
    ca = a.trades[a.trades.exit_ts <= cut].reset_index(drop=True)
    cb = b.trades[b.trades.exit_ts <= cut].reset_index(drop=True)
    pd.testing.assert_frame_equal(ca, cb)
    ea = a.trades[a.trades.entry_ts <= cut][["entry_ts", "entry_price", "qty"]].reset_index(drop=True)
    eb = b.trades[b.trades.entry_ts <= cut][["entry_ts", "entry_price", "qty"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(ea, eb)


# ------------------------------------------------------------------ portfolio

def test_portfolio_never_exceeds_max_gross():
    syms = ["A", "B", "C", "D", "E", "F"]
    cfg = make_cfg(symbols=syms, risk_per_trade=0.02, max_position_pct=0.25, max_gross_pct=0.5)
    bars = {s: make_bars([100.0] * 15) for s in syms}
    res = run_portfolio(bars, Scripted(entries=[2, 5, 8], stop=1.0), list(cfg.symbols),
                        {s: make_asset(s) for s in syms}, cfg, CAPITAL)
    assert (res.exposure <= cfg.risk.max_gross_pct * (1 + 2 * C) + 1e-12).all()
    assert sorted(p["symbol"] for p in res.open_positions) == ["A", "B"]   # deterministic order
    assert res.meta["rejected_entries"] > 0


def test_portfolio_max_open_positions():
    syms = ["A", "B", "C"]
    cfg = make_cfg(symbols=syms, max_open_positions=1)
    bars = {s: make_bars([100.0] * 10) for s in syms}
    res = run_portfolio(bars, Scripted(entries=[2], stop=5.0), list(cfg.symbols),
                        {s: make_asset(s) for s in syms}, cfg, CAPITAL)
    assert [p["symbol"] for p in res.open_positions] == ["A"]


class PerSymbol(Strategy):
    name = "persymbol"

    def __init__(self, plan):
        self.plan = plan  # bars-identity key -> Scripted

    @property
    def warmup_bars(self) -> int:
        return 0

    def signals(self, bars):
        return self.plan[bars.attrs["sym"]].signals(bars)


def test_portfolio_exits_processed_before_entries():
    cfg = make_cfg(symbols=["A", "B"], risk_per_trade=0.02, max_position_pct=0.25, max_gross_pct=0.25)
    a, b = make_bars([100.0] * 10), make_bars([100.0] * 10)
    a.attrs["sym"], b.attrs["sym"] = "A", "B"
    strat = PerSymbol({"A": Scripted(entries=[1], exits=[4], stop=1.0), "B": Scripted(entries=[4], stop=1.0)})
    res = run_portfolio({"A": a, "B": b}, strat, list(cfg.symbols),
                        {"A": make_asset("A"), "B": make_asset("B")}, cfg, CAPITAL)
    assert res.trades.iloc[0].symbol == "A" and res.trades.iloc[0].exit_ts == a.index[5]
    assert res.open_positions[0]["symbol"] == "B"
    assert res.open_positions[0]["entry_ts"] == b.index[5]     # same open A was sold at


def test_portfolio_stock_trades_only_on_its_sessions():
    cfg = make_cfg(symbols=["STK", "CRY"])
    stk = make_bars([100.0] * 10, start="2024-01-01", freq="B")        # Mon 1 Jan ..
    cry = make_bars([50.0] * 14, start="2024-01-01", freq="D")
    stk.attrs["sym"], cry.attrs["sym"] = "STK", "CRY"
    # stock row 4 = Friday 5 Jan -> fill at Monday 8 Jan open
    strat = PerSymbol({"STK": Scripted(entries=[4], stop=5.0), "CRY": Scripted(entries=[5], stop=5.0)})
    syms = [SymbolConfig(symbol="STK", asset_class="stock"), SymbolConfig(symbol="CRY", asset_class="crypto")]
    res = run_portfolio({"STK": stk, "CRY": cry}, strat, syms,
                        {"STK": make_asset("STK"), "CRY": make_asset("CRY", asset_class=AssetClass.CRYPTO)},
                        cfg.model_copy(update={"symbols": tuple(syms)}), CAPITAL)
    entries = {p["symbol"]: p["entry_ts"] for p in res.open_positions}
    assert entries["STK"] == pd.Timestamp("2024-01-08", tz="UTC")
    assert entries["CRY"] == pd.Timestamp("2024-01-07", tz="UTC")   # Sunday: crypto trades every day
    assert len(res.equity) == 14


# ------------------------------------------------------------------ buy and hold

def test_buy_and_hold_matches_hand_calculation():
    bars = make_bars([100.0, 110, 120, 130, 140], closes=[105.0, 115, 125, 135, 150])
    cfg = make_cfg()
    frac = buy_and_hold(bars, SYM, make_asset(), cfg, 1000.0)
    assert frac.equity.iloc[-1] / 1000.0 - 1 == pytest.approx(150 / (100 * (1 + C)) - 1)
    whole = buy_and_hold(bars, SYM, make_asset(fractionable=False), cfg, 1000.0)
    qty = math.floor(1000 / (100 * (1 + C)))
    assert whole.open_positions[0]["qty"] == qty == 9
    assert whole.equity.iloc[-1] == pytest.approx(1000 - qty * 100 * (1 + C) + qty * 150)
    assert whole.costs_paid == pytest.approx(qty * 100 * C)


# ------------------------------------------------------------------ report smoke test (tmp dir only)

class _FakeSource:
    def __init__(self, frames):
        self.frames = frames

    def get_bars(self, sym, start, end=None):
        if sym.symbol not in self.frames:
            raise FileNotFoundError(sym.symbol)
        return self.frames[sym.symbol]


def test_report_smoke(tmp_path, monkeypatch):
    import quantbot.strategies as strategies_mod
    from quantbot.backtest.report import run_backtest_report

    monkeypatch.setattr(strategies_mod, "get_strategy", lambda name, scfg: _named(MACross(), name),
                        raising=False)
    cfg = AppConfig(initial_capital=1000.0, symbols=(
        SymbolConfig(symbol="AAA", asset_class="stock"),
        SymbolConfig(symbol="BTC/USD", asset_class="crypto", data_symbol="IBIT"),
        SymbolConfig(symbol="SHORT", asset_class="stock"),
        SymbolConfig(symbol="MISSING", asset_class="stock"),
    ), data={"bars_dir": str(tmp_path / "bars")})
    src = _FakeSource({"AAA": random_bars(300, seed=3), "BTC/USD": random_bars(300, seed=4),
                       "SHORT": random_bars(10, seed=5)})
    path = run_backtest_report(cfg, tmp_path / "out", strategies=["trend"], source=src)
    text = path.read_text()
    assert "Past results do not guarantee future results" in text
    assert "PROXY" in text and "too little history" in text and "MISSING" in text
    data = json.loads((tmp_path / "out" / "latest_backtest.json").read_text())
    assert data["symbols"]["AAA"]["strategies"]["trend"]["status"] == "ok"
    assert data["symbols"]["SHORT"]["strategies"]["trend"]["status"] == "skipped"
    assert data["portfolio"]["trend"]["status"] == "ok"


def _named(strat, name):
    strat.name = name
    return strat
