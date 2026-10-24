# Prompt for the hourly Robinhood scheduled task (fresh session each run)

Create it at claude.ai → Claude Code → scheduled tasks (routines): repository ashleyymcquestion/claude-trade,
the Robinhood connector turned on, schedule "37 * * * *" (every hour at :37), "new session each run".
Then turn off the old "Robinhood crypto run" task so the two don't both trade.

```
Run the hourly Robinhood run for the repository ashleyymcquestion/claude-trade, authorized by the user. This is a fresh session: nothing from earlier runs is in memory, so everything you need is in the repo and in ROBINHOOD.md.

1. Get the repo on the right branch. If /home/user/claude-trade (or another checkout of ashleyymcquestion/claude-trade) exists, cd into it; otherwise clone https://github.com/ashleyymcquestion/claude-trade. Then: git fetch origin claude/jolly-fermi-tvlmpb && git checkout claude/jolly-fermi-tvlmpb && git pull origin claude/jolly-fermi-tvlmpb
2. Read ROBINHOOD.md on that branch and follow its steps exactly: Setup, Part A (crypto: trader rh-plan), Part B (stocks: quantbot rh-stock-pending / rh-stock-plan / rh-stock-record), Part B2 (SpaceX test: trader rh-spcx-plan / rh-spcx-record), Part B3 (watchlab paper tests: once a day via trader watch-due; gap and Dual Thrust checks every market hour), Part B4 (options lab paper tests, once a day after B3: trader opt-step), 17j (XRP paper test, once a day), Part C (journal, results page, commit). Write the JSON input files in a temporary folder outside the repo. Robinhood tools are mcp__robinhood__*; Artifact/ArtifactData/PushNotification load with ToolSearch. Account: 746480458 only. Skip Part B and B2 when the US stock market is closed (crypto still runs). Keep the run lean: only fetch what ROBINHOOD.md asks for. If the Robinhood tools are not available in this session, place nothing, push a "Robinhood: problem" notification saying the connector is missing, and stop.

Hard rules:
- The code decides the trades. Place ONLY the orders the planners print (`trader rh-plan` for crypto, `quantbot rh-stock-plan` for stocks, `trader rh-spcx-plan` for SPCX), exactly as printed (symbol, side, type, dollar_amount/quantity; for stocks also market_hours and the printed ref_id), each previewed/reviewed first. Never add, resize, retry with different values, or invent orders. If a planner errors or prints no orders, place nothing for that part.
- Watchlab, the options lab and the XRP test are paper only: never place a real order (stock, option or crypto) for their ideas, and never call place_option_order. If a scoreboard says a source is ready_for_real_money, tell the user and ask; don't trade it.
- Crypto: only BTC, ETH, SOL. Stocks: only NVDA, AMD, PLTR, QQQ, plus SPCX only as printed by rh-spcx-plan. Cash only, never margin. Never touch any other holding, and never cancel or change any order except the stock cancellations `rh-stock-plan` prints.
- If a stock review shows any order_checks alert, don't place that order; log the alert.
- If config.robinhood.toml has kill_switch = true, or the stock bot reports halted (kill switch / loss limit), or trade approvals block an order, place nothing for that part and just log it.
- If anything is unclear or a tool returns unexpected data, place nothing, log the problem, and report it.
- Always run `rh-stock-record` after placing stock orders, so stops and loss limits are tracked; always run `rh-spcx-record` once an SPCX order has filled.
3. Commit the files ROBINHOOD.md step 20 lists (data/proxy_history.csv, data/bars/, data/robinhood-stocks.sqlite3, data/spcx-test.json, data/watchlab/, data/optionslab/, data/xrppaper/, data/yahoo/*-USD.csv, data/robinhood-results.csv, journal/robinhood-log.md) and push with: git push -u origin claude/jolly-fermi-tvlmpb (retry on network errors up to 4 times with backoff). Never end a run without pushing: a run that doesn't push loses the bot's records. Do not open a pull request.

Gains to report (use real numbers only; never estimate a fill you didn't see):
- Each SELL: realized gain = proceeds - cost of what was sold. Crypto cost = quantity sold x (direct_cost_basis / direct_quantity) from get_crypto_positions taken BEFORE the sell. Stock gain: the realized P&L rh-stock-record recorded (see `python -m quantbot status --config configs/robinhood_stocks.toml`, recent events "realized"). SPCX gain: sell proceeds minus the buy cost in data/spcx-test.json.
- Each BUY: amount, fill price, and (stocks/SPCX) the stop price.
- Today: account value now minus yesterday's row in data/robinhood-results.csv. Since start: account value now minus the first row ($4.16 on 2026-10-15), in dollars and %.
- Open positions: unrealized gain = value now - cost, per holding.

4. Notify the user: if ANY real order was placed, send a push notification with the PushNotification tool. Title like "Robinhood: 2 trades". Body, short: each trade as "Sold BTC $0.65 (gain -$0.02)" / "Bought NVDA $1.00 @ $217.10, stop $203.50", then "Today +$0.03 | Since start +$0.12 (+2.9%)". If no orders were placed, don't push. Also push (title "Robinhood: problem") if a planner errored, an order was rejected, or a loss limit/kill switch is active.

Final message: 4-6 short lines with account value, today's change and change since start; crypto orders, fills and gains (or "no trades"); stock orders, fills, stops and gains (or "no trades" with the main reason); SPCX test status and price; on the daily paper run, one line each for watchlab, the options lab and the XRP test; any errors; and the results page link https://claude.ai/artifact/HZUowfm349k9HEcWYNHMST
```
