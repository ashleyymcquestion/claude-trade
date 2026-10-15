"""BTC spot price and 1-minute candles from Coinbase's public API (no key needed).

Kalshi settles on the CF Benchmarks BRTI index, which averages several exchanges including
Coinbase. Coinbase alone is close but not identical, which is why the strategy caps how large an
"edge" it will believe (``max_edge``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests

COINBASE = "https://api.exchange.coinbase.com"


@dataclass(frozen=True)
class Candle:
    start: datetime
    close: float


class CoinbaseFeed:
    def __init__(self, product: str = "BTC-USD", session: requests.Session | None = None,
                 timeout: float = 10.0) -> None:
        self.product = product
        self.http = session or requests.Session()
        self.timeout = timeout

    def spot(self) -> float:
        r = self.http.get(f"{COINBASE}/products/{self.product}/ticker", timeout=self.timeout)
        r.raise_for_status()
        return float(r.json()["price"])

    def minute_candles(self, minutes: int) -> list[Candle]:
        """Oldest first. Coinbase returns [time, low, high, open, close, volume], newest first."""
        end = datetime.now(UTC)
        start = end - timedelta(minutes=minutes + 1)
        r = self.http.get(f"{COINBASE}/products/{self.product}/candles", timeout=self.timeout,
                          params={"granularity": 60, "start": start.isoformat(), "end": end.isoformat()})
        r.raise_for_status()
        rows = sorted(r.json(), key=lambda row: row[0])
        return [Candle(datetime.fromtimestamp(row[0], UTC), float(row[4])) for row in rows]
