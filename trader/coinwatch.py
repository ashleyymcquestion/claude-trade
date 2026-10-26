"""Short paper test of a coin or stock the bots don't trade (CHIP = USD.AI, USAR = USA Rare Earth):
`python -m trader coin-watch --symbol CHIP --bid B --ask A [--hours 72]` on every hourly run.

Paper only, never trades. The first call buys $2.00 (or --start dollars) of the coin on paper at Robinhood's ask (what a real buy
would pay) and starts a HOURS-hour test; every later call logs the quote and marks the position at the bid
(what selling would bring in) to data/coinwatch/<SYM>.csv. The first call at or after the end sells at the bid
and closes the test; later calls report the result and change nothing.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

DIR = Path("data/coinwatch")
START = 2.00
HOURS = 48


def step(symbol: str, bid: float, ask: float, now: datetime, hours: float = HOURS,
         start: float = START) -> dict:
    if not 0 < bid <= ask:
        raise ValueError(f"bad quote: bid {bid}, ask {ask}")
    DIR.mkdir(parents=True, exist_ok=True)
    sf = DIR / f"{symbol}-state.json"
    st = json.loads(sf.read_text()) if sf.exists() else None
    if st and st.get("closed"):
        return {"done": True, **st}
    if st is None:
        st = {"symbol": symbol, "opened": now.isoformat(timespec="minutes"),
              "ends": (now + timedelta(hours=hours)).isoformat(timespec="minutes"),
              "entry_ask": ask, "start": start, "units": start / ask}
    value = st["units"] * bid
    row = {"time": now.isoformat(timespec="minutes"), "bid": bid, "ask": ask, "value": round(value, 4),
           "ret": round(value / st.get("start", START) - 1, 4)}
    if now >= datetime.fromisoformat(st["ends"]):
        st.update(closed=row["time"], exit_bid=bid, final_value=row["value"], ret=row["ret"])
        row["closed"] = True
    new = not (DIR / f"{symbol}.csv").exists()
    with open(DIR / f"{symbol}.csv", "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["time", "bid", "ask", "value", "ret", "closed"])
        if new:
            w.writeheader()
        w.writerow(row)
    sf.write_text(json.dumps(st, indent=1) + "\n")
    return {**row, "ends": st["ends"]}
