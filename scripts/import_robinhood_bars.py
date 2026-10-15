"""Convert saved Robinhood `get_equity_historicals` JSON answers into quantbot bar CSVs.

Used where Alpaca's data API is unreachable: an operator (or Claude, through the Robinhood
connector) saves the raw answer, and this script writes `data/bars/<SYMBOL>.csv` with a
`source` provenance column. With --merge, new bars are appended to the existing file.

    python scripts/import_robinhood_bars.py answer1.json [answer2.json ...] [--out data/bars] [--merge]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantbot.data.robinhood import historicals_to_frames, merge_into_csv  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("files", nargs="+")
    p.add_argument("--out", default="data/bars")
    p.add_argument("--merge", action="store_true", help="append to existing CSVs instead of replacing them")
    args = p.parse_args()
    fetched = datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")
    Path(args.out).mkdir(parents=True, exist_ok=True)
    for f in args.files:
        for sym, df in historicals_to_frames(json.loads(Path(f).read_text()), fetched).items():
            path = Path(args.out) / f"{sym}.csv"
            if args.merge:
                df = merge_into_csv(df, path)
            else:
                df.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
            bad = int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
            print(f"{sym}: {len(df)} bars {df.timestamp.min().date()} .. {df.timestamp.max().date()}"
                  f"  non-positive rows={bad}  high<low rows={int((df.high < df.low).sum())} -> {path}")


if __name__ == "__main__":
    main()
