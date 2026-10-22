"""Backtest the watchlab "gaps" idea on past daily bars: `python -m trader.gapbacktest`.

Buy at the open when a stock opens GAP_MIN or more below yesterday's close; sell at yesterday's close if
the day's high reaches it ("filled"), else at the day's close. Daily bars can't show the order of moves
within a day, so this is an estimate, and the live paper trades (checked hourly) are the real test.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

BARS = Path("data/bars")
COST = 0.001  # per side


def gap_trades(symbol: str, gap_min: float = 0.03, start: date = date(2019, 1, 1), folder: Path = BARS) -> list[float]:
    with open(folder / f"{symbol}.csv") as f:
        rows = [(date.fromisoformat(r["timestamp"][:10]), float(r["open"]), float(r["high"]), float(r["close"]))
                for r in csv.DictReader(f)]
    rows = [r for r in rows if r[0] >= start]
    out = []
    for (_, _, _, prev), (_, o, h, c) in zip(rows, rows[1:]):
        if o / prev - 1 <= -gap_min:
            out.append((prev if h >= prev else c) / o - 1 - 2 * COST)
    return out


if __name__ == "__main__":
    for g in (0.02, 0.03, 0.05):
        rets = [r for s in ("NVDA", "AMD", "PLTR", "QQQ") for r in gap_trades(s, g)]
        print(f"gap >= {g:.0%}: {len(rets)} trades, win {sum(r > 0 for r in rets) / len(rets):.0%}, "
              f"avg {sum(rets) / len(rets):+.2%}")
