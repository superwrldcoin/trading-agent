"""Portfolio exposure and stress test across all open positions (memory/positions.json).

  python tools/portfolio.py                  exposure + correlation + open risk + stress test
  python tools/portfolio.py --shocks -3 -5 -10 3 5 10 [--json]

- Exposure by theme (crypto, precious metals, equities): net and gross notional (options as delta-notional), % of equity.
- Correlation of daily returns between held symbols over 30 and 90 days; flags pairs >= 0.7 as one bet.
- Open risk: loss from the current price to each stop (options: premium at risk / unlimited for short options).
- Stress test: every position moved by each shock at once, in two modes:
    orderly - stops fill at the stop price; isolated positions lose at most their margin (liquidation)
    gap     - stops fill at the shocked price (weekend / overnight / flash gap); same liquidation cap
  plus a historical-gap scenario: each gap-prone market (metals, equities) moved by its 95th-percentile opening gap
  against the position over the last year.
Options are revalued with Black-Scholes at the shocked spot, same IV, no time decay (instant shock).
Skills: skills/portfolio-exposure.md, skills/stress-test.md.
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

from tools import fetch_prices, levels, positions  # noqa: E402
from tools import options_math as om  # noqa: E402
from tools import position_calc as pc  # noqa: E402

THEMES = {"BTC/USDT": "crypto", "BCH/USDT": "crypto", "XAU/USD": "precious metals", "SI": "precious metals",
          "GLD": "precious metals", "MSFT": "equities"}
DEFAULT_SHOCKS = (-10.0, -5.0, -3.0, 3.0, 5.0, 10.0)
RISK_FREE = 0.04  # [ASSUMPTION] for option valuation; pass --rate to change
CORR_FLAG = 0.7


def gap_prone(symbol: str) -> bool:
    return levels.WATCHLIST[symbol].session is not levels.CRYPTO


def years_to(expiry: str, now: pd.Timestamp) -> float:
    exp = pd.Timestamp(expiry).tz_localize("UTC") + pd.Timedelta(hours=20)  # ~US close
    return max((exp - now).total_seconds() / (365 * 86400), 0.0)


def option_iv(p: dict, S: float, T: float, r: float) -> tuple[float | None, str]:
    if p.get("iv"):
        return float(p["iv"]), "entry IV"
    iv = om.implied_vol(p["kind"], p["premium"], S, p["strike"], T, r)
    return iv, "IV implied from entry premium at today's spot [ASSUMPTION]"


def value_position(p: dict, S: float, now: pd.Timestamp, r: float) -> dict:
    """Current state of one position at underlying price S."""
    s = 1 if p["side"] == "long" else -1
    if p["instrument"] == "option":
        T = years_to(p["expiry"], now)
        iv, iv_src = option_iv(p, S, T, r)
        sigma = iv or 0.0
        units = p["qty"] * p["multiplier"]
        val = om.price(p["kind"], S, p["strike"], T, r, sigma)
        g = om.greeks(p["kind"], S, p["strike"], T, r, sigma)
        return {"value": val, "pnl": s * (val - p["premium"]) * units, "delta_units": s * g["delta"] * units,
                "notional": s * g["delta"] * units * S, "T": T, "iv": iv, "iv_source": iv_src,
                "greeks": {k: s * v * units for k, v in g.items()},
                "risk_to_stop": (val * units if s == 1 else None), "margin": None, "liquidation": None}
    units = p["size"]
    margin = p["entry"] * units / p["leverage"]
    liq = None
    if p["leverage"] > 1 and p.get("margin", "isolated") == "isolated":
        liq = pc.liquidation_price(p["side"], p["entry"], p["leverage"], p.get("mmr", 0.005))
    stop = p.get("stop")
    risk = None if stop is None else max(0.0, s * (S - stop) * units)
    return {"pnl": s * (S - p["entry"]) * units, "delta_units": s * units, "notional": s * units * S,
            "margin": margin, "liquidation": liq, "risk_to_stop": risk}


def shocked_pnl(p: dict, S: float, S_new: float, now: pd.Timestamp, r: float, mode: str) -> dict:
    """P&L of the position (vs entry) after the underlying jumps from S to S_new."""
    s = 1 if p["side"] == "long" else -1
    if p["instrument"] == "option":
        T = years_to(p["expiry"], now)
        iv, _ = option_iv(p, S, T, r)
        val = om.price(p["kind"], S_new, p["strike"], T, r, iv or 0.0)
        return {"pnl": s * (val - p["premium"]) * p["qty"] * p["multiplier"], "event": ""}
    units, entry = p["size"], p["entry"]
    cur = value_position(p, S, now, r)
    liq, stop = cur["liquidation"], p.get("stop")
    crossed = lambda lvl: lvl is not None and s * (S_new - lvl) <= 0 and s * (S - lvl) > 0
    if crossed(liq) and not (crossed(stop) and s * (stop - liq) > 0 and mode == "orderly"):
        return {"pnl": -cur["margin"], "event": "LIQUIDATED"}
    if crossed(stop):
        fill = stop if mode == "orderly" else S_new
        return {"pnl": s * (fill - entry) * units, "event": "stopped" + ("" if mode == "orderly" else " (gap fill)")}
    return {"pnl": s * (S_new - entry) * units, "event": ""}


def historical_gap(daily: pd.DataFrame, side: str, pct: float = 95) -> float | None:
    """95th-percentile opening gap against the side over the last ~252 sessions, as a fraction (positive)."""
    d = daily.tail(253)
    gap = (d["open"] / d["close"].shift() - 1).dropna()
    if len(gap) < 30:
        return None
    adverse = (-gap if side == "long" else gap).clip(lower=0)
    return float(np.percentile(adverse, pct))


def correlations(daily: dict[str, pd.DataFrame]) -> list[dict]:
    syms = sorted(daily)
    rets = {s: daily[s]["close"].pct_change() for s in syms}
    rets = {s: r.set_axis(r.index.tz_convert("UTC").normalize() if r.index.tz else r.index.normalize()) for s, r in rets.items()}
    rows = []
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            j = pd.concat([rets[a], rets[b]], axis=1, join="inner").dropna()
            rows.append({"a": a, "b": b,
                         "rho30": float(j.tail(30).corr().iloc[0, 1]) if len(j) >= 20 else None,
                         "rho90": float(j.tail(90).corr().iloc[0, 1]) if len(j) >= 60 else None})
    return rows


def analyze(data: dict, prices: dict[str, float], daily: dict[str, pd.DataFrame], now: pd.Timestamp,
            shocks=DEFAULT_SHOCKS, r: float = RISK_FREE) -> dict:
    eq = data.get("equity")
    rows, themes = [], {}
    for p in data["positions"]:
        S = prices[p["symbol"]]
        v = value_position(p, S, now, r)
        rows.append({"position": p, "price": S, **v})
        t = themes.setdefault(THEMES[p["symbol"]], {"net": 0.0, "gross": 0.0, "symbols": set()})
        t["net"] += v["notional"]
        t["gross"] += abs(v["notional"])
        t["symbols"].add(p["symbol"])
    for t in themes.values():
        t["symbols"] = sorted(t["symbols"])
        t["net_pct"] = t["net"] / eq * 100 if eq else None
        t["gross_pct"] = t["gross"] / eq * 100 if eq else None
    risks = [x["risk_to_stop"] for x in rows]
    no_stop = [x["position"]["id"] for x in rows if x["risk_to_stop"] is None]
    open_risk = sum(x for x in risks if x is not None)
    corr = correlations({s: daily[s] for s in {p["symbol"] for p in data["positions"]} if s in daily})

    scenarios = []
    for mode in ("orderly", "gap"):
        for sh in shocks:
            res = [shocked_pnl(x["position"], x["price"], x["price"] * (1 + sh / 100), now, r, mode) for x in rows]
            total = sum(z["pnl"] for z in res)
            scenarios.append({"name": f"all {sh:+g}%", "mode": mode, "total_pnl": total,
                              "account": eq + total if eq is not None else None,
                              "events": [f"{x['position']['id']} {z['event']}" for x, z in zip(rows, res) if z["event"]]})
    gap_res, gap_notes = [], []
    for x in rows:
        sym, side = x["position"]["symbol"], x["position"]["side"]
        g = historical_gap(daily[sym], side) if gap_prone(sym) and sym in daily else None
        if g:
            S_new = x["price"] * (1 - g if side == "long" else 1 + g)
            gap_notes.append(f"{x['position']['id']} {sym}: {g:.2%} against")
        else:
            S_new = x["price"]
        gap_res.append(shocked_pnl(x["position"], x["price"], S_new, now, r, "gap"))
    total = sum(z["pnl"] for z in gap_res)
    scenarios.append({"name": "historical 95th-pct opening gap (gap-prone markets)", "mode": "gap", "total_pnl": total,
                      "account": eq + total if eq is not None else None, "detail": gap_notes,
                      "events": [f"{x['position']['id']} {z['event']}" for x, z in zip(rows, gap_res) if z["event"]]})

    flags = []
    for c in corr:
        hi = max((abs(v) for v in (c["rho30"], c["rho90"]) if v is not None), default=0)
        if hi >= CORR_FLAG:
            flags.append(("warn", f"{c['a']} and {c['b']} correlate {hi:.2f}: treat them as one bet"))
    if no_stop:
        flags.append(("warn", f"no defined risk (no stop / short option): {', '.join(no_stop)}"))
    if eq:
        for name, t in themes.items():
            if t["gross_pct"] and t["gross_pct"] > 100:
                flags.append(("warn", f"{name} gross exposure {t['gross_pct']:.0f}% of equity"))
        worst = min(scenarios, key=lambda s: s["total_pnl"])
        if worst["total_pnl"] < -0.2 * eq:
            flags.append(("fail", f"worst scenario ({worst['name']}, {worst['mode']}) loses "
                                  f"{-worst['total_pnl'] / eq:.0%} of equity"))
    else:
        flags.append(("info", "equity not set: % figures skipped (python tools/positions.py set-equity N)"))
    return {"equity": eq, "rows": rows, "themes": themes, "open_risk": open_risk,
            "open_risk_pct": open_risk / eq * 100 if eq else None, "correlations": corr,
            "scenarios": scenarios, "flags": flags}


def render(a: dict) -> str:
    f = lambda x, nd=2: "n/a" if x is None else f"{x:,.{nd}f}"
    if not a["rows"]:
        return "No open positions. Add one with `python tools/positions.py add ...`.\n"
    lines = ["## Portfolio", f"Equity: {f(a['equity'])} [DATA:user]", "",
             "| Position | Price | P&L | Notional (delta) | Margin | Liquidation | Risk to stop |", "|---|---|---|---|---|---|---|"]
    for x in a["rows"]:
        lines.append(f"| {positions.describe(x['position'])} | {f(x['price'])} | {f(x['pnl'])} | {f(x['notional'])} | "
                     f"{f(x['margin'])} | {f(x['liquidation'])} | {f(x['risk_to_stop']) if x['risk_to_stop'] is not None else 'undefined'} |")
    lines += ["", "| Theme | Symbols | Net notional | Gross | Net % eq | Gross % eq |", "|---|---|---|---|---|---|"]
    for name, t in sorted(a["themes"].items()):
        lines.append(f"| {name} | {', '.join(t['symbols'])} | {f(t['net'])} | {f(t['gross'])} | {f(t['net_pct'], 0)} | {f(t['gross_pct'], 0)} |")
    lines.append(f"\nOpen risk to stops: {f(a['open_risk'])}" + (f" = {a['open_risk_pct']:.2f}% of equity" if a["open_risk_pct"] is not None else "") + " [CALC]")
    if a["correlations"]:
        lines += ["", "| Pair | rho 30d | rho 90d |", "|---|---|---|"]
        lines += [f"| {c['a']} / {c['b']} | {f(c['rho30'])} | {f(c['rho90'])} |" for c in a["correlations"]]
    lines += ["", "| Scenario | Mode | Total P&L | Account after | Events |", "|---|---|---|---|---|"]
    for s in a["scenarios"]:
        lines.append(f"| {s['name']} | {s['mode']} | {f(s['total_pnl'])} | {f(s['account'])} | {'; '.join(s['events']) or '-'} |")
    gap = a["scenarios"][-1].get("detail")
    if gap:
        lines.append(f"\nHistorical gaps used: {'; '.join(gap)} (last ~252 sessions) [CALC]")
    lines.append("Shocks are instant: options revalued at the same IV with no time decay; stops in 'gap' mode fill at the "
                 "shocked price. Not a forecast [CALC].")
    if a["flags"]:
        order = {"fail": 0, "warn": 1, "info": 2}
        lines += [""] + [f"- **{l.upper()}**: {m}" for l, m in sorted(a["flags"], key=lambda z: order[z[0]])]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Portfolio exposure and stress test.")
    ap.add_argument("--shocks", type=float, nargs="+", default=list(DEFAULT_SHOCKS))
    ap.add_argument("--rate", type=float, default=RISK_FREE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    data = positions.load()
    syms = sorted({p["symbol"] for p in data["positions"]})
    prices, daily = {}, {}
    try:
        for s in syms:
            prices[s] = float(fetch_prices.get_ohlcv(s, "15M", bars=50)["close"].iloc[-1])
            daily[s] = fetch_prices.get_ohlcv(s, "1D", bars=400)
    except fetch_prices.FetchError as exc:
        print(f"FETCH FAILED: {exc}")
        return 1
    res = analyze(data, prices, daily, pd.Timestamp.now(tz="UTC"), a.shocks, a.rate)
    print(json.dumps(res, indent=2, default=lambda o: sorted(o) if isinstance(o, set) else str(o)) if a.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
