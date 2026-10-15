"""Deterministic in-memory broker for tests and dry runs.

It has NO market data of its own: prices are whatever the caller sets with ``set_price``.
Nothing it produces is a market fact.

Fill model (market orders, immediate unless a fault is injected):
  * execution price = price * (1 + s) for buys, price * (1 - s) for sells, where s is the
    half-spread + slippage of the asset class (``CostConfig``);
  * fee = notional * commission (stocks: ``stock_commission_bps``; crypto: ``crypto_fee_bps``).
  Together these equal ``CostConfig.one_way_bps(asset_class)``, the same one-way cost the
  backtester and sizing use.

Policy enforced like a cash account: no shorting (a sell larger than the position is rejected),
no margin (a buy costing more than cash is rejected), whole shares unless fractionable.

Fault injection for tests (each applies to the NEXT submitted order only):
  ``reject_next(reason)``, ``partial_fill_next(fraction)``, ``keep_open_next()`` (order stays NEW
  until ``fill_open(cid)``), ``fail_next_submit(exc)`` (the order IS accepted and processed, then
  ``exc`` is raised — a timeout after acceptance, to exercise idempotent retry).
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ..config import CostConfig
from ..models import (
    AccountSnapshot,
    AssetClass,
    AssetInfo,
    Fill,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Side,
)
from .base import Broker, BrokerError, OrderRejected

_EPS = 1e-9


class SimulatedBroker(Broker):
    is_paper = True

    def __init__(self, initial_cash: float, costs: CostConfig | None = None,
                 assets: dict[str, AssetInfo] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        if initial_cash < 0:
            raise ValueError("initial_cash must be >= 0")
        self.cash = float(initial_cash)
        self.costs = costs or CostConfig()
        self.assets: dict[str, AssetInfo] = dict(assets or {})
        self._clock = clock or (lambda: datetime.now(UTC))
        self._fixed_now: datetime | None = None
        self.prices: dict[str, float] = {}
        self.market_open: dict[AssetClass, bool] = {AssetClass.STOCK: True, AssetClass.CRYPTO: True}
        self._positions: dict[str, list[float]] = {}     # symbol -> [qty, avg_entry_price]
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._seq = 0
        self.submit_calls = 0                             # every submit_order call, incl. duplicates
        # one-shot faults
        self._reject_reason: str | None = None
        self._partial_fraction: float | None = None
        self._keep_open = False
        self._fail_exc: BaseException | None = None

    # ---------------------------------------------------------------- test controls
    def set_time(self, now: datetime | None) -> None:
        """Pin the broker clock (None = use the real/injected clock)."""
        self._fixed_now = now

    def now(self) -> datetime:
        return self._fixed_now or self._clock()

    def set_price(self, symbol: str, price: float) -> None:
        if not (price > 0 and math.isfinite(price)):
            raise ValueError(f"invalid price {price!r} for {symbol}")
        self.prices[symbol] = float(price)

    def set_market_open(self, asset_class: AssetClass, is_open: bool) -> None:
        self.market_open[AssetClass(asset_class)] = bool(is_open)

    def reject_next(self, reason: str = "rejected by simulator") -> None:
        self._reject_reason = reason

    def partial_fill_next(self, fraction: float) -> None:
        if not 0 < fraction < 1:
            raise ValueError("fraction must be in (0, 1)")
        self._partial_fraction = float(fraction)

    def keep_open_next(self) -> None:
        self._keep_open = True

    def fail_next_submit(self, exception: BaseException) -> None:
        self._fail_exc = exception

    def fill_open(self, client_order_id: str, price: float | None = None) -> Order:
        """Fill the remaining quantity of an open order at ``price`` (default: current price)."""
        o = self._orders.get(client_order_id)
        if o is None or not o.status.is_open:
            raise BrokerError(f"no open order {client_order_id}")
        if price is not None:
            self.set_price(o.symbol, price)
        remaining = o.qty - o.filled_qty
        self._check_executable(o.symbol, o.side, remaining)
        self._execute(o, remaining)
        return copy.copy(o)

    def asset_class_of(self, symbol: str) -> AssetClass:
        a = self.assets.get(symbol)
        if a is not None:
            return a.asset_class
        return AssetClass.CRYPTO if "/" in symbol else AssetClass.STOCK

    # ---------------------------------------------------------------- Broker API
    def get_account(self) -> AccountSnapshot:
        positions = []
        for sym, (qty, avg) in sorted(self._positions.items()):
            positions.append(Position(sym, qty, avg, self.prices.get(sym, avg)))
        equity = self.cash + sum(p.market_value for p in positions)
        return AccountSnapshot(self.now(), equity, self.cash, tuple(positions))

    def get_asset(self, symbol: str) -> AssetInfo:
        a = self.assets.get(symbol)
        if a is not None:
            return a
        return AssetInfo(symbol, self.asset_class_of(symbol), tradable=True, fractionable=True)

    def is_market_open(self, asset_class: AssetClass) -> bool:
        return self.market_open.get(AssetClass(asset_class), False)

    def submit_order(self, req: OrderRequest) -> Order:
        self.submit_calls += 1
        existing = self._orders.get(req.client_order_id)
        if existing is not None:              # idempotent: never a second order for the same id
            return copy.copy(existing)

        now = self.now()
        self._seq += 1
        order = Order(client_order_id=req.client_order_id, symbol=req.symbol, side=Side(req.side),
                      qty=float(req.qty), status=OrderStatus.NEW, broker_order_id=f"sim-{self._seq}",
                      submitted_at=now, updated_at=now, strategy=req.strategy)

        reason = self._reject_reason or self._validate(req)
        self._reject_reason = None
        if reason:
            order.status = OrderStatus.REJECTED
            order.reject_reason = reason
            self._orders[req.client_order_id] = order
            self._clear_faults()
            raise OrderRejected(reason)

        self._orders[req.client_order_id] = order
        if self._keep_open:
            pass
        elif self._partial_fraction is not None:
            self._execute(order, order.qty * self._partial_fraction)
        else:
            self._execute(order, order.qty)
        fail = self._fail_exc
        self._clear_faults()
        if fail is not None:
            raise fail
        return copy.copy(order)

    def get_order(self, client_order_id: str) -> Order | None:
        o = self._orders.get(client_order_id)
        return copy.copy(o) if o is not None else None

    def list_open_orders(self) -> list[Order]:
        return [copy.copy(o) for o in self._orders.values() if o.status.is_open]

    def cancel_order(self, client_order_id: str) -> None:
        o = self._orders.get(client_order_id)
        if o is None:
            raise BrokerError(f"unknown order {client_order_id}")
        if o.status.is_open:
            o.status = OrderStatus.CANCELED
            o.updated_at = self.now()

    def fills_since(self, client_order_ids: list[str]) -> list[Fill]:
        wanted = set(client_order_ids)
        return [f for f in self._fills if f.client_order_id in wanted]

    # ---------------------------------------------------------------- internals
    def _clear_faults(self) -> None:
        self._partial_fraction = None
        self._keep_open = False
        self._fail_exc = None

    def _validate(self, req: OrderRequest) -> str | None:
        if req.order_type is not OrderType.MARKET:
            return "simulator supports market orders only"
        if not (req.qty > 0 and math.isfinite(req.qty)):
            return f"invalid quantity {req.qty}"
        asset = self.get_asset(req.symbol)
        if not asset.tradable:
            return f"{req.symbol} is not tradable"
        if not asset.fractionable and abs(req.qty - round(req.qty)) > _EPS:
            return f"{req.symbol} is not fractionable; qty {req.qty} must be whole"
        if not self.is_market_open(asset.asset_class):
            return f"market closed for {asset.asset_class.value}"
        try:
            self._check_executable(req.symbol, Side(req.side), req.qty)
        except OrderRejected as e:
            return str(e)
        return None

    def _exec_price_and_fee(self, symbol: str, side: Side, qty: float) -> tuple[float, float]:
        price = self.prices[symbol]
        c = self.costs
        if self.asset_class_of(symbol) is AssetClass.CRYPTO:
            s_bps, fee_bps = c.crypto_half_spread_bps + c.crypto_slippage_bps, c.crypto_fee_bps
        else:
            s_bps, fee_bps = c.stock_half_spread_bps + c.stock_slippage_bps, c.stock_commission_bps
        px = price * (1 + s_bps / 10_000) if side is Side.BUY else price * (1 - s_bps / 10_000)
        return px, qty * px * fee_bps / 10_000

    def _check_executable(self, symbol: str, side: Side, qty: float) -> None:
        if symbol not in self.prices:
            raise OrderRejected(f"no price for {symbol} in simulator")
        if side is Side.SELL:
            held = self._positions.get(symbol, [0.0, 0.0])[0]
            if qty > held + _EPS:
                raise OrderRejected(f"sell {qty} exceeds position {held} (no shorting)")
        else:
            px, fee = self._exec_price_and_fee(symbol, side, qty)
            if qty * px + fee > self.cash + 1e-6:
                raise OrderRejected(f"insufficient cash: need {qty * px + fee:.2f}, have {self.cash:.2f} (no margin)")

    def _execute(self, order: Order, qty: float) -> None:
        if qty <= _EPS:
            return
        px, fee = self._exec_price_and_fee(order.symbol, order.side, qty)
        pos = self._positions.get(order.symbol)
        if order.side is Side.BUY:
            self.cash -= qty * px + fee
            if pos is None:
                self._positions[order.symbol] = [qty, px]
            else:
                new_qty = pos[0] + qty
                pos[1] = (pos[0] * pos[1] + qty * px) / new_qty
                pos[0] = new_qty
        else:
            self.cash += qty * px - fee
            assert pos is not None
            pos[0] -= qty
            if pos[0] <= _EPS:
                del self._positions[order.symbol]
        prev_qty, prev_avg = order.filled_qty, order.filled_avg_price or 0.0
        order.filled_qty = prev_qty + qty
        order.filled_avg_price = (prev_qty * prev_avg + qty * px) / order.filled_qty
        order.status = (OrderStatus.FILLED if order.filled_qty >= order.qty - _EPS
                        else OrderStatus.PARTIALLY_FILLED)
        now = self.now()
        # updated_at must differ between fills of one order: the store dedupes fills on (id, ts, qty, price)
        if order.updated_at is not None and now <= order.updated_at and prev_qty > 0:
            now = order.updated_at + timedelta(microseconds=1)
        order.updated_at = now
        self._fills.append(Fill(order.client_order_id, order.symbol, order.side, qty, px, fee, now))


__all__ = ["SimulatedBroker"]
