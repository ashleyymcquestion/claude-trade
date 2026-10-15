# quantbot: research and paper-trading bot

A systematic trading research and **paper-execution** system: three rule-based strategies,
an event-driven backtester with walk-forward validation, an independent risk manager with
circuit breakers and a persistent kill switch, an Alpaca **paper** adapter, a scheduler, and a
Streamlit monitoring dashboard.

> There is no live-trading code path. Config refuses `mode = "live"`, the Alpaca adapter is
> hard-wired to `paper=True`, and `broker/live_gate.py` refuses to start if anything tries to
> enable live trading. Nothing here promises profits; it exists to measure whether a strategy
> has an edge.

Module contracts: [ARCHITECTURE.md](ARCHITECTURE.md) · Problems: [TROUBLESHOOTING.md](TROUBLESHOOTING.md) ·
Before running unattended: [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)

## Install

```bash
python3.12 -m venv .venv && . .venv/bin/activate      # Python 3.12+ (tested on 3.13)
pip install -r requirements-quantbot.txt
cp .env.example .env                                   # then fill in PAPER keys; never commit .env
set -a; . ./.env; set +a                               # load them into the shell
```

## Commands

Run everything from the repo root (the kill file `state/KILL` and the database are relative to it).

| Task | Command |
|---|---|
| Tests | `python -m pytest tests` |
| Full-period backtest (all strategies, all symbols, benchmarks) | `python -m quantbot backtest --config configs/example.toml` |
| Walk-forward / out-of-sample | `python -m quantbot walkforward --config configs/example.toml` |
| Check symbols are tradable at Alpaca paper | `python -m quantbot verify-assets --config configs/example.toml` |
| One trading cycle (dry run with `mode = "sim"`) | `python -m quantbot paper-once --config configs/example.toml` |
| Paper trading loop (set `mode = "alpaca_paper"`) | `python -m quantbot paper-loop --config configs/local.toml` |
| Status: account, breakers, kill switch, health | `python -m quantbot status --config configs/example.toml` |
| Emergency stop / release | `python -m quantbot kill --reason "..."` · `python -m quantbot unkill` (or `touch state/KILL`) |
| Reset the drawdown breaker (manual by design) | `python -m quantbot reset-drawdown` |
| Dashboard | `streamlit run quantbot/dashboard/app.py -- --config configs/example.toml` |

Reports are written to `reports/` as Markdown + JSON (`latest_backtest.json` feeds the dashboard).

## Markets

