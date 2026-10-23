# Turtle trend following and the overnight effect (Oct 23, 2026)

`python -m trader.classics` on Yahoo daily bars (data/yahoo/, 1990-2026 depending on symbol). Raw output:
classics-output.txt. Costs: 0.05% per side on stocks/ETFs, 1% per side on crypto.

## Turtle (long only, one unit, no adding; System 1 = 20-day breakout / 10-day exit, System 2 = 55 / 20, 2N stop)

| Since 2016 | Hold: per year / worst drop | System 1 | System 2 |
|---|---|---|---|
| SPY | +13.2% / -34% | +6.9% / -14% | +4.0% / -18% |
| QQQ | +18.9% / -36% | +7.0% / -22% | +7.8% / -15% |
| NVDA | +67.7% / -66% | +29.0% / -41% | +30.0% / -36% |
| AMD | +62.2% / -65% | +43.5% / -42% | +28.2% / -38% |
| BTC | +39.7% / -83% | +32.5% / -66% | +34.9% / -53% |
| ETH | +16.8% / -94% | +39.8% / -57% | +16.8% / -66% |
| TSLA | +34.9% / -74% | +36.5% / -52% | +19.9% / -42% |

Result: on 14 symbols the Turtle rules beat simply holding on only 3 (AMD over its full history, ETH, TSLA
since 2016). Everywhere else they made roughly half the return. What they do well is cut the worst drop
roughly in half, because they sit in cash about 55-60% of the time. Win rates are 40-50%: small losses,
a few big wins. The real Turtles traded dozens of futures markets and also shorted; long-only on a few
stocks in a rising market misses most of their edge.

## Overnight effect (buy at the close, sell at the next open; vs open to close)

| | Overnight only | Daytime only | Overnight after costs | Hold |
|---|---|---|---|---|
| QQQ since 1999 | +13.1%/yr | -2.8%/yr | -12.1%/yr | +10.0%/yr |
| SPY since 1993 | +8.1%/yr | +0.7%/yr | -16.0%/yr | +8.8%/yr |
| IWM since 2000 | +11.9%/yr | -4.3%/yr | -13.0%/yr | +7.1%/yr |
| NVDA since 2016 | +51.3%/yr | +10.6%/yr | +17.6%/yr | +67.3%/yr |

The effect is real: almost all of the gains came overnight, and the daytime was flat or negative. But
trading in and out every day pays the spread twice a day (about 25% a year at 0.05% a side), which turns
it into a big loser. Our bots also can't trade at the exact open and close (fractional orders, hourly runs).
Usable lesson: when the bots buy, later in the day is better; when they sell, earlier is better.

## Verdict
Neither beats buy-and-hold on its own. The Turtle rules are a good crash brake (half the drawdown), so
they could be a paper source for "when to be out of the market", not a money maker in a bull market.
