"""Rebalances an Alpaca PAPER account toward the strategy's target weights.

The trading client is always created with paper=True, so live-money keys are
rejected by Alpaca's paper endpoint. There is no code path to a live account.
"""

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import Config
from .data import fetch_recent
from .strategy import latest_target


@dataclass
class PlannedOrder:
    symbol: str
    side: str            # "buy" | "sell"
    notional: float      # USD, always positive
    close_all: bool = False
    reason: str = ""


def broker_symbol(symbol: str) -> str:
    return symbol.replace("/", "")  # Alpaca reports crypto positions as BTCUSD


def plan_orders(target: pd.Series, holdings: dict[str, float], equity: float, cash: float,
                cfg: Config, market_open: bool, busy: set[str] = frozenset()) -> tuple[list[PlannedOrder], list[str]]:
    """Pure function: turn target weights + current holdings into orders.

    holdings maps strategy symbol -> current market value in USD.
    Returns (orders, notes). Sells come first so they free cash for buys.
    """
    sells, buys, notes = [], [], []
    for symbol in cfg.symbols:
        w = float(target.get(symbol, 0.0) or 0.0)
        current = holdings.get(symbol, 0.0)
        diff = w * equity - current
        exiting = w == 0 and current > 0

        if symbol in busy:
            notes.append(f"{symbol}: open order pending, skipped")
            continue
        if not cfg.is_crypto(symbol) and not market_open:
            if abs(diff) / equity > cfg.rebalance_band or exiting:
                notes.append(f"{symbol}: stock market closed, rebalance waits")
            continue
        if abs(diff) / equity <= cfg.rebalance_band and not exiting:
            continue
        if abs(diff) < cfg.min_order_usd:
            continue

        reason = f"target {w:.1%} vs current {current / equity:.1%}"
        if diff < 0:
            sells.append(PlannedOrder(symbol, "sell", round(-diff, 2), close_all=exiting, reason=reason))
        else:
            buys.append(PlannedOrder(symbol, "buy", round(diff, 2), reason=reason))

    # Never spend more cash than we have (plus what sells should free up).
    budget = max(cash, 0.0) + 0.98 * sum(o.notional for o in sells)
    wanted = sum(o.notional for o in buys)
    if wanted > budget > 0:
        scale = budget / wanted
        notes.append(f"buys scaled to {scale:.0%} to fit available cash")
        for o in buys:
            o.notional = math.floor(o.notional * scale * 100) / 100
    elif wanted > 0 and budget <= 0:
        notes.append("no cash available, buys skipped")
        buys = []
    buys = [o for o in buys if o.notional >= cfg.min_order_usd]
    return sells + buys, notes


def daily_loss_breached(equity: float, last_equity: float, cfg: Config) -> bool:
    return last_equity > 0 and equity / last_equity - 1 <= -cfg.max_daily_loss


