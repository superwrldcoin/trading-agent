"""Append a structured analysis-log entry to memory/sessions.md.

One entry per instrument per session. Entries share a session ID so a full watchlist run groups together.
memory/sessions.md is a log, not memory: it isn't auto-loaded, it may contain price levels, and it is
gitignored (private). Lessons still go to journal.md only through MEMORY_UPDATE blocks.

Usage:
  python tools/log_entry.py --request "Full watchlist 4H analysis" --symbol BCH/USDT --side long --grade B \
      --zone 296.30 298.96 --stop 293.64 --targets 308.60 322.60 366.10 --prob 35 \
      --tools levels.py indicators.py position_calc.py --report tools/output/2026-10-04_1856_session.md
  python tools/log_entry.py --request "..." --symbol MSFT --side none --grade none --note "R:R 0.27 fails"
Prints the entry ID. Pass --session <ID> to add more instruments to the same session.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SESSIONS_PATH = ROOT / "memory" / "sessions.md"
HEADER = """# Sessions

Analysis log written by `tools/log_entry.py`. Not memory: not auto-loaded, may contain price levels,
gitignored (private). `Outcome` is updated when a trade closes (see `memory/trades.md`), which lets
post-mortems check how well the `P(T1 before stop)` estimates are calibrated.

"""
GRADES = ("A", "B", "C", "none")
SIDES = ("long", "short", "none")


def session_id(now: pd.Timestamp) -> str:
    return f"S-{now:%Y%m%d-%H%M}"


def validate(e: dict) -> None:
    if e["grade"] not in GRADES:
        raise ValueError(f"grade must be one of {GRADES}")
    if e["side"] not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    if not e["request"].strip():
        raise ValueError("request is required")
    if e["side"] == "none":
        if e["grade"] != "none":
            raise ValueError("a 'none' side (no valid setup) must have grade 'none'")
        if not e.get("note"):
            raise ValueError("no-setup entries need --note with the reason")
        if e.get("base_grade") not in (None, "A", "B", "C"):
            raise ValueError("base grade must be A, B or C")
        return
    if e["grade"] == "none":
        raise ValueError("a setup needs a grade (A/B/C)")
    if not e.get("zone") or e.get("stop") is None or not e.get("targets"):
        raise ValueError("a setup needs zone, stop and targets")
    if e.get("prob") is None or not 0 <= e["prob"] <= 100:
        raise ValueError("a setup needs --prob between 0 and 100 (P(T1 before stop), judgment)")
    low, high = sorted(e["zone"])
    s = 1 if e["side"] == "long" else -1
    if s * (e["stop"] - (low if s == 1 else high)) >= 0:
        raise ValueError(f"stop {e['stop']} must be beyond the zone for a {e['side']}")
    if any(s * (t - (high if s == 1 else low)) <= 0 for t in e["targets"]):
        raise ValueError(f"targets must be beyond the zone in the {e['side']} direction")


def format_entry(e: dict) -> str:
    f = lambda x: f"{x:,.2f}"
    head = f"### {e['id']} | {e['symbol']} " + (f"{e['side']} | grade {e['grade']}" if e["side"] != "none"
                                                  else "| no valid setup")
    lines = [head, f"- Logged: {e['logged_at']}", f"- Request: \"{e['request']}\""]
    if e["side"] == "none" and e.get("base_grade"):
        lines.append(f"- Base grade of the favored side (no setup): {e['base_grade']}"
                     + (f" {e['base_side']}" if e.get("base_side") else ""))
    if e["side"] != "none":
        low, high = sorted(e["zone"])
        lines += [f"- Zone: {f(low)}-{f(high)} | Stop: {f(e['stop'])} | Targets: "
                  + ", ".join(f(t) for t in e["targets"]),
                  f"- P(T1 before stop): {e['prob']:.0f}% [JUDGMENT]"]
    if e.get("tools"):
        lines.append(f"- Tools: {', '.join(e['tools'])}")
    if e.get("report"):
        lines.append(f"- Report: {e['report']}")
    if e.get("note"):
        lines.append(f"- Note: {e['note']}")
    lines.append(f"- Outcome: {'open' if e['side'] != 'none' else 'n/a'}")
    return "\n".join(lines) + "\n\n"


def append(e: dict, path: Path | None = None, now: pd.Timestamp | None = None) -> dict:
    path = path or SESSIONS_PATH
    now = now or pd.Timestamp.now(tz="UTC")
    e = dict(e)
    e.setdefault("session", None)
    e["session"] = e["session"] or session_id(now)
    e["id"] = f"{e['session']}/{e['symbol'].replace('/', '')}"
    e["logged_at"] = f"{now:%Y-%m-%d %H:%M} UTC"
    validate(e)
    if path.exists() and f"### {e['id']} " in path.read_text(encoding="utf-8"):
        raise ValueError(f"{e['id']} is already logged")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(HEADER, encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(format_entry(e))
    return e


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Append an analysis-log entry to memory/sessions.md.")
    ap.add_argument("--request", required=True, help="the user's request, verbatim or summarized")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", required=True, choices=SIDES)
    ap.add_argument("--grade", required=True, choices=GRADES)
    ap.add_argument("--zone", type=float, nargs=2, metavar=("LOW", "HIGH"))
    ap.add_argument("--stop", type=float)
    ap.add_argument("--targets", type=float, nargs="+")
    ap.add_argument("--prob", type=float, help="P(T1 before stop) in %%, judgment")
    ap.add_argument("--tools", nargs="+")
    ap.add_argument("--report")
    ap.add_argument("--note")
    ap.add_argument("--base-grade", choices=["A", "B", "C"], help="no-setup entries: grade of the favored side")
    ap.add_argument("--base-side", choices=["long", "short"], help="no-setup entries: the favored side")
    ap.add_argument("--session", help="reuse a session ID to group instruments")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    entry = {k: getattr(a, k) for k in ("request", "symbol", "side", "grade", "zone", "stop", "targets", "prob",
                                        "tools", "report", "note", "session", "base_grade", "base_side")}
    try:
        e = append(entry)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(e, indent=2) if a.json else f"Logged {e['id']} to {SESSIONS_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
