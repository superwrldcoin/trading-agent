import numpy as np
import pandas as pd
import pytest

from tools import portfolio as pf
from tools import positions as ps

NOW = pd.Timestamp("2026-10-04 20:00", tz="UTC")


def perp(symbol="BTC/USDT", side="long", entry=100.0, size=1.0, lev=10, stop=95.0, margin="isolated"):
    return {"id": "P-x", "symbol": symbol, "instrument": "perp", "side": side, "entry": entry, "size": size,
            "leverage": lev, "stop": stop, "targets": [], "margin": margin, "mmr": 0.005}


def daily(n=300, seed=0, drift=0.0, gaps=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-06-01", periods=n, freq="1D", tz="UTC")
    c = 100 * np.cumprod(1 + drift + rng.normal(0, 0.01, n))
    o = np.r_[c[0], c[:-1] * (1 + gaps * rng.choice([-1, 1], n - 1))]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.005, "low": np.minimum(o, c) * 0.995,
                         "close": c, "volume": 1.0}, index=idx)


# ---------- positions store ----------

def test_positions_add_close_ids_and_validation(tmp_path):
    data = ps.load(tmp_path / "p.json")
    assert data == {"equity": None, "positions": []}
    a = ps.add(data, perp(), now=NOW)
    b = ps.add(data, {"symbol": "MSFT", "instrument": "option", "kind": "call", "side": "long", "strike": 520,
                      "expiry": "2026-11-20", "qty": 2, "premium": 12.5, "multiplier": 100, "iv": 0.28}, now=NOW)
    assert (a["id"], b["id"]) == ("P-001", "P-002")
    ps.close(data, "P-001")
    c = ps.add(data, perp(symbol="SI", entry=60, stop=59), now=NOW)
    assert c["id"] == "P-003"                       # ids are never reused
    ps.save(data, tmp_path / "p.json")
    assert [p["id"] for p in ps.load(tmp_path / "p.json")["positions"]] == ["P-002", "P-003"]
    with pytest.raises(ValueError, match="wrong side"):
        ps.add(data, perp(stop=105.0), now=NOW)
    with pytest.raises(ValueError, match="not on the watchlist"):
        ps.add(data, perp(symbol="DOGE"), now=NOW)
    with pytest.raises(ValueError, match="no open position"):
        ps.close(data, "P-999")


# ---------- valuation and shocks ----------

def test_value_perp_margin_liq_and_risk():
    v = pf.value_position(perp(), 102.0, NOW, 0.04)
    assert v["pnl"] == pytest.approx(2.0) and v["margin"] == pytest.approx(10.0)
    assert v["liquidation"] == pytest.approx(100 * (1 - 0.1 + 0.005))
    assert v["risk_to_stop"] == pytest.approx(7.0)   # from 102 down to the 95 stop
    assert pf.value_position(perp(margin="cross"), 102.0, NOW, 0.04)["liquidation"] is None


def test_shock_orderly_vs_gap_and_liquidation():
    p = perp(entry=100, lev=10, stop=95)             # liq 90.5
    assert pf.shocked_pnl(p, 100, 97, NOW, 0.04, "orderly") == {"pnl": pytest.approx(-3.0), "event": ""}
    assert pf.shocked_pnl(p, 100, 93, NOW, 0.04, "orderly") == {"pnl": pytest.approx(-5.0), "event": "stopped"}
    gap = pf.shocked_pnl(p, 100, 93, NOW, 0.04, "gap")
    assert gap["pnl"] == pytest.approx(-7.0) and "gap fill" in gap["event"]
    assert pf.shocked_pnl(p, 100, 85, NOW, 0.04, "orderly")["event"] == "stopped"    # stop sits above liq
    assert pf.shocked_pnl(p, 100, 85, NOW, 0.04, "gap") == {"pnl": pytest.approx(-10.0), "event": "LIQUIDATED"}
    nostop = perp(entry=100, lev=20, stop=None)       # liq 95.5
    assert pf.shocked_pnl(nostop, 100, 95, NOW, 0.04, "orderly") == {"pnl": pytest.approx(-5.0), "event": "LIQUIDATED"}
    short = perp(side="short", entry=100, lev=10, stop=104)
    assert pf.shocked_pnl(short, 100, 106, NOW, 0.04, "orderly")["pnl"] == pytest.approx(-4.0)


