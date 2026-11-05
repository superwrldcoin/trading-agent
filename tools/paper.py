"""Paper trading desk: simulated trades at live feed prices, with a journal. No real orders, keys or wallets.

  python tools/paper.py status                         account, open/pending trades marked to the live price
  python tools/paper.py open --symbol BTC/USDT --side long --stop 79000 --targets 82000 84000 \
         [--risk-pct 1 | --size N] [--leverage 5] [--limit P] [--p-t1 55] [--note ".."]
  python tools/paper.py close PT-003                   close at the live price
  python tools/paper.py cancel PT-004                  cancel a pending limit order
  python tools/paper.py reset --equity 10000           start a fresh account (old trades are archived in the file)
  python tools/paper.py stats                          journal: win rate, expectancy, equity curve, P(T1) calibration

Rules (AGENT.md): only the user opens paper trades (UI click or this CLI). The agent may pre-fill a ticket but never
opens one. Store: memory/paper.json (private, gitignored).

Fills and exits are simulated from 15M bars of the same feeds the analysis uses (tools/fetch_prices.py):
- market orders fill at the last price; limit orders fill at the limit when a later bar trades through it;
- each bar is checked for the stop / liquidation first, then targets, so a bar that touches both counts as a stop
  (conservative, as in a +2R/-1R "first touch" grading);
- targets scale out by the plan weights (40/40/20, 50/50, 100); the stop stays where it is;
- liquidation uses position_calc (isolated margin, MMR); fees are charged per side on notional.

The Broker class is the seam for a real execution venue later. Only PaperBroker exists; adding a live broker
requires changing AGENT.md's analysis-only rule first.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import fetch_prices, levels, universe  # noqa: E402
from tools import position_calc as pc  # noqa: E402

PAPER_PATH = Path(os.environ.get("TA_PAPER_PATH") or ROOT / "memory" / "paper.json")   # env override for smoke tests
DEFAULT_EQUITY = 10_000.0
DEFAULT_FEE = 0.0005
DEFAULT_MMR = 0.005
QUOTE_TTL = 20            # seconds: the desk wants fresher prices than the 3-min analysis cache
BAR_TF = "15M"
_LOCK = threading.Lock()


class PaperError(ValueError):
    pass


# ---------- broker seam ----------

class Broker:
    """Where prices and fills come from. Real venues are out of scope (AGENT.md: analysis only)."""
    name = "abstract"
    live = False

    def bars(self, symbol: str, bars: int = 700) -> pd.DataFrame:
        raise NotImplementedError


class PaperBroker(Broker):
    name = "paper"

    def bars(self, symbol: str, bars: int = 700) -> pd.DataFrame:
        return fetch_prices.get_ohlcv(symbol, BAR_TF, bars=bars, ttl=QUOTE_TTL)


# ---------- store ----------

def new_account(equity: float = DEFAULT_EQUITY, now: pd.Timestamp | None = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC")
    return {"start_equity": float(equity), "created": now.isoformat(), "fee": DEFAULT_FEE, "mmr": DEFAULT_MMR}


def load(path: Path | None = None) -> dict:
    p = path or PAPER_PATH
    if not p.exists():
        return {"account": new_account(), "trades": [], "next_id": 1, "archive": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save(state: dict, path: Path | None = None) -> None:
    p = path or PAPER_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    tmp.replace(p)


# ---------- accounting ----------

def _s(side: str) -> int:
    return 1 if side == "long" else -1


def balance(state: dict) -> float:
    """Start equity plus realized P&L net of fees (closed trades and partial exits)."""
    return state["account"]["start_equity"] + sum(t.get("realized", 0.0) for t in state["trades"])


def unrealized(t: dict, price: float) -> float:
    if t["status"] != "open":
        return 0.0
    return _s(t["side"]) * (price - t["entry"]) * t["size"] * t["remaining"]


def initial_risk(t: dict) -> float:
    return abs(t["entry"] - t["stop"]) * t["size"]


def _exit(t: dict, frac: float, price: float, ts: str, reason: str) -> None:
    """Close `frac` of the original size at `price`; fees on the exit notional."""
    units = t["size"] * frac
    gross = _s(t["side"]) * (price - t["entry"]) * units
    fee = abs(price * units) * t["fee"]
    t["realized"] += gross - fee
    t["fees"] += fee
    t["remaining"] = round(t["remaining"] - frac, 10)
    t["exits"].append({"ts": ts, "price": price, "frac": frac, "reason": reason, "pnl": gross - fee})
    if t["remaining"] <= 1e-9:
        t["remaining"] = 0.0
        t["status"] = "closed"
        t["closed_at"] = ts
        t["exit_reason"] = reason
        risk = initial_risk(t)
        t["R"] = t["realized"] / risk if risk else None


def _fill(t: dict, price: float, ts: str) -> None:
    t["entry"] = price
    t["opened_at"] = ts
    t["status"] = "open"
    t["liquidation"] = pc.liquidation_price(t["side"], price, t["leverage"], t["mmr"])
    fee = abs(price * t["size"]) * t["fee"]
    t["realized"] -= fee
    t["fees"] += fee
    t["mfe"] = t["mae"] = 0.0


def step(t: dict, bars: pd.DataFrame) -> None:
    """Advance one pending/open trade through bars that start at or after its last processed time."""
    if t["status"] not in ("pending", "open"):
        return
    s = _s(t["side"])
    # bars that START at/after the order time (no pre-fill prices); the last, still-forming bar is re-read next time
    since = pd.Timestamp(t.get("checked_to") or t["created"])
    for ts, b in bars[bars.index >= since].iterrows():
        iso = ts.isoformat()
        if t["status"] == "pending":
            lim = t["limit"]
            if (s > 0 and b["low"] <= lim) or (s < 0 and b["high"] >= lim):
                _fill(t, lim, iso)
            else:
                continue
        # adverse first: whichever of stop / liquidation sits closer to entry is hit first
        worst = b["low"] if s > 0 else b["high"]
        best = b["high"] if s > 0 else b["low"]
        t["mae"] = min(t["mae"], s * (worst - t["entry"]))
        t["mfe"] = max(t["mfe"], s * (best - t["entry"]))
        guard, reason = ((t["stop"], "stop") if s * (t["stop"] - t["liquidation"]) >= 0
                         else (t["liquidation"], "liquidation"))
        if s * (worst - guard) <= 0:
            if t.get("t1_before_stop") is None:
                t["t1_before_stop"] = False
            _exit(t, t["remaining"], guard, iso, reason)
            break
        for tg in t["targets"]:
            if tg.get("hit_at") is None and s * (best - tg["price"]) >= 0:
                tg["hit_at"] = iso
                if t.get("t1_before_stop") is None:
                    t["t1_before_stop"] = True
                _exit(t, min(tg["weight"], t["remaining"]), tg["price"], iso, "target")
        if t["status"] == "closed":
            break
    if len(bars):
        t["checked_to"] = bars.index[-1].isoformat()   # the last bar may still be forming: re-check it next time


def refresh(state: dict, broker: Broker | None = None) -> dict:
    """Advance every pending/open trade with fresh bars; returns {symbol: last price} (and errors)."""
    broker = broker or PaperBroker()
    marks, errors = {}, {}
    syms = sorted({t["symbol"] for t in state["trades"] if t["status"] in ("pending", "open")})
    for sym in syms:
        try:
            bars = broker.bars(sym)
        except Exception as exc:  # one bad feed shouldn't freeze the desk
            errors[sym] = str(exc)[:160]
            continue
        marks[sym] = float(bars["close"].iloc[-1])
        for t in state["trades"]:
            if t["symbol"] == sym:
                step(t, bars)
    return {"marks": marks, "errors": errors}


# ---------- orders ----------

def quote(symbol: str, broker: Broker | None = None) -> dict:
    if symbol not in levels.WATCHLIST:
        raise PaperError(f"{symbol} is not on the watchlist")
    bars = (broker or PaperBroker()).bars(symbol, bars=100)
    last = bars.iloc[-1]
    ago = bars[bars.index <= bars.index[-1] - pd.Timedelta(hours=24)]
    prev = float(ago["close"].iloc[-1]) if len(ago) else float(bars["open"].iloc[0])
    return {"symbol": symbol, "price": float(last["close"]), "ts": bars.index[-1].isoformat(),
            "chg_24h_pct": (float(last["close"]) / prev - 1) * 100, "source": bars.attrs.get("source", "")}


def plan_ticket(state: dict, req: dict, price: float) -> dict:
    """Validate a ticket and size it with position_calc. Returns the trade dict (not yet stored) and a preview."""
    sym, side = req.get("symbol"), req.get("side")
    if sym not in levels.WATCHLIST:
        raise PaperError(f"unknown symbol {sym!r}")
    if side not in ("long", "short"):
        raise PaperError("side must be long or short")
    stop = req.get("stop")
    if stop is None:
        raise PaperError("a stop is required for paper trades (your rules require one)")
    targets = sorted([float(x) for x in req.get("targets") or []], key=lambda x: _s(side) * x)
    if not targets:
        raise PaperError("give at least one target")
    limit = req.get("limit")
    entry = float(limit) if limit is not None else price
    if limit is not None and _s(side) * (entry - price) > 0:
        raise PaperError(f"a {side} limit must be {'below' if side == 'long' else 'above'} the current price "
                         f"{price:,.4f}; use a market order to chase")
    acct = state["account"]
    eq = balance(state)
    spec = universe.contract(sym) or {}
    risk_pct, size = req.get("risk_pct"), req.get("size")
    if req.get("contracts") not in (None, ""):
        if not spec:
            raise PaperError(f"{sym} isn't a futures contract: give units or risk %")
        n = float(req["contracts"])
        if n < 1 or n != int(n):
            raise PaperError("contracts must be a whole number >= 1")
        size = n * spec["multiplier"]
    try:
        res = pc.compute(pc.Plan(asset=sym, side=side, entry=entry, stop=float(stop), targets=targets,
                                 leverage=float(req.get("leverage") or 1), mmr=acct["mmr"], fee=acct["fee"],
                                 size=float(size) if size else None,
                                 equity=eq if not size else None, risk_pct=float(risk_pct or 1) if not size else None,
                                 contract_size=spec.get("multiplier"), contract_name=spec.get("name")))
    except ValueError as exc:
        raise PaperError(str(exc)) from exc
    units = res["units"]
    c = res.get("contracts")
    if c and c["sized"] and not size:
        if c["whole"] < 1:
            raise PaperError(f"risk budget too small for 1 {c['name']} (1 contract risks "
                             f"{c['risk_per_contract']:,.2f} to the stop)")
        units = c["whole"] * c["size"]
    if res["liquidates_before_stop"]:
        raise PaperError(f"liquidation {res['liquidation']:,.4f} is hit before the stop at "
                         f"{float(req.get('leverage') or 1):g}x: lower the leverage")
    weights = res["targets"]
    trade = {"symbol": sym, "side": side, "order": "limit" if limit is not None else "market",
             "limit": entry if limit is not None else None, "entry": entry, "size": units,
             "contracts": (units / c["size"]) if c else None, "contract_name": c["name"] if c else None,
             "leverage": float(req.get("leverage") or 1), "mmr": acct["mmr"], "fee": acct["fee"],
             "stop": float(stop), "targets": [{"price": w["target"], "weight": w["weight"], "hit_at": None}
                                              for w in weights],
             "liquidation": pc.liquidation_price(side, entry, float(req.get("leverage") or 1), acct["mmr"]),
             "p_t1": float(req["p_t1"]) if req.get("p_t1") not in (None, "") else None,
             "note": str(req.get("note") or "")[:280], "source": str(req.get("source") or "ticket")[:40]}
    risk_amt = (abs(entry - float(stop)) + (entry + float(stop)) * acct["fee"]) * units   # loss at the stop incl. fees
    preview = {"entry": entry, "units": units, "notional": units * entry, "margin": units * entry / trade["leverage"],
               "risk": risk_amt, "risk_pct": risk_amt / eq * 100 if eq else None, "liquidation": trade["liquidation"],
               "weighted_R": res["weighted_R"], "targets": [{"price": w["target"], "weight": w["weight"],
                                                             "R": w["R"]} for w in weights],
               "contracts": trade["contracts"], "contract_name": trade["contract_name"], "equity": eq,
               "notes": [n for n in res["notes"] if "per 1 unit" not in n]}
    preview["rules"] = rules_for(state, trade, preview)
    # recorded on the trade so XP can tell rule-clean trades from rule breaks later
    trade["weighted_R"] = res["weighted_R"]          # kept for the daily challenge (needs the planned R at open)
    trade["rules_checked"] = bool(preview["rules"])
    trade["rule_breaks"] = [r["rule"] for r in preview["rules"] if r["result"] == "BREAKS"]
    preview["xp"] = xp_preview(state, trade)
    return {"trade": trade, "preview": preview}


def rules_for(state: dict, trade: dict, preview: dict) -> list[dict]:
    """The user's Trading rules applied to the paper account (same discipline as live). Informational."""
    from tools import rules_check
    try:
        rules = rules_check.load_rules()
    except Exception:
        return []
    now = pd.Timestamp.now(tz="UTC")
    open_pos = [{"symbol": t["symbol"], "entry": t["entry"], "stop": t["stop"], "size": t["size"] * t["remaining"],
                 "side": t["side"]} for t in state["trades"] if t["status"] in ("open", "pending")]
    hist = [{"R": t.get("R"), "closed": pd.Timestamp(t["closed_at"]) if t.get("closed_at") else None,
             "opened": pd.Timestamp(t.get("opened_at") or t["created"])} for t in state["trades"]
            if t["status"] in ("open", "closed")]
    plan = {"symbol": trade["symbol"], "leverage": trade["leverage"], "risk_pct": preview["risk_pct"],
            "notional": preview["notional"], "weighted_r": preview["weighted_R"], "has_stop": True}
    rows = rules_check.check(plan, rules, {"equity": preview["equity"], "positions": open_pos}, hist, now)
    return [r for r in rows if r["result"] != "not set"]


