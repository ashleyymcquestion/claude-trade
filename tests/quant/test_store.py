import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pandas as pd

from quantbot.models import AccountSnapshot, Fill, Order, OrderStatus, Position, Side
from quantbot.store import SCHEMA_VERSION, Store

T0 = datetime(2026, 10, 15, 14, 30, tzinfo=UTC)


def _order(cid="trend-NVDA-2026-10-15-buy", status=OrderStatus.NEW, **kw):
    base = dict(client_order_id=cid, symbol="NVDA", side=Side.BUY, qty=2.0, status=status,
                submitted_at=T0, strategy="trend")
    base.update(kw)
    return Order(**base)


def test_creates_parent_dir_wal_and_schema_version(tmp_path):
    path = tmp_path / "nested" / "dir" / "q.sqlite3"
    s = Store(path)
    assert path.exists()
    assert s.schema_version() == SCHEMA_VERSION
    mode = sqlite3.connect(path).execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    s.close()
    s2 = Store(path)   # idempotent schema creation
    assert s2.schema_version() == SCHEMA_VERSION
    s2.close()


def test_order_upsert_keeps_one_row_and_first_submitted_at(tmp_path):
    s = Store(tmp_path / "q.db")
    s.record_order(_order())
    later = T0 + timedelta(minutes=5)
    s.record_order(_order(status=OrderStatus.FILLED, filled_qty=2.0, filled_avg_price=101.5,
                          broker_order_id="b-1", submitted_at=later, updated_at=later))
    assert len(s.orders()) == 1
    o = s.get_order("trend-NVDA-2026-10-15-buy")
    assert o.status is OrderStatus.FILLED and o.side is Side.BUY
    assert o.filled_qty == 2.0 and o.filled_avg_price == 101.5 and o.broker_order_id == "b-1"
    assert o.submitted_at == T0 and o.submitted_at.tzinfo is not None
    assert o.updated_at == later
    assert s.get_order("missing") is None


def test_open_orders_and_newest_first(tmp_path):
    s = Store(tmp_path / "q.db")
    s.record_order(_order("a", OrderStatus.NEW))
    s.record_order(_order("b", OrderStatus.PARTIALLY_FILLED))
    s.record_order(_order("c", OrderStatus.PENDING))
    s.record_order(_order("d", OrderStatus.FILLED))
    s.record_order(_order("e", OrderStatus.CANCELED))
    assert [o.client_order_id for o in s.open_orders()] == ["a", "b", "c"]
    assert [o.client_order_id for o in s.orders()] == ["e", "d", "c", "b", "a"]
    assert [o.client_order_id for o in s.orders(limit=2)] == ["e", "d"]


def test_fills_duplicate_rejected_and_since(tmp_path):
    s = Store(tmp_path / "q.db")
    f1 = Fill("a", "NVDA", Side.BUY, 1.0, 100.0, 0.05, T0)
    f2 = Fill("a", "NVDA", Side.BUY, 1.0, 100.5, 0.05, T0 + timedelta(seconds=1))
    assert s.record_fill(f1) is True
    assert s.record_fill(f1) is False
    assert s.record_fill(f2) is True
    assert s.fills() == [f1, f2]
    assert s.fills(since=T0 + timedelta(seconds=1)) == [f2]
    assert s.fills()[0].ts.tzinfo is not None


def test_equity_curve(tmp_path):
    s = Store(tmp_path / "q.db")
    pos = (Position("NVDA", 2.0, 100.0, 110.0, "trend", 95.0),)
    s.record_equity(AccountSnapshot(T0 + timedelta(days=1), 1020.0, 800.0, pos))
    s.record_equity(AccountSnapshot(T0, 1000.0, 1000.0))
    df = s.equity_curve()
    assert list(df.columns) == ["ts", "equity", "cash", "gross"]
    assert list(df["equity"]) == [1000.0, 1020.0]
    assert list(df["gross"]) == [0.0, 220.0]
    assert str(df["ts"].dt.tz) == "UTC"
    assert df["ts"].iloc[0] == pd.Timestamp(T0)
    assert s.latest_equity_positions()[0]["symbol"] == "NVDA"
    assert s.peak_equity() == 1020.0
    assert s.equity_at_or_before(T0 + timedelta(hours=1)) == 1000.0
    assert Store(tmp_path / "empty.db").equity_curve().empty


def test_position_meta_roundtrip(tmp_path):
    s = Store(tmp_path / "q.db")
    s.set_position_meta("NVDA", "trend", 95.0, T0, 110.0)
    s.set_position_meta("BTC/USD", "breakout", 60000.0, T0, 65000.0)
    m = s.get_position_meta("NVDA")
    assert m["strategy"] == "trend" and m["stop_price"] == 95.0
    assert m["entry_ts"] == T0 and m["highest_close"] == 110.0
    s.set_position_meta("NVDA", "trend", 99.0, T0, 115.0)
    assert s.get_position_meta("NVDA")["stop_price"] == 99.0
    assert set(s.all_position_meta()) == {"NVDA", "BTC/USD"}
    s.clear_position_meta("NVDA")
    assert s.get_position_meta("NVDA") is None
    assert set(s.all_position_meta()) == {"BTC/USD"}


