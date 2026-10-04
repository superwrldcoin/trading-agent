import pandas as pd
import pytest

from tools import data


def okx_row(ts: str, o, h, l, c, confirm="1"):
    ms = str(int(pd.Timestamp(ts, tz="UTC").timestamp() * 1000))
    return [ms, str(o), str(h), str(l), str(c), "10", "0", "0", confirm]


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_parse_okx_candles_sorts_and_types():
    rows = [okx_row("2026-10-02 04:00", 2, 3, 1, 2.5, confirm="0"),
            okx_row("2026-10-02 00:00", 1, 2, 0.5, 2)]
    df = data.parse_okx_candles(rows)
    assert list(df.index) == [pd.Timestamp("2026-10-02 00:00", tz="UTC"), pd.Timestamp("2026-10-02 04:00", tz="UTC")]
    assert df["close"].tolist() == [2.0, 2.5]
    assert df["confirmed"].tolist() == [True, False]


def test_fetch_okx_candles_pages_until_start(monkeypatch):
    pages = [
        [okx_row("2026-10-02 08:00", 1, 1, 1, 1), okx_row("2026-10-02 04:00", 1, 1, 1, 1)],
        [okx_row("2026-10-02 00:00", 1, 1, 1, 1), okx_row("2026-10-01 20:00", 1, 1, 1, 1)],
        [okx_row("2026-10-01 16:00", 1, 1, 1, 1)],
    ]
    calls = []

    def fake_get(url, params, timeout):
        calls.append(params)
        return FakeResp({"code": "0", "data": pages[len(calls) - 1]})

    monkeypatch.setattr(data.requests, "get", fake_get)
    df = data.fetch_okx_candles("BCH-USDT", start=pd.Timestamp("2026-10-01 22:00", tz="UTC"))
    assert len(calls) == 2  # second page reached the start, so no third request
    assert "after" not in calls[0] and calls[1]["after"] == pages[0][-1][0]
    assert len(df) == 4 and df.index.is_monotonic_increasing


def test_fetch_okx_candles_raises_on_api_error(monkeypatch):
    monkeypatch.setattr(data.requests, "get", lambda *a, **k: FakeResp({"code": "51001", "msg": "bad inst", "data": []}))
    with pytest.raises(RuntimeError, match="bad inst"):
        data.fetch_okx_candles("NOPE", start=pd.Timestamp("2026-10-01", tz="UTC"))


def test_parse_swissquote_mid():
    payload = [{"spreadProfilePrices": [{"bid": 4138.9, "ask": 4139.5}]}]
    assert data.parse_swissquote_mid(payload) == pytest.approx(4139.2)
