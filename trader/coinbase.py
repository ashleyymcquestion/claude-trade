"""Coinbase bot: two weekly crypto strategies, paper traded on Coinbase's own prices.

Coinbase is reached through Claude's Coinbase connector, not an API key, so this module
never talks to Coinbase. An hourly Claude session runs `cb-needs`, fetches what it lists,
runs `cb-run`, and logs the result (COINBASE.md). The code decides every trade.

Strategies (docs/research/crypto-coins.md, trader/cryptomomentum.py), each with half the money:
  btc_trend  hold BTC only while its weekly close is above its 10-week average
  momentum   while BTC is above its 10-week average, hold the `top` coins with the best
             `lookback_weeks` return that are also above their own 10-week average
Weeks end on Sunday (UTC); the targets change once a week, after Sunday's daily candle closes.
Every order is a post-only limit at the best bid (buy) or ask (sell), so it pays the 0.5% maker
fee. A paper order fills only once a later 15-minute candle trades through its price.

cb-run input JSON
  {"now": "2026-10-22T20:37:00Z",
   "best_bid_ask": <coinbase_products_best_bid_ask result>,
   "candles_15m": {"BTC-USDC": <coinbase_products_candles result>, ...},
   "candles_1d": {"BTC-USDC": <coinbase_products_candles result>, ...},   only when cb-needs asks
   "balance": <coinbase_balance result>}                     when cb-needs says balance: true (leftovers report)
"""

from __future__ import annotations

import json
import tomllib
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CONFIG = Path("config.coinbase.toml")
LEDGER = Path("data/coinbase/paper.json")
JOURNAL = Path("journal/coinbase-log.md")


def load_config(path: str | Path = CONFIG) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _now(s: str | None) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00")) if s else datetime.now(timezone.utc)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def product(coin: str, cfg: dict) -> str:
    return f"{coin}-{cfg['quote']}"


def last_week_end(now: datetime) -> str:
    """The most recent Sunday whose daily candle has closed (UTC date)."""
    day = now.astimezone(timezone.utc).date() - timedelta(days=1)          # last finished day
    return str(day - timedelta(days=(day.weekday() + 1) % 7))               # back to Sunday


def new_ledger(cfg: dict) -> dict:
    each = round(cfg["paper_start_usd"] / len(cfg["strategies"]), 2)
    return {"strategies": {s: {"cash": each, "start": each, "coins": {}, "orders": [], "target": {},
                               "week": None} for s in cfg["strategies"]},
            "fills": [], "seq": 0}


# ---------- signals ----------

def weekly_closes(candles_1d: dict, now: datetime) -> pd.DataFrame:
    """Daily candles -> Sunday closes per coin, finished weeks only."""
    cols = {}
    for prod, raw in candles_1d.items():
        c = raw["candles"]
        idx = pd.to_datetime([int(x["start"]) for x in c], unit="s").normalize()
        cols[prod.split("-")[0]] = pd.Series([float(x["close"]) for x in c], index=idx).sort_index()
    daily = pd.DataFrame(cols).sort_index()
    daily = daily[daily.index <= pd.Timestamp(last_week_end(now))]
    return daily[daily.index.dayofweek == 6]


def targets(weekly: pd.DataFrame, cfg: dict) -> tuple[dict, dict]:
    """Target weights per strategy for the latest week, plus the numbers behind them."""
    n, look = cfg["trend_weeks"], cfg["lookback_weeks"]
    info = {}
    for coin in weekly:
        s = weekly[coin].dropna()
        if len(s) < max(n, look + 1):
            continue
        avg = s.iloc[-n:].mean()
        info[coin] = {"close": round(float(s.iloc[-1]), 8), "avg10": round(float(avg), 8),
                      "above": bool(s.iloc[-1] > avg), "ret": round(float(s.iloc[-1] / s.iloc[-1 - look] - 1), 4)}
    btc_up = info.get("BTC", {}).get("above", False)
    out = {"btc_trend": {"BTC": 1.0} if btc_up else {}, "momentum": {}}
    if btc_up:
        ranked = sorted((v["ret"], c) for c, v in info.items() if v["above"] and c in cfg["universe"])
        pick = [c for _, c in reversed(ranked)][:cfg["top"]]
        out["momentum"] = {c: round(1 / len(pick), 6) for c in pick}
    return {s: out[s] for s in cfg["strategies"]}, info


