"""Gap-down rule for real money on the Robinhood agentic account. Built OFF ("enabled": false).

The user asked on Oct 26 to have it ready, switched off, until the watchlab "gaps" paper test clears
its bar (20 closed trades). Backtest on NVDA/AMD/PLTR/QQQ, 2019 on, after costs: a 5%+ gap down bought
at the open and sold at yesterday's close (the fill) or the day's close won 55% of 83 trades, +0.95%
average; 3%+ gaps won 53% of 235, +0.39%.

Rule, checked by each hourly run during regular hours:
  - flat, before 11:00 AM New York, no gap trade yet today: if a symbol opened at least `gap` (5%)
    below yesterday's close and the cash covers `amount` ($1), buy `amount` of the deepest gap;
  - holding: sell exactly the shares this rule bought (never the stock bot's) once the price is back
    to yesterday's close, or at the first run after 3:30 PM New York.
At most one gap trade a day. Fractional shares only take market orders, so nothing rests at the broker.

Ledger (committed, data/gap-live.json):
    {"enabled": false, "amount": "1.00", "gap": 0.05, "symbols": [...], "status": "flat" | "holding",
     "position": {"symbol", "order_id", "quantity", "price", "at", "prev_close"} | null,
     "last_day": "YYYY-MM-DD" | null, "trades": [...]}

State file the session writes for `plan`:
    {"now": "...", "cash": "1.53", "open_order": false,
     "quotes": {"NVDA": {"open": 200.1, "prev_close": 211.0, "mark": 201.5}, ...}}
`open_order` is true if any order for the held (or, when flat, any listed) symbol is still open.
When holding, the stock bot's state must leave out the gap shares (its quantity minus ours).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

LEDGER = Path("data/gap-live.json")
NY = ZoneInfo("America/New_York")
REF_NAMESPACE = uuid.UUID("5d1f3a27-8e4c-4b9a-a6d2-0c7e1f9b3a58")

DEFAULT_LEDGER = {"enabled": False, "amount": "1.00", "gap": 0.05, "symbols": ["NVDA", "AMD", "PLTR", "QQQ"],
                  "status": "flat", "position": None, "last_day": None, "trades": []}


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load_ledger(path: Path = LEDGER) -> dict:
    return {**DEFAULT_LEDGER, **json.loads(path.read_text())} if path.exists() else dict(DEFAULT_LEDGER)


def plan(ledger: dict, state: dict) -> dict:
    """Return {"orders": [...], "notes": [...]}; at most one order, printed exactly as to place."""
    from quantbot.robinhood_stocks import nyse_open

    now = datetime.fromisoformat(state["now"]).astimezone(UTC) if state.get("now") else datetime.now(UTC)
    ny = now.astimezone(NY)
    if not ledger.get("enabled"):
        return {"orders": [], "notes": ["disabled in the ledger (waiting for the paper test and the user's OK)"]}
    if state.get("open_order"):
        return {"orders": [], "notes": ["a gap order is still open; waiting for it"]}
    if not nyse_open(now):
        return {"orders": [], "notes": ["market closed"]}
    quotes = state.get("quotes", {})

    if ledger["status"] == "holding":
        pos = ledger["position"]
        mark = _f(quotes.get(pos["symbol"], {}).get("mark"))
        why = ""
        if mark <= 0:
            return {"orders": [], "notes": [f"no {pos['symbol']} price; not checking the exit"]}
        if mark >= _f(pos["prev_close"]):
            why = f"gap filled: {mark:.2f} >= {_f(pos['prev_close']):.2f}"
        elif ny.time() >= time(15, 30) or ny.date().isoformat() != ledger["last_day"]:
            why = "end of day"
        if not why:
            return {"orders": [], "notes": [f"holding {pos['symbol']} at {mark:.2f}; sells at {_f(pos['prev_close']):.2f} or after 3:30 PM"]}
        return {"orders": [{"symbol": pos["symbol"], "side": "sell", "type": "market", "market_hours": "regular_hours",
                            "quantity": pos["quantity"], "reason": why,
                            "ref_id": str(uuid.uuid5(REF_NAMESPACE, f"sell-{pos['order_id']}"))}], "notes": []}

    today = ny.date().isoformat()
    if ledger.get("last_day") == today:
        return {"orders": [], "notes": ["already made today's gap trade"]}
    if ny.time() >= time(11, 0):
        return {"orders": [], "notes": ["after 11:00 AM New York; no new gap trades today"]}
    gaps = []
    for sym in ledger["symbols"]:
        q = quotes.get(sym, {})
        op, pc = _f(q.get("open")), _f(q.get("prev_close"))
        if op > 0 and pc > 0 and op <= pc * (1 - _f(ledger["gap"])):
            gaps.append((op / pc - 1, sym))
    if not gaps:
        return {"orders": [], "notes": [f"no {_f(ledger['gap']):.0%}+ gap down today"]}
    g, sym = min(gaps)
    amount, cash = _f(ledger["amount"]), _f(state.get("cash"))
    if cash + 1e-9 < amount:
        return {"orders": [], "notes": [f"{sym} gapped {g:.1%} but cash ${cash:.2f} < ${amount:.2f}"]}
    return {"orders": [{"symbol": sym, "side": "buy", "type": "market", "market_hours": "regular_hours",
                        "dollar_amount": f"{amount:.2f}", "prev_close": _f(quotes[sym]["prev_close"]),
                        "reason": f"gap down {g:.1%}",
                        "ref_id": str(uuid.uuid5(REF_NAMESPACE, f"buy-{sym}-{today}"))}], "notes": []}


def record(ledger: dict, side: str, symbol: str, order_id: str, quantity: str, price: str, at: str,
           prev_close: float | None = None) -> dict:
    """Record a FILLED order from `plan`; returns the updated ledger."""
    fill = {"symbol": symbol, "order_id": order_id, "quantity": quantity, "price": price, "at": at}
    if side == "buy":
        if ledger["status"] != "flat" or prev_close is None:
            raise ValueError("buy needs a flat ledger and --prev-close")
        day = datetime.fromisoformat(at).astimezone(NY).date().isoformat()
        return {**ledger, "status": "holding", "position": {**fill, "prev_close": prev_close}, "last_day": day}
    if side == "sell":
        pos = ledger["position"]
        if ledger["status"] != "holding" or symbol != pos["symbol"]:
            raise ValueError(f"sell of {symbol} recorded while {ledger['status']}")
        pnl = round(_f(quantity) * _f(price) - _f(pos["quantity"]) * _f(pos["price"]), 4)
        trade = {"symbol": symbol, "buy": pos, "sell": fill, "pnl": pnl}
        return {**ledger, "status": "flat", "position": None, "trades": [*ledger["trades"], trade]}
    raise ValueError(f"unknown side {side!r}")
