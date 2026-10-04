import pandas as pd
import pytest

from tools import levels


def frame(index_ny: list[str], highs, lows, tz="America/New_York"):
    idx = pd.DatetimeIndex(pd.to_datetime(index_ny)).tz_localize(tz).tz_convert("UTC")
    return pd.DataFrame({"open": lows, "high": highs, "low": lows, "close": highs, "volume": 1.0}, index=idx)


def test_resample_4h_equity_anchors_at_open():
    hours = [f"2026-10-01 {h}" for h in ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]]
    hourly = frame(hours, highs=[10, 11, 12, 13, 14, 15, 16], lows=[1, 2, 3, 4, 5, 6, 7])
    out = levels.resample_4h(hourly, "9h30min")
    assert len(out) == 2
    first, second = out.iloc[0], out.iloc[1]
    assert (first["high"], first["low"], first["close"]) == (13, 1, 13)
    assert (second["high"], second["low"], second["open"]) == (16, 5, 5)
    assert out.index[0] == pd.Timestamp("2026-10-01 13:30", tz="UTC")  # 09:30 EDT


def test_resample_4h_is_dst_safe():
    # Same 09:30 ET open on both sides of the Nov 1 2026 DST change.
    hourly = frame(["2026-10-30 09:30", "2026-11-02 09:30"], highs=[1, 2], lows=[1, 2])
    out = levels.resample_4h(hourly, "9h30min")
    assert len(out) == 2


def test_drop_gold_weekend():
    bars = ["2026-10-02 12:00", "2026-10-02 16:00", "2026-10-02 20:00",  # Fri: keep, keep (overlaps open), drop
            "2026-10-03 12:00",                                         # Sat: drop
            "2026-10-04 12:00", "2026-10-04 16:00"]                     # Sun: drop, keep (overlaps 18:00 open)
    df = frame(bars, highs=[1] * 6, lows=[1] * 6)
    kept = levels.drop_gold_weekend(df).index.tz_convert("America/New_York").strftime("%a %H:%M").tolist()
    assert kept == ["Fri 12:00", "Fri 16:00", "Sun 16:00"]


def test_session_dates_cme_evening_belongs_to_next_day():
    idx = frame(["2026-10-04 18:00", "2026-10-05 10:00"], [1, 1], [1, 1]).index
    dates = levels.session_dates(idx, levels.CME)
    assert [str(d.date()) for d in dates] == ["2026-10-05", "2026-10-05"]


def test_session_dates_crypto_uses_utc():
    idx = pd.DatetimeIndex(["2026-10-03 23:00", "2026-10-04 01:00"], tz="UTC")
    assert [str(d.date()) for d in levels.session_dates(idx, levels.CRYPTO)] == ["2026-10-03", "2026-10-04"]


def test_reference_levels_crypto():
    idx = pd.DatetimeIndex([
        "2026-08-31 00:00",  # August, week of Aug 31 (Mon)
        "2026-09-29 00:00",  # Tue, week Sep 28
        "2026-10-02 00:00",  # Fri, same week
        "2026-10-03 00:00", "2026-10-03 12:00",  # Sat = previous day
        "2026-10-04 08:00",  # Sun = current day, current week Sep 28 - Oct 4
    ], tz="UTC")
    df = pd.DataFrame({"high": [50, 40, 30, 22, 25, 99], "low": [5, 20, 15, 12, 11, 1]}, index=idx)
    lv = levels.reference_levels(df, levels.CRYPTO)
    assert (lv["PDH"], lv["PDL"], lv["day"]) == (25, 11, "2026-10-03")
    assert (lv["PWH"], lv["PWL"]) == (50, 5) and lv["week"].startswith("2026-08-31")
    assert (lv["PMthH"], lv["PMthL"], lv["month"]) == (40, 20, "2026-09")


def test_reference_levels_month_uses_previous_calendar_month():
    idx = pd.DatetimeIndex(["2026-08-15", "2026-09-10", "2026-09-20", "2026-10-02"], tz="UTC")
    df = pd.DataFrame({"high": [100, 70, 80, 90], "low": [1, 30, 20, 40]}, index=idx)
    lv = levels.reference_levels(df, levels.CRYPTO)
    assert (lv["PMthH"], lv["PMthL"], lv["month"]) == (80, 20, "2026-09")


def test_reference_levels_missing_history_returns_none():
    df = pd.DataFrame({"high": [1.0], "low": [0.5]}, index=pd.DatetimeIndex(["2026-10-04"], tz="UTC"))
    lv = levels.reference_levels(df, levels.CRYPTO)
    assert lv["PDH"] is None and lv["PWH"] is None and lv["PMthH"] is None


def test_premium_pct():
    assert levels.premium_pct(4143.10, 4139.24) == pytest.approx(0.0933, abs=1e-3)


def test_history_start_covers_previous_month():
    assert levels.history_start(pd.Timestamp("2026-10-04 18:00", tz="UTC")) == pd.Timestamp("2026-08-25", tz="UTC")


def test_main_rejects_unknown_symbol(capsys):
    assert levels.main(["DOGE"]) == 2
    assert "Unknown symbol" in capsys.readouterr().out


def test_distance_rows_sorted_with_pct_and_atr():
    lv = {"PDH": 383.70, "PDL": 380.36, "day": "2026-10-01", "PWH": 400.78, "PWL": 389.05, "week": "w",
          "PMthH": 413.54, "PMthL": 376.88, "month": "2026-09"}
    rows = levels.distance_rows(lv, last=380.18, atr14=4.11)
    assert [r["level"] for r in rows] == ["PMthH", "PWH", "PWL", "PDH", "PDL", "PMthL"]
    pmthl = rows[-1]
    assert pmthl["dist_pct"] == pytest.approx(-0.8680, abs=1e-3)
    assert pmthl["dist_atr"] == pytest.approx(-0.80, abs=0.01)  # matches the GLD acceptance-test report


def test_distance_rows_handles_missing():
    lv = {"PDH": None, "PDL": None, "day": None, "PWH": 10.0, "PWL": 9.0, "week": "w",
          "PMthH": None, "PMthL": None, "month": None}
    rows = levels.distance_rows(lv, last=9.5, atr14=None)
    assert rows[0]["level"] == "PWH" and rows[0]["dist_atr"] is None
    assert rows[-1]["value"] is None and rows[-1]["dist_pct"] is None
