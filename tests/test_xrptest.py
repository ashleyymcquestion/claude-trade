from datetime import date

import numpy as np
import pandas as pd
import pytest

from trader import xrptest


@pytest.fixture
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(xrptest, "PAPER", tmp_path)
    return tmp_path


def _prices(xrp_trend: float) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=500, freq="D")
    rng = np.random.default_rng(0)
    cols = {c: 100 * np.exp(np.cumsum(0.001 + 0.01 * rng.standard_normal(500))) for c in ("BTC", "ETH", "SOL")}
    cols["XRP"] = np.exp(np.cumsum(xrp_trend + 0.01 * rng.standard_normal(500)))
    return pd.DataFrame(cols, index=idx)


def test_paper_buys_xrp_in_an_uptrend_and_logs(paper):
    row = xrptest.paper_step(1.0, date(2026, 10, 23), _prices(0.004))
    assert row["target_weight"] > 0.05 and row["traded_usd"] > 0
    assert row["paper_value"] == pytest.approx(2.0 - row["traded_usd"] * xrptest.COST)
    assert row["hold_xrp_value"] == pytest.approx(2.0)
    assert (paper / "log.csv").read_text().count("\n") == 2


def test_paper_holds_cash_in_a_downtrend_and_runs_once_a_day(paper):
    row = xrptest.paper_step(1.0, date(2026, 10, 23), _prices(-0.004))
    assert row["target_weight"] == 0 and row["traded_usd"] == 0 and row["paper_value"] == 2.0
    again = xrptest.paper_step(0.5, date(2026, 10, 23), _prices(-0.004))
    assert again["already_ran"] is True
    nxt = xrptest.paper_step(0.5, date(2026, 10, 24), _prices(-0.004))
    assert nxt["paper_value"] == 2.0 and nxt["hold_xrp_value"] == pytest.approx(1.0)
