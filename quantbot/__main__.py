"""quantbot command line (paper only).

    python -m quantbot backtest      [--config C] [--out reports] [--strategies trend breakout]
    python -m quantbot walkforward   [--config C] [--out reports]
    python -m quantbot verify-assets [--config C]
    python -m quantbot paper-once    [--config C] [--db PATH]
    python -m quantbot paper-loop    [--config C] [--db PATH] [--max-iterations N]
    python -m quantbot status        [--config C] [--db PATH]
    python -m quantbot kill --reason "..." / unkill / reset-drawdown

There is no live mode. ``execution.mode`` is "sim" (dry run against local CSV bars, no live prices)
or "alpaca_paper" (Alpaca paper account). Credentials only come from the environment.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .broker.live_gate import assert_live_trading_not_enabled
from .config import AppConfig, Credentials, load_config
from .logging_setup import setup_logging

SIM_NOTICE = ("NOTICE: execution.mode = 'sim'. This is a DRY RUN with the in-memory SimulatedBroker: "
              "prices are the last close of the local CSV bars, there are NO live prices, and the "
              "simulated account resets on every invocation. Nothing here is a real or paper fill.")


def _default_config() -> str:
    env = os.environ.get("QUANTBOT_CONFIG")
    if env:
        return env
    return "configs/local.toml" if Path("configs/local.toml").exists() else "configs/example.toml"


def _who() -> str:
    try:
        return f"cli:{getpass.getuser()}"
    except Exception:
        return "cli"


def _load(args) -> AppConfig:
    return load_config(args.config)


def _db_path(args, cfg: AppConfig) -> str:
    return args.db or cfg.execution.db_path


def _open_store(args, cfg: AppConfig):
    from .store import Store
    return Store(_db_path(args, cfg))


def _risk(cfg: AppConfig, store):
    from .risk.killswitch import KillSwitch
    from .risk.limits import RiskManager
    return RiskManager(cfg.risk, store, KillSwitch(store), initial_capital=cfg.initial_capital)


def _alerter(cfg: AppConfig, store):
    try:
        from .alerts import Alerter
    except ImportError:
        return None
    try:
        return Alerter(cfg.alerts, store)
    except Exception as e:  # pragma: no cover - depends on a module written elsewhere
        print(f"warning: alerter unavailable ({type(e).__name__}); events go to the store only", file=sys.stderr)
        return None


# ---------------------------------------------------------------- research
def cmd_backtest(args) -> int:
    from .backtest.report import run_backtest_report
    cfg = _load(args)
    path = run_backtest_report(cfg, args.out, strategies=args.strategies or None)
    print(f"report written: {path}")
    return 0


def cmd_walkforward(args) -> int:
    from .backtest.report import run_walkforward_report
    cfg = _load(args)
    path = run_walkforward_report(cfg, args.out)
    print(f"report written: {path}")
    return 0


# ---------------------------------------------------------------- broker checks
def cmd_verify_assets(args) -> int:
    from .broker.alpaca_paper import AlpacaPaperBroker
    from .broker.base import BrokerError
    cfg = _load(args)
    try:
        creds = Credentials.from_env()
    except RuntimeError:
        print("error: Alpaca paper credentials are missing. Set ALPACA_API_KEY and ALPACA_SECRET_KEY "
              "in the environment (see .env.example). Nothing was checked.", file=sys.stderr)
        return 2
    symbols = [s.symbol for s in cfg.symbols]
    try:
        broker = AlpacaPaperBroker(creds)
        result = broker.verify_symbols(symbols)
    except BrokerError as e:
        print(f"error: Alpaca paper API unreachable or refused the request: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"error: could not verify assets: {type(e).__name__}", file=sys.stderr)
        return 3
    print(f"{'symbol':<10} {'class':<7} {'tradable':<9} {'fractional':<11} {'status':<9} reason")
    bad = 0
    for sym in symbols:
        r = result[sym]
        bad += not r["tradable"]
        print(f"{sym:<10} {str(r['asset_class']):<7} {str(r['tradable']):<9} {str(r['fractionable']):<11} "
              f"{str(r['status']):<9} {r['reason']}")
    print("note: min notional ($1 fractional stocks/crypto) is an assumption to verify with Alpaca.")
    return 1 if bad else 0


# ---------------------------------------------------------------- paper trading
def build_context(cfg: AppConfig, args):
    """Broker/store/source/strategies/risk for the configured mode (sim or alpaca_paper)."""
    from .data.sources import CsvBarSource
    from .data.validate import completed_bars
    from .engine.scheduler import RunContext
    from .strategies import build_strategies

    store = _open_store(args, cfg)
    risk = _risk(cfg, store)
    strategies = build_strategies(cfg.strategies)
    alerter = _alerter(cfg, store)
    mode = cfg.execution.mode
    if mode == "sim":
        from .broker.sim import SimulatedBroker
        print(SIM_NOTICE, file=sys.stderr)
        broker = SimulatedBroker(cfg.initial_capital, cfg.costs)
        source = CsvBarSource(cfg.data.bars_dir)

        def set_prices(_action: str, now: datetime) -> None:
            for sym in cfg.enabled_symbols():
                try:
                    bars = completed_bars(source.get_bars(sym, datetime(1970, 1, 1, tzinfo=UTC), now), now)
                except Exception:
                    continue
                if not bars.empty:
                    broker.set_price(sym.symbol, float(bars["close"].iloc[-1]))

        return RunContext(broker, store, source, strategies, risk, alerter, on_before_cycle=set_prices)
    if mode == "alpaca_paper":
        from .broker.alpaca_paper import AlpacaPaperBroker
        from .data.sources import AlpacaBarSource
        creds = Credentials.from_env()
        return RunContext(AlpacaPaperBroker(creds), store, AlpacaBarSource(creds), strategies, risk, alerter)
    raise SystemExit(f"unsupported execution.mode {mode!r}")  # unreachable: config forbids it


def cmd_paper_once(args) -> int:
    from .engine.runner import run_cycle
    cfg = _load(args)
    try:
        ctx = build_context(cfg, args)
    except RuntimeError as e:   # missing credentials
        print(f"error: {e}", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    if ctx.on_before_cycle:
        ctx.on_before_cycle("all", now)
    rep = run_cycle(cfg, ctx.broker, ctx.store, ctx.source, ctx.strategies, ctx.risk, now, alerter=ctx.alerter)
    print(json.dumps(rep.summary(), indent=2, default=str))
    return 1 if rep.aborted else 0


def cmd_paper_loop(args) -> int:
    from .engine.scheduler import run_loop
    cfg = _load(args)
    try:
        Credentials.from_env() if cfg.execution.mode == "alpaca_paper" else None
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    why = run_loop(cfg, lambda: build_context(cfg, args), cfg.execution.poll_seconds,
                   max_iterations=args.max_iterations)
    print(f"paper loop stopped: {why}")
    return 0 if why != "halted" else 3


# ---------------------------------------------------------------- operations
def _fmt_ts(ts) -> str:
    return ts.isoformat(timespec="seconds") if ts else "-"


def cmd_status(args) -> int:
    from .risk.killswitch import KillSwitch
    from .risk.limits import FLAG_DAILY, FLAG_DRAWDOWN, FLAG_WEEKLY
    cfg = _load(args)
    store = _open_store(args, cfg)
    try:
        ks = KillSwitch(store).status()
        print(f"mode: {cfg.execution.mode} (paper only)   db: {_db_path(args, cfg)}")
        print(f"kill switch: {'ENGAGED' if ks['engaged'] else 'off'}"
              + (f" by {ks['by']} since {_fmt_ts(ks['since'])}: {ks['reason']}" if ks["engaged"] else ""))
        for label, flag in (("daily breaker", FLAG_DAILY), ("weekly breaker", FLAG_WEEKLY),
                            ("drawdown breaker", FLAG_DRAWDOWN)):
            info = store.get_flag_info(flag)
            print(f"{label}: " + (f"ACTIVE ({info['value']}) {info['reason']}" if info else "off"))
        curve = store.equity_curve()
        if curve.empty:
            print("account: no equity snapshots yet")
        else:
            last = curve.iloc[-1]
            peak = float(curve["equity"].max())
            print(f"account @ {_fmt_ts(last['ts'].to_pydatetime())}: equity {last['equity']:.2f} "
                  f"cash {last['cash']:.2f} gross {last['gross']:.2f} "
                  f"drawdown {max(0.0, 1 - last['equity'] / peak):.2%}")
        positions = store.latest_equity_positions()
        meta = store.all_position_meta()
        print(f"positions ({len(positions)}):")
        for p in positions:
            m = meta.get(p["symbol"], {})
            print(f"  {p['symbol']:<9} qty {p['qty']:.6g} @ {p['avg_entry_price']:.4f} "
                  f"mkt {p['market_price']:.4f} strategy {m.get('strategy', '-')} stop {m.get('stop_price')}")
        opens = store.open_orders()
        print(f"open orders ({len(opens)}):")
        for o in opens:
            print(f"  {o.client_order_id} {o.side.value} {o.qty:.6g} {o.symbol} {o.status.value}")
        print("heartbeats:")
        for hb in store.heartbeats():
            print(f"  {hb['component']:<10} {hb['status']:<8} {_fmt_ts(hb['ts'])} {hb['detail']}")
        print("recent events:")
        for ev in store.events(limit=10):
            print(f"  {_fmt_ts(ev['ts'])} {ev['level']:<8} {ev['kind']}: {ev['message']}")
    finally:
        store.close()
    return 0


def cmd_kill(args) -> int:
    from .risk.killswitch import KillSwitch
    cfg = _load(args)
    store = _open_store(args, cfg)
    try:
        KillSwitch(store).engage(args.reason, by=_who())
    finally:
        store.close()
    print(f"kill switch ENGAGED: {args.reason}")
    return 0


def cmd_unkill(args) -> int:
    from .risk.killswitch import KillSwitch
    cfg = _load(args)
    store = _open_store(args, cfg)
    try:
        ks = KillSwitch(store)
        ks.release(by=_who())
        still = ks.is_engaged()
    finally:
        store.close()
    if still:
        print(f"kill switch still ENGAGED: kill file {ks.kill_file} exists; delete it to release", file=sys.stderr)
        return 1
    print("kill switch released")
    return 0


def cmd_reset_drawdown(args) -> int:
    cfg = _load(args)
    store = _open_store(args, cfg)
    try:
        _risk(cfg, store).reset_drawdown(by=_who())
    finally:
        store.close()
    print("drawdown breaker reset; peak equity rebased from now")
    return 0


# ---------------------------------------------------------------- Robinhood (stocks)
def _rh_cfg(args) -> AppConfig:
    cfg = _load(args)
    if args.db:
        cfg = cfg.model_copy(update={"execution": cfg.execution.model_copy(update={"db_path": args.db})})
    return cfg


def cmd_rh_stock_plan(args) -> int:
    from .robinhood_stocks import plan

    cfg = _rh_cfg(args)
    state = json.loads(Path(args.state).read_text())
    print(json.dumps(plan(cfg, state, cfg.sleeve, args.bars), indent=2, default=str))
    return 0


def cmd_rh_stock_record(args) -> int:
    from .robinhood_stocks import record

    cfg = _rh_cfg(args)
    state = json.loads(Path(args.state).read_text())
    print(json.dumps(record(cfg, state, cfg.sleeve), indent=2, default=str))
    return 0


def cmd_rh_stock_pending(args) -> int:
    from .robinhood_stocks import pending

    print(json.dumps(pending(_rh_cfg(args)), indent=2, default=str))
    return 0


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=_default_config(), help="TOML config path")
    common.add_argument("--db", default=None, help="override execution.db_path")

    p = argparse.ArgumentParser(prog="quantbot", description="quantbot (paper trading only)")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("backtest", parents=[common], help="full-period backtest report")
    b.add_argument("--out", default="reports")
    b.add_argument("--strategies", nargs="*", default=None)
    b.set_defaults(func=cmd_backtest)

    w = sub.add_parser("walkforward", parents=[common], help="walk-forward report")
    w.add_argument("--out", default="reports")
    w.set_defaults(func=cmd_walkforward)

    sub.add_parser("verify-assets", parents=[common], help="check symbols at Alpaca paper").set_defaults(
        func=cmd_verify_assets)
    sub.add_parser("paper-once", parents=[common], help="run one trading cycle").set_defaults(func=cmd_paper_once)
    loop = sub.add_parser("paper-loop", parents=[common], help="run the scheduler loop")
    loop.add_argument("--max-iterations", type=int, default=None)
    loop.set_defaults(func=cmd_paper_loop)
    sub.add_parser("status", parents=[common], help="account, breakers, kill switch, heartbeats").set_defaults(
        func=cmd_status)
    k = sub.add_parser("kill", parents=[common], help="engage the kill switch")
    k.add_argument("--reason", required=True)
    k.set_defaults(func=cmd_kill)
    sub.add_parser("unkill", parents=[common], help="release the kill switch").set_defaults(func=cmd_unkill)
    sub.add_parser("reset-drawdown", parents=[common], help="manually reset the drawdown breaker").set_defaults(
        func=cmd_reset_drawdown)
    rp = sub.add_parser("rh-stock-plan", parents=[common],
                        help="Robinhood stocks: plan orders from a state file (prints JSON; places nothing)")
    rp.add_argument("--state", required=True)
    rp.add_argument("--bars", nargs="*", default=None, help="raw get_equity_historicals answers to merge")
    rp.set_defaults(func=cmd_rh_stock_plan)
    rr = sub.add_parser("rh-stock-record", parents=[common],
                        help="Robinhood stocks: record placed orders' results from a fresh state file")
    rr.add_argument("--state", required=True)
    rr.set_defaults(func=cmd_rh_stock_record)
    sub.add_parser("rh-stock-pending", parents=[common],
                   help="Robinhood stocks: orders whose Robinhood state must be reported").set_defaults(
        func=cmd_rh_stock_pending)
    return p


def main(argv: list[str] | None = None) -> int:
    setup_logging(os.environ.get("QUANTBOT_LOG_LEVEL", "INFO"))
    assert_live_trading_not_enabled()
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
