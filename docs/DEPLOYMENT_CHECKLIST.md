# quantbot deployment and validation checklist

Paper trading only. Tick each item, with the evidence, before leaving the bot running unattended.

## A. Build verification (done in the build sandbox, 2026-10-15)

- [x] `python -m pytest tests` → 236 passed, 0 failed (Python 3.13.16; pins in `requirements-quantbot.txt`)
- [x] No-look-ahead tests for every strategy, indicator and the regime classifier (recompute on truncated histories)
- [x] Config validation forbids live mode, margin, shorting and derivatives
- [x] `paper-once` in sim mode: entries sized at 0.5% risk; a second run in the same bar placed no orders; the kill switch halted all orders
- [x] Dashboard server starts (`/_stcore/health` = ok); AppTest covers empty and populated databases and the emergency stop
- [x] Backtest and walk-forward reports generated from real Robinhood daily bars (`reports/`)
- [ ] **Not done: anything against Alpaca.** The sandbox's network policy blocked `paper-api.alpaca.markets` and `data.alpaca.markets`. The Alpaca adapter is tested only against fakes of the SDK client.

## B. First connection to Alpaca paper (needs your paper keys, on a machine that can reach Alpaca)

- [ ] `.env` holds **paper** keys; `.env` is not committed (`git check-ignore .env`)
- [ ] `python -m quantbot verify-assets --config configs/local.toml` → every enabled symbol tradable; disable any that aren't
- [ ] Confirm against Alpaca's current docs: crypto fee tier (config assumes 25 bps taker), minimum order sizes, fractional rules for each stock
- [ ] Re-run `backtest` and `walkforward` with Alpaca bars (real BTC/USD and ETH/USD history instead of the ETF proxies)
- [ ] Set `execution.mode = "alpaca_paper"` in `configs/local.toml`
- [ ] `paper-once` during market hours → orders appear in the Alpaca paper dashboard with matching `client_order_id`s
- [ ] Run `paper-once` again for the same bar → no duplicate orders
- [ ] Kill the process mid-cycle, restart → reconciliation shows no duplicates and matching positions

## C. Controls drill (paper account)

- [ ] Dashboard EMERGENCY STOP → the next cycle places no orders; `unkill` resumes
- [ ] `touch state/KILL` → halts; deleting it plus `unkill` resumes
- [ ] Lower `daily_loss_limit` on a test config to force a trip → entries blocked, exits allowed, critical alert received
- [ ] Webhook alert received (set `QUANTBOT_ALERT_WEBHOOK`)
- [ ] Unplug the network during a cycle → the cycle aborts safely, an alert fires, the next cycle recovers

## D. Running unattended

- [ ] One `paper-loop` process per database, under a supervisor that restarts it (systemd, launchd, Docker `restart: unless-stopped`)
- [ ] Working directory is the repo root (or `QUANTBOT_KILL_FILE` set to an absolute path)
- [ ] Dashboard reachable only by you (don't expose Streamlit to the internet without auth)
- [ ] Back up `state/quantbot.sqlite3` daily

## E. Evidence needed before even discussing real money

- [ ] At least 3 months of paper trading
- [ ] Paper results within the range the walk-forward predicted, after costs
- [ ] A fresh walk-forward that includes symbols chosen *without* hindsight (survivorship check)
- [ ] Every breaker trip and incident reviewed and written up

## F. Live trading: deliberately not built

A live deployment would be a separate, reviewed change, never a config switch:
- a new `LiveBroker` class in a separate branch, with code review
- explicit written operator sign-off and small hard capital limits
- `live_gate.py` changed in that reviewed change, never toggled by environment or config
- the bot never switches itself from paper to live
