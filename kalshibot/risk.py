"""Kill switch, daily limits, trade sizing and the real-money gate."""

from __future__ import annotations

import os
from pathlib import Path

from .config import KalshiConfig
from .model import taker_fee_cents

LIVE_CONFIRM_ENV = "KALSHI_LIVE_CONFIRM"
LIVE_CONFIRM_VALUE = "I-accept-real-money-losses"


class LiveTradingNotAllowed(RuntimeError):
    pass


def assert_live_allowed(cfg: KalshiConfig, live_flag: bool) -> None:
    """Real orders on the prod exchange need all three: allow_live in the config, the confirm
    environment variable, and ``--live`` on the command line. Demo and dry runs need none."""
    if cfg.run.environment != "prod" or cfg.run.dry_run:
        return
    missing = []
    if not cfg.run.allow_live:
        missing.append("run.allow_live = true in the config")
    if os.environ.get(LIVE_CONFIRM_ENV) != LIVE_CONFIRM_VALUE:
        missing.append(f"{LIVE_CONFIRM_ENV}={LIVE_CONFIRM_VALUE} in the environment")
    if not live_flag:
        missing.append("--live on the command line")
    if missing:
        raise LiveTradingNotAllowed("refusing to trade real money; missing: " + "; ".join(missing))


def kill_reason() -> str | None:
    """Shares the repo's kill switches: the kill file (``$QUANTBOT_KILL_FILE``, default
    ``state/KILL``) and ``KILL_SWITCH=1``. While either is set, no orders are sent."""
    if os.environ.get("KILL_SWITCH", "").strip().lower() in ("1", "true", "yes", "on"):
        return "KILL_SWITCH is set"
    path = Path(os.environ.get("QUANTBOT_KILL_FILE") or "state/KILL")
    try:
        if path.exists():
            return f"kill file {path} exists"
    except OSError:
        return f"kill file {path} unreadable"
    return None


def block_reason(cfg: KalshiConfig, day_stats: dict) -> str | None:
    """Why no new trade may be opened right now, or None."""
    k = kill_reason()
    if k:
        return k
    r = cfg.risk
    if day_stats["trades"] >= r.max_trades_per_day:
        return f"max trades per day reached ({r.max_trades_per_day})"
    # conservative: money in unsettled positions counts as lost until it settles
    loss = day_stats["at_risk_cents"] - day_stats["realized_cents"]
    if loss >= r.daily_loss_limit_usd * 100:
        return f"daily loss limit reached (${loss / 100:.2f} lost or at risk)"
    return None


def size(cfg: KalshiConfig, price_cents: int, day_stats: dict, balance_cents: int | None) -> int:
    """Largest count within the per-trade caps, the remaining daily loss budget and the balance."""
    r = cfg.risk
    budget = r.max_cost_per_trade_usd * 100
    remaining = r.daily_loss_limit_usd * 100 - (day_stats["at_risk_cents"] - day_stats["realized_cents"])
    budget = min(budget, remaining)
    if balance_cents is not None:
        budget = min(budget, balance_cents)
    n = r.max_contracts_per_trade
    while n > 0 and n * price_cents + taker_fee_cents(n, price_cents) > budget:
        n -= 1
    return n
