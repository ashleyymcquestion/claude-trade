"""quantbot on the Robinhood agentic account (stocks), driven by a scheduled Claude session.

Robinhood is reached only through Claude's Robinhood connector, so this module never talks to
Robinhood. The session gathers the account state, runs `plan`, places exactly the orders it
prints, then runs `record` with the results. Everything else (strategies, data validation,
exits-before-entries, stops, sizing, breakers, kill switch, duplicate prevention,
reconciliation) is the same tested code the Alpaca paper runner uses: `engine.runner.run_cycle`
with `RobinhoodPlanBroker` standing in for a broker.

The stock bot manages a SLEEVE of the account: `sleeve` x account value. The crypto strategy
(trader/, config.robinhood.toml `crypto_sleeve`) manages the rest.

State file (JSON) the session writes for `plan` and again (after placing) for `record`:
    {"now": "2026-10-16T13:31:00Z",              # optional; default: current time
     "account_value": 4.19,                     # get_portfolio total_value
     "cash": 1.93,                              # get_portfolio crypto_buying_power / cash (cash only, no margin)
     "market_open": true,                       # optional override; default: NYSE hours + holiday list
     "positions": {"NVDA": {"quantity": "0.0046", "average_buy_price": "217.2", "mark": 218.1}},
     "order_updates": {"<client_order_id>": {"robinhood_order_id": "...", "state": "filled",
                       "filled_quantity": "0.0046", "average_price": "217.2", "updated_at": "..."}}}
"""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from .broker.base import Broker, OrderRejected
from .config import AppConfig
from .models import AccountSnapshot, AssetClass, AssetInfo, Fill, Order, OrderRequest, OrderStatus, Position, Side

NY = ZoneInfo("America/New_York")
REF_NAMESPACE = uuid.UUID("6f1c8d9e-2b7a-4c55-9a3e-0d4b7e1f2a10")

# Best-effort NYSE full-day closures. If a date is missing here, a market order placed on a
# closed day queues for the next open; the session can also pass "market_open": false.
NYSE_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}

_STATE_MAP = {
    "filled": OrderStatus.FILLED, "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "cancelled": OrderStatus.CANCELED, "canceled": OrderStatus.CANCELED,
    "rejected": OrderStatus.REJECTED, "failed": OrderStatus.REJECTED, "voided": OrderStatus.CANCELED,
    "expired": OrderStatus.EXPIRED,
    "new": OrderStatus.NEW, "queued": OrderStatus.NEW, "confirmed": OrderStatus.NEW,
    "unconfirmed": OrderStatus.NEW, "pending": OrderStatus.NEW,
}


def ref_id_for(client_order_id: str) -> str:
    """Deterministic Robinhood ref_id (idempotency key) for one of our orders."""
    return str(uuid.uuid5(REF_NAMESPACE, client_order_id))


def nyse_open(now: datetime) -> bool:
    ny = now.astimezone(NY)
    return ny.weekday() < 5 and ny.date() not in NYSE_HOLIDAYS and time(9, 30) <= ny.time() < time(15, 55)


