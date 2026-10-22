# Robinhood trading log

## 2026-10-15 (first run, placed manually from the chat session)
- Equity for strategy: $1.00 cash. XLM/HBAR and their 3 open limit sells left untouched.
- Target (ETF prices as of 2026-10-14): BTC 32.0%, ETH 22.0%, SOL 0% (BSOL history < 1 year).
- Orders: buy BTC $0.32 market, filled 0.00000384 BTC @ $83,191.83;
  buy ETH $0.25 market (rounded up from $0.22 to Robinhood's $0.25 minimum), filled 0.000101 ETH @ $2,469.58.
- Cash left: ~$0.43. Fees: $0 (spread ~1–2% is the real cost).

## 2026-10-15 (afternoon, from the chat session at the user's request)
- Sold the old XLM/HBAR positions to move the account onto the strategy. Canceled their 3 open
  limit sells and paused the hourly HBAR/XLM scheduled task first.
  - sell 8.98 XLM market, filled @ $0.1828095 = $1.64 (cost $2.00, loss $0.36)
  - sell 18.73 HBAR market, filled @ $0.08432448 = $1.57 (cost $2.00, loss $0.43)
- Ran rh-plan on the new state: equity $4.20, cash $3.64; target (ETF prices as of 2026-10-14)
  BTC 32.0%, ETH 22.0%, SOL 0%.
- Orders: buy BTC $1.03 market, filled 0.0000124 BTC @ $83,009.13;
  buy ETH $0.68 market, filled 0.000274 ETH @ $2,474.90.
- Cash left: ~$1.93 (strategy holds ~46% cash at today's volatility). Fees: $0.

## 2026-10-15 (evening, crypto half, from the chat session at the user's request)
- Account split 50/50 from today: crypto strategy manages 50% of the account (`crypto_sleeve = 0.5`),
  the quantbot stock bot the other 50% (stocks start with the 2026-10-16 morning run).
- rh-plan: account $4.19, crypto sleeve $2.09; target (ETF prices as of 2026-10-14) BTC 32.0%, ETH 22.0%, SOL 0%.
- Orders: sell BTC $0.66 market, filled 0.0000081 BTC @ $81,430.67 ($0.65);
  sell ETH $0.46 market, filled 0.000189 ETH @ $2,432.06 ($0.45).
- After: account $4.16, crypto $1.13, cash $3.03 (stock half can now use up to ~$2.08). Fees: $0.

## 2026-10-21 (10:38 AM PT, hourly scheduled run)
- Account $4.23 (cash $0.53, crypto $1.67, stocks $2.03).
- Crypto: rh-plan sleeve $2.12; target (ETF prices as of 2026-10-20) BTC 39.6%, ETH 29.6%, SOL 0%;
  holdings BTC $0.94, ETH $0.73. No orders.
- Stocks: **skipped, nothing placed.** The account holds NVDA 0.004625 sh (bought 2026-10-19 @ $216.20)
  and QQQ 0.001389 sh (bought 2026-10-19 @ $719.58), both agent-initiated, but
  `data/robinhood-stocks.sqlite3` on this branch has no positions, orders or events: the runs that
  placed them never committed the bot state. Planning on that blank state could re-buy, and these
  holdings have no stops or loss limits tracked. Needs the bot state reconciled before the stock half resumes.
- Also not in the repo: a BTC buy of ~$0.25 today (0.00000297 BTC, intraday cost basis) and no results
  rows since 2026-10-15, so earlier runs' logs were lost the same way.

## 2026-10-21 (10:50 AM PT, stock bot state reconciled at the user's request)
- Replayed the 2026-10-19 13:34 UTC stock plan with the bot's own code (bars through 2026-10-16):
  it planned exactly the two buys that were placed, both mean_reversion (AMD/PLTR signals were
  below the $1 minimum).
- Recorded the real fills with `rh-stock-record`: NVDA 0.004625 @ $216.1999 (order 6ad61c5b…),
  stop $204.26; QQQ 0.001389 @ $719.5784 (order 6ad61c5f…), stop $698.49.
- Dry run of today's plan on a copy of the state: no orders, nothing unmanaged. The stock half
  resumes on the next hourly run.
- Bars for NVDA/AMD/PLTR/QQQ updated through 2026-10-20.

## 2026-10-21 (11:37 AM PT, hourly run)
- Account $4.23 (cash $0.53, crypto $1.67, stocks $2.04).
- Crypto: rh-plan target BTC 39.6%, ETH 29.6%, SOL 0%; holdings BTC $0.94, ETH $0.73. No orders.
- Stocks: rh-stock-plan no orders, nothing skipped; NVDA stop $204.26, QQQ stop $698.49 tracked.
- SPCX test: waiting (cash $0.53 < $1.00); SPCX $158.68.
- Watchlab: already ran today (14 paper trades opened at 11:18 AM PT).

## 2026-10-21 (12:37 PM PT, hourly run)
- Account $4.22 (cash $0.53, crypto $1.66, stocks $2.03). No orders: crypto at target (BTC $0.94, ETH $0.72),
  stocks no signals (NVDA stop $204.26, QQQ stop $698.49), SPCX test waiting (cash $0.53 < $1.00; SPCX $158.36).
- Watchlab and options lab already ran today.

## 2026-10-21 (1:37 PM PT, hourly run)
- Account $4.22 (cash $0.53, crypto $1.66, stocks $2.03). No orders: crypto at target (BTC $0.94, ETH $0.72);
  stock market closed (NVDA closed $221.87, stop $204.26; QQQ $725.21, stop $698.49); SPCX test waiting (closed at $159.28).

## 2026-10-21 (2:37 PM PT, hourly run)
- Account $4.23 (cash $0.53, crypto $1.66, stocks $2.04). No orders: crypto at target (BTC $0.94, ETH $0.72);
  stock market closed (stops NVDA $204.26, QQQ $698.49); SPCX test waiting.

## 2026-10-21 (3:37 PM PT, hourly run)
- Account $4.21 (cash $0.53, crypto $1.66, stocks $2.02). No orders: crypto at target (BTC $0.94, ETH $0.72);
  stock market closed; SPCX test waiting.

## 2026-10-21 (4:37 PM PT, hourly run)
- Account $4.20 (cash $0.53, crypto $1.65, stocks $2.02). No orders: crypto at target (BTC $0.94, ETH $0.72);
  stock market closed; SPCX test waiting.

## 2026-10-21 (5:37 PM PT, hourly run)
- Account $4.20 (cash $0.53, crypto $1.65, stocks $2.02). No orders: crypto at target; stock market closed; SPCX test waiting.

## 2026-10-21 (6:37 PM PT, hourly run)
- Account $4.19 (cash $0.53, crypto $1.65, stocks $2.01). No orders: crypto at target; stock market closed; SPCX test waiting.

## 2026-10-21 (7:37 PM PT, hourly run)
- Account $4.17 (cash $0.53, crypto $1.63, stocks $2.01). No orders: crypto at target; stock market closed; SPCX test waiting.

## 2026-10-21 (8:37 PM PT, hourly run)
- Account $4.16 (cash $0.53, crypto $1.62, stocks $2.01). No orders: crypto at target; stock market closed; SPCX test waiting.

## 2026-10-21 (9:37 PM PT, hourly run)
- Account $4.16 (cash $0.53, crypto $1.62, stocks $2.00). No orders: crypto at target; stock market closed; SPCX test waiting.

## 2026-10-21 (10:37 PM PT, hourly run)
- Account $4.15 (cash $0.53, crypto $1.62, stocks $2.00). No orders: crypto at target; stock market closed; SPCX test waiting.

## 2026-10-21 (11:37 PM PT, hourly run)
- Account $4.15 (cash $0.53, crypto $1.62, stocks $2.00). No orders: crypto at target; stock market closed; SPCX test waiting.

## 2026-10-22 (12:37 AM PT, hourly run)
- Account $4.14 (cash $0.53, crypto $1.61, stocks $2.00). No orders: crypto at target; stock market closed; SPCX test waiting.
- Asked to rotate the stocks into SOL or other trending names if the charts favor it: reviewed at 11:37 PM PT and
  held. NVDA/QQQ sit at their 50-day averages with improving MACD; SOL's MACD just turned negative, and the swap
  would cost ~1-2% in spread. Will re-check after the open.

## 2026-10-22 (1:37 AM PT, hourly run)
- Account $4.13 (cash $0.53, crypto $1.60, stocks $1.99). No orders: crypto at target; stock market closed; SPCX test waiting.

## 2026-10-22 (2:37 AM PT, hourly run)
- Account $4.12 (cash $0.53, crypto $1.60, stocks $1.99). No orders: crypto at target; stock market closed; SPCX test waiting.

## 2026-10-22 (3:37 AM PT, hourly run)
- Account $4.13 (cash $0.53, crypto $1.61, stocks $2.00). No orders: crypto at target; stock market closed; SPCX test waiting.
