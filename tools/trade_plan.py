"""Trade-plan manager: staged entries, average entry and liquidation after each add, take-profit ladder,
breakeven and trailing rules, optional add-on in profit.

  python tools/trade_plan.py --asset BCH/USDT --side long --zone 296.30 298.96 --size 30 --stop 293.64 \
      --targets 308.60 322.60 366.10 --leverage 10 [--equity 10000 --risk-pct 1] [--atr 5.32 --trail-atr 1.5] \
      [--breakeven-after 1] [--addon 309:10]
  python tools/trade_plan.py ... --stages 298.96:9 297.63:9 296.30:12      explicit price:size stages

Answers "if I add at zone 2, what's my new average, liquidation and R:R?": every stage row shows the position
as it stands after that fill. Isolated margin, same leverage on every add, linear contracts. Math only.
Skill: skills/trade-plan.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import position_calc as pc  # noqa: E402


def parse_stage(s: str) -> tuple[float, float]:
    price, size = s.split(":")
    return float(price.replace(",", "")), float(size.replace(",", ""))


def build_stages(side: str, zone: tuple[float, float] | None, stages: list[tuple[float, float]] | None,
                 total_size: float | None) -> list[tuple[float, float]]:
    if stages:
        return stages
    if zone is None or total_size is None:
        raise ValueError("give --stages, or --zone with --size (or --equity/--risk-pct to size it)")
    return [(px, w * total_size) for px, w in pc.tranches(side, *zone)]


def size_from_risk(side: str, zone_or_stages: list[tuple[float, float]], stop: float, equity: float,
                   risk_pct: float, fee: float) -> float:
    """Total size so that a full fill stopped out loses risk_pct of equity (fees included)."""
    w = [sz for _, sz in zone_or_stages]
    avg = sum(px * sz for px, sz in zone_or_stages) / sum(w)
    per_unit = abs(avg - stop) + avg * fee + stop * fee
    return equity * risk_pct / 100 / per_unit


def plan(side: str, stages: list[tuple[float, float]], stop: float, targets: list[float], leverage: float = 1.0,
         mmr: float = 0.005, fee: float = 0.0005, weights: list[float] | None = None, equity: float | None = None,
         atr: float | None = None, trail_atr: float | None = None, breakeven_after: int | None = 1,
         addon: tuple[float, float] | None = None) -> dict:
    s = 1 if side == "long" else -1
    if not stages:
        raise ValueError("no entry stages")
    if any(s * (px - stop) <= 0 for px, _ in stages):
        raise ValueError(f"stop {stop} must be beyond every entry stage for a {side}")
    targets = sorted(targets, key=lambda t: s * t)
    if not targets or any(s * (t - max(px * s for px, _ in stages) * s) <= 0 for t in targets):
        raise ValueError("targets must be beyond every entry stage in the trade direction")
    weights = weights or pc.DEFAULT_TARGET_WEIGHTS.get(len(targets), [1 / len(targets)] * len(targets))
    if len(weights) != len(targets) or abs(sum(weights) - 1) > 1e-6:
        raise ValueError("weights must match targets and sum to 1")

    flags, rows = [], []
    cum, cost = 0.0, 0.0
    prev_liq = None
    for i, (px, sz) in enumerate(stages, 1):
        if prev_liq is not None and s * (px - prev_liq) <= 0:
            flags.append(("fail", f"stage {i} at {px:,.2f} is beyond the liquidation price {prev_liq:,.2f} of the "
                                  f"earlier fills: the position is liquidated before this add can fill"))
        cum += sz
        cost += px * sz
        avg = cost / cum
        liq = pc.liquidation_price(side, avg, leverage, mmr) if leverage > 1 else None
        risk = cum * abs(avg - stop) + cost * fee + cum * stop * fee
        r_t = [s * (t - avg) / abs(avg - stop) for t in targets]
        rows.append({"stage": i, "price": px, "size": sz, "cum_size": cum, "avg_entry": avg, "margin": cost / leverage,
                     "liquidation": liq, "liq_beyond_stop": None if liq is None else s * (stop - liq) > 0,
                     "risk_to_stop": risk, "risk_pct": risk / equity * 100 if equity else None,
                     "R": r_t, "weighted_R": sum(w * r for w, r in zip(weights, r_t))})
        if liq is not None and s * (stop - liq) <= 0:
            flags.append(("fail", f"after stage {i}, liquidation {liq:,.2f} is hit before the stop {stop:,.2f}"))
        prev_liq = liq

    full = rows[-1]
    avg, size = full["avg_entry"], full["cum_size"]
    ladder, remaining, realized, cur_stop = [], size, -cost * fee, stop
    for k, (t, w) in enumerate(zip(targets, weights), 1):
        close = size * w
        remaining -= close
        realized += close * s * (t - avg) - close * t * fee
        if breakeven_after is not None and k == breakeven_after:
            cur_stop = avg
        if trail_atr and atr and k >= 2 and remaining > 1e-12:
            cur_stop = t - s * trail_atr * atr
        locked = remaining * s * (cur_stop - avg)
        ladder.append({"event": f"T{k} hit", "price": t, "closed": close, "remaining": max(remaining, 0.0),
                       "realized": realized, "stop_after": cur_stop if remaining > 1e-12 else None,
                       "remaining_risk": max(0.0, -locked) if remaining > 1e-12 else 0.0,
                       "locked_in": locked if remaining > 1e-12 else 0.0})

    add = None
    if addon:
        apx, asz = addon
        rem_after_t1 = size * (1 - weights[0])
        be_stop = avg if breakeven_after == 1 else stop
        if len(targets) > 1 and s * (apx - targets[1]) >= 0:
            flags.append(("warn", f"add-on at {apx:,.2f} is at or beyond T2 {targets[1]:,.2f}: chasing the next target"))
        if s * (apx - be_stop) <= 0:
            flags.append(("fail", f"add-on at {apx:,.2f} is at or beyond the protective stop {be_stop:,.2f}"))
        if asz > 0.5 * size:
            flags.append(("warn", f"add-on {asz:g} is more than 50% of the original size {size:g} (skill limit)"))
        new_size = rem_after_t1 + asz
        new_avg = (rem_after_t1 * avg + asz * apx) / new_size
        new_liq = pc.liquidation_price(side, new_avg, leverage, mmr) if leverage > 1 else None
        add_risk = asz * s * (apx - be_stop)
        total_risk = max(0.0, new_size * s * (new_avg - be_stop))
        add = {"price": apx, "size": asz, "new_size": new_size, "new_avg": new_avg, "liquidation": new_liq,
               "stop": be_stop, "addon_risk": add_risk, "total_risk_at_stop": total_risk,
               "original_risk": full["risk_to_stop"], "R_remaining": [s * (t - new_avg) / abs(new_avg - be_stop) if new_avg != be_stop else None
                               for t in targets[1:]]}
    if add and add["total_risk_at_stop"] > full["risk_to_stop"]:
        flags.append(("warn", f"after the add-on, the position risks {add['total_risk_at_stop']:,.2f} at its stop, more "
                              f"than the original plan's {full['risk_to_stop']:,.2f}"))
    if full["weighted_R"] < 2:
        flags.append(("warn", f"weighted R {full['weighted_R']:.2f} with every stage filled is below 2.0"))
    return {"side": side, "leverage": leverage, "stop": stop, "targets": targets, "weights": weights,
            "stages": rows, "ladder": ladder, "addon": add, "atr": atr, "trail_atr": trail_atr,
            "breakeven_after": breakeven_after, "equity": equity, "flags": flags}


def render(asset: str, p: dict) -> str:
    f = lambda x, nd=2: "n/a" if x is None else f"{x:,.{nd}f}"
    tl = " / ".join(f"T{i + 1} {f(t)} ({w:.0%})" for i, (t, w) in enumerate(zip(p["targets"], p["weights"])))
    lines = [f"### Trade plan: {asset} {p['side']} {p['leverage']:g}x, stop {f(p['stop'])}, targets {tl}", "",
             "| After stage | Fill | Size | Cum size | Avg entry | Margin | Liquidation | Risk to stop | "
             + " | ".join(f"R T{i + 1}" for i in range(len(p["targets"]))) + " | Weighted R |",
             "|---|---|---|---|---|---|---|---|" + "---|" * len(p["targets"]) + "---|"]
    for r in p["stages"]:
        risk = f(r["risk_to_stop"]) + (f" ({r['risk_pct']:.2f}%)" if r["risk_pct"] is not None else "")
        lines.append(f"| {r['stage']} | {f(r['price'])} | {f(r['size'], 4)} | {f(r['cum_size'], 4)} | {f(r['avg_entry'])} | "
                     f"{f(r['margin'])} | {f(r['liquidation'])} | {risk} | " + " | ".join(f"{x:.2f}" for x in r["R"])
                     + f" | {r['weighted_R']:.2f} |")
    rule = []
    if p["breakeven_after"]:
        rule.append(f"stop to breakeven after T{p['breakeven_after']}")
    if p["trail_atr"] and p["atr"]:
        rule.append(f"trail {p['trail_atr']:g} x ATR ({p['trail_atr'] * p['atr']:,.2f}) behind each target from T2")
    lines += ["", "Take-profit ladder (all stages filled" + (", " + ", ".join(rule) if rule else "") + "):", "",
              "| Event | Price | Closed | Remaining | Realized P&L | Stop after | Risk left | Locked in |",
              "|---|---|---|---|---|---|---|---|"]
    for e in p["ladder"]:
        lines.append(f"| {e['event']} | {f(e['price'])} | {f(e['closed'], 4)} | {f(e['remaining'], 4)} | {f(e['realized'])} | "
                     f"{f(e['stop_after'])} | {f(e['remaining_risk'])} | {f(e['locked_in'])} |")
    a = p["addon"]
    if a:
        lines += ["", f"Add-on after T1: {f(a['size'], 4)} at {f(a['price'])} -> size {f(a['new_size'], 4)}, avg {f(a['new_avg'])}, "
                      f"liquidation {f(a['liquidation'])}, stop {f(a['stop'])}; add-on risk {f(a['addon_risk'])}, "
                      f"position risk at stop {f(a['total_risk_at_stop'])}; R to remaining targets "
                      + ", ".join("n/a" if r is None else f"{r:.2f}" for r in a["R_remaining"])]
    if p["flags"]:
        order = {"fail": 0, "warn": 1}
        lines += [""] + [f"- **{l.upper()}**: {m}" for l, m in sorted(p["flags"], key=lambda x: order[x[0]])]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Trade-plan manager.")
    ap.add_argument("--asset", required=True)
    ap.add_argument("--side", required=True, choices=["long", "short"])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stages", nargs="+", help="PRICE:SIZE ... in fill order")
    g.add_argument("--zone", type=float, nargs=2, metavar=("LOW", "HIGH"))
    ap.add_argument("--size", type=float, help="total size for --zone (30/30/40)")
    ap.add_argument("--stop", type=float, required=True)
    ap.add_argument("--targets", type=float, nargs="+", required=True)
    ap.add_argument("--weights", type=float, nargs="+")
    ap.add_argument("--leverage", type=float, default=1.0)
    ap.add_argument("--mmr", type=float, default=0.005)
    ap.add_argument("--fee", type=float, default=0.0005)
    ap.add_argument("--equity", type=float)
    ap.add_argument("--risk-pct", type=float)
    ap.add_argument("--atr", type=float)
    ap.add_argument("--trail-atr", type=float)
    ap.add_argument("--breakeven-after", type=int, default=1, help="0 = never")
    ap.add_argument("--addon", help="PRICE:SIZE added after T1 (in profit only)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        stages = [parse_stage(x) for x in a.stages] if a.stages else None
        size = a.size
        if size is None and a.zone and a.equity and a.risk_pct:
            size = size_from_risk(a.side, [(px, w) for px, w in pc.tranches(a.side, *a.zone)], a.stop, a.equity,
                                  a.risk_pct, a.fee)
        built = build_stages(a.side, tuple(a.zone) if a.zone else None, stages, size)
        res = plan(a.side, built, a.stop, a.targets, a.leverage, a.mmr, a.fee, a.weights, a.equity, a.atr,
                   a.trail_atr, a.breakeven_after or None, parse_stage(a.addon) if a.addon else None)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(res, indent=2, default=float) if a.json else render(a.asset, res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
