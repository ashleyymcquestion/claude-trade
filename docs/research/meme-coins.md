# Meme coins: can a bot ride them? (Oct 24, 2026)

`python -m trader.memebacktest`: Yahoo daily closes, 2021 - Oct 2026, for 13 meme coins that still trade
(DOGE SHIB PEPE BONK WIF FLOKI BRETT POPCAT MOG PENGU FARTCOIN SPX MEW), 1% cost per side. Rules: buy and hold;
the weekly coin-momentum rule from the Robinhood bot (`trader/cryptomomentum.py`: top 1 or 3 by 2/4/8-week
return, coin above its 10-week average, only while BTC is above its 10-week average); 10/20/55-day breakouts.

| Period | BTC hold | Meme coins held | Momentum (6 settings) | Breakout (3 settings) |
|---|---|---|---|---|
| 2021 | +58% | +24x to +31x (only DOGE, SHIB, FLOKI existed) | +28x to +86x | +6x to +43x |
| 2022 | -65% | -60% to -93% | -42% to -55% | -11% to -39% |
| 2023 | +154% | -2% to +12x | **-17% to -53%** | +1.5x to +2.1x |
| 2024 | +112% | +87% to +100x | +7.7x to +230x | +3.2x to +6.6x |
| 2025 - Oct 2026 | -14% | **-51% to -96% (all 13)** | **-87% to -97%** | -33% to -62% |

## What it means
- The giant numbers in 2021 and 2024 are mostly hindsight: this list is the coins that became famous. Picking
  them in advance, out of thousands launched that year, is the hard part, and the test can't do that.
- The same rules gave back 87-97% in 2025-26. A strategy that turns $1 into $230 and then loses 97% is a
  lottery ticket with extra steps.
- Breakouts lost less in the bad years than momentum or holding, but still lost.

## The "Kimchi" trade
Kimchi (@kimchi1x) is a Solana meme-coin trader reported (unverified) to have made about $40M on the TRUMP
token, which means buying within minutes of launch on a Solana DEX. That can't be backtested here (no price
history before a coin lists on Yahoo/Coinbase/Robinhood), can't be done through the Coinbase or Robinhood
connectors (both only list a coin after it is established), and is a game of buying thousands of launches
where almost all go to zero. The survivors are the stories you hear.

## Verdict
No meme-coin rule here is safe to run with real money. If paper tested at all, the breakout rule is the only
one that didn't collapse in every bad year.
