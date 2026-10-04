import numpy as np
import pandas as pd
import pytest

from tools import indicators as ind
from tools import levels


def bars(highs, lows, closes=None, start="2026-10-01", freq="4h", volume=None):
    idx = pd.date_range(start, periods=len(highs), freq=freq, tz="UTC")
    closes = closes if closes is not None else [(h + l) / 2 for h, l in zip(highs, lows)]
    vol = volume if volume is not None else [1.0] * len(highs)
    return pd.DataFrame({"open": closes, "high": highs, "low": lows, "close": closes, "volume": vol}, index=idx)


def test_ema_seeds_with_sma_then_recurses():
    out = ind.ema(pd.Series([1.0, 2, 3, 4, 5]), 3)  # alpha = 0.5
    assert np.isnan(out.iloc[0]) and np.isnan(out.iloc[1])
    assert out.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_wilder_smoothing():
    out = ind.wilder(pd.Series([2.0, 4, 6, 8]), 2)  # seed mean(2,4)=3, then (1*prev + x)/2
    assert out.iloc[1:].tolist() == [3.0, 4.5, 6.25]


def test_true_range_uses_previous_close():
    df = bars([10, 12], [9, 11], closes=[9.5, 11.5])
    tr = ind.true_range(df)
    assert tr.tolist() == [1.0, 2.5]  # bar 2: max(1, |12-9.5|, |11-9.5|)


def test_atr_constant_range():
    df = bars([11.0] * 20, [10.0] * 20, closes=[10.5] * 20)
    assert ind.atr(df, 14).iloc[-1] == pytest.approx(1.0)


def test_rsi_known_values():
    out = ind.rsi(pd.Series([1.0, 2, 3, 2]), 2)
    # diffs 1, 1, -1. Seed at bar 2: gain 1, loss 0 -> 100. Bar 3: gain 0.5, loss 0.5 -> 50.
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == 100.0 and out.iloc[3] == pytest.approx(50.0)


def test_rvol():
    vol = pd.Series([1.0, 1, 1, 4])
    assert ind.rvol(vol, 4).iloc[-1] == pytest.approx(4 / 1.75)


def test_swings_strict_and_last_two_bars_excluded():
    highs = [1, 2, 5, 2, 1, 3, 3, 1, 1, 9, 1]
    lows = [h - 0.5 for h in highs]
    df = bars(highs, lows)
    sh, sl = ind.swings(df)
    assert sh.tolist() == [5]           # equal highs (3, 3) don't count; 9 is in the last 2 bars' window
    assert sl.tolist() == [0.5]         # the 1-0.5 low at index 4 sits between 2 and 3
    df2 = bars([1, 2, 3, 2, 9], [0, 1, 2, 1, 8])
    assert len(ind.swings(df2)[0]) == 0  # peak at index 2 is beaten by a later bar


def test_classify_structure_cases():
    s = lambda vals: pd.Series(vals, dtype=float)
    up = ind.classify_structure(s([10, 12]), s([5, 6]), price=11)
    assert up["structure"] == "uptrend"
    down = ind.classify_structure(s([12, 10]), s([6, 5]), price=7)
    assert down["structure"] == "downtrend"
    rng = ind.classify_structure(s([10, 12, 11]), s([4, 6, 7]), price=6)  # lower high + higher low
    assert rng["structure"] == "range"
    assert (rng["range_low"], rng["range_high"]) == (4, 12)
    assert rng["range_pos"] == pytest.approx(25.0)
    broken = ind.classify_structure(s([10, 12]), s([5, 6]), price=5.5)
    assert broken["structure"] == "range (uptrend broken)"
    assert ind.classify_structure(s([1]), s([0]), price=1)["structure"] == "n/a"


