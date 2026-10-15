"""Data source and validation tests. No network; synthetic frames only, written to tmp_path."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from quantbot.config import DataConfig, SymbolConfig
from quantbot.data.sources import CsvBarSource, alpaca_bars_to_frame, csv_name, write_bars_csv
from quantbot.data.validate import completed_bars, validate_bars
from quantbot.models import AssetClass, BAR_COLUMNS

CFG = DataConfig()


def make_bars(idx: pd.DatetimeIndex, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx))))
    return pd.DataFrame({"open": close * 0.999, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": rng.integers(1_000, 5_000, len(idx)).astype(float)},
                        index=idx)


def stock_days(start="2024-01-01", periods=60):
    return pd.bdate_range(start, periods=periods, tz="UTC") + pd.Timedelta(hours=5)


# --- CSV source -----------------------------------------------------------------------------------

def test_csv_name_uses_data_symbol_and_sanitizes():
    assert csv_name(SymbolConfig(symbol="BTC/USD", asset_class="crypto")) == "BTC-USD.csv"
    assert csv_name(SymbolConfig(symbol="BTC/USD", asset_class="crypto", data_symbol="IBIT")) == "IBIT.csv"


def test_csv_round_trip(tmp_path):
    bars = make_bars(stock_days())
    sym = SymbolConfig(symbol="QQQ", asset_class="stock")
    path = write_bars_csv(bars, tmp_path / csv_name(sym), source="test-synthetic")
    text = path.read_text().splitlines()
    assert text[0] == "timestamp,open,high,low,close,volume,source"
    assert text[1].endswith(",test-synthetic")

    got = CsvBarSource(tmp_path).get_bars(sym, datetime(2000, 1, 1, tzinfo=timezone.utc))
    assert list(got.columns) == list(BAR_COLUMNS)
    assert str(got.index.tz) == "UTC"
    pd.testing.assert_frame_equal(got, bars, check_names=False, check_freq=False)

    start, end = bars.index[10], bars.index[20]
    sub = CsvBarSource(tmp_path).get_bars(sym, start.to_pydatetime(), end.to_pydatetime())
    assert sub.index[0] == start and sub.index[-1] == end and len(sub) == 11


def test_csv_sorts_dedupes_and_ignores_extra_columns(tmp_path):
    (tmp_path / "ABC.csv").write_text(
        "timestamp,open,high,low,close,volume,source,vwap\n"
        "2024-01-03T05:00:00+00:00,3,3,3,3,30,x,1\n"
        "2024-01-02T05:00:00Z,2,2,2,2,20,x,1\n"
        "2024-01-03T05:00:00+00:00,4,4,4,4,40,x,1\n"
    )
    got = CsvBarSource(tmp_path).get_bars(SymbolConfig(symbol="ABC", asset_class="stock"),
                                          datetime(2024, 1, 1))  # naive start treated as UTC
    assert list(got.columns) == list(BAR_COLUMNS)
    assert got.index.is_monotonic_increasing and got.index.is_unique
    assert got["close"].tolist() == [2.0, 4.0]  # duplicate keeps last


def test_csv_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        CsvBarSource(tmp_path).get_bars(SymbolConfig(symbol="NOPE", asset_class="stock"), datetime(2024, 1, 1))


# --- Alpaca mapping (pure, no network) ------------------------------------------------------------

def test_alpaca_bars_to_frame_maps_multiindex():
    ts = pd.date_range("2024-01-02 05:00", periods=3, freq="D", tz="UTC")
    rows = []
    for sym, base in (("NVDA", 100.0), ("AMD", 50.0)):
        for i, t in enumerate(ts):
            rows.append({"symbol": sym, "timestamp": t, "open": base + i, "high": base + i + 1,
                         "low": base + i - 1, "close": base + i + 0.5, "volume": 1000 + i,
                         "trade_count": 10, "vwap": base + i})
    df = pd.DataFrame(rows).set_index(["symbol", "timestamp"])
    df = df.iloc[::-1]  # unsorted input
    out = alpaca_bars_to_frame(df, "NVDA")
    assert list(out.columns) == list(BAR_COLUMNS)
    assert out.index.equals(pd.DatetimeIndex(ts, name="timestamp"))
    assert out["close"].tolist() == [100.5, 101.5, 102.5]
    assert out.dtypes.eq(float).all()
    assert alpaca_bars_to_frame(df, "PLTR").empty
    assert alpaca_bars_to_frame(pd.DataFrame(), "NVDA").empty


def test_alpaca_source_constructs_without_network():
    from quantbot.config import Credentials
    from quantbot.data.sources import AlpacaBarSource

    src = AlpacaBarSource(Credentials(api_key="k", secret_key="s"))
    assert src.stock_feed == "iex" and src._stock is None  # clients are created lazily


# --- completed_bars -------------------------------------------------------------------------------

def test_completed_bars_drops_in_progress_daily_bar():
    idx = pd.date_range("2024-03-01", periods=5, freq="D", tz="UTC")
    bars = make_bars(idx)
    now = pd.Timestamp("2024-03-05 15:00", tz="UTC")  # the 03-05 bar is still forming
    got = completed_bars(bars, now)
    assert got.index[-1] == pd.Timestamp("2024-03-04", tz="UTC")
    assert len(completed_bars(bars, pd.Timestamp("2024-03-06", tz="UTC"))) == 5


# --- validate_bars --------------------------------------------------------------------------------

def _now_after(bars):
    return bars.index[-1] + pd.Timedelta(hours=30)


def test_valid_stock_bars_pass():
    bars = make_bars(stock_days())
    res = validate_bars(bars, AssetClass.STOCK, _now_after(bars), CFG)
    assert res.ok, res.issues


def test_validate_catches_nan():
    bars = make_bars(stock_days())
    bars.iloc[5, 3] = np.nan
    res = validate_bars(bars, AssetClass.STOCK, _now_after(bars), CFG)
    assert not res.ok and any(i.startswith("nan") for i in res.issues)


def test_validate_catches_high_below_low_and_close_outside():
    bars = make_bars(stock_days())
    bars.iloc[7, bars.columns.get_loc("high")] = bars.iloc[7]["low"] * 0.9
    res = validate_bars(bars, AssetClass.STOCK, _now_after(bars), CFG)
    assert not res.ok and any(i.startswith("high_below_low") for i in res.issues)
    assert any(i.startswith("close_outside_range") for i in res.issues)


def test_validate_catches_non_positive():
    bars = make_bars(stock_days())
    bars.iloc[3, 0] = 0.0
    assert any(i.startswith("non_positive") for i in validate_bars(bars, AssetClass.STOCK, _now_after(bars), CFG).issues)


def test_validate_catches_duplicates_and_unsorted():
    bars = make_bars(stock_days())
    dup = pd.concat([bars, bars.iloc[[10]]])
    res = validate_bars(dup, AssetClass.STOCK, _now_after(bars), CFG)
    assert not res.ok
    assert any(i.startswith("duplicate_index") for i in res.issues)
    assert any(i.startswith("unsorted_index") for i in res.issues)


def test_validate_stock_gap_business_days():
    idx = stock_days(periods=60)
    bars = make_bars(idx)
    # weekends alone are fine (already passes above); a 1-day holiday is fine
    holiday = bars.drop(bars.index[20])
    assert validate_bars(holiday, AssetClass.STOCK, _now_after(bars), CFG).ok
    # 8 missing business days > max_gap_bars=5
    gapped = bars.drop(bars.index[20:28])
    res = validate_bars(gapped, AssetClass.STOCK, _now_after(bars), CFG)
    assert not res.ok and any(i.startswith("gap") for i in res.issues)


def test_validate_crypto_gap_calendar_days():
    idx = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    bars = make_bars(idx)
    # a crypto weekend missing counts (calendar days): 2 missing is ok, 6 is not
    assert validate_bars(bars.drop(bars.index[10:12]), AssetClass.CRYPTO, _now_after(bars), CFG).ok
    res = validate_bars(bars.drop(bars.index[10:16]), AssetClass.CRYPTO, _now_after(bars), CFG)
    assert not res.ok and any(i.startswith("gap") for i in res.issues)


def test_validate_stale():
    idx = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC")
    bars = make_bars(idx)
    fresh = bars.index[-1] + pd.Timedelta(hours=30)
    assert validate_bars(bars, AssetClass.CRYPTO, fresh, CFG).ok
    late = bars.index[-1] + pd.Timedelta(hours=CFG.crypto_stale_after_hours + 1)
    res = validate_bars(bars, AssetClass.CRYPTO, late, CFG)
    assert not res.ok and any(i.startswith("stale") for i in res.issues)
    # stocks have the longer (long-weekend) allowance
    assert validate_bars(bars, AssetClass.STOCK, late, CFG).ok
    very_late = bars.index[-1] + pd.Timedelta(hours=CFG.stock_stale_after_hours + 1)
    assert any(i.startswith("stale") for i in validate_bars(bars, AssetClass.STOCK, very_late, CFG).issues)


def test_validate_empty():
    empty = pd.DataFrame(columns=list(BAR_COLUMNS), index=pd.DatetimeIndex([], tz="UTC"))
    res = validate_bars(empty, AssetClass.STOCK, datetime.now(timezone.utc), CFG)
    assert not res.ok and res.issues
