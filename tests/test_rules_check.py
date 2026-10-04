import pandas as pd
import pytest

from tools import rules_check as rc

NOW = pd.Timestamp("2026-10-04 20:00", tz="UTC")
RULES = {"max_leverage": {"crypto": 10, "precious metals": 5, "equities": 2}, "max_risk_pct_per_trade": 1.0,
         "max_open_risk_pct": 3.0, "max_theme_gross_pct": 200, "max_positions": 3, "min_weighted_r": 2.0,
         "require_stop": True, "max_liq_touch_5d_pct": 10, "max_option_premium_pct": 2, "loss_cooldown_hours": 24,
         "max_trades_per_day": 2}
POS = {"equity": 10000.0, "positions": [
    {"id": "P-001", "symbol": "BCH/USDT", "instrument": "perp", "side": "long", "entry": 310, "size": 20,
     "leverage": 10, "stop": 298, "margin": "isolated"}]}   # risk 240 = 2.4%, notional 6,200


def result(rows, prefix):
    return next(r for r in rows if r["rule"].startswith(prefix))["result"]


def test_load_rules_from_markdown(tmp_path):
    p = tmp_path / "prefs.md"
    p.write_text("# Prefs\n\n## Trading rules\n<!-- rules -->\ntext\n```json\n{\"max_risk_pct_per_trade\": 1.5}\n```\n",
                 encoding="utf-8")
    assert rc.load_rules(p) == {"max_risk_pct_per_trade": 1.5}
    assert rc.load_rules(tmp_path / "missing.md") == {}


def test_repo_rules_template_parses_and_is_unset():
    rules = rc.load_rules()
    assert "max_leverage" in rules and all(v is None for v in rules["max_leverage"].values())
    assert rules["max_risk_pct_per_trade"] is None


def test_trade_history_parsing(tmp_path):
    p = tmp_path / "trades.md"
    p.write_text("# Trades\n\n## T-001: BTC/USDT long, opened 2026-10-03 08:00 UTC, closed 2026-10-04 06:00 UTC\n"
                 "- Result: -1.00R, -100.00\n\n## T-002: SI short, opened 2026-10-04 09:00 UTC, closed 2026-10-04 12:00 UTC\n"
                 "- Result: +0.40R, +36.98\n\n## T-000: <ASSET> template line\n", encoding="utf-8")
    t = rc.trade_history(p)
    assert [x["id"] for x in t] == ["T-001", "T-002"] and t[0]["R"] == -1.0 and t[1]["R"] == 0.4


def test_breaks_and_passes():
    plan = {"symbol": "BTC/USDT", "side": "long", "leverage": 20, "risk_pct": 1.5, "weighted_r": 1.8,
            "has_stop": False, "notional": 17000, "liq_touch_5d": 64, "option_premium": None}
    trades = [{"id": "T-001", "opened": NOW - pd.Timedelta(hours=30), "closed": NOW - pd.Timedelta(hours=6), "R": -1.0},
              {"id": "T-002", "opened": NOW - pd.Timedelta(hours=3), "closed": None, "R": None},
              {"id": "T-003", "opened": NOW - pd.Timedelta(hours=2), "closed": None, "R": None}]
    rows = rc.check(plan, RULES, POS, trades, NOW)
    assert result(rows, "max leverage") == "BREAKS"
    assert result(rows, "max risk per trade") == "BREAKS"
    assert result(rows, "max open risk") == "BREAKS"            # 2.4% + 1.5% > 3%
    assert result(rows, "max crypto gross") == "BREAKS"         # (6,200 + 17,000) / 10,000 = 232% > 200%
    assert result(rows, "max open positions") == "PASS"         # 1 + 1 <= 3
    assert result(rows, "min weighted R") == "BREAKS"
    assert result(rows, "stop required") == "BREAKS"
    assert result(rows, "max 5-day") == "BREAKS"
    assert result(rows, "cooldown") == "BREAKS"                 # last loss 6h ago < 24h
    assert result(rows, "max trades per day") == "BREAKS"       # 2 today + this one > 2
    assert result(rows, "max option premium") == "unknown"


def test_clean_plan_passes_and_unset_rules():
    plan = {"symbol": "SI", "side": "short", "leverage": 3, "risk_pct": 0.5, "weighted_r": 2.8, "has_stop": True,
            "notional": 3000, "liq_touch_5d": 2, "option_premium": None}
    rows = rc.check(plan, RULES, POS, [], NOW)
    assert all(r["result"] in ("PASS", "unknown") for r in rows)
    assert result(rows, "cooldown") == "PASS"
    unset = rc.check(plan, {}, POS, [], NOW)
    assert all(r["result"] == "not set" for r in unset)
    text = rc.render(plan, unset)
    assert "No rule broken" in text and "not set" in text


def test_open_risk_unknown_when_a_position_has_no_stop():
    pos = {"equity": 10000.0, "positions": [dict(POS["positions"][0], stop=None)]}
    plan = {"symbol": "BTC/USDT", "leverage": 5, "risk_pct": 1.0}
    assert result(rc.check(plan, RULES, pos, [], NOW), "max open risk") == "unknown"


def test_render_lists_broken_rules():
    plan = {"symbol": "BTC/USDT", "side": "long", "leverage": 20}
    text = rc.render(plan, rc.check(plan, RULES, POS, [], NOW))
    assert "Breaks 1 of your rules" in text and "max leverage (crypto) (20x vs 10x)" in text
