"""Universe loader and verifier: memory/universe.yaml is the single source of truth for symbols and feeds.

  python tools/universe.py list
  python tools/universe.py verify [SYMBOL ...]     fetch each symbol, write verified / last_verified / verify_note back

Other tools import this module: the watchlist (levels.WATCHLIST), themes, drivers, pairs and ratios all come from
the YAML. This module imports no other project tool at load time (levels imports it).
"""
from __future__ import annotations

import argparse
import re
import sys
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
UNIVERSE_PATH = ROOT / "memory" / "universe.yaml"
THEME_BY_CLASS = {"crypto": "crypto", "metals": "precious metals", "equity": "equities"}
LEVEL_DRIVERS = {"^TNX", "^VIX", "DFII10"}  # rates / vol indexes: correlate by change in level, not % change


@lru_cache(maxsize=4)
def _load(path_str: str, mtime: float) -> dict:
    return yaml.safe_load(Path(path_str).read_text(encoding="utf-8"))


def load(path: Path | None = None) -> dict:
    p = path or UNIVERSE_PATH
    return _load(str(p), p.stat().st_mtime)


def core(path: Path | None = None) -> list[dict]:
    return load(path)["core"]


def entries(path: Path | None = None) -> list[tuple[str, dict]]:
    """All symbol entries with their role: ('core'|'driver'|'pair', entry)."""
    u = load(path)
    return ([("core", e) for e in u.get("core", [])] + [("driver", e) for e in u.get("drivers", [])]
            + [("pair", e) for e in u.get("pairs", [])])


def core_by_id(path: Path | None = None) -> dict[str, dict]:
    return {c["id"]: c for c in core(path)}


def core_symbol_to_id(path: Path | None = None) -> dict[str, str]:
    return {c["symbol"]: c["id"] for c in core(path)}


def themes(path: Path | None = None) -> dict[str, str]:
    return {c["id"]: THEME_BY_CLASS.get(c["asset_class"], c["asset_class"]) for c in core(path)}


def context(core_id: str, path: Path | None = None) -> dict:
    u = load(path)
    return (u.get("context") or {}).get(core_id, {"pairs": [], "drivers": [], "ratios": [], "feeds": []})


def ratios(path: Path | None = None) -> list[dict]:
    return load(path).get("ratios", [])


def entry(symbol: str, path: Path | None = None) -> tuple[str, dict] | None:
    for role, e in entries(path):
        if e["symbol"] == symbol or e.get("id") == symbol:
            return role, e
    return None


def daily_symbol(core_entry: dict) -> str:
    """yfinance daily ticker used for cross-asset correlations (the backup symbol for exchange-fed instruments)."""
    if core_entry.get("source") == "yfinance":
        return core_entry["symbol"]
    return (core_entry.get("backup") or {}).get("symbol", core_entry["symbol"])


# ---------- comment-preserving status updates ----------

def set_status(symbol: str, verified: bool, date: str, note: str | None, path: Path | None = None) -> bool:
    """Rewrite the `verified:` line of one entry (and its last_verified / verify_note lines) in place."""
    p = path or UNIVERSE_PATH
    lines = p.read_text(encoding="utf-8").splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(rf'\s*- symbol: "{re.escape(symbol)}"\s*$', l)), None)
    if start is None:
        return False
    indent = re.match(r"(\s*)-", lines[start]).group(1) + "  "
    end = start + 1
    while end < len(lines) and not re.match(r"\s*- symbol:", lines[end]) and not re.match(r"\S", lines[end] or "x"):
        end += 1
    block = [l for l in lines[start:end] if not re.match(rf"{indent}(last_verified|verify_note):", l)]
    vi = next((i for i, l in enumerate(block) if l.startswith(f"{indent}verified:")), None)
    new = [f"{indent}verified: {'true' if verified else 'false'}", f"{indent}last_verified: {date}"]
    if note:
        new.append(f'{indent}verify_note: "{note.replace(chr(34), chr(39))}"')
    if vi is None:
        while block and not block[-1].strip():
            block.pop()
        block += new + [""]
    else:
        block[vi:vi + 1] = new
    lines[start:end] = block
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _load.cache_clear()
    return True


# ---------- verification ----------

