"""Backtest four famous swing/day traders' setups on ~110 US stocks: `python -m trader.swing`.

Data: data/yahoo/universe/ (python -m trader.yahoo --universe). Signals use a day's close; entries happen the next
day (at the open, or when a buy-stop level trades). Costs 0.05% per side, stops that gap fill at the open.
Portfolio: at most MAX_POS positions, each sized at 1/MAX_POS of equity when opened, cash only.

  qullamaggie   episodic pivot: the stock opens 10%+ above yesterday's close on 3x+ its 20-day volume and closes
                above its open. Buy the next open, stop at the gap day's low, sell on a close below the 10-day
                average (max 60 days). Ranked by gap size.
  bnf           Takashi Kotegawa: close 20%+ below its 25-day average. Buy the next open, sell on a close back at
                the 25-day average or after 15 days. Ranked by how far below.
  raschke       "Holy Grail": ADX(14) > 30 with +DI > -DI (strong uptrend) and the day's low touches the 20-day EMA.
                Buy if the next day trades above that day's high, stop at that day's low, target the 20-day high
                before it, else sell after 10 days. Ranked by ADX.
  minervini     trend template (close > 50 > 150 > 200-day averages, 200-day rising for a month, 30%+ above the
                52-week low, within 25% of the 52-week high) plus a breakout: close above the prior 20-day high on
                1.5x volume after a 20-day range under 15%. Buy the next open, stop 7% below, sell on a close below
                the 50-day average. Ranked by 6-month return.

The universe is today's list of stocks (some of which crashed, but none that were delisted), so results lean
optimistic; compare strategies with each other and with holding SPY over the same dates.
"""

from __future__ import annotations

import csv
from pathlib import Path

DIR = Path("data/yahoo/universe")
BENCH = Path("data/yahoo/SPY.csv")
COST = 0.0005
MAX_POS = 10
STRATS = ("qullamaggie", "bnf", "raschke", "minervini")


