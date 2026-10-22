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

## The edge filter (added Oct 21): only take trades where the odds and the payoff favor us

No options strategy wins every time. What the lab can do is skip trades where the math is against us.
For every idea it now prices several structures and keeps the best one that passes BOTH tests:

1. **Probability of profit at least 55%**, and
2. **Positive expected profit**: (chance of winning x average win) + (chance of losing x average loss) > 0.

Both numbers come from [optionlab](https://github.com/rgaveiga/optionlab) using the stock's **realized**
volatility (how much it actually moved over the last 30 trading days), not the volatility baked into the
option's price ("implied"). That gap is the edge: when options are priced for bigger swings than the
stock really makes, **selling** them has the odds; when they're priced cheap, **buying** them does.

| Structure | What it is | When it tends to pass |
|---|---|---|
| Long call | Buy a call at or just above the price | Options cheap vs real moves |
| Bull call spread | Buy that call, sell one ~10% higher | Cheaper than a call; still needs a rise |
| Bull put spread | Sell a put ~5% below, buy one ~10% below (a credit with capped risk) | Options rich; the stock just has to not fall much |
| Cash-secured put (wheel) | Sell a put ~5% below | Options rich |
| Covered call (wheel) | Sell a call ~5% above cost, while holding 100 shares | Options rich |

**Day one, scored with the filter** (these six were opened before it existed and are kept as a test):

| Position | Option price implies | Stock actually moved | Odds | Expected | Filter |
|---|---|---|---|---|---|
| UAL call (bought) | 48% | 38% | 28% | -$161 | Fail |
| ASST call (bought) | 101% | 71% | 26% | -$116 | Fail |
| SEI call (bought) | 92% | 81% | 29% | -$110 | Fail |
| NVDA put (sold) | 41% | 26% | 86% | +$268 | Pass |
| QQQ put (sold) | 26% | 17% | 90% | +$432 | Pass |
| SPCX put (sold) | 54% | 51% | 71% | +$2 | Pass (barely) |

The lesson: today, options were priced for bigger moves than these stocks actually made, so buying
calls had the odds against us and selling puts had them for us. From now on the lab would have traded
the selling side and skipped the calls. The scoreboard compares the odds the model promised
("model_odds") with the real win rate, so we'll see whether the model can be trusted.

**Two caveats:** past volatility doesn't promise future volatility (a surprise drop can blow through a
sold put), and selling options means many small wins and occasional big losses. That's why spreads cap
the loss, and why real money waits for a long record.

## Tools installed (free)

| Tool | Used for |
|---|---|
| [optionlab](https://github.com/rgaveiga/optionlab) | Odds, expected profit, max win/loss for every candidate trade (in use) |
| [py_vollib](https://github.com/vollib/py_vollib) | Standard option pricing, implied volatility and Greeks (available) |
| [optopsy](https://github.com/michaelchu/optopsy) | Testing strategies on years of past option chains (ready once we have past option data) |

## Backtest: buying calls on past prices (added Oct 22)

`python -m trader opt-backtest` replays daily prices for NVDA, AMD, PLTR and QQQ since 2019. It buys a
35-day at-the-money call each time a signal fires and uses the lab's exit rules (+100%, -50%, or 7 days
before expiry). There's no free history of real option prices, so the option prices are **modeled**:
implied volatility = the stock's realized volatility x a markup, minus a bid/ask cost on each side.

| Assumption | Signal | Trades | Win rate | Avg call return | Avg stock return, same days |
|---|---|---|---|---|---|
| Cheap options (1.0x, 3% cost) | Momentum | 324 | 38% | +10.5% | +1.6% |
| | Pullback | 135 | 47% | +24.4% | +2.6% |
| **Typical (1.3x, 5% cost)** | Momentum | 274 | 33% | **-3.7%** | +1.9% |
| | Pullback | 125 | 39% | **+2.2%** | +2.7% |
| Pricey options (1.6x, 8% cost) | Momentum | 250 | 27% | -15.1% | +2.3% |
| | Pullback | 117 | 30% | -10.2% | +3.4% |

**What it says:** whether buying calls pays depends almost entirely on how expensive the options are.
At typical prices, calls on momentum lost a little and calls on pullbacks (strong stock, short-term dip)
roughly broke even. Most trades lose (60-70%); a few big winners carry the rest. The pullback signal was
the better of the two in every case, which matches the stock bot's mean-reversion entries.

**Caveats:** these four stocks were huge winners since 2019 (picked with hindsight), so real results
would likely be worse; modeled prices miss earnings jumps and volatility spikes. The live paper lab
will tell us whether real option prices look more like the "cheap" or "pricey" row.

## Call tests and pullback calls (added Oct 22)

The user wants the lab to keep paper-testing calls until one strategy wins. Two changes:

1. **Every bullish idea now also opens a plain call** ("follow_flow/call", "momentum_calls/call",
   "pullback_calls/call"): an at-the-money call 30-45 days out, bought at the ask, with no edge filter. The
   filtered pick still opens next to it when something passes. So each idea gets two scoreboard rows: raw calls
   and the filter's choice, and every call carries the odds the model gave it, so we can see whether the model
   is right.
2. **pullback_calls**, the best signal in the call backtest: NVDA, AMD, PLTR or QQQ above its 200-day average
   with RSI(14) under 35 (a strong stock on a short dip). The signal is checked every daily run from the stored
   bars; on Oct 22 NVDA, AMD and QQQ all showed it after the sell-off.

Exits are unchanged: +100%, -50%, or 7 days before expiry, sold at the bid. A strategy "wins" only with a
real record: 20+ closed trades, a win rate and average return that beat simply holding the stock over the same
days. Real money stays off until then, and until you OK it.
