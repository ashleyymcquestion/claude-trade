"""Daily backtest with drift, a no-trade band, execution lag and trading costs."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import Config
from .strategy import DAYS_PER_YEAR, target_weights


@dataclass
class BacktestResult:
    equity: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    benchmark: pd.Series


def run_backtest(prices: pd.DataFrame, cfg: Config) -> BacktestResult:
    targets = target_weights(prices, cfg).shift(cfg.execution_lag).fillna(0.0)
    rets = prices.pct_change(fill_method=None).fillna(0.0).to_numpy()
    cost = np.array([(cfg.crypto_cost_bps if cfg.is_crypto(s) else cfg.stock_cost_bps) / 1e4
                     for s in prices.columns])

    n_days, n_assets = rets.shape
    w = np.zeros(n_assets)
    equity = np.ones(n_days)
    turnover = np.zeros(n_days)
    held = np.zeros((n_days, n_assets))
    tgt = targets.to_numpy()

    for t in range(1, n_days):
        port_ret = float(w @ rets[t])
        w = w * (1 + rets[t]) / (1 + port_ret)
        diff = tgt[t] - w
        trade = (np.abs(diff) > cfg.rebalance_band) | ((tgt[t] == 0) & (w > 0))
        traded = np.where(trade, diff, 0.0)
        w = w + traded
        equity[t] = equity[t - 1] * (1 + port_ret) * (1 - float(np.abs(traded) @ cost))
        turnover[t] = np.abs(traded).sum()
        held[t] = w

    # Benchmark: equal weight across whatever assets have prices, no costs.
    available = prices.notna() & prices.shift(1).notna()
    bench_ret = (prices.pct_change(fill_method=None).where(available)).mean(axis=1).fillna(0.0)

    return BacktestResult(
        equity=pd.Series(equity, index=prices.index, name="strategy"),
        weights=pd.DataFrame(held, index=prices.index, columns=prices.columns),
        turnover=pd.Series(turnover, index=prices.index, name="turnover"),
        benchmark=(1 + bench_ret).cumprod().rename("equal_weight"),
    )


def metrics(equity: pd.Series) -> dict:
    equity = equity.dropna()
    rets = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else np.nan
    vol = rets.std() * np.sqrt(DAYS_PER_YEAR)
    sharpe = rets.mean() * DAYS_PER_YEAR / vol if vol > 0 else np.nan
    max_dd = (equity / equity.cummax() - 1).min()
    return {
        "CAGR": cagr,
        "Volatility": vol,
        "Sharpe": sharpe,
        "Max drawdown": max_dd,
        "Calmar": cagr / abs(max_dd) if max_dd < 0 else np.nan,
        "Total return": equity.iloc[-1] / equity.iloc[0] - 1,
    }


def report(result: BacktestResult, cfg: Config) -> str:
    # Skip the warm-up period where the strategy is forced to sit in cash.
    start = result.equity.index[min(cfg.warmup_days(), len(result.equity) - 2)]
    eq, bench = result.equity[start:], result.benchmark[start:]
    mid = eq.index[len(eq) // 2]

    rows = {
        "Strategy (full)": metrics(eq),
        "Strategy (1st half)": metrics(eq[:mid]),
        "Strategy (2nd half)": metrics(eq[mid:]),
        "Equal-weight benchmark": metrics(bench),
    }
    table = pd.DataFrame(rows).T
    pct = ["CAGR", "Volatility", "Max drawdown", "Total return"]
    table[pct] = table[pct].map(lambda x: f"{x:7.1%}")
    table[["Sharpe", "Calmar"]] = table[["Sharpe", "Calmar"]].map(lambda x: f"{x:5.2f}")

    def year_returns(s: pd.Series) -> pd.Series:
        ends = s.resample("YE").last()
        prev = pd.concat([pd.Series([s.iloc[0]]), ends.iloc[:-1]]).to_numpy()
        return pd.Series(ends.to_numpy() / prev - 1, index=ends.index.year)

    yearly = pd.DataFrame({"strategy": year_returns(eq), "benchmark": year_returns(bench)})
    turnover = result.turnover[start:].sum() / max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    last = result.weights.iloc[-1]
    held = ", ".join(f"{s} {w:.1%}" for s, w in last[last > 0.001].items()) or "cash"

    return "\n".join([
        f"Period: {eq.index[0].date()} -> {eq.index[-1].date()}",
        "",
        table.to_string(),
        "",
        "Calendar-year returns:",
        yearly.map(lambda x: f"{x:7.1%}").to_string(),
        "",
        f"Annual turnover: {turnover:.1f}x",
        f"Latest weights: {held}",
    ])