def test_realized_pnl_between_and_by_strategy(tmp_path):
    s = Store(tmp_path / "q.db")
    s.record_realized_pnl(T0, "NVDA", "trend", -5.0)
    s.record_realized_pnl(T0 + timedelta(hours=1), "AMD", "breakout", 3.0)
    s.record_realized_pnl(T0 + timedelta(hours=2), "QQQ", "trend", -1.0)
    assert s.realized_pnl_between(T0, T0 + timedelta(hours=2)) == -2.0   # end exclusive
    assert s.realized_pnl_between(T0 + timedelta(seconds=1), T0 + timedelta(hours=3)) == 2.0  # start inclusive
    assert s.realized_pnl_between(T0 - timedelta(days=1), T0) == 0.0
    assert s.realized_pnl_by_strategy() == {"breakout": 3.0, "trend": -6.0}


def test_flags_and_history(tmp_path):
    s = Store(tmp_path / "q.db")
    assert s.get_flag("x") is None
    s.set_flag("x", "1", "first")
    assert s.get_flag("x") == "1"
    s.set_flag("x", None, "cleared")
    assert s.get_flag("x") is None
    hist = s.flag_history("x")
    assert [(h["value"], h["reason"]) for h in hist] == [("1", "first"), (None, "cleared")]


def test_events_heartbeats_regimes(tmp_path):
    s = Store(tmp_path / "q.db")
    s.record_event("info", "a", "first", n=1)
    s.record_event("critical", "b", "second", when=T0)
    ev = s.events()
    assert [e["kind"] for e in ev] == ["b", "a"]
    assert ev[1]["fields"] == {"n": 1} and ev[0]["level"] == "critical"
    assert len(s.events(limit=1)) == 1

    s.heartbeat("runner", "ok")
    s.heartbeat("runner", "error", "boom")
    s.heartbeat("scheduler", "ok")
    hb = s.heartbeats()
    assert [h["component"] for h in hb] == ["runner", "scheduler"]
    assert hb[0]["status"] == "error" and hb[0]["detail"] == "boom" and hb[0]["ts"].tzinfo is not None

    s.record_regime("NVDA", T0, "bull_quiet")
    s.record_regime("NVDA", T0 + timedelta(days=1), "bear_volatile")
    s.record_regime("QQQ", T0, "bull_volatile")
    r = s.latest_regimes()
    assert r == {"NVDA": (T0 + timedelta(days=1), "bear_volatile"), "QQQ": (T0, "bull_volatile")}


def test_data_survives_close_and_reopen(tmp_path):
    path = tmp_path / "q.db"
    s = Store(path)
    s.record_order(_order())
    s.record_fill(Fill("a", "NVDA", Side.BUY, 1.0, 100.0, 0.0, T0))
    s.record_equity(AccountSnapshot(T0, 1000.0, 1000.0))
    s.set_position_meta("NVDA", "trend", 95.0, T0, 100.0)
    s.record_realized_pnl(T0, "NVDA", "trend", 4.0)
    s.set_flag("kill_switch", "on")
    s.record_event("info", "k", "m")
    s.heartbeat("runner", "ok")
    s.record_regime("NVDA", T0, "bull_quiet")
    s.close()

    s2 = Store(path)
    assert s2.get_order("trend-NVDA-2026-10-15-buy") is not None
    assert len(s2.fills()) == 1 and len(s2.equity_curve()) == 1
    assert s2.get_position_meta("NVDA")["stop_price"] == 95.0
    assert s2.realized_pnl_between(T0, T0 + timedelta(seconds=1)) == 4.0
    assert s2.get_flag("kill_switch") == "on"
    assert len(s2.events()) == 1 and len(s2.heartbeats()) == 1
    assert "NVDA" in s2.latest_regimes()
    s2.close()


def test_concurrent_writes_from_threads(tmp_path):
    s = Store(tmp_path / "q.db")
    n = 150
    errors: list[BaseException] = []

    def work(tag: str) -> None:
        try:
            for i in range(n):
                s.record_fill(Fill(f"{tag}-{i}", "NVDA", Side.BUY, 1.0, 100.0 + i, 0.0, T0 + timedelta(seconds=i)))
                s.record_event("info", tag, f"{tag} {i}")
                s.record_order(_order(f"{tag}-{i}"))
                s.heartbeat(tag, "ok", str(i))
                s.fills()  # interleaved reads
        except BaseException as e:  # pragma: no cover - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=work, args=(t,)) for t in ("t1", "t2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(s.fills()) == 2 * n
    assert len(s.events(limit=10_000)) == 2 * n
    assert len(s.orders(limit=10_000)) == 2 * n
    assert sqlite3.connect(tmp_path / "q.db").execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    s.close()


def test_two_store_instances_same_file(tmp_path):
    a, b = Store(tmp_path / "q.db"), Store(tmp_path / "q.db")
    a.set_flag("x", "1")
    assert b.get_flag("x") == "1"
    a.close()
    b.close()
