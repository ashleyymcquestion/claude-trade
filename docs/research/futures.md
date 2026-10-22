# Crypto futures: is there a winning strategy? (Oct 22, 2026)

Question: find a futures strategy (long and short, leverage allowed) that wins, ideally enough for $5,000 a month.

## Test
`python -m trader.futuresbacktest`, full grid in `reports/futures_grid.csv`. Daily BTC and ETH closes
2017-Oct 2026 (`data/crypto/daily/`). Pessimistic futures costs: 0.10% fee per side, longs pay 10% a year
funding, and a run counts as liquidated if one day takes equity under 25% of the day before. Each idea is
tried over a grid of settings and judged by its median and worst setting, period by period, so one lucky
setting doesn't count as a win.

Families: long/short above/below an n-day average (`sma_ls`), long-or-cash (`sma_lo`), time-series
momentum (`tsmom`), Donchian breakouts (`donchian`), an average of four trend lengths (`ensemble`); each
at 1x/2x/3x fixed leverage or volatility-targeted (40% target, max 2x; 60% target, max 3x).

## Findings
1. **Shorting crypto lost.** For every family, long/short did worse than long-or-cash. The short
   side paid for every bear-market rally and the rare big crash days. Futures' main extra (shorting) didn't help.
2. **Fixed leverage ruins.** At 3x, most settings were liquidated or lost 95%+ at some point. Even 2x fixed
   had median worst drops of 85-95%.
3. **What held up: long-only trend with volatility sizing.** ETH long-or-cash made money in every period for
   all 6 average lengths, including 2025-26. The setting-free version (below) did too.
4. Walk-forward (each period trade whatever setting was best before it) gave BTC +109% and ETH -30% over
   2019-2026: picking the "best" setting from the past mostly picked yesterday's luck.

## The strategy now paper traded
BTC + ETH, half each. Signal = share of the 20/50/100/200-day averages the close is above (0 to 1, never
short). Size = signal x min(40% / 30-day vol, 2x).

| Period | Strategy | Hold BTC |
|---|---|---|
| 2019-2020 | +311% | +655% |
| 2021-2022 | +12% | -44% |
| 2023-2024 | +115% | +462% |
| 2025-Oct 2026 | +15% | -11% |
| 2019-Oct 2026 per year | **36.5%** (worst drop -35%) | 48% (worst drop -77%) |

It gave up some of the bull-market upside for much smaller crashes. Months up: only 46%; the gains come
in bursts during strong trends, so most months are flat or slightly down.

Without leverage (max 1x, which plain spot buying can do) the same rule made 27-38% a year. So futures
add only a little here, at extra cost and margin risk.

## What $5,000 a month would take
| Assumption | Return per month | Money needed for $5k/month (average, not every month) |
|---|---|---|
| 2019-2026 backtest, 36.5% a year | 2.6% | about $190,000 |
| Last 22 months only, 7.7% a year | 0.6% | about $810,000 |

From about $15 it would take roughly 330x per month. Every strategy that came close to that in the grid
was liquidated. Honest expectation for this strategy: 10-35% a year with 30-50% drops along the way.

## Not tested (no historical data here)
- Cash-and-carry: buy spot, short the dated future, earn the basis (BIT-30OCT26 was about 5% a year above
  spot today). Close to market-neutral, but small returns, and it needs about $1,000 per BTC nano contract.

## Caveats
Daily closes miss intraday swings (liquidations come sooner in reality); Yahoo prices, not Coinbase futures
prices; funding is a flat estimate. Paper trading starts Oct 22, 2026 (`trader/cbfutures.py`, $1,000 paper).
