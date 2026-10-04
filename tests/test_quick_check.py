import numpy as np
import pandas as pd
import pytest

from tools import levels
from tools import quick_check as qc

NOW = pd.Timestamp("2026-10-04 20:00", tz="UTC")


@pytest.mark.parametrize("text, expect", [
    ("BTC long, 20x, entry 98,400, how does it look?",
     dict(symbol="BTC/USDT", side="long", leverage=20, entry=98400, stop=None, targets=[])),
    ("short gold @ 4215 sl 4236.16 tp 4143.7, 4117.5",
     dict(symbol="XAU/USD", side="short", entry=4215, stop=4236.16, targets=[4143.7, 4117.5])),
    ("msft long entry 513.94 stop 487.30 targets 522.85/531 account 10k risk 1%",
     dict(symbol="MSFT", side="long", entry=513.94, stop=487.30, targets=[522.85, 531], equity=10000, risk_pct=1)),
    ("Bitcoin cash buy at 297.5, stop loss 293.64, 10x leverage",
     dict(symbol="BCH/USDT", side="long", entry=297.5, stop=293.64, leverage=10)),
    ("silver short 3x", dict(symbol="SI", side="short", leverage=3, entry=None)),
    ("BTC long 98.4k 25x", dict(symbol="BTC/USDT", side="long", entry=98400, leverage=25)),
    ("how does GLD look?", dict(symbol="GLD", side=None, entry=None, leverage=None)),
    ("BTC long 20x targets 100,000, 105,000", dict(targets=[100000, 105000], entry=None)),
])
def test_parse_request(text, expect):
    req = qc.parse_request(text)
    for k, v in expect.items():
        assert req[k] == v, (k, req[k])


def test_parse_leverage_is_not_mistaken_for_entry():
    req = qc.parse_request("BTC long 20x")
    assert req["leverage"] == 20 and req["entry"] is None


def test_parse_requires_asset():
    with pytest.raises(qc.ParseError, match="couldn't find an asset"):
        qc.parse_request("long 20x entry 100")


def test_to_number():
    assert qc.to_number("98,400") == 98400 and qc.to_number("$98.4k") == 98400 and qc.to_number("4215.5") == 4215.5


# ---------- synthetic market: uptrend with clear 4H swings ----------

def market(n=700, start="2026-07-01"):
    idx = pd.date_range(start, periods=n, freq="4h", tz="UTC")
    t = np.arange(n)
    close = 100 + t * 0.05 + 2 * np.sin(t / 6)          # rising with regular swings
    df = pd.DataFrame({"open": close, "high": close + 0.6, "low": close - 0.6, "close": close,
                       "volume": 1000.0, "confirmed": True}, index=idx)
    daily = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    hourly = df.resample("1h").ffill().tail(800)
    fine = df.resample("15min").ffill().tail(96)
    return {"4H": df, "1D": daily, "1H": hourly, "15M": fine}


def run(text, **kw):
    req = qc.parse_request(text)
    req.update(kw)
    return qc.analyze(req, market(), {}, NOW, mmr=0.005, fee=0.0005)


def test_analyze_at_market_long_derives_stop_and_targets():
    a = run("BTC long 2x")
    assert a["entry"] == pytest.approx(a["price"]) and a["entry_type"] == "at market"
    assert a["stop"] < a["entry"] and "most recent 4H swing low" in a["stop_source"]
    assert a["targets"] and all(t["price"] > a["entry"] for t in a["targets"])
    assert a["conviction"]["long"]["score"] > 0
    assert a["position"]["targets"][0]["R"] > 0
    assert a["verdict"]


def test_liquidation_before_stop_fails():
    a = run("BTC long 150x")
    assert a["verdict"] == "Fails as specified"
    assert any(lvl == "fail" and "BEFORE the stop" in msg for lvl, msg in a["flags"])
    assert any("Leverage <=" in s for s in a["suggestions"])


def test_far_entry_is_flagged_and_uses_daily_fallback_or_flags():
    a = run("BTC long 2x", entry=None)
    price = a["price"]
    b = run(f"BTC long 2x entry {price * 1.2:.2f}")
    assert any("from the current price" in msg for _, msg in b["flags"])
    assert b["entry_type"].startswith("breakout")


def test_user_stop_on_wrong_side_fails():
    a = run("BTC long 2x")
    b = run(f"BTC long 2x entry {a['price']:.2f} stop {a['price'] * 1.01:.2f}")
    assert b["verdict"].startswith("Fails") and "wrong side" in b["verdict"]


def test_no_side_reports_both_convictions():
    a = run("how does BTC look")
    assert a["side"] is None and a["verdict"].startswith("Incomplete")
    text = qc.render(a)
    assert "Conviction long" in text and "Conviction short" in text


def test_leverage_too_high_for_mmr():
    a = run("BTC long 300x")
    assert a["verdict"].startswith("Fails") and "too high for MMR" in a["verdict"]


def test_render_has_milestones_flags_and_single_disclaimer():
    text = qc.render(run("BTC long 20x"))
    assert "| Milestone | Price |" in text and "| Liquidation (20x) |" in text
    assert text.count("not financial advice") == 1
    assert "P(T1 before stop): not estimated" in text


def test_targets_skip_levels_at_entry_and_merge():
    prim = [(100.2, "PDH"), (101.0, "4H swing high"), (101.3, "PWH"), (103.0, "PMthH")]
    out = qc.derive_targets("long", 100.0, prim, [(106.0, "1D swing high")], atr14=1.0)
    assert [v for v, _ in out] == [101.0, 103.0, 106.0]   # 100.2 is within 0.5 ATR of entry; 101.3 merged
    assert "+1 more" in out[0][1]


def test_market_open_sessions():
    sat = pd.Timestamp("2026-10-03 15:00", tz="UTC")
    assert qc.market_open("BTC/USDT", sat)
    assert not qc.market_open("MSFT", sat) and not qc.market_open("XAU/USD", sat)
    mon = pd.Timestamp("2026-10-05 15:00", tz="UTC")  # 11:00 ET
    assert qc.market_open("MSFT", mon) and qc.market_open("SI", mon)


def test_main_parse_error_exit_code(capsys):
    assert qc.main(["long 20x"]) == 2
    assert "couldn't find an asset" in capsys.readouterr().out