@pytest.mark.parametrize("args, grade, four_h, daily", [
    (("long", 316.5, 314.56, 312.50, 53.5, 316.5, 299.91), "A", "aligned", "aligned"),   # BCH skill example
    (("long", 4143.1, 4171.65, 4210.76, 38.7, 4143.1, 4276.02), "C", "opposed", "opposed"),  # gold long
    (("short", 4143.1, 4171.65, 4210.76, 38.7, 4143.1, 4276.02), "A", "aligned", "aligned"),
    (("long", 110, 105, 100, 75, 110, 100), "B", "aligned", "aligned"),   # RSI stretched
    (("long", 110, 105, 100, 60, 90, 100), "B", "aligned", "opposed"),
    (("long", 104, 105, 100, 55, 110, 100), "B", "mixed", "aligned"),
    (("long", 104, 105, 100, 55, 90, 100), "C", "mixed", "opposed"),
    (("long", 110, 105, np.nan, 60, 110, 100), "B", "n/a", "aligned"),
])
def test_momentum_grade(args, grade, four_h, daily):
    g = ind.momentum_grade(*args)
    assert (g["grade"], g["4h"], g["daily"]) == (grade, four_h, daily)


def test_returns_corr_and_yield_diff():
    a = pd.Series(np.linspace(100, 130, 31) + np.sin(np.arange(31)), index=pd.date_range("2026-09-01", periods=31))
    rho, n = ind.returns_corr(a, a * 2)
    assert rho == pytest.approx(1.0) and n == 30
    y = pd.Series(4.0 - np.sin(np.arange(31)) * 0.1, index=a.index)
    rho_y, _ = ind.returns_corr(a, y, diff_b=True)
    assert rho_y < -0.5
    assert ind.returns_corr(a.head(5), a.head(5)) == (None, 4)


def test_daily_closes_follow_session_dates():
    idx = pd.DatetimeIndex(["2026-10-01 20:00", "2026-10-01 23:00"], tz="UTC")  # 16:00 and 19:00 ET
    df = pd.DataFrame({"close": [1.0, 2.0]}, index=idx)
    out = ind.daily_closes(df, levels.CME)  # 18:00 ET roll -> second bar is next session
    assert out.tolist() == [1.0, 2.0] and len(out) == 2


def test_summarize_on_synthetic_trend():
    n = 200
    closes = list(np.linspace(100, 200, n))
    df = bars([c + 1 for c in closes], [c - 1 for c in closes], closes=closes)
    s = ind.summarize(df, levels.CRYPTO)
    assert s["ema20"] > s["ema50"] and s["rsi14"] == 100.0
    assert s["grades"][0]["4h"] == "aligned" and s["grades"][1]["grade"] == "C"


def test_correlation_section_reports_fetch_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(ind.data, "fetch_yf_daily_close", boom)
    daily = {"BCH/USDT": pd.Series(np.arange(1.0, 40), index=pd.date_range("2026-08-01", periods=39))}
    out = ind.correlation_section(daily, ["BCH/USDT"])
    assert "fetch failed: offline" in out


def test_main_rejects_unknown_symbol(capsys):
    assert ind.main(["DOGE"]) == 2
    assert "Unknown symbol" in capsys.readouterr().out


def test_macd_on_linear_series_is_exact():
    # EMA(n) of a straight line lags by exactly (n-1)/2, so MACD = 12.5 - 5.5 = 7 and the histogram is 0.
    m = ind.macd(pd.Series(np.arange(200, dtype=float)))
    assert m["macd"].iloc[-1] == pytest.approx(7.0)
    assert m["signal"].iloc[-1] == pytest.approx(7.0)
    assert m["hist"].iloc[-1] == pytest.approx(0.0)
    assert np.isnan(m["signal"].iloc[30])  # signal needs 9 MACD values after the 26-bar seed


def test_volume_trend():
    assert ind.volume_trend(pd.Series([1.0] * 30 + [2.0] * 20)) == ("rising", pytest.approx(2 / 1.4))
    assert ind.volume_trend(pd.Series([2.0] * 30 + [1.0] * 20))[0] == "falling"
    assert ind.volume_trend(pd.Series([1.0] * 50))[0] == "flat"
    assert ind.volume_trend(pd.Series([1.0] * 10))[0] == "n/a"
    assert ind.volume_trend(pd.Series([0.0] * 60))[0] == "n/a"


