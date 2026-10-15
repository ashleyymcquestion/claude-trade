"""Durable state for quantbot: orders, fills, equity, position metadata, realized P&L, flags,
events, heartbeats and regimes, in one SQLite file.

Design notes
  * WAL journal mode so the dashboard (Streamlit) can read while the runner writes.
  * One connection per thread (``check_same_thread=False`` so ``close()`` can close them all from
    any thread); writes are serialized with a process-local lock, and ``busy_timeout`` covers
    writers in other processes.
  * Timestamps are stored as fixed-width ISO-8601 UTC text (microsecond precision, ``+00:00``) so
    lexical order equals time order and range queries can compare strings. Naive datetimes are
    interpreted as UTC. Every datetime returned is tz-aware UTC.
  * The schema is created idempotently; ``schema_version`` records the version for migrations.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .models import AccountSnapshot, Fill, Order, OrderStatus, Side

SCHEMA_VERSION = 1

_OPEN_STATUSES = tuple(s.value for s in OrderStatus if s.is_open)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    client_order_id   TEXT PRIMARY KEY,
    symbol            TEXT NOT NULL,
    side              TEXT NOT NULL,
    qty               REAL NOT NULL,
    status            TEXT NOT NULL,
    broker_order_id   TEXT,
    filled_qty        REAL NOT NULL DEFAULT 0,
    filled_avg_price  REAL,
    submitted_at      TEXT,
    updated_at        TEXT,
    strategy          TEXT NOT NULL DEFAULT '',
    reject_reason     TEXT,
    created_at        TEXT NOT NULL,
    seq               INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS fills (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id  TEXT NOT NULL,
    symbol           TEXT NOT NULL,
    side             TEXT NOT NULL,
    qty              REAL NOT NULL,
    price            REAL NOT NULL,
    fee              REAL NOT NULL,
    ts               TEXT NOT NULL,
    UNIQUE (client_order_id, ts, qty, price)
);
CREATE INDEX IF NOT EXISTS ix_fills_ts ON fills(ts);
CREATE TABLE IF NOT EXISTS equity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    equity     REAL NOT NULL,
    cash       REAL NOT NULL,
    gross      REAL NOT NULL,
    positions  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_equity_ts ON equity(ts);
CREATE TABLE IF NOT EXISTS position_meta (
    symbol         TEXT PRIMARY KEY,
    strategy       TEXT NOT NULL,
    stop_price     REAL,
    entry_ts       TEXT,
    highest_close  REAL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS realized_pnl (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    strategy  TEXT NOT NULL,
    pnl       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_realized_ts ON realized_pnl(ts);
CREATE TABLE IF NOT EXISTS flags (
    name        TEXT PRIMARY KEY,
    value       TEXT,
    reason      TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flag_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL,
    value   TEXT,
    reason  TEXT NOT NULL DEFAULT '',
    ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_flag_history_name ON flag_history(name);
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    level    TEXT NOT NULL,
    kind     TEXT NOT NULL,
    message  TEXT NOT NULL,
    fields   TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS heartbeats (
    component  TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    ts         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS regimes (
    symbol  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    regime  TEXT NOT NULL,
    PRIMARY KEY (symbol, ts)
);
"""


