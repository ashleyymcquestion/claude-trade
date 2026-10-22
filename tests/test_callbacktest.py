from datetime import date, timedelta

import pytest

from trader import callbacktest as c


def bars(closes, start=date(2024, 1, 1)):
    return [(start + timedelta(days=i), x) for i, x in enumerate(closes)]


def test_black_scholes_known_value():
    # spot 100, strike 100, 1 year, 20% vol, 4% rate: about 9.93
    assert c.bs_call(100, 100, 1.0, 0.20, 0.04) == pytest.approx(9.925, abs=0.01)
    assert c.bs_call(110, 100, 0, 0.2) == 10


def test_momentum_signal():
    rising = [100 * 1.004 ** i for i in range(80)]
    assert c.signal("momentum", rising)
    assert not c.signal("momentum", rising[::-1])
    assert not c.signal("momentum", rising[:40])


def test_rally_hits_target_and_selloff_hits_stop():
    wiggle = [100 * (1.01 if i % 2 else 0.995) ** 1 * 1.004 ** i for i in range(80)]
    up = c.run(bars(wiggle + [wiggle[-1] * 1.03 ** k for k in range(1, 15)]), "momentum", half_spread=0)
    assert up and up[-1]["exit"] == "target" and up[-1]["ret"] >= c.TARGET
    down = c.run(bars(wiggle + [wiggle[-1] * 0.97 ** k for k in range(1, 15)]), "momentum", half_spread=0)
    assert down and down[-1]["exit"] == "stop" and down[-1]["ret"] <= c.STOP


def test_one_position_at_a_time_and_summary(tmp_path):
    closes = [100 * (1.01 if i % 2 else 0.995) * 1.004 ** i for i in range(300)]
    ts = c.run(bars(closes), "momentum")
    assert all(a["closed"] < b["opened"] for a, b in zip(ts, ts[1:]))
    s = c.summarize(ts)
    assert s["trades"] == len(ts) and 0 <= s["win_rate"] <= 1
    assert c.summarize([]) == {"trades": 0}
