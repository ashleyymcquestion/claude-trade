"""Watchlab: paper-test trade ideas from top gainers, buzz, politicians and social trends.

No real orders. Once per trading day (the first hourly run after 10:00 AM New York) the session
gathers four kinds of ideas and this module turns them into $1 paper trades, then checks every
open paper trade against stop / target / time exits. The scoreboard shows which source, if any,
beats simply holding QQQ, so a source only gets real money once it has a record.

Sources:
  gainers      top % gainers today (liquid stocks: market cap > $2B, price > $5, volume > 1M)
  buzz         unusual volume today (relative volume >= 3x) while up, a proxy for social buzz
  politicians  newly disclosed congressional BUYs (stock or options) from get_politician_trades
  social       tickers the session found trending on X / Threads / StockTwits / Reddit via web search
  options      unusual options activity, call-heavy: options volume >= 3x normal and calls >= 2x puts
               (the idea behind Market Rebellion's "Heat Seeker"; a flagged trade can also be a hedge)
  velocity     price moving AND speeding up on rising volume, any ticker: up > 2% today, up > 0.5% in the
               last hour, volume >= 2x normal (Excite Capital's "read the present, measure velocity and
               acceleration of price, volume and time" idea, without predicting anything)
  stacked      tickers flagged by 2+ of the sources above on the same day: signals that "stack up"
               like crossing ripples in a pond, while a lone signal is more likely noise
  gaps         stocks that opened 3%+ below yesterday's close and haven't recovered yet: bet the gap
               "fills". Same-day trade: `gap_check` runs every hourly run and closes it once the price
               is back at yesterday's close, or near the end of the day (backtest: docs/research/watchlab-strategies.md)
  dual_thrust  NVDA, AMD, PLTR, QQQ: range = max(highest high - lowest close, highest close - lowest low) over
               the last 4 days; paper-buy when the price is DT_K x range above today's open, sell if it falls
               DT_K x range below the open, or near the end of the day. Checked every hourly run by `dt_check`
               (backtest on 10-minute bars: python -m trader.intradaybacktest)

Input file for `run` (written by the session):
    {"now": "...",                                  # optional
     "gainers": [{"symbol", "price", "pct"}],       # from preview_scan
     "buzz": [{"symbol", "price", "rel_volume"}],   # from preview_scan
     "politicians": [<raw get_politician_trades entries>],
     "social": [{"symbol", "why", "url"}],
     "options": [{"symbol", "price", "rel_options_volume", "call_put"}],   # from preview_scan
     "velocity": [{"symbol", "price", "pct_1d", "pct_1h", "rel_volume"}],  # from preview_scan
     "gaps": [{"symbol", "price", "prev_close", "open"}],                 # from preview_scan
     "quotes": {"QQQ": 726.5, "<symbol>": <last price>, ...}}   # every symbol in `needs`
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DIR = Path("data/watchlab")
TRADES = DIR / "trades.csv"
STATE = DIR / "state.json"
NY = ZoneInfo("America/New_York")
BENCH = "QQQ"

PICKS = {"gainers": 5, "buzz": 3, "politicians": 5, "social": 3, "options": 3, "velocity": 3, "stacked": 3,
         "gaps": 5, "dual_thrust": 4}                                                                                    # new paper trades per day, max
MAX_HOLD = {"gainers": 5, "buzz": 5, "politicians": 20, "social": 5, "options": 5, "velocity": 5, "stacked": 5,
            "gaps": 1, "dual_thrust": 1}                                                                                 # trading days
STOP, TARGET = -0.08, 0.15
MIN_CALL_PUT = 2.0
VELOCITY_MIN = {"pct_1d": 0.02, "pct_1h": 0.005, "rel_volume": 2.0}
POLITICIAN_FRESH_DAYS = 14
GAP_MIN = 0.03                 # opened at least 3% below yesterday's close
GAP_CLOSE_AFTER = time(15, 30)
DT_SYMBOLS, DT_DAYS, DT_K = ("NVDA", "AMD", "PLTR", "QQQ"), 4, 0.5
BARS = Path("data/bars")  # New York time: gap trades still open after this close at the current price


@dataclass
class Trade:
    source: str
    symbol: str
    opened: str
    entry: float
    bench_entry: float
    reason: str
    status: str = "open"
    days: int = 0
    closed: str = ""
    exit: float = 0.0
    bench_exit: float = 0.0
    exit_reason: str = ""
    target: float = 0.0            # gaps: yesterday's close, where the gap counts as filled
    stop: float = 0.0              # dual_thrust: sell at or below this

    @property
    def ret(self) -> float:
        return self.exit / self.entry - 1 if self.exit else 0.0

    @property
    def excess(self) -> float:
        return self.ret - (self.bench_exit / self.bench_entry - 1) if self.exit else 0.0


def _now(inp: dict) -> datetime:
    ts = inp.get("now")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC) if ts else datetime.now(UTC)


def load_trades(path: Path = TRADES) -> list[Trade]:
    if not path.exists():
        return []
    types = {f.name: f.type for f in fields(Trade)}
    out = []
    for row in csv.DictReader(path.open()):
        out.append(Trade(**{k: (float(v) if types[k] == "float" else int(v) if types[k] == "int" else v)
                            for k, v in row.items() if v is not None}))
    return out


def save_trades(trades: list[Trade], path: Path = TRADES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[f.name for f in fields(Trade)])
        w.writeheader()
        w.writerows(asdict(t) for t in trades)


def load_state(path: Path = STATE) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"last_run": "", "seen_politician_trades": []}


def due(state: dict, now: datetime) -> bool:
    """Once per trading day, on the first run at or after 10:00 AM New York."""
    from quantbot.robinhood_stocks import nyse_open

    ny = now.astimezone(NY)
    return nyse_open(now) and ny.time() >= time(10, 0) and state.get("last_run") != ny.date().isoformat()


def _pol_key(t: dict) -> str:
    return "|".join(str(t.get(k, "")) for k in ("politician_name", "symbol", "asset_type", "transaction_type",
                                                 "transaction_date", "disclosure_date"))


def _fresh_politician_buys(raw: list[dict], seen: set[str], today: date) -> list[dict]:
    out = []
    for t in raw:
        sym = (t.get("symbol") or "").strip()
        if (t.get("transaction_type") != "BUY" or t.get("asset_type") not in ("EQUITY", "OPTION") or not sym
                or _pol_key(t) in seen):
            continue
        try:
            disclosed = date.fromisoformat(t["disclosure_date"])
        except (KeyError, ValueError):
            continue
        if (today - disclosed).days <= POLITICIAN_FRESH_DAYS:
            out.append(t)
    # biggest disclosed amounts first, then the most recent disclosure
    return sorted(out, key=lambda t: (-float((t.get("amount_range") or {}).get("max") or 0), t["disclosure_date"]),
                  reverse=False)


def candidates(inp: dict, trades: list[Trade], state: dict) -> dict[str, list[tuple[str, str]]]:
    """New ideas per source as (symbol, reason), skipping symbols already open in that source."""
    today = _now(inp).astimezone(NY).date()
    open_keys = {(t.source, t.symbol) for t in trades if t.status == "open"}
    out: dict[str, list[tuple[str, str]]] = {}

    def add(source: str, sym: str, reason: str) -> None:
        sym = sym.upper().strip()
        picks = out.setdefault(source, [])
        if sym and (source, sym) not in open_keys and sym not in {s for s, _ in picks} and len(picks) < PICKS[source]:
            picks.append((sym, reason))

    for g in sorted(inp.get("gainers") or [], key=lambda g: -float(g["pct"])):
        add("gainers", g["symbol"], f"up {float(g['pct']) * 100:.1f}% today")
    for b in sorted(inp.get("buzz") or [], key=lambda b: -float(b["rel_volume"])):
        add("buzz", b["symbol"], f"{float(b['rel_volume']):.1f}x normal volume")
    seen = set(state.get("seen_politician_trades") or [])
    for t in _fresh_politician_buys(inp.get("politicians") or [], seen, today):
        add("politicians", t["symbol"], f"{t['politician_name']} bought {t['asset_type'].lower()} "
                                        f"{t['transaction_date']} (disclosed {t['disclosure_date']})")
    for s in inp.get("social") or []:
        add("social", s["symbol"], f"{s.get('why', 'trending')} ({s.get('url', '')})")
    for o in sorted(inp.get("options") or [], key=lambda o: -float(o["rel_options_volume"])):
        if float(o["call_put"]) >= MIN_CALL_PUT:
            add("options", o["symbol"], f"options volume {float(o['rel_options_volume']):.1f}x normal, "
                                        f"calls {float(o['call_put']):.1f}x puts")
    fast = [v for v in inp.get("velocity") or [] if all(float(v[k]) > m for k, m in VELOCITY_MIN.items())]
    for v in sorted(fast, key=lambda v: -float(v["pct_1h"]) * float(v["rel_volume"])):
        add("velocity", v["symbol"], f"up {float(v['pct_1d']) * 100:.1f}% today, {float(v['pct_1h']) * 100:.1f}% "
                                     f"in the last hour, {float(v['rel_volume']):.1f}x volume")

    for g in sorted(_open_gaps(inp), key=_gap):
        add("gaps", g["symbol"], f"opened {_gap(g) * -100:.1f}% below yesterday's close {float(g['prev_close']):.2f}")

    # every symbol each source flagged today (before the per-source caps), most sources first
    flagged = {"gainers": {g["symbol"] for g in inp.get("gainers") or []},
               "buzz": {b["symbol"] for b in inp.get("buzz") or []},
               "politicians": {t["symbol"] for t in _fresh_politician_buys(inp.get("politicians") or [], seen, today)},
               "social": {s["symbol"] for s in inp.get("social") or []},
               "options": {o["symbol"] for o in inp.get("options") or [] if float(o["call_put"]) >= MIN_CALL_PUT},
               "velocity": {v["symbol"] for v in fast}}
    hits: dict[str, list[str]] = {}
    for source, syms in flagged.items():
        for sym in syms:
            hits.setdefault(sym.upper().strip(), []).append(source)
    for sym, sources in sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if len(sources) >= 2:
            add("stacked", sym, "flagged by " + " + ".join(sources))
    return out


def _gap(g: dict) -> float:
    return float(g["open"]) / float(g["prev_close"]) - 1


def _open_gaps(inp: dict) -> list[dict]:
    """Gap-downs that haven't filled yet (price still below yesterday's close)."""
    return [g for g in inp.get("gaps") or [] if _gap(g) <= -GAP_MIN and float(g["price"]) < float(g["prev_close"])]


def needs(inp: dict, trades: list[Trade], state: dict) -> list[str]:
    """Symbols the session must quote before `run`: the benchmark, open trades, and new ideas
    that don't already carry a price."""
    priced = {r["symbol"].upper() for k in ("gainers", "buzz", "options", "velocity", "gaps") for r in (inp.get(k) or [])}
    syms = {BENCH} | {t.symbol for t in trades if t.status == "open"}
    syms |= {s for ideas in candidates(inp, trades, state).values() for s, _ in ideas if s not in priced}
    return sorted(syms)


