# Quantum computing and investing (RebelCon 2026)

Oct 21, 2026. Notes from "The Quantum Future" at RebelCon 2026, Las Vegas: Marc X. LoPresti (Market
Rebellion CEO) and [Nadab Akhtar](https://milkeninstitute.org/index%2ephp/staff/nadab-akhtar), founder and
managing partner of Excite Capital, CEO of Project LightShift (room-temperature photonic quantum processor),
Milken Institute FinTech Advisory Council. Marc and likely Jon Najarian sit on Excite Capital's board. Live
doc: [The Quantum Future – RebelCon Notes](https://claude.ai/artifact/BLw1bZWvWgshBsYAaqrpe5).

## Talk summary

1. **Three kinds of computers**
   - Regular chip (CPU): one person solving a maze by trying one path at a time.
   - GPU / AI chip: a team of people, each trying a different path at the same time.
   - Quantum computer: like sensing all the paths at once and knowing which ones lead out.
2. **Superposition = a spinning coin.** While it spins it's neither heads nor tails; it's both. That's
   what lets quantum check lots of options at once.
3. **"Created or destroyed."** Tiny particles pop in and out of existence; he compares this to the stock
   market, where opportunities appear, vanish or change all day.
4. **The pond.** Drop two stones and the ripples cross. Where waves stack up they get bigger (good
   answers); where they cancel, the water goes flat (bad answers to ignore). Quantum keeps zooming in on
   the big waves until it finds the best answer.
5. **For investing:** quantum can scan a huge number of possible trades, drop the weak ones and focus on
   the best ones faster than a regular computer.
6. **It's a whole industry:** quantum security, quantum software, quantum sensing (super-precise
   measurement) and more.
7. **It's real today:** companies like Excite Capital and Quantum eMotion already use it to make money.

## How Excite Capital says it trades

- **Navigator, not prophet:** it doesn't try to predict the future; it reads what's happening right now
  and reacts, like a GPS rerouting.
- **Inputs:** only price, volume and time. It doesn't care why a stock moves (news, sentiment, hype) or which
  ticker it is.
- **What it measures:** velocity (how fast those numbers change) and acceleration (whether the change is
  speeding up or slowing down).
- **Three rule tiers:** absolute rules never broken (e.g. only stocks and currency pairs, a risk limit),
  hard rules that define correct behavior, and soft rules updated with real-time data.
- **Observation, state, constraint:** the $100 price is just what you see; the state underneath is buyers,
  sellers and liquidity; constraints are your capital and risk limits.

## Fact-check

- **Point 1 oversells it.** A quantum computer doesn't see every path and pick the exit. It holds many
  possibilities in superposition, but reading the answer collapses it to one result. The speedup comes
  only from algorithms that use interference (point 4), and only for certain problems.
- **Point 4 is the accurate one.** That is how quantum algorithms work: make wrong answers cancel and
  right ones reinforce (amplitude amplification, as in Grover's search).
- **Point 5:** the speedup for searching is roughly the square root of the number of options, not
  "all at once". Portfolio optimization on quantum hardware is still at the research and pilot stage. Our
  bot's problem (a few stocks and coins, $4) is solved exactly and instantly on a regular computer.
- **Point 7:** Excite Capital is Nadab's own firm, so "it makes money" is the speaker's claim about his
  own fund; no public track record was found. Quantum eMotion (QNC, NYSE American / TSX Venture) sells
  quantum random-number security products and reported no revenue and a $10.55M net loss for 2025
  ([Quantum eMotion IR](https://www.quantumemotion.com/press-release/quantum-emotion-announces-brokered-life-financing-of-c-6-000-000),
  [edgar.tools](https://app.edgar.tools/companies/QNC)): early-stage and speculative.
- **Excite's method needs no quantum computer.** Velocity and acceleration of price and volume are
  ordinary momentum measures any computer can calculate; the quantum part is how they say they search
  the options.
- **Point 3 is a metaphor only.** Virtual particles have nothing to do with markets.

## What we took from it

Excite's method is now a watchlab source, **velocity**: any liquid stock up more than 2% today, more than
0.5% in the last hour (speeding up) and trading 2x+ its normal volume. The bot's rules already follow
the three tiers: absolute (paper only, liquid stocks, $1 size), hard (-8% stop, +15% target, time exit),
soft (fresh scanner data every day).

The pond idea is a second source, **stacked**: a stock flagged by two or more independent signals
on the same day (top gainer, unusual volume, politician buy, social buzz) gets its own paper trade.
The scoreboard will show whether agreeing signals beat single ones. See
[watchlab-strategies.md](watchlab-strategies.md).
