"""Market data fetchers. All network access lives here so analysis code can be tested on fixtures.

Sources (all keyless, see memory/core.md):
- OKX public market API: native candles for the crypto spot pairs and XAUT-USDT (gold proxy); ticker, mark price
  and contract spec for the USDT perpetuals the user trades.
- yfinance: 1H candles for MSFT and SI=F, resampled to 4H in tools/levels.py.
- Swissquote public quote feed: live spot XAU/USD bid/ask, used only to cross-check XAUT.
"""
from __future__ import annotations

import pandas as pd
import requests

OKX_URL = "https://www.okx.com/api/v5/market/history-candles"
SWISSQUOTE_URL = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/{base}/{quote}"
COLUMNS = ["open", "high", "low", "close", "volume"]
TIMEOUT = 15


def parse_okx_candles(rows: list[list[str]]) -> pd.DataFrame:
    """OKX rows are [ts_ms, o, h, l, c, vol, volCcy, volCcyQuote, confirm], newest first."""
    if not rows:
        return pd.DataFrame(columns=[*COLUMNS, "confirmed"], index=pd.DatetimeIndex([], tz="UTC", name="ts"))
    df = pd.DataFrame([r[:6] for r in rows], columns=["ts", *COLUMNS])
    df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    df[COLUMNS] = df[COLUMNS].astype(float)
    df["confirmed"] = [len(r) > 8 and r[8] == "1" for r in rows]
    return df.set_index("ts").sort_index()


def fetch_okx_candles(inst_id: str, start: pd.Timestamp, bar: str = "4H", max_pages: int = 20) -> pd.DataFrame:
    """Page backwards through OKX history until `start` (UTC) is covered."""
    rows: list[list[str]] = []
    after = None
    for _ in range(max_pages):
        params = {"instId": inst_id, "bar": bar, "limit": 100}
        if after:
            params["after"] = after
        resp = requests.get(OKX_URL, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX error for {inst_id}: {payload.get('msg')}")
        page = payload["data"]
        if not page:
            break
        rows += page
        after = page[-1][0]
        if pd.Timestamp(int(after), unit="ms", tz="UTC") <= start:
            break
    df = parse_okx_candles(rows)
    return df[~df.index.duplicated()]


OKX_API = "https://www.okx.com/api/v5"


def _okx_get(path: str, params: dict) -> list[dict]:
    resp = requests.get(f"{OKX_API}{path}", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("code") != "0" or not payload.get("data"):
        raise RuntimeError(f"OKX {path} {params}: {payload.get('msg') or 'no data'}")
    return payload["data"]


def fetch_okx_last(inst_id: str) -> float:
    """Last traded price for a spot pair or a swap."""
    return float(_okx_get("/market/ticker", {"instId": inst_id})[0]["last"])


def fetch_okx_mark(inst_id: str) -> float:
    """Mark price of a swap (OKX liquidates on mark, not last)."""
    return float(_okx_get("/public/mark-price", {"instType": "SWAP", "instId": inst_id})[0]["markPx"])


def fetch_okx_swap_spec(inst_id: str) -> dict:
    """Contract spec of an OKX swap: ctVal (coins per contract), lotSz, minSz, maxLever."""
    d = _okx_get("/public/instruments", {"instType": "SWAP", "instId": inst_id})[0]
    return {"ct_val": float(d["ctVal"]), "ct_val_ccy": d["ctValCcy"], "lot_sz": float(d["lotSz"]),
            "min_sz": float(d["minSz"]), "max_lever": float(d["lever"]) if d.get("lever") else None}


def fetch_yf_hourly(ticker: str, period: str = "90d", interval: str = "1h") -> pd.DataFrame:
    """Intraday candles from yfinance (default 1H) with a UTC index and lowercase OHLCV columns.

    yfinance limits: 1h up to 730 days, 15m/5m up to 60 days.
    """
    import yfinance as yf

    hist = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
    if hist.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    df = hist.rename(columns=str.lower)[COLUMNS]
    df.index = df.index.tz_convert("UTC")
    df.index.name = "ts"
    return df


def fetch_yf_daily_close(ticker: str, period: str = "120d") -> pd.Series:
    """Daily closes from yfinance, indexed by naive exchange-calendar date."""
    import yfinance as yf

    hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False)
    if hist.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker}")
    close = hist["Close"]
    close.index = pd.DatetimeIndex(close.index.date, name="date")
    return close.rename(ticker)


FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"


def parse_fred_csv(text: str) -> pd.Series:
    from io import StringIO
    df = pd.read_csv(StringIO(text))
    date_col, val_col = df.columns[0], df.columns[1]
    s = pd.to_numeric(df[val_col], errors="coerce")
    s.index = pd.DatetimeIndex(pd.to_datetime(df[date_col]), name="date")
    return s.dropna().rename(val_col)


def fetch_fred_series(series: str) -> pd.Series:
    """Daily FRED series: keyless CSV first; if that fails and FRED_API_KEY is set in the environment, the official API.
    The key is read from the environment only, never requested, stored, or printed."""
    import os
    try:
        resp = requests.get(FRED_CSV_URL.format(series=series), timeout=TIMEOUT)
        resp.raise_for_status()
        s = parse_fred_csv(resp.text)
        if s.empty:
            raise RuntimeError("empty CSV")
        return s
    except Exception as csv_exc:
        key = os.environ.get("FRED_API_KEY")
        if not key:
            raise RuntimeError(f"FRED {series} unavailable (keyless CSV failed: {csv_exc}; no FRED_API_KEY in env)")
        resp = requests.get(FRED_API_URL, params={"series_id": series, "api_key": key, "file_type": "json"},
                            timeout=TIMEOUT)
        resp.raise_for_status()
        obs = resp.json()["observations"]
        s = pd.Series({pd.Timestamp(o["date"]): pd.to_numeric(o["value"], errors="coerce") for o in obs}).dropna()
        s.index.name = "date"
        return s.rename(series)


def parse_swissquote_mid(payload: list[dict]) -> float:
    prices = payload[0]["spreadProfilePrices"][0]
    return (prices["bid"] + prices["ask"]) / 2


def fetch_swissquote_mid(base: str, quote: str) -> float:
    resp = requests.get(SWISSQUOTE_URL.format(base=base, quote=quote), timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_swissquote_mid(resp.json())
