# quantbot troubleshooting

Start with `python -m quantbot status --config <cfg>`. It shows the kill switch, each breaker,
the last account snapshot, positions, open orders, heartbeats and recent events. Logs are JSON
lines on stderr; secrets are redacted.

| Symptom | Likely cause | What to do |
|---|---|---|
| `verify-assets` / `paper-*` says credentials are missing | `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` not exported | `set -a; . ./.env; set +a`, then retry. Use **paper** keys. |
| Connection errors / 403 from Alpaca | network blocked (firewall, sandbox egress policy) or wrong keys | Check you can reach `paper-api.alpaca.markets` and `data.alpaca.markets`. In Claude Code cloud sessions, add both under the environment's Network access → Allowed domains. Regenerate paper keys if 401/403 persists. |
| `verify-assets` lists a symbol as not tradable | Alpaca doesn't offer it, or it's halted | Set `enabled = false` for that symbol. |
| Runner places no orders, status says `halted` | kill switch engaged (store flag **or** `state/KILL` file) | Find out why first (events list). Then `python -m quantbot unkill` and delete `state/KILL` if present. |
| Entries blocked, exits still happen | a breaker is active | Daily clears the next New York day, weekly the next ISO week. Drawdown only clears with `reset-drawdown`, and only after you've reviewed why. |
| A symbol is always skipped with `stale` / `gap` / `invalid` | data feed lagging, holiday, or bad bars | Check the latest bar time. Staleness limits are `data.stock_stale_after_hours` (120) and `data.crypto_stale_after_hours` (50), measured from the bar's open time. Don't loosen them to force a trade. |
| `order ... already exists for this bar` | duplicate protection: an order for this strategy, symbol and bar was already recorded | Expected on reruns. A rejected order can't be retried until the next bar. |
| Order stuck `pending` | process died between recording and the broker's answer | The next cycle looks it up by `client_order_id`. If the broker never received it, it's marked expired after `order_timeout_seconds`. |
| Orders partially filled for a long time | thin market | The runner cancels after `order_timeout_seconds` and keeps the filled part with its stop. |
| Position in the broker but not in the dashboard's strategy column | placed manually or by another tool | The bot doesn't manage positions it didn't open (no metadata). Close them yourself or leave them alone. |
| Dashboard shows "no data yet" | wrong `--config`/db path, or the runner hasn't run | Start the dashboard from the repo root with the same config as the runner. |
| Dashboard health says `stale` | runner not running, crashed, or blocked | Check the process and its logs. Heartbeats older than max(2×poll, 15 min) show as stale. |
| Webhook alerts not arriving | `QUANTBOT_ALERT_WEBHOOK` unset, below `min_level`, or deduplicated | The same alert is sent at most once per 30 minutes. Every alert is in the events list regardless. |
| `database is locked` | two runners on one database | Run exactly one runner per database. The dashboard may share it. |
| Backtest says too little history | symbol newer than the strategy warm-up (e.g. 200 bars) | Expected for new listings. |
| Paper fills look better than expected | paper fills ignore queue position and market impact | Compare paper with backtest costs; raise the cost assumptions in `[costs]` if needed. |

## Recovering after a crash

1. Restart the same command. Each cycle reconciles with the broker first, and order ids are
   deterministic, so it won't duplicate orders.
2. Run `status` and compare positions with the Alpaca paper dashboard. If they differ, the broker
   is the source of truth. Check the events list for `order_lost`, `order_submit_unknown` and `unmanaged_position` entries.
3. If anything looks wrong, engage the kill switch first, then investigate.

## Refreshing research data

Without Alpaca data access, save a Robinhood `get_equity_historicals` (interval `day`) answer to a
file and run `python scripts/import_robinhood_bars.py <file> --out data/bars`. With Alpaca access,
use `quantbot.data.sources.AlpacaBarSource` and `write_bars_csv` (it records the source).
