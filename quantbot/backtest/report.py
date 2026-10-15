"""Backtest and walk-forward reports (Markdown + JSON) for every enabled symbol and strategy.

Every report states where its data came from (CSV ``source`` column, date range, bar count,
whether the series is a research proxy), the cost and asset assumptions used, and that past
results do not guarantee future results. Problems with one symbol/strategy (missing file, too
little history for warm-up) are written into the report instead of aborting the run.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from ..models import AssetClass, AssetInfo
from .engine import BacktestResult, buy_and_hold, run_backtest, run_portfolio
from .metrics import compute_metrics, infer_periods_per_year
from .walkforward import run_segment, walk_forward

if TYPE_CHECKING:  # pragma: no cover
    from ..config import AppConfig, SymbolConfig
    from ..strategies.base import Strategy

ALL_STRATEGIES = ("trend", "mean_reversion", "breakout")
WARNING = ("Past results do not guarantee future results. These are simulated, paper-only "
           "research numbers on historical data with assumed costs; real fills, fees and "
           "availability can differ materially.")
ASSET_NOTE = ("Research AssetInfo is an ASSUMPTION built from config (tradable, fractionable, "
              "min notional $1.00 for both stocks and crypto). Verify each symbol against the "
              "broker's asset endpoint (`verify-assets`) before relying on sizing.")
EARLIEST = datetime(1970, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------- helpers

def research_asset(sym: "SymbolConfig") -> AssetInfo:
    return AssetInfo(symbol=sym.symbol, asset_class=sym.asset_class, tradable=True,
                     fractionable=True, min_notional=1.0)


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.isoformat()
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return x if math.isfinite(x) else None
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if x is pd.NaT:
        return None
    return x


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.2f}%"


def _num(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _cost_assumptions(cfg: "AppConfig") -> dict:
    c = cfg.costs
    return {
        "stock_one_way_bps": c.one_way_bps(AssetClass.STOCK),
        "crypto_one_way_bps": c.one_way_bps(AssetClass.CRYPTO),
        "components": c.model_dump(),
        "note": "one-way cost = fee + half spread + slippage, charged on every fill; assumptions, "
                "verify against the broker's current fee schedule",
    }


def _default_source(cfg: "AppConfig", source):
    if source is not None:
        return source
    from quantbot.data.sources import CsvBarSource  # lazy: written concurrently
    return CsvBarSource(cfg.data.bars_dir)


def _provenance(cfg: "AppConfig", sym: "SymbolConfig", bars: pd.DataFrame, source) -> dict:
    name = (sym.data_symbol or sym.symbol).replace("/", "-")
    path = Path(cfg.data.bars_dir) / f"{name}.csv"
    src_values: list[str] = []
    if type(source).__name__ == "CsvBarSource" and path.exists():
        try:
            head = pd.read_csv(path, nrows=0)
            if "source" in head.columns:
                src_values = sorted(str(v) for v in pd.read_csv(path, usecols=["source"])["source"].dropna().unique())
        except Exception as e:  # noqa: BLE001 - provenance is best effort, reported
            src_values = [f"unreadable source column: {e}"]
    return {
        "loader": type(source).__name__,
        "file": str(path) if type(source).__name__ == "CsvBarSource" else None,
        "source_column": src_values or None,
        "start": bars.index[0] if len(bars) else None,
        "end": bars.index[-1] if len(bars) else None,
        "bars": int(len(bars)),
        "data_symbol": sym.data_symbol,
        "is_proxy": sym.data_symbol is not None,
    }


def _load_all(cfg: "AppConfig", source) -> tuple[dict, dict, dict]:
    bars, prov, errors = {}, {}, {}
    for sym in cfg.enabled_symbols():
        try:
            df = source.get_bars(sym, EARLIEST)
            if df is None or len(df) == 0:
                errors[sym.symbol] = "no bars returned"
                continue
            bars[sym.symbol] = df
            prov[sym.symbol] = _provenance(cfg, sym, df, source)
        except Exception as e:  # noqa: BLE001 - reported per symbol
            errors[sym.symbol] = f"could not load bars: {type(e).__name__}: {e}"
    return bars, prov, errors


def _regimes(bars: pd.DataFrame) -> tuple[pd.Series | None, str | None]:
    try:
        from quantbot.regime import classify  # lazy: written concurrently
        return classify(bars), None
    except Exception as e:  # noqa: BLE001
        return None, f"regime classification unavailable: {type(e).__name__}: {e}"


def _regime_breakdown(trades: pd.DataFrame) -> list[dict]:
    if not len(trades):
        return []
    t = trades.assign(regime_at_entry=trades["regime_at_entry"].fillna("unknown"))
    rows = []
    for reg, g in t.groupby("regime_at_entry", sort=True):
        rows.append({"regime": reg, "trades": int(len(g)), "win_rate": float((g["pnl"] > 0).mean()),
                     "mean_return_pct": float(g["return_pct"].mean()), "total_pnl": float(g["pnl"].sum())})
    return rows


def _resolve_strategies(cfg: "AppConfig", strategies) -> tuple[list["Strategy"], dict[str, str]]:
    errors: dict[str, str] = {}
    if strategies is not None and all(not isinstance(s, str) for s in strategies):
        return list(strategies), errors
    names = list(strategies) if strategies is not None else [
        n for n in ALL_STRATEGIES if getattr(cfg.strategies, n).enabled]
    out = []
    for n in names:
        try:
            from quantbot.strategies import get_strategy  # lazy: written concurrently
            out.append(get_strategy(n, cfg.strategies))
        except Exception as e:  # noqa: BLE001
            errors[n] = f"could not build strategy: {type(e).__name__}: {e}"
    return out, errors


def _metrics_row(label: str, m: dict) -> str:
    return (f"| {label} | {m['num_trades']} | {_pct(m['total_return'])} | {_pct(m['annualized_return'])} | "
            f"{_num(m['sharpe'])} | {_num(m['sortino'])} | {_pct(m['max_drawdown'])} | {_pct(m['win_rate'])} | "
            f"{_num(m['profit_factor'])} | {_pct(m['exposure'])} | ${m['total_costs']:.2f} |")


METRICS_HEADER = ("| run | trades | total | CAGR | Sharpe | Sortino | max DD | win rate | PF | exposure | costs |\n"
                  "|---|---|---|---|---|---|---|---|---|---|---|")


def _header_md(title: str, cfg: "AppConfig", generated: str) -> list[str]:
    ca = _cost_assumptions(cfg)
    r = cfg.risk
    return [
        f"# {title}", "", f"Generated {generated} UTC. Paper-only research.", "",
        f"> **Warning:** {WARNING}", "",
        "## Assumptions", "",
        f"- Costs (one way, every fill): stocks {ca['stock_one_way_bps']:.1f} bps, "
        f"crypto {ca['crypto_one_way_bps']:.1f} bps (fee + half spread + slippage; verify against the broker).",
        f"- Sizing: `size_position` with risk_per_trade {r.risk_per_trade:.2%}, max_position_pct "
        f"{r.max_position_pct:.0%}, max_gross_pct {r.max_gross_pct:.0%}, max_open_positions {r.max_open_positions}.",
        f"- Capital: ${cfg.initial_capital:,.2f}. Decisions at bar t's close execute at bar t+1's open.",
        f"- {ASSET_NOTE}", "",
    ]


def _provenance_md(sym_name: str, p: dict) -> str:
    proxy = f"PROXY: data from `{p['data_symbol']}`" if p["is_proxy"] else "not a proxy"
    src = ", ".join(p["source_column"]) if p["source_column"] else "no `source` column"
    return (f"Data: {p['loader']}{' `' + p['file'] + '`' if p['file'] else ''}; source: {src}; "
            f"{pd.Timestamp(p['start']).date()} to {pd.Timestamp(p['end']).date()}, {p['bars']} bars; {proxy}.")


def _write(out_dir: Path, prefix: str, md: str, payload: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    md_path = out_dir / f"{prefix}_{stamp}.md"
    md_path.write_text(md)
    text = json.dumps(_jsonable(payload), indent=2, allow_nan=False)
    (out_dir / f"{prefix}_{stamp}.json").write_text(text)
    (out_dir / f"latest_{prefix}.json").write_text(text)
    return md_path


# ---------------------------------------------------------------- backtest report

def run_backtest_report(cfg: "AppConfig", out_dir: str | Path, strategies=None, source=None) -> Path:
    """Full-period backtests, buy-and-hold, regime and half splits, portfolio runs. Returns the .md path."""
    source = _default_source(cfg, source)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    strats, strat_errors = _resolve_strategies(cfg, strategies)
    bars_by, prov, load_errors = _load_all(cfg, source)
    capital = cfg.initial_capital

    payload: dict[str, Any] = {
        "kind": "backtest", "generated_at": generated, "warning": WARNING,
        "costs": _cost_assumptions(cfg), "asset_assumptions": ASSET_NOTE,
        "risk": cfg.risk.model_dump(), "capital": capital,
        "strategy_errors": strat_errors, "load_errors": load_errors, "symbols": {}, "portfolio": {},
    }
    md = _header_md("Backtest report", cfg, generated)
    for n, e in strat_errors.items():
        md.append(f"- Strategy `{n}`: {e}")
    for s, e in load_errors.items():
        md.append(f"- Symbol `{s}`: {e}")

    regimes_by: dict[str, pd.Series] = {}
    for sym in cfg.enabled_symbols():
        if sym.symbol not in bars_by:
            continue
        bars = bars_by[sym.symbol]
        asset = research_asset(sym)
        ppy = infer_periods_per_year(bars.index)
        src = ", ".join(prov[sym.symbol]["source_column"] or []) or None
        regimes, regime_note = _regimes(bars)
        if regimes is not None:
            regimes_by[sym.symbol] = regimes
        bh = buy_and_hold(bars, sym, asset, cfg, capital, data_source=src)
        bh_m = compute_metrics(bh, ppy)
        entry: dict[str, Any] = {"provenance": prov[sym.symbol], "periods_per_year": ppy,
                                 "regime_note": regime_note, "buy_and_hold": bh_m, "strategies": {}}
        md += [f"## {sym.symbol}", "", _provenance_md(sym.symbol, prov[sym.symbol]),
               f"Annualisation: {ppy} bars/year (inferred from the bar calendar).", ""]
        if regime_note:
            md += [f"Note: {regime_note}", ""]
        rows = [_metrics_row("buy & hold", bh_m)]
        details: list[str] = []
        for strat in strats:
            warm = int(strat.warmup_bars)
            if len(bars) <= warm + 2:
                msg = f"too little history: {len(bars)} bars, strategy needs {warm} warm-up bars"
                entry["strategies"][strat.name] = {"status": "skipped", "reason": msg}
                rows.append(f"| {strat.name} | {msg} |||||||||| ")
                continue
            try:
                res = run_backtest(bars, strat, sym, asset, cfg, capital, regimes=regimes, data_source=src)
                m = compute_metrics(res, ppy)
                mid = len(bars) // 2
                halves = {}
                for label, a, b in (("first_half", 0, mid), ("second_half", mid, len(bars))):
                    hr = run_segment(bars, strat, sym, asset, cfg, capital, a, b, regimes=regimes)
                    halves[label] = compute_metrics(hr, ppy)
                reg = _regime_breakdown(res.trades)
            except Exception as e:  # noqa: BLE001 - one bad combination must not sink the report
                entry["strategies"][strat.name] = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
                rows.append(f"| {strat.name} | error: {type(e).__name__}: {e} |||||||||| ")
                continue
            entry["strategies"][strat.name] = {
                "status": "ok", "metrics": m, "halves": halves, "regime_breakdown": reg,
                "open_positions": res.open_positions, "rejected_entries": res.meta["rejected_entries"],
                "trades": res.trades.to_dict(orient="records"),
            }
            rows.append(_metrics_row(strat.name, m))
            details += [f"### {sym.symbol} / {strat.name}", "",
                        f"Halves (each half run separately with warm-up; open positions closed at the half's end): "
                        f"first {_pct(halves['first_half']['total_return'])} "
                        f"(Sharpe {_num(halves['first_half']['sharpe'])}, {halves['first_half']['num_trades']} trades), "
                        f"second {_pct(halves['second_half']['total_return'])} "
                        f"(Sharpe {_num(halves['second_half']['sharpe'])}, {halves['second_half']['num_trades']} trades).",
                        f"Open at end (marked to market, not counted as trades): {len(res.open_positions)}. "
                        f"Rejected entries (sizing): {res.meta['rejected_entries']}.", ""]
            if reg:
                details += ["| regime at entry | trades | win rate | mean return | total P&L |", "|---|---|---|---|---|"]
                details += [f"| {r['regime']} | {r['trades']} | {_pct(r['win_rate'])} | {_pct(r['mean_return_pct'])} | "
                            f"${r['total_pnl']:.2f} |" for r in reg]
                details.append("")
        md += [METRICS_HEADER, *rows, "", *details]
        payload["symbols"][sym.symbol] = entry

    if bars_by and strats:
        md += ["## Portfolio (shared cash, all symbols)", "", METRICS_HEADER]
        frames = list(bars_by.values())
        union = frames[0].index
        for b in frames[1:]:
            union = union.union(b.index)
        ppy = infer_periods_per_year(union)
        syms = [s for s in cfg.enabled_symbols() if s.symbol in bars_by]
        assets = {s.symbol: research_asset(s) for s in syms}
        for strat in strats:
            try:
                pr = run_portfolio(bars_by, strat, syms, assets, cfg, capital, regimes_by_symbol=regimes_by)
                pm = compute_metrics(pr, ppy)
                pm["max_exposure"] = float(pr.exposure.max())
                payload["portfolio"][strat.name] = {"status": "ok", "metrics": pm,
                                                    "regime_breakdown": _regime_breakdown(pr.trades)}
                md.append(_metrics_row(strat.name, pm))
            except Exception as e:  # noqa: BLE001
                payload["portfolio"][strat.name] = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
                md.append(f"| {strat.name} | error: {type(e).__name__}: {e} |||||||||| ")
        md += ["", f"Portfolio annualisation: {ppy} bars/year. Stocks trade only on their own sessions.", ""]

    md += ["---", "", f"*{WARNING}*", ""]
    return _write(Path(out_dir), "backtest", "\n".join(md), payload)


# ---------------------------------------------------------------- walk-forward report

def run_walkforward_report(cfg: "AppConfig", out_dir: str | Path, source=None,
                           train_bars: int = 504, test_bars: int = 126) -> Path:
    source = _default_source(cfg, source)
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    names = [n for n in ALL_STRATEGIES if getattr(cfg.strategies, n).enabled]
    bars_by, prov, load_errors = _load_all(cfg, source)
    capital = cfg.initial_capital
    payload: dict[str, Any] = {
        "kind": "walkforward", "generated_at": generated, "warning": WARNING,
        "costs": _cost_assumptions(cfg), "asset_assumptions": ASSET_NOTE, "risk": cfg.risk.model_dump(),
        "capital": capital, "train_bars": train_bars, "test_bars": test_bars,
        "load_errors": load_errors, "symbols": {},
    }
    md = _header_md("Walk-forward report", cfg, generated)
    md += [f"Rolling windows: train {train_bars} bars, test {test_bars} bars. Parameters chosen by train-window "
           "Sharpe only (ties to the configured default); out-of-sample equity is stitched across test windows, "
           "each starting from the previous window's ending equity. Open positions are closed at each test "
           "window's end.", ""]
    for s, e in load_errors.items():
        md.append(f"- Symbol `{s}`: {e}")

    for sym in cfg.enabled_symbols():
        if sym.symbol not in bars_by:
            continue
        bars = bars_by[sym.symbol]
        asset = research_asset(sym)
        ppy = infer_periods_per_year(bars.index)
        src = ", ".join(prov[sym.symbol]["source_column"] or []) or None
        entry: dict[str, Any] = {"provenance": prov[sym.symbol], "periods_per_year": ppy, "strategies": {}}
        md += [f"## {sym.symbol}", "", _provenance_md(sym.symbol, prov[sym.symbol]), ""]
        if len(bars) <= train_bars:
            msg = f"too little history for walk-forward: {len(bars)} bars, need more than {train_bars}"
            entry["note"] = msg
            md += [f"Skipped: {msg}.", ""]
            payload["symbols"][sym.symbol] = entry
            continue
        rows = []
        win_md: list[str] = []
        for name in names:
            try:
                wf = walk_forward(bars, name, sym, asset, cfg, train_bars, test_bars, capital=capital,
                                  data_source=src)
                m = compute_metrics(wf.as_backtest_result(), ppy)
                oos_start = wf.windows[0]["test_start"]
                bh = buy_and_hold(bars.loc[oos_start:], sym, asset, cfg, capital)
                bh_m = compute_metrics(bh, ppy)
            except Exception as e:  # noqa: BLE001
                entry["strategies"][name] = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
                rows.append(f"| {name} | error: {type(e).__name__}: {e} |||||||||| ")
                continue
            entry["strategies"][name] = {"status": "ok", "oos_metrics": m, "buy_and_hold_same_period": bh_m,
                                         "windows": wf.windows,
                                         "oos_trades": wf.oos_trades.to_dict(orient="records")}
            rows += [_metrics_row(f"{name} (OOS)", m), _metrics_row(f"buy & hold, same period", bh_m)]
            win_md += [f"### {sym.symbol} / {name} windows", "",
                       "| test start | test end | params | train Sharpe | test Sharpe | test return | trades |",
                       "|---|---|---|---|---|---|---|"]
            win_md += [f"| {pd.Timestamp(w['test_start']).date()} | {pd.Timestamp(w['test_end']).date()} | "
                       f"{w['chosen_params']} | {_num(w['train_sharpe'])} | {_num(w['test_sharpe'])} | "
                       f"{_pct(w['test_return'])} | {w['test_trades']} |" for w in wf.windows]
            win_md.append("")
        md += [METRICS_HEADER, *rows, "", *win_md]
        payload["symbols"][sym.symbol] = entry

    md += ["---", "", f"*{WARNING}*", ""]
    return _write(Path(out_dir), "walkforward", "\n".join(md), payload)


__all__ = ["run_backtest_report", "run_walkforward_report", "research_asset", "BacktestResult"]
