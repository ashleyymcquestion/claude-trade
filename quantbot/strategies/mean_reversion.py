"""Short-horizon mean reversion: buy sharp oversold dips inside a long-term uptrend."""

from __future__ import annotations

import pandas as pd

from ..config import MeanReversionConfig
from ..indicators import atr, bollinger, rsi, sma, zscore
from .base import Strategy, empty_signals


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(self, cfg: MeanReversionConfig | None = None) -> None:
        self.cfg = cfg or MeanReversionConfig()

    @property
    def warmup_bars(self) -> int:
        c = self.cfg
        return max(c.regime_sma, c.bb_period, c.z_period, c.rsi_period + 1, c.atr_period + 1)

    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        c = self.cfg
        out = empty_signals(bars.index)
        if bars.empty:
            return out
        close = bars["close"].astype(float)
        a = atr(bars["high"].astype(float), bars["low"].astype(float), close, c.atr_period)
        r = rsi(close, c.rsi_period)
        mid, _upper, lower = bollinger(close, c.bb_period, c.bb_std)
        z = zscore(close, c.z_period)
        regime_ok = close > sma(close, c.regime_sma)

        entry = (r < c.rsi_entry) & ((close < lower) | (z < c.z_entry)) & regime_ok
        exit_ = (r > c.rsi_exit) | (close > mid)

        warm = pd.Series(range(len(bars)), index=bars.index) >= self.warmup_bars - 1
        out["entry"] = (entry & warm).fillna(False).astype(bool)
        out["exit"] = exit_.fillna(False).astype(bool)
        out["stop_distance"] = c.stop_atr_mult * a
        out["max_hold_bars"] = float(c.max_hold_bars)
        return out
