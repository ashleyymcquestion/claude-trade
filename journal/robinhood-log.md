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
