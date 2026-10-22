"""Backtest buying calls on past daily bars, with option prices modeled (no real option history yet).

For each symbol in data/bars/, walk the daily closes and buy a ~35-day at-the-money call whenever the
entry signal fires (one position per symbol at a time). Exits match the options lab: +100%, -50%, or
7 days before expiration. Each trade is also compared with simply buying the stock on the same days.

Option prices come from Black-Scholes, because free historical option chains aren't available:
  implied vol = realized 30-day vol x IV_MARKUP   (on Oct 21 the lab saw implied 1.1-1.6x realized)
  buy at model x (1 + HALF_SPREAD), sell at model x (1 - HALF_SPREAD)
That makes the results an estimate, not a record: real fills, earnings jumps and volatility spikes
differ. Treat a strategy as promising only if it still wins with a pessimistic markup.

Signals:
  momentum  close above its 50-day average, the average rising, and up 5%+ over 20 days
  pullback  close above its 200-day average but RSI(14) under 35 (the stock bot's mean-reversion idea)
"""

from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path

BARS = Path("data/bars")
DTE = 35
CLOSE_DTE = 7
TARGET, STOP = 1.0, -0.5
RATE = 0.04
IV_MARKUP = 1.3
HALF_SPREAD = 0.05
VOL_DAYS = 30
SIGNALS = ("momentum", "pullback")


def load_bars(symbol: str, folder: Path = BARS) -> list[tuple[date, float]]:
    with open(folder / f"{symbol}.csv") as f:
        return [(date.fromisoformat(r["timestamp"][:10]), float(r["close"])) for r in csv.DictReader(f)]


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_call(spot: float, strike: float, years: float, vol: float, rate: float = RATE) -> float:
    if years <= 0 or vol <= 0:
        return max(spot - strike, 0.0)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    return spot * _ncdf(d1) - strike * math.exp(-rate * years) * _ncdf(d2)


def realized_vol(closes: list[float]) -> float | None:
    if len(closes) < VOL_DAYS + 1:
        return None
    rets = [math.log(b / a) for a, b in zip(closes[-VOL_DAYS - 1:-1], closes[-VOL_DAYS:])]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var * 252) or None


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) < n + 1:
        return None
    moves = [b - a for a, b in zip(closes[-n - 1:-1], closes[-n:])]
    up = sum(m for m in moves if m > 0) / n
    down = -sum(m for m in moves if m < 0) / n
    return 100.0 if down == 0 else 100 - 100 / (1 + up / down)


def signal(name: str, closes: list[float]) -> bool:
    if name == "momentum":
        if len(closes) < 56:
            return False
        sma = sum(closes[-50:]) / 50
        sma_before = sum(closes[-55:-5]) / 50
        return closes[-1] > sma > sma_before and closes[-1] / closes[-21] - 1 >= 0.05
    if name == "pullback":
        if len(closes) < 200:
            return False
        r = rsi(closes)
        return closes[-1] > sum(closes[-200:]) / 200 and r is not None and r < 35
    raise ValueError(name)


def run(bars: list[tuple[date, float]], sig: str, start: date | None = None,
        iv_markup: float = IV_MARKUP, half_spread: float = HALF_SPREAD) -> list[dict]:
    closes = [c for _, c in bars]
    trades, i = [], 0
    while i < len(bars) - 1:
        day, spot = bars[i]
        hist = closes[: i + 1]
        if (start and day < start) or not signal(sig, hist) or (rv := realized_vol(hist)) is None:
            i += 1
            continue
        vol, strike = rv * iv_markup, spot
        expiry = day.toordinal() + DTE
        entry = bs_call(spot, strike, DTE / 365, vol) * (1 + half_spread)
        j, why = i, "end of data"
        while j < len(bars) - 1:
            j += 1
            d, s = bars[j]
            left = expiry - d.toordinal()
            value = bs_call(s, strike, left / 365, vol) * (1 - half_spread)
            ret = value / entry - 1
            if ret >= TARGET or ret <= STOP or left <= CLOSE_DTE:
                why = "target" if ret >= TARGET else "stop" if ret <= STOP else "time"
                break
        trades.append({"symbol": "", "signal": sig, "opened": day.isoformat(), "closed": bars[j][0].isoformat(),
                       "ret": ret, "stock_ret": bars[j][1] / spot - 1, "exit": why})
        i = j + 1
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    n = len(trades)
    return {"trades": n,
            "win_rate": round(sum(t["ret"] > 0 for t in trades) / n, 3),
            "avg_call_ret": round(sum(t["ret"] for t in trades) / n, 4),
            "avg_stock_ret": round(sum(t["stock_ret"] for t in trades) / n, 4),
            "targets": sum(t["exit"] == "target" for t in trades),
            "stops": sum(t["exit"] == "stop" for t in trades)}


def backtest(symbols: list[str] | None = None, start: date | None = date(2019, 1, 1),
             iv_markup: float = IV_MARKUP, half_spread: float = HALF_SPREAD, folder: Path = BARS) -> dict:
    symbols = symbols or sorted(p.stem for p in folder.glob("*.csv"))
    out = {"assumptions": {"dte": DTE, "iv_markup": iv_markup, "half_spread": half_spread,
                           "start": start.isoformat() if start else None}, "by_signal": {}}
    for sig in SIGNALS:
        rows, all_trades = {}, []
        for sym in symbols:
            ts = run(load_bars(sym, folder), sig, start, iv_markup, half_spread)
            for t in ts:
                t["symbol"] = sym
            rows[sym] = summarize(ts)
            all_trades += ts
        out["by_signal"][sig] = {"all": summarize(all_trades), "by_symbol": rows}
    return out
