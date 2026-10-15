"""Alpaca PAPER broker adapter (alpaca-py 0.44.0).

Hard rules:
  * The SDK client is always ``TradingClient(api_key, secret_key, paper=True)``. There is no
    parameter for a URL override or for ``paper=False``; an injected client whose base URL is not
    the paper endpoint is refused.
  * Credentials come from ``Credentials`` (``Credentials.from_env()`` by default) and are scrubbed
    from every exception message this module raises.
  * ``submit_order`` never resubmits blindly: on a duplicate-``client_order_id`` error, a timeout or
    a connection error it first looks the order up with ``get_order_by_client_id``.

Fills: ``TradingClient`` in alpaca-py 0.44.0 has no account-activities endpoint (that lives on the
Broker API client), so fills are derived from each order's cumulative ``filled_qty`` /
``filled_avg_price``. ``fills_since`` returns one CUMULATIVE record per order; the runner derives
per-execution fills from filled-quantity deltas. Fees are not reported on the order and are 0 here.

Assumptions to verify against Alpaca's current documentation (not verifiable from the sandbox):
  * min notional of $1 for fractional stock orders and for crypto orders;
  * duplicate client_order_id is answered with HTTP 422 and a message containing "unique";
  * ``get_asset("BTC/USD")`` resolves (a fallback to "BTCUSD" is tried on 404).
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from typing import Any

from ..config import Credentials
from ..logging_setup import redact
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

PAPER_URL = "https://paper-api.alpaca.markets"
CRYPTO_QUOTES = ("USDT", "USDC", "USD", "BTC")
FRACTIONAL_MIN_NOTIONAL = 1.0      # assumption, verify
CRYPTO_MIN_NOTIONAL = 1.0          # assumption, verify

_STATUS_MAP = {
    "new": OrderStatus.NEW, "accepted": OrderStatus.NEW, "pending_new": OrderStatus.NEW,
    "accepted_for_bidding": OrderStatus.NEW, "held": OrderStatus.NEW, "calculated": OrderStatus.NEW,
    "pending_review": OrderStatus.NEW, "pending_replace": OrderStatus.NEW,
    "pending_cancel": OrderStatus.NEW, "suspended": OrderStatus.NEW, "stopped": OrderStatus.NEW,
    "done_for_day": OrderStatus.NEW,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED, "replaced": OrderStatus.CANCELED,
    "expired": OrderStatus.EXPIRED,
    "rejected": OrderStatus.REJECTED,
}


def _val(x: Any) -> Any:
    """Enum -> its value; anything else unchanged."""
    return getattr(x, "value", x)


def _f(x: Any, default: float | None = None) -> float | None:
    if x is None or x == "":
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def normalize_symbol(symbol: str, asset_class: AssetClass | str | None = None) -> str:
    """Alpaca reports crypto positions as "BTCUSD" but orders/assets as "BTC/USD". Return the
    slash form for crypto; stocks unchanged."""
    if not symbol or "/" in symbol:
        return symbol
    is_crypto = _val(asset_class) == "crypto" if asset_class is not None else False
    if not is_crypto:
        return symbol
    for q in CRYPTO_QUOTES:
        if symbol.endswith(q) and len(symbol) > len(q):
            return f"{symbol[:-len(q)]}/{q}"
    return symbol


def map_status(status: Any, filled_qty: float = 0.0) -> OrderStatus:
    s = str(_val(status) or "").lower()
    st = _STATUS_MAP.get(s, OrderStatus.NEW)
    if st is OrderStatus.NEW and filled_qty > 0:
        return OrderStatus.PARTIALLY_FILLED
    return st


def _is_crypto_class(asset_class: Any, symbol: str = "") -> bool:
    v = _val(asset_class)
    if v is not None:
        return v == "crypto"
    return "/" in symbol


def map_order(o: Any, strategy: str = "") -> Order:
    filled = _f(getattr(o, "filled_qty", None), 0.0) or 0.0
    symbol = normalize_symbol(getattr(o, "symbol", "") or "",
                              "crypto" if _is_crypto_class(getattr(o, "asset_class", None),
                                                           getattr(o, "symbol", "") or "") else "stock")
    side = Side(str(_val(getattr(o, "side", "buy"))).lower())
    return Order(
        client_order_id=o.client_order_id, symbol=symbol, side=side,
        qty=_f(getattr(o, "qty", None), 0.0) or 0.0,
        status=map_status(getattr(o, "status", None), filled),
        broker_order_id=str(o.id) if getattr(o, "id", None) is not None else None,
        filled_qty=filled, filled_avg_price=_f(getattr(o, "filled_avg_price", None)),
        submitted_at=_utc(getattr(o, "submitted_at", None)),
        updated_at=_utc(getattr(o, "updated_at", None)),
        strategy=strategy,
    )


def map_position(p: Any) -> Position:
    sym = normalize_symbol(p.symbol, "crypto" if _is_crypto_class(getattr(p, "asset_class", None), p.symbol)
                           else "stock")
    qty = _f(p.qty, 0.0) or 0.0
    avg = _f(p.avg_entry_price, 0.0) or 0.0
    price = _f(getattr(p, "current_price", None))
    if price is None:
        mv = _f(getattr(p, "market_value", None))
        price = mv / qty if (mv is not None and qty) else avg
    return Position(symbol=sym, qty=qty, avg_entry_price=avg, market_price=price)


def map_asset(a: Any, symbol: str | None = None) -> AssetInfo:
    crypto = _is_crypto_class(getattr(a, "asset_class", None), a.symbol)
    status = str(_val(getattr(a, "status", "active")) or "").lower()
    tradable = bool(getattr(a, "tradable", False)) and status == "active"
    fractionable = bool(getattr(a, "fractionable", False))
    sym = symbol or normalize_symbol(a.symbol, "crypto" if crypto else "stock")
    if crypto:
        return AssetInfo(sym, AssetClass.CRYPTO, tradable=tradable, fractionable=True,
                         min_order_size=_f(getattr(a, "min_order_size", None), 0.0) or 0.0,
                         min_trade_increment=_f(getattr(a, "min_trade_increment", None), 0.0) or 0.0,
                         min_notional=CRYPTO_MIN_NOTIONAL)
    return AssetInfo(sym, AssetClass.STOCK, tradable=tradable, fractionable=fractionable,
                     min_order_size=0.0, min_trade_increment=0.0,
                     min_notional=FRACTIONAL_MIN_NOTIONAL if fractionable else 0.0)


def _api_error_types() -> tuple[type, ...]:
    try:
        from alpaca.common.exceptions import APIError
        return (APIError,)
    except Exception:  # pragma: no cover - SDK always installed
        return ()


def _status_code(e: BaseException) -> int | None:
    try:
        code = getattr(e, "status_code", None)
    except Exception:
        code = None
    return int(code) if isinstance(code, int) else None


def _api_message(e: BaseException) -> str:
    try:
        msg = e.message  # type: ignore[attr-defined]  # parses the JSON body
        if isinstance(msg, str):
            return msg
    except Exception:
        pass
    raw = str(e)
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "message" in data:
            return str(data["message"])
    except ValueError:
        pass
    return raw


def is_duplicate_error(e: BaseException) -> bool:
    m = _api_message(e).lower()
    return "client_order_id" in m and any(w in m for w in ("unique", "duplicate", "already exists"))


def is_not_found(e: BaseException) -> bool:
    return _status_code(e) == 404 or "not found" in _api_message(e).lower()


def _is_transient(e: BaseException) -> bool:
    """Timeouts and connection failures (requests' exceptions subclass OSError)."""
    return isinstance(e, (TimeoutError, ConnectionError, OSError))


class AlpacaPaperBroker(Broker):
    is_paper = True

    def __init__(self, creds: Credentials | None = None, client: Any | None = None) -> None:
        self._secrets: tuple[str, ...] = ()
        if client is None:
            creds = creds or Credentials.from_env()
            self._secrets = (creds.api_key, creds.secret_key)
            from alpaca.trading.client import TradingClient

            # paper=True is hard-coded; no url_override is ever passed.
            client = TradingClient(creds.api_key, creds.secret_key, paper=True)
        elif creds is not None:
            self._secrets = (creds.api_key, creds.secret_key)
        base = getattr(client, "_base_url", None)
        if isinstance(_val(base), str) and str(_val(base)).rstrip("/") != PAPER_URL:
            raise BrokerError("refusing a trading client that does not point at the Alpaca PAPER endpoint")
        self._client = client
        self._assets: dict[str, AssetInfo] = {}

    # ---------------------------------------------------------------- safety helpers
    def _safe(self, text: str) -> str:
        for s in self._secrets:
            if s:
                text = text.replace(s, "***")
        return redact(text)

    def _err(self, what: str, e: BaseException) -> str:
        code = _status_code(e)
        return self._safe(f"{what}: {type(e).__name__}{f' {code}' if code else ''}: {_api_message(e)}")

    def _call(self, what: str, fn, *args, **kwargs):
        """Run an SDK call; any failure becomes a BrokerError with a scrubbed message."""
        try:
            return fn(*args, **kwargs)
        except BrokerError:
            raise
        except Exception as e:
            raise BrokerError(self._err(what, e)) from None

    # ---------------------------------------------------------------- account / market
    def get_account(self) -> AccountSnapshot:
        acct = self._call("get_account", self._client.get_account)
        positions = self._call("get_all_positions", self._client.get_all_positions)
        pos = tuple(map_position(p) for p in positions)
        cash = _f(getattr(acct, "cash", None), 0.0) or 0.0
        equity = _f(getattr(acct, "equity", None))
        if equity is None:
            equity = cash + sum(p.market_value for p in pos)
        return AccountSnapshot(ts=datetime.now(UTC), equity=equity, cash=cash, positions=pos)

    def is_market_open(self, asset_class: AssetClass) -> bool:
        if AssetClass(asset_class) is AssetClass.CRYPTO:
            return True
        clock = self._call("get_clock", self._client.get_clock)
        return bool(clock.is_open)

    def _raw_asset(self, symbol: str) -> Any:
        try:
            return self._client.get_asset(symbol)
        except Exception as e:
            if "/" in symbol and _api_error_types() and isinstance(e, _api_error_types()) and is_not_found(e):
                try:
                    return self._client.get_asset(symbol.replace("/", ""))
                except Exception as e2:
                    raise BrokerError(self._err(f"get_asset {symbol}", e2)) from None
            raise BrokerError(self._err(f"get_asset {symbol}", e)) from None

    def get_asset(self, symbol: str) -> AssetInfo:
        if symbol not in self._assets:
            self._assets[symbol] = map_asset(self._raw_asset(symbol), symbol)
        return self._assets[symbol]

    def verify_symbols(self, symbols: list[str]) -> dict[str, dict]:
        """Per symbol: {tradable, fractionable, asset_class, status, reason}. A symbol Alpaca does not
        know is reported (not raised); a connection failure raises BrokerError."""
        out: dict[str, dict] = {}
        api_errors = _api_error_types()
        for sym in symbols:
            try:
                try:
                    a = self._client.get_asset(sym)
                except Exception as e:
                    if "/" in sym and api_errors and isinstance(e, api_errors) and is_not_found(e):
                        a = self._client.get_asset(sym.replace("/", ""))
                    else:
                        raise
            except Exception as e:
                if api_errors and isinstance(e, api_errors):
                    out[sym] = {"tradable": False, "fractionable": False, "asset_class": None,
                                "status": "error", "reason": self._err("get_asset", e)}
                    continue
                raise BrokerError(self._err(f"verify {sym}", e)) from None
            info = map_asset(a, sym)
            status = str(_val(getattr(a, "status", "")) or "")
            reasons = []
            if not getattr(a, "tradable", False):
                reasons.append("not tradable at Alpaca")
            if status.lower() != "active":
                reasons.append(f"status {status}")
            if info.asset_class is AssetClass.STOCK and not info.fractionable:
                reasons.append("not fractionable: whole shares only")
            out[sym] = {"tradable": info.tradable, "fractionable": info.fractionable,
                        "asset_class": info.asset_class.value, "status": status,
                        "reason": "; ".join(reasons) or "ok",
                        "min_order_size": info.min_order_size,
                        "min_trade_increment": info.min_trade_increment}
        return out

    # ---------------------------------------------------------------- orders
    def _lookup(self, client_order_id: str) -> Any | None:
        """SDK order for this client id, None if Alpaca says it does not exist."""
        try:
            return self._client.get_order_by_client_id(client_order_id)
        except Exception as e:
            api_errors = _api_error_types()
            if api_errors and isinstance(e, api_errors) and is_not_found(e):
                return None
            raise BrokerError(self._err(f"get_order_by_client_id {client_order_id}", e)) from None

    def get_order(self, client_order_id: str) -> Order | None:
        o = self._lookup(client_order_id)
        return map_order(o) if o is not None else None

    def _build_request(self, req: OrderRequest):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        if req.order_type is not OrderType.MARKET:
            raise OrderRejected("only market orders are supported by quantbot")
        if not (req.qty > 0 and math.isfinite(req.qty)):
            raise OrderRejected(f"invalid quantity {req.qty}")
        asset = self.get_asset(req.symbol)
        if not asset.tradable:
            raise OrderRejected(f"{req.symbol} is not tradable at Alpaca")
        if asset.asset_class is AssetClass.STOCK:
            if not asset.fractionable and abs(req.qty - round(req.qty)) > 1e-9:
                raise OrderRejected(f"{req.symbol} is not fractionable; qty {req.qty} must be whole")
            tif = TimeInForce.DAY
        else:
            tif = TimeInForce.GTC
        return MarketOrderRequest(
            symbol=req.symbol, qty=req.qty,
            side=OrderSide.BUY if Side(req.side) is Side.BUY else OrderSide.SELL,
            time_in_force=tif, client_order_id=req.client_order_id,
        )

    def submit_order(self, req: OrderRequest) -> Order:
        request = self._build_request(req)
        api_errors = _api_error_types()
        attempts = 0
        while True:
            attempts += 1
            try:
                return map_order(self._client.submit_order(request), req.strategy)
            except Exception as e:
                if api_errors and isinstance(e, api_errors):
                    if is_duplicate_error(e):
                        existing = self._lookup(req.client_order_id)
                        if existing is not None:
                            return map_order(existing, req.strategy)
                        raise BrokerError(self._err("duplicate client_order_id but order not found", e)) from None
                    code = _status_code(e)
                    if code in (403, 422):
                        raise OrderRejected(self._err("order rejected", e)) from None
                    if code is not None and code >= 500:
                        # The server may or may not have accepted it: look before deciding.
                        existing = self._lookup(req.client_order_id)
                        if existing is not None:
                            return map_order(existing, req.strategy)
                    raise BrokerError(self._err("submit_order", e)) from None
                if _is_transient(e):
                    # Timeout/connection error: the order may have been accepted. Look first.
                    existing = self._lookup(req.client_order_id)
                    if existing is not None:
                        return map_order(existing, req.strategy)
                    if attempts < 2:
                        continue  # confirmed absent: one retry with the SAME client_order_id
                    raise BrokerError(self._err("submit_order failed twice", e)) from None
                raise BrokerError(self._err("submit_order", e)) from None

    def list_open_orders(self) -> list[Order]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._call("get_orders", self._client.get_orders,
                            GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500))
        return [map_order(o) for o in orders]

    def cancel_order(self, client_order_id: str) -> None:
        o = self._lookup(client_order_id)
        if o is None:
            raise BrokerError(f"cannot cancel unknown order {client_order_id}")
        self._call("cancel_order_by_id", self._client.cancel_order_by_id, o.id)

    def fills_since(self, client_order_ids: list[str]) -> list[Fill]:
        """One cumulative fill record per order (see module docstring); fee unknown -> 0."""
        out: list[Fill] = []
        for cid in client_order_ids:
            o = self._lookup(cid)
            if o is None:
                continue
            m = map_order(o)
            if m.filled_qty > 0 and m.filled_avg_price:
                ts = _utc(getattr(o, "filled_at", None)) or m.updated_at or datetime.now(UTC)
                out.append(Fill(cid, m.symbol, m.side, m.filled_qty, m.filled_avg_price, 0.0, ts))
        return out


__all__ = ["AlpacaPaperBroker", "normalize_symbol", "map_order", "map_position", "map_asset", "map_status"]
