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
- Tiny accounts: Robinhood's $1 minimum blocks some stock entries; margin accounts under $25k get 3 day trades
  per 5 business days.

## Data we can reach
- Robinhood: quotes, daily/10-minute history (5000 bars per call), scanner (crypto history up to 365 days).
- Yahoo (query1.finance.yahoo.com): long daily history for stocks and crypto, e.g. `/v8/finance/chart/BTC-USD?range=10y&interval=1d`.
- Coinbase (Coinbase session): candles down to 1 minute, order book, fees.
