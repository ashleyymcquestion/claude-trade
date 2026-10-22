from trader import budgetbacktest as bb


def _setup(tmp_path):
    opt, bars = tmp_path / "opt", tmp_path / "bars"
    opt.mkdir(), bars.mkdir()
    days = ["2026-09-%02d" % d for d in (1, 2, 3, 4, 8, 9, 10, 11, 14, 15, 16, 17, 18)]
    spots = [100, 99, 98, 97, 100, 103, 106, 108, 110, 112, 114, 115, 116]
    (bars / "XYZ.csv").write_text("timestamp,close\n" + "".join(f"{d}T00:00:00Z,{s}\n" for d, s in zip(days, spots)))
    rows = ["symbol,expiration,type,strike,date,open,high,low,close"]
    for d, s in zip(days, spots):
        for k in (100, 105, 110):
            c = max(s - k, 0) + 0.5
            rows.append(f"XYZ,2026-09-18,call,{k},{d},{c},{c},{c},{c}")
    (opt / "XYZ.csv").write_text("\n".join(rows) + "\n")
    return opt, bars


def test_call_bought_cheap_pays_at_expiration(tmp_path, monkeypatch):
    opt, bars = _setup(tmp_path)
    monkeypatch.setattr(bb, "DTE_WINDOW", (14, 17))
    chain, closes = bb.load_options("XYZ", opt), bb.load_closes("XYZ", bars)
    legs = bb.pick("call_nearest", chain, closes["2026-09-01"], "2026-09-01", "2026-09-18")
    assert legs == [("call", 100.0, 1)]                      # 0.50 + slip <= $1
    r, why = bb.simulate(legs, chain, closes, "2026-09-18", "2026-09-01", "hold")
    assert why == "expired" and abs(r - (16 / 0.53 - 1)) < 1e-9
    r, why = bb.simulate(legs, chain, closes, "2026-09-18", "2026-09-01", "lab")
    assert why == "target"


def test_backtest_runs(tmp_path, monkeypatch):
    opt, bars = _setup(tmp_path)
    monkeypatch.setattr(bb, "DTE_WINDOW", (14, 17))
    out = bb.backtest(["XYZ"], opt, bars)
    assert out["call_nearest | hold | every day"]["win_rate"] == 1.0
