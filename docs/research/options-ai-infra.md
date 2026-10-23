# $100 options on AI-infrastructure stocks (Oct 23, 2026)

Real daily prices of expired monthly options (May-Oct 2026) for IREN, OKLO, VRT, VST, NBIS and CRWV
(`data/optionhist/`, collected from Robinhood). Same test as options-2day.md:
`HOLD=5 SYMS="IREN OKLO VRT VST NBIS CRWV" python -m trader.twoday`. Contracts with 28-45 days left, $100 or less,
bought at the close + $0.03 and sold N trading days later at the close - $0.03.

| Hold | Nearest call: May-Jul / Aug-Oct (avg, median, won) | Nearest put: May-Jul / Aug-Oct |
|---|---|---|
| 2 days | +31%, +9%, 54% / -12%, -26%, 28% | -8%, -16%, 36% / -14%, -30%, 23% |
| 5 days | +170%, +44%, 64% / -27%, -45%, 25% | -3%, -26%, 34% / -16%, -39%, 28% |
| 7 days | +245%, +82%, 72% / -55%, -69%, 9% | -17%, -34%, 31% / -29%, -48%, 22% |

Call spreads and put spreads lost in both halves at every hold except small gains for call spreads in May-Jul.
All bets together: -14% (2 days), +1% (5 days), +1% (7 days), winning only 27-29% of the time.

What it means: calls on these stocks paid hugely while they ran up in the summer (NBIS 145 -> 278, CRWV and IREN
too), then lost most of their value in Aug-Oct. Puts lost in both halves, even in the fall sell-off, because these
options are priced for big moves, so a $100 put needs an even bigger drop to pay. The result is a bet on the
direction of the AI trade, not an edge: no setup won in both halves.
