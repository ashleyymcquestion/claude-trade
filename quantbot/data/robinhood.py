"""Robinhood `get_equity_historicals` answers -> quantbot bar CSVs (with provenance).

The Robinhood connector is reached by a Claude session, not by this code, so the session saves
the raw answer to a file and these helpers turn it into `data/bars/<SYMBOL>.csv`."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def historicals_to_frames(raw: dict, fetched_at: str) -> dict[str, pd.DataFrame]:
    """Raw answer -> {symbol: DataFrame[timestamp, open, high, low, close, volume, source]}.
    Bars marked `interpolated` (gap fill) are dropped; nothing else is changed."""
    out = {}
    data = raw.get("data", raw)
    for result in data["results"]:
        sym = result["symbol"]
        bars = [b for b in result["bars"] if not b.get("interpolated")]
        df = pd.DataFrame({
            "timestamp": pd.to_datetime([b["begins_at"] for b in bars], utc=True),
            "open": [float(b["open_price"]) for b in bars],
            "high": [float(b["high_price"]) for b in bars],
            "low": [float(b["low_price"]) for b in bars],
            "close": [float(b["close_price"]) for b in bars],
            "volume": [float(b["volume"]) for b in bars],
        })
        df["source"] = (f"robinhood get_equity_historicals interval={result.get('interval')} "
                        f"bounds={result.get('bounds')} adjustment=split fetched={fetched_at}")
        out[sym] = df.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    return out


def merge_into_csv(df: pd.DataFrame, path: str | Path) -> pd.DataFrame:
    """Append new bars to an existing CSV; a re-fetched bar replaces the stored one."""
    path = Path(path)
    if path.exists():
        old = pd.read_csv(path)
        old["timestamp"] = pd.to_datetime(old["timestamp"], utc=True)
        df = pd.concat([old, df], ignore_index=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    return df
