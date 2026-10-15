import numpy as np
import pandas as pd
import pytest

from trader.backtest import metrics, report, run_backtest
from trader.config import Config, load_config
from trader.data import align
from trader.live import daily_loss_breached, plan_orders
from trader.strategy import compute_scores, latest_target, target_weights

CFG = Config(stocks=("AAA", "BBB"), crypto=("BTC/USD",))


def synthetic_prices(days=1200, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01", periods=days, freq="D")
    drift = {"AAA": 0.0006, "BBB": -0.0004, "BTC/USD": 0.001}
    vol = {"AAA": 0.01, "BBB": 0.01, "BTC/USD": 0.035}
    return pd.DataFrame({s: 100 * np.exp(np.cumsum(rng.normal(drift[s], vol[s], days)))
                         for s in drift}, index=idx)


def test_repo_config_loads():
    cfg = load_config()
    assert "SPY" in cfg.stocks and "BTC/USD" in cfg.crypto
    assert cfg.max_gross <= 1.0


def test_scores_bounded_and_trend_detected():
    idx = pd.date_range("2020-01-01", periods=500, freq="D")
    prices = pd.DataFrame({"AAA": np.linspace(100, 200, 500), "BBB": np.linspace(200, 100, 500),
                           "BTC/USD": np.linspace(100, 300, 500)}, index=idx)
    scores = compute_scores(prices, CFG).dropna()
    assert ((scores >= 0) & (scores <= 1)).all().all()
    assert scores["AAA"].iloc[-1] == 1.0 and scores["BBB"].iloc[-1] == 0.0


def test_weights_respect_risk_limits():
    w = target_weights(synthetic_prices(), CFG)
    assert (w >= 0).all().all()
    assert (w.sum(axis=1) <= CFG.max_gross + 1e-9).all()
    assert (w <= CFG.max_weight + 1e-9).all().all()
    assert (w["BTC/USD"] <= CFG.max_crypto + 1e-9).all()
    assert (w.iloc[: CFG.sma_slow - 1] == 0).all().all()  # cash during warm-up


def test_no_lookahead():
    prices = synthetic_prices()
    full = target_weights(prices, CFG)
    cut = target_weights(prices.iloc[:900], CFG)
    pd.testing.assert_frame_equal(full.iloc[:900], cut)


def test_latest_target_matches_full_history():
    prices = synthetic_prices()
    pd.testing.assert_series_equal(latest_target(prices, CFG), target_weights(prices, CFG).iloc[-1],
                                   check_names=False, atol=1e-9)


def test_backtest_costs_reduce_returns():
    prices = synthetic_prices()
    cheap = run_backtest(prices, Config(**{**CFG.__dict__, "stock_cost_bps": 0, "crypto_cost_bps": 0}))
    pricey = run_backtest(prices, Config(**{**CFG.__dict__, "stock_cost_bps": 50, "crypto_cost_bps": 100}))
    assert pricey.equity.iloc[-1] < cheap.equity.iloc[-1]
    assert "Sharpe" in report(cheap, CFG)


def test_metrics_drawdown():
    eq = pd.Series([1.0, 1.2, 0.9, 1.1], index=pd.date_range("2020-01-01", periods=4, freq="D"))
    assert metrics(eq)["Max drawdown"] == pytest.approx(-0.25)


def test_align_carries_stocks_over_weekend():
    stocks = pd.DataFrame({"AAA": [1.0, 2.0]}, index=pd.to_datetime(["2024-01-05", "2024-01-08"]))  # Fri, Mon
    crypto = pd.DataFrame({"BTC/USD": [10.0, 11, 12, 13]}, index=pd.date_range("2024-01-05", periods=4))
    out = align(stocks, crypto)
    assert list(out["AAA"]) == [1.0, 1.0, 1.0, 2.0]


def test_plan_orders_sells_first_and_respects_cash():
    target = pd.Series({"AAA": 0.5, "BBB": 0.0, "BTC/USD": 0.3})
    holdings = {"BBB": 2000.0}
    orders, _ = plan_orders(target, holdings, equity=10000, cash=1000, cfg=CFG, market_open=True)
    assert orders[0].symbol == "BBB" and orders[0].side == "sell" and orders[0].close_all
    buys = [o for o in orders if o.side == "buy"]
    assert sum(o.notional for o in buys) <= 1000 + 0.98 * 2000 + 0.01


def test_plan_orders_stocks_wait_for_market_but_crypto_trades():
    target = pd.Series({"AAA": 0.5, "BBB": 0.0, "BTC/USD": 0.3})
    orders, notes = plan_orders(target, {}, equity=10000, cash=10000, cfg=CFG, market_open=False)
    assert [o.symbol for o in orders] == ["BTC/USD"]
    assert any("market closed" in n for n in notes)


def test_plan_orders_ignores_small_changes_and_busy_symbols():
    target = pd.Series({"AAA": 0.31, "BBB": 0.0, "BTC/USD": 0.3})
    holdings = {"AAA": 3000.0}
    orders, _ = plan_orders(target, holdings, equity=10000, cash=7000, cfg=CFG,
                            market_open=True, busy={"BTC/USD"})
    assert orders == []


def test_daily_loss_limit():
    assert daily_loss_breached(9600, 10000, CFG)
    assert not daily_loss_breached(9800, 10000, CFG)


class FakeClient:
    """Stands in for alpaca TradingClient so the full run() path is exercised."""

    def __init__(self, equity=10000.0, last_equity=10000.0):
        from types import SimpleNamespace as NS
        self.NS = NS
        self.account = NS(equity=str(equity), last_equity=str(last_equity), cash=str(equity),
                          non_marginable_buying_power=str(equity), trading_blocked=False, account_blocked=False)
        self.submitted, self.closed = [], []

    def get_account(self): return self.account
    def get_all_positions(self): return []
    def get_orders(self, req): return []
    def get_clock(self): return self.NS(is_open=False)
    def get_asset(self, symbol): return self.NS(fractionable=True, min_trade_increment="0.000001")
    def submit_order(self, req):
        self.submitted.append(req)
        return self.NS(id="fake-id")
    def close_position(self, symbol): self.closed.append(symbol)
    def cancel_orders(self): pass


def make_trader(monkeypatch, client):
    import trader.live as live
    t = live.PaperTrader.__new__(live.PaperTrader)
    t.cfg, t.api_key, t.secret_key, t.client = CFG, "k", "s", client
    monkeypatch.setattr(live, "fetch_recent", lambda cfg, k, s: synthetic_prices())
    return t


def test_run_places_crypto_order_when_market_closed(monkeypatch):
    client = FakeClient()
    summary = make_trader(monkeypatch, client).run()
    assert summary["status"] == "ok" and not summary["errors"]
    assert [r.symbol for r in client.submitted] == [o["symbol"] for o in summary["orders"]]
    assert all(r.symbol == "BTC/USD" for r in client.submitted)


def test_run_dry_run_and_kill_switch_place_nothing(monkeypatch):
    client = FakeClient()
    make_trader(monkeypatch, client).run(dry_run=True)
    monkeypatch.setenv("KILL_SWITCH", "1")
    assert "kill switch" in make_trader(monkeypatch, client).run()["status"]
    assert client.submitted == []


def test_run_halts_on_daily_loss(monkeypatch):
    client = FakeClient(equity=9000, last_equity=10000)
    assert "daily loss" in make_trader(monkeypatch, client).run()["status"]
    assert client.submitted == []
