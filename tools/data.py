"""Market data fetchers. All network access lives here so analysis code can be tested on fixtures.

Sources (all keyless, see memory/core.md):
- OKX public market API: native 4H candles for XAUT-USDT (gold proxy) and BCH-USDT.
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


def fetch_yf_hourly(ticker: str, period: str = "90d") -> pd.DataFrame:
    """1H candles from yfinance with a UTC index and lowercase OHLCV columns."""
    import yfinance as yf

    hist = yf.Ticker(ticker).history(period=period, interval="1h", auto_adjust=False)
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


def parse_swissquote_mid(payload: list[dict]) -> float:
    prices = payload[0]["spreadProfilePrices"][0]
    return (prices["bid"] + prices["ask"]) / 2


def fetch_swissquote_mid(base: str, quote: str) -> float:
    resp = requests.get(SWISSQUOTE_URL.format(base=base, quote=quote), timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_swissquote_mid(resp.json())
