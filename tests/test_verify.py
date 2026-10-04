import pandas as pd
import pytest

from tools import levels, verify


def fine_bars(start: str, periods: int, freq: str = "15min") -> pd.DataFrame:
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    base = pd.Series(range(periods), index=idx, dtype=float) + 100
    return pd.DataFrame({"open": base, "high": base + 0.5, "low": base - 0.5, "close": base, "volume": 1.0})


def test_aggregate_4h_okx_uses_utc_bins():
    fine = fine_bars("2026-10-03 00:00", 32)  # two full 4H bins
    out = verify.aggregate_4h(fine, levels.WATCHLIST["BCH/USDT"])
    assert list(out.index) == [pd.Timestamp("2026-10-03 00:00", tz="UTC"), pd.Timestamp("2026-10-03 04:00", tz="UTC")]
    assert out.iloc[0]["high"] == 115.5 and out.iloc[0]["low"] == 99.5 and out.iloc[0]["close"] == 115
    assert out.iloc[1]["open"] == 116 and out["volume"].tolist() == [16.0, 16.0]


def test_aggregate_4h_gold_drops_weekend():
    fine = fine_bars("2026-10-02 12:00", 16 * 6)  # Fri 08:00 ET through Sat
    out = verify.aggregate_4h(fine, levels.WATCHLIST["XAU/USD"])
    days = out.index.tz_convert("America/New_York").strftime("%a").unique().tolist()
    assert days == ["Fri"]


def test_compare_bars_flags_mismatch_and_skips_edges():
    idx = pd.date_range("2026-10-01", periods=5, freq="4h", tz="UTC")
    native = pd.DataFrame({"high": [10.0] * 5, "low": [9.0] * 5, "close": [9.5] * 5}, index=idx)
    rebuilt = native.copy()
    rebuilt.iloc[0, 0] = 50.0   # first rebuilt bin: skipped (may be partial)
    rebuilt.iloc[2, 0] = 10.1   # 1% off: mismatch
    rebuilt.iloc[4, 1] = 1.0    # newest common bar: skipped (may be forming)
    res = verify.compare_bars(native, rebuilt)
    assert res["bars"] == 3
    assert list(res["mismatches"].index) == [idx[2]]
    assert res["max_diff_pct"] == pytest.approx(1.0)


def test_compare_bars_no_overlap():
    idx = pd.date_range("2026-10-01", periods=2, freq="4h", tz="UTC")
    df = pd.DataFrame({"high": [1.0, 1], "low": [1.0, 1], "close": [1.0, 1]}, index=idx)
    assert verify.compare_bars(df, df.shift(freq="1D"))["bars"] == 0


def test_compare_levels():
    native = {"PDH": 100.0, "PDL": 90.0, "PWH": None}
    rebuilt = {"PDH": 100.01, "PDL": 89.0, "PWH": 5.0}
    rows = {r["level"]: r for r in verify.compare_levels(native, rebuilt, ["PDH", "PDL", "PWH"])}
    assert rows["PDH"]["ok"] and rows["PDH"]["diff_pct"] == pytest.approx(0.01)
    assert not rows["PDL"]["ok"]
    assert rows["PWH"]["diff_pct"] is None and not rows["PWH"]["ok"]


def test_main_rejects_unknown_symbol(capsys):
    assert verify.main(["DOGE"]) == 2
    assert "Unknown symbol" in capsys.readouterr().out
