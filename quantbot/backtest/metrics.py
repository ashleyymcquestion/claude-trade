"""Performance metrics for a :class:`~quantbot.backtest.engine.BacktestResult`.

Conventions: risk-free rate 0; per-bar returns are computed from the equity curve with the
starting capital prepended (so the first bar's move counts); annualisation uses
``periods_per_year`` (252 for stock sessions, 365 for crypto calendar days). Undefined values
(no trades, zero volatility, no losing trades for profit factor) are ``None``, never inf/NaN, so
the dict is JSON-safe.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ..models import AssetClass

if TYPE_CHECKING:  # pragma: no cover
    from .engine import BacktestResult


def periods_per_year(asset_class: AssetClass | str) -> int:
    """252 trading sessions for stocks, 365 calendar days for crypto."""
    return 365 if AssetClass(asset_class) is AssetClass.CRYPTO else 252


def infer_periods_per_year(index: pd.DatetimeIndex) -> int:
    """365 if the series contains weekend bars (true 24/7 crypto data), else 252.

    Use this when a crypto symbol is researched through an exchange-traded proxy, whose bars
    follow stock sessions."""
    if len(index) and bool((pd.DatetimeIndex(index).dayofweek >= 5).any()):
        return 365
    return 252


def _f(x: Any) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def _ts(x: Any) -> str | None:
    return None if x is None else pd.Timestamp(x).isoformat()


def bar_returns(equity: pd.Series, capital: float) -> pd.Series:
    eq = pd.concat([pd.Series([float(capital)]), equity.reset_index(drop=True).astype(float)],
                   ignore_index=True)
    return eq.pct_change().iloc[1:].reset_index(drop=True)


def max_drawdown(equity: pd.Series, capital: float | None = None) -> float:
    """Most negative peak-to-trough fraction (e.g. -0.12). 0.0 if equity never falls."""
    eq = equity.astype(float).to_numpy()
    if capital is not None:
        eq = np.concatenate([[float(capital)], eq])
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.min(eq / peak - 1.0))


def compute_metrics(result: "BacktestResult", periods_per_year: int) -> dict:
    capital = result.initial_capital
    equity = result.equity.dropna()
    trades = result.trades
    n = len(equity)
    out: dict[str, Any] = {
        "start": _ts(equity.index[0]) if n else None,
        "end": _ts(equity.index[-1]) if n else None,
        "bars": n,
        "num_trades": int(len(trades)),
        "total_costs": float(result.costs_paid),
        "open_positions": len(result.open_positions),
    }
    final = float(equity.iloc[-1]) if n else capital
    total_return = final / capital - 1
    out["final_equity"] = final
    out["total_return"] = total_return

    years = n / periods_per_year if n else 0.0
    out["annualized_return"] = (
        _f((final / capital) ** (1 / years) - 1) if years > 0 and final > 0 else None)

    r = bar_returns(equity, capital) if n else pd.Series(dtype=float)
    sd = float(r.std(ddof=1)) if len(r) > 1 else float("nan")
    ann = math.sqrt(periods_per_year)
    out["annualized_volatility"] = _f(sd * ann) if math.isfinite(sd) else None
    out["sharpe"] = _f(r.mean() / sd * ann) if math.isfinite(sd) and sd > 0 else None
    downside = float(np.sqrt(np.mean(np.minimum(r.to_numpy(), 0.0) ** 2))) if len(r) else 0.0
    out["sortino"] = _f(r.mean() / downside * ann) if downside > 0 else None
    out["max_drawdown"] = max_drawdown(equity, capital) if n else 0.0

    if len(trades):
        pnl = trades["pnl"].astype(float)
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        out["win_rate"] = float(len(wins) / len(pnl))
        out["profit_factor"] = _f(wins.sum() / -losses.sum()) if len(losses) else None
        out["expectancy"] = float(pnl.mean())
        out["expectancy_pct"] = float(trades["return_pct"].astype(float).mean())
        out["avg_bars_held"] = float(trades["bars_held"].astype(float).mean())
    else:
        out.update(win_rate=None, profit_factor=None, expectancy=None, expectancy_pct=None,
                   avg_bars_held=None)

    exp = result.exposure.dropna()
    mean_exp = float(exp.mean()) if len(exp) else 0.0
    out["exposure"] = mean_exp
    out["exposure_adjusted_return"] = (
        _f(out["annualized_return"] / mean_exp)
        if mean_exp > 0 and out["annualized_return"] is not None else None)
    return out
