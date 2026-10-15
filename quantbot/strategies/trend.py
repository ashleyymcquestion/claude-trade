"""Trend following: ride established uptrends with confirmed momentum and participation."""

from __future__ import annotations

import pandas as pd

from ..config import TrendConfig
from ..indicators import atr, ema, momentum, relative_volume, sma
from .base import Strategy, empty_signals


class TrendStrategy(Strategy):
    name = "trend"

    def __init__(self, cfg: TrendConfig | None = None) -> None:
        self.cfg = cfg or TrendConfig()

    @property
    def warmup_bars(self) -> int:
        c = self.cfg
        return max(c.sma_long, c.ema_slow, c.momentum_lookback + 1,
                   c.rel_volume_lookback + 1, c.atr_period + 1)

    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        c = self.cfg
        out = empty_signals(bars.index)
        if bars.empty:
            return out
        close = bars["close"].astype(float)
        a = atr(bars["high"].astype(float), bars["low"].astype(float), close, c.atr_period)
        fast, slow = ema(close, c.ema_fast), ema(close, c.ema_slow)
        long_ma = sma(close, c.sma_long)
        mom = momentum(close, c.momentum_lookback)
        rvol = relative_volume(bars["volume"].astype(float), c.rel_volume_lookback)

        entry = ((close > long_ma) & (fast > slow) & (mom > 0)
                 & (rvol >= c.min_rel_volume) & (a / close <= c.max_atr_pct))
        exit_ = (fast < slow) | (close < long_ma)

        warm = pd.Series(range(len(bars)), index=bars.index) >= self.warmup_bars - 1
        out["entry"] = (entry & warm).fillna(False).astype(bool)
        out["exit"] = exit_.fillna(False).astype(bool)
        out["stop_distance"] = c.stop_atr_mult * a
        out["trail_distance"] = c.trail_atr_mult * a
        return out
