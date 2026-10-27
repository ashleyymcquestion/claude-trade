"""One-off real-money CHIP (USD.AI) test buy on the Robinhood agentic account.

The user OK'd this on Oct 26 after the CHIP paper hold (trader/coinwatch.py) ran up: once that paper
test has closed (Oct 28, 4:46 PM PT), buy $0.25 of CHIP-USD once, but only if the bid is still above
the paper test's start price; otherwise skip the test for good. With stop_pct set, sell the whole
position if the bid falls that far below the fill price. With trail_pct set, the ledger keeps the
highest bid seen since the buy ("peak") and sells everything once the bid falls trail_pct below it, so
a climb is kept and a reversal is cut. The user first chose no stop, then (same day) a 25% trailing
exit: wide because CHIP swings 10-15% in a normal day. No profit target. Outside the crypto strategy
(rh-plan never sees CHIP).

Ledger (committed, data/chip-test.json):
    {"enabled": true, "amount": "0.25", "stop_pct": 0.15 | null, "trail_pct": 0.25 | null, "peak": <float> | null, "not_before": "<ISO time>",
     "min_bid": <paper start price>, "status": "waiting" | "holding" | "done" | "skipped",
     "buy": {"order_id", "quantity", "price", "at"} | null, "stop": <float> | null,
     "sell": {...} | null, "note": "..."}

State file the session writes for `plan`:
    {"now": "...", "cash": "1.53", "bid": 0.053, "ask": 0.054, "quantity": "0",
     "open_order": false, "paper_closed": true}
`quantity` is the CHIP quantity_transferable; `open_order` is true if any CHIP order is still open;
`paper_closed` is true once data/coinwatch/CHIP-state.json has "closed".
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

LEDGER = Path("data/chip-test.json")
SYMBOL = "CHIP-USD"
REF_NAMESPACE = uuid.UUID("7c2e9d41-0a5b-4f6e-8d3c-1b9a7e5f2c64")

DEFAULT_LEDGER = {"enabled": True, "amount": "0.25", "stop_pct": None, "trail_pct": 0.25, "peak": None,
                  "not_before": "2026-10-28T16:46-07:00", "min_bid": 0.04596719,
                  "status": "waiting", "buy": None, "stop": None, "sell": None}


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)


def load_ledger(path: Path = LEDGER) -> dict:
    return {**DEFAULT_LEDGER, **json.loads(path.read_text())} if path.exists() else dict(DEFAULT_LEDGER)


def plan(ledger: dict, state: dict) -> dict:
    """Return {"orders": [...], "notes": [...]}; at most one order, printed exactly as to place.
    When the test is skipped or a new peak is seen it also returns "ledger", which the caller saves."""
    now = _ts(state["now"]) if state.get("now") else datetime.now(UTC)
    bid, qty = _f(state.get("bid")), _f(state.get("quantity"))
    if not ledger.get("enabled", True):
        return {"orders": [], "notes": ["disabled in the ledger"]}
    if state.get("open_order"):
        return {"orders": [], "notes": ["a CHIP order is still open; waiting for it"]}
    if bid <= 0:
        return {"orders": [], "notes": ["no CHIP bid; nothing checked"]}

    status = ledger["status"]
    if status == "waiting":
        amount, cash = _f(ledger["amount"]), _f(state.get("cash"))
        wait = ledger.get("wait_for_paper", True)   # false once the user says to buy before the paper test ends
        if now < _ts(ledger["not_before"]) or (wait and not state.get("paper_closed")):
            return {"orders": [], "notes": [f"waiting for the paper test to close ({ledger['not_before']})"]}
        if qty > 0:
            return {"orders": [], "notes": [f"already holds {qty} CHIP without a recorded buy; not buying (needs a look)"]}
        if bid <= _f(ledger["min_bid"]):
            note = f"skipped: bid {bid} is not above the paper start {ledger['min_bid']}"
            return {"orders": [], "notes": [note], "ledger": {**ledger, "status": "skipped", "note": note}}
        if cash + 1e-9 < amount:
            return {"orders": [], "notes": [f"waiting: cash ${cash:.2f} < ${amount:.2f}"]}
        return {"orders": [{"symbol": SYMBOL, "side": "buy", "type": "market", "dollar_amount": f"{amount:.2f}",
                            "ref_id": str(uuid.uuid5(REF_NAMESPACE, "buy")),
                            "reason": f"test buy: bid {bid} > paper start {ledger['min_bid']}"}], "notes": []}
    if status == "holding":
        stop = ledger.get("stop")
        if qty <= 0:
            return {"orders": [], "notes": ["ledger says holding but no CHIP is held; not selling (needs a look)"]}
        out_ledger, why, notes = None, "", []
        if ledger.get("trail_pct") is not None:
            peak = max(_f(ledger.get("peak")), bid)
            exit_at = round(peak * (1 - _f(ledger["trail_pct"])), 8)
            if peak != ledger.get("peak"):
                out_ledger = {**ledger, "peak": peak}
            if bid <= exit_at:
                why = f"trailing exit: bid {bid} <= {exit_at} (peak {peak})"
            else:
                notes.append(f"holding at bid {bid}: peak {peak}, sells at or below {exit_at}")
        if not why and stop is not None and bid <= _f(stop):
            why = f"stop hit: bid {bid} <= {stop}"
        elif not why and not notes:
            notes.append(f"holding at bid {bid}: " + (f"stop {stop}" if stop is not None else "no stop"))
        orders = [{"symbol": SYMBOL, "side": "sell", "type": "market", "quantity": str(state["quantity"]),
                   "ref_id": str(uuid.uuid5(REF_NAMESPACE, "sell")), "reason": why}] if why else []
        return {"orders": orders, "notes": notes, **({"ledger": out_ledger} if out_ledger else {})}
    return {"orders": [], "notes": [f"test {status}; nothing to do"]}


def record(ledger: dict, side: str, order_id: str, quantity: str, price: str, at: str) -> dict:
    """Record a FILLED order from `plan`; returns the updated ledger."""
    fill = {"order_id": order_id, "quantity": quantity, "price": price, "at": at}
    if side == "buy":
        if ledger["status"] != "waiting":
            raise ValueError(f"buy recorded while {ledger['status']}")
        sp = ledger.get("stop_pct")
        stop = round(_f(price) * (1 - _f(sp)), 8) if sp is not None else None
        return {**ledger, "status": "holding", "buy": fill, "stop": stop, "peak": _f(price)}
    if side == "sell":
        if ledger["status"] != "holding":
            raise ValueError(f"sell recorded while {ledger['status']}")
        return {**ledger, "status": "done", "sell": fill}
    raise ValueError(f"unknown side {side!r}")
