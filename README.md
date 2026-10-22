# claude-trade

> **New: [Coinbase bot](COINBASE.md)**, two weekly crypto strategies (BTC trend, coin momentum with a BTC
> filter) paper traded hourly on Coinbase's own prices with limit orders. Log: `journal/coinbase-log.md`.

> **New: [quantbot](docs/QUANTBOT.md)**, a research + paper-trading system with three strategies,
> walk-forward backtests, circuit breakers, a kill switch and a Streamlit dashboard. The section
> below describes the original hourly paper trader in `trader/`, which is unchanged.

An auto-trader for **stocks and crypto** that runs on GitHub Actions every hour, including
overnight, against an **Alpaca paper (fake-money) account**.

> Paper trading only. The broker client is hard-wired to Alpaca's paper endpoint, so live
> keys won't work. Backtests and paper results do not guarantee real-money results.

## Strategy

It uses the same building blocks as managed-futures and quant funds (AQR, Man AHL and others):

1. **Trend following**: own an asset only while it is above its ~200-day average.
2. **Time-series momentum**: check 1, 3, 6 and 12-month returns; more positive windows = bigger position.
3. **Risk parity**: size each position by its volatility, so Bitcoin doesn't swamp bonds.
4. **Volatility targeting**: scale the whole portfolio to ~12% annual volatility. In
   turbulent markets it holds more cash.

The default universe is diversified on purpose: SPY, QQQ, IWM, EFA, TLT (bonds), GLD (gold),
BTC, ETH and SOL. You can edit everything in [`config.toml`](config.toml).

### Risk controls
| Control | Default |
|---|---|
| Leverage | none (max 100% invested) |
| Max per asset | 25% |
| Max crypto total | 30% |
| Daily loss limit | stop trading for the day at -3% (`flatten` option sells everything) |
| Kill switch | repo variable `KILL_SWITCH=1`, or `kill_switch = true` in config |
| Duplicate orders | symbols with an open order are skipped |

## Setup (about 10 minutes)

1. **Create a free Alpaca account** at <https://alpaca.markets>. Switch to **Paper Trading**
   and generate API keys. Paper accounts can trade both stocks and crypto.
2. **Add the keys to GitHub**: repo → *Settings → Secrets and variables → Actions → New
   repository secret*. Add `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.
3. **Make sure the code is on the default branch.** GitHub only runs scheduled workflows
   from the default branch, so merge this branch into it.
4. **Do a dry run**: *Actions → paper-trade → Run workflow*, leaving "dry run" checked. The
   run summary shows the orders it *would* place.
5. After that, it trades on its own every hour at :17.

**Each morning:** open *Actions → paper-trade* and click a run to see equity, targets and
orders. Full JSON logs are attached as artifacts. Your Alpaca paper dashboard shows positions and P&L.

**To stop it:** add the repository *variable* `KILL_SWITCH` = `1` (Settings → Secrets and
variables → Actions → Variables), or disable the workflow.

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export ALPACA_API_KEY=... ALPACA_SECRET_KEY=...

python -m trader backtest --start 2018-01-01   # historical test, with costs
python -m trader trade --dry-run               # see what it would do right now
python -m pytest -q                            # tests
```

The backtest reports results for the first and second half of the period separately. If
the second half falls apart, the strategy is probably overfit. Change settings sparingly,
because every tweak tuned to past data makes the backtest look better than reality.

## Before you ever consider real money

- Paper trade for at least 2–3 months and compare results with the backtest.
- Paper fills are optimistic: no slippage, and the queue position is ideal.
- Live trading would need deliberate code changes. That's intentional.

## Notes

- Stock prices come from Alpaca's free IEX feed. Crypto prices come from Alpaca crypto data.
- GitHub pauses scheduled workflows after 60 days with no repository activity.
- An hourly run uses about 1–2 Actions minutes, which fits the free tier for private repos.
