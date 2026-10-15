"""One trading cycle: reconcile -> account/breakers -> per symbol data checks -> exits -> entries.

Safety properties (each covered by tests/quant/test_runner.py):
  * Every order has a deterministic ``client_order_id`` (strategy, symbol, signal bar date, side).
    Before submitting, the store AND the broker are checked for that id, so a re-run of the same
    bar (or a restart) never submits twice.
  * The order is written to the store as PENDING *before* the broker call; if the process dies
    mid-submit, the next cycle's reconciliation finds it by client_order_id.
  * Never trade on stale or invalid data: such a symbol is skipped and an alert is raised.
  * Kill switch engaged -> no orders of any kind. Breakers block entries; exits stay allowed.
  * A per-symbol exception is recorded and alerted and does not stop other symbols; a broker
    communication failure (``BrokerError`` other than ``OrderRejected``) aborts the cycle without
    assuming any state change.

Fills are derived from changes in each order's cumulative ``filled_qty`` (the Alpaca trading API
offers no per-execution fill feed in alpaca-py 0.44.0), with fee estimated from ``CostConfig``
(stocks: commission bps; crypto: fee bps). A fill's timestamp is the broker's ``updated_at`` for
that order state, so re-processing the same state is deduplicated by the store.

Realized P&L on a SELL fill = qty * sell price - sell fee - qty * average BUY cost (incl. fees)
of the BUY fills since the position's ``entry_ts`` (position meta has no avg-price column).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from ..broker.base import Broker, BrokerError, OrderRejected
from ..config import AppConfig, SymbolConfig
from ..data.validate import completed_bars, validate_bars
from ..logging_setup import log_event
from ..models import (
    AccountSnapshot,
    AssetClass,
    Fill,
    Order,
    OrderRequest,
    OrderStatus,
    Side,
    Signal,
)

log = logging.getLogger("quantbot.runner")

MAX_CID_LEN = 48
_CID_BAD = re.compile(r"[^A-Za-z0-9-]")
_EPS = 1e-9
ENTRY_PLAN_PREFIX = "entry_plan:"
BAR_PERIOD = pd.Timedelta(days=1)


@dataclass
class CycleReport:
    ts: datetime
    symbols_checked: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    signals: list[Signal] = field(default_factory=list)
    orders_submitted: list[str] = field(default_factory=list)   # client_order_ids
    orders_rejected: list[str] = field(default_factory=list)
    exits: list[str] = field(default_factory=list)              # symbols
    entries: list[str] = field(default_factory=list)
    reconciled: int = 0
    cancelled: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    aborted: bool = False
    halted: bool = False

    def summary(self) -> dict:
        return {"ts": self.ts.isoformat(), "checked": len(self.symbols_checked), "skipped": self.skipped,
                "signals": [f"{s.strategy}:{s.symbol}:{s.action}" for s in self.signals],
                "submitted": self.orders_submitted, "rejected": self.orders_rejected,
                "exits": self.exits, "entries": self.entries, "reconciled": self.reconciled,
                "cancelled": self.cancelled, "errors": self.errors, "aborted": self.aborted,
                "halted": self.halted}


def make_client_order_id(strategy: str, symbol: str, bar_ts: datetime, side: Side | str) -> str:
    """Deterministic id: letters, digits and '-' only, at most 48 characters."""
    side_s = Side(side).value
    date_s = pd.Timestamp(bar_ts).tz_convert("UTC").strftime("%Y%m%d") if pd.Timestamp(bar_ts).tzinfo \
        else pd.Timestamp(bar_ts).strftime("%Y%m%d")
    strat = _CID_BAD.sub("", strategy)
    sym = _CID_BAD.sub("", symbol)
    cid = f"{strat}-{sym}-{date_s}-{side_s}"
    if len(cid) <= MAX_CID_LEN:
        return cid
    digest = hashlib.sha1(f"{strategy}|{symbol}".encode()).hexdigest()[:8]
    room = MAX_CID_LEN - len(f"-{date_s}-{side_s}-{digest}")
    head = f"{strat}-{sym}"[:room].rstrip("-")
    return f"{head}-{digest}-{date_s}-{side_s}"


def _finite(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _age_seconds(order: Order, now: datetime) -> float:
    ref = order.submitted_at or order.updated_at
    if ref is None:
        return 0.0
    return (now - ref).total_seconds()


class _Cycle:
    def __init__(self, cfg: AppConfig, broker: Broker, store, source, strategies, risk, now: datetime,
                 alerter=None, asset_classes: set[AssetClass] | None = None) -> None:
        if now.tzinfo is None:
            raise ValueError("run_cycle needs a tz-aware 'now'")
        self.cfg, self.broker, self.store, self.source = cfg, broker, store, source
        self.strategies = list(strategies)
        self.by_name = {s.name: s for s in self.strategies}
        self.risk, self.now, self.alerter = risk, now.astimezone(UTC), alerter
        self.asset_classes = asset_classes
        self.report = CycleReport(ts=self.now)
        self.snapshot: AccountSnapshot | None = None
        self._market_open: dict[AssetClass, bool] = {}

    # ---------------------------------------------------------------- utilities
    def alert(self, level: str, kind: str, message: str, **fields: Any) -> None:
        log_event(log, {"info": logging.INFO, "warning": logging.WARNING}.get(level, logging.ERROR),
                  message, kind=kind, **fields)
        if self.alerter is not None:
            try:
                self.alerter.send(level, kind, message, **fields)
                return
            except Exception as e:   # an alerting failure must not break trading logic
                self.store.record_event("warning", "alert_failed", f"alerter failed: {type(e).__name__}: {e}")
        self.store.record_event(level, kind, message, **fields)

    def _asset_class(self, symbol: str) -> AssetClass:
        try:
            return self.cfg.symbol(symbol).asset_class
        except KeyError:
            return AssetClass.CRYPTO if "/" in symbol else AssetClass.STOCK

    def _fee_bps(self, symbol: str) -> float:
        c = self.cfg.costs
        return c.crypto_fee_bps if self._asset_class(symbol) is AssetClass.CRYPTO else c.stock_commission_bps

    def _held_qty(self, symbol: str) -> float:
        assert self.snapshot is not None
        return sum(p.qty for p in self.snapshot.positions if p.symbol == symbol)

    def _refresh_snapshot(self) -> None:
        self.snapshot = self.broker.get_account()

    # ---------------------------------------------------------------- order/fill bookkeeping
    def apply_update(self, prev: Order, cur: Order) -> Fill | None:
        """Record the fill implied by prev -> cur (filled_qty delta) and its side effects."""
        cur.strategy = cur.strategy or prev.strategy
        delta = cur.filled_qty - prev.filled_qty
        fill = None
        if delta > _EPS and cur.filled_avg_price:
            if prev.filled_qty > _EPS and prev.filled_avg_price:
                price = (cur.filled_qty * cur.filled_avg_price - prev.filled_qty * prev.filled_avg_price) / delta
            else:
                price = cur.filled_avg_price
            fee = delta * price * self._fee_bps(cur.symbol) / 10_000
            ts = cur.updated_at or self.now
            fill = Fill(cur.client_order_id, cur.symbol, Side(cur.side), delta, price, fee, ts)
            if self.store.record_fill(fill):
                self._on_new_fill(fill, cur)
            else:
                fill = None
        self.store.record_order(cur)
        if not cur.status.is_open:
            plan_flag = ENTRY_PLAN_PREFIX + cur.client_order_id
            if self.store.get_flag(plan_flag) is not None:
                self.store.set_flag(plan_flag, None, f"order {cur.status.value}")
        return fill

    def _on_new_fill(self, fill: Fill, order: Order) -> None:
        log_event(log, logging.INFO, "fill", client_order_id=fill.client_order_id, symbol=fill.symbol,
                  side=fill.side.value, qty=fill.qty, price=fill.price, fee=fill.fee)
        meta = self.store.get_position_meta(fill.symbol)
        if fill.side is Side.BUY:
            if meta is None:
                plan_raw = self.store.get_flag(ENTRY_PLAN_PREFIX + fill.client_order_id)
                plan = json.loads(plan_raw) if plan_raw else {}
                sd = _finite(plan.get("stop_distance"))
                stop = fill.price - sd if sd is not None else _finite(plan.get("stop_price"))
                highest = max(fill.price, _finite(plan.get("signal_close")) or fill.price)
                self.store.set_position_meta(fill.symbol, order.strategy, stop, fill.ts, highest)
                self.store.record_event("info", "position_opened",
                                        f"{fill.symbol} entry fill {fill.qty} @ {fill.price:.4f}; stop {stop}",
                                        symbol=fill.symbol, strategy=order.strategy, stop=stop)
            return
        # SELL: realized P&L against the average buy cost since entry
        strategy = (meta or {}).get("strategy") or order.strategy
        avg_cost = None
        if meta is not None and meta.get("entry_ts") is not None:
            buys = [f for f in self.store.fills(since=meta["entry_ts"])
                    if f.symbol == fill.symbol and f.side is Side.BUY]
            q = sum(f.qty for f in buys)
            if q > _EPS:
                avg_cost = sum(f.qty * f.price + f.fee for f in buys) / q
        if avg_cost is None:
            pos = [p for p in (self.snapshot.positions if self.snapshot else ()) if p.symbol == fill.symbol]
            if not pos:
                try:
                    pos = [p for p in self.broker.get_account().positions if p.symbol == fill.symbol]
                except BrokerError:
                    pos = []
            avg_cost = pos[0].avg_entry_price if pos else None
        if avg_cost is None:
            self.alert("warning", "pnl_unknown", f"cannot compute realized P&L for {fill.symbol}: no entry cost",
                       symbol=fill.symbol, client_order_id=fill.client_order_id)
            return
        pnl = fill.qty * fill.price - fill.fee - fill.qty * avg_cost
        self.store.record_realized_pnl(fill.ts, fill.symbol, strategy, pnl)
        self.store.record_event("info", "realized_pnl", f"{fill.symbol} realized {pnl:.2f}",
                                symbol=fill.symbol, strategy=strategy, pnl=pnl)

    def clear_closed_meta(self) -> None:
        """Clear position meta for symbols the broker no longer holds and that have no open order."""
        assert self.snapshot is not None
        held = {p.symbol for p in self.snapshot.positions if p.qty > _EPS}
        open_syms = {o.symbol for o in self.store.open_orders()}
        for sym in self.store.all_position_meta():
            if sym not in held and sym not in open_syms:
                self.store.clear_position_meta(sym)
                self.store.record_event("info", "position_closed", f"{sym} flat; position meta cleared", symbol=sym)

    # ---------------------------------------------------------------- reconcile
    def reconcile(self) -> None:
        timeout = self.cfg.execution.order_timeout_seconds
        for o in self.store.open_orders():
            cur = self.broker.get_order(o.client_order_id)
            age = _age_seconds(o, self.now)
            if cur is None:
                if age > timeout:
                    lost = replace(o, status=OrderStatus.EXPIRED, updated_at=self.now,
                                   reject_reason="not found at broker by client_order_id after timeout")
                    self.store.record_order(lost)
                    self.alert("warning", "order_lost",
                               f"{o.client_order_id} never reached the broker; marked expired (not resubmitted)",
                               client_order_id=o.client_order_id, symbol=o.symbol)
                continue
            self.apply_update(o, cur)
            self.report.reconciled += 1
            if cur.status.is_open and age > timeout:
                self.broker.cancel_order(o.client_order_id)
                after = self.broker.get_order(o.client_order_id) or replace(cur, status=OrderStatus.CANCELED)
                self.apply_update(cur, after)
                self.report.cancelled.append(o.client_order_id)
                self.alert("warning", "order_timeout",
                           f"{o.client_order_id} still {cur.status.value} after {age:.0f}s; cancelled",
                           client_order_id=o.client_order_id, symbol=o.symbol, age_seconds=age,
                           filled_qty=after.filled_qty)

    # ---------------------------------------------------------------- submit
    def submit(self, sym: SymbolConfig, side: Side, qty: float, strategy: str, bar_ts: datetime,
               reason: str, plan: dict | None = None) -> Order | None:
        cid = make_client_order_id(strategy, sym.symbol, bar_ts, side)
        if self.store.get_order(cid) is not None or self.broker.get_order(cid) is not None:
            self.report.skipped[sym.symbol] = f"order {cid} already exists for this bar"
            self.store.record_event("info", "duplicate_prevented", f"{cid} already exists; not resubmitted",
                                    client_order_id=cid, symbol=sym.symbol)
            return None
        pending = Order(cid, sym.symbol, side, qty, OrderStatus.PENDING, submitted_at=self.now,
                        updated_at=self.now, strategy=strategy)
        if plan:
            self.store.set_flag(ENTRY_PLAN_PREFIX + cid, json.dumps(plan), reason)
        self.store.record_order(pending)
        req = OrderRequest(cid, sym.symbol, side, qty, strategy=strategy, reason=reason)
        try:
            result = self.broker.submit_order(req)
        except OrderRejected as e:
            rejected = replace(pending, status=OrderStatus.REJECTED, reject_reason=str(e), updated_at=self.now)
            self.apply_update(pending, rejected)
            self.report.orders_rejected.append(cid)
            self.alert("warning", "order_rejected", f"{cid} rejected: {e}", client_order_id=cid,
                       symbol=sym.symbol, side=side.value, qty=qty)
            return rejected
        except BrokerError as e:
            # Unknown outcome: leave it PENDING; reconciliation will look it up by client_order_id.
            self.alert("critical", "order_submit_unknown",
                       f"{cid} submit failed with unknown outcome; left PENDING for reconciliation: {e}",
                       client_order_id=cid, symbol=sym.symbol)
            raise
        result.strategy = result.strategy or strategy
        self.report.orders_submitted.append(cid)
        self.alert("info", "order_submitted", f"{side.value} {qty} {sym.symbol} ({strategy}): {reason}",
                   client_order_id=cid, symbol=sym.symbol, status=result.status.value)
        self.apply_update(pending, result)
        if result.status is OrderStatus.REJECTED:
            self.report.orders_rejected.append(cid)
        return result

    # ---------------------------------------------------------------- data
    def market_open(self, ac: AssetClass) -> bool:
        if ac not in self._market_open:
            self._market_open[ac] = bool(self.broker.is_market_open(ac))
        return self._market_open[ac]

    def load_bars(self, sym: SymbolConfig) -> pd.DataFrame | None:
        warm = max((s.warmup_bars for s in self.strategies), default=0) + 50
        days = math.ceil(warm * 1.6) + 10 if sym.asset_class is AssetClass.STOCK else warm + 10
        start = self.now - timedelta(days=days)
        try:
            raw = self.source.get_bars(sym, start, self.now)
        except Exception as e:
            self.report.skipped[sym.symbol] = f"data fetch failed: {type(e).__name__}"
            self.alert("warning", "data_error", f"{sym.symbol}: bar fetch failed: {type(e).__name__}: {e}",
                       symbol=sym.symbol)
            return None
        bars = completed_bars(raw, self.now)
        vr = validate_bars(bars, sym.asset_class, self.now, self.cfg.data)
        if not vr.ok:
            self.report.skipped[sym.symbol] = "invalid data: " + "; ".join(vr.issues)
            self.alert("warning", "data_invalid", f"{sym.symbol}: not trading on invalid/stale data: "
                       + "; ".join(vr.issues), symbol=sym.symbol, issues=vr.issues)
            return None
        try:
            from ..regime import classify
            regime = classify(bars).iloc[-1]
            self.store.record_regime(sym.symbol, bars.index[-1].to_pydatetime(), str(regime))
        except Exception as e:   # regime is informational only
            self.store.record_event("warning", "regime_error", f"{sym.symbol}: regime failed: {e}",
                                    symbol=sym.symbol)
        return bars

    # ---------------------------------------------------------------- exits
    def manage_position(self, sym: SymbolConfig, bars: pd.DataFrame) -> None:
        meta = self.store.get_position_meta(sym.symbol)
        if meta is None:
            self.report.skipped[sym.symbol] = "held without position meta (unmanaged)"
            self.alert("warning", "unmanaged_position",
                       f"{sym.symbol} is held but has no owning strategy; not managed automatically",
                       symbol=sym.symbol)
            return
        last_ts = bars.index[-1].to_pydatetime()
        last_close = float(bars["close"].iloc[-1])
        strat = self.by_name.get(meta["strategy"])
        row = strat.signals(bars).iloc[-1] if strat is not None else None

        highest = max(_finite(meta.get("highest_close")) or last_close, last_close)
        old_stop = _finite(meta.get("stop_price"))
        stop = old_stop
        trail = _finite(row["trail_distance"]) if row is not None else None
        if trail is not None and trail > 0:
            t_stop = highest - trail
            stop = t_stop if stop is None else max(stop, t_stop)   # ratchets up only
        self.store.set_position_meta(sym.symbol, meta["strategy"], stop, meta.get("entry_ts"), highest)

        reasons = []
        if row is not None and bool(row["exit"]):
            reasons.append("strategy exit signal")
        if stop is not None and last_close <= stop:
            reasons.append(f"stop hit (close {last_close:.4f} <= stop {stop:.4f})")
        max_hold = _finite(row["max_hold_bars"]) if row is not None else None
        entry_ts = meta.get("entry_ts")
        if max_hold is not None and entry_ts is not None:
            held_bars = int((bars.index + BAR_PERIOD > pd.Timestamp(entry_ts)).sum())
            if held_bars >= max_hold:
                reasons.append(f"max hold reached ({held_bars} >= {max_hold:.0f} bars)")
        if strat is None:
            self.store.record_event("warning", "strategy_missing",
                                    f"{sym.symbol} owned by disabled/unknown strategy {meta['strategy']}; "
                                    "only stops are managed", symbol=sym.symbol)
        if not reasons:
            return
        reason = "; ".join(reasons)
        self.report.signals.append(Signal(sym.symbol, meta["strategy"], last_ts, "exit", stop, reason))
        assert self.snapshot is not None
        decision = self.risk.approve_exit(self.snapshot, sym.symbol, self.now)
        if not decision.approved:
            self.report.skipped[sym.symbol] = f"exit blocked: {decision.reason}"
            self.alert("warning", "exit_blocked", f"{sym.symbol} exit ({reason}) blocked: {decision.reason}",
                       symbol=sym.symbol)
            return
        order = self.submit(sym, Side.SELL, decision.qty, meta["strategy"], last_ts, reason)
        if order is not None and order.status is not OrderStatus.REJECTED:
            self.report.exits.append(sym.symbol)
            self._refresh_snapshot()

    # ---------------------------------------------------------------- entries
    def consider_entry(self, sym: SymbolConfig, bars: pd.DataFrame) -> None:
        last_ts = bars.index[-1].to_pydatetime()
        last_close = float(bars["close"].iloc[-1])
        chosen, row = None, None
        for strat in self.strategies:
            r = strat.signals(bars).iloc[-1]
            if bool(r["entry"]):
                chosen, row = strat, r
                break
        if chosen is None:
            return
        sd = _finite(row["stop_distance"])
        if sd is None or sd <= 0:
            self.report.skipped[sym.symbol] = f"{chosen.name} entry without a valid stop distance"
            return
        stop = last_close - sd
        self.report.signals.append(Signal(sym.symbol, chosen.name, last_ts, "enter", stop, "entry signal"))
        try:
            asset = self.broker.get_asset(sym.symbol)
        except BrokerError as e:
            self.report.skipped[sym.symbol] = f"asset lookup failed: {e}"
            self.alert("warning", "asset_error", f"{sym.symbol}: asset lookup failed: {e}", symbol=sym.symbol)
            return
        assert self.snapshot is not None
        # price estimate: the last completed close (sources offer no live quote)
        decision = self.risk.approve_entry(self.snapshot, sym.symbol, last_close, stop, asset,
                                           self.cfg.costs.one_way_bps(sym.asset_class), self.now)
        if not decision.approved:
            self.report.skipped[sym.symbol] = f"entry not approved: {decision.reason}"
            self.store.record_event("info", "entry_blocked", f"{sym.symbol} {chosen.name} entry: {decision.reason}",
                                    symbol=sym.symbol, strategy=chosen.name)
            return
        plan = {"stop_distance": sd, "stop_price": stop, "signal_close": last_close}
        order = self.submit(sym, Side.BUY, decision.qty, chosen.name, last_ts, f"entry ({decision.reason})", plan)
        if order is not None and order.status is not OrderStatus.REJECTED:
            self.report.entries.append(sym.symbol)
            self._refresh_snapshot()

    # ---------------------------------------------------------------- main
    def run(self) -> CycleReport:
        rep = self.report
        self.store.heartbeat("runner", "running")
        try:
            self.reconcile()
            self._refresh_snapshot()
            self.clear_closed_meta()
            assert self.snapshot is not None
            self.store.record_equity(self.snapshot)
            breaker = self.risk.evaluate(self.snapshot, self.now)
            if not breaker.trading_allowed:
                self.alert("critical", "breaker", "trading restricted: " + "; ".join(breaker.reasons),
                           reasons=breaker.reasons, kill_switch=breaker.kill_switch,
                           daily=breaker.daily_breaker, weekly=breaker.weekly_breaker,
                           drawdown=breaker.drawdown_breaker)
            if self.risk.killswitch.is_engaged():
                rep.halted = True
                for s in self.cfg.enabled_symbols():
                    rep.skipped[s.symbol] = "kill switch engaged"
                self.store.record_event("warning", "cycle_halted", "kill switch engaged: no orders this cycle")
                self.store.heartbeat("runner", "ok", "halted: kill switch engaged")
                return rep

            prepared: list[tuple[SymbolConfig, pd.DataFrame]] = []
            for sym in self.cfg.enabled_symbols():
                if self.asset_classes is not None and sym.asset_class not in self.asset_classes:
                    continue
                rep.symbols_checked.append(sym.symbol)
                try:
                    if not self.market_open(sym.asset_class):
                        rep.skipped[sym.symbol] = f"{sym.asset_class.value} market closed"
                        continue
                    bars = self.load_bars(sym)
                    if bars is not None:
                        prepared.append((sym, bars))
                except BrokerError:
                    raise
                except Exception as e:
                    self._symbol_error(sym, "prepare", e)

            open_syms = {o.symbol for o in self.store.open_orders()}
            for phase in ("exit", "entry"):
                for sym, bars in prepared:
                    if sym.symbol in open_syms:
                        rep.skipped[sym.symbol] = "open order outstanding"
                        continue
                    try:
                        held = self._held_qty(sym.symbol) > _EPS
                        if phase == "exit" and held:
                            self.manage_position(sym, bars)
                        elif phase == "entry" and not held and sym.symbol not in rep.exits:
                            self.consider_entry(sym, bars)
                    except BrokerError:
                        raise
                    except Exception as e:
                        self._symbol_error(sym, phase, e)
            self._refresh_snapshot()
            self.clear_closed_meta()
            if self.snapshot is not None:
                self.store.record_equity(self.snapshot)   # post-trade state for status/dashboard
        except BrokerError as e:
            rep.aborted = True
            rep.errors.append(f"broker: {e}")
            self.alert("critical", "cycle_aborted", f"broker failure, cycle aborted safely: {e}")
            self.store.heartbeat("runner", "error", f"aborted: {e}"[:500])
            return rep
        status = "error" if rep.errors else "ok"
        self.store.heartbeat("runner", status, json.dumps(
            {"submitted": len(rep.orders_submitted), "skipped": len(rep.skipped), "errors": len(rep.errors)}))
        log_event(log, logging.INFO, "cycle_done", **rep.summary())
        return rep

    def _symbol_error(self, sym: SymbolConfig, phase: str, e: Exception) -> None:
        msg = f"{sym.symbol} {phase}: {type(e).__name__}: {e}"
        self.report.errors.append(msg)
        self.report.skipped.setdefault(sym.symbol, f"error: {type(e).__name__}")
        self.alert("critical", "symbol_error", msg, symbol=sym.symbol, phase=phase)


def run_cycle(cfg: AppConfig, broker: Broker, store, source, strategies, risk, now: datetime,
              alerter=None, asset_classes: set[AssetClass] | None = None) -> CycleReport:
    """Run one idempotent trading cycle. ``asset_classes`` limits which symbols are traded
    (the scheduler runs stocks and crypto on separate clocks); None = all enabled symbols."""
    if not getattr(broker, "is_paper", False):
        raise RuntimeError("refusing to run with a broker that is not a paper/sim broker")
    return _Cycle(cfg, broker, store, source, strategies, risk, now, alerter, asset_classes).run()


__all__ = ["CycleReport", "run_cycle", "make_client_order_id"]