def open_trade(state: dict, req: dict, broker: Broker | None = None, now: pd.Timestamp | None = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC")
    q = quote(req.get("symbol"), broker)
    built = plan_ticket(state, req, q["price"])
    t = built["trade"]
    t.update(id=f"PT-{state['next_id']:03d}", created=now.isoformat(), status="pending", realized=0.0, fees=0.0,
             remaining=1.0, exits=[], t1_before_stop=None, R=None, closed_at=None, exit_reason=None,
             checked_to=now.isoformat(), opened_at=None, mfe=0.0, mae=0.0, quote_ts=q["ts"])
    state["next_id"] += 1
    if t["order"] == "market":
        _fill(t, q["price"], now.isoformat())
    state["trades"].append(t)
    return t


def find(state: dict, tid: str) -> dict:
    t = next((t for t in state["trades"] if t["id"] == tid), None)
    if t is None:
        raise PaperError(f"no paper trade {tid}")
    return t


def close_trade(state: dict, tid: str, price: float, now: pd.Timestamp | None = None) -> dict:
    t = find(state, tid)
    if t["status"] != "open":
        raise PaperError(f"{tid} is {t['status']}, not open")
    if t.get("t1_before_stop") is None:
        t["t1_before_stop"] = False   # closed by hand before T1: counts as "T1 not reached" for calibration
    _exit(t, t["remaining"], price, (now or pd.Timestamp.now(tz="UTC")).isoformat(), "manual")
    return t


def cancel_trade(state: dict, tid: str, now: pd.Timestamp | None = None) -> dict:
    t = find(state, tid)
    if t["status"] != "pending":
        raise PaperError(f"{tid} is {t['status']}: only pending limit orders can be cancelled")
    t["status"], t["closed_at"] = "cancelled", (now or pd.Timestamp.now(tz="UTC")).isoformat()
    return t


def reset(state: dict, equity: float, now: pd.Timestamp | None = None) -> dict:
    if equity <= 0:
        raise PaperError("equity must be > 0")
    state.setdefault("archive", []).append({"account": state["account"], "trades": state["trades"]})
    state.update(account=new_account(equity, now), trades=[], next_id=state.get("next_id", 1))
    return state


# ---------- journal ----------

def stats(state: dict) -> dict:
    closed = sorted([t for t in state["trades"] if t["status"] == "closed"], key=lambda t: t["closed_at"])
    start = state["account"]["start_equity"]
    curve, eq, peak, max_dd = [{"ts": state["account"]["created"], "equity": start}], start, start, 0.0
    for t in closed:
        eq += t["realized"]
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100 if peak else 0)
        curve.append({"ts": t["closed_at"], "equity": eq, "id": t["id"]})
    rs = [t["R"] for t in closed if t.get("R") is not None]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(t["realized"] for t in closed if t["realized"] > 0)
    gross_loss = -sum(t["realized"] for t in closed if t["realized"] < 0)
    out = {"closed": len(closed), "wins": len(wins), "win_rate": len(wins) / len(rs) * 100 if rs else None,
           "avg_R": sum(rs) / len(rs) if rs else None, "avg_win_R": sum(wins) / len(wins) if wins else None,
           "avg_loss_R": sum(losses) / len(losses) if losses else None,
           "profit_factor": gross_win / gross_loss if gross_loss else None,
           "net_pnl": sum(t["realized"] for t in closed), "max_drawdown_pct": max_dd, "curve": curve,
           "liquidations": sum(1 for t in closed if t.get("exit_reason") == "liquidation"),
           "by_symbol": {}, "calibration": calibration(closed)}
    for t in closed:
        b = out["by_symbol"].setdefault(t["symbol"], {"n": 0, "pnl": 0.0, "R": 0.0})
        b["n"] += 1
        b["pnl"] += t["realized"]
        b["R"] += t.get("R") or 0.0
    out["insights"] = insights(out, closed)
    return out