class PaperTrader:
    def __init__(self, cfg: Config, api_key: str, secret_key: str):
        from alpaca.trading.client import TradingClient

        self.cfg = cfg
        self.api_key, self.secret_key = api_key, secret_key
        self.client = TradingClient(api_key, secret_key, paper=True)

    def _holdings(self) -> tuple[dict[str, float], dict[str, float]]:
        by_broker = {broker_symbol(s): s for s in self.cfg.symbols}
        values, qty = {}, {}
        for p in self.client.get_all_positions():
            symbol = by_broker.get(p.symbol)
            if symbol is None:
                continue  # positions outside the universe are left alone
            values[symbol] = float(p.market_value)
            qty[symbol] = float(p.qty_available or p.qty)
        return values, qty

    def _busy_symbols(self) -> set[str]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        by_broker = {broker_symbol(s): s for s in self.cfg.symbols}
        orders = self.client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return {by_broker.get(broker_symbol(o.symbol), o.symbol) for o in orders}

    def _submit(self, order: PlannedOrder, qty_available: dict[str, float], prices: pd.Series):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        if order.close_all:
            return self.client.close_position(broker_symbol(order.symbol))

        crypto = self.cfg.is_crypto(order.symbol)
        tif = TimeInForce.GTC if crypto else TimeInForce.DAY
        side = OrderSide.BUY if order.side == "buy" else OrderSide.SELL
        asset = self.client.get_asset(broker_symbol(order.symbol))

        if order.side == "buy" and (crypto or asset.fractionable):
            req = MarketOrderRequest(symbol=order.symbol, notional=order.notional, side=side, time_in_force=tif)
        else:
            qty = order.notional / float(prices[order.symbol])
            if order.side == "sell":
                qty = min(qty, qty_available.get(order.symbol, 0.0))
            if crypto or asset.fractionable:
                step = float(asset.min_trade_increment or 1e-9)
                qty = math.floor(qty / step) * step
            else:
                qty = math.floor(qty)
            if qty <= 0:
                return None
            req = MarketOrderRequest(symbol=order.symbol, qty=round(qty, 9), side=side, time_in_force=tif)
        return self.client.submit_order(req)

    def run(self, dry_run: bool = False) -> dict:
        cfg = self.cfg
        account = self.client.get_account()
        equity, last_equity = float(account.equity), float(account.last_equity)
        summary = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dry_run": dry_run, "equity": equity, "last_equity": last_equity,
            "orders": [], "notes": [], "errors": [], "status": "ok",
        }

        if cfg.kill_switch or os.environ.get("KILL_SWITCH", "").lower() in ("1", "true", "yes"):
            summary["status"] = "kill switch on: no orders placed"
            return summary
        if account.trading_blocked or account.account_blocked:
            summary["status"] = "account blocked by Alpaca: no orders placed"
            return summary

        holdings, qty_available = self._holdings()
        summary["holdings"] = {s: round(v, 2) for s, v in holdings.items()}

        if daily_loss_breached(equity, last_equity, cfg):
            summary["status"] = f"daily loss limit ({cfg.max_daily_loss:.0%}) hit: {cfg.on_daily_loss}"
            if cfg.on_daily_loss == "flatten" and not dry_run:
                self.client.cancel_orders()
                for symbol in holdings:
                    self.client.close_position(broker_symbol(symbol))
                    summary["orders"].append({"symbol": symbol, "side": "sell", "close_all": True})
            return summary

        prices = fetch_recent(cfg, self.api_key, self.secret_key)
        target = latest_target(prices, cfg)
        summary["target"] = {s: round(float(w), 4) for s, w in target.items()}
        summary["prices_as_of"] = str(prices.index[-1].date())

        clock = self.client.get_clock()
        cash = min(float(account.cash), float(account.non_marginable_buying_power or account.cash))
        orders, notes = plan_orders(target, holdings, equity, cash, cfg,
                                    market_open=clock.is_open, busy=self._busy_symbols())
        summary["notes"] = notes

        last_prices = prices.ffill().iloc[-1]
        for order in orders:
            entry = asdict(order)
            if not dry_run:
                try:
                    result = self._submit(order, qty_available, last_prices)
                    entry["order_id"] = str(getattr(result, "id", "")) if result else "skipped (qty rounds to 0)"
                except Exception as e:  # keep going so one bad order doesn't block the rest
                    entry["error"] = str(e)
                    summary["errors"].append(f"{order.symbol}: {e}")
            summary["orders"].append(entry)
        return summary


def write_summary(summary: dict, log_dir: str = "logs") -> str:
    Path(log_dir).mkdir(exist_ok=True)
    stamp = summary["time"].replace(":", "-")
    path = Path(log_dir) / f"run-{stamp}.json"
    path.write_text(json.dumps(summary, indent=2))

    lines = [
        f"## Paper trading run {summary['time']}" + (" (dry run)" if summary["dry_run"] else ""),
        f"**Status:** {summary['status']}  ",
        f"**Equity:** ${summary['equity']:,.2f} (yesterday ${summary['last_equity']:,.2f})",
        "",
    ]
    if summary.get("target"):
        lines += ["| Symbol | Target | Holding |", "|---|---|---|"]
        for s, w in summary["target"].items():
            held = summary.get("holdings", {}).get(s, 0.0)
            lines.append(f"| {s} | {w:.1%} | ${held:,.2f} |")
        lines.append("")
    if summary["orders"]:
        lines += ["**Orders:**", ""]
        lines += [f"- {o['side']} {o['symbol']} ${o.get('notional', 0):,.2f} {o.get('error', '')}"
                  for o in summary["orders"]]
    else:
        lines.append("No orders this run.")
    lines += [f"- note: {n}" for n in summary["notes"]]
    lines += [f"- ERROR: {e}" for e in summary["errors"]]
    markdown = "\n".join(lines)

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(markdown + "\n")
    return markdown
