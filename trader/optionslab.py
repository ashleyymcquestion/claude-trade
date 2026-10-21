"""Options lab: paper-test options strategies with real Robinhood option prices. Places no orders.

Strategies (one contract = 100 shares each, as on Robinhood):
  follow_flow     buy a near-the-money call 30-45 days out on stocks with call-heavy unusual options
                  activity (watchlab source "options" opened today), copying the "smart money" bet
  momentum_calls  the same call on watchlab's velocity / top-gainer picks, instead of the stock
  wheel           on NVDA, QQQ, SPCX: sell a cash-secured put ~5% below the price; if assigned, hold the
                  100 shares and sell covered calls ~5% above cost until they're called away; repeat

Pricing is pessimistic on purpose: buy at the ask, sell at the bid, and skip contracts whose
bid/ask spread is more than 30% of the midpoint. Long calls close at +100%, -50%, or 7 days before
expiration. Short options are held to expiration and settled on the underlying's price at the next
daily run (an approximation of the expiration-day close).

Run with `python -m trader opt-step --input opt_in.json` repeatedly. Each call either prints what the
session must fetch next ({"need": ...}) or, once the file holds everything, records the day and prints
the report. The session adds what was asked to the same file:
    {"now": "...",                                             # optional
     "spots": {"FHN": 23.2, ...},                              # get_equity_quotes last trade price
     "expirations": {"FHN": ["2026-11-20", ...]},              # get_option_chains expiration_dates (all chains)
     "instruments": {"FHN": [{"id", "strike", "type", "expiration"}, ...]},   # get_option_instruments
     "quotes": {"<instrument id>": {"bid": 1.1, "ask": 1.2}}}  # get_option_quotes
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DIR = Path("data/optionslab")
POSITIONS = DIR / "positions.json"
STATE = DIR / "state.json"
NY = ZoneInfo("America/New_York")

WHEEL = ("NVDA", "QQQ", "SPCX")
IDEA_SOURCES = {"follow_flow": ("options",), "momentum_calls": ("velocity", "gainers")}
PER_DAY = {"follow_flow": 3, "momentum_calls": 3}
MAX_OPEN = {"follow_flow": 10, "momentum_calls": 10}
DTE = (30, 45)
PUT_OTM, CALL_OTM = 0.95, 1.05
TARGET, STOP, CLOSE_DTE = 1.0, -0.5, 7
MAX_SPREAD = 0.30


def _now(inp: dict) -> datetime:
    ts = inp.get("now")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC) if ts else datetime.now(UTC)


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def load() -> tuple[list[dict], dict]:
    return _load(POSITIONS, []), _load(STATE, {"last_run": "", "wheel": {}})


def save(positions: list[dict], state: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    POSITIONS.write_text(json.dumps(positions, indent=1) + "\n")
    STATE.write_text(json.dumps(state, indent=1) + "\n")


def todays_ideas(watch_trades: list, today: str) -> dict[str, list[str]]:
    """Underlyings for the call strategies, from watchlab trades opened today."""
    out = {}
    for strat, sources in IDEA_SOURCES.items():
        syms: list[str] = []
        for src in sources:
            for t in watch_trades:
                if t.opened == today and t.source == src and t.symbol not in syms:
                    syms.append(t.symbol)
        out[strat] = syms[:PER_DAY[strat]]
    return out


def _wanted(positions: list[dict], state: dict, ideas: dict[str, list[str]], spots: dict) -> list[dict]:
    """New contracts wanted today: [{strategy, underlying, type, side, target_strike?}]."""
    want = []
    open_pos = [p for p in positions if p["status"] == "open"]
    for strat, syms in ideas.items():
        room = MAX_OPEN[strat] - sum(p["strategy"] == strat for p in open_pos)
        held = {p["underlying"] for p in open_pos if p["strategy"] == strat}
        for s in [s for s in syms if s not in held][:max(0, room)]:
            want.append({"strategy": strat, "underlying": s, "type": "call", "side": "long"})
    for s in WHEEL:
        if any(p["strategy"] == "wheel" and p["underlying"] == s for p in open_pos):
            continue
        w = state.get("wheel", {}).get(s) or {}
        if w.get("shares_cost"):
            want.append({"strategy": "wheel", "underlying": s, "type": "call", "side": "short"})
        else:
            want.append({"strategy": "wheel", "underlying": s, "type": "put", "side": "short"})
    return want


def _pick_expiration(dates: list[str], today: date) -> str | None:
    ok = sorted(d for d in set(dates) if DTE[0] <= (date.fromisoformat(d) - today).days <= DTE[1])
    return ok[0] if ok else None


def _pick_strike(w: dict, spot: float, cost: float | None, strikes: list[float]) -> float | None:
    strikes = sorted(set(strikes))
    if w["side"] == "long":                                   # first strike at or above the price
        above = [k for k in strikes if k >= spot]
        return above[0] if above else None
    if w["type"] == "put":                                    # highest strike <= 95% of the price
        below = [k for k in strikes if k <= spot * PUT_OTM]
        return below[-1] if below else None
    floor = max(cost or 0.0, spot) * CALL_OTM                 # covered call: >= 5% above cost and price
    above = [k for k in strikes if k >= floor]
    return above[0] if above else None


def _quote_ok(q: dict | None) -> bool:
    if not q:
        return False
    bid, ask = float(q.get("bid") or 0), float(q.get("ask") or 0)
    mid = (bid + ask) / 2
    return bid > 0 and ask > 0 and (ask - bid) <= MAX_SPREAD * mid


def step(inp: dict, positions: list[dict], state: dict, watch_trades: list) -> tuple[dict, list[dict], dict]:
    """Return ({"need": {...}}, unchanged, unchanged) until the input holds everything, then
    ({"done": report}, new positions, new state). The arguments are never modified."""
    orig = (positions, state)
    positions, state = copy.deepcopy(positions), copy.deepcopy(state)
    now = _now(inp)
    today = now.astimezone(NY).date()
    if state.get("last_run") == today.isoformat():
        return {"done": {"already_ran": True}}, *orig
    spots = {k.upper(): float(v) for k, v in (inp.get("spots") or {}).items()}
    ideas = todays_ideas(watch_trades, today.isoformat())
    open_pos = [p for p in positions if p["status"] == "open"]
    wheel_state = state.setdefault("wheel", {})

    unders = {p["underlying"] for p in open_pos} | set(WHEEL) | {s for v in ideas.values() for s in v}
    missing = sorted(u for u in unders if u not in spots)
    if missing:
        return {"need": {"spots": missing}}, *orig

    # settle expirations first, so a wheel can roll into its next leg today
    report = {"opened": [], "closed": [], "skipped": []}
    for p in open_pos:
        if date.fromisoformat(p["expiration"]) < today:
            _settle(p, spots[p["underlying"]], today, wheel_state, report)
    open_pos = [p for p in positions if p["status"] == "open"]

    want = _wanted(positions, state, ideas, spots)
    exps = inp.get("expirations") or {}
    need_exp = sorted({w["underlying"] for w in want if w["underlying"] not in exps})
    if need_exp:
        return {"need": {"expirations": need_exp}}, *orig
    insts = inp.get("instruments") or {}
    need_inst, chosen = [], []
    for w in want:
        exp = _pick_expiration(exps.get(w["underlying"]) or [], today)
        if not exp:
            report["skipped"].append(f"{w['strategy']} {w['underlying']}: no expiration {DTE[0]}-{DTE[1]} days out")
            continue
        rows = [r for r in insts.get(w["underlying"]) or [] if r["type"] == w["type"] and r["expiration"] == exp]
        if not rows:
            need_inst.append({"symbol": w["underlying"], "expiration": exp, "type": w["type"]})
            continue
        cost = (wheel_state.get(w["underlying"]) or {}).get("shares_cost")
        k = _pick_strike(w, spots[w["underlying"]], cost, [float(r["strike"]) for r in rows])
        if k is None:
            report["skipped"].append(f"{w['strategy']} {w['underlying']}: no suitable strike")
            continue
        r = next(r for r in rows if float(r["strike"]) == k)
        chosen.append({**w, "expiration": exp, "strike": k, "instrument_id": r["id"]})
    if need_inst:
        return {"need": {"instruments": need_inst}}, *orig

    quotes = inp.get("quotes") or {}
    need_q = sorted({c["instrument_id"] for c in chosen} | {p["instrument_id"] for p in open_pos if p["side"] == "long"})
    need_q = [i for i in need_q if i not in quotes]
    if need_q:
        return {"need": {"quotes": need_q}}, *orig

    # exits for long calls
    for p in open_pos:
        if p["side"] != "long":
            continue
        q = quotes.get(p["instrument_id"]) or {}
        bid = float(q.get("bid") or 0)
        r = bid / p["entry"] - 1
        dte = (date.fromisoformat(p["expiration"]) - today).days
        why = "target" if r >= TARGET else "stop" if r <= STOP else "expiring" if dte <= CLOSE_DTE else ""
        if why:
            _close(p, bid, today, why, report, spots[p["underlying"]])

    # new positions
    for c in chosen:
        q = quotes.get(c["instrument_id"])
        if not _quote_ok(q):
            report["skipped"].append(f"{c['strategy']} {c['underlying']} {c['strike']:g}{c['type'][0].upper()}: "
                                     f"no usable bid/ask {q}")
            continue
        price = float(q["ask"]) if c["side"] == "long" else float(q["bid"])
        p = {"id": str(uuid.uuid4())[:8], **c, "opened": today.isoformat(), "entry": price,
             "underlying_entry": spots[c["underlying"]], "status": "open",
             "closed": "", "exit": 0.0, "exit_reason": "", "pnl": 0.0, "ret": 0.0}
        positions.append(p)
        report["opened"].append({k: p[k] for k in ("strategy", "underlying", "side", "type", "strike",
                                                    "expiration", "entry")})
    state["last_run"] = today.isoformat()
    return {"done": {**report, "scoreboard": scoreboard(positions, state, spots)}}, positions, state


def _settle(p: dict, spot: float, today: date, wheel_state: dict, report: dict) -> None:
    intrinsic = max(0.0, spot - p["strike"]) if p["type"] == "call" else max(0.0, p["strike"] - spot)
    if p["side"] == "long":
        _close(p, intrinsic, today, "expired", report, spot)
        return
    w = wheel_state.setdefault(p["underlying"], {"shares_cost": None, "share_pnl": 0.0, "premium": 0.0})
    w["premium"] = w.get("premium", 0.0) + p["entry"] * 100
    p.update(status="closed", closed=today.isoformat(), exit=0.0, pnl=round(p["entry"] * 100, 2))
    if intrinsic > 0 and p["type"] == "put":
        w["shares_cost"] = p["strike"]
        p["exit_reason"] = "assigned: bought 100 shares"
    elif intrinsic > 0 and p["type"] == "call":
        w["share_pnl"] = w.get("share_pnl", 0.0) + (p["strike"] - w["shares_cost"]) * 100
        w["shares_cost"] = None
        p["exit_reason"] = "called away: sold 100 shares"
    else:
        p["exit_reason"] = "expired worthless (premium kept)"
    p["ret"] = round(p["pnl"] / (p["strike"] * 100), 4)
    report["closed"].append({"strategy": "wheel", "underlying": p["underlying"], "leg": p["type"],
                             "pnl": p["pnl"], "why": p["exit_reason"]})


def _close(p: dict, price: float, today: date, why: str, report: dict, spot: float) -> None:
    p.update(status="closed", closed=today.isoformat(), exit=price, exit_reason=why,
             pnl=round((price - p["entry"]) * 100, 2), ret=round(price / p["entry"] - 1, 4),
             underlying_exit=spot)
    report["closed"].append({"strategy": p["strategy"], "underlying": p["underlying"], "pnl": p["pnl"],
                             "ret": p["ret"], "stock_ret": round(spot / p["underlying_entry"] - 1, 4), "why": why})


def scoreboard(positions: list[dict], state: dict, spots: dict) -> dict:
    out = {}
    for strat in ("follow_flow", "momentum_calls"):
        closed = [p for p in positions if p["strategy"] == strat and p["status"] == "closed"]
        n = len(closed)
        out[strat] = {"open": sum(p["strategy"] == strat and p["status"] == "open" for p in positions),
                      "closed": n,
                      "win_rate": round(sum(p["pnl"] > 0 for p in closed) / n, 3) if n else None,
                      "avg_return_on_premium": round(sum(p["ret"] for p in closed) / n, 4) if n else None,
                      "avg_stock_return": round(sum(p.get("underlying_exit", 0) / p["underlying_entry"] - 1
                                                    for p in closed) / n, 4) if n else None,
                      "total_pnl_usd": round(sum(p["pnl"] for p in closed), 2)}
    wheel = {}
    for s in WHEEL:
        w = state.get("wheel", {}).get(s) or {}
        unreal = (spots.get(s, 0) - w["shares_cost"]) * 100 if w.get("shares_cost") and s in spots else 0.0
        wheel[s] = {"premium_kept_usd": round(w.get("premium", 0.0), 2),
                    "share_pnl_usd": round(w.get("share_pnl", 0.0), 2),
                    "holding_shares_at": w.get("shares_cost"), "unrealized_shares_usd": round(unreal, 2)}
    out["wheel"] = wheel
    return out
