"""Position sizing shared by the backtester and the live runner, so research and paper trading
size trades identically."""

from __future__ import annotations

import math

from ..config import RiskConfig
from ..models import AssetInfo, RiskDecision


def _round_down(qty: float, step: float) -> float:
    if step <= 0:
        return qty
    return math.floor(qty / step + 1e-9) * step


def size_position(
    *,
    equity: float,
    cash: float,
    price: float,
    stop_price: float | None,
    asset: AssetInfo,
    current_gross: float,
    current_symbol_exposure: float,
    risk: RiskConfig,
    one_way_cost_bps: float,
) -> RiskDecision:
    """Largest long quantity that satisfies every constraint, or a rejection with the reason.

    Constraints, all applied:
      * risk: (price - stop + round-trip costs) * qty <= risk_per_trade * equity
      * single position notional <= max_position_pct * equity (including what is already held)
      * total exposure <= max_gross_pct * equity
      * cash: no margin, cost of the order incl. fees <= cash
      * broker minimums: min notional, min order size, unit step, whole shares if not fractionable
    """
    if not asset.tradable:
        return RiskDecision(False, 0.0, f"{asset.symbol} is not tradable")
    if equity <= 0 or price <= 0 or not math.isfinite(price):
        return RiskDecision(False, 0.0, "invalid equity or price")
    if stop_price is None or not math.isfinite(stop_price) or stop_price >= price:
        return RiskDecision(False, 0.0, "a protective stop below the entry price is required")

    cost_frac = one_way_cost_bps / 10_000
    per_unit_risk = (price - stop_price) + 2 * cost_frac * price   # stop loss plus round-trip costs
    qty_risk = risk.risk_per_trade * equity / per_unit_risk

    qty_position = max(0.0, risk.max_position_pct * equity - current_symbol_exposure) / price
    qty_gross = max(0.0, risk.max_gross_pct * equity - current_gross) / price
    qty_cash = max(0.0, cash) / (price * (1 + cost_frac))

    limits = {"risk": qty_risk, "position cap": qty_position, "exposure cap": qty_gross, "cash": qty_cash}
    binding = min(limits, key=limits.get)
    qty = limits[binding]

    step = asset.min_trade_increment if asset.fractionable else 1.0
    qty = _round_down(qty, step) if step > 0 else qty
    if not asset.fractionable:
        qty = float(math.floor(qty + 1e-9))

    if qty <= 0:
        return RiskDecision(False, 0.0, f"no size fits ({binding} limit)")
    if qty * price < max(risk.min_notional_usd, asset.min_notional):
        return RiskDecision(False, 0.0, f"${qty * price:.2f} is below the minimum order (binding: {binding})")
    if asset.min_order_size and qty < asset.min_order_size:
        return RiskDecision(False, 0.0, f"{qty} is below the minimum order size {asset.min_order_size}")
    return RiskDecision(True, qty, f"sized by {binding}")
