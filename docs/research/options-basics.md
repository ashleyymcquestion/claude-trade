# Options basics, with the bot's real paper trades

Oct 21, 2026. A plain-English guide for the options lab (`trader/optionslab.py`, records in
`data/optionslab/`). Every example below is a real Robinhood quote the lab used on its first day.
Everything here is paper trading: no real orders.

## The five words

| Word | Meaning | Example (UAL call, opened Oct 21) |
|---|---|---|
| Call / put | A call gains when the stock rises; a put gains when it falls | Call on United Airlines |
| Strike | The price the contract lets you buy (call) or sell (put) at | $102 (stock was $101.40) |
| Expiration | The last day the contract exists | Nov 20, 2026 (30 days) |
| Premium | The price of the contract, per share | $6.10 ask |
| Contract | Always 100 shares, so cost = premium x 100 | $6.10 x 100 = **$610** |

**Break-even:** a call only makes money at expiration if the stock ends above strike + premium. UAL has
to rise from $101.40 to **$108.10** (+6.6%) in 30 days just to break even. Robinhood put the chance of
profit at about 33%.

## Four things that surprise beginners

1. **One contract is big money.** The cheapest usable contract today cost $370 (ASST $31 call). The MCP
   account has $0.53, which is why the lab is paper only.
2. **The spread is a hidden fee.** You buy at the ask and sell at the bid. FHN's $24 call was 25 cents bid,
   40 cents ask: buy and sell right away and you lose 37% instantly. The lab skips any contract whose
   spread is over 30% of the price; that ruled out FHN, CECO and KYIV on day one.
3. **Time decay ("theta") eats value every day.** The NVDA $207.50 put loses about $0.14 per share a day
   ($14 per contract) if nothing else changes. Buyers fight the clock; sellers get paid by it.
4. **Most long options lose.** Robinhood's own "chance of profit" for today's calls was 21–33%. Buyers
   win big when they're right, but they're usually not.

## The three strategies the lab tests

| Strategy | What it does | Day-one trades | Risk |
|---|---|---|---|
| **follow_flow** | Buys a near-the-money call ~30–45 days out when unusual, call-heavy options activity shows up (the "Heat Seeker" idea) | UAL $102 call @ $6.10 | Can lose the whole premium |
| **momentum_calls** | The same call on the velocity / top-gainer picks, instead of buying the stock | ASST $31 call @ $3.70, SEI $70 call @ $7.70 | Same; tests whether leverage helps or hurts |
| **wheel** | Sells a cash-secured put ~5% below the price and collects the premium. If the stock falls below the strike, you buy 100 shares, then sell covered calls ~5% above until they're called away | NVDA $207.50 put for $455, QQQ $686 put for $673, SPCX $149 put for $535 | You can be forced to buy 100 shares in a falling stock |

**Exit rules for calls:** sell at +100% (double), at -50%, or 7 days before expiration, always at the bid.

**Why the wheel needs so much cash:** selling a put means promising to buy 100 shares at the strike. The
NVDA put needs $20,750 set aside to earn $455 (2.2% in a month), QQQ $68,600 for $673 (1.0%), SPCX
$14,900 for $535 (3.6%). SPCX pays more because it's more volatile: the market prices bigger swings
(implied volatility 54% vs 26% for QQQ).

## How we'll know if any of it works

`python -m trader opt-score` prints the scoreboard: win rate, average return on premium and the stock's
own move over the same days (to see whether the option beat simply owning the stock), and the wheel's
premium kept and share gains or losses. A strategy is only worth real money after a solid record AND
enough cash to trade it properly, and only with your OK.
