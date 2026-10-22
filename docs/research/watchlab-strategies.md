# Watchlab: what the idea sources are, and what we're learning from them

Started 2026-10-21. Paper trades only (`trader/watchlab.py`, ledger `data/watchlab/trades.csv`).
Every idea gets a $1 paper position at the live price and is closed at -8% (stop), +15% (target) or
after a fixed hold (5 trading days; 20 for politician trades). Each close is compared with QQQ
over the same days. A source is only proposed for real money after 20+ closed trades with a win
rate of at least 50% and an average return above QQQ.

## Politician trades (from STOCK Act disclosures, via Tip Ranks)

What the filings show (as of 2026-10-21):

| Member | Style | Example filings |
|---|---|---|
| Rep. Nancy Pelosi | Few, very large, concentrated bets on large-cap growth names, often with long call options alongside the stock. Buys cluster over several days. Sells are mostly big trims at year-end. | Jul 23–28 2026: BE stock + calls ($1M–$5M lots), INTC stock + calls. May 2026: INTC, UBER calls. Dec 2025: sold GOOGL, DIS ($1M–$5M); bought AAPL, NVDA, AMZN calls. |
| Rep. Michael McCaul | Broad, active portfolio, likely manager-run. Many sells; filed ~55 days after the trades (past the 45-day rule). | Disclosed Oct 16: bought VVV, ACN ($50K–$100K), ALH, FCN; sold COP, SAP, WDAY, NUE. |
| Rep. Ro Khanna | Dozens of small ($1K–$15K) buys and sells at once: looks like a managed, diversified account. | Disclosed Oct 9: bought ARE, PFE, COST, TMUS, DPZ...; sold WMT, TXN, NKE... |
| Rep. Cleo Fields | Steady repeat buys of the same mega-cap tech names, like dollar-cost averaging. | AAPL, MSFT, GOOGL, TSM, NVDA every month or two. |
| Sen. Tommy Tuberville | Sold many single stocks in mid-2026; bought defensive sector ETFs (XLP, XLU). | Filed Aug 5 2026 for trades from Dec 2025. |
| Rep. Josh Gottheimer | Many small trades across sectors. | AMAT bought Aug, sold Sep 2026. |

What this means for copying them:
- **The lag is the big problem.** Trades show up 2–8 weeks after they happen, so the move may be over.
- **Most members' trades look like managed-account noise** (Khanna, McCaul, Gottheimer). The
  concentrated, high-conviction bets (Pelosi) are rare: a few clusters a year.
