"""When to run cycles, and the crash-safe polling loop.

  * stocks: once per completed daily bar, i.e. once per New York trading day, between 09:35 and
    15:55 America/New_York on weekdays. Holidays and early closes are decided by the broker clock
    (``broker.is_market_open(STOCK)``) at run time, not by a local calendar.
  * crypto: once per UTC day, after 00:05 UTC (the previous UTC daily bar is complete by then).

Last-run markers are stored in store flags (``scheduler_last_stocks`` = NY date,
``scheduler_last_crypto`` = UTC date), so a restart on the same day does not run again. Each cycle
is idempotent anyway (deterministic client_order_ids), so a crash between the cycle and the marker
write is harmless.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time as dtime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import AppConfig
from ..logging_setup import log_event
from ..models import AssetClass
from .runner import CycleReport, run_cycle

log = logging.getLogger("quantbot.scheduler")

NY = ZoneInfo("America/New_York")
STOCK_WINDOW = (dtime(9, 35), dtime(15, 55))
CRYPTO_AFTER_UTC = dtime(0, 5)
ACTIONS = ("crypto", "stocks")
MAX_BACKOFF_SECONDS = 900


def marker_flag(action: str) -> str:
    return f"scheduler_last_{action}"


def period_key(action: str, now: datetime) -> str:
    """The trading period an action belongs to: NY date for stocks, UTC date for crypto."""
    if action == "stocks":
        return now.astimezone(NY).date().isoformat()
    return now.astimezone(UTC).date().isoformat()


def due_actions(now: datetime, last_runs: dict) -> list[str]:
    """Actions due at ``now`` given ``{"stocks": "YYYY-MM-DD" | None, "crypto": ...}``.

    Stocks being "due" only means the local time window allows it; the caller must still ask the
    broker clock whether the market is open (holidays, early closes)."""
    if now.tzinfo is None:
        raise ValueError("due_actions needs a tz-aware 'now'")
    due: list[str] = []
    utc = now.astimezone(UTC)
    if utc.time() >= CRYPTO_AFTER_UTC and last_runs.get("crypto") != period_key("crypto", now):
        due.append("crypto")
    ny = now.astimezone(NY)
    if (ny.weekday() < 5 and STOCK_WINDOW[0] <= ny.time() <= STOCK_WINDOW[1]
            and last_runs.get("stocks") != period_key("stocks", now)):
        due.append("stocks")
    return due


@dataclass
class RunContext:
    """What ``build_fn`` returns for ``run_loop``."""
    broker: Any
    store: Any
    source: Any
    strategies: list
    risk: Any
    alerter: Any = None
    on_before_cycle: Callable[[str, datetime], None] | None = None   # e.g. sim mode sets prices


_ACTION_CLASS = {"stocks": AssetClass.STOCK, "crypto": AssetClass.CRYPTO}


def run_once_due(cfg: AppConfig, ctx: RunContext, now: datetime) -> dict[str, CycleReport | str]:
    """Run every due action once. Returns {action: report | reason it did not run}."""
    store = ctx.store
    last = {a: store.get_flag(marker_flag(a)) for a in ACTIONS}
    has_class = {s.asset_class for s in cfg.enabled_symbols()}
    out: dict[str, CycleReport | str] = {}
    for action in due_actions(now, last):
        ac = _ACTION_CLASS[action]
        if ac not in has_class:
            continue
        if ac is AssetClass.STOCK and not ctx.broker.is_market_open(AssetClass.STOCK):
            out[action] = "market closed per broker clock"
            continue
        if ctx.on_before_cycle is not None:
            ctx.on_before_cycle(action, now)
        rep = run_cycle(cfg, ctx.broker, store, ctx.source, ctx.strategies, ctx.risk, now,
                        alerter=ctx.alerter, asset_classes={ac})
        out[action] = rep
        if not rep.aborted:
            store.set_flag(marker_flag(action), period_key(action, now), f"cycle ran at {now.isoformat()}")
    return out


def run_loop(cfg: AppConfig, build_fn: Callable[[], RunContext], poll_seconds: float,
             max_iterations: int | None = None, sleep: Callable[[float], None] = time.sleep,
             now_fn: Callable[[], datetime] | None = None) -> str:
    """Poll forever (or ``max_iterations`` times). Returns why it stopped: "halted" or "max_iterations".

    Stops immediately when the kill switch is engaged (heartbeat "halted"). Exceptions are caught,
    recorded and followed by exponential back-off (capped at 15 minutes)."""
    now_fn = now_fn or (lambda: datetime.now(UTC))
    ctx: RunContext | None = None
    failures = 0
    i = 0
    while max_iterations is None or i < max_iterations:
        i += 1
        try:
            if ctx is None:
                ctx = build_fn()
            if ctx.risk.killswitch.is_engaged():
                st = ctx.risk.killswitch.status()
                ctx.store.heartbeat("scheduler", "halted", f"kill switch: {st.get('reason', '')}"[:500])
                ctx.store.record_event("critical", "scheduler_halted",
                                       f"kill switch engaged; scheduler loop stopped ({st.get('reason', '')})")
                log_event(log, logging.CRITICAL, "scheduler_halted", reason=st.get("reason", ""))
                return "halted"
            ctx.store.heartbeat("scheduler", "running")
            results = run_once_due(cfg, ctx, now_fn())
            failed = [a for a, r in results.items() if isinstance(r, CycleReport) and r.aborted]
            if failed:
                raise RuntimeError(f"cycle aborted for {failed}")
            ctx.store.heartbeat("scheduler", "ok", ",".join(f"{a}:{'ran' if isinstance(r, CycleReport) else r}"
                                                            for a, r in results.items()))
            failures = 0
            delay = poll_seconds
        except Exception as e:
            failures += 1
            delay = min(poll_seconds * (2 ** failures), MAX_BACKOFF_SECONDS)
            log_event(log, logging.ERROR, "scheduler_error", error=f"{type(e).__name__}: {e}", backoff=delay)
            if ctx is not None:
                try:
                    ctx.store.heartbeat("scheduler", "error", f"{type(e).__name__}: {e}"[:500])
                    ctx.store.record_event("critical", "scheduler_error", f"{type(e).__name__}: {e}",
                                           backoff_seconds=delay, consecutive_failures=failures)
                except Exception:  # pragma: no cover - store itself broken
                    pass
        if max_iterations is not None and i >= max_iterations:
            break
        sleep(delay)
    return "max_iterations"


__all__ = ["due_actions", "run_loop", "run_once_due", "RunContext", "marker_flag", "period_key"]
