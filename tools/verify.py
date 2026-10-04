"""Cross-check the 4H data and reference levels against 15M and 5M bars.

Usage:  python tools/verify.py [SYMBOL ...]      (default: whole watchlist)

For each symbol and each fine interval, rebuild 4H bars from the fine bars through the same pipeline
(bin alignment, gold weekend filter), then compare bar by bar with the native 4H data and recompute
the reference levels. 15M covers the full level window (PDH..PMthL); 5M covers the last 14 days
(PDH/PDL, PWH/PWL). Writes a markdown report to tools/output/ and prints it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import data, levels  # noqa: E402

TOLERANCE_PCT = 0.02          # bar-level |diff| above this % of price counts as a mismatch
LEVEL_KEYS = ["PDH", "PDL", "PWH", "PWL", "PMthH", "PMthL"]
FINE = {
    # interval: (OKX bar, yfinance interval, days of history or None = full level window, levels checked)
    "15M": ("15m", "15m", None, LEVEL_KEYS),
    "5M": ("5m", "5m", 14, LEVEL_KEYS[:4]),
}


def aggregate_4h(fine: pd.DataFrame, src: levels.Source) -> pd.DataFrame:
    """Fine bars -> 4H using the same bins as the native source (OKX: UTC 4H, yfinance: session-anchored)."""
    if src.provider == "okx":
        key = fine.index.floor("4h")
        out = fine.groupby(key).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                    close=("close", "last"), volume=("volume", "sum"))
        out.index.name = "ts"
    else:
        out = levels.resample_4h(fine, src.session.bar_offset)
    if src.drop_weekend:
        out = levels.drop_gold_weekend(out)
    return out


def compare_bars(native: pd.DataFrame, rebuilt: pd.DataFrame) -> dict:
    """Compare OHLC of 4H bars present in both, skipping the first rebuilt bin (may be partial) and the
    newest common bar (may still be forming / fetched at a different moment)."""
    common = native.index.intersection(rebuilt.index[1:])[:-1]
    if len(common) == 0:
        return {"bars": 0, "max_diff_pct": None, "mismatches": pd.DataFrame()}
    a, b = native.loc[common, ["high", "low", "close"]], rebuilt.loc[common, ["high", "low", "close"]]
    diff_pct = ((b - a) / a * 100).abs()
    worst = diff_pct.max(axis=1)
    bad = worst[worst > TOLERANCE_PCT].sort_values(ascending=False)
    rows = pd.DataFrame({"native_high": a["high"], "fine_high": b["high"], "native_low": a["low"],
                         "fine_low": b["low"], "max_diff_pct": worst}).loc[bad.index]
    return {"bars": len(common), "max_diff_pct": float(worst.max()), "mismatches": rows}


def compare_levels(native: dict, rebuilt: dict, keys: list[str]) -> list[dict]:
    out = []
    for k in keys:
        n, f = native.get(k), rebuilt.get(k)
        diff = None if n is None or f is None else (f / n - 1) * 100
        out.append({"level": k, "native": n, "fine": f, "diff_pct": diff,
                    "ok": diff is not None and abs(diff) <= TOLERANCE_PCT})
    return out


def load_fine(src: levels.Source, interval: str, now: pd.Timestamp) -> pd.DataFrame:
    okx_bar, yf_interval, days, _ = FINE[interval]
    start = levels.history_start(now) if days is None else now - pd.Timedelta(days=days)
    if src.provider == "okx":
        return data.fetch_okx_candles(src.instrument, start=start, bar=okx_bar, max_pages=200)
    period_days = min((now - start).days + 2, 59)  # yfinance: 15m/5m max 60 days
    return data.fetch_yf_hourly(src.instrument, period=f"{period_days}d", interval=yf_interval)


def _f(x, nd=2) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


def verify_symbol(symbol: str, src: levels.Source, now: pd.Timestamp) -> str:
    lines = [f"## {symbol}"]
    try:
        native = levels.load_4h(src, now)
    except Exception as exc:
        return f"## {symbol}\nDATA ERROR (native 4H): {exc}\n"
    native_levels = levels.reference_levels(native, src.session)
    lines.append(f"Native 4H: {src.label}, {len(native)} bars to {native.index[-1]:%Y-%m-%d %H:%M} UTC")
    for interval, (_, _, days, keys) in FINE.items():
        try:
            fine = load_fine(src, interval, now)
        except Exception as exc:
            lines.append(f"\n### {interval}\nDATA ERROR: {exc}")
            continue
        rebuilt = aggregate_4h(fine, src)
        bars = compare_bars(native, rebuilt)
        # Levels from fine data must use the same window as native so "previous period" means the same thing.
        window = rebuilt[rebuilt.index <= native.index[-1]]
        lv = compare_levels(native_levels, levels.reference_levels(window, src.session), keys) if len(window) else []
        verdict = "PASS" if bars["bars"] and bars["mismatches"].empty and all(r["ok"] for r in lv) else "CHECK"
        lines += [
            f"\n### {interval}: {verdict}",
            f"{len(fine)} bars {fine.index[0]:%Y-%m-%d %H:%M} -> {fine.index[-1]:%Y-%m-%d %H:%M} UTC; "
            f"{bars['bars']} 4H bars compared, max OHLC diff {_f(bars['max_diff_pct'], 3)}% "
            f"(tolerance {TOLERANCE_PCT}%), {len(bars['mismatches'])} mismatched",
            "",
            "| Level | 4H | " + interval + " | Diff % | OK |",
            "|---|---|---|---|---|",
        ]
        lines += [f"| {r['level']} | {_f(r['native'])} | {_f(r['fine'])} | {_f(r['diff_pct'], 3)} | "
                  f"{'yes' if r['ok'] else 'NO'} |" for r in lv]
        if not bars["mismatches"].empty:
            lines.append("\nWorst mismatched 4H bars (UTC):")
            for ts, r in bars["mismatches"].head(5).iterrows():
                lines.append(f"- {ts:%Y-%m-%d %H:%M}: high {r.native_high:,.2f} vs {r.fine_high:,.2f}, "
                             f"low {r.native_low:,.2f} vs {r.fine_low:,.2f} ({r.max_diff_pct:.3f}%)")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    symbols = [s.upper() for s in argv] or list(levels.WATCHLIST)
    unknown = [s for s in symbols if s not in levels.WATCHLIST]
    if unknown:
        print(f"Unknown symbol(s): {', '.join(unknown)}. Watchlist: {', '.join(levels.WATCHLIST)}")
        return 2
    levels.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = pd.Timestamp.now(tz="UTC")
    report = f"# Data verification (15M / 5M vs 4H): generated {now:%Y-%m-%d %H:%M} UTC\n\n" + "\n".join(
        verify_symbol(s, levels.WATCHLIST[s], now) for s in symbols)
    (levels.OUTPUT_DIR / f"{now:%Y-%m-%d_%H%M}_verify.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
