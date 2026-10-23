"""Backtest rule-based versions of famous traders' styles: `python -m trader.legends`.

None of these people published rules a bot can copy, and the stock pickers (Lynch, Buffett) used company
financials we don't have history for. So each test is the closest price-only version of the style:

  dennis        Turtle System 1 (20-day breakout, 10-day exit, 2N stop) on a diversified basket, as the
                Turtles traded many markets: SPY QQQ IWM GLD TLT BTC, 1/6 of the money each
  druckenmiller concentrated top-down bets that are cut when wrong: every month hold the ONE asset of
                SPY QQQ IWM GLD TLT BTC with the best 3-month return, only if it's above its 200-day
                average; otherwise cash
  simons        short-term mean reversion (the kind of small statistical edge Renaissance trades): buy
                when RSI(2) < 10 while above the 200-day average, sell when the close is back above the
                5-day average. On SPY QQQ IWM DIA and on NVDA AMD AAPL MSFT
  buffett       "be greedy when others are fearful": buy SPY only after it has fallen 20% from its 1-year high,
                hold 3 years; compared with the average 3-year return from any day
  griffin       Citadel-style multi-strategy: split the money equally across the dennis, druckenmiller and
                simons (index) strategies, which rarely lose at the same time
  lynch         growth at a reasonable price (PEG < 1) needs past earnings data: not backtested here

Costs: 0.05% per side on ETFs/stocks, 1% per side on crypto. Daily Yahoo bars in data/yahoo/.
"""

from __future__ import annotations

from .classics import load, stats

COST, CRYPTO_COST = 0.0005, 0.01
BASKET = ("SPY", "QQQ", "IWM", "GLD", "TLT", "BTC-USD")
INDEXES = ("SPY", "QQQ", "IWM", "DIA")
STOCKS = ("NVDA", "AMD", "AAPL", "MSFT")


def _cost(s: str) -> float:
    return CRYPTO_COST if s.endswith("-USD") else COST


def _closes(symbols) -> dict[str, dict[str, float]]:
    return {s: {b["date"]: b["close"] for b in load(s)} for s in symbols}


def _calendar(start: str) -> list[str]:
    """Stock-market days (SPY's dates) from start on."""
    return [b["date"] for b in load("SPY") if b["date"] >= start]


# ---------- per-symbol daily equity curves, keyed by date ----------

def turtle_curve(symbol: str, entry: int = 20, exit_: int = 10, stop_n: float = 2.0) -> dict[str, float]:
    bars, cost = load(symbol), _cost(symbol)
    tr = [bars[0]["high"] - bars[0]["low"]] + [
        max(b["high"], p["close"]) - min(b["low"], p["close"]) for p, b in zip(bars, bars[1:])]
    eq, cash, pos = {}, 1.0, None
    for i in range(max(entry, 20), len(bars)):
        b = bars[i]
        hi = max(x["high"] for x in bars[i - entry:i])
        lo = min(x["low"] for x in bars[i - exit_:i])
        n = sum(tr[i - 20:i]) / 20
        if pos is None and b["high"] > hi:
            px = max(hi, b["open"]) * (1 + cost)
            pos = {"units": cash / px, "stop": px - stop_n * n}
        elif pos is not None and b["low"] < max(pos["stop"], lo):
            cash = pos["units"] * min(max(pos["stop"], lo), b["open"]) * (1 - cost)
            pos = None
        eq[b["date"]] = pos["units"] * b["close"] if pos else cash
    return eq


def rsi(closes: list[float], n: int = 2) -> float | None:
    if len(closes) < n + 1:
        return None
    moves = [b - a for a, b in zip(closes[-n - 1:-1], closes[-n:])]
    up, down = sum(m for m in moves if m > 0), -sum(m for m in moves if m < 0)
    return 100.0 if down == 0 else 100 - 100 / (1 + up / down)


def simons_curve(symbol: str, buy_below: float = 10) -> tuple[dict[str, float], list[float]]:
    bars, cost = load(symbol), _cost(symbol)
    closes = [b["close"] for b in bars]
    eq, cash, units, entry, trades = {}, 1.0, 0.0, 0.0, []
    for i in range(200, len(bars)):
        c = closes[i]
        if units == 0:
            r = rsi(closes[:i + 1])
            if c > sum(closes[i - 199:i + 1]) / 200 and r is not None and r < buy_below:
                entry = c * (1 + cost)
                units, cash = cash / entry, 0.0
        elif c > sum(closes[i - 4:i + 1]) / 5:
            cash, units = units * c * (1 - cost), 0.0
            trades.append(c * (1 - cost) / entry - 1)
        eq[bars[i]["date"]] = units * c if units else cash
    return eq, trades


