"""Convert saved Robinhood `get_equity_historicals` JSON answers into quantbot bar CSVs.

Used in the build sandbox, where Alpaca's data API is unreachable: an operator (or Claude,
through the Robinhood connector) saves the raw answer, and this script writes
`data/bars/<SYMBOL>.csv` with a `source` provenance column. No values are altered except
dropping bars Robinhood marks `interpolated` (gap fill, no new information).

    python scripts/import_robinhood_bars.py answer1.json [answer2.json ...] [--out data/bars]
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def convert(raw: dict, fetched_at: str) -> dict[str, pd.DataFrame]:
    out = {}
    for result in raw["data"]["results"]:
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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="+")
    p.add_argument("--out", default="data/bars")
    args = p.parse_args()
    fetched = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    Path(args.out).mkdir(parents=True, exist_ok=True)
    for f in args.files:
        for sym, df in convert(json.loads(Path(f).read_text()), fetched).items():
            path = Path(args.out) / f"{sym}.csv"
            df.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
            bad = int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
            print(f"{sym}: {len(df)} bars {df.timestamp.min().date()} .. {df.timestamp.max().date()}"
                  f"  non-positive rows={bad}  high<low rows={int((df.high < df.low).sum())} -> {path}")


if __name__ == "__main__":
    main()
