# Robinhood daily run (agentic crypto account)

A scheduled Claude session follows this once a day. **The code decides the trades; the
session only gathers inputs and places exactly the orders `rh-plan` prints.**

- Account: the agentic account (nickname "MCP"); `rhs_account_number` = `746480458`.
- Coins traded: BTC, ETH, SOL only. Signals come from the IBIT / ETHA / BSOL ETF history in
  `data/proxy_history.csv`, set in `config.robinhood.toml`.
- Never trade any other coin. (The old XLM/HBAR positions were sold on 2026-10-15; if any other
  holding or open order shows up, leave it alone.)
- To stop: set `kill_switch = true` in `config.robinhood.toml`, turn trade approvals on in the
  Robinhood app, or disable the scheduled task.

## Steps

1. `git fetch origin claude/jolly-fermi-tvlmpb && git checkout claude/jolly-fermi-tvlmpb && git pull`
   then `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt`.
2. Gather the account state with the Robinhood connector:
   - `get_portfolio(account_number="746480458")` → `crypto_buying_power.buying_power`
   - `get_crypto_positions(rhs_account_number="746480458")` → BTC / ETH / SOL `quantity` and `quantity_transferable`
   - `get_crypto_quotes(["BTC-USD","ETH-USD","SOL-USD"])` → `mark_price`
   - `get_crypto_orders(rhs_account_number="746480458", state_group="open")` → `currency_code` of each open order
3. Write `state.json` (outside the repo):
   `{"crypto_buying_power": ..., "positions": {"BTC": {"quantity": "...", "quantity_transferable": "..."}, ...},
     "marks": {"BTC": ..., "ETH": ..., "SOL": ...}, "open_order_symbols": [...]}`
4. Fetch recent ETF bars: `get_equity_historicals(symbols=["IBIT","ETHA","BSOL"], interval="day",
   start_time=<last date in data/proxy_history.csv minus 10 days>)`. Save the result JSON exactly as
   returned to `bars.json` (outside the repo).
5. `python -m trader rh-plan --state state.json --bars bars.json` (this also updates the history CSV).
6. For each entry in `orders`, in order: `preview_crypto_order` with exactly its symbol, side, type and
   `dollar_amount` or `quantity`, then `place_crypto_order` with the same values and a fresh UUID `ref_id`.
   If a preview fails, skip that order and record the error. Never add, resize or invent orders.
7. Check fills with `get_crypto_orders(created_at_gte=<run start>)`.
8. Append a dated entry to `journal/robinhood-log.md` (equity, target, orders + fill prices, notes,
   errors), commit `data/proxy_history.csv` and the journal, and push to `claude/jolly-fermi-tvlmpb`.
