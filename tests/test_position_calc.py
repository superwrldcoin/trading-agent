import json

import pytest

from tools import position_calc as pc


def bch_plan(**kw):
    base = dict(asset="BCH/USDT", side="long", entry=309.80, stop=293.64, targets=[322.60, 366.10],
                leverage=10, mmr=0.005, fee=0.0005, equity=10_000, risk_pct=1, atr=5.32)
    base.update(kw)
    return pc.Plan(**base)


def test_known_case_matches_skill_worked_example():
    # skills/leveraged-position-math.md + risk-and-sizing.md
    r = pc.compute(bch_plan())
    assert r["units"] == pytest.approx(6.0747, abs=1e-4)
    assert r["notional"] == pytest.approx(1881.94, abs=0.01)
    assert r["margin"] == pytest.approx(188.19, abs=0.01)
    assert r["liquidation"] == pytest.approx(280.37, abs=0.01)
    assert r["liq_buffer_atr"] == pytest.approx(2.49, abs=0.01)
    assert r["max_leverage_1atr"] == pytest.approx(13.45, abs=0.01)
    assert r["stop_net"] == pytest.approx(-100.00, abs=0.01)  # sizing includes fees -> exactly 1% risk
    t1, t2 = r["targets"]
    assert (t1["net"], t2["net"]) == (pytest.approx(37.92, abs=0.01), pytest.approx(169.98, abs=0.01))
    assert (t1["R"], t2["R"]) == (pytest.approx(0.79, abs=0.01), pytest.approx(3.48, abs=0.01))
    assert r["weighted_R"] == pytest.approx(2.14, abs=0.01)
    assert r["effective_leverage"] == pytest.approx(0.188, abs=0.001)
    assert not r["liquidates_before_stop"]


def test_one_x_long_liquidation_near_zero_and_roe_equals_move():
    r = pc.compute(bch_plan(leverage=1, fee=0, equity=None, risk_pct=None, size=1))
    assert r["liquidation"] == pytest.approx(309.80 * 0.005)
    t1 = r["targets"][0]
    assert t1["roe_pct"] == pytest.approx((322.60 / 309.80 - 1) * 100)


def test_one_x_short_liquidation():
    r = pc.compute(pc.Plan(asset="X", side="short", entry=100, stop=110, targets=[90], leverage=1, mmr=0.005))
    assert r["liquidation"] == pytest.approx(100 * (2 - 0.005))
    assert r["targets"][0]["R"] == pytest.approx(1.0)


def test_very_high_leverage_liquidates_before_stop():
    r = pc.compute(bch_plan(leverage=100))
    assert r["liquidation"] == pytest.approx(309.80 * (1 - 0.01 + 0.005))  # 305.15, above the 293.64 stop
    assert r["liquidates_before_stop"]
    assert any("before the stop" in n for n in r["notes"])


def test_leverage_beyond_mmr_is_rejected():
    with pytest.raises(ValueError, match="too high for MMR"):
        pc.compute(bch_plan(leverage=250))  # 1/250 = 0.4% <= 0.5% MMR


def test_short_zone_tranches_match_session_example():
    # tools/output session: XAU/USD short from range top
    r = pc.compute(pc.Plan(asset="XAU/USD", side="short", zone=(4207.05, 4221.60), stop=4236.16,
                           targets=[4143.70, 4117.50], atr=29.11))
    assert [t["price"] for t in r["tranches"]] == [4207.05, pytest.approx(4214.325), 4221.60]
    assert [t["weight"] for t in r["tranches"]] == [0.30, 0.30, 0.40]
    assert r["entry"] == pytest.approx(4215.05, abs=0.01)
    assert [round(t["R"], 2) for t in r["targets"]] == [3.38, 4.62]
    assert r["weighted_R"] == pytest.approx(4.00, abs=0.01)
    assert r["stop_width_atr"] == pytest.approx(0.73, abs=0.01)


def test_default_weights_three_targets_and_sorting():
    r = pc.compute(pc.Plan(asset="X", side="long", entry=100, stop=95, targets=[120, 105, 110]))
    assert [t["target"] for t in r["targets"]] == [105, 110, 120]
    assert [t["weight"] for t in r["targets"]] == [0.4, 0.4, 0.2]


@pytest.mark.parametrize("kw, msg", [
    (dict(stop=320.0), "wrong side"),
    (dict(targets=[300.0]), "not in profit"),
    (dict(weights=[0.5, 0.4]), "sum to 1"),
    (dict(leverage=0.5), ">= 1"),
    (dict(equity=None), "go together"),
])
def test_invalid_inputs(kw, msg):
    with pytest.raises(ValueError, match=msg):
        pc.compute(bch_plan(**kw))


def test_zone_stop_inside_zone_rejected():
    with pytest.raises(ValueError, match="beyond the whole zone"):
        pc.compute(pc.Plan(asset="X", side="long", zone=(95, 105), stop=97, targets=[120]))


def test_missing_size_reports_per_unit():
    r = pc.compute(bch_plan(equity=None, risk_pct=None))
    assert r["units"] == 1.0 and any("per 1 unit" in n for n in r["notes"])


def test_flags_wide_stop_and_low_rr():
    r = pc.compute(pc.Plan(asset="MSFT", side="long", entry=513.94, stop=487.30, targets=[519.40, 522.85], atr=7.85))
    notes = " ".join(r["notes"])
    assert "wide stop" in notes and "T1 only" in notes and "weighted R 0.27" in notes


def test_cli_json_and_table(capsys):
    args = ["--asset", "BCH/USDT", "--side", "long", "--entry", "309.80", "--stop", "293.64",
            "--targets", "322.60", "366.10", "--leverage", "10", "--equity", "10000", "--risk-pct", "1"]
    assert pc.main(args + ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["liquidation"] == pytest.approx(280.37, abs=0.01)
    assert pc.main(args) == 0
    assert "| Liquidation | 280.37" in capsys.readouterr().out
    assert pc.main(["--asset", "X", "--side", "long", "--entry", "100", "--stop", "105", "--targets", "110"]) == 2
