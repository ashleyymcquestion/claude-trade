"""Daily close prices for stocks and crypto on one shared 7-day calendar."""

from datetime import datetime, timedelta, timezone

import pandas as pd

MAX_FFILL_DAYS = 5  # carry stock prices over weekends/holidays, but not stale data


def align(stock_closes: pd.DataFrame, crypto_closes: pd.DataFrame) -> pd.DataFrame:
    frames = [df for df in (stock_closes, crypto_closes) if not df.empty]
    if not frames:
        raise ValueError("No price data")
    combined = pd.concat(frames, axis=1, sort=True)
    combined.index = pd.DatetimeIndex(combined.index).tz_localize(None).normalize()
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.asfreq("D").ffill(limit=MAX_FFILL_DAYS)


def _closes(barset_df: pd.DataFrame, tz: str) -> pd.DataFrame:
    if barset_df.empty:
        return pd.DataFrame()
    closes = barset_df["close"].unstack(level="symbol")
    # Stock daily bars are stamped at midnight New York; crypto at midnight UTC.
    closes.index = pd.DatetimeIndex(closes.index).tz_convert(tz).tz_localize(None).normalize()
    return closes


def fetch_closes(stocks, crypto, start: datetime, api_key: str, secret_key: str) -> pd.DataFrame:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
    from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    stock_df = pd.DataFrame()
    if stocks:
        client = StockHistoricalDataClient(api_key, secret_key)
        bars = client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=list(stocks), timeframe=TimeFrame.Day, start=start,
            adjustment=Adjustment.ALL, feed=DataFeed.IEX))
        stock_df = _closes(bars.df, "America/New_York")

    crypto_df = pd.DataFrame()
    if crypto:
        client = CryptoHistoricalDataClient(api_key, secret_key)
        bars = client.get_crypto_bars(CryptoBarsRequest(
            symbol_or_symbols=list(crypto), timeframe=TimeFrame.Day, start=start))
        crypto_df = _closes(bars.df, "UTC")

    return align(stock_df, crypto_df)


def fetch_recent(cfg, api_key: str, secret_key: str, extra_days: int = 60) -> pd.DataFrame:
    start = datetime.now(timezone.utc) - timedelta(days=cfg.warmup_days() + extra_days)
    return fetch_closes(cfg.stocks, cfg.crypto, start, api_key, secret_key)


def load_csv(path: str) -> pd.DataFrame:
    """CSV with a date column first and one close-price column per symbol."""
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df.asfreq("D").ffill(limit=MAX_FFILL_DAYS)