def calibration(closed: list[dict]) -> list[dict]:
    """P(T1 before stop) you called vs how often T1 actually came first, in buckets."""
    rows = []
    for lo, hi in ((0, 40), (40, 60), (60, 101)):
        b = [t for t in closed if t.get("p_t1") is not None and lo <= t["p_t1"] < hi and t.get("t1_before_stop") is not None]
        if b:
            rows.append({"bucket": f"{lo}-{min(hi, 100)}%", "n": len(b), "called": sum(t["p_t1"] for t in b) / len(b),
                         "actual": sum(1 for t in b if t["t1_before_stop"]) / len(b) * 100})
    return rows


def insights(s: dict, closed: list[dict]) -> list[str]:
    """Plain-language coaching lines once there's a sample (5+ closed trades). Facts from the journal only."""
    if s["closed"] < 5:
        return [f"{5 - s['closed']} more closed paper trade(s) before the journal draws conclusions."]
    out = []
    if s["avg_loss_R"] is not None and s["avg_loss_R"] < -1.1:
        out.append(f"Average loser is {s['avg_loss_R']:.2f}R, worse than the planned -1R: slippage through stops "
                   f"or liquidations ({s['liquidations']}).")
    if s["win_rate"] is not None and s["avg_win_R"] is not None and s["avg_loss_R"] is not None:
        exp = s["win_rate"] / 100 * s["avg_win_R"] + (1 - s["win_rate"] / 100) * s["avg_loss_R"]
        out.append(f"Expectancy {exp:+.2f}R per trade ({s['win_rate']:.0f}% wins x {s['avg_win_R']:.2f}R, "
                   f"losses {s['avg_loss_R']:.2f}R).")
    manual = [t for t in closed if t.get("exit_reason") == "manual" and (t.get("R") or 0) > 0 and
              not any(x["reason"] == "target" for x in t["exits"])]
    if len(manual) >= 2:
        out.append(f"{len(manual)} winners were closed by hand before T1: check whether you're cutting winners early.")
    for c in s["calibration"]:
        if c["n"] >= 5 and abs(c["called"] - c["actual"]) >= 15:
            out.append(f"In the {c['bucket']} bucket you called {c['called']:.0f}% but T1 came first "
                       f"{c['actual']:.0f}% of the time ({c['n']} trades): "
                       f"{'overconfident' if c['called'] > c['actual'] else 'underconfident'}.")
    if s["by_symbol"]:
        worst = min(s["by_symbol"].items(), key=lambda kv: kv[1]["R"])
        if worst[1]["R"] < 0 and worst[1]["n"] >= 3:
            out.append(f"{worst[0]} is your weakest symbol: {worst[1]['R']:+.2f}R over {worst[1]['n']} trades.")
    return out[:4]


