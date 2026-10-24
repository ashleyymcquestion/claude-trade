# Lessons so far (shared by the Robinhood and Coinbase bots)

Both bots read this before adding or changing a strategy, and add a line when a test teaches something.
Details live in the linked notes.

## How we decide
- The code decides trades. A strategy goes: backtest -> paper trade -> the user's OK -> small real money.
- A backtest result only counts if neighbouring settings agree; one lucky setting among many losers is noise.
- Always charge real costs: Robinhood crypto ~1% per side (spread); Coinbase 0.5% limit / 0.9% market (Intro tier);
  Robinhood stocks ~0.05% slippage.
- One year of data is not enough. Check several separate periods (crypto-coins.md shows why).

## What we've learned (Oct 2026)
- Crypto, chasing the hottest coins weekly: worked 2019-2024, lost badly 2025-2026; fees eat weekly rotation.
  Holding BTC only while it's above its 10-week average is the steadiest rule. (crypto-coins.md)
- Small/odd coins (SKY, MOODENG, XCN) can have no trading at all; filter on dollar volume.
- Opening-range breakout on stocks, checked hourly: lost after costs. Dual Thrust (k 0.5): small edge, paper only.
  (watchlab-strategies.md)
- Buying stock gap-downs to fill: edge grows with gap size (3%+ better than 2%). Paper only.
- Options: momentum calls lost in the model backtest; pullback calls (above 200-day, RSI < 35) were slightly positive.
  Paper only. (options-basics.md)
- $100 options (real prices, May-Oct 2026): the call nearest the price under $100, sold at +100%, averaged
  +$54 per $100 but half went to zero; puts lost. Only tested in a rising market. (options-100-budget.md)
- Turtle breakouts (long only) made about half of buy-and-hold since 2016 but halved the worst drop;
  the overnight effect is real (QQQ +13%/yr overnight, -3% daytime) but costs make trading it a big loser. (classics.md)
- Legends as rules (legends.md): monthly 'all-in on the strongest asset' only won thanks to Bitcoin; Turtle on a
  mixed basket and mixing strategies (Griffin) smooth the ride; RSI(2) dip-buying wins often but tiny; buying SPY after
  a 20% fall beat a random day over 3 years. Since 2016 nothing beat simply holding NVDA/AMD/AAPL/MSFT.
- Swing traders (swing-traders.md, ~110 stocks since 2016): Minervini breakouts matched SPY with half the drawdown and
  made money in 2022; BNF deep-dip buying had the best average trade; Qullamaggie and Raschke lagged on large caps.
- AI-infrastructure options (options-ai-infra.md): $100 calls paid hugely in the summer run-up and lost 27-55% per
  bet in Aug-Oct; puts lost in both halves. No setup won in both halves: it's a bet on the AI trade, not an edge.
- Tiny accounts: Robinhood's $1 minimum blocks some stock entries; margin accounts under $25k get 3 day trades
  per 5 business days.

## Data we can reach
- Robinhood: quotes, daily/10-minute history (5000 bars per call), scanner (crypto history up to 365 days).
- Yahoo (query1.finance.yahoo.com): long daily history for stocks and crypto, e.g. `/v8/finance/chart/BTC-USD?range=10y&interval=1d`.
- Coinbase (Coinbase session): candles down to 1 minute, order book, fees.

- 2026-10-23 XRP: adding XRP to the crypto strategy changed little (+2–3%/yr over 2021–2026, slightly worse
  since 2025; it gets ~8% weight). In every window the strategy trailed plain BTC holding — it cuts drawdowns
  but the ~2% Robinhood spread and trend-exits cost most of the upside. XRP is now paper-tested daily
  (data/xrppaper), never traded for real without the user's OK.

- 2026-10-24 meme coins ("Kimchi"): chase / breakout rules average +18-21% per trade but the MEDIAN trade loses;
  results come from a few mania weeks (2021, early 2024) and are flat-to-negative in 2025-26. Meme basket held
  since Jan 2025: -89%. Yahoo rounds sub-penny coins (SHIB, PEPE, BONK, FLOKI, MOG) to 1-2 digits, faking 25%
  moves; never use those prices for signals. Paper only (data/memepaper).

- 2026-10-24 CHIP (USD.AI): user asked to trade it; declined real money (down ~67% from April high, ~105x revenue
  with no holder revenue share, ~2.2% Robinhood spread, outside the bot's BTC/ETH/SOL rules). Running a 48-hour
  $2 paper hold instead (data/coinwatch, ROBINHOOD.md 17l).
