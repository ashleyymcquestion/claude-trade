"""Backtest "$100 option bets" on REAL past option prices: `python -m trader.budgetbacktest`.

Data: data/optionhist/<SYM>.csv, daily bars of expired monthly contracts (May-Oct 2026) pulled from
Robinhood's get_option_historicals, plus the stock's daily closes in data/bars/. Unlike callbacktest.py
(modeled prices over 7 years), these are the prices those contracts actually traded at, but only for
six months.

Every trading day 28-42 calendar days before an expiration, each rule picks at most one position per
stock that costs $100 or less (100 shares x price), then exits one of three ways:
  hold      to expiration (worth its intrinsic value on the stock's last close)
  lab       the options lab's rules: +100% take profit, -50% stop, else sell 7 days before expiration
  double    take +100% if it ever gets there, else hold to expiration
Fills are pessimistic: buy at the day's close + SLIP, sell at the close - SLIP (cheap contracts have
wide spreads in percentage terms).

Rules (which contract is "best" for $100):
  call_nearest    the call closest to the stock price that still costs <= $100
  call_cheapest   the cheapest call worth at least $0.10 (a lottery ticket)
  put_nearest     the put closest to the stock price that costs <= $100
  call_spread     buy the first call at or above the stock price, sell the nearest higher strike that
                  brings the net cost to <= $100 (max payout = the strike gap)
  put_spread      the same with puts, below the stock price
Each rule also runs only on days the stock fell 3%+ ("after a drop"), the setup that looked tempting
on Oct 22.

Days overlap (the same contract can be bought on several days), so treat the counts as a rough guide;
the per-expiration table shows whether a result comes from one lucky month.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

DIR = Path("data/optionhist")
BARS = Path("data/bars")
BUDGET = 1.00          # $ per share = $100 per contract
SLIP = 0.03            # per side, $ per share
MIN_PRICE = 0.10
DTE_WINDOW = (28, 42)
TARGET, STOP, CLOSE_DTE = 1.0, -0.5, 7
DROP = -0.03
RULES = ("call_nearest", "call_cheapest", "put_nearest", "call_spread", "put_spread")
EXITS = ("hold", "lab", "double")


def load_options(symbol: str, folder: Path = DIR) -> dict:
    """{(expiration, type, strike): {date: close}}"""
    out: dict = defaultdict(dict)
    with open(folder / f"{symbol}.csv") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: r["date"])
    for r in rows:
        key = (r["expiration"], r["type"], float(r["strike"]))
        # a new strike's first bars can be flat 0.01 placeholders; skip them until it really trades
        if key not in out and r["open"] == r["high"] == r["low"] == r["close"] and float(r["close"]) <= 0.01:
            continue
        out[key][r["date"]] = float(r["close"])
    return out


def load_closes(symbol: str, folder: Path = BARS) -> dict[str, float]:
    """Daily closes from data/bars/, or from the Yahoo stock list (data/yahoo/universe/) for other symbols."""
    path = folder / f"{symbol}.csv"
    if not path.exists():
        path = Path("data/yahoo/universe") / f"{symbol}.csv"
    with open(path) as f:
        return {(r.get("timestamp") or r["date"])[:10]: float(r["close"]) for r in csv.DictReader(f)}


def _intrinsic(typ: str, strike: float, spot: float) -> float:
    return max(0.0, spot - strike) if typ == "call" else max(0.0, strike - spot)


def pick(rule: str, chain: dict, spot: float, day: str, expiration: str) -> list[tuple] | None:
    """Legs [(type, strike, +1 buy / -1 sell)] for a rule, or None. chain = {(exp, type, strike): {date: close}}."""
    def px(t, k):
        return chain.get((expiration, t, k), {}).get(day)

    calls = sorted(k for (e, t, k) in chain if e == expiration and t == "call" and px("call", k) is not None)
    puts = sorted(k for (e, t, k) in chain if e == expiration and t == "put" and px("put", k) is not None)
    if rule == "call_nearest":
        ok = [k for k in calls if k >= spot and MIN_PRICE <= px("call", k) + SLIP <= BUDGET]
        return [("call", ok[0], 1)] if ok else None
    if rule == "call_cheapest":
        ok = [k for k in calls if k >= spot and px("call", k) >= MIN_PRICE and px("call", k) + SLIP <= BUDGET]
        return [("call", ok[-1], 1)] if ok else None
    if rule == "put_nearest":
        ok = [k for k in puts if k <= spot and MIN_PRICE <= px("put", k) + SLIP <= BUDGET]
        return [("put", ok[-1], 1)] if ok else None
    if rule in ("call_spread", "put_spread"):
        t = "call" if rule == "call_spread" else "put"
        ks = [k for k in calls if k >= spot] if t == "call" else [k for k in reversed(puts) if k <= spot]
        if len(ks) < 2:
            return None
        k1 = ks[0]
        for k2 in ks[1:]:
            net = (px(t, k1) + SLIP) - (px(t, k2) - SLIP)
            if MIN_PRICE <= net <= BUDGET:
                return [(t, k1, 1), (t, k2, -1)]
        return None
    raise ValueError(rule)


def _value(legs, chain, expiration, day, spot, at_expiry: bool) -> float | None:
    """What the position sells for per share on `day` (intrinsic at expiration)."""
    total = 0.0
    for t, k, side in legs:
        if at_expiry:
            v = _intrinsic(t, k, spot)
        else:
            c = chain.get((expiration, t, k), {}).get(day)
            if c is None:
                return None
            v = c - SLIP if side > 0 else c + SLIP
        total += side * max(v, 0.0) if side > 0 else side * v
    return max(total, 0.0)


def simulate(legs, chain, closes, expiration, entry_day, exit_rule) -> tuple[float, str] | None:
    cost = sum((chain[(expiration, t, k)][entry_day] + SLIP) if s > 0 else -(chain[(expiration, t, k)][entry_day] - SLIP)
               for t, k, s in legs)
    if cost <= 0:
        return None
    exp = date.fromisoformat(expiration)
    days = sorted(d for d in closes if entry_day < d <= expiration)
    for d in days:
        left = (exp - date.fromisoformat(d)).days
        if exit_rule != "hold" and left > 0:
            v = _value(legs, chain, expiration, d, closes[d], False)
            if v is not None:
                r = v / cost - 1
                if r >= TARGET:
                    return r, "target"
                if exit_rule == "lab" and r <= STOP:
                    return r, "stop"
                if exit_rule == "lab" and left <= CLOSE_DTE:
                    return r, "time"
    last = max((d for d in closes if d <= expiration), default=None)
    if last is None or last < expiration and (exp - date.fromisoformat(last)).days > 3:
        return None                                         # expiration not in the stock data yet
    return _value(legs, chain, expiration, last, closes[last], True) / cost - 1, "expired"


def backtest(symbols: list[str] | None = None, folder: Path = DIR, bars: Path = BARS) -> dict:
    symbols = symbols or sorted(p.stem for p in folder.glob("*.csv"))
    results: dict = defaultdict(list)          # (rule, exit, filter) -> [(symbol, expiration, entry, ret)]
    for sym in symbols:
        chain, closes = load_options(sym, folder), load_closes(sym, bars)
        days = sorted(closes)
        prev = {d: days[i - 1] for i, d in enumerate(days) if i}
        for expiration in sorted({e for e, _, _ in chain}):
            exp = date.fromisoformat(expiration)
            for d in days:
                if not DTE_WINDOW[0] <= (exp - date.fromisoformat(d)).days <= DTE_WINDOW[1]:
                    continue
                drop = d in prev and closes[d] / closes[prev[d]] - 1 <= DROP
                for rule in RULES:
                    legs = pick(rule, chain, closes[d], d, expiration)
                    if not legs:
                        continue
                    for ex in EXITS:
                        r = simulate(legs, chain, closes, expiration, d, ex)
                        if r is None:
                            continue
                        row = (sym, expiration, d, r[0], legs)
                        results[(rule, ex, "every day")].append(row)
                        if drop:
                            results[(rule, ex, "after a drop")].append(row)
    out = {}
    for key, rows in sorted(results.items()):
        rets = [r[3] for r in rows]
        by_exp = defaultdict(list)
        for r in rows:
            by_exp[r[1]].append(r[3])
        out[" | ".join(key)] = {
            "trades": len(rets), "win_rate": round(sum(x > 0 for x in rets) / len(rets), 3),
            "avg_ret": round(sum(rets) / len(rets), 3),
            "avg_pnl_per_100": round(100 * sum(rets) / len(rets), 2),
            "doubled_or_more": round(sum(x >= 1 for x in rets) / len(rets), 3),
            "lost_it_all": round(sum(x <= -0.99 for x in rets) / len(rets), 3),
            "by_expiration_avg": {e: round(sum(v) / len(v), 2) for e, v in sorted(by_exp.items())},
        }
    return out


if __name__ == "__main__":
    import json

    print(json.dumps(backtest(), indent=1))
