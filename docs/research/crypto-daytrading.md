# Crypto day trading: does any rule work? (Oct 23, 2026)

`python -m trader.cryptointraday --fetch`: Yahoo hourly BTC/ETH/SOL bars, Oct 2024 - Oct 2026 (2 years). Every rule
decides at an hourly close (as our hourly bots run), fills at that close and is flat by the end of the UTC day.
34 settings across 4 rule families: Dual Thrust breakouts (long only and long/short), mean reversion after big
hourly moves, "the day is up at hour h so hold the rest of the day", and 24-hour-high breakouts.

## Costs decide everything
| Cost per side | What it is | Settings with a positive average trade |
|---|---|---|
| 0.5% | Coinbase spot with limit orders at this account's tier | **0 of 34** (every one loses 0.6% to 1.2% per trade) |
| 0.1% | Coinbase futures, pessimistic | 4 of 34 |
| 0.05% | a high-volume futures tier | 7 of 34 |

Before costs, the best rules made about +0.2% to +0.4% per trade; a spot round trip costs 1%.

## The few that looked good fell apart
The only settings positive at 0.1% were Dual Thrust with a wide trigger (k 0.7, and k 0.5 long only on a 4-day
range). Every one of them made money in the first year and **lost in the second** (Oct 2025 - Oct 2026),
e.g. 4-day k 0.7 long only: +0.68% per trade, then -0.22%; t-stat 1.5 on 175 trades. In the second year all
34 settings lost money at 0.1% costs. This is the same pattern as the stock Dual Thrust test
(docs/research/watchlab-strategies.md): a good-looking stretch, not an edge.

## Verdict
No crypto day-trading rule tested here has an edge after costs. With spot fees it isn't close. Not paper traded:
a rule that lost over the last full year is not worth the hourly usage. Re-run this file in a few months
(`--fetch` gets fresh bars) to see if the wide-trigger Dual Thrust recovers.
