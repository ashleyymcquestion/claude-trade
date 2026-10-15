"""Bar validation. A symbol whose bars fail validation is skipped for that cycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import DataConfig
from ..models import BAR_COLUMNS, AssetClass

BAR_PERIOD = pd.Timedelta(days=1)  # timeframe is fixed to 1Day in DataConfig
_PRICE_COLS = ("open", "high", "low", "close")


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str] = field(default_factory=list)


def _utc(now: datetime | pd.Timestamp) -> pd.Timestamp:
    t = pd.Timestamp(now)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def completed_bars(df: pd.DataFrame, now: datetime | pd.Timestamp) -> pd.DataFrame:
    """Drop bars whose period has not ended yet (daily: ts + 1 day > now)."""
    if df.empty:
        return df
    return df[df.index + BAR_PERIOD <= _utc(now)]


def validate_bars(df: pd.DataFrame, asset_class: AssetClass, now: datetime | pd.Timestamp,
                  cfg: DataConfig) -> ValidationResult:
    issues: list[str] = []
    if df is None or df.empty:
        return ValidationResult(False, ["empty: no bars"])

    missing = [c for c in BAR_COLUMNS if c not in df.columns]
    if missing:
        return ValidationResult(False, [f"missing columns: {missing}"])

    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None:
        return ValidationResult(False, ["index is not a tz-aware DatetimeIndex"])

    data = df.loc[:, list(BAR_COLUMNS)]
    nan_rows = data.isna().any(axis=1)
    if nan_rows.any():
        issues.append(f"nan: {int(nan_rows.sum())} bar(s) with missing values, first at {idx[nan_rows.to_numpy()][0]}")

    prices = data.loc[:, list(_PRICE_COLS)]
    nonpos = (prices <= 0).any(axis=1)
    if nonpos.any():
        issues.append(f"non_positive_price: {int(nonpos.sum())} bar(s)")
    if (data["volume"] < 0).any():
        issues.append("negative_volume")

    hl = data["high"] < data["low"]
    if hl.any():
        issues.append(f"high_below_low: {int(hl.sum())} bar(s), first at {idx[hl.to_numpy()][0]}")
    tol = 1e-9 * data["close"].abs()
    outside = (data["close"] > data["high"] + tol) | (data["close"] < data["low"] - tol)
    if outside.any():
        issues.append(f"close_outside_range: {int(outside.sum())} bar(s)")

    if idx.has_duplicates:
        issues.append(f"duplicate_index: {int(idx.duplicated().sum())} duplicate timestamp(s)")
    if not idx.is_monotonic_increasing:
        issues.append("unsorted_index")

    # Gaps between consecutive bars, in missing bars.
    if len(idx) > 1 and idx.is_monotonic_increasing:
        days = idx.tz_convert("UTC").normalize().tz_localize(None).values.astype("datetime64[D]")
        if asset_class is AssetClass.STOCK:
            # business days strictly between consecutive bars (weekends never count)
            missing_bars = np.busday_count(days[:-1] + np.timedelta64(1, "D"), days[1:])
        else:
            missing_bars = (days[1:] - days[:-1]).astype(int) - 1
        too_long = missing_bars > cfg.max_gap_bars
        if too_long.any():
            worst = int(np.argmax(missing_bars))
            issues.append(f"gap: {int(too_long.sum())} gap(s) longer than {cfg.max_gap_bars} bars; "
                          f"largest {int(missing_bars[worst])} missing after {idx[worst]}")

    stale_hours = (cfg.crypto_stale_after_hours if asset_class is AssetClass.CRYPTO
                   else cfg.stock_stale_after_hours)
    age = _utc(now) - idx.max()
    if age > pd.Timedelta(hours=stale_hours):
        issues.append(f"stale: last bar {idx.max()} is {age.total_seconds() / 3600:.1f}h old "
                      f"(limit {stale_hours}h)")

    return ValidationResult(not issues, issues)
