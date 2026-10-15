"""There is no live-trading adapter in quantbot. This module exists to say so, loudly.

``LIVE_TRADING_ENABLED`` is a constant ``False``. Nothing in this codebase reads a setting, an
environment variable or a CLI flag that could flip it, and ``ExecutionConfig.mode`` only accepts
``"sim"`` and ``"alpaca_paper"``. ``AlpacaPaperBroker`` hard-codes ``TradingClient(..., paper=True)``
and accepts no URL override. ``assert_live_trading_not_enabled()`` runs at CLI start-up and raises
if anything (a monkeypatch, an environment variable, an edited constant) tries to enable it.

A future live deployment would require ALL of the following separate, manual steps. None of them
can be performed by this code, by configuration, or automatically:

  1. A separate repository or protected branch for live code, never merged back into this
     paper-only tree.
  2. A new ``LiveBroker`` class (it does not exist) written there, with independent code review by
     at least one engineer who did not write it.
  3. Paper-trading results from THIS system over a minimum evaluation period (e.g. several months
     covering different market regimes), reviewed against the backtest expectations, including
     costs, slippage, rejected orders and breaker trips.
  4. Explicit, written operator sign-off naming the account, the capital at risk and who is on
     call; recorded outside the codebase.
  5. Small, hard capital limits enforced at the broker account level (separately funded account,
     not just config), with the kill switch and loss breakers re-verified against the live adapter.
  6. Separate credentials stored separately from the paper ones; paper keys never grant live access.
  7. No automatic switching from paper to live, ever: a live process is started deliberately by a
     human, from the separate deployment, and a paper process can never promote itself.
"""

from __future__ import annotations

import os

LIVE_TRADING_ENABLED: bool = False

# Environment variables someone might plausibly set hoping to turn live trading on. Their presence
# with a truthy value is treated as an attempt and refused.
_SUSPECT_ENV = ("QUANTBOT_LIVE", "QUANTBOT_LIVE_TRADING", "LIVE_TRADING", "ALPACA_LIVE", "ALPACA_PAPER")


class LiveTradingNotAllowed(RuntimeError):
    pass


def _truthy(v: str | None) -> bool:
    return v is not None and v.strip().lower() in ("1", "true", "yes", "on", "live")


def assert_live_trading_not_enabled() -> None:
    """Raise LiveTradingNotAllowed if anything indicates an attempt to enable live trading."""
    if LIVE_TRADING_ENABLED is not False:
        raise LiveTradingNotAllowed(
            "LIVE_TRADING_ENABLED was changed. quantbot is paper-only; there is no live adapter. "
            "See quantbot/broker/live_gate.py for the manual steps a live deployment would require.")
    for name in _SUSPECT_ENV:
        val = os.environ.get(name)
        if name == "ALPACA_PAPER":
            # ALPACA_PAPER=false would mean "live" in some tools; refuse it explicitly.
            if val is not None and val.strip().lower() in ("0", "false", "no", "off"):
                raise LiveTradingNotAllowed("ALPACA_PAPER is set to false; quantbot only trades paper")
            continue
        if _truthy(val):
            raise LiveTradingNotAllowed(f"{name} is set; quantbot is paper-only and has no live mode")
