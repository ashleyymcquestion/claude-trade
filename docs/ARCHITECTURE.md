# quantbot architecture and module contracts

Paper-only research and execution system. `trader/` (the older Alpaca/Robinhood planner) is
separate and untouched; `quantbot/` is the new system.

```
data source ─► validate ─► strategies ─► risk (breakers, sizing) ─► broker (sim | alpaca paper)
     │                         │                    │                         │
     └──────────── store (SQLite: orders, fills, equity, flags, events) ◄─────┘
                                        ▲
                         dashboard (Streamlit) · alerts · CLI
```

## Ground rules for every module

- Python 3.12+, type hints, no network or credentials in tests (tests use fixtures/fakes).
- Shared types live in `quantbot/models.py`; config in `quantbot/config.py`. Don't redefine them.
- Bars: tz-aware UTC DatetimeIndex, columns `open high low close volume` (see `models.py`).
- No look-ahead: a decision computed from bar *t* is executed at bar *t+1*'s open, never at *t*.
- Never fabricate market data or results. Synthetic data is allowed only inside tests, and must
  never be written to `data/bars/` or reports.
- Log with `quantbot.logging_setup.log_event`; never log credentials.
- There is no live-trading path. `ExecutionConfig.mode` only allows `sim` and `alpaca_paper`.

## Already written (phase 1)

| File | What |
|---|---|
| `models.py` | `AssetClass, Side, OrderType, OrderStatus, AssetInfo, OrderRequest, Order, Fill, Position, AccountSnapshot, Signal, RiskDecision` |
| `config.py` | `AppConfig` (pydantic, validated) and `load_config(path)`; `Credentials.from_env()` |
| `logging_setup.py` | JSON logs with secret redaction; `setup_logging`, `log_event` |
| `strategies/base.py` | `Strategy` ABC: `warmup_bars`, `signals(bars) -> DataFrame` with `entry, exit, stop_distance, trail_distance, max_hold_bars` |
| `broker/base.py` | `Broker` ABC, `BrokerError`, `OrderRejected` |
| `risk/sizing.py` | `size_position(...) -> RiskDecision` — used by BOTH backtester and runner |

## Contracts to implement

### Data (`quantbot/data/`)
- `sources.py`
  - `class BarSource(ABC): get_bars(sym: SymbolConfig, start: datetime, end: datetime | None = None) -> pd.DataFrame`
  - `CsvBarSource(bars_dir)`: reads `<bars_dir>/<name>.csv`, `name = (sym.data_symbol or sym.symbol).replace("/", "-")`,
    columns `timestamp,open,high,low,close,volume` (+ optional `source` provenance column ignored).
  - `AlpacaBarSource(creds, stock_feed="iex")`: alpaca-py `StockHistoricalDataClient`/`CryptoHistoricalDataClient`,
    daily bars, split+dividend adjusted (`Adjustment.ALL`) for stocks. Imports alpaca lazily.
- `validate.py`
  - `completed_bars(df, now) -> df`: drops a bar whose period has not ended (daily: ts + 1 day > now).
  - `validate_bars(df, asset_class, now, cfg: DataConfig) -> ValidationResult(ok: bool, issues: list[str])`:
    empty, NaN, non-positive prices, high<low, close outside [low,high], unsorted/duplicate index,
    gaps longer than `max_gap_bars` (stocks: business days; crypto: calendar days), stale (last bar older than
    `*_stale_after_hours`).

### Indicators, regimes, strategies
- `indicators.py`: pure functions on Series/DataFrame: `ema, sma, atr, rsi (Wilder), bollinger (mid, upper, lower),
  zscore, donchian (upper, lower — of PRIOR n bars, excluding current), relative_volume (vol / prior-n mean),
  momentum (pct change over n)`. All backward-looking.
- `regime.py`: `classify(bars) -> pd.Series` of `"bull_quiet" | "bull_volatile" | "bear_quiet" | "bear_volatile" | "unknown"`
  (trend: close vs 200-SMA; volatility: ATR% vs its own rolling 252-bar median). Backward-looking.
- `strategies/trend.py` `TrendStrategy(cfg: TrendConfig)`, `strategies/mean_reversion.py`
  `MeanReversionStrategy(cfg: MeanReversionConfig)`, `strategies/breakout.py` `BreakoutStrategy(cfg: BreakoutConfig)`.