def test_option_valuation_and_shock():
    opt = {"id": "P-o", "symbol": "MSFT", "instrument": "option", "kind": "call", "side": "long", "strike": 100,
           "expiry": "2026-12-31", "qty": 1, "premium": 5.0, "multiplier": 100, "iv": 0.30}
    v = pf.value_position(opt, 100.0, NOW, 0.04)
    assert 0.5 < v["delta_units"] / 100 < 0.65 and v["risk_to_stop"] == pytest.approx(v["value"] * 100)
    up = pf.shocked_pnl(opt, 100, 110, NOW, 0.04, "orderly")["pnl"]
    down = pf.shocked_pnl(opt, 100, 90, NOW, 0.04, "orderly")["pnl"]
    assert up > 0 > down and down >= -500.0             # long call can't lose more than the premium
    short_put = dict(opt, kind="put", side="short")
    assert pf.value_position(short_put, 100, NOW, 0.04)["risk_to_stop"] is None   # undefined risk


def test_historical_gap_and_correlations():
    d = daily(gaps=0.02)
    g = pf.historical_gap(d, "long")
    assert 0.015 < g <= 0.0201
    a, b = daily(seed=1), daily(seed=1)
    corr = pf.correlations({"BTC/USDT": a, "BCH/USDT": b})
    assert corr[0]["rho30"] == pytest.approx(1.0) and corr[0]["rho90"] == pytest.approx(1.0)


def test_analyze_end_to_end():
    data = {"equity": 1000.0, "positions": [
        dict(perp("BTC/USDT", entry=100, size=5, lev=20, stop=None), id="P-001"),
        dict(perp("BCH/USDT", entry=100, size=3, lev=10, stop=96), id="P-002"),
        dict(perp("SI", entry=50, size=4, lev=5, stop=48), id="P-003"),
    ]}
    prices = {"BTC/USDT": 100.0, "BCH/USDT": 100.0, "SI": 50.0}
    same = daily(seed=3)
    dly = {"BTC/USDT": same, "BCH/USDT": same, "SI": daily(seed=4, gaps=0.02)}
    a = pf.analyze(data, prices, dly, NOW)
    assert a["themes"]["crypto"]["net"] == pytest.approx(800.0)
    assert a["themes"]["crypto"]["gross_pct"] == pytest.approx(80.0)
    assert a["open_risk"] == pytest.approx(3 * 4 + 4 * 2)          # BCH 12, SI 8; BTC has no stop
    msgs = " ".join(m for _, m in a["flags"])
    assert "treat them as one bet" in msgs and "no defined risk" in msgs and "P-001" in msgs
    m10 = next(s for s in a["scenarios"] if s["name"] == "all -10%" and s["mode"] == "orderly")
    assert "P-001 LIQUIDATED" in m10["events"]                     # 20x, liq 95.5, no stop
    assert m10["total_pnl"] == pytest.approx(-25.0 - 12.0 - 8.0)   # BTC margin 25, BCH to stop, SI to stop
    assert m10["account"] == pytest.approx(1000 - 45.0)
    gap = a["scenarios"][-1]
    assert gap["mode"] == "gap" and any("SI" in d for d in gap["detail"])
    text = pf.render(a)
    assert "| crypto |" in text and "LIQUIDATED" in text and "not a forecast" in text.lower()


def test_render_empty():
    assert "No open positions" in pf.render(pf.analyze({"equity": None, "positions": []}, {}, {}, NOW))