- The NANC ETF (Democratic members' trades) has roughly matched or slightly beaten the S&P 500, and
  commentators credit a handful of traders for most of that
  ([lambdafin](https://www.lambdafin.com/articles/congress-vs-sp-500),
  [etf.com](https://www.etf.com/sections/features/congress-may-complicate-etfs-track-lawmakers-trades),
  [daytraders.com](https://daytraders.com/news/2026/07/23/trump-official-buys-democratic-trades-etf-nanc-pelosi)).
- Watchlab tracks: Pelosi, McCaul, Khanna, Fields, Gottheimer, Tuberville. It takes up to 5 new BUYs
  a day, biggest disclosed amounts first, disclosed within the last 14 days, and holds 20 trading days.

## Top gainers (Robinhood scanner)

Liquid stocks up more than 5% today (market cap > $2B, price > $5, volume > 1M). Up to 5 a day, the
biggest gainers first, bought at the live price late morning. Research is split: big one-day jumps
in large stocks often partly reverse, but news-driven moves on heavy volume can keep going. The
paper record will show which applies here.

## Buzz (unusual volume)

Stocks trading at 3x+ their normal volume while up on the day. Social chatter usually shows up as
volume, so this is a measurable stand-in for "everyone is talking about it".

## Social (X, Threads, StockTwits, Reddit)

X, Threads, StockTwits and Reddit trackers can't be reached directly from the trading environment,
so the daily run uses a web search for today's most-discussed tickers (e.g. the
[AltIndex WallStreetBets](https://altindex.com/wallstreetbets) and [X](https://altindex.com/x-stocks)
trackers and StockTwits trending) and records up to 3, with the link it used. Dates on these pages
are often unclear; only tickers clearly from today or yesterday are taken.

## Stacked (signals that agree)

Added 2026-10-21, from the "pond" idea in the RebelCon quantum talk
([notes](rebelcon-2026-quantum-notes.md)): where ripples cross, waves stack up. A stock flagged by
two or more of the sources above on the same day gets its own paper trade (up to 3 a day, most sources
first, 5-day hold). If agreeing signals really are stronger, this source should beat each single source.
On day one, ASST was both a top gainer and trending on StockTwits.

## Options (unusual options activity)

Added 2026-10-21, from the RebelCon early-stage panel ([notes](rebelcon-2026-early-stage-panel-notes.md)):
the Najarians' "Heat Seeker" scans options order flow for big, out-of-pattern bets that may be "smart
money" ahead of news. Watchlab's version: stocks whose options volume is 3x+ normal, with calls at least
2x puts (a bullish lean), up to 3 a day, biggest volume jump first, 5-day hold. Market Rebellion calls
this an indicator, not a guarantee: a big options trade can be a hedge. Day one: FHN (27x, almost all
calls), CECO, UAL.

## Velocity (price and volume speeding up)

Added 2026-10-21, from Excite Capital's method in the quantum talk ([notes](rebelcon-2026-quantum-notes.md)):
don't predict, read the present. Any liquid stock up more than 2% today and more than 0.5% in the last
hour, on 2x+ normal volume, ranked by last-hour move x volume; up to 3 a day, 5-day hold. Day one: KYIV.

## Gaps (opening gap-downs that may "fill")

Added Oct 22. A stock that opens well below yesterday's close often drifts back toward it during the day.
Each morning the scan flags liquid stocks that opened 3%+ lower and are still below yesterday's close; the
lab paper-buys the biggest 5 gaps, then every hourly run checks them: sell when the price is back at
yesterday's close ("filled"), or after 3:30 PM New York at whatever the price is ("end of day"). Same-day only.

Backtest on NVDA, AMD, PLTR and QQQ daily bars since 2019 (`python -m trader.gapbacktest`, 0.1% cost per side):

| Gap at the open | Trades | Win rate | Avg per trade |
|---|---|---|---|
| 2%+ down | 543 | 54% | +0.12% |
| 3%+ down | 234 | 53% | +0.40% |
| 5%+ down | 82 | 56% | +1.00% |

Fading gap-UPs (betting they fall back) lost slightly, and the account can't short, so only gap-downs are tested.
Caveats: four stocks that rose a lot (hindsight), and daily bars can't show whether the fill came before a
further drop. The paper trades are entered at the price when the scan runs (~10:30 AM New York), not the open.

**Real money and day trading:** a same-day buy and sell is a "day trade". In a margin account under $25,000
(this one is "limited margin"), 4+ day trades in 5 business days flags the account as a Pattern Day Trader
and restricts it. Any real-money version must stay at 3 day trades per rolling 5 business days.

## Dual Thrust (intraday breakout)

Added Oct 22, after testing two day-trading breakouts from github.com/je-suis-tm/quant-trading on Robinhood
10-minute bars for NVDA, AMD, PLTR and QQQ (`python -m trader.intradaybacktest`, 0.05% slippage per side, long
only, flat by the close). "Hourly" means entries and exits only at our hourly checks, the way the bots run.

| Strategy | Period | Trades (hourly) | Win rate | Avg per trade |
|---|---|---|---|---|
| Opening range breakout (30 min) | Feb 19 - Apr 30 | 94 | 49% | +0.18% |
| | May 1 - Oct 22 | 207 | 40% | -0.15% |
| Dual Thrust (4 days, k 0.5) | Feb 19 - Apr 30 | 15 | 67% | +0.51% |
| | May 1 - Oct 22 | 29 | 52% | +0.30% |

The opening range breakout flipped from winning to losing, so it is not used. Dual Thrust made money in both
periods, but on only 44 trades, so it is paper-traded now: every hourly run from 10 AM to 4 PM New York checks the
four stocks, paper-buys a breakout above today's open + 0.5 x the 4-day range, and sells at the stop (open - 0.5 x
range) or after 3:30 PM. Buying and holding the same stocks made more over both periods; Dual Thrust is only in
the market a few days a month, so the test is whether its per-trade edge is real.

### Dual Thrust on 3 years of hourly bars (Oct 22, 2026): it loses
`python -m trader.dtbacktest --fetch` replays the exact paper rule (hourly checks, 0.05% slippage per side) on
Yahoo hourly bars, Nov 2023 - Oct 2026, for NVDA/AMD/PLTR/QQQ plus 20 large stocks and ETFs it was never tuned on.

| Setting | Trades | Win rate | Avg per trade | t-stat |
|---|---|---|---|---|
| 4-day range, k 0.5 (the paper rule) | 1,563 | 42.5% | **-0.085%** | -2.8 |
| same, original 4 stocks only | 225 | 45.3% | -0.125% | -1.3 |
| same, original 4, May - Oct 2026 | 31 | 61% | +0.34% | 1.7 |
| same, original 4, Nov 2023 - Apr 2026 | 194 | 43% | -0.20% | -1.9 |
| 2-day / 6-day range, k 0.3 / 0.7 | 665 - 3,793 each | 41-44% | -0.08% to -0.11% | all negative |

The earlier +0.30% per trade was a lucky 6-month window. Over 100+ trades, in both halves of the period,
on the original stocks and on new ones, with every setting tried, it lost money. The paper test keeps running
(the user asked for 100 paper trades), but this is the verdict: no edge.
