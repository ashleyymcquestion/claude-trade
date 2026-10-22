"""Paper-only futures test for the Coinbase bot: `python -m trader cb-fut-needs` / `cb-fut-run`.

Strategy (the most robust result in trader/futuresbacktest.py, see docs/research/futures.md): for BTC and
ETH, long only, never short. Each coin's signal is the share of its 20/50/100/200-day averages that the
last daily close is above (0, 0.25, ... 1). Size = signal x min(target_vol / 30-day realized vol, max_lev),
half the paper account per coin. Positions are whole Coinbase futures contracts (BIP = 0.01 BTC,
ETP = 0.1 ETH), rebalanced once a day after the UTC daily candle closes.

Paper model: trades fill at the futures mid price and pay fee_rate of the notional; longs pay
funding_per_year on their notional, accrued at each run. Nothing is ever sent to Coinbase.

cb-fut-run input JSON
  {"now": "...", "best_bid_ask": <coinbase_products_best_bid_ask result for the two futures>,
   "candles_1d": {"BTC-USDC": <candles>, "ETH-USDC": <candles>}}     only when cb-fut-needs asks;
                  merged into data/coinbase/daily_closes.csv (seeded from data/crypto/daily/)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .coinbase import JOURNAL, _iso, _now

LEDGER = Path("data/coinbase/futures_paper.json")
HISTORY = Path("data/coinbase/daily_closes.csv")       # BTC/ETH daily closes, seeded from data/crypto/daily/
SEED = Path("data/crypto/daily")
CFG = {
    "start_usd": 1000.0,               # paper only: one contract is ~$240 (ETH) to ~$810 (BTC)
    "contracts": {"BTC": {"product": "BIP-20DEC30-CDE", "size": 0.01},
                  "ETH": {"product": "ETP-20DEC30-CDE", "size": 0.1}},
    "averages": [20, 50, 100, 200],
    "vol_days": 30,
    "target_vol": 0.40,
    "max_lev": 2.0,
    "fee_rate": 0.001,                 # pessimistic: Coinbase futures fee plus slippage
    "funding_per_year": 0.10,          # longs pay; matches the backtest
}


def new_ledger() -> dict:
    return {"cash": CFG["start_usd"], "start": CFG["start_usd"], "contracts": {"BTC": 0, "ETH": 0},
            "entry": {"BTC": 0.0, "ETH": 0.0}, "day": None, "last_run": None, "trades": [], "signals": {}}


def last_day(now: datetime) -> str:
    return str(now.astimezone(timezone.utc).date() - timedelta(days=1))


def load_history(path: Path = HISTORY) -> pd.DataFrame:
    if path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    cols = {c: pd.read_csv(SEED / f"{c}.csv", index_col=0, parse_dates=True)["close"].iloc[:-1]
            for c in CFG["contracts"]}
    return pd.DataFrame(cols).dropna()


def needs(ledger: dict, now: datetime, history: pd.DataFrame | None = None) -> dict:
    due = ledger["day"] != last_day(now)
    history = load_history() if history is None else history
    since = history.index[-1].to_pydatetime().replace(tzinfo=timezone.utc) - timedelta(days=3)
    return {"day_due": due, "best_bid_ask": {"product_ids": [c["product"] for c in CFG["contracts"].values()],
                                             "product_type": "FUTURE"},
            "candles_1d": ({"products": ["BTC-USDC", "ETH-USDC"], "granularity": "1d",
                            "start": _iso(since)} if due else None)}


def daily_closes(raw: dict, now: datetime) -> pd.Series:
    c = raw["candles"]
    s = pd.Series([float(x["close"]) for x in c],
                  index=pd.to_datetime([int(x["start"]) for x in c], unit="s").normalize()).sort_index()
    return s[s.index <= pd.Timestamp(last_day(now))]


def target_weight(close: pd.Series) -> tuple[float, dict]:
    """Share of the paper account to hold in this coin (before the per-coin half)."""
    if len(close) < max(CFG["averages"]) + 1:
        raise ValueError(f"need {max(CFG['averages']) + 1} daily closes, got {len(close)}")
    last = close.iloc[-1]
    sig = sum(last > close.iloc[-n:].mean() for n in CFG["averages"]) / len(CFG["averages"])
    vol = close.pct_change().iloc[-CFG["vol_days"]:].std() * np.sqrt(365)
    lev = min(CFG["target_vol"] / vol, CFG["max_lev"])
    return float(sig * lev), {"close": round(float(last), 2), "signal": sig, "vol": round(float(vol), 3),
                              "lev": round(float(lev), 2)}


def _mid(raw: dict) -> dict:
    out = {}
    for pb in raw.get("pricebooks", []):
        if pb.get("bids") and pb.get("asks"):
            out[pb["product_id"]] = (float(pb["bids"][0]["price"]) + float(pb["asks"][0]["price"])) / 2
    return out


def equity(ledger: dict, mids: dict) -> float:
    """Cash plus open profit on each futures position."""
    eq = ledger["cash"]
    for coin, n in ledger["contracts"].items():
        c = CFG["contracts"][coin]
        if n and c["product"] in mids:
            eq += n * c["size"] * (mids[c["product"]] - ledger["entry"][coin])
    return eq


def step(ledger: dict, inp: dict, history: pd.DataFrame) -> dict:
    now = _now(inp.get("now"))
    mids = _mid(inp["best_bid_ask"])
    rep = {"time": _iso(now), "trades": [], "notes": []}

    # funding on open longs since the last run
    if ledger["last_run"]:
        days = (now - _now(ledger["last_run"])).total_seconds() / 86400
        for coin, n in ledger["contracts"].items():
            c = CFG["contracts"][coin]
            if n > 0 and c["product"] in mids:
                ledger["cash"] -= n * c["size"] * mids[c["product"]] * CFG["funding_per_year"] / 365 * days
    ledger["last_run"] = _iso(now)

    if ledger["day"] != last_day(now):
        if not inp.get("candles_1d"):
            raise ValueError("a new day is due: cb-fut-needs asked for candles_1d")
        new = pd.DataFrame({coin: daily_closes(inp["candles_1d"][f"{coin}-USDC"], now) for coin in CFG["contracts"]})
        history = pd.concat([history, new]).sort_index()
        history = history[~history.index.duplicated(keep="last")]
        rep["history"] = history
        eq = equity(ledger, mids)
        for coin, c in CFG["contracts"].items():
            if c["product"] not in mids:
                rep["notes"].append(f"{coin}: no futures price, skipped")
                continue
            w, info = target_weight(history[coin].dropna())
            ledger["signals"][coin] = info
            price = mids[c["product"]]
            want = int(round(w * eq / len(CFG["contracts"]) / (price * c["size"])))
            have = ledger["contracts"][coin]
            if want == have:
                continue
            # close the old position's profit into cash, then hold `want` contracts at this price
            ledger["cash"] += have * c["size"] * (price - ledger["entry"][coin])
            fee = abs(want - have) * c["size"] * price * CFG["fee_rate"]
            ledger["cash"] -= fee
            ledger["contracts"][coin], ledger["entry"][coin] = want, price if want else 0.0
            trade = {"at": _iso(now), "coin": coin, "product": c["product"], "from": have, "to": want,
                     "price": round(price, 2), "fee": round(fee, 4)}
            ledger["trades"].append(trade)
            rep["trades"].append(trade)
        ledger["day"] = last_day(now)
        rep["signals"] = ledger["signals"]

    eq = equity(ledger, mids)
    rep.update({"equity": round(eq, 2), "return": round(eq / ledger["start"] - 1, 4),
                "contracts": dict(ledger["contracts"])})
    return rep


def journal_line(rep: dict) -> str:
    held = ", ".join(f"{n} {c} contract{'s' if n > 1 else ''}" for c, n in rep["contracts"].items() if n) or "flat"
    line = f"- Futures paper test: ${rep['equity']:,.2f} ({rep['return']:+.1%}), holds {held}."
    if rep["trades"]:
        line += " Trades: " + "; ".join(f"{t['coin']} {t['from']}->{t['to']} contracts @ {t['price']}"
                                        for t in rep["trades"]) + "."
    return line + "\n"


def run(input_path: str, ledger_path: Path = LEDGER, journal_path: Path | None = JOURNAL,
        history_path: Path = HISTORY) -> dict:
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else new_ledger()
    rep = step(ledger, json.loads(Path(input_path).read_text()), load_history(history_path))
    if "history" in rep:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        rep.pop("history").to_csv(history_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    if journal_path and journal_path.exists():
        with open(journal_path, "a") as f:
            f.write(journal_line(rep))
    return rep