def run(inp: dict, trades: list[Trade], state: dict) -> tuple[list[Trade], dict, dict]:
    now = _now(inp)
    today = now.astimezone(NY).date().isoformat()
    quotes = {k.upper(): float(v) for k, v in (inp.get("quotes") or {}).items()}
    for g in sum((inp.get(k) or [] for k in ("gainers", "buzz", "options", "velocity", "gaps")), []):
        quotes.setdefault(g["symbol"].upper(), float(g["price"]))
    bench = quotes.get(BENCH)
    if not bench:
        raise ValueError(f"no {BENCH} quote")
    report = {"opened": [], "closed": [], "unpriced": []}

    # exits first (one check per trading day)
    for t in trades:
        if t.status != "open" or t.opened == today or t.source in ("gaps", "dual_thrust"):   # exit hourly
            continue
        px = quotes.get(t.symbol)
        if not px:
            report["unpriced"].append(t.symbol)
            continue
        t.days += 1
        r = px / t.entry - 1
        why = ("stop" if r <= STOP else "target" if r >= TARGET
               else "time" if t.days >= MAX_HOLD[t.source] else "")
        if why:
            t.status, t.closed, t.exit, t.bench_exit, t.exit_reason = "closed", today, px, bench, why
            report["closed"].append({"source": t.source, "symbol": t.symbol, "ret": round(t.ret, 4),
                                     "excess": round(t.excess, 4), "why": why})

    ideas = candidates(inp, trades, state)
    targets = {g["symbol"].upper(): float(g["prev_close"]) for g in inp.get("gaps") or []}
    for source, picks in ideas.items():
        for sym, reason in picks:
            px = quotes.get(sym)
            if not px:
                report["unpriced"].append(sym)
                continue
            trades.append(Trade(source, sym, today, px, bench, reason,
                                target=targets[sym] if source == "gaps" else 0.0))
            report["opened"].append({"source": source, "symbol": sym, "price": px, "reason": reason})

    seen = set(state.get("seen_politician_trades") or [])
    seen |= {_pol_key(t) for t in inp.get("politicians") or []}
    state = {**state, "last_run": today, "seen_politician_trades": sorted(seen)}
    return trades, state, report


