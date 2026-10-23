"""Options lab: paper-test options strategies with real Robinhood option prices. Places no orders.

Ideas come from watchlab (trades it opened today), a pullback signal and the wheel list:
  follow_flow     stocks with call-heavy unusual options activity (watchlab source "options")
  momentum_calls  watchlab's velocity / top-gainer picks
  pullback_calls  NVDA, AMD, PLTR or QQQ above its 200-day average with RSI(14) under 35: a strong stock on a
                  short dip, the best call signal in the backtest (python -m trader opt-backtest)
  wheel           NVDA, QQQ, SPCX: cash-secured puts; if assigned, covered calls until called away
  budget/...      NVDA, AMD, PLTR, QQQ: $100-or-less bets in six variants (BUDGET_VARIANTS): the call or the put
                  nearest the price that costs $100 or less, sold when it doubles or after 2, 5 or 7 trading
                  days (backtests: python -m trader.budgetbacktest, python -m trader.twoday); no edge filter.
                  One open bet per variant and stock at a time

Strategy choice ("only take trades with an edge"): for every bullish idea the lab prices three
structures with optionlab and keeps the one with the best expected profit per dollar at risk:
  long_call         buy the first strike at or above the price
  bull_call_spread  buy that call, sell the first strike >= 10% above the price (cheaper, capped)
  bull_put_spread   sell the highest put <= 95% of the price, buy the highest put <= 90% (a credit)
A structure only qualifies if its probability of profit is at least MIN_POP AND its expected profit is
positive. Both come from optionlab's Black-Scholes model run with the stock's REALIZED volatility over
the last 30 days, not the volatility the option price implies: selling options that are priced richer
than the stock really moves (or buying ones priced cheaper) is the edge being tested. If nothing
qualifies, the idea is skipped and the report says why. No setup wins every time; this filter only
keeps the odds and the expected payoff on our side, and the scoreboard checks whether the model's
odds come true. The wheel's puts and covered calls go through the same filter.

Call tests ("<idea>/call"): every bullish idea ALSO opens a plain at-the-money long call, with no edge
filter (its model odds are still recorded). The user wants to find a call strategy that wins, so the
scoreboard compares each idea's raw calls with what the filter picked, and the model's odds with reality.

Pricing is pessimistic: buy legs at the ask, sell legs at the bid; skip a leg whose bid/ask spread is
more than 30% of its midpoint. Exits: long calls close at +100%, -50%, or 7 days before expiration;
spreads close once they've made 50% of their maximum profit, or 7 days before expiration; wheel legs
are held to expiration. Expired positions settle on the stock price at the next daily run.

Run with `python -m trader opt-step --input opt_in.json` repeatedly. Each call either prints what the
session must fetch next ({"need": ...}) or, once the file holds everything, records the day:
    {"now": "...",                                              # optional
     "spots": {"FHN": 23.2, ...},                               # get_equity_quotes last trade price
     "history": {"FHN": [closes, oldest first], ...},           # get_equity_historicals day closes, ~45 days
     "expirations": {"FHN": ["2026-11-20", ...]},               # get_option_chains expiration_dates
     "instruments": {"FHN": [{"id", "strike", "type", "expiration"}, ...]},    # get_option_instruments
     "quotes": {"<instrument id>": {"bid": 1.1, "ask": 1.2}}}   # get_option_quotes
"""

from __future__ import annotations

import copy
import json
import math
import uuid
import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DIR = Path("data/optionslab")
POSITIONS = DIR / "positions.json"
STATE = DIR / "state.json"
NY = ZoneInfo("America/New_York")

