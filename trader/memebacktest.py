"""Backtest simple "Kimchi-style" meme coin rules on daily prices: `python -m trader.memebacktest`.

Kimchi (a pseudonymous Solana trader) reportedly made $40M buying TRUMP within hours of its launch. Nobody can
backtest "be first on the one coin that goes 100x", but we can test the rules a bot could follow:

  launch     buy every meme coin on its 2nd day of trading (the earliest daily price), hold 30 days
  chase      each day, if a meme coin rose 20%+ that day, buy the biggest riser at the close, hold 1 / 3 / 7 days
  breakout   buy a coin closing at a new 30-day high with 3x its 30-day average volume, hold 7 days
  hold       buy the whole basket equally on day one and hold (the benchmark), plus BTC

Data: Yahoo daily prices (data/yahoo/meme/, `python -m trader.memebacktest --download`). Costs: COST per side
(about Coinbase's 0.6-0.9% taker fee or half of Robinhood's ~2% spread). Every trade is the same size, so
"avg trade" is what one bet earns on average; "total" compounds the trades of one rule run one at a time.
Caveats: the basket is today's famous meme coins, so it only contains winners that survived (survivorship
bias makes every rule look better than it would have been), and Yahoo's first price is usually days after the
real launch, when early buyers like Kimchi were already up many times.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

DIR = Path("data/yahoo/meme")
COINS = {"DOGE": "DOGE-USD", "SHIB": "SHIB-USD", "PEPE": "PEPE24478-USD", "BONK": "BONK-USD", "WIF": "WIF-USD",
         "FLOKI": "FLOKI-USD", "TRUMP": "TRUMP35336-USD", "PENGU": "PENGU34466-USD", "POPCAT": "POPCAT28782-USD",
         "FARTCOIN": "FARTCOIN-USD", "MOG": "MOG-USD", "BRETT": "BRETT29743-USD", "MEW": "MEW30126-USD"}
# Yahoo rounds sub-penny prices to 1-2 significant digits (PEPE at $0.000004 moves in 25% steps), which fakes
# huge daily jumps, so these coins are downloaded but left out of the tests until better price data is used.
ROUNDED = {"SHIB", "PEPE", "BONK", "FLOKI", "MOG"}
COST = 0.01
CHASE_MIN = 0.20


def download() -> None:
    from .yahoo import fetch

    for name, sym in [*COINS.items(), ("BTC", "BTC-USD")]:
        n = fetch(sym, DIR)
        (DIR / f"{sym}.csv").rename(DIR / f"{name}.csv")
        print(name, n)


def load(name: str, folder: Path = DIR) -> list[dict]:
    with open(folder / f"{name}.csv") as f:
        return [{"date": r["date"], "close": float(r["close"]), "volume": float(r["volume"])} for r in csv.DictReader(f)
                if float(r["close"]) > 0]


def _usable() -> dict:
    return {n: load(n) for n in COINS if n not in ROUNDED and (DIR / f"{n}.csv").exists()}


def _trade(bars: list[dict], i: int, hold: int) -> float | None:
    """Buy at bars[i] close, sell `hold` days later, both sides paying COST."""
    if i + hold >= len(bars):
        return None
    return bars[i + hold]["close"] * (1 - COST) / (bars[i]["close"] * (1 + COST)) - 1


def launch(data: dict, hold: int = 30) -> list[float]:
    return [r for b in data.values() if (r := _trade(b, 1, hold)) is not None]


def chase(data: dict, hold: int) -> list[float]:
    """One position at a time: the day's biggest 20%+ riser, held `hold` days."""
    by_date: dict[str, list] = {}
    for name, b in data.items():
        for i in range(1, len(b)):
            gain = b[i]["close"] / b[i - 1]["close"] - 1
            if gain >= CHASE_MIN:
                by_date.setdefault(b[i]["date"], []).append((gain, name, i))
    out, busy_until = [], ""
    for d in sorted(by_date):
        if d <= busy_until:
            continue
        _, name, i = max(by_date[d])
        if (r := _trade(data[name], i, hold)) is not None:
            out.append(r)
            busy_until = data[name][i + hold]["date"]
    return out


def breakout(data: dict, hold: int = 7) -> list[float]:
    out = []
    for b in data.values():
        i = 31
        while i < len(b) - hold:
            win = b[i - 30:i]
            if b[i]["close"] > max(x["close"] for x in win) and b[i]["volume"] > 3 * sum(x["volume"] for x in win) / 30:
                out.append(_trade(b, i, hold))
                i += hold
            i += 1
    return out


