"""AlpacaPaperBroker against FAKE SDK clients only. No network: the Alpaca endpoints are not
reachable from the build sandbox, so these tests verify the adapter logic, not Alpaca itself."""

import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from alpaca.common.exceptions import APIError
from alpaca.trading.enums import AssetClass as SdkAssetClass
from alpaca.trading.enums import AssetStatus, OrderSide, TimeInForce
from alpaca.trading.enums import OrderStatus as SdkOrderStatus

import quantbot.broker.alpaca_paper as ap
from quantbot.broker.alpaca_paper import AlpacaPaperBroker, normalize_symbol
from quantbot.broker.base import BrokerError, OrderRejected
from quantbot.broker.live_gate import LiveTradingNotAllowed, assert_live_trading_not_enabled
from quantbot.config import Credentials
from quantbot.models import AssetClass, OrderRequest, OrderStatus, Side

KEY = "PKFAKEKEY1234567890ABCD"
SECRET = "fake-secret-value-abcdefghijklmnop"
CREDS = Credentials(api_key=KEY, secret_key=SECRET)
T0 = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


def api_error(status: int, message: str) -> APIError:
    http = SimpleNamespace(response=SimpleNamespace(status_code=status), request=None)
    return APIError(json.dumps({"code": 40010001, "message": message}), http)


def sdk_order(cid, symbol="NVDA", side=OrderSide.BUY, qty="2", filled="2", avg="100.5",
              status=SdkOrderStatus.FILLED, asset_class=SdkAssetClass.US_EQUITY):
    return SimpleNamespace(id=f"uuid-{cid}", client_order_id=cid, symbol=symbol, side=side, qty=qty,
                           filled_qty=filled, filled_avg_price=avg, status=status, submitted_at=T0,
                           updated_at=T0, filled_at=T0, asset_class=asset_class)


def sdk_asset(symbol, cls=SdkAssetClass.US_EQUITY, tradable=True, fractionable=True,
              status=AssetStatus.ACTIVE, min_order_size=None, min_trade_increment=None):
    return SimpleNamespace(symbol=symbol, asset_class=cls, tradable=tradable, fractionable=fractionable,
                           status=status, min_order_size=min_order_size, min_trade_increment=min_trade_increment)


class FakeClient:
    def __init__(self):
        self.orders = {}
        self.submitted = []
        self.submit_errors = []          # raised in order by successive submit_order calls
        self.accept_before_error = False # simulate "accepted, then the response timed out"
        self.assets = {"NVDA": sdk_asset("NVDA"), "BRK.A": sdk_asset("BRK.A", fractionable=False),
                       "BTC/USD": sdk_asset("BTC/USD", SdkAssetClass.CRYPTO, min_order_size="0.0001",
                                            min_trade_increment="0.000000001")}
        self.lookups = []
        self.cancelled = []
        self.clock_open = True

    def submit_order(self, req):
        self.submitted.append(req)
        if self.submit_errors:
            err = self.submit_errors.pop(0)
            if self.accept_before_error:
                self.orders[req.client_order_id] = sdk_order(req.client_order_id, req.symbol)
            raise err
        if req.client_order_id in self.orders:
            raise api_error(422, "client_order_id must be unique")
        o = sdk_order(req.client_order_id, req.symbol, qty=str(req.qty), filled=str(req.qty))
        self.orders[req.client_order_id] = o
        return o

    def get_order_by_client_id(self, cid):
        self.lookups.append(cid)
        if cid not in self.orders:
            raise api_error(404, "order not found")
        return self.orders[cid]

    def get_asset(self, symbol):
        if symbol not in self.assets:
            raise api_error(404, "asset not found")
        return self.assets[symbol]

    def get_clock(self):
        return SimpleNamespace(is_open=self.clock_open)

    def get_account(self):
        return SimpleNamespace(cash="500.00", equity="1250.00")

    def get_all_positions(self):
        return [SimpleNamespace(symbol="BTCUSD", asset_class=SdkAssetClass.CRYPTO, qty="0.01",
                                avg_entry_price="50000", current_price="55000", market_value="550"),
                SimpleNamespace(symbol="NVDA", asset_class=SdkAssetClass.US_EQUITY, qty="2",
                                avg_entry_price="100", current_price="100", market_value="200")]

    def get_orders(self, filter=None):
        self.last_filter = filter
        return [o for o in self.orders.values() if o.status in (SdkOrderStatus.NEW, SdkOrderStatus.PARTIALLY_FILLED)]

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)


def _req(cid="trend-NVDA-20261014-buy", sym="NVDA", qty=2.0, side=Side.BUY):
    return OrderRequest(cid, sym, side, qty, strategy="trend")


