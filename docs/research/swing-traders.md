# Famous day/swing traders' setups, backtested (Oct 23, 2026)

`python -m trader.swing` on ~110 US stocks (`python -m trader.yahoo --universe`), daily bars, 0.05% per side,
at most 10 positions at 10% each, cash only. Raw output: swing-output.txt. Rules are in trader/swing.py.

| Since 2016 (SPY +13.2%/yr, worst drop -34%, Sharpe 0.79) | Per year | Worst drop | Sharpe | Trades | Win rate | Avg trade | Avg hold |
|---|---|---|---|---|---|---|---|
| Qullamaggie episodic pivot | +2.8% | -15% | 0.46 | 197 | 39% | +1.6% | 8 days |
| BNF (20% below 25-day average) | +10.7% | -36% | 0.52 | 353 | 55% | +3.8% | 13 days |
| Raschke Holy Grail | +3.4% | -21% | 0.46 | 1315 | 46% | +0.3% | 4 days |
| Minervini trend template breakout | +10.1% | -15% | 0.82 | 467 | 41% | +2.5% | 35 days |

| Other periods | SPY | Qullamaggie | BNF | Raschke | Minervini |
|---|---|---|---|---|---|
| 2010-2026 per year | +12.0% | +1.8% | +7.2% | +1.3% | +8.4% |
| 2022 (bear market) | -20.0% | -7.1% | +4.3% | -9.3% | +2.4% |
| 2024-2026 per year | +18.7% | +0.2% | +21.4% | +4.9% | +10.1% |

## What it means
- **Minervini** was the best all-round: about SPY's return with less than half the worst drop (-15% vs -34%), the best
  Sharpe, and it made money in 2022 while SPY lost 20%. It sits in cash when no stock qualifies.
- **BNF** (buying stocks crushed 20%+ below their 25-day average) had the best average trade (+3.8%) and beat SPY in
  2024-2026, but has SPY-sized drops. Caution: dip-buying looks best on a list of stocks that survived.
- **Qullamaggie's** episodic pivots are rare among large caps (about 20 a year here), so money sat in cash. His
  results come from small, fast-moving stocks this list mostly lacks.
- **Raschke's Holy Grail** traded the most and earned the least per trade; costs eat a +0.3% average.
- None beat simply holding SPY over the full period; Minervini matched it with far less risk.
