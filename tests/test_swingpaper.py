from datetime import datetime, timedelta, timezone

from trader import swingpaper

T0 = datetime(2026, 10, 26, 14, 0, tzinfo=timezone(timedelta(hours=-7)))


def test_starts_in_cash_and_tracks_hold():
    _, row = swingpaper.step(None, 0.050, 0.051, T0)
    assert row["position"] == "cash" and row["value"] == 2.0
    assert abs(row["hold"] - 2.0 * 0.050 / 0.051) < 1e-3


def test_buys_the_dip_at_the_ask_and_sells_the_rise_at_the_bid():
    st, _ = swingpaper.step(None, 0.050, 0.051, T0)
    st, row = swingpaper.step(st, 0.046, 0.047, T0)          # 8% off the high: wait
    assert row["action"] == ""
    st, row = swingpaper.step(st, 0.045, 0.046, T0)          # 10% off: buy at 0.046
    assert row["action"] == "buy" and st["entry"] == 0.046
    st, row = swingpaper.step(st, 0.050, 0.051, T0)          # +8.7%: hold
    assert row["action"] == ""
    st, row = swingpaper.step(st, 0.0507, 0.0518, T0)        # +10.2%: sell at the bid
    assert row["action"] == "sell" and row["position"] == "cash"
    assert abs(row["value"] - 2.0 / 0.046 * 0.0507) < 1e-3
    assert st["high"] == 0.0507


def test_closes_after_the_test_length():
    st, _ = swingpaper.step(None, 0.050, 0.051, T0)
    st, row = swingpaper.step(st, 0.050, 0.051, T0 + timedelta(days=14))
    assert "closed" in row and st["closed"]["value"] == 2.0
    st2, row = swingpaper.step(st, 0.040, 0.041, T0 + timedelta(days=15))
    assert row["action"] == "closed" and st2["units"] == 0


def test_trail_holds_the_climb_and_sells_25_pct_off_the_high():
    st, row = swingpaper.step_trail(None, 0.050, 0.051, T0)
    assert row["position"] == "coin" and row["value"] == row["hold"]
    st, row = swingpaper.step_trail(st, 0.080, 0.081, T0)            # climbs: new high
    assert row["action"] == "" and st["high"] == 0.080
    st, row = swingpaper.step_trail(st, 0.061, 0.062, T0)            # 23.75% off: hold
    assert row["action"] == ""
    st, row = swingpaper.step_trail(st, 0.059, 0.060, T0)            # >25% off: sell at the bid
    assert row["action"] == "sell" and row["position"] == "cash"
    assert abs(row["value"] - 2.0 / 0.051 * 0.059) < 1e-3
    st, row = swingpaper.step_trail(st, 0.100, 0.101, T0)            # stays in cash after the exit
    assert row["position"] == "cash" and row["action"] == ""


def test_a_closed_round_is_kept_and_the_next_round_starts(tmp_path):
    swingpaper.run("X", 0.050, 0.051, T0, d=tmp_path)
    row = swingpaper.run("X", 0.050, 0.051, T0 + timedelta(days=3), d=tmp_path)
    assert "closed" in row
    row = swingpaper.run("X", 0.060, 0.061, T0 + timedelta(days=3, hours=1), d=tmp_path)
    assert row["value"] == 2.0 and "closed" not in row
    import json
    (r,) = json.loads((tmp_path / "X-rounds.json").read_text())
    assert r["value"] == 2.0
