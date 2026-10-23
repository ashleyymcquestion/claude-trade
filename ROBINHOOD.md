# Robinhood daily run (agentic account)

A scheduled Claude session follows this once each morning (6:31 AM Pacific, just after the US
stock market opens). **The code decides every trade; the session only gathers inputs and places
exactly the orders the planners print.** Never add, resize, retry with different values, or
invent orders.

- Account: the agentic account (nickname "MCP"); `account_number` = `rhs_account_number` = `746480458`.
  It is a "limited margin" account, but both strategies use **cash only**.
- The account is split in two halves (by total account value):
  - **Crypto (Part A):** BTC, ETH, SOL with `trader rh-plan` (`config.robinhood.toml`, `crypto_sleeve = 0.5`).
    Signals from the IBIT / ETHA / BSOL ETF history in `data/proxy_history.csv`.
  - **Stocks (Part B):** NVDA, AMD, PLTR, QQQ with quantbot (`configs/robinhood_stocks.toml`, `sleeve = 0.5`):
    trend + mean-reversion strategies, ATR stops, loss limits, kill switch. Its state lives in
    `data/robinhood-stocks.sqlite3` (committed every run so it survives fresh containers).
- Never trade any other symbol (the one exception is the SPCX test in Part B2). If any other holding or open order shows up, leave it alone.
- To stop:
  - everything: disable the scheduled task, or turn trade approvals on in the Robinhood app;
  - crypto only: `kill_switch = true` in `config.robinhood.toml`;
  - stocks only: `python -m quantbot kill --config configs/robinhood_stocks.toml --reason "..."` and commit
    `data/robinhood-stocks.sqlite3` (release with `unkill`).

## Setup

1. `git fetch origin claude/jolly-fermi-tvlmpb && git checkout claude/jolly-fermi-tvlmpb && git pull origin claude/jolly-fermi-tvlmpb`
   then `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-quantbot.txt -r requirements-options.txt`.
2. `get_portfolio(account_number="746480458")` → keep `total_value` (the account value), `cash` and
   `crypto_buying_power.buying_power`. Write all JSON files below outside the repo.

## Part A: crypto

3. Gather: `get_crypto_positions(rhs_account_number="746480458")` (BTC / ETH / SOL `quantity`,
   `quantity_transferable`), `get_crypto_quotes(["BTC-USD","ETH-USD","SOL-USD"])` (`mark_price`),
   `get_crypto_orders(rhs_account_number="746480458", state_group="open")` (`currency_code` of each).
4. Write `state.json`:
   `{"crypto_buying_power": <buying_power>, "account_value": <total_value>,
     "positions": {"BTC": {"quantity": "...", "quantity_transferable": "..."}, ...},
     "marks": {"BTC": ..., "ETH": ..., "SOL": ...}, "open_order_symbols": [...]}`
5. `get_equity_historicals(symbols=["IBIT","ETHA","BSOL"], interval="day", start_time=<last date in
   data/proxy_history.csv minus 10 days>)`; save the answer exactly as returned to `bars.json`.
6. `python -m trader rh-plan --state state.json --bars bars.json` (also updates the history CSV).
7. For each entry in `orders`, in order: `preview_crypto_order` with exactly its symbol, side, type and
   `dollar_amount` or `quantity`, then `place_crypto_order` with the same values and a fresh UUID `ref_id`.
   If a preview fails, skip that order and record the error.
8. Check fills with `get_crypto_orders(rhs_account_number="746480458", created_at_gte=<run start>)`.

## Part B: stocks

9. `python -m quantbot rh-stock-pending --config configs/robinhood_stocks.toml` lists stock orders from
   earlier runs that are still open in the bot's records. For each, look up its current state with
   `get_equity_orders(account_number="746480458", order_id=<robinhood_order_id>)` (or by symbol and
   `created_at_gte` if the id is missing).
10. Gather: `get_portfolio` again (crypto trades changed it) → `total_value`, `cash`;
    `get_equity_positions(account_number="746480458")` → NVDA / AMD / PLTR / QQQ `quantity`,
    `average_buy_price`; `get_equity_quotes(["NVDA","AMD","PLTR","QQQ"])` → last trade price as `mark`.
