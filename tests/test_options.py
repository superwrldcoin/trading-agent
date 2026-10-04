import numpy as np
import pandas as pd
import pytest

from tools import options as op
from tools import options_math as om

NOW = pd.Timestamp("2026-10-04 21:00", tz="UTC")


def test_parse_leg_variants():
    lg = op.parse_leg("long call 520 2026-11-20 @12.50 x2")
    assert lg == {"side": "long", "kind": "call", "strike": 520.0, "expiry": "2026-11-20", "premium": 12.5, "qty": 2.0, "iv": None}
    lg2 = op.parse_leg("SELL put 85,000 2026-10-30 @ 2,450 iv=0.34")
    assert lg2["side"] == "short" and lg2["strike"] == 85000 and lg2["premium"] == 2450 and lg2["iv"] == 0.34 and lg2["qty"] == 1
    with pytest.raises(ValueError, match="can't read leg"):
        op.parse_leg("long MSFT calls")


def test_parse_deribit_name():
    assert op.parse_deribit_name("BTC-27NOV26-73000-P") == ("2026-11-27", 73000.0, "put")
    assert op.parse_deribit_name("BTC-5OCT26-90000-C") == ("2026-10-05", 90000.0, "call")
    assert op.parse_deribit_name("BTC-PERPETUAL") is None


def test_pick_expiry():
    exps = ["2026-10-02", "2026-10-09", "2026-11-06", "2026-12-18"]
    assert op.pick_expiry(exps, None, 30, NOW) == "2026-11-06"
    assert op.pick_expiry(exps, "2026-12-18", None, NOW) == "2026-12-18"
    with pytest.raises(op.OptionsError, match="not listed"):
        op.pick_expiry(exps, "2026-10-02", None, NOW)   # already expired


def synthetic_chain(spot=100.0, T=0.25, put_skew=0.02):
    rows = []
    for k in range(80, 121, 5):
        for kind in ("call", "put"):
            iv = 0.30 + (put_skew if (kind == "put" and k < spot) else 0)
            p = om.price(kind, spot, k, T, op.RATE, iv)
            rows.append({"kind": kind, "strike": float(k), "bid": p * 0.98, "ask": p * 1.02, "last": p, "iv": iv, "oi": 10})
    return pd.DataFrame(rows)


def test_enrich_atm_iv_and_skew():
    df = op.enrich(synthetic_chain(), 100.0, 0.25)
    assert df["mid"].iloc[0] == pytest.approx(df["last"].iloc[0])
    atm_call = df[(df["strike"] == 100) & (df["kind"] == "call")].iloc[0]
    assert 0.5 < atm_call["delta"] < 0.6
    assert op.atm_iv(df, 100.0) == pytest.approx(0.30)
    assert op.skew_25d(df) == pytest.approx(0.02)       # OTM puts priced 2 vol points richer


def test_call_spread_breakeven_and_limits():
    T_exp = "2027-01-15"
    long_p = om.price("call", 100, 100, op.years_to(T_exp, NOW), op.RATE, 0.3)
    short_p = om.price("call", 100, 110, op.years_to(T_exp, NOW), op.RATE, 0.3)
    legs = [op.parse_leg(f"long call 100 {T_exp} @{long_p:.4f} x1"), op.parse_leg(f"short call 110 {T_exp} @{short_p:.4f} x1")]
    a = op.analyze_legs(legs, 100.0, NOW, 100)
    debit = (long_p - short_p)
    assert a["net_debit"] == pytest.approx(debit * 100, rel=1e-3)
    assert a["breakevens_at_expiry"] == [pytest.approx(100 + debit, abs=0.05)]
    assert a["max_profit"] == pytest.approx((10 - debit) * 100, rel=1e-3)
    assert a["max_loss"] == pytest.approx(-debit * 100, rel=1e-3)
    assert 0 < a["p_profit_model"] < 1
    zero = next(r for r in a["pnl_grid"] if r["shock_pct"] == 0)
    assert zero["d0"] == pytest.approx(0, abs=0.05)


def test_naked_short_call_is_unlimited():
    p = om.price("call", 100, 105, op.years_to("2026-12-18", NOW), op.RATE, 0.25)
    a = op.analyze_legs([op.parse_leg(f"short call 105 2026-12-18 @{p:.4f}")], 100.0, NOW, 100)
    assert a["max_loss"] is None and a["max_profit"] == pytest.approx(p * 100, rel=1e-3)
    assert "UNLIMITED" in op.render_analysis("MSFT", a)


def test_premium_out_of_range_needs_iv():
    with pytest.raises(ValueError, match="outside the model range"):
        op.analyze_legs([op.parse_leg("long call 100 2026-12-18 @150")], 100.0, NOW, 100)


def test_unsupported_underlying():
    with pytest.raises(op.OptionsError, match="BCH has no listed options"):
        op.load_chain("BCH/USDT", None, 30, NOW)


def test_render_chain_and_cli_errors(capsys):
    ch = {"symbol": "MSFT", "ticker": "MSFT", "provider": "test", "multiplier": 100, "note": "", "spot": 100.0,
          "expiry": "2027-01-15", "fetched_at": NOW.isoformat(), "chain": synthetic_chain()}
    text = op.render_chain(ch, op.enrich(ch["chain"], 100.0, op.years_to("2027-01-15", NOW)), 4)
    assert "ATM IV 30.0%" in text and "| 100.00 |" in text and "expected move" in text
    assert op.main(["analyze", "--symbol", "MSFT", "--leg", "nonsense", "--spot", "100"]) == 2
    assert "can't read leg" in capsys.readouterr().out
