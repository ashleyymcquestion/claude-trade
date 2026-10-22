"""Dual Thrust on ~3 years of hourly bars for 24 stocks/ETFs: `python -m trader.dtbacktest [--fetch]`.

The live paper test (trader/watchlab.py, `dual_thrust`) trades NVDA/AMD/PLTR/QQQ with hourly checks, so
it would take ~1.5 years to reach 100 trades. This replays the same rule on Yahoo's hourly bars
(split-adjusted, Nov 2023 onward) for those 4 and 20 symbols the rule was never tuned on.

Rule, as the bot runs it: range = max(highest high - lowest close, highest close - lowest low) over the
previous N days; at each hourly bar close from 10:30 to 14:30 New York, buy if the close is >= today's open +
K x range; after entry, sell at an hourly close <= open - K x range (stop) or at the 15:30 close. 0.05%
slippage per side. One trade per symbol per day, long only.

--fetch downloads the bars into data/intraday_1h/ (not committed; about 12 MB).
"""

from __future__ import annotations

import csv
import json
import math
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
DIR = Path("data/intraday_1h")
SLIPPAGE = 0.0005
ORIGINAL = ["NVDA", "AMD", "PLTR", "QQQ"]
NEW = ["SPY", "IWM", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "NFLX", "COIN", "MU",
       "JPM", "XOM", "UNH", "LLY", "COST", "CRM", "ORCL", "UBER"]
ENTRY_CLOSES = [time(h, 30) for h in range(10, 15)]        # bars closing 10:30 ... 14:30
LAST_CLOSE = time(15, 30)


def fetch(symbol: str) -> None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=60m&range=730d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=30))["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    DIR.mkdir(parents=True, exist_ok=True)
    with open(DIR / f"{symbol}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for i, t in enumerate(d["timestamp"]):
            if None not in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]):
                w.writerow([datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]])


def load(symbol: str) -> dict:
    """{date: [(bar close time NY, open, high, low, close)]}, full regular-hours days only."""
    days = defaultdict(list)
    with open(DIR / f"{symbol}.csv") as f:
        for r in csv.DictReader(f):
            start = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).astimezone(NY)
            end = time(min(start.hour + 1, 16), 0 if start.hour + 1 >= 16 else start.minute)
            days[start.date()].append((end, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    return {d: b for d, b in sorted(days.items()) if len(b) >= 7}


def trades(days: dict, n: int = 4, k: float = 0.5) -> list[tuple]:
    daily = [(d, b[0][1], max(x[2] for x in b), min(x[3] for x in b), b[-1][4]) for d, b in days.items()]
    out = []
    for i in range(n, len(daily)):
        prev = daily[i - n:i]
        rng = max(max(x[2] for x in prev) - min(x[4] for x in prev), max(x[4] for x in prev) - min(x[3] for x in prev))
        d, o = daily[i][0], daily[i][1]
        trigger, stop, entry = o + k * rng, o - k * rng, None
        for t, _, _, _, c in days[d]:
            if entry is None:
                if t in ENTRY_CLOSES and c >= trigger:
                    entry = c
                continue
            if c <= stop or t == LAST_CLOSE:
                out.append((d, c * (1 - SLIPPAGE) / (entry * (1 + SLIPPAGE)) - 1))
                break
    return out


def stats(rets: list[float]) -> dict:
    if not rets:
        return {"trades": 0}
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / max(len(rets) - 1, 1))
    return {"trades": len(rets), "win_rate": round(sum(r > 0 for r in rets) / len(rets), 3),
            "avg": round(m, 5), "t_stat": round(m / (sd / math.sqrt(len(rets))), 2) if sd else None}


def report() -> dict:
    data = {s: load(s) for s in ORIGINAL + NEW}
    out = {}
    for n, k in [(4, 0.5), (2, 0.5), (6, 0.5), (4, 0.3), (4, 0.7)]:
        tr = {s: trades(data[s], n, k) for s in data}
        allr = [(d, r) for s in tr for d, r in tr[s]]
        mid = sorted(d for d, _ in allr)[len(allr) // 2]
        out[f"n{n} k{k}"] = {
            "all": stats([r for _, r in allr]),
            "original 4": stats([r for s in ORIGINAL for _, r in tr[s]]),
            "new 20": stats([r for s in NEW for _, r in tr[s]]),
            "first half": stats([r for d, r in allr if d < mid]),
            "second half": stats([r for d, r in allr if d >= mid]),
            "symbols with avg > 0": sum(1 for s in data if tr[s] and sum(r for _, r in tr[s]) > 0),
        }
    return out


if __name__ == "__main__":
    if "--fetch" in sys.argv:
        for s in ORIGINAL + NEW:
            fetch(s)
    print(json.dumps(report(), indent=1, default=str))
