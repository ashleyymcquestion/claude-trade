"""Strategy registry."""

from __future__ import annotations

from ..config import StrategiesConfig
from .base import SIGNAL_COLUMNS, Strategy
from .breakout import BreakoutStrategy
from .mean_reversion import MeanReversionStrategy
from .trend import TrendStrategy

STRATEGY_NAMES = ("trend", "mean_reversion", "breakout")

_CLASSES = {
    "trend": TrendStrategy,
    "mean_reversion": MeanReversionStrategy,
    "breakout": BreakoutStrategy,
}


def get_strategy(name: str, cfg: StrategiesConfig | None = None) -> Strategy:
    """Build one strategy from its sub-config (regardless of ``enabled``).

    Walk-forward passes a StrategiesConfig with overridden sub-configs, e.g.
    ``cfg.model_copy(update={"trend": cfg.trend.model_copy(update={...})})``."""
    if name not in _CLASSES:
        raise KeyError(f"unknown strategy {name!r}; expected one of {STRATEGY_NAMES}")
    cfg = cfg or StrategiesConfig()
    return _CLASSES[name](getattr(cfg, name))


def build_strategies(cfg: StrategiesConfig) -> list[Strategy]:
    """All enabled strategies, in STRATEGY_NAMES order."""
    return [get_strategy(n, cfg) for n in STRATEGY_NAMES if getattr(cfg, n).enabled]


__all__ = ["SIGNAL_COLUMNS", "STRATEGY_NAMES", "Strategy", "TrendStrategy", "MeanReversionStrategy",
           "BreakoutStrategy", "build_strategies", "get_strategy"]