# ---------- order helpers ----------

def _floor(x: float, step: str) -> Decimal:
    st = Decimal(step)
    return (Decimal(str(x)) / st).to_integral_value(rounding=ROUND_DOWN) * st


def _book(raw: dict) -> dict:
    out = {}
    for pb in raw.get("pricebooks", []):
        if pb.get("bids") and pb.get("asks"):
            out[pb["product_id"].split("-")[0]] = (float(pb["bids"][0]["price"]), float(pb["asks"][0]["price"]))
    return out


def _limit_order(coin, side, usd_or_qty, book, cfg, ledger, strat, now) -> dict | None:
    """A post-only limit at the best bid (buy, sized in USDC) or ask (sell, sized in coins)."""
    base_inc, quote_inc = cfg["increments"][coin]
    bid, ask = book[coin]
    price = _floor(bid if side == "BUY" else ask, quote_inc)
    qty = _floor(usd_or_qty / float(price) / (1 + cfg["maker_fee"]), base_inc) if side == "BUY" \
        else _floor(usd_or_qty, base_inc)
    if qty <= 0 or float(qty * price) < cfg["min_order_usd"]:
        return None
    ledger["seq"] += 1
    return {"id": f"cbp-{strat}-{ledger['seq']}", "coin": coin, "side": side, "limit": str(price),
            "qty": str(qty), "placed_at": _iso(now)}


def real_call(order: dict, cfg: dict) -> dict:
    """The exact coinbase_orders_create arguments this paper order stands for."""
    return {"portfolio_id": cfg["portfolio_id"], "product_id": product(order["coin"], cfg),
            "side": order["side"], "type": "limit", "limit_price": order["limit"], "base_size": order["qty"],
            "post_only": True, "time_in_force": "GTC", "client_order_id": order["id"]}


# ---------- paper engine ----------

def _fill(st: dict, o: dict, cfg: dict, ledger: dict, at: str):
    qty, price = float(o["qty"]), float(o["limit"])
    fee = qty * price * cfg["maker_fee"]
    if o["side"] == "BUY":
        st["cash"] += o["reserved"] - qty * price - fee        # give back what the reservation over-held
        st["coins"][o["coin"]] = st["coins"].get(o["coin"], 0.0) + qty
    else:
        st["cash"] += qty * price - fee
    ledger["fills"].append({"id": o["id"], "coin": o["coin"], "side": o["side"], "qty": o["qty"],
                            "price": o["limit"], "fee": round(fee, 6), "at": at})


def _crossed(o: dict, candles: list) -> str | None:
    """Time of the first 15-minute candle after placement that traded through the limit."""
    placed = _now(o["placed_at"]).timestamp()
    limit = float(o["limit"])
    for c in sorted(candles, key=lambda c: int(c["start"])):
        if int(c["start"]) < placed:
            continue
        if (o["side"] == "BUY" and float(c["low"]) < limit) or (o["side"] == "SELL" and float(c["high"]) > limit):
            return _iso(datetime.fromtimestamp(int(c["start"]), timezone.utc))
    return None


def _cancel(st: dict, o: dict):
    if o["side"] == "BUY":
        st["cash"] += o["reserved"]
    else:
        st["coins"][o["coin"]] = st["coins"].get(o["coin"], 0.0) + float(o["qty"])


def _place(st: dict, o: dict, cfg: dict):
    if o["side"] == "BUY":
        o["reserved"] = round(float(o["qty"]) * float(o["limit"]) * (1 + cfg["maker_fee"]), 8)
        st["cash"] -= o["reserved"]
    else:
        st["coins"][o["coin"]] -= float(o["qty"])
    st["orders"].append(o)


def equity(st: dict, book: dict) -> float:
    reserved = sum(o["reserved"] for o in st["orders"] if o["side"] == "BUY")
    coins = dict(st["coins"])
    for o in st["orders"]:
        if o["side"] == "SELL":
            coins[o["coin"]] = coins.get(o["coin"], 0.0) + float(o["qty"])
    return st["cash"] + reserved + sum(q * sum(book[c]) / 2 for c, q in coins.items() if c in book)


