"""Validated configuration, loaded from TOML. Credentials only ever come from the environment."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BASE_URLS = {
    "demo": "https://demo-api.kalshi.co/trade-api/v2",
    "prod": "https://api.elections.kalshi.com/trade-api/v2",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StrategyConfig(_Strict):
    series_ticker: str = "KXBTC15M"
    # Only trade inside this window of seconds before the market closes. Early in the window the
    # outcome is close to a coin flip and the market prices it that way, so there is no edge there.
    min_seconds_left: int = Field(90, ge=0)
    max_seconds_left: int = Field(600, ge=1)
    # Required model edge per contract, in dollars, AFTER the fee (0.05 = 5 cents).
    min_edge: float = Field(0.05, gt=0, lt=1)
    # An "edge" bigger than this almost always means our BTC price differs from the settlement
    # index (CF Benchmarks BRTI), not that the market is wrong. Skip instead of trading.
    max_edge: float = Field(0.25, gt=0, le=1)
    min_price_cents: int = Field(5, ge=1, le=99)
    max_price_cents: int = Field(95, ge=1, le=99)
    vol_lookback_minutes: int = Field(60, ge=10, le=300)
    # Floor on per-minute BTC volatility (log return), so a calm hour doesn't make the model
    # overconfident. 0.0005 = 0.05% per minute.
    min_vol_per_minute: float = Field(0.0005, gt=0)
    # When the market doesn't report its target price, estimate it from the BTC feed (less exact).
    allow_estimated_strike: bool = False

    @model_validator(mode="after")
    def _check(self) -> "StrategyConfig":
        if self.min_seconds_left >= self.max_seconds_left:
            raise ValueError("min_seconds_left must be below max_seconds_left")
        if self.min_price_cents >= self.max_price_cents:
            raise ValueError("min_price_cents must be below max_price_cents")
        if self.min_edge >= self.max_edge:
            raise ValueError("min_edge must be below max_edge")
        return self


class RiskConfig(_Strict):
    max_contracts_per_trade: int = Field(5, ge=1, le=1000)
    max_cost_per_trade_usd: float = Field(5.0, gt=0)
    max_trades_per_day: int = Field(20, ge=1)
    # Stop for the rest of the New York day once realized losses plus money still tied up in
    # unsettled positions reach this amount.
    daily_loss_limit_usd: float = Field(10.0, gt=0)


class RunConfig(_Strict):
    environment: Literal["demo", "prod"] = "demo"
    # Real money also needs KALSHI_LIVE_CONFIRM in the environment and --live on the command line.
    allow_live: bool = False
    # Log decisions and score them against real results, but never send an order.
    dry_run: bool = False
    poll_seconds: int = Field(15, ge=5, le=300)
    db_path: str = "state/kalshi.sqlite3"
    time_in_force: Literal["immediate_or_cancel", "fill_or_kill"] = "immediate_or_cancel"
    btc_product: str = "BTC-USD"

    @model_validator(mode="after")
    def _check(self) -> "RunConfig":
        if self.environment == "prod" and not self.allow_live and not self.dry_run:
            raise ValueError("environment = 'prod' needs allow_live = true (or dry_run = true)")
        return self


class KalshiConfig(_Strict):
    run: RunConfig = RunConfig()
    strategy: StrategyConfig = StrategyConfig()
    risk: RiskConfig = RiskConfig()

    @property
    def base_url(self) -> str:
        return BASE_URLS[self.run.environment]


def load_config(path: str | Path) -> KalshiConfig:
    with open(path, "rb") as f:
        return KalshiConfig.model_validate(tomllib.load(f))


@dataclass(frozen=True)
class Credentials:
    key_id: str
    private_key_pem: bytes

    def __repr__(self) -> str:   # never print the key
        return f"Credentials(key_id={self.key_id[:6]}…, private_key=<hidden>)"


def credentials_from_env(environment: str) -> Credentials | None:
    """KALSHI_DEMO_* for the demo exchange, KALSHI_PROD_* for real money, so the two never mix.

    The private key is given either as a file path (``..._PRIVATE_KEY_PATH``) or inline PEM
    (``..._PRIVATE_KEY``). Returns None when not set (fine for dry runs, which only read
    public market data).
    """
    prefix = f"KALSHI_{environment.upper()}_"
    key_id = os.environ.get(prefix + "KEY_ID", "").strip()
    pem_path = os.environ.get(prefix + "PRIVATE_KEY_PATH", "").strip()
    pem_inline = os.environ.get(prefix + "PRIVATE_KEY", "")
    if not key_id:
        return None
    if pem_path:
        pem = Path(pem_path).expanduser().read_bytes()
    elif pem_inline.strip():
        pem = pem_inline.replace("\\n", "\n").encode()
    else:
        return None
    return Credentials(key_id, pem)