# ---------- progress: XP, levels, streaks, badges ----------
# XP rewards discipline first and results second (the user's choice, 2026-11-05):
#   on fill:  planned trade (stop + targets) +10, P(T1) called +5, journal note +5, rule-clean +15,
#             each rule broken -25 (max -50). Only the first max_trades_per_day fills of a UTC day earn the
#             positive part, so trading more doesn't level you faster.
#   on close: exit by stop or target (plan respected) +10, liquidation -30, a P(T1) call on the right side +5,
#             paper P&L +10 per R won (losses cost nothing), P&L XP capped at PNL_XP_CAP per UTC day.
# Derived from the journal every time (never stored), so it can't drift; archived accounts still count.
XP = {"plan": 10, "p_t1": 5, "note": 5, "clean": 15, "break": -25, "break_floor": -50,
      "respected": 10, "liquidation": -30, "good_call": 5, "per_R": 10}
PNL_XP_CAP = 50
NOTE_MIN = 10
DEFAULT_DAILY_TRADES = 3
RANKS = [(1, "Rookie"), (3, "Apprentice"), (5, "Analyst"), (8, "Strategist"), (11, "Risk Manager"), (15, "Desk Lead")]
BADGES = [("first_fill", "First fill", "Open your first paper trade"),
          ("first_t1", "Bullseye", "Hit T1 on a paper trade"),
          ("clean_10", "By the book", "10 rule-clean trades in a row"),
          ("journal_10", "Journaler", "Write a note on 10 trades"),
          ("streak_5", "Five-day streak", "5 days in a row with clean trades"),
          ("calibrated", "Calibrated", "20+ P(T1) calls within 10 points of what happened"),
          ("challenge_5", "Regular", "Complete 5 daily challenges")]

