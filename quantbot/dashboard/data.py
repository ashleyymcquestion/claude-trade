"""Every number the dashboard shows, computed from a ``Store`` (and the latest backtest report).

Pure functions, no Streamlit import, so they are unit-testable. Tables are returned as pandas
DataFrames with fixed columns (empty frames when there is no data); scalars are ``None`` when they
cannot be computed yet (e.g. a brand-new database), never a made-up value.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from ..risk.killswitch import KillSwitch
from ..risk.limits import FLAG_DAILY, FLAG_DRAWDOWN, FLAG_DRAWDOWN_RESET, FLAG_WEEKLY, NY, iso_week_label, ny_day_bounds

if TYPE_CHECKING:  # pragma: no cover
    from ..store import Store

RUNNER_COMPONENTS = ("runner",)          # heartbeat component names treated as "the runner"
MIN_STALE_AFTER = timedelta(minutes=15)
ERROR_WORDS = ("error", "fail", "crash", "exception")   # a heartbeat status containing one => "error"


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now


# ---------------------------------------------------------------- account / P&L
def account_summary(store: "Store") -> dict[str, Any]:
    """Latest equity snapshot: ``equity, cash, gross, ts`` (all None when there is none)."""
    curve = store.equity_curve()
    if curve.empty:
        return {"equity": None, "cash": None, "gross": None, "ts": None}
    last = curve.iloc[-1]
    return {"equity": float(last["equity"]), "cash": float(last["cash"]), "gross": float(last["gross"]),
            "ts": last["ts"].to_pydatetime()}


def unrealized_pnl(store: "Store") -> float | None:
    positions = store.latest_equity_positions()
    if not positions and store.equity_curve().empty:
        return None
    total = 0.0
    for p in positions:
        u = p.get("unrealized_pnl")
        if u is None:
            u = float(p.get("qty", 0.0)) * (float(p.get("market_price", 0.0)) - float(p.get("avg_entry_price", 0.0)))
        total += float(u)
    return total


def pnl_summary(store: "Store", now: datetime, initial_capital: float) -> dict[str, Any]:
    """``daily_pnl`` = equity now - equity at 00:00 New York today (last snapshot at/before it, else
    the first snapshot of today); ``total_pnl`` = equity - initial_capital; ``realized_total`` = sum of
    recorded realized P&L; ``unrealized`` = sum over the latest snapshot's positions."""
    now = _aware(now)
    acct = account_summary(store)
    equity = acct["equity"]
    day_start, day_end = ny_day_bounds(now)
    sod = store.equity_at_or_before(day_start)
    if sod is None:
        sod = store.first_equity_between(day_start, day_end)
    realized = sum(store.realized_pnl_by_strategy().values())
    return {
        "equity": equity,
        "start_of_day_equity": sod,
        "daily_pnl": (equity - sod) if equity is not None and sod is not None else None,
        "total_pnl": (equity - float(initial_capital)) if equity is not None else None,
        "total_return": (equity / float(initial_capital) - 1.0) if equity is not None and initial_capital else None,
        "realized_total": float(realized),
        "realized_today": store.realized_pnl_between(day_start, day_end),
        "unrealized": unrealized_pnl(store),
    }


def drawdown_series(store: "Store") -> pd.DataFrame:
    """``ts, equity, peak, drawdown`` (drawdown as a positive fraction below the running peak)."""
    curve = store.equity_curve()
    out = pd.DataFrame({"ts": curve["ts"], "equity": curve["equity"]})
    out["peak"] = out["equity"].cummax()
    out["drawdown"] = (1.0 - out["equity"] / out["peak"]).where(out["peak"] > 0, 0.0).clip(lower=0.0)
    return out


def max_drawdown(store: "Store") -> float | None:
    """Largest peak-to-trough decline of the recorded equity curve, as a positive fraction.
    None when there is no equity data."""
    dd = drawdown_series(store)
    if dd.empty:
        return None
    v = float(dd["drawdown"].max())
    return v if math.isfinite(v) else None


