"""Fetch OHLCV for the watchlist on 5 timeframes: 15M, 1H, 4H, 1D, 1W.

Usage:  python tools/fetch_prices.py [SYMBOL ...] [--tf 15M 1H 4H 1D 1W] [--bars N] [--no-cache] [--json]

- Public, read-only endpoints only (OKX market data, yfinance). No API keys.
- Every result is stamped with its UTC fetch time and the age of the last bar (hours since that bar opened).
- Daily bars from yfinance use the official close, which can differ slightly from the last intraday bar.
- Results are cached for CACHE_TTL seconds (default 180) in tools/output/cache/ to avoid rate limits.
- Failures raise FetchError with the fallback to apply (AGENT.md F2). The CLI exits 1 if anything failed.
  The agent must then use the backup source or user-supplied prices, never invented numbers.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import data, levels  # noqa: E402

TIMEFRAMES = ["15M", "1H", "4H", "1D", "1W"]
TF_MINUTES = {"15M": 15, "1H": 60, "4H": 240, "1D": 1440, "1W": 10080}
OKX_BAR = {"15M": "15m", "1H": "1H", "4H": "4H", "1D": "1Dutc", "1W": "1Wutc"}
YF_INTERVAL = {"15M": "15m", "1H": "1h", "1D": "1d", "1W": "1wk"}
YF_MAX_DAYS = {"15M": 59, "1H": 729}           # yfinance intraday history limits
DEFAULT_BARS = 700                              # EMA200 seed weight < 1% after 700 bars
CACHE_DIR = ROOT / "tools" / "output" / "cache"
CACHE_TTL = 180
FALLBACK = ("Fallback (AGENT.md F2): try the backup source in memory/core.md; if that fails, ask the user "
            "for prices and mark levels 'as of user input'. Do not estimate.")


class FetchError(RuntimeError):
    pass


def history_days(src: levels.Source, tf: str, bars: int) -> int:
    """Calendar days needed to get `bars` bars, allowing for closed sessions."""
    if tf == "1W":
        return bars * 7 + 7
    if src.session is levels.CRYPTO:
        per_day, week_factor = 1440 / TF_MINUTES[tf], 1.0
    elif src.session is levels.EQUITY:
        per_day, week_factor = {"15M": 26, "1H": 7, "4H": 2, "1D": 1}[tf], 1.5  # weekends + holidays
    else:  # CME / spot gold: about 23h a day, 5 days a week
        per_day, week_factor = {"15M": 92, "1H": 23, "4H": 6, "1D": 1}[tf], 1.5
    return int(bars / per_day * week_factor) + 3


def _fetch_raw(src: levels.Source, tf: str, start: pd.Timestamp, now: pd.Timestamp) -> pd.DataFrame:
    if tf == "4H":
        return levels.load_4h(src, now, start=start)  # same pipeline that tools/verify.py checked
    if src.provider == "okx":
        pages = int((now - start) / pd.Timedelta(minutes=TF_MINUTES[tf]) / 100) + 2
        df = data.fetch_okx_candles(src.instrument, start=start, bar=OKX_BAR[tf], max_pages=pages)
        if src.drop_weekend:
            if tf == "1D":
                df = df[df.index.dayofweek < 5]  # XAUT trades weekends; spot gold doesn't
            elif tf != "1W":
                df = levels.drop_gold_weekend(df, bar_minutes=TF_MINUTES[tf])
        return df
    days = (now - start).days + 2
    period = f"{min(days, YF_MAX_DAYS[tf])}d" if tf in YF_MAX_DAYS else f"{days}d"
    return data.fetch_yf_hourly(src.instrument, period=period, interval=YF_INTERVAL[tf])


def _cache_paths(symbol: str, tf: str) -> tuple[Path, Path]:
    key = f"{symbol.replace('/', '')}_{tf}"
    return CACHE_DIR / f"{key}.pkl", CACHE_DIR / f"{key}.json"


def get_ohlcv(symbol: str, tf: str, bars: int = DEFAULT_BARS, use_cache: bool = True,
              ttl: int = CACHE_TTL, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """OHLCV for one watchlist symbol and timeframe. Raises FetchError on any failure.

    df.attrs: symbol, tf, source, fetched_at (UTC ISO), cached (bool), last_bar_age_hours.
    """
    if symbol not in levels.WATCHLIST:
        raise FetchError(f"{symbol}: not on the watchlist ({', '.join(levels.WATCHLIST)})")
    if tf not in TIMEFRAMES:
        raise FetchError(f"{tf}: unsupported timeframe ({', '.join(TIMEFRAMES)})")
    src = levels.WATCHLIST[symbol]
    pkl, meta_path = _cache_paths(symbol, tf)

    if use_cache and pkl.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if time.time() - meta["fetched_epoch"] <= ttl and meta["bars_requested"] >= bars:
            df = pd.read_pickle(pkl).tail(bars)
            return _stamp(df, symbol, tf, meta.get("source", src.label), meta["fetched_at"], cached=True, now=now)

    now = now or pd.Timestamp.now(tz="UTC")
    start = now - pd.Timedelta(days=history_days(src, tf, bars))
    used = src.label
    try:
        df = _fetch_raw(src, tf, start, now)
        if df is None or df.empty:
            raise RuntimeError("returned no data")
        if tf == "4H" and df.attrs.get("feed"):
            used = df.attrs["feed"]  # load_4h already fell back to the backup if needed
    except Exception as exc:
        if src.backup is None or tf == "4H":
            raise FetchError(f"{symbol} {tf}: {src.label} failed: {exc}. {FALLBACK}") from exc
        try:
            df = _fetch_raw(src.backup, tf, start, now)
            if df is None or df.empty:
                raise RuntimeError("returned no data")
            used = f"{src.backup.label} (primary {src.label} failed: {str(exc)[:60]})"
        except Exception as exc2:
            raise FetchError(f"{symbol} {tf}: {src.label} failed: {exc}; backup {src.backup.label} failed: "
                             f"{exc2}. {FALLBACK}") from exc2
    df = df[[c for c in ("open", "high", "low", "close", "volume", "confirmed") if c in df]].tail(bars)

    fetched_at = now.isoformat()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_pickle(pkl)
    meta_path.write_text(json.dumps({"fetched_at": fetched_at, "fetched_epoch": time.time(),
                                     "bars_requested": bars, "source": used}), encoding="utf-8")
    return _stamp(df, symbol, tf, used, fetched_at, cached=False, now=now)


def _stamp(df: pd.DataFrame, symbol: str, tf: str, source: str, fetched_at: str,
           cached: bool, now: pd.Timestamp | None) -> pd.DataFrame:
    now = now or pd.Timestamp.now(tz="UTC")
    df = df.copy()
    df.attrs.update(symbol=symbol, tf=tf, source=source, fetched_at=fetched_at, cached=cached,
                    last_bar_age_hours=round((now - df.index[-1]).total_seconds() / 3600, 2))
    return df


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--tf", nargs="+", default=TIMEFRAMES, choices=TIMEFRAMES)
    ap.add_argument("--bars", type=int, default=DEFAULT_BARS)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    symbols = [s.upper() for s in a.symbols] or list(levels.WATCHLIST)

    levels.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{pd.Timestamp.now(tz='UTC'):%Y-%m-%d_%H%M}"
    rows, failures = [], []
    for sym in symbols:
        for tf in a.tf:
            try:
                df = get_ohlcv(sym, tf, bars=a.bars, use_cache=not a.no_cache)
            except FetchError as exc:
                failures.append(str(exc))
                continue
            path = levels.OUTPUT_DIR / f"{stamp}_{sym.replace('/', '')}_{tf}.csv"
            df.to_csv(path)
            rows.append({"symbol": sym, "tf": tf, "bars": len(df), "first": f"{df.index[0]:%Y-%m-%d %H:%M}",
                         "last": f"{df.index[-1]:%Y-%m-%d %H:%M}", "close": float(df["close"].iloc[-1]),
                         "last_bar_age_h": df.attrs["last_bar_age_hours"], "fetched_at": df.attrs["fetched_at"],
                         "cached": df.attrs["cached"], "source": df.attrs["source"], "file": path.name})
    if a.json:
        print(json.dumps({"results": rows, "failures": failures}, indent=2))
    else:
        print("| Symbol | TF | Bars | First -> last bar (UTC) | Close | Bar age h | Fetched (UTC) | Cached |")
        print("|---|---|---|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['symbol']} | {r['tf']} | {r['bars']} | {r['first']} -> {r['last']} | {r['close']:,.2f} | "
                  f"{r['last_bar_age_h']} | {r['fetched_at'][:16]} | {'yes' if r['cached'] else 'no'} |")
        for f in failures:
            print(f"FETCH FAILED: {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