# Daily challenge: one watchlist symbol per UTC day, the same for everyone with this watchlist (hash of the date).
# Weekends draw from 24/7 crypto only, since CME and stocks are closed. Complete it by paper-trading that symbol
# with a rule-clean ticket, a P(T1) call and planned weighted R >= your min_weighted_r: +40 XP, at most once a day,
# and +20 more if that trade reaches T1 before its stop. It rewards one good setup, not more trades.
CHALLENGE_XP = {"done": 40, "t1": 20}
CHALLENGE_CLASSES = {"crypto", "equity_index", "energy"}
CHALLENGE_EXTRA = {"MGC"}


def _min_r() -> float:
    try:
        from tools import rules_check
        v = (rules_check.load_rules() or {}).get("min_weighted_r")
        return float(v) if v is not None else 2.0
    except Exception:
        return 2.0


def challenge_symbol(day: str) -> str | None:
    """The day's symbol (UTC date 'YYYY-MM-DD'): crypto only on Sat/Sun, else crypto + index/energy/gold micros."""
    import hashlib
    weekend = pd.Timestamp(day).dayofweek >= 5
    pool = sorted(c["id"] for c in universe.core() if c["id"] in levels.WATCHLIST and
                  (c["asset_class"] == "crypto" or (not weekend and (c["asset_class"] in CHALLENGE_CLASSES
                                                                      or c["id"] in CHALLENGE_EXTRA))))
    if not pool:
        return None
    return pool[int(hashlib.sha256(day.encode()).hexdigest(), 16) % len(pool)]


def _qualifies(t: dict, min_r: float) -> list[tuple[str, bool]]:
    return [("Rule-clean ticket", bool(t.get("rules_checked")) and not t.get("rule_breaks")),
            ("P(T1) called", t.get("p_t1") is not None),
            (f"Weighted R >= {min_r:g}", (t.get("weighted_R") or 0) >= min_r - 1e-9)]


