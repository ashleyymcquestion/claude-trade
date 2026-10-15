import base64
import math
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshibot.bot import Bot
from kalshibot.client import KalshiClient, Market, cents, sign
from kalshibot.config import KalshiConfig, RiskConfig, RunConfig, StrategyConfig, credentials_from_env, load_config
from kalshibot.model import decide, effective_seconds, prob_up, taker_fee_cents, vol_per_minute
from kalshibot.pricefeed import Candle
from kalshibot.risk import LIVE_CONFIRM_ENV, LIVE_CONFIRM_VALUE, LiveTradingNotAllowed, assert_live_allowed, block_reason, size
from kalshibot.store import Store

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTBOT_KILL_FILE", str(tmp_path / "KILL"))
    monkeypatch.delenv("KILL_SWITCH", raising=False)
    monkeypatch.delenv(LIVE_CONFIRM_ENV, raising=False)


def _market(seconds_left=300, yes=50, no=52, strike=100_000.0, result="", ticker="KXBTC15M-T1"):
    close = NOW + timedelta(seconds=seconds_left)
    return Market(ticker, close - timedelta(minutes=15), close, "active", yes, no, strike, result)


def _candles(n=61, start=100_000.0, step=0.0):
    t0 = NOW - timedelta(minutes=n)
    # alternating moves give a known, non-zero volatility
    return [Candle(t0 + timedelta(minutes=i), start * math.exp(step * (1 if i % 2 else -1)))
            for i in range(n)]


# ---------------------------------------------------------------- client
def test_signature_verifies_and_ignores_query_string():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sig = sign(key, "1700000000000", "get", "/trade-api/v2/portfolio/balance?x=1")
    key.public_key().verify(
        base64.b64decode(sig), b"1700000000000GET/trade-api/v2/portfolio/balance",
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256())


def test_prices_read_from_cents_or_dollars():
    assert cents({"yes_ask": 57}, "yes_ask") == 57
    assert cents({"yes_ask_dollars": "0.5700"}, "yes_ask") == 57
    assert cents({}, "yes_ask") is None
    m = Market.from_api({"ticker": "T", "open_time": "2026-10-15T13:45:00Z", "close_time": "2026-10-15T14:00:00Z",
                         "status": "active", "yes_ask_dollars": "0.40", "no_ask": 62, "floor_strike": 101234.5})
    assert (m.yes_ask, m.no_ask, m.strike, m.close_time) == (40, 62, 101234.5, NOW)


