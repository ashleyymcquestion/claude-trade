"""Account-level risk: loss breakers, drawdown breaker, kill switch, and entry/exit approval.

Breakers (thresholds from ``RiskConfig``):
  * daily:    realized P&L since 00:00 America/New_York today  <= -daily_loss_limit  * start-of-day equity
  * weekly:   realized P&L since Monday 00:00 New York         <= -weekly_loss_limit * start-of-week equity
  * drawdown: 1 - equity / peak_equity >= max_drawdown (equity includes unrealized P&L)

Each breaker latches in a store flag when it trips, so a restart keeps it active:
  * ``breaker_daily``    = NY date it tripped ("2026-10-15");  clears automatically on a later NY date
  * ``breaker_weekly``   = ISO week it tripped ("2026-W42");   clears automatically in a later ISO week
  * ``breaker_drawdown`` = UTC timestamp it tripped;           clears only via ``reset_drawdown(by)``

Breakers block entries only; exits stay allowed because they reduce risk. The kill switch blocks
everything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from ..config import RiskConfig
from ..models import AccountSnapshot, AssetInfo, RiskDecision, Side
from .sizing import size_position

if TYPE_CHECKING:  # pragma: no cover
    from ..store import Store
    from .killswitch import KillSwitch

NY = ZoneInfo("America/New_York")

FLAG_DAILY = "breaker_daily"
FLAG_WEEKLY = "breaker_weekly"
FLAG_DRAWDOWN = "breaker_drawdown"
FLAG_DRAWDOWN_RESET = "drawdown_reset_at"   # peak is measured from this UTC timestamp after a reset

_EPS = 1e-9


@dataclass(frozen=True)
class BreakerState:
    trading_allowed: bool
    reasons: list[str]
    daily_realized: float
    weekly_realized: float
    unrealized: float
    drawdown: float
    peak_equity: float
    # extras (not in the minimal contract) for dashboards / status output
    kill_switch: bool = False
    daily_breaker: bool = False
    weekly_breaker: bool = False
    drawdown_breaker: bool = False
    start_of_day_equity: float = 0.0
    start_of_week_equity: float = 0.0
    daily_limit_usd: float = 0.0
    weekly_limit_usd: float = 0.0
    ny_date: str = ""
    iso_week: str = ""
    extra: dict = field(default_factory=dict)


def _require_aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("RiskManager needs a tz-aware 'now'")
    return now


def ny_day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """[start, end) of the New York calendar day containing ``now``, as tz-aware datetimes."""
    d = _require_aware(now).astimezone(NY).date()
    start = datetime(d.year, d.month, d.day, tzinfo=NY)
    n = d + timedelta(days=1)
    return start, datetime(n.year, n.month, n.day, tzinfo=NY)


def ny_week_bounds(now: datetime) -> tuple[datetime, datetime]:
    """[Monday 00:00, next Monday 00:00) New York for the ISO week containing ``now``."""
    d = _require_aware(now).astimezone(NY).date()
    monday = d - timedelta(days=d.weekday())
    nxt = monday + timedelta(days=7)
    return (datetime(monday.year, monday.month, monday.day, tzinfo=NY),
            datetime(nxt.year, nxt.month, nxt.day, tzinfo=NY))


def iso_week_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y:04d}-W{w:02d}"


class RiskManager:
    def __init__(self, cfg: RiskConfig, store: "Store", killswitch: "KillSwitch",
                 initial_capital: float | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.killswitch = killswitch
        self.initial_capital = initial_capital

    # ---------------------------------------------------------------- helpers
    def _period_start_equity(self, start: datetime, end: datetime, snapshot: AccountSnapshot,
                             realized: float) -> float:
        """Equity at ``start``: last snapshot at/before it, else the first one inside the period,
        else reconstructed as current equity plus the period's realized loss."""
        eq = self.store.equity_at_or_before(start)
        if eq is None:
            eq = self.store.first_equity_between(start, end)
        if eq is None or eq <= 0:
            eq = snapshot.equity + max(0.0, -realized)
        return eq

    def _peak(self, snapshot: AccountSnapshot) -> float:
        reset_at = self.store.get_flag(FLAG_DRAWDOWN_RESET)
        if reset_at:
            # After a manual reset the high-water mark restarts from the reset time; the initial
            # capital no longer applies (the operator accepted the new baseline).
            candidates = [self.store.peak_equity(since=datetime.fromisoformat(reset_at))]
        else:
            candidates = [self.store.peak_equity(), self.initial_capital]
        candidates.append(snapshot.equity)
        return max(float(c) for c in candidates if c is not None and math.isfinite(c))

    def _trip(self, flag: str, value: str, kind: str, message: str, **fields) -> None:
        self.store.set_flag(flag, value, message)
        self.store.record_event("critical", kind, message, **fields)

    def _clear(self, flag: str, reason: str, kind: str) -> None:
        self.store.set_flag(flag, None, reason)
        self.store.record_event("info", kind, reason)

    # ---------------------------------------------------------------- evaluation
    def evaluate(self, snapshot: AccountSnapshot, now: datetime) -> BreakerState:
        _require_aware(now)
        cfg = self.cfg
        day_start, day_end = ny_day_bounds(now)
        week_start, week_end = ny_week_bounds(now)
        today = day_start.date()
        today_s = today.isoformat()
        week_s = iso_week_label(today)

        daily_realized = self.store.realized_pnl_between(day_start, day_end)
        weekly_realized = self.store.realized_pnl_between(week_start, week_end)
        unrealized = float(sum(p.unrealized_pnl for p in snapshot.positions))
        peak = self._peak(snapshot)
        drawdown = max(0.0, 1.0 - snapshot.equity / peak) if peak > 0 else 0.0

        sod_equity = self._period_start_equity(day_start, day_end, snapshot, daily_realized)
        sow_equity = self._period_start_equity(week_start, week_end, snapshot, weekly_realized)
        daily_limit = cfg.daily_loss_limit * sod_equity
        weekly_limit = cfg.weekly_loss_limit * sow_equity
        tol_d = _EPS * max(1.0, abs(daily_limit))
        tol_w = _EPS * max(1.0, abs(weekly_limit))

        # --- daily
        daily_flag = self.store.get_flag(FLAG_DAILY)
        if daily_flag is not None and daily_flag < today_s:
            self._clear(FLAG_DAILY, f"daily loss breaker from {daily_flag} cleared on new NY day {today_s}",
                        "breaker_daily_cleared")
            daily_flag = None
        if daily_flag is None and daily_realized <= -daily_limit + tol_d:
            self._trip(FLAG_DAILY, today_s, "breaker_daily_tripped",
                       f"daily loss breaker tripped: realized {daily_realized:.2f} <= -{daily_limit:.2f} "
                       f"({cfg.daily_loss_limit:.2%} of start-of-day equity {sod_equity:.2f})",
                       ny_date=today_s, daily_realized=daily_realized, limit=daily_limit)
            daily_flag = today_s
        daily_active = daily_flag is not None

        # --- weekly
        weekly_flag = self.store.get_flag(FLAG_WEEKLY)
        if weekly_flag is not None and weekly_flag < week_s:
            self._clear(FLAG_WEEKLY, f"weekly loss breaker from {weekly_flag} cleared in new ISO week {week_s}",
                        "breaker_weekly_cleared")
            weekly_flag = None
        if weekly_flag is None and weekly_realized <= -weekly_limit + tol_w:
            self._trip(FLAG_WEEKLY, week_s, "breaker_weekly_tripped",
                       f"weekly loss breaker tripped: realized {weekly_realized:.2f} <= -{weekly_limit:.2f} "
                       f"({cfg.weekly_loss_limit:.2%} of start-of-week equity {sow_equity:.2f})",
                       iso_week=week_s, weekly_realized=weekly_realized, limit=weekly_limit)
            weekly_flag = week_s
        weekly_active = weekly_flag is not None

        # --- drawdown (manual reset only)
        dd_flag = self.store.get_flag(FLAG_DRAWDOWN)
        if dd_flag is None and drawdown >= cfg.max_drawdown - _EPS:
            dd_flag = now.astimezone(UTC).isoformat()
            self._trip(FLAG_DRAWDOWN, dd_flag, "breaker_drawdown_tripped",
                       f"drawdown breaker tripped: {drawdown:.2%} >= {cfg.max_drawdown:.2%} "
                       f"(equity {snapshot.equity:.2f}, peak {peak:.2f}, unrealized {unrealized:.2f})",
                       drawdown=drawdown, equity=snapshot.equity, peak=peak, unrealized=unrealized)
        dd_active = dd_flag is not None

        kill = self.killswitch.is_engaged()
        reasons: list[str] = []
        if kill:
            st = self.killswitch.status()
            reasons.append(f"kill switch engaged ({st.get('by', '')}): {st.get('reason', '')}")
        if daily_active:
            reasons.append(f"daily loss breaker active (tripped NY {daily_flag}; "
                           f"realized today {daily_realized:.2f}, limit -{daily_limit:.2f})")
        if weekly_active:
            reasons.append(f"weekly loss breaker active (tripped {weekly_flag}; "
                           f"realized this week {weekly_realized:.2f}, limit -{weekly_limit:.2f})")
        if dd_active:
            reasons.append(f"drawdown breaker active since {dd_flag} (current drawdown {drawdown:.2%}, "
                           f"limit {cfg.max_drawdown:.2%}); needs reset_drawdown")

        return BreakerState(
            trading_allowed=not reasons, reasons=reasons, daily_realized=daily_realized,
            weekly_realized=weekly_realized, unrealized=unrealized, drawdown=drawdown, peak_equity=peak,
            kill_switch=kill, daily_breaker=daily_active, weekly_breaker=weekly_active,
            drawdown_breaker=dd_active, start_of_day_equity=sod_equity, start_of_week_equity=sow_equity,
            daily_limit_usd=daily_limit, weekly_limit_usd=weekly_limit, ny_date=today_s, iso_week=week_s,
        )

    def reset_drawdown(self, by: str, now: datetime | None = None) -> None:
        """Manually clear the drawdown breaker; the high-water mark restarts from now."""
        now = (now or datetime.now(UTC)).astimezone(UTC)
        was = self.store.get_flag(FLAG_DRAWDOWN)
        self.store.set_flag(FLAG_DRAWDOWN_RESET, now.isoformat(), f"drawdown reset by {by}")
        self.store.set_flag(FLAG_DRAWDOWN, None, f"reset by {by}")
        self.store.record_event("warning", "breaker_drawdown_reset",
                                f"drawdown breaker reset by {by}; peak equity rebased from {now.isoformat()}",
                                by=by, was_tripped_at=was)

    # ---------------------------------------------------------------- approvals
    def approve_entry(self, snapshot: AccountSnapshot, symbol: str, price: float, stop_price: float | None,
                      asset: AssetInfo, one_way_cost_bps: float, now: datetime) -> RiskDecision:
        if self.killswitch.is_engaged():
            return RiskDecision(False, 0.0, "kill switch engaged")
        state = self.evaluate(snapshot, now)
        if not state.trading_allowed:
            return RiskDecision(False, 0.0, "; ".join(state.reasons))
        held = {p.symbol for p in snapshot.positions if p.qty > 0}
        if symbol in held:
            return RiskDecision(False, 0.0, f"{symbol} already held (no pyramiding)")
        for o in self.store.open_orders():
            if o.symbol == symbol and Side(o.side) is Side.BUY:
                return RiskDecision(False, 0.0, f"{symbol} has an open buy order {o.client_order_id}")
        if len(held) >= self.cfg.max_open_positions:
            return RiskDecision(False, 0.0, f"max open positions reached ({len(held)}/{self.cfg.max_open_positions})")
        current_symbol_exposure = sum(p.market_value for p in snapshot.positions if p.symbol == symbol)
        return size_position(
            equity=snapshot.equity, cash=snapshot.cash, price=price, stop_price=stop_price, asset=asset,
            current_gross=snapshot.gross_exposure, current_symbol_exposure=current_symbol_exposure,
            risk=self.cfg, one_way_cost_bps=one_way_cost_bps,
        )

    def approve_exit(self, snapshot: AccountSnapshot, symbol: str, now: datetime) -> RiskDecision:
        """Exits are allowed through breakers (they reduce risk); only the kill switch stops them."""
        if self.killswitch.is_engaged():
            return RiskDecision(False, 0.0, "kill switch engaged (no orders of any kind)")
        qty = sum(p.qty for p in snapshot.positions if p.symbol == symbol)
        if qty <= 0:
            return RiskDecision(False, 0.0, f"{symbol} not held")
        return RiskDecision(True, qty, "exit approved")
