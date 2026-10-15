"""Market regime labels, strictly backward-looking.

trend:      close vs its 200-bar SMA (above = bull, at/below = bear)
volatility: ATR(14)/close vs that ratio's own rolling 252-bar median (min 60 bars)
            (above = volatile, at/below = quiet)
Rows without enough history for either input are "unknown".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr, sma

REGIMES = ("bull_quiet", "bull_volatile", "bear_quiet", "bear_volatile", "unknown")

TREND_SMA = 200
ATR_PERIOD = 14
VOL_MEDIAN_WINDOW = 252
VOL_MEDIAN_MIN_PERIODS = 60


def classify(bars: pd.DataFrame) -> pd.Series:
    close = bars["close"].astype(float)
    trend_ma = sma(close, TREND_SMA)
    atr_pct = atr(bars["high"].astype(float), bars["low"].astype(float), close, ATR_PERIOD) / close
    vol_ref = atr_pct.rolling(VOL_MEDIAN_WINDOW, min_periods=VOL_MEDIAN_MIN_PERIODS).median()

    known = trend_ma.notna() & atr_pct.notna() & vol_ref.notna()
    bull = close > trend_ma
    volatile = atr_pct > vol_ref
    label = np.where(bull, np.where(volatile, "bull_volatile", "bull_quiet"),
                     np.where(volatile, "bear_volatile", "bear_quiet"))
    out = pd.Series(label, index=bars.index, dtype=object)
    out[~known] = "unknown"
    out.name = "regime"
    return out
