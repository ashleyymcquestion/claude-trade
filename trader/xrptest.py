"""Would the crypto bot do better with XRP? `python -m trader.xrptest` (backtest) and the daily paper test.

Backtest: runs the Robinhood crypto strategy (config.robinhood.toml: trend + momentum scores, volatility
sizing, 5% no-trade band, 1% cost per side) on Yahoo's daily coin prices (data/yahoo/<COIN>-USD.csv,
`python -m trader.yahoo BTC-USD ETH-USD SOL-USD XRP-USD`), once with today's coins and once with XRP added.
It also shows XRP alone under the same rules and simply holding each coin.

Paper test (paper only, never trades): `python -m trader xrp-paper --price <XRP mark>` once a day. It keeps a
paper XRP position sized the way the strategy would size it with XRP in the coin list, starting from $2.00
on 2026-10-23, paying 1% per side, and logs one row per day to data/xrppaper/log.csv next to simply holding
$2.00 of XRP bought on day one.
"""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd

from .backtest import metrics, run_backtest
from .config import load_config
from .strategy import latest_target

YAHOO = Path("data/yahoo")
CONFIG = "config.robinhood.toml"
COINS = ("BTC", "ETH", "SOL")
PAPER = Path("data/xrppaper")
START_CASH = 2.00
COST = 0.01


def load_coins(coins, folder: Path = YAHOO) -> pd.DataFrame:
    cols = {}
    for c in coins:
        df = pd.read_csv(folder / f"{c}-USD.csv", index_col="date", parse_dates=True)
        cols[c] = df["close"]
    return pd.DataFrame(cols).sort_index().asfreq("D").ffill(limit=3)


def _cfg(coins):
    return replace(load_config(CONFIG), crypto=tuple(coins), crypto_sleeve=1.0)


def run(coins, start: str, prices: pd.DataFrame) -> dict:
    cfg = _cfg(coins)
    res = run_backtest(prices[list(coins)], cfg)
    eq = res.equity[start:]
    m = metrics(eq / eq.iloc[0])
    m["avg_xrp_weight"] = round(float(res.weights["XRP"][start:].mean()), 3) if "XRP" in coins else None
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()}


def backtest() -> dict:
    prices = load_coins(COINS + ("XRP",))
    out = {}
    # SOL's Yahoo history starts in 2020, so compare from 2021 (after the 280-day warm-up) and over recent spans
    for start in ("2021-06-01", "2023-01-01", "2025-01-01"):
        p = prices[prices.index >= pd.Timestamp(start) - pd.DateOffset(days=400)].dropna()
        out[start] = {
            "bot today (BTC/ETH/SOL)": run(COINS, start, p),
            "bot + XRP": run(COINS + ("XRP",), start, p),
            "XRP alone, same rules": run(("XRP",), start, p),
            **{f"hold {c}": {k: round(v, 3) for k, v in metrics(p[c][start:] / p[c][start:].iloc[0]).items()}
               for c in ("BTC", "XRP")},
        }
    return out


# ---- daily paper test -------------------------------------------------------------------------------

def _load_state() -> dict:
    f = PAPER / "state.json"
    return json.loads(f.read_text()) if f.exists() else {}


def paper_step(xrp_price: float, today: date, prices: pd.DataFrame | None = None) -> dict:
    """One paper day: size the XRP position like the 4-coin strategy would, log it. Idempotent per day."""
    state = _load_state()
    if state.get("last") == str(today):
        return {"already_ran": True, **state}
    prices = load_coins(COINS + ("XRP",)) if prices is None else prices
    weight = float(latest_target(prices, _cfg(COINS + ("XRP",))).get("XRP", 0.0))
    if not state:
        state = {"start": str(today), "cash": START_CASH, "xrp": 0.0, "hold_xrp": START_CASH / xrp_price,
                 "trades": 0}
    equity = state["cash"] + state["xrp"] * xrp_price
    want = weight * equity
    have = state["xrp"] * xrp_price
    traded = 0.0
    if abs(want - have) > 0.05 * equity or (weight == 0 and have > 0):
        diff = want - have
        cost = abs(diff) * COST
        state["xrp"] += diff / xrp_price
        state["cash"] -= diff + cost
        state["trades"] += 1
        traded = diff
        equity = state["cash"] + state["xrp"] * xrp_price
    hold = state["hold_xrp"] * xrp_price
    state["last"] = str(today)
    row = {"date": str(today), "xrp_price": round(xrp_price, 4), "target_weight": round(weight, 3),
           "traded_usd": round(traded, 2), "paper_value": round(equity, 4), "hold_xrp_value": round(hold, 4)}
    PAPER.mkdir(parents=True, exist_ok=True)
    (PAPER / "state.json").write_text(json.dumps(state, indent=1) + "\n")
    log = PAPER / "log.csv"
    new = not log.exists()
    with open(log, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    return row


if __name__ == "__main__":
    print(json.dumps(backtest(), indent=1))
