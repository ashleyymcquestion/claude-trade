# Quantum computing and investing (RebelCon 2026)

Oct 21, 2026. Notes from Nadab's quantum session at RebelCon 2026, Las Vegas. The speaker isn't on
the public speaker list ([RebelCon prep page](https://marketrebellion.com/rebelcon26-prep-package/)),
so his full name and company are unconfirmed.

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
7. **It's real today:** companies like "Excite Capital" (name as heard) and Quantum eMotion already use
   it to make money.

## Fact-check

- **Point 1 oversells it.** A quantum computer doesn't see every path and pick the exit. It holds many
  possibilities in superposition, but reading the answer collapses it to one result. The speedup comes
  only from algorithms that use interference (point 4), and only for certain problems.
- **Point 4 is the accurate one.** That is how quantum algorithms work: make wrong answers cancel and
  right ones reinforce (amplitude amplification, as in Grover's search).
- **Point 5:** the speedup for searching is roughly the square root of the number of options, not
  "all at once". Portfolio optimization on quantum hardware is still at the research and pilot stage. Our
  bot's problem (a few stocks and coins, $4) is solved exactly and instantly on a regular computer.
- **Point 7:** Quantum eMotion (QNC) sells quantum random-number security products and is a small,
  early-stage company. The only 2026 revenue numbers found online came from spam pages with conflicting
  figures. Check its filings before trusting any claim that it's profitable. "Excite Capital" couldn't
  be identified.
- **Point 3 is a metaphor only.** Virtual particles have nothing to do with markets.

## What we took from it

The pond idea is now a watchlab source, **stacked**: a stock flagged by two or more independent signals
on the same day (top gainer, unusual volume, politician buy, social buzz) gets its own paper trade.
The scoreboard will show whether agreeing signals beat single ones. See
[watchlab-strategies.md](watchlab-strategies.md).