def _f(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class RobinhoodPlanBroker(Broker):
    """A Broker that never trades: it reads the account from a state file and collects the
    orders the runner wants into `planned` (and cancellations into `cancels`)."""

    is_paper = True   # run_cycle refuses non-paper brokers; this one places nothing itself.

    def __init__(self, cfg: AppConfig, state: dict, sleeve: float, now: datetime) -> None:
        if not 0 < sleeve <= 1:
            raise ValueError("sleeve must be in (0, 1]")
        self.cfg, self.state, self.sleeve, self.now = cfg, state, sleeve, now
        self.symbols = {s.symbol for s in cfg.enabled_symbols()}
        self.planned: list[OrderRequest] = []
        self.planned_prices: dict[str, float] = {}
        self.cancels: list[str] = []
        self.updates = state.get("order_updates") or {}
        self.store = None                      # set by _build; used to fill in fields an update omits
        self._closes: dict[str, float] = {}

    # --- account -------------------------------------------------------------------------
    def _positions(self) -> list[Position]:
        out = []
        for sym, p in (self.state.get("positions") or {}).items():
            qty = _f(p.get("quantity"))
            if sym in self.symbols and qty > 0:
                mark = _f(p.get("mark")) or _f(p.get("average_buy_price"))
                out.append(Position(sym, qty, _f(p.get("average_buy_price"), mark), mark))
        return out

    def sleeve_equity(self) -> float:
        return self.sleeve * _f(self.state["account_value"])

    def get_account(self) -> AccountSnapshot:
        positions = self._positions()
        held_value = sum(p.market_value for p in positions)
        planned_buys, planned_sells = 0.0, set()
        for r in self.planned:
            if r.side is Side.BUY:
                planned_buys += r.qty * self.planned_prices[r.client_order_id]
                positions.append(Position(r.symbol, r.qty, self.planned_prices[r.client_order_id],
                                          self.planned_prices[r.client_order_id], strategy=r.strategy))
            else:
                planned_sells.add(r.symbol)
        # Buys planned in an earlier run whose result hasn't been recorded yet still hold cash
        # (conservative: if one already filled, its cost is counted twice until `record` runs).
        planned_ids = {r.client_order_id for r in self.planned}
        for o in (self.store.open_orders() if self.store is not None else []):
            if o.side is Side.BUY and o.client_order_id not in planned_ids:
                planned_buys += o.qty * (self._closes.get(o.symbol) or 0.0)
        equity = self.sleeve_equity()
        # Cash only (no margin), and only this sleeve's share of it.
        cash = max(0.0, min(_f(self.state.get("cash")), equity - held_value) - planned_buys)
        return AccountSnapshot(self.now, equity, cash, tuple(positions))

    def get_asset(self, symbol: str) -> AssetInfo:
        # Verified with get_equity_tradability on 2026-10-15: NVDA, AMD, PLTR, QQQ tradable and fractional.
        # Robinhood fractional orders: market, regular hours, up to 6 decimals, $1 minimum.
        return AssetInfo(symbol, AssetClass.STOCK, tradable=True, fractionable=True,
                         min_trade_increment=1e-6, min_notional=1.0)

    def is_market_open(self, asset_class: AssetClass) -> bool:
        if asset_class is not AssetClass.STOCK:
            return False
        if "market_open" in self.state:
            return bool(self.state["market_open"])
        return nyse_open(self.now)

    # --- orders --------------------------------------------------------------------------
    def submit_order(self, req: OrderRequest) -> Order:
        if req.side is Side.SELL:
            held = sum(p.qty for p in self._positions() if p.symbol == req.symbol)
            if req.qty > held + 1e-9:
                raise OrderRejected(f"sell {req.qty} > held {held} (no short selling)")
        for existing in self.planned:
            if existing.client_order_id == req.client_order_id:
                break
        else:
            price = next((p.market_price for p in self._positions() if p.symbol == req.symbol), None)
            price = price or self._last_close(req.symbol)
            if not price or price <= 0:
                raise OrderRejected(f"no price for {req.symbol}; cannot account for the order")
            self.planned.append(req)
            self.planned_prices[req.client_order_id] = price
        return Order(req.client_order_id, req.symbol, req.side, req.qty, OrderStatus.NEW,
                     submitted_at=self.now, updated_at=self.now, strategy=req.strategy)

    def _last_close(self, symbol: str) -> float:
        return _f(self.state.get("last_close", {}).get(symbol)) or _f(self._closes.get(symbol))

    def get_order(self, client_order_id: str) -> Order | None:
        for r in self.planned:
            if r.client_order_id == client_order_id:
                return Order(r.client_order_id, r.symbol, r.side, r.qty, OrderStatus.NEW,
                             submitted_at=self.now, updated_at=self.now, strategy=r.strategy)
        u = self.updates.get(client_order_id)
        if u is None:
            return None
        status = _STATE_MAP.get(str(u.get("state", "")).lower())
        if status is None:
            raise ValueError(f"unknown Robinhood order state {u.get('state')!r} for {client_order_id}")
        ts = u.get("updated_at")
        updated = datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else self.now
        prev = self.store.get_order(client_order_id) if self.store is not None else None
        symbol = u.get("symbol") or (prev.symbol if prev else "")
        side = Side(u.get("side") or (prev.side.value if prev else "buy"))
        qty = _f(u.get("quantity")) or (prev.qty if prev else _f(u.get("filled_quantity")))
        return Order(client_order_id, symbol, side, qty, status,
                     broker_order_id=u.get("robinhood_order_id"), filled_qty=_f(u.get("filled_quantity")),
                     filled_avg_price=_f(u.get("average_price")) or None, updated_at=updated)

    def list_open_orders(self) -> list[Order]:
        return [o for cid in self.updates if (o := self.get_order(cid)) and o.status.is_open]

    def cancel_order(self, client_order_id: str) -> None:
        if client_order_id not in self.cancels:
            self.cancels.append(client_order_id)

    def fills_since(self, client_order_ids: list[str]) -> list[Fill]:
        return []


# ---------------------------------------------------------------------------------------------
def _build(cfg: AppConfig, state: dict, sleeve: float, bars_files: list[str] | None, now: datetime):
    from .alerts import Alerter
    from .data.robinhood import historicals_to_frames, merge_into_csv
    from .data.sources import CsvBarSource, csv_name
    from .risk.killswitch import KillSwitch
    from .risk.limits import RiskManager
    from .store import Store
    from .strategies import build_strategies

    for f in bars_files or []:
        frames = historicals_to_frames(json.loads(Path(f).read_text()), now.strftime("%Y-%m-%dT%H:%MZ"))
        for sym, df in frames.items():
            merge_into_csv(df, Path(cfg.data.bars_dir) / f"{sym}.csv")
    source = CsvBarSource(cfg.data.bars_dir)
    broker = RobinhoodPlanBroker(cfg, state, sleeve, now)
    closes = {}
    for s in cfg.enabled_symbols():
        path = Path(cfg.data.bars_dir) / csv_name(s)
        if path.exists():
            import pandas as pd
            closes[s.symbol] = float(pd.read_csv(path)["close"].iloc[-1])
    broker._closes = closes
    store = Store(cfg.execution.db_path)
    broker.store = store
    ks = KillSwitch(store)
    risk = RiskManager(cfg.risk, store, ks, initial_capital=broker.sleeve_equity())
    return broker, store, source, build_strategies(cfg.strategies), risk, Alerter(cfg.alerts, store)


def _now(state: dict) -> datetime:
    ts = state.get("now")
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC) if ts else datetime.now(UTC)