def load(path: Path) -> list[dict]:
    with open(path) as f:
        return [{"date": r["date"], "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                 "close": float(r["close"]), "volume": float(r["volume"])} for r in csv.DictReader(f)]


def _sma(xs: list[float], n: int) -> list[float | None]:
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def _ema(xs: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for i, x in enumerate(xs):
        out.append(x if i == 0 else x * k + out[-1] * (1 - k))
    return out


def _roll(xs: list[float], n: int, fn) -> list[float | None]:
    """fn over the n values BEFORE each index (not including it)."""
    return [fn(xs[i - n:i]) if i >= n else None for i in range(len(xs))]


def _adx(b: list[dict], n: int = 14) -> tuple[list, list, list]:
    tr, pdm, ndm = [0.0], [0.0], [0.0]
    for p, x in zip(b, b[1:]):
        up, dn = x["high"] - p["high"], p["low"] - x["low"]
        tr.append(max(x["high"], p["close"]) - min(x["low"], p["close"]))
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)
    atr = pdi_s = ndi_s = 0.0
    adx, pdi, ndi, dxs = [], [], [], []
    a = None
    for i in range(len(b)):
        atr = atr - atr / n + tr[i]
        pdi_s = pdi_s - pdi_s / n + pdm[i]
        ndi_s = ndi_s - ndi_s / n + ndm[i]
        p = 100 * pdi_s / atr if atr else 0.0
        m = 100 * ndi_s / atr if atr else 0.0
        dx = 100 * abs(p - m) / (p + m) if p + m else 0.0
        dxs.append(dx)
        if i == 2 * n:
            a = sum(dxs[n:]) / (n + 1)
        elif i > 2 * n:
            a = (a * (n - 1) + dx) / n
        adx.append(a)
        pdi.append(p)
        ndi.append(m)
    return adx, pdi, ndi


def indicators(b: list[dict]) -> dict:
    c = [x["close"] for x in b]
    v = [x["volume"] for x in b]
    h = [x["high"] for x in b]
    lo = [x["low"] for x in b]
    adx, pdi, ndi = _adx(b)
    return {"c": c, "sma10": _sma(c, 10), "sma25": _sma(c, 25), "sma50": _sma(c, 50), "sma150": _sma(c, 150),
            "sma200": _sma(c, 200), "ema20": _ema(c, 20), "vol20": _roll(v, 20, lambda w: sum(w) / len(w)),
            "vol50": _roll(v, 50, lambda w: sum(w) / len(w)), "hi20": _roll(h, 20, max), "lo20": _roll(lo, 20, min),
            "chi20": _roll(c, 20, max), "hi252": _roll(h, 252, max), "lo252": _roll(lo, 252, min),
            "adx": adx, "pdi": pdi, "ndi": ndi}


def signal(strat: str, b: list[dict], ind: dict, i: int) -> dict | None:
    """Entry plan from day i's close, or None. Keys: rank, kind ('open' or 'stop'), level, stop."""
    x, c = b[i], ind["c"]
    if i < 260:
        return None
    if strat == "qullamaggie":
        gap = x["open"] / c[i - 1] - 1
        if gap >= 0.10 and ind["vol20"][i] and x["volume"] >= 3 * ind["vol20"][i] and x["close"] > x["open"]:
            return {"rank": gap, "kind": "open", "stop": x["low"]}
    elif strat == "bnf":
        dev = c[i] / ind["sma25"][i] - 1
        if dev <= -0.20:
            return {"rank": -dev, "kind": "open", "stop": 0.0}
    elif strat == "raschke":
        a = ind["adx"][i]
        if a and a > 30 and ind["pdi"][i] > ind["ndi"][i] and x["low"] <= ind["ema20"][i] <= x["high"]:
            return {"rank": a, "kind": "stop", "level": x["high"], "stop": x["low"], "target": ind["hi20"][i]}
    elif strat == "minervini":
        s50, s150, s200, s200m = ind["sma50"][i], ind["sma150"][i], ind["sma200"][i], ind["sma200"][i - 21]
        if not (s50 and s150 and s200 and s200m and c[i] > s50 > s150 > s200 and s200 > s200m):
            return None
        if not (c[i] >= 1.3 * ind["lo252"][i] and c[i] >= 0.75 * ind["hi252"][i]):
            return None
        tight = (ind["hi20"][i] - ind["lo20"][i]) / ind["lo20"][i] < 0.15
        if tight and c[i] > ind["chi20"][i] and ind["vol50"][i] and x["volume"] >= 1.5 * ind["vol50"][i]:
            return {"rank": c[i] / c[i - 126] - 1, "kind": "open", "stop": 0.0}
    return None


def _exit(strat: str, pos: dict, x: dict, ind: dict, i: int) -> tuple[float, str] | None:
    """Exit price and reason for day i, or None to keep holding."""
    if pos["stop"] and x["low"] <= pos["stop"]:
        return min(pos["stop"], x["open"]), "stop"
    if pos.get("target") and x["high"] >= pos["target"]:
        return max(pos["target"], x["open"]), "target"
    held, c = pos["days"], x["close"]
    if strat == "qullamaggie" and ((ind["sma10"][i] and c < ind["sma10"][i]) or held >= 60):
        return c, "trail"
    if strat == "bnf" and (c >= ind["sma25"][i] or held >= 15):
        return c, "back to average" if c >= ind["sma25"][i] else "time"
    if strat == "raschke" and held >= 10:
        return c, "time"
    if strat == "minervini" and ind["sma50"][i] and c < ind["sma50"][i]:
        return c, "below 50-day"
    return None


def backtest(strat: str, data: dict, start: str, end: str = "9999") -> dict:
    cal = [x["date"] for x in load(BENCH) if start <= x["date"] <= end]
    idx = {s: {x["date"]: i for i, x in enumerate(b)} for s, (b, _) in data.items()}
    cash, positions, pending, trades, curve, last = 1.0, {}, [], [], [], {}
    for d in cal:
        # entries planned at yesterday's close
        for sym, sig in pending:
            if sym in positions or len(positions) >= MAX_POS or d not in idx[sym]:
                continue
            b, ind = data[sym]
            i = idx[sym][d]
            x = b[i]
            if sig["kind"] == "open":
                px = x["open"]
            elif x["high"] > sig["level"]:
                px = max(sig["level"], x["open"])
            else:
                continue
            equity = cash + sum(p["units"] * last.get(s, p["entry"]) for s, p in positions.items())
            size = min(cash, equity / MAX_POS)
            if size <= 0:
                break
            stop = sig["stop"] if strat != "minervini" else px * 0.93
            units = size / (px * (1 + COST))
            cash -= size
            positions[sym] = {"units": units, "entry": px * (1 + COST), "stop": stop if stop < px else 0.0,
                              "target": sig.get("target") if sig.get("target") and sig["target"] > px else None,
                              "days": 0, "opened": d}
        pending = []
        # exits and marks
        for sym in list(positions):
            if d not in idx[sym]:
                continue
            b, ind = data[sym]
            i = idx[sym][d]
            p = positions[sym]
            if p["opened"] != d:
                p["days"] += 1
                ex = _exit(strat, p, b[i], ind, i)
                if ex:
                    cash += p["units"] * ex[0] * (1 - COST)
                    trades.append({"ret": ex[0] * (1 - COST) / p["entry"] - 1, "days": p["days"], "why": ex[1]})
                    del positions[sym]
                    continue
            last[sym] = b[i]["close"]
        curve.append(cash + sum(p["units"] * last.get(s, p["entry"]) for s, p in positions.items()))
        # new signals from today's close
        sigs = []
        for sym, (b, ind) in data.items():
            if d in idx[sym] and sym not in positions:
                s = signal(strat, b, ind, idx[sym][d])
                if s:
                    sigs.append((s["rank"], sym, s))
        pending = [(sym, s) for _, sym, s in sorted(sigs, key=lambda t: -t[0])]
    return {"curve": curve, "dates": cal, "trades": trades}


def summarize(res: dict) -> dict:
    eq, tr = res["curve"], res["trades"]
    years = len(eq) / 252
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    r = [b / a - 1 for a, b in zip(eq, eq[1:])]
    m = sum(r) / len(r)
    sd = (sum((x - m) ** 2 for x in r) / len(r)) ** 0.5
    return {"cagr": round(eq[-1] ** (1 / years) - 1, 4), "max_drawdown": round(mdd, 3),
            "sharpe": round(m / sd * 252 ** 0.5, 2) if sd else None, "trades": len(tr),
            "win_rate": round(sum(t["ret"] > 0 for t in tr) / len(tr), 3) if tr else None,
            "avg_trade": round(sum(t["ret"] for t in tr) / len(tr), 4) if tr else None,
            "avg_days": round(sum(t["days"] for t in tr) / len(tr), 1) if tr else None}


def bench(start: str, end: str = "9999") -> dict:
    eq = [x["close"] for x in load(BENCH) if start <= x["date"] <= end]
    return summarize({"curve": [v / eq[0] for v in eq], "trades": []})


def load_universe(folder: Path = DIR) -> dict:
    out = {}
    for p in sorted(folder.glob("*.csv")):
        b = load(p)
        if len(b) > 300:
            out[p.stem] = (b, indicators(b))
    return out


if __name__ == "__main__":
    import json

    data = load_universe()
    for start, end in (("2010-01-01", "9999"), ("2016-01-01", "9999"), ("2022-01-01", "2022-12-31"),
                       ("2024-01-01", "9999")):
        print(f"\n== {start} to {end} ==  SPY: {json.dumps(bench(start, end))}")
        for s in STRATS:
            print(f"{s:12} {json.dumps(summarize(backtest(s, data, start, end)))}")