def current_drawdown(store: "Store") -> float | None:
    dd = drawdown_series(store)
    return None if dd.empty else float(dd["drawdown"].iloc[-1])


# ---------------------------------------------------------------- tables
POSITION_COLUMNS = ["symbol", "qty", "avg_entry_price", "market_price", "market_value", "unrealized_pnl",
                    "unrealized_pct", "strategy", "stop_price", "entry_ts", "highest_close"]


def positions_table(store: "Store") -> pd.DataFrame:
    """Positions of the latest equity snapshot joined with position metadata (strategy, stop).
    Metadata-only symbols (meta present, no position in the snapshot) are listed with qty 0 so an
    orphaned stop is visible rather than hidden."""
    meta = store.all_position_meta()
    rows = []
    seen = set()
    for p in store.latest_equity_positions():
        sym = p.get("symbol", "")
        seen.add(sym)
        m = meta.get(sym, {})
        qty = float(p.get("qty", 0.0))
        entry = float(p.get("avg_entry_price", 0.0))
        price = float(p.get("market_price", 0.0))
        mv = p.get("market_value")
        upnl = p.get("unrealized_pnl")
        rows.append({
            "symbol": sym, "qty": qty, "avg_entry_price": entry, "market_price": price,
            "market_value": float(mv) if mv is not None else qty * price,
            "unrealized_pnl": float(upnl) if upnl is not None else qty * (price - entry),
            "unrealized_pct": (price / entry - 1.0) if entry > 0 else None,
            "strategy": m.get("strategy") or p.get("strategy") or "",
            "stop_price": m.get("stop_price") if m.get("stop_price") is not None else p.get("stop_price"),
            "entry_ts": m.get("entry_ts"), "highest_close": m.get("highest_close"),
        })
    for sym, m in meta.items():
        if sym in seen:
            continue
        rows.append({"symbol": sym, "qty": 0.0, "avg_entry_price": None, "market_price": None,
                     "market_value": 0.0, "unrealized_pnl": 0.0, "unrealized_pct": None,
                     "strategy": m.get("strategy", ""), "stop_price": m.get("stop_price"),
                     "entry_ts": m.get("entry_ts"), "highest_close": m.get("highest_close")})
    return pd.DataFrame(rows, columns=POSITION_COLUMNS)


ORDER_COLUMNS = ["client_order_id", "symbol", "side", "qty", "status", "filled_qty", "filled_avg_price",
                 "strategy", "submitted_at", "updated_at", "broker_order_id", "reject_reason"]


def _order_row(o) -> dict:
    return {"client_order_id": o.client_order_id, "symbol": o.symbol, "side": str(o.side),
            "qty": float(o.qty), "status": str(o.status), "filled_qty": float(o.filled_qty),
            "filled_avg_price": o.filled_avg_price, "strategy": o.strategy, "submitted_at": o.submitted_at,
            "updated_at": o.updated_at, "broker_order_id": o.broker_order_id, "reject_reason": o.reject_reason}


def open_orders_table(store: "Store") -> pd.DataFrame:
    return pd.DataFrame([_order_row(o) for o in store.open_orders()], columns=ORDER_COLUMNS)


def recent_orders_table(store: "Store", limit: int = 100) -> pd.DataFrame:
    return pd.DataFrame([_order_row(o) for o in store.orders(limit=limit)], columns=ORDER_COLUMNS)


FILL_COLUMNS = ["ts", "symbol", "side", "qty", "price", "notional", "fee", "strategy", "client_order_id"]
REALIZED_COLUMNS = ["ts", "symbol", "strategy", "pnl"]


