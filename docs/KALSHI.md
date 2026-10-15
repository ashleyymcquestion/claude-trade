# kalshibot: Kalshi 15-minute BTC up/down

A bot for Kalshi's **"BTC price up in next 15 mins?"** markets (series `KXBTC15M`). It runs
against Kalshi's **demo (fake-money) exchange** by default.

> Nothing here promises a profit. These markets are close to coin flips, you pay a fee on every
> trade, and professional market makers on the other side have faster price feeds. Treat the
> bot as a way to **measure** whether there is an edge after fees before you risk any money.

## How the markets work

* A new market opens every 15 minutes, 24/7.
* **Target price** = the 60-second average of the CF Benchmarks BRTI index just before the open.
* It resolves **YES (up)** if the 60-second average just before the close is **at or above**
  the target, otherwise **NO (down)**. A winning contract pays $1.

## What the bot does

Every 15 seconds (`poll_seconds`):

1. **Settles** its earlier trades once Kalshi publishes the result, and records P&L after fees.
2. **Checks the brakes:** kill switch, trades-per-day limit and daily loss limit.
3. Finds the market closing soonest. It trades **at most once per market**.
4. Gets BTC's price and the last hour of 1-minute prices from Coinbase (public, no key).
5. **Estimates the chance of UP.** It treats BTC over a few minutes as a random walk, using
   volatility from the last hour and adjusting for the 60-second settlement average.
6. **Compares that to the YES and NO prices**, minus Kalshi's fee. It buys only if the expected
   edge is at least `min_edge` (5¢ per contract), and only between 10 minutes and 90 seconds
   before the close.
7. **Skips "edges" above `max_edge` (25¢).** An edge that big almost always means Coinbase's
   price differs from the settlement index, not that the market is wrong.
8. Sends a **limit order at the ask, immediate-or-cancel**. It never chases a price or leaves
   orders resting on the book.

All decisions (including skips) and all trades are stored in `state/kalshi.sqlite3`.

## Safety controls

| Control | Default | Where |
|---|---|---|
| Exchange | demo (fake money) | `run.environment` |
| Max contracts per trade | 5 | `risk.max_contracts_per_trade` |
| Max cost per trade | $5 | `risk.max_cost_per_trade_usd` |
| Max trades per day | 20 | `risk.max_trades_per_day` |
| Daily loss limit | $10, counting open positions as lost until they settle | `risk.daily_loss_limit_usd` |
| Kill switch | create the file `state/KILL`, or set `KILL_SWITCH=1` | shared with quantbot |
| Real money | needs **three** separate opt-ins (below) | |

## Setup (demo exchange)

1. **Create a demo account** at <https://demo.kalshi.co>. It's separate from a real Kalshi
   account, and the money is fake.
2. **Create an API key** there (Account → API keys). Kalshi shows a **Key ID** and downloads a
   **private key file** (`.pem`). Keep the file private: anyone who has it can trade your account.
   Never commit it or paste it into chat.
3. **Install:**
   ```bash
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements-quantbot.txt
   cp .env.example .env      # set KALSHI_DEMO_KEY_ID and KALSHI_DEMO_PRIVATE_KEY_PATH
   set -a; . ./.env; set +a
   ```
4. **Check it works:**
   ```bash
   python -m kalshibot markets      # lists the open 15-minute markets (no key needed)
   python -m kalshibot balance      # confirms your API key works
   python -m kalshibot once         # one decision, printed as JSON
   ```
   If `markets` prints nothing, the demo exchange may not list this series right now. Use
   `--dry-run` with `environment = "prod"` (next section) to watch real markets without trading.
5. **Run it:** `python -m kalshibot run`. Check results with `python -m kalshibot status`.

## Dry run on real markets (no orders, no key)

If you want to score the strategy against **real** market prices without placing orders, set
`environment = "prod"` and `dry_run = true` in a copy of the config, then run
`python -m kalshibot run --config configs/my-kalshi.toml`. The bot only reads public market data,
records the trades it *would* have made, and settles them against the real results.
Dry-run fills are optimistic: they assume the whole order filled at the ask.

## Keeping it running (cheap server)

GitHub Actions is **not** suitable: scheduled runs often start 5–20+ minutes late. Use any small
always-on Linux machine (a ~$5/month VPS, or a Raspberry Pi). Example systemd service
(`/etc/systemd/system/kalshibot.service`):

```ini
[Unit]
Description=kalshibot
After=network-online.target

[Service]
WorkingDirectory=/home/you/claude-trade
EnvironmentFile=/home/you/claude-trade/.env
ExecStart=/home/you/claude-trade/.venv/bin/python -m kalshibot run
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Then run `sudo systemctl enable --now kalshibot`, and watch it with `journalctl -u kalshibot -f`.
To stop trading immediately, run `touch state/KILL`; delete the file to resume.

## Real money (only after the demo results justify it)

Look at `python -m kalshibot status` after **at least a few hundred settled trades**. If P&L
after fees isn't clearly positive, real money will most likely lose too. If you still want to go
live, the bot needs all three of these, so it can't happen by accident:

1. `environment = "prod"` and `allow_live = true` in the config,
2. `KALSHI_LIVE_CONFIRM=I-accept-real-money-losses` in the environment, and
3. `--live` on the command line.

Real-money keys go in `KALSHI_PROD_KEY_ID` / `KALSHI_PROD_PRIVATE_KEY_PATH`, separate from the demo
keys. Start with the smallest limits and a balance you can afford to lose entirely.

## Things to verify against Kalshi's current docs

These were built from Kalshi's published API docs and contract terms, but couldn't be checked
against the live API from the build environment. The demo exchange is the place to confirm them:

* The market's `floor_strike` field holds the target price. If it doesn't, the bot skips every
  market with "no target price available". You can set `allow_estimated_strike = true` to use a
  rougher estimate from Coinbase instead.
* The taker fee formula (`ceil(0.07 × contracts × price × (1 − price))`). Compare it with the fees
  on your first fills.
* `time_in_force = "immediate_or_cancel"` is accepted for orders.
