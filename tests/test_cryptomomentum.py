from trader import cryptomomentum as cm


def _write(tmp_path, rows):
    p = tmp_path / "w.csv"
    head = ["symbol", "turnover_usd"] + [f"w{i}" for i in range(53)]
    lines = [",".join(head)] + [",".join([s, str(t)] + [str(v) for v in vals]) for s, t, vals in rows]
    p.write_text("\n".join(lines) + "\n")
    return p


def test_prelisting_filler_is_blanked(tmp_path):
    # newest first: listed for 3 weeks, the scanner repeats 5.0 before that
    vals = [8.0, 7.0, 6.0] + [5.0] * 50
    closes, _ = cm.load(_write(tmp_path, [("NEW", 1e6, vals)]))
    c = closes["NEW"]
    assert c[-4:] == [5.0, 6.0, 7.0, 8.0]
    assert all(v is None for v in c[:-4])


def test_run_picks_the_rising_coin(tmp_path):
    up = [100 * 1.02 ** (52 - i) for i in range(53)]          # w0 is newest
    down = [100 * 0.98 ** (52 - i) for i in range(53)]
    closes, turnover = cm.load(_write(tmp_path, [("BTC", 1e6, up), ("DOWN", 1e6, down)]))
    r = cm.run(cm.universe(closes, turnover), lookback=4, top=1, cost=0)
    assert r["total"] > 0.5 and r["trades"] == 1