def druckenmiller_curve(menu=BASKET, lookback: int = 63, every: int = 21, start: str = "2005-01-01") -> tuple[dict, list]:
    px = _closes(menu)
    days = _calendar(start)
    hist = {s: sorted(px[s]) for s in menu}
    eq, value, held, picks = {}, 1.0, None, []
    last_px = None
    for k, d in enumerate(days):
        if held and d in px[held] and last_px:
            value *= px[held][d] / last_px
            last_px = px[held][d]
        if k % every == 0:
            best, best_r = None, 0.0
            for s in menu:
                ds = [x for x in hist[s] if x <= d][-201:]
                if len(ds) < 201:
                    continue
                c = [px[s][x] for x in ds]
                r = c[-1] / c[-1 - lookback] - 1
                if c[-1] > sum(c[-200:]) / 200 and r > best_r:
                    best, best_r = s, r
            if best != held:
                if held:
                    value *= 1 - _cost(held)
                if best:
                    value *= 1 - _cost(best)
                held = best
                last_px = px[best].get(d) if best else None
                picks.append((d, best))
        eq[d] = value
    return eq, picks


def buffett_fear(symbol: str = "SPY", drop: float = 0.20, hold_days: int = 756) -> dict:
    bars = load(symbol)
    c = [b["close"] for b in bars]
    fwd = lambda i: c[i + hold_days] / c[i] - 1
    fear, every = [], []
    last_buy = -10 ** 9
    for i in range(len(c) - hold_days):
        peak = max(c[max(0, i - 251):i + 1])                     # 1-year high
        every.append(fwd(i))
        if c[i] <= peak * (1 - drop) and i - last_buy > 252:      # at most one buy a year
            fear.append((bars[i]["date"], round(fwd(i), 3)))
            last_buy = i
    avg = lambda xs: round(sum(xs) / len(xs), 3) if xs else None
    return {"symbol": symbol, "fear_buys": fear, "avg_3y_after_fear": avg([x[1] for x in fear]),
            "avg_3y_any_day": avg(every)}


# ---------- combining and summarising curves ----------

def combine(curves: list[dict[str, float]], days: list[str]) -> list[float]:
    """Equal money in each curve at the start, each one forward-filled on days it has no bar."""
    out, last = [], [None] * len(curves)
    base = [None] * len(curves)
    for d in days:
        for j, cv in enumerate(curves):
            if d in cv:
                last[j] = cv[d]
                if base[j] is None:
                    base[j] = cv[d]
        if any(b is None for b in base):
            continue
        out.append(sum(l / b for l, b in zip(last, base)) / len(curves))
    return out


def sharpe(eq: list[float]) -> float | None:
    r = [b / a - 1 for a, b in zip(eq, eq[1:])]
    if len(r) < 20:
        return None
    m = sum(r) / len(r)
    sd = (sum((x - m) ** 2 for x in r) / (len(r) - 1)) ** 0.5
    return round(m / sd * 252 ** 0.5, 2) if sd else None


def summary(eq: list[float], days: list[str]) -> dict:
    return {**stats(eq, days[-len(eq):]), "sharpe": sharpe(eq)}


def hold_curve(symbol: str) -> dict[str, float]:
    return {b["date"]: b["close"] for b in load(symbol)}


if __name__ == "__main__":
    import json

    for start in ("2005-01-01", "2016-01-01"):
        days = _calendar(start)
        print(f"\n===== from {start} =====")
        rows = {}
        rows["hold SPY"] = summary(combine([hold_curve("SPY")], days), days)
        rows["hold QQQ"] = summary(combine([hold_curve("QQQ")], days), days)
        basket_days = _calendar(max(start, "2015-01-01"))
        rows["hold basket (6, from 2015)"] = summary(combine([hold_curve(s) for s in BASKET], basket_days), basket_days)
        dennis = combine([turtle_curve(s) for s in BASKET], basket_days)
        rows["dennis: turtle basket (from 2015)"] = summary(dennis, basket_days)
        dr, picks = druckenmiller_curve(start=start)
        rows["druckenmiller: best of 6, monthly"] = summary(combine([dr], days), days)
        sim_idx = [simons_curve(s)[0] for s in INDEXES]
        rows["simons: RSI2 dip, 4 index ETFs"] = summary(combine(sim_idx, days), days)
        rows["simons: RSI2 dip, NVDA AMD AAPL MSFT"] = summary(combine([simons_curve(s)[0] for s in STOCKS], days), days)
        rows["hold NVDA AMD AAPL MSFT"] = summary(combine([hold_curve(s) for s in STOCKS], days), days)
        griffin = combine([turtle_curve(s) for s in BASKET] + [dr] + sim_idx, basket_days)
        rows["griffin: dennis + druckenmiller + simons (from 2015)"] = summary(griffin, basket_days)
        for k, v in rows.items():
            print(f"{k:52} {json.dumps(v)}")
        if start == "2016-01-01":
            print("druckenmiller picks (last 12):", picks[-12:])
    trades = {s: simons_curve(s)[1] for s in INDEXES + STOCKS}
    print("\nsimons trade stats:", json.dumps({s: {"n": len(t), "win": round(sum(x > 0 for x in t) / len(t), 2),
                                                  "avg": round(sum(t) / len(t), 4)} for s, t in trades.items() if t}))
    print("buffett fear:", json.dumps(buffett_fear("SPY")))
    print("buffett fear QQQ:", json.dumps(buffett_fear("QQQ")))
