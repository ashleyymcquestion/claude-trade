# $100 options held 2 days (Oct 23, 2026)

`python -m trader.twoday`: every day from May to Oct 2026, buy a $100-or-less NVDA/AMD/PLTR/QQQ option (5 rules x 3
expiration ranges x 5 entry filters = 24 setups with enough trades), sell 2 trading days later, real prices with
$0.03 per side. Setups were ranked on May-Jul and then checked on Aug-Oct, months the ranking never saw.

- All 1,896 two-day bets: average **-12.8%**, only **27%** made money.
- The May-Jul winner (nearest call, 15-27 days, after an up day: +98%) made **-1.5%** in Aug-Oct.
- The average of all 24 setups in Aug-Oct: -25%.
- The only setup positive in both halves: the call nearest the price that costs <= $100 with 28-45 days left
  (+16% then +11% average per bet; after an up day +21% then +29%). It still lost money on 53-62% of bets;
  the average is positive because winners are bigger. Puts lost in every version (stocks rose May-Oct).

Verdict: no 2-day $100 options bet reliably makes money. The least-bad one wins under half the time.

## Longer holds (`HOLD=5 python -m trader.twoday`, also 3 and 10)
| Hold | All bets: avg / won | Best setup both halves (nearest call <= $100, 28-45 days left): May-Jul / Aug-Oct avg | won |
|---|---|---|---|
| 2 days | -13% / 27% | +16% / +11% | 43% / 38% |
| 3 days | -11% / 26% | +30% / +31% | 44% / 38% |
| 5 days | -1% / 23% | +70% / +56% | 41% / 32% |
| 10 days | +18% / 20% | +222% / +31% | 42% / 28% |

At 5 days the nearest call's typical (median) bet lost about 35% in both halves; about 40% of bets lost half or more and
18-29% doubled. Month by month its average was +96% (May), -43% (Jun), -52% (Jul), -40% (Aug), +159% (Sep):
big wins in a few strong months carry it. Longer holds raise the average and lower the win rate.