def needs(ledger: dict, cfg: dict, now: datetime) -> dict:
    """What cb-run needs fetched this hour."""
    week = last_week_end(now)
    due = any(st["week"] != week for st in ledger["strategies"].values())
    # leftovers with a USDC pair are priced once a week for the leftovers report
    coins = set(cfg["universe"]) | (set(cfg["leftovers"]) & set(cfg["increments"])) if due else set()
    for st in ledger["strategies"].values():
        coins |= {c for c, q in st["coins"].items() if q > 0} | set(st["target"]) | {o["coin"] for o in st["orders"]}
    since = {}
    for st in ledger["strategies"].values():
        for o in st["orders"]:
            p = product(o["coin"], cfg)
            since[p] = min(since.get(p, o["placed_at"]), o["placed_at"])
    return {"week_due": due, "balance": due, "best_bid_ask": sorted(product(c, cfg) for c in coins),
            "candles_15m": {p: {"granularity": "15m", "start": s} for p, s in sorted(since.items())},
            "candles_1d": ({"products": [product(c, cfg) for c in cfg["universe"]], "granularity": "1d",
                            "start": _iso(now - timedelta(days=84))} if due else None)}


def step(ledger: dict, inp: dict, cfg: dict) -> dict:
    now = _now(inp.get("now"))
    book = _book(inp["best_bid_ask"])
    rep = {"time": _iso(now), "fills": [], "placed": [], "cancelled": [], "notes": [], "real_calls": []}

    # 1. paper fills from 15-minute candles
    for name, st in ledger["strategies"].items():
        for o in list(st["orders"]):
            raw = inp.get("candles_15m", {}).get(product(o["coin"], cfg))
            at = _crossed(o, raw["candles"]) if raw else None
            if at:
                st["orders"].remove(o)
                _fill(st, o, cfg, ledger, at)
                rep["fills"].append(f"{name}: {o['side']} {o['qty']} {o['coin']} @ {o['limit']}")

    # 2. new week -> new targets; orders left from the old week are cancelled
    week = last_week_end(now)
    if any(st["week"] != week for st in ledger["strategies"].values()):
        if not inp.get("candles_1d"):
            raise ValueError("a new week is due: cb-needs asked for candles_1d")
        tgt, info = targets(weekly_closes(inp["candles_1d"], now), cfg)
        rep["signals"] = info
        for name, st in ledger["strategies"].items():
            if st["week"] != week:
                for o in st["orders"]:
                    _cancel(st, o)
                    rep["cancelled"].append(f"{name}: {o['id']} (new week)")
                st["orders"], st["target"], st["week"] = [], tgt[name], week
        rep["notes"].append(f"week ending {week}: " + "; ".join(
            f"{n} -> {', '.join(st['target']) or 'cash'}" for n, st in ledger["strategies"].items()))

    # 3. reconcile each strategy with its target
    for name, st in ledger["strategies"].items():
        for o in list(st["orders"]):           # stale limit -> move it to the new best price
            if now - _now(o["placed_at"]) >= timedelta(hours=cfg["reprice_hours"]) and o["coin"] in book:
                st["orders"].remove(o)
                _cancel(st, o)
                rep["cancelled"].append(f"{name}: {o['id']} (repriced)")
        if cfg["kill_switch"]:
            continue
        busy = {o["coin"] for o in st["orders"]}
        eq = equity(st, book)
        for coin, q in sorted(st["coins"].items()):
            if q > 0 and coin not in st["target"] and coin not in busy and coin in book:
                o = _limit_order(coin, "SELL", q, book, cfg, ledger, name, now)
                if o:
                    _place(st, o, cfg)
                    rep["placed"].append(o | {"strategy": name})
                else:
                    rep["notes"].append(f"{name}: {coin} {q} is under the ${cfg['min_order_usd']:.0f} minimum, kept")
        for coin, w in st["target"].items():
            if coin in busy or coin not in book:
                continue
            want, have = w * eq, st["coins"].get(coin, 0.0) * sum(book[coin]) / 2
            if want - have < max(cfg["min_order_usd"], 0.5 * want):
                continue                        # holds it already (enter/exit only, no resizing)
            usd = min(want - have, st["cash"], cfg["max_order_usd"])
            o = _limit_order(coin, "BUY", usd, book, cfg, ledger, name, now)
            if o:
                _place(st, o, cfg)
                rep["placed"].append(o | {"strategy": name})
            else:
                rep["notes"].append(f"{name}: {coin} buy of ${usd:.2f} is under the minimum")
    rep["real_calls"] = [real_call(o, cfg) for o in rep["placed"]]
    for o in rep["placed"]:
        o.pop("reserved", None)

    # 4. scoreboard
    rep["strategies"] = {n: {"equity": round(equity(st, book), 4), "start": st["start"],
                             "return": round(equity(st, book) / st["start"] - 1, 4), "cash": round(st["cash"], 4),
                             "coins": {c: q for c, q in st["coins"].items() if q > 0}, "target": st["target"],
                             "open_orders": len(st["orders"])} for n, st in ledger["strategies"].items()}
    if inp.get("balance") and "signals" in rep:
        rep["leftovers"] = leftovers(inp["balance"], book, cfg, rep["signals"], week, now)
    return rep


