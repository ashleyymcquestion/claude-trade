"""Paper day-trading practice on TTWO (paper only, never real money).

The user asked on Nov 10, 2026: "practice day trading ttwo", then "what strategy do you think is best?".
A backtest on 70 days of TTWO 10-minute bars (Aug 3 - Nov 9) found no clear edge: Dual Thrust (n 4, k 0.3)
+1.9% over 18 trades, opening range breakout (30 min) -11.9% over 35, holding TTWO -15.9%. So both are
tracked side by side from START, with $100 paper each, long only, flat by the close, using
trader/intradaybacktest.py (ideal fills on 10-minute bars, 0.05% slippage per side).

Each evening run appends the day's 10-minute bars (get_equity_historicals TTWO, interval 10minute, regular)
to data/intraday/ttwo/TTWO.csv with `python -m trader daytrade-paper --bars <json>`; the report covers the
days from START on.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from . import intradaybacktest as ib

DIR = Path("data/intraday/ttwo")
SYMBOL = "TTWO"
START = date(2026, 11, 11)
STAKE = 100.0
STRATEGIES = {"dual_thrust_k0.3": (ib.dual_thrust, {"n": 4, "k": 0.3}), "orb_30": (ib.orb, {"range_min": 30})}


def append_bars(raw: dict, folder: Path = DIR) -> int:
    """Add new non-interpolated bars from a get_equity_historicals response; returns how many were added."""
    path = folder / f"{SYMBOL}.csv"
    seen = {r["timestamp"] for r in csv.DictReader(open(path))} if path.exists() else set()
    rows = [b for res in raw["data"]["results"] if res["symbol"] == SYMBOL for b in res["bars"]
            if not b.get("interpolated") and b["begins_at"] not in seen]
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        for b in sorted(rows, key=lambda b: b["begins_at"]):
            w.writerow([b["begins_at"], b["open_price"], b["high_price"], b["low_price"], b["close_price"], b["volume"]])
    return len(rows)


def report(folder: Path = DIR, start: date = START) -> dict:
    days = ib.load(SYMBOL, folder)
    out = {}
    for name, (fn, kw) in STRATEGIES.items():
        trades = [t for t in fn(days, mode="ideal", **kw) if t[0] >= start]
        value = STAKE
        for t in trades:
            value *= 1 + t[1]
        out[name] = {**ib.summary(trades), "value": round(value, 2),
                     "last": [[str(t[0]), round(t[1] * 100, 2), t[2]] for t in trades[-3:]]}
    since = [d for d in days if d >= start]
    if since:
        out["hold"] = {"value": round(STAKE * days[since[-1]][-1][4] / days[since[0]][0][1], 2), "days": len(since)}
    return out


def main(bars_file: str | None) -> dict:
    added = append_bars(json.loads(Path(bars_file).read_text())) if bars_file else 0
    return {"bars_added": added, **report()}
