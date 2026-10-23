# Famous traders' styles as testable rules (Oct 23, 2026)

`python -m trader.legends` (raw output: legends-output.txt). Daily Yahoo bars, 0.05% per side on stocks/ETFs,
1% per side on crypto. None of these people published rules; each test is the closest price-only version of
their style. Sharpe = return per unit of risk (higher is better; SPY is about 0.5-0.8).

| Style (rule tested) | Since 2016: per year / worst drop / Sharpe | Since 2005 |
|---|---|---|
| Hold SPY | +13.2% / -34% / 0.79 | +8.9% / -56% / 0.54 |
| Hold QQQ | +18.9% / -36% / 0.89 | +14.1% / -54% / 0.72 |
| **Dennis**: Turtle breakouts on SPY QQQ IWM GLD TLT BTC (vs holding the same 6: +38.8% / -73% / 0.90) | +29.2% / -59% / 0.95 | (from 2015) |
| **Druckenmiller**: each month, all-in on the strongest of those 6 if above its 200-day average | +48.8% / -66% / 1.03 | +29.7% / -64% / 0.87 |
| same, without Bitcoin in the menu | +4.1% / -41% / 0.31 | +6.4% / -36% / 0.43 |
| **Simons**: RSI(2) dip-buying on SPY QQQ IWM DIA | +2.5% / -16% / 0.36 | +2.1% / -18% / 0.30 |
| same on NVDA AMD AAPL MSFT (vs holding them: +55.6% / -61% / 1.25) | +12.6% / -22% / 0.77 | +7.8% / -22% / 0.56 |
| **Griffin**: equal money in the Dennis, Druckenmiller and Simons strategies (from 2015) | +29.1% / -56% / 0.98 | |
| **Buffett**: buy SPY after a 20% fall from its 1-year high, hold 3 years | +41% avg over 3 years (7 buys since 2001) vs +33% from any day | |
| **Lynch**: growth at a reasonable price | not tested: needs past earnings data | |

## What it means
- **Druckenmiller (concentrated momentum)** had the best numbers, but almost all of it came from riding Bitcoin's
  runs; without Bitcoin it lost to holding SPY. A big win from one asset, not proof of a method.
- **Dennis (Turtle on a mixed basket)** gave up some return versus holding the same basket but had a better Sharpe
  and a smaller worst drop. Trend following helps most when the basket mixes things that don't move together.
- **Simons-style dip buying** wins 66-71% of trades but each win is tiny (+0.2% on index ETFs); after costs it
  barely beats cash on ETFs. Renaissance's edge comes from thousands of signals and near-zero costs, not one rule.
- **Griffin's idea (mixing strategies)** worked as designed: the mix had one of the smallest worst drops of the
  high-return strategies, though not a higher return.
- **Buffett's "buy when others are fearful"** helped: 3-year returns after a 20% fall averaged +41% on SPY and +43%
  on QQQ versus +33% and +41% from a random day, with 6 of 7 SPY buys profitable. Rare (about once every 3 years).
- **Simply holding the strong stocks** beat every rule since 2016. The rules mainly buy a smoother ride.
