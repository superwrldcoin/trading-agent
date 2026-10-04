"""Leverage-volatility gate: is the liquidation (and stop) distance safely outside the asset's normal movement?

Usage:  python tools/vol_check.py BTC/USDT long --leverage 40 [--entry 85000] [--stop 83500] [--mmr 0.005] [--json]

Reports the liquidation and stop distances in %, 4H ATR, daily ATR, and average daily range, plus the
**historical frequency** with which price moved at least that far against the position within 1/3/5/10 days
(daily highs/lows over the last ~2 years, measured from each day's close). Also gives the highest leverage that
kept the 5-day liquidation-touch frequency at or below 5%. Historical frequency, not a forecast:
volatility regimes change. Skill: skills/leverage-volatility-check.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import fetch_prices, indicators, levels  # noqa: E402
from tools import position_calc as pc  # noqa: E402

HORIZONS = (1, 3, 5, 10)
LOOKBACK = 500          # daily bars used for the frequencies
SAFE_PROB_5D = 0.05     # leverage suggestion: <= 5% historical 5-day liquidation-touch frequency


def adverse_excursion(daily: pd.DataFrame, side: str, horizon: int) -> pd.Series:
    """Largest move against the position over the next `horizon` bars, as a fraction of each bar's close."""
    c = daily["close"]
    if side == "long":
        worst = daily["low"][::-1].rolling(horizon, min_periods=horizon).min()[::-1].shift(-1)
        exc = (c - worst) / c
    else:
        worst = daily["high"][::-1].rolling(horizon, min_periods=horizon).max()[::-1].shift(-1)
        exc = (worst - c) / c
    return exc.clip(lower=0).dropna()


def touch_probability(daily: pd.DataFrame, side: str, dist_frac: float, horizons=HORIZONS,
                      lookback: int = LOOKBACK) -> dict[int, float | None]:
    d = daily.tail(lookback + max(horizons))
    out = {}
    for n in horizons:
        exc = adverse_excursion(d, side, n).tail(lookback)
        out[n] = None if len(exc) < 30 else float((exc >= dist_frac).mean())
    return out


def leverage_for_odds(daily: pd.DataFrame, side: str, mmr: float, horizon: int = 5,
                      max_prob: float = SAFE_PROB_5D, lookback: int = LOOKBACK) -> float | None:
    """Highest leverage whose liquidation distance (1/L - mmr) exceeded the (1 - max_prob) quantile of
    `horizon`-day adverse excursions."""
    exc = adverse_excursion(daily.tail(lookback + horizon), side, horizon).tail(lookback)
    if len(exc) < 30:
        return None
    q = float(exc.quantile(1 - max_prob))
    return max(1.0, 1 / (q + mmr))


def analyze(daily: pd.DataFrame, df4: pd.DataFrame, side: str, entry: float, leverage: float,
            stop: float | None, mmr: float) -> dict:
    s = 1 if side == "long" else -1
    liq = pc.liquidation_price(side, entry, leverage, mmr)
    atr4 = float(indicators.atr(df4).iloc[-1])
    atr_d = float(indicators.atr(daily).iloc[-1])
    adr = float(((daily["high"] - daily["low"]) / daily["close"]).tail(30).mean())
    liq_dist = abs(entry - liq) / entry
    res = {"side": side, "entry": entry, "leverage": leverage, "mmr": mmr, "liquidation": liq,
           "liq_dist_pct": liq_dist * 100, "liq_atr_4h": abs(entry - liq) / atr4, "liq_atr_1d": abs(entry - liq) / atr_d,
           "liq_adr": liq_dist / adr if adr else None, "atr_4h": atr4, "atr_1d": atr_d, "adr_pct": adr * 100,
           "liq_touch": touch_probability(daily, side, liq_dist),
           "safe_leverage_5d": leverage_for_odds(daily, side, mmr), "lookback_days": min(LOOKBACK, len(daily)),
           "stop": stop, "flags": []}
    flags = res["flags"]
    if stop is not None:
        stop_dist = abs(entry - stop) / entry
        res.update(stop_dist_pct=stop_dist * 100, stop_atr_4h=abs(entry - stop) / atr4,
                   stop_touch=touch_probability(daily, side, stop_dist))
        if s * (stop - liq) <= 0:
            flags.append(("fail", f"stop {stop:,.2f} is beyond the liquidation price {liq:,.2f}: the position is "
                                  "liquidated before the stop can fill"))
        elif abs(stop - liq) < atr4:
            flags.append(("warn", f"only {abs(stop - liq) / atr4:.2f} 4H ATR between stop and liquidation: a fast "
                                  "move or a gap can skip the stop and liquidate"))
    p1, p5 = res["liq_touch"].get(1), res["liq_touch"].get(5)
    if res["liq_atr_1d"] < 1:
        flags.append(("fail", f"liquidation is {res['liq_atr_1d']:.2f} daily ATR away: inside one normal day's move"))
    elif res["liq_atr_1d"] < 2:
        flags.append(("warn", f"liquidation is only {res['liq_atr_1d']:.2f} daily ATR away"))
    if p1 is not None and p1 > 0.05:
        flags.append(("fail", f"price moved {res['liq_dist_pct']:.2f}% against a {side} within 1 day on "
                              f"{p1:.0%} of days in the lookback"))
    elif p5 is not None and p5 > 0.10:
        flags.append(("warn", f"{p5:.0%} historical odds of a liquidation-size move within 5 days"))
    if res["safe_leverage_5d"] and leverage > res["safe_leverage_5d"]:
        flags.append(("warn", f"{leverage:g}x is above {res['safe_leverage_5d']:.1f}x, the leverage that kept "
                              f"5-day liquidation-touch odds <= {SAFE_PROB_5D:.0%} historically"))
    return res


