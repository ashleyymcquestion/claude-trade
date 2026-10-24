import pytest

from trader import memebacktest as m


def _bars(closes, vols=None, start=1):
    vols = vols or [100.0] * len(closes)
    return [{"date": f"2026-{1 + (i + start) // 28:02d}-{1 + (i + start) % 28:02d}", "close": c, "volume": v}
            for i, (c, v) in enumerate(zip(closes, vols))]


@pytest.fixture
def paper(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "PAPER", tmp_path)
    return tmp_path


def test_signals_pick_the_biggest_riser_and_breakouts():
    flat = [1.0] * 40
    jump = _bars(flat + [1.3], [100.0] * 40 + [500.0])        # +30% on 5x volume: chase and breakout
    small = _bars(flat + [1.21])                               # +21%, smaller riser, normal volume
    day = jump[-1]["date"]
    sig = m._signals({"A": jump, "B": small}, day)
    assert {"rule": "chase", "coin": "A", "gain": 0.3} in sig
    assert not any(s["rule"] == "chase" and s["coin"] == "B" for s in sig)
    assert any(s["rule"] == "breakout" and s["coin"] == "A" for s in sig)


def test_paper_opens_then_closes_after_hold_and_runs_once_a_day(paper):
    closes = [1.0] * 40 + [1.3] + [1.5] * 10
    bars = _bars(closes)
    day = bars[40]["date"]
    first = m.paper_step(day, {"A": bars})
    assert first["opened"] and first["open"] == 1
    assert m.paper_step(day, {"A": bars})["already_ran"] is True
    later = m.paper_step(bars[40 + m.PAPER_HOLD]["date"], {"A": bars})
    assert later["closed"] and later["scoreboard"]["trades"] == 1
    assert later["scoreboard"]["avg_trade"] == pytest.approx(1.5 * 0.99 / (1.3 * 1.01) - 1, abs=1e-3)
