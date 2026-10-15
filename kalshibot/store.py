"""SQLite record of every decision and trade, so results can be checked after fees."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .model import Decision

NY = ZoneInfo("America/New_York")

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, ticker TEXT NOT NULL, action TEXT NOT NULL,
    reason TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,      -- at most one trade per market
    side TEXT NOT NULL, count INTEGER NOT NULL, price_cents INTEGER NOT NULL,
    fee_cents INTEGER NOT NULL, close_time TEXT NOT NULL, created_at TEXT NOT NULL,
    ny_date TEXT NOT NULL, dry_run INTEGER NOT NULL, environment TEXT NOT NULL,
    order_id TEXT, status TEXT NOT NULL,           -- 'open' or 'settled'
    result TEXT, pnl_cents INTEGER
);
"""


def ny_date(ts: datetime) -> str:
    return ts.astimezone(NY).date().isoformat()


class Store:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def record_decision(self, d: Decision, ts: datetime) -> None:
        detail = {k: v for k, v in asdict(d).items() if k not in ("ticker", "action", "reason")}
        self.db.execute("INSERT INTO decisions (ts, ticker, action, reason, detail) VALUES (?,?,?,?,?)",
                        (ts.isoformat(), d.ticker, d.action, d.reason, json.dumps(detail)))
        self.db.commit()

    def has_trade(self, ticker: str) -> bool:
        return self.db.execute("SELECT 1 FROM trades WHERE ticker=?", (ticker,)).fetchone() is not None

    def record_trade(self, *, ticker: str, side: str, count: int, price_cents: int, fee_cents: int,
                     close_time: datetime, ts: datetime, dry_run: bool, environment: str,
                     order_id: str | None) -> None:
        self.db.execute(
            "INSERT INTO trades (ticker, side, count, price_cents, fee_cents, close_time, created_at,"
            " ny_date, dry_run, environment, order_id, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,'open')",
            (ticker, side, count, price_cents, fee_cents, close_time.isoformat(), ts.isoformat(),
             ny_date(ts), int(dry_run), environment, order_id))
        self.db.commit()

    def open_trades(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM trades WHERE status='open' ORDER BY id").fetchall()

    def settle(self, trade_id: int, result: str) -> int:
        row = self.db.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        payout = 100 * row["count"] if result == row["side"] else 0
        pnl = payout - row["count"] * row["price_cents"] - row["fee_cents"]
        self.db.execute("UPDATE trades SET status='settled', result=?, pnl_cents=? WHERE id=?",
                        (result, pnl, trade_id))
        self.db.commit()
        return pnl

    def day_stats(self, day: str) -> dict:
        """Trades placed on NY ``day``: count, realized P&L, and cost still at risk in open ones."""
        rows = self.db.execute("SELECT * FROM trades WHERE ny_date=?", (day,)).fetchall()
        realized = sum(r["pnl_cents"] for r in rows if r["status"] == "settled")
        at_risk = sum(r["count"] * r["price_cents"] + r["fee_cents"] for r in rows if r["status"] == "open")
        return {"trades": len(rows), "realized_cents": realized, "at_risk_cents": at_risk}

    def summary(self) -> dict:
        rows = self.db.execute("SELECT * FROM trades WHERE status='settled'").fetchall()
        wins = sum(1 for r in rows if r["pnl_cents"] > 0)
        return {
            "settled": len(rows), "wins": wins,
            "win_rate": wins / len(rows) if rows else None,
            "pnl_usd": sum(r["pnl_cents"] for r in rows) / 100,
            "fees_usd": sum(r["fee_cents"] for r in rows) / 100,
            "open": len(self.open_trades()),
            "decisions": self.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
        }

    def recent_trades(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in
                self.db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def close(self) -> None:
        self.db.close()


def now_utc() -> datetime:
    return datetime.now(UTC)