def render(symbol: str, r: dict) -> str:
    f = lambda x, nd=2: "n/a" if x is None else f"{x:,.{nd}f}"
    pct = lambda p: "n/a" if p is None else f"{p:.1%}"
    lines = [f"### Leverage vs volatility: {symbol} {r['side']} {r['leverage']:g}x from {f(r['entry'])}", "",
             f"Liquidation {f(r['liquidation'])}: {r['liq_dist_pct']:.2f}% away = {r['liq_atr_4h']:.2f} x 4H ATR "
             f"({f(r['atr_4h'])}) = {r['liq_atr_1d']:.2f} x daily ATR ({f(r['atr_1d'])}) = "
             f"{f(r['liq_adr'])} x avg daily range ({r['adr_pct']:.2f}%) [CALC]", "",
             "| Distance | " + " | ".join(f"touched within {n}d" for n in HORIZONS) + " |",
             "|---|" + "---|" * len(HORIZONS),
             f"| Liquidation ({r['liq_dist_pct']:.2f}%) | " + " | ".join(pct(r["liq_touch"][n]) for n in HORIZONS) + " |"]
    if r.get("stop") is not None:
        lines.append(f"| Stop ({r['stop_dist_pct']:.2f}%, {r['stop_atr_4h']:.2f} 4H ATR) | "
                     + " | ".join(pct(r["stop_touch"][n]) for n in HORIZONS) + " |")
    lines += ["", f"Historical frequency over the last {r['lookback_days']} daily bars (daily highs/lows from each close), "
                  "not a forecast [CALC].",
              f"Leverage that kept 5-day liquidation-touch odds <= {SAFE_PROB_5D:.0%}: {f(r['safe_leverage_5d'], 1)}x [CALC]"]
    order = {"fail": 0, "warn": 1}
    if r["flags"]:
        lines += [""] + [f"- **{l.upper()}**: {m}" for l, m in sorted(r["flags"], key=lambda x: order[x[0]])]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Leverage-volatility gate.")
    ap.add_argument("symbol")
    ap.add_argument("side", choices=["long", "short"])
    ap.add_argument("--leverage", type=float, required=True)
    ap.add_argument("--entry", type=float, help="default: current price")
    ap.add_argument("--stop", type=float)
    ap.add_argument("--mmr", type=float, default=0.005)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sym = a.symbol.upper()
    if sym not in levels.WATCHLIST:
        print(f"ERROR: {sym} not on the watchlist ({', '.join(levels.WATCHLIST)})")
        return 2
    try:
        daily = fetch_prices.get_ohlcv(sym, "1D")
        df4 = fetch_prices.get_ohlcv(sym, "4H")
        entry = a.entry or float(df4["close"].iloc[-1])
        res = analyze(daily, df4, a.side, entry, a.leverage, a.stop, a.mmr)
    except fetch_prices.FetchError as exc:
        print(f"FETCH FAILED: {exc}")
        return 1
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(res, indent=2, default=float) if a.json else render(sym, res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
