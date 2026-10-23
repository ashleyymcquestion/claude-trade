# $100 option bets: real-price backtest (Oct 22, 2026)

Question: which option contract costing $100 or less makes money?
Data: real daily prices of every expired monthly NVDA, AMD, PLTR and QQQ contract from May to Oct 2026
(`data/optionhist/`, from Robinhood's get_option_historicals; about 50,000 bars). Backtest:
`python -m trader.budgetbacktest`. Buy 28-42 days before expiration at the close + $0.03, sell at the close - $0.03.

## Results (average P&L per $100 bet, all four stocks, every eligible day)

| Contract | Exit | Bets | Won | Doubled | Lost it all | Avg per $100 |
|---|---|---|---|---|---|---|
| Call nearest the price under $100 | sell at +100%, else hold to expiration | 196 | 50% | 50% | 50% | **+$54** |
| same | lab rules (+100% / -50% / 7 days left) | 196 | 34% | 34% | 0% | +$27 |
| same | hold to expiration | 196 | 13% | 13% | 86% | +$620 (one huge AMD run) |
| Cheapest call ($0.10+) | sell at +100%, else hold | 196 | 43% | 43% | 57% | +$38 |
| Call spread under $100 | any | 20 | 25-35% | | | -$4 to -$33 |
| Put nearest the price under $100 | any | 198 | 0-12% | | | **-$43 to -$100** |
| Put spread under $100 | hold | 20 | 65% | 0% | 35% | +$16 |

Without AMD's May-June run (it went from $245 to $450), the winner still averaged +$49 per $100 (190 bets, 48% won).
By expiration month: May +184%, Jun +108%, Jul -60%, Aug +71%, Sep -90%, Oct +135%. So 4 of 6 months won,
and two months lost almost everything.

## What it means
- The winning bet: the call closest to the stock price that costs under $100, sold as soon as it doubles. Half the
  time it doubled; the other half it went to zero. That only pays because the winners are bigger than the losers.
- Puts lost almost every time: these four stocks rose a lot from May to October (NVDA +13%, AMD +107%,
  PLTR +53%, QQQ +15%). Cheap calls look great in a rising market and will look terrible in a falling one.
- Bets on the same stock in the same month move together, so this is really about 24 separate bets
  (4 stocks x 6 months), of which 14 made money. Not proof; a good reason to paper trade it.

## In the lab
The options lab now paper-trades `budget100` daily: for NVDA, AMD, PLTR and QQQ it buys the call nearest the price
that costs $100 or less (at the ask), sells at +100% (at the bid), else holds to expiration.