def challenge_events(trades: list[dict], min_r: float) -> list[dict]:
    """XP events for every day's challenge in the journal (first qualifying fill of the day's symbol)."""
    events, done = [], set()
    for t in sorted([t for t in trades if t.get("opened_at")], key=lambda t: t["opened_at"]):
        d = _day(t["opened_at"])
        if d in done or t.get("symbol") != challenge_symbol(d) or not all(ok for _, ok in _qualifies(t, min_r)):
            continue
        done.add(d)
        events.append({"ts": t["opened_at"], "id": t["id"], "label": "Daily challenge", "xp": CHALLENGE_XP["done"], "day": d})
        if t["status"] == "closed" and t.get("t1_before_stop"):
            events.append({"ts": t["closed_at"], "id": t["id"], "label": "Challenge T1", "xp": CHALLENGE_XP["t1"], "day": d})
    return events


def challenge(state: dict, now: pd.Timestamp | None = None) -> dict:
    """Today's challenge card: symbol, goal checklist (best attempt so far), status and time to the UTC reset."""
    now = now or pd.Timestamp.now(tz="UTC")
    day, min_r = now.strftime("%Y-%m-%d"), _min_r()
    sym = challenge_symbol(day)
    tries = [t for t in _all_trades(state) if t.get("opened_at") and _day(t["opened_at"]) == day and t.get("symbol") == sym]
    ev = [e for e in challenge_events(tries, min_r) if e["day"] == day]
    best = max(tries, key=lambda t: sum(ok for _, ok in _qualifies(t, min_r)), default=None)
    goals = _qualifies(best, min_r) if best else _qualifies({}, min_r)
    done_id = next((e["id"] for e in ev if e["label"] == "Daily challenge"), None)
    reset = (now.normalize() + pd.Timedelta(days=1))
    return {"date": day, "symbol": sym, "min_R": min_r, "reward": CHALLENGE_XP, "done": done_id is not None,
            "trade_id": done_id, "t1": any(e["label"] == "Challenge T1" for e in ev), "attempts": len(tries),
            "goals": [{"label": l, "ok": bool(best) and ok} for l, ok in goals],
            "resets_at": reset.isoformat(), "weekend": pd.Timestamp(day).dayofweek >= 5}


def level_for(xp: int) -> dict:
    """Level L needs 100 + 50*(L-1) XP to reach L+1 (100, 150, 200, ...)."""
    lvl, floor = 1, 0
    while xp >= floor + 100 + 50 * (lvl - 1):
        floor += 100 + 50 * (lvl - 1)
        lvl += 1
    need = 100 + 50 * (lvl - 1)
    rank = [name for start, name in RANKS if lvl >= start][-1]
    return {"level": lvl, "rank": rank, "into": xp - floor, "need": need, "pct": (xp - floor) / need * 100}


def _daily_cap() -> int:
    try:
        from tools import rules_check
        n = (rules_check.load_rules() or {}).get("max_trades_per_day")
        return int(n) if n else DEFAULT_DAILY_TRADES
    except Exception:
        return DEFAULT_DAILY_TRADES


def _open_parts(t: dict, counted: bool) -> list[tuple[str, int]]:
    """XP for a filled trade. `counted` = within today's trade cap."""
    parts = []
    if counted:
        parts.append(("Planned trade", XP["plan"]))
        if t.get("p_t1") is not None:
            parts.append(("P(T1) called", XP["p_t1"]))
        if len((t.get("note") or "").strip()) >= NOTE_MIN:
            parts.append(("Journal note", XP["note"]))
        if t.get("rules_checked") and not t.get("rule_breaks"):
            parts.append(("Rule-clean", XP["clean"]))
    if t.get("rule_breaks"):
        parts.append((f"Broke {len(t['rule_breaks'])} rule(s)", max(XP["break_floor"], XP["break"] * len(t["rule_breaks"]))))
    return parts


def _all_trades(state: dict) -> list[dict]:
    return [t for a in state.get("archive", []) for t in a["trades"]] + state["trades"]


def _day(ts: str) -> str:
    """UTC calendar day of an ISO timestamp."""
    t = pd.Timestamp(ts)
    return (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).strftime("%Y-%m-%d")


def xp_preview(state: dict, trade: dict, now: pd.Timestamp | None = None) -> dict:
    """What opening this ticket would earn right now (shown on the ticket before the user opens it)."""
    today = (now or pd.Timestamp.now(tz="UTC")).strftime("%Y-%m-%d")
    fills_today = sum(1 for t in _all_trades(state) if t.get("opened_at") and _day(t["opened_at"]) == today)
    counted = fills_today < _daily_cap()
    parts = _open_parts(trade, counted)
    return {"total": sum(x for _, x in parts), "parts": [{"label": l, "xp": x} for l, x in parts],
            "capped": not counted, "note": None if counted else f"Daily trade cap ({_daily_cap()}) reached: only penalties apply"}