def test_credentials_are_per_environment(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    (tmp_path / "k.pem").write_bytes(pem)
    monkeypatch.setenv("KALSHI_DEMO_KEY_ID", "demo-id")
    monkeypatch.setenv("KALSHI_DEMO_PRIVATE_KEY_PATH", str(tmp_path / "k.pem"))
    monkeypatch.delenv("KALSHI_PROD_KEY_ID", raising=False)
    creds = credentials_from_env("demo")
    assert creds.key_id == "demo-id" and "PRIVATE" not in repr(creds)
    assert credentials_from_env("prod") is None
    assert KalshiClient("https://demo-api.kalshi.co/trade-api/v2", creds).authenticated


# ---------------------------------------------------------------- model
def test_fee_matches_kalshi_formula():
    assert taker_fee_cents(1, 50) == 2          # 0.07 * 0.25 = 1.75c -> 2c
    assert taker_fee_cents(100, 50) == 175
    assert taker_fee_cents(10, 10) == 7         # 0.07*10*0.09 = 6.3c -> 7c


def test_probability_behaves():
    assert prob_up(100_000, 100_000, 0.001, 300) == pytest.approx(0.5)
    assert prob_up(100_100, 100_000, 0.001, 300) > 0.5
    assert prob_up(100_100, 100_000, 0.001, 120) > prob_up(100_100, 100_000, 0.001, 600)
    assert effective_seconds(120) == pytest.approx(80)
    assert effective_seconds(0) == 0


def test_vol_has_floor():
    assert vol_per_minute(_candles(step=0.0), 0.0005) == 0.0005
    assert vol_per_minute(_candles(step=0.001), 0.0005) == pytest.approx(0.002, rel=0.01)


def test_decide_window_and_edges():
    cfg = StrategyConfig()
    flat = _candles(step=0.0002)  # below the vol floor
    assert decide(_market(seconds_left=800), 100_000, flat, NOW, cfg).reason.startswith("too early")
    assert decide(_market(seconds_left=30), 100_000, flat, NOW, cfg).reason.startswith("too late")
    # BTC exactly at target: fair 50/50, asks of 50/52 leave no edge after fees
    assert decide(_market(), 100_000, flat, NOW, cfg).action == "skip"
    # BTC 0.05% above target with ~3 min left: up is likely, and YES at 50c is cheap
    d = decide(_market(seconds_left=180), 100_050, flat, NOW, cfg)
    assert d.action == "buy" and d.side == "yes" and d.price_cents == 50
    assert cfg.min_edge <= d.edge <= cfg.max_edge
    # same move but the market already agrees: no trade
    assert decide(_market(seconds_left=180, yes=75, no=27), 100_050, flat, NOW, cfg).action == "skip"
    # implausibly large edge is distrusted
    assert "too large" in decide(_market(seconds_left=180), 100_500, flat, NOW, cfg).reason
    # BTC below target: buys NO
    d = decide(_market(seconds_left=180, yes=50, no=50), 99_950, flat, NOW, cfg)
    assert d.action == "buy" and d.side == "no"


def test_missing_strike_is_skipped_unless_estimates_allowed():
    flat = _candles(step=0.0002)
    m = _market(seconds_left=180, strike=None)
    assert decide(m, 100_050, flat, NOW, StrategyConfig()).reason == "no target price available"
    d = decide(m, 100_050, flat, NOW, StrategyConfig(allow_estimated_strike=True))
    # the candle that ends at the market open supplies the estimate
    assert d.strike_source == "estimated" and d.strike == pytest.approx(100_000, rel=1e-3)


# ---------------------------------------------------------------- risk and config
def test_live_gate_needs_all_three(monkeypatch):
    demo = KalshiConfig()
    assert_live_allowed(demo, live_flag=False)
    prod = KalshiConfig(run=RunConfig(environment="prod", allow_live=True))
    with pytest.raises(LiveTradingNotAllowed):
        assert_live_allowed(prod, live_flag=True)
    monkeypatch.setenv(LIVE_CONFIRM_ENV, LIVE_CONFIRM_VALUE)
    with pytest.raises(LiveTradingNotAllowed):
        assert_live_allowed(prod, live_flag=False)
    assert_live_allowed(prod, live_flag=True)
    with pytest.raises(ValueError):
        RunConfig(environment="prod")


def test_kill_switch_and_daily_limits(tmp_path, monkeypatch):
    cfg = KalshiConfig()
    ok = {"trades": 0, "realized_cents": 0, "at_risk_cents": 0}
    assert block_reason(cfg, ok) is None
    (tmp_path / "KILL").touch()
    assert "kill file" in block_reason(cfg, ok)
    (tmp_path / "KILL").unlink()
    monkeypatch.setenv("KILL_SWITCH", "1")
    assert block_reason(cfg, ok) == "KILL_SWITCH is set"
    monkeypatch.delenv("KILL_SWITCH")
    assert "max trades" in block_reason(cfg, {**ok, "trades": 20})
    assert "daily loss" in block_reason(cfg, {**ok, "realized_cents": -800, "at_risk_cents": 200})


def test_sizing_respects_caps():
    cfg = KalshiConfig(risk=RiskConfig(max_contracts_per_trade=5, max_cost_per_trade_usd=2.0,
                                       daily_loss_limit_usd=10.0))
    ok = {"trades": 0, "realized_cents": 0, "at_risk_cents": 0}
    assert size(cfg, 50, ok, None) == 3          # 4 would cost 200 + 7 fee > 200
    assert size(cfg, 50, ok, 60) == 1            # balance
    assert size(cfg, 50, {**ok, "realized_cents": -950}, None) == 0


def test_shipped_config_loads():
    cfg = load_config("configs/kalshi.toml")
    assert cfg.run.environment == "demo" and not cfg.run.allow_live


# ---------------------------------------------------------------- bot
class FakeClient:
    def __init__(self, markets):
        self.markets = {m.ticker: m for m in markets}
        self.orders = []

    def open_markets(self, series):
        return [m for m in self.markets.values() if not m.result]

    def market(self, ticker):
        return self.markets[ticker]

    def balance_cents(self):
        return 10_000

    def buy(self, ticker, side, count, price, tif):
        self.orders.append((ticker, side, count, price, tif))
        return {"order_id": "o1", "status": "executed", "fill_count": count}

    def order(self, oid):
        return {}


class FakeFeed:
    def __init__(self, spot):
        self._spot = spot

    def spot(self):
        return self._spot

    def minute_candles(self, minutes):
        return _candles(step=0.0002)


def _bot(markets, spot, dry_run=False, clock=lambda: NOW):
    cfg = KalshiConfig(run=RunConfig(dry_run=dry_run))
    return Bot(cfg, FakeClient(markets), FakeFeed(spot), Store(":memory:"), clock=clock)


def test_bot_buys_once_per_market_then_settles():
    m = _market(seconds_left=180)
    bot = _bot([m], 100_050)
    r = bot.tick()
    assert r["status"] == "bought" and r["side"] == "yes" and r["count"] == 5
    assert bot.client.orders == [(m.ticker, "yes", 5, 50, "immediate_or_cancel")]
    assert bot.tick()["status"] == "idle"          # no second trade in the same market
    assert len(bot.client.orders) == 1

    # after the close, the market resolves YES: 5 * (100 - 50) - fee 9c
    bot.client.markets[m.ticker] = Market(**{**m.__dict__, "result": "yes"})
    bot.clock = lambda: NOW + timedelta(minutes=5)
    r = bot.tick()
    assert r["settled"] == [{"ticker": m.ticker, "side": "yes", "result": "yes", "pnl_cents": 241}]
    s = bot.store.summary()
    assert s["settled"] == 1 and s["pnl_usd"] == pytest.approx(2.41)


def test_dry_run_sends_no_orders_but_records_trade():
    bot = _bot([_market(seconds_left=180)], 100_050, dry_run=True)
    assert bot.tick()["status"] == "bought"
    assert bot.client.orders == []
    assert bot.store.recent_trades()[0]["dry_run"] == 1


def test_kill_switch_stops_bot(tmp_path):
    (tmp_path / "KILL").touch()
    bot = _bot([_market(seconds_left=180)], 100_050)
    assert bot.tick()["status"] == "blocked"
    assert bot.client.orders == []
