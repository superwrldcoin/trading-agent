import numpy as np
import pandas as pd
import pytest

from tools import vol_check as vc


def daily(closes, highs=None, lows=None):
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="1D", tz="UTC")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"open": c, "high": highs if highs is not None else c, "low": lows if lows is not None else c,
                         "close": c, "volume": 1.0}, index=idx)


def test_adverse_excursion_long_and_short():
    d = daily([100, 100, 100, 100], highs=[100, 103, 101, 100], lows=[100, 98, 95, 100])
    long1 = vc.adverse_excursion(d, "long", 1)
    assert long1.tolist() == pytest.approx([0.02, 0.05, 0.0])        # next-day lows 98, 95, 100
    long2 = vc.adverse_excursion(d, "long", 2)
    assert long2.tolist() == pytest.approx([0.05, 0.05])             # min of next 2 lows
    short1 = vc.adverse_excursion(d, "short", 1)
    assert short1.tolist() == pytest.approx([0.03, 0.01, 0.0])


def test_touch_probability_counts_frequency():
    n = 200
    lows = [100.0 if i % 4 else 97.0 for i in range(n)]               # every 4th day dips 3%
    d = daily([100.0] * n, lows=lows)
    p = vc.touch_probability(d, "long", 0.02, horizons=(1, 4), lookback=150)
    assert p[1] == pytest.approx(0.25, abs=0.02)
    assert p[4] == pytest.approx(1.0)
    assert vc.touch_probability(d.head(10), "long", 0.02, horizons=(1,))[1] is None  # too little history


def test_leverage_for_odds():
    n = 300
    lows = [100.0 - (i % 10) * 0.2 for i in range(n)]                 # adverse moves 0..1.8%
    d = daily([100.0] * n, lows=lows)
    lev = vc.leverage_for_odds(d, "long", mmr=0.005, horizon=1, max_prob=0.05)
    q = vc.adverse_excursion(d, "long", 1).tail(vc.LOOKBACK).quantile(0.95)
    assert lev == pytest.approx(1 / (q + 0.005))


def _market(n=600, swing=0.03):
    t = np.arange(n)
    c = 100 * (1 + 0.0005 * t)
    d = daily(c, highs=c * (1 + swing / 2), lows=c * (1 - swing / 2))
    df4 = d.copy()
    return d, df4


def test_analyze_flags_high_leverage():
    d, df4 = _market(swing=0.06)                                      # lows 3% under close: beyond the 2% liq distance
    r = vc.analyze(d, df4, "long", 100.0, 40, None, 0.005)          # liq 2% away
    assert r["liq_dist_pct"] == pytest.approx(2.0)
    assert r["liq_touch"][1] > 0.5
    levels = [l for l, _ in r["flags"]]
    assert "fail" in levels and r["safe_leverage_5d"] < 40


def test_analyze_stop_beyond_liquidation_fails():
    d, df4 = _market(swing=0.01)
    r = vc.analyze(d, df4, "long", 100.0, 50, 97.0, 0.005)          # liq 98.5, stop 97 -> liquidated first
    assert any(l == "fail" and "beyond the liquidation" in m for l, m in r["flags"])
    assert "stop_touch" in r


def test_analyze_low_leverage_is_clean():
    d, df4 = _market(swing=0.01)
    r = vc.analyze(d, df4, "long", 100.0, 2, 98.0, 0.005)
    assert r["flags"] == [] and r["liq_touch"][10] == 0.0


def test_render_contains_table_and_disclaimer_line():
    d, df4 = _market()
    text = vc.render("BTC/USDT", vc.analyze(d, df4, "short", 100.0, 10, 103.0, 0.005))
    assert "touched within 5d" in text and "not a forecast" in text and "| Stop (" in text


def test_main_rejects_unknown_symbol(capsys):
    assert vc.main(["DOGE", "long", "--leverage", "10"]) == 2
    assert "not on the watchlist" in capsys.readouterr().out
