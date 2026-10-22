# Trading other coins (Oct 22, 2026)

Question: should the bot trade the coins that are "trading the most and making the most gains", not just BTC/ETH?

## What Robinhood lists
50 coins. Most money traded today (dollar turnover): BTC ($167M), ETH ($26M), XRP ($13M), SOL ($7.8M),
DOGE ($5.9M); everything else is under $2M. SKY, MOODENG and XCN show no trading at all. USDC is a stablecoin.

Biggest gainers since Oct 2025 (weekly closes, today vs a year ago): HYPE +122% and AERO +155% (both
listed partway through the year), UNI +11%, LIT +109% (listed partway). Almost everything else is down
30-80%; BTC -26%, ETH -38%, SOL -46%.

## Backtest: buy the strongest coins each week
`python -m trader.cryptomomentum`, data in `data/crypto/weekly_closes.csv` (Robinhood scanner, 53 weekly
closes for all 50 coins). Each week hold the top 1/3/5 coins by their 2/4/8-week return, optionally only
those above their 10-week average, optionally only while BTC is above its 10-week average. 1% cost per
side for the spread. 35 coins with real trading.

| Version | Results across the 9 lookback/top combos |
|---|---|
| No trend filter | all 9 lost, -36% to -74% |
| Coin above its 10-week average | 7 lost (down to -50%), best +4% |
| Plus BTC above its 10-week average | 8 lost, one +18% (2-week, top 1) |
| Same period: hold BTC / ETH / SOL | -8% / -21% / -18% |
| Hold BTC only while BTC is above its 10-week average | +13% |

## Conclusion
Chasing this year's hot coins lost money in 24 of 27 versions, and the three winners are scattered (no
neighbouring setting agrees), which is what luck looks like. The spread (~2% a round trip) eats weekly
rotation alive, and the small coins fell much harder than BTC in every downturn.

What did work is the simplest thing: own BTC only while it's trending up. That is already what the crypto
planner does with BTC/ETH (trend-filtered), so no new coins are added. Caveats: one year of weekly data,
today's coin list only (survivorship), not a paper record.

Revisit with more history once Robinhood's scanner or an allowed data source gives 2+ years of daily data.
