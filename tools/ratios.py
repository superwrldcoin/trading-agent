"""Computed ratios from memory/universe.yaml (calculated in code, never by the model).

  python tools/ratios.py                    all ratios
  python tools/ratios.py --for SI           only the ratios in SI's context map
  python tools/ratios.py gold_silver_ratio msft_qqq [--json]

Each ratio = daily close of A / daily close of B (yfinance), compared with its own 50-day SMA (trend_ref) and its
20-day change. Reading text comes from the YAML.
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

from tools import data, universe  # noqa: E402


def parse_formula(formula: str) -> tuple[str, str]:
    m = re.match(r"^\s*(\S+)\s*/\s*(\S+)\s*$", formula)
    if not m:
        raise ValueError(f"ratio formula must be 'A / B', got {formula!r}")
    return m.group(1), m.group(2)


def sma_window(trend_ref: str) -> int:
    m = re.match(r"sma_(\d+)d$", trend_ref or "sma_50d")
    if not m:
        raise ValueError(f"unsupported trend_ref {trend_ref!r} (use sma_<N>d)")
    return int(m.group(1))


def compute(a: pd.Series, b: pd.Series, window: int) -> dict:
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(j) < window + 10:
        return {"value": None, "note": f"only {len(j)} overlapping days (< {window + 10})"}
    r = j.iloc[:, 0] / j.iloc[:, 1]
    sma = r.rolling(window).mean()
    val, s_now, s_prev = float(r.iloc[-1]), float(sma.iloc[-1]), float(sma.iloc[-11])
    chg20 = (val / float(r.iloc[-21]) - 1) * 100
    above = val > s_now
    slope_up = s_now > s_prev
    label = ("above" if above else "below") + f" {window}d SMA, SMA {'rising' if slope_up else 'falling'}"
    trend = "rising" if above and slope_up else "falling" if not above and not slope_up else "turning"
    return {"value": val, "sma": s_now, "pct_vs_sma": (val / s_now - 1) * 100, "chg_20d_pct": chg20,
            "label": label, "trend": trend, "as_of": f"{r.index[-1]:%Y-%m-%d}", "days": len(j)}


def run(names: list[str] | None = None, for_id: str | None = None, fetch=data.fetch_yf_daily_close) -> list[dict]:
    defs = universe.ratios()
    if for_id:
        wanted = set(universe.context(for_id).get("ratios", []))
        defs = [d for d in defs if d["name"] in wanted]
    if names:
        defs = [d for d in defs if d["name"] in names]
    cache: dict[str, pd.Series] = {}
    out = []
    for d in defs:
        a_sym, b_sym = parse_formula(d["formula"])
        try:
            for s in (a_sym, b_sym):
                if s not in cache:
                    cache[s] = fetch(s, period="400d")
            res = compute(cache[a_sym], cache[b_sym], sma_window(d.get("trend_ref", "sma_50d")))
        except Exception as exc:
            res = {"value": None, "note": f"fetch failed: {str(exc)[:80]}"}
        out.append({"name": d["name"], "formula": d["formula"], "reading": d.get("reading", ""), **res})
    return out


def render(rows: list[dict]) -> str:
    f = lambda x, nd=4: "n/a" if x is None else f"{x:,.{nd}f}"
    lines = ["| Ratio | Formula | Value | vs SMA | 20d change | Trend | Reading |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        if r.get("value") is None:
            lines.append(f"| {r['name']} | {r['formula']} | n/a | | | | {r.get('note', '')} |")
            continue
        lines.append(f"| {r['name']} | {r['formula']} | {f(r['value'])} | {r['pct_vs_sma']:+.2f}% ({r['label']}) | "
                     f"{r['chg_20d_pct']:+.2f}% | {r['trend']} | {r['reading']} |")
    as_of = next((r["as_of"] for r in rows if r.get("as_of")), None)
    lines.append(f"\nDaily closes from yfinance{', as of ' + as_of if as_of else ''}. Computed in code [CALC].")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Computed ratios from memory/universe.yaml.")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--for", dest="for_id")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rows = run(a.names or None, a.for_id)
    print(json.dumps(rows, indent=2) if a.json else render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
