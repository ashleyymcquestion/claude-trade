"""Backtest crypto day-trading rules on ~2 years of hourly bars: `python -m trader.cryptointraday [--fetch]`.

Bars: Yahoo hourly BTC/ETH/SOL-USD (data/crypto_1h/, not committed; --fetch downloads them). A "day" is a UTC
day. Every rule decides at an hourly close (the way our hourly bots run), fills at that close, and is flat by
the day's last close: no overnight positions.

Rules (each over a small grid, judged by whether the idea works across settings, coins and both halves):
  dual_thrust  long (and short) when the price moves k x the n-day range away from today's open; stop at the
               opposite trigger, else out at the day's last close
  mean_rev     after an hourly drop of z x the 7-day hourly volatility, buy; sell `hold` hours later
               (short version: sell short after a jump of the same size)
  day_mom      if the day is up at hour h (UTC), hold long for the rest of the day; short if it is down
  breakout     close above the prior 24-hour high: buy, sell `hold` hours later

Costs per side, three cases: 0.5% (Coinbase spot, maker limit orders at this account's tier), 0.1%
(Coinbase futures, pessimistic, as in futuresbacktest.py), 0.05% (a high-volume futures tier).
"""

from __future__ import annotations

import csv
import json
import math
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DIR = Path("data/crypto_1h")
COINS = ["BTC", "ETH", "SOL"]
COSTS = {"spot 0.5%": 0.005, "futures 0.1%": 0.001, "futures 0.05%": 0.0005}


def fetch(coin: str) -> None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{coin}-USD?interval=60m&range=730d"
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
                                         timeout=30))["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    DIR.mkdir(parents=True, exist_ok=True)
    with open(DIR / f"{coin}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close"])
        for i, t in enumerate(d["timestamp"]):
            if None not in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]):
                w.writerow([datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            q["open"][i], q["high"][i], q["low"][i], q["close"][i]])


def load(coin: str) -> list[tuple]:
    """[(datetime, open, high, low, close)] for whole hours only, oldest first."""
    rows = []
    with open(DIR / f"{coin}.csv") as f:
        for r in csv.DictReader(f):
            t = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
            if t.minute == 0:
                rows.append((t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    return rows


def by_day(bars: list[tuple]) -> dict:
    days = defaultdict(list)
    for b in bars:
        days[b[0].date()].append(b)
    return {d: b for d, b in sorted(days.items()) if len(b) == 24}     # full UTC days only


# Each rule returns [(date, gross return, side)]; costs are applied later.

def dual_thrust(bars, n=4, k=0.5, short=True):
    days = by_day(bars)
    keys = list(days)
    out = []
    for i in range(n, len(keys)):
        prev = [days[keys[j]] for j in range(i - n, i)]
        hh = max(b[2] for d in prev for b in d)
        ll = min(b[3] for d in prev for b in d)
        hc = max(d[-1][4] for d in prev)
        lc = min(d[-1][4] for d in prev)
        rng = max(hh - lc, hc - ll)
        today = days[keys[i]]
        o = today[0][1]
        up, dn = o + k * rng, o - k * rng
        pos, entry = 0, 0.0
        for j, (_, _, _, _, c) in enumerate(today):
            last = j == len(today) - 1
            if pos == 0 and not last:
                if c >= up:
                    pos, entry = 1, c
                elif short and c <= dn:
                    pos, entry = -1, c
                continue
            if pos and ((pos == 1 and c <= dn) or (pos == -1 and c >= up) or last):
                out.append((keys[i], pos * (c / entry - 1), pos))
                break
    return out


def mean_rev(bars, z=2.0, hold=3, short=False):
    out, rets = [], []
    i = 0
    closes = [b[4] for b in bars]
    while i < len(bars) - hold:
        r = closes[i] / closes[i - 1] - 1 if i else 0.0
        win = rets[-168:]
        rets.append(r)
        if len(win) == 168:
            sd = math.sqrt(sum(x * x for x in win) / len(win))
            side = 1 if r < -z * sd else (-1 if short and r > z * sd else 0)
            same_day = bars[i + hold][0].date() == bars[i][0].date()
            if side and same_day:
                out.append((bars[i][0].date(), side * (closes[i + hold] / closes[i] - 1), side))
                rets.extend(closes[j] / closes[j - 1] - 1 for j in range(i + 1, i + hold + 1))
                i += hold + 1
                continue
        i += 1
    return out


def day_mom(bars, h=12, short=True):
    out = []
    for d, b in by_day(bars).items():
        r = b[h - 1][4] / b[0][1] - 1              # open to the close of hour h
        side = 1 if r > 0 else (-1 if short and r < 0 else 0)
        if side:
            out.append((d, side * (b[-1][4] / b[h - 1][4] - 1), side))
    return out


def breakout(bars, hold=4):
    out, i = [], 24
    while i < len(bars) - hold:
        if bars[i][4] > max(b[2] for b in bars[i - 24:i]) and bars[i + hold][0].date() == bars[i][0].date():
            out.append((bars[i][0].date(), bars[i + hold][4] / bars[i][4] - 1, 1))
            i += hold + 1
        else:
            i += 1
    return out


GRID = [("dual_thrust", dual_thrust, [{"n": n, "k": k, "short": s} for n in (2, 4) for k in (0.3, 0.5, 0.7)
                                      for s in (False, True)]),
        ("mean_rev", mean_rev, [{"z": z, "hold": h, "short": s} for z in (2.0, 3.0) for h in (1, 3, 6)
                                for s in (False, True)]),
        ("day_mom", day_mom, [{"h": h, "short": s} for h in (6, 12, 18) for s in (False, True)]),
        ("breakout", breakout, [{"hold": h} for h in (2, 4, 8)])]


def stats(rets: list[float]) -> dict:
    if not rets:
        return {"trades": 0}
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / max(len(rets) - 1, 1)) or 1e-12
    return {"trades": len(rets), "avg": round(m, 5), "win": round(sum(r > 0 for r in rets) / len(rets), 3),
            "t": round(m / (sd / math.sqrt(len(rets))), 2)}


def report() -> list[dict]:
    data = {c: load(c) for c in COINS}
    rows = []
    for name, fn, settings in GRID:
        for kw in settings:
            trades = [(c, d, r) for c in COINS for d, r, _ in fn(data[c], **kw)]
            dates = sorted(d for _, d, _ in trades)
            mid = dates[len(dates) // 2] if dates else None
            row = {"rule": name, **kw, "gross": stats([r for _, _, r in trades])}
            for label, cost in COSTS.items():
                net = [r - 2 * cost for _, _, r in trades]
                row[label] = stats(net)
            net = [(c, d, r - 2 * COSTS["futures 0.1%"]) for c, d, r in trades]
            row["halves (futures 0.1%)"] = [stats([r for _, d, r in net if d < mid])["avg"] if net else None,
                                            stats([r for _, d, r in net if d >= mid])["avg"] if net else None]
            row["coins up (futures 0.1%)"] = sum(1 for c in COINS if stats([r for cc, _, r in net if cc == c])
                                                 .get("avg", -1) > 0)
            rows.append(row)
    return rows


if __name__ == "__main__":
    if "--fetch" in sys.argv:
        for c in COINS:
            fetch(c)
    for row in report():
        print(json.dumps(row))