11. Write `stock_state.json`:
    `{"account_value": <total_value>, "cash": <cash>,
      "positions": {"NVDA": {"quantity": "...", "average_buy_price": "...", "mark": ...}, ...},
      "order_updates": {"<client_order_id from step 9>": {"robinhood_order_id": "...", "state": "<Robinhood
         order state>", "filled_quantity": "<cumulative filled shares>", "average_price": "...",
         "updated_at": "<ISO time>"}}}`
    Only include the four symbols. Leave `order_updates` empty (`{}`) if step 9 listed nothing.
12. `get_equity_historicals(symbols=["NVDA","AMD","PLTR","QQQ"], interval="day", start_time=<last date in
    data/bars/NVDA.csv minus 10 days>)`; save the answer exactly as returned to `stock_bars.json`.
13. `python -m quantbot rh-stock-plan --config configs/robinhood_stocks.toml --state stock_state.json --bars stock_bars.json`
    (also merges the new bars into `data/bars/`).
14. For each entry in `cancel`: `cancel_equity_order` for its `robinhood_order_id`.
15. For each entry in `orders`, in order: `review_equity_order(account_number="746480458", symbol, side,
    type="market", market_hours="regular_hours", and exactly its dollar_amount (buys) or quantity (sells))`.
    If `order_checks` is not empty, don't place it; note the alert. Otherwise `place_equity_order` with the
    same values and `ref_id` = the order's `ref_id` (deterministic: a retry can't double it). Keep each
    order's `client_order_id` → Robinhood order `id`. If the answer is an approval instead of an order,
    treat it as not placed (state "cancelled", filled 0) and say so.
16. Wait about 30 seconds, then gather again: `get_portfolio`, `get_equity_positions`, `get_equity_quotes`,
    and `get_equity_orders(account_number="746480458", created_at_gte=<run start>)`. Write `stock_after.json`
    like step 11, with `order_updates` for every order placed in step 15, every cancel from step 14, and
    anything from step 9.
17. `python -m quantbot rh-stock-record --config configs/robinhood_stocks.toml --state stock_after.json`
    (records fills, sets stops, records realized P&L, updates the loss limits).

## Part B2: SpaceX test position (SPCX)

