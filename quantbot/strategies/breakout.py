"""Donchian channel breakout with volatility and volume filters."""

from __future__ import annotations

import pandas as pd

from ..config import BreakoutConfig
from ..indicators import atr, donchian, relative_volume
from .base import Strategy, empty_signals


class BreakoutStrategy(Strategy):
    name = "breakout"

    def __init__(self, cfg: BreakoutConfig | None = None) -> None:
        self.cfg = cfg or BreakoutConfig()

    @property
    def warmup_bars(self) -> int:
        c = self.cfg
        return max(c.donchian_entry + 1, c.donchian_exit + 1,
                   c.rel_volume_lookback + 1, c.atr_period + 1)

    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        c = self.cfg
        out = empty_signals(bars.index)
        if bars.empty:
            return out
        high, low = bars["high"].astype(float), bars["low"].astype(float)
        close = bars["close"].astype(float)
        a = atr(high, low, close, c.atr_period)
        atr_pct = a / close
        upper, _ = donchian(high, low, c.donchian_entry)
        _, exit_lower = donchian(high, low, c.donchian_exit)
        rvol = relative_volume(bars["volume"].astype(float), c.rel_volume_lookback)

        entry = ((close > upper) & (atr_pct >= c.min_atr_pct) & (atr_pct <= c.max_atr_pct)
                 & (rvol >= c.min_rel_volume))
        exit_ = close < exit_lower

        warm = pd.Series(range(len(bars)), index=bars.index) >= self.warmup_bars - 1
        out["entry"] = (entry & warm).fillna(False).astype(bool)
        out["exit"] = exit_.fillna(False).astype(bool)
        out["stop_distance"] = c.stop_atr_mult * a
        out["trail_distance"] = c.trail_atr_mult * a
        return out