def trade_history(store: "Store", limit: int = 500) -> dict[str, pd.DataFrame]:
    """``{"fills": ..., "realized": ...}``, both newest first. Fills get their strategy from the order."""
    fills = store.fills()[::-1][:limit]
    strat_cache: dict[str, str] = {}
    rows = []
    for f in fills:
        if f.client_order_id not in strat_cache:
            o = store.get_order(f.client_order_id)
            strat_cache[f.client_order_id] = o.strategy if o is not None else ""
        rows.append({"ts": f.ts, "symbol": f.symbol, "side": str(f.side), "qty": float(f.qty),
                     "price": float(f.price), "notional": float(f.qty) * float(f.price), "fee": float(f.fee),
                     "strategy": strat_cache[f.client_order_id], "client_order_id": f.client_order_id})
    realized = store.realized_pnl_records(limit=limit)
    return {"fills": pd.DataFrame(rows, columns=FILL_COLUMNS),
            "realized": pd.DataFrame(realized, columns=REALIZED_COLUMNS)}


LIVE_PERF_COLUMNS = ["strategy", "realized_pnl", "closed_trades", "wins", "win_rate"]
RESEARCH_COLUMNS = ["scope", "strategy", "status", "trades", "total_return", "annualized_return", "sharpe",
                    "max_drawdown", "win_rate", "profit_factor"]


def strategy_performance(store: "Store", reports_dir: str | Path = "reports") -> dict[str, Any]:
    """``live``: realized P&L per strategy from the paper account (store).
    ``research``: rows from ``<reports_dir>/latest_backtest.json`` if present — backtest results,
    NOT live performance. ``research_meta`` holds generated_at/warning/path; ``research_error`` any
    problem reading the file."""
    recs = store.realized_pnl_records(limit=1_000_000)
    by: dict[str, dict] = {}
    for r in recs:
        d = by.setdefault(r["strategy"] or "(none)", {"realized_pnl": 0.0, "closed_trades": 0, "wins": 0})
        d["realized_pnl"] += float(r["pnl"])
        d["closed_trades"] += 1
        d["wins"] += 1 if float(r["pnl"]) > 0 else 0
    live = pd.DataFrame(
        [{"strategy": k, **v, "win_rate": v["wins"] / v["closed_trades"] if v["closed_trades"] else None}
         for k, v in sorted(by.items())],
        columns=LIVE_PERF_COLUMNS)

    research = pd.DataFrame(columns=RESEARCH_COLUMNS)
    meta: dict[str, Any] | None = None
    error: str | None = None
    path = Path(reports_dir) / "latest_backtest.json"
    if path.exists():
        try:
            payload = json.loads(path.read_text())
            meta = {"path": str(path), "generated_at": payload.get("generated_at"),
                    "warning": payload.get("warning"), "kind": payload.get("kind")}
            rows = []

            def _row(scope: str, name: str, entry: dict) -> dict:
                m = entry.get("metrics") or {}
                return {"scope": scope, "strategy": name, "status": entry.get("status", ""),
                        "trades": m.get("num_trades"), "total_return": m.get("total_return"),
                        "annualized_return": m.get("annualized_return"), "sharpe": m.get("sharpe"),
                        "max_drawdown": m.get("max_drawdown"), "win_rate": m.get("win_rate"),
                        "profit_factor": m.get("profit_factor")}

            for name, entry in (payload.get("portfolio") or {}).items():
                if isinstance(entry, dict):
                    rows.append(_row("portfolio", name, entry))
            for sym, sentry in (payload.get("symbols") or {}).items():
                if not isinstance(sentry, dict):
                    continue
                for name, entry in (sentry.get("strategies") or {}).items():
                    if isinstance(entry, dict):
                        rows.append(_row(sym, name, entry))
                bh = sentry.get("buy_and_hold")
                if isinstance(bh, dict):
                    rows.append(_row(sym, "buy_and_hold", {"status": "ok", "metrics": bh}))
            research = pd.DataFrame(rows, columns=RESEARCH_COLUMNS)
        except (OSError, ValueError, TypeError, AttributeError) as e:
            error = f"could not read {path}: {type(e).__name__}: {e}"
    return {"live": live, "research": research, "research_meta": meta, "research_error": error}


REGIME_COLUMNS = ["symbol", "ts", "regime"]