def verify_symbol(role: str, e: dict, now) -> tuple[bool, str]:
    """Fetch the symbol from its configured source; return (ok, note)."""
    import numpy as np
    import pandas as pd
    sys.path.insert(0, str(ROOT))
    from tools import data

    notes = []
    src = e.get("source")
    try:
        if src == "fred":
            s = data.fetch_fred_series(e.get("fred_series", e["symbol"]))
            age = (now.normalize().tz_localize(None) - s.index[-1]).days
            ok = age <= 7
            notes.append(f"FRED {len(s)} obs, last {s.index[-1]:%Y-%m-%d} = {s.iloc[-1]:.2f}" + ("" if ok else f" (stale {age}d)"))
            return ok, "; ".join(notes)
        if src == "exchange_public":
            feed = e["feed"]
            df = data.fetch_okx_candles(feed["instrument"], start=now - pd.Timedelta(days=3), bar="1H", max_pages=2)
            if df.empty:
                return False, f"OKX {feed['instrument']} returned no candles"
            notes.append(f"OKX {feed['instrument']} last {df['close'].iloc[-1]:,.2f} at {df.index[-1]:%m-%d %H:%M}Z")
            if e.get("spot_check"):
                sc = e["spot_check"]
                spot = data.fetch_swissquote_mid(sc["base"], sc["quote"])
                prem = (df["close"].iloc[-1] / spot - 1) * 100
                notes.append(f"vs Swissquote spot {spot:,.2f} ({prem:+.2f}%)")
            if e.get("backup"):
                b = data.fetch_yf_daily_close(e["backup"]["symbol"], period="10d")
                notes.append(f"backup {e['backup']['symbol']} OK ({b.iloc[-1]:,.2f})")
            return True, "; ".join(notes)
        # yfinance
        daily = data.fetch_yf_daily_close(e["symbol"], period="400d")
        last_date = daily.index[-1]
        age = (now.tz_localize(None).normalize() - last_date).days
        notes.append(f"yfinance {len(daily)} daily bars, last {last_date:%Y-%m-%d} = {daily.iloc[-1]:,.2f}")
        ok = age <= 5
        if not ok:
            notes.append(f"stale: last bar {age} days old")
        try:
            h = data.fetch_yf_hourly(e["symbol"], period="5d", interval="1h")
            notes.append(f"1h OK ({len(h)} bars)")
        except Exception as exc:
            notes.append(f"1h unavailable ({str(exc)[:60]})")
        if e["symbol"] == "^TNX":
            v = float(daily.iloc[-1])
            notes.append("units: percent" if v < 20 else "units: yield x10 (divide by 10)")
        if e["symbol"].endswith("=F"):
            r = daily.pct_change().abs()
            big = int((r > 0.04).sum())
            notes.append(f"continuous contract: {big} daily moves > 4% in the window (check for roll gaps before using levels across them)")
        return ok, "; ".join(notes)
    except Exception as exc:
        return False, f"FAILED: {str(exc)[:160]}"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Universe: list or verify symbols in memory/universe.yaml.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    v = sub.add_parser("verify")
    v.add_argument("symbols", nargs="*")
    v.add_argument("--dry-run", action="store_true", help="don't write results back")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        for role, e in entries():
            status = "verified " + str(e.get("last_verified", "")) if e.get("verified") else "UNVERIFIED"
            print(f"{role:6s} {e['symbol']:10s} {e.get('id', ''):9s} {e.get('source', ''):15s} {status}"
                  + (f" | {e['verify_note']}" if e.get("verify_note") else ""))
        return 0
    import pandas as pd
    now = pd.Timestamp.now(tz="UTC")
    date = f"{now:%Y-%m-%d}"
    todo = [(r, e) for r, e in entries() if not a.symbols or e["symbol"] in a.symbols or e.get("id") in a.symbols]
    failed = 0
    for role, e in todo:
        ok, note = verify_symbol(role, e, now)
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {role:6s} {e['symbol']:10s} {note}")
        if not a.dry_run:
            set_status(e["symbol"], ok, date, note)
    print(f"\n{len(todo) - failed}/{len(todo)} verified" + ("" if a.dry_run else " (written to memory/universe.yaml)"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
