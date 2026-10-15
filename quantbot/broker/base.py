"""Broker interface. Implementations: SimulatedBroker (sim.py), AlpacaPaperBroker (alpaca_paper.py).

There is intentionally no live implementation here. See live_gate.py."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AccountSnapshot, AssetClass, AssetInfo, Fill, Order, OrderRequest


class BrokerError(Exception):
    """Broker answered with an error or could not be reached."""


class OrderRejected(BrokerError):
    pass


class Broker(ABC):
    is_paper: bool = True

    @abstractmethod
    def get_account(self) -> AccountSnapshot: ...

    @abstractmethod
    def get_asset(self, symbol: str) -> AssetInfo: ...

    @abstractmethod
    def is_market_open(self, asset_class: AssetClass) -> bool: ...

    @abstractmethod
    def submit_order(self, req: OrderRequest) -> Order:
        """Idempotent on ``req.client_order_id``: submitting the same id twice must return the
        existing order, never create a second one. Raises OrderRejected on a broker rejection."""

    @abstractmethod
    def get_order(self, client_order_id: str) -> Order | None: ...

    @abstractmethod
    def list_open_orders(self) -> list[Order]: ...

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> None: ...

    @abstractmethod
    def fills_since(self, client_order_ids: list[str]) -> list[Fill]:
        """Fills for these orders the broker knows about (used for reconciliation)."""
