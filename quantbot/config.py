"""Validated configuration. Loaded from TOML; credentials only ever come from the environment."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import AssetClass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SymbolConfig(_Strict):
    symbol: str                                   # broker symbol, e.g. "NVDA" or "BTC/USD"
    asset_class: AssetClass
    data_symbol: str | None = None                # research-only proxy (e.g. "IBIT" for BTC/USD)
    enabled: bool = True


class CostConfig(_Strict):
    """Assumptions, not facts. Each must be checked against the broker's current schedule."""
    stock_commission_bps: float = Field(0.0, ge=0)
    stock_half_spread_bps: float = Field(2.0, ge=0)
    stock_slippage_bps: float = Field(3.0, ge=0)
    crypto_fee_bps: float = Field(25.0, ge=0)     # Alpaca crypto taker fee, lowest volume tier (verify)
    crypto_half_spread_bps: float = Field(5.0, ge=0)
    crypto_slippage_bps: float = Field(5.0, ge=0)

    def one_way_bps(self, asset_class: AssetClass) -> float:
        if asset_class is AssetClass.CRYPTO:
            return self.crypto_fee_bps + self.crypto_half_spread_bps + self.crypto_slippage_bps
        return self.stock_commission_bps + self.stock_half_spread_bps + self.stock_slippage_bps


class RiskConfig(_Strict):
    risk_per_trade: float = Field(0.005, gt=0, le=0.02)
    daily_loss_limit: float = Field(0.015, gt=0, le=0.10)
    weekly_loss_limit: float = Field(0.04, gt=0, le=0.20)
    max_drawdown: float = Field(0.08, gt=0, le=0.50)
    max_position_pct: float = Field(0.25, gt=0, le=1.0)
    max_gross_pct: float = Field(0.50, gt=0, le=1.0)
    min_notional_usd: float = Field(1.0, gt=0)
    max_open_positions: int = Field(6, ge=1)
    # Hard-coded policy, present only so the config file states it; validators forbid changing it.
    allow_margin: Literal[False] = False
    allow_short: Literal[False] = False
    allow_derivatives: Literal[False] = False

    @model_validator(mode="after")
    def _consistent(self) -> "RiskConfig":
        if self.max_position_pct > self.max_gross_pct:
            raise ValueError("max_position_pct cannot exceed max_gross_pct")
        if self.daily_loss_limit > self.weekly_loss_limit:
            raise ValueError("daily_loss_limit cannot exceed weekly_loss_limit")
        if self.weekly_loss_limit > self.max_drawdown:
            raise ValueError("weekly_loss_limit cannot exceed max_drawdown")
        return self


class TrendConfig(_Strict):
    enabled: bool = True
    ema_fast: int = Field(20, ge=2)
    ema_slow: int = Field(50, ge=3)
    sma_long: int = Field(200, ge=10)
    momentum_lookback: int = Field(20, ge=2)
    rel_volume_lookback: int = Field(20, ge=2)
    min_rel_volume: float = Field(0.8, ge=0)
    atr_period: int = Field(14, ge=2)
    max_atr_pct: float = Field(0.08, gt=0)        # skip entries when ATR/price is above this
    stop_atr_mult: float = Field(2.5, gt=0)
    trail_atr_mult: float = Field(3.0, gt=0)

    @model_validator(mode="after")
    def _order(self) -> "TrendConfig":
        if not self.ema_fast < self.ema_slow < self.sma_long:
            raise ValueError("need ema_fast < ema_slow < sma_long")
        return self


class MeanReversionConfig(_Strict):
    enabled: bool = True
    rsi_period: int = Field(2, ge=2)
    rsi_entry: float = Field(10.0, gt=0, lt=50)
    rsi_exit: float = Field(60.0, gt=50, lt=100)
    bb_period: int = Field(20, ge=5)
    bb_std: float = Field(2.0, gt=0)
    z_period: int = Field(20, ge=5)
    z_entry: float = Field(-2.0, lt=0)
    atr_period: int = Field(14, ge=2)
    stop_atr_mult: float = Field(2.0, gt=0)
    max_hold_bars: int = Field(10, ge=1)
    regime_sma: int = Field(200, ge=10)           # only trade when price above this (no adverse trend)


class BreakoutConfig(_Strict):
    enabled: bool = True
    donchian_entry: int = Field(20, ge=5)
    donchian_exit: int = Field(10, ge=2)
    atr_period: int = Field(14, ge=2)
    min_atr_pct: float = Field(0.005, ge=0)
    max_atr_pct: float = Field(0.10, gt=0)
    rel_volume_lookback: int = Field(20, ge=2)
    min_rel_volume: float = Field(1.2, ge=0)
    stop_atr_mult: float = Field(2.0, gt=0)
    trail_atr_mult: float = Field(3.0, gt=0)


class StrategiesConfig(_Strict):
    trend: TrendConfig = TrendConfig()
    mean_reversion: MeanReversionConfig = MeanReversionConfig()
    breakout: BreakoutConfig = BreakoutConfig()


class DataConfig(_Strict):
    bars_dir: str = "data/bars"
    timeframe: Literal["1Day"] = "1Day"
    stock_stale_after_hours: float = Field(96.0, gt=0)    # covers a long weekend
    crypto_stale_after_hours: float = Field(36.0, gt=0)
    max_gap_bars: int = Field(5, ge=0)


class ExecutionConfig(_Strict):
    # "live" is deliberately not an allowed value. See quantbot/broker/live_gate.py.
    mode: Literal["sim", "alpaca_paper"] = "sim"
    db_path: str = "state/quantbot.sqlite3"
    poll_seconds: int = Field(60, ge=5)
    order_timeout_seconds: int = Field(120, ge=10)


class AlertConfig(_Strict):
    webhook_url_env: str = "QUANTBOT_ALERT_WEBHOOK"    # name of env var holding the URL
    min_level: Literal["info", "warning", "critical"] = "warning"


class AppConfig(_Strict):
    initial_capital: float = Field(1000.0, gt=0)
    symbols: tuple[SymbolConfig, ...]
    costs: CostConfig = CostConfig()
    risk: RiskConfig = RiskConfig()
    strategies: StrategiesConfig = StrategiesConfig()
    data: DataConfig = DataConfig()
    execution: ExecutionConfig = ExecutionConfig()
    alerts: AlertConfig = AlertConfig()

    @field_validator("symbols")
    @classmethod
    def _unique(cls, v: tuple[SymbolConfig, ...]) -> tuple[SymbolConfig, ...]:
        names = [s.symbol for s in v]
        if len(names) != len(set(names)):
            raise ValueError("duplicate symbols")
        if not v:
            raise ValueError("at least one symbol required")
        return v

    def enabled_symbols(self) -> list[SymbolConfig]:
        return [s for s in self.symbols if s.enabled]

    def symbol(self, name: str) -> SymbolConfig:
        for s in self.symbols:
            if s.symbol == name:
                return s
        raise KeyError(name)


def load_config(path: str | Path) -> AppConfig:
    with open(path, "rb") as f:
        return AppConfig.model_validate(tomllib.load(f))


class Credentials(BaseModel):
    """Alpaca paper credentials from the environment. repr never shows the secret."""
    model_config = ConfigDict(frozen=True)
    api_key: str
    secret_key: str

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return "Credentials(api_key=***, secret_key=***)"

    __str__ = __repr__

    @classmethod
    def from_env(cls) -> "Credentials":
        key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in the environment")
        return cls(api_key=key, secret_key=secret)
