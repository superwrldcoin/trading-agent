"""Pre-trade rules gate: check a plan against the rules the user wrote in memory/user-preferences.md.

  python tools/rules_check.py --symbol BTC/USDT --side long --leverage 20 [--risk-pct 1.5] [--weighted-r 1.8]
      [--stop 84000 | --no-stop] [--notional 17000] [--liq-touch-5d 64] [--option-premium 2500] [--json]

Rules live in a ```json block after `<!-- rules -->` in user-preferences.md; null = not set. Context comes from
memory/positions.json (open positions, equity) and memory/trades.md (last loss, trades today). Each rule is
PASS / BREAKS / not set / unknown (an input is missing). A checklist the user configured, not advice.
Skill: skills/rules-check.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import levels, positions  # noqa: E402

PREFS_PATH = ROOT / "memory" / "user-preferences.md"
TRADES_PATH = ROOT / "memory" / "trades.md"
THEMES = {"BTC/USDT": "crypto", "BCH/USDT": "crypto", "XAU/USD": "precious metals", "SI": "precious metals",
          "GLD": "precious metals", "MSFT": "equities"}


def load_rules(path: Path | None = None) -> dict:
    text = (path or PREFS_PATH).read_text(encoding="utf-8") if (path or PREFS_PATH).exists() else ""
    m = re.search(r"<!--\s*rules\s*-->.*?```json\s*(\{.*?\})\s*```", text, re.S)
    if not m:
        return {}
    return json.loads(m.group(1))


def trade_history(path: Path | None = None) -> list[dict]:
    """Parse memory/trades.md entries: opened/closed timestamps and realized R."""
    p = path or TRADES_PATH
    if not p.exists():
        return []
    out = []
    for block in re.split(r"\n(?=## T-\d+)", p.read_text(encoding="utf-8")):
        head = re.match(r"## (T-\d+):.*?opened (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC(?:.*?closed (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC)?",
                        block)
        if not head:
            continue
        r = re.search(r"- Result:\s*([+-]?\d+(?:\.\d+)?)R", block)
        out.append({"id": head.group(1), "opened": pd.Timestamp(head.group(2), tz="UTC"),
                    "closed": pd.Timestamp(head.group(3), tz="UTC") if head.group(3) else None,
                    "R": float(r.group(1)) if r else None})
    return out


def check(plan: dict, rules: dict, pos_data: dict, trades: list[dict], now: pd.Timestamp) -> list[dict]:
    eq = pos_data.get("equity")
    theme = THEMES.get(plan["symbol"])
    rows = []

    def row(rule, limit, value, ok, unit=""):
        if limit is None:
            res = "not set"
        elif value is None:
            res = "unknown"
        else:
            res = "PASS" if ok(value, limit) else "BREAKS"
        fmt = lambda x: "n/a" if x is None else (f"{x:,.2f}{unit}" if isinstance(x, float) else f"{x}{unit}")
        rows.append({"rule": rule, "limit": fmt(limit) if limit is not None else "not set",
                     "value": fmt(value), "result": res})

    lev_limits = rules.get("max_leverage") or {}
    row(f"max leverage ({theme})", lev_limits.get(theme), plan.get("leverage"), lambda v, l: v <= l, "x")
    row("max risk per trade", rules.get("max_risk_pct_per_trade"), plan.get("risk_pct"), lambda v, l: v <= l, "%")

    open_risk = None
    if eq:
        cur = sum(abs(p["entry"] - p["stop"]) * p["size"] for p in pos_data["positions"]
                  if p.get("instrument") != "option" and p.get("stop") is not None)
        cur += sum(p["premium"] * p["qty"] * p["multiplier"] for p in pos_data["positions"]
                   if p.get("instrument") == "option" and p["side"] == "long")
        undefined = any((p.get("instrument") != "option" and p.get("stop") is None) or
                        (p.get("instrument") == "option" and p["side"] == "short") for p in pos_data["positions"])
        if plan.get("risk_pct") is not None and not undefined:
            open_risk = cur / eq * 100 + plan["risk_pct"]
    row("max open risk (incl. this plan)", rules.get("max_open_risk_pct"), open_risk, lambda v, l: v <= l, "%")

    theme_gross = None
    if eq and plan.get("notional") is not None:
        g = sum(abs(p["entry"] * p["size"]) for p in pos_data["positions"]
                if p.get("instrument") != "option" and THEMES.get(p["symbol"]) == theme)
        theme_gross = (g + abs(plan["notional"])) / eq * 100
    row(f"max {theme} gross exposure (incl. this plan)", rules.get("max_theme_gross_pct"), theme_gross,
        lambda v, l: v <= l, "%")
    row("max open positions (incl. this plan)", rules.get("max_positions"), len(pos_data["positions"]) + 1,
        lambda v, l: v <= l)
    row("min weighted R", rules.get("min_weighted_r"), plan.get("weighted_r"), lambda v, l: v >= l)
    row("stop required", rules.get("require_stop") or None, plan.get("has_stop"), lambda v, l: bool(v) or not l)
    row("max 5-day liquidation-touch odds", rules.get("max_liq_touch_5d_pct"), plan.get("liq_touch_5d"),
        lambda v, l: v <= l, "%")
    prem_pct = plan["option_premium"] / eq * 100 if eq and plan.get("option_premium") is not None else None
    row("max option premium per trade", rules.get("max_option_premium_pct"), prem_pct, lambda v, l: v <= l, "%")

    losses = [t for t in trades if t["R"] is not None and t["R"] < 0 and t["closed"] is not None]
    since = None
    if losses:
        last = max(losses, key=lambda t: t["closed"])
        since = (now - last["closed"]).total_seconds() / 3600
    cd = rules.get("loss_cooldown_hours")
    if cd is not None and not losses:
        rows.append({"rule": "cooldown after a loss", "limit": f"{cd}h", "value": "no losing trade logged",
                     "result": "PASS"})
    else:
        row("cooldown after a loss (hours since last loss)", cd, since, lambda v, l: v >= l, "h")
    today = sum(1 for t in trades if t["opened"].date() == now.date())
    row("max trades per day (incl. this one)", rules.get("max_trades_per_day"), today + 1, lambda v, l: v <= l)
    return rows


def render(plan: dict, rows: list[dict]) -> str:
    broken = [r for r in rows if r["result"] == "BREAKS"]
    unset = sum(r["result"] == "not set" for r in rows)
    head = (f"**Breaks {len(broken)} of your rules:** " + "; ".join(f"{r['rule']} ({r['value']} vs {r['limit']})"
                                                                     for r in broken)) if broken else \
        "**No rule broken** (of the rules you've set)."
    lines = [f"### Rules check: {plan['symbol']} {plan.get('side', '')} {plan.get('leverage', '')}x", "", head, "",
             "| Rule | Your limit | This plan | Result |", "|---|---|---|---|"]
    lines += [f"| {r['rule']} | {r['limit']} | {r['value']} | {r['result']} |" for r in rows]
    if unset:
        lines.append(f"\n{unset} rule(s) not set. Set them in memory/user-preferences.md (Trading rules block).")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Pre-trade rules gate.")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", choices=["long", "short"])
    ap.add_argument("--leverage", type=float)
    ap.add_argument("--risk-pct", type=float)
    ap.add_argument("--weighted-r", type=float)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--stop", type=float)
    g.add_argument("--no-stop", action="store_true")
    ap.add_argument("--notional", type=float)
    ap.add_argument("--liq-touch-5d", type=float, help="percent, from vol_check")
    ap.add_argument("--option-premium", type=float, help="total premium in $")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    sym = a.symbol.upper()
    if sym not in levels.WATCHLIST:
        print(f"ERROR: {sym} not on the watchlist")
        return 2
    plan = {"symbol": sym, "side": a.side, "leverage": a.leverage, "risk_pct": a.risk_pct, "weighted_r": a.weighted_r,
            "has_stop": True if a.stop is not None else (False if a.no_stop else None), "notional": a.notional,
            "liq_touch_5d": a.liq_touch_5d, "option_premium": a.option_premium}
    rows = check(plan, load_rules(), positions.load(), trade_history(), pd.Timestamp.now(tz="UTC"))
    print(json.dumps(rows, indent=2) if a.json else render(plan, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
