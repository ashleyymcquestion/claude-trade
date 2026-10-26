from datetime import datetime, timedelta

import pytest

from trader import coinwatch


@pytest.fixture(autouse=True)
def tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(coinwatch, "DIR", tmp_path)


def test_buys_at_ask_marks_at_bid_and_closes_after_48_hours():
    t0 = datetime(2026, 10, 24, 17, 0)
    first = coinwatch.step("CHIP", 0.045, 0.046, t0)
    assert first["value"] == pytest.approx(2.0 * 0.045 / 0.046, abs=1e-4)
    mid = coinwatch.step("CHIP", 0.050, 0.051, t0 + timedelta(hours=24))
    assert mid["value"] == pytest.approx(2.0 / 0.046 * 0.050, abs=1e-4) and "closed" not in mid
    end = coinwatch.step("CHIP", 0.040, 0.041, t0 + timedelta(hours=48))
    assert end["closed"] is True and end["ret"] == pytest.approx(0.040 / 0.046 - 1, abs=1e-4)
    after = coinwatch.step("CHIP", 0.06, 0.061, t0 + timedelta(hours=49))
    assert after["done"] is True and after["final_value"] == end["value"]


def test_rejects_a_bad_quote():
    with pytest.raises(ValueError):
        coinwatch.step("CHIP", 0.0, 0.05, datetime(2026, 10, 24))


def test_hours_sets_the_length_on_the_first_call_only():
    t0 = datetime(2026, 10, 25, 20, 0)
    coinwatch.step("USAR", 9.24, 9.26, t0, hours=72)
    assert "closed" not in coinwatch.step("USAR", 9.5, 9.52, t0 + timedelta(hours=60), hours=1)
    assert coinwatch.step("USAR", 9.5, 9.52, t0 + timedelta(hours=72))["closed"] is True
