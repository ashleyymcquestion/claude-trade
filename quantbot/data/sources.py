"""Bar sources. Every source returns the bar contract from ``quantbot.models``:
tz-aware UTC DatetimeIndex (sorted, unique) with float columns open, high, low, close, volume.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Credentials, SymbolConfig
from ..models import BAR_COLUMNS, AssetClass


def _utc(ts: datetime | pd.Timestamp | None) -> pd.Timestamp | None:
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _normalize(df: pd.DataFrame, start: datetime | None, end: datetime | None) -> pd.DataFrame:
    """Contract columns only, float dtype, UTC index sorted, duplicates dropped (keep last)."""
    out = df.loc[:, list(BAR_COLUMNS)].astype(float)
    out = out.sort_index(kind="stable")
    out = out[~out.index.duplicated(keep="last")]
    s, e = _utc(start), _utc(end)
    if s is not None:
        out = out[out.index >= s]
    if e is not None:
        out = out[out.index <= e]
    out.index.name = "timestamp"
    return out


def csv_name(sym: SymbolConfig) -> str:
    return (sym.data_symbol or sym.symbol).replace("/", "-") + ".csv"


class BarSource(ABC):
    @abstractmethod
    def get_bars(self, sym: SymbolConfig, start: datetime, end: datetime | None = None) -> pd.DataFrame:
        """Daily bars for ``sym`` with timestamps in [start, end]."""


class CsvBarSource(BarSource):
    """Reads ``<bars_dir>/<data_symbol or symbol, '/'->'-'>.csv``. Extra columns (e.g. ``source``) are ignored."""

    def __init__(self, bars_dir: str | Path) -> None:
        self.bars_dir = Path(bars_dir)

    def path_for(self, sym: SymbolConfig) -> Path:
        return self.bars_dir / csv_name(sym)

    def get_bars(self, sym: SymbolConfig, start: datetime, end: datetime | None = None) -> pd.DataFrame:
        path = self.path_for(sym)
        if not path.exists():
            raise FileNotFoundError(f"no bar file for {sym.symbol}: {path}")
        raw = pd.read_csv(path)
        missing = [c for c in ("timestamp", *BAR_COLUMNS) if c not in raw.columns]
        if missing:
            raise ValueError(f"{path}: missing columns {missing}")
        raw.index = pd.DatetimeIndex(pd.to_datetime(raw["timestamp"], utc=True, format="ISO8601"))
        return _normalize(raw, start, end)


def write_bars_csv(df: pd.DataFrame, path: str | Path, source: str) -> Path:
    """Write the contract CSV (timestamp,open,high,low,close,volume,source).

    ``source`` is a provenance string (e.g. "alpaca:iex:adj=all"). Never use this for synthetic data
    under data/bars/."""
    if not source:
        raise ValueError("source provenance is required")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise TypeError("bars must have a DatetimeIndex")
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    out = df.loc[:, list(BAR_COLUMNS)].astype(float).copy()
    out.insert(0, "timestamp", [t.isoformat() for t in idx])
    out["source"] = source
    out.to_csv(path, index=False)
    return path


def alpaca_bars_to_frame(bars_df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Map alpaca-py ``BarSet.df`` (MultiIndex symbol,timestamp; extra cols trade_count, vwap)
    to the bar contract for one symbol. Pure: no network."""
    cols = list(BAR_COLUMNS)
    if bars_df is None or bars_df.empty:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in cols},
                            index=pd.DatetimeIndex([], tz="UTC", name="timestamp"))
    df = bars_df
    if isinstance(df.index, pd.MultiIndex):
        if symbol not in df.index.get_level_values("symbol"):
            return alpaca_bars_to_frame(pd.DataFrame(), symbol)
        df = df.xs(symbol, level="symbol")
    idx = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True))
    df = df.set_axis(idx, axis=0)
    return _normalize(df, None, None)


class AlpacaBarSource(BarSource):
    """Daily bars from Alpaca market data. Uses the BROKER symbol (``sym.symbol``), never the
    research proxy ``data_symbol``. Stocks are split+dividend adjusted (Adjustment.ALL)."""

    def __init__(self, creds: Credentials, stock_feed: str = "iex") -> None:
        self._creds = creds
        self.stock_feed = stock_feed
        self._stock = None
        self._crypto = None

    def _clients(self):
        from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient

        if self._stock is None:
            self._stock = StockHistoricalDataClient(self._creds.api_key, self._creds.secret_key)
        if self._crypto is None:
            self._crypto = CryptoHistoricalDataClient(self._creds.api_key, self._creds.secret_key)
        return self._stock, self._crypto

    def get_bars(self, sym: SymbolConfig, start: datetime, end: datetime | None = None) -> pd.DataFrame:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        stock_client, crypto_client = self._clients()
        s, e = _utc(start), _utc(end)
        s_dt = s.to_pydatetime()
        e_dt = e.to_pydatetime() if e is not None else None
        if sym.asset_class is AssetClass.CRYPTO:
            req = CryptoBarsRequest(symbol_or_symbols=sym.symbol, timeframe=TimeFrame.Day,
                                    start=s_dt, end=e_dt)
            barset = crypto_client.get_crypto_bars(req)
        else:
            req = StockBarsRequest(symbol_or_symbols=sym.symbol, timeframe=TimeFrame.Day,
                                   start=s_dt, end=e_dt, adjustment=Adjustment.ALL,
                                   feed=DataFeed(self.stock_feed))
            barset = stock_client.get_stock_bars(req)
        frame = alpaca_bars_to_frame(barset.df, sym.symbol)
        return _normalize(frame, s, e)
