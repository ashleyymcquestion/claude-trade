"""Fair-value model and trade decision for the 15-minute BTC up/down markets.

Rules (from the contract terms): the market resolves YES when the 60-second average of the CF
Benchmarks BRTI before the close is at least the 60-second average before the open (the "target
price"). So the question at any moment is: given BTC is at S now and the target is K, how likely
is the closing average to finish at or above K?

We model BTC over a few minutes as a driftless random walk in log price with volatility estimated
from the last hour of 1-minute returns. Averaging over the final 60 seconds removes some variance,
which the effective-time formula accounts for. This is a simple, honest baseline, not a promise
of an edge: if the market is efficient, it will mostly find nothing worth trading after fees.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime

from .client import Market
from .config import StrategyConfig
from .pricefeed import Candle

TAKER_FEE_RATE = 0.07   # Kalshi general taker fee: ceil(0.07 * C * P * (1 - P)) dollars. Verify.


def taker_fee_cents(count: int, price_cents: int, rate: float = TAKER_FEE_RATE) -> int:
    """Total fee for ``count`` contracts at ``price_cents``, rounded UP to the cent."""
    p = price_cents / 100.0
    # round() first so float noise (e.g. 1.0000000002) doesn't add a cent
    return int(math.ceil(round(rate * count * p * (1 - p) * 100, 6)))


def vol_per_minute(candles: list[Candle], floor: float) -> float:
    """Standard deviation of 1-minute log returns, never below ``floor``."""
    closes = [c.close for c in candles if c.close > 0]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])]
    if len(rets) < 10:
        return floor
    return max(statistics.pstdev(rets), floor)


def effective_seconds(seconds_left: float, window: float = 60.0) -> float:
    """Variance-equivalent time left when the outcome is the average over the last ``window`` s.

    Before the averaging window starts: plain time until the window, plus window/3 (the variance
    of a time-average of a random walk). Inside the window only the unrealized tail remains.
    """
    t = max(seconds_left, 0.0)
    if t >= window:
        return t - window + window / 3.0
    return t ** 3 / (3.0 * window ** 2)


def prob_up(spot: float, strike: float, vol_min: float, seconds_left: float) -> float:
    tau = effective_seconds(seconds_left)
    if tau <= 0 or vol_min <= 0:
        return 1.0 if spot >= strike else 0.0
    sigma = vol_min * math.sqrt(tau / 60.0)
    z = math.log(spot / strike) / sigma
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def estimate_strike(candles: list[Candle], open_time: datetime) -> float | None:
    """Rough target price: the close of the 1-minute candle that ends at the market open."""
    for c in candles:
        if c.start.timestamp() == open_time.timestamp() - 60:
            return c.close
    return None


@dataclass(frozen=True)
class Decision:
    ticker: str
    action: str                 # "buy" or "skip"
    reason: str
    side: str = ""              # "yes" (up) or "no" (down)
    price_cents: int = 0
    prob_up: float | None = None
    edge: float | None = None   # dollars per contract after fee
    spot: float | None = None
    strike: float | None = None
    strike_source: str = ""
    seconds_left: float | None = None
    vol_min: float | None = None


def decide(market: Market, spot: float, candles: list[Candle], now: datetime,
           cfg: StrategyConfig) -> Decision:
    t = market.ticker
    left = (market.close_time - now).total_seconds()
    base = dict(spot=spot, seconds_left=left)
    if left > cfg.max_seconds_left:
        return Decision(t, "skip", f"too early ({left:.0f}s left)", **base)
    if left < cfg.min_seconds_left:
        return Decision(t, "skip", f"too late ({left:.0f}s left)", **base)

    strike, source = market.strike, "market"
    if strike is None and cfg.allow_estimated_strike:
        strike, source = estimate_strike(candles, market.open_time), "estimated"
    if strike is None:
        return Decision(t, "skip", "no target price available", **base)

    vol = vol_per_minute(candles, cfg.min_vol_per_minute)
    p = prob_up(spot, strike, vol, left)
    info = dict(base, prob_up=p, strike=strike, strike_source=source, vol_min=vol)

    best: Decision | None = None
    for side, ask, p_win in (("yes", market.yes_ask, p), ("no", market.no_ask, 1.0 - p)):
        if ask is None or not (cfg.min_price_cents <= ask <= cfg.max_price_cents):
            continue
        q = ask / 100.0
        edge = p_win - q - TAKER_FEE_RATE * q * (1 - q)
        if best is None or edge > (best.edge or -1):
            best = Decision(t, "skip", "", side=side, price_cents=ask, edge=edge, **info)
    if best is None:
        return Decision(t, "skip", "no ask inside the allowed price range", **info)
    if best.edge > cfg.max_edge:
        return _with(best, "skip", f"edge {best.edge:.3f} too large to trust (price feed mismatch?)")
    if best.edge < cfg.min_edge:
        return _with(best, "skip", f"edge {best.edge:.3f} below minimum {cfg.min_edge:.3f}")
    return _with(best, "buy", f"edge {best.edge:.3f} on {best.side.upper()} at {best.price_cents}c")


def _with(d: Decision, action: str, reason: str) -> Decision:
    return Decision(**{**d.__dict__, "action": action, "reason": reason})
