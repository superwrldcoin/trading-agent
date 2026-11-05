import pandas as pd
import pytest

from tools import paper as pp

NOW = pd.Timestamp("2026-11-05 18:00", tz="UTC")


@pytest.fixture(autouse=True)
def cap3(monkeypatch):
    monkeypatch.setattr(pp, "_daily_cap", lambda: 3)


def tr(i, day="2026-11-05", hour=10, status="closed", breaks=(), checked=True, note="", p=None, reason="stop", R=-1.0, t1=False):
    ts = f"{day}T{hour:02d}:00:00+00:00"
    return {"id": f"PT-{i:03d}", "opened_at": ts, "created": ts, "status": status, "rule_breaks": list(breaks),
            "rules_checked": checked, "note": note, "p_t1": p, "exit_reason": reason if status == "closed" else None,
            "R": R if status == "closed" else None, "t1_before_stop": t1 if status == "closed" else None,
            "closed_at": f"{day}T{hour + 1:02d}:00:00+00:00" if status == "closed" else None}


def st(*trades, archive=()):
    return {"account": pp.new_account(10_000), "trades": list(trades), "archive": [{"account": {}, "trades": list(archive)}]}


def labels(pg):
    return {e["label"]: e["xp"] for e in pg["recent"]}


def test_level_curve():
    assert pp.level_for(0) == {"level": 1, "rank": "Rookie", "into": 0, "need": 100, "pct": 0.0}
    assert pp.level_for(100)["level"] == 2 and pp.level_for(249)["level"] == 2 and pp.level_for(250)["level"] == 3
    assert pp.level_for(250)["rank"] == "Apprentice"


def test_clean_planned_trade_stopped_out_still_earns():
    # a disciplined loser: plan + P(T1) + note + rule-clean + plan respected; losses cost no XP
    pg = pp.progress(st(tr(1, note="PDL sweep and reclaim", p=40, reason="stop", R=-1.0, t1=False)), NOW)
    # 10 + 5 + 5 + 15 + 10 + good call (called 40% and the stop came first) 5
    assert pg["xp"] == 50
    assert labels(pg)["Good P(T1) call"] == 5


def test_rule_breaks_cost_xp_and_floor_at_zero():
    pg = pp.progress(st(tr(1, breaks=["max_leverage", "min_weighted_r", "max_risk"], reason="liquidation", R=-1.3)), NOW)
    assert labels(pg)["Broke 3 rule(s)"] == -50 and labels(pg)["Liquidated"] == -30
    assert pg["xp"] == 0          # total never goes negative


def test_only_first_n_fills_a_day_earn_positive_xp_but_penalties_always_apply():
    trades = [tr(i, hour=8 + i, status="open") for i in range(1, 5)] + [tr(5, hour=14, status="open", breaks=["max_trades_per_day"])]
    pg = pp.progress(st(*trades), NOW)
    assert pg["xp"] == 3 * (10 + 15) - 25
    assert pg["today"]["fills"] == 5 and pg["today"]["fill_cap"] == 3


def test_pnl_xp_is_capped_per_day():
    wins = [tr(i, hour=8 + i, reason="target", R=3.0, t1=True, checked=False) for i in range(1, 4)]
    pg = pp.progress(st(*wins), NOW)
    assert pg["today"]["pnl_xp"] == pp.PNL_XP_CAP
    assert sum(e["xp"] for e in pg["recent"] if e["label"].startswith("Won")) == pp.PNL_XP_CAP


def test_cancelled_limits_earn_nothing():
    t = tr(1, status="open")
    t.update(status="cancelled", opened_at=None)
    assert pp.progress(st(t), NOW)["xp"] == 0


def test_streak_counts_clean_days_and_breaks_on_a_dirty_day():
    days = ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]
    trades = [tr(i, day=d, status="open") for i, d in enumerate(days, 1)]
    assert pp.progress(st(*trades), NOW)["streak"] == 5
    trades[2]["rule_breaks"] = ["max_leverage"]
    pg = pp.progress(st(*trades), NOW)
    assert pg["streak"] == 2 and pg["best_streak"] == 2


def test_streak_lapses_after_a_missed_day():
    assert pp.progress(st(tr(1, day="2026-11-03", status="open")), NOW)["streak"] == 0


def test_archived_accounts_keep_their_xp_and_badges():
    pg = pp.progress(st(archive=[tr(1, reason="target", R=1.0, t1=True)]), NOW)
    assert pg["xp"] > 0
    got = {b["id"] for b in pg["badges"] if b["earned"]}
    assert {"first_fill", "first_t1"} <= got and "clean_10" not in got


def test_preview_shows_what_a_ticket_would_earn():
    state = st(*[tr(i, hour=8 + i, status="open") for i in range(1, 4)])
    ticket = {"p_t1": 55, "note": "retest of PWH", "rules_checked": True, "rule_breaks": []}
    pv = pp.xp_preview(state, ticket, NOW)
    assert pv["capped"] and pv["total"] == 0                 # 3 fills today already
    pv = pp.xp_preview(st(), ticket, NOW)
    assert not pv["capped"] and pv["total"] == 10 + 5 + 5 + 15
