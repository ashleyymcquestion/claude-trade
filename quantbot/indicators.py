"""Technical indicators. Pure, strictly backward-looking functions on pandas Series.

Every value at row ``t`` depends only on rows ``<= t`` (``donchian`` and ``relative_volume``
use only rows ``< t`` for their reference window). No ``shift(-n)``, no centered windows, so
truncating the input never changes earlier outputs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    """Simple moving average over the last ``n`` bars (NaN until ``n`` bars exist)."""
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int, min_periods: int | None = None) -> pd.Series:
    """Exponential moving average, span ``n``, recursive form (``adjust=False``).

    Seeded with the first value; NaN for the first ``min_periods`` - 1 bars (default ``n``)."""
    mp = n if min_periods is None else min_periods
    return series.ewm(span=n, adjust=False, min_periods=mp).mean()


def wilder(series: pd.Series, n: int) -> pd.Series:
    """Wilder smoothing (RMA): seeded with the SMA of the first ``n`` valid values, then
    ``r[t] = r[t-1] + (x[t] - r[t-1]) / n``. Leading NaNs are skipped; a NaN after the seed
    carries the previous value forward."""
    x = series.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) == 0:
        return pd.Series(out, index=series.index)
    start = valid[0]
    seed_end = start + n - 1
    window = x[start:seed_end + 1]
    if seed_end >= len(x) or np.isnan(window).any():
        # Not enough clean history to seed (or a hole inside the seed window): all NaN.
        return pd.Series(out, index=series.index)
    prev = window.mean()
    out[seed_end] = prev
    for i in range(seed_end + 1, len(x)):
        if not np.isnan(x[i]):
            prev = prev + (x[i] - prev) / n
        out[i] = prev
    return pd.Series(out, index=series.index)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """max(high-low, |high-prev_close|, |low-prev_close|); first bar is high-low."""
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    """Average true range with Wilder smoothing."""
    return wilder(true_range(high, low, close), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI in [0, 100]. Flat history (no gains, no losses) gives 50."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = wilder(gain, n)
    avg_loss = wilder(loss, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0, np.where(avg_gain > 0, 100.0, 50.0))
    return out.where(avg_gain.notna() & avg_loss.notna())


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(mid, upper, lower) with population standard deviation (ddof=0)."""
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return mid, mid + k * sd, mid - k * sd


def zscore(series: pd.Series, n: int) -> pd.Series:
    """(x - rolling mean) / rolling std over the last ``n`` bars (incl. current); NaN if std is 0."""
    mean = series.rolling(n, min_periods=n).mean()
    sd = series.rolling(n, min_periods=n).std(ddof=0)
    return (series - mean) / sd.where(sd > 0)


def donchian(high: pd.Series, low: pd.Series, n: int) -> tuple[pd.Series, pd.Series]:
    """(upper, lower) channel of the PRIOR ``n`` bars, excluding the current bar."""
    upper = high.rolling(n, min_periods=n).max().shift(1)
    lower = low.rolling(n, min_periods=n).min().shift(1)
    return upper, lower


def relative_volume(volume: pd.Series, n: int = 20) -> pd.Series:
    """Current volume divided by the mean volume of the PRIOR ``n`` bars (NaN if that mean is 0)."""
    ref = volume.rolling(n, min_periods=n).mean().shift(1)
    return volume / ref.where(ref > 0)


def momentum(close: pd.Series, n: int) -> pd.Series:
    """Percent change over ``n`` bars: close / close.shift(n) - 1."""
    return close / close.shift(n) - 1.0
