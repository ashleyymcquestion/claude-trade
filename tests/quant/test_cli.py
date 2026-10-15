import json

import pandas as pd
import pytest

from quantbot.__main__ import main
from quantbot.broker.live_gate import LiveTradingNotAllowed
from quantbot.store import Store

EXAMPLE = "configs/example.toml"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANTBOT_KILL_FILE", str(tmp_path / "KILL"))
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.delenv("QUANTBOT_LIVE", raising=False)
    monkeypatch.delenv("QUANTBOT_ALERT_WEBHOOK", raising=False)   # no webhook calls from tests


def _run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_status_on_empty_db(tmp_path, capsys):
    db = str(tmp_path / "q.db")
    code, out, _ = _run(capsys, "status", "--config", EXAMPLE, "--db", db)
    assert code == 0
    assert "kill switch: off" in out and "daily breaker: off" in out and "no equity snapshots" in out


def test_kill_and_unkill(tmp_path, capsys):
    db = str(tmp_path / "q.db")
    code, out, _ = _run(capsys, "kill", "--config", EXAMPLE, "--db", db, "--reason", "manual stop")
    assert code == 0 and "ENGAGED" in out
    code, out, _ = _run(capsys, "status", "--config", EXAMPLE, "--db", db)
    assert "kill switch: ENGAGED" in out and "manual stop" in out
    code, out, _ = _run(capsys, "unkill", "--config", EXAMPLE, "--db", db)
    assert code == 0 and "released" in out
    with Store(db) as s:
        kinds = [e["kind"] for e in s.events()]
    assert kinds[:2] == ["kill_switch_released", "kill_switch_engaged"]


def test_unkill_refuses_while_kill_file_exists(tmp_path, capsys):
    db = str(tmp_path / "q.db")
    (tmp_path / "KILL").write_text("x")
    code, _, err = _run(capsys, "unkill", "--config", EXAMPLE, "--db", db)
    assert code == 1 and "kill file" in err


def test_reset_drawdown(tmp_path, capsys):
    db = str(tmp_path / "q.db")
    code, out, _ = _run(capsys, "reset-drawdown", "--config", EXAMPLE, "--db", db)
    assert code == 0
    with Store(db) as s:
        assert s.get_flag("drawdown_reset_at") is not None


def test_verify_assets_without_credentials(tmp_path, capsys):
    code, _, err = _run(capsys, "verify-assets", "--config", EXAMPLE)
    assert code == 2
    assert "ALPACA_API_KEY" in err and "missing" in err


def test_live_gate_checked_at_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANTBOT_LIVE", "true")
    with pytest.raises(LiveTradingNotAllowed):
        main(["status", "--config", EXAMPLE, "--db", str(tmp_path / "q.db")])


def test_paper_once_sim_mode_on_tmp_bars(tmp_path, capsys):
    """Sim dry run against synthetic CSVs in a tmp dir (never data/bars/)."""
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    now = pd.Timestamp.now(tz="UTC").normalize()
    idx = pd.date_range(end=now - pd.Timedelta(days=1), periods=60, freq="D", tz="UTC")
    df = pd.DataFrame({"timestamp": [t.isoformat() for t in idx], "open": 100.0, "high": 101.0, "low": 99.0,
                       "close": 100.0, "volume": 1000.0})
    df.to_csv(bars_dir / "BTC-USD.csv", index=False)
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[[symbols]]\nsymbol = "BTC/USD"\nasset_class = "crypto"\n\n'
                   f'[data]\nbars_dir = "{bars_dir}"\n\n[execution]\nmode = "sim"\n')
    code, out, err = _run(capsys, "paper-once", "--config", str(cfg), "--db", str(tmp_path / "q.db"))
    assert code == 0
    assert "NO live prices" in err
    rep = json.loads(out)
    assert rep["checked"] == 1 and rep["aborted"] is False
    with Store(tmp_path / "q.db") as s:
        assert {h["component"] for h in s.heartbeats()} >= {"runner"}
