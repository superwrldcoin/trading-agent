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
    backup: "Source | None" = None
    verified: bool = False


SESSIONS = {"crypto": CRYPTO, "gold_spot": GOLD, "cme": CME, "equity": EQUITY}


def source_from_entry(e: dict) -> Source:
    """Build a Source from a core entry in memory/universe.yaml."""
    session = SESSIONS[e["session"]]
    backup = None
    if e.get("backup"):
        b = e["backup"]["symbol"]
        backup = Source(f"yfinance {b} (backup)", "yfinance", b, session)
    if e.get("source") == "exchange_public":
        feed = e["feed"]
        if feed.get("venue") != "okx":
            raise ValueError(f"{e['id']}: only OKX is supported as an exchange feed (got {feed.get('venue')})")
        label = f"OKX {feed['instrument']}" + (" (spot proxy)" if e.get("spot_check") else "")
        sc = e.get("spot_check")
        return Source(label, "okx", feed["instrument"], session, drop_weekend=bool(feed.get("drop_weekend")),
                      spot_check=(sc["base"], sc["quote"]) if sc else None, backup=backup,
                      verified=bool(e.get("verified")))
    rth = ", RTH" if e["session"] == "equity" else ""
    return Source(f"yfinance {e['symbol']} (1H -> 4H{rth})", "yfinance", e["symbol"], session, backup=backup,
                  verified=bool(e.get("verified")))


def build_watchlist() -> dict[str, Source]:
    from tools import universe
    return {e["id"]: source_from_entry(e) for e in universe.core()}


WATCHLIST = build_watchlist()  # from memory/universe.yaml; nothing hardcoded


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


def drop_gold_weekend(df: pd.DataFrame, bar_minutes: int = 240) -> pd.DataFrame:
    """Drop bars that sit entirely inside the spot-gold weekend close (Fri 17:00 -> Sun 18:00 ET)."""
    wall = df.index.tz_convert(NY).tz_localize(None)
    minutes = wall.dayofweek * 1440 + wall.hour * 60 + wall.minute
    closed = (minutes >= 4 * 1440 + 17 * 60) & (minutes + bar_minutes <= 6 * 1440 + 18 * 60)
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


def utc_4h(hourly: pd.DataFrame) -> pd.DataFrame:
    """1H -> 4H on UTC boundaries (00/04/08...), for 24/7 markets fetched from yfinance."""
    g = hourly.assign(ts=hourly.index).groupby(hourly.index.floor("4h"))
    out = g.agg(ts=("ts", "first"), open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), volume=("volume", "sum"))
    return out.set_index(out.index.rename("ts")).drop(columns="ts")


def load_4h(src: Source, now: pd.Timestamp, start: pd.Timestamp | None = None, use_backup: bool = True) -> pd.DataFrame:
    """4H candles from `start` (default: history_start(now)) to now. Falls back to src.backup if the primary fails;
    the result's attrs["feed"] says which feed was used."""
    start = start if start is not None else history_start(now)
    try:
        if src.provider == "okx":
            df = data.fetch_okx_candles(src.instrument, start=start)
        else:
            days = min((now - start).days + 2, 729)  # yfinance caps 1H history at 730 days
            hourly = data.fetch_yf_hourly(src.instrument, period=f"{days}d")
            df = utc_4h(hourly) if src.session.bar_offset is None else resample_4h(hourly, src.session.bar_offset)
        if df.empty:
            raise RuntimeError(f"{src.label} returned no candles")
    except Exception as exc:
        if not (use_backup and src.backup):
            raise
        df = load_4h(src.backup, now, start, use_backup=False)
        df.attrs["feed"] = f"{src.backup.label} (primary {src.label} failed: {str(exc)[:80]})"
        return df
    if src.drop_weekend:
        df = drop_gold_weekend(df)
    df = df[df.index >= start]
    df.attrs["feed"] = src.label
    return df


def _fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:,.2f}"


LEVEL_ROWS = [("PDH", "day"), ("PDL", "day"), ("PWH", "week"), ("PWL", "week"), ("PMthH", "month"), ("PMthL", "month")]


def distance_rows(lv: dict, last: float, atr14: float | None) -> list[dict]:
    """Each reference level with its distance from the last price, in % and in ATR, sorted high to low."""
    rows = []
    for key, period in LEVEL_ROWS:
        value = lv[key]
        rows.append({"level": key, "value": value, "period": lv[period],
                     "dist_pct": None if value is None else (value / last - 1) * 100,
                     "dist_atr": None if value is None or not atr14 else (value - last) / atr14})
    return sorted(rows, key=lambda r: -(r["value"] if r["value"] is not None else float("-inf")))


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
    else:
        lines.append("Spot check: not applicable (only XAU/USD has a spot cross-check)")
    from tools import indicators  # local import: indicators imports this module
    atr14 = float(indicators.atr(df).iloc[-1]) if len(df) > 14 else None
    lines += [f"ATR14 (4H): {_fmt(atr14)}", "",
              "| Level | Value | Period | Dist from last | ATR away |", "|---|---|---|---|---|"]
    for r in distance_rows(lv, last, atr14):
        dist = "n/a" if r["dist_pct"] is None else f"{r['dist_pct']:+.2f}%"
        away = "n/a" if r["dist_atr"] is None else f"{r['dist_atr']:+.2f}"
        lines.append(f"| {r['level']} | {_fmt(r['value'])} | {r['period']} | {dist} | {away} |")
    return "\n".join(lines) + "\n"


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
