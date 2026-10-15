"""Loads config.toml into a flat, validated Config."""

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.toml"


@dataclass(frozen=True)
class Config:
    stocks: tuple[str, ...] = ("SPY", "QQQ", "IWM", "EFA", "TLT", "GLD")
    crypto: tuple[str, ...] = ("BTC/USD", "ETH/USD", "SOL/USD")

    sma_fast: int = 70
    sma_slow: int = 280
    momentum_lookbacks: tuple[int, ...] = (30, 91, 182, 365)
    vol_lookback: int = 60
    target_vol: float = 0.12
    execution_lag: int = 1

    max_gross: float = 1.0
    max_weight: float = 0.25
    max_crypto: float = 0.30
    rebalance_band: float = 0.02
    min_order_usd: float = 10.0
    max_daily_loss: float = 0.03
    on_daily_loss: str = "halt"
    kill_switch: bool = False
    max_order_usd: float = 1e12

    # Robinhood: coin -> US-listed ETF whose daily history stands in for the coin's.
    proxies: dict = field(default_factory=dict)
    # Robinhood: share of the whole account (crypto + stocks + cash) this strategy may manage.
    # 1.0 = all of it. The rest is left for the stock bot (quantbot, configs/robinhood_stocks.toml).
    crypto_sleeve: float = 1.0

    stock_cost_bps: float = 5.0
    crypto_cost_bps: float = 25.0

    @property
    def symbols(self) -> list[str]:
        return [*self.stocks, *self.crypto]

    def is_crypto(self, symbol: str) -> bool:
        return symbol in self.crypto

    def warmup_days(self) -> int:
        return max(self.sma_slow, *self.momentum_lookbacks, self.vol_lookback) + 1

    def __post_init__(self):
        if self.on_daily_loss not in ("halt", "flatten"):
            raise ValueError("on_daily_loss must be 'halt' or 'flatten'")
        if not 0 < self.max_gross <= 1.0:
            raise ValueError("max_gross must be in (0, 1]; leverage is not supported")
        if not 0 < self.crypto_sleeve <= 1.0:
            raise ValueError("crypto_sleeve must be in (0, 1]")
        if self.sma_fast >= self.sma_slow:
            raise ValueError("sma_fast must be shorter than sma_slow")


def load_config(path: str | Path = DEFAULT_PATH) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    flat = {}
    for section in raw.values():
        flat.update(section)
    known = {f.name for f in fields(Config)}
    unknown = set(flat) - known
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")
    return Config(**{k: tuple(v) if isinstance(v, list) else v for k, v in flat.items()})
