# "Kimchi-style" meme coin rules (2026-10-24)

Kimchi (@kimchi1x, pseudonymous Solana trader) reportedly made ~$40M buying TRUMP within hours of its January
2025 launch. That claim has no public on-chain proof, and the skill (watching new launches 15 hours a day and
holding through a 100x) can't be coded. `python -m trader.memebacktest` tests rules a bot *could* follow, on
Yahoo daily prices for DOGE, WIF, TRUMP, PENGU, POPCAT, FARTCOIN, BRETT and MEW (1% cost per side).
SHIB, PEPE, BONK, FLOKI and MOG are left out: Yahoo rounds their sub-penny prices to one or two digits, which
fakes 25% daily jumps (it produced a bogus "PEPE +25%" signal on Oct 23).

| Rule | Trades | Win rate | Avg trade | Median trade |
|---|---|---|---|---|
| Buy on day 2 of trading, hold 30 days | 8 | 62% | +121% | +115% |
| Chase the day's biggest 20%+ riser, hold 1 day | 140 | 43% | +1% | −2% |
| ... hold 3 days | 102 | 44% | +10% | −3% |
| ... hold 7 days | 76 | 47% | +18% | −3% |
| 30-day high on 3x volume, hold 7 days | 78 | 46% | +21% | −1% |
| Hold all 8 coins from Jan 2025 | | | −89% avg | −90% |
| Hold BTC from Jan 2025 | | | −15% | |

What it means:
- **Averages come from a few monster trades.** The median trade loses on every chase/breakout rule; a handful
  of weeks (DOGE Apr 2021, POPCAT/PEPE Feb 2024) make the whole result.
- **It only works in a meme mania.** Breakout averaged +61% per trade in 2024, +2% in 2025 and −1% in 2026.
  Chase-7d: +37% in 2024, −2% in 2025, −6% in 2026. We are not in a mania now.
- **"Buy at launch" is survivorship bias**: the 8 coins are the famous survivors. Thousands of launches went to
  zero and aren't in the list.
- Holding meme coins since January 2025 lost ~89% on average.

Not for real money. Daily paper test from today (ROBINHOOD.md 17k): `python -m trader.memebacktest --download
--paper` logs chase (biggest 20%+ riser) and breakout signals at the UTC daily close, each held 7 days, in
data/memepaper/trades.json. If it is ever tried for real, Coinbase is the better venue (far more meme coins
than Robinhood), and only after the paper results hold up outside a mania.