def gap_needs(trades: list[Trade]) -> list[str]:
    """Symbols to quote for gap_check; empty when no gap trade is open."""
    syms = sorted({t.symbol for t in trades if t.source == "gaps" and t.status == "open"})
    return [BENCH] + syms if syms else []


def gap_check(inp: dict, trades: list[Trade]) -> dict:
    """Every hourly run: close gap trades whose price is back at yesterday's close ("filled"), and all
    of them once it's GAP_CLOSE_AFTER in New York or the trade is from an earlier day ("end of day")."""
    ny = _now(inp).astimezone(NY)
    today = ny.date().isoformat()
    quotes = {k.upper(): float(v) for k, v in (inp.get("quotes") or {}).items()}
    report = {"closed": [], "unpriced": []}
    for t in trades:
        if t.source != "gaps" or t.status != "open":
            continue
        px, bench = quotes.get(t.symbol), quotes.get(BENCH)
        if not px or not bench:
            report["unpriced"].append(t.symbol)
            continue
        why = ("filled" if px >= t.target else
               "end of day" if ny.time() >= GAP_CLOSE_AFTER or t.opened != today else "")
        if why:
            t.status, t.closed, t.exit, t.bench_exit, t.exit_reason, t.days = "closed", today, px, bench, why, 1
            report["closed"].append({"source": t.source, "symbol": t.symbol, "ret": round(t.ret, 4),
                                     "excess": round(t.excess, 4), "why": why})
    return report


