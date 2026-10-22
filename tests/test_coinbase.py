from datetime import datetime, timedelta, timezone

import pytest

from trader import coinbase as cb

CFG = cb.load_config()
NOW = datetime(2026, 10, 22, 20, 7, tzinfo=timezone.utc)      # a Thursday; last week ended Sun Oct 18


def daily(prices_by_day: list[float], end: str = "2026-10-21") -> dict:
    """Daily candles, newest first like Coinbase, the last one on `end`."""
    last = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    n = len(prices_by_day)
    return {"candles": [{"start": str(int((last - timedelta(days=n - 1 - i)).timestamp())), "close": str(p),
                         "open": str(p), "high": str(p), "low": str(p)} for i, p in enumerate(prices_by_day)][::-1]}


def book(**prices) -> dict:
    return {"pricebooks": [{"product_id": f"{c}-USDC", "bids": [{"price": str(p)}], "asks": [{"price": str(p * 1.0001)}]}
                           for c, p in prices.items()]}


def rising(start, days=120, rate=0.01):
    return [start * (1 + rate) ** i for i in range(days)]


def test_last_week_end_is_a_finished_sunday():
    assert cb.last_week_end(NOW) == "2026-10-18"
    # Monday 00:30 UTC: Sunday's daily candle has just closed
    assert cb.last_week_end(datetime(2026, 10, 19, 0, 30, tzinfo=timezone.utc)) == "2026-10-18"
    # Sunday itself: that day isn't finished, so the week before
    assert cb.last_week_end(datetime(2026, 10, 18, 23, 0, tzinfo=timezone.utc)) == "2026-10-11"


def test_weekly_closes_use_sundays_only():
    w = cb.weekly_closes({"BTC-USDC": daily(rising(100))}, NOW)
    assert all(d.dayofweek == 6 for d in w.index)
    assert str(w.index[-1].date()) == "2026-10-18"


def test_targets_follow_btc_trend():
    up = {"BTC-USDC": daily(rising(100)), "ETH-USDC": daily(rising(10, rate=0.02)),
          "SOL-USDC": daily(rising(10, rate=-0.01)), "XLM-USDC": daily(rising(1, rate=0.005))}
    tgt, info = cb.targets(cb.weekly_closes(up, NOW), CFG)
    assert tgt["btc_trend"] == {"BTC": 1.0}
    assert set(tgt["momentum"]) == {"ETH", "BTC"}            # top 2 by 4-week return, both above average
    assert not info["SOL"]["above"]

    down = dict(up, **{"BTC-USDC": daily(rising(100, rate=-0.01))})
    tgt, _ = cb.targets(cb.weekly_closes(down, NOW), CFG)
    assert tgt == {"btc_trend": {}, "momentum": {}}          # BTC filter: everything in cash