def leftovers(balance: dict, book: dict, cfg: dict, signals: dict, week: str, now: datetime) -> list:
    """Old coins in the real account: value and trend, once a week. A sell is only proposed, never placed."""
    out = []
    for acct in balance.get("accounts", []):
        coin = acct["currency"]
        if coin not in cfg["leftovers"]:
            continue
        qty = float(acct["available_balance"]["value"])
        row = {"coin": coin, "qty": qty}
        if coin in book:
            bid, ask = book[coin]
            row["value"] = round(qty * (bid + ask) / 2, 2)
        s = signals.get(coin)
        if s:
            row["above_10wk_avg"] = s["above"]
            row["ret_4wk"] = s["ret"]
            if not s["above"] and row.get("value", 0) >= cfg["min_order_usd"] and coin in cfg["increments"]:
                o = _limit_order(coin, "SELL", qty, book, cfg, {"seq": 0}, "leftover", now)
                if o:
                    o["id"] = f"cbl-{coin}-{week}"
                    row["proposal"] = "below its 10-week average: sell to USDC? (needs the user's yes)"
                    row["real_call"] = real_call(o, cfg)
        out.append(row)
    return out


# ---------- CLI glue ----------

def load_ledger(cfg: dict, path: Path = LEDGER) -> dict:
    return json.loads(path.read_text()) if path.exists() else new_ledger(cfg)


def save_ledger(ledger: dict, path: Path = LEDGER):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2) + "\n")


def journal_entry(rep: dict) -> str:
    pt = _now(rep["time"]).astimezone(ZoneInfo("America/Los_Angeles"))
    lines = [f"## {pt:%Y-%m-%d} ({pt:%-I:%M %p} PT, hourly paper run)"]
    score = ", ".join(f"{n} ${s['equity']:.2f} ({s['return']:+.1%}, holds {', '.join(s['coins']) or 'cash'})"
                      for n, s in rep["strategies"].items())
    lines.append(f"- Paper: {score}.")
    for kind in ("fills", "cancelled"):
        if rep[kind]:
            lines.append(f"- {kind.capitalize()}: " + "; ".join(rep[kind]) + ".")
    if rep["placed"]:
        lines.append("- Placed (paper limit): " + "; ".join(
            f"{o['strategy']} {o['side']} {o['qty']} {o['coin']} @ {o['limit']}" for o in rep["placed"]) + ".")
    for n in rep["notes"]:
        lines.append(f"- {n}")
    props = [r for r in rep.get("leftovers", []) if r.get("proposal")]
    if props:
        lines.append("- Leftover sell proposals (not placed, need a yes): " + ", ".join(
            f"{r['coin']} ${r['value']:.2f}" for r in props) + ".")
    return "\n".join(lines) + "\n"


def run(input_path: str, cfg_path: str = str(CONFIG), ledger_path: Path = LEDGER,
        journal_path: Path | None = JOURNAL) -> dict:
    cfg = load_config(cfg_path)
    ledger = load_ledger(cfg, ledger_path)
    rep = step(ledger, json.loads(Path(input_path).read_text()), cfg)
    if "signals" in rep:
        ledger["signals"] = rep["signals"]
    save_ledger(ledger, ledger_path)
    if journal_path:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        if not journal_path.exists():
            journal_path.write_text("# Coinbase bot log\n\nPaper trading (config.coinbase.toml, COINBASE.md).\n")
        with open(journal_path, "a") as f:
            f.write("\n" + journal_entry(rep))
    return rep
