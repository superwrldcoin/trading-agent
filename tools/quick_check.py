"""Quick trade check from one plain-English line, e.g. "BTC long, 20x, entry 98,400, how does it look?"

Usage:  python tools/quick_check.py "BTC long, 20x, entry 98,400"  [--json] [--equity 10000 --risk-pct 1]
                                    [--mmr 0.005 --fee 0.0005]

Parses asset / side / leverage / entry / stop / targets / equity / risk from the text, fetches live data
(tools/fetch_prices.py), and runs the same math as the full session: reference levels (levels.py),
EMA + VWAP conviction and structure (indicators.py), position math and liquidation (position_calc.py).
Missing stop or targets are derived from structure and labeled as suggestions. Output: a milestone-table
report (skills/report-format.md) or JSON. Analysis only: never places orders.

Not covered (left to the full agent): news / event calendar, P(T1 before stop) judgment, memory updates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import fetch_prices, indicators, levels, positions, rules_check, vol_check  # noqa: E402
from tools import position_calc as pc  # noqa: E402

# ---------- parsing ----------

ALIASES = [  # longest / most specific first
    (r"bitcoin\s*cash|\bbch\b", "BCH/USDT"),
    (r"\bbitcoin\b|\bbtc\b|\bxbt\b", "BTC/USDT"),
    (r"\bxau\s*/?\s*usd\b|\bxau\b|\bgold\b|\bspot\s*gold\b", "XAU/USD"),
    (r"\bgld\b", "GLD"),
    (r"\bsilver\b|\bxag\s*/?\s*usd\b|\bxag\b|\bsi\b", "SI"),
    (r"\bmsft\b|\bmicrosoft\b", "MSFT"),
]
NUM = r"\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*[kK]?(?![\dxX%])"
DEFAULTS = {"crypto": {"mmr": 0.005, "fee": 0.0005}, "other": {"mmr": 0.005, "fee": 0.0005}}


class ParseError(ValueError):
    pass


def to_number(s: str) -> float:
    s = s.replace("$", "").replace(",", "").strip()
    mult = 1000 if s[-1:] in "kK" else 1
    return float(s.rstrip("kK").strip()) * mult


def parse_request(text: str) -> dict:
    t = " " + text.lower() + " "
    symbol = next((sym for pat, sym in ALIASES if re.search(pat, t)), None)
    side = ("long" if re.search(r"\b(long|buy|bought|bull(ish)?)\b", t)
            else "short" if re.search(r"\b(short|sell|sold|bear(ish)?)\b", t) else None)
    req = {"text": text, "symbol": symbol, "side": side, "leverage": None, "entry": None, "stop": None,
           "targets": [], "equity": None, "risk_pct": None}

    m = re.search(r"\b(\d+(?:\.\d+)?)\s*x\b", t) or re.search(r"\bleverage\s*(?:of|:|=)?\s*(\d+(?:\.\d+)?)", t)
    if m:
        req["leverage"] = float(m.group(1))
    m = re.search(rf"\b(?:stop(?:\s*loss)?|sl|invalidation)\b\s*(?:at|@|:|=)?\s*({NUM})", t)
    if m:
        req["stop"] = to_number(m.group(1))
    m = re.search(rf"\b(?:tps?\d?|targets?|t[1-3]|take\s*profits?)\b\s*(?:at|@|:|=)?\s*({NUM}(?:\s*(?:,|/|&|and)\s*{NUM})*)", t)
    if m:
        req["targets"] = [to_number(x) for x in re.findall(NUM, m.group(1))]
    m = (re.search(rf"\b(?:entry|entered|enter|in|filled|fill)\b\s*(?:price)?\s*(?:at|@|:|=|of)?\s*({NUM})", t)
         or re.search(rf"@\s*({NUM})", t)
         or re.search(rf"\b(?:long|short|buy|sell|bought|sold)\b\s*(?:at|from)?\s*({NUM})", t))
    if m:
        req["entry"] = to_number(m.group(1))
    m = re.search(rf"\b(?:equity|account|balance|capital|acct)\b\s*(?:size)?\s*(?:of|is|:|=)?\s*({NUM})", t)
    if m:
        req["equity"] = to_number(m.group(1))
    m = re.search(r"\brisk(?:ing)?\s*(?:of|:|=)?\s*(\d+(?:\.\d+)?)\s*%", t)
    if m:
        req["risk_pct"] = float(m.group(1))
    if symbol is None:
        raise ParseError(f"couldn't find an asset in {text!r}. Watchlist: {', '.join(levels.WATCHLIST)}")
    return req


# ---------- analysis ----------

def fetch_frames(symbol: str) -> tuple[dict, dict]:
    frames, errors = {}, {}
    for tf in ("15M", "1H", "4H", "1D"):
        try:
            bars = indicators.HOURLY_BARS_FOR_VWAP if tf == "1H" else fetch_prices.DEFAULT_BARS
            frames[tf] = fetch_prices.get_ohlcv(symbol, tf, bars=bars)
        except fetch_prices.FetchError as exc:
            errors[tf] = str(exc)
    return frames, errors


def candidate_levels(lv: dict, df4: pd.DataFrame, df1d: pd.DataFrame | None) -> tuple[list, list]:
    """(primary, daily): primary = reference levels + the last 6 confirmed 4H swing highs/lows;
    daily = 1D swings, used only to fill targets when primary runs out (price discovery)."""
    primary = [(lv[k], k) for k in ("PDH", "PDL", "PWH", "PWL", "PMthH", "PMthL") if lv.get(k) is not None]
    hi4, lo4 = indicators.swings(df4)
    primary += [(float(v), f"4H swing {kind} {t:%m-%d}") for ser, kind in ((hi4.tail(6), "high"), (lo4.tail(6), "low"))
                for t, v in ser.items()]
    daily = []
    if df1d is not None and len(df1d) > 5:
        hi1, lo1 = indicators.swings(df1d)
        daily = [(float(v), f"1D swing {kind} {t:%Y-%m-%d}") for ser, kind in ((hi1, "high"), (lo1, "low"))
                 for t, v in ser.items()]
    return primary, daily


def _beyond(side: str, entry: float, cands: list, atr14: float) -> list:
    s = 1 if side == "long" else -1
    # levels within 0.5 ATR of entry are "at entry", not targets
    return sorted({(v, lbl) for v, lbl in cands if s * (v - entry) > 0.5 * atr14}, key=lambda x: s * (x[0] - entry))


def _merge(levels_sorted: list, atr14: float) -> list:
    merged: list[list] = []
    for v, lbl in levels_sorted:
        if merged and abs(v - merged[-1][0]) <= 0.5 * atr14:
            merged[-1][2] += 1
        else:
            merged.append([v, lbl, 0])
    return [(v, lbl + (f" (+{k} more within 0.5 ATR)" if k else "")) for v, lbl, k in merged]


def derive_targets(side: str, entry: float, primary: list, daily: list, atr14: float, n: int = 3) -> list:
    """Nearest reference levels / recent 4H swings beyond entry (merged within 0.5 ATR); 1D swings fill in only
    beyond the last primary target when fewer than n exist."""
    s = 1 if side == "long" else -1
    out = _merge(_beyond(side, entry, primary, atr14), atr14)[:n]
    if len(out) < n:
        start = out[-1][0] if out else entry
        extra = [(v, lbl) for v, lbl in _beyond(side, start, daily, atr14)]
        out += _merge(extra, atr14)[: n - len(out)]
    return sorted(out, key=lambda x: s * (x[0] - entry))


def derive_stop(side: str, entry: float, df4: pd.DataFrame, df1d: pd.DataFrame | None,
                atr14: float) -> tuple[float, str, bool] | None:
    """Beyond the most recent confirmed 4H swing on the far side of entry, + 0.5 x 4H ATR (levels-and-entries step 5).

    If that swing is more than 4 ATR from entry (no 4H structure near a far-away entry), fall back to the nearest
    1D swing beyond entry. Returns (stop, description, used_daily_fallback).
    """
    hi, lo = indicators.swings(df4)
    ser = lo[lo < entry] if side == "long" else hi[hi > entry]
    kind, sign = ("low", -1) if side == "long" else ("high", 1)
    if not ser.empty:
        t, v = ser.index[-1], float(ser.iloc[-1])
        if abs(entry - v) <= 4 * atr14:
            return v + sign * 0.5 * atr14, f"most recent 4H swing {kind} {v:,.2f} ({t:%m-%d %H:%M}) {'-' if sign < 0 else '+'} 0.5 ATR", False
    if df1d is not None and len(df1d) > 5:
        hi1, lo1 = indicators.swings(df1d)
        d = lo1[lo1 < entry] if side == "long" else hi1[hi1 > entry]
        if not d.empty:
            idx = d.idxmax() if side == "long" else d.idxmin()
            v = float(d.loc[idx])
            return v + sign * 0.5 * atr14, f"nearest 1D swing {kind} {v:,.2f} ({idx:%Y-%m-%d}) {'-' if sign < 0 else '+'} 0.5 ATR", True
    if not ser.empty:
        t, v = ser.index[-1], float(ser.iloc[-1])
        return v + sign * 0.5 * atr14, f"most recent 4H swing {kind} {v:,.2f} ({t:%m-%d %H:%M}) {'-' if sign < 0 else '+'} 0.5 ATR", False
    return None


def market_open(symbol: str, now: pd.Timestamp) -> bool:
    src = levels.WATCHLIST[symbol]
    if src.session is levels.CRYPTO:
        return True
    ny = now.tz_convert("America/New_York")
    mins = ny.dayofweek * 1440 + ny.hour * 60 + ny.minute
    if src.session is levels.EQUITY:
        return ny.dayofweek < 5 and 9 * 60 + 30 <= ny.hour * 60 + ny.minute < 16 * 60
    return not (4 * 1440 + 17 * 60 <= mins < 6 * 1440 + 18 * 60)  # gold / CME: closed Fri 17:00 -> Sun 18:00 ET


def analyze(req: dict, frames: dict, errors: dict, now: pd.Timestamp, mmr: float, fee: float) -> dict:
    symbol, side = req["symbol"], req["side"]
    src = levels.WATCHLIST[symbol]
    df4 = frames.get("4H")
    if df4 is None or len(df4) < 30:
        raise fetch_prices.FetchError(f"{symbol}: no usable 4H data ({errors.get('4H', 'too few bars')}). "
                                      f"{fetch_prices.FALLBACK}")
    price_df = frames.get("15M") if frames.get("15M") is not None else df4
    price = float(price_df["close"].iloc[-1])
    atr14 = float(indicators.atr(df4).iloc[-1])
    lv = levels.reference_levels(df4, src.session)
    s4 = indicators.summarize(df4, src.session)
    _, conv = indicators.conviction_section(frames, src.session, volume_is_proxy=src.drop_weekend)
    out = {"request": req, "symbol": symbol, "side": side, "price": price, "atr14": atr14, "levels": lv,
           "structure": s4["structure"], "conviction": conv["conviction"], "vwap": conv["vwap"],
           "ema_4h": conv["ema_4h"], "ema_1d": conv["ema_1d"], "source": src.label,
           "fetched_at": price_df.attrs.get("fetched_at"), "last_bar": f"{price_df.index[-1]:%Y-%m-%d %H:%M} UTC",
           "market_open": market_open(symbol, now), "errors": errors, "flags": [], "assumptions": []}
    flags, assume = out["flags"], out["assumptions"]
    if src.drop_weekend:
        assume.append("XAU/USD priced from OKX XAUT-USDT (Tether Gold proxy)")
    if not out["market_open"]:
        flags.append(("info", f"market closed: prices as of last bar {out['last_bar']}"))
    if errors:
        flags.append(("warn", "some timeframes failed: " + "; ".join(f"{k}: {v.split('.')[0]}" for k, v in errors.items())))
    if side is None:
        flags.append(("warn", "side not given [MISSING]: showing conviction for both sides, no position math"))
        out["verdict"] = "Incomplete: say long or short"
        return out

    c = conv["conviction"][side]
    grade = c["grade"]
    entry = req["entry"]
    if entry is None:
        entry = price
        assume.append(f"entry not given: using current price {price:,.2f}")
    out["entry"] = entry
    dist_atr = (entry - price) / atr14
    out["entry_dist_pct"], out["entry_dist_atr"] = (entry / price - 1) * 100, dist_atr
    s = 1 if side == "long" else -1
    if abs(dist_atr) <= 0.25:
        out["entry_type"] = "at market"
    elif s * dist_atr > 0:
        out["entry_type"] = "breakout entry (beyond current price in the trade direction)"
    else:
        out["entry_type"] = "pullback / limit entry"
    if abs(dist_atr) > 3:
        flags.append(("warn", f"entry is {out['entry_dist_pct']:+.2f}% ({dist_atr:+.1f} ATR) from the current price "
                              f"{price:,.2f}. Is this a stale or mistyped price? Levels below are computed for your entry"))

    stop, stop_src = req["stop"], "your stop"
    if stop is None:
        d = derive_stop(side, entry, df4, frames.get("1D"), atr14)
        if d is None:
            out["verdict"] = "No valid setup: no confirmed 4H swing to put a structural stop behind"
            flags.append(("fail", out["verdict"]))
            return out
        stop, stop_src = d[0], f"suggested: {d[1]}"
        if d[2]:
            flags.append(("warn", f"no 4H structure within 4 ATR of your entry: stop taken from daily structure "
                                  f"({d[1]}). Check it's still relevant"))
    out["stop"], out["stop_source"] = stop, stop_src

    primary, daily = candidate_levels(lv, df4, frames.get("1D"))
    if req["targets"]:
        targets = [(t, "your target") for t in req["targets"]]
    else:
        targets = derive_targets(side, entry, primary, daily, atr14)
    out["targets"] = [{"price": t, "source": lbl} for t, lbl in targets]

    leverage = req["leverage"] or 1.0
    if req["leverage"] is None:
        assume.append("leverage not given: 1x")
    assume.append(f"maintenance margin {mmr:.2%}, taker fee {fee:.3%} per side (check your exchange)")
    try:
        liq = pc.liquidation_price(side, entry, leverage, mmr)
    except ValueError as exc:
        flags.append(("fail", str(exc)))
        out["verdict"] = f"Fails: {exc}"
        return out
    out["liquidation"] = liq
    out["max_leverage_1atr"] = pc.max_leverage(side, entry, stop, mmr, atr14)
    out["stop_width_atr"] = abs(entry - stop) / atr14
    if s * (entry - stop) <= 0:
        flags.append(("fail", f"stop {stop:,.2f} is on the wrong side of entry {entry:,.2f} for a {side}"))
        out["verdict"] = "Fails: stop on the wrong side of entry"
        return out
    if s * (liq - stop) >= 0:
        flags.append(("fail", f"liquidation {liq:,.2f} is hit BEFORE the stop {stop:,.2f} at {leverage:g}x. "
                              f"Max leverage that keeps liquidation 1 ATR beyond the stop: "
                              f"{out['max_leverage_1atr']:.1f}x"))
    elif abs(stop - liq) < atr14:
        flags.append(("warn", f"liquidation {liq:,.2f} is only {abs(stop - liq) / atr14:.2f} ATR beyond the stop "
                              f"(< 1 ATR). Max leverage for a 1 ATR buffer: {out['max_leverage_1atr']:.1f}x"))
    out["vol"] = None
    if leverage > 1 and frames.get("1D") is not None and len(frames["1D"]) > 60:
        out["vol"] = vol_check.analyze(frames["1D"], df4, side, entry, leverage, stop, mmr)
        seen = {m for _, m in flags}
        for lvl, msg in out["vol"]["flags"]:
            if "liquidated before the stop" in msg or "between stop and liquidation" in msg:
                continue  # already covered by the stop-vs-liquidation checks above
            if msg not in seen:
                flags.append((lvl, f"volatility: {msg}"))
    if out["stop_width_atr"] > 4:
        flags.append(("fail", f"stop is {out['stop_width_atr']:.2f} ATR from entry (> 4 ATR): no valid setup"))
    elif out["stop_width_atr"] > 3:
        flags.append(("warn", f"wide stop: {out['stop_width_atr']:.2f} ATR (grade -1)"))
        grade = {"A": "B", "B": "C", "C": "C"}[grade]

    if targets:
        res = pc.compute(pc.Plan(asset=symbol, side=side, entry=entry, stop=stop, targets=[t for t, _ in targets],
                                 leverage=leverage, mmr=mmr, fee=fee, equity=req["equity"], risk_pct=req["risk_pct"]
                                 if req["equity"] else None, atr=atr14))
        out["position"] = res
        if res["weighted_R"] < 2:
            flags.append(("warn", f"weighted R {res['weighted_R']:.2f} is below the 2.0 minimum"))
        if res["targets"][0]["R"] < 0.5:
            flags.append(("warn", f"T1 is only {res['targets'][0]['R']:.2f}R away"))
        if len(targets) == 1:
            flags.append(("info", "only one reference level beyond entry: 100% at T1"))
    else:
        out["position"] = None
        flags.append(("warn", f"no reference level {'above' if side == 'long' else 'below'} your entry in the data "
                              "(price discovery): no target, so no R:R"))
    if req["equity"] is None:
        assume.append("equity / risk % not given: sizing skipped, $ figures are per 1 unit")

    if c["score"] <= 0:
        flags.append(("warn", f"conviction {c['score']:+d}: counter-trend {side}"))
    flags.append(("info", "event calendar and news not checked in quick check: grade capped at B; use the full agent"))
    grade = "B" if grade == "A" else grade
    out["grade"] = grade

    pos = out["position"]
    plan = {"symbol": symbol, "side": side, "leverage": leverage,
            "risk_pct": req["risk_pct"] if req["equity"] else None,
            "weighted_r": pos["weighted_R"] if pos else None,
            "has_stop": True if req["stop"] is not None else None,
            "notional": pos["notional"] if pos and req["equity"] else None,
            "liq_touch_5d": (out["vol"]["liq_touch"].get(5) or 0) * 100 if out.get("vol") else None,
            "option_premium": None}
    try:
        out["rules"] = rules_check.check(plan, rules_check.load_rules(), positions.load(),
                                         rules_check.trade_history(), now)
    except (ValueError, KeyError) as exc:
        out["rules"] = []
        flags.append(("warn", f"rules check failed: {exc}"))
    for r in out["rules"]:
        if r["result"] == "BREAKS":
            flags.append(("fail", f"breaks your rule: {r['rule']} ({r['value']} vs your limit {r['limit']})"))
    tips = out["suggestions"] = []
    if s * (liq - stop) >= 0 or abs(stop - liq) < atr14:
        tips.append(f"Leverage <= {out['max_leverage_1atr']:.1f}x keeps liquidation at least 1 ATR beyond this stop.")
    if abs(dist_atr) > 3:
        tips.append(f"If you meant entering now, re-run with entry {price:,.0f}"
                    f" (or leave entry out to use the current price).")
    if out["stop_width_atr"] > 3:
        tips.append("A closer structural swing (tighter stop) or a pullback entry nearer the stop would shrink the risk.")
    if out["position"] and out["position"]["weighted_R"] < 2:
        tips.append("R:R is short of 2.0: a better entry (closer to the stop) or a stop nearer structure improves it.")
    sev = [f for f, _ in flags]
    if "fail" in sev:
        out["verdict"] = "Fails as specified"
    elif out["position"] and out["position"]["weighted_R"] >= 2 and grade in ("A", "B") and c["score"] > 0:
        out["verdict"] = "Holds up on paper (see flags)"
    else:
        out["verdict"] = "Weak: needs changes before it makes sense"
    return out


# ---------- rendering ----------

def _f(x, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:,.{nd}f}"


def render(a: dict) -> str:
    req = a["request"]
    lines = [f"## {a['symbol']} {a['side'] or ''} quick check: {a['verdict']}", "",
             f"Request: \"{req['text']}\"",
             f"Data: {a['source']}, last bar {a['last_bar']}, fetched {str(a['fetched_at'])[:16]}Z "
             f"({'market open' if a['market_open'] else 'market closed'}) [DATA]",
             f"Current price {_f(a['price'])} [DATA] | 4H ATR14 {_f(a['atr14'])} [CALC]"]
    st = a["structure"]
    lines.append(f"Structure (4H): {st['structure']}, last-3-swing range {_f(st['range_low'])}-{_f(st['range_high'])}, "
                 f"price at {_f(st['range_pos'], 1)}% [CALC]")
    sides = [a["side"]] if a["side"] else ["long", "short"]
    for sd in sides:
        c = a["conviction"][sd]
        against = [x["check"] for x in c["checks"] if x["points"] < 0]
        lines.append(f"Conviction {sd}: {c['score']:+d}/8 -> {c['grade']}{', ' + c['label'] if c['label'] else ''} "
                     f"(EMA {c['ema_score']:+d}/5, VWAP {c['vwap_score']:+d}/3"
                     + (f"; against: {', '.join(against)}" if against else "") + ") [CALC]")
    if a.get("grade"):
        lines.append(f"Grade after modifiers: **{a['grade']}** [JUDGMENT]")
    if a.get("entry") is not None:
        lines.append(f"Entry {_f(a['entry'])}: {a['entry_type']}, {a['entry_dist_pct']:+.2f}% "
                     f"({a['entry_dist_atr']:+.2f} ATR) from current [CALC]")

    if a.get("stop") is not None:
        pos = a.get("position")
        rows = []
        if pos:
            for t, src in zip(pos["targets"], a["targets"]):
                rows.append((t["target"], f"Target ({t['weight']:.0%})", f"{t['R']:.2f}R",
                             f"{_f(t['net'])} / {t['roe_pct']:,.1f}%", src["source"]))
        rows += [(a["price"], "Current price", "", "", "last close"),
                 (a["entry"], "Entry", "0R", "", "your entry" if req["entry"] else "current price"),
                 (a["stop"], "Stop (4H close)", "-1.00R",
                  f"{_f(pos['stop_net'])} / {pos['stop_roe_pct']:,.1f}%" if pos else "", a["stop_source"])]
        if a.get("liquidation") is not None and (req["leverage"] or 1) > 1:
            rows.append((a["liquidation"], f"Liquidation ({req['leverage'] or 1:g}x)", "", "-margin", "isolated, approx"))
        rows.sort(key=lambda r: -r[0])
        lines += ["", "| Milestone | Price | Dist | R | Net P&L / ROE | Source | Tag |", "|---|---|---|---|---|---|---|"]
        for p, name, r, pnl, src in rows:
            tag = "[DATA]" if name == "Current price" else "[DATA:user]" if src.startswith("your") else "[CALC]"
            lines.append(f"| {name} | {_f(p)} | {(p / a['price'] - 1) * 100:+.2f}% | {r} | {pnl} | {src} | {tag} |")
        if pos:
            lines.append(f"\nWeighted R {pos['weighted_R']:.2f} | stop width {a['stop_width_atr']:.2f} ATR | "
                         f"max leverage for 1 ATR liq buffer {_f(a['max_leverage_1atr'], 1)}x | "
                         f"units {_f(pos['units'], 4)}, margin {_f(pos['margin'])} [CALC]")
        elif a.get("max_leverage_1atr") is not None:
            lines.append(f"\nStop width {a['stop_width_atr']:.2f} ATR | max leverage for 1 ATR liq buffer "
                         f"{_f(a['max_leverage_1atr'], 1)}x [CALC]")

    v = a.get("vol")
    if v:
        p = lambda n: "n/a" if v["liq_touch"].get(n) is None else f"{v['liq_touch'][n]:.0%}"
        lines += ["", f"Leverage vs volatility: liquidation {v['liq_dist_pct']:.2f}% away = {v['liq_atr_1d']:.2f} daily ATR. "
                      f"Historically a move that size against a {a['side']} came within 1d / 3d / 5d / 10d on "
                      f"{p(1)} / {p(3)} / {p(5)} / {p(10)} of days (last {v['lookback_days']} days, not a forecast). "
                      f"Leverage that kept 5-day odds <= 5%: {_f(v['safe_leverage_5d'], 1)}x [CALC]"]
    lv = a["levels"]
    lines += ["", "Reference levels: " + ", ".join(f"{k} {_f(lv[k])}" for k in ("PDH", "PDL", "PWH", "PWL", "PMthH", "PMthL")),
              "VWAP: " + ", ".join(f"{k} {_f(a['vwap'][k])}" for k in ("session", "week", "month"))
              + " | 4H EMA21/50/200: " + " / ".join(_f(a["ema_4h"][k]) for k in ("ema21", "ema50", "ema200"))]
    order = {"fail": 0, "warn": 1, "info": 2}
    if a["flags"]:
        lines += ["", "Flags:"] + [f"- **{lvl.upper()}**: {msg}" for lvl, msg in sorted(a["flags"], key=lambda f: order[f[0]])]
    if a.get("rules") is not None:
        set_rules = [r for r in a["rules"] if r["result"] != "not set"]
        broken = [r for r in set_rules if r["result"] == "BREAKS"]
        if not set_rules:
            lines += ["", "Rules: none set yet (Trading rules block in memory/user-preferences.md)."]
        else:
            lines += ["", f"Rules: {len(broken)} broken of {len(set_rules)} set"
                      + (": " + "; ".join(r["rule"] for r in broken) if broken else "")
                      + ". Full table: python tools/rules_check.py"]
    if a.get("suggestions"):
        lines += ["", "What would change the verdict [JUDGMENT]:"] + [f"- {x}" for x in a["suggestions"]]
    if a["assumptions"]:
        lines += ["", "Assumptions:"] + [f"- {x} [ASSUMPTION]" for x in a["assumptions"]]
    lines += ["", "P(T1 before stop): not estimated in quick check (judgment; ask the full agent).",
              "", "Analysis only, not financial advice. The user makes and executes all trading decisions."]
    return "\n".join(lines) + "\n"


def run(text: str, equity: float | None = None, risk_pct: float | None = None,
        mmr: float | None = None, fee: float | None = None, now: pd.Timestamp | None = None) -> dict:
    req = parse_request(text)
    if equity is not None:
        req["equity"] = equity
    if risk_pct is not None:
        req["risk_pct"] = risk_pct
    if req["equity"] is not None and req["risk_pct"] is None:
        req["risk_pct"] = 1.0
    cls = "crypto" if levels.WATCHLIST[req["symbol"]].session is levels.CRYPTO else "other"
    frames, errors = fetch_frames(req["symbol"])
    return analyze(req, frames, errors, now or pd.Timestamp.now(tz="UTC"),
                   mmr if mmr is not None else DEFAULTS[cls]["mmr"], fee if fee is not None else DEFAULTS[cls]["fee"])


def _jsonable(o):
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return str(o)


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Quick trade check from one line of text.")
    ap.add_argument("text", nargs="+")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--equity", type=float)
    ap.add_argument("--risk-pct", type=float)
    ap.add_argument("--mmr", type=float)
    ap.add_argument("--fee", type=float)
    a = ap.parse_args(argv)
    try:
        res = run(" ".join(a.text), a.equity, a.risk_pct, a.mmr, a.fee)
    except ParseError as exc:
        print(f"ERROR: {exc}")
        return 2
    except fetch_prices.FetchError as exc:
        print(f"FETCH FAILED: {exc}")
        return 1
    print(json.dumps(res, indent=2, default=_jsonable) if a.json else render(res))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