# ---------------------------------------------------------------- paper enforcement
def test_constructor_hardcodes_paper_true(monkeypatch):
    calls = []

    class RecordingTradingClient:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    import alpaca.trading.client as tc
    monkeypatch.setattr(tc, "TradingClient", RecordingTradingClient)
    AlpacaPaperBroker(CREDS)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert kwargs.get("paper") is True
    assert "url_override" not in kwargs and len(args) == 2


def test_real_sdk_client_points_at_paper_and_live_client_is_refused():
    from alpaca.trading.client import TradingClient
    b = AlpacaPaperBroker(CREDS)                      # constructing the client makes no request
    assert str(getattr(b._client._base_url, "value", b._client._base_url)) == ap.PAPER_URL
    live = TradingClient(KEY, SECRET, paper=False)
    with pytest.raises(BrokerError, match="PAPER"):
        AlpacaPaperBroker(CREDS, client=live)


def test_live_gate_raises(monkeypatch):
    assert_live_trading_not_enabled()
    import quantbot.broker.live_gate as lg
    monkeypatch.setattr(lg, "LIVE_TRADING_ENABLED", True)
    with pytest.raises(LiveTradingNotAllowed):
        lg.assert_live_trading_not_enabled()
    monkeypatch.setattr(lg, "LIVE_TRADING_ENABLED", False)
    monkeypatch.setenv("QUANTBOT_LIVE", "1")
    with pytest.raises(LiveTradingNotAllowed):
        lg.assert_live_trading_not_enabled()
    monkeypatch.delenv("QUANTBOT_LIVE")
    monkeypatch.setenv("ALPACA_PAPER", "false")
    with pytest.raises(LiveTradingNotAllowed):
        lg.assert_live_trading_not_enabled()


# ---------------------------------------------------------------- mapping
def test_symbol_normalization():
    assert normalize_symbol("BTCUSD", "crypto") == "BTC/USD"
    assert normalize_symbol("ETHUSDT", AssetClass.CRYPTO) == "ETH/USDT"
    assert normalize_symbol("BTC/USD", "crypto") == "BTC/USD"
    assert normalize_symbol("AMD", "us_equity") == "AMD"
    assert normalize_symbol("USD", "crypto") == "USD"


def test_account_positions_are_normalized():
    b = AlpacaPaperBroker(CREDS, client=FakeClient())
    acct = b.get_account()
    assert acct.cash == 500.0 and acct.equity == 1250.0
    syms = {p.symbol: p for p in acct.positions}
    assert set(syms) == {"BTC/USD", "NVDA"}
    assert syms["BTC/USD"].market_price == 55000.0 and syms["BTC/USD"].avg_entry_price == 50000.0


def test_assets_and_clock():
    c = FakeClient()
    b = AlpacaPaperBroker(CREDS, client=c)
    btc = b.get_asset("BTC/USD")
    assert btc.asset_class is AssetClass.CRYPTO and btc.min_order_size == 0.0001
    assert btc.min_trade_increment == pytest.approx(1e-9)
    nv = b.get_asset("NVDA")
    assert nv.fractionable and nv.min_notional == 1.0
    assert b.is_market_open(AssetClass.CRYPTO)
    c.clock_open = False
    assert not b.is_market_open(AssetClass.STOCK)
    res = b.verify_symbols(["NVDA", "BRK.A", "NOPE"])
    assert res["NVDA"]["tradable"] and res["NVDA"]["reason"] == "ok"
    assert not res["BRK.A"]["fractionable"] and "whole shares" in res["BRK.A"]["reason"]
    assert not res["NOPE"]["tradable"] and res["NOPE"]["status"] == "error"


def test_status_mapping():
    o = ap.map_order(sdk_order("x", status=SdkOrderStatus.ACCEPTED, filled="0", avg=None))
    assert o.status is OrderStatus.NEW and o.filled_avg_price is None
    o = ap.map_order(sdk_order("x", status=SdkOrderStatus.PARTIALLY_FILLED, filled="1"))
    assert o.status is OrderStatus.PARTIALLY_FILLED and o.filled_qty == 1.0
    o = ap.map_order(sdk_order("x", symbol="BTCUSD", asset_class=SdkAssetClass.CRYPTO))
    assert o.symbol == "BTC/USD"


# ---------------------------------------------------------------- submit
def test_submit_builds_market_request_with_tif_and_cid():
    c = FakeClient()
    b = AlpacaPaperBroker(CREDS, client=c)
    o = b.submit_order(_req())
    assert o.status is OrderStatus.FILLED and o.client_order_id == "trend-NVDA-20261014-buy"
    r = c.submitted[0]
    assert r.time_in_force == TimeInForce.DAY and r.client_order_id == "trend-NVDA-20261014-buy"
    assert r.side == OrderSide.BUY and r.qty == 2.0
    b.submit_order(_req("c-btc", "BTC/USD", 0.001))
    assert c.submitted[1].time_in_force == TimeInForce.GTC


