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

    args = parser.parse_args(argv)
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


if __name__ == "__main__":
    main()
