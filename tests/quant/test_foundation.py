import io
import json
import logging

import pytest
from pydantic import ValidationError

from quantbot.config import AppConfig, Credentials, RiskConfig, load_config
from quantbot.logging_setup import JsonFormatter, log_event, redact
from quantbot.models import AssetClass, AssetInfo
from quantbot.risk.sizing import size_position

EXAMPLE = "configs/example.toml"


def test_example_config_loads_and_validates():
    cfg = load_config(EXAMPLE)
    assert cfg.initial_capital == 1000.0
    assert [s.symbol for s in cfg.symbols] == ["BTC/USD", "ETH/USD", "NVDA", "AMD", "PLTR", "QQQ"]
    assert cfg.risk.max_gross_pct == 0.50
    assert cfg.execution.mode == "sim"


def test_live_mode_is_not_a_valid_config():
    raw = {"symbols": [{"symbol": "QQQ", "asset_class": "stock"}], "execution": {"mode": "live"}}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw)


@pytest.mark.parametrize("field", ["allow_margin", "allow_short", "allow_derivatives"])
def test_margin_short_derivatives_cannot_be_enabled(field):
    with pytest.raises(ValidationError):
        RiskConfig(**{field: True})


def test_inconsistent_risk_limits_rejected():
    with pytest.raises(ValidationError):
        RiskConfig(max_position_pct=0.6, max_gross_pct=0.5)
    with pytest.raises(ValidationError):
        RiskConfig(daily_loss_limit=0.05, weekly_loss_limit=0.04)


def test_unknown_config_keys_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"symbols": [{"symbol": "QQQ", "asset_class": "stock"}], "typo": 1})


def test_credentials_from_env_and_never_printed(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY1234567890AB")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "supersecretvalue123")
    creds = Credentials.from_env()
    assert "supersecret" not in repr(creds) and "PKTEST" not in str(creds)


def test_credentials_missing(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError):
        Credentials.from_env()


def test_logs_redact_secrets(monkeypatch):
    monkeypatch.setenv("ALPACA_SECRET_KEY", "supersecretvalue123")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    log = logging.getLogger("t-redact")
    log.handlers[:] = [handler]
    log.propagate = False
    log_event(log, logging.WARNING, "oops", detail="key supersecretvalue123 id PKABCDEFGHIJKLMNOPQR")
    line = json.loads(stream.getvalue())
    assert "supersecret" not in stream.getvalue() and "PKABCDEFGHIJ" not in stream.getvalue()
    assert line["msg"] == "oops" and line["level"] == "warning"
    assert redact("plain text") == "plain text"


STOCK = AssetInfo("NVDA", AssetClass.STOCK, tradable=True, fractionable=True, min_trade_increment=1e-9)
WHOLE = AssetInfo("XYZ", AssetClass.STOCK, tradable=True, fractionable=False)


def _size(**kw):
    args = dict(equity=1000.0, cash=1000.0, price=100.0, stop_price=95.0, asset=STOCK,
                current_gross=0.0, current_symbol_exposure=0.0, risk=RiskConfig(), one_way_cost_bps=0.0)
    args.update(kw)
    return size_position(**args)


def test_sizing_by_risk():
    d = _size()
    assert d.approved and d.qty == pytest.approx(1.0)          # $5 risk / $5 per share


def test_sizing_by_position_cap():
    d = _size(stop_price=99.5)                                  # risk allows 10 shares, cap 25% = $250
    assert d.approved and d.qty == pytest.approx(2.5) and "position cap" in d.reason


def test_sizing_by_gross_cap_and_cash():
    assert _size(stop_price=99.5, current_gross=400.0).qty == pytest.approx(1.0)
    assert _size(stop_price=99.5, cash=50.0).qty == pytest.approx(0.5)


def test_sizing_costs_reduce_size():
    assert _size(one_way_cost_bps=50).qty < _size().qty


def test_sizing_rejections():
    assert not _size(stop_price=None).approved
    assert not _size(stop_price=101.0).approved
    assert not _size(price=500.0, stop_price=490.0, asset=WHOLE).approved     # 1 share > risk, whole shares only
    assert not _size(asset=AssetInfo("X", AssetClass.STOCK, tradable=False, fractionable=True)).approved
    assert not _size(equity=100.0, cash=100.0, stop_price=1.0).approved          # ~$0.51 < $1 minimum


def test_sizing_rounds_up_to_minimum_within_risk_cap():
    small = RiskConfig(risk_per_trade=0.005, max_position_pct=0.5, max_gross_pct=1.0,
                       daily_loss_limit=0.03, weekly_loss_limit=0.06, max_drawdown=0.15,
                       min_order_risk_cap=0.04)
    # $2.10 equity, 7% stop: risk sizing gives $0.15, below the $1 minimum.
    base = dict(equity=2.10, cash=2.10, price=100.0, stop_price=93.0, asset=STOCK,
                current_gross=0.0, current_symbol_exposure=0.0, one_way_cost_bps=0.0)
    d = size_position(risk=small, **base)
    assert d.approved and d.qty * 100.0 == pytest.approx(1.0) and "rounded up" in d.reason
    # A 10% stop on $1 risks 4.8% of equity > 4% cap -> rejected.
    assert not size_position(risk=small, **{**base, "stop_price": 90.0}).approved
    # Cash below $1 -> rejected even within the cap.
    assert not size_position(risk=small, **{**base, "cash": 0.90}).approved
    # Disabled by default.
    assert not size_position(risk=RiskConfig(), **base).approved
