"""Backtest two classic strategies on long Yahoo daily history: `python -m trader.classics`.

Data: data/yahoo/<SYM>.csv (date, open, high, low, close, volume), daily bars from Yahoo's chart API.
Prices are split-adjusted, not dividend-adjusted (holding returns are slightly understated for both
the strategy and buy-and-hold alike).

turtle   Richard Dennis's Turtle rules, long only (the account can't short), one unit per symbol, no adding:
           N = 20-day average true range
           System 1: buy a break above the 20-day high, sell a break below the 10-day low
           System 2: buy a break above the 55-day high, sell a break below the 20-day low
           both: also sell if price falls 2N below the entry
         Breakouts fill at the breakout level (or the open if the day gaps through it), paying COST per side.
overnight  hold QQQ / SPY only from the close to the next open, versus only from the open to the close,
           versus all day. Paying COST twice a day is the catch, so both gross and net are shown.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

DIR = Path("data/yahoo")
COST = 0.0005              # per side for stocks/ETFs (slippage; no commission)
CRYPTO_COST = 0.01         # Robinhood's crypto spread is ~1% per side
TURTLES = {"system1": (20, 10), "system2": (55, 20)}
STOP_N = 2.0


def load(symbol: str, folder: Path = DIR) -> list[dict]:
    with open(folder / f"{symbol}.csv") as f:
        return [{"date": r["date"], **{k: float(r[k]) for k in ("open", "high", "low", "close")}}
                for r in csv.DictReader(f)]


def _cost(symbol: str) -> float:
    return CRYPTO_COST if symbol.endswith("-USD") else COST


def stats(equity: list[float], dates: list[str]) -> dict:
    """CAGR and max drawdown of a daily equity curve."""
    if len(equity) < 2:
        return {}
    years = max((len(equity) - 1) / 252, 1e-9)
    peak, mdd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"from": dates[0], "cagr": round((equity[-1] / equity[0]) ** (1 / years) - 1, 4),
            "total": round(equity[-1] / equity[0] - 1, 3), "max_drawdown": round(mdd, 3)}


def turtle(bars: list[dict], entry: int, exit_: int, cost: float = COST, start: str = "") -> dict:
    tr = [bars[0]["high"] - bars[0]["low"]] + [
        max(b["high"], p["close"]) - min(b["low"], p["close"]) for p, b in zip(bars, bars[1:])]
    eq, cash_eq, pos, trades, days_in = [], 1.0, None, [], 0
    first = max(entry, 20)
    for i in range(first, len(bars)):
        b = bars[i]
        if b["date"] < start:
            continue
        hi = max(x["high"] for x in bars[i - entry:i])
        lo = min(x["low"] for x in bars[i - exit_:i])
        n = sum(tr[i - 20:i]) / 20
        if pos is None:
            if b["high"] > hi:
                px = max(hi, b["open"])
                pos = {"entry": px * (1 + cost), "stop": px - STOP_N * n, "units": cash_eq / (px * (1 + cost))}
        else:
            stop = max(pos["stop"], lo)
            if b["low"] < stop:
                px = min(stop, b["open"])
                cash_eq = pos["units"] * px * (1 - cost)
                trades.append(px * (1 - cost) / pos["entry"] - 1)
                pos = None
        value = pos["units"] * b["close"] if pos else cash_eq
        days_in += pos is not None
        eq.append(value)
    dates = [b["date"] for b in bars[first:] if b["date"] >= start]
    out = stats(eq, dates)
    out.update(trades=len(trades), win_rate=round(sum(t > 0 for t in trades) / len(trades), 3) if trades else None,
               avg_trade=round(sum(trades) / len(trades), 4) if trades else None,
               time_in_market=round(days_in / max(len(eq), 1), 2))
    return out


def hold(bars: list[dict], start: str = "") -> dict:
    sel = [b for b in bars if b["date"] >= start]
    return stats([b["close"] for b in sel], [b["date"] for b in sel])


def overnight(bars: list[dict], cost: float = COST, start: str = "") -> dict:
    """Compound close->next open and open->close returns separately, gross and after paying cost both ways."""
    night = day = night_net = day_net = 1.0
    nd = [(p, b) for p, b in zip(bars, bars[1:]) if b["date"] >= start]
    for p, b in nd:
        r_n, r_d = b["open"] / p["close"], b["close"] / b["open"]
        night *= r_n
        day *= r_d
        night_net *= r_n * (1 - cost) ** 2
        day_net *= r_d * (1 - cost) ** 2
    years = len(nd) / 252
    cagr = lambda x: round(x ** (1 / years) - 1, 4)
    return {"from": nd[0][1]["date"], "years": round(years, 1),
            "overnight_cagr": cagr(night), "daytime_cagr": cagr(day),
            "overnight_net_cagr": cagr(night_net), "daytime_net_cagr": cagr(day_net),
            "hold_cagr": cagr(bars[-1]["close"] / nd[0][0]["close"])}


if __name__ == "__main__":
    import json

    syms = sorted(p.stem for p in DIR.glob("*.csv"))
    for start, label in (("", "full history"), ("2016-01-01", "since 2016"), ("2023-01-01", "since 2023")):
        print(f"\n== Turtle, {label} ==")
        for s in syms:
            bars = load(s)
            row = {"hold": hold(bars, start)}
            for name, (e, x) in TURTLES.items():
                row[name] = turtle(bars, e, x, _cost(s), start)
            print(s, json.dumps(row))
    print("\n== Overnight vs daytime ==")
    for s in ("QQQ", "SPY", "IWM", "NVDA"):
        for start in ("", "2016-01-01"):
            print(s, json.dumps(overnight(load(s), COST, start)))
