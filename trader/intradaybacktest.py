"""Backtest two day-trading breakouts on 10-minute bars: `python -m trader.intradaybacktest`.

Both are long-only (the account can't short) and flat by the close, every trade pays SLIPPAGE per side:

  orb          opening range breakout: the high and low of the first RANGE_MIN minutes set the range; buy
               when the price breaks above the high, sell at the range low (stop) or near the close
  dual_thrust  range = max(highest high - lowest close, highest close - lowest low) over the last N days;
               buy when the price rises K x range above today's open, sell if it falls K x range below
               the open (stop) or near the close

Each runs two ways:
  ideal   watches every 10-minute bar and fills exactly at the trigger (a gap through it fills at the bar's open)
  hourly  the way our bots actually run: it only looks at :40 past each hour (10:40 ... 15:40 New York),
          enters or stops out at that bar's close, and sells whatever is left at the 15:40 check

Bars come from Robinhood (get_equity_historicals, interval 10minute, regular hours) saved in data/intraday/.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
DIR = Path("data/intraday")
SLIPPAGE = 0.0005           # per side
LAST_BAR = time(15, 50)     # ideal exit: the close of the bar starting 15:50
CHECKS = [time(h, 30) for h in range(10, 16)]   # hourly: bars starting 10:30 ... 15:30 close at :40


def load(symbol: str, folder: Path = DIR) -> dict:
    """{date: [(time, open, high, low, close), ...]} in New York time, oldest first."""
    days = defaultdict(list)
    with open(folder / f"{symbol}.csv") as f:
        for r in csv.DictReader(f):
            t = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).astimezone(NY)
            days[t.date()].append((t.time(), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    return {d: bars for d, bars in sorted(days.items()) if len(bars) >= 30}   # skip short/partial days


def _trade(entry: float, exit_: float) -> float:
    return exit_ * (1 - SLIPPAGE) / (entry * (1 + SLIPPAGE)) - 1


def _run(bars, start_i: int, trigger: float, stop: float, mode: str) -> tuple[float, str] | None:
    """Enter long above `trigger` from bar start_i on, stop at `stop`, flat by the close."""
    entry = None
    for t, o, h, l, c in bars[start_i:]:
        if mode == "ideal":
            if entry is None and t < LAST_BAR and h >= trigger:
                entry = max(trigger, o)
                if l <= stop and c <= stop:           # broke out and fell back through the stop in one bar
                    return _trade(entry, min(stop, c)), "stop"
                continue
            if entry is not None and l <= stop:
                return _trade(entry, min(stop, o)), "stop"
            if t >= LAST_BAR:
                return (_trade(entry, c), "close") if entry is not None else None
        else:
            if t not in CHECKS:
                continue
            if entry is None:
                if c >= trigger and t < CHECKS[-1]:
                    entry = c
                continue
            if c <= stop:
                return _trade(entry, c), "stop"
            if t == CHECKS[-1]:
                return _trade(entry, c), "close"
    return None


def orb(days: dict, range_min: int = 30, mode: str = "ideal") -> list[tuple]:
    n = range_min // 10
    out = []
    for d, bars in days.items():
        rng = bars[:n]
        hi, lo = max(b[2] for b in rng), min(b[3] for b in rng)
        r = _run(bars, n, hi, lo, mode)
        if r:
            out.append((d, *r))
    return out


def dual_thrust(days: dict, n: int = 4, k: float = 0.5, mode: str = "ideal") -> list[tuple]:
    daily = [(d, bars[0][1], max(b[2] for b in bars), min(b[3] for b in bars), bars[-1][4])
             for d, bars in days.items()]                       # date, open, high, low, close
    out = []
    for i in range(n, len(daily)):
        prev = daily[i - n:i]
        hh, ll = max(x[2] for x in prev), min(x[3] for x in prev)
        hc, lc = max(x[4] for x in prev), min(x[4] for x in prev)
        rng = max(hh - lc, hc - ll)
        d, o = daily[i][0], daily[i][1]
        r = _run(days[d], 0, o + k * rng, o - k * rng, mode)
        if r:
            out.append((d, *r))
    return out


def summary(trades: list[tuple]) -> dict:
    rets = [t[1] for t in trades]
    if not rets:
        return {"trades": 0}
    total = 1.0
    for r in rets:
        total *= 1 + r
    return {"trades": len(rets), "win_rate": round(sum(r > 0 for r in rets) / len(rets), 3),
            "avg": round(sum(rets) / len(rets), 5), "total": round(total - 1, 4),
            "stops": sum(t[2] == "stop" for t in trades)}


def hold_return(days: dict) -> float:
    ds = list(days.values())
    return round(ds[-1][-1][4] / ds[0][0][1] - 1, 4)


if __name__ == "__main__":
    import json

    syms = sorted(p.stem for p in DIR.glob("*.csv"))
    data = {s: load(s) for s in syms}
    for name, fn, kwargs in [("orb 30 min", orb, {"range_min": 30}), ("orb 60 min", orb, {"range_min": 60}),
                             ("dual thrust n4 k0.5", dual_thrust, {"n": 4, "k": 0.5}),
                             ("dual thrust n4 k0.3", dual_thrust, {"n": 4, "k": 0.3})]:
        for mode in ("ideal", "hourly"):
            per = {s: summary(fn(data[s], mode=mode, **kwargs)) for s in syms}
            allt = [t for s in syms for t in fn(data[s], mode=mode, **kwargs)]
            print(json.dumps({"strategy": name, "mode": mode, "all": summary(allt), "by_symbol": per}))
    print(json.dumps({"buy_and_hold": {s: hold_return(data[s]) for s in syms},
                      "days": {s: len(data[s]) for s in syms}}))