A one-off test outside both strategies (`trader/spcx_test.py`, ledger `data/spcx-test.json`): buy
$1.00 of SPCX once free cash reaches $1.00 during regular hours, then sell all of it once the price
is 5% above the fill (the ledger's `target`, $161.05), which arms a trailing exit: from then on it holds while the price climbs and sells once it falls 3% below the highest price seen, never below the +5% target. `rh-spcx-plan` saves the peak to the ledger itself. Since Oct 22 there is no stop-loss: the user wants it sold
only at a profit. Fractional shares only take market orders, so nothing rests at the broker; this step
checks the target every run. To turn it off, set `"enabled": false` in the ledger.

17a. `get_portfolio` → `cash` (after Parts A and B); `get_equity_positions` → SPCX `quantity` (0 if none);
     `get_equity_quotes(["SPCX"])` → last trade price as `mark`; `get_equity_orders(account_number="746480458",
     symbol="SPCX", created_at_gte=<7 days ago>)` → `open_order` = true if any is not filled/cancelled/rejected/failed.
     Write `spcx_state.json`: `{"cash": "...", "mark": ..., "quantity": "...", "open_order": false}`.
17b. `python -m trader rh-spcx-plan --state spcx_state.json`. For its order, if any: `review_equity_order` with
     exactly its values (an `order_checks` alert → don't place; log it), then `place_equity_order` with the same
     values and its `ref_id`.
17c. If an order was placed, wait about 30 seconds and look it up with `get_equity_orders(order_id=...)`. Once it
     is `filled`, run `python -m trader rh-spcx-record --side <buy|sell> --order-id <id> --quantity
     <cumulative_quantity> --price <average_price> --at <last_transaction_at>`. If it isn't filled yet, the next run
     sees `open_order` and waits; record it then.

## Part B3: watchlab (paper only, once a day)

Paper-tests ideas from top gainers, unusual volume, politician filings and social buzz
(`trader/watchlab.py`, notes in `docs/research/watchlab-strategies.md`). **Places no orders.**

17d. `python -m trader watch-due`. If `due` is false, skip the rest of this part.
17e. Gather and write `watch_in.json` (format in `trader/watchlab.py`):
     - `gainers`: `preview_scan` with % Change (1d, Close) > 0.05, Asset type = STOCK, Market cap > 2000000000,
       Last > 5, Volume (1d) > 1000000 → each row's symbol, Last as `price`, % Change as `pct`.
     - `buzz`: `preview_scan` with Relative volume (1d, length 30) >= 3, % Change (1d, Close) > 0, and the same
       STOCK / market cap / Last / Volume filters → symbol, `price`, `rel_volume`.
     - `politicians`: `get_politician_trades` for each of Nancy Pelosi, Michael McCaul, Ro Khanna, Cleo Fields,
       Josh Gottheimer, Tommy Tuberville; put every returned entry, as returned, in one list.
     - `options`: `preview_scan` with Relative options volume (1d, length 30) >= 3, Options volume (1d) >= 5000, and
       the same STOCK / market cap / Last filters, plus column "Call/put ratio" = `optionsCallDayVolume / optionsPutDayVolume`
       → symbol, `price`, `rel_options_volume`, `call_put`.
     - `velocity`: `preview_scan` with % Change (1d, Close) > 0.02, % Change (1h, Close) > 0.005, Relative volume
       (1d, length 30) >= 2, and the same STOCK / market cap / Last / Volume filters → symbol, `price`, `pct_1d`,
       `pct_1h` (the "% Change (1H, close)" column), `rel_volume`.
     - `gaps`: `preview_scan` with expression filter `dayOpenPrice(session="regular") / close(candleCount=1,
       candlePeriod="1d", session="regular") - 1` <= -0.03 (display_title "Opening gap"), plus the STOCK / market cap /
       Last / Volume filters, and columns "Prev close" = `close(candleCount=1, candlePeriod="1d", session="regular")`,
       "Open" = `dayOpenPrice(session="regular")` → symbol, Last as `price`, `prev_close`, `open`. The result can be
       long on a down day: save it to a file and pull these four fields with jq.
     - `social`: one WebSearch for today's most-discussed tickers on X / Threads / StockTwits / Reddit. Up to 3
       US-listed tickers from a page clearly dated today or yesterday, each with `why` and `url`; else `[]`.
     - `swing`: run `python -m trader.yahoo --universe --recent` (refreshes 2 years of daily bars for ~110 stocks
       from Yahoo, about a minute), then `python -m trader watch-swing --input watch_in.json` (adds the Minervini and
       BNF signals from yesterday's close to the file). If Yahoo fails, skip it and say so; the rest still runs.
17f. `python -m trader watch-needs --input watch_in.json` → `get_equity_quotes` for those symbols; add each last
     trade price to `quotes` in `watch_in.json`.
17g. `python -m trader watch-run --input watch_in.json`. Report the opened / closed paper trades and the scoreboard.
     If any source shows `ready_for_real_money: true`, tell the user and ask before trading it; never trade it
     on your own.

17g2. Every run while the market is open (not only once a day): `python -m trader watch-gap-needs`. If it prints
     symbols, `get_equity_quotes` for them, write `{"quotes": {"QQQ": ..., "SYM": last_trade_price}}` to
     `gap_in.json`, and run `python -m trader watch-gap-check --input gap_in.json`. It closes gap trades that are
     back at yesterday's close ("filled") and, after 3:30 PM New York, all of them ("end of day"). Paper only.

17g3. Every run between 10:00 AM and 4:00 PM New York: `python -m trader watch-dt-needs`. If it prints symbols,
     `get_equity_quotes` for them (last trade price) and `get_equity_historicals(symbols=[the four stocks],
     interval="10minute", start_time=<today 13:00 UTC>)` (today's first bar's open_price is the day's open). Write
     `{"opens": {"NVDA": ...}, "quotes": {"QQQ": ..., "NVDA": ...}}` to `dt_in.json` and run
     `python -m trader watch-dt-check --input dt_in.json`. It opens Dual Thrust paper trades on a breakout and closes
     them at the stop or after 3:30 PM. Paper only.

## Part B4: options lab (paper only, once a day, after Part B3)

Paper-tests options strategies with real option prices; for each idea it picks the structure (long call, bull call
spread, bull put spread, cash-secured put, covered call) whose odds and expected profit beat its price (`trader/optionslab.py`, guide in
`docs/research/options-basics.md`). **Places no orders.**

17h. Start `opt_in.json` as `{}` and run `python -m trader opt-step --input opt_in.json`. Each run prints either
     `{"need": ...}` or `{"done": ...}`. Fetch what it asks, add it to the file, and run again:
     - `spots`: `get_equity_quotes` for those symbols → `{"SYM": last_trade_price}`
     - `history`: `get_equity_historicals(symbols (10 per call), interval="day", start_time=<50 days ago>)` →
       `{"SYM": [close_price, ... oldest first]}`, then append today's spot as the last close
     - `expirations`: `get_option_chains(underlying_symbol=SYM)` → `{"SYM": [all expiration_dates]}`
     - `instruments`: `get_option_instruments(chain_symbol, expiration_dates, type)` for each request → `{"SYM":
       [{"id", "strike", "type", "expiration"}]}` (strikes from 60% to 140% of the stock price: the $100 test,
       `budget100`, looks for cheap contracts far from the price)
     - `quotes`: `get_option_quotes(ids)` → `{"<id>": {"bid": bid_price, "ask": ask_price}}`
17i. Report the opened / closed paper positions and the scoreboard. If it prints `{"done": {"already_ran": true}}`,
     today's options run is already recorded.

17j. XRP paper test (paper only, once a day, any time; never buy XRP for real): `python -m trader.yahoo BTC-USD
     ETH-USD SOL-USD XRP-USD`, then `get_crypto_quotes(["XRP-USD"])` and `python -m trader xrp-paper --price
     <mark_price>`. It sizes a $2.00 paper XRP position the way the crypto strategy would with XRP in its coin
     list and logs the day to `data/xrppaper/log.csv` (`{"already_ran": true}` = today is recorded). Report one
     line: target weight, paper value vs holding XRP.

## Part C: results and commit

18. Append a dated entry to `journal/robinhood-log.md`: account value; crypto targets, orders and fills;
    stock orders, fills, stops and anything skipped (the `report.skipped` reasons); errors.
19. Record the day's results:
    - `get_portfolio(account_number="746480458")` and append one row to `data/robinhood-results.csv`:
      `date` (today, Pacific), `value` = `total_value`, `cash`, `crypto` = `crypto_value` (2 decimals),
      and a short `note` (e.g. "No trades" or "Bought NVDA $1.00; BTC no change"). One row per date: if
      today's row already exists, replace it.
    - Build the JSON copy: `python3 -c "import csv,json; json.dump([{**r,'value':float(r['value']),'cash':float(r['cash']),'crypto':float(r['crypto'])} for r in csv.DictReader(open('data/robinhood-results.csv'))], open('daily-results.json','w'))"`
      (write it outside the repo).
    - Update the results page https://claude.ai/artifact/HZUowfm349k9HEcWYNHMST: upload the JSON with
      the Artifact tool (`url` = that page, `file_path` = the JSON, `asset: true`), then with ArtifactData
      `get` collection `datasets` doc `daily` and `update` it (pinned with `if_version`) with
      `source: {kind: "file", url: <the upload's /_blob/... url>, name: "daily-results.json"}` and
      `updated: {at: <now, ISO>, by: "Claude (daily run)"}`. If those tools aren't available, skip this
      and say so in the final message; the CSV in the repo still has the row.
20. Commit `data/proxy_history.csv`, `data/bars/`, `data/robinhood-stocks.sqlite3`, `data/spcx-test.json`, `data/watchlab/`, `data/optionslab/`, `data/xrppaper/`, `data/yahoo/*-USD.csv`,
    `data/robinhood-results.csv` and `journal/robinhood-log.md` together, and push to
    `claude/jolly-fermi-tvlmpb`.
