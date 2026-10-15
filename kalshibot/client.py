"""Minimal Kalshi Trade API v2 client.

Authenticated requests carry three headers. The signature is RSA-PSS / SHA-256 over
``timestamp_ms + METHOD + path``, where path includes ``/trade-api/v2`` and excludes the query
string. Market data endpoints are public and need no key.

Kalshi is moving prices from integer cents (``yes_ask``) to dollar strings (``yes_ask_dollars``);
the helpers here read either.
"""

from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from .config import Credentials


class KalshiError(RuntimeError):
    pass


def sign(private_key, timestamp_ms: str, method: str, path: str) -> str:
    message = (timestamp_ms + method.upper() + path.split("?")[0]).encode()
    sig = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode()


def cents(obj: dict, field: str) -> int | None:
    """Read a price as integer cents from ``field`` (cents) or ``field_dollars`` (dollar string)."""
    v = obj.get(field)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(round(v))
    d = obj.get(field + "_dollars")
    if d not in (None, ""):
        try:
            return int(round(float(d) * 100))
        except (TypeError, ValueError):
            return None
    return None


def count_field(obj: dict, field: str) -> int:
    """Read a contract count from ``field`` or its fixed-point ``field_fp`` variant."""
    for name in (field, field + "_fp"):
        v = obj.get(name)
        if v not in (None, ""):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                pass
    return 0


def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Market:
    ticker: str
    open_time: datetime
    close_time: datetime
    status: str
    yes_ask: int | None      # cents; price to buy YES ("up")
    no_ask: int | None       # cents; price to buy NO ("down")
    strike: float | None     # target price (BTC average before open), if the API reports it
    result: str              # "yes" / "no" once settled, else ""

    @classmethod
    def from_api(cls, m: dict) -> "Market":
        strike = m.get("floor_strike")
        try:
            strike = float(strike) if strike not in (None, "") else None
        except (TypeError, ValueError):
            strike = None
        return cls(
            ticker=m["ticker"],
            open_time=parse_time(m.get("open_time")),
            close_time=parse_time(m.get("close_time")),
            status=str(m.get("status", "")),
            yes_ask=cents(m, "yes_ask"),
            no_ask=cents(m, "no_ask"),
            strike=strike if strike and strike > 0 else None,
            result=str(m.get("result") or "").lower(),
        )


class KalshiClient:
    def __init__(self, base_url: str, creds: Credentials | None = None,
                 session: requests.Session | None = None, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._path_prefix = urlparse(self.base_url).path      # "/trade-api/v2"
        self._creds = creds
        self._key = (serialization.load_pem_private_key(creds.private_key_pem, password=None)
                     if creds else None)
        self.http = session or requests.Session()
        self.timeout = timeout

    @property
    def authenticated(self) -> bool:
        return self._key is not None

    def _headers(self, method: str, path: str) -> dict:
        if self._key is None:
            raise KalshiError("this call needs API credentials (see docs/KALSHI.md)")
        ts = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self._creds.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sign(self._key, ts, method, self._path_prefix + path),
        }

    def _request(self, method: str, path: str, *, auth: bool, params=None, json=None) -> dict:
        headers = self._headers(method, path) if auth else {}
        r = self.http.request(method, self.base_url + path, params=params, json=json,
                              headers=headers, timeout=self.timeout)
        if r.status_code >= 400:
            raise KalshiError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    # ---------------------------------------------------------------- public market data
    def open_markets(self, series_ticker: str) -> list[Market]:
        data = self._request("GET", "/markets", auth=False,
                             params={"series_ticker": series_ticker, "status": "open", "limit": 50})
        return [Market.from_api(m) for m in data.get("markets", [])]

    def market(self, ticker: str) -> Market:
        return Market.from_api(self._request("GET", f"/markets/{ticker}", auth=False)["market"])

    # ---------------------------------------------------------------- account (signed)
    def balance_cents(self) -> int:
        return int(self._request("GET", "/portfolio/balance", auth=True).get("balance", 0))

    def buy(self, ticker: str, side: str, count: int, price_cents: int, time_in_force: str) -> dict:
        """Limit buy of ``count`` contracts on ``side`` ("yes"/"no") at up to ``price_cents``."""
        if side not in ("yes", "no"):
            raise ValueError(side)
        body = {
            "ticker": ticker, "action": "buy", "side": side, "count": int(count), "type": "limit",
            f"{side}_price": int(price_cents), "time_in_force": time_in_force,
            "client_order_id": str(uuid.uuid4()),
        }
        return self._request("POST", "/portfolio/orders", auth=True, json=body).get("order", {})

    def order(self, order_id: str) -> dict:
        return self._request("GET", f"/portfolio/orders/{order_id}", auth=True).get("order", {})
