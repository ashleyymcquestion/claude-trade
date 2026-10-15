"""Rolling walk-forward evaluation.

For each window: parameters are chosen from a deliberately small grid using ONLY the train
window (best Sharpe; ties and undefined Sharpe go to the configured default, then to the
earlier/simpler grid entry), then the chosen strategy is run on the following test window.

The test-window run is given the bars immediately before ``test_start`` as indicator warm-up,
but entries can only fill at or after ``test_start`` (``trade_start``), and the warm-up part of
the equity curve is trimmed off. Positions still open at the end of a test window are closed at
its last close (with exit costs), so every out-of-sample trade lives entirely inside its window.
Each test window starts with the previous window's ending equity, so the stitched OOS equity
curve is continuous and compounds.

Strategies are built with ``quantbot.strategies.get_strategy(name, StrategiesConfig)``, imported
lazily so this module imports before the strategy modules exist.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd

from .engine import TRADE_COLUMNS, BacktestResult, run_backtest
from .metrics import compute_metrics, infer_periods_per_year

if TYPE_CHECKING:  # pragma: no cover
    from ..config import AppConfig, SymbolConfig
    from ..models import AssetInfo
    from ..strategies.base import Strategy

DEFAULT_GRIDS: dict[str, dict[str, list]] = {
    "trend": {"ema_fast": [10, 20], "ema_slow": [50, 100]},
    "mean_reversion": {"rsi_entry": [5.0, 10.0, 15.0], "max_hold_bars": [5, 10]},
    "breakout": {"donchian_entry": [20, 55], "donchian_exit": [10, 20]},
}


@dataclass
class WalkForwardResult:
    oos_equity: pd.Series
    oos_trades: pd.DataFrame
    windows: list[dict]
    oos_exposure: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    meta: dict = field(default_factory=dict)

    def as_backtest_result(self) -> BacktestResult:
        costs = float(self.oos_trades["costs"].sum()) if len(self.oos_trades) else 0.0
        return BacktestResult(equity=self.oos_equity, trades=self.oos_trades, costs_paid=costs,
                              exposure=self.oos_exposure, meta=dict(self.meta))


def build_strategy(name: str, cfg: "AppConfig", params: dict | None = None) -> "Strategy":
    """Strategy ``name`` with ``params`` overriding its sub-config.

    The overridden sub-config is re-validated (``model_validate``), so an invalid combination
    (e.g. ema_fast >= ema_slow) raises ``pydantic.ValidationError`` instead of silently running.
    """
    import quantbot.strategies as strategies_mod  # lazy: written concurrently

    scfg = cfg.strategies
    if params:
        sub = getattr(scfg, name)
        new_sub = type(sub).model_validate({**sub.model_dump(), **params})
        scfg = scfg.model_copy(update={name: new_sub})
    return strategies_mod.get_strategy(name, scfg)


def _candidates(name: str, cfg: "AppConfig", grid: dict[str, list]) -> list[dict]:
    keys = list(grid)
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]
    sub = getattr(cfg.strategies, name, None)
    default = {k: getattr(sub, k) for k in keys} if sub is not None and all(
        hasattr(sub, k) for k in keys) else None
    if default is not None:
        combos = [c for c in combos if c != default]
        combos.insert(0, default)   # ties go to the configured default
    return combos


def trim_result(res: BacktestResult, start: pd.Timestamp) -> BacktestResult:
    """Keep only equity/exposure from ``start`` and trades that ENTERED at or after it."""
    trades = res.trades[res.trades["entry_ts"] >= start].reset_index(drop=True) \
        if len(res.trades) else res.trades
    meta = dict(res.meta)
    eq = res.equity[res.equity.index >= start]
    meta["start"] = eq.index[0] if len(eq) else None
    # the trimmed series starts from the equity just before `start` (== capital: no positions
    # can exist before trade_start)
    before = res.equity[res.equity.index < start]
    meta["capital"] = float(before.iloc[-1]) if len(before) else res.initial_capital
    return BacktestResult(equity=eq, trades=trades, costs_paid=float(trades["costs"].sum()) if len(trades) else 0.0,
                          exposure=res.exposure[res.exposure.index >= start], meta=meta,
                          open_positions=list(res.open_positions))


def run_segment(bars: pd.DataFrame, strategy: "Strategy", sym: "SymbolConfig", asset: "AssetInfo",
                cfg: "AppConfig", capital: float, start_idx: int, end_idx: int,
                regimes: pd.Series | None = None, close_at_end: bool = True) -> BacktestResult:
    """Backtest bars[start_idx:end_idx] using up to ``warmup_bars`` earlier bars as warm-up only."""
    warm = int(strategy.warmup_bars) + 1
    seg = bars.iloc[max(0, start_idx - warm):end_idx]
    start = bars.index[start_idx]
    res = run_backtest(seg, strategy, sym, asset, cfg, capital, regimes=regimes,
                       trade_start=start, close_at_end=close_at_end)
    return trim_result(res, start)


def select_params(train: pd.DataFrame, strategy_name: str, sym: "SymbolConfig", asset: "AssetInfo",
                  cfg: "AppConfig", grid: dict[str, list], capital: float) -> tuple[dict, float | None, list[dict]]:
    """Choose parameters on ``train`` only. Returns (params, train_sharpe, all candidate scores)."""
    ppy = infer_periods_per_year(train.index)
    best: dict | None = None
    best_sharpe: float | None = None
    scores: list[dict] = []
    for params in _candidates(strategy_name, cfg, grid):
        try:
            strat = build_strategy(strategy_name, cfg, params)
        except ValueError as e:  # pydantic.ValidationError subclasses ValueError
            scores.append({"params": params, "sharpe": None, "note": f"invalid: {e.__class__.__name__}"})
            continue
        res = run_backtest(train, strat, sym, asset, cfg, capital, close_at_end=True)
        sharpe = compute_metrics(res, ppy)["sharpe"] if len(res.trades) else None
        scores.append({"params": params, "sharpe": sharpe, "trades": int(len(res.trades))})
        if best is None:
            best, best_sharpe = params, sharpe
        elif sharpe is not None and (best_sharpe is None or sharpe > best_sharpe):
            best, best_sharpe = params, sharpe
    return (best or {}), best_sharpe, scores


def walk_forward(bars: pd.DataFrame, strategy_name: str, sym: "SymbolConfig", asset: "AssetInfo",
                 cfg: "AppConfig", train_bars: int = 504, test_bars: int = 126,
                 grid: dict[str, list] | None = None, capital: float | None = None,
                 data_source: str | None = None) -> WalkForwardResult:
    if train_bars < 1 or test_bars < 1:
        raise ValueError("train_bars and test_bars must be positive")
    grid = DEFAULT_GRIDS.get(strategy_name, {}) if grid is None else grid
    capital = float(cfg.initial_capital if capital is None else capital)
    ppy = infer_periods_per_year(bars.index)
    n = len(bars)
    windows: list[dict] = []
    eq_parts: list[pd.Series] = []
    ex_parts: list[pd.Series] = []
    tr_parts: list[pd.DataFrame] = []
    equity = capital
    test_start = train_bars
    while test_start < n:
        test_end = min(test_start + test_bars, n)
        train = bars.iloc[test_start - train_bars:test_start]
        params, train_sharpe, scores = select_params(train, strategy_name, sym, asset, cfg, grid, capital)
        strat = build_strategy(strategy_name, cfg, params)
        res = run_segment(bars, strat, sym, asset, cfg, equity, test_start, test_end)
        t0 = bars.index[test_start]
        assert not len(res.trades) or (res.trades["entry_ts"] >= t0).all()
        m = compute_metrics(res, ppy)
        windows.append({
            "train_start": train.index[0], "train_end": train.index[-1],
            "test_start": t0, "test_end": bars.index[test_end - 1],
            "chosen_params": params, "train_sharpe": train_sharpe,
            "test_sharpe": m["sharpe"], "test_return": m["total_return"],
            "test_trades": int(len(res.trades)), "candidates": scores,
        })
        eq_parts.append(res.equity)
        ex_parts.append(res.exposure)
        if len(res.trades):
            tr_parts.append(res.trades)
        equity = float(res.equity.iloc[-1])
        test_start = test_end

    oos_eq = pd.concat(eq_parts) if eq_parts else pd.Series(dtype=float, name="equity")
    oos_ex = pd.concat(ex_parts) if ex_parts else pd.Series(dtype=float, name="exposure")
    oos_tr = (pd.concat(tr_parts, ignore_index=True) if tr_parts
              else pd.DataFrame(columns=list(TRADE_COLUMNS)))
    meta: dict[str, Any] = {
        "symbol": sym.symbol, "data_symbol": sym.data_symbol, "strategy": strategy_name,
        "data_source": data_source, "capital": capital, "train_bars": train_bars,
        "test_bars": test_bars, "grid": grid, "periods_per_year": ppy,
        "start": oos_eq.index[0] if len(oos_eq) else None,
        "end": oos_eq.index[-1] if len(oos_eq) else None,
    }
    if not windows:
        meta["note"] = f"only {n} bars; need more than train_bars={train_bars} for one window"
    return WalkForwardResult(oos_equity=oos_eq, oos_trades=oos_tr, windows=windows,
                             oos_exposure=oos_ex, meta=meta)
