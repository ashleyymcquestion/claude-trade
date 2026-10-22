"""One-off SpaceX (SPCX) test position on the Robinhood agentic account.

Outside both strategies: buy a fixed dollar amount of SPCX once, as soon as the account has
that much free cash during regular hours, then sell the whole position when the price reaches
the profit target (fill price x (1 + target_pct)) or falls to the stop (fill price x (1 - stop_pct)).
Either can be off (null): since Oct 22 the user wants it sold only at a profit, so the stop is off.
Robinhood only takes market orders for fractional shares, so both are checked by each hourly run
instead of resting at the broker.

Ledger (committed, data/spcx-test.json):
    {"enabled": true, "amount": "1.00", "stop_pct": 0.08 | null, "target_pct": 0.08 | null,
     "status": "waiting" | "holding" | "done",
     "buy": {"order_id", "quantity", "price", "at"} | null, "stop": <float> | null, "target": <float> | null,
     "sell": {"order_id", "quantity", "price", "at"} | null}

State file the session writes for `plan`:
    {"now": "...", "cash": "0.53", "mark": 160.2, "quantity": "0", "open_order": false}
`quantity` is the SPCX shares held; `open_order` is true if any SPCX order is still open.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

LEDGER = Path("data/spcx-test.json")
SYMBOL = "SPCX"
REF_NAMESPACE = uuid.UUID("3b8f0a52-6d1e-4c7b-9f2a-5e4d1c0b7a93")

DEFAULT_LEDGER = {"enabled": True, "amount": "1.00", "stop_pct": 0.08, "target_pct": None, "status": "waiting",
                  "buy": None, "stop": None, "target": None, "sell": None}


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _now(state: dict) -> datetime:
    ts = state.get("now")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC) if ts else datetime.now(UTC)


def load_ledger(path: Path = LEDGER) -> dict:
    return {**DEFAULT_LEDGER, **json.loads(path.read_text())} if path.exists() else dict(DEFAULT_LEDGER)


def plan(ledger: dict, state: dict) -> dict:
    """Return {"orders": [...], "notes": [...]}; at most one order, printed exactly as to place."""
    from quantbot.robinhood_stocks import nyse_open

    now = _now(state)
    notes, orders = [], []
    qty, mark = _f(state.get("quantity")), _f(state.get("mark"))
    if not ledger.get("enabled", True):
        return {"orders": [], "notes": ["disabled in the ledger"]}
    if state.get("open_order"):
        return {"orders": [], "notes": ["an SPCX order is still open; waiting for it"]}
    if not nyse_open(now):
        return {"orders": [], "notes": ["market closed (fractional orders are regular hours only)"]}

    status = ledger["status"]
    if status == "waiting":
        amount = _f(ledger["amount"])
        cash = _f(state.get("cash"))
        if qty > 0:
            notes.append(f"already holds {qty} SPCX without a recorded buy; not buying (needs a look)")
        elif cash + 1e-9 < amount:
            notes.append(f"waiting: cash ${cash:.2f} < ${amount:.2f}")
        else:
            orders.append({"symbol": SYMBOL, "side": "buy", "type": "market", "market_hours": "regular_hours",
                           "dollar_amount": f"{amount:.2f}", "ref_id": str(uuid.uuid5(REF_NAMESPACE, "buy")),
                           "reason": f"test buy: cash ${cash:.2f} >= ${amount:.2f}"})
    elif status == "holding":
        stop, target = ledger.get("stop"), ledger.get("target")
        why = ""
        if qty <= 0:
            notes.append("ledger says holding but no SPCX is held; not selling (needs a look)")
        elif mark <= 0:
            notes.append("no SPCX price; not checking the stop or target")
        elif target is not None and mark >= _f(target):
            why = f"target hit: {mark:.2f} >= {_f(target):.2f}"
        elif stop is not None and mark <= _f(stop):
            why = f"stop hit: {mark:.2f} <= {_f(stop):.2f}"
        else:
            notes.append(f"holding at {mark:.2f}: target {_f(target):.2f}" if target is not None else
                         f"holding at {mark:.2f}: no target")
            notes[-1] += f", stop {_f(stop):.2f}" if stop is not None else ", no stop (sells only at a profit)"
        if why:
            orders.append({"symbol": SYMBOL, "side": "sell", "type": "market", "market_hours": "regular_hours",
                           "quantity": str(state["quantity"]), "ref_id": str(uuid.uuid5(REF_NAMESPACE, "sell")),
                           "reason": why})
    else:
        notes.append("test finished; nothing to do")
    return {"orders": orders, "notes": notes}


def record(ledger: dict, side: str, order_id: str, quantity: str, price: str, at: str) -> dict:
    """Record a FILLED order from `plan`; returns the updated ledger."""
    fill = {"order_id": order_id, "quantity": quantity, "price": price, "at": at}
    if side == "buy":
        if ledger["status"] != "waiting":
            raise ValueError(f"buy recorded while {ledger['status']}")
        sp, tp = ledger.get("stop_pct"), ledger.get("target_pct")
        stop = round(_f(price) * (1 - _f(sp)), 4) if sp is not None else None
        target = round(_f(price) * (1 + _f(tp)), 4) if tp is not None else None
        return {**ledger, "status": "holding", "buy": fill, "stop": stop, "target": target}
    if side == "sell":
        if ledger["status"] != "holding":
            raise ValueError(f"sell recorded while {ledger['status']}")
        return {**ledger, "status": "done", "sell": fill}
    raise ValueError(f"unknown side {side!r}")
