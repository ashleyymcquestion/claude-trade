import numpy as np
import pandas as pd
import pytest

from quantbot import indicators as ind


def s(values):
    return pd.Series(values, dtype=float, index=pd.date_range("2024-01-01", periods=len(values), tz="UTC"))


def test_sma_hand_checked():
    out = ind.sma(s([1, 2, 3, 4, 5]), 3)
    assert out.isna().tolist() == [True, True, False, False, False]
    assert out.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_ema_matches_pandas_recursive_form():
    x = s([10, 11, 12, 11, 13, 15, 14, 16])
    out = ind.ema(x, 3, min_periods=1)
    exp = x.ewm(span=3, adjust=False).mean()
    pd.testing.assert_series_equal(out, exp)
    # hand check: alpha = 0.5 -> 10, 10.5, 11.25
    assert out.iloc[:3].tolist() == [10.0, 10.5, 11.25]
    # default min_periods = n masks warm-up
    assert ind.ema(x, 3).isna().sum() == 2


def test_wilder_seeded_with_sma():
    out = ind.wilder(s([1, 2, 3, 4, 10]), 3)
    assert out.isna().tolist() == [True, True, False, False, False]
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[3] == pytest.approx(2.0 + (4 - 2.0) / 3)
    assert out.iloc[4] == pytest.approx(out.iloc[3] + (10 - out.iloc[3]) / 3)


def test_atr_known_example():
    high = s([10, 12, 11, 15])
    low = s([8, 9, 9, 12])
    close = s([9, 11, 10, 14])
    # TR: 2 ; max(3, |12-9|, |9-9|)=3 ; max(2, 0, 2)=2 ; max(3, 5, 2)=5
    tr = ind.true_range(high, low, close)
    assert tr.tolist() == [2.0, 3.0, 2.0, 5.0]
    a = ind.atr(high, low, close, 3)
    assert np.isnan(a.iloc[1])
    assert a.iloc[2] == pytest.approx(7 / 3)
    assert a.iloc[3] == pytest.approx(7 / 3 + (5 - 7 / 3) / 3)


def test_rsi_bounds_and_extremes():
    rng = np.random.default_rng(1)
    x = s(100 + np.cumsum(rng.normal(0, 1, 500)))
    r = ind.rsi(x, 14).dropna()
    assert len(r) > 400 and (r >= 0).all() and (r <= 100).all()
    assert ind.rsi(s(np.arange(1, 30)), 14).dropna().eq(100).all()
    assert ind.rsi(s(np.arange(30, 1, -1)), 14).dropna().eq(0).all()
    assert ind.rsi(s([5.0] * 20), 14).dropna().eq(50).all()


def test_rsi_hand_checked():
    # deltas: +1, -1, +2 ; n=2 seed: gain avg (1+0)/2=0.5, loss avg (0+1)/2=0.5 -> RSI 50
    r = ind.rsi(s([10, 11, 10, 12]), 2)
    assert np.isnan(r.iloc[1])
    assert r.iloc[2] == pytest.approx(50.0)
    # next: gain 0.5+(2-0.5)/2=1.25, loss 0.5+(0-0.5)/2=0.25 -> rs=5 -> 100-100/6
    assert r.iloc[3] == pytest.approx(100 - 100 / 6)


def test_bollinger_and_zscore():
    x = s([1, 2, 3, 4, 5])
    mid, up, lo = ind.bollinger(x, 3, 2.0)
    sd = np.std([3, 4, 5])
    assert mid.iloc[-1] == 4.0
    assert up.iloc[-1] == pytest.approx(4 + 2 * sd)
    assert lo.iloc[-1] == pytest.approx(4 - 2 * sd)
    z = ind.zscore(x, 3)
    assert z.iloc[-1] == pytest.approx((5 - 4) / sd)
    assert ind.zscore(s([2, 2, 2, 2]), 3).isna().all()  # zero std -> NaN, not inf


def test_donchian_excludes_current_bar():
    high = s([1, 2, 3, 4, 100])
    low = s([0.5, 1, 2, 3, 0.1])
    up, lo = ind.donchian(high, low, 3)
    assert up.isna().tolist()[:3] == [True, True, True]
    assert up.iloc[3] == 3.0       # max of bars 0..2
    assert up.iloc[4] == 4.0       # the 100 on the current bar is NOT included
    assert lo.iloc[4] == 1.0       # the 0.1 on the current bar is NOT included


def test_relative_volume_uses_prior_bars_only():
    v = s([10, 10, 10, 40])
    rv = ind.relative_volume(v, 3)
    assert rv.iloc[:3].isna().all()
    assert rv.iloc[3] == pytest.approx(4.0)


def test_momentum():
    m = ind.momentum(s([100, 110, 121]), 2)
    assert m.iloc[2] == pytest.approx(0.21)
    assert m.iloc[:2].isna().all()


@pytest.mark.parametrize("fn", [
    lambda x: ind.sma(x, 5), lambda x: ind.ema(x, 5), lambda x: ind.rsi(x, 5),
    lambda x: ind.bollinger(x, 5)[2], lambda x: ind.zscore(x, 5), lambda x: ind.momentum(x, 5),
    lambda x: ind.relative_volume(x, 5), lambda x: ind.atr(x * 1.01, x * 0.99, x, 5),
    lambda x: ind.donchian(x * 1.01, x * 0.99, 5)[0],
])
def test_indicators_are_truncation_invariant(fn):
    rng = np.random.default_rng(7)
    x = s(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 200))))
    full = fn(x)
    for t in range(1, 200, 13):
        pd.testing.assert_series_equal(fn(x.iloc[:t]), full.iloc[:t])
