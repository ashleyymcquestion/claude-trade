"""Run the Streamlit app headlessly (streamlit.testing.v1.AppTest) against a tmp database."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from quantbot.models import AccountSnapshot, Fill, Order, OrderStatus, Position, Side  # noqa: E402
from quantbot.risk.killswitch import KillSwitch  # noqa: E402
from quantbot.store import Store  # noqa: E402

APP = Path(__file__).resolve().parents[2] / "quantbot" / "dashboard" / "app.py"
TIMEOUT = 60


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "state" / "dash.sqlite3"
    cfg = tmp_path / "cfg.toml"
    cfg.write_text(
        'initial_capital = 1000.0\n'
        '[[symbols]]\nsymbol = "NVDA"\nasset_class = "stock"\n'
        '[[symbols]]\nsymbol = "BTC/USD"\nasset_class = "crypto"\n'
        f'[execution]\ndb_path = "{db.as_posix()}"\n'
    )
    monkeypatch.setenv("QUANTBOT_KILL_FILE", str(tmp_path / "KILL"))
    monkeypatch.setattr(sys, "argv", ["app.py", "--config", str(cfg), "--reports-dir", str(tmp_path / "reports")])
    return db


def _run() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _populate(db: Path) -> None:
    s = Store(db)
    now = datetime.now(UTC)
    s.record_equity(AccountSnapshot(now - timedelta(days=2), 1000.0, 1000.0))
    s.record_equity(AccountSnapshot(now - timedelta(days=1), 1050.0, 900.0))
    s.record_equity(AccountSnapshot(now, 1010.0, 800.0, (Position("NVDA", 2.0, 100.0, 105.0, "trend", 95.0),)))
    s.set_position_meta("NVDA", "trend", 95.0, now - timedelta(days=2), 106.0)
    s.record_order(Order("trend-NVDA-x-buy", "NVDA", Side.BUY, 2.0, OrderStatus.FILLED, filled_qty=2.0,
                         filled_avg_price=100.0, strategy="trend"))
    s.record_order(Order("breakout-BTC-USD-x-buy", "BTC/USD", Side.BUY, 0.001, OrderStatus.NEW, strategy="breakout"))
    s.record_fill(Fill("trend-NVDA-x-buy", "NVDA", Side.BUY, 2.0, 100.0, 0.0, now - timedelta(days=2)))
    s.record_realized_pnl(now - timedelta(days=1), "AMD", "mean_reversion", -4.0)
    s.record_regime("NVDA", now - timedelta(days=1), "bull_quiet")
    s.record_event("warning", "data_stale", "AMD bars stale")
    s.heartbeat("runner", "ok", "cycle done")
    s.set_flag("breaker_daily", now.date().isoformat(), "daily breaker tripped (test)")
    s.close()


def _texts(at: AppTest) -> str:
    parts = []
    for coll in (at.markdown, at.caption, at.info, at.warning, at.error, at.success, at.title, at.subheader):
        parts.extend(str(e.value) for e in coll)
    return "\n".join(parts)


def test_renders_on_empty_db(env):
    at = _run()
    txt = _texts(at)
    assert "No data yet" in txt
    assert "No open positions." in txt and "No events yet." in txt
    assert [m.value for m in at.metric][:2] == ["—", "—"]
    assert any(b.label == "EMERGENCY STOP" for b in at.button)


def test_renders_on_populated_db(env):
    _populate(env)
    at = _run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Equity"] == "$1,010.00"
    assert metrics["Cash"] == "$800.00"
    assert metrics["Total P&L"] == "+10.00"
    assert metrics["Max drawdown"] == f"{1 - 1010 / 1050:.2%}"
    assert len(at.dataframe) >= 5
    txt = _texts(at)
    assert "breaker_daily active" in txt
    assert "No backtest report found" in txt


def test_emergency_stop_requires_confirmation_then_engages(env):
    at = _run()
    at.button(key="estop_button").click().run()
    assert not at.exception
    assert any("confirmation" in e.value for e in at.error)
    s = Store(env)
    assert not KillSwitch(s).is_engaged()
    s.close()

    at.checkbox(key="estop_confirm").check().run()
    at.button(key="estop_button").click().run()
    assert not at.exception
    s = Store(env)
    st = KillSwitch(s).status()
    assert st["engaged"] and st["by"] == "dashboard" and st["reason"] == "dashboard emergency stop"
    s.close()
    errors = [str(e.value) for e in at.error]
    assert any("Kill switch engaged by dashboard" in e for e in errors)      # status bar after rerun
    assert any("ENGAGED from the dashboard" in e for e in errors)            # flash message


def test_release_requires_typing_release(env):
    s = Store(env)
    KillSwitch(s).engage("pre-engaged", by="pytest")
    s.close()
    at = _run()
    at.text_input(key="release_text").input("release please").run()
    at.button(key="release_button").click().run()
    s = Store(env)
    assert KillSwitch(s).is_engaged()
    s.close()
    at.text_input(key="release_text").input("RELEASE").run()
    at.button(key="release_button").click().run()
    assert not at.exception
    s = Store(env)
    assert not KillSwitch(s).is_engaged()
    s.close()


def test_app_does_not_import_broker_sdk():
    src = APP.read_text()
    assert "import alpaca" not in src and "from alpaca" not in src
