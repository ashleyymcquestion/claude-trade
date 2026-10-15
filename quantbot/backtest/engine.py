"""Event-driven, long-only backtester.

Why our own engine
------------------
We deliberately wrote a small event-driven simulator instead of using an off-the-shelf library:

* ``backtesting.py`` is single-asset and AGPL-licensed (licence risk for this codebase), and it
  cannot run a shared-cash multi-symbol portfolio.
* ``vectorbt`` is heavy (numba, many transitive deps) and its vectorised fills are harder to
  audit bar by bar.
* Above all, this engine sizes every trade with :func:`quantbot.risk.sizing.size_position`, the
  same function the paper-trading runner uses, so a backtest sizes exactly like paper trading.
  Every fill rule below is a few explicit lines that can be read and tested.

Execution rules (identical for single-symbol and portfolio runs; ``run_backtest`` is a
one-symbol ``run_portfolio``)
-------------------------------------------------------------------------------------------
For each timestamp of the (union) timeline, for every symbol that has a bar at that timestamp:

1. **Open, exits first.** Exits decided at the previous bar's close (exit signal or
   ``max_hold_bars`` reached) fill at this bar's open * (1 - one_way_cost).
2. **Open, entries** (symbols sorted alphabetically, so the order is deterministic). An entry
   decided at the previous close fills at this bar's open * (1 + one_way_cost). It is sized with
   ``size_position(price=this open, stop_price=open - stop_distance, equity=cash + positions
   marked at their latest close, ...)``. Using the open quote for sizing is equivalent to a
   market-on-open order sized from the opening quote, which is what the paper runner does.
   Rejected sizes record nothing (only a counter in ``meta``).
   The trade's protective stop is ``entry fill - stop_distance`` (signal row of the decision).
3. **Intrabar stops.** If the bar's low <= the active stop (max of the protective stop and the
   trailing stop), exit at ``min(open, stop)`` -- a gap below the stop fills at the open --
   times (1 - one_way_cost). A position entered at this open is already protected.
4. **Close.** Mark to market; update the highest close since entry and ratchet the trailing
   stop ``highest_close - trail_distance`` (it only ever moves up; ``trail_distance`` is the
   value from the entry decision row).
5. **Signals** of this bar's row are read and become pending orders for the symbol's NEXT bar.
   An entry is only taken when flat at this close, and a row that says both entry and exit
   does not enter, so a single signal can never enter and exit at the same open.

``one_way_cost`` comes from ``cfg.costs.one_way_bps(asset_class)`` and already contains fees,
half the spread and slippage.

Signals are computed once on the whole series. That is only acceptable because the
:class:`~quantbot.strategies.base.Strategy` contract guarantees each row uses only that bar and
earlier ones; the decision of row *t* is never acted on before bar *t+1*'s open.

Market sessions: daily bar files contain only real trading sessions (stocks: exchange days;
crypto: every UTC day), so "the next bar" is always the next session in which the asset
actually traded. In a portfolio the timeline is the union of all symbols' timestamps; a stock
only trades (and only has its stops checked) on its own bars, while crypto trades every day.
Between a symbol's bars its position is marked at its latest close.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from ..models import AssetInfo
from ..risk.sizing import size_position
from ..strategies.base import SIGNAL_COLUMNS

if TYPE_CHECKING:  # pragma: no cover
    from ..config import AppConfig, SymbolConfig
    from ..strategies.base import Strategy

TRADE_COLUMNS = (
    "symbol", "strategy", "entry_ts", "entry_price", "exit_ts", "exit_price", "qty", "pnl",
    "return_pct", "costs", "exit_reason", "bars_held", "regime_at_entry",
)


@dataclass
class BacktestResult:
    """Outcome of a simulation.

    equity     account equity at each bar close (index = timeline)
    trades     CLOSED trades only, columns ``TRADE_COLUMNS``. ``entry_price``/``exit_price`` are
               fill prices including costs; ``pnl`` is net of all costs; ``costs`` is the dollar
               cost of both legs.
    costs_paid total dollar costs paid, including the entry leg of still-open positions
    exposure   gross position value / equity at each bar close
    meta       symbol(s), strategy, data_source, start, end, capital, ...
    open_positions  positions still open at the end, marked to market (not counted as trades)
    """
    equity: pd.Series
    trades: pd.DataFrame
    costs_paid: float
    exposure: pd.Series
    meta: dict = field(default_factory=dict)
    open_positions: list[dict] = field(default_factory=list)

    @property
    def initial_capital(self) -> float:
        return float(self.meta["capital"])


@dataclass
class _Position:
    symbol: str
    qty: float
    entry_fill: float
    entry_ts: pd.Timestamp
    stop: float
    trail_distance: float
    max_hold: float
    entry_cost: float
    regime: str | None
    highest_close: float = -math.inf
    trail_stop: float = -math.inf
    bars_held: int = 0           # closes seen while in the position

    @property
    def active_stop(self) -> float:
        return max(self.stop, self.trail_stop)


@dataclass
class _SymbolData:
    sym: str
    asset: AssetInfo
    cost: float                  # one-way cost as a fraction
    index: pd.DatetimeIndex
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    entry: np.ndarray
    exit: np.ndarray
    stop_d: np.ndarray
    trail_d: np.ndarray
    max_hold: np.ndarray
    regime: list | None


def _prepare(bars: pd.DataFrame, signals: pd.DataFrame, sym: str, asset: AssetInfo, cost: float,
             regimes: pd.Series | None) -> _SymbolData:
    if not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
        raise ValueError(f"{sym}: bars index must be sorted and unique")
    missing = [c for c in SIGNAL_COLUMNS if c not in signals.columns]
    if missing:
        raise ValueError(f"{sym}: strategy signals missing columns {missing}")
    sig = signals.reindex(bars.index)
    reg = None
    if regimes is not None:
        reg = [None if (isinstance(v, float) and math.isnan(v)) or v is None else str(v)
               for v in regimes.reindex(bars.index).tolist()]
    return _SymbolData(
        sym=sym, asset=asset, cost=cost, index=bars.index,
        o=bars["open"].to_numpy(float), h=bars["high"].to_numpy(float),
        lo=bars["low"].to_numpy(float), c=bars["close"].to_numpy(float),
        entry=sig["entry"].fillna(False).to_numpy(bool),
        exit=sig["exit"].fillna(False).to_numpy(bool),
        stop_d=sig["stop_distance"].to_numpy(float),
        trail_d=sig["trail_distance"].to_numpy(float),
        max_hold=sig["max_hold_bars"].to_numpy(float),
        regime=reg,
    )


def _simulate(data: dict[str, _SymbolData], strategy_name: str, cfg: "AppConfig", capital: float,
              *, trade_start: pd.Timestamp | None = None, close_at_end: bool = False) -> BacktestResult:
    """Core loop shared by run_backtest and run_portfolio. See the module docstring."""
    risk = cfg.risk
    symbols = sorted(data)
    timeline = data[symbols[0]].index
    for s in symbols[1:]:
        timeline = timeline.union(data[s].index)
    # local bar position of each symbol on the timeline (-1 = no bar at that timestamp)
    local = {s: pd.Series(np.arange(len(data[s].index)), index=data[s].index)
             .reindex(timeline).fillna(-1).astype(int).to_numpy() for s in symbols}

    cash = float(capital)
    positions: dict[str, _Position] = {}
    pending_entry: dict[str, int] = {}     # symbol -> local index of the signal row
    pending_exit: dict[str, str] = {}      # symbol -> reason
    mark: dict[str, float] = {}            # latest known price per symbol
    trades: list[dict] = []
    costs_paid = 0.0
    rejected: list[str] = []
    equity_out = np.empty(len(timeline))
    exposure_out = np.empty(len(timeline))

    def close_position(s: str, raw_price: float, ts: pd.Timestamp, reason: str, extra_bar: int) -> None:
        nonlocal cash, costs_paid
        p = positions.pop(s)
        d = data[s]
        fill = raw_price * (1 - d.cost)
        exit_cost = p.qty * raw_price * d.cost
        cash += p.qty * fill
        costs_paid += exit_cost
        trades.append({
            "symbol": s, "strategy": strategy_name, "entry_ts": p.entry_ts, "entry_price": p.entry_fill,
            "exit_ts": ts, "exit_price": fill, "qty": p.qty, "pnl": p.qty * (fill - p.entry_fill),
            "return_pct": fill / p.entry_fill - 1, "costs": p.entry_cost + exit_cost,
            "exit_reason": reason, "bars_held": p.bars_held + extra_bar, "regime_at_entry": p.regime,
        })

    for k, ts in enumerate(timeline):
        active = [s for s in symbols if local[s][k] >= 0]

        # 1. exits at the open (before any entry, so freed cash/exposure is available)
        for s in active:
            reason = pending_exit.pop(s, None)
            if reason is not None and s in positions:
                close_position(s, data[s].o[local[s][k]], ts, reason, 0)

        # 2. entries at the open, deterministic symbol order
        for s in active:
            sig_i = pending_entry.pop(s, None)
            if sig_i is None or s in positions:
                continue
            if trade_start is not None and ts < trade_start:
                continue
            d, i = data[s], local[s][k]
            if len(positions) >= risk.max_open_positions:
                rejected.append(f"{ts.date()} {s}: max_open_positions")
                continue
            gross = sum(p.qty * mark[q] for q, p in positions.items())
            price = d.o[i]
            sd = d.stop_d[sig_i]
            stop_px = price - sd if math.isfinite(sd) and sd > 0 else None
            # Sizing from the open quote == a market-on-open order sized from the opening quote.
            dec = size_position(
                equity=cash + gross, cash=cash, price=price, stop_price=stop_px, asset=d.asset,
                current_gross=gross, current_symbol_exposure=0.0, risk=risk,
                one_way_cost_bps=d.cost * 10_000,
            )
            if not dec.approved:
                rejected.append(f"{ts.date()} {s}: {dec.reason}")
                continue
            qty = dec.qty
            fill = price * (1 + d.cost)
            entry_cost = qty * price * d.cost
            cash -= qty * fill
            costs_paid += entry_cost
            mark[s] = price
            positions[s] = _Position(
                symbol=s, qty=qty, entry_fill=fill, entry_ts=ts, stop=fill - sd,
                trail_distance=float(d.trail_d[sig_i]), max_hold=float(d.max_hold[sig_i]),
                entry_cost=entry_cost, regime=d.regime[sig_i] if d.regime is not None else None,
            )

        # 3. intrabar protective / trailing stops
        for s in active:
            p = positions.get(s)
            if p is None:
                continue
            d, i = data[s], local[s][k]
            stop = p.active_stop
            if d.lo[i] <= stop:
                reason = "trail_stop" if p.trail_stop > p.stop else "stop"
                close_position(s, min(d.o[i], stop), ts, reason, 1)

        # 4. close: mark, ratchet trailing stops
        for s in active:
            d, i = data[s], local[s][k]
            mark[s] = d.c[i]
            p = positions.get(s)
            if p is not None:
                p.bars_held += 1
                p.highest_close = max(p.highest_close, d.c[i])
                if math.isfinite(p.trail_distance) and p.trail_distance > 0:
                    p.trail_stop = max(p.trail_stop, p.highest_close - p.trail_distance)
        gross = sum(p.qty * mark[q] for q, p in positions.items())
        equity = cash + gross
        equity_out[k] = equity
        exposure_out[k] = gross / equity if equity > 0 else float("nan")

        # 5. this bar's signals become orders for the symbol's next bar
        for s in active:
            d, i = data[s], local[s][k]
            p = positions.get(s)
            if p is not None:
                if d.exit[i]:
                    pending_exit[s] = "signal"
                elif math.isfinite(p.max_hold) and p.bars_held >= p.max_hold:
                    pending_exit[s] = "max_hold"
            elif d.entry[i] and not d.exit[i]:
                pending_entry[s] = i

    if close_at_end and positions and len(timeline):
        for s in sorted(positions):
            p = positions[s]
            close_position(s, mark[s], timeline[-1], "end_of_data", 0)
        equity_out[-1] = cash

    open_positions = [
        {"symbol": s, "entry_ts": p.entry_ts, "entry_price": p.entry_fill, "qty": p.qty,
         "mark_price": mark[s], "market_value": p.qty * mark[s],
         "unrealized_pnl": p.qty * (mark[s] - p.entry_fill), "stop": p.active_stop,
         "bars_held": p.bars_held, "regime_at_entry": p.regime}
        for s, p in sorted(positions.items())
    ]
    trades_df = pd.DataFrame(trades, columns=list(TRADE_COLUMNS))
    meta = {
        "symbol": symbols[0] if len(symbols) == 1 else symbols,
        "strategy": strategy_name,
        "start": timeline[0] if len(timeline) else None,
        "end": timeline[-1] if len(timeline) else None,
        "capital": float(capital),
        "rejected_entries": len(rejected),
        "rejections": rejected[-50:],
    }
    return BacktestResult(
        equity=pd.Series(equity_out, index=timeline, name="equity"),
        trades=trades_df, costs_paid=float(costs_paid),
        exposure=pd.Series(exposure_out, index=timeline, name="exposure"),
        meta=meta, open_positions=open_positions,
    )


def run_backtest(bars: pd.DataFrame, strategy: "Strategy", sym: "SymbolConfig", asset: AssetInfo,
                 cfg: "AppConfig", capital: float, regimes: pd.Series | None = None, *,
                 data_source: str | None = None, trade_start: pd.Timestamp | None = None,
                 close_at_end: bool = False) -> BacktestResult:
    """Single-symbol long-only backtest (see module docstring for the fill rules).

    trade_start   no entry fills before this timestamp (earlier bars are warm-up only); used by
                  walk-forward and split-sample tests.
    close_at_end  close any open position at the last close (with exit costs) instead of
                  leaving it open and marked to market.
    """
    cost = cfg.costs.one_way_bps(sym.asset_class) / 10_000
    sig = strategy.signals(bars)
    data = {sym.symbol: _prepare(bars, sig, sym.symbol, asset, cost, regimes)}
    res = _simulate(data, strategy.name, cfg, capital, trade_start=trade_start, close_at_end=close_at_end)
    res.meta.update(symbol=sym.symbol, data_symbol=sym.data_symbol, data_source=data_source,
                    one_way_cost_bps=cost * 10_000)
    return res


def run_portfolio(bars_by_symbol: dict[str, pd.DataFrame], strategy: "Strategy",
                  syms: list["SymbolConfig"], assets: dict[str, AssetInfo], cfg: "AppConfig",
                  capital: float, regimes_by_symbol: dict[str, pd.Series] | None = None, *,
                  data_source: str | None = None) -> BacktestResult:
    """One strategy across several symbols on a shared cash account.

    ``max_position_pct``/``max_gross_pct`` and cash are enforced by ``size_position`` (fed the
    current gross exposure); ``max_open_positions`` by counting open positions. Exits fill
    before entries at each open; entries are processed in symbol order.
    """
    data = {}
    for s in syms:
        if s.symbol not in bars_by_symbol:
            continue
        bars = bars_by_symbol[s.symbol]
        cost = cfg.costs.one_way_bps(s.asset_class) / 10_000
        reg = (regimes_by_symbol or {}).get(s.symbol)
        data[s.symbol] = _prepare(bars, strategy.signals(bars), s.symbol, assets[s.symbol], cost, reg)
    if not data:
        raise ValueError("run_portfolio: no symbol has bars")
    res = _simulate(data, strategy.name, cfg, capital)
    res.meta.update(symbol=sorted(data), data_source=data_source)
    return res


def buy_and_hold(bars: pd.DataFrame, sym: "SymbolConfig", asset: AssetInfo, cfg: "AppConfig",
                 capital: float, *, data_source: str | None = None) -> BacktestResult:
    """Benchmark: buy as much as cash allows at the first bar's open (+ costs) and hold.

    Not risk-sized (it is a benchmark, 100% invested). Whole units if the asset is not
    fractionable, otherwise rounded down to ``min_trade_increment`` when set. The position is
    left open and marked to market (no exit cost).
    """
    cost = cfg.costs.one_way_bps(sym.asset_class) / 10_000
    price = float(bars["open"].iloc[0])
    qty = capital / (price * (1 + cost))
    if not asset.fractionable:
        qty = float(math.floor(qty + 1e-9))
    elif asset.min_trade_increment > 0:
        qty = math.floor(qty / asset.min_trade_increment + 1e-9) * asset.min_trade_increment
    fill = price * (1 + cost)
    entry_cost = qty * price * cost
    cash = capital - qty * fill
    close = bars["close"].astype(float)
    equity = (cash + qty * close).rename("equity")
    exposure = ((qty * close) / equity).rename("exposure")
    last = float(close.iloc[-1])
    open_positions = [] if qty <= 0 else [{
        "symbol": sym.symbol, "entry_ts": bars.index[0], "entry_price": fill, "qty": qty,
        "mark_price": last, "market_value": qty * last, "unrealized_pnl": qty * (last - fill),
        "stop": None, "bars_held": len(bars), "regime_at_entry": None}]
    meta = {"symbol": sym.symbol, "data_symbol": sym.data_symbol, "strategy": "buy_and_hold",
            "data_source": data_source, "start": bars.index[0], "end": bars.index[-1],
            "capital": float(capital), "one_way_cost_bps": cost * 10_000}
    return BacktestResult(equity=equity, trades=pd.DataFrame(columns=list(TRADE_COLUMNS)),
                          costs_paid=float(entry_cost), exposure=exposure, meta=meta,
                          open_positions=open_positions)
