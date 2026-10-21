"""CLI: python -m trader backtest | trade"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .backtest import report, run_backtest
from .config import DEFAULT_PATH, load_config
from .data import fetch_closes, load_csv


def _keys() -> tuple[str, str]:
    key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        sys.exit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY (paper trading keys).")
    return key, secret


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trader")
    parser.add_argument("--config", default=str(DEFAULT_PATH))
    sub = parser.add_subparsers(dest="cmd", required=True)

    bt = sub.add_parser("backtest", help="test the strategy on historical data")
    bt.add_argument("--start", default="2018-01-01")
    bt.add_argument("--csv", help="use a local CSV of daily closes instead of Alpaca data")
    bt.add_argument("--out", default="output", help="folder for the equity curve CSV")

    tr = sub.add_parser("trade", help="rebalance the Alpaca PAPER account once")
    tr.add_argument("--dry-run", action="store_true", help="plan orders without sending them")

    rh = sub.add_parser("rh-plan", help="plan orders for the Robinhood crypto account (prints JSON)")
    rh.add_argument("--history", default="data/proxy_history.csv")
    rh.add_argument("--bars", help="raw get_equity_historicals JSON to merge into the history")
    rh.add_argument("--state", required=True, help="account state JSON (see trader/robinhood.py)")

    sp = sub.add_parser("rh-spcx-plan", help="SPCX test position: print the order to place, if any")
    sp.add_argument("--state", required=True, help="SPCX state JSON (see trader/spcx_test.py)")
    sr = sub.add_parser("rh-spcx-record", help="SPCX test position: record a filled order")
    sr.add_argument("--side", required=True, choices=["buy", "sell"])
    sr.add_argument("--order-id", required=True)
    sr.add_argument("--quantity", required=True)
    sr.add_argument("--price", required=True, help="the order's average_price")
    sr.add_argument("--at", required=True, help="fill time (ISO)")

    wd = sub.add_parser("watch-due", help="watchlab: is today's paper-test research run due now?")
    wd.add_argument("--now", help="ISO time (default: now)")
    wn = sub.add_parser("watch-needs", help="watchlab: symbols to quote before watch-run")
    wn.add_argument("--input", required=True, help="ideas JSON (see trader/watchlab.py)")
    wr = sub.add_parser("watch-run", help="watchlab: open/close paper trades and print the scoreboard")
    wr.add_argument("--input", required=True, help="ideas JSON with quotes (see trader/watchlab.py)")
    sub.add_parser("watch-score", help="watchlab: print the scoreboard")

    os_ = sub.add_parser("opt-step", help="options lab (paper): print what to fetch next, or record the day")
    os_.add_argument("--input", required=True, help="options lab JSON (see trader/optionslab.py)")
    sub.add_parser("opt-score", help="options lab: print the scoreboard")

    args = parser.parse_args(argv)
    if args.cmd.startswith("opt-"):
        import json

        from . import optionslab, watchlab

        positions, state = optionslab.load()
        if args.cmd == "opt-score":
            print(json.dumps(optionslab.scoreboard(positions, state, {}), indent=2))
            return
        result, positions, state = optionslab.step(json.loads(Path(args.input).read_text()), positions, state,
                                                   watchlab.load_trades())
        if "done" in result and not result["done"].get("already_ran"):
            optionslab.save(positions, state)
        print(json.dumps(result, indent=2))
        return
    if args.cmd.startswith("watch-"):
        import json

        from . import watchlab

        trades, state = watchlab.load_trades(), watchlab.load_state()
        if args.cmd == "watch-due":
            print(json.dumps({"due": watchlab.due(state, watchlab._now({"now": args.now}))}))
        elif args.cmd == "watch-needs":
            print(json.dumps(watchlab.needs(json.loads(Path(args.input).read_text()), trades, state)))
        elif args.cmd == "watch-run":
            trades, state, rep = watchlab.run(json.loads(Path(args.input).read_text()), trades, state)
            watchlab.save_trades(trades)
            watchlab.STATE.write_text(json.dumps(state, indent=1) + "\n")
            print(json.dumps({**rep, "scoreboard": watchlab.scoreboard(trades)}, indent=2))
        else:
            print(json.dumps(watchlab.scoreboard(trades), indent=2))
        return
    if args.cmd in ("rh-spcx-plan", "rh-spcx-record"):
        import json

        from . import spcx_test

        ledger = spcx_test.load_ledger()
        if args.cmd == "rh-spcx-plan":
            print(json.dumps(spcx_test.plan(ledger, json.loads(Path(args.state).read_text())), indent=2))
        else:
            ledger = spcx_test.record(ledger, args.side, args.order_id, args.quantity, args.price, args.at)
            spcx_test.LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")
            print(json.dumps(ledger, indent=2))
        return

    if args.cmd == "rh-plan" and args.config == str(DEFAULT_PATH):
        args.config = str(DEFAULT_PATH.with_name("config.robinhood.toml"))
    cfg = load_config(args.config)

    if args.cmd == "backtest":
        if args.csv:
            prices = load_csv(args.csv)[list(cfg.symbols)]
        else:
            start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
            prices = fetch_closes(cfg.stocks, cfg.crypto, start, *_keys())
        result = run_backtest(prices, cfg)
        print(report(result, cfg))
        Path(args.out).mkdir(exist_ok=True)
        result.equity.to_frame().join(result.benchmark).to_csv(Path(args.out) / "equity.csv")

    elif args.cmd == "trade":
        from .live import PaperTrader, write_summary

        summary = PaperTrader(cfg, *_keys()).run(dry_run=args.dry_run)
        print(write_summary(summary))
        if summary["errors"]:
            sys.exit(1)

    elif args.cmd == "rh-plan":
        import json

        from .robinhood import run

        print(json.dumps(run(cfg, args.history, args.state, args.bars), indent=2))


if __name__ == "__main__":
    main()