def test_paper_cycle_buy_fill_then_sell(tmp_path):
    ledger = cb.new_ledger(CFG)
    candles = {"BTC-USDC": daily(rising(100)), "ETH-USDC": daily(rising(10, rate=0.02)),
               "SOL-USDC": daily(rising(10, rate=-0.01))}
    rep = cb.step(ledger, {"now": cb._iso(NOW), "best_bid_ask": book(BTC=80000, ETH=2400, SOL=100),
                           "candles_1d": candles}, CFG)
    placed = {(o["strategy"], o["coin"]) for o in rep["placed"]}
    assert placed == {("btc_trend", "BTC"), ("momentum", "BTC"), ("momentum", "ETH")}
    assert all(c["type"] == "limit" and c["post_only"] and c["portfolio_id"] == CFG["portfolio_id"]
               for c in rep["real_calls"])
    bt = ledger["strategies"]["btc_trend"]
    assert bt["cash"] >= 0 and float(bt["orders"][0]["limit"]) == 80000.0
    assert rep["strategies"]["btc_trend"]["equity"] == pytest.approx(5.0, abs=0.01)

    # an hour later a 15-minute candle trades below the bid: the buy fills at the limit, 0.5% fee
    later = NOW + timedelta(hours=1)
    c15 = {"candles": [{"start": str(int((NOW + timedelta(minutes=15)).timestamp())), "low": "79990", "high": "80100"},
                       {"start": str(int((NOW - timedelta(minutes=15)).timestamp())), "low": "1", "high": "1"}]}
    needs = cb.needs(ledger, CFG, later)
    assert not needs["week_due"] and "BTC-USDC" in needs["candles_15m"]
    rep = cb.step(ledger, {"now": cb._iso(later), "best_bid_ask": book(BTC=80000, ETH=2400),
                           "candles_15m": {"BTC-USDC": c15}}, CFG)
    assert any("btc_trend: BUY" in f for f in rep["fills"])
    assert bt["coins"]["BTC"] > 0 and not bt["orders"] and 0 <= bt["cash"] < 0.1
    assert rep["placed"] == []                                 # already holds its target

    # next week BTC drops below its average: everything is sold with limit asks
    next_week = NOW + timedelta(days=7)
    falling = dict(candles, **{"BTC-USDC": daily(rising(100) + [50] * 7, end="2026-10-28")})
    rep = cb.step(ledger, {"now": cb._iso(next_week), "best_bid_ask": book(BTC=70000, ETH=2400, SOL=100),
                           "candles_1d": falling}, CFG)
    assert bt["target"] == {} and any(o["strategy"] == "btc_trend" and o["side"] == "SELL" for o in rep["placed"])
    assert "momentum: " in "; ".join(rep["cancelled"])         # ETH buy left from last week was cancelled


def test_new_week_without_daily_candles_fails():
    with pytest.raises(ValueError):
        cb.step(cb.new_ledger(CFG), {"now": cb._iso(NOW), "best_bid_ask": book(BTC=80000)}, CFG)


def test_leftovers_are_only_proposed():
    ledger = cb.new_ledger(CFG)
    candles = {"BTC-USDC": daily(rising(100)), "XLM-USDC": daily(rising(0.2, rate=-0.01))}
    balance = {"accounts": [{"currency": "XLM", "available_balance": {"value": "26.9"}},
                            {"currency": "SPELL", "available_balance": {"value": "3"}},
                            {"currency": "USDC", "available_balance": {"value": "10"}}]}
    rep = cb.step(ledger, {"now": cb._iso(NOW), "best_bid_ask": book(BTC=80000, XLM=0.1757),
                           "candles_1d": candles, "balance": balance}, CFG)
    xlm = next(r for r in rep["leftovers"] if r["coin"] == "XLM")
    assert xlm["proposal"] and xlm["real_call"]["side"] == "SELL" and xlm["real_call"]["post_only"]
    assert all(o["coin"] != "XLM" or o["side"] == "BUY" for o in rep["placed"])   # no paper/real XLM sell
    assert {r["coin"] for r in rep["leftovers"]} == {"XLM", "SPELL"}


def test_kill_switch_places_nothing():
    cfg = dict(CFG, kill_switch=True)
    rep = cb.step(cb.new_ledger(cfg), {"now": cb._iso(NOW), "best_bid_ask": book(BTC=80000),
                                       "candles_1d": {"BTC-USDC": daily(rising(100))}}, cfg)
    assert rep["placed"] == []


def test_run_writes_ledger_and_journal(tmp_path):
    import json
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"now": cb._iso(NOW), "best_bid_ask": book(BTC=80000),
                               "candles_1d": {"BTC-USDC": daily(rising(100))}}))
    rep = cb.run(str(inp), ledger_path=tmp_path / "paper.json", journal_path=tmp_path / "log.md")
    assert (tmp_path / "paper.json").exists()
    text = (tmp_path / "log.md").read_text()
    assert "1:07 PM PT" in text and "btc_trend" in text and rep["placed"]
