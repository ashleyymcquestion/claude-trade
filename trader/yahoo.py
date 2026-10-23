"""Download daily bars from Yahoo's chart API: `python -m trader.yahoo [--universe] [SYM ...]`.

Writes data/yahoo/<SYM>.csv (date, open, high, low, close, volume), split-adjusted, from 1990 (or listing).
--universe downloads UNIVERSE into data/yahoo/universe/ (git-ignored: ~40 MB, re-run to rebuild it).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

DIR = Path("data/yahoo")
# ~110 liquid US stocks across sectors, including some that crashed (PTON, LCID, ZM, ...), so a test isn't only winners
UNIVERSE = """AAPL MSFT NVDA AMD AMZN GOOGL META TSLA NFLX AVGO ORCL CRM ADBE INTC CSCO QCOM TXN MU AMAT LRCX KLAC
MRVL PANW CRWD SNOW SHOP PYPL UBER ABNB PLTR COIN MSTR SMCI ARM TSM ASML JPM BAC GS MS WFC C V MA AXP UNH LLY
JNJ PFE MRK ABBV TMO ISRG XOM CVX COP OXY SLB CAT DE BA GE HON LMT RTX UPS FDX WMT COST HD LOW TGT NKE SBUX
MCD KO PEP PG DIS T VZ CMCSA ENPH FSLR ROKU ZM DOCU TTD NET DDOG MDB OKTA TWLO ETSY W PTON CVNA AFRM HOOD RIVN
LCID SOFI DKNG BABA NIO SNAP PINS SPOT INTU NOW""".split()


def fetch(symbol: str, folder: Path) -> int:
    end = int(time.time())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=631152000&period2={end}&interval=1d"
    raw = subprocess.run(["curl", "-sS", "-A", "Mozilla/5.0", url], capture_output=True, text=True, check=True).stdout
    res = json.loads(raw)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(folder / f"{symbol}.csv", "w") as f:
        f.write("date,open,high,low,close,volume\n")
        for i, t in enumerate(res["timestamp"]):
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, l, c) or o <= 0:
                continue
            f.write(f"{datetime.fromtimestamp(t, UTC).date()},{o},{h},{l},{c},{q['volume'][i] or 0}\n")
            n += 1
    return n


if __name__ == "__main__":
    args = sys.argv[1:]
    folder = DIR / "universe" if "--universe" in args else DIR
    syms = [a for a in args if not a.startswith("--")] or (UNIVERSE if "--universe" in args else [])
    for s in syms:
        try:
            print(s, fetch(s, folder))
        except Exception as e:          # one bad symbol shouldn't stop the rest
            print(s, "failed:", e)
        time.sleep(0.4)
