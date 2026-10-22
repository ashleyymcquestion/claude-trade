# Coinbase bot (hourly, paper trading)

A scheduled Claude session follows this every hour at :07 (the Robinhood run is at :37).
**The code decides every trade (`trader/coinbase.py`, `config.coinbase.toml`); the session only fetches
data, runs the planner and logs.** Research behind it: `docs/research/crypto-coins.md`.

- Portfolio `8350d6dc-8eac-5d98-a186-c4d0fc3ebbba` only. Quote currency USDC only.
- Strategies, $5 of paper money each (the real account has $10 USDC):
  - `btc_trend`: hold BTC only while BTC's weekly (Sunday) close is above its 10-week average.
  - `momentum`: while BTC is above its 10-week average, hold the 2 coins with the best 4-week return
    that are also above their own 10-week average. Candidates: BTC, ETH, SOL and the old coins Coinbase
    trades against USDC (ADA, XLM, ALGO, CRV, XCN, SKY).
- Orders are post-only limit orders at the best bid/ask (0.5% maker fee, not 0.9% for market orders).
  Paper orders fill only when a later 15-minute candle trades through the limit; an unfilled order is
  moved to the new best price after 6 hours.

## Hard rules for the session
- **Mode is paper. Never call `coinbase_orders_create`, `coinbase_orders_edit`, `coinbase_orders_cancel`,
  `coinbase_convert_*`, `coinbase_transfer` or `coinbase_x402_pay`.** The `real_calls` and leftover
  `real_call` entries in the output are what a real order *would* be. They are placed only when the user
  says yes to that specific order in a chat, never by the scheduled run.
- Never transfer or withdraw anything.
- Leftover coins (XLM, ALGO, XCN, SKY, SPELL, ASM, ADA, CRV) are only reported on. A sell proposal means
  "below its 10-week average"; it waits for the user.
- To stop: disable the scheduled task, or `kill_switch = true` in `config.coinbase.toml` (no new orders).

## Each run
1. `git fetch origin claude/clever-archimedes-ixq37a && git checkout claude/clever-archimedes-ixq37a && git pull origin claude/clever-archimedes-ixq37a`,
   then `pip install -r requirements.txt` if pandas is missing.
2. `python -m trader cb-needs` prints what to fetch. Write every file below outside the repo.
3. Fetch exactly that and write `cb_in.json` (see the docstring at the top of `trader/coinbase.py`):
   - `"now"`: the current UTC time (ISO).
   - `"best_bid_ask"`: `coinbase_products_best_bid_ask(product_ids=<best_bid_ask list>)`, as returned.
   - `"candles_15m"`: for each product listed: `coinbase_products_candles(product_id, granularity="15m",
     start=<its start>)`, as returned, keyed by product id.
   - only if `candles_1d` is not null (once a week, after Sunday's candle closes): for each product,
     `coinbase_products_candles(product_id, granularity="1d", start=<start>)`, keyed by product id. Only the
     Sunday candles are used, so you may keep just those (each with its `start` and `close`).
   - only if `balance` is true: `coinbase_balance(portfolio_id="8350d6dc-8eac-5d98-a186-c4d0fc3ebbba")`, as returned.
4. `python -m trader cb-run --input cb_in.json`. It updates the paper ledger `data/coinbase/paper.json`
   and appends the entry to `journal/coinbase-log.md`. If it fails, fix nothing in the strategy: append a
   short note with the error to the journal instead.
5. Commit `data/coinbase/paper.json` and `journal/coinbase-log.md` ("Coinbase hourly run <date> <time> PT: ...")
   and push to `claude/clever-archimedes-ixq37a` (retry up to 4 times on network errors).
6. If the output has new `leftovers` proposals or the paper results look worth acting on, say so in the
   final message. Don't place anything.

`python -m trader cb-score` prints the ledger's scoreboard and recent paper fills.