def dt_levels(today: date, folder: Path = BARS) -> dict[str, float]:
    """Dual Thrust range per symbol from the DT_DAYS daily bars before `today`."""
    out = {}
    for sym in DT_SYMBOLS:
        try:
            with open(folder / f"{sym}.csv") as f:
                rows = [r for r in csv.DictReader(f) if r["timestamp"][:10] < today.isoformat()][-DT_DAYS:]
        except FileNotFoundError:
            continue
        if len(rows) < DT_DAYS:
            continue
        hi, lo, cl = ([float(r[k]) for r in rows] for k in ("high", "low", "close"))
        out[sym] = max(max(hi) - min(cl), max(cl) - min(lo))
    return out


def dt_needs(trades: list[Trade], now: datetime) -> list[str]:
    """Symbols to quote (and get today's open for) on this run; empty outside 10:00-15:59 New York."""
    ny = now.astimezone(NY)
    if not (time(10, 0) <= ny.time() < time(16, 0)):
        return []
    return [BENCH, *(s for s in DT_SYMBOLS if s != BENCH)]


def dt_check(inp: dict, trades: list[Trade], ranges: dict[str, float]) -> dict:
    """Every hourly run: close open dual_thrust trades at the stop or after GAP_CLOSE_AFTER, then open new ones
    whose price is DT_K x range above today's open (once per symbol per day, not after GAP_CLOSE_AFTER).
    inp: {"now", "opens": {"SYM": today's open}, "quotes": {"QQQ": ..., "SYM": last price}}"""
    ny = _now(inp).astimezone(NY)
    today = ny.date().isoformat()
    quotes = {k.upper(): float(v) for k, v in (inp.get("quotes") or {}).items()}
    opens = {k.upper(): float(v) for k, v in (inp.get("opens") or {}).items()}
    bench = quotes.get(BENCH)
    report = {"opened": [], "closed": [], "unpriced": []}
    if not bench:
        raise ValueError(f"no {BENCH} quote")
    late = ny.time() >= GAP_CLOSE_AFTER
    for t in trades:
        if t.source != "dual_thrust" or t.status != "open":
            continue
        px = quotes.get(t.symbol)
        if not px:
            report["unpriced"].append(t.symbol)
            continue
        why = "stop" if px <= t.stop else "end of day" if late or t.opened != today else ""
        if why:
            t.status, t.closed, t.exit, t.bench_exit, t.exit_reason, t.days = "closed", today, px, bench, why, 1
            report["closed"].append({"source": t.source, "symbol": t.symbol, "ret": round(t.ret, 4),
                                     "excess": round(t.excess, 4), "why": why})
    if late:
        return report
    done_today = {t.symbol for t in trades if t.source == "dual_thrust" and t.opened == today}
    for sym in DT_SYMBOLS:
        if sym in done_today or sym not in ranges or sym not in opens or sym not in quotes:
            continue
        o, rng, px = opens[sym], ranges[sym], quotes[sym]
        if px >= o + DT_K * rng:
            t = Trade("dual_thrust", sym, today, px, bench,
                      f"{px:.2f} >= open {o:.2f} + {DT_K} x 4-day range {rng:.2f}", stop=round(o - DT_K * rng, 4))
            trades.append(t)
            report["opened"].append({"source": t.source, "symbol": sym, "price": px, "reason": t.reason})
    return report


def scoreboard(trades: list[Trade]) -> dict:
    out = {}
    for source in PICKS:
        closed = [t for t in trades if t.source == source and t.status == "closed"]
        n = len(closed)
        out[source] = {"open": sum(t.source == source and t.status == "open" for t in trades), "closed": n,
                       "win_rate": round(sum(t.ret > 0 for t in closed) / n, 3) if n else None,
                       "avg_return": round(sum(t.ret for t in closed) / n, 4) if n else None,
                       "avg_vs_qqq": round(sum(t.excess for t in closed) / n, 4) if n else None,
                       # a source earns a real-money proposal only with a record
                       "ready_for_real_money": bool(n >= 20 and sum(t.excess for t in closed) / n > 0
                                                    and sum(t.ret > 0 for t in closed) / n >= 0.5)}
    return out
