"""Strategy interface.

A strategy is a pure function of one symbol's bars. It returns, for EVERY bar, what it would
decide at that bar's close using only that bar and earlier ones. The backtester and the live
runner both act on a row no earlier than the next bar's open, so a strategy can never see
the price it trades at.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

SIGNAL_COLUMNS = ("entry", "exit", "stop_distance", "trail_distance", "max_hold_bars")


class Strategy(ABC):
    name: str = "base"

    @property
    @abstractmethod
    def warmup_bars(self) -> int:
        """Bars needed before the first valid signal."""

    @abstractmethod
    def signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Return a frame on ``bars.index`` with columns SIGNAL_COLUMNS:

        entry          bool   open a long at the next bar if flat
        exit           bool   close the long at the next bar if held
        stop_distance  float  initial protective stop = fill price - stop_distance (NaN = none)
        trail_distance float  trailing stop = highest close since entry - trail_distance (NaN = none)
        max_hold_bars  float  exit after this many bars in the trade (NaN = no limit)

        Rows inside the warm-up period must have entry=False. Implementations must only use
        rolling/expanding/ewm computations that look backwards (no ``shift(-n)``, no centered
        windows).
        """


def empty_signals(index: pd.Index) -> pd.DataFrame:
    return pd.DataFrame(
        {"entry": False, "exit": False, "stop_distance": float("nan"),
         "trail_distance": float("nan"), "max_hold_bars": float("nan")},
        index=index,
    )