def test_tf_state_and_alignment_score():
    assert ind.tf_state(110, 105, 100, 60) == "bull"
    assert ind.tf_state(90, 95, 100, 40) == "bear"
    assert ind.tf_state(104, 105, 100, 55) == "mixed"
    assert ind.tf_state(104, 105, np.nan, 55) == "n/a"
    states = {"15M": "bull", "1H": "bull", "4H": "mixed", "1D": "bear", "1W": "n/a"}
    assert ind.alignment_score(states, "long") == 1 and ind.alignment_score(states, "short") == -1


def test_timeframe_row_and_ema200_warmup():
    df = bars([c + 1 for c in range(100, 400)], [c - 1 for c in range(100, 400)], closes=list(range(100, 400)))
    row = ind.timeframe_row(df)
    assert row["state"] == "bull" and row["bars"] == 300 and row["ema200_note"] == "n/a"  # seed still ~37%
    assert row["ema9"] > row["ema21"] > row["ema50"] > row["ema200"]
    assert row["macd"] == pytest.approx(7.0)
    short = ind.timeframe_row(df.tail(150))
    assert np.isnan(short["ema200"]) and short["ema200_note"] == "n/a"


def test_timeframe_section_shows_failures_and_score():
    df = bars([c + 1 for c in range(100, 400)], [c - 1 for c in range(100, 400)], closes=list(range(100, 400)))
    text, info = ind.timeframe_section({"4H": df, "1D": df}, {"1W": "OKX timed out"}, ignore_volume=True)
    assert "| 1W | FETCH FAILED: OKX timed out" in text
    assert info["scores"] == {"long": 2, "short": -2}
    assert "ignore (token volume)" in text


