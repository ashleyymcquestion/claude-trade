"""Paper swing test (paper only, never real money): does swinging a coin beat holding it?

The user asked on Oct 26 whether CHIP could be swing traded. Each hourly run feeds the bid and ask:
  - flat: buy all paper cash at the ask once the bid is `dip` (10%) below the highest bid seen since
    the start or the last sell;
  - holding: sell everything at the bid once the bid is `rise` (10%) above the buy price (the ask
    paid). No stop.
Buying at the ask and selling at the bid counts the spread on every round trip. A buy-and-hold of the
same paper dollars from the first call (at its ask) is the benchmark. The test ends after `days`.

State (data/swingpaper/<SYMBOL>-state.json) and one CSV row per call (data/swingpaper/<SYMBOL>.csv).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path

DIR = Path("data/swingpaper")
START, DIP, RISE, DAYS = 2.0, 0.10, 0.10, 14


def step(st: dict | None, bid: float, ask: float, now: datetime, start: float = START,
         dip: float = DIP, rise: float = RISE, days: int = DAYS) -> tuple[dict, dict]:
    """Advance the test one quote; returns (new state, row). A closed test is returned unchanged."""
    if st is None:
        st = {"opened": now.isoformat(timespec="minutes"),
              "ends": (now + timedelta(days=days)).isoformat(timespec="minutes"),
              "start": start, "dip": dip, "rise": rise, "cash": start, "units": 0.0, "entry": None,
              "high": bid, "trades": [], "hold_units": start / ask}
    st = {**st, "trades": list(st["trades"])}
    action = ""
    if st.get("closed"):
        action = "closed"
    elif st["units"] == 0:
        st["high"] = max(st["high"], bid)
        if bid <= st["high"] * (1 - st["dip"]):
            st["units"], st["entry"], st["cash"] = st["cash"] / ask, ask, 0.0
            st["trades"].append({"time": now.isoformat(timespec="minutes"), "side": "buy", "price": ask})
            action = "buy"
    elif bid >= st["entry"] * (1 + st["rise"]):
        st["cash"], st["units"], st["entry"], st["high"] = st["units"] * bid, 0.0, None, bid
        st["trades"].append({"time": now.isoformat(timespec="minutes"), "side": "sell", "price": bid})
        action = "sell"
    value = st["cash"] + st["units"] * bid
    hold = st["hold_units"] * bid
    if not st.get("closed") and now >= datetime.fromisoformat(st["ends"]):
        st["closed"] = {"time": now.isoformat(timespec="minutes"), "value": round(value, 4), "hold": round(hold, 4)}
    row = {"time": now.isoformat(timespec="minutes"), "bid": bid, "ask": ask, "action": action,
           "value": round(value, 4), "ret": round(value / st["start"] - 1, 4),
           "hold": round(hold, 4), "hold_ret": round(hold / st["start"] - 1, 4),
           "position": "coin" if st["units"] else "cash", "trades": len(st["trades"])}
    if st.get("closed"):
        row["closed"] = st["closed"]
    return st, row


def run(symbol: str, bid: float, ask: float, now: datetime, d: Path = DIR) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    sp, lp = d / f"{symbol}-state.json", d / f"{symbol}.csv"
    old = json.loads(sp.read_text()) if sp.exists() else None
    if old and old.get("closed"):
        return {"closed": old["closed"]}
    st, row = step(old, bid, ask, now)
    sp.write_text(json.dumps(st, indent=1) + "\n")
    new = not lp.exists()
    with lp.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["time", "bid", "ask", "action", "value", "ret", "hold", "hold_ret",
                                          "position", "trades"], extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)
    return row
