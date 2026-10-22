"""Backtest crypto futures strategies (long AND short, optional leverage): `python -m trader.futuresbacktest`.

Daily closes from data/crypto/daily/ (Yahoo, 2016/2017 onward). A signal uses closes up to day t and is
held from t to t+1, so there is no look-ahead. Futures model, kept on the pessimistic side:
  fee        0.10% of the notional traded, per side (Coinbase's futures fee plus slippage)
  funding    longs pay 10% a year on the notional; shorts receive nothing
  ruin       if one day's loss takes equity under 25% of the day before (about where Coinbase's ~21-25%
             overnight margin would liquidate), the run is marked liquidated and ends at that value
Daily closes hide intraday swings, so real liquidations would come sooner than this shows.

Strategy families (each tried over a grid, to see whether the idea works or one setting got lucky):
  sma_ls    long above the n-day average, short below
  sma_lo    long above the n-day average, flat below (what spot can do)
  tsmom     long if the last L days' return is positive, short if negative
  donchian  long on a close above the prior N-day high, short on a close below the prior N-day low
  ensemble  average of sma_ls over 20/50/100/200 days (a position from -1 to +1)
Every family can be volatility-targeted: leverage = target_vol / 30-day realized vol, capped at max_lev.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("data/crypto/daily")
FEE = 0.001
FUNDING = 0.10 / 365
RUIN = 0.25
PERIODS = [("2018-01-01", "2018-12-31"), ("2019-01-01", "2020-12-31"), ("2021-01-01", "2022-12-31"),
           ("2023-01-01", "2024-12-31"), ("2025-01-01", "2026-12-31")]


def load(coin: str) -> pd.Series:
    s = pd.read_csv(DATA / f"{coin}.csv", index_col=0, parse_dates=True)["close"]
    return s.iloc[:-1]                                   # last row is an unfinished day


def signal(close: pd.Series, family: str, p: int) -> pd.Series:
    if family == "sma_ls":
        return np.sign(close - close.rolling(p).mean())
    if family == "sma_lo":
        return (close > close.rolling(p).mean()).astype(float)
    if family == "tsmom":
        return np.sign(close.pct_change(p))
    if family == "donchian":
        hi, lo = close.shift(1).rolling(p).max(), close.shift(1).rolling(p).min()
        raw = pd.Series(np.where(close > hi, 1.0, np.where(close < lo, -1.0, np.nan)), index=close.index)
        return raw.ffill().fillna(0.0)
    if family == "ensemble":
        return sum(np.sign(close - close.rolling(n).mean()) for n in (20, 50, 100, 200)) / 4
    raise ValueError(family)


def position(close: pd.Series, family: str, p: int, lev: float, target_vol: float | None) -> pd.Series:
    sig = signal(close, family, p).fillna(0.0)
    if target_vol:
        vol = close.pct_change().rolling(30).std() * np.sqrt(365)
        sig = sig * (target_vol / vol).clip(upper=lev)
    else:
        sig = sig * lev
    return sig.fillna(0.0)


def simulate(close: pd.Series, pos: pd.Series, start: str, end: str) -> dict:
    """Equity path over [start, end]; position decided at t's close earns t+1's return."""
    ret = close.pct_change()
    held = pos.shift(1)
    days = close.loc[start:end].index
    eq, peak, mdd, path, prev = 1.0, 1.0, 0.0, [], 0.0
    liquidated = False
    for d in days:
        p = held.get(d, 0.0)
        if np.isnan(p):
            p = 0.0
        cost = abs(p - prev) * FEE + (max(p, 0) * FUNDING)
        new = eq * (1 + p * ret[d] - cost)
        if new < eq * RUIN:
            eq, liquidated = max(new, 0.0), True
            path.append((d, eq))
            break
        eq, prev = new, p
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
        path.append((d, eq))
    curve = pd.Series([v for _, v in path], index=[d for d, _ in path])
    months = curve.resample("ME").last().pct_change().dropna()
    years = max(len(curve) / 365, 1e-9)
    return {"total": round(curve.iloc[-1] - 1, 4), "cagr": round(curve.iloc[-1] ** (1 / years) - 1, 4) if curve.iloc[-1] > 0 else -1.0,
            "max_dd": round(mdd, 4), "liquidated": liquidated,
            "monthly_median": round(float(months.median()), 4) if len(months) else 0.0,
            "months_up": round(float((months > 0).mean()), 2) if len(months) else 0.0}


GRID = {"sma_ls": [20, 50, 70, 100, 150, 200], "sma_lo": [20, 50, 70, 100, 150, 200],
        "tsmom": [7, 14, 28, 56, 84], "donchian": [20, 55, 100], "ensemble": [0]}
LEVERAGE = [(1, None), (2, None), (3, None), (2, 0.4), (3, 0.6)]   # (max leverage, vol target)


def grid(coins=("BTC", "ETH")) -> list[dict]:
    rows = []
    for coin in coins:
        close = load(coin)
        for fam, params in GRID.items():
            for p in params:
                for lev, tv in LEVERAGE:
                    pos = position(close, fam, p, lev, tv)
                    row = {"coin": coin, "family": fam, "p": p, "lev": lev, "tv": tv}
                    for a, b in PERIODS:
                        if close.loc[:a].shape[0] < 210:          # not enough warm-up for this coin
                            row[a[:4]] = None
                            continue
                        r = simulate(close, pos, a, b)
                        row[a[:4]] = r["total"] if not r["liquidated"] else "LIQ"
                    full = simulate(close, pos, "2019-01-01", "2026-12-31")
                    row.update({f"full_{k}": v for k, v in full.items()})
                    rows.append(row)
    return rows


def walk_forward(coins=("BTC", "ETH"), score: str = "cagr") -> dict:
    """Each period, trade the setting that scored best over all earlier periods (no peeking ahead)."""
    out = {}
    for coin in coins:
        close = load(coin)
        cands = [(f, p, lev, tv) for f, ps in GRID.items() for p in ps for lev, tv in LEVERAGE]
        pos = {c: position(close, *c) for c in cands}
        chain, picks = 1.0, []
        for i, (a, b) in enumerate(PERIODS[1:], start=1):
            past_start = PERIODS[0][0] if close.loc[:PERIODS[0][0]].shape[0] >= 210 else PERIODS[1][0]
            if past_start >= a:
                continue
            best = max(cands, key=lambda c: (lambda r: -9 if r["liquidated"] else r[score])(
                simulate(close, pos[c], past_start, PERIODS[i - 1][1])))
            r = simulate(close, pos[best], a, b)
            chain *= 1 + r["total"]
            picks.append({"period": a[:4], "pick": best, "result": r["total"], "max_dd": r["max_dd"],
                          "liquidated": r["liquidated"]})
        out[coin] = {"picks": picks, "chained_total": round(chain - 1, 4)}
    return out


if __name__ == "__main__":
    rows = grid()
    Path("reports").mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv("reports/futures_grid.csv", index=False)
    df = pd.DataFrame(rows)
    df["cfg"] = df["lev"].astype(str) + "x" + df["tv"].fillna(0).map(lambda v: f" vt{v:.0%}" if v else "")
    summary = df.groupby(["coin", "family", "cfg"]).agg(
        settings=("p", "count"), full_cagr_median=("full_cagr", "median"), full_cagr_worst=("full_cagr", "min"),
        max_dd_median=("full_max_dd", "median"), liquidated=("full_liquidated", "sum")).reset_index()
    print(summary.sort_values("full_cagr_median", ascending=False).to_string(index=False))
    print(json.dumps(walk_forward(), indent=1, default=str))