def test_main_returns_1_when_a_timeframe_fails(monkeypatch, tmp_path, capsys):
    df = bars([c + 1 for c in range(100, 400)], [c - 1 for c in range(100, 400)], closes=list(range(100, 400)))
    df.attrs["fetched_at"] = "2026-10-04T19:00:00+00:00"

    def fake_get(sym, tf, bars):
        if tf == "1W":
            raise ind.fetch_prices.FetchError("BTC/USDT 1W: down. Fallback (AGENT.md F2)")
        return df
    monkeypatch.setattr(ind.fetch_prices, "get_ohlcv", fake_get)
    monkeypatch.setattr(ind.levels, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ind.data, "fetch_yf_daily_close", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    assert ind.main(["BTC/USDT"]) == 1
    out = capsys.readouterr().out
    assert "FETCH FAILED: BTC/USDT 1W" in out and "Alignment score" in out


def test_ema_quality_thresholds():
    assert ind.ema_quality(150, 200) == "n/a"
    assert ind.ema_quality(220, 200) == "n/a"     # gold weekly: seed weight ~82%
    assert ind.ema_quality(456, 200) == "approx"  # BTC weekly: ~8%
    assert ind.ema_quality(600, 200) == "approx"  # ~1.8%
    assert ind.ema_quality(700, 200) == ""        # < 1%


def test_anchored_vwap_restarts_each_period():
    idx = pd.DatetimeIndex(["2026-10-03 22:00", "2026-10-03 23:00", "2026-10-04 00:00", "2026-10-04 01:00"], tz="UTC")
    df = pd.DataFrame({"high": [11.0, 13, 21, 23], "low": [9.0, 11, 19, 21], "close": [10.0, 12, 20, 22],
                       "volume": [1.0, 3, 2, 2]}, index=idx)
    v = ind.anchored_vwap(df, levels.session_dates(df.index, levels.CRYPTO))
    # day 1: (10*1 + 12*3) / 4 = 11.5 ; day 2 restarts: (20*2 + 22*2) / 4 = 21
    assert v.tolist() == [10.0, 11.5, 20.0, 21.0]


def test_anchored_vwap_zero_volume_is_nan():
    idx = pd.date_range("2026-10-04", periods=2, freq="1h", tz="UTC")
    df = pd.DataFrame({"high": [1.0, 1], "low": [1.0, 1], "close": [1.0, 1], "volume": [0.0, 0]}, index=idx)
    assert ind.anchored_vwap(df, levels.session_dates(df.index, levels.CRYPTO)).isna().all()


def test_vwap_levels_session_week_month():
    # Hourly: Sun Sep 27 (prior week, prior month), then Sat Oct 3 and Sun Oct 4. Typical price = (H+L+C)/3.
    hourly = pd.concat([
        bars([30.0] * 24, [30.0] * 24, closes=[30.0] * 24, start="2026-09-27", freq="1h"),   # tp 30
        bars([10.0] * 24, [10.0] * 24, closes=[10.0] * 24, start="2026-10-03", freq="1h"),   # tp 10
        bars([10.0] * 24, [10.0] * 24, closes=[40.0] * 24, start="2026-10-04", freq="1h"),   # tp 20
    ])
    fine = bars([20.0] * 8, [20.0] * 8, closes=[20.0] * 8, start="2026-10-04 10:00", freq="15min")
    vw = ind.vwap_levels(fine, hourly, levels.CRYPTO)
    assert vw["session"] == pytest.approx(20.0)
    assert vw["week"] == pytest.approx(15.0)    # Oct 3 + Oct 4 (week Sep 28 - Oct 4); Sep 27 is the prior week
    assert vw["month"] == pytest.approx(15.0)   # October only; September bars excluded
    assert vw["notes"] == []
    partial = ind.vwap_levels(None, hourly.loc["2026-10-03":], levels.CRYPTO)
    assert partial["session"] is None and any("partial" in n for n in partial["notes"])


E4 = {"ema21": 100.0, "ema50": 95.0, "ema200": 90.0}
E1 = {"ema21": 98.0, "ema50": 92.0, "ema200": 85.0}
VW = {"session": 101.0, "week": 99.0, "month": 97.0}


def test_conviction_full_long_is_a():
    c = ind.conviction("long", 102.0, E4, E1, VW)
    assert (c["score"], c["ema_score"], c["vwap_score"], c["grade"], c["label"]) == (8, 5, 3, "A", "")
    s = ind.conviction("short", 102.0, E4, E1, VW)
    assert (s["score"], s["grade"], s["label"]) == (-8, "C", "counter-trend")


@pytest.mark.parametrize("price, score, grade", [
    (102.0, 8, "A"),   # above everything
    (100.5, 6, "A"),   # below session VWAP only: 8 - 2 = 6
    (98.5, 2, "C"),    # below 4H EMA21, session and weekly VWAP: 5 up, 3 down
    (96.0, -2, "C"),
])
def test_conviction_grade_boundaries(price, score, grade):
    c = ind.conviction("long", price, E4, E1, VW)
    assert (c["score"], c["grade"]) == (score, grade)


def test_conviction_b_band_and_na_checks():
    vw = {"session": None, "week": None, "month": None}
    c = ind.conviction("long", 102.0, E4, E1, vw)
    assert (c["score"], c["grade"], c["na"]) == (5, "B", 3)  # VWAP missing caps it at B
    e4 = dict(E4, ema200=None)
    c2 = ind.conviction("long", 102.0, e4, dict(E1, ema200=None), vw)
    assert c2["score"] == 3 and c2["na"] == 5


def test_ema_values_drops_unsettled_ema200():
    short = bars([c + 1 for c in range(100, 400)], [c - 1 for c in range(100, 400)], closes=list(range(100, 400)))
    v = ind.ema_values(short)
    assert v["ema21"] is not None and v["ema200"] is None  # 300 bars: seed weight ~37%
    assert ind.ema_values(None) == {"ema21": None, "ema50": None, "ema200": None}


def test_conviction_section_renders_and_flags_proxy_volume():
    n = 900
    closes = list(np.linspace(100, 200, n))
    df = bars([c + 1 for c in closes], [c - 1 for c in closes], closes=closes, start="2026-08-01", freq="1h")
    frames = {"4H": df, "1D": df, "1H": df, "15M": df.tail(40)}
    text, info = ind.conviction_section(frames, levels.CRYPTO, volume_is_proxy=True)
    assert info["conviction"]["long"]["score"] >= 6 and info["conviction"]["long"]["grade"] == "A"
    assert "Conviction long: **+" in text and "| Session VWAP |" in text
    assert "XAUT token volume" in text
