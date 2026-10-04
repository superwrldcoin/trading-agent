"""Record a closed trade in memory/trades.md (private) with computed results; feeds skills/post-mortem.md.

Linear (perp / spot / futures / CFD / stock):
  python tools/log_trade.py --symbol BTC/USDT --side long --entry 98400 --exit 99500 --size 0.2 --stop 97000 \
      --leverage 20 --opened "2026-10-01 12:00" --closed "2026-10-03 08:00" --thesis "PWH breakout retest" \
      [--entries 98400:0.1 98000:0.1] [--exits 99000:0.1 99500:0.1] [--fees 12.5 | --fee-rate 0.0005] [--funding 1.2]
      [--t1 99800] [--thesis-recalled] [--session S-20261004-2038/BTCUSDT] [--prob 55] [--margin isolated]
Options:
  python tools/log_trade.py --symbol MSFT --side long --instrument option --kind call --strike 520 --expiry 2026-11-20 \
      --qty 2 --premium-open 23.20 --premium-close 31.00 --opened ... --closed ... --thesis "..."

Computes the blended entry and exit, net P&L after fees and funding, realized R (needs a stop; options: R vs premium at
risk), MAE/MFE in R from 15M/1H bars between open and close, and whether T1 came before the stop. Assigns the next T-NNN,
appends to trades.md, and (with --session) updates that session entry's Outcome line for calibration.
Times are UTC unless they carry an offset.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import fetch_prices, levels  # noqa: E402

TRADES_PATH = ROOT / "memory" / "trades.md"
SESSIONS_PATH = ROOT / "memory" / "sessions.md"
TRADES_HEADER = "# Trades\n\nClosed-trade log written by `tools/log_trade.py`. Private (gitignored).\n\n"


def parse_fills(items: list[str] | None, price: float | None, size: float | None) -> list[tuple[float, float]]:
    if items:
        out = []
        for it in items:
            p, s = it.split(":")
            out.append((float(p.replace(",", "")), float(s.replace(",", ""))))
        return out
    if price is None or size is None:
        raise ValueError("give a price and --size, or explicit PRICE:SIZE fills")
    return [(price, size)]


def to_utc(s: str) -> pd.Timestamp:
    t = pd.Timestamp(s)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def excursions(bars: pd.DataFrame, side: str, entry: float, stop: float | None, t1: float | None,
               opened: pd.Timestamp, closed: pd.Timestamp) -> dict:
    w = bars[(bars.index >= opened.floor("15min")) & (bars.index <= closed)]
    if w.empty:
        return {"mae": None, "mfe": None, "mae_R": None, "mfe_R": None, "t1_first": None, "bars": 0}
    s = 1 if side == "long" else -1
    worst = w["low"].min() if s == 1 else w["high"].max()
    best = w["high"].max() if s == 1 else w["low"].min()
    risk = abs(entry - stop) if stop is not None else None
    out = {"mae": float(worst), "mfe": float(best), "bars": len(w),
           "mae_R": None if not risk else -max(0.0, s * (entry - worst)) / risk,
           "mfe_R": None if not risk else max(0.0, s * (best - entry)) / risk, "t1_first": None}
    if t1 is not None and stop is not None:
        for _, b in w.iterrows():
            hit_t1 = (b["high"] >= t1) if s == 1 else (b["low"] <= t1)
            hit_stop = (b["low"] <= stop) if s == 1 else (b["high"] >= stop)
            if hit_t1 and hit_stop:
                out["t1_first"] = "same bar (ambiguous)"
                break
            if hit_t1 or hit_stop:
                out["t1_first"] = "yes" if hit_t1 else "no"
                break
    return out


def compute_linear(side: str, entries: list, exits: list, stop: float | None, fees: float | None,
                   fee_rate: float, funding: float) -> dict:
    s = 1 if side == "long" else -1
    size_in, size_out = sum(z for _, z in entries), sum(z for _, z in exits)
    if abs(size_in - size_out) > 1e-9:
        raise ValueError(f"entry size {size_in:g} and exit size {size_out:g} differ: log only fully closed trades")
    entry = sum(p * z for p, z in entries) / size_in
    exitp = sum(p * z for p, z in exits) / size_out
    gross = s * (exitp - entry) * size_in
    fee = fees if fees is not None else (entry + exitp) * size_in * fee_rate
    net = gross - fee - funding
    risk = abs(entry - stop) * size_in if stop is not None else None
    if stop is not None and s * (entry - stop) <= 0:
        raise ValueError(f"stop {stop} is on the wrong side of the entry {entry:.4f}")
    return {"entry": entry, "exit": exitp, "size": size_in, "gross": gross, "fees": fee, "funding": funding, "net": net,
            "risk": risk, "R": net / risk if risk else None, "fees_estimated": fees is None}


def compute_option(side: str, qty: float, mult: float, p_open: float, p_close: float, fees: float) -> dict:
    s = 1 if side == "long" else -1
    gross = s * (p_close - p_open) * qty * mult
    net = gross - fees
    risk = p_open * qty * mult if s == 1 else None
    return {"gross": gross, "fees": fees, "net": net, "risk": risk, "R": net / risk if risk else None}


def next_trade_id(text: str) -> str:
    nums = [int(n) for n in re.findall(r"^## T-(\d+):", text, re.M) if int(n) > 0]
    return f"T-{max(nums, default=0) + 1:03d}"


def format_entry(tid: str, t: dict) -> str:
    f = lambda x, nd=2: "unknown" if x is None else f"{x:,.{nd}f}"
    lines = [f"## {tid}: {t['symbol']} {t['side']}" + (f" {t['qty']:g}x {t['kind']} {t['strike']:g} exp {t['expiry']}"
                                                       if t["instrument"] == "option" else "")
             + f", opened {t['opened']:%Y-%m-%d %H:%M} UTC, closed {t['closed']:%Y-%m-%d %H:%M} UTC"]
    if t["instrument"] == "option":
        lines += [f"- Premium: open {f(t['premium_open'])}, close {f(t['premium_close'])} (x{t['multiplier']:g})",
                  f"- Risk: {'premium paid ' + f(t['risk']) if t['risk'] else 'undefined (short option)'}"]
    else:
        lines += [f"- Entry: {', '.join(f'{p:g} x {z:g}' for p, z in t['entries'])}; blended {f(t['entry'], 4)}",
                  f"- Stop at entry: {f(t['stop'])}" + ("" if t["stop"] is not None else " (no stop: R undefined)"),
                  f"- Exits: {', '.join(f'{p:g} x {z:g}' for p, z in t['exits'])}; blended {f(t['exit'], 4)}",
                  f"- Leverage / margin mode: {t['leverage']:g}x {t['margin']}" if t["leverage"] else "- Leverage / margin mode: unknown"]
    fee_note = " (estimated at fee rate)" if t.get("fees_estimated") else ""
    lines += [f"- Fees / funding: {f(t['fees'])}{fee_note} / {f(t.get('funding', 0))}",
              f"- Thesis ({'recalled after the fact' if t['thesis_recalled'] else 'pre-entry'}): \"{t['thesis']}\""
              if t["thesis"] else "- Thesis: [MISSING]",
              f"- Session link: {t['session'] or 'none'}",
              f"- P(T1 before stop) at entry: {str(round(t['prob'])) + '% [JUDGMENT]' if t['prob'] is not None else 'none'}",
              f"- Result: {('%+.2fR' % t['R']) if t['R'] is not None else 'R undefined'}, {('+' if t['net'] >= 0 else '')}{f(t['net'])} net"
              + (f"; MAE {t['mae_R']:+.2f}R, MFE {t['mfe_R']:+.2f}R" if t.get("mae_R") is not None else "; MAE/MFE unknown"),
              f"- T1 before stop: {t.get('t1_first') or 'unknown'}",
              "- Label: (post-mortem)", "- Post-mortem: pending", ""]
    return "\n".join(lines) + "\n"


def update_session_outcome(session_id: str, outcome: str, path: Path | None = None) -> bool:
    p = path or SESSIONS_PATH
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    m = re.search(rf"(### {re.escape(session_id)} .*?\n(?:- .*\n)*?)- Outcome: [^\n]*", text)
    if not m:
        return False
    start = m.end() - len(m.group(0)) + len(m.group(1))
    new = text[:start] + f"- Outcome: {outcome}" + text[m.end():]
    p.write_text(new, encoding="utf-8")
    return True


def record(t: dict, path: Path | None = None) -> str:
    path = path or TRADES_PATH
    text = path.read_text(encoding="utf-8") if path.exists() else TRADES_HEADER
    tid = next_trade_id(text)
    text = text.replace("_No trades yet._\n", "")
    if not text.endswith("\n\n"):
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + format_entry(tid, t), encoding="utf-8")
    return tid


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Record a closed trade in memory/trades.md.")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--instrument", default="perp", choices=["perp", "spot", "future", "cfd", "stock", "etf", "option"])
    ap.add_argument("--entry", type=float)
    ap.add_argument("--exit", type=float)
    ap.add_argument("--size", type=float)
    ap.add_argument("--entries", nargs="+")
    ap.add_argument("--exits", nargs="+")
    ap.add_argument("--stop", type=float)
    ap.add_argument("--t1", type=float, help="first target, to record whether it came before the stop")
    ap.add_argument("--leverage", type=float)
    ap.add_argument("--margin", default="isolated", choices=["isolated", "cross"])
    ap.add_argument("--fees", type=float)
    ap.add_argument("--fee-rate", type=float, default=0.0005)
    ap.add_argument("--funding", type=float, default=0.0)
    ap.add_argument("--kind", choices=["call", "put"])
    ap.add_argument("--strike", type=float)
    ap.add_argument("--expiry")
    ap.add_argument("--qty", type=float)
    ap.add_argument("--multiplier", type=float, default=100.0)
    ap.add_argument("--premium-open", type=float)
    ap.add_argument("--premium-close", type=float)
    ap.add_argument("--opened", required=True)
    ap.add_argument("--closed", required=True)
    ap.add_argument("--thesis")
    ap.add_argument("--thesis-recalled", action="store_true", help="thesis written after the fact, from memory")
    ap.add_argument("--session")
    ap.add_argument("--prob", type=float)
    a = ap.parse_args(argv)
    sym = a.symbol.upper()
    try:
        if sym not in levels.WATCHLIST:
            raise ValueError(f"{sym} not on the watchlist")
        opened, closed = to_utc(a.opened), to_utc(a.closed)
        if closed <= opened:
            raise ValueError("closed must be after opened")
        t = {"symbol": sym, "side": a.side, "instrument": a.instrument, "opened": opened, "closed": closed,
             "thesis": a.thesis, "thesis_recalled": a.thesis_recalled, "session": a.session, "prob": a.prob}
        if a.instrument == "option":
            if None in (a.kind, a.strike, a.expiry, a.qty, a.premium_open, a.premium_close):
                raise ValueError("options need --kind --strike --expiry --qty --premium-open --premium-close")
            t.update(kind=a.kind, strike=a.strike, expiry=a.expiry, qty=a.qty, multiplier=a.multiplier,
                     premium_open=a.premium_open, premium_close=a.premium_close,
                     **compute_option(a.side, a.qty, a.multiplier, a.premium_open, a.premium_close, a.fees or 0.0))
        else:
            entries = parse_fills(a.entries, a.entry, a.size)
            exits = parse_fills(a.exits, a.exit, a.size)
            t.update(entries=entries, exits=exits, stop=a.stop, leverage=a.leverage, margin=a.margin,
                     **compute_linear(a.side, entries, exits, a.stop, a.fees, a.fee_rate, a.funding))
            age_days = (pd.Timestamp.now(tz="UTC") - opened).days
            tf = "15M" if age_days < 55 else "1H"
            try:
                bars = fetch_prices.get_ohlcv(sym, tf, bars=min(5000, max(200, int((pd.Timestamp.now(tz="UTC") - opened)
                                                                           / pd.Timedelta(minutes=15 if tf == "15M" else 60)) + 50)))
                t.update(excursions(bars, a.side, t["entry"], a.stop, a.t1, opened, closed))
            except fetch_prices.FetchError as exc:
                print(f"note: MAE/MFE unknown ({str(exc).split('.')[0]})")
        tid = record(t)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(format_entry(tid, t).rstrip())
    if a.session:
        outcome = f"{tid}, {('%+.2fR' % t['R']) if t['R'] is not None else 'R undefined'}, T1 before stop: {t.get('t1_first') or 'unknown'}"
        print("Session outcome updated." if update_session_outcome(a.session, outcome) else f"note: session {a.session} not found")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
