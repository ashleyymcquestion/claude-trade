"""Order planner for the Robinhood agentic crypto account.

Robinhood is reached through Claude's Robinhood connector, not an API key, so
this module never talks to Robinhood. A scheduled Claude session gathers the
inputs with the connector, runs `python -m trader rh-plan`, and places exactly
the orders it prints. See ROBINHOOD.md.

Inputs
  history CSV   daily closes of the proxy ETFs (data/proxy_history.csv)
  --bars JSON   optional raw get_equity_historicals result, merged into history
  --state JSON  {"crypto_buying_power": 1.0, "account_value": 4.19 (needed when crypto_sleeve < 1),
                 "positions": {"BTC": {"quantity": "0.0001", "quantity_transferable": "0.0001"}},
                 "marks": {"BTC": 82377.69, "ETH": 2446.05, "SOL": 106.01},
                 "open_order_symbols": ["XLM"]}
"""

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd

from .config import Config
from .data import MAX_FFILL_DAYS
from .live import plan_orders
from .strategy import latest_target


def bars_to_closes(raw: dict) -> pd.DataFrame:
    """Raw get_equity_historicals JSON -> one close column per ETF symbol."""
    cols = {}
    for result in raw["data"]["results"]:
        bars = [b for b in result["bars"] if not b.get("interpolated")]
        cols[result["symbol"]] = pd.Series(
            [float(b["close_price"]) for b in bars],
            index=pd.to_datetime([b["begins_at"] for b in bars]).tz_localize(None).normalize())
    return pd.DataFrame(cols).sort_index()


def merge_history(history: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    merged = pd.concat([history, new]).sort_index()
    return merged[~merged.index.duplicated(keep="last")]


def load_history(path: str | Path) -> pd.DataFrame:
    if not Path(path).exists():
        return pd.DataFrame()
    return pd.read_csv(path, index_col=0, parse_dates=True)


def coin_prices(etf_closes: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """ETF closes renamed to coins, on a 7-day calendar (ETF prices held over weekends)."""
    missing = [etf for etf in cfg.proxies.values() if etf not in etf_closes]
    if missing:
        raise ValueError(f"History is missing proxy ETFs: {missing}")
    prices = etf_closes[[cfg.proxies[c] for c in cfg.crypto]]
    prices.columns = list(cfg.crypto)
    return prices.asfreq("D").ffill(limit=MAX_FFILL_DAYS)


def plan(prices: pd.DataFrame, state: dict, cfg: Config) -> dict:
    marks = {s: float(state["marks"][s]) for s in cfg.crypto}
    positions = state.get("positions", {})
    holdings = {s: float(positions.get(s, {}).get("quantity", 0)) * marks[s] for s in cfg.crypto}
    cash = float(state["crypto_buying_power"])
    equity = cash + sum(holdings.values())
    sleeve_note = None
    if cfg.crypto_sleeve < 1.0:
        # Shared account: manage only this strategy's share of the whole account's value.
        if "account_value" not in state:
            raise ValueError("crypto_sleeve < 1 needs account_value (get_portfolio total_value) in the state")
        budget = cfg.crypto_sleeve * float(state["account_value"])
        equity = min(budget, equity)
        cash = max(0.0, min(cash, equity - sum(holdings.values())))
        sleeve_note = f"sleeve {cfg.crypto_sleeve:.0%} of account ${float(state['account_value']):.2f} = ${budget:.2f}"

    target = latest_target(prices, cfg)
    result = {
        "equity": round(equity, 2), "cash": round(cash, 2), "prices_as_of": str(prices.index[-1].date()),
        "target": {s: round(float(w), 4) for s, w in target.items()},
        "holdings_usd": {s: round(v, 2) for s, v in holdings.items()},
        "orders": [], "notes": [sleeve_note] if sleeve_note else [],
    }
    if cfg.kill_switch:
        result["notes"].append("kill switch on: no orders")
        return result
    if equity <= 0:
        result["notes"].append("account has no strategy equity")
        return result

    busy = {s for s in cfg.crypto if s in set(state.get("open_order_symbols", []))}
    # Small accounts: a buy just under Robinhood's minimum is rounded up to it when
    # the result stays within the rebalance band of the target and cash allows.
    slack = cfg.rebalance_band * equity
    loose = replace(cfg, min_order_usd=max(cfg.min_order_usd - slack, 0.01))
    orders, notes = plan_orders(target, holdings, equity, cash, loose, market_open=True, busy=busy)
    result["notes"] += notes
    spend = sum(o.notional for o in orders if o.side == "buy")
    kept = []
    for o in orders:
        if o.notional < cfg.min_order_usd:
            if o.side == "buy" and spend - o.notional + cfg.min_order_usd <= cash:
                result["notes"].append(f"{o.symbol}: ${o.notional:.2f} rounded up to the ${cfg.min_order_usd:.2f} minimum")
                spend += cfg.min_order_usd - o.notional
                o.notional = cfg.min_order_usd
            else:
                result["notes"].append(f"{o.symbol}: ${o.notional:.2f} is below the ${cfg.min_order_usd:.2f} minimum, skipped")
                continue
        kept.append(o)
    orders = kept

    for o in orders:
        if o.notional > cfg.max_order_usd:
            result["notes"].append(f"{o.symbol}: ${o.notional:,.2f} capped at max_order_usd ${cfg.max_order_usd:,.2f}")
            o.notional = cfg.max_order_usd
            o.close_all = False
        order = {"symbol": o.symbol, "side": o.side, "type": "market", "reason": o.reason}
        if o.close_all:
            order["quantity"] = str(positions[o.symbol]["quantity_transferable"])
        else:
            order["dollar_amount"] = f"{o.notional:.2f}"
        result["orders"].append(order)
    return result


def run(cfg: Config, history_path: str, state_path: str, bars_path: str | None = None) -> dict:
    history = load_history(history_path)
    if bars_path:
        history = merge_history(history, bars_to_closes(json.loads(Path(bars_path).read_text())))
        Path(history_path).parent.mkdir(parents=True, exist_ok=True)
        history.to_csv(history_path)
    state = json.loads(Path(state_path).read_text())
    return plan(coin_prices(history, cfg), state, cfg)