- `strategies/__init__.py`: `build_strategies(cfg: StrategiesConfig) -> list[Strategy]` (enabled ones),
  `STRATEGY_NAMES = ("trend", "mean_reversion", "breakout")`.

### Backtesting (`quantbot/backtest/`)
- `engine.py`: `run_backtest(bars, strategy, sym: SymbolConfig, asset: AssetInfo, cfg: AppConfig, capital: float)
  -> BacktestResult(equity: pd.Series, trades: pd.DataFrame, costs_paid: float, exposure: pd.Series)`.
  Single symbol, long only, next-open execution with one-way costs from `cfg.costs.one_way_bps`, sizing via
  `risk.sizing.size_position` (with `cfg.risk`), protective + trailing stops checked against each bar's low
  (gap below stop fills at the open), max-hold exits, fractional rules from `AssetInfo`.
  `run_portfolio(bars_by_symbol, strategy, syms, assets, cfg, capital) -> BacktestResult` on a shared cash
  account enforcing `max_gross_pct`/`max_position_pct`.
  `buy_and_hold(bars, sym, asset, cfg, capital) -> BacktestResult`.
- `metrics.py`: `compute_metrics(result, periods_per_year) -> dict` — total & annualized return, Sharpe, Sortino,
  max drawdown, win rate, profit factor, expectancy per trade, exposure-adjusted return, costs, trade count.
- `walkforward.py`: rolling train/test windows, a small parameter grid per strategy chosen on train only,
  stitched out-of-sample equity and metrics.
- `report.py`: `run_backtest_report(cfg, out_dir, strategies=None) -> Path` and
  `run_walkforward_report(cfg, out_dir) -> Path` writing Markdown + JSON under `reports/`,
  including per-regime breakdown and buy-and-hold benchmarks, and the data provenance of every series.

### Risk and state
- `store.py` `class Store(path)` (SQLite, WAL, creates schema, safe across restarts):
  `record_order(Order)` (upsert by client_order_id), `get_order(cid)`, `open_orders()`, `orders(limit=200)`,
  `record_fill(Fill) -> bool` (False on duplicate), `fills(since=None)`,
  `record_equity(AccountSnapshot)`, `equity_curve() -> DataFrame[ts, equity, cash, gross]`,
  `set_position_meta(symbol, strategy, stop_price, entry_ts, highest_close)`, `get_position_meta(symbol)`,
  `clear_position_meta(symbol)`, `all_position_meta() -> dict`,
  `record_realized_pnl(ts, symbol, strategy, pnl)`, `realized_pnl_between(start, end) -> float`,
  `set_flag(name, value: str | None, reason="")`, `get_flag(name) -> str | None`,
  `record_event(level, kind, message, **fields)`, `events(limit=100) -> list[dict]`,
  `heartbeat(component, status, detail="")`, `heartbeats() -> list[dict]`,
  `record_regime(symbol, ts, regime)`, `latest_regimes() -> dict[str, tuple[datetime, str]]`.
- `risk/killswitch.py` `KillSwitch(store)`: `engage(reason, by)`, `release(by)`, `is_engaged() -> bool`,
  `status() -> dict`. Persisted in the store; survives restarts. Engaged = no new orders of any kind.
- `risk/limits.py` `RiskManager(cfg: RiskConfig, store, killswitch)`:
  `evaluate(snapshot: AccountSnapshot, now) -> BreakerState(trading_allowed, reasons, daily_realized,
  weekly_realized, unrealized, drawdown, peak_equity)`; daily breaker latches until the next New York trading day,
  weekly until the next ISO week, drawdown until a manual `reset_drawdown(by)`. Breakers block entries only;
  exits remain allowed (they reduce risk). Unrealized losses count toward drawdown and are reported.
  `approve_entry(snapshot, symbol, price, stop_price, asset, one_way_cost_bps, now) -> RiskDecision` (kill switch,
  breakers, max_open_positions, then `size_position`).

### Execution (`quantbot/broker/`, `quantbot/engine/`)
- `broker/sim.py` `SimulatedBroker(store?, prices, costs)`: deterministic fills at a supplied price with costs;
  supports partial fills and rejections for tests.
