"""Options: chains with greeks and expected move, and multi-leg position analysis.

  python tools/options.py chain MSFT [--expiry 2026-11-20 | --dte 45] [--strikes 8]
  python tools/options.py analyze --symbol MSFT --leg "long call 520 2026-11-20 @12.50 x2" \
      [--leg "short call 560 2026-11-20 @4.10 x2"] [--spot 517.86]

Sources (public, keyless): yfinance option chains for MSFT and GLD (and SLV as the silver proxy, GLD as the spot-gold
proxy); Deribit for BTC options (prices in BTC, converted to USD at the underlying price; 1 contract = 1 BTC).
BCH has no listed options. Greeks are Black-Scholes from each option's implied volatility (European, r = 4%
[ASSUMPTION], no dividends); US equity/ETF options are American, so deep ITM puts are slightly undervalued.
Skill: skills/options-greeks.md.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import options_math as om  # noqa: E402

RATE = 0.04
UNDERLYING = {  # watchlist symbol -> (provider, ticker, multiplier, note)
    "MSFT": ("yfinance", "MSFT", 100, ""),
    "GLD": ("yfinance", "GLD", 100, ""),
    "SLV": ("yfinance", "SLV", 100, ""),
    "XAU/USD": ("yfinance", "GLD", 100, "spot gold has no listed options here: GLD options used as the proxy"),
    "SI": ("yfinance", "SLV", 100, "COMEX silver options aren't available keyless: SLV (iShares Silver) options used as the proxy"),
    "BTC/USDT": ("deribit", "BTC", 1, "Deribit BTC options: premiums quoted in BTC, shown in USD; 1 contract = 1 BTC"),
}
DERIBIT_URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option"
TIMEOUT = 15


class OptionsError(RuntimeError):
    pass


# ---------- data ----------

def fetch_yf_chain(ticker: str, expiry: str | None, dte: int | None, now: pd.Timestamp) -> tuple[pd.DataFrame, float, str]:
    import yfinance as yf
    tk = yf.Ticker(ticker)
    exps = list(tk.options)
    if not exps:
        raise OptionsError(f"yfinance has no option expiries for {ticker}")
    exp = pick_expiry(exps, expiry, dte, now)
    ch = tk.option_chain(exp)
    spot = float(tk.history(period="5d", interval="1d")["Close"].iloc[-1])
    rows = []
    for kind, df in (("call", ch.calls), ("put", ch.puts)):
        for _, r in df.iterrows():
            rows.append({"kind": kind, "strike": float(r["strike"]), "bid": float(r["bid"] or 0), "ask": float(r["ask"] or 0),
                         "last": float(r["lastPrice"] or 0), "iv": float(r["impliedVolatility"] or 0),
                         "oi": int(r["openInterest"]) if not pd.isna(r["openInterest"]) else 0})
    return pd.DataFrame(rows), spot, exp


def parse_deribit_name(name: str) -> tuple[str, float, str] | None:
    m = re.match(r"BTC-(\d{1,2}[A-Z]{3}\d{2})-(\d+)-([CP])$", name)
    if not m:
        return None
    exp = pd.to_datetime(m.group(1), format="%d%b%y").strftime("%Y-%m-%d")
    return exp, float(m.group(2)), "call" if m.group(3) == "C" else "put"


def fetch_deribit_chain(expiry: str | None, dte: int | None, now: pd.Timestamp) -> tuple[pd.DataFrame, float, str]:
    resp = requests.get(DERIBIT_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    rows = []
    for r in resp.json()["result"]:
        p = parse_deribit_name(r["instrument_name"])
        if not p:
            continue
        u = float(r.get("underlying_price") or 0)
        usd = lambda x: float(x) * u if x is not None else 0.0
        rows.append({"expiry": p[0], "kind": p[2], "strike": p[1], "bid": usd(r.get("bid_price")), "ask": usd(r.get("ask_price")),
                     "last": usd(r.get("mark_price")), "iv": float(r.get("mark_iv") or 0) / 100, "oi": float(r.get("open_interest") or 0),
                     "underlying": u})
    if not rows:
        raise OptionsError("Deribit returned no BTC options")
    df = pd.DataFrame(rows)
    exp = pick_expiry(sorted(df["expiry"].unique()), expiry, dte, now)
    sub = df[df["expiry"] == exp].drop(columns="expiry")
    return sub.reset_index(drop=True), float(sub["underlying"].median()), exp


def pick_expiry(exps: list[str], expiry: str | None, dte: int | None, now: pd.Timestamp) -> str:
    future = [e for e in exps if pd.Timestamp(e).tz_localize("UTC") + pd.Timedelta(hours=20) > now]
    if not future:
        raise OptionsError("no unexpired expiries")
    if expiry:
        if expiry not in future:
            raise OptionsError(f"expiry {expiry} not listed. Available: {', '.join(future[:12])}")
        return expiry
    target = now + pd.Timedelta(days=dte if dte is not None else 30)
    return min(future, key=lambda e: abs(pd.Timestamp(e).tz_localize("UTC") - target))


def years_to(expiry: str, now: pd.Timestamp) -> float:
    exp = pd.Timestamp(expiry).tz_localize("UTC") + pd.Timedelta(hours=20)
    return max((exp - now).total_seconds() / (365 * 86400), 0.0)


def load_chain(symbol: str, expiry: str | None, dte: int | None, now: pd.Timestamp) -> dict:
    if symbol not in UNDERLYING:
        raise OptionsError(f"no options source for {symbol} (BCH has no listed options). Supported: {', '.join(UNDERLYING)}")
    prov, ticker, mult, note = UNDERLYING[symbol]
    try:
        df, spot, exp = (fetch_deribit_chain(expiry, dte, now) if prov == "deribit"
                         else fetch_yf_chain(ticker, expiry, dte, now))
    except OptionsError:
        raise
    except Exception as exc:
        raise OptionsError(f"{prov} options fetch failed for {ticker}: {exc}") from exc
    return {"symbol": symbol, "ticker": ticker, "provider": prov, "multiplier": mult, "note": note, "chain": df,
            "spot": spot, "expiry": exp, "fetched_at": now.isoformat()}


# ---------- analysis ----------

def enrich(chain: pd.DataFrame, spot: float, T: float, r: float = RATE) -> pd.DataFrame:
    df = chain.copy()
    df["mid"] = np.where((df["bid"] > 0) & (df["ask"] > 0), (df["bid"] + df["ask"]) / 2, df["last"])
    g = [om.greeks(k, spot, s, T, r, iv) if iv > 0 else dict.fromkeys(("delta", "gamma", "theta", "vega", "rho"), np.nan)
         for k, s, iv in zip(df["kind"], df["strike"], df["iv"])]
    for key in ("delta", "gamma", "theta", "vega"):
        df[key] = [x[key] for x in g]
    return df


def atm_iv(df: pd.DataFrame, spot: float) -> float | None:
    near = df[(df["iv"] > 0.01)].copy()
    if near.empty:
        return None
    near["dist"] = (near["strike"] - spot).abs()
    k = near.sort_values("dist")["strike"].iloc[0]
    ivs = near[near["strike"] == k]["iv"]
    return float(ivs.mean())


def skew_25d(df: pd.DataFrame) -> float | None:
    """25-delta put IV minus 25-delta call IV (positive = puts richer)."""
    puts = df[(df["kind"] == "put") & df["delta"].notna() & (df["iv"] > 0.01)]
    calls = df[(df["kind"] == "call") & df["delta"].notna() & (df["iv"] > 0.01)]
    if puts.empty or calls.empty:
        return None
    p = puts.iloc[(puts["delta"] + 0.25).abs().argmin()]
    c = calls.iloc[(calls["delta"] - 0.25).abs().argmin()]
    return float(p["iv"] - c["iv"])


LEG_RE = re.compile(r"^\s*(long|short|buy|sell)\s+(call|put)\s+([\d.,]+)\s+(\d{4}-\d{2}-\d{2})\s*@\s*([\d.,]+)"
                    r"(?:\s*x\s*([\d.]+))?(?:\s+iv\s*=\s*([\d.]+))?\s*$", re.I)


def parse_leg(text: str) -> dict:
    m = LEG_RE.match(text)
    if not m:
        raise ValueError(f"can't read leg {text!r}. Format: 'long call 520 2026-11-20 @12.50 x2 [iv=0.28]'")
    side = "long" if m.group(1).lower() in ("long", "buy") else "short"
    return {"side": side, "kind": m.group(2).lower(), "strike": float(m.group(3).replace(",", "")),
            "expiry": m.group(4), "premium": float(m.group(5).replace(",", "")), "qty": float(m.group(6) or 1),
            "iv": float(m.group(7)) if m.group(7) else None}


def analyze_legs(legs: list[dict], spot: float, now: pd.Timestamp, multiplier: float, r: float = RATE,
                 shocks=(-10, -5, -2, 0, 2, 5, 10)) -> dict:
    out_legs, tot = [], dict.fromkeys(("delta", "gamma", "theta", "vega"), 0.0)
    debit = 0.0
    for lg in legs:
        s = 1 if lg["side"] == "long" else -1
        T = years_to(lg["expiry"], now)
        iv = lg["iv"] or om.implied_vol(lg["kind"], lg["premium"], spot, lg["strike"], T, r)
        if iv is None:
            raise ValueError(f"premium {lg['premium']} for the {lg['strike']:g} {lg['kind']} is outside the model range "
                             "at today's spot: pass iv=")
        units = lg["qty"] * multiplier
        g = om.greeks(lg["kind"], spot, lg["strike"], T, r, iv)
        for k in tot:
            tot[k] += s * g[k] * units
        d2 = (math.log(spot / lg["strike"]) + (r - 0.5 * iv * iv) * T) / (iv * math.sqrt(T)) if T > 0 else 0
        p_itm = 0.5 * (1 + math.erf((d2 if lg["kind"] == "call" else -d2) / math.sqrt(2))) if T > 0 else None
        debit += s * lg["premium"] * units
        out_legs.append({**lg, "T": T, "iv_used": iv, "iv_source": "given" if lg["iv"] else "implied from premium",
                         "value_now": om.price(lg["kind"], spot, lg["strike"], T, r, iv), "p_itm": p_itm,
                         "greeks": {k: s * v * units for k, v in g.items()}})
    exp_T = min(l["T"] for l in out_legs)

    def pnl(S_new: float, t_fwd_years: float) -> float:
        total = 0.0
        for lg in out_legs:
            s = 1 if lg["side"] == "long" else -1
            val = om.price(lg["kind"], S_new, lg["strike"], max(lg["T"] - t_fwd_years, 0.0), r, lg["iv_used"])
            total += s * (val - lg["premium"]) * lg["qty"] * multiplier
        return total

    grid = np.linspace(0.01 * spot, 3 * spot, 3000)
    at_exp = np.array([pnl(S, exp_T) for S in grid])
    sign_change = np.where(np.diff(np.sign(at_exp)) != 0)[0]
    breakevens = [float(grid[i] - at_exp[i] * (grid[i + 1] - grid[i]) / (at_exp[i + 1] - at_exp[i])) for i in sign_change]
    slope_hi = at_exp[-1] - at_exp[-2]
    max_profit = None if slope_hi > 1e-9 else float(at_exp.max())
    max_loss = None if slope_hi < -1e-9 else float(at_exp.min())
    em = om.expected_move(spot, out_legs[0]["iv_used"], exp_T)
    sigma = out_legs[0]["iv_used"] * math.sqrt(exp_T) if exp_T > 0 else 0
    lognorm = lambda S: 0.5 * (1 + math.erf((math.log(S / spot) - (r - 0.5 * out_legs[0]["iv_used"] ** 2) * exp_T) / (sigma * math.sqrt(2)))) if sigma else None
    prof_mask = at_exp > 0
    p_profit = None
    if sigma:
        cdf = np.array([lognorm(S) for S in grid])
        p_profit = float(np.sum(np.diff(cdf) * prof_mask[1:]))
    days = sorted({0, min(7, int(exp_T * 365)), int(exp_T * 365 / 2), int(exp_T * 365)})
    table = [{"shock_pct": sh, **{f"d{d}": pnl(spot * (1 + sh / 100), d / 365) for d in days}} for sh in shocks]
    return {"spot": spot, "multiplier": multiplier, "legs": out_legs, "net_greeks": tot, "net_debit": debit,
            "breakevens_at_expiry": breakevens, "max_profit": max_profit, "max_loss": max_loss,
            "expected_move_1sd": em, "p_profit_model": p_profit, "days": days, "pnl_grid": table, "rate": r}


# ---------- rendering ----------

def _f(x, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{nd}f}"


def render_chain(c: dict, df: pd.DataFrame, n: int) -> str:
    T = years_to(c["expiry"], pd.Timestamp(c["fetched_at"]))
    iv = atm_iv(df, c["spot"])
    em = om.expected_move(c["spot"], iv, T) if iv else None
    sk = skew_25d(df)
    lines = [f"### Options chain: {c['symbol']} ({c['ticker']}, {c['provider']}), expiry {c['expiry']} ({T * 365:.0f} DTE)",
             f"Spot {_f(c['spot'])} | ATM IV {_f(iv * 100 if iv else None, 1)}% | expected move by expiry "
             f"+/-{_f(em)} ({_f(em / c['spot'] * 100 if em else None, 1)}%, 1 sd) | 25-delta skew (put - call IV) "
             f"{_f(sk * 100 if sk is not None else None, 1)} vol pts [CALC]"]
    if c["note"]:
        lines.append(f"Note: {c['note']}")
    strikes = sorted(df["strike"].unique(), key=lambda k: abs(k - c["spot"]))[:n]
    lines += ["", "| Strike | Call bid/ask | Call IV | Call delta | Put bid/ask | Put IV | Put delta | Gamma | Theta/day (C/P) | Vega (C) | OI (C/P) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k in sorted(strikes):
        cr = df[(df["strike"] == k) & (df["kind"] == "call")]
        pr = df[(df["strike"] == k) & (df["kind"] == "put")]
        cg = cr.iloc[0] if len(cr) else None
        pg = pr.iloc[0] if len(pr) else None
        val = lambda r, key, nd=2: _f(float(r[key]), nd) if r is not None else "n/a"
        lines.append(f"| {_f(k)} | {val(cg, 'bid')}/{val(cg, 'ask')} | {_f(cg['iv'] * 100, 1) if cg is not None else 'n/a'}% | "
                     f"{val(cg, 'delta', 3)} | {val(pg, 'bid')}/{val(pg, 'ask')} | {_f(pg['iv'] * 100, 1) if pg is not None else 'n/a'}% | "
                     f"{val(pg, 'delta', 3)} | {val(cg, 'gamma', 4)} | {val(cg, 'theta')}/{val(pg, 'theta')} | {val(cg, 'vega')} | "
                     f"{val(cg, 'oi', 0)}/{val(pg, 'oi', 0)} |")
    lines.append(f"\nGreeks per 1 unit of underlying (x{c['multiplier']} per contract): Black-Scholes from each strike's IV, "
                 f"r = {RATE:.0%} [ASSUMPTION]. Data fetched {c['fetched_at'][:16]}Z [DATA].")
    return "\n".join(lines) + "\n"


def render_analysis(symbol: str, a: dict) -> str:
    g = a["net_greeks"]
    lines = [f"### Options position: {symbol} (spot {_f(a['spot'])}, x{a['multiplier']:g} per contract)", "",
             "| Leg | Premium | IV used | Value now | P(ITM at expiry) | Delta | Theta/day | Vega |", "|---|---|---|---|---|---|---|---|"]
    for lg in a["legs"]:
        lines.append(f"| {lg['side']} {lg['qty']:g} x {lg['kind']} {lg['strike']:g} {lg['expiry']} | {_f(lg['premium'])} | "
                     f"{lg['iv_used'] * 100:.1f}% ({lg['iv_source']}) | {_f(lg['value_now'])} | "
                     f"{_f(lg['p_itm'] * 100 if lg['p_itm'] is not None else None, 0)}% | {_f(lg['greeks']['delta'], 1)} | "
                     f"{_f(lg['greeks']['theta'])} | {_f(lg['greeks']['vega'])} |")
    be = ", ".join(_f(b) for b in a["breakevens_at_expiry"]) or "none"
    lines += ["", f"Net: {'debit' if a['net_debit'] > 0 else 'credit'} {_f(abs(a['net_debit']))} | delta {_f(g['delta'], 1)} "
                  f"(= {_f(g['delta'] * a['spot'])} of underlying) | gamma {_f(g['gamma'], 3)} | theta {_f(g['theta'])}/day | "
                  f"vega {_f(g['vega'])} per vol pt [CALC]",
              f"At expiry: breakeven {be}; max profit {_f(a['max_profit']) if a['max_profit'] is not None else 'unlimited'}; "
              f"max loss {_f(a['max_loss']) if a['max_loss'] is not None else 'UNLIMITED'} [CALC]",
              f"Expected move to first expiry: +/-{_f(a['expected_move_1sd'])} (1 sd from IV). Model probability of profit at expiry: "
              f"{_f(a['p_profit_model'] * 100 if a['p_profit_model'] is not None else None, 0)}% (risk-neutral lognormal, not a forecast) [CALC]",
              "", "P&L by spot move and days forward (same IV):", "",
              "| Spot move | " + " | ".join(f"day {d}" for d in a["days"]) + " |", "|---|" + "---|" * len(a["days"])]
    for row in a["pnl_grid"]:
        lines.append(f"| {row['shock_pct']:+d}% | " + " | ".join(_f(row[f'd{d}']) for d in a["days"]) + " |")
    if a["max_loss"] is None:
        lines.append("\n- **WARN**: unlimited loss (naked short option). It needs a stop plan and margin, and counts as undefined risk in portfolio-exposure.")
    if g["theta"] < 0 and abs(g["theta"]) * 7 > 0.1 * abs(a["net_debit"]):
        lines.append(f"- **WARN**: time decay costs {_f(-g['theta'] * 7)} per week, over 10% of the premium paid.")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Options chains, greeks and position analysis.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chain")
    c.add_argument("symbol")
    c.add_argument("--expiry")
    c.add_argument("--dte", type=int)
    c.add_argument("--strikes", type=int, default=8)
    an = sub.add_parser("analyze")
    an.add_argument("--symbol", required=True)
    an.add_argument("--leg", action="append", required=True)
    an.add_argument("--spot", type=float, help="default: fetched from the chain source")
    an.add_argument("--multiplier", type=float)
    an.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    now = pd.Timestamp.now(tz="UTC")
    sym = a.symbol.upper()
    try:
        if a.cmd == "chain":
            ch = load_chain(sym, a.expiry, a.dte, now)
            df = enrich(ch["chain"], ch["spot"], years_to(ch["expiry"], now))
            print(render_chain(ch, df, a.strikes))
            return 0
        legs = [parse_leg(x) for x in a.leg]
        mult = a.multiplier or UNDERLYING.get(sym, (None, None, 100))[2]
        spot = a.spot
        if spot is None:
            spot = load_chain(sym, legs[0]["expiry"], None, now)["spot"]
        res = analyze_legs(legs, spot, now, mult)
        print(json.dumps(res, indent=2, default=float) if a.json else render_analysis(sym, res))
        return 0
    except (OptionsError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
