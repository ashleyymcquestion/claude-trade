"""One trading tick, and the loop that repeats it."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from .client import KalshiClient, KalshiError, count_field
from .config import KalshiConfig
from .model import Decision, decide, taker_fee_cents
from .pricefeed import CoinbaseFeed
from .risk import block_reason, size
from .store import Store, ny_date, now_utc

log = logging.getLogger("kalshibot")

# Kalshi needs a little time after the close to publish the result.
SETTLE_GRACE = timedelta(seconds=30)


class Bot:
    def __init__(self, cfg: KalshiConfig, client: KalshiClient, feed: CoinbaseFeed, store: Store,
                 clock=now_utc) -> None:
        self.cfg, self.client, self.feed, self.store, self.clock = cfg, client, feed, store, clock

    def settle_open(self) -> list[dict]:
        done = []
        now = self.clock()
        for t in self.store.open_trades():
            if datetime.fromisoformat(t["close_time"]) + SETTLE_GRACE > now:
                continue
            try:
                m = self.client.market(t["ticker"])
            except KalshiError as e:
                log.warning("could not fetch %s to settle: %s", t["ticker"], e)
                continue
            if m.result in ("yes", "no"):
                pnl = self.store.settle(t["id"], m.result)
                done.append({"ticker": t["ticker"], "side": t["side"], "result": m.result, "pnl_cents": pnl})
                log.info("settled %s: bought %s, result %s, P&L $%.2f", t["ticker"], t["side"],
                         m.result, pnl / 100)
        return done

    def tick(self) -> dict:
        settled = self.settle_open()
        now = self.clock()
        stats = self.store.day_stats(ny_date(now))
        blocked = block_reason(self.cfg, stats)
        if blocked:
            log.info("not trading: %s", blocked)
            return {"status": "blocked", "reason": blocked, "settled": settled}

        markets = [m for m in self.client.open_markets(self.cfg.strategy.series_ticker)
                   if m.close_time and m.close_time > now]
        if not markets:
            return {"status": "idle", "reason": "no open market in the series", "settled": settled}
        market = min(markets, key=lambda m: m.close_time)
        if self.store.has_trade(market.ticker):
            return {"status": "idle", "reason": f"already traded {market.ticker}", "settled": settled}

        spot = self.feed.spot()
        candles = self.feed.minute_candles(self.cfg.strategy.vol_lookback_minutes)
        d = decide(market, spot, candles, now, self.cfg.strategy)
        if d.action != "buy":
            self.store.record_decision(d, now)
            return {"status": "skip", "reason": d.reason, "ticker": d.ticker, "settled": settled}
        return {**self._execute(d, market.close_time, stats, now), "settled": settled}

    def _execute(self, d: Decision, close_time: datetime, stats: dict, now: datetime) -> dict:
        run = self.cfg.run
        balance = None if run.dry_run else self.client.balance_cents()
        n = size(self.cfg, d.price_cents, stats, balance)
        if n <= 0:
            d = Decision(**{**d.__dict__, "action": "skip", "reason": d.reason + "; size is 0 after limits"})
            self.store.record_decision(d, now)
            return {"status": "skip", "reason": d.reason, "ticker": d.ticker}

        order_id = None
        if run.dry_run:
            filled = n      # optimistic: assumes the whole order fills at the ask
        else:
            order = self.client.buy(d.ticker, d.side, n, d.price_cents, run.time_in_force)
            order_id = order.get("order_id")
            filled = count_field(order, "fill_count")
            if not filled and order_id and order.get("status") not in ("canceled", "executed"):
                filled = count_field(self.client.order(order_id), "fill_count")
        self.store.record_decision(d, now)
        if filled <= 0:
            log.info("%s: order for %d %s at %dc did not fill", d.ticker, n, d.side, d.price_cents)
            return {"status": "unfilled", "ticker": d.ticker, "reason": d.reason}

        fee = taker_fee_cents(filled, d.price_cents)
        self.store.record_trade(ticker=d.ticker, side=d.side, count=filled, price_cents=d.price_cents,
                                fee_cents=fee, close_time=close_time, ts=now, dry_run=run.dry_run,
                                environment=run.environment, order_id=order_id)
        log.info("%sbought %d %s on %s at %dc (fee %dc): %s", "[dry run] " if run.dry_run else "",
                 filled, d.side.upper(), d.ticker, d.price_cents, fee, d.reason)
        return {"status": "bought", "ticker": d.ticker, "side": d.side, "count": filled,
                "price_cents": d.price_cents, "fee_cents": fee, "reason": d.reason}

    def run_forever(self, max_iterations: int | None = None, sleep=time.sleep) -> None:
        i = 0
        while max_iterations is None or i < max_iterations:
            i += 1
            try:
                result = self.tick()
                if result["status"] not in ("idle", "skip"):
                    log.info("tick: %s", result)
                else:
                    log.debug("tick: %s", result)
            except Exception:     # network blips must not kill an unattended loop
                log.exception("tick failed; retrying next interval")
            sleep(self.cfg.run.poll_seconds)
