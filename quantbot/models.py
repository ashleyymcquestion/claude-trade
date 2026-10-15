"""Shared domain types. Every module talks in these types; nothing else crosses module lines.

Bar data contract (used everywhere):
    A pandas DataFrame indexed by a tz-aware UTC DatetimeIndex (sorted ascending, unique),
    with float columns ``open, high, low, close, volume``. One frame per symbol. A bar's
    timestamp is its OPEN time; the bar is complete only after the next bar's timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

BAR_COLUMNS = ("open", "high", "low", "close", "volume")


class AssetClass(StrEnum):
    STOCK = "stock"
    CRYPTO = "crypto"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(StrEnum):
    NEW = "new"                  # accepted, not filled
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    PENDING = "pending"          # submitted locally, broker answer unknown (e.g. timeout)

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING)


@dataclass(frozen=True)
class AssetInfo:
    """What the broker says about a symbol. Sizing must respect these."""
    symbol: str
    asset_class: AssetClass
    tradable: bool
    fractionable: bool
    min_order_size: float = 0.0      # in units (crypto) ; 0 = no unit minimum
    min_trade_increment: float = 0.0  # unit step ; 0 = any
    min_notional: float = 1.0        # USD


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str             # deterministic, see engine: f"{strategy}-{symbol}-{bar_ts}-{side}"
    symbol: str
    side: Side
    qty: float                       # units; always > 0 (fractional allowed when fractionable)
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    strategy: str = ""
    reason: str = ""


@dataclass
class Order:
    client_order_id: str
    symbol: str
    side: Side
    qty: float
    status: OrderStatus
    broker_order_id: str | None = None
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    submitted_at: datetime | None = None
    updated_at: datetime | None = None
    strategy: str = ""
    reject_reason: str | None = None


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    symbol: str
    side: Side
    qty: float
    price: float
    fee: float
    ts: datetime


@dataclass
class Position:
    symbol: str
    qty: float                       # long only: > 0
    avg_entry_price: float
    market_price: float
    strategy: str = ""
    stop_price: float | None = None

    @property
    def market_value(self) -> float:
        return self.qty * self.market_price

    @property
    def unrealized_pnl(self) -> float:
        return self.qty * (self.market_price - self.avg_entry_price)


@dataclass(frozen=True)
class AccountSnapshot:
    ts: datetime
    equity: float
    cash: float
    positions: tuple[Position, ...] = field(default_factory=tuple)

    @property
    def gross_exposure(self) -> float:
        return sum(p.market_value for p in self.positions)


@dataclass(frozen=True)
class Signal:
    """A strategy's decision for one symbol at the close of bar ``ts``.

    Acted on no earlier than the NEXT bar (no look-ahead)."""
    symbol: str
    strategy: str
    ts: datetime
    action: str                      # "enter" | "exit" | "hold"
    stop_price: float | None = None  # protective stop for an entry, price level
    reason: str = ""


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    qty: float = 0.0
    reason: str = ""
