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
