import numpy as np
import pandas as pd
import pytest

from tools import crypto_feeds as cf
from tools import ratios as rt


def series(vals, start="2025-06-01"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq="1D"), dtype=float)


def test_parse_formula_and_window():
    assert rt.parse_formula("GC=F / SI=F") == ("GC=F", "SI=F")
    assert rt.sma_window("sma_50d") == 50
    with pytest.raises(ValueError):
        rt.parse_formula("GC=F * SI=F")
    with pytest.raises(ValueError):
        rt.sma_window("ema_20")


def test_compute_rising_ratio():
    n = 120
    a = series(np.linspace(100, 160, n))
    b = series(np.full(n, 100.0))
    r = rt.compute(a, b, 50)
    assert r["value"] == pytest.approx(1.6) and r["trend"] == "rising" and r["pct_vs_sma"] > 0
    assert r["chg_20d_pct"] == pytest.approx((1.6 / (a.iloc[-21] / 100) - 1) * 100)
    falling = rt.compute(b, a, 50)
    assert falling["trend"] == "falling"
    assert rt.compute(a.head(30), b.head(30), 50)["value"] is None


def test_run_uses_universe_and_context(monkeypatch):
    n = 120
    data = {"GC=F": series(np.full(n, 4100.0)), "SI=F": series(np.linspace(50, 60, n)),
            "MSFT": series(np.full(n, 500.0)), "QQQ": series(np.full(n, 700.0))}
    rows = rt.run(for_id="SI", fetch=lambda s, period: data[s])
    assert [r["name"] for r in rows] == ["gold_silver_ratio"] and rows[0]["trend"] == "falling"
    rows2 = rt.run(["msft_qqq"], fetch=lambda s, period: data[s])
    assert rows2[0]["value"] == pytest.approx(500 / 700)

    def boom(s, period):
        raise RuntimeError("offline")
    bad = rt.run(["msft_qqq"], fetch=boom)
    assert bad[0]["value"] is None and "offline" in bad[0]["note"]
    assert "n/a" in rt.render(bad)


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def fake_get(url, params=None, timeout=None):
    if url.endswith("/public/funding-rate"):
        return FakeResp({"code": "0", "data": [{"fundingRate": "0.0006", "fundingTime": "1791158400000"}]})
    if url.endswith("/funding-rate-history"):
        return FakeResp({"code": "0", "data": [{"realizedRate": "0.0004", "fundingRate": "0.0004"}] * 21})
    if url.endswith("/public/open-interest"):
        return FakeResp({"code": "0", "data": [{"oiUsd": "2450000000", "oiCcy": "28400"}]})
    if url.endswith("/open-interest-volume"):
        return FakeResp({"code": "0", "data": [["1791129600000", "0", "1"]] * 10})
    if url.endswith("/long-short-account-ratio"):
        return FakeResp({"code": "0", "data": [["1791151200000", "2.4"]] + [["1791147600000", "1.9"]] * 30})
    if "coingecko" in url:
        return FakeResp({"data": {"market_cap_percentage": {"btc": 59.3}, "market_cap_change_percentage_24h_usd": -1.9}})
    raise AssertionError(url)


def test_crypto_feeds_with_fake_api(monkeypatch):
    monkeypatch.setattr(cf.requests, "get", fake_get)
    r = cf.run("BTC/USDT")
    f = r["feeds"]
    assert f["funding_rate"]["reading"].startswith("crowded longs")
    assert f["funding_rate"]["annualized_pct"] == pytest.approx(0.0006 * 3 * 365 * 100)
    assert f["open_interest"]["chg_7d_pct"] is None and "unavailable" in f["open_interest"]["history_note"]
    assert f["long_short_ratio"]["reading"].startswith("most accounts long")
    assert f["btc_dominance"]["btc_dominance_pct"] == 59.3
    text = cf.render(r)
    assert "Funding" in text and "never changes the grade" in text


def test_bch_feeds_follow_universe_and_errors_are_soft(monkeypatch):
    def flaky(url, params=None, timeout=None):
        if "coingecko" in url:
            raise ConnectionError("rate limited")
        return fake_get(url, params, timeout)
    monkeypatch.setattr(cf.requests, "get", flaky)
    r = cf.run("BCH/USDT")
    assert set(r["feeds"]) == {"funding_rate", "open_interest", "btc_dominance"}   # no long_short in BCH's context
    assert "error" in r["feeds"]["btc_dominance"]
    with pytest.raises(ValueError, match="not a crypto"):
        cf.run("MSFT")