def progress(state: dict, now: pd.Timestamp | None = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC")
    cap = _daily_cap()
    trades = sorted([t for t in _all_trades(state) if t.get("opened_at")], key=lambda t: t["opened_at"])
    events, fills_per_day, pnl_per_day = [], {}, {}
    for t in trades:
        d = _day(t["opened_at"])
        fills_per_day[d] = fills_per_day.get(d, 0) + 1
        for label, xp in _open_parts(t, fills_per_day[d] <= cap):
            events.append({"ts": t["opened_at"], "id": t["id"], "label": label, "xp": xp})
    for t in sorted([t for t in trades if t["status"] == "closed"], key=lambda t: t["closed_at"]):
        ts, d = t["closed_at"], _day(t["closed_at"])
        reason = t.get("exit_reason")
        if reason in ("stop", "target"):
            events.append({"ts": ts, "id": t["id"], "label": "Plan respected", "xp": XP["respected"]})
        elif reason == "liquidation":
            events.append({"ts": ts, "id": t["id"], "label": "Liquidated", "xp": XP["liquidation"]})
        if t.get("p_t1") is not None and t.get("t1_before_stop") is not None \
                and (t["p_t1"] >= 50) == bool(t["t1_before_stop"]) and t["p_t1"] != 50:
            events.append({"ts": ts, "id": t["id"], "label": "Good P(T1) call", "xp": XP["good_call"]})
        if (t.get("R") or 0) > 0:
            room = PNL_XP_CAP - pnl_per_day.get(d, 0)
            xp = min(room, int(round(t["R"] * XP["per_R"])))
            if xp > 0:
                pnl_per_day[d] = pnl_per_day.get(d, 0) + xp
                events.append({"ts": ts, "id": t["id"], "label": f"Won {t['R']:+.2f}R", "xp": xp})
    ch_events = challenge_events(trades, _min_r())
    events += [{k: v for k, v in e.items() if k != "day"} for e in ch_events]
    events.sort(key=lambda e: e["ts"])
    total = max(0, sum(e["xp"] for e in events))

    # clean streak: consecutive UTC days with >= 1 fill and no rule breaks, ending today or yesterday
    days = {}
    for t in trades:
        d = _day(t["opened_at"])
        days[d] = days.get(d, True) and not t.get("rule_breaks")
    streak = best = run = 0
    prev = None
    for d in sorted(days):
        cur = pd.Timestamp(d)
        run = (run + 1 if prev is not None and (cur - prev).days == 1 else 1) if days[d] else 0
        best, prev = max(best, run), cur
    last = max(days) if days else None
    if last and days[last] and (now.normalize().tz_localize(None) - pd.Timestamp(last)).days <= 1:
        streak = run

    closed = [t for t in trades if t["status"] == "closed"]
    clean_run = best_clean = 0
    for t in trades:
        clean_run = clean_run + 1 if t.get("rules_checked") and not t.get("rule_breaks") else 0
        best_clean = max(best_clean, clean_run)
    calls = [t for t in closed if t.get("p_t1") is not None and t.get("t1_before_stop") is not None]
    cal_ok = len(calls) >= 20 and all(abs(c["called"] - c["actual"]) <= 10 for c in calibration(calls) if c["n"] >= 5)
    earned = {"first_fill": bool(trades), "first_t1": any(t.get("t1_before_stop") for t in closed),
              "clean_10": best_clean >= 10, "journal_10": sum(1 for t in trades if len((t.get("note") or "").strip()) >= NOTE_MIN) >= 10,
              "streak_5": best >= 5, "calibrated": cal_ok,
              "challenge_5": sum(1 for e in ch_events if e["label"] == "Daily challenge") >= 5}
    today = now.strftime("%Y-%m-%d")
    return {"xp": total, **level_for(total), "streak": streak, "best_streak": best,
            "today": {"fills": fills_per_day.get(today, 0), "fill_cap": cap,
                      "pnl_xp": pnl_per_day.get(today, 0), "pnl_cap": PNL_XP_CAP,
                      "xp": sum(e["xp"] for e in events if _day(e["ts"]) == today)},
            "badges": [{"id": k, "name": n, "how": h, "earned": earned[k]} for k, n, h in BADGES],
            "recent": events[-8:][::-1]}


def snapshot(state: dict, marks: dict, errors: dict | None = None) -> dict:
    """Everything the UI shows: account, trades with live P&L, journal stats."""
    rows = []
    unreal = open_risk = 0.0
    for t in state["trades"]:
        r = dict(t)
        px = marks.get(t["symbol"])
        r["mark"] = px
        if t["status"] == "open" and px is not None:
            r["unrealized"] = unrealized(t, px)
            risk = initial_risk(t)
            r["open_R"] = (t["realized"] + r["unrealized"]) / risk if risk else None
            unreal += r["unrealized"]
            open_risk += max(0.0, _s(t["side"]) * (t["entry"] - t["stop"])) * t["size"] * t["remaining"]
        rows.append(r)
    bal = balance(state)
    return {"account": {**state["account"], "balance": bal, "unrealized": unreal, "equity": bal + unreal,
                        "open_risk": open_risk, "open_risk_pct": open_risk / bal * 100 if bal else None,
                        "return_pct": (bal + unreal) / state["account"]["start_equity"] * 100 - 100},
            "trades": rows[::-1], "marks": marks, "errors": errors or {}, "stats": stats(state),
            "progress": progress(state), "challenge": challenge(state), "broker": PaperBroker.name}


# ---------- API used by the web server and the CLI ----------

def api_status(path: Path | None = None, broker: Broker | None = None) -> dict:
    with _LOCK:
        state = load(path)
        r = refresh(state, broker)
        save(state, path)
    return snapshot(state, r["marks"], r["errors"])


def api_preview(req: dict, path: Path | None = None, broker: Broker | None = None) -> dict:
    q = quote(req.get("symbol"), broker)
    return {"quote": q, **plan_ticket(load(path), req, q["price"])}


def api_open(req: dict, path: Path | None = None, broker: Broker | None = None) -> dict:
    with _LOCK:
        state = load(path)
        t = open_trade(state, req, broker)
        save(state, path)
    return t


def api_close(tid: str, path: Path | None = None, broker: Broker | None = None) -> dict:
    with _LOCK:
        state = load(path)
        t = find(state, tid)
        if t["status"] != "open":
            raise PaperError(f"{tid} is {t['status']}, not open")
        refresh(state, broker)       # targets/stop may have been hit since the last look: then that exit stands
        if t["status"] == "open":
            close_trade(state, tid, quote(t["symbol"], broker)["price"])
        save(state, path)
    return t


def api_cancel(tid: str, path: Path | None = None) -> dict:
    with _LOCK:
        state = load(path)
        t = cancel_trade(state, tid)
        save(state, path)
    return t


def api_reset(equity: float, path: Path | None = None) -> dict:
    with _LOCK:
        state = reset(load(path), equity)
        save(state, path)
    return state["account"]


# ---------- CLI ----------

def _fmt_trade(t: dict) -> str:
    from tools.fmt import num
    tg = ", ".join(f"{num(x['price'])}{'*' if x.get('hit_at') else ''}" for x in t["targets"])
    pnl = t.get("unrealized")
    return (f"{t['id']} {t['status']:9s} {t['symbol']:9s} {t['side']:5s} {num(t['size'], 4)} @ {num(t['entry'])} "
            f"stop {num(t['stop'])} tgt {tg} liq {num(t['liquidation'])}"
            + (f" mark {num(t['mark'])} uP&L {num(pnl)}" if pnl is not None else "")
            + (f" R {t['R']:+.2f} ({t['exit_reason']})" if t.get("R") is not None else ""))


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="Paper trading desk (simulated; no real orders).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("stats")
    o = sub.add_parser("open")
    o.add_argument("--symbol", required=True)
    o.add_argument("--side", required=True, choices=["long", "short"])
    o.add_argument("--stop", type=float, required=True)
    o.add_argument("--targets", type=float, nargs="+", required=True)
    o.add_argument("--risk-pct", type=float)
    o.add_argument("--size", type=float)
    o.add_argument("--leverage", type=float, default=1.0)
    o.add_argument("--limit", type=float)
    o.add_argument("--p-t1", type=float)
    o.add_argument("--note", default="")
    for name in ("close", "cancel"):
        sub.add_parser(name).add_argument("id")
    r = sub.add_parser("reset")
    r.add_argument("--equity", type=float, default=DEFAULT_EQUITY)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "open":
            t = api_open({"symbol": a.symbol.upper(), "side": a.side, "stop": a.stop, "targets": a.targets,
                          "risk_pct": a.risk_pct, "size": a.size, "leverage": a.leverage, "limit": a.limit,
                          "p_t1": a.p_t1, "note": a.note, "source": "cli"})
            print("Opened " + _fmt_trade(t))
        elif a.cmd == "close":
            print("Closed " + _fmt_trade(api_close(a.id)))
        elif a.cmd == "cancel":
            print("Cancelled " + _fmt_trade(api_cancel(a.id)))
        elif a.cmd == "reset":
            print(f"New paper account: {api_reset(a.equity)['start_equity']:,.2f}")
        else:
            snap = api_status()
            ac = snap["account"]
            print(f"Paper account: equity {ac['equity']:,.2f} (balance {ac['balance']:,.2f}, unrealized "
                  f"{ac['unrealized']:+,.2f}, {ac['return_pct']:+.2f}%)")
            if a.cmd == "status":
                for t in snap["trades"]:
                    if t["status"] in ("open", "pending"):
                        print("  " + _fmt_trade(t))
                for sym, err in snap["errors"].items():
                    print(f"  FETCH FAILED {sym}: {err}")
            else:
                s, pg = snap["stats"], snap["progress"]
                print(f"Level {pg['level']} {pg['rank']}: {pg['xp']} XP ({pg['into']}/{pg['need']} to next), "
                      f"clean streak {pg['streak']} day(s), best {pg['best_streak']}")
                print(json.dumps({k: v for k, v in s.items() if k != "curve"}, indent=1, default=float))
    except (PaperError, fetch_prices.FetchError) as exc:
        print(f"ERROR: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