def to_utc(ts: datetime) -> datetime:
    """Tz-aware UTC; naive datetimes are taken to be UTC already."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _ts(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return to_utc(ts).isoformat(timespec="microseconds")


def _parse(text: str | None) -> datetime | None:
    if text is None:
        return None
    return to_utc(datetime.fromisoformat(text))


def _now() -> datetime:
    return datetime.now(UTC)


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._memory = self.path == ":memory:"
        if not self._memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._conns_lock = threading.Lock()
        self._conns: list[sqlite3.Connection] = []
        self._local = threading.local()
        self._shared: sqlite3.Connection | None = None   # only for ":memory:"
        self._closed = False
        self._init_schema()

    # ---------------------------------------------------------------- connection plumbing
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        if not self._memory:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        with self._conns_lock:
            self._conns.append(conn)
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("Store is closed")
        if self._memory:
            if self._shared is None:
                self._shared = self._connect()
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """Serialized write transaction (BEGIN IMMEDIATE ... COMMIT / ROLLBACK)."""
        with self._write_lock:
            conn = self._conn
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def _read(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        if self._memory:   # a single shared connection must not be used concurrently
            with self._write_lock:
                return self._conn.execute(sql, params).fetchall()
        return self._conn.execute(sql, params).fetchall()

    def _init_schema(self) -> None:
        with self._write() as c:
            for stmt in _SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)
            row = c.execute("SELECT MAX(version) FROM schema_version").fetchone()
            if row[0] is None:
                c.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                          (SCHEMA_VERSION, _ts(_now())))
            elif row[0] > SCHEMA_VERSION:
                raise RuntimeError(f"database schema v{row[0]} is newer than this code (v{SCHEMA_VERSION})")

    def schema_version(self) -> int:
        return int(self._read("SELECT MAX(version) FROM schema_version")[0][0])

    def close(self) -> None:
        with self._conns_lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:   # pragma: no cover - best effort
                pass
        self._shared = None
        self._local = threading.local()
        self._closed = True

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- orders
    def record_order(self, order: Order) -> None:
        """Insert or update by client_order_id. ``submitted_at`` and ``created_at`` keep the values
        from the first insert (a later non-null submitted_at only fills in a missing one)."""
        now = _ts(_now())
        with self._write() as c:
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM orders").fetchone()[0]
            c.execute(
                """
                INSERT INTO orders (client_order_id, symbol, side, qty, status, broker_order_id,
                    filled_qty, filled_avg_price, submitted_at, updated_at, strategy, reject_reason,
                    created_at, seq)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    side = excluded.side,
                    qty = excluded.qty,
                    status = excluded.status,
                    broker_order_id = COALESCE(excluded.broker_order_id, orders.broker_order_id),
                    filled_qty = excluded.filled_qty,
                    filled_avg_price = COALESCE(excluded.filled_avg_price, orders.filled_avg_price),
                    submitted_at = COALESCE(orders.submitted_at, excluded.submitted_at),
                    updated_at = excluded.updated_at,
                    strategy = CASE WHEN excluded.strategy != '' THEN excluded.strategy ELSE orders.strategy END,
                    reject_reason = excluded.reject_reason
                """,
                (
                    order.client_order_id, order.symbol, Side(order.side).value, float(order.qty),
                    OrderStatus(order.status).value, order.broker_order_id, float(order.filled_qty),
                    order.filled_avg_price, _ts(order.submitted_at), _ts(order.updated_at) or now,
                    order.strategy or "", order.reject_reason, now, seq,
                ),
            )

    @staticmethod
    def _row_to_order(r: sqlite3.Row) -> Order:
        return Order(
            client_order_id=r["client_order_id"], symbol=r["symbol"], side=Side(r["side"]),
            qty=r["qty"], status=OrderStatus(r["status"]), broker_order_id=r["broker_order_id"],
            filled_qty=r["filled_qty"], filled_avg_price=r["filled_avg_price"],
            submitted_at=_parse(r["submitted_at"]), updated_at=_parse(r["updated_at"]),
            strategy=r["strategy"], reject_reason=r["reject_reason"],
        )

    def get_order(self, client_order_id: str) -> Order | None:
        rows = self._read("SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,))
        return self._row_to_order(rows[0]) if rows else None

    def open_orders(self) -> list[Order]:
        marks = ",".join("?" * len(_OPEN_STATUSES))
        rows = self._read(f"SELECT * FROM orders WHERE status IN ({marks}) ORDER BY seq", _OPEN_STATUSES)
        return [self._row_to_order(r) for r in rows]

    def orders(self, limit: int = 200) -> list[Order]:
        """Newest first (by first-recorded order)."""
        rows = self._read("SELECT * FROM orders ORDER BY seq DESC LIMIT ?", (int(limit),))
        return [self._row_to_order(r) for r in rows]

    # ---------------------------------------------------------------- fills
    def record_fill(self, fill: Fill) -> bool:
        """True if stored, False if this exact fill (order id, ts, qty, price) was already recorded."""
        with self._write() as c:
            cur = c.execute(
                """INSERT OR IGNORE INTO fills (client_order_id, symbol, side, qty, price, fee, ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (fill.client_order_id, fill.symbol, Side(fill.side).value, float(fill.qty),
                 float(fill.price), float(fill.fee), _ts(fill.ts)),
            )
            return cur.rowcount == 1

    def fills(self, since: datetime | None = None) -> list[Fill]:
        """Oldest first; ``since`` is inclusive."""
        if since is None:
            rows = self._read("SELECT * FROM fills ORDER BY ts, id")
        else:
            rows = self._read("SELECT * FROM fills WHERE ts >= ? ORDER BY ts, id", (_ts(since),))
        return [
            Fill(client_order_id=r["client_order_id"], symbol=r["symbol"], side=Side(r["side"]),
                 qty=r["qty"], price=r["price"], fee=r["fee"], ts=_parse(r["ts"]))
            for r in rows
        ]

    # ---------------------------------------------------------------- equity
    def record_equity(self, snapshot: AccountSnapshot) -> None:
        positions = [
            {"symbol": p.symbol, "qty": p.qty, "avg_entry_price": p.avg_entry_price,
             "market_price": p.market_price, "strategy": p.strategy, "stop_price": p.stop_price,
             "market_value": p.market_value, "unrealized_pnl": p.unrealized_pnl}
            for p in snapshot.positions
        ]
        with self._write() as c:
            c.execute(
                "INSERT INTO equity (ts, equity, cash, gross, positions) VALUES (?, ?, ?, ?, ?)",
                (_ts(snapshot.ts), float(snapshot.equity), float(snapshot.cash),
                 float(snapshot.gross_exposure), json.dumps(positions)),
            )

    def equity_curve(self) -> pd.DataFrame:
        """Columns ``ts`` (tz-aware UTC), ``equity``, ``cash``, ``gross``; sorted by ts."""
        rows = self._read("SELECT ts, equity, cash, gross FROM equity ORDER BY ts, id")
        df = pd.DataFrame([dict(r) for r in rows], columns=["ts", "equity", "cash", "gross"])
        df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
        for col in ("equity", "cash", "gross"):
            df[col] = df[col].astype(float)
        return df.reset_index(drop=True)

    def latest_equity_positions(self) -> list[dict]:
        """Positions JSON of the most recent equity snapshot (empty list if none)."""
        rows = self._read("SELECT positions FROM equity ORDER BY ts DESC, id DESC LIMIT 1")
        return json.loads(rows[0]["positions"]) if rows else []

    def equity_at_or_before(self, ts: datetime) -> float | None:
        """Equity of the last snapshot with timestamp <= ts, or None."""
        rows = self._read("SELECT equity FROM equity WHERE ts <= ? ORDER BY ts DESC, id DESC LIMIT 1",
                          (_ts(ts),))
        return float(rows[0]["equity"]) if rows else None

    def first_equity_between(self, start: datetime, end: datetime) -> float | None:
        """Equity of the first snapshot with start <= ts < end, or None."""
        rows = self._read("SELECT equity FROM equity WHERE ts >= ? AND ts < ? ORDER BY ts, id LIMIT 1",
                          (_ts(start), _ts(end)))
        return float(rows[0]["equity"]) if rows else None

    def peak_equity(self, since: datetime | None = None) -> float | None:
        """Max recorded equity (optionally only snapshots at or after ``since``), or None."""
        if since is None:
            rows = self._read("SELECT MAX(equity) FROM equity")
        else:
            rows = self._read("SELECT MAX(equity) FROM equity WHERE ts >= ?", (_ts(since),))
        v = rows[0][0]
        return None if v is None else float(v)

    # ---------------------------------------------------------------- position metadata
    def set_position_meta(self, symbol: str, strategy: str, stop_price: float | None,
                          entry_ts: datetime | None, highest_close: float | None) -> None:
        with self._write() as c:
            c.execute(
                """INSERT INTO position_meta (symbol, strategy, stop_price, entry_ts, highest_close, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol) DO UPDATE SET strategy = excluded.strategy,
                       stop_price = excluded.stop_price, entry_ts = excluded.entry_ts,
                       highest_close = excluded.highest_close, updated_at = excluded.updated_at""",
                (symbol, strategy, stop_price, _ts(entry_ts), highest_close, _ts(_now())),
            )

    @staticmethod
    def _meta(r: sqlite3.Row) -> dict:
        return {"symbol": r["symbol"], "strategy": r["strategy"], "stop_price": r["stop_price"],
                "entry_ts": _parse(r["entry_ts"]), "highest_close": r["highest_close"],
                "updated_at": _parse(r["updated_at"])}

    def get_position_meta(self, symbol: str) -> dict | None:
        rows = self._read("SELECT * FROM position_meta WHERE symbol = ?", (symbol,))
        return self._meta(rows[0]) if rows else None

    def clear_position_meta(self, symbol: str) -> None:
        with self._write() as c:
            c.execute("DELETE FROM position_meta WHERE symbol = ?", (symbol,))

    def all_position_meta(self) -> dict[str, dict]:
        return {r["symbol"]: self._meta(r) for r in self._read("SELECT * FROM position_meta ORDER BY symbol")}

    # ---------------------------------------------------------------- realized P&L
    def record_realized_pnl(self, ts: datetime, symbol: str, strategy: str, pnl: float) -> None:
        with self._write() as c:
            c.execute("INSERT INTO realized_pnl (ts, symbol, strategy, pnl) VALUES (?, ?, ?, ?)",
                      (_ts(ts), symbol, strategy or "", float(pnl)))

    def realized_pnl_between(self, start: datetime, end: datetime) -> float:
        """Sum of realized P&L with start <= ts < end."""
        rows = self._read("SELECT COALESCE(SUM(pnl), 0.0) FROM realized_pnl WHERE ts >= ? AND ts < ?",
                          (_ts(start), _ts(end)))
        return float(rows[0][0])

    def realized_pnl_by_strategy(self) -> dict[str, float]:
        rows = self._read("SELECT strategy, SUM(pnl) AS pnl FROM realized_pnl GROUP BY strategy ORDER BY strategy")
        return {r["strategy"]: float(r["pnl"]) for r in rows}

    def realized_pnl_records(self, limit: int = 500) -> list[dict]:
        """Newest first: ts, symbol, strategy, pnl."""
        rows = self._read("SELECT ts, symbol, strategy, pnl FROM realized_pnl ORDER BY ts DESC, id DESC LIMIT ?",
                          (int(limit),))
        return [{"ts": _parse(r["ts"]), "symbol": r["symbol"], "strategy": r["strategy"], "pnl": r["pnl"]}
                for r in rows]

    # ---------------------------------------------------------------- flags
    def set_flag(self, name: str, value: str | None, reason: str = "") -> None:
        """Set (or, with ``value=None``, clear) a named flag. Every change is kept in flag_history."""
        now = _ts(_now())
        with self._write() as c:
            c.execute(
                """INSERT INTO flags (name, value, reason, updated_at) VALUES (?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET value = excluded.value, reason = excluded.reason,
                       updated_at = excluded.updated_at""",
                (name, value, reason, now),
            )
            c.execute("INSERT INTO flag_history (name, value, reason, ts) VALUES (?, ?, ?, ?)",
                      (name, value, reason, now))

    def get_flag(self, name: str) -> str | None:
        rows = self._read("SELECT value FROM flags WHERE name = ?", (name,))
        return rows[0]["value"] if rows else None

    def get_flag_info(self, name: str) -> dict | None:
        """``{"value", "reason", "updated_at"}`` for a set flag, None when unset or cleared."""
        rows = self._read("SELECT value, reason, updated_at FROM flags WHERE name = ?", (name,))
        if not rows or rows[0]["value"] is None:
            return None
        r = rows[0]
        return {"value": r["value"], "reason": r["reason"], "updated_at": _parse(r["updated_at"])}

    def flag_history(self, name: str) -> list[dict]:
        """Oldest first: ``{"ts", "value", "reason"}``."""
        rows = self._read("SELECT ts, value, reason FROM flag_history WHERE name = ? ORDER BY id", (name,))
        return [{"ts": _parse(r["ts"]), "value": r["value"], "reason": r["reason"]} for r in rows]

    # ---------------------------------------------------------------- events
    def record_event(self, level: str, kind: str, message: str, **fields: Any) -> None:
        with self._write() as c:
            c.execute("INSERT INTO events (ts, level, kind, message, fields) VALUES (?, ?, ?, ?, ?)",
                      (_ts(_now()), level, kind, message, json.dumps(fields, default=str)))

    def events(self, limit: int = 100) -> list[dict]:
        """Newest first: ``{"id", "ts", "level", "kind", "message", "fields"}``."""
        rows = self._read("SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),))
        return [{"id": r["id"], "ts": _parse(r["ts"]), "level": r["level"], "kind": r["kind"],
                 "message": r["message"], "fields": json.loads(r["fields"])} for r in rows]

    # ---------------------------------------------------------------- heartbeats
    def heartbeat(self, component: str, status: str, detail: str = "") -> None:
        with self._write() as c:
            c.execute(
                """INSERT INTO heartbeats (component, status, detail, ts) VALUES (?, ?, ?, ?)
                   ON CONFLICT(component) DO UPDATE SET status = excluded.status,
                       detail = excluded.detail, ts = excluded.ts""",
                (component, status, detail, _ts(_now())),
            )

    def heartbeats(self) -> list[dict]:
        rows = self._read("SELECT component, status, detail, ts FROM heartbeats ORDER BY component")
        return [{"component": r["component"], "status": r["status"], "detail": r["detail"],
                 "ts": _parse(r["ts"])} for r in rows]

    # ---------------------------------------------------------------- regimes
    def record_regime(self, symbol: str, ts: datetime, regime: str) -> None:
        with self._write() as c:
            c.execute("INSERT OR REPLACE INTO regimes (symbol, ts, regime) VALUES (?, ?, ?)",
                      (symbol, _ts(ts), regime))

    def latest_regimes(self) -> dict[str, tuple[datetime, str]]:
        rows = self._read(
            """SELECT r.symbol, r.ts, r.regime FROM regimes r
               JOIN (SELECT symbol, MAX(ts) AS ts FROM regimes GROUP BY symbol) m
                 ON r.symbol = m.symbol AND r.ts = m.ts ORDER BY r.symbol"""
        )
        return {r["symbol"]: (_parse(r["ts"]), r["regime"]) for r in rows}