def regimes_table(store: "Store") -> pd.DataFrame:
    return pd.DataFrame([{"symbol": s, "ts": ts, "regime": r} for s, (ts, r) in store.latest_regimes().items()],
                        columns=REGIME_COLUMNS)


EVENT_COLUMNS = ["ts", "level", "kind", "message", "fields"]


def events_table(store: "Store", limit: int = 100) -> pd.DataFrame:
    rows = [{"ts": e["ts"], "level": e["level"], "kind": e["kind"], "message": e["message"],
             "fields": json.dumps(e["fields"], default=str) if e["fields"] else ""}
            for e in store.events(limit=limit)]
    return pd.DataFrame(rows, columns=EVENT_COLUMNS)


# ---------------------------------------------------------------- risk state / health
def breaker_status(store: "Store", now: datetime | None = None, kill_file: str | Path | None = None) -> dict[str, Any]:
    """Kill switch status plus each breaker flag. A daily/weekly flag from an earlier NY day / ISO
    week is still reported as set (the runner clears it on its next evaluation) but marked
    ``expired`` so the operator knows it no longer blocks once the runner cycles."""
    now = _aware(now or datetime.now(UTC))
    ks = KillSwitch(store, kill_file=kill_file).status()
    today = now.astimezone(NY).date()
    today_s, week_s = today.isoformat(), iso_week_label(today)
    out: dict[str, Any] = {"kill_switch": ks, "breakers": {}}
    for name, current in ((FLAG_DAILY, today_s), (FLAG_WEEKLY, week_s), (FLAG_DRAWDOWN, None)):
        info = store.get_flag_info(name)
        if info is None:
            out["breakers"][name] = {"active": False, "value": None, "reason": "", "updated_at": None,
                                     "expired": False}
            continue
        expired = current is not None and info["value"] < current
        out["breakers"][name] = {"active": not expired, "value": info["value"], "reason": info["reason"],
                                 "updated_at": info["updated_at"], "expired": expired}
    out["drawdown_reset_at"] = store.get_flag(FLAG_DRAWDOWN_RESET)
    out["any_breaker_active"] = any(b["active"] for b in out["breakers"].values())
    out["entries_allowed"] = not ks["engaged"] and not out["any_breaker_active"]
    return out


HEALTH_COLUMNS = ["component", "status", "detail", "ts", "age_seconds", "stale"]


def health(store: "Store", now: datetime, poll_seconds: int = 60) -> dict[str, Any]:
    """Heartbeats with their age. A component is ``stale`` when its heartbeat is older than
    ``max(2 * poll_seconds, 15 minutes)``. Overall ``runner`` state is ``"ok"``, ``"stale"``,
    ``"error"`` (last status mentions error/fail/crash/exception) or ``"missing"`` (never reported)."""
    now = _aware(now)
    stale_after = max(timedelta(seconds=2 * poll_seconds), MIN_STALE_AFTER)
    rows = []
    for hb in store.heartbeats():
        age = (now - hb["ts"]).total_seconds() if hb["ts"] is not None else None
        rows.append({"component": hb["component"], "status": hb["status"], "detail": hb["detail"],
                     "ts": hb["ts"], "age_seconds": age,
                     "stale": age is None or age > stale_after.total_seconds()})
    table = pd.DataFrame(rows, columns=HEALTH_COLUMNS)
    runner_rows = [r for r in rows if r["component"] in RUNNER_COMPONENTS
                   or r["component"].startswith(RUNNER_COMPONENTS)]
    if not runner_rows:
        runner = "missing"
        runner_row = None
    else:
        runner_row = max(runner_rows, key=lambda r: r["ts"] or datetime.min.replace(tzinfo=UTC))
        if runner_row["stale"]:
            runner = "stale"
        elif any(w in str(runner_row["status"]).lower() for w in ERROR_WORDS):
            runner = "error"
        else:
            runner = "ok"
    return {"table": table, "runner": runner, "runner_heartbeat": runner_row,
            "stale_after_seconds": stale_after.total_seconds(), "any_stale": bool(table["stale"].any())
            if not table.empty else False}