| Config symbol | Trades at Alpaca as | Research data in this repo |
|---|---|---|
| BTC/USD, ETH/USD | crypto, 24/7, fractional | **IBIT / ETHA ETF proxies** (2024+ only; Alpaca's crypto history was unreachable from the build sandbox) |
| NVDA, AMD, PLTR, QQQ | US equities, regular session, fractional market orders | Robinhood daily bars, split-adjusted, 2015+ (PLTR from its 2020 listing) |

Support at Alpaca has **not been verified** yet (the sandbox could not reach Alpaca). Run
`verify-assets` with paper keys; it exits non-zero and names any symbol that isn't tradable,
and you should set `enabled = false` for it in the config.

## How it trades

Each cycle (`engine/runner.py`): reconcile orders and fills with the broker → snapshot account →
evaluate breakers → per symbol: fetch daily bars, drop the incomplete bar, validate (gaps,
staleness, bad prices — invalid data means no trade) → exits first (strategy exit, protective
stop, trailing stop, max hold) → entries (first enabled strategy with a signal owns the symbol;
risk manager approves and sizes) → submit market orders with a deterministic `client_order_id`
(`strategy-symbol-barDate-side`), recorded locally *before* the broker call, so a crash or
retry can never place a duplicate → record equity, regimes, heartbeats, alerts.

Scheduling (`engine/scheduler.py`): stocks once per completed daily bar between 09:35 and 15:55
New York on days the broker clock says the market is open; crypto once per UTC day after
00:05. Run markers are stored, so a restart doesn't run a day twice.

Stops are checked once per cycle against the latest completed daily bar, not intraday. A gap
through a stop is filled at the next market order's price, which can be well below the stop.

## Strategies

| Name | Entry (all must hold) | Exit | Stops |
|---|---|---|---|
| `trend` | close > 200-SMA, 20-EMA > 50-EMA, 20-bar momentum > 0, relative volume ≥ 0.8, ATR% ≤ 8% | 20-EMA < 50-EMA or close < 200-SMA | 2.5×ATR initial, 3×ATR trailing |
| `mean_reversion` | RSI(2) < 10 and (close < lower Bollinger or z-score < −2), only above the 200-SMA | RSI > 60 or close > Bollinger mid, or 10 bars | 2×ATR |
| `breakout` | close > prior 20-bar high, ATR% in [0.5%, 10%], relative volume ≥ 1.2 | close < prior 10-bar low | 2×ATR initial, 3×ATR trailing |

All parameters are in the config. Signals use only completed bars; tests check this by
recomputing on truncated histories.

## Risk controls (`risk/`)

| Control | Default | Behaviour |
|---|---|---|
| Risk per trade | 0.5% of equity | stop distance + round-trip costs sets the size |
| Single position | ≤ 25% of equity | |
| Combined positions | ≤ 50% of equity | at entry |
| Daily realized loss | 1.5% | blocks entries until the next New York day; exits still allowed |
| Weekly realized loss | 4% | blocks entries until next ISO week |
| Drawdown (incl. unrealized) | 8% from peak | blocks entries until a manual `reset-drawdown` |
| Kill switch | off | blocks **all** orders; persisted in SQLite and via `state/KILL`; survives restarts |
| Margin, shorting, derivatives | forbidden | config validation rejects enabling them |

Sizing (`risk/sizing.py`) is the same function in backtests and paper trading, and also enforces
cash, broker minimum notional, minimum order size, unit step and whole shares where needed.

## First research results (real data, assumed costs)

From `reports/backtest_20261015-204855.md` and `reports/walkforward_20261015-204925.md`:

- On the four stocks, `trend` and `mean_reversion` were profitable **out of sample** in
  walk-forward (profit factor about 2), but with 0.5% risk per trade and ATR stops the bot is
  invested only about 2–9% of the time. Out-of-sample returns were +13% to +18% over 2017–2026 per
  stock for `trend`, with drawdowns of about 2–3%. Buy-and-hold returned far more with 35–85%
  drawdowns.
- `breakout` was weaker and inconsistent; on QQQ it was roughly break-even.
- Crypto (ETF proxies, 2024+) has too few trades to conclude anything.
- **Selection bias:** NVDA, AMD and PLTR were chosen in 2026 knowing they were winners. That
  flatters every result here. Treat these numbers as an upper bound.
- The backtester enforces sizing and exposure limits but does not simulate the circuit breakers.
  Breakout's portfolio drawdown (−8.6%) would have tripped the 8% breaker.

## Layout

```
quantbot/   config.py models.py logging_setup.py store.py alerts.py indicators.py regime.py
            data/ (sources, validate)  strategies/ (trend, mean_reversion, breakout)
            backtest/ (engine, metrics, walkforward, report)  risk/ (sizing, limits, killswitch)
            broker/ (base, sim, alpaca_paper, live_gate)  engine/ (runner, scheduler)
            dashboard/ (data, app)  __main__.py
configs/example.toml   data/bars/*.csv   reports/   scripts/import_robinhood_bars.py   tests/quant/
```

## Live on Robinhood (stocks sleeve)

Since 2026-10-15, quantbot also trades real money on the Robinhood agentic account, through the
scheduled Claude session in [ROBINHOOD.md](../ROBINHOOD.md) (Robinhood has no API keys; the
session uses the Robinhood connector). `quantbot/robinhood_stocks.py` runs the same `run_cycle`
with `RobinhoodPlanBroker`, which places nothing itself: it prints the orders, and the session
places exactly those, then reports the results back with `rh-stock-record`.

- Config: `configs/robinhood_stocks.toml`. Sleeve = 50% of the account (the BTC/ETH strategy has the
  other 50%, `crypto_sleeve` in `config.robinhood.toml`). Trend + mean reversion on NVDA, AMD, PLTR, QQQ.
- Small-account sizing: the normal 0.5% risk per trade would be ~1 cent, so `min_order_risk_cap = 0.04`
  allows Robinhood's $1 minimum when a stop-out at $1 loses at most 4% of the sleeve (~8 cents).
  Limits: daily 3%, weekly 6%, drawdown 15% of the sleeve; at most 2 positions.
- State: `data/robinhood-stocks.sqlite3`, committed every run. `python -m quantbot status --config
  configs/robinhood_stocks.toml` and the dashboard read it.
- The sleeve's equity is 50% of the whole account's value, so crypto moves also move it (and its
  drawdown). Deposits raise it.
