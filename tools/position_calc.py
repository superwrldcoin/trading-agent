"""Position math for a planned trade: liquidation, P&L/ROE per target, R:R, pyramid tranches.

Formulas follow skills/leveraged-position-math.md and skills/risk-and-sizing.md
(isolated margin, linear USDT-style contracts). Math only: never places orders.

Usage examples:
  python tools/position_calc.py --asset BCH/USDT --side long --entry 309.80 --stop 293.64 \
      --targets 322.60 366.10 --leverage 10 --mmr 0.005 --fee 0.0005 --equity 10000 --risk-pct 1 --atr 5.32
  python tools/position_calc.py --asset XAU/USD --side short --zone 4207.05 4221.60 --stop 4236.16 \
      --targets 4143.70 4117.50 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

TRANCHE_WEIGHTS = (0.30, 0.30, 0.40)  # first touch -> best price; largest piece at the best price
DEFAULT_TARGET_WEIGHTS = {1: [1.0], 2: [0.5, 0.5], 3: [0.4, 0.4, 0.2]}


@dataclass
class Plan:
    asset: str
    side: str                                  # "long" | "short"
    stop: float
    targets: list[float]
    entry: float | None = None                 # single entry, or use `zone`
    zone: tuple[float, float] | None = None    # (low, high) -> 30/30/40 pyramid tranches
    weights: list[float] | None = None         # scale-out per target; default 100 / 50-50 / 40-40-20
    leverage: float = 1.0
    mmr: float = 0.005                         # maintenance margin rate
    fee: float = 0.0005                        # taker fee per side, as a fraction
    size: float | None = None                  # units; or derive from equity + risk_pct
    equity: float | None = None
    risk_pct: float | None = None              # e.g. 1 = 1% of equity
    atr: float | None = None                   # enables ATR-based buffer and max-leverage check
    funding_rate: float = 0.0                  # per 8h, as a fraction
    hold_hours: float = 0.0
    notes: list[str] = field(default_factory=list)


def tranches(side: str, low: float, high: float) -> list[tuple[float, float]]:
    """Pyramid entries across the zone, first touch -> best price."""
    mid = (low + high) / 2
    prices = [high, mid, low] if side == "long" else [low, mid, high]
    return list(zip(prices, TRANCHE_WEIGHTS))


def liquidation_price(side: str, entry: float, leverage: float, mmr: float) -> float:
    if 1 / leverage <= mmr:
        raise ValueError(f"leverage {leverage}x is too high for MMR {mmr:.2%}: liquidation at or beyond entry")
    if side == "long":
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)


def max_leverage(side: str, entry: float, stop: float, mmr: float, buffer: float) -> float:
    """Highest leverage that keeps liquidation at least `buffer` beyond the stop."""
    if side == "long":
        denom = 1 + mmr - (stop - buffer) / entry
    else:
        denom = (stop + buffer) / entry - 1 + mmr
    return float("inf") if denom <= 0 else 1 / denom


def validate(p: Plan) -> None:
    if p.side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    if (p.entry is None) == (p.zone is None):
        raise ValueError("give exactly one of entry or zone")
    if not p.targets:
        raise ValueError("at least one target is required")
    if p.leverage < 1:
        raise ValueError("leverage must be >= 1")
    if not 0 <= p.mmr < 0.5 or p.fee < 0:
        raise ValueError("mmr must be in [0, 0.5) and fee >= 0")
    if p.zone and p.zone[0] >= p.zone[1]:
        raise ValueError("zone must be (low, high) with low < high")
    if p.weights is not None:
        if len(p.weights) != len(p.targets) or abs(sum(p.weights) - 1) > 1e-6:
            raise ValueError("weights must match targets and sum to 1")
    if (p.equity is None) != (p.risk_pct is None):
        raise ValueError("equity and risk_pct go together")


def compute(p: Plan) -> dict:
    validate(p)
    s = 1 if p.side == "long" else -1
    legs = tranches(p.side, *p.zone) if p.zone else [(p.entry, 1.0)]
    entry = sum(px * w for px, w in legs)

    if s * (entry - p.stop) <= 0:
        raise ValueError(f"stop {p.stop} is on the wrong side of entry {entry:.4f} for a {p.side}")
    if p.zone and s * (min(p.zone) if p.side == "long" else max(p.zone)) <= s * p.stop:
        raise ValueError("stop must be beyond the whole zone")
    bad = [t for t in p.targets if s * (t - entry) <= 0]
    if bad:
        raise ValueError(f"targets {bad} are not in profit for a {p.side} from {entry:.4f}")
    targets = sorted(p.targets, key=lambda t: s * t)
    weights = p.weights or DEFAULT_TARGET_WEIGHTS.get(len(targets), [1 / len(targets)] * len(targets))

    risk_per_unit = abs(entry - p.stop)
    per_unit_with_fees = risk_per_unit + entry * p.fee + p.stop * p.fee
    notes = list(p.notes)
    if p.size is not None:
        units = p.size
    elif p.equity is not None:
        units = p.equity * p.risk_pct / 100 / per_unit_with_fees
    else:
        units = 1.0
        notes.append("size [MISSING]: dollar figures are per 1 unit")

    notional = units * entry
    margin = notional / p.leverage
    liq = liquidation_price(p.side, entry, p.leverage, p.mmr)
    liq_before_stop = s * (liq - p.stop) >= 0
    buffer = s * (p.stop - liq)
    entry_fee = notional * p.fee
    funding = notional * p.funding_rate * (p.hold_hours / 8)

    rows = []
    weighted_r = 0.0
    for t, w in zip(targets, weights):
        r = s * (t - entry) / risk_per_unit
        gross = w * units * s * (t - entry)
        net = gross - w * units * t * p.fee - w * entry_fee
        rows.append({"target": t, "weight": w, "R": r, "gross": gross, "net": net,
                     "roe_pct": net / (margin * w) * 100, "dist_pct": (t / entry - 1) * 100})
        weighted_r += w * r
    stop_net = units * s * (p.stop - entry) - units * p.stop * p.fee - entry_fee

    out = {
        "asset": p.asset, "side": p.side, "entry": entry,
        "tranches": [{"price": px, "weight": w} for px, w in legs] if p.zone else None,
        "stop": p.stop, "units": units, "notional": notional, "leverage": p.leverage, "margin": margin,
        "effective_leverage": notional / p.equity if p.equity else None,
        "liquidation": liq, "liq_dist_pct": (liq / entry - 1) * 100,
        "liq_buffer_vs_stop": buffer, "liq_buffer_atr": buffer / p.atr if p.atr else None,
        "liquidates_before_stop": liq_before_stop,
        "max_leverage_1atr": max_leverage(p.side, entry, p.stop, p.mmr, p.atr) if p.atr else None,
        "risk_per_unit": risk_per_unit, "stop_width_atr": risk_per_unit / p.atr if p.atr else None,
        "targets": rows, "weighted_R": weighted_r,
        "stop_net": stop_net, "stop_roe_pct": stop_net / margin * 100,
        "funding_estimate": funding, "notes": notes,
    }
    if liq_before_stop:
        notes.append("FAIL: liquidation is reached before the stop")
    elif p.atr and buffer < p.atr:
        notes.append(f"FAIL: liquidation only {buffer / p.atr:.2f} ATR beyond stop (< 1 ATR)")
    if p.atr and risk_per_unit / p.atr > 3:
        notes.append(f"wide stop: {risk_per_unit / p.atr:.2f} ATR (> 3)")
    if rows[0]["R"] < 0.5:
        notes.append(f"T1 only {rows[0]['R']:.2f}R (< 0.5)")
    if weighted_r < 2.0:
        notes.append(f"weighted R {weighted_r:.2f} below the 2.0 default minimum")
    return out


def render(res: dict) -> str:
    f = lambda x, nd=2: "n/a" if x is None else f"{x:,.{nd}f}"
    lines = [f"Position: {res['asset']} {res['side']}, {f(res['leverage'], 1)}x isolated", "",
             "| Item | Value |", "|---|---|",
             f"| Entry (blended) | {f(res['entry'])} |"]
    if res["tranches"]:
        lines.append("| Tranches | " + ", ".join(f"{f(t['price'])} ({t['weight']:.0%})" for t in res["tranches"]) + " |")
    lines += [
        f"| Stop | {f(res['stop'])} (width {f(res['stop_width_atr'])} ATR) |",
        f"| Units / notional / margin | {f(res['units'], 4)} / {f(res['notional'])} / {f(res['margin'])} |",
        f"| Liquidation | {f(res['liquidation'])} ({f(res['liq_dist_pct'])}% from entry; buffer vs stop "
        f"{f(res['liq_buffer_vs_stop'])} = {f(res['liq_buffer_atr'])} ATR) |",
        f"| Max leverage (liq >= 1 ATR beyond stop) | {f(res['max_leverage_1atr'])} |",
        f"| Net P&L at stop | {f(res['stop_net'])} (ROE {f(res['stop_roe_pct'], 1)}%) |",
        f"| Weighted R | {f(res['weighted_R'])} |", "",
        "| Target | Weight | R | Net P&L | ROE | Dist |", "|---|---|---|---|---|---|",
    ]
    lines += [f"| {f(t['target'])} | {t['weight']:.0%} | {f(t['R'])} | {f(t['net'])} | {f(t['roe_pct'], 1)}% | "
              f"{f(t['dist_pct'])}% |" for t in res["targets"]]
    if res["notes"]:
        lines += ["", "Notes:"] + [f"- {n}" for n in res["notes"]]
    return "\n".join(lines)


def parse_args(argv: list[str]) -> tuple[Plan, bool]:
    ap = argparse.ArgumentParser(description="Position math: liquidation, P&L/ROE per target, R:R, tranches.")
    ap.add_argument("--asset", required=True)
    ap.add_argument("--side", required=True, choices=["long", "short"])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--entry", type=float)
    g.add_argument("--zone", type=float, nargs=2, metavar=("LOW", "HIGH"))
    ap.add_argument("--stop", type=float, required=True)
    ap.add_argument("--targets", type=float, nargs="+", required=True)
    ap.add_argument("--weights", type=float, nargs="+", help="fractions summing to 1, in target order")
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--mmr", type=float, default=0.005)
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--size", type=float)
    ap.add_argument("--equity", type=float)
    ap.add_argument("--risk-pct", type=float)
    ap.add_argument("--atr", type=float)
    ap.add_argument("--funding-rate", type=float, default=0.0)
    ap.add_argument("--hold-hours", type=float, default=0.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    plan = Plan(asset=a.asset, side=a.side, stop=a.stop, targets=a.targets, entry=a.entry,
                zone=tuple(a.zone) if a.zone else None, weights=a.weights, leverage=a.leverage, mmr=a.mmr,
                fee=a.fee, size=a.size, equity=a.equity, risk_pct=a.risk_pct, atr=a.atr,
                funding_rate=a.funding_rate, hold_hours=a.hold_hours)
    return plan, a.json


def main(argv: list[str]) -> int:
    plan, as_json = parse_args(argv)
    try:
        res = compute(plan)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(res, indent=2, default=float) if as_json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
