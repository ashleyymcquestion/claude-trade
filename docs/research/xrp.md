# Adding XRP to the crypto bot (2026-10-23)

`python -m trader.xrptest` runs the Robinhood crypto strategy (config.robinhood.toml, 1% cost per side)
on Yahoo daily coin prices, with and without XRP. Raw numbers: `xrp-output.json`.

| From | Bot today (BTC/ETH/SOL) | Bot + XRP | XRP alone, same rules | Hold BTC | Hold XRP |
|---|---|---|---|---|---|
| 2021-06 | +4.5%/yr, worst −53% | +6.9%/yr, worst −51% | +5.0%/yr, worst −44% | +15.8%/yr, worst −77% | +4.7%/yr, worst −78% |
| 2023-01 | +18.4%/yr, worst −44% | +21.9%/yr, worst −47% | +15.0%/yr, worst −44% | +51.6%/yr, worst −53% | +42.2%/yr, worst −72% |
| 2025-01 | −15.2%/yr, worst −44% | −16.0%/yr, worst −47% | −16.9%/yr, worst −44% | −8.1%/yr, worst −53% | −27.6%/yr, worst −72% |

- XRP added a little over 2021–2026 (+2–3%/yr) and cost a little since 2025. The bot gives it about 7–9% of
  the money on average, so it never matters much either way.
- The bigger finding: in every window the strategy trailed simply holding BTC. It cuts the worst drops
  (−44% to −53% vs −77% for BTC since 2021) but gives up most of the gain, partly because Robinhood's ~2%
  round-trip spread is charged on every rebalance.
- Daily paper test from today: `python -m trader xrp-paper --price <XRP mark>` (ROBINHOOD.md 17j) sizes a
  $2.00 paper XRP position the way the 4-coin strategy would, logged to data/xrppaper/log.csv next to just
  holding $2.00 of XRP. Day one (Oct 23, XRP $1.29): target weight 6.3%, paper bought $0.13.
