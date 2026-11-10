"""Take-Two (TTWO) test position on the Robinhood agentic account, real money (the user asked on Nov 10, 2026,
"can you start trading ttwo" and picked: $1 once, sold at +8% profit or -8% loss; Robinhood's dollar-order
minimum is $1, so $0.50 was not possible). Same rules and state file as the SPCX test (see trader/spcx_test.py);
only the symbol, ledger and order ids differ.

Ledger (committed, data/ttwo-test.json); state file for `plan`:
    {"now": "...", "cash": "1.53", "mark": 222.4, "quantity": "0", "open_order": false}
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from . import spcx_test

LEDGER = Path("data/ttwo-test.json")
SYMBOL = "TTWO"
REF_NAMESPACE = uuid.UUID("9a4c2e71-3b5d-4f86-8e1a-7d2c6b0f4e39")
DEFAULT_LEDGER = {**spcx_test.DEFAULT_LEDGER, "stop_pct": 0.08, "target_pct": 0.08}


def load_ledger(path: Path = LEDGER) -> dict:
    return {**DEFAULT_LEDGER, **json.loads(path.read_text())} if path.exists() else dict(DEFAULT_LEDGER)


def plan(ledger: dict, state: dict) -> dict:
    return spcx_test.plan(ledger, state, symbol=SYMBOL, ns=REF_NAMESPACE)


record = spcx_test.record