WHEEL = ("NVDA", "QQQ", "SPCX")
IDEA_SOURCES = {"follow_flow": ("options",), "momentum_calls": ("velocity", "gainers")}
PULLBACK = ("NVDA", "AMD", "PLTR", "QQQ")      # signal from data/bars (the CLI adds them as "long_closes")
IDEAS = ("follow_flow", "momentum_calls", "pullback_calls")
PER_DAY = {"follow_flow": 3, "momentum_calls": 3, "pullback_calls": 4}
MAX_OPEN = {**{i: 10 for i in IDEAS}, **{f"{i}/call": 10 for i in IDEAS}}
BULLISH = ("long_call", "bull_call_spread", "bull_put_spread")
DTE = (30, 45)
PUT_OTM, PUT_WING, CALL_WING, CC_OTM = 0.95, 0.90, 1.10, 1.05
MIN_POP = 0.55
RATE = 0.04
VOL_DAYS = 30
TARGET, STOP, CLOSE_DTE, SPREAD_TAKE = 1.0, -0.5, 7, 0.5
MAX_SPREAD = 0.30
BUDGET = 1.00                  # $ per share: one contract for $100 or less
BUDGET_RANGE = (0.6, 1.4)      # strikes to price, as a share of the stock price
BUDGET_SYMBOLS = PULLBACK
BUDGET_VARIANTS = {f"budget/{side}-{days}d": (f"{side}_nearest", days)   # name: (budgetbacktest rule, trading days held)
                   for side in ("call", "put") for days in (2, 5, 7)}


def _now(inp: dict) -> datetime:
    ts = inp.get("now")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC) if ts else datetime.now(UTC)


def _migrate(p: dict) -> dict:
    """Positions recorded before multi-leg support had one leg stored flat."""
    if "legs" in p:
        return p
    action = "buy" if p["side"] == "long" else "sell"
    structure = "long_call" if p["side"] == "long" else ("cash_secured_put" if p["type"] == "put" else "covered_call")
    leg = {"instrument_id": p["instrument_id"], "type": p["type"], "action": action, "strike": p["strike"],
           "entry": p["entry"]}
    keep = {k: p[k] for k in ("id", "strategy", "underlying", "expiration", "opened", "underlying_entry",
                              "status", "closed", "exit_reason", "pnl", "ret") if k in p}
    return {**keep, "structure": structure, "legs": [leg], "net_debit": _net_debit([leg]), "pop": None,
            "ev": None, "note": "opened before the edge filter"}


def load() -> tuple[list[dict], dict]:
    positions = json.loads(POSITIONS.read_text()) if POSITIONS.exists() else []
    state = json.loads(STATE.read_text()) if STATE.exists() else {"last_run": "", "wheel": {}}
    return [_migrate(p) for p in positions], state