- `broker/alpaca_paper.py` `AlpacaPaperBroker(creds)`: `TradingClient(..., paper=True)` hard-coded; maps SDK models
  to `models.py`; `client_order_id` for idempotency (on duplicate, fetch the existing order).
- `broker/live_gate.py`: no live adapter. `assert_live_trading_not_enabled()`; documents the separate,
  manual authorization steps a future live deployment would need.
- `engine/runner.py` `run_cycle(cfg, broker, store, source, strategies, risk, now) -> CycleReport`:
  fetch → validate (skip symbol on failure) → signals on completed bars → exits first (signal, stop, trail,
  max-hold) → entries via `RiskManager.approve_entry` → submit with deterministic
  `client_order_id = f"{strategy}-{symbol}-{bar_date}-{side}"` (sanitized) after checking store and broker
  for an existing one → reconcile open orders, fills, positions → record equity, regimes, heartbeat → alerts.
- `engine/scheduler.py`: stocks act once per completed daily bar during the regular session
  (09:35–15:55 New York, weekdays, broker clock decides holidays); crypto once per UTC day; loop with
  `poll_seconds`, crash-safe (each cycle is idempotent).
- `quantbot/__main__.py` CLI: `backtest`, `walkforward`, `verify-assets`, `paper-once`, `paper-loop`, `status`,
  `kill`, `unkill`, `reset-drawdown`.

### Monitoring
- `alerts.py` `Alerter(cfg: AlertConfig, store)`: `send(level, kind, message, **fields)` → store event + log +
  optional webhook (URL from the env var named in config), deduplicated within 30 minutes per kind.
- `dashboard/app.py` Streamlit: equity, cash, positions, open orders, daily and total P&L, max drawdown,
  strategy performance (latest report JSON), trade history, regimes, breaker status, heartbeats/health,
  and an emergency stop button that engages the kill switch.

## As built (phase 4 additions)

- `RiskManager(cfg, store, killswitch, initial_capital=None)`; also `approve_exit(snapshot, symbol, now)`
  (blocked only by the kill switch) and `reset_drawdown(by, now=None)` (measures the peak from the reset).
  `approve_entry` also rejects when the store has an open BUY order for the symbol, so order statuses in the
  store must be kept current by reconciliation.
- `KillSwitch(store, kill_file=None)`; the kill file path comes from `QUANTBOT_KILL_FILE` (default `state/KILL`,
  relative to the working directory). The file overrides `release()`.
- Store extras: `realized_pnl_by_strategy()`, `flag_history(name)`, `get_flag_info`, `equity_at_or_before`,
  `first_equity_between`, `peak_equity(since=None)`, `latest_equity_positions`, `realized_pnl_records`.
- Flag names: `kill_switch`, `breaker_daily` (NY date), `breaker_weekly` (ISO week), `breaker_drawdown`,
  `drawdown_reset_at`.

## As built (phases 3, 5 and 6 additions)

- `run_backtest(..., regimes=None, data_source=None, trade_start=None, close_at_end=False)`; `BacktestResult`
  also has `meta` and `open_positions`. Backtests enforce sizing and exposure caps but not the circuit breakers.
- `quantbot.strategies.get_strategy(name, cfg)`; `data.sources.write_bars_csv`, `alpaca_bars_to_frame`.
- `run_cycle(cfg, broker, store, source, strategies, risk, now, alerter=None, asset_classes=None)`; it refuses a
  broker whose `is_paper` is false. Fills are derived from changes in each order's `filled_qty` (alpaca-py's
  `TradingClient` has no account-activities call). Store flags `entry_plan:<cid>` (stop distance for the fill)
  and `scheduler_last_*` (run markers). Equity is recorded at the start and end of every cycle.
- Heartbeat components: `runner`, `scheduler`. `Alerter(cfg, store, sender=None, clock=None)` with
  `alert_last:<kind>` dedupe flags. Dashboard: `streamlit run quantbot/dashboard/app.py -- --config <toml>
  [--reports-dir reports]`; pure data functions in `dashboard/data.py`.
- Staleness defaults: stocks 120h, crypto 50h, both measured from the bar's open time.
