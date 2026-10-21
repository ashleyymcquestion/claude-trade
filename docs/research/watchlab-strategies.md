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
