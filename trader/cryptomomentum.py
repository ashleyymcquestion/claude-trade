"""Backtest weekly momentum across Robinhood's crypto list: `python -m trader.cryptomomentum`.

Each week, rank the coins by their return over the last LOOKBACK weeks, keep the TOP coins that are also
above their 10-week average (the trend filter), and hold them equally for the next week. With
btc_filter, it only holds anything while Bitcoin is above its own 10-week average. Any week with
nothing qualifying sits in cash. Every change in holdings pays COST per side (Robinhood's crypto spread is
about 1.9% round trip, so 1% per side).

Data is data/crypto/weekly_closes.csv: 53 weekly closes (w0 = now, w52 = a year ago) for every coin
Robinhood lists, pulled from the Robinhood scanner with close(candleCount=7*k, candlePeriod="1d").
Caveats: one year of weekly data is short, and the list is today's coins (ones that were delisted are
missing), so treat a good result as a reason to paper trade, not proof.
"""

from __future__ import annotations

import csv
from pathlib import Path

FILE = Path("data/crypto/weekly_closes.csv")
COST = 0.01
MIN_TURNOVER = 100_000      # dollars traded today; drops coins Robinhood barely trades
SKIP = {"USDC"}
TREND_WEEKS = 10


def load(path: Path = FILE) -> tuple[dict[str, list[float | None]], dict[str, float]]:
    """{symbol: closes oldest first} with pre-listing filler removed, and {symbol: turnover}."""
    closes, turnover = {}, {}
    with open(path) as f:
        for r in csv.DictReader(f):
            vals = [float(r[f"w{i}"]) if r[f"w{i}"] else None for i in range(52, -1, -1)]
            # before a coin was listed the scanner repeats its first price; blank out that run
            first = next((i for i, v in enumerate(vals) if v is not None), len(vals))
            j = first
            while j + 1 < len(vals) and vals[j + 1] == vals[first]:
                j += 1
            if j > first:
                vals[first:j] = [None] * (j - first)
            closes[r["symbol"]] = vals
            turnover[r["symbol"]] = float(r["turnover_usd"] or 0)
    return closes, turnover


def universe(closes: dict, turnover: dict, min_turnover: float = MIN_TURNOVER) -> dict:
    return {s: c for s, c in closes.items() if s not in SKIP and turnover.get(s, 0) >= min_turnover}


def run(closes: dict, lookback: int = 4, top: int = 3, trend: bool = True, cost: float = COST,
        btc_filter: bool = False) -> dict:
    n = len(next(iter(closes.values())))
    start = max(lookback, TREND_WEEKS if trend or btc_filter else 0)
    value, held, weekly, trades = 1.0, set(), [], 0
    for t in range(start, n - 1):
        scores = []
        btc = closes["BTC"]
        if btc_filter and btc[t] <= sum(btc[t - TREND_WEEKS + 1:t + 1]) / TREND_WEEKS:
            closes_now = {}
        else:
            closes_now = closes
        for s, c in closes_now.items():
            if c[t] is None or c[t - lookback] is None or c[t + 1] is None:
                continue
            if trend:
                window = c[t - TREND_WEEKS + 1:t + 1]
                if None in window or c[t] <= sum(window) / TREND_WEEKS:
                    continue
            scores.append((c[t] / c[t - lookback] - 1, s))
        pick = {s for _, s in sorted(scores, reverse=True)[:top]}
        changed = len(pick ^ held)
        trades += changed
        slots = max(len(pick), len(held), 1)
        value *= 1 - cost * changed / slots
        ret = sum(closes[s][t + 1] / closes[s][t] - 1 for s in pick) / len(pick) if pick else 0.0
        value *= 1 + ret
        weekly.append(ret)
        held = pick
    value *= 1 - cost * (1 if held else 0)          # sell at the end
    return {"lookback": lookback, "top": top, "trend": trend, "btc_filter": btc_filter, "weeks": len(weekly),
            "total": round(value - 1, 4), "weeks_in_market": sum(r != 0 for r in weekly), "trades": trades}


def hold(closes: dict, symbol: str, lookback: int = 4, trend: bool = True) -> float:
    c = closes[symbol]
    start = max(lookback, TREND_WEEKS if trend else 0)
    return round(c[-1] / c[start] - 1, 4)


if __name__ == "__main__":
    import json

    closes, turnover = load()
    uni = universe(closes, turnover)
    print(json.dumps({"coins": sorted(uni)}))
    for trend, btc_filter in ((True, False), (False, False), (True, True)):
        for lookback in (2, 4, 8):
            for top in (1, 3, 5):
                print(json.dumps(run(uni, lookback, top, trend, btc_filter=btc_filter)))
    print(json.dumps({"hold_same_period": {s: hold(closes, s) for s in ("BTC", "ETH", "SOL")}}))
