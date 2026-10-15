"""Walk-forward: window layout, train-only selection, no OOS trade before its test window."""

from __future__ import annotations

import pandas as pd
import pytest

import quantbot.backtest.walkforward as wfmod
import quantbot.strategies as strategies_mod
from quantbot.config import StrategiesConfig, SymbolConfig

from .test_backtest import MACross, make_asset, make_cfg, random_bars

SYM = SymbolConfig(symbol="TEST", asset_class="stock")


def toy_factory(name: str, scfg: StrategiesConfig):
    """Stands in for quantbot.strategies.get_strategy: a backward-looking MA cross driven by
    the trend sub-config (so grid overrides actually change behaviour)."""
    assert name == "trend"
    s = MACross(fast=scfg.trend.ema_fast, slow=scfg.trend.ema_slow)
    s.name = name
    return s


@pytest.fixture(autouse=True)
def _toy_strategies(monkeypatch):
    monkeypatch.setattr(strategies_mod, "get_strategy", toy_factory, raising=False)


def _run(bars, **kw):
    return wfmod.walk_forward(bars, "trend", SYM, make_asset(), make_cfg(), train_bars=300, test_bars=100, **kw)


def test_window_layout_and_no_trade_before_test_start():
    bars = random_bars(900, seed=11)
    wf = _run(bars)
    assert len(wf.windows) == 6
    prev_end = None
    for w in wf.windows:
        assert w["train_start"] < w["train_end"] < w["test_start"] <= w["test_end"]
        if prev_end is not None:
            assert w["test_start"] > prev_end            # test windows never overlap
        prev_end = w["test_end"]
        assert w["chosen_params"].keys() == {"ema_fast", "ema_slow"}
    starts = [w["test_start"] for w in wf.windows]
    ends = [w["test_end"] for w in wf.windows]
    assert len(wf.oos_trades) > 0
    for _, t in wf.oos_trades.iterrows():
        k = max(i for i, s in enumerate(starts) if s <= t.entry_ts)
        assert starts[k] <= t.entry_ts <= ends[k] and t.exit_ts <= ends[k]
    assert wf.oos_equity.index[0] == starts[0]
    assert wf.oos_equity.index.is_monotonic_increasing and not wf.oos_equity.index.has_duplicates
    assert len(wf.oos_equity) == 900 - 300


def test_selection_only_sees_train_data(monkeypatch):
    bars = random_bars(700, seed=12)
    seen = []
    real = wfmod.select_params

    def spy(train, *a, **k):
        seen.append(train.index[-1])
        return real(train, *a, **k)

    monkeypatch.setattr(wfmod, "select_params", spy)
    wf = _run(bars)
    assert len(seen) == len(wf.windows) == 4
    for last_train_ts, w in zip(seen, wf.windows):
        assert last_train_ts == w["train_end"] < w["test_start"]


def test_chosen_params_unaffected_by_future_data():
    bars = random_bars(700, seed=13)
    a = _run(bars)
    changed = bars.copy()
    cut = 300  # first test window starts here
    changed.iloc[cut:] = changed.iloc[cut:] * 1.5
    b = _run(changed)
    assert a.windows[0]["chosen_params"] == b.windows[0]["chosen_params"]
    assert a.windows[0]["train_sharpe"] == b.windows[0]["train_sharpe"]


def test_oos_equity_continues_from_previous_window():
    bars = random_bars(900, seed=14)
    wf = _run(bars)
    eq = wf.oos_equity
    # each window starts from the previous window's ending equity: the first bar of a window
    # can only differ from the previous close by that bar's own trading P&L
    for w in wf.windows[1:]:
        before = eq[eq.index < w["test_start"]].iloc[-1]
        first_bar = eq.loc[w["test_start"]]
        assert abs(first_bar / before - 1) < 0.05
    m = wfmod.compute_metrics(wf.as_backtest_result(), 252)
    assert m["bars"] == len(eq) and m["num_trades"] == len(wf.oos_trades)


def test_default_wins_ties_and_too_short_history():
    bars = random_bars(250, seed=15)
    wf = _run(bars)
    assert wf.windows == [] and "note" in wf.meta and wf.oos_equity.empty
    # a flat train window gives no trades anywhere -> configured default (20, 50) chosen
    flat = random_bars(500, seed=16)
    flat[["open", "high", "low", "close"]] = 100.0
    wf2 = _run(flat)
    assert all(w["chosen_params"] == {"ema_fast": 20, "ema_slow": 50} for w in wf2.windows)


def test_default_grids_are_small():
    for name, grid in wfmod.DEFAULT_GRIDS.items():
        n = 1
        for v in grid.values():
            n *= len(v)
        assert n <= 6, name
