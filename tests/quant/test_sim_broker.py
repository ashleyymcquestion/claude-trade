import pytest

from quantbot.broker.base import OrderRejected
from quantbot.broker.sim import SimulatedBroker
from quantbot.config import CostConfig
from quantbot.models import AssetClass, AssetInfo, OrderRequest, OrderStatus, Side

COSTS = CostConfig(stock_commission_bps=1.0, stock_half_spread_bps=2.0, stock_slippage_bps=3.0,
                   crypto_fee_bps=25.0, crypto_half_spread_bps=5.0, crypto_slippage_bps=5.0)


def _broker(cash=10_000.0, assets=None):
    b = SimulatedBroker(cash, COSTS, assets)
    b.set_price("NVDA", 100.0)
    b.set_price("BTC/USD", 50_000.0)
    return b


def _req(cid="c1", sym="NVDA", side=Side.BUY, qty=10.0):
    return OrderRequest(cid, sym, side, qty, strategy="t")


def test_market_buy_fills_with_costs_and_fees():
    b = _broker()
    o = b.submit_order(_req())
    assert o.status is OrderStatus.FILLED and o.filled_qty == 10
    px = 100.0 * (1 + 5 / 10_000)                     # half spread + slippage on price
    assert o.filled_avg_price == pytest.approx(px)
    fee = 10 * px * 1 / 10_000                         # commission as fee
    assert b.cash == pytest.approx(10_000 - 10 * px - fee)
    (f,) = b.fills_since(["c1"])
    assert f.fee == pytest.approx(fee) and f.price == pytest.approx(px)
    # total one-way cost equals CostConfig.one_way_bps
    assert (10 * px + fee) / 1000 - 1 == pytest.approx(COSTS.one_way_bps(AssetClass.STOCK) / 10_000, rel=1e-3)
    acct = b.get_account()
    (p,) = acct.positions
    assert p.symbol == "NVDA" and p.qty == 10 and p.market_price == 100.0
    assert acct.equity == pytest.approx(b.cash + 1000.0)


def test_crypto_sell_round_trip_costs():
    b = _broker()
    b.submit_order(_req("b", "BTC/USD", Side.BUY, 0.01))
    o = b.submit_order(_req("s", "BTC/USD", Side.SELL, 0.01))
    assert o.filled_avg_price == pytest.approx(50_000 * (1 - 10 / 10_000))
    assert b.get_account().positions == ()
    # lost exactly two one-way costs (35 bps each, approximately)
    assert 10_000 - b.cash == pytest.approx(500 * 2 * 35 / 10_000, rel=0.01)


def test_submit_is_idempotent_on_client_order_id():
    b = _broker()
    o1 = b.submit_order(_req())
    o2 = b.submit_order(_req(qty=99))
    assert o2.client_order_id == o1.client_order_id and o2.qty == 10
    assert len(b.fills_since(["c1"])) == 1
    assert b.get_account().positions[0].qty == 10


def test_reject_next_and_rejected_order_is_recorded():
    b = _broker()
    b.reject_next("insufficient buying power (test)")
    with pytest.raises(OrderRejected, match="insufficient buying power"):
        b.submit_order(_req())
    assert b.get_order("c1").status is OrderStatus.REJECTED
    assert b.get_account().positions == ()
    # fault is one-shot
    assert b.submit_order(_req("c2")).status is OrderStatus.FILLED


def test_no_shorting_and_no_margin():
    b = _broker(cash=500.0)
    with pytest.raises(OrderRejected, match="no shorting"):
        b.submit_order(_req("s", side=Side.SELL, qty=1))
    with pytest.raises(OrderRejected, match="insufficient cash"):
        b.submit_order(_req("big", qty=10))
    b.submit_order(_req("ok", qty=2))
    with pytest.raises(OrderRejected, match="exceeds position"):
        b.submit_order(_req("s2", side=Side.SELL, qty=3))


def test_partial_fill_then_fill_open():
    b = _broker()
    b.partial_fill_next(0.4)
    o = b.submit_order(_req())
    assert o.status is OrderStatus.PARTIALLY_FILLED and o.filled_qty == pytest.approx(4)
    assert [x.client_order_id for x in b.list_open_orders()] == ["c1"]
    b.fill_open("c1", price=110.0)
    o = b.get_order("c1")
    assert o.status is OrderStatus.FILLED and o.filled_qty == pytest.approx(10)
    fills = b.fills_since(["c1"])
    assert len(fills) == 2 and fills[0].ts < fills[1].ts
    assert o.filled_avg_price == pytest.approx((4 * 100 + 6 * 110) / 10 * (1 + 5 / 10_000))


def test_keep_open_and_cancel():
    b = _broker()
    b.keep_open_next()
    o = b.submit_order(_req())
    assert o.status is OrderStatus.NEW and o.filled_qty == 0
    b.cancel_order("c1")
    assert b.get_order("c1").status is OrderStatus.CANCELED
    assert b.cash == 10_000.0


def test_fail_next_submit_is_timeout_after_acceptance():
    b = _broker()
    b.fail_next_submit(TimeoutError("simulated timeout"))
    with pytest.raises(TimeoutError):
        b.submit_order(_req())
    # the order was accepted and filled before the "timeout"; a retry returns it, no second order
    o = b.submit_order(_req())
    assert o.status is OrderStatus.FILLED
    assert len(b.fills_since(["c1"])) == 1 and b.get_account().positions[0].qty == 10


def test_fractional_and_market_hours():
    assets = {"AMD": AssetInfo("AMD", AssetClass.STOCK, tradable=True, fractionable=False)}
    b = _broker(assets=assets)
    b.set_price("AMD", 50.0)
    with pytest.raises(OrderRejected, match="fractionable"):
        b.submit_order(_req("f", "AMD", qty=1.5))
    b.set_market_open(AssetClass.STOCK, False)
    assert not b.is_market_open(AssetClass.STOCK) and b.is_market_open(AssetClass.CRYPTO)
    with pytest.raises(OrderRejected, match="market closed"):
        b.submit_order(_req("g", "AMD", qty=1))
    assert b.submit_order(_req("h", "BTC/USD", qty=0.001)).status is OrderStatus.FILLED
