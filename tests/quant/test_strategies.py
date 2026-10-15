"""Strategy and regime tests. Synthetic bars are generated here (seeded) and never written to disk."""

import numpy as np
import pandas as pd
import pytest

from quantbot import indicators as ind
from quantbot.config import BreakoutConfig, MeanReversionConfig, StrategiesConfig, TrendConfig
from quantbot.regime import REGIMES, classify
from quantbot.strategies import STRATEGY_NAMES, build_strategies, get_strategy
from quantbot.strategies.base import SIGNAL_COLUMNS
from quantbot.strategies.breakout import BreakoutStrategy
from quantbot.strategies.mean_reversion import MeanReversionStrategy
from quantbot.strategies.trend import TrendStrategy


def synthetic_bars(n: int = 900, seed: int = 42) -> pd.DataFrame:
    """Regime-switching random walk with OHLCV; deterministic for a seed."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-0.002, 0.0, 0.0025], size=n // 60 + 1), 60)[:n]
    vol = np.repeat(rng.choice([0.01, 0.025], size=n // 90 + 1), 90)[:n]
    ret = drift + vol * rng.standard_normal(n)
    close = 100 * np.exp(np.cumsum(ret))
    open_ = close * np.exp(vol * 0.3 * rng.standard_normal(n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol * 0.6, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol * 0.6, n)))
    volume = rng.lognormal(13, 0.5, n)
    idx = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)


ALL_STRATEGIES = [
    TrendStrategy(TrendConfig()),
    TrendStrategy(TrendConfig(ema_fast=5, ema_slow=12, sma_long=30, min_rel_volume=0.0)),
    MeanReversionStrategy(MeanReversionConfig()),
    MeanReversionStrategy(MeanReversionConfig(regime_sma=50, rsi_entry=25)),
    BreakoutStrategy(BreakoutConfig()),
    BreakoutStrategy(BreakoutConfig(donchian_entry=10, donchian_exit=5, min_rel_volume=0.5)),
]
IDS = [f"{s.name}-{i}" for i, s in enumerate(ALL_STRATEGIES)]


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=IDS)
def test_no_look_ahead(strategy):
    """Signals up to t must be identical whether or not later bars exist."""
    bars = synthetic_bars()
    full = strategy.signals(bars)
    for t in list(range(5, len(bars), 23)) + [len(bars) - 1]:
        part = strategy.signals(bars.iloc[:t])
        pd.testing.assert_frame_equal(part, full.iloc[:t], check_freq=False)


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=IDS)
def test_no_look_ahead_future_perturbation(strategy):
    """Changing the future (wildly) must not change past rows."""
    bars = synthetic_bars(seed=3)
    full = strategy.signals(bars)
    cut = 600
    shocked = bars.copy()
    shocked.iloc[cut:, :4] *= 3.0
    shocked.iloc[cut:, 4] *= 10.0
    pd.testing.assert_frame_equal(strategy.signals(shocked).iloc[:cut], full.iloc[:cut])


def test_strategies_actually_trade_on_synthetic_data():
    """Guard against vacuous no-look-ahead tests: each strategy type produces some entries and exits."""
    bars = synthetic_bars()
    for name in ("trend", "mean_reversion", "breakout"):
        sigs = [s.signals(bars) for s in ALL_STRATEGIES if s.name == name]
        assert any(x["entry"].any() for x in sigs), name
        assert any(x["exit"].any() for x in sigs), name


@pytest.mark.parametrize("strategy", ALL_STRATEGIES, ids=IDS)
def test_signal_frame_shape_and_warmup(strategy):
    bars = synthetic_bars()
    sig = strategy.signals(bars)
    assert tuple(sig.columns) == SIGNAL_COLUMNS
    assert sig.index.equals(bars.index)
    assert sig["entry"].dtype == bool and sig["exit"].dtype == bool
    assert not sig["entry"].iloc[: strategy.warmup_bars - 1].any()
    # short history: nothing at all
    short = strategy.signals(bars.iloc[: strategy.warmup_bars - 1])
    assert not short["entry"].any()
    assert tuple(strategy.signals(bars.iloc[:0]).columns) == SIGNAL_COLUMNS


def test_stop_trail_hold_columns():
    bars = synthetic_bars()
    t = TrendStrategy(TrendConfig()).signals(bars)
    assert (t["stop_distance"].dropna() > 0).all() and t["trail_distance"].notna().any()
    assert t["max_hold_bars"].isna().all()
    m = MeanReversionStrategy(MeanReversionConfig(max_hold_bars=7)).signals(bars)
    assert m["trail_distance"].isna().all() and (m["max_hold_bars"] == 7).all()
    b = BreakoutStrategy(BreakoutConfig()).signals(bars)
    assert b["trail_distance"].notna().any() and b["max_hold_bars"].isna().all()
    # where an entry fires, a stop distance must be defined
    for sig in (t, m, b):
        assert sig.loc[sig["entry"], "stop_distance"].notna().all()


def test_mean_reversion_regime_filter_blocks_entries_below_sma():
    cfg = MeanReversionConfig(regime_sma=50, rsi_entry=25)
    bars = synthetic_bars()
    sig = MeanReversionStrategy(cfg).signals(bars)
    below = bars["close"] <= bars["close"].rolling(50).mean()
    assert below.any()
    assert not sig.loc[below, "entry"].any()

    # Sharp one-bar dips on a steady downtrend vs. on a steady uptrend. The oversold conditions
    # (RSI + band/z-score) fire in both; only the regime filter differs.
    dip_cfg = MeanReversionConfig(regime_sma=50, rsi_entry=25, bb_std=1.5, z_entry=-1.5)
    n = 300
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")

    def dipped(growth):
        c = pd.Series(100 * growth ** np.arange(n), index=idx)
        c.iloc[100::40] *= 0.96
        return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6},
                            index=idx)

    def oversold(b):
        c = b["close"]
        _, _, lower = ind.bollinger(c, dip_cfg.bb_period, dip_cfg.bb_std)
        return (ind.rsi(c, dip_cfg.rsi_period) < dip_cfg.rsi_entry) & (
            (c < lower) | (ind.zscore(c, dip_cfg.z_period) < dip_cfg.z_entry))

    down, up = dipped(0.998), dipped(1.002)
    assert oversold(down).iloc[60:].any() and oversold(up).iloc[60:].any()
    assert not MeanReversionStrategy(dip_cfg).signals(down)["entry"].any()
    assert MeanReversionStrategy(dip_cfg).signals(up)["entry"].any()


def test_breakout_entry_requires_close_above_prior_channel():
    bars = synthetic_bars()
    cfg = BreakoutConfig(donchian_entry=10, donchian_exit=5, min_rel_volume=0.5)
    sig = BreakoutStrategy(cfg).signals(bars)
    prior_high = bars["high"].rolling(10).max().shift(1)
    assert sig["entry"].any()
    assert (bars.loc[sig["entry"], "close"] > prior_high[sig["entry"]]).all()


def test_regime_no_look_ahead_and_labels():
    bars = synthetic_bars(n=1000)
    full = classify(bars)
    assert set(full.unique()) <= set(REGIMES)
    assert (full.iloc[:199] == "unknown").all()
    assert (full.iloc[300:] != "unknown").all()
    assert len(set(full.iloc[300:])) >= 3
    for t in range(10, len(bars), 31):
        pd.testing.assert_series_equal(classify(bars.iloc[:t]), full.iloc[:t])


def test_registry():
    assert STRATEGY_NAMES == ("trend", "mean_reversion", "breakout")
    cfg = StrategiesConfig()
    assert [s.name for s in build_strategies(cfg)] == list(STRATEGY_NAMES)
    cfg2 = cfg.model_copy(update={"mean_reversion": MeanReversionConfig(enabled=False)})
    assert [s.name for s in build_strategies(cfg2)] == ["trend", "breakout"]
    over = cfg.model_copy(update={"trend": TrendConfig(ema_fast=10, ema_slow=30, sma_long=100)})
    t = get_strategy("trend", over)
    assert isinstance(t, TrendStrategy) and t.cfg.ema_fast == 10 and t.warmup_bars == 100
    assert get_strategy("mean_reversion", cfg2).name == "mean_reversion"  # disabled still buildable
    with pytest.raises(KeyError):
        get_strategy("nope", cfg)
