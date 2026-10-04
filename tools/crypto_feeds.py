"""Crypto positioning feeds (context only): funding rate, open interest, long/short account ratio, BTC dominance.

  python tools/crypto_feeds.py BTC/USDT       feeds listed for the symbol in memory/universe.yaml (extra_feeds)
  python tools/crypto_feeds.py BCH/USDT --json

Sources (public, keyless): OKX USDT-perpetual funding rate (+ 7-day history), open interest snapshot (history when
OKX returns non-zero values), long/short account ratio; CoinGecko global BTC dominance.
These describe positioning and crowding. They never change the conviction grade; mention extremes in Notes.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import universe  # noqa: E402

OKX = "https://www.okx.com/api/v5"
COINGECKO_GLOBAL = "https://api.coingecko.com/api/v3/global"
TIMEOUT = 15
FUNDING_HOT = 0.0005    # 0.05% per 8h: crowded longs
FUNDING_COLD = -0.0003  # -0.03% per 8h: crowded shorts
LS_HIGH, LS_LOW = 2.0, 0.7


def _get(url: str, params: dict | None = None) -> dict:
    r = requests.get(url, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    if isinstance(j, dict) and j.get("code") not in (None, "0"):
        raise RuntimeError(j.get("msg") or f"code {j.get('code')}")
    return j


def ccy_for(core_id: str) -> str:
    e = universe.core_by_id().get(core_id)
    if not e or e.get("asset_class") != "crypto":
        raise ValueError(f"{core_id} is not a crypto core instrument")
    return e["feed"]["instrument"].split("-")[0]


def funding(ccy: str) -> dict:
    inst = f"{ccy}-USDT-SWAP"
    cur = _get(f"{OKX}/public/funding-rate", {"instId": inst})["data"][0]
    hist = _get(f"{OKX}/public/funding-rate-history", {"instId": inst, "limit": 21})["data"]
    rates = [float(h["realizedRate"] or h["fundingRate"]) for h in hist]
    rate = float(cur["fundingRate"])
    avg7 = sum(rates) / len(rates) if rates else None
    reading = ("crowded longs (high positive funding)" if rate >= FUNDING_HOT else
               "crowded shorts (negative funding)" if rate <= FUNDING_COLD else "neutral")
    return {"inst": inst, "rate_8h": rate, "annualized_pct": rate * 3 * 365 * 100, "avg_7d_8h": avg7,
            "next_funding": pd.Timestamp(int(cur["fundingTime"]), unit="ms", tz="UTC").isoformat(), "reading": reading}


def open_interest(ccy: str) -> dict:
    inst = f"{ccy}-USDT-SWAP"
    snap = _get(f"{OKX}/public/open-interest", {"instType": "SWAP", "instId": inst})["data"][0]
    out = {"inst": inst, "oi_usd": float(snap["oiUsd"]), "oi_ccy": float(snap["oiCcy"]), "chg_7d_pct": None,
           "history_note": ""}
    try:
        rows = _get(f"{OKX}/rubik/stat/contracts/open-interest-volume", {"ccy": ccy, "period": "1D"})["data"]
        series = [(int(t), float(oi)) for t, oi, *_ in rows if float(oi) > 0]
        if len(series) >= 8 and series[0][0] > int(pd.Timestamp.now(tz="UTC").timestamp() * 1000) - 3 * 86400000:
            out["chg_7d_pct"] = (series[0][1] / series[7][1] - 1) * 100
        else:
            out["history_note"] = "OI history unavailable (OKX returned zeros for recent days)"
    except Exception as exc:
        out["history_note"] = f"OI history unavailable ({str(exc)[:60]})"
    return out


def long_short(ccy: str) -> dict:
    rows = _get(f"{OKX}/rubik/stat/contracts/long-short-account-ratio", {"ccy": ccy, "period": "1H"})["data"]
    now, day_ago = float(rows[0][1]), float(rows[min(24, len(rows) - 1)][1])
    reading = ("most accounts long (crowded)" if now >= LS_HIGH else "most accounts short (crowded)" if now <= LS_LOW
               else "balanced")
    return {"ratio": now, "ratio_24h_ago": day_ago, "reading": reading,
            "as_of": pd.Timestamp(int(rows[0][0]), unit="ms", tz="UTC").isoformat()}


def btc_dominance() -> dict:
    d = _get(COINGECKO_GLOBAL)["data"]
    return {"btc_dominance_pct": float(d["market_cap_percentage"]["btc"]),
            "mcap_change_24h_pct": float(d.get("market_cap_change_percentage_24h_usd") or 0)}


FEEDS = {"funding_rate": funding, "open_interest": open_interest, "long_short_ratio": long_short,
         "btc_dominance": lambda ccy: btc_dominance()}


def run(core_id: str) -> dict:
    ccy = ccy_for(core_id)
    wanted = universe.context(core_id).get("feeds") or universe.core_by_id()[core_id].get("extra_feeds", [])
    out = {"symbol": core_id, "ccy": ccy, "fetched_at": pd.Timestamp.now(tz="UTC").isoformat(), "feeds": {}}
    for name in wanted:
        try:
            out["feeds"][name] = FEEDS[name](ccy)
        except Exception as exc:
            out["feeds"][name] = {"error": str(exc)[:120]}
    return out


def render(r: dict) -> str:
    f = r["feeds"]
    lines = [f"### Crypto positioning: {r['symbol']} (fetched {r['fetched_at'][:16]}Z) [DATA]"]
    if "funding_rate" in f:
        x = f["funding_rate"]
        lines.append(f"- Funding ({x.get('inst', '')}): " + (x["error"] if "error" in x else
                     f"{x['rate_8h'] * 100:+.4f}% per 8h ({x['annualized_pct']:+.1f}%/yr), 7d avg "
                     f"{(x['avg_7d_8h'] or 0) * 100:+.4f}%: {x['reading']}"))
    if "open_interest" in f:
        x = f["open_interest"]
        lines.append(f"- Open interest: " + (x["error"] if "error" in x else
                     f"${x['oi_usd'] / 1e6:,.0f}M ({x['oi_ccy']:,.0f} {r['ccy']})"
                     + (f", 7d {x['chg_7d_pct']:+.1f}%" if x["chg_7d_pct"] is not None else f"; {x['history_note']}")))
    if "long_short_ratio" in f:
        x = f["long_short_ratio"]
        lines.append(f"- Long/short accounts: " + (x["error"] if "error" in x else
                     f"{x['ratio']:.2f} (24h ago {x['ratio_24h_ago']:.2f}): {x['reading']}"))
    if "btc_dominance" in f:
        x = f["btc_dominance"]
        lines.append(f"- BTC dominance: " + (x["error"] if "error" in x else
                     f"{x['btc_dominance_pct']:.1f}% (total crypto market cap 24h {x['mcap_change_24h_pct']:+.2f}%)"))
    lines.append("Positioning context only: it never changes the grade. Extremes go in Notes and the bear case.")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Crypto positioning feeds.")
    ap.add_argument("symbol")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        r = run(a.symbol.upper())
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(json.dumps(r, indent=2) if a.json else render(r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