def stats(rets: list[float]) -> dict:
    if not rets:
        return {"trades": 0}
    total = 1.0
    for r in rets:
        total *= 1 + r
    s = sorted(rets)
    return {"trades": len(rets), "win_rate": round(sum(r > 0 for r in rets) / len(rets), 2),
            "avg_trade": round(sum(rets) / len(rets), 3), "median": round(s[len(s) // 2], 3),
            "best": round(s[-1], 2), "worst": round(s[0], 2), "total": round(total - 1, 2)}


def hold_basket(data: dict, start: str) -> dict:
    rets = []
    for b in data.values():
        b = [x for x in b if x["date"] >= start]
        if len(b) > 1:
            rets.append(b[-1]["close"] / b[0]["close"] - 1)
    return {"coins": len(rets), "avg": round(sum(rets) / len(rets), 2), "median": round(sorted(rets)[len(rets) // 2], 2)}


def run() -> dict:
    data = _usable()
    btc = load("BTC")
    out = {"launch (2nd day, hold 30d)": stats(launch(data))}
    for h in (1, 3, 7):
        out[f"chase 20%+ riser, hold {h}d"] = stats(chase(data, h))
    out["breakout + 3x volume, hold 7d"] = stats(breakout(data))
    for start in ("2024-01-01", "2025-01-01"):
        out[f"hold basket from {start}"] = hold_basket(data, start)
        b = [x for x in btc if x["date"] >= start]
        out[f"hold BTC from {start}"] = round(b[-1]["close"] / b[0]["close"] - 1, 2)
    return out


# ---- daily paper test (paper only, never trades) ------------------------------------------------------

PAPER = Path("data/memepaper")
PAPER_HOLD = 7


def _signals(data: dict, day: str) -> list[dict]:
    """Today's chase (20%+ riser) and breakout (30-day high on 3x volume) signals, from closes up to `day`."""
    out = []
    for name, b in data.items():
        b = [x for x in b if x["date"] <= day]
        if len(b) < 32 or b[-1]["date"] != day:
            continue
        gain = b[-1]["close"] / b[-2]["close"] - 1
        win = b[-31:-1]
        if gain >= CHASE_MIN:
            out.append({"rule": "chase", "coin": name, "gain": round(gain, 3)})
        if b[-1]["close"] > max(x["close"] for x in win) and b[-1]["volume"] > 3 * sum(x["volume"] for x in win) / 30:
            out.append({"rule": "breakout", "coin": name, "gain": round(gain, 3)})
    chase = sorted((s for s in out if s["rule"] == "chase"), key=lambda s: -s["gain"])[:1]   # only the biggest riser
    return chase + [s for s in out if s["rule"] == "breakout"]


def paper_step(day: str, data: dict | None = None) -> dict:
    """Close paper trades held PAPER_HOLD days, open today's signals at today's close. Idempotent per day."""
    data = data or _usable()
    PAPER.mkdir(parents=True, exist_ok=True)
    f = PAPER / "trades.json"
    trades = json.loads(f.read_text()) if f.exists() else []
    if any(t["opened"] == day for t in trades) or (PAPER / f"done-{day}").exists():
        return {"already_ran": True}
    closes = {n: {x["date"]: x["close"] for x in b} for n, b in data.items()}
    closed = []
    for t in trades:
        if t["status"] == "open":
            dates = sorted(d for d in closes[t["coin"]] if t["opened"] < d <= day)
            if len(dates) >= PAPER_HOLD:
                px = closes[t["coin"]][dates[PAPER_HOLD - 1]]
                t.update(status="closed", closed=dates[PAPER_HOLD - 1], exit=px,
                         ret=round(px * (1 - COST) / (t["entry"] * (1 + COST)) - 1, 4))
                closed.append(t)
    opened = []
    for s in _signals(data, day):
        if any(t["coin"] == s["coin"] and t["status"] == "open" for t in trades):
            continue
        t = {**s, "opened": day, "entry": closes[s["coin"]][day], "status": "open"}
        trades.append(t)
        opened.append(t)
    f.write_text(json.dumps(trades, indent=1) + "\n")
    (PAPER / f"done-{day}").touch()
    done = [t["ret"] for t in trades if t["status"] == "closed"]
    return {"day": day, "opened": [f"{t['rule']} {t['coin']} (+{t['gain']:.0%})" for t in opened],
            "closed": [f"{t['coin']} {t['ret']:+.1%}" for t in closed],
            "open": sum(t["status"] == "open" for t in trades), "scoreboard": stats(done)}


if __name__ == "__main__":
    if "--download" in sys.argv:
        download()
    if "--paper" in sys.argv:       # yesterday's completed daily close (UTC), the last full candle
        from datetime import UTC, datetime, timedelta

        print(json.dumps(paper_step(str((datetime.now(UTC) - timedelta(days=1)).date()))))
    else:
        print(json.dumps(run(), indent=1))
