import pandas as pd

from tools import paper as pp
from tools import universe
from tests.test_paper import FakeBroker, fresh


def test_symbol_is_deterministic_and_crypto_only_on_weekends():
    days = pd.date_range("2026-11-02", periods=28, freq="D").strftime("%Y-%m-%d")
    classes = {c["id"]: c["asset_class"] for c in universe.core()}
    picks = {d: pp.challenge_symbol(d) for d in days}
    assert picks == {d: pp.challenge_symbol(d) for d in days}                     # same day -> same symbol
    assert len(set(picks.values())) >= 4                                          # it rotates
    for d, s in picks.items():
        if pd.Timestamp(d).dayofweek >= 5:
            assert classes[s] == "crypto", (d, s)
        else:
            assert classes[s] in pp.CHALLENGE_CLASSES or s in pp.CHALLENGE_EXTRA, (d, s)


def _trade(**kw):
    t = {"id": "PT-001", "symbol": "BTC/USDT", "opened_at": "2026-11-05T10:00:00+00:00", "status": "open",
         "rules_checked": True, "rule_breaks": [], "p_t1": 60, "weighted_R": 2.4, "t1_before_stop": None}
    t.update(kw)
    return t


def test_events_need_all_three_goals_and_count_once_per_day(monkeypatch):
    monkeypatch.setattr(pp, "challenge_symbol", lambda d: "BTC/USDT")
    good = _trade()
    assert [e["label"] for e in pp.challenge_events([good], 2.0)] == ["Daily challenge"]
    for bad in (_trade(rule_breaks=["max leverage"]), _trade(p_t1=None), _trade(weighted_R=1.5),
                _trade(symbol="ETH/USDT"), _trade(rules_checked=False)):
        assert pp.challenge_events([bad], 2.0) == [], bad
    twice = [good, _trade(id="PT-002", opened_at="2026-11-05T12:00:00+00:00")]
    assert len(pp.challenge_events(twice, 2.0)) == 1                              # one completion a day
    hit = _trade(status="closed", t1_before_stop=True, closed_at="2026-11-05T14:00:00+00:00")
    assert [e["xp"] for e in pp.challenge_events([hit], 2.0)] == [40, 20]


def test_challenge_card_and_xp_end_to_end(monkeypatch):
    now = pd.Timestamp("2026-11-05 10:00", tz="UTC")
    monkeypatch.setattr(pp, "challenge_symbol", lambda d: "BTC/USDT")
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: now))
    st, b = fresh(), FakeBroker([(100, 100, 100, 100)])
    card = pp.challenge(st, now)
    assert card["symbol"] == "BTC/USDT" and not card["done"] and not any(g["ok"] for g in card["goals"])
    assert card["resets_at"].startswith("2026-11-06T00:00")
    # weighted R 1.0 (one target at +1R): breaks the min-weighted-R rule too, so not rule-clean; not done
    pp.open_trade(st, dict(symbol="BTC/USDT", side="long", stop=95, targets=[105], risk_pct=1, p_t1=60), b, now=now)
    card = pp.challenge(st, now)
    assert card["attempts"] == 1 and not card["done"] and [g["ok"] for g in card["goals"]] == [False, True, False]
    xp_before = pp.progress(st, now)["xp"]
    t = pp.open_trade(st, dict(symbol="BTC/USDT", side="long", stop=95, targets=[110, 115], risk_pct=1, p_t1=60), b, now=now)
    assert t["weighted_R"] >= 2
    card = pp.challenge(st, now)
    assert card["done"] and card["trade_id"] == t["id"]
    pg = pp.progress(st, now)
    assert any(e["label"] == "Daily challenge" for e in pg["recent"])
    assert pg["xp"] >= xp_before + 40
    assert pp.snapshot(st, {})["challenge"]["done"]
