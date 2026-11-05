import json

import pandas as pd
import pytest

from tools import paper as pp
from tools import rules_check, store
from tests.test_paper import FakeBroker, T0, fresh


# ---------- store ----------

def test_reads_never_create_the_database_and_defaults_apply(isolated_store):
    assert store.get_settings() == {"rules_enabled": True} and store.get_doc("paper") is None
    assert not store.path().exists()


def test_docs_settings_persist_across_connections(isolated_store):
    store.put_doc("paper", {"trades": [1, 2]})
    store.set_settings({"rules_enabled": False, "theme": "dark"})
    assert store.get_doc("paper") == {"trades": [1, 2]}
    assert store.get_settings() == {"rules_enabled": False, "theme": "dark"}
    store.set_settings({"theme": "light"})                       # merge, not replace
    assert store.get_settings()["rules_enabled"] is False and store.setting("theme") == "light"
    assert [u["id"] for u in store.users()] == ["local"]
    store.put_doc("paper", {"other": True}, user="guest")         # multi-user ready: separate rows per user
    assert store.get_doc("paper") == {"trades": [1, 2]} and store.get_doc("paper", user="guest") == {"other": True}
    assert {u["id"] for u in store.users()} == {"local", "guest"}
    with pytest.raises(ValueError):
        store.set_settings(["not", "a", "dict"])


def test_legacy_paper_json_is_imported_once_and_kept(isolated_store):
    legacy = isolated_store / "legacy-paper.json"
    legacy.write_text(json.dumps({"account": {"start_equity": 5000}, "trades": [], "next_id": 4}), encoding="utf-8")
    assert store.get_doc("paper")["next_id"] == 4 and legacy.exists()
    legacy.write_text("{}", encoding="utf-8")                     # the database now wins over the old file
    assert store.get_doc("paper")["next_id"] == 4


def test_daily_backup_export_import_roundtrip(isolated_store):
    store.put_doc("paper", {"n": 1})
    day = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d")
    assert (store.backup_dir() / f"app-{day}.db").exists()          # first write of the day snapshots the db
    dump = store.export()
    assert dump["format"] == "trading-agent-export" and dump["docs"]["paper"] == {"n": 1}
    store.put_doc("paper", {"n": 2})
    store.set_settings({"rules_enabled": False})
    assert store.import_(dump)["docs"] == ["paper"]
    assert store.get_doc("paper") == {"n": 1} and store.setting("rules_enabled") is True
    assert len(list(store.backup_dir().glob("app-*_*.db"))) == 1          # import made a safety backup first
    with pytest.raises(ValueError, match="not a trading-agent export"):
        store.import_({"docs": {}})


def test_paper_uses_the_store_unless_a_json_path_is_given(isolated_store, monkeypatch):
    monkeypatch.setattr(pp, "PAPER_PATH", None)
    st = pp.load()
    st["next_id"] = 9
    pp.save(st)
    assert store.get_doc("paper")["next_id"] == 9 and pp.load()["next_id"] == 9


# ---------- house rules switch ----------

def test_switch_and_cli(capsys):
    assert rules_check.enabled() is True
    assert rules_check.main(["--set-enabled", "off"]) == 0 and rules_check.enabled() is False
    assert rules_check.main(["--symbol", "BTC/USDT", "--side", "long", "--leverage", "50"]) == 0
    assert "Rules: off" in capsys.readouterr().out
    rules_check.set_enabled(True)
    assert rules_check.main(["--symbol", "BTC/USDT", "--side", "long", "--leverage", "50"]) == 0
    out = capsys.readouterr().out
    assert "Rules check" in out and "Rules: off" not in out          # the rules table is back


def ticket(**kw):
    t = dict(symbol="BTC/USDT", side="long", stop=95, targets=[110], risk_pct=1, leverage=2, p_t1=60)
    t.update(kw)
    return t


def test_rules_on_keeps_the_guardrails():
    st, b = fresh(), FakeBroker([(100, 100, 100, 100)])
    with pytest.raises(pp.PaperError, match="stop is required while house rules are on"):
        pp.open_trade(st, ticket(stop=None), b, now=T0)
    with pytest.raises(pp.PaperError, match="at least one target"):
        pp.open_trade(st, ticket(targets=[]), b, now=T0)
    with pytest.raises(pp.PaperError, match="hit before the stop"):
        pp.open_trade(st, ticket(leverage=50, stop=90), b, now=T0)
    t = pp.open_trade(st, ticket(leverage=12), b, now=T0)
    assert t["mode"] == "rules" and "max leverage (crypto)" in t["rule_breaks"]


def test_rules_off_lets_you_trade_your_way():
    rules_check.set_enabled(False)
    st, b = fresh(), FakeBroker([(100, 100, 100, 100), (100, 101, 97.5, 98)])
    built = pp.api_preview(ticket(stop=None, targets=[], risk_pct=None, size=10, leverage=50), None, b)
    p = built["preview"]
    assert p["rules"] == [] and p["rules_off"] and p["risk_to"] == "liquidation" and p["weighted_R"] is None
    assert built["preview"]["xp"]["free"] is True
    with pytest.raises(pp.PaperError, match="size by units"):
        pp.open_trade(st, ticket(stop=None, risk_pct=1), b, now=T0)
    t = pp.open_trade(st, ticket(stop=None, targets=[], risk_pct=None, size=10, leverage=50), b, now=T0)
    assert t["mode"] == "free" and t["stop"] is None and t["rule_breaks"] == [] and not t["rules_checked"]
    # liquidation before the stop is allowed with rules off (it becomes the exit)
    t2 = pp.open_trade(st, ticket(leverage=50, stop=90), b, now=T0)
    assert t2["mode"] == "free" and t2["liquidation"] > 90
    b.upto = 2
    pp.refresh(st, b)                        # 50x long liquidates near 98.5: the 97.5 low takes both out
    assert t["status"] == "closed" and t["exit_reason"] == "liquidation" and t["R"] == pytest.approx(-1, abs=0.1)
    assert t2["exit_reason"] == "liquidation"
    snap = pp.snapshot(st, {"BTC/USDT": 98.0})
    assert snap["rules_enabled"] is False and snap["progress"]["xp"] == 0          # nothing on the discipline track
    assert snap["progress"]["freestyle"]["trades"] == 2 and snap["progress"]["freestyle"]["xp"] == 0  # floor at 0


def test_freestyle_xp_scores_results_and_is_capped_per_day(monkeypatch):
    rules_check.set_enabled(False)
    st, b = fresh(), FakeBroker([(100, 100, 100, 100), (100, 131, 99.9, 130)])
    for _ in range(4):
        pp.open_trade(st, ticket(stop=95, targets=[130], p_t1=70), b, now=T0)    # each wins about 6R
    b.upto = 2
    pp.refresh(st, b)
    fs = pp.progress(st, T0)["freestyle"]
    assert fs["xp"] == pp.FREE_DAILY_CAP and fs["today"]["gained"] == pp.FREE_DAILY_CAP
    assert {e["label"] for e in fs["recent"]} >= {"T1 first"} or any(e["label"].startswith("Won") for e in fs["recent"])
    # the discipline track and the daily challenge ignore free trades
    pg = pp.progress(st, T0)
    assert pg["xp"] == 0 and pg["today"]["fills"] == 0
    assert pp.challenge_events(st["trades"], 2.0) == []
    monkeypatch.setattr(pp, "challenge_symbol", lambda d: "BTC/USDT")
    assert pp.challenge(st, T0)["attempts"] == 0                     # free trades aren't challenge attempts
