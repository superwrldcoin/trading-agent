"""Fetch 4H candles for the watchlist and compute PDH/PDL, PWH/PWL, PMthH/PMthL.

Usage:  python tools/levels.py [SYMBOL ...]      (default: whole watchlist)
Writes a markdown report and per-symbol 4H CSVs to tools/output/, and prints the report.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import data  # noqa: E402

OUTPUT_DIR = ROOT / "tools" / "output"
NY = "America/New_York"
PREMIUM_WARN_PCT = 0.2


@dataclass(frozen=True)
class Session:
    """How to bucket bars into trading days.

    day_shift: hours added to New York wall time so a session's evening open lands on the next date
               (CME opens 18:00 ET -> +6h; spot gold rolls 17:00 ET -> +7h). None = use UTC dates.
    bar_offset: 4H bin anchor in New York wall time (equities 09:30, CME 18:00). None = bars come native.
    """
    day_shift: int | None
    bar_offset: str | None = None


CRYPTO = Session(day_shift=None)
GOLD = Session(day_shift=7)
CME = Session(day_shift=6, bar_offset="18h")
EQUITY = Session(day_shift=0, bar_offset="9h30min")


@dataclass(frozen=True)
class Source:
    label: str
    provider: str  # "okx" or "yfinance"
    instrument: str
    session: Session
    drop_weekend: bool = False
    spot_check: tuple[str, str] | None = None


WATCHLIST = {
    "XAU/USD": Source("OKX XAUT-USDT (Tether Gold, spot proxy)", "okx", "XAUT-USDT", GOLD,
                      drop_weekend=True, spot_check=("XAU", "USD")),
    "MSFT": Source("yfinance MSFT (1H -> 4H, RTH)", "yfinance", "MSFT", EQUITY),
    "SI": Source("yfinance SI=F (COMEX silver futures, 1H -> 4H)", "yfinance", "SI=F", CME),
    "BCH/USDT": Source("OKX BCH-USDT", "okx", "BCH-USDT", CRYPTO),
}


def resample_4h(hourly: pd.DataFrame, bar_offset: str) -> pd.DataFrame:
    """Group 1H bars into 4H bars anchored at `bar_offset` New York wall time (DST-safe).

    Each 4H bar is labeled with the UTC timestamp of its first hourly bar.
    """
    wall = hourly.index.tz_convert(NY).tz_localize(None)
    offset = pd.Timedelta(bar_offset)
    key = (wall - offset).floor("4h") + offset
    grouped = hourly.assign(ts=hourly.index).groupby(key)
    out = grouped.agg(ts=("ts", "first"), open=("open", "first"), high=("high", "max"),
                      low=("low", "min"), close=("close", "last"), volume=("volume", "sum"))
    return out.set_index("ts").sort_index()


def drop_gold_weekend(df: pd.DataFrame) -> pd.DataFrame:
    """Drop bars that sit entirely inside the spot-gold weekend close (Fri 17:00 -> Sun 18:00 ET)."""
    wall = df.index.tz_convert(NY).tz_localize(None)
    minutes = wall.dayofweek * 1440 + wall.hour * 60 + wall.minute
    closed = (minutes >= 4 * 1440 + 17 * 60) & (minutes + 240 <= 6 * 1440 + 18 * 60)
    return df[~closed]


def session_dates(index: pd.DatetimeIndex, session: Session) -> pd.DatetimeIndex:
    if session.day_shift is None:
        return index.tz_convert("UTC").tz_localize(None).normalize()
    wall = index.tz_convert(NY).tz_localize(None)
    return (wall + pd.Timedelta(hours=session.day_shift)).normalize()


def _previous_period(df: pd.DataFrame, periods: pd.PeriodIndex) -> dict:
    current = periods.max()
    earlier = periods[periods < current]
    if len(earlier) == 0:
        return {"high": None, "low": None, "period": None}
    prev = earlier.max()
    rows = df[periods == prev]
    return {"high": float(rows["high"].max()), "low": float(rows["low"].min()), "period": str(prev)}


def reference_levels(df: pd.DataFrame, session: Session) -> dict:
    """Previous day/week/month high and low relative to the session of the latest bar."""
    dates = session_dates(df.index, session)
    day = _previous_period(df, dates.to_period("D"))
    week = _previous_period(df, dates.to_period("W-SUN"))
    month = _previous_period(df, dates.to_period("M"))
    return {
        "PDH": day["high"], "PDL": day["low"], "day": day["period"],
        "PWH": week["high"], "PWL": week["low"], "week": week["period"],
        "PMthH": month["high"], "PMthL": month["low"], "month": month["period"],
    }


def premium_pct(price: float, spot: float) -> float:
    return (price / spot - 1) * 100


def history_start(now: pd.Timestamp) -> pd.Timestamp:
    """Start of the previous month minus a week of slack, so PMthH/PMthL are always covered."""
    first_of_month = now.tz_convert("UTC").normalize().replace(day=1)
    return (first_of_month - pd.DateOffset(months=1)) - pd.Timedelta(days=7)


def load_4h(src: Source, now: pd.Timestamp, start: pd.Timestamp | None = None) -> pd.DataFrame:
    """4H candles from `start` (default: history_start(now)) to now."""
    start = start if start is not None else history_start(now)
    if src.provider == "okx":
        df = data.fetch_okx_candles(src.instrument, start=start)
    else:
        days = min((now - start).days + 2, 729)  # yfinance caps 1H history at 730 days
        df = resample_4h(data.fetch_yf_hourly(src.instrument, period=f"{days}d"), src.session.bar_offset)
    if src.drop_weekend:
        df = drop_gold_weekend(df)
    return df[df.index >= start]


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.2f}"


def analyze(symbol: str, src: Source, now: pd.Timestamp, stamp: str) -> str:
    try:
        df = load_4h(src, now)
    except Exception as exc:  # report and move on so one bad feed doesn't kill the run
        return f"## {symbol}\nDATA ERROR from {src.label}: {exc}\n"
    if df.empty:
        return f"## {symbol}\nDATA ERROR: no candles returned from {src.label}\n"

    csv_path = OUTPUT_DIR / f"{stamp}_{symbol.replace('/', '')}_4h.csv"
    df.to_csv(csv_path)

    last_ts, last = df.index[-1], float(df["close"].iloc[-1])
    status = "open" if "confirmed" in df and not df["confirmed"].iloc[-1] else "latest"
    lv = reference_levels(df, src.session)
    lines = [
        f"## {symbol}",
        f"Data: {src.label}, 4H, {df.index[0]:%Y-%m-%d %H:%M} -> {last_ts:%Y-%m-%d %H:%M} UTC "
        f"({len(df)} bars; last bar {status}), file {csv_path.name}",
        f"Last price: {_fmt(last)}",
    ]
    if src.spot_check:
        try:
            spot = data.fetch_swissquote_mid(*src.spot_check)
            prem = premium_pct(last, spot)
            warn = "  WARNING: proxy deviates from spot" if abs(prem) > PREMIUM_WARN_PCT else ""
            lines.append(f"Spot check: Swissquote {'/'.join(src.spot_check)} mid {_fmt(spot)} "
                         f"(proxy premium {prem:+.3f}%){warn}")
        except Exception as exc:
            lines.append(f"Spot check: unavailable ({exc})")
    lines += [
        "",
        "| Level | Value | Period |",
        "|---|---|---|",
        f"| PDH | {_fmt(lv['PDH'])} | {lv['day']} |",
        f"| PDL | {_fmt(lv['PDL'])} | {lv['day']} |",
        f"| PWH | {_fmt(lv['PWH'])} | {lv['week']} |",
        f"| PWL | {_fmt(lv['PWL'])} | {lv['week']} |",
        f"| PMthH | {_fmt(lv['PMthH'])} | {lv['month']} |",
        f"| PMthL | {_fmt(lv['PMthL'])} | {lv['month']} |",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    symbols = [s.upper() for s in argv] or list(WATCHLIST)
    unknown = [s for s in symbols if s not in WATCHLIST]
    if unknown:
        print(f"Unknown symbol(s): {', '.join(unknown)}. Watchlist: {', '.join(WATCHLIST)}")
        return 2
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC")
    stamp = f"{now:%Y-%m-%d_%H%M}"
    report = f"# Reference levels: generated {now:%Y-%m-%d %H:%M} UTC\n\n" + "\n".join(
        analyze(s, WATCHLIST[s], now, stamp) for s in symbols)
    (OUTPUT_DIR / f"{stamp}_levels.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
