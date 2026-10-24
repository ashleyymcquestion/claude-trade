"""Backtest meme coins: `python -m trader.memebacktest`.

Data: Yahoo daily closes for 13 meme coins that still trade today (DOGE SHIB PEPE BONK WIF FLOKI BRETT POPCAT
MOG PENGU FARTCOIN SPX MEW) plus BTC, in data/crypto/meme/ (not committed). Big caveat: these are the
survivors. Thousands of meme coins launched and went to zero, and a "Kimchi-style" launch trade (buying a coin
like TRUMP in its first minutes) can't be tested here: there is no price history from before a coin is listed.

Rules, same costs everywhere (1% per side, about Robinhood's crypto spread; Coinbase limit orders are 0.5%):
  hold      buy each coin on its first listed day and hold
  momentum  each week hold the `top` coins with the best `lookback`-week return that are above their own
            10-week average, only while BTC is above its 10-week average (trader/cryptomomentum.py's rule)
  breakout  per coin: buy a close above the prior `n`-day high, sell a close below the prior `n//2`-day low
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

DIR = Path("data/crypto/meme")
COST = 0.01
MEMES = ["DOGE", "SHIB", "PEPE", "BONK", "WIF", "FLOKI", "BRETT", "POPCAT", "MOG", "PENGU", "FARTCOIN", "SPX", "MEW"]
PERIODS = [("2021-01-01", "2021-12-31"), ("2022-01-01", "2022-12-31"), ("2023-01-01", "2023-12-31"),
           ("2024-01-01", "2024-12-31"), ("2025-01-01", "2026-12-31")]


def load() -> pd.DataFrame:
    cols = {}
    for c in MEMES + ["BTC"]:
        with open(DIR / f"{c}.csv") as f:
            rows = list(csv.DictReader(f))
        cols[c] = pd.Series([float(r["close"]) for r in rows], index=pd.to_datetime([r["date"] for r in rows]))
    return pd.DataFrame(cols).sort_index()


def hold(px: pd.DataFrame, start: str, end: str) -> dict:
    out = {}
    for c in MEMES:
        s = px[c].loc[start:end].dropna()
        if len(s) > 30:
            out[c] = round(s.iloc[-1] / s.iloc[0] * (1 - COST) ** 2 - 1, 3)
    return out


def momentum(px: pd.DataFrame, start: str, end: str, lookback=4, top=1) -> float:
    wk = px.resample("W-SUN").last()
    btc_up = wk["BTC"] > wk["BTC"].rolling(10).mean()
    above = wk[MEMES] > wk[MEMES].rolling(10).mean()
    ret = wk[MEMES] / wk[MEMES].shift(lookback) - 1
    weeks = wk.loc[start:end].index
    value, held = 1.0, set()
    for i, t in enumerate(weeks[:-1]):
        nxt = weeks[i + 1]
        pick = set()
        if btc_up.get(t, False):
            r = ret.loc[t][above.loc[t]].dropna()
            pick = set(r.sort_values(ascending=False).index[:top])
        changed = len(pick ^ held)
        value *= 1 - COST * changed / max(len(pick), len(held), 1)
        if pick:
            value *= 1 + sum(wk.loc[nxt, c] / wk.loc[t, c] - 1 for c in pick) / len(pick)
        held = pick
    return round(value * (1 - COST * bool(held)) - 1, 3)


def breakout(px: pd.DataFrame, start: str, end: str, n=20) -> float:
    """Equal-weight average of per-coin breakout results over coins listed for the whole period."""
    res = []
    for c in MEMES:
        s = px[c].dropna()
        hi, lo = s.shift(1).rolling(n).max(), s.shift(1).rolling(n // 2).min()
        s, hi, lo = s.loc[start:end], hi.loc[start:end], lo.loc[start:end]
        if len(s) < 60 or s.index[0] > pd.Timestamp(start) + pd.Timedelta(days=30.0):
            continue
        value, entry = 1.0, None
        for t in s.index:
            if entry is None and s[t] > hi[t]:
                entry = s[t]
            elif entry is not None and s[t] < lo[t]:
                value *= s[t] / entry * (1 - COST) ** 2
                entry = None
        if entry is not None:
            value *= s.iloc[-1] / entry * (1 - COST) ** 2
        res.append(value - 1)
    return round(sum(res) / len(res), 3) if res else None


if __name__ == "__main__":
    px = load()
    for a, b in PERIODS:
        btc = px["BTC"].loc[a:b]
        row = {"period": a[:4] if a[:4] != "2025" else "2025-Oct 2026",
               "btc_hold": round(btc.iloc[-1] / btc.iloc[0] - 1, 3),
               "meme_hold": hold(px, a, b),
               "momentum": {f"{lb}wk top{tp}": momentum(px, a, b, lb, tp) for lb in (2, 4, 8) for tp in (1, 3)},
               "breakout": {f"{n}d": breakout(px, a, b, n) for n in (10, 20, 55)}}
        print(json.dumps(row))
