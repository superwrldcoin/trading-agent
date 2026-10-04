"""Open positions store: memory/positions.json (private, gitignored). Feeds portfolio.py, stress tests, rules_check.py.

  python tools/positions.py list
  python tools/positions.py set-equity 10000
  python tools/positions.py add --symbol BTC/USDT --side long --entry 85000 --size 0.1 --leverage 10 --stop 83000 \
      [--targets 87400 90000] [--margin isolated|cross] [--instrument perp|spot|future|cfd|etf|stock]
  python tools/positions.py add-option --symbol MSFT --kind call --side long --strike 520 --expiry 2026-11-20 \
      --qty 2 --premium 12.50 [--multiplier 100] [--iv 0.28]
  python tools/positions.py close P-001            (removes it; record the trade with tools/log_trade.py)

These are the user's records; the agent edits them only when the user asks.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import levels  # noqa: E402

POSITIONS_PATH = ROOT / "memory" / "positions.json"


def load(path: Path | None = None) -> dict:
    path = path or POSITIONS_PATH
    if not path.exists():
        return {"equity": None, "positions": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("equity", None)
    data.setdefault("positions", [])
    return data


def save(data: dict, path: Path | None = None) -> None:
    path = path or POSITIONS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def next_id(data: dict) -> str:
    nums = [int(p["id"].split("-")[1]) for p in data["positions"] if p.get("id", "").startswith("P-")]
    nums += data.get("_closed_ids", [])
    return f"P-{max(nums, default=0) + 1:03d}"


def validate(p: dict) -> None:
    if p["symbol"] not in levels.WATCHLIST:
        raise ValueError(f"{p['symbol']} is not on the watchlist ({', '.join(levels.WATCHLIST)})")
    if p["side"] not in ("long", "short"):
        raise ValueError("side must be long or short")
    if p["instrument"] == "option":
        if p["kind"] not in ("call", "put") or p["strike"] <= 0 or p["qty"] <= 0 or p["premium"] < 0:
            raise ValueError("option needs kind call/put, strike > 0, qty > 0, premium >= 0")
        pd.Timestamp(p["expiry"])  # raises on a bad date
        return
    if p["entry"] <= 0 or p["size"] <= 0 or p["leverage"] < 1:
        raise ValueError("entry > 0, size > 0, leverage >= 1 required")
    s = 1 if p["side"] == "long" else -1
    if p.get("stop") is not None and s * (p["entry"] - p["stop"]) <= 0:
        raise ValueError(f"stop {p['stop']} is on the wrong side of entry {p['entry']} for a {p['side']}")


def add(data: dict, pos: dict, now: pd.Timestamp | None = None) -> dict:
    pos = dict(pos)
    pos["id"] = next_id(data)
    pos["opened"] = (now or pd.Timestamp.now(tz="UTC")).strftime("%Y-%m-%d %H:%M UTC")
    validate(pos)
    data["positions"].append(pos)
    return pos


def close(data: dict, pid: str) -> dict:
    for i, p in enumerate(data["positions"]):
        if p["id"] == pid:
            data.setdefault("_closed_ids", []).append(int(pid.split("-")[1]))
            return data["positions"].pop(i)
    raise ValueError(f"no open position {pid}")


def describe(p: dict) -> str:
    if p["instrument"] == "option":
        return (f"{p['id']} {p['symbol']} {p['side']} {p['qty']:g}x {p['kind']} {p['strike']:g} exp {p['expiry']} "
                f"@ {p['premium']:g} (mult {p['multiplier']:g})")
    extra = f" stop {p['stop']:g}" if p.get("stop") is not None else " no stop"
    return (f"{p['id']} {p['symbol']} {p['side']} {p['size']:g} @ {p['entry']:g}, {p['leverage']:g}x "
            f"{p['margin']}{extra} ({p['instrument']})")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Manage open positions (memory/positions.json).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    se = sub.add_parser("set-equity")
    se.add_argument("equity", type=float)
    a1 = sub.add_parser("add")
    a1.add_argument("--symbol", required=True)
    a1.add_argument("--side", required=True, choices=["long", "short"])
    a1.add_argument("--entry", type=float, required=True)
    a1.add_argument("--size", type=float, required=True, help="units of the underlying (e.g. 0.1 BTC, 10 shares)")
    a1.add_argument("--leverage", type=float, default=1.0)
    a1.add_argument("--stop", type=float)
    a1.add_argument("--targets", type=float, nargs="*")
    a1.add_argument("--margin", choices=["isolated", "cross"], default="isolated")
    a1.add_argument("--mmr", type=float, default=0.005)
    a1.add_argument("--instrument", default="perp", choices=["perp", "spot", "future", "cfd", "etf", "stock"])
    a2 = sub.add_parser("add-option")
    a2.add_argument("--symbol", required=True)
    a2.add_argument("--kind", required=True, choices=["call", "put"])
    a2.add_argument("--side", required=True, choices=["long", "short"])
    a2.add_argument("--strike", type=float, required=True)
    a2.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    a2.add_argument("--qty", type=float, required=True, help="contracts")
    a2.add_argument("--premium", type=float, required=True, help="per unit of underlying, as quoted")
    a2.add_argument("--multiplier", type=float, default=100.0, help="100 for US equity options; 1 for OKX BTC (per BTC)")
    a2.add_argument("--iv", type=float, help="implied vol at entry, e.g. 0.28")
    cl = sub.add_parser("close")
    cl.add_argument("id")
    a = ap.parse_args(argv)

    data = load()
    try:
        if a.cmd == "list":
            print(f"Equity: {data['equity'] if data['equity'] is not None else 'not set (positions.py set-equity N)'}")
            for p in data["positions"]:
                print(describe(p))
            if not data["positions"]:
                print("No open positions.")
            return 0
        if a.cmd == "set-equity":
            data["equity"] = a.equity
            msg = f"Equity set to {a.equity:,.2f}"
        elif a.cmd == "add":
            pos = add(data, {"symbol": a.symbol.upper(), "instrument": a.instrument, "side": a.side, "entry": a.entry,
                             "size": a.size, "leverage": a.leverage, "stop": a.stop, "targets": a.targets or [],
                             "margin": a.margin, "mmr": a.mmr})
            msg = f"Added {describe(pos)}"
        elif a.cmd == "add-option":
            pos = add(data, {"symbol": a.symbol.upper(), "instrument": "option", "kind": a.kind, "side": a.side,
                             "strike": a.strike, "expiry": a.expiry, "qty": a.qty, "premium": a.premium,
                             "multiplier": a.multiplier, "iv": a.iv})
            msg = f"Added {describe(pos)}"
        else:
            pos = close(data, a.id)
            msg = f"Closed {describe(pos)}. Record it: python tools/log_trade.py ..."
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    save(data)
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
