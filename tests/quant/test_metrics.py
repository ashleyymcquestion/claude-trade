"""Metrics on hand-built equity curves and trade lists."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from quantbot.backtest.engine import TRADE_COLUMNS, BacktestResult
from quantbot.backtest.metrics import (
    compute_metrics, infer_periods_per_year, max_drawdown, periods_per_year)
from quantbot.models import AssetClass


def result(equity, capital=100.0, pnls=(), exposure=None, costs=0.0) -> BacktestResult:
    idx = pd.date_range("2024-01-01", periods=len(equity), freq="B", tz="UTC")
    rows = [{c: None for c in TRADE_COLUMNS} | {"pnl": p, "return_pct": p / 100.0, "bars_held": 2}
            for p in pnls]
    trades = pd.DataFrame(rows, columns=list(TRADE_COLUMNS))
    exp = pd.Series(exposure if exposure is not None else [0.0] * len(equity), index=idx)
    return BacktestResult(equity=pd.Series(equity, index=idx, dtype=float), trades=trades,
                          costs_paid=costs, exposure=exp, meta={"capital": capital})


def test_periods_per_year():
    assert periods_per_year(AssetClass.STOCK) == 252
    assert periods_per_year("crypto") == 365
    assert infer_periods_per_year(pd.date_range("2024-01-01", periods=10, freq="B", tz="UTC")) == 252
    assert infer_periods_per_year(pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")) == 365


def test_max_drawdown_hand_built():
    m = compute_metrics(result([110, 99, 120, 108]), 252)
    assert m["max_drawdown"] == pytest.approx(99 / 110 - 1)
    # the starting capital counts as a peak
    assert max_drawdown(pd.Series([90.0, 95.0]), capital=100.0) == pytest.approx(-0.10)
    assert compute_metrics(result([101, 102, 103]), 252)["max_drawdown"] == 0.0


def test_sharpe_sign_and_total_return():
    rng = np.random.default_rng(0)
    up = 100 * np.cumprod(1 + 0.002 + rng.normal(0, 0.001, 100))
    down = 100 * np.cumprod(1 - 0.002 + rng.normal(0, 0.001, 100))
    mu, md = compute_metrics(result(up), 252), compute_metrics(result(down), 252)
    assert mu["sharpe"] > 0 and mu["sortino"] > 0
    assert md["sharpe"] < 0 and md["sortino"] < 0
    assert mu["total_return"] == pytest.approx(up[-1] / 100 - 1)


def test_cagr_and_exposure_adjusted_return():
    eq = np.linspace(100, 110, 252)
    m = compute_metrics(result(eq, exposure=[0.5] * 252), 252)
    assert m["annualized_return"] == pytest.approx(0.10)
    assert m["exposure"] == pytest.approx(0.5)
    assert m["exposure_adjusted_return"] == pytest.approx(0.20)


def test_trade_statistics():
    m = compute_metrics(result([100, 120], pnls=[10, -5, 20, -5], costs=3.0), 252)
    assert m["num_trades"] == 4
    assert m["win_rate"] == pytest.approx(0.5)
    assert m["profit_factor"] == pytest.approx(30 / 10)
    assert m["expectancy"] == pytest.approx(5.0)
    assert m["expectancy_pct"] == pytest.approx(0.05)
    assert m["total_costs"] == 3.0


def test_profit_factor_none_without_losers():
    m = compute_metrics(result([100, 120], pnls=[10, 5]), 252)
    assert m["profit_factor"] is None and m["win_rate"] == 1.0


def test_zero_trades_and_flat_equity_do_not_crash():
    m = compute_metrics(result([100.0] * 20), 252)
    assert m["num_trades"] == 0 and m["win_rate"] is None and m["profit_factor"] is None
    assert m["sharpe"] is None and m["sortino"] is None and m["exposure_adjusted_return"] is None
    assert m["total_return"] == 0.0 and m["max_drawdown"] == 0.0
    json.dumps(m, allow_nan=False)   # JSON-safe
