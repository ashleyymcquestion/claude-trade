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

Input file for `run` (written by the session):
    {"now": "...",                                  # optional
     "gainers": [{"symbol", "price", "pct"}],       # from preview_scan
     "buzz": [{"symbol", "price", "rel_volume"}],   # from preview_scan
     "politicians": [<raw get_politician_trades entries>],
     "social": [{"symbol", "why", "url"}],
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

PICKS = {"gainers": 5, "buzz": 3, "politicians": 5, "social": 3}           # new paper trades per day, max
MAX_HOLD = {"gainers": 5, "buzz": 5, "politicians": 20, "social": 5}      # trading days
STOP, TARGET = -0.08, 0.15
POLITICIAN_FRESH_DAYS = 14


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
                            for k, v in row.items()}))
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
    return out


def needs(inp: dict, trades: list[Trade], state: dict) -> list[str]:
    """Symbols the session must quote before `run`: the benchmark, open trades, and new ideas
    that don't already carry a price."""
    priced = {g["symbol"].upper() for g in (inp.get("gainers") or [])} | {b["symbol"].upper() for b in (inp.get("buzz") or [])}
    syms = {BENCH} | {t.symbol for t in trades if t.status == "open"}
    syms |= {s for ideas in candidates(inp, trades, state).values() for s, _ in ideas if s not in priced}
    return sorted(syms)


def run(inp: dict, trades: list[Trade], state: dict) -> tuple[list[Trade], dict, dict]:
    now = _now(inp)
    today = now.astimezone(NY).date().isoformat()
    quotes = {k.upper(): float(v) for k, v in (inp.get("quotes") or {}).items()}
    for g in (inp.get("gainers") or []) + (inp.get("buzz") or []):
        quotes.setdefault(g["symbol"].upper(), float(g["price"]))
    bench = quotes.get(BENCH)
    if not bench:
        raise ValueError(f"no {BENCH} quote")
    report = {"opened": [], "closed": [], "unpriced": []}

    # exits first (one check per trading day)
    for t in trades:
        if t.status != "open" or t.opened == today:
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
    for source, picks in ideas.items():
        for sym, reason in picks:
            px = quotes.get(sym)
            if not px:
                report["unpriced"].append(sym)
                continue
            trades.append(Trade(source, sym, today, px, bench, reason))
            report["opened"].append({"source": source, "symbol": sym, "price": px, "reason": reason})

    seen = set(state.get("seen_politician_trades") or [])
    seen |= {_pol_key(t) for t in inp.get("politicians") or []}
    state = {**state, "last_run": today, "seen_politician_trades": sorted(seen)}
    return trades, state, report


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