def test_fractional_qty_on_non_fractionable_stock_rejected_locally():
    c = FakeClient()
    b = AlpacaPaperBroker(CREDS, client=c)
    with pytest.raises(OrderRejected, match="fractionable"):
        b.submit_order(_req("x", "BRK.A", 0.5))
    assert c.submitted == []


def test_duplicate_client_order_id_returns_existing_without_second_submit():
    c = FakeClient()
    c.orders["dup"] = sdk_order("dup")
    b = AlpacaPaperBroker(CREDS, client=c)
    o = b.submit_order(_req("dup"))
    assert o.client_order_id == "dup" and o.status is OrderStatus.FILLED
    assert len(c.submitted) == 1          # the single attempt that was refused as duplicate
    assert c.lookups == ["dup"]


def test_timeout_after_acceptance_looks_up_and_does_not_resubmit():
    c = FakeClient()
    c.submit_errors = [TimeoutError("read timed out")]
    c.accept_before_error = True
    b = AlpacaPaperBroker(CREDS, client=c)
    o = b.submit_order(_req("t1"))
    assert o.client_order_id == "t1"
    assert len(c.submitted) == 1 and c.lookups == ["t1"]


def test_connection_error_before_acceptance_retries_once_after_lookup():
    import requests
    c = FakeClient()
    c.submit_errors = [requests.exceptions.ConnectionError("connection refused")]
    b = AlpacaPaperBroker(CREDS, client=c)
    o = b.submit_order(_req("t2"))
    assert o.status is OrderStatus.FILLED
    assert len(c.submitted) == 2 and c.lookups == ["t2"]   # looked up BEFORE the retry


def test_repeated_timeouts_raise_broker_error():
    c = FakeClient()
    c.submit_errors = [TimeoutError("t"), TimeoutError("t")]
    b = AlpacaPaperBroker(CREDS, client=c)
    with pytest.raises(BrokerError):
        b.submit_order(_req("t3"))
    assert len(c.submitted) == 2 and c.lookups == ["t3", "t3"]


@pytest.mark.parametrize("status", [403, 422])
def test_rejection_maps_to_order_rejected(status):
    c = FakeClient()
    c.submit_errors = [api_error(status, "insufficient buying power")]
    b = AlpacaPaperBroker(CREDS, client=c)
    with pytest.raises(OrderRejected, match="insufficient buying power"):
        b.submit_order(_req("r"))


def test_server_error_looks_up_before_deciding():
    c = FakeClient()
    c.submit_errors = [api_error(500, "internal")]
    c.accept_before_error = True
    b = AlpacaPaperBroker(CREDS, client=c)
    assert b.submit_order(_req("s5")).client_order_id == "s5"
    assert c.lookups == ["s5"]


def test_credentials_never_in_exception_messages_or_logs(caplog):
    c = FakeClient()
    c.submit_errors = [api_error(403, f"forbidden for key {KEY} secret {SECRET}")]
    b = AlpacaPaperBroker(CREDS, client=c)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(OrderRejected) as ei:
        b.submit_order(_req("leak"))
    text = str(ei.value) + repr(ei.value)
    assert KEY not in text and SECRET not in text and "***" in text
    assert ei.value.__cause__ is None and ei.value.__suppress_context__

    def boom(*a, **k):
        raise ConnectionError(f"failed with {SECRET}")
    c.get_account = boom
    with pytest.raises(BrokerError) as ei2:
        b.get_account()
    assert SECRET not in str(ei2.value)
    assert KEY not in caplog.text and SECRET not in caplog.text
    assert KEY not in repr(CREDS) and SECRET not in str(CREDS)


def test_open_orders_cancel_and_fills():
    c = FakeClient()
    c.orders["o1"] = sdk_order("o1", status=SdkOrderStatus.NEW, filled="0", avg=None)
    c.orders["o2"] = sdk_order("o2", status=SdkOrderStatus.PARTIALLY_FILLED, filled="1", avg="99")
    b = AlpacaPaperBroker(CREDS, client=c)
    assert {o.client_order_id for o in b.list_open_orders()} == {"o1", "o2"}
    assert getattr(c.last_filter.status, "value", c.last_filter.status) == "open"
    b.cancel_order("o1")
    assert c.cancelled == ["uuid-o1"]
    with pytest.raises(BrokerError):
        b.cancel_order("missing")
    fills = b.fills_since(["o1", "o2", "missing"])
    assert len(fills) == 1 and fills[0].qty == 1.0 and fills[0].price == 99.0
    assert b.get_order("missing") is None
