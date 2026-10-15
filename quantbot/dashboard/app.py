"""quantbot paper-trading dashboard (Streamlit).

    streamlit run quantbot/dashboard/app.py -- --config configs/example.toml [--reports-dir reports]

Read-only view of the SQLite store named by ``execution.db_path`` in the config. The ONLY write it
can make is the kill switch (EMERGENCY STOP / release). It never imports a broker SDK or contacts a
broker. All numbers come from ``quantbot.dashboard.data``.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

# `streamlit run` puts this file's directory on sys.path, not the repo root.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from quantbot.config import AppConfig, load_config  # noqa: E402
from quantbot.dashboard import data as D  # noqa: E402
from quantbot.risk.killswitch import KillSwitch  # noqa: E402
from quantbot.store import Store  # noqa: E402

REFRESH_SECONDS = 30
DEFAULT_CONFIG = "configs/example.toml"


# ---------------------------------------------------------------- setup
def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--reports-dir", default="reports")
    args, _ = p.parse_known_args(sys.argv[1:])
    return args


def _money(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:+,.2f}" if signed else f"${v:,.2f}"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.2%}"


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    s = int(seconds)
    if s < 120:
        return f"{s}s ago"
    if s < 7200:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


def _table(df: pd.DataFrame, empty: str) -> None:
    if df.empty:
        st.info(empty)
    else:
        st.dataframe(df, hide_index=True, width="stretch")


def _open_store(db_path: str) -> Store:
    return Store(db_path)


# ---------------------------------------------------------------- sections
def render_status(cfg: AppConfig, db_path: str) -> None:
    now = datetime.now(UTC)
    store = _open_store(db_path)
    try:
        br = D.breaker_status(store, now)
        hl = D.health(store, now, cfg.execution.poll_seconds)
        acct = D.account_summary(store)
        pnl = D.pnl_summary(store, now, cfg.initial_capital)
        mdd = D.max_drawdown(store)
    finally:
        store.close()

    cols = st.columns(4)
    with cols[0]:
        st.badge(f"PAPER ONLY · mode={cfg.execution.mode}", color="blue")
    with cols[1]:
        ks = br["kill_switch"]
        if ks["engaged"]:
            st.badge("KILL SWITCH ENGAGED", color="red")
        else:
            st.badge("kill switch off", color="green")
    with cols[2]:
        active = [n.replace("breaker_", "") for n, b in br["breakers"].items() if b["active"]]
        if active:
            st.badge("breakers: " + ", ".join(active), color="orange")
        else:
            st.badge("no breakers", color="green")
    with cols[3]:
        color = {"ok": "green", "stale": "orange", "error": "red", "missing": "gray"}[hl["runner"]]
        rb = hl["runner_heartbeat"]
        st.badge(f"runner: {hl['runner']}" + (f" ({_ago(rb['age_seconds'])})" if rb else ""), color=color)

    if br["kill_switch"]["engaged"]:
        ks = br["kill_switch"]
        st.error(f"Kill switch engaged by {ks['by'] or '?'} since {ks['since'] or '?'}: {ks['reason']}. "
                 "No orders of any kind will be submitted.")
    for name, b in br["breakers"].items():
        if b["active"]:
            st.warning(f"{name} active ({b['value']}): {b['reason']} — new entries blocked; exits allowed.")
        elif b["expired"]:
            st.caption(f"{name} from {b['value']} has expired; the runner clears it on its next cycle.")

    m = st.columns(5)
    m[0].metric("Equity", _money(acct["equity"]),
                help=f"latest snapshot {acct['ts']:%Y-%m-%d %H:%M UTC}" if acct["ts"] else "no snapshot yet")
    m[1].metric("Cash", _money(acct["cash"]))
    m[2].metric("Daily P&L", _money(pnl["daily_pnl"], signed=True),
                help="equity now minus equity at 00:00 New York today")
    m[3].metric("Total P&L", _money(pnl["total_pnl"], signed=True),
                delta=_pct(pnl["total_return"]) if pnl["total_return"] is not None else None,
                help=f"equity minus initial capital {_money(cfg.initial_capital)}")
    m[4].metric("Max drawdown", _pct(mdd), help="largest peak-to-trough fall of the recorded equity curve")
    st.caption(f"Realized total {_money(pnl['realized_total'], signed=True)} · "
               f"unrealized {_money(pnl['unrealized'], signed=True)} · "
               f"gross exposure {_money(acct['gross'])} · refreshed {now:%H:%M:%S} UTC "
               f"(every {REFRESH_SECONDS}s)")
    if acct["equity"] is None:
        st.info("No data yet: the database has no equity snapshots. Start the runner (paper-once / paper-loop).")


def render_controls(db_path: str) -> None:
    st.markdown(
        "<style>.st-key-estop button{background:#c62828;color:#fff;border-color:#c62828;font-weight:700}"
        ".st-key-estop button:hover{background:#b71c1c;color:#fff}</style>",
        unsafe_allow_html=True,
    )
    left, right = st.columns([3, 2])
    with left, st.container(border=True):
        st.markdown("**Emergency stop** — engages the kill switch: the runner submits no orders at all "
                    "(entries or exits) until it is released.")
        confirm = st.checkbox("I confirm I want to stop all trading", key="estop_confirm")
        with st.container(key="estop"):
            clicked = st.button("EMERGENCY STOP", key="estop_button", type="primary")
        if clicked:
            if not confirm:
                st.error("Tick the confirmation box first.")
            else:
                store = _open_store(db_path)
                try:
                    KillSwitch(store).engage(reason="dashboard emergency stop", by="dashboard")
                finally:
                    store.close()
                st.session_state["flash"] = ("error", "Kill switch ENGAGED from the dashboard.")
                st.rerun()
    with right, st.expander("Release kill switch"):
        typed = st.text_input("Type RELEASE to confirm", key="release_text")
        if st.button("Release", key="release_button"):
            if typed.strip() != "RELEASE":
                st.error("Type RELEASE exactly to release the kill switch.")
            else:
                store = _open_store(db_path)
                try:
                    ks = KillSwitch(store)
                    ks.release(by="dashboard")
                    still = ks.is_engaged()
                    kill_file = ks.kill_file
                finally:
                    store.close()
                if still:
                    st.session_state["flash"] = ("warning", f"Released in the database, but the kill file "
                                                            f"{kill_file} still exists: delete it to resume.")
                else:
                    st.session_state["flash"] = ("success", "Kill switch released.")
                st.rerun()


def render_body(cfg: AppConfig, db_path: str, reports_dir: str) -> None:
    now = datetime.now(UTC)
    store = _open_store(db_path)
    try:
        curve = D.drawdown_series(store)
        positions = D.positions_table(store)
        open_orders = D.open_orders_table(store)
        history = D.trade_history(store)
        perf = D.strategy_performance(store, reports_dir)
        regimes = D.regimes_table(store)
        hl = D.health(store, now, cfg.execution.poll_seconds)
        events = D.events_table(store, limit=100)
    finally:
        store.close()

    st.subheader("Equity curve")
    if curve.empty:
        st.info("No equity snapshots yet.")
    else:
        st.line_chart(curve.set_index("ts")[["equity", "peak"]])

    st.subheader("Positions")
    _table(positions, "No open positions.")
    st.subheader("Open orders")
    _table(open_orders, "No open orders.")

    st.subheader("Trade history")
    t1, t2 = st.tabs(["Fills", "Realized P&L"])
    with t1:
        _table(history["fills"], "No fills yet.")
    with t2:
        _table(history["realized"], "No closed trades yet.")

    st.subheader("Strategy performance")
    st.markdown("**Paper account (live paper results, realized P&L)**")
    _table(perf["live"], "No realized P&L yet.")
    st.markdown("**Research backtest — NOT live results**")
    if perf["research_error"]:
        st.warning(perf["research_error"])
    elif perf["research_meta"] is None:
        st.info(f"No backtest report found at {Path(reports_dir) / 'latest_backtest.json'}.")
    else:
        meta = perf["research_meta"]
        st.caption(f"From {meta['path']}, generated {meta['generated_at']}. Simulated on historical data "
                   f"with assumed costs; past/backtest performance does not predict paper or live results.")
        if meta.get("warning"):
            st.caption(f"Report warning: {meta['warning']}")
        _table(perf["research"], "The report has no strategy rows.")

    st.subheader("Market regimes")
    _table(regimes, "No regimes recorded yet.")

    st.subheader("Health")
    st.caption(f"A heartbeat older than {int(hl['stale_after_seconds'] // 60)} min is stale.")
    _table(hl["table"], "No heartbeats yet (runner has not reported).")

    st.subheader("Recent events and alerts")
    _table(events, "No events yet.")


# ---------------------------------------------------------------- main
def main() -> None:
    st.set_page_config(page_title="quantbot (paper)", layout="wide")
    args = _args()
    st.title("quantbot — paper trading dashboard")
    try:
        cfg = load_config(args.config)
    except Exception as e:  # noqa: BLE001 - show the problem instead of a stack trace
        st.error(f"Could not load config {args.config}: {type(e).__name__}: {e}")
        st.stop()
        return
    db_path = cfg.execution.db_path
    st.caption(f"config `{args.config}` · database `{db_path}` · read-only except the kill switch")

    flash = st.session_state.pop("flash", None)
    if flash:
        getattr(st, flash[0])(flash[1])

    st.fragment(run_every=REFRESH_SECONDS)(render_status)(cfg, db_path)
    render_controls(db_path)
    st.fragment(run_every=REFRESH_SECONDS)(render_body)(cfg, db_path, args.reports_dir)


main()