def pending(cfg: AppConfig) -> list[dict]:
    """Orders still open in the bot's records: the session must report each one's current
    Robinhood state in the next state file's `order_updates`."""
    from .store import Store

    store = Store(cfg.execution.db_path)
    try:
        return [{"client_order_id": o.client_order_id, "robinhood_order_id": o.broker_order_id,
                 "symbol": o.symbol, "side": o.side.value, "status": o.status.value}
                for o in store.open_orders()]
    finally:
        store.close()


def plan(cfg: AppConfig, state: dict, sleeve: float, bars_files: list[str] | None = None) -> dict:
    """Run one cycle and return the orders for the session to place (exactly as printed)."""
    from .engine.runner import run_cycle

    now = _now(state)
    broker, store, source, strategies, risk, alerter = _build(cfg, state, sleeve, bars_files, now)
    try:
        report = run_cycle(cfg, broker, store, source, strategies, risk, now, alerter=alerter,
                           asset_classes={AssetClass.STOCK})
        snap = broker.get_account()
        orders = []
        for r in broker.planned:
            order = {"client_order_id": r.client_order_id, "ref_id": ref_id_for(r.client_order_id),
                     "symbol": r.symbol, "side": r.side.value, "type": "market",
                     "market_hours": "regular_hours", "strategy": r.strategy, "reason": r.reason}
            if r.side is Side.BUY:
                est = r.qty * broker.planned_prices[r.client_order_id]
                order["dollar_amount"] = f"{max(1.0, round(est, 2)):.2f}"
            else:
                # Sell the whole position as Robinhood reports it (no rounding).
                order["quantity"] = str((state.get("positions") or {})[r.symbol]["quantity"])
            orders.append(order)
        cancels = []
        for cid in broker.cancels:
            o = store.get_order(cid)
            cancels.append({"client_order_id": cid,
                            "robinhood_order_id": (broker.updates.get(cid) or {}).get("robinhood_order_id")
                            or (o.broker_order_id if o else None)})
        return {"now": now.isoformat(), "sleeve": sleeve, "sleeve_equity": round(snap.equity, 2),
                "sleeve_cash_after_orders": round(snap.cash, 2), "orders": orders, "cancel": cancels,
                "report": report.summary()}
    finally:
        store.close()


def record(cfg: AppConfig, state: dict, sleeve: float) -> dict:
    """After placing: reconcile the placed orders' results (fills, stops, realized P&L) and record
    equity. `state` is a fresh snapshot including `order_updates` for today's orders."""
    from .engine.runner import _Cycle

    now = _now(state)
    broker, store, source, strategies, risk, alerter = _build(cfg, state, sleeve, None, now)
    try:
        cyc = _Cycle(cfg, broker, store, source, strategies, risk, now, alerter=alerter)
        missing = [o.client_order_id for o in store.open_orders() if o.client_order_id not in broker.updates]
        cyc.reconcile()
        cyc._refresh_snapshot()
        cyc.clear_closed_meta()
        store.record_equity(cyc.snapshot)
        store.heartbeat("runner", "ok", "robinhood record")
        return {"now": now.isoformat(), "reconciled": cyc.report.reconciled,
                "open_orders": [o.client_order_id for o in store.open_orders()],
                "not_reported_yet": missing,
                "positions": {k: {"strategy": v["strategy"], "stop": v["stop_price"]}
                              for k, v in store.all_position_meta().items()},
                "sleeve_equity": round(cyc.snapshot.equity, 2)}
    finally:
        store.close()