def save(positions: list[dict], state: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    POSITIONS.write_text(json.dumps(positions, indent=1) + "\n")
    STATE.write_text(json.dumps(state, indent=1) + "\n")


def realized_vol(closes: list[float]) -> float | None:
    """Annualized volatility of the last VOL_DAYS daily log returns."""
    c = [float(x) for x in closes][-(VOL_DAYS + 1):]
    if len(c) < 21:
        return None
    r = [math.log(b / a) for a, b in zip(c, c[1:])]
    mean = sum(r) / len(r)
    return math.sqrt(sum((x - mean) ** 2 for x in r) / (len(r) - 1) * 252)


def todays_ideas(watch_trades: list, today: str) -> dict[str, list[str]]:
    """Underlyings for the bullish strategies, from watchlab trades opened today."""
    out = {}
    for strat, sources in IDEA_SOURCES.items():
        syms: list[str] = []
        for src in sources:
            for t in watch_trades:
                if t.opened == today and t.source == src and t.symbol not in syms:
                    syms.append(t.symbol)
        out[strat] = syms[:PER_DAY[strat]]
    return out


def pullback_ideas(long_closes: dict, spots: dict) -> list[str]:
    """Underlyings whose closes (oldest first) plus today's spot show the backtest's pullback signal."""
    from .callbacktest import signal

    return [s for s in PULLBACK if s in long_closes and s in spots
            and signal("pullback", [float(x) for x in long_closes[s]] + [spots[s]])][:PER_DAY["pullback_calls"]]


def _wanted(positions: list[dict], state: dict, ideas: dict[str, list[str]]) -> list[dict]:
    want = []
    open_pos = [p for p in positions if p["status"] == "open"]
    for strat, syms in ideas.items():
        room = MAX_OPEN[strat] - sum(p["strategy"] == strat for p in open_pos)
        held = {p["underlying"] for p in open_pos if p["strategy"] == strat}
        for s in [s for s in syms if s not in held][:max(0, room)]:
            want.append({"strategy": strat, "underlying": s, "structures": BULLISH, "types": ("call", "put")})
        test = f"{strat}/call"
        room = MAX_OPEN[test] - sum(p["strategy"] == test for p in open_pos)
        held = {p["underlying"] for p in open_pos if p["strategy"] == test}
        for s in [s for s in syms if s not in held][:max(0, room)]:
            want.append({"strategy": test, "underlying": s, "structures": ("long_call",), "types": ("call",),
                         "test": True})
    for name in BUDGET_VARIANTS:
        for s in BUDGET_SYMBOLS:
            if not any(p["strategy"] == name and p["underlying"] == s for p in open_pos):
                want.append({"strategy": name, "underlying": s, "structures": ("budget",), "types": ("call", "put"),
                             "test": True})
    for s in WHEEL:
        if any(p["strategy"] == "wheel" and p["underlying"] == s for p in open_pos):
            continue
        if (state.get("wheel", {}).get(s) or {}).get("shares_cost"):
            want.append({"strategy": "wheel", "underlying": s, "structures": ("covered_call",), "types": ("call",)})
        else:
            want.append({"strategy": "wheel", "underlying": s, "structures": ("cash_secured_put",), "types": ("put",)})
    return want


def _pick_expiration(dates: list[str], today: date) -> str | None:
    ok = sorted(d for d in set(dates) if DTE[0] <= (date.fromisoformat(d) - today).days <= DTE[1])
    return ok[0] if ok else None


def _first_at_or_above(rows: list[dict], x: float) -> dict | None:
    above = sorted((r for r in rows if float(r["strike"]) >= x - 1e-9), key=lambda r: float(r["strike"]))
    return above[0] if above else None


def _last_at_or_below(rows: list[dict], x: float) -> dict | None:
    below = sorted((r for r in rows if float(r["strike"]) <= x + 1e-9), key=lambda r: float(r["strike"]))
    return below[-1] if below else None


def build(structure: str, spot: float, cost: float | None, calls: list[dict], puts: list[dict]) -> list[dict] | None:
    """Legs [{row, action}] for a structure, or None if the chain can't make it."""
    if structure in ("long_call", "bull_call_spread"):
        atm = _first_at_or_above(calls, spot)
        if not atm:
            return None
        if structure == "long_call":
            return [{"row": atm, "action": "buy"}]
        wing = _first_at_or_above(calls, spot * CALL_WING)
        if not wing or float(wing["strike"]) <= float(atm["strike"]):
            return None
        return [{"row": atm, "action": "buy"}, {"row": wing, "action": "sell"}]
    if structure in ("bull_put_spread", "cash_secured_put"):
        short = _last_at_or_below(puts, spot * PUT_OTM)
        if not short:
            return None
        if structure == "cash_secured_put":
            return [{"row": short, "action": "sell"}]
        wing = _last_at_or_below(puts, spot * PUT_WING)
        if not wing or float(wing["strike"]) >= float(short["strike"]):
            return None
        return [{"row": short, "action": "sell"}, {"row": wing, "action": "buy"}]
    if structure == "covered_call":
        row = _first_at_or_above(calls, max(cost or 0.0, spot) * CC_OTM)
        return [{"row": row, "action": "sell"}] if row else None
    raise ValueError(structure)


def _quote_ok(q: dict | None) -> bool:
    if not q:
        return False
    bid, ask = float(q.get("bid") or 0), float(q.get("ask") or 0)
    return bid > 0 and ask > 0 and (ask - bid) <= MAX_SPREAD * (bid + ask) / 2


def evaluate(legs: list[dict], spot: float, vol: float, start: date, expiration: str) -> dict:
    """optionlab: probability of profit and expected profit (per contract, $) at expiration."""
    from optionlab import Inputs, run_strategy

    strategy = [{"type": l["type"], "strike": float(l["strike"]), "premium": float(l["entry"]), "n": 100,
                 "action": l["action"]} for l in legs]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        out = run_strategy(Inputs(stock_price=spot, volatility=vol, interest_rate=RATE, min_stock=spot * 0.3,
                                  max_stock=spot * 2.5, price_step=max(0.01, round(spot / 2000, 2)),
                                  start_date=start, target_date=date.fromisoformat(expiration),
                                  strategy=strategy))
    pop = float(out.probability_of_profit)
    ev = pop * float(out.expected_profit_if_profitable or 0) + (1 - pop) * float(out.expected_loss_if_unprofitable or 0)
    return {"pop": round(pop, 4), "ev": round(ev, 2), "max_profit": round(float(out.maximum_return_in_the_domain), 2),
            "max_loss": round(-float(out.minimum_return_in_the_domain), 2)}


def _net_debit(legs: list[dict]) -> float:
    """Per share: positive = paid (debit), negative = received (credit)."""
    return round(sum(l["entry"] if l["action"] == "buy" else -l["entry"] for l in legs), 4)


def step(inp: dict, positions: list[dict], state: dict, watch_trades: list) -> tuple[dict, list[dict], dict]:
    """Return ({"need": {...}}, unchanged, unchanged) until the input holds everything, then
    ({"done": report}, new positions, new state). The arguments are never modified."""
    orig = (positions, state)
    positions, state = copy.deepcopy([_migrate(p) for p in positions]), copy.deepcopy(state)
    now = _now(inp)
    today = now.astimezone(NY).date()
    if state.get("last_run") == today.isoformat():
        return {"done": {"already_ran": True}}, *orig
    spots = {k.upper(): float(v) for k, v in (inp.get("spots") or {}).items()}
    ideas = todays_ideas(watch_trades, today.isoformat())
    wheel_state = state.setdefault("wheel", {})
    open_pos = [p for p in positions if p["status"] == "open"]

    long_closes = inp.get("long_closes") or {}
    unders = ({p["underlying"] for p in open_pos} | set(WHEEL) | {s for v in ideas.values() for s in v}
              | {s for s in PULLBACK if s in long_closes} | set(BUDGET_SYMBOLS))
    if missing := sorted(u for u in unders if u not in spots):
        return {"need": {"spots": missing}}, *orig
    ideas["pullback_calls"] = pullback_ideas(long_closes, spots)

    report = {"opened": [], "closed": [], "skipped": []}
    for p in open_pos:
        if date.fromisoformat(p["expiration"]) < today:
            _settle(p, spots[p["underlying"]], today, wheel_state, report)
    open_pos = [p for p in positions if p["status"] == "open"]

    want = _wanted(positions, state, ideas)
    hist = {k.upper(): v for k, v in (inp.get("history") or {}).items()}
    if need_h := sorted({w["underlying"] for w in want if w["underlying"] not in hist}):
        return {"need": {"history": need_h}}, *orig
    exps = inp.get("expirations") or {}
    if need_e := sorted({w["underlying"] for w in want if w["underlying"] not in exps}):
        return {"need": {"expirations": need_e}}, *orig

    insts = inp.get("instruments") or {}
    need_i, plans = [], []
    for w in want:
        u = w["underlying"]
        exp = _pick_expiration(exps.get(u) or [], today)
        vol = realized_vol(hist.get(u) or [])
        if not exp or not vol:
            report["skipped"].append(f"{w['strategy']} {u}: " + ("no expiration 30-45 days out" if not exp
                                                                 else "not enough price history"))
            continue
        rows = {t: [r for r in insts.get(u) or [] if r["type"] == t and r["expiration"] == exp] for t in w["types"]}
        if missing_t := [t for t in w["types"] if not rows[t]]:
            need_i += [{"symbol": u, "expiration": exp, "type": t} for t in missing_t]
            continue
        if w["structures"] == ("budget",):
            lo, hi = BUDGET_RANGE
            cands = [r for t in w["types"] for r in rows[t] if lo * spots[u] <= float(r["strike"]) <= hi * spots[u]]
            plans.append({**w, "structure": "budget", "expiration": exp, "vol": vol,
                          "legs": [{"row": r, "action": "buy"} for r in cands]})
            continue
        cost = (wheel_state.get(u) or {}).get("shares_cost")
        for s in w["structures"]:
            if legs := build(s, spots[u], cost, rows.get("call", []), rows.get("put", [])):
                plans.append({**w, "structure": s, "expiration": exp, "vol": vol, "legs": legs})
    if need_i:
        return {"need": {"instruments": need_i}}, *orig

    quotes = inp.get("quotes") or {}
    ids = {l["row"]["id"] for p in plans for l in p["legs"]} | {
        l["instrument_id"] for p in open_pos if p["strategy"] != "wheel" for l in p["legs"]}
    if need_q := sorted(i for i in ids if i not in quotes):
        return {"need": {"quotes": need_q}}, *orig

    for p in open_pos:
        if p["strategy"] != "wheel":
            _check_exit(p, quotes, spots[p["underlying"]], today, report)

    # pick the best qualifying structure per idea
    by_idea: dict[tuple, list[dict]] = {}
    for pl in plans:
        by_idea.setdefault((pl["strategy"], pl["underlying"]), []).append(pl)
    for (strat, u), cands in by_idea.items():
        if cands[0]["structure"] == "budget":
            _open_budget(cands[0], quotes, spots[u], today, positions, report)
            continue
        scored, notes = [], []
        test = cands[0].get("test", False)
        for pl in cands:
            qs = [quotes.get(l["row"]["id"]) for l in pl["legs"]]
            if not all(_quote_ok(q) for q in qs):
                notes.append(f"{pl['structure']}: bid/ask too wide")
                continue
            legs = [{"instrument_id": l["row"]["id"], "type": l["row"]["type"], "action": l["action"],
                     "strike": float(l["row"]["strike"]),
                     "entry": float(q["ask"]) if l["action"] == "buy" else float(q["bid"])}
                    for l, q in zip(pl["legs"], qs)]
            ev = evaluate(legs, spots[u], pl["vol"], today, pl["expiration"])
            if not test and (ev["pop"] < MIN_POP or ev["ev"] <= 0):
                notes.append(f"{pl['structure']}: odds {ev['pop']:.0%}, expected ${ev['ev']:+.0f}")
                continue
            scored.append((ev["ev"] / max(ev["max_loss"], 1.0), pl, legs, ev))
        if not scored:
            report["skipped"].append(f"{strat} {u}: no edge ({'; '.join(notes)})")
            continue
        _, pl, legs, ev = max(scored, key=lambda x: x[0])
        pos = {"id": str(uuid.uuid4())[:8], "strategy": strat, "structure": pl["structure"], "underlying": u,
               "expiration": pl["expiration"], "legs": legs, "net_debit": _net_debit(legs),
               "opened": today.isoformat(), "underlying_entry": spots[u], "vol": round(pl["vol"], 4), **ev,
               "status": "open", "closed": "", "exit_reason": "", "pnl": 0.0, "ret": 0.0}
        positions.append(pos)
        report["opened"].append({k: pos[k] for k in ("strategy", "structure", "underlying", "expiration",
                                                      "net_debit", "pop", "ev")}
                                | {"legs": [f"{l['action']} {l['strike']:g}{l['type'][0].upper()} @ {l['entry']}"
                                            for l in legs]})
    state["last_run"] = today.isoformat()
    return {"done": {**report, "scoreboard": scoreboard(positions, state, spots)}}, positions, state


def _open_budget(pl: dict, quotes: dict, spot: float, today: date, positions: list[dict], report: dict) -> None:
    """A budget/... bet: budgetbacktest.pick chooses among the priced contracts; buy at the ask, sell at the bid."""
    from . import budgetbacktest as bb

    rows = {(r["row"]["type"], float(r["row"]["strike"])): r["row"] for r in pl["legs"]
            if _quote_ok(quotes.get(r["row"]["id"]))}
    day, exp = today.isoformat(), pl["expiration"]
    # pick() adds SLIP to buys and subtracts it from sells; feed it the mid so buys land near the ask
    chain = {(exp, t, k): {day: (float(quotes[r["id"]]["bid"]) + float(quotes[r["id"]]["ask"])) / 2}
             for (t, k), r in rows.items()}
    rule = BUDGET_VARIANTS[pl["strategy"]][0]
    picked = bb.pick(rule, chain, spot, day, exp)
    if not picked:
        report["skipped"].append(f"{pl['strategy']} {pl['underlying']}: nothing under ${BUDGET * 100:.0f}")
        return
    legs = []
    for t, k, side in picked:
        q = quotes[rows[(t, k)]["id"]]
        legs.append({"instrument_id": rows[(t, k)]["id"], "type": t, "action": "buy" if side > 0 else "sell",
                     "strike": k, "entry": float(q["ask"]) if side > 0 else float(q["bid"])})
    debit = _net_debit(legs)
    if not 0 < debit <= BUDGET:
        report["skipped"].append(f"{pl['strategy']} {pl['underlying']}: costs ${debit * 100:.0f} at the ask/bid")
        return
    ev = evaluate(legs, spot, pl["vol"], today, exp)
    pos = {"id": str(uuid.uuid4())[:8], "strategy": pl["strategy"], "structure": rule, "underlying": pl["underlying"],
           "expiration": exp, "legs": legs, "net_debit": debit, "opened": today.isoformat(), "underlying_entry": spot,
           "vol": round(pl["vol"], 4), **ev, "status": "open", "closed": "", "exit_reason": "", "pnl": 0.0, "ret": 0.0}
    positions.append(pos)
    report["opened"].append({k: pos[k] for k in ("strategy", "structure", "underlying", "expiration", "net_debit",
                                                  "pop", "ev")}
                            | {"legs": [f"{l['action']} {l['strike']:g}{l['type'][0].upper()} @ {l['entry']}" for l in legs]})


def _close_value(legs: list[dict], quotes: dict) -> float:
    """What closing now brings in, per share: sell longs at the bid, buy back shorts at the ask."""
    return sum(float(quotes[l["instrument_id"]].get("bid") or 0) if l["action"] == "buy"
               else -float(quotes[l["instrument_id"]].get("ask") or 0) for l in legs)


def _check_exit(p: dict, quotes: dict, spot: float, today: date, report: dict) -> None:
    value = _close_value(p["legs"], quotes)
    pnl = (value - p["net_debit"]) * 100
    dte = (date.fromisoformat(p["expiration"]) - today).days
    if p["strategy"] in BUDGET_VARIANTS:
        r = pnl / (p["net_debit"] * 100)
        import numpy as np

        held = int(np.busday_count(p["opened"], today.isoformat()))
        hold = BUDGET_VARIANTS[p["strategy"]][1]
        why = ("doubled" if r >= TARGET else f"held {hold} days" if held >= hold
               else "expiring" if dte <= 0 else "")
    elif p["structure"] == "long_call":
        r = pnl / (p["net_debit"] * 100)
        why = "target" if r >= TARGET else "stop" if r <= STOP else "expiring" if dte <= CLOSE_DTE else ""
    else:
        max_profit = p.get("max_profit") or 0
        why = ("took 50% of max profit" if max_profit and pnl >= SPREAD_TAKE * max_profit
               else "expiring" if dte <= CLOSE_DTE else "")
    if why:
        _close(p, value, pnl, today, why, report, spot)


def _close(p: dict, value: float, pnl: float, today: date, why: str, report: dict, spot: float) -> None:
    risk = p["net_debit"] * 100 if p["net_debit"] > 0 else (p.get("max_loss") or abs(p["net_debit"]) * 100)
    p.update(status="closed", closed=today.isoformat(), exit_value=round(value, 4), exit_reason=why,
             pnl=round(pnl, 2), ret=round(pnl / risk, 4) if risk else 0.0, underlying_exit=spot)
    report["closed"].append({"strategy": p["strategy"], "structure": p["structure"], "underlying": p["underlying"],
                             "pnl": p["pnl"], "stock_ret": round(spot / p["underlying_entry"] - 1, 4), "why": why})


def _intrinsic(leg: dict, spot: float) -> float:
    return max(0.0, spot - leg["strike"]) if leg["type"] == "call" else max(0.0, leg["strike"] - spot)


def _settle(p: dict, spot: float, today: date, wheel_state: dict, report: dict) -> None:
    if p["strategy"] != "wheel":
        value = sum(_intrinsic(l, spot) * (1 if l["action"] == "buy" else -1) for l in p["legs"])
        _close(p, value, (value - p["net_debit"]) * 100, today, "expired", report, spot)
        return
    leg = p["legs"][0]
    w = wheel_state.setdefault(p["underlying"], {"shares_cost": None, "share_pnl": 0.0, "premium": 0.0})
    w["premium"] = w.get("premium", 0.0) + leg["entry"] * 100
    p.update(status="closed", closed=today.isoformat(), exit_value=0.0, pnl=round(leg["entry"] * 100, 2),
             underlying_exit=spot)
    if _intrinsic(leg, spot) > 0 and leg["type"] == "put":
        w["shares_cost"] = leg["strike"]
        p["exit_reason"] = "assigned: bought 100 shares"
    elif _intrinsic(leg, spot) > 0 and leg["type"] == "call":
        w["share_pnl"] = w.get("share_pnl", 0.0) + (leg["strike"] - w["shares_cost"]) * 100
        w["shares_cost"] = None
        p["exit_reason"] = "called away: sold 100 shares"
    else:
        p["exit_reason"] = "expired worthless (premium kept)"
    p["ret"] = round(p["pnl"] / (leg["strike"] * 100), 4)
    report["closed"].append({"strategy": "wheel", "structure": p["structure"], "underlying": p["underlying"],
                             "pnl": p["pnl"], "why": p["exit_reason"]})


def _stats(closed: list[dict]) -> dict:
    n = len(closed)
    pops = [p["pop"] for p in closed if p.get("pop") is not None]
    return {"closed": n, "win_rate": round(sum(p["pnl"] > 0 for p in closed) / n, 3) if n else None,
            "model_odds": round(sum(pops) / len(pops), 3) if pops else None,   # what the model promised
            "total_pnl_usd": round(sum(p["pnl"] for p in closed), 2)}


def scoreboard(positions: list[dict], state: dict, spots: dict) -> dict:
    positions = [_migrate(p) for p in positions]
    out: dict = {"by_idea": {}, "by_structure": {}}
    for strat in [x for i in IDEAS for x in (i, f"{i}/call")] + list(BUDGET_VARIANTS):
        out["by_idea"][strat] = {"open": sum(p["strategy"] == strat and p["status"] == "open" for p in positions),
                                 **_stats([p for p in positions if p["strategy"] == strat and p["status"] == "closed"])}
    for s in BULLISH:
        out["by_structure"][s] = {"open": sum(p["structure"] == s and p["status"] == "open" for p in positions),
                                  **_stats([p for p in positions if p["structure"] == s and p["status"] == "closed"])}
    wheel = {}
    for s in WHEEL:
        w = state.get("wheel", {}).get(s) or {}
        unreal = (spots[s] - w["shares_cost"]) * 100 if w.get("shares_cost") and s in spots else 0.0
        wheel[s] = {"premium_kept_usd": round(w.get("premium", 0.0), 2),
                    "share_pnl_usd": round(w.get("share_pnl", 0.0), 2),
                    "holding_shares_at": w.get("shares_cost"), "unrealized_shares_usd": round(unreal, 2)}
    out["wheel"] = wheel
    return out
