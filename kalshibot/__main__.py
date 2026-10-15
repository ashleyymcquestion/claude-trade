"""kalshibot command line.

    python -m kalshibot markets  [--config C]              # show the current 15-minute markets
    python -m kalshibot balance  [--config C]              # check your API key works
    python -m kalshibot once     [--config C] [--dry-run] [--live]
    python -m kalshibot run      [--config C] [--dry-run] [--live] [--max-iterations N]
    python -m kalshibot status   [--config C]              # trades, win rate, P&L after fees

Defaults to the demo (fake-money) exchange. See docs/KALSHI.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .bot import Bot
from .client import KalshiClient
from .config import KalshiConfig, credentials_from_env, load_config
from .pricefeed import CoinbaseFeed
from .risk import LiveTradingNotAllowed, assert_live_allowed, kill_reason
from .store import Store, now_utc

DEFAULT_CONFIG = "configs/kalshi.toml"


def _config(args) -> KalshiConfig:
    cfg = load_config(args.config)
    if getattr(args, "dry_run", False):
        cfg = cfg.model_copy(update={"run": cfg.run.model_copy(update={"dry_run": True})})
    return cfg


def _client(cfg: KalshiConfig, need_auth: bool) -> KalshiClient:
    creds = credentials_from_env(cfg.run.environment)
    if need_auth and creds is None:
        env = cfg.run.environment.upper()
        sys.exit(f"No API key for the {cfg.run.environment} exchange. Set KALSHI_{env}_KEY_ID and "
                 f"KALSHI_{env}_PRIVATE_KEY_PATH (see docs/KALSHI.md), or use --dry-run.")
    return KalshiClient(cfg.base_url, creds)


def _banner(cfg: KalshiConfig) -> None:
    mode = "DRY RUN (no orders)" if cfg.run.dry_run else (
        "DEMO exchange (fake money)" if cfg.run.environment == "demo" else "PROD exchange (REAL MONEY)")
    print(f"kalshibot: {mode}; series {cfg.strategy.series_ticker}", file=sys.stderr)


def _bot(args) -> Bot:
    cfg = _config(args)
    try:
        assert_live_allowed(cfg, args.live)
    except LiveTradingNotAllowed as e:
        sys.exit(str(e))
    _banner(cfg)
    client = _client(cfg, need_auth=not cfg.run.dry_run)
    return Bot(cfg, client, CoinbaseFeed(cfg.run.btc_product), Store(cfg.run.db_path))


def cmd_markets(args) -> None:
    cfg = _config(args)
    now = now_utc()
    for m in sorted(KalshiClient(cfg.base_url).open_markets(cfg.strategy.series_ticker),
                    key=lambda m: m.close_time):
        left = (m.close_time - now).total_seconds()
        print(f"{m.ticker}  closes in {left:5.0f}s  target {m.strike or '?'}  "
              f"YES ask {m.yes_ask}c  NO ask {m.no_ask}c")


def cmd_balance(args) -> None:
    cfg = _config(args)
    print(f"{cfg.run.environment} balance: ${_client(cfg, need_auth=True).balance_cents() / 100:.2f}")


def cmd_once(args) -> None:
    print(json.dumps(_bot(args).tick(), default=str, indent=2))


def cmd_run(args) -> None:
    _bot(args).run_forever(max_iterations=args.max_iterations)


def cmd_status(args) -> None:
    cfg = _config(args)
    store = Store(cfg.run.db_path)
    s = store.summary()
    print(json.dumps({"kill_switch": kill_reason(), **s}, indent=2))
    for t in store.recent_trades(10):
        pnl = "open" if t["pnl_cents"] is None else f"${t['pnl_cents'] / 100:+.2f}"
        dry = " (dry run)" if t["dry_run"] else ""
        print(f"{t['created_at'][:19]}  {t['ticker']}  {t['count']}x {t['side'].upper()} "
              f"@ {t['price_cents']}c  {pnl}{dry}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="kalshibot", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("markets", cmd_markets), ("balance", cmd_balance), ("once", cmd_once),
                     ("run", cmd_run), ("status", cmd_status)):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default=DEFAULT_CONFIG)
        sp.set_defaults(fn=fn)
        if name in ("once", "run"):
            sp.add_argument("--dry-run", action="store_true", help="log decisions, send no orders")
            sp.add_argument("--live", action="store_true", help="required for real-money orders")
        if name == "run":
            sp.add_argument("--max-iterations", type=int, default=None)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args.fn(args)


if __name__ == "__main__":
    main()
