import pandas as pd
import pytest

from tools import paper as pp

T0 = pd.Timestamp("2026-11-05 10:00", tz="UTC")


class FakeBroker(pp.Broker):
    """Serves a fixed 15M OHLC path; `upto` controls how much of it is 'visible' yet."""
    def __init__(self, rows, start=T0 - pd.Timedelta(minutes=15)):
        idx = pd.date_range(start, periods=len(rows), freq="15min", tz="UTC")
        self.df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
        self.df["volume"] = 1.0
        self.upto = 1

    def bars(self, symbol, bars=700):
        return self.df.iloc[:self.upto]


def opened(state, broker, **kw):
    req = dict(symbol="BTC/USDT", side="long", stop=95.0, targets=[105.0, 110.0], risk_pct=1, leverage=2, p_t1=60)
    req.update(kw)
    return pp.open_trade(state, req, broker, now=T0)


def fresh():
    return {"account": pp.new_account(10_000, T0), "trades": [], "next_id": 1, "archive": []}


def test_market_long_scales_out_at_targets_and_books_R():
    b = FakeBroker([(100, 100, 100, 100), (100, 106, 99, 105), (105, 111, 104, 110)])
    st = fresh()
    t = opened(st, b)
    assert t["status"] == "open" and t["entry"] == 100 and t["size"] == pytest.approx(10_000 * 0.01 / (5 + 0.1 * 0 + 100 * 0.0005 + 95 * 0.0005))
    b.upto = 2
    pp.refresh(st, b)
    assert t["targets"][0]["hit_at"] and t["remaining"] == pytest.approx(0.5) and t["t1_before_stop"] is True
    b.upto = 3
    pp.refresh(st, b)
    assert t["status"] == "closed" and t["exit_reason"] == "target"
    # 50% at +5, 50% at +10 = +7.5 per unit before fees on a 5-point stop -> 1.5R minus fees
    assert t["R"] == pytest.approx(1.5, abs=0.05) and t["R"] < 1.5
    assert pp.balance(st) == pytest.approx(10_000 + t["realized"])


def test_bar_touching_stop_and_target_counts_as_stop():
    b = FakeBroker([(100, 100, 100, 100), (100, 106, 94, 100)])
    st = fresh()
    t = opened(st, b)
    b.upto = 2
    pp.refresh(st, b)
    assert t["exit_reason"] == "stop" and t["t1_before_stop"] is False and t["R"] == pytest.approx(-1, abs=0.05)


def test_liquidation_before_stop_is_rejected_and_limit_fills_later():
    b = FakeBroker([(100, 100, 100, 100), (100, 101, 97, 98)])
    st = fresh()
    with pytest.raises(pp.PaperError, match="liquidation"):
        opened(st, b, leverage=50)
    with pytest.raises(pp.PaperError, match="below the current price"):
        opened(st, b, limit=101)
    t = opened(st, b, limit=98, stop=95)
    assert t["status"] == "pending" and t["entry"] == 98
    b.upto = 2
    pp.refresh(st, b)
    assert t["status"] == "open" and t["opened_at"] == b.df.index[1].isoformat()
    tid = t["id"]
    pp.close_trade(st, tid, 99.0)
    assert t["status"] == "closed" and t["exit_reason"] == "manual" and t["t1_before_stop"] is False
    with pytest.raises(pp.PaperError, match="not open"):
        pp.close_trade(st, tid, 99.0)


def test_short_and_cancel_and_contracts():
    b = FakeBroker([(100, 100, 100, 100), (100, 100.5, 94, 95)])
    st = fresh()
    t = opened(st, b, side="short", stop=104, targets=[95])
    b.upto = 2
    pp.refresh(st, b)
    assert t["status"] == "closed" and t["R"] > 0
    p = opened(st, b, side="short", limit=103, stop=106, targets=[95])
    pp.cancel_trade(st, p["id"])
    assert p["status"] == "cancelled"
    b.upto = 1                                    # back to a 100 quote for the futures tickets
    m = pp.open_trade(st, dict(symbol="MES", side="long", stop=95, targets=[110], contracts=3), b, now=T0)
    assert m["size"] == 15 and m["contracts"] == 3 and m["contract_name"] == "MES"
    with pytest.raises(pp.PaperError, match="too small for 1 MES"):
        pp.open_trade(st, dict(symbol="MES", side="long", stop=50, targets=[110], risk_pct=0.1), b, now=T0)
    with pytest.raises(pp.PaperError, match="isn't a futures"):
        opened(st, b, contracts=2)


def test_stats_calibration_insights_and_snapshot(tmp_path):
    st = fresh()
    b = FakeBroker([(100, 100, 100, 100), (100, 106, 99, 105)])
    for _ in range(3):
        t = opened(st, b, targets=[105], p_t1=80)
    b2 = FakeBroker([(100, 100, 100, 100), (100, 101, 94, 95)])
    for _ in range(3):
        opened(st, b2, targets=[105], p_t1=80)
    b.upto = b2.upto = 2
    for t in st["trades"][:3]:
        pp.step(t, b.bars("x"))
    for t in st["trades"][3:]:
        pp.step(t, b2.bars("x"))
    s = pp.stats(st)
    assert s["closed"] == 6 and s["win_rate"] == pytest.approx(50)
    cal = s["calibration"][0]
    assert cal["bucket"] == "60-100%" and cal["called"] == 80 and cal["actual"] == 50
    assert any("overconfident" in line for line in s["insights"]) and any("Expectancy" in x for x in s["insights"])
    assert len(s["curve"]) == 7
    snap = pp.snapshot(st, {"BTC/USDT": 100.0})
    assert snap["account"]["equity"] == pytest.approx(pp.balance(st)) and snap["trades"][0]["id"] == "PT-006"
    path = tmp_path / "paper.json"
    pp.save(st, path)
    assert pp.load(path)["next_id"] == 7
    pp.api_reset(5000, path)
    again = pp.load(path)
    assert again["account"]["start_equity"] == 5000 and again["trades"] == [] and len(again["archive"]) == 1


def test_preview_includes_rules_and_quote(tmp_path):
    b = FakeBroker([(100, 100, 100, 100)])
    out = pp.api_preview(dict(symbol="BTC/USDT", side="long", stop=95, targets=[110], risk_pct=1, leverage=12),
                         tmp_path / "p.json", b)
    assert out["quote"]["price"] == 100 and out["preview"]["risk_pct"] == pytest.approx(1, abs=0.01)
    lev = next(r for r in out["preview"]["rules"] if r["rule"].startswith("max leverage"))
    assert lev["result"] == "BREAKS"   # crypto cap is 10x
    with pytest.raises(pp.PaperError, match="stop is required"):
        pp.api_preview(dict(symbol="BTC/USDT", side="long", targets=[110]), tmp_path / "p.json", b)
