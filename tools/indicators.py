"""Indicators on 15M/1H/4H/1D/1W, 4H swings/structure/grade, alignment score, and driver correlations.

Usage:  python tools/indicators.py [SYMBOL ...] [--bars N] [--tf 15M 1H 4H 1D 1W]   (default: whole watchlist, 600 bars)
Writes a markdown report to tools/output/ and prints it. Data comes from tools/fetch_prices.py.
Definitions follow skills/ (multi-timeframe-momentum, levels-and-entries, market-structure, macro-and-catalysts).

Per timeframe: EMA 9/21/50/200, EMA20 (grade rule), RSI14, MACD(12,26,9), ATR14, volume trend, RVOL.
Timeframe state uses the momentum-skill rule: bull = close > EMA20 > EMA50 and RSI > 50; bear = mirror; else mixed.
Alignment score for a direction = timeframes agreeing minus timeframes opposing (-5..+5). The A/B/C grade is
still the 4H + daily rule from the skill; the score is extra context.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import data, fetch_prices, levels  # noqa: E402

CORR_WINDOW = 30
DRIVER_PERIOD = "120d"
EMA_SET = (9, 21, 50, 200)
VOL_TREND_BAND = 0.10  # SMA20/SMA50 of volume above 1.10 = rising, below 0.90 = falling
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
    ("BTC/USDT", "^NDX", False),
    ("BTC/USDT", "DX-Y.NYB", False),
    ("GLD", "DX-Y.NYB", False),
]
PAIRS = [("XAU/USD", "SI"), ("XAU/USD", "GLD"), ("BCH/USDT", "BTC/USDT")]  # watchlist-to-watchlist correlations


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


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def volume_trend(volume: pd.Series) -> tuple[str, float]:
    """'rising' / 'falling' / 'flat' from the SMA20/SMA50 volume ratio."""
    if len(volume) < 50 or volume.tail(50).sum() == 0:
        return "n/a", float("nan")
    ratio = float(volume.rolling(20).mean().iloc[-1] / volume.rolling(50).mean().iloc[-1])
    trend = "rising" if ratio > 1 + VOL_TREND_BAND else "falling" if ratio < 1 - VOL_TREND_BAND else "flat"
    return trend, ratio


def tf_state(close: float, ema20: float, ema50: float, rsi14: float) -> str:
    if any(np.isnan(x) for x in (ema20, ema50, rsi14)):
        return "n/a"
    if close > ema20 > ema50 and rsi14 > 50:
        return "bull"
    if close < ema20 < ema50 and rsi14 < 50:
        return "bear"
    return "mixed"


def alignment_score(states: dict[str, str], direction: str) -> int:
    want, against = ("bull", "bear") if direction == "long" else ("bear", "bull")
    return sum(st == want for st in states.values()) - sum(st == against for st in states.values())


def timeframe_row(df: pd.DataFrame) -> dict:
    close = df["close"]
    m = macd(close)
    trend, ratio = volume_trend(df["volume"])
    closed = df[df["confirmed"]] if "confirmed" in df else df.iloc[:-1]
    rv = rvol(df["volume"])
    row = {f"ema{n}": float(ema(close, n).iloc[-1]) for n in (*EMA_SET, 20)}
    row.update(close=float(close.iloc[-1]), rsi14=float(rsi(close).iloc[-1]), atr14=float(atr(df).iloc[-1]),
               macd=float(m["macd"].iloc[-1]), signal=float(m["signal"].iloc[-1]), hist=float(m["hist"].iloc[-1]),
               vol_trend=trend, vol_ratio=ratio, bars=len(df),
               rvol=float(rv.loc[closed.index[-1]]) if len(closed) else float("nan"))
    row["state"] = tf_state(row["close"], row["ema20"], row["ema50"], row["rsi14"])
    row["ema200_note"] = ema_quality(len(df), 200)
    return row


def ema_quality(bars: int, n: int) -> str:
    """'' when settled, 'approx' when the SMA seed still carries 1-10% weight, 'n/a' above 10% or too few bars."""
    if bars < n:
        return "n/a"
    seed_weight = (1 - 2 / (n + 1)) ** (bars - n)
    return "n/a" if seed_weight > 0.10 else "approx" if seed_weight > 0.01 else ""


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


def timeframe_section(frames: dict[str, pd.DataFrame], errors: dict[str, str], ignore_volume: bool) -> tuple[str, dict]:
    rows = {tf: timeframe_row(frames[tf]) for tf in fetch_prices.TIMEFRAMES if tf in frames and len(frames[tf]) >= 2}
    lines = ["", "| TF | Bars | Close | EMA9 | EMA21 | EMA50 | EMA200 | RSI14 | MACD / signal / hist | ATR14 | Vol trend | State |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for tf in fetch_prices.TIMEFRAMES:
        if tf in errors:
            lines.append(f"| {tf} | FETCH FAILED: {errors[tf]} | | | | | | | | | | |")
        elif tf in rows:
            r = rows[tf]
            note = r["ema200_note"]
            e200 = "n/a (short history)" if note == "n/a" else _f(r["ema200"]) + (f" ({note})" if note else "")
            vol = "ignore (token volume)" if ignore_volume else f"{r['vol_trend']} ({_f(r['vol_ratio'])})"
            lines.append(f"| {tf} | {r['bars']} | {_f(r['close'])} | {_f(r['ema9'])} | {_f(r['ema21'])} | "
                         f"{_f(r['ema50'])} | {e200} | {_f(r['rsi14'], 1)} | {_f(r['macd'])} / {_f(r['signal'])} / "
                         f"{_f(r['hist'])} | {_f(r['atr14'])} | {vol} | {r['state']} |")
    states = {tf: r["state"] for tf, r in rows.items()}
    scores = {d: alignment_score(states, d) for d in ("long", "short")}
    lines.append("")
    lines.append(f"Alignment score ({len(states)} TFs, EMA20/50 + RSI rule): long {scores['long']:+d}, "
                 f"short {scores['short']:+d} [" + ", ".join(f"{tf} {st}" for tf, st in states.items()) + "]")
    return "\n".join(lines) + "\n", {"states": states, "scores": scores, "rows": rows}


def correlation_section(daily: dict[str, pd.Series], symbols: list[str]) -> str:
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
                driver_cache[ticker] = data.fetch_yf_daily_close(ticker, period=DRIVER_PERIOD)
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
    parser.add_argument("--bars", type=int, default=fetch_prices.DEFAULT_BARS)
    parser.add_argument("--tf", nargs="+", default=fetch_prices.TIMEFRAMES, choices=fetch_prices.TIMEFRAMES)
    args = parser.parse_args(argv)
    symbols = [s.upper() for s in args.symbols] or list(levels.WATCHLIST)
    unknown = [s for s in symbols if s not in levels.WATCHLIST]
    if unknown:
        print(f"Unknown symbol(s): {', '.join(unknown)}. Watchlist: {', '.join(levels.WATCHLIST)}")
        return 2
    tfs = list(dict.fromkeys(["4H", *args.tf]))  # 4H always: structure and grade are built on it

    levels.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC")
    sections, daily, failed = [], {}, False
    for sym in symbols:
        src = levels.WATCHLIST[sym]
        frames, errors = {}, {}
        for tf in tfs:
            try:
                frames[tf] = fetch_prices.get_ohlcv(sym, tf, bars=args.bars)
            except fetch_prices.FetchError as exc:
                errors[tf], failed = str(exc), True
        df = frames.get("4H")
        if df is None or len(df) < 2:
            sections.append(f"## {sym}\nDATA ERROR (4H): {errors.get('4H', 'not enough candles')}\n")
            continue
        s = summarize(df, src.session)
        daily[sym] = s["daily_series"]
        tf_text, _ = timeframe_section(frames, errors, ignore_volume=src.drop_weekend)
        fetched = ", ".join(f"{tf} {frames[tf].attrs['fetched_at'][:16]}Z" for tf in fetch_prices.TIMEFRAMES if tf in frames)
        sections.append(render(sym, src, df, s) + tf_text + f"Fetched (UTC): {fetched}\n")
    sections.append(correlation_section(daily, symbols))

    report = f"# Indicators: generated {now:%Y-%m-%d %H:%M} UTC ({args.bars} bars per TF)\n\n" + "\n".join(sections)
    (levels.OUTPUT_DIR / f"{now:%Y-%m-%d_%H%M}_indicators.md").write_text(report, encoding="utf-8")
    print(report)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
