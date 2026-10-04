"""4H indicators, swings, structure, momentum grade, and driver correlations for the watchlist.

Usage:  python tools/indicators.py [SYMBOL ...] [--days N]     (default: whole watchlist, 120 days)
Writes a markdown report to tools/output/ and prints it. Definitions follow skills/
(multi-timeframe-momentum, levels-and-entries, market-structure, macro-and-catalysts).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import data, levels  # noqa: E402

DEFAULT_DAYS = 120
CORR_WINDOW = 30
SWING_SIDE = 2
# EMA/RSI need about 3x their period in bars before values settle.
SETTLE_4H = 150
SETTLE_DAILY = 60

# (watchlist symbol, yfinance driver ticker, use_diff). Yields are compared by change in level, not % change.
DRIVERS = [
    ("XAU/USD", "DX-Y.NYB", False),
    ("XAU/USD", "^TNX", True),
    ("SI", "DX-Y.NYB", False),
    ("MSFT", "^NDX", False),
    ("MSFT", "^TNX", True),
    ("BCH/USDT", "BTC-USD", False),
]
PAIRS = [("XAU/USD", "SI")]  # watchlist-to-watchlist correlations


# ---------- indicators ----------

def _seeded(x: pd.Series, n: int, alpha: float) -> pd.Series:
    """Recursive smoother seeded with the SMA of the first n valid values (TradingView convention)."""
    vals = x.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    valid = np.flatnonzero(~np.isnan(vals))
    if len(valid) < n:
        return pd.Series(out, index=x.index)
    start = valid[n - 1]
    out[start] = vals[valid[:n]].mean()
    for i in range(start + 1, len(vals)):
        out[i] = alpha * vals[i] + (1 - alpha) * out[i - 1]
    return pd.Series(out, index=x.index)


def ema(close: pd.Series, n: int) -> pd.Series:
    return _seeded(close, n, 2 / (n + 1))


def wilder(x: pd.Series, n: int) -> pd.Series:
    return _seeded(x, n, 1 / n)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift()
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    diff = close.diff()
    gain, loss = wilder(diff.clip(lower=0), n), wilder((-diff).clip(lower=0), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + gain / loss)
    return out.where(loss != 0, 100.0).where(gain.notna())


def rvol(volume: pd.Series, n: int = 20) -> pd.Series:
    return volume / volume.rolling(n).mean()


def swings(df: pd.DataFrame, side: int = SWING_SIDE) -> tuple[pd.Series, pd.Series]:
    """Confirmed swing highs/lows: strictly beyond `side` bars on each side. The last `side` bars never qualify."""
    h, l = df["high"], df["low"]
    is_high = pd.Series(True, index=df.index)
    is_low = pd.Series(True, index=df.index)
    for k in range(1, side + 1):
        is_high &= (h > h.shift(k)) & (h > h.shift(-k))
        is_low &= (l < l.shift(k)) & (l < l.shift(-k))
    return h[is_high], l[is_low]


def daily_closes(df: pd.DataFrame, session: levels.Session) -> pd.Series:
    dates = levels.session_dates(df.index, session)
    return df["close"].groupby(dates).last()


# ---------- structure and grade ----------

def classify_structure(highs: pd.Series, lows: pd.Series, price: float, lookback: int = 3) -> dict:
    """Uptrend = last 2 swing highs and lows both higher; downtrend = both lower; otherwise range.

    Range bounds = highest/lowest of the last `lookback` confirmed swings.
    """
    if len(highs) < 2 or len(lows) < 2:
        return {"structure": "n/a", "range_high": None, "range_low": None, "range_pos": None}
    hh, hl = highs.iloc[-1] > highs.iloc[-2], lows.iloc[-1] > lows.iloc[-2]
    lh, ll = highs.iloc[-1] < highs.iloc[-2], lows.iloc[-1] < lows.iloc[-2]
    kind = "uptrend" if hh and hl else "downtrend" if lh and ll else "range"
    # Break of structure: a close beyond the last opposing swing ends the trend label.
    if kind == "uptrend" and price < lows.iloc[-1]:
        kind = "range (uptrend broken)"
    elif kind == "downtrend" and price > highs.iloc[-1]:
        kind = "range (downtrend broken)"
    top, bottom = float(highs.iloc[-lookback:].max()), float(lows.iloc[-lookback:].min())
    pos = (price - bottom) / (top - bottom) * 100 if top > bottom else None
    return {"structure": kind, "range_high": top, "range_low": bottom, "range_pos": pos}


def momentum_grade(direction: str, close: float, ema20: float, ema50: float, rsi14: float,
                   daily_close: float, daily_ema20: float) -> dict:
    """Base grade before modifiers, per skills/multi-timeframe-momentum.md."""
    sign = 1 if direction == "long" else -1
    if np.isnan(ema50) or np.isnan(rsi14):
        four_h = "n/a"
    else:
        stack_with = sign * (close - ema20) > 0 and sign * (ema20 - ema50) > 0
        stack_against = sign * (close - ema20) < 0 and sign * (ema20 - ema50) < 0
        rsi_with, rsi_against = sign * (rsi14 - 50) > 0, sign * (rsi14 - 50) < 0
        four_h = "aligned" if stack_with and rsi_with else "opposed" if stack_against and rsi_against else "mixed"
    daily = "n/a" if np.isnan(daily_ema20) else "aligned" if sign * (daily_close - daily_ema20) > 0 else "opposed"
    in_band = (50 <= rsi14 <= 70) if direction == "long" else (30 <= rsi14 <= 50)

    if four_h == "n/a":
        rsi_ok = not np.isnan(rsi14) and sign * (rsi14 - 50) > 0
        grade = "B" if rsi_ok and daily == "aligned" else "C"
    elif four_h == "opposed":
        grade = "C"
    elif four_h == "aligned" and daily == "aligned":
        grade = "A" if in_band else "B"
    elif four_h == "aligned" or (four_h == "mixed" and daily == "aligned"):
        grade = "B"
    else:
        grade = "C"
    return {"direction": direction, "4h": four_h, "daily": daily, "grade": grade}


# ---------- correlations ----------

def returns_corr(a: pd.Series, b: pd.Series, window: int = CORR_WINDOW, diff_b: bool = False) -> tuple[float | None, int]:
    """Pearson correlation of daily % returns of `a` vs `b` (b as level change if diff_b) over the last `window` days."""
    ra = a.pct_change()
    rb = b.diff() if diff_b else b.pct_change()
    joined = pd.concat([ra, rb], axis=1, join="inner").dropna().tail(window)
    if len(joined) < 10:
        return None, len(joined)
    return float(joined.iloc[:, 0].corr(joined.iloc[:, 1])), len(joined)


# ---------- report ----------

def summarize(df: pd.DataFrame, session: levels.Session) -> dict:
    close = df["close"]
    e20, e50, r14, a14 = ema(close, 20), ema(close, 50), rsi(close), atr(df)
    daily = daily_closes(df, session)
    d20 = ema(daily, 20)
    highs, lows = swings(df)
    last = float(close.iloc[-1])
    confirmed = df[df["confirmed"]] if "confirmed" in df else df.iloc[:-1]
    rv = rvol(df["volume"])
    return {
        "close": last, "ema20": float(e20.iloc[-1]), "ema50": float(e50.iloc[-1]),
        "rsi14": float(r14.iloc[-1]), "atr14": float(a14.iloc[-1]),
        "daily_close": float(daily.iloc[-1]), "daily_ema20": float(d20.iloc[-1]),
        "bars_4h": len(df), "bars_daily": len(daily),
        "highs": highs.tail(4), "lows": lows.tail(4),
        "structure": classify_structure(highs, lows, last),
        "rvol_last_closed": float(rv.loc[confirmed.index[-1]]) if len(confirmed) else float("nan"),
        "last_closed_ts": confirmed.index[-1] if len(confirmed) else None,
        "grades": [momentum_grade(d, last, float(e20.iloc[-1]), float(e50.iloc[-1]), float(r14.iloc[-1]),
                                  float(daily.iloc[-1]), float(d20.iloc[-1])) for d in ("long", "short")],
        "daily_series": daily,
    }


def _f(x, nd=2) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{nd}f}"


def render(symbol: str, src: levels.Source, df: pd.DataFrame, s: dict) -> str:
    warm = []
    if s["bars_4h"] < SETTLE_4H:
        warm.append(f"4H EMA50 approximate ({s['bars_4h']} bars < {SETTLE_4H})")
    if s["bars_daily"] < SETTLE_DAILY:
        warm.append(f"daily EMA20 approximate ({s['bars_daily']} days < {SETTLE_DAILY})")
    st = s["structure"]
    swing_fmt = lambda ser: ", ".join(f"{v:,.2f} ({t:%m-%d %H:%M})" for t, v in ser.items()) or "none"
    lines = [
        f"## {symbol}",
        f"Data: {src.label}, 4H, {df.index[0]:%Y-%m-%d %H:%M} -> {df.index[-1]:%Y-%m-%d %H:%M} UTC "
        f"({s['bars_4h']} bars, {s['bars_daily']} sessions)",
        "",
        "| Indicator | Value |",
        "|---|---|",
        f"| Close | {_f(s['close'])} |",
        f"| EMA20 / EMA50 (4H) | {_f(s['ema20'])} / {_f(s['ema50'])} |",
        f"| RSI14 (4H) | {_f(s['rsi14'], 1)} |",
        f"| ATR14 (4H) | {_f(s['atr14'])} |",
        f"| Daily close / EMA20 | {_f(s['daily_close'])} / {_f(s['daily_ema20'])} |",
        f"| RVOL last closed bar | {_f(s['rvol_last_closed'])} "
        f"({s['last_closed_ts']:%m-%d %H:%M} UTC) |" if s["last_closed_ts"] is not None else "| RVOL | n/a |",
        "",
        f"Swing highs: {swing_fmt(s['highs'])}",
        f"Swing lows: {swing_fmt(s['lows'])}",
        f"Structure: {st['structure']}; last-3-swing range {_f(st['range_low'])}-{_f(st['range_high'])}, "
        f"price at {_f(st['range_pos'], 1)}%",
    ]
    for g in s["grades"]:
        lines.append(f"Base grade {g['direction']}: **{g['grade']}** (4H {g['4h']}, daily {g['daily']})")
    if src.drop_weekend:
        lines.append("Note: XAUT volume is token volume, not gold market volume; ignore RVOL.")
    if warm:
        lines.append("Warm-up: " + "; ".join(warm))
    return "\n".join(lines) + "\n"


def correlation_section(daily: dict[str, pd.Series], symbols: list[str], days: int) -> str:
    rows = ["## Correlations (daily returns)", "", "| Pair | rho | n |", "|---|---|---|"]
    driver_cache: dict[str, pd.Series | Exception] = {}
    for a, b in PAIRS:
        if a in daily and b in daily:
            rho, n = returns_corr(daily[a], daily[b])
            rows.append(f"| {a} vs {b} | {_f(rho)} | {n} |")
    for sym, ticker, use_diff in DRIVERS:
        if sym not in symbols or sym not in daily:
            continue
        if ticker not in driver_cache:
            try:
                driver_cache[ticker] = data.fetch_yf_daily_close(ticker, period=f"{days}d")
            except Exception as exc:
                driver_cache[ticker] = exc
        drv = driver_cache[ticker]
        if isinstance(drv, Exception):
            rows.append(f"| {sym} vs {ticker} | n/a (fetch failed: {drv}) | 0 |")
            continue
        rho, n = returns_corr(daily[sym], drv, diff_b=use_diff)
        label = f"{ticker} (change)" if use_diff else ticker
        rows.append(f"| {sym} vs {label} | {_f(rho)} | {n} |")
    return "\n".join(rows) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("symbols", nargs="*")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    args = parser.parse_args(argv)
    symbols = [s.upper() for s in args.symbols] or list(levels.WATCHLIST)
    unknown = [s for s in symbols if s not in levels.WATCHLIST]
    if unknown:
        print(f"Unknown symbol(s): {', '.join(unknown)}. Watchlist: {', '.join(levels.WATCHLIST)}")
        return 2

    levels.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC")
    start = now - pd.Timedelta(days=args.days)
    sections, daily = [], {}
    for sym in symbols:
        src = levels.WATCHLIST[sym]
        try:
            df = levels.load_4h(src, now, start=start)
        except Exception as exc:
            sections.append(f"## {sym}\nDATA ERROR from {src.label}: {exc}\n")
            continue
        if len(df) < 2:
            sections.append(f"## {sym}\nDATA ERROR: not enough candles from {src.label}\n")
            continue
        s = summarize(df, src.session)
        daily[sym] = s["daily_series"]
        sections.append(render(sym, src, df, s))
    sections.append(correlation_section(daily, symbols, args.days))

    report = f"# Indicators: generated {now:%Y-%m-%d %H:%M} UTC ({args.days} days)\n\n" + "\n".join(sections)
    (levels.OUTPUT_DIR / f"{now:%Y-%m-%d_%H%M}_indicators.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
