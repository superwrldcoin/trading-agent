import pandas as pd
import pytest

from tools import fetch_prices as fp
from tools import levels

NOW = pd.Timestamp("2026-10-04 19:00", tz="UTC")


def frame(start="2026-10-01", periods=10, freq="1h"):
    idx = pd.date_range(start, periods=periods, freq=freq, tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": range(periods), "volume": 1.0}, index=idx)


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(levels, "OUTPUT_DIR", tmp_path)


def test_history_days_by_session():
    assert fp.history_days(levels.WATCHLIST["BTC/USDT"], "1H", 600) == 28    # 600/24 + 3
    assert fp.history_days(levels.WATCHLIST["MSFT"], "4H", 600) == 453       # 600/2 * 1.5 + 3
    assert fp.history_days(levels.WATCHLIST["SI"], "1W", 600) == 4207


def test_stamps_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(fp, "_fetch_raw", lambda src, tf, start, now: calls.append(tf) or frame())
    df = fp.get_ohlcv("BTC/USDT", "1H", bars=5, now=NOW)
    assert len(df) == 5 and df.attrs["fetched_at"] == NOW.isoformat() and df.attrs["cached"] is False
    assert df.attrs["last_bar_age_hours"] == pytest.approx((NOW - df.index[-1]).total_seconds() / 3600)
    again = fp.get_ohlcv("BTC/USDT", "1H", bars=5, now=NOW)
    assert again.attrs["cached"] is True and calls == ["1H"]       # second call served from cache
    assert again.attrs["fetched_at"] == NOW.isoformat()            # keeps the original fetch time


def test_cache_expires_and_bypass(monkeypatch):
    calls = []
    monkeypatch.setattr(fp, "_fetch_raw", lambda *a: calls.append(1) or frame())
    fp.get_ohlcv("BTC/USDT", "1H", bars=5, now=NOW)
    fp.get_ohlcv("BTC/USDT", "1H", bars=5, now=NOW, ttl=-1)        # expired
    fp.get_ohlcv("BTC/USDT", "1H", bars=5, now=NOW, use_cache=False)
    fp.get_ohlcv("BTC/USDT", "1H", bars=8, now=NOW)                # more bars than cached -> refetch
    assert len(calls) == 4


def test_fails_loudly_with_fallback(monkeypatch):
    def boom(*a):
        raise ConnectionError("HTTP 451")
    monkeypatch.setattr(fp, "_fetch_raw", boom)
    with pytest.raises(fp.FetchError, match=r"BCH/USDT 4H: OKX BCH-USDT failed: HTTP 451.*Fallback \(AGENT.md F2\)"):
        fp.get_ohlcv("BCH/USDT", "4H", now=NOW)


def test_empty_data_is_an_error(monkeypatch):
    monkeypatch.setattr(fp, "_fetch_raw", lambda *a: frame().iloc[:0])
    with pytest.raises(fp.FetchError, match="returned no data"):
        fp.get_ohlcv("MSFT", "1D", now=NOW)


def test_rejects_unknown_symbol_and_tf():
    with pytest.raises(fp.FetchError, match="not on the watchlist"):
        fp.get_ohlcv("DOGE", "1H")
    with pytest.raises(fp.FetchError, match="unsupported timeframe"):
        fp.get_ohlcv("MSFT", "2H")


def test_okx_dispatch_uses_utc_bars_and_drops_gold_weekend_days(monkeypatch):
    seen = {}

    def fake_okx(inst, start, bar, max_pages):
        seen["bar"] = bar
        return frame("2026-09-28", periods=7, freq="1D")  # Mon..Sun
    monkeypatch.setattr(fp.data, "fetch_okx_candles", fake_okx)
    out = fp._fetch_raw(levels.WATCHLIST["XAU/USD"], "1D", NOW - pd.Timedelta(days=10), NOW)
    assert seen["bar"] == "1Dutc"
    assert out.index.dayofweek.max() == 4  # Sat/Sun removed
    fp._fetch_raw(levels.WATCHLIST["BTC/USDT"], "1W", NOW - pd.Timedelta(days=100), NOW)
    assert seen["bar"] == "1Wutc"


def test_yfinance_dispatch_caps_intraday_period(monkeypatch):
    seen = {}

    def fake_yf(ticker, period, interval):
        seen.update(ticker=ticker, period=period, interval=interval)
        return frame()
    monkeypatch.setattr(fp.data, "fetch_yf_hourly", fake_yf)
    fp._fetch_raw(levels.WATCHLIST["GLD"], "15M", NOW - pd.Timedelta(days=200), NOW)
    assert seen == {"ticker": "GLD", "period": "59d", "interval": "15m"}
    fp._fetch_raw(levels.WATCHLIST["SI"], "1W", NOW - pd.Timedelta(days=3000), NOW)
    assert seen["interval"] == "1wk" and seen["period"] == "3002d"


def test_cli_exit_code_and_failure_message(monkeypatch, capsys):
    def maybe(src, tf, start, now):
        if tf == "1W":
            raise TimeoutError("timed out")
        return frame()
    monkeypatch.setattr(fp, "_fetch_raw", maybe)
    assert fp.main(["BTC/USDT", "--tf", "1H", "1W"]) == 1
    out = capsys.readouterr().out
    assert "| BTC/USDT | 1H |" in out and "FETCH FAILED: BTC/USDT 1W" in out
    assert fp.main(["BTC/USDT", "--tf", "1H"]) == 0
