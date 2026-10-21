from datetime import datetime

import pytest

from trader import watchlab as w

DAY1 = "2026-10-21T15:00:00Z"   # Wed 11:00 ET
DAY2 = "2026-10-22T15:00:00Z"


def inp(now=DAY1, **kw):
    return {"now": now, "gainers": [], "buzz": [], "politicians": [], "social": [], "quotes": {"QQQ": 100.0}, **kw}


def pol(sym, ttype="BUY", disclosed="2026-10-20", asset="EQUITY"):
    return {"politician_name": "Rep. X", "symbol": sym, "asset_type": asset, "transaction_type": ttype,
            "transaction_date": "2026-10-01", "disclosure_date": disclosed}


def test_due_once_per_day_after_ten_ny():
    st = {"last_run": ""}
    assert not w.due(st, datetime.fromisoformat("2026-10-21T13:40:00+00:00"))   # 9:40 ET
    assert w.due(st, datetime.fromisoformat(DAY1.replace("Z", "+00:00")))
    assert not w.due({"last_run": "2026-10-21"}, datetime.fromisoformat(DAY1.replace("Z", "+00:00")))


def test_opens_top_gainers_sorted_and_capped():
    gainers = [{"symbol": f"S{i}", "price": 10.0, "pct": i / 100} for i in range(8)]
    trades, state, rep = w.run(inp(gainers=gainers), [], w.load_state(w.DIR / "nope.json"))
    assert [t.symbol for t in trades] == ["S7", "S6", "S5", "S4", "S3"]
    assert state["last_run"] == "2026-10-21"


def test_politicians_only_fresh_unseen_buys():
    raw = [pol("AAA"), pol("BBB", ttype="SELL"), pol("CCC", disclosed="2026-08-01"), pol("DDD", asset="OPTION")]
    st = {"last_run": "", "seen_politician_trades": []}
    assert w.needs(inp(politicians=raw), [], st) == ["AAA", "DDD", "QQQ"]
    trades, st, _ = w.run(inp(politicians=raw, quotes={"QQQ": 100, "AAA": 5, "DDD": 7}), [], st)
    assert {t.symbol for t in trades} == {"AAA", "DDD"}
    trades2, _, rep = w.run(inp(DAY2, politicians=raw, quotes={"QQQ": 100, "AAA": 5, "DDD": 7}), trades, st)
    assert rep["opened"] == []    # already seen


@pytest.mark.parametrize("px,why", [(91.0, "stop"), (116.0, "target")])
def test_stop_and_target(px, why):
    trades, st, _ = w.run(inp(gainers=[{"symbol": "AAA", "price": 100.0, "pct": 0.1}]), [], {"last_run": ""})
    trades, _, rep = w.run(inp(DAY2, quotes={"QQQ": 101.0, "AAA": px}), trades, st)
    [c] = rep["closed"]
    assert c["why"] == why and trades[0].excess == pytest.approx(px / 100 - 1 - 0.01)


def test_time_exit_and_scoreboard():
    trades, st, _ = w.run(inp(gainers=[{"symbol": "AAA", "price": 100.0, "pct": 0.1}]), [], {"last_run": ""})
    for d in range(22, 29):
        if d in (24, 25):   # weekend: no run
            continue
        trades, st, rep = w.run(inp(f"2026-10-{d}T15:00:00Z", quotes={"QQQ": 100.0, "AAA": 102.0}), trades, st)
    assert trades[0].exit_reason == "time" and trades[0].days == 5
    sb = w.scoreboard(trades)["gainers"]
    assert sb["closed"] == 1 and sb["win_rate"] == 1.0 and not sb["ready_for_real_money"]


def test_save_load_roundtrip(tmp_path):
    trades, _, _ = w.run(inp(gainers=[{"symbol": "AAA", "price": 100.0, "pct": 0.1}]), [], {"last_run": ""})
    w.save_trades(trades, tmp_path / "t.csv")
    assert w.load_trades(tmp_path / "t.csv") == trades
